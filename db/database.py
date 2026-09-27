#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль инициализации и работы с локальной базой данных SQLite.

Хранит данные водителей, организаций, ТС, договоров и справочник адресов.

Все SELECT-запросы возвращают данные ОТСОРТИРОВАННЫМИ.

Для справочника адресов дополнительно извлекается ГОРОД (колонка city),
по которому идёт сортировка: сначала по городу, потом по всему адресу.
"""

import logging
import os
import sqlite3
from typing import List, Dict, Any, Optional, Tuple

from core import audit
from core.security import backup_database, backup_size_kb, restrict_to_current_user

# Логирование SQL-запросов (Часть 2 задания): фабрика соединения пишет
# в logs/debug.log запрос, число строк и время, а медленные запросы (>1 с)
# помечает SLOW в logs/app.log.
from db.query_logging import LoggingConnection

# Полнотекстовый поиск (FTS5) — регистронезависимый поиск по кириллице.
# Модуль сам следит за доступностью FTS5: если её нет, поиск идёт старым LIKE.
from db import fts

# Парсер адресов живёт в core/address_utils.py (Шаг 1 рефакторинга).
# Имя сохранено прежним, чтобы не менять вызовы внутри модуля.
from core.address_utils import extract_city as _extract_city

logger = logging.getLogger("db.database")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contracts.db")

# Индексы по часто используемым полям (Шаг 2 оптимизации производительности).
# Вынесены на уровень модуля, чтобы проверять их наличие до миграций.
INDEXES = (
    ("idx_contracts_driver",          "contracts(driver_id)"),
    ("idx_contracts_customer",        "contracts(customer_id)"),
    ("idx_contracts_carrier",         "contracts(carrier_id)"),
    ("idx_contract_points_contract",  "contract_points(contract_id)"),
    ("idx_vehicles_carrier",          "vehicles(carrier_id)"),
    ("idx_vehicles_vin",              "vehicles(vin)"),
    ("idx_drivers_full_name",         "drivers(full_name)"),
    ("idx_address_book_point_address", "address_book(point_type, address)"),
    # Шаг 4 оптимизации: индекс под сортировку справочника адресов.
    ("idx_address_book_sort_nocase",
     "address_book(point_type, city COLLATE NOCASE, address COLLATE NOCASE)"),
    # Вариант В (мягкое удаление): списки и поиск почти всегда идут
    # с условием is_deleted = 0, поэтому индексируем этот флаг.
    ("idx_drivers_active",            "drivers(is_deleted)"),
    ("idx_customers_active",          "customers(is_deleted)"),
    ("idx_carriers_active",           "carriers(is_deleted)"),
)

# Таблицы справочников, где удаление «мягкое» (вариант В).
# Запись не стирается, а помечается is_deleted = 1: она исчезает из списков
# и поиска, но остаётся в базе, поэтому её можно восстановить, а ссылки из
# contracts (driver_id / customer_id / carrier_id) остаются целыми.
SOFT_DELETE_TABLES = ("drivers", "customers", "carriers")


# ─────────────────────────────────────────────────────────────
# Соединение и инициализация
# ─────────────────────────────────────────────────────────────

def get_connection(foreign_keys: bool = True) -> sqlite3.Connection:
    """
    Возвращает подключение к базе данных с настроенными PRAGMA.

    Шаг 1 оптимизации производительности:
      * journal_mode=WAL  — читатели не блокируют писателя и наоборот
        (режим сохраняется в файле БД, поэтому включается один раз);
      * busy_timeout=5000 — при блокировке ждать до 5 секунд вместо
        мгновенной ошибки «database is locked»;
      * foreign_keys=ON   — контроль ссылочной целостности (как было).
    """
    conn = sqlite3.connect(DB_PATH, timeout=30, factory=LoggingConnection)

    conn.execute("PRAGMA busy_timeout = 5000")
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")

    # WAL может быть недоступен (например, БД на сетевом диске или
    # файл только для чтения) — тогда работаем в прежнем режиме.
    try:
        mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()
        logger.debug(f"journal_mode: {mode[0] if mode else 'неизвестно'}")
    except sqlite3.DatabaseError as e:
        logger.warning(f"Не удалось включить WAL, работаем в обычном режиме: {e}")

    return conn


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


def _needs_migration(cursor: sqlite3.Cursor) -> bool:
    """
    Нужны ли миграции схемы (Шаг 5 задания по безопасности).

    Перед такими изменениями делается резервная копия базы.
    """
    required_columns = (
        ("drivers", "birth_place"),
        ("drivers", "passport_issuer"),
        ("drivers", "phone"),
        ("address_book", "city"),
    )
    for table, column in required_columns:
        if not _column_exists(cursor, table, column):
            return True

    # Мягкое удаление (вариант В): флаг есть во всех таблицах справочника
    for table in SOFT_DELETE_TABLES:
        if not _column_exists(cursor, table, "is_deleted"):
            return True

    # FTS5-индексы: их отсутствие тоже миграция (перед ней делается бэкап).
    # Если FTS5 в сборке нет, проверять нечего — поиск работает через LIKE.
    try:
        conn = cursor.connection
        if fts.fts5_available(conn):
            for fts_name in fts.FTS_TABLES:
                if not fts.table_exists(conn, fts_name):
                    return True
    except Exception:  # noqa: BLE001 — проверка не должна ломать инициализацию
        pass

    for index_name, _ in INDEXES:
        if not _index_exists(cursor, index_name):
            return True

    return False


def init_database() -> None:
    """Инициализирует таблицы в БД и добавляет недостающие колонки."""
    conn = get_connection()
    cursor = conn.cursor()

    # ── drivers ──
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
            is_deleted INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
            is_deleted INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── vehicles ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            carrier_id INTEGER,
            vin TEXT,
            brand_model TEXT,
            plate_number TEXT,
            year INTEGER,
            color TEXT,
            vehicle_type TEXT,
            FOREIGN KEY (carrier_id) REFERENCES carriers(id) ON DELETE SET NULL
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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE SET NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL,
            FOREIGN KEY (carrier_id) REFERENCES carriers(id) ON DELETE SET NULL
        )
    """)

    # ── contract_points ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contract_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER,
            point_type TEXT,
            sort_order INTEGER,
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

    conn.commit()

    # ── Резервная копия перед миграциями (Шаг 5 задания) ──
    # VACUUM INTO делает консистентную копию даже при включённом WAL.
    if _needs_migration(cursor):
        backup_path = backup_database(DB_PATH, reason="before_migration")
        if backup_path:
            audit.log_event(
                "database_backup",
                file=os.path.basename(backup_path),
                size_kb=backup_size_kb(backup_path),
            )
        else:
            logger.warning("Миграции выполняются без резервной копии")

    # ── Миграция: drivers ──
    needed_columns = [
        ("birth_place", "TEXT"),
        ("passport_issuer", "TEXT"),
        ("phone", "TEXT"),
    ]
    for col_name, col_type in needed_columns:
        if not _column_exists(cursor, "drivers", col_name):
            try:
                cursor.execute(f"ALTER TABLE drivers ADD COLUMN {col_name} {col_type}")
                logger.info(f"Добавлена колонка drivers.{col_name}")
            except Exception as e:
                logger.warning(f"Не удалось добавить колонку {col_name}: {e}")

    # ── Миграция: address_book.city ──
    if not _column_exists(cursor, "address_book", "city"):
        try:
            cursor.execute("ALTER TABLE address_book ADD COLUMN city TEXT")
            logger.info("Добавлена колонка address_book.city")
        except Exception as e:
            logger.warning(f"Не удалось добавить city: {e}")

    # ── Миграция: мягкое удаление (вариант В) ──
    # Существующие записи получают is_deleted = 0, то есть остаются видимыми:
    # поведение пользователя не меняется, меняется только способ удаления.
    for table in SOFT_DELETE_TABLES:
        if not _column_exists(cursor, table, "is_deleted"):
            try:
                cursor.execute(
                    f"ALTER TABLE {table} ADD COLUMN is_deleted INTEGER DEFAULT 0"
                )
                logger.info(
                    f"Добавлена колонка {table}.is_deleted (мягкое удаление)"
                )
            except Exception as e:
                logger.warning(f"Не удалось добавить {table}.is_deleted: {e}")

    for table in SOFT_DELETE_TABLES:
        try:
            cursor.execute(
                f"UPDATE {table} SET is_deleted = 0 WHERE is_deleted IS NULL"
            )
        except sqlite3.DatabaseError as e:
            logger.warning(f"Не удалось проставить is_deleted = 0 в {table}: {e}")

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

    conn.commit()

    # ── Миграция: заполняем city для существующих записей ──
    try:
        cursor.execute(
            "SELECT id, address FROM address_book "
            "WHERE city IS NULL OR city = ''"
        )
        rows = cursor.fetchall()
        if rows:
            logger.info(f"Извлекаем city для {len(rows)} адресов...")
            # Пакетное обновление вместо UPDATE на каждый адрес:
            # на больших справочниках это в разы быстрее.
            cursor.executemany(
                "UPDATE address_book SET city = ? WHERE id = ?",
                ((_extract_city(addr), addr_id) for addr_id, addr in rows),
            )
            conn.commit()
            logger.info(f"city заполнен для {len(rows)} адресов")
    except Exception as e:
        logger.warning(f"Не удалось заполнить city: {e}")

    # ── FTS5: индексы для регистронезависимого поиска по кириллице ──
    # Создаются один раз; при первом создании индекс наполняется
    # существующими данными. Триггеров нет — синхронизация из кода.
    try:
        if fts.ensure_schema(conn):
            logger.info(
                "Поиск: FTS5 включён (unicode61), резервный путь LIKE сохранён"
            )
        else:
            logger.info("Поиск: FTS5 недоступен, используется LIKE")
    except Exception as e:  # noqa: BLE001 — поиск не должен ломать запуск
        logger.warning(f"Не удалось подготовить FTS5-поиск: {e}")

    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")
    audit.log_event("database_initialized", db=os.path.basename(DB_PATH))

    # ── Права на файлы базы (Шаг 5 задания) ──
    # contracts.db содержит персональные данные водителей, поэтому доступ
    # оставляем только текущему пользователю (файл + WAL/SHM при наличии).
    restrict_to_current_user(DB_PATH)
    for suffix in ("-wal", "-shm"):
        sidecar = DB_PATH + suffix
        if os.path.exists(sidecar):
            restrict_to_current_user(sidecar)


# ─────────────────────────────────────────────────────────────
# CRUD: Водители
# ─────────────────────────────────────────────────────────────

def save_driver(driver_data: Dict[str, Any]) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO drivers (
            full_name, birth_date, birth_place,
            passport_series, passport_number,
            passport_issue_date, passport_issuer, passport_code,
            registration_address,
            license_series, license_number,
            license_issue_date, license_expiry_date, license_categories,
            phone
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        driver_data.get("full_name", ""),
        driver_data.get("birth_date", ""),
        driver_data.get("birth_place", ""),
        driver_data.get("passport_series", ""),
        driver_data.get("passport_number", ""),
        driver_data.get("passport_issue_date", ""),
        driver_data.get("passport_issuer", ""),
        driver_data.get("passport_code", ""),
        driver_data.get("registration_address", ""),
        driver_data.get("license_series", ""),
        driver_data.get("license_number", ""),
        driver_data.get("license_issue_date", ""),
        driver_data.get("license_expiry_date", ""),
        driver_data.get("license_categories", ""),
        driver_data.get("phone", ""),
    ))
    driver_id = cursor.lastrowid
    # FTS5: без триггеров — обновляем индекс вручную там же, где пишем данные.
    fts.replace_row(
        conn, "fts_drivers", driver_id, (driver_data.get("full_name", ""),)
    )
    conn.commit()
    conn.close()
    logger.info(f"Водитель сохранён: ID={driver_id}")
    return driver_id


def update_driver(driver_id: int, driver_data: Dict[str, Any]) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Старое ФИО нужно FTS5: удаление из индекса идёт по старым значениям.
        old = cursor.execute(
            "SELECT full_name FROM drivers WHERE id = ?", (driver_id,)
        ).fetchone()

        cursor.execute("""
            UPDATE drivers SET
                full_name = ?, birth_date = ?, birth_place = ?,
                passport_series = ?, passport_number = ?,
                passport_issue_date = ?, passport_issuer = ?, passport_code = ?,
                registration_address = ?,
                license_series = ?, license_number = ?,
                license_issue_date = ?, license_expiry_date = ?, license_categories = ?,
                phone = ?
            WHERE id = ?
        """, (
            driver_data.get("full_name", ""),
            driver_data.get("birth_date", ""),
            driver_data.get("birth_place", ""),
            driver_data.get("passport_series", ""),
            driver_data.get("passport_number", ""),
            driver_data.get("passport_issue_date", ""),
            driver_data.get("passport_issuer", ""),
            driver_data.get("passport_code", ""),
            driver_data.get("registration_address", ""),
            driver_data.get("license_series", ""),
            driver_data.get("license_number", ""),
            driver_data.get("license_issue_date", ""),
            driver_data.get("license_expiry_date", ""),
            driver_data.get("license_categories", ""),
            driver_data.get("phone", ""),
            driver_id,
        ))
        if old:
            fts.replace_row(
                conn, "fts_drivers", driver_id,
                (driver_data.get("full_name", ""),), (old[0],),
            )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления водителя: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def load_driver(driver_id: int) -> Optional[Dict[str, Any]]:
    """
    Водитель по ID — включая мягко удалённого (is_deleted = 1).

    Скрытие удалённых делает не эта функция, а списки/поиск
    (get_all_drivers / search_drivers): по ID запись нужна, например,
    чтобы показать связь договора или восстановить водителя.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM drivers WHERE id = ?", (driver_id,))
    row = cursor.fetchone()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return dict(zip(cols, row)) if row else None


def search_drivers(
    search_term: str,
    limit: int = 50,
    include_deleted: bool = False,
) -> List[Dict[str, Any]]:
    """
    Поиск водителей по ФИО, паспорту или телефону.

    Шаг 5 оптимизации: поиск целиком перенесён в SQL. Раньше UI выгружал
    всю таблицу водителей (get_all_drivers) и фильтровал её в Python —
    на 20 000 записях это ~80 мс на каждое нажатие клавиши.

    Регистр больше не важен для кириллицы: словесные запросы («иванов»,
    «ИВАНОВ») идут через FTS5 и находят «Иванов». Запросы с цифрами
    (паспорт «18 22 926830», телефон) и случаи, когда FTS ничего не нашёл,
    обслуживает прежний LIKE — поведение сохранено.

    Паспорт сравнивается так же, как раньше в интерфейсе: склейка
    «серия + пробел + номер», поэтому поиск по «18 22 926830» работает.

    include_deleted=True показывает и мягко удалённых (режим «Показывать
    удалённых» в менеджере базы) — иначе они не должны попадаться в поиске.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        only_active = 1 if include_deleted else 0

        # ── Путь 1: FTS5 по ФИО (регистронезависимо для кириллицы) ──
        match_query = fts.match_query_for(search_term) if search_term else None
        if match_query and fts.fts5_available(conn):
            try:
                _ensure_fts_fresh(conn, "fts_drivers")
                cursor.execute(
                    "SELECT d.* FROM drivers d "
                    "JOIN fts_drivers f ON f.rowid = d.id "
                    "WHERE fts_drivers MATCH ? AND (d.is_deleted = 0 OR ?) "
                    "ORDER BY d.full_name COLLATE NOCASE LIMIT ?",
                    (match_query, only_active, int(limit)),
                )
                rows = cursor.fetchall()
                if rows:
                    cols = [desc[0] for desc in cursor.description]
                    logger.debug(
                        f"Поиск водителей (FTS5): {search_term!r} -> "
                        f"{match_query!r}, найдено {len(rows)}"
                    )
                    return [dict(zip(cols, row)) for row in rows]
            except sqlite3.DatabaseError as e:
                logger.warning(f"FTS5-поиск водителей не удался, откат на LIKE: {e}")

        # ── Путь 2: LIKE (как раньше) ──
        like = f"%{search_term}%"
        cursor.execute(
            "SELECT * FROM drivers "
            "WHERE (is_deleted = 0 OR ?) AND ("
            "      full_name LIKE ? "
            "   OR (COALESCE(passport_series, '') || ' ' || COALESCE(passport_number, '')) LIKE ? "
            "   OR COALESCE(phone, '') LIKE ?) "
            "ORDER BY full_name COLLATE NOCASE LIMIT ?",
            (only_active, like, like, like, int(limit)),
        )
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def get_all_drivers(include_deleted: bool = False) -> List[Dict[str, Any]]:
    """Все водители; по умолчанию без мягко удалённых."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM drivers WHERE is_deleted = 0 OR ? "
        "ORDER BY full_name COLLATE NOCASE",
        (1 if include_deleted else 0,),
    )
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def restore_driver(driver_id: int) -> bool:
    """
    Возвращает мягко удалённого водителя в справочник (вариант В).

    Данные тягача/прицепа не трогались при удалении, поэтому после
    восстановления они снова доступны.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE drivers SET is_deleted = 0 WHERE id = ?", (driver_id,)
        )
        conn.commit()
        logger.info(f"Водитель восстановлен: ID={driver_id}")
        audit.log_event("driver_restored", driver_id=driver_id)
        return True
    except Exception as e:
        logger.error(f"Ошибка восстановления водителя: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def delete_driver(driver_id: int) -> bool:
    """
    Мягкое удаление водителя (вариант В): is_deleted = 1.

    Запись НЕ стирается и ссылки не рвутся:
      * водитель исчезает из списков и поиска (get_all_drivers,
        search_drivers), то есть для пользователя он удалён;
      * contracts.driver_id остаётся заполненным — история перевозок
        сохраняет, кто вёз;
      * данные тягача/прицепа (driver_vehicles) не удаляются;
      * запись можно вернуть: restore_driver().

    История изменений: сначала здесь был каскад (DELETE FROM contracts),
    затем SET NULL, теперь мягкое удаление.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT COUNT(*) FROM contracts WHERE driver_id = ?", (driver_id,)
        )
        linked_contracts = cursor.fetchone()[0]

        cursor.execute(
            "UPDATE drivers SET is_deleted = 1 WHERE id = ?", (driver_id,)
        )
        conn.commit()

        logger.info(
            f"Водитель удалён (мягко): ID={driver_id}, "
            f"договоров по-прежнему связано: {linked_contracts}"
        )
        audit.log_event(
            "driver_deleted",
            driver_id=driver_id,
            count=linked_contracts,
            entities="soft_delete",
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления водителя: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


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
                license_number, license_date
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
            org_data.get("license_number", ""),
            org_data.get("license_date", ""),
        ))
    else:
        cursor.execute("""
            INSERT INTO customers (
                full_name, short_name, inn, kpp, ogrn,
                legal_address, actual_address, bank_account,
                bik, correspondent_account, bank_name,
                director_name, director_position, phone, email
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    license_number = ?, license_date = ?
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
                org_data.get("license_date", ""), org_id,
            ))
        else:
            cursor.execute("""
                UPDATE customers SET
                    full_name = ?, short_name = ?, inn = ?, kpp = ?, ogrn = ?,
                    legal_address = ?, actual_address = ?, bank_account = ?,
                    bik = ?, correspondent_account = ?, bank_name = ?,
                    director_name = ?, director_position = ?, phone = ?, email = ?
                WHERE id = ?
            """, (
                org_data.get("full_name", ""), org_data.get("short_name", ""),
                org_data.get("inn", ""), org_data.get("kpp", ""),
                org_data.get("ogrn", ""), org_data.get("legal_address", ""),
                org_data.get("actual_address", ""), org_data.get("bank_account", ""),
                org_data.get("bik", ""), org_data.get("correspondent_account", ""),
                org_data.get("bank_name", ""), org_data.get("director_name", ""),
                org_data.get("director_position", ""), org_data.get("phone", ""),
                org_data.get("email", ""), org_id,
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

def save_vehicles(vehicles: List[Dict[str, Any]], carrier_id: Optional[int] = None) -> List[int]:
    conn = get_connection()
    cursor = conn.cursor()
    ids = []
    for vehicle in vehicles:
        cursor.execute("""
            INSERT INTO vehicles (carrier_id, vin, brand_model, plate_number, year, color, vehicle_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            carrier_id,
            vehicle.get("vin", ""),
            vehicle.get("brand_model", ""),
            vehicle.get("plate_number", ""),
            vehicle.get("year", 0),
            vehicle.get("color", ""),
            vehicle.get("vehicle_type", "Тягач"),
        ))
        ids.append(cursor.lastrowid)
    conn.commit()
    conn.close()
    logger.info(f"Сохранено ТС: {len(ids)}")
    return ids


# ─────────────────────────────────────────────────────────────
# Сохранение договора + точек
# ─────────────────────────────────────────────────────────────

def save_contract(contract_data: Dict[str, Any]) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO contracts (
            contract_number, contract_date, start_date, end_date,
            route, price_without_vat, vat_rate, price_with_vat,
            currency, special_conditions, driver_id, customer_id, carrier_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    ))
    contract_id = cursor.lastrowid
    conn.commit()
    conn.close()
    logger.info(f"Договор сохранён: ID={contract_id}")
    return contract_id


def save_contract_points(contract_id: int, loadings: List[Dict], unloadings: List[Dict]) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM contract_points WHERE contract_id = ?", (contract_id,))
    for i, l in enumerate(loadings):
        cursor.execute(
            "INSERT INTO contract_points (contract_id, point_type, sort_order, address, date, time_window) "
            "VALUES (?, 'loading', ?, ?, ?, ?)",
            (contract_id, i, l.get("address", ""), l.get("date", ""), l.get("time_window", ""))
        )
    for i, u in enumerate(unloadings):
        cursor.execute(
            "INSERT INTO contract_points (contract_id, point_type, sort_order, address, date, time_window) "
            "VALUES (?, 'unloading', ?, ?, ?, ?)",
            (contract_id, i, u.get("address", ""), u.get("date", ""), u.get("time_window", ""))
        )
    conn.commit()
    conn.close()


def load_contract_points(contract_id: int) -> Dict[str, List[Dict]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT point_type, sort_order, address, date, time_window "
        "FROM contract_points WHERE contract_id = ? ORDER BY point_type, sort_order",
        (contract_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    loadings, unloadings = [], []
    for ptype, order, addr, date, tw in rows:
        item = {"address": addr or "", "date": date or "", "time_window": tw or ""}
        if ptype == "loading":
            loadings.append(item)
        else:
            unloadings.append(item)
    return {"loadings": loadings, "unloadings": unloadings}


# ─────────────────────────────────────────────────────────────
# Address Book
# ─────────────────────────────────────────────────────────────

def save_address(point_type: str, address: str, date: str = "", time_window: str = "") -> Optional[int]:
    if not address or not address.strip():
        return None

    address = address.strip()
    date = (date or "").strip()
    time_window = (time_window or "").strip()
    city = _extract_city(address)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, usage_count, address, city FROM address_book "
            "WHERE point_type = ? AND address = ?",
            (point_type, address)
        )
        row = cursor.fetchone()
        if row:
            addr_id, usage, old_address, old_city = row
            cursor.execute(
                "UPDATE address_book SET usage_count = usage_count + 1, city = ?, "
                "date = CASE WHEN ? != '' THEN ? ELSE date END, "
                "time_window = CASE WHEN ? != '' THEN ? ELSE time_window END "
                "WHERE id = ?",
                (city, date, date, time_window, time_window, addr_id)
            )
            # FTS5: адрес не менялся, но город мог (правим индекс вручную,
            # без триггеров — см. db/fts.py).
            fts.replace_row(
                conn, "fts_addresses", addr_id,
                (old_address, city), (old_address, old_city),
            )
            conn.commit()
            return addr_id
        else:
            cursor.execute(
                "INSERT INTO address_book "
                "(point_type, address, city, date, time_window, usage_count) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (point_type, address, city, date, time_window)
            )
            addr_id = cursor.lastrowid
            fts.replace_row(conn, "fts_addresses", addr_id, (address, city))
            conn.commit()
            logger.info(f"Адрес добавлен: ID={addr_id}, город='{city}'")
            return addr_id
    except Exception as e:
        logger.error(f"Ошибка сохранения адреса: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def _ensure_fts_fresh(conn, fts_name: str) -> bool:
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


def _addresses_fts_count(cursor, point_type: str, match_query: str) -> int:
    """Сколько адресов этого типа найдёт FTS5 (0 — значит, идём в LIKE-резерв)."""
    cursor.execute(
        "SELECT COUNT(*) FROM address_book a "
        "JOIN fts_addresses f ON f.rowid = a.id "
        "WHERE fts_addresses MATCH ? AND a.point_type = ?",
        (match_query, point_type),
    )
    return int(cursor.fetchone()[0] or 0)


def get_addresses(
    point_type: str,
    search: str = "",
    limit: int = 500,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Возвращает СТРАНИЦУ адресов, отсортированных:
      1) по городу (city) — по алфавиту;
      2) при равенстве города — по всему адресу.

    Поиск по подстроке идёт двумя путями:
      * FTS5 (unicode61) — регистронезависимо для кириллицы: «мурманск»
        находит «Мурманск», поиск идёт по адресу и городу;
      * LIKE — резервный путь: если FTS5 недоступен, запрос содержит цифры
        (номер дома, индекс) или FTS ничего не нашёл (например, ищут середину
        слова «ольский» в «Кольский», чего токенизатор не умеет).

    Шаг 4 оптимизации:
      * жёсткий LIMIT 500 заменён параметрами limit/offset — при росте
        справочника адреса больше не «исчезают»: UI добирает их страницами
        (см. count_addresses и кнопку «Показать ещё»);
      * сортировка идёт по city/address COLLATE NOCASE, что соответствует
        индексу idx_address_book_sort_nocase — SQLite отдаёт первые N строк
        без сортировки всей таблицы (замер: 47.7 ms → 0.8 ms на 250k адресов).

    :param limit: размер страницы
    :param offset: сколько строк пропустить
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # ── Путь 1: FTS5 (быстрый и регистронезависимый) ──
        match_query = fts.match_query_for(search) if search else None
        if match_query and fts.fts5_available(conn):
            try:
                _ensure_fts_fresh(conn, "fts_addresses")
                found = _addresses_fts_count(cursor, point_type, match_query)
                if found:
                    cursor.execute(
                        "SELECT a.* FROM address_book a "
                        "JOIN fts_addresses f ON f.rowid = a.id "
                        "WHERE fts_addresses MATCH ? AND a.point_type = ? "
                        "ORDER BY a.city COLLATE NOCASE, a.address COLLATE NOCASE "
                        "LIMIT ? OFFSET ?",
                        (match_query, point_type, int(limit), int(offset)),
                    )
                    rows = cursor.fetchall()
                    cols = [desc[0] for desc in cursor.description]
                    logger.debug(
                        f"Поиск адресов (FTS5): {search!r} -> {match_query!r}, "
                        f"найдено {found}"
                    )
                    return [dict(zip(cols, row)) for row in rows]
            except sqlite3.DatabaseError as e:
                logger.warning(f"FTS5-поиск адресов не удался, откат на LIKE: {e}")

        # ── Путь 2: LIKE (как раньше + город) ──
        sql = "SELECT * FROM address_book WHERE point_type = ?"
        params: List[Any] = [point_type]

        if search:
            sql += " AND (address LIKE ? OR COALESCE(city, '') LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])

        sql += (
            " ORDER BY city COLLATE NOCASE, address COLLATE NOCASE"
            " LIMIT ? OFFSET ?"
        )
        params.extend([int(limit), int(offset)])

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def count_addresses(point_type: str, search: str = "") -> int:
    """
    Общее число адресов по фильтру (для пагинации в справочнике).

    Считает тем же путём, что и get_addresses: FTS5, а при недоступности
    или отсутствии совпадений — LIKE. Поэтому «Показано 500 из N» всегда
    согласовано со списком.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        match_query = fts.match_query_for(search) if search else None
        if match_query and fts.fts5_available(conn):
            try:
                _ensure_fts_fresh(conn, "fts_addresses")
                found = _addresses_fts_count(cursor, point_type, match_query)
                if found:
                    return found
            except sqlite3.DatabaseError as e:
                logger.warning(f"FTS5-подсчёт адресов не удался, откат на LIKE: {e}")

        if search:
            cursor.execute(
                "SELECT COUNT(*) FROM address_book "
                "WHERE point_type = ? AND (address LIKE ? OR COALESCE(city, '') LIKE ?)",
                (point_type, f"%{search}%", f"%{search}%"),
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM address_book WHERE point_type = ?",
                (point_type,),
            )

        return int(cursor.fetchone()[0] or 0)
    finally:
        conn.close()


def rebuild_fts_index() -> bool:
    """
    Полностью перестраивает поисковые индексы FTS5 (адреса, водители,
    организации).

    Нужна редко: при обычной работе индекс поддерживается точечно, а после
    массового импорта доливаются только новые строки. Метод пригодится,
    если база правилась в обход приложения (например, вручную в SQLite).
    """
    conn = get_connection()
    try:
        ok = fts.rebuild_all(conn)
        conn.commit()
        logger.info(f"Перестройка FTS-индексов: {'успешно' if ok else 'недоступно'}")
        return ok
    except sqlite3.DatabaseError as e:
        logger.warning(f"Не удалось перестроить FTS-индексы: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def update_address(addr_id: int, address: str, date: str = "", time_window: str = "") -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        city = _extract_city(address.strip())

        # Старые значения нужны FTS5: удаление из индекса идёт по ним.
        old = cursor.execute(
            "SELECT address, city FROM address_book WHERE id = ?", (addr_id,)
        ).fetchone()

        cursor.execute(
            "UPDATE address_book SET address = ?, city = ?, date = ?, time_window = ? WHERE id = ?",
            (address.strip(), city, date.strip(), time_window.strip(), addr_id)
        )
        if old:
            fts.replace_row(
                conn, "fts_addresses", addr_id,
                (address.strip(), city), (old[0], old[1]),
            )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления адреса: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def delete_address(addr_id: int) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        old = cursor.execute(
            "SELECT address, city FROM address_book WHERE id = ?", (addr_id,)
        ).fetchone()
        cursor.execute("DELETE FROM address_book WHERE id = ?", (addr_id,))
        if old:
            fts.delete_row(conn, "fts_addresses", addr_id, (old[0], old[1]))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления адреса: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def import_addresses_from_list(point_type: str, addresses: List[Dict[str, str]]) -> int:
    """
    Пакетный импорт адресов (Шаг 3 оптимизации).

    Раньше на каждый адрес выполнялись SELECT + INSERT/UPDATE — при импорте
    из Excel на тысячи строк это тысячи обращений к базе. Теперь одна
    команда executemany с ON CONFLICT по уникальному ключу
    (point_type, address): существующие адреса увеличивают usage_count,
    новые — добавляются.

    :return: сколько НОВЫХ адресов добавлено
    """
    if not addresses:
        return 0

    # Готовим строки заранее: пустые адреса пропускаем, город считаем здесь,
    # чтобы не делать это внутри цикла работы с БД.
    rows = []
    for item in addresses:
        addr = (item.get("address") or "").strip()
        if not addr:
            continue
        rows.append((
            point_type,
            addr,
            _extract_city(addr),
            (item.get("date") or "").strip(),
            (item.get("time_window") or "").strip(),
        ))

    if not rows:
        return 0

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Разница в количестве записей = число новых адресов.
        # Считаем до и после в одной транзакции, поэтому значение точное.
        before = cursor.execute(
            "SELECT COUNT(*) FROM address_book WHERE point_type = ?", (point_type,)
        ).fetchone()[0]

        cursor.executemany(
            "INSERT INTO address_book "
            "(point_type, address, city, date, time_window, usage_count) "
            "VALUES (?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(point_type, address) DO UPDATE SET "
            "    usage_count = usage_count + 1, "
            "    city = COALESCE(NULLIF(excluded.city, ''), address_book.city)",
            rows,
        )

        # FTS5: индекс здесь НЕ трогаем — импорт не должен платить за него.
        # Новые строки доливаются при первом поиске (database._ensure_fts_fresh
        # видит расхождение last_id и добавляет ровно новые записи) либо
        # вручную через rebuild_fts_index(). Так массовый импорт из Excel
        # остаётся ровно таким же быстрым, как до внедрения FTS5.
        conn.commit()

        after = cursor.execute(
            "SELECT COUNT(*) FROM address_book WHERE point_type = ?", (point_type,)
        ).fetchone()[0]

        added = after - before
        logger.info(
            f"Импортировано в '{point_type}': обработано {len(rows)}, новых {added} "
            f"(FTS-индекс догонится при первом поиске)"
        )
        audit.log_event(
            "addresses_imported", point_type=point_type,
            count=len(rows), added=added,
        )
        return added
    except Exception as e:
        logger.error(f"Ошибка импорта адресов: {e}")
        conn.rollback()
        return 0
    finally:
        conn.close()