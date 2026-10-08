#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Маршрут» окна типа «Логистикс Рус» (ЭТАП 3.1.C.B.2, FIX-2.2, FIX-3).

Маршрут этой заявки — направление, точки погрузки и выгрузки и общий план
по каждой стороне. В разделе 1 заявки грузоотправитель ОДИН (обычно
ООО «ВОТУР МОТОР РУС»), а адресов погрузки у него может быть несколько —
до 10. Поэтому на вкладке одно поле «Грузоотправитель» и таблица адресов
погрузки с одной колонкой. Раздел 2 не менялся: до 10 пар
«грузополучатель + адрес выгрузки».

Образец таблицы с грузополучателями — ui/tabs/contract_tab.py
(unloadings_table): до 10 блоков, кнопки «Добавить» / «Удалить». Пустой
таблица тоже может быть: удалить разрешено и последнюю строку (ШАГ FIX-5),
а о пустом разделе заявки скажет валидатор типа.

Отличие от ui/windows/formika/tabs/route_tab.py: у Формики точек ровно две
(адрес погрузки и адрес выгрузки отдельными полями), здесь их списки —
в бланке Логистикс Рус печатаются строка «Грузоотправитель: …», строки
«Адрес погрузки №N: …» и блоки «Грузополучатель №N: …».

Ключи get_data() — route, shipper_name, loading_addresses, consignees,
unloading_date, unloading_time_from / _to — читает
ui/windows/logistiks_rus/data.py::_build_route. Дата и окно времени у точек
маршрута не вводятся: в бланке они печатаются общей строкой плана, поэтому
сборка сама подставляет их в каждую точку.

План ПОГРУЗКИ (loading_date, loading_time_from / _to) на этой вкладке
отсутствует: шагом FIX-3 он переехал на вкладку «Заказчик» — дата и время
подачи ТС относятся к заявке в целом. Здесь остался только план выгрузки.

Кнопки «Из справочника» есть у обеих таблиц: у адресов погрузки адрес
берётся из справочника как есть, у грузополучателей оттуда приходят и
наименование (графа «Юр. Лицо»), и адрес выгрузки (графа «Адрес доставки
автомобилей»).
"""

import logging
from typing import Any, Dict, List, Mapping

from PyQt5.QtCore import QDate, Qt, QTime, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QDateEdit, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QMessageBox, QScrollArea, QTableWidget,
    QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableDateEdit, PasteableLineEdit, RecognitionPanel
from ui.widgets.table_helpers import (
    MODE_CONTENTS, MODE_STRETCH,
    install_tooltip_on_table, make_table_expandable, setup_point_table,
)

logger = logging.getLogger("ui.windows.logistiks_rus.tabs.route_tab")

#: Сколько адресов погрузки и блоков грузополучателей в бланке.
#: Значение совпадает с ui/windows/logistiks_rus/data.py::MAX_POINTS.
MAX_POINTS = 10

#: Сколько строк показывать при открытии вкладки. Это НЕ минимум: строки
#: можно удалить все — пустая таблица норма (ШАГ FIX-5), а пустой раздел
#: заявки поймает валидатор типа.
MIN_ROWS = 1

#: Колонки таблицы грузополучателей: наименование и адрес.
COL_NAME = 0
COL_ADDRESS = 1

#: Колонка таблицы адресов погрузки — адрес (наименования у неё нет:
#: грузоотправитель один и стоит отдельным полем).
COL_LOADING_ADDRESS = 0

#: Режимы и ширины колонок таблиц точек (ШАГ FIX-5): наименование — по
#: содержимому, адрес — главная колонка, тянется по ширине таблицы.
LOADING_ADDRESSES_COLUMNS_CONFIG = ((COL_LOADING_ADDRESS, MODE_STRETCH, 0),)
CONSIGNEES_COLUMNS_CONFIG = (
    (COL_NAME, MODE_CONTENTS, 0),
    (COL_ADDRESS, MODE_STRETCH, 0),
)

#: Нижние границы ширин: адрес обрезался в узкой колонке.
LOADING_ADDRESSES_COLUMN_MINIMUMS = {COL_LOADING_ADDRESS: 150}
CONSIGNEES_COLUMN_MINIMUMS = {COL_NAME: 100, COL_ADDRESS: 150}

#: Нижняя граница ВЫСОТЫ таблиц точек: шапка (около 21) плюс две полные
#: строки по 40 пикселей. Было 80 — видно было полторы строки. Верхняя
#: граница у таблиц своя (160): выше неё растягиваться некуда.
POINT_TABLE_MIN_HEIGHT = 120
POINT_TABLE_MAX_HEIGHT = 160

#: Ключи QSettings для раскладки колонок (у таблиц она своя).
LOADING_ADDRESSES_WIDTHS_KEY = "ui/logistiks_rus/loading_addresses_columns"
CONSIGNEES_WIDTHS_KEY = "ui/logistiks_rus/consignees_columns"

#: Грузоотправитель по умолчанию: в заявках этого типа он один и тот же.
DEFAULT_SHIPPER_NAME = "ООО «ВОТУР МОТОР РУС»"

#: Окно времени погрузки по умолчанию — как на вкладке условий договора.
DEFAULT_LOADING_TIME_FROM = QTime(8, 0)
DEFAULT_LOADING_TIME_TO = QTime(20, 0)

#: Окно времени выгрузки по умолчанию.
DEFAULT_UNLOADING_TIME_FROM = QTime(9, 0)
DEFAULT_UNLOADING_TIME_TO = QTime(18, 0)

#: На сколько дней вперёд показывать плановую дату выгрузки.
DEFAULT_UNLOADING_DAYS = 3


class NoWheelDateEdit(QDateEdit):
    """Дата с календарём без случайного изменения колёсиком мыши."""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelTimeEdit(QTimeEdit):
    """Время, которое меняется только при явном редактировании."""

    def wheelEvent(self, event):
        event.ignore()


class RouteTab(TabMixin, QWidget):
    """Направление, точки маршрута и план погрузки/выгрузки."""

    # Сигнал для передачи текста в окно на распознавание
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    def __init__(self):
        super().__init__()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)

        # ── Панель распознавания ──
        self.recognition_panel = RecognitionPanel(
            placeholder="Вставьте текст с маршрутом, грузоотправителем и грузополучателями..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Маршрут» ──
        route_group, route_layout = theme.section_box("Маршрут")

        self.route = PasteableLineEdit("Москва - Казань")
        route_layout.addRow("Направление", self.route)

        layout.addWidget(route_group)

        # ── Грузоотправитель и адреса погрузки ──
        # В бланке раздел 1 — ОДИН грузоотправитель и до 10 нумерованных
        # адресов погрузки, поэтому таблица адресов одноколоночная.
        shippers_group = QGroupBox(f"Погрузка (до {MAX_POINTS} адресов)")
        shippers_layout = QVBoxLayout(shippers_group)

        shipper_form = QFormLayout()
        shipper_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # Первый аргумент PasteableLineEdit — ПОДСКАЗКА (placeholder), а не
        # значение: постоянного грузоотправителя кладём в поле явно, как
        # заказчика на вкладке «Заказчик» (customer_tab.py).
        self.shipper_name = PasteableLineEdit(DEFAULT_SHIPPER_NAME)
        self.shipper_name.setText(DEFAULT_SHIPPER_NAME)
        self.shipper_name.set_required(True)
        shipper_form.addRow(
            theme.required_label("Грузоотправитель"), self.shipper_name
        )
        shippers_layout.addLayout(shipper_form)

        shippers_buttons = QHBoxLayout()
        self.btn_add_loading_address = theme.secondary_button(
            "Добавить адрес погрузки",
            tooltip="Добавить строку адреса погрузки",
        )
        self.btn_add_loading_address.clicked.connect(self._on_add_loading_address)
        shippers_buttons.addWidget(self.btn_add_loading_address)

        self.btn_remove_loading_address = theme.danger_button(
            "Удалить", tooltip="Удалить выбранный адрес погрузки"
        )
        self.btn_remove_loading_address.clicked.connect(self._on_remove_loading_address)
        shippers_buttons.addWidget(self.btn_remove_loading_address)

        self.btn_book_loading = theme.secondary_button(
            "Из справочника",
            tooltip="Выбрать адрес погрузки из справочника",
        )
        self.btn_book_loading.clicked.connect(self._on_open_book_loading)
        shippers_buttons.addWidget(self.btn_book_loading)

        shippers_buttons.addStretch()
        shippers_layout.addLayout(shippers_buttons)

        self.loading_addresses_table = self._create_loading_addresses_table()
        shippers_layout.addWidget(self.loading_addresses_table)

        layout.addWidget(shippers_group)

        # ── Таблица «Грузополучатели» ──
        consignees_group = QGroupBox(f"Грузополучатели (до {MAX_POINTS})")
        consignees_layout = QVBoxLayout(consignees_group)

        consignees_buttons = QHBoxLayout()
        self.btn_add_consignee = theme.secondary_button(
            "Добавить грузополучателя", tooltip="Добавить строку грузополучателя"
        )
        self.btn_add_consignee.clicked.connect(self._on_add_consignee)
        consignees_buttons.addWidget(self.btn_add_consignee)

        self.btn_remove_consignee = theme.danger_button(
            "Удалить", tooltip="Удалить выбранную строку"
        )
        self.btn_remove_consignee.clicked.connect(self._on_remove_consignee)
        consignees_buttons.addWidget(self.btn_remove_consignee)

        self.btn_book_consignee = theme.secondary_button(
            "Из справочника",
            tooltip="Выбрать грузополучателя из справочника салонов",
        )
        self.btn_book_consignee.clicked.connect(self._on_open_book_consignee)
        consignees_buttons.addWidget(self.btn_book_consignee)

        consignees_buttons.addStretch()
        consignees_layout.addLayout(consignees_buttons)

        self.consignees_table = self._create_points_table()
        consignees_layout.addWidget(self.consignees_table)

        layout.addWidget(consignees_group)

        # ── Группа «План выгрузки» ──
        # План ПОГРУЗКИ (дата и время подачи ТС) переехал на вкладку
        # «Заказчик» шагом FIX-3: он относится к заявке в целом, а не к
        # точкам доставки. Здесь остался только план выгрузки.
        unloading_group, unloading_layout = theme.section_box("План выгрузки")

        self.unloading_date = self._make_date_edit(
            QDate.currentDate().addDays(DEFAULT_UNLOADING_DAYS)
        )
        unloading_layout.addRow("Плановая дата выгрузки", self.unloading_date)

        unloading_time_layout = QHBoxLayout()
        self.unloading_time_from = self._make_time_edit(DEFAULT_UNLOADING_TIME_FROM)
        unloading_time_layout.addWidget(QLabel("с"))
        unloading_time_layout.addWidget(self.unloading_time_from)
        self.unloading_time_to = self._make_time_edit(DEFAULT_UNLOADING_TIME_TO)
        unloading_time_layout.addWidget(QLabel("по"))
        unloading_time_layout.addWidget(self.unloading_time_to)
        unloading_time_layout.addStretch()
        unloading_layout.addRow("Время выгрузки", unloading_time_layout)

        layout.addWidget(unloading_group)

        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Логистикс Рус RouteTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Виджеты вкладки
    # ─────────────────────────────────────────────────────────

    def _create_loading_addresses_table(self) -> QTableWidget:
        """Таблица адресов погрузки: одна колонка «Адрес погрузки»."""
        table = QTableWidget(MIN_ROWS, 1)
        table.setHorizontalHeaderLabels(["Адрес погрузки"])
        setup_point_table(
            table,
            LOADING_ADDRESSES_COLUMNS_CONFIG,
            storage_key=LOADING_ADDRESSES_WIDTHS_KEY,
            minimums=LOADING_ADDRESSES_COLUMN_MINIMUMS,
        )
        install_tooltip_on_table(table)
        make_table_expandable(table)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setMinimumHeight(POINT_TABLE_MIN_HEIGHT)
        table.setMaximumHeight(POINT_TABLE_MAX_HEIGHT)
        for row in range(MIN_ROWS):
            self._init_loading_address_row(table, row)
        return table

    def _create_points_table(self) -> QTableWidget:
        """Пустая таблица точек маршрута: наименование и адрес."""
        table = QTableWidget(MIN_ROWS, 2)
        table.setHorizontalHeaderLabels(["Наименование", "Адрес"])
        setup_point_table(
            table,
            CONSIGNEES_COLUMNS_CONFIG,
            storage_key=CONSIGNEES_WIDTHS_KEY,
            minimums=CONSIGNEES_COLUMN_MINIMUMS,
        )
        install_tooltip_on_table(table)
        make_table_expandable(table)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setMinimumHeight(POINT_TABLE_MIN_HEIGHT)
        table.setMaximumHeight(POINT_TABLE_MAX_HEIGHT)
        for row in range(MIN_ROWS):
            self._init_point_row(table, row)
        return table

    @staticmethod
    def _make_date_edit(value: QDate) -> PasteableDateEdit:
        """Дата с календарём и кнопкой вставки из буфера."""
        date_edit = NoWheelDateEdit()
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setCalendarPopup(True)
        date_edit.setDate(value)
        return PasteableDateEdit(date_edit)

    @staticmethod
    def _make_time_edit(value: QTime) -> NoWheelTimeEdit:
        """Время в формате «HH:mm» без случайной прокрутки."""
        time_edit = NoWheelTimeEdit()
        time_edit.setDisplayFormat("HH:mm")
        time_edit.setTime(value)
        time_edit.setFixedWidth(90)
        return time_edit

    @staticmethod
    def _init_loading_address_row(table: QTableWidget, row: int) -> None:
        """Пустая строка адреса погрузки."""
        table.setItem(row, COL_LOADING_ADDRESS, QTableWidgetItem(""))

    @staticmethod
    def _init_point_row(table: QTableWidget, row: int) -> None:
        """Пустая строка точки маршрута."""
        table.setItem(row, COL_NAME, QTableWidgetItem(""))
        table.setItem(row, COL_ADDRESS, QTableWidgetItem(""))

    @staticmethod
    def _cell_text(table: QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        return item.text().strip() if item else ""

    # ─────────────────────────────────────────────────────────
    # Строки таблиц адресов погрузки и грузополучателей
    # ─────────────────────────────────────────────────────────

    def _on_add_loading_address(self) -> None:
        """Добавляет адрес погрузки; сверх 10 не пускает."""
        self._add_point_row(self.loading_addresses_table, "адрес погрузки")

    def _on_remove_loading_address(self) -> None:
        """Удаляет выбранный адрес погрузки."""
        self._remove_point_row(self.loading_addresses_table, "адрес погрузки")

    def _on_add_consignee(self) -> None:
        """Добавляет строку грузополучателя; сверх 10 не пускает."""
        self._add_point_row(self.consignees_table, "грузополучателя")

    def _on_remove_consignee(self) -> None:
        """Удаляет выбранную строку грузополучателя."""
        self._remove_point_row(self.consignees_table, "грузополучатель")

    def _is_loading_addresses_table(self, table: QTableWidget) -> bool:
        """True, если таблица — список адресов погрузки (одна колонка)."""
        return table is self.loading_addresses_table

    def _add_point_row(self, table: QTableWidget, title: str) -> None:
        """Общее добавление строки для обеих таблиц (в connect — без lambda)."""
        row_count = table.rowCount()
        if row_count >= MAX_POINTS:
            logger.info("Логистикс Рус: точек уже %s — больше не помещается", row_count)
            QMessageBox.warning(
                self, "Ограничение",
                f"В бланк помещается не больше {MAX_POINTS} блоков: "
                f"добавить ещё один {title} нельзя.",
            )
            return

        table.insertRow(row_count)
        if self._is_loading_addresses_table(table):
            self._init_loading_address_row(table, row_count)
        else:
            self._init_point_row(table, row_count)
        logger.debug("Логистикс Рус: добавлена строка (%s)", title)

    def _remove_point_row(self, table: QTableWidget, title: str) -> None:
        """
        Общее удаление строки для обеих таблиц.

        Последнюю строку удалить можно (ШАГ FIX-5): пустая таблица — норма,
        о пустом разделе заявки скажет валидатор типа.
        """
        row = table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Удаление", "Выберите строку для удаления.")
            return

        table.removeRow(row)
        logger.debug("Логистикс Рус: удалена строка (%s)", title)

    def _row_is_empty(self, table: QTableWidget, row: int) -> bool:
        """True, если в строке нет ни одного значения."""
        columns = 1 if self._is_loading_addresses_table(table) else 2
        return all(
            not self._cell_text(table, row, column)
            for column in range(columns)
        )

    def _resolve_target_row(self, table: QTableWidget) -> int:
        """
        Номер строки, в которую класть выбранное из справочника значение.

        Порядок выбора:
          1) текущая строка, если она пуста;
          2) первая пустая из существующих;
          3) новая строка, если не превышен MAX_POINTS;
          4) иначе — предупреждение и -1.
        """
        row = table.currentRow()
        if row >= 0 and self._row_is_empty(table, row):
            return row

        for index in range(table.rowCount()):
            if self._row_is_empty(table, index):
                return index

        if table.rowCount() >= MAX_POINTS:
            QMessageBox.warning(
                self, "Ограничение",
                f"В бланк помещается не больше {MAX_POINTS} строк: "
                f"свободной строки нет, удалите лишнюю.",
            )
            return -1

        index = table.rowCount()
        table.insertRow(index)
        if self._is_loading_addresses_table(table):
            self._init_loading_address_row(table, index)
        else:
            self._init_point_row(table, index)
        return index

    # ─────────────────────────────────────────────────────────
    # Заполнение из справочника
    # ─────────────────────────────────────────────────────────

    def _on_open_book_loading(self) -> None:
        """Адрес погрузки из справочника адресов."""
        from ui.address_book_dialog import AddressBookDialog

        dialog = AddressBookDialog("loading", parent=self)
        if not dialog.exec_():
            return

        selected = dialog.selected_address or {}
        address = str(selected.get("address") or "").strip()
        if not address:
            return

        table = self.loading_addresses_table
        row = self._resolve_target_row(table)
        if row < 0:
            return

        table.blockSignals(True)
        try:
            table.setItem(row, COL_LOADING_ADDRESS, QTableWidgetItem(address))
        finally:
            table.blockSignals(False)

        table.selectRow(row)

    def _on_open_book_consignee(self) -> None:
        """
        Грузополучатель из справочника салонов.

        Наименование берётся из графы «Юр. Лицо» (salon_name), адрес — из
        графы «Адрес доставки автомобилей» (address): обе колонки приходят
        из справочника салонов (см. ui/address_book_dialog.py).
        """
        from ui.address_book_dialog import AddressBookDialog

        dialog = AddressBookDialog("unloading", parent=self)
        if not dialog.exec_():
            return

        selected = dialog.selected_address or {}
        name = str(selected.get("salon_name") or "").strip()
        address = str(selected.get("address") or "").strip()

        table = self.consignees_table
        row = self._resolve_target_row(table)
        if row < 0:
            return

        table.blockSignals(True)
        try:
            table.setItem(row, COL_NAME, QTableWidgetItem(name))
            table.setItem(row, COL_ADDRESS, QTableWidgetItem(address))
        finally:
            table.blockSignals(False)

        table.selectRow(row)

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Маршрут, грузоотправитель, адреса погрузки, грузополучатели и план
        ВЫГРУЗКИ (даты — ISO, время — «HH:mm»).

        Грузоотправитель отдаётся одним полем, адреса погрузки — списком
        строк; грузополучатели — массивом consignees по образцу
        ui/tabs/contract_tab.py. Пустые строки таблиц в данные не попадают.

        Плана погрузки здесь нет: дата и время подачи ТС переехали на вкладку
        «Заказчик» шагом FIX-3 (см. customer_tab.py) — ключи loading_date /
        loading_time_from / loading_time_to отдаёт она.
        """
        return {
            "route": self.route.text().strip(),
            "shipper_name": self.shipper_name.text().strip(),
            "loading_addresses": self._read_loading_addresses(),
            "consignees": self._read_points(self.consignees_table),
            "unloading_date": self.unloading_date.date().toString("yyyy-MM-dd"),
            "unloading_time_from": self.unloading_time_from.time().toString("HH:mm"),
            "unloading_time_to": self.unloading_time_to.time().toString("HH:mm"),
        }

    def _read_loading_addresses(self) -> List[str]:
        """
        Заполненные адреса погрузки — списком строк.

        Пустая строка таблицы адресом не считается: иначе в бланк попали бы
        пустые строки «Адрес погрузки №N:». Нумерация в бланке идёт по
        порядку списка, поэтому пустые строки не должны «съедать» номер.
        """
        addresses: List[str] = []
        table = self.loading_addresses_table
        for row in range(table.rowCount()):
            address = self._cell_text(table, row, COL_LOADING_ADDRESS)
            if address:
                addresses.append(address)
        return addresses

    @staticmethod
    def _read_points(table: QTableWidget) -> List[Dict[str, str]]:
        """
        Заполненные точки таблицы: [{"name": …, "address": …}, …].

        Строка, где пусто и наименование, и адрес, точкой не считается —
        иначе в бланк попали бы пустые блоки.
        """
        points: List[Dict[str, str]] = []
        for row in range(table.rowCount()):
            name = RouteTab._cell_text(table, row, COL_NAME)
            address = RouteTab._cell_text(table, row, COL_ADDRESS)
            if not name and not address:
                continue
            points.append({"name": name, "address": address})
        return points

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет маршрут, грузоотправителя, адреса погрузки, таблицу
        грузополучателей и план погрузки/выгрузки.

        Пустые значения игнорируются — частичное распознавание не должно
        сбрасывать уже введённый маршрут. Список заменяет таблицу целиком
        (как в CargoTab), но пустой список её не трогает: «точек не
        распознано» и «стереть введённое» — разные вещи.

        Старый формат ответа (массив shippers с наименованиями) принимается
        как fallback: имя берётся из первой точки, адреса — из всех.
        """
        if not data:
            return

        route = str(data.get("route") or "").strip()
        if route:
            self.route.setText(route)

        shipper_name = str(data.get("shipper_name") or "").strip()
        loading_addresses = data.get("loading_addresses")

        if not shipper_name and not isinstance(loading_addresses, (list, tuple)):
            # Fallback старого распознавания: массив shippers с именами.
            old_points = [
                item for item in (data.get("shippers") or [])
                if isinstance(item, Mapping)
            ]
            if old_points:
                names = [
                    str(point.get("name") or "").strip()
                    for point in old_points
                    if str(point.get("name") or "").strip()
                ]
                if names:
                    shipper_name = names[0]
                loading_addresses = [
                    str(point.get("address") or "") for point in old_points
                ]

        if shipper_name:
            self.shipper_name.setText(shipper_name)

        self._fill_loading_addresses(loading_addresses)
        self._fill_points(self.consignees_table, data.get("consignees"))

        # План ВЫГРУЗКИ. План погрузки приходит с вкладки «Заказчик» (FIX-3):
        # здесь этих ключей больше нет, и читать их отсюда нечем.
        if data.get("unloading_date"):
            self._set_date(self.unloading_date, data["unloading_date"])

        self._set_time(self.unloading_time_from, data.get("unloading_time_from"))
        self._set_time(self.unloading_time_to, data.get("unloading_time_to"))

        logger.info("Логистикс Рус: данные маршрута заполнены")

    def _fill_loading_addresses(self, items: Any) -> None:
        """
        Перерисовывает таблицу адресов погрузки по списку из данных.

        Лишние адреса (сверх 10) отбрасываются: строк в бланке ровно 10.
        Не список и пустой список оставляют таблицу как есть.
        """
        if not isinstance(items, (list, tuple)):
            return

        addresses = [str(item).strip() for item in items if str(item or "").strip()]
        if not addresses:
            return

        if len(addresses) > MAX_POINTS:
            logger.warning(
                "Логистикс Рус: адресов погрузки %s, в бланк помещается %s — "
                "лишние не выводятся",
                len(addresses), MAX_POINTS,
            )
            addresses = addresses[:MAX_POINTS]

        table = self.loading_addresses_table
        table.blockSignals(True)
        try:
            table.setRowCount(0)
            table.setRowCount(len(addresses))
            for row, address in enumerate(addresses):
                table.setItem(row, COL_LOADING_ADDRESS, QTableWidgetItem(address))
            table.clearSelection()
        finally:
            table.blockSignals(False)

    def _fill_points(self, table: QTableWidget, items: Any) -> None:
        """
        Перерисовывает таблицу точек по списку из данных.

        Лишние точки (сверх 10) отбрасываются: блоков в бланке ровно 10.
        Не список и пустой список оставляют таблицу как есть.
        """
        if not isinstance(items, (list, tuple)):
            return

        points = [item for item in items if isinstance(item, Mapping)]
        if not points:
            return

        if len(points) > MAX_POINTS:
            logger.warning(
                "Логистикс Рус: точек %s, в бланк помещается %s — лишние не выводятся",
                len(points), MAX_POINTS,
            )
            points = points[:MAX_POINTS]

        table.blockSignals(True)
        try:
            table.setRowCount(0)
            table.setRowCount(len(points))
            for row, point in enumerate(points):
                self._init_point_row(table, row)
                table.setItem(
                    row, COL_NAME,
                    QTableWidgetItem(str(point.get("name") or "")),
                )
                table.setItem(
                    row, COL_ADDRESS,
                    QTableWidgetItem(str(point.get("address") or "")),
                )
            table.clearSelection()
        finally:
            table.blockSignals(False)

    @staticmethod
    def _set_time(time_edit: QTimeEdit, value: Any) -> None:
        """Ставит время из строки «HH:mm» (пустое или битое — не трогаем)."""
        text = str(value or "").strip()
        if not text:
            return

        parsed = QTime.fromString(text, "HH:mm")
        if not parsed.isValid():
            logger.warning("Логистикс Рус: время %r не распознано", text[:5])
            return
        time_edit.setTime(parsed)

    def clear(self) -> None:
        """Очищает маршрут и возвращает план ВЫГРУЗКИ к значениям по умолчанию."""
        self.route.clear()
        self.shipper_name.setText(DEFAULT_SHIPPER_NAME)

        self.loading_addresses_table.blockSignals(True)
        try:
            self.loading_addresses_table.setRowCount(0)
            self.loading_addresses_table.setRowCount(MIN_ROWS)
            for row in range(MIN_ROWS):
                self._init_loading_address_row(self.loading_addresses_table, row)
            self.loading_addresses_table.clearSelection()
        finally:
            self.loading_addresses_table.blockSignals(False)

        self.consignees_table.blockSignals(True)
        try:
            self.consignees_table.setRowCount(0)
            self.consignees_table.setRowCount(MIN_ROWS)
            for row in range(MIN_ROWS):
                self._init_point_row(self.consignees_table, row)
            self.consignees_table.clearSelection()
        finally:
            self.consignees_table.blockSignals(False)

        self.unloading_date.setDate(
            QDate.currentDate().addDays(DEFAULT_UNLOADING_DAYS)
        )
        self.unloading_time_from.setTime(DEFAULT_UNLOADING_TIME_FROM)
        self.unloading_time_to.setTime(DEFAULT_UNLOADING_TIME_TO)
        self.recognition_panel.clear()

        logger.debug("Логистикс Рус: поля маршрута очищены")


__all__ = [
    "RouteTab",
    "NoWheelDateEdit",
    "NoWheelTimeEdit",
    "MAX_POINTS",
    "MIN_ROWS",
    "COL_NAME",
    "COL_ADDRESS",
    "COL_LOADING_ADDRESS",
    "LOADING_ADDRESSES_COLUMNS_CONFIG",
    "CONSIGNEES_COLUMNS_CONFIG",
    "LOADING_ADDRESSES_COLUMN_MINIMUMS",
    "CONSIGNEES_COLUMN_MINIMUMS",
    "LOADING_ADDRESSES_WIDTHS_KEY",
    "CONSIGNEES_WIDTHS_KEY",
    "DEFAULT_SHIPPER_NAME",
    "DEFAULT_UNLOADING_TIME_FROM",
    "DEFAULT_UNLOADING_TIME_TO",
    "DEFAULT_UNLOADING_DAYS",
]
