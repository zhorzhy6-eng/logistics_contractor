#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Стресс-тест распознавания ТЕКСТА через GigaChat-3-Pro: 1000 сценариев.

    python tools/run_text_stress_gigachat.py --write-baseline   # эталонный прогон
    python tools/run_text_stress_gigachat.py --resume           # продолжить
    python tools/run_text_stress_gigachat.py --sample 20        # первые 20
    python tools/run_text_stress_gigachat.py --local-only       # без сети
    python tools/run_text_stress_gigachat.py --report           # метрики по всему
                                                                # прогрессу, без сети

Что делает
----------
1. Читает 1000 зёрен из `tests/data/synthetic_docs/text_seeds.json`.
2. Собирает тексты генератором `tools/make_text_scenarios.py`
   (одно зерно → один текст).
3. ПРОГОНЯЕТ КАЖДЫЙ ТЕКСТ ЛОКАЛЬНО (`extract_local_fields`) — бесплатно
   и мгновенно: это вторая половина сравнения «облако против правил».
4. Отправляет тексты в GigaChat-3-Pro ПО ОДНОМУ, с паузой (по умолчанию
   19 секунд — около 190 запросов в час, лимит Freemium ~200).
   Параллельность ЗАПРЕЩЕНА: у тарифа один поток.
5. Считает recall / precision / F1 той же формулой, что и стресс-тест
   документов (`tools.synthetic_stress.score`), и пишет метрики в
   `tests/data/synthetic_docs/baseline_text_gigachat.json`.

Прогресс
--------
После КАЖДОГО сценария пишется `tests/_tmp/_text_stress_progress.json`
(в git не идёт). Прерванный прогон продолжается ключом `--resume`:
сценарии с готовым ответом пропускаются, повторных запросов нет.

Безопасность (перед каждым запросом)
------------------------------------
1. Ни одно ФИО / паспорт / ИНН / VIN / адрес сценария не встречается в
   рабочей `contracts.db` (поиск подстрокой, включая «только цифры»).
2. В тексте нет путей и имён файлов оператора.
3. `Pseudonymizer.anonymize()` дал непустой маппинг; персональные значения
   в отправляемом тексте заменены плейсхолдерами.
После ответа: плейсхолдеров `<<...>>` в результате не осталось.

При любом нарушении — СТОП (код 2), прогон не продолжается.
В логи и в файлы попадают ТОЛЬКО числа, имена полей и метки: ни одного
значения из документа.
"""

import argparse
import json
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.gigachat_client import GigaChatClient  # noqa: E402
from core.pseudonymizer import Pseudonymizer  # noqa: E402
from tools.make_text_scenarios import (  # noqa: E402
    SEEDS_PATH, db_haystack, find_db_collisions, load_seeds, scenario_from_seed,
)
from tools.synthetic_stress import flatten, score, _norm, _norm_field  # noqa: E402

#: Прогресс прогона (в git не идёт: там нет значений, но и незачем).
PROGRESS_PATH = PROJECT_ROOT / "tests" / "_tmp" / "_text_stress_progress.json"

#: Метрики эталонного прогона (в git; только числа и имена полей).
BASELINE_PATH = (
    PROJECT_ROOT / "tests" / "data" / "synthetic_docs" / "baseline_text_gigachat.json"
)

#: Метрики по ВСЕМУ прогрессу: местоположение ошибок по полям (топы missed /
#: wrong / extra) и сравнение партий «до фикса» и «после фикса». В git тоже
#: только числа и имена полей.
METRICS_1000_PATH = (
    PROJECT_ROOT / "tests" / "data" / "synthetic_docs"
    / "baseline_text_gigachat_1000.json"
)

#: Партия ответов, снятых ДО фикса псевдонимизатора (список номеров сценариев).
LEGACY_NUMBERS_PATH = PROJECT_ROOT / "tests" / "_tmp" / "_text_stress_264_numbers.txt"

#: Замер «маска заходила на следующую строку» на СТАРОМ коде (проба
#: `tests/_tmp/_probe_mask_newline.py --save`). Файла может не быть — тогда
#: замер не печатается. Новое значение считается на месте.
LEGACY_MASK_PATH = PROJECT_ROOT / "tests" / "_tmp" / "_mask_newline_old.json"

#: Поля, которые обязан был починить фикс псевдонимизатора: маска заходила на
#: следующую строку и съедала метку поля («Место рождения», «Телефон»,
#: «Кем выдан», «Код подразделения», «Адрес регистрации»). Здесь только имена.
FIELDS_AFTER_PSEUDONYMIZER_FIX = (
    "driver.birth_place",
    "driver.phone",
    "driver.passport_issuer",
    "driver.passport_code",
    "driver.registration_address",
    "carrier.bank_name",
    "customer.bank_name",
)

#: Допуск падения метрик относительно baseline.
TOLERANCE = 0.02

#: Сколько полей в топах missed / wrong / extra.
TOP_FIELDS = 20

#: Пауза между запросами: ~190 запросов в час при лимите Freemium ~200.
DEFAULT_PAUSE = 19.0

#: Пауза, если пришёл 429 (лимит запросов).
RATE_LIMIT_PAUSE = 60.0

#: Сколько раз повторить сценарий после 429, прежде чем записать ошибку.
MAX_RATE_LIMIT_RETRIES = 2

#: Больше столько ответов 429 за 10 минут — СТОП (требование шага).
RATE_LIMIT_STOP_COUNT = 5
RATE_LIMIT_STOP_WINDOW = 600.0

#: Признаки пути или имени файла оператора в тексте (СТОП, если найдено).
OPERATOR_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\|\\\\[A-Za-z]|/Users/|/home/|\.docx\b|\.xlsx\b|\.pdf\b|"
    r"\.jpg\b|\.png\b)", re.IGNORECASE,
)

#: Остаток плейсхолдера в ответе модели — признак несработавшего восстановления.
PLACEHOLDER_RE = re.compile(r"<<[A-ZА-Я_]+_\d+>>")

#: Значения сценария, которые обязаны быть закрыты плейсхолдером.
#: Ключи РАЗНЫЕ ПО РАЗДЕЛАМ: `full_name` водителя — это человек, `full_name`
#: организации — название юрлица, и персональными данными оно не является
#: (псевдонимизатор их поэтому и не трогает — см. core/pseudonymizer.py).
#: Требовать плейсхолдер на название ООО значило бы ловить не утечку,
#: а собственную слишком широкую проверку.
PERSONAL_KEYS_BY_SECTION = {
    "driver": ("full_name", "passport_series", "passport_number",
               "license_series", "license_number", "birth_place", "phone",
               "registration_address"),
    "carrier": ("director_name",),
    "customer": ("director_name",),
}

#: Поля, по которым строится карта «где облако, где правила».
FIELD_KEYS_ORDER = (
    "driver.full_name", "driver.birth_date", "driver.birth_place",
    "driver.passport_series", "driver.passport_number",
    "driver.passport_issue_date", "driver.passport_issuer",
    "driver.passport_code", "driver.registration_address",
    "driver.license_series", "driver.license_number",
    "driver.license_issue_date", "driver.license_expiry_date",
    "driver.license_categories", "driver.phone",
    "carrier.full_name", "carrier.entity_type", "carrier.inn", "carrier.kpp",
    "carrier.ogrn", "carrier.legal_address", "carrier.actual_address",
    "carrier.bank_account", "carrier.bik", "carrier.correspondent_account",
    "carrier.bank_name", "carrier.director_name", "carrier.director_position",
    "carrier.phone", "carrier.email",
    "customer.full_name", "customer.entity_type", "customer.inn",
    "customer.kpp", "customer.ogrn", "customer.legal_address",
    "customer.actual_address", "customer.bank_account", "customer.bik",
    "customer.correspondent_account", "customer.bank_name",
    "customer.director_name", "customer.director_position", "customer.phone",
    "vehicles.vin", "vehicles.brand_model", "vehicles.plate_number",
    "vehicles.year", "vehicles.color", "vehicles.vehicle_type",
    "tractor.brand_model", "tractor.plate_number", "tractor.color",
    "tractor.year",
    "trailer.brand_model", "trailer.plate_number", "trailer.color",
    "trailer.year",
    "contract.number", "contract.date", "contract.route",
    "contract.vat_rate", "contract.payment_days",
)

#: Остановка по сигналу (Ctrl+C): прогресс дописывается, прогон завершается.
_STOP = False

#: Служебные значения, которые системный промпт велит ставить ВСЕГДА, даже
#: когда в документе их нет: «НДС в РФ 22 %, если не указано иное», «год
#: выпуска 0, если не указан». Приложение их не считает содержимым
#: (`GigaChatClient._has_meaningful_values`), а формула precision — считает.
#: Для этого и вторая метрика: `precision_core` без таких подстановок.
PROMPT_DEFAULT_VALUES = {
    "contract.vat_rate": {"22", "20"},
    "contract.payment_days": {"10", "0"},
    "contract.price_without_vat": {"0"},
    "contract.price_input": {"0"},
    "vehicles.year": {"0"},
    "tractor.year": {"0"},
    "trailer.year": {"0"},
}


def _handle_signal(signum, frame):                              # noqa: ARG001
    global _STOP
    _STOP = True


class SafetyStop(RuntimeError):
    """Нарушение проверки безопасности: прогон немедленно останавливается."""


class AuthStop(RuntimeError):
    """401/403: ключ или scope — дальше идти нельзя."""


class RateLimitStop(RuntimeError):
    """Слишком много 429 за короткое время."""


# ─────────────────────────────────────────────────────────────
# Метрики
# ─────────────────────────────────────────────────────────────

def _flat_norm(source: Any) -> Dict[str, str]:
    """Плоская карта «поле → нормализованное значение» (ключи без индексов)."""
    result: Dict[str, str] = {}
    for key, value in flatten(source).items():
        if not _norm(value):
            continue
        result[re.sub(r"\[\d+\]", "", key)] = _norm_field(key, value)
    return result


def prompt_default_extras(expected: Dict[str, Any], got: Any) -> List[str]:
    """
    Поля, заполненные моделью «по умолчанию из промпта», а не из документа.

    Промпт требует ставить НДС 22 %, срок оплаты и год выпуска, даже когда
    их в тексте нет. В договор они и должны попадать — это настройки
    приложения, а не ошибка распознавания, — но в precision они весят как
    лишние поля. Возвращаются ТОЛЬКО ИМЕНА полей: значений здесь не хранится.
    """
    expected_keys = set(_flat_norm(expected))
    return sorted(
        key for key, value in _flat_norm(got).items()
        if key not in expected_keys and value in PROMPT_DEFAULT_VALUES.get(key, ())
    )


def field_stats(expected: Dict[str, Any], got: Any) -> Dict[str, Dict[str, int]]:
    """
    Статистика по каждому полю: сколько раз было в эталоне, найдено, совпало.

    Нужна для карты «какие поля стоит отдавать облаку, а какие хватает
    разобрать правилами»: агрегат по документам этого не показывает.
    """
    stats: Dict[str, Dict[str, int]] = {}

    def flat(source: Any) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for key, value in flatten(source).items():
            if not _norm(value):
                continue
            result[re.sub(r"\[\d+\]", "", key)] = _norm_field(key, value)
        return result

    expected_flat = flat(expected)
    got_flat = flat(got)
    for key, value in expected_flat.items():
        bucket = stats.setdefault(key, {"expected": 0, "found": 0, "matched": 0})
        bucket["expected"] += 1
        if key in got_flat:
            bucket["found"] += 1
            if got_flat[key] == value:
                bucket["matched"] += 1
    return stats


def merge_field_stats(target: Dict[str, Dict[str, int]],
                      source: Dict[str, Dict[str, int]]) -> None:
    for key, bucket in source.items():
        slot = target.setdefault(key, {"expected": 0, "found": 0, "matched": 0})
        for name, value in bucket.items():
            slot[name] += value


def group_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """recall / precision / F1 по группе сценариев (сумма tp, expected, found)."""
    expected = sum(row["expected"] for row in rows)
    found = sum(row["found"] for row in rows)
    true_positive = sum(row["true_positive"] for row in rows)
    service = sum(row.get("default_extras", 0) for row in rows)
    recall = true_positive / expected if expected else 0.0
    precision = true_positive / found if found else 0.0
    found_core = found - service
    precision_core = true_positive / found_core if found_core > 0 else 0.0
    f1 = 2 * recall * precision / (recall + precision) if (recall + precision) else 0.0
    return {
        "documents": len(rows),
        "expected": expected,
        "found": found,
        "true_positive": true_positive,
        "service_filled": service,
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "precision_core": round(precision_core, 4),
        "f1": round(f1, 4),
    }


def summarise(results: List[Dict[str, Any]], source: str) -> Dict[str, Any]:
    """
    Свод метрик по одному источнику ("gigachat" или "local").

    Считаются только сценарии, где у источника есть результат: сбой запроса
    не должен улучшать или ухудшать метрику — он виден отдельным счётчиком.
    """
    rows, by_kind, by_level = [], {}, {}
    fields: Dict[str, Dict[str, int]] = {}
    for item in results:
        data = item.get(source)
        if not data:
            continue
        row = {"expected": data["expected"], "found": data["found"],
               "true_positive": data["true_positive"],
               "default_extras": len(data.get("default_extras") or [])}
        rows.append(row)
        by_kind.setdefault(item["kind"], []).append(row)
        by_level.setdefault(item["level"], []).append(row)
        merge_field_stats(fields, data.get("field_stats") or {})
    report = {
        "overall": group_metrics(rows),
        "by_kind": {kind: group_metrics(group) for kind, group in sorted(by_kind.items())},
        "by_level": {level: group_metrics(group)
                     for level, group in sorted(by_level.items())},
        "by_field": {key: fields[key] for key in FIELD_KEYS_ORDER if key in fields},
    }
    for key in sorted(fields):
        report["by_field"].setdefault(key, fields[key])
    return report


# ─────────────────────────────────────────────────────────────
# Прогресс
# ─────────────────────────────────────────────────────────────

def load_progress() -> Dict[str, Any]:
    if not PROGRESS_PATH.exists():
        return {"version": 1, "results": {}, "counters": {}}
    try:
        data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Прогресс не прочитан ({type(exc).__name__}) — начинаю заново")
        return {"version": 1, "results": {}, "counters": {}}
    data.setdefault("results", {})
    data.setdefault("counters", {})
    return data


def save_progress(progress: Dict[str, Any]) -> None:
    """Атомарная запись: обрыв на записи не портит файл прогресса."""
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    progress["updated_at"] = datetime.now().isoformat(timespec="seconds")
    temporary = PROGRESS_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(progress, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    temporary.replace(PROGRESS_PATH)


# ─────────────────────────────────────────────────────────────
# Безопасность
# ─────────────────────────────────────────────────────────────

def safety_check(scenario: Dict[str, Any], db_rows: List[str],
                 number: int) -> Dict[str, Any]:
    """
    Проверки перед отправкой. Возвращает отчёт (числа и метки, без значений).

    :raises SafetyStop: если текст нельзя отправлять наружу.
    """
    text = scenario["text"]

    if OPERATOR_PATH_RE.search(text):
        raise SafetyStop(f"#{number}: в тексте найден путь или имя файла оператора")

    collisions = find_db_collisions(scenario, db_rows)
    if collisions:
        raise SafetyStop(
            f"#{number}: {len(collisions)} значений сценария совпали с рабочей базой")

    masked, mapping = Pseudonymizer().anonymize(text)
    report = {"placeholders": len(mapping), "unmasked_personal": 0, "masked_ok": True}

    has_personal = any(
        str(item.get(key) or "").strip()
        for section, payload in scenario["ground_truth"].items()
        for item in (payload if isinstance(payload, list) else [payload])
        if isinstance(item, dict)
        for key in PERSONAL_KEYS_BY_SECTION.get(section, ())
    )
    if not mapping and has_personal:
        raise SafetyStop(
            f"#{number}: в тексте есть персональные данные, но обезличивание "
            f"не сработало (маппинг пуст)")

    for section, payload in scenario["ground_truth"].items():
        keys = PERSONAL_KEYS_BY_SECTION.get(section, ())
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in keys:
                value = str(item.get(key) or "").strip()
                if value and value.casefold() in masked.casefold():
                    report["unmasked_personal"] += 1
                    report["masked_ok"] = False
    if report["unmasked_personal"]:
        raise SafetyStop(
            f"#{number}: {report['unmasked_personal']} персональных значений "
            f"остались в тексте открытыми")

    report["length"] = len(text)
    report["masked_length"] = len(masked)
    return report


def check_answer(number: int, data: Any) -> None:
    """После ответа: плейсхолдеры обязаны быть восстановлены."""
    leftovers = PLACEHOLDER_RE.findall(json.dumps(data, ensure_ascii=False))
    if leftovers:
        raise SafetyStop(
            f"#{number}: в ответе остались невосстановленные плейсхолдеры "
            f"({len(leftovers)} шт., первый — {leftovers[0]})")


# ─────────────────────────────────────────────────────────────
# Локальный разбор и один запрос
# ─────────────────────────────────────────────────────────────

def local_result(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Локальный разбор текста — без сети и без ключа."""
    from core.document_import_service import extract_local_fields

    expected = scenario["ground_truth"]
    try:
        data = extract_local_fields(scenario["text"]) or {}
    except Exception as exc:                                     # noqa: BLE001
        data = {}
        result = score(expected, data)
        result["error"] = type(exc).__name__
        result["field_stats"] = field_stats(expected, data)
        result["default_extras"] = prompt_default_extras(expected, data)
        return result
    result = score(expected, data)
    result["field_stats"] = field_stats(expected, data)
    result["default_extras"] = prompt_default_extras(expected, data)
    result["error"] = ""
    return result


def gigachat_result(client: GigaChatClient, scenario: Dict[str, Any],
                    number: int = 0) -> Tuple[Dict[str, Any], float]:
    """Один запрос к GigaChat и метрика ответа."""
    started = time.perf_counter()
    data = client.recognize_text(scenario["text"], cancel=None)
    elapsed = time.perf_counter() - started
    check_answer(number, data)
    expected = scenario["ground_truth"]
    result = score(expected, data)
    result["field_stats"] = field_stats(expected, data)
    result["default_extras"] = prompt_default_extras(expected, data)
    result["error"] = ""
    if isinstance(data, dict) and isinstance(data.get("_raw_text"), str):
        result["warning"] = "текстовый fallback"
    return result, elapsed


# ─────────────────────────────────────────────────────────────
# Прогон
# ─────────────────────────────────────────────────────────────

def _sleep(seconds: float) -> None:
    """Пауза, прерываемая Ctrl+C: остаток ожидания не «съедается»."""
    remaining = max(0.0, float(seconds))
    while remaining > 0 and not _STOP:
        step = min(0.5, remaining)
        time.sleep(step)
        remaining -= step


def _describe_error(exc: BaseException) -> str:
    text = str(exc)
    if "429" in text:
        return "429"
    if "401" in text:
        return "401"
    if "403" in text:
        return "403"
    if "404" in text:
        return "404"
    return type(exc).__name__


def run(args) -> int:
    seeds = load_seeds(SEEDS_PATH)
    all_seeds = seeds["seeds"]
    all_meta = seeds["scenarios"]
    total = len(all_seeds)
    print(f"Сценариев в раскладке: {total}")

    limit = args.sample if args.sample else total
    if args.limit:
        limit = min(limit, args.limit)
    selection = list(range(limit))
    if args.kind:
        selection = [i for i in selection if all_meta[i]["kind"] in args.kind]
    if args.level:
        selection = [i for i in selection if all_meta[i]["level"] in args.level]
    print(f"К прогону: {len(selection)}")

    progress = load_progress() if args.resume else {"version": 1, "results": {},
                                                    "counters": {}}
    if args.resume and progress["results"]:
        print(f"Продолжение: уже готово {len(progress['results'])} сценариев")
    progress["model"] = args.model
    progress["scope"] = args.scope
    progress["pause"] = args.pause

    scenarios: Dict[int, Dict[str, Any]] = {}
    for index in selection:
        meta = all_meta[index]
        scenarios[meta["number"]] = scenario_from_seed(
            all_seeds[index], meta["kind"], meta["level"])

    # ── Локальный разбор: бесплатно, поэтому считается для ВСЕЙ выборки ──
    for number, scenario in scenarios.items():
        entry = progress["results"].setdefault(str(number), {"number": number})
        entry.setdefault("kind", all_meta[number - 1]["kind"])
        entry.setdefault("level", all_meta[number - 1]["level"])
        if not entry.get("local"):
            entry["local"] = local_result(scenario)

    db_rows = db_haystack()
    print(f"Строк рабочей базы для сверки: {len(db_rows)}")

    counters = {"requests": 0, "ok": 0, "errors": 0, "safety": 0,
                "rate_limited": 0, "seconds": 0.0}
    progress["counters"] = counters
    save_progress(progress)

    if args.local_only or args.dry_run:
        if args.dry_run:
            print("Режим --dry-run: проверки безопасности без запросов")
            for number in sorted(scenarios):
                try:
                    safety_check(scenarios[number], db_rows, number)
                except SafetyStop as exc:
                    print(f"СТОП (безопасность): {exc}")
                    return 2
            print(f"Проверки безопасности пройдены: {len(scenarios)} сценариев")
        else:
            print("Режим --local-only: сеть не используется")
        report = build_report(progress, selection, all_meta, args, counters)
        print_report(report)
        if args.write_baseline:
            write_baseline(report)
        elif BASELINE_PATH.exists():
            print(verdict(report, load_baseline(), len(selection)))
        return 0

    if args.write_baseline:
        # Эталонный прогон пишется «с нуля»: старые ответы другой модели
        # смешивать с новыми нельзя.
        stale = [key for key, value in progress["results"].items()
                 if key in {str(all_meta[i]["number"]) for i in selection}
                 and value.get("gigachat")]
        if stale and not args.resume:
            print(f"Сброшено прежних ответов модели: {len(stale)}")
            for key in stale:
                progress["results"][key].pop("gigachat", None)

    client = GigaChatClient(scope=args.scope, model=args.model)
    print(f"Модель: {client.model} | scope: {client.scope} | "
          f"пауза {args.pause} с")

    rate_limit_hits: List[float] = []
    pending = [n for n in sorted(scenarios) if not progress["results"][str(n)].get("gigachat")]
    print(f"К отправке: {len(pending)} | "
          f"готовых ответов: {len(scenarios) - len(pending)}")

    for position, number in enumerate(pending, start=1):
        if _STOP:
            print("Остановка по сигналу — прогресс сохранён")
            break
        meta = all_meta[number - 1]
        scenario = scenarios[number]
        entry = progress["results"][str(number)]

        # ── Безопасность ──
        try:
            entry["safety"] = safety_check(scenario, db_rows, number)
        except SafetyStop as exc:
            counters["safety"] += 1
            save_progress(progress)
            print(f"\nСТОП (безопасность): {exc}")
            return 2

        # ── Запрос ──
        cycle_started = time.perf_counter()
        status, error = "ok", ""
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                counters["requests"] += 1
                result, elapsed = gigachat_result(client, scenario, number)
                entry["gigachat"] = result
                entry["seconds"] = round(elapsed, 2)
                counters["ok"] += 1
                counters["seconds"] += elapsed
                status = "ok"
                break
            except Exception as exc:                             # noqa: BLE001
                kind = _describe_error(exc)
                error = kind
                if kind == "401":
                    save_progress(progress)
                    print(f"\nСТОП (401): ключ недействителен. "
                          f"Запустите: python set_key.py")
                    return 2
                if kind == "403":
                    save_progress(progress)
                    print(f"\nСТОП (403): scope «{args.scope}» отклонён. "
                          f"Попробуйте --scope GIGACHAT_API_B2B.")
                    return 2
                if kind == "404":
                    save_progress(progress)
                    print(f"\nСТОП (404): модель «{args.model}» не найдена. "
                          f"Проверьте имя в GET /models.")
                    return 2
                if kind == "429":
                    counters["rate_limited"] += 1
                    now = time.time()
                    rate_limit_hits.append(now)
                    rate_limit_hits = [t for t in rate_limit_hits
                                       if now - t <= RATE_LIMIT_STOP_WINDOW]
                    if len(rate_limit_hits) > RATE_LIMIT_STOP_COUNT:
                        save_progress(progress)
                        print(f"\nСТОП (429): лимит запросов исчерпан "
                              f"({len(rate_limit_hits)} за 10 минут)")
                        return 2
                    if attempt < MAX_RATE_LIMIT_RETRIES:
                        print(f"  429 — пауза {RATE_LIMIT_PAUSE:.0f} с "
                              f"(повтор {attempt + 1}/{MAX_RATE_LIMIT_RETRIES})")
                        _sleep(RATE_LIMIT_PAUSE)
                        continue
                status = "error"
                entry["error"] = kind
                entry["error_text"] = str(exc)[:200]
                counters["errors"] += 1
                break

        entry["status"] = status
        save_progress(progress)

        elapsed_cycle = time.perf_counter() - cycle_started
        pause = max(0.0, args.pause - elapsed_cycle)
        done = counters["ok"] + counters["errors"]
        metric = entry.get("gigachat") or {}
        print(f"[{done:>4}/{len(pending)}] #{number} {meta['kind']}/{meta['level']} "
              f"{status}"
              + (f" r={metric['recall']:.2f} p={metric['precision']:.2f} "
                 f"f1={metric['f1']:.2f}" if metric else f" ({error})")
              + f" | {elapsed_cycle:.1f} с + пауза {pause:.1f} с", flush=True)

        if done % 25 == 0:
            partial = summarise(list(progress["results"].values()), "gigachat")
            print(f"   --- промежуточно: n={partial['overall']['documents']} "
                  f"recall={partial['overall']['recall']:.4f} "
                  f"precision={partial['overall']['precision']:.4f} "
                  f"f1={partial['overall']['f1']:.4f}", flush=True)

        if position < len(pending):
            _sleep(pause)

    report = build_report(progress, selection, all_meta, args, counters)
    print_report(report)

    if args.write_baseline:
        write_baseline(report)
        return 0
    if BASELINE_PATH.exists():
        print(verdict(report, load_baseline(), len(selection)))
        return 0 if _verdict_ok(report, load_baseline(), len(selection)) else 1
    print("Baseline отсутствует — сравнивать не с чем (запустите с --write-baseline)")
    return 0


# ─────────────────────────────────────────────────────────────
# Отчёт и baseline
# ─────────────────────────────────────────────────────────────

def build_report(progress: Dict[str, Any], selection: List[int],
                 all_meta: List[Dict[str, Any]], args,
                 counters: Dict[str, Any]) -> Dict[str, Any]:
    numbers = {str(all_meta[index]["number"]) for index in selection}
    results = [progress["results"][key] for key in sorted(numbers, key=int)
               if key in progress["results"]]
    done = [item for item in results if item.get("gigachat")]
    return {
        "scenarios": len(results),
        "answered": len(done),
        "model": args.model,
        "scope": args.scope,
        "pause_seconds": args.pause,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "requests": dict(counters),
        "gigachat": summarise(results, "gigachat"),
        "local": summarise(results, "local"),
    }


def write_baseline(report: Dict[str, Any], path: Path = BASELINE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    print(f"Baseline записан: {path}")


def load_baseline(path: Path = BASELINE_PATH) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────
# Метрики по всему прогрессу: где именно ошибается каждый движок
# ─────────────────────────────────────────────────────────────

def _result_rows(progress: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Записи прогресса по возрастанию номера сценария."""
    results = progress.get("results") or {}
    keys = [key for key in results if str(key).isdigit()]
    return [results[key] for key in sorted(keys, key=int)]


def _empty_field() -> Dict[str, int]:
    return {"expected": 0, "missed": 0, "wrong": 0,
            "found": 0, "matched": 0, "extra": 0,
            "extra_default": 0, "extra_schema": 0, "extra_real": 0}


def _share(part: int, total: int) -> float:
    """Доля с округлением до 4 знаков; пустой знаменатель — 0.0, а не деление на ноль."""
    return round(part / total, 4) if total else 0.0


def _f1(recall: float, precision: float) -> float:
    return round(2 * recall * precision / (recall + precision), 4) \
        if (recall + precision) else 0.0


def _schema_artifact(key: str, schema: Dict[str, Any]) -> bool:
    """Есть ли поле в схеме разбора. `contract.loading_address` — есть."""
    section, _, field = key.partition(".")
    return section not in schema or field not in schema[section]


def field_error_stats(results: List[Dict[str, Any]],
                      source: str) -> Dict[str, Dict[str, int]]:
    """
    По каждому полю: сколько раз было в эталоне, пропущено, испорчено, лишнее.

    `missed` и `wrong` — взаимоисключающие: поле либо не пришло вовсе, либо
    пришло другим значением. `extra` — поле, которого в эталоне не было;
    из них `extra_default` — служебные подстановки промпта (`vat_rate`,
    год выпуска 0), `extra_schema` — поля схемы ответа, которых нет в
    эталоне сценария, `extra_real` — настоящие лишние поля. Хранятся ТОЛЬКО
    имена полей и счётчики: значений здесь нет.
    """
    from core.document_import_service import SCHEMA

    stats: Dict[str, Dict[str, int]] = {}
    for item in results:
        data = item.get(source)
        if not data:
            continue
        service = set(data.get("default_extras") or [])
        for key, bucket in (data.get("field_stats") or {}).items():
            slot = stats.setdefault(key, _empty_field())
            slot["expected"] += bucket.get("expected", 0)
            slot["found"] += bucket.get("found", 0)
            slot["matched"] += bucket.get("matched", 0)
        for key in data.get("missed") or []:
            stats.setdefault(key, _empty_field())["missed"] += 1
        for key in data.get("wrong") or []:
            stats.setdefault(key, _empty_field())["wrong"] += 1
        for key in data.get("extra") or []:
            slot = stats.setdefault(key, _empty_field())
            slot["extra"] += 1
            if key in service:
                slot["extra_default"] += 1
            elif _schema_artifact(key, SCHEMA):
                slot["extra_schema"] += 1
            else:
                slot["extra_real"] += 1
    return stats


def top_error_fields(stats: Dict[str, Dict[str, int]], key: str,
                     limit: int = TOP_FIELDS) -> List[Dict[str, Any]]:
    """
    Топ полей по одному счётчику (`missed` / `wrong` / `extra`).

    Сортировка: сначала по числу (убывание), затем по имени — чтобы файл
    не «поехал» от перестановки одинаковых чисел.
    """
    items = [(name, bucket) for name, bucket in stats.items() if bucket.get(key, 0)]
    items.sort(key=lambda pair: (-pair[1][key], pair[0]))
    return [{"field": name, key: bucket[key], "expected": bucket["expected"],
             "rate": _share(bucket[key], bucket["expected"])}
            for name, bucket in items[:limit]]


def top_extra_fields(stats: Dict[str, Dict[str, int]],
                     limit: int = TOP_FIELDS) -> List[Dict[str, Any]]:
    """
    Топ-20 лишних полей с пометкой, ОТКУДА поле взялось.

    * `default_extras` — служебные подстановки промпта (`vat_rate`,
      `payment_days`, год выпуска `0`). В документе они нужны: это настройки
      приложения, а не ошибка распознавания, — но формула `score` считает их
      лишними, поэтому `precision_core` считается без них.
    * `вне схемы` — поля, которых нет во внутренней схеме разбора
      (`core.document_import_service.SCHEMA`), но которые системный промпт
      требует возвращать: `contract.price_without_vat`, `loading_address`,
      `unloading_address`, `carrier.short_name`. Приложение их использует
      (вкладка «Стоимость», точки маршрута), а замер их записывает в потери
      precision — это расхождение ЗАМЕРА и реальности, а не ошибка модели.
    * `лишнее` — настоящее лишнее поле: его нет ни в эталоне, ни в подстановках.
    """
    items = [(name, bucket) for name, bucket in stats.items() if bucket.get("extra")]
    items.sort(key=lambda pair: (-pair[1]["extra"], pair[0]))
    rows = []
    for name, bucket in items[:limit]:
        if bucket["extra_default"]:
            kind = "default_extras"
        elif bucket["extra_schema"]:
            kind = "вне схемы"
        else:
            kind = "лишнее"
        rows.append({
            "field": name,
            "extra": bucket["extra"],
            "expected": bucket["expected"],
            "extra_default": bucket["extra_default"],
            "extra_schema": bucket["extra_schema"],
            "extra_real": bucket["extra_real"],
            "kind": kind,
        })
    return rows


def field_metrics_pair(results: List[Dict[str, Any]], field: str) -> Dict[str, Any]:
    """recall / precision / f1 поля у обоих движков (по одной партии)."""
    row: Dict[str, Any] = {}
    for source in ("gigachat", "local"):
        stats = field_error_stats(results, source)
        bucket = stats.get(field) or _empty_field()
        row[source] = {
            "expected": bucket["expected"],
            "found": bucket["found"],
            "matched": bucket["matched"],
            "recall": _share(bucket["matched"], bucket["expected"]),
            "precision": _share(bucket["matched"], bucket["found"]),
            "f1": _f1(_share(bucket["matched"], bucket["expected"]),
                      _share(bucket["matched"], bucket["found"])),
        }
    return row


def load_legacy_numbers(path: Path = LEGACY_NUMBERS_PATH) -> set:
    """Номера сценариев, ответы по которым сняты ДО фикса псевдонимизатора."""
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8").strip()
    return {int(part) for part in text.split(",") if part.strip()}


def count_mask_newlines() -> int:
    """
    Сколько сценариев имеют перенос строки ВНУТРИ маски (текущий код).

    Тот же замер, что в `tests/_tmp/_probe_mask_newline.py`, но считается на
    месте: он нужен только для сравнения «было → стало» в отчёте.
    """
    from tools.make_text_scenarios import load_seeds, scenario_from_seed

    seeds = load_seeds()
    pseudo = Pseudonymizer()
    count = 0
    for seed, meta in zip(seeds["seeds"], seeds["scenarios"]):
        scenario = scenario_from_seed(seed, meta["kind"], meta["level"])
        _masked, mapping = pseudo.anonymize(scenario["text"])
        if any("\n" in value for value in mapping.values()):
            count += 1
    return count


def build_metrics_1000(progress: Dict[str, Any],
                       path: Path = METRICS_1000_PATH) -> Dict[str, Any]:
    """
    Метрики по ВСЕМУ прогрессу (не по выборке запуска).

    Отвечает на вопрос «что чинить следующим»: где именно теряются поля,
    где они приходят неверными и какие поля добавляются зря. Отдельно —
    партии «до фикса псевдонимизатора» и «после», потому что прогресс
    смешанный: 264 ответа сняты старым кодом.
    """
    results = _result_rows(progress)
    answered = [item for item in results if item.get("gigachat")]
    legacy = load_legacy_numbers()
    old_rows = [item for item in answered if item["number"] in legacy]
    new_rows = [item for item in answered if item["number"] not in legacy]

    report: Dict[str, Any] = {
        "model": progress.get("model", ""),
        "scope": progress.get("scope", ""),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": progress.get("updated_at", ""),
        "scenarios": len(results),
        "scenarios_with_answer": len(answered),
        "scenarios_with_error": sum(1 for item in results if item.get("error")),
        "batches": {
            "before_pseudonymizer_fix": {
                "label": "снято ДО фикса 0cbf386", "scenarios": len(old_rows)},
            "after_pseudonymizer_fix": {
                "label": "снято ПОСЛЕ фикса 0cbf386", "scenarios": len(new_rows)},
        },
        "totals": {},
        "by_kind": {},
        "by_level": {},
        "top_missed": {},
        "top_wrong": {},
        "top_extra": {},
        "fields_after_pseudonymizer_fix": {},
    }
    for source in ("gigachat", "local"):
        summary = summarise(answered, source)
        report["totals"][source] = summary["overall"]
        report["by_kind"][source] = summary["by_kind"]
        report["by_level"][source] = summary["by_level"]
        engine_fields = field_error_stats(answered, source)
        report["top_missed"][source] = top_error_fields(engine_fields, "missed")
        report["top_wrong"][source] = top_error_fields(engine_fields, "wrong")
        report["top_extra"][source] = top_extra_fields(engine_fields)

    fields: Dict[str, Any] = {}
    for field in FIELDS_AFTER_PSEUDONYMIZER_FIX:
        fields[field] = {
            "before": field_metrics_pair(old_rows, field),
            "after": field_metrics_pair(new_rows, field),
            "all": field_metrics_pair(answered, field),
            "matched": {
                "before": fields_expected_map(old_rows, field),
                "after": fields_expected_map(new_rows, field),
                "all": fields_expected_map(answered, field),
            },
        }
    report["fields_after_pseudonymizer_fix"] = fields

    if LEGACY_MASK_PATH.exists():
        legacy_mask = json.loads(LEGACY_MASK_PATH.read_text(encoding="utf-8"))
        numbers = legacy_mask.get("numbers") or []
        report["pseudonymizer_mask"] = {
            "scenarios_with_newline_before": len(numbers),
            "scenarios_with_newline_after": count_mask_newlines(),
            "scenarios_with_label_inside_before": legacy_mask.get("label_eaten", 0),
            "masks_total_before": legacy_mask.get("total_masks", 0),
        }
    if path:
        write_json(report, path)
    return report


def fields_expected_map(results: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    """Сколько раз поле «совпало / было в эталоне» у каждого движка."""
    row: Dict[str, int] = {}
    for source in ("gigachat", "local"):
        bucket = field_error_stats(results, source).get(field) or _empty_field()
        row[f"{source}_matched"] = bucket["matched"]
        row[f"{source}_expected"] = bucket["expected"]
    return row


def write_json(report: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    print(f"Метрики записаны: {path}")


def print_metrics(report: Dict[str, Any]) -> None:
    print("=" * 68)
    print(f"Метрики по {report['scenarios']} сценариям "
          f"(с ответом {report['scenarios_with_answer']}, "
          f"с ошибкой {report['scenarios_with_error']})")
    for source, title in (("gigachat", "GigaChat"), ("local", "Локальный разбор")):
        row = report["totals"][source]
        print(f"{title}: recall={row['recall']:.4f} precision={row['precision']:.4f} "
              f"precision_core={row['precision_core']:.4f} f1={row['f1']:.4f}")
    print("\nТоп-10 пропущенных полей (GigaChat):")
    for item in report["top_missed"]["gigachat"][:10]:
        print(f"    {item['field']:<34} missed {item['missed']:>4} из "
              f"{item['expected']:>4} ({item['rate']:.2f})")
    print("Топ-10 неверных полей (GigaChat):")
    for item in report["top_wrong"]["gigachat"][:10]:
        print(f"    {item['field']:<34} wrong  {item['wrong']:>4} из "
              f"{item['expected']:>4} ({item['rate']:.2f})")
    print("Топ-10 лишних полей (GigaChat):")
    for item in report["top_extra"]["gigachat"][:10]:
        print(f"    {item['field']:<34} extra  {item['extra']:>4} "
              f"(служебных {item['extra_default']}, вне схемы {item['extra_schema']}, "
              f"настоящих {item['extra_real']}) — {item['kind']}")
    print("\nПоля после фикса псевдонимизатора (recall: было / стало):")
    for field, row in report["fields_after_pseudonymizer_fix"].items():
        before = row["before"]["gigachat"]
        after = row["after"]["gigachat"]
        print(f"    {field:<34} облако {before['recall']:.2f} / "
              f"{after['recall']:.2f}; правила {row['before']['local']['recall']:.2f} "
              f"/ {row['after']['local']['recall']:.2f}")
    mask = report.get("pseudonymizer_mask")
    if mask:
        print(f"\nМаска с переносом строки: было "
              f"{mask['scenarios_with_newline_before']}, стало "
              f"{mask['scenarios_with_newline_after']}; "
              f"с подписью поля внутри маски было "
              f"{mask['scenarios_with_label_inside_before']}")


def main_report(argv=None) -> int:
    """Собрать метрики по всему прогрессу и записать их в JSON."""
    parser = argparse.ArgumentParser(
        description="Метрики стресс-теста текста по всему прогрессу прогона.")
    parser.add_argument("--progress", default=str(PROGRESS_PATH),
                        help="файл прогресса (по умолчанию tests/_tmp/...)")
    parser.add_argument("--out", default=str(METRICS_1000_PATH),
                        help="куда записать метрики")
    parser.add_argument("--print", dest="show", action="store_true",
                        help="печатать сводку, не записывая файл")
    args = parser.parse_args(argv)

    progress_path = Path(args.progress)
    if not progress_path.exists():
        print(f"Нет файла прогресса: {progress_path}\n"
              f"Сначала прогон: python tools/run_text_stress_gigachat.py --resume")
        return 2
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    report = build_metrics_1000(progress, None if args.show else Path(args.out))
    print_metrics(report)
    return 0


def _verdict_ok(report: Dict[str, Any], baseline: Dict[str, Any],
                selection_size: int) -> bool:
    """Сравнение допустимо только на полной выборке."""
    if selection_size < baseline.get("scenarios", 0):
        return True
    for source in ("gigachat", "local"):
        current = report[source]["overall"]
        was = baseline.get(source, {}).get("overall")
        if not was:
            continue
        if current["recall"] < was["recall"] - TOLERANCE:
            return False
    return True


def verdict(report: Dict[str, Any], baseline: Dict[str, Any],
            selection_size: int) -> str:
    if selection_size < baseline.get("scenarios", 0):
        return (f"Прогон неполный ({selection_size} из {baseline['scenarios']}) — "
                f"сравнение с baseline не делается")
    lines = [f"Сравнение с baseline (допуск {TOLERANCE}):"]
    ok = True
    for source, title in (("gigachat", "GigaChat"), ("local", "локальный")):
        current = report[source]["overall"]
        was = baseline.get(source, {}).get("overall")
        if not was:
            continue
        delta = current["recall"] - was["recall"]
        mark = "OK" if delta >= -TOLERANCE else "РЕГРЕССИЯ"
        ok = ok and delta >= -TOLERANCE
        lines.append(f"  {title}: recall {was['recall']:.4f} → "
                     f"{current['recall']:.4f} ({delta:+.4f}) — {mark}")
    lines.append("ИТОГ: " + ("РЕГРЕССИИ НЕТ" if ok else "ЕСТЬ РЕГРЕССИЯ"))
    return "\n".join(lines)


def print_report(report: Dict[str, Any]) -> None:
    print("=" * 68)
    print(f"Модель: {report['model']} | scope: {report['scope']} | "
          f"ответов: {report['answered']} из {report['scenarios']}")
    requests = report["requests"]
    print(f"Запросов: {requests.get('requests', 0)} | ок: {requests.get('ok', 0)} | "
          f"ошибок: {requests.get('errors', 0)} | 429: {requests.get('rate_limited', 0)}")
    if requests.get("seconds"):
        count = max(1, requests.get("ok", 1))
        print(f"Среднее время ответа: {requests['seconds'] / count:.1f} с")
    for source, title in (("gigachat", "GigaChat"), ("local", "Локальный разбор")):
        overall = report[source]["overall"]
        print(f"\n{title}: recall={overall['recall']:.4f} "
              f"precision={overall['precision']:.4f} "
              f"precision_core={overall['precision_core']:.4f} "
              f"f1={overall['f1']:.4f} "
              f"(полей в эталоне {overall['expected']}, найдено {overall['found']}, "
              f"служебных {overall['service_filled']})")
        print("  по видам:")
        for kind, row in report[source]["by_kind"].items():
            print(f"    {kind:<14} recall={row['recall']:.4f} "
                  f"precision={row['precision']:.4f} f1={row['f1']:.4f} "
                  f"(n={row['documents']})")
        print("  по уровням:")
        for level, row in report[source]["by_level"].items():
            print(f"    {level:<14} recall={row['recall']:.4f} "
                  f"precision={row['precision']:.4f} f1={row['f1']:.4f} "
                  f"(n={row['documents']})")

    cloud = report["gigachat"]["by_field"]
    rules = report["local"]["by_field"]
    better, worse = [], []
    for key in sorted(set(cloud) | set(rules)):
        c = cloud.get(key, {}).get("matched", 0) / max(1, cloud.get(key, {}).get("expected", 0))
        r = rules.get(key, {}).get("matched", 0) / max(1, rules.get(key, {}).get("expected", 0))
        expected = max(cloud.get(key, {}).get("expected", 0),
                       rules.get(key, {}).get("expected", 0))
        if expected < 10:
            continue
        (better if c > r + 0.05 else worse if r > c + 0.05 else []).append(
            (abs(c - r), key, c, r, expected))
    print("\nГде облако сильнее правил (топ-10):")
    for _, key, c, r, expected in sorted(better, reverse=True)[:10]:
        print(f"    {key:<34} облако {c:.2f} правила {r:.2f} (n={expected})")
    print("Где правила сильнее облака (топ-10):")
    for _, key, c, r, expected in sorted(worse, reverse=True)[:10]:
        print(f"    {key:<34} облако {c:.2f} правила {r:.2f} (n={expected})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Стресс-тест распознавания текста через GigaChat-3-Pro.")
    parser.add_argument("--write-baseline", action="store_true",
                        help="записать baseline_text_gigachat.json")
    parser.add_argument("--report", action="store_true",
                        help="только метрики по всему прогрессу: топы missed / "
                             "wrong / extra и партии «до/после фикса» → "
                             "baseline_text_gigachat_1000.json (без сети)")
    parser.add_argument("--resume", action="store_true",
                        help="продолжить с сохранённого прогресса")
    parser.add_argument("--sample", type=int, default=0,
                        help="взять только N первых сценариев")
    parser.add_argument("--limit", type=int, default=0,
                        help="максимум сценариев в этом запуске")
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE,
                        help=f"интервал между запросами, с (по умолчанию {DEFAULT_PAUSE})")
    parser.add_argument("--model", default=GigaChatClient.DEFAULT_MODEL,
                        help="имя модели GigaChat")
    parser.add_argument("--scope", default="GIGACHAT_API_PERS",
                        help="scope ключа: GIGACHAT_API_PERS или GIGACHAT_API_B2B")
    parser.add_argument("--kind", action="append", default=[],
                        help="только этот вид сценариев (можно несколько раз)")
    parser.add_argument("--level", action="append", default=[],
                        help="только этот уровень (можно несколько раз)")
    parser.add_argument("--local-only", action="store_true",
                        help="считать только локальный разбор, без сети")
    parser.add_argument("--dry-run", action="store_true",
                        help="без клиента: проверить раскладку и безопасность")
    args = parser.parse_args(argv)

    if args.report:
        return main_report(["--progress", str(PROGRESS_PATH),
                            "--out", str(METRICS_1000_PATH)])

    if not SEEDS_PATH.exists():
        print(f"Нет файла зёрен: {SEEDS_PATH}\n"
              f"Соберите его: python tools/make_text_scenarios.py --write-seeds")
        return 2

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):
        pass

    try:
        return run(args)
    except SafetyStop as exc:
        print(f"\nСТОП (безопасность): {exc}")
        return 2
    except AuthStop as exc:
        print(f"\nСТОП (доступ): {exc}")
        return 2
    except RateLimitStop as exc:
        print(f"\nСТОП (лимит): {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
