#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проба Этапа 2: старый путь и новый путь дают ОДИН объект модуля.

Запускается ДВУМЯ режимами, и это важно:
  * ``old`` — первым импортируется старый путь (как в приватной части:
    ``from core.recognizer import ...``);
  * ``new`` — первым импортируется новый путь (``from services.recognition...
    import ...``) в СВЕЖЕМ процессе, где ``core`` ещё не загружен целиком.

Второй режим ловит цикл импорта: если ``services/recognition/*`` начнёт
импортировать то, что определяется в конце ``core/__init__.py``, падение
будет видно только здесь.

Запуск: python tools/check_stage2_aliases.py [old|new]
"""

import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: (старое имя модуля, новый путь, имя, которое обязано быть доступно)
MODULES = [
    ("core.document_reader", "services.recognition.reader", "read_document"),
    ("core.document_ocr", "services.recognition.ocr", "recognize_image"),
    ("core.document_fields", "services.recognition.fields", "extract_document_fields"),
    ("core.recognizer", "services.recognition.recognizer", "DataMapper"),
    ("core.document_import_service", "services.recognition.import_service",
     "DocumentImportService"),
]


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "old"
    if mode not in ("old", "new"):
        print(f"неизвестный режим: {mode}")
        return 2

    print(f"режим: {mode}")
    for old_name, new_name, attribute in MODULES:
        first, second = (old_name, new_name) if mode == "old" else (new_name, old_name)
        first_module = importlib.import_module(first)
        second_module = importlib.import_module(second)
        same = first_module is second_module
        attribute_ok = hasattr(first_module, attribute) and hasattr(second_module, attribute)
        print(
            f"  {old_name:32s} <-> {new_name:36s} "
            f"тот же модуль: {same}  {attribute}: {attribute_ok}"
        )
        if not (same and attribute_ok):
            print("  ПРОВАЛ")
            return 1

    # `core.<старое_имя>` должен читаться ещё и как атрибут пакета.
    import core

    for old_name, _new_name, _attribute in MODULES:
        short = old_name.split(".", 1)[1]
        if getattr(core, short, None) is not sys.modules[old_name]:
            print(f"  атрибут core.{short} не тот же модуль — ПРОВАЛ")
            return 1

    print("  атрибуты пакета core: ок")
    print("ИТОГ: ок")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
