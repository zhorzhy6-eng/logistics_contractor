#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вызов инструментов GigaChat: проводной формат (Часть 0, F.1).

Что было сломано
----------------
Максимилиан отвечал текстом вместо вызова инструментов. Причина оказалась
не в промпте: описания инструментов уходили в поле ``tools``, а
``api.giga.chat/v1`` это поле принимает и МОЛЧА ИГНОРИРУЕТ. Доказательство
из разведки живого API (24.10.2026, GigaChat-3-Pro):

  * ``tools`` с заведомо неверной схемой (``"parameters": "МУСОР"``) даёт
    200 и обычный текст — параметр не разбирается вовсе;
  * ``functions`` + ``function_call: "auto"`` работает:
    ``finish_reason="function_call"``, ``message.function_call`` с именем и
    аргументами-ОБЪЕКТОМ;
  * ``function_call: "required"`` → 400 (значение не поддержано);
  * результат функции принимается сообщением
    ``{"role": "function", "name": …, "content": "<строка>"}``:
    ``role: "tool"`` → 422, ``content`` объектом → 400;
  * история обязана содержать ``assistant.function_call`` этого вызова,
    иначе результат отвергается (422 «every function result must have an
    assistant function call in history»).

Здесь это зафиксировано тестами: сеть не нужна, транспорт подменён.
"""

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.gigachat_client as gigachat_module  # noqa: E402
from core.agent.agent import Agent  # noqa: E402
from core.agent.memory import AgentMemory  # noqa: E402
from core.agent.tools import Tool, build_registry  # noqa: E402
from core.gigachat_client import (  # noqa: E402
    _functions_for_wire,
    _messages_for_wire,
    _tool_calls_from_message,
)


# ─────────────────────────────────────────────────────────────
# Подставной транспорт
# ─────────────────────────────────────────────────────────────

class FakeResponse:
    """Ответ /chat/completions с заданным сообщением модели."""

    def __init__(self, message, status_code=200):
        self.status_code = status_code
        self.text = json.dumps(message, ensure_ascii=False)
        self._message = message

    def json(self):
        return {
            "choices": [{"message": self._message, "finish_reason": "stop"}]
        }


class FakeTransport:
    """
    Транспорт GigaChat: отдаёт заранее заданные сообщения по очереди.

    Запоминает каждый отправленный payload — по нему и проверяется
    проводной формат.
    """

    def __init__(self, messages):
        self.messages = list(messages)
        self.payloads = []

    def post(self, url, **kwargs):
        """Подменяет ``requests.post`` (сигнатура вызова клиента)."""
        self.payloads.append(kwargs.get("json") or {})
        message = (
            self.messages.pop(0) if self.messages
            else {"role": "assistant", "content": "готово"}
        )
        return FakeResponse(message)

    @property
    def last(self):
        """Последний отправленный payload."""
        return self.payloads[-1] if self.payloads else {}


def _tool_call_message(name="search_driver", arguments=None, call_id="call-1"):
    """Сообщение модели «прошу вызвать функцию» в формате GigaChat."""
    return {
        "role": "assistant",
        "content": "",
        "function_call": {
            "id": call_id,
            "name": name,
            "arguments": {"query": "Иванов"} if arguments is None else arguments,
        },
    }


@pytest.fixture
def transport(monkeypatch):
    """Подменённый транспорт; по умолчанию — вызов функции, затем текст."""
    fake = FakeTransport([_tool_call_message(),
                          {"role": "assistant", "content": "Нашёл одного водителя."}])
    monkeypatch.setattr(gigachat_module.requests, "post", fake.post)
    return fake


@pytest.fixture
def client(monkeypatch, transport):
    """Настоящий GigaChatClient с подменённым транспортом: (клиент, транспорт)."""
    real_client = gigachat_module.GigaChatClient(
        auth_key="synthetic-key", ca_bundle=""
    )
    monkeypatch.setattr(real_client, "_get_token", lambda force=False: "TOKEN")
    return real_client, transport


class StubRepository:
    """История диалога без базы."""

    def append(self, session_id, role, content, tool_name=None):
        return 1

    def load(self, session_id, limit):
        return []

    def clear_session(self, session_id):
        return 0

    def purge_other_sessions(self, session_id):
        return 0


def make_agent(client, max_tool_steps=5, registry=None):
    """Агент с настоящим реестром инструментов и памятью без базы."""
    return Agent(
        client=client,
        memory=AgentMemory(repository=StubRepository(), autoload=False),
        registry=registry,
        max_tool_steps=max_tool_steps,
    )


def spy_tool(calls, name="search_driver", result=None):
    """
    Инструмент-«шпион»: записывает аргументы и отдаёт заданный результат.

    :param calls: список, куда складываются аргументы вызовов;
    :param result: что вернуть (по умолчанию — один найденный водитель).
    """
    def function(query: str = "", **_):
        """Подставной поиск: реальная база тесту не нужна."""
        calls.append(query)
        if result is not None:
            return result
        return {
            "ok": True,
            "data": [{"id": 1, "full_name": "Иванов Иван Иванович"}],
            "count": 1,
        }

    return Tool(
        name=name,
        description="Найти водителя в справочнике",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        function=function,
    )


def replace_tool(registry, tool):
    """Заменяет инструмент в реестре инструментом-шпионом."""
    registry.register(tool)
    return registry


# ─────────────────────────────────────────────────────────────
# 1. Описания инструментов → functions
# ─────────────────────────────────────────────────────────────

def test_tools_are_converted_to_functions():
    """OpenAI-вид инструментов превращается в legacy ``functions``."""
    tools = [{
        "type": "function",
        "function": {
            "name": "search_driver",
            "description": "Найти водителя",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }]

    functions = _functions_for_wire(tools)

    assert len(functions) == 1
    function = functions[0]
    assert function["name"] == "search_driver"
    assert function["description"] == "Найти водителя"
    assert function["parameters"]["type"] == "object"
    assert "additionalProperties" not in function["parameters"], (
        "служебный ключ схемы остался — лишний повод для молчаливого отказа"
    )


def test_functions_conversion_accepts_plain_shape():
    """Готовый вид ``{name, parameters}`` принимается без обёртки."""
    functions = _functions_for_wire([
        {"name": "get_help", "parameters": {}},
        {"description": "без имени"},
        "не словарь",
    ])

    assert [item["name"] for item in functions] == ["get_help"]
    assert functions[0]["parameters"] == {"type": "object", "properties": {}}


def test_functions_conversion_inlines_refs():
    """Ссылки ``$ref`` раскрываются: legacy-формат схемы их не понимает."""
    tools = [{
        "type": "function",
        "function": {
            "name": "fill",
            "description": "Заполнить",
            "parameters": {
                "type": "object",
                "properties": {"value": {"$ref": "#/$defs/Text"}},
                "$defs": {"Text": {"type": "string"}},
            },
        },
    }]

    parameters = _functions_for_wire(tools)[0]["parameters"]

    assert parameters["properties"]["value"] == {"type": "string"}
    assert "$defs" not in parameters


# ─────────────────────────────────────────────────────────────
# 2. История → сообщения провода
# ─────────────────────────────────────────────────────────────

def test_assistant_tool_calls_become_function_call():
    """``tool_calls`` (OpenAI) → ``function_call`` (GigaChat)."""
    messages = [
        {"role": "user", "content": "найди Иванова"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "search_driver",
                             "arguments": '{"query": "Иванов"}'},
            }],
        },
    ]

    assistant = _messages_for_wire(messages)[-1]

    assert "tool_calls" not in assistant
    assert assistant["function_call"]["name"] == "search_driver"
    assert assistant["function_call"]["arguments"] == {"query": "Иванов"}, (
        "аргументы обязаны уходить ОБЪЕКТОМ: строка JSON даёт 400"
    )


def test_tool_result_becomes_function_role():
    """Результат инструмента уходит ролью ``function`` со строкой внутри."""
    messages = [{
        "role": "tool",
        "tool_name": "search_driver",
        "tool_call_id": "call-1",
        "content": {"ok": True, "data": []},
    }]

    wire = _messages_for_wire(messages)

    assert wire[0]["role"] == "function"
    assert wire[0]["name"] == "search_driver"
    assert isinstance(wire[0]["content"], str), (
        "content объектом даёт 400 «invalid JSON syntax»"
    )
    assert json.loads(wire[0]["content"])["ok"] is True
    assert "tool_call_id" not in wire[0]


def test_broken_arguments_do_not_break_the_wire():
    """Неразобранные аргументы не роняют запрос — уходят пустым объектом."""
    messages = [{
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {"name": "search_driver", "arguments": "{не json"},
        }],
    }]

    assert _messages_for_wire(messages)[0]["function_call"]["arguments"] == {}


# ─────────────────────────────────────────────────────────────
# 3. Ответ модели → tool_calls
# ─────────────────────────────────────────────────────────────

def test_function_call_becomes_tool_calls():
    """``function_call`` из ответа нормализуется в OpenAI-вид."""
    calls = _tool_calls_from_message(_tool_call_message())

    assert len(calls) == 1
    assert calls[0]["id"] == "call-1"
    assert calls[0]["function"]["name"] == "search_driver"
    assert calls[0]["function"]["arguments"] == '{"query": "Иванов"}'


def test_existing_tool_calls_are_passed_through():
    """Если модель ответила по-новому (``tool_calls``), ответ не портится."""
    original = [{"id": "x", "type": "function",
                 "function": {"name": "get_help", "arguments": "{}"}}]

    assert _tool_calls_from_message({"tool_calls": original}) == original


def test_plain_text_answer_has_no_calls():
    """Обычный текстовый ответ не превращается в вызов."""
    assert _tool_calls_from_message({"content": "привет"}) == []


# ─────────────────────────────────────────────────────────────
# 4. Полный путь: клиент → агент → инструмент → ответ
# ─────────────────────────────────────────────────────────────

def test_client_sends_functions_not_tools(client):
    """
    Клиент отправляет ``functions`` и НЕ отправляет ``tools``.

    Это и есть регресс: пока уходило ``tools``, модель не вызывала
    инструменты вовсе.
    """
    real_client, _fake = client

    real_client.complete_chat(
        messages=[{"role": "user", "content": "найди водителя Иванова"}],
        tools=[{
            "type": "function",
            "function": {
                "name": "search_driver",
                "description": "Найти водителя",
                "parameters": {"type": "object",
                               "properties": {"query": {"type": "string"}},
                               "required": ["query"]},
            },
        }],
    )

    payload = _fake.last
    assert payload["functions"], "описания функций не ушли"
    assert payload["function_call"] == "auto"
    assert "tools" not in payload, "поле tools этот endpoint игнорирует"
    assert "tool_choice" not in payload, "tool_choice здесь не работает"


def test_client_normalizes_function_call_in_answer(client):
    """Ответ клиента всегда содержит ``tool_calls`` — в удобном виде."""
    real_client, _fake = client

    message = real_client.complete_chat(
        messages=[{"role": "user", "content": "найди водителя Иванова"}]
    )

    assert message["tool_calls"], "вызов функции не нормализован"
    assert message["tool_calls"][0]["function"]["name"] == "search_driver"


def test_agent_calls_tool_and_answers(client):
    """
    Полный цикл агента: модель просит инструмент, агент его выполняет,
    результат уходит обратно, модель отвечает текстом.
    """
    real_client, fake = client
    calls = []
    registry = replace_tool(build_registry(), spy_tool(calls))
    agent = make_agent(real_client, registry=registry)

    reply = agent.send("найди водителя Иванова")

    assert calls == ["Иванов"], "инструмент не вызван или вызван не с тем аргументом"
    assert reply.tool_names == ["search_driver"]
    assert reply.text == "Нашёл одного водителя."
    assert reply.steps == 2

    # Второй запрос обязан нести результат инструмента ролью function.
    second = fake.payloads[1]["messages"]
    assert second[-1]["role"] == "function"
    assert second[-1]["name"] == "search_driver"
    # Значение ФИО уходит в модель ЗАМАСКИРОВАННЫМ (<<PERSON_1>>): данные
    # оператора наружу не отдаются. Оригинал восстанавливается уже в ответе.
    assert "count" in second[-1]["content"]
    assert "PERSON_1" in second[-1]["content"], "ПДн ушли в модель без маски"
    # И вызов ассистента — в legacy-виде, иначе GigaChat отвергнет запрос.
    assert second[-2]["function_call"]["name"] == "search_driver"


def test_agent_loop_keeps_call_history(client):
    """История диалога хранит вызов инструмента — её видно в памяти."""
    real_client, _fake = client
    calls = []
    registry = replace_tool(
        build_registry(), spy_tool(calls, result={"ok": True, "data": [], "count": 0})
    )
    agent = make_agent(real_client, registry=registry)

    agent.send("найди водителя Иванова")

    stored = [item for item in agent.memory.messages
              if item.role == "assistant" and item.tool_calls]

    assert stored, "вызов инструмента не попал в историю"
    assert stored[0].tool_calls[0]["function"]["name"] == "search_driver"


def test_repeated_tool_steps_stop_at_limit(monkeypatch):
    """
    Модель, которая зовёт инструмент бесконечно, останавливается по лимиту.

    Живой случай: инструмент возвращает пустой список, и модель просит
    поискать ещё раз. Оператор не должен ждать вечно.
    """
    fake = FakeTransport([
        _tool_call_message(name="get_help", arguments={}, call_id=f"call-{index}")
        for index in range(10)
    ])
    monkeypatch.setattr(gigachat_module.requests, "post", fake.post)

    real_client = gigachat_module.GigaChatClient(
        auth_key="synthetic-key", ca_bundle=""
    )
    monkeypatch.setattr(real_client, "_get_token", lambda force=False: "TOKEN")

    agent = make_agent(real_client, max_tool_steps=3)
    reply = agent.send("помоги")

    assert reply.step_limit_reached is True
    assert len(reply.calls) == 3
    assert "лимит" in reply.text.lower()
