#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Полнотекстовый поиск FTS5 для регистронезависимого поиска по кириллице.

Проблема, которую решает модуль
-------------------------------
В SQLite `LIKE` регистронезависим только для латиницы (ASCII), поэтому
запрос «мурманск» не находил «Мурманск», а «складская» — «Складская».
FTS5 с токенизатором `unicode61 remove_diacritics 2` приводит кириллицу
к нижнему регистру при индексации и при поиске, поэтому регистр перестаёт
иметь значение.

Как устроено
------------
* **external content**: FTS-таблицы не хранят копию данных
  (`content='address_book'`, `content_rowid='id'`), а читают их из основной
  таблицы. Дублируется только поисковый индекс.
* **без триггеров**: синхронизация идёт из кода —
  - одиночные записи: `replace_row()` / `delete_row()` рядом с INSERT/UPDATE/DELETE;
  - массовый импорт: `sync_content()` доливает ТОЛЬКО новые строки
    (`WHERE id > last_id`), поэтому повторный импорт ничего не индексирует;
  - `rebuild()` — полная перестройка индекса вручную.
* **отсутствие FTS5 не ломает поиск**: если виртуальные таблицы недоступны
  (или запрос не подходит для MATCH), вызывающий код использует старый LIKE.

Что FTS5 не умеет (и почему есть резервный путь)
------------------------------------------------
* MATCH ищет по ТОКЕНАМ, а не по подстрокам: «ольский» не найдёт «Кольский»
  (спасает LIKE-резерв — поведение как раньше);
* «ё» не сворачивается в «е» (проверено на SQLite 3.50.4), поэтому в запрос
  добавляются варианты написания: «королев» ищет и «Королёв»;
* пунктуация (`ул. Складская, д.7`, `пр-т`, `()`) — синтаксическая ошибка
  MATCH, поэтому ввод санитизируется в набор quoted-токенов.
"""

import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("db.fts")

#: Токенизатор: Unicode + снятие диакритики (регистронезависимость кириллицы).
TOKENIZER = "unicode61 remove_diacritics 2"

#: FTS-таблица -> (таблица-источник, индексируемые колонки)
FTS_TABLES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "fts_addresses": ("address_book", ("address", "city")),
    "fts_drivers": ("drivers", ("full_name",)),
    "fts_customers": (
        "customers",
        ("full_name", "short_name", "inn", "director_name"),
    ),
    "fts_carriers": (
        "carriers",
        ("full_name", "short_name", "inn", "director_name"),
    ),
}

#: Служебная таблица «ключ-значение» (в ней лежит last_id индексации).
META_TABLE = "app_meta"

#: Сколько вариантов написания через «е»/«ё» допускается для одного токена.
MAX_YO_VARIANTS = 8

#: Слова и числа: всё остальное (пунктуация) — разделители.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_HAS_DIGIT_RE = re.compile(r"\d", re.UNICODE)

#: Доступность FTS5 проверяется один раз на процесс (None = ещё не проверяли).
_fts5_state: Optional[bool] = None


# ─────────────────────────────────────────────────────────────
# Доступность и схема
# ─────────────────────────────────────────────────────────────

def fts5_available(conn: sqlite3.Connection) -> bool:
    """Есть ли в этой сборке SQLite поддержка FTS5 (результат кэшируется)."""
    global _fts5_state
    if _fts5_state is None:
        try:
            conn.execute("CREATE VIRTUAL TABLE temp.__fts5_probe USING fts5(x)")
            conn.execute("DROP TABLE temp.__fts5_probe")
            _fts5_state = True
        except sqlite3.DatabaseError as e:
            logger.warning(
                f"FTS5 недоступен ({e}); поиск работает через LIKE, как раньше"
            )
            _fts5_state = False
    return bool(_fts5_state)


def reset_availability_cache() -> None:
    """Сбрасывает кэш доступности FTS5 (для тестов)."""
    global _fts5_state
    _fts5_state = None


def ensure_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {META_TABLE} (key TEXT PRIMARY KEY, value TEXT)"
    )


def ensure_schema(conn: sqlite3.Connection) -> bool:
    """
    Создаёт FTS-таблицы и служебную app_meta (идемпотентно).

    После создания выполняется первичная индексация уже существующих данных.
    :return: True, если FTS готов к работе
    """
    if not fts5_available(conn):
        return False

    ensure_meta_table(conn)

    for name, (source, columns) in FTS_TABLES.items():
        try:
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {name} USING fts5("
                f"{', '.join(columns)}, "
                f"content='{source}', content_rowid='id', "
                f"tokenize=\"{TOKENIZER}\""
                f")"
            )
        except sqlite3.DatabaseError as e:
            logger.warning(f"Не удалось создать {name}: {e}")
            return False

    # Первичная индексация: у только что созданных таблиц last_id ещё нет.
    for name in FTS_TABLES:
        try:
            sync_content(conn, name)
        except sqlite3.DatabaseError as e:
            logger.warning(f"Не удалось проиндексировать {name}: {e}")

    return True


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ?", (name,)
    ).fetchone()
    return row is not None


# ─────────────────────────────────────────────────────────────
# Синхронизация
# ─────────────────────────────────────────────────────────────

def _get_last_id(conn: sqlite3.Connection, fts_name: str) -> Optional[int]:
    ensure_meta_table(conn)
    row = conn.execute(
        f"SELECT value FROM {META_TABLE} WHERE key = ?", (f"fts_last_id:{fts_name}",)
    ).fetchone()
    if not row or row[0] in (None, ""):
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _set_last_id(conn: sqlite3.Connection, fts_name: str, value: int) -> None:
    ensure_meta_table(conn)
    conn.execute(
        f"INSERT INTO {META_TABLE}(key, value) VALUES(?, ?) "
        f"ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (f"fts_last_id:{fts_name}", str(int(value))),
    )


def _max_id(conn: sqlite3.Connection, source: str) -> int:
    value = conn.execute(f"SELECT MAX(id) FROM {source}").fetchone()[0]
    return int(value or 0)


def rebuild(conn: sqlite3.Connection, fts_name: str) -> bool:
    """
    Полная перестройка одного FTS-индекса из таблицы-источника.

    Вызывается вручную (после экзотических операций или для починки),
    при обычном импорте не нужна: см. sync_content().
    """
    if not fts5_available(conn):
        return False
    if fts_name not in FTS_TABLES:
        return False

    source, _ = FTS_TABLES[fts_name]
    try:
        conn.execute(f"INSERT INTO {fts_name}({fts_name}) VALUES('rebuild')")
        _set_last_id(conn, fts_name, _max_id(conn, source))
        logger.info(f"FTS-индекс перестроен: {fts_name}")
        return True
    except sqlite3.DatabaseError as e:
        logger.warning(f"Не удалось перестроить {fts_name}: {e}")
        return False


def rebuild_all(conn: sqlite3.Connection) -> bool:
    """Перестраивает все FTS-индексы (публичная точка входа)."""
    if not fts5_available(conn):
        return False
    ok = True
    for name in FTS_TABLES:
        ok = rebuild(conn, name) and ok
    return ok


def sync_content(conn: sqlite3.Connection, fts_name: str) -> int:
    """
    Догоняет индекс до таблицы-источника, индексируя ТОЛЬКО новые строки.

    Стоимость пропорциональна числу новых записей, а не размеру таблицы:
    повторный импорт тех же адресов не индексирует ничего.

    Вызывается там, где данные пишутся (импорт, инициализация), и только
    если needs_sync() сообщил о расхождении.

    :return: сколько строк добавлено в индекс
    """
    if not fts5_available(conn):
        return 0
    if fts_name not in FTS_TABLES:
        return 0

    source, columns = FTS_TABLES[fts_name]
    last_id = _get_last_id(conn, fts_name)
    max_id = _max_id(conn, source)

    if last_id is None:
        # Индекс ещё ни разу не строился — полная перестройка.
        rebuild(conn, fts_name)
        return max_id

    if max_id > last_id:
        conn.execute(
            f"INSERT INTO {fts_name}(rowid, {', '.join(columns)}) "
            f"SELECT id, {', '.join(columns)} FROM {source} WHERE id > ?",
            (last_id,),
        )
        _set_last_id(conn, fts_name, max_id)
        logger.debug(f"FTS {fts_name}: доиндексировано строк {max_id - last_id}")
        return max_id - last_id

    return 0


def needs_sync(conn: sqlite3.Connection, fts_name: str) -> bool:
    """
    Разошёлся ли индекс с таблицей-источником (только чтение, без записи).

    Так выглядит страховка от правок в обход приложения: если данные
    добавили вручную в SQLite, при следующем поиске индекс догонится.
    Обычные save/update/delete держат last_id актуальным, и здесь
    возвращается False (никакой дополнительной работы).
    """
    if not fts5_available(conn) or fts_name not in FTS_TABLES:
        return False

    source, _ = FTS_TABLES[fts_name]
    last_id = _get_last_id(conn, fts_name)
    if last_id is None:
        return True
    return _max_id(conn, source) > last_id


def _fts_value(value: Any) -> str:
    """FTS5 хранит текст; NULL приводим к пустой строке."""
    return "" if value is None else str(value)


def _bump_last_id(conn: sqlite3.Connection, fts_name: str, rowid: int) -> None:
    """
    Поднимает last_id до rowid, если строка оказалась «новее» отметки.

    Без этого точечная вставка (save_address/save_driver/...) осталась бы
    незамеченной для last_id, и следующий sync_content вставил бы ту же
    строку в индекс повторно — в результатах поиска появились бы дубли.
    """
    last_id = _get_last_id(conn, fts_name)
    if last_id is None or rowid > last_id:
        _set_last_id(conn, fts_name, rowid)


def replace_row(
    conn: sqlite3.Connection,
    fts_name: str,
    rowid: int,
    new_values: Sequence[Any],
    old_values: Optional[Sequence[Any]] = None,
) -> bool:
    """
    Синхронизирует одну строку индекса (INSERT или UPDATE).

    :param old_values: значения ДО изменения. Для FTS5 с external content
        удаление обязано выполняться именно со старыми значениями,
        поэтому вызывающий код читает их заранее.
    """
    if not fts5_available(conn) or fts_name not in FTS_TABLES:
        return False

    _, columns = FTS_TABLES[fts_name]
    placeholders = ", ".join("?" * len(columns))
    column_list = ", ".join(columns)

    try:
        if old_values is not None:
            conn.execute(
                f"INSERT INTO {fts_name}({fts_name}, rowid, {column_list}) "
                f"VALUES('delete', ?, {placeholders})",
                (rowid, *[_fts_value(v) for v in old_values]),
            )
        conn.execute(
            f"INSERT INTO {fts_name}(rowid, {column_list}) VALUES(?, {placeholders})",
            (rowid, *[_fts_value(v) for v in new_values]),
        )
        # last_id держим актуальным, иначе ближайший sync_content посчитает
        # эту строку «новой» и вставит её в индекс второй раз.
        _bump_last_id(conn, fts_name, rowid)
        return True
    except sqlite3.DatabaseError as e:
        logger.warning(f"Не удалось обновить индекс {fts_name} для id={rowid}: {e}")
        return False


def delete_row(
    conn: sqlite3.Connection,
    fts_name: str,
    rowid: int,
    old_values: Sequence[Any],
) -> bool:
    """Убирает строку из индекса (значения нужны из-за external content)."""
    if not fts5_available(conn) or fts_name not in FTS_TABLES:
        return False

    _, columns = FTS_TABLES[fts_name]
    placeholders = ", ".join("?" * len(columns))
    try:
        conn.execute(
            f"INSERT INTO {fts_name}({fts_name}, rowid, {', '.join(columns)}) "
            f"VALUES('delete', ?, {placeholders})",
            (rowid, *[_fts_value(v) for v in old_values]),
        )
        return True
    except sqlite3.DatabaseError as e:
        logger.warning(f"Не удалось удалить id={rowid} из {fts_name}: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# Построение запроса
# ─────────────────────────────────────────────────────────────

def tokens(text: str) -> List[str]:
    """Слова и числа из пользовательского ввода (пунктуация — разделитель)."""
    return _WORD_RE.findall(text or "")


def _yo_variants(token: str, limit: int = MAX_YO_VARIANTS) -> List[str]:
    """
    Варианты написания токена с «е»/«ё».

    FTS5 (unicode61) НЕ считает «ё» диакритикой, поэтому «королев» не находит
    «Королёв». Компенсируем перебором вариантов (с ограничением, чтобы запрос
    не разрастался на словах вроде «переехал»).
    """
    if "е" not in token and "ё" not in token:
        return [token]

    variants = {token}
    if "ё" in token:
        variants.add(token.replace("ё", "е"))

    positions = [i for i, ch in enumerate(token) if ch == "е"]
    if 0 < len(positions) <= 4:
        for mask in range(1, 1 << len(positions)):
            chars = list(token)
            for bit, pos in enumerate(positions):
                if mask & (1 << bit):
                    chars[pos] = "ё"
            variants.add("".join(chars))

    return sorted(variants)[:limit]


def build_match_query(text: str) -> Optional[str]:
    """
    Превращает пользовательский ввод в безопасный MATCH-запрос FTS5.

    Каждый токен берётся в кавычки (пунктуация и кавычки ввода больше не
    ломают синтаксис) и ищется как префикс: «склад» найдёт «Складская».
    Токены соединяются через AND, варианты «е/ё» — через OR.

    :return: строка для MATCH или None, если искать нечего
    """
    words = tokens(text)
    if not words:
        return None

    groups = []
    for word in words:
        variants = _yo_variants(word.lower())
        quoted = " OR ".join(f'"{v}"*' for v in variants)
        groups.append(f"({quoted})" if len(variants) > 1 else quoted)

    return " AND ".join(groups)


def needs_like_fallback(text: str) -> bool:
    """
    Нужен ли заведомо LIKE-путь.

    FTS5 не покроет: запросы с цифрами (паспорт, телефон, ИНН, номер дома —
    там нужен поиск по подстроке) и ввод без букв/цифр вообще.
    """
    if not text or not text.strip():
        return False
    if _HAS_DIGIT_RE.search(text):
        return True
    return not tokens(text)


def match_query_for(text: str) -> Optional[str]:
    """
    MATCH-запрос для текста или None, если FTS использовать нельзя.

    None означает «используй старый LIKE» — это и есть резервный путь.
    """
    if needs_like_fallback(text):
        return None
    return build_match_query(text)
