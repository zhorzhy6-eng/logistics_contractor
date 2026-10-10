#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Стоимость» окна типа «Логистикс Рус».

Особенность заявки: вариант расчёта зависит от экспедитора. У ООО в бланке
три суммы — без НДС, НДС по ставке и итого; у ИП стоимость всегда без НДС и
ставка не применяется (вариант бланка выбирает генератор по
contract.carrier_type).

Суммы считаются по ЕДИНОМУ правилу НДС — «НДС В ТОМ ЧИСЛЕ», тому же, что в
Аренде, Формике и договоре-заявке на перевозку (core/vat.py::compute_vat):

    sum_total  = введённое число  (ИТОГ — то, что видит заказчик);
    sum_wo_vat = sum_total / (1 + ставка/100);
    sum_vat    = sum_total − sum_wo_vat.

Оператор вводит ОДНУ сумму — итог; база без НДС и налог выводятся из неё.
Налог считается ВЫЧИТАНИЕМ из итога, а не умножением базы на ставку: только
так `sum_wo_vat + sum_vat` даёт ровно `sum_total`.

Раньше на вкладке был переключатель «Считать от» (Без НДС / С НДС) и своя
копия формул: в режиме «Без НДС» налог считался СВЕРХУ и прибавлялся к
введённому числу. Локальные формулы убраны — вкладка считает тем же ядром,
что и остальные типы (сведение к core/vat.py).

Поля:
  * «Стоимость (итог)»   — единственное поле ввода (у ИП это та же сумма
                           документа: налога нет, итог равен базе);
  * «В том числе НДС»    — всегда только для чтения (расчётное);
  * «Стоимость без НДС»  — всегда только для чтения (расчётное);
  * «Ставка НДС»         — пункты списка ядра (у ИП заблокировано: «0%»).

Ключи get_data() — carrier_type, entity_type, vat_rate, price_with_vat,
price_without_vat, vat_amount и исторические имена стоимости
(amount_without_vat, amount, amount_mode): их читает
ui/windows/logistiks_rus/data.py::_build_price.

Ставки — из ядра: «Без НДС» / «0%» / «5%» / «7%» / «10%» / «22%», по
умолчанию «22%». У «Без НДС» множитель единичный: в бланке ООО (там есть
плейсхолдер ставки) печатается «0%» и нулевой налог, а в ИП-бланке оговорка
«Без НДС» напечатана заранее.
"""

import logging
from typing import Any, Dict, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QDoubleSpinBox, QScrollArea, QVBoxLayout, QWidget,
)

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

logger = logging.getLogger("ui.windows.logistiks_rus.tabs.price_tab")

#: Типы экспедитора: от них зависит и бланк, и расчёт сумм.
CARRIER_TYPES = ("ООО", "ИП")

#: Тип по умолчанию — как в ui/windows/logistiks_rus/data.py.
DEFAULT_CARRIER_TYPE = "ООО"

#: Ставка ИП: в бланке одна сумма, «Без НДС».
ZERO_VAT_RATE = "0%"

#: Ставка, означающая «налогом не облагается» (пункт списка ядра).
VAT_FREE = "Без НДС"

#: Границы вводимой суммы (как на вкладке условий договора).
MAX_AMOUNT = 100_000_000

#: Обозначение рублей у сумм — как в бланке.
AMOUNT_SUFFIX = " ₽"

#: Подписи полей суммы: вводит оператор только ИТОГ.
TOTAL_LABEL = "Стоимость (итог)"
BASE_LABEL = "Стоимость без НДС"
VAT_LABEL = "В том числе НДС"


class NoWheelComboBox(QComboBox):
    """Не переключает тип и ставку НДС при прокрутке формы колёсиком."""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """Не меняет сумму при прокрутке формы колёсиком."""

    def wheelEvent(self, event):
        event.ignore()


#: Исторические имена формул Логистикса: это ТЕ ЖЕ функции ядра, не копии.
rate_to_factor = vat_factor


class PriceTab(TabMixin, QWidget):
    """
    Стоимость перевозки: итог (вводит оператор), НДС и база без НДС
    (расчётные).

    Считает «НДС в том числе»: то же правило, что в Аренде, Формике и
    перевозке (core/vat.py).
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
            placeholder="Вставьте текст со стоимостью перевозки (сумма, НДС, условия)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Стоимость перевозки» ──
        price_group, price_layout = theme.section_box("Стоимость перевозки")

        self.carrier_type = NoWheelComboBox()
        self.carrier_type.addItems(list(CARRIER_TYPES))
        self.carrier_type.setCurrentText(DEFAULT_CARRIER_TYPE)
        self.carrier_type.setToolTip(
            "Тип экспедитора: у ООО стоимость с НДС, у ИП — без НДС"
        )
        price_layout.addRow("Тип экспедитора", self.carrier_type)

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
        # Это ИТОГ: база без НДС и налог считаются из него.
        self.amount_total = NoWheelDoubleSpinBox()
        self.amount_total.setRange(0, MAX_AMOUNT)
        self.amount_total.setDecimals(2)
        self.amount_total.setSuffix(AMOUNT_SUFFIX)
        self.amount_total.setGroupSeparatorShown(True)
        self.amount_total.setToolTip(
            "Итоговая сумма заявки (то, что видит заказчик). "
            "База без НДС и налог считаются из неё."
        )
        self.amount_total.valueChanged.connect(self._recalculate)
        price_layout.addRow(f"{TOTAL_LABEL} *", self.amount_total)

        # ── Расчётные поля: только для чтения всегда ──
        # Оформление берётся из темы (ui/theme.py). Локальный стиль нужен
        # потому, что Qt не пересчитывает QSS при смене свойства readOnly
        # у уже отрисованного поля.
        self.vat_amount = self._make_readonly_amount()
        price_layout.addRow(VAT_LABEL, self.vat_amount)

        self.amount_without_vat = self._make_readonly_amount()
        price_layout.addRow(BASE_LABEL, self.amount_without_vat)

        layout.addWidget(price_group)

        # ── Группа «Особые условия» ──
        conditions_group, conditions_layout = theme.section_box("Особые условия")

        self.special_conditions = PasteableTextEdit(
            "Дополнительные условия заявки...", max_height=120
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

        # ── Пересчёт при смене типа, суммы и ставки ──
        self.carrier_type.currentIndexChanged.connect(self._on_carrier_type_changed)
        self._recalculate()

        logger.debug("Логистикс Рус PriceTab инициализирована")

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

    def _is_ip(self) -> bool:
        """ИП ли текущий экспедитор (у ИП стоимость всегда без НДС)."""
        return "ИП" in self.carrier_type.currentText()

    def entity_type(self) -> str:
        """Форма экспедитора — единый ключ типа («ООО» / «ИП»)."""
        return self.carrier_type.currentText()

    def vat_rate_num(self) -> float:
        """
        Ставка НДС числом: «22%» → 22.0.

        У ИП ставка не применяется: в списке стоит «0%», и это же значение
        ждёт сборка данных (vat_rate_num = 0.0).
        """
        if self._is_ip():
            return 0.0
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
        """Число, которое ввёл пользователь, — итог заявки."""
        return float(self.amount_total.value())

    def vat_values(self) -> Dict[str, float]:
        """
        Разбивка итога по ставке — ОДНИМ вызовом ядра (core/vat.compute_vat).

        :return: словарь `sum_total`, `sum_wo_nds`, `sum_nds`.
        """
        return compute_vat(self.input_amount(), self.vat_rate_text())

    def total_value(self) -> float:
        """Итог заявки: ровно введённое число, до копеек."""
        return self.vat_values()["sum_total"]

    def amount_without_vat_value(self) -> Optional[float]:
        """Сумма без НДС — итог с выделенным налогом (ключ price_without_vat)."""
        return self.vat_values()["sum_wo_nds"]

    def amount_with_vat_value(self) -> Optional[float]:
        """
        Сумма с НДС: у ООО — итог, у ИП — та же единственная сумма документа.

        У ИП налога нет: итог равен базе, поэтому возвращается то же число
        (в ИП-бланке плейсхолдеров суммы с НДС нет — сборка данных их туда
        не пишет).
        """
        return self.total_value()

    def vat_amount_value(self) -> float:
        """НДС из итога: sum_total − sum_wo_vat, до копеек (у ИП — ноль)."""
        return self.vat_values()["sum_nds"]

    def _on_carrier_type_changed(self, index: int) -> None:
        """
        Переключает вариант расчёта при смене типа экспедитора.

        ИП: ставка НДС заблокирована и равна «0%», налогов нет.
        ООО: ставка снова доступна; если она осталась нулевой (её выставил
        вариант ИП), возвращается ставка по умолчанию — 22%.
        """
        if self._is_ip():
            self.vat_rate.setCurrentText(ZERO_VAT_RATE)
            self.vat_rate.setEnabled(False)
        else:
            self.vat_rate.setEnabled(True)
            if self.vat_rate.currentText() == ZERO_VAT_RATE:
                self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)

        self._recalculate()
        # В лог идёт только вариант расчёта: суммы — это данные заявки.
        logger.info(
            "Логистикс Рус: тип экспедитора %s, ставка НДС %s",
            self.carrier_type.currentText(), self.vat_rate.currentText(),
        )

    def _set_amount(self, field: NoWheelDoubleSpinBox, value: float) -> None:
        """Кладёт сумму в поле, не поднимая пересчёт (его делает вызывающий)."""
        field.blockSignals(True)
        try:
            field.setValue(float(value))
        finally:
            field.blockSignals(False)

    def _recalculate(self) -> None:
        """
        Пересчитывает НДС и базу от введённого ИТОГА.

        Поле ввода не трогается вовсе — ни округлением, ни сменой значения
        под руками у пользователя.
        """
        vat = self.vat_values()
        self._set_amount(self.vat_amount, vat["sum_nds"])
        self._set_amount(self.amount_without_vat, vat["sum_wo_nds"])

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Стоимость (ключи — как ждёт сборка данных).

        Единые ключи всех типов — `entity_type`, `vat_rate`, `price_with_vat`
        (итог), `price_without_vat` (база) и `vat_amount`; исторические имена
        Логистикса (`amount_without_vat`, `amount`) отдаются теми же числами —
        их читает `ui/windows/logistiks_rus/data.py::_build_price`.

        У ИП налога нет: `vat_amount` = 0, а `price_with_vat` равен
        единственной сумме документа — в ИП-бланке она напечатана как
        «Стоимость услуг … Без НДС».
        """
        vat = self.vat_values()

        return {
            # ── Единые ключи типа (как в Экспедиторстве) ──
            "entity_type": self.entity_type(),
            "vat_rate": self.vat_rate_text(),
            "price_with_vat": vat["sum_total"],
            "price_without_vat": vat["sum_wo_nds"],
            "vat_amount": vat["sum_nds"],
            # ── Исторические имена стоимости Логистикса ──
            "carrier_type": self.carrier_type.currentText(),
            "amount_without_vat": vat["sum_wo_nds"],
            "amount": vat["sum_total"],
            "vat_rate_num": self.vat_rate_num(),
            "special_conditions": self.special_conditions.toPlainText().strip(),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет стоимость.

        Принимает и единые ключи (price_with_vat, price_without_vat,
        vat_amount), и исторические имена Логистикс Рус (amount_without_vat,
        amount, amount_mode), и ключи распознавания (sum_total, sum_wo_vat,
        price_input): распознавание отдаёт блок contract целиком. Пустые
        значения игнорируются, а нулевая сумма пропускается: у промпта 0.0
        означает «суммы в документе не было», и стирать ею введённое нельзя.

        В поле суммы встаёт ИТОГ. Если в данных итога нет, а есть только база
        без НДС (записи до перехода на «НДС в том числе»), итог
        восстанавливается прежней формулой — `база × (1 + ставка/100)`:
        пересборка старой заявки даёт прежние суммы до копейки.
        """
        if not data:
            return

        self._apply_carrier_type(data.get("carrier_type") or data.get("entity_type"))

        rate = self._rate_from_data(data)

        total = self._first_amount(data, self.SUM_TOTAL_KEYS)
        base = self._first_amount(data, self.SUM_WITHOUT_VAT_KEYS)
        if total is None and base is not None:
            total = total_from_base(base, rate if rate is not None else 0.0)

        if total is not None and total > 0:
            self._set_amount(self.amount_total, total)

        # Ставку у ИП не трогаем: там она заблокирована и всегда «0%».
        if rate is not None and not self._is_ip():
            index = self.vat_rate.findText(rate)
            if index >= 0 and index != self.vat_rate.currentIndex():
                self.vat_rate.setCurrentIndex(index)

        if data.get("special_conditions"):
            self.special_conditions.setPlainText(str(data["special_conditions"]))

        # Тип, сумма и ставка могли измениться — суммы пересчитываем сразу.
        self._recalculate()

        logger.info("Логистикс Рус: данные стоимости заполнены")

    #: Ключи ИТОГА (суммы с НДС): он и вводится оператором.
    SUM_TOTAL_KEYS = (
        "price_with_vat", "sum_total", "amount", "amount_total",
    )

    #: Ключи суммы БЕЗ НДС: поле вкладки, затем распознавание и старые записи.
    SUM_WITHOUT_VAT_KEYS = (
        "price_without_vat", "sum_wo_vat", "amount_without_vat", "price_input",
    )

    @staticmethod
    def _first_amount(data: Dict[str, Any], keys) -> Optional[float]:
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

    def _apply_carrier_type(self, value: Any) -> None:
        """Ставит тип экспедитора из данных (пустое значение не трогает)."""
        text = str(value or "").strip()
        if not text:
            return

        carrier_type = "ИП" if "ИП" in text else DEFAULT_CARRIER_TYPE
        index = self.carrier_type.findText(carrier_type)
        if index >= 0:
            self.carrier_type.setCurrentIndex(index)

    def clear(self) -> None:
        """
        Сбрасывает стоимость к значениям по умолчанию.

        По умолчанию — ООО, 22% и нулевая сумма: ровно то, что вкладка
        показывает при открытии.
        """
        self.carrier_type.setCurrentText(DEFAULT_CARRIER_TYPE)
        self.vat_rate.setEnabled(True)
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        self._set_amount(self.amount_total, 0.0)
        self.special_conditions.clear()
        self._recalculate()
        self.recognition_panel.clear()

        logger.debug("Логистикс Рус: поля стоимости очищены")


__all__ = [
    "PriceTab",
    "NoWheelComboBox",
    "NoWheelDoubleSpinBox",
    "CARRIER_TYPES",
    "DEFAULT_CARRIER_TYPE",
    "VAT_RATES",
    "DEFAULT_VAT_RATE",
    "VAT_FREE",
    "ZERO_VAT_RATE",
    "MAX_AMOUNT",
    "AMOUNT_SUFFIX",
    "TOTAL_LABEL",
    "BASE_LABEL",
    "VAT_LABEL",
    "rate_to_factor",
    "base_from_total",
    "total_from_base",
]
