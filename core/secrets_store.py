#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Безопасное хранение секретов приложения (Шаг 2 задания по безопасности).

Ключ GigaChat больше НЕ хранится в config/settings.json. Он лежит в системном
хранилище учётных данных:

    Windows  — Credential Manager (через библиотеку keyring, backend WinVaultKeyring)
    macOS    — Keychain
    Linux    — Secret Service (gnome-keyring / kwallet)

Служебные имена записи (один сервис, разные username):
    service  = "logistics_contractor"
    username = "gigachat_api_key"   — ключ GigaChat (set_key.py)
    username = "dadata_api_key"     — ключ DaData  (set_dadata_key.py)
    username = "dadata_secret_key"  — секретный ключ DaData для cleaner-методов
                                      (set_dadata_secret.py)

Модуль ничего не логирует о значении ключа: только факт наличия/отсутствия.
"""

import logging
from typing import Optional

from core import audit

logger = logging.getLogger("core.secrets_store")

SERVICE_NAME = "logistics_contractor"
GIGACHAT_KEY_NAME = "gigachat_api_key"
#: Ключ DaData — другая запись того же сервиса (set_dadata_key.py).
DADATA_KEY_NAME = "dadata_api_key"
#: Секретный ключ DaData — отдельная запись того же сервиса
#: (set_dadata_secret.py, cleaner-методы; клиент пока не использует).
DADATA_SECRET_KEY_NAME = "dadata_secret_key"

# Единый текст для интерфейса и логов (без самого ключа)
MISSING_KEY_MESSAGE = (
    "Ключ GigaChat не найден. Запустите set_key.py для его добавления."
)
MISSING_DADATA_KEY_MESSAGE = (
    "Ключ DaData не найден. Запустите set_dadata_key.py для его добавления."
)
MISSING_DADATA_SECRET_MESSAGE = (
    "Секретный ключ DaData не найден. Запустите set_dadata_secret.py "
    "для его добавления."
)
KEYRING_MISSING_MESSAGE = (
    "Библиотека keyring не установлена. Установите её командой: "
    "pip install keyring"
)
KEYRING_UNAVAILABLE_MESSAGE = (
    "Windows Credential Manager недоступен: системное хранилище секретов "
    "не отвечает. Ключ не сохранён — в файлы проекта он не записывается."
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


# ─────────────────────────────────────────────────────────────
# Ключ DaData (подсказки по ИНН — core/dadata_client.py)
# ─────────────────────────────────────────────────────────────
# Хранится ровно там же, где ключ GigaChat: service = SERVICE_NAME,
# username = DADATA_KEY_NAME. Функции GigaChat выше не изменялись.

def keyring_available() -> bool:
    """Доступно ли системное хранилище (Windows Credential Manager)."""
    return _keyring_module() is not None


def _title_case(title: str) -> str:
    """
    «ключ DaData» → «Ключ DaData» для логов.

    str.capitalize() здесь не подходит: он приводит к нижнему регистру
    весь остаток строки и превращает «DaData» в «dadata».
    """
    text = str(title or "")
    return text[:1].upper() + text[1:]


def _read_secret(username: str, title: str,
                 missing_message: str) -> Optional[str]:
    """
    Читает секрет из системного хранилища.

    Значение секрета не логируется: в лог попадают только факт наличия
    и тип ошибки хранилища.
    """
    kr = _keyring_module()
    if kr is None:
        return None

    try:
        value = kr.get_password(SERVICE_NAME, username)
    except Exception as e:
        logger.error(
            f"Не удалось прочитать {title} из хранилища: "
            f"{type(e).__name__}: {e}"
        )
        return None

    value = (value or "").strip()
    if not value:
        logger.warning(missing_message)
        return None
    return value


def _write_secret(username: str, key: str, title: str, event: str) -> bool:
    """Сохраняет секрет в системном хранилище (значение не логируется)."""
    kr = _keyring_module()
    if kr is None:
        return False

    key = (key or "").strip()
    if not key:
        logger.error(f"Пустой {title} — сохранять нечего")
        return False

    try:
        kr.set_password(SERVICE_NAME, username, key)
    except Exception as e:
        logger.error(
            f"Не удалось сохранить {title} в хранилище: "
            f"{type(e).__name__}: {e}"
        )
        return False

    logger.info(f"{_title_case(title)} сохранён в хранилище ({backend_name()})")
    audit.log_event(event, source="keyring")
    return True


def _erase_secret(username: str, title: str, event: str) -> bool:
    """Удаляет секрет из системного хранилища."""
    kr = _keyring_module()
    if kr is None:
        return False

    try:
        kr.delete_password(SERVICE_NAME, username)
        logger.info(f"{_title_case(title)} удалён из хранилища")
        audit.log_event(event, source="keyring")
        return True
    except Exception as e:
        # Частая ситуация: записи просто нет
        logger.warning(f"Не удалось удалить {title}: {type(e).__name__}: {e}")
        return False


def get_dadata_key() -> Optional[str]:
    """
    Читает ключ DaData из системного хранилища.

    :return: ключ или None, если он не сохранён/хранилище недоступно
    """
    return _read_secret(
        DADATA_KEY_NAME, "ключ DaData", MISSING_DADATA_KEY_MESSAGE
    )


def set_dadata_key(key: str) -> bool:
    """
    Сохраняет ключ DaData в системном хранилище.

    Резервного варианта с записью в файл нет и не будет: если keyring
    недоступен, возвращается False.

    :return: True при успехе
    """
    return _write_secret(DADATA_KEY_NAME, key, "ключ DaData", "dadata_key_saved")


def delete_dadata_key() -> bool:
    """Удаляет ключ DaData из системного хранилища."""
    return _erase_secret(DADATA_KEY_NAME, "ключ DaData", "dadata_key_deleted")


def has_dadata_key() -> bool:
    """Проверяет наличие ключа DaData, не раскрывая его значение."""
    return bool(get_dadata_key())


def describe_dadata_key_state() -> str:
    """Текст для интерфейса: есть ключ DaData или нет (без самого ключа)."""
    if has_dadata_key():
        return f"Ключ DaData сохранён в системном хранилище ({backend_name()})"
    return MISSING_DADATA_KEY_MESSAGE


# ─────────────────────────────────────────────────────────────
# Секретный ключ DaData (cleaner-методы)
# ─────────────────────────────────────────────────────────────
# Отдельная запись того же сервиса: service = SERVICE_NAME,
# username = DADATA_SECRET_KEY_NAME. Сохраняется скриптом
# set_dadata_secret.py и пока не используется ни одним клиентом —
# это задел под отдельную задачу по clean/passport.

def get_dadata_secret() -> Optional[str]:
    """
    Читает секретный ключ DaData из системного хранилища.

    :return: ключ или None, если он не сохранён/хранилище недоступно
    """
    return _read_secret(
        DADATA_SECRET_KEY_NAME, "секретный ключ DaData",
        MISSING_DADATA_SECRET_MESSAGE,
    )


def set_dadata_secret(key: str) -> bool:
    """
    Сохраняет секретный ключ DaData в системном хранилище.

    Резервного варианта с записью в файл нет: если keyring недоступен,
    возвращается False.

    :return: True при успехе
    """
    return _write_secret(
        DADATA_SECRET_KEY_NAME, key, "секретный ключ DaData",
        "dadata_secret_saved",
    )


def delete_dadata_secret() -> bool:
    """Удаляет секретный ключ DaData из системного хранилища."""
    return _erase_secret(
        DADATA_SECRET_KEY_NAME, "секретный ключ DaData",
        "dadata_secret_deleted",
    )


def has_dadata_secret() -> bool:
    """Проверяет наличие секретного ключа, не раскрывая его значение."""
    return bool(get_dadata_secret())


def describe_dadata_secret_state() -> str:
    """Текст для интерфейса: есть секретный ключ или нет (без самого ключа)."""
    if has_dadata_secret():
        return (
            "Секретный ключ DaData сохранён в системном хранилище "
            f"({backend_name()})"
        )
    return MISSING_DADATA_SECRET_MESSAGE
