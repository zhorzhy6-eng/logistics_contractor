#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Груз» окна типа «Формика» (ЭТАП 3.1.B.2).

Груз Формики — перевозимые автомобили: в бланке это таблица, куда вписывают
марку/модель и VIN. В таблицу помещается не больше 12 машин (столько строк
в шаблоне), поэтому больше строк вкладка не создаёт.

Ключ get_data() — vehicles: список словарей вида
{"brand_model": str, "vin": str} — читает
ui/windows/formika/data.py::_build_cargo.
"""

import logging
from typing import Any, Dict, List

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QHeaderView, QHBoxLayout, QLabel, QMessageBox,
    QScrollArea, QSpinBox, QStyledItemDelegate, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import RecognitionPanel
from ui.widgets.table_helpers import install_column_settings_menu

logger = logging.getLogger("ui.windows.formika.tabs.cargo_tab")

#: Сколько машин помещается в таблицу бланка Формики.
#: Значение совпадает с ui/windows/formika/data.py::MAX_CARS.
MAX_CARS = 12

#: Сколько пустых строк показывать при открытии вкладки.
MIN_ROWS = 3

#: Колонки таблицы: №, марка/модель, VIN.
COL_NUMBER = 0
COL_BRAND = 1
COL_VIN = 2

#: Ключ QSettings: состав колонок этой таблицы (ШАГ FIX-6, часть B3).
COLUMNS_STORAGE_KEY = "ui/formika_cargo/columns"


class _NumberDelegate(QStyledItemDelegate):
    """Ячейка «№» — счётчик строки: редактируется только стрелками."""

    def createEditor(self, parent, option, index):
        spin = QSpinBox(parent)
        spin.setRange(1, MAX_CARS)
        return spin


class CargoTab(TabMixin, QWidget):
    """Перевозимые автомобили: марка/модель и VIN, до 12 штук."""

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
            placeholder="Вставьте текст с данными автомобилей (марка, VIN)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Таблица машин ──
        hint = QLabel(f"Перевозимые автомобили (до {MAX_CARS}):")
        hint.setObjectName("sectionHeading")
        layout.addWidget(hint)

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

        self.vehicles_table = QTableWidget(MIN_ROWS, 3)
        self.vehicles_table.setHorizontalHeaderLabels(["№", "Марка/модель", "VIN"])
        self.vehicles_table.horizontalHeader().setSectionResizeMode(
            COL_NUMBER, QHeaderView.ResizeToContents
        )
        self.vehicles_table.horizontalHeader().setSectionResizeMode(
            COL_BRAND, QHeaderView.Stretch
        )
        self.vehicles_table.horizontalHeader().setSectionResizeMode(
            COL_VIN, QHeaderView.ResizeToContents
        )
        self.vehicles_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.vehicles_table.setItemDelegateForColumn(
            COL_NUMBER, _NumberDelegate(self.vehicles_table)
        )
        # ── Состав колонок (ШАГ FIX-6, часть B3) ──
        # Правый клик по шапке → галочки «какие колонки показывать».
        # VIN и марка обязательны: без них строка машины теряет смысл.
        # Колонки прячутся, а не удаляются, поэтому `get_data()` продолжает
        # читать их значения (см. тест test_hidden_column_data_still_available).
        install_column_settings_menu(
            self.vehicles_table,
            [("number", "№", True), ("brand_model", "Марка/модель", True),
             ("vin", "VIN", True)],
            storage_key=COLUMNS_STORAGE_KEY,
            on_changed=self._reapply_column_widths,
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

        logger.debug("Formika CargoTab инициализирована")

    def _reapply_column_widths(self) -> None:
        """
        Возвращает режимы колонок после смены их состава (ШАГ FIX-6).

        Показанная обратно колонка сохраняет свой режим: «№» — по
        содержимому, марка — растянуть, VIN — по содержимому. Скрой её
        и покажи заново — она останется такой же, как при открытии окна.
        """
        header = self.vehicles_table.horizontalHeader()
        header.setSectionResizeMode(COL_NUMBER, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_BRAND, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_VIN, QHeaderView.ResizeToContents)

    # ─────────────────────────────────────────────────────────
    # Строки таблицы
    # ─────────────────────────────────────────────────────────

    def _init_row(self, row: int) -> None:
        """Пустая строка: номер строки, пустые марка и VIN."""
        self.vehicles_table.setItem(row, COL_NUMBER, self._number_item(row))
        self.vehicles_table.setItem(row, COL_BRAND, QTableWidgetItem(""))
        self.vehicles_table.setItem(row, COL_VIN, QTableWidgetItem(""))

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
            logger.info("Формика: машин уже %s — больше не помещается", row_count)
            QMessageBox.warning(
                self, "Ограничение",
                f"В бланк помещается не больше {MAX_CARS} автомобилей.",
            )
            return

        self.vehicles_table.insertRow(row_count)
        self._init_row(row_count)
        logger.debug("Формика: добавлена строка автомобиля %s", row_count + 1)

    def _on_remove_vehicle(self) -> None:
        """Удаляет выбранную строку и перенумеровывает оставшиеся."""
        row = self.vehicles_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Удаление", "Выберите строку для удаления.")
            return

        self.vehicles_table.removeRow(row)
        self._renumber_rows()
        logger.debug("Формика: удалена строка автомобиля %s", row + 1)

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Список машин: только строки, где заполнена марка или VIN.

        Пустые строки в данные не попадают — иначе в бланке появились бы
        пустые строки таблицы груза.
        """
        vehicles: List[Dict[str, str]] = []
        for row in range(self.vehicles_table.rowCount()):
            brand_model = self._cell_text(self.vehicles_table, row, COL_BRAND)
            vin = self._cell_text(self.vehicles_table, row, COL_VIN)
            if not brand_model and not vin:
                continue
            vehicles.append({"brand_model": brand_model, "vin": vin})

        return {"vehicles": vehicles}

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет таблицу машин.

        Лишние машины (сверх 12) отбрасываются — в бланк они всё равно не
        помещаются; диалог здесь не показывается: заполнение идёт по кнопке
        или из распознавания и не должно останавливать работу окна.
        Пустой список оставляет таблицу пустой на MIN_ROWS строк.
        """
        if not data:
            return

        vehicles = data.get("vehicles")
        if not isinstance(vehicles, (list, tuple)):
            return

        rows = [item for item in vehicles if isinstance(item, dict)]
        if len(rows) > MAX_CARS:
            logger.warning(
                "Формика: машин %s, в бланк помещается %s — лишние отброшены",
                len(rows), MAX_CARS,
            )
            rows = rows[:MAX_CARS]

        # Перерисовка таблицы — без сигналов: они не нужны, а лишние
        # срабатывания обработчиков ячеек только мешают.
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
            else:
                self.vehicles_table.setRowCount(MIN_ROWS)
                for row in range(MIN_ROWS):
                    self._init_row(row)
        finally:
            self.vehicles_table.blockSignals(False)

        logger.info("Формика: данные груза заполнены (машин: %s)", len(rows))

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

        self.recognition_panel.clear()

        logger.debug("Формика: таблица груза очищена")


__all__ = ["CargoTab", "MAX_CARS", "MIN_ROWS"]
