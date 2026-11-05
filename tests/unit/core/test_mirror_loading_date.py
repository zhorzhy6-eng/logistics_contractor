#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Дата погрузки в зеркале «Экспедиторство → клиентский тип» (фикс 05.11.2026).

Дефект: в целевом окне вместо плановой даты погрузки из Экспедиторства
оставалось ЗАВОДСКОЕ значение поля — сегодняшнее число, то есть дата самого
договора. Карты зеркала (приватная часть) плановую дату не переносили вовсе.

Правило «где в источнике дата погрузки» теперь живёт в ядре
(``core.mirror.loading_plan_date``), а не в каждой карте по-своему: карты
клиентских типов только называют СВОЁ поле. Здесь проверяется само правило:

  * плановая дата погрузки главнее даты точки;
  * дата договора (``contract.date``) в дату погрузки не попадает никогда —
    ни основным путём, ни запасным;
  * запасной путь — дата ПЕРВОЙ погрузки (старые записи без плановой даты);
  * пустой и битый источник даёт "" (поле в план не уходит).

Qt здесь не нужен: это чистое ядро.
"""

import core.mirror as mirror_module
from core.mirror import loading_plan_date


def _source(contract=None, loadings=None):
    """Источник в том виде, в каком его отдаёт collect_source."""
    return {
        "contract": dict(contract or {}),
        "loadings": list(loadings or []),
    }


# ─────────────────────────────────────────────────────────────
# Что считается датой погрузки
# ─────────────────────────────────────────────────────────────

def test_plan_date_wins_over_point_date_and_contract_date():
    """Плановая дата погрузки главнее даты точки и даты договора."""
    source = _source(
        {"date": "2026-11-05", "loading_plan_date": "2026-11-07"},
        [{"address": "адрес", "date": "2026-11-06"}],
    )

    assert loading_plan_date(source) == "2026-11-07"


def test_contract_date_is_never_the_loading_date():
    """
    Дата договора — НЕ дата погрузки.

    Именно эта подмена и была дефектом: в цели оказывалось сегодняшнее
    число — дата договора. Даже когда других дат в источнике нет, правило
    обязано вернуть пусто, а не ``contract.date``.
    """
    source = _source({"date": "2026-11-05"})

    assert loading_plan_date(source) == ""
    assert loading_plan_date(source) != source["contract"]["date"]


def test_falls_back_to_first_loading_point():
    """Плановой даты нет — берётся дата ПЕРВОЙ погрузки."""
    source = _source(
        {"date": "2026-11-05"},
        [
            {"address": "первая", "date": "2026-11-06"},
            {"address": "вторая", "date": "2026-11-09"},
        ],
    )

    assert loading_plan_date(source) == "2026-11-06"


def test_first_point_without_date_does_not_use_second():
    """
    У первой погрузки даты нет — вторая её не подменяет.

    В целевом бланке одна дата погрузки и одна первая точка: дата второй
    погрузки к ней не относится. Лучше пустое поле, чем чужая дата.
    """
    source = _source(
        {},
        [
            {"address": "первая"},
            {"address": "вторая", "date": "2026-11-09"},
        ],
    )

    assert loading_plan_date(source) == ""


# ─────────────────────────────────────────────────────────────
# Пустой и битый источник
# ─────────────────────────────────────────────────────────────

def test_empty_source_gives_empty_string():
    """Нет данных — нет и даты погрузки (поле в план не уходит)."""
    assert loading_plan_date(None) == ""
    assert loading_plan_date({}) == ""
    assert loading_plan_date(_source()) == ""
    assert loading_plan_date({"contract": None, "loadings": None}) == ""


def test_broken_source_gives_empty_string():
    """Мусор вместо словарей не роняет зеркало и не даёт даты."""
    assert loading_plan_date({"contract": "мусор", "loadings": "мусор"}) == ""
    assert loading_plan_date({"loadings": [None, "строка", 42]}) == ""
    assert loading_plan_date({"contract": {"loading_plan_date": None}}) == ""


def test_values_are_stripped():
    """Значение приводится к тексту без обрамляющих пробелов."""
    source = _source({"loading_plan_date": "  2026-11-07  "})
    assert loading_plan_date(source) == "2026-11-07"


def test_date_is_taken_as_the_tab_gives_it():
    """Формат даты правило не меняет: вкладка отдаёт ISO, ISO и уходит."""
    source = _source({"loading_plan_date": "2026-11-07"})
    assert loading_plan_date(source) == "2026-11-07"


# ─────────────────────────────────────────────────────────────
# Контракт модуля
# ─────────────────────────────────────────────────────────────

def test_rule_is_public():
    """Правило доступно и как атрибут модуля, и через __all__."""
    assert mirror_module.loading_plan_date is loading_plan_date
    assert "loading_plan_date" in mirror_module.__all__
