#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты базового окна типа договора (ui/windows/base_window.py, ЭТАП 2C).

Каркас окна проверяется на подклассе с двумя вкладками: разметка вкладок
из TAB_CONFIGS, синхронизация сайдбара, селектор типа в шапке, кнопка
«Выход», заглушки действий и поведение закрытия (hide, а не уничтожение).

Qt — в offscreen-режиме.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

import ui.windows.base_window as base_window_module  # noqa: E402
from ui.windows.base_window import BaseContractWindow  # noqa: E402


class DemoWindow(BaseContractWindow):
    """Подкласс для тестов: две вкладки, как у настоящих окон типов."""

    CONTRACT_TYPE = "formika"
    WINDOW_TITLE = "Формика"
    TAB_CONFIGS = [
        ("Заказчик", "customer.svg"),
        ("Груз", "contract.svg"),
    ]


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def messages(monkeypatch):
    """Перехватывает QMessageBox.information: список показанных текстов."""
    shown = []

    def fake_information(parent, title, text, *args, **kwargs):
        shown.append((title, text))
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "information", staticmethod(fake_information))
    return shown


@pytest.fixture
def window(qt_app, messages):
    win = DemoWindow()
    win.show()
    yield win
    win.force_close()


# ─────────────────────────────────────────────────────────────
# Разметка окна
# ─────────────────────────────────────────────────────────────

def test_tabs_follow_tab_configs(window):
    assert window.tabs.count() == 2
    assert window.tab_titles() == ["Заказчик", "Груз"]


def test_side_nav_matches_tabs(window):
    assert window.side_nav.count() == 2
    assert window.side_nav.item_text(0) == "Заказчик"
    assert window.side_nav.item_text(1) == "Груз"


def test_side_nav_switches_tabs(window):
    window.side_nav.buttons()[1].click()

    assert window.tabs.currentIndex() == 1
    assert window.side_nav.current_index() == 1


def test_programmatic_tab_change_highlights_side_nav(window):
    window.tabs.setCurrentIndex(0)

    assert window.side_nav.current_index() == 0


def test_window_title_comes_from_subclass(window):
    assert window.windowTitle() == "Формика"


def test_every_tab_has_action_panel(window):
    for index in range(window.tabs.count()):
        tab = window.tabs.widget(index)
        assert tab.btn_create_contract.parent() is not None
        assert tab.btn_clear_form.parent() is not None
        assert tab.btn_recognize.parent() is not None


# ─────────────────────────────────────────────────────────────
# Селектор типа договора в шапке
# ─────────────────────────────────────────────────────────────

def test_selector_shows_current_type(window):
    assert window.selector.current_type() == "formika"
    assert window.selector.combo.currentText() == "Формика"


def test_selector_selection_emits_switch_request(qt_app, messages):
    win = DemoWindow()
    collected = []
    win.switch_to_type_requested.connect(collected.append)
    try:
        win.selector.combo.setCurrentIndex(0)  # «Экспедиторство»
        assert collected == ["perevozka"]
    finally:
        win.force_close()


def test_programmatic_selector_change_does_not_emit(qt_app, messages):
    win = DemoWindow()
    collected = []
    win.switch_to_type_requested.connect(collected.append)
    try:
        win.selector.set_current_type("perevozka")
        assert collected == []
        assert win.selector.current_type() == "perevozka"
    finally:
        win.force_close()


# ─────────────────────────────────────────────────────────────
# Кнопка «Выход»
# ─────────────────────────────────────────────────────────────

def test_exit_button_exists_in_header(window):
    assert window.btn_exit.parent() is not None
    assert window.btn_exit.text() == "Выход"
    assert window.btn_exit.objectName() == "secondary"


def test_exit_button_emits_exit_request(qt_app, messages):
    win = DemoWindow()
    collected = []
    win.exit_requested.connect(lambda: collected.append(True))
    try:
        win.btn_exit.click()
        assert collected == [True]
    finally:
        win.force_close()


# ─────────────────────────────────────────────────────────────
# Заглушки действий
# ─────────────────────────────────────────────────────────────

def test_create_contract_button_shows_placeholder(window, messages):
    tab = window.tabs.widget(0)
    tab.btn_create_contract.click()

    assert len(messages) == 1
    _title, text = messages[0]
    assert "Формика" in text
    assert "разработке" in text or "следующих версиях" in text


def test_clear_button_shows_placeholder(window, messages):
    tab = window.tabs.widget(1)
    tab.btn_clear_form.click()

    assert len(messages) == 1
    _title, text = messages[0]
    assert "Формика" in text


def test_recognize_button_calls_prompt_lookup(window, monkeypatch, messages):
    calls = []

    def fake_get_prompt(contract_type):
        calls.append(contract_type)
        return None

    monkeypatch.setattr(base_window_module, "get_prompt", fake_get_prompt)

    tab = window.tabs.widget(0)
    tab.btn_recognize.click()

    assert calls == ["formika"]
    assert len(messages) == 1
    assert "ещё не написан" in messages[0][1]


def test_recognize_reports_existing_prompt(window, monkeypatch, messages):
    monkeypatch.setattr(base_window_module, "get_prompt", lambda _t: "текст промпта")

    window.tabs.widget(0).btn_recognize.click()

    assert "Промпт: задан" in messages[0][1]


# ─────────────────────────────────────────────────────────────
# Закрытие окна
# ─────────────────────────────────────────────────────────────

def test_close_hides_window_without_destroying_it(qt_app, messages):
    win = DemoWindow()
    win.show()

    win.close()

    # окно живо (обращение к нему не падает), но скрыто
    assert win.isVisible() is False
    assert win.tabs.count() == 2
    assert win.selector.current_type() == "formika"

    win.force_close()


def test_force_close_really_closes_window(qt_app, messages):
    win = DemoWindow()
    win.show()

    win.force_close()

    assert win.isVisible() is False
    assert getattr(win, "_force_close", False) is True


def test_base_window_declares_required_signals(qt_app, messages):
    win = DemoWindow()
    try:
        assert hasattr(win, "switch_to_type_requested")
        assert hasattr(win, "exit_requested")
        # сигналы можно подключать — значит, это настоящие pyqtSignal
        win.switch_to_type_requested.connect(lambda _key: None)
        win.exit_requested.connect(lambda: None)
    finally:
        win.force_close()


def test_unknown_contract_type_does_not_break_window(qt_app, messages):
    class BrokenWindow(BaseContractWindow):
        CONTRACT_TYPE = "unknown_type"

    win = BrokenWindow()
    try:
        assert win.windowTitle() == "unknown_type"
        assert win.tabs.count() == 0
    finally:
        win.force_close()
