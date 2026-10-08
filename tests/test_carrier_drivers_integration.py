#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сквозной цикл «перевозчик → водитель → форма» (ШАГ «Дерево перевозчиков +
двусторонняя загрузка водитель ↔ перевозчик», части A–D).

Проверяется связка целиком: настоящий `MainWindow` + настоящий
`DbManagerDialog` с теми же обработчиками, что ставит
`MainWindow._on_open_db_manager`. Что смотрим:

  * водитель, привязанный к перевозчику, стоит в дереве под ним;
  * двойной клик по водителю заполняет И вкладку «Водитель», И вкладку
    «Перевозчик» (перевозчик подтягивается из карточки водителя);
  * двойной клик по перевозчику заполняет ТОЛЬКО перевозчика — вкладка
    «Водитель» остаётся как была (временного водителя оператор впишет сам);
  * сохранение в базу после такой загрузки ссылается на ТУ ЖЕ запись
    перевозчика, а не на её копию;
  * мягко удалённый перевозчик подтягивается (ссылка живёт дольше списка);
  * поиск находит водителя по ФИО и перевозчика по ИНН, родитель не теряется;
  * одинаковые ФИО у разных перевозчиков не путаются;
  * у перевозчика без водителей детей нет — узел остаётся кликабельным.

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
GALUSHKIN_NAME = "Галушкин Петр Михайлович"

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

    `information` здесь тоже обязателен: без подмены настоящее модальное
    окно («Успех», «Данные сохранены в базу!») роняет прогон access
    violation — и падает не тест с окном, а сохранение после него.
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
def window(qt_app, isolated_db, quiet_dialogs, monkeypatch):
    """
    MainWindow без GigaChat: настоящая форма, к которой подключается диалог.

    Уборка — как в `test_ui_main_window_carrier_match.py`: окно закрывается
    по-настоящему и снимается с реестра зеркала, иначе его находят чужие
    тесты.
    """
    from PyQt5.QtCore import QEvent

    from ui.main_window import MainWindow
    from ui.windows.base_window import clear_source_windows

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    win = MainWindow()
    yield win

    try:
        win.system_theme_watcher.stop()
    except Exception:  # noqa: BLE001 — наблюдателя может не быть вовсе
        pass
    win.force_close()
    win.deleteLater()
    app = QApplication.instance()
    if app is not None:
        app.sendPostedEvents(None, QEvent.DeferredDelete)
    clear_source_windows()


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


def _manager(window):
    """Диалог с теми же обработчиками, что ставит MainWindow."""
    from ui.db_manager_dialog import DbManagerDialog

    return DbManagerDialog(
        window,
        on_load_driver=window._load_driver_from_db,
        on_load_customer=window._load_customer_from_db,
        on_load_carrier=window._load_carrier_from_db,
    )


def _add_driver(isolated_db, full_name, carrier_id=None, **extra):
    payload = {"full_name": full_name}
    if carrier_id is not None:
        payload["default_carrier_id"] = carrier_id
    payload.update(extra)
    return isolated_db.save_driver(payload)


def _all_nodes(tree):
    for index in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(index)
        yield top
        for child_index in range(top.childCount()):
            yield top.child(child_index)


def _node(tree, text):
    for node in _all_nodes(tree):
        if text in node.text(0):
            return node
    raise AssertionError(f"узел {text!r} не найден в дереве")


def _top_names(tree):
    return [
        tree.topLevelItem(index).text(0).removeprefix("🚛 ")
        for index in range(tree.topLevelItemCount())
    ]


def _double_click(tree, node):
    tree.itemDoubleClicked.emit(node, 0)


def _button(widget, text):
    found = [b for b in widget.findChildren(QPushButton) if b.text() == text]
    assert len(found) == 1, f"кнопка {text!r}: найдено {len(found)}"
    return found[0]


def _contracts(isolated_db):
    conn = isolated_db.get_connection()
    try:
        rows = conn.execute(
            "SELECT contract_number, driver_id, carrier_id FROM contracts"
        ).fetchall()
    finally:
        conn.close()
    return rows


# ─────────────────────────────────────────────────────────────
# Цикл целиком
# ─────────────────────────────────────────────────────────────

def test_full_cycle_attach_and_load_via_tree(window, isolated_db, carrier_a):
    """Привязали водителя, открыли дерево, двойной клик — форма заполнена."""
    driver_id = _add_driver(isolated_db, GALUSHKIN_NAME)
    # Привязка «по-настоящему»: и поле водителя, и запись истории.
    isolated_db.set_default_carrier(driver_id, carrier_a)

    dialog = _manager(window)
    try:
        tree = dialog.carriers_tree
        _double_click(tree, _node(tree, GALUSHKIN_NAME))
    finally:
        dialog.close()

    assert window.driver_tab.full_name.text() == GALUSHKIN_NAME
    assert window.carrier_tab.full_name.text() == CARRIER_A_NAME
    assert window.carrier_tab.inn.text() == "7701234567"
    assert window.driver_tab.carrier_combo.currentData() == carrier_a
    assert window.tabs.currentWidget() is window.driver_tab
    assert isolated_db.get_driver_carriers(driver_id, active_only=True)


def test_full_cycle_double_click_carrier_only(window, isolated_db, carrier_a):
    """Двойной клик по перевозчику: вкладка «Водитель» не тронута."""
    _add_driver(isolated_db, GALUSHKIN_NAME, carrier_a)
    window.driver_tab.full_name.setText("Временный водитель")
    window.driver_tab.phone.setText("+7 (000) 000-00-00")

    dialog = _manager(window)
    try:
        tree = dialog.carriers_tree
        _double_click(tree, _node(tree, CARRIER_A_NAME))
    finally:
        dialog.close()

    assert window.carrier_tab.full_name.text() == CARRIER_A_NAME
    assert window.driver_tab.full_name.text() == "Временный водитель"
    assert window.driver_tab.phone.text() == "+7 (000) 000-00-00"


def test_full_cycle_unlinked_driver_shows_info(
    window, isolated_db, info_recorder
):
    """Водитель без привязки: в дереве его нет, а кнопка объясняет, что делать."""
    _add_driver(isolated_db, GALUSHKIN_NAME)

    dialog = _manager(window)
    try:
        assert _top_names(dialog.carriers_tree) == [], "привязки нет — детей нет"
        dialog.drivers_table.selectRow(0)
        _button(dialog.drivers_tab, CARRIER_BUTTON_TEXT).click()
    finally:
        dialog.close()

    assert len(info_recorder.texts) == 1
    assert "не привязан" in info_recorder.texts[0]


def test_full_cycle_soft_deleted_carrier_still_pulls(
    window, isolated_db, carrier_a
):
    """
    Мягко удалённый перевозчик всё равно подтягивается в форму.

    В списке вкладки «Водитель» его уже нет (списки удалённых скрывают),
    поэтому проверяем именно форму перевозчика: в договор должен уйти тот,
    на кого водитель работает.
    """
    driver_id = _add_driver(isolated_db, GALUSHKIN_NAME, carrier_a)
    isolated_db.delete_organization(carrier_a, is_carrier=True)

    dialog = _manager(window)
    try:
        dialog.drivers_table.selectRow(0)
        dialog._on_load_driver()
    finally:
        dialog.close()

    assert window.driver_tab.full_name.text() == GALUSHKIN_NAME
    assert window.carrier_tab.full_name.text() == CARRIER_A_NAME
    assert window.carrier_tab.inn.text() == "7701234567"
    assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_a


def test_full_cycle_save_keeps_carrier_link(window, isolated_db, carrier_a):
    """После загрузки из дерева договор ссылается на ту же запись перевозчика."""
    driver_id = _add_driver(isolated_db, GALUSHKIN_NAME, carrier_a)
    window.contract_tab.number.setText("TREE-CYCLE-1")

    dialog = _manager(window)
    try:
        tree = dialog.carriers_tree
        _double_click(tree, _node(tree, GALUSHKIN_NAME))
    finally:
        dialog.close()

    window._on_save_to_db()

    rows = _contracts(isolated_db)
    assert len(rows) == 1
    number, saved_driver_id, saved_carrier_id = rows[0]
    assert number == "TREE-CYCLE-1"
    assert saved_carrier_id == carrier_a, "ссылка на справочник, а не копия"
    assert saved_driver_id != driver_id, "в базу ушла новая запись водителя"
    assert len(isolated_db.get_all_organizations(is_carrier=True)) == 1


# ─────────────────────────────────────────────────────────────
# Поиск
# ─────────────────────────────────────────────────────────────

def test_full_cycle_search_finds_driver_by_name(
    window, isolated_db, carrier_a, carrier_b
):
    """Поиск по ФИО водителя показывает его перевозчика и самого водителя."""
    _add_driver(isolated_db, GALUSHKIN_NAME, carrier_a)
    _add_driver(isolated_db, DRIVER_NAME, carrier_b)

    dialog = _manager(window)
    try:
        dialog._schedule_carrier_tree_filter("Галушкин")
        dialog._apply_carrier_tree_filter()
        tree = dialog.carriers_tree

        assert _top_names(tree) == [CARRIER_A_NAME]
        assert tree.topLevelItem(0).childCount() == 1

        _double_click(tree, _node(tree, GALUSHKIN_NAME))
    finally:
        dialog.close()

    assert window.driver_tab.full_name.text() == GALUSHKIN_NAME
    assert window.carrier_tab.full_name.text() == CARRIER_A_NAME


def test_full_cycle_search_finds_carrier_by_inn(
    window, isolated_db, carrier_a, carrier_b
):
    """Поиск по ИНН перевозчика оставляет его со всеми водителями."""
    _add_driver(isolated_db, GALUSHKIN_NAME, carrier_b)
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)

    dialog = _manager(window)
    try:
        dialog._schedule_carrier_tree_filter("7707654321")
        dialog._apply_carrier_tree_filter()
        tree = dialog.carriers_tree

        assert _top_names(tree) == [CARRIER_B_NAME]
        assert tree.topLevelItem(0).childCount() == 1

        _double_click(tree, _node(tree, GALUSHKIN_NAME))
    finally:
        dialog.close()

    assert window.carrier_tab.full_name.text() == CARRIER_B_NAME
    assert window.carrier_tab.inn.text() == "7707654321"


def test_full_cycle_two_carriers_with_same_named_drivers(
    window, isolated_db, carrier_a, carrier_b
):
    """Однофамильцы у разных перевозчиков не путаются."""
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    _add_driver(isolated_db, DRIVER_NAME, carrier_b)

    dialog = _manager(window)
    try:
        tree = dialog.carriers_tree
        _assert_two_children_with_same_name(tree, DRIVER_NAME)

        top_b = _node(tree, CARRIER_B_NAME)
        _double_click(tree, top_b.child(0))
    finally:
        dialog.close()

    assert window.driver_tab.full_name.text() == DRIVER_NAME
    assert window.carrier_tab.full_name.text() == CARRIER_B_NAME
    assert window.carrier_tab.inn.text() == "7707654321"


def _assert_two_children_with_same_name(tree, full_name: str) -> None:
    """У двух перевозчиков — по водителю с одинаковым ФИО."""
    parents = [
        tree.topLevelItem(index) for index in range(tree.topLevelItemCount())
    ]
    assert len(parents) == 2
    for parent in parents:
        assert parent.childCount() == 1
        assert full_name in parent.child(0).text(0)


def test_full_cycle_carrier_with_no_drivers_shows_empty(
    window, isolated_db, carrier_a
):
    """У перевозчика без водителей детей нет, но узел кликабельный."""
    window.driver_tab.full_name.setText("Временный водитель")

    dialog = _manager(window)
    try:
        tree = dialog.carriers_tree
        top = tree.topLevelItem(0)
        assert top.childCount() == 0

        _double_click(tree, top)
    finally:
        dialog.close()

    assert window.carrier_tab.full_name.text() == CARRIER_A_NAME
    assert window.driver_tab.full_name.text() == "Временный водитель"


def test_full_cycle_driver_edit_keeps_views_in_sync(
    window, isolated_db, carrier_a, carrier_b, monkeypatch
):
    """Правка водителя в дереве: и таблица, и дерево показывают новое."""
    import ui.db_manager_dialog as module

    driver_id = _add_driver(isolated_db, GALUSHKIN_NAME, carrier_a)

    class FakeDialog:
        def __init__(self, driver, parent=None):
            self.driver = driver

        def exec_(self):
            return QDialog.Accepted

        def get_driver_data(self):
            return {"full_name": GALUSHKIN_NAME, "default_carrier_id": carrier_b}

        def get_vehicle_data(self):
            return {}

    monkeypatch.setattr(module, "EditDriverDialog", FakeDialog)

    dialog = _manager(window)
    try:
        tree = dialog.carriers_tree
        tree.setCurrentItem(_node(tree, GALUSHKIN_NAME))
        dialog._on_edit_carrier()

        # В таблице водителей — новый перевозчик…
        assert dialog.drivers_table.item(0, 2).text() == CARRIER_B_NAME
        # …и в дереве водитель переехал под него.
        top_b = _node(tree, CARRIER_B_NAME)
        assert top_b.childCount() == 1
        assert GALUSHKIN_NAME in top_b.child(0).text(0)
        assert _node(tree, CARRIER_A_NAME).childCount() == 0
    finally:
        dialog.close()

    assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_b
