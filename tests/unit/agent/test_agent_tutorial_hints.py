#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Подсказки режима «Обучение» (задание: система обучения, часть 4).

Что проверяется
---------------
1. **Чистая логика** ``_build_hint``: по данным формы и типу договора
   возвращает подсказку о следующем шаге; пустая форма → «откройте форму»;
   заполненная → «создайте документ».
2. **Следующий шаг совпадает с обязательными полями** (proactivity.REQUIRED):
   у «Экспедиторства» первым подсказывается перевозчик, у «Аренды» —
   арендодатель; подсказка напоминает, что запись можно подтянуть из базы.
3. **Точка входа** ``tutorial_step_hint``: без окна даёт общую подсказку и
   не падает.
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.tutorial_hints import (  # noqa: E402
    PULLABLE_SECTIONS,
    _build_hint,
    onboarding_decision,
    tutorial_step_hint,
)


class FakeContext:
    """Подставной контекст агента: только окно и тип договора."""

    def __init__(self, window=None, contract_type=""):
        self.window = window
        self.contract_type = contract_type

    def current_window(self):
        return self.window


# ─────────────────────────────────────────────────────────────
# Чистая логика подсказки
# ─────────────────────────────────────────────────────────────

def test_hint_empty_form_mentions_first_required_field():
    """Пустая форма Экспедиторства: подсказывается перевозчик."""
    sections = {"carrier_tab": {"full_name": "", "inn": ""}}
    hint = _build_hint(sections, "perevozka")
    assert "наименование перевозчика" in hint
    assert "Перевозчик" in hint


def test_hint_reminds_pullable_section():
    """Раздел, который можно подтянуть из базы, — с напоминанием."""
    hint = _build_hint({"carrier_tab": {"full_name": "", "inn": ""}}, "perevozka")
    assert "справочника" in hint


def test_hint_arenda_starts_with_lessor():
    """У Аренды первым подсказывается Арендодатель, а не Арендатор."""
    sections = {
        "lessor_tab": {"full_name": "", "inn": ""},
        "lessee_tab": {"full_name": "", "inn": ""},
    }
    hint = _build_hint(sections, "arenda_ts")
    assert "арендодателя" in hint
    assert "Арендодатель" in hint


def test_hint_complete_form():
    """Когда обязательные и рекомендуемые поля заполнены — «создайте документ»."""
    sections = {
        "carrier_tab": {
            "full_name": "ООО «Ромашка»", "inn": "7701234567",
            "kpp": "770101001", "legal_address": "г. Москва",
            "director_name": "Петров Пётр Петрович",
        }
    }
    hint = _build_hint(sections, "perevozka")
    assert "создайте документ" in hint


def test_hint_no_form():
    """Без формы — общая подсказка открыть форму."""
    assert "форму" in _build_hint({}, "perevozka")


def test_hint_ignores_empty_contract_type():
    """Пустой тип договора не роняет подсказку (fallback на перевозку)."""
    hint = _build_hint({"carrier_tab": {"full_name": "", "inn": ""}}, "")
    assert hint


# ─────────────────────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────────────────────

def test_step_hint_without_window():
    """Без окна подсказка не падает и возвращает общую фразу."""
    hint = tutorial_step_hint(FakeContext())
    assert "форму" in hint


def test_step_hint_never_raises_on_bad_context():
    """Любой мусорный контекст не роняет подсказку."""
    hint = tutorial_step_hint(object())
    assert hint


def test_pullable_sections_are_titles():
    """Список подтягиваемых разделов — русские названия, а не ключи."""
    assert "Перевозчик" in PULLABLE_SECTIONS
    assert "Арендодатель" in PULLABLE_SECTIONS
    assert "Водитель" in PULLABLE_SECTIONS


# ─────────────────────────────────────────────────────────────
# Предложение обучения при первом запуске
# ─────────────────────────────────────────────────────────────

def test_onboarding_decision_offer():
    """Первый запуск, режим выключен — предложить."""
    assert onboarding_decision({"tutorial_seen": False, "tutorial_mode": False}) == "offer"


def test_onboarding_decision_none():
    """Предложение уже показывалось — ничего не делать."""
    assert onboarding_decision({"tutorial_seen": True, "tutorial_mode": False}) == "none"


def test_onboarding_decision_mark_seen():
    """Режим уже включён — только поставить отметку, не спрашивать."""
    assert onboarding_decision({"tutorial_seen": False, "tutorial_mode": True}) == "mark_seen"


def test_onboarding_decision_empty_is_offer():
    """Пустые настройки — это первый запуск: предложить."""
    assert onboarding_decision({}) == "offer"
