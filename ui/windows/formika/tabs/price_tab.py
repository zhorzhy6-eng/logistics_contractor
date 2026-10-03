#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Стоимость» окна типа «Формика» (ЭТАП 3.1.B.2).

Особенность Формики: в бланке одна сумма, и она уже включает НДС. Поэтому
пользователь вводит только её — «Сумма с НДС», а «в том числе НДС» и
«Сумма без НДС» вкладка считает сама:

    amount_with_vat    = amount
    amount_without_vat = amount / (1 + ставка/100)

Сумма прописью считается через core.num_to_words.amount_to_words — тем же
модулем, что и в генераторах договоров. Логика ContractTab здесь не
копируется: там сумма может быть и с НДС, и без него, у Формики вариант один.

Ключи get_data() — amount, amount_without_vat, amount_with_vat, vat_rate,
vat_rate_num, payment_days, special_conditions — читает
ui/windows/formika/data.py::_build_price.
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLineEdit,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from core.num_to_words import amount_to_words
from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import PasteableTextEdit, RecognitionPanel

logger = logging.getLogger("ui.windows.formika.tabs.price_tab")

#: Ставки НДС в выпадающем списке; по умолчанию — первая.
VAT_RATES = ("22%", "20%", "10%", "0%")
DEFAULT_VAT_RATE = "22%"

#: Срок оплаты по умолчанию — как в бланке Формики.
DEFAULT_PAYMENT_DAYS = 10

#: Границы вводимой суммы (как на вкладке условий договора).
MAX_AMOUNT = 100_000_000


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


class PriceTab(TabMixin, QWidget):
    """Стоимость перевозки: сумма с НДС, расчёт без НДС и сумма прописью."""

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
            placeholder="Вставьте текст со стоимостью перевозки (сумма, НДС, срок оплаты)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Стоимость перевозки» ──
        price_group = QGroupBox("Стоимость перевозки")
        price_layout = QFormLayout(price_group)

        # Сумма с НДС — единственное поле, которое вводит пользователь.
        self.amount = NoWheelDoubleSpinBox()
        self.amount.setRange(0, MAX_AMOUNT)
        self.amount.setDecimals(2)
        self.amount.setSuffix(" ₽")
        self.amount.setGroupSeparatorShown(True)
        price_layout.addRow("Сумма с НДС *", self.amount)

        self.vat_rate = NoWheelComboBox()
        self.vat_rate.addItems(list(VAT_RATES))
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        price_layout.addRow("Ставка НДС", self.vat_rate)

        # ── Расчётные поля: только для чтения ──
        # Оформление берётся из темы (ui/theme.py). Локальный стиль нужен
        # потому, что Qt не пересчитывает QSS при смене свойства readOnly
        # у уже отрисованного поля.
        self.amount_without_vat = QLineEdit()
        self.amount_without_vat.setReadOnly(True)
        self.amount_without_vat.setProperty("readonlyField", True)
        self.amount_without_vat.setStyleSheet(theme.readonly_field_qss())
        price_layout.addRow("Сумма без НДС", self.amount_without_vat)

        self.amount_with_vat = QLineEdit()
        self.amount_with_vat.setReadOnly(True)
        self.amount_with_vat.setProperty("readonlyField", True)
        self.amount_with_vat.setStyleSheet(theme.readonly_field_qss())
        price_layout.addRow("Сумма с НДС (итог)", self.amount_with_vat)

        # Сумма прописью — как и суммы выше, только для чтения.
        self.amount_words = QLineEdit()
        self.amount_words.setReadOnly(True)
        self.amount_words.setProperty("readonlyField", True)
        self.amount_words.setStyleSheet(theme.readonly_field_qss())
        price_layout.addRow("Сумма прописью", self.amount_words)

        layout.addWidget(price_group)

        # ── Группа «Порядок оплаты» ──
        payment_group = QGroupBox("Порядок оплаты")
        payment_layout = QFormLayout(payment_group)

        self.payment_days = NoWheelSpinBox()
        self.payment_days.setRange(0, 365)
        self.payment_days.setValue(DEFAULT_PAYMENT_DAYS)
        self.payment_days.setSuffix(" дн.")
        payment_layout.addRow("Срок оплаты", self.payment_days)

        # ── Особые условия ──
        self.special_conditions = PasteableTextEdit(
            "Дополнительные условия заявки...", max_height=120
        )
        payment_layout.addRow("Особые условия", self.special_conditions)

        layout.addWidget(payment_group)

        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        # ── Пересчёт при вводе суммы и смене ставки ──
        self.amount.valueChanged.connect(self._recalculate)
        self.vat_rate.currentIndexChanged.connect(self._recalculate)
        self._recalculate()

        logger.debug("Formika PriceTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Расчёт
    # ─────────────────────────────────────────────────────────

    def vat_rate_num(self) -> float:
        """Ставка НДС числом (22% → 22.0)."""
        try:
            return float(self.vat_rate.currentText().replace("%", "").strip())
        except (ValueError, TypeError):
            return 0.0

    def amount_without_vat_value(self) -> float:
        """
        Сумма без НДС: amount / (1 + ставка/100), округление до копеек.

        При нулевой ставке без НДС равна сумме с НДС — делить не на что.
        """
        rate = self.vat_rate_num()
        amount = float(self.amount.value())
        if rate <= 0:
            return round(amount, 2)
        return round(amount / (1 + rate / 100), 2)

    def _recalculate(self) -> None:
        """Пересчитывает суммы и сумму прописью по текущей ставке НДС."""
        amount = float(self.amount.value())
        amount_without_vat = self.amount_without_vat_value()

        # Сумма в бланке одна и уже с НДС, поэтому «итог» — это введённая сумма.
        self.amount_with_vat.setText(f"{amount:.2f} ₽")
        self.amount_without_vat.setText(f"{amount_without_vat:.2f} ₽")

        # Прописью — сумма с НДС: она и печатается в договоре-заявке.
        self.amount_words.setText(amount_to_words(amount))

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """Стоимость и порядок оплаты (ключи — как ждёт сборка данных)."""
        return {
            "amount": float(self.amount.value()),
            "amount_without_vat": self.amount_without_vat_value(),
            "amount_with_vat": float(self.amount.value()),
            "vat_rate": self.vat_rate.currentText(),
            "vat_rate_num": self.vat_rate_num(),
            "payment_days": int(self.payment_days.value()),
            "special_conditions": self.special_conditions.toPlainText().strip(),
        }

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет стоимость.

        Принимает как ключи вкладки (amount), так и ключи ContractData
        (price_input, price_with_vat): распознавание отдаёт блок contract
        целиком. Значения по умолчанию без данных не подставляются — иначе
        распознавание без блока стоимости вернуло бы ставку НДС к 22%.
        """
        if not data:
            return

        amount = data.get("amount")
        if amount in (None, ""):
            amount = data.get("price_input")
        if amount in (None, ""):
            amount = data.get("price_with_vat")
        if amount not in (None, ""):
            try:
                self.amount.setValue(float(amount))
            except (ValueError, TypeError):
                logger.warning("Формика: стоимость не распознана как число")

        vat_rate = str(data.get("vat_rate") or "").strip()
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
                logger.warning("Формика: срок оплаты не распознан как число")

        if data.get("special_conditions"):
            self.special_conditions.setPlainText(str(data["special_conditions"]))

        # Ставка могла измениться — суммы и пропись пересчитываем сразу.
        self._recalculate()

        logger.info("Формика: данные стоимости заполнены")

    def clear(self) -> None:
        """Сбрасывает стоимость и порядок оплаты к значениям по умолчанию."""
        self.amount.setValue(0)
        self.vat_rate.setCurrentText(DEFAULT_VAT_RATE)
        self.payment_days.setValue(DEFAULT_PAYMENT_DAYS)
        self.special_conditions.clear()
        self._recalculate()
        self.recognition_panel.clear()

        logger.debug("Формика: поля стоимости очищены")


__all__ = ["PriceTab", "VAT_RATES", "DEFAULT_VAT_RATE", "DEFAULT_PAYMENT_DAYS"]
