#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Стоимость» окна типа «Хавалы» (ЭТАП 3.1.E.B.2).

В бланке Хавалов ОДНА ставка на всю заявку: колонка «Ставка с НДС» объединена
на все строки таблицы, и промпт отдельно требует не выводить её из других
сумм, не округлять и не считать самому. Поэтому на вкладке одно денежное
поле — price_with_vat.

СТАВКА НДС (vat_rate) В БЛАНК НЕ ПИШЕТСЯ: ячейки для неё нет, ключ лежит
в UNMAPPED_ZAYAVKA_FIELDS генератора (core/contracts/zayavka/generator.py).
В схеме промпта поле есть («по умолчанию 22%»), и сборщик положит его
в ответ, если вкладка ставку отдала
(ui/windows/havaly/data.py, послабление 2). Здесь поле пустое по умолчанию —
«не указана», и тогда ключа в данных НЕТ: подставлять 22% за пользователя
нельзя, иначе пустая форма перестанет быть пустой (эту проверку держит
tests/test_ui_havaly_data.py). Нужна ставка в заявке — выбирается из списка
или вписывается руками.

Ставка с НДС, равная нулю, — это «ставка не указана»: ровно так её читают
промпт («если ставка не указана — 0.0»), генератор и валидатор. Поэтому
пустое поле вкладка отдаёт пустой строкой, а не нулём.

Ключи get_data() — price_with_vat, vat_rate — читает
ui/windows/havaly/data.py::_zayavka_of (принимает и vat_rate_num — ставку
числом, как её отдаёт вкладка Логистикс Рус). Имена полей СОВПАДАЮТ
с ключами схемы промпта (соглашение ЭТАПА 3.1.E.B.1).
"""

import logging
from typing import Any, Dict, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QDoubleSpinBox, QScrollArea, QVBoxLayout, QWidget,
)

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import RecognitionPanel

logger = logging.getLogger("ui.windows.havaly.tabs.price_tab")

#: Ставки НДС в выпадающем списке. Первая — «не указана»: у Хавалов ячейки
#: для ставки в бланке нет, и за пользователя она не подставляется.
VAT_RATES = ("", "22%", "20%", "10%", "0%")
DEFAULT_VAT_RATE = ""

#: Границы вводимой ставки (как на вкладке условий договора).
MAX_AMOUNT = 100_000_000

#: Подпись денежного поля.
AMOUNT_SUFFIX = " ₽"


class NoWheelComboBox(QComboBox):
    """Не меняет ставку НДС при прокрутке формы колёсиком."""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """Не меняет ставку при прокрутке формы колёсиком."""

    def wheelEvent(self, event):
        event.ignore()


class PriceTab(TabMixin, QWidget):
    """Стоимость перевозки: ставка с НДС и ставка НДС."""

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
            placeholder="Вставьте текст со стоимостью (ставка с НДС, ставка НДС)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Стоимость перевозки» ──
        price_group, price_layout = theme.section_box("Стоимость перевозки")

        self.price_with_vat = NoWheelDoubleSpinBox()
        self.price_with_vat.setRange(0, MAX_AMOUNT)
        self.price_with_vat.setDecimals(2)
        self.price_with_vat.setSuffix(AMOUNT_SUFFIX)
        self.price_with_vat.setGroupSeparatorShown(True)
        self.price_with_vat.setToolTip(
            "Ставка с НДС из бланка: одна на всю заявку, на все машины"
        )
        price_layout.addRow(
            theme.required_label("Ставка с НДС"), self.price_with_vat
        )

        self.vat_rate = NoWheelComboBox()
        self.vat_rate.setEditable(True)
        self.vat_rate.addItems(list(VAT_RATES))
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        self.vat_rate.setToolTip(
            "Ставка НДС строкой. В бланк не печатается: ячейки для неё нет. "
            "Пусто — «ставка не указана», в данные поле не попадает"
        )
        price_layout.addRow("Ставка НДС", self.vat_rate)

        layout.addWidget(price_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Хавалы PriceTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def price_with_vat_value(self) -> float:
        """Ставка с НДС числом (0.0 — «не указана»)."""
        return float(self.price_with_vat.value())

    def vat_rate_text(self) -> str:
        """
        Ставка НДС строкой: «22%», «0%», «Без НДС», пусто — «не указана».

        Знак процента доставляется только числу («20» → «20%»): ставка,
        написанная словами («Без НДС»), уходит в данные как напечатана —
        промпт требует возвращать ставку в том виде, в каком она в документе
        («без НДС» → «0%» — это дело распознавания, а не вкладки). Пустое
        поле остаётся пустым: 22% за пользователя не подставляются.
        """
        text = str(self.vat_rate.currentText() or "").strip()
        if not text:
            return ""
        return f"{text}%" if text.replace(",", ".").replace(".", "").isdigit() else text

    def get_data(self) -> Dict[str, Any]:
        """
        Ставка с НДС (числом) и ставка НДС (строкой).

        Незаполненные поля ключей не создают: пустая форма должна оставаться
        пустой — ноль в ставке означает «ставки нет», и подставлять его
        в ответ (как и 22% в ставку НДС) нельзя.
        """
        data: Dict[str, Any] = {}

        price = self.price_with_vat_value()
        if price:
            data["price_with_vat"] = price

        vat_rate = self.vat_rate_text()
        if vat_rate:
            data["vat_rate"] = vat_rate

        return data

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет стоимость.

        Принимает ключ вкладки (price_with_vat) и ключи ContractData
        (amount_with_vat, sum_total, price): распознавание отдаёт блок
        contract целиком. Пустые значения игнорируются, ноль ставку не
        сбрасывает — у промпта 0.0 значит «ставки в документе не было»
        (так же читает ноль вкладка Логистикс Рус).

        Ставка НДС принимается строкой (vat_rate) или числом (vat_rate_num);
        неизвестная строка в список не добавляется — поле просто получит
        её текстом, а в данные она уйдёт как есть.
        """
        if not data:
            return

        amount = data.get("price_with_vat")
        if amount in (None, ""):
            amount = data.get("amount_with_vat")
        if amount in (None, ""):
            amount = data.get("sum_total")
        if amount in (None, ""):
            amount = data.get("price")

        if amount not in (None, ""):
            number = self._to_amount(amount)
            if number is None:
                logger.warning("Хавалы: ставка с НДС не распознана как число")
            elif number > 0:
                self.price_with_vat.setValue(number)

        vat_rate = str(data.get("vat_rate") or "").strip()
        if not vat_rate and data.get("vat_rate_num") is not None:
            vat_rate = self._format_rate(data.get("vat_rate_num"))
        if vat_rate:
            self._apply_vat_rate(vat_rate)

        logger.info("Хавалы: данные стоимости заполнены")

    @staticmethod
    def _to_amount(value: Any) -> Optional[float]:
        """
        Ставка числом из строки документа: «1 234,56 руб.» → 1234.56.

        Разделители тысяч и запятая как десятичный разделитель — обычный
        формат распознанных сумм. Непонятное значение даёт None.
        """
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)

        text = (
            str(value).strip().lower()
            .replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
            .replace("₽", "").replace("руб.", "").replace("руб", "")
            .replace("р.", "").replace("%", "")
        )
        if not text:
            return None

        text = text.replace(",", ".")
        if text.count(".") > 1:
            head, _, tail = text.rpartition(".")
            text = head.replace(".", "") + "." + tail
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _format_rate(value: Any) -> str:
        """Ставка НДС строкой из числа: 22.0 → «22%», 0.0 → «0%»."""
        number = PriceTab._to_amount(value)
        if number is None:
            return ""
        return f"{number:.0f}%" if number.is_integer() else f"{number:g}%"

    def _apply_vat_rate(self, vat_rate: str) -> None:
        """Ставит ставку НДС: известную — из списка, незнакомую — текстом."""
        index = self.vat_rate.findText(vat_rate)
        if index >= 0:
            self.vat_rate.setCurrentIndex(index)
        else:
            self.vat_rate.setCurrentText(vat_rate)

    def clear(self) -> None:
        """Очищает ставку с НДС и возвращает ставку НДС в «не указана»."""
        self.price_with_vat.setValue(0)
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        self.recognition_panel.clear()

        logger.debug("Хавалы: поля стоимости очищены")


__all__ = [
    "PriceTab",
    "VAT_RATES",
    "DEFAULT_VAT_RATE",
    "MAX_AMOUNT",
    "AMOUNT_SUFFIX",
]
