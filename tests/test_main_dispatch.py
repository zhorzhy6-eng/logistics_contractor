#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты запуска приложения через WindowManager (ЭТАПЫ 2B и 2C, main.py).

Проверяют: фабрики окон покрывают все пункты списка типов, «Экспедиторство»
открывает MainWindow, у остальных типов — свои окна-каркасы (ЭТАП 2C),
окна не пересоздаются при возврате к типу, сигналы окна переключают типы,
а автозакрытие приложения при закрытии последнего окна выключено
(окна лишь прячутся, данные в формах остаются).

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


#: Ожидаемое окно для каждого типа (ЭТАП 2C: окна типов, не заглушка).
EXPECTED_WINDOWS = {
    "formika": "FormikaWindow",
    "logistiks_rus": "LogistiksRusWindow",
    "arenda_ts": "ArendaTsWindow",
    "zayavka_excel": "HavalyWindow",
}


@pytest.mark.parametrize("contract_type", [
    "formika", "logistiks_rus", "arenda_ts", "zayavka_excel",
])
def test_other_types_open_their_windows(manager, contract_type):
    """У каждого типа договора — своё окно-каркас (ЭТАП 2C, шаг 2C.4)."""
    from ui.windows.base_window import BaseContractWindow

    window = manager.switch_to(contract_type)

    assert isinstance(window, BaseContractWindow)
    assert type(window).__name__ == EXPECTED_WINDOWS[contract_type]
    assert window.CONTRACT_TYPE == contract_type
    assert window.isVisible() is True
    assert manager.current_type() == contract_type


def test_switching_types_hides_previous_window(manager):
    from ui.main_window import MainWindow
    from ui.windows.formika import FormikaWindow

    main_window = manager.switch_to("perevozka")
    formika = manager.switch_to("formika")

    assert isinstance(main_window, MainWindow)
    assert isinstance(formika, FormikaWindow)
    assert main_window.isVisible() is False
    assert formika.isVisible() is True


def test_return_to_type_keeps_same_window(manager):
    """Данные в форме не теряются: окно то же, а не созданное заново."""
    main_window = manager.switch_to("perevozka")
    main_window.carrier_tab.full_name.setText("ООО «Транс-Логистик»")

    manager.switch_to("formika")
    again = manager.switch_to("perevozka")

    assert again is main_window
    assert again.carrier_tab.full_name.text() == "ООО «Транс-Логистик»"


# ─────────────────────────────────────────────────────────────
# Сигналы окон: переключение типа и выход (ЭТАП 2C)
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def receiver(main_module, manager, qt_app):
    """Приёмник сигналов окон: то же, что создаёт main() при запуске."""
    return main_module._make_signals_receiver(manager, qt_app)


def test_switch_opens_window_and_wires_signals(main_module, manager, receiver):
    from ui.windows.formika import FormikaWindow

    window = main_module._switch(manager, receiver, "formika")

    assert isinstance(window, FormikaWindow)
    assert getattr(window, "_type_signals_wired", False) is True


def test_switch_sets_selector_to_window_type(main_module, manager, receiver):
    window = main_module._switch(manager, receiver, "arenda_ts")

    assert window.selector.current_type() == "arenda_ts"


def test_window_selector_switches_back_to_perevozka(main_module, manager,
                                                    receiver):
    """Селектор в шапке окна типа переключает окна через тот же менеджер."""
    from ui.main_window import MainWindow

    window = main_module._switch(manager, receiver, "formika")
    index = window.selector.keys().index("perevozka")
    window.selector.combo.setCurrentIndex(index)

    assert manager.current_type() == "perevozka"
    assert isinstance(manager.current_window(), MainWindow)
    assert manager.current_window().selector.current_type() == "perevozka"


def test_signals_are_wired_once(main_module, manager, receiver, monkeypatch):
    """Повторное подключение сигналов не удваивает переключения."""
    calls = []
    original = manager.switch_to

    def spy(contract_type):
        calls.append(contract_type)
        return original(contract_type)

    monkeypatch.setattr(manager, "switch_to", spy)

    first = main_module._switch(manager, receiver, "formika")
    second = main_module._switch(manager, receiver, "formika")

    assert first is second
    assert calls == ["formika", "formika"]

    index = first.selector.keys().index("perevozka")
    first.selector.combo.setCurrentIndex(index)

    assert calls == ["formika", "formika", "perevozka"]


def test_switch_unknown_type_keeps_previous_window(main_module, qt_app,
                                                   monkeypatch):
    """Тип без фабрики: окно не меняется, селектор возвращается назад."""
    from ui.main_window import MainWindow
    from ui.windows import WindowManager

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    manager = WindowManager({"perevozka": lambda: MainWindow()})
    receiver = main_module._make_signals_receiver(manager, qt_app)
    try:
        window = main_module._switch(manager, receiver, "perevozka")
        window.selector.set_current_type("formika")

        assert main_module._switch(manager, receiver, "formika") is None
        assert manager.current_type() == "perevozka"
        assert window.selector.current_type() == "perevozka"
    finally:
        manager.close_all()


def test_do_exit_closes_windows_and_quits(main_module, manager, qt_app,
                                          monkeypatch):
    closed = []
    quit_calls = []
    monkeypatch.setattr(manager, "close_all", lambda: closed.append(True))
    monkeypatch.setattr(qt_app, "quit", lambda: quit_calls.append(True))

    main_module._do_exit(manager, qt_app)

    assert closed == [True]
    assert quit_calls == [True]


def test_do_exit_survives_without_manager_and_app(main_module):
    """Выход не должен падать, даже если ссылки уже потеряны."""
    main_module._do_exit(None, None)


def test_window_exit_signal_reaches_do_exit(main_module, manager, qt_app,
                                            receiver, monkeypatch):
    calls = []
    monkeypatch.setattr(
        main_module, "_do_exit",
        lambda mgr, app: calls.append((mgr, app)),
    )

    window = main_module._switch(manager, receiver, "zayavka_excel")
    window.exit_requested.emit()

    assert len(calls) == 1
    assert calls[0][0] is manager
    assert calls[0][1] is qt_app


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


# ─────────────────────────────────────────────────────────────
# Настоящий запуск: переключение типов и выход (ЭТАП 2C)
#
# Сценарий повторяет работу пользователя: запуск → выбор
# «Экспедиторство» → переключение на «Формика» селектором в шапке →
# возврат → кнопка «Выход». Проверяется в отдельном процессе (Qt —
# синглтон) и заодно ловит циклы ссылок Python ↔ Qt: с ними процесс
# падал по access violation при завершении (грабли шага 2B.7).
# ─────────────────────────────────────────────────────────────

_SWITCH_SUBPROCESS_SCRIPT = """
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.argv = ["main.py"]

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox

# Модальные окна в прогоне не показываем: сценарий проверяет переключение
# типов и выход, а не диалоги.
QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)

import db.database as database
database.init_database = lambda: None

from ui.contract_picker import ContractPickerDialog
ContractPickerDialog.exec_ = lambda self: QDialog.Accepted
ContractPickerDialog.selected_type = lambda self: "perevozka"

import main

results = []


def visible_window():
    app = QApplication.instance()
    for widget in app.topLevelWidgets():
        if widget.isVisible() and hasattr(widget, "selector"):
            return widget
    return None


def step1():
    window = visible_window()
    results.append("START=%s" % type(window).__name__)
    results.append("START_TABS=%s" % window.tabs.count())
    index = window.selector.keys().index("formika")
    window.selector.combo.setCurrentIndex(index)
    QTimer.singleShot(0, step2)


def step2():
    window = visible_window()
    results.append("AFTER_SWITCH=%s" % type(window).__name__)
    results.append("SELECTOR=%s" % window.selector.current_type())
    results.append("EXIT_BTN=%s" % window.btn_exit.text())
    results.append("TABS=%s" % ",".join(window.tab_titles()))
    index = window.selector.keys().index("perevozka")
    window.selector.combo.setCurrentIndex(index)
    QTimer.singleShot(0, step3)


def step3():
    window = visible_window()
    results.append("BACK=%s" % type(window).__name__)
    window.btn_exit.click()


QTimer.singleShot(0, step1)

rc = main.main()
print("RC=%s" % rc)
for line in results:
    print(line)

app = QApplication.instance()
alive = [w for w in app.topLevelWidgets() if w.isVisible()] if app else []
print("VISIBLE_LEFT=%s" % len(alive))
print("DONE")
"""


def test_real_main_switches_types_and_exits(work_file):
    """
    Реальный main(): селектор переключает окна, «Выход» завершает работу.

    Сценарий пишется во временный файл, а не передаётся аргументом -c:
    длинная командная строка не проходит на Windows (WinError 5).
    """
    script_path = work_file("_main_switch_scenario.py")
    script_path.write_text(_SWITCH_SUBPROCESS_SCRIPT, encoding="utf-8")

    env = dict(
        os.environ,
        QT_QPA_PLATFORM="offscreen",
        PYTHONIOENCODING="utf-8",
        PYTHONPATH=str(PROJECT_ROOT),
    )
    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
    )

    assert result.returncode == 0, result.stderr
    for marker in (
        "RC=0",
        "START=MainWindow",
        "START_TABS=6",
        "AFTER_SWITCH=FormikaWindow",
        "SELECTOR=formika",
        "EXIT_BTN=Выход",
        "TABS=Заказчик,Груз,Маршрут,Водитель,ТС,Стоимость",
        "BACK=MainWindow",
        "VISIBLE_LEFT=0",
        "DONE",
    ):
        assert marker in result.stdout, (
            f"нет маркера {marker!r} в выводе:\n{result.stdout}"
        )
