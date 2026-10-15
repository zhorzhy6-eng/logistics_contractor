#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Раздел настроек «Правовая база» (и, на втором этапе, «RAG»).

Модуль отдаёт ГОТОВЫЙ ВИДЖЕТ, который можно вставить в любое окно настроек:

    section = LegalSettingsSection(parent=self)
    layout.addWidget(section)

Почему отдельный виджет, а не правка существующего диалога
---------------------------------------------------------
Настройки Максимилиана живут в ``ui/agent_settings_dialog.py`` — этот файл
ведёт другой разработчик, и менять его нельзя (изоляция шага). Поэтому
раздел собран здесь и подключается ОДНОЙ строкой вставки виджета. Когда
придёт время интеграции, в ``agent_settings_dialog.py`` добавится вызов
``LegalSettingsSection`` — и ничего больше.

Что показывает
--------------
* путь к каталогу правовой базы и объём (оригиналы + текст);
* число документов и проиндексированных фрагментов;
* **[Скачать правовую базу]** — открывает диалог ``ui/legal_base_dialog``;
* раздел «RAG»: **[Индексировать правовую базу]**, **[Переиндексировать]**,
  размер индекса и статус (см. core/agent/rag.py).

Настройки хранятся в config/settings.json в разделе ``legal``:
    "legal": {
      "enabled": true,
      "auto_index": false,
      "top_k": 5,
      "chunk_size": 900,
      "chunk_overlap": 100,
      "embedding_model": "Embeddings",
      "lexical_fallback": true
    }
Раздел добавлен этим шагом; значения по умолчанию лежат здесь и не
дублируются в других модулях.
"""

import json
import logging
from typing import Any, Dict, Optional

from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget, QMessageBox,
    QProgressBar, QSpinBox, QVBoxLayout, QWidget,
)

from core.legal_parser import LegalParser, human_size
from core.settings_service import SettingsService, get_settings_service
from ui import theme

logger = logging.getLogger("ui.legal_settings")

#: Раздел настроек в config/settings.json.
SETTINGS_KEY = "legal"

#: Значения по умолчанию (единственный источник правды для этого раздела).
DEFAULT_LEGAL_SETTINGS: Dict[str, Any] = {
    "enabled": True,
    "auto_index": False,
    "top_k": 5,
    "chunk_size": 900,
    "chunk_overlap": 100,
    "embedding_model": "Embeddings",
    "lexical_fallback": True,
    "local_fallback": True,
}


def normalize_legal_settings(values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Приводит настройки правовой базы к словарю с допустимыми значениями.

    Ошибочное значение не роняет настройки: берётся значение по умолчанию
    и пишется предупреждение в лог (без персональных данных).

    :param values: сырой словарь из settings.json (может быть None);
    :return: нормализованный словарь.
    """
    result = dict(DEFAULT_LEGAL_SETTINGS)
    source = values if isinstance(values, dict) else {}
    for key, default in DEFAULT_LEGAL_SETTINGS.items():
        if key not in source:
            continue
        value = source[key]
        if isinstance(default, bool):
            result[key] = bool(value)
        elif isinstance(default, int):
            try:
                result[key] = int(value)
            except (TypeError, ValueError):
                logger.warning(f"Настройка legal.{key}: ожидалось число, оставляю {default}")
        else:
            result[key] = str(value)

    result["top_k"] = max(1, min(20, result["top_k"]))
    result["chunk_size"] = max(300, min(4000, result["chunk_size"]))
    result["chunk_overlap"] = max(0, min(result["chunk_size"] // 2, result["chunk_overlap"]))
    return result


def load_legal_settings(service: Optional[SettingsService] = None) -> Dict[str, Any]:
    """
    Читает раздел ``legal`` из config/settings.json.

    :param service: сервис настроек; None — общий экземпляр проекта;
    :return: нормализованные настройки.
    """
    service = service or get_settings_service()
    try:
        return normalize_legal_settings(service.get(SETTINGS_KEY))
    except Exception as e:  # noqa: BLE001 — битый settings.json не должен ронять окно
        logger.warning(f"Раздел настроек legal не прочитан: {e}")
        return dict(DEFAULT_LEGAL_SETTINGS)


def save_legal_settings(values: Dict[str, Any], service: Optional[SettingsService] = None) -> bool:
    """
    Записывает раздел ``legal`` в config/settings.json.

    :param values: словарь настроек;
    :param service: сервис настроек; None — общий экземпляр проекта;
    :return: True, если запись прошла.
    """
    service = service or get_settings_service()
    payload = normalize_legal_settings(values)
    try:
        return bool(service.update({SETTINGS_KEY: payload}, save=True))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Раздел настроек legal не сохранён: {e}")
        return False


class IndexWorker(QObject):
    """
    Индексация правовой базы в отдельном потоке.

    Индексация считает эмбеддинги для тысяч фрагментов — это минуты
    сетевого времени. В главном потоке окно замёрзло бы.
    """

    #: Ход работы: «(этап, сделано, всего)».
    progressed = pyqtSignal(str, int, int)
    #: Готово: текст отчёта.
    finished = pyqtSignal(str)
    #: Ошибка: текст для оператора.
    failed = pyqtSignal(str)

    def __init__(self, action: str, reindex: bool = False,
                 settings: Optional[Dict[str, Any]] = None):
        """
        :param action: «index» или «stats»;
        :param reindex: True — пересобрать индекс с нуля;
        :param settings: настройки раздела legal.
        """
        super().__init__()
        self._action = action
        self._reindex = reindex
        self._settings = settings or dict(DEFAULT_LEGAL_SETTINGS)

    @pyqtSlot()
    def run(self) -> None:
        """Выполняет индексацию (вызывается в рабочем потоке)."""
        try:
            if self._action == "stats":
                self.finished.emit(self._stats_text())
                return
            from core.agent.rag import RAG  # локальный импорт: RAG нужен только здесь

            rag = RAG(progress=self._on_progress, **self._settings_kwargs())
            summary = rag.reindex() if self._reindex else rag.index_documents()
            self.finished.emit(self._summary_text(summary))
        except Exception as e:  # noqa: BLE001 — поток не должен падать молча
            logger.exception("Индексация правовой базы не выполнена")
            self.failed.emit(f"{type(e).__name__}: {e}")

    def _settings_kwargs(self) -> Dict[str, Any]:
        """Отбирает из настроек то, что понимает RAG."""
        keys = ("top_k", "chunk_size", "chunk_overlap", "embedding_model",
                "lexical_fallback", "local_fallback")
        return {key: self._settings[key] for key in keys if key in self._settings}

    def _on_progress(self, stage: str, done: int, total: int) -> None:
        """Пробрасывает прогресс индексации в окно."""
        self.progressed.emit(str(stage), int(done), int(total))

    def _summary_text(self, summary: Dict[str, Any]) -> str:
        """Отчёт об индексации."""
        lines = [
            f"Документов: {summary.get('documents', 0)} | фрагментов: {summary.get('chunks', 0)}",
            f"Эмбеддинги: {summary.get('embedded', 0)} из {summary.get('chunks', 0)}",
            f"Пропущено без изменений: {summary.get('skipped', 0)}",
            f"Время: {summary.get('seconds', 0):.1f} с",
        ]
        for name, value in (summary.get("errors") or {}).items():
            lines.append(f"  ОШИБКА {name}: {value}")
        return "\n".join(str(line) for line in lines)

    def _stats_text(self) -> str:
        """Краткая сводка по индексу."""
        from db.crud import knowledge

        conn = knowledge.get_connection()
        try:
            data = knowledge.stats(conn=conn)
        finally:
            conn.close()
        return (f"Документов в индексе: {data['documents']} | "
                f"фрагментов: {data['chunks']} | "
                f"с эмбеддингами: {data['embedded']} | "
                f"размер индекса: {human_size(data['bytes'])}")


class LegalSettingsSection(QGroupBox):
    """
    Раздел «Правовая база» для окна настроек.

    Виджет самодостаточен: создаёт парсер, показывает объём базы и запускает
    скачивание/индексацию. Родительское окно не обязано знать о его
    внутреннем устройстве.
    """

    def __init__(self, parent: Optional[QWidget] = None,
                 parser: Optional[LegalParser] = None,
                 settings_service: Optional[SettingsService] = None):
        """
        :param parent: родительское окно;
        :param parser: готовый парсер (в тестах — с MOCK-сессией);
        :param settings_service: сервис настроек (в тестах — свой файл).
        """
        super().__init__("Правовая база", parent)
        self.parser = parser or LegalParser()
        self.settings_service = settings_service
        self.settings = load_legal_settings(settings_service)
        self._thread: Optional[QThread] = None
        self._worker: Optional[QObject] = None
        self._build_ui()
        self.refresh()

    # ── интерфейс ──

    def _build_ui(self) -> None:
        """Собирает виджеты раздела."""
        outer = QVBoxLayout(self)
        outer.setSpacing(8)

        self.status_label = QLabel("Читаю состояние правовой базы…")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

        form = QFormLayout()
        self.top_k_spin = QSpinBox()
        self.top_k_spin.setRange(1, 20)
        self.top_k_spin.setValue(int(self.settings["top_k"]))
        self.top_k_spin.setToolTip("Сколько фрагментов подставлять в контекст ответа")
        self.top_k_spin.valueChanged.connect(self._on_settings_changed)
        form.addRow("Фрагментов в ответе (top-k):", self.top_k_spin)

        self.chunk_spin = QSpinBox()
        self.chunk_spin.setRange(300, 4000)
        self.chunk_spin.setSingleStep(50)
        self.chunk_spin.setValue(int(self.settings["chunk_size"]))
        self.chunk_spin.setToolTip("Размер фрагмента текста в символах (500–1000 — рабочий диапазон)")
        self.chunk_spin.valueChanged.connect(self._on_settings_changed)
        form.addRow("Размер фрагмента, символов:", self.chunk_spin)

        self.overlap_spin = QSpinBox()
        self.overlap_spin.setRange(0, 500)
        self.overlap_spin.setSingleStep(10)
        self.overlap_spin.setValue(int(self.settings["chunk_overlap"]))
        self.overlap_spin.setToolTip("Перекрытие соседних фрагментов, чтобы не терять стыки статей")
        self.overlap_spin.valueChanged.connect(self._on_settings_changed)
        form.addRow("Перекрытие, символов:", self.overlap_spin)
        outer.addLayout(form)

        buttons = QHBoxLayout()
        self.update_all_button = theme.secondary_button(
            "Обновить всё",
            "Скачать все категории правовой базы с pravo.gov.ru и обновить индекс")
        self.update_all_button.clicked.connect(self.update_all)
        buttons.addWidget(self.update_all_button)

        self.download_document_button = theme.secondary_button(
            "Скачать документ",
            "Найти акт на портале по названию или номеру и добавить в базу")
        self.download_document_button.clicked.connect(self.download_document)
        buttons.addWidget(self.download_document_button)

        self.download_category_button = theme.secondary_button(
            "Скачать категорию",
            "Отметить категории и скачать их целиком")
        self.download_category_button.clicked.connect(self.download_category)
        buttons.addWidget(self.download_category_button)

        buttons.addStretch(1)
        outer.addLayout(buttons)

        buttons2 = QHBoxLayout()
        self.index_button = theme.primary_button(
            "Индексировать правовую базу", "Собрать RAG-индекс по скачанным документам")
        self.index_button.clicked.connect(self.index_documents)
        buttons2.addWidget(self.index_button)

        self.reindex_button = theme.secondary_button(
            "Переиндексировать", "Удалить индекс и собрать его заново")
        self.reindex_button.clicked.connect(self.reindex_documents)
        buttons2.addWidget(self.reindex_button)
        buttons2.addStretch(1)
        outer.addLayout(buttons2)

        documents_title = QLabel("Скачанные документы:")
        outer.addWidget(documents_title)
        self.documents_list = QListWidget()
        self.documents_list.setObjectName("legalDocumentsList")
        self.documents_list.setMaximumHeight(140)
        self.documents_list.setToolTip(
            "Документы правовой базы: сколько места занимает каждый акт")
        outer.addWidget(self.documents_list)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        outer.addWidget(self.result_label)

    # ── состояние ──

    def refresh(self) -> None:
        """Обновляет подписи: объём базы, число фрагментов, размер индекса."""
        try:
            data = self.parser.stats()
        except Exception as e:  # noqa: BLE001 — база может быть занята
            logger.error(f"Правовая база: сводка недоступна: {e}")
            self.status_label.setText(f"Не удалось прочитать правовую базу: {e}")
            return

        index_info = self._index_stats()
        self.status_label.setText(
            f"Документов: <b>{data['documents']}</b> (файлов: {data['downloaded']}) | "
            f"текста: {data['text_chars']:,} символов | ".replace(",", " ") +
            f"оригиналы: {human_size(data['original_bytes'])}<br>"
            f"Каталог: {data['data_dir']}<br>"
            f"Последнее обновление: {data['last_update'] or '—'} | "
            f"последняя проверка: {data['last_check'] or '—'}<br>"
            f"RAG-индекс: {index_info}"
        )
        self._fill_documents()

    def _index_stats(self) -> str:
        """Строка о состоянии RAG-индекса (пустая база — не ошибка)."""
        try:
            from db.crud import knowledge

            conn = knowledge.get_connection()
            try:
                data = knowledge.stats(conn=conn)
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Индекс недоступен: {e}")
            return "не собран"
        if not data["chunks"]:
            return "не собран"
        return (f"{data['documents']} док. / {data['chunks']} фрагментов "
                f"({human_size(data['bytes'])}, эмбеддингов: {data['embedded']})")

    # ── действия ──

    def _fill_documents(self) -> None:
        """
        Показывает список скачанных документов (что уже есть в базе).

        Список нужен оператору рядом с кнопками загрузки: видно, что уже
        скачано, сколько места занимает и когда обновлялось.
        """
        self.documents_list.clear()
        try:
            rows = self.parser.documents(only_downloaded=True, limit=200)
        except Exception as e:  # noqa: BLE001 — база может быть занята
            logger.debug(f"Правовая база: список документов недоступен: {e}")
            self.documents_list.addItem("Список недоступен — база занята")
            return

        if not rows:
            self.documents_list.addItem(
                "Пока ничего не скачано — нажмите «Обновить всё»"
            )
            return

        for row in rows:
            title = str(row.get("short_title") or row.get("title") or row["id"])
            size = row.get("original_bytes") or 0
            parts = [title]
            if row.get("number"):
                parts.append(str(row["number"]))
            if row.get("downloaded_at"):
                parts.append(f"скачан {str(row['downloaded_at'])[:10]}")
            if size:
                parts.append(human_size(int(size)))
            self.documents_list.addItem(" · ".join(parts))

    def open_download_dialog(self) -> None:
        """Открывает диалог «Правовая база» и обновляет подписи после него."""
        from ui.legal_base_dialog import LegalBaseDialog

        dialog = LegalBaseDialog(parent=self.window(), parser=self.parser)
        dialog.exec_()
        self.refresh()

    def _open_dialog_with(self, action: str) -> None:
        """
        Открывает диалог «Правовая база» и сразу запускает в нём действие.

        Кнопки раздела [Обновить всё], [Скачать документ] и [Скачать
        категорию] ведут в тот же диалог: там поле ввода, чек-боксы
        категорий, таблица документов и отчёт о загрузке. Дублировать эту
        логику в настройках незачем — но оператору удобно попадать сразу в
        нужное место, поэтому нужная кнопка нажимается сама.

        :param action: имя метода диалога («download_all»,
            «download_categories», «download_document»).
        """
        from ui.legal_base_dialog import LegalBaseDialog

        dialog = LegalBaseDialog(parent=self.window(), parser=self.parser)
        #: Действие запускается после показа окна: диалог стартует поток, и
        #: делать это до появления окна нельзя — оператор не увидит прогресс.
        trigger = getattr(dialog, action, None)
        if callable(trigger):
            QTimer.singleShot(0, trigger)
        dialog.exec_()
        self.refresh()

    def update_all(self) -> None:
        """[Обновить всё]: скачать все категории правовой базы."""
        self._open_dialog_with("download_all")

    def download_document(self) -> None:
        """[Скачать документ]: открыть диалог с полем ввода названия акта."""
        self._open_dialog_with("download_document")

    def download_category(self) -> None:
        """[Скачать категорию]: открыть диалог с чек-боксами категорий."""
        self._open_dialog_with("download_categories")

    def index_documents(self) -> None:
        """Запускает индексацию (без пересборки)."""
        self._start_index(reindex=False)

    def reindex_documents(self) -> None:
        """Пересобирает индекс с нуля."""
        answer = QMessageBox.question(
            self, "Правовая база",
            "Удалить текущий индекс и собрать его заново?\n"
            "Тексты документов останутся на диске — удаляются только фрагменты и эмбеддинги.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._start_index(reindex=True)

    def _start_index(self, reindex: bool) -> None:
        """Запускает индексацию в отдельном потоке."""
        if self._thread is not None and self._thread.isRunning():
            QMessageBox.information(self, "Правовая база", "Индексация уже идёт.")
            return
        self._set_busy(True)
        self.result_label.setText("Индексация…")
        self.progress.setRange(0, 0)

        thread = QThread(self)
        worker = IndexWorker("index", reindex=reindex, settings=self.settings)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progressed.connect(self._on_index_progress)
        worker.finished.connect(self._on_index_finished)
        worker.failed.connect(self._on_index_failed)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    @pyqtSlot(str, int, int)
    def _on_index_progress(self, stage: str, done: int, total: int) -> None:
        """Показывает ход индексации."""
        names = {"scan": "чтение документов", "chunk": "разбиение", "embed": "эмбеддинги",
                 "save": "сохранение"}
        label = names.get(stage, stage)
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.progress.setFormat(f"{label}: %v из %m")
        else:
            self.progress.setRange(0, 0)
            self.progress.setFormat(label)

    @pyqtSlot(str)
    def _on_index_finished(self, text: str) -> None:
        """Показывает отчёт и обновляет подписи."""
        self._stop_thread()
        self.result_label.setText(text.replace("\n", "<br>"))
        self.refresh()

    @pyqtSlot(str)
    def _on_index_failed(self, text: str) -> None:
        """Показывает ошибку индексации."""
        self._stop_thread()
        self.result_label.setText(f"Ошибка индексации: {text}")
        QMessageBox.warning(self, "Правовая база", f"Индексация не выполнена:\n{text}")

    def _stop_thread(self) -> None:
        """Останавливает поток и снимает блокировку кнопок."""
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
        self._thread = None
        self._worker = None
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        """Блокирует кнопки на время индексации."""
        for button in (self.index_button, self.reindex_button):
            button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if not busy:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.setFormat("")

    # ── настройки ──

    def _on_settings_changed(self, _value: int = 0) -> None:
        """Сохраняет настройки при изменении значений."""
        self.settings = normalize_legal_settings({
            **self.settings,
            "top_k": self.top_k_spin.value(),
            "chunk_size": self.chunk_spin.value(),
            "chunk_overlap": self.overlap_spin.value(),
        })
        save_legal_settings(self.settings, self.settings_service)

    def closeEvent(self, event) -> None:  # noqa: N802 — имя из Qt
        """Не закрывает раздел, пока идёт индексация."""
        if self._thread is not None and self._thread.isRunning():
            QMessageBox.information(self, "Правовая база",
                                    "Дождитесь окончания индексации.")
            event.ignore()
            return
        super().closeEvent(event)


def settings_json_snippet() -> str:
    """
    Возвращает фрагмент settings.json для раздела ``legal``.

    Нужен документации и тестам: так видно, что именно пишет раздел.

    :return: JSON-строка с отступом в 2 пробела.
    """
    return json.dumps({SETTINGS_KEY: DEFAULT_LEGAL_SETTINGS}, ensure_ascii=False, indent=2)


__all__ = [
    "DEFAULT_LEGAL_SETTINGS",
    "IndexWorker",
    "LegalSettingsSection",
    "SETTINGS_KEY",
    "load_legal_settings",
    "normalize_legal_settings",
    "save_legal_settings",
    "settings_json_snippet",
]
