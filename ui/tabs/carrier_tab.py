#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Перевозчик (исполнитель)».
Содержит поля для ввода данных организации-перевозчика.

Поддерживает загрузку даты лицензии в разных форматах:
  - ISO:       2023-01-26
  - Русский:   26.01.2023
  - ISO с T:   2023-01-26T00:00:00
"""

import logging
from typing import Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit,
    QGroupBox, QDateEdit, QScrollArea,
    QComboBox,
)
from PyQt5.QtCore import QDate, pyqtSignal

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableLineEdit, PasteableTextEdit, PasteableDateEdit, RecognitionPanel

logger = logging.getLogger("ui.tabs.carrier_tab")


class CarrierTab(TabMixin, QWidget):
    """
    Вкладка с данными перевозчика (исполнителя).

    Поля сгруппированы в смысловые блоки-карточки (тема — ui/theme.py):
    тип, общие сведения, адреса, банк, руководитель, лицензия. Порядок и
    состав полей не менялись, адреса вынесены из «Общих сведений» отдельным
    блоком — так видно, что заполнять в первую очередь.
    """

    recognize_requested = pyqtSignal(str)

    #: Типы, для которых КПП обязателен (у ИП его не существует — см. валидатор)
    TYPES_WITH_KPP = ("ООО",)

    def __init__(self):
        super().__init__()

        # Создаём прокручиваемую область
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        # ── Заголовок вкладки: где я и что важно ──
        layout.addWidget(theme.page_title(
            "Перевозчик (исполнитель)",
            "Поля со звёздочкой обязательны — они печатаются в договоре. "
            "Бледно-жёлтый фон означает, что обязательное поле ещё не заполнено.",
        ))

        # ── Панель распознавания ──
        self.recognition_panel = RecognitionPanel(
            placeholder="Вставьте текст с данными перевозчика (реквизиты организации)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Блок 1. Тип перевозчика ──
        type_group, type_layout = theme.section_box("Тип перевозчика")

        self.carrier_type = QComboBox()
        self.carrier_type.addItems([
            "ООО (с НДС)",
            "ИП с НДС",
            "ИП без НДС",
        ])
        self.carrier_type.setCurrentIndex(0)
        # Тип влияет на обязательность КПП: у ИП его не существует
        self.carrier_type.currentIndexChanged.connect(self._on_carrier_type_changed)
        type_layout.addRow(theme.required_label("Тип"), self.carrier_type)

        self.vat_rate = QLineEdit()
        self.vat_rate.setPlaceholderText("22")
        self.vat_rate.setText("22")
        self.vat_rate.setMaxLength(2)
        type_layout.addRow(theme.make_label("Ставка НДС, %"), self.vat_rate)

        layout.addWidget(type_group)

        # ── Блок 2. Общие сведения ──
        general_group, general_layout = theme.section_box("Общие сведения")

        self.full_name = PasteableLineEdit("ООО «Транс-Логистик»")
        self.full_name.set_required(True)
        general_layout.addRow(
            theme.required_label("Полное наименование"), self.full_name
        )

        self.short_name = PasteableLineEdit("ООО «Транс-Логистик»")
        general_layout.addRow(
            theme.make_label("Сокращённое наименование"), self.short_name
        )

        self.inn = PasteableLineEdit("7707654321")
        self.inn.setMaxLength(12)
        self.inn.set_required(True)
        general_layout.addRow(theme.required_label("ИНН"), self.inn)

        self.kpp = PasteableLineEdit("770701001")
        self.kpp.setMaxLength(9)
        self.kpp.set_required(True)
        self.kpp_label = theme.required_label("КПП")
        general_layout.addRow(self.kpp_label, self.kpp)

        self.ogrn = PasteableLineEdit("1027700261234")
        self.ogrn.setMaxLength(15)
        general_layout.addRow(theme.make_label("ОГРН / ОГРНИП"), self.ogrn)

        layout.addWidget(general_group)

        # ── Блок 3. Адреса (раньше жили внутри «Общих сведений») ──
        address_group, address_layout = theme.section_box("Адреса")

        self.legal_address = PasteableTextEdit("Юридический адрес", max_height=54)
        address_layout.addRow(
            theme.make_label("Юридический адрес"), self.legal_address
        )

        self.actual_address = PasteableTextEdit("Фактический адрес", max_height=54)
        address_layout.addRow(
            theme.make_label("Фактический адрес"), self.actual_address
        )

        layout.addWidget(address_group)

        # ── Блок 4. Банковские реквизиты ──
        bank_group, bank_layout = theme.section_box("Банковские реквизиты")

        self.bank_account = PasteableLineEdit("40702810000000000002")
        self.bank_account.setMaxLength(20)
        self.bank_account.set_required(True)
        bank_layout.addRow(theme.required_label("Расчётный счёт"), self.bank_account)

        self.bik = PasteableLineEdit("044525225")
        self.bik.setMaxLength(9)
        self.bik.set_required(True)
        bank_layout.addRow(theme.required_label("БИК"), self.bik)

        self.correspondent_account = PasteableLineEdit("30101810400000000225")
        self.correspondent_account.setMaxLength(20)
        bank_layout.addRow(
            theme.make_label("Корр. счёт"), self.correspondent_account
        )

        self.bank_name = PasteableLineEdit("ПАО Сбербанк")
        bank_layout.addRow(theme.make_label("Наименование банка"), self.bank_name)

        layout.addWidget(bank_group)

        # ── Блок 5. Руководитель и контакты ──
        contact_group, contact_layout = theme.section_box("Руководитель и контакты")

        self.director_name = PasteableLineEdit("Сидоров Сидор Сидорович")
        contact_layout.addRow(
            theme.make_label("ФИО руководителя"), self.director_name
        )

        self.director_position = PasteableLineEdit("Генеральный директор")
        contact_layout.addRow(theme.make_label("Должность"), self.director_position)

        self.phone = PasteableLineEdit("+7 (495) 987-65-43")
        contact_layout.addRow(theme.make_label("Телефон"), self.phone)

        self.email = PasteableLineEdit("info@trans-logistik.ru")
        contact_layout.addRow(theme.make_label("E-mail"), self.email)

        layout.addWidget(contact_group)

        # ── Блок 6. Лицензия ──
        license_group, license_layout = theme.section_box("Лицензия на перевозки")

        self.license_number = PasteableLineEdit("АК-77-123456")
        license_layout.addRow(
            theme.make_label("Номер лицензии"), self.license_number
        )

        date_edit = QDateEdit()
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate())
        self.license_date = PasteableDateEdit(date_edit)
        license_layout.addRow(
            theme.make_label("Дата выдачи лицензии"), self.license_date
        )

        layout.addWidget(license_group)

        layout.addStretch()

        scroll.setWidget(content_widget)

        # Основной layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

        self._on_carrier_type_changed()

        logger.debug("CarrierTab инициализирована")

    def _on_carrier_type_changed(self) -> None:
        """
        КПП обязателен только для ООО.

        У индивидуального предпринимателя КПП не существует, и валидатор его
        не требует — звёздочка и подсветка снимаются, чтобы не пугать зря.
        """
        carrier_type = self.carrier_type.currentText()
        needs_kpp = carrier_type.startswith(self.TYPES_WITH_KPP)

        theme.set_label_required(self.kpp_label, "КПП", needs_kpp)
        self.kpp.set_required(needs_kpp)
        logger.debug(
            f"Тип перевозчика: {carrier_type!r}, КПП обязателен: {needs_kpp}"
        )

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
            "license_number": self.license_number.text().strip(),
            "license_date": self.license_date.date().toString("yyyy-MM-dd"),
            "carrier_type": self.carrier_type.currentText(),
            "vat_rate": self.vat_rate.text().strip(),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет поля данными.

        Пустые значения игнорируются: распознавание приходит неполным
        (модель может отдать блок со всеми пустыми строками), и введённые
        вручную реквизиты не должны исчезать. Полная замена формы —
        через clear() перед вызовом.
        """
        if not data:
            return

        text_fields = (
            "full_name", "short_name", "inn", "kpp", "ogrn",
            "bank_account", "bik", "correspondent_account", "bank_name",
            "director_name", "director_position", "phone", "email",
            "license_number",
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

        # ── Дата лицензии (универсальный парсер) ──
        license_date = data.get("license_date", "")
        if license_date:
            self._set_date(self.license_date, license_date)

        # ── Тип перевозчика ──
        carrier_type = data.get("carrier_type", "") or data.get("entity_type", "")

        if not carrier_type:
            # Автоопределение по названию
            full_name_lower = self.full_name.text().lower()
            if "ип " in full_name_lower or full_name_lower.startswith("ип") or "индивидуальный предприниматель" in full_name_lower:
                carrier_type = "ИП с НДС"
            else:
                carrier_type = "ООО (с НДС)"

        # Ищем совпадение в списке
        for i in range(self.carrier_type.count()):
            if carrier_type in self.carrier_type.itemText(i):
                self.carrier_type.setCurrentIndex(i)
                break

        # ── Ставка НДС ──
        vat_rate = data.get("vat_rate", "")
        if vat_rate:
            self.vat_rate.setText(vat_rate)

        logger.info("Данные перевозчика заполнены")

    def clear(self) -> None:
        """Очищает все поля."""
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
        self.license_number.clear()
        self.license_date.setDate(QDate.currentDate())
        self.carrier_type.setCurrentIndex(0)
        self.vat_rate.setText("22")
        self.recognition_panel.clear()

        logger.debug("Поля перевозчика очищены")