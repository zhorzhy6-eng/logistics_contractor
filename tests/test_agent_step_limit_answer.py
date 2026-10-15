#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Служебный ответ, когда инструментов вызвано больше лимита.

НАЙДЕНО СТРЕСС-ТЕСТОМ (200 запросов, 15.10.2026, запросы №22 «Найди в базе»,
№23 «Покажи всё», №31 «Покажи отчёт»). Оператор получал вместо ответа
отладочный дамп:

    Дошёл до лимита в 3 шагов — дальше звать инструменты не буду.
    Успешно выполнено: 3. Что нашёл:
    • get_form_state: carrier_tab={'filled': {'license_date': '2026-10-15',
      ...}}, driver_tab={...}, trailer_tab={'tractor': {...}}, ...

Три беды в одном ответе: машинные имена инструментов (`get_form_state`),
`repr` вложенных словарей и длина в две тысячи символов. Проверяется, что
теперь в ответе — человеческие названия инструментов и сжатые данные.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.agent import (  # noqa: E402
    ANSWER_LIMIT_AFTER_LIMIT, Agent, ToolCall, _brief, _call_title,
)
from core.agent.memory import AgentMemory  # noqa: E402
from core.agent.tools import AgentContext, Tool, ToolRegistry  # noqa: E402


class NullRepository:
    """История без базы: тесту нужна только память диалога."""

    def append(self, *args, **kwargs):
        return 1

    def load(self, *args, **kwargs):
        return []

    def count(self, *args, **kwargs):
        return 0

    def clear_session(self, *args, **kwargs):
        return 0

    def purge_other_sessions(self, *args, **kwargs):
        return 0


class AlwaysToolClient:
    """Подставная модель: на КАЖДЫЙ шаг просит инструмент."""

    def __init__(self, tool="look_at_form", arguments=None, limit=10):
        self.tool = tool
        self.arguments = dict(arguments or {})
        self.limit = limit
        self.calls = 0

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.calls += 1
        if self.calls > self.limit:
            return {"role": "assistant", "content": "хватит"}
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"call_{self.calls}",
                "type": "function",
                "function": {
                    "name": self.tool,
                    "arguments": json.dumps(self.arguments, ensure_ascii=False),
                },
            }],
        }


def form_state():
    """Ответ инструмента «состояние формы» — как в живом прогоне."""
    return {
        "ok": True,
        "data": {
            "carrier_tab": {
                "filled": {"license_date": "2026-10-15", "vat_rate": "22"},
                "filled_count": 3,
                "empty_count": 16,
            },
            "driver_tab": {
                "filled": {"birth_date": "1996-10-15"},
                "filled_count": 4,
                "empty_count": 12,
            },
            "trailer_tab": {
                "tractor": {"brand_model": "", "plate_number": ""},
                "filled_count": 2,
                "empty_count": 0,
            },
        },
    }


def build_agent(result=None, tool="look_at_form", summary="Список открытых вкладок"):
    """Агент с одним инструментом, который всегда отвечает одинаково."""
    def function(**_):
        return result if result is not None else form_state()

    registry = ToolRegistry([
        Tool(name=tool, description="Смотрит форму", summary=summary,
             parameters={"type": "object", "properties": {}},
             function=function),
    ])
    memory = AgentMemory(session_id="limit", repository=NullRepository())
    client = AlwaysToolClient(tool=tool)
    return Agent(client=client, context=AgentContext(), memory=memory,
                 registry=registry, max_tool_steps=2)


# ─────────────────────────────────────────────────────────────
# Сжатие данных
# ─────────────────────────────────────────────────────────────

def test_brief_counts_a_list():
    assert _brief([]) == "ничего не найдено"
    assert _brief([1, 2, 3]) == "найдено 3"


def test_brief_describes_empty_dict_as_empty():
    assert _brief({}) == "пусто"
    assert _brief(None) == ""


def test_brief_compresses_form_state_into_counters():
    """
    Вложенное состояние формы — счётчиками, а не свалкой ключей.

    Именно это и печаталось оператору: `carrier_tab={'filled': {...}}`.
    """
    text = _brief(form_state()["data"])
    assert text == ("carrier_tab: заполнено 3, пусто 16; "
                    "driver_tab: заполнено 4, пусто 12; "
                    "trailer_tab: заполнено 2, пусто 0")
    assert "{" not in text and "}" not in text


def test_brief_keeps_short_scalar_values():
    text = _brief({"count": 5, "query": "Ромашка"})
    assert text == "count=5; query=Ромашка"


def test_brief_shows_lists_inside_dicts_as_counts():
    text = _brief({"found": [1, 2, 3, 4]})
    assert text == "found: 4"


def test_brief_truncates_long_text():
    text = _brief("я" * 500)
    assert len(text) <= 160
    assert text.endswith("…")


def test_brief_limits_the_number_of_fields():
    """Не больше четырёх полей: ответ оператору, а не выгрузка."""
    text = _brief({f"k{index}": index for index in range(20)})
    assert text.count("=") == 4


# ─────────────────────────────────────────────────────────────
# Человеческое имя инструмента
# ─────────────────────────────────────────────────────────────

def test_call_title_uses_human_summary():
    tool = Tool(name="get_form_state", description="", summary="Поля формы",
                parameters={}, function=lambda **_: {})
    call = ToolCall(name="get_form_state", tool=tool)
    assert _call_title(call) == "поля формы"


def test_call_title_falls_back_to_readable_name():
    """Инструмент без описания называется хотя бы без подчёркиваний."""
    call = ToolCall(name="get_open_tabs", tool=None)
    assert _call_title(call) == "get open tabs"


def test_call_title_without_anything_is_generic():
    assert _call_title(ToolCall(name="", tool=None)) == "инструмент"


# ─────────────────────────────────────────────────────────────
# Служебный ответ целиком
# ─────────────────────────────────────────────────────────────

def test_answer_after_limit_has_no_machine_names_or_dumps():
    """
    Ответ оператору: человеческие названия и сжатые данные.

    Машинного имени `look_at_form` и фигурных скобок `repr` быть не должно.
    """
    reply = build_agent().send("Покажи всё")

    assert reply.step_limit_reached is True
    assert "look_at_form" not in reply.text
    assert "{" not in reply.text and "}" not in reply.text
    assert "список открытых вкладок" in reply.text.lower()
    assert "заполнено 3, пусто 16" in reply.text


def test_answer_after_limit_is_short():
    """Длина ответа ограничена: две тысячи символов оператору не нужны."""
    reply = build_agent().send("Покажи всё")
    assert len(reply.text) <= ANSWER_LIMIT_AFTER_LIMIT


def test_answer_after_limit_mentions_failures():
    """Что не получилось — тоже часть ответа."""
    agent = build_agent(result={"ok": False, "error": "нет доступа"})
    reply = agent.send("Покажи всё")
    assert "Не получилось" in reply.text
    assert "нет доступа" not in reply.text      # внутренняя ошибка не оператору


def test_answer_after_limit_keeps_declined_mark():
    """Отменённый оператором вызов помечается как отменённый."""
    agent = build_agent()
    tool = agent.registry.get("look_at_form")
    calls = [
        ToolCall(name="look_at_form", tool=tool,
                 result={"ok": True, "data": []}),
        ToolCall(name="look_at_form", tool=tool, declined=True,
                 result={"ok": False, "error": "оператор отменил действие"}),
    ]
    text = agent._final_text_after_limit(calls)
    assert "(отменено вами)" in text


def test_answer_after_limit_names_the_step_limit_in_human_words():
    """Про лимит сказано по-человечески, без «шагов» в машинном смысле."""
    reply = build_agent().send("Покажи всё")
    assert "успеваю 2 шага" in reply.text
    assert "Уточните запрос" in reply.text


def test_answer_after_limit_shows_at_most_three_results():
    """В ответе — итог, а не журнал: не больше трёх результатов."""
    agent = build_agent()
    tool = agent.registry.get("look_at_form")
    calls = [
        ToolCall(name="look_at_form", tool=tool,
                 result={"ok": True, "data": [index]})
        for index in range(6)
    ]
    text = agent._final_text_after_limit(calls)
    assert text.count("•") == 3
