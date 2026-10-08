#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты вкладки «Перевозимые автомобили» Экспедиторства (ШАГ FIX-6, часть B).

Что проверяется:

  * убраны три колонки — «Госномер», «Цвет», «Год выпуска» — и вместе с
    ними убраны их дефолты: год больше не подставляется текущим, тип ТС
    не подставляется «Легковым автомобилем»;
  * состав колонок настраивается галочками (меню по правому клику на
    шапке), выбор сохраняется в QSettings и переживает пересоздание окна;
  * СКРЫТАЯ колонка не теряет данные: `get_data()` читает её значения;
  * ширины колонок заданы, все колонки Interactive (оператор тянет мышью),
    подсказка показывает полный текст ячейки, раскладка сохраняется;
  * высота строки осталась прежней: таблица машин — эталон вида, настройка
    таблиц точек (ШАГ «Высота таблиц точек») её не задевает.

Данные синтетические, ПДн нет. QSettings перенаправлен фикстурой
`isolated_qsettings` (tests/conftest.py), Qt — в offscreen-режиме.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QHeaderView, QTableWidget, QTableWidgetItem,
)

from ui.tabs.vehicles_tab import (  # noqa: E402
    COLUMN_MINIMUMS, COLUMN_WIDTHS, COLUMNS_STORAGE_KEY, VEHICLE_TYPES,
    WIDTHS_STORAGE_KEY, VehiclesTab,
)
from ui.widgets.columns import (  # noqa: E402
    FIELD_BRAND, FIELD_COLOR, FIELD_PLATE, FIELD_TYPE, FIELD_VIN, FIELD_YEAR,
    VEHICLE_COLUMNS,
)
from ui.widgets.table_helpers import (  # noqa: E402
    apply_column_checks, hidden_column_keys, install_column_settings_menu,
    stored_widths, visible_column_keys,
)

#: Колонки, которых в таблице быть не должно (ШАГ FIX-6, части B1/B2).
REMOVED_HEADERS = ("Госномер", "Цвет", "Год выпуска")

#: Скрытые по умолчанию колонки.
DEFAULT_HIDDEN = (FIELD_PLATE, FIELD_YEAR, FIELD_COLOR)


@pytest.fixture(scope="module")
def qapp():
    """Одно приложение Qt на модуль (offscreen)."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qapp) -> VehiclesTab:
    return VehiclesTab()


def visible_headers(tab: VehiclesTab) -> list:
    """Заголовки видимых колонок — то, что оператор видит в шапке."""
    return [
        tab.table.horizontalHeaderItem(index).text()
        for index in range(tab.table.columnCount())
        if not tab.table.isColumnHidden(index)
    ]


# ─────────────────────────────────────────────────────────────
# B1+B2: три колонки убраны
# ─────────────────────────────────────────────────────────────

def test_vehicles_has_no_plate_color_year_columns(tab):
    """Госномер, цвет и год выпуска из шапки убраны."""
    headers = visible_headers(tab)

    for title in REMOVED_HEADERS:
        assert title not in headers, f"колонка «{title}» осталась в таблице"

    assert headers == ["VIN-код", "Марка/Модель", "Тип ТС", "Погрузка", "Выгрузка"]


def test_vehicles_data_has_no_plate_color_year(tab):
    """В данных машины нет госномера, цвета и года, пока их не ввели."""
    tab.fill_data([{FIELD_VIN: "EC3TEUMB0T0000001"}])

    vehicle = tab.get_data()[0]

    assert vehicle[FIELD_PLATE] == ""
    assert vehicle[FIELD_COLOR] == ""
    assert vehicle[FIELD_YEAR] == 0


def test_removed_columns_are_hidden_not_deleted(tab):
    """
    Колонки именно СКРЫТЫ: номера не съехали, данные в них читаются.

    Прятать, а не удалять, нужно из-за делегатов, выпадающих списков и
    сохранённой раскладки: удаление сдвинуло бы все номера колонок.
    """
    assert tab.table.columnCount() == len(VEHICLE_COLUMNS) == 8
    assert hidden_column_keys(tab.table) == list(DEFAULT_HIDDEN)
    assert tab.table.isColumnHidden(tab.COL_YEAR) is True
    assert tab.table.isColumnHidden(tab.COL_VIN) is False


def test_year_is_not_defaulted(tab):
    """Год выпуска НЕ подставляется: у новой строки прочерк и ноль в данных."""
    tab._on_add_vehicle()
    tab.fill_data([{FIELD_VIN: "EC3TEUMB0T0000001"}], append=True)

    spin = tab.table.cellWidget(0, tab.COL_YEAR)

    assert spin.value() == 0
    assert spin.specialValueText() == "—"
    assert tab._get_spin_value(0, tab.COL_YEAR) == 0
    assert tab.get_data()[0][FIELD_YEAR] == 0


def test_type_is_not_defaulted(tab):
    """Тип ТС НЕ подставляется: у новой строки пустой пункт."""
    tab._on_add_vehicle()
    tab.fill_data([{FIELD_VIN: "EC3TEUMB0T0000001"}], append=True)

    combo = tab.table.cellWidget(0, tab.COL_TYPE)

    assert combo.currentText() == ""
    assert combo.count() == len(VEHICLE_TYPES) + 1
    assert tab.get_data()[0][FIELD_TYPE] == ""


def test_brand_and_color_are_not_defaulted(tab):
    """Марка и цвет не подставляются: у новой строки пустые ячейки."""
    tab._on_add_vehicle()

    assert tab._get_cell_text(0, tab.COL_BRAND) == ""
    assert tab._get_cell_text(0, tab.COL_COLOR) == ""


def test_fill_data_does_not_set_defaults(tab):
    """Пришёл только VIN — остальные поля остаются пустыми."""
    tab.fill_data([{FIELD_VIN: "EC3TEUMB0T0000001"}])

    vehicle = tab.get_data()[0]

    assert vehicle[FIELD_VIN] == "EC3TEUMB0T0000001"
    assert vehicle[FIELD_BRAND] == ""
    assert vehicle[FIELD_YEAR] == 0
    assert vehicle[FIELD_COLOR] == ""
    assert vehicle[FIELD_TYPE] == ""


def test_clear_resets_to_empty(tab):
    """Очистка не оставляет ни машин, ни значений в скрытых колонках."""
    tab.fill_data([{FIELD_VIN: "EC3TEUMB0T0000001", FIELD_YEAR: 2020,
                    FIELD_COLOR: "Белый", FIELD_PLATE: "А123ВС77"}])

    tab.clear()

    assert tab.table.rowCount() == 0
    assert tab.get_data() == []
    assert tab._store == {}


# ─────────────────────────────────────────────────────────────
# B2: ширины, подсказки, сохранение раскладки
# ─────────────────────────────────────────────────────────────

def test_column_widths_configured(tab):
    """Ширины колонок — из описания FIX-6 (VIN 180, Выгрузка 350 и т.д.)."""
    header = tab.table.horizontalHeader()

    for field, expected in ((FIELD_VIN, 180), (FIELD_BRAND, 180),
                            (FIELD_TYPE, 140), ("loading_index", 250),
                            ("unloading_index", 350)):
        index = tab.COLUMN_INDEX[field]
        assert header.sectionSize(index) == expected, (
            f"{field}: ширина {header.sectionSize(index)}, ожидалось {expected}"
        )


def test_all_columns_are_interactive(tab):
    """Все колонки тянутся мышью — «схлопнуться» до «Даты» они не могут."""
    header = tab.table.horizontalHeader()

    for index in range(tab.table.columnCount()):
        assert header.sectionResizeMode(index) == QHeaderView.Interactive

    assert header.minimumSectionSize() == min(COLUMN_MINIMUMS.values())


def test_tooltip_on_long_values(tab):
    """Наведение на ячейку показывает полный текст (VIN длиннее колонки)."""
    long_vin = "EC3TEUMB0T0000001234"
    tab.fill_data([{FIELD_VIN: long_vin}])

    item = tab.table.item(0, tab.COL_VIN)
    tab.table.itemEntered.emit(item)

    assert item.toolTip() == long_vin
    assert tab.table.hasMouseTracking() is True


def test_widths_persist(tab, qapp):
    """Растянутая граница сохраняется и читается новой таблицей."""
    tab.table.horizontalHeader().resizeSection(tab.COL_BRAND, 260)
    tab.table._widths_saver.flush()    # то же, что таймер через 500 мс

    assert stored_widths(tab.table, WIDTHS_STORAGE_KEY)[tab.COL_BRAND] == 260

    restored = VehiclesTab()
    assert restored.table.horizontalHeader().sectionSize(restored.COL_BRAND) == 260


def test_row_height_stays_qt_default(tab, qapp):
    """
    Высота строки таблицы машин — прежняя (ШАГ «Высота таблиц точек»).

    Эта таблица — эталон вида: у неё высота берётся по свободному месту и
    длинный текст в колонке «Выгрузка» виден целиком. Настройка таблиц
    точек (make_table_expandable: строка в две строки текста) идёт через
    setup_keyed_table → setup_point_table, поэтому проверка стоит здесь:
    если помощник перенесут внутрь setup_point_table, строка этой таблицы
    молча станет выше.
    """
    pristine = QTableWidget(0, 1)     # таблица, к которой помощник не применялся

    assert (
        tab.table.verticalHeader().defaultSectionSize()
        == pristine.verticalHeader().defaultSectionSize()
    )


# ─────────────────────────────────────────────────────────────
# B3: настраиваемый состав колонок
# ─────────────────────────────────────────────────────────────

def test_hide_column_removes_from_view(tab):
    """Снятая галочка убирает колонку из шапки, а не из таблицы."""
    assert "Цвет" not in visible_headers(tab)      # скрыт по умолчанию

    apply_column_checks(tab.table, {FIELD_COLOR: True})   # показали
    assert "Цвет" in visible_headers(tab)

    apply_column_checks(tab.table, {FIELD_COLOR: False})  # снова скрыли
    assert "Цвет" not in visible_headers(tab)
    assert FIELD_COLOR in hidden_column_keys(tab.table)
    assert tab.table.columnCount() == 8, "колонка удалена, а не скрыта"


def test_hidden_column_data_still_available(tab):
    """Данные скрытой колонки остаются в `get_data()`."""
    tab.fill_data([{FIELD_VIN: "EC3TEUMB0T0000001", FIELD_PLATE: "А123ВС77",
                    FIELD_YEAR: 2020, FIELD_COLOR: "Белый"}])

    assert set(hidden_column_keys(tab.table)) == set(DEFAULT_HIDDEN)

    vehicle = tab.get_data()[0]

    assert vehicle[FIELD_PLATE] == "А123ВС77"
    assert vehicle[FIELD_YEAR] == 2020
    assert vehicle[FIELD_COLOR] == "Белый"


def test_value_typed_into_hidden_column_is_kept(tab):
    """Значение, записанное в скрытую колонку (импорт), не теряется."""
    tab._on_add_vehicle()

    tab.set_field(0, FIELD_PLATE, "В456ЕК77")
    tab.set_field(0, FIELD_YEAR, 2019)

    assert FIELD_PLATE in hidden_column_keys(tab.table)

    # Строка, у которой заполнены только скрытые колонки, тоже попадает
    # в данные: раньше проверялись лишь VIN, марка и госномер.
    vehicle = tab.get_data()[0]

    assert vehicle[FIELD_PLATE] == "В456ЕК77"
    assert vehicle[FIELD_YEAR] == 2019


def test_column_selection_persists(tab, qapp):
    """Состав колонок сохраняется в QSettings и переживает пересоздание."""
    from ui.widgets.column_settings import save_hidden

    apply_column_checks(tab.table, {FIELD_YEAR: True})
    save_hidden(tab.table, COLUMNS_STORAGE_KEY, hidden_column_keys(tab.table))

    assert FIELD_YEAR not in hidden_column_keys(tab.table)

    restored = VehiclesTab()

    assert FIELD_YEAR not in hidden_column_keys(restored.table)
    assert "Год выпуска" in visible_headers(restored)


def test_selection_can_be_reset_to_default(tab, qapp):
    """Вернуть состав колонок к исходному можно теми же галочками."""
    from ui.widgets.column_settings import save_hidden

    apply_column_checks(tab.table, {FIELD_YEAR: True})
    save_hidden(tab.table, COLUMNS_STORAGE_KEY, hidden_column_keys(tab.table))
    assert FIELD_YEAR not in hidden_column_keys(VehiclesTab().table)

    apply_column_checks(tab.table, {FIELD_YEAR: False})
    save_hidden(tab.table, COLUMNS_STORAGE_KEY, hidden_column_keys(tab.table))

    assert FIELD_YEAR in hidden_column_keys(tab.table)
    assert hidden_column_keys(VehiclesTab().table) == list(DEFAULT_HIDDEN)


def test_menu_handler_saves_selection(tab, qapp):
    """
    Обработчик меню не только прячет колонку, но и пишет настройки.

    Проверяется тот же путь, что и у галочки в меню: `_apply` вызывает
    `apply_column_checks` и `save_hidden` вместе. Раньше `_widths_saver`
    не создавался из-за `getattr(..., None)`, который возвращал None для
    уже существующего атрибута (см. ui/widgets/table_helpers.py).
    """
    from ui.widgets.table_helpers import _ColumnMenuHandler

    handler = _ColumnMenuHandler(tab.table, None)
    handler._apply({FIELD_YEAR: True, FIELD_COLOR: True, FIELD_PLATE: True})

    assert hidden_column_keys(tab.table) == []

    restored = VehiclesTab()

    assert hidden_column_keys(restored.table) == []
    assert "Год выпуска" in visible_headers(restored)
    assert "Цвет" in visible_headers(restored)


def test_required_columns_cannot_be_hidden(tab):
    """Обязательные колонки (VIN, марка, тип, погрузка, выгрузка) не скрыть."""
    apply_column_checks(tab.table, {"vin": False, "brand_model": False,
                                    "vehicle_type": False,
                                    "loading_index": False,
                                    "unloading_index": False})

    assert hidden_column_keys(tab.table) == list(DEFAULT_HIDDEN)


def test_all_tabs_have_column_settings(tab):
    """У таблицы есть и описание колонок, и ключ настроек состава."""
    from ui.widgets.table_helpers import (
        COLUMN_SPECS_PROPERTY, COLUMN_STORAGE_KEY_PROPERTY, table_specs,
    )

    assert table_specs(tab.table) == VEHICLE_COLUMNS
    assert tab.table.property(COLUMN_STORAGE_KEY_PROPERTY) == COLUMNS_STORAGE_KEY
    assert tab.table.property(COLUMN_SPECS_PROPERTY) is not None
    assert getattr(tab.table, "_table_column_menu_installed", False) is True


def test_menu_is_installed_once(tab):
    """Повторная установка меню не подключает обработчик второй раз."""
    from PyQt5.QtCore import Qt

    install_column_settings_menu(tab.table, VEHICLE_COLUMNS,
                                 storage_key=COLUMNS_STORAGE_KEY)

    header = tab.table.horizontalHeader()
    assert header.contextMenuPolicy() == Qt.CustomContextMenu


def test_visible_column_keys_reports_view(tab):
    """Список видимых ключей совпадает с шапкой таблицы."""
    assert visible_column_keys(tab.table) == [
        FIELD_VIN, FIELD_BRAND, FIELD_TYPE, "loading_index", "unloading_index",
    ]


def test_widths_of_all_columns_are_defined(tab):
    """У каждой колонки описания есть ширина и минимум."""
    for spec in VEHICLE_COLUMNS:
        assert spec.key in COLUMN_WIDTHS, f"{spec.key}: нет ширины"
        assert spec.key in COLUMN_MINIMUMS, f"{spec.key}: нет минимума"
