#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Справочник адресов (места загрузки и выгрузки) — таблица address_book.

Поиск идёт двумя путями: FTS5 (регистронезависимо для кириллицы) и прежний
LIKE-резерв (цифры, середина слова, поля салона — они в индекс не входят).
Массовый импорт индекс НЕ трогает: он догоняется при первом поиске
(см. db/crud/search.py), поэтому импорт из Excel остаётся быстрым.
"""

import logging
import sqlite3
from typing import Any, Dict, List, Optional

from core import audit
from core.address_utils import extract_city as _extract_city

from db import fts
from db.connection import get_connection
from db.crud.search import addresses_fts_count, ensure_fts_fresh

logger = logging.getLogger("db.crud.addresses")


def save_address(
    point_type: str,
    address: str,
    date: str = "",
    time_window: str = "",
    salon_name: str = "",
    salon_code: str = "",
    salon_inn: str = "",
    salon_city: str = "",
) -> Optional[int]:
    """
    Сохраняет адрес в справочнике (или обновляет существующий).

    Четыре первых параметра — прежний вызов (point_type, address, date,
    time_window): старые вызовы продолжают работать без правок. Поля
    салона (ШАГ FIX-2.2) — опциональные: пустое значение НЕ затирает то,
    что уже записано в справочнике.
    """
    if not address or not address.strip():
        return None

    address = address.strip()
    date = (date or "").strip()
    time_window = (time_window or "").strip()
    salon_name = (salon_name or "").strip()
    salon_code = (salon_code or "").strip()
    salon_inn = (salon_inn or "").strip()
    salon_city = (salon_city or "").strip()
    city = _extract_city(address)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, usage_count, address, city FROM address_book "
            "WHERE point_type = ? AND address = ?",
            (point_type, address)
        )
        row = cursor.fetchone()
        if row:
            addr_id, usage, old_address, old_city = row
            cursor.execute(
                "UPDATE address_book SET usage_count = usage_count + 1, city = ?, "
                "date = CASE WHEN ? != '' THEN ? ELSE date END, "
                "time_window = CASE WHEN ? != '' THEN ? ELSE time_window END, "
                "salon_name = CASE WHEN ? != '' THEN ? ELSE salon_name END, "
                "salon_code = CASE WHEN ? != '' THEN ? ELSE salon_code END, "
                "salon_inn  = CASE WHEN ? != '' THEN ? ELSE salon_inn END, "
                "salon_city = CASE WHEN ? != '' THEN ? ELSE salon_city END "
                "WHERE id = ?",
                (city, date, date, time_window, time_window,
                 salon_name, salon_name, salon_code, salon_code,
                 salon_inn, salon_inn, salon_city, salon_city, addr_id)
            )
            # FTS5: адрес не менялся, но город мог (правим индекс вручную,
            # без триггеров — см. db/fts.py).
            fts.replace_row(
                conn, "fts_addresses", addr_id,
                (old_address, city), (old_address, old_city),
            )
            conn.commit()
            return addr_id
        else:
            cursor.execute(
                "INSERT INTO address_book "
                "(point_type, address, city, date, time_window, usage_count, "
                " salon_name, salon_code, salon_inn, salon_city) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (point_type, address, city, date, time_window,
                 salon_name, salon_code, salon_inn, salon_city)
            )
            addr_id = cursor.lastrowid
            fts.replace_row(conn, "fts_addresses", addr_id, (address, city))
            conn.commit()
            logger.info(f"Адрес добавлен: ID={addr_id}, город='{city}'")
            return addr_id
    except Exception as e:
        logger.error(f"Ошибка сохранения адреса: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def get_addresses(
    point_type: str,
    search: str = "",
    limit: int = 500,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Возвращает СТРАНИЦУ адресов, отсортированных:
      1) по городу (city) — по алфавиту;
      2) при равенстве города — по всему адресу.

    Поиск по подстроке идёт двумя путями:
      * FTS5 (unicode61) — регистронезависимо для кириллицы: «мурманск»
        находит «Мурманск», поиск идёт по адресу и городу;
      * LIKE — резервный путь: если FTS5 недоступен, запрос содержит цифры
        (номер дома, индекс) или FTS ничего не нашёл (например, ищут середину
        слова «ольский» в «Кольский», чего токенизатор не умеет).
        Сюда же попадает поиск по ПОЛЯМ САЛОНА (ШАГ FIX-2.2): код
        («JMR-A048»), наименование юр. лица и ИНН в FTS-индекс не входят,
        поэтому такие запросы всегда идут через LIKE.

    Шаг 4 оптимизации:
      * жёсткий LIMIT 500 заменён параметрами limit/offset — при росте
        справочника адреса больше не «исчезают»: UI добирает их страницами
        (см. count_addresses и кнопку «Показать ещё»);
      * сортировка идёт по city/address COLLATE NOCASE, что соответствует
        индексу idx_address_book_sort_nocase — SQLite отдаёт первые N строк
        без сортировки всей таблицы (замер: 47.7 ms → 0.8 ms на 250k адресов).

    :param limit: размер страницы
    :param offset: сколько строк пропустить
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # ── Путь 1: FTS5 (быстрый и регистронезависимый) ──
        match_query = fts.match_query_for(search) if search else None
        if match_query and fts.fts5_available(conn):
            try:
                ensure_fts_fresh(conn, "fts_addresses")
                found = addresses_fts_count(cursor, point_type, match_query)
                if found:
                    cursor.execute(
                        "SELECT a.* FROM address_book a "
                        "JOIN fts_addresses f ON f.rowid = a.id "
                        "WHERE fts_addresses MATCH ? AND a.point_type = ? "
                        "ORDER BY a.city COLLATE NOCASE, a.address COLLATE NOCASE "
                        "LIMIT ? OFFSET ?",
                        (match_query, point_type, int(limit), int(offset)),
                    )
                    rows = cursor.fetchall()
                    cols = [desc[0] for desc in cursor.description]
                    logger.debug(
                        f"Поиск адресов (FTS5): {search!r} -> {match_query!r}, "
                        f"найдено {found}"
                    )
                    return [dict(zip(cols, row)) for row in rows]
            except sqlite3.DatabaseError as e:
                logger.warning(f"FTS5-поиск адресов не удался, откат на LIKE: {e}")

        # ── Путь 2: LIKE (как раньше + город + поля салона) ──
        sql = "SELECT * FROM address_book WHERE point_type = ?"
        params: List[Any] = [point_type]

        if search:
            sql += (
                " AND (address LIKE ? OR COALESCE(city, '') LIKE ?"
                " OR COALESCE(salon_name, '') LIKE ?"
                " OR COALESCE(salon_code, '') LIKE ?"
                " OR COALESCE(salon_inn, '') LIKE ?)"
            )
            params.extend([f"%{search}%"] * 5)

        sql += (
            " ORDER BY city COLLATE NOCASE, address COLLATE NOCASE"
            " LIMIT ? OFFSET ?"
        )
        params.extend([int(limit), int(offset)])

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def count_addresses(point_type: str, search: str = "") -> int:
    """
    Общее число адресов по фильтру (для пагинации в справочнике).

    Считает тем же путём, что и get_addresses: FTS5, а при недоступности
    или отсутствии совпадений — LIKE. Поэтому «Показано 500 из N» всегда
    согласовано со списком.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        match_query = fts.match_query_for(search) if search else None
        if match_query and fts.fts5_available(conn):
            try:
                ensure_fts_fresh(conn, "fts_addresses")
                found = addresses_fts_count(cursor, point_type, match_query)
                if found:
                    return found
            except sqlite3.DatabaseError as e:
                logger.warning(f"FTS5-подсчёт адресов не удался, откат на LIKE: {e}")

        if search:
            cursor.execute(
                "SELECT COUNT(*) FROM address_book "
                "WHERE point_type = ? AND (address LIKE ? OR COALESCE(city, '') LIKE ?"
                " OR COALESCE(salon_name, '') LIKE ?"
                " OR COALESCE(salon_code, '') LIKE ?"
                " OR COALESCE(salon_inn, '') LIKE ?)",
                (point_type, *([f"%{search}%"] * 5)),
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM address_book WHERE point_type = ?",
                (point_type,),
            )

        return int(cursor.fetchone()[0] or 0)
    finally:
        conn.close()


def update_address(
    addr_id: int,
    address: str,
    date: str = "",
    time_window: str = "",
    salon_name: str = "",
    salon_code: str = "",
    salon_inn: str = "",
    salon_city: str = "",
) -> bool:
    """
    Обновляет запись справочника адресов.

    Первые четыре параметра — прежний вызов; поля салона (ШАГ FIX-2.2)
    опциональны и по умолчанию пусты — тогда колонка сохраняет прежнее
    значение (CASE WHEN '' THEN старое), чтобы правка адреса из старого
    окна не стирала данные салона.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        city = _extract_city(address.strip())
        salon_name = (salon_name or "").strip()
        salon_code = (salon_code or "").strip()
        salon_inn = (salon_inn or "").strip()
        salon_city = (salon_city or "").strip()

        # Старые значения нужны FTS5: удаление из индекса идёт по ним.
        old = cursor.execute(
            "SELECT address, city FROM address_book WHERE id = ?", (addr_id,)
        ).fetchone()

        cursor.execute(
            "UPDATE address_book SET address = ?, city = ?, date = ?, "
            "time_window = ?, "
            "salon_name = CASE WHEN ? != '' THEN ? ELSE salon_name END, "
            "salon_code = CASE WHEN ? != '' THEN ? ELSE salon_code END, "
            "salon_inn  = CASE WHEN ? != '' THEN ? ELSE salon_inn END, "
            "salon_city = CASE WHEN ? != '' THEN ? ELSE salon_city END "
            "WHERE id = ?",
            (address.strip(), city, date.strip(), time_window.strip(),
             salon_name, salon_name, salon_code, salon_code,
             salon_inn, salon_inn, salon_city, salon_city, addr_id)
        )
        if old:
            fts.replace_row(
                conn, "fts_addresses", addr_id,
                (address.strip(), city), (old[0], old[1]),
            )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления адреса: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def delete_address(addr_id: int) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        old = cursor.execute(
            "SELECT address, city FROM address_book WHERE id = ?", (addr_id,)
        ).fetchone()
        cursor.execute("DELETE FROM address_book WHERE id = ?", (addr_id,))
        if old:
            fts.delete_row(conn, "fts_addresses", addr_id, (old[0], old[1]))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления адреса: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def import_addresses_from_list(point_type: str, addresses: List[Dict[str, str]]) -> int:
    """
    Пакетный импорт адресов (Шаг 3 оптимизации).

    Раньше на каждый адрес выполнялись SELECT + INSERT/UPDATE — при импорте
    из Excel на тысячи строк это тысячи обращений к базе. Теперь одна
    команда executemany с ON CONFLICT по уникальному ключу
    (point_type, address): существующие адреса увеличивают usage_count,
    новые — добавляются.

    ШАГ FIX-2.2: элемент может нести поля салона (salon_name, salon_code,
    salon_inn, salon_city) — они пишутся в свои колонки. Пустое значение
    НЕ затирает уже записанное: COALESCE(NULLIF(excluded.X, ''), X).

    :return: сколько НОВЫХ адресов добавлено
    """
    if not addresses:
        return 0

    # Готовим строки заранее: пустые адреса пропускаем, город считаем здесь,
    # чтобы не делать это внутри цикла работы с БД.
    rows = []
    for item in addresses:
        addr = (item.get("address") or "").strip()
        if not addr:
            continue
        rows.append((
            point_type,
            addr,
            _extract_city(addr),
            (item.get("date") or "").strip(),
            (item.get("time_window") or "").strip(),
            (item.get("salon_name") or "").strip(),
            (item.get("salon_code") or "").strip(),
            (item.get("salon_inn") or "").strip(),
            (item.get("salon_city") or "").strip(),
        ))

    if not rows:
        return 0

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Разница в количестве записей = число новых адресов.
        # Считаем до и после в одной транзакции, поэтому значение точное.
        before = cursor.execute(
            "SELECT COUNT(*) FROM address_book WHERE point_type = ?", (point_type,)
        ).fetchone()[0]

        cursor.executemany(
            "INSERT INTO address_book "
            "(point_type, address, city, date, time_window, usage_count, "
            " salon_name, salon_code, salon_inn, salon_city) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?) "
            "ON CONFLICT(point_type, address) DO UPDATE SET "
            "    usage_count = usage_count + 1, "
            "    city = COALESCE(NULLIF(excluded.city, ''), address_book.city), "
            "    salon_name = COALESCE(NULLIF(excluded.salon_name, ''), "
            "                          address_book.salon_name), "
            "    salon_code = COALESCE(NULLIF(excluded.salon_code, ''), "
            "                          address_book.salon_code), "
            "    salon_inn  = COALESCE(NULLIF(excluded.salon_inn, ''), "
            "                          address_book.salon_inn), "
            "    salon_city = COALESCE(NULLIF(excluded.salon_city, ''), "
            "                          address_book.salon_city)",
            rows,
        )

        # FTS5: индекс здесь НЕ трогаем — импорт не должен платить за него.
        # Новые строки доливаются при первом поиске (database._ensure_fts_fresh
        # видит расхождение last_id и добавляет ровно новые записи) либо
        # вручную через rebuild_fts_index(). Так массовый импорт из Excel
        # остаётся ровно таким же быстрым, как до внедрения FTS5.
        conn.commit()

        after = cursor.execute(
            "SELECT COUNT(*) FROM address_book WHERE point_type = ?", (point_type,)
        ).fetchone()[0]

        added = after - before
        logger.info(
            f"Импортировано в '{point_type}': обработано {len(rows)}, новых {added} "
            f"(FTS-индекс догонится при первом поиске)"
        )
        audit.log_event(
            "addresses_imported", point_type=point_type,
            count=len(rows), added=added,
        )
        return added
    except Exception as e:
        logger.error(f"Ошибка импорта адресов: {e}")
        conn.rollback()
        return 0
    finally:
        conn.close()


__all__ = [
    "count_addresses",
    "delete_address",
    "get_addresses",
    "import_addresses_from_list",
    "save_address",
    "update_address",
]
