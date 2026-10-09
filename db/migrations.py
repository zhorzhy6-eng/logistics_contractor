#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Миграции схемы базы данных.

Модуль доводит существующую базу до текущей схемы: добавляет недостающие
колонки, индексы, догоняет FTS-индексы и заполняет city у адресов.
Перед изменениями делается резервная копия (VACUUM INTO — см.
core/security.py::backup_database), поэтому бэкап делается ТОЛЬКО когда
миграция действительно нужна (_needs_migration).

Резервное копирование и права на файлы приходят ПАРАМЕТРАМИ из фасада
db/database.py: тесты подменяют их на уровне этого модуля
(tests/conftest.py::isolated_db), и подмена должна действовать.
"""

import logging
import os
import sqlite3
from typing import Callable, Optional

from core import audit
from core.address_utils import extract_city as _extract_city
from core.security import backup_database as _default_backup
from core.security import backup_size_kb
from core.security import restrict_to_current_user as _default_restrict

from db import connection, fts, schema
from db.schema import INDEXES, SOFT_DELETE_TABLES, _column_exists, _index_exists

logger = logging.getLogger("db.migrations")

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

def run(
    conn: sqlite3.Connection,
    backup: Optional[Callable] = None,
    db_path: Optional[str] = None,
) -> None:
    """
    Выполняет миграции схемы на открытом соединении.

    :param conn: открытое соединение с базой (закрывает его вызывающий);
    :param backup: функция резервного копирования (по умолчанию —
        core.security.backup_database). Фасад передаёт имя из СВОЕГО модуля,
        чтобы подмена `db.database.backup_database` в тестах действовала;
    :param db_path: путь к файлу базы (по умолчанию — текущий путь соединения).
    """
    if backup is None:
        backup = _default_backup
    if not db_path:
        db_path = connection.current_db_path()

    cursor = conn.cursor()

    # ── Резервная копия перед миграциями (Шаг 5 задания) ──
    # VACUUM INTO делает консистентную копию даже при включённом WAL.
    if _needs_migration(cursor):
        backup_path = backup(db_path, reason="before_migration")
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

    schema.create_indexes(cursor)

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

def secure_files(db_path: str, restrict: Optional[Callable] = None) -> None:
    """
    Оставляет доступ к файлам базы только текущему пользователю.

    contracts.db содержит персональные данные водителей, поэтому права
    ограничиваются (файл + WAL/SHM при наличии). Функция ограничения прав
    приходит параметром: фасад передаёт имя из своего модуля, чтобы подмена
    `db.database.restrict_to_current_user` в тестах действовала.
    """
    if restrict is None:
        restrict = _default_restrict

    restrict(db_path)
    for suffix in ("-wal", "-shm"):
        sidecar = db_path + suffix
        if os.path.exists(sidecar):
            restrict(sidecar)

__all__ = ["run", "secure_files"]
