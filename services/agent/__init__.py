#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Максимилиан — ИИ-ассистент внутри программы.

Состав (разбиение Этапа 5, 16.10.2026):

  * ``core/``    — ядро агента: цикл function calling, память, промпт;
  * ``tools/``   — инструменты: реестр, аналитика, DaData, право, RAG;
  * ``persona/`` — личность: образы, эмоциональная поддержка.

Публичный API:

    from services.agent import Agent, AgentContext, AgentReply

Или через старый путь (работает через алиасы ``sys.modules``,
зарегистрированные в ``core/__init__.py``):

    from core.agent import Agent

Границы те же, что были у ``core/agent/``:

  * инструменты НЕ работают с базой напрямую — только через ``db/crud/*``;
  * агент не удаляет данные, не создаёт договоры, не меняет настройки;
  * опасные действия (заполнение формы) требуют подтверждения оператора;
  * персональные данные маскируются перед отправкой в GigaChat.

Пакет не зависит от PyQt: интерфейс (``ui/agent_widget.py``) знает про
агента, агент про интерфейс — нет. Единственная связь с UI — объект
«окно», который передаётся в ``AgentContext`` и опрашивается через
``getattr``.
"""

from services.agent.core.agent import (
    Agent,
    AgentContext,
    AgentReply,
    ToolCall,
)
from services.agent.core.memory import AgentMemory, Message
from services.agent.core.prompt import build_system_prompt
from services.agent.persona.personas import (
    DEFAULT_PERSONA,
    PERSONAS,
    Persona,
    persona,
)
from services.agent.tools.registry import (
    Tool,
    ToolRegistry,
    build_registry,
)

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
