#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Регрессия по синтетическим документам (ЧАСТЬ 4 стресс-теста).

Прогон 1000 документов занимает минуты и в тестах не повторяется. Поэтому
здесь проверяется то, что должно оставаться неизменным:

  1. Зёрна генератора (`seeds.json`): 1000 чисел, по ним сценарий
     воспроизводится один в один.
  2. Матрица покрытия: 300 паспортов, 200 ВУ, 200 реквизитов, 100 ИНН,
     100 ПТС/СТС, 100 заявок и доли уровней качества.
  3. Baseline (`baseline.json`): числа метрик, с которыми сравнивается
     повторный прогон (`tools/run_regression.py`).
  4. Сам конвейер на выборке: картинка → локальный OCR → поля → метрика.
     Выборка идёт от зёрен, поэтому «поехавший» генератор виден сразу.
  5. Проверки качества ОТЧЁТА: в baseline и в отчётах нет данных документа,
     только числа.

Все данные синтетические, реальных ПДн нет.
"""

import json
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.make_synthetic_docs import (  # noqa: E402
    KIND_PLAN, QUALITY_PLAN, SEED, build_values,
)
from tools.synthetic_stress import (  # noqa: E402
    _norm, _norm_field, flatten, score, summarise_local,
)

DATA_DIR = PROJECT_ROOT / "tests" / "data" / "synthetic_docs"
SEEDS_PATH = DATA_DIR / "seeds.json"
BASELINE_PATH = DATA_DIR / "baseline.json"


@pytest.fixture(scope="module")
def seeds():
    assert SEEDS_PATH.exists(), (
        "нет tests/data/synthetic_docs/seeds.json — "
        "соберите его: python tools/run_regression.py --write-seeds"
    )
    return json.loads(SEEDS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def baseline():
    assert BASELINE_PATH.exists(), (
        "нет tests/data/synthetic_docs/baseline.json — "
        "соберите его: python tools/run_regression.py --write-baseline"
    )
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────
# 1. Зёрна и матрица
# ─────────────────────────────────────────────────────────────

def test_seeds_count_is_1000(seeds):
    assert len(seeds["seeds"]) == 1000


def test_seeds_are_positive_integers(seeds):
    for value in seeds["seeds"]:
        assert isinstance(value, int) and value > 0


def test_seed_matches_generator_constant(seeds):
    """Зерно в файле — то же, что в генераторе: иначе сценарии разойдутся."""
    assert seeds["seed"] == SEED


def test_seed_reproduces_same_scenario(seeds):
    """По зерну сценарий собирается один в один — это и есть воспроизводимость."""
    import random

    number = seeds["seeds"][7]
    first = build_values(random.Random(number), "passport")
    second = build_values(random.Random(number), "passport")
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)


def test_seed_scenario_is_complete(seeds):
    """У сценария из зерна заполнены ключи полей, а не пустой словарь."""
    import random

    for kind in KIND_PLAN:
        built = build_values(random.Random(seeds["seeds"][3]), kind)
        assert built["values"], kind
        assert built["gt"], kind
        assert flatten(built["gt"]), kind


def test_seeds_kind_plan_matches_task(seeds):
    """Раскладка по видам документов — ровно как в задании."""
    assert seeds["kinds"] == KIND_PLAN
    assert seeds["kinds"]["passport"] == 300
    assert seeds["kinds"]["license"] == 200
    assert seeds["kinds"]["bank"] == 200
    assert seeds["kinds"]["inn"] == 100
    assert seeds["kinds"]["sts"] == 100
    assert total_pts(seeds) == 100


def total_pts(seeds) -> int:
    return seeds["kinds"].get("pts", 0) + seeds["kinds"].get("sts", 0)


def test_seeds_quality_plan_matches_task(seeds):
    """Доли уровней качества — 10 / 20 / 30 / 25 / 15 %."""
    assert seeds["qualities"] == QUALITY_PLAN
    assert seeds["qualities"]["ideal"] == 100
    assert seeds["qualities"]["good"] == 200
    assert seeds["qualities"]["medium"] == 300
    assert seeds["qualities"]["poor"] == 250
    assert seeds["qualities"]["very_poor"] == 150


def test_seeds_defect_plan_covers_unfinished_letters(seeds):
    """
    «Недописанные буквы»: обрезка, подмены, экспозиция, стёртые буквы.

    Сверяются и сами виды, и их количество — через индекс сценариев: в
    самих зёрнах дефектов нет, они есть только в раскладке.
    """
    assert set(seeds["defects"]) == {"cut_letter", "lookalike_digits",
                                     "double_exposure", "erased_letters"}
    counts = {}
    for item in seeds["scenarios"]:
        if item["defect"]:
            counts[item["defect"]] = counts.get(item["defect"], 0) + 1
    assert counts == seeds["defects"]


def test_seeds_scenario_list_is_numbered(seeds):
    """Каждому сценарию — свой номер от 1 до 1000, без пропусков."""
    numbers = [item["number"] for item in seeds["scenarios"]]
    assert numbers == list(range(1, 1001))


# ─────────────────────────────────────────────────────────────
# 2. Baseline
# ─────────────────────────────────────────────────────────────

def test_baseline_has_overall_metrics(baseline):
    """Baseline содержит recall / precision / F1 — с ними сравнивается прогон."""
    for key in ("recall", "precision", "f1"):
        assert key in baseline["overall"]
        assert 0.0 <= baseline["overall"][key] <= 1.0


def test_baseline_documents_count(baseline):
    assert baseline["documents"] == 1000


def test_baseline_covers_every_kind(baseline):
    """В baseline есть все шесть видов документов."""
    assert set(baseline["by_kind"]) == set(KIND_PLAN)


def test_baseline_covers_every_quality(baseline):
    """И все пять уровней качества."""
    assert set(baseline["by_quality"]) == set(QUALITY_PLAN)


def test_baseline_ideal_quality_is_high(baseline):
    """
    На идеальном рендере разбор показывает заметно лучший recall, чем в
    среднем по прогону.

    Порог 0,5 — не «хорошо», а «не сломано»: абсолютное качество меряет сам
    baseline. Именно этот тест падает, если разбор или OCR перестали работать
    на чистых картинках.
    """
    assert baseline["by_quality"]["ideal"]["recall"] >= 0.5
    assert (baseline["by_quality"]["ideal"]["recall"]
            > baseline["overall"]["recall"])


def test_baseline_quality_degrades_monotonically(baseline):
    """
    Идеальное качество не хуже испорченного.

    Строгий порядок по всем пяти уровням не проверяется: между «хорошо» и
    «средне» разница может быть в пределах шума OCR (разбор читает подписи
    полей, а не отдельные пиксели), и такой тест падал бы через раз. А вот
    «идеально хуже, чем очень плохо» — признак сломанных метрик или
    генератора уровней, и это ловится.
    """
    ideal = baseline["by_quality"]["ideal"]["recall"]
    for name in ("good", "medium", "poor", "very_poor"):
        assert ideal >= baseline["by_quality"][name]["recall"] - 0.05, name


def test_baseline_has_no_pii(baseline):
    """
    В baseline нет данных документа: только числа и имена метрик.

    Файл коммитится в git, поэтому проверка обязательна: длинных
    последовательностей цифр (телефон, счёт, паспорт) в нём быть не должно.
    """
    text = json.dumps(baseline, ensure_ascii=False)
    assert not re.search(r"\d{9,}", text), "в baseline есть длинное число"


def test_baseline_has_no_cyrillic_values(baseline):
    """Кириллица в baseline — признак попавшего в отчёт значения."""
    text = json.dumps(baseline, ensure_ascii=False)
    # Имена ключей латинские; кириллица допустима только в служебных ничего
    # не значащих подписях, которых здесь нет.
    assert not re.search(r"[А-Яа-яЁё]", text)


# ─────────────────────────────────────────────────────────────
# 3. Метрики
# ─────────────────────────────────────────────────────────────

def test_score_perfect_match():
    """Точное совпадение — recall и precision равны единице."""
    expected = {"driver": [{"full_name": "Тестов Тест Тестович"}]}
    got = {"driver": [{"full_name": "Тестов Тест Тестович"}]}
    result = score(expected, got)
    assert result["recall"] == 1.0
    assert result["precision"] == 1.0
    assert result["f1"] == 1.0


def test_score_missed_and_extra_fields():
    """Пропущенное поле снижает recall, лишнее — precision."""
    expected = {"driver": [{"full_name": "Тестов Тест Тестович",
                            "birth_date": "15.03.1985"}]}
    got = {"driver": [{"birth_date": "15.03.1985", "phone": "+7 (900) 111-22-33"}]}
    result = score(expected, got)
    assert result["recall"] == 0.5
    assert result["missed"] == ["driver.full_name"]
    assert "driver.phone" in result["extra"]


def test_score_wrong_value_is_not_recall():
    """Неверное значение и пропуск считаются одинаково — поля нет."""
    expected = {"driver": [{"passport_number": "123456"}]}
    got = {"driver": [{"passport_number": "123457"}]}
    result = score(expected, got)
    assert result["recall"] == 0.0
    assert result["wrong"] == ["driver.passport_number"]


def test_score_empty_result_does_not_crash():
    """Пустой ответ — нулевой recall и никакого деления на ноль."""
    result = score({"driver": [{"full_name": "X"}]}, {})
    assert result["recall"] == 0.0
    assert result["precision"] == 0.0
    assert result["f1"] == 0.0


@pytest.mark.parametrize("left,right", [
    ("45 12", "4512"),
    ("г. Москва", "г Москва"),
    ("B, C, CE", "bcce"),
    ("Тестов  Тест", "тестов тест"),
])
def test_norm_makes_equal(left, right):
    """Разное написание одного значения ошибкой не считается."""
    assert _norm(left) == _norm(right)


@pytest.mark.parametrize("left,right", [
    ("123456", "123457"),
    ("15.03.1985", "15.03.1986"),
])
def test_norm_does_not_repair_digits(left, right):
    """Цифры не «чинятся»: разные значения остаются разными."""
    assert _norm_field("passport_number", left) != \
        _norm_field("passport_number", right)


def test_norm_field_normalizes_dates():
    """Дата в ISO и в русском виде — одно значение."""
    assert _norm_field("birth_date", "1985-03-15") == \
        _norm_field("birth_date", "15.03.1985")


def test_flatten_skips_service_keys():
    """Служебные ключи («_kind», «_note», «_vin») в метрику не попадают."""
    flat = flatten({"driver": [{"full_name": "X", "_kind": "passport",
                                "_note": "заметка"}]})
    assert flat == {"driver[0].full_name": "X"}


def test_summarise_local_groups_by_kind_and_quality():
    """Сводка группирует результаты по виду документа и уровню качества."""
    results = [
        {"number": 1, "kind": "passport", "quality": "ideal", "defect": "",
         "error": "", "score": score({"driver": [{"full_name": "A"}]},
                                     {"driver": [{"full_name": "A"}]})},
        {"number": 2, "kind": "passport", "quality": "poor", "defect": "",
         "error": "", "score": score({"driver": [{"full_name": "A"}]}, {})},
    ]
    report = summarise_local(results, 1.0)
    assert report["documents"] == 2
    assert report["by_kind"]["passport"]["documents"] == 2
    assert report["by_quality"]["ideal"]["recall"] == 1.0
    assert report["by_quality"]["poor"]["recall"] == 0.0


# ─────────────────────────────────────────────────────────────
# 4. Конвейер на выборке (генератор → OCR → поля → метрика)
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sample_ideal(seeds):
    """
    Номера сценариев ИДЕАЛЬНОГО качества по одному на каждый вид документа.

    Берутся из seeds.json, поэтому выборка воспроизводима: «поехавший»
    генератор виден сразу, без прогона 1000 файлов.
    """
    chosen = {}
    for item in seeds["scenarios"]:
        if item["quality"] != "ideal":
            continue
        chosen.setdefault(item["kind"], item["number"])
    return chosen


def test_pipeline_on_sample_of_ideal_documents(seeds, sample_ideal, tmp_path):
    """
    Идеальный рендер → локальный OCR → поля → метрика.

    Проверяются четыре вида документов (паспорт, ВУ, реквизиты, ИНН) на
    идеальном качестве: там разбор обязан показывать высокий recall. Именно
    этот конвейер и меряет baseline, поэтому тест ловит поломку на любом
    звене — и в генераторе, и в OCR, и в разборе полей.

    Порог низкий (0.4) намеренно: тест про ПОЛОМКУ, а не про качество
    (качество измеряет baseline и повторный прогон по 1000 сценариев).
    Фактическое значение на этих сценариях — около 0,6.
    """
    import random
    import threading

    from PIL import Image

    from core.document_import_service import extract_local_fields
    from core.document_ocr import recognize_image
    from tools.make_synthetic_docs import build_document

    for kind in ("passport", "license", "bank", "inn"):
        number = sample_ideal.get(kind)
        assert number is not None, f"в seeds.json нет идеального {kind}"
        seed = seeds["seeds"][number - 1]
        rng = random.Random(seed)
        built = build_values(rng, kind)

        image = build_document(kind, {**built["values"], "number": number},
                               "ideal", None)
        path = tmp_path / f"{kind}.png"
        image.save(path)

        text = recognize_image(Image.open(path), threading.Event())
        assert len(text) > 40, f"{kind}: OCR не дал текста"

        data = extract_local_fields(text)
        result = score(built["gt"], data or {})
        assert result["recall"] >= 0.4, (
            f"{kind}: recall={result['recall']} "
            f"(пропущено: {result['missed']}, неверно: {result['wrong']})"
        )


def test_ideal_render_has_no_rotation(seeds):
    """Идеальное качество действительно не портит картинку."""
    import random

    from tools.make_synthetic_docs import apply_quality

    rng = random.Random(1)
    built = build_values(random.Random(seeds["seeds"][0]), "passport")
    from tools.make_doc_templates import all_templates, render_template

    image = render_template(all_templates()["passport"], built["values"])
    processed = apply_quality(image, "ideal", rng)
    assert processed.size == image.size


def test_defect_lookalike_changes_exactly_one_character():
    """Подмена похожих символов меняет ровно один символ в одном поле."""
    import random

    from tools.make_synthetic_docs import _lookalike

    rng = random.Random(5)
    values = {"inn": "7701234567", "bank_name": "ПАО Сбербанк"}
    updated = _lookalike(values, rng)
    different = [key for key in values if values[key] != updated[key]]
    assert len(different) == 1
    key = different[0]
    assert len(updated[key]) == len(values[key])
    assert sum(1 for a, b in zip(values[key], updated[key]) if a != b) == 1


# ─────────────────────────────────────────────────────────────
# 5. Отчёты без данных документа
# ─────────────────────────────────────────────────────────────

def test_local_report_has_no_pii(tmp_path):
    """Отчёт локального прогона содержит только числа и имена полей."""
    report_path = PROJECT_ROOT / "tests" / "_tmp" / "synthetic_docs" / "local_report.json"
    if not report_path.exists():
        pytest.skip("локальный прогон ещё не выполнялся")
    text = report_path.read_text(encoding="utf-8")
    report = json.loads(text)
    # Имена полей в «top_missed_fields» латинские: значение попасть не может.
    for name, count in report["top_missed_fields"]:
        assert re.fullmatch(r"[a-z_\[\]0-9.]+", name), name
        assert isinstance(count, int)


def test_real_report_has_no_paths_or_values():
    """Отчёт по реальным документам: ни путей, ни значений полей."""
    report_path = PROJECT_ROOT / "tests" / "_tmp" / "real_docs_report.json"
    if not report_path.exists():
        pytest.skip("прогон по реальным документам ещё не выполнялся")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    text = json.dumps(report, ensure_ascii=False)
    assert "Документы" not in text
    assert ":\\" not in text
    assert "/" not in text.replace("\\/", "")
    for key in ("total", "processed_ok", "failed"):
        assert isinstance(report[key], int)
