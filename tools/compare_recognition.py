#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сравнение «локально против GigaChat» на ОДНИХ И ТЕХ ЖЕ сценариях.

Берутся номера из отчёта GigaChat (`gigachat_report.json`) и по ним заново
считается локальный результат: картинка → Tesseract → разбор полей → метрика.
Так сравнение честное: одни документы, один ground truth, одна формула метрики.

Данные синтетические.
"""

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"E:\Programmy\logistics_contractor")
sys.path.insert(0, str(ROOT))

from tools.synthetic_stress import _recognize_path, score  # noqa: E402

SYNTH = ROOT / "tests" / "_tmp" / "synthetic_docs"
report = json.loads((SYNTH / "gigachat_report.json").read_text(encoding="utf-8"))

from core.document_import_service import extract_local_fields  # noqa: E402

rows = []
local_expected = local_tp = local_found = 0
for entry in report["results"]:
    number = entry["number"]
    gt = json.loads((SYNTH / "gt" / f"{number}.json").read_text(encoding="utf-8"))
    text, source = _recognize_path(SYNTH / gt["image"])
    local = score(gt["gt"], extract_local_fields(text) if text else {})
    local_expected += local["expected"]
    local_tp += local["true_positive"]
    local_found += local["found"]
    rows.append({
        "number": number, "kind": entry["kind"], "quality": entry["quality"],
        "defect": entry["defect"], "text_source": source,
        "local_recall": local["recall"], "local_precision": local["precision"],
        "giga_recall": entry["score"]["recall"],
        "giga_precision": entry["score"]["precision"],
    })

local_recall = round(local_tp / local_expected, 4) if local_expected else 0.0
local_precision = round(local_tp / local_found, 4) if local_found else 0.0

print(f"Сценариев: {len(rows)} (одни и те же для обоих)")
print(f"ЛОКАЛЬНО:  recall={local_recall:.3f} precision={local_precision:.3f}")
print(f"GIGACHAT:  recall={report['metrics']['overall']['recall']:.3f} "
      f"precision={report['metrics']['overall']['precision']:.3f}")
print()
print("Где локальный разбор провалился, а модель справилась:")
wins = [r for r in rows if r["giga_recall"] > r["local_recall"]]
print(f"  таких сценариев: {len(wins)} из {len(rows)}")
for row in wins[:25]:
    print(f"  №{row['number']:4d} {row['kind']:9s} {row['quality']:9s} "
          f"{row['defect'] or '-':16s} локально {row['local_recall']:.2f} "
          f"→ модель {row['giga_recall']:.2f}")
print()
print("Где локальный разбор был ЛУЧШЕ:")
losses = [r for r in rows if r["giga_recall"] < r["local_recall"]]
print(f"  таких сценариев: {len(losses)}")
for row in losses[:10]:
    print(f"  №{row['number']:4d} {row['kind']:9s} {row['quality']:9s} "
          f"локально {row['local_recall']:.2f} → модель {row['giga_recall']:.2f}")

out = {
    "scenarios": len(rows),
    "local": {"recall": local_recall, "precision": local_precision},
    "gigachat": {"recall": report["metrics"]["overall"]["recall"],
                 "precision": report["metrics"]["overall"]["precision"]},
    "gigachat_better": len(wins),
    "local_better": len(losses),
    "rows": rows,
}
(SYNTH / "comparison.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\nОтчёт: {SYNTH / 'comparison.json'}")
