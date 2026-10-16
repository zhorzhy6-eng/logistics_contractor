#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Кнопка диагностики в окнах (задание, часть 3, пункт A).

Решено так: **одно место в меню плюс кнопка в панели Максимилиана** (A.4).

  * «Справка → Диагностика…» есть в КАЖДОМ окне — и в «Экспедиторстве»
    (``ui/main_window.py``), и в окнах типов (``ui/windows/base_window.py``):
    оператор ищет помощь там, где работает, а не в одном конкретном окне.
    Меню добавляется одним вызовом ``install_diagnostics_menu(window)``.
  * кнопка «🔧 Диагностика» живёт в панели Максимилиана
    (``ui/agent_widget.py``): панель — то место, куда оператор приходит
    сказать «сломалось».

Почему не отдельная кнопка в шапке: шапка у каждого окна своя, и место в ней
уже занято рабочими кнопками. Меню «Справка» — привычное место для
диагностики, а панель Максимилиана — второе, о котором оператор уже знает.
"""

import logging
from typing import Optional

from PyQt5.QtWidgets import QAction, QWidget

logger = logging.getLogger("ui.diagnostics_menu")

#: Текст пункта меню (задание, часть A.1: «Справка → Диагностика»).
MENU_TITLE = "Справка"
ACTION_TITLE = "Диагностика…"

#: Подсказка пункта меню.
ACTION_HINT = (
    "Проверить состояние программы и собрать отчёт для разработчика. "
    "Диагностика ничего не меняет — только читает."
)


def install_diagnostics_menu(window: Optional[QWidget],
                             live_network: bool = False) -> Optional[QAction]:
    """
    Добавляет в окно меню «Справка → Диагностика» (задание, часть A.1).

    :param window: окно (QMainWindow). У окна без ``menuBar`` (заглушка в
        тесте) ничего не делается: сообщение уходит в лог.
    :param live_network: проверять ли GigaChat и DaData сетевыми запросами.
    :return: созданный QAction (нужен тестам) или None.
    """
    if window is None:
        return None

    menu_bar = getattr(window, "menuBar", None)
    if not callable(menu_bar):
        logger.info("Диагностика: у окна нет меню — пункт не добавлен")
        return None

    try:
        bar = menu_bar()
    except Exception as e:  # noqa: BLE001 — меню не должно ронять окно
        logger.warning("Диагностика: меню недоступно (%s)", type(e).__name__)
        return None

    action = QAction(ACTION_TITLE, window)
    action.setStatusTip(ACTION_HINT)
    action.setObjectName("diagnosticsAction")
    action.triggered.connect(window.open_diagnostics)
    #: Ссылка на действие держится окном: QAction без родителя-владельца
    #: собирается сборщиком мусора Python раньше, чем Qt закончит работу.
    window.diagnostics_action = action

    menu = bar.addMenu(MENU_TITLE)
    menu.setObjectName("helpMenu")
    menu.addAction(action)
    window.help_menu = menu
    logger.info("Диагностика: пункт «%s → %s» добавлен",
                MENU_TITLE, ACTION_TITLE)
    return action


def window_diagnostics(window: Optional[QWidget],
                       live_network: bool = False) -> Optional[str]:
    """
    Открывает диалог диагностики для окна (общий код меню и кнопки).

    Импорт внутри функции: диагностика — надстройка, и её сбой не должен
    мешать открытию окна.
    """
    try:
        from ui.diagnostics_dialog import open_diagnostics

        return open_diagnostics(window, live_network=live_network)
    except Exception:  # noqa: BLE001 — окно не должно падать из-за отчёта
        logger.exception("Диалог диагностики не открылся")
        return None


class DiagnosticsMenuMixin:
    """
    Добавляет окну пункт «Справка → Диагностика» и метод его открытия.

    Миксин, а не дублирование в двух окнах: «Экспедиторство» и окна типов
    наследуются от разных классов (QMainWindow и BaseContractWindow), и
    единственный общий способ дать им одно и то же поведение — миксин.
    Подключается первым в списке базовых классов.
    """

    #: Можно ли проверять GigaChat и DaData сетевыми запросами.
    diagnostics_live_network: bool = False

    def init_diagnostics_menu(self) -> Optional[QAction]:
        """Добавляет меню «Справка → Диагностика» (вызывается из __init__)."""
        return install_diagnostics_menu(self, live_network=bool(
            self.diagnostics_live_network))

    def open_diagnostics(self) -> Optional[str]:
        """Открывает диалог диагностики (слот пункта меню и кнопки)."""
        return window_diagnostics(self, live_network=bool(
            self.diagnostics_live_network))


__all__ = [
    "ACTION_TITLE",
    "MENU_TITLE",
    "DiagnosticsMenuMixin",
    "install_diagnostics_menu",
    "window_diagnostics",
]
