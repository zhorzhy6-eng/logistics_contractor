#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты валидатора договора аренды ТС с экипажем (ЭТАП 3.1.D.A.5).

Проверяют обязательный минимум типа: номер и дата договора, срок аренды,
реквизиты Арендатора и Арендодателя, объект аренды (тягач и прицеп), хотя бы
одна перевозимая машина с VIN, точки погрузки и выгрузки с адресом, маршрут,
экипаж (ФИО, дата рождения, паспорт, ВУ, адрес регистрации, телефон) и
арендную плату.

Отдельные группы — три варианта Арендатора (ООО / ИП с НДС / ИП без НДС):
КПП проверяется только у ООО (у ИП заполненный КПП — замечание), у ООО и
ИП с НДС три суммы и обязательная ставка НДС, у ИП без НДС сумма одна.

Всё остальное (нестандартный VIN, машин больше 12, точек больше 10, точка с
датой без адреса, пустые краткие наименования сторон, ЭДО, маршрут не в виде
«откуда — куда») — замечания, а не ошибки: договор печатать можно.

Данные, которые печатает генератор, проходят этот же валидатор без ошибок
(см. tests/test_arenda_ts_generator.py::_payload) — проверка ниже.

PyQt5 тестам не нужен: проверяется только core.

Все данные синтетические, реальных ПДн нет.
"""

import inspect
import logging
import re
from pathlib import Path

import pytest

from core.contracts.arenda_ts import generator as arenda_generator
from core.contracts.arenda_ts.generator import ArendaTsGenerator
from core.contracts.arenda_ts.validator import ArendaTsValidator
from core.contracts.factory import GeneratorFactory
from core.contracts.registry import ContractTypeRegistry

#: Варианты Арендатора: ключ — contract["carrier_type"].
VARIANTS = ("ООО", "ИП с НДС", "ИП без НДС")

#: Суммы теста: 221 099,18 + 22% = 48 641,82 → итого 269 741,00.
BASE_SUM = 221099.18
VAT_SUM = 48641.82
TOTAL_SUM = 269741.00


@pytest.fixture(scope="module", autouse=True)
def _builtin_types_loaded():
    """Регистрация типов: без неё фабрика не знает про arenda_ts."""
    ContractTypeRegistry.load_builtin()


def _vin(number: int) -> str:
    """VIN ровно из 17 символов ISO 3779 (без букв I, O, Q)."""
    return f"TESTV1N{number:010d}"


def _lessee(variant: str = "ООО") -> dict:
    """Арендатор — наша сторона: от её вида зависит вариант бланка."""
    if variant == "ООО":
        return {
            "entity_type": "ООО",
            "full_name": "ООО «Арендатор Тест»",
            "short_name": "ООО «АТ»",
            "inn": "7701234567",
            "kpp": "770101001",
            "ogrn": "1027700132195",
            "legal_address": "г. Москва, ул. Тестовая, д. 1",
            "director_name": "Петров Пётр Петрович",
            "edo": "1A2B3C4D-1111-2222-3333-444455556666",
        }

    return {
        "entity_type": "ИП",
        "full_name": "Индивидуальный предприниматель Смирнов Сергей Сергеевич",
        "short_name": "ИП Смирнов С.С.",
        "inn": "770123456789",
        # У индивидуального предпринимателя КПП не бывает: промпт отдаёт "".
        "kpp": "",
        "ogrn": "321770000123456",
        "legal_address": "г. Москва, ул. Тестовая, д. 7",
        "director_name": "Смирнов Сергей Сергеевич",
        "edo": "1A2B3C4D-1111-2222-3333-444455556667",
    }


def _lessor() -> dict:
    """Арендодатель — вторая сторона (в бланках всегда ООО)."""
    return {
        "full_name": "ООО «Арендодатель Тест»",
        "short_name": "ООО «АДТ»",
        "inn": "7709876543",
        "ogrn": "1027700132196",
        "legal_address": "г. Москва, ул. Вторая, д. 2",
        "director_name": "Сидоров Сидор Сидорович",
        "edo": "1A2B3C4D-1111-2222-3333-444455556668",
    }


def _vehicle(number: int) -> dict:
    """Машина таблицы п. 3.1 (не тягач и не прицеп)."""
    return {"brand_model": f"МОДЕЛЬ {number}", "vin": _vin(number)}


def _loading(number: int) -> dict:
    """Точка погрузки: адрес, дата и окно подачи ТС."""
    return {
        "address": f"Адрес погрузки {number}",
        "date": "2026-09-21",
        "time_from": "08:00",
        "time_to": "18:00",
    }


def _unloading(number: int) -> dict:
    """Точка выгрузки: адрес и плановая дата завершения."""
    return {"address": f"Адрес выгрузки {number}", "date": "2026-09-27"}


def _driver() -> dict:
    """Экипаж в том виде, в каком его отдаёт распознавание (п. 3.5)."""
    return {
        "full_name": "Иванов Иван Иванович",
        "birth_date": "1980-01-01",
        "passport": "18 22 926830",
        "license": "99 36 123456",
        "address": "г. Москва, ул. Водительская, д. 3",
        "phone": "+7 (999) 123-45-67",
    }


def _payload(
    variant: str = "ООО",
    cars: int = 2,
    loadings: int = 2,
    unloadings: int = 2,
) -> dict:
    """
    Корректно заполненный договор аренды.

    Блоки сторон, маршрут, срок аренды и точки лежат в contract: так их видят
    и валидатор, и генератор (generate() переносит туда же корневые поля
    распознавания, см. ArendaTsGenerator._hoist_contract_fields).
    """
    without_vat = variant == "ИП без НДС"

    contract = {
        "number": "АБ-123",
        "date": "2026-09-19",
        "carrier_type": variant,
        "route": "Москва — Калуга — Чехов",
        "lease_start_date": "2026-09-20",
        "lease_end_date": "2026-09-28",
        "lessee": _lessee(variant),
        "lessor": _lessor(),
        "loadings": [_loading(number) for number in range(1, loadings + 1)],
        "unloadings": [_unloading(number) for number in range(1, unloadings + 1)],
    }

    if without_vat:
        # Одна сумма «НДС не облагается», ставка нулевая.
        contract["vat_rate"] = "0%"
        contract["sum_total"] = BASE_SUM
    else:
        # Три суммы: без НДС, НДС по ставке, итого.
        contract["vat_rate"] = "22%"
        contract["vat_rate_num"] = 22
        contract["sum_wo_vat"] = BASE_SUM
        contract["sum_vat"] = VAT_SUM
        contract["sum_total"] = TOTAL_SUM

    return {
        "tractor": {
            "brand_model": "Тягач-Модель",
            "plate_number": "А001АА01",
            "vehicle_type": "грузовой тягач седельный",
        },
        "trailer": {"brand_model": "Прицеп-Модель", "plate_number": "Б002ББ02"},
        "vehicles": [_vehicle(number) for number in range(1, cars + 1)],
        "driver": _driver(),
        "contract": contract,
    }


@pytest.fixture
def validator() -> ArendaTsValidator:
    return ArendaTsValidator()


@pytest.fixture
def payload() -> dict:
    """Корректно заполненный договор с Арендатором-ООО (2 машины, 2+2 точки)."""
    return _payload("ООО")


@pytest.fixture
def ip_with_vat_payload() -> dict:
    """Корректно заполненный договор с Арендатором-ИП с НДС."""
    return _payload("ИП с НДС")


@pytest.fixture
def ip_without_vat_payload() -> dict:
    """Корректно заполненный договор с Арендатором-ИП без НДС (одна сумма)."""
    return _payload("ИП без НДС")


def _errors(report) -> str:
    return "\n".join(report.errors)


# ─────────────────────────────────────────────────────────────
# Регистрация и базовое поведение
# ─────────────────────────────────────────────────────────────

def test_validator_is_registered_for_arenda_ts():
    spec = ContractTypeRegistry.get("arenda_ts", strict=True)
    assert spec.validator_class is ArendaTsValidator
    assert isinstance(
        GeneratorFactory.get_validator("arenda_ts", strict=True),
        ArendaTsValidator,
    )
    assert ArendaTsGenerator.VALIDATOR_CLASS is ArendaTsValidator


def test_contract_type_is_arenda_ts():
    assert ArendaTsValidator.CONTRACT_TYPE == "arenda_ts"


def test_limits_match_generator():
    """Пределы бланка в валидаторе и в генераторе совпадают."""
    assert ArendaTsValidator.MAX_CARS == arenda_generator.MAX_CARS
    assert ArendaTsValidator.MAX_POINTS == arenda_generator.MAX_POINTS


def test_variant_names_match_generator_templates():
    """Названия вариантов — это ключи бланков генератора."""
    assert set(ArendaTsGenerator.TEMPLATE_NAMES) == {
        ArendaTsValidator.OOO,
        ArendaTsValidator.IP_WITH_VAT,
        ArendaTsValidator.IP_WITHOUT_VAT,
    }


@pytest.mark.parametrize("carrier_type", ["ООО", "ИП с НДС", "ИП без НДС", "", "Прочее"])
def test_variant_flags_match_generator(carrier_type):
    """Вид Арендатора разбирается так же, как в генераторе (выбор бланка)."""
    assert ArendaTsValidator._variant_flags(carrier_type) == (
        ArendaTsGenerator._variant_flags(carrier_type)
    )


@pytest.mark.parametrize("is_ip_without_vat", [False, True])
def test_base_price_matches_generator(is_ip_without_vat):
    """База арендной платы читается из тех же полей и в том же порядке."""
    contract = {
        "sum_wo_vat": BASE_SUM,
        "sum_total": TOTAL_SUM,
        "price_without_vat": 1000.0,
    }
    assert ArendaTsValidator._base_price(contract, is_ip_without_vat) == (
        ArendaTsGenerator._base_price(contract, is_ip_without_vat)
    )


def test_empty_data_reports_all_required_errors(validator):
    """Пустой ContractData: на месте все обязательные ошибки."""
    report = validator.check({})

    expected = [
        "Не заполнен номер договора аренды",
        "Не заполнена дата договора",
        "Не указана дата начала аренды",
        "Не указана дата окончания аренды",
        "Не заполнено наименование Арендатора",
        "Не заполнен ИНН Арендатора",
        "Не заполнен ОГРН(ИП) Арендатора",
        "Не заполнен адрес Арендатора",
        "Не заполнено ФИО руководителя Арендатора",
        "Не заполнен КПП Арендатора",
        "Не заполнено наименование Арендодателя",
        "Не заполнен ИНН Арендодателя",
        "Не заполнен ОГРН Арендодателя",
        "Не заполнен адрес Арендодателя",
        "Не заполнено ФИО руководителя Арендодателя",
        "Не заполнена марка тягача",
        "Не заполнен госномер тягача",
        "Не заполнен тип ТС тягача",
        "Не заполнена марка прицепа",
        "Не заполнен госномер прицепа",
        "Добавьте хотя бы одну перевозимую машину",
        "Укажите хотя бы одну точку погрузки",
        "Укажите хотя бы одну точку выгрузки",
        "Не указан маршрут аренды",
        "Не заполнено ФИО водителя (экипажа)",
        "Не заполнена дата рождения водителя",
        "Не заполнены паспортные данные водителя",
        "Не заполнены данные водительского удостоверения",
        "Не заполнен адрес регистрации",
        "Не заполнен телефон водителя",
        "Стоимость без НДС должна быть больше нуля",
        "Не указана ставка НДС",
    ]
    missing = [message for message in expected if message not in report.errors]
    assert missing == [], f"нет ошибок: {missing}\n{_errors(report)}"


def test_empty_data_warns_instead_of_failing(validator):
    """Пустой ContractData: замечания тоже есть, но документ ими не блокируется."""
    report = validator.check({})

    assert report.has_errors
    assert "Не заполнено краткое наименование Арендатора" in report.warnings
    assert "Не заполнен ЭДО Арендодателя" in report.warnings


def test_filled_ooo_payload_has_no_errors(validator, payload):
    """ООО с тремя суммами и КПП: ошибок и замечаний нет."""
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert report.is_clean, report.warnings


def test_filled_ip_with_vat_payload_has_no_errors(validator, ip_with_vat_payload):
    """ИП с НДС: три суммы, КПП нет — ошибок и замечаний нет."""
    report = validator.check(ip_with_vat_payload)

    assert report.errors == [], _errors(report)
    assert report.is_clean, report.warnings


def test_filled_ip_without_vat_payload_has_no_errors(validator, ip_without_vat_payload):
    """ИП без НДС: одна сумма «НДС не облагается» — ошибок и замечаний нет."""
    report = validator.check(ip_without_vat_payload)

    assert report.errors == [], _errors(report)
    assert report.is_clean, report.warnings


def test_validate_returns_only_errors(validator, payload):
    payload["contract"].pop("number")
    errors = validator.validate(payload)

    assert isinstance(errors, list)
    assert "Не заполнен номер договора аренды" in errors


def test_report_text_has_russian_blocks(validator, payload):
    payload["contract"].pop("number")
    text = validator.check(payload).format_text()

    assert "Ошибки — нужно исправить:" in text


def test_validator_does_not_use_core_validator(validator, payload):
    """
    Валидатор типа работает по своему набору полей.

    core.validator.Validator требует реквизитов перевозчика и заказчика в
    формате договора-заявки на перевозку — у аренды другая схема (две стороны,
    срок аренды, экипаж). Если бы валидатор делегировал ему проверку, валидные
    данные дали бы ошибки, а в модуле появился бы импорт или вызов чужого
    валидатора.
    """
    source = Path(inspect.getsourcefile(ArendaTsValidator)).read_text(
        encoding="utf-8"
    )

    assert validator.check(payload).errors == []
    assert re.search(r"(?<![A-Za-z_])Validator\(", source) is None, (
        "валидатор аренды не должен вызывать core.validator.Validator"
    )
    assert "from core.validator import Validator" not in source


def test_generator_payload_passes_validator():
    """Данные из тестов генератора проходят валидатор без ошибок."""
    generator_tests = pytest.importorskip("test_arenda_ts_generator")

    for variant in VARIANTS:
        data = ArendaTsGenerator._hoist_contract_fields(
            generator_tests._payload(variant)
        )
        report = ArendaTsValidator().check(data)

        assert report.errors == [], f"{variant}: {_errors(report)}"


def test_generator_validate_hook_uses_arenda_validator(payload, templates_dir):
    generator = ArendaTsGenerator(templates_dir=str(templates_dir))
    report = generator.validate(ArendaTsGenerator._hoist_contract_fields(payload))

    assert report.errors == [], _errors(report)


def test_validator_is_stable_on_odd_input(validator):
    """Мусор вместо данных не должен ронять валидатор."""
    for data in (None, {}, [], "строка", 42, {"vehicles": "не список"}):
        report = validator.check(data)
        assert report.has_errors


def test_logs_have_counts_without_personal_data(caplog, validator, payload):
    """В лог валидатора попадают только счётчики и вид Арендатора — без ПДн."""
    with caplog.at_level(logging.INFO, logger="core.contracts.arenda_ts.validator"):
        validator.check(payload)

    messages = "\n".join(
        record.getMessage() for record in caplog.records
        if record.name == "core.contracts.arenda_ts.validator"
    )

    assert "машин=2" in messages
    assert "точек погрузки=2" in messages
    assert "вариант=ООО" in messages

    for fragment in ("Арендатор Тест", "Арендодатель Тест", "Петров", "Сидоров",
                     "Иванов", "7701234567", "TESTV1N", "Адрес погрузки",
                     "Москва", "221", "18 22 926830", "99 36 123456"):
        assert fragment not in messages, f"в логе есть «{fragment}»"


# ─────────────────────────────────────────────────────────────
# Договор и срок аренды
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field, expected", [
    ("number", "Не заполнен номер договора аренды"),
    ("date", "Не заполнена дата договора"),
])
def test_contract_header_fields_are_required(validator, payload, field, expected):
    payload["contract"].pop(field)
    report = validator.check(payload)

    assert expected in report.errors, _errors(report)


def test_contract_number_of_spaces_is_empty(validator, payload):
    payload["contract"]["number"] = "   "
    report = validator.check(payload)

    assert "Не заполнен номер договора аренды" in report.errors


@pytest.mark.parametrize("field, expected", [
    ("lease_start_date", "Не указана дата начала аренды"),
    ("lease_end_date", "Не указана дата окончания аренды"),
])
def test_lease_dates_are_required(validator, payload, field, expected):
    payload["contract"].pop(field)
    report = validator.check(payload)

    assert expected in report.errors, _errors(report)


def test_lease_end_before_start_is_error(validator, payload):
    """Окончание аренды раньше начала — ошибка, а не замечание."""
    payload["contract"]["lease_end_date"] = "2026-09-19"
    report = validator.check(payload)

    assert "Дата окончания аренды раньше даты начала аренды" in report.errors


def test_lease_end_equal_start_is_not_an_error(validator, payload):
    """Аренда на один день — норма: начало и окончание совпадают."""
    payload["contract"]["lease_start_date"] = "2026-09-20"
    payload["contract"]["lease_end_date"] = "2026-09-20"
    report = validator.check(payload)

    assert not any("раньше" in error for error in report.errors)


def test_lease_dates_in_document_format_are_compared(validator, payload):
    """Распознавание отдаёт даты в формате ДД.ММ.ГГГГ — порядок тот же."""
    payload["contract"]["lease_start_date"] = "20.09.2026"
    payload["contract"]["lease_end_date"] = "28.09.2026"
    report = validator.check(payload)

    assert not any("раньше" in error for error in report.errors)


def test_unparsable_lease_date_is_not_compared(validator, payload):
    """Непонятная дата не даёт ошибки порядка: сравнивать нечего."""
    payload["contract"]["lease_end_date"] = "по факту"
    report = validator.check(payload)

    assert not any("раньше" in error for error in report.errors)


# ─────────────────────────────────────────────────────────────
# Стороны: Арендатор
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field, expected", [
    ("full_name", "Не заполнено наименование Арендатора"),
    ("inn", "Не заполнен ИНН Арендатора"),
    ("ogrn", "Не заполнен ОГРН(ИП) Арендатора"),
    ("legal_address", "Не заполнен адрес Арендатора"),
    ("director_name", "Не заполнено ФИО руководителя Арендатора"),
    ("kpp", "Не заполнен КПП Арендатора"),
])
def test_lessee_fields_are_required(validator, payload, field, expected):
    """У ООО-Арендатора обязательны в том числе КПП и ОГРН."""
    payload["contract"]["lessee"][field] = ""
    report = validator.check(payload)

    assert expected in report.errors, _errors(report)


def test_lessee_ogrnip_key_is_accepted(validator, payload):
    """ОГРНИП может прийти отдельным ключом ogrnip."""
    lessee = payload["contract"]["lessee"]
    lessee["ogrnip"] = lessee.pop("ogrn")
    report = validator.check(payload)

    assert "Не заполнен ОГРН(ИП) Арендатора" not in report.errors


def test_lessee_address_from_address_key(validator, payload):
    """Адрес справочника лежит в поле address, бланка — в legal_address."""
    lessee = payload["contract"]["lessee"]
    lessee["address"] = lessee.pop("legal_address")
    report = validator.check(payload)

    assert "Не заполнен адрес Арендатора" not in report.errors


def test_kpp_is_required_for_ooo(validator, payload):
    """У ООО пустой КПП — ошибка: плейсхолдер в бланке обязателен."""
    payload["contract"]["lessee"]["kpp"] = ""
    report = validator.check(payload)

    assert "Не заполнен КПП Арендатора" in report.errors


@pytest.mark.parametrize("variant", ["ИП с НДС", "ИП без НДС"])
def test_ip_without_kpp_has_no_kpp_errors(validator, variant):
    """У ИП КПП не проверяется: плейсхолдера в ИП-бланке нет."""
    report = validator.check(_payload(variant))

    assert not any("КПП" in error for error in report.errors), _errors(report)


def test_kpp_of_ip_is_warning_not_error(validator, ip_with_vat_payload):
    """У ИП заполненный КПП — замечание: печатать можно, но так не бывает."""
    ip_with_vat_payload["contract"]["lessee"]["kpp"] = "770101001"
    report = validator.check(ip_with_vat_payload)

    assert "У ИП не бывает КПП" in report.warnings
    assert not any("КПП" in error for error in report.errors), _errors(report)


def test_customer_short_name_is_warning(validator, payload):
    payload["contract"]["lessee"].pop("short_name")
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert "Не заполнено краткое наименование Арендатора" in report.warnings


def test_ooo_basis_without_ustav_is_warning(validator, payload):
    """Распознанное основание ООО без «Устава» — замечание, не ошибка."""
    payload["contract"]["lessee"]["basis"] = (
        "свидетельства о государственной регистрации"
    )
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("Устава" in warning for warning in report.warnings)


def test_ooo_basis_with_ustav_is_not_a_warning(validator, payload):
    payload["contract"]["lessee"]["basis"] = "Устава"
    report = validator.check(payload)

    assert not any("Устава" in warning for warning in report.warnings)


def test_edo_missing_is_warning(validator, payload):
    """ЭДО необязателен, но при наличии печатается — о пропуске замечание."""
    payload["contract"]["lessee"].pop("edo")
    payload["contract"]["lessor"].pop("edo")
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert "Не заполнен ЭДО Арендатора" in report.warnings
    assert "Не заполнен ЭДО Арендодателя" in report.warnings


def test_edo_from_flat_contract_key_is_accepted(validator, payload):
    """ЭДО принимается и плоским ключом contract (lessee_edo)."""
    lessee = payload["contract"]["lessee"]
    payload["contract"]["lessee_edo"] = lessee.pop("edo")
    report = validator.check(payload)

    assert "Не заполнен ЭДО Арендатора" not in report.warnings


# ─────────────────────────────────────────────────────────────
# Стороны: Арендодатель
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field, expected", [
    ("full_name", "Не заполнено наименование Арендодателя"),
    ("inn", "Не заполнен ИНН Арендодателя"),
    ("ogrn", "Не заполнен ОГРН Арендодателя"),
    ("legal_address", "Не заполнен адрес Арендодателя"),
    ("director_name", "Не заполнено ФИО руководителя Арендодателя"),
])
def test_lessor_fields_are_required(validator, payload, field, expected):
    payload["contract"]["lessor"][field] = ""
    report = validator.check(payload)

    assert expected in report.errors, _errors(report)


def test_lessor_kpp_is_not_checked(validator, payload):
    """Арендодатель в бланке всегда ООО: КПП у него не печатается."""
    payload["contract"]["lessor"]["kpp"] = ""
    payload["contract"]["lessor"].pop("kpp")
    report = validator.check(payload)

    assert not any("КПП" in error for error in report.errors), _errors(report)


def test_lessor_short_name_is_warning(validator, payload):
    payload["contract"]["lessor"].pop("short_name")
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert "Не заполнено краткое наименование Арендодателя" in report.warnings


def test_parties_can_come_from_customer_and_carrier(validator, payload):
    """
    Вкладки интерфейса кладут Арендатора в customer, Арендодателя — в carrier.

    Если блоков в contract нет, валидатор читает эти данные оттуда.
    """
    contract = payload["contract"]
    payload["customer"] = contract.pop("lessee")
    payload["carrier"] = contract.pop("lessor")
    report = validator.check(payload)

    assert report.errors == [], _errors(report)


def test_parties_from_customer_keep_ooo_kpp_requirement(validator, payload):
    """Требование КПП не теряется и при данных из блока customer."""
    contract = payload["contract"]
    payload["customer"] = contract.pop("lessee")
    payload["carrier"] = contract.pop("lessor")
    payload["customer"]["kpp"] = ""
    report = validator.check(payload)

    assert "Не заполнен КПП Арендатора" in report.errors


# ─────────────────────────────────────────────────────────────
# Объект аренды: тягач и прицеп (п. 2.1)
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field, expected", [
    ("brand_model", "Не заполнена марка тягача"),
    ("plate_number", "Не заполнен госномер тягача"),
    ("vehicle_type", "Не заполнен тип ТС тягача"),
])
def test_tractor_fields_are_required(validator, payload, field, expected):
    payload["tractor"][field] = ""
    report = validator.check(payload)

    assert expected in report.errors, _errors(report)


def test_tractor_type_from_ts_type_key(validator, payload):
    """Тип ТС справочника лежит в поле ts_type, распознавания — vehicle_type."""
    payload["tractor"].pop("vehicle_type")
    payload["tractor"]["ts_type"] = "седельный тягач"
    report = validator.check(payload)

    assert "Не заполнен тип ТС тягача" not in report.errors


@pytest.mark.parametrize("field, expected", [
    ("brand_model", "Не заполнена марка прицепа"),
    ("plate_number", "Не заполнен госномер прицепа"),
])
def test_trailer_fields_are_required(validator, payload, field, expected):
    payload["trailer"][field] = ""
    report = validator.check(payload)

    assert expected in report.errors, _errors(report)


# ─────────────────────────────────────────────────────────────
# Машины п. 3.1: минимум одна, у каждой VIN
# ─────────────────────────────────────────────────────────────

def test_at_least_one_cargo_vehicle_required(validator, payload):
    payload["vehicles"] = []
    report = validator.check(payload)

    assert "Добавьте хотя бы одну перевозимую машину" in report.errors


def test_tractor_and_trailer_are_not_cargo(validator, payload):
    """В vehicles только тягач и прицеп — перевозимых машин нет."""
    payload["vehicles"] = [
        {"brand_model": "Тягач-Модель", "vehicle_type": "Тягач"},
        {"brand_model": "Прицеп-Модель", "vehicle_type": "Полуприцеп"},
    ]
    report = validator.check(payload)

    assert "Добавьте хотя бы одну перевозимую машину" in report.errors


def test_empty_vehicle_rows_are_ignored(validator, payload):
    """Строка-пустышка из таблицы машиной не считается."""
    payload["vehicles"] = [{}, {"vin": "", "brand_model": "  "}]
    report = validator.check(payload)

    assert "Добавьте хотя бы одну перевозимую машину" in report.errors


def test_every_vehicle_needs_vin(validator, payload):
    payload["vehicles"].append({"brand_model": "МОДЕЛЬ 3"})
    report = validator.check(payload)

    assert any("Машина №3: не заполнен VIN" in error for error in report.errors)


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
    payload["vehicles"][0]["vin"] = _vin(1).lower()
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


# ─────────────────────────────────────────────────────────────
# Точки погрузки и выгрузки (п. 3.2, 3.3)
# ─────────────────────────────────────────────────────────────

def test_no_loadings_is_error(validator, payload):
    payload["contract"]["loadings"] = []
    report = validator.check(payload)

    assert "Укажите хотя бы одну точку погрузки" in report.errors


def test_no_unloadings_is_error(validator, payload):
    payload["contract"]["unloadings"] = []
    report = validator.check(payload)

    assert "Укажите хотя бы одну точку выгрузки" in report.errors


def test_one_point_with_address_is_enough(validator, payload):
    payload["contract"]["loadings"] = [_loading(1)]
    payload["contract"]["unloadings"] = [_unloading(1)]
    report = validator.check(payload)

    assert report.errors == [], _errors(report)


def test_point_with_date_but_without_address_is_warning(validator, payload):
    """Дата есть, адреса нет: строка в бланке останется недозаполненной."""
    payload["contract"]["loadings"] = [
        _loading(1),
        {"address": "", "date": "2026-09-22"},
    ]
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert "Точка №2: адрес пуст" in report.warnings


def test_point_with_time_but_without_address_is_warning(validator, payload):
    """Окно подачи ТС у распознанной точки лежит в time_from / time_to."""
    payload["contract"]["loadings"] = [
        {"address": "", "date": "", "time_from": "08:00", "time_to": "18:00"},
    ]
    report = validator.check(payload)

    assert "Точка №1: адрес пуст" in report.warnings


def test_point_without_address_but_with_name_is_not_warned(validator, payload):
    """Пустая точка без даты и времени адресом не считается, но и не шумит."""
    payload["contract"]["loadings"] = [_loading(1)]
    payload["contract"]["unloadings"] = [
        _unloading(1),
        {"address": "", "date": "", "time_window": ""},
    ]
    report = validator.check(payload)

    assert not any("адрес пуст" in warning for warning in report.warnings)


def test_empty_point_rows_are_ignored(validator, payload):
    """Строка-пустышка из таблицы точкой не считается."""
    payload["contract"]["loadings"] = [
        {"address": "", "date": "", "time_from": "", "time_to": ""},
    ]
    report = validator.check(payload)

    assert "Укажите хотя бы одну точку погрузки" in report.errors
    assert not any("адрес пуст" in warning for warning in report.warnings)


def test_points_can_come_from_top_level(validator, payload):
    """Точки верхнего уровня (loadings / unloadings) тоже принимаются."""
    contract = payload["contract"]
    payload["loadings"] = contract.pop("loadings")
    payload["unloadings"] = contract.pop("unloadings")
    report = validator.check(payload)

    assert report.errors == [], _errors(report)


def test_more_than_ten_loadings_is_warning(validator, payload):
    payload["contract"]["loadings"] = [
        _loading(number) for number in range(1, 12)
    ]
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("Точек погрузки 11" in warning for warning in report.warnings)


def test_more_than_ten_unloadings_is_warning(validator, payload):
    payload["contract"]["unloadings"] = [
        _unloading(number) for number in range(1, 12)
    ]
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("Точек выгрузки 11" in warning for warning in report.warnings)


def test_ten_points_is_not_a_warning(validator, payload):
    payload["contract"]["loadings"] = [_loading(number) for number in range(1, 11)]
    payload["contract"]["unloadings"] = [
        _unloading(number) for number in range(1, 11)
    ]
    report = validator.check(payload)

    assert not any("в бланк помещается" in warning for warning in report.warnings)


# ─────────────────────────────────────────────────────────────
# Маршрут (п. 3.4)
# ─────────────────────────────────────────────────────────────

def test_route_is_required(validator, payload):
    payload["contract"].pop("route")
    report = validator.check(payload)

    assert "Не указан маршрут аренды" in report.errors


def test_route_without_dash_is_warning(validator, payload):
    """Маршрут без тире — мягкая проверка: печатать можно."""
    payload["contract"]["route"] = "Москва Калуга Чехов"
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert any("не похож на" in warning for warning in report.warnings)


def test_route_with_hyphen_is_not_a_warning(validator, payload):
    """Дефис вместо длинного тире — норма (так пишут в документах)."""
    payload["contract"]["route"] = "Мурманск - Пятигорск"
    report = validator.check(payload)

    assert not any("не похож на" in warning for warning in report.warnings)


# ─────────────────────────────────────────────────────────────
# Экипаж (п. 3.5)
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field, expected", [
    ("full_name", "Не заполнено ФИО водителя (экипажа)"),
    ("birth_date", "Не заполнена дата рождения водителя"),
    ("passport", "Не заполнены паспортные данные водителя"),
    ("license", "Не заполнены данные водительского удостоверения"),
    ("phone", "Не заполнен телефон водителя"),
])
def test_driver_fields_are_required(validator, payload, field, expected):
    payload["driver"][field] = ""
    report = validator.check(payload)

    assert expected in report.errors, _errors(report)


def test_driver_without_license_is_error(validator, payload):
    """Экипаж без водительского удостоверения в договор не годится."""
    payload["driver"].pop("license")
    report = validator.check(payload)

    assert "Не заполнены данные водительского удостоверения" in report.errors


def test_driver_without_passport_is_error(validator, payload):
    payload["driver"].pop("passport")
    report = validator.check(payload)

    assert "Не заполнены паспортные данные водителя" in report.errors


def test_driver_registration_address_is_required(validator, payload):
    payload["driver"].pop("address")
    report = validator.check(payload)

    assert "Не заполнен адрес регистрации" in report.errors


def test_driver_registration_address_key_is_accepted(validator, payload):
    """Распознавание кладёт адрес в address, справочник — в registration_address."""
    driver = payload["driver"]
    driver["registration_address"] = driver.pop("address")
    report = validator.check(payload)

    assert "Не заполнен адрес регистрации" not in report.errors


def test_driver_passport_and_license_from_registry_fields(validator, payload):
    """Справочник водителя хранит паспорт и ВУ серией и номером отдельно."""
    payload["driver"] = {
        "full_name": "Иванов Иван Иванович",
        "birth_date": "1980-01-01",
        "passport_series": "18 22",
        "passport_number": "926830",
        "license_series": "99 36",
        "license_number": "123456",
        "registration_address": "г. Москва, ул. Водительская, д. 3",
        "phone": "+7 (999) 123-45-67",
    }
    report = validator.check(payload)

    assert report.errors == [], _errors(report)


def test_driver_empty_series_and_number_is_error(validator, payload):
    """Пустые серия и номер — те же пустые паспортные данные."""
    payload["driver"].pop("passport")
    payload["driver"]["passport_series"] = "  "
    payload["driver"]["passport_number"] = ""
    report = validator.check(payload)

    assert "Не заполнены паспортные данные водителя" in report.errors


# ─────────────────────────────────────────────────────────────
# Арендная плата: ООО и ИП с НДС (три суммы)
# ─────────────────────────────────────────────────────────────

def test_ooo_cost_is_required(validator, payload):
    """Базы арендной платы нет ни в одном поле — ошибка."""
    payload["contract"].pop("sum_wo_vat")
    payload["contract"].pop("price_without_vat", None)
    report = validator.check(payload)

    assert "Стоимость без НДС должна быть больше нуля" in report.errors


def test_ooo_cost_can_come_from_price_without_vat(validator, payload):
    """Данные вкладки «Стоимость»: сумма без НДС лежит в price_without_vat."""
    payload["contract"].pop("sum_wo_vat")
    payload["contract"]["price_without_vat"] = BASE_SUM
    report = validator.check(payload)

    assert "Стоимость без НДС должна быть больше нуля" not in report.errors


def test_zero_cost_is_error(validator, payload):
    payload["contract"]["sum_wo_vat"] = 0
    report = validator.check(payload)

    assert "Стоимость без НДС должна быть больше нуля" in report.errors


def test_cost_as_text_is_accepted(validator, payload):
    """Распознавание и справочники отдают суммы строкой с разделителями."""
    payload["contract"]["sum_wo_vat"] = "221 099,18"
    report = validator.check(payload)

    assert "Стоимость без НДС должна быть больше нуля" not in report.errors


def test_ooo_vat_rate_is_required(validator, payload):
    """У ООО НДС считается по ставке: без неё суммы в бланке не будет."""
    payload["contract"].pop("vat_rate")
    payload["contract"].pop("vat_rate_num")
    report = validator.check(payload)

    assert "Не указана ставка НДС" in report.errors


def test_ooo_vat_rate_as_text_is_accepted(validator, payload):
    payload["contract"].pop("vat_rate_num")
    payload["contract"]["vat_rate"] = "22%"
    report = validator.check(payload)

    assert "Не указана ставка НДС" not in report.errors


def test_ooo_zero_vat_rate_is_error(validator, payload):
    """Нулевая ставка в варианте ООО — та же ошибка: НДС в бланке по ставке."""
    payload["contract"]["vat_rate_num"] = 0
    payload["contract"]["vat_rate"] = "0%"
    report = validator.check(payload)

    assert "Не указана ставка НДС" in report.errors


# ─────────────────────────────────────────────────────────────
# Арендная плата: ИП без НДС (одна сумма)
# ─────────────────────────────────────────────────────────────

def test_ip_without_vat_single_sum_is_enough(validator, ip_without_vat_payload):
    """Одна сумма «НДС не облагается» в sum_total — этого достаточно."""
    report = validator.check(ip_without_vat_payload)

    assert report.errors == [], _errors(report)
    assert not any("ставка НДС" in warning for warning in report.warnings)


def test_ip_without_vat_needs_sum(validator, ip_without_vat_payload):
    ip_without_vat_payload["contract"].pop("sum_total")
    report = validator.check(ip_without_vat_payload)

    assert "Стоимость без НДС должна быть больше нуля" in report.errors


def test_ip_without_vat_cost_can_come_from_price_without_vat(
    validator, ip_without_vat_payload
):
    ip_without_vat_payload["contract"].pop("sum_total")
    ip_without_vat_payload["contract"]["price_without_vat"] = BASE_SUM
    report = validator.check(ip_without_vat_payload)

    assert "Стоимость без НДС должна быть больше нуля" not in report.errors


def test_ip_without_vat_with_vat_rate_is_warning(validator, ip_without_vat_payload):
    """Ставка НДС у ИП без НДС — замечание: в бланке НДС не считается."""
    ip_without_vat_payload["contract"]["vat_rate"] = "22%"
    report = validator.check(ip_without_vat_payload)

    assert report.errors == [], _errors(report)
    assert "У ИП без НДС ставка НДС не применяется" in report.warnings


def test_ip_without_vat_zero_rate_is_not_a_warning(validator, ip_without_vat_payload):
    ip_without_vat_payload["contract"]["vat_rate_num"] = 0
    report = validator.check(ip_without_vat_payload)

    assert not any("ставка НДС" in warning for warning in report.warnings)


def test_ip_with_vat_needs_rate(validator, ip_with_vat_payload):
    """У ИП с НДС ставка обязательна так же, как у ООО."""
    ip_with_vat_payload["contract"].pop("vat_rate")
    ip_with_vat_payload["contract"].pop("vat_rate_num")
    report = validator.check(ip_with_vat_payload)

    assert "Не указана ставка НДС" in report.errors


# ─────────────────────────────────────────────────────────────
# Устойчивость: вид Арендатора неизвестен
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("carrier_type", ["", "   ", "ООО (с НДС)", "ООО", "Прочее"])
def test_empty_carrier_type_is_treated_as_ooo(validator, payload, carrier_type):
    """Пустой и незнакомый вид Арендатора трактуется как ООО (вариант бланка)."""
    payload["contract"]["carrier_type"] = carrier_type
    report = validator.check(payload)

    assert report.errors == [], _errors(report)
    assert "У ИП не бывает КПП" not in report.warnings


def test_empty_carrier_type_keeps_kpp_requirement(validator, payload):
    payload["contract"]["carrier_type"] = ""
    payload["contract"]["lessee"]["kpp"] = ""
    report = validator.check(payload)

    assert "Не заполнен КПП Арендатора" in report.errors


def test_carrier_type_is_derived_from_lessee_entity_type(validator, payload):
    """Поле carrier_type не заполнено — вид берётся из entity_type Арендатора."""
    payload["contract"].pop("carrier_type")
    report = validator.check(payload)

    assert report.errors == [], _errors(report)


@pytest.mark.parametrize("variant", ["ИП с НДС", "ИП без НДС"])
def test_ip_variant_is_derived_from_entity_type(validator, variant):
    """У ИП без carrier_type вид выводится из entity_type и ставки НДС."""
    data = _payload(variant)
    data["contract"].pop("carrier_type")
    report = validator.check(data)

    assert report.errors == [], _errors(report)
    assert not any("КПП" in error for error in report.errors), _errors(report)


def test_ip_derived_variant_warns_about_kpp(validator, ip_with_vat_payload):
    """Выведенный вид ИП тоже даёт замечание о КПП, а не ошибку."""
    ip_with_vat_payload["contract"].pop("carrier_type")
    ip_with_vat_payload["contract"]["lessee"]["kpp"] = "770101001"
    report = validator.check(ip_with_vat_payload)

    assert "У ИП не бывает КПП" in report.warnings
    assert not any("КПП" in error for error in report.errors), _errors(report)
