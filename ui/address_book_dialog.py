#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог справочника адресов погрузки/выгрузки.
Позволяет выбирать, добавлять, редактировать и удалять адреса.
Поддерживает импорт из Excel-файла.

Сортировка: включена по клику на заголовок, при загрузке — по алфавиту.
"""

import logging
import os
from typing import Dict, Any, Optional, List

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QLineEdit, QAbstractItemView, QFormLayout,
    QGroupBox, QWidget, QFileDialog,
)
from PyQt5.QtCore import Qt, QTimer

from db.database import (
    get_addresses, save_address, update_address, delete_address,
    import_addresses_from_list, count_addresses,
)

logger = logging.getLogger("ui.address_book_dialog")


class EditAddressDialog(QDialog):
    """Диалог добавления/редактирования одного адреса."""

    def __init__(self, address: str = "", date: str = "", time_window: str = "",
                 title: str = "Адрес", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.address_edit = QLineEdit(address)
        self.address_edit.setPlaceholderText("Полный адрес")
        form.addRow("Адрес *:", self.address_edit)

        self.date_edit = QLineEdit(date)
        self.date_edit.setPlaceholderText("ДД.ММ.ГГГГ (опционально)")
        form.addRow("Дата:", self.date_edit)

        self.time_edit = QLineEdit(time_window)
        self.time_edit.setPlaceholderText("09:00–20:00 (опционально)")
        form.addRow("Время:", self.time_edit)

        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch()

        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(btn_cancel)

        btn_save = QPushButton("💾 Сохранить")
        btn_save.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white;
                font-weight: bold; padding: 8px 20px;
                border-radius: 5px;
            }
            QPushButton:hover { background-color: #45a049; }
        """)
        btn_save.clicked.connect(self._on_save)
        buttons.addWidget(btn_save)

        layout.addLayout(buttons)

    def _on_save(self):
        if not self.address_edit.text().strip():
            QMessageBox.warning(self, "Ошибка", "Адрес обязателен.")
            return
        self.accept()

    def get_data(self) -> Dict[str, str]:
        return {
            "address": self.address_edit.text().strip(),
            "date": self.date_edit.text().strip(),
            "time_window": self.time_edit.text().strip(),
        }


class AddressBookDialog(QDialog):
    """
    Диалог справочника адресов.
    point_type: 'loading' | 'unloading'
    """

    def __init__(self, point_type: str, parent=None):
        super().__init__(parent)

        self.point_type = point_type
        self.selected_address: Optional[Dict[str, str]] = None

        # ── Состояние пагинации (Шаг 4 оптимизации) ──
        self.PAGE_SIZE = 500
        self._offset = 0
        self._total = 0

        title_map = {
            "loading": "📋 Справочник адресов погрузки",
            "unloading": "📋 Справочник адресов выгрузки",
        }
        self.setWindowTitle(title_map.get(point_type, "📋 Справочник адресов"))
        self.setMinimumSize(1000, 600)

        layout = QVBoxLayout(self)

        # ── Заголовок ──
        title = QLabel(self.windowTitle())
        title.setStyleSheet("font-size: 14px; font-weight: bold; padding: 5px;")
        layout.addWidget(title)

        # ── Поиск ──
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("🔍 Поиск:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Подстрока адреса...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search)
        search_layout.addWidget(self.search_input, 1)

        btn_reset = QPushButton("Сбросить")
        btn_reset.clicked.connect(lambda: self.search_input.clear())
        search_layout.addWidget(btn_reset)

        layout.addLayout(search_layout)

        # ── Таблица ──
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "Адрес", "Дата", "Время", "Использован"])

        # ── СОРТИРОВКА ──
        self.table.setSortingEnabled(True)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._on_pick)
        layout.addWidget(self.table)

        # ── Пагинация (Шаг 4 оптимизации) ──
        # Раньше запрос отдавал максимум 500 адресов, и при росте справочника
        # часть записей была недоступна. Теперь страницы догружаются.
        pager = QHBoxLayout()
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color: #555;")
        pager.addWidget(self.lbl_count)
        pager.addStretch()

        self.btn_more = QPushButton("Показать ещё")
        self.btn_more.setEnabled(False)
        self.btn_more.clicked.connect(self._on_show_more)
        pager.addWidget(self.btn_more)
        layout.addLayout(pager)

        # ── Кнопки ──
        buttons = QHBoxLayout()

        btn_add = QPushButton("➕ Добавить в справочник")
        btn_add.clicked.connect(self._on_add)
        buttons.addWidget(btn_add)

        btn_import = QPushButton("📥 Импорт из Excel")
        btn_import.setStyleSheet("""
            QPushButton {
                background-color: #2196F3; color: white;
                font-weight: bold; padding: 6px 14px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #1976D2; }
        """)
        btn_import.clicked.connect(self._on_import_excel)
        buttons.addWidget(btn_import)

        btn_edit = QPushButton("✏ Редактировать")
        btn_edit.clicked.connect(self._on_edit)
        buttons.addWidget(btn_edit)

        btn_delete = QPushButton("🗑 Удалить")
        btn_delete.setStyleSheet("""
            QPushButton {
                background-color: #f44336; color: white;
                font-weight: bold; padding: 6px 14px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #d32f2f; }
        """)
        btn_delete.clicked.connect(self._on_delete)
        buttons.addWidget(btn_delete)

        buttons.addStretch()

        btn_pick = QPushButton("✓ Выбрать")
        btn_pick.setStyleSheet("""
            QPushButton {
                background-color: #2196F3; color: white;
                font-weight: bold; padding: 8px 20px;
                border-radius: 5px;
            }
            QPushButton:hover { background-color: #1976D2; }
        """)
        btn_pick.clicked.connect(self._on_pick)
        buttons.addWidget(btn_pick)

        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.reject)
        buttons.addWidget(btn_close)

        layout.addLayout(buttons)

        # ── Debounce поиска (Шаг 4/5 оптимизации) ──
        # Запрос к базе уходит не на каждую букву, а через 250 мс после
        # последнего нажатия — набор текста не тормозит из-за поиска.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._reload_first_page)

        self._load_data()

    # ─────────────────────────────────────────────────────────
    # Поиск / загрузка
    # ─────────────────────────────────────────────────────────
    def _on_search(self, text: str):
        """
        Перезапускает таймер поиска (debounce 250 мс).

        Раньше запрос уходил на каждое нажатие клавиши; теперь — после паузы.
        """
        self._search_timer.start()

    def _current_search(self) -> str:
        return self.search_input.text().strip()

    def _reload_first_page(self):
        """Загружает первую страницу с учётом текущего поиска."""
        self._offset = 0
        self._load_data(search=self._current_search(), reset=True)

    def _on_show_more(self):
        """Догружает следующую страницу адресов."""
        self._load_data(search=self._current_search(), reset=False)

    def _update_pager(self):
        """Обновляет счётчик «Показано X из Y» и доступность кнопки догрузки."""
        shown = self.table.rowCount()
        self.lbl_count.setText(f"Показано {shown} из {self._total}")
        rest = max(self._total - shown, 0)
        self.btn_more.setEnabled(rest > 0)
        self.btn_more.setText(f"Показать ещё {min(self.PAGE_SIZE, rest)}" if rest else "Все адреса показаны")

    def _load_data(self, search: str = "", reset: bool = True):
        """
        Загружает страницу адресов.

        :param search: подстрока адреса (пусто — без фильтра)
        :param reset: True — начать с первой страницы (поиск, добавление,
                      редактирование), False — догрузить следующую порцию
        """
        try:
            total = count_addresses(self.point_type, search)
            items = get_addresses(
                self.point_type, search=search, limit=self.PAGE_SIZE, offset=self._offset
            )
            self._total = total
        except Exception as e:
            logger.error(f"Ошибка загрузки справочника: {e}")
            items = []

        # Отключаем сортировку на время заполнения
        self.table.setSortingEnabled(False)
        if reset:
            self.table.setRowCount(0)

        for item in items:
            row = self.table.rowCount()
            self.table.insertRow(row)

            item_id = QTableWidgetItem(str(item.get("id", "")))
            item_id.setData(Qt.UserRole, item)
            self.table.setItem(row, 0, item_id)

            self.table.setItem(row, 1, QTableWidgetItem(item.get("address", "") or ""))
            self.table.setItem(row, 2, QTableWidgetItem(item.get("date", "") or ""))
            self.table.setItem(row, 3, QTableWidgetItem(item.get("time_window", "") or ""))
            self.table.setItem(row, 4, QTableWidgetItem(f"{item.get('usage_count', 0)} раз"))

        # Включаем сортировку и сортируем по «Адрес» (колонка 1)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(1, Qt.AscendingOrder)

        self._offset += len(items)
        self._update_pager()

        logger.debug(
            f"Загружено адресов: {len(items)} из {self._total} "
            f"(offset={self._offset - len(items)}, тип={self.point_type})"
        )

    def _get_selected(self) -> Optional[Dict[str, Any]]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if not item:
            return None
        return item.data(Qt.UserRole)

    # ─────────────────────────────────────────────────────────
    # Добавление / редактирование / удаление
    # ─────────────────────────────────────────────────────────
    def _on_add(self):
        dialog = EditAddressDialog(title="Новый адрес", parent=self)
        if dialog.exec_():
            data = dialog.get_data()
            save_address(
                self.point_type,
                data["address"],
                data["date"],
                data["time_window"],
            )
            self._reload_first_page()

    def _on_edit(self):
        selected = self._get_selected()
        if not selected:
            QMessageBox.warning(self, "Редактирование", "Выберите запись.")
            return

        dialog = EditAddressDialog(
            address=selected.get("address", ""),
            date=selected.get("date", "") or "",
            time_window=selected.get("time_window", "") or "",
            title="Редактирование адреса",
            parent=self,
        )
        if dialog.exec_():
            data = dialog.get_data()
            update_address(
                selected["id"],
                data["address"],
                data["date"],
                data["time_window"],
            )
            self._reload_first_page()

    def _on_delete(self):
        selected = self._get_selected()
        if not selected:
            QMessageBox.warning(self, "Удаление", "Выберите запись.")
            return

        reply = QMessageBox.question(
            self, "Удаление",
            f"Удалить адрес из справочника?\n\n{selected.get('address', '')}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            delete_address(selected["id"])
            self._reload_first_page()

    # ─────────────────────────────────────────────────────────
    # Импорт из Excel
    # ─────────────────────────────────────────────────────────
    def _on_import_excel(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите Excel-файл со списком адресов",
            "",
            "Excel-файлы (*.xlsx *.xls);;Все файлы (*.*)",
        )
        if not path:
            return

        try:
            import openpyxl
        except ImportError:
            QMessageBox.critical(
                self, "Ошибка",
                "Не установлен модуль openpyxl.\n\n"
                "Установите: pip install openpyxl"
            )
            return

        try:
            wb = openpyxl.load_workbook(path, data_only=True)
            ws = wb.active

            addresses = []
            skipped_header = False

            for row in ws.iter_rows(min_row=1, max_col=1, values_only=True):
                value = row[0]
                if value is None:
                    continue
                text = str(value).strip()
                if not text:
                    continue
                if not skipped_header and "адрес" in text.lower():
                    skipped_header = True
                    continue
                addresses.append({
                    "address": text,
                    "date": "",
                    "time_window": "",
                })

            if not addresses:
                QMessageBox.warning(
                    self, "Импорт",
                    "В файле не найдено ни одного адреса "
                    "(первая колонка пуста?)."
                )
                return

            reply = QMessageBox.question(
                self, "Импорт",
                f"Найдено адресов: {len(addresses)}.\n\n"
                f"Импортировать их в справочник «{self.point_type}»?\n\n"
                f"Дубликаты будут учтены как «+1 использование».",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return

            added = import_addresses_from_list(self.point_type, addresses)

            QMessageBox.information(
                self, "Импорт завершён",
                f"Обработано адресов: {len(addresses)}\n"
                f"Добавлено новых: {added}\n"
                f"Уже были в справочнике: {len(addresses) - added}"
            )
            logger.info(
                f"Импорт из Excel: {path}, обработано={len(addresses)}, "
                f"добавлено={added}"
            )

            self._reload_first_page()

        except Exception as e:
            logger.exception("Ошибка импорта из Excel")
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось прочитать файл:\n{e}"
            )

    # ─────────────────────────────────────────────────────────
    # Выбор
    # ─────────────────────────────────────────────────────────
    def _on_pick(self):
        selected = self._get_selected()
        if not selected:
            QMessageBox.warning(self, "Выбор", "Выберите адрес из списка.")
            return
        self.selected_address = {
            "address": selected.get("address", ""),
            "date": selected.get("date", "") or "",
            "time_window": selected.get("time_window", "") or "",
        }
        self.accept()