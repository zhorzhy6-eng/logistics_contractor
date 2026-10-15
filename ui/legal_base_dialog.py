#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог «Правовая база»: документы pravo.gov.ru на локальном диске.

Что показывает
--------------
* список скачанных (и известных) документов: название, номер, дата, орган,
  категория, объём текста, источник;
* дату последнего обновления и размер базы (оригиналы + текст);
* кнопки: **[Обновить всё]**, **[Скачать документ]** (поле ввода),
  **[Скачать категорию]** (флажки), **[Проверить обновления]**, **[Открыть
  папку]**, **[Закрыть]**.

Почему работа идёт в отдельном потоке
-------------------------------------
Скачивание с распознаванием PDF занимает минуты: 48 страниц постановления
№ 2200 — это ~80 секунд только OCR. В главном потоке окно замёрзло бы, и
оператор решил бы, что программа повисла. Поэтому сеть и распознавание
живут в ``LegalWorker`` (QThread), а диалог показывает ход и не даёт
запустить вторую задачу поверх первой.

Изоляция
--------
Модуль самостоятельный: он НЕ подключён к настройкам Максимилиана
(``ui/agent_settings_dialog.py`` — чужая зона) и ничего в них не меняет.
Точка подключения позже — ``ui/legal_settings.py``.
"""

import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from core.legal_parser import LegalParser, human_size
from db.crud import legal_metadata
from ui import theme

logger = logging.getLogger("ui.legal_base_dialog")

#: Колонки таблицы документов: (заголовок, ширина).
COLUMNS = (
    ("Документ", 380),
    ("Номер", 90),
    ("Дата", 90),
    ("Категория", 140),
    ("Текст", 90),
    ("Источник", 110),
)

#: Сколько символов текста показывать в предпросмотре.
PREVIEW_LIMIT = 6000


class LegalWorker(QObject):
    """
    Выполняет длинную операцию парсера в отдельном потоке.

    Сигналы повторяют то, что нужно диалогу: ход работы, готовый отчёт или
    текст ошибки. Исключение не глушится молча — оператор должен видеть,
    что именно не получилось.
    """

    #: Ход работы: «(этап, сделано, всего)».
    progressed = pyqtSignal(str, int, int)
    #: Работа окончена: текст отчёта.
    finished = pyqtSignal(str)
    #: Работа упала: текст ошибки.
    failed = pyqtSignal(str)

    def __init__(self, parser: LegalParser, action: str, payload: Optional[Dict[str, Any]] = None):
        """
        :param parser: готовый парсер (создаётся в главном потоке);
        :param action: «all», «category», «document», «updates», «stats»;
        :param payload: параметры операции (категории, идентификатор, дни).
        """
        super().__init__()
        self._parser = parser
        self._action = action
        self._payload = payload or {}

    @pyqtSlot()
    def run(self) -> None:
        """Выполняет операцию (вызывается в рабочем потоке)."""
        try:
            self._parser.progress = self._on_progress
            if self._action == "all":
                self.finished.emit(self._run_all())
            elif self._action == "category":
                self.finished.emit(self._run_categories())
            elif self._action == "document":
                self.finished.emit(self._run_document())
            elif self._action == "updates":
                self.finished.emit(self._run_updates())
            else:
                self.finished.emit(self._run_stats())
        except Exception as e:  # noqa: BLE001 — поток не должен падать молча
            logger.exception("Правовая база: операция не выполнена")
            self.failed.emit(f"{type(e).__name__}: {e}")

    def _on_progress(self, stage: str, done: int, total: int) -> None:
        """Пробрасывает прогресс парсера в диалог."""
        self.progressed.emit(str(stage), int(done), int(total))

    # ── операции ──

    def _run_all(self) -> str:
        """Скачивает все категории списка источников."""
        results = self._parser.download_all()
        return _results_text("Скачивание всех категорий", results)

    def _run_categories(self) -> str:
        """Скачивает выбранные категории."""
        parts: List[str] = []
        for category in self._payload.get("categories") or []:
            results = self._parser.download_all(category=category)
            parts.append(_results_text(f"Категория «{category}»", results))
        return "\n\n".join(parts) or "Категории не выбраны."

    def _run_document(self) -> str:
        """Ищет и скачивает один документ по запросу оператора."""
        query = str(self._payload.get("query") or "").strip()
        if not query:
            return "Введите название, номер документа или адрес страницы портала."
        found = self._parser.search(query, limit=1, max_pages=2)
        if not found:
            found = self._parser.search("", limit=200, max_pages=3)
            found = [item for item in found if query.lower() in item.title.lower()][:1]
        if not found:
            return f"Документ по запросу «{query}» на портале не найден."
        document = found[0]
        self._parser._save(document)
        result = self._parser.download_document(document)
        if result.ok:
            note = " (текст распознан OCR)" if result.ocr_used else ""
            return (f"Скачан: {document.title}\n"
                    f"Файл: {result.original_path}\n"
                    f"Текст: {result.text_path} — {result.text_chars} символов{note}")
        return f"Не скачан: {document.title}\nПричина: {result.error}"

    def _run_updates(self) -> str:
        """Проверяет обновления за период и скачивает новые акты."""
        days = int(self._payload.get("days") or 30)
        summary = self._parser.update(days=days)
        lines = [
            f"Проверка обновлений с {summary['since']}",
            f"Найдено: {summary['found']} | скачано: {summary['downloaded']} | ошибок: {summary['errors']}",
            "",
        ]
        for document in summary["documents"][:20]:
            lines.append(f"  {document.publish_date} | {document.number or 'б/н':>12} | {document.title[:70]}")
        return "\n".join(lines)

    def _run_stats(self) -> str:
        """Собирает сводку по базе."""
        data = self._parser.stats()
        lines = [
            f"Документов: {data['documents']} | файлов: {data['downloaded']}",
            f"Символов текста: {data['text_chars']}",
            f"Оригиналы: {human_size(data['original_bytes'])} | текст: {human_size(data['text_bytes'])}",
            f"Последнее обновление: {data['last_update'] or '—'}",
            f"Последняя проверка: {data['last_check'] or '—'}",
            f"Каталог: {data['data_dir']}",
        ]
        return "\n".join(lines)


def _results_text(title: str, results) -> str:
    """
    Собирает отчёт по списку результатов скачивания.

    :param title: заголовок отчёта;
    :param results: список DownloadResult;
    :return: текст отчёта.
    """
    ok = [item for item in results if item.ok and not item.skipped]
    skipped = [item for item in results if item.skipped]
    unavailable = [item for item in results if item.unavailable]
    failed = [item for item in results if item.failed]
    lines = [title,
             f"Скачано: {len(ok)} | уже было: {len(skipped)} | "
             f"без источника: {len(unavailable)} | ошибок: {len(failed)}", ""]
    for item in ok:
        note = " [OCR]" if item.ocr_used else ""
        lines.append(f"  OK {item.document.display_title[:60]}{note}: {item.text_chars} символов")
    for item in skipped:
        lines.append(f"  -- {item.document.display_title[:60]}: файл уже есть "
                     f"({item.text_chars} символов)")
    for item in unavailable:
        lines.append(f"  !! {item.document.display_title[:60]}: акт опубликован до 2011 г., "
                     f"файл добавляется вручную")
    for item in failed:
        lines.append(f"  ОШИБКА {item.document.display_title[:60]}: {item.error}")
    return "\n".join(lines)


class LegalBaseDialog(QDialog):
    """
    Диалог «Правовая база»: список документов и операции с ними.

    Диалог ничего не решает сам: любую работу выполняет ``LegalParser``,
    а окно только показывает состояние и передаёт команды.
    """

    def __init__(self, parent: Optional[QWidget] = None, parser: Optional[LegalParser] = None):
        """
        :param parent: родительское окно;
        :param parser: готовый парсер (в тестах — с MOCK-сессией).
        """
        super().__init__(parent)
        self.parser = parser or LegalParser()
        self._thread: Optional[QThread] = None
        self._worker: Optional[LegalWorker] = None
        self._current_rows: List[Dict[str, Any]] = []

        self.setWindowTitle("Правовая база pravo.gov.ru")
        self.resize(1080, 720)
        self._build_ui()
        self.refresh()

    # ── построение интерфейса ──

    def _build_ui(self) -> None:
        """Собирает виджеты диалога."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.summary_label = QLabel("Загрузка сведений…")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        # ── фильтры ──
        filter_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск по названию или номеру…")
        self.search_edit.returnPressed.connect(self.refresh)
        filter_row.addWidget(self.search_edit, 1)

        self.downloaded_only = QCheckBox("Только скачанные")
        self.downloaded_only.stateChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.downloaded_only)

        refresh_button = theme.secondary_button("Обновить список", "Перечитать базу метаданных")
        refresh_button.clicked.connect(self.refresh)
        filter_row.addWidget(refresh_button)
        layout.addLayout(filter_row)

        # ── таблица ──
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([title for title, _ in COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for index, (_title, width) in enumerate(COLUMNS[1:], start=1):
            header.setSectionResizeMode(index, QHeaderView.Interactive)
            self.table.setColumnWidth(index, width)
        self.table.itemSelectionChanged.connect(self._show_selected)
        layout.addWidget(self.table, 1)

        # ── предпросмотр ──
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Выберите документ, чтобы увидеть извлечённый текст.")
        self.preview.setMinimumHeight(120)
        layout.addWidget(self.preview)

        # ── категории ──
        categories_title = QLabel("Категории для скачивания:")
        layout.addWidget(categories_title)
        self.categories_list = QListWidget()
        self.categories_list.setMaximumHeight(96)
        self.categories_list.setViewMode(QListWidget.ListMode)
        for name, count in self.parser.sources_categories().items():
            item = QListWidgetItem(f"{name} — {count} док.")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, name)
            self.categories_list.addItem(item)
        layout.addWidget(self.categories_list)

        # ── поле ввода «Скачать документ» ──
        document_row = QHBoxLayout()
        self.document_edit = QLineEdit()
        self.document_edit.setPlaceholderText(
            "Название или номер документа для поиска на портале — например «Правила перевозок грузов»"
        )
        self.document_edit.returnPressed.connect(self.download_document)
        document_row.addWidget(self.document_edit, 1)
        self.download_document_button = theme.primary_button(
            "Скачать документ", "Найти документ на портале и скачать его")
        self.download_document_button.clicked.connect(self.download_document)
        document_row.addWidget(self.download_document_button)
        layout.addLayout(document_row)

        # ── кнопки действий ──
        buttons = QHBoxLayout()
        self.update_all_button = theme.primary_button(
            "Обновить всё", "Скачать все категории списка источников")
        self.update_all_button.clicked.connect(self.download_all)
        buttons.addWidget(self.update_all_button)

        self.download_category_button = theme.secondary_button(
            "Скачать категорию", "Скачать отмеченные категории")
        self.download_category_button.clicked.connect(self.download_categories)
        buttons.addWidget(self.download_category_button)

        self.check_updates_button = theme.secondary_button(
            "Проверить обновления", "Найти и скачать акты, опубликованные за последние 30 дней")
        self.check_updates_button.clicked.connect(self.check_updates)
        buttons.addWidget(self.check_updates_button)

        open_folder_button = theme.secondary_button("Открыть папку", "Открыть каталог правовой базы")
        open_folder_button.clicked.connect(self.open_folder)
        buttons.addWidget(open_folder_button)

        buttons.addStretch(1)
        close_button = theme.secondary_button("Закрыть", "Закрыть диалог")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        # ── прогресс и отчёт ──
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setPlaceholderText("Здесь появятся итоги скачивания и проверки обновлений.")
        layout.addWidget(self.log)

    # ── наполнение ──

    def refresh(self) -> None:
        """Перечитывает базу и перерисовывает таблицу."""
        query = self.search_edit.text().strip()
        only_downloaded = self.downloaded_only.isChecked()
        try:
            rows = self.parser.documents(query=query or None, only_downloaded=only_downloaded, limit=500)
            stats = self.parser.stats()
        except Exception as e:  # noqa: BLE001 — база может быть занята или битой
            logger.error(f"Правовая база: не удалось прочитать список: {e}")
            self.log.setPlainText(f"Не удалось прочитать правовую базу: {e}")
            return

        self._current_rows = rows
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (
                row.get("short_title") or row.get("title") or row.get("id") or "",
                row.get("number") or "б/н",
                row.get("doc_date") or row.get("publish_date") or "",
                row.get("category") or "",
                f"{int(row.get('text_chars') or 0):,}".replace(",", " ") if row.get("text_chars") else "—",
                row.get("source") or "",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setToolTip(str(row.get("title") or ""))
                self.table.setItem(row_index, column, item)

        self.summary_label.setText(
            f"Документов в базе: <b>{stats['documents']}</b> "
            f"(скачано файлов: {stats['downloaded']}) | "
            f"текста: {stats['text_chars']:,} символов | ".replace(",", " ") +
            f"оригиналы: {human_size(stats['original_bytes'])} | "
            f"текст: {human_size(stats['text_bytes'])}<br>"
            f"Последнее обновление: {stats['last_update'] or '—'} | "
            f"последняя проверка обновлений: {stats['last_check'] or '—'}<br>"
            f"Каталог: {stats['data_dir']}"
        )
        if not rows:
            self.preview.setPlainText("В базе пока нет документов. Нажмите «Обновить всё».")

    def _on_filter_changed(self, _state: int) -> None:
        """Перерисовывает список при смене фильтра «только скачанные»."""
        self.refresh()

    def _show_selected(self) -> None:
        """Показывает извлечённый текст выбранного документа."""
        indexes = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not indexes:
            return
        row_index = indexes[0].row()
        if row_index >= len(self._current_rows):
            return
        row = self._current_rows[row_index]
        path = row.get("text_path") or ""
        header = f"{row.get('title') or ''}\n{row.get('url') or ''}\n\n"
        if not path or not os.path.exists(path):
            self.preview.setPlainText(
                header + "Текст ещё не извлечён. Скачайте документ — он появится здесь."
            )
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read(PREVIEW_LIMIT)
        except OSError as e:
            self.preview.setPlainText(header + f"Не удалось прочитать файл: {e}")
            return
        self.preview.setPlainText(header + text)

    # ── операции ──

    def _busy(self) -> bool:
        """Идёт ли уже длинная операция."""
        return self._thread is not None and self._thread.isRunning()

    def _start(self, action: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """
        Запускает операцию в отдельном потоке.

        :param action: имя операции для LegalWorker;
        :param payload: её параметры.
        """
        if self._busy():
            QMessageBox.information(self, "Правовая база", "Дождитесь окончания текущей операции.")
            return
        self._set_busy(True)
        self.log.setPlainText("Работаю…")
        self.progress.setRange(0, 0)

        thread = QThread(self)
        worker = LegalWorker(self.parser, action, payload)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progressed.connect(self._on_progress)
        # Только связанные методы: lambda и partial в connect запрещены —
        # замыкание держит Python-объект, связь живёт в C++ объекте сигнала,
        # и получается цикл Python ↔ Qt (AGENTS.md § 4). Поток диалог берёт
        # из self._thread, поэтому передавать его через сигнал не нужно.
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        # Поток и работник удаляются после завершения: иначе QThread
        # остаётся жить и второй запуск упрётся в «уже работает».
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    @pyqtSlot(str, int, int)
    def _on_progress(self, stage: str, done: int, total: int) -> None:
        """Показывает ход работы."""
        names = {"prepare": "подготовка", "resolve": "поиск документов", "download": "скачивание"}
        label = names.get(stage, stage)
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.progress.setFormat(f"{label}: %v из %m")
        else:
            self.progress.setRange(0, 0)
            self.progress.setFormat(label)

    @pyqtSlot(str)
    def _on_finished(self, text: str) -> None:
        """Завершает операцию и обновляет список."""
        self._stop_thread()
        self.log.setPlainText(text)
        self.refresh()

    @pyqtSlot(str)
    def _on_failed(self, text: str) -> None:
        """Показывает ошибку операции."""
        self._stop_thread()
        self.log.setPlainText(f"Ошибка: {text}")
        QMessageBox.warning(self, "Правовая база", f"Операция не выполнена:\n{text}")

    def _stop_thread(self) -> None:
        """Останавливает поток и снимает блокировку кнопок."""
        thread = self._thread
        if thread is not None:
            thread.quit()
            thread.wait(5000)
        self._thread = None
        self._worker = None
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        """Блокирует кнопки на время работы."""
        for button in (self.update_all_button, self.download_category_button,
                       self.download_document_button, self.check_updates_button):
            button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if not busy:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.setFormat("")

    # ── слоты кнопок ──

    def download_all(self) -> None:
        """Скачивает все категории списка источников."""
        self._start("all")

    def download_categories(self) -> None:
        """Скачивает отмеченные категории."""
        selected = [
            self.categories_list.item(index).data(Qt.UserRole)
            for index in range(self.categories_list.count())
            if self.categories_list.item(index).checkState() == Qt.Checked
        ]
        if not selected:
            QMessageBox.information(self, "Правовая база",
                                    "Отметьте хотя бы одну категорию в списке ниже.")
            return
        self._start("category", {"categories": selected})

    def download_document(self) -> None:
        """Ищет и скачивает документ по строке оператора."""
        query = self.document_edit.text().strip()
        if not query:
            QMessageBox.information(self, "Правовая база", "Введите название или номер документа.")
            return
        self._start("document", {"query": query})

    def check_updates(self) -> None:
        """Проверяет и скачивает обновления за 30 дней."""
        self._start("updates", {"days": 30})

    def open_folder(self) -> None:
        """Открывает каталог правовой базы в проводнике."""
        path = self.parser.data_dir
        try:
            if not os.path.isdir(path):
                os.makedirs(path, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: S606 — открытие проводника, не запуск файла
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:  # noqa: BLE001 — проводник может быть недоступен
            QMessageBox.warning(self, "Правовая база", f"Не удалось открыть папку:\n{e}")

    def closeEvent(self, event) -> None:  # noqa: N802 — имя из Qt
        """Не закрывает окно, пока идёт скачивание."""
        if self._busy():
            QMessageBox.information(self, "Правовая база",
                                    "Дождитесь окончания скачивания — окно закроется после него.")
            event.ignore()
            return
        super().closeEvent(event)


def show_legal_base(parent: Optional[QWidget] = None,
                    parser: Optional[LegalParser] = None) -> int:
    """
    Открывает диалог «Правовая база» модально.

    :param parent: родительское окно;
    :param parser: готовый парсер (в тестах — с MOCK-сессией);
    :return: код завершения диалога.
    """
    dialog = LegalBaseDialog(parent=parent, parser=parser)
    return dialog.exec_()


__all__ = ["COLUMNS", "LegalBaseDialog", "LegalWorker", "show_legal_base"]
