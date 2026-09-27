#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты FTS5-поиска (регистронезависимый поиск по кириллице).

Что проверяется:
  * «мурманск» находит «Мурманск» (адреса), «складская» — «Складская»;
  * «иванов» находит «Иванов» (водители), «ромашка» — «ООО «Ромашка»»;
  * «королев» находит «Королёв» (перебор «е»/«ё», т.к. unicode61 её не сворачивает);
  * пунктуация в запросе («ул. Складская, д.7», «пр-т», «()») не ломает поиск;
  * индекс синхронизируется при одиночных записях, импорте и через rebuild;
  * середина слова («ольский» в «Кольский») по-прежнему находится LIKE-резервом;
  * при недоступном FTS5 поиск не ломается, а работает как раньше (LIKE);
  * импорт не замедляется: повторный импорт ничего не индексирует.
"""

import time

import pytest

import db.fts as fts_module


def _fts_ready(db_module) -> bool:
    conn = db_module.get_connection()
    try:
        return fts_module.fts5_available(conn)
    finally:
        conn.close()


@pytest.fixture
def db(isolated_db):
    """Изолированная БД + пропуск теста, если в сборке нет FTS5."""
    if not _fts_ready(isolated_db):
        pytest.skip("в этой сборке SQLite нет FTS5")
    return isolated_db


# ─────────────────────────────────────────────────────────────
# Схема
# ─────────────────────────────────────────────────────────────

def test_fts_schema_created(db):
    conn = db.get_connection()
    try:
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
    finally:
        conn.close()

    for fts_name in fts_module.FTS_TABLES:
        assert fts_name in names, f"нет FTS-таблицы {fts_name}"
    assert fts_module.META_TABLE in names


def test_fts_uses_external_content(db):
    """FTS-таблица не хранит копию: она объявлена с content=<источник>."""
    conn = db.get_connection()
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'fts_addresses'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert "content='address_book'" in sql
    assert "content_rowid='id'" in sql
    assert "unicode61" in sql


# ─────────────────────────────────────────────────────────────
# Регистронезависимый поиск по кириллице
# ─────────────────────────────────────────────────────────────

def test_address_search_is_case_insensitive(db):
    db.save_address("loading", "183052, г.Мурманск, пр.Кольский, д.53")
    db.save_address("loading", "г. Пятигорск, Бештаугорское шоссе 17")

    for query in ("мурманск", "Мурманск", "МУРМАНСК", "МуРмАнСк"):
        found = db.get_addresses("loading", query)
        assert len(found) == 1, f"запрос {query!r} не нашёл «Мурманск»"
        assert "Мурманск" in found[0]["address"]

    # префиксный поиск: «мурман» тоже должен находить
    assert len(db.get_addresses("loading", "мурман")) == 1
    # и наоборот: пятигорский адрес не попадает
    assert all("Пятигорск" not in row["address"] for row in db.get_addresses("loading", "мурманск"))


def test_address_search_by_word_inside_address_is_case_insensitive(db):
    db.save_address("loading", "г. Королёв, ул. Складская, д.7")

    for query in ("складская", "Складская", "СКЛАДСКАЯ"):
        found = db.get_addresses("loading", query)
        assert len(found) == 1, f"запрос {query!r} не нашёл «Складская»"


def test_address_search_by_city(db):
    """Поиск идёт и по city (раньше город в поиске не участвовал)."""
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO address_book (point_type, address, city, usage_count) "
            "VALUES ('loading', '183052, пр.Кольский, д.53', 'мурманск', 1)"
        )
        conn.commit()
    finally:
        conn.close()

    assert db.rebuild_fts_index()
    found = db.get_addresses("loading", "мурманск")
    assert len(found) == 1
    assert found[0]["city"] == "мурманск"


def test_yo_letter_is_found_both_ways(db):
    """unicode61 не сворачивает «ё», поэтому в запрос добавляются варианты."""
    db.save_address("loading", "г. Королёв, ул. Ленина, д.1")
    db.save_address("loading", "г. Королев, ул. Мира, д.2")

    assert len(db.get_addresses("loading", "королев")) == 2
    assert len(db.get_addresses("loading", "королёв")) == 2


def test_driver_search_is_case_insensitive(db):
    driver_id = db.save_driver({"full_name": "Иванов Иван Иванович"})
    db.save_driver({"full_name": "Петров Пётр Петрович"})

    for query in ("иванов", "Иванов", "ИВАНОВ", "иван"):
        found = db.search_drivers(query)
        assert [d["id"] for d in found] == [driver_id], f"запрос {query!r}"


def test_organization_search_is_case_insensitive(db):
    carrier_id = db.save_organization(
        {"full_name": "ООО «Ромашка»", "director_name": "Ахмедов Тимур Артурович"},
        is_carrier=True,
    )
    customer_id = db.save_organization(
        {"full_name": "ООО «ЗаказчикТранс»"}, is_carrier=False
    )

    assert [o["id"] for o in db.search_organizations("ромашка", True)] == [carrier_id]
    assert [o["id"] for o in db.search_organizations("РОМАШКА", True)] == [carrier_id]
    assert [o["id"] for o in db.search_organizations("заказчиктранс", False)] == [customer_id]
    # поиск по ФИО руководителя тоже не зависит от регистра
    assert [o["id"] for o in db.search_organizations("ахмедов", True)] == [carrier_id]


def test_search_special_characters_do_not_break(db):
    db.save_address("loading", "г. Королёв, ул. Складская, д.7")

    for query in ("ул. Складская, д.7", "пр-т", "()", "*", '"', "Складская; DROP TABLE"):
        db.get_addresses("loading", query)      # не должно бросать
        db.count_addresses("loading", query)
        db.search_drivers(query)
        db.search_organizations(query, True)


def test_search_by_digits_still_works(db):
    """Паспорт/телефон/ИНН ищутся по подстроке — это LIKE-путь."""
    driver_id = db.save_driver({
        "full_name": "Иванов Иван Иванович",
        "passport_series": "18 22",
        "passport_number": "926830",
        "phone": "+7 (999) 111-22-33",
    })
    assert [d["id"] for d in db.search_drivers("18 22 926830")] == [driver_id]
    assert [d["id"] for d in db.search_drivers("111-22-33")] == [driver_id]

    org_id = db.save_organization({"full_name": "ООО «Ромашка»", "inn": "7701234567"}, is_carrier=True)
    assert [o["id"] for o in db.search_organizations("7701234567", True)] == [org_id]
    # подстрока ИНН (не с начала) — тоже LIKE
    assert [o["id"] for o in db.search_organizations("0123", True)] == [org_id]


def test_middle_of_word_found_by_like_fallback(db):
    """FTS ищет токены, поэтому середину слова добирает LIKE-резерв."""
    db.save_address("loading", "183052, г.Мурманск, пр.Кольский, д.53")

    assert db.get_addresses("loading", "ольский"), "LIKE-резерв не сработал"
    assert db.count_addresses("loading", "ольский") == 1


def test_deleted_driver_is_not_found(db):
    driver_id = db.save_driver({"full_name": "Иванов Иван Иванович"})
    assert db.delete_driver(driver_id) is True

    assert db.search_drivers("иванов") == []
    assert [d["id"] for d in db.search_drivers("иванов", include_deleted=True)] == [driver_id]

    assert db.restore_driver(driver_id) is True
    assert [d["id"] for d in db.search_drivers("иванов")] == [driver_id]


# ─────────────────────────────────────────────────────────────
# Синхронизация индекса
# ─────────────────────────────────────────────────────────────

def test_index_sync_on_single_writes(db):
    addr_id = db.save_address("loading", "г.Мурманск, ул. Складская, д.1")
    assert db.get_addresses("loading", "складская")

    # изменение адреса: старое значение уходит из индекса, новое появляется
    assert db.update_address(addr_id, "г.Тверь, ул. Новая, д.1")
    assert db.get_addresses("loading", "складская") == []
    assert len(db.get_addresses("loading", "тверь")) == 1

    # удаление: строка пропадает из поиска
    assert db.delete_address(addr_id)
    assert db.get_addresses("loading", "тверь") == []


def test_index_sync_on_driver_update(db):
    driver_id = db.save_driver({"full_name": "Иванов Иван Иванович"})
    assert db.search_drivers("иванов")

    assert db.update_driver(driver_id, {"full_name": "Сидоров Сидор Сидорович"})
    assert db.search_drivers("иванов") == [], "старое ФИО осталось в индексе"
    assert [d["id"] for d in db.search_drivers("сидоров")] == [driver_id]


def test_index_sync_on_organization_update(db):
    org_id = db.save_organization({"full_name": "ООО «Ромашка»"}, is_carrier=True)
    assert db.search_organizations("ромашка", True)

    assert db.update_organization(org_id, {"full_name": "ООО «Василёк»"}, is_carrier=True)
    assert db.search_organizations("ромашка", True) == []
    assert [o["id"] for o in db.search_organizations("василек", True)] == [org_id]


def test_index_sync_after_import(db):
    items = [
        {"address": f"{100000 + i}, г.Мурманск, ул. Складская, д.{i}"}
        for i in range(50)
    ]
    assert db.import_addresses_from_list("loading", items) == 50

    # импорт индекс не трогает: расхождение видно по last_id…
    conn = db.get_connection()
    try:
        assert fts_module.needs_sync(conn, "fts_addresses") is True
    finally:
        conn.close()

    # …а первый поиск догоняет индекс и находит новые адреса
    assert db.count_addresses("loading", "мурманск") == 50

    conn = db.get_connection()
    try:
        assert fts_module.needs_sync(conn, "fts_addresses") is False
    finally:
        conn.close()

    # повторный импорт тех же адресов ничего не меняет
    assert db.import_addresses_from_list("loading", items) == 0
    assert db.count_addresses("loading", "мурманск") == 50


def test_rebuild_restores_index(db):
    db.save_address("loading", "183052, г.Мурманск, пр.Кольский, д.53")
    assert db.get_addresses("loading", "кольский")

    # имитируем рассинхрон: полностью чистим индекс
    conn = db.get_connection()
    try:
        conn.execute("INSERT INTO fts_addresses(fts_addresses) VALUES('delete-all')")
        conn.commit()
    finally:
        conn.close()

    # «Кольский» есть только в адресе (в городе его нет), а LIKE регистрозависим,
    # поэтому пустой индекс означает и пустой результат поиска
    assert db.get_addresses("loading", "кольский") == []

    assert db.rebuild_fts_index() is True
    assert len(db.get_addresses("loading", "кольский")) == 1


# ─────────────────────────────────────────────────────────────
# Резервный путь (FTS5 недоступен)
# ─────────────────────────────────────────────────────────────

def test_like_fallback_when_fts5_disabled(isolated_db, monkeypatch):
    """Если FTS5 нет, поиск работает как раньше — через LIKE."""
    monkeypatch.setattr(fts_module, "fts5_available", lambda conn: False)

    isolated_db.save_address("loading", "183052, г.Мурманск, пр.Кольский, д.53")

    # точный регистр — находится (старое поведение сохранено)
    assert len(isolated_db.get_addresses("loading", "Кольский")) == 1
    # другой регистр в адресе — не находится: LIKE по кириллице регистрозависим,
    # именно это и исправляет FTS5
    assert isolated_db.get_addresses("loading", "кольский") == []
    # поиск по городу в нижнем регистре тоже работает (в city он хранится так)
    assert isolated_db.count_addresses("loading", "мурманск") == 1


def test_fts_error_falls_back_to_like(db, monkeypatch):
    """Ошибка MATCH не должна ломать поиск — уходим в LIKE."""
    db.save_address("loading", "183052, г.Мурманск, пр.Кольский, д.53")

    real_match = fts_module.match_query_for
    monkeypatch.setattr(
        fts_module, "match_query_for", lambda text: 'сломанный "запрос'
    )
    assert len(db.get_addresses("loading", "Мурманск")) == 1   # LIKE нашёл

    monkeypatch.setattr(fts_module, "match_query_for", real_match)


# ─────────────────────────────────────────────────────────────
# Пагинация и согласованность
# ─────────────────────────────────────────────────────────────

def test_count_matches_results_with_fts(db):
    for i in range(5):
        db.save_address("loading", f"{100000 + i}, г.Мурманск, ул. Складская, д.{i}")
    db.save_address("loading", "г. Тверь, ул. Новая, д.1")

    assert db.count_addresses("loading", "мурманск") == 5
    assert len(db.get_addresses("loading", "мурманск", limit=2, offset=0)) == 2
    assert len(db.get_addresses("loading", "мурманск", limit=2, offset=4)) == 1
    assert len(db.get_addresses("loading", "мурманск", limit=100)) == 5


# ─────────────────────────────────────────────────────────────
# Построение MATCH-запроса
# ─────────────────────────────────────────────────────────────

def test_match_query_builder():
    assert fts_module.build_match_query("мурманск") == '"мурманск"*'
    assert fts_module.build_match_query("ул. Складская, д.7") == (
        '"ул"* AND "складская"* AND "д"* AND "7"*'
    )
    # «ё» даёт варианты, слова без «е/ё» — один вариант
    query = fts_module.build_match_query("королев")
    assert '"королев"*' in query and '"королёв"*' in query


def test_match_query_rejects_digits_and_punctuation():
    assert fts_module.match_query_for("18 22 926830") is None
    assert fts_module.match_query_for("()") is None
    assert fts_module.match_query_for("") is None
    assert fts_module.match_query_for("Мурманск") is not None


# ─────────────────────────────────────────────────────────────
# Производительность
# ─────────────────────────────────────────────────────────────

def test_import_of_20k_addresses_is_not_slow(db):
    """
    Импорт 20 000 адресов не замедляется из-за FTS.

    Индекс намеренно не обновляется внутри импорта: он догоняется при первом
    поиске. Поэтому здесь проверяется и скорость импорта, и то, что поиск
    после него всё равно находит данные.
    """
    items = [
        {"address": f"{100000 + i}, г.Мурманск, ул. Складская, д.{i}"}
        for i in range(20000)
    ]

    started = time.perf_counter()
    added = db.import_addresses_from_list("loading", items)
    first_import = time.perf_counter() - started

    assert added == 20000
    # запас намеренно большой: тест ловит регресс «в разы», а не микросекунды
    assert first_import < 10.0, f"первый импорт 20k занял {first_import:.2f} с"

    # повторный импорт тех же адресов — тоже без работы с индексом
    started = time.perf_counter()
    assert db.import_addresses_from_list("loading", items) == 0
    second_import = time.perf_counter() - started
    assert second_import < 5.0, f"повторный импорт 20k занял {second_import:.2f} с"

    # первый поиск догоняет индекс (+20k строк) и находит всё
    started = time.perf_counter()
    total = db.count_addresses("loading", "складская")
    elapsed = time.perf_counter() - started
    assert total == 20000
    assert elapsed < 3.0, f"первый FTS-поиск по 20k занял {elapsed:.2f} с"


def test_search_finds_case_variants_faster_than_like(db):
    """FTS находит то, что LIKE не находил, и укладывается в разумное время."""
    items = [
        {"address": f"{100000 + i}, г.Мурманск, ул. Складская, д.{i}"}
        for i in range(2000)
    ]
    db.import_addresses_from_list("loading", items)

    started = time.perf_counter()
    found = db.get_addresses("loading", "мурманск", limit=500)
    elapsed = time.perf_counter() - started

    assert len(found) == 500
    assert elapsed < 2.0, f"страница FTS-поиска заняла {elapsed:.2f} с"

    # LIKE в нижнем регистре не нашёл бы ничего — это и была исходная проблема
    assert db.count_addresses("loading", "МУРМАНСК") == 2000
