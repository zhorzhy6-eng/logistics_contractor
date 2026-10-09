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
путь у нас (см. db/connection.py::set_db_path_provider). По той же причине
`init_database()` берёт резервное копирование, ограничение прав и путь к
файлу салонов ИЗ ЭТОГО модуля, а не из внутренних: их подменяют в тестах
(tests/conftest.py::isolated_db).

Модуль `fts` импортируется сюда не для работы, а ради совместимости:
`db.database.fts` был виден и до разбиения (через него берут модуль поиска).
"""

import logging
import os

from core import audit
from core.security import backup_database, restrict_to_current_user

from db import connection, fts, migrations, salons, schema  # noqa: F401 (fts — см. __all__)
from db.connection import DB_PATH as _DEFAULT_DB_PATH
from db.connection import get_connection
from db.crud.addresses import (
    count_addresses,
    delete_address,
    get_addresses,
    import_addresses_from_list,
    save_address,
    update_address,
)
from db.crud.contracts import (
    load_contract_points,
    save_contract,
    save_contract_points,
    save_contract_with_details,
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


#: Публичные имена точки входа. Два имени с подчёркиванием тоже оставлены
#: ради совместимости: `_needs_migration` и `_load_salons_if_empty` зовут
#: тесты (tests/test_prepayment.py, tests/test_db_address_book.py и другие).
__all__ = [
    # соединение и инициализация
    "DB_PATH", "get_connection", "init_database",
    # справочники (db/crud/)
    "count_addresses", "delete_address", "get_addresses",
    "import_addresses_from_list", "save_address", "update_address",
    "load_contract_points", "save_contract", "save_contract_points",
    "save_contract_with_details",
    "COUNTERPARTY_FIELDS", "delete_counterparty", "get_all_counterparties",
    "load_counterparty", "restore_counterparty", "save_counterparty",
    "search_counterparties", "update_counterparty",
    "ACTIVE_LINK_SQL", "delete_driver", "get_all_drivers",
    "get_carrier_drivers", "get_driver_carriers", "link_driver_to_carrier",
    "load_driver", "restore_driver", "save_driver", "search_drivers",
    "set_default_carrier", "unlink_driver_from_carrier", "update_driver",
    "delete_organization", "find_organization_id", "get_all_organizations",
    "load_organization", "load_organization_by_id", "restore_organization",
    "save_organization", "search_organizations", "update_organization",
    "load_driver_vehicle", "save_driver_vehicle", "save_vehicles",
    # схема, миграции, поиск, справочник салонов
    "INDEXES", "SOFT_DELETE_TABLES", "rebuild_fts_index",
    "SALONS_XLSX_NAME", "SALON_COLUMN_KEYS", "read_salons_rows",
    "salons_xlsx_path",
    # имена для тестов (см. комментарий выше)
    "_load_salons_if_empty", "_needs_migration",
]
