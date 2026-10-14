#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Память Максимилиана (задание, часть 5): таблицы agent_memory и agent_feedback.

Зачем это нужно
--------------
Оператор каждый день пишет одно и то же: «Ромашка» вместо «ООО «Ромашка»»,
«всегда НДС 20 % для Ромашки», «ИНН с пробелами — убери пробелы». Память
позволяет помощнику не переспрашивать: алиасы, привычки и исправления
складываются в базу и подкладываются в следующий запрос.

Три вида записей (``kind``):

  ``alias``      — «Ромашка» = ООО «Ромашка» (сокращение → полное имя);
  ``habit``      — «всегда НДС 20 % для Ромашки» (правило по умолчанию);
  ``correction`` — «ИНН с пробелами → нормализовать» (как исправлять).

Оценки (``agent_feedback``) — это 👍/👎 на ответ помощника: по ним видно,
какие подсказки полезны, а какие мешают. Отдельная таблица, потому что
оценка относится к СООБЩЕНИЮ истории, а не к записи памяти.

Обе таблицы независимы от справочников: помощник пишет только сюда, и
удаление его памяти (кнопка «Забыть всё») не трогает ни договоры, ни
водителей. CREATE TABLE IF NOT EXISTS безопасен и на чистой, и на рабочей
базе, поэтому в _needs_migration таблицы не вносятся.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from db.connection import get_connection

logger = logging.getLogger("db.crud.agent_memory")

#: Виды записей памяти.
KINDS = ("alias", "habit", "correction")

#: Оценки: 1 — понравилось, -1 — не понравилось.
FEEDBACK_VALUES = (1, -1)

#: Длина значения и ключа: длинные «простыни» в памяти не нужны — они
#: только занимают место в контексте модели.
MAX_KEY_CHARS = 200
MAX_VALUE_CHARS = 400

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS agent_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        source TEXT,
        hits INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )
"""

CREATE_FEEDBACK_SQL = """
    CREATE TABLE IF NOT EXISTS agent_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        message_id INTEGER,
        rating INTEGER NOT NULL,
        comment TEXT,
        created_at TEXT
    )
"""

INDEXES = (
    ("idx_agent_memory_kind", "agent_memory(kind, key)"),
    ("idx_agent_feedback_message", "agent_feedback(message_id)"),
)

#: Индекс лежит и в общем списке db/schema.py::INDEXES: так он попадает в
#: общую проверку схемы. Здесь он нужен затем, чтобы ensure_tables()
#: поднимал таблицы целиком (таблицы + индексы) и в тестах, и в миграции.
MAIN_INDEX_NAME = "idx_agent_memory_kind"


def _now() -> str:
    """Момент записи в ISO (UTC): сортировка и «когда запомнили»."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip(value: Any, limit: int) -> str:
    """Строка без краевых пробелов с ограничением длины."""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def ensure_tables(conn: Optional[sqlite3.Connection] = None) -> bool:
    """
    Создаёт таблицы памяти и индексы, если их нет (идемпотентно).

    :return: True, если таблицы доступны (созданы или уже были)
    """
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(CREATE_TABLE_SQL)
        cursor.execute(CREATE_FEEDBACK_SQL)
        for name, target in INDEXES:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
        conn.commit()
        return True
    except sqlite3.DatabaseError as e:
        logger.warning(f"Не удалось подготовить таблицы памяти агента: {e}")
        return False
    finally:
        if own_connection:
            conn.close()


# ─────────────────────────────────────────────────────────────
# Память: алиасы, привычки, исправления
# ─────────────────────────────────────────────────────────────

def append(
    kind: str,
    key: str,
    value: str,
    source: str = "",
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[int]:
    """
    Записывает факт в память (повторная запись обновляет значение).

    Одинаковые пары «kind + key» не дублируются: оператор может трижды
    сказать «Ромашка — это ООО «Ромашка»», и в памяти должна остаться одна
    запись с последним значением, а не три.

    :return: ID записи или None, если записать не удалось (память не должна
        ломать диалог: агент продолжит работать без неё)
    """
    kind_value = str(kind or "").strip().lower()
    if kind_value not in KINDS:
        logger.warning("agent_memory: неизвестный вид записи %r — пропуск", kind)
        return None

    key_value = _clip(key, MAX_KEY_CHARS)
    value_text = _clip(value, MAX_VALUE_CHARS)
    if not key_value or not value_text:
        logger.debug("agent_memory: пустая запись (%r) — пропуск", kind_value)
        return None

    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM agent_memory WHERE kind = ? AND key = ?",
            (kind_value, key_value),
        )
        row = cursor.fetchone()
        moment = _now()
        if row:
            cursor.execute(
                "UPDATE agent_memory SET value = ?, source = ?, updated_at = ? "
                "WHERE id = ?",
                (value_text, str(source or ""), moment, row[0]),
            )
            record_id = int(row[0])
        else:
            cursor.execute(
                "INSERT INTO agent_memory (kind, key, value, source, hits,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
                (kind_value, key_value, value_text, str(source or ""),
                 moment, moment),
            )
            record_id = int(cursor.lastrowid)
        if own_connection:
            conn.commit()
        logger.info("agent_memory: записано (%s), вид=%s", record_id, kind_value)
        return record_id
    except sqlite3.DatabaseError as e:
        logger.warning(f"agent_memory: запись не сохранена ({e})")
        return None
    finally:
        if own_connection:
            conn.close()


def load(
    kind: str = "",
    limit: int = 50,
    conn: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """
    Записи памяти: свежие сверху (необязательно — только одного вида).

    Пусто — если записей нет или таблицы ещё нет: панель должна работать и
    на базе, где память ни разу не использовалась.
    """
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        kind_value = str(kind or "").strip().lower()
        if kind_value:
            cursor.execute(
                "SELECT id, kind, key, value, source, hits, created_at,"
                "       updated_at FROM agent_memory WHERE kind = ? "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                (kind_value, max(1, int(limit))),
            )
        else:
            cursor.execute(
                "SELECT id, kind, key, value, source, hits, created_at,"
                "       updated_at FROM agent_memory "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                (max(1, int(limit)),),
            )
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except sqlite3.DatabaseError as e:
        logger.warning(f"agent_memory: записи не прочитаны ({e})")
        return []
    finally:
        if own_connection:
            conn.close()


def count(conn: Optional[sqlite3.Connection] = None) -> int:
    """Сколько записей в памяти (0 — пусто или таблицы нет)."""
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM agent_memory")
        return int(cursor.fetchone()[0] or 0)
    except sqlite3.DatabaseError as e:
        logger.warning(f"agent_memory: подсчёт не удался ({e})")
        return 0
    finally:
        if own_connection:
            conn.close()


def touch(record_ids: Sequence[int],
          conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Отмечает, что записи пригодились (счётчик hits +1).

    Нужно для отбора «top-5 релевантных»: то, что оператор подтверждал
    раньше, полезнее случайной записи. Ошибка счётчика ничего не ломает.
    """
    ids = [int(item) for item in (record_ids or ()) if str(item).strip()]
    if not ids:
        return 0

    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        placeholders = ", ".join("?" for _ in ids)
        cursor.execute(
            f"UPDATE agent_memory SET hits = COALESCE(hits, 0) + 1 "
            f"WHERE id IN ({placeholders})",
            tuple(ids),
        )
        updated = cursor.rowcount
        if own_connection:
            conn.commit()
        return int(updated)
    except sqlite3.DatabaseError as e:
        logger.warning(f"agent_memory: счётчик обращений не обновлён ({e})")
        return 0
    finally:
        if own_connection:
            conn.close()


def delete(record_id: int, conn: Optional[sqlite3.Connection] = None) -> bool:
    """
    Удаляет одну запись памяти (кнопка «Удалить» в настройках).

    :return: True, если запись была и удалена
    """
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM agent_memory WHERE id = ?", (int(record_id),))
        removed = cursor.rowcount
        if own_connection:
            conn.commit()
        logger.info("agent_memory: запись %s удалена (%s)", record_id, removed)
        return bool(removed)
    except (sqlite3.DatabaseError, TypeError, ValueError) as e:
        logger.warning(f"agent_memory: запись не удалена ({e})")
        return False
    finally:
        if own_connection:
            conn.close()


def clear(kind: str = "", conn: Optional[sqlite3.Connection] = None) -> int:
    """
    «Забыть всё»: удаляет записи памяти (необязательно — только одного вида).

    :return: сколько записей удалено (-1 — не удалось)
    """
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        kind_value = str(kind or "").strip().lower()
        if kind_value:
            cursor.execute("DELETE FROM agent_memory WHERE kind = ?", (kind_value,))
        else:
            cursor.execute("DELETE FROM agent_memory")
        removed = cursor.rowcount
        if own_connection:
            conn.commit()
        logger.info("agent_memory: очищено записей %s", removed)
        return int(removed)
    except sqlite3.DatabaseError as e:
        logger.warning(f"agent_memory: очистка не удалась ({e})")
        return -1
    finally:
        if own_connection:
            conn.close()


# ─────────────────────────────────────────────────────────────
# Оценки ответов (agent_feedback)
# ─────────────────────────────────────────────────────────────

def add_feedback(
    rating: int,
    *,
    session_id: str = "",
    message_id: Optional[int] = None,
    comment: str = "",
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[int]:
    """
    Записывает оценку ответа помощника (``1`` — 👍, ``-1`` — 👎).

    Доска оценок нужна для отбора полезных подсказок: 👎 на подсказку — это
    сигнал не повторять её. Оценка ни на что не влияет напрямую и ничего не
    удаляет.

    :return: ID записи или None, если записать не удалось
    """
    try:
        value = int(rating)
    except (TypeError, ValueError):
        logger.warning("agent_feedback: оценка %r не число — пропуск", rating)
        return None
    if value not in FEEDBACK_VALUES:
        logger.warning("agent_feedback: оценка %r недопустима — пропуск", rating)
        return None

    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO agent_feedback (session_id, message_id, rating,"
            " comment, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(session_id or ""), message_id, value,
             _clip(comment, MAX_VALUE_CHARS), _now()),
        )
        record_id = int(cursor.lastrowid)
        if own_connection:
            conn.commit()
        logger.info("agent_feedback: оценка %s записана", value)
        return record_id
    except sqlite3.DatabaseError as e:
        logger.warning(f"agent_feedback: оценка не сохранена ({e})")
        return None
    finally:
        if own_connection:
            conn.close()


def load_feedback(
    limit: int = 50,
    conn: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """Последние оценки (свежие сверху) — для настроек и диагностики."""
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, session_id, message_id, rating, comment, created_at "
            "FROM agent_feedback ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except sqlite3.DatabaseError as e:
        logger.warning(f"agent_feedback: оценки не прочитаны ({e})")
        return []
    finally:
        if own_connection:
            conn.close()


def feedback_stats(conn: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
    """Сколько оценок всего, сколько «за» и сколько «против»."""
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COALESCE(SUM(CASE WHEN rating > 0 THEN 1 ELSE 0 END), 0),"
            "       COALESCE(SUM(CASE WHEN rating < 0 THEN 1 ELSE 0 END), 0),"
            "       COUNT(*) FROM agent_feedback"
        )
        row = cursor.fetchone() or (0, 0, 0)
        return {"liked": int(row[0]), "disliked": int(row[1]),
                "total": int(row[2])}
    except sqlite3.DatabaseError as e:
        logger.warning(f"agent_feedback: статистика не собрана ({e})")
        return {"liked": 0, "disliked": 0, "total": 0}
    finally:
        if own_connection:
            conn.close()


__all__ = [
    "CREATE_FEEDBACK_SQL",
    "CREATE_TABLE_SQL",
    "FEEDBACK_VALUES",
    "INDEXES",
    "KINDS",
    "MAX_KEY_CHARS",
    "MAX_VALUE_CHARS",
    "MAIN_INDEX_NAME",
    "add_feedback",
    "append",
    "clear",
    "count",
    "delete",
    "ensure_tables",
    "feedback_stats",
    "load",
    "load_feedback",
    "touch",
]
