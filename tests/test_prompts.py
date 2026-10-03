#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты промптов по типам договоров (core/prompts, шаг 4 инфраструктуры).

Проверяют: заготовки типов существуют и импортируются, get_prompt() отдаёт
None для незаполненных промптов и для неизвестного типа, а сам механизм
умеет возвращать строку, когда PROMPT заполнен (проверяется на подменённом
модуле — реальные промпты появятся вместе с генераторами типов).
"""

import sys
import types

import pytest

from core.contracts.contract_types import ContractType
from core.prompts import PROMPT_MODULES, get_prompt


def test_all_ui_types_have_prompt_module():
    """У пяти пунктов выпадающего списка есть модуль промпта."""
    for key in ("perevozka", "formika", "logistiks_rus",
                "arenda_ts", "zayavka_excel"):
        assert key in PROMPT_MODULES, f"нет модуля промпта для {key!r}"


def test_havaly_maps_to_zayavka_excel():
    """«Хавалы» — это тип zayavka_excel, и промпт лежит в модуле havaly."""
    assert PROMPT_MODULES["zayavka_excel"] == "core.prompts.havaly"


def test_prompt_modules_are_importable():
    for key, module_name in PROMPT_MODULES.items():
        module = __import__(module_name, fromlist=["PROMPT"])
        assert hasattr(module, "PROMPT"), f"{module_name}: нет PROMPT ({key})"


def test_get_prompt_perevozka_is_string_or_none():
    """На этом шаге промпт перевозки не задан — распознавание на дефолте."""
    prompt = get_prompt("perevozka")
    assert prompt is None or isinstance(prompt, str)


@pytest.mark.parametrize("contract_type", [
    "formika", "logistiks_rus", "arenda_ts", "zayavka_excel",
])
def test_get_prompt_stubs_are_none(contract_type):
    assert get_prompt(contract_type) is None


@pytest.mark.parametrize("value", ["unknown", "", None, "expediciya"])
def test_get_prompt_unknown_type_is_none(value):
    assert get_prompt(value) is None


def test_get_prompt_returns_text_when_filled(monkeypatch):
    """Заполненный PROMPT отдаётся как строка (механизм рабочих промптов)."""
    stub = types.ModuleType("core.prompts._test_stub")
    stub.PROMPT = "  Текст промпта  "
    monkeypatch.setitem(sys.modules, "core.prompts._test_stub", stub)
    monkeypatch.setitem(
        PROMPT_MODULES, "test_type_xyz", "core.prompts._test_stub"
    )

    assert get_prompt("test_type_xyz") == "Текст промпта"


def test_get_prompt_survives_broken_module(monkeypatch):
    """Сломанный модуль промпта даёт None, а не исключение."""
    monkeypatch.setitem(
        PROMPT_MODULES, "test_type_broken", "core.prompts.no_such_module_xyz"
    )
    assert get_prompt("test_type_broken") is None


def test_expediciya_prompt_not_promised():
    """expediciya — служебная заглушка, отдельного промпта ей не заводим."""
    assert "expediciya" not in PROMPT_MODULES
    assert ContractType.EXPEDICIYA.value == "expediciya"
