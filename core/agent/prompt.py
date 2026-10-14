#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Системный промпт Максимилиана (задание, часть C).

Промпт живёт ОТДЕЛЬНЫМ ФАЙЛОМ (``core/agent/prompts/maximilian.txt``), а не
строкой в коде: правка тона и правил не должна требовать правки agent.py
(задание, часть C.3). Здесь — загрузка файла, подстановка образа и списка
инструментов, плюс понятные значения по умолчанию, если файл потерялся.

Подстановки в шаблоне:

  ``{persona}``  — блок про образ (core/agent/personas.py);
  ``{tools}``    — список доступных инструментов «имя — описание»;
  ``{tool_steps}`` — лимит шагов вызова инструментов на один запрос.
"""

import logging
import os
from typing import Iterable, Optional, Tuple

from core.agent.personas import DEFAULT_PERSONA, persona

logger = logging.getLogger("core.agent.prompt")

#: Папка с текстами промптов агента.
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

#: Файл системного промпта.
PROMPT_FILE = "maximilian.txt"

#: Запасной промпт: используется, только если файл не читается (сборка без
#: ресурсов, случайное удаление). Короткий, но те же границы безопасности.
FALLBACK_PROMPT = (
    "Ты — Максимилиан, ассистент логиста в программе logistics_contractor.\n"
    "Ты помогаешь объяснять поля, искать данные и заполнять формы.\n"
    "Ты не выдумываешь данные: только то, что вернули инструменты.\n"
    "Ты не создаёшь и не удаляешь данные без явного подтверждения.\n"
    "Отвечай по-русски, кратко и по делу.\n"
    "Говори простыми словами: оператор не программист. Не называй имена\n"
    "служебных команд и не объясняй, как ты устроен внутри.\n"
    "Различай вопросы и команды: «Сможешь…?» — это вопрос, на него\n"
    "отвечают словами, а не ищут в базе. Команда без объекта («Заполни»,\n"
    "«Найди») — повод уточнить, что именно нужно, а не искать это слово.\n"
    "{persona}\n"
    "Доступные инструменты:\n{tools}\n"
    "Не больше {tool_steps} вызовов инструментов на один запрос.\n"
)


def prompt_path() -> str:
    """Полный путь к файлу системного промпта."""
    return os.path.join(PROMPTS_DIR, PROMPT_FILE)


def load_prompt_template(path: Optional[str] = None) -> str:
    """
    Читает шаблон промпта с диска.

    Ошибка чтения не прерывает работу агента: берётся FALLBACK_PROMPT, а
    причина уходит в лог. Панель агента должна открываться даже при
    повреждённой установке.
    """
    target = path or prompt_path()
    try:
        with open(target, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            return text
        logger.warning("Файл промпта пуст: %s — берём запасной", target)
    except OSError as e:
        logger.error("Не удалось прочитать промпт %s: %s — берём запасной", target, e)
    return FALLBACK_PROMPT


def _tools_block(tools: Iterable) -> str:
    """
    Список инструментов для промпта: «имя — описание».

    Принимает и объекты Tool, и пары (имя, описание): вызывающему коду не
    нужно приводить данные к одному виду.
    """
    lines = []
    for item in tools or ():
        name = getattr(item, "name", None)
        if name is None and isinstance(item, (tuple, list)) and item:
            name, description = item[0], (item[1] if len(item) > 1 else "")
        else:
            description = getattr(item, "description", "")
        lines.append(f"- {name} — {description}")
    return "\n".join(lines) if lines else "- (инструменты недоступны)"


def noise_words_block() -> str:
    """
    Список служебных слов для промпта (команды, вопросы, ругательства).

    Список берётся из core/agent/words.py — из ТОГО ЖЕ места, откуда его
    берёт проверка в инструментах поиска (core/agent/tools.py). Так промпт
    и код не могут разойтись: модель знает ровно те слова, которые код
    считает служебными, и наоборот.

    Подстановка в шаблон обязательна: без неё промпт потеряет правило
    «проверь запрос по списку перед поиском», и проблемы #12–#14 вернутся.
    """
    from core.agent.words import prompt_block

    return prompt_block()


def build_system_prompt(
    tools: Iterable = (),
    persona_key: str = DEFAULT_PERSONA,
    max_tool_steps: int = 5,
    path: Optional[str] = None,
    memory: str = "",
) -> str:
    """
    Собирает системный промпт: шаблон + образ + инструменты + лимит шагов.

    :param tools: инструменты (объекты Tool или пары «имя, описание»);
    :param persona_key: ключ образа из core/agent/personas.py;
    :param max_tool_steps: лимит вызовов инструментов на один запрос;
    :param path: переопределение файла промпта (нужно тестам);
    :param memory: блок «что помню» (core/agent/long_memory.py) — TOP-5
        релевантных записей памяти. Пусто — блок не добавляется.
    """
    template = load_prompt_template(path)
    persona_data = persona(persona_key)

    # Ошибка форматирования (в тексте промпта осталась чужая фигурная скобка)
    # не должна ронять панель: подставляем блокирующим способом.
    try:
        prompt = template.format(
            persona=persona_data.prompt_block(),
            tools=_tools_block(tools),
            tool_steps=int(max_tool_steps),
            noise_words=noise_words_block(),
        )
    except (KeyError, IndexError, ValueError) as e:
        logger.error(
            "Шаблон промпта содержит неизвестную подстановку (%s) — "
            "подставляем образ и инструменты без форматирования", e
        )
        prompt = (
            template
            + "\n\n" + persona_data.prompt_block()
            + "\n\nДоступные инструменты:\n" + _tools_block(tools)
            + f"\n\nНе больше {int(max_tool_steps)} вызовов инструментов "
              f"на один запрос.\n"
        )

    # Память добавляется ОТДЕЛЬНЫМ блоком в конце: она меняется от запроса к
    # запросу, а шаблон — нет. Если вспомнить нечего, блок не появляется
    # вовсе (пустой раздел только сбивал бы модель).
    block = str(memory or "").strip()
    if block:
        prompt = prompt + "\n\n" + block + "\n"
    return prompt


def prompt_files() -> Tuple[str, ...]:
    """Файлы промптов агента (нужно тестам и диагностике)."""
    try:
        names = sorted(
            name for name in os.listdir(PROMPTS_DIR) if name.endswith(".txt")
        )
    except OSError:
        return ()
    return tuple(names)


__all__ = [
    "FALLBACK_PROMPT",
    "PROMPTS_DIR",
    "PROMPT_FILE",
    "build_system_prompt",
    "load_prompt_template",
    "noise_words_block",
    "prompt_files",
    "prompt_path",
]
