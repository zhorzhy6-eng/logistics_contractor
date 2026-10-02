#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты клиента DaData (core/dadata_client.py).

Реальные запросы НЕ выполняются: requests.post подменяется через monkeypatch,
ответы синтетические. Ключ в тестах — фиктивная строка, keyring не читается.

Проверяется:
  * разбор ответа для юрлица и для ИП (у ИП нет КПП);
  * «ничего не найдено» — это None, а не ошибка;
  * разные тексты ошибок: 401/403, 429, 5xx, TLS, соединение, таймаут;
  * отсутствие ключа — ValueError с понятным текстом;
  * ключ и ИНН не попадают в лог (только длина ИНН и статусы).
"""

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
import requests  # noqa: E402

from core import dadata_client  # noqa: E402
from core.dadata_client import (  # noqa: E402
    DadataClient,
    bank_status_warning,
    normalize_fms_code,
    status_warning,
)

TEST_KEY = "test_dummy_key_1234567890abcdef"
INN_LEGAL = "7719402047"
INN_IP = "770708389334"
FULL_LEGAL_NAME = 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "МОТОРИКА"'
SHORT_LEGAL_NAME = 'ООО "МОТОРИКА"'
DIRECTOR = "Давидюк Андрей Павлович"
ADDRESS = "г Москва, Волгоградский пр-кт, д 42 к 5, помещ 1Н"
BIC = "044525225"
BANK_NAME = "ПАО СБЕРБАНК"
CORR_ACCOUNT = "30101810400000000225"
FMS_CODE = "500-123"
FMS_ISSUER = "Отделом УФМС России по г. Москве по району Хамовники"


class FakeResponse:
    """Минимальный ответ requests: статус, тело и json()."""

    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("response is not JSON")
        return self._payload


def legal_payload() -> dict:
    """Синтетический ответ DaData для юрлица."""
    return {
        "suggestions": [
            {
                "value": SHORT_LEGAL_NAME,
                "unrestricted_value": FULL_LEGAL_NAME,
                "data": {
                    "inn": INN_LEGAL,
                    "kpp": "772301001",
                    "ogrn": "1157746078984",
                    "type": "LEGAL",
                    "name": {
                        "full_with_opf": FULL_LEGAL_NAME,
                        "short_with_opf": SHORT_LEGAL_NAME,
                    },
                    "address": {"value": ADDRESS},
                    "management": {"name": DIRECTOR, "post": "ГЕНЕРАЛЬНЫЙ ДИРЕКТОР"},
                    "state": {"status": "ACTIVE"},
                    "phones": [{"value": "+7 495 123-45-67"}],
                    "emails": [{"value": "info@motorika.ru"}],
                },
            }
        ]
    }


def individual_payload() -> dict:
    """Синтетический ответ DaData для индивидуального предпринимателя."""
    return {
        "suggestions": [
            {
                "value": "ИП Иванов Иван Иванович",
                "data": {
                    "inn": INN_IP,
                    "ogrn": "304500116000157",
                    "type": "INDIVIDUAL",
                    "fio": {
                        "surname": "Иванов",
                        "name": "Иван",
                        "patronymic": "Иванович",
                    },
                    "address": {"value": "г Москва, ул Тестовая, д 1"},
                    "state": {"status": "ACTIVE"},
                },
            }
        ]
    }


@pytest.fixture
def post_stub(monkeypatch):
    """
    Подменяет requests.post в core/dadata_client.

    state["response"] — что вернуть, state["exception"] — что бросить,
    state["calls"] — журнал вызовов (url, headers, json, timeout).
    """
    state = {"calls": [], "response": None, "exception": None}

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        state["calls"].append({
            "url": url, "headers": headers or {}, "json": json, "timeout": timeout,
        })
        if state["exception"] is not None:
            raise state["exception"]
        return state["response"]

    monkeypatch.setattr(dadata_client.requests, "post", fake_post)
    return state


def make_client(**kwargs) -> DadataClient:
    kwargs.setdefault("api_key", TEST_KEY)
    return DadataClient(**kwargs)


# ─────────────────────────────────────────────────────────────
# Успешные ответы
# ─────────────────────────────────────────────────────────────

def test_legal_entity_all_fields_filled(post_stub):
    """Юрлицо: в плоском словаре заполнены все поля реквизитов."""
    post_stub["response"] = FakeResponse(200, legal_payload())

    data = make_client().find_party_by_inn(INN_LEGAL)

    assert data is not None
    assert data["full_name"] == FULL_LEGAL_NAME
    assert data["short_name"] == SHORT_LEGAL_NAME
    assert data["inn"] == INN_LEGAL
    assert data["kpp"] == "772301001"
    assert data["ogrn"] == "1157746078984"
    assert data["legal_address"] == ADDRESS
    assert data["director_name"] == DIRECTOR
    assert data["director_position"] == "ГЕНЕРАЛЬНЫЙ ДИРЕКТОР"
    assert data["entity_type"] == "LEGAL"
    assert data["status"] == "ACTIVE"
    assert data["phone"] == "+7 495 123-45-67"
    assert data["email"] == "info@motorika.ru"


def test_individual_entrepreneur_has_no_kpp(post_stub):
    """ИП: КПП пустой, должность — «Индивидуальный предприниматель»."""
    post_stub["response"] = FakeResponse(200, individual_payload())

    data = make_client().find_party_by_inn(INN_IP)

    assert data is not None
    assert data["kpp"] == ""
    assert data["entity_type"] == "INDIVIDUAL"
    assert data["director_position"] == "Индивидуальный предприниматель"
    assert data["director_name"] == "Иванов Иван Иванович"
    assert data["full_name"] == "Индивидуальный предприниматель Иванов Иван Иванович"
    assert data["short_name"] == data["full_name"]
    # У ИП тоже есть ОГРНИП — он должен попасть в поле ОГРН
    assert data["ogrn"] == "304500116000157"


def test_request_uses_expected_url_headers_and_payload(post_stub):
    """Запрос уходит на findById/party с Token-авторизацией и branch_type=MAIN."""
    post_stub["response"] = FakeResponse(200, legal_payload())

    make_client().find_party_by_inn(f"  {INN_LEGAL}  ")

    assert len(post_stub["calls"]) == 1
    call = post_stub["calls"][0]
    assert call["url"] == dadata_client.FIND_PARTY_URL
    assert call["json"] == {"query": INN_LEGAL, "branch_type": "MAIN"}
    assert call["headers"]["Authorization"] == f"Token {TEST_KEY}"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["timeout"] == 10


def test_empty_suggestions_returns_none(post_stub):
    """Пустой suggestions — «не найдено», а не ошибка."""
    post_stub["response"] = FakeResponse(200, {"suggestions": []})

    assert make_client().find_party_by_inn(INN_LEGAL) is None


def test_missing_suggestions_key_returns_none(post_stub):
    """Ответ без ключа suggestions тоже означает «не найдено»."""
    post_stub["response"] = FakeResponse(200, {})

    assert make_client().find_party_by_inn(INN_LEGAL) is None


def test_suggestion_without_requisites_returns_none(post_stub):
    """Подсказка без названия и ИНН бесполезна — считаем, что не найдено."""
    post_stub["response"] = FakeResponse(200, {"suggestions": [{"data": {}}]})

    assert make_client().find_party_by_inn(INN_LEGAL) is None


# ─────────────────────────────────────────────────────────────
# Ошибки HTTP
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [401, 403])
def test_unauthorized_key_error(post_stub, status):
    """401/403 — сообщение про неверный или отозванный ключ."""
    post_stub["response"] = FakeResponse(status, None, text="unauthorized")

    with pytest.raises(RuntimeError) as exc:
        make_client().find_party_by_inn(INN_LEGAL)

    message = str(exc.value).lower()
    assert "ключ" in message
    assert "set_dadata_key" in message


def test_rate_limit_error_mentions_limits(post_stub):
    """429 — сообщение про лимит 10 000/день или 30/сек."""
    post_stub["response"] = FakeResponse(429, None, text="too many requests")

    with pytest.raises(RuntimeError) as exc:
        make_client().find_party_by_inn(INN_LEGAL)

    message = str(exc.value)
    assert "лимит" in message.lower()
    assert "10 000" in message


@pytest.mark.parametrize("status", [500, 502, 503])
def test_server_error_is_reported_as_unavailable(post_stub, status):
    """5xx — «сервер DaData недоступен, повторите позже»."""
    post_stub["response"] = FakeResponse(status, None, text="server error")

    with pytest.raises(RuntimeError) as exc:
        make_client().find_party_by_inn(INN_LEGAL)

    assert "недоступен" in str(exc.value).lower()


def test_unexpected_status_is_reported(post_stub):
    """Прочие коды (например, 400) — общая ошибка API с кодом."""
    post_stub["response"] = FakeResponse(400, None, text="bad request")

    with pytest.raises(RuntimeError) as exc:
        make_client().find_party_by_inn(INN_LEGAL)

    assert "400" in str(exc.value)


def test_non_json_body_is_reported(post_stub):
    """Ответ 200 с не-JSON телом не роняет клиент, а превращается в RuntimeError."""
    post_stub["response"] = FakeResponse(200, None, text="<html>")

    with pytest.raises(RuntimeError) as exc:
        make_client().find_party_by_inn(INN_LEGAL)

    assert "json" in str(exc.value).lower()


# ─────────────────────────────────────────────────────────────
# Сетевые ошибки
# ─────────────────────────────────────────────────────────────

def test_ssl_error_mentions_tls(post_stub):
    """SSLError — понятный текст про TLS (без подсказок GigaChat)."""
    post_stub["exception"] = requests.exceptions.SSLError("certificate verify failed")

    with pytest.raises(RuntimeError) as exc:
        make_client().find_party_by_inn(INN_LEGAL)

    assert "TLS" in str(exc.value)


def test_connection_error_mentions_dadata_host(post_stub):
    """ConnectionError — «нет соединения с dadata.ru»."""
    post_stub["exception"] = requests.exceptions.ConnectionError("connection refused")

    with pytest.raises(RuntimeError) as exc:
        make_client().find_party_by_inn(INN_LEGAL)

    message = str(exc.value).lower()
    assert "соединения" in message
    assert "dadata.ru" in message


def test_timeout_error_mentions_waiting(post_stub):
    """Timeout — «превышено время ожидания»."""
    post_stub["exception"] = requests.exceptions.Timeout("timed out")

    with pytest.raises(RuntimeError) as exc:
        make_client().find_party_by_inn(INN_LEGAL)

    assert "время ожидания" in str(exc.value).lower()


# ─────────────────────────────────────────────────────────────
# Ключ и валидация ввода
# ─────────────────────────────────────────────────────────────

def test_missing_key_raises_value_error(monkeypatch):
    """Без ключа клиент не создаётся: ValueError с подсказкой про скрипт."""
    monkeypatch.setattr(dadata_client, "get_dadata_key", lambda: None)

    with pytest.raises(ValueError) as exc:
        DadataClient()

    message = str(exc.value)
    assert "Ключ DaData не найден" in message
    assert "set_dadata_key.py" in message


def test_key_is_taken_from_secret_store(monkeypatch, post_stub):
    """Если ключ не передан, он читается из системного хранилища."""
    monkeypatch.setattr(dadata_client, "get_dadata_key", lambda: TEST_KEY)
    post_stub["response"] = FakeResponse(200, legal_payload())

    data = DadataClient().find_party_by_inn(INN_LEGAL)

    assert data is not None
    assert post_stub["calls"][0]["headers"]["Authorization"] == f"Token {TEST_KEY}"


def test_blank_inn_does_not_reach_api(post_stub):
    """Пустой ИНН — ValueError, запрос к DaData не отправляется."""
    post_stub["response"] = FakeResponse(200, legal_payload())

    with pytest.raises(ValueError):
        make_client().find_party_by_inn("   ")

    assert post_stub["calls"] == []


# ─────────────────────────────────────────────────────────────
# Логи: без ключа, без ИНН и без персональных данных
# ─────────────────────────────────────────────────────────────

def test_key_and_inn_are_not_logged(post_stub, caplog):
    """В логе нет ни ключа, ни ИНН, ни ФИО руководителя."""
    post_stub["response"] = FakeResponse(200, legal_payload())

    with caplog.at_level(logging.DEBUG, logger="core.dadata_client"):
        make_client().find_party_by_inn(INN_LEGAL)

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert TEST_KEY not in text
    assert INN_LEGAL not in text
    assert DIRECTOR not in text


def test_errors_log_only_type_and_status(post_stub, caplog):
    """При сбое в лог попадает статус, но не ИНН и не ключ."""
    post_stub["response"] = FakeResponse(429, None, text="limited")

    with caplog.at_level(logging.DEBUG, logger="core.dadata_client"):
        with pytest.raises(RuntimeError):
            make_client().find_party_by_inn(INN_LEGAL)

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "429" in text
    assert INN_LEGAL not in text
    assert TEST_KEY not in text
    # Длина ИНН логируется — это безопасная величина
    assert f"длина={len(INN_LEGAL)}" in text


# ─────────────────────────────────────────────────────────────
# Статус организации (предупреждение для юрбезопасности)
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    ("ACTIVE", ""),
    ("LIQUIDATING", "ликвидируется"),
    ("LIQUIDATED", "ликвидирована"),
    ("BANKRUPT", "банкротство"),
    ("REORGANIZING", "присоединения"),
])
def test_status_warning_mapping(status, expected):
    text = status_warning(status)
    if not expected:
        assert text == ""
        return
    assert expected in text.lower()
    assert "Проверьте реквизиты" in text


def test_status_warning_is_silent_for_unknown_empty_status():
    assert status_warning("") == ""
    assert status_warning(None) == ""


def test_status_warning_reports_unknown_status():
    """Незнакомый статус не угадываем, но и не молчим."""
    text = status_warning("SOMETHING_NEW")
    assert "SOMETHING_NEW" in text


# ═════════════════════════════════════════════════════════════
# Банк по БИК (findById/bank)
# ═════════════════════════════════════════════════════════════

def bank_payload() -> dict:
    """Синтетический ответ DaData для банка (findById/bank)."""
    return {
        "suggestions": [
            {
                "value": BANK_NAME,
                "unrestricted_value": BANK_NAME,
                "data": {
                    "bic": BIC,
                    "swift": "SABRRUMM",
                    "inn": "7707083893",
                    "kpp": "773601001",
                    "correspondent_account": CORR_ACCOUNT,
                    "name": {
                        "payment": BANK_NAME,
                        "full": 'ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО "СБЕРБАНК РОССИИ"',
                    },
                    "payment_city": "Москва",
                    "state": {"status": "ACTIVE"},
                },
            }
        ]
    }


def fms_payload() -> dict:
    """Синтетический ответ DaData для подразделения ФМС."""
    return {
        "suggestions": [
            {
                "value": FMS_ISSUER,
                "unrestricted_value": FMS_ISSUER,
                "data": {"code": FMS_CODE, "type": "FMS_UNIT"},
            },
            {
                "value": "Отделением УФМС России по г. Москве № 2",
                "data": {"code": FMS_CODE},
            },
        ]
    }


def test_find_bank_by_bic_success(post_stub):
    """Банк: возвращается плоский словарь с нужными приложению полями."""
    post_stub["response"] = FakeResponse(200, bank_payload())

    data = make_client().find_bank_by_bic(BIC)

    assert data is not None
    assert data["bank_name"] == BANK_NAME
    assert data["correspondent_account"] == CORR_ACCOUNT
    assert data["bic"] == BIC
    assert data["swift"] == "SABRRUMM"
    assert data["payment_city"] == "Москва"
    assert data["state"] == "ACTIVE"


def test_find_bank_by_bic_request_shape(post_stub):
    """Запрос идёт на findById/bank с Token-авторизацией и без branch_type."""
    post_stub["response"] = FakeResponse(200, bank_payload())

    make_client().find_bank_by_bic(f"  {BIC}  ")

    assert len(post_stub["calls"]) == 1
    call = post_stub["calls"][0]
    assert call["url"] == dadata_client.FIND_BANK_URL
    assert call["json"] == {"query": BIC}
    assert call["headers"]["Authorization"] == f"Token {TEST_KEY}"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["timeout"] == 10


def test_find_bank_by_bic_not_found(post_stub):
    """Пустой suggestions — «банк не найден», это не ошибка."""
    post_stub["response"] = FakeResponse(200, {"suggestions": []})

    assert make_client().find_bank_by_bic(BIC) is None


def test_find_bank_by_bic_suggestion_without_data(post_stub):
    """Если data нет, название берётся из value подсказки."""
    post_stub["response"] = FakeResponse(200, {
        "suggestions": [{"value": BANK_NAME}],
    })

    data = make_client().find_bank_by_bic(BIC)

    assert data is not None
    assert data["bank_name"] == BANK_NAME
    assert data["correspondent_account"] == ""


@pytest.mark.parametrize("bic", ["", "   ", "12345678", "0445252255",
                                 "04452522a", "0445-25225"])
def test_find_bank_by_bic_invalid_bic(post_stub, bic):
    """Не 9 цифр — ValueError, запрос к API не уходит."""
    post_stub["response"] = FakeResponse(200, bank_payload())

    with pytest.raises(ValueError):
        make_client().find_bank_by_bic(bic)

    assert post_stub["calls"] == []


def test_find_bank_by_bic_401(post_stub):
    """401 — сообщение про неверный или отозванный ключ."""
    post_stub["response"] = FakeResponse(401, None, text="unauthorized")

    with pytest.raises(RuntimeError) as exc:
        make_client().find_bank_by_bic(BIC)

    message = str(exc.value).lower()
    assert "ключ" in message
    assert "set_dadata_key" in message


def test_find_bank_by_bic_429(post_stub):
    """429 — сообщение про лимит запросов."""
    post_stub["response"] = FakeResponse(429, None, text="too many requests")

    with pytest.raises(RuntimeError) as exc:
        make_client().find_bank_by_bic(BIC)

    message = str(exc.value)
    assert "лимит" in message.lower()
    assert "10 000" in message


def test_find_bank_by_bic_connection_error(post_stub):
    """ConnectionError — понятный текст про соединение."""
    post_stub["exception"] = requests.exceptions.ConnectionError("refused")

    with pytest.raises(RuntimeError) as exc:
        make_client().find_bank_by_bic(BIC)

    assert "соединения" in str(exc.value).lower()


def test_find_bank_by_bic_timeout(post_stub):
    """Timeout — понятный текст про ожидание."""
    post_stub["exception"] = requests.exceptions.Timeout("timed out")

    with pytest.raises(RuntimeError) as exc:
        make_client().find_bank_by_bic(BIC)

    assert "время ожидания" in str(exc.value).lower()


def test_find_bank_by_bic_no_key(monkeypatch):
    """Без ключа клиент не создаётся — ValueError с подсказкой про скрипт."""
    monkeypatch.setattr(dadata_client, "get_dadata_key", lambda: None)

    with pytest.raises(ValueError) as exc:
        DadataClient()

    assert "set_dadata_key.py" in str(exc.value)


def test_bank_and_key_are_not_logged(post_stub, caplog):
    """В логе нет ни ключа, ни БИК, ни названия банка."""
    post_stub["response"] = FakeResponse(200, bank_payload())

    with caplog.at_level(logging.DEBUG, logger="core.dadata_client"):
        make_client().find_bank_by_bic(BIC)

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert TEST_KEY not in text
    assert BIC not in text
    assert BANK_NAME not in text
    # Длина БИК — безопасная величина
    assert f"длина={len(BIC)}" in text


@pytest.mark.parametrize("status,expected", [
    ("ACTIVE", ""),
    ("LIQUIDATING", "ликвидируется"),
    ("LIQUIDATED", "ликвидирован"),
    ("BANKRUPT", "банкротство"),
])
def test_bank_status_warning_mapping(status, expected):
    """Статус банка: формулировка в мужском роде и без слова «организация»."""
    text = bank_status_warning(status)
    if not expected:
        assert text == ""
        return
    assert expected in text.lower()
    assert "банк" in text.lower()
    assert "Проверьте реквизиты" in text


def test_bank_status_warning_uses_bank_word():
    text = bank_status_warning("LIQUIDATED")
    assert "организация" not in text.lower()


# ═════════════════════════════════════════════════════════════
# Подразделение ФМС (suggest/fms_unit)
# ═════════════════════════════════════════════════════════════

def test_suggest_fms_unit_success(post_stub):
    """ФМС: возвращается список подсказок (весь suggestion как есть)."""
    post_stub["response"] = FakeResponse(200, fms_payload())

    data = make_client().suggest_fms_unit(FMS_CODE)

    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["value"] == FMS_ISSUER
    assert data[0]["data"]["code"] == FMS_CODE


def test_suggest_fms_unit_request_shape(post_stub):
    """Запрос идёт на suggest/fms_unit с кодом в query."""
    post_stub["response"] = FakeResponse(200, fms_payload())

    make_client().suggest_fms_unit(FMS_CODE)

    call = post_stub["calls"][0]
    assert call["url"] == dadata_client.SUGGEST_FMS_URL
    assert call["json"] == {"query": FMS_CODE}
    assert call["headers"]["Authorization"] == f"Token {TEST_KEY}"


def test_suggest_fms_unit_normalizes_code(post_stub):
    """«500123» без дефиса превращается в «500-123»."""
    post_stub["response"] = FakeResponse(200, fms_payload())

    make_client().suggest_fms_unit("500123")

    assert post_stub["calls"][0]["json"] == {"query": "500-123"}


def test_suggest_fms_unit_empty(post_stub):
    """Пустой suggestions — пустой список, а не None и не ошибка."""
    post_stub["response"] = FakeResponse(200, {"suggestions": []})

    assert make_client().suggest_fms_unit(FMS_CODE) == []


def test_suggest_fms_unit_suggestions_without_data(post_stub):
    """Подсказки без data (и мусор в списке) не роняют разбор."""
    post_stub["response"] = FakeResponse(200, {
        "suggestions": [{"value": FMS_ISSUER}, "мусор", {"data": {"code": "1"}}],
    })

    data = make_client().suggest_fms_unit(FMS_CODE)

    assert [item.get("value") for item in data] == [FMS_ISSUER, None]
    assert all(isinstance(item, dict) for item in data)


@pytest.mark.parametrize("code", ["", "   ", "12", "1-2", "abc", "500-12"])
def test_suggest_fms_unit_invalid_code(post_stub, code):
    """Меньше 6 цифр — ValueError, запрос не отправляется."""
    post_stub["response"] = FakeResponse(200, fms_payload())

    with pytest.raises(ValueError):
        make_client().suggest_fms_unit(code)

    assert post_stub["calls"] == []


def test_suggest_fms_unit_401(post_stub):
    """401 при запросе ФМС — RuntimeError про ключ."""
    post_stub["response"] = FakeResponse(401, None, text="unauthorized")

    with pytest.raises(RuntimeError) as exc:
        make_client().suggest_fms_unit(FMS_CODE)

    assert "ключ" in str(exc.value).lower()


def test_suggest_fms_unit_connection_error(post_stub):
    """ConnectionError при запросе ФМС — RuntimeError про соединение."""
    post_stub["exception"] = requests.exceptions.ConnectionError("refused")

    with pytest.raises(RuntimeError) as exc:
        make_client().suggest_fms_unit(FMS_CODE)

    assert "соединения" in str(exc.value).lower()


def test_fms_code_is_not_logged(post_stub, caplog):
    """Код подразделения и его подсказки в лог не попадают — только длина."""
    post_stub["response"] = FakeResponse(200, fms_payload())

    with caplog.at_level(logging.DEBUG, logger="core.dadata_client"):
        make_client().suggest_fms_unit(FMS_CODE)

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert FMS_CODE not in text
    assert FMS_ISSUER not in text
    assert TEST_KEY not in text
    assert f"длина кода={len(FMS_CODE)}" in text


@pytest.mark.parametrize("raw,expected", [
    ("500-123", "500-123"),
    ("500123", "500-123"),
    (" 500123 ", "500-123"),
    ("500-123 ", "500-123"),
    ("1234567", "1234567"),          # больше цифр — не трогаем
    ("500 123", "500-123"),          # пробел тоже приводим к дефису
])
def test_normalize_fms_code_variants(raw, expected):
    assert normalize_fms_code(raw) == expected


def test_normalize_fms_code_rejects_short_input():
    with pytest.raises(ValueError) as exc:
        normalize_fms_code("500-1")

    assert "цифр" in str(exc.value)
