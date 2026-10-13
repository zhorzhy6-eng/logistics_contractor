#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Аватар и настройки Максимилиана (задание, часть H.3).

Что проверяется
---------------
1. **Аватар.** Четыре образа (учёный кот, кот-логист, кот-хранитель, без
   образа) рисуются по-разному; кот мигает, а на время запроса держит
   глаза широко открытыми.
2. **Диалог настроек.** Открывается на текущих значениях, отдаёт выбранное
   и сохраняет его в файл настроек; кнопка «⚙» панели связана с диалогом.

Настройки читаются и пишутся в файл tests/_tmp: рабочий
config/settings.json оператора тесты не трогают.

Файл отделён от tests/test_agent_widget.py намеренно: Qt-объекты живут до
конца процесса, и десятки окон с диалогами в одном файле роняют прогон
при выходе (0xC0000409).
"""

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtWidgets import QApplication  # noqa: E402

from core.settings_service import SettingsService  # noqa: E402
from ui.agent_avatar import (  # noqa: E402
    CatAvatar, avatar_icon, avatar_keys, avatar_pixmap, paint_cat,
)
from ui.agent_settings_dialog import AgentSettingsDialog  # noqa: E402
from ui.agent_widget import install_agent, uninstall_agent  # noqa: E402


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
def settings_path(work_file):
    """Путь к отдельному файлу настроек (рабочий config не трогаем)."""
    return work_file("agent_settings.json")


@pytest.fixture
def settings(settings_path):
    """Сервис настроек с разделом agent по умолчанию."""
    service = SettingsService(str(settings_path))
    service.update({"agent": dict(SettingsService.DEFAULTS["agent"])})
    return service


# ─────────────────────────────────────────────────────────────
# 1. Аватар (задание, часть D.2)
# ─────────────────────────────────────────────────────────────

def test_four_avatars_are_drawn(app, work_file):
    """У четырёх образов есть свой рисунок (и они отличаются)."""
    assert len(avatar_keys()) == 4
    digests = set()
    for key in avatar_keys():
        pixmap = avatar_pixmap(64, key)
        assert pixmap.width() == 64
        assert not pixmap.isNull()
        path = work_file(f"avatar_{key}.png")
        assert pixmap.save(str(path)) is True
        assert path.stat().st_size > 500, f"{key}: картинка подозрительно пуста"
        digests.add(path.read_bytes())
    assert len(digests) == 4, "образы должны отличаться друг от друга"


def test_avatar_sizes(app):
    """Аватар рисуется в трёх нужных размерах: 32, 64, 96 (задание, часть D.2)."""
    for size in (32, 64, 96):
        pixmap = avatar_pixmap(size, "cat_scientist")
        assert pixmap.width() == size
        assert pixmap.height() == size
        assert not pixmap.isNull()
        icon = avatar_icon(size, "cat_scientist")
        assert not icon.isNull()


def test_avatar_blinks_and_thinks(app):
    """Аватар мигает, а на время запроса держит глаза широко открытыми."""
    avatar = CatAvatar(64, "cat_scientist")
    assert avatar.eyes_state() == "open"

    avatar.blink_now()
    assert avatar.eyes_state() == "closed"

    avatar.set_thinking(True)
    assert avatar.eyes_state() == "wide"
    assert avatar.is_thinking() is True

    avatar.set_thinking(False)
    assert avatar.eyes_state() == "open"
    avatar.hide()


def test_keeper_avatar_sleeps(app):
    """Кот-хранитель спит: у него глаза закрыты всегда."""
    avatar = CatAvatar(64, "cat_keeper")
    assert avatar.eyes_state() == "closed"
    avatar.blink_now()
    assert avatar.eyes_state() == "closed"
    avatar.hide()


def test_avatar_persona_can_be_changed(app):
    """Образ аватара меняется на лету (настройки → панель)."""
    avatar = CatAvatar(32, "neutral")
    assert avatar.persona() == "neutral"
    avatar.set_persona("cat_logist")
    assert avatar.persona() == "cat_logist"
    # Неизвестный ключ — образ по умолчанию, без исключения.
    avatar.set_persona("кот-космонавт")
    assert avatar.persona() == "cat_scientist"
    avatar.hide()


def test_paint_cat_scales_without_errors(app):
    """Рисование масштабируется: 96 px и «нестандартный» размер 17 px."""
    from PyQt5.QtCore import QRectF
    from PyQt5.QtGui import QPainter, QPixmap

    for size in (17, 96, 128):
        pixmap = QPixmap(size, size)
        pixmap.fill()
        painter = QPainter(pixmap)
        try:
            paint_cat(painter, size, "cat_logist", "wide", QRectF(0, 0, size, size))
        finally:
            painter.end()
        assert not pixmap.isNull()


# ─────────────────────────────────────────────────────────────
# 2. Диалог настроек (задание, часть D.3)
# ─────────────────────────────────────────────────────────────

def test_settings_dialog_returns_values(app, settings):
    """Диалог отдаёт выбранные значения в раздел agent."""
    dialog = AgentSettingsDialog(settings.agent_settings())
    dialog.enabled.setChecked(True)
    dialog.avatar_combo.setCurrentIndex(
        dialog.avatar_combo.findData("cat_logist")
    )
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("panel"))
    dialog.show_floating.setChecked(True)
    dialog.show_on_start.setChecked(True)
    dialog.max_history.setValue(35)
    dialog.max_steps.setValue(4)

    values = dialog.get_settings()

    assert values["avatar"] == "cat_logist"
    assert values["mode"] == "panel"
    assert values["show_floating"] is True
    assert values["show_panel_on_start"] is True
    assert values["max_history"] == 35
    assert values["max_tool_steps"] == 4
    assert values["name"] == "Максимилиан"
    dialog.close()


def test_settings_dialog_shows_current_values(app, settings):
    """Диалог открывается на текущих настройках, а не на дефолтах."""
    settings.update({"agent": {"avatar": "cat_keeper", "mode": "icon",
                               "max_tool_steps": 2, "show_floating": True}})
    dialog = AgentSettingsDialog(settings.agent_settings())

    assert dialog.avatar_combo.currentData() == "cat_keeper"
    assert dialog.mode_combo.currentData() == "icon"
    assert dialog.max_steps.value() == 2
    assert dialog.show_floating.isChecked() is True
    dialog.close()


def test_settings_saved_to_file(settings_path, settings):
    """Значения из диалога попадают в файл настроек."""
    dialog = AgentSettingsDialog(settings.agent_settings())
    dialog.avatar_combo.setCurrentIndex(dialog.avatar_combo.findData("neutral"))
    dialog.enabled.setChecked(False)

    assert settings.update_agent(dialog.get_settings()) is True
    dialog.close()

    with open(settings_path, "r", encoding="utf-8") as file:
        raw = json.load(file)
    assert raw["agent"]["avatar"] == "neutral"
    assert raw["agent"]["enabled"] is False
    # Остальные ключи раздела на месте — раздел не «потерялся».
    assert raw["agent"]["max_history"] == 20
    assert raw["agent"]["hotkey"] == "Ctrl+M"


def test_settings_dialog_has_clear_history_buttons(app, settings):
    """В диалоге есть очистка истории и удаление прошлых сессий."""
    dialog = AgentSettingsDialog(settings.agent_settings())
    assert dialog.btn_clear_history.text() == "Очистить историю диалога"
    assert dialog.btn_purge_sessions.text() == "Удалить прошлые сессии"
    dialog.close()


def test_panel_is_wired_to_agent_settings(app, settings, monkeypatch):
    """
    Кнопка «⚙» в панели связана с диалогом настроек агента.

    Проверяется связка «сигнал панели → метод AgentWidget → диалог».
    Подставной диалог: настоящий модальный диалог ждал бы оператора.
    """
    # Подставное окно с шапкой, к которому можно поставить агента.
    from PyQt5.QtWidgets import (
        QFrame, QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget,
    )

    class StubWindow(QMainWindow):
        CONTRACT_TYPE = "perevozka"

        def __init__(self):
            super().__init__()
            central = QWidget()
            self.setCentralWidget(central)
            layout = QVBoxLayout(central)
            header = QFrame()
            header.setObjectName("appHeader")
            QHBoxLayout(header).addWidget(QLabel("Заголовок"))
            layout.addWidget(header)

    import ui.agent_widget as widget_module

    monkeypatch.setattr(widget_module, "get_settings_service", lambda: settings)

    window = StubWindow()
    opened = []

    class FakeDialog:
        Accepted = 1

        def __init__(self, values, parent=None):
            opened.append(values)

        def exec_(self):
            return 0  # отмена: настройки не меняются

        def get_settings(self):
            return {}

    import ui.agent_settings_dialog as dialog_module

    monkeypatch.setattr(dialog_module, "AgentSettingsDialog", FakeDialog)

    widget = install_agent(window, client_factory=lambda: None)
    widget.show_panel()

    widget.panel.settings_requested.emit()
    app.processEvents()

    assert opened, "диалог настроек должен был открыться"
    assert "mode" in opened[0], "диалогу передаются текущие настройки агента"
    # И сама кнопка подключена к этому сигналу.
    assert widget.panel.btn_settings.receivers(
        widget.panel.btn_settings.clicked
    ) > 0

    uninstall_agent(window)
    window.close()
