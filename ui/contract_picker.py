#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог выбора типа договора при запуске приложения (шаг 5 инфраструктуры).

Показывается до главного окна: пользователь выбирает тип договора, и
приложение открывает соответствующее окно — «Экспедиторство» работает как
раньше (MainWindow), остальные типы открывают окно-заглушку.

Порядок пунктов задан здесь жёстко (PICKER_ORDER), а не взят из
ContractTypeRegistry.titles(): в реестре ключи сортируются по алфавиту,
а пользователю нужен порядок из задания. Ключ типа хранится в data пункта,
поэтому название можно менять, не ломая логику запуска.
"""

import logging
from typing import List, Optional, Tuple

from PyQt5.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QVBoxLayout,
)

from ui import theme

logger = logging.getLogger("ui.contract_picker")

#: Пункты выпадающего списка: (ключ ContractType, название для пользователя).
#: expediciya («Экспедиторская заявка») в список не входит: это служебная
#: заглушка шага 8, а не пользовательский тип договора.
PICKER_ORDER: Tuple[Tuple[str, str], ...] = (
    ("perevozka", "Экспедиторство"),
    ("formika", "Формика"),
    ("logistiks_rus", "Логистикс Рус"),
    ("arenda_ts", "Разовая аренда"),
    ("zayavka_excel", "Хавалы"),
)

#: Тип, выбранный в списке по умолчанию (историческое поведение программы).
DEFAULT_PICKER_TYPE = "perevozka"


def picker_items() -> List[Tuple[str, str]]:
    """Копия списка пунктов (для тестов и внешнего кода)."""
    return list(PICKER_ORDER)


def picker_title(contract_type: str) -> str:
    """Название типа для заголовков окон; неизвестный ключ возвращается как есть."""
    for key, title in PICKER_ORDER:
        if key == contract_type:
            return title
    return str(contract_type or "")


class ContractPickerDialog(QDialog):
    """Выбор типа договора: подпись «Выбрать», выпадающий список и кнопки."""

    def __init__(self, parent: Optional[QDialog] = None):
        super().__init__(parent)

        self.setWindowTitle("Выбор типа договора")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        heading = QLabel("Договоры")
        heading.setObjectName("appHeading")
        layout.addWidget(heading)

        hint = QLabel("Выберите тип договора для работы")
        hint.setObjectName("appSubheading")
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)

        self.label = QLabel("Выбрать")
        row.addWidget(self.label)

        self.combo = QComboBox()
        self.combo.setObjectName("contractTypeCombo")
        for key, title in PICKER_ORDER:
            # data пункта — ключ ContractType: логика выбора не зависит
            # от того, как пункт называется в интерфейсе.
            self.combo.addItem(title, key)
        self.combo.setCurrentIndex(self._index_of(DEFAULT_PICKER_TYPE))
        row.addWidget(self.combo, 1)

        layout.addLayout(row)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)

        self.btn_open = theme.accent_button(
            "Открыть", tooltip="Открыть выбранный тип договора"
        )
        self.btn_open.setDefault(True)
        self.btn_open.clicked.connect(self.accept)
        buttons.addWidget(self.btn_open)

        self.btn_cancel = theme.secondary_button(
            "Отмена", tooltip="Закрыть программу, ничего не открывая"
        )
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_cancel)

        layout.addLayout(buttons)

    def _index_of(self, contract_type: str) -> int:
        """Индекс пункта по ключу типа; -1, если ключа в списке нет."""
        for index in range(self.combo.count()):
            if self.combo.itemData(index) == contract_type:
                return index
        return -1

    # ── Результат выбора ──
    def selected_type(self) -> str:
        """Ключ выбранного типа договора (значение ContractType)."""
        data = self.combo.currentData()
        return "" if data is None else str(data)

    def selected_title(self) -> str:
        """Название выбранного типа — для заголовка открываемого окна."""
        return self.combo.currentText()

    def select_type(self, contract_type: str) -> bool:
        """
        Программно выбирает тип (для тестов и сценариев запуска).

        False — если такого пункта в списке нет.
        """
        index = self._index_of(contract_type)
        if index < 0:
            logger.warning(f"Тип договора {contract_type!r} отсутствует в списке")
            return False
        self.combo.setCurrentIndex(index)
        return True


__all__ = [
    "DEFAULT_PICKER_TYPE",
    "PICKER_ORDER",
    "ContractPickerDialog",
    "picker_items",
    "picker_title",
]
