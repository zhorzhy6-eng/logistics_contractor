#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Водитель» окна типа «Логистикс Рус» (ЭТАП 3.1.C.B.2).

В бланке этой заявки у водителя печатается только ФИО: паспорта,
водительского удостоверения и телефона в нём нет. Поэтому вкладка
умышленно упрощена до одного поля — вкладка водителя Формики
(ui/windows/formika/tabs/driver_tab.py) здесь служить образцом не может.

Ключ get_data() — full_name — читает
ui/windows/logistiks_rus/data.py::_build_driver (остальные ключи он
игнорирует, а валидатор типа их и не требует).
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.logistiks_rus.tabs.driver_tab")


class DriverTab(TabMixin, QWidget):
    """Водитель автовоза: только ФИО."""

    # Сигнал для передачи текста в окно на распознавание
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    def __init__(self):
        super().__init__()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)

        # ── Панель распознавания ──
        self.recognition_panel = RecognitionPanel(
            placeholder="Вставьте текст с ФИО водителя..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Водитель» ──
        driver_group, driver_layout = theme.section_box("Водитель")

        self.full_name = PasteableLineEdit("Иванов Иван Иванович")
        driver_layout.addRow("ФИО", self.full_name)

        layout.addWidget(driver_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Логистикс Рус DriverTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """ФИО водителя — единственное поле этой вкладки."""
        return {"full_name": self.full_name.text().strip()}

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет ФИО водителя.

        Пустое значение игнорируется: частичное распознавание не должно
        сбрасывать уже введённое ФИО.
        """
        if not data:
            return

        full_name = str(data.get("full_name") or "").strip()
        if full_name:
            self.full_name.setText(full_name)

        logger.info("Логистикс Рус: данные водителя заполнены")

    def clear(self) -> None:
        """Очищает ФИО водителя."""
        self.full_name.clear()
        self.recognition_panel.clear()

        logger.debug("Логистикс Рус: поля водителя очищены")


__all__ = ["DriverTab"]
