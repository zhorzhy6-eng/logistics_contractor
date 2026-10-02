#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тема оформления Windows: определение текущего режима и слежение за сменой.

Зачем отдельный модуль
----------------------
Qt 5.15 не сообщает о системной теме: ``QStyleHints.colorScheme()`` и сигнал
``colorSchemeChanged`` появились только в Qt 6.5, а ``QEvent.ThemeChange``
в PyQt5 отсутствует. Поэтому режим читается из реестра Windows через
``winreg`` (стандартная библиотека — новых зависимостей нет), а изменение
отслеживается периодическим опросом через ``QTimer``.

Логики приложения здесь нет: модуль отвечает только на вопрос «Windows
сейчас в тёмном режиме?» и сообщает об изменении. Ключи тем — те же, что
в ui/theme_palettes.py (``classic`` и ``dark_pro``).
"""

import logging
import sys
from typing import Any, Optional

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

logger = logging.getLogger("ui.system_theme")

#: Ключ реестра с режимом оформления приложений.
_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

#: Значение AppsUseLightTheme: 1 — светлое оформление, 0 — тёмное.
_REGISTRY_VALUE = "AppsUseLightTheme"
_LIGHT_VALUE = 1

#: Ключи тем приложения (см. ui/theme_palettes.py).
LIGHT_THEME = "classic"
DARK_THEME = "dark_pro"

#: Как часто проверять системный режим (мс). Чтение одного значения реестра
#: занимает микросекунды, поэтому опрос незаметен для пользователя.
DEFAULT_INTERVAL_MS = 3000

#: Как часто перечитывать режим, если реестр прочитать не удалось.
RETRY_INTERVAL_MS = 10000


def system_theme_supported() -> bool:
    """Есть ли смысл спрашивать систему о теме (только Windows)."""
    return sys.platform.startswith("win")


def read_system_theme() -> Optional[str]:
    """
    Тема Windows: LIGHT_THEME, DARK_THEME или None, если определить нельзя.

    None — не ошибка: на других платформах, при отсутствии ключа в реестре
    или при запрете чтения приложение просто не навязывает свою тему.
    """
    if not system_theme_supported():
        return None

    try:
        import winreg
    except ImportError:  # pragma: no cover — на Windows winreg есть всегда
        return None

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_KEY) as key:
            value, _value_type = winreg.QueryValueEx(key, _REGISTRY_VALUE)
    except OSError as e:
        logger.debug("Тема Windows недоступна: %s", e)
        return None

    try:
        use_light = int(value)
    except (TypeError, ValueError):
        logger.debug("Неожиданное значение AppsUseLightTheme: %r", value)
        return None

    return LIGHT_THEME if use_light == _LIGHT_VALUE else DARK_THEME


def preferred_theme(settings: Any, default: str = LIGHT_THEME) -> str:
    """
    Тема для запуска приложения.

    Если включено следование за Windows (``ui_theme_follow_system``),
    берётся системный режим; если система недоступна или следование
    выключено — сохранённый выбор пользователя (``ui_theme``).

    :param settings: SettingsService (достаточно get_str/get_bool).
    """
    manual = settings.get_str("ui_theme", "") or default
    if not settings.get_bool("ui_theme_follow_system", True):
        return manual
    return read_system_theme() or manual


class SystemThemeWatcher(QObject):
    """
    Следит за темой Windows и сообщает о смене.

    Сигнал ``theme_changed`` приходит только при фактическом изменении
    режима (и никогда — при первом чтении), поэтому подписчик может
    спокойно применять тему, не опасаясь лишних перерисовок.
    """

    #: Новый ключ темы: LIGHT_THEME или DARK_THEME.
    theme_changed = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None,
                 interval_ms: int = DEFAULT_INTERVAL_MS):
        super().__init__(parent)
        self._interval_ms = int(interval_ms)
        self._current: Optional[str] = None

        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self.check_now)

    # ── Управление ──
    def start(self) -> Optional[str]:
        """Запоминает текущий режим и включает слежение."""
        self._current = read_system_theme()
        if self._interval_ms > 0:
            self._timer.start()
        logger.debug("Слежение за темой Windows включено (режим: %s)", self._current)
        return self._current

    def stop(self) -> None:
        """Выключает слежение (например, пользователь выбрал тему вручную)."""
        self._timer.stop()
        logger.debug("Слежение за темой Windows выключено")

    def is_running(self) -> bool:
        return self._timer.isActive()

    def current(self) -> Optional[str]:
        """Последний известный режим системы."""
        return self._current

    # ── Проверка ──
    def check_now(self) -> Optional[str]:
        """
        Проверяет режим системы сейчас.

        :return: текущий режим (LIGHT_THEME/DARK_THEME) или None.
        """
        theme = read_system_theme()
        previous, self._current = self._current, theme

        if theme is not None and previous is not None and theme != previous:
            logger.info("Тема Windows изменилась: %s → %s", previous, theme)
            self.theme_changed.emit(theme)

        return theme


__all__ = [
    "LIGHT_THEME",
    "DARK_THEME",
    "DEFAULT_INTERVAL_MS",
    "RETRY_INTERVAL_MS",
    "system_theme_supported",
    "read_system_theme",
    "preferred_theme",
    "SystemThemeWatcher",
]
