#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог «Водители перевозчика» (ДОПОЛНЕНИЕ к шагу «Дерево перевозчиков +
двусторонняя загрузка водитель ↔ перевозчик»).

Что проверяется:

  * два списка рядом: «Привязанные водители» и «Доступные водители»;
  * в левом — те, у кого этот перевозчик основной; в правом — остальные
    (в том числе закреплённые за другим перевозчиком);
  * в обоих списках множественный выбор (ExtendedSelection);
  * «→ Привязать» переносит выделенных в левый список (нескольких сразу),
    закрывая прежнюю связь у чужого перевозчика;
  * «← Отвязать» спрашивает подтверждение, очищает привязку и закрывает
    активную связь; записи НЕ удаляются;
  * поиск по ФИО фильтрует оба списка и сбрасывает выделение;
  * двойной клик переносит водителя в другой список;
  * действия видны по флагу `changed` (по нему менеджер обновляет таблицы);
  * логи без Пдн.

Qt поднимается в offscreen-режиме, база — временная (`isolated_db`),
данные синтетические, Пдн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging  # noqa: E402

import pytest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QAbstractItemView, QApplication, QMessageBox,
)

DRIVER_A = "Иванов Иван Иванович"
DRIVER_B = "Галушкин Петр Михайлович"
DRIVER_C = "Петров Пётр Петрович"

CARRIER_A_NAME = "ООО «Фас Транс»"
CARRIER_B_NAME = "ООО «Ромашка»"


class MessageRecorder:
    """Подмена QMessageBox-метода: помнит тексты и отдаёт нужный ответ."""

    def __init__(self, answer=QMessageBox.Ok):
        self.answer = answer
        self.texts = []

    def __call__(self, parent, title, text, *args, **kwargs):
        self.texts.append(text)
        return self.answer


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
def warning_recorder(monkeypatch):
    recorder = MessageRecorder()
    monkeypatch.setattr(QMessageBox, "warning", recorder)
    return recorder


@pytest.fixture
def question_recorder(monkeypatch):
    recorder = MessageRecorder(answer=QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "question", recorder)
    return recorder


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


def _dialog(isolated_db, carrier_id, **kwargs):
    from ui.carrier_drivers_dialog import CarrierDriversDialog

    return CarrierDriversDialog(carrier_id, **kwargs)


def _select_ids(widget, driver_ids):
    """Выделяет строки по ID водителей (множественный выбор)."""
    from PyQt5.QtCore import Qt

    selected = []
    for index in range(widget.count()):
        item = widget.item(index)
        record = item.data(Qt.UserRole)
        if record and record.get("id") in driver_ids:
            item.setSelected(True)
            selected.append(item)
    assert len(selected) == len(driver_ids), "не все водители найдены в списке"
    return selected


def _titles(widget):
    return [widget.item(index).text() for index in range(widget.count())]


# ─────────────────────────────────────────────────────────────
# Два списка
# ─────────────────────────────────────────────────────────────

def test_two_lists_are_side_by_side(qt_app, isolated_db, carrier_a, quiet_dialogs):
    """Оба списка есть, у каждого свой заголовок со счётчиком."""
    from ui.carrier_drivers_dialog import AVAILABLE_TITLE, LINKED_TITLE

    dialog = _dialog(isolated_db, carrier_a)
    try:
        assert dialog.linked_list is not dialog.available_list
        assert LINKED_TITLE in dialog.linked_label.text()
        assert AVAILABLE_TITLE in dialog.available_label.text()
        assert dialog.linked_list.count() == 0
        assert dialog.available_list.count() == 0
    finally:
        dialog.deleteLater()


def test_buttons_are_in_place(qt_app, isolated_db, carrier_a, quiet_dialogs):
    """Подписи кнопок — ровно те, что ждёт оператор."""
    from PyQt5.QtWidgets import QPushButton

    from ui.carrier_drivers_dialog import (
        CLOSE_BUTTON_TEXT, LINK_BUTTON_TEXT, UNLINK_BUTTON_TEXT,
    )

    dialog = _dialog(isolated_db, carrier_a)
    try:
        texts = {b.text() for b in dialog.findChildren(QPushButton)}
        for expected in (LINK_BUTTON_TEXT, UNLINK_BUTTON_TEXT, CLOSE_BUTTON_TEXT):
            assert expected in texts, expected
    finally:
        dialog.deleteLater()


def test_linked_driver_is_in_left_list(
    qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs
):
    """Водитель с этим перевозчиком — в «Привязанных»."""
    driver_id = _add_driver(isolated_db, DRIVER_A, carrier_a)
    _add_driver(isolated_db, DRIVER_B, carrier_b)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        assert dialog.linked_driver_ids() == [driver_id]
        assert _titles(dialog.linked_list) == [DRIVER_A]
        assert dialog.linked_label.text().endswith("(1)")
    finally:
        dialog.deleteLater()


def test_other_drivers_are_available(
    qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs
):
    """Остальные водители — в «Доступных» (в том числе чужие перевозчику)."""
    _add_driver(isolated_db, DRIVER_A, carrier_a)
    other = _add_driver(isolated_db, DRIVER_B, carrier_b)
    free = _add_driver(isolated_db, DRIVER_C)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        assert sorted(dialog.available_driver_ids()) == sorted([other, free])
        assert _titles(dialog.available_list) == [DRIVER_B, DRIVER_C]
    finally:
        dialog.deleteLater()


def test_extended_selection_is_enabled(
    qt_app, isolated_db, carrier_a, quiet_dialogs
):
    """Оба списка умеют множественный выбор."""
    dialog = _dialog(isolated_db, carrier_a)
    try:
        for widget in (dialog.linked_list, dialog.available_list):
            assert widget.selectionMode() == QAbstractItemView.ExtendedSelection
    finally:
        dialog.deleteLater()


# ─────────────────────────────────────────────────────────────
# Привязка
# ─────────────────────────────────────────────────────────────

def test_link_selected_drivers_moves_them(
    qt_app, isolated_db, carrier_a, quiet_dialogs
):
    """«→ Привязать» переносит СРАЗУ нескольких выделенных водителей."""
    first = _add_driver(isolated_db, DRIVER_A)
    second = _add_driver(isolated_db, DRIVER_B)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        _select_ids(dialog.available_list, [first, second])
        assert sorted(dialog.selected_available_ids()) == sorted([first, second])

        dialog.btn_link.click()

        assert sorted(dialog.linked_driver_ids()) == sorted([first, second])
        assert dialog.available_driver_ids() == []
        assert dialog.changed is True
        for driver_id in (first, second):
            assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_a
    finally:
        dialog.deleteLater()


def test_link_creates_history_links(
    qt_app, isolated_db, carrier_a, quiet_dialogs
):
    """Привязка пишет и историю работы у перевозчика."""
    driver_id = _add_driver(isolated_db, DRIVER_A)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        _select_ids(dialog.available_list, [driver_id])
        dialog.btn_link.click()

        active = isolated_db.get_driver_carriers(driver_id, active_only=True)
        assert [link["carrier_id"] for link in active] == [carrier_a]
    finally:
        dialog.deleteLater()


def test_link_transfers_driver_from_other_carrier(
    qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs
):
    """Чужой водитель переходит к нам, прежняя связь закрывается."""
    driver_id = _add_driver(isolated_db, DRIVER_A, carrier_b)
    isolated_db.link_driver_to_carrier(driver_id, carrier_b, started_at="2026-01-10")

    dialog = _dialog(isolated_db, carrier_a)
    try:
        _select_ids(dialog.available_list, [driver_id])
        dialog.btn_link.click()

        assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_a
        active = isolated_db.get_driver_carriers(driver_id, active_only=True)
        assert [link["carrier_id"] for link in active] == [carrier_a]
        assert len(isolated_db.get_driver_carriers(driver_id)) == 2
        assert dialog.linked_driver_ids() == [driver_id]
    finally:
        dialog.deleteLater()


def test_link_without_selection_warns(
    qt_app, isolated_db, carrier_a, quiet_dialogs, warning_recorder
):
    """Ничего не выделено — подсказка, список не меняется."""
    driver_id = _add_driver(isolated_db, DRIVER_A)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        dialog.btn_link.click()

        assert len(warning_recorder.texts) == 1
        assert "Доступные" in warning_recorder.texts[0]
        assert isolated_db.load_driver(driver_id)["default_carrier_id"] is None
        assert dialog.changed is False
    finally:
        dialog.deleteLater()


# ─────────────────────────────────────────────────────────────
# Отвязка
# ─────────────────────────────────────────────────────────────

def test_unlink_selected_drivers_moves_them(
    qt_app, isolated_db, carrier_a, quiet_dialogs, question_recorder
):
    """«← Отвязать» возвращает выделенных в «Доступные», записи не удаляя."""
    first = _add_driver(isolated_db, DRIVER_A, carrier_a)
    second = _add_driver(isolated_db, DRIVER_B, carrier_a)
    isolated_db.link_driver_to_carrier(first, carrier_a)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        _select_ids(dialog.linked_list, [first, second])
        dialog.btn_unlink.click()

        assert question_recorder.texts, "подтверждение не спрашивали"
        assert dialog.linked_driver_ids() == []
        assert sorted(dialog.available_driver_ids()) == sorted([first, second])
        assert dialog.changed is True
        for driver_id in (first, second):
            assert isolated_db.load_driver(driver_id)["default_carrier_id"] is None
            assert isolated_db.load_driver(driver_id)["full_name"], "запись цела"
        assert isolated_db.get_driver_carriers(first, active_only=True) == []
        assert isolated_db.get_driver_carriers(first), "история работы сохранена"
    finally:
        dialog.deleteLater()


def test_unlink_asks_confirmation(
    qt_app, isolated_db, carrier_a, quiet_dialogs, question_recorder
):
    """Ответ «Нет» оставляет водителя привязанным."""
    question_recorder.answer = QMessageBox.No
    driver_id = _add_driver(isolated_db, DRIVER_A, carrier_a)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        _select_ids(dialog.linked_list, [driver_id])
        dialog.btn_unlink.click()

        assert question_recorder.texts
        assert dialog.linked_driver_ids() == [driver_id]
        assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_a
        assert dialog.changed is False
    finally:
        dialog.deleteLater()


def test_unlink_without_selection_warns(
    qt_app, isolated_db, carrier_a, quiet_dialogs, warning_recorder
):
    """Отвязывать нечего: подсказка про левый список."""
    _add_driver(isolated_db, DRIVER_A, carrier_a)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        dialog.btn_unlink.click()

        assert len(warning_recorder.texts) == 1
        assert "Привязанные" in warning_recorder.texts[0]
    finally:
        dialog.deleteLater()


# ─────────────────────────────────────────────────────────────
# Поиск
# ─────────────────────────────────────────────────────────────

def test_search_filters_both_lists(
    qt_app, isolated_db, carrier_a, quiet_dialogs
):
    """Поиск по ФИО фильтрует оба списка сразу."""
    linked = _add_driver(isolated_db, DRIVER_A, carrier_a)
    available = _add_driver(isolated_db, DRIVER_B)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        dialog.search_input.setText("иванов")

        assert dialog.visible_linked_ids() == [linked]
        assert dialog.visible_available_ids() == []

        dialog.search_input.setText("галушкин")

        assert dialog.visible_linked_ids() == []
        assert dialog.visible_available_ids() == [available]
    finally:
        dialog.deleteLater()


def test_search_reset_shows_everything(
    qt_app, isolated_db, carrier_a, quiet_dialogs
):
    """«Сбросить» возвращает оба списка целиком."""
    linked = _add_driver(isolated_db, DRIVER_A, carrier_a)
    available = _add_driver(isolated_db, DRIVER_B)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        dialog.search_input.setText("иванов")
        assert dialog.visible_available_ids() == []

        dialog.btn_reset.click()

        assert dialog.visible_linked_ids() == [linked]
        assert dialog.visible_available_ids() == [available]
    finally:
        dialog.deleteLater()


def test_search_clears_selection(
    qt_app, isolated_db, carrier_a, quiet_dialogs
):
    """Поиск сбрасывает выделение: скрытые строки не должны уезжать."""
    driver_id = _add_driver(isolated_db, DRIVER_A, carrier_a)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        _select_ids(dialog.linked_list, [driver_id])
        assert dialog.selected_linked_ids() == [driver_id]

        dialog.search_input.setText("петров")

        assert dialog.selected_linked_ids() == []
    finally:
        dialog.deleteLater()


# ─────────────────────────────────────────────────────────────
# Двойной клик
# ─────────────────────────────────────────────────────────────

def test_double_click_available_links_driver(
    qt_app, isolated_db, carrier_a, quiet_dialogs
):
    """Двойной клик по доступному водителю привязывает его."""
    driver_id = _add_driver(isolated_db, DRIVER_A)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        dialog._on_available_double_clicked(dialog.available_list.item(0))

        assert dialog.linked_driver_ids() == [driver_id]
        assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_a
    finally:
        dialog.deleteLater()


def test_double_click_linked_unlinks_driver(
    qt_app, isolated_db, carrier_a, quiet_dialogs, question_recorder
):
    """Двойной клик по привязанному отвязывает его (с подтверждением)."""
    driver_id = _add_driver(isolated_db, DRIVER_A, carrier_a)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        dialog._on_linked_double_clicked(dialog.linked_list.item(0))

        assert question_recorder.texts, "подтверждение не спрашивали"
        assert dialog.linked_driver_ids() == []
        assert isolated_db.load_driver(driver_id)["default_carrier_id"] is None
    finally:
        dialog.deleteLater()


def test_double_click_linked_respects_refusal(
    qt_app, isolated_db, carrier_a, quiet_dialogs, question_recorder
):
    """Ответ «Нет» на двойной клик оставляет привязку."""
    question_recorder.answer = QMessageBox.No
    driver_id = _add_driver(isolated_db, DRIVER_A, carrier_a)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        dialog._on_linked_double_clicked(dialog.linked_list.item(0))

        assert dialog.linked_driver_ids() == [driver_id]
        assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_a
    finally:
        dialog.deleteLater()


# ─────────────────────────────────────────────────────────────
# Прочее
# ─────────────────────────────────────────────────────────────

def test_soft_deleted_driver_is_not_listed(
    qt_app, isolated_db, carrier_a, quiet_dialogs
):
    """Мягко удалённого водителя в списках нет."""
    driver_id = _add_driver(isolated_db, DRIVER_A, carrier_a)
    isolated_db.delete_driver(driver_id)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        assert dialog.linked_driver_ids() == []
        assert dialog.available_driver_ids() == []
    finally:
        dialog.deleteLater()


def test_carrier_name_is_in_header(
    qt_app, isolated_db, carrier_a, quiet_dialogs
):
    """В шапке диалога — наименование перевозчика, а без него — его ID."""
    dialog = _dialog(isolated_db, carrier_a, carrier_name=CARRIER_A_NAME)
    try:
        assert CARRIER_A_NAME in dialog.header_text
    finally:
        dialog.deleteLater()

    dialog = _dialog(isolated_db, carrier_a)
    try:
        assert f"ID={carrier_a}" in dialog.header_text
    finally:
        dialog.deleteLater()


def test_close_button_keeps_changes(
    qt_app, isolated_db, carrier_a, quiet_dialogs
):
    """«Закрыть» закрывает диалог: изменения уже в базе, флаг changed виден."""
    driver_id = _add_driver(isolated_db, DRIVER_A)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        _select_ids(dialog.available_list, [driver_id])
        dialog.btn_link.click()
        dialog.btn_close.click()

        assert dialog.result() == dialog.Accepted
        assert dialog.changed is True
        assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_a
    finally:
        dialog.deleteLater()


def test_actions_are_logged_without_personal_data(
    qt_app, isolated_db, carrier_a, quiet_dialogs, caplog
):
    """В логах — ID и количества, ФИО водителя не пишем."""
    caplog.set_level(logging.DEBUG, logger="ui.carrier_drivers_dialog")
    driver_id = _add_driver(isolated_db, DRIVER_A)

    dialog = _dialog(isolated_db, carrier_a)
    try:
        _select_ids(dialog.available_list, [driver_id])
        dialog.btn_link.click()
    finally:
        dialog.deleteLater()

    assert f"driver_id={driver_id}" in caplog.text
    assert f"carrier_id={carrier_a}" in caplog.text
    assert DRIVER_A not in caplog.text, "ФИО в лог не пишем"
