#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Заказчик» окна типа «Логистикс Рус» (ЭТАП 3.1.C.B.2, FIX-3).

Заказчик этой заявки фиксирован — в бланке печатается
ООО «ДжейСиСиТиЭс Интернейшнл Логистикс Рус», поэтому реквизитов здесь нет:
вкладка заполняет шапку заявки (номер и дату), наименование заказчика и
ПЛАН ПОГРУЗКИ. Образец — ui/windows/formika/tabs/customer_tab.py.

Наименование подставлено по умолчанию, но поле остаётся редактируемым:
в заявке встречается другой заказчик, и вводить его заново каждый раз
не нужно.

План погрузки (шаг FIX-3) переехал сюда с вкладки «Маршрут»: дата погрузки
и окно времени подачи ТС относятся к заявке в целом (раздел 1 бланка —
«Дата / время погрузки»), и заполняют их вместе с заказчиком. На «Маршруте»
остался план выгрузки — он про точки доставки.

Ключи get_data() — number, date, name, loading_date, loading_time_from,
loading_time_to — читает ui/windows/logistiks_rus/data.py::_build_customer
(name уходит и в customer.full_name, и в customer.short_name, а план
погрузки — в contract.loading_*).
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import QDate, QTime, pyqtSignal
from PyQt5.QtWidgets import (
    QDateEdit, QHBoxLayout, QLabel, QScrollArea, QTimeEdit, QVBoxLayout, QWidget,
)

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableDateEdit, PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.logistiks_rus.tabs.customer_tab")

#: Заказчик заявки «Логистикс Рус»: он же печатается в бланке.
DEFAULT_CUSTOMER_NAME = "ООО «ДжейСиСиТиЭс Интернейшнл Логистикс Рус»"

#: Окно времени погрузки по умолчанию — как было на вкладке «Маршрут»
#: до переноса (шаг FIX-3): значения те же, менялось только место.
DEFAULT_LOADING_TIME_FROM = QTime(8, 0)
DEFAULT_LOADING_TIME_TO = QTime(20, 0)

#: Формат даты и времени — как в остальных вкладках проекта.
DATE_FORMAT = "dd.MM.yyyy"
TIME_FORMAT = "HH:mm"


class NoWheelDateEdit(QDateEdit):
    """Дата с календарём без случайного изменения колёсиком мыши."""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelTimeEdit(QTimeEdit):
    """Время, которое меняется только при явном редактировании."""

    def wheelEvent(self, event):
        event.ignore()


class CustomerTab(TabMixin, QWidget):
    """Номер и дата заявки, наименование заказчика и план погрузки."""

    # Сигнал для передачи текста в окно на распознавание
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    def __init__(self):
        super().__init__()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)

        # ── Панель распознавания ──
        self.recognition_panel = RecognitionPanel(
            placeholder="Вставьте текст заявки (номер, дата, заказчик, "
                        "дата и время погрузки)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Заявка» ──
        contract_group, contract_layout = theme.section_box("Заявка")

        self.number = PasteableLineEdit("ЛР-001")
        contract_layout.addRow("Номер заявки", self.number)

        date_edit = NoWheelDateEdit()
        date_edit.setDisplayFormat(DATE_FORMAT)
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate())
        self.date = PasteableDateEdit(date_edit)
        contract_layout.addRow("Дата заявки", self.date)

        # ── Группа «Заказчик» ──
        customer_group, customer_layout = theme.section_box("Заказчик")

        self.name = PasteableLineEdit(DEFAULT_CUSTOMER_NAME)
        self.name.setText(DEFAULT_CUSTOMER_NAME)
        customer_layout.addRow("Наименование", self.name)

        # ── Группа «План погрузки» (переехала с вкладки «Маршрут», FIX-3) ──
        loading_group, loading_layout = theme.section_box("План погрузки")

        self.loading_date = self._make_date_edit(QDate.currentDate())
        loading_layout.addRow("Дата погрузки", self.loading_date)

        loading_time_layout = QHBoxLayout()
        self.loading_time_from = self._make_time_edit(DEFAULT_LOADING_TIME_FROM)
        loading_time_layout.addWidget(QLabel("с"))
        loading_time_layout.addWidget(self.loading_time_from)
        self.loading_time_to = self._make_time_edit(DEFAULT_LOADING_TIME_TO)
        loading_time_layout.addWidget(QLabel("по"))
        loading_time_layout.addWidget(self.loading_time_to)
        loading_time_layout.addStretch()
        loading_layout.addRow("Время погрузки", loading_time_layout)

        layout.addWidget(contract_group)
        layout.addWidget(customer_group)
        layout.addWidget(loading_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Логистикс Рус CustomerTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Виджеты вкладки
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _make_date_edit(value: QDate) -> PasteableDateEdit:
        """Дата с календарём и кнопкой вставки из буфера."""
        date_edit = NoWheelDateEdit()
        date_edit.setDisplayFormat(DATE_FORMAT)
        date_edit.setCalendarPopup(True)
        date_edit.setDate(value)
        return PasteableDateEdit(date_edit)

    @staticmethod
    def _make_time_edit(value: QTime) -> NoWheelTimeEdit:
        """Время «ЧЧ:ММ» без случайного изменения колёсиком."""
        time_edit = NoWheelTimeEdit()
        time_edit.setDisplayFormat(TIME_FORMAT)
        time_edit.setTime(value)
        time_edit.setFixedWidth(90)
        return time_edit

    @staticmethod
    def _set_time(time_edit: QTimeEdit, value: Any) -> None:
        """Ставит время из строки «HH:mm» (пустое или битое — не трогаем)."""
        text = str(value or "").strip()
        if not text:
            return

        parsed = QTime.fromString(text, TIME_FORMAT)
        if not parsed.isValid():
            logger.warning("Логистикс Рус: время %r не распознано", text[:5])
            return
        time_edit.setTime(parsed)

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """Номер, дата заявки (ISO), заказчик и план погрузки (время «ЧЧ:ММ»)."""
        return {
            "number": self.number.text().strip(),
            "date": self.date.date().toString("yyyy-MM-dd"),
            "name": self.name.text().strip(),
            "loading_date": self.loading_date.date().toString("yyyy-MM-dd"),
            "loading_time_from": self.loading_time_from.time().toString(TIME_FORMAT),
            "loading_time_to": self.loading_time_to.time().toString(TIME_FORMAT),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет номер, дату, наименование заказчика и план погрузки.

        Пустые значения игнорируются: распознавание часто отдаёт блок
        целиком с пустыми строками, и уже введённое не должно исчезать.
        Наименование принимается и как name, и как full_name: промпт отдаёт
        реквизиты заказчика ключами ContractData (customer.full_name).
        """
        if not data:
            return

        number = str(data.get("number") or "").strip()
        if number:
            self.number.setText(number)

        # Дату принимаем и как date, и как date_raw (разные промпты).
        date_value = data.get("date") or data.get("date_raw")
        if date_value:
            self._set_date(self.date, date_value)

        name = str(data.get("name") or data.get("full_name") or "").strip()
        if name:
            self.name.setText(name)

        # План погрузки: дата и окно времени подачи ТС.
        if data.get("loading_date"):
            self._set_date(self.loading_date, data["loading_date"])
        self._set_time(self.loading_time_from, data.get("loading_time_from"))
        self._set_time(self.loading_time_to, data.get("loading_time_to"))

        logger.info("Логистикс Рус: данные заказчика заполнены")

    def clear(self) -> None:
        """
        Очищает номер, возвращает дату на сегодня, имя заказчика — к дефолту
        и план погрузки — к значениям по умолчанию (сегодня, 08:00, 20:00).
        """
        self.number.clear()
        self.date.setDate(QDate.currentDate())
        self.name.setText(DEFAULT_CUSTOMER_NAME)
        self.loading_date.setDate(QDate.currentDate())
        self.loading_time_from.setTime(DEFAULT_LOADING_TIME_FROM)
        self.loading_time_to.setTime(DEFAULT_LOADING_TIME_TO)
        self.recognition_panel.clear()

        logger.debug("Логистикс Рус: поля заказчика очищены")


__all__ = [
    "CustomerTab",
    "NoWheelDateEdit",
    "NoWheelTimeEdit",
    "DEFAULT_CUSTOMER_NAME",
    "DEFAULT_LOADING_TIME_FROM",
    "DEFAULT_LOADING_TIME_TO",
    "DATE_FORMAT",
    "TIME_FORMAT",
]

