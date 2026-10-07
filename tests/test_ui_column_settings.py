#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты настраиваемого состава колонок во всех таблицах машин (ШАГ FIX-6, B3).

Таблиц «Перевозимые авто» в проекте пять — по одной на тип договора:

  * Экспедиторство — `ui/tabs/vehicles_tab.py` (полный набор: VIN, марка,
    тип ТС, погрузка, выгрузка + скрытые по умолчанию госномер, год, цвет);
  * Логистикс Рус — `ui/windows/logistiks_rus/tabs/cargo_tab.py`;
  * Разовая аренда — `ui/windows/arenda_ts/tabs/cargo_tab.py`;
  * Формика — `ui/windows/formika/tabs/cargo_tab.py`;
  * Хавалы — `ui/windows/havaly/tabs/cargo_tab.py`.

Проверяется одно и то же для всех пяти:

  * у таблицы есть описание колонок, ключ настроек и меню «какие колонки
    показывать» (правый клик по шапке);
  * снятая галочка убирает колонку из шапки, но НЕ из таблицы: номера
    колонок остаются на месте;
  * данные скрытой колонки читаются `get_data()` — состав колонок это
    то, что видно, а не то, что попадает в договор;
  * обязательные колонки (VIN и марка) скрыть нельзя;
  * выбор сохраняется в QSettings и переживает пересоздание вкладки.

Данные синтетические, ПДн нет. QSettings перенаправлен фикстурой
`isolated_qsettings` (tests/conftest.py), Qt — в offscreen-режиме.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QTableWidgetItem,
)

from ui.widgets.table_helpers import (  # noqa: E402
    COLUMN_SPECS_PROPERTY, COLUMN_STORAGE_KEY_PROPERTY, apply_column_checks,
    hidden_column_keys, install_column_settings_menu, table_specs,
    visible_column_keys,
)

#: Ключи колонок и заполняемые поля у каждой таблицы.
#: «данные» — что положить в таблицу, «скрытая» — какую колонку прячем.
EXTRA_HAVALY = ("model", "dealer", "dealer_code")


@pytest.fixture(scope="module")
def qapp():
    """Одно приложение Qt на модуль (offscreen)."""
    app = QApplication.instance() or QApplication([])
    yield app


def _expedition_tab():
    from ui.tabs.vehicles_tab import VehiclesTab

    return VehiclesTab(), "table", "vin", 1


def _logistiks_tab():
    from ui.windows.logistiks_rus.tabs.cargo_tab import CargoTab

    return CargoTab(), "vehicles_table", "vin", 2


def _arenda_tab():
    from ui.windows.arenda_ts.tabs.cargo_tab import CargoTab

    return CargoTab(), "vehicles_table", "vin", 2


def _formika_tab():
    from ui.windows.formika.tabs.cargo_tab import CargoTab

    return CargoTab(), "vehicles_table", "vin", 2


def _havaly_tab():
    from ui.windows.havaly.tabs.cargo_tab import CargoTab

    return CargoTab(), "vehicles_table", "vin", 1


#: (название, фабрика вкладки, ключ колонки VIN, ключ обязательной марки)
TABS = (
    ("Экспедиторство", _expedition_tab, "vin", "brand_model"),
    ("Логистикс Рус", _logistiks_tab, "vin", "brand_model"),
    ("Разовая аренда", _arenda_tab, "vin", "brand_model"),
    ("Формика", _formika_tab, "vin", "brand_model"),
    ("Хавалы", _havaly_tab, "vin", "brand"),
)


@pytest.mark.parametrize("title, factory, vin_key, brand_key", TABS,
                         ids=[item[0] for item in TABS])
def test_all_tabs_have_column_settings(qapp, title, factory, vin_key, brand_key):
    """У каждой таблицы машин есть описание колонок, ключ и меню."""
    tab, table_attr, _vin_col, _extra = factory()
    try:
        table = getattr(tab, table_attr)
        specs = table_specs(table)
        keys = [spec.key for spec in specs]

        assert specs, f"{title}: нет описания колонок"
        assert vin_key in keys, f"{title}: в описании нет VIN"
        assert brand_key in keys, f"{title}: в описании нет марки"
        assert table.property(COLUMN_STORAGE_KEY_PROPERTY), f"{title}: нет ключа настроек"
        assert table.property(COLUMN_SPECS_PROPERTY) is not None
        assert getattr(table, "_table_column_menu_installed", False) is True

        # Заголовки таблицы совпадают с описанием — расхождение ловим сразу.
        headers = [
            table.horizontalHeaderItem(index).text()
            for index in range(table.columnCount())
        ]
        assert headers == [spec.title for spec in specs], f"{title}: шапка ≠ описание"
    finally:
        tab.deleteLater()


@pytest.mark.parametrize("title, factory, vin_key, brand_key", TABS,
                         ids=[item[0] for item in TABS])
def test_required_columns_cannot_be_hidden(qapp, title, factory, vin_key, brand_key):
    """VIN и марку скрыть нельзя: без них строка машины теряет смысл."""
    tab, table_attr, _vin_col, _extra = factory()
    try:
        table = getattr(tab, table_attr)
        before = hidden_column_keys(table)

        apply_column_checks(table, {vin_key: False, brand_key: False})

        assert hidden_column_keys(table) == before, f"{title}: обязательную скрыли"
        assert vin_key in visible_column_keys(table), title
        assert brand_key in visible_column_keys(table), title
    finally:
        tab.deleteLater()


@pytest.mark.parametrize("title, factory, vin_key, brand_key", TABS,
                         ids=[item[0] for item in TABS])
def test_hide_column_removes_from_view(qapp, title, factory, vin_key, brand_key):
    """Скрытие убирает колонку из шапки, но не из таблицы."""
    tab, table_attr, _vin_col, _extra = factory()
    try:
        table = getattr(tab, table_attr)
        optional = [spec.key for spec in table_specs(table) if spec.optional]
        if not optional:
            pytest.skip(f"{title}: все колонки обязательные — скрывать нечего")

        key = optional[0]
        columns_before = table.columnCount()

        apply_column_checks(table, {key: False})

        assert key in hidden_column_keys(table), title
        assert key not in visible_column_keys(table), title
        assert table.columnCount() == columns_before, (
            f"{title}: колонка удалена, а не скрыта"
        )

        apply_column_checks(table, {key: True})

        assert key not in hidden_column_keys(table), title
    finally:
        tab.deleteLater()


def test_hidden_column_data_still_available(qapp):
    """
    Данные скрытой колонки остаются в `get_data()` (Хавалы).

    У вкладки «Груз» Хавалов пять полей машины; прячем дилера с кодом —
    в данных они обязаны остаться, иначе подтверждённое распознаванием
    значение пропало бы из бланка .xlsx.
    """
    from ui.windows.havaly.tabs.cargo_tab import CargoTab

    tab = CargoTab()
    try:
        tab.fill_data({"vehicles": [{
            "vin": "EC3TEUMB0T0000001", "brand": "Jolion",
            "model": "1.5T DCT", "dealer": "Дилер Тест", "dealer_code": "D-01",
        }]})

        apply_column_checks(tab.vehicles_table, {"dealer": False,
                                                 "dealer_code": False})

        assert set(hidden_column_keys(tab.vehicles_table)) == {"dealer", "dealer_code"}

        vehicle = tab.get_data()["vehicles"][0]

        assert vehicle["dealer"] == "Дилер Тест"
        assert vehicle["dealer_code"] == "D-01"
    finally:
        tab.deleteLater()


def test_column_selection_persists_for_cargo_tab(qapp):
    """Выбор состава колонок переживает пересоздание вкладки (Хавалы)."""
    from ui.windows.havaly.tabs import cargo_tab as module

    first = module.CargoTab()
    try:
        install_column_settings_menu(
            first.vehicles_table,
            [("number", "№", True), ("vin", "VIN", True), ("brand", "Марка", True),
             ("model", "Модель", False), ("dealer", "Дилер", False),
             ("dealer_code", "Код дилера", False)],
            storage_key=module.COLUMNS_STORAGE_KEY,
        )
        apply_column_checks(first.vehicles_table, {"model": False})
        from ui.widgets.column_settings import save_hidden

        save_hidden(first.vehicles_table, module.COLUMNS_STORAGE_KEY,
                    hidden_column_keys(first.vehicles_table))
    finally:
        first.deleteLater()

    second = module.CargoTab()
    try:
        assert "model" in hidden_column_keys(second.vehicles_table)
        assert "Модель" not in [
            second.vehicles_table.horizontalHeaderItem(index).text()
            for index in range(second.vehicles_table.columnCount())
            if not second.vehicles_table.isColumnHidden(index)
        ]
    finally:
        second.deleteLater()


def test_hidden_column_keeps_values_set_directly(qapp):
    """
    Значение в скрытую колонку можно записать напрямую (импорт документов).

    Скрытая колонка остаётся в модели, поэтому `setItem` по-прежнему
    работает и данные не теряются. Проверяется на «Цвете» Экспедиторства:
    колонка скрыта по умолчанию, а значение в неё пишет импорт документов.
    """
    from ui.tabs.vehicles_tab import VehiclesTab

    tab = VehiclesTab()
    try:
        assert "color" in hidden_column_keys(tab.table)

        tab.table.insertRow(0)          # у вкладки нет пустых строк «по умолчанию»
        tab.table.setItem(0, tab.COL_VIN, QTableWidgetItem("EC3TEUMB0T0000009"))
        tab.table.setItem(0, tab.COL_BRAND, QTableWidgetItem("МОДЕЛЬ"))
        tab.table.setItem(0, tab.COL_COLOR, QTableWidgetItem("Белый"))
        assert tab.table.isColumnHidden(tab.COL_COLOR) is True

        vehicle = tab.get_data()[0]

        assert vehicle["color"] == "Белый"
        assert vehicle["vin"] == "EC3TEUMB0T0000009"
    finally:
        tab.deleteLater()
