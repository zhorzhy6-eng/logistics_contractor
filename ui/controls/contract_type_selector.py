#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Селектор типа договора в шапке окна (ЭТАП 2C).

Виджет — только переключатель: показывает текущий тип и сообщает
сигналом contract_type_selected(str), что пользователь выбрал другой.
Логика переключения окон — в main.py через WindowManager.

Селектор не знает, какое окно его содержит, и не переключает окна сам:
иначе виджет пришлось бы связывать с менеджером окон, а окна типов
остались бы зависимы друг от друга.
"""

import logging
from typing import Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from core.contracts.picker_order import PICKER_ORDER

logger = logging.getLogger("ui.controls.contract_type_selector")


class ContractTypeSelector(QWidget):
    """Выпадающий список типов договоров с подписью."""

    #: Пользователь выбрал ДРУГОЙ тип договора (значение ContractType).
    contract_type_selected = pyqtSignal(str)

    def __init__(self, current_type: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("contractTypeSelector")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel("Тип договора:")
        self.combo = QComboBox()
        self.combo.setObjectName("contractTypeCombo")
        # data пункта — ключ ContractType: логика переключения не зависит
        # от того, как пункт называется в интерфейсе.
        for key, title in PICKER_ORDER:
            self.combo.addItem(title, key)
        self.set_current_type(current_type)

        layout.addWidget(label)
        layout.addWidget(self.combo)

        self.combo.currentIndexChanged.connect(self._on_changed)

    # ── Служебное ──
    def _select_by_key(self, key: str) -> bool:
        """Ставит пункт по ключу типа, не эмитя сигнал выбора."""
        for i in range(self.combo.count()):
            if self.combo.itemData(i) == key:
                was_blocked = self.combo.blockSignals(True)
                self.combo.setCurrentIndex(i)
                self.combo.blockSignals(was_blocked)
                return True
        return False

    def _on_changed(self, index: int) -> None:
        """
        Выбор пользователя: сообщаем ключ выбранного типа.

        Сравнивать с current_type() здесь нельзя: к моменту вызова слота
        список уже показывает новый пункт, и такое сравнение всегда истинно
        (сигнал не ушёл бы никогда). Повторный выбор того же пункта сигнала
        не даёт сам QComboBox — currentIndexChanged не эмитится.
        """
        key = self.combo.itemData(index)
        if not key:
            return
        logger.info("UI: выбор типа договора: %s", key)
        self.contract_type_selected.emit(str(key))

    # ── Состояние ──
    def current_type(self) -> str:
        """Ключ показанного в списке типа договора."""
        return str(self.combo.currentData() or "")

    def set_current_type(self, key: str) -> bool:
        """
        Программная установка (не эмитит сигнал).

        Нужна, когда окно показывает другой тип: main.py вызывает её после
        переключения окна через WindowManager. False — ключа нет в списке
        (тогда и выбор не меняется).
        """
        wanted = str(key or "")
        if not self._select_by_key(wanted):
            logger.warning("Тип договора %r отсутствует в списке", wanted)
            return False
        return True

    def titles(self):
        """Названия пунктов в порядке списка (для тестов и диагностики)."""
        return [self.combo.itemText(i) for i in range(self.combo.count())]

    def keys(self):
        """Ключи пунктов в порядке списка (для тестов и диагностики)."""
        return [self.combo.itemData(i) for i in range(self.combo.count())]


__all__ = ["ContractTypeSelector"]
