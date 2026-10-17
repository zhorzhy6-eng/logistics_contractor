#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Подсказки при запуске: панель не начинает разговор с замечания.

Что было
--------
Настройки «проактивность» + «открывать панель при старте» вместе давали
такое: через пять секунд после запуска программы в панели появлялось
юридическое предупреждение «Маршрут перевозки: Поле «contract_tab.route»
не заполнено…» — и обрывалось на полуслове. Оператор только открыл
программу, форма пустая по определению, и первое, что он видел, был упрёк.

Что проверяется
---------------
1. Первые `STARTUP_QUIET_SECONDS` секунд подсказки (проактивные и
   юридические) не предлагаются вовсе — и сообщений в панели нет.
2. После тишины они снова работают: подсказка полезна, глушить её навсегда
   нельзя.
3. Режим «Обучение» тишиной не глушится: его включил сам оператор.
4. Панель закрыта — подсказок нет ни при каких условиях (это было и
   раньше, страж на месте).

Qt — offscreen, настройки — подставной сервис (рабочий
`config/settings.json` не трогается).
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget,
)

from core.settings_service import SettingsService  # noqa: E402
from ui.agent_widget import install_agent, uninstall_agent  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app():
    """QApplication на весь модуль (Qt допускает только один)."""
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def flush_events():
    """Прокручивает события после каждого теста."""
    yield
    application = QApplication.instance()
    if application is not None:
        application.processEvents()


@pytest.fixture
def settings(work_file):
    """Сервис настроек с разделом agent по умолчанию."""
    service = SettingsService(str(work_file("agent_settings.json")))
    service.update({"agent": dict(SettingsService.DEFAULTS["agent"])})
    return service


@pytest.fixture
def install_service(settings, monkeypatch):
    """Подставляет сервис настроек в ui/agent_widget."""
    import ui.agent_widget as widget_module

    monkeypatch.setattr(widget_module, "get_settings_service", lambda: settings)
    return settings


@pytest.fixture
def stub_window(app):
    """Минимальное окно (как в остальных тестах панели)."""

    class StubWindow(QMainWindow):
        CONTRACT_TYPE = "perevozka"

        def __init__(self):
            super().__init__()
            central = QWidget()
            self.setCentralWidget(central)
            layout = QVBoxLayout(central)
            self.header = QFrame()
            self.header.setObjectName("appHeader")
            header_layout = QHBoxLayout(self.header)
            header_layout.addWidget(QLabel("Заголовок"))
            header_layout.addStretch()
            self.header_layout = header_layout
            layout.addWidget(self.header)
            self.resize(900, 600)

    window = StubWindow()
    window.show()
    app.processEvents()
    yield window
    uninstall_agent(window)
    window.close()


def panel_bubbles(panel):
    """Тексты сообщений на панели (без растяжки в конце списка)."""
    return [
        panel.history_layout.itemAt(index).widget().text()
        for index in range(panel.history_layout.count() - 1)
    ]


@pytest.fixture
def hint_calls(monkeypatch):
    """Считает, какие подсказки панель предложила (без настоящих проверок)."""
    calls = []

    def record(name):
        def _inner(*_args, **_kwargs):
            calls.append(name)
        return _inner

    return calls, record


def enable_hints(service, **extra):
    """Включает проактивные и юридические подсказки в настройках теста."""
    values = {"proactive": True, "legal_hints": True, "show_panel_on_start": True}
    values.update(extra)
    service.update({"agent": values})


# ─────────────────────────────────────────────────────────────
# 1. Тишина при запуске
# ─────────────────────────────────────────────────────────────

def test_startup_quiet_blocks_hints(app, stub_window, install_service,
                                    hint_calls, monkeypatch):
    """Сразу после запуска панель молчит: ни проактивности, ни юр. подсказки."""
    enable_hints(install_service)
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    app.processEvents()

    calls, record = hint_calls
    monkeypatch.setattr(widget, "_offer_proactive", record("proactive"))
    monkeypatch.setattr(widget, "_offer_legal_hint", record("legal"))
    monkeypatch.setattr(widget, "_offer_tutorial", record("tutorial"))

    widget._check_suggestions()

    assert "proactive" not in calls, f"при запуске подсказок быть не должно: {calls}"
    assert "legal" not in calls, f"при запуске подсказок быть не должно: {calls}"
    # В панели при старте допустимо только предложение режима «Обучение»
    # (оно показывается один раз), но никаких юридических предупреждений.
    texts = panel_bubbles(widget.panel)
    assert not any("Маршрут перевозки" in text for text in texts), texts
    assert not any("Проверьте." in text for text in texts), texts


def test_hints_return_after_quiet(app, stub_window, install_service,
                                  hint_calls, monkeypatch):
    """Тишина кончилась — подсказки снова предлагаются."""
    enable_hints(install_service)
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    app.processEvents()

    calls, record = hint_calls
    monkeypatch.setattr(widget, "_offer_proactive", record("proactive"))
    monkeypatch.setattr(widget, "_offer_legal_hint", record("legal"))
    monkeypatch.setattr(widget, "_offer_tutorial", record("tutorial"))

    widget._startup_quiet.start(seconds=0)
    widget._check_suggestions()

    assert calls == ["proactive", "legal", "tutorial"], (
        "после тишины подсказки должны работать: глушить их навсегда нельзя"
    )


def test_tutorial_mode_is_not_muted(app, stub_window, install_service,
                                    hint_calls, monkeypatch):
    """Режим «Обучение» тишиной не глушится: его включил сам оператор."""
    enable_hints(install_service, tutorial_mode=True)
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    app.processEvents()

    calls, record = hint_calls
    monkeypatch.setattr(widget, "_offer_proactive", record("proactive"))
    monkeypatch.setattr(widget, "_offer_legal_hint", record("legal"))
    monkeypatch.setattr(widget, "_offer_tutorial", record("tutorial"))

    widget._check_suggestions()

    assert calls == ["tutorial"]


def test_no_hints_while_panel_is_closed(app, stub_window, install_service,
                                       hint_calls, monkeypatch):
    """Панель закрыта — подсказок нет ни при каких настройках."""
    enable_hints(install_service, show_panel_on_start=False)
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget._startup_quiet.start(seconds=0)
    app.processEvents()
    assert widget.panel is not None and not widget.panel.isVisible()

    calls, record = hint_calls
    monkeypatch.setattr(widget, "_offer_proactive", record("proactive"))
    monkeypatch.setattr(widget, "_offer_legal_hint", record("legal"))
    monkeypatch.setattr(widget, "_offer_tutorial", record("tutorial"))

    widget._check_suggestions()

    assert calls == []
