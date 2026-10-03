#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Заказчик» окна типа «Формика» (ЭТАП 3.1.B.2).

Стороны договора в бланке Формики фиксированы (ООО «Формика» и
ООО «ТЕХНОЛОГИСТИКА»), поэтому реквизитов здесь нет: вкладка заполняет
только шапку документа-заявки — номер и дату.

Ключи get_data() — number, date — читает
ui/windows/formika/data.py::_build_customer.
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import QDate, pyqtSignal
from PyQt5.QtWidgets import (
    QDateEdit, QFormLayout, QGroupBox, QScrollArea, QVBoxLayout, QWidget,
)

from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableDateEdit, PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.formika.tabs.customer_tab")


class CustomerTab(TabMixin, QWidget):
    """Номер и дата договора-заявки (шапка бланка)."""

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
            placeholder="Вставьте текст договора-заявки (номер, дата)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Договор-заявка» ──
        contract_group = QGroupBox("Договор-заявка")
        contract_layout = QFormLayout(contract_group)

        self.number = PasteableLineEdit("ТЛ-447")
        contract_layout.addRow("Номер заявки", self.number)

        date_edit = QDateEdit()
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate())
        self.date = PasteableDateEdit(date_edit)
        contract_layout.addRow("Дата заявки", self.date)

        layout.addWidget(contract_group)

        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Formika CustomerTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """Номер и дата заявки (дата — ISO, как ждёт сборка данных)."""
        return {
            "number": self.number.text().strip(),
            "date": self.date.date().toString("yyyy-MM-dd"),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет номер и дату.

        Пустые значения игнорируются: распознавание часто отдаёт блок
        целиком с пустыми строками, и уже введённое не должно исчезать.
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

        logger.info("Формика: данные заявки заполнены")

    def clear(self) -> None:
        """Очищает номер и возвращает дату на сегодня."""
        self.number.clear()
        self.date.setDate(QDate.currentDate())
        self.recognition_panel.clear()

        logger.debug("Формика: поля заявки очищены")


__all__ = ["CustomerTab"]
