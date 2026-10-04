#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Маршрут» окна типа «Логистикс Рус» (ЭТАП 3.1.C.B.2).

Маршрут этой заявки — направление, точки погрузки и выгрузки таблицами и
общий план по каждой стороне. Образец таблиц с грузоотправителями и
грузополучателями — ui/tabs/contract_tab.py (loadings_table /
unloadings_table): до 10 блоков, кнопки «Добавить» / «Удалить», минимум
одна строка.

Отличие от ui/windows/formika/tabs/route_tab.py: у Формики точек ровно две
(адрес погрузки и адрес выгрузки отдельными полями), здесь их списки —
в бланке Логистикс Рус печатаются блоки «Грузоотправитель: …» и
«Грузополучатель №N: …» с наименованием и адресом.

Ключи get_data() — route, shippers, consignees, loading_date,
loading_time_from / _to, unloading_date, unloading_time_from / _to — читает
ui/windows/logistiks_rus/data.py::_build_route. Дата и окно времени у точек
маршрута не вводятся: в бланке они печатаются общей строкой плана, поэтому
сборка сама подставляет их в каждую точку.
"""

import logging
from typing import Any, Dict, List, Mapping

from PyQt5.QtCore import QDate, QTime, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QDateEdit, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QScrollArea, QTableWidget, QTableWidgetItem, QTimeEdit,
    QVBoxLayout, QWidget,
)

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableDateEdit, PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.logistiks_rus.tabs.route_tab")

#: Сколько блоков грузоотправителей и грузополучателей в бланке.
#: Значение совпадает с ui/windows/logistiks_rus/data.py::MAX_POINTS.
MAX_POINTS = 10

#: Сколько строк показывать при открытии вкладки (меньше не бывает).
MIN_ROWS = 1

#: Колонки таблиц точек маршрута: наименование и адрес.
COL_NAME = 0
COL_ADDRESS = 1

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
            placeholder="Вставьте текст с маршрутом, грузоотправителями и грузополучателями..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Маршрут» ──
        route_group, route_layout = theme.section_box("Маршрут")

        self.route = PasteableLineEdit("Москва - Казань")
        route_layout.addRow("Направление", self.route)

        layout.addWidget(route_group)

        # ── Таблица «Грузоотправители» ──
        shippers_group = QGroupBox(f"Грузоотправители (до {MAX_POINTS})")
        shippers_layout = QVBoxLayout(shippers_group)

        shippers_buttons = QHBoxLayout()
        self.btn_add_shipper = theme.secondary_button(
            "Добавить грузоотправителя", tooltip="Добавить строку грузоотправителя"
        )
        self.btn_add_shipper.clicked.connect(self._on_add_shipper)
        shippers_buttons.addWidget(self.btn_add_shipper)

        self.btn_remove_shipper = theme.danger_button(
            "Удалить", tooltip="Удалить выбранную строку"
        )
        self.btn_remove_shipper.clicked.connect(self._on_remove_shipper)
        shippers_buttons.addWidget(self.btn_remove_shipper)

        shippers_buttons.addStretch()
        shippers_layout.addLayout(shippers_buttons)

        self.shippers_table = self._create_points_table()
        shippers_layout.addWidget(self.shippers_table)

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

        consignees_buttons.addStretch()
        consignees_layout.addLayout(consignees_buttons)

        self.consignees_table = self._create_points_table()
        consignees_layout.addWidget(self.consignees_table)

        layout.addWidget(consignees_group)

        # ── Группа «План погрузки» ──
        loading_group, loading_layout = theme.section_box("План погрузки")

        self.loading_date = self._make_date_edit(QDate.currentDate())
        loading_layout.addRow("Дата погрузки", self.loading_date)

        loading_time_layout = QHBoxLayout()
        self.loading_time_from = self._make_time_edit(DEFAULT_LOADING_TIME_FROM)
        loading_time_layout.addWidget(QLabel("с"))
        loading_time_layout.addWidget(self.loading_time_from)
        self.loading_time_to = self._make_time_edit(DEFAULT_LOADING_TIME_TO)
        loading_time_layout.addWidget(QLabel("по"))
        loading_time_layout.addWidget(self.loading_time_to)
        loading_time_layout.addStretch()
        loading_layout.addRow("Время погрузки", loading_time_layout)

        layout.addWidget(loading_group)

        # ── Группа «План выгрузки» ──
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

    def _create_points_table(self) -> QTableWidget:
        """Пустая таблица точек маршрута: наименование и адрес."""
        table = QTableWidget(MIN_ROWS, 2)
        table.setHorizontalHeaderLabels(["Наименование", "Адрес"])
        table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(COL_ADDRESS, QHeaderView.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setMinimumHeight(80)
        table.setMaximumHeight(160)
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
    def _init_point_row(table: QTableWidget, row: int) -> None:
        """Пустая строка точки маршрута."""
        table.setItem(row, COL_NAME, QTableWidgetItem(""))
        table.setItem(row, COL_ADDRESS, QTableWidgetItem(""))

    @staticmethod
    def _cell_text(table: QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        return item.text().strip() if item else ""

    # ─────────────────────────────────────────────────────────
    # Строки таблиц грузоотправителей и грузополучателей
    # ─────────────────────────────────────────────────────────

    def _on_add_shipper(self) -> None:
        """Добавляет строку грузоотправителя; сверх 10 не пускает."""
        self._add_point_row(self.shippers_table, "грузоотправителя")

    def _on_remove_shipper(self) -> None:
        """Удаляет выбранную строку грузоотправителя."""
        self._remove_point_row(self.shippers_table, "грузоотправитель")

    def _on_add_consignee(self) -> None:
        """Добавляет строку грузополучателя; сверх 10 не пускает."""
        self._add_point_row(self.consignees_table, "грузополучателя")

    def _on_remove_consignee(self) -> None:
        """Удаляет выбранную строку грузополучателя."""
        self._remove_point_row(self.consignees_table, "грузополучатель")

    def _add_point_row(self, table: QTableWidget, title: str) -> None:
        """Общее добавление строки для обеих таблиц (в connect — без lambda)."""
        row_count = table.rowCount()
        if row_count >= MAX_POINTS:
            logger.info("Логистикс Рус: точек уже %s — больше не помещается", row_count)
            QMessageBox.warning(
                self, "Ограничение",
                f"В бланк помещается не больше {MAX_POINTS} блоков: "
                f"добавить ещё одного {title} нельзя.",
            )
            return

        table.insertRow(row_count)
        self._init_point_row(table, row_count)
        logger.debug("Логистикс Рус: добавлена строка (%s)", title)

    def _remove_point_row(self, table: QTableWidget, title: str) -> None:
        """Общее удаление строки: последнюю строку таблицы не убираем."""
        row = table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Удаление", "Выберите строку для удаления.")
            return
        if table.rowCount() <= MIN_ROWS:
            QMessageBox.warning(
                self, "Удаление",
                f"Должен остаться хотя бы один {title}.",
            )
            return

        table.removeRow(row)
        logger.debug("Логистикс Рус: удалена строка (%s)", title)

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Маршрут, точки и план (даты — ISO, время — «HH:mm»).

        Точки отдаются массивами shippers / consignees по образцу
        ui/tabs/contract_tab.py; пустые строки таблиц в них не попадают.
        """
        return {
            "route": self.route.text().strip(),
            "shippers": self._read_points(self.shippers_table),
            "consignees": self._read_points(self.consignees_table),
            "loading_date": self.loading_date.date().toString("yyyy-MM-dd"),
            "loading_time_from": self.loading_time_from.time().toString("HH:mm"),
            "loading_time_to": self.loading_time_to.time().toString("HH:mm"),
            "unloading_date": self.unloading_date.date().toString("yyyy-MM-dd"),
            "unloading_time_from": self.unloading_time_from.time().toString("HH:mm"),
            "unloading_time_to": self.unloading_time_to.time().toString("HH:mm"),
        }

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
        Заполняет маршрут, таблицы точек и план погрузки/выгрузки.

        Пустые значения игнорируются — частичное распознавание не должно
        сбрасывать уже введённый маршрут. Список точек заменяет таблицу
        целиком (как в CargoTab), но пустой список её не трогает: «точек не
        распознано» и «стереть введённое» — разные вещи.
        """
        if not data:
            return

        route = str(data.get("route") or "").strip()
        if route:
            self.route.setText(route)

        self._fill_points(self.shippers_table, data.get("shippers"))
        self._fill_points(self.consignees_table, data.get("consignees"))

        if data.get("loading_date"):
            self._set_date(self.loading_date, data["loading_date"])
        if data.get("unloading_date"):
            self._set_date(self.unloading_date, data["unloading_date"])

        self._set_time(self.loading_time_from, data.get("loading_time_from"))
        self._set_time(self.loading_time_to, data.get("loading_time_to"))
        self._set_time(self.unloading_time_from, data.get("unloading_time_from"))
        self._set_time(self.unloading_time_to, data.get("unloading_time_to"))

        logger.info("Логистикс Рус: данные маршрута заполнены")

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
        """Очищает маршрут и возвращает план к значениям по умолчанию."""
        self.route.clear()

        for table in (self.shippers_table, self.consignees_table):
            table.blockSignals(True)
            try:
                table.setRowCount(0)
                table.setRowCount(MIN_ROWS)
                for row in range(MIN_ROWS):
                    self._init_point_row(table, row)
                table.clearSelection()
            finally:
                table.blockSignals(False)

        self.loading_date.setDate(QDate.currentDate())
        self.unloading_date.setDate(
            QDate.currentDate().addDays(DEFAULT_UNLOADING_DAYS)
        )
        self.loading_time_from.setTime(DEFAULT_LOADING_TIME_FROM)
        self.loading_time_to.setTime(DEFAULT_LOADING_TIME_TO)
        self.unloading_time_from.setTime(DEFAULT_UNLOADING_TIME_FROM)
        self.unloading_time_to.setTime(DEFAULT_UNLOADING_TIME_TO)
        self.recognition_panel.clear()

        logger.debug("Логистикс Рус: поля маршрута очищены")


__all__ = [
    "RouteTab",
    "MAX_POINTS",
    "MIN_ROWS",
    "DEFAULT_LOADING_TIME_FROM",
    "DEFAULT_LOADING_TIME_TO",
    "DEFAULT_UNLOADING_TIME_FROM",
    "DEFAULT_UNLOADING_TIME_TO",
    "DEFAULT_UNLOADING_DAYS",
]
