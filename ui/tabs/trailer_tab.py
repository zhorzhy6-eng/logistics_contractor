#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Тягач и полуприцеп».
Содержит поля для ввода данных транспортного средства перевозчика.
"""

import logging
from typing import Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLineEdit,
    QGroupBox, QSpinBox, QScrollArea,
)
from PyQt5.QtCore import pyqtSignal

from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.tabs.trailer_tab")


class _YearSpin(QSpinBox):
    """
    Год выпуска БЕЗ значения по умолчанию (ШАГ FIX-6, часть C).

    Раньше вкладка ставила тягачу 2023, а полуприцепу 2020 — эти числа
    выглядели как данные, которых оператор не вводил, и уезжали в договор.
    Теперь пустое поле показывает прочерк (`setSpecialValueText`), а в
    данные уходит 0 — «год не указан».
    """

    def __init__(self):
        super().__init__()
        self.setRange(0, 2100)
        self.setSpecialValueText("—")

    def set_year(self, year: Any) -> None:
        """Ставит год; пустое или неразбираемое значение — прочерк."""
        try:
            value = int(year or 0)
        except (TypeError, ValueError):
            value = 0
        self.setValue(value if 0 <= value <= 2100 else 0)

    def year(self) -> int:
        """Год числом; 0 — «не указан»."""
        return int(self.value())


class TrailerTab(TabMixin, QWidget):
    """
    Вкладка с данными тягача и полуприцепа.
    """

    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    def __init__(self):
        super().__init__()

        # Создаём прокручиваемую область
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)

        # ── Панель распознавания ──
        self.recognition_panel = RecognitionPanel(
            placeholder="Вставьте текст с данными тягача и полуприцепа..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Тягач» ──
        tractor_group = QGroupBox("Тягач")
        tractor_layout = QFormLayout(tractor_group)

        self.tractor_brand = PasteableLineEdit("Foton Auman")
        tractor_layout.addRow("Марка/Модель *", self.tractor_brand)

        self.tractor_plate = PasteableLineEdit("O844XY196")
        tractor_layout.addRow("Гос. номер *", self.tractor_plate)

        self.tractor_color = PasteableLineEdit("Белый")
        tractor_layout.addRow("Цвет", self.tractor_color)

        self.tractor_year = _YearSpin()
        tractor_layout.addRow("Год выпуска", self.tractor_year)

        layout.addWidget(tractor_group)

        # ── Группа «Полуприцеп» ──
        trailer_group = QGroupBox("Полуприцеп")
        trailer_layout = QFormLayout(trailer_group)

        self.trailer_brand = PasteableLineEdit("YANGMINDA")
        trailer_layout.addRow("Марка/Модель", self.trailer_brand)

        self.trailer_plate = PasteableLineEdit("71ABF18")
        trailer_layout.addRow("Гос. номер", self.trailer_plate)

        self.trailer_color = PasteableLineEdit("Серый")
        trailer_layout.addRow("Цвет", self.trailer_color)

        self.trailer_year = _YearSpin()
        trailer_layout.addRow("Год выпуска", self.trailer_year)

        layout.addWidget(trailer_group)

        layout.addStretch()

        scroll.setWidget(content_widget)

        # Основной layout
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("TrailerTab инициализирована")

    # --------------------------------------------------------
    # СБОР ДАННЫХ
    # --------------------------------------------------------
    def get_tractor_data(self) -> Dict[str, Any]:
        """
        Возвращает данные ТОЛЬКО тягача.
        Используется в MainWindow._on_save_to_db / _on_create_contract.

        Год отдаётся строкой; пустое поле — пустая строка, а НЕ «0»:
        «0» попал бы в базу (и в карточку ТС) как настоящее значение.
        """
        return {
            "brand_model": self.tractor_brand.text().strip(),
            "plate_number": self.tractor_plate.text().strip(),
            "color": self.tractor_color.text().strip(),
            "year": self._year_text(self.tractor_year),
        }

    def get_trailer_data(self) -> Dict[str, Any]:
        """
        Возвращает данные ТОЛЬКО полуприцепа.
        Используется в MainWindow._on_save_to_db / _on_create_contract.
        """
        return {
            "brand_model": self.trailer_brand.text().strip(),
            "plate_number": self.trailer_plate.text().strip(),
            "color": self.trailer_color.text().strip(),
            "year": self._year_text(self.trailer_year),
        }

    @staticmethod
    def _year_text(field: "_YearSpin") -> str:
        """Год строкой: 0 («не указан») — пустая строка."""
        year = field.year()
        return str(year) if year else ""

    def get_data(self) -> Dict[str, Any]:
        """
        Совместимость со старой версией.
        Возвращает словарь с ключами 'tractor' и 'trailer'.
        """
        return {
            "tractor": self.get_tractor_data(),
            "trailer": self.get_trailer_data(),
        }

    # --------------------------------------------------------
    # ЗАПОЛНЕНИЕ ДАННЫМИ
    # --------------------------------------------------------
    def fill_data(self, tractor: Dict[str, Any], trailer: Dict[str, Any]) -> None:
        """
        Заполняет поля данными тягача и полуприцепа.

        Пустые значения игнорируются: частичное распознавание не должно
        сбрасывать уже введённые марку, номер и год. Год не пришёл —
        поле остаётся пустым (прочерк), число не выдумывается.
        """
        if tractor:
            if tractor.get("brand_model"):
                self.tractor_brand.setText(tractor["brand_model"])
            if tractor.get("plate_number"):
                self.tractor_plate.setText(tractor["plate_number"])
            if tractor.get("color"):
                self.tractor_color.setText(tractor["color"])
            if tractor.get("year"):
                self.tractor_year.set_year(tractor["year"])

        if trailer:
            if trailer.get("brand_model"):
                self.trailer_brand.setText(trailer["brand_model"])
            if trailer.get("plate_number"):
                self.trailer_plate.setText(trailer["plate_number"])
            if trailer.get("color"):
                self.trailer_color.setText(trailer["color"])
            if trailer.get("year"):
                self.trailer_year.set_year(trailer["year"])

        logger.info("Данные тягача и полуприцепа заполнены")

    # --------------------------------------------------------
    # ОЧИСТКА
    # --------------------------------------------------------
    def clear(self) -> None:
        """
        Очищает все поля.

        Год выпуска возвращается к ПУСТОМУ значению (0 — «не указан»),
        а не к прежним «2023» / «2020»: это были не данные оператора,
        а подстановка, которая уезжала в договор (ШАГ FIX-6, часть C).
        """
        self.tractor_brand.clear()
        self.tractor_plate.clear()
        self.tractor_color.clear()
        self.tractor_year.set_year(None)
        self.trailer_brand.clear()
        self.trailer_plate.clear()
        self.trailer_color.clear()
        self.trailer_year.set_year(None)
        self.recognition_panel.clear()

        logger.debug("Поля тягача и полуприцепа очищены")