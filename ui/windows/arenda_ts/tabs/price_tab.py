#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Стоимость» окна типа «Разовая аренда».

Арендная плата (раздел 4.1 бланка) считается по ЕДИНОМУ правилу НДС —
«НДС В ТОМ ЧИСЛЕ», тому же, что и в договоре-заявке на перевозку
(core/vat.py::compute_vat):

    sum_total  = введённое число  (ИТОГ договора — то, что видит заказчик);
    sum_wo_vat = sum_total / (1 + ставка/100);
    sum_vat    = sum_total − sum_wo_vat.

Оператор вводит ОДНУ сумму — итог; база без НДС и налог выводятся из неё.
Налог считается ВЫЧИТАНИЕМ из итога, а не умножением базы на ставку: только
так `sum_wo_vat + sum_vat` даёт ровно `sum_total`, и три суммы договора
сходятся без «копейки на округлении».

Раньше на вкладке был переключатель «Считать от» (Без НДС / С НДС) и своя
копия формул: в режиме «Без НДС» налог считался СВЕРХУ и прибавлялся к
введённому числу, поэтому сумма в договоре отличалась от той, что называл
оператор. Локальные формулы убраны — вкладка считает тем же ядром, что и
остальные типы (сведение к core/vat.py).

Ставки — из ядра: «Без НДС» / «0%» / «5%» / «7%» / «10%» / «22%», по
умолчанию «22%».

Сумма прописью считается от ИТОГА — через core.num_to_words.amount_to_words,
тем же модулем, что и в генераторах договоров.

Срок оплаты (п. 4.5 бланка) — отдельное поле «Срок оплаты, банковских дней»,
по умолчанию 30. Ноль — это «срок не задан»: пользователь его не вводил,
и договор не должен выглядеть заполненным; о нулевом сроке предупредит
валидатор. Отрицательного срока у банковских дней не бывает, поэтому
минимум поля — 0.

Ключи get_data() — vat_rate, price_with_vat, price_without_vat, vat_amount
(единые для всех типов) и исторические имена сумм аренды sum_wo_vat /
sum_vat / sum_total: их читает ui/windows/arenda_ts/data.py::_build_price.
Вид Арендатора (ООО / ИП с НДС / ИП без НДС) выбирается на вкладке
«Арендатор» — на этой вкладке его нет, и вид бланка по-прежнему решает
сборка данных.
"""

import logging
from typing import Any, Dict, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QDoubleSpinBox, QLineEdit, QScrollArea, QSpinBox, QVBoxLayout,
    QWidget,
)

from core.num_to_words import amount_to_words
from core.vat import (
    DEFAULT_VAT_RATE,
    VAT_RATES,
    base_from_total,
    compute_vat,
    normalize_vat_rate,
    total_from_base,
    vat_factor,
    vat_rate_number,
)
from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableTextEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.arenda_ts.tabs.price_tab")

#: Ставка, означающая «налогом не облагается» (пункт списка ядра).
VAT_FREE = "Без НДС"

#: Ставка «ноль»: налог есть, но он нулевой (экспорт, международные рейсы).
ZERO_VAT_RATE = "0%"

#: Границы вводимой суммы (как на вкладке условий договора).
MAX_AMOUNT = 100_000_000

#: Обозначение рублей у сумм — как в бланке.
AMOUNT_SUFFIX = " ₽"

#: Подписи полей суммы: вводит оператор только ИТОГ.
TOTAL_LABEL = "Стоимость (итог)"
BASE_LABEL = "Стоимость без НДС"
VAT_LABEL = "НДС"

#: Срок оплаты по умолчанию — как в бланке аренды (п. 4.5).
DEFAULT_PAYMENT_DAYS = 30
#: Границы срока оплаты: 0 — «срок не задан» (предупредит валидатор).
MIN_PAYMENT_DAYS = 0
MAX_PAYMENT_DAYS = 365


class NoWheelComboBox(QComboBox):
    """Не переключает ставку НДС при прокрутке формы колёсиком."""

    def wheelEvent(self, event):
        event.ignore()

class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """Не меняет сумму при прокрутке формы колёсиком."""

    def wheelEvent(self, event):
        event.ignore()

class NoWheelSpinBox(QSpinBox):
    """Не меняет срок оплаты при прокрутке формы колёсиком."""

    def wheelEvent(self, event):
        event.ignore()


#: Исторические имена формул аренды: это ТЕ ЖЕ функции ядра, не копии.
#: `rate_to_factor` («22%» → 1.22) — множитель core.vat.vat_factor.
rate_to_factor = vat_factor


class PriceTab(TabMixin, QWidget):
    """
    Арендная плата: итог (вводит оператор), НДС и база без НДС (расчётные),
    сумма прописью и срок оплаты.

    Считает «НДС в том числе»: то же правило, что в перевозке, Логистиксе и
    Формике (core/vat.py).
    """

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
            "Ставка НДС: налог выделяется из введённого итога "
            "(«НДС в том числе»)"
        )
        self.vat_rate.currentIndexChanged.connect(self._recalculate)
        price_layout.addRow("Ставка НДС", self.vat_rate)

        # Сумма — единственное поле, которое вводит пользователь.
        # Это ИТОГ договора: база без НДС и налог считаются из него.
        self.sum_total = NoWheelDoubleSpinBox()
        self.sum_total.setRange(0, MAX_AMOUNT)
        self.sum_total.setDecimals(2)
        self.sum_total.setSuffix(AMOUNT_SUFFIX)
        self.sum_total.setGroupSeparatorShown(True)
        self.sum_total.setToolTip(
            "Итоговая сумма договора (то, что видит заказчик). "
            "База без НДС и налог считаются из неё."
        )
        self.sum_total.valueChanged.connect(self._recalculate)
        price_layout.addRow(f"{TOTAL_LABEL} *", self.sum_total)

        # ── Расчётные поля: только для чтения ──
        # Оформление берётся из темы (ui/theme.py). Локальный стиль нужен
        # потому, что Qt не пересчитывает QSS при смене свойства readOnly
        # у уже отрисованного поля.
        self.sum_vat = self._make_readonly_amount()
        price_layout.addRow(VAT_LABEL, self.sum_vat)

        self.sum_wo_vat = self._make_readonly_amount()
        price_layout.addRow(BASE_LABEL, self.sum_wo_vat)

        self.sum_total_words = QLineEdit()
        self.sum_total_words.setReadOnly(True)
        self.sum_total_words.setProperty("readonlyField", True)
        self.sum_total_words.setStyleSheet(theme.readonly_field_qss())
        price_layout.addRow("Сумма прописью", self.sum_total_words)

        layout.addWidget(price_group)

        # ── Группа «Порядок оплаты» ──
        payment_group, payment_layout = theme.section_box("Порядок оплаты")

        self.payment_days = NoWheelSpinBox()
        self.payment_days.setRange(MIN_PAYMENT_DAYS, MAX_PAYMENT_DAYS)
        self.payment_days.setValue(DEFAULT_PAYMENT_DAYS)
        self.payment_days.setSuffix(" дн.")
        self.payment_days.setToolTip(
            "Срок оплаты из п. 4.5 бланка: «Оплата производится в течение "
            f"{DEFAULT_PAYMENT_DAYS} (…) банковских дней». 0 — срок не задан"
        )
        payment_layout.addRow("Срок оплаты, банковских дней", self.payment_days)

        layout.addWidget(payment_group)

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
    # Виджеты вкладки
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _make_readonly_amount() -> NoWheelDoubleSpinBox:
        """Расчётная сумма: только чтение, без кнопок «вверх/вниз»."""
        field = NoWheelDoubleSpinBox()
        field.setRange(0, MAX_AMOUNT)
        field.setDecimals(2)
        field.setSuffix(AMOUNT_SUFFIX)
        field.setGroupSeparatorShown(True)
        field.setReadOnly(True)
        field.setProperty("readonlyField", True)
        field.setStyleSheet(theme.readonly_field_qss())
        field.setButtonSymbols(QDoubleSpinBox.NoButtons)
        return field

    # ─────────────────────────────────────────────────────────
    # Расчёт
    # ─────────────────────────────────────────────────────────

    def vat_rate_num(self) -> float:
        """Ставка НДС числом: «22%» → 22.0, «0%» и «Без НДС» → 0.0."""
        return vat_rate_number(self.vat_rate.currentText(), default=0.0)

    def vat_rate_text(self) -> str:
        """Выбранная ставка строкой — ровно пункт списка ядра."""
        return self.vat_rate.currentText()

    def amount_mode_text(self) -> str:
        """
        Режим ввода суммы — оставлен для совместимости: он теперь один.

        Вкладка считает «НДС в том числе», то есть оператор всегда называет
        ИТОГ; прежний переключатель «Считать от» убран вместе с локальными
        формулами.
        """
        return "С НДС"

    def calculates_from_total(self) -> bool:
        """True: число в поле суммы — всегда ИТОГ (с НДС)."""
        return True

    def input_amount(self) -> float:
        """Число, которое ввёл пользователь, — итог договора."""
        return float(self.sum_total.value())

    def vat_values(self) -> Dict[str, float]:
        """
        Разбивка итога по ставке — ОДНИМ вызовом ядра (core/vat.compute_vat).

        :return: словарь `sum_total`, `sum_wo_nds`, `sum_nds`.
        """
        return compute_vat(self.input_amount(), self.vat_rate_text())

    def total_value(self) -> float:
        """Итог договора: ровно введённое число, до копеек."""
        return self.vat_values()["sum_total"]

    def base_amount_value(self) -> float:
        """Сумма без НДС — итог с выделенным налогом (ключ sum_wo_vat)."""
        return self.vat_values()["sum_wo_nds"]

    def vat_amount_value(self) -> float:
        """НДС из итога: sum_total − sum_wo_vat, до копеек."""
        return self.vat_values()["sum_nds"]

    def payment_days_value(self) -> int:
        """Срок оплаты в банковских днях (по умолчанию 30)."""
        return int(self.payment_days.value())

    def _recalculate(self) -> None:
        """Пересчитывает НДС, базу и сумму прописью по текущей ставке."""
        vat = self.vat_values()

        self.sum_vat.setValue(vat["sum_nds"])
        self.sum_wo_vat.setValue(vat["sum_wo_nds"])
        # Прописью — итог: он и печатается в договоре аренды.
        self.sum_total_words.setText(amount_to_words(vat["sum_total"]))

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Стоимость аренды (ключи — как ждёт сборка данных).

        Единые ключи всех типов — `vat_rate`, `price_with_vat` (итог),
        `price_without_vat` (база) и `vat_amount`; исторические имена сумм
        аренды (`sum_wo_vat` / `sum_vat` / `sum_total`) отдаются теми же
        числами — их читает `ui/windows/arenda_ts/data.py::_build_price`.

        Три суммы согласованы между собой: `price_without_vat + vat_amount`
        даёт ровно `price_with_vat`. Раскладывать их по варианту бланка будет
        сборщик — он знает вид Арендатора с вкладки «Арендатор».
        """
        vat = self.vat_values()

        return {
            # ── Единые ключи типа (как в Экспедиторстве) ──
            "vat_rate": self.vat_rate_text(),
            "price_with_vat": vat["sum_total"],
            "price_without_vat": vat["sum_wo_nds"],
            "vat_amount": vat["sum_nds"],
            # ── Исторические имена сумм аренды ──
            "sum_wo_vat": vat["sum_wo_nds"],
            "sum_vat": vat["sum_nds"],
            "sum_total": vat["sum_total"],
            "vat_rate_num": self.vat_rate_num(),
            "payment_days": self.payment_days_value(),
            "special_conditions": self.special_conditions.toPlainText().strip(),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет стоимость аренды.

        Принимает и единые ключи (price_with_vat, price_without_vat,
        vat_amount), и исторические имена аренды (sum_wo_vat, sum_vat,
        sum_total), и ключ распознавания price_input: распознавание отдаёт
        блок contract целиком. Пустые значения игнорируются, нулевая сумма
        пропускается — у промпта 0.0 означает «суммы в документе не было»,
        и стирать ею введённое нельзя.

        В поле суммы встаёт ИТОГ (price_with_vat → sum_total). Если в данных
        итога нет, а есть только база без НДС (записи до перехода на «НДС в
        том числе»), итог восстанавливается прежней формулой —
        `база × (1 + ставка/100)`: пересборка старого договора даёт прежние
        суммы до копейки.
        """
        if not data:
            return

        rate = self._rate_from_data(data)

        total = self._first_amount(data, self.SUM_TOTAL_KEYS)
        base = self._first_amount(data, self.SUM_WITHOUT_VAT_KEYS)
        if total is None and base is not None:
            total = total_from_base(base, rate)

        if total is not None:
            self._set_input_amount(total)

        if rate is not None:
            self._set_vat_rate(rate)

        payment_days = data.get("payment_days")
        if payment_days not in (None, ""):
            try:
                self.payment_days.setValue(int(payment_days))
            except (ValueError, TypeError):
                logger.warning("Разовая аренда: срок оплаты не распознан как число")

        if data.get("special_conditions"):
            self.special_conditions.setPlainText(str(data["special_conditions"]))

        # Сумма и ставка могли измениться — пересчитываем сразу.
        self._recalculate()

        logger.info("Разовая аренда: данные стоимости заполнены")

    #: Ключи ИТОГА (суммы с НДС): он и вводится оператором.
    SUM_TOTAL_KEYS = ("price_with_vat", "sum_total")

    #: Ключи суммы БЕЗ НДС: поле вкладки, затем ContractData и распознавание.
    SUM_WITHOUT_VAT_KEYS = ("price_without_vat", "sum_wo_vat", "price_input")

    @classmethod
    def _first_amount(cls, data: Dict[str, Any], keys) -> Optional[float]:
        """
        Первая непустая положительная сумма из ключей.

        Ноль и пустое значение пропускаются: у промпта 0.0 означает «суммы
        в документе не было», и нулём нельзя ни заполнять вкладку, ни затирать
        введённое вручную число.
        """
        for key in keys:
            value = data.get(key)
            if value in (None, ""):
                continue
            try:
                number = float(
                    str(value).replace(" ", "").replace("\u00a0", "")
                    .replace("₽", "").replace(",", ".")
                )
            except (ValueError, TypeError):
                continue
            if number > 0:
                return number
        return None

    @classmethod
    def _rate_from_data(cls, data: Dict[str, Any]) -> Optional[str]:
        """
        Ставка НДС из данных — пунктом списка ядра, иначе None.

        Принимаются и строка («22%», «Без НДС», «не облагается»), и число
        (vat_rate_num = 22): распознавание отдаёт ставку обоими способами.
        """
        rate = normalize_vat_rate(data.get("vat_rate"))
        if rate:
            return rate

        number = data.get("vat_rate_num")
        if number in (None, ""):
            return None
        rate = normalize_vat_rate(number)
        return rate or None

    def _set_vat_rate(self, rate: str) -> None:
        """Ставит пункт списка ставок, если он там есть."""
        index = self.vat_rate.findText(rate)
        if index >= 0 and index != self.vat_rate.currentIndex():
            self.vat_rate.setCurrentIndex(index)

    def _set_input_amount(self, amount: float) -> None:
        """
        Кладёт ИТОГ в поле ввода — ровно как есть.

        Число не пересчитывается: вкладка показывает именно ту сумму, которая
        была в документе или в записи. Сигналы блокируем — пересчёт (уже с
        новой ставкой НДС) делает вызывающий код.
        """
        self.sum_total.blockSignals(True)
        try:
            self.sum_total.setValue(float(amount))
        finally:
            self.sum_total.blockSignals(False)

    def clear(self) -> None:
        """Сбрасывает стоимость к значениям по умолчанию (0, 22%, 30 дней)."""
        self.sum_total.blockSignals(True)
        try:
            self.sum_total.setValue(0)
        finally:
            self.sum_total.blockSignals(False)
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        self.payment_days.setValue(DEFAULT_PAYMENT_DAYS)
        self.special_conditions.clear()
        self._recalculate()
        self.recognition_panel.clear()

        logger.debug("Разовая аренда: поля стоимости очищены")


__all__ = [
    "PriceTab",
    "NoWheelComboBox",
    "NoWheelDoubleSpinBox",
    "NoWheelSpinBox",
    "VAT_RATES",
    "DEFAULT_VAT_RATE",
    "VAT_FREE",
    "ZERO_VAT_RATE",
    "MAX_AMOUNT",
    "AMOUNT_SUFFIX",
    "TOTAL_LABEL",
    "BASE_LABEL",
    "VAT_LABEL",
    "DEFAULT_PAYMENT_DAYS",
    "MIN_PAYMENT_DAYS",
    "MAX_PAYMENT_DAYS",
    "rate_to_factor",
    "base_from_total",
    "total_from_base",
]
