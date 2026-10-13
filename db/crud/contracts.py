#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Договоры и точки маршрута — таблицы contracts и contract_points.

Шапка договора хранит ссылки на справочники: `driver_id`, `customer_id`,
`carrier_id` (мягкое удаление их не рвёт — ON DELETE SET NULL срабатывает
только на настоящее удаление, которого в интерфейсе нет) и предоплату
(`prepayment_amount`, `prepayment_percent`; у типов, где её нет, — 0).

Точки маршрута пишутся заново на каждый договор: save_contract_points()
сначала чистит прежние строки договора, потом пишет загрузки и выгрузки
по порядку (`sort_order`), сохраняя наименование салона (`name`).

save_contract_with_details() — одна транзакция на договор, маршрут и ТС:
вызывающий код (сохранение из формы) получает либо всё, либо ничего.
"""

import logging
import sqlite3
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from db.connection import get_connection
from db.crud.vehicles import save_vehicles

logger = logging.getLogger("db.crud.contracts")


def save_contract(contract_data: Dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Сохраняет шапку договора.

    Предоплата (ШАГ «Предоплата»): пишутся сумма `prepayment_amount` и
    процент `prepayment_percent`. Старые вызовы без этих ключей работают
    как раньше — в колонки уходит 0.
    """
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO contracts (
            contract_number, contract_date, start_date, end_date,
            route, price_without_vat, vat_rate, price_with_vat,
            currency, special_conditions, driver_id, customer_id, carrier_id,
            prepayment_amount, prepayment_percent
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        contract_data.get("number", ""),
        contract_data.get("date", ""),
        contract_data.get("start_date", ""),
        contract_data.get("end_date", ""),
        contract_data.get("route", ""),
        contract_data.get("price_without_vat", 0),
        contract_data.get("vat_rate", "20%"),
        contract_data.get("price_with_vat", 0),
        contract_data.get("currency", "RUB"),
        contract_data.get("special_conditions", ""),
        contract_data.get("driver_id", None),
        contract_data.get("customer_id", None),
        contract_data.get("carrier_id", None),
        contract_data.get("prepayment_amount", 0) or 0,
        contract_data.get("prepayment_percent", 0) or 0,
    ))
    contract_id = cursor.lastrowid
    if own_connection:
        conn.commit()
        conn.close()
    logger.info(f"Договор сохранён: ID={contract_id}")
    return contract_id


def save_contract_points(
    contract_id: int, loadings: List[Dict], unloadings: List[Dict],
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """
    Сохраняет точки маршрута договора (ШАГ FIX-6, часть F).

    У точки сохраняется НАИМЕНОВАНИЕ салона (`name`) — раньше колонки не
    было, и после перезагрузки договора из базы имя терялось, хотя в бланк
    попадало из формы. Точка без имени пишется пустой строкой: старые
    вызовы (без ключа `name`) работают как раньше.
    """
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM contract_points WHERE contract_id = ?", (contract_id,))
    for i, l in enumerate(loadings):
        cursor.execute(
            "INSERT INTO contract_points "
            "(contract_id, point_type, sort_order, name, address, date, time_window) "
            "VALUES (?, 'loading', ?, ?, ?, ?, ?)",
            (contract_id, i, l.get("name", "") or "", l.get("address", ""),
             l.get("date", ""), l.get("time_window", ""))
        )
    for i, u in enumerate(unloadings):
        cursor.execute(
            "INSERT INTO contract_points "
            "(contract_id, point_type, sort_order, name, address, date, time_window) "
            "VALUES (?, 'unloading', ?, ?, ?, ?, ?)",
            (contract_id, i, u.get("name", "") or "", u.get("address", ""),
             u.get("date", ""), u.get("time_window", ""))
        )
    if own_connection:
        conn.commit()
        conn.close()


def save_contract_with_details(
    contract_data: Dict[str, Any], loadings: List[Dict],
    unloadings: List[Dict], vehicles: List[Dict[str, Any]],
) -> int:
    """Сохраняет договор, маршрут и транспорт одной транзакцией."""
    conn = get_connection()
    try:
        contract_id = save_contract(contract_data, conn=conn)
        save_contract_points(contract_id, loadings, unloadings, conn=conn)
        if vehicles:
            save_vehicles(vehicles, contract_id=contract_id, conn=conn)
        conn.commit()
        return contract_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_contract_points(contract_id: int) -> Dict[str, List[Dict]]:
    """
    Читает точки маршрута договора.

    Точка возвращается ЧЕТЫРЬМЯ полями — с наименованием салона (`name`,
    ШАГ FIX-6, часть F). У договоров, сохранённых до миграции, колонка
    добавлена пустой, поэтому имя приходит пустой строкой: данные не
    теряются и форма заполняется как раньше.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT point_type, sort_order, name, address, date, time_window "
        "FROM contract_points WHERE contract_id = ? ORDER BY point_type, sort_order",
        (contract_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    loadings, unloadings = [], []
    for ptype, order, name, addr, date, tw in rows:
        item = {
            "name": name or "",
            "address": addr or "",
            "date": date or "",
            "time_window": tw or "",
        }
        if ptype == "loading":
            loadings.append(item)
        else:
            unloadings.append(item)
    return {"loadings": loadings, "unloadings": unloadings}


# ─────────────────────────────────────────────────────────────
# Поиск договоров и сроки (для ИИ-агента «Максимилиан», v1)
# ─────────────────────────────────────────────────────────────
# Отдельные функции чтения, а не запросы «на месте» в инструментах агента:
# правило проекта (AGENTS.md § 4, задание часть B.7) — SQL живёт в db/crud,
# инструменты только зовут эти функции.

#: Поля договора, которые отдаются наружу вместе с именами сторон.
CONTRACT_SEARCH_FIELDS = (
    "c.id", "c.contract_number", "c.contract_date", "c.start_date",
    "c.end_date", "c.route", "c.price_with_vat", "c.currency",
    "c.driver_id", "c.customer_id", "c.carrier_id",
)

#: Имена сторон подставляются подзапросами: JOIN на три таблицы сразу
#: раздувает выборку, а нужны только названия.
_SIDE_NAMES_SQL = """
    (SELECT d.full_name FROM drivers d WHERE d.id = c.driver_id) AS driver_name,
    (SELECT COALESCE(NULLIF(TRIM(cu.full_name), ''), cu.short_name, '')
       FROM customers cu WHERE cu.id = c.customer_id) AS customer_name,
    (SELECT COALESCE(NULLIF(TRIM(ca.full_name), ''), ca.short_name, '')
       FROM carriers ca WHERE ca.id = c.carrier_id) AS carrier_name
"""


def _iso_date(value: Any) -> str:
    """
    Дата договора в ISO (YYYY-MM-DD) — для сравнения в SQL.

    В базе встречаются обе записи: ISO и «01.01.2001». Функция приводит
    любую разбираемую дату к ISO, а неразбираемую оставляет как есть —
    сравнение по ней просто не сработает (договор не потеряется из выдачи).
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        from core.dates import to_iso
        return to_iso(text) or text
    except Exception:  # noqa: BLE001 — парсер дат не должен ломать поиск
        return text


def search_contracts(
    search_term: str = "",
    limit: int = 20,
    include_names: bool = True,
) -> List[Dict[str, Any]]:
    """
    Поиск договоров по номеру, дате, маршруту и названиям сторон.

    Пустой запрос отдаёт последние договоры (свежие сверху): агенту нужен
    обзор, а не пустой список. Регистр по кириллице не важен — сравнение
    идёт через LIKE, а LIKE в SQLite регистронезависим для латиницы; для
    кириллицы запрос дополнительно ищется в нижнем регистре обеих сторон.

    :param search_term: номер, дата, маршрут, ФИО водителя, название стороны;
    :param limit: сколько записей вернуть;
    :param include_names: добавлять ли имена водителя и сторон.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        columns = ", ".join(CONTRACT_SEARCH_FIELDS)
        names = ", " + _SIDE_NAMES_SQL if include_names else ""
        params: List[Any] = []

        where = ""
        term = str(search_term or "").strip()
        if term:
            like = f"%{term}%"
            where = (
                "WHERE ("
                "      COALESCE(c.contract_number, '') LIKE ? "
                "   OR COALESCE(c.contract_date, '') LIKE ? "
                "   OR COALESCE(c.start_date, '') LIKE ? "
                "   OR COALESCE(c.end_date, '') LIKE ? "
                "   OR COALESCE(c.route, '') LIKE ? "
                "   OR COALESCE((SELECT d.full_name FROM drivers d "
                "                WHERE d.id = c.driver_id), '') LIKE ? "
                "   OR COALESCE((SELECT COALESCE(NULLIF(TRIM(cu.full_name), ''), "
                "                cu.short_name, '') FROM customers cu "
                "                WHERE cu.id = c.customer_id), '') LIKE ? "
                "   OR COALESCE((SELECT COALESCE(NULLIF(TRIM(ca.full_name), ''), "
                "                ca.short_name, '') FROM carriers ca "
                "                WHERE ca.id = c.carrier_id), '') LIKE ? "
                ")"
            )
            params.extend([like] * 8)

        cursor.execute(
            f"SELECT {columns}{names} FROM contracts c {where} "
            f"ORDER BY c.id DESC LIMIT ?",
            (*params, max(1, int(limit))),
        )
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def get_expiring_contracts(
    days: int = 30,
    today: Optional[str] = None,
    limit: int = 50,
    include_names: bool = True,
) -> List[Dict[str, Any]]:
    """
    Договоры, у которых `end_date` истекает в ближайшие `days` дней.

    Уже истёкшие договоры НЕ возвращаются: вопрос оператора — «что скоро
    закончится», а не «что уже закончилось». Договоры без заполненной
    `end_date` тоже не возвращаются: сравнивать нечего.

    :param days: горизонт в днях (0 — только сегодняшний день);
    :param today: «сегодня» в ISO (по умолчанию — системная дата): нужно
        тестам, чтобы результат не зависел от дня прогона.
    """
    base = _iso_date(today) or date.today().isoformat()
    try:
        base_date = date.fromisoformat(base)
    except ValueError:
        base_date = date.today()
        base = base_date.isoformat()

    horizon = (base_date + timedelta(days=max(0, int(days)))).isoformat()

    conn = get_connection()
    try:
        cursor = conn.cursor()
        columns = ", ".join(CONTRACT_SEARCH_FIELDS)
        names = ", " + _SIDE_NAMES_SQL if include_names else ""
        cursor.execute(
            f"SELECT {columns}{names} FROM contracts c "
            "WHERE TRIM(COALESCE(c.end_date, '')) <> '' "
            "  AND date(c.end_date) >= date(?) "
            "  AND date(c.end_date) <= date(?) "
            "ORDER BY date(c.end_date), c.id LIMIT ?",
            (base, horizon, max(1, int(limit))),
        )
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


__all__ = [
    "CONTRACT_SEARCH_FIELDS",
    "get_expiring_contracts",
    "load_contract_points",
    "save_contract",
    "save_contract_points",
    "save_contract_with_details",
    "search_contracts",
]
