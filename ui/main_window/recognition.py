# -*- coding: utf-8 -*-
"""
Распознавание данных документа: задача, сигналы и слоты окна.

Этап 6 (16.10.2026), подэтап 1.C: сюда переехали из прежнего
`ui/main_window.py` классы `RecognitionSignals`, `RecognitionTask`,
`BulkPasteDialog` и все слоты распознавания. Логика методов не менялась —
только переезд.

ГЛАВНАЯ ГРАБЛЯ этого модуля (грабли 2B.7): `lambda` и `functools.partial`
в `connect` ЗАПРЕЩЕНЫ. И то и другое держит сильную ссылку на окно, а сама
связь живёт в C++ объекте сигналов — получается цикл Python ↔ Qt, который
роняет процесс при выходе (0xC0000005). Поэтому все слоты — методы, а
конкретную задачу слот находит у отправителя сигнала:
`RecognitionSignals.owner()` (слабая ссылка на задачу).
"""

import logging
import weakref
from typing import Any, Dict

from PyQt5.QtCore import QObject, QRunnable, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QLabel, QMessageBox,
    QTextEdit, QVBoxLayout,
)

from core.gigachat_client import GigaChatClient, normalized_source_text
from core.recognizer import DataMapper, filled_only, filled_only_list
from core.trace import filled_fields_summary

from ui import theme

# Имя логгера задано ЯВНО (а не __name__): после переезда в пакет
# `ui.main_window.recognition` прежние записи выпали бы из настройки
# `config/logging_config.py` (APP_LOGGERS = "core", "ui", "db", ...) и
# `tests/test_logging_config.py`, который их проверяет.
logger = logging.getLogger("ui.main_window")


class RecognitionSignals(QObject):
    """Сигналы задачи распознавания (Шаг 8 оптимизации)."""

    finished = pyqtSignal(dict, str)  # данные распознавания, исходный текст
    error = pyqtSignal(str)
    cancelled = pyqtSignal()
    progress = pyqtSignal(int, str)   # процент, текст статуса

    def __init__(self, task=None):
        super().__init__()
        # Ссылка на задачу-владельца СЛАБАЯ. Сильная дала бы цикл
        # «задача → сигналы → задача», а связать слот с конкретной задачей
        # через lambda или functools.partial нельзя: и то и другое держит
        # сильную ссылку на окно, а связь живёт в C++ объекте сигналов —
        # получается цикл Python ↔ Qt, который роняет процесс при выходе
        # (грабли 2B.7). Слот берёт задачу у отправителя: self.sender().owner().
        self._task_ref = weakref.ref(task) if task is not None else None

    def owner(self):
        """Задача-владелец сигналов (None, если её уже собрал сборщик мусора)."""
        return self._task_ref() if self._task_ref is not None else None


class RecognitionTask(QRunnable):
    """
    Задача распознавания для QThreadPool (Шаг 8 оптимизации).

    Отличие от прежнего QThread:
      * пул потоков управляет жизненным циклом — объект не «теряется»
        при повторном запуске (раньше self.recognition_thread перезаписывался);
      * есть отмена: HTTP-запрос прервать нельзя, но результат отменённой
        задачи не применяется к интерфейсу;
      * есть прогресс: пользователь видит, на каком шаге распознавание.
    """

    def __init__(self, client: GigaChatClient, text: str):
        super().__init__()
        self.client = client
        self.text = text
        self.signals = RecognitionSignals(self)
        self._cancelled = False

    # ── Отмена ──
    def cancel(self) -> None:
        self._cancelled = True
        logger.info("Задача распознавания помечена как отменённая")

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self):
        try:
            self.signals.progress.emit(5, "Подготовка текста...")

            if self._cancelled:
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(
                20, f"Отправка в GigaChat ({len(self.text)} символов)..."
            )
            data = self.client.recognize_text(self.text)

            # Результат отменённой задачи не применяем
            if self._cancelled:
                logger.info("Распознавание отменено — результат не применяется")
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(90, "Разбор ответа модели...")

            if data is None:
                self.signals.error.emit("Модель вернула пустой ответ")
                return

            self.signals.progress.emit(100, "Распознавание завершено")
            # Исходный текст уходит вместе с данными: по нему список ТС
            # выравнивается после ответа модели (см. _on_recognition_finished).
            # Текст берётся нормализованный — ровно тот, что ушёл в модель.
            self.signals.finished.emit(data, normalized_source_text(self.text))

        except Exception as e:
            if self._cancelled:
                self.signals.cancelled.emit()
                return
            logger.exception("Ошибка в задаче распознавания")
            self.signals.error.emit(str(e))


class BulkPasteDialog(QDialog):
    """
    Диалог массовой вставки текста для распознавания.

    Класс переехал сюда из `ui/main_window.py` вместе со слотом
    `_on_recognize_clicked`, который его открывает. ВНИМАНИЕ: в проекте есть
    ВТОРАЯ копия класса с тем же именем — `ui/widgets/__init__.py`. Она
    старше, в окне не использовалась, и её поведение (используемый здесь
    `ui.theme.clipboard_button()`) сохранено дословно: разбор дублей —
    отдельная задача, а не этап переезда.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Заполнение данных водителя и ТС")
        self.resize(900, 700)

        layout = QVBoxLayout(self)

        header = QLabel(
            "Вставьте данные водителя и транспортного средства\n"
            "GigaChat автоматически распознает и заполнит все поля"
        )
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 14px; font-weight: bold; padding: 10px;")
        layout.addWidget(header)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Вставьте сюда данные...")
        self.text_edit.setStyleSheet("font-size: 12px;")
        layout.addWidget(self.text_edit)

        btn_paste = theme.clipboard_button()
        btn_paste.clicked.connect(self._paste_from_clipboard)
        layout.addWidget(btn_paste)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _paste_from_clipboard(self):
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        logger.debug(
            f"UI: вставка из буфера в диалоге распознавания | "
            f"символов: {len(text) if text else 0}"
        )
        self.text_edit.insertPlainText(text)

    def get_text(self) -> str:
        return self.text_edit.toPlainText()


class RecognitionMixin:
    """
    Слоты распознавания главного окна Экспедиторства.

    Миксин ничего не создаёт при инициализации: виджеты (`btn_recognize`,
    `btn_cancel`, `progress_bar`, `vehicles_tab`) и пул потоков
    (`thread_pool`) готовит `MainWindow.__init__` вместе с `LayoutMixin`.
    Своего `__init__` у миксина нет — иначе MRO-цепочка `__init__` пошла бы
    не в том порядке.
    """

    def _on_recognize_clicked(self):
        self._log_ui_action("нажата кнопка «Распознать данные»")
        if self.gigachat is None:
            self._log_ui_action("распознавание невозможно: GigaChat не инициализирован")
            QMessageBox.critical(
                self, "Ошибка",
                "GigaChat не инициализирован.\n\n"
                "Проверьте ключ в разделе «⚙ Настройки»."
            )
            return

        dialog = BulkPasteDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            self._log_ui_action("диалог массовой вставки закрыт без распознавания")
            return

        text = dialog.get_text().strip()
        if not text:
            self._log_ui_action("распознавание отменено: пустой текст")
            QMessageBox.warning(self, "Внимание", "Текст пуст.")
            return

        self._log_ui_action("текст принят из диалога", length=len(text))
        self._start_recognition(text)

    def _on_tab_recognize_requested(self, text: str):
        self._log_ui_action(
            "запрос распознавания со вкладки",
            tab=self.tabs.tabText(self.tabs.currentIndex()),
            length=len(text) if text else 0,
        )
        logger.info(
            f"Получен запрос на распознавание со вкладки: "
            f"{len(text) if text else 0} символов"
        )

        if self.gigachat is None:
            QMessageBox.critical(
                self, "Ошибка",
                "GigaChat не инициализирован.\n\n"
                "Проверьте ключ в разделе «⚙ Настройки»."
            )
            return

        if not text or not text.strip():
            QMessageBox.warning(
                self, "Внимание",
                "На вкладке нет текста для распознавания.\n\n"
                "Вставьте данные в поле «Вставить из буфера» и нажмите "
                "«Распознать вкладку»."
            )
            return

        self._start_recognition(text)

    def _start_recognition(self, text: str):
        """Запускает распознавание в пуле потоков (Шаг 8 оптимизации)."""
        self._log_ui_action(
            "старт распознавания",
            length=len(text),
            provider=self.settings_service.get("provider"),
        )
        self.btn_recognize.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        # Пока идёт распознавание, строки ТС тащить нельзя: список машин
        # перезапишется ответом модели (см. ui/widgets/row_reorder.py).
        self.vehicles_tab.set_reorder_enabled(False)
        self.statusBar().showMessage(
            f"Отправка в GigaChat ({len(text)} символов)..."
        )

        task = RecognitionTask(self.gigachat, text)
        self.recognition_task = task

        # Сигналы привязываем к КОНКРЕТНОЙ задаче: результат «старой»
        # (отменённой) задачи не должен влиять на новую. Связь — метод-слот
        # без lambda и без partial: и замыкание, и partial держат сильную
        # ссылку на окно, а связь живёт в C++ объекте сигналов — это цикл
        # Python ↔ Qt, роняющий процесс при выходе (грабли 2B.7). Задачу слот
        # находит у отправителя сигнала: RecognitionSignals.owner().
        task.signals.finished.connect(self._on_recognition_finished)
        task.signals.error.connect(self._on_recognition_error)
        task.signals.cancelled.connect(self._on_recognition_cancelled)
        task.signals.progress.connect(self._on_recognition_progress)

        self.thread_pool.start(task)
        logger.info(
            f"Задача распознавания запущена в пуле "
            f"(активных потоков: {self.thread_pool.activeThreadCount()})"
        )

    # --------------------------------------------------------
    # ОТМЕНА И ПРОГРЕСС  ← Шаг 8 оптимизации
    # --------------------------------------------------------
    def _is_current_task(self, task) -> bool:
        return task is None or task is self.recognition_task

    def _cancel_recognition(self):
        """
        Отменяет текущее распознавание.

        HTTP-запрос прервать нельзя, поэтому задача помечается отменённой
        (её результат не будет применён), а интерфейс освобождается сразу —
        пользователю не нужно ждать ответа сервера, чтобы начать заново.
        """
        task = self.recognition_task

        self._log_ui_action("нажата кнопка «Отменить распознавание»")

        if task is None or task.is_cancelled:
            logger.info("Отмена: активной задачи распознавания нет")
            return

        task.cancel()
        self.recognition_task = None
        self.btn_recognize.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.progress_bar.setVisible(False)
        self.vehicles_tab.set_reorder_enabled(True)
        self.statusBar().showMessage("Распознавание отменено", 5000)
        logger.info("Распознавание отменено пользователем")

    def _task_of_sender(self):
        """
        Задача распознавания, чьи сигналы пришли в слот.

        Задача берётся у отправителя сигнала: Qt отдаёт в sender() объект
        RecognitionSignals, а он помнит своего владельца слабой ссылкой
        (см. RecognitionSignals.owner). Так слот знает КОНКРЕТНУЮ задачу и
        отличается от «старой»: связать задачу через lambda или partial
        нельзя — это цикл Python ↔ Qt (грабли 2B.7).
        """
        signals = self.sender()
        task = signals.owner() if isinstance(signals, RecognitionSignals) else None
        if task is not None:
            return task

        current = self.recognition_task
        if current is not None and getattr(current, "signals", None) is signals:
            return current
        return None

    def _task_from(self, task):
        """Задача из аргумента слота; без него — по отправителю сигнала."""
        return task if task is not None else self._task_of_sender()

    def _on_recognition_progress(self, percent: int, message: str, task=None):
        task = self._task_from(task)
        if not self._is_current_task(task):
            return
        self.progress_bar.setValue(max(0, min(100, int(percent))))
        logger.info("Распознавание: прогресс=%s%%", percent)
        if message:
            self.statusBar().showMessage(message)

    def _on_recognition_cancelled(self, task=None):
        task = self._task_from(task)
        logger.info("Задача распознавания завершилась с отменой")
        if not self._is_current_task(task):
            return
        self._finish_recognition_ui(task)

    def _finish_recognition_ui(self, task=None):
        """Возвращает интерфейс в исходное состояние после распознавания."""
        if not self._is_current_task(task):
            return

        self.recognition_task = None
        self.btn_recognize.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        # Распознавание закончилось — строки ТС снова можно переносить.
        self.vehicles_tab.set_reorder_enabled(True)

    def _log_recognition_result(self, data: Dict[str, Any]):
        """
        Подробно пишет в debug.log: что распознали, а что нет.

        Логируются только имена полей и количества записей — значения
        (ФИО, адреса, паспорт) в лог не попадают.
        """
        recognized, missing = [], []

        for key, title in self._RECOGNITION_SECTIONS:
            value = data.get(key)

            if key == "vehicles":
                count = len(value) if isinstance(value, list) else 0
                if count:
                    recognized.append(f"{title}: {count} записей")
                else:
                    missing.append(title)
                continue

            if isinstance(value, dict) and value:
                recognized.append(f"{title}: {filled_fields_summary(value)}")
            else:
                missing.append(title)

        logger.debug(
            "Распознавание: разделы получены — "
            + ("; ".join(recognized) if recognized else "нет")
        )
        logger.debug(
            "Распознавание: НЕ распознано — "
            + (", ".join(missing) if missing else "нет")
        )
        logger.debug(f"Распознавание: все ключи ответа: {sorted(data.keys())}")

    def _on_recognition_finished(self, data: Dict[str, Any],
                                 source_text: str = "", task=None):
        """
        Заполняет вкладки результатом распознавания.

        `source_text` — текст, ушедший в модель (сигнал `finished` отдаёт его
        вторым аргументом). Нужен списку ТС: по строкам текста проверяется,
        что машин столько же и что VIN не потерялись. Вызов без аргумента
        (старые тесты, ручной вызов) список ТС просто не выравнивает.
        """
        task = self._task_from(task)
        if not self._is_current_task(task):
            logger.info("Результат устаревшей задачи распознавания проигнорирован")
            return

        logger.info(f"Распознавание завершено: {list(data.keys())}")
        self._log_recognition_result(data)
        try:
            # Распознавание накладывается на форму, а не заменяет её целиком:
            # пустые поля ответа не должны стирать то, что пользователь ввёл
            # руками (модель по промпту отдаёт блок «customer» пустым).
            driver_data = filled_only(data.get("driver"))
            if driver_data:
                self.driver_tab.fill_data(driver_data)
                logger.info("Данные водителя заполнены")
                logger.debug(
                    f"Вкладка «Водитель»: {filled_fields_summary(driver_data)}"
                )
            else:
                logger.info(
                    "Вкладка «Водитель»: в ответе нет данных — оставляем как есть"
                )

            customer_data = filled_only(data.get("customer"))
            if customer_data:
                self.customer_tab.fill_data(customer_data)
                logger.info("Данные заказчика заполнены")
                logger.debug(
                    f"Вкладка «Заказчик»: {filled_fields_summary(customer_data)}"
                )
            else:
                logger.info(
                    "Вкладка «Заказчик»: в ответе нет данных — "
                    "введённое вручную сохранено"
                )

            carrier_data = filled_only(data.get("carrier"))
            if carrier_data:
                self.carrier_tab.fill_data(carrier_data)
                logger.info("Данные перевозчика заполнены")
                logger.debug(
                    f"Вкладка «Перевозчик»: {filled_fields_summary(carrier_data)}"
                )
            else:
                logger.info(
                    "Вкладка «Перевозчик»: в ответе нет данных — оставляем как есть"
                )

            vehicles_data = filled_only_list(data.get("vehicles"))
            if vehicles_data:
                # Список ТС выравнивается по исходному тексту: VIN — на
                # латиницу, потерянная моделью строка возвращается на своё
                # место. Без этого марки съезжают на строку выше, а строк
                # в таблице оказывается меньше, чем в тексте.
                vehicles_data = DataMapper.repair_vehicle_list(
                    vehicles_data, source_text
                )
                self.vehicles_tab.fill_data(vehicles_data)
                logger.info(f"Таблица ТС заполнена: {len(vehicles_data)} записей")
                for i, vehicle in enumerate(vehicles_data, start=1):
                    logger.debug(
                        f"ТС #{i}: {filled_fields_summary(vehicle)}"
                    )
            else:
                logger.info(
                    "Таблица ТС: в ответе нет заполненных строк — оставляем как есть"
                )

            tractor_data = filled_only(data.get("tractor"))
            trailer_data = filled_only(data.get("trailer"))
            if tractor_data or trailer_data:
                self.trailer_tab.fill_data(tractor_data, trailer_data)
                logger.info("Данные тягача и полуприцепа заполнены")
                logger.debug(
                    f"Тягач: {filled_fields_summary(tractor_data)} | "
                    f"полуприцеп: {filled_fields_summary(trailer_data)}"
                )
            else:
                logger.info(
                    "Вкладка «Тягач и полуприцеп»: в ответе нет данных — "
                    "оставляем как есть"
                )

            contract_data = filled_only(data.get("contract"))
            if contract_data:
                self.contract_tab.fill_data(contract_data)
                logger.info("Данные договора заполнены")
                logger.debug(
                    f"Вкладка «Договор»: {filled_fields_summary(contract_data)}"
                )
            else:
                logger.info(
                    "Вкладка «Договор»: в ответе нет данных — оставляем как есть"
                )

            self._sync_loadings_to_vehicles()
            self._sync_unloadings_to_vehicles()

            self.statusBar().showMessage("Распознавание завершено", 5000)
            QMessageBox.information(
                self, "Успех", "Данные успешно распознаны и заполнены!"
            )
        except Exception as e:
            logger.exception("Ошибка при заполнении вкладок")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось заполнить вкладки:\n{e}"
            )
        finally:
            self._finish_recognition_ui(task)

    def _on_recognition_error(self, error_msg: str, task=None):
        task = self._task_from(task)
        if not self._is_current_task(task):
            logger.info("Ошибка устаревшей задачи распознавания проигнорирована")
            return

        logger.error(f"Ошибка распознавания: {error_msg}")
        self.statusBar().showMessage("Ошибка распознавания", 5000)
        self._finish_recognition_ui(task)
        QMessageBox.critical(
            self, "Ошибка распознавания",
            f"Не удалось распознать данные:\n\n{error_msg}"
        )
