#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
«Менеджер базы»: перевозчики у водителей (ШАГ «Привязка водителей
к перевозчикам», часть D).

Что проверяется:

  * в таблице водителей есть колонка «Перевозчик» с названием основного
    перевозчика; у водителя с историей работы в подсказке видно «+N»;
  * фильтр «Перевозчик:» — «Все», «— без перевозчика —» и каждый
    перевозчик справочника; смена фильтра отбирает строки;
  * в диалоге редактирования водителя есть поле «Перевозчик» и кнопка
    «📅 История работы у перевозчиков…» (у новой записи она выключена:
    истории ещё не за что зацепиться);
  * сохранение из менеджера пишет и `drivers.default_carrier_id`,
    и запись истории `driver_carriers`;
  * диалог истории показывает связи, умеет открыть новую (закрыв прежнюю
    активную) и закрыть выбранную, ничего не удаляя.

Qt поднимается в offscreen-режиме, база — временная (`isolated_db`),
данные синтетические, Пдн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QDialog, QMessageBox, QPushButton, QTableWidget,
)

DRIVER_NAME = "Иванов Иван Иванович"


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def quiet_dialogs(monkeypatch):
    """Модальные окна не показываем: offscreen их не переживает."""
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)


@pytest.fixture
def manager(qt_app, isolated_db, quiet_dialogs):
    """Менеджер базы поверх временной базы."""
    from ui.db_manager_dialog import DbManagerDialog

    dialog = DbManagerDialog()
    yield dialog
    dialog.close()


@pytest.fixture
def carrier_a(isolated_db):
    return isolated_db.save_organization(
        {"full_name": "ООО «Альфа»", "inn": "7701234567"}, is_carrier=True
    )


@pytest.fixture
def carrier_b(isolated_db):
    return isolated_db.save_organization(
        {"full_name": "ООО «Бета»", "inn": "7709876543"}, is_carrier=True
    )


def _carrier_column(manager):
    """Номер колонки «Перевозчик» (по имени, а не по числу в тесте)."""
    from ui.db_manager_dialog import DRIVER_COL_CARRIER

    header = manager.drivers_table.horizontalHeaderItem(DRIVER_COL_CARRIER)
    assert header.text() == "Перевозчик"
    return DRIVER_COL_CARRIER


def _column_values(manager, column: int):
    return [
        manager.drivers_table.item(row, column).text()
        for row in range(manager.drivers_table.rowCount())
    ]


def _rows_by_name(manager):
    from ui.db_manager_dialog import DRIVER_COL_NAME

    return {
        manager.drivers_table.item(row, DRIVER_COL_NAME).text(): row
        for row in range(manager.drivers_table.rowCount())
    }


# ─────────────────────────────────────────────────────────────
# D.1: колонка «Перевозчик»
# ─────────────────────────────────────────────────────────────

def test_drivers_table_has_carrier_column(manager):
    """Колонка «Перевозчик» есть, и таблица описана конфигом целиком."""
    from ui.db_manager_dialog import DRIVERS_COLUMNS_CONFIG

    column = _carrier_column(manager)

    assert manager.drivers_table.columnCount() == len(DRIVERS_COLUMNS_CONFIG)
    assert column < manager.drivers_table.columnCount()


def test_driver_row_shows_carrier_name(manager, isolated_db, carrier_a):
    """В колонке — название основного перевозчика водителя."""
    isolated_db.save_driver({
        "full_name": DRIVER_NAME,
        "default_carrier_id": carrier_a,
    })
    manager._load_drivers()

    assert _column_values(manager, _carrier_column(manager)) == ["ООО «Альфа»"]


def test_driver_without_carrier_has_empty_cell(manager, isolated_db):
    """У водителя без привязки ячейка пустая (а не «None»)."""
    isolated_db.save_driver({"full_name": DRIVER_NAME})
    manager._load_drivers()

    assert _column_values(manager, _carrier_column(manager)) == [""]


def test_carrier_cell_tooltip_lists_extra_carriers(
    manager, isolated_db, carrier_a, carrier_b
):
    """История работы видна в подсказке: «+1 (ООО «Альфа»)»."""
    driver_id = isolated_db.save_driver({
        "full_name": DRIVER_NAME,
        "default_carrier_id": carrier_b,
    })
    # Работал у Альфы, перешёл в Бету: одна закрытая связь в истории.
    isolated_db.link_driver_to_carrier(driver_id, carrier_a, started_at="2026-01-10")
    isolated_db.link_driver_to_carrier(driver_id, carrier_b, started_at="2026-03-05")

    manager._load_drivers()

    item = manager.drivers_table.item(0, _carrier_column(manager))
    assert item.text() == "ООО «Бета»"
    assert "+1" in item.toolTip()
    assert "ООО «Альфа»" in item.toolTip()


def test_carrier_cell_without_history_has_no_plus(manager, isolated_db, carrier_a):
    """Одна связь — подсказка без «+N»."""
    isolated_db.save_driver({
        "full_name": DRIVER_NAME,
        "default_carrier_id": carrier_a,
    })
    manager._load_drivers()

    item = manager.drivers_table.item(0, _carrier_column(manager))
    assert "+" not in item.toolTip()


# ─────────────────────────────────────────────────────────────
# D.2: фильтр по перевозчику
# ─────────────────────────────────────────────────────────────

def test_carrier_filter_items(manager, carrier_a, carrier_b):
    """Пункты фильтра: «Все», «— без перевозчика —» и справочник."""
    from ui.db_manager_dialog import CARRIER_FILTER_ALL, CARRIER_FILTER_NONE

    manager._fill_carrier_filter()
    combo = manager.carrier_filter

    assert isinstance(combo, QComboBox)
    texts = [combo.itemText(index) for index in range(combo.count())]
    assert texts[0] == "Все"
    assert texts[1] == "— без перевозчика —"
    assert "ООО «Альфа»" in texts and "ООО «Бета»" in texts

    assert combo.itemData(0) == CARRIER_FILTER_ALL
    assert combo.itemData(1) == CARRIER_FILTER_NONE
    assert carrier_a in [combo.itemData(index) for index in range(combo.count())]


def test_filter_default_is_all(manager, isolated_db, carrier_a):
    """По умолчанию фильтр «Все»: список водителей не сужается."""
    isolated_db.save_driver({"full_name": DRIVER_NAME, "default_carrier_id": carrier_a})
    isolated_db.save_driver({"full_name": "Петров Пётр Петрович"})

    manager._load_drivers()

    assert manager.drivers_table.rowCount() == 2


def test_filter_by_carrier(manager, isolated_db, carrier_a, carrier_b):
    """Выбран перевозчик — в таблице только его водители."""
    isolated_db.save_driver({"full_name": DRIVER_NAME, "default_carrier_id": carrier_a})
    isolated_db.save_driver({
        "full_name": "Петров Пётр Петрович", "default_carrier_id": carrier_b,
    })
    isolated_db.save_driver({"full_name": "Сидоров Сидор Сидорович"})

    manager._fill_carrier_filter()
    combo = manager.carrier_filter
    index = next(
        i for i in range(combo.count()) if combo.itemData(i) == carrier_a
    )
    combo.setCurrentIndex(index)

    assert _column_values(manager, _carrier_column(manager)) == ["ООО «Альфа»"]
    assert list(_rows_by_name(manager)) == [DRIVER_NAME]


def test_filter_without_carrier(manager, isolated_db, carrier_a):
    """Пункт «— без перевозчика —» показывает водителей без привязки."""
    isolated_db.save_driver({"full_name": DRIVER_NAME, "default_carrier_id": carrier_a})
    isolated_db.save_driver({"full_name": "Сидоров Сидор Сидорович"})

    manager._fill_carrier_filter()
    manager.carrier_filter.setCurrentIndex(1)

    assert list(_rows_by_name(manager)) == ["Сидоров Сидор Сидорович"]
    assert _column_values(manager, _carrier_column(manager)) == [""]


def test_filter_works_with_search(manager, isolated_db, carrier_a, carrier_b):
    """Фильтр действует и на результат поиска."""
    isolated_db.save_driver({"full_name": DRIVER_NAME, "default_carrier_id": carrier_a})
    isolated_db.save_driver({
        "full_name": "Иванов Пётр Петрович", "default_carrier_id": carrier_b,
    })

    manager._fill_carrier_filter()
    combo = manager.carrier_filter
    index = next(
        i for i in range(combo.count()) if combo.itemData(i) == carrier_b
    )
    combo.setCurrentIndex(index)

    manager._driver_search_text = "Иванов"
    manager._apply_driver_filter()

    assert list(_rows_by_name(manager)) == ["Иванов Пётр Петрович"]


# ─────────────────────────────────────────────────────────────
# D.3: диалог редактирования водителя
# ─────────────────────────────────────────────────────────────

def test_edit_driver_dialog_has_carrier_field(qt_app, isolated_db, carrier_a):
    """В диалоге есть поле «Перевозчик» с выбранным значением записи."""
    from ui.db_manager_dialog import EditDriverDialog

    dialog = EditDriverDialog({
        "id": 1, "full_name": DRIVER_NAME, "default_carrier_id": carrier_a,
    })
    try:
        assert isinstance(dialog.carrier_combo, QComboBox)
        assert dialog.carrier_combo.currentData() == carrier_a
        assert dialog.get_driver_data()["default_carrier_id"] == carrier_a
    finally:
        dialog.deleteLater()


def test_edit_driver_dialog_default_is_none(qt_app, isolated_db):
    """Без привязки в поле стоит «— не указан —» и в данные идёт None."""
    from ui.db_manager_dialog import EditDriverDialog

    dialog = EditDriverDialog({"id": 1, "full_name": DRIVER_NAME})
    try:
        assert dialog.carrier_combo.currentIndex() == 0
        assert dialog.get_driver_data()["default_carrier_id"] is None
    finally:
        dialog.deleteLater()


def test_edit_driver_dialog_has_history_button(qt_app, isolated_db):
    """Кнопка «История работы у перевозчиков…» есть у сохранённой записи."""
    from ui.db_manager_dialog import EditDriverDialog

    dialog = EditDriverDialog({"id": 5, "full_name": DRIVER_NAME})
    try:
        buttons = [
            button for button in dialog.findChildren(QPushButton)
            if "История работы у перевозчиков" in button.text()
        ]
        assert len(buttons) == 1
        assert buttons[0].isEnabled() is True
    finally:
        dialog.deleteLater()


def test_history_button_is_off_for_new_driver(qt_app, isolated_db):
    """У новой записи истории ещё нет — кнопка выключена."""
    from ui.db_manager_dialog import EditDriverDialog

    dialog = EditDriverDialog({})
    try:
        buttons = [
            button for button in dialog.findChildren(QPushButton)
            if "История работы у перевозчиков" in button.text()
        ]
        assert len(buttons) == 1
        assert buttons[0].isEnabled() is False
    finally:
        dialog.deleteLater()


def test_created_driver_gets_carrier_and_history(
    manager, isolated_db, carrier_a, monkeypatch
):
    """«➕ Добавить» пишет и привязку водителя, и запись истории."""
    import ui.db_manager_dialog as module

    class FakeDialog:
        def __init__(self, driver, parent=None):
            self.driver = driver

        def exec_(self):
            return QDialog.Accepted

        def get_driver_data(self):
            return {
                "full_name": DRIVER_NAME,
                "passport_series": "60 26",
                "passport_number": "123456",
                "default_carrier_id": carrier_a,
            }

        def get_vehicle_data(self):
            return {}

    monkeypatch.setattr(module, "EditDriverDialog", FakeDialog)

    manager._on_add_driver()

    drivers = isolated_db.get_all_drivers(with_carrier_name=True)
    assert len(drivers) == 1
    assert drivers[0]["default_carrier_id"] == carrier_a
    assert drivers[0]["carrier_name"] == "ООО «Альфа»"

    links = isolated_db.get_driver_carriers(drivers[0]["id"])
    assert [(link["carrier_id"], link["ended_at"]) for link in links] == [
        (carrier_a, None),
    ]


def test_edited_driver_gets_carrier_and_history(
    manager, isolated_db, carrier_a, carrier_b, monkeypatch
):
    """«✏ Редактировать» переводит водителя к другому перевозчику."""
    import ui.db_manager_dialog as module

    driver_id = isolated_db.save_driver({
        "full_name": DRIVER_NAME,
        "default_carrier_id": carrier_a,
    })
    isolated_db.link_driver_to_carrier(driver_id, carrier_a, started_at="2026-01-10")

    class FakeDialog:
        def __init__(self, driver, parent=None):
            self.driver = driver

        def exec_(self):
            return QDialog.Accepted

        def get_driver_data(self):
            return {"full_name": DRIVER_NAME, "default_carrier_id": carrier_b}

        def get_vehicle_data(self):
            return {}

    monkeypatch.setattr(module, "EditDriverDialog", FakeDialog)
    manager._load_drivers()
    manager.drivers_table.selectRow(0)

    manager._on_edit_driver()

    assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_b

    links = isolated_db.get_driver_carriers(driver_id)
    assert len(links) == 2, "история сохраняется"
    active = isolated_db.get_driver_carriers(driver_id, active_only=True)
    assert [link["carrier_id"] for link in active] == [carrier_b]


# ─────────────────────────────────────────────────────────────
# D.3: диалог истории работы
# ─────────────────────────────────────────────────────────────

def test_history_dialog_lists_links(qt_app, isolated_db, carrier_a, carrier_b):
    """Таблица истории: перевозчик, начало, окончание."""
    from ui.db_manager_dialog import DriverCarrierHistoryDialog

    driver_id = isolated_db.save_driver({
        "full_name": DRIVER_NAME, "default_carrier_id": carrier_b,
    })
    isolated_db.link_driver_to_carrier(driver_id, carrier_a, started_at="2026-01-10")
    isolated_db.link_driver_to_carrier(driver_id, carrier_b, started_at="2026-03-05")

    dialog = DriverCarrierHistoryDialog(driver_id)
    try:
        headers = [
            dialog.table.horizontalHeaderItem(column).text()
            for column in range(dialog.table.columnCount())
        ]
        assert headers == ["Перевозчик", "Начало", "Окончание"]
        assert isinstance(dialog.table, QTableWidget)
        assert dialog.table.rowCount() == 2

        by_carrier = {
            dialog.table.item(row, 0).text(): (
                dialog.table.item(row, 1).text(),
                dialog.table.item(row, 2).text(),
            )
            for row in range(dialog.table.rowCount())
        }
        assert by_carrier["ООО «Бета»"] == ("2026-03-05", "работает")
        assert by_carrier["ООО «Альфа»"] == ("2026-01-10", "2026-03-05")
    finally:
        dialog.deleteLater()


def test_history_dialog_add_closes_previous(
    qt_app, isolated_db, carrier_a, carrier_b
):
    """«➕ Добавить» открывает связь и закрывает прежнюю активную."""
    from ui.db_manager_dialog import DriverCarrierHistoryDialog

    driver_id = isolated_db.save_driver({
        "full_name": DRIVER_NAME, "default_carrier_id": carrier_a,
    })
    isolated_db.link_driver_to_carrier(driver_id, carrier_a, started_at="2026-01-10")

    dialog = DriverCarrierHistoryDialog(driver_id)
    try:
        index = next(
            i for i in range(dialog.carrier_combo.count())
            if dialog.carrier_combo.itemData(i) == carrier_b
        )
        dialog.carrier_combo.setCurrentIndex(index)
        dialog.btn_add.click()

        assert dialog.table.rowCount() == 2
        active = isolated_db.get_driver_carriers(driver_id, active_only=True)
        assert [link["carrier_id"] for link in active] == [carrier_b]
    finally:
        dialog.deleteLater()


def test_history_dialog_close_link(qt_app, isolated_db, carrier_a):
    """«🔗 Закрыть связь» ставит дату окончания у выбранной записи."""
    from ui.db_manager_dialog import DriverCarrierHistoryDialog

    driver_id = isolated_db.save_driver({
        "full_name": DRIVER_NAME, "default_carrier_id": carrier_a,
    })
    isolated_db.link_driver_to_carrier(driver_id, carrier_a, started_at="2026-01-10")

    dialog = DriverCarrierHistoryDialog(driver_id)
    try:
        dialog.table.selectRow(0)
        dialog.btn_close_link.click()

        links = isolated_db.get_driver_carriers(driver_id)
        assert len(links) == 1, "запись истории не удаляется"
        assert links[0]["ended_at"], "дата окончания не поставлена"
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 2).text() != "работает"
    finally:
        dialog.deleteLater()
