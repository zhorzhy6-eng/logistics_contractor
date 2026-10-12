#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Нормализация ответа модели по «меточным» полям (core/gigachat_client).

Что проверяется: когда метка поля стоит в тексте БЕЗ двоеточия, модель
приклеивает её к значению —

  * bank_name   → «Банк ПАО «Промсвязьбанк»» вместо «ПАО «Промсвязьбанк»»;
  * director_name → «Руководитель Иванов Иван Иванович» вместо ФИО.

Здесь проверяются обе половины правки: правила системного промпта (модель
обязана вернуть значение без метки) и постобработка ответа (страховка, если
модель правило всё же нарушила). Значения не выдумываются: снимается только
слово-метка, и только если после неё осталось осмысленное значение.

Все данные синтетические.
"""

import json
from threading import Event
from types import SimpleNamespace

import pytest

from core.gigachat_client import (
    GigaChatClient, _normalize_labeled_values, _strip_label_prefix,
)


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

    client = GigaChatClient(auth_key="synthetic-key", ca_bundle="")
    monkeypatch.setattr(client, "_get_token", lambda force=False: "TOKEN-12345")

    sent = {"payloads": [], "answer": "{}"}

    def fake_post(url, **kwargs):
        sent["payloads"].append(kwargs.get("json"))
        return _FakeChatResponse(sent["answer"])

    monkeypatch.setattr(module.requests, "post", fake_post)
    return client, sent


# ─────────────────────────────────────────────────────────────
# Снятие метки: юнит-уровень
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("Банк ПАО «Промсвязьбанк»", "ПАО «Промсвязьбанк»"),
    ("банк ВТБ (ПАО)", "ВТБ (ПАО)"),
    ("Банк: Сбербанк", "Сбербанк"),
    ("Банк. Росбанк", "Росбанк"),
    ("Банк получателя ПАО Сбербанк", "ПАО Сбербанк"),
    ("Наименование банка АО «Тинькофф Банк»", "АО «Тинькофф Банк»"),
])
def test_bank_label_is_stripped(raw, expected):
    data = _normalize_labeled_values({"carrier": {"bank_name": raw}})
    assert data["carrier"]["bank_name"] == expected


@pytest.mark.parametrize("raw", [
    "ПАО «Промсвязьбанк»",          # значения без метки не трогаются
    "ВТБ (ПАО)",
    "Банк",                          # одна метка без значения — оставляем как есть
    "",
    "   ",
])
def test_bank_value_without_label_is_untouched(raw):
    data = _normalize_labeled_values({"carrier": {"bank_name": raw}})
    assert data["carrier"]["bank_name"] == raw


@pytest.mark.parametrize("raw, expected", [
    ("Руководитель Иванов Иван Иванович", "Иванов Иван Иванович"),
    ("Директор. Петров Пётр Петрович", "Петров Пётр Петрович"),
    ("Директор:Сидоров Семён Семёнович", "Сидоров Семён Семёнович"),
    ("Генеральный директор Кузнецова Анна Петровна", "Кузнецова Анна Петровна"),
    ("Исполняющий обязанности директора Ильина Алина Никитична",
     "Ильина Алина Никитична"),
    ("и.о. директора Морозов Илья Петрович", "Морозов Илья Петрович"),
    ("Руководитель О'Брайен Шон Патрикович", "О'Брайен Шон Патрикович"),
])
def test_director_label_is_stripped(raw, expected):
    data = _normalize_labeled_values({"carrier": {"director_name": raw}})
    assert data["carrier"]["director_name"] == expected


@pytest.mark.parametrize("raw", [
    "Иванов Иван Иванович",
    "Директор",                       # осталось бы пустое значение
    "Директор Смирнов",               # одно слово — ФИО не получится
    "Индивидуальный предприниматель Иванов Иван Иванович",  # это роль ИП, не метка
])
def test_director_value_is_not_destroyed(raw):
    data = _normalize_labeled_values({"carrier": {"director_name": raw}})
    assert data["carrier"]["director_name"] == raw


def test_strip_label_prefix_ignores_empty_tail():
    assert _strip_label_prefix("Банк", ("банк",)) == "Банк"
    assert _strip_label_prefix("Банк ", ("банк",)) == "Банк"
    assert _strip_label_prefix("", ("банк",)) == ""


def test_customer_section_is_cleaned_too():
    data = _normalize_labeled_values({"customer": {
        "director_name": "Руководитель Сергеев Ярослав Андреевич",
        "bank_name": "Банк ПАО «Росбанк»",
    }})
    assert data["customer"]["director_name"] == "Сергеев Ярослав Андреевич"
    assert data["customer"]["bank_name"] == "ПАО «Росбанк»"


def test_input_data_is_not_mutated():
    source = {"carrier": {"bank_name": "Банк ВТБ (ПАО)", "inn": "7707083893"}}
    result = _normalize_labeled_values(source)
    assert source["carrier"]["bank_name"] == "Банк ВТБ (ПАО)"
    assert result["carrier"]["bank_name"] == "ВТБ (ПАО)"


def test_other_fields_and_sections_are_untouched():
    source = {
        "carrier": {"inn": "7707083893", "kpp": "770701001",
                    "legal_address": "г. Москва, ул. Тестовая, д. 1"},
        "driver": {"full_name": "Иванов Иван Иванович"},
        "vehicles": [{"vehicle_type": "Тягач", "brand_model": "KAMAZ 5490"}],
        "contract": {"bank_name": "не должно тронуть"},
    }
    result = _normalize_labeled_values(source)
    assert result == source


@pytest.mark.parametrize("data", [None, {}, {"carrier": "строка"},
                                  {"carrier": None}, [], "текст"])
def test_broken_input_is_returned_as_is(data):
    assert _normalize_labeled_values(data) == data


# ─────────────────────────────────────────────────────────────
# Сквозь клиент: ответ модели доходит до вызывающей стороны очищенным
# ─────────────────────────────────────────────────────────────

def test_client_returns_cleaned_bank_and_director(client):
    client, sent = client
    sent["answer"] = json.dumps({"carrier": {
        "bank_name": "Банк ВТБ (ПАО)",
        "director_name": "Руководитель Иванов Иван Иванович",
        "director_position": "Директор",
    }}, ensure_ascii=False)

    parsed = client.recognize_text("ПЕРЕВОЗЧИК\nООО «Ромашка»\nДиректор Иванов Иван Иванович")

    assert parsed["carrier"]["bank_name"] == "ВТБ (ПАО)"
    assert parsed["carrier"]["director_name"] == "Иванов Иван Иванович"
    assert parsed["carrier"]["director_position"] == "Директор"


def test_client_keeps_correct_values(client):
    client, sent = client
    sent["answer"] = json.dumps({"carrier": {
        "bank_name": "ПАО «Промсвязьбанк»",
        "director_name": "Петров Пётр Петрович",
    }}, ensure_ascii=False)

    parsed = client.recognize_text("ПЕРЕВОЗЧИК\nООО «Ромашка»")

    assert parsed["carrier"]["bank_name"] == "ПАО «Промсвязьбанк»"
    assert parsed["carrier"]["director_name"] == "Петров Пётр Петрович"


def test_vision_answer_is_cleaned_too(monkeypatch):
    """Vision идёт тем же путём нормализации, что и текст."""
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
            "choices": [{"message": {"content": json.dumps({"carrier": {
                "bank_name": "Банк ПАО «Росбанк»",
                "director_name": "Директор. Фролов Георгий Викторович",
            }}, ensure_ascii=False)}}]})

    monkeypatch.setattr(module.requests, "post", fake_post)
    result, warning = client.recognize_image(Image.new("RGB", (30, 30)), Event())

    assert result["carrier"]["bank_name"] == "ПАО «Росбанк»"
    assert result["carrier"]["director_name"] == "Фролов Георгий Викторович"
    assert warning == ""


# ─────────────────────────────────────────────────────────────
# Системный промпт: примеры для банка и директора
# ─────────────────────────────────────────────────────────────

def test_prompt_gives_bank_example_without_label():
    prompt = GigaChatClient.SYSTEM_PROMPT
    assert "Банк ПАО «Промсвязьбанк»" in prompt
    assert "не «Банк ПАО «Промсвязьбанк»»" in prompt


def test_prompt_explains_label_without_colon():
    prompt = GigaChatClient.SYSTEM_PROMPT
    assert "Руководитель Иванов Иван Иванович" in prompt
    assert "director_position" in prompt
