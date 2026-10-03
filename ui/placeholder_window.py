#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Окно-заглушка для типов договоров, генераторы которых ещё не реализованы
(шаг 6 инфраструктуры типов).

Открывается из main.py вместо MainWindow, когда в диалоге выбора типа выбран
не «Экспедиторство»: рабочим остаётся только договор-заявка на перевозку,
остальные типы (Формика, Логистикс Рус, Разовая аренда, Хавалы) — заготовки.

Окно ничего не делает с данными: показывает название выбранного типа, его
ключ (для диагностики) и закрывается кнопкой «Закрыть».
"""

import logging

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget,
)

from ui import theme
from ui.contract_picker import picker_title

logger = logging.getLogger("ui.placeholder_window")


class PlaceholderWindow(QMainWindow):
    """Сообщает, что выбранный тип договора в разработке."""

    def __init__(self, contract_type: str, title: str = "", parent=None):
        super().__init__(parent)

        self.contract_type = str(contract_type or "")
        #: Название для пользователя: явный title → название из пикера → ключ.
        self.title = (
            str(title or "")
            or picker_title(self.contract_type)
            or self.contract_type
        )

        self.setWindowTitle(f"{self.title} — в разработке")
        self.resize(640, 360)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)

        self.heading = QLabel(f"Тип договора «{self.title}» в разработке")
        self.heading.setObjectName("appHeading")
        self.heading.setWordWrap(True)
        self.heading.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.heading)

        self.hint = QLabel(
            "Генератор этого типа ещё не реализован — сейчас доступен только "
            "тип «Экспедиторство» (договор-заявка на перевозку)."
        )
        self.hint.setObjectName("appSubheading")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.type_label = QLabel(f"Ключ типа: {self.contract_type}")
        self.type_label.setObjectName("sectionHint")
        layout.addWidget(self.type_label)

        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)

        self.btn_close = theme.secondary_button(
            "Закрыть", tooltip="Закрыть окно"
        )
        self.btn_close.clicked.connect(self.close)
        buttons.addWidget(self.btn_close)

        layout.addLayout(buttons)

        self.statusBar().showMessage(f"Заглушка типа «{self.title}»")
        logger.info(
            f"Открыта заглушка типа договора: {self.contract_type} ({self.title})"
        )

    def message(self) -> str:
        """Текст заголовка окна (для тестов и внешних проверок)."""
        return self.heading.text()

    def close_button(self):
        """Кнопка «Закрыть» (для тестов)."""
        return self.btn_close


__all__ = ["PlaceholderWindow"]
