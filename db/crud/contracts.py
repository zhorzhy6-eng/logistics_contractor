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


__all__ = [
    "load_contract_points",
    "save_contract",
    "save_contract_points",
    "save_contract_with_details",
]
