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
from core.address_utils import extract_city as _extract_city, split_salon_name

from db import fts
from db.connection import get_connection
from db.crud.search import addresses_fts_count, ensure_fts_fresh

logger = logging.getLogger("db.crud.addresses")

#: Колонки строки справочника, которые нужны починке старых записей.
_ADDRESS_ROW_COLUMNS = (
    "id", "address", "salon_name", "usage_count", "city",
    "date", "time_window", "salon_code", "salon_inn", "salon_city",
)

#: Адреса-артефакты прежнего импорта: так в справочник попадала шапка Excel.
#: Сравнение — в Python через casefold: SQLite LOWER() не понимает кириллицу.
_IMPORT_ARTIFACTS = frozenset({"наименование салона", "наименование", "адрес"})

#: Сколько адресов запрашивать одним SELECT при починке старых записей.
_REPAIR_QUERY_CHUNK = 400


def _text(value: Any) -> str:
    """Строка без None: 0.0 == falsy, поэтому только явное str(...)."""
    return "" if value is None else str(value).strip()


def _row_from_db(row: tuple) -> Dict[str, Any]:
    """Строка SQLite → словарь с понятными именами колонок."""
    return dict(zip(_ADDRESS_ROW_COLUMNS, row))


def _prepare_import_rows(addresses: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Чистит адреса и наименования до записи в базу."""
    prepared: List[Dict[str, str]] = []
    for item in addresses:
        raw_address = _text(item.get("address"))
        if not raw_address:
            continue
        salon_name = _text(item.get("salon_name"))
        clean_address, salon_name = split_salon_name(raw_address, salon_name)
        if not clean_address:
            continue
        prepared.append({
            "address": clean_address,
            "raw_address": raw_address,
            "salon_name": salon_name,
            "date": _text(item.get("date")),
            "time_window": _text(item.get("time_window")),
            "salon_code": _text(item.get("salon_code")),
            "salon_inn": _text(item.get("salon_inn")),
            "salon_city": _text(item.get("salon_city")),
            "city": _extract_city(clean_address),
        })
    return prepared


def _repair_keys(item: Dict[str, str]) -> List[str]:
    """
    Адреса, под которыми старая запись могла лежать в справочнике.

    Прежний импорт писал в address либо наименование салона, либо адрес
    вместе с наименованием в хвосте. Оба варианта нужно узнать, чтобы
    не завести дубль.
    """
    clean = item["address"]
    name = item["salon_name"]
    raw = item["raw_address"]
    keys: List[str] = []
    for key in (raw, name):
        key = _text(key)
        if key and key != clean and key not in keys:
            keys.append(key)
    if name:
        for key in (f"{clean}, {name}", f"{clean} {name}"):
            if key != clean and key not in keys:
                keys.append(key)
    return keys


def _row_score(row: Dict[str, Any]) -> tuple:
    """Какую из старых записей взять за основу: с наименованием и большим usage."""
    return (
        1 if _text(row.get("salon_name")) else 0,
        int(row.get("usage_count") or 0),
        -int(row.get("id") or 0),
    )


def _detach(by_address: Dict[str, List[Dict[str, Any]]], row: Dict[str, Any]) -> None:
    """Убирает запись из всех адресных корзин (адрес у неё меняется)."""
    for key, bucket in list(by_address.items()):
        bucket[:] = [item for item in bucket if item is not row]
        if not bucket:
            del by_address[key]


def _merge_values(
    target: Dict[str, Any], item: Dict[str, str], usage: int
) -> Dict[str, Any]:
    """Непустые поля старой записи важнее пустых полей импорта."""
    return {
        "salon_name": _text(target.get("salon_name")) or _text(item.get("salon_name")),
        "salon_code": _text(target.get("salon_code")) or _text(item.get("salon_code")),
        "salon_inn": _text(target.get("salon_inn")) or _text(item.get("salon_inn")),
        "salon_city": _text(target.get("salon_city")) or _text(item.get("salon_city")),
        "date": _text(target.get("date")) or _text(item.get("date")),
        "time_window": _text(target.get("time_window")) or _text(item.get("time_window")),
        "usage_count": usage,
    }


def _fetch_existing_rows(
    cursor, point_type: str, wanted
) -> Dict[str, List[Dict[str, Any]]]:
    """Читает только те записи, чьи адреса могли совпасть с импортом."""
    found: Dict[str, List[Dict[str, Any]]] = {}
    values = sorted({_text(value) for value in wanted if _text(value)})
    for start in range(0, len(values), _REPAIR_QUERY_CHUNK):
        chunk = values[start:start + _REPAIR_QUERY_CHUNK]
        placeholders = ", ".join("?" * len(chunk))
        cursor.execute(
            "SELECT " + ", ".join(_ADDRESS_ROW_COLUMNS) + " "
            "FROM address_book WHERE point_type = ? "
            f"AND address IN ({placeholders})",
            (point_type, *chunk),
        )
        for row in cursor.fetchall():
            item = _row_from_db(row)
            found.setdefault(_text(item["address"]), []).append(item)
    return found


def _find_import_artifacts(cursor, point_type: str) -> List[tuple]:
    """Строки, куда прежний импорт записал шапку файла вместо адреса."""
    cursor.execute(
        "SELECT id, address, city FROM address_book "
        "WHERE point_type = ? AND COALESCE(salon_name, '') = '' "
        "AND LENGTH(address) <= 40",
        (point_type,),
    )
    return [
        row for row in cursor.fetchall()
        if _text(row[1]).casefold() in _IMPORT_ARTIFACTS
    ]


def _delete_import_artifacts(
    conn, cursor, point_type: str, artifacts: List[tuple]
) -> int:
    """Удаляет строки-артефакты и их записи в FTS-индексе."""
    for row_id, address, city in artifacts:
        fts.delete_row(conn, "fts_addresses", row_id, (address, city))
        cursor.execute("DELETE FROM address_book WHERE id = ?", (row_id,))
    return len(artifacts)


def _repair_existing_rows(
    conn, cursor, point_type: str, prepared: List[Dict[str, str]]
) -> tuple:
    """
    Починить записи, оставшиеся от прежнего импорта, ДО вставки новых.

    Трогаются только записи, чьи адреса или наименования есть во входном
    файле. Чужие строки справочника остаются как были.

    :return: (сколько новых адресов, сколько записей починено, сколько
             дублей слито, сколько строк-артефактов удалено)
    """
    artifacts = _find_import_artifacts(cursor, point_type)

    wanted = set()
    dirty_keys = set()
    for item in prepared:
        wanted.add(item["address"])
        wanted.add(item["raw_address"])
        for key in _repair_keys(item):
            wanted.add(key)
            dirty_keys.add(key)
        if item["salon_name"]:
            dirty_keys.add(item["salon_name"])

    by_address = _fetch_existing_rows(cursor, point_type, wanted)

    if artifacts or any(
        _text(row["address"]) in dirty_keys
        for bucket in by_address.values()
        for row in bucket
    ):
        # FTS5 с external content: перед точечным UPDATE строка обязана уже
        # быть в индексе, иначе 'delete' не найдёт её. Синхронизация нужна
        # только когда действительно есть что чинить.
        ensure_fts_fresh(conn, "fts_addresses")

    removed = _delete_import_artifacts(conn, cursor, point_type, artifacts)
    deleted = {row[0] for row in artifacts}
    new_count = 0
    repaired = 0
    merged = 0
    processed_clean = set()

    def alive(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [row for row in rows if row["id"] not in deleted]

    for item in prepared:
        clean = item["address"]
        if clean in processed_clean:
            continue
        processed_clean.add(clean)

        clean_rows = alive(by_address.get(clean, []))
        dirty: List[Dict[str, Any]] = []
        seen = set()
        for key in _repair_keys(item):
            for row in alive(by_address.get(key, [])):
                if row["id"] in seen:
                    continue
                if clean_rows and row["id"] == clean_rows[0]["id"]:
                    continue
                seen.add(row["id"])
                dirty.append(row)

        if not clean_rows and not dirty:
            new_count += 1
            continue

        if clean_rows:
            target = clean_rows[0]
            extras = dirty
            target_is_clean = True
            if not extras:
                # Обычный повторный импорт: запись уже чистая, основная
                # вставка ниже сама обновит usage_count и поля салона.
                # Лишний UPDATE на каждую строку здесь не нужен.
                continue
        else:
            target = max(dirty, key=_row_score)
            extras = [row for row in dirty if row["id"] != target["id"]]
            target_is_clean = False

        usage = int(target.get("usage_count") or 0)
        for extra in extras:
            usage += int(extra.get("usage_count") or 0)

        values = _merge_values(target, item, usage)
        city = _text(target.get("city")) or _extract_city(clean)

        if target_is_clean:
            cursor.execute(
                "UPDATE address_book SET city = ?, salon_name = ?, "
                "salon_code = ?, salon_inn = ?, salon_city = ?, date = ?, "
                "time_window = ?, usage_count = ? WHERE id = ?",
                (city, values["salon_name"], values["salon_code"],
                 values["salon_inn"], values["salon_city"], values["date"],
                 values["time_window"], values["usage_count"], target["id"]),
            )
        else:
            old_address = _text(target.get("address"))
            old_city = _text(target.get("city"))
            cursor.execute(
                "UPDATE address_book SET address = ?, city = ?, salon_name = ?, "
                "salon_code = ?, salon_inn = ?, salon_city = ?, date = ?, "
                "time_window = ?, usage_count = ? WHERE id = ?",
                (clean, city, values["salon_name"], values["salon_code"],
                 values["salon_inn"], values["salon_city"], values["date"],
                 values["time_window"], values["usage_count"], target["id"]),
            )
            fts.replace_row(
                conn, "fts_addresses", target["id"],
                (clean, city), (old_address, old_city),
            )
            _detach(by_address, target)
            target["address"] = clean
            target["city"] = city
            by_address.setdefault(clean, []).append(target)
            repaired += 1

        target.update(values)
        target["city"] = city

        for extra in extras:
            old_address = _text(extra.get("address"))
            old_city = _text(extra.get("city"))
            fts.delete_row(
                conn, "fts_addresses", extra["id"], (old_address, old_city)
            )
            cursor.execute("DELETE FROM address_book WHERE id = ?", (extra["id"],))
            deleted.add(extra["id"])
            _detach(by_address, extra)
            merged += 1

    return new_count, repaired, merged, removed


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


def import_addresses_from_list(
    point_type: str,
    addresses: List[Dict[str, str]],
    stats: Optional[Dict[str, int]] = None,
) -> int:
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

    ФИКС ИМПОРТА САЛОНОВ: перед вставкой адрес чистится правилом
    core.address_utils.split_salon_name (наименование из хвоста адреса
    переезжает в salon_name), а уже существующие «грязные» записи этого
    справочника починяются: адрес = наименование, адрес с наименованием
    в хвосте, дубли — сливаются. Чужие записи не трогаются.

    :param stats: необязательный словарь для интерфейса; заполняется
        processed / added / repaired / merged / removed.
    :return: сколько НОВЫХ адресов добавлено
    """
    if not addresses:
        if stats is not None:
            stats.update(
                processed=0, added=0, repaired=0, merged=0, removed=0
            )
        return 0

    prepared = _prepare_import_rows(addresses)
    if not prepared:
        if stats is not None:
            stats.update(
                processed=0, added=0, repaired=0, merged=0, removed=0
            )
        return 0

    rows = [
        (
            point_type,
            item["address"],
            item["city"],
            item["date"],
            item["time_window"],
            item["salon_name"],
            item["salon_code"],
            item["salon_inn"],
            item["salon_city"],
        )
        for item in prepared
    ]

    conn = get_connection()
    cursor = conn.cursor()

    try:
        added, repaired, merged, removed = _repair_existing_rows(
            conn, cursor, point_type, prepared
        )

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

        # FTS5: новые строки здесь НЕ трогаем — импорт не должен платить за
        # индекс. Они доливаются при первом поиске (ensure_fts_fresh видит
        # расхождение last_id) либо вручную через rebuild_fts_index().
        # Починенные старые строки обновляются точечно в _repair_existing_rows.
        conn.commit()

        logger.info(
            f"Импортировано в '{point_type}': обработано {len(prepared)}, "
            f"новых {added}, починено {repaired}, слито дублей {merged}"
            f"{f', удалено артефактов {removed}' if removed else ''}"
            " (FTS-индекс догонится при первом поиске)"
        )
        audit.log_event(
            "addresses_imported", point_type=point_type,
            count=len(prepared), added=added,
        )
        if stats is not None:
            stats.update(
                processed=len(prepared), added=added,
                repaired=repaired, merged=merged, removed=removed,
            )
        return added
    except Exception as e:
        logger.error(f"Ошибка импорта адресов: {e}")
        conn.rollback()
        if stats is not None:
            stats.update(
                processed=0, added=0, repaired=0, merged=0, removed=0
            )
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
