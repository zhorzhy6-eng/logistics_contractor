#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Стоимость» окна типа «Разовая аренда» (ЭТАП 3.1.D.B.2, FIX-1).

Арендная плата (раздел 4.1 бланка) считается от одной величины, но вводить
её можно с любой стороны — как договорился заказчик:

    режим «Без НДС»:  база = введённое число;
                      sum_vat   = база × ставка/100,
                      sum_total = база + sum_vat;
    режим «С НДС»:    итог = введённое число;
                      база = итог / (1 + ставка/100),
                      sum_vat = итог − база.

В шаблон (и в генератор) уходит одно и то же итоговое число: логика
генератора от режима ввода не зависит — он получает sum_wo_vat / sum_vat /
sum_total и печатает их. Смена режима НЕ сбрасывает сумму: текущее число
пересчитывается в базу нового режима, поэтому итог остаётся прежним.

Сумма прописью считается от ИТОГА и от режима не зависит — через
core.num_to_words.amount_to_words, тем же модулем, что и в генераторах
договоров; образец — вкладка стоимости Формики
(ui/windows/formika/tabs/price_tab.py).

Срок оплаты (п. 4.5 бланка) — отдельное поле «Срок оплаты, банковских дней»,
по умолчанию 30. Ноль — это «срок не задан»: пользователь его не вводил,
и договор не должен выглядеть заполненным; о нулевом сроке предупредит
валидатор. Отрицательного срока у банковских дней не бывает, поэтому
минимум поля — 0.

Ставка НДС на этой вкладке выбирается всегда, включая «0%»: вид Арендатора
(ООО / ИП с НДС / ИП без НДС) выбирается на вкладке «Арендатор», и сборка
данных сама решает по нему, какие суммы попадут в бланк
(ui/windows/arenda_ts/data.py::_build_price).

Ключи get_data() — sum_wo_vat, sum_vat, sum_total, vat_rate, vat_rate_num,
payment_days, special_conditions — читает ui/windows/arenda_ts/data.py::
_build_price.
"""

import logging
from typing import Any, Dict, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QDoubleSpinBox, QLineEdit, QScrollArea, QSpinBox, QVBoxLayout,
    QWidget,
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

#: Режимы ввода суммы: от базы (без НДС) и от итога (с НДС).
MODE_WITHOUT_VAT = "Без НДС"
MODE_WITH_VAT = "С НДС"
#: Значения переключателя «Считать от:» в порядке показа.
AMOUNT_MODES = (MODE_WITHOUT_VAT, MODE_WITH_VAT)
#: Режим по умолчанию — как было до FIX-1: сумма вводится без НДС.
DEFAULT_AMOUNT_MODE = MODE_WITHOUT_VAT

#: Срок оплаты по умолчанию — как в бланке аренды (п. 4.5).
DEFAULT_PAYMENT_DAYS = 30
#: Границы срока оплаты: 0 — «срок не задан» (предупредит валидатор).
MIN_PAYMENT_DAYS = 0
MAX_PAYMENT_DAYS = 365

#: Подписи поля ввода суммы — зависят от режима.
BASE_LABEL = "Сумма без НДС"
TOTAL_LABEL = "Сумма с НДС"


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


def rate_to_factor(rate: float) -> float:
    """Множитель «с НДС» по ставке: 22% → 1.22, 0% → 1.0."""
    return 1 + float(rate) / 100


def base_from_total(total: float, rate: float) -> float:
    """База из суммы с НДС: итог / (1 + ставка/100), до копеек."""
    if rate <= 0:
        return round(float(total), 2)
    return round(float(total) / rate_to_factor(rate), 2)


def total_from_base(base: float, rate: float) -> float:
    """Итог из суммы без НДС: база + база × ставка/100, до копеек."""
    return round(float(base) * rate_to_factor(rate), 2)


def vat_from_base(base: float, rate: float) -> float:
    """НДС от суммы без НДС: база × ставка/100, до копеек."""
    if rate <= 0:
        return 0.0
    return round(float(base) * rate / 100, 2)


def mode_from_amounts(
    base: Optional[float],
    total: Optional[float],
    rate: float,
) -> str:
    """
    Режим ввода, в котором были получены сохранённые суммы.

    Нужен для восстановления формы и данных из БД: вкладка помнит не только
    суммы, но и то, с какой стороны их вводили. Порядок проверок:

      1. базы нет, а итог есть — «С НДС»: единственная сумма документа не
         должна получить НДС сверху;
      2. при ненулевой ставке база и итог связаны множителем (1 + ставка/100)
         — какой из двух режимов даёт такую пару, тот и был;
      3. при нулевой ставке база равна итогу в обоих режимах: «С НДС» —
         только если базы в данных не было (её пишет лишь вкладка);
      4. на всякий случай — «Без НДС»: этот режим в проекте основной.
    """
    if base is None:
        return MODE_WITH_VAT if total is not None else DEFAULT_AMOUNT_MODE
    if total is None:
        return MODE_WITHOUT_VAT

    base_value = round(float(base), 2)
    total_value = round(float(total), 2)

    if rate > 0:
        if total_value == total_from_base(base_value, rate):
            return MODE_WITHOUT_VAT
        if base_value == base_from_total(total_value, rate):
            return MODE_WITH_VAT
        return DEFAULT_AMOUNT_MODE

    return MODE_WITH_VAT if total_value == base_value else DEFAULT_AMOUNT_MODE


class PriceTab(TabMixin, QWidget):
    """
    Арендная плата: сумма (с НДС или без), НДС по ставке, итог, прописью
    и срок оплаты.
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

        # От какой величины считать введённое число (FIX-1, БАГ 1).
        self.amount_mode = NoWheelComboBox()
        self.amount_mode.addItems(list(AMOUNT_MODES))
        self.amount_mode.setCurrentText(DEFAULT_AMOUNT_MODE)
        self.amount_mode.setToolTip(
            "Что означает сумма в поле ниже: «Без НДС» — налог начисляется "
            "сверху, «С НДС» — налог выделяется из неё"
        )
        # Какой режим уже применён к полю: нужно, чтобы первый же вызов
        # _on_mode_changed не принял текущий режим за смену пользователем.
        self._applied_mode = self.amount_mode.currentText()
        self.amount_mode.currentIndexChanged.connect(self._on_mode_changed)
        price_layout.addRow("Считать от", self.amount_mode)

        # Сумма — единственное поле, которое вводит пользователь.
        # Подпись зависит от режима: в «С НДС» это итоговая сумма.
        self.sum_wo_vat = NoWheelDoubleSpinBox()
        self.sum_wo_vat.setRange(0, MAX_AMOUNT)
        self.sum_wo_vat.setDecimals(2)
        self.sum_wo_vat.setSuffix(AMOUNT_SUFFIX)
        self.sum_wo_vat.setGroupSeparatorShown(True)
        self.sum_wo_vat.valueChanged.connect(self._recalculate)
        price_layout.addRow(self._make_sum_label(BASE_LABEL), self.sum_wo_vat)
        self._sum_edit_label = price_layout.labelForField(self.sum_wo_vat)

        self.vat_rate = NoWheelComboBox()
        self.vat_rate.addItems(list(VAT_RATES))
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        self.vat_rate.setToolTip(
            "Ставка НДС: при «0%» налог не начисляется, итог равен сумме без НДС"
        )
        self.vat_rate.currentIndexChanged.connect(self._recalculate)
        price_layout.addRow("Ставка НДС", self.vat_rate)

        # ── Расчётные поля: только для чтения ──
        # Оформление берётся из темы (ui/theme.py). Локальный стиль нужен
        # потому, что Qt не пересчитывает QSS при смене свойства readOnly
        # у уже отрисованного поля.
        self.sum_vat = self._make_readonly_amount()
        price_layout.addRow("НДС", self.sum_vat)

        self.sum_total = self._make_readonly_amount()
        price_layout.addRow("Итого", self.sum_total)

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
    def _make_sum_label(text: str):
        """Подпись поля суммы: её текст меняется вместе с режимом ввода."""
        return theme.make_label(text)

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
        """Ставка НДС числом: «22%» → 22.0, «0%» → 0.0."""
        return self._rate_num(self.vat_rate.currentText())

    @staticmethod
    def _rate_num(text: Any) -> float:
        """Ставка числом из текста: «22%» → 22.0, мусор → 0.0."""
        try:
            return float(str(text).replace("%", "").strip())
        except (ValueError, TypeError):
            return 0.0

    def amount_mode_text(self) -> str:
        """Текущий режим ввода суммы: «Без НДС» или «С НДС»."""
        return self.amount_mode.currentText()

    def calculates_from_total(self) -> bool:
        """True, если число в поле суммы — сумма С НДС (итог)."""
        return self.amount_mode_text() == MODE_WITH_VAT

    def input_amount(self) -> float:
        """Число, которое ввёл пользователь (в текущем режиме)."""
        return float(self.sum_wo_vat.value())

    def base_amount_value(self) -> float:
        """
        Сумма без НДС — база арендной платы (ключ sum_wo_vat).

        В режиме «Без НДС» это введённое число, в режиме «С НДС» — оно же
        с выделенным налогом. При нулевой ставке база равна введённому числу:
        выделять нечего.
        """
        amount = self.input_amount()
        if not self.calculates_from_total():
            return round(amount, 2)
        return base_from_total(amount, self.vat_rate_num())

    def vat_amount_value(self) -> float:
        """
        НДС: sum_wo_vat × ставка/100, до копеек.

        В режиме «С НДС» налог — это разница между введённым итогом и базой:
        так сумма налога и база в сумме дают ровно введённое число, без
        «потерянной» копейки. При нулевой ставке налога нет — ровно ноль.
        """
        rate = self.vat_rate_num()
        if rate <= 0:
            return 0.0
        if self.calculates_from_total():
            return round(self.input_amount() - self.base_amount_value(), 2)
        return vat_from_base(self.input_amount(), rate)

    def total_value(self) -> float:
        """
        Итог: сумма без НДС плюс НДС, до копеек.

        В режиме «С НДС» итог — ровно введённое число: пользователь назвал
        именно его, и округление не должно его сдвигать. При нулевой ставке
        итог равен сумме без НДС — прибавлять нечего.
        """
        rate = self.vat_rate_num()
        if rate <= 0:
            return round(self.input_amount(), 2)
        if self.calculates_from_total():
            return round(self.input_amount(), 2)
        return total_from_base(self.input_amount(), rate)

    def payment_days_value(self) -> int:
        """Срок оплаты в банковских днях (по умолчанию 30)."""
        return int(self.payment_days.value())

    def _recalculate(self) -> None:
        """Пересчитывает НДС, итог и сумму прописью по текущей ставке."""
        vat_amount = self.vat_amount_value()
        total = self.total_value()

        self.sum_vat.setValue(vat_amount)
        self.sum_total.setValue(total)
        # Прописью — итог: он и печатается в договоре аренды.
        self.sum_total_words.setText(amount_to_words(total))

    def _on_mode_changed(self) -> None:
        """
        Смена режима ввода: сумма НЕ сбрасывается, а пересчитывается.

        Число в поле остаётся ровно тем, что ввёл пользователь, — меняется
        только его смысл: «Без НДС 230 000» и «С НДС 230 000» — разные
        договоры. Пересчитываются расчётные поля ниже (НДС, итог, прописью),
        а подпись поля показывает, что теперь означает это число. Так ни одна
        введённая цифра не меняется у пользователя под руками.
        """
        mode = self.amount_mode_text()
        if mode == self._applied_mode:
            return

        self._applied_mode = mode
        self._update_sum_label()
        self._recalculate()
        logger.debug("Разовая аренда: режим ввода суммы — %s", mode)

    def _update_sum_label(self) -> None:
        """Подпись поля суммы по режиму: «Сумма без НДС» / «Сумма с НДС»."""
        if self._sum_edit_label is None:
            return
        theme.set_label_required(
            self._sum_edit_label,
            TOTAL_LABEL if self.calculates_from_total() else BASE_LABEL,
            False,
        )

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Стоимость аренды (ключи — как ждёт сборка данных).

        Суммы отдаются числами: раскладывать их по варианту бланка будет
        сборщик — он знает вид Арендатора с вкладки «Арендатор». Три суммы
        согласованы между собой при любом режиме ввода, поэтому логика
        генератора от режима не зависит.
        """
        return {
            "sum_wo_vat": self.base_amount_value(),
            "sum_vat": self.vat_amount_value(),
            "sum_total": self.total_value(),
            "vat_rate": self.vat_rate.currentText(),
            "vat_rate_num": self.vat_rate_num(),
            "payment_days": self.payment_days_value(),
            "special_conditions": self.special_conditions.toPlainText().strip(),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет стоимость аренды.

        Принимает и ключи вкладки (sum_wo_vat), и ключи ContractData
        (price_without_vat, price_with_vat, sum_total), и ключ срока оплаты
        (payment_days): распознавание отдаёт блок contract целиком. Пустые
        значения игнорируются, нулевая сумма пропускается — у промпта 0.0
        означает «суммы в документе не было», и стирать ею введённое нельзя.

        Режим ввода восстанавливается по самим суммам (mode_from_amounts):
        данные, сохранённые в режиме «С НДС», в нём же и откроются. Если пара
        «база + итог» под сохранённую ставку не подходит (ставку поменяли),
        восстанавливается режим по умолчанию, а суммы пересчитываются от базы.
        """
        if not data:
            return

        base = self._first_amount(data, self.SUM_WITHOUT_VAT_KEYS)
        total = self._first_amount(data, self.SUM_TOTAL_KEYS)

        rate = self._rate_num(
            data.get("vat_rate")
            if data.get("vat_rate") not in (None, "")
            else data.get("vat_rate_num")
        )
        mode = mode_from_amounts(base, total, rate)
        self._set_mode(mode)

        if mode == MODE_WITH_VAT:
            # В поле — итог: пользователь называл именно его.
            amount = total if total is not None else base
        else:
            amount = base if base is not None else total

        if amount is not None:
            self._set_input_amount(amount)

        vat_rate = str(data.get("vat_rate") or "").strip()
        if not vat_rate and data.get("vat_rate_num") is not None:
            # Ставка числом: 22 и 22.0 — это «22%», а не «22.0%».
            vat_rate = self._rate_text(data.get("vat_rate_num"))
        if vat_rate:
            if not vat_rate.endswith("%"):
                vat_rate = f"{vat_rate}%"
            index = self.vat_rate.findText(vat_rate)
            if index >= 0:
                self.vat_rate.setCurrentIndex(index)

        payment_days = data.get("payment_days")
        if payment_days not in (None, ""):
            try:
                self.payment_days.setValue(int(payment_days))
            except (ValueError, TypeError):
                logger.warning("Разовая аренда: срок оплаты не распознан как число")

        if data.get("special_conditions"):
            self.special_conditions.setPlainText(str(data["special_conditions"]))

        # Сумма, режим и ставка могли измениться — пересчитываем сразу.
        self._recalculate()

        logger.info("Разовая аренда: данные стоимости заполнены")

    #: Ключи суммы БЕЗ НДС: поле вкладки, затем ContractData и распознавание.
    SUM_WITHOUT_VAT_KEYS = ("sum_wo_vat", "price_without_vat")

    #: Ключи ИТОГА (суммы с НДС): промпт кладёт её в sum_total.
    SUM_TOTAL_KEYS = ("sum_total", "price_with_vat")

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

    def _set_input_amount(self, amount: float) -> None:
        """
        Кладёт сумму документа в поле ввода — ровно как есть.

        Число не пересчитывается: режим уже выставлен вызывающим кодом, и поле
        показывает именно ту сумму, которая была в документе. Сигналы
        блокируем — пересчёт (уже с новой ставкой НДС) делает вызывающий код.
        """
        self.sum_wo_vat.blockSignals(True)
        try:
            self.sum_wo_vat.setValue(float(amount))
        finally:
            self.sum_wo_vat.blockSignals(False)

    @staticmethod
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

    def clear(self) -> None:
        """Сбрасывает стоимость к значениям по умолчанию (0, 22%, 30 дней)."""
        self.sum_wo_vat.setValue(0)
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        self._set_mode(DEFAULT_AMOUNT_MODE)
        self.payment_days.setValue(DEFAULT_PAYMENT_DAYS)
        self.special_conditions.clear()
        self._recalculate()
        self.recognition_panel.clear()

        logger.debug("Разовая аренда: поля стоимости очищены")

    def _set_mode(self, mode: str) -> None:
        """Ставит режим ввода суммы, не пересчитывая уже очищенное поле."""
        index = self.amount_mode.findText(mode)
        if index >= 0 and index != self.amount_mode.currentIndex():
            self.amount_mode.blockSignals(True)
            try:
                self.amount_mode.setCurrentIndex(index)
            finally:
                self.amount_mode.blockSignals(False)
            self._applied_mode = mode
            self._update_sum_label()


__all__ = [
    "PriceTab",
    "NoWheelComboBox",
    "NoWheelDoubleSpinBox",
    "NoWheelSpinBox",
    "VAT_RATES",
    "DEFAULT_VAT_RATE",
    "ZERO_VAT_RATE",
    "MAX_AMOUNT",
    "AMOUNT_SUFFIX",
    "MODE_WITHOUT_VAT",
    "MODE_WITH_VAT",
    "AMOUNT_MODES",
    "DEFAULT_AMOUNT_MODE",
    "DEFAULT_PAYMENT_DAYS",
    "MIN_PAYMENT_DAYS",
    "MAX_PAYMENT_DAYS",
    "BASE_LABEL",
    "TOTAL_LABEL",
    "rate_to_factor",
    "base_from_total",
    "total_from_base",
    "vat_from_base",
]
