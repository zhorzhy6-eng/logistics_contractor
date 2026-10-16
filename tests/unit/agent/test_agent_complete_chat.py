#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Регресс: Максимилиан зовёт ``complete_chat`` у ЭКЗЕМПЛЯРА клиента.

Что было сломано
----------------
Настоящий ``GigaChatClient.complete_chat`` был помечен ``@staticmethod``,
хотя первым параметром принимает ``self``. При вызове через экземпляр
(``client.complete_chat(...)``) Python для статического метода не
подставляет ``self``, и ЛЮБОЙ запрос оператора заканчивался ошибкой::

    TypeError: GigaChatClient.complete_chat() missing 1 required
    positional argument: 'self'

Почему это не поймали 139 тестов
--------------------------------
Тесты ядра подставляют ``MockChat`` — обычный класс, где ``complete_chat``
объявлен обычным методом. Такой мок ведёт себя правильно, поэтому дефект
живого клиента в тестах не всплывал: расхождение было ровно в декораторе
настоящего класса.

Что проверяется здесь
---------------------
1. ``complete_chat`` у живого клиента — метод экземпляра, а не статический.
2. Полный путь оператора («Что ты умеешь?») проходит на НАСТОЯЩЕМ
   ``GigaChatClient`` с подменённым транспортом: панель → Agent → SafeChat →
   клиент → ответ. Сети нет, ``requests.post`` подменён.
3. Класс вместо экземпляра отвергается сразу и с рецептом — а не падением
   «missing 1 required positional argument» в момент запроса.
"""

import inspect
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

from core.agent.agent import Agent  # noqa: E402
from core.agent.llm import SafeChat, ensure_client  # noqa: E402
from core.agent.memory import AgentMemory  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Подставной транспорт GigaChat
# ─────────────────────────────────────────────────────────────

class _FakeResponse:
    """Ответ HTTP с сообщением модели (формат /chat/completions)."""

    def __init__(self, content: str = "", tool_calls=None, status_code: int = 200):
        self.status_code = status_code
        self.text = content
        self._content = content
        self._tool_calls = list(tool_calls or ())

    def json(self):
        return {"choices": [{"message": {"content": self._content,
                                         "tool_calls": self._tool_calls}}]}


@pytest.fixture
def live_client(monkeypatch):
    """
    НАСТОЯЩИЙ GigaChatClient с подменённым транспортом: (client, sent).

    Подменяются только токен и ``requests.post`` — то есть всё, что ходит
    в сеть. Сам объект клиента, его методы и разбор ответа настоящие:
    именно на этом стыке и жил баг.
    """
    import core.gigachat_client as module

    client = module.GigaChatClient(auth_key="synthetic-key", ca_bundle="")
    monkeypatch.setattr(client, "_get_token", lambda force=False: "TOKEN-12345")

    sent = {"payloads": [], "answer": "Я Максимилиан, помощник по договорам."}

    def fake_post(url, **kwargs):
        sent["payloads"].append({"url": url, "json": kwargs.get("json")})
        return _FakeResponse(sent["answer"])

    monkeypatch.setattr(module.requests, "post", fake_post)
    return client, sent


class StubRepository:
    """История диалога без базы: тест проверяет вызов клиента, не SQLite."""

    def __init__(self):
        self.rows = []

    def append(self, session_id, message):
        self.rows.append(message)

    def load(self, session_id, limit):
        return []

    def clear_session(self, session_id):
        return 0

    def purge_other_sessions(self, session_id):
        return 0


def make_agent(client):
    """Агент без Qt и без базы: только клиент, память и реестр инструментов."""
    return Agent(
        client=client,
        memory=AgentMemory(repository=StubRepository(), autoload=False),
    )


# ─────────────────────────────────────────────────────────────
# 1. Признак дефекта: complete_chat — метод экземпляра
# ─────────────────────────────────────────────────────────────

def test_complete_chat_is_not_staticmethod():
    """
    ``complete_chat`` объявлен обычным методом.

    Проверяем описание класса (``getattr_static``), а не живой атрибут:
    у экземпляра статический метод и обычный метод выглядят одинаково —
    разница видна только на уровне класса, где и прятался дефект.
    """
    from core.gigachat_client import GigaChatClient

    raw = inspect.getattr_static(GigaChatClient, "complete_chat")

    assert not isinstance(raw, staticmethod), (
        "complete_chat снова помечен @staticmethod: вызов через экземпляр "
        "упадёт с «missing 1 required positional argument: 'self'»"
    )
    assert inspect.isfunction(raw)


def test_bound_method_expects_messages_not_self(live_client):
    """
    У связанного метода первый параметр — ``messages``, а не ``self``.

    Это и есть проверка подстановки: если ``self`` остался в сигнатуре
    связанного метода, значит он не связывается — вызов упадёт.
    """
    client, _sent = live_client
    params = list(inspect.signature(client.complete_chat).parameters)

    assert "self" not in params
    assert params[0] == "messages"


# ─────────────────────────────────────────────────────────────
# 2. Полный путь оператора на живом клиенте
# ─────────────────────────────────────────────────────────────

def test_complete_chat_runs_on_live_client(live_client):
    """SafeChat + живой клиент: запрос уходит, сообщение модели приходит."""
    client, sent = live_client
    chat = SafeChat(client)

    message = chat.complete([{"role": "user", "content": "Что ты умеешь?"}])

    assert message["role"] == "assistant"
    assert message["content"] == "Я Максимилиан, помощник по договорам."
    assert sent["payloads"], "запрос не ушёл — клиент не был вызван"
    assert sent["payloads"][-1]["json"]["messages"] == [
        {"role": "user", "content": "Что ты умеешь?"}
    ]


def test_agent_answers_what_can_you_do(live_client):
    """
    «Что ты умеешь?» через Agent — тот самый сценарий из панели.

    До фикса здесь была ошибка «missing 1 required positional argument:
    'self'»; после — обычный ответ модели.
    """
    client, sent = live_client
    reply = make_agent(client).send("Что ты умеешь?")

    assert reply.text == "Я Максимилиан, помощник по договорам."
    assert reply.steps == 1
    assert sent["payloads"], "запрос не ушёл — клиент не был вызван"


def test_agent_sends_tools_to_live_client(live_client):
    """
    Инструменты агента доезжают до клиента в формате, который он понимает.

    Регресс: описания уходили в поле ``tools``, а этот endpoint его молча
    игнорирует — модель не вызывала инструменты вовсе. Рабочее поле —
    ``functions`` + ``function_call`` (проверено живьём, см.
    core/gigachat_client.py, раздел «ФОРМАТ ВЫЗОВА ФУНКЦИЙ»).
    """
    client, sent = live_client
    agent = make_agent(client)
    agent.send("Что ты умеешь?")

    payload = sent["payloads"][-1]["json"]
    assert payload["functions"], "описания инструментов не ушли в GigaChat"
    assert payload["function_call"] == "auto"
    assert "tools" not in payload, (
        "вернулось поле tools: этот endpoint его игнорирует, инструменты "
        "нужно отправлять в functions"
    )

    # Короткое имя и схема аргументов без служебных ключей.
    function = payload["functions"][0]
    assert function["name"]
    assert function["description"]
    assert function["parameters"]["type"] == "object"
    assert "additionalProperties" not in function["parameters"]


# ─────────────────────────────────────────────────────────────
# 3. Класс вместо экземпляра — понятная ошибка на входе
# ─────────────────────────────────────────────────────────────

def test_class_instead_of_instance_is_rejected():
    """Передали класс — падаем сразу и объясняем, как создать клиента."""
    from core.gigachat_client import GigaChatClient

    with pytest.raises(TypeError) as exc:
        SafeChat(GigaChatClient)

    text = str(exc.value)
    assert "Передан класс GigaChatClient вместо экземпляра" in text
    assert "GigaChatClient(...)" in text


def test_agent_rejects_class_instead_of_instance():
    """Та же защита работает и на входе в агент (сборка в ui)."""
    from core.gigachat_client import GigaChatClient

    with pytest.raises(TypeError):
        Agent(client=GigaChatClient)


def test_ensure_client_returns_instance_unchanged(live_client):
    """Пригодный экземпляр защита пропускает без изменений."""
    client, _sent = live_client

    assert ensure_client(client) is client


def test_object_without_complete_chat_is_rejected():
    """Объект без ``complete_chat`` — тоже понятная ошибка, а не AttributeError."""

    class NoChat:
        pass

    with pytest.raises(TypeError) as exc:
        SafeChat(NoChat())

    assert "complete_chat" in str(exc.value)


def test_mock_instance_path_still_works():
    """Мок-ЭКЗЕМПЛЯР (как в тестах ядра) продолжает работать."""

    class MockChat:
        def __init__(self):
            self.seen = []

        def complete_chat(self, messages, tools=None, temperature=0.2):
            self.seen.append(list(messages))
            return {"role": "assistant", "content": "ок", "tool_calls": []}

    mock = MockChat()
    reply = make_agent(mock).send("Что ты умеешь?")

    assert reply.text == "ок"
    assert mock.seen, "мок-экземпляр не получил запрос"
