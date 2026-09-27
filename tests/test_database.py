#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты слоя БД (db/database.py).

Отдельное внимание — варианту В:
  * удаление водителя/организации мягкое (is_deleted = 1), поэтому запись
    исчезает из списков и поиска, но остаётся в базе;
  * связанные договоры НЕ удаляются и ссылка contracts.driver_id
    остаётся заполненной — история перевозок сохраняет, кто вёз;
  * запись можно вернуть (restore_driver / restore_organization).
"""

import os

import pytest

from core.settings_service import SettingsService


# ─────────────────────────────────────────────────────────────
# Инициализация и настройки SQLite
# ─────────────────────────────────────────────────────────────

def test_init_creates_tables(isolated_db):
    conn = isolated_db.get_connection()
    try:
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    for table in ("drivers", "customers", "carriers", "vehicles",
                  "driver_vehicles", "contracts", "contract_points", "address_book"):
        assert table in names, f"нет таблицы {table}"


def test_journal_mode_is_wal(isolated_db):
    conn = isolated_db.get_connection()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        conn.close()

    assert mode.lower() == "wal"
    assert busy == 5000
    assert foreign_keys == 1


def test_all_indexes_created(isolated_db):
    conn = isolated_db.get_connection()
    try:
        created = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
    finally:
        conn.close()

    for index_name, _ in isolated_db.INDEXES:
        assert index_name in created, f"нет индекса {index_name}"


# ─────────────────────────────────────────────────────────────
# Водители
# ─────────────────────────────────────────────────────────────

def test_driver_crud(isolated_db):
    driver_id = isolated_db.save_driver({
        "full_name": "Иванов Иван Иванович",
        "passport_series": "18 22",
        "passport_number": "926830",
        "phone": "+7 (999) 111-22-33",
    })
    assert isinstance(driver_id, int) and driver_id > 0

    loaded = isolated_db.load_driver(driver_id)
    assert loaded["full_name"] == "Иванов Иван Иванович"

    assert isolated_db.update_driver(driver_id, {"full_name": "Петров Пётр Петрович"})
    assert isolated_db.load_driver(driver_id)["full_name"] == "Петров Пётр Петрович"

    assert len(isolated_db.get_all_drivers()) == 1
    assert any(d["id"] == driver_id for d in isolated_db.search_drivers("Петров"))


def test_search_drivers_by_passport_and_phone(isolated_db):
    driver_id = isolated_db.save_driver({
        "full_name": "Иванов Иван Иванович",
        "passport_series": "18 22",
        "passport_number": "926830",
        "phone": "+7 (999) 111-22-33",
    })
    assert any(d["id"] == driver_id for d in isolated_db.search_drivers("18 22 926830"))
    assert any(d["id"] == driver_id for d in isolated_db.search_drivers("111-22-33"))
    assert isolated_db.search_drivers("нет такого") == []


def test_driver_vehicle(isolated_db):
    driver_id = isolated_db.save_driver({"full_name": "Иванов Иван Иванович"})
    assert isolated_db.save_driver_vehicle(driver_id, {
        "tractor_brand": "Foton Auman", "tractor_plate": "O844XY196",
    })
    vehicle = isolated_db.load_driver_vehicle(driver_id)
    assert vehicle["tractor_plate"] == "O844XY196"

    # повторное сохранение обновляет запись, а не создаёт вторую
    assert isolated_db.save_driver_vehicle(driver_id, {"tractor_plate": "A001AA"})
    assert isolated_db.load_driver_vehicle(driver_id)["tractor_plate"] == "A001AA"


def test_migration_adds_is_deleted_to_existing_db(work_file, monkeypatch):
    """
    Старая база (без is_deleted) мигрирует без потери данных.

    Проверяется именно миграция: таблица создана «по-старому», в ней уже
    есть запись, и после init_database() она должна остаться видимой.
    """
    import sqlite3

    import db.database as database

    db_file = work_file("old_contracts.db")
    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    monkeypatch.setattr(database, "restrict_to_current_user", lambda *a, **k: True)
    monkeypatch.setattr(database, "backup_database", lambda *a, **k: None)

    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute(
            "CREATE TABLE drivers ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  full_name TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO drivers (full_name) VALUES ('Иванов Иван Иванович')")
        conn.commit()
    finally:
        conn.close()

    database.init_database()

    conn = database.get_connection()
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(drivers)").fetchall()]
        flag = conn.execute("SELECT is_deleted FROM drivers WHERE id = 1").fetchone()[0]
    finally:
        conn.close()

    assert "is_deleted" in columns
    assert flag == 0, "существующая запись должна остаться видимой"
    assert [d["full_name"] for d in database.get_all_drivers()] == ["Иванов Иван Иванович"]


# ─────────────────────────────────────────────────────────────
# Организации
# ─────────────────────────────────────────────────────────────

def test_organization_crud(isolated_db):
    carrier_id = isolated_db.save_organization(
        {"full_name": "ООО «Перевозчик»", "inn": "7701234567"}, is_carrier=True
    )
    customer_id = isolated_db.save_organization(
        {"full_name": "ООО «Заказчик»", "inn": "7707654321"}, is_carrier=False
    )
    assert carrier_id and customer_id
    # это разные таблицы, поэтому записи независимы: перевозчик не виден среди заказчиков
    assert len(isolated_db.get_all_organizations(is_carrier=True)) == 1
    assert len(isolated_db.get_all_organizations(is_carrier=False)) == 1

    assert isolated_db.get_all_organizations(is_carrier=True)[0]["full_name"] == "ООО «Перевозчик»"
    assert isolated_db.get_all_organizations(is_carrier=False)[0]["full_name"] == "ООО «Заказчик»"

    assert isolated_db.update_organization(carrier_id, {"full_name": "ООО «Новый»"}, is_carrier=True)
    assert isolated_db.get_all_organizations(is_carrier=True)[0]["full_name"] == "ООО «Новый»"

    assert any(o["id"] == carrier_id for o in isolated_db.search_organizations("Новый", True))


def test_search_organizations_by_director(isolated_db):
    org_id = isolated_db.save_organization({
        "full_name": "ООО «Ромашка»", "inn": "7701234567",
        "director_name": "Ахмедов Тимур Артурович",
    }, is_carrier=True)
    assert any(o["id"] == org_id for o in isolated_db.search_organizations("Ахмедов", True))


# ─────────────────────────────────────────────────────────────
# ТС, договоры и точки
# ─────────────────────────────────────────────────────────────

def test_vehicles_and_contract(isolated_db):
    carrier_id = isolated_db.save_organization({"full_name": "ООО «Перевозчик»"}, is_carrier=True)
    vehicle_ids = isolated_db.save_vehicles(
        [{"vin": "EC3TEUMB0T0002608", "brand_model": "JETOUR T2", "plate_number": "А123ВС77"}],
        carrier_id=carrier_id,
    )
    assert len(vehicle_ids) == 1

    contract_id = isolated_db.save_contract({
        "number": "23092026-74", "date": "2026-09-23", "route": "Мурманск - Пятигорск",
        "price_without_vat": 180300.0, "vat_rate": "22%", "price_with_vat": 219966.0,
        "carrier_id": carrier_id,
    })
    assert isinstance(contract_id, int)

    isolated_db.save_contract_points(
        contract_id,
        [{"address": "Склад А", "date": "2026-09-24", "time_window": "09:00-18:00"}],
        [{"address": "Точка Б", "date": "2026-09-25", "time_window": ""}],
    )
    points = isolated_db.load_contract_points(contract_id)
    assert [p["address"] for p in points["loadings"]] == ["Склад А"]
    assert [p["address"] for p in points["unloadings"]] == ["Точка Б"]


# ─────────────────────────────────────────────────────────────
# Вариант В: мягкое удаление сохраняет историю договоров
# ─────────────────────────────────────────────────────────────

def test_soft_delete_driver_keeps_contract_and_link(isolated_db):
    driver_id = isolated_db.save_driver({"full_name": "Иванов Иван Иванович"})
    contract_id = isolated_db.save_contract({
        "number": "23092026-74", "date": "2026-09-23", "route": "A - B",
        "price_without_vat": 100.0, "driver_id": driver_id,
    })

    assert isolated_db.delete_driver(driver_id) is True

    conn = isolated_db.get_connection()
    try:
        row = conn.execute(
            "SELECT driver_id, contract_number FROM contracts WHERE id = ?", (contract_id,)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "договор удалён вместе с водителем"
    assert row[0] == driver_id, "ссылка на водителя должна сохраниться"
    assert row[1] == "23092026-74"


def test_soft_delete_driver_hides_from_lists_and_search(isolated_db):
    driver_id = isolated_db.save_driver({
        "full_name": "Иванов Иван Иванович",
        "passport_series": "18 22",
        "passport_number": "926830",
    })

    assert isolated_db.delete_driver(driver_id) is True

    # «Призраков» нет: ни в списке, ни в поиске
    assert isolated_db.get_all_drivers() == []
    assert isolated_db.search_drivers("Иванов") == []
    assert isolated_db.search_drivers("18 22 926830") == []

    # но запись физически на месте и помечена как удалённая
    stored = isolated_db.load_driver(driver_id)
    assert stored is not None
    assert stored["is_deleted"] == 1

    # режим «Показывать удалённых» её видит
    shown = isolated_db.get_all_drivers(include_deleted=True)
    assert [d["id"] for d in shown] == [driver_id]
    assert isolated_db.search_drivers("Иванов", include_deleted=True)


def test_soft_delete_driver_keeps_his_vehicle_and_can_be_restored(isolated_db):
    driver_id = isolated_db.save_driver({"full_name": "Иванов Иван Иванович"})
    isolated_db.save_driver_vehicle(driver_id, {"tractor_plate": "O844XY196"})

    assert isolated_db.delete_driver(driver_id) is True
    assert isolated_db.load_driver(driver_id)["is_deleted"] == 1
    # данные тягача/прицепа не потеряны — иначе восстановление было бы пустым
    assert isolated_db.load_driver_vehicle(driver_id)["tractor_plate"] == "O844XY196"

    assert isolated_db.restore_driver(driver_id) is True
    assert isolated_db.load_driver(driver_id)["is_deleted"] == 0
    assert [d["id"] for d in isolated_db.get_all_drivers()] == [driver_id]
    assert isolated_db.load_driver_vehicle(driver_id)["tractor_plate"] == "O844XY196"


def test_delete_missing_driver_returns_true(isolated_db):
    """Удаление несуществующей записи не считается ошибкой (поведение сохранено)."""
    assert isolated_db.delete_driver(99999) is True


def test_soft_delete_organization_keeps_contract_and_link(isolated_db):
    carrier_id = isolated_db.save_organization({"full_name": "ООО «Перевозчик»"}, is_carrier=True)
    contract_id = isolated_db.save_contract({
        "number": "1", "date": "2026-09-23", "route": "A - B",
        "price_without_vat": 100.0, "carrier_id": carrier_id,
    })

    assert isolated_db.delete_organization(carrier_id, is_carrier=True) is True

    conn = isolated_db.get_connection()
    try:
        row = conn.execute(
            "SELECT carrier_id FROM contracts WHERE id = ?", (contract_id,)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == carrier_id, "ссылка на перевозчика должна сохраниться"


def test_soft_delete_organization_hidden_and_restorable(isolated_db):
    org_id = isolated_db.save_organization(
        {"full_name": "ООО «Ромашка»", "inn": "7701234567"}, is_carrier=False
    )

    assert isolated_db.delete_organization(org_id, is_carrier=False) is True
    assert isolated_db.get_all_organizations(is_carrier=False) == []
    assert isolated_db.search_organizations("Ромашка", False) == []
    assert isolated_db.get_all_organizations(is_carrier=False, include_deleted=True)

    assert isolated_db.restore_organization(org_id, is_carrier=False) is True
    assert [o["id"] for o in isolated_db.get_all_organizations(is_carrier=False)] == [org_id]
    assert isolated_db.search_organizations("Ромашка", False)


def test_carrier_vehicles_survive_soft_delete(isolated_db):
    """ТС перевозчика не удаляются: иначе восстановление теряло бы данные."""
    carrier_id = isolated_db.save_organization({"full_name": "ООО «Перевозчик»"}, is_carrier=True)
    isolated_db.save_vehicles([{"vin": "EC3TEUMB0T0002608"}], carrier_id=carrier_id)

    assert isolated_db.delete_organization(carrier_id, is_carrier=True) is True

    conn = isolated_db.get_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM vehicles WHERE carrier_id = ?", (carrier_id,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 1


def test_contract_gets_reference_ids_from_contract_data(isolated_db):
    """Ссылки договора заполняются из ContractData (вариант В)."""
    from core.contract_data import ContractData

    driver_id = isolated_db.save_driver({"full_name": "Иванов Иван Иванович"})
    carrier_id = isolated_db.save_organization({"full_name": "ООО «Перевозчик»"}, is_carrier=True)
    customer_id = isolated_db.save_organization({"full_name": "ООО «Заказчик»"}, is_carrier=False)

    data = ContractData(contract={"number": "77", "date": "2026-09-23", "route": "A - B"})
    assert data.to_db_dict()["driver_id"] is None  # пока не привязали

    data.bind_reference_ids(
        driver_id=driver_id, customer_id=customer_id, carrier_id=carrier_id
    )
    payload = data.to_db_dict()
    assert payload["driver_id"] == driver_id
    assert payload["customer_id"] == customer_id
    assert payload["carrier_id"] == carrier_id

    contract_id = isolated_db.save_contract(payload)

    conn = isolated_db.get_connection()
    try:
        row = conn.execute(
            "SELECT driver_id, customer_id, carrier_id FROM contracts WHERE id = ?",
            (contract_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row == (driver_id, customer_id, carrier_id)

    # договор остаётся связанным и после уборки водителя из справочника
    isolated_db.delete_driver(driver_id)
    conn = isolated_db.get_connection()
    try:
        still_linked = conn.execute(
            "SELECT driver_id FROM contracts WHERE id = ?", (contract_id,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert still_linked == driver_id


# ─────────────────────────────────────────────────────────────
# Справочник адресов
# ─────────────────────────────────────────────────────────────

def test_address_book_crud_and_pagination(isolated_db):
    for i in range(7):
        isolated_db.save_address("loading", f"10000{i}, г.Мурманск, ул. Складская, д.{i}")

    assert isolated_db.count_addresses("loading") == 7
    page = isolated_db.get_addresses("loading", limit=3, offset=0)
    assert len(page) == 3
    page2 = isolated_db.get_addresses("loading", limit=3, offset=3)
    assert len(page2) == 3
    assert {row["id"] for row in page}.isdisjoint({row["id"] for row in page2})

    # все записи достижимы постранично (раньше мешал жёсткий LIMIT 500)
    seen = set()
    offset = 0
    while True:
        batch = isolated_db.get_addresses("loading", limit=2, offset=offset)
        if not batch:
            break
        seen.update(row["id"] for row in batch)
        offset += len(batch)
    assert len(seen) == 7


def test_address_search_and_city(isolated_db):
    isolated_db.save_address("loading", "183052, г.Мурманск, пр.Кольский, д.53")
    isolated_db.save_address("loading", "109316, г. Москва, Волгоградский пр-т")

    found = isolated_db.get_addresses("loading", "Мурманск")
    assert len(found) == 1
    assert found[0]["city"] == "мурманск"
    assert isolated_db.count_addresses("loading", "Мурманск") == 1


def test_import_addresses_counts_new_only(isolated_db):
    items = [{"address": f"20000{i}, г.Тверь, ул. Импортная, д.{i}"} for i in range(5)]
    assert isolated_db.import_addresses_from_list("loading", items) == 5
    # повторный импорт не создаёт дублей и не считается новым
    assert isolated_db.import_addresses_from_list("loading", items) == 0
    assert isolated_db.count_addresses("loading") == 5
    assert isolated_db.import_addresses_from_list("loading", []) == 0
    assert isolated_db.import_addresses_from_list("loading", [{"address": "   "}]) == 0


def test_update_and_delete_address(isolated_db):
    addr_id = isolated_db.save_address("loading", "183052, г.Мурманск, пр.Кольский, д.53")
    assert isolated_db.update_address(addr_id, "183053, г.Мурманск, пр.Кольский, д.54")
    updated = isolated_db.get_addresses("loading")[0]
    assert updated["address"].endswith("д.54")
    assert updated["city"] == "мурманск"

    assert isolated_db.delete_address(addr_id)
    assert isolated_db.count_addresses("loading") == 0
