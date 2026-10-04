#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Маршрут» окна типа «Формика» (ЭТАП 3.1.B.2).

Маршрут Формики — это направление, две точки (погрузка и выгрузка) и план
погрузки: дата и окно времени. Ключи get_data() читает
ui/windows/formika/data.py::_build_route: поля шапки уходят в contract,
адрес погрузки — в loadings[0], адрес выгрузки — в unloadings[0].

Плановая дата выгрузки — поле только для чтения: в бланке Формики она
печатается, но вводится не вручную (сначала рассчитывается по сроку
доставки), поэтому здесь она показывается и в данные не отдаётся.
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import QDate, QTime, pyqtSignal
from PyQt5.QtWidgets import (
    QDateEdit, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QScrollArea, QTimeEdit, QVBoxLayout, QWidget,
)

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.formika.tabs.route_tab")

#: Время окна погрузки по умолчанию — как на вкладке условий договора.
DEFAULT_TIME_FROM = QTime(8, 0)
DEFAULT_TIME_TO = QTime(20, 0)

#: На сколько дней вперёд показывать плановую дату выгрузки.
DEFAULT_UNLOADING_DAYS = 3


class PlanDateEdit(QDateEdit):
    """Дата с календарём без случайного изменения колёсиком мыши."""

    def wheelEvent(self, event):
        event.ignore()


class PlanTimeEdit(QTimeEdit):
    """Время, которое меняется только при явном редактировании."""

    def wheelEvent(self, event):
        event.ignore()


class RouteTab(TabMixin, QWidget):
    """Маршрут, адреса точек и плановые дата/время погрузки."""

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
            placeholder="Вставьте текст с маршрутом и адресами погрузки/выгрузки..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Маршрут» ──
        route_group = QGroupBox("Маршрут")
        route_layout = QFormLayout(route_group)

        self.route = PasteableLineEdit("Мурманск - Пятигорск")
        route_layout.addRow("Направление", self.route)

        self.loading_address = PasteableLineEdit("183052, г. Мурманск, пр. Кольский, д. 53")
        route_layout.addRow("Адрес погрузки", self.loading_address)

        self.unloading_address = PasteableLineEdit("г. Пятигорск, Бештаугорское шоссе 17")
        route_layout.addRow("Адрес выгрузки", self.unloading_address)

        layout.addWidget(route_group)

        # ── Группа «План погрузки» ──
        plan_group = QGroupBox("План погрузки")
        plan_layout = QFormLayout(plan_group)

        self.loading_plan_date = PlanDateEdit()
        self.loading_plan_date.setDisplayFormat("dd.MM.yyyy")
        self.loading_plan_date.setCalendarPopup(True)
        self.loading_plan_date.setDate(QDate.currentDate())
        self.loading_plan_date.setFixedWidth(150)
        self.loading_plan_date.setToolTip(
            "Выберите дату через стрелку календаря или введите вручную"
        )
        plan_layout.addRow("Плановая дата погрузки", self.loading_plan_date)

        time_layout = QHBoxLayout()

        self.loading_plan_time_from = PlanTimeEdit()
        self.loading_plan_time_from.setDisplayFormat("HH:mm")
        self.loading_plan_time_from.setTime(DEFAULT_TIME_FROM)
        self.loading_plan_time_from.setFixedWidth(90)
        time_layout.addWidget(QLabel("с"))
        time_layout.addWidget(self.loading_plan_time_from)

        self.loading_plan_time_to = PlanTimeEdit()
        self.loading_plan_time_to.setDisplayFormat("HH:mm")
        self.loading_plan_time_to.setTime(DEFAULT_TIME_TO)
        self.loading_plan_time_to.setFixedWidth(90)
        time_layout.addWidget(QLabel("по"))
        time_layout.addWidget(self.loading_plan_time_to)
        time_layout.addStretch()

        plan_layout.addRow("Время погрузки", time_layout)

        # ── Плановая дата выгрузки: только для чтения ──
        self.unloading_plan_date = PlanDateEdit()
        self.unloading_plan_date.setDisplayFormat("dd.MM.yyyy")
        self.unloading_plan_date.setCalendarPopup(True)
        self.unloading_plan_date.setDate(
            QDate.currentDate().addDays(DEFAULT_UNLOADING_DAYS)
        )
        self.unloading_plan_date.setFixedWidth(150)
        self.unloading_plan_date.setReadOnly(True)
        self.unloading_plan_date.setButtonSymbols(QDateEdit.NoButtons)
        self.unloading_plan_date.setToolTip(
            "Рассчитывается по сроку доставки и в заявке не редактируется"
        )
        self.unloading_plan_date.setProperty("readonlyField", True)
        self.unloading_plan_date.setStyleSheet(theme.readonly_field_qss())
        plan_layout.addRow("Плановая дата выгрузки", self.unloading_plan_date)

        layout.addWidget(plan_group)

        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Formika RouteTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """Маршрут и план погрузки (даты — ISO, время — «HH:mm»)."""
        return {
            "route": self.route.text().strip(),
            "loading_address": self.loading_address.text().strip(),
            "unloading_address": self.unloading_address.text().strip(),
            "loading_plan_date": self.loading_plan_date.date().toString("yyyy-MM-dd"),
            "loading_plan_time_from": self.loading_plan_time_from.time().toString("HH:mm"),
            "loading_plan_time_to": self.loading_plan_time_to.time().toString("HH:mm"),
            # Только для отображения: сборка данных это поле не читает.
            "unloading_plan_date": self.unloading_plan_date.date().toString("yyyy-MM-dd"),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет маршрут и план погрузки.

        Пустые значения игнорируются — как и на других вкладках: частичное
        распознавание не должно сбрасывать уже введённый маршрут и адреса.
        """
        if not data:
            return

        text_fields = ("route", "loading_address", "unloading_address")
        for field in text_fields:
            value = str(data.get(field) or "").strip()
            if value:
                getattr(self, field).setText(value)

        if data.get("loading_plan_date"):
            self._set_date(self.loading_plan_date, data["loading_plan_date"])

        self._set_time(self.loading_plan_time_from, data.get("loading_plan_time_from"))
        self._set_time(self.loading_plan_time_to, data.get("loading_plan_time_to"))

        # Дату выгрузки сборка данных не читает, но показать её полезно.
        unloading_plan_date = data.get("unloading_plan_date")
        if unloading_plan_date:
            self._set_date(self.unloading_plan_date, unloading_plan_date)

        logger.info("Формика: данные маршрута заполнены")

    @staticmethod
    def _set_time(time_edit: QTimeEdit, value: Any) -> None:
        """Ставит время из строки «HH:mm» (пустое или битое — не трогаем)."""
        text = str(value or "").strip()
        if not text:
            return

        parsed = QTime.fromString(text, "HH:mm")
        if not parsed.isValid():
            logger.warning("Формика: время %r не распознано", text[:5])
            return
        time_edit.setTime(parsed)

    def clear(self) -> None:
        """Очищает маршрут и возвращает план погрузки к значениям по умолчанию."""
        self.route.clear()
        self.loading_address.clear()
        self.unloading_address.clear()
        self.loading_plan_date.setDate(QDate.currentDate())
        self.loading_plan_time_from.setTime(DEFAULT_TIME_FROM)
        self.loading_plan_time_to.setTime(DEFAULT_TIME_TO)
        self.unloading_plan_date.setDate(
            QDate.currentDate().addDays(DEFAULT_UNLOADING_DAYS)
        )
        self.recognition_panel.clear()

        logger.debug("Формика: поля маршрута очищены")


__all__ = ["RouteTab", "DEFAULT_TIME_FROM", "DEFAULT_TIME_TO"]
