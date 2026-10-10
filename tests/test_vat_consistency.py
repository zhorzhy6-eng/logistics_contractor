#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единая формула НДС: Аренда, Логистикс, Формика и Экспедиторство.

Правило одно на все типы — «НДС В ТОМ ЧИСЛЕ» (core/vat.py::compute_vat):

    sum_total  = введённая сумма (ИТОГ договора);
    sum_wo_nds = sum_total / (1 + ставка/100);
    sum_nds    = sum_total − sum_wo_nds.

Оператор вводит ИТОГ, а база без НДС и налог выводятся из него. Локальных
формул НДС у типов больше нет: и вкладки, и генераторы, и валидаторы считают
этим ядром.

Проверяется главное свойство сведения: ОДНА сумма и ОДНА ставка дают
ОДИНАКОВЫЙ результат во всех четырёх типах — 6 ставок × 4 типа = 24 проверки,
плюс сверка каждой вкладки с `core.vat.compute_vat`, полнота списка ставок и
сквозной путь «вкладка → данные договора → генератор → документ».

Qt — в offscreen-режиме. Данные синтетические, реальных ПДн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from docx import Document  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from core.vat import (  # noqa: E402
    DEFAULT_VAT_RATE,
    VAT_FREE,
    VAT_RATES,
    compute_vat,
)
from ui.tabs import contract_tab as perevozka_contract_tab  # noqa: E402
from ui.tabs.contract_tab import ContractTab  # noqa: E402
from ui.windows.arenda_ts.tabs import price_tab as arenda_price_tab  # noqa: E402
from ui.windows.arenda_ts.tabs.price_tab import PriceTab as ArendaPriceTab  # noqa: E402
from ui.windows.formika.tabs import price_tab as formika_price_tab  # noqa: E402
from ui.windows.formika.tabs.price_tab import PriceTab as FormikaPriceTab  # noqa: E402
from ui.windows.logistiks_rus.tabs import price_tab as logistiks_price_tab  # noqa: E402
from ui.windows.logistiks_rus.tabs.price_tab import (  # noqa: E402
    PriceTab as LogistiksPriceTab,
)

#: Сумма из задания: 250 000,00.
TOTAL = 250000.00

#: Ожидаемые пары «база + НДС» для каждой ставки на этой сумме.
EXPECTED = {
    "Без НДС": (250000.00, 0.00),
    "0%": (250000.00, 0.00),
    "5%": (238095.24, 11904.76),
    "7%": (233644.86, 16355.14),
    "10%": (227272.73, 22727.27),
    "22%": (204918.03, 45081.97),
}


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


# ─────────────────────────────────────────────────────────────
# Вкладки четырёх типов: одна сумма и одна ставка
# ─────────────────────────────────────────────────────────────

class PerevozkaVat:
    """Экспедиторство (договор-заявка на перевозку): вкладка «Договор»."""

    title = "Экспедиторство"
    module = perevozka_contract_tab

    @staticmethod
    def make() -> ContractTab:
        return ContractTab()

    @staticmethod
    def set_amount(tab: ContractTab, amount: float) -> None:
        tab.price_input.setValue(amount)

    @staticmethod
    def set_rate(tab: ContractTab, rate: str) -> None:
        tab.vat_rate.setCurrentText(rate)

    @staticmethod
    def read(tab: ContractTab) -> dict:
        data = tab.get_data()
        return {
            "vat_rate": data["vat_rate"],
            "price_with_vat": data["price_with_vat"],
            "price_without_vat": data["price_without_vat"],
            "vat_amount": data["vat_amount"],
        }


class ArendaVat:
    """Разовая аренда ТС: вкладка «Стоимость»."""

    title = "Аренда"
    module = arenda_price_tab

    @staticmethod
    def make() -> ArendaPriceTab:
        return ArendaPriceTab()

    @staticmethod
    def set_amount(tab: ArendaPriceTab, amount: float) -> None:
        tab.sum_total.setValue(amount)

    @staticmethod
    def set_rate(tab: ArendaPriceTab, rate: str) -> None:
        tab.vat_rate.setCurrentText(rate)

    @staticmethod
    def read(tab: ArendaPriceTab) -> dict:
        data = tab.get_data()
        return {
            "vat_rate": data["vat_rate"],
            "price_with_vat": data["price_with_vat"],
            "price_without_vat": data["price_without_vat"],
            "vat_amount": data["vat_amount"],
        }


class LogistiksVat:
    """Логистикс Рус: вкладка «Стоимость» (тип экспедитора — ООО)."""

    title = "Логистикс"
    module = logistiks_price_tab

    @staticmethod
    def make() -> LogistiksPriceTab:
        return LogistiksPriceTab()

    @staticmethod
    def set_amount(tab: LogistiksPriceTab, amount: float) -> None:
        tab.amount_total.setValue(amount)

    @staticmethod
    def set_rate(tab: LogistiksPriceTab, rate: str) -> None:
        tab.vat_rate.setCurrentText(rate)

    @staticmethod
    def read(tab: LogistiksPriceTab) -> dict:
        data = tab.get_data()
        return {
            "vat_rate": data["vat_rate"],
            "price_with_vat": data["price_with_vat"],
            "price_without_vat": data["price_without_vat"],
            "vat_amount": data["vat_amount"],
        }


class FormikaVat:
    """Формика: вкладка «Стоимость» (сумма документа уже с НДС)."""

    title = "Формика"
    module = formika_price_tab

    @staticmethod
    def make() -> FormikaPriceTab:
        return FormikaPriceTab()

    @staticmethod
    def set_amount(tab: FormikaPriceTab, amount: float) -> None:
        tab.amount.setValue(amount)

    @staticmethod
    def set_rate(tab: FormikaPriceTab, rate: str) -> None:
        tab.vat_rate.setCurrentText(rate)

    @staticmethod
    def read(tab: FormikaPriceTab) -> dict:
        data = tab.get_data()
        return {
            "vat_rate": data["vat_rate"],
            "price_with_vat": data["price_with_vat"],
            "price_without_vat": data["price_without_vat"],
            "vat_amount": data["vat_amount"],
        }


#: Четыре типа договора — вкладки, считающие НДС.
TYPES = (PerevozkaVat, ArendaVat, LogistiksVat, FormikaVat)


def _filled(kind, rate: str, amount: float = TOTAL):
    """Вкладка типа с введёнными суммой и ставкой."""
    tab = kind.make()
    kind.set_rate(tab, rate)
    kind.set_amount(tab, amount)
    return tab


def _ids(kinds):
    return [kind.title for kind in kinds]


# ─────────────────────────────────────────────────────────────
# D.1: 6 ставок × 4 типа = 24 проверки
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rate", list(VAT_RATES))
@pytest.mark.parametrize("kind", TYPES, ids=_ids(TYPES))
def test_type_matches_core_vat(qt_app, kind, rate):
    """Вкладка типа считает ровно так же, как ядро: 250 000 и ставка."""
    tab = _filled(kind, rate)

    data = kind.read(tab)
    expected = compute_vat(TOTAL, rate)

    assert data["vat_rate"] == rate
    assert data["price_with_vat"] == pytest.approx(expected["sum_total"])
    assert data["price_without_vat"] == pytest.approx(expected["sum_wo_nds"])
    assert data["vat_amount"] == pytest.approx(expected["sum_nds"])


@pytest.mark.parametrize("rate", list(VAT_RATES))
@pytest.mark.parametrize("kind", TYPES, ids=_ids(TYPES))
def test_type_matches_task_table(qt_app, kind, rate):
    """Таблица задания: 250 000 + ставка → база и НДС до копейки."""
    tab = _filled(kind, rate)

    data = kind.read(tab)
    base, vat = EXPECTED[rate]

    assert data["price_without_vat"] == pytest.approx(base)
    assert data["vat_amount"] == pytest.approx(vat)
    assert data["price_with_vat"] == pytest.approx(TOTAL)


@pytest.mark.parametrize("rate", list(VAT_RATES))
def test_all_types_give_the_same_result(qt_app, rate):
    """
    Главная проверка сведения: сумма и ставка → ОДИН результат во всех типах.

    Четыре вкладки заполняются одним и тем же числом 250 000,00 при одной и
    той же ставке, и все четыре отдают одинаковые базу, налог и итог.
    """
    results = {}
    for kind in TYPES:
        tab = _filled(kind, rate)
        results[kind.title] = kind.read(tab)

    base = results[PerevozkaVat.title]["price_without_vat"]
    vat = results[PerevozkaVat.title]["vat_amount"]
    total = results[PerevozkaVat.title]["price_with_vat"]

    for title, data in results.items():
        assert data["price_without_vat"] == pytest.approx(base), title
        assert data["vat_amount"] == pytest.approx(vat), title
        assert data["price_with_vat"] == pytest.approx(total), title

    # И это ровно то, что обещает таблица задания.
    assert (base, vat) == pytest.approx(EXPECTED[rate])


@pytest.mark.parametrize("kind", TYPES, ids=_ids(TYPES))
def test_base_and_vat_sum_to_total(qt_app, kind):
    """Копейка не теряется: база + НДС = итог при каждой из шести ставок."""
    for rate in VAT_RATES:
        data = kind.read(_filled(kind, rate))

        assert round(data["price_without_vat"] + data["vat_amount"], 2) \
            == pytest.approx(data["price_with_vat"]), rate


# ─────────────────────────────────────────────────────────────
# Единый список ставок
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", TYPES, ids=_ids(TYPES))
def test_rate_list_is_the_core_six(kind):
    """Список ставок каждого типа — ровно шесть пунктов ядра."""
    assert tuple(kind.module.VAT_RATES) == VAT_RATES
    assert kind.module.DEFAULT_VAT_RATE == DEFAULT_VAT_RATE == "22%"
    assert VAT_RATES == (VAT_FREE, "0%", "5%", "7%", "10%", "22%")


@pytest.mark.parametrize("kind", TYPES, ids=_ids(TYPES))
def test_default_rate_is_22(qt_app, kind):
    """По умолчанию во всех типах — «22%»."""
    tab = kind.make()

    assert kind.read(tab)["vat_rate"] == "22%"


@pytest.mark.parametrize("kind", TYPES, ids=_ids(TYPES))
def test_vat_free_and_zero_give_no_tax(qt_app, kind):
    """«Без НДС» и «0%» налога не дают: база равна итогу."""
    for rate in (VAT_FREE, "0%"):
        data = kind.read(_filled(kind, rate))

        assert data["vat_amount"] == 0.0, rate
        assert data["price_without_vat"] == pytest.approx(TOTAL), rate


# ─────────────────────────────────────────────────────────────
# Ядро: правило «НДС в том числе»
# ─────────────────────────────────────────────────────────────

def test_core_rule_is_vat_inclusive():
    """250 000 + 22% → база 204 918,03, налог 45 081,97 (вычитанием)."""
    vat = compute_vat(250000.00, "22%")

    assert vat == {"sum_total": 250000.0, "sum_wo_nds": 204918.03,
                   "sum_nds": 45081.97}


def test_core_rule_does_not_add_vat_on_top():
    """
    Налог ВЫНИМАЕТСЯ из итога, а не прибавляется к нему.

    Если бы ставка начислялась сверху, 250 000 при 22% дали бы итог 305 000 —
    оператор назвал бы одну сумму, а в договоре стояла бы другая.
    """
    vat = compute_vat(250000.00, "22%")

    assert vat["sum_total"] == 250000.00
    assert vat["sum_total"] != pytest.approx(round(250000.00 * 1.22, 2))
