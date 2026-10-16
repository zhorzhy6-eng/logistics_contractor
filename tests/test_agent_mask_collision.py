#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Маскирование в диалоге: один плейсхолдер — одно значение.

НАЙДЕНО СТРЕСС-ТЕСТОМ (200 запросов, 15.10.2026, запрос №7 из категории B.1).
Оператор спросил «Найди», а Максимилиан ответил разбором слова «Заполни» —
то есть ответом на ПРЕДЫДУЩИЙ вопрос. Причина нашлась в истории диалога
(таблица ``agent_history`` рабочей копии базы):

  * маска ПДн закрывает короткие сообщения оператора целиком: «Заполни» →
    ``<<PERSON_1>>`` (одно слово с заглавной буквы NER-модель считает именем);
  * КАЖДОЕ сообщение маскировалось СВОИМ маппингом, а маппинги сливались
    через ``dict.update`` — имя ``<<PERSON_1>>`` доставалось то одному, то
    другому значению. В модель уходили два РАЗНЫХ сообщения оператора одним
    и тем же текстом ``<<PERSON_1>>``, а ``restore`` возвращал то значение,
    которое попало в маппинг последним: инструмент получал слово из ЧУЖОГО
    сообщения.

Здесь проверяется правило, которое это закрывает: в пределах одного запроса
у каждого значения СВОЙ токен, а одно и то же значение — всегда один и тот
же токен. Поведение самой NER-модели (какие слова она считает именами) не
проверяется: это чужая модель, и от неё тесты не зависят — для столкновения
достаточно любых двух ФИО.

Что НЕ входит в эту правку: точность детектора (императив «Заполни»
по-прежнему считается именем). Он живёт в core/pseudonymizer.py, а этот файл
AGENTS.md § 4 относит к «спроси перед изменением» — предложение записано в
docs/STRESS_AGENT.md.
"""

import json
import sys
from pathlib import Path

import pytest

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.agent import Agent  # noqa: E402
from core.agent.llm import SafeChat  # noqa: E402
from core.agent.memory import AgentMemory  # noqa: E402
from core.agent.tools import AgentContext, Tool, ToolRegistry  # noqa: E402

#: Два разных ФИО. Каждое ПО ОТДЕЛЬНОСТИ маскируется в ``<<PERSON_1>>``:
#: счётчик начинается с единицы в каждом вызове — отсюда и столкновение.
FIRST_PERSON = "Иванов Иван Иванович"
SECOND_PERSON = "Петров Пётр Петрович"
THIRD_PERSON = "Сидоров Сидор Сидорович"


class SilentClient:
    """Подставная модель: отвечает текстом, к инструментам не зовёт."""

    def __init__(self, answer="готово"):
        self.answer = answer
        self.seen = []

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.seen.append([dict(message) for message in messages])
        return {"role": "assistant", "content": self.answer}


class ScriptedClient:
    """
    Подставная модель: на ПЕРВОМ шаге каждого запроса просит инструмент,
    дальше отвечает текстом.

    Ровно так ведёт себя настоящий GigaChat: сначала вызов, затем текст.
    Аргументы вызова задаёт тест — именно их и проверяем. Шаг определяется
    по последнему сообщению: если оно от оператора — это начало запроса.
    """

    def __init__(self, tool="search_anything", arguments=None):
        self.tool = tool
        self.arguments = dict(arguments or {})
        self.calls = 0
        self.last_result = ""
        self.seen = []

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.calls += 1
        self.seen.append([dict(message) for message in messages])
        for message in reversed(messages):
            if message.get("role") in ("tool", "function"):
                self.last_result = message.get("content") or ""
                break
        if (messages or [{}])[-1].get("role") == "user":
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": f"call_{self.calls}",
                    "type": "function",
                    "function": {
                        "name": self.tool,
                        "arguments": json.dumps(self.arguments,
                                                ensure_ascii=False),
                    },
                }],
            }
        return {"role": "assistant", "content": self.last_result or "готово"}


def wire_users(messages):
    """Тексты сообщений оператора так, как их увидела модель."""
    return [message.get("content") for message in messages
            if message.get("role") == "user"]


# ─────────────────────────────────────────────────────────────
# Один плейсхолдер — одно значение
# ─────────────────────────────────────────────────────────────

def test_two_people_get_different_placeholders():
    """
    Два разных ФИО маскируются оба — но РАЗНЫМИ токенами.

    До правки в модель уходило два одинаковых ``<<PERSON_1>>``: модель не
    могла отличить одно сообщение оператора от другого, а инструмент
    получал имя из последнего замаскированного сообщения.
    """
    chat = SafeChat(SilentClient())
    chat.begin_turn()
    masked = chat._safe_messages([
        {"role": "user", "content": FIRST_PERSON},
        {"role": "assistant", "content": "Что с ним сделать?"},
        {"role": "user", "content": SECOND_PERSON},
    ])
    first, second = wire_users(masked)
    assert first == "<<PERSON_1>>"
    assert second == "<<PERSON_2>>"
    assert first != second


def test_restore_returns_the_value_of_its_own_message():
    """Токен первого сообщения восстанавливается в ПЕРВОЕ значение."""
    chat = SafeChat(SilentClient())
    chat.begin_turn()
    chat._safe_messages([
        {"role": "user", "content": FIRST_PERSON},
        {"role": "user", "content": SECOND_PERSON},
    ])
    assert chat.restore("<<PERSON_1>>") == FIRST_PERSON
    assert chat.restore("<<PERSON_2>>") == SECOND_PERSON


def test_tool_message_does_not_steal_the_token():
    """
    Результат инструмента маскируется тем же правилом.

    Именно на этом ломался живой диалог: текст ошибки инструмента содержал
    фамилию, она получала ``<<PERSON_1>>`` и ПЕРЕЗАПИСЫВАЛА значение
    сообщения оператора.
    """
    chat = SafeChat(SilentClient())
    chat.begin_turn()
    chat._safe_messages([
        {"role": "user", "content": FIRST_PERSON},
        {"role": "tool", "content": f"Не нашёл {SECOND_PERSON} в базе",
         "name": "search_anything", "tool_call_id": "call_1"},
    ])
    assert chat.restore("<<PERSON_1>>") == FIRST_PERSON
    assert chat.restore("<<PERSON_2>>") == SECOND_PERSON


def test_restore_of_unknown_token_still_cleans_up():
    """Незнакомый токен по-прежнему вычищается из ответа (как и раньше)."""
    chat = SafeChat(SilentClient())
    chat.begin_turn()
    chat._safe_messages([{"role": "user", "content": FIRST_PERSON}])
    assert chat.restore("Ищу <<PERSON_9>>").strip() == "Ищу"


def test_same_value_keeps_the_same_placeholder_in_every_message():
    """
    Одно и то же значение во всех сообщениях — один токен.

    Иначе модель видела бы одно и то же ФИО как две разные записи.
    """
    chat = SafeChat(SilentClient())
    chat.begin_turn()
    masked = chat._safe_messages([
        {"role": "user", "content": FIRST_PERSON},
        {"role": "user", "content": f"найди {FIRST_PERSON} в базе"},
    ])
    users = wire_users(masked)
    assert users[0] == "<<PERSON_1>>"
    assert users[1] == "найди <<PERSON_1>> в базе"


def test_placeholders_are_unique_across_many_values():
    """Три разных ФИО — три разных токена, и все восстанавливаются."""
    values = [FIRST_PERSON, SECOND_PERSON, THIRD_PERSON]
    chat = SafeChat(SilentClient())
    chat.begin_turn()
    masked = chat._safe_messages([
        {"role": "user", "content": value} for value in values
    ])
    tokens = wire_users(masked)
    assert len(set(tokens)) == len(values)
    assert [chat.restore(token) for token in tokens] == values


def test_mapping_is_forgotten_between_requests():
    """Маппинг живёт один запрос: в следующем те же токены означают новое."""
    chat = SafeChat(SilentClient())
    chat.begin_turn()
    chat._safe_messages([{"role": "user", "content": FIRST_PERSON}])
    assert chat.restore("<<PERSON_1>>") == FIRST_PERSON

    chat.end_turn()
    chat.begin_turn()
    chat._safe_messages([{"role": "user", "content": SECOND_PERSON}])
    assert chat.restore("<<PERSON_1>>") == SECOND_PERSON


def test_describe_masking_counts_both_values():
    """В журнал уходят только типы и количество — по обоим значениям."""
    chat = SafeChat(SilentClient())
    chat.begin_turn()
    chat._safe_messages([
        {"role": "user", "content": FIRST_PERSON},
        {"role": "user", "content": SECOND_PERSON},
    ])
    assert chat.masked_count == 2
    assert chat.describe_masking() == "PERSON=2"


def test_secrets_of_different_kinds_do_not_collide_either():
    """Правило общее: столкновение имён закрыто для любых типов токенов."""
    chat = SafeChat(SilentClient())
    chat.begin_turn()
    chat._safe_messages([
        {"role": "user", "content": "+7 913 402-77-15"},
        {"role": "user", "content": "+7 495 123-45-67"},
    ])
    assert chat.restore("<<PHONE_1>>") == "+7 913 402-77-15"
    assert chat.restore("<<PHONE_2>>") == "+7 495 123-45-67"


def test_text_without_personal_data_is_not_changed():
    """Обычный текст маска не трогает: лишних токенов не появляется."""
    chat = SafeChat(SilentClient())
    chat.begin_turn()
    masked = chat._safe_messages([
        {"role": "user", "content": "Сколько договоров за год?"},
    ])
    assert wire_users(masked) == ["Сколько договоров за год?"]
    assert chat.masked_count == 0


def test_system_and_assistant_messages_are_not_masked():
    """Маска ставится только на сообщения оператора и результаты инструментов."""
    chat = SafeChat(SilentClient())
    chat.begin_turn()
    masked = chat._safe_messages([
        {"role": "system", "content": FIRST_PERSON},
        {"role": "assistant", "content": SECOND_PERSON},
    ])
    assert [message["content"] for message in masked] == [FIRST_PERSON,
                                                          SECOND_PERSON]


# ─────────────────────────────────────────────────────────────
# Через агента: что увидела модель, то и получил инструмент
# ─────────────────────────────────────────────────────────────

class RecordingTool:
    """Инструмент-запись: складывает аргументы, с которыми его позвали."""

    def __init__(self):
        self.calls = []

    def __call__(self, query="", **_):
        self.calls.append(query)
        return {"ok": True, "data": [{"found": query}], "count": 1,
                "query": query}


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


def build_agent(client, recorder):
    """Агент с одним инструментом поиска вместо полного реестра."""
    registry = ToolRegistry([
        Tool(
            name="search_anything",
            description="Поиск записи во всех справочниках",
            parameters={"type": "object",
                        "properties": {"query": {"type": "string"}}},
            function=recorder,
        ),
    ])
    memory = AgentMemory(session_id="test-mask", repository=NullRepository())
    return Agent(client=client, context=AgentContext(), memory=memory,
                 registry=registry, max_tool_steps=3)


def test_tool_receives_the_value_the_model_saw():
    """
    Модель повторила токен из истории — инструмент обязан получить то
    значение, которому этот токен соответствует, а не последнее по времени.
    """
    recorder = RecordingTool()
    client = ScriptedClient(arguments={"query": "<<PERSON_1>>"})
    agent = build_agent(client, recorder)

    agent.send(FIRST_PERSON)
    agent.send(SECOND_PERSON)

    assert recorder.calls == [FIRST_PERSON, FIRST_PERSON]


def test_model_sees_both_messages_as_different_text():
    """В модель два разных сообщения уходят РАЗНЫМ текстом."""
    recorder = RecordingTool()
    client = ScriptedClient(arguments={"query": "ничего"})
    agent = build_agent(client, recorder)

    agent.send(FIRST_PERSON)
    agent.send(SECOND_PERSON)

    users = wire_users(client.seen[-1])
    assert users == ["<<PERSON_1>>", "<<PERSON_2>>"]


def test_model_sees_same_value_as_same_text():
    """Одно значение в двух сообщениях — один и тот же токен."""
    recorder = RecordingTool()
    client = ScriptedClient(arguments={"query": "ничего"})
    agent = build_agent(client, recorder)

    agent.send(FIRST_PERSON)
    agent.send(FIRST_PERSON)

    users = wire_users(client.seen[-1])
    assert users == ["<<PERSON_1>>", "<<PERSON_1>>"]


def test_agent_restores_arguments_before_calling_tool():
    """Аргументы инструмента восстанавливаются из маски перед вызовом."""
    recorder = RecordingTool()
    client = ScriptedClient(arguments={"query": "<<PERSON_1>>"})
    agent = build_agent(client, recorder)

    agent.send(SECOND_PERSON)

    assert recorder.calls == [SECOND_PERSON]


def test_answer_with_placeholder_is_restored_for_operator():
    """Ответ модели восстанавливается: оператор не должен видеть токены."""
    recorder = RecordingTool()
    client = SilentClient(answer="Нашёл <<PERSON_1>> в базе")
    agent = build_agent(client, recorder)

    reply = agent.send(FIRST_PERSON)

    assert "<<" not in reply.text
    assert FIRST_PERSON in reply.text


# ─────────────────────────────────────────────────────────────
# Живой случай: короткая команда целиком уходит под маску
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def natasha_layer(natasha_warmed_up, monkeypatch):
    """
    Включает реальный слой Natasha для одного теста.

    ``natasha_warmed_up`` (tests/conftest.py) строит модели один раз на
    прогон; по умолчанию слой выключен (autouse-фикстура
    ``natasha_layer_off``). Если библиотеки нет — тест пропускается.
    """
    import core.pseudonymizer as pseudonymizer

    monkeypatch.setattr(pseudonymizer, "_NATASHA_AVAILABLE", None)
    if pseudonymizer._natasha_pipeline() is None:
        pytest.skip("natasha недоступна — маскирование работает на регулярках")
    return pseudonymizer


def test_live_case_two_commands_do_not_share_a_placeholder(natasha_layer):
    """
    Живой случай стресс-теста: «Заполни» и «Отмени» — два разных сообщения.

    Оба коротких и оба целиком уходят под маску (NER видит в них имя).
    Проверяем главное: токены РАЗНЫЕ, и каждый восстанавливается в своё
    слово. Точность самого NER здесь не проверяется.
    """
    chat = SafeChat(SilentClient())
    chat.begin_turn()
    masked = chat._safe_messages([
        {"role": "user", "content": "Заполни"},
        {"role": "assistant", "content": "Что заполнить?"},
        {"role": "user", "content": "Отмени"},
    ])
    first, second = wire_users(masked)
    if first == "Заполни" and second == "Отмени":
        pytest.skip("NER не считает эти слова именами — маски и не было")
    assert first != second
    assert chat.restore(first) == "Заполни"
    assert chat.restore(second) == "Отмени"
