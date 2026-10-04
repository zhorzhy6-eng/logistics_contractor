#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Заказчик» окна типа «Логистикс Рус» (ЭТАП 3.1.C.B.2).

Заказчик этой заявки фиксирован — в бланке печатается
ООО «ДжейСиСиТиЭс Интернейшнл Логистикс Рус», поэтому реквизитов здесь нет:
вкладка заполняет шапку заявки (номер и дату) и наименование заказчика.
Образец — ui/windows/formika/tabs/customer_tab.py.

Наименование подставлено по умолчанию, но поле остаётся редактируемым:
в заявке встречается другой заказчик, и вводить его заново каждый раз
не нужно.

Ключи get_data() — number, date, name — читает
ui/windows/logistiks_rus/data.py::_build_customer: name уходит и в
customer.full_name, и в customer.short_name.
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import QDate, pyqtSignal
from PyQt5.QtWidgets import (
    QDateEdit, QScrollArea, QVBoxLayout, QWidget,
)

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableDateEdit, PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.logistiks_rus.tabs.customer_tab")

#: Заказчик заявки «Логистикс Рус»: он же печатается в бланке.
DEFAULT_CUSTOMER_NAME = "ООО «ДжейСиСиТиЭс Интернейшнл Логистикс Рус»"


class CustomerTab(TabMixin, QWidget):
    """Номер и дата заявки, наименование заказчика (шапка бланка)."""

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
            placeholder="Вставьте текст заявки (номер, дата, заказчик)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Заявка» ──
        contract_group, contract_layout = theme.section_box("Заявка")

        self.number = PasteableLineEdit("ЛР-001")
        contract_layout.addRow("Номер заявки", self.number)

        date_edit = QDateEdit()
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate())
        self.date = PasteableDateEdit(date_edit)
        contract_layout.addRow("Дата заявки", self.date)

        # ── Группа «Заказчик» ──
        customer_group, customer_layout = theme.section_box("Заказчик")

        self.name = PasteableLineEdit(DEFAULT_CUSTOMER_NAME)
        self.name.setText(DEFAULT_CUSTOMER_NAME)
        customer_layout.addRow("Наименование", self.name)

        layout.addWidget(contract_group)
        layout.addWidget(customer_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Логистикс Рус CustomerTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """Номер, дата заявки (ISO) и наименование заказчика."""
        return {
            "number": self.number.text().strip(),
            "date": self.date.date().toString("yyyy-MM-dd"),
            "name": self.name.text().strip(),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет номер, дату и наименование заказчика.

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

        logger.info("Логистикс Рус: данные заказчика заполнены")

    def clear(self) -> None:
        """Очищает номер, возвращает дату на сегодня и имя заказчика к дефолту."""
        self.number.clear()
        self.date.setDate(QDate.currentDate())
        self.name.setText(DEFAULT_CUSTOMER_NAME)
        self.recognition_panel.clear()

        logger.debug("Логистикс Рус: поля заказчика очищены")


__all__ = ["CustomerTab", "DEFAULT_CUSTOMER_NAME"]
