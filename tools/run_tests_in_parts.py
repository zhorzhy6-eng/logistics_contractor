# -*- coding: utf-8 -*-
"""
Полный прогон тестов ЧАСТЯМИ: гоняет pytest по 23 файла в отдельном процессе.

Нужен потому, что одним процессом полный набор роняет pytest с MemoryError
(139 файлов, PyQt5). Каждая часть получает СВОЙ --basetemp: два pytest с общим
basetemp одновременно запускать нельзя.

ВАЖНО ПРО РАЗБОР ИТОГА. Строку сводки нельзя искать свободным поиском
«N failed / N error» по всему выводу: pytest печатает ещё и блоки логов
захвата (`ERROR core.mirror:mirror.py:342 …`), и наивный разбор считает их
тестами. Сводка берётся ТОЛЬКО из итоговой строки процесса, а «N error»
учитывается лишь тогда, когда pytest пишет её как счётчик тестов
(«1 error», «2 errors»), а не как уровень лога.
"""

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS = PROJECT_ROOT / "tests"
PART_SIZE = 23

#: «3 failed», «1 error», «2 errors» — но не «ERROR core.mirror:…».
COUNTER = re.compile(r"(\d+) (passed|failed|skipped|error|errors|deselected)\b")


def summarize(line: str) -> dict:
    """Разбирает итоговую строку pytest в счётчики."""
    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0, "deselected": 0}
    for number, word in COUNTER.findall(line):
        key = "error" if word.startswith("error") else word
        counts[key] += int(number)
    return counts


def main() -> int:
    files = sorted(p.name for p in TESTS.glob("test_*.py"))
    parts = [files[i:i + PART_SIZE] for i in range(0, len(files), PART_SIZE)]
    print(f"файлов: {len(files)}, частей: {len(parts)}", flush=True)

    total = {"passed": 0, "failed": 0, "skipped": 0, "error": 0, "deselected": 0}
    failed_tests = []

    for index, part in enumerate(parts, 1):
        basetemp = TESTS / "_tmp" / f"_pt_run_p{index:02d}"
        cmd = [
            sys.executable, "-m", "pytest",
            *[f"tests/{name}" for name in part],
            "-o", "addopts=", "-q", "-p", "no:cacheprovider",
            f"--basetemp={basetemp}",
        ]
        result = subprocess.run(
            cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        summary_line = lines[-1] if lines else ""
        counts = summarize(summary_line)
        for key in total:
            total[key] += counts[key]
        print(
            f"часть {index:02d}/{len(parts)}: {summary_line}",
            flush=True,
        )

        # Строки падений pytest печатает ТОЛЬКО в блоке «short test summary
        # info» и всегда начинаются с «FAILED » / «ERROR » + путь к тесту.
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("FAILED tests/") or stripped.startswith("ERROR tests/"):
                failed_tests.append(stripped)

    print()
    print(
        "ИТОГ: {passed} passed, {skipped} skipped, {failed} failed, {error} error "
        "({deselected} deselected)".format(**total)
    )
    print(f"упавших тестов: {len(failed_tests)}")
    for line in sorted(set(failed_tests)):
        print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
