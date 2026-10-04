#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Стоимость» окна типа «Логистикс Рус» (ЭТАП 3.1.C.B.2).

Особенность заявки: вариант расчёта зависит от экспедитора, а не от
пользователя. У ООО в бланке три суммы — без НДС, НДС по ставке и итого;
у ИП стоимость всегда без НДС и ставка не применяется (вариант бланка
выбирает генератор по contract.carrier_type). Поэтому пользователь вводит
одну величину — «Сумма без НДС», а остальное вкладка считает сама:

    ООО: amount_with_vat = amount_without_vat × (1 + ставка/100)
         vat_amount     = amount_without_vat × ставка/100
    ИП:  обе суммы пусты, ставка НДС заблокирована и равна «0%»

Образец — ui/windows/formika/tabs/price_tab.py: там расчёт обратный
(в бланке Формики одна сумма, уже с НДС), поэтому логика не копируется.

Ключи get_data() — carrier_type, amount_without_vat, amount_with_vat,
vat_rate, vat_rate_num, special_conditions — читает
ui/windows/logistiks_rus/data.py::_build_price.
"""

import logging
from typing import Any, Dict, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QDoubleSpinBox, QLineEdit, QScrollArea, QVBoxLayout, QWidget,
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

#: Сумма с НДС и НДС — расчётные поля, ввод в них запрещён.
AMOUNT_SUFFIX = " ₽"


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

        # Сумма без НДС — единственное поле, которое вводит пользователь.
        self.amount_without_vat = NoWheelDoubleSpinBox()
        self.amount_without_vat.setRange(0, MAX_AMOUNT)
        self.amount_without_vat.setDecimals(2)
        self.amount_without_vat.setSuffix(AMOUNT_SUFFIX)
        self.amount_without_vat.setGroupSeparatorShown(True)
        price_layout.addRow("Сумма без НДС", self.amount_without_vat)

        self.vat_rate = NoWheelComboBox()
        self.vat_rate.addItems(list(VAT_RATES))
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        price_layout.addRow("Ставка НДС", self.vat_rate)

        # ── Расчётные поля: только для чтения ──
        # Оформление берётся из темы (ui/theme.py). Локальный стиль нужен
        # потому, что Qt не пересчитывает QSS при смене свойства readOnly
        # у уже отрисованного поля.
        self.amount_with_vat = QLineEdit()
        self.amount_with_vat.setReadOnly(True)
        self.amount_with_vat.setProperty("readonlyField", True)
        self.amount_with_vat.setStyleSheet(theme.readonly_field_qss())
        price_layout.addRow("Сумма с НДС", self.amount_with_vat)

        self.vat_amount = QLineEdit()
        self.vat_amount.setReadOnly(True)
        self.vat_amount.setProperty("readonlyField", True)
        self.vat_amount.setStyleSheet(theme.readonly_field_qss())
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
        self.vat_rate.currentIndexChanged.connect(self._recalculate)
        self._recalculate()

        logger.debug("Логистикс Рус PriceTab инициализирована")

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

    def amount_with_vat_value(self) -> Optional[float]:
        """
        Сумма с НДС: amount_without_vat × (1 + ставка/100), до копеек.

        У ИП такой суммы в бланке нет — возвращается None, и поле остаётся
        пустым.
        """
        if self._is_ip():
            return None
        amount = float(self.amount_without_vat.value())
        return round(amount * (1 + self.vat_rate_num() / 100), 2)

    def vat_amount_value(self) -> Optional[float]:
        """
        НДС от суммы без НДС: amount_without_vat × ставка/100.

        У ИП налога нет — возвращается None.
        """
        if self._is_ip():
            return None
        amount = float(self.amount_without_vat.value())
        return round(amount * self.vat_rate_num() / 100, 2)

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

        self._recalculate()
        # В лог идёт только вариант расчёта: суммы — это данные заявки.
        logger.info(
            "Логистикс Рус: тип экспедитора %s, ставка НДС %s",
            self.carrier_type.currentText(), self.vat_rate.currentText(),
        )

    def _recalculate(self) -> None:
        """Пересчитывает сумму с НДС и НДС по текущей ставке."""
        amount_with_vat = self.amount_with_vat_value()
        vat_amount = self.vat_amount_value()

        if amount_with_vat is None or vat_amount is None:
            # ИП: в бланке одна сумма — расчётные поля остаются пустыми.
            self.amount_with_vat.setText("")
            self.vat_amount.setText("")
            return

        self.amount_with_vat.setText(f"{amount_with_vat:.2f}{AMOUNT_SUFFIX}")
        self.vat_amount.setText(f"{vat_amount:.2f}{AMOUNT_SUFFIX}")

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Стоимость (ключи — как ждёт сборка данных).

        У ИП суммы с НДС нет: в ключе amount_with_vat пустая строка, а
        vat_rate_num = 0 — сборка данных на пустое значение ключ не создаёт.
        """
        amount_with_vat = self.amount_with_vat_value()

        return {
            "carrier_type": self.carrier_type.currentText(),
            "amount_without_vat": float(self.amount_without_vat.value()),
            "amount_with_vat": "" if amount_with_vat is None else amount_with_vat,
            "vat_rate": self.vat_rate.currentText(),
            "vat_rate_num": self.vat_rate_num(),
            "special_conditions": self.special_conditions.toPlainText().strip(),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет стоимость.

        Принимает как ключи вкладки (amount_without_vat), так и ключи
        ContractData и распознанных сумм (price_without_vat, price_input,
        sum_wo_vat, sum_total): распознавание отдаёт блок contract целиком.
        Пустые значения игнорируются, а нулевая сумма пропускается: у
        промпта 0.0 означает «суммы в документе не было», и стирать ею
        введённое нельзя.
        """
        if not data:
            return

        self._apply_carrier_type(data.get("carrier_type"))

        amount = data.get("amount_without_vat")
        if amount in (None, ""):
            amount = data.get("price_without_vat")
        if amount in (None, ""):
            amount = data.get("price_input")
        if amount in (None, ""):
            amount = data.get("sum_wo_vat")
        if amount in (None, ""):
            amount = data.get("sum_total")

        if amount not in (None, ""):
            try:
                amount_value = float(amount)
            except (ValueError, TypeError):
                logger.warning("Логистикс Рус: стоимость не распознана как число")
            else:
                if amount_value > 0:
                    self.amount_without_vat.setValue(amount_value)

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

        # Тип и ставка могли измениться — суммы пересчитываем сразу.
        self._recalculate()

        logger.info("Логистикс Рус: данные стоимости заполнены")

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
        """Сбрасывает стоимость к значениям по умолчанию (ООО, 22%)."""
        self.carrier_type.setCurrentText(DEFAULT_CARRIER_TYPE)
        self.amount_without_vat.setValue(0)
        self.vat_rate.setEnabled(True)
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        self.special_conditions.clear()
        self._recalculate()
        self.recognition_panel.clear()

        logger.debug("Логистикс Рус: поля стоимости очищены")


__all__ = [
    "PriceTab",
    "CARRIER_TYPES",
    "DEFAULT_CARRIER_TYPE",
    "VAT_RATES",
    "DEFAULT_VAT_RATE",
    "ZERO_VAT_RATE",
    "MAX_AMOUNT",
]
