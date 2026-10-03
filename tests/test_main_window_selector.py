#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты селектора типа и кнопки «Выход» в шапке MainWindow (ЭТАП 2C, шаг 2C.5).

MainWindow остаётся рабочим окном «Экспедиторство» и НЕ становится
подклассом BaseContractWindow: здесь проверяется только то, что добавилось —
селектор типа договора, кнопка «Выход» и их сигналы. Прежние атрибуты окна
(кнопка «Создать договор», вкладки, сайдбар) не тронуты.

Qt — в offscreen-режиме.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qt_app, monkeypatch):
    """MainWindow без обращения к GigaChat и без модальных окон."""
    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    win = MainWindow()
    yield win

    win.system_theme_watcher.stop()
    win._force_close = True
    win.close()
    win.deleteLater()


# ─────────────────────────────────────────────────────────────
# Селектор типа договора
# ─────────────────────────────────────────────────────────────

def test_selector_exists_and_shows_perevozka(window):
    assert window.selector is not None
    assert window.selector.parent() is not None
    assert window.selector.current_type() == "perevozka"
    assert window.selector.combo.currentText() == "Экспедиторство"


def test_selector_has_all_picker_items(window):
    from core.contracts.picker_order import PICKER_ORDER

    assert window.selector.keys() == [key for key, _title in PICKER_ORDER]
    assert window.selector.titles() == [title for _key, title in PICKER_ORDER]


def test_selector_emits_switch_request(window):
    collected = []
    window.switch_to_type_requested.connect(collected.append)

    index = window.selector.keys().index("formika")
    window.selector.combo.setCurrentIndex(index)

    assert collected == ["formika"]


def test_programmatic_selector_change_does_not_emit(window):
    collected = []
    window.switch_to_type_requested.connect(collected.append)

    window.selector.set_current_type("arenda_ts")

    assert collected == []
    assert window.selector.current_type() == "arenda_ts"


def test_switch_signal_carries_string(window):
    collected = []
    window.switch_to_type_requested.connect(collected.append)

    index = window.selector.keys().index("zayavka_excel")
    window.selector.combo.setCurrentIndex(index)

    assert collected == ["zayavka_excel"]
    assert isinstance(collected[0], str)


# ─────────────────────────────────────────────────────────────
# Кнопка «Выход»
# ─────────────────────────────────────────────────────────────

def test_exit_button_in_header(window):
    assert window.btn_exit is not None
    assert window.btn_exit.parent() is not None
    assert window.btn_exit.text() == "Выход"
    assert window.btn_exit.objectName() == "secondary"


def test_exit_button_emits_exit_request(window):
    collected = []
    window.exit_requested.connect(lambda: collected.append(True))

    window.btn_exit.click()

    assert collected == [True]


# ─────────────────────────────────────────────────────────────
# Прежние элементы шапки не тронуты
# ─────────────────────────────────────────────────────────────

def test_create_contract_button_still_in_header(window):
    """Тест test_ui_theme.py:504 требует родителя у кнопки — она на месте."""
    assert window.btn_create_contract.parent() is not None
    assert window.btn_create_contract.objectName() == "accent"


def test_tabs_and_side_nav_unchanged(window):
    assert window.tabs.count() == 6
    assert window.side_nav.count() == 6
    assert [window.tabs.tabText(i) for i in range(6)] == [
        "Перевозчик", "Водитель", "Тягач и полуприцеп",
        "Перевозимые авто", "Договор", "Заказчик",
    ]


def test_main_window_still_needs_no_arguments(qt_app, monkeypatch):
    """Обратная совместимость: MainWindow() создаётся без аргументов."""
    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )

    win = MainWindow()
    try:
        assert win.selector.current_type() == "perevozka"
    finally:
        win.system_theme_watcher.stop()
        win._force_close = True
        win.close()
        win.deleteLater()
