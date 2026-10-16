#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Раскладка агента Максимилиана после разбиения (Этап 5, 16.10.2026).

Проверяет то, чего не видит обычный прогон тестов модулей:

1. `core/agent/` ДЕЙСТВИТЕЛЬНО разобран: в нём остались только `__init__.py`
   (shim) и `words.py`, а модули агента лежат в `services/agent/` тремя
   группами (`core/`, `tools/`, `persona/`).
2. Старые пути `core.agent.*` работают через алиасы и ведут к ТОМУ ЖЕ
   объекту модуля, что и новые пути. Это главная страховка шага: публичная
   часть (53 файла) и приватная часть (`tests/test_agent_all_windows.py`,
   `test_agent_tutorial.py`, `test_diagnostics.py`,
   `tools/migration/restore_public_edits.py`) импортируют агента по старым
   путям, и правок там не делалось.
3. Публичный API агента не изменился: `Agent`, `AgentContext`, `AgentReply`,
   `ToolCall`, `AgentMemory`, `Message`, `Tool`, `ToolRegistry`,
   `build_registry`, `build_system_prompt`, `PERSONAS`, `Persona`,
   `persona`, `DEFAULT_PERSONA` — доступны из обоих путей.
4. `words.py` НЕ переезжал и находится по прежнему адресу: его читает
   `core/pseudonymizer.py` ФАЙЛОМ по пути `core/agent/words.py`.
5. Приватный сосед `core/agent/labels.py` (названия клиентских типов и
   обязательные поля) по-прежнему находится через `__path__` пакета
   `core.agent` — иначе клиентские типы молча выпадают из подсказок агента.
6. Промпт `maximilian.txt` переехал вместе с `prompt.py`, и `PROMPTS_DIR`
   указывает на новое место.
7. Имена логгеров не изменились (логи не выпадают из `APP_LOGGERS`).
8. `tutorial.project_root()` считает корень проекта, а не папку `services/`.

Зачем отдельный файл: без него удаление блока алиасов в `core/__init__.py`
не покраснит НИ ОДИН тест проекта — импорты внутри `tests/` поднимаются
`conftest.py`, который импортирует пакеты целиком.

Проба, которая ловит цикл импорта в ОБРАТНОМ порядке (новый путь первым),
лежит рядом: `tools/check_stage5_aliases.py` (режимы `old` и `new`).
"""

import importlib
import logging
import subprocess
import sys
from pathlib import Path

import pytest

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
AGENT_OLD = PROJECT_ROOT / "core" / "agent"
AGENT_NEW = PROJECT_ROOT / "services" / "agent"
AGENT_CORE = AGENT_NEW / "core"
AGENT_TOOLS = AGENT_NEW / "tools"
AGENT_PERSONA = AGENT_NEW / "persona"

#: Файлы, которые остались в `core/agent/` — и ничего больше.
OLD_PACKAGE_FILES = ["__init__.py", "words.py"]

#: Пакеты новой раскладки.
NEW_PACKAGES = [
    "services.agent",
    "services.agent.core",
    "services.agent.tools",
    "services.agent.persona",
]

#: Группа «ядро»: файл на новом месте и прежний адрес модуля.
CORE_MODULES = [
    ("agent.py", "core.agent.agent"),
    ("llm.py", "core.agent.llm"),
    ("memory.py", "core.agent.memory"),
    ("long_memory.py", "core.agent.long_memory"),
    ("prompt.py", "core.agent.prompt"),
]

#: Группа «инструменты» (файл, старый адрес модуля).
TOOL_MODULES = [
    ("registry.py", "core.agent.tools"),
    ("analytics.py", "core.agent.analytics_tools"),
    ("dadata.py", "core.agent.dadata_tools"),
    ("legal.py", "core.agent.legal_tools"),
    ("rag.py", "core.agent.rag"),
    ("context.py", "core.agent.context_tools"),
    ("proactivity.py", "core.agent.proactivity"),
    ("proactivity_tools.py", "core.agent.proactivity_tools"),
    ("fill_parser.py", "core.agent.fill_parser"),
    ("tutorial.py", "core.agent.tutorial"),
    ("tutorial_hints.py", "core.agent.tutorial_hints"),
]

#: Группа «личность».
PERSONA_MODULES = [
    ("personas.py", "core.agent.personas"),
    ("emotional.py", "core.agent.emotional"),
]

#: Публичный API пакета агента (доступен из обоих путей).
PUBLIC_API = [
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
]

#: Логгеры: имя модуля в старом мире → имя логгера. Логи проекта читаются
#: настройкой `config/logging_config.py` (`APP_LOGGERS` = дерево `core`).
#: Переезд НЕ должен менять эти имена.
LOGGER_NAMES = [
    ("services.agent.core.agent", "core.agent.agent"),
    ("services.agent.core.llm", "core.agent.llm"),
    ("services.agent.core.memory", "core.agent.memory"),
    ("services.agent.core.long_memory", "core.agent.long_memory"),
    ("services.agent.core.prompt", "core.agent.prompt"),
    ("services.agent.tools.registry", "core.agent.tools"),
    ("services.agent.tools.analytics", "core.agent.analytics_tools"),
    ("services.agent.tools.dadata", "core.agent.dadata_tools"),
    ("services.agent.tools.legal", "core.agent.legal_tools"),
    ("services.agent.tools.rag", "core.agent.rag"),
    ("services.agent.tools.context", "core.agent.context_tools"),
    ("services.agent.tools.proactivity", "core.agent.proactivity"),
    ("services.agent.tools.proactivity_tools", "core.agent.proactivity_tools"),
    ("services.agent.tools.fill_parser", "core.agent.fill_parser"),
    ("services.agent.tools.tutorial", "core.agent.tutorial"),
    ("services.agent.tools.tutorial_hints", "core.agent.tutorial_hints"),
    ("services.agent.persona.emotional", "core.agent.emotional"),
]


def _run_in_fresh_process(code: str) -> subprocess.CompletedProcess:
    """Запускает код в свежем процессе из корня проекта."""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def _agent_alias_names():
    """Старые имена модулей агента — из карты `core._AGENT_ALIASES`."""
    import core

    return tuple(core._AGENT_ALIASES)


#: Старые имена модулей агента (берутся из карты алиасов `core`).
_AGENT_ALIAS_NAMES = _agent_alias_names()


# ─────────────────────────────────────────────────────────
# 1. Файлы и пакеты
# ─────────────────────────────────────────────────────────

def test_old_package_has_only_shim_and_words():
    """
    В `core/agent/` остались ровно два модуля: shim и `words.py`.

    `words.py` не переезжает НАМЕРЕННО: `core/pseudonymizer.py` читает его
    ФАЙЛОМ по пути `core/agent/words.py` (обычный импорт тянул бы за собой
    весь пакет агента и давал «partially initialized module»).
    """
    python_files = sorted(
        path.name for path in AGENT_OLD.glob("*.py")
    )
    assert python_files == OLD_PACKAGE_FILES, (
        f"в core/agent/ осталось {python_files} — ожидались {OLD_PACKAGE_FILES}"
    )


def test_prompt_file_moved():
    """Промпт `maximilian.txt` переехал вместе с `prompt.py`."""
    assert (AGENT_CORE / "prompts" / "maximilian.txt").is_file(), (
        "нет services/agent/core/prompts/maximilian.txt"
    )
    assert not (AGENT_OLD / "prompts").exists(), (
        "папка core/agent/prompts/ вернулась — переезд Этапа 5 отменён?"
    )


@pytest.mark.parametrize(
    "module_name",
    [name for _, name in CORE_MODULES] + ["core.agent.prompts"],
    ids=[name for _, name in CORE_MODULES] + ["core.agent.prompts"],
)
def test_old_module_files_are_gone(module_name):
    """Файлов на старых адресах больше нет (кроме двух разрешённых)."""
    short = module_name.split(".")[-1]
    assert not (AGENT_OLD / f"{short}.py").exists(), (
        f"core/agent/{short}.py вернулся — разбиение Этапа 5 отменено?"
    )


@pytest.mark.parametrize("package", NEW_PACKAGES)
def test_new_package_exists_with_docstring(package):
    """Все четыре пакета новой раскладки — обычные пакеты с докстрокой."""
    module = importlib.import_module(package)
    assert module.__doc__ and module.__doc__.strip(), (
        f"у пакета {package} нет докстроки"
    )


@pytest.mark.parametrize(
    "directory", [AGENT_CORE, AGENT_TOOLS, AGENT_PERSONA],
    ids=["core", "tools", "persona"],
)
def test_new_group_directory_exists(directory):
    """Папки трёх групп на месте."""
    assert directory.is_dir(), f"нет папки {directory}"


@pytest.mark.parametrize(
    "filename", [name for name, _ in CORE_MODULES], ids=lambda name: name
)
def test_core_module_file_exists(filename):
    """Файлы группы «ядро» на месте."""
    assert (AGENT_CORE / filename).is_file(), f"нет services/agent/core/{filename}"


@pytest.mark.parametrize(
    "filename", [name for name, _ in TOOL_MODULES], ids=lambda name: name
)
def test_tool_module_file_exists(filename):
    """Файлы группы «инструменты» на месте."""
    assert (AGENT_TOOLS / filename).is_file(), f"нет services/agent/tools/{filename}"


@pytest.mark.parametrize(
    "filename", [name for name, _ in PERSONA_MODULES], ids=lambda name: name
)
def test_persona_module_file_exists(filename):
    """Файлы группы «личность» на месте."""
    assert (AGENT_PERSONA / filename).is_file(), f"нет services/agent/persona/{filename}"


def test_words_stayed_in_old_package():
    """
    `words.py` остался на прежнем адресе.

    Его читает `core/pseudonymizer.py` как ФАЙЛ (`_WORDS_MODULE =
    "core.agent.words"`), и переезд увёл бы таблицу служебных слов
    из-под загрузчика.
    """
    assert (AGENT_OLD / "words.py").is_file(), (
        "core/agent/words.py пропал — обезличивание потеряет таблицу "
        "служебных слов"
    )


def test_pseudonymizer_points_at_words_file():
    """Загрузчик слов в `pseudonymizer.py` по-прежнему смотрит на старый путь."""
    from core import pseudonymizer

    assert pseudonymizer._WORDS_MODULE == "core.agent.words"
    assert pseudonymizer.load_words_module() is not None


# ─────────────────────────────────────────────────────────
# 2. Оба пути дают ОДИН модуль
# ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "old_name,new_name",
    [
        ("core.agent.agent", "services.agent.core.agent"),
        ("core.agent.llm", "services.agent.core.llm"),
        ("core.agent.memory", "services.agent.core.memory"),
        ("core.agent.long_memory", "services.agent.core.long_memory"),
        ("core.agent.prompt", "services.agent.core.prompt"),
        ("core.agent.tools", "services.agent.tools.registry"),
        ("core.agent.analytics_tools", "services.agent.tools.analytics"),
        ("core.agent.dadata_tools", "services.agent.tools.dadata"),
        ("core.agent.legal_tools", "services.agent.tools.legal"),
        ("core.agent.rag", "services.agent.tools.rag"),
        ("core.agent.context_tools", "services.agent.tools.context"),
        ("core.agent.proactivity", "services.agent.tools.proactivity"),
        ("core.agent.proactivity_tools", "services.agent.tools.proactivity_tools"),
        ("core.agent.fill_parser", "services.agent.tools.fill_parser"),
        ("core.agent.tutorial", "services.agent.tools.tutorial"),
        ("core.agent.tutorial_hints", "services.agent.tools.tutorial_hints"),
        ("core.agent.personas", "services.agent.persona.personas"),
        ("core.agent.emotional", "services.agent.persona.emotional"),
    ],
    ids=lambda value: value.split(".")[-1],
)
def test_old_and_new_paths_are_the_same_module(old_name, new_name):
    """
    Старый адрес и новый — ОДИН объект модуля.

    Разъехались — значит, код видит два разных класса: подмена в тестах и
    `isinstance` в рабочем коде перестают работать. Именно так и сломался
    первый заход Этапа 5 (перехватчик импорта вместо алиасов).
    """
    old = importlib.import_module(old_name)
    new = importlib.import_module(new_name)
    assert old is new, f"{old_name} и {new_name} — разные объекты модуля"


@pytest.mark.parametrize(
    "old_name",
    [name for _, name in CORE_MODULES]
    + [name for _, name in TOOL_MODULES]
    + [name for _, name in PERSONA_MODULES],
)
def test_old_name_is_in_sys_modules(old_name):
    """Старое имя лежит в `sys.modules` — это читает `from ... import ...`."""
    module = importlib.import_module(old_name)
    assert sys.modules.get(old_name) is module


def test_alias_lands_as_package_attribute():
    """
    Алиас виден и как АТРИБУТ пакета `core.agent` — БЕЗ участия `__getattr__`.

    Одного `sys.modules` мало: инструкция `import core.agent.llm` после
    загрузки читает последнее имя как атрибут родителя. Ровно на этом
    спотыкались Этапы 3, 4 и 5.

    Проверяется именно запись в `__dict__` пакета: `getattr` вернул бы имя
    и через запасной `__getattr__` shim'а, и дефект остался бы незамеченным
    (проверено на багe — с выключенным `setattr` этот тест обязан краснеть).
    """
    import core.agent as agent

    for short, full in (
        ("llm", "core.agent.llm"),
        ("tools", "core.agent.tools"),
        ("context_tools", "core.agent.context_tools"),
        ("analytics_tools", "core.agent.analytics_tools"),
        ("tutorial_hints", "core.agent.tutorial_hints"),
    ):
        assert short in vars(agent), (
            f"core.agent.{short} не записан атрибутом пакета — "
            "import core.agent." + short + " упадёт с AttributeError"
        )
        assert vars(agent)[short] is sys.modules[full], (
            f"атрибут core.agent.{short} — не тот модуль"
        )


def test_module_name_is_the_new_one():
    """`__name__` модуля — новый: это переехавший файл, а не копия."""
    module = importlib.import_module("core.agent.llm")
    assert module.__name__ == "services.agent.core.llm"


def test_alias_helper_is_used_for_agent_aliases():
    """
    Алиасы агента зарегистрированы общей функцией (`register_module_alias`).

    Проверяется по факту: и запись в `sys.modules`, и атрибут пакета —
    то есть обе регистрации на месте.
    """
    import core
    import core.agent as agent

    assert hasattr(core, "_AGENT_ALIASES")
    for short, full in (
        ("llm", "core.agent.llm"),
        ("memory", "core.agent.memory"),
        ("rag", "core.agent.rag"),
    ):
        assert sys.modules[full] is getattr(agent, short)


# ─────────────────────────────────────────────────────────
# 3. Публичный API
# ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", PUBLIC_API)
def test_public_api_from_new_path(name):
    """Публичный API доступен по НОВОМУ пути."""
    module = importlib.import_module("services.agent")
    assert hasattr(module, name), f"services.agent потерял {name}"


@pytest.mark.parametrize("name", PUBLIC_API)
def test_public_api_from_old_path(name):
    """Тот же публичный API доступен и по СТАРОМУ пути (через алиасы)."""
    module = importlib.import_module("core.agent")
    assert hasattr(module, name), f"core.agent потерял {name}"


@pytest.mark.parametrize("name", PUBLIC_API)
def test_public_api_is_in_dunder_all(name):
    """`__all__` пакета перечисляет публичный API."""
    module = importlib.import_module("services.agent")
    assert name in module.__all__


def test_public_names_are_the_same_objects():
    """`from core.agent import Agent` — тот же класс, что и в `services.agent`."""
    from core.agent import Agent as OldAgent
    from core.agent import Tool as OldTool
    from core.agent import persona as old_persona
    from services.agent import Agent as NewAgent
    from services.agent import Tool as NewTool
    from services.agent import persona as new_persona

    assert OldAgent is NewAgent
    assert OldTool is NewTool
    assert old_persona is new_persona


def test_class_module_points_at_new_place():
    """Классы объявлены в новых модулях (проверка «это тот же файл»)."""
    from services.agent import Agent, AgentMemory, Persona, Tool, ToolRegistry
    from services.agent.tools.registry import AgentContext

    assert Agent.__module__ == "services.agent.core.agent"
    assert AgentContext.__module__ == "services.agent.tools.registry"
    assert AgentMemory.__module__ == "services.agent.core.memory"
    assert Tool.__module__ == "services.agent.tools.registry"
    assert ToolRegistry.__module__ == "services.agent.tools.registry"
    assert Persona.__module__ == "services.agent.persona.personas"


def test_agent_package_docstring_mentions_three_groups():
    """Докстрока пакета описывает новую раскладку (три группы)."""
    import services.agent as agent

    text = agent.__doc__ or ""
    for part in ("core/", "tools/", "persona/"):
        assert part in text, f"в докстроке services.agent нет {part}"


def test_core_agent_shim_keeps_path():
    """
    У пакета `core.agent` есть `__path__` — он остался ОБЫЧНЫМ пакетом.

    Это нужно приватному соседу `core/agent/labels.py`: расширение
    `__path__` работает только у пакета.
    """
    import core.agent as agent

    assert hasattr(agent, "__path__")
    assert str(AGENT_OLD) in list(agent.__path__)


# ─────────────────────────────────────────────────────────
# 4. Приватный сосед и режим ядра
# ─────────────────────────────────────────────────────────

def test_default_bootstrap_extends_core_agent():
    """`core.agent` (как и `services.agent`) — в списке расширяемых путей."""
    from core import private_path

    assert "core.agent" in private_path.PACKAGE_BOOTSTRAP
    assert "services.agent" in private_path.PACKAGE_BOOTSTRAP


def test_private_labels_are_found_when_private_part_is_attached():
    """
    Приватные названия клиентских типов видны инструментам агента.

    Проверка мягкая: без приватной папки типов просто нет, и это нормальный
    режим ядра. Но если папка ЕСТЬ, названия ОБЯЗАНЫ домешаться — иначе
    клиентские типы молча выпадают из подсказок агента (ошибка тихая,
    она и была поймана на первом заходе Этапа 5).
    """
    from core.private_path import is_private_available

    if not is_private_available():
        pytest.skip("приватная часть не подключена — режим ядра")

    from services.agent.tools.context import TYPE_TITLES
    from services.agent.tools.proactivity import REQUIRED

    assert "perevozka" in TYPE_TITLES
    assert len(TYPE_TITLES) > 1, (
        "приватная часть подключена, но названий клиентских типов нет: "
        "core.agent.labels не находится"
    )
    assert len(REQUIRED) > 1, (
        "приватная часть подключена, но обязательных полей клиентских "
        "типов нет: core.agent.labels не находится"
    )


def test_core_agent_labels_import_matches_context_titles():
    """Названия из `core.agent.labels` доехали до инструмента контекста."""
    from core.private_path import is_private_available

    if not is_private_available():
        pytest.skip("приватная часть не подключена — режим ядра")

    from core.agent.labels import TYPE_TITLES as private_titles
    from services.agent.tools import context

    for key, title in private_titles.items():
        assert context.TYPE_TITLES.get(key) == title


# ─────────────────────────────────────────────────────────
# 5. Промпт, логгеры, пути
# ─────────────────────────────────────────────────────────

def test_prompt_dir_points_at_new_place():
    """
    `PROMPTS_DIR` строится от `__file__` — значит, он уже новый.

    Ошибка была бы тихой: `load_prompt_template` молча вернул бы
    `FALLBACK_PROMPT`, и агент отвечал бы не тем промптом.
    """
    from services.agent.core import prompt

    assert Path(prompt.PROMPTS_DIR) == AGENT_CORE / "prompts"
    assert Path(prompt.prompt_path()).is_file()


def test_real_prompt_is_loaded_not_fallback():
    """Читается НАСТОЯЩИЙ промпт, а не запасной."""
    from services.agent.core.prompt import FALLBACK_PROMPT, load_prompt_template

    text = load_prompt_template()
    assert text
    assert text != FALLBACK_PROMPT
    assert len(text) > len(FALLBACK_PROMPT)


def test_prompt_template_is_complete():
    """В файле промпта есть все подстановки шаблона."""
    from services.agent.core.prompt import load_prompt_template

    text = load_prompt_template()
    for placeholder in ("{persona}", "{tools}", "{tool_steps}"):
        assert placeholder in text, f"в промпте нет {placeholder}"


@pytest.mark.parametrize(
    "module_name,logger_name",
    LOGGER_NAMES,
    ids=[name.split(".")[-1] for name, _ in LOGGER_NAMES],
)
def test_logger_names_did_not_change(module_name, logger_name):
    """
    Имя логгера не изменилось при переезде.

    Логи проекта читаются настройкой (`APP_LOGGERS` = дерево `core`): имя
    `services.agent.*` выпало бы из настройки, и инструменты замолчали бы.
    """
    module = importlib.import_module(module_name)
    assert module.logger.name == logger_name, (
        f"{module_name}: логгер {module.logger.name!r} вместо {logger_name!r}"
    )


def test_agent_loggers_are_under_core_tree():
    """Имена логгеров агента по-прежнему попадают в дерево `core`."""
    from services.agent.tools import registry

    assert logging.getLogger(registry.logger.name).name.startswith("core.")


def test_tutorial_project_root_is_project_root():
    """
    `tutorial.project_root()` считает корень ПРОЕКТА.

    Файл переехал на уровень глубже (`core/agent/` → `services/agent/tools/`),
    и без правки корнем стала бы папка `services/`. Ошибка тихая: список
    папок инструкций просто оказался бы пустым.
    """
    from services.agent.tools import tutorial

    assert Path(tutorial.project_root()) == PROJECT_ROOT
    assert tutorial.TUTORIAL_RELATIVE_PATH == str(
        Path("data") / "knowledge" / "tutorial"
    )


def test_tutorial_finds_public_instructions():
    """Публичные инструкции оператора находятся (папка есть и не пуста)."""
    from services.agent.tools import tutorial

    directories = tutorial.tutorial_dirs()
    assert directories, "список папок инструкций пуст"
    found = [
        directory for directory in directories
        if "knowledge" in directory and Path(directory).is_dir()
    ]
    if not (PROJECT_ROOT / "data" / "knowledge" / "tutorial").is_dir():
        pytest.skip("публичной папки инструкций нет")
    assert found, f"публичная папка инструкций не найдена среди {directories}"


# ─────────────────────────────────────────────────────────
# 6. Оба порядка импорта — отдельными процессами
# ─────────────────────────────────────────────────────────

def test_order_old_first():
    """Старый путь ПЕРВЫМ, затем новый — свежим процессом."""
    result = _run_in_fresh_process(
        "import core.agent, services.agent;"
        "assert core.agent.agent is services.agent.core.agent;"
        "assert core.agent.tools is services.agent.tools.registry;"
        "print('ok')"
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_order_new_first():
    """
    Новый путь ПЕРВЫМ, затем старый — свежим процессом.

    Именно этот порядок ломался в Этапах 2–4: в общем прогоне pytest
    `core` уже поднят `conftest.py`, и обратный порядок там не проверяется
    НИКОГДА.
    """
    result = _run_in_fresh_process(
        "import importlib, services.agent, core.agent;"
        "personas = importlib.import_module('services.agent.persona.personas');"
        "assert core.agent.llm is services.agent.core.llm;"
        "assert core.agent.personas is personas;"
        "print('ok')"
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_persona_name_shadows_subpackage():
    """
    `services.agent.persona` — ФУНКЦИЯ, а подпакет — `services.agent.persona`
    в `sys.modules`. Это НЕ ошибка переезда, а поведение Python.

    Функция `persona` объявлена в `personas.py` и лежит в публичном API
    пакета, поэтому в `__init__.py` она привязывается к имени пакета.
    Проверено минимальным примером на временном пакете (16.10.2026):
    имя из `__init__.py` затеняет одноимённый подпакет и в обычном Python.

    Поэтому импорт подпакета идёт ЧЕРЕЗ `importlib.import_module` (и через
    `from services.agent.persona.personas import …` — он тоже работает:
    это импорт подмодуля, его обслуживает `sys.modules`).
    """
    import services.agent as agent
    from services.agent.persona.personas import PERSONAS, persona

    assert callable(agent.persona)
    assert agent.persona is persona
    assert "perevozka" in PERSONAS or PERSONAS, "personas не загрузился"

    subpackage = importlib.import_module("services.agent.persona")
    assert subpackage is not agent.persona
    assert (subpackage.__file__ or "").endswith("__init__.py")


def test_submodule_import_first_in_fresh_process():
    """`from core.agent.memory import AgentMemory` первым — тоже работает."""
    result = _run_in_fresh_process(
        "from core.agent.memory import AgentMemory;"
        "import services.agent.core.memory as new;"
        "assert AgentMemory is new.AgentMemory;"
        "print('ok')"
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_all_old_paths_in_one_fresh_process():
    """Все старые пути агента поднимаются в ОДНОМ свежем процессе."""
    result = _run_in_fresh_process(
        "import importlib;"
        "names = ["
        "'core.agent', 'core.agent.agent', 'core.agent.llm',"
        "'core.agent.memory', 'core.agent.long_memory', 'core.agent.prompt',"
        "'core.agent.tools', 'core.agent.analytics_tools',"
        "'core.agent.dadata_tools', 'core.agent.legal_tools',"
        "'core.agent.rag', 'core.agent.context_tools',"
        "'core.agent.proactivity', 'core.agent.proactivity_tools',"
        "'core.agent.fill_parser', 'core.agent.tutorial',"
        "'core.agent.tutorial_hints', 'core.agent.personas',"
        "'core.agent.emotional', 'core.agent.words',"
        "];"
        "[importlib.import_module(name) for name in names];"
        "print('ok', len(names))"
    )
    assert result.returncode == 0, result.stderr
    assert "ok 20" in result.stdout


def test_import_statement_for_every_alias_in_fresh_process():
    """
    `import core.agent.<имя>` (ИМЕННО ИНСТРУКЦИЯ) для всех алиасов сразу.

    Инструкция `import a.b.c` после загрузки читает последнее имя как
    АТРИБУТ родителя — это единственный механизм, который ломается, если
    алиас положили только в `sys.modules`. Через `importlib.import_module`
    дефект не виден: там чтение идёт из `sys.modules`.

    Проверяется в СВЕЖЕМ процессе: в общем прогоне pytest пакет `core.agent`
    уже поднят `conftest.py`, и атрибуты выставлены импортом подмодулей.
    """
    module_names = sorted(
        old for old in _AGENT_ALIAS_NAMES if old != "core.agent"
    )
    code = "\n".join(f"import {name}" for name in module_names)
    code += "\nimport core.agent\nprint('ok', len(%r))" % (module_names,)
    result = _run_in_fresh_process(code)
    assert result.returncode == 0, (
        f"инструкция import упала для одного из алиасов:\n{result.stderr}"
    )
    assert f"ok {len(module_names)}" in result.stdout


def test_registry_import_does_not_break_prompt():
    """Инструменты агента поднимаются и находят промпт (сквозная проверка)."""
    result = _run_in_fresh_process(
        "from core.agent.tools import build_registry, AgentContext;"
        "from core.agent.prompt import build_system_prompt;"
        "registry = build_registry(AgentContext());"
        "text = build_system_prompt(registry.prompt_lines(), max_tool_steps=3);"
        "assert len(registry.names()) > 20;"
        "print('ok', len(registry.names()))"
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
