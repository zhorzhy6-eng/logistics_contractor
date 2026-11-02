#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Формат ключа GigaChat — одна проверка на все пути (02.11.2026).

Что случилось
-------------
2 ноября 2026 распознавание перестало работать: на каждую попытку оператор
видел «Ошибка распознавания … Ошибка авторизации GigaChat: 400
{"code":4,"message":"Can't decode 'Authorization' header"}». В системном
хранилище лежал ключ `sk-…` — так выглядят ключи OpenAI-совместимых сервисов,
а не «Authorization key» GigaChat. Программа приняла его молча (set_key.py
проверял только длину и ASCII) и узнала о подмене лишь на первом запросе
токена, уже во время работы.

Что проверяется
---------------
1. Правило формата знает про Base64, префикс «Basic», пробелы, короткую
   строку и ключ другого сервиса (`sk-…`), и НИКОГДА не возвращает само
   значение ключа — только причину.
2. Хранилище не принимает такой ключ: `set_gigachat_key` поднимает
   ValueError, в хранилище ничего не появляется.
3. Клиент GigaChat отказывает ДО сети: ни одного HTTP-запроса, понятный
   текст вместо ответа сервера.
4. `describe_key_state` честно говорит «формат неверный», а не «сохранён».
5. Консольный `set_key.py` отказывается сохранять такой ключ и выходит
   с кодом 1.

Настоящий Credential Manager тесты не трогают: keyring подменён словарём
в памяти (тот же приём, что в test_dadata_secret_store.py).
"""

import base64
import logging

import pytest

from core import secrets_store
from core.gigachat_client import GigaChatClient

#: Настоящий по форме ключ GigaChat: Base64 от пары «client_id:secret».
VALID_KEY = base64.b64encode(
    b"01234567-89ab-cdef-0123-456789abcdef:fedcba98-7654-3210-fedc-ba9876543210"
).decode()

#: Ключ другого сервиса: «sk-» + 32 символа — так выглядят ключи
#: OpenAI-совместимых API. Именно такой и оказался в хранилище 02.11.2026.
WRONG_SERVICE_KEY = "sk-" + "0123456789abcdef0123456789abcdef"


class FakeKeyring:
    """Мини-замена keyring: пароли хранятся в словаре в памяти."""

    def __init__(self):
        self.storage = {}

    def get_keyring(self):
        return self

    def get_password(self, service, username):
        return self.storage.get((service, username))

    def set_password(self, service, username, value):
        self.storage[(service, username)] = value

    def delete_password(self, service, username):
        if (service, username) not in self.storage:
            raise KeyError("записи нет")
        del self.storage[(service, username)]


@pytest.fixture
def fake_keyring(monkeypatch):
    """Подменяет keyring так, что настоящий Credential Manager не трогается."""
    fake = FakeKeyring()
    monkeypatch.setattr(secrets_store, "_keyring_module", lambda: fake)
    return fake


# ─────────────────────────────────────────────────────────────
# 1. Правило формата
# ─────────────────────────────────────────────────────────────

def test_real_shaped_key_is_accepted():
    """Ключ, который выглядит как настоящий, проверку проходит."""
    assert secrets_store.gigachat_key_problem(VALID_KEY) is None
    assert secrets_store.is_valid_gigachat_key(VALID_KEY) is True
    assert secrets_store.gigachat_key_error_text(VALID_KEY) == ""


def test_key_without_padding_is_accepted():
    """Ключ без хвостовых «=» не отвергается: выравнивание добавляется само."""
    stripped = VALID_KEY.rstrip("=")
    assert stripped != VALID_KEY
    assert secrets_store.is_valid_gigachat_key(stripped) is True


def test_basic_prefix_and_spaces_are_removed():
    """
    «Basic », пробелы и перевод строки снимаются.

    Так ключ выглядит в буфере обмена: в документации он стоит в заголовке
    «Authorization: Basic <ключ>», и оператор копирует строку целиком.
    """
    assert secrets_store.normalize_gigachat_key("  Basic abc==\n") == "abc=="
    assert secrets_store.normalize_gigachat_key("ab cd\nef") == "abcdef"
    assert secrets_store.normalize_gigachat_key(None) == ""
    assert secrets_store.is_valid_gigachat_key(f" Basic {VALID_KEY} ") is True


@pytest.mark.parametrize("value,expected", [
    ("", "пустое"),
    ("   ", "пустое"),
    ("Basic ", "пустое"),
    (WRONG_SERVICE_KEY, "sk-"),
    ("https://developers.sber.ru/portal/products/gigachat-api", "ссылку"),
    ("abc", "короче"),
    # Больше минимальной длины, но с символами вне алфавита Base64:
    # так выглядит ключ, скопированный с лишним словом или дефисом.
    ("ключ-из-кабинета-сбербанка-очень-длинный", "Base64"),
    ("MDEyMzQ1Njc4OWFiY2RlZg!!!", "Base64"),
    # 17 символов Base64: формально Base64, но ключом быть не может.
    ("MDEyMzQ1Njc4OWFi", "короче"),
])
def test_wrong_values_are_rejected_with_a_reason(value, expected):
    """Плохое значение отвергается, и причина названа словами."""
    problem = secrets_store.gigachat_key_problem(value)

    assert problem is not None, f"значение {value!r} не должно считаться ключом"
    assert expected in problem


def test_problem_text_never_contains_the_value(fake_keyring):
    """
    Ни причина, ни текст отказа не раскрывают сам ключ.

    Ключ — секрет: он уходит в журнал и на экран только в виде «причина».
    """
    problem = secrets_store.gigachat_key_problem(WRONG_SERVICE_KEY)
    text = secrets_store.gigachat_key_error_text(WRONG_SERVICE_KEY)

    assert WRONG_SERVICE_KEY not in problem
    assert WRONG_SERVICE_KEY not in text
    assert "sk-" in problem
    assert "Authorization key" in text
    assert "Ключи API" in text


# ─────────────────────────────────────────────────────────────
# 2. Хранилище не принимает ключ неверного формата
# ─────────────────────────────────────────────────────────────

def test_store_refuses_wrong_service_key(fake_keyring, caplog):
    """`set_gigachat_key` поднимает ValueError, в хранилище пусто."""
    with caplog.at_level(logging.DEBUG, logger="core.secrets_store"):
        with pytest.raises(ValueError) as error:
            secrets_store.set_gigachat_key(WRONG_SERVICE_KEY)

    assert fake_keyring.storage == {}
    assert "Authorization key" in str(error.value)
    # Причина — в журнал, значение — нет.
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "sk-" in logged
    assert WRONG_SERVICE_KEY not in logged


def test_store_refuses_short_key(fake_keyring):
    """Обрезанная строка тоже не ключ: её не сохраняем."""
    with pytest.raises(ValueError):
        secrets_store.set_gigachat_key("MDEyMzQ1Njc4OWFi")

    assert fake_keyring.storage == {}


def test_store_normalizes_key_before_saving(fake_keyring):
    """Префикс «Basic » и пробелы снимаются ещё при записи."""
    assert secrets_store.set_gigachat_key(f" Basic {VALID_KEY} ") is True

    stored = fake_keyring.storage[
        (secrets_store.SERVICE_NAME, secrets_store.GIGACHAT_KEY_NAME)
    ]
    assert stored == VALID_KEY
    assert secrets_store.get_gigachat_key() == VALID_KEY


def test_store_keeps_empty_key_as_false(fake_keyring):
    """Пустой ключ — не ошибка формата, а «сохранять нечего» (как было)."""
    assert secrets_store.set_gigachat_key("") is False
    assert fake_keyring.storage == {}


def test_describe_state_reports_bad_format(fake_keyring):
    """Сохранённый, но негодный ключ называется так, а не «сохранён»."""
    fake_keyring.storage[
        (secrets_store.SERVICE_NAME, secrets_store.GIGACHAT_KEY_NAME)
    ] = WRONG_SERVICE_KEY

    text = secrets_store.describe_key_state()

    assert "формат неверный" in text
    assert "ключ заново" in text
    assert WRONG_SERVICE_KEY not in text


def test_describe_state_reports_good_key(fake_keyring):
    """Годный ключ по-прежнему «сохранён в системном хранилище»."""
    secrets_store.set_gigachat_key(VALID_KEY)

    text = secrets_store.describe_key_state()

    assert "сохранён в системном хранилище" in text
    assert VALID_KEY not in text


# ─────────────────────────────────────────────────────────────
# 3. Клиент GigaChat отказывает ДО сети
# ─────────────────────────────────────────────────────────────

def test_client_refuses_wrong_key_before_network(monkeypatch):
    """
    Ключ `sk-…`: исключение ДО запроса, ни одного обращения к сервису.

    Без этой проверки оператор видел ответ сервера сырым JSON, а запрос
    уходил в сеть — на каждую попытку распознавания.
    """
    calls = []

    class NetworkForbidden:
        def post(self, *args, **kwargs):
            calls.append(args)
            raise AssertionError("запрос к GigaChat не должен был уйти")

    import core.gigachat_client as module
    monkeypatch.setattr(module.requests, "post", NetworkForbidden().post)

    client = GigaChatClient(auth_key=WRONG_SERVICE_KEY, ca_bundle="")

    with pytest.raises(RuntimeError) as error:
        client._get_token()

    assert calls == [], "формат ключа должен проверяться до сети"
    text = str(error.value)
    assert "неверного формата" in text
    assert "sk-" in text
    assert WRONG_SERVICE_KEY not in text


def test_client_has_key_problem_method():
    """У клиента есть публичный ответ на вопрос «что не так с ключом»."""
    good = GigaChatClient(auth_key=VALID_KEY, ca_bundle="")
    bad = GigaChatClient(auth_key=WRONG_SERVICE_KEY, ca_bundle="")

    assert good.key_problem() is None
    assert "sk-" in bad.key_problem()


def test_client_normalizes_key_at_init():
    """Ключ с заголовком «Basic» и пробелами клиент приводит к самому Base64."""
    client = GigaChatClient(auth_key=f" Basic {VALID_KEY} ", ca_bundle="")

    assert client.auth_key == VALID_KEY
    assert client.key_problem() is None


def test_client_wrong_key_does_not_touch_token_cache(monkeypatch):
    """Отказ по формату не трогает кэш токена и не подменяет старый токен."""
    client = GigaChatClient(auth_key=WRONG_SERVICE_KEY, ca_bundle="")
    client._token = None
    client._token_expires = 0

    with pytest.raises(RuntimeError):
        client._get_token()

    assert client._token is None


def test_client_valid_key_sends_basic_header(monkeypatch):
    """Годный ключ уходит в заголовке «Authorization: Basic …» — как нужно API."""
    import core.gigachat_client as module
    import time as time_module

    sent = {}

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "access_token": "TOKEN-OK",
                "expires_at": int((time_module.time() + 1800) * 1000),
            }

    def fake_post(url, headers=None, **kwargs):
        sent["url"] = url
        sent["headers"] = headers or {}
        return Response()

    monkeypatch.setattr(module.requests, "post", fake_post)

    client = GigaChatClient(auth_key=VALID_KEY, ca_bundle="")

    assert client._get_token() == "TOKEN-OK"
    assert sent["headers"]["Authorization"] == f"Basic {VALID_KEY}"
    assert sent["url"] == GigaChatClient.AUTH_URL


def test_client_400_is_explained(monkeypatch):
    """400 от сервиса объясняется словами, а не отдаётся сырым JSON."""
    import core.gigachat_client as module

    class Response:
        status_code = 400
        text = '{"code":4,"message":"Can\'t decode \'Authorization\' header"}'

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(module.requests, "post", lambda *a, **k: Response())

    client = GigaChatClient(auth_key=VALID_KEY, ca_bundle="")

    with pytest.raises(RuntimeError) as error:
        client._get_token()

    text = str(error.value)
    assert "отказал в авторизации" in text
    assert "scope" in text


# ─────────────────────────────────────────────────────────────
# 4. Консольный set_key.py
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def set_key_module(monkeypatch, fake_keyring):
    """Модуль set_key.py без настоящего ввода и без файлового журнала."""
    import set_key

    monkeypatch.setattr(set_key, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(set_key, "_ask", lambda prompt: "y")
    return set_key


def test_script_refuses_wrong_service_key(set_key_module, fake_keyring, capsys,
                                          monkeypatch):
    """`python set_key.py` с ключом `sk-…`: код 1, в хранилище пусто."""
    monkeypatch.setattr(
        set_key_module.getpass, "getpass", lambda prompt: WRONG_SERVICE_KEY
    )

    assert set_key_module.main([]) == 1

    out = capsys.readouterr().out
    assert "ОШИБКА" in out
    assert "sk-" in out
    assert WRONG_SERVICE_KEY not in out
    assert fake_keyring.storage == {}


def test_script_saves_valid_key(set_key_module, fake_keyring, capsys, monkeypatch):
    """Годный ключ сохраняется, код 0, значение обратно читается."""
    monkeypatch.setattr(set_key_module.getpass, "getpass", lambda prompt: VALID_KEY)

    assert set_key_module.main([]) == 0

    assert secrets_store.get_gigachat_key() == VALID_KEY
    assert "Готово" in capsys.readouterr().out


def test_check_mode_reports_bad_format(set_key_module, fake_keyring, capsys):
    """`--check` с негодным ключом говорит об ошибке, а не «OK»."""
    fake_keyring.storage[
        (secrets_store.SERVICE_NAME, secrets_store.GIGACHAT_KEY_NAME)
    ] = WRONG_SERVICE_KEY

    assert set_key_module.main(["--check"]) == 1

    out = capsys.readouterr().out
    assert "ОШИБКА" in out
    assert "Authorization key" in out


def test_check_mode_accepts_good_key(set_key_module, fake_keyring, capsys):
    """`--check` с годным ключом по-прежнему «OK» и код 0."""
    secrets_store.set_gigachat_key(VALID_KEY)

    assert set_key_module.main(["--check"]) == 0
    assert "OK" in capsys.readouterr().out
