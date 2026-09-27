#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Перевозимые автомобили».
Таблица с колонками:
  - VIN-код
  - Марка/Модель
  - Госномер
  - Год выпуска (по умолчанию — текущий год)
  - Цвет
  - Тип ТС
  - Погрузка  (выпадающий список)
  - Выгрузка (выпадающий список)

Колонки можно перетаскивать за заголовки (кроме фиксированных).
Ширина колонок настраивается вручную.
"""

import logging
from datetime import datetime
from typing import Dict, Any, List

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QMessageBox,
    QHeaderView, QComboBox, QSpinBox,
)
from PyQt5.QtCore import pyqtSignal

from ui.tabs.base_tab import TabMixin
from ui.widgets import RecognitionPanel

logger = logging.getLogger("ui.tabs.vehicles_tab")

# Значение «не привязано» — попадает во все точки
NO_POINT = "— (все)"


def _current_year() -> int:
    """Возвращает текущий год. Используется как дефолт для поля 'Год выпуска'."""
    return datetime.now().year


class VehiclesTab(TabMixin, QWidget):
    """
    Вкладка с таблицей транспортных средств.
    """

    recognize_requested = pyqtSignal(str)

    # Колонки таблицы
    COL_VIN = 0
    COL_BRAND = 1
    COL_PLATE = 2
    COL_YEAR = 3
    COL_COLOR = 4
    COL_TYPE = 5
    COL_LOADING = 6
    COL_UNLOADING = 7

    COLUMNS = [
        "VIN-код",
        "Марка/Модель",
        "Госномер",
        "Год выпуска",
        "Цвет",
        "Тип ТС",
        "Погрузка",
        "Выгрузка",
    ]

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)

        # ── Панель распознавания ──
        self.recognition_panel = RecognitionPanel(
            placeholder="Вставьте текст с VIN-кодами автомобилей..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Кнопки управления ──
        button_layout = QHBoxLayout()

        self.btn_add = QPushButton("➕ Добавить ТС")
        self.btn_add.clicked.connect(self._on_add_vehicle)
        button_layout.addWidget(self.btn_add)

        self.btn_remove = QPushButton("✕ Удалить ТС")
        self.btn_remove.clicked.connect(self._on_remove_vehicle)
        button_layout.addWidget(self.btn_remove)

        button_layout.addStretch()

        layout.addLayout(button_layout)

        # ── Таблица ТС ──
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)

        header = self.table.horizontalHeader()

        # ── Колонки можно двигать за заголовки ──
        header.setSectionsMovable(True)

        # ── Режим ширины: Interactive — пользователь может менять вручную ──
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)

        # ── Минимальная ширина, чтобы не «схлопывались» ──
        header.setMinimumSectionSize(60)

        # ── Начальные ширины ──
        # VIN — по содержимому
        header.setSectionResizeMode(self.COL_VIN, QHeaderView.ResizeToContents)
        # Марка — 240 px (хватит для «JETOUR T2 2.0Т 8AT Премиум»)
        self.table.setColumnWidth(self.COL_BRAND, 240)
        # Госномер — по содержимому
        header.setSectionResizeMode(self.COL_PLATE, QHeaderView.ResizeToContents)
        # Год — фиксированный, компактный
        self.table.setColumnWidth(self.COL_YEAR, 90)
        # Цвет — 100 px
        self.table.setColumnWidth(self.COL_COLOR, 100)
        # Тип ТС — 160 px (для «Легковой автомобиль»)
        self.table.setColumnWidth(self.COL_TYPE, 160)
        # Погрузка / Выгрузка — 280 px (чтобы видеть адрес)
        self.table.setColumnWidth(self.COL_LOADING, 280)
        self.table.setColumnWidth(self.COL_UNLOADING, 280)

        self.table.setEditTriggers(
            QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed
        )

        layout.addWidget(self.table)

        # ── Кэш текущих списков точек ──
        self._loadings_points: List[Dict[str, str]] = []
        self._unloadings_points: List[Dict[str, str]] = []

        logger.debug("VehiclesTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Синхронизация списков точек с contract_tab
    # ─────────────────────────────────────────────────────────

    def update_loadings_list(self, loadings: List[Dict[str, str]]) -> None:
        """
        Обновляет выпадающий список погрузок в каждой строке.
        loadings — список словарей {'address', 'date', 'time_window'}.
        """
        self._loadings_points = loadings or []
        self._refresh_all_combos(
            self.COL_LOADING,
            self._build_point_items(self._loadings_points, "Погрузка"),
        )
        logger.debug(f"Список погрузок обновлён: {len(self._loadings_points)} шт.")

    def update_unloadings_list(self, unloadings: List[Dict[str, str]]) -> None:
        """Обновляет выпадающий список выгрузок в каждой строке."""
        self._unloadings_points = unloadings or []
        self._refresh_all_combos(
            self.COL_UNLOADING,
            self._build_point_items(self._unloadings_points, "Выгрузка"),
        )
        logger.debug(f"Список выгрузок обновлён: {len(self._unloadings_points)} шт.")

    @staticmethod
    def _build_point_items(points: List[Dict[str, str]], title: str) -> List[str]:
        """
        Строит список отображаемых строк для QComboBox.
        Первый элемент — «— (все)», далее «Погрузка 1 (адрес...)» и т.д.
        Адрес обрезается до 60 символов (было 40 — теперь длиннее, т.к. колонка шире).
        """
        items = [NO_POINT]
        for i, p in enumerate(points, 1):
            address = (p.get("address") or "").strip()
            if len(address) > 60:
                address = address[:60] + "…"
            items.append(f"{title} {i} ({address})")
        return items

    def _refresh_all_combos(self, column: int, items: List[str]) -> None:
        """
        Обновляет все QComboBox в указанной колонке.
        Сохраняет текущий выбор пользователя, если такой пункт ещё существует.
        """
        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, column)
            if not isinstance(combo, QComboBox):
                continue

            current_text = combo.currentText()

            combo.blockSignals(True)
            combo.clear()
            combo.addItems(items)

            if current_text in items:
                combo.setCurrentText(current_text)
            else:
                combo.setCurrentIndex(0)
            combo.blockSignals(False)

    # ─────────────────────────────────────────────────────────
    # Создание ячеек-виджетов
    # ─────────────────────────────────────────────────────────

    def _make_combo(self, column: int) -> QComboBox:
        """Создаёт QComboBox для колонки Погрузка/Выгрузка."""
        if column == self.COL_LOADING:
            points = self._loadings_points
            title = "Погрузка"
        else:
            points = self._unloadings_points
            title = "Выгрузка"

        items = self._build_point_items(points, title)

        combo = QComboBox()
        combo.addItems(items)
        combo.setStyleSheet("""
            QComboBox {
                padding: 3px 6px;
                border: 1px solid #BDBDBD;
                border-radius: 3px;
                background-color: white;
            }
            QComboBox:hover { background-color: #F5F5F5; }
        """)
        return combo

    def _make_year_spin(self, year: int = None) -> QSpinBox:
        """
        Создаёт QSpinBox для года выпуска.
        Дефолт — текущий год.
        """
        spin = QSpinBox()
        spin.setRange(1950, _current_year() + 1)
        if year is None or year <= 0:
            spin.setValue(_current_year())
        else:
            try:
                spin.setValue(int(year))
            except (ValueError, TypeError):
                spin.setValue(_current_year())
        return spin

    # ─────────────────────────────────────────────────────────
    # Добавление / удаление
    # ─────────────────────────────────────────────────────────

    def _on_add_vehicle(self) -> None:
        """Добавляет новую пустую строку в таблицу."""
        row = self.table.rowCount()
        self.table.insertRow(row)

        self.table.setItem(row, self.COL_VIN, QTableWidgetItem(""))
        self.table.setItem(row, self.COL_BRAND, QTableWidgetItem(""))
        self.table.setItem(row, self.COL_PLATE, QTableWidgetItem(""))

        # Год — текущий
        year_spin = self._make_year_spin()
        self.table.setCellWidget(row, self.COL_YEAR, year_spin)

        self.table.setItem(row, self.COL_COLOR, QTableWidgetItem(""))

        type_combo = QComboBox()
        type_combo.addItems(["Легковой автомобиль", "Тягач", "Прицеп", "Фургон", "Автобус"])
        self.table.setCellWidget(row, self.COL_TYPE, type_combo)

        # ── Погрузка / Выгрузка ──
        self.table.setCellWidget(row, self.COL_LOADING, self._make_combo(self.COL_LOADING))
        self.table.setCellWidget(row, self.COL_UNLOADING, self._make_combo(self.COL_UNLOADING))

        logger.debug(f"Добавлено ТС: строка {row}, год={_current_year()}")

    def _on_remove_vehicle(self) -> None:
        """Удаляет выбранную строку."""
        current_row = self.table.currentRow()

        if current_row < 0:
            QMessageBox.warning(self, "Удаление ТС", "Выберите строку для удаления.")
            return

        reply = QMessageBox.question(
            self,
            "Удалить ТС",
            f"Удалить ТС из строки {current_row + 1}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.table.removeRow(current_row)
            logger.info(f"ТС удалено: строка {current_row}")

    # ─────────────────────────────────────────────────────────
    # Сбор данных
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> List[Dict[str, Any]]:
        """Собирает данные ТС из таблицы."""
        vehicles = []
        for row in range(self.table.rowCount()):
            vehicle = {
                "vin": self._get_cell_text(row, self.COL_VIN),
                "brand_model": self._get_cell_text(row, self.COL_BRAND),
                "plate_number": self._get_cell_text(row, self.COL_PLATE),
                "year": self._get_spin_value(row, self.COL_YEAR),
                "color": self._get_cell_text(row, self.COL_COLOR),
                "vehicle_type": self._get_combo_value(row, self.COL_TYPE),
                "loading_index": self._get_point_index(row, self.COL_LOADING),
                "unloading_index": self._get_point_index(row, self.COL_UNLOADING),
            }

            if any([vehicle["vin"], vehicle["brand_model"], vehicle["plate_number"]]):
                vehicles.append(vehicle)

        return vehicles

    def _get_point_index(self, row: int, column: int) -> int:
        """Возвращает индекс выбранной точки (1..N) или 0, если «— (все)»."""
        combo = self.table.cellWidget(row, column)
        if not isinstance(combo, QComboBox):
            return 0

        return combo.currentIndex()

    # ─────────────────────────────────────────────────────────
    # Заполнение таблицы
    # ─────────────────────────────────────────────────────────

    def fill_data(self, vehicles: List[Dict[str, Any]]) -> None:
        """Заполняет таблицу данными."""
        self.table.setRowCount(0)

        for vehicle in vehicles:
            row = self.table.rowCount()
            self.table.insertRow(row)

            self.table.setItem(row, self.COL_VIN, QTableWidgetItem(str(vehicle.get("vin", ""))))
            self.table.setItem(row, self.COL_BRAND, QTableWidgetItem(str(vehicle.get("brand_model", ""))))
            self.table.setItem(row, self.COL_PLATE, QTableWidgetItem(str(vehicle.get("plate_number", ""))))

            # ── Год: из данных или текущий ──
            year_value = vehicle.get("year", 0)
            year_spin = self._make_year_spin(year_value)
            self.table.setCellWidget(row, self.COL_YEAR, year_spin)

            self.table.setItem(row, self.COL_COLOR, QTableWidgetItem(str(vehicle.get("color", ""))))

            type_combo = QComboBox()
            type_combo.addItems(["Легковой автомобиль", "Тягач", "Прицеп", "Фургон", "Автобус"])
            vehicle_type = vehicle.get("vehicle_type", "Легковой автомобиль")
            idx = type_combo.findText(vehicle_type)
            if idx >= 0:
                type_combo.setCurrentIndex(idx)
            self.table.setCellWidget(row, self.COL_TYPE, type_combo)

            # ── Погрузка / Выгрузка ──
            loading_combo = self._make_combo(self.COL_LOADING)
            unloading_combo = self._make_combo(self.COL_UNLOADING)

            loading_index = vehicle.get("loading_index", 0) or 0
            unloading_index = vehicle.get("unloading_index", 0) or 0

            if 0 <= loading_index < loading_combo.count():
                loading_combo.setCurrentIndex(loading_index)
            if 0 <= unloading_index < unloading_combo.count():
                unloading_combo.setCurrentIndex(unloading_index)

            self.table.setCellWidget(row, self.COL_LOADING, loading_combo)
            self.table.setCellWidget(row, self.COL_UNLOADING, unloading_combo)

        logger.info(f"Таблица ТС заполнена: {len(vehicles)} записей")

    # ─────────────────────────────────────────────────────────
    # Очистка
    # ─────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Очищает таблицу."""
        self.table.setRowCount(0)
        self.recognition_panel.clear()
        logger.debug("Таблица ТС очищена")

    # ─────────────────────────────────────────────────────────
    # Вспомогательные методы
    # ─────────────────────────────────────────────────────────

    def _get_cell_text(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        if item:
            return item.text().strip()
        return ""

    def _get_spin_value(self, row: int, col: int) -> int:
        widget = self.table.cellWidget(row, col)
        if isinstance(widget, QSpinBox):
            return widget.value()
        return 0

    def _get_combo_value(self, row: int, col: int) -> str:
        widget = self.table.cellWidget(row, col)
        if isinstance(widget, QComboBox):
            return widget.currentText()
        return ""