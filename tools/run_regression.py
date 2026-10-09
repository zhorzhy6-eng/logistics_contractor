#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Повторный прогон по зёрнам для регрессии (ЧАСТЬ 4 стресс-теста).

    python tools/run_regression.py                 # прогон и сравнение с baseline
    python tools/run_regression.py --write-seeds    # записать seeds.json
    python tools/run_regression.py --write-baseline # записать baseline.json
    python tools/run_regression.py --limit 100      # быстрый прогон на выборке
    python tools/run_regression.py --sample 50      # по 50 документов на вид

ЗАЧЕМ ОТДЕЛЬНЫЙ ИНСТРУМЕНТ
--------------------------
`tools/synthetic_stress.py synthetic` меряет качество разбора. Этот
инструмент отвечает на другой вопрос: «стало хуже, чем было?» Он берёт те же
зёрна, что записаны в git (`tests/data/synthetic_docs/seeds.json`), гоняет по
ним документы и сравнивает recall с baseline. Падение ниже допуска
(`--tolerance`) — это регрессия, и её видно без чтения глаз.

ЧТО ЗАПИСЫВАЕТСЯ В GIT
----------------------
`seeds.json` — 1000 чисел, `baseline.json` — метрики. Оба файла содержат
ТОЛЬКО числа: ни одного значения из документа. Документы и картинки лежат в
`tests/_tmp/` и в git не идут.
"""

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.make_synthetic_docs import (  # noqa: E402
    DEFECT_PLAN, KIND_PLAN, QUALITY_PLAN, SEED, _plan, build_document,
    build_values,
)
from tools.synthetic_stress import score, summarise_local  # noqa: E402

DATA_DIR = PROJECT_ROOT / "tests" / "data" / "synthetic_docs"
SEEDS_PATH = DATA_DIR / "seeds.json"
BASELINE_PATH = DATA_DIR / "baseline.json"

#: Схема файлов: меняется, если меняется их состав.
SCHEMA_VERSION = 1

#: Допуск регрессии: на сколько recall может упасть без тревоги. OCR —
#: шумная вещь, но не настолько: 0,02 — это «два поля из ста».
DEFAULT_TOLERANCE = 0.02


def write_seeds(path: Path = SEEDS_PATH) -> Dict[str, Any]:
    """
    Записывает 1000 зёрен генератора.

    В файл идут ТОЛЬКО числа: пересобрать сценарий по зерну может любой, а
    сами данные документа в git не попадают (AGENTS.md § 4).
    """
    plan = _plan(sum(KIND_PLAN.values()))
    rng = random.Random(SEED)
    seeds = [rng.randint(1, 2 ** 31 - 1) for _ in plan]

    payload = {
        "version": SCHEMA_VERSION,
        "seed": SEED,
        "count": len(seeds),
        "kinds": KIND_PLAN,
        "qualities": QUALITY_PLAN,
        "defects": DEFECT_PLAN,
        "seeds": seeds,
        "scenarios": [
            {"number": int(number), "kind": kind, "quality": quality,
             "defect": defect or ""}
            for (number, kind, quality, defect) in plan
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return payload


def load_seeds(path: Path = SEEDS_PATH) -> Dict[str, Any]:
    if not path.exists():
        return write_seeds(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _is_readable_json(path: Path) -> bool:
    """Файл есть и читается как JSON (иначе сравнение с ним невозможно)."""
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (OSError, ValueError):
        return False


def _stratified_numbers(plan: List[Dict[str, Any]], sample: int) -> List[int]:
    """
    По N сценариев на каждый вид — С СОХРАНЕНИЕМ ДОЛЕЙ КАЧЕСТВА.

    Просто «первые N» брать нельзя: у видов разный состав уровней качества, и
    выборка оказалась бы смещённой (у паспортов больше идеальных, у заявок
    больше плохих). Тогда сравнение с baseline показывало бы регрессию там,
    где её нет, — на этой ошибке инструмент и попался в первый раз.
    """
    by_kind: Dict[str, Dict[str, List[int]]] = defaultdict(lambda: defaultdict(list))
    for item in plan:
        by_kind[item["kind"]][item["quality"]].append(item["number"])

    numbers: List[int] = []
    for kind, groups in by_kind.items():
        total = sum(len(v) for v in groups.values())
        if not total:
            continue
        for quality, group in groups.items():
            share = max(1, round(sample * len(group) / total))
            numbers.extend(sorted(group)[:share])
    return sorted(numbers)


def run(limit: int = 0, sample: int = 0, workers: int = 4,
        use_gigachat: bool = False) -> Dict[str, Any]:
    """
    Прогон по зёрнам: собрать документ, распознать локально, сравнить.

    :param limit: взять первые N сценариев (0 — все).
    :param sample: взять по N сценариев КАЖДОГО вида с сохранением долей
        качества (0 — не ограничивать). Полное сравнение с baseline — только
        без этого аргумента: на выборке метрики по построению отличаются.
    :param use_gigachat: дополнительно прогнать до 25 худших через GigaChat
        (только синтетика). По умолчанию выключено: это внешний вызов.
    """
    seeds = load_seeds()
    plan = seeds["scenarios"]
    numbers = list(range(1, len(plan) + 1))
    if sample:
        numbers = _stratified_numbers(plan, sample)
    if limit:
        numbers = numbers[:limit]

    from core.document_import_service import extract_local_fields
    from tools.synthetic_stress import _recognize_path

    started = time.time()
    results: List[Dict[str, Any]] = []
    for done, number in enumerate(numbers, 1):
        item = plan[number - 1]
        seed = seeds["seeds"][number - 1]
        rng = random.Random(seed)

        built = build_values(rng, item["kind"])
        render_values = dict(built["values"])
        if item["defect"] == "lookalike_digits":
            from tools.make_synthetic_docs import _lookalike
            render_values = _lookalike(render_values, rng)
        render_values["number"] = number
        image = build_document(item["kind"], render_values, item["quality"],
                               item["defect"] or None)
        work = PROJECT_ROOT / "tests" / "_tmp" / "regression_images"
        work.mkdir(parents=True, exist_ok=True)
        path = work / f"{number}.png"
        image.save(path)

        entry: Dict[str, Any] = {
            "number": number, "kind": item["kind"], "quality": item["quality"],
            "defect": item["defect"], "source": "", "error": "",
        }
        try:
            text, source = _recognize_path(path)
            entry["source"] = source
            entry["score"] = score(built["gt"],
                                   extract_local_fields(text) if text else {})
        except Exception as exc:                            # noqa: BLE001
            entry["error"] = type(exc).__name__
            entry["score"] = score(built["gt"], {})

        results.append(entry)
        path.unlink(missing_ok=True)
        if done % 100 == 0:
            print(f"  обработано {done} из {len(numbers)}")

    report = summarise_local(results, time.time() - started)
    if use_gigachat:
        report["gigachat"] = _run_gigachat(results, limit=25)
    return report


def _run_gigachat(results: List[Dict[str, Any]], limit: int) -> Dict[str, Any]:
    """
    До 25 запросов к GigaChat на самых плохих СИНТЕТИЧЕСКИХ сценариях.

    Реальные документы сюда не попадают никогда: список берётся из
    результатов синтетического прогона.
    """
    from core.gigachat_client import GigaChatClient
    from core.recognizer import DataMapper
    from PIL import Image

    usable = [r for r in results if r["source"] and r["kind"] != "application"]
    usable.sort(key=lambda r: (r["score"]["recall"], r["number"]))
    worst = usable[:limit]
    if not worst:
        return {"requests": 0, "note": "нет сценариев для проверки"}

    client = GigaChatClient()
    if not getattr(client, "auth_key", ""):
        return {"requests": 0, "note": "ключ GigaChat не настроен"}

    import threading

    seeds = load_seeds()
    plan = seeds["scenarios"]
    work = PROJECT_ROOT / "tests" / "_tmp" / "regression_images"
    work.mkdir(parents=True, exist_ok=True)

    entries = []
    requests_made = 0
    for result in worst:
        number = result["number"]
        item = plan[number - 1]
        seed = seeds["seeds"][number - 1]
        built = build_values(random.Random(seed), item["kind"])
        render_values = dict(built["values"])
        if item["defect"] == "lookalike_digits":
            from tools.make_synthetic_docs import _lookalike
            render_values = _lookalike(render_values, random.Random(seed))
        render_values["number"] = number
        image = build_document(item["kind"], render_values, item["quality"],
                               item["defect"] or None)
        path = work / f"giga_{number}.png"
        image.save(path)
        entry: Dict[str, Any] = {"number": number, "kind": item["kind"],
                                 "status": "", "error": ""}
        try:
            requests_made += 1
            # Пара (данные, предупреждение) — распаковка обязательна.
            data, _warning = client.recognize_image(Image.open(path),
                                                    threading.Event())
            mapped = DataMapper.process_full_response(data or {})
            entry["status"] = "ok"
            entry["score"] = score(built["gt"], mapped or {})
        except Exception as exc:                            # noqa: BLE001
            entry["status"] = "error"
            entry["error"] = type(exc).__name__
            entry["score"] = score(built["gt"], {})
        entries.append(entry)
        path.unlink(missing_ok=True)
        time.sleep(2.0)

    return {
        "requests": requests_made,
        "results": entries,
        "metrics": summarise_local(
            [{"number": e["number"], "kind": e["kind"], "quality": "",
              "defect": "", "error": e["error"], "score": e["score"]}
             for e in entries], 0.0)["overall"],
    }


def compare(report: Dict[str, Any], baseline: Dict[str, Any],
            tolerance: float = DEFAULT_TOLERANCE) -> Dict[str, Any]:
    """
    Сравнивает прогон с baseline. Возвращает расхождения и вердикт.

    Сравнивается recall: именно он отвечает на вопрос «поля находятся?».
    Precision в регрессии не проверяется — она растёт, когда разбор начинает
    находить лишнее, и падение recall это уже поймает.
    """
    drops: List[Dict[str, Any]] = []
    for section in ("by_kind", "by_quality"):
        for key, expected in baseline.get(section, {}).items():
            actual = report.get(section, {}).get(key)
            if not actual:
                drops.append({"section": section, "key": key,
                              "expected": expected["recall"], "actual": None,
                              "drop": 1.0, "reason": "группа пропала"})
                continue
            drop = expected["recall"] - actual["recall"]
            if drop > tolerance:
                drops.append({"section": section, "key": key,
                              "expected": expected["recall"],
                              "actual": actual["recall"],
                              "drop": round(drop, 4)})

    overall_expected = baseline.get("overall", {}).get("recall", 0.0)
    overall_actual = report.get("overall", {}).get("recall", 0.0)
    overall_drop = overall_expected - overall_actual
    if overall_drop > tolerance:
        drops.append({"section": "overall", "key": "recall",
                      "expected": overall_expected, "actual": overall_actual,
                      "drop": round(overall_drop, 4)})

    return {
        "tolerance": tolerance,
        "ok": not drops,
        "overall_expected": overall_expected,
        "overall_actual": overall_actual,
        "drops": drops,
    }


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(
        description="Повторный прогон по зёрнам и сравнение с baseline"
    )
    parser.add_argument("--write-seeds", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sample", type=int, default=0)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--gigachat", action="store_true",
                        help="дополнительно до 25 запросов к GigaChat "
                             "(только синтетика)")
    args = parser.parse_args(argv)

    if args.write_seeds:
        payload = write_seeds()
        print(f"Зёрна записаны: {SEEDS_PATH} ({payload['count']} штук)")
        return 0

    if args.write_baseline:
        report = run(limit=args.limit, sample=args.sample,
                     use_gigachat=args.gigachat)
        baseline = {
            "version": SCHEMA_VERSION,
            "documents": len(report.get("by_kind", {})) and
            report["documents"],
            "overall": report["overall"],
            "by_kind": {k: {"recall": v["recall"], "precision": v["precision"],
                            "f1": v["f1"], "documents": v["documents"]}
                        for k, v in report["by_kind"].items()},
            "by_quality": {k: {"recall": v["recall"],
                               "precision": v["precision"], "f1": v["f1"],
                               "documents": v["documents"]}
                           for k, v in report["by_quality"].items()},
        }
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print(f"Baseline записан: {BASELINE_PATH}")
        print(json.dumps(baseline["overall"], ensure_ascii=False))
        return 0

    report = run(limit=args.limit, sample=args.sample,
                 use_gigachat=args.gigachat)
    overall = report["overall"]
    print(f"Документов: {report['documents']} ({report['seconds']} с)")
    print(f"recall={overall['recall']:.3f} "
          f"precision={overall['precision']:.3f} f1={overall['f1']:.3f}")

    if args.sample or args.limit:
        # На выборке сравнение с baseline некорректно: доли видов и уровней
        # качества другие по построению. Об этом честно сообщается, а не
        # выдаётся «регрессия», которой нет.
        print("Это ВЫБОРКА, а не полный прогон: сравнение с baseline "
              "пропущено.")
        print("Полное сравнение: python tools/run_regression.py (без "
              "--sample и --limit)")
        return 0

    if not BASELINE_PATH.exists() or not _is_readable_json(BASELINE_PATH):
        print("Baseline отсутствует или повреждён — сравнение пропущено.")
        print("Запишите его: python tools/run_regression.py --write-baseline")
        return 0

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    verdict = compare(report, baseline, args.tolerance)
    if verdict["ok"]:
        print(f"РЕГРЕССИИ НЕТ (допуск {args.tolerance}): "
              f"{verdict['overall_actual']:.3f} против "
              f"{verdict['overall_expected']:.3f}")
        return 0

    print(f"РЕГРЕССИЯ: {len(verdict['drops'])} расхождений")
    for drop in verdict["drops"]:
        print(f"  {drop['section']}/{drop['key']}: "
              f"{drop['expected']:.3f} → {drop['actual']}, "
              f"падение {drop['drop']:.3f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
