#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты разбора паспорта РФ и водительского удостоверения
(core/document_fields.py): серия/номер, код подразделения, границы полей.

Все данные синтетические. Реальные персональные данные из отчёта о дефекте
(ФИО, номера, адрес, госномера, марки) здесь не используются.
Отдельный тест проверяет, что слой обезличивания ПДн не затронут правками.
"""

import pytest

from core.document_import_service import extract_local_fields

# ── Кейс 1: полный набор ─────────────────────────────────────────────────
FULL_TEXT = """Водитель:
Кузнецов Пётр Иванович-Младший, д.р. 15.03.1985,
место рождения: Республика Татарстан, г. Казань, ул. Баумана 12

Паспорт РФ:
45 12 345678, выдан 20.06.2015,
код подразделения: 160-001
Кем выдан: Отделом УФМС России по Республике Татарстан
Адрес регистрации:
Республика Татарстан, г. Казань, ул. Ленина 5, кв. 10

Водительское удостоверение:
16 34 567890, выдано 10.10.2020, срок до 10.10.2030,
категории B, C, CE

Телефон: +7 900 123-45-67

Тягач: KAMAZ 5490, госномер А123ВС77, цвет синий
Полуприцеп: KRONE SD, госномер ЕК456789"""

# ── Кейс 2: код подразделения с пробелом ─────────────────────────────────
CODE_WITH_SPACE_TEXT = """Водитель:
Кузнецов Пётр Иванович, д.р. 15.03.1985
Паспорт РФ:
45 12 345678, выдан 20.06.2015
код подразделения: 160 001
Адрес регистрации:
Республика Татарстан, г. Казань, ул. Ленина 5, кв. 10"""

# ── Кейс 3: пустые поля ВУ ───────────────────────────────────────────────
EMPTY_LICENSE_TEXT = (
    "Водительское удостоверение:\n"
    "11 22 334455, выдано 05.05.2019, срок до —, категории —"
)

# ── Кейс 4: только тягач ─────────────────────────────────────────────────
TRACTOR_ONLY_TEXT = "Тягач: VOLVO FH, госномер М456ОР50, цвет красный"

# ── Кейс 5: адрес без следующего поля ────────────────────────────────────
ADDRESS_ONLY_TEXT = (
    "Адрес регистрации:\n"
    "Республика Татарстан, г. Казань, ул. Ленина 5, кв. 10"
)

# ── Кейс 6: серия и номер без пробелов ───────────────────────────────────
SOLID_NUMBER_TEXT = """Водитель:
Кузнецов Пётр Иванович, д.р. 15.03.1985
Паспорт РФ:
4512345678, выдан 20.06.2015
код подразделения: 160-001"""

# ── Серия слитно, номер отделён: «4512 345678», OCR часто даёт «9609 188174» ──
GLUED_SERIES_TEXT = """Водитель:
Кузнецов Пётр Иванович, д.р. 15.03.1985
Паспорт РФ:
9609 188174, выдан 20.06.2015
код подразделения: 160-001"""

# ── Раздельные метки «Серия:» и «Номер:» ─────────────────────────────────
SERIES_BY_LABELS_TEXT = """Водитель:
Кузнецов Пётр Иванович, д.р. 15.03.1985
Паспорт РФ:
Серия: 45 12
Номер: 345678
выдан 20.06.2015
код подразделения: 160-001"""

LICENSE_SOLID_TEXT = """Водитель:
Кузнецов Пётр Иванович, д.р. 15.03.1985
Водительское удостоверение:
1634567890, выдано 10.10.2020, срок до 10.10.2030, категории B, C"""

LICENSE_BY_LABELS_TEXT = """Водитель:
Кузнецов Пётр Иванович, д.р. 15.03.1985
Водительское удостоверение:
Серия: 16 34
Номер: 567890
выдано 10.10.2020
категории BC"""

#: Плейсхолдеры полей формы (ui/tabs/driver_tab.py) и прочерки —
#: ни один из них не должен попасть в распознанные значения.
PLACEHOLDERS = ("XX XX", "ХХ ХХ", "XXX-XXX", "ХХХ-ХХХ", "—", "-", "")


def _data(text):
    return extract_local_fields(text)


def _driver(text):
    data = _data(text)
    assert "driver" in data, f"водитель не распознан: {data}"
    return data["driver"][0]


def _tractor(text):
    data = _data(text)
    assert "tractor" in data, f"тягач не распознан: {data}"
    return data["tractor"][0]


@pytest.fixture
def full():
    return _data(FULL_TEXT)


# ─────────────────────────────────────────────────────────────
# Серия и номер паспорта
# ─────────────────────────────────────────────────────────────

def test_series_and_number_are_split(full):
    driver = full["driver"][0]
    assert driver["passport_series"] == "45 12"
    assert driver["passport_number"] == "345678"


def test_series_and_number_without_spaces():
    driver = _driver(SOLID_NUMBER_TEXT)
    assert driver["passport_series"] == "45 12"
    assert driver["passport_number"] == "345678"


def test_glued_series_with_separate_number():
    driver = _driver(GLUED_SERIES_TEXT)
    assert driver["passport_series"] == "96 09"
    assert driver["passport_number"] == "188174"


def test_series_and_number_by_labels():
    driver = _driver(SERIES_BY_LABELS_TEXT)
    assert driver["passport_series"] == "45 12"
    assert driver["passport_number"] == "345678"
    assert driver["passport_issue_date"] == "20.06.2015"


def test_number_is_not_a_part_of_series():
    """Регресс: в номер не должна попадать часть серии («96 09»)."""
    for text in (FULL_TEXT, SOLID_NUMBER_TEXT, GLUED_SERIES_TEXT, SERIES_BY_LABELS_TEXT):
        driver = _driver(text)
        assert len(driver["passport_number"]) == 6
        assert driver["passport_number"].isdigit()
        assert len(driver["passport_series"].replace(" ", "")) == 4


def test_passport_and_license_numbers_are_not_mixed(full):
    driver = full["driver"][0]
    assert driver["license_series"] == "16 34"
    assert driver["license_number"] == "567890"
    assert driver["passport_number"] != driver["license_number"]


# ─────────────────────────────────────────────────────────────
# Код подразделения
# ─────────────────────────────────────────────────────────────

def test_passport_code_is_extracted(full):
    assert full["driver"][0]["passport_code"] == "160-001"


def test_passport_code_with_space():
    assert _driver(CODE_WITH_SPACE_TEXT)["passport_code"] == "160-001"


def test_passport_code_with_em_dash():
    text = FULL_TEXT.replace("код подразделения: 160-001", "код подразделения: 160—001")
    assert _driver(text)["passport_code"] == "160-001"


def test_passport_code_absent_stays_absent():
    text = FULL_TEXT.replace("код подразделения: 160-001\n", "")
    assert "passport_code" not in _driver(text)


# ─────────────────────────────────────────────────────────────
# Границы полей: адрес не захватывает следующее поле
# ─────────────────────────────────────────────────────────────

def test_registration_address_does_not_swallow_license_block(full):
    address = full["driver"][0]["registration_address"]
    assert address == "Республика Татарстан, г. Казань, ул. Ленина 5, кв. 10"
    for marker in ("Водительское", "Тягач", "Полуприцеп", "Телефон", "Паспорт"):
        assert marker not in address


def test_registration_address_without_next_field():
    driver = _driver(ADDRESS_ONLY_TEXT)
    assert driver["registration_address"] == "Республика Татарстан, г. Казань, ул. Ленина 5, кв. 10"


def test_registration_address_stops_at_tractor():
    text = (
        "Водитель:\nКузнецов Пётр Иванович, д.р. 15.03.1985\n"
        "Адрес регистрации:\nРеспублика Татарстан, г. Казань, ул. Ленина 5, кв. 10\n"
        "Тягач: VOLVO FH, госномер М456ОР50"
    )
    driver = _driver(text)
    assert "Тягач" not in driver["registration_address"]
    assert "VOLVO" not in driver["registration_address"]


def test_birth_place_is_complete_and_not_truncated(full):
    driver = full["driver"][0]
    assert driver["birth_place"] == "Республика Татарстан, г. Казань, ул. Баумана 12"
    assert driver["birth_place"] != driver["registration_address"]


def test_birth_place_multiline():
    text = (
        "Водитель:\nКузнецов Пётр Иванович, д.р. 15.03.1985\n"
        "место рождения: Республика Татарстан,\n"
        "г. Казань, ул. Баумана 12\n"
        "Паспорт РФ:\n45 12 345678, выдан 20.06.2015"
    )
    assert _driver(text)["birth_place"] == "Республика Татарстан, г. Казань, ул. Баумана 12"


# ─────────────────────────────────────────────────────────────
# ФИО, даты, пустые поля, плейсхолдеры
# ─────────────────────────────────────────────────────────────

def test_hyphenated_name_is_kept_whole(full):
    assert full["driver"][0]["full_name"] == "Кузнецов Пётр Иванович-Младший"


def test_dates_are_dd_mm_yyyy(full):
    driver = full["driver"][0]
    assert driver["birth_date"] == "15.03.1985"
    assert driver["passport_issue_date"] == "20.06.2015"
    assert driver["license_issue_date"] == "10.10.2020"
    assert driver["license_expiry_date"] == "10.10.2030"


def test_empty_fields_stay_empty():
    driver = _driver(EMPTY_LICENSE_TEXT)
    assert driver["license_series"] == "11 22"
    assert driver["license_number"] == "334455"
    assert driver["license_issue_date"] == "05.05.2019"
    assert driver.get("license_categories", "") == ""
    assert driver.get("license_expiry_date", "") == ""


def test_no_placeholders_or_dashes_in_values(full):
    """Плейсхолдеры формы («XX XX», «ХХХ-ХХХ») и прочерки в данные не попадают."""
    values = []
    for section in ("driver", "tractor", "trailer"):
        for record in full.get(section, []):
            values.extend(str(value) for value in record.values() if value)
    assert values
    for value in values:
        assert value not in PLACEHOLDERS
        assert "XX" not in value.upper() or value.isdigit()
        assert "—" not in value


def test_empty_document_has_no_placeholder_values():
    driver = _driver(EMPTY_LICENSE_TEXT)
    for value in driver.values():
        assert str(value).strip() not in PLACEHOLDERS


# ─────────────────────────────────────────────────────────────
# Категории ВУ
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("B, C", "B, C"),
    ("BC", "B, C"),
    ("B,C", "B, C"),
    ("B C", "B, C"),
    ("B, C, CE", "B, C, CE"),
])
def test_license_categories_formats(raw, expected):
    text = (
        "Водитель:\nКузнецов Пётр Иванович, д.р. 15.03.1985\n"
        f"Водительское удостоверение:\n77 88 999000, выдано 01.01.2021, категории {raw}"
    )
    assert _driver(text)["license_categories"] == expected


def test_license_series_and_number_by_labels():
    driver = _driver(LICENSE_BY_LABELS_TEXT)
    assert driver["license_series"] == "16 34"
    assert driver["license_number"] == "567890"
    assert driver["license_categories"] == "B, C"


def test_license_series_and_number_without_spaces():
    driver = _driver(LICENSE_SOLID_TEXT)
    assert driver["license_series"] == "16 34"
    assert driver["license_number"] == "567890"
    assert driver["license_expiry_date"] == "10.10.2030"


# ─────────────────────────────────────────────────────────────
# Тягач и полуприцеп
# ─────────────────────────────────────────────────────────────

def test_tractor_russian_plate(full):
    tractor = full["tractor"][0]
    assert tractor["brand_model"] == "KAMAZ 5490"
    assert tractor["plate_number"] == "А123ВС77"
    assert tractor["color"].lower() == "синий"


def test_trailer_foreign_plate_is_kept():
    data = _data(
        "Тягач: DAF XF, госномер Н789ТУ99, цвет чёрный\n"
        "Полуприцеп: SCHMITZ CARGOBULL, госномер 01AB234CD"
    )
    assert data["trailer"][0]["plate_number"] == "01AB234CD"
    assert data["trailer"][0]["brand_model"] == "SCHMITZ CARGOBULL"


def test_tractor_and_trailer_are_not_swapped(full):
    assert full["tractor"][0]["plate_number"] == "А123ВС77"
    assert full["trailer"][0]["plate_number"] == "ЕК456789"
    assert full["trailer"][0]["brand_model"] == "KRONE SD"


def test_trailer_is_empty_when_not_mentioned():
    data = _data(TRACTOR_ONLY_TEXT)
    assert data["tractor"][0]["plate_number"] == "М456ОР50"
    assert "trailer" not in data


# ─────────────────────────────────────────────────────────────
# Страховка: слой обезличивания ПДн не затронут
# ─────────────────────────────────────────────────────────────

def test_pseudonymization_layer_is_untouched():
    """Правки парсера не меняют обезличивание: round-trip по-прежнему работает."""
    from core.pseudonymizer import Pseudonymizer

    text = "Кузнецов Пётр Иванович, паспорт 45 12 345678, тел +7 900 123-45-67"
    safe, mapping = Pseudonymizer().anonymize(text)
    assert "Кузнецов" not in safe
    assert "345678" not in safe
    assert Pseudonymizer().restore(safe, mapping) == text
