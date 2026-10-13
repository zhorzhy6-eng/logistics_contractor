#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Пакет ИИ-агента «Максимилиан» (версия 1).

Состав:

  * ``prompt.py``   — системный промпт (отдельный файл, не хардкод);
  * ``personas.py`` — образы (учёный кот, кот-логист, кот-хранитель, без образа);
  * ``tools.py``    — реестр инструментов (function calling) над db/crud/*;
  * ``memory.py``   — история диалога (в памяти + таблица agent_history);
  * ``llm.py``      — вызов GigaChat с инструментами и маскированием ПДн;
  * ``agent.py``    — главный цикл: сообщение → инструменты → ответ.

Границы (см. задание, части G и B.7):

  * инструменты НЕ работают с базой напрямую — только через db/crud/*;
  * агент не удаляет данные, не создаёт договоры, не меняет настройки;
  * опасные действия (заполнение формы) требуют подтверждения оператора;
  * персональные данные маскируются перед отправкой в GigaChat.

Пакет не зависит от PyQt: интерфейс (ui/agent_widget.py) знает про агента,
агент про интерфейс — нет. Единственная связь с UI — объект «окно»,
который передаётся в AgentContext и опрашивается через getattr.
"""

from core.agent.agent import Agent, AgentContext, AgentReply, ToolCall
from core.agent.memory import AgentMemory, Message
from core.agent.personas import DEFAULT_PERSONA, PERSONAS, Persona, persona
from core.agent.prompt import build_system_prompt
from core.agent.tools import Tool, ToolRegistry, build_registry

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
