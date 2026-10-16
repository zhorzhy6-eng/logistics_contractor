#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты вкладки «Перевозчик» — числовые реквизиты (ШАГ FIX-6, часть A).

Повод: в готовом договоре в блоке реквизитов появилось
«Корреспондентский счет БИК 044030786» — подпись поля приехала ВМЕСТЕ со
значением (распознавание или вставка из чужого документа). Проверяется,
что:

  * в поля ИНН / КПП / ОГРН / расчётный счёт / БИК / корр. счёт попадают
    ТОЛЬКО цифры — и при раскладке ответа (`fill_data`), и при сборке
    данных (`get_data`);
  * у числовых полей есть предел длины (БИК — 9 символов, счёт — 20),
    поэтому «грязная» вставка не превращается в реквизит договора;
  * значение без единой цифры поле не затирает: непонятный ответ модели
    не должен стирать введённые реквизиты;
  * правила промпта требуют от модели цифры без служебных слов.

Данные синтетические, ПДн нет. База и сеть не нужны.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from core.gigachat_client import GigaChatClient  # noqa: E402
from ui.tabs.carrier_tab import CarrierTab, _normalize_digits  # noqa: E402

#: «Грязные» значения — ровно то, что приходит из документа/распознавания.
DIRTY = {
    "bik": "Корреспондентский счет БИК 044030786",
    "correspondent_account": "Корр. счёт: 30101810600000000786",
    "bank_account": "Расчётный счёт № 40702810032000023498",
    "inn": "ИНН 7802102105",
    "kpp": "КПП 780201001",
    "ogrn": "ОГРН 1027700132195",
}

CLEAN = {
    "bik": "044030786",
    "correspondent_account": "30101810600000000786",
    "bank_account": "40702810032000023498",
    "inn": "7802102105",
    "kpp": "780201001",
    "ogrn": "1027700132195",
}


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qt_app) -> CarrierTab:
    return CarrierTab()


# ─────────────────────────────────────────────────────────────
# Нормализация значений
# ─────────────────────────────────────────────────────────────

def test_normalize_digits_keeps_only_digits():
    assert _normalize_digits("Корреспондентский счет БИК 044030786") == "044030786"
    assert _normalize_digits("30101810600000000786") == "30101810600000000786"
    assert _normalize_digits("044 525 225") == "044525225"
    assert _normalize_digits("7701-234-567") == "7701234567"
    assert _normalize_digits(None) == ""
    assert _normalize_digits("нет цифр") == ""
    assert _normalize_digits(0) == "0"


def test_numeric_fields_have_length_caps(tab):
    """
    У числовых полей есть предел длины: «грязная» вставка не станет договором.

    Предел считается по ПОДПИСИ и цифрам вместе, поэтому вставленный руками
    текст обрезается ещё в поле (БИК — 9 символов, счёт — 20). Полноценно
    нормализует значения `fill_data`: он кладёт в поле уже готовые цифры.
    """
    assert tab.bik.line_edit.maxLength() == 9
    assert tab.correspondent_account.line_edit.maxLength() == 20
    assert tab.bank_account.line_edit.maxLength() == 20
    assert tab.inn.line_edit.maxLength() == 12
    assert tab.kpp.line_edit.maxLength() == 9
    assert tab.ogrn.line_edit.maxLength() == 15


# ─────────────────────────────────────────────────────────────
# fill_data: грязные значения из распознавания
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", sorted(DIRTY))
def test_fill_data_normalizes_numeric_requisites(tab, field):
    """«Корреспондентский счет БИК 044030786» в поле БИК → «044030786»."""
    tab.fill_data({field: DIRTY[field]})

    assert getattr(tab, field).text() == CLEAN[field]
    assert getattr(tab, field).text().isdigit()


def test_fill_data_normalizes_all_requisites_at_once(tab):
    """Полный блок реквизитов с подписями: в полях только цифры."""
    tab.fill_data(dict(DIRTY, bank_name="Филиал «АЛЬФА-БАНК»"))

    data = tab.get_data()
    for field in DIRTY:
        assert data[field] == CLEAN[field], f"{field}: {data[field]!r}"

    # Подпись банка не тронута — нормализуются только числовые поля.
    assert tab.bank_name.text() == "Филиал «АЛЬФА-БАНК»"


def test_fill_data_does_not_wipe_on_value_without_digits(tab):
    """Ответ без единой цифры поле не затирает."""
    tab.bik.setText("044030786")

    tab.fill_data({"bik": "не разобралось"})

    assert tab.bik.text() == "044030786"


def test_fill_data_keeps_empty_values_untouched(tab):
    """Пустое значение не сбрасывает введённое (распознавание неполное)."""
    tab.correspondent_account.setText("30101810600000000786")

    tab.fill_data({"correspondent_account": "", "bik": None})

    assert tab.correspondent_account.text() == "30101810600000000786"


# ─────────────────────────────────────────────────────────────
# get_data: сборка данных идёт в договор
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", sorted(DIRTY))
def test_get_data_returns_digits_only(tab, field):
    """
    Значение уходит в договор цифрами — даже если в поле есть лишнее.

    Поле заполняется через `fill_data` (рабочий путь: распознавание,
    справочник, импорт документа), после чего проверяется сборка данных.
    """
    tab.fill_data({field: DIRTY[field]})

    value = tab.get_data()[field]

    assert value == CLEAN[field]
    assert value.isdigit()


def test_get_data_strips_spaces_and_dashes(tab):
    """
    Пробелы и дефисы оператор ставит для читаемости — в договор не идут.

    Проверяется на ИНН: у него предел 12 символов, поэтому и «грязная»
    запись целиком помещается в поле (у БИК и счетов предел впритык).
    """
    tab.inn.setText("7701-234-567")

    assert tab.get_data()["inn"] == "7701234567"


def test_get_data_drops_letters_typed_by_hand(tab):
    """Текст в поле (без цифр) в договор не уходит — остаётся пустая строка."""
    tab.correspondent_account.setText("не разобралось")

    assert tab.get_data()["correspondent_account"] == ""


def test_get_data_empty_requisites_stay_empty(tab):
    """Пустые поля остаются пустыми: ничего не выдумываем."""
    data = tab.get_data()

    for field in DIRTY:
        assert data[field] == ""


# ─────────────────────────────────────────────────────────────
# Правило промпта: только цифры (ШАГ FIX-6, часть A2)
# ─────────────────────────────────────────────────────────────

def test_prompt_forbids_service_words_in_requisites():
    """
    Промпт распознавания перевозки (SYSTEM_PROMPT) требует только цифры.

    Промпт перевозки живёт в core/gigachat_client.py: у типа
    `core/prompts/perevozka.py` своего текста нет (PROMPT = None) — правило
    извлечения реквизитов одно на всех, и оно здесь.
    """
    prompt = GigaChatClient.SYSTEM_PROMPT

    assert "НИКОГДА не включай в эти поля слова «БИК»" in prompt
    assert "Корреспондентский счет" in prompt
    assert "correspondent_account (20 цифр)" in prompt
    assert "bik (9 цифр)" in prompt
