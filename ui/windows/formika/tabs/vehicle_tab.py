#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «ТС» окна типа «Формика» (ЭТАП 3.1.B.2).

Тягач и полуприцеп — два блока полей. Марка/модель и цвет в бланке Формики
идут в одну ячейку, поэтому на вкладке это отдельные поля: сборка данных
(ui/windows/formika/data.py::_build_vehicle) раскладывает их по ключам
ContractData.tractor / .trailer.

Тип ТС у прицепа не спрашивается: в бланке он не печатается.
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QFormLayout, QGroupBox, QScrollArea, QVBoxLayout, QWidget,
)

from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.formika.tabs.vehicle_tab")

#: Типы тягача, встречающиеся в заявках Формики.
TRACTOR_TYPES = (
    "Седельный тягач",
    "Грузовой автомобиль",
    "Автопоезд",
)


class VehicleTab(TabMixin, QWidget):
    """Тягач и полуприцеп: марка, номер, тип, цвет и год выпуска."""

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
            placeholder="Вставьте текст с данными тягача и прицепа (марка, номер, год)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Тягач» ──
        tractor_group = QGroupBox("Тягач")
        tractor_layout = QFormLayout(tractor_group)

        self.tractor_brand = PasteableLineEdit("Foton Auman")
        tractor_layout.addRow("Марка/модель", self.tractor_brand)

        self.tractor_plate = PasteableLineEdit("O844XY196")
        tractor_layout.addRow("Госномер", self.tractor_plate)

        self.tractor_type = PasteableLineEdit("Седельный тягач")
        tractor_layout.addRow("Тип ТС", self.tractor_type)

        self.tractor_color = PasteableLineEdit("Белый")
        tractor_layout.addRow("Цвет", self.tractor_color)

        self.tractor_year = PasteableLineEdit("2023")
        self.tractor_year.setMaxLength(4)
        tractor_layout.addRow("Год выпуска", self.tractor_year)

        layout.addWidget(tractor_group)

        # ── Группа «Полуприцеп» ──
        trailer_group = QGroupBox("Полуприцеп")
        trailer_layout = QFormLayout(trailer_group)

        self.trailer_brand = PasteableLineEdit("YANGMINDA")
        trailer_layout.addRow("Марка/модель", self.trailer_brand)

        self.trailer_plate = PasteableLineEdit("71ABF18")
        trailer_layout.addRow("Госномер", self.trailer_plate)

        self.trailer_color = PasteableLineEdit("Серый")
        trailer_layout.addRow("Цвет", self.trailer_color)

        self.trailer_year = PasteableLineEdit("2020")
        self.trailer_year.setMaxLength(4)
        trailer_layout.addRow("Год выпуска", self.trailer_year)

        layout.addWidget(trailer_group)

        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Formika VehicleTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    #: Соответствие «ключ данных → имя поля вкладки».
    FIELDS = (
        "tractor_brand", "tractor_plate", "tractor_type",
        "tractor_color", "tractor_year",
        "trailer_brand", "trailer_plate", "trailer_color", "trailer_year",
    )

    def get_data(self) -> Dict[str, Any]:
        """Поля тягача и полуприцепа."""
        return {
            field: getattr(self, field).text().strip()
            for field in self.FIELDS
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет поля ТС.

        Пустые значения игнорируются: частичное распознавание не должно
        сбрасывать уже введённые марку, номер и год.
        """
        if not data:
            return

        for field in self.FIELDS:
            value = str(data.get(field) or "").strip()
            if value:
                getattr(self, field).setText(value)

        logger.info("Формика: данные ТС заполнены")

    def clear(self) -> None:
        """Очищает поля тягача и полуприцепа."""
        for field in self.FIELDS:
            getattr(self, field).clear()
        self.recognition_panel.clear()

        logger.debug("Формика: поля ТС очищены")


__all__ = ["VehicleTab", "TRACTOR_TYPES"]
