#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Справочник контрагентов — таблица counterparties.

Таблица независима от carriers/customers: контрагент привязан к типу
договора (contract_type) и роли стороны (role). «Экспедиторство» работает
со своими таблицами — здесь справочник для остальных типов (Формика,
Логистикс Рус, аренда).

FTS5 для этого справочника не заводится: объём записей небольшой, а LIKE
даёт предсказуемый результат и на подстроке, и на цифрах ИНН.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from core import audit

from db.connection import get_connection

logger = logging.getLogger("db.crud.counterparties")


#: Редактируемые поля контрагента: один список для INSERT и UPDATE,
#: чтобы наборы колонок в запросах не разъезжались.
COUNTERPARTY_FIELDS: Tuple[str, ...] = (
    "contract_type", "role", "full_name", "short_name", "inn", "kpp", "ogrn",
    "legal_address", "actual_address", "bank_account", "bik",
    "correspondent_account", "bank_name", "director_name",
    "director_position", "phone", "email",
)


def _counterparty_values(data: Dict[str, Any]) -> List[Any]:
    """
    Значения полей контрагента в порядке COUNTERPARTY_FIELDS.

    Пустой ИНН пишется как NULL, а не как пустая строка: в SQLite пустые
    строки конфликтуют в UNIQUE(contract_type, role, inn) между собой,
    и второго контрагента без ИНН (физлицо, ИП без ИНН в тексте) было бы
    не сохранить. NULL в UNIQUE-ограничении не конфликтует.
    """
    values: List[Any] = []
    for name in COUNTERPARTY_FIELDS:
        value = data.get(name)
        if name == "inn":
            text = str(value).strip() if value is not None else ""
            values.append(text or None)
        else:
            values.append("" if value is None else value)
    return values


def _validate_counterparty(data: Dict[str, Any]) -> None:
    """Обязательные поля контрагента: тип договора, роль и наименование."""
    for name, title in (
        ("contract_type", "тип договора"),
        ("role", "роль"),
        ("full_name", "наименование"),
    ):
        if not str(data.get(name) or "").strip():
            raise ValueError(f"Контрагент: не заполнено обязательное поле «{title}»")


def save_counterparty(data: Dict[str, Any]) -> int:
    """
    Добавляет контрагента в справочник и возвращает его ID.

    Дубль по (contract_type, role, inn) не сохраняется: sqlite3.IntegrityError
    пробрасывается наружу, чтобы вызывающий код мог показать понятное
    сообщение. Ошибка не оставляет открытого соединения (finally).
    """
    _validate_counterparty(data)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        columns = ", ".join(COUNTERPARTY_FIELDS)
        placeholders = ", ".join("?" for _ in COUNTERPARTY_FIELDS)
        cursor.execute(
            f"INSERT INTO counterparties ({columns}) VALUES ({placeholders})",
            _counterparty_values(data),
        )
        cp_id = cursor.lastrowid
        conn.commit()
        logger.info(
            f"Контрагент сохранён: ID={cp_id} "
            f"({data.get('contract_type')}/{data.get('role')})"
        )
        return cp_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_counterparty(cp_id: int, data: Dict[str, Any]) -> bool:
    """
    Обновляет запись контрагента. False — запись не найдена или ошибка БД.

    Поля, которых нет в data, очищаются (как в update_organization):
    вызывающий код передаёт полный набор данных формы.
    """
    _validate_counterparty(data)

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        assignments = ", ".join(f"{name} = ?" for name in COUNTERPARTY_FIELDS)
        cursor.execute(
            f"UPDATE counterparties SET {assignments} WHERE id = ?",
            (*_counterparty_values(data), int(cp_id)),
        )
        updated = cursor.rowcount > 0
        conn.commit()
        if updated:
            logger.info(f"Контрагент обновлён: ID={cp_id}")
        else:
            logger.warning(f"Контрагент не найден для обновления: ID={cp_id}")
        return updated
    except Exception as e:
        logger.error(f"Ошибка обновления контрагента ID={cp_id}: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def load_counterparty(cp_id: int) -> Optional[Dict[str, Any]]:
    """Контрагент по ID или None."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM counterparties WHERE id = ?", (int(cp_id),))
        row = cursor.fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in cursor.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def get_all_counterparties(
    contract_type: str = "",
    role: str = "",
    include_deleted: bool = False,
) -> List[Dict[str, Any]]:
    """
    Контрагенты справочника.

    Пустые contract_type / role означают «без фильтра»; по умолчанию
    мягко удалённые записи не показываются (include_deleted=True — показать).
    """
    sql = "SELECT * FROM counterparties WHERE 1 = 1"
    params: List[Any] = []

    if not include_deleted:
        sql += " AND is_deleted = 0"
    if contract_type:
        sql += " AND contract_type = ?"
        params.append(str(contract_type))
    if role:
        sql += " AND role = ?"
        params.append(str(role))
    sql += " ORDER BY full_name COLLATE NOCASE"

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def search_counterparties(
    contract_type: str,
    role: str,
    search_term: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Поиск контрагентов по наименованию, ИНН или руководителю (LIKE).

    FTS5 для этого справочника не заводится (шаг 3 инфраструктуры):
    объём записей небольшой, а LIKE даёт предсказуемый результат
    и на подстроке, и на цифрах ИНН. Пустые contract_type / role —
    поиск по всем типам и ролям.
    """
    like = f"%{search_term or ''}%"

    sql = (
        "SELECT * FROM counterparties "
        "WHERE is_deleted = 0 AND ("
        "      full_name LIKE ? "
        "   OR COALESCE(short_name, '') LIKE ? "
        "   OR COALESCE(inn, '') LIKE ? "
        "   OR COALESCE(director_name, '') LIKE ?)"
    )
    params: List[Any] = [like, like, like, like]

    if contract_type:
        sql += " AND contract_type = ?"
        params.append(str(contract_type))
    if role:
        sql += " AND role = ?"
        params.append(str(role))
    sql += " ORDER BY full_name COLLATE NOCASE LIMIT ?"
    params.append(int(limit))

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def delete_counterparty(cp_id: int) -> bool:
    """
    Мягкое удаление контрагента: is_deleted = 1 (как у организаций).

    Запись остаётся в базе и возвращается через restore_counterparty().
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE counterparties SET is_deleted = 1 WHERE id = ?", (int(cp_id),)
        )
        deleted = cursor.rowcount > 0
        conn.commit()
        if deleted:
            logger.info(f"Контрагент удалён (мягко): ID={cp_id}")
            audit.log_event(
                "counterparty_deleted", cp_id=cp_id, entities="soft_delete"
            )
        else:
            logger.warning(f"Контрагент не найден для удаления: ID={cp_id}")
        return deleted
    except Exception as e:
        logger.error(f"Ошибка удаления контрагента ID={cp_id}: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def restore_counterparty(cp_id: int) -> bool:
    """Возвращает мягко удалённого контрагента в справочник."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE counterparties SET is_deleted = 0 WHERE id = ?", (int(cp_id),)
        )
        restored = cursor.rowcount > 0
        conn.commit()
        if restored:
            logger.info(f"Контрагент восстановлен: ID={cp_id}")
        else:
            logger.warning(f"Контрагент не найден для восстановления: ID={cp_id}")
        return restored
    except Exception as e:
        logger.error(f"Ошибка восстановления контрагента ID={cp_id}: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


__all__ = [
    "COUNTERPARTY_FIELDS",
    "delete_counterparty",
    "get_all_counterparties",
    "load_counterparty",
    "restore_counterparty",
    "save_counterparty",
    "search_counterparties",
    "update_counterparty",
]
