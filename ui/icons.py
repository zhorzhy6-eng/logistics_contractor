#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Помощники иконок с учётом активной темы (ЭТАП 2B).

Раньше _themed_icon/_tab_icon/_action_icon/_resource_path жили только
в ui/main_window.py. С добавлением кнопок на вкладки их надо
использовать и там — поэтому вынесены сюда.

Поведение не меняется: для тёмной темы сначала ищется вариант в
подпапке dark/, иначе берётся обычный файл.
"""

import logging
import os

from PyQt5.QtGui import QIcon

from core import app_paths
from ui import theme

logger = logging.getLogger("ui.icons")


def resource_path(relative: str) -> str:
    """
    Путь к ресурсу: в разработке — корень проекта, в .exe — папка сборки.

    Считается общим модулем путей (core/app_paths.py), а не здесь: тот же
    вопрос решают шаблоны, правовая база и настройки, и второй ответ на
    него разошёлся бы с первым.
    """
    return str(app_paths.resource_path(relative))


def themed_icon(folder: str, name: str) -> QIcon:
    """
    Иконка с учётом активной темы.

    Для тёмных тем сначала ищется вариант в подпапке ``dark/``: светлые
    значки на тёмном фоне читаются плохо. Если варианта нет, берётся обычный
    файл — отсутствие картинки не должно ломать интерфейс.
    """
    if theme.is_dark():
        dark_path = resource_path(
            os.path.join("resources", "icons", folder, "dark", name)
        )
        dark_icon = QIcon(dark_path)
        if not dark_icon.isNull():
            return dark_icon

    path = resource_path(os.path.join("resources", "icons", folder, name))
    icon = QIcon(path)
    if icon.isNull():
        logger.warning("Иконка не загружена: %s", path)
    return icon


def tab_icon(name: str) -> QIcon:
    """Иконка вкладки (resources/icons/tabs)."""
    return themed_icon("tabs", name)


def action_icon(name: str) -> QIcon:
    """Иконка действия (resources/icons/actions)."""
    return themed_icon("actions", name)


__all__ = [
    "action_icon",
    "resource_path",
    "tab_icon",
    "themed_icon",
]
