#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
`db.database.load_organization_by_id` — организация по ID (ШАГ «Дерево
перевозчиков + двусторонняя загрузка водитель ↔ перевозчик», часть A).

Зачем функция нужна: в карточке водителя есть `default_carrier_id`, и по
нему нужно подтянуть перевозчика в форму. Списки и поиск мягко удалённые
записи скрывают, а ссылка обязана их пережить — поэтому чтение по ID
отдаёт запись независимо от `is_deleted`.

Что проверяется:

  * перевозчик и заказчик читаются по ID (полный набор колонок, dict);
  * мягко удалённая запись читается (`is_deleted = 1`);
  * несуществующий, нулевой и отрицательный ID дают None;
  * чтение ничего не создаёт и не меняет в базе;
  * работает без WAL-спутников (`-wal` / `-shm`) рядом с файлом базы;
  * у заказчика нет колонок лицензии, которые есть у перевозчика;
  * одно и то же число в двух таблицах — разные записи (роли не путаются).

База — временная (`isolated_db`), данные синтетические, Пдн нет.
"""

from pathlib import Path

CARRIER_NAME = "ООО «Фас Транс»"
CARRIER_INN = "7701234567"
CARRIER_KPP = "770101001"
CUSTOMER_NAME = "ООО «Ромашка»"
CUSTOMER_INN = "7707654321"


def _save_carrier(isolated_db, **extra):
    payload = {"full_name": CARRIER_NAME, "inn": CARRIER_INN, "kpp": CARRIER_KPP}
    payload.update(extra)
    return isolated_db.save_organization(payload, is_carrier=True)


def _save_customer(isolated_db, **extra):
    payload = {"full_name": CUSTOMER_NAME, "inn": CUSTOMER_INN}
    payload.update(extra)
    return isolated_db.save_organization(payload, is_carrier=False)


def _drop_wal_sidecars(isolated_db) -> None:
    """
    Убирает файлы -wal / -shm, предварительно перенеся их содержимое в базу.

    Просто удалить спутники нельзя: незаверенные данные потерялись бы.
    Поэтому сначала checkpoint, и только потом удаление.
    """
    conn = isolated_db.get_connection()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()

    for suffix in ("-wal", "-shm"):
        Path(str(isolated_db.DB_PATH) + suffix).unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────
# A.1: чтение по ID
# ─────────────────────────────────────────────────────────────

def test_load_carrier_by_id_returns_dict(isolated_db):
    """Перевозчик читается по ID — словарь с его реквизитами."""
    carrier_id = _save_carrier(isolated_db)

    record = isolated_db.load_organization_by_id(carrier_id, is_carrier=True)

    assert isinstance(record, dict)
    assert record["id"] == carrier_id
    assert record["full_name"] == CARRIER_NAME


def test_load_carrier_by_id_returns_all_columns(isolated_db):
    """В записи есть все колонки таблицы carriers (SELECT *)."""
    carrier_id = _save_carrier(isolated_db)

    record = isolated_db.load_organization_by_id(carrier_id, is_carrier=True)

    conn = isolated_db.get_connection()
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(carriers)")
        }
    finally:
        conn.close()

    assert set(record) == columns


def test_load_carrier_by_id_not_found_returns_none(isolated_db):
    """Нет записи с таким ID — None (и никаких исключений)."""
    _save_carrier(isolated_db)

    assert isolated_db.load_organization_by_id(999999, is_carrier=True) is None


def test_load_customer_by_id(isolated_db):
    """Заказчик (is_carrier=False) читается из своей таблицы."""
    customer_id = _save_customer(isolated_db)
    _save_carrier(isolated_db)

    record = isolated_db.load_organization_by_id(customer_id, is_carrier=False)

    assert record["id"] == customer_id
    assert record["full_name"] == CUSTOMER_NAME


def test_load_soft_deleted_carrier(isolated_db):
    """Мягко удалённый перевозчик читается: ссылка водителя его переживает."""
    carrier_id = _save_carrier(isolated_db)
    isolated_db.delete_organization(carrier_id, is_carrier=True)

    record = isolated_db.load_organization_by_id(carrier_id, is_carrier=True)

    assert record is not None
    assert record["id"] == carrier_id
    assert record["is_deleted"] == 1


def test_load_soft_deleted_customer(isolated_db):
    """Мягко удалённый заказчик читается так же."""
    customer_id = _save_customer(isolated_db)
    isolated_db.delete_organization(customer_id, is_carrier=False)

    record = isolated_db.load_organization_by_id(customer_id, is_carrier=False)

    assert record is not None
    assert record["is_deleted"] == 1


def test_load_carrier_with_zero_id_returns_none(isolated_db):
    """ID = 0 — не «пустая запись», а отсутствие записи."""
    _save_carrier(isolated_db)

    assert isolated_db.load_organization_by_id(0, is_carrier=True) is None


def test_load_carrier_with_negative_id_returns_none(isolated_db):
    """Отрицательный ID тоже даёт None."""
    _save_carrier(isolated_db)

    assert isolated_db.load_organization_by_id(-1, is_carrier=True) is None


def test_load_carrier_does_not_create_record(isolated_db):
    """Чтение по несуществующему ID не заводит новую запись."""
    _save_carrier(isolated_db)
    before = isolated_db.get_all_organizations(is_carrier=True, include_deleted=True)

    isolated_db.load_organization_by_id(4242, is_carrier=True)

    after = isolated_db.get_all_organizations(is_carrier=True, include_deleted=True)
    assert len(after) == len(before) == 1


def test_load_carrier_works_without_wal_sidecar(isolated_db):
    """Чтение работает, когда рядом с базой нет файлов -wal и -shm."""
    carrier_id = _save_carrier(isolated_db)
    _drop_wal_sidecars(isolated_db)

    assert not Path(str(isolated_db.DB_PATH) + "-wal").exists()

    record = isolated_db.load_organization_by_id(carrier_id, is_carrier=True)
    assert record is not None
    assert record["full_name"] == CARRIER_NAME


def test_load_carrier_returns_inn_and_kpp(isolated_db):
    """Реквизиты, по которым перевозчика узнают, на месте."""
    carrier_id = _save_carrier(isolated_db)

    record = isolated_db.load_organization_by_id(carrier_id, is_carrier=True)

    assert record["inn"] == CARRIER_INN
    assert record["kpp"] == CARRIER_KPP


def test_load_customer_returns_different_columns(isolated_db):
    """У заказчика нет колонок лицензии — они есть только у перевозчика."""
    customer_id = _save_customer(isolated_db)
    carrier_id = _save_carrier(isolated_db, license_number="АК-123456")

    customer = isolated_db.load_organization_by_id(customer_id, is_carrier=False)
    carrier = isolated_db.load_organization_by_id(carrier_id, is_carrier=True)

    assert "license_number" not in customer
    assert "license_date" not in customer
    assert carrier["license_number"] == "АК-123456"


def test_load_by_id_matches_load_organization(isolated_db):
    """`load_organization_by_id` и `load_organization` — одно и то же чтение."""
    carrier_id = _save_carrier(isolated_db)

    assert isolated_db.load_organization_by_id(
        carrier_id, is_carrier=True
    ) == isolated_db.load_organization(carrier_id, is_carrier=True)


def test_same_id_in_two_tables_gives_different_records(isolated_db):
    """ID 1 есть и у заказчика, и у перевозчика — роли не путаются."""
    _save_carrier(isolated_db)
    _save_customer(isolated_db)

    carrier = isolated_db.load_organization_by_id(1, is_carrier=True)
    customer = isolated_db.load_organization_by_id(1, is_carrier=False)

    assert carrier["full_name"] == CARRIER_NAME
    assert customer["full_name"] == CUSTOMER_NAME


def test_load_carrier_reads_nothing_after_connection_closed(isolated_db):
    """Функция сама открывает и закрывает соединение — повторный вызов работает."""
    carrier_id = _save_carrier(isolated_db)

    first = isolated_db.load_organization_by_id(carrier_id, is_carrier=True)
    second = isolated_db.load_organization_by_id(carrier_id, is_carrier=True)

    assert first == second
