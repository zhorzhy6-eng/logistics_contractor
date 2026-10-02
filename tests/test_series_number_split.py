#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты починки разбиения «серия / номер» паспорта и ВУ в ответе модели.

Обезличивание заменяет «60 26 123456» одним плейсхолдером, поэтому модель
возвращает весь токен в одном поле. Ядро (core/gigachat_client) обязано
после восстановления разложить пару: серия «XX XX», номер «XXXXXX».

Все данные синтетические. Логика обезличивания в этих тестах не меняется,
а проверяется как есть.
"""

import json
from threading import Event
from types import SimpleNamespace

import pytest


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


DRIVER_TEXT = (
    "Водитель:\nКузнецов Пётр Иванович, д.р. 15.03.1985\n"
    "Паспорт РФ:\n{passport}, выдан 20.06.2015\n"
    "код подразделения: 610-050\n"
    "Водительское удостоверение:\n{license}, выдано 10.10.2020, категории B, C"
)


def _recognize(client, sent, answer, text):
    sent["answer"] = answer
    return client.recognize_text(text)


# ─────────────────────────────────────────────────────────────
# Паспорт: токен модели в одном поле
# ─────────────────────────────────────────────────────────────

def test_passport_token_in_number_field_is_split(client):
    client, sent = client
    answer = ('{"driver": {"full_name": "<<PERSON_1>>", '
              '"passport_number": "<<PASSPORT_1>>", "passport_code": "610-050"}}')
    parsed = _recognize(client, sent, answer,
                        DRIVER_TEXT.format(passport="60 26 123456", license="99 12 654321"))
    driver = parsed["driver"]
    assert driver["passport_series"] == "60 26"
    assert driver["passport_number"] == "123456"
    assert driver["passport_code"] == "610-050"
    assert driver["full_name"] == "Кузнецов Пётр Иванович"

    # В модель ушли только плейсхолдеры, а не оригиналы.
    user_content = sent["payloads"][-1]["messages"][1]["content"]
    assert "60 26 123456" not in user_content
    assert "Кузнецов" not in user_content
    assert "<<PASSPORT_1>>" in user_content
    # И в результате нет ни плейсхолдеров, ни UI-заглушек.
    dumped = json.dumps(parsed, ensure_ascii=False)
    assert "<<" not in dumped
    assert "XX" not in dumped


def test_passport_token_in_series_field_is_split(client):
    client, sent = client
    answer = '{"driver": {"passport_series": "<<PASSPORT_1>>"}}'
    parsed = _recognize(client, sent, answer,
                        DRIVER_TEXT.format(passport="60 26 123456", license="99 12 654321"))
    assert parsed["driver"]["passport_series"] == "60 26"
    assert parsed["driver"]["passport_number"] == "123456"


def test_passport_token_in_both_fields_is_split(client):
    client, sent = client
    answer = ('{"driver": {"passport_series": "<<PASSPORT_1>>", '
              '"passport_number": "<<PASSPORT_1>>"}}')
    parsed = _recognize(client, sent, answer,
                        DRIVER_TEXT.format(passport="60 26 123456", license="99 12 654321"))
    assert parsed["driver"]["passport_series"] == "60 26"
    assert parsed["driver"]["passport_number"] == "123456"


@pytest.mark.parametrize("raw", ["60 26 123456", "6026123456"])
def test_passport_split_works_for_any_separators(client, raw):
    client, sent = client
    answer = '{"driver": {"passport_number": "<<PASSPORT_1>>"}}'
    parsed = _recognize(client, sent, answer,
                        DRIVER_TEXT.format(passport=raw, license="99 12 654321"))
    assert parsed["driver"]["passport_series"] == "60 26"
    assert parsed["driver"]["passport_number"] == "123456"


def test_normalize_splits_value_with_any_separators():
    """Дефис/точка/слитность: разложение пары работает на уровне ядра."""
    import core.gigachat_client as module

    cases = [
        ("60 26 123456", "60 26", "123456"),
        ("60-26 123456", "60 26", "123456"),
        ("6026123456", "60 26", "123456"),
        ("60.26 123456", "60 26", "123456"),
    ]
    for raw, series, number in cases:
        data = module._normalize_document_ids({"driver": {"passport_number": raw}})
        assert data["driver"]["passport_series"] == series
        assert data["driver"]["passport_number"] == number


# ─────────────────────────────────────────────────────────────
# Водительское удостоверение
# ─────────────────────────────────────────────────────────────

def test_license_token_in_number_field_is_split(client):
    client, sent = client
    answer = '{"driver": {"license_number": "<<LICENSE_1>>", "license_categories": "B, C"}}'
    parsed = _recognize(client, sent, answer,
                        DRIVER_TEXT.format(passport="60 26 123456", license="99 12 654321"))
    assert parsed["driver"]["license_series"] == "99 12"
    assert parsed["driver"]["license_number"] == "654321"
    assert parsed["driver"]["license_categories"] == "B, C"


def test_license_token_in_series_field_is_split(client):
    client, sent = client
    answer = '{"driver": {"license_series": "<<LICENSE_1>>"}}'
    parsed = _recognize(client, sent, answer,
                        DRIVER_TEXT.format(passport="60 26 123456", license="99 12 654321"))
    assert parsed["driver"]["license_series"] == "99 12"
    assert parsed["driver"]["license_number"] == "654321"


# ─────────────────────────────────────────────────────────────
# Значения не выдумываются
# ─────────────────────────────────────────────────────────────

def test_valid_pair_is_not_touched(client):
    client, sent = client
    answer = '{"driver": {"passport_series": "60 26", "passport_number": "123456"}}'
    parsed = _recognize(client, sent, answer,
                        DRIVER_TEXT.format(passport="60 26 123456", license="99 12 654321"))
    assert parsed["driver"]["passport_series"] == "60 26"
    assert parsed["driver"]["passport_number"] == "123456"


def test_number_without_series_is_not_invented(client):
    client, sent = client
    answer = '{"driver": {"passport_number": "123456"}}'
    parsed = _recognize(client, sent, answer,
                        DRIVER_TEXT.format(passport="60 26 123456", license="99 12 654321"))
    assert parsed["driver"].get("passport_series", "") == ""
    assert parsed["driver"]["passport_number"] == "123456"


def test_empty_document_fields_stay_empty(client):
    client, sent = client
    answer = '{"driver": {"full_name": "<<PERSON_1>>"}}'
    parsed = _recognize(client, sent, answer,
                        DRIVER_TEXT.format(passport="60 26 123456", license="99 12 654321"))
    driver = parsed["driver"]
    assert driver.get("passport_series", "") == ""
    assert driver.get("passport_number", "") == ""
    assert driver.get("license_series", "") == ""
    assert driver.get("license_number", "") == ""


def test_wrong_length_value_is_left_as_is(client):
    client, sent = client
    answer = '{"driver": {"passport_number": "60 26 12345"}}'
    parsed = _recognize(client, sent, answer,
                        DRIVER_TEXT.format(passport="60 26 123456", license="99 12 654321"))
    assert parsed["driver"].get("passport_series", "") == ""
    assert parsed["driver"]["passport_number"] == "60 26 12345"


# ─────────────────────────────────────────────────────────────
# Vision: structured-ответ тоже нормализуется
# ─────────────────────────────────────────────────────────────

def test_vision_structured_answer_is_split(monkeypatch):
    from PIL import Image
    import core.gigachat_client as module

    client = module.GigaChatClient(auth_key="synthetic-key", ca_bundle="")
    monkeypatch.setattr(client, "_get_token", lambda force=False: "TOKEN")

    def fake_post(url, **kwargs):
        if url.endswith("/files"):
            return SimpleNamespace(status_code=200, text="{}",
                                   json=lambda: {"id": "synthetic-id"})
        if url.endswith("/delete"):
            return SimpleNamespace(status_code=200, text="{}", json=lambda: {})
        return SimpleNamespace(status_code=200, text="{}", json=lambda: {
            "choices": [{"message": {"content":
                '{"driver": {"full_name": "Тестов Тест Тестович", '
                '"passport_number": "60 26 123456", "passport_code": "610-050"}}'}}]})

    monkeypatch.setattr(module.requests, "post", fake_post)
    result, warning = client.recognize_image(Image.new("RGB", (30, 30)), Event())
    assert result["driver"]["full_name"] == "Тестов Тест Тестович"
    assert result["driver"]["passport_series"] == "60 26"
    assert result["driver"]["passport_number"] == "123456"
    assert result["driver"]["passport_code"] == "610-050"
    assert warning == ""
