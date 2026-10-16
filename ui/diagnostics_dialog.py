#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог самодиагностики программы (задание, часть 3, пункты B–E).

Что умеет
---------
* **[Провести диагностику]** — прогоняет проверки и показывает их списком:
  знак статуса, название, короткий вывод. Подробности — по клику на строку
  (панель внизу).
* **[Сохранить лог в файл]** — собирает ZIP с отчётом, логами и сведениями
  о системе. ПДн маскируются, ключи API не попадают вовсе.
* **[Отправить разработчику]** — открывает почтовый клиент с темой
  «Logistics Contractor — отчёт диагностики» и вложением-архивом. Если
  адрес разработчика не задан в настройках — открывается папка с архивом,
  и оператор отправляет его сам (задание, часть E.2).
* **[Открыть папку с логами]** — проводник на папке logs.
* **[Закрыть]**.

Почему сборка архива — в отдельном потоке
-----------------------------------------
Обезличивание отчёта и логов занимает секунды (разбор текста целиком), и
делать это в главном потоке нельзя: окно замёрзло бы, и оператор решил бы,
что программа зависла. Поэтому архив собирает ``DiagnosticWorker``, а диалог
показывает полосу прогресса и кнопки блокирует до конца работы.

Что диалог НЕ делает: ничего не чинит, не меняет настройки и не трогает
базу. Кнопки — это чтение и упаковка.
"""

import logging
import os
import subprocess
import sys
from typing import Dict, List, Optional

from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from core import diagnostics
from core.diagnostics import CheckResult, DiagnosticReport

logger = logging.getLogger("ui.diagnostics_dialog")

#: Сколько времени показывать сообщение о готовом отчёте, мс.
MESSAGE_MS = 8000

#: Тема письма разработчику (задание, часть E.1).
MAIL_SUBJECT = "Logistics Contractor — отчёт диагностики"

#: Сколько подробностей проверки показывать (лог ошибок бывает длинным).
DETAILS_LIMIT = 12000


class DiagnosticWorker(QObject):
    """
    Собирает архив диагностики в отдельном потоке.

    Сигналы повторяют то, что нужно диалогу: готовый путь к архиву или
    текст ошибки. Исключение не глушится молча — оператор должен видеть,
    что отчёт не собрался.
    """

    #: Архив собран: путь к файлу.
    built = pyqtSignal(str)
    #: Не получилось: текст для оператора.
    failed = pyqtSignal(str)
    #: Шаг работы («маскирую логи…») — для подписи под полосой прогресса.
    stage = pyqtSignal(str)

    def __init__(self, report: DiagnosticReport,
                 target_dir: Optional[str] = None,
                 log_dir: Optional[str] = None):
        super().__init__()
        self._report = report
        self._target = target_dir
        self._log_dir = log_dir

    @pyqtSlot()
    def build(self) -> None:
        """Собирает ZIP с отчётом (вызывается в рабочем потоке)."""
        try:
            self.stage.emit("Маскирую персональные данные и собираю архив…")
            path = diagnostics.build_archive(self._report,
                                             target_dir=self._target,
                                             log_dir=self._log_dir)
        except Exception as e:  # noqa: BLE001 — отчёт не должен ронять окно
            logger.exception("Архив диагностики не собран")
            self.failed.emit(
                f"Отчёт не собрался: {type(e).__name__}. "
                f"Попробуйте ещё раз или пришлите логи вручную."
            )
            return
        self.built.emit(path)


class DiagnosticsDialog(QDialog):
    """
    Окно диагностики: список проверок, подробности, кнопки действий.

    :param parent: родитель-виджет (главное окно или окно типа);
    :param auto_start: сразу провести диагностику (кнопка меню «Справка»);
    :param live_network: проверять GigaChat и DaData сетевыми запросами.
    """

    def __init__(self, parent: Optional[QWidget] = None,
                 auto_start: bool = True, live_network: bool = False,
                 archive_dir: Optional[str] = None,
                 log_dir: Optional[str] = None):
        super().__init__(parent)
        self._report = DiagnosticReport()
        self._live = live_network
        self._archive_dir = archive_dir
        self._log_dir = log_dir
        self._archive_path = ""
        self._thread: Optional[QThread] = None
        self._worker: Optional[DiagnosticWorker] = None

        self.setWindowTitle("Диагностика программы")
        #: Ширина с запасом: пять кнопок с русскими подписями в 760 px не
        #: помещались, и текст обрезался («ровести диагностик…»).
        self.setMinimumSize(940, 580)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        title = QLabel(
            "Проверка состояния программы и сбор отчёта для разработчика. "
            "Диагностика ничего не меняет — только читает."
        )
        title.setObjectName("appSubheading")
        title.setWordWrap(True)
        layout.addWidget(title)

        self.table = self._build_table()
        layout.addWidget(self.table, 1)

        self.details = QTextEdit()
        self.details.setObjectName("diagnosticsDetails")
        self.details.setReadOnly(True)
        self.details.setPlaceholderText(
            "Подробности проверки появятся здесь — щёлкните по строке."
        )
        self.details.setFixedHeight(150)
        layout.addWidget(self.details)

        self.progress = QProgressBar()
        self.progress.setRange(0, len(diagnostics.CHECK_ORDER))
        self.progress.setValue(0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel("Готово к проверке.")
        self.status.setObjectName("mutedLabel")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        layout.addLayout(self._build_buttons())

        self.table.itemSelectionChanged.connect(self._show_selected_details)
        if auto_start:
            QTimer.singleShot(0, self.run_checks)

    # ---------------------------------------------------------
    # Построение
    # ---------------------------------------------------------
    def _build_table(self) -> QTableWidget:
        """Таблица проверок: знак статуса, название, вывод."""
        table = QTableWidget(0, 3, self)
        table.setHorizontalHeaderLabels(["", "Проверка", "Результат"])
        table.setObjectName("diagnosticsTable")
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        return table

    def _build_buttons(self) -> QHBoxLayout:
        """Кнопки действий (задание, часть B.2)."""
        self.btn_run = QPushButton("Провести диагностику")
        self.btn_save = QPushButton("Сохранить лог в файл")
        self.btn_send = QPushButton("Отправить разработчику")
        self.btn_open = QPushButton("Открыть папку с логами")
        self.btn_close = QPushButton("Закрыть")

        self.btn_run.clicked.connect(self.run_checks)
        self.btn_save.clicked.connect(self.save_archive)
        self.btn_send.clicked.connect(self.send_to_developer)
        self.btn_open.clicked.connect(self.open_logs_folder)
        self.btn_close.clicked.connect(self.accept)

        self.btn_save.setEnabled(False)
        self.btn_send.setEnabled(False)

        row = QHBoxLayout()
        row.addWidget(self.btn_run)
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_send)
        row.addWidget(self.btn_open)
        row.addStretch()
        row.addWidget(self.btn_close)
        return row

    # ---------------------------------------------------------
    # Проверки
    # ---------------------------------------------------------
    def run_checks(self) -> None:
        """Прогоняет все проверки и заполняет таблицу."""
        self.table.setRowCount(0)
        self.details.clear()
        self.progress.setVisible(True)
        self.progress.setRange(0, len(diagnostics.CHECK_ORDER))
        self.progress.setValue(0)
        self._set_busy(True)
        self.status.setText("Проверяю…")
        #: Даём Qt перерисовать окно до тяжёлых проверок.
        self.repaint()

        report = diagnostics.run_diagnostics(
            live_network=self._live, progress=self._on_progress,
        )
        self._report = report
        self._fill_table(report.checks)

        self.progress.setValue(self.progress.maximum())
        self.status.setText(report.summary_line() + ".")
        self._set_busy(False)
        self.btn_save.setEnabled(bool(report.checks))
        self.btn_send.setEnabled(bool(report.checks))

        if report.checks:
            self.table.selectRow(0)

    @pyqtSlot(int, int, str)
    def _on_progress(self, step: int, total: int, key: str) -> None:
        """Двигает полосу прогресса (вызывается из run_diagnostics)."""
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(step)
        self.status.setText(f"Проверяю: {TITLES.get(key, key)}…")
        self.repaint()

    def _fill_table(self, checks: List[CheckResult]) -> None:
        """Переносит результаты проверок в таблицу."""
        self.table.setRowCount(len(checks))
        for row, check in enumerate(checks):
            mark = QTableWidgetItem(check.mark)
            mark.setTextAlignment(0x0002 | 0x0080)  # AlignHCenter | AlignVCenter
            name = QTableWidgetItem(check.title)
            summary = QTableWidgetItem(check.summary)
            summary.setToolTip("\n".join([check.summary] + check.details[:20]))
            for column, item in enumerate((mark, name, summary)):
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 26)

    def _show_selected_details(self) -> None:
        """Показывает подробности выбранной проверки."""
        rows = self.table.selectionModel().selectedRows() \
            if self.table.selectionModel() else []
        if not rows:
            return
        index = rows[0].row()
        if not 0 <= index < len(self._report.checks):
            return
        check = self._report.checks[index]
        text = "\n".join(check.details) or "Подробностей нет."
        self.details.setPlainText(text[:DETAILS_LIMIT])

    def _set_busy(self, busy: bool) -> None:
        """Блокирует кнопки на время работы."""
        for button in (self.btn_run, self.btn_save, self.btn_send, self.btn_open):
            button.setEnabled(not busy)
        if not busy:
            self.btn_save.setEnabled(bool(self._report.checks))
            self.btn_send.setEnabled(bool(self._report.checks))

    # ---------------------------------------------------------
    # Архив и отправка
    # ---------------------------------------------------------
    def save_archive(self) -> None:
        """Собирает ZIP с отчётом (в отдельном потоке) и показывает путь."""
        if not self._report.checks:
            QMessageBox.information(self, "Диагностика",
                                    "Сначала проведите диагностику.")
            return
        if self._thread is not None:
            return

        self._set_busy(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # бесконечная: длительность неизвестна
        self.status.setText("Собираю отчёт…")

        thread = QThread(self)
        worker = DiagnosticWorker(self._report, target_dir=self._archive_dir,
                                  log_dir=self._log_dir)
        worker.moveToThread(thread)
        thread.started.connect(worker.build)
        worker.stage.connect(self._on_stage)
        worker.built.connect(self._on_archive_built)
        worker.failed.connect(self._on_archive_failed)
        worker.built.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_archive_finished)

        self._thread = thread
        self._worker = worker
        thread.start()

    @pyqtSlot(str)
    def _on_stage(self, message: str) -> None:
        """Промежуточный шаг сборки архива."""
        self.status.setText(message)

    @pyqtSlot(str)
    def _on_archive_built(self, path: str) -> None:
        """Архив собран: запоминаем путь и говорим оператору."""
        self._archive_path = path
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.status.setText(f"Отчёт сохранён: {path}")
        logger.info("Диагностика: отчёт сохранён (%s)",
                    os.path.basename(path))

    @pyqtSlot(str)
    def _on_archive_failed(self, message: str) -> None:
        """Архив не собрался: показываем причину."""
        self.status.setText(message)
        QMessageBox.warning(self, "Диагностика", message)

    @pyqtSlot()
    def _on_archive_finished(self) -> None:
        """Поток сборки закончил работу (успешно или нет)."""
        thread, worker = self._thread, self._worker
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.wait(5000)
            thread.deleteLater()
        if worker is not None:
            worker.deleteLater()
        self.progress.setVisible(False)
        self._set_busy(False)

    def send_to_developer(self) -> None:
        """
        Открывает почтовый клиент с готовым письмом (задание, часть E.1).

        Адрес разработчика берётся из настроек (``diagnostics_email``).
        Адрес не задан — открываем папку с архивом: оператор отправит файл
        сам (задание, часть E.2). Придумывать адрес за владельца программы
        нельзя: письмо уйдёт не туда.
        """
        if not self._archive_path:
            self.save_archive()
            if not self._archive_path:
                #: Архив ещё собирается: второй раз объяснять не нужно.
                QTimer.singleShot(1500, self._send_when_ready)
                return

        address = self._developer_email()
        if not address:
            self.open_folder(os.path.dirname(self._archive_path))
            QMessageBox.information(
                self, "Отправить разработчику",
                "Адрес разработчика не задан в настройках "
                "(параметр diagnostics_email).\n\n"
                f"Отчёт сохранён:\n{self._archive_path}\n\n"
                "Отправьте этот файл разработчику любым удобным способом.",
            )
            return

        url = mailto_url(address, self._archive_path)
        if not open_url(url):
            self.open_folder(os.path.dirname(self._archive_path))
            QMessageBox.information(
                self, "Отправить разработчику",
                "Не удалось открыть почтовый клиент.\n\n"
                f"Отчёт сохранён:\n{self._archive_path}\n\n"
                "Приложите этот файл к письму вручную.",
            )

    def _send_when_ready(self) -> None:
        """Повторная попытка отправки, когда архив уже собран."""
        if self._archive_path:
            self.send_to_developer()
        else:
            self.status.setText(
                "Отчёт ещё собирается — нажмите «Отправить разработчику» "
                "ещё раз через несколько секунд."
            )

    def _developer_email(self) -> str:
        """Адрес разработчика из настроек (пусто — не задан)."""
        try:
            from core.settings_service import get_settings_service

            return get_settings_service().get_str("diagnostics_email")
        except Exception as e:  # noqa: BLE001 — настройки не должны ронять
            logger.warning("Адрес разработчика не прочитан: %s",
                           type(e).__name__)
            return ""

    def open_logs_folder(self) -> None:
        """Открывает папку с логами (кнопка «Открыть папку с логами»)."""
        self.open_folder(self._log_dir or diagnostics.logs_dir())

    def open_folder(self, path: str) -> None:
        """Открывает папку в проводнике (или в файловом менеджере системы)."""
        target = str(path or "")
        if not target or not os.path.isdir(target):
            QMessageBox.information(self, "Диагностика",
                                    f"Папка не найдена: {target}")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(target)  # noqa: S606 — открыть папку в проводнике
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])  # noqa: S603,S607
            else:
                subprocess.Popen(["xdg-open", target])  # noqa: S603,S607
        except Exception as e:  # noqa: BLE001 — проводник может не открыться
            logger.warning("Папка не открыта: %s", type(e).__name__)
            QMessageBox.information(
                self, "Диагностика", f"Папка: {target}"
            )

    # ---------------------------------------------------------
    # Для тестов и проб
    # ---------------------------------------------------------
    def report(self) -> DiagnosticReport:
        """Отчёт последней проверки."""
        return self._report

    def archive_path(self) -> str:
        """Путь к собранному архиву (пусто — ещё не собирали)."""
        return self._archive_path

    def wait_for_archive(self, timeout_ms: int = 60000) -> str:
        """
        Ждёт окончания сборки архива (тесты и пробы).

        Ждём не только путь, но и завершения потока: кнопки разблокирует
        сигнал ``finished``, и без этого ожидания тест увидел бы выключенные
        кнопки. Возвращает путь к архиву или пустую строку, если не дождались.
        """
        from PyQt5.QtWidgets import QApplication

        waited = 0
        while self._thread is not None and waited < timeout_ms:
            QApplication.processEvents()
            QThread.msleep(50)
            waited += 50
        QApplication.processEvents()
        return self._archive_path


#: Названия проверок по ключам — для подписи «Проверяю: …».
TITLES: Dict[str, str] = {
    "database": "база данных",
    "settings": "настройки",
    "logs": "логи",
    "gigachat": "GigaChat",
    "dadata": "DaData",
    "agent_memory": "память Максимилиана",
    "disk": "свободное место",
    "version": "версия программы",
}


def mailto_url(address: str, archive_path: str = "") -> str:
    """
    Ссылка «написать письмо» с темой и телом (задание, часть E.1).

    Вложение через ``mailto:`` приложить нельзя (так устроен протокол), а вот
    тему и текст — можно: в тексте есть путь к архиву, и оператору остаётся
    перетащить файл в письмо.
    """
    from urllib.parse import quote

    body = (
        "Отчёт диагностики во вложении.\n\n"
        + (f"Файл: {archive_path}\n\n" if archive_path else "")
        + f"Версия программы: {diagnostics.APP_VERSION}\n"
    )
    return (f"mailto:{quote(address)}"
            f"?subject={quote(MAIL_SUBJECT)}&body={quote(body)}")


def open_url(url: str) -> bool:
    """Открывает ссылку системным способом (True — получилось)."""
    try:
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices

        return bool(QDesktopServices.openUrl(QUrl(url)))
    except Exception as e:  # noqa: BLE001
        logger.warning("Ссылка не открыта: %s", type(e).__name__)
        return False


def open_diagnostics(parent: Optional[QWidget] = None,
                     live_network: bool = False) -> Optional[str]:
    """
    Открывает диалог диагностики (точка входа для меню и агента).

    :return: путь к собранному архиву, если оператор его сохранил.
    """
    dialog = DiagnosticsDialog(parent, auto_start=True,
                               live_network=live_network)
    try:
        dialog.exec_()
        return dialog.archive_path()
    finally:
        dialog.deleteLater()


__all__ = [
    "DiagnosticsDialog",
    "MAIL_SUBJECT",
    "TITLES",
    "mailto_url",
    "open_diagnostics",
]
