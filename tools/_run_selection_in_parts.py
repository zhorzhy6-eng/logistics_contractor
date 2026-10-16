# -*- coding: utf-8 -*-
"""
Прогон ЗАДАННОГО СПИСКА тестов частями, по N штук в отдельном процессе.

Зачем: `tools/run_tests_in_parts.py` делит набор по ФАЙЛАМ (23 файла на
процесс) и потому не отвечает на вопрос «какой именно тест уронил процесс».
Этот скрипт принимает список node id (по одному в строке) и делит его по
ШТУКАМ — так видно, на какой части процесс упал и какие тесты в неё вошли.

Нужен потому, что PyQt5 на Python 3.14 роняет pytest при большом числе
UI-тестов в ОДНОМ процессе (в `run_tests_in_parts.py` про то же: полный
набор одним процессом даёт MemoryError). Прогон частями — штатный способ
прогнать много UI-тестов и получить честную сводку.

Запуск:
    python -m pytest tests/ -o addopts="" -q --basetemp=tests/_tmp/_pt_collect \\
        --collect-only -k "<фильтр>" > tests/_tmp/_collect.txt
    # оставить только строки node id, затем:
    python tools/_run_selection_in_parts.py tests/_tmp/_selection.txt 60

Подчёркивание в имени — скрипт вспомогательный, не часть конвейера тестов.
"""

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COUNTER = re.compile(r"(\d+) (passed|failed|skipped|error|errors|deselected)\b")


def summarize(line: str) -> dict:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0, "deselected": 0}
    for number, word in COUNTER.findall(line):
        key = "error" if word.startswith("error") else word
        counts[key] += int(number)
    return counts


def main() -> int:
    selection = Path(sys.argv[1])
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    ids = [line.strip() for line in selection.read_text(encoding="utf-8").splitlines()
           if line.strip()]
    parts = [ids[i:i + size] for i in range(0, len(ids), size)]
    print(f"тестов: {len(ids)}, частей: {len(parts)} по {size}", flush=True)

    total = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    failed_tests = []
    for index, part in enumerate(parts, 1):
        basetemp = PROJECT_ROOT / "tests" / "_tmp" / f"_pt_sel_{index:03d}"
        cmd = [sys.executable, "-m", "pytest", *part, "-o", "addopts=", "-q",
               "-p", "no:cacheprovider", f"--basetemp={basetemp}"]
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        summary = lines[-1] if lines else ""
        counts = summarize(summary)
        for key in total:
            total[key] += counts[key]
        failed_tests.extend(
            line.split(" ")[1] for line in lines if line.startswith("FAILED ")
        )
        marker = "OK" if result.returncode == 0 else f"КОД {result.returncode}"
        print(f"  часть {index:03d}: {marker} | {summary}", flush=True)
        if result.returncode != 0 and counts["passed"] == 0 and counts["failed"] == 0:
            print("  ПРОЦЕСС УПАЛ, первых файлов части: "
                  + ", ".join(x.split("::")[0] for x in part[:3]), flush=True)
            break

    print(f"ИТОГО: {total}", flush=True)
    if failed_tests:
        print("УПАВШИЕ ТЕСТЫ:", flush=True)
        for name in failed_tests:
            print("   ", name, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
