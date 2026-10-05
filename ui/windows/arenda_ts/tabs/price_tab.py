#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Стоимость» окна типа «Разовая аренда» (ЭТАП 3.1.D.B.2).

Арендная плата (раздел 4.1 бланка) считается от одной введённой величины —
суммы без НДС. Остальное вкладка считает сама:

    ставка 0%:  sum_vat = 0, sum_total = sum_wo_vat
    ставка > 0: sum_vat = sum_wo_vat × ставка/100,
                sum_total = sum_wo_vat + sum_vat

Сумма прописью считается через core.num_to_words.amount_to_words — тем же
модулем, что и в генераторах договоров; образец — вкладка стоимости Формики
(ui/windows/formika/tabs/price_tab.py).

Ставка НДС на этой вкладке выбирается всегда, включая «0%»: вид Арендатора
(ООО / ИП с НДС / ИП без НДС) выбирается на вкладке «Арендатор», и сборка
данных сама решает по нему, какие суммы попадут в бланк
(ui/windows/arenda_ts/data.py::_build_price).

Ключи get_data() — sum_wo_vat, sum_vat, sum_total, vat_rate, vat_rate_num,
special_conditions — читает ui/windows/arenda_ts/data.py::_build_price.
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QDoubleSpinBox, QLineEdit, QScrollArea, QVBoxLayout, QWidget,
)

from core.num_to_words import amount_to_words
from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableTextEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.arenda_ts.tabs.price_tab")

#: Ставки НДС в выпадающем списке; по умолчанию — первая.
VAT_RATES = ("22%", "20%", "10%", "0%")
DEFAULT_VAT_RATE = "22%"

#: Ставка, при которой НДС не начисляется: sum_total = sum_wo_vat.
ZERO_VAT_RATE = "0%"

#: Границы вводимой суммы (как на вкладке условий договора).
MAX_AMOUNT = 100_000_000

#: Обозначение рублей у сумм — как в бланке.
AMOUNT_SUFFIX = " ₽"


class NoWheelComboBox(QComboBox):
    """Не переключает ставку НДС при прокрутке формы колёсиком."""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """Не меняет сумму при прокрутке формы колёсиком."""

    def wheelEvent(self, event):
        event.ignore()


def _rate_text(value: Any) -> str:
    """
    Ставка НДС строкой без знака процента: 22 и 22.0 → «22», «10%» → «10».

    Распознавание отдаёт ставку и числом (vat_rate_num), и строкой: число
    22.0 нельзя превращать в «22.0%» — такого пункта в списке нет.
    """
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        number = float(value)
        # 22.0 → «22», 9.5 → «9.5»: целые ставки пишутся без дробной части.
        return f"{number:.0f}" if number.is_integer() else f"{number:g}"
    return str(value).strip().replace("%", "").strip()


class PriceTab(TabMixin, QWidget):
    """Арендная плата: сумма без НДС, НДС по ставке, итог и сумма прописью."""

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
            placeholder="Вставьте текст со стоимостью аренды (сумма, НДС, условия)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Арендная плата» ──
        price_group, price_layout = theme.section_box("Арендная плата")

        self.vat_rate = NoWheelComboBox()
        self.vat_rate.addItems(list(VAT_RATES))
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        self.vat_rate.setToolTip(
            "Ставка НДС: при «0%» налог не начисляется, итог равен сумме без НДС"
        )
        self.vat_rate.currentIndexChanged.connect(self._recalculate)
        price_layout.addRow("Ставка НДС", self.vat_rate)

        # Сумма без НДС — единственное поле, которое вводит пользователь.
        self.sum_wo_vat = NoWheelDoubleSpinBox()
        self.sum_wo_vat.setRange(0, MAX_AMOUNT)
        self.sum_wo_vat.setDecimals(2)
        self.sum_wo_vat.setSuffix(AMOUNT_SUFFIX)
        self.sum_wo_vat.setGroupSeparatorShown(True)
        self.sum_wo_vat.valueChanged.connect(self._recalculate)
        price_layout.addRow("Сумма без НДС", self.sum_wo_vat)

        # ── Расчётные поля: только для чтения ──
        # Оформление берётся из темы (ui/theme.py). Локальный стиль нужен
        # потому, что Qt не пересчитывает QSS при смене свойства readOnly
        # у уже отрисованного поля.
        self.sum_vat = NoWheelDoubleSpinBox()
        self.sum_vat.setRange(0, MAX_AMOUNT)
        self.sum_vat.setDecimals(2)
        self.sum_vat.setSuffix(AMOUNT_SUFFIX)
        self.sum_vat.setGroupSeparatorShown(True)
        self.sum_vat.setReadOnly(True)
        self.sum_vat.setProperty("readonlyField", True)
        self.sum_vat.setStyleSheet(theme.readonly_field_qss())
        self.sum_vat.setButtonSymbols(QDoubleSpinBox.NoButtons)
        price_layout.addRow("НДС", self.sum_vat)

        self.sum_total = NoWheelDoubleSpinBox()
        self.sum_total.setRange(0, MAX_AMOUNT)
        self.sum_total.setDecimals(2)
        self.sum_total.setSuffix(AMOUNT_SUFFIX)
        self.sum_total.setGroupSeparatorShown(True)
        self.sum_total.setReadOnly(True)
        self.sum_total.setProperty("readonlyField", True)
        self.sum_total.setStyleSheet(theme.readonly_field_qss())
        self.sum_total.setButtonSymbols(QDoubleSpinBox.NoButtons)
        price_layout.addRow("Итого", self.sum_total)

        self.sum_total_words = QLineEdit()
        self.sum_total_words.setReadOnly(True)
        self.sum_total_words.setProperty("readonlyField", True)
        self.sum_total_words.setStyleSheet(theme.readonly_field_qss())
        price_layout.addRow("Сумма прописью", self.sum_total_words)

        layout.addWidget(price_group)

        # ── Группа «Особые условия» ──
        conditions_group, conditions_layout = theme.section_box("Особые условия")

        self.special_conditions = PasteableTextEdit(
            "Дополнительные условия договора аренды...", max_height=120
        )
        conditions_layout.addRow("Особые условия", self.special_conditions)

        layout.addWidget(conditions_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        # Ставка по умолчанию — 22%: суммы и пропись считаем сразу.
        self._recalculate()

        logger.debug("Разовая аренда PriceTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Расчёт
    # ─────────────────────────────────────────────────────────

    def vat_rate_num(self) -> float:
        """Ставка НДС числом: «22%» → 22.0, «0%» → 0.0."""
        try:
            return float(self.vat_rate.currentText().replace("%", "").strip())
        except (ValueError, TypeError):
            return 0.0
    def vat_amount_value(self) -> float:
        """
        НДС от суммы без НДС: sum_wo_vat × ставка/100, до копеек.

        При нулевой ставке налога нет — ровно ноль.
        """
        rate = self.vat_rate_num()
        if rate <= 0:
            return 0.0
        return round(float(self.sum_wo_vat.value()) * rate / 100, 2)

    def total_value(self) -> float:
        """
        Итог: сумма без НДС плюс НДС, до копеек.

        При нулевой ставке итог равен сумме без НДС — прибавлять нечего.
        """
        return round(float(self.sum_wo_vat.value()) + self.vat_amount_value(), 2)

    def _recalculate(self) -> None:
        """Пересчитывает НДС, итог и сумму прописью по текущей ставке."""
        vat_amount = self.vat_amount_value()
        total = self.total_value()

        self.sum_vat.setValue(vat_amount)
        self.sum_total.setValue(total)
        # Прописью — итог: он и печатается в договоре аренды.
        self.sum_total_words.setText(amount_to_words(total))

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Стоимость аренды (ключи — как ждёт сборка данных).

        Суммы отдаются числами: раскладывать их по варианту бланка будет
        сборщик — он знает вид Арендатора с вкладки «Арендатор».
        """
        return {
            "sum_wo_vat": float(self.sum_wo_vat.value()),
            "sum_vat": self.vat_amount_value(),
            "sum_total": self.total_value(),
            "vat_rate": self.vat_rate.currentText(),
            "vat_rate_num": self.vat_rate_num(),
            "special_conditions": self.special_conditions.toPlainText().strip(),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет стоимость аренды.

        Принимает и ключи вкладки (sum_wo_vat), и ключи ContractData
        (price_without_vat, price_input): распознавание отдаёт блок contract
        целиком. Пустые значения игнорируются, нулевая сумма пропускается —
        у промпта 0.0 означает «суммы в документе не было», и стирать ею
        введённое нельзя.
        """
        if not data:
            return

        # База арендной платы: поле вкладки, затем ключи ContractData и
        # распознанных сумм — промпт кладёт одну сумму в sum_total, а
        # sum_wo_vat оставляет нулём.
        amount = None
        for key in ("sum_wo_vat", "price_without_vat", "price_input", "sum_total"):
            if data.get(key) not in (None, ""):
                amount = data.get(key)
                break

        if amount is not None:
            try:
                amount_value = float(
                    str(amount).replace(" ", "").replace("\u00a0", "").replace(",", ".")
                )
            except (ValueError, TypeError):
                logger.warning("Разовая аренда: сумма не распознана как число")
            else:
                if amount_value > 0:
                    self.sum_wo_vat.setValue(amount_value)

        vat_rate = str(data.get("vat_rate") or "").strip()
        if not vat_rate and data.get("vat_rate_num") is not None:
            # Ставка числом: 22 и 22.0 — это «22%», а не «22.0%».
            vat_rate = _rate_text(data.get("vat_rate_num"))
        if vat_rate:
            if not vat_rate.endswith("%"):
                vat_rate = f"{vat_rate}%"
            index = self.vat_rate.findText(vat_rate)
            if index >= 0:
                self.vat_rate.setCurrentIndex(index)

        if data.get("special_conditions"):
            self.special_conditions.setPlainText(str(data["special_conditions"]))

        # Сумма и ставка могли измениться — пересчитываем сразу.
        self._recalculate()

        logger.info("Разовая аренда: данные стоимости заполнены")

    def clear(self) -> None:
        """Сбрасывает стоимость к значениям по умолчанию (0 и 22%)."""
        self.sum_wo_vat.setValue(0)
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        self.special_conditions.clear()
        self._recalculate()
        self.recognition_panel.clear()

        logger.debug("Разовая аренда: поля стоимости очищены")


__all__ = [
    "PriceTab",
    "NoWheelComboBox",
    "NoWheelDoubleSpinBox",
    "VAT_RATES",
    "DEFAULT_VAT_RATE",
    "ZERO_VAT_RATE",
    "MAX_AMOUNT",
    "AMOUNT_SUFFIX",
]
