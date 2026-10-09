#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Транспорт — таблицы vehicles и driver_vehicles.

Две разные записи об одном и том же:

  * `vehicles` — машины ДОГОВОРА (одна запись на автомобиль, у перевозчика
    их может быть несколько, у договора тоже): save_vehicles() пишет список
    строк, каждая ссылается на договор и (необязательно) на перевозчика;
  * `driver_vehicles` — тягач и прицеп ВОДИТЕЛЯ: одна запись на водителя
    (driver_id UNIQUE), save_driver_vehicle() обновляет её или создаёт.

FTS-индекса у этих таблиц нет: поиск по ТС не заводился.
"""

import logging
import sqlite3
from typing import Any, Dict, List, Optional

from db.connection import get_connection

logger = logging.getLogger("db.crud.vehicles")


# ─────────────────────────────────────────────────────────────
# Тягач и прицеп водителя (driver_vehicles)
# ─────────────────────────────────────────────────────────────

def save_driver_vehicle(driver_id: int, vehicle_data: Dict[str, Any]) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM driver_vehicles WHERE driver_id = ?", (driver_id,))
        row = cursor.fetchone()

        if row:
            cursor.execute("""
                UPDATE driver_vehicles SET
                    tractor_brand = ?, tractor_plate = ?, tractor_color = ?, tractor_year = ?,
                    trailer_brand = ?, trailer_plate = ?, trailer_color = ?, trailer_year = ?
                WHERE driver_id = ?
            """, (
                vehicle_data.get("tractor_brand", ""),
                vehicle_data.get("tractor_plate", ""),
                vehicle_data.get("tractor_color", ""),
                vehicle_data.get("tractor_year", ""),
                vehicle_data.get("trailer_brand", ""),
                vehicle_data.get("trailer_plate", ""),
                vehicle_data.get("trailer_color", ""),
                vehicle_data.get("trailer_year", ""),
                driver_id,
            ))
        else:
            cursor.execute("""
                INSERT INTO driver_vehicles (
                    driver_id,
                    tractor_brand, tractor_plate, tractor_color, tractor_year,
                    trailer_brand, trailer_plate, trailer_color, trailer_year
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                driver_id,
                vehicle_data.get("tractor_brand", ""),
                vehicle_data.get("tractor_plate", ""),
                vehicle_data.get("tractor_color", ""),
                vehicle_data.get("tractor_year", ""),
                vehicle_data.get("trailer_brand", ""),
                vehicle_data.get("trailer_plate", ""),
                vehicle_data.get("trailer_color", ""),
                vehicle_data.get("trailer_year", ""),
            ))
        conn.commit()
        logger.info(f"Тягач/прицеп сохранены для водителя ID={driver_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения ТС: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def load_driver_vehicle(driver_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM driver_vehicles WHERE driver_id = ?", (driver_id,))
    row = cursor.fetchone()
    cols = [desc[0] for desc in cursor.description] if cursor.description else []
    conn.close()
    return dict(zip(cols, row)) if row else None


# ─────────────────────────────────────────────────────────────
# ТС договора (vehicles)
# ─────────────────────────────────────────────────────────────

def save_vehicles(
    vehicles: List[Dict[str, Any]],
    carrier_id: Optional[int] = None,
    contract_id: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> List[int]:
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    cursor = conn.cursor()
    ids = []
    for vehicle in vehicles:
        cursor.execute("""
            INSERT INTO vehicles (carrier_id, contract_id, vin, brand_model, plate_number, year, color, vehicle_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            carrier_id,
            contract_id,
            vehicle.get("vin", ""),
            vehicle.get("brand_model", ""),
            vehicle.get("plate_number", ""),
            vehicle.get("year", 0),
            vehicle.get("color", ""),
            vehicle.get("vehicle_type", "Тягач"),
        ))
        ids.append(cursor.lastrowid)
    if own_connection:
        conn.commit()
        conn.close()
    logger.info(f"Сохранено ТС: {len(ids)}")
    return ids


__all__ = [
    "load_driver_vehicle",
    "save_driver_vehicle",
    "save_vehicles",
]
