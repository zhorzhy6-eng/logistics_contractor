#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты сверки со справочником салонов при распознавании (ШАГ FIX-2.2, часть E).

Проверяется core/gigachat_client.py:

  * поиск сигналов в тексте — коды JMR-Axxx, ИНН (10–12 цифр) и адреса —
    и запросы к справочнику выгрузки по salon_code / salon_inn / address;
  * формат выдержки «КОД | Юр. Лицо | Город | Адрес доставки»;
  * подстановка выдержки в промпт вместо маркера {{SALONS_DIRECTORY}} и
    дописывание в конец, если маркера нет;
  * БЕЗ сигналов промпт уходит без изменений;
  * recognize_text: своему промпту выдержка подставляется, системному
    промпту клиента (перевозка) — нет.

База — изолированная (fixture isolated_db), сеть не задействована.
Все данные синтетические, реальных ПДн нет.
"""

import logging

import pytest

from core.gigachat_client import (
    GigaChatClient,
    SALONS_DIRECTORY_MARKER,
    apply_salons_directory,
    find_salon_directory_rows,
    format_salon_directory,
)
from db.database import save_address

#: Тестовая запись справочника салонов.
SALON = {
    "address": "г. Москва, ул. Складская, д. 8",
    "salon_name": 'ООО "КАР АЦ"',
    "salon_code": "JMR-A048",
    "salon_inn": "7701234567",
    "salon_city": "Москва",
}

#: Вторая запись — чтобы проверить, что лишнее не попадает в выдержку.
SALON_2 = {
    "address": "г. Тверь, ул. Новая, д. 1",
    "salon_name": 'ООО "Другой салон"',
    "salon_code": "JMR-B001",
    "salon_inn": "6901234567",
    "salon_city": "Тверь",
}

#: Промпт типа с маркером — как у Логистикс Рус.
PROMPT_WITH_MARKER = (
    "СПРАВОЧНИК САЛОНОВ\n"
    "Если адрес совпадает с записью из справочника — бери данные оттуда.\n"
    f"{SALONS_DIRECTORY_MARKER}\n"
    "СХЕМА ОТВЕТА {\"shipper_name\": \"\"}"
)


@pytest.fixture(autouse=True)
def _no_real_salons_file(monkeypatch):
    """
    Изолированная база не должна заливать рабочий data/spravochnik…xlsx.

    init_database() при первом запуске подхватывает файл справочника салонов
    (ШАГ FIX-2.2, п. C.9) — в этих тестах справочник свой, из save_address.
    """
    import db.database as database

    monkeypatch.setattr(
        database, "salons_xlsx_path",
        lambda: "tests/_tmp/no-such-salons-file.xlsx",
    )


@pytest.fixture
def salon_book(isolated_db):
    """Справочник выгрузки с двумя салонами."""
    save_address("unloading", SALON["address"], salon_name=SALON["salon_name"],
                 salon_code=SALON["salon_code"], salon_inn=SALON["salon_inn"],
                 salon_city=SALON["salon_city"])
    save_address("unloading", SALON_2["address"], salon_name=SALON_2["salon_name"],
                 salon_code=SALON_2["salon_code"], salon_inn=SALON_2["salon_inn"],
                 salon_city=SALON_2["salon_city"])
    return isolated_db


# ─────────────────────────────────────────────────────────────
# Поиск сигналов
# ─────────────────────────────────────────────────────────────

def _codes(rows):
    return sorted(row["salon_code"] for row in rows)


def test_lookup_finds_by_salon_code(salon_book):
    rows = find_salon_directory_rows("Отгрузка на JMR-A048, время с 9 до 20")

    assert _codes(rows) == ["JMR-A048"]


def test_lookup_finds_by_inn(salon_book):
    rows = find_salon_directory_rows("Получатель: ООО, ИНН 7701234567")

    assert _codes(rows) == ["JMR-A048"]


def test_lookup_finds_by_address(salon_book):
    rows = find_salon_directory_rows(
        "Адрес выгрузки: г. Москва, ул. Складская, д. 8"
    )

    assert _codes(rows) == ["JMR-A048"]


def test_lookup_finds_several_signals(salon_book):
    text = (
        "Салон JMR-A048 (ИНН 7701234567), адрес: г. Москва, ул. Складская, д. 8\n"
        "Салон JMR-B001, адрес: г. Тверь, ул. Новая, д. 1"
    )

    assert _codes(find_salon_directory_rows(text)) == ["JMR-A048", "JMR-B001"]


def test_lookup_without_signals_is_empty(salon_book):
    """Текст без кодов, ИНН и адресов справочник не запрашивает."""
    assert find_salon_directory_rows("Заявка № ЛР-1 от 24.09.2026") == []
    assert find_salon_directory_rows("") == []
    assert find_salon_directory_rows(None) == []


def test_lookup_with_unknown_salon_is_empty(salon_book):
    """Сигнал есть, но записи в справочнике нет — выдержка пустая."""
    assert find_salon_directory_rows("Салон JMR-Z999") == []
    assert find_salon_directory_rows("ИНН 9999999999") == []


def test_lookup_ignores_foreign_inn_length(salon_book):
    """9 и 13 цифр ИНН не считаются — это не ИНН."""
    assert find_salon_directory_rows("номер 770123456") == []
    assert find_salon_directory_rows("номер 77012345678") == []


def test_lookup_logs_no_personal_data(salon_book, caplog):
    """В лог попадают только количества сигналов, без значений."""
    with caplog.at_level(logging.INFO, logger="core.gigachat_client"):
        find_salon_directory_rows("Салон JMR-A048, ул. Складская, д. 8")

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "найдено записей" in messages
    for fragment in ("Складская", "КАР АЦ", "JMR-A048", "7701234567"):
        assert fragment not in messages, f"в логе есть «{fragment}»"


# ─────────────────────────────────────────────────────────────
# Формат выдержки
# ─────────────────────────────────────────────────────────────

def test_format_directory_row(salon_book):
    rows = find_salon_directory_rows("JMR-A048")

    text = format_salon_directory(rows)

    assert text.startswith("СПРАВОЧНИК САЛОНОВ (найдено 1 записей):")
    assert 'JMR-A048 | ООО "КАР АЦ" | Москва | г. Москва, ул. Складская, д. 8' in text


def test_format_directory_is_empty_without_rows():
    assert format_salon_directory([]) == ""
    assert format_salon_directory(None) == ""


def test_format_directory_fills_missing_fields(salon_book):
    """Пустые поля записи печатаются прочерком, а не «None»."""
    save_address("unloading", "г. Тверь, ул. Пустая, д. 9", salon_code="JMR-C003")
    rows = find_salon_directory_rows("Салон JMR-C003")

    text = format_salon_directory(rows)

    # Город берётся из адреса (_extract_city — он приводит к нижнему
    # регистру), наименование салона пустое и печатается прочерком.
    assert "JMR-C003 | — | тверь | г. Тверь, ул. Пустая, д. 9" in text


# ─────────────────────────────────────────────────────────────
# Подстановка в промпт
# ─────────────────────────────────────────────────────────────

def test_marker_is_replaced(salon_book):
    prompt = apply_salons_directory(PROMPT_WITH_MARKER, "Салон JMR-A048")

    assert SALONS_DIRECTORY_MARKER not in prompt
    assert "СПРАВОЧНИК САЛОНОВ (найдено 1 записей):" in prompt
    assert 'JMR-A048 | ООО "КАР АЦ" | Москва' in prompt
    # Остальной текст промпта не тронут.
    assert "СХЕМА ОТВЕТА" in prompt


def test_without_signals_prompt_is_unchanged(salon_book):
    """Сигналов нет — промпт уходит ровно таким, каким его передали."""
    prompt = apply_salons_directory(PROMPT_WITH_MARKER, "Заявка № ЛР-1")

    assert prompt == PROMPT_WITH_MARKER
    assert SALONS_DIRECTORY_MARKER in prompt


def test_directory_is_appended_without_marker(salon_book):
    """Маркера в промпте нет, но записи нашлись — выдержка идёт в конец."""
    prompt = apply_salons_directory("СВОЙ ПРОМПТ", "Салон JMR-A048")

    assert prompt.startswith("СВОЙ ПРОМПТ")
    assert prompt.endswith("г. Москва, ул. Складская, д. 8")
    assert "СПРАВОЧНИК САЛОНОВ" in prompt


def test_apply_to_empty_prompt_does_nothing(salon_book):
    assert apply_salons_directory("", "Салон JMR-A048") == ""
    assert apply_salons_directory(None, "Салон JMR-A048") is None


# ─────────────────────────────────────────────────────────────
# recognize_text: подстановка в промпт типа
# ─────────────────────────────────────────────────────────────

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

    instance = module.GigaChatClient(auth_key="synthetic-key", ca_bundle="")
    monkeypatch.setattr(instance, "_get_token", lambda force=False: "TOKEN-12345")

    sent = {"payloads": [], "answer": "{}"}

    def fake_post(url, **kwargs):
        sent["payloads"].append(kwargs.get("json"))
        return _FakeChatResponse(sent["answer"])

    monkeypatch.setattr(module.requests, "post", fake_post)
    return instance, sent


def _system_content(payload: dict) -> str:
    return payload["messages"][0]["content"]


def test_recognize_text_fills_directory_for_type_prompt(client, salon_book):
    """Своему промпту выдержка подставляется, если сигналы есть."""
    instance, sent = client

    instance.recognize_text(
        "Заявка № ЛР-1. Салон JMR-A048, адрес: г. Москва, ул. Складская, д. 8",
        prompt=PROMPT_WITH_MARKER,
    )

    system_prompt = _system_content(sent["payloads"][-1])
    assert SALONS_DIRECTORY_MARKER not in system_prompt
    assert 'JMR-A048 | ООО "КАР АЦ" | Москва' in system_prompt


def test_recognize_text_keeps_directory_section_for_type_prompt(client, salon_book):
    """Секция «СПРАВОЧНИК САЛОНОВ» из промпта остаётся на месте."""
    instance, sent = client

    instance.recognize_text("Салон JMR-A048", prompt=PROMPT_WITH_MARKER)

    system_prompt = _system_content(sent["payloads"][-1])
    assert "Если адрес совпадает с записью из справочника" in system_prompt
    # Маркер заменён содержимым, а не удалён вместе со строкой.
    assert "СПРАВОЧНИК САЛОНОВ (найдено 1 записей):" in system_prompt


def test_recognize_text_without_signals_keeps_prompt(client, salon_book):
    """Сигналов нет — промпт уходит без выдержки (маркер остаётся)."""
    instance, sent = client

    instance.recognize_text("Заявка № ЛР-1", prompt=PROMPT_WITH_MARKER)

    assert _system_content(sent["payloads"][-1]) == PROMPT_WITH_MARKER


def test_recognize_text_does_not_touch_system_prompt(client, salon_book):
    """Промпт перевозки (без своего промпта) не меняется."""
    instance, sent = client

    instance.recognize_text("Салон JMR-A048, ул. Складская, д. 8")

    assert _system_content(sent["payloads"][-1]) == GigaChatClient.SYSTEM_PROMPT


def test_recognize_text_prompt_without_marker_is_appended(client, salon_book):
    """Если у промпта нет маркера, выдержка дописывается в конец."""
    instance, sent = client

    instance.recognize_text("Салон JMR-A048", prompt="СВОЙ ПРОМПТ")

    system_prompt = _system_content(sent["payloads"][-1])
    assert system_prompt.startswith("СВОЙ ПРОМПТ")
    assert "СПРАВОЧНИК САЛОНОВ (найдено 1 записей):" in system_prompt
