#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Условия договора».
Содержит:
  - тип перевозчика (единый источник истины)
  - ставку НДС
  - маршрут
  - места погрузки/выгрузки (таблицы «Наименование | Адрес | Дата | Время»)
  - ПЛАНОВЫЕ даты подачи ТС и завершения выгрузки (для шаблона)
  - стоимость и порядок оплаты
  - особые условия

Наименование салона (ШАГ FIX-2.5) подтягивается из справочника адресов:
при вводе адреса вкладка ищет запись с похожим адресом и, если наименование
в строке ещё пустое, подставляет его. Ручной ввод не затирается: любое
заполненное наименование остаётся как есть.

Отправляет сигналы loadings_changed / unloadings_changed при изменении
таблиц погрузок/выгрузок, чтобы другие вкладки могли обновить свои списки.
"""

import logging
import re
from typing import Dict, Any, List, Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLineEdit,
    QDateEdit, QTimeEdit, QDoubleSpinBox, QComboBox, QTextEdit,
    QLabel, QGroupBox, QHBoxLayout, QScrollArea, QRadioButton,
    QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)
from PyQt5.QtCore import QDate, QTime, QTimer, pyqtSignal, Qt

from ui.tabs.base_tab import TabMixin
from ui.widgets import RecognitionPanel
from ui import theme
from ui.address_book_dialog import AddressBookDialog
from db.database import get_addresses

logger = logging.getLogger("ui.tabs.contract_tab")

MAX_POINTS = 10

#: Колонки таблиц погрузок и выгрузок (порядок — как в бланке договора:
#: сначала наименование салона, затем адрес).
COL_NAME = 0
COL_ADDRESS = 1
COL_DATE = 2
COL_TIME = 3

#: Заголовки колонок таблиц точек маршрута.
POINT_HEADERS = ("Наименование", "Адрес *", "Дата", "Время")

#: Пауза перед поиском салона в справочнике: запрос уходит после того, как
#: пользователь перестал печатать адрес, а не на каждую букву.
SALON_LOOKUP_DELAY_MS = 400

#: Сколько первых полей адреса участвует в запасном запросе («г. Москва,
#: ул. Перерва, д. 19» → «г. Москва, ул. Перерва»): полный адрес из UI
#: почти никогда не совпадает с адресом справочника посимвольно.
SALON_FALLBACK_PARTS = 2

#: Размер выборки при поиске салона: имя ищем среди первых совпадений —
#: страница справочника целиком здесь не нужна.
SALON_SEARCH_LIMIT = 20


class PlanDateEdit(QDateEdit):
    """Дата с календарём без случайного изменения колёсиком мыши."""

    def wheelEvent(self, event):
        event.ignore()


class PlanTimeEdit(QTimeEdit):
    """Время, которое меняется только при явном редактировании."""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelComboBox(QComboBox):
    """Не переключает пункт при прокрутке формы."""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """Не меняет стоимость при прокрутке формы."""

    def wheelEvent(self, event):
        event.ignore()


class ContractTab(TabMixin, QWidget):
    """Вкладка с условиями договора."""

    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    # ── Сигналы для синхронизации с другими вкладками ──
    loadings_changed = pyqtSignal()
    unloadings_changed = pyqtSignal()

    def __init__(self):
        super().__init__()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)

        # ── Панель распознавания ──
        self.recognition_panel = RecognitionPanel(
            placeholder="Вставьте текст с условиями договора (маршрут, даты, стоимость)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Основные условия ──
        main_group = QGroupBox("Основные условия")
        main_layout = QFormLayout(main_group)

        self.number = QLineEdit()
        self.number.setPlaceholderText("Автоматически")
        main_layout.addRow("Номер договора *", self.number)

        self.date = QDateEdit()
        self.date.setDisplayFormat("dd.MM.yyyy")
        self.date.setCalendarPopup(True)
        self.date.setDate(QDate.currentDate())
        main_layout.addRow("Дата заключения *", self.date)

        layout.addWidget(main_group)

        # ── Маршрут ──
        route_group = QGroupBox("Маршрут и погрузка/выгрузка")
        route_layout = QVBoxLayout(route_group)

        route_form = QFormLayout()
        self.route = QLineEdit()
        self.route.setPlaceholderText("Москва → Санкт-Петербург")
        route_form.addRow("Маршрут *", self.route)
        route_layout.addLayout(route_form)

        # ── Таблица «Места погрузки» ──
        loading_label = QLabel("📍 Места погрузки (до 10):")
        loading_label.setStyleSheet("font-weight: bold; margin-top: 6px;")
        route_layout.addWidget(loading_label)

        load_btns = QHBoxLayout()
        self.btn_add_loading = theme.secondary_button("Добавить погрузку")
        self.btn_add_loading.clicked.connect(self._on_add_loading)
        load_btns.addWidget(self.btn_add_loading)

        self.btn_remove_loading = theme.danger_button("Удалить погрузку")
        self.btn_remove_loading.clicked.connect(self._on_remove_loading)
        load_btns.addWidget(self.btn_remove_loading)

        self.btn_book_loading = theme.secondary_button("Из справочника")
        self.btn_book_loading.clicked.connect(lambda: self._on_open_book("loading"))
        load_btns.addWidget(self.btn_book_loading)

        load_btns.addStretch()
        route_layout.addLayout(load_btns)

        self.loadings_table = QTableWidget(1, len(POINT_HEADERS))
        self.loadings_table.setHorizontalHeaderLabels(list(POINT_HEADERS))
        self.loadings_table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.ResizeToContents)
        self.loadings_table.horizontalHeader().setSectionResizeMode(COL_ADDRESS, QHeaderView.Stretch)
        self.loadings_table.horizontalHeader().setSectionResizeMode(COL_DATE, QHeaderView.ResizeToContents)
        self.loadings_table.horizontalHeader().setSectionResizeMode(COL_TIME, QHeaderView.ResizeToContents)
        self.loadings_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.loadings_table.setMinimumHeight(80)
        self._init_loading_row(0)
        route_layout.addWidget(self.loadings_table)

        # ── Таблица «Места выгрузки» ──
        unloading_label = QLabel("📍 Места выгрузки (до 10):")
        unloading_label.setStyleSheet("font-weight: bold; margin-top: 6px;")
        route_layout.addWidget(unloading_label)

        unload_btns = QHBoxLayout()
        self.btn_add_unloading = theme.secondary_button("Добавить выгрузку")
        self.btn_add_unloading.clicked.connect(self._on_add_unloading)
        unload_btns.addWidget(self.btn_add_unloading)

        self.btn_remove_unloading = theme.danger_button("Удалить выгрузку")
        self.btn_remove_unloading.clicked.connect(self._on_remove_unloading)
        unload_btns.addWidget(self.btn_remove_unloading)

        self.btn_book_unloading = theme.secondary_button("Из справочника")
        self.btn_book_unloading.clicked.connect(lambda: self._on_open_book("unloading"))
        unload_btns.addWidget(self.btn_book_unloading)

        unload_btns.addStretch()
        route_layout.addLayout(unload_btns)

        self.unloadings_table = QTableWidget(1, len(POINT_HEADERS))
        self.unloadings_table.setHorizontalHeaderLabels(list(POINT_HEADERS))
        self.unloadings_table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.ResizeToContents)
        self.unloadings_table.horizontalHeader().setSectionResizeMode(COL_ADDRESS, QHeaderView.Stretch)
        self.unloadings_table.horizontalHeader().setSectionResizeMode(COL_DATE, QHeaderView.ResizeToContents)
        self.unloadings_table.horizontalHeader().setSectionResizeMode(COL_TIME, QHeaderView.ResizeToContents)
        self.unloadings_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.unloadings_table.setMinimumHeight(80)
        self._init_unloading_row(0)
        route_layout.addWidget(self.unloadings_table)

        layout.addWidget(route_group)

        # ═══════════════════════════════════════════════════════════
        # ── ПЛАНОВЫЕ ДАТЫ (для шаблона договора) ──
        # ═══════════════════════════════════════════════════════════
        plan_group = QGroupBox("Плановые даты (для шаблона договора)")
        plan_layout = QFormLayout(plan_group)

        self.loading_plan_date = PlanDateEdit()
        self.loading_plan_date.setDisplayFormat("dd.MM.yyyy")
        self.loading_plan_date.setCalendarPopup(True)
        self.loading_plan_date.setDate(QDate.currentDate())
        self.loading_plan_date.setFixedWidth(150)
        self.loading_plan_date.setToolTip("Выберите дату через стрелку календаря или введите вручную")
        plan_layout.addRow("Плановая дата подачи ТС под погрузку:", self.loading_plan_date)

        time_layout = QHBoxLayout()

        self.loading_plan_time_from = PlanTimeEdit()
        self.loading_plan_time_from.setDisplayFormat("HH:mm")
        self.loading_plan_time_from.setTime(QTime(8, 0))
        self.loading_plan_time_from.setFixedWidth(90)
        time_layout.addWidget(QLabel("с"))
        time_layout.addWidget(self.loading_plan_time_from)

        self.loading_plan_time_to = PlanTimeEdit()
        self.loading_plan_time_to.setDisplayFormat("HH:mm")
        self.loading_plan_time_to.setTime(QTime(20, 0))
        self.loading_plan_time_to.setFixedWidth(90)
        time_layout.addWidget(QLabel("по"))
        time_layout.addWidget(self.loading_plan_time_to)
        time_layout.addStretch()

        plan_layout.addRow("Время подачи:", time_layout)

        self.unloading_plan_date = PlanDateEdit()
        self.unloading_plan_date.setDisplayFormat("dd.MM.yyyy")
        self.unloading_plan_date.setCalendarPopup(True)
        self.unloading_plan_date.setDate(QDate.currentDate().addDays(3))
        self.unloading_plan_date.setFixedWidth(150)
        self.unloading_plan_date.setToolTip("Выберите дату через стрелку календаря или введите вручную")
        plan_layout.addRow("Плановая дата завершения выгрузки:", self.unloading_plan_date)

        layout.addWidget(plan_group)

        # ═══════════════════════════════════════════════════════════
        # ── СТОИМОСТЬ УСЛУГ ──
        # ═══════════════════════════════════════════════════════════
        price_group = QGroupBox("Стоимость услуг")
        price_layout = QFormLayout(price_group)

        self.carrier_type = NoWheelComboBox()
        self.carrier_type.addItems([
            "ООО (с НДС)",
            "ИП с НДС",
            "ИП без НДС",
        ])
        self.carrier_type.setCurrentIndex(0)
        price_layout.addRow("Тип перевозчика *", self.carrier_type)

        self.vat_type_label = QLabel("Тип стоимости:")
        self.vat_type_layout = QHBoxLayout()
        self.radio_with_vat = QRadioButton("С НДС")
        self.radio_without_vat = QRadioButton("Без НДС")
        self.radio_without_vat.setChecked(True)
        self.vat_type_layout.addWidget(self.radio_with_vat)
        self.vat_type_layout.addWidget(self.radio_without_vat)
        self.vat_type_layout.addStretch()
        price_layout.addRow(self.vat_type_label, self.vat_type_layout)

        self.price_input = NoWheelDoubleSpinBox()
        self.price_input.setRange(0, 100000000)
        self.price_input.setDecimals(2)
        self.price_input.setSuffix(" ₽")
        self.price_input.setValue(400000)
        price_layout.addRow("Стоимость *", self.price_input)

        self.vat_rate = QLineEdit()
        self.vat_rate.setPlaceholderText("22")
        self.vat_rate.setMaxLength(2)
        self.vat_rate.setText("22")
        price_layout.addRow("Ставка НДС (%)", self.vat_rate)

        self.price_with_vat = QLineEdit()
        self.price_with_vat.setReadOnly(True)
        # Оформление «только для чтения» берётся из темы (ui/theme.py):
        # локальный стиль нужен потому, что Qt не пересчитывает QSS при
        # изменении свойства readOnly у уже отрисованного поля.
        self.price_with_vat.setProperty("readonlyField", True)
        self.price_with_vat.setStyleSheet(theme.readonly_field_qss())
        price_layout.addRow("Стоимость (с НДС)", self.price_with_vat)

        self.price_without_vat = QLineEdit()
        self.price_without_vat.setReadOnly(True)
        self.price_without_vat.setProperty("readonlyField", True)
        self.price_without_vat.setStyleSheet(theme.readonly_field_qss())
        price_layout.addRow("Стоимость (без НДС)", self.price_without_vat)

        self.payment_days = QLineEdit()
        self.payment_days.setPlaceholderText("10")
        self.payment_days.setMaxLength(3)
        self.payment_days.setText("10")
        price_layout.addRow("Срок оплаты (дней) *", self.payment_days)

        layout.addWidget(price_group)

        # ── Особые условия ──
        special_group = QGroupBox("Особые условия")
        special_layout = QVBoxLayout(special_group)

        self.special_conditions = QTextEdit()
        self.special_conditions.setPlaceholderText("Дополнительные условия договора...")
        self.special_conditions.setFixedHeight(200)
        special_layout.addWidget(self.special_conditions)

        layout.addWidget(special_group)

        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        # Добавляется в основной layout (вне прокрутки): поля вкладки идут
        # сверху вниз, и фиксированные высоты внутри scroll не меняются.
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        # ── Сигналы расчёта ──
        self.price_input.valueChanged.connect(self._calculate_price)
        self.vat_rate.textChanged.connect(self._calculate_price)
        self.radio_with_vat.toggled.connect(self._calculate_price)
        self.radio_without_vat.toggled.connect(self._calculate_price)

        # ── Автоподстановка ставки НДС ──
        self.carrier_type.currentIndexChanged.connect(self._on_carrier_type_changed)

        # ── Сигналы изменения таблиц погрузок/выгрузок ──
        self.loadings_table.itemChanged.connect(self._on_table_item_changed)
        self.unloadings_table.itemChanged.connect(self._on_table_item_changed)

        # ── Поиск салона в справочнике (одна ячейка за раз) ──
        # Таймер один на вкладку: ввод идёт в одну ячейку, а держать
        # таймер на каждую строку обеих таблиц — лишние объекты.
        self._salon_cell: Optional[tuple] = None
        self._salon_timer = QTimer(self)
        self._salon_timer.setSingleShot(True)
        self._salon_timer.setInterval(SALON_LOOKUP_DELAY_MS)
        self._salon_timer.timeout.connect(self._lookup_salon_name)

        self._generate_contract_number()
        self._calculate_price()

        logger.debug("ContractTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Инициализация строк
    # ─────────────────────────────────────────────────────────

    def _init_loading_row(self, row: int, address: str = "", date_str: str = "",
                          time_str: str = "", name: str = "") -> None:
        self.loadings_table.setItem(row, COL_NAME, QTableWidgetItem(name))
        self.loadings_table.setItem(row, COL_ADDRESS, QTableWidgetItem(address))
        self.loadings_table.setItem(row, COL_DATE, QTableWidgetItem(date_str))
        self.loadings_table.setItem(row, COL_TIME, QTableWidgetItem(time_str))

    def _init_unloading_row(self, row: int, address: str = "", date_str: str = "",
                            time_str: str = "", name: str = "") -> None:
        self.unloadings_table.setItem(row, COL_NAME, QTableWidgetItem(name))
        self.unloadings_table.setItem(row, COL_ADDRESS, QTableWidgetItem(address))
        self.unloadings_table.setItem(row, COL_DATE, QTableWidgetItem(date_str))
        self.unloadings_table.setItem(row, COL_TIME, QTableWidgetItem(time_str))

    # ─────────────────────────────────────────────────────────
    # Сигналы при изменении таблиц
    # ─────────────────────────────────────────────────────────

    def _on_table_item_changed(self, item) -> None:
        """
        Реагирует на правку ячейки таблицы точек маршрута.

        Изменение адреса дополнительно запускает поиск салона: адрес —
        ключ, по которому наименование берётся из справочника. Остальные
        колонки (и ручная правка наименования) только оповещают соседние
        вкладки.
        """
        table = self.sender()
        if table is self.loadings_table:
            self.loadings_changed.emit()
        elif table is self.unloadings_table:
            self.unloadings_changed.emit()
        else:
            return

        if item.column() == COL_ADDRESS:
            self._salon_cell = (table, item.row())
            self._salon_timer.start()

    # ─────────────────────────────────────────────────────────
    # Подтягивание наименования салона из справочника
    # ─────────────────────────────────────────────────────────

    def _lookup_salon_name(self) -> None:
        """
        Ищет салон по адресу и подставляет наименование в соседнюю колонку.

        Правила (ШАГ FIX-2.5):
          * ищем только когда адрес заполнен, а наименование ПУСТОЕ —
            ручной ввод и уже подставленное значение не затираются;
          * ничего не нашли — оставляем пустое наименование, адрес не трогаем.
        """
        cell = self._salon_cell
        self._salon_cell = None
        if cell is None:
            return

        table, row = cell
        if row < 0 or row >= table.rowCount():
            return

        address = self._get_cell(table, row, COL_ADDRESS).strip()
        name = self._get_cell(table, row, COL_NAME).strip()
        if not address or name:
            return

        salon_name = self._find_salon_name(table, address)
        if not salon_name:
            return

        # Сигналы блокируем: подстановка не должна запускать новый поиск
        # и рассылать сигналы соседним вкладкам.
        table.blockSignals(True)
        table.setItem(row, COL_NAME, QTableWidgetItem(salon_name))
        table.blockSignals(False)

        # Список точек у соседней вкладки изменился (в нём есть наименование).
        self._emit_points_changed(table)
        logger.info(f"Салон подтянут из справочника: строка {row}")

    def _find_salon_name(self, table: QTableWidget, address: str) -> str:
        """
        Первое наименование салона из справочника по адресу.

        Справочник ищет подстроку: сначала пробуем адрес целиком, затем —
        его начало (первые два поля). Ошибки чтения базы не поднимаются
        наверх: без справочника вкладка должна работать как раньше.
        """
        point_type = "loading" if table is self.loadings_table else "unloading"

        for query in self._salon_queries(address):
            try:
                records = get_addresses(point_type, search=query, limit=SALON_SEARCH_LIMIT)
            except Exception as e:  # noqa: BLE001 — справочник может быть недоступен
                logger.error(
                    f"Поиск салона в справочнике не удался ({type(e).__name__})"
                )
                return ""

            for record in records:
                salon_name = str(record.get("salon_name") or "").strip()
                if salon_name:
                    return salon_name

        return ""

    @staticmethod
    def _salon_queries(address: str) -> List[str]:
        """Запросы к справочнику: адрес целиком, затем его начало."""
        queries = [address]
        parts = [part.strip() for part in address.split(",") if part.strip()]
        if len(parts) > SALON_FALLBACK_PARTS:
            queries.append(", ".join(parts[:SALON_FALLBACK_PARTS]))
        return queries

    def _emit_points_changed(self, table: QTableWidget) -> None:
        """Сигнал «точки изменились» — тот же, что при правке ячейки."""
        if table is self.loadings_table:
            self.loadings_changed.emit()
        else:
            self.unloadings_changed.emit()

    # ─────────────────────────────────────────────────────────
    # Публичные методы для получения актуальных списков
    # ─────────────────────────────────────────────────────────

    def get_loadings(self) -> List[Dict[str, str]]:
        """Возвращает список погрузок (только заполненные адреса)."""
        return self._read_table(self.loadings_table)

    def get_unloadings(self) -> List[Dict[str, str]]:
        """Возвращает список выгрузок (только заполненные адреса)."""
        return self._read_table(self.unloadings_table)

    # ─────────────────────────────────────────────────────────
    # Тип перевозчика → автоподстановка ставки НДС
    # ─────────────────────────────────────────────────────────

    def _on_carrier_type_changed(self, index: int) -> None:
        """При выборе типа перевозчика автоподставляет ставку НДС."""
        carrier_type = self.carrier_type.currentText()

        if "без НДС" in carrier_type:
            self.vat_rate.setText("0")
            self.vat_rate.setReadOnly(True)
            # Заблокированное поле оформляет тема (цвета — из активной палитры)
            self.vat_rate.setProperty("readonlyField", True)
            self.vat_rate.setStyleSheet(theme.readonly_field_qss())
            self.radio_without_vat.setChecked(True)
        else:
            self.vat_rate.setReadOnly(False)
            self.vat_rate.setProperty("readonlyField", False)
            self.vat_rate.setStyleSheet("")
            if not self.vat_rate.text().strip() or self.vat_rate.text().strip() == "0":
                self.vat_rate.setText("22")

        self._calculate_price()
        logger.info(f"Тип перевозчика: {carrier_type}, ставка НДС: {self.vat_rate.text()}%")

    # ─────────────────────────────────────────────────────────
    # Кнопки +/- таблиц
    # ─────────────────────────────────────────────────────────

    def _on_add_loading(self) -> None:
        if self.loadings_table.rowCount() >= MAX_POINTS:
            QMessageBox.warning(self, "Ограничение", f"Максимум {MAX_POINTS} мест погрузки.")
            return
        row = self.loadings_table.rowCount()
        self.loadings_table.insertRow(row)
        self._init_loading_row(row)
        self.loadings_changed.emit()

    def _on_remove_loading(self) -> None:
        row = self.loadings_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Удаление", "Выберите строку для удаления.")
            return
        if self.loadings_table.rowCount() <= 1:
            QMessageBox.warning(self, "Удаление", "Должно остаться хотя бы одно место погрузки.")
            return
        self.loadings_table.removeRow(row)
        self.loadings_changed.emit()

    def _on_add_unloading(self) -> None:
        if self.unloadings_table.rowCount() >= MAX_POINTS:
            QMessageBox.warning(self, "Ограничение", f"Максимум {MAX_POINTS} мест выгрузки.")
            return
        row = self.unloadings_table.rowCount()
        self.unloadings_table.insertRow(row)
        self._init_unloading_row(row)
        self.unloadings_changed.emit()

    def _on_remove_unloading(self) -> None:
        row = self.unloadings_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Удаление", "Выберите строку для удаления.")
            return
        if self.unloadings_table.rowCount() <= 1:
            QMessageBox.warning(self, "Удаление", "Должно остаться хотя бы одно место выгрузки.")
            return
        self.unloadings_table.removeRow(row)
        self.unloadings_changed.emit()

    # ─────────────────────────────────────────────────────────
    # Справочник адресов
    # ─────────────────────────────────────────────────────────

    def _on_open_book(self, point_type: str) -> None:
        """
        Выбор точки из справочника: в строку идут И наименование, И адрес.

        У справочника выгрузки это справочник салонов (ШАГ FIX-2.2), поэтому
        вместе с адресом берётся наименование юр. лица — ровно то, что
        печатается в заголовке блока выгрузки.
        """
        dialog = AddressBookDialog(point_type, parent=self)
        if dialog.exec_():
            selected = dialog.selected_address
            if not selected:
                return

            table = self.loadings_table if point_type == "loading" else self.unloadings_table

            row = table.currentRow()

            def _row_is_empty(r: int) -> bool:
                addr = table.item(r, COL_ADDRESS)
                return not addr or not addr.text().strip()

            if row < 0 or not _row_is_empty(row):
                found = False
                for r in range(table.rowCount()):
                    if _row_is_empty(r):
                        row = r
                        found = True
                        break
                if not found:
                    if table.rowCount() >= MAX_POINTS:
                        QMessageBox.warning(self, "Ограничение", f"Максимум {MAX_POINTS} мест.")
                        return
                    row = table.rowCount()
                    table.insertRow(row)
                    if point_type == "loading":
                        self._init_loading_row(row)
                    else:
                        self._init_unloading_row(row)

            # ── Блокируем сигналы, чтобы не было лишних emit-ов и поиска ──
            table.blockSignals(True)
            table.setItem(row, COL_NAME, QTableWidgetItem(selected.get("salon_name", "")))
            table.setItem(row, COL_ADDRESS, QTableWidgetItem(selected.get("address", "")))
            table.setItem(row, COL_DATE, QTableWidgetItem(selected.get("date", "")))
            table.setItem(row, COL_TIME, QTableWidgetItem(selected.get("time_window", "")))
            table.blockSignals(False)

            table.selectRow(row)

            # ── Один явный emit ──
            self._emit_points_changed(table)

            logger.info(f"Адрес из справочника вставлен в строку {row} ({point_type})")

    # ─────────────────────────────────────────────────────────
    # Чтение таблиц
    # ─────────────────────────────────────────────────────────

    def _read_table(self, table: QTableWidget) -> List[Dict[str, str]]:
        """
        Строки таблицы точками маршрута.

        Наименование салона уходит ключом `name` (как его ждёт генератор);
        строка без адреса точкой не считается — она пустая.
        """
        result = []
        for row in range(table.rowCount()):
            name = self._get_cell(table, row, COL_NAME)
            address = self._get_cell(table, row, COL_ADDRESS)
            date_str = self._get_cell(table, row, COL_DATE)
            time_str = self._get_cell(table, row, COL_TIME)
            if address.strip():
                result.append({
                    "name": name.strip(),
                    "address": address.strip(),
                    "date": date_str.strip(),
                    "time_window": time_str.strip(),
                })
        return result

    @staticmethod
    def _get_cell(table: QTableWidget, row: int, col: int) -> str:
        item = table.item(row, col)
        return item.text() if item else ""

    # ─────────────────────────────────────────────────────────
    # Заполнение таблиц
    # ─────────────────────────────────────────────────────────

    def _fill_table(self, table: QTableWidget, items: List[Dict[str, str]], init_func) -> None:
        table.blockSignals(True)
        table.setRowCount(0)
        if not items:
            items = [{"name": "", "address": "", "date": "", "time_window": ""}]
        for item in items:
            row = table.rowCount()
            table.insertRow(row)
            init_func(
                row,
                item.get("address", ""),
                item.get("date", ""),
                item.get("time_window", ""),
                self._point_name(item),
            )
        table.blockSignals(False)

    @staticmethod
    def _point_name(item: Dict[str, Any]) -> str:
        """
        Наименование салона из данных точки.

        Ключ `name` — свой для вкладки; `salon_name` принимается как
        запасной (так поле называется в справочнике салонов и в ответе
        распознавания Логистикса).
        """
        name = item.get("name") or item.get("salon_name") or ""
        return str(name).strip()

    # ─────────────────────────────────────────────────────────
    # Автогенерация номера
    # ─────────────────────────────────────────────────────────

    def _generate_contract_number(self) -> None:
        import random
        from datetime import datetime
        current_date = datetime.now()
        suffix = random.randint(10, 99)
        number = f"{current_date.strftime('%d%m%Y')}-{suffix}"
        self.number.setText(number)
        logger.info(f"Сгенерирован номер договора: {number}")

    # ─────────────────────────────────────────────────────────
    # Авторасчёт стоимости
    # ─────────────────────────────────────────────────────────

    def _calculate_price(self) -> None:
        try:
            input_price = self.price_input.value()

            carrier_type = self.carrier_type.currentText()
            if "без НДС" in carrier_type:
                vat_rate = 0.0
                self.vat_rate.setText("0")
            else:
                vat_rate = float(self.vat_rate.text().strip() or 0)
        except (ValueError, TypeError):
            vat_rate = 0
            input_price = 0

        vat_multiplier = 1 + vat_rate / 100

        if self.radio_without_vat.isChecked():
            price_without_vat = input_price
            price_with_vat = round(input_price * vat_multiplier, 2)
        else:
            price_with_vat = input_price
            price_without_vat = round(input_price / vat_multiplier, 2) if vat_multiplier else input_price

        self.price_without_vat.setText(f"{price_without_vat:.2f} ₽")
        self.price_with_vat.setText(f"{price_with_vat:.2f} ₽")

    # ─────────────────────────────────────────────────────────
    # Сбор данных
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        try:
            vat_rate = float(self.vat_rate.text().strip() or 0)
        except (ValueError, TypeError):
            vat_rate = 0

        try:
            payment_days = int(self.payment_days.text().strip() or 0)
        except (ValueError, TypeError):
            payment_days = 0

        price_without_vat_text = self.price_without_vat.text().replace("₽", "").strip()
        price_with_vat_text = self.price_with_vat.text().replace("₽", "").strip()

        try:
            price_without_vat = float(price_without_vat_text.replace(",", "."))
        except ValueError:
            price_without_vat = 0.0

        try:
            price_with_vat = float(price_with_vat_text.replace(",", "."))
        except ValueError:
            price_with_vat = 0.0

        loadings = self._read_table(self.loadings_table)
        unloadings = self._read_table(self.unloadings_table)

        loading_address = loadings[0]["address"] if loadings else ""
        loading_date = loadings[0]["date"] if loadings else ""
        loading_time_window = loadings[0]["time_window"] if loadings else ""

        unloading_address_1 = unloadings[0]["address"] if len(unloadings) > 0 else ""
        unloading_address_2 = unloadings[1]["address"] if len(unloadings) > 1 else ""
        unloading_date = unloadings[-1]["date"] if unloadings else ""
        unloading_time_window = unloadings[-1]["time_window"] if unloadings else ""

        loading_plan_date_iso = self.loading_plan_date.date().toString("yyyy-MM-dd")
        loading_plan_time_from_str = self.loading_plan_time_from.time().toString("HH:mm")
        loading_plan_time_to_str = self.loading_plan_time_to.time().toString("HH:mm")
        unloading_plan_date_iso = self.unloading_plan_date.date().toString("yyyy-MM-dd")

        return {
            "number": self.number.text().strip(),
            "date": self.date.date().toString("yyyy-MM-dd"),
            "route": self.route.text().strip(),
            "loadings": loadings,
            "unloadings": unloadings,
            "loading_address": loading_address,
            "loading_date": loading_date,
            "loading_time_window": loading_time_window,
            "unloading_address_1": unloading_address_1,
            "unloading_address_2": unloading_address_2,
            "unloading_date": unloading_date,
            "unloading_time_window": unloading_time_window,
            "carrier_type": self.carrier_type.currentText(),
            "vat_rate": f"{vat_rate:.0f}%",
            "vat_rate_num": vat_rate,
            "price_input": self.price_input.value(),
            "price_without_vat": price_without_vat,
            "price_with_vat": price_with_vat,
            "vat_type": "with_vat" if self.radio_with_vat.isChecked() else "without_vat",
            "payment_days": payment_days,
            "special_conditions": self.special_conditions.toPlainText().strip(),
            "loading_plan_date": loading_plan_date_iso,
            "loading_plan_time_from": loading_plan_time_from_str,
            "loading_plan_time_to": loading_plan_time_to_str,
            "loading_plan_time_full": f"с {loading_plan_time_from_str} по {loading_plan_time_to_str}",
            "unloading_plan_date": unloading_plan_date_iso,
        }

    # ─────────────────────────────────────────────────────────
    # Заполнение из распознанных данных
    # ─────────────────────────────────────────────────────────

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет условия договора.

        Как и на других вкладках, обновляется только то, что реально пришло:
        при распознавании ответ бывает частичным, и пустые значения не должны
        сбрасывать введённые вручную номер, даты, стоимость и срок оплаты.
        """
        if not data:
            return

        if data.get("number"):
            self.number.setText(data["number"])

        if data.get("date"):
            self._set_date(self.date, data["date"])

        if data.get("route"):
            self.route.setText(data["route"])

        # ── Погрузки ──
        loadings = data.get("loadings")
        if not loadings and data.get("loading_address"):
            loadings = [{
                "name": self._point_name(data),
                "address": data.get("loading_address", ""),
                "date": data.get("loading_date", ""),
                "time_window": data.get("loading_time_window", ""),
            }]
        if loadings:
            self._fill_table(self.loadings_table, loadings, self._init_loading_row)

        # ── Выгрузки ──
        unloadings = data.get("unloadings")
        if not unloadings:
            legacy = []
            if data.get("unloading_address_1"):
                legacy.append({
                    "name": data.get("unloading_name_1", ""),
                    "address": data.get("unloading_address_1", ""),
                    "date": data.get("unloading_date", ""),
                    "time_window": data.get("unloading_time_window", ""),
                })
            if data.get("unloading_address_2"):
                legacy.append({
                    "name": data.get("unloading_name_2", ""),
                    "address": data.get("unloading_address_2", ""),
                    "date": data.get("unloading_date", ""),
                    "time_window": data.get("unloading_time_window", ""),
                })
            unloadings = legacy
        if unloadings:
            self._fill_table(self.unloadings_table, unloadings, self._init_unloading_row)

        # ── Тип перевозчика ──
        carrier_type = data.get("carrier_type", "")
        if carrier_type:
            idx = self.carrier_type.findText(carrier_type)
            if idx >= 0:
                self.carrier_type.setCurrentIndex(idx)

        # ── Стоимость ──
        # Значения по умолчанию здесь не подставляются: иначе распознавание
        # без блока «contract» вернуло бы НДС и срок оплаты к 22% и 10 дням.
        vat_type = data.get("vat_type", "")
        if vat_type == "with_vat":
            self.radio_with_vat.setChecked(True)
        elif vat_type == "without_vat":
            self.radio_without_vat.setChecked(True)

        price_input = data.get("price_input", 0)
        if price_input:
            self.price_input.setValue(float(price_input))

        vat_rate = data.get("vat_rate", "")
        if vat_rate:
            vat_rate_clean = str(vat_rate).replace("%", "").strip()
            if vat_rate_clean:
                self.vat_rate.setText(vat_rate_clean)

        payment_days = data.get("payment_days", "")
        if payment_days:
            self.payment_days.setText(str(payment_days))

        if data.get("special_conditions"):
            self.special_conditions.setPlainText(data["special_conditions"])

        # ── Плановые даты ──
        plan_loading_date = data.get("loading_plan_date", "")
        if plan_loading_date:
            self._set_date(self.loading_plan_date, plan_loading_date)
        elif loadings:
            first_loading_date = loadings[0].get("date", "")
            if first_loading_date:
                self._set_date(self.loading_plan_date, first_loading_date)

        plan_time_from = data.get("loading_plan_time_from", "")
        plan_time_to = data.get("loading_plan_time_to", "")

        if plan_time_from and plan_time_to:
            try:
                self.loading_plan_time_from.setTime(QTime.fromString(plan_time_from, "HH:mm"))
                self.loading_plan_time_to.setTime(QTime.fromString(plan_time_to, "HH:mm"))
            except Exception:
                pass
        elif loadings:
            tw = loadings[0].get("time_window", "") or ""
            m = re.search(r"(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})", tw)
            if m:
                self.loading_plan_time_from.setTime(QTime.fromString(m.group(1), "HH:mm"))
                self.loading_plan_time_to.setTime(QTime.fromString(m.group(2), "HH:mm"))

        plan_unloading_date = data.get("unloading_plan_date", "")
        if plan_unloading_date:
            self._set_date(self.unloading_plan_date, plan_unloading_date)
        elif unloadings:
            last_unloading_date = unloadings[-1].get("date", "")
            if last_unloading_date:
                self._set_date(self.unloading_plan_date, last_unloading_date)

        self._calculate_price()

        # Отправляем сигналы после заполнения
        self.loadings_changed.emit()
        self.unloadings_changed.emit()

        logger.info("Данные договора заполнены")

    # ─────────────────────────────────────────────────────────
    # Очистка
    # ─────────────────────────────────────────────────────────

    def clear(self) -> None:
        self._generate_contract_number()
        self.date.setDate(QDate.currentDate())
        self.route.clear()

        self.loadings_table.blockSignals(True)
        self.loadings_table.setRowCount(0)
        self.loadings_table.insertRow(0)
        self._init_loading_row(0)
        self.loadings_table.blockSignals(False)

        self.unloadings_table.blockSignals(True)
        self.unloadings_table.setRowCount(0)
        self.unloadings_table.insertRow(0)
        self._init_unloading_row(0)
        self.unloadings_table.blockSignals(False)

        self.loading_plan_date.setDate(QDate.currentDate())
        self.loading_plan_time_from.setTime(QTime(8, 0))
        self.loading_plan_time_to.setTime(QTime(20, 0))
        self.unloading_plan_date.setDate(QDate.currentDate().addDays(3))

        self.carrier_type.setCurrentIndex(0)
        self.vat_rate.setReadOnly(False)
        self.vat_rate.setText("22")

        self.price_input.setValue(400000)
        self.payment_days.setText("10")
        self.special_conditions.clear()
        self.radio_without_vat.setChecked(True)

        self._calculate_price()
        self.recognition_panel.clear()

        # Отправляем сигналы после очистки
        self.loadings_changed.emit()
        self.unloadings_changed.emit()

        logger.debug("Поля договора очищены")
