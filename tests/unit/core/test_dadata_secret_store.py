#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты хранения секретного ключа DaData (core/secrets_store.py).

Реальный Windows Credential Manager НЕ используется: keyring подменяется
мини-заменой в памяти, поэтому тесты не читают и не портят настоящие
секреты пользователя. Проверяется только логика обёрток:

  * секрет сохраняется и читается по своему username;
  * секрет не затирает обычный ключ DaData и ключ GigaChat;
  * удаление и has_dadata_secret;
  * describe_dadata_secret_state не раскрывает значение;
  * при недоступном keyring функции возвращают False/None, а не пишут файл;
  * события аудита dadata_secret_saved / dadata_secret_deleted.
"""

import logging

import pytest

from core import secrets_store

TEST_SECRET = "test_secret_key_1234567890abcdefghij"
TEST_API_KEY = "test_api_key_1234567890abcdef"


class FakeKeyring:
    """Мини-замена keyring: пароли хранятся в словаре в памяти."""

    def __init__(self):
        self.storage = {}

    # backend_name() в secrets_store использует get_keyring()
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


@pytest.fixture
def audit_events(monkeypatch):
    """Собирает имена событий аудита вместо записи в logs/audit.log."""
    events = []
    monkeypatch.setattr(
        secrets_store.audit, "log_event",
        lambda action, **fields: events.append(action),
    )
    return events


# ─────────────────────────────────────────────────────────────
# Имена записей
# ─────────────────────────────────────────────────────────────

def test_secret_username_is_stable_and_unique():
    """Секрет живёт в том же сервисе, но под своим username."""
    assert secrets_store.SERVICE_NAME == "logistics_contractor"
    assert secrets_store.DADATA_SECRET_KEY_NAME == "dadata_secret_key"
    assert secrets_store.DADATA_SECRET_KEY_NAME != secrets_store.DADATA_KEY_NAME
    assert secrets_store.DADATA_SECRET_KEY_NAME != secrets_store.GIGACHAT_KEY_NAME


def test_missing_secret_message_points_to_script():
    assert "set_dadata_secret.py" in secrets_store.MISSING_DADATA_SECRET_MESSAGE


# ─────────────────────────────────────────────────────────────
# Сохранение и чтение
# ─────────────────────────────────────────────────────────────

def test_set_and_get_secret(fake_keyring):
    assert secrets_store.set_dadata_secret(TEST_SECRET) is True
    assert secrets_store.get_dadata_secret() == TEST_SECRET
    assert fake_keyring.storage[
        (secrets_store.SERVICE_NAME, secrets_store.DADATA_SECRET_KEY_NAME)
    ] == TEST_SECRET


def test_secret_is_stripped(fake_keyring):
    assert secrets_store.set_dadata_secret(f"  {TEST_SECRET}  ") is True
    assert secrets_store.get_dadata_secret() == TEST_SECRET


def test_secret_does_not_overwrite_other_keys(fake_keyring):
    """Секрет, обычный ключ DaData и ключ GigaChat — три разные записи."""
    assert secrets_store.set_dadata_key(TEST_API_KEY) is True
    assert secrets_store.set_dadata_secret(TEST_SECRET) is True

    assert secrets_store.get_dadata_key() == TEST_API_KEY
    assert secrets_store.get_dadata_secret() == TEST_SECRET
    assert len(fake_keyring.storage) == 2


def test_empty_secret_is_not_saved(fake_keyring):
    assert secrets_store.set_dadata_secret("") is False
    assert secrets_store.set_dadata_secret("   ") is False
    assert secrets_store.set_dadata_secret(None) is False
    assert fake_keyring.storage == {}


def test_blank_stored_value_is_treated_as_missing(fake_keyring):
    fake_keyring.storage[
        (secrets_store.SERVICE_NAME, secrets_store.DADATA_SECRET_KEY_NAME)
    ] = "   "
    assert secrets_store.get_dadata_secret() is None


# ─────────────────────────────────────────────────────────────
# Наличие, удаление и статус
# ─────────────────────────────────────────────────────────────

def test_has_secret_before_and_after(fake_keyring):
    assert secrets_store.has_dadata_secret() is False

    secrets_store.set_dadata_secret(TEST_SECRET)
    assert secrets_store.has_dadata_secret() is True

    secrets_store.delete_dadata_secret()
    assert secrets_store.has_dadata_secret() is False


def test_delete_secret_removes_entry(fake_keyring):
    secrets_store.set_dadata_secret(TEST_SECRET)

    assert secrets_store.delete_dadata_secret() is True
    assert secrets_store.get_dadata_secret() is None
    assert fake_keyring.storage == {}


def test_delete_missing_secret_returns_false(fake_keyring):
    assert secrets_store.delete_dadata_secret() is False


def test_describe_state_does_not_reveal_secret(fake_keyring, caplog):
    with caplog.at_level(logging.DEBUG, logger="core.secrets_store"):
        secrets_store.set_dadata_secret(TEST_SECRET)
        text = secrets_store.describe_dadata_secret_state()

    assert "Секретный ключ DaData сохранён" in text
    assert TEST_SECRET not in text

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert TEST_SECRET not in logged


def test_describe_state_without_secret(fake_keyring):
    text = secrets_store.describe_dadata_secret_state()
    assert text == secrets_store.MISSING_DADATA_SECRET_MESSAGE
    assert "set_dadata_secret.py" in text


# ─────────────────────────────────────────────────────────────
# keyring недоступен: никакого fallback на файл
# ─────────────────────────────────────────────────────────────

def test_functions_are_safe_without_keyring(monkeypatch):
    monkeypatch.setattr(secrets_store, "_keyring_module", lambda: None)

    assert secrets_store.keyring_available() is False
    assert secrets_store.set_dadata_secret(TEST_SECRET) is False
    assert secrets_store.get_dadata_secret() is None
    assert secrets_store.has_dadata_secret() is False
    assert secrets_store.delete_dadata_secret() is False
    assert secrets_store.describe_dadata_secret_state() == (
        secrets_store.MISSING_DADATA_SECRET_MESSAGE
    )


def test_keyring_errors_are_not_raised(monkeypatch, caplog):
    """Ошибка хранилища не вылетает наружу: False/None и запись в лог."""

    class BrokenKeyring:
        def get_keyring(self):
            return self

        def get_password(self, service, username):
            raise RuntimeError("vault locked")

        def set_password(self, service, username, value):
            raise RuntimeError("vault locked")

        def delete_password(self, service, username):
            raise RuntimeError("vault locked")

    monkeypatch.setattr(secrets_store, "_keyring_module", lambda: BrokenKeyring())

    with caplog.at_level(logging.DEBUG, logger="core.secrets_store"):
        assert secrets_store.set_dadata_secret(TEST_SECRET) is False
        assert secrets_store.get_dadata_secret() is None
        assert secrets_store.delete_dadata_secret() is False

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "RuntimeError" in logged      # тип ошибки — можно
    assert TEST_SECRET not in logged     # значение — нельзя


# ─────────────────────────────────────────────────────────────
# Аудит
# ─────────────────────────────────────────────────────────────

def test_audit_events_for_save_and_delete(fake_keyring, audit_events):
    secrets_store.set_dadata_secret(TEST_SECRET)
    secrets_store.delete_dadata_secret()

    assert audit_events == ["dadata_secret_saved", "dadata_secret_deleted"]


def test_no_audit_event_for_empty_secret(fake_keyring, audit_events):
    secrets_store.set_dadata_secret("")

    assert audit_events == []
