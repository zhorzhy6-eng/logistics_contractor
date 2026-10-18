#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Метаданные правовой базы: документы с pravo.gov.ru.

Модуль владеет ОТДЕЛЬНЫМ файлом базы — ``db/legal_metadata.db``. Основная
база приложения (``contracts.db``) сюда не подключена намеренно:

  * правовая база — это справочник, а не рабочие данные оператора; её можно
    удалить и пересобрать заново, не тронув ни одного договора;
  * схема правовой базы меняется вместе с парсером, а не вместе с формами
    договоров, поэтому её незачем тащить через db/schema.py и db/migrations.py.

Путь к файлу принадлежит ЭТОМУ модулю (``legal_db_path()``): тесты подменяют
его переменной окружения ``LOGISTICS_LEGAL_DB_PATH``, а инструменты — вызовом
``set_legal_db_path()``.

Таблица одна — ``legal_documents``. Ключ ``id`` стабилен и не зависит от
источника: ``eo:<номер опубликования>`` для портала опубликования,
``nd:<номер>`` для банка «Законодательство России», ``cfg:<id>`` для записи
из списка источников. По нему же связываются фрагменты текста
(db/crud/knowledge.py).
"""

import json
import logging
import os
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

from core import app_paths

logger = logging.getLogger("db.crud.legal_metadata")

#: Переменная окружения для подмены пути к базе (тесты, портативные сборки).
LEGAL_DB_ENV = "LOGISTICS_LEGAL_DB_PATH"

#: Имя файла базы метаданных правовой базы (пишется в работе).
DEFAULT_LEGAL_DB_NAME = "legal_metadata.db"

#: Путь к базе по умолчанию — в папке ПРОГРАММЫ, а не рядом с кодом: в .exe
#: код лежит в архиве PyInstaller, и база рядом с ним была бы недоступна для
#: записи (см. core/app_paths.py).
DEFAULT_LEGAL_DB_PATH = str(
    app_paths.writable_path(os.path.join("db", DEFAULT_LEGAL_DB_NAME))
)

#: Явная подмена пути (приоритетнее переменной окружения).
_override_path: Optional[str] = None

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS legal_documents (
    id            TEXT PRIMARY KEY,
    eo_number     TEXT NOT NULL DEFAULT '',
    nd            TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    short_title   TEXT NOT NULL DEFAULT '',
    number        TEXT NOT NULL DEFAULT '',
    doc_date      TEXT NOT NULL DEFAULT '',
    publish_date  TEXT NOT NULL DEFAULT '',
    authority     TEXT NOT NULL DEFAULT '',
    doc_type      TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    url           TEXT NOT NULL DEFAULT '',
    file_url      TEXT NOT NULL DEFAULT '',
    original_path TEXT NOT NULL DEFAULT '',
    text_path     TEXT NOT NULL DEFAULT '',
    text_chars    INTEGER NOT NULL DEFAULT 0,
    checksum      TEXT NOT NULL DEFAULT '',
    ocr_used      INTEGER NOT NULL DEFAULT 0,
    downloaded_at TEXT NOT NULL DEFAULT '',
    updated_at    TEXT NOT NULL DEFAULT '',
    extra         TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS legal_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_legal_docs_category ON legal_documents(category);
CREATE INDEX IF NOT EXISTS idx_legal_docs_publish  ON legal_documents(publish_date);
CREATE INDEX IF NOT EXISTS idx_legal_docs_source   ON legal_documents(source);
CREATE INDEX IF NOT EXISTS idx_legal_docs_number   ON legal_documents(number);
"""

#: Колонки, которые пишет save_document (порядок = порядок в схеме).
DOCUMENT_FIELDS = (
    "id", "eo_number", "nd", "title", "short_title", "number", "doc_date",
    "publish_date", "authority", "doc_type", "category", "source", "url",
    "file_url", "original_path", "text_path", "text_chars", "checksum",
    "ocr_used", "downloaded_at", "updated_at", "extra",
)


# ─────────────────────────────────────────────────────────────
# Путь к базе
# ─────────────────────────────────────────────────────────────

def set_legal_db_path(path: Optional[str]) -> None:
    """
    Задаёт путь к базе правовой базы (None — вернуться к значению по умолчанию).

    :param path: путь к файлу SQLite или None.
    """
    global _override_path
    _override_path = str(path) if path else None


def legal_db_path() -> str:
    """
    Путь к файлу базы правовой базы.

    Приоритет: явная подмена (set_legal_db_path) → переменная окружения
    ``LOGISTICS_LEGAL_DB_PATH`` → ``db/legal_metadata.db`` рядом с проектом.
    """
    if _override_path:
        return _override_path
    env = os.environ.get(LEGAL_DB_ENV)
    if env:
        return env
    return DEFAULT_LEGAL_DB_PATH


# ─────────────────────────────────────────────────────────────
# Соединение
# ─────────────────────────────────────────────────────────────

def _py_lower(value: Any) -> Any:
    """
    Нижний регистр средствами Python — для поиска по кириллице.

    Встроенный SQLite ``lower()`` знает только латиницу: ``lower('ГК РФ')``
    возвращает строку без изменений, поэтому поиск «гражданский» не находил
    бы «Гражданский кодекс». Функция регистрируется в соединении под именем
    ``py_lower`` и используется в запросах поиска.

    :param value: значение из колонки;
    :return: строка в нижнем регистре (не строки возвращаются как есть).
    """
    return value.lower() if isinstance(value, str) else value


def get_connection(path: Optional[str] = None) -> sqlite3.Connection:
    """
    Открывает соединение с базой правовой базы.

    Настройки те же, что у основной базы (см. db/connection.py): WAL, чтобы
    чтение не блокировало запись, busy_timeout и контроль ссылочной
    целостности. Строки возвращаются как sqlite3.Row — по имени колонки.
    Дополнительно регистрируется ``py_lower`` (см. _py_lower).

    :param path: путь к файлу; None — взять legal_db_path().
    """
    target = path or legal_db_path()
    parent = os.path.dirname(os.path.abspath(target))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(target, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.create_function("py_lower", 1, _py_lower)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError as e:  # сетевой диск, только чтение и т. п.
        logger.warning(f"Правовая база: WAL недоступен, обычный режим: {e}")
    return conn


def init_db(conn: Optional[sqlite3.Connection] = None) -> None:
    """
    Создаёт таблицы правовой базы (идемпотентно).

    :param conn: готовое соединение; None — открыть своё и закрыть после.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        if own:
            conn.close()


# ─────────────────────────────────────────────────────────────
# Запись
# ─────────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Преобразует строку БД в словарь (extra разбирается из JSON)."""
    # Именно row.keys(): sqlite3.Row при переборе отдаёт ЗНАЧЕНИЯ, а не имена
    # колонок (в отличие от dict). Подсказка линтера здесь не подходит.
    data = {key: row[key] for key in row.keys()}  # noqa: SIM118
    raw = data.get("extra") or "{}"
    try:
        data["extra"] = json.loads(raw)
    except (TypeError, ValueError):
        data["extra"] = {}
    data["ocr_used"] = bool(data.get("ocr_used"))
    return data


def save_document(document: Dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """
    Сохраняет или обновляет документ (upsert по id).

    Незаполненные поля не затирают уже сохранённые значения: повторный
    проход парсера без текста не должен стирать скачанный ранее файл.

    :param document: словарь с полями DOCUMENT_FIELDS (лишние игнорируются);
    :param conn: готовое соединение; None — открыть своё.
    :return: итоговая запись из базы.
    """
    doc_id = str(document.get("id") or "").strip()
    if not doc_id:
        raise ValueError("Правовая база: у документа не заполнен id")

    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)

    values: Dict[str, Any] = {}
    for field in DOCUMENT_FIELDS:
        if field == "id":
            continue
        if field == "extra":
            extra = document.get("extra") or {}
            values[field] = json.dumps(extra, ensure_ascii=False)
            continue
        if field == "ocr_used":
            values[field] = 1 if document.get(field) else 0
            continue
        if field == "text_chars":
            try:
                values[field] = int(document.get(field) or 0)
            except (TypeError, ValueError):
                values[field] = 0
            continue
        values[field] = "" if document.get(field) is None else str(document.get(field))

    # Пустая строка не должна перетирать уже известное значение.
    columns = list(values.keys())
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(
        f"{col}=CASE WHEN excluded.{col} IN ('', 0) THEN {col} ELSE excluded.{col} END"
        for col in columns
    )
    sql = (
        f"INSERT INTO legal_documents (id, {', '.join(columns)}) "
        f"VALUES (?, {placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [doc_id] + [values[col] for col in columns])
    conn.commit()

    saved = get_document(doc_id, conn=conn)
    if own:
        conn.close()
    return saved or {}


def save_documents(documents: Iterable[Dict[str, Any]], conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Сохраняет пачку документов (одно соединение, одна фиксация на запись).

    :param documents: последовательность словарей-документов;
    :param conn: готовое соединение; None — открыть своё.
    :return: сколько документов записано.
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    count = 0
    try:
        for document in documents:
            save_document(document, conn=conn)
            count += 1
    finally:
        if own:
            conn.close()
    return count


def set_meta(key: str, value: str, conn: Optional[sqlite3.Connection] = None) -> None:
    """
    Записывает служебное значение (например, время последней синхронизации).

    :param key: имя ключа;
    :param value: значение (строка);
    :param conn: готовое соединение; None — открыть своё.
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    conn.execute(
        "INSERT INTO legal_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(key), "" if value is None else str(value)),
    )
    conn.commit()
    if own:
        conn.close()


def get_meta(key: str, default: str = "", conn: Optional[sqlite3.Connection] = None) -> str:
    """Читает служебное значение (пустая строка, если ключа нет)."""
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    try:
        row = conn.execute("SELECT value FROM legal_meta WHERE key = ?", (str(key),)).fetchone()
        return row["value"] if row else default
    finally:
        if own:
            conn.close()


# ─────────────────────────────────────────────────────────────
# Чтение
# ─────────────────────────────────────────────────────────────

def get_document(doc_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """
    Возвращает документ по id.

    :param doc_id: стабильный идентификатор документа;
    :param conn: готовое соединение; None — открыть своё.
    :return: словарь документа или None.
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    try:
        row = conn.execute("SELECT * FROM legal_documents WHERE id = ?", (str(doc_id),)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        if own:
            conn.close()


def find_by_number(number: str, doc_date: str = "", conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """
    Ищет документ по номеру и (необязательно) дате.

    Нужен парсеру, чтобы не скачивать один и тот же акт дважды, даже если
    он попал в список источников под другим идентификатором.

    :param number: номер акта, например «2200» или «259-ФЗ»;
    :param doc_date: дата в ISO (YYYY-MM-DD); пусто — не учитывать;
    :param conn: готовое соединение; None — открыть своё.
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    try:
        if doc_date:
            row = conn.execute(
                "SELECT * FROM legal_documents WHERE number = ? AND doc_date = ? LIMIT 1",
                (str(number), str(doc_date)),
            ).fetchone()
            if row:
                return _row_to_dict(row)
        row = conn.execute(
            "SELECT * FROM legal_documents WHERE number = ? LIMIT 1", (str(number),)
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        if own:
            conn.close()


def list_documents(category: Optional[str] = None, source: Optional[str] = None,
                   only_downloaded: bool = False, limit: Optional[int] = None,
                   conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """
    Список документов базы с необязательными фильтрами.

    :param category: категория из config/legal_sources.json («codes», …);
    :param source: источник («publication», «ips», «manual»);
    :param only_downloaded: только те, у кого есть файл оригинала;
    :param limit: максимум строк;
    :param conn: готовое соединение; None — открыть своё.
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    sql = "SELECT * FROM legal_documents WHERE 1=1"
    params: List[Any] = []
    if category:
        sql += " AND category = ?"
        params.append(str(category))
    if source:
        sql += " AND source = ?"
        params.append(str(source))
    if only_downloaded:
        sql += " AND original_path <> ''"
    sql += " ORDER BY publish_date DESC, id ASC"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    try:
        return [_row_to_dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        if own:
            conn.close()


def search_documents(query: str, limit: int = 50,
                     conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """
    Поиск по названию, номеру и тегам (регистронезависимо).

    :param query: строка поиска;
    :param limit: максимум строк;
    :param conn: готовое соединение; None — открыть своё.
    """
    term = (query or "").strip()
    if not term:
        return list_documents(limit=limit, conn=conn)
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    like = f"%{term.lower()}%"
    sql = (
        "SELECT * FROM legal_documents "
        "WHERE py_lower(title) LIKE ? OR py_lower(number) LIKE ? OR py_lower(short_title) LIKE ? "
        "   OR py_lower(extra) LIKE ? OR py_lower(authority) LIKE ? "
        "ORDER BY publish_date DESC, id ASC LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (like, like, like, like, like, int(limit))).fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        if own:
            conn.close()


def delete_document(doc_id: str, conn: Optional[sqlite3.Connection] = None) -> bool:
    """
    Удаляет запись о документе (файлы на диске не трогает).

    :param doc_id: идентификатор документа;
    :param conn: готовое соединение; None — открыть своё.
    :return: True, если строка была.
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    try:
        cur = conn.execute("DELETE FROM legal_documents WHERE id = ?", (str(doc_id),))
        conn.commit()
        return bool(cur.rowcount)
    finally:
        if own:
            conn.close()


def stats(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """
    Сводка по правовой базе для интерфейса.

    :return: словарь с числом документов, скачанных файлов, объёмом текста,
        разбивкой по категориям и датой последнего обновления.
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM legal_documents").fetchone()["n"]
        downloaded = conn.execute(
            "SELECT COUNT(*) AS n FROM legal_documents WHERE original_path <> ''"
        ).fetchone()["n"]
        chars = conn.execute("SELECT COALESCE(SUM(text_chars), 0) AS n FROM legal_documents").fetchone()["n"]
        last = conn.execute(
            "SELECT MAX(NULLIF(updated_at, '')) AS d FROM legal_documents"
        ).fetchone()["d"]
        by_category = {
            row["category"] or "без категории": row["n"]
            for row in conn.execute(
                "SELECT category, COUNT(*) AS n FROM legal_documents GROUP BY category ORDER BY category"
            ).fetchall()
        }
        return {
            "documents": int(total or 0),
            "downloaded": int(downloaded or 0),
            "text_chars": int(chars or 0),
            "last_update": last or "",
            "by_category": by_category,
            "db_path": legal_db_path(),
        }
    finally:
        if own:
            conn.close()


__all__ = [
    "DEFAULT_LEGAL_DB_PATH",
    "DOCUMENT_FIELDS",
    "LEGAL_DB_ENV",
    "SCHEMA_SQL",
    "delete_document",
    "find_by_number",
    "get_connection",
    "get_document",
    "get_meta",
    "init_db",
    "legal_db_path",
    "list_documents",
    "save_document",
    "save_documents",
    "search_documents",
    "set_legal_db_path",
    "set_meta",
    "stats",
]
