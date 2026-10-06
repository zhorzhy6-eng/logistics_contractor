#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты справочника адресов/салонов (ШАГ FIX-2.2, часть C).

Проверяется работа с таблицей address_book на ИЗОЛИРОВАННОЙ базе
(fixture isolated_db): миграция новых колонок справочника салонов,
save_address / update_address с полями салона, поиск по коду, наименованию
и ИНН, пакетный импорт и автозагрузка файла «Места выгрузок» при первом
запуске (init_database).

Отдельная группа тестов — разбор самого xlsx-файла справочника
(db.database.read_salons_rows): графы ищутся по заголовкам, а не по
номерам, лишние колонки не читаются.

Все данные синтетические, реальных ПДн нет.
"""

import sqlite3

import openpyxl
import pytest

from db.database import (
    SALONS_XLSX_NAME,
    count_addresses,
    get_addresses,
    import_addresses_from_list,
    read_salons_rows,
    salons_xlsx_path,
    save_address,
    update_address,
)

#: Заголовки граф тестового файла справочника салонов.
SALON_HEADERS = (
    "№", "КОД", "ИНН", "КПП", "Юр. Лицо", "КОД", "Город",
    "Юридический адрес", "Адрес доставки автомобилей",
    "Получатели уведомлений (e-mail)", "Контактный номер",
    "Контактный номер приемщика", "Комментарии (график и время приемки)",
    "Региональный Менеджер",
)

#: Строка тестового справочника: (код, ИНН, юр. лицо, город, адрес, менеджер).
SALON_ROW = (
    "JMR-A048", "7701234567", 'ООО "КАР АЦ"', "Москва",
    "г. Москва, ул. Складская, д. 8", "Менеджеров Менеджер",
)

#: Второй салон — для проверки поиска и количества.
SALON_ROW_2 = (
    "JMR-A031", "7203229173", 'ООО "ЦС-Моторс"', "Екатеринбург",
    "г. Екатеринбург, пр-т Космонавтов, д. 3", "закрыт",
)


# ─────────────────────────────────────────────────────────────
# Вспомогательное: изоляция от рабочего файла справочника
# ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_real_salons_file(monkeypatch):
    """
    Изолированная база не должна заливать рабочий data/spravochnik…xlsx.

    init_database() при первом запуске подхватывает файл справочника салонов
    (ШАГ FIX-2.2, п. C.9). В тестах это лишнее: у каждой проверки свои данные,
    поэтому путь подменяется на несуществующий. Тесты, которым файл нужен,
    подменяют путь сами (_patch_salons_file).
    """
    import db.database as database

    monkeypatch.setattr(
        database, "salons_xlsx_path",
        lambda: "tests/_tmp/no-such-salons-file.xlsx",
    )


# ─────────────────────────────────────────────────────────────
# Вспомогательное: тестовые xlsx-файлы
# ─────────────────────────────────────────────────────────────

def _salon_xlsx(path, rows=(SALON_ROW,), headers=SALON_HEADERS):
    """Собирает файл справочника салонов: шапка + строки."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(list(headers))
    for row in rows:
        number = rows.index(row) + 1 if isinstance(rows, list) else 1
        sheet.append([
            number, row[0], row[1], "667845001", row[2], row[0], row[3],
            "юридический адрес", row[4], "mail@example.ru", "8(900) 000-00-00",
            "", "с 9 до 20", row[5],
        ])
    workbook.save(str(path))
    return str(path)


def _addresses_xlsx(path, addresses):
    """Файл прежнего формата: одна колонка с адресами."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Адрес"])
    for address in addresses:
        sheet.append([address])
    workbook.save(str(path))
    return str(path)


def _patch_salons_file(monkeypatch, path):
    """Подменяет путь к файлу справочника салонов (data/spravochnik_...)."""
    import db.database as database

    monkeypatch.setattr(database, "salons_xlsx_path", lambda: str(path))


# ─────────────────────────────────────────────────────────────
# Миграция колонок
# ─────────────────────────────────────────────────────────────

def test_salon_columns_appear_after_init(isolated_db):
    """После init_database в address_book есть все колонки салона."""
    conn = isolated_db.get_connection()
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(address_book)")
        }
    finally:
        conn.close()

    for column in ("salon_name", "salon_code", "salon_inn", "salon_city"):
        assert column in columns, f"нет колонки {column}: {sorted(columns)}"


def test_migration_keeps_existing_data(work_file, monkeypatch):
    """
    Миграция на «старой» базе: записи и их значения остаются на месте.

    База собирается вручную в схеме ДО FIX-2.2 (без колонок салона), затем
    над ней выполняется init_database — так выглядит рабочий contracts.db
    при обновлении программы.
    """
    import db.database as database

    db_file = work_file("old_contracts.db")
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE address_book ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " point_type TEXT NOT NULL, address TEXT NOT NULL, city TEXT,"
        " date TEXT, time_window TEXT, usage_count INTEGER DEFAULT 0,"
        " created_at TEXT DEFAULT CURRENT_TIMESTAMP,"
        " UNIQUE(point_type, address))"
    )
    conn.execute(
        "INSERT INTO address_book (point_type, address, city, usage_count) "
        "VALUES ('unloading', 'г. Старый, ул. Прежняя, д. 1', 'Старый', 5)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    monkeypatch.setattr(database, "restrict_to_current_user", lambda *a, **k: True)
    monkeypatch.setattr(database, "backup_database", lambda *a, **k: None)
    monkeypatch.setattr(database, "salons_xlsx_path", lambda: "нет-такого-файла.xlsx")

    database.init_database()

    conn = database.get_connection()
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(address_book)")
        }
        row = conn.execute(
            "SELECT address, city, usage_count, salon_name, salon_code, salon_inn "
            "FROM address_book WHERE point_type = 'unloading'"
        ).fetchone()
    finally:
        conn.close()

    assert {"salon_name", "salon_code", "salon_inn", "salon_city"} <= columns
    assert row == ("г. Старый, ул. Прежняя, д. 1", "Старый", 5, None, None, None)


# ─────────────────────────────────────────────────────────────
# save_address / update_address с полями салона
# ─────────────────────────────────────────────────────────────

def test_save_address_with_salon_fields(isolated_db):
    """Поля салона записываются и читаются обратно."""
    addr_id = save_address(
        "unloading", "г. Москва, ул. Складская, д. 8",
        salon_name='ООО "КАР АЦ"', salon_code="JMR-A048",
        salon_inn="7701234567", salon_city="Москва",
    )
    assert addr_id

    row = get_addresses("unloading")[0]
    assert row["salon_name"] == 'ООО "КАР АЦ"'
    assert row["salon_code"] == "JMR-A048"
    assert row["salon_inn"] == "7701234567"
    assert row["salon_city"] == "Москва"


def test_save_address_old_call_signature_still_works(isolated_db):
    """Прежний вызов с четырьмя позиционными параметрами работает как раньше."""
    addr_id = save_address("loading", "г. Москва, ул. Южная, д. 2", "", "08:00-20:00")

    assert addr_id
    row = get_addresses("loading")[0]
    assert row["address"] == "г. Москва, ул. Южная, д. 2"
    assert row["time_window"] == "08:00-20:00"
    assert row["salon_name"] in (None, "")


def test_save_address_twice_keeps_salon_fields(isolated_db):
    """Повторное сохранение без полей салона не затирает их (usage_count растёт)."""
    save_address(
        "unloading", "г. Москва, ул. Складская, д. 8",
        salon_name='ООО "КАР АЦ"', salon_code="JMR-A048",
    )
    save_address("unloading", "г. Москва, ул. Складская, д. 8")

    rows = get_addresses("unloading")
    assert len(rows) == 1
    assert rows[0]["usage_count"] == 2
    assert rows[0]["salon_name"] == 'ООО "КАР АЦ"'
    assert rows[0]["salon_code"] == "JMR-A048"


def test_update_address_new_fields_and_empty_values(isolated_db):
    """update_address правит поля салона, а пустые значения не затирают их."""
    addr_id = save_address(
        "unloading", "г. Москва, ул. Складская, д. 8",
        salon_name="Старое имя", salon_code="JMR-OLD",
    )

    assert update_address(addr_id, "г. Москва, ул. Складская, д. 8",
                          salon_name="Новое имя")
    row = get_addresses("unloading")[0]
    assert row["salon_name"] == "Новое имя"
    assert row["salon_code"] == "JMR-OLD", "пустое поле затёрло код"

    assert update_address(addr_id, "г. Москва, ул. Складская, д. 8",
                          salon_code="JMR-NEW", salon_city="Химки")
    row = get_addresses("unloading")[0]
    assert row["salon_code"] == "JMR-NEW"
    assert row["salon_city"] == "Химки"
    assert row["salon_name"] == "Новое имя", "пустое поле затёрло наименование"


# ─────────────────────────────────────────────────────────────
# Поиск
# ─────────────────────────────────────────────────────────────

def _seed_salon(isolated_db, row=SALON_ROW, address=None):
    return save_address(
        "unloading",
        address or row[4],
        salon_name=row[2], salon_code=row[0], salon_inn=row[1], salon_city=row[3],
    )


def test_search_finds_by_salon_code(isolated_db):
    _seed_salon(isolated_db)

    assert count_addresses("unloading", "JMR-A048") == 1
    assert len(get_addresses("unloading", search="JMR-A048")) == 1


def test_search_finds_by_salon_name(isolated_db):
    _seed_salon(isolated_db)

    assert count_addresses("unloading", "КАР АЦ") == 1
    assert len(get_addresses("unloading", search="КАР")) == 1


def test_search_finds_by_salon_inn(isolated_db):
    _seed_salon(isolated_db)

    assert count_addresses("unloading", "7701234567") == 1
    assert len(get_addresses("unloading", search="7701234567")) == 1


def test_search_does_not_find_foreign_salon(isolated_db):
    _seed_salon(isolated_db)

    assert count_addresses("unloading", "JMR-ZZZ") == 0
    assert get_addresses("unloading", search="JMR-ZZZ") == []


# ─────────────────────────────────────────────────────────────
# Пакетный импорт
# ─────────────────────────────────────────────────────────────

def test_import_with_salon_fields(isolated_db):
    """Импорт пишет поля салона; повторный импорт только поднимает usage_count."""
    items = [
        {"address": SALON_ROW[4], "salon_name": SALON_ROW[2],
         "salon_code": SALON_ROW[0], "salon_inn": SALON_ROW[1],
         "salon_city": SALON_ROW[3]},
        {"address": SALON_ROW_2[4], "salon_name": SALON_ROW_2[2],
         "salon_code": SALON_ROW_2[0], "salon_inn": SALON_ROW_2[1],
         "salon_city": SALON_ROW_2[3]},
    ]

    assert import_addresses_from_list("unloading", items) == 2
    assert import_addresses_from_list("unloading", items) == 0

    rows = {row["salon_code"]: row for row in get_addresses("unloading")}
    assert set(rows) == {"JMR-A048", "JMR-A031"}
    assert rows["JMR-A048"]["salon_name"] == 'ООО "КАР АЦ"'
    assert rows["JMR-A048"]["usage_count"] == 2
    assert rows["JMR-A048"]["salon_inn"] == "7701234567"


def test_import_empty_salon_fields_do_not_erase(isolated_db):
    """Пустые поля салона при повторном импорте не стирают записанные."""
    import_addresses_from_list("unloading", [{
        "address": SALON_ROW[4], "salon_code": "JMR-A048",
    }])
    import_addresses_from_list("unloading", [{"address": SALON_ROW[4]}])

    row = get_addresses("unloading")[0]
    assert row["salon_code"] == "JMR-A048"


# ─────────────────────────────────────────────────────────────
# Разбор файла справочника салонов
# ─────────────────────────────────────────────────────────────

def test_read_salons_rows_by_headers(work_dir):
    """Графы находятся по заголовкам, лишние колонки не читаются."""
    path = _salon_xlsx(work_dir / "salons.xlsx")

    rows = read_salons_rows(str(path))

    assert len(rows) == 1
    assert rows[0] == {
        "address": SALON_ROW[4],
        "salon_name": SALON_ROW[2],
        "salon_code": SALON_ROW[0],
        "salon_inn": SALON_ROW[1],
        "salon_city": SALON_ROW[3],
        "date": "",
        "time_window": "",
    }
    # Контакты и регионального менеджера в записи нет — это ПДн.
    assert "менеджер" not in " ".join(rows[0]).lower()
    assert "@" not in " ".join(rows[0])


def test_read_salons_rows_skips_empty_addresses(work_dir):
    """Строка без адреса доставки записью не считается."""
    path = _salon_xlsx(work_dir / "salons_gap.xlsx", rows=[SALON_ROW])
    workbook = openpyxl.load_workbook(str(path))
    workbook.active.append([2, "JMR-B001", "1234567890", "1", "Без адреса",
                            "JMR-B001", "Тверь", "", "", "", "", "", "", ""])
    workbook.save(str(path))

    rows = read_salons_rows(str(path))

    assert [row["salon_code"] for row in rows] == ["JMR-A048"]


def test_read_salons_rows_reordered_columns(work_dir):
    """Порядок граф в файле может быть другим — ищутся по названиям."""
    headers = ("Город", "Юр. Лицо", "ИНН", "Адрес доставки автомобилей", "КОД")
    path = work_dir / "reordered.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(list(headers))
    sheet.append([SALON_ROW[3], SALON_ROW[2], SALON_ROW[1], SALON_ROW[4], SALON_ROW[0]])
    workbook.save(str(path))

    rows = read_salons_rows(str(path))

    assert rows == [{
        "address": SALON_ROW[4],
        "salon_name": SALON_ROW[2],
        "salon_code": SALON_ROW[0],
        "salon_inn": SALON_ROW[1],
        "salon_city": SALON_ROW[3],
        "date": "",
        "time_window": "",
    }]


def test_read_salons_rows_without_expected_header(work_dir):
    """Файл без ожидаемой шапки не читается — импорт его пропускает."""
    path = _addresses_xlsx(work_dir / "plain.xlsx", ["г. Москва, ул. Южная, д. 2"])

    assert read_salons_rows(str(path)) == []


def test_read_salons_rows_missing_file(work_dir):
    assert read_salons_rows(str(work_dir / "нет-такого.xlsx")) == []


# ─────────────────────────────────────────────────────────────
# Автозагрузка при первом запуске
# ─────────────────────────────────────────────────────────────

def test_salons_are_loaded_on_first_start(isolated_db, work_dir, monkeypatch):
    """Пустой справочник выгрузок заполняется файлом при первом запуске."""
    path = _salon_xlsx(work_dir / "salons.xlsx", rows=[SALON_ROW, SALON_ROW_2])
    _patch_salons_file(monkeypatch, path)

    from db.database import _load_salons_if_empty

    added = _load_salons_if_empty()

    assert added == 2
    assert count_addresses("unloading") == 2
    row = {r["salon_code"]: r for r in get_addresses("unloading")}["JMR-A031"]
    assert row["salon_name"] == 'ООО "ЦС-Моторс"'
    assert row["salon_city"] == "Екатеринбург"


def test_salons_are_not_reloaded_when_not_empty(isolated_db, work_dir, monkeypatch):
    """Если справочник уже заполнен, файл повторно не заливается."""
    path = _salon_xlsx(work_dir / "salons.xlsx", rows=[SALON_ROW, SALON_ROW_2])
    _patch_salons_file(monkeypatch, path)
    _seed_salon(isolated_db)

    from db.database import _load_salons_if_empty

    assert _load_salons_if_empty() == 0
    assert count_addresses("unloading") == 1


def test_missing_salons_file_is_not_an_error(isolated_db, work_dir, monkeypatch):
    """Файла справочника нет — автозагрузки нет, ошибки тоже."""
    _patch_salons_file(monkeypatch, work_dir / "нет-такого.xlsx")

    from db.database import _load_salons_if_empty

    assert _load_salons_if_empty() == 0
    assert count_addresses("unloading") == 0


def test_salons_file_exists_in_project():
    """Файл справочника салонов лежит в data/ и читается."""
    import os

    path = salons_xlsx_path()
    assert os.path.basename(path) == SALONS_XLSX_NAME
    assert os.path.exists(path), f"нет файла справочника салонов: {path}"

    rows = read_salons_rows(path)
    assert len(rows) == 192, f"в файле {len(rows)} записей, ожидалось 192"
    assert all(row["address"] for row in rows)
    assert all(row["salon_code"] for row in rows)
    assert all(row["salon_name"] for row in rows)


def test_salons_file_has_no_personal_data():
    """
    В файле проекта нет контактов: только деловые графы.

    Оригинал «Места выгрузок.xlsx» содержит e-mail получателей уведомлений,
    телефоны и ФИО региональных менеджеров — по AGENTS.md § 4 они не
    коммитятся. Файл проекта собирается tools/make_salons_reference.py из
    локального оригинала в data/private/.
    """
    import re

    rows = read_salons_rows(salons_xlsx_path())
    text = "\n".join(value for row in rows for value in row.values())

    assert "@" not in text, "в файле есть адреса электронной почты"
    # Телефон отличается от почтового индекса и номера дома: у него есть
    # разделитель (скобка, пробел, дефис) и не меньше 9 цифр подряд.
    # Разделителями считаются только те, что стоят между цифрами, — перенос
    # строки между двумя записями телефоном быть не может.
    phone_re = re.compile(
        r"(?:\+7|8)[ \t(-]?\d{3}[ \t)-]?\d{3}[ \t-]?\d{2}[ \t-]?\d{2}"
    )
    assert not phone_re.search(text), "в файле есть телефоны"
    # Каждая запись проверяется отдельно: склейка двух строк не должна
    # выглядеть телефоном.
    for row in rows:
        assert not phone_re.search(row["address"]), f"телефон в адресе: {row['address']}"
    # ФИО региональных менеджеров в файл не попало: графа «Региональный
    # Менеджер» в него вообще не переносится (см. tools/make_salons_reference.py),
    # а в деловых графах фамилий нет — только города и юр. лица.
    assert all("менеджер" not in row["salon_name"].lower() for row in rows)
