#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «ТС» окна типа «Хавалы» (ЭТАП 3.1.E.B.2).

Автовоз этой заявки — пять колонок бланка: «Марка Автовоза», «Цвет кабины»,
«Номер автовоза», «Марка прицепа» и «Номер Прицепа». Прицеп здесь не отдельный
объект, как в аренде, а часть автовоза: отдельных блоков "tractor" и "trailer"
в схеме промпта нет — все пять полей плоские, в блоке "zayavka".

«Наименование транспортной компании» — колонка бланка ФИКСИРОВАНА: это
перевозчик заявки, в бланке он напечатан (ООО "ТЕХНОЛОГИСТИКА"), и промпт
прямо запрещает переносить эту колонку в ответ — перевозчика называет поле
carrier_name блока "zayavka" (вкладка «Заявка»). Поэтому здесь только
справочная строка: видно, чей это автовоз, но поле не редактируется и в
get_data() его нет — лишнего ключа схема не знает.

Ключи get_data() — tractor_brand, tractor_color, tractor_plate,
trailer_brand, trailer_plate — читает ui/windows/havaly/data.py::_zayavka_of
(и _tractor_block / _trailer_block для формы ContractData). Имена полей
СОВПАДАЮТ с ключами схемы промпта (соглашение ЭТАПА 3.1.E.B.1).
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from core.contracts.zayavka.generator import CARRIER_NAME
from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableLineEdit, RecognitionPanel
from ui.windows.havaly.tabs.customer_tab import fixed_line_edit

logger = logging.getLogger("ui.windows.havaly.tabs.vehicle_tab")


class VehicleTab(TabMixin, QWidget):
    """Автовоз и прицеп: марка, цвет кабины и номера."""

    # Сигнал для передачи текста в окно на распознавание
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    #: Поля вкладки: ровно те ключи, которые читает сборщик.
    FIELDS = (
        "tractor_brand", "tractor_color", "tractor_plate",
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
            placeholder="Вставьте текст с данными автовоза и прицепа..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Транспортная компания» (фиксирована) ──
        company_group, company_layout = theme.section_box(
            "Транспортная компания (фиксирована)"
        )

        self.transport_company = fixed_line_edit(
            CARRIER_NAME,
            "Перевозчик заявки: напечатан в бланке, не редактируется",
        )
        company_layout.addRow("Наименование", self.transport_company)

        layout.addWidget(company_group)

        # ── Группа «Автовоз» ──
        tractor_group, tractor_layout = theme.section_box("Автовоз")

        self.tractor_brand = PasteableLineEdit("КАМАЗ-5490")
        tractor_layout.addRow("Марка автовоза", self.tractor_brand)

        self.tractor_color = PasteableLineEdit("Белый")
        tractor_layout.addRow("Цвет кабины", self.tractor_color)

        self.tractor_plate = PasteableLineEdit("А001АА77")
        tractor_layout.addRow("Номер автовоза", self.tractor_plate)

        layout.addWidget(tractor_group)

        # ── Группа «Прицеп» ──
        trailer_group, trailer_layout = theme.section_box("Прицеп")

        self.trailer_brand = PasteableLineEdit("Тонар-9741")
        trailer_layout.addRow("Марка прицепа", self.trailer_brand)

        self.trailer_plate = PasteableLineEdit("БВ002277")
        trailer_layout.addRow("Номер прицепа", self.trailer_plate)

        layout.addWidget(trailer_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Хавалы VehicleTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Автовоз и прицеп: пять полей.

        «Наименование транспортной компании» в данные не отдаётся: в схеме
        промпта такого поля нет, перевозчика называет carrier_name вкладки
        «Заявка». Строка на вкладке — только для справки.
        """
        return {
            field: getattr(self, field).text().strip()
            for field in self.FIELDS
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет автовоз и прицеп.

        Пустые значения игнорируются: частичное распознавание не должно
        сбрасывать уже введённые марку, цвет и номера. Название транспортной
        компании из данных не принимается — оно фиксировано.
        """
        if not data:
            return

        for field in self.FIELDS:
            value = str(data.get(field) or "").strip()
            if value:
                getattr(self, field).setText(value)

        logger.info("Хавалы: данные ТС заполнены")

    def clear(self) -> None:
        """Очищает поля автовоза и прицепа, компанию возвращает к константе."""
        for field in self.FIELDS:
            getattr(self, field).clear()
        self.transport_company.setText(CARRIER_NAME)
        self.recognition_panel.clear()

        logger.debug("Хавалы: поля ТС очищены")


__all__ = ["VehicleTab"]
