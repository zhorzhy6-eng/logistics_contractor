#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Водитель» окна типа «Хавалы» (ЭТАП 3.1.E.B.2).

Водитель в бланке Хавалов — девять колонок таблицы, но полей в схеме промпта
БОЛЬШЕ: фамилия, имя и отчество лежат в РАЗНЫХ колонках («Фамилия», «Имя»,
«Отчество»), а серия и номер паспорта — тоже в разных («Серия Паспорта»,
«Номер Паспорта»). Это отличие от вкладок других типов, где ФИО или паспорт
часто вводятся одной строкой: промпт отдельно требует «НЕ склеивай их в одно»
и «переписывай ровно как напечатано». Поэтому здесь тринадцать отдельных
полей, и ни одно из них не выводится из соседнего.

Три даты — дата выдачи В/У, дата выдачи паспорта и дата рождения — разные
поля, связи между ними нет.

Отдельного блока "driver" в схеме Хавалов нет: поля водителя лежат плоскими
ключами блока "zayavka" (в отличие от перевозки и аренды). Кнопки справочника
водителей здесь не ставятся: справочник хранит ФИО одной строкой и паспорт
серией с номером, а вкладке нужны раздельные поля.

Ключи get_data() — driver_last_name, driver_first_name, driver_middle_name,
driver_license_number, driver_license_issue_date, driver_passport_series,
driver_passport_number, driver_passport_issuer, driver_passport_issue_date,
driver_citizenship, driver_birth_date, driver_registration, driver_phone —
читает ui/windows/havaly/data.py::_zayavka_of (и _driver_block, который
снимает префикс driver_ для формы ContractData). Имена полей СОВПАДАЮТ
с ключами схемы промпта (соглашение ЭТАПА 3.1.E.B.1).
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import QDate, pyqtSignal
from PyQt5.QtWidgets import QDateEdit, QScrollArea, QVBoxLayout, QWidget

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableDateEdit, PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.havaly.tabs.driver_tab")

#: Дата в поле ввода — «дд.мм.гггг», как в остальных вкладках проекта.
#: В данные вкладка отдаёт ISO, формат документа собирает сборщик.
DATE_FORMAT = "dd.MM.yyyy"

#: Сколько лет назад родился водитель — дата по умолчанию в форме.
DEFAULT_BIRTH_YEARS_AGO = 30


class NoWheelDateEdit(QDateEdit):
    """Дата с календарём без случайного изменения колёсиком мыши."""

    def wheelEvent(self, event):
        event.ignore()


class DriverTab(TabMixin, QWidget):
    """Водитель: ФИО, документы, гражданство, прописка и телефон."""

    # Сигнал для передачи текста в окно на распознавание
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    #: Текстовые поля вкладки: ровно те ключи, которые читает сборщик.
    #: Фамилия, имя и отчество — три РАЗНЫХ поля (требование промпта);
    #: серия и номер паспорта — тоже разные.
    FIELDS = (
        "driver_last_name", "driver_first_name", "driver_middle_name",
        "driver_license_number",
        "driver_passport_series", "driver_passport_number",
        "driver_passport_issuer",
        "driver_citizenship", "driver_registration", "driver_phone",
    )

    #: Поля-даты вкладки: три независимые даты.
    DATE_FIELDS = (
        "driver_license_issue_date",
        "driver_passport_issue_date",
        "driver_birth_date",
    )

    def __init__(self):
        super().__init__()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)

        # ── Панель распознавания ──
        self.recognition_panel = RecognitionPanel(
            placeholder="Вставьте текст с данными водителя (ФИО, В/У, паспорт)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Водитель» ──
        driver_group, driver_layout = theme.section_box("Водитель")

        self.driver_last_name = PasteableLineEdit("Иванов")
        self.driver_last_name.set_required(True)
        driver_layout.addRow(
            theme.required_label("Фамилия"), self.driver_last_name
        )

        self.driver_first_name = PasteableLineEdit("Иван")
        driver_layout.addRow("Имя", self.driver_first_name)

        self.driver_middle_name = PasteableLineEdit("Иванович")
        driver_layout.addRow("Отчество", self.driver_middle_name)

        self.driver_birth_date = self._make_date_edit(
            QDate.currentDate().addYears(-DEFAULT_BIRTH_YEARS_AGO)
        )
        driver_layout.addRow("Дата рождения", self.driver_birth_date)

        self.driver_citizenship = PasteableLineEdit("Российская Федерация")
        driver_layout.addRow("Гражданство", self.driver_citizenship)

        self.driver_phone = PasteableLineEdit("+7 (999) 123-45-67")
        driver_layout.addRow("Телефон", self.driver_phone)

        self.driver_registration = PasteableLineEdit(
            "г. Москва, ул. Водительская, д. 3"
        )
        driver_layout.addRow("Прописка", self.driver_registration)

        layout.addWidget(driver_group)

        # ── Группа «Водительское удостоверение» ──
        license_group, license_layout = theme.section_box(
            "Водительское удостоверение"
        )

        self.driver_license_number = PasteableLineEdit("99 АА 123456")
        license_layout.addRow("Номер В/У", self.driver_license_number)

        self.driver_license_issue_date = self._make_date_edit(
            QDate.currentDate()
        )
        license_layout.addRow("Дата выдачи В/У", self.driver_license_issue_date)

        layout.addWidget(license_group)

        # ── Группа «Паспорт» ──
        passport_group, passport_layout = theme.section_box("Паспорт")

        # Серия и номер — разные колонки бланка и разные поля схемы.
        self.driver_passport_series = PasteableLineEdit("18 22")
        passport_layout.addRow("Серия", self.driver_passport_series)

        self.driver_passport_number = PasteableLineEdit("926830")
        passport_layout.addRow("Номер", self.driver_passport_number)

        self.driver_passport_issuer = PasteableLineEdit(
            "Отделом УФМС России по г. Москве"
        )
        passport_layout.addRow("Кем выдан", self.driver_passport_issuer)

        self.driver_passport_issue_date = self._make_date_edit(
            QDate.currentDate()
        )
        passport_layout.addRow(
            "Дата выдачи паспорта", self.driver_passport_issue_date
        )

        layout.addWidget(passport_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Хавалы DriverTab инициализирована")

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

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Тринадцать полей водителя (даты — в ISO).

        Даты отдаются в ISO: их читает сборщик, а в формат документа
        ДД.ММ.ГГГГ их переводит он же (date.py::_date_text).
        """
        data: Dict[str, Any] = {
            field: getattr(self, field).text().strip()
            for field in self.FIELDS
        }
        for field in self.DATE_FIELDS:
            data[field] = getattr(self, field).date().toString("yyyy-MM-dd")
        return data

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет поля водителя.

        Пустые значения игнорируются: частичное распознавание не должно
        сбрасывать уже введённые ФИО и документы. Даты принимаются в любом
        формате, который понимает общий разбор (core.dates.parse_date).

        ФИО принимается только по своим ключам (driver_last_name и т. д.):
        склейки «Иванов Иван Иванович» в один ключ нет — разделение одной
        строки на части промпт запрещает, и угадывать его здесь нельзя.
        """
        if not data:
            return

        for field in self.FIELDS:
            value = str(data.get(field) or "").strip()
            if value:
                getattr(self, field).setText(value)

        for field in self.DATE_FIELDS:
            value = data.get(field)
            if value:
                self._set_date(getattr(self, field), value)

        logger.info("Хавалы: данные водителя заполнены")

    def clear(self) -> None:
        """Очищает поля водителя, даты возвращает к значениям по умолчанию."""
        for field in self.FIELDS:
            getattr(self, field).clear()

        today = QDate.currentDate()
        self.driver_license_issue_date.setDate(today)
        self.driver_passport_issue_date.setDate(today)
        self.driver_birth_date.setDate(today.addYears(-DEFAULT_BIRTH_YEARS_AGO))
        self.recognition_panel.clear()

        logger.debug("Хавалы: поля водителя очищены")


__all__ = [
    "DriverTab",
    "NoWheelDateEdit",
    "DATE_FORMAT",
    "DEFAULT_BIRTH_YEARS_AGO",
]
