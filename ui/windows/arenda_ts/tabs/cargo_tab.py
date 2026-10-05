#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Груз» окна типа «Разовая аренда» (ЭТАП 3.1.D.B.2).

Груз здесь — перевозимые автомобили: в бланке это таблица п. 3.1, куда
вписывают марку/модель, VIN и точки погрузки и выгрузки каждой машины.
Строк в шаблоне 12, поэтому больше вкладка не создаёт.

Отличие от вкладки «Груз» Логистикс Рус (ui/windows/logistiks_rus/tabs/
cargo_tab.py): там три колонки (№, марка/модель, VIN), здесь пять — у каждой
машины свои точки погрузки и выгрузки, как в бланке аренды. В шапке таблицы
показано, сколько машин заполнено: это поле cargo_count, его читает
ui/windows/arenda_ts/data.py::_build_cargo (счётчик обновляется сам).

Тягач и прицеп в этот список не попадают: они объект аренды и заполняются
на вкладке «ТС» (раздел 2.1 бланка).

Ключи get_data() — cargo_count и vehicles (список словарей
{brand_model, vin, loading_point, unloading_point}) — читает
ui/windows/arenda_ts/data.py::_build_cargo.
"""

import logging
from typing import Any, Dict, List

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QScrollArea, QSpinBox, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import RecognitionPanel

logger = logging.getLogger("ui.windows.arenda_ts.tabs.cargo_tab")

#: Сколько машин помещается в таблицу п. 3.1 бланка.
#: Значение совпадает с ui/windows/arenda_ts/data.py::MAX_CARS.
MAX_CARS = 12

#: Сколько пустых строк показывать при открытии вкладки.
MIN_ROWS = 3

#: Колонки таблицы: №, марка/модель, VIN, точка погрузки, точка выгрузки.
COL_NUMBER = 0
COL_BRAND = 1
COL_VIN = 2
COL_LOADING_POINT = 3
COL_UNLOADING_POINT = 4

#: Заголовки колонок — как в таблице п. 3.1 бланка.
HEADERS = ["№", "Марка, модель", "VIN", "Точка погрузки", "Точка выгрузки"]


class _NumberDelegate(QStyledItemDelegate):
    """Ячейка «№» — счётчик строки: редактируется только стрелками."""

    def createEditor(self, parent, option, index):
        spin = QSpinBox(parent)
        spin.setRange(1, MAX_CARS)
        return spin


class CargoTab(TabMixin, QWidget):
    """Перевозимые автомобили: марка, VIN и точки в строке, до 12 штук."""

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
            placeholder="Вставьте текст с перевозимыми автомобилями (марка, VIN, точки)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Счётчик машин: заполняется сам при изменении таблицы ──
        count_layout = QHBoxLayout()
        count_label = QLabel(f"Перевозимые автомобили (до {MAX_CARS}):")
        count_label.setObjectName("sectionHeading")
        count_layout.addWidget(count_label)

        self.cargo_count = QLineEdit()
        self.cargo_count.setReadOnly(True)
        self.cargo_count.setProperty("readonlyField", True)
        self.cargo_count.setStyleSheet(theme.readonly_field_qss())
        self.cargo_count.setFixedWidth(60)
        self.cargo_count.setToolTip("Сколько машин заполнено — считается по таблице")
        count_layout.addWidget(self.cargo_count)

        count_layout.addWidget(QLabel("машин"))
        count_layout.addStretch()
        layout.addLayout(count_layout)

        # ── Кнопки таблицы ──
        buttons = QHBoxLayout()
        self.btn_add_vehicle = theme.secondary_button(
            "Добавить ТС", tooltip="Добавить строку с автомобилем"
        )
        self.btn_add_vehicle.clicked.connect(self._on_add_vehicle)
        buttons.addWidget(self.btn_add_vehicle)

        self.btn_remove_vehicle = theme.danger_button(
            "Удалить ТС", tooltip="Удалить выбранную строку"
        )
        self.btn_remove_vehicle.clicked.connect(self._on_remove_vehicle)
        buttons.addWidget(self.btn_remove_vehicle)

        buttons.addStretch()
        layout.addLayout(buttons)

        # ── Таблица машин ──
        self.vehicles_table = QTableWidget(MIN_ROWS, len(HEADERS))
        self.vehicles_table.setHorizontalHeaderLabels(HEADERS)
        self.vehicles_table.horizontalHeader().setSectionResizeMode(
            COL_NUMBER, QHeaderView.ResizeToContents
        )
        self.vehicles_table.horizontalHeader().setSectionResizeMode(
            COL_BRAND, QHeaderView.Stretch
        )
        self.vehicles_table.horizontalHeader().setSectionResizeMode(
            COL_VIN, QHeaderView.ResizeToContents
        )
        self.vehicles_table.horizontalHeader().setSectionResizeMode(
            COL_LOADING_POINT, QHeaderView.Stretch
        )
        self.vehicles_table.horizontalHeader().setSectionResizeMode(
            COL_UNLOADING_POINT, QHeaderView.Stretch
        )
        self.vehicles_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.vehicles_table.setItemDelegateForColumn(
            COL_NUMBER, _NumberDelegate(self.vehicles_table)
        )
        self.vehicles_table.setMinimumHeight(200)
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

        # ── Счётчик машин: таблица изменилась — счётчик обновился ──
        # Сигнал берётся у самого QLineEdit: у QLineEdit он есть, а
        # setReadOnly ввод не блокирует — блокируем запись в счётчик ниже.
        self.vehicles_table.itemChanged.connect(self._on_table_changed)
        self.cargo_count.textChanged.connect(self._on_count_changed)
        self._update_count()

        logger.debug("Разовая аренда CargoTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Строки таблицы
    # ─────────────────────────────────────────────────────────

    def _init_row(self, row: int) -> None:
        """Пустая строка: номер строки и пустые поля машины."""
        self.vehicles_table.setItem(row, COL_NUMBER, self._number_item(row))
        for column in (
            COL_BRAND, COL_VIN, COL_LOADING_POINT, COL_UNLOADING_POINT,
        ):
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
        """Добавляет строку; сверх 12 машин не пускает."""
        row_count = self.vehicles_table.rowCount()
        if row_count >= MAX_CARS:
            logger.info(
                "Разовая аренда: машин уже %s — больше не помещается", row_count
            )
            QMessageBox.warning(
                self, "Ограничение",
                f"В бланк помещается не больше {MAX_CARS} автомобилей.",
            )
            return

        self.vehicles_table.insertRow(row_count)
        self._init_row(row_count)
        self._update_count()
        logger.debug("Разовая аренда: добавлена строка автомобиля %s", row_count + 1)

    def _on_remove_vehicle(self) -> None:
        """Удаляет выбранную строку и перенумеровывает оставшиеся."""
        row = self.vehicles_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Удаление", "Выберите строку для удаления.")
            return

        self.vehicles_table.removeRow(row)
        self._renumber_rows()
        self._update_count()
        logger.debug("Разовая аренда: удалена строка автомобиля %s", row + 1)

    # ─────────────────────────────────────────────────────────
    # Счётчик машин
    # ─────────────────────────────────────────────────────────

    def _on_table_changed(self, _item: QTableWidgetItem) -> None:
        """Правка ячейки таблицы — пересчитываем заполненные машины."""
        self._update_count()

    def _on_count_changed(self, text: str) -> None:
        """
        Счётчик только для чтения: правку откатываем.

        Поле readOnly, но вставка из буфера и программный ввод его обходят,
        а показывать в бланке чужое число нельзя — счётчик считается по
        таблице (data.py::_build_cargo берёт его из вкладки как есть).
        """
        expected = str(self._filled_count())
        if text != expected:
            self.cargo_count.setText(expected)

    def _filled_count(self) -> int:
        """Сколько строк таблицы заполнено (машина без марки и VIN — пустая)."""
        return len(self._read_vehicles())

    def _update_count(self) -> None:
        """Пересчитывает и показывает число заполненных машин."""
        count = self._filled_count()
        if self.cargo_count.text() != str(count):
            self.cargo_count.setText(str(count))

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def _read_vehicles(self) -> List[Dict[str, str]]:
        """
        Заполненные машины таблицы.

        Строка без марки и без VIN машиной не считается: в бланке это была бы
        пустая строка таблицы п. 3.1.
        """
        vehicles: List[Dict[str, str]] = []
        for row in range(self.vehicles_table.rowCount()):
            brand_model = self._cell_text(self.vehicles_table, row, COL_BRAND)
            vin = self._cell_text(self.vehicles_table, row, COL_VIN)
            if not brand_model and not vin:
                continue
            vehicles.append({
                "brand_model": brand_model,
                "vin": vin,
                "loading_point": self._cell_text(
                    self.vehicles_table, row, COL_LOADING_POINT
                ),
                "unloading_point": self._cell_text(
                    self.vehicles_table, row, COL_UNLOADING_POINT
                ),
            })
        return vehicles

    def get_data(self) -> Dict[str, Any]:
        """
        Машины таблицы и их количество (ключи — как ждёт сборка данных).

        cargo_count считается по таблице: его обновляет и правка ячеек, и
        добавление с удалением строк.
        """
        vehicles = self._read_vehicles()
        return {"cargo_count": len(vehicles), "vehicles": vehicles}

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет таблицу машин.

        Лишние машины (сверх 12) отбрасываются — в бланк они всё равно не
        помещаются; диалог здесь не показывается: заполнение идёт по кнопке
        или из распознавания и не должно останавливать работу окна. Пустой
        список оставляет таблицу пустой на MIN_ROWS строк.
        """
        if not data:
            return

        vehicles = data.get("vehicles")
        if not isinstance(vehicles, (list, tuple)):
            return

        rows = [item for item in vehicles if isinstance(item, dict)]
        if len(rows) > MAX_CARS:
            logger.warning(
                "Разовая аренда: машин %s, в бланк помещается %s — лишние отброшены",
                len(rows), MAX_CARS,
            )
            rows = rows[:MAX_CARS]

        # Перерисовка таблицы — без сигналов: они не нужны, а счётчик
        # пересчитывается один раз в конце, а не на каждую ячейку.
        self.vehicles_table.blockSignals(True)
        try:
            self.vehicles_table.setRowCount(0)

            if rows:
                self.vehicles_table.setRowCount(len(rows))
                for row, vehicle in enumerate(rows):
                    self._init_row(row)
                    self.vehicles_table.setItem(
                        row, COL_BRAND,
                        QTableWidgetItem(str(vehicle.get("brand_model") or "")),
                    )
                    self.vehicles_table.setItem(
                        row, COL_VIN,
                        QTableWidgetItem(str(vehicle.get("vin") or "")),
                    )
                    self.vehicles_table.setItem(
                        row, COL_LOADING_POINT,
                        QTableWidgetItem(str(vehicle.get("loading_point") or "")),
                    )
                    self.vehicles_table.setItem(
                        row, COL_UNLOADING_POINT,
                        QTableWidgetItem(str(vehicle.get("unloading_point") or "")),
                    )
            else:
                self.vehicles_table.setRowCount(MIN_ROWS)
                for row in range(MIN_ROWS):
                    self._init_row(row)
        finally:
            self.vehicles_table.blockSignals(False)

        self._update_count()
        logger.info("Разовая аренда: данные груза заполнены (машин: %s)", len(rows))

    def clear(self) -> None:
        """Очищает таблицу, оставляя MIN_ROWS пустых строк."""
        self.vehicles_table.blockSignals(True)
        try:
            self.vehicles_table.setRowCount(0)
            self.vehicles_table.setRowCount(MIN_ROWS)
            for row in range(MIN_ROWS):
                self._init_row(row)
            self.vehicles_table.clearSelection()
        finally:
            self.vehicles_table.blockSignals(False)

        self._update_count()
        self.recognition_panel.clear()

        logger.debug("Разовая аренда: таблица груза очищена")


__all__ = [
    "CargoTab",
    "MAX_CARS",
    "MIN_ROWS",
    "HEADERS",
]
