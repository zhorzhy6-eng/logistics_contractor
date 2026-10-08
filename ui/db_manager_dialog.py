#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог управления базой данных.
Позволяет просматривать, искать, редактировать, загружать и удалять:
- водителей (включая тягач и прицеп)
- заказчиков
- перевозчиков

Двойной клик по строке = загрузить запись в форму и закрыть диалог.

Сортировка: включена по клику на заголовок, при загрузке — по алфавиту.

Два режима работы
-----------------
Режим выбирается аргументом `open_tab` (шаг FIX-1-T2):

  * без него — прежний менеджер базы (кнопка «База данных» в
    «Экспедиторстве»): видны все три вкладки, запись уходит в форму
    по «📂 Загрузить в форму» или двойному клику и вызывается один из
    обработчиков on_load_driver / on_load_customer / on_load_carrier;
  * с ним — режим ВЫБОРА записи для конкретной вкладки: открывается одна
    вкладка (OPEN_TAB_*), кнопки создания, правки и удаления скрыты —
    диалог не меняет справочник, а только отдаёт выбранную запись в
    on_pick(record). Так справочник переиспользуют вкладки аренды, не
    обзаводясь собственной базой и не путая роли сторон.

Роли аренды и таблицы справочника (см. db/database.py)
-----------------------------------------------------
  * Арендатор — НАША сторона договора аренды, её реквизиты лежат там же,
    где реквизиты заказчика: таблица customers (save_organization(...,
    is_carrier=False));
  * Арендодатель — вторая сторона, таблица carriers (is_carrier=True).
Отдельной таблицы для аренды нет и не заводится.
"""

import logging
from typing import Dict, Any, Optional, Callable, List

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QMessageBox, QLabel, QAbstractItemView,
    QLineEdit, QFormLayout, QScrollArea, QGroupBox, QTextEdit,
    QCheckBox, QComboBox, QDateEdit,
)
from PyQt5.QtCore import Qt, QTimer, QDate

from core import audit

from db.database import (
    get_all_drivers,
    get_all_organizations,
    delete_driver,
    delete_organization,
    restore_driver,
    restore_organization,
    save_driver,
    save_organization,
    search_drivers,
    search_organizations,
    update_driver,
    update_organization,
    save_driver_vehicle,
    load_driver_vehicle,
    get_driver_carriers,
    link_driver_to_carrier,
    unlink_driver_from_carrier,
    set_default_carrier,
)

from ui import theme
from ui.tabs.driver_tab import CARRIER_NONE_TITLE
from ui.widgets.table_helpers import (
    MODE_FIXED,
    install_tooltip_on_table, setup_point_table,
)

logger = logging.getLogger("ui.db_manager_dialog")

#: Значения аргумента open_tab: какую вкладку открыть в режиме выбора.
#: Совпадают с индексами вкладок QTabWidget в __init__ (перевозчики, водители,
#: заказчики) — по ним же выбирается таблица и обработчик.
OPEN_TAB_CARRIERS = "carriers"
OPEN_TAB_DRIVERS = "drivers"
OPEN_TAB_CUSTOMERS = "customers"

#: Вкладка QTabWidget по имени режима выбора.
OPEN_TAB_INDEX = {
    OPEN_TAB_CARRIERS: 0,
    OPEN_TAB_DRIVERS: 1,
    OPEN_TAB_CUSTOMERS: 2,
}

#: Колонки таблиц справочника (ШАГ FIX-6, часть E).
#: Все колонки Interactive: оператор тянет границы мышью, длинные
#: наименования и ФИО видно целиком, лишнее уходит в горизонтальную
#: прокрутку. Раскладка запоминается в QSettings — у каждой таблицы свой
#: ключ (`ui/db_manager/…`), как у остальных таблиц проекта.
ORGANIZATIONS_COLUMNS_CONFIG = (
    (0, MODE_FIXED, 60),    # ID
    (1, MODE_FIXED, 320),   # Наименование
    (2, MODE_FIXED, 130),   # ИНН
    (3, MODE_FIXED, 120),   # КПП
    (4, MODE_FIXED, 250),   # Директор
    (5, MODE_FIXED, 90),    # Статус
)

DRIVERS_COLUMNS_CONFIG = (
    (0, MODE_FIXED, 60),    # ID
    (1, MODE_FIXED, 260),   # ФИО
    (2, MODE_FIXED, 220),   # Перевозчик (ШАГ «Привязка водителей…»)
    (3, MODE_FIXED, 110),   # Дата рождения
    (4, MODE_FIXED, 140),   # Паспорт
    (5, MODE_FIXED, 140),   # Телефон
    (6, MODE_FIXED, 90),    # Статус
)

#: Нижние границы ширин: ниже них колонка не сжимается.
ORGANIZATIONS_COLUMN_MINIMUMS = {0: 50, 1: 160, 2: 100, 3: 90, 4: 140, 5: 80}
DRIVERS_COLUMN_MINIMUMS = {0: 50, 1: 160, 2: 160, 3: 110, 4: 140, 5: 140, 6: 80}

#: Номера колонок таблицы водителей: читаются по имени, чтобы добавление
#: колонки «Перевозчик» не сдвинуло молча остальные (ШАГ «Привязка
#: водителей к перевозчикам»).
DRIVER_COL_ID = 0
DRIVER_COL_NAME = 1
DRIVER_COL_CARRIER = 2
DRIVER_COL_BIRTH_DATE = 3
DRIVER_COL_PASSPORT = 4
DRIVER_COL_PHONE = 5
DRIVER_COL_STATUS = 6

#: Пункт фильтра «Все перевозчики» и «без перевозчика» в списке водителей.
CARRIER_FILTER_ALL = -1
CARRIER_FILTER_NONE = 0

#: Ключи QSettings для раскладки таблиц справочника.
ORGANIZATIONS_WIDTHS_KEY = "ui/db_manager/organizations"
DRIVERS_WIDTHS_KEY = "ui/db_manager/drivers"
#: Раскладка таблицы истории работы водителя у перевозчиков.
DRIVER_CARRIERS_WIDTHS_KEY = "ui/db_manager/driver_carriers"


# ═════════════════════════════════════════════════════════════
# Диалоги редактирования
# ═════════════════════════════════════════════════════════════

class EditCarrierDialog(QDialog):
    """Диалог создания/редактирования перевозчика или заказчика."""

    def __init__(self, org: Dict[str, Any], is_carrier: bool = True, parent=None):
        super().__init__(parent)

        self.is_carrier = is_carrier
        # Режим создания: запись без id (пустой dict из _on_add_org)
        self.is_new = not org.get("id")

        if self.is_new:
            title = "➕ Новый перевозчик" if is_carrier else "➕ Новый заказчик"
        else:
            title = "✏ Редактирование перевозчика" if is_carrier else "✏ Редактирование заказчика"
        self.setWindowTitle(title)
        self.setMinimumSize(650, 700)

        layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        content = QWidget()
        content_layout = QVBoxLayout(content)

        # ── Общие сведения ──
        g1 = QGroupBox("Общие сведения")
        f1 = QFormLayout(g1)
        self.full_name = QLineEdit(org.get("full_name", ""))
        self.short_name = QLineEdit(org.get("short_name", ""))
        self.inn = QLineEdit(org.get("inn", ""))
        self.kpp = QLineEdit(org.get("kpp", ""))
        self.ogrn = QLineEdit(org.get("ogrn", ""))
        f1.addRow("Полное наименование:", self.full_name)
        f1.addRow("Сокращённое:", self.short_name)
        f1.addRow("ИНН:", self.inn)
        f1.addRow("КПП:", self.kpp)
        f1.addRow("ОГРН:", self.ogrn)
        content_layout.addWidget(g1)

        # ── Адреса ──
        g2 = QGroupBox("Адреса")
        f2 = QFormLayout(g2)
        self.legal_address = QTextEdit(org.get("legal_address", ""))
        self.legal_address.setMaximumHeight(60)
        self.actual_address = QTextEdit(org.get("actual_address", ""))
        self.actual_address.setMaximumHeight(60)
        f2.addRow("Юридический адрес:", self.legal_address)
        f2.addRow("Фактический адрес:", self.actual_address)
        content_layout.addWidget(g2)

        # ── Банк ──
        g3 = QGroupBox("Банковские реквизиты")
        f3 = QFormLayout(g3)
        self.bank_account = QLineEdit(org.get("bank_account", ""))
        self.bik = QLineEdit(org.get("bik", ""))
        self.corr_account = QLineEdit(org.get("correspondent_account", ""))
        self.bank_name = QLineEdit(org.get("bank_name", ""))
        f3.addRow("Расчётный счёт:", self.bank_account)
        f3.addRow("БИК:", self.bik)
        f3.addRow("Корр. счёт:", self.corr_account)
        f3.addRow("Банк:", self.bank_name)
        content_layout.addWidget(g3)

        # ── Директор ──
        g4 = QGroupBox("Директор и контакты")
        f4 = QFormLayout(g4)
        self.director_name = QLineEdit(org.get("director_name", ""))
        self.director_position = QLineEdit(org.get("director_position", ""))
        self.phone = QLineEdit(org.get("phone", ""))
        self.email = QLineEdit(org.get("email", ""))
        f4.addRow("ФИО директора:", self.director_name)
        f4.addRow("Должность:", self.director_position)
        f4.addRow("Телефон:", self.phone)
        f4.addRow("Email:", self.email)

        if is_carrier:
            self.license_number = QLineEdit(org.get("license_number", ""))
            self.license_date = QLineEdit(org.get("license_date", ""))
            f4.addRow("Номер лицензии:", self.license_number)
            f4.addRow("Дата лицензии:", self.license_date)
        else:
            self.license_number = None
            self.license_date = None

        content_layout.addWidget(g4)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

        # Кнопки
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_cancel = theme.secondary_button("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_save = theme.accent_button(
            "💾 Сохранить",
            tooltip="Создать запись" if self.is_new else "Сохранить изменения",
        )
        btn_save.clicked.connect(self._on_save)
        btn_layout.addWidget(btn_save)

        layout.addLayout(btn_layout)

    def _on_save(self):
        if not self.full_name.text().strip():
            QMessageBox.warning(self, "Ошибка", "Полное наименование обязательно.")
            return
        self.accept()

    def get_data(self) -> Dict[str, Any]:
        data = {
            "full_name": self.full_name.text().strip(),
            "short_name": self.short_name.text().strip(),
            "inn": self.inn.text().strip(),
            "kpp": self.kpp.text().strip(),
            "ogrn": self.ogrn.text().strip(),
            "legal_address": self.legal_address.toPlainText().strip(),
            "actual_address": self.actual_address.toPlainText().strip(),
            "bank_account": self.bank_account.text().strip(),
            "bik": self.bik.text().strip(),
            "correspondent_account": self.corr_account.text().strip(),
            "bank_name": self.bank_name.text().strip(),
            "director_name": self.director_name.text().strip(),
            "director_position": self.director_position.text().strip(),
            "phone": self.phone.text().strip(),
            "email": self.email.text().strip(),
        }
        if self.is_carrier and self.license_number is not None:
            data["license_number"] = self.license_number.text().strip()
            data["license_date"] = self.license_date.text().strip()
        return data


class EditDriverDialog(QDialog):
    """Диалог создания/редактирования водителя (с тягачом и прицепом)."""

    def __init__(self, driver: Dict[str, Any], parent=None):
        super().__init__(parent)

        self.driver_id = driver.get("id")
        # Режим создания: запись без id (пустой dict из _on_add_driver)
        self.is_new = not self.driver_id

        self.setWindowTitle(
            "➕ Новый водитель" if self.is_new else "✏ Редактирование водителя"
        )
        self.setMinimumSize(620, 850)

        layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        content = QWidget()
        content_layout = QVBoxLayout(content)

        # ── Паспорт ──
        g1 = QGroupBox("Паспортные данные")
        f1 = QFormLayout(g1)
        self.full_name = QLineEdit(driver.get("full_name", ""))
        self.birth_date = QLineEdit(driver.get("birth_date", ""))
        self.birth_place = QLineEdit(driver.get("birth_place", ""))
        self.passport_series = QLineEdit(driver.get("passport_series", ""))
        self.passport_number = QLineEdit(driver.get("passport_number", ""))
        self.passport_issue_date = QLineEdit(driver.get("passport_issue_date", ""))
        self.passport_issuer = QLineEdit(driver.get("passport_issuer", ""))
        self.passport_code = QLineEdit(driver.get("passport_code", ""))
        self.registration_address = QTextEdit(driver.get("registration_address", ""))
        self.registration_address.setMaximumHeight(60)

        f1.addRow("ФИО:", self.full_name)
        f1.addRow("Дата рождения:", self.birth_date)
        f1.addRow("Место рождения:", self.birth_place)
        f1.addRow("Серия паспорта:", self.passport_series)
        f1.addRow("Номер паспорта:", self.passport_number)
        f1.addRow("Дата выдачи:", self.passport_issue_date)
        f1.addRow("Кем выдан:", self.passport_issuer)
        f1.addRow("Код подразделения:", self.passport_code)
        f1.addRow("Адрес регистрации:", self.registration_address)
        content_layout.addWidget(g1)

        # ── ВУ ──
        g2 = QGroupBox("Водительское удостоверение")
        f2 = QFormLayout(g2)
        self.license_series = QLineEdit(driver.get("license_series", ""))
        self.license_number = QLineEdit(driver.get("license_number", ""))
        self.license_issue_date = QLineEdit(driver.get("license_issue_date", ""))
        self.license_expiry_date = QLineEdit(driver.get("license_expiry_date", ""))
        self.license_categories = QLineEdit(driver.get("license_categories", ""))
        self.phone = QLineEdit(driver.get("phone", ""))
        f2.addRow("Серия ВУ:", self.license_series)
        f2.addRow("Номер ВУ:", self.license_number)
        f2.addRow("Дата выдачи ВУ:", self.license_issue_date)
        f2.addRow("Срок действия:", self.license_expiry_date)
        f2.addRow("Категории:", self.license_categories)
        f2.addRow("Телефон:", self.phone)
        content_layout.addWidget(g2)

        # ── Перевозчик (ШАГ «Привязка водителей к перевозчикам») ──
        # Основной перевозчик водителя: тот же справочник carriers, что
        # и вкладка «Водитель» Экспедиторства. История работы (у каких
        # перевозчиков и с каких дат) — отдельным диалогом.
        g_carrier = QGroupBox("Перевозчик")
        f_carrier = QFormLayout(g_carrier)

        self.carrier_combo = QComboBox()
        self.carrier_combo.setToolTip(
            "Основной перевозчик водителя. Подставляется в договор,\n"
            "если в нём перевозчик не выбран."
        )
        self._fill_carriers()
        self._select_carrier(driver.get("default_carrier_id"))
        f_carrier.addRow("Перевозчик:", self.carrier_combo)

        self.btn_carrier_history = theme.secondary_button(
            "📅 История работы у перевозчиков…",
            tooltip="У каких перевозчиков водитель работал и с каких дат",
        )
        self.btn_carrier_history.clicked.connect(self._on_carrier_history)
        if self.is_new:
            # Историю несут записи driver_carriers, а у новой записи id ещё нет.
            self.btn_carrier_history.setEnabled(False)
            self.btn_carrier_history.setToolTip(
                "История появится после сохранения водителя"
            )
        f_carrier.addRow("", self.btn_carrier_history)
        content_layout.addWidget(g_carrier)

        # ── Тягач ──
        g3 = QGroupBox("Тягач")
        f3 = QFormLayout(g3)
        self.tractor_brand = QLineEdit()
        self.tractor_plate = QLineEdit()
        self.tractor_color = QLineEdit()
        self.tractor_year = QLineEdit()
        f3.addRow("Марка/Модель:", self.tractor_brand)
        f3.addRow("Гос. номер:", self.tractor_plate)
        f3.addRow("Цвет:", self.tractor_color)
        f3.addRow("Год выпуска:", self.tractor_year)
        content_layout.addWidget(g3)

        # ── Прицеп ──
        g4 = QGroupBox("Прицеп / Полуприцеп")
        f4 = QFormLayout(g4)
        self.trailer_brand = QLineEdit()
        self.trailer_plate = QLineEdit()
        self.trailer_color = QLineEdit()
        self.trailer_year = QLineEdit()
        f4.addRow("Марка/Модель:", self.trailer_brand)
        f4.addRow("Гос. номер:", self.trailer_plate)
        f4.addRow("Цвет:", self.trailer_color)
        f4.addRow("Год выпуска:", self.trailer_year)
        content_layout.addWidget(g4)

        # Загружаем тягач/прицеп из БД
        self._load_driver_vehicle()

        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

        # Кнопки
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_cancel = theme.secondary_button("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_save = theme.accent_button(
            "💾 Сохранить",
            tooltip="Создать запись" if self.is_new else "Сохранить изменения",
        )
        btn_save.clicked.connect(self._on_save)
        btn_layout.addWidget(btn_save)

        layout.addLayout(btn_layout)

    def _fill_carriers(self) -> int:
        """
        Заполняет список перевозчиков диалога.

        Первый пункт — «— не указан —» (itemData = None). Справочник может
        быть недоступен (нет базы) — тогда список остаётся с одним пунктом,
        диалог этим не ломается.

        :return: сколько перевозчиков в списке (без пункта «не указан»).
        """
        carriers: List[Dict[str, Any]] = []
        try:
            carriers = get_all_organizations(is_carrier=True)
        except Exception as e:  # noqa: BLE001 — без справочника диалог живёт
            logger.warning(f"Справочник перевозчиков недоступен: {e}")

        self.carrier_combo.clear()
        self.carrier_combo.addItem(CARRIER_NONE_TITLE, None)
        for org in carriers:
            name = str(org.get("full_name") or org.get("short_name") or "").strip()
            if not name:
                continue
            self.carrier_combo.addItem(name, org.get("id"))

        return self.carrier_combo.count() - 1

    def _select_carrier(self, carrier_id: Any) -> bool:
        """Выбирает перевозчика по id; нет такого — «— не указан —»."""
        if carrier_id is None or carrier_id == "":
            self.carrier_combo.setCurrentIndex(0)
            return False

        try:
            wanted = int(carrier_id)
        except (TypeError, ValueError):
            self.carrier_combo.setCurrentIndex(0)
            return False

        for index in range(self.carrier_combo.count()):
            if self.carrier_combo.itemData(index) == wanted:
                self.carrier_combo.setCurrentIndex(index)
                return True

        logger.debug(
            f"Водитель ID={self.driver_id}: перевозчик ID={wanted} "
            f"в справочнике не найден — оставлено «{CARRIER_NONE_TITLE}»"
        )
        self.carrier_combo.setCurrentIndex(0)
        return False

    def _on_carrier_history(self):
        """Кнопка «История работы у перевозчиков…»."""
        if not self.driver_id:
            QMessageBox.information(
                self, "История работы",
                "Сначала сохраните водителя — история привязана к записи.",
            )
            return

        dialog = DriverCarrierHistoryDialog(self.driver_id, parent=self)
        dialog.exec_()

    def _load_driver_vehicle(self):
        """Подгружает тягач/прицеп из БД."""
        if not self.driver_id:
            return
        try:
            data = load_driver_vehicle(self.driver_id)
            if data:
                self.tractor_brand.setText(data.get("tractor_brand", "") or "")
                self.tractor_plate.setText(data.get("tractor_plate", "") or "")
                self.tractor_color.setText(data.get("tractor_color", "") or "")
                self.tractor_year.setText(data.get("tractor_year", "") or "")
                self.trailer_brand.setText(data.get("trailer_brand", "") or "")
                self.trailer_plate.setText(data.get("trailer_plate", "") or "")
                self.trailer_color.setText(data.get("trailer_color", "") or "")
                self.trailer_year.setText(data.get("trailer_year", "") or "")
        except Exception as e:
            logger.warning(f"Не удалось загрузить тягач/прицеп: {e}")

    def _on_save(self):
        if not self.full_name.text().strip():
            QMessageBox.warning(self, "Ошибка", "ФИО обязательно.")
            return
        self.accept()

    def get_driver_data(self) -> Dict[str, Any]:
        return {
            "full_name": self.full_name.text().strip(),
            "birth_date": self.birth_date.text().strip(),
            "birth_place": self.birth_place.text().strip(),
            "passport_series": self.passport_series.text().strip(),
            "passport_number": self.passport_number.text().strip(),
            "passport_issue_date": self.passport_issue_date.text().strip(),
            "passport_issuer": self.passport_issuer.text().strip(),
            "passport_code": self.passport_code.text().strip(),
            "registration_address": self.registration_address.toPlainText().strip(),
            "license_series": self.license_series.text().strip(),
            "license_number": self.license_number.text().strip(),
            "license_issue_date": self.license_issue_date.text().strip(),
            "license_expiry_date": self.license_expiry_date.text().strip(),
            "license_categories": self.license_categories.text().strip(),
            "phone": self.phone.text().strip(),
            # Основной перевозчик: None — «— не указан —».
            "default_carrier_id": self.carrier_combo.currentData(),
        }

    def get_vehicle_data(self) -> Dict[str, Any]:
        return {
            "tractor_brand": self.tractor_brand.text().strip(),
            "tractor_plate": self.tractor_plate.text().strip(),
            "tractor_color": self.tractor_color.text().strip(),
            "tractor_year": self.tractor_year.text().strip(),
            "trailer_brand": self.trailer_brand.text().strip(),
            "trailer_plate": self.trailer_plate.text().strip(),
            "trailer_color": self.trailer_color.text().strip(),
            "trailer_year": self.trailer_year.text().strip(),
        }


class DriverCarrierHistoryDialog(QDialog):
    """
    История работы водителя у перевозчиков (ШАГ «Привязка водителей
    к перевозчикам»).

    Таблица — записи driver_carriers: перевозчик, дата начала, дата
    окончания. Пустая дата окончания значит «работает сейчас».
    «Добавить» открывает связь с выбранным перевозчиком (и закрывает
    прежнюю активную), «Закрыть связь» ставит дату окончания у выбранной
    строки. Записи не удаляются: это история.
    """

    #: Колонки таблицы истории.
    COLUMNS = ("Перевозчик", "Начало", "Окончание")

    def __init__(self, driver_id: int, parent=None):
        super().__init__(parent)

        self.driver_id = driver_id
        self.setWindowTitle("📅 История работы у перевозчиков")
        self.setMinimumSize(640, 460)

        layout = QVBoxLayout(self)

        layout.addWidget(theme.page_title(
            "История работы у перевозчиков",
            "💡 Пустое «Окончание» — водитель работает у этого перевозчика сейчас\n"
            "💡 «Добавить» открывает новую связь и закрывает прежнюю активную\n"
            "💡 «Закрыть связь» ставит дату окончания — запись остаётся историей",
        ))

        # ── Добавление связи ──
        add_layout = QHBoxLayout()
        add_layout.addWidget(QLabel("Перевозчик:"))

        self.carrier_combo = QComboBox()
        self.carrier_combo.setMinimumWidth(240)
        add_layout.addWidget(self.carrier_combo, 1)

        add_layout.addWidget(QLabel("Начало:"))
        self.started_edit = QDateEdit()
        self.started_edit.setDisplayFormat("dd.MM.yyyy")
        self.started_edit.setCalendarPopup(True)
        self.started_edit.setDate(QDate.currentDate())
        add_layout.addWidget(self.started_edit)

        self.btn_add = theme.primary_button(
            "➕ Добавить", tooltip="Открыть связь с выбранным перевозчиком",
        )
        self.btn_add.clicked.connect(self._on_add)
        add_layout.addWidget(self.btn_add)

        layout.addLayout(add_layout)

        # ── Таблица истории ──
        self.table = QTableWidget()
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        setup_point_table(
            self.table,
            (
                (0, MODE_FIXED, 300),   # Перевозчик
                (1, MODE_FIXED, 120),   # Начало
                (2, MODE_FIXED, 120),   # Окончание
            ),
            storage_key=DRIVER_CARRIERS_WIDTHS_KEY,
            minimums={0: 160, 1: 100, 2: 100},
        )
        install_tooltip_on_table(self.table)
        layout.addWidget(self.table)

        # ── Кнопки ──
        buttons = QHBoxLayout()

        self.btn_close_link = theme.danger_button(
            "🔗 Закрыть связь",
            tooltip="Поставить дату окончания у выбранной записи",
        )
        self.btn_close_link.clicked.connect(self._on_close_link)
        buttons.addWidget(self.btn_close_link)

        buttons.addStretch()

        self.btn_close = theme.secondary_button("Закрыть")
        self.btn_close.clicked.connect(self.accept)
        buttons.addWidget(self.btn_close)

        layout.addLayout(buttons)

        self._load_carriers()
        self.reload()

    # ─────────────────────────────────────────────────────────
    # Данные
    # ─────────────────────────────────────────────────────────

    def _load_carriers(self) -> None:
        """Список перевозчиков для добавления связи."""
        try:
            carriers = get_all_organizations(is_carrier=True)
        except Exception as e:  # noqa: BLE001 — без справочника список пуст
            logger.warning(f"Справочник перевозчиков недоступен: {e}")
            carriers = []

        self.carrier_combo.clear()
        for org in carriers:
            name = str(org.get("full_name") or org.get("short_name") or "").strip()
            if not name:
                continue
            self.carrier_combo.addItem(name, org.get("id"))

    def reload(self) -> None:
        """Перечитывает историю работы водителя из базы."""
        try:
            links = get_driver_carriers(self.driver_id)
        except Exception as e:  # noqa: BLE001 — пустая история лучше падения
            logger.error(f"Ошибка чтения истории работы водителя: {e}")
            links = []

        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        for link in links:
            row = self.table.rowCount()
            self.table.insertRow(row)

            item_carrier = QTableWidgetItem(
                str(link.get("carrier_name") or link.get("carrier_short_name") or "")
            )
            item_carrier.setData(Qt.UserRole, link)
            self.table.setItem(row, 0, item_carrier)

            started = str(link.get("started_at") or "")
            finished = str(link.get("ended_at") or "")
            self.table.setItem(row, 1, QTableWidgetItem(started))
            # Пустое окончание — активная связь: пишем словами, чтобы
            # пустая ячейка не читалась как «данных нет».
            item_finish = QTableWidgetItem(finished or "работает")
            if not finished:
                item_finish.setForeground(theme.deleted_row_color())
            self.table.setItem(row, 2, item_finish)

        self.table.setSortingEnabled(True)
        logger.debug(
            f"История работы водителя ID={self.driver_id}: записей {len(links)}"
        )

    def _current_link(self) -> Optional[Dict[str, Any]]:
        """Выбранная запись истории (None — строка не выбрана)."""
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    # ─────────────────────────────────────────────────────────
    # Действия
    # ─────────────────────────────────────────────────────────

    def _on_add(self):
        """Открывает связь с выбранным перевозчиком."""
        carrier_id = self.carrier_combo.currentData()
        if not carrier_id:
            QMessageBox.warning(
                self, "Добавление связи",
                "Выберите перевозчика в списке. Если справочник пуст — "
                "заведите перевозчика на вкладке «🚛 Перевозчики».",
            )
            return

        started = self.started_edit.date().toString("yyyy-MM-dd")
        link_id = link_driver_to_carrier(
            self.driver_id, int(carrier_id), started_at=started
        )
        if not link_id:
            QMessageBox.critical(self, "Ошибка", "Не удалось добавить связь.")
            return

        self.reload()
        logger.info(
            f"Связь водителя ID={self.driver_id} с перевозчиком "
            f"ID={carrier_id} добавлена"
        )

    def _on_close_link(self):
        """Ставит дату окончания у выбранной связи."""
        link = self._current_link()
        if not link:
            QMessageBox.warning(self, "Закрытие связи", "Выберите запись в списке.")
            return

        if str(link.get("ended_at") or ""):
            QMessageBox.information(
                self, "Закрытие связи", "У этой записи уже стоит дата окончания."
            )
            return

        carrier_id = link.get("carrier_id")
        if carrier_id in (None, ""):
            QMessageBox.warning(
                self, "Закрытие связи", "В записи нет перевозчика."
            )
            return
        if not unlink_driver_from_carrier(self.driver_id, int(carrier_id)):
            QMessageBox.critical(self, "Ошибка", "Не удалось закрыть связь.")
            return

        self.reload()
        logger.info(
            f"Связь водителя ID={self.driver_id} с перевозчиком "
            f"ID={carrier_id} закрыта"
        )


# ═════════════════════════════════════════════════════════════
# Основной диалог
# ═════════════════════════════════════════════════════════════

class DbManagerDialog(QDialog):
    """Диалог управления базой данных (менеджер записей или режим выбора)."""

    # Сколько строк максимум показывать при поиске
    # (защита от вывода тысяч строк в таблицу)
    SEARCH_LIMIT = 500

    def __init__(
        self,
        parent=None,
        on_load_driver: Optional[Callable] = None,
        on_load_customer: Optional[Callable] = None,
        on_load_carrier: Optional[Callable] = None,
        *,
        open_tab: str = "",
        show_deleted: bool = False,
        on_pick: Optional[Callable] = None,
    ):
        """
        :param open_tab: "" — прежний менеджер базы (три вкладки, правка и
            удаление записей); "carriers" / "drivers" / "customers" — режим
            выбора записи: одна вкладка, кнопки правки скрыты, выбранная
            запись уходит в on_pick(record).
        :param show_deleted: показывать ли мягко удалённые записи. В режиме
            выбора по умолчанию скрыты: выбирать убранное из справочника
            незачем (переключатель на вкладке остаётся доступен).
        :param on_pick: обработчик выбранной записи в режиме выбора.
        """
        super().__init__(parent)

        #: Режим выбора записи ("" — менеджер базы, как раньше).
        self.picker_tab = open_tab if open_tab in OPEN_TAB_INDEX else ""
        self.on_pick = on_pick

        self.on_load_driver = on_load_driver
        self.on_load_customer = on_load_customer
        self.on_load_carrier = on_load_carrier

        self.setWindowTitle(
            "🗄 Управление базой данных" if not self.picker_tab
            else "🗄 Выбор записи из справочника"
        )
        self.setMinimumSize(1100, 650)

        layout = QVBoxLayout(self)

        if self.picker_tab:
            layout.addWidget(theme.page_title(
                "Выбор записи из справочника",
                "💡 Двойной клик по строке — выбрать запись и заполнить вкладку\n"
                "💡 Клик по заголовку столбца — сортировка\n"
                "💡 Справочник общий: то, что сохранено здесь, видно и в других "
                "окнах программы",
            ))
        else:
            layout.addWidget(theme.page_title(
                "Управление сохранёнными записями",
                "💡 Двойной клик по строке — загрузить запись в форму\n"
                "💡 Клик по заголовку столбца — сортировка\n"
                "💡 «➕ Добавить» создаёт новую запись вручную\n"
                "💡 Удаление мягкое: запись скрывается из справочника и её можно "
                "вернуть кнопкой «♻ Восстановить»",
            ))

        # ── Показ мягко удалённых записей (вариант В) ──
        self.chk_deleted = QCheckBox("Показывать удалённые")
        self.chk_deleted.setChecked(bool(show_deleted))
        self.chk_deleted.setToolTip(
            "Показать записи, убранные из справочника. Их можно восстановить."
        )
        self.chk_deleted.stateChanged.connect(self._on_toggle_deleted)
        layout.addWidget(self.chk_deleted)

        self.tabs = QTabWidget()

        self.carriers_tab = self._create_org_tab(is_carrier=True)
        self.tabs.addTab(self.carriers_tab, "🚛 Перевозчики")

        self.drivers_tab = self._create_driver_tab()
        self.tabs.addTab(self.drivers_tab, "👤 Водители")

        self.customers_tab = self._create_org_tab(is_carrier=False)
        self.tabs.addTab(self.customers_tab, "🏢 Заказчики")

        if self.picker_tab:
            # Оставляем одну вкладку: выбор идёт по нужной роли, и спутать
            # перевозчика с заказчиком (а в аренде — арендодателя с
            # арендатором) нельзя.
            for name in (OPEN_TAB_CARRIERS, OPEN_TAB_DRIVERS, OPEN_TAB_CUSTOMERS):
                if name != self.picker_tab:
                    self.tabs.setTabVisible(OPEN_TAB_INDEX[name], False)
            self.tabs.setCurrentIndex(OPEN_TAB_INDEX[self.picker_tab])

        layout.addWidget(self.tabs)

        close_layout = QHBoxLayout()
        close_layout.addStretch()

        self.btn_close = theme.secondary_button("Закрыть")
        self.btn_close.clicked.connect(self.accept)
        close_layout.addWidget(self.btn_close)

        layout.addLayout(close_layout)

        # ── Debounce поиска (Шаг 5 оптимизации) ──
        self._org_search_text = {True: "", False: ""}
        self._org_timers = {}
        for is_carrier in (True, False):
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(250)
            timer.timeout.connect(lambda c=is_carrier: self._apply_org_filter(c))
            self._org_timers[is_carrier] = timer

        self._driver_search_text = ""
        self._driver_timer = QTimer(self)
        self._driver_timer.setSingleShot(True)
        self._driver_timer.setInterval(250)
        self._driver_timer.timeout.connect(self._apply_driver_filter)

        self._load_all()

        logger.debug(
            f"DbManagerDialog инициализирован (режим выбора: "
            f"{self.picker_tab or 'нет'})"
        )

    # ─────────────────────────────────────────────────────────
    # Вкладки
    # ─────────────────────────────────────────────────────────

    def _create_org_tab(self, is_carrier: bool) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("🔍 Поиск:"))
        search_input = QLineEdit()
        search_input.setPlaceholderText("Наименование, ИНН или ФИО директора...")
        search_input.setClearButtonEnabled(True)
        search_layout.addWidget(search_input, 1)
        btn_reset = theme.secondary_button("Сбросить")
        search_layout.addWidget(btn_reset)
        layout.addLayout(search_layout)

        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(
            ["ID", "Наименование", "ИНН", "КПП", "Директор", "Статус"]
        )

        # ── СОРТИРОВКА ──
        table.setSortingEnabled(True)

        # ── ШИРИНЫ, ПОДСКАЗКИ И РАСКЛАДКА (ШАГ FIX-6, часть E) ──
        # Раньше ширины задавались здесь вручную и нигде не запоминались:
        # после перезапуска раскладка возвращалась к исходной. Теперь — общий
        # помощник: все колонки Interactive, у каждой свой минимум, длинное
        # наименование видно в подсказке, раскладка живёт в QSettings.
        setup_point_table(
            table,
            ORGANIZATIONS_COLUMNS_CONFIG,
            storage_key=ORGANIZATIONS_WIDTHS_KEY,
            minimums=ORGANIZATIONS_COLUMN_MINIMUMS,
        )
        install_tooltip_on_table(table)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setWordWrap(False)

        # ── Двойной клик = загрузить в форму ──
        table.doubleClicked.connect(lambda: self._on_load_org(is_carrier))

        #: Роль таблицы: по этому признаку выбирается таблица-источник и
        #: обработчик записи (в аренде — арендатор / арендодатель).
        table.setProperty("is_carrier", is_carrier)

        if is_carrier:
            self.carriers_table = table
        else:
            self.customers_table = table

        layout.addWidget(table)

        button_layout = QHBoxLayout()

        btn_add = theme.primary_button(
            "➕ Добавить",
            tooltip=(
                "Создать нового перевозчика вручную"
                if is_carrier else
                "Создать нового заказчика вручную"
            ),
        )
        btn_add.clicked.connect(lambda: self._on_add_org(is_carrier))
        button_layout.addWidget(btn_add)

        btn_load = theme.secondary_button("📂 Загрузить в форму")
        btn_load.clicked.connect(lambda: self._on_load_org(is_carrier))
        button_layout.addWidget(btn_load)

        btn_edit = theme.secondary_button("✏ Редактировать")
        btn_edit.clicked.connect(lambda: self._on_edit_org(is_carrier))
        button_layout.addWidget(btn_edit)

        btn_delete = theme.danger_button("🗑 Удалить")
        btn_delete.clicked.connect(lambda: self._on_delete_org(is_carrier))
        button_layout.addWidget(btn_delete)

        btn_restore = theme.secondary_button(
            "♻ Восстановить",
            tooltip="Вернуть мягко удалённую запись в справочник",
        )
        btn_restore.clicked.connect(lambda: self._on_restore_org(is_carrier))
        button_layout.addWidget(btn_restore)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        # В режиме выбора справочник только читают: кнопки, которые его
        # меняют, прячем — оставляем «Загрузить в форму».
        if self.picker_tab:
            self._apply_picker_mode(
                (btn_add, btn_load, btn_edit, btn_delete, btn_restore)
            )

        search_input.textChanged.connect(lambda text, c=is_carrier: self._schedule_org_filter(c, text))
        btn_reset.clicked.connect(lambda: search_input.clear())

        return widget

    def _create_driver_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("🔍 Поиск:"))
        search_input = QLineEdit()
        search_input.setPlaceholderText("ФИО, паспорт или телефон...")
        search_input.setClearButtonEnabled(True)
        search_layout.addWidget(search_input, 1)
        btn_reset = theme.secondary_button("Сбросить")
        search_layout.addWidget(btn_reset)
        layout.addLayout(search_layout)

        # ── Фильтр по перевозчику (ШАГ «Привязка водителей
        # к перевозчикам») ──
        # Показывает водителей, закреплённых за одним перевозчиком, либо
        # тех, у кого привязки нет вовсе.
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Перевозчик:"))
        self.carrier_filter = QComboBox()
        self.carrier_filter.setMinimumWidth(280)
        self.carrier_filter.setToolTip(
            "Показать водителей, закреплённых за перевозчиком.\n"
            "«— без перевозчика —» — те, у кого привязки нет."
        )
        filter_layout.addWidget(self.carrier_filter, 1)
        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        self._fill_carrier_filter()
        self.carrier_filter.currentIndexChanged.connect(
            self._on_carrier_filter_changed
        )

        table = QTableWidget()
        table.setColumnCount(len(DRIVERS_COLUMNS_CONFIG))
        table.setHorizontalHeaderLabels(
            ["ID", "ФИО", "Перевозчик", "Дата рождения",
             "Паспорт", "Телефон", "Статус"]
        )

        # ── СОРТИРОВКА ──
        table.setSortingEnabled(True)

        # ── ШИРИНЫ, ПОДСКАЗКИ И РАСКЛАДКА (ШАГ FIX-6, часть E) ──
        # См. таблицу организаций: общий помощник, все колонки Interactive,
        # минимумы, подсказки и сохранение раскладки в QSettings.
        setup_point_table(
            table,
            DRIVERS_COLUMNS_CONFIG,
            storage_key=DRIVERS_WIDTHS_KEY,
            minimums=DRIVERS_COLUMN_MINIMUMS,
        )
        install_tooltip_on_table(table)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setWordWrap(False)

        # ── Двойной клик = загрузить водителя ──
        table.doubleClicked.connect(self._on_load_driver)

        self.drivers_table = table
        layout.addWidget(table)

        button_layout = QHBoxLayout()

        btn_add = theme.primary_button(
            "➕ Добавить",
            tooltip="Создать нового водителя вручную",
        )
        btn_add.clicked.connect(self._on_add_driver)
        button_layout.addWidget(btn_add)

        btn_load = theme.secondary_button("📂 Загрузить в форму")
        btn_load.clicked.connect(self._on_load_driver)
        button_layout.addWidget(btn_load)

        btn_edit = theme.secondary_button("✏ Редактировать")
        btn_edit.clicked.connect(self._on_edit_driver)
        button_layout.addWidget(btn_edit)

        btn_delete = theme.danger_button("🗑 Удалить")
        btn_delete.clicked.connect(self._on_delete_driver)
        button_layout.addWidget(btn_delete)

        btn_restore = theme.secondary_button(
            "♻ Восстановить",
            tooltip="Вернуть мягко удалённого водителя в справочник",
        )
        btn_restore.clicked.connect(self._on_restore_driver)
        button_layout.addWidget(btn_restore)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        if self.picker_tab:
            self._apply_picker_mode(
                (btn_add, btn_load, btn_edit, btn_delete, btn_restore)
            )

        search_input.textChanged.connect(self._schedule_driver_filter)
        btn_reset.clicked.connect(lambda: search_input.clear())

        return widget

    def _apply_picker_mode(self, buttons: tuple) -> None:
        """
        Настраивает кнопки вкладки для режима выбора записи.

        Справочник в этом режиме только читают: «Добавить», «Редактировать»,
        «Удалить» и «Восстановить» скрываются, а кнопка загрузки называется
        «Выбрать» — по ней видно, что запись уйдёт в форму, а не откроется
        здесь. Нажатие на неё по-прежнему идёт в _on_load_org /
        _on_load_driver: логика выбора одна и та же.
        """
        add_btn, load_btn, edit_btn, delete_btn, restore_btn = buttons
        add_btn.setVisible(False)
        edit_btn.setVisible(False)
        delete_btn.setVisible(False)
        restore_btn.setVisible(False)
        load_btn.setText("📂 Выбрать")
        load_btn.setToolTip("Заполнить вкладку выбранной записью")

    # ─────────────────────────────────────────────────────────
    # Загрузка данных
    # ─────────────────────────────────────────────────────────

    def _show_deleted(self) -> bool:
        """Включён ли режим показа мягко удалённых записей."""
        try:
            return bool(self.chk_deleted.isChecked())
        except AttributeError:  # до создания чекбокса (не должно случаться)
            return False

    def _on_toggle_deleted(self) -> None:
        """Переключатель «Показывать удалённые»: перечитываем все вкладки."""
        self._log_ui_action(
            "показ удалённых записей",
            enabled=self._show_deleted(),
        )
        self._load_all()

    def _log_ui_action(self, action: str, **details) -> None:
        """DEBUG-лог действий в менеджере базы (как в MainWindow)."""
        tail = " | " + " | ".join(f"{k}={v}" for k, v in details.items()) if details else ""
        logger.debug(f"UI: {action}{tail}")

    def _load_all(self) -> None:
        self._load_organizations(is_carrier=True)
        self._load_organizations(is_carrier=False)
        self._load_drivers()

    def _load_organizations(self, is_carrier: bool) -> None:
        try:
            orgs = get_all_organizations(
                is_carrier=is_carrier, include_deleted=self._show_deleted()
            )
        except Exception as e:
            logger.error(f"Ошибка загрузки организаций: {e}")
            orgs = []

        table = self._org_table(is_carrier)
        self._fill_org_table(table, orgs)
        logger.info(f"Загружено организаций: {len(orgs)} (is_carrier={is_carrier})")

    def _fill_org_table(self, table: QTableWidget, orgs: List[Dict[str, Any]]) -> None:
        # Отключаем сортировку, пока заполняем
        table.setSortingEnabled(False)
        table.setRowCount(0)

        for org in orgs:
            row = table.rowCount()
            table.insertRow(row)

            item_id = QTableWidgetItem(str(org.get("id", "")))
            item_id.setData(Qt.UserRole, org)
            table.setItem(row, 0, item_id)

            table.setItem(row, 1, QTableWidgetItem(org.get("full_name", "")))
            table.setItem(row, 2, QTableWidgetItem(org.get("inn", "")))
            table.setItem(row, 3, QTableWidgetItem(org.get("kpp", "")))
            table.setItem(row, 4, QTableWidgetItem(org.get("director_name", "")))

            status = "удалён" if org.get("is_deleted") else ""
            item_status = QTableWidgetItem(status)
            if status:
                item_status.setForeground(theme.deleted_row_color())
            table.setItem(row, 5, item_status)

        # Включаем сортировку и сортируем по «Наименование» (колонка 1)
        table.setSortingEnabled(True)
        table.sortByColumn(1, Qt.AscendingOrder)

    def _load_drivers(self) -> None:
        try:
            # with_carrier_name=True: в колонке «Перевозчик» показывается
            # основной перевозчик водителя (drivers.default_carrier_id).
            drivers = get_all_drivers(
                include_deleted=self._show_deleted(),
                with_carrier_name=True,
            )
        except Exception as e:
            logger.error(f"Ошибка загрузки водителей: {e}")
            drivers = []

        table = self.drivers_table
        self._fill_driver_table(table, drivers)
        logger.info(f"Загружено водителей: {len(drivers)}")

    def _fill_driver_table(self, table: QTableWidget, drivers: List[Dict[str, Any]]) -> None:
        # Отключаем сортировку, пока заполняем
        table.setSortingEnabled(False)
        table.setRowCount(0)

        shown = 0
        for driver in drivers:
            if not self._driver_matches_carrier_filter(driver):
                continue

            row = table.rowCount()
            table.insertRow(row)
            shown += 1

            item_id = QTableWidgetItem(str(driver.get("id", "")))
            item_id.setData(Qt.UserRole, driver)
            table.setItem(row, DRIVER_COL_ID, item_id)

            table.setItem(
                row, DRIVER_COL_NAME, QTableWidgetItem(driver.get("full_name", ""))
            )

            # ── Перевозчик ──
            # В колонке — основной перевозчик; если у водителя есть ещё
            # история работы, об этом говорит подсказка «+N».
            item_carrier = QTableWidgetItem(str(driver.get("carrier_name") or ""))
            tooltip = self._carrier_tooltip(driver)
            if tooltip:
                item_carrier.setToolTip(tooltip)
            table.setItem(row, DRIVER_COL_CARRIER, item_carrier)

            table.setItem(
                row, DRIVER_COL_BIRTH_DATE,
                QTableWidgetItem(driver.get("birth_date", "")),
            )

            passport = f"{driver.get('passport_series', '')} {driver.get('passport_number', '')}".strip()
            table.setItem(row, DRIVER_COL_PASSPORT, QTableWidgetItem(passport))
            table.setItem(
                row, DRIVER_COL_PHONE, QTableWidgetItem(driver.get("phone", ""))
            )

            status = "удалён" if driver.get("is_deleted") else ""
            item_status = QTableWidgetItem(status)
            if status:
                item_status.setForeground(theme.deleted_row_color())
            table.setItem(row, DRIVER_COL_STATUS, item_status)

        # Включаем сортировку и сортируем по «ФИО» (колонка 1)
        table.setSortingEnabled(True)
        table.sortByColumn(DRIVER_COL_NAME, Qt.AscendingOrder)

        if shown != len(drivers):
            logger.debug(
                f"Фильтр по перевозчику: показано {shown} из {len(drivers)}"
            )

    def _carrier_tooltip(self, driver: Dict[str, Any]) -> str:
        """
        Подсказка колонки «Перевозчик».

        Если у водителя есть ещё перевозчики в истории работы, к названию
        основного добавляется «+N» (сколько ещё) — иначе о них не узнать.
        """
        name = str(driver.get("carrier_name") or "").strip()
        extra = self._extra_carriers(driver)

        if not extra:
            return name
        return (
            f"{name or 'Основной не указан'}\n"
            f"Ещё перевозчиков в истории: +{len(extra)} "
            f"({', '.join(extra)})"
        )

    def _extra_carriers(self, driver: Dict[str, Any]) -> List[str]:
        """Названия перевозчиков из истории водителя, кроме основного."""
        driver_id = driver.get("id")
        if not driver_id:
            return []

        try:
            links = get_driver_carriers(driver_id)
        except Exception as e:  # noqa: BLE001 — подсказка не должна ломать список
            logger.warning(f"История работы водителя недоступна: {e}")
            return []

        main_id = driver.get("default_carrier_id")
        names: List[str] = []
        for link in links:
            if main_id and link.get("carrier_id") == main_id:
                continue
            name = str(
                link.get("carrier_name") or link.get("carrier_short_name") or ""
            ).strip()
            if name and name not in names:
                names.append(name)
        return names

    # ─────────────────────────────────────────────────────────
    # Фильтр водителей по перевозчику
    # ─────────────────────────────────────────────────────────

    def _fill_carrier_filter(self) -> int:
        """
        Заполняет фильтр «Перевозчик:» списком справочника.

        Пункты: «Все» (по умолчанию), «— без перевозчика —» и каждый
        перевозчик. itemData: ALL / NONE / id перевозчика.

        :return: сколько перевозчиков в фильтре.
        """
        try:
            carriers = get_all_organizations(is_carrier=True)
        except Exception as e:  # noqa: BLE001 — без справочника фильтр пуст
            logger.warning(f"Справочник перевозчиков недоступен: {e}")
            carriers = []

        self.carrier_filter.blockSignals(True)
        self.carrier_filter.clear()
        self.carrier_filter.addItem("Все", CARRIER_FILTER_ALL)
        self.carrier_filter.addItem("— без перевозчика —", CARRIER_FILTER_NONE)

        for org in carriers:
            name = str(org.get("full_name") or org.get("short_name") or "").strip()
            if not name:
                continue
            self.carrier_filter.addItem(name, org.get("id"))

        self.carrier_filter.blockSignals(False)
        return self.carrier_filter.count() - 2

    def _carrier_filter_value(self) -> int:
        """Выбранное значение фильтра (ALL по умолчанию)."""
        try:
            value = self.carrier_filter.currentData()
        except AttributeError:  # фильтр ещё не создан (не должно случаться)
            return CARRIER_FILTER_ALL
        return CARRIER_FILTER_ALL if value is None else int(value)

    def _driver_matches_carrier_filter(self, driver: Dict[str, Any]) -> bool:
        """Проходит ли водитель через фильтр «Перевозчик:»."""
        value = self._carrier_filter_value()
        if value == CARRIER_FILTER_ALL:
            return True

        carrier_id = driver.get("default_carrier_id")
        if value == CARRIER_FILTER_NONE:
            return carrier_id in (None, "", 0)

        try:
            return int(carrier_id) == value
        except (TypeError, ValueError):
            return False

    def _on_carrier_filter_changed(self):
        """Смена фильтра: перечитываем список водителей."""
        value = self._carrier_filter_value()
        self._log_ui_action("фильтр водителей по перевозчику", carrier_id=value)

        # Фильтр сам список не перечитывает: пункты берутся из справочника
        # при открытии диалога, а фильтрация идёт по уже загруженным строкам.
        # Поиск при этом сохраняется: он тоже отбирает строки.
        if self._driver_search_text.strip():
            self._apply_driver_filter()
        else:
            self._load_drivers()

    # ─────────────────────────────────────────────────────────
    # Фильтрация
    # ─────────────────────────────────────────────────────────

    def _schedule_org_filter(self, is_carrier: bool, text: str) -> None:
        """
        Откладывает фильтрацию организаций (Шаг 5 оптимизации).

        Запрос к базе уходит не на каждое нажатие клавиши, а через 250 мс
        после того, как пользователь перестал печатать.
        """
        self._org_search_text[is_carrier] = text or ""
        self._org_timers[is_carrier].start()

    def _apply_org_filter(self, is_carrier: bool) -> None:
        """Фильтрация организаций целиком в SQL (без выгрузки всей таблицы)."""
        text = (self._org_search_text.get(is_carrier) or "").strip()

        if not text:
            self._load_organizations(is_carrier)
            return

        try:
            orgs = search_organizations(
                text,
                is_carrier=is_carrier,
                limit=self.SEARCH_LIMIT,
                include_deleted=self._show_deleted(),
            )
        except Exception as e:
            logger.error(f"Ошибка поиска организаций: {e}")
            orgs = []

        table = self._org_table(is_carrier)
        self._fill_org_table(table, orgs)
        logger.info(f"Поиск организаций: запрос {len(text)} симв., найдено {len(orgs)}")

    def _schedule_driver_filter(self, text: str) -> None:
        """Откладывает фильтрацию водителей (debounce 250 мс)."""
        self._driver_search_text = text or ""
        self._driver_timer.start()

    def _apply_driver_filter(self) -> None:
        """
        Фильтрация водителей целиком в SQL.

        Раньше здесь выгружалась вся таблица водителей и фильтровалась
        в Python — на 20 000 записях это ~80 мс на каждое нажатие клавиши.
        Теперь поиск идёт по ФИО, паспорту и телефону средствами SQLite.
        """
        text = (self._driver_search_text or "").strip()

        if not text:
            self._load_drivers()
            return

        try:
            found = search_drivers(
                text,
                limit=self.SEARCH_LIMIT,
                include_deleted=self._show_deleted(),
                with_carrier_name=True,
            )
        except Exception as e:
            logger.error(f"Ошибка поиска водителей: {e}")
            found = []

        self._fill_driver_table(self.drivers_table, found)
        logger.info(f"Поиск водителей: запрос {len(text)} симв., найдено {len(found)}")

    # ─────────────────────────────────────────────────────────
    # Действия
    # ─────────────────────────────────────────────────────────

    def _org_table(self, is_carrier: bool) -> QTableWidget:
        """
        Таблица организаций нужной роли.

        Основной путь — именованные атрибуты вкладок (carriers_table /
        customers_table): они создаются в __init__ и всегда соответствуют
        своей роли. Признак is_carrier на таблице — запасной вариант и метка
        для тестов: у QVariant пустое значение и False неразличимы, поэтому
        сравнение идёт по exact-значению, а не по приведению к bool.
        """
        attribute = "carriers_table" if is_carrier else "customers_table"
        table = getattr(self, attribute, None)
        if table is not None:
            return table

        for candidate in self.findChildren(QTableWidget):
            if candidate.property("is_carrier") is is_carrier:
                return candidate

        logger.error(f"Таблица организаций не найдена (is_carrier={is_carrier})")
        return None

    def _picker_load(self, is_carrier: bool, record: Dict[str, Any]) -> bool:
        """
        Отдаёт выбранную запись в режиме выбора (on_pick).

        Обработчик записи один: вкладка, открывшая диалог, сама знает, куда
        её положить. Поэтому роль (is_carrier) здесь только в логе — она
        говорит, из какой таблицы пришла запись.

        :return: True — запись передана; False — диалог работает менеджером
            базы и обработчика выбора у него нет.
        """
        if not self.picker_tab:
            return False
        if self.on_pick is None:
            logger.warning("Режим выбора открыт без обработчика записи")
            return False

        logger.info(
            f"Выбор записи из справочника: таблица "
            f"{'carriers' if is_carrier else 'customers'}"
        )
        self.on_pick(record)
        return True

    def _on_load_org(self, is_carrier: bool) -> None:
        table = self._org_table(is_carrier)
        row = -1 if table is None else table.currentRow()

        if row < 0:
            QMessageBox.warning(self, "Загрузка", "Выберите запись из списка.")
            return

        item = table.item(row, 0)
        org = item.data(Qt.UserRole) if item else None

        if not org:
            QMessageBox.warning(self, "Загрузка", "Не удалось прочитать запись.")
            return

        try:
            # Режим выбора: запись уходит во вкладку, которая открыла диалог
            # (в аренде — «Арендатор» или «Арендодатель»).
            if self._picker_load(is_carrier, org):
                self.accept()
                return

            if is_carrier and self.on_load_carrier:
                self.on_load_carrier(org)
            elif not is_carrier and self.on_load_customer:
                self.on_load_customer(org)
            self.accept()
        except Exception as e:
            logger.error(f"Ошибка загрузки организации: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить запись:\n\n{e}")

    def _on_load_driver(self) -> None:
        table = self.drivers_table
        row = table.currentRow()

        if row < 0:
            QMessageBox.warning(self, "Загрузка", "Выберите запись из списка.")
            return

        item = table.item(row, 0)
        driver = item.data(Qt.UserRole) if item else None

        if not driver:
            QMessageBox.warning(self, "Загрузка", "Не удалось прочитать запись.")
            return

        try:
            # Подгружаем тягач/прицеп и подмешиваем в данные водителя
            if driver.get("id"):
                try:
                    vehicle = load_driver_vehicle(driver["id"])
                    if vehicle:
                        driver["tractor"] = {
                            "brand_model": vehicle.get("tractor_brand", ""),
                            "plate_number": vehicle.get("tractor_plate", ""),
                            "color": vehicle.get("tractor_color", ""),
                            "year": vehicle.get("tractor_year", ""),
                        }
                        driver["trailer"] = {
                            "brand_model": vehicle.get("trailer_brand", ""),
                            "plate_number": vehicle.get("trailer_plate", ""),
                            "color": vehicle.get("trailer_color", ""),
                            "year": vehicle.get("trailer_year", ""),
                        }
                except Exception as e:
                    logger.warning(f"Не удалось подгрузить ТС водителя: {e}")

            if self._picker_load(False, driver):
                self.accept()
                return

            if self.on_load_driver:
                self.on_load_driver(driver)
            self.accept()
        except Exception as e:
            logger.error(f"Ошибка загрузки водителя: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить запись:\n\n{e}")

    def _on_edit_org(self, is_carrier: bool) -> None:
        table = self._org_table(is_carrier)
        row = table.currentRow()

        if row < 0:
            QMessageBox.warning(self, "Редактирование", "Выберите запись.")
            return

        item = table.item(row, 0)
        org = item.data(Qt.UserRole) if item else None

        if not org:
            QMessageBox.warning(self, "Редактирование", "Не удалось прочитать запись.")
            return

        org_id = org.get("id")
        dialog = EditCarrierDialog(org, is_carrier=is_carrier, parent=self)

        if dialog.exec_():
            new_data = dialog.get_data()
            ok = update_organization(org_id, new_data, is_carrier=is_carrier)
            if ok:
                self._load_organizations(is_carrier=is_carrier)
                QMessageBox.information(self, "Готово", "Данные обновлены.")
            else:
                QMessageBox.critical(self, "Ошибка", "Не удалось сохранить изменения.")

    def _on_edit_driver(self) -> None:
        table = self.drivers_table
        row = table.currentRow()

        if row < 0:
            QMessageBox.warning(self, "Редактирование", "Выберите запись.")
            return

        item = table.item(row, 0)
        driver = item.data(Qt.UserRole) if item else None

        if not driver:
            QMessageBox.warning(self, "Редактирование", "Не удалось прочитать запись.")
            return

        driver_id = driver.get("id")
        dialog = EditDriverDialog(driver, parent=self)

        if dialog.exec_():
            driver_data = dialog.get_driver_data()
            ok1 = update_driver(driver_id, driver_data)

            # Привязка к перевозчику: значение записано update_driver,
            # а запись истории (driver_carriers) заводит set_default_carrier —
            # если активной связи с этим перевозчиком ещё нет.
            if driver_data.get("default_carrier_id"):
                set_default_carrier(driver_id, driver_data.get("default_carrier_id"))

            vehicle_data = dialog.get_vehicle_data()
            ok2 = save_driver_vehicle(driver_id, vehicle_data)

            if ok1 and ok2:
                self._load_drivers()
                QMessageBox.information(self, "Готово", "Данные водителя и ТС обновлены.")
            else:
                QMessageBox.critical(self, "Ошибка", "Не удалось сохранить изменения.")

    def _on_add_org(self, is_carrier: bool) -> None:
        """
        Создание новой организации вручную (перевозчик или заказчик).

        Открывает тот же диалог, что и редактирование, но с пустыми полями
        (в него не передаётся id), а по «Сохранить» вызывается
        save_organization(), то есть создаётся новая запись.
        """
        entity = "carrier" if is_carrier else "customer"
        logger.info(f"Открытие диалога добавления организации (is_carrier={is_carrier})")
        self._log_ui_action("нажата кнопка «➕ Добавить»", entity=entity)

        try:
            dialog = EditCarrierDialog({}, is_carrier=is_carrier, parent=self)
            if dialog.exec_() != QDialog.Accepted:
                logger.info(f"Создание организации отменено пользователем ({entity})")
                return

            data = dialog.get_data()
            org_id = save_organization(data, is_carrier=is_carrier)

            logger.info(f"Организация создана: ID={org_id}, is_carrier={is_carrier}")
            audit.log_event("organization_created", org_id=org_id, entity=entity)

            self._load_organizations(is_carrier=is_carrier)
            QMessageBox.information(self, "Готово", "Запись добавлена.")
        except Exception as e:
            logger.exception("Ошибка создания организации")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось добавить запись:\n{e}"
            )

    def _on_add_driver(self) -> None:
        """
        Создание нового водителя вручную (вместе с тягачом/прицепом).

        В диалог не передаётся id, поэтому _load_driver_vehicle() ничего не
        подгружает, все поля пустые. После save_driver() при заполненных
        полях ТС сохраняется и строка driver_vehicles.
        """
        logger.info("Открытие диалога добавления водителя")
        self._log_ui_action("нажата кнопка «➕ Добавить»", entity="driver")

        try:
            dialog = EditDriverDialog({}, parent=self)
            if dialog.exec_() != QDialog.Accepted:
                logger.info("Создание водителя отменено пользователем")
                return

            driver_data = dialog.get_driver_data()
            driver_id = save_driver(driver_data)

            # Основной перевозчик выбран — заводим и запись истории
            # (driver_carriers); само значение уже записано save_driver.
            if driver_data.get("default_carrier_id"):
                set_default_carrier(driver_id, driver_data.get("default_carrier_id"))

            vehicle_data = dialog.get_vehicle_data()
            if any(str(value or "").strip() for value in vehicle_data.values()):
                save_driver_vehicle(driver_id, vehicle_data)
                logger.info(f"Тягач/прицеп созданы для водителя ID={driver_id}")
            else:
                logger.debug("Поля тягача/прицепа пусты — запись ТС не создаётся")

            logger.info(f"Водитель создан: ID={driver_id}")
            audit.log_event("driver_created", driver_id=driver_id)

            self._load_drivers()
            QMessageBox.information(self, "Готово", "Водитель добавлен.")
        except Exception as e:
            logger.exception("Ошибка создания водителя")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось добавить водителя:\n{e}"
            )

    def _on_delete_org(self, is_carrier: bool) -> None:
        table = self._org_table(is_carrier)
        row = table.currentRow()

        if row < 0:
            QMessageBox.warning(self, "Удаление", "Выберите запись из списка.")
            return

        item = table.item(row, 0)
        org = item.data(Qt.UserRole) if item else None

        if not org:
            QMessageBox.warning(self, "Удаление", "Не удалось прочитать запись.")
            return

        org_id = org.get("id")
        org_name = org.get("full_name", "без названия")

        reply = QMessageBox.question(
            self,
            "Удаление",
            f"Убрать запись из справочника?\n\n{org_name}\n\n"
            f"Запись не стирается: договоры и связанные ТС сохранятся, "
            f"а саму запись можно вернуть кнопкой «♻ Восстановить».",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            success = delete_organization(org_id, is_carrier=is_carrier)
            if success:
                self._load_organizations(is_carrier=is_carrier)
                QMessageBox.information(
                    self, "Успех",
                    "Запись убрана из справочника.\n"
                    "Вернуть её можно кнопкой «♻ Восстановить»."
                )
                logger.info(f"Организация удалена (мягко): ID={org_id}")
            else:
                QMessageBox.critical(self, "Ошибка", "Не удалось удалить запись.")
        except Exception as e:
            logger.error(f"Ошибка удаления: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить:\n\n{e}")

    def _on_restore_org(self, is_carrier: bool) -> None:
        """Возвращает мягко удалённую организацию в справочник."""
        table = self._org_table(is_carrier)
        row = table.currentRow()

        if row < 0:
            QMessageBox.warning(self, "Восстановление", "Выберите запись из списка.")
            return

        item = table.item(row, 0)
        org = item.data(Qt.UserRole) if item else None

        if not org:
            QMessageBox.warning(
                self, "Восстановление", "Не удалось прочитать запись."
            )
            return

        if not org.get("is_deleted"):
            QMessageBox.information(
                self, "Восстановление", "Эта запись и так доступна в справочнике."
            )
            return

        org_id = org.get("id")
        try:
            if restore_organization(org_id, is_carrier=is_carrier):
                self._load_organizations(is_carrier=is_carrier)
                QMessageBox.information(
                    self, "Готово", "Запись возвращена в справочник."
                )
                logger.info(f"Организация восстановлена: ID={org_id}")
            else:
                QMessageBox.critical(self, "Ошибка", "Не удалось восстановить запись.")
        except Exception as e:
            logger.error(f"Ошибка восстановления: {e}")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось восстановить:\n\n{e}"
            )

    def _on_delete_driver(self) -> None:
        table = self.drivers_table
        row = table.currentRow()

        if row < 0:
            QMessageBox.warning(self, "Удаление", "Выберите запись из списка.")
            return

        item = table.item(row, 0)
        driver = item.data(Qt.UserRole) if item else None

        if not driver:
            QMessageBox.warning(self, "Удаление", "Не удалось прочитать запись.")
            return

        driver_id = driver.get("id")
        driver_name = driver.get("full_name", "без имени")

        reply = QMessageBox.question(
            self,
            "Удаление",
            f"Убрать водителя из справочника?\n\n{driver_name}\n\n"
            f"Запись не стирается: договоры и данные тягача/прицепа сохранятся, "
            f"а водителя можно вернуть кнопкой «♻ Восстановить».",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            success = delete_driver(driver_id)
            if success:
                self._load_drivers()
                QMessageBox.information(
                    self, "Успех",
                    "Водитель убран из справочника.\n"
                    "Вернуть его можно кнопкой «♻ Восстановить»."
                )
                logger.info(f"Водитель удалён (мягко): ID={driver_id}")
            else:
                QMessageBox.critical(self, "Ошибка", "Не удалось удалить водителя.")
        except Exception as e:
            logger.error(f"Ошибка удаления водителя: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить:\n\n{e}")

    def _on_restore_driver(self) -> None:
        """Возвращает мягко удалённого водителя в справочник."""
        table = self.drivers_table
        row = table.currentRow()

        if row < 0:
            QMessageBox.warning(self, "Восстановление", "Выберите запись из списка.")
            return

        item = table.item(row, 0)
        driver = item.data(Qt.UserRole) if item else None

        if not driver:
            QMessageBox.warning(
                self, "Восстановление", "Не удалось прочитать запись."
            )
            return

        if not driver.get("is_deleted"):
            QMessageBox.information(
                self, "Восстановление", "Этот водитель и так доступен в справочнике."
            )
            return

        driver_id = driver.get("id")
        try:
            if restore_driver(driver_id):
                self._load_drivers()
                QMessageBox.information(
                    self, "Готово", "Водитель возвращён в справочник."
                )
                logger.info(f"Водитель восстановлен: ID={driver_id}")
            else:
                QMessageBox.critical(
                    self, "Ошибка", "Не удалось восстановить водителя."
                )
        except Exception as e:
            logger.error(f"Ошибка восстановления водителя: {e}")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось восстановить:\n\n{e}"
            )