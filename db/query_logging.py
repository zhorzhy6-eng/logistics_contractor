#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Логирование запросов к SQLite (Часть 2 задания).

Модуль подключается как фабрика соединения (sqlite3.connect(..., factory=...)),
поэтому покрывает ВСЕ обращения к базе без правок в самих запросах.

В logs/debug.log (DEBUG, только при --debug) попадает:
    какой запрос, сколько строк вернулось, сколько времени занял.

Запрос дольше SLOW_QUERY_SECONDS помечается как SLOW и пишется уровнем
WARNING — такие строки видны и в logs/app.log.

Безопасность: параметры запросов НЕ логируются (в них бывают ФИО и паспорта),
а строковые литералы внутри SQL заменяются на '?' — на случай, если запрос
собран подстановкой, а не параметрами.
"""

import logging
import re
import sqlite3
import time

logger = logging.getLogger("db.queries")

#: Запрос дольше этого времени считается медленным (помечается SLOW)
SLOW_QUERY_SECONDS = 1.0
MAX_SQL_LEN = 160

_WHITESPACE = re.compile(r"\s+")
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_NUMBER_LITERAL = re.compile(r"\b\d{4,}\b")


def safe_sql(sql: str) -> str:
    """Короткое безопасное представление SQL: без значений и переносов строк."""
    text = _WHITESPACE.sub(" ", str(sql)).strip()
    text = _STRING_LITERAL.sub("'?'", text)
    text = _NUMBER_LITERAL.sub("N", text)
    if len(text) > MAX_SQL_LEN:
        text = text[:MAX_SQL_LEN] + "…"
    return text


def _operation(sql: str) -> str:
    return safe_sql(sql).split(" ", 1)[0].upper()


def _log_query(sql: str, elapsed: float, rows=None, note: str = "") -> None:
    rows_part = "" if rows is None else f" | строк: {rows}"
    note_part = f" | {note}" if note else ""
    message = (
        f"SQL {_operation(sql)}: {safe_sql(sql)} "
        f"| время: {elapsed * 1000:.1f} мс{rows_part}{note_part}"
    )
    if elapsed >= SLOW_QUERY_SECONDS:
        logger.warning(f"SLOW {message}")
    else:
        logger.debug(message)


def _rowcount(cursor) -> "int | None":
    """Число затронутых строк для INSERT/UPDATE/DELETE (для SELECT — None)."""
    try:
        count = cursor.rowcount
    except Exception:  # noqa: BLE001 — rowcount есть не всегда
        return None
    if count is None or count < 0:
        return None
    return count


class _QueryLoggingMixin:
    """
    Замер и логирование выполнения запроса.

    Важно: основная часть кода (db/database.py) выполняет запросы через
    `conn.cursor().execute(...)`, а не через `conn.execute(...)`. Поэтому
    перехват нужен именно на курсоре — иначе запросы не логировались бы.
    Connection.execute в CPython сам создаёт курсор и вызывает его execute,
    так что одно переопределение покрывает оба пути и не даёт дублей.
    """

    def execute(self, sql, parameters=()):
        start = time.perf_counter()
        cursor = super().execute(sql, parameters)
        _log_query(sql, time.perf_counter() - start, _rowcount(cursor))
        return cursor

    def executemany(self, sql, seq_of_parameters):
        start = time.perf_counter()
        cursor = super().executemany(sql, seq_of_parameters)
        elapsed = time.perf_counter() - start
        _log_query(sql, elapsed, _rowcount(cursor), note="executemany")
        return cursor

    def executescript(self, sql_script):
        start = time.perf_counter()
        cursor = super().executescript(sql_script)
        elapsed = time.perf_counter() - start
        _log_query(sql_script, elapsed, note="script")
        return cursor


class LoggingCursor(_QueryLoggingMixin, sqlite3.Cursor):
    """Курсор: сообщает, какой запрос выполнен и сколько строк получено."""

    def fetchall(self):
        rows = super().fetchall()
        logger.debug(f"SQL fetchall | получено строк: {len(rows)}")
        return rows

    def fetchone(self):
        row = super().fetchone()
        logger.debug(
            f"SQL fetchone | {'строка получена' if row is not None else 'строк нет'}"
        )
        return row

    def fetchmany(self, size=None):
        rows = super().fetchmany(size) if size is not None else super().fetchmany()
        logger.debug(f"SQL fetchmany | получено строк: {len(rows)}")
        return rows


class LoggingConnection(sqlite3.Connection):
    """
    Соединение, отдающее логирующие курсоры.

    Собственных execute/executemany здесь нет намеренно: их унаследованная
    реализация создаёт LoggingCursor, который и пишет строку в лог.
    """

    def cursor(self, factory=None):
        return super().cursor(factory or LoggingCursor)
