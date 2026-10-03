#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты справочника контрагентов (db/database.py, шаг 3 инфраструктуры типов).

Проверяют: создание таблицы counterparties и её индексов, CRUD, уникальность
пары (тип договора, роль, ИНН), фильтрацию по типу и роли, поиск по
наименованию, мягкое удаление с восстановлением.

Отдельно закреплено решение по пустому ИНН: он пишется как NULL, поэтому
несколько контрагентов одной роли без ИНН сохраняются (в SQLite пустые
строки конфликтовали бы в UNIQUE между собой).
"""

import sqlite3

import pytest


def _counterparty(**overrides):
    """Полностью заполненный контрагент с возможностью переопределить поля."""
    data = {
        "contract_type": "arenda_ts",
        "role": "Арендатор",
        "full_name": "ООО «Ромашка»",
        "short_name": "ООО «Ромашка»",
        "inn": "7701234567",
        "kpp": "770101001",
        "ogrn": "1027700132195",
        "legal_address": "г. Москва, ул. Тестовая, д. 1",
        "actual_address": "г. Москва, ул. Тестовая, д. 1",
        "bank_account": "40702810000000000001",
        "bik": "044525225",
        "correspondent_account": "30101810400000000225",
        "bank_name": "ПАО Сбербанк",
        "director_name": "Петров Пётр Петрович",
        "director_position": "Генеральный директор",
        "phone": "+7 (495) 123-45-67",
        "email": "info@example.ru",
    }
    data.update(overrides)
    return data


# ─────────────────────────────────────────────────────────────
# Схема
# ─────────────────────────────────────────────────────────────

def test_counterparties_table_created(isolated_db):
    conn = isolated_db.get_connection()
    try:
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert "counterparties" in names


def test_counterparty_indexes_created(isolated_db):
    conn = isolated_db.get_connection()
    try:
        indexes = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
    finally:
        conn.close()

    for index_name in (
        "idx_counterparties_type",
        "idx_counterparties_role",
        "idx_counterparties_active",
    ):
        assert index_name in indexes, f"нет индекса {index_name}"


def test_counterparties_does_not_break_existing_tables(isolated_db):
    """Новая таблица не подменяет справочники перевозки (экспедиторство)."""
    conn = isolated_db.get_connection()
    try:
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    for table in ("drivers", "customers", "carriers", "contracts"):
        assert table in names, f"пропала таблица {table}"


# ─────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────

def test_save_and_load_counterparty(isolated_db):
    cp_id = isolated_db.save_counterparty(_counterparty())
    assert cp_id > 0

    loaded = isolated_db.load_counterparty(cp_id)
    assert loaded is not None
    assert loaded["contract_type"] == "arenda_ts"
    assert loaded["role"] == "Арендатор"
    assert loaded["full_name"] == "ООО «Ромашка»"
    assert loaded["inn"] == "7701234567"
    assert loaded["bik"] == "044525225"
    assert loaded["is_deleted"] == 0
    assert loaded["created_at"]


def test_load_unknown_counterparty_returns_none(isolated_db):
    assert isolated_db.load_counterparty(999999) is None


def test_update_counterparty(isolated_db):
    cp_id = isolated_db.save_counterparty(_counterparty())
    changed = _counterparty(
        full_name="ООО «Ромашка Плюс»",
        phone="+7 (495) 000-00-00",
        role="Арендодатель",
    )
    assert isolated_db.update_counterparty(cp_id, changed) is True

    loaded = isolated_db.load_counterparty(cp_id)
    assert loaded["full_name"] == "ООО «Ромашка Плюс»"
    assert loaded["phone"] == "+7 (495) 000-00-00"
    assert loaded["role"] == "Арендодатель"


def test_update_unknown_counterparty_returns_false(isolated_db):
    assert isolated_db.update_counterparty(999999, _counterparty()) is False


def test_required_fields_raise(isolated_db):
    for field in ("contract_type", "role", "full_name"):
        with pytest.raises(ValueError):
            isolated_db.save_counterparty(_counterparty(**{field: "  "}))


# ─────────────────────────────────────────────────────────────
# Уникальность и пустой ИНН
# ─────────────────────────────────────────────────────────────

def test_counterparty_unique_by_type_role_inn(isolated_db):
    isolated_db.save_counterparty(_counterparty())
    with pytest.raises(sqlite3.IntegrityError):
        isolated_db.save_counterparty(_counterparty(full_name="ООО «Дубль»"))


def test_same_inn_allowed_for_other_type_or_role(isolated_db):
    """Тот же ИНН в другом типе договора или другой роли — не дубль."""
    isolated_db.save_counterparty(_counterparty())
    isolated_db.save_counterparty(
        _counterparty(contract_type="formika", role="Арендатор")
    )
    isolated_db.save_counterparty(
        _counterparty(contract_type="arenda_ts", role="Арендодатель")
    )
    assert len(isolated_db.get_all_counterparties()) == 3


def test_empty_inn_saved_as_null_and_allows_multiples(isolated_db):
    first = isolated_db.save_counterparty(
        _counterparty(full_name="ИП Иванов И. И.", inn="")
    )
    second = isolated_db.save_counterparty(
        _counterparty(full_name="ИП Петров П. П.", inn="   ")
    )

    assert isolated_db.load_counterparty(first)["inn"] is None
    assert isolated_db.load_counterparty(second)["inn"] is None
    assert len(isolated_db.get_all_counterparties(
        contract_type="arenda_ts", role="Арендатор"
    )) == 2


# ─────────────────────────────────────────────────────────────
# Фильтрация и поиск
# ─────────────────────────────────────────────────────────────

def test_filter_by_contract_type_and_role(isolated_db):
    isolated_db.save_counterparty(_counterparty())
    isolated_db.save_counterparty(
        _counterparty(contract_type="formika", role="Перевозчик",
                      full_name="ООО «Формика-Партнёр»", inn="7709999999")
    )
    isolated_db.save_counterparty(
        _counterparty(contract_type="arenda_ts", role="Арендодатель",
                      full_name="ООО «Арендодатель»", inn="7705555555")
    )

    only_arenda = isolated_db.get_all_counterparties(contract_type="arenda_ts")
    assert {row["role"] for row in only_arenda} == {"Арендатор", "Арендодатель"}

    only_arendator = isolated_db.get_all_counterparties(
        contract_type="arenda_ts", role="Арендатор"
    )
    assert [row["full_name"] for row in only_arendator] == ["ООО «Ромашка»"]

    everything = isolated_db.get_all_counterparties()
    assert len(everything) == 3


def test_search_counterparties(isolated_db):
    isolated_db.save_counterparty(_counterparty())
    isolated_db.save_counterparty(
        _counterparty(role="Арендодатель", full_name="ООО «Арендодатель»",
                      inn="7705555555")
    )

    by_name = isolated_db.search_counterparties("arenda_ts", "Арендатор", "Ромашка")
    assert [row["full_name"] for row in by_name] == ["ООО «Ромашка»"]

    by_inn = isolated_db.search_counterparties("arenda_ts", "Арендатор", "7701234567")
    assert len(by_inn) == 1

    by_director = isolated_db.search_counterparties("arenda_ts", "", "Петров")
    assert len(by_director) == 2

    # Поиск ограничен типом договора: чужие типы не попадают
    assert isolated_db.search_counterparties("formika", "", "Ромашка") == []


# ─────────────────────────────────────────────────────────────
# Мягкое удаление
# ─────────────────────────────────────────────────────────────

def test_soft_delete_and_restore(isolated_db):
    cp_id = isolated_db.save_counterparty(_counterparty())

    assert isolated_db.delete_counterparty(cp_id) is True
    # Запись осталась в базе, но исчезла из списка и поиска
    assert isolated_db.load_counterparty(cp_id)["is_deleted"] == 1
    assert isolated_db.get_all_counterparties() == []
    assert isolated_db.search_counterparties("arenda_ts", "Арендатор", "Ромашка") == []
    assert len(isolated_db.get_all_counterparties(include_deleted=True)) == 1

    assert isolated_db.restore_counterparty(cp_id) is True
    assert isolated_db.load_counterparty(cp_id)["is_deleted"] == 0
    assert len(isolated_db.get_all_counterparties()) == 1


def test_delete_and_restore_unknown_counterparty(isolated_db):
    assert isolated_db.delete_counterparty(999999) is False
    assert isolated_db.restore_counterparty(999999) is False
