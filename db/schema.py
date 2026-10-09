#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Схема базы данных: таблицы, индексы, служебные проверки.

Модуль только СОЗДАЁТ объекты схемы (идемпотентно: IF NOT EXISTS).
Изменение схемы у существующей базы — в db/migrations.py: там же решается,
нужна ли перед этим резервная копия.

Порядок в init_database():
  1. schema.create_all()      — таблицы;
  2. migrations.run()         — колонки, индексы, FTS, city.
Поэтому индексы создаются ПОСЛЕ миграций: индекс по новой колонке
(`idx_drivers_default_carrier`) на старой базе иначе не создался бы.
"""

import logging
import sqlite3

logger = logging.getLogger("db.schema")


# Индексы по часто используемым полям (Шаг 2 оптимизации производительности).
# Вынесены на уровень модуля, чтобы проверять их наличие до миграций.
INDEXES = (
    ("idx_contracts_driver",          "contracts(driver_id)"),
    ("idx_contracts_customer",        "contracts(customer_id)"),
    ("idx_contracts_carrier",         "contracts(carrier_id)"),
    ("idx_contract_points_contract",  "contract_points(contract_id)"),
    ("idx_vehicles_carrier",          "vehicles(carrier_id)"),
    ("idx_vehicles_contract",         "vehicles(contract_id)"),
    ("idx_vehicles_vin",              "vehicles(vin)"),
    ("idx_drivers_full_name",         "drivers(full_name)"),
    # Привязка водителей к перевозчикам (ШАГ «Привязка водителей
    # к перевозчикам»): основной перевозчик водителя и история работы.
    ("idx_drivers_default_carrier",   "drivers(default_carrier_id)"),
    ("idx_driver_carriers_driver",    "driver_carriers(driver_id)"),
    ("idx_driver_carriers_carrier",   "driver_carriers(carrier_id)"),
    ("idx_driver_carriers_active",    "driver_carriers(driver_id, ended_at)"),
    ("idx_address_book_point_address", "address_book(point_type, address)"),
    # Шаг 4 оптимизации: индекс под сортировку справочника адресов.
    ("idx_address_book_sort_nocase",
     "address_book(point_type, city COLLATE NOCASE, address COLLATE NOCASE)"),
    # Вариант В (мягкое удаление): списки и поиск почти всегда идут
    # с условием is_deleted = 0, поэтому индексируем этот флаг.
    ("idx_drivers_active",            "drivers(is_deleted)"),
    ("idx_customers_active",          "customers(is_deleted)"),
    ("idx_carriers_active",           "carriers(is_deleted)"),
    # Шаг 3 инфраструктуры типов договоров: справочник контрагентов
    # (counterparties) фильтруется по типу договора и роли.
    ("idx_counterparties_type",       "counterparties(contract_type)"),
    ("idx_counterparties_role",       "counterparties(contract_type, role)"),
    ("idx_counterparties_active",     "counterparties(is_deleted)"),
)


# Таблицы справочников, где удаление «мягкое» (вариант В).
# Запись не стирается, а помечается is_deleted = 1: она исчезает из списков
# и поиска, но остаётся в базе, поэтому её можно восстановить, а ссылки из
# contracts (driver_id / customer_id / carrier_id) остаются целыми.
SOFT_DELETE_TABLES = ("drivers", "customers", "carriers")


# ─────────────────────────────────────────────────────────────
# Проверки схемы
# ─────────────────────────────────────────────────────────────

def _column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    """Проверяет, существует ли колонка в таблице."""
    cursor.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cursor.fetchall()]
    return column in cols


def _index_exists(cursor: sqlite3.Cursor, index_name: str) -> bool:
    """Проверяет, существует ли индекс."""
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
        (index_name,),
    )
    return cursor.fetchone() is not None


# ─────────────────────────────────────────────────────────────
# Создание схемы
# ─────────────────────────────────────────────────────────────

def create_all(conn: sqlite3.Connection) -> None:
    """Создаёт таблицы базы (CREATE TABLE IF NOT EXISTS) и фиксирует их."""
    cursor = conn.cursor()

    # ── drivers ──
    # default_carrier_id — основной перевозчик водителя (ШАГ «Привязка
    # водителей к перевозчикам»): подставляется в договор по умолчанию.
    # У водителей, заведённых раньше, значение NULL — данные не теряются.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS drivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            birth_date TEXT,
            birth_place TEXT,
            passport_series TEXT,
            passport_number TEXT,
            passport_issue_date TEXT,
            passport_issuer TEXT,
            passport_code TEXT,
            registration_address TEXT,
            license_series TEXT,
            license_number TEXT,
            license_issue_date TEXT,
            license_expiry_date TEXT,
            license_categories TEXT,
            phone TEXT,
            default_carrier_id INTEGER,
            is_deleted INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (default_carrier_id) REFERENCES carriers(id) ON DELETE SET NULL
        )
    """)

    # ── customers ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            short_name TEXT,
            inn TEXT,
            kpp TEXT,
            ogrn TEXT,
            legal_address TEXT,
            actual_address TEXT,
            bank_account TEXT,
            bik TEXT,
            correspondent_account TEXT,
            bank_name TEXT,
            director_name TEXT,
            director_position TEXT,
            phone TEXT,
            email TEXT,
            entity_type TEXT,
            basis TEXT,
            is_deleted INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── carriers ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS carriers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            short_name TEXT,
            inn TEXT,
            kpp TEXT,
            ogrn TEXT,
            legal_address TEXT,
            actual_address TEXT,
            bank_account TEXT,
            bik TEXT,
            correspondent_account TEXT,
            bank_name TEXT,
            director_name TEXT,
            director_position TEXT,
            phone TEXT,
            email TEXT,
            license_number TEXT,
            license_date TEXT,
            entity_type TEXT,
            basis TEXT,
            is_deleted INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── vehicles ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            carrier_id INTEGER,
            contract_id INTEGER,
            vin TEXT,
            brand_model TEXT,
            plate_number TEXT,
            year INTEGER,
            color TEXT,
            vehicle_type TEXT,
            FOREIGN KEY (carrier_id) REFERENCES carriers(id) ON DELETE SET NULL,
            FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE CASCADE
        )
    """)

    # ── driver_vehicles ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS driver_vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER UNIQUE,
            tractor_brand TEXT,
            tractor_plate TEXT,
            tractor_color TEXT,
            tractor_year TEXT,
            trailer_brand TEXT,
            trailer_plate TEXT,
            trailer_color TEXT,
            trailer_year TEXT,
            FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE CASCADE
        )
    """)

    # ── driver_carriers: история работы водителя у перевозчиков ──
    # ШАГ «Привязка водителей к перевозчикам»: связь «водитель ↔ перевозчик»
    # была видна только через договоры. Запись без ended_at — активная связь.
    # CREATE TABLE IF NOT EXISTS безопасен и на чистой, и на рабочей базе,
    # поэтому в _needs_migration таблица не вносится.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS driver_carriers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL,
            carrier_id INTEGER NOT NULL,
            started_at TEXT,
            ended_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE CASCADE,
            FOREIGN KEY (carrier_id) REFERENCES carriers(id) ON DELETE CASCADE
        )
    """)

    # ── contracts ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_number TEXT,
            contract_date TEXT,
            start_date TEXT,
            end_date TEXT,
            route TEXT,
            price_without_vat REAL,
            vat_rate TEXT,
            price_with_vat REAL,
            currency TEXT,
            special_conditions TEXT,
            driver_id INTEGER,
            customer_id INTEGER,
            carrier_id INTEGER,
            prepayment_amount REAL DEFAULT 0,
            prepayment_percent REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE SET NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL,
            FOREIGN KEY (carrier_id) REFERENCES carriers(id) ON DELETE SET NULL
        )
    """)

    # ── contract_points ──
    # name — наименование салона точки (ШАГ FIX-6, часть F): в бланк оно
    # попадает из формы, и без колонки терялось при перезагрузке договора
    # из базы. У договоров, сохранённых раньше, значение пустое.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contract_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER,
            point_type TEXT,
            sort_order INTEGER,
            name TEXT,
            address TEXT,
            date TEXT,
            time_window TEXT,
            FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE CASCADE
        )
    """)

    # ── address_book ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS address_book (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            point_type TEXT NOT NULL,
            address TEXT NOT NULL,
            city TEXT,
            date TEXT,
            time_window TEXT,
            usage_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(point_type, address)
        )
    """)

    # ── counterparties: справочник контрагентов (шаг 3 инфраструктуры) ──
    # Таблица независима от carriers/customers и привязана к типу договора
    # и роли стороны («Арендатор»/«Арендодатель» и т.д.). Колонки
    # существующих таблиц не меняются, поэтому в _needs_migration её нет:
    # CREATE TABLE IF NOT EXISTS безопасен и на чистой, и на рабочей базе.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS counterparties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_type TEXT NOT NULL,
            role TEXT NOT NULL,
            full_name TEXT NOT NULL,
            short_name TEXT,
            inn TEXT,
            kpp TEXT,
            ogrn TEXT,
            legal_address TEXT,
            actual_address TEXT,
            bank_account TEXT,
            bik TEXT,
            correspondent_account TEXT,
            bank_name TEXT,
            director_name TEXT,
            director_position TEXT,
            phone TEXT,
            email TEXT,
            is_deleted INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(contract_type, role, inn)
        )
    """)

    conn.commit()


def create_indexes(cursor: sqlite3.Cursor) -> None:
    """
    Создаёт индексы (CREATE INDEX IF NOT EXISTS).

    Вызывается ПОСЛЕ миграций: часть индексов ссылается на колонки, которых
    на старой базе ещё нет (idx_drivers_default_carrier и другие).
    """
    # ── Индекс для быстрой сортировки ──
    try:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_address_book_city
            ON address_book(point_type, city, address)
        """)
    except Exception as e:
        logger.warning(f"Не удалось создать индекс: {e}")

    # ── Индексы по часто используемым полям (Шаг 2 оптимизации) ──
    # CREATE INDEX IF NOT EXISTS безопасен для существующих баз:
    # на уже проиндексированной базе запрос ничего не делает.
    created = 0
    for index_name, target in INDEXES:
        try:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {target}")
            created += 1
        except sqlite3.DatabaseError as e:
            logger.warning(f"Не удалось создать индекс {index_name} ON {target}: {e}")

    logger.info(f"Индексы проверены/созданы: {created} из {len(INDEXES)}")


__all__ = [
    "INDEXES",
    "SOFT_DELETE_TABLES",
    "create_all",
    "create_indexes",
]
