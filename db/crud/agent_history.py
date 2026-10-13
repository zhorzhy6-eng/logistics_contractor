#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
История диалога с ИИ-агентом «Максимилиан» (таблица agent_history).

Задание, часть F. Таблица отдельная и на справочники не влияет: агент
пишет только сюда. Сессия — один запуск программы (``session_id``), поэтому
новая сессия начинает историю заново, а старые можно удалить целиком.

Роль записи:

  ``user``      — сообщение оператора;
  ``assistant`` — ответ Максимилиана;
  ``tool``      — вызов инструмента и его результат (``tool_name``).

В ``content`` попадают те же данные, что видит оператор в панели. Для
инструментов это уже безопасное представление (см. core/agent/tools.py:
значения полей не раскрываются сверх нужного).
"""

import logging
import sqlite3
from typing import Any, Dict, List, Optional

from db.connection import get_connection

logger = logging.getLogger("db.crud.agent_history")

#: Разрешённые роли записи истории.
ROLES = ("user", "assistant", "tool")

#: Таблица создаётся и здесь: миграция зовёт эту функцию, а тесты и
#: инструменты — её же, чтобы не дублировать SQL в двух файлах.
CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS agent_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        tool_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

INDEXES = (
    ("idx_agent_history_session", "agent_history(session_id, id)"),
)

#: Индекс лежит и в общем списке db/schema.py::INDEXES: так он попадает
#: в общую проверку схемы. Здесь он нужен затем, чтобы ensure_table()
#: поднимал таблицу целиком (таблица + индекс) и в тестах, и в миграции.
MAIN_INDEX_NAME = "idx_agent_history_session"


def ensure_table(conn: Optional[sqlite3.Connection] = None) -> bool:
    """
    Создаёт таблицу и индекс, если их нет (идемпотентно).

    :return: True, если таблица доступна (создана или уже была)
    """
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(CREATE_TABLE_SQL)
        for name, target in INDEXES:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
        conn.commit()
        return True
    except sqlite3.DatabaseError as e:
        logger.warning(f"Не удалось подготовить таблицу agent_history: {e}")
        return False
    finally:
        if own_connection:
            conn.close()


def append(
    session_id: str,
    role: str,
    content: str,
    tool_name: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[int]:
    """
    Добавляет запись в историю.

    :return: ID записи или None, если записать не удалось (история агента
        не должна ломать диалог: панель продолжает работать в памяти)
    """
    role_value = str(role or "").strip()
    if role_value not in ROLES:
        logger.warning(f"agent_history: неизвестная роль {role!r} — запись пропущена")
        return None

    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO agent_history (session_id, role, content, tool_name) "
            "VALUES (?, ?, ?, ?)",
            (str(session_id), role_value, str(content or ""), tool_name),
        )
        record_id = cursor.lastrowid
        if own_connection:
            conn.commit()
        return record_id
    except sqlite3.DatabaseError as e:
        logger.warning(f"agent_history: запись не сохранена ({e})")
        return None
    finally:
        if own_connection:
            conn.close()


def load(
    session_id: str,
    limit: int = 20,
    conn: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """
    Последние ``limit`` записей сессии — в порядке от старых к новым.

    Читается «хвост» (ORDER BY id DESC + LIMIT), а затем разворачивается:
    при длинной истории грузить её целиком незачем.
    """
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, session_id, role, content, tool_name, created_at "
            "FROM agent_history WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (str(session_id), max(1, int(limit))),
        )
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in reversed(rows)]
    except sqlite3.DatabaseError as e:
        logger.warning(f"agent_history: история не прочитана ({e})")
        return []
    finally:
        if own_connection:
            conn.close()


def count(session_id: str, conn: Optional[sqlite3.Connection] = None) -> int:
    """Сколько записей в сессии (0 — сессия пуста или таблицы нет)."""
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM agent_history WHERE session_id = ?",
            (str(session_id),),
        )
        return int(cursor.fetchone()[0] or 0)
    except sqlite3.DatabaseError as e:
        logger.warning(f"agent_history: подсчёт не удался ({e})")
        return 0
    finally:
        if own_connection:
            conn.close()


def clear_session(session_id: str, conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Удаляет историю ОДНОЙ сессии («Очистить историю» в панели).

    :return: сколько записей удалено (-1 — не удалось)
    """
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM agent_history WHERE session_id = ?", (str(session_id),)
        )
        removed = cursor.rowcount
        if own_connection:
            conn.commit()
        logger.info(f"agent_history: сессия очищена, удалено записей {removed}")
        return int(removed)
    except sqlite3.DatabaseError as e:
        logger.warning(f"agent_history: очистка не удалась ({e})")
        return -1
    finally:
        if own_connection:
            conn.close()


def purge_other_sessions(
    keep_session_id: str,
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    """
    Удаляет истории ВСЕХ сессий, кроме текущей.

    Удаляются только записи агента: справочники, договоры и адреса эта
    функция не трогает (задание, часть F.4).
    """
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM agent_history WHERE session_id <> ?",
            (str(keep_session_id),),
        )
        removed = cursor.rowcount
        if own_connection:
            conn.commit()
        logger.info(f"agent_history: старые сессии удалены, записей {removed}")
        return int(removed)
    except sqlite3.DatabaseError as e:
        logger.warning(f"agent_history: чистка старых сессий не удалась ({e})")
        return -1
    finally:
        if own_connection:
            conn.close()


__all__ = [
    "CREATE_TABLE_SQL",
    "INDEXES",
    "ROLES",
    "append",
    "clear_session",
    "count",
    "ensure_table",
    "load",
    "purge_other_sessions",
]
