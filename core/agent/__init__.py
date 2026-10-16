#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shim: ``core.agent`` → ``services.agent`` (Этап 5, 16.10.2026).

Всё содержимое пакета переехало в ``services/agent/``:

  * ``services/agent/core/``    — ядро: ``agent``, ``llm``, ``memory``,
    ``long_memory``, ``prompt`` (+ ``prompts/maximilian.txt``);
  * ``services/agent/tools/``   — инструменты: ``registry`` (был ``tools.py``),
    ``analytics``, ``dadata``, ``legal``, ``rag``, ``context``,
    ``proactivity``, ``proactivity_tools``, ``fill_parser``, ``tutorial``,
    ``tutorial_hints``;
  * ``services/agent/persona/`` — личность: ``personas``, ``emotional``.

Здесь остались только два файла: этот shim и ``words.py`` (общая таблица
служебных слов: её читают и агент, и ``core/pseudonymizer.py`` файлом по
пути ``core/agent/words.py`` — переезд увёл бы её из-под загрузчика).

ПОЧЕМУ ИМПОРТОВ ЗДЕСЬ НЕТ. Статический реэкспорт
(``from core.agent.agent import Agent``) в момент ПЕРВОГО импорта
``services.agent.*`` упирается в цикл: `services.agent.tools.registry`
импортирует ``core.agent.words`` → выполняется ЭТОТ файл → он просит
``core.agent.agent``, которого по старому пути уже нет. Поэтому имена
отдаются ЛЕНИВО, через module-level ``__getattr__``: он срабатывает
только на обращение к атрибуту (``core.agent.Agent`` и
``from core.agent import Agent``), а импорт ПОДМОДУЛЯ
(``from core.agent.memory import AgentMemory``) обслуживают алиасы
``sys.modules``, зарегистрированные в ``core/__init__.py``.

``__path__`` пакета сохраняется и ДОПОЛНЯЕТСЯ приватной папкой: у
``core.agent`` есть приватный сосед ``core/agent/labels.py``
(названия клиентских типов и обязательные поля), он лежит в приватной
части проекта. Без этого дополнения ``from core.agent.labels import …``
в ``services/agent/tools/context.py`` и ``proactivity.py`` перестал бы
находиться. Ошибка тихая: приватные типы просто выпали бы из подсказок.
"""

# ── Приватный сосед: core/agent/labels.py из приватной части проекта ──
# Импорт `core.private_path` внутри try: при ПЕРВОМ импорте
# `services.agent.*` пакет `core` уже начал загружаться (см. пояснение
# выше), и падать из-за этого shim не должен — приватные названия типов
# нужны подсказкам, а не работе агента.
try:
    from core.private_path import private_dir_for as _private_dir_for

    _private_agent = _private_dir_for("core.agent")
    if _private_agent is not None and str(_private_agent) not in __path__:
        __path__.append(str(_private_agent))
except Exception:  # noqa: BLE001 — без приватной части агент работает
    pass


def __getattr__(name):
    """
    Ленивый реэкспорт имён агента из ``services.agent``.

    Вызывается только на доступ к АТРИБУТУ пакета (``core.agent.Agent``)
    или на ``from core.agent import Agent``. Импорт подмодулей идёт мимо:
    его обслуживают алиасы ``sys.modules`` из ``core/__init__.py``.
    """
    from services import agent as _services_agent

    try:
        return getattr(_services_agent, name)
    except AttributeError:
        raise AttributeError(
            f"module 'core.agent' has no attribute {name!r}"
        ) from None


__all__ = [
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
