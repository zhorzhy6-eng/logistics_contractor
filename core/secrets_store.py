#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Безопасное хранение секретов приложения (Шаг 2 задания по безопасности).

Ключ GigaChat больше НЕ хранится в config/settings.json. Он лежит в системном
хранилище учётных данных:

    Windows  — Credential Manager (через библиотеку keyring, backend WinVaultKeyring)
    macOS    — Keychain
    Linux    — Secret Service (gnome-keyring / kwallet)

Служебные имена записи:
    service  = "logistics_contractor"
    username = "gigachat_api_key"

Модуль ничего не логирует о значении ключа: только факт наличия/отсутствия.
"""

import logging
from typing import Optional

from core import audit

logger = logging.getLogger("core.secrets_store")

SERVICE_NAME = "logistics_contractor"
GIGACHAT_KEY_NAME = "gigachat_api_key"

# Единый текст для интерфейса и логов (без самого ключа)
MISSING_KEY_MESSAGE = (
    "Ключ GigaChat не найден. Запустите set_key.py для его добавления."
)
KEYRING_MISSING_MESSAGE = (
    "Библиотека keyring не установлена. Установите её командой: "
    "pip install keyring"
)


def _keyring_module():
    """Возвращает модуль keyring или None, если он не установлен."""
    try:
        import keyring
        return keyring
    except ImportError as e:
        logger.error(f"{KEYRING_MISSING_MESSAGE} ({e})")
        return None


def backend_name() -> str:
    """Имя активного хранилища (для диагностики и интерфейса)."""
    kr = _keyring_module()
    if kr is None:
        return "недоступно"
    try:
        return type(kr.get_keyring()).__name__
    except Exception as e:
        logger.warning(f"Не удалось определить хранилище секретов: {e}")
        return "неизвестно"


def get_gigachat_key() -> Optional[str]:
    """
    Читает ключ GigaChat из системного хранилища.

    :return: ключ или None, если он не сохранён/хранилище недоступно
    """
    kr = _keyring_module()
    if kr is None:
        return None

    try:
        value = kr.get_password(SERVICE_NAME, GIGACHAT_KEY_NAME)
    except Exception as e:
        logger.error(
            f"Не удалось прочитать ключ GigaChat из хранилища: "
            f"{type(e).__name__}: {e}"
        )
        return None

    value = (value or "").strip()
    if not value:
        logger.warning(MISSING_KEY_MESSAGE)
        return None
    return value


def set_gigachat_key(key: str) -> bool:
    """
    Сохраняет ключ GigaChat в системном хранилище.

    :return: True при успехе
    """
    kr = _keyring_module()
    if kr is None:
        return False

    key = (key or "").strip()
    if not key:
        logger.error("Пустой ключ GigaChat — сохранять нечего")
        return False

    try:
        kr.set_password(SERVICE_NAME, GIGACHAT_KEY_NAME, key)
    except Exception as e:
        logger.error(
            f"Не удалось сохранить ключ GigaChat в хранилище: "
            f"{type(e).__name__}: {e}"
        )
        return False

    logger.info(f"Ключ GigaChat сохранён в хранилище ({backend_name()})")
    audit.log_event("gigachat_key_saved", source="keyring")
    return True


def delete_gigachat_key() -> bool:
    """Удаляет ключ GigaChat из системного хранилища."""
    kr = _keyring_module()
    if kr is None:
        return False

    try:
        kr.delete_password(SERVICE_NAME, GIGACHAT_KEY_NAME)
        logger.info("Ключ GigaChat удалён из хранилища")
        audit.log_event("gigachat_key_deleted", source="keyring")
        return True
    except Exception as e:
        # Частая ситуация: записи просто нет
        logger.warning(f"Не удалось удалить ключ GigaChat: {type(e).__name__}: {e}")
        return False


def has_gigachat_key() -> bool:
    """Проверяет наличие ключа, не раскрывая его значение."""
    return bool(get_gigachat_key())


def describe_key_state() -> str:
    """Текст для интерфейса: есть ключ или нет (без самого ключа)."""
    if has_gigachat_key():
        return f"Ключ GigaChat сохранён в системном хранилище ({backend_name()})"
    return MISSING_KEY_MESSAGE
