#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Груз» окна типа «Хавалы» (ЭТАП 3.1.E.B.2).

Груз этой заявки — перевозимые автомобили: в бланке это таблица, где одна
строка = одна машина. Строк данных в бланке ровно 10, поэтому больше вкладка
не создаёт (MAX_VEHICLES в ui/windows/havaly/data.py).

Ровно ПЯТЬ колонок машины — как в схеме промпта (массив "vehicles":
vin, brand, model, dealer, dealer_code). Марка и модель в бланке лежат
в РАЗНЫХ колонках, поэтому и здесь это два разных поля, а не «Марка, модель».
Дилера и код дилера в заявках других типов нет — это особенность Хавалов.

Номер лота стоит отдельным полем над таблицей, а не колонкой в каждой
строке. В бланке это колонка «Номер Лота», но значение у неё ОДНО на всю
заявку (промпт: общие сведения повторяются в каждой строке и относятся ко
всей заявке — бери их один раз): десять одинаковых ячеек на вкладке
означали бы десять мест, где значение может разойтись. То же поле есть на
вкладке «Заявка», и там оно с приоритетом
(ui/windows/havaly/data.py::_SHARED_ZAYAVKA_FIELDS).

Автовоз и прицеп в этот список не попадают: это не перевозимые машины,
они заполняются на вкладке «ТС».

Ключи get_data() — lot_number и vehicles (список словарей по пяти полям) —
читает ui/windows/havaly/data.py::_build_vehicles. Имена полей машины
СОВПАДАЮТ с ключами схемы промпта (соглашение ЭТАПА 3.1.E.B.1).
"""

import logging
from typing import Any, Dict, List, Mapping

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QHeaderView, QHBoxLayout, QLabel, QMessageBox,
    QScrollArea, QSpinBox, QStyledItemDelegate, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.havaly.tabs.cargo_tab")

#: Сколько машин помещается в таблицу бланка: строк данных в нём 10.
#: Значение совпадает с ui/windows/havaly/data.py::MAX_VEHICLES.
MAX_CARS = 10

#: Сколько пустых строк показывать при открытии вкладки.
MIN_ROWS = 3

#: Колонки таблицы: № и пять полей машины из схемы промпта.
COL_NUMBER = 0
COL_VIN = 1
COL_BRAND = 2
COL_MODEL = 3
COL_DEALER = 4
COL_DEALER_CODE = 5

#: Заголовки колонок — как в шапке бланка (колонки VIN, Марка, Модель,
#: Дилер, Код дилера). Марка и модель — отдельные колонки, не одна строка.
HEADERS = ["№", "VIN", "Марка", "Модель", "Дилер", "Код дилера"]

#: Поля машины: ровно те ключи, которые читает сборщик
#: (ui/windows/havaly/data.py::VEHICLE_FIELDS), в порядке схемы промпта.
VEHICLE_FIELDS = ("vin", "brand", "model", "dealer", "dealer_code")

#: Колонка таблицы для каждого поля машины.
COLUMN_OF = {
    "vin": COL_VIN,
    "brand": COL_BRAND,
    "model": COL_MODEL,
    "dealer": COL_DEALER,
    "dealer_code": COL_DEALER_CODE,
}


class _NumberDelegate(QStyledItemDelegate):
    """Ячейка «№» — счётчик строки: редактируется только стрелками."""

    def createEditor(self, parent, option, index):
        spin = QSpinBox(parent)
        spin.setRange(1, MAX_CARS)
        return spin


class CargoTab(TabMixin, QWidget):
    """Перевозимые автомобили: пять полей на машину, до 10 штук."""

    # Сигнал для передачи текста в окно на распознавание
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    #: Сколько машин помещается в бланк (значение — из MAX_CARS модуля:
    #: столько же строк у генератора и в сборщике, сверяется тестом).
    MAX_CARS = MAX_CARS

    #: Поля машины: ровно те ключи, которые читает сборщик (VEHICLE_FIELDS
    #: модуля), в порядке схемы промпта. Марка и модель — разные поля.
    VEHICLE_FIELDS = VEHICLE_FIELDS

    def __init__(self):
        super().__init__()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)

        # ── Панель распознавания ──
        self.recognition_panel = RecognitionPanel(
            placeholder="Вставьте текст с машинами (VIN, марка, модель, дилер)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Заявка»: номер лота, общий для всей таблицы ──
        zayavka_group, zayavka_layout = theme.section_box("Заявка")

        self.lot_number = PasteableLineEdit("ЛОТ-2026-001")
        self.lot_number.set_required(True)
        zayavka_layout.addRow(
            theme.required_label("Номер лота"), self.lot_number
        )

        layout.addWidget(zayavka_group)

        # ── Таблица машин ──
        hint = QLabel(f"Перевозимые машины (до {MAX_CARS}):")
        hint.setObjectName("sectionHeading")
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        self.btn_add_vehicle = theme.secondary_button(
            "Добавить машину", tooltip="Добавить строку с автомобилем"
        )
        self.btn_add_vehicle.clicked.connect(self._on_add_vehicle)
        buttons.addWidget(self.btn_add_vehicle)

        self.btn_remove_vehicle = theme.danger_button(
            "Удалить машину", tooltip="Удалить выбранную строку"
        )
        self.btn_remove_vehicle.clicked.connect(self._on_remove_vehicle)
        buttons.addWidget(self.btn_remove_vehicle)

        buttons.addStretch()
        layout.addLayout(buttons)

        self.vehicles_table = QTableWidget(MIN_ROWS, len(HEADERS))
        self.vehicles_table.setHorizontalHeaderLabels(HEADERS)
        self.vehicles_table.horizontalHeader().setSectionResizeMode(
            COL_NUMBER, QHeaderView.ResizeToContents
        )
        self.vehicles_table.horizontalHeader().setSectionResizeMode(
            COL_VIN, QHeaderView.ResizeToContents
        )
        for column in (COL_BRAND, COL_MODEL, COL_DEALER, COL_DEALER_CODE):
            self.vehicles_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.Stretch
            )
        self.vehicles_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.vehicles_table.setItemDelegateForColumn(
            COL_NUMBER, _NumberDelegate(self.vehicles_table)
        )
        self.vehicles_table.setMinimumHeight(220)
        for row in range(MIN_ROWS):
            self._init_row(row)
        layout.addWidget(self.vehicles_table)

        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Хавалы CargoTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Строки таблицы
    # ─────────────────────────────────────────────────────────

    def _init_row(self, row: int) -> None:
        """Пустая строка: номер строки и пустые поля машины."""
        self.vehicles_table.setItem(row, COL_NUMBER, self._number_item(row))
        for column in COLUMN_OF.values():
            self.vehicles_table.setItem(row, column, QTableWidgetItem(""))

    @staticmethod
    def _number_item(row: int) -> QTableWidgetItem:
        """Ячейка «№»: по центру и без ручного редактирования."""
        item = QTableWidgetItem(str(row + 1))
        item.setTextAlignment(Qt.AlignCenter)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        return item

    @staticmethod
    def _cell_text(table: QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        return item.text().strip() if item else ""

    def _renumber_rows(self) -> None:
        """Обновляет номера строк после добавления или удаления."""
        for row in range(self.vehicles_table.rowCount()):
            item = self.vehicles_table.item(row, COL_NUMBER)
            if item is None:
                self.vehicles_table.setItem(row, COL_NUMBER, self._number_item(row))
            else:
                item.setText(str(row + 1))

    def _on_add_vehicle(self) -> None:
        """Добавляет строку; сверх 10 машин не пускает."""
        row_count = self.vehicles_table.rowCount()
        if row_count >= MAX_CARS:
            logger.info("Хавалы: машин уже %s — больше не помещается", row_count)
            QMessageBox.warning(
                self, "Ограничение",
                f"В бланк помещается не больше {MAX_CARS} машин.",
            )
            return

        self.vehicles_table.insertRow(row_count)
        self._init_row(row_count)
        logger.debug("Хавалы: добавлена строка машины %s", row_count + 1)

    def _on_remove_vehicle(self) -> None:
        """Удаляет выбранную строку и перенумеровывает оставшиеся."""
        row = self.vehicles_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Удаление", "Выберите строку для удаления.")
            return

        self.vehicles_table.removeRow(row)
        self._renumber_rows()
        logger.debug("Хавалы: удалена строка машины %s", row + 1)

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def _read_vehicles(self) -> List[Dict[str, str]]:
        """
        Заполненные машины таблицы — по пяти полям схемы промпта.

        Строка без единого заполненного поля машиной не считается: в бланке
        это была бы пустая строка таблицы (правило промпта — такую строку
        пропускать). VIN при этом может быть пустым: «Vin по факту погрузки»
        в заявках Хавалов обычное дело, машину выдают марка и модель.
        """
        vehicles: List[Dict[str, str]] = []
        for row in range(self.vehicles_table.rowCount()):
            vehicle = {
                field: self._cell_text(self.vehicles_table, row, column)
                for field, column in COLUMN_OF.items()
            }
            if not any(vehicle.values()):
                continue
            vehicles.append(vehicle)
        return vehicles

    def get_data(self) -> Dict[str, Any]:
        """
        Номер лота и машины (ключи — как ждёт сборка данных).

        Машины отдаются списком словарей под ключом vehicles: сборщик читает
        его из вкладки «Груз» и раскладывает в массив "vehicles" схемы
        промпта, отбрасывая лишние (сверх 10) — здесь их и не бывает.
        """
        return {
            "lot_number": self.lot_number.text().strip(),
            "vehicles": self._read_vehicles(),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет номер лота и таблицу машин.

        Пустые и незаданные значения не трогают уже введённое: частичное
        распознавание не должно стирать форму. Таблица перерисовывается,
        только если пришёл непустой список машин; лишние (сверх 10)
        отбрасываются без диалога — заполнение не должно останавливать окно.
        """
        if not data:
            return

        lot_number = str(data.get("lot_number") or "").strip()
        if lot_number:
            self.lot_number.setText(lot_number)

        vehicles = data.get("vehicles")
        if isinstance(vehicles, (list, tuple)):
            rows = [item for item in vehicles if isinstance(item, Mapping)]
            if rows:
                self._fill_vehicles(rows)

        logger.info("Хавалы: данные груза заполнены")

    def _fill_vehicles(self, vehicles: List[Mapping[str, Any]]) -> None:
        """
        Перерисовывает таблицу машин по списку из данных.

        Строк в таблице — по числу машин, но не меньше MIN_ROWS: распозналась
        одна машина, а дальше пользователь добавит ещё, и пустые строки для
        этого уже должны быть на месте.
        """
        if len(vehicles) > MAX_CARS:
            logger.warning(
                "Хавалы: машин %s, в бланк помещается %s — лишние не выводятся",
                len(vehicles), MAX_CARS,
            )
            vehicles = vehicles[:MAX_CARS]

        # Перерисовка — без сигналов: они не нужны, а лишние срабатывания
        # обработчиков ячеек только мешают.
        self.vehicles_table.blockSignals(True)
        try:
            self.vehicles_table.setRowCount(0)
            self.vehicles_table.setRowCount(max(len(vehicles), MIN_ROWS))
            for row in range(self.vehicles_table.rowCount()):
                self._init_row(row)
            for row, vehicle in enumerate(vehicles):
                for field, column in COLUMN_OF.items():
                    self.vehicles_table.setItem(
                        row, column,
                        QTableWidgetItem(str(vehicle.get(field) or "")),
                    )
            self.vehicles_table.clearSelection()
        finally:
            self.vehicles_table.blockSignals(False)

        logger.info("Хавалы: машины заполнены (строк: %s)", len(vehicles))

    def clear(self) -> None:
        """Очищает номер лота и таблицу, оставляя MIN_ROWS пустых строк."""
        self.lot_number.clear()

        self.vehicles_table.blockSignals(True)
        try:
            self.vehicles_table.setRowCount(0)
            self.vehicles_table.setRowCount(MIN_ROWS)
            for row in range(MIN_ROWS):
                self._init_row(row)
            self.vehicles_table.clearSelection()
        finally:
            self.vehicles_table.blockSignals(False)

        self.recognition_panel.clear()

        logger.debug("Хавалы: таблица груза очищена")


__all__ = [
    "CargoTab",
    "MAX_CARS",
    "MIN_ROWS",
    "HEADERS",
    "VEHICLE_FIELDS",
    "COLUMN_OF",
]
