#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты разбора паспорта РФ, водительского удостоверения, тягача и полуприцепа
(core/document_fields.py).

Все данные синтетические: вымышленные ФИО, номера, адреса, марки и госномера.
Реальные персональные данные из отчёта о дефекте здесь не используются.
"""

import pytest

from core.document_import_service import extract_local_fields

# ── Кейс 1: полный набор (паспорт, ВУ, тягач, полуприцеп) ─────────────────
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

# ── Кейс 8 из разведки: те же данные, но каждая строка отдельно ──────────
MULTILINE_TEXT = """Водитель:
Сидоров Артём Львович-Младший
дата рождения: 15.03.1985
место рождения: Республика Татарстан, г. Казань, ул. Баумана 12
Паспорт РФ
45 12 345678
выдан 20.06.2015
код подразделения 160 001
Кем выдан: Отделом УФМС России по Республике Татарстан
Адрес регистрации:
Республика Татарстан, г. Казань, ул. Ленина 5, кв. 10
Водительское удостоверение
16 34 567890
выдано 10.10.2020
срок до 10.10.2030
категории B, C, CE
Телефон: +7 900 123-45-67
Тягач: KAMAZ 5490, госномер А123ВС77, цвет синий
Полуприцеп: KRONE SD, госномер ЕК456789"""

TRACTOR_ONLY_TEXT = "Тягач: VOLVO FH, госномер М456ОР50, цвет красный"

FOREIGN_TRAILER_TEXT = (
    "Тягач: DAF XF, госномер Н789ТУ99, цвет чёрный\n"
    "Полуприцеп: SCHMITZ CARGOBULL, госномер 01AB234CD"
)

HYPHEN_SURNAME_TEXT = "Водитель: Иванов-Петров Сергей Олегович, д.р. 01.01.1990"

REGISTRATION_ONLY_TEXT = (
    "Адрес регистрации:\n"
    "Республика Татарстан, с. Ново-Тестово, Заречная 2-й пер"
)


def _driver(text):
    data = extract_local_fields(text)
    assert "driver" in data, f"водитель не распознан: {data}"
    return data["driver"][0]


def _tractor(text):
    data = extract_local_fields(text)
    assert "tractor" in data, f"тягач не распознан: {data}"
    return data["tractor"][0]


def _trailer(text):
    data = extract_local_fields(text)
    assert "trailer" in data, f"полуприцеп не распознан: {data}"
    return data["trailer"][0]


@pytest.fixture
def full():
    return extract_local_fields(FULL_TEXT)


@pytest.fixture
def multiline():
    return extract_local_fields(MULTILINE_TEXT)


# ─────────────────────────────────────────────────────────────
# Паспорт РФ
# ─────────────────────────────────────────────────────────────

def test_passport_code_is_extracted(full):
    assert full["driver"][0]["passport_code"] == "160-001"


def test_passport_code_with_space_is_normalized(multiline):
    assert multiline["driver"][0]["passport_code"] == "160-001"


def test_passport_series_and_number_are_not_swapped(full):
    driver = full["driver"][0]
    assert driver["passport_series"] == "45 12"
    assert driver["passport_number"] == "345678"
    # Серия паспорта не должна попадать в номер ВУ и наоборот.
    assert driver["license_series"] == "16 34"
    assert driver["license_number"] == "567890"


def test_passport_fields_from_multiline_card(multiline):
    driver = multiline["driver"][0]
    assert driver["passport_series"] == "45 12"
    assert driver["passport_number"] == "345678"
    assert driver["passport_issue_date"] == "20.06.2015"
    assert driver["passport_issuer"].startswith("Отделом УФМС")


def test_dates_are_dd_mm_yyyy(full):
    driver = full["driver"][0]
    assert driver["birth_date"] == "15.03.1985"
    assert driver["passport_issue_date"] == "20.06.2015"
    assert driver["license_issue_date"] == "10.10.2020"
    assert driver["license_expiry_date"] == "10.10.2030"


def test_birth_date_short_label_is_supported():
    driver = _driver("Водитель: Кузнецов Пётр Иванович, д.р. 15.03.1985")
    assert driver["birth_date"] == "15.03.1985"


# ─────────────────────────────────────────────────────────────
# Место рождения и адрес регистрации
# ─────────────────────────────────────────────────────────────

def test_birth_place_is_not_registration_address(full):
    driver = full["driver"][0]
    assert driver["birth_place"] == "Республика Татарстан, г. Казань, ул. Баумана 12"
    assert driver["registration_address"] == "Республика Татарстан, г. Казань, ул. Ленина 5, кв. 10"


def test_birth_place_does_not_swallow_next_block(multiline):
    """Регресс: адрес не должен склеиваться со следующим заголовком."""
    driver = multiline["driver"][0]
    assert "Водительское удостоверение" not in driver["registration_address"]
    assert "Паспорт" not in driver["birth_place"]


def test_registration_address_with_ordinal_lane():
    driver = _driver(REGISTRATION_ONLY_TEXT)
    assert driver["registration_address"] == "Республика Татарстан, с. Ново-Тестово, Заречная 2-й пер"


# ─────────────────────────────────────────────────────────────
# ФИО
# ─────────────────────────────────────────────────────────────

def test_hyphenated_patronymic_is_kept_whole(full):
    assert full["driver"][0]["full_name"] == "Кузнецов Пётр Иванович-Младший"


def test_hyphenated_surname_is_kept_whole():
    assert _driver(HYPHEN_SURNAME_TEXT)["full_name"] == "Иванов-Петров Сергей Олегович"


def test_hyphen_is_not_left_dangling(multiline):
    assert multiline["driver"][0]["full_name"] == "Сидоров Артём Львович-Младший"


# ─────────────────────────────────────────────────────────────
# Водительское удостоверение
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("B, C, CE", "B, C, CE"),
    ("BC", "B, C"),
    ("B,C", "B, C"),
    ("B C", "B, C"),
    ("B, C", "B, C"),
])
def test_license_category_formats(raw, expected):
    text = f"Водительское удостоверение:\n77 88 999000, выдано 01.01.2021, категории {raw}"
    assert _driver(text)["license_categories"] == expected


def test_license_categories_from_full_card(full):
    assert full["driver"][0]["license_categories"] == "B, C, CE"


def test_empty_fields_stay_empty():
    text = "Водительское удостоверение:\n11 22 334455, выдано 05.05.2019, срок до —, категории —"
    driver = _driver(text)
    assert driver["license_series"] == "11 22"
    assert driver["license_number"] == "334455"
    assert driver["license_issue_date"] == "05.05.2019"
    assert driver.get("license_categories", "") == ""
    assert driver.get("license_expiry_date", "") == ""
    # Прочерк не должен становиться значением ни в одном поле.
    assert "—" not in " ".join(str(v) for v in driver.values())


def test_license_fields_multiline(multiline):
    driver = multiline["driver"][0]
    assert driver["license_series"] == "16 34"
    assert driver["license_number"] == "567890"
    assert driver["license_expiry_date"] == "10.10.2030"
    assert driver["license_categories"] == "B, C, CE"


# ─────────────────────────────────────────────────────────────
# Тягач и полуприцеп
# ─────────────────────────────────────────────────────────────

def test_tractor_fields(full):
    tractor = full["tractor"][0]
    assert tractor["brand_model"] == "KAMAZ 5490"
    assert tractor["plate_number"] == "А123ВС77"
    assert tractor["color"].lower() == "синий"


def test_trailer_fields(full):
    trailer = full["trailer"][0]
    assert trailer["brand_model"] == "KRONE SD"
    assert trailer["plate_number"] == "ЕК456789"


def test_tractor_and_trailer_are_not_swapped(full):
    assert full["tractor"][0]["plate_number"] == "А123ВС77"
    assert full["trailer"][0]["plate_number"] == "ЕК456789"
    assert "TRAILER" not in full["tractor"][0].get("brand_model", "").upper()


def test_trailer_is_empty_when_not_mentioned():
    data = extract_local_fields(TRACTOR_ONLY_TEXT)
    assert data["tractor"][0]["brand_model"] == "VOLVO FH"
    assert data["tractor"][0]["plate_number"] == "М456ОР50"
    # Данные тягача не дублируются в полуприцеп.
    assert "trailer" not in data


def test_russian_tractor_plate_and_color(full, multiline):
    for data in (full, multiline):
        assert data["tractor"][0]["plate_number"] == "А123ВС77"
        assert data["tractor"][0]["color"].lower() == "синий"


def test_foreign_trailer_plate_is_not_dropped():
    data = extract_local_fields(FOREIGN_TRAILER_TEXT)
    trailer = data["trailer"][0]
    assert trailer["plate_number"] == "01AB234CD"
    assert trailer["brand_model"] == "SCHMITZ CARGOBULL"
    assert data["tractor"][0]["plate_number"] == "Н789ТУ99"


def test_brand_model_stays_one_field(full):
    """Составная марка не разбивается по пробелу."""
    assert full["tractor"][0]["brand_model"] == "KAMAZ 5490"
    assert full["trailer"][0]["brand_model"] == "KRONE SD"


# ─────────────────────────────────────────────────────────────
# Телефон
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "+7 900 123-45-67",
    "+7 (900) 123-45-67",
    "8 900 123 45 67",
    "+79001234567",
])
def test_phone_formats(raw):
    driver = _driver(
        f"Водитель: Кузнецов Пётр Иванович\nд.р. 15.03.1985\nТелефон: {raw}")
    assert driver["phone"]
    digits = "".join(ch for ch in driver["phone"] if ch.isdigit())
    assert digits.endswith("9001234567")


def test_phone_from_full_card(full):
    assert full["driver"][0]["phone"] == "+7 900 123-45-67"
