#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты селектора типа договора в шапке окна
(ui/controls/contract_type_selector.py, ЭТАП 2C).

Селектор — только переключатель: он показывает текущий тип и сообщает
сигналом о выборе другого. Переключение окон делает main.py, поэтому
здесь проверяется именно поведение виджета: состав и порядок пунктов,
программная установка без сигнала и эмиссия ровно один раз на смену типа.

Qt поднимается в offscreen-режиме: окна не показываются.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from core.contracts.picker_order import PICKER_ORDER  # noqa: E402
from ui.controls.contract_type_selector import ContractTypeSelector  # noqa: E402

#: Ожидаемый порядок пунктов — из задания, а не из реестра (там алфавит).
EXPECTED_ORDER = [
    ("perevozka", "Экспедиторство"),
    ("formika", "Формика"),
    ("logistiks_rus", "Логистикс Рус"),
    ("arenda_ts", "Разовая аренда"),
    ("zayavka_excel", "Хавалы"),
]


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def selector(qt_app):
    widget = ContractTypeSelector("perevozka")
    yield widget
    widget.deleteLater()


@pytest.fixture
def emitted(qt_app):
    """Сборщик выбранных типов: подключается к сигналу селектора."""
    collected = []

    def _connect(widget):
        widget.contract_type_selected.connect(collected.append)
        return collected

    _connect.collected = collected  # type: ignore[attr-defined]
    return _connect


# ─────────────────────────────────────────────────────────────
# Состав и начальное состояние
# ─────────────────────────────────────────────────────────────

def test_selector_has_five_items_in_required_order(selector):
    assert selector.combo.count() == 5
    assert list(zip(selector.keys(), selector.titles())) == EXPECTED_ORDER


def test_selector_uses_shared_picker_order(selector):
    """Порядок берётся из core/contracts/picker_order.py, а не задан заново."""
    assert selector.keys() == [key for key, _title in PICKER_ORDER]
    assert selector.titles() == [title for _key, title in PICKER_ORDER]


def test_selector_starts_on_given_type(selector):
    assert selector.current_type() == "perevozka"
    assert selector.combo.currentText() == "Экспедиторство"
    assert selector.combo.itemData(selector.combo.currentIndex()) == "perevozka"


@pytest.mark.parametrize("contract_type", [
    "perevozka", "formika", "logistiks_rus", "arenda_ts", "zayavka_excel",
])
def test_selector_can_be_created_for_every_type(qt_app, contract_type):
    widget = ContractTypeSelector(contract_type)
    try:
        assert widget.current_type() == contract_type
    finally:
        widget.deleteLater()


# ─────────────────────────────────────────────────────────────
# Программная установка
# ─────────────────────────────────────────────────────────────

def test_set_current_type_does_not_emit(selector, emitted):
    collected = emitted(selector)

    selector.set_current_type("formika")

    assert selector.current_type() == "formika"
    assert collected == []


def test_set_current_type_switches_visible_item(selector):
    selector.set_current_type("arenda_ts")

    assert selector.combo.currentText() == "Разовая аренда"
    assert selector.combo.currentIndex() == 3


def test_set_unknown_type_keeps_selection(selector):
    """Неизвестный ключ не роняет окно и не меняет показанный тип."""
    selector.set_current_type("expediciya")

    assert selector.current_type() == "perevozka"


# ─────────────────────────────────────────────────────────────
# Выбор пользователя
# ─────────────────────────────────────────────────────────────

def test_user_selection_emits_key(selector, emitted):
    collected = emitted(selector)

    index = selector.keys().index("formika")
    selector.combo.setCurrentIndex(index)

    assert collected == ["formika"]
    assert selector.current_type() == "formika"


def test_selection_emits_once_per_change(selector, emitted):
    collected = emitted(selector)

    selector.combo.setCurrentIndex(selector.keys().index("logistiks_rus"))
    selector.combo.setCurrentIndex(selector.keys().index("arenda_ts"))

    assert collected == ["logistiks_rus", "arenda_ts"]


def test_reselecting_current_type_does_not_emit(selector, emitted):
    collected = emitted(selector)

    # setCurrentIndex на тот же индекс сигнала не даёт
    selector.combo.setCurrentIndex(selector.combo.currentIndex())

    assert collected == []


def test_returning_to_programmatic_type_emits_after_user_choice(selector, emitted):
    """
    Возврат к «своему» типу после чужого выбора — это тоже смена: окно
    получило сигнал и должно вернуться к прежнему типу.
    """
    collected = emitted(selector)

    selector.combo.setCurrentIndex(selector.keys().index("zayavka_excel"))
    assert collected == ["zayavka_excel"]

    selector.combo.setCurrentIndex(selector.keys().index("perevozka"))
    assert collected == ["zayavka_excel", "perevozka"]


def test_selector_is_themed_widget_with_label(qt_app):
    from PyQt5.QtWidgets import QLabel

    widget = ContractTypeSelector("perevozka")
    try:
        labels = [label.text() for label in widget.findChildren(QLabel)]
        assert "Тип договора:" in labels
    finally:
        widget.deleteLater()


def test_selector_has_no_exit_or_window_logic(qt_app):
    """Виджет не управляет окнами: у него нет ни кнопок, ни окна-владельца."""
    from PyQt5.QtWidgets import QPushButton

    widget = ContractTypeSelector("perevozka")
    try:
        assert widget.findChildren(QPushButton) == []
        assert widget.parent() is None
    finally:
        widget.deleteLater()
