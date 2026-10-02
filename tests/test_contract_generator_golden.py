#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Золотые тесты договоров (Шаг 6 рефакторинга архитектуры контрактов).

Вывод ContractGenerator после рефакторинга должен совпадать с эталонами,
снятыми ДО него (tests/data/golden/*, см. tools/make_golden.py).

Сравнение — по структурному отпечатку: абзацы, таблицы, порядок элементов
тела, оставшиеся плейсхолдеры, хеш текста. Байты docx-контейнера
(sha256_docx) не сравниваются: они недетерминированы между прогонами
даже у исходного кода (проверено эмпирически на шаге 3).

Все данные синтетические, реальных ПДн нет.
"""

import json
import shutil
from pathlib import Path

import pytest

from core.contract_generator import ContractGenerator
from tools.make_golden import GOLDEN_DIR, SCENARIOS, fingerprint, strip_embedded_fonts

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_golden_document_matches_baseline(scenario, work_dir):
    """
    Сценарий (ООО / ИП с НДС / ИП без НДС / маршрут с «дыркой») даёт
    документ, идентичный эталону шага 0 по структуре и тексту.
    """
    template_name, payload = SCENARIOS[scenario]

    expected = json.loads(
        (GOLDEN_DIR / f"{scenario}.json").read_text(encoding="utf-8")
    )

    templates_dir = work_dir / f"golden_tpl_{scenario}"
    shutil.rmtree(templates_dir, ignore_errors=True)
    templates_dir.mkdir(parents=True, exist_ok=True)
    strip_embedded_fonts(
        PROJECT_ROOT / "templates" / template_name,
        templates_dir / template_name,
    )

    output = work_dir / f"golden_out_{scenario}.docx"
    try:
        generator = ContractGenerator(templates_dir=str(templates_dir))
        generator.generate_docx(payload, str(output))

        actual = fingerprint(output)

        assert actual["paragraphs"] == expected["paragraphs"]
        assert actual["tables"] == expected["tables"]
        assert actual["body"] == expected["body"]
        assert actual["placeholders_left"] == expected["placeholders_left"]
        assert actual["sha256_text"] == expected["sha256_text"]
    finally:
        shutil.rmtree(templates_dir, ignore_errors=True)
        output.unlink(missing_ok=True)


def test_golden_dir_has_all_scenarios():
    """Каждому сценарию соответствует пара <имя>.docx + <имя>.json."""
    for scenario in SCENARIOS:
        assert (GOLDEN_DIR / f"{scenario}.docx").exists(), scenario
        assert (GOLDEN_DIR / f"{scenario}.json").exists(), scenario
