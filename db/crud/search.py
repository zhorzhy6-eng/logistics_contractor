#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Общий слой поиска: полнотекстовый индекс и подсчёт совпадений.

Поисковые функции конкретных справочников живут в модуле своей таблицы
(search_drivers — в drivers.py, search_organizations — в organizations.py,
get_addresses/count_addresses — в addresses.py): правка одной таблицы
должна оставаться правкой одного файла. Здесь — только то, что нужно
НЕСКОЛЬКИМ таблицам сразу.

Резервный путь LIKE остался внутри каждой поисковой функции: он устроен
по-разному (у водителей — паспорт и телефон, у организаций — ИНН и
руководитель), и общая обёртка только запутала бы чтение.
"""

import logging
import sqlite3

from db import fts

logger = logging.getLogger("db.crud.search")


def ensure_fts_fresh(conn, fts_name: str) -> bool:
    """
    Догоняет FTS-индекс, если данные менялись в обход приложения.

    Обычные save/update/delete/import держат индекс актуальным, поэтому
    здесь почти всегда ничего не происходит (одно чтение MAX(id)).
    Если же строки добавили вручную в SQLite, индекс доливается и изменения
    фиксируются — иначе они потерялись бы при закрытии соединения.

    :return: True, если индекс пришлось догонять
    """
    try:
        if not fts.needs_sync(conn, fts_name):
            return False
        added = fts.sync_content(conn, fts_name)
        conn.commit()
        logger.info(f"FTS-индекс {fts_name} досинхронизирован: +{added} строк")
        return True
    except sqlite3.DatabaseError as e:
        logger.warning(f"Не удалось синхронизировать {fts_name}: {e}")
        return False


def addresses_fts_count(cursor, point_type: str, match_query: str) -> int:
    """Сколько адресов этого типа найдёт FTS5 (0 — значит, идём в LIKE-резерв)."""
    cursor.execute(
        "SELECT COUNT(*) FROM address_book a "
        "JOIN fts_addresses f ON f.rowid = a.id "
        "WHERE fts_addresses MATCH ? AND a.point_type = ?",
        (match_query, point_type),
    )
    return int(cursor.fetchone()[0] or 0)


__all__ = ["addresses_fts_count", "ensure_fts_fresh"]
