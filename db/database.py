#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Точка входа для работы с локальной базой данных SQLite.

Здесь осталась только инициализация базы и имена, которыми пользуется
проект: `init_database()`, `get_connection()` и функции справочников.
Сама работа разложена по модулям пакета:

  connection.py  — соединение и PRAGMA (WAL, busy_timeout, foreign_keys);
  schema.py      — таблицы и индексы;
  migrations.py  — доведение существующей базы до текущей схемы + бэкап;
  fts.py         — полнотекстовый поиск FTS5 (регистр по кириллице);
  salons.py      — справочник салонов («Места выгрузок») из Excel;
  crud/          — запросы по таблицам (водители, организации, адреса, …).

ВАЖНО: `db.database` остаётся точкой входа. Приложение, генераторы и тесты
импортируют имена ОТСЮДА, о внутреннем устройстве пакета им знать не нужно.

Путь к базе (`DB_PATH`) принадлежит этому модулю: тесты и рабочие
инструменты подменяют его на уровне модуля, поэтому соединение спрашивает
путь у нас (см. db/connection.py::set_db_path_provider).
"""

import datetime
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from core import audit
from core.security import backup_database, restrict_to_current_user

from db import connection, fts, migrations, salons, schema
from db.connection import DB_PATH as _DEFAULT_DB_PATH
from db.connection import current_db_path, get_connection
from db.crud.addresses import (
    count_addresses,
    delete_address,
    get_addresses,
    import_addresses_from_list,
    save_address,
    update_address,
)
from db.crud.counterparties import (
    COUNTERPARTY_FIELDS,
    delete_counterparty,
    get_all_counterparties,
    load_counterparty,
    restore_counterparty,
    save_counterparty,
    search_counterparties,
    update_counterparty,
)
from db.crud.drivers import (
    ACTIVE_LINK_SQL,
    delete_driver,
    get_all_drivers,
    get_carrier_drivers,
    get_driver_carriers,
    link_driver_to_carrier,
    load_driver,
    restore_driver,
    save_driver,
    search_drivers,
    set_default_carrier,
    unlink_driver_from_carrier,
    update_driver,
)
from db.crud.organizations import (
    delete_organization,
    find_organization_id,
    get_all_organizations,
    load_organization,
    load_organization_by_id,
    restore_organization,
    save_organization,
    search_organizations,
    update_organization,
)
from db.crud.vehicles import (
    load_driver_vehicle,
    save_driver_vehicle,
    save_vehicles,
)
from db.crud.search import ensure_fts_fresh as _ensure_fts_fresh
from db.fts import rebuild_fts_index
from db.migrations import _needs_migration
from db.salons import (
    SALONS_XLSX_NAME,
    SALON_COLUMN_KEYS,
    read_salons_rows,
    salons_xlsx_path,
)
from db.schema import INDEXES, SOFT_DELETE_TABLES

logger = logging.getLogger("db.database")

DB_PATH = _DEFAULT_DB_PATH


def _db_path_for_connection() -> str:
    """
    Путь к базе для новых соединений — из ЭТОГО модуля.

    Тесты и инструменты подменяют `db.database.DB_PATH`; чтобы подмена
    действовала, модуль соединения спрашивает путь здесь (см. db/connection.py).
    """
    return DB_PATH


# Регистрируем источник пути: с этого момента connection.get_connection()
# открывает именно тот файл, на который указывает DB_PATH этого модуля.
connection.set_db_path_provider(_db_path_for_connection)


# ─────────────────────────────────────────────────────────────
# Инициализация базы
# ─────────────────────────────────────────────────────────────

def init_database() -> None:
    """
    Инициализирует таблицы в БД и добавляет недостающие колонки.

    Порядок тот же, что был: таблицы → миграции (с бэкапом) → индексы →
    city → FTS5 → закрытие соединения → справочник салонов → права на файлы.
    Резервное копирование и ограничение прав берутся ИЗ ЭТОГО модуля: тесты
    подменяют их здесь (tests/conftest.py::isolated_db).
    """
    conn = get_connection()
    schema.create_all(conn)
    migrations.run(conn, backup=backup_database, db_path=DB_PATH)

    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")
    audit.log_event("database_initialized", db=os.path.basename(DB_PATH))

    # ── Справочник салонов при первом запуске (ШАГ FIX-2.2, п. C.9) ──
    # Импорт идёт ПОСЛЕ закрытия основного соединения: _load_salons_if_empty
    # работает своим соединением, а два писателя на одной базе не нужны.
    # Файл читается и на НЕпустом справочнике, если в нём нет ни одного кода
    # салона: так база, набранная до FIX-2.2, получает наименования и адреса
    # из справочника, а её собственные записи остаются на месте.
    _load_salons_if_empty()

    # ── Права на файлы базы (Шаг 5 задания) ──
    # contracts.db содержит персональные данные водителей, поэтому доступ
    # оставляем только текущему пользователю (файл + WAL/SHM при наличии).
    migrations.secure_files(DB_PATH, restrict_to_current_user)


def _load_salons_if_empty() -> int:
    """
    Первый запуск: если справочник салонов ещё не залит, читает файл.

    Путь к файлу берётся ЗДЕСЬ (salons_xlsx_path), а не внутри db/salons.py:
    тесты подменяют `db.database.salons_xlsx_path`.

    :return: сколько записей импортировано (0 — импорта не было)
    """
    return salons.load_if_empty(salons_xlsx_path())


# ─────────────────────────────────────────────────────────────
# Сохранение договора + точек
# ─────────────────────────────────────────────────────────────

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


