#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Перевозимые автомобили» (Экспедиторство).

Таблица машин с колонками:
  - VIN-код            (обязательная)
  - Марка/Модель       (обязательная)
  - Тип ТС             (обязательная)
  - Погрузка           (выпадающий список точек маршрута)
  - Выгрузка           (выпадающий список точек маршрута)
  - Госномер, Год выпуска, Цвет — необязательные: элементы справочной
    карточки машины, в бланк договора перевозки они не печатаются
    (см. `ui/widgets/columns.py::VEHICLE_COLUMNS`).

ШАГ FIX-6 (часть B):
  * колонки «Госномер», «Цвет» и «Год выпуска» убраны из таблицы
    по умолчанию — оператор их не заполнял, а дефолт «текущий год»
    вводил в заблуждение;
  * состав колонок настраивается: правый клик по шапке → «Какие колонки
    показывать» (галочки), выбор сохраняется в QSettings;
  * ширины: VIN 180, Марка 180, Тип ТС 140, Погрузка 250, Выгрузка 350 —
    все колонки тянутся мышью, лишнее уходит в горизонтальную прокрутку;
  * подсказка показывает полный текст ячейки.

Скрытая колонка НЕ теряет данные: значения лежат в `self._store`
(по строке таблицы) и читаются `get_data()` независимо от того, видна
колонка или нет. Показать колонку обратно — тем же меню.

Списки точек маршрута в колонках «Погрузка» / «Выгрузка» строятся из данных
вкладки «Условия договора»: если у точки есть наименование салона
(ШАГ FIX-2.5), оно показывается перед адресом.
"""

import logging
from typing import Any, Dict, List, Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QMessageBox,
    QHeaderView, QComboBox, QSpinBox,
)
from PyQt5.QtCore import pyqtSignal

from ui.tabs.base_tab import TabMixin
from ui.widgets import RecognitionPanel
from ui.widgets.columns import (
    FIELD_BRAND, FIELD_COLOR, FIELD_LOADING, FIELD_PLATE, FIELD_TYPE,
    FIELD_UNLOADING, FIELD_VIN, FIELD_YEAR,
    VEHICLE_COLUMNS,
)
from ui.widgets.table_helpers import (
    MODE_FIXED, install_column_settings_menu, install_tooltip_on_table,
    setup_keyed_table,
)
from ui import theme

logger = logging.getLogger("ui.tabs.vehicles_tab")

# Значение «не привязано» — попадает во все точки
NO_POINT = "— (все)"

#: Типы ТС для выпадающего списка колонки «Тип ТС».
VEHICLE_TYPES = ["Легковой автомобиль", "Тягач", "Прицеп", "Фургон", "Автобус"]

#: Ключ QSettings: состав колонок и раскладка ширин этой таблицы.
COLUMNS_STORAGE_KEY = "ui/vehicles/columns"
WIDTHS_STORAGE_KEY = "ui/vehicles/widths"

#: Ширины колонок по умолчанию (ШАГ FIX-6, часть B2).
COLUMN_WIDTHS: Dict[str, int] = {
    FIELD_VIN: 180,
    FIELD_BRAND: 180,
    FIELD_TYPE: 140,
    FIELD_LOADING: 250,
    FIELD_UNLOADING: 350,
    FIELD_PLATE: 140,
    FIELD_YEAR: 100,
    FIELD_COLOR: 120,
}

#: Нижние границы: уже этого колонка не сжимается (шапка не «схлопывается»).
COLUMN_MINIMUMS: Dict[str, int] = {
    FIELD_VIN: 120,
    FIELD_BRAND: 140,
    FIELD_TYPE: 110,
    FIELD_LOADING: 160,
    FIELD_UNLOADING: 200,
    FIELD_PLATE: 100,
    FIELD_YEAR: 80,
    FIELD_COLOR: 90,
}


class _NoDefaultSpin(QSpinBox):
    """
    Поле года выпуска без «дефолтного» значения.

    Год не подставляется сам: пустое поле печатает прочерк
    (`setSpecialValueText("—")`), а не текущий год. Раньше вкладка ставила
    текущий год всем машинам — в договоре это выглядело как заполненное
    поле, которого оператор не вводил.
    """

    def __init__(self, year: Any = None):
        super().__init__()
        self.setRange(0, 2100)
        self.setSpecialValueText("—")
        self.set_year(year)

    def set_year(self, year: Any) -> None:
        """Ставит год; пустое или неразбираемое значение — прочерк."""
        try:
            value = int(year or 0)
        except (TypeError, ValueError):
            value = 0
        self.setValue(value if 0 <= value <= 2100 else 0)

    def year(self) -> int:
        """Год числом; 0 — «не указан»."""
        return int(self.value())


class VehiclesTab(TabMixin, QWidget):
    """
    Вкладка с таблицей транспортных средств.
    """

    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    #: Описание колонок: ключ, заголовок, обязательность (ШАГ FIX-6, часть B3).
    #: Номера колонок НЕ меняются при настройке состава — колонки прячутся,
    #: а не удаляются (иначе съехали бы делегаты и сохранённая раскладка).
    COLUMN_SPECS = VEHICLE_COLUMNS

    # Колонки таблицы — номера внутренние, стабильные.
    COL_VIN = 0
    COL_BRAND = 1
    COL_TYPE = 2
    COL_LOADING = 3
    COL_UNLOADING = 4
    COL_PLATE = 5
    COL_YEAR = 6
    COL_COLOR = 7

    #: Заголовки по номерам колонок.
    COLUMNS = [spec.title for spec in VEHICLE_COLUMNS]

    #: Номер колонки по ключу поля (для чтения/записи ячеек).
    COLUMN_INDEX: Dict[str, int] = {
        FIELD_VIN: COL_VIN,
        FIELD_BRAND: COL_BRAND,
        FIELD_TYPE: COL_TYPE,
        FIELD_LOADING: COL_LOADING,
        FIELD_UNLOADING: COL_UNLOADING,
        FIELD_PLATE: COL_PLATE,
        FIELD_YEAR: COL_YEAR,
        FIELD_COLOR: COL_COLOR,
    }

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

        self.btn_add = theme.secondary_button("Добавить ТС")
        self.btn_add.clicked.connect(self._on_add_vehicle)
        button_layout.addWidget(self.btn_add)

        self.btn_remove = theme.danger_button("Удалить ТС")
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

        # ── Значения скрытых колонок живут здесь, а не только в ячейках ──
        # Ключ — строка таблицы; значение — словарь полей машины.
        self._store: Dict[int, Dict[str, Any]] = {}

        self._setup_columns()

        self.table.setEditTriggers(
            QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed
        )

        layout.addWidget(self.table)

        # ── Кэш текущих списков точек ──
        self._loadings_points: List[Dict[str, str]] = []
        self._unloadings_points: List[Dict[str, str]] = []

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        layout.addWidget(self._tab_actions)

        logger.debug(
            "VehiclesTab инициализирована: колонок %s, колонок по умолчанию %s",
            len(self.COLUMNS), len(self.COLUMN_SPECS),
        )

    # ─────────────────────────────────────────────────────────
    # Настройка таблицы: ширины, подсказки, состав колонок
    # ─────────────────────────────────────────────────────────

    def _setup_columns(self) -> None:
        """
        Ширины, подсказки и меню состава колонок (ШАГ FIX-6, часть B).

        Все колонки — Interactive (оператор тянет границы мышью), ширины
        заданы по умолчанию, лишнее уходит в горизонтальную прокрутку
        (политику прокрутки Qt включает сам, когда сумма ширин больше
        ширины таблицы). Раскладка и состав колонок сохраняются в QSettings.
        """
        setup_keyed_table(
            self.table,
            [(spec.key, MODE_FIXED, COLUMN_WIDTHS.get(spec.key, 120))
             for spec in self.COLUMN_SPECS],
            column_index_by_key=self.COLUMN_INDEX,
            storage_key=WIDTHS_STORAGE_KEY,
            minimums=COLUMN_MINIMUMS,
        )

        install_tooltip_on_table(self.table)
        install_column_settings_menu(
            self.table,
            self.COLUMN_SPECS,
            storage_key=COLUMNS_STORAGE_KEY,
            on_changed=self._reapply_column_widths,
        )

    def _reapply_column_widths(self) -> None:
        """
        Возвращает ширины после смены состава колонок.

        Показанная обратно колонка могла остаться с нулевой шириной (её
        ни разу не показывали) — доводим до значения по умолчанию, если
        сохранённой раскладки для неё нет.
        """
        header = self.table.horizontalHeader()
        for key, index in self.COLUMN_INDEX.items():
            if self.table.isColumnHidden(index):
                continue
            if header.sectionSize(index) < COLUMN_MINIMUMS.get(key, 80):
                header.resizeSection(index, COLUMN_WIDTHS.get(key, 120))

    # ─────────────────────────────────────────────────────────
    # Значения строки: ячейка + хранилище скрытых колонок
    # ─────────────────────────────────────────────────────────

    def _cell_text(self, row: int, field: str) -> str:
        """
        Текст ячейки по КЛЮЧУ поля: работает и для скрытой колонки.

        Скрытая колонка остаётся в модели — `item()` её отдаёт как обычно.
        Если ячейки нет вовсе (строка создана до появления колонки),
        значение берётся из хранилища строки: данные не теряются.
        """
        index = self.COLUMN_INDEX.get(field)
        if index is None:
            return ""

        item = self.table.item(row, index)
        if item is not None:
            return item.text().strip()

        stored = self._store.get(row, {}).get(field)
        return "" if stored is None else str(stored).strip()

    def _combo_value(self, row: int, field: str) -> str:
        """Значение выпадающего списка по ключу поля (или пусто)."""
        index = self.COLUMN_INDEX.get(field)
        if index is None:
            return ""

        widget = self.table.cellWidget(row, index)
        if isinstance(widget, QComboBox):
            return widget.currentText()
        return ""

    def _point_index(self, row: int, field: str) -> int:
        """Индекс выбранной точки (1..N) или 0, если «— (все)»."""
        index = self.COLUMN_INDEX.get(field)
        if index is None:
            return 0

        combo = self.table.cellWidget(row, index)
        if not isinstance(combo, QComboBox):
            return 0

        return combo.currentIndex()

    def _year_value(self, row: int) -> int:
        """Год выпуска числом; 0 — «не указан» (пустое поле)."""
        spin = self.table.cellWidget(row, self.COL_YEAR)
        if isinstance(spin, _NoDefaultSpin):
            return spin.year()
        return 0

    def _remember_row(self, row: int) -> None:
        """
        Складывает значения строки в хранилище.

        Нужно перед удалением строки и при перерисовке: если колонку
        прячут, значения её ячеек всё равно должны читаться `get_data()`.
        """
        values: Dict[str, Any] = {}
        for field, index in self.COLUMN_INDEX.items():
            widget = self.table.cellWidget(row, index)
            if isinstance(widget, QComboBox):
                values[field] = widget.currentText()
            elif isinstance(widget, _NoDefaultSpin):
                values[field] = widget.year()
            else:
                item = self.table.item(row, index)
                values[field] = item.text().strip() if item is not None else ""
        self._store[row] = values

    def _drop_row_memory(self, row: int) -> None:
        """Сдвигает хранилище строк после удаления строки `row`."""
        self._store = {
            (key - 1 if key > row else key): value
            for key, value in self._store.items()
            if key != row
        }

    # ─────────────────────────────────────────────────────────
    # Синхронизация списков точек с contract_tab
    # ─────────────────────────────────────────────────────────

    def update_loadings_list(self, loadings: List[Dict[str, str]]) -> None:
        """
        Обновляет выпадающий список погрузок в каждой строке.

        loadings — список словарей {'name', 'address', 'date', 'time_window'}.
        Наименование салона необязательно (ШАГ FIX-2.5): точка, введённая
        руками, приходит без него, и список выглядит как раньше.
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
        Первый элемент — «— (все)», далее «Погрузка 1 (наименование, адрес)».
        Наименование салона идёт первым — как в заголовке блока договора;
        без него в скобках остаётся только адрес.
        Адрес обрезается до 60 символов (было 40 — теперь длиннее, т.к. колонка шире).
        """
        items = [NO_POINT]
        for i, p in enumerate(points, 1):
            address = (p.get("address") or "").strip()
            if len(address) > 60:
                address = address[:60] + "…"

            name = (p.get("name") or p.get("salon_name") or "").strip()
            label = f"{name}, {address}" if name else address
            items.append(f"{title} {i} ({label})")
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
        return combo

    @staticmethod
    def _make_type_combo(vehicle_type: str = "") -> QComboBox:
        """
        Создаёт список типов ТС.

        Первый пункт — пустой: у новой строки тип НЕ выбран. Раньше здесь
        молча стоял «Легковой автомобиль», то есть тип попадал в данные
        без участия оператора (ШАГ FIX-6, часть C).
        """
        combo = QComboBox()
        combo.addItem("")
        combo.addItems(VEHICLE_TYPES)
        if not vehicle_type:
            return combo

        index = combo.findText(vehicle_type)
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    def _init_row(self, row: int, vehicle: Optional[Dict[str, Any]] = None) -> None:
        """
        Заполняет строку таблицы значениями машины.

        Год выпуска НЕ подставляется по умолчанию (ШАГ FIX-6, часть C):
        не пришёл в данных — поле пустое, в нём прочерк.
        """
        vehicle = vehicle or {}

        for field in (FIELD_VIN, FIELD_BRAND, FIELD_PLATE, FIELD_COLOR):
            index = self.COLUMN_INDEX[field]
            item = QTableWidgetItem(self._as_text(vehicle.get(field)))
            self.table.setItem(row, index, item)

        self.table.setCellWidget(
            row, self.COL_TYPE,
            self._make_type_combo(self._as_text(vehicle.get(FIELD_TYPE))),
        )
        self.table.setCellWidget(
            row, self.COL_YEAR, _NoDefaultSpin(vehicle.get(FIELD_YEAR))
        )

        # ── Погрузка / Выгрузка ──
        loading_combo = self._make_combo(self.COL_LOADING)
        unloading_combo = self._make_combo(self.COL_UNLOADING)

        loading_index = vehicle.get(FIELD_LOADING, 0) or 0
        unloading_index = vehicle.get(FIELD_UNLOADING, 0) or 0

        if 0 <= loading_index < loading_combo.count():
            loading_combo.setCurrentIndex(loading_index)
        if 0 <= unloading_index < unloading_combo.count():
            unloading_combo.setCurrentIndex(unloading_index)

        self.table.setCellWidget(row, self.COL_LOADING, loading_combo)
        self.table.setCellWidget(row, self.COL_UNLOADING, unloading_combo)

        self._remember_row(row)

    @staticmethod
    def _as_text(value: Any) -> str:
        """
        Значение ячейки строкой.

        Пустое значение (None, 0, отсутствующий ключ) — пустая строка:
        `0.0 == falsy`, поэтому проверка идёт через `is None` (AGENTS.md § 5.2).
        """
        if value is None:
            return ""
        return str(value).strip()

    # ─────────────────────────────────────────────────────────
    # Добавление / удаление
    # ─────────────────────────────────────────────────────────

    def _on_add_vehicle(self) -> None:
        """Добавляет новую пустую строку в таблицу."""
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._init_row(row)

        logger.debug(f"Добавлено ТС: строка {row}")

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
            self._drop_row_memory(current_row)
            self._refresh_all_combos(
                self.COL_LOADING,
                self._build_point_items(self._loadings_points, "Погрузка"),
            )
            self._refresh_all_combos(
                self.COL_UNLOADING,
                self._build_point_items(self._unloadings_points, "Выгрузка"),
            )
            logger.info(f"ТС удалено: строка {current_row}")

    # ─────────────────────────────────────────────────────────
    # Сбор данных
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> List[Dict[str, Any]]:
        """
        Собирает данные ТС из таблицы.

        Читаются ВСЕ поля строки, включая скрытые колонки: состав колонок —
        это то, что оператор видит, а не то, что попадает в данные. Год и
        цвет не подставляются по умолчанию: нет значения — пусто.
        """
        vehicles = []
        for row in range(self.table.rowCount()):
            self._remember_row(row)

            vehicle = {
                FIELD_VIN: self._cell_text(row, FIELD_VIN),
                FIELD_BRAND: self._cell_text(row, FIELD_BRAND),
                FIELD_PLATE: self._cell_text(row, FIELD_PLATE),
                FIELD_YEAR: self._year_value(row),
                FIELD_COLOR: self._cell_text(row, FIELD_COLOR),
                FIELD_TYPE: self._combo_value(row, FIELD_TYPE),
                FIELD_LOADING: self._point_index(row, FIELD_LOADING),
                FIELD_UNLOADING: self._point_index(row, FIELD_UNLOADING),
            }

            if self._is_filled(vehicle):
                vehicles.append(vehicle)

        return vehicles

    @staticmethod
    def _is_filled(vehicle: Dict[str, Any]) -> bool:
        """
        Строка таблицы считается заполненной, если в ней есть хоть что-то.

        Раньше проверялись только VIN, марка и госномер: строка с одним
        годом или типом ТС молча не попадала в данные.
        """
        for field in (FIELD_VIN, FIELD_BRAND, FIELD_PLATE, FIELD_COLOR, FIELD_TYPE):
            if str(vehicle.get(field) or "").strip():
                return True
        return bool(vehicle.get(FIELD_YEAR))

    # ─────────────────────────────────────────────────────────
    # Заполнение таблицы
    # ─────────────────────────────────────────────────────────

    def fill_data(self, vehicles: List[Dict[str, Any]], *, append=False,
                  imported=False) -> None:
        """
        Заполняет таблицу данными.

        Незаданные поля остаются пустыми: подстановки «текущий год» и
        «Легковой автомобиль» убраны (ШАГ FIX-6, часть C) — они выглядели
        как данные, введённые оператором. Параметр `imported` оставлен для
        совместимости: импорт документов и так приходит со своими значениями.
        """
        if not append:
            self.table.setRowCount(0)
            self._store.clear()

        for vehicle in vehicles:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._init_row(row, vehicle)

        logger.info(f"Таблица ТС заполнена: {len(vehicles)} записей")

    # ─────────────────────────────────────────────────────────
    # Очистка
    # ─────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Очищает таблицу."""
        self.table.setRowCount(0)
        self._store.clear()
        self.recognition_panel.clear()
        logger.debug("Таблица ТС очищена")

    # ─────────────────────────────────────────────────────────
    # Вспомогательные методы
    # ─────────────────────────────────────────────────────────

    def _get_cell_text(self, row: int, col: int) -> str:
        """Текст ячейки по НОМЕРУ колонки (совместимость с импортом документов)."""
        item = self.table.item(row, col)
        if item:
            return item.text().strip()
        return ""

    def _get_spin_value(self, row: int, col: int) -> int:
        """Значение числового поля по НОМЕРУ колонки (год выпуска)."""
        widget = self.table.cellWidget(row, col)
        if isinstance(widget, _NoDefaultSpin):
            return widget.year()
        if isinstance(widget, QSpinBox):
            return widget.value()
        return 0

    def _get_combo_value(self, row: int, col: int) -> str:
        """Значение выпадающего списка по НОМЕРУ колонки."""
        widget = self.table.cellWidget(row, col)
        if isinstance(widget, QComboBox):
            return widget.currentText()
        return ""

    def get_field(self, row: int, field: str) -> Any:
        """
        Значение поля строки по КЛЮЧУ — для импорта документов.

        Работает и когда колонка скрыта: значение лежит в модели/хранилище.
        Год отдаётся числом (0 — не указан), остальные поля — строкой.
        """
        if field == FIELD_YEAR:
            return self._year_value(row)
        if field in (FIELD_TYPE, FIELD_LOADING, FIELD_UNLOADING):
            return self._combo_value(row, field)
        return self._cell_text(row, field)

    def set_field(self, row: int, field: str, value: Any) -> None:
        """
        Ставит значение поля строки по КЛЮЧУ — для импорта документов.

        Колонка может быть скрыта: ячейка всё равно обновляется, поэтому
        подтверждённое в импорте значение не теряется.
        """
        index = self.COLUMN_INDEX.get(field)
        if index is None:
            return

        if field == FIELD_YEAR:
            spin = self.table.cellWidget(row, index)
            if isinstance(spin, _NoDefaultSpin):
                spin.set_year(value)
            return

        if field in (FIELD_TYPE, FIELD_LOADING, FIELD_UNLOADING):
            combo = self.table.cellWidget(row, index)
            if isinstance(combo, QComboBox):
                text = self._as_text(value)
                found = combo.findText(text)
                if found >= 0:
                    combo.setCurrentIndex(found)
            return

        item = self.table.item(row, index)
        if item is None:
            item = QTableWidgetItem("")
            self.table.setItem(row, index, item)
        item.setText(self._as_text(value))
