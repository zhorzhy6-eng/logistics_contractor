#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Условия договора».
Содержит:
  - тип перевозчика (единый источник истины)
  - ставку НДС
  - маршрут
  - места погрузки/выгрузки (таблицы)
  - ПЛАНОВЫЕ даты подачи ТС и завершения выгрузки (для шаблона)
  - стоимость и порядок оплаты
  - особые условия

Отправляет сигналы loadings_changed / unloadings_changed при изменении
таблиц погрузок/выгрузок, чтобы другие вкладки могли обновить свои списки.
"""

import logging
import re
from typing import Dict, Any, List

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLineEdit,
    QDateEdit, QTimeEdit, QDoubleSpinBox, QComboBox, QTextEdit,
    QLabel, QGroupBox, QHBoxLayout, QScrollArea, QRadioButton,
    QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QAbstractItemView,
)
from PyQt5.QtCore import QDate, QTime, pyqtSignal, Qt

from ui.tabs.base_tab import TabMixin
from ui.widgets import RecognitionPanel
from ui.address_book_dialog import AddressBookDialog

logger = logging.getLogger("ui.tabs.contract_tab")

MAX_POINTS = 10


class ContractTab(TabMixin, QWidget):
    """Вкладка с условиями договора."""

    recognize_requested = pyqtSignal(str)

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
        self.btn_add_loading = QPushButton("➕ Добавить погрузку")
        self.btn_add_loading.clicked.connect(self._on_add_loading)
        load_btns.addWidget(self.btn_add_loading)

        self.btn_remove_loading = QPushButton("✕ Удалить погрузку")
        self.btn_remove_loading.clicked.connect(self._on_remove_loading)
        load_btns.addWidget(self.btn_remove_loading)

        self.btn_book_loading = QPushButton("📋 Из справочника")
        self.btn_book_loading.setStyleSheet("""
            QPushButton {
                background-color: #FFC107; color: #212121;
                font-weight: bold; padding: 6px 14px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #FFB300; }
        """)
        self.btn_book_loading.clicked.connect(lambda: self._on_open_book("loading"))
        load_btns.addWidget(self.btn_book_loading)

        load_btns.addStretch()
        route_layout.addLayout(load_btns)

        self.loadings_table = QTableWidget(1, 3)
        self.loadings_table.setHorizontalHeaderLabels(["Адрес *", "Дата", "Время"])
        self.loadings_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.loadings_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.loadings_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.loadings_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.loadings_table.setMinimumHeight(80)
        self._init_loading_row(0)
        route_layout.addWidget(self.loadings_table)

        # ── Таблица «Места выгрузки» ──
        unloading_label = QLabel("📍 Места выгрузки (до 10):")
        unloading_label.setStyleSheet("font-weight: bold; margin-top: 6px;")
        route_layout.addWidget(unloading_label)

        unload_btns = QHBoxLayout()
        self.btn_add_unloading = QPushButton("➕ Добавить выгрузку")
        self.btn_add_unloading.clicked.connect(self._on_add_unloading)
        unload_btns.addWidget(self.btn_add_unloading)

        self.btn_remove_unloading = QPushButton("✕ Удалить выгрузку")
        self.btn_remove_unloading.clicked.connect(self._on_remove_unloading)
        unload_btns.addWidget(self.btn_remove_unloading)

        self.btn_book_unloading = QPushButton("📋 Из справочника")
        self.btn_book_unloading.setStyleSheet("""
            QPushButton {
                background-color: #FFC107; color: #212121;
                font-weight: bold; padding: 6px 14px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #FFB300; }
        """)
        self.btn_book_unloading.clicked.connect(lambda: self._on_open_book("unloading"))
        unload_btns.addWidget(self.btn_book_unloading)

        unload_btns.addStretch()
        route_layout.addLayout(unload_btns)

        self.unloadings_table = QTableWidget(1, 3)
        self.unloadings_table.setHorizontalHeaderLabels(["Адрес *", "Дата", "Время"])
        self.unloadings_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.unloadings_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.unloadings_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.unloadings_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.unloadings_table.setMinimumHeight(80)
        self._init_unloading_row(0)
        route_layout.addWidget(self.unloadings_table)

        layout.addWidget(route_group)

        # ═══════════════════════════════════════════════════════════
        # ── ПЛАНОВЫЕ ДАТЫ (для шаблона договора) ──
        # ═══════════════════════════════════════════════════════════
        plan_group = QGroupBox("📅 Плановые даты (для шаблона договора)")
        plan_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                background-color: #E3F2FD;
                border: 2px solid #2196F3;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #0D47A1;
            }
        """)
        plan_layout = QFormLayout(plan_group)

        self.loading_plan_date = QDateEdit()
        self.loading_plan_date.setDisplayFormat("dd.MM.yyyy")
        self.loading_plan_date.setCalendarPopup(True)
        self.loading_plan_date.setDate(QDate.currentDate())
        self.loading_plan_date.setStyleSheet("""
            QDateEdit {
                font-weight: bold;
                background-color: #FFF9C4;
                padding: 4px;
                border: 1px solid #FBC02D;
                border-radius: 3px;
            }
        """)
        plan_layout.addRow("Плановая дата подачи ТС под погрузку:", self.loading_plan_date)

        time_layout = QHBoxLayout()

        self.loading_plan_time_from = QTimeEdit()
        self.loading_plan_time_from.setDisplayFormat("HH:mm")
        self.loading_plan_time_from.setTime(QTime(8, 0))
        self.loading_plan_time_from.setStyleSheet("""
            QTimeEdit {
                font-weight: bold;
                background-color: #FFF9C4;
                padding: 4px;
                border: 1px solid #FBC02D;
                border-radius: 3px;
            }
        """)
        time_layout.addWidget(QLabel("с"))
        time_layout.addWidget(self.loading_plan_time_from)

        self.loading_plan_time_to = QTimeEdit()
        self.loading_plan_time_to.setDisplayFormat("HH:mm")
        self.loading_plan_time_to.setTime(QTime(20, 0))
        self.loading_plan_time_to.setStyleSheet("""
            QTimeEdit {
                font-weight: bold;
                background-color: #FFF9C4;
                padding: 4px;
                border: 1px solid #FBC02D;
                border-radius: 3px;
            }
        """)
        time_layout.addWidget(QLabel("по"))
        time_layout.addWidget(self.loading_plan_time_to)
        time_layout.addStretch()

        plan_layout.addRow("Время подачи:", time_layout)

        self.unloading_plan_date = QDateEdit()
        self.unloading_plan_date.setDisplayFormat("dd.MM.yyyy")
        self.unloading_plan_date.setCalendarPopup(True)
        self.unloading_plan_date.setDate(QDate.currentDate().addDays(3))
        self.unloading_plan_date.setStyleSheet("""
            QDateEdit {
                font-weight: bold;
                background-color: #FFF9C4;
                padding: 4px;
                border: 1px solid #FBC02D;
                border-radius: 3px;
            }
        """)
        plan_layout.addRow("Плановая дата завершения выгрузки:", self.unloading_plan_date)

        layout.addWidget(plan_group)

        # ═══════════════════════════════════════════════════════════
        # ── СТОИМОСТЬ УСЛУГ ──
        # ═══════════════════════════════════════════════════════════
        price_group = QGroupBox("Стоимость услуг")
        price_layout = QFormLayout(price_group)

        self.carrier_type = QComboBox()
        self.carrier_type.addItems([
            "ООО (с НДС)",
            "ИП с НДС",
            "ИП без НДС",
        ])
        self.carrier_type.setCurrentIndex(0)
        self.carrier_type.setStyleSheet("""
            QComboBox {
                font-weight: bold;
                font-size: 13px;
                padding: 6px;
                background-color: #FFF9C4;
                border: 2px solid #FBC02D;
                border-radius: 3px;
                color: #BF360C;
            }
            QComboBox:hover { background-color: #FFF59D; }
            QComboBox::drop-down { border: none; }
        """)
        price_layout.addRow("🏢 Тип перевозчика *", self.carrier_type)

        self.vat_type_label = QLabel("Тип стоимости:")
        self.vat_type_layout = QHBoxLayout()
        self.radio_with_vat = QRadioButton("С НДС")
        self.radio_without_vat = QRadioButton("Без НДС")
        self.radio_without_vat.setChecked(True)
        self.vat_type_layout.addWidget(self.radio_with_vat)
        self.vat_type_layout.addWidget(self.radio_without_vat)
        self.vat_type_layout.addStretch()
        price_layout.addRow(self.vat_type_label, self.vat_type_layout)

        self.price_input = QDoubleSpinBox()
        self.price_input.setRange(0, 100000000)
        self.price_input.setDecimals(2)
        self.price_input.setSuffix(" ₽")
        self.price_input.setValue(400000)
        price_layout.addRow("Стоимость *", self.price_input)

        self.vat_rate = QLineEdit()
        self.vat_rate.setPlaceholderText("22")
        self.vat_rate.setMaxLength(2)
        self.vat_rate.setText("22")
        self.vat_rate.setStyleSheet("""
            QLineEdit {
                font-weight: bold;
                background-color: #FFF9C4;
                border: 1px solid #FBC02D;
                border-radius: 3px;
                padding: 3px;
            }
        """)
        price_layout.addRow("Ставка НДС (%)", self.vat_rate)

        self.price_with_vat = QLineEdit()
        self.price_with_vat.setReadOnly(True)
        self.price_with_vat.setStyleSheet("background-color: #f0f0f0; font-weight: bold;")
        price_layout.addRow("Стоимость (с НДС)", self.price_with_vat)

        self.price_without_vat = QLineEdit()
        self.price_without_vat.setReadOnly(True)
        self.price_without_vat.setStyleSheet("background-color: #f0f0f0; font-weight: bold;")
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
        self.special_conditions.setMaximumHeight(100)
        special_layout.addWidget(self.special_conditions)

        layout.addWidget(special_group)

        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

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

        self._generate_contract_number()
        self._calculate_price()

        logger.debug("ContractTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Инициализация строк
    # ─────────────────────────────────────────────────────────

    def _init_loading_row(self, row: int, address: str = "", date_str: str = "", time_str: str = "") -> None:
        self.loadings_table.setItem(row, 0, QTableWidgetItem(address))
        self.loadings_table.setItem(row, 1, QTableWidgetItem(date_str))
        self.loadings_table.setItem(row, 2, QTableWidgetItem(time_str))

    def _init_unloading_row(self, row: int, address: str = "", date_str: str = "", time_str: str = "") -> None:
        self.unloadings_table.setItem(row, 0, QTableWidgetItem(address))
        self.unloadings_table.setItem(row, 1, QTableWidgetItem(date_str))
        self.unloadings_table.setItem(row, 2, QTableWidgetItem(time_str))

    # ─────────────────────────────────────────────────────────
    # Сигналы при изменении таблиц
    # ─────────────────────────────────────────────────────────

    def _on_table_item_changed(self, item) -> None:
        """Отправляет сигнал при любом изменении ячеек таблиц."""
        sender = self.sender()
        if sender is self.loadings_table:
            self.loadings_changed.emit()
        elif sender is self.unloadings_table:
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
            self.vat_rate.setStyleSheet("""
                QLineEdit {
                    font-weight: bold;
                    background-color: #E0E0E0;
                    color: #666;
                    border: 1px solid #BDBDBD;
                    border-radius: 3px;
                    padding: 3px;
                }
            """)
            self.radio_without_vat.setChecked(True)
        else:
            self.vat_rate.setReadOnly(False)
            self.vat_rate.setStyleSheet("""
                QLineEdit {
                    font-weight: bold;
                    background-color: #FFF9C4;
                    border: 1px solid #FBC02D;
                    border-radius: 3px;
                    padding: 3px;
                }
            """)
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
        dialog = AddressBookDialog(point_type, parent=self)
        if dialog.exec_():
            selected = dialog.selected_address
            if not selected:
                return

            table = self.loadings_table if point_type == "loading" else self.unloadings_table

            row = table.currentRow()

            def _row_is_empty(r: int) -> bool:
                addr = table.item(r, 0)
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

            # ── Блокируем сигналы, чтобы не было 4 emit-ов подряд ──
            table.blockSignals(True)
            table.setItem(row, 0, QTableWidgetItem(selected.get("address", "")))
            table.setItem(row, 1, QTableWidgetItem(selected.get("date", "")))
            table.setItem(row, 2, QTableWidgetItem(selected.get("time_window", "")))
            table.blockSignals(False)

            table.selectRow(row)

            # ── Один явный emit ──
            if point_type == "loading":
                self.loadings_changed.emit()
            else:
                self.unloadings_changed.emit()

            logger.info(f"Адрес из справочника вставлен в строку {row} ({point_type})")

    # ─────────────────────────────────────────────────────────
    # Чтение таблиц
    # ─────────────────────────────────────────────────────────

    def _read_table(self, table: QTableWidget) -> List[Dict[str, str]]:
        result = []
        for row in range(table.rowCount()):
            address = self._get_cell(table, row, 0)
            date_str = self._get_cell(table, row, 1)
            time_str = self._get_cell(table, row, 2)
            if address.strip():
                result.append({
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
            items = [{"address": "", "date": "", "time_window": ""}]
        for item in items:
            row = table.rowCount()
            table.insertRow(row)
            init_func(
                row,
                item.get("address", ""),
                item.get("date", ""),
                item.get("time_window", ""),
            )
        table.blockSignals(False)

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
                    "address": data.get("unloading_address_1", ""),
                    "date": data.get("unloading_date", ""),
                    "time_window": data.get("unloading_time_window", ""),
                })
            if data.get("unloading_address_2"):
                legacy.append({
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