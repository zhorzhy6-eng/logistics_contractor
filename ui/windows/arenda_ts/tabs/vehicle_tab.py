#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «ТС» окна типа «Разовая аренда» (ЭТАП 3.1.D.B.2, FIX-1).

Объект аренды — автопоезд: тягач и полуприцеп, и договор, по которому он
передан. В бланке это шапка (номер и дата договора, п. 2.5 — срок аренды)
и раздел 2.1 (марка, госномер и тип ТС тягача, марка и госномер прицепа).

ТРИ разные даты (FIX-1, БАГ 2) — это отдельные поля, автоподстановки между
ними нет:

  * «Плановый период аренды, с»  → lease_start_date   (п. 2.5 бланка);
  * «Плановый период аренды, по» → lease_end_date     (п. 2.5 бланка);
  * «Планируемая дата завершения рейса» →
    planned_completion_date (п. 3.3.2 бланка).

Даты не связаны: рейс может завершиться раньше окончания аренды (в образце
ТЛ-574 окончание аренды 28.09.2026, а плановая дата завершения — 26.09.2026)
или позже неё. Заполняются обе руками; изменение одной не трогает другие.

Отличие от вкладки «ТС» Логистикс Рус (ui/windows/logistiks_rus/tabs/
vehicle_tab.py): там четыре поля, здесь шесть — добавлены тип ТС тягача (он
обязателен, иначе валидатор не пропустит договор) и плановая дата завершения
рейса. Номер и дата договора живут на этой же вкладке: в бланке это шапка,
и отдельной вкладки «Договор» нет.

Ключи get_data() — contract_number, contract_date, lease_start_date,
lease_end_date, planned_completion_date, tractor_brand, tractor_plate,
tractor_type, trailer_brand, trailer_plate — читает
ui/windows/arenda_ts/data.py::_build_vehicle.
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import QDate, pyqtSignal
from PyQt5.QtWidgets import QDateEdit, QScrollArea, QVBoxLayout, QWidget

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableDateEdit, PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.arenda_ts.tabs.vehicle_tab")

#: Сколько лет аренды показывать по умолчанию (п. 2.5 бланка — срок аренды).
DEFAULT_LEASE_YEARS = 1

#: Через сколько дней после начала аренды планируется завершение рейса.
#: Это ПЛАНОВАЯ дата п. 3.3.2, она не равна окончанию аренды: рейс обычно
#: завершается раньше, чем истекает срок аренды.
DEFAULT_COMPLETION_DAYS = 3

#: Дата в поле ввода — «дд.мм.гггг», как в остальных вкладках проекта.
DATE_FORMAT = "dd.MM.yyyy"


class NoWheelDateEdit(QDateEdit):
    """Дата с календарём без случайного изменения колёсиком мыши."""

    def wheelEvent(self, event):
        event.ignore()


class VehicleTab(TabMixin, QWidget):
    """Договор аренды, срок аренды, тягач и прицеп."""

    # Сигнал для передачи текста в окно на распознавание
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    #: Текстовые поля вкладки: ровно те ключи, которые читает сборка данных
    #: (_build_vehicle в data.py). Даты заполняются отдельно.
    FIELDS = (
        "contract_number",
        "tractor_brand", "tractor_plate", "tractor_type",
        "trailer_brand", "trailer_plate",
    )

    #: Поля-даты вкладки: три РАЗНЫЕ даты, связи между ними нет (FIX-1).
    DATE_FIELDS = (
        "contract_date",
        "lease_start_date",
        "lease_end_date",
        "planned_completion_date",
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
            placeholder="Вставьте текст с данными договора аренды, тягача и прицепа..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Договор аренды» ──
        contract_group, contract_layout = theme.section_box("Договор аренды")

        self.contract_number = PasteableLineEdit("01/2026")
        self.contract_number.set_required(True)
        contract_layout.addRow(
            theme.required_label("Номер договора"), self.contract_number
        )

        self.contract_date = self._make_date_edit(QDate.currentDate())
        contract_layout.addRow(
            theme.required_label("Дата договора"), self.contract_date
        )

        layout.addWidget(contract_group)

        # ── Группа «Срок аренды» (п. 2.5 бланка) ──
        lease_group, lease_layout = theme.section_box("Срок аренды")

        self.lease_start_date = self._make_date_edit(QDate.currentDate())
        lease_layout.addRow(
            theme.required_label("Плановый период аренды, с"),
            self.lease_start_date,
        )

        self.lease_end_date = self._make_date_edit(
            QDate.currentDate().addYears(DEFAULT_LEASE_YEARS)
        )
        lease_layout.addRow(
            theme.required_label("Плановый период аренды, по"),
            self.lease_end_date,
        )

        # Плановая дата завершения рейса (п. 3.3.2) — НЕ то же самое, что
        # окончание аренды: рейс может завершиться раньше или позже.
        # Поле самостоятельное, из соседних дат не подставляется.
        self.planned_completion_date = self._make_date_edit(
            QDate.currentDate().addDays(DEFAULT_COMPLETION_DAYS)
        )
        lease_layout.addRow(
            theme.required_label("Планируемая дата завершения рейса"),
            self.planned_completion_date,
        )

        layout.addWidget(lease_group)

        # ── Группа «Тягач» ──
        tractor_group, tractor_layout = theme.section_box("Тягач")

        self.tractor_brand = PasteableLineEdit("DAF XF 95.430")
        self.tractor_brand.set_required(True)
        tractor_layout.addRow(
            theme.required_label("Марка, модель"), self.tractor_brand
        )

        self.tractor_plate = PasteableLineEdit("М342СА761")
        self.tractor_plate.set_required(True)
        tractor_layout.addRow(
            theme.required_label("Госномер"), self.tractor_plate
        )

        # Тип ТС обязателен: без него валидатор не пропустит договор.
        self.tractor_type = PasteableLineEdit("Седельный тягач")
        self.tractor_type.set_required(True)
        tractor_layout.addRow(
            theme.required_label("Тип ТС"), self.tractor_type
        )

        layout.addWidget(tractor_group)

        # ── Группа «Прицеп» ──
        trailer_group, trailer_layout = theme.section_box("Прицеп")

        self.trailer_brand = PasteableLineEdit("KRONE SD")
        self.trailer_brand.set_required(True)
        trailer_layout.addRow(
            theme.required_label("Марка, модель"), self.trailer_brand
        )

        self.trailer_plate = PasteableLineEdit("ВК123478")
        self.trailer_plate.set_required(True)
        trailer_layout.addRow(
            theme.required_label("Госномер"), self.trailer_plate
        )

        layout.addWidget(trailer_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Разовая аренда VehicleTab инициализирована")

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

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Договор, срок аренды, тягач и прицеп (даты — в ISO).

        Даты отдаются в ISO: их читает сборка данных, а генератор печатает
        уже в формате бланка.
        """
        data: Dict[str, Any] = {
            field: getattr(self, field).text().strip()
            for field in self.FIELDS
        }
        for field in self.DATE_FIELDS:
            data[field] = getattr(self, field).date().toString("yyyy-MM-dd")
        return data

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет договор, срок аренды, тягач и прицеп.

        Пустые значения игнорируются: частичное распознавание не должно
        сбрасывать уже введённые данные. Даты принимаются в любом формате,
        который понимает общий разбор (core.dates.parse_date).

        Три даты заполняются КАЖДАЯ СВОИМ ключом: lease_start_date,
        lease_end_date и planned_completion_date. Ни одна из них не выводится
        из других — в документе это разные даты (FIX-1).
        """
        if not data:
            return

        for field in self.FIELDS:
            value = str(data.get(field) or "").strip()
            if value:
                getattr(self, field).setText(value)

        # Номер договора промпт отдаёт и как contract_number, и как number.
        number = str(
            data.get("contract_number") or data.get("number") or ""
        ).strip()
        if number:
            self.contract_number.setText(number)

        for field in self.DATE_FIELDS:
            value = data.get(field)
            if not value:
                # Дата договора может прийти без уточнения — просто date.
                value = data.get("date") if field == "contract_date" else None
            if value:
                self._set_date(getattr(self, field), value)

        logger.info("Разовая аренда: данные ТС заполнены")

    def clear(self) -> None:
        """Очищает поля договора, тягача и прицепа, возвращает даты к норме."""
        for field in self.FIELDS:
            getattr(self, field).clear()
        today = QDate.currentDate()
        self.contract_date.setDate(today)
        self.lease_start_date.setDate(today)
        self.lease_end_date.setDate(today.addYears(DEFAULT_LEASE_YEARS))
        self.planned_completion_date.setDate(
            today.addDays(DEFAULT_COMPLETION_DAYS)
        )
        self.recognition_panel.clear()

        logger.debug("Разовая аренда: поля ТС очищены")


__all__ = [
    "VehicleTab",
    "NoWheelDateEdit",
    "DEFAULT_LEASE_YEARS",
    "DEFAULT_COMPLETION_DAYS",
    "DATE_FORMAT",
]
