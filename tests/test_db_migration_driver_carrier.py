#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Миграция «привязка водителей к перевозчикам» (ШАГ «Привязка водителей
к перевозчикам», часть A).

Что проверяется на СИНТЕТИЧЕСКОЙ базе прежней схемы (drivers без колонки
default_carrier_id):

  * `init_database()` добавляет колонку `drivers.default_carrier_id`;
  * создаётся таблица `driver_carriers` и её индексы;
  * существующие водители НЕ теряются: строки на месте, значения прежних
    колонок не изменились, а `default_carrier_id` у них NULL (поведение
    справочника остаётся прежним);
  * колонка внесена в `_needs_migration` — значит, перед миграцией
    автоматически делается резервная копия базы (`backup_database`),
    и копия действительно появляется в папке backup/;
  * повторный запуск миграции ничего не ломает (идемпотентность);
  * на ЧИСТОЙ базе колонка и таблица есть сразу, без всякой миграции.

Данные синтетические (ФИО «Иванов Иван Иванович», паспорт «60 26 123456»),
ПДн нет. База — во временной папке tests/_tmp.
"""

import os
import shutil
import sqlite3
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

#: Схема «до шага»: у drivers нет default_carrier_id, таблицы driver_carriers
#: нет вовсе.
OLD_SCHEMA = """
    CREATE TABLE drivers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        birth_date TEXT,
        passport_series TEXT,
        passport_number TEXT,
        phone TEXT,
        is_deleted INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE carriers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        short_name TEXT,
        inn TEXT,
        is_deleted INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
"""


def _make_old_database(path) -> None:
    """База прежней схемы: перевозчик и два водителя."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(OLD_SCHEMA)
        conn.execute(
            "INSERT INTO carriers (id, full_name, inn) "
            "VALUES (1, 'ООО «Альфа»', '7701234567')"
        )
        conn.execute(
            "INSERT INTO drivers "
            "(id, full_name, birth_date, passport_series, passport_number, phone) "
            "VALUES (1, 'Иванов Иван Иванович', '1980-01-01', '60 26', '123456', "
            "'+7 (999) 123-45-67')"
        )
        conn.execute(
            "INSERT INTO drivers "
            "(id, full_name, birth_date, passport_series, passport_number) "
            "VALUES (2, 'Петров Пётр Петрович', '1985-05-05', '60 27', '654321')"
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def old_db(work_dir, monkeypatch):
    """
    Временная база прежней схемы в отдельной папке.

    Папка нужна своя: резервная копия кладётся в `backup/` рядом с базой,
    и проверять её появление удобнее в изолированном месте (после теста
    папка удаляется целиком).

    Возвращает (модуль db.database, путь к базе).
    """
    import db.database as database

    folder = work_dir / f"migr_drv_carrier_{uuid.uuid4().hex[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "contracts.db"
    _make_old_database(path)

    monkeypatch.setattr(database, "DB_PATH", str(path))
    monkeypatch.setattr(database, "restrict_to_current_user", lambda *a, **k: True)
    # Справочник салонов в этой базе не нужен: файла нет.
    monkeypatch.setattr(
        database, "salons_xlsx_path", lambda: str(folder / "no-salons.xlsx")
    )

    yield database, path

    shutil.rmtree(folder, ignore_errors=True)


def _columns(path, table: str):
    conn = sqlite3.connect(str(path))
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def _tables(path):
    conn = sqlite3.connect(str(path))
    try:
        return [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        ]
    finally:
        conn.close()


def _indexes(path, like: str):
    conn = sqlite3.connect(str(path))
    try:
        return [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND name LIKE ? ORDER BY name",
                (like,),
            )
        ]
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# A.1–A.5: миграция рабочей базы
# ─────────────────────────────────────────────────────────────

def test_migration_adds_default_carrier_id(old_db):
    """До миграции колонки нет — после init_database() она есть."""
    database, path = old_db

    assert "default_carrier_id" not in _columns(path, "drivers"), (
        "тестовая база должна быть в старой схеме"
    )

    database.init_database()

    assert "default_carrier_id" in _columns(path, "drivers")


def test_migration_creates_driver_carriers_table(old_db):
    """Таблица истории работы создаётся вместе с колонкой."""
    database, path = old_db

    assert "driver_carriers" not in _tables(path)

    database.init_database()

    assert "driver_carriers" in _tables(path)
    assert _columns(path, "driver_carriers") == [
        "id", "driver_id", "carrier_id", "started_at", "ended_at", "created_at",
    ]


def test_migration_creates_driver_carrier_indexes(old_db):
    """Индексы привязки создаются: колонка, обе стороны связи и активные."""
    database, path = old_db

    database.init_database()

    created = _indexes(path, "idx_driver%")
    assert "idx_drivers_default_carrier" in created
    assert "idx_driver_carriers_driver" in created
    assert "idx_driver_carriers_carrier" in created
    assert "idx_driver_carriers_active" in created


def test_migration_does_not_touch_existing_drivers(old_db):
    """Данные водителей не теряются: строки на месте, привязки нет."""
    database, path = old_db

    database.init_database()

    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT id, full_name, birth_date, passport_series, passport_number, "
            "       phone, default_carrier_id "
            "FROM drivers ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 2, "водители потерялись при миграции"

    first = rows[0]
    assert first[1] == "Иванов Иван Иванович"
    assert first[2] == "1980-01-01"
    assert first[3] == "60 26"
    assert first[4] == "123456"
    assert first[5] == "+7 (999) 123-45-67"
    assert first[6] is None, "у заведённых раньше водителей привязки нет"

    assert rows[1][1] == "Петров Пётр Петрович"
    assert rows[1][6] is None

    # Справочник перевозчиков миграция тоже не трогает.
    assert [row[0] for row in sqlite3.connect(str(path)).execute(
        "SELECT full_name FROM carriers"
    )] == ["ООО «Альфа»"]


def test_migration_keeps_driver_fields_readable(old_db):
    """Запись водителя после миграции читается штатными функциями."""
    database, path = old_db

    database.init_database()
    driver = database.load_driver(1)

    assert driver["full_name"] == "Иванов Иван Иванович"
    assert driver["default_carrier_id"] is None

    listed = database.get_all_drivers(with_carrier_name=True)
    assert [item["full_name"] for item in listed] == [
        "Иванов Иван Иванович", "Петров Пётр Петрович",
    ]
    assert [item["carrier_name"] for item in listed] == ["", ""]


def test_migration_creates_backup(old_db):
    """
    Перед миграцией создаётся резервная копия базы.

    Колонка внесена в `_needs_migration`, поэтому `init_database()` зовёт
    `backup_database(reason="before_migration")` ДО ALTER TABLE, и копия
    появляется в папке backup/ рядом с базой.
    """
    database, path = old_db

    backup_folder = os.path.join(os.path.dirname(str(path)), "backup")
    assert not os.path.isdir(backup_folder), "копий до миграции быть не должно"

    database.init_database()

    copies = [
        name for name in os.listdir(backup_folder)
        if name.startswith("contracts_") and name.endswith(".db")
    ]
    assert copies, "резервная копия перед миграцией не создана"

    # Копия — настоящая база, и в ней ещё СТАРАЯ схема (снимок до ALTER).
    copy_path = os.path.join(backup_folder, copies[0])
    assert "default_carrier_id" not in _columns(copy_path, "drivers")
    assert len(sqlite3.connect(copy_path).execute(
        "SELECT id FROM drivers"
    ).fetchall()) == 2


def test_needs_migration_sees_the_new_column(old_db):
    """Колонка в списке обязательных — признак «нужна миграция и бэкап»."""
    database, path = old_db

    conn = sqlite3.connect(str(path))
    try:
        assert database._needs_migration(conn.cursor()) is True
    finally:
        conn.close()


def test_migration_is_idempotent(old_db):
    """Повторный запуск миграции ничего не ломает и не дублирует колонку."""
    database, path = old_db

    database.init_database()
    database.init_database()

    assert _columns(path, "drivers").count("default_carrier_id") == 1
    assert _columns(path, "driver_carriers").count("id") == 1

    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM drivers").fetchone()[0] == 2
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# Чистая база: то же самое, без миграции
# ─────────────────────────────────────────────────────────────

def test_fresh_database_has_column_and_table(isolated_db):
    """На чистой базе колонка и таблица есть сразу (CREATE TABLE)."""
    conn = isolated_db.get_connection()
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(drivers)")]
        tables = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        ]
    finally:
        conn.close()

    assert "default_carrier_id" in columns
    assert "driver_carriers" in tables


def test_fresh_database_driver_link_works(isolated_db):
    """Круг «привязал → прочитал» на чистой базе."""
    carrier_id = isolated_db.save_organization(
        {"full_name": "ООО «Альфа»"}, is_carrier=True
    )
    driver_id = isolated_db.save_driver({
        "full_name": "Иванов Иван Иванович",
        "passport_series": "60 26",
        "passport_number": "123456",
        "default_carrier_id": carrier_id,
    })

    isolated_db.link_driver_to_carrier(driver_id, carrier_id)

    assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_id
    assert [link["carrier_name"] for link in
            isolated_db.get_driver_carriers(driver_id)] == ["ООО «Альфа»"]
