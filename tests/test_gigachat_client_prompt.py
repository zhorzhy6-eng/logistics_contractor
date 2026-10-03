#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты своего промпта в GigaChatClient.recognize_text (ЭТАП 3.1.A.6.1).

recognize_text(text, prompt=None) должен:
  * без промпта (и с пустым) слать модели SYSTEM_PROMPT — поведение
    перевозки остаётся 1:1;
  * со своим промптом слать именно его: так типы со своим промптом
    (Формика, Логистикс Рус, Аренда, Хавалы) передают правила извлечения
    из core/prompts/;
  * по-прежнему обезличивать текст документа (промпт — не данные);
  * по-прежнему принимать retries и разбирать ответ.

Реальные запросы не выполняются: requests.post и _get_token подменяются.
Все данные синтетические, реальных ПДн нет.
"""

import logging

import pytest

from core.prompts import get_prompt


class _FakeChatResponse:
    """Ответ GigaChat с заданным содержимым; реальные запросы не выполняются."""

    status_code = 200

    def __init__(self, content: str):
        self._content = content
        self.text = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture
def client(monkeypatch):
    """Клиент с подменённым транспортом: (client, sent)."""
    import core.gigachat_client as module

    client = module.GigaChatClient(auth_key="synthetic-key", ca_bundle="")
    monkeypatch.setattr(client, "_get_token", lambda force=False: "TOKEN-12345")

    sent = {"payloads": [], "answer": "{}"}

    def fake_post(url, **kwargs):
        sent["payloads"].append(kwargs.get("json"))
        return _FakeChatResponse(sent["answer"])

    monkeypatch.setattr(module.requests, "post", fake_post)
    return client, sent


def _system_content(payload: dict) -> str:
    return payload["messages"][0]["content"]


def _user_content(payload: dict) -> str:
    return payload["messages"][1]["content"]


# ─────────────────────────────────────────────────────────────
# Промпт по умолчанию: поведение перевозки не меняется
# ─────────────────────────────────────────────────────────────

def test_call_without_prompt_uses_system_prompt(client):
    client, sent = client
    client.recognize_text("Водитель: тест")

    from core.gigachat_client import GigaChatClient

    assert _system_content(sent["payloads"][-1]) == GigaChatClient.SYSTEM_PROMPT


def test_none_prompt_uses_system_prompt(client):
    client, sent = client
    client.recognize_text("Водитель: тест", prompt=None)

    from core.gigachat_client import GigaChatClient

    assert _system_content(sent["payloads"][-1]) == GigaChatClient.SYSTEM_PROMPT


def test_empty_prompt_uses_system_prompt(client):
    client, sent = client
    client.recognize_text("Водитель: тест", prompt="")

    from core.gigachat_client import GigaChatClient

    assert _system_content(sent["payloads"][-1]) == GigaChatClient.SYSTEM_PROMPT


def test_whitespace_prompt_is_used_as_is(client):
    """Пробельный промпт — это уже переданное значение, не «пусто»."""
    client, sent = client
    client.recognize_text("Водитель: тест", prompt="   ")

    assert _system_content(sent["payloads"][-1]) == "   "


def test_first_message_roles_are_preserved(client):
    client, sent = client
    client.recognize_text("Водитель: тест", prompt="СВОЙ ПРОМПТ")

    messages = sent["payloads"][-1]["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert _system_content(sent["payloads"][-1]) == "СВОЙ ПРОМПТ"


# ─────────────────────────────────────────────────────────────
# Свой промпт типа
# ─────────────────────────────────────────────────────────────

def test_custom_prompt_is_sent_positionally(client):
    client, sent = client
    client.recognize_text("Водитель: тест", "TEST")

    assert _system_content(sent["payloads"][-1]) == "TEST"


def test_custom_prompt_is_sent_by_keyword(client):
    client, sent = client
    client.recognize_text("Водитель: тест", prompt="TEST")

    assert _system_content(sent["payloads"][-1]) == "TEST"


def test_response_is_parsed_with_custom_prompt(client):
    client, sent = client
    sent["answer"] = '{"contract": {"number": "ФМ-2026-1"}}'

    parsed = client.recognize_text("Договор-заявка № ФМ-2026-1", prompt="TEST")

    assert parsed["contract"]["number"] == "ФМ-2026-1"


def test_formika_prompt_can_be_passed_to_client(client):
    """Промпт Формики из core/prompts доходит до модели как есть."""
    client, sent = client
    formika_prompt = get_prompt("formika")
    assert formika_prompt

    client.recognize_text("Договор-заявка", prompt=formika_prompt)

    assert _system_content(sent["payloads"][-1]) == formika_prompt


# ─────────────────────────────────────────────────────────────
# Обезличивание и retries работают при своём промпте
# ─────────────────────────────────────────────────────────────

def test_personal_data_is_anonymized_with_custom_prompt(client):
    client, sent = client
    client.recognize_text("Водитель: Иванов Иван Иванович, паспорт 18 22 926830",
                          prompt="TEST")

    user_text = _user_content(sent["payloads"][-1])
    assert "Иванов" not in user_text
    assert "926830" not in user_text
    assert "<<" in user_text, "ожидались плейсхолдеры обезличивания"
    # Промпт правила не обезличиваются и уходят дословно.
    assert _system_content(sent["payloads"][-1]) == "TEST"


def test_retries_keyword_still_supported(client):
    """Второй параметр теперь prompt, поэтому retries передаётся именованно."""
    client, sent = client
    client.recognize_text("Водитель: тест", prompt="TEST", retries=0)

    assert len(sent["payloads"]) == 1


def test_retries_default_does_not_add_requests_on_success(client):
    client, sent = client
    client.recognize_text("Водитель: тест")

    assert len(sent["payloads"]) == 1


# ─────────────────────────────────────────────────────────────
# Логи и неприкосновенность SYSTEM_PROMPT
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("prompt, expected", [
    (None, "промпт=дефолтный"),
    ("", "промпт=дефолтный"),
    ("TEST", "промпт=свой"),
])
def test_log_reports_which_prompt_is_used(client, caplog, prompt, expected):
    client, sent = client

    with caplog.at_level(logging.INFO, logger="core.gigachat_client"):
        client.recognize_text("Водитель: тест", prompt=prompt)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert expected in messages


def test_log_has_no_prompt_text(client, caplog):
    """В лог не попадает текст промпта — только признак «свой/дефолтный»."""
    client, sent = client
    secret_prompt = "СЕКРЕТНЫЙ-ПРОМПТ-ТИПА"

    with caplog.at_level(logging.DEBUG, logger="core.gigachat_client"):
        client.recognize_text("Водитель: тест", prompt=secret_prompt)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert secret_prompt not in messages


def test_system_prompt_is_not_modified():
    """SYSTEM_PROMPT остаётся промптом перевозки (правка только добавила параметр)."""
    from core.gigachat_client import GigaChatClient

    assert "парсер данных" in GigaChatClient.SYSTEM_PROMPT
    assert "carrier" in GigaChatClient.SYSTEM_PROMPT
    assert "<<PERSON_1>>" in GigaChatClient.SYSTEM_PROMPT
