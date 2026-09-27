#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Заказчик (плательщик)».
"""

import logging
from typing import Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QGroupBox,
    QScrollArea, QPushButton, QHBoxLayout,
)
from PyQt5.QtCore import pyqtSignal

from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableLineEdit, PasteableTextEdit, RecognitionPanel

logger = logging.getLogger("ui.tabs.customer_tab")


class CustomerTab(TabMixin, QWidget):
    """
    Вкладка с данными заказчика.
    """

    recognize_requested = pyqtSignal(str)

    def __init__(self):
        super().__init__()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)

        # ── Панель распознавания ──
        self.recognition_panel = RecognitionPanel(
            placeholder="Вставьте текст с данными заказчика (реквизиты организации)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Общие сведения» ──
        general_group = QGroupBox("Общие сведения")
        general_layout = QFormLayout(general_group)

        self.full_name = PasteableLineEdit("ООО «Ромашка»")
        general_layout.addRow("Полное наименование *", self.full_name)

        self.short_name = PasteableLineEdit("ООО «Ромашка»")
        general_layout.addRow("Сокращённое наименование", self.short_name)

        self.inn = PasteableLineEdit("7701234567")
        self.inn.setMaxLength(12)
        general_layout.addRow("ИНН *", self.inn)

        self.kpp = PasteableLineEdit("770101001")
        self.kpp.setMaxLength(9)
        general_layout.addRow("КПП *", self.kpp)

        self.ogrn = PasteableLineEdit("1027700132195")
        self.ogrn.setMaxLength(15)
        general_layout.addRow("ОГРН", self.ogrn)

        self.legal_address = PasteableTextEdit("Юридический адрес", max_height=60)
        general_layout.addRow("Юридический адрес *", self.legal_address)

        self.actual_address = PasteableTextEdit("Фактический адрес", max_height=60)
        general_layout.addRow("Фактический адрес", self.actual_address)

        layout.addWidget(general_group)

        # ── Группа «Банковские реквизиты» ──
        bank_group = QGroupBox("Банковские реквизиты")
        bank_layout = QFormLayout(bank_group)

        self.bank_account = PasteableLineEdit("40702810000000000001")
        self.bank_account.setMaxLength(20)
        bank_layout.addRow("Расчётный счёт *", self.bank_account)

        self.bik = PasteableLineEdit("044525225")
        self.bik.setMaxLength(9)
        bank_layout.addRow("БИК *", self.bik)

        self.correspondent_account = PasteableLineEdit("30101810400000000225")
        self.correspondent_account.setMaxLength(20)
        bank_layout.addRow("Корр. счёт *", self.correspondent_account)

        self.bank_name = PasteableLineEdit("ПАО Сбербанк")
        bank_layout.addRow("Наименование банка *", self.bank_name)

        layout.addWidget(bank_group)

        # ── Группа «Руководитель и контакты» ──
        contact_group = QGroupBox("Руководитель и контакты")
        contact_layout = QFormLayout(contact_group)

        self.director_name = PasteableLineEdit("Петров Петр Петрович")
        contact_layout.addRow("ФИО руководителя *", self.director_name)

        self.director_position = PasteableLineEdit("Генеральный директор")
        contact_layout.addRow("Должность руководителя *", self.director_position)

        self.phone = PasteableLineEdit("+7 (495) 123-45-67")
        contact_layout.addRow("Телефон", self.phone)

        self.email = PasteableLineEdit("info@example.ru")
        contact_layout.addRow("E-mail", self.email)

        layout.addWidget(contact_group)

        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        logger.debug("CustomerTab инициализирована")

    def get_data(self) -> Dict[str, Any]:
        """Собирает данные."""
        return {
            "full_name": self.full_name.text().strip(),
            "short_name": self.short_name.text().strip(),
            "inn": self.inn.text().strip(),
            "kpp": self.kpp.text().strip(),
            "ogrn": self.ogrn.text().strip(),
            "legal_address": self.legal_address.toPlainText().strip(),
            "actual_address": self.actual_address.toPlainText().strip(),
            "bank_account": self.bank_account.text().strip(),
            "bik": self.bik.text().strip(),
            "correspondent_account": self.correspondent_account.text().strip(),
            "bank_name": self.bank_name.text().strip(),
            "director_name": self.director_name.text().strip(),
            "director_position": self.director_position.text().strip(),
            "phone": self.phone.text().strip(),
            "email": self.email.text().strip(),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет поля заказчика.

        Пустые значения из data игнорируются: распознавание часто отдаёт блок
        «customer» со всеми пустыми строками, и введённые вручную реквизиты
        не должны из-за этого исчезать. Полная замена формы (например,
        загрузка из справочника) выполняется через clear() перед вызовом.
        """
        if not data:
            return

        text_fields = (
            "full_name", "short_name", "inn", "kpp", "ogrn",
            "bank_account", "bik", "correspondent_account", "bank_name",
            "director_name", "director_position", "phone", "email",
        )
        for field in text_fields:
            value = str(data.get(field) or "").strip()
            if value:
                getattr(self, field).setText(value)

        legal_address = str(data.get("legal_address") or "").strip()
        if legal_address:
            self.legal_address.setPlainText(legal_address)

        actual_address = str(data.get("actual_address") or "").strip()
        if actual_address:
            self.actual_address.setPlainText(actual_address)

        logger.info("Данные заказчика заполнены")

    def clear(self) -> None:
        """Очищает поля."""
        self.full_name.clear()
        self.short_name.clear()
        self.inn.clear()
        self.kpp.clear()
        self.ogrn.clear()
        self.legal_address.clear()
        self.actual_address.clear()
        self.bank_account.clear()
        self.bik.clear()
        self.correspondent_account.clear()
        self.bank_name.clear()
        self.director_name.clear()
        self.director_position.setText("Генеральный директор")
        self.phone.clear()
        self.email.clear()
        self.recognition_panel.clear()

        logger.debug("Поля заказчика очищены")