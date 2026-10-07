#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Стоимость» окна типа «Логистикс Рус» (ЭТАП 3.1.C.B.2, FIX-3).

Особенность заявки: вариант расчёта зависит от экспедитора, а не от
пользователя. У ООО в бланке три суммы — без НДС, НДС по ставке и итого;
у ИП стоимость всегда без НДС и ставка не применяется (вариант бланка
выбирает генератор по contract.carrier_type).

Сумму вводит пользователь, и вводить её можно с ЛЮБОЙ стороны — как
договорились с заказчиком (шаг FIX-3, переключатель «Считать от»):

    режим «Без НДС»:  база  = введённое число;
                      НДС   = база × ставка/100,
                      итого = база + НДС;
    режим «С НДС»:    итого = введённое число;
                      база  = итого / (1 + ставка/100),
                      НДС   = итого − база.

Поля и режимы:
  * «Сумма без НДС»   — редактируемое в режиме «Без НДС»,
                        только для чтения в режиме «С НДС»;
  * «Сумма с НДС»     — только для чтения в режиме «Без НДС»,
                        редактируемое в режиме «С НДС»;
  * «В том числе НДС» — всегда только для чтения (расчётное);
  * «Ставка НДС»      — редактируемое (у ИП заблокировано и равно «0%»).

В шаблон (и в генератор) уходит одно и то же итоговое число: логика
генератора от режима ввода не зависит — он читает price_without_vat и
считает НДС и итог сам. Меняется только интерфейс вкладки (шаг FIX-3).

У ИП суммы с НДС нет: оба поля суммы показывают единственную сумму
документа и расчётные поля остаются пустыми.

Образец — ui/windows/formika/tabs/price_tab.py: там расчёт обратный
(в бланке Формики одна сумма, уже с НДС), поэтому логика не копируется.
Расчёт режимов взят с вкладки стоимости аренды
(ui/windows/arenda_ts/tabs/price_tab.py): формулы те же, но поля другие —
у аренды одно поле ввода и подпись меняется вместе с режимом, здесь оба
поля суммы видны сразу и переключается их доступность. Импортировать
функции аренды нельзя: это модуль другого типа договора, и связь через
него сделала бы типы зависимыми друг от друга.

Ключи get_data() — carrier_type, amount_mode, amount_without_vat,
amount_with_vat, vat_amount, vat_rate, vat_rate_num, special_conditions —
читает ui/windows/logistiks_rus/data.py::_build_price.
"""

import logging
from typing import Any, Dict, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QDoubleSpinBox, QScrollArea, QVBoxLayout, QWidget,
)

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableTextEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.logistiks_rus.tabs.price_tab")

#: Типы экспедитора: от них зависит и бланк, и расчёт сумм.
CARRIER_TYPES = ("ООО", "ИП")

#: Тип по умолчанию — как в ui/windows/logistiks_rus/data.py.
DEFAULT_CARRIER_TYPE = "ООО"

#: Ставки НДС в выпадающем списке; по умолчанию — первая.
VAT_RATES = ("22%", "20%", "10%", "0%")
DEFAULT_VAT_RATE = "22%"

#: Ставка ИП: в бланке одна сумма, «Без НДС».
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
#: Режим по умолчанию — как было до FIX-3: сумма вводится без НДС.
DEFAULT_AMOUNT_MODE = MODE_WITHOUT_VAT


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

    Нужен для восстановления формы: вкладка помнит не только суммы, но и то,
    с какой стороны их вводили. Порядок проверок:

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


class NoWheelComboBox(QComboBox):
    """Не переключает тип и ставку НДС при прокрутке формы колёсиком."""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """Не меняет сумму при прокрутке формы колёсиком."""

    def wheelEvent(self, event):
        event.ignore()


class PriceTab(TabMixin, QWidget):
    """Стоимость перевозки: сумма без НДС, НДС по ставке и итог."""

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

        # От какой величины считать введённое число (FIX-3): «Без НДС» —
        # налог начисляется сверху, «С НДС» — налог выделяется из суммы.
        self.amount_mode = NoWheelComboBox()
        self.amount_mode.addItems(list(AMOUNT_MODES))
        self.amount_mode.setCurrentText(DEFAULT_AMOUNT_MODE)
        self.amount_mode.setToolTip(
            "Что означает сумма в редактируемом поле: «Без НДС» — налог "
            "начисляется сверху, «С НДС» — налог выделяется из неё"
        )
        # Какой режим уже применён к полям: нужно, чтобы первый же вызов
        # _on_mode_changed не принял текущий режим за смену пользователем.
        self._applied_mode = self.amount_mode.currentText()
        self.amount_mode.currentIndexChanged.connect(self._on_mode_changed)
        price_layout.addRow("Считать от", self.amount_mode)

        # Сумма без НДС: редактируемая в режиме «Без НДС», иначе расчётная.
        self.amount_without_vat = NoWheelDoubleSpinBox()
        self.amount_without_vat.setRange(0, MAX_AMOUNT)
        self.amount_without_vat.setDecimals(2)
        self.amount_without_vat.setSuffix(AMOUNT_SUFFIX)
        self.amount_without_vat.setGroupSeparatorShown(True)
        self.amount_without_vat.valueChanged.connect(self._recalculate)
        price_layout.addRow("Сумма без НДС", self.amount_without_vat)

        # Сумма с НДС: расчётная в режиме «Без НДС», редактируемая в «С НДС».
        self.amount_with_vat = NoWheelDoubleSpinBox()
        self.amount_with_vat.setRange(0, MAX_AMOUNT)
        self.amount_with_vat.setDecimals(2)
        self.amount_with_vat.setSuffix(AMOUNT_SUFFIX)
        self.amount_with_vat.setGroupSeparatorShown(True)
        self.amount_with_vat.valueChanged.connect(self._recalculate)
        price_layout.addRow("Сумма с НДС", self.amount_with_vat)

        self.vat_rate = NoWheelComboBox()
        self.vat_rate.addItems(list(VAT_RATES))
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        price_layout.addRow("Ставка НДС", self.vat_rate)

        # ── Расчётное поле: только для чтения всегда ──
        # Оформление берётся из темы (ui/theme.py). Локальный стиль нужен
        # потому, что Qt не пересчитывает QSS при смене свойства readOnly
        # у уже отрисованного поля.
        self.vat_amount = self._make_readonly_amount()
        price_layout.addRow("В том числе НДС", self.vat_amount)

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
        self.amount_without_vat.valueChanged.connect(self._recalculate)
        self.amount_with_vat.valueChanged.connect(self._recalculate)
        self.vat_rate.currentIndexChanged.connect(self._recalculate)
        # Режим «Без НДС» по умолчанию: сразу расставляем доступность полей
        # (в нём «Сумма с НДС» — расчётная) и считаем НДС.
        self._apply_mode_fields()
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

    @staticmethod
    def _set_field_readonly(field: NoWheelDoubleSpinBox, readonly: bool) -> None:
        """
        Делает поле суммы расчётным или редактируемым.

        Qt не пересчитывает QSS при смене свойства readOnly у уже
        отрисованного поля, поэтому стиль и свойство ставятся вместе.
        Кнопки «вверх/вниз» у расчётного поля убираются: значение в нём
        считает вкладка, а не пользователь.
        """
        field.setReadOnly(readonly)
        field.setProperty("readonlyField", readonly)
        field.setStyleSheet(theme.readonly_field_qss() if readonly else "")
        field.setButtonSymbols(
            QDoubleSpinBox.NoButtons if readonly else QDoubleSpinBox.UpDownArrows
        )

    # ─────────────────────────────────────────────────────────
    # Расчёт
    # ─────────────────────────────────────────────────────────

    def _is_ip(self) -> bool:
        """ИП ли текущий экспедитор (у ИП стоимость всегда без НДС)."""
        return "ИП" in self.carrier_type.currentText()

    def vat_rate_num(self) -> float:
        """
        Ставка НДС числом: «22%» → 22.0.

        У ИП ставка не применяется: в списке стоит «0%», и это же значение
        ждёт сборка данных (vat_rate_num = 0.0).
        """
        if self._is_ip():
            return 0.0
        try:
            return float(self.vat_rate.currentText().replace("%", "").strip())
        except (ValueError, TypeError):
            return 0.0

    def amount_mode_text(self) -> str:
        """Текущий режим ввода суммы: «Без НДС» или «С НДС»."""
        return self.amount_mode.currentText()

    def calculates_from_total(self) -> bool:
        """True, если пользователь называет сумму С НДС (итог)."""
        return self.amount_mode_text() == MODE_WITH_VAT

    def input_amount(self) -> float:
        """
        Число, которое ввёл пользователь — в текущем режиме.

        В режиме «Без НДС» это поле «Сумма без НДС», в режиме «С НДС» —
        «Сумма с НДС»: редактируемое поле одно, и именно его значение
        называет пользователь.
        """
        if self.calculates_from_total():
            return float(self.amount_with_vat.value())
        return float(self.amount_without_vat.value())

    def amount_without_vat_value(self) -> Optional[float]:
        """
        Сумма без НДС — база расчёта (ключ amount_without_vat).

        В режиме «Без НДС» это введённое число, в режиме «С НДС» — оно же
        с выделенным налогом. У ИП такой суммы в бланке нет: единственная
        сумма документа и есть база, поэтому возвращается введённое число.
        """
        amount = self.input_amount()
        if self._is_ip():
            return round(amount, 2)
        if not self.calculates_from_total():
            return round(amount, 2)
        return base_from_total(amount, self.vat_rate_num())

    def amount_with_vat_value(self) -> Optional[float]:
        """
        Сумма с НДС: база × (1 + ставка/100), до копеек.

        В режиме «С НДС» это ровно введённое число: пользователь назвал
        именно его, и округление не должно его сдвигать. При нулевой ставке
        итог равен сумме без НДС — прибавлять нечего. У ИП такой суммы в
        бланке нет — возвращается None, и поле остаётся пустым (как до
        FIX-3: в ИП-бланке одна сумма документа).
        """
        if self._is_ip():
            return None
        rate = self.vat_rate_num()
        if rate <= 0:
            return round(self.amount_without_vat_value(), 2)
        if self.calculates_from_total():
            return round(self.input_amount(), 2)
        return total_from_base(self.input_amount(), rate)

    def vat_amount_value(self) -> Optional[float]:
        """
        НДС: база × ставка/100, до копеек.

        В режиме «С НДС» налог — это разница между введённым итогом и базой:
        так налог и база в сумме дают ровно введённое число, без
        «потерянной» копейки. При нулевой ставке налога нет — ровно ноль.
        У ИП налога нет — возвращается None.
        """
        if self._is_ip():
            return None
        rate = self.vat_rate_num()
        if rate <= 0:
            return 0.0
        if self.calculates_from_total():
            return round(self.input_amount() - self.amount_without_vat_value(), 2)
        return vat_from_base(self.input_amount(), rate)

    def _on_carrier_type_changed(self, index: int) -> None:
        """
        Переключает вариант расчёта при смене типа экспедитора.

        ИП: ставка НДС заблокирована и равна «0%», суммы с НДС нет.
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

        self._apply_mode_fields()
        self._recalculate()
        # В лог идёт только вариант расчёта: суммы — это данные заявки.
        logger.info(
            "Логистикс Рус: тип экспедитора %s, ставка НДС %s",
            self.carrier_type.currentText(), self.vat_rate.currentText(),
        )

    def _on_mode_changed(self) -> None:
        """
        Смена режима ввода: суммы НЕ сбрасываются, а пересчитываются.

        Числа в полях остаются теми, что были, — меняется только то, какое
        из них называет пользователь: «Без НДС 230 000» и «С НДС 230 000» —
        разные заявки. Редактируемым становится поле нового режима, а второе
        пересчитывается от него.
        """
        mode = self.amount_mode_text()
        if mode == self._applied_mode:
            return

        self._applied_mode = mode
        self._apply_mode_fields()
        self._recalculate()
        logger.debug("Логистикс Рус: режим ввода суммы — %s", mode)

    def _apply_mode_fields(self) -> None:
        """Расставляет доступность полей суммы по текущему режиму."""
        # У ИП суммы с НДС в бланке нет: оба поля показывают одну сумму
        # документа, и вводить её можно в любом из них — «Сумма без НДС»
        # остаётся редактируемой, как было до FIX-3.
        if self._is_ip():
            self._set_field_readonly(self.amount_without_vat, False)
            self._set_field_readonly(self.amount_with_vat, False)
            return

        from_total = self.calculates_from_total()
        self._set_field_readonly(self.amount_without_vat, from_total)
        self._set_field_readonly(self.amount_with_vat, not from_total)

    def _set_amount(self, field: NoWheelDoubleSpinBox, value: float) -> None:
        """Кладёт сумму в поле, не поднимая пересчёт (его делает вызывающий)."""
        field.blockSignals(True)
        try:
            field.setValue(float(value))
        finally:
            field.blockSignals(False)

    @staticmethod
    def _clear_amount(field: NoWheelDoubleSpinBox) -> None:
        """
        Очищает поле суммы до пустого текста.

        QDoubleSpinBox пустоты как значения не знает (минимум — «0,00 ₽»),
        поэтому у поля на время убирается суффикс «₽», значение сбрасывается,
        а текст очищается: у ИП суммы с НДС в бланке нет, и висеть в поле
        чужое число не должно. Суффикс возвращается сразу — поле показывается
        пользователю в обычном виде.
        """
        suffix = field.suffix()
        field.blockSignals(True)
        try:
            field.setSuffix("")
            field.setValue(0.0)
            field.clear()
        finally:
            field.setSuffix(suffix)
            field.blockSignals(False)

    def _set_mode(self, mode: str) -> None:
        """Ставит режим ввода, не пересчитывая уже заполненные поля."""
        index = self.amount_mode.findText(mode)
        if index >= 0 and index != self.amount_mode.currentIndex():
            self.amount_mode.blockSignals(True)
            try:
                self.amount_mode.setCurrentIndex(index)
            finally:
                self.amount_mode.blockSignals(False)
            self._applied_mode = mode
        self._apply_mode_fields()

    def _recalculate(self) -> None:
        """
        Пересчитывает ту сумму, которую пользователь НЕ вводил.

        В режиме «Без НДС» введённое число — база: считаются сумма с НДС и
        налог. В режиме «С НДС» введённое число — итог: считаются база и
        налог. Поле ввода не трогается вовсе — ни округлением, ни сменой
        значения под руками у пользователя.
        """
        if self._is_ip():
            # ИП: одна сумма без НДС, налога нет — оба расчётных поля пусты
            # (QDoubleSpinBox пустоты не умеет, поэтому clear()), а поле
            # ввода остаётся как есть.
            self._clear_amount(self.vat_amount)
            self._clear_amount(self.amount_with_vat)
            return

        if self.calculates_from_total():
            self._set_amount(self.amount_without_vat,
                             self.amount_without_vat_value())
        else:
            self._set_amount(self.amount_with_vat, self.amount_with_vat_value())

        self._set_amount(self.vat_amount, self.vat_amount_value())

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Стоимость (ключи — как ждёт сборка данных).

        amount_mode — режим ввода суммы («Без НДС» / «С НДС»): по нему видно,
        с какой стороны пользователь называл сумму. Сборка данных его не
        читает (генератор считает НДС сам от price_without_vat), но ключ
        нужен, чтобы восстановить форму в том же режиме.

        У ИП суммы с НДС нет: в ключе amount_with_vat пустая строка, а
        vat_rate_num = 0 — сборка данных на пустое значение ключ не создаёт.
        """
        amount_with_vat = self.amount_with_vat_value()

        return {
            "carrier_type": self.carrier_type.currentText(),
            "amount_mode": self.amount_mode_text(),
            "amount_without_vat": float(self.amount_without_vat_value() or 0.0),
            "amount_with_vat": "" if amount_with_vat is None else amount_with_vat,
            "vat_amount": self.vat_amount_value() or 0.0,
            "vat_rate": self.vat_rate.currentText(),
            "vat_rate_num": self.vat_rate_num(),
            "special_conditions": self.special_conditions.toPlainText().strip(),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет стоимость.

        Принимает и ключи вкладки (amount_without_vat, amount_with_vat,
        amount_mode), и ключи ContractData и распознанных сумм
        (price_without_vat, price_input, sum_wo_vat, sum_total): распознавание
        отдаёт блок contract целиком. Пустые значения игнорируются, а нулевая
        сумма пропускается: у промпта 0.0 означает «суммы в документе не
        было», и стирать ею введённое нельзя.

        Режим ввода (шаг FIX-3):
          * пришёл ключ amount_mode — берём его;
          * ключа нет, но пришла сумма С НДС (amount_with_vat / price_with_vat
            / vat_amount) без базы — режим «С НДС»: единственная сумма
            документа не должна получить налог сверху;
          * иначе — режим по умолчанию, «Без НДС»: так работали до FIX-3,
            и так же читается пара «база + итог» от старой формы.
        """
        if not data:
            return

        self._apply_carrier_type(data.get("carrier_type"))

        mode = str(data.get("amount_mode") or "").strip()
        if mode not in AMOUNT_MODES:
            mode = self._mode_from_data(data)
        self._set_mode(mode)

        amount = self._document_amount(data, mode)

        if amount is not None and amount > 0:
            if self.calculates_from_total():
                self._set_amount(self.amount_with_vat, amount)
            else:
                self._set_amount(self.amount_without_vat, amount)

        # Ставку у ИП не трогаем: там она заблокирована и всегда «0%».
        if not self._is_ip():
            vat_rate = str(data.get("vat_rate") or "").strip()
            if not vat_rate and data.get("vat_rate_num") is not None:
                vat_rate = str(data.get("vat_rate_num"))
            if vat_rate:
                if not vat_rate.endswith("%"):
                    vat_rate = f"{vat_rate}%"
                index = self.vat_rate.findText(vat_rate)
                if index >= 0:
                    self.vat_rate.setCurrentIndex(index)

        if data.get("special_conditions"):
            self.special_conditions.setPlainText(str(data["special_conditions"]))

        # Тип, режим и ставка могли измениться — суммы пересчитываем сразу.
        self._recalculate()

        logger.info("Логистикс Рус: данные стоимости заполнены")

    def _mode_from_data(self, data: Dict[str, Any]) -> str:
        """
        Режим ввода по самим суммам — для данных без ключа amount_mode.

        Сумма с НДС без суммы без НДС — это «С НДС»: единственная сумма
        документа (у ИП или когда в документе указан только итог) не должна
        получить налог сверху. Во всех остальных случаях — «Без НДС»:
        этот режим в проекте основной, и именно так читаются пары
        «сумма без НДС + итог».
        """
        base = self._first_amount(data, (
            "amount_without_vat", "price_without_vat", "price_input",
            "sum_wo_vat",
        ))
        total = self._first_amount(data, (
            "amount_with_vat", "price_with_vat", "vat_amount",
        ))
        if base is None and total is not None:
            return MODE_WITH_VAT
        return DEFAULT_AMOUNT_MODE

    def _document_amount(self, data: Dict[str, Any], mode: str) -> Optional[float]:
        """
        Сумма документа — с той стороны, которую называет режим.

        В режиме «С НДС» это итог (amount_with_vat / price_with_vat), и лишь
        при его отсутствии — база. В режиме «Без НДС» — наоборот: сначала
        база (amount_without_vat / price_without_vat / price_input /
        sum_wo_vat), затем sum_total (у ИП единственная сумма документа
        лежит там). Порядок совпадает с порядком источников в сборке данных
        (ui/windows/logistiks_rus/data.py::_resolve_price_contract).
        """
        if mode == MODE_WITH_VAT:
            keys = (
                "amount_with_vat", "price_with_vat", "vat_amount",
                "amount_without_vat", "price_without_vat", "sum_total",
            )
        else:
            keys = (
                "amount_without_vat", "price_without_vat", "price_input",
                "sum_wo_vat", "sum_total",
            )
        return self._first_amount(data, keys)

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

        По умолчанию — ООО, 22% и режим «Без НДС»: ровно то, что вкладка
        показывает при открытии (шаг FIX-3).
        """
        self.carrier_type.setCurrentText(DEFAULT_CARRIER_TYPE)
        self._set_mode(DEFAULT_AMOUNT_MODE)
        self._set_amount(self.amount_without_vat, 0.0)
        self._set_amount(self.amount_with_vat, 0.0)
        self.vat_rate.setEnabled(True)
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        self.special_conditions.clear()
        self._set_amount(self.vat_amount, 0.0)
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
    "ZERO_VAT_RATE",
    "MAX_AMOUNT",
    "AMOUNT_SUFFIX",
    "MODE_WITHOUT_VAT",
    "MODE_WITH_VAT",
    "AMOUNT_MODES",
    "DEFAULT_AMOUNT_MODE",
    "rate_to_factor",
    "base_from_total",
    "total_from_base",
    "vat_from_base",
    "mode_from_amounts",
]
