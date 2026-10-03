#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты запуска приложения через WindowManager (ЭТАП 2B, main.py).

Проверяют: фабрики окон покрывают все пункты списка типов, «Экспедиторство»
открывает MainWindow, остальные типы — окно-заглушку, окна не пересоздаются
при возврате к типу, а автозакрытие приложения при закрытии последнего окна
выключено (окна лишь прячутся, данные в формах остаются).

`quitOnLastWindowClosed` проверяется в отдельном процессе: QApplication —
синглтон, и повторно создавать его внутри pytest нельзя.
Qt — в offscreen-режиме.
"""

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from ui.windows import WindowManager  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Ожидаемые ключи фабрик — ровно пункты списка типов договоров.
EXPECTED_TYPES = {
    "perevozka", "formika", "logistiks_rus", "arenda_ts", "zayavka_excel",
}


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def main_module():
    """Модуль main с подменённым argv: pytest-аргументы ему не подходят."""
    import importlib

    saved_argv = sys.argv
    sys.argv = ["main.py"]
    sys.modules.pop("main", None)
    try:
        module = importlib.import_module("main")
    finally:
        sys.argv = saved_argv
    yield module
    sys.modules.pop("main", None)


@pytest.fixture
def manager(main_module, qt_app, monkeypatch):
    """WindowManager с фабриками приложения; окна закрываются после теста."""
    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    manager = WindowManager(main_module._make_factories())
    yield manager

    for window in manager.windows().values():
        watcher = getattr(window, "system_theme_watcher", None)
        if watcher is not None:
            watcher.stop()
    manager.close_all()


# ─────────────────────────────────────────────────────────────
# Фабрики окон
# ─────────────────────────────────────────────────────────────

def test_factories_cover_all_picker_types(main_module):
    factories = main_module._make_factories()
    assert set(factories) == EXPECTED_TYPES
    assert len(factories) == 5
    assert all(callable(factory) for factory in factories.values())


def test_nothing_is_created_before_switch(main_module, manager):
    """Фабрики ленивые: до первого показа окна ничего не создано."""
    assert manager.windows() == {}


def test_perevozka_opens_main_window(manager):
    from ui.main_window import MainWindow

    window = manager.switch_to("perevozka")

    assert isinstance(window, MainWindow)
    assert window.isVisible() is True
    assert manager.current_type() == "perevozka"


@pytest.mark.parametrize("contract_type", [
    "formika", "logistiks_rus", "arenda_ts", "zayavka_excel",
])
def test_other_types_open_placeholder_window(manager, contract_type):
    from ui.placeholder_window import PlaceholderWindow

    window = manager.switch_to(contract_type)

    assert isinstance(window, PlaceholderWindow)
    assert window.contract_type == contract_type
    assert window.isVisible() is True
    assert manager.current_type() == contract_type


def test_switching_types_hides_previous_window(manager):
    from ui.main_window import MainWindow
    from ui.placeholder_window import PlaceholderWindow

    main_window = manager.switch_to("perevozka")
    placeholder = manager.switch_to("formika")

    assert isinstance(main_window, MainWindow)
    assert isinstance(placeholder, PlaceholderWindow)
    assert main_window.isVisible() is False
    assert placeholder.isVisible() is True


def test_return_to_type_keeps_same_window(manager):
    """Данные в форме не теряются: окно то же, а не созданное заново."""
    main_window = manager.switch_to("perevozka")
    main_window.carrier_tab.full_name.setText("ООО «Транс-Логистик»")

    manager.switch_to("formika")
    again = manager.switch_to("perevozka")

    assert again is main_window
    assert again.carrier_tab.full_name.text() == "ООО «Транс-Логистик»"


# ─────────────────────────────────────────────────────────────
# Автозакрытие приложения выключено
# ─────────────────────────────────────────────────────────────

_SUBPROCESS_SCRIPT = """
import sys
sys.argv = ["main.py"]

from PyQt5.QtWidgets import QApplication, QDialog

import db.database as database
database.init_database = lambda: None

from ui.contract_picker import ContractPickerDialog
ContractPickerDialog.exec_ = lambda self: QDialog.Rejected

import main
rc = main.main()

app = QApplication.instance()
print("RC=%s" % rc)
print("QUIT_ON_LAST=%s" % (app.quitOnLastWindowClosed() if app else "no-app"))
"""


def test_main_disables_quit_on_last_window_closed():
    """
    main.py выключает автозакрытие: закрытие окна — это hide(), а не выход.

    Проверяется в отдельном процессе: QApplication — синглтон, а main()
    создаёт его сам.
    """
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
    )

    assert result.returncode == 0, result.stderr
    assert "RC=0" in result.stdout, result.stdout
    assert "QUIT_ON_LAST=False" in result.stdout, result.stdout
