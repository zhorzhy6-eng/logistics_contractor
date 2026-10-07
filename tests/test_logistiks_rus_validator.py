#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты валидатора заявки «Логистикс Рус» (ЭТАП 3.1.C.A.5).

Проверяют обязательный минимум типа: номер и дата заявки, наименование
заказчика, хотя бы один грузоотправитель и грузополучатель с адресом,
хотя бы одна перевозимая машина с VIN, марка и госномер тягача и прицепа,
ФИО водителя, стоимость. Отдельная группа — раздел 5 «Стоимость»: у ООО
считается НДС по ставке, у ИП стоимость всегда без НДС, и ставка НДС там не
применяется.

Всё остальное (нестандартный VIN, машин больше 12, половина блока точки,
пустое краткое наименование заказчика, расхождение с cargo_count) —
замечания, а не ошибки: заявку печатать можно.

Данные, которые печатает генератор, проходят этот же валидатор без ошибок
(см. tests/test_logistiks_rus_generator.py::_payload) — проверка ниже.

Все данные синтетические, реальных ПДн нет.
"""

import inspect
import re
from pathlib import Path

import pytest

from core.contracts.factory import GeneratorFactory
from core.contracts.logistiks_rus.generator import LogistiksRusGenerator
from core.contracts.logistiks_rus.validator import LogistiksRusValidator
from core.contracts.registry import ContractTypeRegistry

#: Поля стоимости заявки — те же, что проверяет валидатор (см. PRICE_FIELDS).
PRICE_FIELDS = (
    "price_without_vat",
    "price_input",
    "sum_wo_vat",
    "sum_vat",
    "sum_total",
)


@pytest.fixture(scope="module", autouse=True)
def _builtin_types_loaded():
    """Регистрация типов: без неё фабрика не знает про logistiks_rus."""
    ContractTypeRegistry.load_builtin()


def _vehicle(number: int) -> dict:
    """Синтетическая перевозимая машина (VIN — 17 символов ISO 3779)."""
    return {
        "vin": f"TESTV1N00000000{number:02d}",
        "brand_model": f"МОДЕЛЬ {number}",
        "vehicle_type": "Легковой автомобиль",
    }


def _point(kind: str, number: int, name=None, address=None) -> dict:
    """
    Точка маршрута: грузоотправитель (kind="shipper") или грузополучатель.

    name/address можно подменить (в том числе пустой строкой) — так
    проверяются частично заполненные блоки.
    """
    labels = {"shipper": "Грузоотправитель", "consignee": "Грузополучатель"}
    return {
        "name": f"ООО «{labels[kind]} {number}»" if name is None else name,
        "address": f"Адрес {kind} {number}" if address is None else address,
        "date": "2026-09-26" if kind == "shipper" else "2026-10-01",
        "time_window": "08:00-20:00",
    }


def _payload(carrier_type: str = "ООО (с НДС)", price=221099.18) -> dict:
    """
    Корректно заполненная заявка (3 машины, 2 погрузки, 2 выгрузки).

    Точки маршрута вложены в contract["loadings"] / contract["unloadings"]:
    только этот путь сохраняет название грузоотправителя (см. докстринг
    core/contracts/logistiks_rus/generator.py).
    """
    return {
        "driver": {"full_name": "Иванов Иван Иванович"},
        "customer": {
            "full_name": "ООО «Заказчик Тест»",
            "short_name": "ООО «Заказчик Тест»",
        },
        "vehicles": [_vehicle(number) for number in range(1, 4)] + [
            # Тягач и полуприцеп лежат в справочнике машин, но грузом не являются.
            {"brand_model": "Тягач-Модель", "plate_number": "А001АА01",
             "vehicle_type": "Тягач"},
            {"brand_model": "Прицеп-Модель", "plate_number": "Б002ББ02",
             "vehicle_type": "Полуприцеп"},
        ],
        "tractor": {"brand_model": "Тягач-Модель", "plate_number": "А001АА01"},
        "trailer": {"brand_model": "Прицеп-Модель", "plate_number": "Б002ББ02"},
        "contract": {
            "number": "ЛР-2026-1",
            "date": "2026-09-24",
            "carrier_type": carrier_type,
            "vat_rate": "22%",
            "vat_rate_num": 22,
            "price_without_vat": price,
            "loadings": [_point("shipper", n) for n in (1, 2)],
            "unloadings": [_point("consignee", n) for n in (1, 2)],
        },
    }


@pytest.fixture
def payload() -> dict:
    """Корректно заполненная заявка ООО (3 машины)."""
    return _payload()


@pytest.fixture
def ip_payload() -> dict:
    """Корректно заполненная заявка ИП: одна сумма «Без НДС», ставка не применяется."""
    data = _payload(carrier_type="ИП без НДС")
    contract = data["contract"]
    contract.pop("vat_rate")
    contract.pop("vat_rate_num")
    contract.pop("price_without_vat")
    contract["sum_total"] = 269741.0
    return data


@pytest.fixture
def validator() -> LogistiksRusValidator:
    return LogistiksRusValidator()


def _errors(report) -> str:
    return "\n".join(report.errors)


# ─────────────────────────────────────────────────────────────
# Регистрация и базовое поведение
# ─────────────────────────────────────────────────────────────

def test_validator_is_registered_for_logistiks_rus():
    spec = ContractTypeRegistry.get("logistiks_rus", strict=True)
    assert spec.validator_class is LogistiksRusValidator
    assert isinstance(
        GeneratorFactory.get_validator("logistiks_rus", strict=True),
        LogistiksRusValidator,
    )
    assert LogistiksRusGenerator.VALIDATOR_CLASS is LogistiksRusValidator


def test_contract_type_is_logistiks_rus():
    assert LogistiksRusValidator.CONTRACT_TYPE == "logistiks_rus"


def test_price_fields_match_test_constant():
    """Список полей стоимости в тестах совпадает с тем, что проверяет валидатор."""
    assert set(PRICE_FIELDS) == set(LogistiksRusValidator.PRICE_FIELDS)


def test_empty_data_reports_all_required_errors(validator):
    """Пустой ContractData: на месте все обязательные ошибки."""
    report = validator.check({})

    expected = [
        "Не заполнен номер заявки",
        "Не заполнена дата заявки",
        "Не заполнено наименование заказчика",
        "Укажите хотя бы одного грузоотправителя с адресом",
        "Укажите хотя бы одного грузополучателя с адресом",
        "Добавьте хотя бы одну перевозимую машину",
        "Не заполнена марка тягача",
        "Не заполнен госномер тягача",
        "Не заполнена марка прицепа",
        "Не заполнен госномер прицепа",
        "Не заполнено ФИО водителя",
        "Стоимость услуг должна быть больше нуля",
    ]
    missing = [message for message in expected if message not in report.errors]
    assert missing == [], f"нет ошибок: {missing}\n{_errors(report)}"


def test_empty_data_warns_instead_of_failing(validator):
    """Пустой ContractData: предупреждения тоже есть, но ошибок не больше."""
    report = validator.check({})
    assert report.has_errors
    assert any("краткое наименование заказчика" in w for w in report.warnings)


def test_filled_ooo_payload_has_no_errors(validator, payload):
    report = validator.check(payload)
    assert report.errors == [], _errors(report)
    assert report.is_clean, report.warnings


def test_filled_ip_payload_has_no_errors(validator, ip_payload):
    report = validator.check(ip_payload)
    assert report.errors == [], _errors(report)
    assert report.is_clean, report.warnings


def test_validate_returns_only_errors(validator, payload):
    payload["contract"].pop("number")
    errors = validator.validate(payload)

    assert isinstance(errors, list)
    assert "Не заполнен номер заявки" in errors


def test_report_text_has_russian_blocks(validator, payload):
    payload["contract"].pop("number")
    text = validator.check(payload).format_text()

    assert "Ошибки — нужно исправить:" in text


def test_validator_does_not_use_core_validator(validator, payload):
    """
    Валидатор типа работает по своему набору полей.

    core.validator.Validator требует реквизитов сторон (ИНН/КПП/ОГРН,
    банковские счета) — в заявке их нет. Если бы валидатор делегировал ему
    проверку, валидные данные дали бы ошибки, а в модуле появился бы импорт
    или вызов чужого валидатора.
    """
    source = Path(inspect.getsourcefile(LogistiksRusValidator)).read_text(
        encoding="utf-8"
    )

    assert validator.check(payload).errors == []
    assert re.search(r"(?<![A-Za-z_])Validator\(", source) is None, (
        "валидатор Логистикс Рус не должен вызывать core.validator.Validator"
    )
    assert "import Validator" not in source
    assert "from core.validator import Validator" not in source


def test_generator_payload_passes_validator(payload):
    """Данные, которые печатает генератор, проходят его же валидацию."""
    report = LogistiksRusValidator().check(payload)
    assert report.errors == [], _errors(report)


def test_generator_own_payload_passes_validator():
    """Тот же тест на данных из тестов генератора (единый источник правды)."""
    generator_tests = pytest.importorskip("test_logistiks_rus_generator")
    generator_payload = generator_tests._payload()

    report = LogistiksRusValidator().check(generator_payload)
    assert report.errors == [], _errors(report)


def test_generator_validate_hook_uses_logistiks_validator(payload, templates_dir):
    generator = LogistiksRusGenerator(templates_dir=str(templates_dir))
    report = generator.validate(payload)

    assert report.errors == [], _errors(report)


# ─────────────────────────────────────────────────────────────
# Заявка и заказчик
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field, expected", [
    ("number", "Не заполнен номер заявки"),
    ("date", "Не заполнена дата заявки"),
])
def test_contract_header_fields_are_required(validator, payload, field, expected):
    payload["contract"].pop(field)
    report = validator.check(payload)

    assert expected in report.errors, _errors(report)


def test_contract_number_of_spaces_is_empty(validator, payload):
    payload["contract"]["number"] = "   "
    report = validator.check(payload)

    assert "Не заполнен номер заявки" in report.errors


def test_customer_name_is_required(validator, payload):
    payload["customer"]["full_name"] = ""
    report = validator.check(payload)

    assert "Не заполнено наименование заказчика" in report.errors


def test_missing_customer_short_name_is_warning(validator, payload):
    payload["customer"].pop("short_name")
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("краткое наименование заказчика" in w for w in report.warnings)


# ─────────────────────────────────────────────────────────────
# Грузоотправители и грузополучатели
# ─────────────────────────────────────────────────────────────

def test_no_shippers_is_error(validator, payload):
    payload["contract"]["loadings"] = []
    report = validator.check(payload)

    assert "Укажите хотя бы одного грузоотправителя с адресом" in report.errors


def test_one_shipper_with_address_is_enough(validator, payload):
    payload["contract"]["loadings"] = [_point("shipper", 1)]
    report = validator.check(payload)

    assert "Укажите хотя бы одного грузоотправителя с адресом" not in report.errors


def test_shipper_with_name_only_is_error_and_warning(validator, payload):
    """
    Единственный грузоотправитель без адреса: адреса нет ни у одной точки —
    это ошибка, плюс замечание о незаполненном блоке.
    """
    payload["contract"]["loadings"] = [_point("shipper", 1, address="")]
    report = validator.check(payload)

    assert "Укажите хотя бы одного грузоотправителя с адресом" in report.errors
    assert "1-й грузоотправителя: не указан адрес" in report.warnings


def test_shipper_with_name_only_is_warning_when_another_has_address(validator, payload):
    """Второй грузоотправитель с адресом закрывает ошибку, замечание остаётся."""
    payload["contract"]["loadings"] = [
        _point("shipper", 1, address=""),
        _point("shipper", 2),
    ]
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert "1-й грузоотправителя: не указан адрес" in report.warnings


def test_shipper_with_address_only_is_warning(validator, payload):
    """
    Единственный грузоотправитель с адресом, но без имени — замечание есть.

    Формулировка — общая на раздел (ШАГ FIX-2.5): наименования нет ни у одной
    точки, значит справочник салонов, скорее всего, просто не использовали.
    """
    payload["contract"]["loadings"] = [_point("shipper", 1, name="")]
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert "Грузоотправители: не указаны наименования — " \
           "выберите точки из справочника салонов" in report.warnings


def test_shipper_name_missing_among_named_is_warning(validator, payload):
    """Имя есть у части точек — замечание адресное, по номеру точки."""
    payload["contract"]["loadings"] = [
        _point("shipper", 1),
        _point("shipper", 2, name=""),
    ]
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert "2-й грузоотправителя: не указано наименование" in report.warnings


def test_empty_shipper_rows_are_ignored(validator, payload):
    """Строки-пустышки из таблицы не считаются грузоотправителями."""
    payload["contract"]["loadings"] = [
        {"name": "", "address": "", "date": "", "time_window": ""},
        {"name": "  ", "address": "  "},
    ]
    report = validator.check(payload)

    assert "Укажите хотя бы одного грузоотправителя с адресом" in report.errors


def test_shippers_before_name_bearing_points_count(validator, payload):
    """Первый грузоотправитель без адреса, второй с адресом — ошибки нет."""
    payload["contract"]["loadings"] = [
        _point("shipper", 1, address=""),
        _point("shipper", 2),
    ]
    report = validator.check(payload)

    assert "Укажите хотя бы одного грузоотправителя с адресом" not in report.errors
    assert any("1-й грузоотправителя: не указан адрес" in w for w in report.warnings)


def test_no_consignees_is_error(validator, payload):
    payload["contract"]["unloadings"] = []
    report = validator.check(payload)

    assert "Укажите хотя бы одного грузополучателя с адресом" in report.errors


def test_one_consignee_with_address_is_enough(validator, payload):
    payload["contract"]["unloadings"] = [_point("consignee", 1)]
    report = validator.check(payload)

    assert "Укажите хотя бы одного грузополучателя с адресом" not in report.errors


def test_consignee_with_name_only_is_error_and_warning(validator, payload):
    """Единственный грузополучатель без адреса: ошибка + замечание."""
    payload["contract"]["unloadings"] = [_point("consignee", 1, address="")]
    report = validator.check(payload)

    assert "Укажите хотя бы одного грузополучателя с адресом" in report.errors
    assert "1-й грузополучателя: не указан адрес" in report.warnings


def test_consignee_name_only_is_warning_when_another_has_address(validator, payload):
    payload["contract"]["unloadings"] = [
        _point("consignee", 1, address=""),
        _point("consignee", 2),
    ]
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert "1-й грузополучателя: не указан адрес" in report.warnings


def test_points_can_come_from_top_level(validator, payload):
    """Точки верхнего уровня (loadings/unloadings) тоже принимаются."""
    contract = payload["contract"]
    payload["loadings"] = contract.pop("loadings")
    payload["unloadings"] = contract.pop("unloadings")
    report = validator.check(payload)

    assert report.errors == [], _errors(report)


def test_points_can_come_from_recognized_shippers(validator, payload):
    """
    Распознанный документ приходит с массивами shippers / consignees
    (см. схему ответа в core/prompts/logistiks_rus.py).
    """
    contract = payload["contract"]
    contract["shippers"] = contract.pop("loadings")
    contract["consignees"] = contract.pop("unloadings")
    report = validator.check(payload)

    assert report.errors == [], _errors(report)


def test_points_without_names_are_not_scolded(validator, payload):
    """
    Точки без наименования — адрес есть, наименование неизвестно.

    С ШАГА FIX-2.5 приведённая точка несёт ключ name всегда (пустой строкой,
    см. core.contract_data._as_point_list), поэтому валидатор о незаполненном
    наименовании СООБЩАЕТ: пользователь увидит, что салон из справочника не
    подтянулся, и сможет выбрать его вручную.

    Если без наименования ВСЕ точки раздела, замечание печатается одно на
    раздел, а не по строке на точку (см. _check_points): справочником
    пользуются не все, и повторять одно и то же десять раз незачем.
    """
    payload["contract"]["loadings"] = [
        {"address": "Адрес погрузки 1", "date": "2026-09-26", "time_window": ""}
    ]
    payload["contract"]["unloadings"] = [{"address": "Адрес выгрузки 1"}]
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert report.warnings == [
        "Грузоотправители: не указаны наименования — "
        "выберите точки из справочника салонов",
        "Грузополучатели: не указаны наименования — "
        "выберите точки из справочника салонов",
    ]


def test_one_unnamed_point_among_named_ones_is_reported(validator, payload):
    """
    Часть точек без наименования — замечание по КАЖДОЙ такой точке.

    Здесь пустое наименование — сигнал, что салон не подтянулся, а не
    «справочником не пользуются»: остальные точки имя имеют.
    """
    payload["contract"]["loadings"] = [
        {"name": "ООО «Салон 1»", "address": "Адрес погрузки 1"},
        {"name": "", "address": "Адрес погрузки 2"},
        {"address": "Адрес погрузки 3"},
    ]
    payload["contract"]["unloadings"] = [
        {"name": "ООО «Салон 2»", "address": "Адрес выгрузки 1"},
    ]
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert report.warnings == [
        "2-й грузоотправителя: не указано наименование",
        "3-й грузоотправителя: не указано наименование",
    ]


def test_single_unnamed_point_gets_general_warning(validator, payload):
    """Одна точка без имени в разделе — тоже одно общее замечание, не «1-й»."""
    payload["contract"]["loadings"] = [{"address": "Единственная погрузка"}]
    payload["contract"]["unloadings"] = [
        {"name": "ООО «Салон»", "address": "Адрес выгрузки 1"},
    ]
    report = validator.check(payload)

    assert report.warnings == [
        "Грузоотправители: не указаны наименования — "
        "выберите точки из справочника салонов",
    ]


def test_points_with_names_are_not_scolded(validator, payload):
    """Все точки с наименованиями — замечаний о них нет."""
    payload["contract"]["loadings"] = [
        {"name": "ООО «Салон 1»", "address": "Адрес погрузки 1"},
    ]
    payload["contract"]["unloadings"] = [
        {"name": "ООО «Салон 2»", "address": "Адрес выгрузки 1"},
    ]
    report = validator.check(payload)

    assert not any("наименован" in w for w in report.warnings)


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
        {"brand_model": "Тягач-Модель", "plate_number": "А001АА01",
         "vehicle_type": "Тягач"},
        {"brand_model": "Прицеп-Модель", "plate_number": "Б002ББ02",
         "vehicle_type": "Полуприцеп"},
    ]
    report = validator.check(payload)

    assert "Добавьте хотя бы одну перевозимую машину" in report.errors


def test_every_vehicle_needs_vin(validator, payload):
    payload["vehicles"].append(
        {"brand_model": "МОДЕЛЬ 4", "vin": "", "vehicle_type": "Легковой автомобиль"}
    )
    report = validator.check(payload)

    assert any("Машина №4: не заполнен VIN" in error for error in report.errors)


def test_nonstandard_vin_is_warning_not_error(validator, payload):
    payload["vehicles"][0]["vin"] = "EC3TEUMB0T0000"
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("не похож на стандартный" in warning for warning in report.warnings)


def test_vin_with_forbidden_letters_is_warning(validator, payload):
    """Буквы I, O, Q в VIN недопустимы (ISO 3779)."""
    payload["vehicles"][0]["vin"] = "EC3TEUMB0T0000OO1"
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("не похож на стандартный" in warning for warning in report.warnings)


def test_vin_is_checked_case_insensitively(validator, payload):
    payload["vehicles"][0]["vin"] = "testvin00000000001"
    report = validator.check(payload)

    assert report.errors == [], _errors(report)


def test_more_than_twelve_cars_is_warning(validator, payload):
    payload["vehicles"] = [_vehicle(number) for number in range(1, 16)]
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("в бланк помещается 12" in warning for warning in report.warnings)


def test_twelve_cars_is_not_a_warning(validator, payload):
    payload["vehicles"] = [_vehicle(number) for number in range(1, 13)]
    report = validator.check(payload)

    assert not any("в бланк помещается" in warning for warning in report.warnings)


def test_cargo_count_mismatch_is_warning(validator, payload):
    payload["contract"]["cargo_count"] = 5
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("cargo_count" in warning for warning in report.warnings)


def test_cargo_count_match_is_not_a_warning(validator, payload):
    payload["contract"]["cargo_count"] = 3
    report = validator.check(payload)

    assert not any("cargo_count" in warning for warning in report.warnings)


def test_cargo_count_as_text_is_compared(validator, payload):
    """Распознавание может отдать количество строкой."""
    payload["contract"]["cargo_count"] = "3"
    report = validator.check(payload)

    assert not any("cargo_count" in warning for warning in report.warnings)


def test_cargo_count_from_recognized_sum_is_warning(validator, payload):
    """«4 шт.» вместо 3 машин — замечание, а не ошибка."""
    payload["contract"]["cargo_count"] = "4 шт."
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("cargo_count" in warning for warning in report.warnings)


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

    assert expected in report.errors, _errors(report)


@pytest.mark.parametrize("field, expected", [
    ("brand_model", "Не заполнена марка прицепа"),
    ("plate_number", "Не заполнен госномер прицепа"),
])
def test_trailer_fields_are_required(validator, payload, field, expected):
    payload["trailer"][field] = ""
    report = validator.check(payload)

    assert expected in report.errors, _errors(report)


# ─────────────────────────────────────────────────────────────
# Водитель: в заявке печатается только ФИО
# ─────────────────────────────────────────────────────────────

def test_driver_full_name_is_required(validator, payload):
    payload["driver"]["full_name"] = ""
    report = validator.check(payload)

    assert "Не заполнено ФИО водителя" in report.errors


def test_driver_without_phone_and_passport_is_not_a_warning(validator, payload):
    """Паспорт, ВУ и телефон в бланке не печатаются — их отсутствие молчит."""
    payload["driver"] = {"full_name": "Иванов Иван Иванович"}
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert not any("водител" in warning for warning in report.warnings)


# ─────────────────────────────────────────────────────────────
# Стоимость: ООО
# ─────────────────────────────────────────────────────────────

def test_ooo_cost_with_vat_rate_has_no_errors(validator, payload):
    """ООО: стоимость есть, ставка НДС 22 — ошибок и замечаний нет."""
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert report.is_clean, report.warnings


def test_cost_is_required(validator, payload):
    for key in PRICE_FIELDS:
        payload["contract"].pop(key, None)
    report = validator.check(payload)

    assert "Стоимость услуг должна быть больше нуля" in report.errors


@pytest.mark.parametrize("field, value", [
    ("price_without_vat", 221099.18),
    ("price_input", "221 099,18"),
    ("sum_wo_vat", 221099.18),
    ("sum_total", 269741.0),
])
def test_cost_can_come_from_any_price_field(validator, payload, field, value):
    for key in PRICE_FIELDS:
        payload["contract"].pop(key, None)
    payload["contract"][field] = value
    report = validator.check(payload)

    assert "Стоимость услуг должна быть больше нуля" not in report.errors


def test_zero_cost_is_error(validator, payload):
    payload["contract"]["price_without_vat"] = 0
    report = validator.check(payload)

    assert "Стоимость услуг должна быть больше нуля" in report.errors


def test_missing_vat_rate_is_warning(validator, payload):
    payload["contract"].pop("vat_rate")
    payload["contract"].pop("vat_rate_num")
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert "Не указана ставка НДС" in report.warnings


def test_vat_rate_can_come_as_text(validator, payload):
    payload["contract"].pop("vat_rate_num")
    payload["contract"]["vat_rate"] = "22%"
    report = validator.check(payload)

    assert "Не указана ставка НДС" not in report.warnings


def test_explicit_zero_vat_rate_is_warning(validator, payload):
    payload["contract"]["vat_rate_num"] = 0
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert "Ставка НДС = 0%, проверьте" in report.warnings


# ─────────────────────────────────────────────────────────────
# Стоимость: ИП
# ─────────────────────────────────────────────────────────────

def test_ip_cost_from_sum_total_has_no_errors(validator, ip_payload):
    """ИП: одна сумма «Без НДС» в sum_total, ставка не задана."""
    report = validator.check(ip_payload)

    assert report.errors == [], _errors(report)
    assert report.is_clean, report.warnings


def test_ip_with_vat_rate_num_is_warning(validator, ip_payload):
    ip_payload["contract"]["vat_rate_num"] = 22
    report = validator.check(ip_payload)

    assert report.errors == [], _errors(report)
    assert "У ИП ставка НДС не применяется" in report.warnings


def test_ip_with_zero_vat_rate_is_not_a_warning(validator, ip_payload):
    """У ИП нулевая ставка — норма, замечания нет."""
    ip_payload["contract"]["vat_rate_num"] = 0
    report = validator.check(ip_payload)

    assert not any("ставка НДС" in warning for warning in report.warnings)


def test_ip_without_price_is_error(validator, ip_payload):
    ip_payload["contract"].pop("sum_total")
    report = validator.check(ip_payload)

    assert "Стоимость услуг должна быть больше нуля" in report.errors


def test_ip_cost_can_come_from_price_without_vat(validator, ip_payload):
    """Данные вкладки «Стоимость»: сумма без НДС — единственная сумма ИП."""
    ip_payload["contract"].pop("sum_total")
    ip_payload["contract"]["price_without_vat"] = 135833.0
    report = validator.check(ip_payload)

    assert "Стоимость услуг должна быть больше нуля" not in report.errors


# ─────────────────────────────────────────────────────────────
# Устойчивость: тип экспедитора неизвестен
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("carrier_type", ["", "   ", "ООО (с НДС)", "ООО", "Прочее"])
def test_empty_carrier_type_is_treated_as_ooo(validator, payload, carrier_type):
    """Пустой и незнакомый тип экспедитора трактуется как ООО (вариант бланка)."""
    payload["contract"]["carrier_type"] = carrier_type
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert "У ИП ставка НДС не применяется" not in report.warnings


def test_empty_carrier_type_without_vat_rate_warns_like_ooo(validator, payload):
    payload["contract"]["carrier_type"] = ""
    payload["contract"].pop("vat_rate")
    payload["contract"].pop("vat_rate_num")
    report = validator.check(payload)

    assert "Не указана ставка НДС" in report.warnings


def test_carrier_type_can_come_from_carrier_block(validator, payload):
    """Историческое место типа перевозчика — блок carrier."""
    payload["contract"].pop("carrier_type")
    payload["carrier"] = {"carrier_type": "ИП без НДС", "full_name": "ИП Тестов Т.Т."}
    report = validator.check(payload)

    assert "У ИП ставка НДС не применяется" in report.warnings


@pytest.mark.parametrize("carrier_type", ["ИП", "ИП без НДС", "ИП с НДС"])
def test_any_ip_variant_is_recognized(validator, payload, carrier_type):
    payload["contract"]["carrier_type"] = carrier_type
    report = validator.check(payload)

    assert "У ИП ставка НДС не применяется" in report.warnings


def test_validator_is_stable_on_odd_input(validator):
    """Мусор вместо данных не должен ронять валидатор."""
    for data in (None, {}, [], "строка", 42, {"vehicles": "не список"}):
        report = validator.check(data)
        assert report.has_errors
