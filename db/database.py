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
# CRUD: Тягач и прицеп водителя
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
# CRUD: Организации
# ─────────────────────────────────────────────────────────────

def save_organization(org_data: Dict[str, Any], is_carrier: bool = False) -> int:
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
                _ensure_fts_fresh(conn, fts_name)
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


# ─────────────────────────────────────────────────────────────
# CRUD: ТС
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


