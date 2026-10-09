#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Соединение с локальной базой SQLite.

Модуль отвечает ровно за одно: открыть файл базы с нужными PRAGMA.
Схема — в db/schema.py, миграции — в db/migrations.py, запросы — в db/crud/.

Путь к базе
-----------
Владелец пути — фасад `db/database.py` (константа `DB_PATH`): её подменяют
тесты и рабочие инструменты на уровне модуля (`db.database.DB_PATH = …`).
Чтобы подмена продолжала действовать, фасад регистрирует здесь источник
пути (`set_db_path_provider`), а соединение каждый раз спрашивает путь у него
(`current_db_path`). Поэтому модули db/crud/* НЕ читают DB_PATH напрямую.
"""

import logging
import os
import sqlite3

# Логирование SQL-запросов (Часть 2 задания): фабрика соединения пишет
# в logs/debug.log запрос, число строк и время, а медленные запросы (>1 с)
# помечает SLOW в logs/app.log.
from db.query_logging import LoggingConnection

logger = logging.getLogger("db.connection")

#: Путь к файлу базы по умолчанию — рядом с пакетом db.
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contracts.db")

#: Источник пути к базе: функция без аргументов, возвращающая путь.
#: Ставится фасадом db.database (см. модуль-документацию).
_db_path_provider = None


def set_db_path_provider(provider) -> None:
    """
    Регистрирует источник пути к базе (вызывает фасад db.database).

    :param provider: функция без аргументов, возвращающая путь к файлу базы.
    """
    global _db_path_provider
    _db_path_provider = provider


def current_db_path() -> str:
    """
    Путь к файлу базы, с которым работает приложение сейчас.

    Если фасад зарегистрировал источник, берётся путь из него — так
    действует подмена `db.database.DB_PATH` в тестах. Если источника нет
    (модуль соединения импортирован в одиночку), возвращается DB_PATH.
    """
    provider = _db_path_provider
    if provider is not None:
        try:
            path = provider()
        except Exception as e:  # noqa: BLE001 — путь не должен ронять соединение
            logger.warning(f"Не удалось получить путь к базе: {e}")
            path = None
        if path:
            return str(path)
    return DB_PATH


def get_connection(foreign_keys: bool = True) -> sqlite3.Connection:
    """
    Возвращает подключение к базе данных с настроенными PRAGMA.

    Шаг 1 оптимизации производительности:
      * journal_mode=WAL  — читатели не блокируют писателя и наоборот
        (режим сохраняется в файле БД, поэтому включается один раз);
      * busy_timeout=5000 — при блокировке ждать до 5 секунд вместо
        мгновенной ошибки «database is locked»;
      * foreign_keys=ON   — контроль ссылочной целостности (как было).
    """
    conn = sqlite3.connect(current_db_path(), timeout=30, factory=LoggingConnection)

    conn.execute("PRAGMA busy_timeout = 5000")
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")

    # WAL может быть недоступен (например, БД на сетевом диске или
    # файл только для чтения) — тогда работаем в прежнем режиме.
    try:
        mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()
        logger.debug(f"journal_mode: {mode[0] if mode else 'неизвестно'}")
    except sqlite3.DatabaseError as e:
        logger.warning(f"Не удалось включить WAL, работаем в обычном режиме: {e}")

    return conn


__all__ = [
    "DB_PATH",
    "current_db_path",
    "get_connection",
    "set_db_path_provider",
]
