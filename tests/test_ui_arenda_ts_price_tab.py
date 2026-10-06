#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты вкладки «Стоимость» окна «Разовая аренда» (ШАГ FIX-1).

Проверяются три вещи этого шага — каждая от расчёта до готового договора:

  * БАГ 1: сумма вводится и «без НДС», и «с НДС». Переключатель «Считать от»
    меняет смысл введённого числа, а не само число: итог и НДС пересчитываются,
    в бланк уходит одно и то же итоговое число;
  * БАГ 3: срок оплаты — поле «Срок оплаты, банковских дней», по умолчанию 30,
    ключ payment_days доходит до contract.

Расчёт вынесен в чистые функции модуля (base_from_total / total_from_base /
vat_from_base / mode_from_amounts) — их можно проверить без интерфейса, поэтому
файл начинается с математики, затем идёт вкладка, затем стык с data.py и
генератором.

Qt — в offscreen-режиме. Данные синтетические, реальных ПДн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from docx import Document  # noqa: E402
from PyQt5.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QSpinBox  # noqa: E402

from core.contract_data import ContractData  # noqa: E402
from ui.windows.arenda_ts.data import collect_arenda_ts_data  # noqa: E402
from ui.windows.arenda_ts.tabs import price_tab as price_tab_module  # noqa: E402
from ui.windows.arenda_ts.tabs.price_tab import (  # noqa: E402
    MODE_WITH_VAT,
    MODE_WITHOUT_VAT,
    PriceTab,
    base_from_total,
    mode_from_amounts,
    rate_to_factor,
    total_from_base,
    vat_from_base,
)

# ─────────────────────────────────────────────────────────────
# Константы тестовых данных
# ─────────────────────────────────────────────────────────────

#: Сумма заказчика «230 000 с НДС» при ставке 22% (задание FIX-1):
#: 230 000,00 = 188 524,59 + 41 475,41.
TOTAL_230K = 230000.00
BASE_230K = 188524.59
VAT_230K = 41475.41

#: Суммы «из документа» для проверки обратного счёта.
BASE_SUM = 221099.18
VAT_SUM = 48641.82
TOTAL_SUM = 269741.00

#: Ставка, при которой НДС не начисляется.
ZERO_RATE = 0.0
RATE_22 = 22.0

#: Срок оплаты из задания и значение по умолчанию.
PAYMENT_DAYS_CUSTOM = 45
PAYMENT_DAYS_DEFAULT = 30


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qt_app):
    """Свежая вкладка «Стоимость» в режиме по умолчанию («Без НДС»)."""
    widget = PriceTab()
    yield widget
    widget.deleteLater()


# ─────────────────────────────────────────────────────────────
# Вспомогательное
# ─────────────────────────────────────────────────────────────

def _tab(qt_app, mode: str = MODE_WITHOUT_VAT) -> PriceTab:
    """Вкладка в нужном режиме ввода суммы."""
    widget = PriceTab()
    widget.amount_mode.setCurrentText(mode)
    return widget


def _flatten(text: str) -> str:
    """Текст одной строкой: любые пробелы (в том числе неразрывные) — по одному."""
    import re

    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _document_text(doc) -> str:
    """Весь текст документа: абзацы и таблицы, пробелы нормализованы."""
    parts = [_flatten(p.text) for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(_flatten(cell.text))
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────
# Математика НДС (чистые функции модуля)
# ─────────────────────────────────────────────────────────────

def test_rate_to_factor():
    """22% → 1.22, 0% → 1.0."""
    assert rate_to_factor(22.0) == pytest.approx(1.22)
    assert rate_to_factor(ZERO_RATE) == pytest.approx(1.0)


def test_total_from_base_adds_vat():
    """Итог = база × (1 + ставка/100), до копеек."""
    assert total_from_base(BASE_230K, RATE_22) == pytest.approx(TOTAL_230K)
    assert total_from_base(1000.0, ZERO_RATE) == pytest.approx(1000.0)


def test_base_from_total_extracts_vat():
    """База = итог / (1 + ставка/100), до копеек."""
    assert base_from_total(TOTAL_230K, RATE_22) == pytest.approx(BASE_230K)
    assert base_from_total(1000.0, ZERO_RATE) == pytest.approx(1000.0)


def test_vat_from_base_is_rate_share():
    """НДС = база × ставка/100; при нулевой ставке — ровно ноль."""
    assert vat_from_base(1000.0, RATE_22) == pytest.approx(220.0)
    assert vat_from_base(1000.0, ZERO_RATE) == 0.0


@pytest.mark.parametrize("rate", [22.0, 20.0, 10.0, 0.0])
def test_base_and_total_round_trip(rate):
    """Пересчёт «туда и обратно» не сдвигает сумму больше чем на копейку."""
    for total in (TOTAL_230K, TOTAL_SUM, 999.99, 100.0, 1.0):
        base = base_from_total(total, rate)
        assert abs(total_from_base(base, rate) - total) <= 0.01, (rate, total)


def test_vat_and_base_sum_to_total():
    """НДС и база в сумме дают ровно итог: ни одной потерянной копейки."""
    base = base_from_total(TOTAL_230K, RATE_22)
    vat = round(TOTAL_230K - base, 2)

    assert base == pytest.approx(BASE_230K)
    assert vat == pytest.approx(VAT_230K)
    assert round(base + vat, 2) == pytest.approx(TOTAL_230K)


def test_mode_from_amounts_without_base_is_with_vat():
    """Базы нет, а итог есть — единственную сумму читаем как сумму с НДС."""
    assert mode_from_amounts(None, TOTAL_230K, RATE_22) == MODE_WITH_VAT
    assert mode_from_amounts(None, None, RATE_22) == MODE_WITHOUT_VAT


def test_mode_from_amounts_reads_consistent_pair():
    """Пара «база + итог» под ставку читается как введённая без НДС."""
    assert mode_from_amounts(BASE_230K, TOTAL_230K, RATE_22) == MODE_WITHOUT_VAT


def test_mode_from_amounts_zero_rate_prefers_with_vat():
    """При «0%» суммы равны: «С НДС» — только если базы в данных не было."""
    assert mode_from_amounts(None, TOTAL_SUM, ZERO_RATE) == MODE_WITH_VAT
    assert mode_from_amounts(TOTAL_SUM, TOTAL_SUM, ZERO_RATE) == MODE_WITH_VAT


def test_mode_from_amounts_mismatched_pair_falls_back():
    """Ставку поменяли — пара не сходится: режим по умолчанию."""
    assert mode_from_amounts(BASE_230K, 999999.0, RATE_22) \
        == price_tab_module.DEFAULT_AMOUNT_MODE


# ─────────────────────────────────────────────────────────────
# Вкладка: состав полей
# ─────────────────────────────────────────────────────────────

def test_tab_has_amount_mode_switch(tab):
    """Переключатель «Считать от»: «Без НДС» (по умолчанию) и «С НДС»."""
    assert isinstance(tab.amount_mode, QComboBox)
    assert [tab.amount_mode.itemText(i) for i in range(tab.amount_mode.count())] \
        == list(price_tab_module.AMOUNT_MODES)
    assert tab.amount_mode_text() == MODE_WITHOUT_VAT
    assert tab.calculates_from_total() is False


def test_tab_has_single_editable_amount_field(tab):
    """Одно редактируемое поле суммы; НДС и итог — расчётные."""
    assert isinstance(tab.sum_wo_vat, QDoubleSpinBox)
    assert tab.sum_wo_vat.isReadOnly() is False
    assert tab.sum_vat.isReadOnly() is True
    assert tab.sum_total.isReadOnly() is True


def test_tab_has_payment_days_field(tab):
    """Поле срока оплаты — целое число банковских дней."""
    assert isinstance(tab.payment_days, QSpinBox)
    assert not isinstance(tab.payment_days, QDoubleSpinBox)
    assert tab.payment_days.value() == PAYMENT_DAYS_DEFAULT
    assert tab.payment_days.minimum() == price_tab_module.MIN_PAYMENT_DAYS
    assert tab.payment_days.maximum() == price_tab_module.MAX_PAYMENT_DAYS


def test_tab_labels_explain_the_mode(tab):
    """Подпись поля суммы меняется вместе с режимом ввода."""
    assert price_tab_module.BASE_LABEL in tab._sum_edit_label.text()

    tab.amount_mode.setCurrentText(MODE_WITH_VAT)

    assert price_tab_module.TOTAL_LABEL in tab._sum_edit_label.text()


# ─────────────────────────────────────────────────────────────
# БАГ 1: ввод суммы «без НДС» и «с НДС»
# ─────────────────────────────────────────────────────────────

def test_without_vat_mode_adds_vat_on_top(qt_app):
    """«Без НДС»: итог = база × (1 + ставка/100), НДС = итог − база."""
    tab = _tab(qt_app)

    tab.sum_wo_vat.setValue(TOTAL_230K)
    data = tab.get_data()

    assert data["sum_wo_vat"] == pytest.approx(TOTAL_230K)
    assert data["sum_total"] == pytest.approx(round(TOTAL_230K * 1.22, 2))
    assert data["sum_vat"] == pytest.approx(round(data["sum_total"] - TOTAL_230K, 2))


def test_with_vat_mode_extracts_vat(qt_app):
    """«С НДС»: база = итог / (1 + ставка/100), НДС = итог − база."""
    tab = _tab(qt_app, MODE_WITH_VAT)

    tab.sum_wo_vat.setValue(TOTAL_230K)
    data = tab.get_data()

    assert data["sum_total"] == pytest.approx(TOTAL_230K)
    assert data["sum_wo_vat"] == pytest.approx(base_from_total(TOTAL_230K, RATE_22))
    assert data["sum_vat"] == pytest.approx(
        round(TOTAL_230K - data["sum_wo_vat"], 2)
    )


def test_with_vat_mode_rounding_matches_task(qt_app):
    """230 000,00 с НДС 22% → база 188 524,59 и НДС 41 475,41."""
    tab = _tab(qt_app, MODE_WITH_VAT)

    tab.sum_wo_vat.setValue(230000.00)
    data = tab.get_data()

    assert data["sum_wo_vat"] == 188524.59
    assert data["sum_vat"] == 41475.41
    assert data["sum_total"] == 230000.00


def test_mode_switch_does_not_lose_the_value(tab):
    """Смена режима не сбрасывает и не меняет введённое число."""
    tab.sum_wo_vat.setValue(TOTAL_230K)

    tab.amount_mode.setCurrentText(MODE_WITH_VAT)

    assert tab.input_amount() == pytest.approx(TOTAL_230K)
    assert tab.sum_wo_vat.isReadOnly() is False


def test_mode_switch_recalculates_the_other_side(tab):
    """Переключение режима пересчитывает вторую сумму из текущей."""
    tab.sum_wo_vat.setValue(TOTAL_230K)

    tab.amount_mode.setCurrentText(MODE_WITH_VAT)
    data = tab.get_data()

    assert data["sum_total"] == pytest.approx(TOTAL_230K)
    assert data["sum_wo_vat"] == pytest.approx(BASE_230K)
    assert data["sum_vat"] == pytest.approx(VAT_230K)


def test_mode_switch_back_restores_amounts(tab):
    """«Туда и обратно»: суммы возвращаются к исходным."""
    tab.sum_wo_vat.setValue(TOTAL_230K)
    before = tab.get_data()

    tab.amount_mode.setCurrentText(MODE_WITH_VAT)
    tab.amount_mode.setCurrentText(MODE_WITHOUT_VAT)
    after = tab.get_data()

    assert after["sum_wo_vat"] == pytest.approx(before["sum_wo_vat"])
    assert after["sum_vat"] == pytest.approx(before["sum_vat"])
    assert after["sum_total"] == pytest.approx(before["sum_total"])


def test_both_modes_give_the_same_total(qt_app):
    """
    Главная проверка БАГ 1: оба режима дают ОДИН итог.

    «Без НДС»: база 188 524,59 → итог 230 000,00.
    «С НДС»:   итог 230 000,00 → база 188 524,59.
    """
    without_vat = _tab(qt_app, MODE_WITHOUT_VAT)
    without_vat.sum_wo_vat.setValue(BASE_230K)

    with_vat = _tab(qt_app, MODE_WITH_VAT)
    with_vat.sum_wo_vat.setValue(TOTAL_230K)

    left = without_vat.get_data()
    right = with_vat.get_data()

    assert left["sum_wo_vat"] == right["sum_wo_vat"] == pytest.approx(BASE_230K)
    assert left["sum_vat"] == right["sum_vat"] == pytest.approx(VAT_230K)
    assert left["sum_total"] == right["sum_total"] == pytest.approx(TOTAL_230K)


def test_words_follow_the_total_in_both_modes(qt_app):
    """Сумма прописью — от итога и от режима не зависит."""
    without_vat = _tab(qt_app, MODE_WITHOUT_VAT)
    without_vat.sum_wo_vat.setValue(BASE_230K)

    with_vat = _tab(qt_app, MODE_WITH_VAT)
    with_vat.sum_wo_vat.setValue(TOTAL_230K)

    assert without_vat.sum_total_words.text() == with_vat.sum_total_words.text()
    assert without_vat.sum_total_words.text().startswith("Двести тридцать тысяч")


def test_words_are_not_about_base_sum(tab):
    """Прописью пишется итог, а не введённая база: «Без НДС» добавляет налог."""
    tab.sum_wo_vat.setValue(1000.0)

    assert "тысяча" in tab.sum_total_words.text()


@pytest.mark.parametrize("rate", ["22%", "20%", "10%", "0%"])
def test_base_and_vat_always_sum_to_total(tab, rate):
    """При любой ставке база + НДС = итог, а итог не уходит от введённого."""
    tab.vat_rate.setCurrentText(rate)
    tab.sum_wo_vat.setValue(TOTAL_230K)
    without_vat = tab.get_data()

    tab.amount_mode.setCurrentText(MODE_WITH_VAT)
    with_vat = tab.get_data()

    assert round(without_vat["sum_wo_vat"] + without_vat["sum_vat"], 2) \
        == pytest.approx(without_vat["sum_total"])
    assert round(with_vat["sum_wo_vat"] + with_vat["sum_vat"], 2) \
        == pytest.approx(with_vat["sum_total"])


# ─────────────────────────────────────────────────────────────
# БАГ 3: срок оплаты
# ─────────────────────────────────────────────────────────────

def test_payment_days_default_is_thirty(tab):
    """Значение по умолчанию — 30 банковских дней (как в бланке)."""
    assert tab.get_data()["payment_days"] == PAYMENT_DAYS_DEFAULT
    assert PAYMENT_DAYS_DEFAULT == price_tab_module.DEFAULT_PAYMENT_DAYS


def test_payment_days_accepts_custom_value(tab):
    tab.payment_days.setValue(PAYMENT_DAYS_CUSTOM)

    assert tab.get_data()["payment_days"] == PAYMENT_DAYS_CUSTOM


def test_payment_days_is_integer(tab):
    tab.payment_days.setValue(PAYMENT_DAYS_CUSTOM)

    value = tab.get_data()["payment_days"]
    assert isinstance(value, int)
    assert not isinstance(value, bool)


def test_payment_days_zero_means_not_set(tab):
    """Ноль — «срок не задан»: поле это принимает, о нём скажет валидатор."""
    tab.payment_days.setValue(0)

    assert tab.get_data()["payment_days"] == 0


def test_payment_days_rejects_negative(tab):
    """Отрицательного срока не бывает: поле не уходит в минус."""
    tab.payment_days.setValue(-5)

    assert tab.get_data()["payment_days"] == 0


def test_payment_days_has_upper_bound(tab):
    """Верхняя граница не даёт ввести «срок на десятилетие»."""
    tab.payment_days.setValue(9999)

    assert tab.get_data()["payment_days"] == price_tab_module.MAX_PAYMENT_DAYS


def test_payment_days_fill_and_read_back(tab):
    tab.fill_data({"sum_wo_vat": BASE_SUM, "payment_days": PAYMENT_DAYS_CUSTOM})

    assert tab.get_data()["payment_days"] == PAYMENT_DAYS_CUSTOM


def test_payment_days_ignores_empty_and_garbage(tab):
    """Пустое и нечисловое значение не сбрасывают введённый срок."""
    tab.payment_days.setValue(PAYMENT_DAYS_CUSTOM)

    tab.fill_data({"payment_days": ""})
    tab.fill_data({"payment_days": None})
    tab.fill_data({"payment_days": "мусор"})

    assert tab.get_data()["payment_days"] == PAYMENT_DAYS_CUSTOM


def test_payment_days_does_not_depend_on_amount(tab):
    """Срок оплаты не зависит ни от суммы, ни от режима ввода."""
    tab.payment_days.setValue(PAYMENT_DAYS_CUSTOM)
    tab.sum_wo_vat.setValue(TOTAL_230K)

    tab.amount_mode.setCurrentText(MODE_WITH_VAT)

    assert tab.get_data()["payment_days"] == PAYMENT_DAYS_CUSTOM


def test_clear_returns_payment_days_to_default(tab):
    tab.payment_days.setValue(PAYMENT_DAYS_CUSTOM)

    tab.clear()

    assert tab.get_data()["payment_days"] == PAYMENT_DAYS_DEFAULT
    assert tab.amount_mode_text() == MODE_WITHOUT_VAT


# ─────────────────────────────────────────────────────────────
# Вкладка → данные договора
# ─────────────────────────────────────────────────────────────

def _contract_of(tab: PriceTab) -> dict:
    """ContractData из одной вкладки «Стоимость» (остальные разделы пусты)."""
    data = collect_arenda_ts_data({"price": tab})
    assert isinstance(data, ContractData)
    return data.contract


@pytest.mark.parametrize("mode", [MODE_WITHOUT_VAT, MODE_WITH_VAT])
def test_payment_days_reaches_contract(qt_app, mode):
    """Ключ payment_days собирается в contract в обоих режимах."""
    tab = _tab(qt_app, mode)
    tab.sum_wo_vat.setValue(TOTAL_230K)
    tab.payment_days.setValue(PAYMENT_DAYS_CUSTOM)

    contract = _contract_of(tab)

    assert contract["payment_days"] == PAYMENT_DAYS_CUSTOM


@pytest.mark.parametrize("mode", [MODE_WITHOUT_VAT, MODE_WITH_VAT])
def test_contract_sums_are_the_same_in_both_modes(qt_app, mode):
    """
    В бланк уходит одно и то же итоговое число, каким бы ни был режим ввода.

    Логика генератора от режима не зависит: он читает sum_wo_vat / sum_vat /
    sum_total, и они согласованы между собой в любом режиме.
    """
    tab = _tab(qt_app, mode)
    tab.vat_rate.setCurrentText("22%")
    tab.sum_wo_vat.setValue(TOTAL_230K if mode == MODE_WITH_VAT else BASE_230K)

    contract = _contract_of(tab)

    assert contract["sum_wo_vat"] == pytest.approx(BASE_230K)
    assert contract["sum_vat"] == pytest.approx(VAT_230K)
    assert contract["sum_total"] == pytest.approx(TOTAL_230K)
    assert contract["price_without_vat"] == pytest.approx(BASE_230K)
    assert contract["price_with_vat"] == pytest.approx(TOTAL_230K)


def test_zero_payment_days_is_not_written_to_contract(qt_app):
    """Ноль в поле — «срок не задан»: в contract ключ не попадает."""
    tab = _tab(qt_app)
    tab.sum_wo_vat.setValue(TOTAL_230K)
    tab.payment_days.setValue(0)

    contract = _contract_of(tab)

    assert "payment_days" not in contract


def test_unparsable_payment_days_is_not_written(qt_app):
    """Мусор вместо срока в contract не попадает: бланк печатает число."""
    contract = collect_arenda_ts_data({
        "price": {"sum_wo_vat": BASE_SUM, "payment_days": "тридцать"},
    }).contract

    assert "payment_days" not in contract


def test_payment_days_from_recognition_payload(qt_app):
    """Срок оплаты приходит и от распознавания — строкой или числом."""
    tab = _tab(qt_app)

    tab.fill_data({"sum_wo_vat": BASE_SUM, "payment_days": "45"})
    assert tab.get_data()["payment_days"] == PAYMENT_DAYS_CUSTOM

    tab.fill_data({"payment_days": 30})
    assert tab.get_data()["payment_days"] == PAYMENT_DAYS_DEFAULT


# ─────────────────────────────────────────────────────────────
# Стык с генератором: сумма и срок оплаты в готовом договоре
# ─────────────────────────────────────────────────────────────

def _filled_tabs(qt_app, mode: str = MODE_WITH_VAT,
                 amount: float = TOTAL_230K) -> dict:
    """
    Семь вкладок окна аренды с минимально достаточными данными.

    Вкладка «Стоимость» заполняется через интерфейс (проверяется именно она),
    остальные разделы — словарями: у каждой вкладки свой набор тестов.

    :param amount: число, которое пользователь ввёл в поле суммы. Один и тот
        же итог 230 000,00 вводится по-разному: в режиме «С НДС» — числом
        230 000,00, в режиме «Без НДС» — числом 188 524,59.
    """
    from PyQt5.QtCore import QDate

    price = _tab(qt_app, mode)
    price.vat_rate.setCurrentText("22%")
    price.sum_wo_vat.setValue(amount)
    price.payment_days.setValue(PAYMENT_DAYS_CUSTOM)

    return {
        "lessee": {
            "carrier_type": "ООО",
            "full_name": "ООО «Арендатор-Тест»",
            "short_name": "ООО «АТ»",
            "inn": "7701234567",
            "kpp": "770101001",
            "ogrn": "1027700132195",
            "address": "г. Москва, ул. Арендаторская, д. 1",
            "account": "40702810000000000001",
            "bank": "ПАО Сбербанк",
            "bik": "044525225",
            "corr_account": "30101810400000000225",
            "director_position": "Генеральный директор",
            "director_name": "Петров Пётр Петрович",
        },
        "lessor": {
            "full_name": "ООО «Арендодатель-Тест»",
            "short_name": "ООО «АД»",
            "inn": "7707654321",
            "ogrn": "1027700261234",
            "address": "г. Москва, ул. Арендодательская, д. 2",
            "account": "40702810000000000002",
            "bank": "АО «Банк Второй»",
            "bik": "044525226",
            "corr_account": "30101810400000000226",
            "director_position": "Директор",
            "director_name": "Сидоров Сидор Сидорович",
        },
        "vehicle": {
            "contract_number": "01/2026",
            "contract_date": QDate(2026, 9, 23).toString("yyyy-MM-dd"),
            "lease_start_date": QDate(2026, 9, 28).toString("yyyy-MM-dd"),
            "lease_end_date": QDate(2026, 10, 5).toString("yyyy-MM-dd"),
            "tractor_brand": "DAF XF",
            "tractor_plate": "М342СА761",
            "tractor_type": "Седельный тягач",
            "trailer_brand": "KRONE SD",
            "trailer_plate": "ВК123478",
        },
        "route": {
            "route": "г. Москва — г. Казань",
            "loadings": [{"name": "Склад", "address": "г. Москва, ул. Складская, д. 1",
                          "date": "2026-09-28", "time_from": "08:00",
                          "time_to": "18:00"}],
            "unloadings": [{"name": "Приёмка", "address": "г. Казань, ул. Приёмная, д. 3",
                            "date": "2026-10-02"}],
        },
        "cargo": {
            "vehicles": [{"brand_model": "JETOUR T2", "vin": "EC3TEUMB0T0002608",
                          "loading_point": "Москва", "unloading_point": "Казань"}],
        },
        "crew": {
            "driver_full_name": "Иванов Иван Иванович",
            "driver_birth_date": "1980-01-01",
            "driver_passport": "18 22 926830",
            "driver_passport_issuer": "Отделом УФМС России по г. Москве",
            "driver_passport_issue_date": "2023-01-30",
            "driver_license": "99 36 123456",
            "driver_license_issue_date": "2020-01-01",
            "driver_registration_address": "г. Москва, ул. Тестовая, д. 1",
            "driver_phone": "+7 (999) 123-45-67",
        },
        "price": price,
    }


def _generated_document(qt_app, templates_dir, work_dir, mode=MODE_WITH_VAT,
                        folder: str = "", amount: float = TOTAL_230K):
    """
    Готовый договор из настоящей вкладки «Стоимость» и вкладок-словарей.

    Имя выходного файла зависит только от номера и даты договора, поэтому
    каждому варианту теста даётся своя папка — иначе второй договор затрёт
    первый, и оба теста прочитали бы один и тот же документ.
    """
    from core.contracts.arenda_ts.generator import ArendaTsGenerator

    if folder:
        work_dir = work_dir / folder
        work_dir.mkdir(parents=True, exist_ok=True)

    tabs = _filled_tabs(qt_app, mode, amount)
    contract_data = collect_arenda_ts_data(tabs)

    generator = ArendaTsGenerator(templates_dir=str(templates_dir))
    path = generator.generate(contract_data, output_dir=str(work_dir))
    return Document(path)


def test_vat_calculation_reaches_the_document(qt_app, templates_dir, work_dir):
    """
    Сумма, введённая «с НДС», доходит до бланка тремя согласованными числами.

    Ввод «230 000 с НДС» → в п. 4.1 бланка «188 524,59 без НДС», «НДС 22% —
    41 475,41» и «Итого с НДС: 230 000,00», каждое число ещё и прописью.
    """
    doc = _generated_document(qt_app, templates_dir, work_dir,
                              mode=MODE_WITH_VAT, folder="from_vat")
    text = _document_text(doc)

    assert ("– 188 524,59 руб. (Сто восемьдесят восемь тысяч пятьсот двадцать "
            "четыре рубля пятьдесят девять копеек) — стоимость без НДС;") in text
    assert ("– НДС 22% — 41 475,41 руб. (Сорок одна тысяча четыреста "
            "семьдесят пять рублей сорок одна копейка);") in text
    assert "Итого с НДС: 230 000,00 руб." in text
    assert "{{" not in text


def test_same_total_from_both_input_modes(qt_app, templates_dir, work_dir):
    """
    Оба режима ввода дают одинаковый итог в договоре: логика генератора
    от режима не зависит.

    «С НДС»: введено 230 000,00 — это итог.
    «Без НДС»: введено 188 524,59 — это база, налог начисляется сверху.
    Документы обязаны совпасть.
    """
    from_vat = _document_text(_generated_document(
        qt_app, templates_dir, work_dir, mode=MODE_WITH_VAT, folder="mode_vat"))
    from_base = _document_text(_generated_document(
        qt_app, templates_dir, work_dir, mode=MODE_WITHOUT_VAT,
        folder="mode_base", amount=BASE_230K))

    assert "Итого с НДС: 230 000,00 руб." in from_vat
    assert "Итого с НДС: 230 000,00 руб." in from_base
    assert from_vat == from_base


def test_generator_replacements_carry_the_payment_days(qt_app, templates_dir):
    """Ключ payment_days доходит до сборщика данных договора."""
    from core.contracts.arenda_ts.generator import ArendaTsGenerator

    tabs = _filled_tabs(qt_app)
    contract_data = collect_arenda_ts_data(tabs)

    assert contract_data.contract["payment_days"] == PAYMENT_DAYS_CUSTOM

    generator = ArendaTsGenerator(templates_dir=str(templates_dir))
    replacements = generator.build_replacements(contract_data)

    # Плейсхолдера {payment_days} в бланке аренды пока нет (см. STATE.md,
    # FIX-1) — проверяем, что значение собрано и готово к подстановке.
    assert contract_data.contract["payment_days"] == 45
    assert replacements["sum_total"] == "230\u00a0000,00"
