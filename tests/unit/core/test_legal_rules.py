#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты каталога юридических правил (core/legal_rules.py).

Главная проверка файла — не «правило загрузилось», а «правило не выдумано»:
test_quote_is_found_in_source_file берёт цитату КАЖДОГО правила и ищет её в
файле правовой базы дословно. Выдуманная норма, переписанная «своими
словами» цитата или несуществующая статья падают здесь, а не в суде.

Второй важный тест — test_payment_days_is_required_not_max: в задаче
предлагалось правило «срок оплаты не больше 30 дней (УАТ ст. 34)», но в
правовой базе такой нормы нет — УАТ ст. 34 говорит об ответственности, а
срок оплаты отдан соглашению сторон (ГК РФ ст. 790 п. 1). Правило в
каталоге поэтому — «срок должен быть согласован», а типовое значение 30
дней живёт отдельно (tests/unit/agent/test_legal_compare.py). Тест
сторожит именно это решение: если кто-то вернёт «max 30» со ссылкой на
УАТ ст. 34, он покраснеет.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import legal_rules as lr

#: Корень проекта — по маркеру (pytest.ini), а не по числу parents.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)

#: Файл каталога правил проекта.
RULES_FILE = PROJECT_ROOT / "data" / "knowledge" / "legal" / "rules.json"

#: Все имена правил проекта — берутся из файла, а не перечислены руками:
#: новое правило обязано пройти те же проверки, что и старые.
RULE_NAMES = tuple(lr.load_rules(force=True))


@pytest.fixture(autouse=True)
def _fresh_rules():
    """Каждый тест начинает с каталога проекта, а не с чужого кэша."""
    lr.reset_rules()
    yield
    lr.reset_rules()


# ─────────────────────────────────────────────────────────────
# 1. Каталог: состав и обязательные поля
# ─────────────────────────────────────────────────────────────

def test_rules_file_exists():
    """Файл правил на месте — иначе проверять форму нечем."""
    assert RULES_FILE.is_file(), f"нет файла правил: {RULES_FILE}"


def test_rules_are_loaded():
    """В каталоге есть правила (а не пустой объект)."""
    rules = lr.load_rules(force=True)
    assert len(rules) >= 5, f"правил всего {len(rules)}"
    assert "vat_rate" in rules


def test_service_keys_are_not_rules():
    """Ключи-пояснения (_comment, _version, _note_…) правилами не считаются."""
    assert not [name for name in RULE_NAMES if name.startswith("_")]
    assert "_comment" not in RULE_NAMES


@pytest.mark.parametrize("name", RULE_NAMES)
def test_rule_has_source_and_explanation(name):
    """У правила есть источник, файл источника и объяснение для оператора."""
    rule = lr.get_rule(name)
    assert rule is not None
    assert str(rule.get("source") or "").strip(), f"{name}: нет source"
    assert str(rule.get("source_file") or "").strip(), f"{name}: нет source_file"
    assert str(rule.get("explanation") or "").strip(), f"{name}: нет explanation"


@pytest.mark.parametrize("name", RULE_NAMES)
def test_rule_has_human_title(name):
    """У правила есть название для человека, а не только машинный ключ."""
    rule = lr.get_rule(name)
    title = str(rule.get("title") or "").strip()
    assert title, f"{name}: нет title"
    assert title != name, f"{name}: title совпадает с ключом — это не название"
    assert title[:1].isupper(), f"{name}: title начинается не с заглавной"


@pytest.mark.parametrize("name", RULE_NAMES)
def test_quote_is_found_in_source_file(name):
    """
    Цитата правила есть в его файле-источнике ДОСЛОВНО.

    Это и есть защита от выдуманной нормы: сочинить статью можно, а найти
    сочинённую цитату в тексте закона — нет.

    Тексты актов в git НЕ хранятся (data/knowledge/legal/.gitignore: это
    десятки мегабайт сканов). На чистом клоне файла-источника нет — тогда
    проверка пропускается: проверить нечем. Как только базу скачали
    (python tools/download_legal.py --all), тест снова проверяет каждую
    цитату.
    """
    rule = lr.get_rule(name)
    assert str(rule.get("quote") or "").strip(), f"{name}: нет quote"
    path = lr.source_path(rule)
    assert path is not None, f"{name}: не указан source_file"
    if not path.is_file():
        pytest.skip(f"текст акта не скачан: {path.name}")
    assert lr.quote_found_in_source(rule), (
        f"{name}: цитаты нет в {path.name} — правило не подтверждено базой"
    )


@pytest.mark.parametrize("name", RULE_NAMES)
def test_checkable_rule_has_field(name):
    """У проверяемого правила есть поле формы; справочное — может не иметь."""
    rule = lr.get_rule(name)
    if rule["rule_type"] == "reference":
        assert not lr.field_paths(rule) or rule.get("field")
        return
    assert lr.field_paths(rule), f"{name}: проверять нечего — нет field"


def test_rule_types_are_known():
    """Тип каждого правила — из списка, который умеет check_rule."""
    allowed = set(lr.CHECK_TYPES) | {"reference"}
    unknown = {
        name: rule["rule_type"]
        for name, rule in lr.load_rules(force=True).items()
        if rule["rule_type"] not in allowed
    }
    assert unknown == {}, f"неизвестные типы правил: {unknown}"


# ─────────────────────────────────────────────────────────────
# 2. Доступ к правилам
# ─────────────────────────────────────────────────────────────

def test_get_rule_returns_catalog_entry():
    """get_rule отдаёт описание правила с источником."""
    rule = lr.get_rule("vat_rate")
    assert rule is not None
    assert rule["source"] == "НК РФ ст. 164 п. 3"
    assert rule["rule_type"] == "exact"


def test_get_rule_unknown_is_none():
    """Неизвестное правило — None, а не исключение."""
    assert lr.get_rule("нет_такого") is None
    assert lr.get_rule("") is None


def test_rule_names_keep_file_order():
    """Имена правил идут в порядке файла: диалог показывает их так же."""
    names = lr.rule_names()
    assert names[0] == "vat_rate"
    assert len(names) == len(set(names))


def test_rules_for_type_filters_by_applies_to():
    """Правила фильтруются по типу договора (у аренды нет проверки ВУ)."""
    perevozka = lr.rules_for_type("perevozka")
    arenda = lr.rules_for_type("arenda_ts")
    assert "driver_license_number" in perevozka
    assert "driver_license_number" not in arenda
    assert "vat_rate" in arenda


def test_rules_for_type_empty_returns_all():
    """Пустой тип — все правила: лучше проверить лишнее, чем промолчать."""
    assert set(lr.rules_for_type("")) == set(lr.rule_names())


def test_rules_for_type_unknown_type_is_empty():
    """Неизвестный тип получает только общие правила (без applies_to)."""
    assert lr.rules_for_type("чужой_тип") == {}


# ─────────────────────────────────────────────────────────────
# 3. Ставка НДС
# ─────────────────────────────────────────────────────────────

def test_vat_rate_22_passes():
    """22% — действующая ставка, нарушения нет."""
    result = lr.check_rule("vat_rate", "22%")
    assert result["ok"] is True
    assert result["checked"] is True


def test_vat_rate_20_is_violation_with_number_and_source():
    """20% — нарушение: в тексте есть и фактическое, и требуемое значение."""
    result = lr.check_rule("vat_rate", "20%")
    assert result["ok"] is False
    assert "20%" in result["message"]
    assert "22%" in result["message"]
    assert "164" in result["message"]
    assert result["severity"] == "error"


def test_vat_rate_free_is_allowed():
    """«Без НДС» и «0%» — не ошибка, ставка просто не применяется."""
    assert lr.check_rule("vat_rate", "без НДС")["ok"] is True
    assert lr.check_rule("vat_rate", "0%")["ok"] is True
    assert lr.check_rule("vat_rate", 0)["ok"] is True


def test_vat_rate_empty_is_not_checked():
    """Пустое значение — «не проверено», а не «нарушено»."""
    result = lr.check_rule("vat_rate", "")
    assert result["ok"] is True
    assert result["checked"] is False


def test_vat_rate_ten_percent_is_violation():
    """10% (льготная ставка) в договоре перевозки — нарушение основной ставки."""
    result = lr.check_rule("vat_rate", "10%")
    assert result["ok"] is False
    assert "10%" in result["message"]


# ─────────────────────────────────────────────────────────────
# 4. Срок оплаты
# ─────────────────────────────────────────────────────────────

def test_payment_days_empty_is_violation():
    """Срок оплаты не заполнен — плата не согласована (ГК РФ ст. 790 п. 1)."""
    result = lr.check_rule("payment_days", 0)
    assert result["ok"] is False
    assert result["checked"] is True
    assert "790" in result["message"]


def test_payment_days_any_positive_passes():
    """Положительный срок законом не ограничен: 45 дней — не нарушение."""
    for days in (10, 30, 45, 120):
        assert lr.check_rule("payment_days", days)["ok"] is True


def test_payment_days_is_required_not_max():
    """
    Срок оплаты в каталоге — «должен быть согласован», а не «не больше 30».

    В правовой базе нет нормы, ограничивающей срок оплаты: УАТ ст. 34 —
    об ответственности, а ГК РФ ст. 790 п. 1 отдаёт плату соглашению
    сторон. Тест фиксирует решение, чтобы «полезная» правка не вернула
    выдуманный предел 30 дней со ссылкой на УАТ ст. 34.
    """
    rule = lr.get_rule("payment_days")
    assert rule["rule_type"] == "required"
    assert "УАТ" not in rule["source"], (
        "УАТ ст. 34 не устанавливает срок оплаты — ссылка была бы выдумкой"
    )
    assert rule.get("value") is None, "у правила-требования нет предельного числа"


# ─────────────────────────────────────────────────────────────
# 5. Маршрут и водитель
# ─────────────────────────────────────────────────────────────

def test_route_required_blank_is_violation():
    """Пустой маршрут — замечание со ссылкой на правила перевозок грузов."""
    result = lr.check_rule("route_required", "   ")
    assert result["ok"] is False
    assert "2200" in result["message"]


def test_route_required_filled_passes():
    """Заполненный маршрут — нарушения нет."""
    assert lr.check_rule("route_required", "Москва — Тула")["ok"] is True


def test_driver_license_valid_expired_is_violation():
    """Истёкший срок действия ВУ — замечание с датой и источником."""
    result = lr.check_rule("driver_license_valid", "2020-01-01")
    assert result["ok"] is False
    assert "01.01.2020" in result["message"]
    assert "12.7" in result["message"]


def test_driver_license_valid_future_passes():
    """Действующее ВУ — нарушения нет."""
    assert lr.check_rule("driver_license_valid", "2999-12-31")["ok"] is True


def test_driver_license_valid_bad_date_is_not_checked():
    """Нечитаемая дата — «не проверено»: придумывать нарушение нельзя."""
    result = lr.check_rule("driver_license_valid", "не дата")
    assert result["ok"] is True
    assert result["checked"] is False


def test_driver_license_number_blank_is_violation():
    """Без номера ВУ водителя нечем подтвердить допуск (УАТ ст. 10 ч. 1)."""
    result = lr.check_rule("driver_license_number", "")
    assert result["ok"] is False
    assert "10" in result["message"]


# ─────────────────────────────────────────────────────────────
# 6. Справочные нормы
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "pretension_reply_days",
    "transport_nakladnaya_required",
    "claim_period",
    "carrier_delay_penalty",
])
def test_reference_rule_is_not_checked(name):
    """Справочная норма описывает право, но форму по ней не проверяют."""
    result = lr.check_rule(name, 1)
    assert result["ok"] is True
    assert result["checked"] is False
    assert result["rule_type"] == "reference"


def test_unknown_rule_returns_error_result():
    """Неизвестное правило — понятный текст, а не исключение."""
    result = lr.check_rule("нет_такого", 1)
    assert result["ok"] is False
    assert result["checked"] is False
    assert "нет в каталоге" in result["message"]


# ─────────────────────────────────────────────────────────────
# 7. Разбор значений
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("22%", 22.0),
    ("45", 45.0),
    ("0,1", 0.1),
    (30, 30.0),
    (7.5, 7.5),
])
def test_as_number_parses_form_values(value, expected):
    """Число разбирается и из «22%», и из «0,1» с запятой."""
    assert lr.as_number(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", ["", "  ", None, "без НДС", True])
def test_as_number_returns_none_for_non_numbers(value):
    """Нечисловое значение — None, а не выдуманный ноль."""
    assert lr.as_number(value) is None


def test_as_date_parses_iso_and_russian():
    """Дата читается в ISO и в русской записи."""
    assert lr.as_date("2026-10-17").isoformat() == "2026-10-17"
    assert lr.as_date("17.10.2026").isoformat() == "2026-10-17"
    assert lr.as_date("2026-10-17 08:30:00").isoformat() == "2026-10-17"
    assert lr.as_date("") is None
    assert lr.as_date("17 октября") is None


@pytest.mark.parametrize("value,blank", [
    ("", True), ("   ", True), (None, True), (0, True), (0.0, True),
    ("0", False), (1, False), ("маршрут", False), (False, False),
])
def test_is_blank(value, blank):
    """Пустота: None, пробелы и ноль у числа; «0» строкой — уже значение."""
    assert lr.is_blank(value) is blank


def test_format_date_uses_russian_style():
    """Дата для оператора — 17.10.2026, а не ISO."""
    assert lr.format_date("2026-10-17") == "17.10.2026"
    assert lr.format_date("") == ""


def test_resolve_field_uses_alias():
    """Поле ищется по синониму: у аренды маршрут лежит в route_tab."""
    rule = lr.get_rule("route_required")
    path, value = lr.resolve_field({"route_tab.route": "Москва — Тула"}, rule)
    assert path == "route_tab.route"
    assert value == "Москва — Тула"


def test_resolve_field_missing_returns_empty():
    """Поля в форме нет — путь пустой (правило будет пропущено)."""
    path, value = lr.resolve_field({"driver_tab.full_name": "Иванов"}, lr.get_rule("route_required"))
    assert path == ""
    assert value is None


# ─────────────────────────────────────────────────────────────
# 8. Описание для человека
# ─────────────────────────────────────────────────────────────

def test_describe_rule_contains_source_and_quote():
    """Описание правила называет источник и приводит цитату."""
    text = lr.describe_rule("vat_rate")
    assert "НК РФ ст. 164 п. 3" in text
    assert "22 процента" in text
    assert "contract_tab.vat_rate" in text


def test_describe_reference_rule_says_not_checked():
    """По справочной норме прямо сказано, что поле не проверяется."""
    text = lr.describe_rule("claim_period")
    assert "справочная" in text
    assert "один год" in text


def test_describe_unknown_rule_is_honest():
    """О неизвестном правиле — прямая фраза, а не пустая строка."""
    assert "нет" in lr.describe_rule("нет_такого")


# ─────────────────────────────────────────────────────────────
# 9. Свой файл правил (тесты и пробы)
# ─────────────────────────────────────────────────────────────

def test_load_rules_from_custom_path(tmp_path):
    """Каталог читается из указанного файла — путь подменяется тестом."""
    custom = tmp_path / "rules.json"
    custom.write_text(json.dumps({
        "_comment": "проба",
        "demo_rule": {
            "title": "Пробное правило",
            "rule_type": "max",
            "field": "contract_tab.demo",
            "value": 10,
            "severity": "warning",
            "source": "проба",
            "source_file": "",
            "explanation": "проверка разбора",
        },
    }, ensure_ascii=False), encoding="utf-8")

    rules = lr.load_rules(custom, force=True)
    assert list(rules) == ["demo_rule"]
    assert lr.check_rule("demo_rule", 11)["ok"] is False
    assert lr.check_rule("demo_rule", 10)["ok"] is True


def test_broken_rules_file_does_not_crash(tmp_path, caplog):
    """Битый JSON — пустой каталог и запись в журнал, а не падение."""
    broken = tmp_path / "rules.json"
    broken.write_text("{ это не json", encoding="utf-8")
    assert lr.load_rules(broken, force=True) == {}


def test_missing_rules_file_does_not_crash(tmp_path):
    """Нет файла — пустой каталог; инструмент честно скажет «проверять нечем»."""
    assert lr.load_rules(tmp_path / "нет.json", force=True) == {}
