#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Виджет Максимилиана: иконка, панель, плавающий кот (задание, часть H.3).

Что проверяется
---------------
1. **Иконка и панель.** Иконка стоит в правом нижнем углу окна (статус-бар),
   клик открывает и закрывает панель, клик по «✕» закрывает, сообщения
   видны в истории.
2. **Горячая клавиша.** Сочетание из настроек подключено и переключает
   панель; режимы «только иконка» / «только панель» работают.
3. **Плавающий кот.** Появляется по настройке, открывает панель,
   перетаскивается (и перетаскивание не считается кликом), не перекрывает
   иконку агента.
4. **Выключение.** ``agent.enabled = false`` — в окне нет ни иконки, ни
   панели, ни кота, ни клавиши (задание, часть E.4).
5. **Подтверждение.** Опасное действие не выполняется без «да»; диалог
   показывает, что именно собирается записать.

Все проверки идут в offscreen-режиме: окна поднимаются настоящие, но на
экран не выводятся.

Почему файл разделён с tests/test_agent_dialogs.py: Qt-объекты (окна,
панели, диалоги) живут до конца процесса, и один файл с двумя десятками
таких тестов роняет прогон при выходе (0xC0000409). Разделение по темам
держит число одновременно существующих виджетов небольшим.
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import QObject, QPoint, Qt, pyqtSignal  # noqa: E402
from PyQt5.QtGui import QKeySequence, QMouseEvent  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget,
)

from core.settings_service import SettingsService  # noqa: E402
from ui.agent_widget import (  # noqa: E402
    PANEL_ANIMATION_MS, PANEL_WIDTH, AgentIconButton, ConfirmationDialog,
    FloatingCat, install_agent, uninstall_agent,
)


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────

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


@pytest.fixture
def install_service(settings, monkeypatch):
    """
    Подставляет сервис настроек в ui/agent_widget.

    Панель берёт настройки через get_settings_service(); в тестах это
    должен быть файл в tests/_tmp, а не рабочий config/settings.json.
    """
    import ui.agent_widget as widget_module

    monkeypatch.setattr(widget_module, "get_settings_service", lambda: settings)
    return settings


@pytest.fixture
def stub_window(app):
    """Минимальное окно с шапкой и центральным виджетом (как у MainWindow)."""

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


# ─────────────────────────────────────────────────────────────
# Вспомогательное
# ─────────────────────────────────────────────────────────────

def settle(app, widget) -> None:
    """
    Доводит анимацию панели до конца.

    Анимация идёт 200 мс, и в offscreen-режиме события таймера не приходят
    сами: тесты ждут её явно, а не спят.
    """
    widget.finish_panel_animation()
    app.processEvents()


def panel_bubbles(panel):
    """Пузыри сообщений на панели (без растяжки в конце списка)."""
    return [
        panel.history_layout.itemAt(index).widget()
        for index in range(panel.history_layout.count() - 1)
    ]


# ─────────────────────────────────────────────────────────────
# 1. Иконка и панель
# ─────────────────────────────────────────────────────────────

def test_icon_is_placed_in_bottom_right(app, stub_window, install_service):
    """
    Иконка кота встаёт в ПРАВЫЙ НИЖНИЙ угол окна (статус-бар) и кликабельна.

    Статус-бар — нижняя полоса окна, поэтому иконка не перекрывает ни
    кнопки внизу формы, ни таблицы вкладок.
    """
    widget = install_agent(stub_window, client_factory=lambda: None)
    app.processEvents()
    assert widget is not None
    assert widget.icon_button is not None
    assert widget.icon_button.parentWidget() is stub_window.statusBar()
    assert stub_window.statusBar().isVisible() is True
    assert "Максимилиан" in widget.icon_button.toolTip()

    # Геометрия: иконка ниже середины окна и правее середины.
    icon = widget.icon_button
    center = stub_window.rect().center()
    icon_center = icon.mapTo(stub_window, icon.rect().center())
    assert icon_center.x() > center.x(), "иконка должна быть справа"
    assert icon_center.y() > center.y(), "иконка должна быть внизу"

    # Клик по иконке открывает панель, второй клик — закрывает.
    widget.icon_button.click()
    settle(app, widget)
    assert widget.is_panel_visible() is True

    widget.icon_button.click()
    settle(app, widget)
    assert widget.is_panel_visible() is False


def test_floating_cat_does_not_cover_icon(app, stub_window, install_service):
    """
    Плавающий кот не накладывается на иконку и не сдвигает её.

    Кот встаёт в правом нижнем углу, поднятый над статус-баром: там
    иконка. Перетаскивание кота иконку не трогает.
    """
    install_service.update({"agent": {"show_floating": True}})
    widget = install_agent(stub_window, client_factory=lambda: None)
    app.processEvents()

    icon = widget.icon_button
    assert icon is not None
    position = icon.mapTo(stub_window, icon.rect().topLeft())

    cat = widget.floating
    assert cat is not None
    cat_bottom = cat.y() + cat.height()
    icon_top = icon.mapToGlobal(icon.rect().topLeft()).y()
    assert cat_bottom <= icon_top, "кот перекрывает иконку агента"

    # Перетаскивание кота иконку не двигает.
    cat.move(200, 200)
    app.processEvents()
    assert icon.mapTo(stub_window, icon.rect().topLeft()) == position
    assert icon.parentWidget() is stub_window.statusBar()


def test_panel_has_all_parts(app, stub_window, install_service):
    """В панели есть аватар, имя, история, поле ввода и кнопка отправки."""
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    app.processEvents()
    panel = widget.panel

    assert panel.avatar.avatar_size() == 64
    assert panel.name_label.text() == "Максимилиан"
    assert panel.role_label.text()  # подпись образа
    assert panel.btn_send.text() == "Отправить"
    assert panel.input.placeholderText()
    assert panel.width() <= PANEL_WIDTH + 1


def test_panel_animation_duration(app, stub_window, install_service):
    """Выезд панели — 200 мс и по свойству ширины."""
    widget = install_agent(stub_window, client_factory=lambda: None)
    assert widget._animation.duration() == PANEL_ANIMATION_MS
    assert PANEL_ANIMATION_MS == 200
    assert bytes(widget._animation.propertyName()) == b"maximumWidth"


def test_close_button_hides_panel(app, stub_window, install_service):
    """Кнопка «✕» закрывает панель (через владельца, а не просто hide)."""
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    settle(app, widget)
    assert widget.is_panel_visible() is True

    widget.panel.btn_close.click()
    settle(app, widget)
    assert widget.is_panel_visible() is False


def test_messages_appear_in_history(app, stub_window, install_service):
    """Сообщения оператора и агента попадают в историю панели."""
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    panel = widget.panel

    panel.append_message("что такое ИНН?", author="user")
    panel.append_message("ИНН — номер налогоплательщика.", author="agent",
                         tool_names=["explain_field"])
    app.processEvents()

    labels = panel_bubbles(panel)
    assert len(labels) == 2
    assert labels[0].objectName() == "agentBubbleUser"
    assert labels[1].objectName() == "agentBubbleAgent"
    assert "explain_field" in labels[1].toolTip()


def test_panel_loads_history_from_memory(app, stub_window, install_service):
    """Сохранённая история показывается в панели при открытии."""
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget._agent.memory.add("user", "прошлый вопрос")
    widget._agent.memory.add("assistant", "прошлый ответ")

    widget.panel.set_history(widget._agent.memory.messages)
    app.processEvents()

    texts = [label.text() for label in panel_bubbles(widget.panel)]
    assert "прошлый вопрос" in texts
    assert "прошлый ответ" in texts


def test_clear_history_button(app, stub_window, install_service, monkeypatch):
    """«Очистить историю» убирает сообщения и память диалога."""
    widget = install_agent(stub_window, client_factory=lambda: None)
    # Базы в этом тесте нет: подменяем только запись в неё, чтобы очистка
    # памяти и панели проверялась без поднятия таблицы agent_history.
    monkeypatch.setattr(widget._agent.memory.repository, "clear_session",
                        lambda session_id: 0)

    widget._agent.memory.add("user", "вопрос")
    widget.show_panel()
    widget.panel.append_message("вопрос", author="user")
    app.processEvents()

    widget.on_clear_history()

    assert widget._agent.memory.messages == []
    assert panel_bubbles(widget.panel) == []


def test_empty_message_is_not_sent(app, stub_window, install_service):
    """Пустое поле ввода не отправляет сообщение."""
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()

    sent = []
    widget.panel.submitted.connect(sent.append)
    widget.panel.input.setPlainText("   ")
    widget.panel.emit_submitted()

    assert sent == []


# ─────────────────────────────────────────────────────────────
# 2. Горячая клавиша и режимы
# ─────────────────────────────────────────────────────────────

def test_hotkey_is_installed(app, stub_window, install_service):
    """Горячая клавиша из настроек подключена и переключает панель."""
    widget = install_agent(stub_window, client_factory=lambda: None)
    assert widget.shortcut is not None
    assert QKeySequence(widget.shortcut) == QKeySequence("Ctrl+M")
    # Клавиша ловится фильтром событий окна, а не объектом QShortcut.
    assert widget._hotkey is not None

    QTest.keySequence(stub_window, QKeySequence("Ctrl+M"))
    settle(app, widget)
    assert widget.is_panel_visible() is True

    QTest.keySequence(stub_window, QKeySequence("Ctrl+M"))
    settle(app, widget)
    assert widget.is_panel_visible() is False


def test_hotkey_changes_with_settings(app, stub_window, install_service):
    """Смена сочетания в настройках переподключает клавишу."""
    widget = install_agent(stub_window, client_factory=lambda: None)
    install_service.update({"agent": {"hotkey": "Ctrl+Shift+K"}})
    widget.reload_settings()

    assert widget.shortcut is not None
    assert QKeySequence(widget.shortcut) == QKeySequence("Ctrl+Shift+K")
    assert widget.hotkey_sequence() == QKeySequence("Ctrl+Shift+K")


def test_mode_icon_hides_panel_possibility(app, stub_window, install_service):
    """Режим «только иконка» не создаёт панель и не ставит клавишу."""
    install_service.update({"agent": {"mode": "icon"}})
    widget = install_agent(stub_window, client_factory=lambda: None)

    assert widget.icon_button is not None
    assert widget.panel is None
    assert widget.shortcut is None


def test_mode_panel_hides_icon(app, stub_window, install_service):
    """Режим «только панель» не показывает иконку."""
    install_service.update({"agent": {"mode": "panel"}})
    widget = install_agent(stub_window, client_factory=lambda: None)

    assert widget.icon_button is None
    assert widget.panel is not None
    assert widget.shortcut is not None


# ─────────────────────────────────────────────────────────────
# 3. Плавающий кот
# ─────────────────────────────────────────────────────────────

def test_floating_cat_is_optional(app, stub_window, install_service):
    """Плавающий кот появляется только по настройке и открывает панель."""
    widget = install_agent(stub_window, client_factory=lambda: None)
    assert widget.floating is None, "по умолчанию кота нет"

    install_service.update({"agent": {"show_floating": True}})
    widget.reload_settings()

    assert isinstance(widget.floating, FloatingCat)
    assert widget.floating.isVisible() is True

    widget.floating.clicked.emit()
    settle(app, widget)
    assert widget.is_panel_visible() is True

    install_service.update({"agent": {"show_floating": False}})
    widget.reload_settings()
    assert widget.floating.isVisible() is False


def test_floating_cat_can_be_dragged(app, stub_window, install_service):
    """Кота можно перетащить (и это не считается кликом)."""

    class ClickCounter(QObject):
        """Счётчик кликов: lambda в connect запрещена (AGENTS.md § 5.1)."""

        clicked = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.count = 0

        def on_clicked(self):
            self.count += 1

    cat = FloatingCat("cat_scientist")
    counter = ClickCounter()
    cat.clicked.connect(counter.on_clicked)
    cat.move(100, 100)
    start = cat.pos()

    move = QMouseEvent(QMouseEvent.MouseMove, QPoint(40, 40),
                       Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    release = QMouseEvent(QMouseEvent.MouseButtonRelease, QPoint(40, 40),
                          Qt.LeftButton, Qt.NoButton, Qt.NoModifier)

    cat._drag_offset = QPoint(0, 0)
    cat.mouseMoveEvent(move)
    cat._moved = True
    cat.mouseReleaseEvent(release)
    cat.hide()

    assert cat.pos() != start
    assert counter.count == 0, "перетаскивание не должно открывать панель"


# ─────────────────────────────────────────────────────────────
# 4. Выключение агента (задание, часть E.4)
# ─────────────────────────────────────────────────────────────

def test_disabled_agent_shows_nothing(app, stub_window, install_service):
    """agent.enabled=false — в окне нет ни иконки, ни панели, ни клавиши."""
    install_service.update({"agent": {"enabled": False}})

    widget = install_agent(stub_window, client_factory=lambda: None)

    assert widget is None
    assert getattr(stub_window, "agent_widget", None) is None
    # В шапке окна не появилось кнопки агента.
    assert stub_window.findChildren(AgentIconButton) == []


def test_agent_disabled_after_start_hides_everything(app, stub_window, install_service):
    """Выключение в настройках на лету убирает иконку, панель и кота."""
    install_service.update({"agent": {"show_floating": True}})
    widget = install_agent(stub_window, client_factory=lambda: None)
    widget.show_panel()
    settle(app, widget)
    assert widget.is_panel_visible() is True

    install_service.update({"agent": {"enabled": False}})
    widget.reload_settings()
    app.processEvents()

    assert widget.is_panel_visible() is False
    assert widget.icon_button.isVisible() is False
    assert widget.floating.isVisible() is False
    assert widget.shortcut is None


def test_enabled_agent_can_be_turned_off_and_on(app, stub_window, install_service):
    """Обратное включение возвращает иконку и панель."""
    widget = install_agent(stub_window, client_factory=lambda: None)

    install_service.update({"agent": {"enabled": False}})
    widget.reload_settings()
    assert widget.icon_button.isVisible() is False

    install_service.update({"agent": {"enabled": True}})
    widget.reload_settings()
    assert widget.icon_button.isVisible() is True
    assert widget.panel is not None


# ─────────────────────────────────────────────────────────────
# 5. Диалог подтверждения (задание, часть D.5)
# ─────────────────────────────────────────────────────────────

def test_confirmation_dialog_asks_about_field(app, install_service):
    """В диалоге подтверждения видно, какое поле и чем заполняется."""
    from core.agent.tools import build_registry

    tool = build_registry().get("fill_field")
    dialog = ConfirmationDialog(tool, {"field": "carrier_tab.full_name",
                                       "value": "ООО «Ромашка»"})
    text = dialog.confirmation_text()
    assert "Заполнить поле формы" in text
    assert "carrier_tab.full_name" in text
    # Значение поля в вопросе не пересказывается: его видно в деталях.
    assert "Ромашка" not in text
    assert dialog.yes_button.text() == "Да"
    assert dialog.no_button.text() == "Нет"
    assert dialog.details_button.text() == "Показать детали"
    # По умолчанию выбран безопасный ответ.
    assert dialog.no_button.isDefault() is True
    dialog.close()


def test_confirmation_dialog_auto_answer(app, stub_window, install_service):
    """
    Автоответ закрывает диалог выбранной кнопкой (нужно пробам).

    Родитель — настоящий виджет: диалог без родителя в offscreen-режиме
    роняет процесс (проверено пробой tests/_tmp/_agent_states.py).
    """
    from core.agent.tools import build_registry

    tool = build_registry().get("fill_field")

    dialog = ConfirmationDialog(tool, {"field": "inn", "value": "7701234567"},
                                stub_window)
    dialog.auto_answer(True, 0)
    dialog.exec_()
    assert dialog.is_confirmed() is True
    dialog.close()

    dialog = ConfirmationDialog(tool, {"field": "inn", "value": "7701234567"},
                                stub_window)
    dialog.auto_answer(False, 0)
    dialog.exec_()
    assert dialog.is_confirmed() is False
    dialog.close()
