#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Служебный ответ, когда запрос остановлен предохранителем.

НАЙДЕНО СТРЕСС-ТЕСТОМ (200 запросов, 15.10.2026, запросы №22 «Найди в базе»,
№23 «Покажи всё», №31 «Покажи отчёт»). Оператор получал вместо ответа
отладочный дамп:

    Дошёл до лимита в 3 шагов — дальше звать инструменты не буду.
    Успешно выполнено: 3. Что нашёл:
    • get_form_state: carrier_tab={'filled': {'license_date': '2026-10-15',
      ...}}, driver_tab={...}, trailer_tab={'tractor': {...}}, ...

Две беды в одном ответе: машинные имена инструментов (`get_form_state`) и
`repr` вложенных словарей. Сначала ответ стал человечнее (сжатые данные), а
затем, после поднятия лимита шагов до 20, стал ещё и полезнее: оператор
получает предложение разбить задачу на части и выбрать первый шаг.

Проверяется, что в ответе нет ни машинных имён, ни `repr`, ни технических
идентификаторов (`window=Генератор…`, `contract_type=perevozka`), а лимит
назван по-человечески.
"""

import json
import sys
from pathlib import Path

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
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
    Состояние формы — счётчиками, а не свалкой ключей.

    Именно это и печаталось оператору: `carrier_tab={'filled': {...}}`.
    Имена вкладок (`carrier_tab`, `driver_tab`) — технические: из ответа
    оператору они убраны (задание: без технических идентификаторов), а сами
    счётчики заполненных полей остались.
    """
    text = _brief({"Поля формы": {"filled_count": 3, "empty_count": 16}})
    assert text == "Поля формы: заполнено 3, пусто 16"

    state = _brief(form_state()["data"])
    assert "{" not in state and "}" not in state
    for machine_name in ("carrier_tab", "driver_tab", "trailer_tab"):
        assert machine_name not in state


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
    Ответ оператору: человеческое название инструмента и никаких дампов.

    Машинного имени `look_at_form`, фигурных скобок `repr` и имён вкладок
    (`carrier_tab`) быть не должно: это имена из кода, оператор их не знает.
    """
    reply = build_agent().send("Покажи всё")

    assert reply.step_limit_reached is True
    assert "look_at_form" not in reply.text
    assert "{" not in reply.text and "}" not in reply.text
    assert "carrier_tab" not in reply.text
    assert "список открытых вкладок" in reply.text.lower()


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
    """
    Про лимит сказано по-человечески: сколько шагов успеваю.

    Счёт идёт по НАСТРОЙКЕ, а не по факту: оператору важно, сколько он
    может получить за один запрос.
    """
    reply = build_agent().send("Покажи всё")
    assert "успеваю 2 шага" in reply.text
    assert "лимит исчерпан" in reply.text


def test_answer_after_limit_offers_to_split_the_task():
    """
    Главное в ответе — предложение разбить задачу и выбор первого шага.

    Это и есть «человеческий ответ» из задания: оператор получает выход из
    тупика, а не отчёт о том, что лимит кончился.
    """
    reply = build_agent().send("Куда тыкать? Мне нужно внести данные "
                               "по организации")

    assert "Задача большая — не успел за один запрос" in reply.text
    assert "Давай по частям" in reply.text
    assert "Что сделать первым:" in reply.text
    assert "• Найти организацию в базе?" in reply.text
    assert "• Заполнить форму?" in reply.text
    assert "• Проверить в ЕГРЮЛ?" in reply.text


def test_answer_after_limit_says_what_was_done_without_a_dump():
    """
    Сделанное — одной строкой с количеством, а не разделом с дампом.

    Раньше в ответе был блок «Что успел узнать:» и по строке на инструмент с
    его данными. Теперь это одна строка «Успел: …».
    """
    reply = build_agent().send("Покажи всё")

    assert "Что успел узнать" not in reply.text
    assert reply.text.count("Успел:") == 1
    assert "{" not in reply.text and "}" not in reply.text
