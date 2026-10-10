#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Условия договора».
Содержит:
  - форму перевозчика (ООО / ИП) и ставку НДС — из них вычисляется
    «тип перевозчика», по которому выбирается бланк
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
    QLabel, QGroupBox, QHBoxLayout, QScrollArea,
    QMessageBox, QTableWidget, QTableWidgetItem,
    QAbstractItemView,
)
from PyQt5.QtCore import QDate, QTime, QTimer, pyqtSignal, Qt

from core.vat import (
    DEFAULT_ENTITY_TYPE, DEFAULT_VAT_RATE, ENTITY_TYPES, VAT_RATES,
    compute_carrier_type, compute_vat, normalize_entity_type,
    normalize_vat_rate, split_carrier_type, total_from_base, vat_rate_number,
)
from ui.tabs.base_tab import TabMixin
from ui.widgets import RecognitionPanel
from ui.widgets.table_helpers import (
    MODE_CONTENTS, MODE_FIXED, MODE_STRETCH,
    install_tooltip_on_table, make_table_expandable, setup_point_table,
)
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

#: Режимы и ширины колонок таблиц точек (ШАГ FIX-5). Адрес — главная
#: колонка, она тянется по ширине таблицы; наименование — по содержимому;
#: дата и время фиксированные: в них 10 и 5 символов, растягивать нечего.
POINT_COLUMNS_CONFIG = (
    (COL_NAME, MODE_CONTENTS, 0),
    (COL_ADDRESS, MODE_STRETCH, 0),
    (COL_DATE, MODE_FIXED, 90),
    (COL_TIME, MODE_FIXED, 80),
)

#: Нижние границы ширин: «Дат» и «Вре» в шапке — это слишком узкие колонки.
POINT_COLUMN_MINIMUMS = {COL_NAME: 100, COL_DATE: 80, COL_TIME: 70}

#: Нижняя граница ВЫСОТЫ таблиц точек: 120 пикселей — это шапка (около 21)
#: и две полные строки по 40 (ROW_HEIGHT_TWO_LINES). Было 80: видно было
#: полторы строки, и адрес второй точки оператор не читал. Минимум не мешает
#: растяжению: политика Expanding отдаёт таблице свободное место, а 120 —
#: это пол, ниже которого она не сжимается.
POINT_TABLE_MIN_HEIGHT = 120

#: Ключи QSettings для раскладки колонок (у таблиц она своя).
LOADINGS_WIDTHS_KEY = "ui/contract_tab/loadings_columns"
UNLOADINGS_WIDTHS_KEY = "ui/contract_tab/unloadings_columns"

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
        self.btn_book_loading.clicked.connect(self._on_open_loading_book)
        load_btns.addWidget(self.btn_book_loading)

        load_btns.addStretch()
        route_layout.addLayout(load_btns)

        self.loadings_table = self._make_points_table(LOADINGS_WIDTHS_KEY)
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
        self.btn_book_unloading.clicked.connect(self._on_open_unloading_book)
        unload_btns.addWidget(self.btn_book_unloading)

        unload_btns.addStretch()
        route_layout.addLayout(unload_btns)

        self.unloadings_table = self._make_points_table(UNLOADINGS_WIDTHS_KEY)
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
        # Форма стороны и ставка НДС выбираются ЗДЕСЬ (шаг «Ставки НДС»):
        # раньше форма была переключателем «Тип перевозчика», а «с НДС или
        # без» — радиокнопками, и это расходилось с видом перевозчика из
        # справочника. Теперь форма и ставка — один источник истины, а
        # `carrier_type` вкладка вычисляет из них (core/vat.py).
        price_group = QGroupBox("Стоимость услуг")
        price_layout = QFormLayout(price_group)

        self.entity_type = NoWheelComboBox()
        self.entity_type.addItems(list(ENTITY_TYPES))
        self.entity_type.setCurrentText(DEFAULT_ENTITY_TYPE)
        self.entity_type.setToolTip(
            "Форма перевозчика: ООО или ИП. От неё зависит бланк договора."
        )
        price_layout.addRow("Форма", self.entity_type)

        self.vat_rate = NoWheelComboBox()
        self.vat_rate.addItems(list(VAT_RATES))
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        self.vat_rate.setToolTip(
            "Ставка НДС. «Без НДС» — перевозчик не плательщик налога "
            "(УСН до 20 млн ₽), «0%» — ставка есть, налог нулевой."
        )
        price_layout.addRow("Ставка НДС", self.vat_rate)

        self.price_input = NoWheelDoubleSpinBox()
        self.price_input.setRange(0, 100000000)
        self.price_input.setDecimals(2)
        self.price_input.setSuffix(" ₽")
        # Стоимость НЕ подставляется: пустое поле — ноль, о незаполненной
        # стоимости скажет валидатор. Раньше здесь стояло 400 000 ₽ —
        # число выглядело как введённое оператором (ШАГ FIX-6, часть C).
        self.price_input.setValue(0)
        self.price_input.setToolTip(
            "Стоимость услуг — ИТОГ, который видит заказчик (сумма договора). "
            "Базу без НДС и сам налог вкладка считает из неё."
        )
        price_layout.addRow("Стоимость *", self.price_input)

        # ── Расчётные поля: только для чтения ──
        # Оформление «только для чтения» берётся из темы (ui/theme.py):
        # локальный стиль нужен потому, что Qt не пересчитывает QSS при
        # изменении свойства readOnly у уже отрисованного поля.
        self.vat_amount = QLineEdit()
        self.vat_amount.setReadOnly(True)
        self.vat_amount.setProperty("readonlyField", True)
        self.vat_amount.setStyleSheet(theme.readonly_field_qss())
        price_layout.addRow("НДС", self.vat_amount)

        self.price_without_vat = QLineEdit()
        self.price_without_vat.setReadOnly(True)
        self.price_without_vat.setProperty("readonlyField", True)
        self.price_without_vat.setStyleSheet(theme.readonly_field_qss())
        price_layout.addRow("Стоимость без НДС", self.price_without_vat)

        self.payment_days = QLineEdit()
        self.payment_days.setPlaceholderText("10")
        self.payment_days.setMaxLength(3)
        self.payment_days.setText("10")
        price_layout.addRow("Срок оплаты (дней) *", self.payment_days)

        # ── Предоплата (разбивка оплаты на предоплату и окончательный расчёт) ──
        # Вводить можно ЛЮБОЕ из двух полей: оператор называет либо сумму
        # в рублях, либо процент, и второе поле пересчитывается. База
        # процента — «Стоимость», то есть итог договора (та же величина, что
        # печатается в договоре). При изменении стоимости сумма предоплаты
        # НЕ пересчитывается (её ввёл оператор) — обновляется только процент.
        self.prepayment_amount = NoWheelDoubleSpinBox()
        self.prepayment_amount.setRange(0, 100000000)
        self.prepayment_amount.setDecimals(2)
        self.prepayment_amount.setSuffix(" ₽")
        self.prepayment_amount.setValue(0)
        self.prepayment_amount.setToolTip(
            "Сумма предоплаты в рублях. 0 — предоплата не предусмотрена. "
            "Можно вводить и здесь, и в поле «%» — второе поле пересчитается."
        )
        price_layout.addRow("Предоплата, ₽", self.prepayment_amount)

        self.prepayment_percent = NoWheelDoubleSpinBox()
        self.prepayment_percent.setRange(0, 100)
        self.prepayment_percent.setDecimals(2)
        self.prepayment_percent.setSuffix(" %")
        self.prepayment_percent.setValue(0)
        self.prepayment_percent.setToolTip(
            "Процент предоплаты от «Стоимости». Можно вводить и здесь, "
            "и в поле «Сумма» — второе поле пересчитается."
        )
        price_layout.addRow("Предоплата, %", self.prepayment_percent)

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
        self.vat_rate.currentIndexChanged.connect(self._on_vat_rate_changed)
        # Предоплата двусторонняя: ввод суммы считает процент, ввод процента
        # считает сумму. Слоты защищены флагом _prepayment_syncing, иначе
        # setValue одного поля вызывал бы слот второго и так по кругу.
        self.prepayment_amount.valueChanged.connect(self._on_prepayment_changed)
        self.prepayment_percent.valueChanged.connect(
            self._on_prepayment_percent_changed
        )

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

        # Процент предоплаты и флаг синхронизации полей: до первого расчёта
        # предоплаты нет, синхронизация не идёт.
        self._prepayment_percent = 0.0
        self._prepayment_syncing = False

        self._generate_contract_number()
        self._calculate_price()

        logger.debug("ContractTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Таблицы точек маршрута
    # ─────────────────────────────────────────────────────────

    def _make_points_table(self, storage_key: str) -> QTableWidget:
        """
        Таблица точек маршрута: четыре колонки, ширины, подсказки, раскладка.

        Обе таблицы вкладки (погрузки и выгрузки) устроены одинаково,
        отличается только ключ хранения раскладки, поэтому настройка живёт
        здесь, а не дублируется дважды (ШАГ FIX-5).

        Высота: таблица растягивается по вертикали, длинный адрес
        переносится по словам, строка вмещает две строки текста
        (ШАГ «Высота таблиц точек»).
        """
        table = QTableWidget(1, len(POINT_HEADERS))
        table.setHorizontalHeaderLabels(list(POINT_HEADERS))
        setup_point_table(
            table,
            POINT_COLUMNS_CONFIG,
            storage_key=storage_key,
            minimums=POINT_COLUMN_MINIMUMS,
        )
        install_tooltip_on_table(table)
        make_table_expandable(table)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setMinimumHeight(POINT_TABLE_MIN_HEIGHT)
        return table

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
    # Форма и ставка НДС → пересчёт стоимости
    # ─────────────────────────────────────────────────────────

    def current_vat_rate(self) -> str:
        """Ставка НДС из списка: «22%», «10%», «5%», «7%», «0%», «Без НДС»."""
        return self.vat_rate.currentText()

    def current_vat_rate_number(self) -> float:
        """Ставка НДС числом: «22%» → 22.0, «Без НДС» и «0%» → 0.0."""
        return vat_rate_number(self.current_vat_rate())

    def current_entity_type(self) -> str:
        """Форма перевозчика: «ООО» или «ИП»."""
        return self.entity_type.currentText()

    def computed_carrier_type(self) -> str:
        """
        Вид перевозчика — из формы и ставки (его читает генератор).

        Отдельным методом, а не строкой в get_data(): то же значение нужно
        и при загрузке записи, и в тестах стыка «вкладка → бланк».
        """
        return compute_carrier_type(
            self.current_entity_type(), self.current_vat_rate()
        )

    def _on_vat_rate_changed(self, index: int) -> None:
        """Смена ставки НДС: суммы пересчитываются, в лог — только ставка."""
        self._calculate_price()
        logger.info(f"Ставка НДС: {self.current_vat_rate()}")

    def apply_entity_type(self, entity_type: Any) -> bool:
        """
        Форма перевозчика из справочника — в поле «Форма».

        Нужна при загрузке перевозчика из базы: по форме выбирается бланк
        (ООО или ИП). Ставка НДС не трогается — в справочнике её нет, а
        угадывать её по виду стороны нельзя: ООО бывает и на ОСН, и на УСН.

        :return: True, если форма установлена.
        """
        normalized = normalize_entity_type(entity_type)
        if not normalized:
            logger.debug("Форма стороны из справочника не распознана — пропущена")
            return False

        self.entity_type.setCurrentText(normalized)
        logger.info(f"Форма перевозчика из справочника: {normalized}")
        return True

    def _apply_party_and_rate(self, data: Dict[str, Any]) -> None:
        """
        Форма и ставка НДС из данных (распознавание, база, старые записи).

        Сначала берутся явные поля `entity_type` и `vat_rate`. Если их нет,
        вид восстанавливается из `carrier_type` — так он хранился до этого
        шага: «ООО (с НДС)» → ООО и 22 %, «ИП без НДС» → ИП и «Без НДС».
        Ничего не пришло — поля не трогаются: распознавание без блока
        стоимости не должно сбрасывать выбор оператора.
        """
        entity_type = normalize_entity_type(data.get("entity_type"))
        vat_rate = normalize_vat_rate(data.get("vat_rate"))

        if not entity_type or not vat_rate:
            legacy_entity, legacy_rate = split_carrier_type(data.get("carrier_type"))
            entity_type = entity_type or legacy_entity
            vat_rate = vat_rate or legacy_rate

        if entity_type:
            self.entity_type.setCurrentText(entity_type)
        if vat_rate:
            self.vat_rate.setCurrentText(vat_rate)

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
        """
        Удаляет выбранную строку погрузки.

        Удалить можно и последнюю строку: пустая таблица — норма, о пустом
        разделе скажет валидатор при создании договора (ШАГ FIX-5).
        """
        row = self.loadings_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Удаление", "Выберите строку для удаления.")
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
        """
        Удаляет выбранную строку выгрузки.

        Последнюю строку удалить тоже можно (см. _on_remove_loading):
        пустая таблица — норма, за пустой раздел отвечает валидатор.
        """
        row = self.unloadings_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Удаление", "Выберите строку для удаления.")
            return
        self.unloadings_table.removeRow(row)
        self.unloadings_changed.emit()

    # ─────────────────────────────────────────────────────────
    # Справочник адресов
    # ─────────────────────────────────────────────────────────

    def _on_open_loading_book(self) -> None:
        """
        Кнопка «Из справочника» у мест погрузки.

        Отдельный слот, а не lambda с аргументом: lambda в connect,
        захватывающая вкладку, создаёт цикл ссылок Python ↔ Qt и роняет
        процесс при выходе (грабли 2B.7).
        """
        self._on_open_book("loading")

    def _on_open_unloading_book(self) -> None:
        """Кнопка «Из справочника» у мест выгрузки (см. _on_open_loading_book)."""
        self._on_open_book("unloading")

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
        """
        Пересчитывает базу без НДС и налог от введённой стоимости.

        «Стоимость» — это ИТОГ договора (сумма, которую видит заказчик):
        оператор вводит одну сумму, а вкладка вынимает из неё НДС («НДС в том
        числе»). Раньше было наоборот: вводилась база, налог считался сверху
        и прибавлялся, из-за чего сумма в договоре была больше введённой.

        «Без НДС» и «0%» дают нулевой налог: в первом случае перевозчик не
        плательщик, во втором ставка есть, а налог нулевой.
        """
        try:
            price_with_vat = float(self.price_input.value())
        except (ValueError, TypeError):
            price_with_vat = 0.0

        vat = compute_vat(price_with_vat, self.current_vat_rate())

        self.vat_amount.setText(f"{vat['sum_nds']:.2f} ₽")
        self.price_without_vat.setText(f"{vat['sum_wo_nds']:.2f} ₽")

        self._sync_prepayment_percent_from_amount()

    # ─────────────────────────────────────────────────────────
    # Предоплата: двусторонний ввод (сумма ↔ процент)
    # ─────────────────────────────────────────────────────────

    def _current_total_with_vat(self) -> float:
        """
        Текущий итог с НДС — база для процента предоплаты.

        Это ровно та сумма, которую оператор ввёл в поле «Стоимость» и
        которая печатается в договоре как итог. Значение читается из самого
        поля (а не из подписи «Стоимость без НДС»), чтобы процент считался
        от того же числа, что уходит в договор.
        """
        try:
            return round(float(self.price_input.value() or 0), 2)
        except (ValueError, TypeError):
            return 0.0

    def _sync_prepayment_percent_from_amount(self) -> None:
        """
        Обновляет ПРОЦЕНТ по текущей сумме предоплаты и итогу договора.

        Вызывается после изменения стоимости: сумму предоплаты оператор
        ввёл руками, поэтому она не трогается — пересчитывается только
        процент.
        """
        if self._prepayment_syncing:
            return

        total = self._current_total_with_vat()
        prepay = float(self.prepayment_amount.value() or 0)

        if total > 0 and prepay > 0:
            percent = round(prepay / total * 100, 2)
        else:
            # Стоимости ещё нет или предоплаты нет — процент нулевой.
            percent = 0.0

        self._prepayment_syncing = True
        try:
            self.prepayment_percent.setValue(percent)
            self._prepayment_percent = percent
        finally:
            self._prepayment_syncing = False

    def _on_prepayment_changed(self) -> None:
        """Оператор изменил СУММУ предоплаты — пересчитать процент."""
        if self._prepayment_syncing:
            return

        self._prepayment_syncing = True
        try:
            total = self._current_total_with_vat()
            prepay = float(self.prepayment_amount.value() or 0)
            if total > 0 and prepay > 0:
                percent = round(prepay / total * 100, 2)
                self.prepayment_percent.setValue(percent)
                self._prepayment_percent = percent
            else:
                self.prepayment_percent.setValue(0)
                self._prepayment_percent = 0.0
        finally:
            self._prepayment_syncing = False

    def _on_prepayment_percent_changed(self) -> None:
        """Оператор изменил ПРОЦЕНТ — пересчитать сумму предоплаты."""
        if self._prepayment_syncing:
            return

        self._prepayment_syncing = True
        try:
            total = self._current_total_with_vat()
            percent = float(self.prepayment_percent.value() or 0)
            if total > 0 and percent > 0:
                amount = round(total * percent / 100, 2)
                self.prepayment_amount.setValue(amount)
                self._prepayment_percent = percent
            else:
                self.prepayment_amount.setValue(0)
                self._prepayment_percent = 0.0
        finally:
            self._prepayment_syncing = False

    def _apply_prepayment(self, data: Dict[str, Any]) -> None:
        """
        Восстанавливает поля предоплаты из данных (распознавание, база).

        Пара «сумма + процент» берётся из данных как есть: сохранённый
        процент приоритетнее пересчитанного, иначе при загрузке из базы он
        «поехал» бы от округления суммы. Если пришла только одна величина —
        вторая считается от неё. Если нет ни одной — поля не трогаются:
        распознавание без блока стоимости не должно стирать введённое.

        Оба поля заполняются под флагом синхронизации, и расчёт делается
        ОДИН раз после него: иначе каждый `setValue` тянул бы за собой слот
        второго поля.
        """
        if "prepayment_amount" not in data and "prepayment_percent" not in data:
            return

        amount = self._prepayment_number(data.get("prepayment_amount"), 100000000)
        percent = self._prepayment_number(data.get("prepayment_percent"), 100)
        if amount is None and percent is None:
            return

        self._prepayment_syncing = True
        try:
            if amount is not None:
                self.prepayment_amount.setValue(amount)
            if percent is not None:
                self.prepayment_percent.setValue(percent)
        finally:
            self._prepayment_syncing = False

        if amount is not None and percent is None:
            # Процента в данных нет — считаем его по сумме.
            self._on_prepayment_changed()
        elif percent is not None and amount is None:
            # Суммы в данных нет — считаем её по проценту.
            self._on_prepayment_percent_changed()
        else:
            # Обе величины пришли из данных: сохранённый процент
            # приоритетнее пересчитанного.
            self._prepayment_percent = percent

    @staticmethod
    def _prepayment_number(value: Any, limit: float) -> Optional[float]:
        """
        Число из значения поля предоплаты; None — если это не число.

        Значения вне допустимых границ тоже дают None: подставлять
        «предоплату» из мусора нельзя, поле останется как есть.
        """
        if value is None or value == "":
            return None
        try:
            number = float(value)
        except (ValueError, TypeError):
            return None
        if 0 <= number <= limit:
            return number
        return None

    # ─────────────────────────────────────────────────────────
    # Сбор данных
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Условия договора одним словарём.

        Форма и ставка НДС уходят как есть (`entity_type`, `vat_rate`), а
        `carrier_type` ВЫЧИСЛЯЕТСЯ из них: по нему генератор выбирает бланк
        (ООО → shablon_ooo.docx, ИП с НДС → shablon_ip_with_vat.docx,
        ИП без НДС → shablon_ip_without_vat.docx).

        «Стоимость» — ИТОГ договора, поэтому `price_with_vat` — это ровно
        введённое число, а `price_without_vat` и `vat_amount` посчитаны из
        него тем же правилом, что и в генераторе («НДС в том числе»).
        `price_input` остаётся для распознавания и ручного ввода: под этим
        именем сумма приходит из модели.
        """
        vat_rate_text = self.current_vat_rate()
        vat_rate_num = self.current_vat_rate_number()

        try:
            payment_days = int(self.payment_days.text().strip() or 0)
        except (ValueError, TypeError):
            payment_days = 0

        try:
            price_with_vat = round(float(self.price_input.value() or 0), 2)
        except (ValueError, TypeError):
            price_with_vat = 0.0

        vat = compute_vat(price_with_vat, vat_rate_text)

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

        try:
            prepay_amount = float(self.prepayment_amount.value() or 0)
        except (ValueError, TypeError):
            prepay_amount = 0.0

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
            "entity_type": self.current_entity_type(),
            "carrier_type": self.computed_carrier_type(),
            "vat_rate": vat_rate_text,
            "vat_rate_num": vat_rate_num,
            "price_input": self.price_input.value(),
            "price_with_vat": vat["sum_total"],
            "price_without_vat": vat["sum_wo_nds"],
            "vat_amount": vat["sum_nds"],
            "payment_days": payment_days,
            "prepayment_amount": prepay_amount,
            "prepayment_percent": getattr(self, "_prepayment_percent", 0.0),
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

        # ── Форма и ставка НДС ──
        self._apply_party_and_rate(data)

        # ── Стоимость ──
        # Значения по умолчанию здесь не подставляются: иначе распознавание
        # без блока «contract» вернуло бы НДС и срок оплаты к 22% и 10 дням.
        # Порядок источников:
        #   1) `price_with_vat` — ИТОГ (так его отдаёт эта же вкладка и
        #      хранит база): он и есть та сумма, которую вводит оператор;
        #   2) `price_input` — сумма от распознавания или ручного ввода;
        #   3) только `price_without_vat` (записи, сохранённые до перехода на
        #      «НДС в том числе») — итог восстанавливается умножением базы на
        #      (1 + ставка/100), то есть ровно так, как он считался раньше.
        price_total = data.get("price_with_vat")
        if price_total in (None, ""):
            price_total = data.get("price_input")
        if price_total in (None, ""):
            price_base = data.get("price_without_vat")
            if price_base not in (None, ""):
                try:
                    price_total = total_from_base(
                        float(price_base), self.current_vat_rate()
                    )
                except (ValueError, TypeError):
                    price_total = None
                    logger.warning("Стоимость без НДС в данных не число — поле не тронуто")
        if price_total not in (None, ""):
            try:
                self.price_input.setValue(float(price_total))
            except (ValueError, TypeError):
                logger.warning("Стоимость в данных не число — поле не тронуто")

        payment_days = data.get("payment_days", "")
        if payment_days:
            self.payment_days.setText(str(payment_days))

        # ── Предоплата ──
        # Поля восстанавливаются парой: если в данных есть и сумма, и процент,
        # берётся СОХРАНЁННЫЙ процент (иначе при загрузке из базы он «поехал»
        # бы от округления суммы). Если есть только сумма — процент
        # считается от неё, если только процент — по нему считается сумма.
        # Заполнение идёт ПОСЛЕДНИМ, после _calculate_price() ниже: иначе
        # пересчёт стоимости переписал бы восстановленный процент.
        prepayment_from_data = data

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

        # Предоплата — последней: _calculate_price() обновляет процент по
        # сумме, а из данных процент мог прийти своим (см. выше).
        self._apply_prepayment(prepayment_from_data)

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

        self.entity_type.setCurrentText(DEFAULT_ENTITY_TYPE)
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)

        # Стоимость возвращается к ПУСТОМУ значению (0), а не к 400 000 ₽:
        # подстановка выглядела как ввод оператора (ШАГ FIX-6, часть C).
        self.price_input.setValue(0)
        self.payment_days.setText("10")
        # Предоплата: оба поля к нулю. Значения ставятся под флагом
        # синхронизации, чтобы setValue не тянул за собой встречный расчёт.
        self._prepayment_syncing = True
        try:
            self.prepayment_amount.setValue(0)
            self.prepayment_percent.setValue(0)
        finally:
            self._prepayment_syncing = False
        self._prepayment_percent = 0.0
        self.special_conditions.clear()

        self._calculate_price()
        self.recognition_panel.clear()

        # Отправляем сигналы после очистки
        self.loadings_changed.emit()
        self.unloadings_changed.emit()

        logger.debug("Поля договора очищены")
