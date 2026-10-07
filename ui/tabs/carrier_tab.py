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
import re
from typing import Dict, Any, List

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit,
    QGroupBox, QDateEdit, QScrollArea,
    QComboBox,
)
from PyQt5.QtCore import QDate, pyqtSignal

from ui import theme
from ui.tabs.base_tab import DadataBankMixin, DadataFillMixin
from ui.widgets import PasteableLineEdit, PasteableTextEdit, PasteableDateEdit, RecognitionPanel

logger = logging.getLogger("ui.tabs.carrier_tab")

#: Поля-реквизиты, в которые пускаются ТОЛЬКО цифры: ИНН, КПП, ОГРН,
#: расчётный счёт, БИК, корр. счёт. Слова и знаки из них вычищаются.
DIGITS_ONLY_FIELDS = (
    "inn", "kpp", "ogrn", "bank_account", "bik", "correspondent_account",
)


def _normalize_digits(value: Any) -> str:
    """
    Только цифры из значения — для БИК, счетов, ИНН, КПП и ОГРН.

    Распознавание и вставка из чужого документа приносят поле вместе с его
    подписью: «Корреспондентский счет БИК 044030786» вместо «044030786».
    В договоре от такого значения остаётся фраза посреди реквизитов, а поле
    БИК перестаёт проходить проверку «9 цифр». Пустое значение остаётся
    пустым — ничего не выдумываем.
    """
    return re.sub(r"\D", "", "" if value is None else str(value))


class CarrierTab(DadataFillMixin, DadataBankMixin, QWidget):
    """
    Вкладка с данными перевозчика (исполнителя).

    Поля сгруппированы в смысловые блоки-карточки (тема — ui/theme.py):
    тип, общие сведения, адреса, банк, руководитель, лицензия. Порядок и
    состав полей не менялись, адреса вынесены из «Общих сведений» отдельным
    блоком — так видно, что заполнять в первую очередь.

    Кнопки «🔎» из DaData: у поля ИНН — реквизиты организации
    (DadataFillMixin), у поля БИК — банк и корр. счёт (DadataBankMixin).
    Обе срабатывают только по явному нажатию, без автозаполнения при вводе.
    """

    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    #: Объявляются в самой вкладке: TabMixin — не QObject, pyqtSignal там
    #: невозможен.
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    #: Название вкладки для сообщений и логов DaData
    DADATA_TAB_TITLE = "Перевозчик"

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
        # Кнопка «🔎» — заполнение реквизитов по ИНН из DaData.
        # Запрос уходит только по нажатию: у поля ИНН нет обработчиков ввода.
        general_layout.addRow(
            theme.required_label("ИНН"), self._setup_dadata_fill(self.inn)
        )

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
        # Кнопка «🔎» — банк и корр. счёт по БИК (DaData).
        # Запрос уходит только по нажатию: у поля БИК нет обработчиков ввода.
        bank_layout.addRow(
            theme.required_label("БИК"), self._setup_dadata_bank_fill(self.bik)
        )

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

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        # Вне прокручиваемой области: кнопки видны всегда. Внутри scroll
        # их пришлось бы искать прокруткой, а вкладка «Перевозчик» длинная.
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

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

    def _apply_dadata_extra(self, data: Dict[str, Any]) -> List[str]:
        """
        Тип перевозчика по данным DaData (только эта вкладка).

        DaData не знает про НДС, поэтому у ИП ставится «ИП с НДС», у остальных
        «ООО (с НДС)» — ставку налога пользователь уточняет сам.

        :return: список изменённых полей (для итогового диалога)
        """
        entity_type = str(data.get("entity_type") or "").strip().upper()
        if not entity_type:
            return []

        target = "ИП с НДС" if entity_type == "INDIVIDUAL" else "ООО (с НДС)"
        index = self.carrier_type.findText(target)
        if index < 0 or index == self.carrier_type.currentIndex():
            return []

        self.carrier_type.setCurrentIndex(index)
        logger.info("DaData: тип перевозчика установлен по данным организации")
        return ["carrier_type"]

    def get_data(self) -> Dict[str, Any]:
        """
        Собирает данные.

        Числовые реквизиты (ИНН, КПП, ОГРН, счета, БИК) уходят ТОЛЬКО
        цифрами: пробелы и дефисы оператор ставит для читаемости, а в
        договор и в проверку «9 цифр» они попадать не должны.
        """
        data = {
            "full_name": self.full_name.text().strip(),
            "short_name": self.short_name.text().strip(),
            "legal_address": self.legal_address.toPlainText().strip(),
            "actual_address": self.actual_address.toPlainText().strip(),
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
        for field in DIGITS_ONLY_FIELDS:
            data[field] = _normalize_digits(getattr(self, field).text())

        return data

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
            if not value:
                continue

            # Реквизиты-числа: «Корреспондентский счет БИК 044030786» в поле
            # БИК — это подпись вместе со значением, а не значение. В поле
            # кладутся только цифры; если цифр нет вовсе, поле не трогаем.
            if field in DIGITS_ONLY_FIELDS:
                digits = _normalize_digits(value)
                if not digits:
                    logger.debug(
                        "Поле %s: цифр в значении нет — оставлено как было", field
                    )
                    continue
                value = digits

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