#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Двусторонняя привязка в «Менеджере базы» (ДОПОЛНЕНИЕ к шагу «Дерево
перевозчиков + двусторонняя загрузка водитель ↔ перевозчик»).

Что проверяется на живом диалоге:

  * кнопка «🚛 Перевозчик…» на вкладке «Водители»: выключена без выбранной
    строки, включается с ней и открывает диалог привязки;
  * после закрытия диалога привязки таблица водителей и дерево
    перевозчиков перечитываются (колонка «Перевозчик» и место водителя
    в дереве не отстают от базы);
  * кнопка «👤 Водители…» в карточке перевозчика открывает диалог
    «Водители перевозчика»; после него менеджер тоже обновляет таблицы,
    даже если правку карточки оператор отменил;
  * у нового (несохранённого) перевозчика и у заказчика кнопки
    «👤 Водители…» нет;
  * в режиме выбора записи кнопка «🚛 Перевозчик…» скрыта.

Qt поднимается в offscreen-режиме, база — временная (`isolated_db`),
данные синтетические, Пдн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QMessageBox, QPushButton,
)

DRIVER_NAME = "Иванов Иван Иванович"
DRIVER_OTHER = "Галушкин Петр Михайлович"

CARRIER_A_NAME = "ООО «Фас Транс»"
CARRIER_B_NAME = "ООО «Ромашка»"

CARRIER_BUTTON_TEXT = "🚛 Перевозчик…"
DRIVERS_BUTTON_TEXT = "👤 Водители…"


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
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)


@pytest.fixture
def manager(qt_app, isolated_db, quiet_dialogs):
    from ui.db_manager_dialog import DbManagerDialog

    dialog = DbManagerDialog()
    yield dialog
    dialog.close()


@pytest.fixture
def carrier_a(isolated_db):
    return isolated_db.save_organization(
        {"full_name": CARRIER_A_NAME, "inn": "7701234567"}, is_carrier=True
    )


@pytest.fixture
def carrier_b(isolated_db):
    return isolated_db.save_organization(
        {"full_name": CARRIER_B_NAME, "inn": "7707654321"}, is_carrier=True
    )


def _add_driver(isolated_db, full_name, carrier_id=None):
    payload = {"full_name": full_name}
    if carrier_id is not None:
        payload["default_carrier_id"] = carrier_id
    return isolated_db.save_driver(payload)


def _button(widget, text):
    found = [b for b in widget.findChildren(QPushButton) if b.text() == text]
    assert len(found) == 1, f"кнопка {text!r}: найдено {len(found)}"
    return found[0]


def _carrier_column_texts(manager):
    """Колонка «Перевозчик» таблицы водителей."""
    from ui.db_manager_dialog import DRIVER_COL_CARRIER

    return [
        manager.drivers_table.item(row, DRIVER_COL_CARRIER).text()
        for row in range(manager.drivers_table.rowCount())
    ]


def _tree_names(manager):
    """Названия верхнего уровня дерева перевозчиков."""
    tree = manager.carriers_tree
    return [
        tree.topLevelItem(index).text(0).removeprefix("🚛 ")
        for index in range(tree.topLevelItemCount())
    ]


def _driver_place_in_tree(manager, full_name):
    """Под каким перевозчиком стоит водитель (None — не найден)."""
    tree = manager.carriers_tree
    for index in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(index)
        for child_index in range(top.childCount()):
            if full_name in top.child(child_index).text(0):
                return top.text(0).removeprefix("🚛 ")
    return None


# ─────────────────────────────────────────────────────────────
# Кнопка «🚛 Перевозчик…»
# ─────────────────────────────────────────────────────────────

def test_carrier_button_needs_selection(manager, isolated_db, carrier_a):
    """Кнопка выключена без выбранной строки и включается с ней."""
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    manager._load_drivers()
    button = _button(manager.drivers_tab, CARRIER_BUTTON_TEXT)

    assert button.isEnabled() is False

    manager.drivers_table.selectRow(0)

    assert button.isEnabled() is True


def test_carrier_button_opens_binding_dialog_and_refreshes(
    manager, isolated_db, carrier_a, carrier_b, monkeypatch
):
    """
    После диалога привязки таблицы менеджера показывают новое состояние.

    Заглушка диалога делает то же, что настоящий: меняет привязку в базе и
    возвращает `changed=True`.
    """
    import ui.db_manager_dialog as module

    driver_id = _add_driver(isolated_db, DRIVER_NAME, carrier_a)

    class FakeDialog:
        def __init__(self, driver, parent=None, on_load_carrier=None):
            self.driver = driver
            self.changed = False

        def exec_(self):
            isolated_db.set_default_carrier(self.driver["id"], carrier_b)
            self.changed = True
            return QDialog.Accepted

    monkeypatch.setattr(module, "DriverCarrierDialog", FakeDialog)

    manager._load_drivers()
    manager.drivers_table.selectRow(0)
    assert _carrier_column_texts(manager) == [CARRIER_A_NAME]

    _button(manager.drivers_tab, CARRIER_BUTTON_TEXT).click()

    assert _carrier_column_texts(manager) == [CARRIER_B_NAME]
    assert _driver_place_in_tree(manager, DRIVER_NAME) == CARRIER_B_NAME
    assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_b


def test_driver_dialog_unbind_refreshes_tables(
    manager, isolated_db, carrier_a, monkeypatch
):
    """Отвязка в диалоге: в таблице пустая колонка, в дереве водителя нет."""
    import ui.db_manager_dialog as module

    driver_id = _add_driver(isolated_db, DRIVER_NAME, carrier_a)

    class FakeDialog:
        def __init__(self, driver, parent=None, on_load_carrier=None):
            self.driver = driver
            self.changed = True

        def exec_(self):
            isolated_db.set_default_carrier(self.driver["id"], None)
            return QDialog.Accepted

    monkeypatch.setattr(module, "DriverCarrierDialog", FakeDialog)

    manager._load_drivers()
    manager.drivers_table.selectRow(0)

    manager._on_open_driver_carrier()

    assert _carrier_column_texts(manager) == [""]
    assert _driver_place_in_tree(manager, DRIVER_NAME) is None
    assert isolated_db.load_driver(driver_id)["default_carrier_id"] is None


def test_carrier_button_without_changes_keeps_tables(
    manager, isolated_db, carrier_a, monkeypatch
):
    """Диалог закрыли ничего не меняя: таблицы остаются как были."""
    import ui.db_manager_dialog as module

    _add_driver(isolated_db, DRIVER_NAME, carrier_a)

    class FakeDialog:
        def __init__(self, driver, parent=None, on_load_carrier=None):
            self.changed = False

        def exec_(self):
            return QDialog.Rejected

    monkeypatch.setattr(module, "DriverCarrierDialog", FakeDialog)

    manager._load_drivers()
    manager.drivers_table.selectRow(0)

    manager._on_open_driver_carrier()

    assert _carrier_column_texts(manager) == [CARRIER_A_NAME]


def test_carrier_button_hidden_in_picker_mode(qt_app, isolated_db, quiet_dialogs):
    """В режиме выбора записи кнопка привязки скрыта: справочник только читают."""
    from ui.db_manager_dialog import DbManagerDialog

    dialog = DbManagerDialog(None, open_tab="drivers", on_pick=lambda record: None)
    try:
        assert _button(dialog.drivers_tab, CARRIER_BUTTON_TEXT).isVisible() is False
    finally:
        dialog.close()


# ─────────────────────────────────────────────────────────────
# Кнопка «👤 Водители…» в карточке перевозчика
# ─────────────────────────────────────────────────────────────

def test_carrier_card_has_drivers_button(qt_app, isolated_db, carrier_a, quiet_dialogs):
    """У сохранённого перевозчика кнопка есть и не спрятана.

    Проверяем `isHidden()`, а не `isVisible()`: окно самого диалога в тесте
    не показывается, и `isVisible()` было бы False у любой кнопки.
    """
    from ui.db_manager_dialog import EditCarrierDialog

    record = isolated_db.load_organization_by_id(carrier_a, is_carrier=True)
    dialog = EditCarrierDialog(record, is_carrier=True)
    try:
        button = _button(dialog, DRIVERS_BUTTON_TEXT)
        assert button.isHidden() is False
        assert button.isEnabled() is True
    finally:
        dialog.deleteLater()


def test_drivers_button_hidden_for_new_carrier(
    qt_app, isolated_db, quiet_dialogs
):
    """У нового перевозчика водителей ещё нет — кнопка скрыта."""
    from ui.db_manager_dialog import EditCarrierDialog

    dialog = EditCarrierDialog({}, is_carrier=True)
    try:
        assert _button(dialog, DRIVERS_BUTTON_TEXT).isHidden() is True
    finally:
        dialog.deleteLater()


def test_drivers_button_hidden_for_customer(
    qt_app, isolated_db, quiet_dialogs
):
    """У заказчика водителей не бывает — кнопки нет."""
    from ui.db_manager_dialog import EditCarrierDialog

    customer_id = isolated_db.save_organization(
        {"full_name": "ООО «Заказчик»"}, is_carrier=False
    )
    record = isolated_db.load_organization_by_id(customer_id, is_carrier=False)
    dialog = EditCarrierDialog(record, is_carrier=False)
    try:
        assert _button(dialog, DRIVERS_BUTTON_TEXT).isHidden() is True
    finally:
        dialog.deleteLater()


def test_drivers_button_opens_dialog(
    qt_app, isolated_db, carrier_a, quiet_dialogs, monkeypatch
):
    """«👤 Водители…» открывает диалог водителей ЭТОГО перевозчика."""
    import ui.db_manager_dialog as module
    from ui.db_manager_dialog import EditCarrierDialog

    seen = {}

    class FakeDialog:
        def __init__(self, carrier_id, parent=None, carrier_name=""):
            seen["carrier_id"] = carrier_id
            seen["carrier_name"] = carrier_name
            self.changed = True

        def exec_(self):
            seen["exec"] = True
            return QDialog.Accepted

    monkeypatch.setattr(module, "CarrierDriversDialog", FakeDialog)

    record = isolated_db.load_organization_by_id(carrier_a, is_carrier=True)
    dialog = EditCarrierDialog(record, is_carrier=True)
    try:
        _button(dialog, DRIVERS_BUTTON_TEXT).click()

        assert seen["exec"] is True
        assert seen["carrier_id"] == carrier_a
        assert seen["carrier_name"] == CARRIER_A_NAME
        assert dialog.drivers_changed is True
    finally:
        dialog.deleteLater()


def test_drivers_button_keeps_flag_when_nothing_changed(
    qt_app, isolated_db, carrier_a, quiet_dialogs, monkeypatch
):
    """Диалог закрыли без изменений — флага нет, лишних перечитываний нет."""
    import ui.db_manager_dialog as module
    from ui.db_manager_dialog import EditCarrierDialog

    class FakeDialog:
        def __init__(self, carrier_id, parent=None, carrier_name=""):
            self.changed = False

        def exec_(self):
            return QDialog.Rejected

    monkeypatch.setattr(module, "CarrierDriversDialog", FakeDialog)

    record = isolated_db.load_organization_by_id(carrier_a, is_carrier=True)
    dialog = EditCarrierDialog(record, is_carrier=True)
    try:
        _button(dialog, DRIVERS_BUTTON_TEXT).click()

        assert dialog.drivers_changed is False
    finally:
        dialog.deleteLater()


def test_manager_refreshes_even_when_carrier_edit_cancelled(
    manager, isolated_db, carrier_a, carrier_b, monkeypatch
):
    """
    Привязки меняются сразу, а карточку перевозчика оператор отменил.

    Менеджер всё равно перечитывает таблицы: иначе колонка «Перевозчик»
    показывала бы прежнего перевозчика.
    """
    import ui.db_manager_dialog as module

    driver_id = _add_driver(isolated_db, DRIVER_NAME)

    class FakeDialog:
        def __init__(self, org, is_carrier=True, parent=None):
            self.drivers_changed = False

        def exec_(self):
            # Как настоящий диалог: привязка записана в базу, карточка не сохранена.
            isolated_db.set_default_carrier(driver_id, carrier_a)
            self.drivers_changed = True
            return QDialog.Rejected

        def get_data(self):
            return {}

    monkeypatch.setattr(module, "EditCarrierDialog", FakeDialog)

    manager._load_drivers()
    assert _carrier_column_texts(manager) == [""]

    manager._edit_org_record(
        isolated_db.load_organization_by_id(carrier_b, is_carrier=True), True
    )

    assert _carrier_column_texts(manager) == [CARRIER_A_NAME]
    assert _driver_place_in_tree(manager, DRIVER_NAME) == CARRIER_A_NAME
    # Карточка не сохранялась — запись перевозчика та же.
    assert isolated_db.load_organization_by_id(
        carrier_b, is_carrier=True
    )["full_name"] == CARRIER_B_NAME


def test_carrier_edit_refreshes_driver_views(
    manager, isolated_db, carrier_a, carrier_b, monkeypatch
):
    """Обычное сохранение карточки перевозчика обновляет таблицу и дерево."""
    import ui.db_manager_dialog as module

    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    _add_driver(isolated_db, DRIVER_OTHER, carrier_a)

    class FakeDialog:
        def __init__(self, org, is_carrier=True, parent=None):
            self.drivers_changed = False

        def exec_(self):
            return QDialog.Accepted

        def get_data(self):
            return {"full_name": "ООО «Фас Транс-2»", "inn": "7701234567"}

    monkeypatch.setattr(module, "EditCarrierDialog", FakeDialog)

    manager._load_drivers()
    manager._edit_org_record(
        isolated_db.load_organization_by_id(carrier_a, is_carrier=True), True
    )

    assert _carrier_column_texts(manager) == ["ООО «Фас Транс-2»"] * 2
    assert sorted(_tree_names(manager)) == sorted(
        ["ООО «Фас Транс-2»", CARRIER_B_NAME]
    )
    # Водители остались под своим перевозчиком (у него новое название).
    assert _driver_place_in_tree(manager, DRIVER_NAME) == "ООО «Фас Транс-2»"
