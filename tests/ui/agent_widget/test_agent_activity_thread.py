#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Шаг агента показывается в панели ИЗ ПОТОКА ИНТЕРФЕЙСА.

НАЙДЕНО СТРЕСС-ТЕСТОМ (200 запросов, 15.10.2026): прогон дважды падал
насмерть — процесс завершался с кодом 1 без traceback, в консоли оставалось

    QObject::setParent: Cannot set parent, new parent is in a different thread
    QObject::killTimer: Timers cannot be stopped from another thread

Причина — `AgentWidget._panel_call`: вызов панели уходил «в главный поток»
через ``QTimer.singleShot(0, lambda: func(*args))``. Но `QTimer.singleShot`,
вызванный ИЗ РАБОЧЕГО потока, создаёт таймер в этом же рабочем потоке — и
лямбда выполняется в нём, то есть панель (виджет!) менялась из чужого
потока. Падало не каждый раз, а когда таймер успевал сработать до конца
запроса: два падения на ~90 запросов.

Правильный путь — сигнал: Qt доставляет его в очередь главного потока и
вызывает слот там. Здесь проверяется именно это: и то, что шаг доходит до
панели, и то, что доходит он из главного потока.
"""

import os
import sys
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

from core.settings_service import SettingsService  # noqa: E402
from ui.agent_widget import install_agent, uninstall_agent  # noqa: E402


class ScriptedClient:
    """Подставная модель: один вызов инструмента, затем текст."""

    def __init__(self, tool="search_anything"):
        self.tool = tool
        self.calls = 0

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": self.tool,
                        "arguments": '{"query": "Ромашка"}',
                    },
                }],
            }
        return {"role": "assistant", "content": "готово"}


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

    service = SettingsService(str(work_file("activity_settings.json")))
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
    uninstall_agent(window)
    window.close()


def build_widget(window):
    """Ставит Максимилиана с подставной моделью и возвращает виджет."""
    widget = install_agent(window, client_factory=ScriptedClient,
                           open_on_start=True)
    assert widget is not None
    widget.show_panel()
    window.agent_widget.finish_panel_animation()
    QApplication.processEvents()
    return widget


# ─────────────────────────────────────────────────────────────
# Сигнал вместо таймера в рабочем потоке
# ─────────────────────────────────────────────────────────────

def test_widget_has_activity_signal():
    """У виджета есть сигнал шага — им и передаётся активность."""
    from ui.agent_widget import AgentWidget

    assert hasattr(AgentWidget, "activity_reported")


def test_panel_call_does_not_touch_widgets_from_another_thread(window):
    """
    Устаревший `_panel_call` из чужого потока НЕ вызывает функцию.

    Раньше он откладывал вызов через `QTimer.singleShot(0, lambda: …)` —
    и это исполнялось в рабочем потоке. Теперь такой вызов пропускается с
    предупреждением: панель меняется только сигналом.
    """
    widget = build_widget(window)
    called = []

    run_in_thread(lambda: widget._panel_call(called.append, "из другого потока"))

    assert called == [], "панель тронута из чужого потока"


def run_in_thread(func, ms: int = 300):
    """
    Выполняет функцию в отдельном потоке С ЦИКЛОМ СОБЫТИЙ.

    Цикл здесь принципиален: старая ошибка проявлялась именно так — таймер,
    созданный в рабочем потоке, срабатывал в его же цикле событий. Поток без
    ``exec_()`` такую ошибку просто не воспроизвёл бы, и проверка ничего не
    ловила бы.
    """
    class Worker(QThread):
        def run(self):
            func()
            QTimer.singleShot(ms, self.quit)
            self.exec_()

    thread = Worker()
    thread.start()
    for _ in range(300):
        QApplication.processEvents()
        if thread.isFinished():
            break
        thread.wait(10)
    thread.wait(3000)
    return thread


def test_activity_from_the_worker_thread_reaches_the_panel(window):
    """
    Шаг, отправленный из рабочего потока, доходит до панели.

    И доходит он в ГЛАВНОМ потоке: слот сигнала вызывается очередью
    событий главного потока, а не в том потоке, который его испустил.
    """
    widget = build_widget(window)
    seen = []
    panel = widget.panel
    original = panel.set_status

    def spy(text, busy=False):
        seen.append((QThread.currentThread(), text, busy))
        return original(text, busy)

    widget.activity_reported.connect(spy)
    try:
        run_in_thread(lambda: widget.activity_reported.emit("Выполняю: поиск"))
        for _ in range(100):
            QApplication.processEvents()
            if seen:
                break
    finally:
        widget.activity_reported.disconnect(spy)

    assert seen, "шаг агента не дошёл до панели"
    main_thread = QThread.currentThread()
    assert all(thread is main_thread for thread, _text, _busy in seen), (
        "панель обновлена не из главного потока"
    )


def test_agent_activity_reaches_the_panel_through_the_widget(window):
    """
    Живой путь: рабочий поток агента сообщает шаг — панель его показывает.

    Проверяется и то, что доходит, и то, что доходит из ГЛАВНОГО потока.
    """
    widget = build_widget(window)
    seen = []
    panel = widget.panel
    original = panel.set_status

    def spy(text, busy=False):
        seen.append((QThread.currentThread(), text))
        return original(text, busy)

    widget.panel.set_status = spy
    try:
        run_in_thread(lambda: widget._on_activity("Выполняю: поиск водителя"))
        for _ in range(100):
            QApplication.processEvents()
            if seen:
                break
    finally:
        panel.set_status = original

    assert any("Выполняю" in text for _thread, text in seen), (
        "шаг агента не показан в панели"
    )
    main_thread = QThread.currentThread()
    assert all(thread is main_thread for thread, _text in seen), (
        "панель обновлялась из рабочего потока"
    )
