#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Базовое окно для новых типов договоров (ЭТАП 2C).

Каркас:
  * шапка: название окна, селектор типа, кнопка «Выход»;
  * body: SideNav слева + QTabWidget справа (по образцу MainWindow);
  * вкладки размечены в TAB_CONFIGS подкласса (пока пустые);
  * панель действий «Создать договор» / «Очистить форму» внизу каждой вкладки;
  * селектор эмитит switch_to_type_requested(str), main.py ловит и переключает.

Что НЕ делает:
  * не генерирует документы (это ЭТАПЫ 3+);
  * не вызывает GigaChat (кнопка «Распознать данные» — заглушка);
  * не трогает MainWindow (Экспедиторство).

Окна типов живут всё время приложения: закрытие крестиком — это hide()
(данные в форме не теряются), реальное закрытие — force_close(), его
вызывает WindowManager.close_all() при выходе.
"""

import logging
from typing import List, Optional, Tuple

from PyQt5.QtCore import QSize, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from core.contracts.picker_order import picker_title
from core.prompts import get_prompt
from ui import theme
from ui.controls.contract_type_selector import ContractTypeSelector
from ui.icons import action_icon, tab_icon
from ui.navigation import SideNav

logger = logging.getLogger("ui.windows.base_window")


class BaseContractWindow(QMainWindow):
    """Общий каркас окна типа договора: шапка, сайдбар, вкладки-заглушки."""

    #: Ключ типа (значение ContractType) — обязателен у подкласса.
    CONTRACT_TYPE: str = ""
    #: Заголовок окна — обязателен у подкласса.
    WINDOW_TITLE: str = ""
    #: Вкладки: (заголовок, ключ иконки в resources/icons/tabs/).
    TAB_CONFIGS: List[Tuple[str, str]] = []

    #: Пользователь выбрал другой тип в селекторе.
    switch_to_type_requested = pyqtSignal(str)
    #: Пользователь нажал «Выход».
    exit_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(self.WINDOW_TITLE or picker_title(self.CONTRACT_TYPE))
        self.resize(1200, 900)
        self._init_ui()
        logger.info(
            "Окно типа %r создано: вкладок=%s",
            self.CONTRACT_TYPE, self.tabs.count(),
        )

    # ---------------------------------------------------------
    # Построение интерфейса
    # ---------------------------------------------------------
    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 16, 20, 8)
        main_layout.setSpacing(12)

        main_layout.addWidget(self._build_header())

        # ── Body: сайдбар + вкладки (как в MainWindow) ──
        self.tabs = QTabWidget()
        self.tabs.setIconSize(QSize(24, 24))
        # Верхние ярлыки скрыты: разделы переключает сайдбар. Сам QTabWidget
        # остаётся — страницы, их порядок и сигналы такие же, как в MainWindow.
        self.tabs.tabBar().setVisible(False)

        self.side_nav = SideNav("Разделы")
        for tab_title, icon_key in self.TAB_CONFIGS:
            tab = self._make_placeholder_tab(tab_title)
            self.tabs.addTab(tab, tab_icon(icon_key), tab_title)
            self.side_nav.add_item(tab_title, tab_icon(icon_key))

        self.side_nav.navigate.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self._sync_side_nav)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self.side_nav)
        body.addWidget(self.tabs, 1)
        main_layout.addLayout(body)

        self._sync_side_nav(self.tabs.currentIndex())
        self.statusBar().showMessage("Готово")

    def _build_header(self) -> QFrame:
        """Шапка: название окна, селектор типа договора и кнопка «Выход»."""
        header_frame = QFrame()
        header_frame.setObjectName("appHeader")
        header = QHBoxLayout(header_frame)
        header.setContentsMargins(16, 10, 16, 10)
        header.setSpacing(12)

        heading = QVBoxLayout()
        heading.setSpacing(2)
        title = QLabel(self.WINDOW_TITLE or picker_title(self.CONTRACT_TYPE))
        title.setObjectName("appHeading")
        subtitle = QLabel("Тип договора в разработке")
        subtitle.setObjectName("appSubheading")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()

        self.selector = ContractTypeSelector(self.CONTRACT_TYPE)
        # Сигнал в сигнал: логика переключения окон живёт в main.py,
        # виджет только сообщает о выборе пользователя.
        self.selector.contract_type_selected.connect(self.switch_to_type_requested)
        header.addWidget(self.selector)

        self.btn_exit = theme.secondary_button(
            "Выход", tooltip="Закрыть программу"
        )
        self.btn_exit.clicked.connect(self.exit_requested)
        header.addWidget(self.btn_exit)

        return header_frame

    def _make_placeholder_tab(self, title: str) -> QWidget:
        """Вкладка-заглушка: сообщение и панель действий."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        label = QLabel(f"Вкладка «{title}» в разработке")
        label.setObjectName("sectionHint")
        layout.addWidget(label)
        layout.addStretch()

        layout.addWidget(self._build_tab_actions(tab))

        return tab

    def _build_tab_actions(self, tab: QWidget) -> QFrame:
        """
        Панель «Создать договор» + «Очистить форму» (заглушки).

        Кнопки, как и у вкладок MainWindow (ЭТАП 2B), сохраняются атрибутами
        самой вкладки: btn_create_contract, btn_clear_form, btn_recognize.
        """
        frame = QFrame()
        frame.setObjectName("actionBar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        tab.btn_create_contract = theme.accent_button(
            "Создать договор",
            tooltip="Проверить данные и сформировать договор DOCX",
        )
        tab.btn_create_contract.setIcon(action_icon("contract.svg"))
        tab.btn_create_contract.setIconSize(QSize(18, 18))
        tab.btn_create_contract.clicked.connect(self._on_create_contract)
        layout.addWidget(tab.btn_create_contract)

        tab.btn_clear_form = theme.secondary_button(
            "Очистить форму",
            tooltip="Очистить поля только этой вкладки",
        )
        tab.btn_clear_form.setIcon(action_icon("clear.svg"))
        tab.btn_clear_form.setIconSize(QSize(18, 18))
        tab.btn_clear_form.clicked.connect(self._on_clear_tab)
        layout.addWidget(tab.btn_clear_form)

        tab.btn_recognize = theme.primary_button("Распознать данные")
        tab.btn_recognize.setIcon(action_icon("recognize.svg"))
        tab.btn_recognize.setIconSize(QSize(18, 18))
        tab.btn_recognize.clicked.connect(self._on_recognize)
        layout.addWidget(tab.btn_recognize)

        layout.addStretch()
        return frame

    def _sync_side_nav(self, index: int) -> None:
        """Подсвечивает пункт сайдбара при смене вкладки."""
        side_nav = getattr(self, "side_nav", None)
        if side_nav is not None:
            side_nav.set_current_index(index)

    # ---------------------------------------------------------
    # Вспомогательное (для тестов и внешнего кода)
    # ---------------------------------------------------------
    def tab_titles(self) -> List[str]:
        """Заголовки вкладок в порядке добавления."""
        return [self.tabs.tabText(i) for i in range(self.tabs.count())]

    def refresh_icons(self) -> None:
        """
        Пересобирает иконки под активную тему.

        Пока окна-заглушки создаются в текущей теме; метод нужен на будущее,
        когда окна типов станут рабочими и переживут смену оформления.
        """
        for index, (_title, icon_key) in enumerate(self.TAB_CONFIGS):
            icon = tab_icon(icon_key)
            self.tabs.setTabIcon(index, icon)
            self.side_nav.set_item_icon(index, icon)
        for tab in self._tabs():
            for attr, name in (
                ("btn_create_contract", "contract.svg"),
                ("btn_clear_form", "clear.svg"),
                ("btn_recognize", "recognize.svg"),
            ):
                button = getattr(tab, attr, None)
                if button is not None:
                    button.setIcon(action_icon(name))

    def _tabs(self) -> List[QWidget]:
        """Вкладки окна (страницы QTabWidget)."""
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def _sender_tab(self) -> Optional[QWidget]:
        """
        Вкладка, которой принадлежит отправитель сигнала.

        Кнопки панели действий лежат внутри вкладки, но подниматься к ней
        нужно по иерархии виджетов: sender() — это кнопка, а не страница
        QTabWidget.
        """
        widget = self.sender()
        while widget is not None and self.tabs.indexOf(widget) < 0:
            widget = widget.parentWidget()
        return widget

    # ---------------------------------------------------------
    # Заглушки действий
    # ---------------------------------------------------------
    def _on_create_contract(self) -> None:
        logger.info(
            "UI: «Создать договор» для типа %s — ещё не реализовано",
            self.CONTRACT_TYPE,
        )
        QMessageBox.information(
            self, "В разработке",
            f"Генерация договора типа «{picker_title(self.CONTRACT_TYPE)}» "
            f"появится в следующих версиях."
        )

    def _on_clear_tab(self) -> None:
        """
        Очистка вкладки — заглушка (ЭТАП 2C).

        Вкладка берётся у отправителя сигнала (sender()), а не из замыкания:
        lambda, захватывающая вкладку, создаёт цикл ссылок Python ↔ Qt и
        роняет процесс при выходе (грабли ЭТАПА 2B).
        """
        tab = self._sender_tab()
        tab_title = self.tabs.tabText(self.tabs.indexOf(tab)) if tab is not None else ""
        logger.info(
            "UI: «Очистить форму» для типа %s, вкладка %r — ещё не реализовано",
            self.CONTRACT_TYPE, tab_title,
        )
        QMessageBox.information(
            self, "В разработке",
            f"Очистка вкладки для типа «{picker_title(self.CONTRACT_TYPE)}» "
            f"появится в следующих версиях."
        )

    def _on_recognize(self) -> None:
        """Набросок распознавания (ЭТАП 2C): вызывает get_prompt и логирует."""
        prompt = get_prompt(self.CONTRACT_TYPE)
        logger.info(
            "UI: набросок распознавания, тип=%s, промпт задан=%s",
            self.CONTRACT_TYPE, prompt is not None,
        )
        QMessageBox.information(
            self, "В разработке",
            f"Распознавание для типа «{picker_title(self.CONTRACT_TYPE)}» "
            f"появится в следующих версиях.\n\n"
            f"Промпт: {'задан' if prompt else 'ещё не написан'}."
        )

    # ---------------------------------------------------------
    # Закрытие окна — скрытие
    # ---------------------------------------------------------
    def closeEvent(self, event) -> None:
        """
        Закрытие окна = скрытие (ЭТАП 2C).

        Окна типов живут всё время приложения: крестик не должен терять
        данные формы. Реальное завершение — force_close() из
        WindowManager.close_all().
        """
        if getattr(self, "_force_close", False):
            event.accept()
            return

        logger.info("Окно типа %s скрыто (окно не уничтожено)", self.CONTRACT_TYPE)
        event.ignore()
        self.hide()

    def force_close(self) -> None:
        """Реальное закрытие (используется WindowManager.close_all())."""
        self._force_close = True
        self.blockSignals(True)
        self.close()
        self.deleteLater()


__all__ = ["BaseContractWindow"]
