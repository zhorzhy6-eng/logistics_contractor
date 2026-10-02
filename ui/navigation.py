#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Боковая навигация главного окна (сайдбар).

Виджет — только оформление и ввод: он показывает разделы и сообщает
выбранный индекс сигналом ``navigate(int)``. Никакой логики приложения
здесь нет: страницы по-прежнему живут в ``QTabWidget`` главного окна
(ui/main_window.py), сайдбар лишь переключает его текущую страницу.

Такое разделение сохраняет прежнее поведение вкладок: сигналы
``currentChanged``, порядок страниц, ``self.tabs.setCurrentWidget(...)``
и статус-бар работают как раньше.
"""

import logging
from typing import List, Optional

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QFrame, QLabel, QPushButton, QVBoxLayout, QWidget,
)

logger = logging.getLogger("ui.navigation")


class SideNav(QFrame):
    """Список разделов слева: иконка, подпись, активный пункт выделен."""

    #: Выбран раздел с таким индексом (индекс совпадает с индексом вкладки).
    navigate = pyqtSignal(int)

    def __init__(
        self,
        title: str = "Подготовка данных и оформление документов",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("sideNav")

        self._buttons: List[QPushButton] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 12, 8, 12)
        root.setSpacing(6)

        self.section_title = QLabel(title)
        self.section_title.setObjectName("navSectionTitle")
        self.section_title.setWordWrap(True)
        root.addWidget(self.section_title)

        separator = QFrame()
        separator.setObjectName("navSeparator")
        separator.setFixedHeight(1)
        root.addWidget(separator)

        self._items_layout = QVBoxLayout()
        self._items_layout.setContentsMargins(0, 4, 0, 0)
        self._items_layout.setSpacing(2)
        root.addLayout(self._items_layout)

        root.addStretch(1)

    # ── Наполнение ──
    def add_item(self, text: str, icon: Optional[QIcon] = None,
                 tooltip: str = "") -> QPushButton:
        """Добавляет пункт навигации и возвращает созданную кнопку."""
        button = QPushButton(text)
        button.setObjectName("navItem")
        button.setCheckable(True)
        button.setAutoExclusive(False)  # эксклюзивность обеспечивает сам SideNav
        button.setCursor(Qt.PointingHandCursor)
        button.setIconSize(QSize(20, 20))
        if icon is not None and not icon.isNull():
            button.setIcon(icon)
        if tooltip:
            button.setToolTip(tooltip)
        else:
            button.setToolTip(text)

        index = len(self._buttons)
        button.clicked.connect(lambda _checked=False, i=index: self.navigate.emit(i))

        self._items_layout.addWidget(button)
        self._buttons.append(button)
        return button

    # ── Состояние ──
    def count(self) -> int:
        """Сколько пунктов в навигации."""
        return len(self._buttons)

    def item_text(self, index: int) -> str:
        """Подпись пункта."""
        if 0 <= index < len(self._buttons):
            return self._buttons[index].text()
        return ""

    def current_index(self) -> int:
        """Индекс выделенного пункта (-1, если ничего не выбрано)."""
        for index, button in enumerate(self._buttons):
            if button.isChecked():
                return index
        return -1

    def set_current_index(self, index: int) -> None:
        """
        Выделяет пункт без повторной эмиссии navigate.

        Вызывается главным окном при смене вкладки (в том числе программной,
        например после загрузки записи из справочника).
        """
        for i, button in enumerate(self._buttons):
            was_blocked = button.blockSignals(True)
            button.setChecked(i == index)
            button.blockSignals(was_blocked)

    def set_item_icon(self, index: int, icon: QIcon) -> None:
        """Меняет иконку пункта (например, при переключении на тёмную тему)."""
        if 0 <= index < len(self._buttons) and icon is not None and not icon.isNull():
            self._buttons[index].setIcon(icon)

    def buttons(self) -> List[QPushButton]:
        """Кнопки пунктов (для тестов и обновления иконок)."""
        return list(self._buttons)


__all__ = ["SideNav"]
