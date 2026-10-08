#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты-аудит всех таблиц проекта (ШАГ FIX-6, часть E).

Задача части E — пройтись по ВСЕМ таблицам, а не только по точкам маршрута
и «Перевозимым авто», и убедиться, что у каждой:

  * колонки не сжимаются до «Дат» и «Вре» — у каждой есть минимальная
    ширина, а у таблиц точек и справочников ещё и сохранённая раскладка;
  * длинное значение можно прочитать: либо колонка тянется, либо работает
    подсказка ячейки;
  * если не влезает — есть горизонтальная прокрутка (Qt включает её сам,
    когда сумма ширин больше ширины таблицы);
  * раскладка сохраняется там, где у таблицы есть ключ QSettings.

Таблиц в проекте девять (плюс диалог импорта документов):

  точки маршрута   — 6 таблиц в трёх типах (Экспедиторство, Логистикс,
                     аренда) + справочник салонов;
  перевозимые авто — 5 таблиц (Экспедиторство, Логистикс, аренда, Формика,
                     Хавалы);
  справочник       — организации, водители (менеджер базы), салоны.

Проверки идут по ЖИВЫМ виджетам: таблица создаётся в offscreen-режиме и
опрашивается, а не сверяется с текстом исходника.

Данные синтетические, ПДн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt5")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QAbstractItemView, QApplication, QHeaderView,
)

from ui.widgets.column_settings import (  # noqa: E402
    load_hidden, make_specs, save_hidden,
)
from ui.widgets.table_helpers import (  # noqa: E402
    AUTOSAVE_DELAY_MS, STORAGE_KEY_PROPERTY, WidthsSaver, apply_column_checks,
    hidden_column_keys, install_column_settings_menu, install_tooltip_on_table,
    setup_point_table, stored_widths, table_specs, visible_column_keys,
)


@pytest.fixture(scope="module")
def qapp():
    """Одно приложение Qt на модуль (offscreen)."""
    app = QApplication.instance() or QApplication([])
    yield app


# ─────────────────────────────────────────────────────────────
# Таблицы точек маршрута (6 штук) и справочник салонов
# ─────────────────────────────────────────────────────────────

def _contract_tab_tables(qapp):
    """Таблицы погрузок и выгрузок Экспедиторства."""
    from ui.tabs.contract_tab import ContractTab

    tab = ContractTab()
    return [(tab.loadings_table, "loading"), (tab.unloadings_table, "unloading")], tab


def _logistiks_tables(qapp):
    """Адреса погрузки и грузополучатели Логистикса."""
    from ui.windows.logistiks_rus.tabs.route_tab import RouteTab

    tab = RouteTab()
    return [
        (tab.loading_addresses_table, "loading_addresses"),
        (tab.consignees_table, "consignees"),
    ], tab


def _arenda_tables(qapp):
    """Точки погрузки и выгрузки аренды ТС."""
    from ui.windows.arenda_ts.tabs.route_tab import RouteTab

    tab = RouteTab()
    return [(tab.loadings_table, "loading"), (tab.unloadings_table, "unloading")], tab


#: Пары «название — фабрика списка таблиц»: у каждой таблицы свой ключ QSettings.
POINT_TABLE_GROUPS = (
    ("Экспедиторство — условия договора", _contract_tab_tables),
    ("Логистикс Рус — маршрут", _logistiks_tables),
    ("Разовая аренда — маршрут", _arenda_tables),
)


@pytest.mark.parametrize("title, factory", POINT_TABLE_GROUPS,
                         ids=[item[0] for item in POINT_TABLE_GROUPS])
def test_point_tables_have_widths_and_storage(qapp, title, factory):
    """
    Все таблицы точек: ширины, минимумы, подсказки и ключ раскладки.

    Сами таблицы НЕ удаляются по одной: `deleteLater` у дочернего виджета
    уносит и вкладку-владельца, а вместе с ней — остальные её таблицы
    (проверка падала на «объект уже удалён»). Удаляется вкладка целиком.
    """
    tables, owner = factory(qapp)
    try:
        for table, name in tables:
            header = table.horizontalHeader()

            assert table.columnCount() > 0, f"{title}/{name}: нет колонок"
            assert table.property(STORAGE_KEY_PROPERTY), (
                f"{title}/{name}: нет ключа раскладки"
            )
            assert getattr(table, "_table_tooltips_installed", False) is True, (
                f"{title}/{name}: подсказки не подключены"
            )
            assert header.minimumSectionSize() >= 60, (
                f"{title}/{name}: колонки можно сжать в нитку"
            )

            # Ширина каждой колонки не меньше минимума: заголовок не «схлопнут».
            for column in range(table.columnCount()):
                assert header.sectionSize(column) >= header.minimumSectionSize(), (
                    f"{title}/{name}: колонка {column} уже минимума"
                )
    finally:
        owner.deleteLater()


def test_address_book_table_is_interactive_and_stored(qapp, isolated_db):
    """Справочник салонов: все колонки тянутся мышью, раскладка сохраняется."""
    from ui.address_book_dialog import (
        COL_ADDRESS, COLUMNS_CONFIG, COLUMN_MINIMUMS, WIDTHS_KEY,
        AddressBookDialog,
    )

    dialog = AddressBookDialog("unloading")
    try:
        header = dialog.table.horizontalHeader()

        for column in range(dialog.table.columnCount()):
            assert header.sectionResizeMode(column) == QHeaderView.Interactive

        assert dialog.table.property(STORAGE_KEY_PROPERTY) == WIDTHS_KEY
        assert len(COLUMNS_CONFIG) == dialog.table.columnCount()
        assert set(COLUMN_MINIMUMS) == set(range(dialog.table.columnCount()))

        # Ширины можно менять и они записываются.
        header.resizeSection(COL_ADDRESS, 420)
        dialog.table._widths_saver.flush()
        assert stored_widths(dialog.table, WIDTHS_KEY)[COL_ADDRESS] == 420
    finally:
        dialog.deleteLater()


# ─────────────────────────────────────────────────────────────
# Справочники менеджера базы (организации, водители)
# ─────────────────────────────────────────────────────────────

def test_db_manager_tables_have_widths_and_storage(qapp, isolated_db):
    """Организации и водители: ширины, минимумы, подсказки, сохранение."""
    from ui.db_manager_dialog import (
        DRIVERS_COLUMNS_CONFIG, DRIVERS_WIDTHS_KEY,
        ORGANIZATIONS_COLUMNS_CONFIG, ORGANIZATIONS_WIDTHS_KEY, DbManagerDialog,
    )

    dialog = DbManagerDialog()
    try:
        for table, config, key in (
            (dialog.carriers_table, ORGANIZATIONS_COLUMNS_CONFIG,
             ORGANIZATIONS_WIDTHS_KEY),
            (dialog.customers_table, ORGANIZATIONS_COLUMNS_CONFIG,
             ORGANIZATIONS_WIDTHS_KEY),
            (dialog.drivers_table, DRIVERS_COLUMNS_CONFIG, DRIVERS_WIDTHS_KEY),
        ):
            header = table.horizontalHeader()

            assert table.property(STORAGE_KEY_PROPERTY) == key
            assert getattr(table, "_table_tooltips_installed", False) is True
            assert header.minimumSectionSize() >= 50
            assert len(config) == table.columnCount()

            for column, _mode, width in config:
                assert header.sectionSize(column) == width, (
                    f"{key}: колонка {column} шириной {header.sectionSize(column)}, "
                    f"ожидалось {width}"
                )
    finally:
        dialog.deleteLater()


def test_db_manager_widths_persist(qapp, isolated_db):
    """Раскладка справочника переживает пересоздание диалога."""
    from ui.db_manager_dialog import DRIVERS_WIDTHS_KEY, DbManagerDialog

    first = DbManagerDialog()
    try:
        first.drivers_table.horizontalHeader().resizeSection(1, 400)
        first.drivers_table._widths_saver.flush()
        assert stored_widths(first.drivers_table, DRIVERS_WIDTHS_KEY)[1] == 400
    finally:
        first.deleteLater()

    second = DbManagerDialog()
    try:
        assert second.drivers_table.horizontalHeader().sectionSize(1) == 400
    finally:
        second.deleteLater()


# ─────────────────────────────────────────────────────────────
# Диалог импорта документов
# ─────────────────────────────────────────────────────────────

def test_document_import_table_has_widths(qapp, isolated_db, monkeypatch):
    """
    Дерево проверки импорта: ширины заданы, последняя колонка не тянется.

    Диалогу нужно настоящее окно Экспедиторства: он снимает слепок формы
    (`form_snapshot`), а не работает с заглушкой. Ширины дереву задаёт тот же
    помощник, что и таблицам (`table_header` умеет и `QTreeWidget`).
    """
    from PyQt5.QtWidgets import QMessageBox

    from ui.document_import_dialog import COL_WHAT, DocumentImportDialog, TREE_HEADERS
    from ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_init_gigachat_client", lambda *a, **k: False)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    window = MainWindow()
    dialog = DocumentImportDialog(window)
    try:
        header = dialog.tree.header()

        assert dialog.tree.columnCount() == len(TREE_HEADERS)
        # У QTreeWidget шапка — один элемент, текст колонки берётся по номеру.
        assert dialog.tree.headerItem().text(COL_WHAT) == TREE_HEADERS[COL_WHAT]
        assert header.stretchLastSection() is False
        for column in range(dialog.tree.columnCount()):
            assert header.sectionResizeMode(column) == QHeaderView.Interactive
            assert header.sectionSize(column) > 0
    finally:
        dialog.deleteLater()
        window.close()


# ─────────────────────────────────────────────────────────────
# Общий помощник: проверки на «синтетической» таблице
# ─────────────────────────────────────────────────────────────

def _demo_table(qapp):
    from PyQt5.QtWidgets import QTableWidget

    table = QTableWidget(1, 4)
    table.setHorizontalHeaderLabels(["Наименование", "Адрес", "Дата", "Время"])
    return table


def test_horizontal_scrollbar_appears_when_columns_do_not_fit(qapp):
    """
    Если сумма ширин больше таблицы, колонки не сжимаются в нитку:
    лишнее уходит в горизонтальную прокрутку (полоса доступна).

    Проверяется на таблице из четырёх колонок по 400 px в окне 500 px.
    """
    table = _demo_table(qapp)
    try:
        from ui.widgets.table_helpers import MODE_FIXED

        setup_point_table(
            table,
            [(column, MODE_FIXED, 400) for column in range(4)],
            storage_key="ui/test/audit_scroll",
        )
        table.resize(500, 200)
        table.show()
        qapp.processEvents()

        header = table.horizontalHeader()
        assert header.length() > table.viewport().width(), (
            "колонки шире таблицы — проверка бессмысленна"
        )
        # Полоса прокрутки не выключена и реально доступна: Qt показывает её,
        # когда содержимое шире вида.
        assert table.horizontalScrollBarPolicy() != Qt.ScrollBarAlwaysOff
        assert table.horizontalScrollBar().maximum() > 0, (
            "лишняя ширина не ушла в прокрутку"
        )
        # И ни одна колонка не сжата ниже своего минимума.
        for column in range(4):
            assert header.sectionSize(column) == 400
    finally:
        table.deleteLater()


def test_width_saver_is_idempotent(qapp):
    """Автосохранение подключается один раз и пишет с паузой."""
    table = _demo_table(qapp)
    try:
        from ui.widgets.table_helpers import MODE_FIXED

        setup_point_table(table, [(0, MODE_FIXED, 120)],
                          storage_key="ui/test/audit_saver")
        first = table._widths_saver
        setup_point_table(table, [(0, MODE_FIXED, 120)],
                          storage_key="ui/test/audit_saver")

        assert isinstance(first, WidthsSaver)
        assert table._widths_saver is first, "подключили второй обработчик"
        assert AUTOSAVE_DELAY_MS > 0
    finally:
        table.deleteLater()


def test_column_settings_helpers_are_shared(qapp):
    """Описание колонок и выбор состава живут в общем помощнике."""
    table = _demo_table(qapp)
    try:
        specs = make_specs([("name", "Наименование", True),
                            ("address", "Адрес", False)])
        install_column_settings_menu(table, specs,
                                     storage_key="ui/test/audit_columns")

        assert table_specs(table) == specs
        assert visible_column_keys(table) == ["name", "address"]

        apply_column_checks(table, {"address": False})
        save_hidden(table, "ui/test/audit_columns", hidden_column_keys(table))

        assert visible_column_keys(table) == ["name"]
        assert load_hidden(table, "ui/test/audit_columns", specs) == ["address"]
    finally:
        table.deleteLater()


def test_broken_selection_in_settings_is_ignored(qapp):
    """Битый список колонок в настройках не роняет таблицу."""
    from ui.widgets.table_helpers import _settings

    specs = make_specs([("name", "Наименование", True), ("address", "Адрес", False)])
    settings = _settings()
    settings.setValue("ui/columns/ui/test/audit_broken", "не список вовсе")
    settings.sync()

    table = _demo_table(qapp)
    try:
        install_column_settings_menu(table, specs,
                                     storage_key="ui/test/audit_broken")

        # Чужой ключ не подошёл — состав берётся по умолчанию.
        assert visible_column_keys(table) == ["name", "address"]
    finally:
        table.deleteLater()


def test_tooltips_work_on_every_table_kind(qapp):
    """Подсказка ставится на ячейку любой таблицы (общий помощник)."""
    table = _demo_table(qapp)
    try:
        from PyQt5.QtWidgets import QTableWidgetItem

        install_tooltip_on_table(table)
        install_tooltip_on_table(table)          # повторно — без второго обработчика

        long_text = "г. Москва, ул. Перерва, д. 19, стр. 3, пост охраны № 2"
        table.setItem(0, 1, QTableWidgetItem(long_text))
        table.itemEntered.emit(table.item(0, 1))

        assert table.item(0, 1).toolTip() == long_text
        assert table.hasMouseTracking() is True
        assert table.selectionBehavior() in (
            QAbstractItemView.SelectItems, QAbstractItemView.SelectRows,
        )
    finally:
        table.deleteLater()
