#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты валидатора договора-заявки «Формика» (ЭТАП 3.1.A.4).

Проверяют обязательный минимум типа: номер/дата/маршрут, адреса погрузки и
выгрузки, хотя бы одна перевозимая машина с VIN у каждой, ФИО и паспорт
водителя, марка и госномер тягача и прицепа, стоимость с НДС. Всё
остальное — замечания, а не ошибки: договор печатать можно.

Все данные синтетические, реальных ПДн нет.
"""

import pytest

from core.contracts.factory import GeneratorFactory
from core.contracts.formika.generator import FormikaGenerator
from core.contracts.formika.validator import FormikaValidator
from core.contracts.registry import ContractTypeRegistry


@pytest.fixture(scope="module", autouse=True)
def _builtin_types_loaded():
    ContractTypeRegistry.load_builtin()


@pytest.fixture
def payload() -> dict:
    """Корректно заполненный договор Формики (2 машины)."""
    return {
        "driver": {
            "full_name": "Иванов Иван Иванович",
            "birth_date": "1980-01-01",
            "passport_series": "18 22",
            "passport_number": "926830",
            "passport_issue_date": "2023-01-30",
            "passport_issuer": "Отделом УФМС России по г. Москве",
            "registration_address": "г. Москва, ул. Тестовая, д. 1",
            "license_series": "99 36",
            "license_number": "123456",
            "phone": "+7 (999) 123-45-67",
        },
        "carrier": {"full_name": "ООО «Ромашка»", "entity_type": "ООО"},
        "customer": {"full_name": "ООО «Заказчик»"},
        "vehicles": [
            {"vin": "EC3TEUMB0T0000001", "brand_model": "МОДЕЛЬ 1",
             "vehicle_type": "Легковой автомобиль"},
            {"vin": "EC3TEUMB0T0000002", "brand_model": "МОДЕЛЬ 2",
             "vehicle_type": "Легковой автомобиль"},
        ],
        "tractor": {"brand_model": "Foton Auman", "plate_number": "O844XY196",
                    "vehicle_type": "Грузовой тягач седельный"},
        "trailer": {"brand_model": "YANGMINDA", "plate_number": "71ABF18"},
        "contract": {
            "number": "ФМ-2026-1",
            "date": "2026-07-24",
            "route": "г. Воронеж - г. Москва",
            "vat_rate": "22%",
            "vat_rate_num": 22,
            "price_without_vat": 180300.0,
            "price_with_vat": 219966.0,
            "loading_plan_date": "2026-07-27",
        },
        "loadings": [{"address": "г. Воронеж, ул. Остужева 52Б",
                      "date": "2026-07-27", "time_window": "09:00-15:00"}],
        "unloadings": [{"address": "г. Москва, Перерва 19 стр 3",
                        "date": "2026-07-30", "time_window": ""}],
    }


@pytest.fixture
def validator() -> FormikaValidator:
    return FormikaValidator()


def _errors(report) -> str:
    return "\n".join(report.errors)


# ─────────────────────────────────────────────────────────────
# Регистрация и базовое поведение
# ─────────────────────────────────────────────────────────────

def test_validator_is_registered_for_formika():
    spec = ContractTypeRegistry.get("formika", strict=True)
    assert spec.validator_class is FormikaValidator
    assert isinstance(
        GeneratorFactory.get_validator("formika", strict=True), FormikaValidator
    )
    assert FormikaGenerator.VALIDATOR_CLASS is FormikaValidator


def test_valid_payload_has_no_errors(validator, payload):
    report = validator.check(payload)
    assert report.errors == [], _errors(report)


def test_generator_validate_hook_uses_formika_validator(payload, templates_dir):
    generator = FormikaGenerator(templates_dir=str(templates_dir))
    report = generator.validate(payload)
    assert report.errors == []


def test_validate_returns_only_errors(validator, payload):
    payload["contract"].pop("number")
    errors = validator.validate(payload)
    assert isinstance(errors, list)
    assert any("номер" in error for error in errors)


def test_report_text_has_russian_blocks(validator, payload):
    payload["contract"].pop("number")
    text = validator.check(payload).format_text()
    assert "Ошибки — нужно исправить:" in text


def test_empty_data_reports_errors_without_crashing(validator):
    report = validator.check({"contract": {}})
    assert report.has_errors
    assert any("машину" in error for error in report.errors)


# ─────────────────────────────────────────────────────────────
# Договор: номер, дата, маршрут, адреса
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field, expected", [
    ("number", "номер"),
    ("date", "дата"),
    ("route", "маршрут"),
])
def test_contract_header_fields_are_required(validator, payload, field, expected):
    payload["contract"].pop(field)
    report = validator.check(payload)
    assert any(expected in error for error in report.errors), _errors(report)


def test_loading_address_is_required(validator, payload):
    payload["loadings"] = []
    report = validator.check(payload)
    assert "Укажите пункт погрузки" in report.errors


def test_loading_address_can_come_from_legacy_field(validator, payload):
    payload["loadings"] = []
    payload["contract"]["loading_address"] = "г. Воронеж, ул. Остужева 52Б"
    report = validator.check(payload)
    assert "Укажите пункт погрузки" not in report.errors


def test_unloading_address_is_required(validator, payload):
    payload["unloadings"] = []
    report = validator.check(payload)
    assert "Укажите пункт выгрузки" in report.errors


def test_missing_plan_date_and_time_are_warnings(validator, payload):
    payload["contract"].pop("loading_plan_date")
    payload["loadings"][0]["date"] = ""
    payload["loadings"][0]["time_window"] = ""

    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("Не указана плановая дата погрузки" in warning
               for warning in report.warnings)
    assert any("время погрузки" in warning for warning in report.warnings)


# ─────────────────────────────────────────────────────────────
# Груз: минимум одна машина, VIN обязателен
# ─────────────────────────────────────────────────────────────

def test_at_least_one_cargo_vehicle_required(validator, payload):
    payload["vehicles"] = []
    report = validator.check(payload)
    assert "Добавьте хотя бы одну перевозимую машину" in report.errors


def test_tractor_and_trailer_are_not_cargo(validator, payload):
    """В vehicles только тягач и прицеп — груза нет."""
    payload["vehicles"] = [
        {"brand_model": "Foton Auman", "plate_number": "O844XY196",
         "vehicle_type": "Тягач"},
        {"brand_model": "YANGMINDA", "plate_number": "71ABF18",
         "vehicle_type": "Полуприцеп"},
    ]
    report = validator.check(payload)
    assert "Добавьте хотя бы одну перевозимую машину" in report.errors


def test_every_vehicle_needs_vin(validator, payload):
    payload["vehicles"].append(
        {"brand_model": "МОДЕЛЬ 3", "vin": "", "vehicle_type": "Легковой автомобиль"}
    )
    report = validator.check(payload)
    assert any("Машина №3: не заполнен VIN" in error for error in report.errors)


def test_nonstandard_vin_is_warning_not_error(validator, payload):
    payload["vehicles"][0]["vin"] = "EC3TEUMB0T0000"
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("не похож на стандартный" in warning for warning in report.warnings)


def test_vin_is_checked_case_insensitively(validator, payload):
    payload["vehicles"][0]["vin"] = "ec3teumb0t0000001"
    report = validator.check(payload)
    assert report.errors == [], _errors(report)


def test_missing_brand_is_warning(validator, payload):
    payload["vehicles"][0]["brand_model"] = ""
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("не заполнена марка/модель" in warning for warning in report.warnings)


def test_more_than_twelve_cars_is_warning(validator, payload):
    payload["vehicles"] = [
        {"vin": f"EC3TEUMB0T000{number:04d}", "brand_model": f"МОДЕЛЬ {number}",
         "vehicle_type": "Легковой автомобиль"}
        for number in range(1, 14)
    ]
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("в бланк помещается 12" in warning for warning in report.warnings)


def test_twelve_cars_is_not_a_warning(validator, payload):
    payload["vehicles"] = [
        {"vin": f"EC3TEUMB0T000{number:04d}", "brand_model": f"МОДЕЛЬ {number}",
         "vehicle_type": "Легковой автомобиль"}
        for number in range(1, 13)
    ]
    report = validator.check(payload)
    assert not any("в бланк помещается" in warning for warning in report.warnings)


# ─────────────────────────────────────────────────────────────
# Водитель
# ─────────────────────────────────────────────────────────────

def test_driver_full_name_is_required(validator, payload):
    payload["driver"]["full_name"] = ""
    report = validator.check(payload)
    assert "Не заполнено ФИО водителя" in report.errors


def test_passport_series_is_required(validator, payload):
    payload["driver"]["passport_series"] = ""
    report = validator.check(payload)
    assert "Не заполнена серия паспорта водителя" in report.errors


def test_passport_number_is_required(validator, payload):
    payload["driver"]["passport_number"] = ""
    report = validator.check(payload)
    assert "Не заполнен номер паспорта водителя" in report.errors


@pytest.mark.parametrize("field, value, expected", [
    ("passport_series", "182", "должна содержать 4 цифры"),
    ("passport_number", "92683", "должен содержать 6 цифр"),
])
def test_passport_parts_format(validator, payload, field, value, expected):
    payload["driver"][field] = value
    report = validator.check(payload)
    assert any(expected in error for error in report.errors)


def test_optional_driver_fields_are_warnings(validator, payload):
    payload["driver"]["license_series"] = ""
    payload["driver"]["license_number"] = ""
    payload["driver"]["phone"] = ""

    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("Не заполнены данные водителя" in warning for warning in report.warnings)


# ─────────────────────────────────────────────────────────────
# Тягач и прицеп
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field, expected", [
    ("brand_model", "Не заполнена марка тягача"),
    ("plate_number", "Не заполнен госномер тягача"),
])
def test_tractor_fields_are_required(validator, payload, field, expected):
    payload["tractor"][field] = ""
    report = validator.check(payload)
    assert expected in report.errors


@pytest.mark.parametrize("field, expected", [
    ("brand_model", "Не заполнена марка прицепа"),
    ("plate_number", "Не заполнен госномер прицепа"),
])
def test_trailer_fields_are_required(validator, payload, field, expected):
    payload["trailer"][field] = ""
    report = validator.check(payload)
    assert expected in report.errors


def test_missing_tractor_type_is_warning(validator, payload):
    payload["tractor"]["vehicle_type"] = ""
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("тип ТС тягача" in warning for warning in report.warnings)


# ─────────────────────────────────────────────────────────────
# Стоимость
# ─────────────────────────────────────────────────────────────

def test_cost_is_required(validator, payload):
    for key in ("price_with_vat", "price_without_vat", "price_input"):
        payload["contract"].pop(key, None)

    report = validator.check(payload)
    assert "Стоимость перевозки должна быть больше нуля" in report.errors


def test_cost_can_come_from_price_without_vat(validator, payload):
    payload["contract"].pop("price_with_vat")
    report = validator.check(payload)
    assert report.errors == [], _errors(report)


def test_cost_can_come_from_recognized_price_input(validator, payload):
    """Распознанная сумма приходит в price_input (она уже с НДС)."""
    payload["contract"].pop("price_with_vat")
    payload["contract"].pop("price_without_vat")
    payload["contract"]["price_input"] = "219 966,00"

    report = validator.check(payload)
    assert report.errors == [], _errors(report)


def test_cost_as_text_with_spaces_is_accepted(validator, payload):
    payload["contract"]["price_with_vat"] = "219\u00a0966,00"
    report = validator.check(payload)
    assert report.errors == [], _errors(report)


def test_missing_vat_rate_is_warning(validator, payload):
    payload["contract"].pop("vat_rate")
    payload["contract"].pop("vat_rate_num")
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("ставка НДС" in warning for warning in report.warnings)


def test_generator_payload_passes_its_own_validator(templates_dir):
    """Данные, которые печатает генератор, проходят его же валидацию."""
    from tools.make_golden import FORMIKA_SCENARIOS

    _, golden_payload = FORMIKA_SCENARIOS["formika_sample"]
    report = FormikaValidator().check(golden_payload)

    assert report.errors == [], _errors(report)
