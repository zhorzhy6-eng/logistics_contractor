#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проба Этапа 4: старый путь `core.contracts.base_generator` и новый
`core.contracts.base` дают ОДИН объект модуля.

Запускается ДВУМЯ режимами, и это не украшение:

  * ``old`` — первым импортируется СТАРЫЙ путь (как в четырёх клиентских
    генераторах приватной части и в `perevozka/postprocess.py`);
  * ``new`` — первым импортируется НОВЫЙ путь
    (``from core.contracts.base import BaseContractGenerator``) в СВЕЖЕМ
    процессе, где ``core`` ещё не загружен целиком.

Второй режим и есть причина существования пробы: в общем прогоне pytest
``core`` уже поднят ``tests/conftest.py``, поэтому «новый путь первым»
там не проверяется НИКОГДА. На этом месте спотыкались первые заходы
Этапов 2 и 3.

Проба проверяет ТРИ вещи, и две из них — пойманные дефекты прошлых этапов:

  1. старый и новый путь — один и тот же объект модуля;
  2. алиас доехал до ``sys.modules`` (это читает
     ``from core.contracts.base_generator import ...``);
  3. ``core.contracts.base_generator`` читается ещё и как АТРИБУТ пакета
     (``import core.contracts.base_generator`` после загрузки берёт последнее
     имя как атрибут родителя; одного ``sys.modules`` МАЛО — ровно на этом
     упал Этап 3).

Плюс две проверки, специфичные для Этапа 4:

  4. ``core.contracts`` остался пакетом-ПРОСТРАНСТВОМ ИМЁН (``__file__``
     равен None). Появление ``core/contracts/__init__.py`` сломало бы
     импорт клиентских типов из приватной части;
  5. публичный API (четыре имени) виден из ОБОИХ путей.

Запуск: python tools/check_stage4_aliases.py [old|new]
"""

import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: Старое и новое имена модуля.
OLD_NAME = "core.contracts.base_generator"
NEW_NAME = "core.contracts.base"

#: Имена, которые обязаны быть доступны из обоих путей.
PUBLIC_API = (
    "BaseContractGenerator",
    "PostprocessStep",
    "ConvertNewlinesStep",
    "NormalizeSpacesStep",
)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "old"
    if mode not in ("old", "new"):
        print(f"неизвестный режим: {mode}")
        return 2

    print(f"режим: {mode}")

    first, second = (OLD_NAME, NEW_NAME) if mode == "old" else (NEW_NAME, OLD_NAME)
    first_module = importlib.import_module(first)
    second_module = importlib.import_module(second)

    same = first_module is second_module
    print(f"  {first:32s} <-> {second:22s} тот же модуль: {same}")
    if not same:
        print("  ПРОВАЛ: пути разъехались на два модуля")
        return 1

    for name in PUBLIC_API:
        if not (hasattr(first_module, name) and hasattr(second_module, name)):
            print(f"  ПРОВАЛ: {name} недоступно из одного из путей")
            return 1
    print(f"  публичный API из обоих путей: {len(PUBLIC_API)} имени на месте")

    # Алиас обязан лежать в sys.modules — именно это читает
    # `from core.contracts.base_generator import ...`.
    if OLD_NAME not in sys.modules:
        print(f"  ПРОВАЛ: {OLD_NAME} нет в sys.modules")
        return 1
    print("  алиас в sys.modules: ок")

    # `core.contracts.base_generator` должен читаться и как АТРИБУТ пакета.
    import core.contracts as contracts

    if getattr(contracts, "base_generator", None) is not sys.modules[OLD_NAME]:
        print("  ПРОВАЛ: атрибут core.contracts.base_generator — не тот модуль")
        return 1
    print("  атрибут пакета core.contracts: ок")

    # `core.contracts` обязан остаться namespace-пакетом: с `__init__.py`
    # приватные типы договоров перестают находиться.
    if contracts.__file__ is not None:
        print(
            "  ПРОВАЛ: core.contracts стал обычным пакетом "
            f"({contracts.__file__}) — приватные типы не найдутся"
        )
        return 1
    print("  core.contracts — пакет-пространство имён: ок")

    # Наследование подклассов живое.
    from core.contracts.perevozka.generator import PerevozkaGenerator

    base_class = first_module.BaseContractGenerator
    if not issubclass(PerevozkaGenerator, base_class):
        print("  ПРОВАЛ: PerevozkaGenerator больше не наследуется от базы")
        return 1
    print("  PerevozkaGenerator наследуется от базы: ок")

    print("ИТОГ: ок")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
