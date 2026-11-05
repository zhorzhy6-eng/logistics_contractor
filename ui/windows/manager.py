#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Менеджер окон типов договоров (ЭТАП 2B).

Окна создаются лениво (при первом обращении) и живут до выхода
из приложения — данные в формах не теряются при переключении типа.

Переключение = hide() текущего окна + show() выбранного. Закрытие
окна пользователем (крестик) — event.ignore() + hide(), данные целы
(см. closeEvent в ui/main_window.py и ui/placeholder_window.py).
"""

import logging
import weakref
from typing import Callable, Dict, Optional, Tuple

from PyQt5.QtWidgets import QMainWindow

logger = logging.getLogger("ui.windows.manager")


#: Слабые ссылки на менеджер окон. Нужны окну «Экспедиторство»: кнопка
#: «Создать договор» спрашивает, какие типы умеют зеркало, и берёт окно
#: цели отсюда. Ссылка СЛАБАЯ: сильная навсегда продлила бы жизнь менеджеру
#: и всем его окнам (в тестах это утечка, в приложении — лишний цикл
#: «окно → менеджер → окно»). Реестр — список, а не одно значение: тесты
#: создают менеджеры пачками, и устаревшая ссылка должна исчезнуть сама.
_MANAGER_REFS: list = []


def register_window_manager(manager) -> None:
    """
    Регистрирует менеджер окон для связанных договоров.

    Вызывается main.py сразу после создания WindowManager. Не QObject и не
    зависимость от ui.main_window: менеджер — обычный объект, а Экспедиторство
    (ui/main_window/contract.py) ищет его по реестру, поэтому ui.main_window
    не знает ни одного класса окна типа.
    """
    if manager is None:
        return

    _MANAGER_REFS[:] = [ref for ref in _MANAGER_REFS if ref() is not None]
    if any(ref() is manager for ref in _MANAGER_REFS):
        return

    try:
        _MANAGER_REFS.append(weakref.ref(manager))
    except TypeError:
        # Объект без слабых ссылок (заглушка в тесте): регистрировать нечего.
        logger.warning("Связанные договоры: менеджер окон не поддерживает weakref")
        return

    logger.info("Связанные договоры: менеджер окон зарегистрирован")


def current_window_manager():
    """
    Живой менеджер окон или None.

    None — обычный режим: main.py ещё не создал менеджер (тесты, запуск без
    окна). Тогда связанных типов просто нет, и Экспедиторство работает как
    раньше, без диалога выбора.
    """
    for ref in list(_MANAGER_REFS):
        manager = ref()
        if manager is not None:
            return manager
    return None


def clear_window_manager() -> None:
    """Сбрасывает реестр менеджера окон (тесты)."""
    _MANAGER_REFS.clear()


class WindowManager:
    """
    Держит окна по ключу ContractType.value, переключает видимое.

    Не QObject: сигналы (если нужны) — отдельно, через QObject-обёртку.
    Пока никаких сигналов: main.py сам вызывает switch_to().
    """

    def __init__(self, factories: Dict[str, Callable[[], QMainWindow]]):
        self._factories = dict(factories)
        self._windows: Dict[str, QMainWindow] = {}
        self._current_type: Optional[str] = None

    # ---------------------------------------------------------
    # Реестр типов
    # ---------------------------------------------------------
    def contract_types(self) -> Tuple[str, ...]:
        """
        Ключи типов, для которых менеджер умеет создать окно.

        Порядок — как у фабрик (тот же, что при запуске). По этому списку
        «Экспедиторство» узнаёт, какие типы вообще доступны, и оставляет из
        них только те, у которых есть кнопка «Отзеркалить».
        """
        return tuple(self._factories)

    # ---------------------------------------------------------
    # Создание окон
    # ---------------------------------------------------------
    def window_for(self, contract_type: str) -> Optional[QMainWindow]:
        """Создаёт окно лениво, кэширует."""
        if contract_type in self._windows:
            return self._windows[contract_type]

        factory = self._factories.get(contract_type)
        if factory is None:
            logger.error("Нет фабрики окна для типа %r", contract_type)
            return None

        window = factory()
        self._windows[contract_type] = window
        logger.info(
            "Окно создано: тип=%s, окон=%s", contract_type, len(self._windows)
        )
        return window

    # ---------------------------------------------------------
    # Переключение
    # ---------------------------------------------------------
    def switch_to(self, contract_type: str) -> Optional[QMainWindow]:
        """Скрывает текущее окно, показывает выбранное."""
        if contract_type == self._current_type and contract_type in self._windows:
            window = self._windows[contract_type]
            window.show()
            window.raise_()
            window.activateWindow()
            return window

        # Окно создаём ДО того, как спрячем текущее: если фабрики для типа
        # нет, пользователь не останется без окна вовсе.
        window = self.window_for(contract_type)
        if window is None:
            return None

        if self._current_type is not None:
            current = self._windows.get(self._current_type)
            if current is not None and current is not window:
                current.hide()

        window.show()
        window.raise_()
        window.activateWindow()
        self._current_type = contract_type
        logger.info("Переключение окна: тип=%s", contract_type)
        return window

    def current_type(self) -> Optional[str]:
        """Ключ типа, окно которого показано сейчас."""
        return self._current_type

    def current_window(self) -> Optional[QMainWindow]:
        """Показанное окно (None, если переключения ещё не было)."""
        if self._current_type is None:
            return None
        return self._windows.get(self._current_type)

    def windows(self) -> Dict[str, QMainWindow]:
        """Копия реестра созданных окон: ключ типа → окно."""
        return dict(self._windows)

    # ---------------------------------------------------------
    # Завершение работы
    # ---------------------------------------------------------
    @staticmethod
    def _force_close(window: QMainWindow) -> None:
        """
        Закрывает окно по-настоящему.

        Крестик в окне только прячет его (данные не теряются), поэтому для
        реального выхода нужно явное разрешение: окна, которые его умеют
        (MainWindow/PlaceholderWindow, метод force_close), закрываются
        по-настоящему; остальные — обычным close().
        """
        force = getattr(window, "force_close", None)
        if callable(force):
            force()
        else:
            window.close()

    def close_all(self) -> None:
        """Реальное закрытие всех окон (при выходе)."""
        for contract_type, window in list(self._windows.items()):
            try:
                self._force_close(window)
            except Exception as e:  # noqa: BLE001 — выход не должен падать
                logger.error(
                    "Не удалось закрыть окно типа %r: %s", contract_type, e
                )
        self._windows.clear()
        self._current_type = None
        logger.info("Все окна закрыты")


__all__ = [
    "WindowManager",
    "clear_window_manager",
    "current_window_manager",
    "register_window_manager",
]
