#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты окна-заглушки для нереализованных типов договоров
(ui/placeholder_window.py, шаг 6).

Проверяют: заголовок окна и текст сообщения содержат название выбранного
типа, ключ типа сохраняется, кнопка «Закрыть» действительно закрывает окно,
а неизвестный ключ не роняет окно. Qt — в offscreen-режиме.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ui.placeholder_window import PlaceholderWindow  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qt_app):
    win = PlaceholderWindow("formika", "Формика")
    yield win
    win.close()
    win.deleteLater()


# ─────────────────────────────────────────────────────────────
# Заголовок и текст
# ─────────────────────────────────────────────────────────────

def test_window_title_names_type(window):
    assert "Формика" in window.windowTitle()
    assert "разработке" in window.windowTitle().lower()


def test_message_names_type(window):
    assert window.message() == "Тип договора «Формика» в разработке"


def test_contract_type_is_stored(window):
    assert window.contract_type == "formika"


def test_hint_mentions_working_type(window):
    """Пользователю сказано, какой тип работает: экспедиторство."""
    assert "Экспедиторство" in window.hint.text()


def test_type_key_is_shown_for_diagnostics(window):
    assert "formika" in window.type_label.text()


# ─────────────────────────────────────────────────────────────
# Кнопка «Закрыть»
# ─────────────────────────────────────────────────────────────

def test_close_button_exists(window):
    assert window.close_button().text() == "Закрыть"
    assert window.close_button().objectName() == "secondary"


def test_close_button_closes_window(window):
    window.show()
    assert window.isVisible() is True

    window.close_button().click()
    assert window.isVisible() is False


# ─────────────────────────────────────────────────────────────
# Все заготовки типов
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key,title", [
    ("logistiks_rus", "Логистикс Рус"),
    ("arenda_ts", "Разовая аренда"),
    ("zayavka_excel", "Хавалы"),
])
def test_each_stub_type_opens(qt_app, key, title):
    win = PlaceholderWindow(key, title)
    try:
        assert title in win.message()
        assert win.contract_type == key
    finally:
        win.close()
        win.deleteLater()


def test_title_falls_back_to_picker(qt_app):
    """Без явного названия берётся подпись из списка типов."""
    win = PlaceholderWindow("zayavka_excel")
    try:
        assert "Хавалы" in win.message()
    finally:
        win.close()
        win.deleteLater()


def test_unknown_type_does_not_crash(qt_app):
    win = PlaceholderWindow("unknown_type", "")
    try:
        assert "unknown_type" in win.message()
    finally:
        win.close()
        win.deleteLater()
