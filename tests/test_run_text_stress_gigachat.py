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
    METRICS_1000_PATH, PROMPT_DEFAULT_VALUES, SafetyStop, _describe_error,
    _result_rows, _verdict_ok, build_metrics_1000, check_answer, field_error_stats,
    field_metrics_pair, field_stats, group_metrics, load_legacy_numbers,
    prompt_default_extras, safety_check, summarise, top_error_fields,
    top_extra_fields, verdict,
)
import json
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
    assert report["placeholders"] >= 4          # ФИО, серия, номер, телефон
    assert report["unmasked_personal"] == 0
    # Маскирование обязано ИЗМЕНИТЬ текст. Длину сравнивать нельзя: два
    # токена паспорта («<<PASSPORT_SERIES_1>> <<PASSPORT_NUMBER_1>>») длиннее
    # самой серии с номером.
    assert report["length"] != report["masked_length"]


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
# Метрики по всему прогрессу: где именно ошибается движок
# ─────────────────────────────────────────────────────────────

def engine_result(**overrides):
    """Запись прогресса одного сценария с готовыми метриками движка."""
    row = {
        "expected": 3, "found": 4, "true_positive": 2,
        "missed": [], "wrong": [], "extra": [], "default_extras": [],
        "field_stats": {},
    }
    row.update(overrides)
    return row


def progress_item(number, kind="drivers", level="simple", **overrides):
    item = {"number": number, "kind": kind, "level": level,
            "gigachat": engine_result(), "local": engine_result()}
    item.update(overrides)
    return item


def progress_of(*items):
    return {"results": {str(item["number"]): item for item in items},
            "model": "GigaChat-3-Pro", "scope": "GIGACHAT_API_PERS",
            "updated_at": "2026-10-12T18:07:41"}


def test_result_rows_sorted_by_number():
    data = progress_of(progress_item(10), progress_item(2), progress_item(7))
    assert [row["number"] for row in _result_rows(data)] == [2, 7, 10]


def test_field_error_stats_splits_missed_wrong_extra():
    item = progress_item(1, gigachat=engine_result(
        missed=["driver.passport_series"],
        wrong=["driver.phone"],
        extra=["carrier.short_name"],
        field_stats={"driver.birth_date": {"expected": 2, "found": 1, "matched": 1}}))
    stats = field_error_stats([item], "gigachat")
    assert stats["driver.passport_series"]["missed"] == 1
    assert stats["driver.passport_series"]["wrong"] == 0
    assert stats["driver.phone"]["wrong"] == 1
    assert stats["carrier.short_name"]["extra"] == 1
    assert stats["driver.birth_date"]["matched"] == 1


def test_field_error_stats_marks_service_and_out_of_schema_extras():
    """
    Служебная подстановка промпта и поле «вне схемы» — разные пометки.

    `contract.price_without_vat` приходит как `0.0` (подстановка промпта), а
    `carrier.short_name` — настоящее лишнее поле: оно есть в схеме, но в
    эталоне сценария его не было.
    """
    item = progress_item(1, gigachat=engine_result(
        extra=["contract.price_without_vat", "carrier.short_name"],
        default_extras=["contract.price_without_vat"]))
    stats = field_error_stats([item], "gigachat")
    assert stats["contract.price_without_vat"]["extra_default"] == 1
    assert stats["contract.price_without_vat"]["extra_real"] == 0
    assert stats["carrier.short_name"]["extra_real"] == 1
    assert stats["carrier.short_name"]["extra_default"] == 0


def test_field_error_stats_skips_scenarios_without_answer():
    item = progress_item(1, gigachat=None, local=engine_result(
        missed=["driver.phone"]))
    assert field_error_stats([item], "gigachat") == {}
    assert field_error_stats([item], "local")["driver.phone"]["missed"] == 1


def test_top_error_fields_sorted_by_count_then_name():
    stats = {
        "vehicles.color": {"expected": 10, "missed": 4, "wrong": 0, "extra": 0,
                           "extra_default": 0, "extra_schema": 0, "extra_real": 0},
        "driver.phone": {"expected": 10, "missed": 4, "wrong": 0, "extra": 0,
                         "extra_default": 0, "extra_schema": 0, "extra_real": 0},
        "driver.birth_date": {"expected": 10, "missed": 1, "wrong": 0, "extra": 0,
                              "extra_default": 0, "extra_schema": 0, "extra_real": 0},
    }
    top = top_error_fields(stats, "missed")
    assert [row["field"] for row in top] == [
        "driver.phone", "vehicles.color", "driver.birth_date"]
    assert top[0]["rate"] == 0.4
    assert top_error_fields(stats, "missed", limit=1) == [top[0]]


def test_top_error_fields_ignores_zero_counts():
    stats = {"driver.phone": {"expected": 5, "missed": 0, "wrong": 0, "extra": 0,
                              "extra_default": 0, "extra_schema": 0, "extra_real": 0}}
    assert top_error_fields(stats, "missed") == []


def test_top_extra_fields_marks_kind():
    stats = {
        "contract.vat_rate": {"expected": 0, "missed": 0, "wrong": 0, "extra": 9,
                              "extra_default": 9, "extra_schema": 0, "extra_real": 0},
        "contract.loading_address": {"expected": 0, "missed": 0, "wrong": 0,
                                     "extra": 5, "extra_default": 0,
                                     "extra_schema": 5, "extra_real": 0},
        "carrier.short_name": {"expected": 0, "missed": 0, "wrong": 0, "extra": 2,
                               "extra_default": 0, "extra_schema": 0,
                               "extra_real": 2},
    }
    kinds = {row["field"]: row["kind"] for row in top_extra_fields(stats)}
    assert kinds == {"contract.vat_rate": "default_extras",
                     "contract.loading_address": "вне схемы",
                     "carrier.short_name": "лишнее"}


def test_field_metrics_pair_counts_recall_and_precision():
    item = progress_item(1, gigachat=engine_result(
        field_stats={"driver.phone": {"expected": 4, "found": 5, "matched": 3}}))
    row = field_metrics_pair([item], "driver.phone")["gigachat"]
    assert row["recall"] == 0.75
    assert row["precision"] == 0.6
    assert row["f1"] == round(2 * 0.75 * 0.6 / 1.35, 4)


def test_field_metrics_pair_empty_field_does_not_crash():
    row = field_metrics_pair([progress_item(1)], "driver.passport_code")
    assert row["gigachat"]["recall"] == 0.0
    assert row["gigachat"]["f1"] == 0.0


def test_build_metrics_1000_structure_and_batches():
    """Метрики совпадают с `summarise`, партии делятся по файлу номеров."""
    items = [progress_item(1), progress_item(2, kind="vehicles", level="hard")]
    metrics = build_metrics_1000(progress_of(*items), None)
    assert metrics["scenarios"] == 2
    assert metrics["scenarios_with_answer"] == 2
    for source in ("gigachat", "local"):
        assert metrics["totals"][source] == summarise(items, source)["overall"]
        assert set(metrics["by_kind"][source]) == {"drivers", "vehicles"}
        assert set(metrics["by_level"][source]) == {"simple", "hard"}
    all_numbers = load_legacy_numbers()
    legacy = set() if not all_numbers else {1, 2} & all_numbers
    assert metrics["batches"]["before_pseudonymizer_fix"]["scenarios"] == len(legacy)
    assert metrics["batches"]["after_pseudonymizer_fix"]["scenarios"] == 2 - len(legacy)


def test_build_metrics_1000_writes_json(tmp_path):
    target = tmp_path / "metrics.json"
    build_metrics_1000(progress_of(progress_item(1)), target)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["totals"]["gigachat"]["documents"] == 1
    assert data["top_extra"] == {"gigachat": [], "local": []}


def test_build_metrics_1000_covers_fields_after_pseudonymizer_fix():
    metrics = build_metrics_1000(progress_of(progress_item(1)), None)
    fields = metrics["fields_after_pseudonymizer_fix"]
    assert "driver.birth_place" in fields
    assert "carrier.bank_name" in fields
    assert set(fields["driver.birth_place"]) == {"before", "after", "all", "matched"}


def test_build_metrics_1000_has_no_field_values(tmp_path):
    """
    Метрики идут в git: в них допустимы только числа и ИМЕНА полей.

    Значения из документов сюда попасть не должны — ни прямо, ни через
    содержимое `extra` / `missed` (там хранятся только имена).
    """
    target = tmp_path / "metrics.json"
    item = progress_item(1, gigachat=engine_result(
        missed=["driver.passport_series"], wrong=["driver.phone"],
        extra=["carrier.bank_name"], default_extras=["contract.vat_rate"]))
    build_metrics_1000(progress_of(item), target)
    text = target.read_text(encoding="utf-8")
    for value in ("Ерофеев", "553107", "+7 (913)", "47 12"):
        assert value not in text


def test_repo_metrics_file_is_valid_and_safe():
    """Файл метрик в репозитории: JSON, есть сводка и только имена полей."""
    if not METRICS_1000_PATH.exists():
        pytest.skip("baseline_text_gigachat_1000.json ещё не собран")
    data = json.loads(METRICS_1000_PATH.read_text(encoding="utf-8"))
    assert data["scenarios"] == 1000
    assert data["scenarios_with_answer"] == 1000
    assert data["totals"]["gigachat"]["documents"] == 1000
    assert data["totals"]["local"]["documents"] == 1000
    assert len(data["top_missed"]["gigachat"]) <= 20
    assert len(data["top_wrong"]["gigachat"]) <= 20
    assert len(data["top_extra"]["gigachat"]) <= 20
    for row in data["top_extra"]["gigachat"]:
        assert row["kind"] in {"default_extras", "вне схемы", "лишнее"}
    assert data["batches"]["before_pseudonymizer_fix"]["scenarios"] == 264
    assert data["batches"]["after_pseudonymizer_fix"]["scenarios"] == 736


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
