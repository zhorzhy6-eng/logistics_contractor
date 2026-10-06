#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Заявка» окна типа «Хавалы» (ЭТАП 3.1.E.B.2).

Шапка бланка: над таблицей стоит строка «Дата заявки:», а в самой таблице
первая колонка — «Номер Лота». Оба поля редактируемые: дата берётся из
строки над таблицей, номер лота — из шапки (он же есть на вкладке «Груз»,
см. ниже).

СТОРОНЫ ФИКСИРОВАНЫ. Заказчик всегда «Сюрлогистик», перевозчик всегда
«ООО ТЕХНОЛОГИСТИКА» (core/prompts/havaly.py, раздел «СТОРОНЫ»): в бланке
они напечатаны, от заявки к заявке не меняются, отдельных блоков carrier
и customer в схеме нет. Поэтому на вкладке это СПРАВОЧНЫЕ строки: они
показывают, что уйдёт в бланк, но не редактируются (кнопки вставки
у таких полей выключены — буфер их не перезапишет). Константы берутся
из генератора (core/contracts/zayavka/generator.py::CUSTOMER_NAME и
CARRIER_NAME) — одна правда на генератор и вкладку; при чтении формы
генератор подставляет те же значения.

Номер лота есть и на вкладке «Груз» (в бланке это колонка строки таблицы,
и её видно на обеих вкладках). Значение одно: сборщик берёт то, что
заполнено, и вкладке «Заявка» отдаёт приоритет
(ui/windows/havaly/data.py::_SHARED_ZAYAVKA_FIELDS) — как в бланке, где
дата заявки стоит в шапке.

Ключи get_data() — date, lot_number, customer_name, carrier_name — читает
ui/windows/havaly/data.py::_zayavka_of. Имена полей СОВПАДАЮТ с ключами
схемы промпта (соглашение ЭТАПА 3.1.E.B.1): вкладка отдаёт ровно имя поля
схемы, а не своё.
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import QDate, Qt, pyqtSignal
from PyQt5.QtWidgets import QDateEdit, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from core.contracts.zayavka.generator import CARRIER_NAME, CUSTOMER_NAME
from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableDateEdit, PasteableLineEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.havaly.tabs.customer_tab")

#: Дата в поле ввода — «дд.мм.гггг», как в остальных вкладках проекта.
#: В данные вкладка отдаёт ISO, а формат документа собирает сборщик.
DATE_FORMAT = "dd.MM.yyyy"


class NoWheelDateEdit(QDateEdit):
    """Дата с календарём без случайного изменения колёсиком мыши."""

    def wheelEvent(self, event):
        event.ignore()


def fixed_line_edit(value: str, tooltip: str) -> QLineEdit:
    """
    Поле фиксированного значения: видно, но не редактируется.

    Заказчик и перевозчик этой заявки напечатаны в бланке и не меняются,
    поэтому пользователь их не вводит — он должен их видеть. Поле только
    для чтения и вне фокуса: вставка из буфера и правка с клавиатуры
    невозможны, значение всегда ровно то, что уйдёт в бланк.
    """
    field = QLineEdit(value)
    field.setReadOnly(True)
    field.setFocusPolicy(Qt.NoFocus)
    field.setProperty("readonlyField", True)
    field.setStyleSheet(theme.readonly_field_qss())
    field.setToolTip(tooltip)
    return field


class CustomerTab(TabMixin, QWidget):
    """Заявка: номер, дата и фиксированные стороны."""

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
            placeholder="Вставьте текст заявки (номер лота, дата заявки)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Заявка» ──
        zayavka_group, zayavka_layout = theme.section_box("Заявка")

        self.date = self._make_date_edit(QDate.currentDate())
        zayavka_layout.addRow(
            theme.required_label("Дата заявки"), self.date
        )

        # Номер лота: в бланке колонка «Номер Лота» (шапка таблицы). То же
        # поле есть на вкладке «Груз» — значение одно, см. docstring.
        self.lot_number = PasteableLineEdit("ЛОТ-2026-001")
        self.lot_number.set_required(True)
        zayavka_layout.addRow(
            theme.required_label("Номер лота"), self.lot_number
        )

        layout.addWidget(zayavka_group)

        # ── Группа «Стороны» (фиксированы) ──
        parties_group, parties_layout = theme.section_box("Стороны (фиксированы)")

        self.customer_name = fixed_line_edit(
            CUSTOMER_NAME,
            "Заказчик заявки: напечатан в бланке, не редактируется",
        )
        parties_layout.addRow("Заказчик", self.customer_name)

        self.carrier_name = fixed_line_edit(
            CARRIER_NAME,
            "Перевозчик заявки: напечатан в бланке, не редактируется",
        )
        parties_layout.addRow("Перевозчик", self.carrier_name)

        layout.addWidget(parties_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Хавалы CustomerTab инициализирована")

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
        Дата (ISO), номер лота и стороны.

        Стороны отдаются ВСЕГДА и только константами: в промпте они
        заполнены «даже если блока сторон в тексте нет» — пустыми они не
        бывают. Сборщик подставляет те же значения, если вкладка промолчит.
        """
        return {
            "date": self.date.date().toString("yyyy-MM-dd"),
            "lot_number": self.lot_number.text().strip(),
            "customer_name": CUSTOMER_NAME,
            "carrier_name": CARRIER_NAME,
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет дату и номер лота.

        Пустые значения игнорируются: распознавание отдаёт схему целиком
        с пустыми строками, и уже введённое не должно исчезать. Стороны не
        заполняются из данных никогда — они фиксированы (даже если модель
        вернула их другими).
        """
        if not data:
            return

        lot_number = str(data.get("lot_number") or "").strip()
        if lot_number:
            self.lot_number.setText(lot_number)

        # Дату принимаем и как date, и как date_raw: промпты типов отдают
        # её под разными именами.
        date_value = data.get("date") or data.get("date_raw")
        if date_value:
            self._set_date(self.date, date_value)

        logger.info("Хавалы: данные заявки заполнены")

    def clear(self) -> None:
        """Очищает номер лота, возвращает дату на сегодня, стороны — к норме."""
        today = QDate.currentDate()
        self.date.setDate(today)
        self.lot_number.clear()
        # Стороны фиксированы: «очистить» их нельзя, возвращаем константы.
        self.customer_name.setText(CUSTOMER_NAME)
        self.carrier_name.setText(CARRIER_NAME)
        self.recognition_panel.clear()

        logger.debug("Хавалы: поля заявки очищены")


__all__ = [
    "CustomerTab",
    "NoWheelDateEdit",
    "fixed_line_edit",
    "DATE_FORMAT",
    "CUSTOMER_NAME",
    "CARRIER_NAME",
]
