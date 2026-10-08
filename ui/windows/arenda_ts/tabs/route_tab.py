#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Маршрут» окна типа «Разовая аренда» (ЭТАП 3.1.D.B.2).

Маршрут аренды — направление и точки: в бланке это раздел 3.4 (маршрут),
3.2 (до 10 точек погрузки) и 3.3 (до 10 точек выгрузки). Образец таблиц с
точками — ui/tabs/contract_tab.py (loadings_table / unloadings_table):
кнопки «Добавить» / «Удалить», не больше 10 строк. Пустой таблица тоже
может быть: удалить разрешено и последнюю строку (ШАГ FIX-5), а о пустом
разделе маршрута скажет валидатор типа.
Отличие от Логистикс Рус (ui/windows/logistiks_rus/tabs/route_tab.py): там
у точки только наименование и адрес, а дата и время — общие для всего плана,
здесь у каждой точки погрузки своя дата и своё время подачи ТС («с» и «по»).

Время в таблице вводится строкой «HH:mm» (в бланке печатается «с 08:00 до
18:00»), дата — строкой в любом формате, который понимает общий разбор
(core.dates.parse_date): в данные она уходит в ISO.

Ключи get_data() — route, loadings, unloadings — читает
ui/windows/arenda_ts/data.py::_build_route. Точка — словарь
{name, address, date, time_from, time_to} у погрузки и
{name, address, date} у выгрузки; пустые строки таблиц в данные не попадают.

Колонка «Дата» таблицы точек выгрузки в договор не идёт: п. 3.3.2 бланка
печатает планируемую дату завершения рейса — отдельное поле вкладки «ТС»
(planned_completion_date). Колонка остаётся справочной, и об этом
пользователю говорит подсказка ячейки (UNLOADING_DATE_TOOLTIP, шаг
FIX-1-T2): значение и поведение вкладки не меняются.
"""

import logging
from typing import Any, Dict, List, Mapping

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QGroupBox, QHBoxLayout, QMessageBox,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.dates import parse_date
from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableLineEdit, RecognitionPanel
from ui.widgets.table_helpers import (
    MODE_CONTENTS, MODE_FIXED, MODE_STRETCH,
    install_tooltip_on_table, make_table_expandable, setup_point_table,
)

logger = logging.getLogger("ui.windows.arenda_ts.tabs.route_tab")

#: Сколько точек помещается в бланк — разделы 3.2 и 3.3.
#: Значение совпадает с ui/windows/arenda_ts/data.py::MAX_POINTS.
MAX_POINTS = 10

#: Сколько строк показывать при открытии вкладки. Это НЕ минимум: строки
#: можно удалить все — пустая таблица точек норма (ШАГ FIX-5), а пустой
#: раздел маршрута поймает валидатор типа.
MIN_ROWS = 1

#: Колонки таблиц точек маршрута.
COL_NAME = 0
COL_ADDRESS = 1
COL_DATE = 2
COL_TIME_FROM = 3
COL_TIME_TO = 4

#: Заголовки колонок: у погрузки есть время подачи ТС, у выгрузки — нет.
LOADING_HEADERS = ["Наименование", "Адрес", "Дата", "Время с", "Время по"]
UNLOADING_HEADERS = ["Наименование", "Адрес", "Дата"]

#: Режимы и ширины колонок таблиц точек (ШАГ FIX-5): адрес — главная
#: колонка, тянется по ширине таблицы; наименование — по содержимому;
#: дата и время фиксированные: в них 10 и 5 символов.
LOADING_COLUMNS_CONFIG = (
    (COL_NAME, MODE_CONTENTS, 0),
    (COL_ADDRESS, MODE_STRETCH, 0),
    (COL_DATE, MODE_FIXED, 90),
    (COL_TIME_FROM, MODE_FIXED, 80),
    (COL_TIME_TO, MODE_FIXED, 80),
)
UNLOADING_COLUMNS_CONFIG = (
    (COL_NAME, MODE_CONTENTS, 0),
    (COL_ADDRESS, MODE_STRETCH, 0),
    (COL_DATE, MODE_FIXED, 90),
)

#: Нижние границы ширин: «Дат» и «Вре» в шапке — это слишком узкие колонки.
LOADING_COLUMN_MINIMUMS = {
    COL_NAME: 100, COL_DATE: 80, COL_TIME_FROM: 70, COL_TIME_TO: 70,
}
UNLOADING_COLUMN_MINIMUMS = {COL_NAME: 100, COL_DATE: 80}

#: Ключи QSettings для раскладки колонок (у таблиц она своя).
LOADINGS_WIDTHS_KEY = "ui/arenda_ts/loadings_columns"
UNLOADINGS_WIDTHS_KEY = "ui/arenda_ts/unloadings_columns"

#: Нижняя граница ВЫСОТЫ таблиц точек: шапка (около 21) плюс две полные
#: строки по 40 пикселей. Было 90 — видно было полторы строки. Верхние
#: границы у погрузки и выгрузки свои (180 и 160), ниже минимум не бывает.
POINT_TABLE_MIN_HEIGHT = 120
LOADING_TABLE_MAX_HEIGHT = 180
UNLOADING_TABLE_MAX_HEIGHT = 160

#: Пояснение к колонке «Дата» таблицы точек ВЫГРУЗКИ (шаг FIX-1-T2).
#: В договор эта колонка не идёт: п. 3.3.2 бланка печатает планируемую дату
#: завершения рейса — отдельное поле вкладки «ТС» (planned_completion_date).
#: Колонка остаётся справочной: по ней видно, к какой точке относится дата
#: завершения, а поведение вкладки не меняется.
UNLOADING_DATE_TOOLTIP = (
    "Справочно. В договор идёт планируемая дата завершения рейса "
    "(вкладка ТС)"
)


class RouteTab(TabMixin, QWidget):
    """Направление аренды и точки погрузки и выгрузки."""

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
            placeholder="Вставьте текст с маршрутом и точками погрузки и выгрузки..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Маршрут» ──
        route_group, route_layout = theme.section_box("Маршрут")

        self.route = PasteableLineEdit("Москва - Казань")
        route_layout.addRow("Маршрут", self.route)

        layout.addWidget(route_group)

        # ── Таблица «Точки погрузки» ──
        loading_group = QGroupBox(f"Точки погрузки (до {MAX_POINTS})")
        loading_layout = QVBoxLayout(loading_group)

        loading_buttons = QHBoxLayout()
        self.btn_add_loading = theme.secondary_button(
            "Добавить погрузку", tooltip="Добавить строку точки погрузки"
        )
        self.btn_add_loading.clicked.connect(self._on_add_loading)
        loading_buttons.addWidget(self.btn_add_loading)

        self.btn_remove_loading = theme.danger_button(
            "Удалить погрузку", tooltip="Удалить выбранную строку"
        )
        self.btn_remove_loading.clicked.connect(self._on_remove_loading)
        loading_buttons.addWidget(self.btn_remove_loading)

        loading_buttons.addStretch()
        loading_layout.addLayout(loading_buttons)

        self.loadings_table = self._create_table(LOADING_HEADERS, width_for_time=True)
        loading_layout.addWidget(self.loadings_table)

        layout.addWidget(loading_group)

        # ── Таблица «Точки выгрузки» ──
        unloading_group = QGroupBox(f"Точки выгрузки (до {MAX_POINTS})")
        unloading_layout = QVBoxLayout(unloading_group)

        unloading_buttons = QHBoxLayout()
        self.btn_add_unloading = theme.secondary_button(
            "Добавить выгрузку", tooltip="Добавить строку точки выгрузки"
        )
        self.btn_add_unloading.clicked.connect(self._on_add_unloading)
        unloading_buttons.addWidget(self.btn_add_unloading)

        self.btn_remove_unloading = theme.danger_button(
            "Удалить выгрузку", tooltip="Удалить выбранную строку"
        )
        self.btn_remove_unloading.clicked.connect(self._on_remove_unloading)
        unloading_buttons.addWidget(self.btn_remove_unloading)

        unloading_buttons.addStretch()
        unloading_layout.addLayout(unloading_buttons)

        self.unloadings_table = self._create_table(
            UNLOADING_HEADERS, width_for_time=False
        )
        unloading_layout.addWidget(self.unloadings_table)

        layout.addWidget(unloading_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Разовая аренда RouteTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Виджеты вкладки
    # ─────────────────────────────────────────────────────────

    def _create_table(self, headers: List[str], *, width_for_time: bool) -> QTableWidget:
        """
        Пустая таблица точек маршрута с одной строкой.

        Ширины задаёт общий помощник (ui/widgets/table_helpers.py): адрес
        тянется по ширине, наименование идёт по содержимому, дата и время
        фиксированные — в них всегда 10 и 5 символов. Раскладка колонок
        запоминается между сеансами (ШАГ FIX-5). У таблицы точек выгрузки
        колонка «Дата» получает пояснение (UNLOADING_DATE_TOOLTIP): в договор
        эта колонка не идёт, а молчащее поле выглядело бы ошибкой.
        """
        table = QTableWidget(MIN_ROWS, len(headers))
        table.setHorizontalHeaderLabels(headers)
        columns_config = (
            LOADING_COLUMNS_CONFIG if width_for_time else UNLOADING_COLUMNS_CONFIG
        )
        table_minimums = (
            LOADING_COLUMN_MINIMUMS if width_for_time
            else UNLOADING_COLUMN_MINIMUMS
        )
        setup_point_table(
            table,
            columns_config,
            storage_key=(
                LOADINGS_WIDTHS_KEY if width_for_time else UNLOADINGS_WIDTHS_KEY
            ),
            minimums=table_minimums,
        )
        install_tooltip_on_table(table)
        make_table_expandable(table)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setMinimumHeight(POINT_TABLE_MIN_HEIGHT)
        table.setMaximumHeight(
            LOADING_TABLE_MAX_HEIGHT if width_for_time
            else UNLOADING_TABLE_MAX_HEIGHT
        )
        # Колонка «Дата» есть и у погрузки, и у выгрузки, но пояснение нужно
        # только выгрузке: дата погрузки — это плановая дата подачи ТС, она
        # печатается в бланке (п. 3.2).
        tooltip = "" if width_for_time else UNLOADING_DATE_TOOLTIP
        if tooltip:
            # Подсказка стоит и на заголовке: пользователь ведёт мышь к шапке
            # колонки, а не к пустой ячейке.
            header_item = table.horizontalHeaderItem(COL_DATE)
            if header_item is not None:
                header_item.setToolTip(tooltip)
        for row in range(MIN_ROWS):
            self._init_row(table, len(headers), row, date_tooltip=tooltip)
        return table

    @staticmethod
    def _init_row(
        table: QTableWidget,
        columns: int,
        row: int,
        *,
        date_tooltip: str = "",
    ) -> None:
        """
        Пустая строка точки маршрута.

        date_tooltip — пояснение к ячейке «Дата» (у выгрузки — «справочно»);
        пустая строка оставляет ячейку без подсказки.
        """
        for column in range(columns):
            tooltip = date_tooltip if column == COL_DATE else ""
            item = QTableWidgetItem("")
            if tooltip:
                item.setToolTip(tooltip)
            table.setItem(row, column, item)

    @staticmethod
    def _cell_text(table: QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        return item.text().strip() if item else ""

    # ─────────────────────────────────────────────────────────
    # Строки таблиц
    # ─────────────────────────────────────────────────────────

    def _date_tooltip_for(self, table: QTableWidget) -> str:
        """
        Пояснение к колонке «Дата» для конкретной таблицы точек.

        У выгрузки дата в договор не идёт (её печатает п. 3.3.2 из вкладки
        «ТС»), поэтому ячейка получает UNLOADING_DATE_TOOLTIP; у погрузки
        дата — плановая дата подачи ТС, она печатается в бланке (п. 3.2),
        и подсказка там не нужна.
        """
        return UNLOADING_DATE_TOOLTIP if table is self.unloadings_table else ""

    def _on_add_loading(self) -> None:
        """Добавляет строку погрузки; сверх 10 не пускает."""
        self._add_point_row(self.loadings_table, "точку погрузки")

    def _on_remove_loading(self) -> None:
        """Удаляет выбранную строку погрузки."""
        self._remove_point_row(self.loadings_table, "точка погрузки")

    def _on_add_unloading(self) -> None:
        """Добавляет строку выгрузки; сверх 10 не пускает."""
        self._add_point_row(self.unloadings_table, "точку выгрузки")

    def _on_remove_unloading(self) -> None:
        """Удаляет выбранную строку выгрузки."""
        self._remove_point_row(self.unloadings_table, "точка выгрузки")

    def _add_point_row(self, table: QTableWidget, title: str) -> None:
        """Общее добавление строки для обеих таблиц (в connect — без lambda)."""
        row_count = table.rowCount()
        if row_count >= MAX_POINTS:
            logger.info(
                "Разовая аренда: точек уже %s — больше не помещается", row_count
            )
            QMessageBox.warning(
                self, "Ограничение",
                f"В бланк помещается не больше {MAX_POINTS} точек: "
                f"добавить ещё одну {title} нельзя.",
            )
            return

        table.insertRow(row_count)
        self._init_row(
            table, table.columnCount(), row_count,
            date_tooltip=self._date_tooltip_for(table),
        )
        logger.debug("Разовая аренда: добавлена строка (%s)", title)

    def _remove_point_row(self, table: QTableWidget, title: str) -> None:
        """
        Общее удаление строки для обеих таблиц.

        Последнюю строку удалить можно (ШАГ FIX-5): пустая таблица — норма,
        о пустом разделе маршрута скажет валидатор типа.
        """
        row = table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Удаление", "Выберите строку для удаления.")
            return

        table.removeRow(row)
        logger.debug("Разовая аренда: удалена строка (%s)", title)

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Маршрут, точки погрузки и точки выгрузки (даты — в ISO).

        Точки отдаются массивами loadings / unloadings по образцу
        ui/tabs/contract_tab.py; пустые строки таблиц в них не попадают.
        """
        return {
            "route": self.route.text().strip(),
            "loadings": self._read_points(self.loadings_table, with_time=True),
            "unloadings": self._read_points(self.unloadings_table, with_time=False),
        }

    @classmethod
    def _read_points(cls, table: QTableWidget, *, with_time: bool) -> List[Dict[str, str]]:
        """
        Заполненные точки таблицы.

        Погрузка: {name, address, date, time_from, time_to}; выгрузка:
        {name, address, date} — времени подачи ТС у выгрузки в бланке нет.
        Строка, где пусто и наименование, и адрес, точкой не считается:
        иначе в бланк попали бы пустые блоки.
        """
        points: List[Dict[str, str]] = []
        for row in range(table.rowCount()):
            name = cls._cell_text(table, row, COL_NAME)
            address = cls._cell_text(table, row, COL_ADDRESS)
            if not name and not address:
                continue

            point = {
                "name": name,
                "address": address,
                "date": cls._iso_date(cls._cell_text(table, row, COL_DATE)),
            }
            if with_time:
                point["time_from"] = cls._cell_text(table, row, COL_TIME_FROM)
                point["time_to"] = cls._cell_text(table, row, COL_TIME_TO)
            points.append(point)
        return points

    @staticmethod
    def _iso_date(text: str) -> str:
        """
        Дата точки в ISO: «26.09.2026» → «2026-09-26».

        Разобрать не удалось — пустая строка: дата в таблице необязательна,
        и мусор в бланк попадать не должен (о нём скажет валидатор типа).
        """
        if not text:
            return ""

        parsed = parse_date(text, warn=False)
        if parsed is None:
            logger.warning("Разовая аренда: дата точки не распознана (%s символов)", len(text))
            return ""
        return parsed.strftime("%Y-%m-%d")

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет маршрут и таблицы точек.

        Пустые значения игнорируются: частичное распознавание не должно
        сбрасывать уже введённый маршрут. Непустой список точек заменяет
        таблицу целиком (как в CargoTab), пустой — не трогает: «точек не
        распознано» и «стереть введённое» — разные вещи.
        """
        if not data:
            return

        route = str(data.get("route") or "").strip()
        if route:
            self.route.setText(route)

        self._fill_points(self.loadings_table, data.get("loadings"), with_time=True)
        self._fill_points(self.unloadings_table, data.get("unloadings"), with_time=False)

        logger.info("Разовая аренда: данные маршрута заполнены")

    def _fill_points(self, table: QTableWidget, items: Any, *, with_time: bool) -> None:
        """
        Перерисовывает таблицу точек по списку из данных.

        Лишние точки (сверх 10) отбрасываются: строк в бланке ровно 10.
        Не список и пустой список оставляют таблицу как есть.
        """
        if not isinstance(items, (list, tuple)):
            return

        points = [item for item in items if isinstance(item, Mapping)]
        if not points:
            return

        if len(points) > MAX_POINTS:
            logger.warning(
                "Разовая аренда: точек %s, в бланк помещается %s — лишние не выводятся",
                len(points), MAX_POINTS,
            )
            points = points[:MAX_POINTS]

        columns = table.columnCount()
        tooltip = self._date_tooltip_for(table)
        table.blockSignals(True)
        try:
            table.setRowCount(0)
            table.setRowCount(len(points))
            for row, point in enumerate(points):
                self._init_row(table, columns, row, date_tooltip=tooltip)
                table.setItem(
                    row, COL_NAME, QTableWidgetItem(str(point.get("name") or ""))
                )
                table.setItem(
                    row, COL_ADDRESS, QTableWidgetItem(str(point.get("address") or ""))
                )
                table.setItem(
                    row, COL_DATE, QTableWidgetItem(self._date_cell(point.get("date")))
                )
                if tooltip:
                    # Ячейка пересоздана — пояснение к колонке «Дата» возвращаем.
                    table.item(row, COL_DATE).setToolTip(tooltip)
                if with_time:
                    table.setItem(
                        row, COL_TIME_FROM,
                        QTableWidgetItem(str(point.get("time_from") or "")),
                    )
                    table.setItem(
                        row, COL_TIME_TO,
                        QTableWidgetItem(str(point.get("time_to") or "")),
                    )
            table.clearSelection()
        finally:
            table.blockSignals(False)

    @staticmethod
    def _date_cell(value: Any) -> str:
        """Дата в ячейке таблицы: ISO и «дд.мм.гггг» показываются как в бланке."""
        text = str(value or "").strip()
        if not text:
            return ""

        parsed = parse_date(text, warn=False)
        if parsed is None:
            return text
        return parsed.strftime("%d.%m.%Y")

    def clear(self) -> None:
        """Очищает маршрут и возвращает обе таблицы к одной пустой строке."""
        self.route.clear()

        for table in (self.loadings_table, self.unloadings_table):
            columns = table.columnCount()
            tooltip = self._date_tooltip_for(table)
            table.blockSignals(True)
            try:
                table.setRowCount(0)
                table.setRowCount(MIN_ROWS)
                for row in range(MIN_ROWS):
                    self._init_row(table, columns, row, date_tooltip=tooltip)
                table.clearSelection()
            finally:
                table.blockSignals(False)

        self.recognition_panel.clear()

        logger.debug("Разовая аренда: поля маршрута очищены")


__all__ = [
    "RouteTab",
    "MAX_POINTS",
    "MIN_ROWS",
    "COL_NAME",
    "COL_ADDRESS",
    "COL_DATE",
    "COL_TIME_FROM",
    "COL_TIME_TO",
    "LOADING_HEADERS",
    "UNLOADING_HEADERS",
    "LOADING_COLUMNS_CONFIG",
    "UNLOADING_COLUMNS_CONFIG",
    "LOADING_COLUMN_MINIMUMS",
    "UNLOADING_COLUMN_MINIMUMS",
    "LOADINGS_WIDTHS_KEY",
    "UNLOADINGS_WIDTHS_KEY",
    "UNLOADING_DATE_TOOLTIP",
]
