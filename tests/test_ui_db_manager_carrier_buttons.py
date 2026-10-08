#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Кнопки «Менеджера базы» после перехода на дерево (ШАГ «Дерево перевозчиков +
двусторонняя загрузка водитель ↔ перевозчик», части B.6, B.7 и D).

Что проверяется:

  * на вкладке «Перевозчики» нет кнопки «👤 Водители…» — дерево её заменяет
    (водители видны прямо под перевозчиком);
  * на вкладке «Водители» есть кнопка «🚛 Перевозчик…»: она активна только
    при выбранном водителе и открывает диалог привязки «Перевозчик
    водителя» (там водителя можно привязать, отвязать и загрузить
    перевозчика в форму) — подробности в `test_ui_driver_carrier_dialog.py`;
  * «➕ Добавить», «📂 Загрузить в форму», «✏ Редактировать», «🗑 Удалить»,
    «♻ Восстановить» работают и с деревом: перевозчик — с перевозчиком,
    водитель — с водителем.

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
CARRIER_A_NAME = "ООО «Фас Транс»"
CARRIER_B_NAME = "ООО «Ромашка»"

CARRIER_BUTTON_TEXT = "🚛 Перевозчик…"


class MessageRecorder:
    """Подмена QMessageBox.information: помнит показанные тексты."""

    def __init__(self):
        self.texts = []

    def __call__(self, parent, title, text, *args, **kwargs):
        self.texts.append(text)
        return QMessageBox.Ok


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def quiet_dialogs(monkeypatch):
    """
    Модальные окна не показываем: offscreen их не переживает.

    `information` подменяется здесь, а `info_recorder` (там, где нужен)
    перекрывает подмену своим обработчиком — порядок фикстур это учитывает.
    """
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)


@pytest.fixture
def info_recorder(monkeypatch):
    recorder = MessageRecorder()
    monkeypatch.setattr(QMessageBox, "information", recorder)
    return recorder


@pytest.fixture
def warning_recorder(monkeypatch):
    recorder = MessageRecorder()
    monkeypatch.setattr(QMessageBox, "warning", recorder)
    return recorder


@pytest.fixture
def manager(qt_app, isolated_db, quiet_dialogs, info_recorder):
    from ui.db_manager_dialog import DbManagerDialog

    dialog = DbManagerDialog()
    yield dialog
    dialog.close()


@pytest.fixture
def carrier_a(isolated_db):
    return isolated_db.save_organization(
        {
            "full_name": CARRIER_A_NAME,
            "inn": "7701234567",
            "kpp": "770101001",
            "director_name": "Петров Пётр Петрович",
        },
        is_carrier=True,
    )


@pytest.fixture
def carrier_b(isolated_db):
    return isolated_db.save_organization(
        {"full_name": CARRIER_B_NAME, "inn": "7707654321"}, is_carrier=True
    )


def _buttons_with_text(widget, text):
    return [
        button for button in widget.findChildren(QPushButton)
        if button.text() == text
    ]


def _button(widget, text):
    found = _buttons_with_text(widget, text)
    assert len(found) == 1, f"кнопка {text!r}: найдено {len(found)}"
    return found[0]


def _add_driver(isolated_db, full_name, carrier_id=None, **extra):
    payload = {"full_name": full_name}
    if carrier_id is not None:
        payload["default_carrier_id"] = carrier_id
    payload.update(extra)
    return isolated_db.save_driver(payload)


def _carrier_card_button(manager):
    return _button(manager.drivers_tab, CARRIER_BUTTON_TEXT)


# ─────────────────────────────────────────────────────────────
# D.1–D.2: кнопки на вкладках
# ─────────────────────────────────────────────────────────────

def test_carrier_tab_has_drivers_button_hidden(manager, isolated_db):
    """Кнопки «👤 Водители…» нет: водители видны в дереве."""
    assert _buttons_with_text(manager.carriers_tab, "👤 Водители…") == []
    assert _buttons_with_text(manager.carriers_tab, "👤 Водители") == []
    assert manager.carriers_tree is not None, "вместо кнопки — дерево"


def test_driver_tab_has_carrier_button(manager, isolated_db):
    """На вкладке «Водители» есть ровно одна кнопка «🚛 Перевозчик…»."""
    button = _carrier_card_button(manager)

    assert button.isEnabled() is False, "без выбранного водителя кнопка выключена"
    assert button.toolTip() != ""


def test_manager_buttons_are_all_in_place(manager):
    """Существующие кнопки не переименованы ни на одной вкладке."""
    for tab in (manager.carriers_tab, manager.drivers_tab, manager.customers_tab):
        texts = {button.text() for button in tab.findChildren(QPushButton)}
        for expected in (
            "➕ Добавить",
            "📂 Загрузить в форму",
            "✏ Редактировать",
            "🗑 Удалить",
            "♻ Восстановить",
        ):
            assert expected in texts, expected


def test_carrier_button_disabled_without_selection(manager, isolated_db):
    """Пока водитель не выбран, карточку перевозчика открывать нечего."""
    _add_driver(isolated_db, DRIVER_NAME)
    manager._load_drivers()

    assert _carrier_card_button(manager).isEnabled() is False


def test_carrier_button_enabled_with_selection(manager, isolated_db, carrier_a):
    """Выбрали водителя — кнопка карточки перевозчика активна."""
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    manager._load_drivers()
    manager.drivers_table.selectRow(0)

    assert _carrier_card_button(manager).isEnabled() is True


def test_carrier_button_opens_binding_dialog(
    qt_app, isolated_db, carrier_a, quiet_dialogs, monkeypatch
):
    """Кнопка открывает диалог привязки «Перевозчик водителя» с этой записью."""
    from ui.db_manager_dialog import DbManagerDialog
    import ui.db_manager_dialog as module

    seen = {}
    loaded = []

    class FakeDialog:
        def __init__(self, driver, parent=None, on_load_carrier=None):
            seen["driver"] = dict(driver)
            seen["on_load_carrier"] = on_load_carrier
            seen["parent"] = parent
            self.changed = True

        def exec_(self):
            seen["exec"] = True
            return QDialog.Accepted

    monkeypatch.setattr(module, "DriverCarrierDialog", FakeDialog)

    driver_id = _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    dialog = DbManagerDialog(on_load_carrier=loaded.append)
    try:
        dialog._load_drivers()
        dialog.drivers_table.selectRow(0)

        dialog._on_open_driver_carrier()

        assert seen["exec"] is True
        assert seen["driver"]["id"] == driver_id
        assert seen["driver"]["default_carrier_id"] == carrier_a
        assert seen["parent"] is dialog

        # Обработчик «📂 Загрузить в форму» из диалога доходит до вызывающей
        # стороны: это тот же обработчик, что и у самого менеджера.
        record = isolated_db.load_organization_by_id(carrier_a, is_carrier=True)
        seen["on_load_carrier"](record)
        assert [item["id"] for item in loaded] == [carrier_a]
    finally:
        dialog.close()


def test_carrier_button_opens_dialog_for_unlinked_driver(
    manager, isolated_db, monkeypatch
):
    """У водителя без привязки диалог тоже открывается: привязать можно там."""
    import ui.db_manager_dialog as module

    seen = {}

    class FakeDialog:
        def __init__(self, driver, parent=None, on_load_carrier=None):
            seen["driver"] = dict(driver)
            self.changed = False

        def exec_(self):
            seen["exec"] = True
            return QDialog.Accepted

    monkeypatch.setattr(module, "DriverCarrierDialog", FakeDialog)
    _add_driver(isolated_db, DRIVER_NAME)
    manager._load_drivers()
    manager.drivers_table.selectRow(0)

    manager._on_open_driver_carrier()

    assert seen["exec"] is True
    assert seen["driver"]["default_carrier_id"] is None


def test_carrier_button_no_exception_without_selection(
    manager, isolated_db, carrier_a, warning_recorder, monkeypatch
):
    """Строка не выбрана — предупреждение, диалог не открывается."""
    import ui.db_manager_dialog as module

    class BombDialog:
        def __init__(self, *args, **kwargs):
            raise AssertionError("диалог без выбранного водителя не открываем")

    monkeypatch.setattr(module, "DriverCarrierDialog", BombDialog)
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    manager._load_drivers()
    manager.drivers_table.clearSelection()
    manager.drivers_table.setCurrentCell(-1, -1)

    manager._on_open_driver_carrier()

    assert len(warning_recorder.texts) == 1
    assert "Выберите водителя" in warning_recorder.texts[0]


def test_carrier_button_hidden_in_picker_mode(qt_app, isolated_db, quiet_dialogs):
    """В режиме выбора справочник только читают: карточки перевозчика нет.

    Проверяем `isHidden()`: окно диалога в тесте не показывается, и
    `isVisible()` было бы False у любой кнопки.
    """
    from ui.db_manager_dialog import DbManagerDialog

    dialog = DbManagerDialog(None, open_tab="drivers", on_pick=lambda record: None)
    try:
        button = _button(dialog.drivers_tab, CARRIER_BUTTON_TEXT)
        assert button.isHidden() is True
    finally:
        dialog.close()


# ─────────────────────────────────────────────────────────────
# B.6: «➕ Добавить»
# ─────────────────────────────────────────────────────────────

def test_add_button_creates_carrier(manager, isolated_db, monkeypatch):
    """«➕ Добавить» на вкладке перевозчиков создаёт узел дерева."""
    import ui.db_manager_dialog as module

    class FakeDialog:
        def __init__(self, org, is_carrier=True, parent=None):
            self.is_carrier = is_carrier

        def exec_(self):
            return QDialog.Accepted

        def get_data(self):
            return {"full_name": CARRIER_B_NAME, "inn": "7707654321"}

    monkeypatch.setattr(module, "EditCarrierDialog", FakeDialog)

    _button(manager.carriers_tab, "➕ Добавить").click()

    saved = isolated_db.get_all_organizations(is_carrier=True)
    assert [org["full_name"] for org in saved] == [CARRIER_B_NAME]
    assert manager.carriers_tree.topLevelItemCount() == 1
    assert CARRIER_B_NAME in manager.carriers_tree.topLevelItem(0).text(0)


def test_add_button_creates_driver(
    manager, isolated_db, carrier_a, monkeypatch
):
    """«➕ Добавить» на вкладке водителей: строка в таблице и узел в дереве."""
    import ui.db_manager_dialog as module

    class FakeDialog:
        def __init__(self, driver, parent=None):
            self.driver = driver

        def exec_(self):
            return QDialog.Accepted

        def get_driver_data(self):
            return {"full_name": DRIVER_NAME, "default_carrier_id": carrier_a}

        def get_vehicle_data(self):
            return {}

    monkeypatch.setattr(module, "EditDriverDialog", FakeDialog)

    _button(manager.drivers_tab, "➕ Добавить").click()

    assert manager.drivers_table.rowCount() == 1
    assert manager.drivers_table.item(0, 1).text() == DRIVER_NAME

    top = manager.carriers_tree.topLevelItem(0)
    assert top.childCount() == 1
    assert DRIVER_NAME in top.child(0).text(0)


# ─────────────────────────────────────────────────────────────
# B.7: «📂 Загрузить в форму»
# ─────────────────────────────────────────────────────────────

def test_load_button_works_for_carrier(qt_app, isolated_db, quiet_dialogs):
    """Кнопка на вкладке перевозчиков отдаёт выбранного перевозчика."""
    from ui.db_manager_dialog import DbManagerDialog

    carrier_id = isolated_db.save_organization(
        {"full_name": CARRIER_A_NAME, "inn": "7701234567"}, is_carrier=True
    )
    loaded = []
    dialog = DbManagerDialog(on_load_carrier=loaded.append)
    try:
        tree = dialog.carriers_tree
        tree.setCurrentItem(tree.topLevelItem(0))

        _button(dialog.carriers_tab, "📂 Загрузить в форму").click()

        assert [record["id"] for record in loaded] == [carrier_id]
        assert dialog.result() == QDialog.Accepted
    finally:
        dialog.close()


def test_load_button_works_for_driver(qt_app, isolated_db, quiet_dialogs):
    """Кнопка на вкладке водителей отдаёт выбранного водителя."""
    from ui.db_manager_dialog import DbManagerDialog

    driver_id = isolated_db.save_driver({"full_name": DRIVER_NAME})
    loaded = []
    dialog = DbManagerDialog(on_load_driver=loaded.append)
    try:
        dialog.drivers_table.selectRow(0)

        _button(dialog.drivers_tab, "📂 Загрузить в форму").click()

        assert [record["id"] for record in loaded] == [driver_id]
        assert dialog.result() == QDialog.Accepted
    finally:
        dialog.close()


def test_load_button_works_for_driver_node_in_tree(
    qt_app, isolated_db, quiet_dialogs
):
    """Кнопка на вкладке перевозчиков у узла-водителя отдаёт водителя с ТС."""
    from ui.db_manager_dialog import DbManagerDialog

    carrier_id = isolated_db.save_organization(
        {"full_name": CARRIER_A_NAME}, is_carrier=True
    )
    driver_id = isolated_db.save_driver(
        {"full_name": DRIVER_NAME, "default_carrier_id": carrier_id}
    )
    isolated_db.save_driver_vehicle(
        driver_id, {"tractor_plate": "O844XY196", "trailer_plate": "71ABF18"}
    )

    loaded = []
    dialog = DbManagerDialog(on_load_driver=loaded.append)
    try:
        top = dialog.carriers_tree.topLevelItem(0)
        dialog.carriers_tree.setCurrentItem(top.child(0))

        _button(dialog.carriers_tab, "📂 Загрузить в форму").click()

        assert [record["id"] for record in loaded] == [driver_id]
        assert loaded[0]["default_carrier_id"] == carrier_id
        assert loaded[0]["tractor"]["plate_number"] == "O844XY196"
        assert loaded[0]["trailer"]["plate_number"] == "71ABF18"
    finally:
        dialog.close()


def test_picker_mode_renames_load_button(
    qt_app, isolated_db, quiet_dialogs
):
    """В режиме выбора кнопка называется «📂 Выбрать», правила те же."""
    from ui.db_manager_dialog import DbManagerDialog

    isolated_db.save_organization({"full_name": CARRIER_A_NAME}, is_carrier=True)
    picked = []
    dialog = DbManagerDialog(
        None, open_tab="carriers", on_pick=picked.append
    )
    try:
        tree = dialog.carriers_tree
        tree.setCurrentItem(tree.topLevelItem(0))

        _button(dialog.carriers_tab, "📂 Выбрать").click()

        assert [record["full_name"] for record in picked] == [CARRIER_A_NAME]
    finally:
        dialog.close()


def test_picker_mode_driver_node_gives_its_carrier(
    qt_app, isolated_db, quiet_dialogs
):
    """Выбор вкладки-перевозчика: клик по водителю = его перевозчик."""
    from ui.db_manager_dialog import DbManagerDialog

    carrier_id = isolated_db.save_organization(
        {"full_name": CARRIER_A_NAME}, is_carrier=True
    )
    isolated_db.save_driver(
        {"full_name": DRIVER_NAME, "default_carrier_id": carrier_id}
    )
    picked = []
    dialog = DbManagerDialog(None, open_tab="carriers", on_pick=picked.append)
    try:
        top = dialog.carriers_tree.topLevelItem(0)
        dialog.carriers_tree.setCurrentItem(top.child(0))

        _button(dialog.carriers_tab, "📂 Выбрать").click()

        assert [record["id"] for record in picked] == [carrier_id]
        assert picked[0]["full_name"] == CARRIER_A_NAME
    finally:
        dialog.close()
