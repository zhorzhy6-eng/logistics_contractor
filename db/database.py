#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль инициализации и работы с локальной базой данных SQLite.

Хранит данные водителей, организаций, ТС, договоров и справочник адресов.

Все SELECT-запросы возвращают данные ОТСОРТИРОВАННЫМИ.

Для справочника адресов дополнительно извлекается ГОРОД (колонка city),
по которому идёт сортировка: сначала по городу, потом по всему адресу.
"""

import datetime
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

#: Файл справочника салонов («Места выгрузок»), который раскладывается по
#: заголовкам граф. Лежит в data/ — там же, где остальные входные файлы.
SALONS_XLSX_NAME = "spravochnik_mest_vygruzki.xlsx"

#: Ключевые слова заголовков файла справочника салонов → имена полей.
#: Сравнение идёт по подстроке в шапке (регистр не важен): «КОД (второй)»
#: и «КОД» — одна и та же графа, а «Адрес доставки автомобилей» находится
#: по слову «Адрес доставки».
SALON_COLUMN_KEYS = (
    ("salon_code", ("код",)),
    ("salon_inn", ("инн",)),
    ("salon_name", ("юр. лицо", "юридическое лицо")),
    ("salon_city", ("город",)),
    ("address", ("адрес доставки",)),
)


def salons_xlsx_path() -> str:
    """Путь к файлу справочника салонов в папке data/ проекта."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", SALONS_XLSX_NAME)


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
        # Привязка водителей к перевозчикам (ШАГ «Привязка водителей
        # к перевозчикам»): основной перевозчик водителя. Перед этой
        # миграцией тоже делается резервная копия базы.
        ("drivers", "default_carrier_id"),
        ("address_book", "city"),
        ("address_book", "salon_name"),
        ("address_book", "salon_code"),
        ("address_book", "salon_inn"),
        ("address_book", "salon_city"),
        ("vehicles", "contract_id"),
        # Наименование салона в точке маршрута (ШАГ FIX-6, часть F):
        # перед этой миграцией тоже делается резервная копия базы.
        ("contract_points", "name"),
        # Предоплата (ШАГ «Предоплата»): сумма и процент по договору.
        # Колонки общие для таблицы contracts, но заполняет их только
        # Экспедиторство — остальные типы пишут 0 по умолчанию.
        ("contracts", "prepayment_amount"),
        ("contracts", "prepayment_percent"),
        # Вид лица и основание полномочий стороны (ШАГ «Полные стороны +
        # склонение с учётом рода»). В форме редактирования заказчика и
        # перевозчика поля были, а в базе их не было: после сохранения вид
        # («ООО» / «ИП с НДС» / «ИП без НДС») и основание («Устава» /
        # «свидетельства о государственной регистрации») терялись, и в
        # договоре сторона печаталась с чужой формулировкой.
        ("carriers", "entity_type"),
        ("carriers", "basis"),
        ("customers", "entity_type"),
        ("customers", "basis"),
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


def _find_salon_columns(header: Tuple[Any, ...]) -> Dict[str, int]:
    """
    Индексы граф файла справочника салонов по ключевым словам шапки.

    Возвращает {имя поля: индекс колонки}. Ключ «код» ищется ПЕРВЫМ
    совпадением по порядку граф (в файле две графы КОД — берём левую).
    Если ни одного ключа не найдено, словарь пуст: файл не похож на
    справочник салонов, импортировать его построчно нельзя.
    """
    found: Dict[str, int] = {}
    for index, title in enumerate(header):
        text = str(title or "").strip().lower()
        if not text:
            continue
        for field, keywords in SALON_COLUMN_KEYS:
            if field in found:
                continue
            if any(keyword in text for keyword in keywords):
                found[field] = index
                break
    return found


def read_salons_rows(path: str) -> List[Dict[str, str]]:
    """
    Читает файл «Места выгрузок» и отдаёт строки справочника салонов.

    Графы ищутся по ЗАГОЛОВКАМ (первая непустая строка), а не по номерам:
    порядок колонок в файле заказчика может меняться. Ожидаемые графы —
    КОД, ИНН, Юр. Лицо, Город, Адрес доставки автомобилей; лишние графы
    (e-mail, телефоны, комментарии, региональный менеджер) не читаются:
    в справочнике им места нет, а контакты — персональные данные.

    Возвращает список словарей {address, salon_name, salon_code, salon_inn,
    salon_city, date, time_window}. Пустой список — либо файла нет, либо
    шапка не распознана (тогда вызывающий код импорт не делает).
    """
    if not path or not os.path.exists(path):
        logger.info(f"Файл справочника салонов не найден: {path}")
        return []

    try:
        import openpyxl
    except ImportError:
        logger.warning("openpyxl не установлен — справочник салонов не загружен")
        return []

    try:
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as e:  # noqa: BLE001 — файл может быть занят или битым
        logger.warning(f"Не удалось открыть справочник салонов ({type(e).__name__})")
        return []

    try:
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)

        header = None
        columns: Dict[str, int] = {}
        for row in rows:
            if row is None or all(value is None or not str(value).strip() for value in row):
                continue
            header = row
            columns = _find_salon_columns(row)
            break

        if not columns or "address" not in columns:
            logger.warning(
                "В файле справочника салонов не найдена ожидаемая шапка "
                "(КОД / ИНН / Юр. Лицо / Город / Адрес доставки) — импорт пропущен"
            )
            return []

        def cell(row: Tuple[Any, ...], field: str) -> str:
            index = columns.get(field)
            if index is None or index >= len(row):
                return ""
            value = row[index]
            return "" if value is None else str(value).strip()

        result: List[Dict[str, str]] = []
        for row in rows:
            if row is None:
                continue
            address = cell(row, "address")
            if not address:
                continue
            result.append({
                "address": address,
                "salon_name": cell(row, "salon_name"),
                "salon_code": cell(row, "salon_code"),
                "salon_inn": cell(row, "salon_inn"),
                "salon_city": cell(row, "salon_city"),
                "date": "",
                "time_window": "",
            })

        return result
    finally:
        try:
            workbook.close()
        except Exception:  # noqa: BLE001 — закрытие не должно ломать импорт
            pass


def _load_salons_if_empty() -> int:
    """
    Первый запуск: если справочник салонов ещё не залит, читает файл.

    «Не залит» — это два случая:
      * таблица выгрузок пуста (чистая база) — заливается всё;
      * записи есть, но НИ У ОДНОЙ нет кода салона (база старше FIX-2.2:
        адреса набирались вручную или прежним импортом) — тогда файл
        догружает недостающие поля и добавляет записи, которых нет.
        Значения существующих записей не теряются: адрес — ключ уникальности,
        а пустые поля только дополняются (см. import_addresses_from_list).

    Если файла нет или в нём не распознана шапка — тихо ничего не делаем:
    приложение работает и без него, справочник наполняется вручную.

    :return: сколько записей импортировано (0 — импорта не было)
    """
    conn = get_connection()
    try:
        total = int(
            conn.execute(
                "SELECT COUNT(*) FROM address_book WHERE point_type = 'unloading'"
            ).fetchone()[0] or 0
        )
        with_code = int(
            conn.execute(
                "SELECT COUNT(*) FROM address_book "
                "WHERE point_type = 'unloading' AND COALESCE(salon_code, '') != ''"
            ).fetchone()[0] or 0
        )
    except sqlite3.DatabaseError as e:
        logger.warning(f"Не удалось проверить справочник выгрузок: {e}")
        return 0
    finally:
        conn.close()

    if total and with_code:
        logger.debug(
            f"Справочник салонов уже загружен ({with_code} записей с кодом)"
        )
        return 0

    rows = read_salons_rows(salons_xlsx_path())
    if not rows:
        logger.info(
            "Справочник салонов не загружен автоматически "
            "(нет файла или не распознана шапка)"
        )
        return 0

    added = import_addresses_from_list("unloading", rows)
    logger.info(
        f"Загружен справочник салонов: {len(rows)} записей, новых {added} "
        f"(было в базе: {total})"
    )
    return added


def init_database() -> None:
    """Инициализирует таблицы в БД и добавляет недостающие колонки."""
    conn = get_connection()
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

    # ── Миграция: drivers.default_carrier_id (ШАГ «Привязка водителей
    # к перевозчикам») ──
    # Основной перевозчик водителя: ALTER TABLE ADD COLUMN добавляет колонку
    # к существующей таблице и НЕ трогает строки — у уже заведённых водителей
    # значение NULL (привязки нет, поведение прежнее).
    if not _column_exists(cursor, "drivers", "default_carrier_id"):
        try:
            cursor.execute(
                "ALTER TABLE drivers ADD COLUMN default_carrier_id INTEGER "
                "REFERENCES carriers(id) ON DELETE SET NULL"
            )
            logger.info("Добавлена колонка drivers.default_carrier_id")
        except Exception as e:
            logger.warning(
                f"Не удалось добавить drivers.default_carrier_id: {e}"
            )

    if not _column_exists(cursor, "vehicles", "contract_id"):
        cursor.execute(
            "ALTER TABLE vehicles ADD COLUMN contract_id INTEGER "
            "REFERENCES contracts(id) ON DELETE CASCADE"
        )
        logger.info("Добавлена колонка vehicles.contract_id")

    # ── Миграция: address_book.city ──
    if not _column_exists(cursor, "address_book", "city"):
        try:
            cursor.execute("ALTER TABLE address_book ADD COLUMN city TEXT")
            logger.info("Добавлена колонка address_book.city")
        except Exception as e:
            logger.warning(f"Не удалось добавить city: {e}")

    # ── Миграция: contract_points.name (ШАГ FIX-6, часть F) ──
    # В таблице точек маршрута не было колонки с наименованием салона, хотя
    # в бланк оно попадает из формы: после перезагрузки сохранённого
    # договора имя терялось. ALTER TABLE ADD COLUMN добавляет колонку к
    # существующей таблице и НЕ трогает строки: у уже сохранённых договоров
    # name = NULL, а `load_contract_points` отдаёт его пустой строкой.
    if not _column_exists(cursor, "contract_points", "name"):
        try:
            cursor.execute("ALTER TABLE contract_points ADD COLUMN name TEXT")
            logger.info("Добавлена колонка contract_points.name")
        except Exception as e:
            logger.warning(f"Не удалось добавить contract_points.name: {e}")

    # ── Миграция: contracts — предоплата (ШАГ «Предоплата») ──
    # Сумма и процент предоплаты по договору. Процент хранится рядом с
    # суммой, хотя его можно пересчитать: он показывает, каким оператор
    # видел договор на момент сохранения (стоимость могли потом изменить).
    # ALTER TABLE ADD COLUMN добавляет колонки к существующей таблице и НЕ
    # трогает строки: у уже сохранённых договоров предоплата = 0.
    for column, sql_type in (
        ("prepayment_amount", "REAL DEFAULT 0"),
        ("prepayment_percent", "REAL DEFAULT 0"),
    ):
        if not _column_exists(cursor, "contracts", column):
            try:
                cursor.execute(f"ALTER TABLE contracts ADD COLUMN {column} {sql_type}")
                logger.info(f"Добавлена колонка contracts.{column}")
            except Exception as e:
                logger.warning(f"Не удалось добавить contracts.{column}: {e}")

    # ── Миграция: организации — вид лица и основание (ШАГ «Полные стороны
    # + склонение с учётом рода») ──
    # В форме редактирования заказчика и перевозчика поля «Тип» и
    # «Основание» были, а в базе — нет: после сохранения они терялись, и
    # при загрузке записи в форму вид стороны сбрасывался, а в договоре
    # ИП печатался как ООО. ALTER TABLE ADD COLUMN добавляет колонки к
    # существующей таблице и НЕ трогает строки: у уже заведённых сторон
    # entity_type = NULL, и вид стороны выводится из ИНН и наименования.
    for table in ("customers", "carriers"):
        for column, sql_type in (("entity_type", "TEXT"), ("basis", "TEXT")):
            if not _column_exists(cursor, table, column):
                try:
                    cursor.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
                    )
                    logger.info(f"Добавлена колонка {table}.{column}")
                except Exception as e:
                    logger.warning(f"Не удалось добавить {table}.{column}: {e}")

    # ── Миграция: address_book — справочник салонов (ШАГ FIX-2.2) ──
    # Справочник адресов стал справочником МЕСТ ВЫГРУЗКИ: у записи появились
    # код салона (JMR-Axxx), наименование юр. лица, ИНН и город салона.
    # Колонки добавляются к существующей таблице: старые записи не трогаются,
    # их данные остаются на месте.
    for column, sql_type in (
        ("salon_name", "TEXT"),
        ("salon_code", "TEXT"),
        ("salon_inn", "TEXT"),
        ("salon_city", "TEXT"),
    ):
        if not _column_exists(cursor, "address_book", column):
            try:
                cursor.execute(
                    f"ALTER TABLE address_book ADD COLUMN {column} {sql_type}"
                )
                logger.info(f"Добавлена колонка address_book.{column}")
            except Exception as e:
                logger.warning(f"Не удалось добавить {column}: {e}")

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
    restrict_to_current_user(DB_PATH)
    for suffix in ("-wal", "-shm"):
        sidecar = DB_PATH + suffix
        if os.path.exists(sidecar):
            restrict_to_current_user(sidecar)


# ─────────────────────────────────────────────────────────────
# CRUD: Водители
# ─────────────────────────────────────────────────────────────

def _carrier_id_or_none(value: Any) -> Optional[int]:
    """
    ID перевозчика из данных формы: пустое значение — это NULL.

    Вкладка отдаёт «— не указан —» как None, но через промежуточные
    словари значение может прийти пустой строкой или нулём — все они
    значат «привязки нет».
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


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
            phone, default_carrier_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        # Основной перевозчик (ШАГ «Привязка водителей к перевозчикам»).
        # Ключа нет — водитель заводится без привязки (NULL).
        _carrier_id_or_none(driver_data.get("default_carrier_id")),
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

        # Основной перевозчик (ШАГ «Привязка водителей к перевозчикам»)
        # обновляется ТОЛЬКО когда ключ пришёл в данных: вкладка аренды
        # собирает запись из своих полей (merge_driver_records), и этого
        # ключа в ней нет — иначе сохранение из аренды обнулило бы
        # привязку, выставленную в «Экспедиторстве».
        if "default_carrier_id" in driver_data:
            cursor.execute(
                "UPDATE drivers SET default_carrier_id = ? WHERE id = ?",
                (
                    _carrier_id_or_none(driver_data.get("default_carrier_id")),
                    driver_id,
                ),
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
    with_carrier_name: bool = False,
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

    with_carrier_name=True добавляет к записи название основного
    перевозчика (`carrier_name`) — менеджеру базы нужна колонка
    «Перевозчик». По умолчанию (False) набор колонок прежний, чтобы
    не менять существующие вызовы.
    """
    conn = get_connection()
    cursor = conn.cursor()

    carrier_column = (
        ", COALESCE(NULLIF(TRIM(c.full_name), ''), c.short_name, '') "
        "AS carrier_name"
        if with_carrier_name else ""
    )
    carrier_join = (
        "LEFT JOIN carriers c ON c.id = d.default_carrier_id "
        if with_carrier_name else ""
    )

    try:
        only_active = 1 if include_deleted else 0

        # ── Путь 1: FTS5 по ФИО (регистронезависимо для кириллицы) ──
        match_query = fts.match_query_for(search_term) if search_term else None
        if match_query and fts.fts5_available(conn):
            try:
                _ensure_fts_fresh(conn, "fts_drivers")
                cursor.execute(
                    f"SELECT d.*{carrier_column} FROM drivers d "
                    f"{carrier_join}"
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
            f"SELECT d.*{carrier_column} FROM drivers d "
            f"{carrier_join}"
            "WHERE (d.is_deleted = 0 OR ?) AND ("
            "      d.full_name LIKE ? "
            "   OR (COALESCE(d.passport_series, '') || ' ' || COALESCE(d.passport_number, '')) LIKE ? "
            "   OR COALESCE(d.phone, '') LIKE ?) "
            "ORDER BY d.full_name COLLATE NOCASE LIMIT ?",
            (only_active, like, like, like, int(limit)),
        )
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def get_all_drivers(
    include_deleted: bool = False,
    with_carrier_name: bool = False,
) -> List[Dict[str, Any]]:
    """
    Все водители; по умолчанию без мягко удалённых.

    with_carrier_name=True добавляет название основного перевозчика
    (`carrier_name`); по умолчанию набор колонок прежний.
    """
    carrier_column = (
        ", COALESCE(NULLIF(TRIM(c.full_name), ''), c.short_name, '') "
        "AS carrier_name"
        if with_carrier_name else ""
    )
    carrier_join = (
        "LEFT JOIN carriers c ON c.id = d.default_carrier_id "
        if with_carrier_name else ""
    )

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT d.*{carrier_column} FROM drivers d "
        f"{carrier_join}"
        "WHERE d.is_deleted = 0 OR ? "
        "ORDER BY d.full_name COLLATE NOCASE",
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
# CRUD: Привязка водителей к перевозчикам
# ─────────────────────────────────────────────────────────────
# ШАГ «Привязка водителей к перевозчикам». До него связь «водитель ↔
# перевозчик» существовала только через договоры (contracts.driver_id +
# contracts.carrier_id), и в справочнике водителей не было видно, на кого
# он работает. Теперь у водителя есть основной перевозчик
# (drivers.default_carrier_id), а таблица driver_carriers хранит историю:
# запись без ended_at — активная связь.
#
# Мягкое удаление ничего не рвёт: delete_driver() не трогает историю
# (её видно после restore_driver), а delete_organization(is_carrier=True)
# не обнуляет drivers.default_carrier_id — запись перевозчика остаётся
# в базе, как и ссылки договоров.

def _active_link_sql(prefix: str = "") -> str:
    """
    Условие «связь ещё активна» для колонки ended_at.

    Пустая строка приравнена к NULL: так активными остаются и записи,
    добавленные в обход link_driver_to_carrier.

    :param prefix: префикс таблицы («dc.»), если запрос с JOIN.
    """
    column = f"{prefix}ended_at"
    return f"({column} IS NULL OR {column} = '')"


#: То же условие без префикса таблицы — для запросов к одной driver_carriers.
ACTIVE_LINK_SQL = _active_link_sql()


def _today_iso() -> str:
    """Сегодняшняя дата в ISO (ГГГГ-ММ-ДД) — формат дат справочника."""
    return datetime.date.today().isoformat()


def link_driver_to_carrier(
    driver_id: int,
    carrier_id: int,
    started_at: str = "",
    ended_at: str = "",
) -> int:
    """
    Добавляет запись в driver_carriers.

    Если у водителя уже есть АКТИВНАЯ связь с этим же перевозчиком
    (ended_at пуст) — новую не создаёт, возвращает id существующей.

    Переход к ДРУГОМУ перевозчику закрывает прежние активные связи: у них
    проставляется ended_at (дата начала новой связи, а если она не задана —
    сегодняшняя). Так в истории не остаётся двух «текущих» перевозчиков.

    :param started_at: дата начала работы (пусто — сегодняшняя).
    :param ended_at: дата окончания (пусто — связь активная).
    :return: id записи в driver_carriers.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id FROM driver_carriers "
            "WHERE driver_id = ? AND carrier_id = ? "
            f"  AND {ACTIVE_LINK_SQL} "
            "ORDER BY id DESC LIMIT 1",
            (driver_id, carrier_id),
        )
        row = cursor.fetchone()
        if row:
            logger.debug(
                f"Связь водителя ID={driver_id} с перевозчиком "
                f"ID={carrier_id} уже активна — запись не создаётся"
            )
            return int(row[0])

        start = str(started_at or "").strip() or _today_iso()
        finish = str(ended_at or "").strip() or None

        cursor.execute(
            "UPDATE driver_carriers SET ended_at = ? "
            "WHERE driver_id = ? AND carrier_id != ? "
            f"  AND {ACTIVE_LINK_SQL}",
            (start, driver_id, carrier_id),
        )
        closed = cursor.rowcount

        cursor.execute(
            "INSERT INTO driver_carriers "
            "(driver_id, carrier_id, started_at, ended_at) "
            "VALUES (?, ?, ?, ?)",
            (driver_id, carrier_id, start, finish),
        )
        link_id = cursor.lastrowid
        conn.commit()

        logger.info(
            f"Водитель привязан к перевозчику: driver_id={driver_id}, "
            f"carrier_id={carrier_id}, закрыто прежних связей: {closed}"
        )
        audit.log_event(
            "driver_linked_to_carrier",
            driver_id=driver_id,
            carrier_id=carrier_id,
            count=closed,
        )
        return int(link_id)
    except Exception as e:
        logger.error(f"Ошибка привязки водителя к перевозчику: {e}")
        if conn:
            conn.rollback()
        return 0
    finally:
        if conn:
            conn.close()


def unlink_driver_from_carrier(
    driver_id: int,
    carrier_id: int,
    ended_at: str = "",
) -> bool:
    """
    Закрывает активную связь (ставит ended_at).

    Если ended_at пуст — сегодняшняя дата. Запись НЕ удаляется: история
    работы у перевозчика остаётся в базе.

    :return: True, если активная связь была и закрыта.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        finish = str(ended_at or "").strip() or _today_iso()
        cursor.execute(
            "UPDATE driver_carriers SET ended_at = ? "
            "WHERE driver_id = ? AND carrier_id = ? "
            f"  AND {ACTIVE_LINK_SQL}",
            (finish, driver_id, carrier_id),
        )
        closed = cursor.rowcount
        conn.commit()

        logger.info(
            f"Связь водителя с перевозчиком закрыта: driver_id={driver_id}, "
            f"carrier_id={carrier_id}, записей закрыто: {closed}"
        )
        if closed:
            audit.log_event(
                "driver_unlinked_from_carrier",
                driver_id=driver_id,
                carrier_id=carrier_id,
                count=closed,
            )
        return closed > 0
    except Exception as e:
        logger.error(f"Ошибка закрытия связи водителя с перевозчиком: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def get_driver_carriers(
    driver_id: int,
    active_only: bool = False,
) -> List[Dict[str, Any]]:
    """
    История работы водителя у перевозчиков.

    С JOIN на carriers, чтобы вернуть название перевозчика
    (`carrier_name`). Сортировка: активные первыми, потом по started_at
    по убыванию (свежие — выше).

    :param active_only: только действующие связи (ended_at пуст).
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        sql = (
            "SELECT dc.id, dc.driver_id, dc.carrier_id, "
            "       dc.started_at, dc.ended_at, dc.created_at, "
            "       COALESCE(NULLIF(TRIM(c.full_name), ''), c.short_name, '') "
            "           AS carrier_name, "
            "       COALESCE(c.short_name, '') AS carrier_short_name, "
            "       COALESCE(c.inn, '') AS carrier_inn "
            "FROM driver_carriers dc "
            "LEFT JOIN carriers c ON c.id = dc.carrier_id "
            "WHERE dc.driver_id = ?"
        )
        if active_only:
            sql += f" AND {_active_link_sql('dc.')}"
        sql += (
            " ORDER BY (dc.ended_at IS NULL OR dc.ended_at = '') DESC, "
            "          dc.started_at DESC, dc.id DESC"
        )

        cursor.execute(sql, (driver_id,))
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def get_carrier_drivers(
    carrier_id: int,
    active_only: bool = True,
) -> List[Dict[str, Any]]:
    """
    Водители, работающие у перевозчика.

    С JOIN на drivers: возвращаются все колонки водителя плюс поля связи
    (`link_id`, `carrier_id`, `started_at`, `ended_at`).

    :param active_only: только действующие связи (ended_at пуст).
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        sql = (
            "SELECT d.*, dc.id AS link_id, dc.carrier_id AS carrier_id, "
            "       dc.started_at AS started_at, dc.ended_at AS ended_at "
            "FROM driver_carriers dc "
            "JOIN drivers d ON d.id = dc.driver_id "
            "WHERE dc.carrier_id = ?"
        )
        if active_only:
            sql += f" AND {_active_link_sql('dc.')}"
        sql += " ORDER BY d.full_name COLLATE NOCASE"

        cursor.execute(sql, (carrier_id,))
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def set_default_carrier(
    driver_id: int,
    carrier_id: Optional[int],
) -> bool:
    """
    Устанавливает основной перевозчик водителя.

    Если carrier_id заполнен — обновляет drivers.default_carrier_id и (если
    активной связи ещё нет) пишет запись в driver_carriers.
    Если carrier_id = None — очищает default_carrier_id; история работы
    (driver_carriers) при этом НЕ трогается.

    :return: True, если значение записано.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE drivers SET default_carrier_id = ? WHERE id = ?",
            (carrier_id, driver_id),
        )
        updated = cursor.rowcount
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка установки основного перевозчика: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

    if carrier_id is None:
        logger.info(f"Основной перевозчик водителя очищен: driver_id={driver_id}")
        return updated > 0

    logger.info(
        f"Основной перевозчик водителя: driver_id={driver_id}, "
        f"carrier_id={carrier_id}"
    )
    # Активной связи может не быть (например, водителя только что завели):
    # тогда она появляется здесь же. Если связь уже есть — link ничего
    # не создаёт.
    link_driver_to_carrier(driver_id, carrier_id)
    return True


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
# CRUD: КОНТРАГЕНТЫ (шаг 3 инфраструктуры типов договоров)
# ─────────────────────────────────────────────────────────────
# Таблица counterparties независима от carriers/customers: контрагент
# привязан к типу договора (contract_type) и роли стороны (role).
# «Экспедиторство» продолжает работать со своими таблицами — здесь только
# новый справочник для будущих типов (Формика, Логистикс Рус, аренда).

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


# ─────────────────────────────────────────────────────────────
# Address Book
# ─────────────────────────────────────────────────────────────

def save_address(
    point_type: str,
    address: str,
    date: str = "",
    time_window: str = "",
    salon_name: str = "",
    salon_code: str = "",
    salon_inn: str = "",
    salon_city: str = "",
) -> Optional[int]:
    """
    Сохраняет адрес в справочнике (или обновляет существующий).

    Четыре первых параметра — прежний вызов (point_type, address, date,
    time_window): старые вызовы продолжают работать без правок. Поля
    салона (ШАГ FIX-2.2) — опциональные: пустое значение НЕ затирает то,
    что уже записано в справочнике.
    """
    if not address or not address.strip():
        return None

    address = address.strip()
    date = (date or "").strip()
    time_window = (time_window or "").strip()
    salon_name = (salon_name or "").strip()
    salon_code = (salon_code or "").strip()
    salon_inn = (salon_inn or "").strip()
    salon_city = (salon_city or "").strip()
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
                "time_window = CASE WHEN ? != '' THEN ? ELSE time_window END, "
                "salon_name = CASE WHEN ? != '' THEN ? ELSE salon_name END, "
                "salon_code = CASE WHEN ? != '' THEN ? ELSE salon_code END, "
                "salon_inn  = CASE WHEN ? != '' THEN ? ELSE salon_inn END, "
                "salon_city = CASE WHEN ? != '' THEN ? ELSE salon_city END "
                "WHERE id = ?",
                (city, date, date, time_window, time_window,
                 salon_name, salon_name, salon_code, salon_code,
                 salon_inn, salon_inn, salon_city, salon_city, addr_id)
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
                "(point_type, address, city, date, time_window, usage_count, "
                " salon_name, salon_code, salon_inn, salon_city) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (point_type, address, city, date, time_window,
                 salon_name, salon_code, salon_inn, salon_city)
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
        Сюда же попадает поиск по ПОЛЯМ САЛОНА (ШАГ FIX-2.2): код
        («JMR-A048»), наименование юр. лица и ИНН в FTS-индекс не входят,
        поэтому такие запросы всегда идут через LIKE.

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

        # ── Путь 2: LIKE (как раньше + город + поля салона) ──
        sql = "SELECT * FROM address_book WHERE point_type = ?"
        params: List[Any] = [point_type]

        if search:
            sql += (
                " AND (address LIKE ? OR COALESCE(city, '') LIKE ?"
                " OR COALESCE(salon_name, '') LIKE ?"
                " OR COALESCE(salon_code, '') LIKE ?"
                " OR COALESCE(salon_inn, '') LIKE ?)"
            )
            params.extend([f"%{search}%"] * 5)

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
                "WHERE point_type = ? AND (address LIKE ? OR COALESCE(city, '') LIKE ?"
                " OR COALESCE(salon_name, '') LIKE ?"
                " OR COALESCE(salon_code, '') LIKE ?"
                " OR COALESCE(salon_inn, '') LIKE ?)",
                (point_type, *([f"%{search}%"] * 5)),
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


def update_address(
    addr_id: int,
    address: str,
    date: str = "",
    time_window: str = "",
    salon_name: str = "",
    salon_code: str = "",
    salon_inn: str = "",
    salon_city: str = "",
) -> bool:
    """
    Обновляет запись справочника адресов.

    Первые четыре параметра — прежний вызов; поля салона (ШАГ FIX-2.2)
    опциональны и по умолчанию пусты — тогда колонка сохраняет прежнее
    значение (CASE WHEN '' THEN старое), чтобы правка адреса из старого
    окна не стирала данные салона.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        city = _extract_city(address.strip())
        salon_name = (salon_name or "").strip()
        salon_code = (salon_code or "").strip()
        salon_inn = (salon_inn or "").strip()
        salon_city = (salon_city or "").strip()

        # Старые значения нужны FTS5: удаление из индекса идёт по ним.
        old = cursor.execute(
            "SELECT address, city FROM address_book WHERE id = ?", (addr_id,)
        ).fetchone()

        cursor.execute(
            "UPDATE address_book SET address = ?, city = ?, date = ?, "
            "time_window = ?, "
            "salon_name = CASE WHEN ? != '' THEN ? ELSE salon_name END, "
            "salon_code = CASE WHEN ? != '' THEN ? ELSE salon_code END, "
            "salon_inn  = CASE WHEN ? != '' THEN ? ELSE salon_inn END, "
            "salon_city = CASE WHEN ? != '' THEN ? ELSE salon_city END "
            "WHERE id = ?",
            (address.strip(), city, date.strip(), time_window.strip(),
             salon_name, salon_name, salon_code, salon_code,
             salon_inn, salon_inn, salon_city, salon_city, addr_id)
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

    ШАГ FIX-2.2: элемент может нести поля салона (salon_name, salon_code,
    salon_inn, salon_city) — они пишутся в свои колонки. Пустое значение
    НЕ затирает уже записанное: COALESCE(NULLIF(excluded.X, ''), X).

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
            (item.get("salon_name") or "").strip(),
            (item.get("salon_code") or "").strip(),
            (item.get("salon_inn") or "").strip(),
            (item.get("salon_city") or "").strip(),
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
            "(point_type, address, city, date, time_window, usage_count, "
            " salon_name, salon_code, salon_inn, salon_city) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?) "
            "ON CONFLICT(point_type, address) DO UPDATE SET "
            "    usage_count = usage_count + 1, "
            "    city = COALESCE(NULLIF(excluded.city, ''), address_book.city), "
            "    salon_name = COALESCE(NULLIF(excluded.salon_name, ''), "
            "                          address_book.salon_name), "
            "    salon_code = COALESCE(NULLIF(excluded.salon_code, ''), "
            "                          address_book.salon_code), "
            "    salon_inn  = COALESCE(NULLIF(excluded.salon_inn, ''), "
            "                          address_book.salon_inn), "
            "    salon_city = COALESCE(NULLIF(excluded.salon_city, ''), "
            "                          address_book.salon_city)",
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
