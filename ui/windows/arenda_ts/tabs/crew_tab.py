#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Экипаж» окна типа «Разовая аренда» (ЭТАП 3.1.D.B.2).

Экипаж в этом типе — член экипажа Арендодателя: в бланке это раздел 3.5,
девять полей водителя. Образец — ui/tabs/driver_tab.py (паспорт, ВУ, адрес,
телефон), но состав полей другой:

  * паспорт и водительское удостоверение вводятся ОДНОЙ строкой
    («18 22 926830») — так же, как их отдаёт распознавание
    (core/prompts/arenda_ts.py). Раздельных серии и номера здесь нет
    умышленно: в контракте get_data() это ключи driver_passport и
    driver_license, а разбирает строку сборщик
    (ui/windows/arenda_ts/data.py::_split_series_number) — генератор и
    валидатор принимают оба вида;
  * нет места рождения, категорий ВУ и срока его действия: в бланке аренды
    они не печатаются.

Ключи get_data() — driver_full_name, driver_birth_date, driver_passport,
driver_passport_issuer, driver_passport_issue_date, driver_license,
driver_license_issue_date, driver_registration_address, driver_phone —
читает ui/windows/arenda_ts/data.py::_build_crew.

Кнопка «🔎» у поля «Кем выдан паспорт» (DadataDriverMixin) ищет
подразделение ФМС по коду подразделения — только по явному нажатию. Сам код
для поиска нужен, поэтому в форме есть поле «Код подразделения»; в данные
вкладки оно не попадает: в бланке его печатает строка «Кем выдан».
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import QDate, pyqtSignal
from PyQt5.QtWidgets import QDateEdit, QScrollArea, QVBoxLayout, QWidget

from ui import theme
from ui.tabs.base_tab import DadataDriverMixin
from ui.widgets import (
    PasteableDateEdit, PasteableLineEdit, PasteableTextEdit, RecognitionPanel,
)

logger = logging.getLogger("ui.windows.arenda_ts.tabs.crew_tab")

#: Сколько лет назад родился водитель — дата по умолчанию в форме.
DEFAULT_BIRTH_YEARS_AGO = 30

#: Дата в поле ввода — «дд.мм.гггг», как в остальных вкладках проекта.
DATE_FORMAT = "dd.MM.yyyy"


class NoWheelDateEdit(QDateEdit):
    """Дата с календарём без случайного изменения колёсиком мыши."""

    def wheelEvent(self, event):
        event.ignore()


class CrewTab(DadataDriverMixin, QWidget):
    """Экипаж Арендодателя: водитель, его паспорт, ВУ и контакты."""

    # Сигнал для передачи текста в окно на распознавание
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    #: Название вкладки для сообщений и логов DaData
    DADATA_TAB_TITLE = "Экипаж"

    #: Поле, в которое попадает выбранное подразделение ФМС: у вкладки оно
    #: называется driver_passport_issuer (ключ контракта get_data()).
    DADATA_FMS_TARGET_FIELD = "driver_passport_issuer"

    #: Девять полей вкладки: ровно те ключи, которые читает сборка данных
    #: (_build_crew в data.py). Больше полей здесь нет намеренно — паспорт и
    #: удостоверение приходят одной строкой, разбирает их сборщик.
    FIELDS = (
        "driver_full_name",
        "driver_passport",
        "driver_passport_issuer",
        "driver_license",
        "driver_registration_address",
        "driver_phone",
    )

    #: Поля-даты вкладки.
    DATE_FIELDS = (
        "driver_birth_date",
        "driver_passport_issue_date",
        "driver_license_issue_date",
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
            placeholder="Вставьте текст с данными водителя (ФИО, паспорт, ВУ)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Водитель» ──
        driver_group, driver_layout = theme.section_box("Водитель")

        self.driver_full_name = PasteableLineEdit("Иванов Иван Иванович")
        self.driver_full_name.set_required(True)
        driver_layout.addRow(
            theme.required_label("ФИО"), self.driver_full_name
        )

        self.driver_birth_date = self._make_date_edit(
            QDate.currentDate().addYears(-DEFAULT_BIRTH_YEARS_AGO)
        )
        driver_layout.addRow(
            theme.required_label("Дата рождения"), self.driver_birth_date
        )

        driver_layout.addRow(
            theme.make_label("Телефон"), self._make_phone_field()
        )

        layout.addWidget(driver_group)

        # ── Группа «Паспорт» ──
        passport_group, passport_layout = theme.section_box("Паспорт")

        # Серия и номер — одной строкой: так их отдаёт распознавание, и так
        # их читает сборщик (driver_passport).
        self.driver_passport = PasteableLineEdit("18 22 926830")
        self.driver_passport.set_required(True)
        passport_layout.addRow(
            theme.required_label("Серия и номер"), self.driver_passport
        )

        self.driver_passport_issue_date = self._make_date_edit(QDate.currentDate())
        passport_layout.addRow(
            theme.make_label("Дата выдачи"), self.driver_passport_issue_date
        )

        # Код подразделения нужен кнопке «🔎» (канал fms ищет по нему), но в
        # данные вкладки не входит: в бланке печатается строка «Кем выдан».
        # Значения по умолчанию здесь нет — это код из паспорта конкретного
        # человека (образец подсказки виден в подсказке поля).
        self.passport_code = PasteableLineEdit("500-123")
        self.passport_code.setMaxLength(7)
        self.passport_code.setToolTip(
            "Код подразделения из паспорта (6 цифр, например 500-123) — "
            "по нему кнопка «🔎» ищет подразделение ФМС"
        )
        passport_layout.addRow(
            theme.make_label("Код подразделения"), self.passport_code
        )

        self.driver_passport_issuer = PasteableLineEdit(
            "Отделом УФМС России по г. Москве"
        )
        # Кнопка «🔎» — подразделение ФМС по коду подразделения (DaData).
        # Запрос уходит только по нажатию, автозаполнения при вводе нет.
        passport_layout.addRow(
            theme.make_label("Кем выдан"),
            self._setup_dadata_fms_fill(self.driver_passport_issuer),
        )

        layout.addWidget(passport_group)

        # ── Группа «Водительское удостоверение» ──
        license_group, license_layout = theme.section_box("Водительское удостоверение")

        self.driver_license = PasteableLineEdit("99 36 123456")
        self.driver_license.set_required(True)
        license_layout.addRow(
            theme.required_label("Серия и номер"), self.driver_license
        )

        self.driver_license_issue_date = self._make_date_edit(QDate.currentDate())
        license_layout.addRow(
            theme.make_label("Дата выдачи"), self.driver_license_issue_date
        )

        layout.addWidget(license_group)

        # ── Группа «Адрес регистрации» ──
        address_group, address_layout = theme.section_box("Адрес регистрации")

        self.driver_registration_address = PasteableTextEdit(
            "Полный адрес регистрации", max_height=60
        )
        address_layout.addRow(
            theme.required_label("Адрес регистрации"),
            self.driver_registration_address,
        )

        layout.addWidget(address_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Разовая аренда CrewTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Виджеты вкладки
    # ─────────────────────────────────────────────────────────

    def _make_phone_field(self) -> PasteableLineEdit:
        """Поле телефона: без кнопки распознавания — она в панели сверху."""
        self.driver_phone = PasteableLineEdit("+7 (999) 123-45-67")
        return self.driver_phone

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
        Данные экипажа (даты — в ISO).

        Ровно девять полей бланка. Паспорт и удостоверение — одной строкой:
        серию и номер из неё достаёт сборщик данных, а не вкладка.
        """
        data: Dict[str, Any] = {
            "driver_full_name": self.driver_full_name.text().strip(),
            "driver_passport": self.driver_passport.text().strip(),
            "driver_passport_issuer": self.driver_passport_issuer.text().strip(),
            "driver_license": self.driver_license.text().strip(),
            "driver_registration_address": (
                self.driver_registration_address.toPlainText().strip()
            ),
            "driver_phone": self.driver_phone.text().strip(),
        }
        for field in self.DATE_FIELDS:
            data[field] = getattr(self, field).date().toString("yyyy-MM-dd")
        return data

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет данные экипажа.

        Пустые значения игнорируются: частичное распознавание не должно
        сбрасывать уже введённые паспорт и телефон. Принимаются и ключи
        вкладки (driver_full_name), и ключи ContractData (full_name,
        birth_date, registration_address): распознавание отдаёт блок driver
        целиком (core/prompts/arenda_ts.py).
        """
        if not data:
            return

        for field in self.FIELDS:
            value = self._text_value(data, field)
            if not value:
                # Блочный вид распознавания: ключ без приставки driver_.
                value = self._text_value(data, field[len("driver_"):])
            if value:
                if field == "driver_registration_address":
                    self.driver_registration_address.setPlainText(value)
                else:
                    getattr(self, field).setText(value)

        for field in self.DATE_FIELDS:
            value = data.get(field) or data.get(field[len("driver_"):])
            if value:
                self._set_date(getattr(self, field), value)

        # Серия и номер, пришедшие раздельно (справочник водителя), снова
        # склеиваются в одну строку: в контракте вкладки она одна.
        self._join_document(data, "driver_passport", "passport")
        self._join_document(data, "driver_license", "license")

        logger.info("Разовая аренда: данные экипажа заполнены")

    @staticmethod
    def _text_value(data: Dict[str, Any], key: str) -> str:
        """Значение ключа строкой без пробелов (нет ключа — пустая строка)."""
        return str(data.get(key) or "").strip()

    def _join_document(
        self, data: Dict[str, Any], field: str, document: str
    ) -> None:
        """
        Склеивает серию и номер документа в одну строку, если пришли раздельно.

        Своих полей серии и номера у вкладки нет (в контракте строка одна),
        поэтому такие данные принимаются только здесь:
        «18 22» + «926830» → «18 22 926830». Уже заполненная строка не
        трогается — введённое вручную не перезаписывается.
        """
        if getattr(self, field).text().strip():
            return

        series = (
            self._text_value(data, f"{field}_series")
            or self._text_value(data, f"{document}_series")
        )
        number = (
            self._text_value(data, f"{field}_number")
            or self._text_value(data, f"{document}_number")
        )
        if not series and not number:
            return

        getattr(self, field).setText(" ".join(part for part in (series, number) if part))
        logger.debug("Разовая аренда: %s собран из серии и номера", document)

    def clear(self) -> None:
        """Очищает поля экипажа и возвращает даты к значениям по умолчанию."""
        self.driver_full_name.clear()
        self.driver_birth_date.setDate(
            QDate.currentDate().addYears(-DEFAULT_BIRTH_YEARS_AGO)
        )
        self.driver_passport.clear()
        self.driver_passport_issue_date.setDate(QDate.currentDate())
        self.passport_code.clear()
        self.driver_passport_issuer.clear()
        self.driver_license.clear()
        self.driver_license_issue_date.setDate(QDate.currentDate())
        self.driver_registration_address.clear()
        self.driver_phone.clear()
        self.recognition_panel.clear()

        logger.debug("Разовая аренда: поля экипажа очищены")


__all__ = [
    "CrewTab",
    "NoWheelDateEdit",
    "DEFAULT_BIRTH_YEARS_AGO",
    "DATE_FORMAT",
]
