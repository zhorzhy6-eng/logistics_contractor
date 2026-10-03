#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты помощников иконок (ЭТАП 2B, ui/icons.py).

Проверяют: значки действий и вкладок действительно загружаются, путь к
ресурсам ведёт в проект, а для тёмной темы берётся вариант из подпапки
``dark/``. Qt — в offscreen-режиме.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ui import icons, theme  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def classic_theme(qt_app):
    """Каждый тест начинается со светлой темы: тема — состояние процесса."""
    theme.apply_theme(qt_app, "classic")
    yield
    theme.apply_theme(qt_app, "classic")


# ─────────────────────────────────────────────────────────────
# Загрузка значков
# ─────────────────────────────────────────────────────────────

def test_action_icon_is_loaded(qt_app):
    icon = icons.action_icon("contract.svg")
    assert not icon.isNull()
    assert not icon.pixmap(18, 18).isNull()


def test_tab_icon_is_loaded(qt_app):
    icon = icons.tab_icon("carrier.svg")
    assert not icon.isNull()
    assert not icon.pixmap(24, 24).isNull()


def test_clear_icon_is_loaded(qt_app):
    """Кнопка «Очистить форму» на вкладках: значок есть в наборе actions."""
    assert not icons.action_icon("clear.svg").isNull()


def test_missing_icon_does_not_crash(qt_app):
    """Отсутствующий файл — пустая иконка, без исключения."""
    assert icons.action_icon("no-such-icon.svg").isNull()


# ─────────────────────────────────────────────────────────────
# Путь к ресурсам
# ─────────────────────────────────────────────────────────────

def test_resource_path_points_to_project():
    path = icons.resource_path("templates")
    assert "templates" in path
    assert os.path.isdir(path)


def test_resource_path_uses_meipass(monkeypatch, tmp_path):
    """В сборке PyInstaller ресурсы берутся из распакованной папки."""
    monkeypatch.setattr(icons.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert icons.resource_path("resources/icons") == os.path.join(
        str(tmp_path), "resources/icons"
    )


# ─────────────────────────────────────────────────────────────
# Тёмная тема: подпапка dark/
# ─────────────────────────────────────────────────────────────

def test_dark_theme_prefers_dark_variant(qt_app, monkeypatch):
    """Для тёмной темы сначала запрашивается файл из dark/."""
    asked = []
    original = icons.resource_path

    def spy(relative):
        asked.append(relative.replace("\\", "/"))
        return original(relative)

    monkeypatch.setattr(icons, "resource_path", spy)
    theme.apply_theme(qt_app, "dark_pro")

    icon = icons.action_icon("contract.svg")

    assert not icon.isNull()
    assert any("/dark/" in path for path in asked), asked


def test_light_theme_does_not_look_into_dark(qt_app, monkeypatch):
    """Для светлой темы dark/-вариант не запрашивается вовсе."""
    asked = []
    original = icons.resource_path

    def spy(relative):
        asked.append(relative.replace("\\", "/"))
        return original(relative)

    monkeypatch.setattr(icons, "resource_path", spy)
    theme.apply_theme(qt_app, "classic")

    icons.tab_icon("driver.svg")

    assert asked
    assert not any("/dark/" in path for path in asked), asked


def test_icon_changes_with_theme(qt_app):
    """Значок тёмной темы отличается от светлого (в наборе есть dark/)."""
    light = icons.action_icon("contract.svg").pixmap(18, 18).toImage()
    theme.apply_theme(qt_app, "dark_pro")
    dark = icons.action_icon("contract.svg").pixmap(18, 18).toImage()

    assert not dark.isNull()
    assert light != dark


def test_missing_dark_variant_falls_back(qt_app, monkeypatch):
    """Если dark/-варианта нет, берётся обычный файл (без пустой иконки)."""
    original = icons.resource_path

    def fake(relative):
        path = original(relative)
        if "/dark/" in relative.replace("\\", "/"):
            # Вариант для тёмной темы «отсутствует»
            return os.path.join(os.path.dirname(path), "no-dark-variant.svg")
        return path

    monkeypatch.setattr(icons, "resource_path", fake)
    theme.apply_theme(qt_app, "dark_pro")

    icon = icons.action_icon("contract.svg")
    assert not icon.isNull()


# ─────────────────────────────────────────────────────────────
# Обратная совместимость: имена остались в ui/main_window.py
# ─────────────────────────────────────────────────────────────

def test_main_window_reexports_icon_helpers(qt_app):
    """Старые имена из ui.main_window указывают на новые функции."""
    from ui import main_window

    assert main_window._action_icon is icons.action_icon
    assert main_window._tab_icon is icons.tab_icon
    assert main_window._themed_icon is icons.themed_icon
    assert main_window._resource_path is icons.resource_path
