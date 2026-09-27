#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты валидатора договора (core/validator.py) — 11 сценариев.

Логика: ошибки (errors) — то, без чего договор не имеет смысла; замечания
(warnings) — неполные реквизиты, которые не мешают печати.
"""

import pytest

from core.validator import ValidationReport, Validator


def _carrier_ip(**overrides):
    """Перевозчик-ИП: у ИП нет КПП."""
    carrier = {
        "full_name": "Индивидуальный предприниматель Хейгетян Елена Валентиновна",
        "short_name": "ИП Хейгетян Е.В.",
        "inn": "770123456789",
        "kpp": "",
        "ogrn": "315770000100012",
        "entity_type": "ИП",
        "legal_address": "г. Москва, ул. Тестовая, д. 1",
        "bank_account": "40702810000000000001",
        "bik": "044525225",
        "bank_name": "ПАО Сбербанк",
        "director_name": "Хейгетян Елена Валентиновна",
        "director_position": "Индивидуальный предприниматель",
    }
    carrier.update(overrides)
    return carrier


@pytest.fixture
def valid_payload(contract_payload):
    """Полностью корректный набор данных."""
    return contract_payload


# ─────────────────────────────────────────────────────────────
# Сценарий 1: валидный договор
# ─────────────────────────────────────────────────────────────

def test_valid_contract_has_no_errors(valid_payload):
    report = Validator().check(valid_payload)
    assert report.errors == []
    assert report.warnings == []
    assert report.is_clean
    assert report.has_errors is False


# ─────────────────────────────────────────────────────────────
# Сценарий 2: пустой водитель
# ─────────────────────────────────────────────────────────────

def test_empty_driver_is_error(valid_payload):
    payload = dict(valid_payload)
    payload["driver"] = {}
    report = Validator().check(payload)
    assert any("ФИО водителя" in e for e in report.errors)
    assert any("водителя" in w for w in report.warnings)


def test_driver_without_passport_number_is_warning(valid_payload):
    payload = dict(valid_payload)
    payload["driver"] = dict(valid_payload["driver"], passport_number="")
    report = Validator().check(payload)
    assert report.errors == []
    assert any("паспорт" in w.lower() for w in report.warnings)


def test_driver_with_wrong_passport_number_is_error(valid_payload):
    payload = dict(valid_payload)
    payload["driver"] = dict(valid_payload["driver"], passport_number="123")
    report = Validator().check(payload)
    assert any("6 цифр" in e for e in report.errors)


# ─────────────────────────────────────────────────────────────
# Сценарий 3: ИП без КПП
# ─────────────────────────────────────────────────────────────

def test_ip_without_kpp_is_not_error(valid_payload):
    payload = dict(valid_payload)
    payload["carrier"] = _carrier_ip()
    payload["contract"] = dict(valid_payload["contract"], carrier_type="ИП без НДС")
    report = Validator().check(payload)
    assert not any("КПП перевозчика" in e for e in report.errors)
    assert report.errors == []


def test_ooo_without_kpp_is_error(valid_payload):
    """Для ООО КПП остаётся обязательным."""
    payload = dict(valid_payload)
    payload["carrier"] = dict(valid_payload["carrier"], kpp="")
    report = Validator().check(payload)
    assert any("КПП перевозчика" in e for e in report.errors)


def test_ip_entity_type_detected_from_name(valid_payload):
    payload = dict(valid_payload)
    payload["carrier"] = _carrier_ip(kpp="770701001", entity_type="")
    report = Validator().check(payload)
    assert not any("КПП перевозчика" in e for e in report.errors)


# ─────────────────────────────────────────────────────────────
# Сценарий 4: серия паспорта «18 22»
# ─────────────────────────────────────────────────────────────

def test_passport_series_with_space_is_valid(valid_payload):
    payload = dict(valid_payload)
    payload["driver"] = dict(valid_payload["driver"], passport_series="18 22")
    report = Validator().check(payload)
    assert not any("Серия паспорта" in e for e in report.errors)


def test_passport_series_without_space_is_valid(valid_payload):
    payload = dict(valid_payload)
    payload["driver"] = dict(valid_payload["driver"], passport_series="1822")
    report = Validator().check(payload)
    assert not any("Серия паспорта" in e for e in report.errors)


def test_broken_passport_series_is_error(valid_payload):
    payload = dict(valid_payload)
    payload["driver"] = dict(valid_payload["driver"], passport_series="18")
    report = Validator().check(payload)
    assert any("Серия паспорта" in e for e in report.errors)


# ─────────────────────────────────────────────────────────────
# Сценарий 5: пустой заказчик
# ─────────────────────────────────────────────────────────────

def test_empty_customer_is_warning_not_error(valid_payload):
    payload = dict(valid_payload)
    payload["customer"] = {}
    report = Validator().check(payload)

    assert report.errors == [], report.errors
    assert report.has_errors is False
    assert any("заказчика" in w for w in report.warnings)
    assert any("Реквизиты заказчика не заполнены" in w for w in report.warnings)


def test_partially_filled_customer_warns_about_missing_fields(valid_payload):
    payload = dict(valid_payload)
    payload["customer"] = {"full_name": "ООО «Заказчик»", "inn": "7707654321",
                           "kpp": "770701001"}
    report = Validator().check(payload)
    assert report.errors == []
    assert any("заказчика" in w for w in report.warnings)


def test_customer_broken_account_format_is_error(valid_payload):
    """Опечатка в счёте заказчика — ошибка, а не замечание."""
    payload = dict(valid_payload)
    payload["customer"] = dict(valid_payload["customer"], bank_account="123")
    report = Validator().check(payload)
    assert any("расчётный счёт заказчика" in e.lower() for e in report.errors)


# ─────────────────────────────────────────────────────────────
# Сценарии 6-11: остальные проверки договора
# ─────────────────────────────────────────────────────────────

def test_missing_contract_number_and_date_and_route(valid_payload):
    payload = dict(valid_payload)
    payload["contract"] = dict(valid_payload["contract"], number="", date="", route="")
    report = Validator().check(payload)
    assert any("номер договора" in e.lower() for e in report.errors)
    assert any("дата договора" in e.lower() for e in report.errors)
    assert any("маршрут" in e.lower() for e in report.errors)


def test_missing_points(valid_payload):
    payload = dict(valid_payload)
    payload["loadings"] = []
    payload["unloadings"] = []
    report = Validator().check(payload)
    assert any("погрузки" in e.lower() for e in report.errors)
    assert any("выгрузки" in e.lower() for e in report.errors)


def test_zero_price_is_error(valid_payload):
    payload = dict(valid_payload)
    payload["contract"] = dict(valid_payload["contract"], price_without_vat=0)
    report = Validator().check(payload)
    assert any("Стоимость" in e for e in report.errors)


def test_no_vehicles_is_error(valid_payload):
    payload = dict(valid_payload)
    payload["vehicles"] = []
    report = Validator().check(payload)
    assert any("транспортное средство" in e for e in report.errors)


def test_vehicle_without_vin_is_warning(valid_payload):
    payload = dict(valid_payload)
    payload["vehicles"] = [{"vin": "", "brand_model": "JETOUR T2", "plate_number": "А123ВС77"}]
    report = Validator().check(payload)
    assert report.errors == []
    assert any("VIN" in w for w in report.warnings)


def test_empty_tractor_and_trailer_are_warnings(valid_payload):
    payload = dict(valid_payload)
    payload["tractor"] = {}
    payload["trailer"] = {}
    report = Validator().check(payload)
    assert report.errors == []
    assert any("тягача" in w for w in report.warnings)
    assert any("полуприцепа" in w for w in report.warnings)


def test_payment_days_warning(valid_payload):
    payload = dict(valid_payload)
    payload["contract"] = dict(valid_payload["contract"], payment_days=0)
    report = Validator().check(payload)
    assert report.errors == []
    assert any("Срок оплаты" in w for w in report.warnings)


# ─────────────────────────────────────────────────────────────
# Обратная совместимость и форматирование отчёта
# ─────────────────────────────────────────────────────────────

def test_validate_returns_only_errors(valid_payload):
    """Старый API validate() возвращает список строк с ошибками."""
    assert Validator().validate(valid_payload) == []

    payload = dict(valid_payload, driver={})
    errors = Validator().validate(payload)
    assert isinstance(errors, list)
    assert all(isinstance(item, str) for item in errors)
    assert errors


def test_check_accepts_contract_data_object(valid_payload):
    from core.contract_data import ContractData
    report = Validator().check(ContractData.coerce(valid_payload))
    assert report.errors == []


def test_report_format_text_sections():
    report = ValidationReport(errors=["Ошибка 1"], warnings=["Замечание 1"])
    text = report.format_text()
    assert "Ошибки" in text
    assert "Замечания" in text
    assert "Ошибка 1" in text
    assert "Замечание 1" in text


def test_report_truncates_long_lists():
    report = ValidationReport(errors=[f"Ошибка {i}" for i in range(20)])
    text = report.format_text(max_items=5)
    assert "и ещё 15" in text


def test_report_flags():
    clean = ValidationReport()
    assert clean.is_clean and not clean.has_errors

    with_warning = ValidationReport(warnings=["что-то"])
    assert with_warning.is_clean is False
    assert with_warning.has_errors is False

    with_error = ValidationReport(errors=["что-то"])
    assert with_error.has_errors is True
