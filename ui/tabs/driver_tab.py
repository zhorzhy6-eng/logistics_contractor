#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Водитель и ВУ».
Содержит поля для ввода данных водителя и его водительского удостоверения.

Поддерживает загрузку дат в разных форматах:
  - ISO:       2023-01-26
  - Русский:   26.01.2023
  - ISO с T:   2023-01-26T00:00:00
"""

import logging
import re
from typing import Any, Dict, List

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLineEdit, QDateEdit,
    QTextEdit, QGroupBox, QLabel, QScrollArea, QPushButton,
    QHBoxLayout, QApplication, QComboBox,
)
from PyQt5.QtCore import QDate, pyqtSignal

from db.database import get_all_organizations
from ui import theme
from ui.tabs.base_tab import DadataDriverMixin
from ui.widgets import PasteableLineEdit, PasteableTextEdit, PasteableDateEdit, RecognitionPanel

logger = logging.getLogger("ui.tabs.driver_tab")

#: Первый пункт списка перевозчиков: водитель может быть не привязан ни
#: к кому (у водителей, заведённых до этого шага, привязки нет).
CARRIER_NONE_TITLE = "— не указан —"


# ============================================================
# ХЕЛПЕР: нормализация серии паспорта / ВУ
# ============================================================
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

    # Оставляем только цифры
    digits = re.sub(r"\D", "", str(value))

    if len(digits) == 4:
        return f"{digits[:2]} {digits[2:]}"
    if len(digits) == 2:
        return digits
    if len(digits) == 3:
        return f"{digits[:2]} {digits[2:]}"

    # Если цифр больше 4 (или меньше 2) — возвращаем как было (без мусора)
    return str(value).strip()


class DriverTab(DadataDriverMixin, QWidget):
    """
    Вкладка с данными водителя.

    Кнопка «🔎» у поля «Кем выдан паспорт» ищет подразделение ФМС по коду
    подразделения (поле «Код подразделения») через DaData — только по
    явному нажатию, без автозаполнения при вводе.
    """

    # Сигнал для передачи данных в главное окно для распознавания
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    #: Название вкладки для сообщений и логов DaData
    DADATA_TAB_TITLE = "Водитель"

    def __init__(self):
        super().__init__()

        # Создаём прокручиваемую область
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

        # ── Группа «Перевозчик» (ШАГ «Привязка водителей к перевозчикам») ──
        # Связь «водитель ↔ перевозчик» раньше существовала только через
        # договоры, поэтому в справочнике водителей не было видно, на кого он
        # работает. Здесь выбирается ОСНОВНОЙ перевозчик: он подставляется
        # в договор, если в форме перевозчик не заполнен.
        carrier_group = QGroupBox("Перевозчик (где работает водитель)")
        carrier_layout = QFormLayout(carrier_group)

        self.carrier_combo = QComboBox()
        self.carrier_combo.setToolTip(
            "Основной перевозчик водителя. Подставляется в договор,\n"
            "если в нём перевозчик не выбран."
        )
        carrier_layout.addRow("Основной перевозчик", self.carrier_combo)

        self.btn_refresh_carriers = theme.secondary_button(
            "🔄 Обновить список",
            tooltip="Перечитать перевозчиков из справочника: он мог\n"
                    "пополниться, пока вкладка открыта",
        )
        self.btn_refresh_carriers.clicked.connect(self._on_refresh_carriers)
        carrier_layout.addRow("", self.btn_refresh_carriers)

        layout.addWidget(carrier_group)

        # Список перевозчиков — из общего справочника (таблица carriers).
        self.fill_carriers()

        # ── Группа «Паспортные данные» ──
        passport_group = QGroupBox("Паспортные данные")
        passport_layout = QFormLayout(passport_group)

        # ФИО
        self.full_name = PasteableLineEdit("Иванов Иван Иванович")
        passport_layout.addRow("ФИО *", self.full_name)

        # Дата рождения
        date_edit = QDateEdit()
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate().addYears(-30))
        self.birth_date = PasteableDateEdit(date_edit)
        passport_layout.addRow("Дата рождения", self.birth_date)

        # Место рождения
        self.birth_place = PasteableLineEdit("г. Москва")
        passport_layout.addRow("Место рождения", self.birth_place)

        # Серия паспорта
        # ВАЖНО: 5 символов — 4 цифры + пробел («60 24»)
        self.passport_series = PasteableLineEdit("XX XX")
        self.passport_series.setMaxLength(5)
        passport_layout.addRow("Серия паспорта *", self.passport_series)

        # Номер паспорта
        self.passport_number = PasteableLineEdit("XXXXXX")
        self.passport_number.setMaxLength(6)
        passport_layout.addRow("Номер паспорта *", self.passport_number)

        # Дата выдачи паспорта
        date_edit = QDateEdit()
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate())
        self.passport_issue_date = PasteableDateEdit(date_edit)
        passport_layout.addRow("Дата выдачи паспорта", self.passport_issue_date)

        # Кем выдан паспорт
        self.passport_issuer = PasteableLineEdit("Отделом УФМС России по г. Москве")
        # Кнопка «🔎» — подразделение ФМС по коду подразделения (DaData).
        # Запрос уходит только по нажатию, автозаполнения при вводе нет.
        passport_layout.addRow(
            "Кем выдан паспорт", self._setup_dadata_fms_fill(self.passport_issuer)
        )

        # Код подразделения
        self.passport_code = PasteableLineEdit("XXX-XXX")
        self.passport_code.setMaxLength(7)
        passport_layout.addRow("Код подразделения *", self.passport_code)

        # Адрес регистрации
        self.registration_address = PasteableTextEdit("Полный адрес регистрации", max_height=60)
        passport_layout.addRow("Адрес регистрации *", self.registration_address)

        layout.addWidget(passport_group)

        # ── Группа «Водительское удостоверение» ──
        license_group = QGroupBox("Водительское удостоверение")
        license_layout = QFormLayout(license_group)

        # Серия ВУ
        # ВАЖНО: 5 символов — 4 цифры + пробел («99 36»)
        self.license_series = PasteableLineEdit("XX XX")
        self.license_series.setMaxLength(5)
        license_layout.addRow("Серия ВУ", self.license_series)

        # Номер ВУ
        self.license_number = PasteableLineEdit("XXXXXX")
        self.license_number.setMaxLength(6)
        license_layout.addRow("Номер ВУ", self.license_number)

        # Дата выдачи ВУ
        date_edit = QDateEdit()
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate())
        self.license_issue_date = PasteableDateEdit(date_edit)
        license_layout.addRow("Дата выдачи ВУ", self.license_issue_date)

        # Срок действия ВУ
        date_edit = QDateEdit()
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate().addYears(10))
        self.license_expiry_date = PasteableDateEdit(date_edit)
        license_layout.addRow("Срок действия ВУ", self.license_expiry_date)

        # Категории ВУ
        self.license_categories = PasteableLineEdit("A, B, C, E")
        license_layout.addRow("Категории ВУ *", self.license_categories)

        # Телефон
        self.phone = PasteableLineEdit("+7 (___) ___-__-__")
        license_layout.addRow("Телефон", self.phone)

        layout.addWidget(license_group)

        # Растяжка
        layout.addStretch()

        scroll.setWidget(content_widget)

        # Основной layout
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("DriverTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Перевозчик водителя (ШАГ «Привязка водителей к перевозчикам»)
    # ─────────────────────────────────────────────────────────

    def fill_carriers(self) -> int:
        """
        Перечитывает справочник перевозчиков в выпадающий список.

        Первый пункт — «— не указан —» (itemData = None), дальше перевозчики
        из общего справочника: itemText — полное наименование (или
        сокращённое, если полного нет), itemData — id.

        Список берётся из базы при каждом вызове: перевозчиков заводят в
        «Менеджере базы» прямо во время работы, поэтому у кнопки «Обновить
        список» и у этого метода одна задача.

        :return: сколько перевозчиков в списке (без пункта «не указан»).
        """
        selected = self.carrier_combo.currentData() if hasattr(
            self, "carrier_combo"
        ) else None

        carriers: List[Dict[str, Any]] = []
        try:
            carriers = get_all_organizations(is_carrier=True)
        except Exception as e:  # noqa: BLE001 — без справочника вкладка живёт
            logger.warning(f"Справочник перевозчиков недоступен: {e}")

        self.carrier_combo.blockSignals(True)
        self.carrier_combo.clear()
        self.carrier_combo.addItem(CARRIER_NONE_TITLE, None)

        for org in carriers:
            name = str(org.get("full_name") or org.get("short_name") or "").strip()
            if not name:
                continue
            self.carrier_combo.addItem(name, org.get("id"))

        # Прежний выбор не теряется, если перевозчик остался в справочнике.
        self._select_carrier(selected)
        self.carrier_combo.blockSignals(False)

        count = self.carrier_combo.count() - 1
        logger.info(f"Список перевозчиков обновлён: {count}")
        return count

    def _select_carrier(self, carrier_id: Any) -> bool:
        """
        Выбирает перевозчика по id.

        Нет такого пункта (или id пуст) — встаёт «— не указан —»: пустая
        привязка это нормальное состояние, а не ошибка.
        """
        if carrier_id is None or carrier_id == "":
            self.carrier_combo.setCurrentIndex(0)
            return False

        try:
            wanted = int(carrier_id)
        except (TypeError, ValueError):
            logger.debug("Привязка водителя: id перевозчика не число — сброшено")
            self.carrier_combo.setCurrentIndex(0)
            return False

        for index in range(self.carrier_combo.count()):
            if self.carrier_combo.itemData(index) == wanted:
                self.carrier_combo.setCurrentIndex(index)
                return True

        logger.debug(
            f"Привязка водителя: перевозчик ID={wanted} в списке не найден — "
            f"оставлено «{CARRIER_NONE_TITLE}»"
        )
        self.carrier_combo.setCurrentIndex(0)
        return False

    def _on_refresh_carriers(self):
        """Кнопка «🔄 Обновить список»: перечитать справочник перевозчиков."""
        count = self.fill_carriers()
        logger.debug(f"UI: список перевозчиков обновлён вручную ({count})")

    def get_data(self) -> Dict[str, Any]:
        """Собирает данные из полей."""
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
            # Основной перевозчик: None — «— не указан —».
            "default_carrier_id": self.carrier_combo.currentData(),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """Заполняет поля данными."""
        if not data:
            return

        if data.get("full_name"):
            self.full_name.setText(data["full_name"])
        if data.get("birth_date"):
            self._set_date(self.birth_date, data["birth_date"])
        if data.get("birth_place"):
            self.birth_place.setText(data["birth_place"])

        # Серию нормализуем: "6024" → "60 24"
        if data.get("passport_series"):
            self.passport_series.setText(_normalize_series(data["passport_series"]))

        if data.get("passport_number"):
            self.passport_number.setText(data["passport_number"])
        if data.get("passport_issue_date"):
            self._set_date(self.passport_issue_date, data["passport_issue_date"])
        if data.get("passport_issuer"):
            self.passport_issuer.setText(data["passport_issuer"])
        if data.get("passport_code"):
            self.passport_code.setText(data["passport_code"])
        if data.get("registration_address"):
            self.registration_address.setPlainText(data["registration_address"])

        # Серию ВУ тоже нормализуем: "9936" → "99 36"
        if data.get("license_series"):
            self.license_series.setText(_normalize_series(data["license_series"]))

        if data.get("license_number"):
            self.license_number.setText(data["license_number"])
        if data.get("license_issue_date"):
            self._set_date(self.license_issue_date, data["license_issue_date"])
        if data.get("license_expiry_date"):
            self._set_date(self.license_expiry_date, data["license_expiry_date"])
        if data.get("license_categories"):
            self.license_categories.setText(data["license_categories"])
        if data.get("phone"):
            self.phone.setText(data["phone"])

        # Основной перевозчик: ключа может не быть (распознавание, зеркало,
        # старые данные) — тогда выбор не трогаем.
        if "default_carrier_id" in data:
            self._select_carrier(data.get("default_carrier_id"))

        logger.info("Данные водителя заполнены")

    def clear(self) -> None:
        """Очищает все поля."""
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
        # Основной перевозчик — на «— не указан —» (список не перечитываем).
        self._select_carrier(None)
        self.recognition_panel.clear()

        logger.debug("Поля водителя очищены")