#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты прогонщика стресс-теста текста (`tools/run_text_stress_gigachat.py`).

Здесь проверяются ТОЛЬКО чистые функции: проверки безопасности, метрики и
вердикт о регрессии. Сеть не используется, ключ не нужен, запросы не идут:
сам прогон 1000 сценариев — отдельная долгая операция, а не тест.
"""

import pytest

from tools.make_text_scenarios import build_scenario
from tools.run_text_stress_gigachat import (
    PROMPT_DEFAULT_VALUES, SafetyStop, _describe_error, _verdict_ok, check_answer,
    field_stats, group_metrics, prompt_default_extras, safety_check, summarise,
    verdict,
)
import random


def scenario(kind="drivers", level="simple", seed=7):
    return build_scenario(random.Random(seed), kind, level)


def driver_scenario():
    return {
        "text": "\n".join([
            "ВОДИТЕЛЬ",
            "ФИО: Ерофеев Тимур Валерьевич",
            "Паспорт: 47 12 553107",
            "Телефон: +7 (913) 402-77-15",
        ]),
        "ground_truth": {"driver": {
            "full_name": "Ерофеев Тимур Валерьевич",
            "passport_series": "47 12",
            "passport_number": "553107",
            "phone": "+7 (913) 402-77-15",
        }},
    }


# ─────────────────────────────────────────────────────────────
# Проверки безопасности
# ─────────────────────────────────────────────────────────────

def test_safety_check_passes_normal_scenario():
    report = safety_check(driver_scenario(), [], 1)
    assert report["masked_ok"] is True
    assert report["placeholders"] >= 3          # ФИО, паспорт, телефон
    assert report["unmasked_personal"] == 0
    assert report["length"] > report["masked_length"]


def test_safety_check_stops_on_operator_path():
    broken = driver_scenario()
    broken["text"] += "\nСкан: E:\\Документы\\Иванов\\паспорт.pdf"
    with pytest.raises(SafetyStop) as error:
        safety_check(broken, [], 2)
    assert "путь" in str(error.value)


def test_safety_check_stops_on_db_collision():
    """Если ФИО сценария есть в рабочей базе — отправлять нельзя."""
    known = driver_scenario()["ground_truth"]["driver"]["full_name"].casefold()
    with pytest.raises(SafetyStop) as error:
        safety_check(driver_scenario(), [known], 3)
    assert "рабочей базой" in str(error.value)


def test_safety_check_stops_on_any_db_collision():
    """
    Совпадение ЛЮБОГО значимого значения с рабочей базой — стоп.

    Проверка намеренно шире «ФИО/паспорт/ИНН» из задания: в текст не должно
    попадать ничего из базы оператора — ни адрес, ни название контрагента,
    ни госномер. Сценарии генератора эту сверку проходят (см.
    `tests/test_make_text_scenarios.py`), поэтому в прогоне она не срабатывает.
    """
    organization = {
        "text": "ПЕРЕВОЗЧИК\nООО «Урал-Карго»\nИНН 2376958831",
        "ground_truth": {"carrier": {"full_name": "ООО «Урал-Карго»",
                                     "inn": "2376958831"}},
    }
    with pytest.raises(SafetyStop) as error:
        safety_check(organization, ["ооо «урал-карго»"], 4)
    assert "рабочей базой" in str(error.value)


def test_safety_check_does_not_require_masking_company_name():
    """Название юрлица плейсхолдером не закрывается — и это не нарушение."""
    organization = {
        "text": "ПЕРЕВОЗЧИК\nООО «Урал-Карго»\nИНН 2376958831",
        "ground_truth": {"carrier": {"full_name": "ООО «Урал-Карго»",
                                     "inn": "2376958831"}},
    }
    report = safety_check(organization, [], 5)
    assert report["unmasked_personal"] == 0


def test_check_answer_stops_on_leftover_placeholder():
    with pytest.raises(SafetyStop) as error:
        check_answer(6, {"driver": {"full_name": "<<PERSON_1>>"}})
    assert "плейсхолдер" in str(error.value)


def test_check_answer_accepts_clean_result():
    check_answer(7, {"driver": {"full_name": "Ерофеев Тимур Валерьевич"}})


# ─────────────────────────────────────────────────────────────
# Служебные значения промпта
# ─────────────────────────────────────────────────────────────

def test_prompt_default_extras_detects_service_values():
    expected = {"carrier": {"inn": "2376958831"}}
    got = {"carrier": {"inn": "2376958831"},
           "contract": {"vat_rate": "22%", "payment_days": 10,
                        "price_without_vat": 0},
           "tractor": {"year": 0}, "trailer": {"year": 0}}
    extras = prompt_default_extras(expected, got)
    assert extras == ["contract.payment_days", "contract.price_without_vat",
                      "contract.vat_rate", "tractor.year", "trailer.year"]


def test_prompt_default_extras_keeps_real_values():
    """Настоящая ставка из документа — не «служебная подстановка»."""
    expected = {"contract": {"vat_rate": "22%"}}
    got = {"contract": {"vat_rate": "22%", "payment_days": 30},
           "vehicles": [{"year": 2019}]}
    extras = prompt_default_extras(expected, got)
    assert extras == []
    assert "contract.vat_rate" not in PROMPT_DEFAULT_VALUES.get("x", set())


def test_prompt_default_extras_ignores_document_year():
    expected = {"vehicles": [{"vin": "X" * 17}]}
    got = {"vehicles": [{"vin": "X" * 17, "year": 2019}]}
    assert prompt_default_extras(expected, got) == []


# ─────────────────────────────────────────────────────────────
# Метрики
# ─────────────────────────────────────────────────────────────

def test_field_stats_counts_expected_found_matched():
    expected = {"driver": {"full_name": "Иванов Иван Иванович",
                           "passport_number": "553107"}}
    got = {"driver": {"full_name": "Иванов Иван Иванович",
                      "passport_number": "000000",
                      "phone": "+79134027715"}}
    stats = field_stats(expected, got)
    assert stats["driver.full_name"] == {"expected": 1, "found": 1, "matched": 1}
    assert stats["driver.passport_number"] == {"expected": 1, "found": 1, "matched": 0}
    assert "driver.phone" not in stats          # в эталоне его нет


def test_field_stats_normalizes_separators_and_dates():
    expected = {"driver": {"passport_series": "47 12",
                           "birth_date": "02.04.1980"}}
    got = {"driver": {"passport_series": "4712", "birth_date": "1980-04-02"}}
    stats = field_stats(expected, got)
    assert stats["driver.passport_series"]["matched"] == 1
    assert stats["driver.birth_date"]["matched"] == 1


def test_group_metrics_counts_precision_core_without_service_fields():
    rows = [
        {"expected": 3, "found": 8, "true_positive": 3, "default_extras": 5},
        {"expected": 2, "found": 7, "true_positive": 2, "default_extras": 5},
    ]
    metrics = group_metrics(rows)
    assert metrics["recall"] == 1.0
    assert metrics["service_filled"] == 10
    assert metrics["precision"] == round(5 / 15, 4)
    assert metrics["precision_core"] == 1.0


def test_group_metrics_empty_rows_do_not_crash():
    metrics = group_metrics([])
    assert metrics["documents"] == 0
    assert metrics["recall"] == 0.0


def test_summarise_splits_by_kind_and_level():
    results = [
        {"kind": "drivers", "level": "simple",
         "local": {"expected": 4, "found": 4, "true_positive": 4,
                   "default_extras": []}},
        {"kind": "drivers", "level": "hard",
         "local": {"expected": 4, "found": 2, "true_positive": 2,
                   "default_extras": []}},
        {"kind": "vehicles", "level": "simple", "local": None},
    ]
    report = summarise(results, "local")
    assert report["overall"]["documents"] == 2
    assert report["by_kind"]["drivers"]["recall"] == 0.75
    assert "vehicles" not in report["by_kind"]
    assert report["by_level"]["simple"]["documents"] == 1


# ─────────────────────────────────────────────────────────────
# Вердикт о регрессии
# ─────────────────────────────────────────────────────────────

def report_with(recall, scenarios=1000):
    overall = {"recall": recall, "precision": 0.5, "precision_core": 0.6,
               "f1": 0.5}
    return {"scenarios": scenarios, "gigachat": {"overall": overall},
            "local": {"overall": dict(overall)}}


def test_verdict_ok_inside_tolerance():
    baseline = report_with(0.50)
    assert _verdict_ok(report_with(0.49), baseline, 1000) is True
    assert "РЕГРЕССИИ НЕТ" in verdict(report_with(0.49), baseline, 1000)


def test_verdict_fails_beyond_tolerance():
    baseline = report_with(0.50)
    assert _verdict_ok(report_with(0.47), baseline, 1000) is False
    assert "ЕСТЬ РЕГРЕССИЯ" in verdict(report_with(0.47), baseline, 1000)


def test_verdict_not_applied_to_partial_run():
    baseline = report_with(0.50)
    assert _verdict_ok(report_with(0.10), baseline, 20) is True
    assert "неполный" in verdict(report_with(0.10), baseline, 20)


# ─────────────────────────────────────────────────────────────
# Разбор ошибок API
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("message, expected", [
    ("GigaChat вернул 429: превышен лимит запросов", "429"),
    ("GigaChat вернул 401: неверный access_token", "401"),
    ("GigaChat: HTTP 403.", "403"),
    ("GigaChat вернул 404: модель не найдена", "404"),
    ("GigaChat: превышено время ожидания", "RuntimeError"),
    ("что-то совсем другое", "RuntimeError"),
])
def test_describe_error_classifies_api_failures(message, expected):
    error = RuntimeError(message)
    assert _describe_error(error) == expected


def test_generated_scenarios_pass_safety_check():
    """Сценарии генератора обязаны проходить проверку без ручных правок."""
    for seed in (1, 42, 555, 9999):
        for kind, level in (("drivers", "simple"), ("drivers", "extreme"),
                            ("organizations", "hard"), ("vehicles", "medium")):
            data = build_scenario(random.Random(seed), kind, level)
            report = safety_check(data, [], seed)
            assert report["masked_ok"] is True
