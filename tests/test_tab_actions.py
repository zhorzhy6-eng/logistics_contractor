#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты панели действий на вкладках (ЭТАП 2B).

У каждой вкладки внизу есть «Создать договор» и «Очистить форму»; клики
уходят в окно сигналами create_contract_requested / clear_requested.
Сами сигналы объявлены в вкладках, а не в TabMixin (он не QObject).
Qt — в offscreen-режиме.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtTest import QSignalSpy  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QFrame, QGroupBox, QPushButton,
)

from ui.tabs.base_tab import TabMixin  # noqa: E402
from ui.tabs.carrier_tab import CarrierTab  # noqa: E402
from ui.tabs.contract_tab import ContractTab  # noqa: E402
from ui.tabs.customer_tab import CustomerTab  # noqa: E402
from ui.tabs.driver_tab import DriverTab  # noqa: E402
from ui.tabs.trailer_tab import TrailerTab  # noqa: E402
from ui.tabs.vehicles_tab import VehiclesTab  # noqa: E402

#: Все вкладки окна: (имя для отчёта, класс)
TAB_FACTORIES = [
    ("carrier", CarrierTab),
    ("driver", DriverTab),
    ("trailer", TrailerTab),
    ("vehicles", VehiclesTab),
    ("contract", ContractTab),
    ("customer", CustomerTab),
]


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(params=TAB_FACTORIES, ids=[name for name, _ in TAB_FACTORIES])
def tab(qt_app, request):
    """Свежая вкладка каждого типа."""
    _, factory = request.param
    widget = factory()
    yield widget
    widget.deleteLater()


# ─────────────────────────────────────────────────────────────
# Кнопки и панель
# ─────────────────────────────────────────────────────────────

def test_tab_has_both_action_buttons(tab):
    assert isinstance(tab.btn_create_contract, QPushButton)
    assert tab.btn_create_contract.text() == "Создать договор"
    assert isinstance(tab.btn_clear_form, QPushButton)
    assert tab.btn_clear_form.text() == "Очистить форму"


def test_action_buttons_have_theme_roles(tab):
    """«Создать» — акцент темы, «Очистить» — приглушённая кнопка."""
    assert tab.btn_create_contract.objectName() == "accent"
    assert tab.btn_clear_form.objectName() == "secondary"


def test_action_buttons_have_icons(tab):
    assert not tab.btn_create_contract.icon().isNull()
    assert not tab.btn_clear_form.icon().isNull()


def test_tab_actions_panel_is_action_bar(tab):
    """Панель — QFrame#actionBar, а не QGroupBox (список групп вкладки фиксирован)."""
    assert isinstance(tab._tab_actions, QFrame)
    assert tab._tab_actions.objectName() == "actionBar"
    assert not isinstance(tab._tab_actions, QGroupBox)


def test_tab_actions_panel_is_attached_below_content(tab):
    """Панель встроена в layout вкладки и идёт последней — то есть внизу."""
    layout = tab.layout()
    assert layout is not None
    assert tab._tab_actions.parent() is not None

    last = layout.itemAt(layout.count() - 1).widget()
    assert last is tab._tab_actions


def test_action_buttons_live_inside_panel(tab):
    assert tab.btn_create_contract.parent() is tab._tab_actions
    assert tab.btn_clear_form.parent() is tab._tab_actions


# ─────────────────────────────────────────────────────────────
# Сигналы
# ─────────────────────────────────────────────────────────────

def test_create_button_emits_signal(tab):
    spy = QSignalSpy(tab.create_contract_requested)
    tab.btn_create_contract.click()
    assert len(spy) == 1


def test_clear_button_emits_signal(tab):
    spy = QSignalSpy(tab.clear_requested)
    tab.btn_clear_form.click()
    assert len(spy) == 1


def test_clear_button_does_not_clear_fields_itself(tab):
    """Кнопка только сообщает о намерении: чистит окно, а не вкладка."""
    if hasattr(tab, "full_name"):
        tab.full_name.setText("Проверка")
    tab.btn_clear_form.click()
    if hasattr(tab, "full_name"):
        assert tab.full_name.text() == "Проверка"


def test_signals_are_not_in_mixin():
    """
    TabMixin — не QObject: pyqtSignal в нём был бы TypeError.

    Сигналы объявляет каждая вкладка, а миксин лишь вызывает emit().
    """
    from PyQt5.QtCore import QObject

    assert not issubclass(TabMixin, QObject)
    for name in ("create_contract_requested", "clear_requested"):
        assert name not in vars(TabMixin)


@pytest.mark.parametrize("name,expected", [
    ("create_contract_requested", "create_contract_requested"),
    ("clear_requested", "clear_requested"),
])
def test_every_tab_declares_signals(qt_app, name, expected):
    for tab_name, factory in TAB_FACTORIES:
        widget = factory()
        try:
            assert hasattr(widget, expected), tab_name
        finally:
            widget.deleteLater()


# ─────────────────────────────────────────────────────────────
# MainWindow: сигналы вкладок подключены к окну
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def window(qt_app, monkeypatch):
    """MainWindow без GigaChat, без таймера слежения за темой Windows."""
    from PyQt5.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    win = MainWindow()
    yield win
    win.system_theme_watcher.stop()
    win.close()
    win.deleteLater()


def test_window_has_no_clear_button_in_header(window):
    """«Очистить форму» из шапки убрана: она есть только на вкладках."""
    assert not hasattr(window, "btn_clear")


def test_window_keeps_create_button_in_header(window):
    """Кнопка «Создать договор» осталась в шапке."""
    assert window.btn_create_contract.parent() is not None


def test_tab_create_signal_is_connected_to_window(qt_app, monkeypatch):
    """Клик по кнопке вкладки доходит до MainWindow._on_create_contract."""
    from PyQt5.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    calls = []
    monkeypatch.setattr(
        MainWindow, "_on_create_contract", lambda self: calls.append("create")
    )

    win = MainWindow()
    try:
        for tab in (
            win.carrier_tab, win.driver_tab, win.trailer_tab,
            win.vehicles_tab, win.contract_tab, win.customer_tab,
        ):
            calls.clear()
            tab.btn_create_contract.click()
            assert calls == ["create"], type(tab).__name__
    finally:
        win.system_theme_watcher.stop()
        win.close()
        win.deleteLater()


def test_clear_tab_clears_only_that_tab(window):
    """_on_clear_tab чистит указанную вкладку и не трогает остальные."""
    window.driver_tab.full_name.setText("Иванов Иван Иванович")
    window.contract_tab.route.setText("Москва → Санкт-Петербург")

    window._on_clear_tab(window.contract_tab)

    assert window.contract_tab.route.text() == ""
    assert window.driver_tab.full_name.text() == "Иванов Иван Иванович"


def test_clear_button_on_tab_clears_only_that_tab(window):
    """Кнопка «Очистить форму» на вкладке действует только на свою вкладку."""
    window.driver_tab.full_name.setText("Иванов Иван Иванович")
    window.contract_tab.route.setText("Москва → Санкт-Петербург")

    window.driver_tab.btn_clear_form.click()

    assert window.driver_tab.full_name.text() == ""
    assert window.contract_tab.route.text() == "Москва → Санкт-Петербург"


def test_direct_clear_of_one_tab_does_not_touch_others(window):
    """Прямой вызов clear() на вкладке не затрагивает соседние."""
    window.carrier_tab.full_name.setText("ООО «Транс-Логистик»")
    window.contract_tab.route.setText("Москва → Санкт-Петербург")

    window.carrier_tab.clear()

    assert window.carrier_tab.full_name.text() == ""
    assert window.contract_tab.route.text() == "Москва → Санкт-Петербург"


def test_old_clear_form_still_clears_everything(window):
    """Старый _on_clear_form сохранён: чистит все вкладки (совместимость)."""
    window.driver_tab.full_name.setText("Иванов Иван Иванович")
    window.contract_tab.route.setText("Москва → Санкт-Петербург")

    window._on_clear_form()

    assert window.driver_tab.full_name.text() == ""
    assert window.contract_tab.route.text() == ""


def test_theme_change_rebuilds_tab_button_icons(window, qt_app):
    """Смена темы пересобирает значки кнопок на вкладках."""
    from ui import theme

    try:
        theme.apply_theme(qt_app, "classic")
        window._refresh_nav_icons()
        icon_before = window.contract_tab.btn_create_contract.icon()

        theme.apply_theme(qt_app, "dark_pro")
        window._refresh_nav_icons()
        icon_after = window.contract_tab.btn_create_contract.icon()

        assert not icon_after.isNull()
        assert icon_before.cacheKey() != icon_after.cacheKey()
    finally:
        theme.apply_theme(qt_app, "classic")

