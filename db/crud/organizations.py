#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Заказчики и перевозчики — таблицы customers и carriers.

Таблицу выбирает флаг `is_carrier`: `"carriers" if is_carrier else "customers"`.
Функции у сторон одни и те же (различается только набор колонок: у
перевозчика есть лицензия), поэтому реализация здесь одна, а модули
`db/crud/customers.py` и `db/crud/carriers.py` дают каждой таблице своё имя.

Мягкое удаление (вариант В): delete_organization() ставит is_deleted = 1 —
ссылки договоров (contracts.customer_id / carrier_id) и ТС перевозчика
остаются целыми, запись можно вернуть restore_organization().
find_organization_id() мягко удалённые находит специально: ссылка договора
должна пережить удаление из справочника.

Запись нормализует ДОЛЖНОСТЬ руководителя: «ДИРЕКТОР» из DaData или из выписки
ЕГРЮЛ сохраняется как «Директор» (_normalize_organization_fields). Наименования
(`full_name`, `short_name`) не трогаются: для них авторитетный источник — DaData
(данные ЕГРЮЛ), и «ООО "АВАТЭК"» капсом остаётся как есть. Правила общие для
всех входов и живут в core/text_normalize.py.

Поиск — FTS5 по наименованию/ИНН/руководителю и прежний LIKE-резерв
(цифры ИНН, середина слова).
"""

import logging
import sqlite3
from typing import Any, Dict, List, Optional

from core import audit
from core.text_normalize import normalize_organization_fields

from db import fts
from db.connection import get_connection
from db.crud.search import ensure_fts_fresh

logger = logging.getLogger("db.crud.organizations")


def _normalize_organization_fields(org_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Реквизиты организации с нормализованной ДОЛЖНОСТЬЮ руководителя.

    Правится ровно одно поле — `director_position`: «ДИРЕКТОР» → «Директор»
    (см. `core/text_normalize.py`). Сокращения («ИП», «ООО», «АО») и значения
    в обычном регистре не меняются.

    Наименования (`full_name`, `short_name`) НЕ нормализуются: для них
    авторитетный источник — DaData (данные ЕГРЮЛ). «ООО "АВАТЭК"» капсом —
    это запись реестра, а «ООО "Аватэк"» было бы уже другим наименованием.

    Возвращается КОПИЯ: вызывающий код (форма, распознавание) продолжает
    работать со своими значениями и не получает неожиданной правки
    на месте. Поля, которых в словаре нет, не добавляются.
    """
    return normalize_organization_fields(org_data)


def save_organization(org_data: Dict[str, Any], is_carrier: bool = False) -> int:
    # Нормализация ДО записи и ДО индекса: в базе и в FTS5 должно лежать
    # одно и то же значение, иначе поиск найдёт запись по строке, которой
    # в таблице уже нет.
    org_data = _normalize_organization_fields(org_data)

    conn = get_connection()
    cursor = conn.cursor()

    if is_carrier:
        cursor.execute("""
            INSERT INTO carriers (
                full_name, short_name, inn, kpp, ogrn,
                legal_address, actual_address, bank_account,
                bik, correspondent_account, bank_name,
                director_name, director_position, phone, email,
                license_number, license_date, entity_type, basis
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            org_data.get("full_name", ""),
            org_data.get("short_name", ""),
            org_data.get("inn", ""),
            org_data.get("kpp", ""),
            org_data.get("ogrn", ""),
            org_data.get("legal_address", ""),
            org_data.get("actual_address", ""),
            org_data.get("bank_account", ""),
            org_data.get("bik", ""),
            org_data.get("correspondent_account", ""),
            org_data.get("bank_name", ""),
            org_data.get("director_name", ""),
            org_data.get("director_position", ""),
            org_data.get("phone", ""),
            org_data.get("email", ""),
            org_data.get("license_number", ""),
            org_data.get("license_date", ""),
            org_data.get("entity_type", ""),
            org_data.get("basis", ""),
        ))
    else:
        cursor.execute("""
            INSERT INTO customers (
                full_name, short_name, inn, kpp, ogrn,
                legal_address, actual_address, bank_account,
                bik, correspondent_account, bank_name,
                director_name, director_position, phone, email,
                entity_type, basis
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            org_data.get("full_name", ""),
            org_data.get("short_name", ""),
            org_data.get("inn", ""),
            org_data.get("kpp", ""),
            org_data.get("ogrn", ""),
            org_data.get("legal_address", ""),
            org_data.get("actual_address", ""),
            org_data.get("bank_account", ""),
            org_data.get("bik", ""),
            org_data.get("correspondent_account", ""),
            org_data.get("bank_name", ""),
            org_data.get("director_name", ""),
            org_data.get("director_position", ""),
            org_data.get("phone", ""),
            org_data.get("email", ""),
            org_data.get("entity_type", ""),
            org_data.get("basis", ""),
        ))

    org_id = cursor.lastrowid
    # FTS5: индекс обновляем вручную (триггеров нет).
    fts.replace_row(
        conn,
        "fts_carriers" if is_carrier else "fts_customers",
        org_id,
        (
            org_data.get("full_name", ""),
            org_data.get("short_name", ""),
            org_data.get("inn", ""),
            org_data.get("director_name", ""),
        ),
    )
    conn.commit()
    conn.close()
    logger.info(f"Организация сохранена: ID={org_id}")
    return org_id


def update_organization(org_id: int, org_data: Dict[str, Any], is_carrier: bool = False) -> bool:
    # Нормализация ДО записи и ДО индекса (см. save_organization).
    org_data = _normalize_organization_fields(org_data)

    conn = None
    table = "carriers" if is_carrier else "customers"
    fts_name = "fts_carriers" if is_carrier else "fts_customers"
    fts_fields = ("full_name", "short_name", "inn", "director_name")
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Старые значения для FTS5: удаление из индекса идёт по ним.
        old = cursor.execute(
            f"SELECT {', '.join(fts_fields)} FROM {table} WHERE id = ?", (org_id,)
        ).fetchone()

        if is_carrier:
            cursor.execute("""
                UPDATE carriers SET
                    full_name = ?, short_name = ?, inn = ?, kpp = ?, ogrn = ?,
                    legal_address = ?, actual_address = ?, bank_account = ?,
                    bik = ?, correspondent_account = ?, bank_name = ?,
                    director_name = ?, director_position = ?, phone = ?, email = ?,
                    license_number = ?, license_date = ?,
                    entity_type = ?, basis = ?
                WHERE id = ?
            """, (
                org_data.get("full_name", ""), org_data.get("short_name", ""),
                org_data.get("inn", ""), org_data.get("kpp", ""),
                org_data.get("ogrn", ""), org_data.get("legal_address", ""),
                org_data.get("actual_address", ""), org_data.get("bank_account", ""),
                org_data.get("bik", ""), org_data.get("correspondent_account", ""),
                org_data.get("bank_name", ""), org_data.get("director_name", ""),
                org_data.get("director_position", ""), org_data.get("phone", ""),
                org_data.get("email", ""), org_data.get("license_number", ""),
                org_data.get("license_date", ""),
                org_data.get("entity_type", ""), org_data.get("basis", ""),
                org_id,
            ))
        else:
            cursor.execute("""
                UPDATE customers SET
                    full_name = ?, short_name = ?, inn = ?, kpp = ?, ogrn = ?,
                    legal_address = ?, actual_address = ?, bank_account = ?,
                    bik = ?, correspondent_account = ?, bank_name = ?,
                    director_name = ?, director_position = ?, phone = ?, email = ?,
                    entity_type = ?, basis = ?
                WHERE id = ?
            """, (
                org_data.get("full_name", ""), org_data.get("short_name", ""),
                org_data.get("inn", ""), org_data.get("kpp", ""),
                org_data.get("ogrn", ""), org_data.get("legal_address", ""),
                org_data.get("actual_address", ""), org_data.get("bank_account", ""),
                org_data.get("bik", ""), org_data.get("correspondent_account", ""),
                org_data.get("bank_name", ""), org_data.get("director_name", ""),
                org_data.get("director_position", ""), org_data.get("phone", ""),
                org_data.get("email", ""),
                org_data.get("entity_type", ""), org_data.get("basis", ""),
                org_id,
            ))
        if old:
            fts.replace_row(
                conn, fts_name, org_id,
                tuple(org_data.get(f, "") for f in fts_fields),
                old,
            )
        conn.commit()
        logger.info(f"Организация обновлена: ID={org_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления организации: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def search_organizations(
    search_term: str,
    is_carrier: bool = False,
    limit: int = 50,
    include_deleted: bool = False,
) -> List[Dict[str, Any]]:
    """
    Поиск организаций по наименованию, ИНН или ФИО руководителя.

    Шаг 5 оптимизации: поиск по руководителю добавлен в SQL — раньше UI
    добирал такие записи, выгружая всю таблицу организаций и фильтруя её
    в Python на каждое нажатие клавиши.

    Словесные запросы («ромашка») идут через FTS5 и не зависят от регистра
    («Ромашка», «РОМАШКА»). Запросы с цифрами (ИНН, ОГРН), а также случаи,
    когда FTS ничего не нашёл, обслуживает прежний LIKE.

    include_deleted=True показывает и мягко удалённые записи.
    """
    table = "carriers" if is_carrier else "customers"
    fts_name = "fts_carriers" if is_carrier else "fts_customers"
    conn = get_connection()
    cursor = conn.cursor()

    try:
        only_active = 1 if include_deleted else 0

        # ── Путь 1: FTS5 по наименованию/ИНН/руководителю ──
        match_query = fts.match_query_for(search_term) if search_term else None
        if match_query and fts.fts5_available(conn):
            try:
                ensure_fts_fresh(conn, fts_name)
                cursor.execute(
                    f"SELECT o.* FROM {table} o "
                    f"JOIN {fts_name} f ON f.rowid = o.id "
                    f"WHERE {fts_name} MATCH ? AND (o.is_deleted = 0 OR ?) "
                    f"ORDER BY o.full_name COLLATE NOCASE LIMIT ?",
                    (match_query, only_active, int(limit)),
                )
                rows = cursor.fetchall()
                if rows:
                    cols = [desc[0] for desc in cursor.description]
                    logger.debug(
                        f"Поиск организаций (FTS5, {table}): {search_term!r} -> "
                        f"{match_query!r}, найдено {len(rows)}"
                    )
                    return [dict(zip(cols, row)) for row in rows]
            except sqlite3.DatabaseError as e:
                logger.warning(
                    f"FTS5-поиск организаций не удался, откат на LIKE: {e}"
                )

        # ── Путь 2: LIKE (как раньше) ──
        like = f"%{search_term}%"
        cursor.execute(
            f"SELECT * FROM {table} "
            f"WHERE (is_deleted = 0 OR ?) AND ("
            f"      full_name LIKE ? "
            f"   OR COALESCE(inn, '') LIKE ? "
            f"   OR COALESCE(director_name, '') LIKE ?) "
            f"ORDER BY full_name COLLATE NOCASE LIMIT ?",
            (only_active, like, like, like, int(limit)),
        )
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def get_all_organizations(
    is_carrier: bool = False,
    include_deleted: bool = False,
) -> List[Dict[str, Any]]:
    """Все организации; по умолчанию без мягко удалённых."""
    table = "carriers" if is_carrier else "customers"
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT * FROM {table} WHERE is_deleted = 0 OR ? "
        f"ORDER BY full_name COLLATE NOCASE",
        (1 if include_deleted else 0,),
    )
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def load_organization(
    org_id: int,
    is_carrier: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Организация по ID — включая мягко удалённую (is_deleted = 1).

    Нужна, чтобы подставить в форму перевозчика, на которого закреплён
    водитель (drivers.default_carrier_id): по ID запись отдаётся даже
    убранной из справочника — ссылка договора и привязка водителя должны
    её пережить.
    """
    table = "carriers" if is_carrier else "customers"
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {table} WHERE id = ?", (org_id,))
        row = cursor.fetchone()
        if not row:
            return None
        cols = [desc[0] for desc in cursor.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def load_organization_by_id(
    org_id: int,
    is_carrier: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Организация по ID — включая мягко удалённую.

    Нужна, когда нужно подтянуть перевозчика по `default_carrier_id` из
    карточки водителя. Скрытие удалённых делает не эта функция, а
    списки/поиск.

    Реализация — тонкая обёртка над `load_organization`: тело запроса уже
    живёт там, второй копии SQL в модуле не нужно. Имя оставлено явным
    («by_id»), потому что рядом есть `find_organization_id` — поиск по
    РЕКВИЗИТАМ, а не по номеру записи.
    """
    return load_organization(org_id, is_carrier=is_carrier)


def find_organization_id(
    org_data: Dict[str, Any],
    is_carrier: bool = False,
) -> Optional[int]:
    """
    ID существующей организации по её реквизитам.

    Ищет по ИНН (главный ключ), затем по полному наименованию.
    Возвращает None, если запись НЕ найдена. НИКОГДА не создаёт
    новую запись.

    Мягко удалённые (is_deleted=1) тоже находятся — ссылка в договоре
    должна пережить мягкое удаление из справочника.

    :param org_data: словарь с реквизитами (inn, full_name, ...)
    :param is_carrier: True → carriers, False → customers
    """
    table = "carriers" if is_carrier else "customers"

    inn = str(org_data.get("inn") or "").strip()
    full_name = str(org_data.get("full_name") or "").strip()

    if not inn and not full_name:
        return None

    conn = get_connection()
    try:
        cursor = conn.cursor()

        # 1. По ИНН (самый надёжный ключ)
        if inn:
            cursor.execute(
                f"SELECT id FROM {table} WHERE inn = ? LIMIT 1",
                (inn,),
            )
            row = cursor.fetchone()
            if row:
                return int(row[0])

        # 2. По полному наименованию (если ИНН пуст или не нашёлся)
        if full_name:
            cursor.execute(
                f"SELECT id FROM {table} "
                f"WHERE full_name = ? COLLATE NOCASE LIMIT 1",
                (full_name,),
            )
            row = cursor.fetchone()
            if row:
                return int(row[0])

        return None
    finally:
        conn.close()


def restore_organization(org_id: int, is_carrier: bool = False) -> bool:
    """Возвращает мягко удалённую организацию в справочник (вариант В)."""
    table = "carriers" if is_carrier else "customers"
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE {table} SET is_deleted = 0 WHERE id = ?", (org_id,)
        )
        conn.commit()
        logger.info(f"Организация восстановлена: ID={org_id} ({table})")
        audit.log_event(
            "organization_restored", org_id=org_id,
            entity="carrier" if is_carrier else "customer",
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка восстановления организации: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def delete_organization(org_id: int, is_carrier: bool = False) -> bool:
    """
    Мягкое удаление организации (вариант В): is_deleted = 1.

    Запись остаётся в базе, ссылки contracts.customer_id / carrier_id
    сохраняются, ТС перевозчика не удаляются — запись можно вернуть
    через restore_organization().
    """
    table = "carriers" if is_carrier else "customers"
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE {table} SET is_deleted = 1 WHERE id = ?", (org_id,)
        )
        conn.commit()
        logger.info(f"Организация удалена (мягко): ID={org_id} ({table})")
        audit.log_event(
            "organization_deleted", org_id=org_id,
            entity="carrier" if is_carrier else "customer",
            entities="soft_delete",
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления организации: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


__all__ = [
    "delete_organization",
    "find_organization_id",
    "get_all_organizations",
    "load_organization",
    "load_organization_by_id",
    "restore_organization",
    "save_organization",
    "search_organizations",
    "update_organization",
    "_normalize_organization_fields",
]
