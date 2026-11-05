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

from core.address_utils import split_salon_name

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

#: Первые строки файла result.xlsx (деловой справочник салонов, без ПДн).
#: (наименование в колонке, адрес в файле, чистый адрес после импорта).
RESULT_SAMPLE_ROWS = [
    (
        "АВТОМАГ (Нижневартовск) ООО",
        "Нижневартовск, Мира, владение 3/п, строение 11, "
        "АВТОМАГ (Нижневартовск) ООО",
        "Нижневартовск, Мира, владение 3/п, строение 11",
    ),
    (
        "ВМ-С-АЗИЯ ООО",
        "Сургут, Югорский тракт, дом № 23, сооружение 1, ВМ-С-АЗИЯ ООО",
        "Сургут, Югорский тракт, дом № 23, сооружение 1",
    ),
    (
        'ООО "СК-МОТОРС ПРЕМИУМ"',
        'Сургут, Профсоюзов, дом № 1/3, ООО "СК-МОТОРС ПРЕМИУМ"',
        "Сургут, Профсоюзов, дом № 1/3",
    ),
    (
        'АО "Моторавто"',
        "Кировская область, г.о. город Киров, г. Киров, "
        "ул. Московская, д. 106а",
        "Кировская область, г.о. город Киров, г. Киров, "
        "ул. Московская, д. 106а",
    ),
    (
        'ООО "Спектр-Авто-Н"',
        "Ярославская область, г.о. город Ярославль, г. Ярославль, "
        "пр-кт Фрунзе, д. 34",
        "Ярославская область, г.о. город Ярославль, г. Ярославль, "
        "пр-кт Фрунзе, д. 34",
    ),
]


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


def _two_column_salon_xlsx(path, rows):
    """Файл оператора: две графы «Наименование салона» и «Адрес»."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Наименование салона", "Адрес"])
    for name, address in rows:
        sheet.append([name, address])
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


def test_legacy_addresses_get_salon_fields_from_file(
    isolated_db, work_dir, monkeypatch
):
    """
    База, набранная до FIX-2.2, получает поля салона из файла.

    Записи без кода салона — признак «файл ещё не читали»: адрес из файла
    обновляет такую запись (usage_count +1 и наименование/город), а его
    собственные значения не теряются.
    """
    path = _salon_xlsx(work_dir / "salons.xlsx", rows=[SALON_ROW])
    _patch_salons_file(monkeypatch, path)
    save_address("unloading", SALON_ROW[4])          # старая запись без салона
    save_address("unloading", "г. Тверь, ул. Своя, д. 1")  # записи нет в файле

    from db.database import _load_salons_if_empty

    assert _load_salons_if_empty() == 0, "новых адресов файл не добавил"

    rows = {row["address"]: row for row in get_addresses("unloading")}
    assert rows[SALON_ROW[4]]["salon_name"] == SALON_ROW[2]
    assert rows[SALON_ROW[4]]["salon_code"] == SALON_ROW[0]
    assert rows[SALON_ROW[4]]["usage_count"] == 2
    # Запись, которой нет в файле, осталась нетронутой.
    assert rows["г. Тверь, ул. Своя, д. 1"]["salon_code"] in (None, "")


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


# ─────────────────────────────────────────────────────────────
# Двухколоночный файл оператора и починка старых записей
# ─────────────────────────────────────────────────────────────

def test_read_salons_rows_two_column_file(work_dir):
    """Шапка «Наименование салона» + «Адрес» читается и чистит хвост."""
    path = _two_column_salon_xlsx(
        work_dir / "two_columns.xlsx",
        [(name, address) for name, address, _clean in RESULT_SAMPLE_ROWS[:3]],
    )

    rows = read_salons_rows(path)

    assert len(rows) == 3
    for parsed, (name, _address, clean) in zip(rows, RESULT_SAMPLE_ROWS[:3]):
        assert parsed["salon_name"] == name
        assert parsed["address"] == clean


def test_read_salons_rows_standard_file_is_not_changed():
    """Справочник на 192 строки читается как раньше, без обрезаний."""
    rows = read_salons_rows(salons_xlsx_path())

    assert len(rows) == 192
    for row in rows:
        assert split_salon_name(row["address"], row["salon_name"]) == (
            row["address"], row["salon_name"]
        )


def test_read_salons_rows_result_sample(tests_data_dir):
    """Первые 10 строк result.xlsx: наименование и адрес есть у всех."""
    path = tests_data_dir / "salons" / "result_sample.xlsx"

    rows = read_salons_rows(str(path))

    assert len(rows) == 10
    assert all(row["address"] for row in rows)
    assert all(row["salon_name"] for row in rows)
    assert rows[0]["address"] == RESULT_SAMPLE_ROWS[0][2]


def test_import_result_sample_fills_name_and_cleans_address(
    isolated_db, tests_data_dir
):
    """После импорта первых 10 строк в базе и адрес, и наименование."""
    path = tests_data_dir / "salons" / "result_sample.xlsx"
    rows = read_salons_rows(str(path))

    assert import_addresses_from_list("unloading", rows) == 10

    db_rows = get_addresses("unloading")
    assert len(db_rows) == 10
    for row in db_rows:
        assert row["address"], "потерялся адрес"
        assert row["salon_name"], "потерялось наименование"
        assert row["salon_name"].casefold() not in row["address"].casefold(), (
            f"наименование осталось в адресе: {row['address']}"
        )

    by_address = {row["address"]: row for row in db_rows}
    for name, _raw, clean in RESULT_SAMPLE_ROWS:
        assert by_address[clean]["salon_name"] == name


def test_import_stats_for_new_rows(isolated_db):
    """stats заполняется для интерфейса: что обработано и что добавлено."""
    stats = {}

    added = import_addresses_from_list(
        "unloading",
        [{"address": "г. Тверь, ул. Новая, д. 1"}],
        stats=stats,
    )

    assert added == 1
    assert stats == {
        "processed": 1, "added": 1,
        "repaired": 0, "merged": 0, "removed": 0,
    }


def test_import_repairs_old_dirty_salon_rows(isolated_db):
    """
    Старая запись-наименование и старая запись с хвостом сливаются в одну.

    Так выглядит база после прежнего импорта result.xlsx: в address лежит
    либо само наименование, либо адрес с наименованием в хвосте, плюс
    строка-шапка «Наименование салона».
    """
    name = "АВТОМАГ (Нижневартовск) ООО"
    raw = (
        "Нижневартовск, Мира, владение 3/п, строение 11, "
        "АВТОМАГ (Нижневартовск) ООО"
    )
    clean = "Нижневартовск, Мира, владение 3/п, строение 11"

    save_address("unloading", name)                 # адрес = наименование
    save_address("unloading", raw, salon_name=name)  # хвост в адресе
    save_address("unloading", "Наименование салона")  # строка-шапка

    stats = {}
    added = import_addresses_from_list(
        "unloading",
        [{"address": raw, "salon_name": name}],
        stats=stats,
    )

    assert added == 0, "старая запись должна быть починена, а не добавлена"
    assert stats["repaired"] == 1
    assert stats["merged"] == 1
    assert stats["removed"] == 1

    rows = get_addresses("unloading")
    assert len(rows) == 1, "дубли не слились"
    row = rows[0]
    assert row["address"] == clean
    assert row["salon_name"] == name
    assert row["usage_count"] == 3, "usage_count двух старых записей не сложился"

    # FTS-индекс обновлён точечно: поиск по новой части адреса работает.
    assert get_addresses("unloading", search="Мира")[0]["address"] == clean


def test_import_repairs_shared_salon_name_without_duplicates(isolated_db):
    """Одно имя на несколько адресов: одна запись чинится, остальные — новые."""
    name = 'ООО "Мэйджор Авто Комплекс"'
    address_1 = (
        "Московская область, г.о. Люберцы, г. Дзержинский, "
        "ул. Алексеевская, д. 12"
    )
    address_2 = (
        "Московская область, г.о. Красногорск, д Михалково, "
        "тер. Мэйджор Сити, стр. 7"
    )

    save_address("unloading", name)  # след прежнего fallback

    stats = {}
    added = import_addresses_from_list(
        "unloading",
        [
            {"address": f"{address_1}, {name}", "salon_name": name},
            {"address": f"{address_2}, {name}", "salon_name": name},
        ],
        stats=stats,
    )

    assert added == 1
    assert stats["repaired"] == 1

    rows = get_addresses("unloading")
    assert len(rows) == 2
    assert {row["address"] for row in rows} == {address_1, address_2}
    assert {row["salon_name"] for row in rows} == {name}


def test_import_does_not_touch_unrelated_record(isolated_db):
    """Чужая запись справочника не меняется и не удаляется."""
    save_address("unloading", "г. Тверь, ул. Своя, д. 1")

    import_addresses_from_list(
        "unloading",
        [{"address": "г. Москва, ул. Новая, д. 2", "salon_name": "ООО «Салон»"}],
    )

    rows = {row["address"]: row for row in get_addresses("unloading")}
    assert "г. Тверь, ул. Своя, д. 1" in rows
    assert rows["г. Тверь, ул. Своя, д. 1"]["salon_name"] in (None, "")
    assert rows["г. Москва, ул. Новая, д. 2"]["salon_name"] == "ООО «Салон»"
