#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты сравнения формы с типовым договором (legal_compare.py).

Главное, что здесь охраняется, — РАЗДЕЛЕНИЕ нормы и практики:

* типовой договор честно называет себя практикой, а норма права в нём
  указана только там, где она есть в базе (НДС);
* 45 дней оплаты — «расхождение с типовым», а не «нарушение закона»;
* неизвестный тип не получает чужой типовой бланк;
* форма при сравнении не меняется.
"""

from __future__ import annotations

import copy
import json

import pytest

from services.agent.tools import legal_compare as cmp


#: Значения формы, совпадающие с типовым договором перевозки.
MATCHING = {
    "contract_tab.number": "ТЛ-1",
    "contract_tab.date": "2026-10-17",
    "contract_tab.route": "Москва — Тула",
    "contract_tab.price_with_vat": 100000,
    "contract_tab.payment_days": 30,
    "contract_tab.vat_rate": "22%",
}


# ─────────────────────────────────────────────────────────────
# 1. Типовые договоры проекта
# ─────────────────────────────────────────────────────────────

def test_typical_files_exist_for_known_types():
    """У перевозки и аренды есть файл типового договора."""
    assert cmp.typical_path("perevozka") is not None
    assert cmp.typical_path("arenda_ts") is not None
    assert cmp.typical_path("perevozka").is_file()
    assert cmp.typical_path("arenda_ts").is_file()


def test_unknown_type_has_no_typical():
    """Неизвестный тип не получает чужой бланк: сравнивать не с чем."""
    assert cmp.typical_path("чужой") is None
    assert cmp.load_typical("чужой") is None


def test_typical_is_practice_not_law():
    """
    Типовой договор не выдаёт свои значения за норму права.

    В задаче предлагалось указать источником «УАТ ст. 34» для 30 дней
    оплаты и пени 0,1%. В правовой базе этого нет: УАТ ст. 34 — об
    ответственности, а срок оплаты отдан соглашению сторон (ГК РФ ст. 790
    п. 1). Тест сторожит честность файла.
    """
    typical = cmp.load_typical("perevozka")
    source = str(typical.get("source") or "")
    assert "УАТ ст. 34" not in source
    assert "практика" in str(typical.get("_comment") or "").casefold()
    assert "УАТ ст. 34" not in str(typical.get("note") or "")


def test_typical_values_are_present():
    """В типовом есть срок оплаты, пеня, НДС и обязательные поля."""
    typical = cmp.load_typical("perevozka")
    assert typical["payment_days"] == 30
    assert typical["penalty_rate"] == pytest.approx(0.1)
    assert typical["vat_rate"] == 22
    assert typical["required_fields"] == ["number", "date", "route", "price"]


def test_broken_typical_file_does_not_crash(tmp_path, monkeypatch):
    """Битый файл типового — None и запись в журнал, а не падение."""
    broken = tmp_path / "typical.json"
    broken.write_text("{ не json", encoding="utf-8")
    monkeypatch.setitem(cmp.TYPICAL_FILES, "perevozka", "typical.json")
    monkeypatch.setattr(cmp, "TEMPLATES_DIR", tmp_path)
    assert cmp.load_typical("perevozka") is None


# ─────────────────────────────────────────────────────────────
# 2. Сравнение
# ─────────────────────────────────────────────────────────────

def test_matching_form_has_no_discrepancies():
    """Форма по типовому — отличий нет."""
    result = cmp.compare_with_typical("perevozka", values=MATCHING)
    assert result["ok"] is True
    assert result["discrepancies"] == []
    assert result["matched"] >= 4
    assert "Отличий" in result["summary"]


def test_payment_days_difference_is_discrepancy_not_violation():
    """45 дней — расхождение с типовым, и в тексте это прямо сказано."""
    values = dict(MATCHING, **{"contract_tab.payment_days": 45})
    result = cmp.compare_with_typical("perevozka", values=values)
    assert len(result["discrepancies"]) == 1
    item = result["discrepancies"][0]
    assert item["field"] == "contract_tab.payment_days"
    assert item["current"] == 45
    assert item["typical"] == 30
    assert "45" in item["message"] and "30" in item["message"]
    assert "нарушения закона" in result["summary"]


def test_vat_difference_is_discrepancy():
    """Ставка НДС тоже сравнивается с типовой."""
    values = dict(MATCHING, **{"contract_tab.vat_rate": "20%"})
    result = cmp.compare_with_typical("perevozka", values=values)
    fields = [item["field"] for item in result["discrepancies"]]
    assert "contract_tab.vat_rate" in fields


def test_empty_required_field_is_discrepancy():
    """Пустое обязательное поле типового — расхождение с внятным текстом."""
    values = dict(MATCHING, **{"contract_tab.route": ""})
    result = cmp.compare_with_typical("perevozka", values=values)
    item = next(item for item in result["discrepancies"]
                if item["field"] == "contract_tab.route")
    assert "обязательное поле" in item["message"]


def test_arenda_uses_its_own_fields():
    """У аренды поля лежат в price_tab/route_tab — сравнение их находит."""
    values = {
        "price_tab.payment_days": 30,
        "price_tab.vat_rate": "22%",
        "route_tab.route": "Москва — Тула",
        "contract_tab.number": "А-1",
        "contract_tab.date": "2026-10-17",
        "price_tab.sum_total": 100000,
    }
    result = cmp.compare_with_typical("arenda_ts", values=values)
    assert result["typical"].startswith("Типовой договор аренды")
    assert result["discrepancies"] == []


def test_missing_field_goes_to_skipped():
    """Поля пени в форме нет — оно пропущено, а не «нарушено»."""
    result = cmp.compare_with_typical("perevozka", values=MATCHING)
    skipped = {item["field"]: item for item in result["skipped"]}
    assert "contract_tab.penalty_rate" in skipped
    assert "нет" in skipped["contract_tab.penalty_rate"]["reason"]


def test_unknown_type_says_nothing_to_compare():
    """Неизвестный тип — понятный итог, а не пустой список молча."""
    result = cmp.compare_with_typical("чужой", values=MATCHING)
    assert result["discrepancies"] == []
    assert "сравнивать не с чем" in result["summary"]


def test_empty_form_says_nothing_to_compare():
    """Пустая форма: сравнивать нечего."""
    result = cmp.compare_with_typical("perevozka", values={})
    assert result["discrepancies"] == []
    assert "нечего" in result["summary"].lower()


def test_form_is_not_modified():
    """Сравнение только читает: значения формы остаются прежними."""
    values = dict(MATCHING, **{"contract_tab.payment_days": 45})
    before = copy.deepcopy(values)
    cmp.compare_with_typical("perevozka", values=values)
    assert values == before


def test_window_values_are_used():
    """Значения берутся из окна, если словарь не передан."""

    class Tab:
        def __init__(self, data):
            self._data = data

        def get_data(self):
            return dict(self._data)

    class Window:
        CONTRACT_TYPE = "perevozka"

        def __init__(self):
            # Вкладка отдаёт поля БЕЗ префикса раздела: префикс добавляет
            # collect_form_data, и двойной префикс дал бы «contract_tab.
            # contract_tab.number» — такого поля в форме нет.
            self._tabs = {"contract_tab": Tab({
                "number": "ТЛ-1",
                "date": "2026-10-17",
                "route": "Москва — Тула",
                "price_with_vat": 100000,
                "payment_days": 30,
                "vat_rate": "22%",
            })}

        def _tab_keys(self):
            return ("contract_tab",)

        def _tab_by_key(self, key):
            return self._tabs.get(key)

    result = cmp.compare_with_typical("perevozka", window=Window())
    assert result["matched"] >= 4


# ─────────────────────────────────────────────────────────────
# 3. Текст для оператора
# ─────────────────────────────────────────────────────────────

def test_format_comparison_marks_practice():
    """В тексте прямо сказано: это практика, а не норма права."""
    values = dict(MATCHING, **{"contract_tab.payment_days": 45})
    result = cmp.compare_with_typical("perevozka", values=values)
    text = cmp.format_comparison(result)
    assert "практика, не норма права" in text
    assert "45" in text


def test_typical_digest_lists_values_and_warning():
    """Описание типового: значения, обязательные поля и предупреждение."""
    text = cmp.typical_digest("perevozka")
    assert "30" in text
    assert "обязательные поля" in text
    assert "не требование закона" in text


def test_typical_digest_unknown_type_is_honest():
    """О неизвестном типе — прямая фраза."""
    assert "нет" in cmp.typical_digest("чужой")


def test_typical_json_files_are_valid():
    """Файлы типовых — корректный JSON (их правят руками)."""
    for name in set(cmp.TYPICAL_FILES.values()):
        path = cmp.TEMPLATES_DIR / name
        assert path.is_file(), f"нет файла {name}"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert data.get("contract_type")
