#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты закрытия окон типов договоров (ЭТАП 2B).

Крестик (или кнопка «Закрыть» в заглушке) окно только прячет: окна типов
живут всё время приложения, и данные в формах не должны теряться. Реальное
закрытие — force_close() (его вызывает WindowManager.close_all()).
Qt — в offscreen-режиме.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from ui.placeholder_window import PlaceholderWindow  # noqa: E402
from ui.windows import WindowManager  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def main_window(qt_app, monkeypatch):
    """MainWindow без GigaChat и без слежения за темой Windows."""
    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    window = MainWindow()
    yield window
    window.system_theme_watcher.stop()
    window.force_close()
    window.deleteLater()


@pytest.fixture
def placeholder(qt_app):
    window = PlaceholderWindow("formika", "Формика")
    yield window
    window.force_close()
    window.deleteLater()


# ─────────────────────────────────────────────────────────────
# MainWindow
# ─────────────────────────────────────────────────────────────

def test_main_window_close_hides_instead_of_closing(main_window):
    main_window.show()
    assert main_window.isVisible() is True

    main_window.close()

    assert main_window.isHidden() is True
    assert main_window.isVisible() is False


def test_main_window_stays_alive_after_close(main_window):
    """Окно не уничтожено: данные формы целы и его можно показать снова."""
    main_window.show()
    main_window.carrier_tab.full_name.setText("ООО «Транс-Логистик»")
    main_window.contract_tab.route.setText("Москва → Санкт-Петербург")

    main_window.close()

    assert main_window.carrier_tab.full_name.text() == "ООО «Транс-Логистик»"
    assert main_window.contract_tab.route.text() == "Москва → Санкт-Петербург"

    main_window.show()
    assert main_window.isVisible() is True


def test_main_window_force_close_is_allowed(main_window):
    main_window.show()

    main_window.force_close()

    assert main_window.isHidden() is True
    assert main_window._force_close is True


def test_main_window_close_twice_is_safe(main_window):
    main_window.close()
    main_window.close()
    assert main_window.isHidden() is True


# ─────────────────────────────────────────────────────────────
# PlaceholderWindow
# ─────────────────────────────────────────────────────────────

def test_placeholder_close_hides_instead_of_closing(placeholder):
    placeholder.show()
    assert placeholder.isVisible() is True

    placeholder.close()

    assert placeholder.isHidden() is True
    assert placeholder.contract_type == "formika"


def test_placeholder_close_button_hides_window(placeholder):
    """Кнопка «Закрыть» в заглушке прячет окно, не уничтожая его."""
    placeholder.show()

    placeholder.close_button().click()

    assert placeholder.isVisible() is False
    assert placeholder.message() == "Тип договора «Формика» в разработке"

    placeholder.show()
    assert placeholder.isVisible() is True


def test_placeholder_force_close_is_allowed(placeholder):
    placeholder.show()

    placeholder.force_close()

    assert placeholder.isHidden() is True
    assert placeholder._force_close is True


# ─────────────────────────────────────────────────────────────
# Связка с WindowManager: выход приложения закрывает окна по-настоящему
# ─────────────────────────────────────────────────────────────

def test_manager_close_all_really_closes_windows(qt_app):
    manager = WindowManager({
        "formika": lambda: PlaceholderWindow("formika", "Формика"),
    })

    window = manager.switch_to("formika")
    assert window.isVisible() is True

    manager.close_all()

    assert window._force_close is True
    assert window.isHidden() is True
    assert manager.windows() == {}
    assert manager.current_type() is None
