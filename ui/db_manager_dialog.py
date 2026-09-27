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
"""

import logging
from typing import Dict, Any, Optional, Callable, List

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QMessageBox, QLabel, QAbstractItemView,
    QLineEdit, QFormLayout, QScrollArea, QGroupBox, QTextEdit,
    QCheckBox,
)
from PyQt5.QtCore import Qt, QTimer

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
)

from ui import theme

logger = logging.getLogger("ui.db_manager_dialog")


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


# ═════════════════════════════════════════════════════════════
# Основной диалог
# ═════════════════════════════════════════════════════════════

class DbManagerDialog(QDialog):
    """Диалог управления базой данных."""

    # Сколько строк максимум показывать при поиске
    # (защита от вывода тысяч строк в таблицу)
    SEARCH_LIMIT = 500

    def __init__(
        self,
        parent=None,
        on_load_driver: Optional[Callable] = None,
        on_load_customer: Optional[Callable] = None,
        on_load_carrier: Optional[Callable] = None,
    ):
        super().__init__(parent)

        self.on_load_driver = on_load_driver
        self.on_load_customer = on_load_customer
        self.on_load_carrier = on_load_carrier

        self.setWindowTitle("🗄 Управление базой данных")
        self.setMinimumSize(1100, 650)

        layout = QVBoxLayout(self)

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

        logger.debug("DbManagerDialog инициализирован")

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

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionsMovable(True)
        header.setMinimumSectionSize(60)
        table.setColumnWidth(0, 60)
        table.setColumnWidth(1, 320)
        table.setColumnWidth(2, 130)
        table.setColumnWidth(3, 120)
        table.setColumnWidth(4, 250)
        table.setColumnWidth(5, 90)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setWordWrap(False)

        # ── Двойной клик = загрузить в форму ──
        table.doubleClicked.connect(lambda: self._on_load_org(is_carrier))

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

        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(
            ["ID", "ФИО", "Дата рождения", "Паспорт", "Телефон", "Статус"]
        )

        # ── СОРТИРОВКА ──
        table.setSortingEnabled(True)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionsMovable(True)
        header.setMinimumSectionSize(60)
        table.setColumnWidth(0, 60)
        table.setColumnWidth(1, 320)
        table.setColumnWidth(2, 140)
        table.setColumnWidth(3, 180)
        table.setColumnWidth(4, 200)
        table.setColumnWidth(5, 90)
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

        search_input.textChanged.connect(self._schedule_driver_filter)
        btn_reset.clicked.connect(lambda: search_input.clear())

        return widget

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

        table = self.carriers_table if is_carrier else self.customers_table
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
                item_status.setForeground(Qt.red)
            table.setItem(row, 5, item_status)

        # Включаем сортировку и сортируем по «Наименование» (колонка 1)
        table.setSortingEnabled(True)
        table.sortByColumn(1, Qt.AscendingOrder)

    def _load_drivers(self) -> None:
        try:
            drivers = get_all_drivers(include_deleted=self._show_deleted())
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

        for driver in drivers:
            row = table.rowCount()
            table.insertRow(row)

            item_id = QTableWidgetItem(str(driver.get("id", "")))
            item_id.setData(Qt.UserRole, driver)
            table.setItem(row, 0, item_id)

            table.setItem(row, 1, QTableWidgetItem(driver.get("full_name", "")))
            table.setItem(row, 2, QTableWidgetItem(driver.get("birth_date", "")))

            passport = f"{driver.get('passport_series', '')} {driver.get('passport_number', '')}".strip()
            table.setItem(row, 3, QTableWidgetItem(passport))
            table.setItem(row, 4, QTableWidgetItem(driver.get("phone", "")))

            status = "удалён" if driver.get("is_deleted") else ""
            item_status = QTableWidgetItem(status)
            if status:
                item_status.setForeground(Qt.red)
            table.setItem(row, 5, item_status)

        # Включаем сортировку и сортируем по «ФИО» (колонка 1)
        table.setSortingEnabled(True)
        table.sortByColumn(1, Qt.AscendingOrder)

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

        table = self.carriers_table if is_carrier else self.customers_table
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
            )
        except Exception as e:
            logger.error(f"Ошибка поиска водителей: {e}")
            found = []

        self._fill_driver_table(self.drivers_table, found)
        logger.info(f"Поиск водителей: запрос {len(text)} симв., найдено {len(found)}")

    # ─────────────────────────────────────────────────────────
    # Действия
    # ─────────────────────────────────────────────────────────

    def _on_load_org(self, is_carrier: bool) -> None:
        table = self.carriers_table if is_carrier else self.customers_table
        row = table.currentRow()

        if row < 0:
            QMessageBox.warning(self, "Загрузка", "Выберите запись из списка.")
            return

        item = table.item(row, 0)
        org = item.data(Qt.UserRole) if item else None

        if not org:
            QMessageBox.warning(self, "Загрузка", "Не удалось прочитать запись.")
            return

        try:
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

            if self.on_load_driver:
                self.on_load_driver(driver)
            self.accept()
        except Exception as e:
            logger.error(f"Ошибка загрузки водителя: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить запись:\n\n{e}")

    def _on_edit_org(self, is_carrier: bool) -> None:
        table = self.carriers_table if is_carrier else self.customers_table
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

            driver_id = save_driver(dialog.get_driver_data())

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
        table = self.carriers_table if is_carrier else self.customers_table
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
        table = self.carriers_table if is_carrier else self.customers_table
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