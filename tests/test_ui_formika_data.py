#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты сборки данных Формики из вкладок (ЭТАП 3.1.B.1).

Проверяется ui/windows/formika/data.py::collect_formika_data: раскладка
полей вкладок по ContractData и устойчивость сборки (нет вкладки, нет
get_data(), get_data() упал, вернул не словарь).

Qt не нужен: вкладки подменяются простыми объектами-заглушками со своим
get_data(). Отдельный тест следит за тем, чтобы модуль и дальше не тянул
PyQt5 — окно создаёт вкладки, а сборка данных от них не зависит.
"""

import pytest

from core.contract_data import ContractData
from ui.windows.formika.data import (
    DEFAULT_VEHICLE_TYPE,
    DRIVER_FIELDS,
    MAX_CARS,
    collect_formika_data,
)

#: VIN из conftest (17 символов, ISO 3779) — на нём данных хватает валидатору.
VIN_OK = "EC3TEUMB0T0002608"


class StubTab:
    """Вкладка-заглушка: отдаёт заранее заданный словарь."""

    def __init__(self, data):
        self._data = data
        self.calls = 0

    def get_data(self):
        self.calls += 1
        return self._data


class BrokenTab:
    """Вкладка, чей get_data() падает."""

    def get_data(self):
        raise RuntimeError("вкладка сломана")


class NoGetDataTab:
    """Вкладка без get_data() (например, ещё не написанная)."""


class NotADictTab:
    """Вкладка, вернувшая не словарь."""

    def get_data(self):
        return ["не", "словарь"]


# ─────────────────────────────────────────────────────────────
# Пустой вход и устойчивость
# ─────────────────────────────────────────────────────────────

def test_empty_input_gives_empty_contract_data():
    cd = collect_formika_data({})

    assert isinstance(cd, ContractData)
    assert cd.contract == {}
    assert cd.driver == {}
    assert cd.vehicles == []
    assert cd.tractor == {}
    assert cd.trailer == {}
    assert cd.loadings == []
    assert cd.unloadings == []


def test_empty_input_keeps_default_city():
    """Город не заполняем: ContractData сам подставляет «Москва»."""
    cd = collect_formika_data({})

    assert cd.city == ""
    assert cd.resolved_city() == "Москва"


def test_partial_tabs_do_not_break_collection():
    tabs = {"customer": {"number": "ТЛ-447", "date": "2026-09-23"}}

    cd = collect_formika_data(tabs)

    assert cd.contract["number"] == "ТЛ-447"
    assert cd.vehicles == []
    assert cd.driver == {}


def test_tab_object_with_get_data_is_used():
    tab = StubTab({"number": "  ТЛ-447  ", "date": "2026-09-23"})

    cd = collect_formika_data({"customer": tab})

    assert tab.calls == 1
    assert cd.contract["number"] == "ТЛ-447"


def test_failing_get_data_does_not_break_collection():
    tabs = {
        "customer": BrokenTab(),
        "route": {"route": "Мурманск - Пятигорск"},
    }

    cd = collect_formika_data(tabs)

    assert cd.contract.get("number") is None
    assert cd.contract["route"] == "Мурманск - Пятигорск"


def test_tab_without_get_data_is_skipped():
    cd = collect_formika_data({"driver": NoGetDataTab()})

    assert cd.driver == {}


def test_tab_returning_not_a_dict_is_skipped():
    cd = collect_formika_data({"cargo": NotADictTab()})

    assert cd.vehicles == []


def test_tabs_not_a_mapping_gives_empty_result():
    """Даже совсем неверный вход не роняет сборку."""
    cd = collect_formika_data(None)

    assert isinstance(cd, ContractData)
    assert cd.contract == {}


def test_data_module_does_not_import_qt():
    """Сборка данных не зависит от интерфейса — PyQt5 в модуле не нужен."""
    import ui.windows.formika.data as data_module

    assert not hasattr(data_module, "QWidget")
    assert "PyQt5" not in getattr(data_module, "__dict__", {})


# ─────────────────────────────────────────────────────────────
# Заказчик: номер и дата
# ─────────────────────────────────────────────────────────────

def test_number_and_date_are_stripped():
    cd = collect_formika_data({
        "customer": {"number": "  ТЛ-447 \n", "date": " 2026-09-23 "},
    })

    assert cd.contract["number"] == "ТЛ-447"
    assert cd.contract["date"] == "2026-09-23"


def test_empty_number_is_skipped():
    cd = collect_formika_data({"customer": {"number": "   ", "date": "2026-09-23"}})

    assert "number" not in cd.contract
    assert "date" in cd.contract


def test_empty_date_is_skipped():
    cd = collect_formika_data({"customer": {"number": "ТЛ-447", "date": ""}})

    assert "date" not in cd.contract
    assert "number" in cd.contract


# ─────────────────────────────────────────────────────────────
# Груз: перевозимые машины
# ─────────────────────────────────────────────────────────────

def test_vehicles_are_collected_with_default_type():
    cd = collect_formika_data({"cargo": {"vehicles": [
        {"brand_model": "JETOUR T2", "vin": VIN_OK},
    ]}})

    assert cd.vehicles == [{
        "vehicle_type": DEFAULT_VEHICLE_TYPE,
        "brand_model": "JETOUR T2",
        "vin": VIN_OK,
    }]


def test_empty_vehicle_rows_are_dropped():
    cd = collect_formika_data({"cargo": {"vehicles": [
        {"brand_model": "", "vin": ""},
        {"brand_model": "  ", "vin": "  "},
        {"brand_model": "JETOUR T2", "vin": VIN_OK},
    ]}})

    assert len(cd.vehicles) == 1
    assert cd.vehicles[0]["brand_model"] == "JETOUR T2"


def test_vehicle_with_only_brand_or_only_vin_is_kept():
    """Заполнена хотя бы одна колонка — строку не выбрасываем."""
    cd = collect_formika_data({"cargo": {"vehicles": [
        {"brand_model": "JETOUR T2", "vin": ""},
        {"brand_model": "", "vin": VIN_OK},
    ]}})

    assert len(cd.vehicles) == 2
    assert cd.vehicles[0]["brand_model"] == "JETOUR T2"
    assert cd.vehicles[1]["vin"] == VIN_OK


def test_vehicles_are_limited_to_max_cars():
    rows = [{"brand_model": f"Машина {i}", "vin": VIN_OK} for i in range(1, 16)]

    cd = collect_formika_data({"cargo": {"vehicles": rows}})

    assert len(cd.vehicles) == MAX_CARS
    assert cd.vehicles[-1]["brand_model"] == f"Машина {MAX_CARS}"


def test_vehicles_not_a_list_is_ignored():
    cd = collect_formika_data({"cargo": {"vehicles": "мусор"}})

    assert cd.vehicles == []


# ─────────────────────────────────────────────────────────────
# Маршрут
# ─────────────────────────────────────────────────────────────

def test_route_is_collected():
    cd = collect_formika_data({"route": {
        "route": "Мурманск - Пятигорск",
        "loading_address": "183052, г.Мурманск, пр.Кольский, д.53",
        "unloading_address": "г. Пятигорск, Бештаугорское шоссе 17",
        "loading_plan_date": "2026-09-24",
        "loading_plan_time_from": "09:00",
        "loading_plan_time_to": "18:00",
    }})

    assert cd.contract["route"] == "Мурманск - Пятигорск"
    assert cd.contract["loading_plan_date"] == "2026-09-24"
    assert cd.contract["loading_plan_time_from"] == "09:00"
    assert cd.contract["loading_plan_time_to"] == "18:00"

    assert cd.loadings == [{
        "name": "",
        "address": "183052, г.Мурманск, пр.Кольский, д.53",
        "date": "2026-09-24",
        "time_window": "09:00-18:00",
    }]
    # ContractData приводит точки к одному виду (name/address/date/time_window),
    # поэтому у выгрузки, кроме адреса, стоят пустые имя, дата и окно времени.
    assert cd.unloadings == [{
        "name": "",
        "address": "г. Пятигорск, Бештаугорское шоссе 17",
        "date": "",
        "time_window": "",
    }]


def test_route_time_window_with_one_boundary():
    cd = collect_formika_data({"route": {
        "loading_address": "г. Мурманск",
        "loading_plan_time_from": "09:00",
    }})

    assert cd.loadings[0]["time_window"] == "09:00"


def test_route_without_loading_address_has_no_loadings():
    cd = collect_formika_data({"route": {
        "route": "Мурманск - Пятигорск",
        "unloading_address": "г. Пятигорск",
    }})

    assert cd.loadings == []
    assert [point["address"] for point in cd.unloadings] == ["г. Пятигорск"]


def test_route_without_unloading_address_has_no_unloadings():
    cd = collect_formika_data({"route": {"loading_address": "г. Мурманск"}})

    assert cd.loadings[0]["address"] == "г. Мурманск"
    assert cd.unloadings == []


def test_route_does_not_duplicate_points_in_contract():
    """Точки живут в loadings/unloadings, а не в contract — как ждёт генератор."""
    cd = collect_formika_data({"route": {"loading_address": "г. Мурманск"}})

    assert "loadings" not in cd.contract
    assert "unloadings" not in cd.contract


def test_resolved_city_comes_from_loading_address():
    """Город определяется по первой погрузке (город в сборе не задаём)."""
    cd = collect_formika_data({"route": {
        "loading_address": "183052, г.Мурманск, пр.Кольский, д.53",
    }})

    assert cd.city == ""
    assert cd.resolved_city() == "Мурманск"


# ─────────────────────────────────────────────────────────────
# Водитель
# ─────────────────────────────────────────────────────────────

def test_driver_fields_are_collected(driver_data):
    cd = collect_formika_data({"driver": driver_data})

    for field in DRIVER_FIELDS:
        expected = str(driver_data[field]).strip()
        assert cd.driver[field] == expected


def test_missing_driver_fields_become_empty_strings():
    cd = collect_formika_data({"driver": {"full_name": "Иванов Иван Иванович"}})

    assert cd.driver["full_name"] == "Иванов Иван Иванович"
    assert cd.driver["passport_series"] == ""
    assert cd.driver["phone"] == ""
    assert set(cd.driver) == set(DRIVER_FIELDS)


# ─────────────────────────────────────────────────────────────
# ТС: тягач и полуприцеп
# ─────────────────────────────────────────────────────────────

def test_tractor_and_trailer_are_collected():
    cd = collect_formika_data({"vehicle": {
        "tractor_brand": "Foton Auman",
        "tractor_plate": "O844XY196",
        "tractor_type": "Грузовой тягач седельный",
        "tractor_color": "Белый",
        "tractor_year": "2023",
        "trailer_brand": "YANGMINDA",
        "trailer_plate": "71ABF18",
        "trailer_color": "Серый",
        "trailer_year": "2020",
    }})

    assert cd.tractor == {
        "brand_model": "Foton Auman",
        "plate_number": "O844XY196",
        "vehicle_type": "Грузовой тягач седельный",
        "color": "Белый",
        "year": "2023",
    }
    assert cd.trailer == {
        "brand_model": "YANGMINDA",
        "plate_number": "71ABF18",
        "color": "Серый",
        "year": "2020",
    }


def test_empty_tractor_and_trailer_fields_are_skipped():
    cd = collect_formika_data({"vehicle": {
        "tractor_brand": "Foton Auman",
        "tractor_plate": "   ",
        "trailer_brand": "",
        "trailer_plate": "",
    }})

    assert cd.tractor == {"brand_model": "Foton Auman"}
    assert cd.trailer == {}


# ─────────────────────────────────────────────────────────────
# Стоимость
# ─────────────────────────────────────────────────────────────

def test_price_fields_are_collected():
    cd = collect_formika_data({"price": {
        "amount": "219 966,00",
        "amount_without_vat": 180300.0,
        "amount_with_vat": 219966.0,
        "vat_rate": "22%",
        "vat_rate_num": 22,
        "payment_days": 10,
        "special_conditions": "  Топливо за счёт заказчика  ",
    }})

    assert cd.contract["price_input"] == "219 966,00"
    assert cd.contract["price_without_vat"] == 180300.0
    assert cd.contract["price_with_vat"] == 219966.0
    assert cd.contract["vat_rate"] == "22%"
    assert cd.contract["vat_rate_num"] == 22
    assert cd.contract["payment_days"] == 10
    assert cd.contract["special_conditions"] == "Топливо за счёт заказчика"


def test_empty_price_fields_are_skipped():
    cd = collect_formika_data({"price": {
        "amount": "",
        "amount_without_vat": None,
        "amount_with_vat": "   ",
        "vat_rate": "",
        "vat_rate_num": None,
        "payment_days": "",
        "special_conditions": "",
    }})

    assert cd.contract == {}


def test_zero_amount_and_zero_vat_rate_are_values():
    """0 — это значение, а не пустое поле: его не выбрасываем."""
    cd = collect_formika_data({"price": {
        "amount_with_vat": 0,
        "vat_rate_num": 0,
        "payment_days": 0,
    }})

    assert cd.contract["price_with_vat"] == 0
    assert cd.contract["vat_rate_num"] == 0
    assert cd.contract["payment_days"] == 0


# ─────────────────────────────────────────────────────────────
# Полный сценарий: все шесть вкладок вместе
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def full_tabs(driver_data):
    """Все шесть вкладок окна Формики, заполненные как в жизни."""
    return {
        "customer": StubTab({"number": "ТЛ-447", "date": "2026-09-23"}),
        "cargo": StubTab({"vehicles": [
            {"brand_model": "JETOUR T2", "vin": VIN_OK},
            {"brand_model": "", "vin": ""},
        ]}),
        "route": StubTab({
            "route": "Мурманск - Пятигорск",
            "loading_address": "183052, г.Мурманск, пр.Кольский, д.53",
            "unloading_address": "г. Пятигорск, Бештаугорское шоссе 17",
            "loading_plan_date": "2026-09-24",
            "loading_plan_time_from": "09:00",
            "loading_plan_time_to": "18:00",
        }),
        "driver": StubTab(driver_data),
        "vehicle": StubTab({
            "tractor_brand": "Foton Auman",
            "tractor_plate": "O844XY196",
            "tractor_type": "Грузовой тягач седельный",
            "trailer_brand": "YANGMINDA",
            "trailer_plate": "71ABF18",
        }),
        "price": StubTab({
            "amount_with_vat": 219966.0,
            "vat_rate": "22%",
            "vat_rate_num": 22,
            "payment_days": 10,
        }),
    }


def test_full_scenario_collects_everything(full_tabs):
    cd = collect_formika_data(full_tabs)

    assert cd.contract["number"] == "ТЛ-447"
    assert cd.contract["date"] == "2026-09-23"
    assert cd.contract["route"] == "Мурманск - Пятигорск"
    assert cd.contract["price_with_vat"] == 219966.0
    assert len(cd.vehicles) == 1
    assert cd.driver["full_name"] == "Иванов Иван Иванович"
    assert cd.tractor["plate_number"] == "O844XY196"
    assert cd.trailer["plate_number"] == "71ABF18"
    assert cd.loadings[0]["address"].endswith("д.53")
    assert cd.unloadings[0]["address"] == "г. Пятигорск, Бештаугорское шоссе 17"


def test_full_scenario_keeps_contract_and_carrier_empty(full_tabs):
    """Стороны в бланке Формики фиксированы — своих значений не выдумываем."""
    cd = collect_formika_data(full_tabs)

    assert cd.customer == {}
    assert cd.carrier == {}


def test_full_scenario_summary_is_safe_for_logs(full_tabs):
    """
    Сводка для логов не падает и не содержит самих данных.

    repr() здесь не проверяется: он печатает номер договора (историческое
    поведение ContractData, core/contract_data.py не трогаем на этом этапе).
    """
    cd = collect_formika_data(full_tabs)
    summary = cd.summary()

    assert "vehicles=1" in summary
    assert "loadings=1" in summary
    assert "ТЛ-447" not in summary
    assert "Иванов" not in summary


def test_full_scenario_passes_formika_validator(full_tabs):
    """Собранных данных достаточно, чтобы валидатор Формики не нашёл ошибок."""
    from core.contracts.formika.validator import FormikaValidator

    report = FormikaValidator().check(collect_formika_data(full_tabs))

    assert report.errors == [], report.errors


def test_full_scenario_generator_gets_expected_placeholders(full_tabs, templates_dir):
    """Генератор Формики читает из собранных данных ровно то, что ждёт."""
    from core.contracts.formika.generator import FormikaGenerator

    generator = FormikaGenerator(templates_dir=str(templates_dir))
    replacements = generator._build_replacements_map(
        collect_formika_data(full_tabs)
    )

    assert replacements["contract_number"] == "ТЛ-447"
    assert replacements["car_1_vin"] == VIN_OK
    assert replacements["car_2_vin"] == ""
    assert replacements["tractor_plate"] == "O844XY196"
    assert replacements["trailer_plate"] == "71ABF18"
    assert replacements["driver_name"] == "Иванов Иван Иванович"
    assert replacements["loading_plan_time_from"] == "09:00"
    assert replacements["loading_plan_time_to"] == "18:00"
