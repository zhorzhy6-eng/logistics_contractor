#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты единого контракта данных (core/contract_data.py).

Здесь фиксируется поведение, из-за отсутствия которого в проекте ломались
тягач, полуприцеп и город в договоре.
"""

import pytest

from core.contract_data import DEFAULT_CITY, ContractData


# ─────────────────────────────────────────────────────────────
# coerce: совместимость с разными форматами входа
# ─────────────────────────────────────────────────────────────

def test_coerce_none_returns_empty():
    data = ContractData.coerce(None)
    assert isinstance(data, ContractData)
    assert data.driver == {}
    assert data.vehicles == []
    assert data.loadings == []


def test_coerce_returns_same_instance():
    data = ContractData()
    assert ContractData.coerce(data) is data


def test_coerce_ignores_unexpected_type():
    """Не-dict на входе не роняет приложение."""
    data = ContractData.coerce("строка вместо словаря")
    assert isinstance(data, ContractData)
    assert data.driver == {}


def test_coerce_reads_flat_tractor_and_trailer():
    data = ContractData.coerce({
        "tractor": {"brand_model": "Foton Auman", "plate_number": "O844XY196"},
        "trailer": {"brand_model": "YANGMINDA", "plate_number": "71ABF18"},
    })
    assert data.tractor["plate_number"] == "O844XY196"
    assert data.trailer["plate_number"] == "71ABF18"


def test_coerce_reads_legacy_nested_format():
    """
    Историческая структура data["trailer"]["tractor"] — именно из-за неё
    тягач и полуприцеп не попадали в договор.
    """
    data = ContractData.coerce({
        "tractor": {},
        "trailer": {
            "tractor": {"brand_model": "OLD-TRACTOR", "plate_number": "A001AA"},
            "trailer": {"brand_model": "OLD-TRAILER", "plate_number": "B002BB"},
        },
    })
    assert data.tractor["brand_model"] == "OLD-TRACTOR"
    assert data.tractor["plate_number"] == "A001AA"
    assert data.trailer["brand_model"] == "OLD-TRAILER"
    assert data.trailer["plate_number"] == "B002BB"


def test_coerce_loadings_from_contract_block():
    """Точки могут лежать внутри contract (старый формат вкладки «Договор»)."""
    data = ContractData.coerce({
        "contract": {
            "number": "1",
            "loadings": [{"address": "Точка А", "date": "2026-09-24"}],
            "unloadings": [{"address": "Точка Б", "date": "2026-09-25"}],
        },
    })
    assert len(data.loadings) == 1
    assert data.loadings[0]["address"] == "Точка А"
    assert len(data.unloadings) == 1
    assert data.unloadings[0]["address"] == "Точка Б"


def test_coerce_normalizes_point_types():
    """Точки приводятся к строкам с тремя полями."""
    data = ContractData.coerce({
        "loadings": [{"address": 123, "date": None, "time_window": 5}],
    })
    point = data.loadings[0]
    assert point == {"address": "123", "date": "", "time_window": "5"}


def test_coerce_drops_invalid_vehicles():
    """Не-словари в списке ТС отбрасываются."""
    data = ContractData.coerce({"vehicles": [{"vin": "X"}, "мусор", 42, None]})
    assert len(data.vehicles) == 1
    assert data.vehicles[0]["vin"] == "X"


# ─────────────────────────────────────────────────────────────
# resolved_city
# ─────────────────────────────────────────────────────────────

def test_city_from_first_loading():
    data = ContractData.coerce({
        "loadings": [{"address": "183052, г.Мурманск, пр.Кольский, д.53"}],
    })
    assert data.resolved_city() == "Мурманск"


def test_city_from_explicit_field():
    data = ContractData(city="г. Москва")
    assert data.resolved_city() == "Москва"


def test_city_from_carrier_address_when_no_loadings():
    data = ContractData.coerce({
        "carrier": {"legal_address": "450027, Республика Башкортостан, г.Уфа, ул. Тестовая, 1"},
    })
    assert data.resolved_city() == "Уфа"


def test_city_default_is_moscow():
    """Историческое поведение: если город определить не удалось."""
    assert ContractData().resolved_city() == DEFAULT_CITY == "Москва"


def test_city_untouched_when_unparsable():
    """Непонятное значение не теряется."""
    assert ContractData(city="123").resolved_city() == "123"


def test_city_key_lowercase():
    data = ContractData.coerce({
        "loadings": [{"address": "183052, г.Мурманск, пр.Кольский, д.53"}],
    })
    assert data.city_key() == "мурманск"


# ─────────────────────────────────────────────────────────────
# Представления для потребителей
# ─────────────────────────────────────────────────────────────

def test_to_generator_dict_structure(contract_payload):
    data = ContractData.coerce(contract_payload)
    payload = data.to_generator_dict()

    assert set(payload) >= {"driver", "carrier", "customer", "vehicles",
                            "tractor", "trailer", "contract", "city"}
    # плоские ключи: именно это чинило баг с тягачом
    assert payload["tractor"]["plate_number"] == "O844XY196"
    assert payload["trailer"]["plate_number"] == "71ABF18"
    assert payload["contract"]["loadings"][0]["address"].startswith("183052")
    assert payload["contract"]["unloadings"]
    assert payload["city"] == "Мурманск"


def test_to_generator_dict_does_not_share_state(contract_payload):
    """Изменение результата не влияет на исходный объект."""
    data = ContractData.coerce(contract_payload)
    payload = data.to_generator_dict()
    payload["contract"]["number"] = "ИЗМЕНЕНО"
    assert data.contract["number"] == "23092026-74"


def test_to_db_dict_contains_contract_and_ids(contract_payload):
    data = ContractData.coerce(contract_payload)
    payload = data.to_db_dict()

    assert payload["number"] == "23092026-74"
    assert payload["price_without_vat"] == 180300.0
    assert payload["driver_id"] is None
    assert payload["customer_id"] is None
    assert payload["carrier_id"] is None


def test_to_db_dict_keeps_explicit_ids():
    data = ContractData(contract={"number": "1"}, driver_id=7, carrier_id=9)
    payload = data.to_db_dict()
    assert payload["driver_id"] == 7
    assert payload["carrier_id"] == 9


# ─────────────────────────────────────────────────────────────
# to_vehicle_rows
# ─────────────────────────────────────────────────────────────

def test_to_vehicle_rows_includes_tractor_and_trailer(contract_payload):
    rows = ContractData.coerce(contract_payload).to_vehicle_rows()
    types = [row.get("vehicle_type") for row in rows]
    assert len(rows) == 3
    assert "Тягач" in types
    assert "Полуприцеп" in types
    assert "Легковой автомобиль" in types


def test_to_vehicle_rows_skips_empty_tractor_and_trailer():
    data = ContractData(vehicles=[{"vin": "X", "brand_model": "Y"}])
    rows = data.to_vehicle_rows()
    assert len(rows) == 1
    assert rows[0].get("vehicle_type") is None   # перевозимые ТС идут как есть


def test_to_vehicle_rows_uses_plate_as_marker():
    """Тягач попадает в список по госномеру, как было в MainWindow."""
    data = ContractData(tractor={"plate_number": "O844XY196"},
                        trailer={"brand_model": "YANGMINDA"})
    rows = data.to_vehicle_rows()
    assert len(rows) == 1
    assert rows[0]["vehicle_type"] == "Тягач"


# ─────────────────────────────────────────────────────────────
# Диагностика без персональных данных
# ─────────────────────────────────────────────────────────────

def test_summary_has_no_personal_data(contract_payload):
    data = ContractData.coerce(contract_payload)
    text = data.summary()

    for secret in ("Иванов", "926830", "Мурманск", "Ромашка", "Пятигорск"):
        assert secret not in text, f"в summary попало: {secret}"
    assert "loadings=1" in text
    assert "vehicles=1" in text


def test_repr_has_no_personal_data(contract_payload):
    text = repr(ContractData.coerce(contract_payload))
    for secret in ("Иванов", "926830", "Мурманск", "Ромашка"):
        assert secret not in text, f"в repr попало: {secret}"
    # номер договора — не персональные данные, он остаётся для отладки
    assert "23092026-74" in text


@pytest.mark.parametrize("field", ["driver", "carrier", "customer"])
def test_summary_reports_presence_not_content(contract_payload, field):
    data = ContractData.coerce(contract_payload)
    assert f"{field}=да" in data.summary()
