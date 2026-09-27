#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты извлечения данных (core/recognizer.py) и разбора адресов
(core/address_utils.py).

Проверяются парсеры полей, восстановление потерянных VIN, разбор блока
договора и нормализация городов — в том числе на реальных адресах из
contracts.db, если база доступна.
"""

import sqlite3

import pytest

from core.address_utils import extract_city, city_for_document
from core.recognizer import DataMapper

VIN_1 = "EC3TEUMB0T0002608"
VIN_2 = "EC3TEUMB9T0003935"
VIN_3 = "LB3P11SN4TH405381"


# ─────────────────────────────────────────────────────────────
# Скалярные парсеры
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    ("2026-09-23", "2026-09-23"),
    ("23.09.2026", "2026-09-23"),
    ("23/09/2026", "2026-09-23"),
    ("2026.09.23", "2026-09-23"),
    ("23-09-2026", "2026-09-23"),
    ("20260923", "2026-09-23"),
    ("23 09 2026", "2026-09-23"),
    ("2026-09-23T00:00:00", "2026-09-23"),
])
def test_parse_date(value, expected):
    assert DataMapper._parse_date(value) == expected


@pytest.mark.parametrize("value", ["", None, "null", "none", "undefined", "yyyy-mm-dd", "мусор"])
def test_parse_date_invalid(value):
    assert DataMapper._parse_date(value) == ""


@pytest.mark.parametrize("value, expected", [
    ("7701234567", "7701234567"),
    ("770123456789", "770123456789"),
    (" 7701234567 ", "7701234567"),
    ("ИНН 7701234567", "7701234567"),
])
def test_parse_inn(value, expected):
    assert DataMapper._parse_inn(value) == expected


def test_parse_inn_wrong_length_keeps_digits():
    """Неверная длина — цифры всё равно возвращаются (поведение зафиксировано)."""
    assert DataMapper._parse_inn("123") == "123"


def test_parse_kpp():
    assert DataMapper._parse_kpp("770701001") == "770701001"
    assert DataMapper._parse_kpp("") == ""
    assert DataMapper._parse_kpp(None) == ""


def test_parse_ogrn():
    assert DataMapper._parse_ogrn("1027700132195") == "1027700132195"
    assert DataMapper._parse_ogrn("315770000100012") == "315770000100012"
    assert DataMapper._parse_ogrn("") == ""


@pytest.mark.parametrize("value, expected", [
    ("o844xy196", "O844XY196"),
    ("O 844 XY 196", "O844XY196"),
    ("71-abf-18", "71ABF18"),
    ("", ""),
    (None, ""),
    ("null", ""),
])
def test_parse_plate(value, expected):
    assert DataMapper._parse_plate(value) == expected


def test_parse_plate_does_not_transliterate():
    """Кириллические буквы не превращаются в латинские — это реальное поведение."""
    assert DataMapper._parse_plate("о844ху196") == "О844ХУ196"


@pytest.mark.parametrize("value, expected", [
    (2024, 2024),
    ("2024", 2024),
    ("2024 год", 2024),
    (0, 0),
    ("", 0),
    (None, 0),
    ("null", 0),
    ("мусор", 0),
    (1800, 0),          # вне допустимого диапазона
    (3000, 0),
    (True, 0),          # bool не год
])
def test_parse_year(value, expected):
    assert DataMapper._parse_year(value) == expected


@pytest.mark.parametrize("value, expected", [
    (0, 0), (1, 1), (3, 3), (10, 10),
    ("2", 2), ("", 0), (None, 0), (11, 0), (-1, 0), ("мусор", 0), (True, 0),
])
def test_parse_index(value, expected):
    assert DataMapper._parse_index(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("текст", "текст"),
    ("  текст  ", "текст"),
    (None, ""),
    ("null", ""), ("none", ""), ("NaN", ""), ("undefined", ""),
    (5, "5"),
    (5.5, "5.5"),
])
def test_clean_value(value, expected):
    assert DataMapper._clean_value(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("22%", "22%"), ("22", "22%"), (22, "22%"), ("0", "0%"),
    ("", "22%"), (None, "22%"), ("null", "22%"), ("мусор", "22%"),
])
def test_parse_vat_rate(value, expected):
    assert DataMapper._parse_vat_rate(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("прицеп", "Прицеп"),
    ("ПОЛУПРИЦЕП", "Прицеп"),
    ("фургон", "Фургон"),
    ("автобус", "Фургон"),
    ("седельный тягач", "Тягач"),
    ("тягач", "Тягач"),
    ("легковой", "Легковой автомобиль"),
    ("", "Легковой автомобиль"),
    (None, "Легковой автомобиль"),
    ("null", "Легковой автомобиль"),
])
def test_normalize_vehicle_type(value, expected):
    assert DataMapper._normalize_vehicle_type(value) == expected


# ─────────────────────────────────────────────────────────────
# VIN: разделение, поиск, восстановление
# ─────────────────────────────────────────────────────────────

def test_split_brand_and_vin_existing_vin():
    brand, vin = DataMapper._split_brand_and_vin("JETOUR T2", VIN_1)
    assert (brand, vin) == ("JETOUR T2", VIN_1)


def test_split_brand_and_vin_from_dash():
    brand, vin = DataMapper._split_brand_and_vin(f"JETOUR T2 - {VIN_1}")
    assert brand == "JETOUR T2"
    assert vin == VIN_1


def test_split_brand_and_vin_glued():
    brand, vin = DataMapper._split_brand_and_vin(f"JETOUR T2{VIN_1}")
    assert brand == "JETOUR T2"
    assert vin == VIN_1


def test_split_brand_and_vin_absent():
    brand, vin = DataMapper._split_brand_and_vin("JETOUR T2", "")
    assert brand == "JETOUR T2"
    assert vin == ""


def test_split_brand_and_vin_empty():
    assert DataMapper._split_brand_and_vin("", "") == ("", "")


def test_find_vins_unique_and_ordered():
    text = f"Машина A {VIN_1}\nМашина B {VIN_2}\nПовтор {VIN_1}\nМашина C {VIN_3}"
    assert DataMapper._find_vins_in_text(text) == [VIN_1, VIN_2, VIN_3]


def test_find_vins_ignores_forbidden_letters():
    """В VIN не бывает букв I, O, Q — такие «коды» не считаются VIN."""
    assert DataMapper._find_vins_in_text("IOQIOQIOQIOQIOQIO") == []


def test_find_vins_empty_text():
    assert DataMapper._find_vins_in_text("") == []
    assert DataMapper._find_vins_in_text(None) == []


@pytest.mark.parametrize("line, expected", [
    (f"JETOUR T2 2.0Т 7DCT Престиж\t{VIN_1}", "JETOUR T2 2.0Т 7DCT Престиж"),
    (f"JETOUR T2 - {VIN_1}", "JETOUR T2"),
    (f"{VIN_1} — JETOUR T2", "JETOUR T2"),
    (f"1. JETOUR T2 - {VIN_1}", "JETOUR T2"),
    (f"JETOUR T2   {VIN_1}", "JETOUR T2"),
])
def test_extract_brand_for_vin(line, expected):
    assert DataMapper._extract_brand_for_vin(line, VIN_1) == expected


def test_extract_brand_for_vin_not_found():
    assert DataMapper._extract_brand_for_vin("нет такого вина тут", VIN_1) == ""


def test_recover_missing_vins_adds_lost():
    recognized = [{"vin": VIN_1, "brand_model": "JETOUR T2", "year": 0,
                   "plate_number": "", "color": "", "vehicle_type": "Легковой автомобиль",
                   "loading_index": 0, "unloading_index": 0}]
    text = f"JETOUR T2\t{VIN_1}\nHAVAL H5\t{VIN_2}"

    result = DataMapper._recover_missing_vins(recognized, text)
    vins = [v["vin"] for v in result]
    assert vins == [VIN_1, VIN_2]
    assert result[1]["brand_model"] == "HAVAL H5"


def test_recover_missing_vins_nothing_to_recover():
    recognized = [{"vin": VIN_1, "brand_model": "JETOUR T2"}]
    assert DataMapper._recover_missing_vins(recognized, f"JETOUR T2\t{VIN_1}") == recognized


def test_recover_missing_vins_orders_by_text():
    """Порядок машин восстанавливается по порядку в исходном тексте."""
    recognized = [{"vin": VIN_3, "brand_model": "C"}]
    text = f"B {VIN_2}\nC {VIN_3}\nA {VIN_1}"
    result = DataMapper._recover_missing_vins(list(recognized), text)
    assert [v["vin"] for v in result] == [VIN_2, VIN_3, VIN_1]


def test_recover_missing_vins_without_source_text():
    recognized = [{"vin": VIN_1}]
    assert DataMapper._recover_missing_vins(recognized, "") == recognized


# ─────────────────────────────────────────────────────────────
# Разбор маршрута
# ─────────────────────────────────────────────────────────────

def test_split_multi_address_by_newline():
    point = {"address": "Точка А\nТочка Б", "date": "2026-09-24", "time_window": "09:00"}
    parts = DataMapper._split_multi_address(point)
    assert len(parts) == 2
    assert parts[0]["address"] == "Точка А"
    assert parts[1]["date"] == "2026-09-24"


def test_split_multi_address_by_markers():
    point = {"address": "Погрузка 1: Склад А\nПогрузка 2: Склад Б", "date": "", "time_window": ""}
    parts = DataMapper._split_multi_address(point)
    assert [p["address"] for p in parts] == ["Склад А", "Склад Б"]


def test_split_multi_address_single():
    point = {"address": "Один адрес", "date": "", "time_window": ""}
    assert DataMapper._split_multi_address(point) == [point]


def test_split_multi_address_empty():
    point = {"address": "", "date": "", "time_window": ""}
    assert DataMapper._split_multi_address(point) == [point]


# ─────────────────────────────────────────────────────────────
# Полные мапперы
# ─────────────────────────────────────────────────────────────

def test_map_driver_data(driver_data):
    result = DataMapper.map_driver_data(driver_data)
    assert result["full_name"] == "Иванов Иван Иванович"
    assert result["birth_date"] == "1980-01-01"
    assert result["passport_full"] == "18 22 926830"
    assert len(result) >= len(DataMapper.DRIVER_FIELDS)


def test_map_driver_data_empty():
    assert DataMapper.map_driver_data({}) == {}


def test_map_driver_data_cleans_junk():
    result = DataMapper.map_driver_data({"full_name": "  Иванов  ", "phone": "null"})
    assert result["full_name"] == "Иванов"
    assert result["phone"] == ""


def test_map_organization_ip_resets_kpp():
    result = DataMapper.map_organization_data({
        "full_name": "Индивидуальный предприниматель Хейгетян Елена Валентиновна",
        "inn": "770123456789",
        "kpp": "770701001",
        "ogrn": "315770000100012",
    }, is_carrier=True)

    assert result["entity_type"] == "ИП"
    assert result["kpp"] == ""
    assert result["director_name"] == "Хейгетян Елена Валентиновна"
    assert result["director_position"] == "Индивидуальный предприниматель"
    assert "license_number" in result


def test_map_organization_ooo_keeps_kpp(organization_data):
    result = DataMapper.map_organization_data(organization_data, is_carrier=False)
    assert result["entity_type"] == "ООО"
    assert result["kpp"] == "770101001"
    assert "license_number" not in result   # лицензия только у перевозчика


def test_map_organization_empty():
    assert DataMapper.map_organization_data({}) == {}


def test_map_vehicles_data_splits_vin_from_brand():
    result = DataMapper.map_vehicles_data([
        {"vin": "", "brand_model": f"JETOUR T2 - {VIN_1}", "year": "2024",
         "vehicle_type": "легковой"},
    ])
    assert len(result) == 1
    assert result[0]["vin"] == VIN_1
    assert result[0]["brand_model"] == "JETOUR T2"
    assert result[0]["year"] == 2024
    assert result[0]["vehicle_type"] == "Легковой автомобиль"


def test_map_vehicles_data_skips_empty_and_junk():
    result = DataMapper.map_vehicles_data([
        {"vin": "", "brand_model": ""},
        "мусор",
        None,
        {"vin": VIN_1, "brand_model": ""},
    ])
    assert len(result) == 1
    assert result[0]["vin"] == VIN_1


def test_map_vehicles_data_empty():
    assert DataMapper.map_vehicles_data([]) == []
    assert DataMapper.map_vehicles_data(None) == []


def test_map_tractor_and_trailer():
    tractor = DataMapper.map_tractor_data({"brand_model": "Foton", "plate_number": "o844xy196",
                                           "color": "Белый", "year": "2023"})
    assert tractor == {"brand_model": "Foton", "plate_number": "O844XY196",
                       "color": "Белый", "year": 2023}
    trailer = DataMapper.map_trailer_data({"brand_model": "YANGMINDA", "plate_number": "71-abf-18"})
    assert trailer["plate_number"] == "71ABF18"
    assert DataMapper.map_tractor_data({}) == {}
    assert DataMapper.map_trailer_data({}) == {}


def test_map_contract_data_loadings_and_unloadings():
    result = DataMapper.map_contract_data({
        "number": "23092026-74",
        "date": "23.09.2026",
        "route": "Мурманск - Пятигорск",
        "price_without_vat": "180300.5",
        "vat_rate": "22",
        "payment_days": "10",
        "loadings": [{"address": "Склад А", "date": "24.09.2026", "time_window": "09:00-18:00"},
                     {"address": "Склад Б", "date": "25.09.2026"}],
        "unloadings": [{"address": "Точка В", "date": "27.09.2026"}],
    })
    assert result["number"] == "23092026-74"
    assert result["date"] == "2026-09-23"
    assert result["price_without_vat"] == 180300.5
    assert result["vat_rate"] == "22%"
    assert result["payment_days"] == 10
    assert [p["address"] for p in result["loadings"]] == ["Склад А", "Склад Б"]
    assert result["loading_address"] == "Склад А"
    assert result["unloading_address"] == "Точка В"


def test_map_contract_data_legacy_aliases():
    result = DataMapper.map_contract_data({
        "loading_address": "Склад А",
        "loading_date": "24.09.2026",
        "unloading_address_1": "Точка Б",
        "unloading_address_2": "Точка В",
        "unloading_date": "27.09.2026",
    })
    assert [p["address"] for p in result["loadings"]] == ["Склад А"]
    assert result["unloading_address_1"] == "Точка Б"
    assert result["unloading_address_2"] == "Точка В"


def test_map_contract_data_deduplicates():
    result = DataMapper.map_contract_data({
        "loadings": [{"address": "Склад А"}, {"address": "склад а"}, {"address": "Склад Б"}],
    })
    assert [p["address"] for p in result["loadings"]] == ["Склад А", "Склад Б"]


def test_map_contract_data_splits_multi_address():
    result = DataMapper.map_contract_data({
        "loadings": [{"address": "Склад А\nСклад Б"}],
    })
    assert [p["address"] for p in result["loadings"]] == ["Склад А", "Склад Б"]


def test_map_contract_data_bad_numbers():
    result = DataMapper.map_contract_data({
        "price_without_vat": "мусор",
        "payment_days": "мусор",
    })
    assert result["price_without_vat"] == 0.0
    assert result["payment_days"] == 10


def test_map_contract_data_empty():
    assert DataMapper.map_contract_data({}) == {}


def test_map_contract_data_reads_points_from_own_dict():
    """map_contract_data читает ключ loadings из переданного блока."""
    result = DataMapper.map_contract_data({
        "number": "1",
        "loadings": [{"address": "Склад А"}],
    })
    assert [p["address"] for p in result["loadings"]] == ["Склад А"]


def test_process_full_response_ignores_top_level_points(contract_payload):
    """
    Граница поведения: process_full_response передаёт в маппер только блок
    contract, поэтому точки обязаны лежать внутри него (так их и возвращает
    модель по системному промпту).
    """
    payload = {
        "contract": dict(contract_payload["contract"]),
        "loadings": contract_payload["loadings"],
    }
    result = DataMapper.process_full_response(payload)
    assert result["contract"]["loadings"] == []


def test_process_full_response(contract_payload):
    """Полный ответ модели: точки приходят внутри блока contract."""
    payload = dict(contract_payload)
    payload["contract"] = dict(contract_payload["contract"])
    payload["contract"]["loadings"] = contract_payload["loadings"]
    payload["contract"]["unloadings"] = contract_payload["unloadings"]

    result = DataMapper.process_full_response(payload)
    assert set(result) == {"driver", "customer", "carrier", "vehicles",
                           "tractor", "trailer", "contract"}
    assert result["tractor"]["plate_number"] == "O844XY196"
    assert result["trailer"]["plate_number"] == "71ABF18"
    assert result["contract"]["loadings"]
    assert result["contract"]["unloadings"]


def test_process_full_response_restores_vins():
    payload = {
        "vehicles": [{"vin": VIN_1, "brand_model": "JETOUR T2"}],
        "contract": {},
    }
    source = f"JETOUR T2\t{VIN_1}\nHAVAL H5\t{VIN_2}"
    result = DataMapper.process_full_response(payload, source_text=source)
    assert [v["vin"] for v in result["vehicles"]] == [VIN_1, VIN_2]


def test_process_full_response_tolerates_garbage():
    result = DataMapper.process_full_response({})
    assert result["driver"] == {}
    assert result["vehicles"] == []
    assert result["contract"] == {}   # пустой блок не наполняется фиктивными ключами


# ─────────────────────────────────────────────────────────────
# Города: примеры и реальные адреса из базы
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("address, expected", [
    ("183052, г.Мурманск, пр.Кольский, д.53", "мурманск"),
    ("109316, г. Москва, Волгоградский пр-т", "москва"),
    ("ул. Крылатая, 12/1, Краснодар", "краснодар"),
    ("450027, Республика Башкортостан, г.Уфа, ул. Тестовая, 1", "уфа"),
    ("Ворсино", "ворсино"),
    ("", ""),
])
def test_extract_city_examples(address, expected):
    assert extract_city(address) == expected


def test_city_for_document_title_case():
    assert city_for_document("183052, г.Мурманск, пр.Кольский, д.53") == "Мурманск"
    assert city_for_document("") == "Москва"
    assert city_for_document("", default="") == ""


def test_extract_city_on_real_addresses(real_db_path):
    """Проверка на реальных адресах из contracts.db (если база есть)."""
    if not real_db_path.exists():
        pytest.skip("contracts.db отсутствует — проверка на реальных данных пропущена")

    conn = sqlite3.connect(f"file:{real_db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT address, city FROM address_book").fetchall()
    finally:
        conn.close()

    assert rows, "в справочнике нет адресов"

    for address, city in rows:
        if not address:
            continue
        extracted = extract_city(address)
        assert extracted, f"город не извлечён из адреса: {address!r}"
        if city:
            # city в базе заполнялся этой же функцией, поэтому значения совпадают
            assert extracted == city, f"{address!r}: {extracted!r} != {city!r}"


def test_extract_city_never_empty_for_addresses_with_letters():
    """Если в адресе есть буквы, город (хотя бы грубо) определяется."""
    for address in ("Клин", "г. Клин", "Московская область, г. Клин, ул. Ленина, 1"):
        assert extract_city(address) != ""
