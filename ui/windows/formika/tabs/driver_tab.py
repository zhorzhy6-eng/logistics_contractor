#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Водитель» окна типа «Формика» (ЭТАП 3.1.B.2).

Поля те же, что на вкладке водителя Экспедиторства (ui/tabs/driver_tab.py):
паспорт, адрес регистрации, водительское удостоверение и телефон. Серия
паспорта и ВУ нормализуется к виду «XX XX» — так же, как на той вкладке.

Ключи get_data() перечислены в ui/windows/formika/data.py::DRIVER_FIELDS;
их читает _build_driver.
"""

import logging
import re
from typing import Any, Dict

from PyQt5.QtCore import QDate, pyqtSignal
from PyQt5.QtWidgets import (
    QDateEdit, QFormLayout, QGroupBox, QScrollArea, QVBoxLayout, QWidget,
)

from ui.tabs.base_tab import TabMixin
from ui.widgets import (
    PasteableDateEdit, PasteableLineEdit, PasteableTextEdit, RecognitionPanel,
)

logger = logging.getLogger("ui.windows.formika.tabs.driver_tab")


def _normalize_series(value: str) -> str:
    """
    Приводит серию к виду «XX XX» (2 цифры, пробел, 2 цифры).

    Примеры:
      "6024"   → "60 24"
      "60 24"  → "60 24"
      "60-24"  → "60 24"
      "60 2"   → "60 2"   (неполную не трогаем)
      ""       → ""
    """
    if not value:
        return ""

    digits = re.sub(r"\D", "", str(value))

    if len(digits) == 4:
        return f"{digits[:2]} {digits[2:]}"
    if len(digits) == 2:
        return digits
    if len(digits) == 3:
        return f"{digits[:2]} {digits[2:]}"

    # Если цифр больше 4 (или меньше 2) — возвращаем как было (без мусора)
    return str(value).strip()


class DriverTab(TabMixin, QWidget):
    """Данные водителя и водительского удостоверения."""

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
            placeholder="Вставьте текст с данными водителя (ФИО, паспорт, ВУ)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Паспортные данные» ──
        passport_group = QGroupBox("Паспортные данные")
        passport_layout = QFormLayout(passport_group)

        self.full_name = PasteableLineEdit("Иванов Иван Иванович")
        passport_layout.addRow("ФИО", self.full_name)

        date_edit = QDateEdit()
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate().addYears(-30))
        self.birth_date = PasteableDateEdit(date_edit)
        passport_layout.addRow("Дата рождения", self.birth_date)

        self.birth_place = PasteableLineEdit("г. Москва")
        passport_layout.addRow("Место рождения", self.birth_place)

        # ВАЖНО: 5 символов — 4 цифры + пробел («60 24»)
        self.passport_series = PasteableLineEdit("XX XX")
        self.passport_series.setMaxLength(5)
        passport_layout.addRow("Серия паспорта", self.passport_series)

        self.passport_number = PasteableLineEdit("XXXXXX")
        self.passport_number.setMaxLength(6)
        passport_layout.addRow("Номер паспорта", self.passport_number)

        date_edit = QDateEdit()
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate())
        self.passport_issue_date = PasteableDateEdit(date_edit)
        passport_layout.addRow("Дата выдачи паспорта", self.passport_issue_date)

        self.passport_issuer = PasteableLineEdit("Отделом УФМС России по г. Москве")
        passport_layout.addRow("Кем выдан паспорт", self.passport_issuer)

        self.passport_code = PasteableLineEdit("XXX-XXX")
        self.passport_code.setMaxLength(7)
        passport_layout.addRow("Код подразделения", self.passport_code)

        self.registration_address = PasteableTextEdit(
            "Полный адрес регистрации", max_height=60
        )
        passport_layout.addRow("Адрес регистрации", self.registration_address)

        layout.addWidget(passport_group)

        # ── Группа «Водительское удостоверение» ──
        license_group = QGroupBox("Водительское удостоверение")
        license_layout = QFormLayout(license_group)

        # ВАЖНО: 5 символов — 4 цифры + пробел («99 36»)
        self.license_series = PasteableLineEdit("XX XX")
        self.license_series.setMaxLength(5)
        license_layout.addRow("Серия ВУ", self.license_series)

        self.license_number = PasteableLineEdit("XXXXXX")
        self.license_number.setMaxLength(6)
        license_layout.addRow("Номер ВУ", self.license_number)

        date_edit = QDateEdit()
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate())
        self.license_issue_date = PasteableDateEdit(date_edit)
        license_layout.addRow("Дата выдачи ВУ", self.license_issue_date)

        date_edit = QDateEdit()
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate().addYears(10))
        self.license_expiry_date = PasteableDateEdit(date_edit)
        license_layout.addRow("Срок действия ВУ", self.license_expiry_date)

        self.license_categories = PasteableLineEdit("A, B, C, E")
        license_layout.addRow("Категории ВУ", self.license_categories)

        self.phone = PasteableLineEdit("+7 (___) ___-__-__")
        license_layout.addRow("Телефон", self.phone)

        layout.addWidget(license_group)

        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Formika DriverTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """Собирает данные водителя (даты — ISO)."""
        return {
            "full_name": self.full_name.text().strip(),
            "birth_date": self.birth_date.date().toString("yyyy-MM-dd"),
            "birth_place": self.birth_place.text().strip(),
            "passport_series": _normalize_series(self.passport_series.text()),
            "passport_number": self.passport_number.text().strip(),
            "passport_issue_date": self.passport_issue_date.date().toString("yyyy-MM-dd"),
            "passport_issuer": self.passport_issuer.text().strip(),
            "passport_code": self.passport_code.text().strip(),
            "registration_address": self.registration_address.toPlainText().strip(),
            "license_series": _normalize_series(self.license_series.text()),
            "license_number": self.license_number.text().strip(),
            "license_issue_date": self.license_issue_date.date().toString("yyyy-MM-dd"),
            "license_expiry_date": self.license_expiry_date.date().toString("yyyy-MM-dd"),
            "license_categories": self.license_categories.text().strip(),
            "phone": self.phone.text().strip(),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет поля водителя.

        Пустые значения игнорируются: распознавание бывает частичным и не
        должно сбрасывать уже введённое. Серии нормализуются.
        """
        if not data:
            return

        if data.get("full_name"):
            self.full_name.setText(str(data["full_name"]))
        if data.get("birth_date"):
            self._set_date(self.birth_date, data["birth_date"])
        if data.get("birth_place"):
            self.birth_place.setText(str(data["birth_place"]))

        # Серию нормализуем: "6024" → "60 24"
        if data.get("passport_series"):
            self.passport_series.setText(_normalize_series(data["passport_series"]))

        if data.get("passport_number"):
            self.passport_number.setText(str(data["passport_number"]))
        if data.get("passport_issue_date"):
            self._set_date(self.passport_issue_date, data["passport_issue_date"])
        if data.get("passport_issuer"):
            self.passport_issuer.setText(str(data["passport_issuer"]))
        if data.get("passport_code"):
            self.passport_code.setText(str(data["passport_code"]))
        if data.get("registration_address"):
            self.registration_address.setPlainText(str(data["registration_address"]))

        # Серию ВУ тоже нормализуем: "9936" → "99 36"
        if data.get("license_series"):
            self.license_series.setText(_normalize_series(data["license_series"]))

        if data.get("license_number"):
            self.license_number.setText(str(data["license_number"]))
        if data.get("license_issue_date"):
            self._set_date(self.license_issue_date, data["license_issue_date"])
        if data.get("license_expiry_date"):
            self._set_date(self.license_expiry_date, data["license_expiry_date"])
        if data.get("license_categories"):
            self.license_categories.setText(str(data["license_categories"]))
        if data.get("phone"):
            self.phone.setText(str(data["phone"]))

        logger.info("Формика: данные водителя заполнены")

    def clear(self) -> None:
        """Очищает поля и возвращает даты к значениям по умолчанию."""
        self.full_name.clear()
        self.birth_date.setDate(QDate.currentDate().addYears(-30))
        self.birth_place.clear()
        self.passport_series.clear()
        self.passport_number.clear()
        self.passport_issue_date.setDate(QDate.currentDate())
        self.passport_issuer.clear()
        self.passport_code.clear()
        self.registration_address.clear()
        self.license_series.clear()
        self.license_number.clear()
        self.license_issue_date.setDate(QDate.currentDate())
        self.license_expiry_date.setDate(QDate.currentDate().addYears(10))
        self.license_categories.clear()
        self.phone.clear()
        self.recognition_panel.clear()

        logger.debug("Формика: поля водителя очищены")


__all__ = ["DriverTab"]
