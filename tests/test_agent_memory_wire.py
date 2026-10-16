#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
История диалога → запрос к GigaChat: незавершённый блок инструментов.

НАЙДЕНО СТРЕСС-ТЕСТОМ (200 запросов, 15.10.2026) перебором историй
(`tests/_tmp/_stress_agent/_probe_wire_index.py`: 7431 история, 5 расхождений
на первом же проходе).

Что было. В `AgentMemory.wire_messages` начало блока инструментов хранилось
как индекс в СПИСКЕ ЗАПРОСА (`wire`), а проверка «все ли вызовы получили
ответ» брала по этому индексу сообщение из СПИСКА ИСТОРИИ
(``self._messages[block_start]``). Списки не совпадают: системный промпт
добавляет один элемент, «висячие» результаты инструментов пропускаются.
Проверялось ЧУЖОЕ сообщение — и незакрытый блок (ассистент с вызовом, на
который не пришёл результат) оставался в запросе, хотя сам же модуль
описывает такой запрос как недопустимый. Итог: следующий запрос оператора
уходил в GigaChat с недопустимой историей.

Здесь два вида проверок:

  * точечные случаи (что вырезается, что остаётся);
  * сверка с ЭТАЛОНОМ на переборе историй — чтобы правило не сломалось
    снова при следующей правке.
"""

import itertools
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

from core.agent.agent import Agent  # noqa: E402
from core.agent.memory import AgentMemory, Message  # noqa: E402
from core.agent.tools import AgentContext  # noqa: E402


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


def memory_with(messages):
    """Память с заданной историей (без базы и без загрузки)."""
    memory = AgentMemory(session_id="wire", repository=NullRepository(),
                         autoload=False)
    memory._messages = list(messages)
    return memory


def user(text="запрос"):
    return Message(role="user", content=text)


def assistant(call_id="", with_calls=True, content="текст"):
    calls = []
    if with_calls:
        calls = [{
            "id": call_id or "call_1",
            "type": "function",
            "function": {"name": "search_anything",
                         "arguments": json.dumps({"query": "Ромашка"},
                                                 ensure_ascii=False)},
        }]
    return Message(role="assistant", content=content, tool_calls=calls)


def tool(call_id, content='{"ok": true}'):
    return Message(role="tool", content=content, tool_name="search_anything",
                   tool_call_id=call_id)


def roles(wire):
    """Роли запроса без системного сообщения (оно есть всегда)."""
    return [item["role"] for item in wire if item.get("role") != "system"]


# ─────────────────────────────────────────────────────────────
# Точечные случаи
# ─────────────────────────────────────────────────────────────

def test_complete_block_is_kept():
    """Вызов и результат на месте — блок остаётся целиком."""
    wire = memory_with([user(), assistant("a1"), tool("a1")]).wire_messages("с")
    assert roles(wire) == ["user", "assistant", "tool"]


def test_unfinished_tail_is_dropped():
    """Ответа инструмента нет — блок вырезан: GigaChat такого не примет."""
    wire = memory_with([user(), assistant("a1")]).wire_messages("с")
    assert roles(wire) == ["user"]


def test_unfinished_block_before_new_request_is_dropped():
    """
    Незакрытый блок в СЕРЕДИНЕ истории тоже вырезается.

    Именно этот случай не срабатывал: проверка смотрела не на то сообщение,
    и блок уезжал в GigaChat перед новым запросом оператора.
    """
    wire = memory_with([
        user("первый"),
        assistant("a1"),          # вызов остался без результата
        user("второй"),
    ]).wire_messages("с")
    assert roles(wire) == ["user", "user"]


def test_result_for_another_call_is_not_an_answer():
    """Результат с чужим id ответом не считается — блок вырезан."""
    wire = memory_with([user(), assistant("a1"), tool("other")]).wire_messages("с")
    assert roles(wire) == ["user"]


def test_orphan_tool_result_is_skipped():
    """Результат без своего вызова (обрезанная история) в запрос не идёт."""
    wire = memory_with([tool("old"), user(), assistant("a1"),
                        tool("a1")]).wire_messages("с")
    assert roles(wire) == ["user", "assistant", "tool"]


def test_broken_block_in_the_middle_does_not_touch_good_ones():
    """Плохой блок вырезается, соседние хорошие остаются."""
    wire = memory_with([
        user("первый"),
        assistant("a1"), tool("a1"),
        user("второй"),
        assistant("a2"),          # без результата
        user("третий"),
        assistant("a3"), tool("a3"),
    ]).wire_messages("с")
    assert roles(wire) == ["user", "assistant", "tool", "user", "user",
                           "assistant", "tool"]


def test_two_calls_with_one_answer_are_not_enough():
    """Из двух вызовов ответил один — блок всё равно неполный."""
    calls = [
        {"id": "a1", "type": "function",
         "function": {"name": "search_anything", "arguments": "{}"}},
        {"id": "a2", "type": "function",
         "function": {"name": "count_records", "arguments": "{}"}},
    ]
    message = Message(role="assistant", content="", tool_calls=calls)
    wire = memory_with([user(), message, tool("a1")]).wire_messages("с")
    assert roles(wire) == ["user"]


def test_two_calls_with_two_answers_are_kept():
    """Ответили на оба вызова — блок полный."""
    calls = [
        {"id": "a1", "type": "function",
         "function": {"name": "search_anything", "arguments": "{}"}},
        {"id": "a2", "type": "function",
         "function": {"name": "count_records", "arguments": "{}"}},
    ]
    message = Message(role="assistant", content="", tool_calls=calls)
    wire = memory_with([user(), message, tool("a1"), tool("a2")]).wire_messages("с")
    assert roles(wire) == ["user", "assistant", "tool", "tool"]


def test_system_prompt_is_always_first():
    """Системный промпт идёт первым и не мешает вырезанию блока."""
    wire = memory_with([user(), assistant("a1")]).wire_messages("правила")
    assert wire[0] == {"role": "system", "content": "правила"}


def test_history_without_blocks_is_untouched():
    """Обычный диалог без инструментов проходит как есть."""
    wire = memory_with([user("привет"), assistant(with_calls=False)]).wire_messages("с")
    assert roles(wire) == ["user", "assistant"]


# ─────────────────────────────────────────────────────────────
# Сверка с эталоном на переборе историй
# ─────────────────────────────────────────────────────────────

def wire_reference(messages, system_prompt):
    """
    Эталон: то же правило, но блок отслеживается ПО САМОМУ сообщению.

    Отличие от проверяемого кода ровно одно и намеренное: здесь нет
    индекса из чужого списка.
    """
    wire = []
    block_assistant = None
    block_mark = 0
    answered = set()

    for message in messages:
        if block_assistant is not None and message.role != "tool":
            if not AgentMemory._all_answered(block_assistant, answered):
                del wire[block_mark:]
            block_assistant = None
            answered = set()

        if message.role == "assistant" and message.tool_calls:
            block_assistant = message
            block_mark = len(wire)
            answered = set()
        elif message.role == "tool" and block_assistant is None:
            continue

        if message.role == "tool" and message.tool_call_id:
            answered.add(message.tool_call_id)

        wire.append(message.wire())

    if block_assistant is not None and not AgentMemory._all_answered(
            block_assistant, answered):
        del wire[block_mark:]
    return wire


#: Кусочки историй для перебора: закрытые блоки, открытые, «висячие» ответы.
PIECES = (
    [user()],
    [assistant("a1"), tool("a1")],
    [assistant("a2"), tool("a2"), assistant(with_calls=False)],
    [tool("old")],
    [assistant("a3")],
    [assistant("a3"), tool("other")],
)


def test_wire_matches_reference_on_every_history():
    """
    Перебор историй: провод агента обязан совпадать с эталоном.

    Проверка ловит именно тот класс ошибок, из-за которого правка и
    делалась: расхождение индексов двух разных списков.
    """
    checked = 0
    for length in (2, 3, 4):
        for combination in itertools.product(PIECES, repeat=length):
            messages = [message for piece in combination for message in piece]
            if len(messages) > 9:
                continue
            checked += 1
            actual = memory_with(messages).wire_messages("правила")
            expected = [{"role": "system", "content": "правила"}
                        ] + wire_reference(messages, "правила")
            assert actual == expected, (
                f"история: {[(m.role, m.tool_call_id) for m in messages]}\n"
                f"агент : {[(m['role'], m.get('tool_call_id')) for m in actual]}\n"
                f"эталон: {[(m['role'], m.get('tool_call_id')) for m in expected]}"
            )
    assert checked > 200, "перебор получился слишком маленьким"


# ─────────────────────────────────────────────────────────────
# Через агента: оборванный блок не ломает следующий запрос
# ─────────────────────────────────────────────────────────────

class StrictClient:
    """
    Подставной GigaChat: НЕ принимает недопустимую историю.

    Настоящий GigaChat отвечает на такое 400/422 — здесь то же самое, но
    исключением: тест проверяет, доходит ли до модели недопустимая история.
    """

    def __init__(self):
        self.calls = 0

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.calls += 1
        open_calls = set()
        for message in messages:
            role = message.get("role")
            if role == "assistant" and message.get("tool_calls"):
                open_calls |= {str(call.get("id") or "")
                               for call in message["tool_calls"]}
            elif role in ("tool", "function"):
                open_calls.discard(str(message.get("tool_call_id") or
                                       message.get("name") or ""))
            elif role == "user" and open_calls:
                raise RuntimeError(
                    "GigaChat вернул 422: незакрытый вызов инструмента "
                    "в истории"
                )
        if open_calls:
            raise RuntimeError("GigaChat вернул 422: вызов без результата")
        return {"role": "assistant", "content": "готово"}


def test_next_request_survives_an_unfinished_block():
    """
    Оператор закрыл программу посреди ответа — следующий вопрос обязан
    пройти: недопустимая история до модели не доходит.
    """
    client = StrictClient()
    agent = Agent(client=client, context=AgentContext(),
                  memory=memory_with([]))
    agent.memory._repository = NullRepository()

    #: Так выглядит история, оборванная на вызове инструмента.
    agent.memory.add("user", "найди Ромашку", persist=False)
    agent.memory.add("assistant", "", persist=False, tool_calls=[{
        "id": "call_1", "type": "function",
        "function": {"name": "search_anything",
                     "arguments": json.dumps({"query": "Ромашка"},
                                             ensure_ascii=False)},
    }])

    reply = agent.send("сколько договоров за год")

    assert reply.text == "готово"
    assert client.calls == 1
