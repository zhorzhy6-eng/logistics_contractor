#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «ТС» окна типа «Логистикс Рус» (ЭТАП 3.1.C.B.2).

Автовоз этой заявки — тягач и прицеп, по марке и госномеру на каждый.
Тип ТС, цвет и год выпуска в бланке Логистикс Рус не печатаются, поэтому
вкладка умышленно упрощена до четырёх полей: образец Формики
(ui/windows/formika/tabs/vehicle_tab.py) с пятью полями на блок здесь не
подходит.

Ключи get_data() — tractor_brand, tractor_plate, trailer_brand,
trailer_plate — читает ui/windows/logistiks_rus/data.py::_build_vehicle:
они раскладываются по ключам ContractData.tractor / .trailer
(brand_model и plate_number).
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.logistiks_rus.tabs.vehicle_tab")


class VehicleTab(TabMixin, QWidget):
    """Тягач и прицеп автовоза: марка/модель и госномер."""

    # Сигнал для передачи текста в окно на распознавание
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    #: Поля вкладки: ровно те, что читает сборка данных.
    FIELDS = (
        "tractor_brand", "tractor_plate",
        "trailer_brand", "trailer_plate",
    )

    def __init__(self):
        super().__init__()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)

        # ── Панель распознавания ──
        self.recognition_panel = RecognitionPanel(
            placeholder="Вставьте текст с данными тягача и прицепа (марка, госномер)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Тягач» ──
        tractor_group, tractor_layout = theme.section_box("Тягач")

        self.tractor_brand = PasteableLineEdit("DAF XF 95.430")
        tractor_layout.addRow("Марка, модель", self.tractor_brand)

        self.tractor_plate = PasteableLineEdit("М342СА761")
        tractor_layout.addRow("Госномер", self.tractor_plate)

        # ── Группа «Прицеп» ──
        trailer_group, trailer_layout = theme.section_box("Прицеп")

        self.trailer_brand = PasteableLineEdit("KRONE SD")
        trailer_layout.addRow("Марка, модель", self.trailer_brand)

        self.trailer_plate = PasteableLineEdit("ВК123478")
        trailer_layout.addRow("Госномер", self.trailer_plate)

        layout.addWidget(tractor_group)
        layout.addWidget(trailer_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Логистикс Рус VehicleTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """Марка и госномер тягача и прицепа."""
        return {
            field: getattr(self, field).text().strip()
            for field in self.FIELDS
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет поля тягача и прицепа.

        Пустые значения игнорируются: частичное распознавание не должно
        сбрасывать уже введённые марку и госномер.
        """
        if not data:
            return

        for field in self.FIELDS:
            value = str(data.get(field) or "").strip()
            if value:
                getattr(self, field).setText(value)

        logger.info("Логистикс Рус: данные ТС заполнены")

    def clear(self) -> None:
        """Очищает поля тягача и прицепа."""
        for field in self.FIELDS:
            getattr(self, field).clear()
        self.recognition_panel.clear()

        logger.debug("Логистикс Рус: поля ТС очищены")


__all__ = ["VehicleTab"]
