#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты юридической проверки формы (services/agent/tools/legal_check.py).

Что проверяется:

* нарушение находится и несёт источник (без источника замечание не
  считается юридическим);
* пустое поле и отсутствующее поле — «не проверено», а не «нарушено»;
* проверка НЕ меняет форму (входной словарь остаётся тем же);
* тип договора отсекает правила, которых в этой форме нет (в аренде нет
  вкладки водителя);
* итог и текст для оператора собираются из нарушений.

Qt здесь не нужен: инструмент принимает словари, а «окно» — подставной
объект с get_data, как в остальных тестах агента.
"""

from __future__ import annotations

import copy

import pytest

from services.agent.tools import legal_check as lc


# ─────────────────────────────────────────────────────────────
# Подставное окно: вкладки с get_data
# ─────────────────────────────────────────────────────────────

class FakeTab:
    """Вкладка формы: отдаёт свой словарь через get_data."""

    def __init__(self, data):
        self._data = dict(data)

    def get_data(self):
        return dict(self._data)


class FakeWindow:
    """Окно формы: ключи вкладок и сами вкладки (как у окон типов)."""

    CONTRACT_TYPE = "perevozka"

    def __init__(self, tabs, contract_type="perevozka"):
        self._tabs = {key: FakeTab(data) for key, data in tabs.items()}
        self.CONTRACT_TYPE = contract_type

    def _tab_keys(self):
        return tuple(self._tabs)

    def _tab_by_key(self, key):
        return self._tabs.get(key)


#: Значения формы «Экспедиторства», где всё в порядке.
GOOD_VALUES = {
    "contract_tab.vat_rate": "22%",
    "contract_tab.payment_days": 10,
    "contract_tab.route": "Москва — Тула",
    "driver_tab.license_number": "99 36 123456",
    "driver_tab.license_expiry_date": "2999-12-31",
}


# ─────────────────────────────────────────────────────────────
# 1. Нарушения находятся и несут источник
# ─────────────────────────────────────────────────────────────

def test_good_form_has_no_violations():
    """Заполненная по нормам форма — нарушений нет."""
    result = lc.check_contract_legal(contract_type="perevozka", values=GOOD_VALUES)
    assert result["ok"] is True
    assert result["violations"] == []
    assert result["checked"] >= 4
    assert "Нарушений нет" in result["summary"]


def test_vat_20_is_violation_with_source():
    """Ставка 20% — ошибка со ссылкой на НК РФ ст. 164 п. 3."""
    values = dict(GOOD_VALUES, **{"contract_tab.vat_rate": "20%"})
    result = lc.check_contract_legal(contract_type="perevozka", values=values)
    assert len(result["violations"]) == 1
    violation = result["violations"][0]
    assert violation["rule"] == "vat_rate"
    assert violation["severity"] == "error"
    assert "НК РФ ст. 164 п. 3" in violation["source"]
    assert violation["field"] == "contract_tab.vat_rate"
    assert violation["current"] == "20%"
    assert violation["required"] == 22
    assert violation["quote"], "у нарушения обязана быть цитата нормы"


def test_empty_route_is_violation():
    """Пустой маршрут — замечание (реквизит заказа-наряда)."""
    values = dict(GOOD_VALUES, **{"contract_tab.route": ""})
    result = lc.check_contract_legal(contract_type="perevozka", values=values)
    rules = [item["rule"] for item in result["violations"]]
    assert "route_required" in rules


def test_expired_license_is_violation():
    """Просроченное ВУ — замечание со ссылкой на КоАП ст. 12.7."""
    values = dict(GOOD_VALUES, **{"driver_tab.license_expiry_date": "2020-01-01"})
    result = lc.check_contract_legal(contract_type="perevozka", values=values)
    rules = [item["rule"] for item in result["violations"]]
    assert "driver_license_valid" in rules


def test_payment_days_zero_is_violation():
    """Срок оплаты не указан — плата не согласована (ГК РФ ст. 790 п. 1)."""
    values = dict(GOOD_VALUES, **{"contract_tab.payment_days": 0})
    result = lc.check_contract_legal(contract_type="perevozka", values=values)
    rules = [item["rule"] for item in result["violations"]]
    assert "payment_days" in rules


# ─────────────────────────────────────────────────────────────
# 2. «Не проверено» — не нарушение
# ─────────────────────────────────────────────────────────────

def test_empty_form_says_nothing_to_check():
    """Пустая форма: честно «проверять нечего», а не список нарушений."""
    result = lc.check_contract_legal(contract_type="perevozka", values={})
    assert result["ok"] is True
    assert result["violations"] == []
    assert result["checked"] == 0
    assert "нечего" in result["summary"].lower()


def test_missing_field_goes_to_skipped():
    """Поля в форме нет — правило пропущено с причиной, а не нарушено."""
    values = {"contract_tab.vat_rate": "22%"}
    result = lc.check_contract_legal(contract_type="perevozka", values=values)
    skipped = {item["rule"]: item for item in result["skipped"]}
    assert "route_required" in skipped
    assert "нет" in skipped["route_required"]["reason"]
    assert result["violations"] == []


def test_reference_rules_are_skipped_not_violations():
    """Справочные нормы попадают в пропущенные, а не в нарушения."""
    result = lc.check_contract_legal(contract_type="perevozka", values=GOOD_VALUES)
    skipped = {item["rule"]: item for item in result["skipped"]}
    assert "claim_period" in skipped
    assert "справочная" in skipped["claim_period"]["reason"]
    assert all(item["rule"] != "claim_period" for item in result["violations"])


def test_empty_value_is_not_a_violation_for_numeric_rule():
    """Пустая ставка НДС — «не проверено», а не занижение налога."""
    values = dict(GOOD_VALUES, **{"contract_tab.vat_rate": ""})
    result = lc.check_contract_legal(contract_type="perevozka", values=values)
    assert all(item["rule"] != "vat_rate" for item in result["violations"])


# ─────────────────────────────────────────────────────────────
# 3. Форма не меняется
# ─────────────────────────────────────────────────────────────

def test_form_is_not_modified():
    """Проверка только читает: входной словарь остаётся точно таким же."""
    values = dict(GOOD_VALUES, **{"contract_tab.vat_rate": "20%"})
    before = copy.deepcopy(values)
    lc.check_contract_legal(contract_type="perevozka", values=values)
    assert values == before


def test_window_tabs_are_not_modified():
    """Вкладки окна после проверки отдают те же данные."""
    window = FakeWindow({
        "contract_tab": {"vat_rate": "20%", "route": "", "payment_days": 0},
        "driver_tab": {"license_number": "", "license_expiry_date": ""},
    })
    before = {key: window._tab_by_key(key).get_data() for key in window._tab_keys()}
    lc.check_contract_legal(window=window)
    after = {key: window._tab_by_key(key).get_data() for key in window._tab_keys()}
    assert after == before


# ─────────────────────────────────────────────────────────────
# 4. Окно, тип договора, правила
# ─────────────────────────────────────────────────────────────

def test_window_values_are_read():
    """Значения берутся из вкладок окна, если словарь не передан."""
    window = FakeWindow({
        "contract_tab": {"vat_rate": "20%", "route": "Москва — Тула",
                         "payment_days": 10},
    })
    result = lc.check_contract_legal(window=window)
    assert [item["rule"] for item in result["violations"]] == ["vat_rate"]


def test_contract_type_is_taken_from_window():
    """Тип договора берётся у окна, если аргумент не передан."""
    window = FakeWindow({"contract_tab": {"vat_rate": "22%"}}, contract_type="arenda_ts")
    result = lc.check_contract_legal(window=window)
    assert result["contract_type"] == "arenda_ts"


def test_driver_rules_are_skipped_for_arenda():
    """В аренде нет вкладки водителя — правила о ВУ пропускаются."""
    values = {"price_tab.vat_rate": "22%", "route_tab.route": "Москва — Тула",
              "driver_tab.license_number": ""}
    result = lc.check_contract_legal(contract_type="arenda_ts", values=values)
    skipped = {item["rule"] for item in result["skipped"]}
    assert "driver_license_number" not in skipped  # правила нет для этого типа
    assert all(item["rule"] != "driver_license_number"
               for item in result["violations"])


def test_alias_field_is_used_for_arenda():
    """Маршрут аренды лежит в route_tab — правило находит его по синониму."""
    values = {"price_tab.vat_rate": "22%", "route_tab.route": ""}
    result = lc.check_contract_legal(contract_type="arenda_ts", values=values)
    violation = next(item for item in result["violations"]
                     if item["rule"] == "route_required")
    assert violation["field"] == "route_tab.route"


def test_unknown_contract_type_checks_all_rules():
    """Неизвестный тип не отменяет проверку: правила применяются все."""
    result = lc.check_contract_legal(contract_type="чужой_тип", values=GOOD_VALUES)
    assert result["checked"] >= 4


def test_empty_catalog_says_nothing_to_check(monkeypatch):
    """Пустой каталог правил — понятный итог, а не тишина."""
    monkeypatch.setattr(lc.legal_rules, "rules_for_type", lambda _t: {})
    monkeypatch.setattr(lc.legal_rules, "load_rules", lambda *a, **k: {})
    result = lc.check_contract_legal(contract_type="perevozka", values=GOOD_VALUES)
    assert result["violations"] == []
    assert "нечем" in result["summary"]


# ─────────────────────────────────────────────────────────────
# 5. Итог и текст для оператора
# ─────────────────────────────────────────────────────────────

def test_summary_counts_severity():
    """В итоге видно, сколько ошибок и сколько замечаний."""
    values = dict(GOOD_VALUES, **{
        "contract_tab.vat_rate": "20%",
        "contract_tab.route": "",
    })
    result = lc.check_contract_legal(contract_type="perevozka", values=values)
    assert "2" in result["summary"]
    assert "ошибок 1" in result["summary"]
    assert "замечаний 1" in result["summary"]


def test_errors_are_listed_first():
    """Сначала ошибки, потом замечания: оператор читает сверху вниз."""
    values = dict(GOOD_VALUES, **{
        "contract_tab.vat_rate": "20%",
        "contract_tab.route": "",
    })
    result = lc.check_contract_legal(contract_type="perevozka", values=values)
    severities = [item["severity"] for item in result["violations"]]
    assert severities == sorted(severities, key=lambda s: lc.SEVERITY_ORDER[s])


def test_format_violations_text_has_title_and_source():
    """Текст для диалога: важность, название, объяснение, источник."""
    values = dict(GOOD_VALUES, **{"contract_tab.vat_rate": "20%"})
    result = lc.check_contract_legal(contract_type="perevozka", values=values)
    text = lc.format_violations(result)
    assert "Ставка НДС" in text
    assert "Источник:" in text
    assert "НК РФ ст. 164 п. 3" in text


def test_has_errors():
    """has_errors отличает ошибку от замечания."""
    values = dict(GOOD_VALUES, **{"contract_tab.vat_rate": "20%"})
    result = lc.check_contract_legal(contract_type="perevozka", values=values)
    assert lc.has_errors(result) is True

    values = dict(GOOD_VALUES, **{"contract_tab.route": ""})
    result = lc.check_contract_legal(contract_type="perevozka", values=values)
    assert lc.has_errors(result) is False


def test_digest_lists_checkable_rules_and_sources():
    """Перед проверкой оператор видит, какие нормы применяются."""
    text = lc.legal_rules_digest("perevozka")
    assert "Проверяется правил" in text
    assert "НК РФ ст. 164 п. 3" in text


# ─────────────────────────────────────────────────────────────
# 6. Разбор формы
# ─────────────────────────────────────────────────────────────

def test_flatten_form_nests_dicts_and_keeps_lists():
    """Вложенные блоки разворачиваются, списки остаются списками."""
    values = lc.flatten_form({
        "contract_tab": {"route": {"route": "Москва — Тула", "price": 100},
                         "loadings": [{"address": "Москва"}]},
        "driver_tab": {"full_name": "Иванов Иван"},
        "лишнее": "не словарь",
    })
    assert values["contract_tab.route.route"] == "Москва — Тула"
    assert values["contract_tab.route.price"] == 100
    assert values["contract_tab.loadings"] == [{"address": "Москва"}]
    assert values["driver_tab.full_name"] == "Иванов Иван"
    assert "лишнее" not in values


def test_values_take_priority_over_form_data():
    """Готовые плоские значения важнее развернутых разделов."""
    values = {"contract_tab.vat_rate": "22%", "contract_tab.route": "Москва — Тула"}
    result = lc.check_contract_legal(
        contract_type="perevozka", values=values,
        form_data={"contract_tab": {"vat_rate": "20%"}},
    )
    assert result["violations"] == []
