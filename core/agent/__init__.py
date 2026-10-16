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

КАК ЭТО РАБОТАЕТ. Три разных написания импорта обслуживают три разных
механизма, и путать их нельзя:

  * ``from core.agent import Agent`` — модульный ``__getattr__`` ниже:
    он берёт имя из ``services.agent``;
  * ``from core.agent.memory import AgentMemory`` и
    ``import core.agent.memory`` — алиасы ``sys.modules``, которые ставит
    ``_resolve_module`` через ``core.private_path.register_module_alias``
    (ДВЕ регистрации: имя в ``sys.modules`` и атрибут пакета — одного
    ``sys.modules`` мало, на этом спотыкались Этапы 3–5);
  * ``import core.agent.words`` — модуль живёт здесь же, алиас не нужен.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ. Статического реэкспорта
(``from core.agent.agent import Agent``) нет намеренно. Он запускает
``services.agent.*`` в момент, когда ``core`` может быть загружен лишь
ЧАСТИЧНО (так и происходит: ``services.agent.tools.registry`` импортирует
``core.agent.words`` → выполняется этот файл → он просит
``services.agent.tools.registry``, который ещё не догружен). Ленивое
взятие имён убирает цикл целиком.

``__path__`` пакета сохраняется и ДОПОЛНЯЕТСЯ приватной папкой: у
``core.agent`` есть приватный сосед ``core/agent/labels.py``
(названия клиентских типов и обязательные поля), он лежит в приватной
части проекта. Основной механизм — ``core.agent`` в ``PACKAGE_BOOTSTRAP``
(``core/private_path.py``): ``attach_private_root()`` находит пакет в
``sys.modules`` и дополняет путь. Здесь — страховка на обратный порядок,
когда пакет загрузился РАНЬШЕ подключения приватной части.
"""

# ── Приватный сосед: core/agent/labels.py из приватной части проекта ──
# Импорт `core.private_path` держим в try: если приватной части нет или
# `core` ещё не готов, shim падать не должен — без названий клиентских
# типов агент работает в режиме ядра (только «Экспедиторство»).
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
    или на ``from core.agent import Agent``. Имена МОДУЛЕЙ агента
    (``core.agent.memory``) идут сюда же — ``_resolve_module`` поднимает
    модуль и ставит алиас, после чего работает обычный механизм импорта.
    """
    from services import agent as _services_agent

    if name in _services_agent.__all__:
        return getattr(_services_agent, name)

    from core import _resolve_agent_module

    return _resolve_agent_module(name)


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
