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

Вкладка «Перевозчики» — дерево (ШАГ «Дерево перевозчиков»)
----------------------------------------------------------
Верхний уровень — перевозчики, дети — водители, привязанные к перевозчику
через `drivers.default_carrier_id`. Стрелка «+»/«−» раскрывает список;
двойной клик по перевозчику подтягивает ТОЛЬКО перевозчика (водителя
оператор впишет сам, если тот временный), двойной клик по водителю —
и водителя, и его перевозчика. Поиск фильтрует оба уровня: совпал
перевозчик — видны все его водители, совпал водитель — виден его
перевозчик и сам водитель.

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
from typing import Dict, Any, Optional, Callable, List, Set, Tuple

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QMessageBox, QLabel, QAbstractItemView,
    QLineEdit, QFormLayout, QScrollArea, QGroupBox, QTextEdit,
    QCheckBox, QComboBox, QDateEdit, QTreeWidget, QTreeWidgetItem,
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
from ui.carrier_drivers_dialog import CarrierDriversDialog
from ui.driver_carrier_dialog import DriverCarrierDialog
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

# ── Дерево перевозчиков (ШАГ «Дерево перевозчиков») ──
# Вкладка «Перевозчики» — не таблица, а дерево: верхний уровень —
# перевозчики, дети — их водители (drivers.default_carrier_id).
# Колонок пять (без «ID» таблицы организаций): запись лежит в данных узла,
# а не в отдельной колонке. У водителя своих ИНН и КПП нет, поэтому те же
# колонки означают другое: «Директор» — дата рождения, «Статус» — телефон.
CARRIER_TREE_HEADERS = ("Наименование", "ИНН", "КПП", "Директор", "Статус")

CARRIER_TREE_COLUMNS_CONFIG = (
    (0, MODE_FIXED, 340),   # Наименование (у водителя — «👤 ФИО»)
    (1, MODE_FIXED, 130),   # ИНН
    (2, MODE_FIXED, 120),   # КПП
    (3, MODE_FIXED, 220),   # Директор / дата рождения водителя
    (4, MODE_FIXED, 150),   # Статус / телефон водителя
)

CARRIER_TREE_COLUMN_MINIMUMS = {0: 200, 1: 100, 2: 90, 3: 140, 4: 120}

#: Номера колонок дерева перевозчиков (читаются по имени, а не по числу).
CARRIER_COL_NAME = 0
CARRIER_COL_INN = 1
CARRIER_COL_KPP = 2
CARRIER_COL_DIRECTOR = 3
CARRIER_COL_STATUS = 4

#: Колонка, в данных которой лежит запись узла (одна на все колонки).
NODE_COLUMN = 0

#: Роли данных узла: сама запись справочника и вид узла.
NODE_RECORD_ROLE = Qt.UserRole
NODE_KIND_ROLE = Qt.UserRole + 1

NODE_KIND_CARRIER = "carrier"
NODE_KIND_DRIVER = "driver"

#: Приставки узлов: по ним видно уровень дерева.
CARRIER_NODE_PREFIX = "🚛 "
DRIVER_NODE_PREFIX = "👤 "

#: Что показывать вместо пустого ФИО водителя.
DRIVER_WITHOUT_NAME = "(без ФИО)"

#: Подпись мягко удалённой записи (колонка «Статус», как и раньше).
DELETED_STATUS_TITLE = "удалён"

#: Ключи QSettings для раскладки таблиц справочника.
ORGANIZATIONS_WIDTHS_KEY = "ui/db_manager/organizations"
DRIVERS_WIDTHS_KEY = "ui/db_manager/drivers"
#: Раскладка дерева перевозчиков.
CARRIER_TREE_WIDTHS_KEY = "ui/db_manager/carriers_tree"
#: Раскладка таблицы истории работы водителя у перевозчиков.
DRIVER_CARRIERS_WIDTHS_KEY = "ui/db_manager/driver_carriers"

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
        self.org_id = org.get("id")

        #: Менялись ли привязки водителей (диалог «👤 Водители…»): по этому
        #: признаку менеджер базы обновляет таблицы, даже если карточку
        #: перевозчика оператор в итоге не сохранил.
        self.drivers_changed = False

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

        # ── Водители перевозчика (ДОПОЛНЕНИЕ к шагу «Дерево перевозчиков») ──
        # Кнопка есть только у СОХРАНЁННОГО перевозчика: у новой записи
        # водителей ещё нет — привязывать не к кому.
        self.btn_drivers = theme.secondary_button(
            "👤 Водители…",
            tooltip="Привязать водителей к перевозчику и отвязать лишних",
        )
        self.btn_drivers.clicked.connect(self._on_drivers)
        self.btn_drivers.setVisible(bool(is_carrier) and not self.is_new)
        btn_layout.addWidget(self.btn_drivers)

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

    def _on_drivers(self) -> None:
        """
        «👤 Водители…»: привязка водителей к этому перевозчику.

        Привязки пишутся в базу сразу (диалог их не откладывает), поэтому
        после закрытия поднимаем флаг `drivers_changed` — менеджер базы по
        нему перечитает таблицу водителей и дерево.
        """
        if not self.org_id:
            QMessageBox.information(
                self, "Водители", "Сначала сохраните перевозчика."
            )
            return

        dialog = CarrierDriversDialog(
            self.org_id,
            parent=self,
            carrier_name=self.full_name.text().strip(),
        )
        dialog.exec_()
        if dialog.changed:
            self.drivers_changed = True
            logger.info(
                f"Привязки водителей перевозчика изменены: ID={self.org_id}"
            )

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
                "💡 Двойной клик по записи — выбрать её и заполнить вкладку\n"
                "💡 Стрелка «+» у перевозчика — показать его водителей\n"
                "💡 Клик по заголовку столбца — сортировка\n"
                "💡 Справочник общий: то, что сохранено здесь, видно и в других "
                "окнах программы",
            ))
        else:
            layout.addWidget(theme.page_title(
                "Управление сохранёнными записями",
                "💡 Двойной клик по записи — загрузить её в форму\n"
                "💡 Перевозчик: стрелка «+» показывает его водителей, двойной "
                "клик по водителю подтягивает и водителя, и перевозчика\n"
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

        self.carriers_tab = self._create_carrier_tab()
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

        # ── Поиск по дереву перевозчиков (ШАГ «Дерево перевозчиков») ──
        # Фильтруются оба уровня сразу, поэтому запрос идёт к базе через
        # паузу, как и у таблиц: на каждое нажатие клавиши — лишняя работа.
        self._carrier_search_text = ""
        self._carrier_timer = QTimer(self)
        self._carrier_timer.setSingleShot(True)
        self._carrier_timer.setInterval(250)
        self._carrier_timer.timeout.connect(self._apply_carrier_tree_filter)

        #: Раскрытые перевозчики: состояние переживает перестройку дерева
        #: (после правки, удаления или поиска).
        self._expanded_carrier_ids: Set[int] = set()

        self._load_all()

        logger.debug(
            f"DbManagerDialog инициализирован (режим выбора: "
            f"{self.picker_tab or 'нет'})"
        )

    # ─────────────────────────────────────────────────────────
    # Вкладки
    # ─────────────────────────────────────────────────────────

    def _create_org_tab(self, is_carrier: bool) -> QWidget:
        """
        Вкладка справочника организаций ТАБЛИЦЕЙ (заказчики).

        Перевозчики живут в дереве (`_create_carrier_tab`): у них есть
        подчинённый уровень — водители. Общие части вкладок (строка поиска,
        панель кнопок) вынесены в помощники, чтобы обе вкладки выглядели
        одинаково и подписи кнопок не разъезжались.
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)

        search_input = self._build_org_search_row(
            layout, "Наименование, ИНН или ФИО директора..."
        )

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
        table.doubleClicked.connect(self._on_load_customer)

        #: Роль таблицы: по этому признаку выбирается таблица-источник и
        #: обработчик записи (в аренде — арендатор / арендодатель).
        table.setProperty("is_carrier", is_carrier)

        self.customers_table = table

        layout.addWidget(table)

        buttons = self._build_org_button_bar(layout, is_carrier)

        # В режиме выбора справочник только читают: кнопки, которые его
        # меняют, прячем — оставляем «Загрузить в форму».
        if self.picker_tab:
            self._apply_picker_mode(buttons)

        search_input.textChanged.connect(self._on_customer_search_changed)

        return widget

    def _create_carrier_tab(self) -> QWidget:
        """
        Вкладка «Перевозчики» — дерево «перевозчик → его водители».

        Верхний уровень — перевозчики, дети — водители, привязанные через
        `drivers.default_carrier_id`. Дети заполняются сразу, без ленивой
        подгрузки: водителей у одного перевозчика единицы, а поиск и
        сортировка должны видеть оба уровня.
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)

        search_input = self._build_org_search_row(
            layout, "Перевозчик, ИНН, ФИО директора или водителя..."
        )

        tree = QTreeWidget()
        tree.setColumnCount(len(CARRIER_TREE_HEADERS))
        tree.setHeaderLabels(list(CARRIER_TREE_HEADERS))

        # ── Раскрытие, выделение, сортировка ──
        # Стрелки «+»/«−» раскрывают узел; двойной клик занят загрузкой
        # записи в форму, поэтому раскрывать им ничего не нужно.
        tree.setRootIsDecorated(True)
        tree.setAlternatingRowColors(True)
        tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tree.setSortingEnabled(True)
        tree.header().setSectionsMovable(True)
        tree.header().setStretchLastSection(False)

        # Ширины, минимумы, подсказки и раскладка — общий помощник таблиц:
        # шапка дерева настраивается так же (table_header умеет QTreeWidget).
        setup_point_table(
            tree,
            CARRIER_TREE_COLUMNS_CONFIG,
            storage_key=CARRIER_TREE_WIDTHS_KEY,
            minimums=CARRIER_TREE_COLUMN_MINIMUMS,
        )
        install_tooltip_on_table(tree)

        # ── Двойной клик = загрузить запись в форму ──
        # Перевозчик — только перевозчик, водитель — водитель и его
        # перевозчик (разбирает _on_carrier_item_double_clicked). Щелчок по
        # стрелке «+»/«−» сюда не приходит: Qt отдаёт украшение узла себе
        # (QTreeView::mouseDoubleClickEvent не шлёт doubleClicked для него).
        tree.itemDoubleClicked.connect(self._on_carrier_item_double_clicked)

        # Состояние раскрытия переживает перестройку дерева (правка,
        # удаление, поиск): запоминаем раскрытые перевозчики по ID.
        tree.itemExpanded.connect(self._on_carrier_node_expanded)
        tree.itemCollapsed.connect(self._on_carrier_node_collapsed)

        #: Признак роли вкладки — как у таблиц организаций.
        tree.setProperty("is_carrier", True)

        self.carriers_tree = tree
        layout.addWidget(tree)

        buttons = self._build_org_button_bar(layout, is_carrier=True)

        if self.picker_tab:
            self._apply_picker_mode(buttons)

        search_input.textChanged.connect(self._on_carrier_search_changed)

        return widget

    def _build_org_search_row(
        self, layout: QVBoxLayout, placeholder: str
    ) -> QLineEdit:
        """
        Строка поиска вкладки справочника: поле и кнопка «Сбросить».

        Возвращает поле: вкладка сама решает, что делать с текстом
        (у заказчиков фильтр по таблице, у перевозчиков — по дереву).
        Кнопка «Сбросить» подключена к `clear()` самого поля — очистка
        вызовет textChanged, и список вернётся к полному (без lambda).
        """
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("🔍 Поиск:"))
        search_input = QLineEdit()
        search_input.setPlaceholderText(placeholder)
        search_input.setClearButtonEnabled(True)
        search_layout.addWidget(search_input, 1)
        btn_reset = theme.secondary_button("Сбросить")
        btn_reset.clicked.connect(search_input.clear)
        search_layout.addWidget(btn_reset)
        layout.addLayout(search_layout)
        return search_input

    def _build_org_button_bar(
        self, layout: QVBoxLayout, is_carrier: bool
    ) -> Tuple[QPushButton, ...]:
        """
        Панель кнопок вкладки справочника — общая для дерева и таблицы.

        Подписи, роли темы и порядок кнопок заданы здесь один раз (на это
        опираются тесты менеджера базы), а обработчики берутся у своей роли.
        Слоты — связанные методы БЕЗ параметров: `lambda` в `connect`
        запрещены (AGENTS.md § 5.1, цикл ссылок Python ↔ Qt).

        :return: кнопки в порядке добавления — их получает `_apply_picker_mode`.
        """
        if is_carrier:
            on_add = self._on_add_carrier
            on_load = self._on_load_carrier
            on_edit = self._on_edit_carrier
            on_delete = self._on_delete_carrier
            on_restore = self._on_restore_carrier
            add_tooltip = "Создать нового перевозчика вручную"
        else:
            on_add = self._on_add_customer
            on_load = self._on_load_customer
            on_edit = self._on_edit_customer
            on_delete = self._on_delete_customer
            on_restore = self._on_restore_customer
            add_tooltip = "Создать нового заказчика вручную"

        button_layout = QHBoxLayout()

        btn_add = theme.primary_button("➕ Добавить", tooltip=add_tooltip)
        btn_add.clicked.connect(on_add)
        button_layout.addWidget(btn_add)

        btn_load = theme.secondary_button("📂 Загрузить в форму")
        btn_load.clicked.connect(on_load)
        button_layout.addWidget(btn_load)

        btn_edit = theme.secondary_button("✏ Редактировать")
        btn_edit.clicked.connect(on_edit)
        button_layout.addWidget(btn_edit)

        btn_delete = theme.danger_button("🗑 Удалить")
        btn_delete.clicked.connect(on_delete)
        button_layout.addWidget(btn_delete)

        btn_restore = theme.secondary_button(
            "♻ Восстановить",
            tooltip="Вернуть мягко удалённую запись в справочник",
        )
        btn_restore.clicked.connect(on_restore)
        button_layout.addWidget(btn_restore)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        return (btn_add, btn_load, btn_edit, btn_delete, btn_restore)

    # ── Слоты кнопок вкладок справочника ──
    # Отдельные методы без параметров, а не lambda с ролью: сигнал clicked
    # передаёт признак нажатия, а замыкание завело бы цикл ссылок
    # Python ↔ Qt (AGENTS.md § 5.1).

    def _on_add_carrier(self) -> None:
        """«➕ Добавить» на вкладке «Перевозчики»."""
        self._on_add_org(True)

    def _on_load_carrier(self) -> None:
        """«📂 Загрузить в форму» на вкладке «Перевозчики»."""
        self._on_load_org(True)

    def _on_edit_carrier(self) -> None:
        """«✏ Редактировать» на вкладке «Перевозчики»."""
        self._on_edit_org(True)

    def _on_delete_carrier(self) -> None:
        """«🗑 Удалить» на вкладке «Перевозчики»."""
        self._on_delete_org(True)

    def _on_restore_carrier(self) -> None:
        """«♻ Восстановить» на вкладке «Перевозчики»."""
        self._on_restore_org(True)

    def _on_add_customer(self) -> None:
        """«➕ Добавить» на вкладке «Заказчики»."""
        self._on_add_org(False)

    def _on_load_customer(self) -> None:
        """«📂 Загрузить в форму» / двойной клик на вкладке «Заказчики»."""
        self._on_load_org(False)

    def _on_edit_customer(self) -> None:
        """«✏ Редактировать» на вкладке «Заказчики»."""
        self._on_edit_org(False)

    def _on_delete_customer(self) -> None:
        """«🗑 Удалить» на вкладке «Заказчики»."""
        self._on_delete_org(False)

    def _on_restore_customer(self) -> None:
        """«♻ Восстановить» на вкладке «Заказчики»."""
        self._on_restore_org(False)

    def _on_customer_search_changed(self, text: str) -> None:
        """Ввод в поиске заказчиков: отложенная фильтрация таблицы."""
        self._schedule_org_filter(False, text)

    def _on_carrier_search_changed(self, text: str) -> None:
        """Ввод в поиске перевозчиков: отложенная фильтрация дерева."""
        self._schedule_carrier_tree_filter(text)


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

        # ── Карточка перевозчика водителя (ШАГ «Дерево перевозчиков») ──
        # На вкладке «Перевозчики» этой кнопки нет: там дерево, и до
        # перевозчика водителя видно по родителю. Здесь же водитель один,
        # и к его перевозчику нужен быстрый путь.
        self.btn_carrier_card = theme.secondary_button(
            "🚛 Перевозчик…",
            tooltip=(
                "Открыть карточку перевозчика выбранного водителя.\n"
                "Если водитель ни за кем не закреплён — подскажем, что делать."
            ),
        )
        self.btn_carrier_card.clicked.connect(self._on_open_driver_carrier)
        self.btn_carrier_card.setEnabled(False)
        button_layout.addWidget(self.btn_carrier_card)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        # Кнопка карточки активна только при выбранном водителе: открывать
        # нечего, пока строка не выбрана.
        table.itemSelectionChanged.connect(self._on_driver_selection_changed)

        if self.picker_tab:
            self._apply_picker_mode(
                (btn_add, btn_load, btn_edit, btn_delete, btn_restore)
            )
            # В режиме выбора справочник только читают: карточка перевозчика
            # здесь такая же правка, как «✏ Редактировать».
            self.btn_carrier_card.setVisible(False)

        search_input.textChanged.connect(self._schedule_driver_filter)
        btn_reset.clicked.connect(search_input.clear)

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
        """
        Перечитывает вкладку справочника организаций.

        У перевозчиков вкладка — дерево (ШАГ «Дерево перевозчиков»),
        у заказчиков — таблица: роль выбирает представление.
        """
        if is_carrier:
            self._load_carriers_tree()
            return

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

    # ─────────────────────────────────────────────────────────
    # Дерево перевозчиков (ШАГ «Дерево перевозчиков»)
    # ─────────────────────────────────────────────────────────

    def _load_carriers_tree(self) -> None:
        """
        Перечитывает дерево перевозчиков.

        Читает оба уровня разом: перевозчиков (верхний уровень) и водителей
        (дети — те, у кого заполнен `default_carrier_id`). Мягко удалённые
        показываются по переключателю «Показывать удалённые»: по умолчанию
        их нет ни на одном уровне.
        """
        show_deleted = self._show_deleted()

        try:
            carriers = get_all_organizations(
                is_carrier=True, include_deleted=show_deleted
            )
        except Exception as e:
            logger.error(f"Ошибка загрузки перевозчиков: {e}")
            carriers = []

        try:
            # with_carrier_name не нужен: родитель в дереве и есть перевозчик.
            drivers = get_all_drivers(include_deleted=show_deleted)
        except Exception as e:
            logger.error(f"Ошибка загрузки водителей для дерева: {e}")
            drivers = []

        self._fill_carriers_tree(carriers, drivers, self._carrier_search_text)
        logger.info(
            f"Дерево перевозчиков: перевозчиков {len(carriers)}, "
            f"водителей {len(drivers)}"
        )

    def _fill_carriers_tree(
        self,
        carriers: List[Dict[str, Any]],
        drivers: List[Dict[str, Any]],
        search_text: str = "",
    ) -> None:
        """
        Собирает дерево: перевозчики и их водители.

        :param search_text: поиск по ОБОИМ уровням. Перевозчик подошёл —
        виден со всеми своими водителями; подошёл водитель — виден его
        перевозчик и только совпавшие водители; ни того, ни другого —
        перевозчика в дереве нет. Пустой поиск — все.

        Раскрытие: совпадение внутри перевозчика раскрывается сразу, иначе
        узел возвращается в то состояние, в котором оператор его оставил
        (`_expanded_carrier_ids`).
        """
        tree = self.carriers_tree
        needle = (search_text or "").strip().lower()

        drivers_by_carrier: Dict[int, List[Dict[str, Any]]] = {}
        for driver in drivers:
            carrier_id = self._as_id(driver.get("default_carrier_id"))
            if carrier_id is None:
                continue
            drivers_by_carrier.setdefault(carrier_id, []).append(driver)

        # Отключаем сортировку, пока заполняем (как у таблиц справочника).
        tree.setSortingEnabled(False)
        tree.clear()

        shown = 0
        for org in carriers:
            carrier_id = self._as_id(org.get("id"))
            children = drivers_by_carrier.get(carrier_id, [])

            if not needle or self._carrier_matches_search(org, needle):
                matching_children = children
            else:
                matching_children = [
                    driver for driver in children
                    if self._driver_matches_search(driver, needle)
                ]
                if not matching_children:
                    continue

            parent = self._make_carrier_item(org)
            tree.addTopLevelItem(parent)
            shown += 1

            for driver in matching_children:
                parent.addChild(self._make_driver_item(driver))

            if needle and matching_children:
                parent.setExpanded(True)
            elif carrier_id in self._expanded_carrier_ids:
                parent.setExpanded(True)

        # Включаем сортировку и сортируем по «Наименование» (колонка 0).
        # Явный sortItems нужен: одного setSortingEnabled(True) после
        # заполнения Qt не хватает — модель пересортировывает не всегда
        # (проверено пробой на временной базе). Порядок — как у таблиц
        # справочника: по наименованию.
        tree.setSortingEnabled(True)
        tree.sortItems(CARRIER_COL_NAME, Qt.AscendingOrder)

        if needle:
            logger.info(
                f"Поиск по дереву перевозчиков: запрос {len(needle)} симв., "
                f"перевозчиков {shown}"
            )

    def _make_carrier_item(self, org: Dict[str, Any]) -> QTreeWidgetItem:
        """Узел перевозчика: наименование, ИНН, КПП, директор, статус."""
        name = str(org.get("full_name") or org.get("short_name") or "").strip()
        item = QTreeWidgetItem([
            f"{CARRIER_NODE_PREFIX}{name}",
            str(org.get("inn") or ""),
            str(org.get("kpp") or ""),
            str(org.get("director_name") or ""),
            DELETED_STATUS_TITLE if org.get("is_deleted") else "",
        ])
        self._mark_node(item, org, NODE_KIND_CARRIER)
        return item

    def _make_driver_item(self, driver: Dict[str, Any]) -> QTreeWidgetItem:
        """
        Узел водителя внутри перевозчика.

        Колонки те же, но означают другое (вариант A разбора ТЗ): ИНН и КПП
        у водителя нет, «Директор» — дата рождения, «Статус» — телефон.
        У мягко удалённого водителя в «Статусе» стоит «удалён»: это важнее
        телефона, а сам телефон виден на вкладке «Водители».
        """
        full_name = str(driver.get("full_name") or "").strip()
        status = (
            DELETED_STATUS_TITLE if driver.get("is_deleted")
            else str(driver.get("phone") or "")
        )
        item = QTreeWidgetItem([
            f"{DRIVER_NODE_PREFIX}{full_name or DRIVER_WITHOUT_NAME}",
            "",
            "",
            str(driver.get("birth_date") or ""),
            status,
        ])
        self._mark_node(item, driver, NODE_KIND_DRIVER)
        return item

    def _mark_node(
        self, item: QTreeWidgetItem, record: Dict[str, Any], kind: str
    ) -> None:
        """
        Кладёт запись и вид узла в данные строки.

        Запись — в `NODE_RECORD_ROLE` (её читают действия: загрузка, правка,
        удаление), вид — в `NODE_KIND_ROLE`. Мягко удалённый узел целиком
        подкрашивается, как строка таблицы.
        """
        item.setData(NODE_COLUMN, NODE_RECORD_ROLE, record)
        item.setData(NODE_COLUMN, NODE_KIND_ROLE, kind)

        if record.get("is_deleted"):
            brush = theme.deleted_row_color()
            for column in range(len(CARRIER_TREE_HEADERS)):
                item.setForeground(column, brush)

    @staticmethod
    def _node_record(item: Optional[QTreeWidgetItem]) -> Dict[str, Any]:
        """Запись справочника, лежащая в узле дерева (пусто — не узел)."""
        if item is None:
            return {}
        record = item.data(NODE_COLUMN, NODE_RECORD_ROLE)
        return record if isinstance(record, dict) else {}

    @staticmethod
    def _node_kind(item: Optional[QTreeWidgetItem]) -> str:
        """Вид узла: NODE_KIND_CARRIER / NODE_KIND_DRIVER (пусто — не узел)."""
        if item is None:
            return ""
        return str(item.data(NODE_COLUMN, NODE_KIND_ROLE) or "")

    @staticmethod
    def _as_id(value: Any) -> Optional[int]:
        """ID записи числом; пустое и мусор — None."""
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _selected_carrier_node(self) -> Optional[QTreeWidgetItem]:
        """
        Выбранный узел дерева перевозчиков (None — ничего не выбрано).

        Сначала текущий узел, затем выделение: после перестройки дерева
        «текущего» может не быть, а выделенная строка остаётся.
        """
        tree = getattr(self, "carriers_tree", None)
        if tree is None:
            return None

        item = tree.currentItem()
        if item is not None:
            return item

        selected = tree.selectedItems()
        return selected[0] if selected else None

    def _carrier_tab_selection(self) -> Tuple[str, Dict[str, Any]]:
        """Что выбрано на вкладке «Перевозчики»: (вид узла, запись)."""
        item = self._selected_carrier_node()
        return self._node_kind(item), self._node_record(item)

    @staticmethod
    def _text_matches(values: Tuple[Any, ...], needle: str) -> bool:
        """Есть ли подстрока поиска хотя бы в одном из значений."""
        return any(needle in str(value or "").lower() for value in values)

    def _carrier_matches_search(self, org: Dict[str, Any], needle: str) -> bool:
        """Поля перевозчика, по которым ищет дерево (те же, что у таблицы)."""
        return self._text_matches(
            (
                org.get("full_name"),
                org.get("short_name"),
                org.get("inn"),
                org.get("kpp"),
                org.get("director_name"),
            ),
            needle,
        )

    def _driver_matches_search(self, driver: Dict[str, Any], needle: str) -> bool:
        """Поля водителя, по которым ищет дерево (как на вкладке «Водители»)."""
        passport = (
            f"{driver.get('passport_series') or ''} "
            f"{driver.get('passport_number') or ''}"
        )
        return self._text_matches(
            (driver.get("full_name"), passport, driver.get("phone")),
            needle,
        )

    def _on_carrier_node_expanded(self, item: QTreeWidgetItem) -> None:
        """Узел раскрыт: запоминаем перевозчика — дерево перестраивается."""
        carrier_id = self._carrier_id_of_node(item)
        if carrier_id is not None:
            self._expanded_carrier_ids.add(carrier_id)

    def _on_carrier_node_collapsed(self, item: QTreeWidgetItem) -> None:
        """Узел свёрнут: перевозчик больше не раскрыт."""
        carrier_id = self._carrier_id_of_node(item)
        if carrier_id is not None:
            self._expanded_carrier_ids.discard(carrier_id)

    def _carrier_id_of_node(self, item: QTreeWidgetItem) -> Optional[int]:
        """ID перевозчика, к которому относится узел (для детей — родителя)."""
        if item is None:
            return None
        parent = item if item.parent() is None else item.parent()
        return self._as_id(self._node_record(parent).get("id"))

    # ─────────────────────────────────────────────────────────
    # Поиск по дереву перевозчиков
    # ─────────────────────────────────────────────────────────

    def _schedule_carrier_tree_filter(self, text: str) -> None:
        """Откладывает фильтрацию дерева (debounce 250 мс, как у таблиц)."""
        self._carrier_search_text = text or ""
        self._carrier_timer.start()

    def _apply_carrier_tree_filter(self) -> None:
        """
        Применяет поиск к дереву: перечитывает оба уровня из базы.

        Фильтрация идёт по уже загруженным записям, но уровней два, и
        «водитель, которого видно только вместе с перевозчиком» — это
        именно запрос к базе, а не перерисовка строк.
        """
        self._load_carriers_tree()

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

    def _org_table(self, is_carrier: bool) -> Optional[QTableWidget]:
        """
        Таблица организаций нужной роли (заказчики).

        Перевозчики в ШАГЕ «Дерево перевозчиков» переехали на дерево:
        таблицы у этой роли больше нет, поэтому для неё возвращается None,
        а запись берётся из узла (`_carrier_tab_selection`).

        Основной путь — именованный атрибут customers_table: он создаётся
        в __init__ и всегда соответствует своей роли. Признак is_carrier на
        таблице — запасной вариант и метка для тестов: у QVariant пустое
        значение и False неразличимы, поэтому сравнение идёт по
        exact-значению, а не по приведению к bool.
        """
        if is_carrier:
            return None

        table = getattr(self, "customers_table", None)
        if table is not None:
            return table

        for candidate in self.findChildren(QTableWidget):
            if candidate.property("is_carrier") is False:
                return candidate

        logger.error("Таблица заказчиков не найдена")
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
        """
        «📂 Загрузить в форму» на вкладке справочника организаций.

        У перевозчиков вкладка — дерево: выбран может быть любой уровень,
        и разбирает его `_load_carrier_item`. У заказчиков — таблица.
        """
        if is_carrier:
            self._load_carrier_item(self._selected_carrier_node())
            return

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
            if self._picker_load(False, org):
                self.accept()
                return

            if self.on_load_customer:
                self.on_load_customer(org)
            self.accept()
        except Exception as e:
            logger.error(f"Ошибка загрузки организации: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить запись:\n\n{e}")

    # ── Дерево перевозчиков: загрузка узла в форму ──

    def _on_carrier_item_double_clicked(
        self, item: QTreeWidgetItem, column: int
    ) -> None:
        """
        Двойной клик по узлу дерева перевозчиков.

        Перевозчик → в форму уходит ТОЛЬКО перевозчик: вкладка «Водитель»
        не трогается, оператор сам решит, кого вписать (например, временного
        водителя, которого нет в базе). Водитель → в форму уходит И водитель,
        И его перевозчик (перевозчика подтягивает MainWindow).

        Щелчок по стрелке «+»/«−» сюда не приходит: Qt считает украшение
        узла своим (QTreeView::mouseDoubleClickEvent не шлёт doubleClicked,
        если клик пришёлся на украшение) — узел только раскрывается.
        Двойной клик по пустому месту дерева тоже ничего не делает: узла нет,
        а без узла и записи нет (для кнопки «Загрузить в форму» подсказка
        «Выберите запись из списка» остаётся — см. `_load_carrier_item`).
        """
        if item is None:
            return
        self._load_carrier_item(item)

    def _load_carrier_item(self, item: Optional[QTreeWidgetItem]) -> None:
        """Загружает в форму узел дерева перевозчиков (перевозчика или водителя)."""
        record = self._node_record(item)
        if not record:
            QMessageBox.warning(self, "Загрузка", "Выберите запись из списка.")
            return

        try:
            if self._node_kind(item) == NODE_KIND_DRIVER:
                self._load_driver_node(item, record)
                return
            self._load_carrier_record(record)
        except Exception as e:
            logger.error(f"Ошибка загрузки записи дерева перевозчиков: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить запись:\n\n{e}")

    def _load_driver_node(
        self, item: Optional[QTreeWidgetItem], driver: Dict[str, Any]
    ) -> None:
        """
        Водитель, выбранный в дереве перевозчиков.

        В режиме ВЫБОРА вкладка «Перевозчики» отдаёт перевозчика (так её
        открывает аренда — «Арендодатель»), поэтому выбранный водитель
        означает «водитель вот этого перевозчика»: отдаём родителя.
        В остальных случаях — обычная загрузка водителя.
        """
        if self.picker_tab == OPEN_TAB_CARRIERS:
            parent = self._node_record(item.parent() if item is not None else None)
            if parent:
                self._load_carrier_record(parent)
                return
            logger.warning("Узел водителя без перевозчика — загружаем водителя")

        self._load_driver_record(driver)

    def _load_carrier_record(self, org: Dict[str, Any]) -> None:
        """Отдаёт перевозчика в форму; вкладка «Водитель» не трогается."""
        logger.info(f"Загрузка перевозчика из справочника: ID={org.get('id')}")

        if self._picker_load(True, org):
            self.accept()
            return

        if self.on_load_carrier:
            self.on_load_carrier(org)
        self.accept()

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

        self._load_driver_record(driver)

    def _load_driver_record(self, driver: Dict[str, Any]) -> None:
        """
        Отдаёт водителя в форму — общий путь таблицы и дерева.

        Тягач/прицеп подмешиваются в запись здесь же: MainWindow заполняет
        ими вкладку «Тягач и полуприцеп», и из дерева перевозчиков водитель
        должен прийти с тем же набором полей, что из таблицы «Водители».
        """
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
        """
        «✏ Редактировать» на вкладке справочника.

        В дереве перевозчиков выбран может быть водитель: тогда открывается
        диалог ВОДИТЕЛЯ (свой у перевозчика — диалог организации).
        """
        if is_carrier:
            kind, record = self._carrier_tab_selection()
            if not record:
                QMessageBox.warning(self, "Редактирование", "Выберите запись.")
                return
            if kind == NODE_KIND_DRIVER:
                self._edit_driver_record(record)
            else:
                self._edit_org_record(record, True)
            return

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

        self._edit_org_record(org, is_carrier)

    def _edit_org_record(self, org: Dict[str, Any], is_carrier: bool) -> None:
        """Правка организации: диалог и сохранение (общий путь таблицы и дерева)."""
        org_id = org.get("id")
        dialog = EditCarrierDialog(org, is_carrier=is_carrier, parent=self)
        accepted = dialog.exec_()

        # «👤 Водители…» пишет привязки в базу сразу: таблицы обновляем, даже
        # если карточку перевозчика оператор в итоге не сохранил.
        if getattr(dialog, "drivers_changed", False):
            self._refresh_driver_views()

        if not accepted:
            return

        new_data = dialog.get_data()
        ok = update_organization(org_id, new_data, is_carrier=is_carrier)
        if ok:
            if is_carrier:
                # Перевозчик — дерево, а у водителей в таблице колонка
                # «Перевозчик»: обновляем оба представления.
                self._refresh_driver_views()
            else:
                self._load_organizations(is_carrier=False)
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

        self._edit_driver_record(driver)

    def _edit_driver_record(self, driver: Dict[str, Any]) -> None:
        """Правка водителя (и его ТС) — общий путь таблицы и дерева."""
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
                self._refresh_driver_views()
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

            self._refresh_driver_views()
            QMessageBox.information(self, "Готово", "Водитель добавлен.")
        except Exception as e:
            logger.exception("Ошибка создания водителя")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось добавить водителя:\n{e}"
            )

    def _refresh_driver_views(self) -> None:
        """
        Перечитывает оба представления водителей.

        Водитель виден и в таблице вкладки «Водители», и дочерним узлом
        дерева перевозчиков (ШАГ «Дерево перевозчиков»). После правки,
        добавления, удаления и восстановления обновлять нужно оба, иначе
        дерево покажет прежнюю привязку.
        """
        self._load_drivers()
        self._load_carriers_tree()

    def _on_delete_org(self, is_carrier: bool) -> None:
        """«🗑 Удалить»: у перевозчиков — узел дерева (перевозчик или водитель)."""
        if is_carrier:
            kind, record = self._carrier_tab_selection()
            if not record:
                QMessageBox.warning(self, "Удаление", "Выберите запись из списка.")
                return
            if kind == NODE_KIND_DRIVER:
                self._delete_driver_record(record)
            else:
                self._delete_org_record(record, True)
            return

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

        self._delete_org_record(org, is_carrier)

    def _delete_org_record(self, org: Dict[str, Any], is_carrier: bool) -> None:
        """Мягкое удаление организации (общий путь таблицы и дерева)."""
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
        """«♻ Восстановить»: у перевозчиков — узел дерева (организация или водитель)."""
        if is_carrier:
            kind, record = self._carrier_tab_selection()
            if not record:
                QMessageBox.warning(self, "Восстановление", "Выберите запись из списка.")
                return
            if kind == NODE_KIND_DRIVER:
                self._restore_driver_record(record)
            else:
                self._restore_org_record(record, True)
            return

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

        self._restore_org_record(org, is_carrier)

    def _restore_org_record(self, org: Dict[str, Any], is_carrier: bool) -> None:
        """Возвращает мягко удалённую организацию в справочник."""
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

        self._delete_driver_record(driver)

    def _delete_driver_record(self, driver: Dict[str, Any]) -> None:
        """Мягкое удаление водителя (общий путь таблицы и дерева)."""
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
                self._refresh_driver_views()
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

        self._restore_driver_record(driver)

    def _restore_driver_record(self, driver: Dict[str, Any]) -> None:
        """Возвращает мягко удалённого водителя в справочник (таблица и дерево)."""
        if not driver.get("is_deleted"):
            QMessageBox.information(
                self, "Восстановление", "Этот водитель и так доступен в справочнике."
            )
            return

        driver_id = driver.get("id")
        try:
            if restore_driver(driver_id):
                self._refresh_driver_views()
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

    # ─────────────────────────────────────────────────────────
    # Карточка перевозчика выбранного водителя (вкладка «Водители»)
    # ─────────────────────────────────────────────────────────

    def _on_driver_selection_changed(self) -> None:
        """Выбор строки в таблице водителей: кнопка карточки перевозчика."""
        button = getattr(self, "btn_carrier_card", None)
        if button is None:  # до создания кнопки (не должно случаться)
            return
        button.setEnabled(self._driver_row_record() is not None)

    def _driver_row_record(self) -> Optional[Dict[str, Any]]:
        """Запись водителя, выбранная в таблице вкладки «Водители»."""
        table = getattr(self, "drivers_table", None)
        row = -1 if table is None else table.currentRow()
        if row < 0:
            return None

        item = table.item(row, DRIVER_COL_ID)
        record = item.data(Qt.UserRole) if item else None
        return record if isinstance(record, dict) else None

    def _on_open_driver_carrier(self) -> None:
        """
        «🚛 Перевозчик…»: диалог привязки водителя к перевозчику.

        Открывается список всех перевозчиков: текущий выделен и помечен «✓»,
        там же можно привязать другого, отвязать (с подтверждением) и
        загрузить перевозчика в форму. Вкладка «Водитель» диалогом не
        трогается — он про привязку к справочнику.
        """
        self._log_ui_action("нажата кнопка «🚛 Перевозчик…»")

        driver = self._driver_row_record()
        if driver is None:
            QMessageBox.warning(self, "Перевозчик", "Выберите водителя из списка.")
            return

        dialog = DriverCarrierDialog(
            driver,
            parent=self,
            on_load_carrier=self.on_load_carrier,
        )
        dialog.exec_()

        if dialog.changed:
            # Привязка поменялась — обновляем таблицу водителей и дерево.
            self._refresh_driver_views()