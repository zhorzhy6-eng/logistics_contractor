#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты единого парсера дат (core/dates.py).

Фиксируют поведение, которое раньше было размазано по пяти копиям кода
(вкладки водителя/перевозчика/договора, PasteableDateEdit, recognizer).
"""

from datetime import date, datetime

import pytest

from core.dates import (
    DATE_FORMATS,
    day_of_month,
    month_name,
    parse_date,
    to_day_month,
    to_display,
    to_iso,
)


# ─────────────────────────────────────────────────────────────
# Все поддерживаемые форматы
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    ("2023-01-26", "2023-01-26"),        # ISO
    ("26.01.2023", "2023-01-26"),        # русский
    ("26/01/2023", "2023-01-26"),        # слэш
    ("2023.01.26", "2023-01-26"),        # точки ISO
    ("26-01-2023", "2023-01-26"),        # дефис
    ("20230126", "2023-01-26"),          # слитно
    ("26 01 2023", "2023-01-26"),        # пробелы
])
def test_all_supported_formats(value, expected):
    """Каждый из семи форматов разбирается в одну и ту же дату."""
    assert to_iso(value) == expected


@pytest.mark.parametrize("value", [
    "2023-01-26T00:00:00",
    "2023-01-26T15:30:45",
    "2023-01-26 00:00:00",
    "2023-01-26 23:59",
])
def test_dates_with_time(value):
    """Время отбрасывается."""
    assert to_iso(value) == "2023-01-26"


def test_date_formats_constant_complete():
    """В модуле перечислены все семь форматов."""
    assert len(DATE_FORMATS) == 7


# ─────────────────────────────────────────────────────────────
# Объекты datetime/date
# ─────────────────────────────────────────────────────────────

def test_datetime_object():
    assert to_iso(datetime(2023, 1, 26, 15, 30)) == "2023-01-26"


def test_date_object():
    assert to_iso(date(2023, 1, 26)) == "2023-01-26"


def test_parse_date_returns_datetime():
    parsed = parse_date("26.01.2023")
    assert isinstance(parsed, datetime)
    assert (parsed.year, parsed.month, parsed.day) == (2023, 1, 26)


# ─────────────────────────────────────────────────────────────
# Некорректные и пустые значения
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    None, "", "   ", "\t", "\n",
    "null", "NULL", "None", "none", "nan", "undefined", "nil", "yyyy-mm-dd",
])
def test_empty_values_return_none(value):
    """Пустые и «пустые по смыслу» значения — None."""
    assert parse_date(value, warn=False) is None


@pytest.mark.parametrize("value", [
    "не дата", "abc", "32.13.2023", "2023-13-45", "26/13/2023",
    "----", "2023", "26.01", "01.01.23",
])
def test_invalid_dates_return_none(value):
    """Мусор и несуществующие даты — None."""
    assert parse_date(value, warn=False) is None


def test_invalid_date_with_warning(caplog):
    """При разборе мусора с warn=True пишется предупреждение без самого значения-ПДн."""
    with caplog.at_level("WARNING", logger="core.dates"):
        assert parse_date("не дата") is None
    assert "Не удалось распознать дату" in caplog.text


def test_invalid_date_without_warning(caplog):
    """warn=False — тихий режим (используется в форматтерах)."""
    with caplog.at_level("WARNING", logger="core.dates"):
        assert parse_date("не дата", warn=False) is None
    assert "Не удалось распознать дату" not in caplog.text


# ─────────────────────────────────────────────────────────────
# Краевые случаи: кавычки, пробелы, лишние символы
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    ('"2023-01-26"', "2023-01-26"),      # кавычки из JSON
    ("  2023-01-26  ", "2023-01-26"),    # пробелы по краям
    ("2023-01-26 ", "2023-01-26"),
    (" 26.01.2023", "2023-01-26"),
])
def test_whitespace_and_quotes(value, expected):
    assert to_iso(value) == expected


def test_leading_zeros_preserved():
    assert to_iso("05.01.2023") == "2023-01-05"


def test_leap_year_valid():
    assert to_iso("29.02.2024") == "2024-02-29"


def test_leap_year_invalid():
    """29 февраля невисокосного года — не дата."""
    assert parse_date("29.02.2023", warn=False) is None


# ─────────────────────────────────────────────────────────────
# Форматтеры
# ─────────────────────────────────────────────────────────────

def test_to_display():
    assert to_display("2023-01-26") == "26.01.2023"


def test_to_day_month():
    assert to_day_month("2023-09-24") == "24.09"


def test_day_of_month():
    assert day_of_month("2023-01-05") == "05"


@pytest.mark.parametrize("value, expected", [
    ("2026-01-15", "января"),
    ("2026-05-01", "мая"),
    ("2026-09-23", "сентября"),
    ("2026-12-31", "декабря"),
])
def test_month_name(value, expected):
    assert month_name(value) == expected


def test_month_name_default():
    """При неразобранной дате — историческое «сентября»."""
    assert month_name(None) == "сентября"
    assert month_name("") == "сентября"
    assert month_name("мусор") == "сентября"


def test_formatters_return_default_on_bad_input():
    assert to_iso("мусор") == ""
    assert to_iso("мусор", default="—") == "—"
    assert to_display(None) == ""
    assert day_of_month("мусор") == ""
    assert to_day_month("мусор") == ""


def test_formatters_do_not_log_warnings(caplog):
    """Форматтеры с warn=False не спамят в лог."""
    with caplog.at_level("WARNING", logger="core.dates"):
        to_iso("мусор")
        to_display("мусор")
        day_of_month("мусор")
    assert "Не удалось распознать дату" not in caplog.text
