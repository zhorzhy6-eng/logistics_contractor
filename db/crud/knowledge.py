#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Фрагменты правовой базы и их эмбеддинги (RAG-индекс).

Хранилище
---------
Таблицы живут в ТОЙ ЖЕ базе, что и метаданные документов
(``db/legal_metadata.db``): фрагмент без документа смысла не имеет, а один
файл базы проще удалить и пересобрать целиком. Соединение берётся из
db/crud/legal_metadata.py — правила те же (WAL, busy_timeout, py_lower).

Таблицы
-------
``knowledge_chunks`` — фрагменты текста:
    * ``doc_id``      — ссылка на legal_documents.id;
    * ``chunk_index`` — номер фрагмента внутри документа (с 0);
    * ``article``     — статья/пункт, если их удалось выделить из текста;
    * ``text``        — сам фрагмент;
    * ``embedding``   — вектор как BLOB (float32, порядок байт little-endian);
    * ``model``       — какой моделью считан вектор (смена модели = пересчёт).

``knowledge_docs`` — состояние индексации по документу: сколько фрагментов,
сколько из них с векторами, контрольная сумма исходного текста. По сумме
видно, изменился ли файл с прошлой индексации.

``knowledge_fts`` — полнотекстовый индекс фрагментов (FTS5). Нужен, когда
эмбеддинги недоступны: тогда поиск идёт по словам, а не по смыслу, и это
честно помечается в результате (``mode = "lexical"``).
"""

import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional, Sequence

from db.crud import legal_metadata

logger = logging.getLogger("db.crud.knowledge")

#: Токенизатор FTS5: Unicode + снятие диакритики (регистронезависимость кириллицы).
TOKENIZER = "unicode61 remove_diacritics 2"

#: Имя FTS-таблицы фрагментов.
FTS_TABLE = "knowledge_fts"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id      TEXT NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    article     TEXT NOT NULL DEFAULT '',
    part        TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL DEFAULT '',
    char_start  INTEGER NOT NULL DEFAULT 0,
    char_end    INTEGER NOT NULL DEFAULT 0,
    chars       INTEGER NOT NULL DEFAULT 0,
    embedding   BLOB,
    dim         INTEGER NOT NULL DEFAULT 0,
    model       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_doc_index
    ON knowledge_chunks(doc_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON knowledge_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_article ON knowledge_chunks(article);

CREATE TABLE IF NOT EXISTS knowledge_docs (
    doc_id     TEXT PRIMARY KEY,
    chunks     INTEGER NOT NULL DEFAULT 0,
    chars      INTEGER NOT NULL DEFAULT 0,
    embedded   INTEGER NOT NULL DEFAULT 0,
    checksum   TEXT NOT NULL DEFAULT '',
    model      TEXT NOT NULL DEFAULT '',
    indexed_at TEXT NOT NULL DEFAULT ''
);
"""

_fts_state: Optional[bool] = None

#: Пути к базам, для которых схема индекса уже создана в этом процессе.
#: Проверка нужна, потому что функции чтения получают ГОТОВОЕ соединение и
#: сами схему не создают — а без неё первый же поиск по пустой базе падал бы
#: с «no such table: knowledge_chunks».
_schema_ready: set = set()

#: Слова и числа: остальное — разделители (то же правило, что в db/fts.py).
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

#: Окончания, которые отбрасываются при поиске (от длинных к коротким).
#:
#: Полноценная морфология здесь не нужна: запрос «просрочку» должен находить
#: «просрочка» и «просрочки». Усечение до основы плюс поиск по префиксу это
#: даёт: «просрочк*» находит все три формы, а точная форма — только одну.
_ENDINGS = (
    "ованиями", "ования", "ениями", "ения", "ания", "ании", "ость", "ости",
    "ыми", "ими", "ого", "его", "ому", "ему", "ыми", "ах", "ях", "ам", "ям",
    "ов", "ев", "ие", "ия", "ии", "ей", "ой", "ый", "ий", "ая", "яя", "ое",
    "ее", "ые", "ую", "юю", "ом", "ем", "ла", "ло", "ли", "ть", "ет", "ут",
    "ют", "ит", "ат", "ят", "ал", "ил", "ыл", "ся", "сь", "у", "ю", "а", "я",
    "о", "е", "ы", "и", "й", "ь",
)

#: Минимальная длина основы после усечения окончания.
_MIN_STEM = 4

#: Служебные слова: в поиске не участвуют (иначе «за», «для», «или» тянут
#: на себя внимание полнотекстового индекса).
_STOP_WORDS = frozenset({
    "для", "или", "при", "над", "под", "про", "без", "что", "как", "это",
    "его", "её", "ее", "их", "все", "всё", "так", "там", "тут", "них",
    "который", "которая", "которые", "которых", "если", "чтобы", "когда",
    "какие", "какой", "какая", "каким", "чем", "тем", "уже", "ещё", "еще",
    "быть", "есть", "был", "была", "были", "будет", "можно", "нужно", "надо",
})


# ─────────────────────────────────────────────────────────────
# Схема
# ─────────────────────────────────────────────────────────────

def fts5_available(conn: sqlite3.Connection) -> bool:
    """
    Есть ли в этой сборке SQLite поддержка FTS5 (результат кэшируется).

    :param conn: соединение с базой;
    :return: True, если FTS5 работает.
    """
    global _fts_state
    if _fts_state is None:
        try:
            conn.execute("CREATE VIRTUAL TABLE temp.__knowledge_fts_probe USING fts5(x)")
            conn.execute("DROP TABLE temp.__knowledge_fts_probe")
            _fts_state = True
        except sqlite3.DatabaseError as e:
            logger.warning(f"FTS5 недоступен ({e}); поиск пойдёт перебором по словам")
            _fts_state = False
    return bool(_fts_state)


def reset_availability_cache() -> None:
    """Сбрасывает кэш доступности FTS5 (для тестов)."""
    global _fts_state
    _fts_state = None


def get_connection() -> sqlite3.Connection:
    """
    Соединение с базой правовой базы (то же, что у метаданных).

    :return: sqlite3.Connection с настроенными PRAGMA и функцией py_lower.
    """
    return legal_metadata.get_connection()


def init_db(conn: Optional[sqlite3.Connection] = None) -> None:
    """
    Создаёт таблицы RAG-индекса (идемпотентно).

    :param conn: готовое соединение; None — открыть своё и закрыть после.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        legal_metadata.init_db(conn)
        conn.executescript(SCHEMA_SQL)
        if fts5_available(conn):
            try:
                conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
                    f"text, article, content='knowledge_chunks', content_rowid='id', "
                    f"tokenize=\"{TOKENIZER}\""
                    f")"
                )
            except sqlite3.DatabaseError as e:
                logger.warning(f"Полнотекстовый индекс фрагментов не создан: {e}")
        conn.commit()
        _schema_ready.add(legal_metadata.legal_db_path())
    finally:
        if own:
            conn.close()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """
    Гарантирует, что схема индекса есть (один раз на путь к базе).

    Вызывается функциями чтения, которые получают готовое соединение:
    поиск по ещё не индексированной базе должен возвращать пустой список,
    а не падать с «no such table».

    :param conn: готовое соединение.
    """
    if legal_metadata.legal_db_path() in _schema_ready:
        return
    init_db(conn)


# ─────────────────────────────────────────────────────────────
# Запись
# ─────────────────────────────────────────────────────────────

def _pack_vector(vector: Sequence[float]) -> bytes:
    """
    Упаковывает вектор в BLOB (float32, little-endian).

    :param vector: последовательность чисел;
    :return: байты.
    """
    import numpy as np

    return np.asarray(vector, dtype="<f4").tobytes()


def unpack_vector(blob: Optional[bytes], dim: int = 0) -> List[float]:
    """
    Разбирает BLOB обратно в список чисел.

    :param blob: байты вектора (None — пустой список);
    :param dim: ожидаемая размерность (для проверки целостности);
    :return: список чисел.
    """
    if not blob:
        return []
    import numpy as np

    if len(blob) % 4:  # float32 — четыре байта; остаток означает порчу данных
        logger.warning(f"Вектор повреждён: длина {len(blob)} не кратна 4 байтам")
        return []
    vector = np.frombuffer(blob, dtype="<f4")
    if dim and len(vector) != dim:
        logger.warning(f"Вектор повреждён: ожидалось {dim}, получено {len(vector)}")
        return []
    return [float(value) for value in vector]


def replace_document_chunks(doc_id: str, chunks: Sequence[Dict[str, Any]],
                            conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Заменяет все фрагменты документа (старые удаляются).

    :param doc_id: идентификатор документа в legal_documents;
    :param chunks: список словарей «text, article, part, char_start, char_end,
        embedding, model»;
    :param conn: готовое соединение; None — открыть своё.
    :return: сколько фрагментов записано.
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    try:
        delete_document_chunks(doc_id, conn=conn)
        total_chars = 0
        embedded = 0
        model = ""
        for index, chunk in enumerate(chunks):
            text = str(chunk.get("text") or "")
            vector = chunk.get("embedding")
            blob = _pack_vector(vector) if vector else None
            dim = len(vector) if vector else 0
            if vector:
                embedded += 1
                model = str(chunk.get("model") or model)
            cursor = conn.execute(
                "INSERT INTO knowledge_chunks "
                "(doc_id, chunk_index, article, part, text, char_start, char_end, chars, "
                " embedding, dim, model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                (
                    str(doc_id), index,
                    str(chunk.get("article") or ""), str(chunk.get("part") or ""),
                    text, int(chunk.get("char_start") or 0), int(chunk.get("char_end") or 0),
                    len(text), blob, dim, str(chunk.get("model") or ""),
                ),
            )
            if fts5_available(conn):
                conn.execute(
                    f"INSERT INTO {FTS_TABLE}(rowid, text, article) VALUES (?, ?, ?)",
                    (cursor.lastrowid, text, str(chunk.get("article") or "")),
                )
            total_chars += len(text)
        conn.execute(
            "INSERT INTO knowledge_docs (doc_id, chunks, chars, embedded, checksum, model, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(doc_id) DO UPDATE SET "
            "chunks=excluded.chunks, chars=excluded.chars, embedded=excluded.embedded, "
            "checksum=excluded.checksum, model=excluded.model, indexed_at=excluded.indexed_at",
            (str(doc_id), len(chunks), total_chars, embedded,
             str(chunks[0].get("checksum") or "") if chunks else "", model),
        )
        conn.commit()
        return len(chunks)
    finally:
        if own:
            conn.close()


def delete_document_chunks(doc_id: str, conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Удаляет фрагменты документа (и строки полнотекстового индекса).

    :param doc_id: идентификатор документа;
    :param conn: готовое соединение; None — открыть своё.
    :return: сколько фрагментов удалено.
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    try:
        rows = conn.execute("SELECT id FROM knowledge_chunks WHERE doc_id = ?",
                            (str(doc_id),)).fetchall()
        if fts5_available(conn) and rows:
            for row in rows:
                conn.execute(f"DELETE FROM {FTS_TABLE} WHERE rowid = ?", (row["id"],))
        cursor = conn.execute("DELETE FROM knowledge_chunks WHERE doc_id = ?", (str(doc_id),))
        conn.execute("DELETE FROM knowledge_docs WHERE doc_id = ?", (str(doc_id),))
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        if own:
            conn.close()


def set_embeddings(doc_id: str, vectors: Dict[int, Sequence[float]], model: str,
                   conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Дописывает вектора к уже сохранённым фрагментам.

    Нужно, когда индексация текста и расчёт эмбеддингов идут порциями:
    текст уже лежит в базе, а вектора приходят позже.

    :param doc_id: идентификатор документа;
    :param vectors: «номер фрагмента → вектор»;
    :param model: имя модели;
    :param conn: готовое соединение; None — открыть своё.
    :return: сколько векторов записано.
    """
    if not vectors:
        return 0
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    try:
        written = 0
        for index, vector in vectors.items():
            blob = _pack_vector(vector)
            cursor = conn.execute(
                "UPDATE knowledge_chunks SET embedding = ?, dim = ?, model = ? "
                "WHERE doc_id = ? AND chunk_index = ?",
                (blob, len(vector), str(model), str(doc_id), int(index)),
            )
            written += int(cursor.rowcount or 0)
        conn.execute(
            "UPDATE knowledge_docs SET embedded = ("
            "  SELECT COUNT(*) FROM knowledge_chunks WHERE doc_id = ? AND embedding IS NOT NULL"
            ") WHERE doc_id = ?",
            (str(doc_id), str(doc_id)),
        )
        conn.commit()
        return written
    finally:
        if own:
            conn.close()


def clear_all(conn: Optional[sqlite3.Connection] = None) -> None:
    """
    Полностью очищает индекс (документы на диске не трогает).

    :param conn: готовое соединение; None — открыть своё.
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    try:
        conn.execute("DELETE FROM knowledge_chunks")
        conn.execute("DELETE FROM knowledge_docs")
        if fts5_available(conn):
            conn.execute(f"DELETE FROM {FTS_TABLE}")
        conn.commit()
    finally:
        if own:
            conn.close()


# ─────────────────────────────────────────────────────────────
# Чтение
# ─────────────────────────────────────────────────────────────

def document_state(doc_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    """
    Состояние индексации одного документа.

    :param doc_id: идентификатор документа;
    :param conn: готовое соединение; None — открыть своё.
    :return: словарь состояния или None.
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    else:
        ensure_schema(conn)
    try:
        row = conn.execute("SELECT * FROM knowledge_docs WHERE doc_id = ?",
                           (str(doc_id),)).fetchone()
        return dict(row) if row else None
    finally:
        if own:
            conn.close()


def iter_chunks(doc_id: Optional[str] = None, with_vectors: bool = False,
                conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """
    Читает фрагменты индекса.

    :param doc_id: только по этому документу; None — все;
    :param with_vectors: разбирать ли BLOB в список чисел (иначе только dim);
    :param conn: готовое соединение; None — открыть своё.
    :return: список словарей фрагментов.
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    else:
        ensure_schema(conn)
    sql = ("SELECT c.*, d.title AS doc_title, d.short_title AS doc_short, d.number AS doc_number, "
           "       d.doc_date AS doc_date, d.url AS doc_url, d.category AS doc_category "
           "FROM knowledge_chunks c LEFT JOIN legal_documents d ON d.id = c.doc_id")
    params: List[Any] = []
    if doc_id:
        sql += " WHERE c.doc_id = ?"
        params.append(str(doc_id))
    sql += " ORDER BY c.doc_id, c.chunk_index"
    try:
        rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if with_vectors:
                item["vector"] = unpack_vector(item.pop("embedding", None), item.get("dim") or 0)
            else:
                item.pop("embedding", None)
            result.append(item)
        return result
    finally:
        if own:
            conn.close()


def stem_token(token: str) -> str:
    """
    Отбрасывает окончание слова (лёгкое усечение, без словаря).

    :param token: слово в нижнем регистре;
    :return: основа слова (само слово, если окончание не подошло).
    """
    for ending in _ENDINGS:
        if token.endswith(ending) and len(token) - len(ending) >= _MIN_STEM:
            return token[: len(token) - len(ending)]
    return token


def token_group(token: str) -> str:
    """
    Строит FTS-условие для одного слова: точная форма или его основа.

    :param token: слово запроса;
    :return: фрагмент MATCH-запроса.
    """
    stem = stem_token(token)
    if stem == token:
        return f'"{token}"*'
    return f'("{token}"* OR "{stem}"*)'


def query_tokens(query: str) -> List[str]:
    """
    Разбирает строку запроса на значимые слова.

    :param query: строка поиска;
    :return: список слов (без коротких и служебных).
    """
    return [token for token in _WORD_RE.findall(str(query or "").lower())
            if len(token) > 2 and token not in _STOP_WORDS]


def _token_frequency(conn: sqlite3.Connection, token: str) -> int:
    """
    Сколько фрагментов содержат слово (или его основу).

    :param conn: соединение;
    :param token: слово;
    :return: число фрагментов.
    """
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH ?",
            (token_group(token),),
        ).fetchone()
        return int(row["n"] or 0)
    except sqlite3.DatabaseError as e:
        logger.debug(f"Частота слова «{token}» не посчитана: {e}")
        return 0


def _match_rows(conn: sqlite3.Connection, match: str, limit: int,
                strict: bool) -> List[Dict[str, Any]]:
    """
    Выполняет MATCH-запрос и приводит строки к общему виду.

    :param conn: соединение;
    :param match: выражение для MATCH;
    :param limit: сколько строк вернуть;
    :param strict: признак «строгий запрос» (для отчёта);
    :return: список фрагментов.
    """
    rows = conn.execute(
        "SELECT c.*, d.title AS doc_title, d.short_title AS doc_short, "
        "       d.number AS doc_number, d.doc_date AS doc_date, d.url AS doc_url, "
        "       bm25(knowledge_fts) AS rank "
        "FROM knowledge_fts JOIN knowledge_chunks c ON c.id = knowledge_fts.rowid "
        "LEFT JOIN legal_documents d ON d.id = c.doc_id "
        "WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT ?",
        (match, int(limit)),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item.pop("embedding", None)
        item["score"] = 1.0 / (1.0 + abs(float(item.pop("rank", 0.0))))
        item["strict"] = bool(strict)
        result.append(item)
    return result


def search_lexical(query: str, limit: int = 5,
                   conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """
    Полнотекстовый поиск по фрагментам (когда векторов нет).

    Запрос строится в три ступени — от строгого к мягкому:

      1. **самые редкие слова**: берутся 3 (затем 2, затем 1) слова с
         наименьшей частотой в индексе и соединяются через AND. Требовать
         ВСЕ слова нельзя: фрагменты нарезаны по 900 символов, и слова
         одного вопроса часто оказываются в соседних кусках;
      2. **основа слова**: «просрочку» ищется и как «просрочк*», иначе
         падежные формы не находятся;
      3. **любое из слов** (OR) — если редких слов в индексе нет вовсе.

    Если FTS5 недоступен, работает перебор по вхождению слов (LIKE).

    :param query: строка поиска;
    :param limit: сколько фрагментов вернуть;
    :param conn: готовое соединение; None — открыть своё.
    :return: список фрагментов с полем ``score``.
    """
    term = (query or "").strip()
    if not term:
        return []
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    else:
        ensure_schema(conn)
    try:
        tokens = query_tokens(term)
        if fts5_available(conn) and tokens:
            ranked = sorted(tokens, key=lambda token: _token_frequency(conn, token))
            for count in (3, 2, 1):
                if len(ranked) < count:
                    continue
                match = " AND ".join(token_group(token) for token in ranked[:count])
                try:
                    rows = _match_rows(conn, match, int(limit), strict=count > 1)
                except sqlite3.DatabaseError as e:
                    logger.warning(f"FTS-поиск не сработал ({e}); иду перебором")
                    rows = []
                if rows:
                    return rows
            try:
                return _match_rows(conn, " OR ".join(token_group(token) for token in ranked),
                                   int(limit), strict=False)
            except sqlite3.DatabaseError as e:
                logger.warning(f"FTS-поиск не сработал ({e}); иду перебором")
        return _search_like(conn, tokens or [term.lower()], int(limit))
    finally:
        if own:
            conn.close()


def _search_like(conn: sqlite3.Connection, tokens: List[str], limit: int) -> List[Dict[str, Any]]:
    """
    Резервный поиск: подсчёт вхождений слов средствами SQLite.

    :param conn: соединение;
    :param tokens: слова запроса;
    :param limit: сколько фрагментов вернуть;
    :return: список фрагментов с полем ``score``.
    """
    if not tokens:
        return []
    stems = []
    for token in tokens:
        stems.append(token)
        stem = stem_token(token)
        if stem != token:
            stems.append(stem)
    conditions = " OR ".join("py_lower(c.text) LIKE ?" for _ in stems)
    params = [f"%{stem}%" for stem in stems]
    sql = (
        "SELECT c.*, d.title AS doc_title, d.short_title AS doc_short, d.number AS doc_number, "
        "       d.doc_date AS doc_date, d.url AS doc_url "
        f"FROM knowledge_chunks c LEFT JOIN legal_documents d ON d.id = c.doc_id "
        f"WHERE {conditions} LIMIT ?"
    )
    params.append(max(1, limit) * 20)
    rows = conn.execute(sql, params).fetchall()
    scored = []
    for row in rows:
        text = str(row["text"] or "").lower()
        score = sum(text.count(stem) for stem in stems)
        item = dict(row)
        item.pop("embedding", None)
        item["score"] = float(score)
        item["strict"] = False
        scored.append(item)
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[: max(1, limit)]


def stats(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """
    Сводка по индексу для интерфейса.

    :param conn: готовое соединение; None — открыть своё.
    :return: словарь «документов / фрагментов / с векторами / размер / модель».
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    else:
        ensure_schema(conn)
    try:
        documents = conn.execute("SELECT COUNT(*) AS n FROM knowledge_docs").fetchone()["n"]
        chunks = conn.execute("SELECT COUNT(*) AS n FROM knowledge_chunks").fetchone()["n"]
        embedded = conn.execute(
            "SELECT COUNT(*) AS n FROM knowledge_chunks WHERE embedding IS NOT NULL"
        ).fetchone()["n"]
        chars = conn.execute(
            "SELECT COALESCE(SUM(chars), 0) AS n FROM knowledge_chunks"
        ).fetchone()["n"]
        size = conn.execute(
            "SELECT COALESCE(SUM(LENGTH(embedding)), 0) AS n FROM knowledge_chunks"
        ).fetchone()["n"]
        model = conn.execute(
            "SELECT model FROM knowledge_docs WHERE model <> '' ORDER BY indexed_at DESC LIMIT 1"
        ).fetchone()
        last = conn.execute("SELECT MAX(indexed_at) AS d FROM knowledge_docs").fetchone()["d"]
        return {
            "documents": int(documents or 0),
            "chunks": int(chunks or 0),
            "embedded": int(embedded or 0),
            "text_chars": int(chars or 0),
            "bytes": int(size or 0),
            "model": (model["model"] if model else ""),
            "indexed_at": last or "",
            "db_path": legal_metadata.legal_db_path(),
        }
    finally:
        if own:
            conn.close()


def indexed_doc_ids(conn: Optional[sqlite3.Connection] = None) -> List[str]:
    """
    Идентификаторы документов, уже попавших в индекс.

    :param conn: готовое соединение; None — открыть своё.
    :return: список идентификаторов.
    """
    own = conn is None
    conn = conn or get_connection()
    if own:
        init_db(conn)
    else:
        ensure_schema(conn)
    try:
        rows = conn.execute("SELECT doc_id FROM knowledge_docs").fetchall()
        return [str(row["doc_id"]) for row in rows]
    finally:
        if own:
            conn.close()


__all__ = [
    "FTS_TABLE",
    "SCHEMA_SQL",
    "TOKENIZER",
    "clear_all",
    "delete_document_chunks",
    "document_state",
    "ensure_schema",
    "fts5_available",
    "get_connection",
    "indexed_doc_ids",
    "init_db",
    "iter_chunks",
    "replace_document_chunks",
    "reset_availability_cache",
    "query_tokens",
    "search_lexical",
    "set_embeddings",
    "stats",
    "stem_token",
    "token_group",
    "unpack_vector",
]
