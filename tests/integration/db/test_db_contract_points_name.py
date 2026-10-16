#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты миграции `contract_points.name` (ШАГ FIX-6, часть F).

Повод: в таблице точек маршрута не было колонки с наименованием салона,
хотя в бланк оно попадает из формы (`core/contract_data.py::_as_point_list`
хранит `name` с ШАГА FIX-2.5). Значит, при перезагрузке сохранённого
договора из базы имя терялось.

Что проверяется:

  * миграция ДОБАВЛЯЕТ колонку `name` к уже существующей таблице и НЕ
    теряет строки: старый договор со своими точками остаётся на месте,
    а имя у него приходит пустой строкой;
  * `save_contract_points` пишет имя, `load_contract_points` его читает —
    круг «сохранил → прочитал» замкнут;
  * договор без имён (старые вызовы без ключа `name`) сохраняется как
    раньше: пустая строка вместо имени;
  * `_needs_migration` видит новую колонку — значит, перед миграцией
    делается резервная копия базы.

База — временная (`isolated_db` / собственная копия в tests/_tmp),
данные синтетические, ПДн нет.
"""

import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


# ─────────────────────────────────────────────────────────────
# Помощники: старая схема и «договор до миграции»
# ─────────────────────────────────────────────────────────────

OLD_SCHEMA = """
    CREATE TABLE contracts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_number TEXT,
        contract_date TEXT
    );
    CREATE TABLE contract_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id INTEGER,
        point_type TEXT,
        sort_order INTEGER,
        address TEXT,
        date TEXT,
        time_window TEXT,
        FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE CASCADE
    );
"""


def _make_old_database(path) -> None:
    """База прежней схемы: договор с двумя точками, колонки name ещё нет."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(OLD_SCHEMA)
        conn.execute(
            "INSERT INTO contracts (id, contract_number, contract_date) "
            "VALUES (1, 'OLD-1', '2026-09-23')"
        )
        conn.execute(
            "INSERT INTO contract_points "
            "(contract_id, point_type, sort_order, address, date, time_window) "
            "VALUES (1, 'loading', 0, 'Склад А', '2026-09-24', '09:00-18:00')"
        )
        conn.execute(
            "INSERT INTO contract_points "
            "(contract_id, point_type, sort_order, address, date, time_window) "
            "VALUES (1, 'unloading', 0, 'Склад Б', '2026-09-25', '')"
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def old_db(work_file, monkeypatch):
    """
    Временная база прежней схемы + подмена пути в модуле db.database.

    Возвращает (модуль, путь): миграция запускается через init_database().
    """
    import db.database as database

    path = work_file("contracts.db")
    _make_old_database(path)

    monkeypatch.setattr(database, "DB_PATH", str(path))
    monkeypatch.setattr(database, "restrict_to_current_user", lambda *a, **k: True)
    monkeypatch.setattr(database, "backup_database", lambda *a, **k: None)
    # Автозагрузка справочника салонов в этом тесте не нужна: файла нет.
    monkeypatch.setattr(
        database, "salons_xlsx_path", lambda: "tests/_tmp/no-such-salons.xlsx"
    )
    return database, path


def _columns(path, table: str):
    conn = sqlite3.connect(str(path))
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# F1: миграция добавляет колонку
# ─────────────────────────────────────────────────────────────

def test_migration_adds_name_column(old_db):
    """После init_database у contract_points есть колонка name."""
    database, path = old_db

    assert "name" not in _columns(path, "contract_points"), (
        "тестовая база должна быть в старой схеме"
    )

    database.init_database()

    assert "name" in _columns(path, "contract_points")


def test_migration_keeps_existing_rows(old_db):
    """Миграция НЕ теряет данные: строки и их значения на месте."""
    database, path = old_db

    database.init_database()

    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT contract_id, point_type, address, date, time_window, name "
            "FROM contract_points ORDER BY point_type"
        ).fetchall()
        contracts = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    finally:
        conn.close()

    assert contracts == 1
    assert len(rows) == 2, "строки точек потерялись при миграции"

    by_type = {row[1]: row for row in rows}
    assert by_type["loading"][2] == "Склад А"
    assert by_type["loading"][3] == "2026-09-24"
    assert by_type["unloading"][2] == "Склад Б"


def test_existing_rows_get_empty_name(old_db):
    """У договоров, сохранённых до миграции, имя — пустое (не теряется)."""
    database, path = old_db

    database.init_database()
    points = database.load_contract_points(1)

    assert points["loadings"][0]["name"] == ""
    assert points["unloadings"][0]["name"] == ""
    # Остальные поля точки не пострадали.
    assert points["loadings"][0]["address"] == "Склад А"
    assert points["loadings"][0]["time_window"] == "09:00-18:00"


def test_migration_is_idempotent(old_db):
    """Повторный запуск миграции ничего не ломает."""
    database, path = old_db

    database.init_database()
    database.init_database()

    assert _columns(path, "contract_points").count("name") == 1
    points = database.load_contract_points(1)
    assert len(points["loadings"]) == 1 and len(points["unloadings"]) == 1


def test_needs_migration_sees_the_new_column(old_db):
    """
    Колонка в списке обязательных: перед миграцией делается бэкап базы.

    `_needs_migration` — тот самый признак, по которому init_database
    вызывает backup_database до ALTER TABLE.
    """
    database, path = old_db

    conn = sqlite3.connect(str(path))
    try:
        cursor = conn.cursor()
        assert database._needs_migration(cursor) is True
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# F2–F4: save / load имени
# ─────────────────────────────────────────────────────────────

def test_save_and_load_point_name(isolated_db):
    """Круг «сохранил → прочитал»: наименование салона доходит до базы."""
    contract_id = isolated_db.save_contract({"number": "NAME-1"})

    isolated_db.save_contract_points(
        contract_id,
        [{"name": "ООО «Салон Погрузки»", "address": "Склад А",
          "date": "2026-09-24", "time_window": "09:00-18:00"}],
        [{"name": "ООО «Салон Выгрузки»", "address": "Склад Б",
          "date": "2026-09-25", "time_window": ""}],
    )

    points = isolated_db.load_contract_points(contract_id)

    assert points["loadings"][0]["name"] == "ООО «Салон Погрузки»"
    assert points["unloadings"][0]["name"] == "ООО «Салон Выгрузки»"
    assert points["loadings"][0]["address"] == "Склад А"


def test_save_without_name_writes_empty_string(isolated_db):
    """Старый вызов (без ключа name) сохраняется как раньше — с пустым именем."""
    contract_id = isolated_db.save_contract({"number": "NAME-2"})

    isolated_db.save_contract_points(
        contract_id,
        [{"address": "Склад А", "date": "2026-09-24", "time_window": ""}],
        [{"address": "Склад Б", "date": "2026-09-25", "time_window": ""}],
    )

    points = isolated_db.load_contract_points(contract_id)

    assert points["loadings"][0]["name"] == ""
    assert points["unloadings"][0]["name"] == ""


def test_none_name_is_saved_as_empty_string(isolated_db):
    """Имя «None» из чужого источника не попадает в базу как текст «None»."""
    contract_id = isolated_db.save_contract({"number": "NAME-3"})

    isolated_db.save_contract_points(
        contract_id,
        [{"name": None, "address": "Склад А"}],
        [{"name": "", "address": "Склад Б"}],
    )

    points = isolated_db.load_contract_points(contract_id)

    assert points["loadings"][0]["name"] == ""
    assert "None" not in str(points)


def test_loading_legacy_contract_without_name(isolated_db):
    """
    Договор, сохранённый ДО миграции, читается без имени.

    Строка кладётся в обход save_contract_points — так выглядит база,
    которую миграция уже прошла: колонка есть, значение NULL.
    """
    contract_id = isolated_db.save_contract({"number": "LEGACY-1"})

    conn = isolated_db.get_connection()
    try:
        conn.execute(
            "INSERT INTO contract_points "
            "(contract_id, point_type, sort_order, name, address, date, time_window) "
            "VALUES (?, 'loading', 0, NULL, 'Старый склад', '2026-09-24', '')",
            (contract_id,),
        )
        conn.commit()
    finally:
        conn.close()

    points = isolated_db.load_contract_points(contract_id)

    assert points["loadings"][0]["name"] == ""
    assert points["loadings"][0]["address"] == "Старый склад"


def test_save_with_details_keeps_point_names(isolated_db):
    """Тот же путь, которым пользуется окно: save_contract_with_details."""
    contract_id = isolated_db.save_contract_with_details(
        {"number": "NAME-4"},
        [{"name": "Салон А", "address": "Склад А", "date": "2026-09-24",
          "time_window": "09:00-18:00"}],
        [{"name": "Салон Б", "address": "Склад Б", "date": "2026-09-25",
          "time_window": ""}],
        [{"vin": "EC3TEUMB0T0000001", "brand_model": "МОДЕЛЬ"}],
    )

    points = isolated_db.load_contract_points(contract_id)

    assert [point["name"] for point in points["loadings"]] == ["Салон А"]
    assert [point["name"] for point in points["unloadings"]] == ["Салон Б"]


def test_point_name_round_trip_from_tab_dict(isolated_db):
    """
    Точка в том виде, в каком её отдаёт вкладка, доходит до базы.

    Проверяется стык «данные вкладки → база»: ключ `name` (его кладёт
    `ui/tabs/contract_tab.py::get_loadings`) пишется и читается обратно.
    Сама вкладка здесь не поднимается: её тесты живут в
    tests/test_ui_contract_tab.py (там есть QApplication и справочник).
    """
    loadings = [{"name": "ООО «Салон»", "address": "Склад А",
                 "date": "2026-09-24", "time_window": "09:00-18:00"}]
    unloadings = [{"name": "", "address": "Склад Б",
                   "date": "2026-09-25", "time_window": ""}]

    contract_id = isolated_db.save_contract({"number": "NAME-5"})
    isolated_db.save_contract_points(contract_id, loadings, unloadings)

    saved = isolated_db.load_contract_points(contract_id)

    assert saved["loadings"] == loadings
    assert saved["unloadings"] == unloadings
