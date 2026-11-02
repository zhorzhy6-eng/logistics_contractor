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

import base64
import binascii
import logging
import re
import sys as _sys
from typing import Optional

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


# ─────────────────────────────────────────────────────────────
# ФОРМАТ КЛЮЧА GIGACHAT
# ─────────────────────────────────────────────────────────────
# Ключ GigaChat существует в программе в ОДНОМ виде: Base64 от пары
# client_id:client_secret («Authorization key» из кабинета Sber). В заголовке
# он уходит как «Authorization: Basic <ключ>», то есть сервис первым делом
# декодирует его из Base64. Всё, что не Base64, сервис отвергает ответом
# 400 «Can't decode 'Authorization' header» — причём только ПОСЛЕ запроса,
# то есть уже во время распознавания документа.
#
# Поэтому формат проверяется здесь, в единственной точке записи ключа, и ещё
# раз — перед запросом токена. Причина, по которой ключ не годится, называется
# словами: по ней видно, вставили ключ другого сервиса, скопировали половину
# строки или прихватили лишние символы, — значение ключа никуда не попадает.

#: Минимальная длина ключа: Base64 от пары UUID занимает 73 символа, но
#: выравнивание и сокращения бывают разными — 20 символов это уже не «ключ».
GIGACHAT_KEY_MIN_LENGTH = 20

#: Заголовок «Basic », который оператор иногда копирует вместе с ключом.
_BASIC_PREFIX_RE = re.compile(r"^\s*basic\s+", re.IGNORECASE)

#: Классический алфавит Base64 с «+», «/» и хвостовыми «=».
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")

#: Текст отказа для интерфейса и журнала (значение ключа в нём не участвует).
INVALID_GIGACHAT_KEY_MESSAGE = (
    "Ключ GigaChat неверного формата: сервис не сможет его декодировать.\n\n"
    "Ключ GigaChat — это «Authorization key» из личного кабинета "
    "Sber Developer Studio: проект GigaChat API → «Настройки API» → "
    "«Получить ключ». Это строка Base64 из латинских букв, цифр, «+», «/» "
    "и «=», обычно длиной 60–100 символов.\n\n"
    "Вставьте ключ заново: «Настройки» → «Ключи API…» "
    "(или python set_key.py)."
)


def normalize_gigachat_key(value: Optional[str]) -> str:
    """
    Ключ в том виде, в каком он уходит в заголовок Authorization.

    Оператор копирует ключ по-разному: с заголовком «Basic », с переносом
    строки, с пробелами по краям. В хранилище и в заголовок должен попадать
    сам Base64, поэтому префикс снимается, а все пробелы и переводы строк
    удаляются.
    """
    text = _BASIC_PREFIX_RE.sub("", str(value or ""))
    return "".join(text.split())


def gigachat_key_problem(value: Optional[str]) -> Optional[str]:
    """
    Почему значение не может быть ключом GigaChat (None — формат верный).

    Проверяется ФОРМА, а не содержимое: ключ — секрет, разбирать его на части
    незачем. Текст причины значения не содержит, поэтому его можно писать
    в журнал и показывать оператору.
    """
    text = normalize_gigachat_key(value)
    if not text:
        return "значение пустое"
    if text.casefold().startswith("sk-"):
        return ("значение начинается с «sk-» — так выглядят ключи "
                "OpenAI-совместимых сервисов, а не Authorization key GigaChat")
    if "://" in text:
        return "значение похоже на ссылку, а не на ключ"
    if len(text) < GIGACHAT_KEY_MIN_LENGTH:
        return (f"ключ короче {GIGACHAT_KEY_MIN_LENGTH} символов — "
                "похоже, скопирована только часть строки")
    if not _BASE64_RE.match(text):
        return ("в значении есть символы вне алфавита Base64 (допустимы "
                "латинские буквы, цифры, «+», «/» и «=»)")
    try:
        raw = base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
    except (binascii.Error, ValueError):
        return "значение не декодируется как Base64"
    if not raw:
        return "значение декодируется в пустую строку"
    return None


def is_valid_gigachat_key(value: Optional[str]) -> bool:
    """Годится ли значение как ключ GigaChat по форме (содержимое не проверяется)."""
    return gigachat_key_problem(value) is None


def gigachat_key_error_text(value: Optional[str]) -> str:
    """
    Полный текст отказа «ключ неверного формата» (пустая строка — формат верный).

    Собирается в одном месте, чтобы окно, консоль и клиент GigaChat говорили
    оператору одно и то же.
    """
    problem = gigachat_key_problem(value)
    if problem is None:
        return ""
    return f"{INVALID_GIGACHAT_KEY_MESSAGE}\n\nПричина: {problem}."


def __getattr__(name):
    """
    Ленивый доступ к модулю `core.audit` как к атрибуту `audit`.

    Имя обязано читаться как атрибут модуля: тесты подменяют его через
    `monkeypatch.setattr(secrets_store.audit, "log_event", …)`, а такая
    подмена работает именно с атрибутом. При этом импортировать `core` на
    уровне модуля нельзя — это замыкает цикл (см. докстроку `_log_audit`).
    """
    if name == "audit":
        from core import audit
        return audit
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _log_audit(event: str) -> None:
    """
    Запись события аудита (без значения ключа — только факт действия).

    Импорт `core.audit` здесь ЛОКАЛЬНЫЙ, а не на уровне модуля, и это
    осознанное решение, а не стилистика. Пакет `core` при импорте поднимает
    `core/__init__.py`, который регистрирует алиасы Этапа 3 и потому сам
    импортирует `infrastructure.*`. Модульный импорт замыкал бы цикл

        store → core/__init__ → infrastructure.api.gigachat → store

    и ронял бы `import infrastructure.secrets.store` первым:
    `ImportError: cannot import name 'MISSING_KEY_MESSAGE' from partially
    initialized module`. Разрыв цикла — в том модуле, который его создаёт
    (тот же приём, что в `services/recognition/import_service.py`, Этап 2).

    Берём `audit` через `getattr` по модулю, а НЕ глобальным именем:
    модульный `__getattr__` обслуживает только доступ к АТРИБУТУ, внутри
    модуля имя `audit` не резолвится (грабля записана в `docs/STATE.md`).
    Заодно это единственный способ увидеть подмену
    `monkeypatch.setattr(secrets_store.audit, "log_event", …)`.
    """
    getattr(_sys.modules[__name__], "audit").log_event(event, source="keyring")


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

    Значение нормализуется (без префикса «Basic» и пробелов): ключ, положенный
    в хранилище старой версией программы вместе с заголовком, должен работать.

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
    normalized = normalize_gigachat_key(value)
    if normalized != value:
        logger.debug("Ключ GigaChat в хранилище записан с пробелами или «Basic» — нормализован")
    return normalized


def set_gigachat_key(key: str) -> bool:
    """
    Сохраняет ключ GigaChat в системном хранилище.

    Формат проверяется ЗДЕСЬ, а не только в вызывающем коде: это единственная
    точка, через которую ключ попадает в программу, — консольный set_key.py,
    окно «Ключи API…» и сторонние скрипты идут через неё. Ключ неверного
    формата в хранилище оказаться не должен: иначе распознавание узнает об
    этом только от сервера и только в момент работы оператора.

    :raises ValueError: значение не может быть ключом GigaChat (пустой ключ —
        False, как и раньше: это «сохранять нечего», а не ошибка формата).
    :return: True при успехе
    """
    kr = _keyring_module()
    if kr is None:
        return False

    key = normalize_gigachat_key(key)
    if not key:
        logger.error("Пустой ключ GigaChat — сохранять нечего")
        return False

    problem = gigachat_key_problem(key)
    if problem is not None:
        # Значение ключа в журнал не попадает — только причина.
        logger.error("Ключ GigaChat не сохранён: %s", problem)
        raise ValueError(gigachat_key_error_text(key))

    try:
        kr.set_password(SERVICE_NAME, GIGACHAT_KEY_NAME, key)
    except Exception as e:
        logger.error(
            f"Не удалось сохранить ключ GigaChat в хранилище: "
            f"{type(e).__name__}: {e}"
        )
        return False

    logger.info(f"Ключ GigaChat сохранён в хранилище ({backend_name()})")
    _log_audit("gigachat_key_saved")
    return True


def delete_gigachat_key() -> bool:
    """Удаляет ключ GigaChat из системного хранилища."""
    kr = _keyring_module()
    if kr is None:
        return False

    try:
        kr.delete_password(SERVICE_NAME, GIGACHAT_KEY_NAME)
        logger.info("Ключ GigaChat удалён из хранилища")
        _log_audit("gigachat_key_deleted")
        return True
    except Exception as e:
        # Частая ситуация: записи просто нет
        logger.warning(f"Не удалось удалить ключ GigaChat: {type(e).__name__}: {e}")
        return False


def has_gigachat_key() -> bool:
    """Проверяет наличие ключа, не раскрывая его значение."""
    return bool(get_gigachat_key())


def describe_key_state() -> str:
    """
    Текст для интерфейса: есть ключ или нет (без самого ключа).

    Ключ, который лежит в хранилище, но не может быть ключом GigaChat (не
    Base64), называется именно так: иначе окно настроек бодро сообщало бы
    «ключ сохранён», а распознавание падало бы на авторизации.
    """
    key = get_gigachat_key()
    if not key:
        return MISSING_KEY_MESSAGE
    problem = gigachat_key_problem(key)
    if problem is not None:
        return (
            f"Ключ GigaChat сохранён, но формат неверный: {problem}. "
            f"Вставьте ключ заново."
        )
    return f"Ключ GigaChat сохранён в системном хранилище ({backend_name()})"


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
    _log_audit(event)
    return True


def _erase_secret(username: str, title: str, event: str) -> bool:
    """Удаляет секрет из системного хранилища."""
    kr = _keyring_module()
    if kr is None:
        return False

    try:
        kr.delete_password(SERVICE_NAME, username)
        logger.info(f"{_title_case(title)} удалён из хранилища")
        _log_audit(event)
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
