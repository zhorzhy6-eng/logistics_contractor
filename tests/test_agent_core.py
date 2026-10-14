#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ядро Максимилиана: цикл, память, образы, маскирование (задание, часть H.2).

Что проверяется
---------------
1. **Цикл агента с мок-GigaChat:** простой текстовый ответ; один вызов
   инструмента; два вызова подряд; отказ от инструмента на простом вопросе;
   лимит шагов (не больше ``max_tool_steps``).
2. **Подтверждения.** Опасный инструмент не выполняется без «да»; отказ
   оператора возвращается модели как «отменено», а форма не меняется.
3. **Память.** Сохраняется и загружается из базы; лимит сообщений
   соблюдается; «висячий» результат инструмента не уходит в модель.
4. **Образы.** Тон образа попадает в системный промпт; неизвестный ключ
   даёт образ по умолчанию.
5. **Промпт.** Инструменты и лимит шагов подставляются; шаблон читается из
   файла, а не из кода.
6. **Маскирование.** В GigaChat персональные данные уходят плейсхолдерами,
   в ответе оператору оригиналы восстанавливаются.

Реальных запросов нет: вместо клиента используется ``MockChat`` —
он отдаёт заранее заготовленные ответы и записывает то, что получил.
"""

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.agent import Agent, AgentContext  # noqa: E402
from core.agent.llm import SafeChat  # noqa: E402
from core.agent.memory import AgentMemory, Message  # noqa: E402
from core.agent.personas import (  # noqa: E402
    DEFAULT_PERSONA, PERSONAS, persona, persona_keys,
)
from core.agent.prompt import build_system_prompt, load_prompt_template  # noqa: E402
from core.agent.tools import Tool, ToolRegistry  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Мок GigaChat
# ─────────────────────────────────────────────────────────────

def text_reply(content: str) -> dict:
    """Ответ модели текстом."""
    return {"role": "assistant", "content": content, "tool_calls": []}


def tool_reply(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    """Ответ модели с вызовом инструмента."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments, ensure_ascii=False)},
        }],
    }


class MockChat:
    """
    Подставной клиент GigaChat.

    Отдаёт ответы по очереди и запоминает КАЖДЫЙ запрос: тесты проверяют
    не только результат, но и то, что ушло наружу (маскирование).
    """

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.requests.append({"messages": [dict(m) for m in messages],
                              "tools": list(tools or ())})
        if not self.replies:
            return text_reply("Ответы закончились")
        return self.replies.pop(0)


class EchoPlaceholderChat(MockChat):
    """
    Мок, который возвращает первую маску, найденную в запросе.

    Так проверяется восстановление оригиналов: агент обязан вернуть
    оператору то, что было закрыто плейсхолдером.
    """

    def __init__(self):
        super().__init__([])
        self.placeholder = ""

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.requests.append({"messages": [dict(m) for m in messages],
                              "tools": list(tools or ())})
        if not self.placeholder:
            import re
            found = re.search(r"<<[A-Z_]+_\d+>>",
                              str(messages[-1].get("content") or ""))
            self.placeholder = found.group(0) if found else ""
        return text_reply(f"Нашёл: {self.placeholder}" if self.placeholder
                          else "Ничего не нашёл")


# ─────────────────────────────────────────────────────────────
# Заглушки окна и инструментов
# ─────────────────────────────────────────────────────────────

class StubTab:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get_data(self):
        return dict(self.data)

    def fill_data(self, values=None, *args):
        self.data.update(values or {})


class StubWindow:
    CONTRACT_TYPE = "perevozka"

    def __init__(self):
        self.driver_tab = StubTab({"full_name": "Иванов Иван Иванович",
                                   "phone": "+7 900 111-22-33"})
        self.carrier_tab = StubTab({"full_name": "ООО «Ромашка»"})
        self.customer_tab = StubTab({})
        self.trailer_tab = StubTab({})
        self.vehicles_tab = StubTab({})
        self.contract_tab = StubTab({"number": "23092026-74"})

    def _tab_keys(self):
        return ("driver_tab", "carrier_tab", "customer_tab",
                "trailer_tab", "vehicles_tab", "contract_tab")

    def _tab_by_key(self, key):
        return getattr(self, key, None)


class ConfirmationRecorder:
    """Запоминает вопросы подтверждения и отвечает «да» или «нет»."""

    def __init__(self, answer: bool = True):
        self.answer = answer
        self.asked = []

    def __call__(self, tool, arguments):
        self.asked.append((tool.name, dict(arguments)))
        return self.answer


def make_registry(calls_log=None, fill_window=None):
    """Небольшой реестр: безопасный инструмент и опасный (заполнение)."""
    registry = ToolRegistry()

    def read_something(query: str = ""):
        if calls_log is not None:
            calls_log.append(("read_something", query))
        return {"ok": True, "data": [{"id": 1, "name": "Иванов"}], "count": 1}

    def write_something(field: str = "", value: str = ""):
        if calls_log is not None:
            calls_log.append(("write_something", field, value))
        if fill_window is not None:
            fill_window.driver_tab.data[field] = value
        return {"ok": True, "data": {"field": field}}

    registry.register(Tool(
        name="read_something",
        description="Прочитать что-нибудь из базы",
        parameters={"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]},
        function=read_something,
        summary="Чтение данных",
    ))
    registry.register(Tool(
        name="write_something",
        description="Записать значение в форму",
        parameters={"type": "object", "properties": {
            "field": {"type": "string"}, "value": {"type": "string"}},
            "required": ["field", "value"]},
        function=write_something,
        requires_confirmation=True,
        summary="Заполнить поле формы",
        hide_value_args=("value",),
    ))
    return registry


# ─────────────────────────────────────────────────────────────
# 1. Цикл агента
# ─────────────────────────────────────────────────────────────

def test_simple_text_answer():
    """Простой вопрос — один запрос к модели, без инструментов."""
    chat = MockChat([text_reply("ИНН — это номер налогоплательщика.")])
    memory = AgentMemory(repository=FakeRepository(), autoload=False)
    agent = Agent(client=chat, memory=memory, registry=make_registry())

    reply = agent.send("что такое ИНН?")

    assert reply.text == "ИНН — это номер налогоплательщика."
    assert reply.calls == []
    assert reply.steps == 1
    assert len(chat.requests) == 1
    # Запрос ушёл с системным промптом и описаниями инструментов.
    first = chat.requests[0]
    assert first["messages"][0]["role"] == "system"
    assert "Максимилиан" in first["messages"][0]["content"]
    assert [tool["function"]["name"] for tool in first["tools"]] == [
        "read_something", "write_something"]


def test_one_tool_call_then_answer():
    """Один вызов инструмента, затем текстовый ответ."""
    calls = []
    chat = MockChat([
        tool_reply("read_something", {"query": "Иванов"}),
        text_reply("Нашёл одного водителя: Иванов."),
    ])
    agent = Agent(client=chat, memory=AgentMemory(repository=FakeRepository(),
                                                  autoload=False),
                  registry=make_registry(calls_log=calls))

    reply = agent.send("найди Иванова")

    assert calls == [("read_something", "Иванов")]
    assert reply.tool_names == ["read_something"]
    assert "Иванов" in reply.text
    assert reply.steps == 2
    assert len(chat.requests) == 2
    # Результат инструмента ушёл в модель отдельным сообщением роли tool.
    tool_messages = [m for m in chat.requests[1]["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["name"] == "read_something"
    assert json.loads(tool_messages[0]["content"])["ok"] is True


def test_two_tool_calls_in_a_row():
    """Два вызова инструментов подряд — тоже штатный путь."""
    calls = []
    chat = MockChat([
        tool_reply("read_something", {"query": "Иванов"}, call_id="c1"),
        tool_reply("read_something", {"query": "Петров"}, call_id="c2"),
        text_reply("Нашёл обоих."),
    ])
    agent = Agent(client=chat, memory=AgentMemory(repository=FakeRepository(),
                                                  autoload=False),
                  registry=make_registry(calls_log=calls))

    reply = agent.send("найди Иванова и Петрова")

    assert calls == [("read_something", "Иванов"), ("read_something", "Петров")]
    assert reply.tool_names == ["read_something", "read_something"]
    assert reply.steps == 3


def test_tool_call_reports_missing_tool_to_model():
    """Модель попросила несуществующий инструмент — она получает ошибку."""
    chat = MockChat([
        tool_reply("teleport", {}),
        text_reply("Такого не умею."),
    ])
    agent = Agent(client=chat, memory=AgentMemory(repository=FakeRepository(),
                                                  autoload=False),
                  registry=make_registry())

    reply = agent.send("телепортируй меня")

    assert reply.text == "Такого не умею."
    result = json.loads([
        m for m in chat.requests[1]["messages"] if m["role"] == "tool"
    ][0]["content"])
    assert result["ok"] is False
    assert "не существует" in result["error"]


def test_broken_arguments_do_not_crash_agent():
    """Невалидный JSON в аргументах — ошибка для модели, не падение."""
    chat = MockChat([
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "read_something", "arguments": "{это не json"},
        }]},
        text_reply("Не разобрал аргументы."),
    ])
    agent = Agent(client=chat, memory=AgentMemory(repository=FakeRepository(),
                                                  autoload=False),
                  registry=make_registry())

    reply = agent.send("сломай аргументы")

    assert reply.text == "Не разобрал аргументы."
    result = json.loads([
        m for m in chat.requests[1]["messages"] if m["role"] == "tool"
    ][0]["content"])
    assert result["ok"] is False
    assert "не разобраны" in result["error"]


def test_step_limit_stops_tool_calls():
    """Лимит шагов: больше max_tool_steps вызовов не делается."""
    calls = []
    chat = MockChat([tool_reply("read_something", {"query": f"q{index}"},
                                call_id=f"c{index}") for index in range(10)])
    agent = Agent(client=chat, memory=AgentMemory(repository=FakeRepository(),
                                                  autoload=False),
                  registry=make_registry(calls_log=calls), max_tool_steps=3)

    reply = agent.send("ищи, пока не найдёшь")

    assert reply.step_limit_reached is True
    assert len(calls) == 3, "вызовов должно быть ровно по лимиту"
    assert "лимит" in reply.text.lower() or "лимита" in reply.text.lower()
    # Модель дёрнули ровно три раза: четвёртого запроса нет.
    assert len(chat.requests) == 3


def test_empty_message_does_not_reach_model():
    """
    Пустое сообщение не уходит в модель, а получает понятную подсказку.

    Формулировка совпадает с подсказкой панели (AgentPanel.EMPTY_INPUT_HINT):
    оператор видит один и тот же текст, откуда бы ни пришёл пустой запрос.
    """
    chat = MockChat([text_reply("не должно случиться")])
    agent = Agent(client=chat, memory=AgentMemory(repository=FakeRepository(),
                                                  autoload=False),
                  registry=make_registry())

    reply = agent.send("   ")

    assert chat.requests == []
    assert reply.text == "Введите запрос."


def test_agent_without_client_reports_clearly():
    """Без клиента агент отвечает понятной фразой, а не падает."""
    agent = Agent(client=None, memory=AgentMemory(repository=FakeRepository(),
                                                  autoload=False),
                  registry=make_registry())
    reply = agent.send("привет")
    assert "GigaChat" in reply.text


# ─────────────────────────────────────────────────────────────
# 2. Подтверждения
# ─────────────────────────────────────────────────────────────

def test_dangerous_tool_runs_after_confirmation():
    """С подтверждением опасный инструмент выполняется."""
    window = StubWindow()
    calls = []
    confirm = ConfirmationRecorder(answer=True)
    chat = MockChat([
        tool_reply("write_something", {"field": "full_name", "value": "Петров П.П."}),
        text_reply("Записал."),
    ])
    context = AgentContext(window=window, confirm=confirm)
    agent = Agent(client=chat, context=context,
                  memory=AgentMemory(repository=FakeRepository(), autoload=False),
                  registry=make_registry(calls_log=calls, fill_window=window))

    reply = agent.send("заполни водителя")

    assert len(confirm.asked) == 1
    assert confirm.asked[0][0] == "write_something"
    assert calls == [("write_something", "full_name", "Петров П.П.")]
    assert window.driver_tab.data["full_name"] == "Петров П.П."
    assert reply.text == "Записал."


def test_dangerous_tool_declined_is_not_executed():
    """Отказ оператора: инструмент не выполняется, модель узнаёт об отказе."""
    window = StubWindow()
    calls = []
    confirm = ConfirmationRecorder(answer=False)
    chat = MockChat([
        tool_reply("write_something", {"field": "full_name", "value": "Петров П.П."}),
        text_reply("Хорошо, не записываю."),
    ])
    context = AgentContext(window=window, confirm=confirm)
    agent = Agent(client=chat, context=context,
                  memory=AgentMemory(repository=FakeRepository(), autoload=False),
                  registry=make_registry(calls_log=calls, fill_window=window))

    reply = agent.send("заполни водителя")

    assert calls == [], "инструмент не должен выполняться"
    assert window.driver_tab.data["full_name"] == "Иванов Иван Иванович"
    result = json.loads([
        m for m in chat.requests[1]["messages"] if m["role"] == "tool"
    ][0]["content"])
    assert result["declined"] is True
    assert "отмен" in result["error"]
    assert reply.calls[0].declined is True


def test_dangerous_tool_without_dialog_is_denied():
    """Нет диалога подтверждения — опасное действие запрещено."""
    window = StubWindow()
    calls = []
    chat = MockChat([
        tool_reply("write_something", {"field": "full_name", "value": "Петров"}),
        text_reply("Не могу без подтверждения."),
    ])
    agent = Agent(client=chat, context=AgentContext(window=window, confirm=None),
                  memory=AgentMemory(repository=FakeRepository(), autoload=False),
                  registry=make_registry(calls_log=calls, fill_window=window))

    agent.send("заполни")

    assert calls == []


def test_read_tool_does_not_ask_confirmation():
    """Чтение не спрашивает подтверждения."""
    confirm = ConfirmationRecorder(answer=False)
    chat = MockChat([
        tool_reply("read_something", {"query": "Иванов"}),
        text_reply("Нашёл."),
    ])
    agent = Agent(client=chat, context=AgentContext(confirm=confirm),
                  memory=AgentMemory(repository=FakeRepository(), autoload=False),
                  registry=make_registry())

    agent.send("найди Иванова")

    assert confirm.asked == []


# ─────────────────────────────────────────────────────────────
# 3. Память
# ─────────────────────────────────────────────────────────────

class FakeRepository:
    """Подставная история: та же работа, что у таблицы agent_history."""

    def __init__(self):
        self.rows = []
        self.cleared = []
        self.purged = 0

    def append(self, session_id, role, content, tool_name=None):
        self.rows.append({"id": len(self.rows) + 1, "session_id": session_id,
                          "role": role, "content": content,
                          "tool_name": tool_name})
        return len(self.rows)

    def load(self, session_id, limit=20):
        rows = [row for row in self.rows if row["session_id"] == session_id]
        return rows[-limit:]

    def clear_session(self, session_id):
        before = len(self.rows)
        self.rows = [row for row in self.rows if row["session_id"] != session_id]
        self.cleared.append(session_id)
        return before - len(self.rows)

    def purge_other_sessions(self, keep_session_id):
        before = len(self.rows)
        self.rows = [row for row in self.rows if row["session_id"] == keep_session_id]
        self.purged += 1
        return before - len(self.rows)


def test_memory_saves_and_loads():
    """История сохраняется и читается обратно (та же сессия)."""
    repository = FakeRepository()
    memory = AgentMemory(session_id="s1", repository=repository, autoload=False)
    memory.add("user", "привет")
    memory.add("assistant", "здравствуйте")

    restored = AgentMemory(session_id="s1", repository=repository)

    assert [message.role for message in restored.messages] == ["user", "assistant"]
    assert restored.messages[0].content == "привет"


def test_memory_keeps_only_last_messages():
    """В памяти остаются последние max_history сообщений."""
    memory = AgentMemory(session_id="s2", max_history=3,
                         repository=FakeRepository(), autoload=False)
    for index in range(6):
        memory.add("user", f"сообщение {index}")

    assert len(memory.messages) == 3
    assert memory.messages[0].content == "сообщение 3"


def test_memory_clears_current_session():
    """Очистка истории убирает записи только текущей сессии."""
    repository = FakeRepository()
    first = AgentMemory(session_id="a", repository=repository, autoload=False)
    second = AgentMemory(session_id="b", repository=repository, autoload=False)
    first.add("user", "один")
    second.add("user", "два")

    removed = first.clear()

    assert removed == 1
    assert first.messages == []
    assert len(second.messages) == 1


def test_memory_purges_other_sessions():
    """Чистка прошлых сессий не трогает текущую."""
    repository = FakeRepository()
    old = AgentMemory(session_id="old", repository=repository, autoload=False)
    current = AgentMemory(session_id="new", repository=repository, autoload=False)
    old.add("user", "старое")
    current.add("user", "новое")

    removed = current.purge_other_sessions()

    assert removed == 1
    assert len(current.messages) == 1


def test_wire_messages_drops_dangling_tool_result():
    """Результат инструмента без вызова в модель не уходит."""
    memory = AgentMemory(session_id="s3", max_history=20,
                         repository=FakeRepository(), autoload=False)
    memory.add("tool", '{"ok": true}', tool_name="read_something",
               tool_call_id="c1")
    memory.add("user", "привет")

    wire = memory.wire_messages("промпт")

    assert [item["role"] for item in wire] == ["system", "user"]


def test_wire_messages_drops_incomplete_tool_block():
    """Вызов инструмента без результата тоже не уходит (обрыв диалога)."""
    memory = AgentMemory(session_id="s4", repository=FakeRepository(), autoload=False)
    memory.add("user", "вопрос")
    memory.add("assistant", "", tool_calls=[{
        "id": "c1", "type": "function",
        "function": {"name": "read_something", "arguments": "{}"}}])

    wire = memory.wire_messages("")

    assert [item["role"] for item in wire] == ["user"]


def test_wire_messages_keep_complete_tool_block():
    """Полный блок «вызов + результат» уходит целиком."""
    memory = AgentMemory(session_id="s5", repository=FakeRepository(), autoload=False)
    memory.add("user", "вопрос")
    memory.add("assistant", "", tool_calls=[{
        "id": "c1", "type": "function",
        "function": {"name": "read_something", "arguments": "{}"}}])
    memory.add("tool", '{"ok": true}', tool_name="read_something", tool_call_id="c1")

    wire = memory.wire_messages("")

    assert [item["role"] for item in wire] == ["user", "assistant", "tool"]


def test_broken_repository_does_not_break_dialog():
    """Ошибка записи в базу не мешает диалогу (история живёт в памяти)."""

    class BrokenRepository:
        def append(self, *args, **kwargs):
            raise RuntimeError("база недоступна")

        def load(self, *args, **kwargs):
            raise RuntimeError("база недоступна")

        def clear_session(self, *args, **kwargs):
            raise RuntimeError("база недоступна")

        def purge_other_sessions(self, *args, **kwargs):
            raise RuntimeError("база недоступна")

    memory = AgentMemory(session_id="s6", repository=BrokenRepository())
    memory.add("user", "привет")

    assert len(memory.messages) == 1
    assert memory.clear() == -1


# ─────────────────────────────────────────────────────────────
# 4. Образы и промпт
# ─────────────────────────────────────────────────────────────

def test_personas_are_five_and_have_tone():
    """Пять образов, у каждого свой тон и описание."""
    assert set(PERSONAS) == {"cat_scientist", "cat_logist", "cat_detective",
                             "cat_keeper", "neutral"}
    for key, item in PERSONAS.items():
        assert item.tone.strip(), key
        assert item.description.strip(), key
        assert item.prompt_block(), key


def test_persona_keys_and_avatar_keys_match():
    """
    У каждого образа есть свой рисунок — и наоборот.

    Qt здесь не поднимаем: сверяются только списки ключей (рисунок образа
    проверяет tests/test_agent_dialogs.py). Наборы обязаны совпадать: образ
    из настроек, для которого нет рисунка, молча показал бы учёного кота,
    а «висячий» ключ рисования было бы невозможно выбрать в списке.
    """
    from ui.agent_avatar import avatar_keys

    assert set(avatar_keys()) == set(persona_keys())


def test_persona_falls_back_to_default():
    """Неизвестный образ — образ по умолчанию, без исключения."""
    assert persona("кот-космонавт").key == DEFAULT_PERSONA
    assert persona("").key == DEFAULT_PERSONA


def test_persona_tone_reaches_prompt():
    """Тон выбранного образа попадает в системный промпт."""
    for key in PERSONAS:
        prompt = build_system_prompt(tools=[("read_something", "чтение")],
                                     persona_key=key)
        assert PERSONAS[key].samples[0] in prompt if PERSONAS[key].samples else True
        assert "инструменты" in prompt.lower()


def test_prompt_file_is_used_and_formatted():
    """Промпт читается ИЗ ФАЙЛА и подставляет инструменты и лимит шагов."""
    template = load_prompt_template()
    assert "{tools}" in template, "шаблон должен содержать подстановку"
    assert "Максимилиан" in template

    prompt = build_system_prompt(tools=[("search_driver", "поиск водителя")],
                                 max_tool_steps=7)
    assert "search_driver — поиск водителя" in prompt
    assert "7" in prompt


def test_broken_prompt_template_does_not_crash():
    """Битый шаблон не ломает промпт: образ и инструменты допишутся."""
    broken = "Инструкция {unknown_placeholder} без нужных подстановок"
    prompt = build_system_prompt(tools=[("get_help", "справка")], path=None)
    assert prompt

    # Прямая проверка устойчивости: шаблон с чужой скобкой.
    from core.agent import prompt as prompt_module

    original = prompt_module.load_prompt_template
    prompt_module.load_prompt_template = lambda path=None: broken
    try:
        result = build_system_prompt(tools=[("get_help", "справка")])
    finally:
        prompt_module.load_prompt_template = original
    assert "get_help — справка" in result


def test_missing_prompt_file_uses_fallback():
    """Потерянный файл промпта — запасной текст, а не пустая строка."""
    template = load_prompt_template("нет-такого-файла.txt")
    assert "Максимилиан" in template


# ─────────────────────────────────────────────────────────────
# 5. Маскирование (задание, часть G.3)
# ─────────────────────────────────────────────────────────────

def test_personal_data_is_masked_before_sending():
    """Персональные данные уходят в модель плейсхолдерами, а не текстом."""
    chat = MockChat([text_reply("Понял.")])
    agent = Agent(client=chat, memory=AgentMemory(repository=FakeRepository(),
                                                  autoload=False),
                  registry=make_registry())

    agent.send("Найди Иванова Ивана Ивановича, телефон +7 900 111-22-33")

    sent = chat.requests[0]["messages"][-1]["content"]
    assert "<<" in sent, "в тексте должны быть плейсхолдеры"
    assert "111-22-33" not in sent, "телефон должен быть закрыт"
    assert "900" not in sent


def test_placeholders_are_restored_in_answer():
    """
    Плейсхолдер из запроса восстанавливается в ответе оператору.

    Мок возвращает ТУ ЖЕ маску, которую получил: так проверяется именно
    связка «маска ушла наружу — оригинал вернулся оператору», без опоры на
    то, какие сущности найдёт детектор в конкретной фразе.
    """
    chat = EchoPlaceholderChat()
    agent = Agent(client=chat, memory=AgentMemory(repository=FakeRepository(),
                                                  autoload=False),
                  registry=make_registry())

    reply = agent.send("Найди Иванова Ивана Ивановича, телефон +7 900 111-22-33")

    assert chat.placeholder, "маска должна была уйти в модель"
    assert chat.placeholder not in reply.text, "плейсхолдер остался в ответе"
    assert "+7 900 111-22-33" in reply.text, "оригинал должен вернуться"
    # В истории диалога тоже оригинал: оператор видит свои данные.
    assert "+7 900 111-22-33" in agent.memory.messages[-1].content


def test_tool_result_is_masked_before_sending():
    """Результат инструмента тоже обезличивается: в модель уходит маска."""
    chat = MockChat([
        tool_reply("read_something", {"query": "Иванов"}),
        text_reply("Готово."),
    ])
    agent = Agent(client=chat, memory=AgentMemory(repository=FakeRepository(),
                                                  autoload=False),
                  registry=make_registry())

    agent.send("найди Иванова Ивана Ивановича")

    # Второй запрос содержит историю целиком: в ней не должно быть ФИО
    # открытым текстом — ни в сообщении оператора, ни в результатах.
    second = chat.requests[1]["messages"]
    text = json.dumps(second, ensure_ascii=False)
    assert "Иванов Иван Иванович" not in text


def test_memory_keeps_real_values_for_operator():
    """В памяти диалога значения настоящие: оператор должен их видеть."""
    repository = FakeRepository()
    chat = MockChat([text_reply("Готово.")])
    memory = AgentMemory(repository=repository, autoload=False)
    agent = Agent(client=chat, memory=memory, registry=make_registry())

    agent.send("Найди Иванова Ивана Ивановича")

    assert memory.messages[0].content == "Найди Иванова Ивана Ивановича"
    # А в модель ушла маска (проверено выше) — здесь важен контраст.
    assert "Иванов Иван Иванович" not in json.dumps(
        chat.requests[0]["messages"], ensure_ascii=False)


def test_masking_mapping_is_forgotten_after_turn():
    """Маппинг живёт один запрос: после ответа восстанавливать нечего."""
    chat = MockChat([text_reply("Хорошо.")])
    agent = Agent(client=chat, memory=AgentMemory(repository=FakeRepository(),
                                                  autoload=False),
                  registry=make_registry())

    agent.send("Найди Иванова Ивана Ивановича")

    assert agent.chat.masked_count == 0


def test_safe_chat_parses_and_reports_bad_arguments():
    """Разбор вызовов инструментов: строка JSON, объект и мусор."""
    parsed = SafeChat.parse_tool_calls({
        "content": "", "tool_calls": [
            {"id": "a", "function": {"name": "f", "arguments": '{"x": 1}'}},
            {"id": "b", "function": {"name": "g", "arguments": {"y": 2}}},
            {"id": "c", "function": {"name": "h", "arguments": "не json"}},
        ]})
    assert parsed[0]["arguments"] == {"x": 1}
    assert parsed[1]["arguments"] == {"y": 2}
    assert parsed[2]["arguments"] == {}
    assert "не разобраны" in parsed[2]["error"]


def test_safe_chat_requires_client_message():
    """Если клиент вернул не сообщение — понятная ошибка, не падение."""

    class WeirdClient:
        def complete_chat(self, **kwargs):
            return "просто строка"

    chat = SafeChat(WeirdClient())
    with pytest.raises(RuntimeError):
        chat.complete([{"role": "user", "content": "привет"}])


# ─────────────────────────────────────────────────────────────
# 6. Прочее
# ─────────────────────────────────────────────────────────────

def test_real_windows_expose_tab_keys():
    """
    Настоящие окна сами рассказывают агенту про свою раскладку вкладок.

    `MainWindow` и `BaseContractWindow` дают ключи вкладок в одном виде —
    иначе инструменты формы читали бы раскладку по именам атрибутов, а они
    у каждого окна свои.
    """
    from ui.main_window import MainWindow
    from ui.windows.base_window import BaseContractWindow

    assert callable(getattr(MainWindow, "_tab_keys", None))
    assert callable(getattr(BaseContractWindow, "_tab_keys", None))

    keys = MainWindow._tab_keys(object())
    assert "driver_tab" in keys
    assert "contract_tab" in keys
    assert keys == tuple(keys)

    # Окно типа берёт раскладку из своего класса (наследника не создаём:
    # BaseContractWindow — QMainWindow, а тесты ядра идут без Qt).
    class Stub:
        _TAB_KEYS = ("customer_tab", "vehicle_tab")
        _tab_keys = BaseContractWindow._tab_keys

    assert Stub()._tab_keys() == ("customer_tab", "vehicle_tab")


def test_collect_form_data_reads_sections_by_tab_keys():
    """
    Чтение формы идёт по ключам вкладок окна, а не по именам атрибутов.

    Псевдо-окно отдаёт ключи явно: инструменты должны спросить их у окна
    и сложить разделы в словарь «ключ вкладки → поля».
    """
    from core.agent.tools import collect_form_data

    class Tab:
        def __init__(self, data):
            self.data = data

        def get_data(self):
            return dict(self.data)

    class Window:
        def __init__(self):
            self.tabs = {"driver_tab": Tab({"full_name": "Иванов И.И."}),
                         "contract_tab": Tab({"number": "1"})}

        def _tab_keys(self):
            return ("driver_tab", "contract_tab")

        def _tab_by_key(self, key):
            return self.tabs.get(key)

    sections = collect_form_data(Window())

    assert sections["driver_tab"]["full_name"] == "Иванов И.И."
    assert sections["contract_tab"]["number"] == "1"


def test_message_wire_format():
    """Формат сообщений для GigaChat: роли, инструменты, вызовы."""
    user = Message(role="user", content="привет")
    assert user.wire() == {"role": "user", "content": "привет"}

    assistant = Message(role="assistant", content="", tool_calls=[
        {"id": "c1", "type": "function",
         "function": {"name": "f", "arguments": "{}"}}])
    assert "tool_calls" in assistant.wire()

    tool = Message(role="tool", content="{}", tool_name="f", tool_call_id="c1")
    wire = tool.wire()
    assert wire["name"] == "f"
    assert wire["tool_call_id"] == "c1"


def test_agent_clear_history():
    """Очистка истории доступна агенту (кнопка в панели)."""
    repository = FakeRepository()
    agent = Agent(client=MockChat([]),
                  memory=AgentMemory(repository=repository, autoload=False),
                  registry=make_registry())
    agent.memory.add("user", "привет")

    assert agent.clear_history() == 1
    assert agent.memory.messages == []
