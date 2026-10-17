#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Плавающий кот закрывается вместе с программой (баг #18, тесты).

Что было не так
---------------
Кот — окно верхнего уровня (`Qt.Tool` + `WindowStaysOnTopHint`) и создавался
БЕЗ родителя: `FloatingCat(persona_key)` попадал строкой в параметр `parent`.
Главное окно закрывалось, программа выключалась, а кот оставался на экране.
Вторая часть бага: родитель у кота появился, но на настоящей Windows
`Qt.Tool`-окно НЕ прячется вместе с родителем, и кот продолжал висеть поверх
всех программ после того, как окно скрыли или свернули.

Что проверяется
---------------
1. **Родитель.** У кота есть родитель — окно программы; идиома
   `FloatingCat(ключ, window)` (второй позиционный аргумент) тоже ставит
   родителя, а не образ.
2. **Закрытие.** `close_cat()` останавливает анимацию, прячет и закрывает
   окно, идемпотентен.
3. **Выход из программы.** `force_close()` окна закрывает кота; закрытие
   крестиком (hide) тоже прячет его.
4. **Выключенная настройка.** При `show_floating = false` кот НЕ создаётся
   вовсе (а не создаётся и прячется).
5. **Страховка.** `close_floating_cats()` закрывает всех оставшихся котов:
   это вызов из `main._do_exit`.
6. **Таймеры.** После закрытия анимация аватара остановлена.
7. **Кот не висит на экране.** Он прячется, когда окно программы скрыто
   (крестик), свёрнуто, когда оператор ушёл в другое приложение, когда
   открыт модальный диалог и когда открыта панель Максимилиана. Причина:
   `Qt.Tool`-окно верхнего уровня НЕ скрывается вместе с родителем на
   настоящей Windows (на offscreen скрывается — из-за этого баг #18 не
   ловился тестами; живая проба `tests/_tmp/cat_visibility_probe.py`).

Qt — в offscreen-режиме; база — изолированная.
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

pytest.importorskip("PyQt5")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMainWindow  # noqa: E402

from ui.agent_widget import (  # noqa: E402
    AgentWidget, FloatingCat, close_floating_cats,
)


@pytest.fixture(scope="module")
def qapp():
    """Одно приложение Qt на модуль (offscreen)."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp):
    """Обычное окно — родитель кота."""
    widget = QMainWindow()
    widget.resize(900, 600)
    widget.show()
    yield widget
    widget.deleteLater()


@pytest.fixture
def settings_service():
    """Сервис настроек приложения (файл подменяют фикстуры conftest)."""
    from core.settings_service import get_settings_service

    return get_settings_service()


@pytest.fixture
def agent(window, settings_service, monkeypatch):
    """Максимилиан в окне, без клиента GigaChat (он тут не нужен)."""
    settings_service.update_agent({"enabled": True, "show_floating": False})
    widget = AgentWidget(window, client_factory=lambda: None)
    monkeypatch.setattr(window, "agent_widget", widget, raising=False)
    yield widget
    widget.shutdown()
    widget.deleteLater()


# ─────────────────────────────────────────────────────────────
# 1. Родитель и флаги окна
# ─────────────────────────────────────────────────────────────

def test_cat_gets_parent_window(qapp, window):
    """Кот, созданный с родителем, остаётся окном верхнего уровня."""
    cat = FloatingCat(persona_key="cat_scientist", parent=window)
    try:
        assert cat.parent() is window
        assert cat.isWindow() is True
        assert cat.windowFlags() & Qt.Tool
        assert cat.windowFlags() & Qt.FramelessWindowHint
    finally:
        cat.close_cat()
        cat.deleteLater()


def test_cat_second_positional_argument_is_parent(qapp, window):
    """Идиома `FloatingCat(ключ, окно)` ставит родителя, а не образ."""
    cat = FloatingCat("cat_scientist", window)
    try:
        assert cat.parent() is window
    finally:
        cat.close_cat()
        cat.deleteLater()


def test_agent_creates_cat_with_parent(qapp, window, agent, settings_service):
    """Кот, созданный Максимилианом, привязан к окну программы."""
    settings_service.update_agent({"show_floating": True})
    agent.apply_settings()
    assert agent.floating is not None
    assert agent.floating.parent() is window


# ─────────────────────────────────────────────────────────────
# 2. close_cat()
# ─────────────────────────────────────────────────────────────

def test_close_cat_hides_and_closes(qapp, window):
    """close_cat() прячет кота и помечает его закрытым."""
    cat = FloatingCat(persona_key="cat_scientist", parent=window)
    cat.show()
    assert cat.isVisible() is True

    cat.close_cat()
    assert cat.isVisible() is False
    assert cat.is_closed is True


def test_close_cat_is_idempotent(qapp, window):
    """Повторное закрытие ничего не делает и не падает."""
    cat = FloatingCat(persona_key="cat_scientist", parent=window)
    cat.close_cat()
    cat.close_cat()
    assert cat.is_closed is True


def test_close_cat_stops_avatar_animation(qapp, window):
    """После закрытия анимация аватара остановлена (таймеров не осталось)."""
    cat = FloatingCat(persona_key="cat_scientist", parent=window)
    cat.show()
    cat.close_cat()

    timer = getattr(cat.avatar, "_timer", None)
    if timer is not None:
        assert timer.isActive() is False


# ─────────────────────────────────────────────────────────────
# 3. Выход из программы
# ─────────────────────────────────────────────────────────────

def test_shutdown_closes_cat(qapp, window, agent, settings_service):
    """shutdown() Максимилиана закрывает кота (его зовёт force_close окна)."""
    settings_service.update_agent({"show_floating": True})
    agent.apply_settings()
    cat = agent.floating
    assert cat is not None and cat.isVisible() is True

    agent.shutdown()
    assert cat.is_closed is True
    assert cat.isVisible() is False


def test_close_floating_closes_cat(qapp, window, agent, settings_service):
    """close_floating() закрывает кота и забывает ссылку на него."""
    settings_service.update_agent({"show_floating": True})
    agent.apply_settings()
    cat = agent.floating

    agent.close_floating()
    assert cat.is_closed is True
    assert agent.floating is None
    # Повторный вызов безопасен: кота уже нет.
    agent.close_floating()


def test_close_floating_without_cat(qapp, window, agent):
    """Кота нет — close_floating() просто ничего не делает."""
    agent.floating = None
    agent.close_floating()
    assert agent.floating is None


# ─────────────────────────────────────────────────────────────
# 4. Настройка show_floating
# ─────────────────────────────────────────────────────────────

def test_floating_disabled_does_not_create_cat(qapp, window, agent,
                                               settings_service):
    """При show_floating = false кот НЕ создаётся вовсе."""
    settings_service.update_agent({"show_floating": False})
    agent.apply_settings()
    assert agent.floating is None


def test_floating_enabled_creates_and_shows_cat(qapp, window, agent,
                                                settings_service):
    """При show_floating = true кот создаётся и виден."""
    settings_service.update_agent({"show_floating": True})
    agent.apply_settings()
    assert agent.floating is not None
    assert agent.floating.isVisible() is True


def test_floating_turned_off_hides_existing_cat(qapp, window, agent,
                                                settings_service):
    """Выключение настройки прячет уже созданного кота."""
    settings_service.update_agent({"show_floating": True})
    agent.apply_settings()
    cat = agent.floating

    settings_service.update_agent({"show_floating": False})
    agent.apply_settings()
    assert cat.isVisible() is False


# ─────────────────────────────────────────────────────────────
# 5. Страховка выхода: close_floating_cats()
# ─────────────────────────────────────────────────────────────

def test_close_floating_cats_finds_all(qapp, window):
    """Страховка закрывает всех котов, оставшихся в приложении."""
    cats = [FloatingCat(persona_key="cat_scientist", parent=window)
            for _ in range(3)]
    for cat in cats:
        cat.show()

    closed = close_floating_cats(qapp)
    assert closed >= 3
    assert all(cat.is_closed for cat in cats)
    for cat in cats:
        cat.deleteLater()


def test_close_floating_cats_without_qt(monkeypatch, qapp):
    """Qt не поднят — страховка отвечает нулём, а не падает."""
    from ui import agent_widget as module

    monkeypatch.setattr(module.QApplication, "instance",
                        staticmethod(lambda: None))
    assert close_floating_cats(None) == 0


# ─────────────────────────────────────────────────────────────
# 6. Кот не висит на экране: окно скрыли, свернули, ушли в другое приложение
# ─────────────────────────────────────────────────────────────

def test_cat_hides_when_owner_window_is_hidden(qapp, window, agent,
                                              settings_service):
    """
    Окно скрыли (крестик) — кот спрятался САМ, а не «силами платформы».

    На платформе `offscreen` Qt скрывает дочернее окно-кот вместе с
    родителем, поэтому обычный `window.hide()` скрыл бы кота и без
    исправления. На настоящей Windows этого НЕ происходит (живая проба
    `tests/_tmp/cat_visibility_probe.py`: после `window.hide()` кот
    оставался видимым — это и был баг #18). Здесь кот показывается заново
    уже после скрытия окна (так ведёт себя платформа), и проверяется, что
    событие Hide окна его прячет.
    """
    from PyQt5.QtGui import QHideEvent

    settings_service.update_agent({"show_floating": True})
    agent.apply_settings()
    cat = agent.floating
    assert cat is not None

    window.hide()
    cat.show()                      # как на Windows: платформа не прячет
    assert cat.isVisible() is True

    QApplication.sendEvent(window, QHideEvent())
    assert cat.isVisible() is False, "кот остался на экране после скрытия окна"
    assert cat.is_closed is False, "скрытие окна не закрывает кота навсегда"


def test_cat_hides_when_window_minimized(qapp, window, agent, settings_service):
    """Окно свернули — кот не должен висеть поверх чужих окон."""
    settings_service.update_agent({"show_floating": True})
    agent.apply_settings()
    cat = agent.floating
    assert cat is not None

    window.showMinimized()
    qapp.processEvents()
    assert cat._is_wanted_visible() is False
    assert cat.isVisible() is False


def test_cat_hides_when_application_is_inactive(qapp, window, agent,
                                                settings_service, monkeypatch):
    """Оператор ушёл в другую программу — кот прячется, а не «мешает»."""
    from PyQt5.QtCore import Qt

    settings_service.update_agent({"show_floating": True})
    agent.apply_settings()
    cat = agent.floating
    assert cat is not None and cat.isVisible() is True

    monkeypatch.setattr(QApplication, "applicationState",
                        staticmethod(lambda: Qt.ApplicationInactive))
    cat.on_application_state_changed(Qt.ApplicationInactive)
    assert cat.isVisible() is False

    monkeypatch.setattr(QApplication, "applicationState",
                        staticmethod(lambda: Qt.ApplicationActive))
    cat.on_application_state_changed(Qt.ApplicationActive)
    assert cat.isVisible() is True


def test_cat_hides_while_panel_is_open(qapp, window, agent, settings_service):
    """Пока панель открыта, кот уступает место её полю ввода и кнопке."""
    settings_service.update_agent({"show_floating": True})
    agent.apply_settings()
    cat = agent.floating
    assert cat is not None and cat.isVisible() is True

    agent.show_panel()
    agent.finish_panel_animation()
    qapp.processEvents()
    assert agent.is_panel_visible() is True
    assert cat.isVisible() is False, "кот закрывает поле ввода панели"

    agent.hide_panel()
    qapp.processEvents()
    assert cat.isVisible() is True


def test_cat_hides_while_modal_dialog_is_open(qapp, window, agent,
                                              settings_service):
    """Модальный диалог перекрывает кота: на время диалога прячемся."""
    from PyQt5.QtCore import QEvent

    settings_service.update_agent({"show_floating": True})
    agent.apply_settings()
    cat = agent.floating
    assert cat is not None and cat.isVisible() is True

    QApplication.sendEvent(cat, QEvent(QEvent.WindowBlocked))
    assert cat.isVisible() is False

    QApplication.sendEvent(cat, QEvent(QEvent.WindowUnblocked))
    assert cat.isVisible() is True


# ─────────────────────────────────────────────────────────────
# 7. Живая проверка: окно программы закрывается — кот исчезает
# ─────────────────────────────────────────────────────────────

#: На раннере GitHub этот тест роняет процесс НАТИВНО (junit пустой, сводки
#: нет): строится настоящее главное окно и закрывается (или скрывается).
#: Локально проходит — и один, и всей папкой. В CI пропускается маркером
#: `runner_crash`, на машине оператора идёт: разбор — `docs/STATE.md`, ЧАСТЬ 4.
@pytest.mark.runner_crash
def test_main_window_force_close_closes_cat(qapp, isolated_db, settings_service):
    """
    force_close() настоящего окна «Экспедиторство» закрывает кота.

    Это дословный сценарий бага #18: включён плавающий кот, окно
    закрывается «по-настоящему» (так выходит из программы main._do_exit),
    и кот не должен остаться на экране.
    """
    from ui.main_window import MainWindow

    # Панель «при старте» выключается ЯВНО: с ней кот по построению прячется
    # (уступает место полю ввода), а тест про закрытие окна, а не про старт.
    settings_service.update_agent(
        {"show_floating": True, "show_panel_on_start": False})
    main = MainWindow()
    main.show()
    try:
        cat = getattr(main.agent_widget, "floating", None)
        assert cat is not None, "кот не создан при show_floating = true"
        assert cat.parent() is main
        assert cat.isVisible() is True

        main.force_close()
        assert cat.is_closed is True
        assert cat.isVisible() is False
    finally:
        main.deleteLater()


#: На раннере GitHub этот тест роняет процесс НАТИВНО (junit пустой, сводки
#: нет): строится настоящее главное окно и закрывается (или скрывается).
#: Локально проходит — и один, и всей папкой. В CI пропускается маркером
#: `runner_crash`, на машине оператора идёт: разбор — `docs/STATE.md`, ЧАСТЬ 4.
@pytest.mark.runner_crash
def test_main_window_hide_hides_cat(qapp, isolated_db, settings_service):
    """Закрытие крестиком (скрытие окна) тоже прячет кота."""
    from ui.main_window import MainWindow

    # Как и в предыдущем тесте: панель «при старте» выключена, иначе кот
    # спрятан панелью, и проверка «крестик прячет кота» ничего не значит.
    settings_service.update_agent(
        {"show_floating": True, "show_panel_on_start": False})
    main = MainWindow()
    main.show()
    try:
        cat = getattr(main.agent_widget, "floating", None)
        assert cat is not None
        assert cat.isVisible() is True

        main.close()          # крестик: окно прячется (ЭТАП 2B)
        assert cat.isVisible() is False
    finally:
        main.force_close()
        main.deleteLater()
