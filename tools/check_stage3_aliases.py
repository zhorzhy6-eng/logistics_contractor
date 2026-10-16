#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проба Этапа 3: старый путь и новый путь дают ОДИН объект модуля.

Запускается ДВУМЯ режимами, и это важно:
  * ``old`` — первым импортируется старый путь (как в UI, агенте и приватной
    части: ``from core.gigachat_client import GigaChatClient``);
  * ``new`` — первым импортируется новый путь
    (``from infrastructure.api.gigachat import GigaChatClient``) в СВЕЖЕМ
    процессе, где ``core`` ещё не загружен целиком.

Второй режим ловит цикл импорта: ``infrastructure/*`` импортирует ``core.*``
на уровне модуля, поэтому связь двусторонняя (``core`` → ``gigachat`` →
``core``). Если её разорвать неправильно, падение видно ТОЛЬКО здесь: в общем
прогоне pytest ``core`` уже поднят ``tests/conftest.py``.

Проба проверяет ТРИ вещи, и две из них — не украшение, а пойманные дефекты
первого захода Этапа 3:

  1. старый и новый путь — один и тот же объект модуля;
  2. ВСЕ пять алиасов доехали до ``sys.modules`` (первый заход терял
     ``core.gigachat_client`` и ``core.dadata_client``, когда импорт начинался
     с ``infrastructure.secrets.store``: сторона ``store`` замыкала цикл и
     ``gigachat`` падал на частичном модуле);
  3. ``core.<старое_имя>`` читается ещё и как АТРИБУТ пакета (первый заход
     регистрировал только ``sys.modules``, и ``core.gigachat_client`` падало
     с AttributeError).

Запуск: python tools/check_stage3_aliases.py [old|new]
"""

import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: (старое имя модуля, новый путь, имя, которое обязано быть доступно)
MODULES = [
    ("core.gigachat_client", "infrastructure.api.gigachat", "GigaChatClient"),
    ("core.dadata_client", "infrastructure.api.dadata", "DadataClient"),
    ("core.secrets_store", "infrastructure.secrets.store", "get_gigachat_key"),
    ("core.tls_config", "infrastructure.api.tls", "describe_tls"),
    ("core.json_text", "core.utils.json_text", "shorten_json_text"),
]

#: Имена, которые обязаны резолвиться через `core` ленивым `__getattr__`.
REEXPORTED = [
    "GigaChatClient",
    "DadataClient",
    "MISSING_KEY_MESSAGE",
    "get_gigachat_key",
    "describe_tls",
    "resolve_ca_bundle",
    "ssl_error_hint",
    "shorten_json_text",
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
        attribute_ok = (
            hasattr(first_module, attribute) and hasattr(second_module, attribute)
        )
        print(
            f"  {old_name:26s} <-> {new_name:34s} "
            f"тот же модуль: {same}  {attribute}: {attribute_ok}"
        )
        if not (same and attribute_ok):
            print("  ПРОВАЛ")
            return 1

    # Все пять алиасов обязаны лежать в sys.modules — именно это читает
    # `from core.<старое_имя> import ...`.
    missing = [old for old, _new, _attr in MODULES if old not in sys.modules]
    if missing:
        print(f"  в sys.modules нет алиасов: {missing} — ПРОВАЛ")
        return 1
    print("  алиасы в sys.modules: все пять на месте")

    # `core.<старое_имя>` должен читаться ещё и как атрибут пакета.
    import core

    for old_name, _new_name, _attribute in MODULES:
        short = old_name.split(".", 1)[1]
        if getattr(core, short, None) is not sys.modules[old_name]:
            print(f"  атрибут core.{short} не тот же модуль — ПРОВАЛ")
            return 1

    print("  атрибуты пакета core: ок")

    # Ленивый реэкспорт имён.
    for name in REEXPORTED:
        if getattr(core, name, None) is None:
            print(f"  core.{name} не резолвится — ПРОВАЛ")
            return 1

    print("  реэкспорт имён через core: ок")
    print("ИТОГ: ок")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
