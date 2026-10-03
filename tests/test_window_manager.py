#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты менеджера окон типов договоров (ЭТАП 2B, ui/windows/manager.py).

Проверяют: окна создаются лениво и по одному разу, переключение скрывает
предыдущее окно, реестр содержит только созданные окна, close_all()
действительно закрывает окна. Окна в тестах — лёгкие заглушки: настоящие
MainWindow и PlaceholderWindow проверяются в своих тестах.
Qt — в offscreen-режиме.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMainWindow  # noqa: E402

from ui.windows import WindowManager  # noqa: E402


class FakeWindow(QMainWindow):
    """Минимальное окно: видимость, заголовок и след закрытия."""

    def __init__(self, contract_type: str):
        super().__init__()
        self.contract_type = contract_type
        self.setWindowTitle(f"Окно {contract_type}")
        self.closed = False
        self.forced = False

    def close(self):
        self.closed = True
        return super().close()


class ForcedFakeWindow(FakeWindow):
    """Окно, умеющее реально закрываться (как MainWindow: force_close)."""

    def force_close(self):
        self.forced = True
        self.closed = True
        super().close()


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def factories():
    """Фабрики окон со счётчиком вызовов."""
    calls = []

    def make(contract_type):
        def factory():
            calls.append(contract_type)
            return FakeWindow(contract_type)
        return factory

    manager = WindowManager({
        "perevozka": make("perevozka"),
        "formika": make("formika"),
        "logistiks_rus": make("logistiks_rus"),
    })
    return manager, calls


# ─────────────────────────────────────────────────────────────
# Ленивое создание
# ─────────────────────────────────────────────────────────────

def test_no_windows_before_first_use(factories):
    manager, calls = factories
    assert manager.windows() == {}
    assert manager.current_type() is None
    assert manager.current_window() is None
    assert calls == []


def test_window_created_lazily_and_cached(factories, qt_app):
    manager, calls = factories

    first = manager.window_for("perevozka")
    second = manager.window_for("perevozka")

    assert isinstance(first, FakeWindow)
    assert first is second
    assert calls == ["perevozka"]
    assert list(manager.windows()) == ["perevozka"]


def test_unknown_type_returns_none(factories):
    manager, calls = factories
    assert manager.window_for("unknown_type") is None
    assert calls == []
    assert manager.windows() == {}


# ─────────────────────────────────────────────────────────────
# Переключение
# ─────────────────────────────────────────────────────────────

def test_switch_to_creates_and_shows_window(factories, qt_app):
    manager, calls = factories

    window = manager.switch_to("perevozka")

    assert isinstance(window, FakeWindow)
    assert window.isVisible() is True
    assert manager.current_type() == "perevozka"
    assert manager.current_window() is window
    assert calls == ["perevozka"]


def test_switch_hides_previous_window(factories, qt_app):
    manager, calls = factories

    first = manager.switch_to("perevozka")
    second = manager.switch_to("formika")

    assert first.isVisible() is False
    assert second.isVisible() is True
    assert manager.current_type() == "formika"
    assert calls == ["perevozka", "formika"]


def test_switch_back_does_not_recreate_window(factories, qt_app):
    manager, calls = factories

    first = manager.switch_to("perevozka")
    manager.switch_to("formika")
    again = manager.switch_to("perevozka")

    assert again is first
    assert again.isVisible() is True
    assert calls == ["perevozka", "formika"]


def test_switch_to_same_type_twice(factories, qt_app):
    manager, calls = factories

    first = manager.switch_to("perevozka")
    second = manager.switch_to("perevozka")

    assert first is second
    assert second.isVisible() is True
    assert calls == ["perevozka"]


def test_switch_to_unknown_type_keeps_current_window(factories, qt_app):
    """Неизвестный тип не должен оставить пользователя без окна."""
    manager, _calls = factories

    window = manager.switch_to("perevozka")
    assert manager.switch_to("no_such_type") is None

    assert window.isVisible() is True
    assert manager.current_type() == "perevozka"


def test_windows_contains_only_created(factories, qt_app):
    manager, _calls = factories

    manager.switch_to("perevozka")
    manager.switch_to("logistiks_rus")

    assert set(manager.windows()) == {"perevozka", "logistiks_rus"}


# ─────────────────────────────────────────────────────────────
# Закрытие
# ─────────────────────────────────────────────────────────────

def test_close_all_closes_windows(factories, qt_app):
    manager, _calls = factories

    first = manager.switch_to("perevozka")
    second = manager.switch_to("formika")

    manager.close_all()

    assert first.closed is True
    assert second.closed is True
    assert first.isVisible() is False
    assert second.isVisible() is False
    assert manager.windows() == {}
    assert manager.current_type() is None
    assert manager.current_window() is None


def test_close_all_uses_force_close_when_available(qt_app):
    """Окна, которые крестиком только прячутся, закрываются через force_close."""
    manager = WindowManager({"perevozka": lambda: ForcedFakeWindow("perevozka")})

    window = manager.switch_to("perevozka")
    manager.close_all()

    assert window.forced is True


def test_close_all_on_empty_manager_is_safe(factories):
    manager, _calls = factories
    manager.close_all()
    assert manager.windows() == {}
    assert manager.current_type() is None
