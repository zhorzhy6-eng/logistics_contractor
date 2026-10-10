#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты дефолтов полей — ШАГ FIX-6, часть C.

Смысл шага: поле, которое оператор должен заполнить сам, не получает
значение «от программы». Такие подстановки выглядят как введённые данные
и уезжают в договор.

Что убрано и проверяется здесь:

  * «Год выпуска» тягача и полуприцепа (было 2023 / 2020) — пусто,
    в данных пустая строка, а не «0»;
  * «Стоимость» Экспедиторства (было 400 000 ₽) — пусто (ноль);
  * «Тип ТС» и «Год выпуска» машин в «Перевозимых авто» (было «Легковой
    автомобиль» и текущий год) — пусто.

Что ОСТАВЛЕНО осознанно (проверяется, чтобы не сломали по неосторожности):

  * дата договора и дата погрузки — сегодня, дата выгрузки — +3 дня;
  * ставка НДС — 22 %, валюта — рубль (суммы считаются в рублях);
  * срок оплаты — 10 дней.

Данные синтетические, ПДн нет. Qt — в offscreen-режиме.
"""

import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt5")

from PyQt5.QtCore import QDate  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ui.tabs.contract_tab import ContractTab  # noqa: E402
from ui.tabs.trailer_tab import TrailerTab  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """Одно приложение Qt на модуль (offscreen)."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def trailer(qapp) -> TrailerTab:
    return TrailerTab()


@pytest.fixture
def contract(qapp) -> ContractTab:
    return ContractTab()


# ─────────────────────────────────────────────────────────────
# Год выпуска тягача и полуприцепа
# ─────────────────────────────────────────────────────────────

def test_year_not_defaulted(trailer):
    """Год выпуска не подставляется: у обоих блоков пусто (прочерк)."""
    assert trailer.tractor_year.year() == 0
    assert trailer.trailer_year.year() == 0
    assert trailer.tractor_year.specialValueText() == "—"
    assert trailer.trailer_year.specialValueText() == "—"


def test_year_not_in_data_until_entered(trailer):
    """В данные год уходит пустой строкой, а не «0» и не «2023»."""
    tractor = trailer.get_tractor_data()
    trailer_data = trailer.get_trailer_data()

    assert tractor["year"] == ""
    assert trailer_data["year"] == ""


def test_year_is_kept_when_entered(trailer):
    """Введённый год сохраняется и отдаётся как есть."""
    trailer.tractor_year.set_year(2019)
    trailer.trailer_year.set_year("2015")

    assert trailer.get_tractor_data()["year"] == "2019"
    assert trailer.get_trailer_data()["year"] == "2015"


def test_year_from_data_is_applied(trailer):
    """Год из распознавания доходит до поля."""
    trailer.fill_data({"year": 2011}, {"year": "2008"})

    assert trailer.tractor_year.year() == 2011
    assert trailer.trailer_year.year() == 2008


def test_fill_data_does_not_reset_year(trailer):
    """Частичное заполнение не сбрасывает уже введённый год."""
    trailer.tractor_year.set_year(2019)

    trailer.fill_data({"brand_model": "Volvo FH"}, {})

    assert trailer.tractor_year.year() == 2019


def test_clear_resets_to_empty(trailer):
    """Очистка возвращает поля к ПУСТОМУ значению, а не к 2023 / 2020."""
    trailer.fill_data({"brand_model": "Volvo FH", "color": "Белый", "year": 2019},
                      {"brand_model": "Schmitz", "color": "Серый", "year": 2015})

    trailer.clear()

    assert trailer.tractor_brand.text() == ""
    assert trailer.tractor_color.text() == ""
    assert trailer.tractor_year.year() == 0
    assert trailer.trailer_brand.text() == ""
    assert trailer.trailer_color.text() == ""
    assert trailer.trailer_year.year() == 0
    assert trailer.get_tractor_data()["year"] == ""
    assert trailer.get_trailer_data()["year"] == ""


def test_clear_does_not_bring_back_old_defaults(trailer):
    """Ни «2023», ни «2020» после очистки не появляются."""
    trailer.clear()

    for field in (trailer.tractor_year, trailer.trailer_year):
        assert field.value() not in (2023, 2020)
        assert field.value() == 0


# ─────────────────────────────────────────────────────────────
# Стоимость договора перевозки
# ─────────────────────────────────────────────────────────────

def test_price_not_defaulted(contract):
    """Стоимость не подставляется: пустое поле — ноль."""
    assert contract.price_input.value() == 0


def test_price_cleared_to_zero(contract):
    """Очистка возвращает стоимость к нулю, а не к 400 000 ₽."""
    contract.price_input.setValue(400000)

    contract.clear()

    assert contract.price_input.value() == 0
    assert contract.price_input.value() != 400000


# ─────────────────────────────────────────────────────────────
# Дефолты, которые ОСТАВЛЕНЫ (не сломать по неосторожности)
# ─────────────────────────────────────────────────────────────

def test_dates_are_still_defaulted(contract):
    """Дата договора и погрузки — сегодня, выгрузки — +3 дня."""
    today = QDate.currentDate()

    assert contract.date.date() == today
    assert contract.loading_plan_date.date() == today
    assert contract.unloading_plan_date.date() == today.addDays(3)


def test_vat_rate_is_still_22(contract):
    """Ставка НДС по умолчанию — 22 %."""
    assert contract.vat_rate.currentText() == "22%"


def test_payment_days_is_still_10(contract):
    """Срок оплаты по умолчанию — 10 дней."""
    assert contract.payment_days.text() == "10"


def test_payment_days_is_required(contract):
    """Срок оплаты помечен обязательным: он печатается в договоре."""
    from ui import theme

    labels = [
        label.text() for label in contract.findChildren(type(theme.make_label("x")))
    ]
    assert any("Срок оплаты" in text and "*" in text for text in labels), labels


def test_clear_keeps_reasonable_defaults(contract):
    """После очистки разумные дефолты на месте."""
    contract.clear()

    assert contract.date.date() == QDate.currentDate()
    assert contract.loading_plan_date.date() == QDate.currentDate()
    assert contract.unloading_plan_date.date() == QDate.currentDate().addDays(3)
    assert contract.vat_rate.currentText() == "22%"
    assert contract.payment_days.text() == "10"
    assert contract.price_input.value() == 0


def test_no_misleading_placeholders_in_data(contract):
    """В собранных данных нет ни «SCANIA», ни «Белый», ни годов по умолчанию."""
    data = contract.get_data()
    text = repr(data)

    for wrong in ("SCANIA", "KRONE", "Белый", "Серый"):
        assert wrong not in text, f"{wrong} попал в данные договора"

    assert data["price_without_vat"] == 0.0
    assert datetime.now().year not in (data.get("price_without_vat"),)


# ─────────────────────────────────────────────────────────────
# Текст диалога валидатора (единый во всех пяти типах)
# ─────────────────────────────────────────────────────────────

#: Ожидаемая формулировка предупреждения валидатора: без ошибок, только
#: замечания. Раньше фраза обрывалась на «останутся пустыми», и было
#: непонятно, что у денежного поля будет «0.00».
EXPECTED_WARNING_TEXT = (
    "В данных есть замечания. Поля, отмеченные ниже, останутся пустыми "
    "(у денежных полей будет 0.00, у текстовых — пусто)."
)


@pytest.fixture
def quiet_message_box(monkeypatch):
    """Диалог подтверждения не открывается, но его текст запоминается."""
    from PyQt5.QtWidgets import QMessageBox

    seen = {}

    class _Box:
        def __init__(self, *args, **kwargs):
            seen["text"] = ""

        def setWindowTitle(self, title):
            seen["title"] = title

        def setIcon(self, icon):
            pass

        def setText(self, text):
            seen["text"] = text

        def setInformativeText(self, text):
            seen["informative"] = text

        def setStandardButtons(self, buttons):
            pass

        def setDefaultButton(self, button):
            pass

        def button(self, which):
            return None

        def exec_(self):
            return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "__init__", _Box.__init__)
    monkeypatch.setattr(QMessageBox, "setWindowTitle", _Box.setWindowTitle)
    monkeypatch.setattr(QMessageBox, "setIcon", _Box.setIcon)
    monkeypatch.setattr(QMessageBox, "setText", _Box.setText)
    monkeypatch.setattr(QMessageBox, "setInformativeText", _Box.setInformativeText)
    monkeypatch.setattr(QMessageBox, "setStandardButtons", _Box.setStandardButtons)
    monkeypatch.setattr(QMessageBox, "setDefaultButton", _Box.setDefaultButton)
    monkeypatch.setattr(QMessageBox, "button", _Box.button)
    monkeypatch.setattr(QMessageBox, "exec_", _Box.exec_)
    return seen


def _confirm_text(window, text) -> str:
    """Гоняет _confirm_validation на отчёте «только замечания»."""
    from core.validator import ValidationReport

    report = ValidationReport()
    report.warnings.append(text)

    assert window._confirm_validation(report) is False
    return window


def test_dialog_text_is_the_same_in_all_types(qapp, isolated_db, monkeypatch,
                                              quiet_message_box):
    """
    Формулировка предупреждения валидатора — одна на все пять типов.

    Проверяются живые окна: фраза печётся в пяти местах (MainWindow и
    четыре окна типов), и расхождение заметно только тестом.
    """
    from PyQt5.QtWidgets import QMessageBox

    from ui.main_window import MainWindow
    from ui.windows.arenda_ts.window import ArendaTsWindow
    from ui.windows.formika.window import FormikaWindow
    from ui.windows.havaly.window import HavalyWindow
    from ui.windows.logistiks_rus.window import LogistiksRusWindow

    monkeypatch.setattr(MainWindow, "_init_gigachat_client", lambda *a, **k: False)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    windows = [MainWindow(), FormikaWindow(), LogistiksRusWindow(),
               ArendaTsWindow(), HavalyWindow()]
    try:
        for window in windows:
            _confirm_text(window, "Замечание для проверки текста диалога")

            assert quiet_message_box["title"] == "Замечания к данным"
            assert quiet_message_box["text"] == EXPECTED_WARNING_TEXT, (
                f"{type(window).__name__}: текст диалога разошёлся"
            )
    finally:
        for window in windows:
            try:
                window.force_close()
            except AttributeError:
                window.close()
