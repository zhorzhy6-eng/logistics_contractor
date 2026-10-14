#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Общий слой поиска: полнотекстовый индекс, нечёткие уровни и подсчёт
совпадений.

Поисковые функции конкретных справочников живут в модуле своей таблицы
(search_drivers — в drivers.py, search_organizations — в organizations.py,
get_addresses/count_addresses — в addresses.py): правка одной таблицы
должна оставаться правкой одного файла. Здесь — только то, что нужно
НЕСКОЛЬКИМ таблицам сразу.

Резервный путь LIKE остался внутри каждой поисковой функции: он устроен
по-разному (у водителей — паспорт и телефон, у организаций — ИНН и
руководитель), и общая обёртка только запутала бы чтение.

Нечёткие уровни (см. core/fuzzy.py) собраны здесь одной функцией
``search_by_levels``: правила разбора опечаток общие для всех таблиц,
поэтому дублировать цикл по уровням в четырёх модулях не нужно.
"""

import logging
import sqlite3
from typing import Any, Callable, Dict, List, Sequence

from core import fuzzy

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


def search_by_levels(
    fetch: Callable[[str, List[Any]], List[Dict[str, Any]]],
    term: str,
    columns: Sequence[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """
    Нечёткий поиск по уровням: строгий запрос → с опечатками и падежами.

    Уровни описаны в core/fuzzy.py. Здесь — только их обход: первый
    уровень, на котором что-то нашлось, и даёт ответ. Так точное
    совпадение никогда не вытесняется «догадкой», а мягкие правила
    включаются лишь тогда, когда строгих совпадений нет вовсе.

    :param fetch: функция «выполни SQL с параметрами и верни строки».
        Её даёт вызывающий модуль: SQL у каждой таблицы свой;
    :param term: запрос оператора (может быть с опечаткой);
    :param columns: колонки для поиска (имена, не значения);
    :param limit: сколько записей вернуть;
    :return: найденные строки (пустой список — не нашлось ничего).
    """
    for level, condition, params in fuzzy.search_levels(term, columns):
        rows = fetch(condition, list(params))
        if rows:
            logger.debug(
                "Нечёткий поиск: %r → уровень %s, найдено %s",
                term, level, len(rows),
            )
            return rows[: max(1, int(limit))]
    return []


__all__ = ["addresses_fts_count", "ensure_fts_fresh", "search_by_levels"]
