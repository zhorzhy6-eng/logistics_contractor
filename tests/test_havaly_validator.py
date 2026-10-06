#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты валидатора Excel-заявки Хавалов (ЭТАП 3.1.E.A.3).

Главное, что здесь закреплено, — ПРАВИЛО ШАГА: валидатор не мешает работе.
Ошибок (``report.errors``) у типа почти нет — только структурные, когда
файл невозможно открыть или в нём нет самой формы. Всё остальное —
замечания:

  * пустой блок заявки → только замечания, ни одной ошибки;
  * 11 машин → замечание про вместимость бланка, а не ошибка;
  * битый файл (нет листа TDSheet) → ошибка;
  * файла нет / не .xlsx / нет строки шапки → ошибка;
  * валидатор не блокирует ни ``generate``, ни ``fill_from_template``:
    файл сохраняется даже тогда, когда отчёт полон замечаний.

Отдельно проверяются стыки:

  * имена полей: валидатор читает те же ключи, что пишет генератор
    (единый источник — core/prompts/havaly.py);
  * данные из формы: валидатор принимает и путь к .xlsx, и схему промпта,
    и плоский словарь, и ``ContractData``;
  * логи без ПДн: ни ФИО, ни VIN, ни телефонов, ни адресов.

Все данные синтетические, реальных ПДн нет. Выходные файлы создаются
в tests/_tmp и удаляются в finally.
"""

import logging
import sys
import uuid
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.contract_data import ContractData  # noqa: E402
from core.contracts.zayavka.generator import (  # noqa: E402
    CARRIER_NAME,
    CUSTOMER_NAME,
    HEADERS,
    MAX_VEHICLES,
    SHEET_NAME,
    ZayavkaExcelGenerator,
    ZayavkaTemplateError,
)

from core.contracts.zayavka.validator import ZayavkaExcelValidator  # noqa: E402

SAMPLE_NAME = "Хавалы_образец.xlsx"

#: Папка тестовых форм и метка текущего прогона.
STAGING_DIR = Path(__file__).resolve().parent / "_tmp" / "_havaly_forms"
_RUN_ID = uuid.uuid4().hex[:8]

#: Поля, которых не хватает в «полной» заявке — их не бывает в форме.
DATE = "05.10.2026"


def _staging(name: str) -> Path:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    return STAGING_DIR / f"{_RUN_ID}_val_{name}"


def make_zayavka(**overrides) -> dict:
    """Синтетические общие сведения заявки: всё заполнено."""
    data = {
        "date": DATE,
        "lot_number": "LOT-42",
        "loading_city": "Калуга",
        "loading_point": "ул. Промышленная, 12",
        "unloading_city": "Москва",
        "unloading_point": "Складской проезд, 5",
        "carrier_name": CARRIER_NAME,
        "customer_name": CUSTOMER_NAME,
        "tractor_brand": "Foton Auman",
        "tractor_color": "Белый",
        "tractor_plate": "О844ХУ196",
        "trailer_brand": "YANGMINDA",
        "trailer_plate": "71ABF18",
        "driver_last_name": "Тестов",
        "driver_first_name": "Тест",
        "driver_middle_name": "Тестович",
        "driver_license_number": "9901 123456",
        "driver_license_issue_date": "12.03.2019",
        "driver_passport_series": "1822",
        "driver_passport_number": "926830",
        "driver_passport_issuer": "Отделом УФМС России по г. Москве",
        "driver_passport_issue_date": "30.01.2023",
        "driver_citizenship": "РФ",
        "driver_birth_date": "01.01.1980",
        "driver_registration": "г. Москва, ул. Тестовая, д. 1",
        "driver_phone": "+7 (999) 123-45-67",
        "loading_plan_date": "07.10.2026",
        "loading_plan_time": "09:00",
        "price_with_vat": 123456.78,
        "vat_rate": "22%",
    }
    data.update(overrides)
    return data


def make_vehicles(count: int) -> list:
    """Машины с корректными VIN (17 символов, без I, O, Q)."""
    return [
        {
            "vin": f"EC3TEUMB0T00026{index:02d}",
            "brand": f"МАРКА {index}",
            "model": f"МОДЕЛЬ {index}",
            "dealer": f"Дилер {index}",
            "dealer_code": f"D-{index:03d}",
        }
        for index in range(1, count + 1)
    ]


def make_data(vehicles=None, **overrides) -> dict:
    """Полный набор данных заявки для валидатора."""
    return {
        "zayavka": make_zayavka(**overrides),
        "vehicles": vehicles if vehicles is not None else make_vehicles(2),
    }


def make_form(path: Path, *, sheet_name: str = SHEET_NAME,
              headers=HEADERS, rows=None, party: bool = True,
              date: bool = True) -> Path:
    """
    Собирает .xlsx-форму: дата, шапка, строки данных, блок сторон.

    ``date``, ``headers`` и ``rows`` выключаются, когда тесту нужен
    ОБРЫВОК формы, а не форма целиком: например, файл без подписи
    «Дата заявки:» или без блока сторон.
    """
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_name
    if date:
        sheet["A5"] = "Дата заявки:"
        sheet["B5"] = DATE
    if headers:
        for index, header in enumerate(headers, start=1):
            sheet.cell(row=6, column=index, value=header)
    for offset, row in enumerate(rows or []):
        for index, value in enumerate(row, start=1):
            if value is not None:
                sheet.cell(row=7 + offset, column=index, value=value)
    if party:
        # Подписи сторон и их значения — рядом: у одного заказчика блок
        # выглядит так («Заказчик» | «Сюрлогистик»), у другого — строка
        # подписей и строка значений (как в образце). Валидатор обязан
        # понимать оба вида, поэтому здесь проверяется первый.
        sheet.cell(row=20, column=3, value="Заказчик")
        sheet.cell(row=20, column=4, value=CUSTOMER_NAME)
        sheet.cell(row=20, column=10, value="Перевозчик")
        sheet.cell(row=20, column=11, value=CARRIER_NAME)
    book.save(str(path))
    return path


def generate_to(generator: ZayavkaExcelGenerator, data: dict, out_dir: Path) -> Path:
    """Готовый файл заявки; удаляется вызывающим тестом."""
    return Path(generator.generate(data, str(out_dir)))


@pytest.fixture(scope="session", autouse=True)
def clean_staging_dir():
    """Убирает папку тестовых форм после прогона."""
    yield
    import shutil

    shutil.rmtree(STAGING_DIR, ignore_errors=True)


@pytest.fixture
def validator() -> ZayavkaExcelValidator:
    return ZayavkaExcelValidator()


@pytest.fixture
def generator() -> ZayavkaExcelGenerator:
    return ZayavkaExcelGenerator()


@pytest.fixture
def out_dir(work_dir) -> Path:
    path = work_dir / "havaly_validator"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ─────────────────────────────────────────────────────────────
# Контракт типа
# ─────────────────────────────────────────────────────────────

def test_contract_type(validator):
    """Валидатор объявляет тот же тип, что генератор и реестр."""
    assert ZayavkaExcelValidator.CONTRACT_TYPE == "zayavka_excel"
    assert ZayavkaExcelValidator.CONTRACT_TYPE == ZayavkaExcelGenerator.CONTRACT_TYPE
    assert validator.MAX_VEHICLES == MAX_VEHICLES == 10


def test_is_base_validator(validator):
    """Валидатор — подкласс базы (реестр ждёт именно её)."""
    from core.contracts.base_validator import BaseValidator

    assert isinstance(validator, BaseValidator)


def test_common_rules_are_not_applied(validator):
    """
    Общие правила договора-заявки к Excel-форме не применяются.

    core.validator.Validator требует номер и дату договора, стоимость без
    НДС, срок оплаты, тягач и прицеп отдельными блоками — у Хавалов этого
    нет, и ошибок из-за этого быть не должно.
    """
    report = validator.check({"zayavka": {}, "vehicles": []})

    assert report.errors == []
    assert report.warnings
    assert not any("договор" in item.lower() for item in report.errors)


# ─────────────────────────────────────────────────────────────
# Пустая заявка: только замечания
# ─────────────────────────────────────────────────────────────

def test_empty_zayavka_has_only_warnings(validator):
    """Пустой блок заявки — ни одной ошибки, только замечания."""
    report = validator.check({"zayavka": {}, "vehicles": []})

    assert isinstance(report.errors, list) and report.errors == []
    assert report.has_errors is False
    assert report.is_clean is False
    assert len(report.warnings) >= 5


def test_empty_zayavka_names_what_is_missing(validator):
    """Замечания перечисляют, чего не хватает, — без значений."""
    report = validator.check({"zayavka": {}, "vehicles": []})
    text = "\n".join(report.warnings)

    assert "нет ни одной машины" in text
    assert "ставка с НДС" in text
    assert "город погрузки" in text
    assert "фамилия" in text
    assert "Заказчик" in text
    assert "Перевозчик" in text


def test_none_input_does_not_crash(validator):
    """Неожиданный вход (None) — отчёт, а не падение."""
    report = validator.check(None)

    assert report.errors == []
    assert report.warnings


def test_unknown_input_type_does_not_crash(validator):
    """Вход непонятного типа тоже не роняет валидатор."""
    report = validator.check(12345)

    assert report.errors == []
    assert report.warnings


def test_empty_dict_does_not_crash(validator):
    report = validator.check({})

    assert report.errors == []
    assert report.warnings


# ─────────────────────────────────────────────────────────────
# Полная заявка: чисто или почти чисто
# ─────────────────────────────────────────────────────────────

def test_full_zayavka_has_no_errors(validator):
    """Полностью заполненная заявка — без ошибок."""
    report = validator.check(make_data())

    assert report.errors == []


def test_full_zayavka_warnings_are_only_about_data(validator):
    """
    Замечания полной заявки — только про незаполненные необязательные поля.

    Обязательного минимума в форме нет: водителю не нужны ни СНИЛС, ни
    адрес места жительства, поэтому «чисто» здесь означает «ошибок нет»,
    а замечания допустимы и перечисляют, что стоит дозаполнить.
    """
    report = validator.check(make_data())
    text = "\n".join(report.warnings).lower()

    assert "не заполнено" in text or report.warnings == []
    for fragment in ("не заполнена ставка", "нет ни одной машины"):
        assert fragment not in text


# ─────────────────────────────────────────────────────────────
# Границы: 0 / 1 / 10 / 11 машин
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("count", [1, 10])
def test_vehicle_counts_within_limit(validator, count):
    """1 и 10 машин — про вместимость бланка ни слова."""
    report = validator.check(make_data(vehicles=make_vehicles(count)))
    text = "\n".join(report.warnings)

    assert "в бланк помещается" not in text
    assert report.errors == []


def test_zero_vehicles_is_a_warning(validator):
    """0 машин — замечание, не ошибка: заявку сохранить можно."""
    report = validator.check(make_data(vehicles=[]))

    assert report.errors == []
    assert any("нет ни одной машины" in item for item in report.warnings)


def test_eleven_vehicles_is_a_warning_not_an_error(validator):
    """11 машин — замечание про вместимость, а не ошибка."""
    report = validator.check(make_data(vehicles=make_vehicles(11)))
    text = "\n".join(report.warnings)

    assert report.errors == []
    assert "Машин 11" in text
    assert f"помещается {MAX_VEHICLES}" in text
    assert "лишние в файл не попадут" in text


def test_eleven_vehicles_still_reports_vin_problems(validator):
    """Замечания по машинам выдаются и при переполнении бланка."""
    vehicles = make_vehicles(11)
    vehicles[0]["vin"] = "короткий"
    vehicles[1]["vin"] = ""
    report = validator.check(make_data(vehicles=vehicles))
    text = "\n".join(report.warnings)

    assert report.errors == []
    assert "Машина №1: VIN не похож" in text
    assert "Машина №2: не заполнен VIN" in text


# ─────────────────────────────────────────────────────────────
# VIN и ставка
# ─────────────────────────────────────────────────────────────

def test_vin_is_not_required_but_reported(validator):
    """
    VIN не обязателен: без него машина в заявке есть.

    Промпт прямо говорит: если VIN неизвестен, но марка или модель
    заполнены — строку всё равно вернуть, с пустым vin. Валидатор об этом
    напоминает, но ошибкой это не считает.
    """
    vehicles = [{"vin": "", "brand": "HAVAL", "model": "DASHING",
                 "dealer": "Дилер", "dealer_code": "D-1"}]
    report = validator.check(make_data(vehicles=vehicles))
    text = "\n".join(report.warnings)

    assert report.errors == []
    assert "Машина №1: не заполнен VIN" in text


def test_empty_row_without_description_is_silent(validator):
    """
    Пустая строка (нет ни VIN, ни марки) — это не машина.

    Про неё замечания нет: такие строки пропускает и распознавание
    (промпт: «строку, где не заполнено ни одно из пяти полей, пропускай»).
    """
    vehicles = [
        {"vin": "", "brand": "", "model": "", "dealer": "", "dealer_code": ""},
        make_vehicles(1)[0],
    ]
    report = validator.check(make_data(vehicles=vehicles))
    text = "\n".join(report.warnings)

    assert report.errors == []
    assert "Машина №1" not in text
    assert "Машина №2" not in text


def test_unusual_vin_is_a_warning(validator):
    """VIN не по стандарту (17 символов, без I/O/Q) — замечание."""
    vehicles = make_vehicles(1)
    vehicles[0]["vin"] = "EC3TEUMB0T000260"      # 16 символов
    report = validator.check(make_data(vehicles=vehicles))

    assert report.errors == []
    assert any("VIN не похож на стандартный" in item for item in report.warnings)


def test_empty_price_is_a_warning(validator):
    """Ставка не заполнена (0.0) — замечание, не ошибка."""
    report = validator.check(make_data(price_with_vat=0.0))

    assert report.errors == []
    assert any("ставка с НДС" in item for item in report.warnings)


@pytest.mark.parametrize("value", ["1 234,56 руб.", 1234.56, "1234.56"])
def test_price_formats_are_accepted(validator, value):
    """Ставка в любом виде («1 234,56 руб.», число) замечаний не вызывает."""
    report = validator.check(make_data(price_with_vat=value))
    text = "\n".join(report.warnings)

    assert report.errors == []
    assert "ставка с НДС" not in text


# ─────────────────────────────────────────────────────────────
# Стороны
# ─────────────────────────────────────────────────────────────

def test_missing_fixed_parties_are_warnings(validator):
    """
    Стороны фиксированы, но их отсутствие — замечание, не ошибка.

    Стороны напечатаны в бланке, поэтому заявка сохранится и без них;
    предупреждаем только о том, что данные пришли не от распознавания.
    """
    report = validator.check(
        make_data(customer_name="", carrier_name="")
    )
    text = "\n".join(report.warnings)

    assert report.errors == []
    assert "фиксированная сторона «Заказчик»" in text
    assert "фиксированная сторона «Перевозчик»" in text


def test_present_fixed_parties_are_silent(validator):
    """Заполненные стороны замечаний не вызывают."""
    report = validator.check(make_data())
    text = "\n".join(report.warnings)

    assert "фиксированная сторона" not in text


# ─────────────────────────────────────────────────────────────
# Даты и время
# ─────────────────────────────────────────────────────────────

def test_broken_date_is_a_warning(validator):
    """Дата, которая не разбирается, — замечание с названием поля."""
    report = validator.check(make_data(driver_birth_date="не помню"))

    assert report.errors == []
    assert "Не разобрана дата: дата рождения" in report.warnings


@pytest.mark.parametrize("value", ["05.10.2026", "2026-10-05", "05/10/2026"])
def test_understandable_dates_are_silent(validator, value):
    """Понятные даты замечаний не вызывают."""
    report = validator.check(make_data(date=value, loading_plan_date=value))

    assert not any("Не разобрана дата" in item for item in report.warnings)


def test_words_instead_of_date_are_a_warning(validator):
    """
    «5 октября» — замечание: core/dates.py такие даты не разбирает.

    Это не ошибка типа: текст попадёт в файл как есть, и пользователь
    увидит, что дату стоит привести к обычному виду.
    """
    report = validator.check(make_data(driver_birth_date="5 октября 1980"))

    assert report.errors == []
    assert "Не разобрана дата: дата рождения" in report.warnings


def test_broken_time_is_a_warning(validator):
    """Время не в формате «ЧЧ:ММ» — замечание."""
    report = validator.check(make_data(loading_plan_time="утром"))

    assert report.errors == []
    assert any("ЧЧ:ММ" in item for item in report.warnings)


@pytest.mark.parametrize("value", ["09:00", "9:00", "09:00:00"])
def test_understandable_time_is_silent(validator, value):
    """
    Время в понятном виде замечаний не вызывает.

    «09:00:00» приходит, если в присланном файле время хранится
    полноценным значением Excel.
    """
    report = validator.check(make_data(loading_plan_time=value))

    assert not any("ЧЧ:ММ" in item for item in report.warnings)


# ─────────────────────────────────────────────────────────────
# Структурные ошибки: файл
# ─────────────────────────────────────────────────────────────

def test_missing_file_is_an_error(validator, out_dir):
    """Нет файла — ошибка с путём."""
    report = validator.check(str(out_dir / "нет-такого.xlsx"))

    assert report.has_errors
    assert any("не найден" in item for item in report.errors)


def test_file_without_tdsheet_is_an_error(validator, out_dir):
    """Нет листа TDSheet — ошибка, и она одна из немногих."""
    path = make_form(_staging("no_tdsheet.xlsx"), sheet_name="Лист1")

    report = validator.check(str(path))

    assert report.has_errors
    assert any("нет листа TDSheet" in item for item in report.errors)


def test_file_without_header_is_an_error(validator, out_dir):
    """Нет строки шапки — ошибка."""
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_NAME
    sheet["A1"] = "Другой документ"
    path = _staging("no_header.xlsx")
    book.save(str(path))

    report = validator.check(str(path))

    assert report.has_errors
    assert any("шапк" in item for item in report.errors)


def test_broken_file_is_an_error(validator, out_dir):
    """Не-xlsx внутри .xlsx — ошибка, а не исключение."""
    path = _staging("broken.xlsx")
    path.write_bytes("это не архив".encode("utf-8"))

    report = validator.check(str(path))

    assert report.has_errors
    assert any("Не удалось открыть" in item for item in report.errors)


def test_valid_file_has_no_errors(validator, generator, out_dir):
    """Хороший файл (наш бланк) — без ошибок."""
    report = validator.check(generator.template_path())

    assert report.errors == []
    assert report.warnings


def test_sample_file_has_no_errors(validator, templates_dir):
    """Присланный образец — тоже без ошибок (машин в нём нет — замечание)."""
    sample = templates_dir / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    report = validator.check(str(sample))

    assert report.errors == []
    assert any("нет ни одной машины" in item for item in report.warnings)


def test_generated_file_has_no_errors(validator, generator, out_dir):
    """Сгенерированный файл валидатор считает правильным."""
    path = generate_to(generator, make_data(), out_dir)
    try:
        report = validator.check(str(path))

        assert report.errors == []
        assert "нет ни одной машины" not in "\n".join(report.warnings)
    finally:
        path.unlink(missing_ok=True)


def test_party_block_of_foreign_file_is_reported(validator, out_dir):
    """
    Файл без блока сторон — замечание (ошибки нет).

    Шапка при этом на месте: без неё файл непригоден целиком, и это уже
    другая ошибка (см. test_file_without_header_is_an_error).
    """
    path = make_form(_staging("no_party.xlsx"), party=False, date=False)

    report = validator.check(str(path))

    assert report.errors == []
    assert any("нет блока сторон" in item for item in report.warnings)


def test_other_parties_in_file_are_reported(validator, out_dir):
    """
    В блоке сторон указаны чужие организации — замечание.

    Стороны в бланке напечатаны и в файл не пишутся, поэтому сохранению
    это не мешает, но признак чужой формы заметить стоит.
    """
    path = make_form(_staging("other_party.xlsx"), date=False)
    book = load_workbook(path)
    sheet = book[SHEET_NAME]
    sheet.cell(row=20, column=4, value="ООО «Совсем другой заказчик»")
    sheet.cell(row=20, column=11, value="ООО «Другой перевозчик»")
    book.save(str(path))

    report = validator.check(str(path))
    text = "\n".join(report.warnings)

    assert report.errors == []
    assert "под «Заказчик» указана другая организация" in text
    assert "под «Перевозчик» указана другая организация" in text


def test_party_labels_and_values_in_separate_rows(validator, out_dir):
    """
    Блок сторон в два ряда (как в образце заказчика) тоже проверяется.

    В образце подписи стоят в одной строке, а названия организаций — под
    ними. Если значение стоит НЕ рядом с подписью, про чужую организацию
    сказать нечего: об этом честно молчим, и это не ошибка.
    """
    path = make_form(_staging("two_row_party.xlsx"), date=False)
    book = load_workbook(path)
    sheet = book[SHEET_NAME]
    sheet.cell(row=20, column=4, value=None)
    sheet.cell(row=20, column=11, value=None)
    sheet.cell(row=21, column=3, value=CUSTOMER_NAME)
    sheet.cell(row=21, column=10, value=CARRIER_NAME)
    book.save(str(path))

    report = validator.check(str(path))

    assert report.errors == []
    assert not any("другая организация" in item for item in report.warnings)


def test_broken_file_does_not_stop_data_checks(validator, out_dir):
    """
    Битый файл не мешает остальным проверкам: отчёт собирается целиком.

    Данных из файла нет, поэтому проверки содержимого дают замечания —
    и это правильно: пользователь видит и ошибку структуры, и то, что
    заполнять нечего.
    """
    path = _staging("broken2.xlsx")
    path.write_bytes("не архив".encode("utf-8"))

    report = validator.check(str(path))

    assert report.has_errors
    assert any("нет ни одной машины" in item for item in report.warnings)


# ─────────────────────────────────────────────────────────────
# Вход: путь, схема промпта, плоский словарь, ContractData
# ─────────────────────────────────────────────────────────────

def test_accepts_prompt_schema(validator):
    """Схема промпта A.2 принимается как есть."""
    report = validator.check(make_data())

    assert report.errors == []


def test_accepts_flat_dict(validator):
    """Плоский словарь (сборщик UI) тоже принимается."""
    flat = dict(make_zayavka())
    flat["vehicles"] = make_vehicles(2)

    report = validator.check(flat)

    assert report.errors == []
    assert "нет ни одной машины" not in "\n".join(report.warnings)


def test_accepts_contract_data_object(validator):
    """
    ContractData принимается: поля заявки валидатор читает из contract.

    Отдельно проверяется, что блок "zayavka" не теряется: coerce знает
    только свои поля, поэтому валидатор переносит поля заявки в contract
    сам (см. ZayavkaExcelValidator._as_contract).
    """
    data = make_data()
    cd = ContractData(contract=data["zayavka"], vehicles=data["vehicles"])

    report = validator.check(cd)

    assert report.errors == []
    text = "\n".join(report.warnings)
    assert "нет ни одной машины" not in text
    assert "фиксированная сторона" not in text


def test_path_like_string_is_read_as_file(validator, out_dir):
    """Строка, похожая на путь к .xlsx, читается как файл."""
    path = generate_to(ZayavkaExcelGenerator(), make_data(), out_dir)
    try:
        report = validator.check(str(path))
        assert report.errors == []
    finally:
        path.unlink(missing_ok=True)


def test_text_that_is_not_a_path_is_not_read(validator):
    """Обычный текст файлом не считается: валидатор не ищет его на диске."""
    assert validator._is_path("Сюрлогистик") is False
    assert validator._is_path("заявка.xlsx") is False


def test_path_objects_are_accepted(validator, generator, out_dir):
    """pathlib.Path тоже принимается как путь."""
    path = generate_to(generator, make_data(), out_dir)
    try:
        report = validator.check(path)
        assert report.errors == []
    finally:
        path.unlink(missing_ok=True)


def test_file_read_error_becomes_report_not_exception(validator, out_dir):
    """
    Любая неудача чтения — отчёт, а не исключение наружу.

    Валидатор вызывают перед генерацией: падение здесь сломало бы весь
    сценарий, поэтому ошибка обязана превратиться в строку отчёта.
    """
    path = _staging("directory.xlsx")
    path.mkdir(parents=True, exist_ok=True)
    try:
        report = validator.check(str(path))
        assert report.has_errors
    finally:
        path.rmdir()


# ─────────────────────────────────────────────────────────────
# Валидатор не блокирует генерацию
# ─────────────────────────────────────────────────────────────

def test_validator_does_not_block_generate(validator, generator, out_dir):
    """Даже с замечаниями генерация проходит и файл сохраняется."""
    data = make_data(vehicles=[], price_with_vat=0.0,
                     customer_name="", carrier_name="")
    report = validator.check(data)

    assert report.errors == []          # блокировать нечем
    assert len(report.warnings) >= 3

    path = generate_to(generator, data, out_dir)
    try:
        assert path.exists()
        assert path.stat().st_size > 0
    finally:
        path.unlink(missing_ok=True)


def test_validator_does_not_block_fill_from_template(validator, generator,
                                                     templates_dir, out_dir):
    """Присланный файл заполняется независимо от отчёта валидатора."""
    sample = templates_dir / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    data = make_data(vehicles=[])
    report = validator.check(data)
    assert report.errors == []

    path = Path(generator.fill_from_template(str(sample), data, str(out_dir)))
    try:
        assert path.exists()
    finally:
        path.unlink(missing_ok=True)


def test_validator_does_not_change_data(validator):
    """Валидатор данные не портит: генератор получает то же самое."""
    import copy

    data = make_data()
    snapshot = copy.deepcopy(data)

    validator.check(data)

    assert data == snapshot


def test_eleven_vehicles_are_still_written(validator, generator, out_dir):
    """
    11 машин: валидатор предупреждает, генератор пишет первые 10.

    Это и есть «не мешает работе»: замечание не отменяет сохранение файла.
    """
    data = make_data(vehicles=make_vehicles(11))
    report = validator.check(data)
    assert report.errors == []

    path = generate_to(generator, data, out_dir)
    try:
        back = generator.read_template(str(path))
        assert len(back["vehicles"]) == MAX_VEHICLES
    finally:
        path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────
# Логи: без ПДн
# ─────────────────────────────────────────────────────────────

#: Значения синтетических ПДн: их не должно быть в логах.
PII_FRAGMENTS = (
    "Тестов", "Тестович", "9901", "926830", "1822",
    "EC3TEUMB0T0002601", "Промышленная", "Складской", "Тестовая",
    "О844ХУ196", "71ABF18", "+7 (999)",
)


def test_logs_have_no_personal_data(validator, caplog):
    """В логе валидации — только счётчики, ни одного значения."""
    with caplog.at_level(logging.DEBUG,
                         logger="core.contracts.zayavka.validator"):
        validator.check(make_data())

    messages = "\n".join(
        record.getMessage() for record in caplog.records
        if record.name.startswith("core.contracts.zayavka.validator")
    )
    assert messages
    assert "машин=2" in messages
    for fragment in PII_FRAGMENTS:
        assert fragment not in messages, f"в логе есть «{fragment}»"


def test_logs_of_file_check_have_no_personal_data(validator, generator,
                                                  out_dir, caplog):
    """Проверка файла тоже логируется без значений ячеек."""
    path = generate_to(generator, make_data(), out_dir)
    try:
        with caplog.at_level(logging.DEBUG,
                             logger="core.contracts.zayavka.validator"):
            validator.check(str(path))
    finally:
        path.unlink(missing_ok=True)

    messages = "\n".join(
        record.getMessage() for record in caplog.records
        if record.name.startswith("core.contracts.zayavka.validator")
    )
    for fragment in PII_FRAGMENTS:
        assert fragment not in messages, f"в логе есть «{fragment}»"


def test_logs_do_not_contain_column_names_as_data(validator, out_dir, caplog):
    """В логе структуры — ни значений, ни чужих данных из файла."""
    path = make_form(
        _staging("log_check.xlsx"),
        rows=[["СЕКРЕТНОЕ-ЗНАЧЕНИЕ"] * len(HEADERS)],
    )
    with caplog.at_level(logging.DEBUG):
        validator.check(str(path))

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "СЕКРЕТНОЕ-ЗНАЧЕНИЕ" not in messages


# ─────────────────────────────────────────────────────────────
# Стык с генератором
# ─────────────────────────────────────────────────────────────

def test_validator_reads_generator_keys(validator, generator, out_dir):
    """
    Стык «генератор → валидатор»: валидатор понимает то, что записал
    генератор.

    Файл генерируется из данных, потом читается валидатором как файл —
    и замечаний становится не больше, чем было по исходным данным
    (расхождение означало бы, что генератор и валидатор разошлись
    в именах полей).
    """
    data = make_data()
    direct = validator.check(data)

    path = generate_to(generator, data, out_dir)
    try:
        from_file = validator.check(str(path))
    finally:
        path.unlink(missing_ok=True)

    assert from_file.errors == direct.errors == []
    assert len(from_file.warnings) == len(direct.warnings)


def test_validator_uses_generator_reader(validator):
    """
    Валидатор читает формы генератором, а не своим парсером.

    Так исключено расхождение: границы листа, шапка и блок сторон
    разбираются ровно тем же кодом, что и при заполнении.
    """
    assert isinstance(validator._generator(), ZayavkaExcelGenerator)


def test_structure_error_text_matches_generator(validator, out_dir):
    """Структурная ошибка валидатора — та же, что у генератора."""
    path = make_form(_staging("no_tdsheet2.xlsx"), sheet_name="Лист1")

    report = validator.check(str(path))
    generator = ZayavkaExcelGenerator()
    with pytest.raises(ZayavkaTemplateError) as error:
        generator.read_template(str(path))

    assert str(error.value) in report.errors
