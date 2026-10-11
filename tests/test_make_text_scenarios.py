#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты генератора 1000 синтетических текстов (`tools/make_text_scenarios.py`).

Проверяется то, на чём держится стресс-тест:

  1. **Раскладка** — 1000 сценариев, виды и уровни ровно по заданию.
  2. **Детерминизм** — одно зерно даёт один и тот же текст побайтово;
     без этого регрессия по метрикам «поехала» бы от прогона к прогону.
  3. **Вымышленность и валидность данных** — контрольные суммы ИНН и ОГРН
     сходятся (иначе `core.pseudonymizer` не обезличит номер и он уйдёт
     в облако открытым), телефоны только 900–999, VIN из 17 символов.
  4. **Безопасность** — ни одно значение сценария не встречается в рабочей
     `contracts.db`, персональные значения закрываются плейсхолдерами.
  5. **Форма эталона** — ground truth говорит на языке `SCHEMA`, иначе
     метрику не свести одной формулой.

Здесь НЕТ ни одного реального значения: все данные берутся из словарей
генератора. Тесты с рабочей базой пропускаются, если её нет на месте.
"""

import json
import re
from pathlib import Path

import pytest

from core.document_import_service import SCHEMA
from core.pseudonymizer import Pseudonymizer
from tools.make_text_scenarios import (
    BASE_SEED, KIND_PLAN, KINDS, LEVEL_PLAN, LEVELS, SEEDS_PATH,
    _pii_values, build_plan, build_scenario, build_seeds, db_haystack,
    find_db_collisions, inn_checksum_ok, load_seeds, ogrn_checksum_ok,
    scenario_from_seed,
)
from tools.synthetic_stress import score

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Сколько сценариев брать в проверки с обезличиванием: один вызов
#: Pseudonymizer — около 90 мс, тысяча заняла бы полторы минуты.
PSEUDONYMIZER_SAMPLE = 40

#: Признак пути или имени файла оператора — в текстах его быть не должно.
OPERATOR_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|\\\\[A-Za-z]|/Users/|/home/|\.docx\b)")


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def seeds():
    if not SEEDS_PATH.exists():
        pytest.skip(f"нет {SEEDS_PATH} — соберите: "
                    f"python tools/make_text_scenarios.py --write-seeds")
    return load_seeds(SEEDS_PATH)


@pytest.fixture(scope="module")
def scenarios(seeds):
    """Все 1000 сценариев: генерация занимает сотые доли секунды."""
    return [scenario_from_seed(seed, meta["kind"], meta["level"])
            for seed, meta in zip(seeds["seeds"], seeds["scenarios"])]


@pytest.fixture(scope="module")
def sample(seeds):
    """Стратифицированная выборка: по пять сценариев каждого вида."""
    picked, per_kind = [], {}
    for index, meta in enumerate(seeds["scenarios"]):
        if per_kind.get(meta["kind"], 0) >= 5:
            continue
        per_kind[meta["kind"]] = per_kind.get(meta["kind"], 0) + 1
        picked.append((meta, scenario_from_seed(seeds["seeds"][index],
                                                meta["kind"], meta["level"])))
    return picked


def flat_fields(scenario):
    """Поля эталона списком «раздел.поле» и их значения."""
    for section, payload in scenario["ground_truth"].items():
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict):
                for key, value in item.items():
                    yield f"{section}.{key}", value


# ─────────────────────────────────────────────────────────────
# 1. Раскладка
# ─────────────────────────────────────────────────────────────

def test_seeds_file_has_thousand_integers(seeds):
    assert seeds["count"] == 1000
    assert len(seeds["seeds"]) == 1000
    assert all(isinstance(value, int) and value > 0 for value in seeds["seeds"])
    assert len(set(seeds["seeds"])) == 1000, "зёрна должны быть уникальны"


def test_seeds_file_keeps_only_numbers(seeds):
    """Файл зёрен идёт в git: значений полей в нём быть не должно."""
    text = SEEDS_PATH.read_text(encoding="utf-8")
    assert not re.search(r"[А-Яа-яЁё]", text), "в text_seeds.json попал текст"


def test_plan_matches_task_kinds(seeds):
    counts = {}
    for meta in seeds["scenarios"]:
        counts[meta["kind"]] = counts.get(meta["kind"], 0) + 1
    assert counts == KIND_PLAN
    assert sum(counts.values()) == 1000


@pytest.mark.parametrize("kind", KINDS)
def test_plan_matches_task_levels(seeds, kind):
    counts = {}
    for meta in seeds["scenarios"]:
        if meta["kind"] == kind:
            counts[meta["level"]] = counts.get(meta["level"], 0) + 1
    assert counts == LEVEL_PLAN[kind]


def test_scenarios_are_numbered_and_ordered(seeds):
    numbers = [meta["number"] for meta in seeds["scenarios"]]
    assert numbers == list(range(1, 1001))


def test_plan_is_shuffled(seeds):
    """Первые 250 сценариев — не один вид: частичный прогон представителен."""
    first_kinds = {meta["kind"] for meta in seeds["scenarios"][:50]}
    assert len(first_kinds) >= 4


def test_build_plan_is_deterministic():
    assert build_plan() == build_plan()
    assert len(build_plan()) == 1000


# ─────────────────────────────────────────────────────────────
# 2. Детерминизм
# ─────────────────────────────────────────────────────────────

def test_same_seed_gives_same_scenario():
    first = scenario_from_seed(123456789, "drivers", "hard")
    second = scenario_from_seed(123456789, "drivers", "hard")
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)


def test_different_seeds_give_different_texts():
    first = scenario_from_seed(11111, "drivers", "simple")["text"]
    second = scenario_from_seed(22222, "drivers", "simple")["text"]
    assert first != second


def test_all_seed_texts_are_unique(scenarios):
    texts = [scenario["text"] for scenario in scenarios]
    assert len(set(texts)) == len(texts)


def test_build_seeds_reproduces_written_file(seeds):
    """
    Та же сборка (с той же сверкой по базе) даёт те же зёрна.

    Сверка с рабочей базой тратит попытки генератора, поэтому зёрна
    воспроизводятся только вместе с ней. Файл зёрен — источник истины:
    если база оператора изменится и какое-то зерно начнёт совпадать,
    прогонщик остановится на проверке безопасности, а не «поедет» молча.
    """
    rows = db_haystack()
    if not rows:
        pytest.skip("рабочая contracts.db не найдена — сверка не делается")
    rebuilt_seeds, rebuilt_meta = build_seeds(
        seed=seeds["seed"],
        collision_check=lambda scenario: find_db_collisions(scenario, rows))
    assert rebuilt_seeds == seeds["seeds"]
    assert rebuilt_meta == seeds["scenarios"]


def test_base_seed_is_stable():
    assert BASE_SEED == 20261011


# ─────────────────────────────────────────────────────────────
# 3. Вымышленные и валидные данные
# ─────────────────────────────────────────────────────────────

def test_inn_checksum_helper_accepts_valid_and_rejects_broken():
    assert inn_checksum_ok("7712345671") is True          # сумма сходится
    assert inn_checksum_ok("7712345678") is False         # последняя цифра «8»
    assert inn_checksum_ok("500100732259") is True        # 12 цифр, ИП
    assert inn_checksum_ok("500100732258") is False
    assert inn_checksum_ok("123") is False


def test_ogrn_checksum_helper_accepts_valid_and_rejects_broken():
    assert ogrn_checksum_ok("1027700132195") is True
    assert ogrn_checksum_ok("1027700132196") is False
    assert ogrn_checksum_ok("304500000000001") in (True, False)  # форма верна
    assert ogrn_checksum_ok("123") is False


def test_all_inn_in_scenarios_are_valid(scenarios):
    checked = 0
    for scenario in scenarios:
        for key, value in flat_fields(scenario):
            if key.endswith(".inn"):
                digits = "".join(ch for ch in str(value) if ch.isdigit())
                assert inn_checksum_ok(digits), f"{key}: контрольная сумма не сходится"
                checked += 1
    assert checked >= 250, "ИНН должны быть в большинстве сценариев организаций"


def test_all_ogrn_in_scenarios_are_valid(scenarios):
    checked = 0
    for scenario in scenarios:
        for key, value in flat_fields(scenario):
            if key.endswith(".ogrn"):
                digits = "".join(ch for ch in str(value) if ch.isdigit())
                assert ogrn_checksum_ok(digits), f"{key}: контрольная сумма не сходится"
                checked += 1
    assert checked >= 250


def test_phones_are_mobile_range_900_999(scenarios):
    checked = 0
    for scenario in scenarios:
        for key, value in flat_fields(scenario):
            if not key.endswith(".phone"):
                continue
            digits = "".join(ch for ch in str(value) if ch.isdigit())
            assert digits.startswith("79"), f"{key}: телефон не из диапазона 900–999"
            checked += 1
    assert checked >= 200


def test_vin_has_seventeen_characters(scenarios):
    checked = 0
    for scenario in scenarios:
        for key, value in flat_fields(scenario):
            if not key.endswith(".vin"):
                continue
            assert len(str(value)) == 17, f"{key}: длина VIN не 17"
            checked += 1
    assert checked >= 250


def test_kpp_length_is_nine(scenarios):
    for scenario in scenarios:
        for key, value in flat_fields(scenario):
            if key.endswith(".kpp"):
                digits = "".join(ch for ch in str(value) if ch.isdigit())
                assert len(digits) == 9


def test_addresses_use_dictionary_streets(scenarios):
    """Улицы — из словаря генератора, дома вымышленные."""
    from tools.make_synthetic_docs import STREETS

    known = {street.replace("ул. ", "").casefold() for street in STREETS}
    found = 0
    for scenario in scenarios:
        for key, value in flat_fields(scenario):
            if "address" not in key:
                continue
            text = str(value).casefold()
            if any(street in text for street in known):
                found += 1
    assert found >= 100, "адреса должны собираться из словаря улиц"


# ─────────────────────────────────────────────────────────────
# 4. Безопасность
# ─────────────────────────────────────────────────────────────

def test_texts_have_no_operator_paths(scenarios):
    for scenario in scenarios:
        assert not OPERATOR_PATH_RE.search(scenario["text"]), \
            "в синтетический текст попал путь оператора"


def test_texts_have_no_placeholders(scenarios):
    """Плейсхолдеры ставит обезличиватель на лету, в файле их быть не может."""
    for scenario in scenarios:
        assert "<<" not in scenario["text"]


def test_anonymizer_masks_person_names(sample):
    pseudo = Pseudonymizer()
    for meta, scenario in sample:
        masked, mapping = pseudo.anonymize(scenario["text"])
        assert mapping, f"#{meta['number']}: обезличивание ничего не нашло"
        for key, value in flat_fields(scenario):
            # Название организации — не персональные данные: псевдонимизатор
            # их и не скрывает (см. PERSONAL_KEYS_BY_SECTION в прогонщике).
            if key in ("driver.full_name", "carrier.director_name",
                       "customer.director_name"):
                assert str(value).casefold() not in masked.casefold(), \
                    f"#{meta['number']}: {key} ушло бы в облако открытым"


def test_anonymizer_masks_documents_and_phones(sample):
    pseudo = Pseudonymizer()
    for meta, scenario in sample:
        masked, _mapping = pseudo.anonymize(scenario["text"])
        for key, value in flat_fields(scenario):
            if key.endswith((".passport_series", ".passport_number",
                             ".license_series", ".license_number", ".phone")):
                digits = "".join(ch for ch in str(value) if ch.isdigit())
                assert digits not in masked, \
                    f"#{meta['number']}: {key} ушло бы в облако открытым"


def test_no_scenario_collides_with_working_db(seeds):
    rows = db_haystack()
    if not rows:
        pytest.skip("рабочая contracts.db не найдена — сверка не делается")
    collisions = []
    for seed, meta in zip(seeds["seeds"], seeds["scenarios"]):
        scenario = scenario_from_seed(seed, meta["kind"], meta["level"])
        if find_db_collisions(scenario, rows):
            collisions.append(meta["number"])
    assert not collisions, f"совпадения с рабочей базой: {collisions[:10]}"


def test_pii_values_are_collected_from_ground_truth(scenarios):
    """В сценариях водителя проверка безопасности получает непустой список."""
    with_values = sum(1 for scenario in scenarios if _pii_values(scenario))
    assert with_values == len(scenarios)


# ─────────────────────────────────────────────────────────────
# 5. Форма эталона и уровни
# ─────────────────────────────────────────────────────────────

#: Поля эталона, которых нет в `SCHEMA`, но которые есть в тексте и которые
#: модель обязана вернуть по своему промпту:
#:   * `entity_type` — форма стороны («ООО» / «ИП»). Приложение её ВЫЧИСЛЯЕТ
#:     из ставки НДС (`core/vat.py::compute_carrier_type`), а не извлекает,
#:     поэтому в SCHEMA её нет; модель же отвечает формой прямо.
#:   * `loading_address` / `unloading_address` — адреса точек маршрута:
#:     в схеме приложения их нет, у модели есть, локальные правила меток
#:     этих полей не знают вовсе.
#: Это ровно те поля, по которым сравнение «облако против правил» и нужно.
GT_KEYS_OUTSIDE_SCHEMA = {
    "carrier.entity_type", "customer.entity_type",
    "contract.loading_address", "contract.unloading_address",
}


def test_ground_truth_uses_known_sections(scenarios):
    for scenario in scenarios:
        assert scenario["ground_truth"], "эталон не может быть пустым"
        for section in scenario["ground_truth"]:
            assert section in SCHEMA, f"неизвестный раздел эталона: {section}"


def test_ground_truth_fields_are_in_schema(scenarios):
    for scenario in scenarios:
        for section, payload in scenario["ground_truth"].items():
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                for key in item:
                    if f"{section}.{key}" in GT_KEYS_OUTSIDE_SCHEMA:
                        continue
                    assert key in SCHEMA[section], f"{section}.{key} нет в SCHEMA"


def test_ground_truth_has_no_empty_values(scenarios):
    for scenario in scenarios:
        for key, value in flat_fields(scenario):
            assert str(value).strip(), f"{key}: пустое значение в эталоне"


def test_score_on_own_ground_truth_is_perfect(scenarios):
    """Формула метрики обязана давать 1.0, если ответ совпал с эталоном."""
    for scenario in scenarios[:50]:
        result = score(scenario["ground_truth"], scenario["ground_truth"])
        assert result["recall"] == 1.0
        assert result["precision"] == 1.0


def test_simple_texts_use_colon_labels(seeds, scenarios):
    for meta, scenario in zip(seeds["scenarios"], scenarios):
        if meta["level"] != "simple" or meta["kind"] == "organizations":
            continue
        assert re.search(r":\s", scenario["text"]), \
            f"#{meta['number']}: в simple-тексте нет метки с двоеточием"


def test_extreme_texts_are_harder_than_simple(seeds, scenarios):
    """У extreme встречаются опечатки меток, апострофы или кириллица в VIN."""
    markers = ("'", "ВИН", "Г/н", "Тел.", "Госномер", "VIN-код")
    extreme = [scenario["text"] for meta, scenario in zip(seeds["scenarios"], scenarios)
               if meta["level"] == "extreme"]
    assert extreme
    assert any(any(marker in text for marker in markers) for text in extreme)


def test_documents_kind_has_several_blocks(seeds, scenarios):
    counts = []
    for meta, scenario in zip(seeds["scenarios"], scenarios):
        if meta["kind"] == "documents" and meta["level"] in ("hard", "extreme"):
            counts.append(len(scenario["ground_truth"]))
    assert counts and min(counts) >= 3, "смешанный документ — это несколько блоков"


def test_local_parser_finds_something_in_simple_drivers(seeds, scenarios):
    """Проверка «правила вообще работают»: на simple-водителях recall высокий."""
    from core.document_import_service import extract_local_fields

    recalls = []
    for meta, scenario in zip(seeds["scenarios"], scenarios):
        if meta["kind"] == "drivers" and meta["level"] == "simple":
            result = score(scenario["ground_truth"],
                           extract_local_fields(scenario["text"]) or {})
            recalls.append(result["recall"])
    assert recalls
    assert sum(recalls) / len(recalls) > 0.6
