#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог «Перевозчик водителя» (ДОПОЛНЕНИЕ к шагу «Дерево перевозчиков +
двусторонняя загрузка водитель ↔ перевозчик»).

Что проверяется:

  * список всех перевозчиков справочника; текущий выделен и помечен «✓»;
  * подпись «Текущий перевозчик: …» (в том числе у мягко удалённого);
  * «✓ Привязать выбранного» пишет привязку и заводит запись истории;
  * «✗ Отвязать» спрашивает подтверждение, очищает привязку и закрывает
    активную связь, но записи истории не удаляет;
  * «📂 Загрузить в форму» отдаёт перевозчика обработчику и закрывает диалог;
  * поиск сужает список по наименованию и ИНН;
  * двойной клик по перевозчику привязывает его;
  * логи без Пдн (ФИО в лог не попадает).

Qt поднимается в offscreen-режиме, база — временная (`isolated_db`),
данные синтетические, Пдн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging  # noqa: E402

import pytest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QAbstractItemView, QApplication, QDialog, QMessageBox, QPushButton,
)

DRIVER_NAME = "Иванов Иван Иванович"
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
def question_recorder(monkeypatch):
    recorder = MessageRecorder(answer=QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "question", recorder)
    return recorder


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


def _driver(isolated_db, carrier_id=None, **extra):
    payload = {"full_name": DRIVER_NAME, "phone": "+7 (999) 111-22-33"}
    if carrier_id is not None:
        payload["default_carrier_id"] = carrier_id
    payload.update(extra)
    driver_id = isolated_db.save_driver(payload)
    return isolated_db.load_driver(driver_id)


def _dialog(isolated_db, carrier_id=None, on_load_carrier=None, **extra):
    """Диалог поверх водителя из временной базы."""
    from ui.driver_carrier_dialog import DriverCarrierDialog

    record = _driver(isolated_db, carrier_id, **extra)
    return DriverCarrierDialog(record, on_load_carrier=on_load_carrier), record


def _visible_ids(dialog):
    return dialog.visible_carrier_ids()


def _item_by_id(dialog, carrier_id):
    from PyQt5.QtCore import Qt

    for index in range(dialog.carriers_list.count()):
        item = dialog.carriers_list.item(index)
        record = item.data(Qt.UserRole)
        if record and record.get("id") == carrier_id:
            return item
    raise AssertionError(f"перевозчик ID={carrier_id} не найден в списке")


def _select(dialog, carrier_id):
    item = _item_by_id(dialog, carrier_id)
    dialog.carriers_list.setCurrentItem(item)
    item.setSelected(True)
    return item


def _button(dialog, text):
    found = [b for b in dialog.findChildren(QPushButton) if b.text() == text]
    assert len(found) == 1, f"кнопка {text!r}: найдено {len(found)}"
    return found[0]


# ─────────────────────────────────────────────────────────────
# Список и текущий перевозчик
# ─────────────────────────────────────────────────────────────

def test_dialog_lists_all_carriers(qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs):
    """В списке — все активные перевозчики справочника."""
    from ui.driver_carrier_dialog import DriverCarrierDialog

    dialog = DriverCarrierDialog(isolated_db.load_driver(
        isolated_db.save_driver({"full_name": DRIVER_NAME})
    ))
    try:
        assert sorted(_visible_ids(dialog)) == sorted([carrier_a, carrier_b])
        assert dialog.carriers_list.count() == 2
    finally:
        dialog.deleteLater()


def test_buttons_are_in_place(qt_app, isolated_db, carrier_a, quiet_dialogs):
    """Кнопки диалога названы ровно так, как ожидает оператор."""
    from ui.driver_carrier_dialog import (
        BIND_BUTTON_TEXT, CLOSE_BUTTON_TEXT, LOAD_BUTTON_TEXT,
        UNBIND_BUTTON_TEXT,
    )

    dialog, _record = _dialog(isolated_db, carrier_a)
    try:
        texts = {b.text() for b in dialog.findChildren(QPushButton)}
        for expected in (BIND_BUTTON_TEXT, UNBIND_BUTTON_TEXT,
                         LOAD_BUTTON_TEXT, CLOSE_BUTTON_TEXT):
            assert expected in texts, expected
    finally:
        dialog.deleteLater()


def test_current_carrier_is_selected(qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs):
    """Текущий перевозчик выделен, помечен «✓» и назван в подписи."""
    from ui.driver_carrier_dialog import CURRENT_MARK

    dialog, _record = _dialog(isolated_db, carrier_a)
    try:
        item = _item_by_id(dialog, carrier_a)
        assert item.isSelected() is True
        assert item.text().startswith(CURRENT_MARK)
        assert dialog.selected_carrier_id() == carrier_a
        assert CARRIER_A_NAME in dialog.current_label.text()
        assert dialog.btn_unbind.isEnabled() is True
    finally:
        dialog.deleteLater()


def test_no_carrier_means_no_selection(qt_app, isolated_db, carrier_a, quiet_dialogs):
    """Без привязки ничего не выделено, «Отвязать» выключена."""
    from ui.driver_carrier_dialog import NO_CARRIER_TITLE

    dialog, _record = _dialog(isolated_db)
    try:
        assert dialog.carriers_list.selectedItems() == []
        assert dialog.current_carrier_id() is None
        assert NO_CARRIER_TITLE in dialog.current_label.text()
        assert dialog.btn_unbind.isEnabled() is False
    finally:
        dialog.deleteLater()


def test_soft_deleted_carrier_is_shown_in_label(
    qt_app, isolated_db, carrier_a, quiet_dialogs
):
    """Мягко удалённый перевозчик: в подписи это видно, в списке его нет."""
    from ui.driver_carrier_dialog import DriverCarrierDialog

    record = _driver(isolated_db, carrier_a)
    isolated_db.delete_organization(carrier_a, is_carrier=True)

    dialog = DriverCarrierDialog(record)
    try:
        assert CARRIER_A_NAME in dialog.current_label.text()
        assert "удалён" in dialog.current_label.text()
        assert carrier_a not in _visible_ids(dialog)
    finally:
        dialog.deleteLater()


# ─────────────────────────────────────────────────────────────
# Привязка
# ─────────────────────────────────────────────────────────────

def test_bind_selected_carrier(
    qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs, info_recorder
):
    """«✓ Привязать выбранного» пишет привязку и обновляет подпись."""
    dialog, record = _dialog(isolated_db)
    try:
        _select(dialog, carrier_b)
        dialog.btn_bind.click()

        assert isolated_db.load_driver(record["id"])["default_carrier_id"] == carrier_b
        assert dialog.changed is True
        assert dialog.current_carrier_id() == carrier_b
        assert CARRIER_B_NAME in dialog.current_label.text()
        assert len(info_recorder.texts) == 1
    finally:
        dialog.deleteLater()


def test_bind_creates_history_link(
    qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs
):
    """Привязка заводит и запись истории работы у перевозчика."""
    dialog, record = _dialog(isolated_db)
    try:
        _select(dialog, carrier_b)
        dialog.btn_bind.click()

        links = isolated_db.get_driver_carriers(record["id"], active_only=True)
        assert [link["carrier_id"] for link in links] == [carrier_b]
    finally:
        dialog.deleteLater()


def test_bind_closes_previous_link(
    qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs
):
    """Переход к другому перевозчику закрывает прежнюю активную связь."""
    dialog, record = _dialog(isolated_db, carrier_a)
    isolated_db.link_driver_to_carrier(record["id"], carrier_a, started_at="2026-01-10")
    try:
        _select(dialog, carrier_b)
        dialog.btn_bind.click()

        assert isolated_db.load_driver(record["id"])["default_carrier_id"] == carrier_b
        active = isolated_db.get_driver_carriers(record["id"], active_only=True)
        assert [link["carrier_id"] for link in active] == [carrier_b]
        history = isolated_db.get_driver_carriers(record["id"])
        assert len(history) == 2, "прежняя связь закрыта, а не удалена"
    finally:
        dialog.deleteLater()


def test_bind_without_selection_warns(
    qt_app, isolated_db, carrier_a, quiet_dialogs, warning_recorder
):
    """Привязывать нечего: подсказка «выберите перевозчика»."""
    dialog, record = _dialog(isolated_db)
    try:
        dialog.carriers_list.clearSelection()
        dialog.carriers_list.setCurrentItem(None)
        dialog.btn_bind.click()

        assert len(warning_recorder.texts) == 1
        assert isolated_db.load_driver(record["id"])["default_carrier_id"] is None
    finally:
        dialog.deleteLater()


def test_double_click_binds_carrier(
    qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs
):
    """Двойной клик по перевозчику = привязать его."""
    dialog, record = _dialog(isolated_db)
    try:
        dialog._on_item_double_clicked(_item_by_id(dialog, carrier_b))

        assert isolated_db.load_driver(record["id"])["default_carrier_id"] == carrier_b
        assert dialog.changed is True
    finally:
        dialog.deleteLater()


# ─────────────────────────────────────────────────────────────
# Отвязка
# ─────────────────────────────────────────────────────────────

def test_unbind_clears_carrier(
    qt_app, isolated_db, carrier_a, quiet_dialogs, question_recorder
):
    """«✗ Отвязать» очищает привязку и закрывает активную связь."""
    dialog, record = _dialog(isolated_db, carrier_a)
    isolated_db.link_driver_to_carrier(record["id"], carrier_a)
    try:
        dialog.btn_unbind.click()

        assert question_recorder.texts, "подтверждение не спрашивали"
        assert isolated_db.load_driver(record["id"])["default_carrier_id"] is None
        assert isolated_db.get_driver_carriers(record["id"], active_only=True) == []
        assert len(isolated_db.get_driver_carriers(record["id"])) == 1, "история цела"
        assert dialog.changed is True
        assert dialog.current_carrier_id() is None
    finally:
        dialog.deleteLater()


def test_unbind_asks_confirmation(
    qt_app, isolated_db, carrier_a, quiet_dialogs, question_recorder
):
    """Ответ «Нет» ничего не меняет."""
    question_recorder.answer = QMessageBox.No

    dialog, record = _dialog(isolated_db, carrier_a)
    try:
        dialog.btn_unbind.click()

        assert question_recorder.texts
        assert isolated_db.load_driver(record["id"])["default_carrier_id"] == carrier_a
        assert dialog.changed is False
    finally:
        dialog.deleteLater()


def test_unbind_without_carrier_shows_info(
    qt_app, isolated_db, carrier_a, quiet_dialogs, info_recorder
):
    """Отвязывать нечего: диалог сообщает об этом, привязка не пишется."""
    dialog, record = _dialog(isolated_db)
    try:
        dialog._on_unbind()

        assert len(info_recorder.texts) == 1
        assert "не привязан" in info_recorder.texts[0]
        assert isolated_db.load_driver(record["id"])["default_carrier_id"] is None
    finally:
        dialog.deleteLater()


# ─────────────────────────────────────────────────────────────
# Поиск
# ─────────────────────────────────────────────────────────────

def test_search_filters_by_name(qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs):
    """Поиск по наименованию оставляет только совпавших."""
    dialog, _record = _dialog(isolated_db)
    try:
        dialog.search_input.setText("ромашк")

        assert _visible_ids(dialog) == [carrier_b]
    finally:
        dialog.deleteLater()


def test_search_filters_by_inn(qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs):
    """Поиск по ИНН тоже работает."""
    dialog, _record = _dialog(isolated_db)
    try:
        dialog.search_input.setText("7707654321")

        assert _visible_ids(dialog) == [carrier_b]
    finally:
        dialog.deleteLater()


def test_search_reset_shows_all(qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs):
    """«Сбросить» возвращает полный список."""
    dialog, _record = _dialog(isolated_db)
    try:
        dialog.search_input.setText("ромашк")
        assert _visible_ids(dialog) == [carrier_b]

        dialog.btn_reset.click()

        assert sorted(_visible_ids(dialog)) == sorted([carrier_a, carrier_b])
    finally:
        dialog.deleteLater()


def test_search_clears_selection(qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs):
    """Поиск сбрасывает выделение: скрытую строку выбирать нельзя."""
    dialog, _record = _dialog(isolated_db, carrier_a)
    try:
        assert dialog.selected_carrier_id() == carrier_a

        dialog.search_input.setText("ромашк")

        assert dialog.carriers_list.selectedItems() == []
        assert dialog.selected_carrier_id() is None
    finally:
        dialog.deleteLater()


# ─────────────────────────────────────────────────────────────
# Загрузка в форму и закрытие
# ─────────────────────────────────────────────────────────────

def test_load_to_form_gives_carrier(qt_app, isolated_db, carrier_a, quiet_dialogs):
    """«📂 Загрузить в форму» отдаёт запись перевозчика и закрывает диалог."""
    loaded = []
    dialog, _record = _dialog(isolated_db, carrier_a, on_load_carrier=loaded.append)
    try:
        dialog.btn_load.click()

        assert [record["id"] for record in loaded] == [carrier_a]
        assert loaded[0]["full_name"] == CARRIER_A_NAME
        assert dialog.result() == QDialog.Accepted
    finally:
        dialog.deleteLater()


def test_load_to_form_without_selection_uses_current(
    qt_app, isolated_db, carrier_a, quiet_dialogs
):
    """Выделения нет — грузим текущего перевозчика водителя."""
    loaded = []
    dialog, _record = _dialog(isolated_db, carrier_a, on_load_carrier=loaded.append)
    try:
        dialog.carriers_list.clearSelection()
        dialog.carriers_list.setCurrentItem(None)
        dialog._on_load_to_form()

        assert [record["id"] for record in loaded] == [carrier_a]
    finally:
        dialog.deleteLater()


def test_load_to_form_without_carrier_warns(
    qt_app, isolated_db, carrier_a, quiet_dialogs, warning_recorder
):
    """Ни выбора, ни привязки — подсказка вместо пустой загрузки."""
    dialog, _record = _dialog(isolated_db)
    try:
        dialog._on_load_to_form()

        assert len(warning_recorder.texts) == 1
        assert dialog.result() != QDialog.Accepted
    finally:
        dialog.deleteLater()


def test_close_button_closes_dialog(qt_app, isolated_db, carrier_a, quiet_dialogs):
    """«Закрыть» закрывает диалог, ничего не меняя."""
    dialog, record = _dialog(isolated_db, carrier_a)
    try:
        _button(dialog, "Закрыть").click()

        assert dialog.result() == QDialog.Accepted
        assert dialog.changed is False
        assert isolated_db.load_driver(record["id"])["default_carrier_id"] == carrier_a
    finally:
        dialog.deleteLater()


def test_selection_is_single(qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs):
    """У перевозчиков выбор одиночный: привязка идёт «выбранному»."""
    dialog, _record = _dialog(isolated_db)
    try:
        assert (
            dialog.carriers_list.selectionMode()
            == QAbstractItemView.SingleSelection
        )
    finally:
        dialog.deleteLater()


# ─────────────────────────────────────────────────────────────
# Логи
# ─────────────────────────────────────────────────────────────

def test_actions_are_logged_without_personal_data(
    qt_app, isolated_db, carrier_a, carrier_b, quiet_dialogs, caplog
):
    """В лог уходят ID и количества — ФИО водителя не попадает."""
    caplog.set_level(logging.DEBUG, logger="ui.driver_carrier_dialog")

    dialog, record = _dialog(isolated_db)
    try:
        _select(dialog, carrier_b)
        dialog.btn_bind.click()
        dialog.btn_unbind.click()
    finally:
        dialog.deleteLater()

    assert f"driver_id={record['id']}" in caplog.text
    assert f"carrier_id={carrier_b}" in caplog.text
    assert DRIVER_NAME not in caplog.text, "ФИО в лог не пишем"
