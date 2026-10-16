#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Инструменты формы выполняются в потоке интерфейса.

НАЙДЕНО ВТОРЫМ ПРОГОНОМ СТРЕСС-ТЕСТА (15.10.2026). Прогон дошёл до №83
(третье автозаполнение) и упал:

    QObject: Cannot create children for a parent that is in a different
    thread. (Parent is QTextDocument(…), parent's thread is QThread(…),
    current thread is QThread(…))

Перед этим на каждом запросе к форме Qt писал: «Cannot set parent, new
parent is in a different thread», «installEventFilter(): Cannot filter events
for objects in a different thread», «startTimer: Timers cannot be started from
another thread».

Причина: агент работает в рабочем потоке, а инструменты вида
``get_form_state`` и ``parse_and_fill`` читают и меняют ВИДЖЕТЫ формы.
Здесь проверяется правило, которое это закрывает: такие инструменты
выполняются в потоке интерфейса, а рабочий ждёт результат.
"""

import os
import sys
import threading
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import QThread, QTimer  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget,
)

from core.agent.tools import AgentContext, Tool, ToolRegistry  # noqa: E402
from core.settings_service import SettingsService  # noqa: E402


@pytest.fixture(scope="module")
def app():
    """QApplication на весь модуль (Qt допускает только один)."""
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def flush_events():
    """Прокручивает события после каждого теста (очередь удалений Qt)."""
    yield
    application = QApplication.instance()
    if application is not None:
        application.processEvents()


@pytest.fixture
def install_service(work_file, monkeypatch):
    """Сервис настроек в tests/_tmp вместо рабочего config/settings.json."""
    import ui.agent_widget as widget_module

    service = SettingsService(str(work_file("gui_tools_settings.json")))
    service.update({"agent": dict(SettingsService.DEFAULTS["agent"])})
    monkeypatch.setattr(widget_module, "get_settings_service", lambda: service)
    return service


@pytest.fixture
def window(app):
    """Минимальное окно (как MainWindow: шапка и центральный виджет)."""

    class StubWindow(QMainWindow):
        CONTRACT_TYPE = "perevozka"

        def __init__(self):
            super().__init__()
            central = QWidget()
            self.setCentralWidget(central)
            layout = QVBoxLayout(central)
            self.header = QWidget()
            self.header_layout = QHBoxLayout(self.header)
            self.header_layout.addWidget(QLabel("Заголовок"))
            layout.addWidget(self.header)
            self.resize(900, 600)

    window = StubWindow()
    window.show()
    app.processEvents()
    yield window
    from ui.agent_widget import uninstall_agent

    uninstall_agent(window)
    window.close()


def build_widget(window):
    """Ставит Максимилиана и возвращает виджет."""
    from ui.agent_widget import install_agent

    widget = install_agent(window, open_on_start=True)
    assert widget is not None
    widget.show_panel()
    widget.finish_panel_animation()
    QApplication.processEvents()
    return widget


def run_in_thread(func, ms: int = 300):
    """
    Выполняет функцию в отдельном потоке С ЦИКЛОМ СОБЫТИЙ.

    Цикл нужен, чтобы имитировать рабочий поток агента: он живёт до выхода
    из программы и обрабатывает события.
    """
    class Worker(QThread):
        def run(self):
            func()
            QTimer.singleShot(ms, self.quit)
            self.exec_()

    thread = Worker()
    thread.start()
    for _ in range(600):
        QApplication.processEvents()
        if thread.isFinished():
            break
        thread.wait(10)
    thread.wait(3000)
    return thread


# ─────────────────────────────────────────────────────────────
# Что и куда переведено
# ─────────────────────────────────────────────────────────────

def test_gui_tools_are_listed():
    """Список инструментов формы не пуст и не разошёлся с реестром."""
    from ui.agent_widget import GUI_TOOLS

    assert "parse_and_fill" in GUI_TOOLS
    assert "get_form_state" in GUI_TOOLS
    assert "get_current_form" in GUI_TOOLS
    assert "search_anything" not in GUI_TOOLS      # поиск форму не трогает


def test_widget_routes_registry_tools(window):
    """
    Реестр агента: инструменты формы обёрнуты, остальные — нет.

    Так ядро остаётся без знания о Qt, а решение принимает интерфейс.
    """
    from ui.agent_widget import _GuiTool

    widget = build_widget(window)
    widget._load_history(20, 3)
    registry = widget._agent.registry
    for name in ("parse_and_fill", "get_form_state", "get_current_form"):
        assert isinstance(registry.get(name).function, _GuiTool), name
    for name in ("search_anything", "count_records", "search_legal"):
        assert not isinstance(registry.get(name).function, _GuiTool), name


def test_routing_counts_once(window):
    """Повторный перевод ничего не ломает: обёртка не оборачивается дважды."""
    from ui.agent_widget import _GuiTool

    widget = build_widget(window)
    widget._load_history(20, 3)
    agent = widget._agent
    assert widget._route_gui_tools(agent) == 0
    assert isinstance(agent.registry.get("parse_and_fill").function, _GuiTool)


# ─────────────────────────────────────────────────────────────
# Поток вызова
# ─────────────────────────────────────────────────────────────

def test_gui_tool_helper_calls_directly_from_the_gui_thread(window):
    """Из потока интерфейса вызов идёт сразу, без сигналов."""
    from ui.agent_widget import run_in_gui_thread

    widget = build_widget(window)
    seen = []

    def function(**arguments):
        seen.append((threading.get_ident(), arguments))
        return {"ok": True}

    result = run_in_gui_thread(widget, function, {"query": "Ромашка"})

    assert result == {"ok": True}
    assert seen == [(threading.get_ident(), {"query": "Ромашка"})]
    assert widget._gui_call is None


def test_gui_tool_helper_moves_the_call_to_the_gui_thread(window):
    """
    Из рабочего потока инструмент выполняется в ПОТОКЕ ИНТЕРФЕЙСА.

    Это и есть правка: раньше вызов оставался в рабочем потоке и Qt ронял
    программу на первом же создании виджета.
    """
    from ui.agent_widget import run_in_gui_thread

    widget = build_widget(window)
    main_thread = threading.get_ident()
    seen = []
    answers = []

    def function(**arguments):
        seen.append((threading.get_ident(), arguments))
        return {"ok": True, "data": []}

    def from_worker():
        answers.append(run_in_gui_thread(widget, function, {"query": "тест"}))

    run_in_thread(from_worker)

    assert answers == [{"ok": True, "data": []}]
    assert seen and seen[0][0] == main_thread, "вызов ушёл не в поток интерфейса"
    assert seen[0][1] == {"query": "тест"}


def test_error_in_the_gui_thread_reaches_the_worker(window):
    """Исключение инструмента возвращается рабочему потоку, а не глотается."""
    from ui.agent_widget import run_in_gui_thread

    widget = build_widget(window)
    caught = []

    def function(**arguments):
        raise ValueError("формы нет")

    def from_worker():
        try:
            run_in_gui_thread(widget, function, {})
        except Exception as e:  # noqa: BLE001 — проверяем сам факт
            caught.append(e)

    run_in_thread(from_worker)

    assert caught and isinstance(caught[0], ValueError)
    assert str(caught[0]) == "формы нет"


def test_gui_call_requested_without_a_request_does_nothing(window):
    """Сигнал без запроса (окно закрывается) ничего не делает и не падает."""
    widget = build_widget(window)
    widget._gui_call = None
    widget.gui_call_requested.emit()
    QApplication.processEvents()
    assert widget._gui_call is None


def test_wrapped_tool_returns_the_result_through_the_registry(window):
    """
    Полный путь: вызов инструмента формы через реестр из рабочего потока.

    Реестр — тот же, что у агента; вызов идёт так же, как его делает
    ``ToolRegistry.call``. Имя инструмента взято из GUI_TOOLS: решение
    «выполнять в потоке интерфейса» принимается по имени.
    """
    widget = build_widget(window)
    widget._load_history(20, 3)
    registry = widget._agent.registry
    main_thread = threading.get_ident()
    seen = []
    answers = []

    def function(**arguments):
        seen.append(threading.get_ident())
        return {"ok": True, "data": {"thread": threading.get_ident()}}

    registry.get("get_form_state").function = function
    #: Перевод в поток интерфейса делается при сборке агента; подменённую
    #: функцию оборачиваем тем же правилом.
    assert widget._route_gui_tools(widget._agent) == 1

    def from_worker():
        answers.append(registry.call("get_form_state", {}))

    run_in_thread(from_worker)

    assert seen == [main_thread]
    assert answers and answers[0]["ok"] is True


def test_agent_context_tools_are_not_routed_twice(window):
    """Инструменты НЕ из списка формы остаются обычными функциями."""
    from ui.agent_widget import _GuiTool

    widget = build_widget(window)
    plain = Tool(name="probe_plain", description="",
                 parameters={}, function=lambda **_: {"ok": True})
    registry = ToolRegistry([plain])
    widget._agent.registry = registry
    routed = widget._route_gui_tools(widget._agent)
    assert routed == 0
    assert not isinstance(registry.get("probe_plain").function, _GuiTool)


def test_context_is_still_available_for_tools(window):
    """Контекст агента не подменяется: инструменты видят то же окно."""
    widget = build_widget(window)
    widget._load_history(20, 3)
    assert isinstance(widget._agent.context, AgentContext)
    assert widget._agent.context.window is window
