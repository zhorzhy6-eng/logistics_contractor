#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Маршрут» окна типа «Хавалы» (ЭТАП 3.1.E.B.2).

Маршрут этой заявки — четыре поля бланка: «Город погрузки», «Пункт погрузки»,
«Город доставки» (в схеме промпта — unloading_city) и «Пункт разгрузки».
Город и пункт — РАЗНЫЕ поля: промпт отдельно требует не склеивать их между
собой и не переносить адрес из пункта в город. Поэтому здесь четыре поля,
а не два: в заявках других типов город и адрес часто идут одной строкой.

План погрузки — «Планируемая дата погрузки» и «Время погрузки» (одно время,
а не окно «с … по»: в бланке колонка «Время погрузки» одна). Время хранится
QTimeEdit и отдаётся в «ЧЧ:ММ» — сборщик приводит его к тому же виду.

Ключи get_data() — loading_city, loading_point, unloading_city,
unloading_point, loading_plan_date, loading_plan_time — читает
ui/windows/havaly/data.py::_zayavka_of. Имена полей СОВПАДАЮТ с ключами
схемы промпта (соглашение ЭТАПА 3.1.E.B.1): никаких «_edit» и «_address».
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import QDate, QTime, pyqtSignal
from PyQt5.QtWidgets import QDateEdit, QTimeEdit, QScrollArea, QVBoxLayout, QWidget

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableDateEdit, PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.havaly.tabs.route_tab")

#: Дата в поле ввода — «дд.мм.гггг», как в остальных вкладках проекта.
#: В данные вкладка отдаёт ISO, формат документа собирает сборщик.
DATE_FORMAT = "dd.MM.yyyy"

#: Время погрузки по умолчанию — начало рабочего дня склада.
DEFAULT_LOADING_TIME = QTime(9, 0)


class NoWheelDateEdit(QDateEdit):
    """Дата с календарём без случайного изменения колёсиком мыши."""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelTimeEdit(QTimeEdit):
    """Время, которое меняется только при явном редактировании."""

    def wheelEvent(self, event):
        event.ignore()


class RouteTab(TabMixin, QWidget):
    """Места погрузки и разгрузки и план погрузки."""

    # Сигнал для передачи текста в окно на распознавание
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    #: Текстовые поля вкладки: ровно те ключи, которые читает сборщик.
    #: Город и пункт — разные поля (требование промпта).
    FIELDS = (
        "loading_city", "loading_point",
        "unloading_city", "unloading_point",
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
            placeholder="Вставьте текст с маршрутом (города, пункты, план погрузки)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Погрузка» ──
        loading_group, loading_layout = theme.section_box("Погрузка")

        self.loading_city = PasteableLineEdit("г. Москва")
        loading_layout.addRow("Город погрузки", self.loading_city)

        self.loading_point = PasteableLineEdit("Склад Север, ул. Складская, д. 1")
        loading_layout.addRow("Пункт погрузки", self.loading_point)

        layout.addWidget(loading_group)

        # ── Группа «Разгрузка» ──
        unloading_group, unloading_layout = theme.section_box("Разгрузка")

        self.unloading_city = PasteableLineEdit("г. Казань")
        unloading_layout.addRow("Город доставки", self.unloading_city)

        self.unloading_point = PasteableLineEdit("Площадка Юг, ул. Промышленная, д. 5")
        unloading_layout.addRow("Пункт разгрузки", self.unloading_point)

        layout.addWidget(unloading_group)

        # ── Группа «План погрузки» ──
        plan_group, plan_layout = theme.section_box("План погрузки")

        self.loading_plan_date = self._make_date_edit(QDate.currentDate())
        plan_layout.addRow("Планируемая дата погрузки", self.loading_plan_date)

        self.loading_plan_time = self._make_time_edit(DEFAULT_LOADING_TIME)
        plan_layout.addRow("Время погрузки", self.loading_plan_time)

        layout.addWidget(plan_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Хавалы RouteTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Виджеты вкладки
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _make_date_edit(value: QDate) -> PasteableDateEdit:
        """Дата с календарём и кнопкой вставки из буфера."""
        date_edit = NoWheelDateEdit()
        date_edit.setDisplayFormat(DATE_FORMAT)
        date_edit.setCalendarPopup(True)
        date_edit.setDate(value)
        return PasteableDateEdit(date_edit)

    @staticmethod
    def _make_time_edit(value: QTime) -> NoWheelTimeEdit:
        """Время в формате «HH:mm» без случайной прокрутки."""
        time_edit = NoWheelTimeEdit()
        time_edit.setDisplayFormat("HH:mm")
        time_edit.setTime(value)
        time_edit.setFixedWidth(90)
        return time_edit

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Места погрузки и разгрузки и план погрузки.

        Дата — ISO, время — «ЧЧ:ММ»: ровно то, что ждёт сборщик (он приводит
        дату к формату документа ДД.ММ.ГГГГ, а время — к «ЧЧ:ММ»).
        """
        data: Dict[str, Any] = {
            field: getattr(self, field).text().strip()
            for field in self.FIELDS
        }
        data["loading_plan_date"] = (
            self.loading_plan_date.date().toString("yyyy-MM-dd")
        )
        data["loading_plan_time"] = (
            self.loading_plan_time.time().toString("HH:mm")
        )
        return data

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет места погрузки и разгрузки и план погрузки.

        Пустые значения игнорируются: частичное распознавание не должно
        сбрасывать уже введённый маршрут. Даты принимаются в любом формате,
        который понимает общий разбор (core.dates.parse_date).
        """
        if not data:
            return

        for field in self.FIELDS:
            value = str(data.get(field) or "").strip()
            if value:
                getattr(self, field).setText(value)

        date_value = data.get("loading_plan_date")
        if date_value:
            self._set_date(self.loading_plan_date, date_value)

        time_value = str(data.get("loading_plan_time") or "").strip()
        if time_value:
            self._set_time(self.loading_plan_time, time_value)

        logger.info("Хавалы: данные маршрута заполнены")

    @staticmethod
    def _set_time(time_edit: QTimeEdit, value: str) -> None:
        """
        Ставит время из строки «ЧЧ:ММ», «ЧЧ:ММ:СС» или «Ч:ММ».

        Распознавание отдаёт время без ведущего нуля («9:00») так же часто,
        как с ним: часовой формат перебирается от строгого к свободному.
        Непонятное значение поле не трогает.
        """
        for fmt in ("HH:mm", "HH:mm:ss", "H:mm", "H:mm:ss"):
            parsed = QTime.fromString(value, fmt)
            if parsed.isValid():
                time_edit.setTime(parsed)
                return

        # Значение пришло из распознанных данных: без самого текста.
        logger.warning(
            "Хавалы: время погрузки не разобрано (длина=%s)", len(value)
        )

    def clear(self) -> None:
        """Очищает поля маршрута и возвращает план к значениям по умолчанию."""
        for field in self.FIELDS:
            getattr(self, field).clear()
        self.loading_plan_date.setDate(QDate.currentDate())
        self.loading_plan_time.setTime(DEFAULT_LOADING_TIME)
        self.recognition_panel.clear()

        logger.debug("Хавалы: поля маршрута очищены")


__all__ = [
    "RouteTab",
    "NoWheelDateEdit",
    "NoWheelTimeEdit",
    "DATE_FORMAT",
    "DEFAULT_LOADING_TIME",
]
