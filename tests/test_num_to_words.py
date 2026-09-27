#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты конвертера «сумма прописью» (core/num_to_words.py).

Проверяются склонения рублей и копеек, диапазон значений и краевые случаи.
"""

import pytest

from core.num_to_words import (
    KOPEKS_FORMS,
    RUBLES_FORMS,
    _plural_form,
    _triple_to_words,
    amount_to_words,
)


# ─────────────────────────────────────────────────────────────
# Точные значения (зафиксированное поведение)
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("amount, expected", [
    (0, "Ноль рублей ноль копеек"),
    (0.01, "Ноль рублей одна копейка"),
    (1, "Один рубль ноль копеек"),
    (2, "Два рубля ноль копеек"),
    (5, "Пять рублей ноль копеек"),
    (11, "Одиннадцать рублей ноль копеек"),
    (21, "Двадцать один рубль ноль копеек"),
    (22, "Двадцать два рубля ноль копеек"),
    (25, "Двадцать пять рублей ноль копеек"),
    (100, "Сто рублей ноль копеек"),
    (101, "Сто один рубль ноль копеек"),
    (1000, "Одна тысяча рублей ноль копеек"),
    (1001, "Одна тысяча один рубль ноль копеек"),
    (5000, "Пять тысяч рублей ноль копеек"),
    (88000, "Восемьдесят восемь тысяч рублей ноль копеек"),
    (260000, "Двести шестьдесят тысяч рублей ноль копеек"),
    (400000, "Четыреста тысяч рублей ноль копеек"),
    (488000, "Четыреста восемьдесят восемь тысяч рублей ноль копеек"),
    (1000000, "Один миллион рублей ноль копеек"),
    (196721.31, "Сто девяносто шесть тысяч семьсот двадцать один рубль "
                "тридцать одна копейка"),
    (213114.75, "Двести тринадцать тысяч сто четырнадцать рублей "
                "семьдесят пять копеек"),
    (390000.50, "Триста девяносто тысяч рублей пятьдесят копеек"),
    (46885.25, "Сорок шесть тысяч восемьсот восемьдесят пять рублей "
               "двадцать пять копеек"),
    (1234567.89, "Один миллион двести тридцать четыре тысячи пятьсот "
                 "шестьдесят семь рублей восемьдесят девять копеек"),
])
def test_exact_amount_to_words(amount, expected):
    assert amount_to_words(amount) == expected


# ─────────────────────────────────────────────────────────────
# Склонения
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("number, expected", [
    (1, "рубль"), (2, "рубля"), (3, "рубля"), (4, "рубля"),
    (5, "рублей"), (10, "рублей"), (11, "рублей"), (12, "рублей"),
    (14, "рублей"), (15, "рублей"), (20, "рублей"), (21, "рубль"),
    (22, "рубля"), (25, "рублей"), (101, "рубль"), (111, "рублей"),
    (1000, "рублей"), (1001, "рубль"), (1002, "рубля"),
])
def test_ruble_declension(number, expected):
    assert _plural_form(number, RUBLES_FORMS) == expected


@pytest.mark.parametrize("number, expected", [
    (1, "копейка"), (2, "копейки"), (4, "копейки"), (5, "копеек"),
    (11, "копеек"), (21, "копейка"), (22, "копейки"), (25, "копеек"),
])
def test_kopek_declension(number, expected):
    assert _plural_form(number, KOPEKS_FORMS) == expected


def test_kopeks_written_as_words():
    assert amount_to_words(1.01).endswith("одна копейка")
    assert amount_to_words(1.02).endswith("две копейки")
    assert amount_to_words(1.05).endswith("пять копеек")
    assert amount_to_words(1.11).endswith("одиннадцать копеек")
    assert amount_to_words(1.21).endswith("двадцать одна копейка")
    assert amount_to_words(1.00).endswith("ноль копеек")


def test_wording_of_rubles_in_phrase():
    assert "Один рубль" in amount_to_words(1)
    assert "Два рубля" in amount_to_words(2)
    assert "Пять рублей" in amount_to_words(5)


# ─────────────────────────────────────────────────────────────
# Диапазон 0 … 1 000 000
# ─────────────────────────────────────────────────────────────

def test_range_produces_non_empty_result():
    """На всём диапазоне функция не падает и выдаёт осмысленную строку."""
    for amount in range(0, 1_000_001, 7_777):
        text = amount_to_words(amount)
        assert text, f"пустой результат для {amount}"
        assert "рубл" in text


@pytest.mark.parametrize("amount", [0, 1, 999, 1000, 999_999, 1_000_000])
def test_range_boundaries(amount):
    text = amount_to_words(amount)
    assert text.endswith("копеек") or text.endswith("копейка") or text.endswith("копейки")


def test_millions():
    assert amount_to_words(2_000_000).startswith("Два миллиона")
    assert amount_to_words(5_000_000).startswith("Пять миллионов")


def test_triple_to_words_female():
    """Тысячи — женский род: «одна тысяча», «две тысячи»."""
    assert _triple_to_words(1, female=True) == "одна"
    assert _triple_to_words(2, female=True) == "две"
    assert _triple_to_words(1, female=False) == "один"
    assert _triple_to_words(2, female=False) == "два"


# ─────────────────────────────────────────────────────────────
# Округление и знак
# ─────────────────────────────────────────────────────────────

def test_rounding_up_to_ruble():
    """99.999 копеек догоняет до целого рубля."""
    assert amount_to_words(99.999) == "Сто рублей ноль копеек"


def test_rounding_half_kopeck():
    assert amount_to_words(0.005) == "Ноль рублей одна копейка"


def test_negative_amount():
    text = amount_to_words(-100)
    assert text.startswith("Минус")
    assert "Сто рублей" in text


# ─────────────────────────────────────────────────────────────
# Краевые случаи
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["abc", None, "", [], {}, "12abc"])
def test_invalid_input_returns_empty(value):
    assert amount_to_words(value) == ""


def test_string_number_is_accepted():
    assert amount_to_words("1000") == "Одна тысяча рублей ноль копеек"


def test_other_currency_returns_number_only():
    assert amount_to_words(1234.56, currency="usd") == "Одна тысяча двести тридцать четыре"


def test_zero():
    assert amount_to_words(0) == "Ноль рублей ноль копеек"
