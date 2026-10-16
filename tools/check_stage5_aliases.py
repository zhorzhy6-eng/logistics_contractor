#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проба Этапа 5: старые пути `core.agent.*` и новые `services.agent.*` дают
ОДИН объект модуля.

Запускается ДВУМЯ режимами, и это не украшение:

  * ``old`` — первым импортируется СТАРЫЙ путь (так работает вся публичная
    часть — 53 файла — и вся приватная часть: `tests/test_agent_all_windows.py`,
    `test_agent_tutorial.py`, `test_diagnostics.py`,
    `tools/migration/restore_public_edits.py`);
  * ``new`` — первым импортируется НОВЫЙ путь
    (``import services.agent``) в СВЕЖЕМ процессе, где ``core`` ещё не
    загружен целиком.

Второй режим и есть причина существования пробы: в общем прогоне pytest
``core`` уже поднят ``tests/conftest.py``, поэтому «новый путь первым» там
не проверяется НИКОГДА. На этом месте спотыкались первые заходы
Этапов 2, 3 и 4.

Проба проверяет ЧЕТЫРЕ вещи, и три из них — пойманные дефекты:

  1. старый и новый путь — один и тот же объект модуля (КЛЮЧЕВОЕ: первый
     заход Этапа 5 использовал перехватчик импорта и породил ДВА объекта —
     `monkeypatch` по старому пути переставал быть виден коду, который
     работает по новому);
  2. алиас доехал до ``sys.modules`` (это читает
     ``from core.agent.llm import SafeChat``);
  3. алиас лежит и АТРИБУТОМ пакета (``import core.agent.llm`` читает
     последнее имя как атрибут родителя; одного ``sys.modules`` МАЛО —
     ровно на этом упал Этап 3);
  4. публичный API виден из ОБОИХ путей, и приватный сосед
     ``core/agent/labels.py`` находится, если приватная часть подключена
     (иначе клиентские типы молча выпадают из подсказок агента).

Запуск: python tools/check_stage5_aliases.py [old|new]
"""

import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: «Старое имя модуля → новое» — снимок карты `core._AGENT_ALIASES`
#: на момент написания пробы. Проверяются ВСЕ имена: одно забытое —
#: и старый путь падает с ModuleNotFoundError.
ALIASES = {
    "core.agent.agent": "services.agent.core.agent",
    "core.agent.llm": "services.agent.core.llm",
    "core.agent.memory": "services.agent.core.memory",
    "core.agent.long_memory": "services.agent.core.long_memory",
    "core.agent.prompt": "services.agent.core.prompt",
    "core.agent.tools": "services.agent.tools.registry",
    "core.agent.analytics_tools": "services.agent.tools.analytics",
    "core.agent.dadata_tools": "services.agent.tools.dadata",
    "core.agent.legal_tools": "services.agent.tools.legal",
    "core.agent.rag": "services.agent.tools.rag",
    "core.agent.context_tools": "services.agent.tools.context",
    "core.agent.proactivity": "services.agent.tools.proactivity",
    "core.agent.proactivity_tools": "services.agent.tools.proactivity_tools",
    "core.agent.fill_parser": "services.agent.tools.fill_parser",
    "core.agent.tutorial": "services.agent.tools.tutorial",
    "core.agent.tutorial_hints": "services.agent.tools.tutorial_hints",
    "core.agent.personas": "services.agent.persona.personas",
    "core.agent.emotional": "services.agent.persona.emotional",
    "core.agent.diagnostics": "core.diagnostics",
}

#: Имена модулей, которые живут в `core/agent/` и алиасов не имеют.
STAYED = ("core.agent.words",)

#: Публичный API: обязан быть виден из обоих путей.
PUBLIC_API = (
    "Agent",
    "AgentContext",
    "AgentMemory",
    "AgentReply",
    "DEFAULT_PERSONA",
    "Message",
    "PERSONAS",
    "Persona",
    "Tool",
    "ToolCall",
    "ToolRegistry",
    "build_registry",
    "build_system_prompt",
    "persona",
)


def _fail(message: str) -> int:
    print(f"  ПРОВАЛ: {message}")
    return 1


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "old"
    if mode not in ("old", "new"):
        print(f"неизвестный режим: {mode}")
        return 2

    print(f"режим: {mode}")

    # Приватная часть подключается ТАК ЖЕ, как в приложении: `main.py` зовёт
    # `attach_private_root()` до первого импорта пакетов типов. Проба делает
    # то же самое — иначе она проверяла бы не тот сценарий, в котором
    # программа работает, и не увидела бы потерю приватного соседа
    # `core/agent/labels.py`.
    from core.private_path import attach_private_root, is_private_available

    attach_private_root()
    print(f"  приватная часть: {is_private_available()}")

    # Порядок импорта — суть пробы.
    if mode == "new":
        importlib.import_module("services.agent")
        importlib.import_module("core.agent")
    else:
        importlib.import_module("core.agent")
        importlib.import_module("services.agent")

    import core  # noqa: F401 — поднимает блок алиасов

    # 1. Каждый алиас — ОДИН объект модуля.
    different = []
    for old_name, new_name in ALIASES.items():
        old = importlib.import_module(old_name)
        new = importlib.import_module(new_name)
        if old is not new:
            different.append((old_name, new_name, id(old), id(new)))
    if different:
        for old_name, new_name, old_id, new_id in different:
            print(f"    {old_name} (id={old_id}) != {new_name} (id={new_id})")
        return _fail("старый и новый путь разъехались на два модуля")
    print(f"  один объект модуля: {len(ALIASES)} алиасов на месте")

    # Пакет читается ПОСЛЕ шага 1: в режиме `new` подмодули старого пути
    # загружаются именно там, и атрибуты появляются вместе с ними.
    import core.agent as agent_package

    # 2. Алиасы лежат в sys.modules (это читает `from ... import ...`).
    missing = [name for name in ALIASES if name not in sys.modules]
    if missing:
        return _fail(f"нет в sys.modules: {missing}")
    print("  алиасы в sys.modules: ок")

    # 3. Алиасы лежат атрибутами пакета `core.agent`
    #    (`import core.agent.llm` читает последнее имя как атрибут).
    absent = []
    for old_name, _ in ALIASES.items():
        short = old_name.split(".")[-1]
        if short not in vars(agent_package):
            absent.append(short)
        elif vars(agent_package)[short] is not sys.modules[old_name]:
            absent.append(short + " (чужой модуль)")
    if absent:
        return _fail(f"нет атрибутов пакета core.agent: {absent}")
    print(f"  атрибуты пакета core.agent: ок ({len(ALIASES)})")

    # 4. Модули, которые НЕ переезжали, остались на месте.
    for name in STAYED:
        if importlib.import_module(name) is None:
            return _fail(f"{name} не импортируется")
    print(f"  остались на месте: {', '.join(STAYED)}")

    # 5. Публичный API из обоих путей.
    old_api = importlib.import_module("core.agent")
    new_api = importlib.import_module("services.agent")
    for name in PUBLIC_API:
        if not hasattr(old_api, name) or not hasattr(new_api, name):
            return _fail(f"{name} недоступно из одного из путей")
        if getattr(old_api, name) is not getattr(new_api, name):
            return _fail(f"{name} — разные объекты в старом и новом пути")
    print(f"  публичный API из обоих путей: {len(PUBLIC_API)} имени на месте")

    # 6. Приватный сосед: названия клиентских типов и обязательные поля.
    private_note = "приватная часть не подключена — режим ядра"
    try:
        from services.agent.tools.context import TYPE_TITLES
        from services.agent.tools.proactivity import REQUIRED

        if len(TYPE_TITLES) > 1 and len(REQUIRED) > 1:
            private_note = (
                f"приватный сосед найден: типов {len(TYPE_TITLES)}, "
                f"обязательных наборов {len(REQUIRED)}"
            )
        else:
            if is_private_available():
                return _fail(
                    "приватная часть подключена, но core.agent.labels "
                    "не находится — клиентские типы выпали из подсказок"
                )
    except Exception as e:  # noqa: BLE001 — приватность не должна ронять пробу
        private_note = f"проверка приватных названий пропущена: {e}"
    print(f"  {private_note}")

    # 7. Промпт читается настоящий (не запасной) — переезд prompts/.
    from services.agent.core.prompt import FALLBACK_PROMPT, load_prompt_template

    text = load_prompt_template()
    if not text or text == FALLBACK_PROMPT:
        return _fail("читается запасной промпт — PROMPTS_DIR смотрит не туда")
    print(f"  промпт: {len(text)} знаков (не запасной)")

    print("ИТОГ: ок")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
