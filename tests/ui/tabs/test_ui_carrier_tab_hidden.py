#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Перевозчик»: блок «Тип перевозчика» скрыт (шаг «Ставки НДС в UI»).

Вид стороны и ставку НДС оператор выбирает на вкладке «Договор» (поля
«Форма» и «Ставка НДС»). На вкладке «Перевозчик» этот блок больше не
показывается, но НЕ удалён: из скрытых полей по-прежнему собираются
`carrier_type` и `vat_rate` — их читают справочник, распознавание и
вкладка «Договор».

Что проверяется:

  * поля «Тип» и «Ставка НДС, %» СКРЫТЫ (виджеты на месте, но не видны);
  * `get_data()` отдаёт ровно те же ключи, что и раньше;
  * `fill_data()` заполняет скрытые поля;
  * `apply_entity_type()` (вид из справочника) работает как раньше.

Данные синтетические, ПДн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from ui.tabs.carrier_tab import CarrierTab  # noqa: E402

#: Ключи, которые вкладка отдавала до этого шага. Скрытие полей не должно
#: ни убрать, ни переименовать ни одного из них.
EXPECTED_KEYS = {
    "full_name", "short_name", "legal_address", "actual_address", "bank_name",
    "director_name", "director_position", "phone", "email",
    "license_number", "license_date", "carrier_type", "vat_rate",
    "inn", "kpp", "ogrn", "bank_account", "bik", "correspondent_account",
}


@pytest.fixture(scope="module")
def qapp():
    """Одно приложение Qt на модуль (offscreen)."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qapp) -> CarrierTab:
    return CarrierTab()


@pytest.fixture
def shown(tab, qapp) -> CarrierTab:
    """
    Показанная вкладка: `isVisible()` у скрытого поля виден только тогда,
    когда показаны и вкладка, и её родители.
    """
    tab.show()
    qapp.processEvents()
    yield tab
    tab.hide()


# ─────────────────────────────────────────────────────────────
# Поля скрыты, а не удалены
# ─────────────────────────────────────────────────────────────

def test_carrier_type_field_is_hidden(shown):
    """Поле «Тип» скрыто — виджета не видно, но он существует."""
    assert shown.carrier_type is not None
    assert shown.carrier_type.isVisible() is False


def test_vat_rate_field_is_hidden(shown):
    """Поле «Ставка НДС, %» скрыто вместе с блоком."""
    assert shown.vat_rate is not None
    assert shown.vat_rate.isVisible() is False


def test_whole_block_is_hidden(shown):
    """Скрыт весь блок «Тип перевозчика» — вместе с подписями полей."""
    assert shown.type_group.isVisible() is False
    assert shown.type_group.isHidden() is True


def test_labels_of_hidden_fields_are_hidden_too(shown):
    """Подписи «Тип» и «Ставка НДС, %» тоже не видны (скрыт блок)."""
    layout = shown.type_group.layout()
    labels = [layout.labelForField(field) for field in
              (shown.carrier_type, shown.vat_rate)]

    assert all(label is not None for label in labels), labels
    for label in labels:
        assert label.isVisible() is False


def test_hidden_fields_still_hold_values(tab):
    """Скрытые поля живые: значения в них есть и меняются (не заглушки)."""
    assert tab.carrier_type.currentText() == "ООО (с НДС)"
    assert tab.vat_rate.text() == "22"

    tab.carrier_type.setCurrentText("ИП без НДС")
    tab.vat_rate.setText("0")

    assert tab.carrier_type.currentText() == "ИП без НДС"
    assert tab.vat_rate.text() == "0"


# ─────────────────────────────────────────────────────────────
# Стыки: get_data, fill_data, apply_entity_type
# ─────────────────────────────────────────────────────────────

def test_get_data_returns_the_same_keys(tab):
    """Набор ключей не изменился — стыки со справочником и договором целы."""
    assert set(tab.get_data()) == EXPECTED_KEYS


def test_get_data_carries_hidden_field_values(tab):
    """Значения скрытых полей уходят в данные как раньше."""
    tab.carrier_type.setCurrentText("ИП без НДС")
    tab.vat_rate.setText("0")

    data = tab.get_data()

    assert data["carrier_type"] == "ИП без НДС"
    assert data["vat_rate"] == "0"


def test_fill_data_fills_hidden_fields(tab):
    """Загрузка записи заполняет скрытые поля (и по-прежнему всё остальное)."""
    tab.fill_data({
        "full_name": "ООО «Ромашка»",
        "carrier_type": "ИП без НДС",
        "vat_rate": "0",
    })

    assert tab.carrier_type.currentText() == "ИП без НДС"
    assert tab.vat_rate.text() == "0"
    assert tab.full_name.text() == "ООО «Ромашка»"


def test_fill_data_accepts_entity_type(tab):
    """Вид стороны приходит и ключом entity_type (так его хранит справочник)."""
    tab.fill_data({"entity_type": "ИП"})

    assert tab.carrier_type.currentText() == "ИП с НДС"


def test_apply_entity_type_sets_hidden_field(tab):
    """Вид из справочника ставится в скрытое поле (ИП из базы — ИП)."""
    assert tab.apply_entity_type("ИП") is True

    assert tab.carrier_type.currentText() == "ИП с НДС"


def test_kpp_requirement_follows_hidden_field(tab):
    """КПП обязателен только у ООО — правило работает и со скрытым полем."""
    tab.apply_entity_type("ООО")
    assert tab.kpp.is_required() is True

    tab.carrier_type.setCurrentText("ИП с НДС")
    assert tab.kpp.is_required() is False


def test_clear_returns_hidden_defaults(tab):
    """Очистка возвращает скрытым полям значения по умолчанию."""
    tab.carrier_type.setCurrentText("ИП без НДС")
    tab.vat_rate.setText("0")

    tab.clear()

    assert tab.carrier_type.currentText() == "ООО (с НДС)"
    assert tab.vat_rate.text() == "22"
