# -*- coding: utf-8 -*-
"""
Тема оформления: выбор пользователя, следование за темой Windows, иконки.

Этап 6 (16.10.2026), подэтап 1.J: сюда переехали из прежнего
`ui/main_window.py` методы оформления. Логика не менялась — только переезд.

Правило приоритета (менять нельзя): ручной выбор пользователя в диалоге
настроек важнее системной темы Windows. Пока флажок «Следовать за темой
Windows» включён, смена режима Windows переключает оформление, но в
`config/settings.json` НЕ пишется — это не выбор оператора.
"""

import logging

from PyQt5.QtWidgets import QApplication

from ui import theme
from ui.icons import action_icon as _action_icon
from ui.icons import tab_icon as _tab_icon

# Имя логгера задано ЯВНО (а не __name__): после переезда в пакет
# `ui.main_window.theme` прежние записи выпали бы из настройки
# `config/logging_config.py` (APP_LOGGERS = "core", "ui", "db", ...).
logger = logging.getLogger("ui.main_window")


class ThemeMixin:
    """
    Оформление окна: тема, слежение за Windows, иконки вкладок и кнопок.

    Своего `__init__` у миксина нет: виджеты (`side_nav`, кнопки, вкладки)
    создаёт `LayoutMixin`, а watcher системы заводит `MainWindow.__init__`
    уже ПОСЛЕ раскладки. Методы миксина написаны так, чтобы работать и до
    появления виджетов: они ищут их через `getattr(..., None)`.
    """

    def _apply_theme_choice(self, theme_key: str, save: bool = True) -> None:
        """
        Применяет тему оформления к приложению.

        Меняется только визуальный слой (QSS и палитра Qt): состав полей,
        данные формы и логика не затрагиваются. По умолчанию выбор сразу
        сохраняется в настройках — это ручной выбор пользователя, который
        приоритетнее системной темы Windows.
        """
        key = theme.normalize(theme_key)
        theme.apply_theme(QApplication.instance(), key)
        self._refresh_nav_icons()

        if save:
            self.settings_service.update({"ui_theme": key})

        self._log_ui_action("смена темы оформления", theme=key)
        self.statusBar().showMessage(
            f"Тема оформления: {theme.theme_label(key)}", 5000
        )

    # --------------------------------------------------------
    # СЛЕДОВАНИЕ ЗА ТЕМОЙ WINDOWS
    # --------------------------------------------------------
    def _sync_system_theme_watcher(self) -> None:
        """
        Включает или выключает слежение за темой Windows по настройке.

        Слежение работает, пока пользователь не выбрал тему сам (флажок
        «Следовать за темой Windows» в настройках): ручной выбор
        приоритетнее системного режима.
        """
        watcher = getattr(self, "system_theme_watcher", None)
        if watcher is None:
            return

        follow = self.settings_service.get_bool("ui_theme_follow_system", True)
        if follow and not watcher.is_running():
            watcher.start()
        elif not follow and watcher.is_running():
            watcher.stop()

    def _on_system_theme_changed(self, theme_key: str) -> None:
        """
        Windows сменил режим — переключаем оформление.

        В настройки тема не записывается: это не выбор пользователя, а
        следование за системой. При выключенном следовании ничего не делаем.
        """
        if not self.settings_service.get_bool("ui_theme_follow_system", True):
            return

        self._apply_theme_choice(theme_key, save=False)
        self._log_ui_action("тема Windows изменилась", theme=theme_key)
        self.statusBar().showMessage(
            f"Тема Windows: {theme.theme_label(theme_key)}", 5000
        )

    def _refresh_nav_icons(self) -> None:
        """
        Пересобирает иконки под активную тему.

        У тёмных тем свой набор значков (resources/icons/**/dark/), поэтому
        после смены оформления их нужно заменить, иначе светлые иконки
        останутся на тёмном фоне.
        """
        side_nav = getattr(self, "side_nav", None)
        if side_nav is not None:
            for index, name in enumerate(self._TAB_ICON_NAMES):
                side_nav.set_item_icon(index, _tab_icon(name))

        for button, name in (
            (getattr(self, "btn_create_contract", None), "contract.svg"),
            (getattr(self, "btn_recognize", None), "recognize.svg"),
            (getattr(self, "btn_save_db", None), "save.svg"),
            (getattr(self, "btn_open_db", None), "database.svg"),
            (getattr(self, "btn_settings", None), "settings.svg"),
        ):
            if button is not None:
                button.setIcon(_action_icon(name))

        # ── Кнопки на вкладках (ЭТАП 2B) ──
        # «Создать договор» и «Очистить форму» живут на каждой вкладке: при
        # смене темы их значки нужно пересобрать, иначе светлые останутся
        # на тёмном фоне.
        for tab in (
            getattr(self, "carrier_tab", None),
            getattr(self, "driver_tab", None),
            getattr(self, "trailer_tab", None),
            getattr(self, "vehicles_tab", None),
            getattr(self, "contract_tab", None),
            getattr(self, "customer_tab", None),
        ):
            if tab is None:
                continue
            for attr, name in (
                ("btn_create_contract", "contract.svg"),
                ("btn_clear_form", "clear.svg"),
            ):
                button = getattr(tab, attr, None)
                if button is not None:
                    button.setIcon(_action_icon(name))
