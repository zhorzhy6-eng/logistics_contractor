#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Кнопка «Обучение» в панели Максимилиана (задание: система обучения, часть 4).

Что проверяется
---------------
1. **Кнопка.** В шапке панели есть переключаемая кнопка «🎓» с подсказкой
   «Обучение».
2. **Переключение.** Клик по кнопке включает/выключает `agent.tutorial_mode`
   в настройках и сразу переключает движок подсказок.
3. **Синхронизация.** Изменение настройки извне обновляет состояние кнопки.
4. **Показ подсказки.** Включённый режим кладёт подсказку в панель.

Все проверки — в offscreen-режиме, с подставным сервисом настроек (рабочий
`config/settings.json` не трогается).
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
    """Минимальное окно (как в tests/test_agent_widget.py)."""

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


# ─────────────────────────────────────────────────────────────
# Кнопка «Обучение»
# ─────────────────────────────────────────────────────────────

def test_tutorial_button_present(app, stub_window, install_service):
    """В шапке панели есть переключаемая кнопка «🎓» с подсказкой «Обучение»."""
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    app.processEvents()

    button = widget.panel.btn_tutorial
    assert button is not None
    assert button.isCheckable() is True
    assert "Обучение" in button.toolTip()
    assert not button.isChecked()


def test_tutorial_toggle_updates_settings(app, stub_window, install_service):
    """Клик по кнопке включает режим в настройках и движок подсказок."""
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    app.processEvents()

    assert install_service.agent_setting("tutorial_mode") is False

    widget.panel.btn_tutorial.click()
    app.processEvents()
    assert install_service.agent_setting("tutorial_mode") is True
    assert widget._tutorial_engine.enabled is True

    widget.panel.btn_tutorial.click()
    app.processEvents()
    assert install_service.agent_setting("tutorial_mode") is False
    assert widget._tutorial_engine.enabled is False


def test_tutorial_button_syncs_from_settings(app, stub_window, install_service):
    """Включение режима в настройках обновляет состояние кнопки."""
    install_service.update({"agent": {"tutorial_mode": True}})
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    app.processEvents()

    assert widget.panel.btn_tutorial.isChecked() is True
    assert widget._tutorial_engine.enabled is True


def test_tutorial_hint_appears_in_panel(app, stub_window, install_service, monkeypatch):
    """Включённый режим «Обучение» кладёт подсказку в панель."""
    install_service.update({"agent": {"tutorial_mode": True}})
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    app.processEvents()

    from core.agent import tutorial_hints

    monkeypatch.setattr(
        tutorial_hints, "tutorial_step_hint",
        lambda context: "Сейчас заполните: «ИНН перевозчика».",
    )

    widget._offer_tutorial()
    app.processEvents()

    assert any("ИНН перевозчика" in text for text in panel_bubbles(widget.panel))


# ─────────────────────────────────────────────────────────────
# Предложение обучения при первом запуске
# ─────────────────────────────────────────────────────────────

def test_first_run_offers_tutorial(app, stub_window, install_service):
    """Первый запуск: в панели появляется предложение, ставится отметка."""
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    app.processEvents()

    assert install_service.agent_setting("tutorial_seen") is True
    texts = panel_bubbles(widget.panel)
    assert any("Обучение" in text for text in texts)


def test_first_run_offer_not_repeated(app, stub_window, install_service):
    """Если предложение уже было — при открытии панели ничего нового."""
    install_service.update({"agent": {"tutorial_seen": True}})
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    app.processEvents()

    texts = panel_bubbles(widget.panel)
    assert not any("подсказывал на каждом шаге" in text for text in texts)


def test_first_run_mode_already_on_marks_seen_without_offer(app, stub_window, install_service):
    """Режим уже включён: только отметка, без лишнего сообщения."""
    install_service.update({"agent": {"tutorial_mode": True}})
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    app.processEvents()

    assert install_service.agent_setting("tutorial_seen") is True
    texts = panel_bubbles(widget.panel)
    assert not any("подсказывал на каждом шаге" in text for text in texts)
