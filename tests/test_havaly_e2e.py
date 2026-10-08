#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сводные (интеграционные) тесты Excel-заявки Хавалов (ЭТАП 3.1.E.A.3).

Здесь проверяется вся цепочка целиком:

    данные → валидатор → генератор → файл в output/ → чтение обратно

а не отдельные модули: у них свои тесты
(tests/test_havaly_validator.py, tests/test_havaly_generator.py,
tests/test_havaly_template.py, tests/test_prompts_havaly.py). Цель этих
тестов — СТЫКИ, где модули договариваются об именах полей и о форме
данных: расхождение там не видно ни одному модульному тесту.

Что проверяется:

  1. «промпт → генератор»: имена ключей JSON в схеме ответа
     (core/prompts/havaly.py) — те же, что генератор кладёт в файл и
     возвращает при чтении. Плейсхолдеры распознавания (§A.2) доезжают
     до файла и возвращаются как есть.
  2. «валидатор → генератор»: валидатор не блокирует работу, не портит
     данные, а генератор не падает на проверенных данных.
  3. «генератор → файл → чтение»: полный круг на 0/1/2/10/11 машинах.
  4. «фабрика → генератор»: тип zayavka_excel из реестра даёт тот же
     генератор, что и прямой импорт, и он не наследуется от DOCX-базы.
  5. «присланный файл → файл»: fill_from_template на образце заказчика
     сохраняет его оформление и заполняет данные.
  6. Логи всей цепочки — без ПДн.

Все данные синтетические, реальных ПДн нет. Выходные файлы создаются
в tests/_tmp и удаляются в finally.
"""

import logging
import shutil
import sys
import uuid
from pathlib import Path

import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.contract_data import ContractData  # noqa: E402
from core.contracts.base_generator import BaseContractGenerator  # noqa: E402
from core.contracts.contract_types import ContractType  # noqa: E402
from core.contracts.factory import GeneratorFactory  # noqa: E402
from core.contracts.registry import ContractTypeRegistry  # noqa: E402
from core.contracts.zayavka.generator import (  # noqa: E402
    CARRIER_NAME,
    CUSTOMER_NAME,
    FILE_PREFIX,
    HEADERS,
    HEADER_OF,
    MAX_VEHICLES,
    SHEET_NAME,
    TEMPLATE_NAME,
    VEHICLE_KEYS,
    ZAYAVKA_KEYS,
    ZayavkaExcelGenerator,
)
from core.contracts.zayavka.postprocess import TrimVehicleRowsStep  # noqa: E402
from core.contracts.zayavka.validator import ZayavkaExcelValidator  # noqa: E402
from core.prompts import get_prompt  # noqa: E402
from core.prompts.havaly import PROMPT  # noqa: E402

SAMPLE_NAME = "Хавалы_образец.xlsx"

#: Папка тестовых форм и метка прогона.
STAGING_DIR = Path(__file__).resolve().parent / "_tmp" / "_havaly_forms"
_RUN_ID = uuid.uuid4().hex[:8]

DATE = "05.10.2026"

#: Плейсхолдеры распознавания: их подставляет pseudonymizer, и они должны
#: доехать до файла ровно в том виде, в каком пришли.
PLACEHOLDER_FIO = "<<PERSON_1>>"
PLACEHOLDER_PHONE = "<<PHONE_1>>"

#: Отпечатки синтетических ПДн: их не должно быть в логах.
PII_FRAGMENTS = (
    "Тестов", "Тестович", "9901", "926830", "1822",
    "EC3TEUMB0T0002601", "Промышленная", "Складской", "Тестовая",
    "О844ХУ196", "71ABF18", "+7 (999)", "123456.78",
)


def make_zayavka(**overrides) -> dict:
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
    return {
        "zayavka": make_zayavka(**overrides),
        "vehicles": vehicles if vehicles is not None else make_vehicles(2),
    }


def staging(name: str) -> Path:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    return STAGING_DIR / f"{_RUN_ID}_e2e_{name}"


@pytest.fixture(scope="session", autouse=True)
def clean_staging_dir():
    """Убирает папку тестовых форм после прогона."""
    yield
    shutil.rmtree(STAGING_DIR, ignore_errors=True)


@pytest.fixture
def generator() -> ZayavkaExcelGenerator:
    return ZayavkaExcelGenerator()


@pytest.fixture
def validator() -> ZayavkaExcelValidator:
    return ZayavkaExcelValidator()


@pytest.fixture
def out_dir(work_dir) -> Path:
    """
    Папка вывода теста.

    Отдельная от output/ проекта: тесты не должны оставлять файлы рядом
    с рабочими договорами. Проверка «файл попадает в output» делается
    отдельно, на пути по умолчанию (см. test_default_output_dir).
    """
    path = work_dir / "havaly_e2e"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def saved(generator, out_dir):
    """Готовые файлы теста: удаляются после проверки (вместе с папкой рейса)."""
    created: list = []

    def _generate(data, method="generate", source=None):
        if method == "generate":
            path = generator.generate(data, str(out_dir))
        else:
            path = generator.fill_from_template(
                source or generator.template_path(), data, str(out_dir)
            )
        created.append(Path(path))
        return Path(path)

    yield _generate

    for path in created:
        # Папка рейса — тоже за тестом: имя папки содержит фамилию водителя,
        # и оставлять её в tests/_tmp от прогона к прогону незачем.
        shutil.rmtree(path.parent, ignore_errors=True)


def read_back(generator, path) -> dict:
    return generator.read_template(str(path))


def drop(path) -> None:
    """Убирает готовый файл вместе с папкой рейса."""
    shutil.rmtree(Path(path).parent, ignore_errors=True)


# ─────────────────────────────────────────────────────────────
# Стык 1: промпт → генератор
# ─────────────────────────────────────────────────────────────

def test_prompt_is_available_for_type():
    """Промпт типа отдаётся тем же ключом, что объявляет генератор."""
    assert get_prompt(ZayavkaExcelGenerator.CONTRACT_TYPE) is PROMPT
    assert "СХЕМА ОТВЕТА" in PROMPT


def test_generator_keys_are_prompt_keys(generator, saved):
    """
    Ключи JSON у генератора и у промпта — одни и те же.

    Промпт — единый источник имён (решение A.2). Генератор не должен
    заводить свои имена: файл читает и пишет именно этот словарь.
    """
    import json

    schema_text = PROMPT[PROMPT.index("{", PROMPT.index("СХЕМА ОТВЕТА")):]
    for heading in ("ПЛЕЙСХОЛДЕРЫ", "Плейсхолдеры"):
        cut = schema_text.find(heading)
        if cut != -1:
            schema_text = schema_text[:cut]
    schema = json.loads(schema_text[:schema_text.rindex("}") + 1])

    path = saved(make_data())
    back = read_back(generator, path)

    assert set(back["zayavka"]) == set(schema["zayavka"])
    assert set(back["vehicles"][0]) == set(schema["vehicles"][0])
    assert set(back["vehicles"][0]) == set(VEHICLE_KEYS)
    assert set(back["zayavka"]) - set(ZAYAVKA_KEYS) == {
        "vat_rate", "customer_name", "carrier_name"
    }


def test_placeholders_survive_the_round_trip(generator, saved):
    """
    Плейсхолдеры распознавания доезжают до файла как есть.

    Водитель может прийти обезличенным (<<PERSON_1>>, <<PHONE_1>>):
    генератор обязан положить их в ячейки без изменений — раскрывает
    плейсхолдеры pseudonymizer, а не форма.
    """
    data = make_data(
        vehicles=make_vehicles(1),
        driver_last_name=PLACEHOLDER_FIO,
        driver_first_name="",
        driver_middle_name="",
        driver_phone=PLACEHOLDER_PHONE,
    )
    path = saved(data)
    back = read_back(generator, path)

    assert back["zayavka"]["driver_last_name"] == PLACEHOLDER_FIO
    assert back["zayavka"]["driver_first_name"] == ""
    assert back["zayavka"]["driver_middle_name"] == ""
    assert back["zayavka"]["driver_phone"] == PLACEHOLDER_PHONE


def test_vin_stub_from_prompt_is_not_written(generator, saved):
    """Заглушка VIN из промпта даёт пустую ячейку и пустой VIN при чтении."""
    vehicles = make_vehicles(1)
    vehicles[0]["vin"] = "Vin по факту погрузки"
    path = saved(make_data(vehicles=vehicles))
    back = read_back(generator, path)

    assert back["vehicles"][0]["vin"] == ""
    assert back["vehicles"][0]["brand"] == vehicles[0]["brand"]


# ─────────────────────────────────────────────────────────────
# Стык 2: валидатор → генератор
# ─────────────────────────────────────────────────────────────

def test_validator_passes_valid_data(validator):
    report = validator.check(make_data())

    assert report.errors == []


def test_validator_does_not_block_the_chain(validator, generator, saved):
    """Полный круг проходит, даже если валидатор полон замечаний."""
    data = make_data(vehicles=[], price_with_vat=0.0,
                     customer_name="", carrier_name="")
    report = validator.check(data)
    assert report.errors == []
    assert report.warnings

    path = saved(data)
    back = read_back(generator, path)

    assert path.exists()
    assert back["vehicles"] == []


def test_validator_reads_what_generator_wrote(validator, generator, saved):
    """Файл генератора валидатор считает правильным — без ошибок."""
    path = saved(make_data())
    report = validator.check(str(path))

    assert report.errors == []


def test_validator_report_matches_generator_warnings(validator, generator,
                                                     saved):
    """Обрезку машин видит и валидатор, и генератор — как замечание."""
    data = make_data(vehicles=make_vehicles(11))

    report = validator.check(data)
    assert report.errors == []
    assert any("Машин 11" in item for item in report.warnings)

    path = saved(data)
    assert len(read_back(generator, path)["vehicles"]) == MAX_VEHICLES


def test_validator_accepts_contract_data(validator):
    """ContractData проходит цепочку: поля заявки не теряются."""
    data = make_data()
    cd = ContractData(contract=data["zayavka"], vehicles=data["vehicles"])

    report = validator.check(cd)

    assert report.errors == []
    assert "нет ни одной машины" not in "\n".join(report.warnings)


# ─────────────────────────────────────────────────────────────
# Стык 3: генератор → файл → чтение
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("count", [0, 1, 2, 10, 11])
def test_full_cycle_for_vehicle_counts(generator, saved, count):
    """
    Полный цикл на 0/1/2/10/11 машинах.

    Данные заявки возвращаются все, кроме полей, которых в бланке нет
    (vat_rate), а машин — не больше десяти.
    """
    data = make_data(vehicles=make_vehicles(count))
    path = saved(data)
    back = read_back(generator, path)

    assert len(back["vehicles"]) == min(count, MAX_VEHICLES)
    assert back["vehicles"] == make_vehicles(min(count, MAX_VEHICLES))

    expected = dict(data["zayavka"])
    expected["vat_rate"] = ""       # в бланке нет ячейки для ставки НДС
    if not count:
        # Без машин общие сведения писать некуда: они стоят колонками
        # таблицы. Остаётся только дата заявки, стороны (они фиксированы)
        # и числовой ноль в ставке — «ставка не указана».
        expected = {key: "" for key in expected}
        expected["date"] = data["zayavka"]["date"]
        expected["price_with_vat"] = 0.0
        expected["customer_name"] = CUSTOMER_NAME
        expected["carrier_name"] = CARRIER_NAME
    assert back["zayavka"] == expected


def test_file_name_and_folder(generator, saved):
    """
    Имя и место файла: <папка рейса>/Заявка_Хавалы_<дата ISO>.xlsx.

    Имя файла не изменилось (зависит только от даты заявки), а лежит он
    теперь в папке рейса внутри папки вывода (ШАГ «Папка на рейс»).
    """
    path = saved(make_data())

    assert path.name == f"{FILE_PREFIX}_2026-10-05.xlsx"
    assert path.suffix == ".xlsx"
    assert path.parent.name == "Тестов_Т.Т._Калуга-Москва_05.10.2026"
    assert path.parent.parent.name == "havaly_e2e"     # папка вывода теста


def test_default_output_dir_is_project_output(generator):
    """По умолчанию файл идёт в output/ проекта (там же, где договоры)."""
    output_dir = Path(generator.default_output_dir())

    assert output_dir.name == "output"
    assert output_dir == Path(generator.template_path()).parent.parent / "output"


def test_file_is_valid_workbook_with_single_sheet(generator, saved):
    """Готовый файл — обычная книга с единственным листом TDSheet."""
    path = saved(make_data())
    book = load_workbook(path)

    assert book.sheetnames == [SHEET_NAME]
    assert book[SHEET_NAME].sheet_state == "visible"


def test_styles_only_on_filled_cells(generator, saved):
    """Даты записаны датами, длинный текст — с переносом, ставка — числом."""
    path = saved(make_data(vehicles=make_vehicles(1)))
    sheet = load_workbook(path)[SHEET_NAME]

    assert sheet["B5"].number_format == "DD.MM.YYYY"
    assert sheet["U7"].number_format == "DD.MM.YYYY"
    assert sheet["AD7"].number_format == "h:mm"
    assert sheet["F7"].alignment.wrap_text is True
    assert sheet["AF7"].value == pytest.approx(123456.78)


def test_template_is_not_changed_by_generation(generator, saved):
    """Эталонный бланк генерация не меняет."""
    import hashlib

    template = Path(generator.template_path())
    before = hashlib.sha256(template.read_bytes()).hexdigest()

    saved(make_data(vehicles=make_vehicles(3)))

    assert hashlib.sha256(template.read_bytes()).hexdigest() == before


# ─────────────────────────────────────────────────────────────
# Стык 4: фабрика → генератор
# ─────────────────────────────────────────────────────────────

def test_registry_knows_type():
    """Тип zayavka_excel зарегистрирован и виден реестру."""
    ContractTypeRegistry.load_builtin()
    if ContractType.ZAYAVKA_EXCEL.value not in ContractTypeRegistry.known_types():
        import importlib

        importlib.reload(
            importlib.import_module("core.contracts.zayavka")
        )

    assert ContractType.ZAYAVKA_EXCEL.value in ContractTypeRegistry.known_types()
    spec = ContractTypeRegistry.get(ContractType.ZAYAVKA_EXCEL.value)
    assert spec.title == ZayavkaExcelGenerator.TITLE
    assert spec.generator_class is ZayavkaExcelGenerator
    assert spec.validator_class is ZayavkaExcelValidator


def test_factory_builds_working_generator(generator, out_dir):
    """
    Генератор из фабрики работает так же, как прямой.

    Фабрика передаёт templates_dir — генератор обязан принять его
    контрактом (как DOCX-типы), иначе UI не сможет его создать.
    """
    built = GeneratorFactory.get_generator("zayavka_excel", strict=True)

    assert isinstance(built, ZayavkaExcelGenerator)
    assert built.template_path() == generator.template_path()

    path = Path(built.generate(make_data(), str(out_dir)))
    try:
        back = built.read_template(str(path))
        assert back["vehicles"] == make_vehicles(2)
    finally:
        drop(path)


def test_factory_builds_validator():
    """Фабрика отдаёт валидатор типа."""
    built = GeneratorFactory.get_validator("zayavka_excel", strict=True)

    assert isinstance(built, ZayavkaExcelValidator)


def test_generator_is_not_docx_based(generator):
    """Excel-заявка не наследуется от DOCX-базы (решение по аудиту)."""
    assert not isinstance(generator, BaseContractGenerator)
    assert not issubclass(ZayavkaExcelGenerator, BaseContractGenerator)


def test_generator_contract_matches_docx_types(generator):
    """
    У генератора есть имена, которые читает UI у DOCX-типов.

    UI обращается к TEMPLATE_NAMES и default_output_dir(): без них тип
    нельзя было бы подключить к общему окну.
    """
    assert ZayavkaExcelGenerator.TEMPLATE_NAMES == {"zayavka": TEMPLATE_NAME}
    assert generator.templates["zayavka"] == generator.template_path()
    assert callable(generator.default_output_dir)
    assert generator.MAX_VEHICLES == MAX_VEHICLES


def test_postprocess_pipeline_is_declared(generator):
    """Конвейер постобработки объявлен и содержит шаг обрезки строк."""
    steps = generator.postprocess_steps()

    assert [step.name for step in steps] == [TrimVehicleRowsStep.name]
    assert all(isinstance(step, TrimVehicleRowsStep) for step in steps)


# ─────────────────────────────────────────────────────────────
# Стык 5: присланный файл → готовый файл
# ─────────────────────────────────────────────────────────────

def test_fill_from_sample_end_to_end(generator, templates_dir, saved):
    """
    Полный круг на образце заказчика: файл сохранён, читается, данные на месте.

    В образце строки данных пустые (шесть размеченных), поэтому здесь
    проверяется и то, что данные встают в его строки, и то, что чтение
    возвращает именно их.
    """
    sample = templates_dir / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    data = make_data(vehicles=make_vehicles(2))
    path = saved(data, method="fill", source=str(sample))

    assert path.exists()
    back = read_back(generator, path)

    assert back["vehicles"] == data["vehicles"]
    assert back["zayavka"]["loading_city"] == data["zayavka"]["loading_city"]
    assert back["zayavka"]["driver_phone"] == data["zayavka"]["driver_phone"]
    assert back["zayavka"]["price_with_vat"] == pytest.approx(
        data["zayavka"]["price_with_vat"]
    )


def test_fill_from_sample_keeps_its_look(generator, templates_dir, saved):
    """Оформление присланного файла не ломается: шрифты, рамки, ширины."""
    sample = templates_dir / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    before = load_workbook(str(sample))[SHEET_NAME]
    fonts = {
        coordinate: (before[coordinate].font.name, before[coordinate].font.size)
        for coordinate in ("A7", "B7", "Q7", "AF7")
    }
    borders = {
        coordinate: before[coordinate].border.left.style
        for coordinate in ("A7", "B7", "Q7", "AF7")
    }
    widths = {key: item.width for key, item in before.column_dimensions.items()}

    path = saved(make_data(vehicles=make_vehicles(2)), method="fill",
                 source=str(sample))
    after = load_workbook(path)[SHEET_NAME]

    for coordinate, font in fonts.items():
        assert (after[coordinate].font.name, after[coordinate].font.size) == font
        assert after[coordinate].border.left.style == borders[coordinate]
    for key, width in widths.items():
        assert after.column_dimensions[key].width == width


def test_fill_from_sample_moves_parties_up(generator, templates_dir, saved):
    """Лишние строки образца удалены, блок сторон поднялся вслед за ними."""
    sample = templates_dir / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    path = saved(make_data(vehicles=make_vehicles(2)), method="fill",
                 source=str(sample))
    sheet = load_workbook(path)[SHEET_NAME]
    values = {
        cell.value: cell.row
        for row in sheet.iter_rows() for cell in row if cell.value is not None
    }

    # Шапка 6 + 2 строки данных + две пустые строки бланка = 11.
    assert values["Заказчик"] == 11
    assert values["Сюрлогистик"] == 12
    assert values["ФИО, подпись, печать"] == 15


def test_sample_is_not_modified(generator, templates_dir, saved):
    """Присланный файл читается, но не перезаписывается."""
    import hashlib

    sample = templates_dir / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    before = hashlib.sha256(sample.read_bytes()).hexdigest()
    saved(make_data(), method="fill", source=str(sample))

    assert hashlib.sha256(sample.read_bytes()).hexdigest() == before


def test_generated_file_can_be_filled_again(generator, out_dir):
    """
    Готовый файл годится как бланк для следующей заявки.

    Так выглядит рабочий сценарий: заказчик вернул заполненную форму,
    а из неё делают следующую заявку. Дата заявки во второй раз другая —
    иначе имя файла совпало бы с исходным (генератор такое запрещает,
    см. test_same_file_is_refused).
    """
    first = Path(generator.generate(make_data(vehicles=make_vehicles(2)),
                                    str(out_dir)))
    second_data = make_data(vehicles=make_vehicles(1), lot_number="LOT-43",
                            loading_city="Тула", date="06.10.2026")
    second = Path(generator.fill_from_template(str(first), second_data,
                                               str(out_dir)))
    try:
        back = generator.read_template(str(second))
        assert back["vehicles"] == second_data["vehicles"]
        assert back["zayavka"]["lot_number"] == "LOT-43"
        assert back["zayavka"]["loading_city"] == "Тула"
        assert second.name == f"{FILE_PREFIX}_2026-10-06.xlsx"
    finally:
        drop(first)
        drop(second)


def test_same_file_is_refused(generator, out_dir):
    """
    Генератор не перезаписывает исходный файл.

    Имя вывода зависит только от даты заявки, поэтому теоретически может
    совпасть с именем источника — тогда запись затёрла бы присланный бланк.
    Вместо порчи файла генератор говорит об этом прямо.
    """
    from core.contracts.zayavka.generator import ZayavkaTemplateError

    first = Path(generator.generate(make_data(vehicles=make_vehicles(1)),
                                    str(out_dir)))
    try:
        with pytest.raises(ZayavkaTemplateError, match="совпадает с исходным"):
            generator.fill_from_template(str(first), make_data(), str(out_dir))
        # Файл цел: его не затёрли.
        assert generator.read_template(str(first))["vehicles"] == make_vehicles(1)
    finally:
        drop(first)


def test_round_trip_of_foreign_column_order(generator, out_dir):
    """
    Чужой порядок колонок проходит всю цепочку.

    Собирается форма с ПЕРЕСТАВЛЕННЫМИ колонками (как присылают разные
    заказчики), заполняется и читается обратно: данные должны вернуться
    теми же — значит, матчинг по заголовкам работает на всём пути.
    """
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_NAME
    sheet["A5"] = "Дата заявки:"
    # Переворачиваем пары «заголовок ↔ поле» вместе с колонками.
    order = list(range(len(HEADERS)))[6:] + list(range(len(HEADERS)))[:6]
    for position, index in enumerate(order, start=1):
        sheet.cell(row=6, column=position, value=HEADERS[index])
    source = staging("foreign_order.xlsx")
    book.save(str(source))

    data = make_data(vehicles=make_vehicles(2))
    path = Path(generator.fill_from_template(str(source), data, str(out_dir)))
    try:
        back = generator.read_template(str(path))
        assert back["vehicles"] == data["vehicles"]
        assert back["zayavka"]["loading_city"] == data["zayavka"]["loading_city"]
        assert back["zayavka"]["driver_phone"] == data["zayavka"]["driver_phone"]
    finally:
        drop(path)
        source.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────
# Стык 6: логи без ПДн
# ─────────────────────────────────────────────────────────────

def test_full_cycle_logs_have_no_personal_data(validator, generator, out_dir,
                                              caplog):
    """Вся цепочка (валидация → генерация → чтение) логируется без ПДн."""
    data = make_data()

    with caplog.at_level(logging.DEBUG, logger="core.contract_generator"):
        report = validator.check(data)
        path = Path(generator.generate(data, str(out_dir)))
        try:
            generator.read_template(str(path))
        finally:
            drop(path)

    assert report.errors == []
    messages = "\n".join(
        record.getMessage() for record in caplog.records
        if record.name == "core.contract_generator"
    )
    assert messages
    for fragment in PII_FRAGMENTS:
        assert fragment not in messages, f"в логе есть «{fragment}»"


def test_fill_logs_have_no_personal_data(generator, templates_dir, out_dir,
                                        caplog):
    """Заполнение присланного файла тоже не пишет значений в лог."""
    sample = templates_dir / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    with caplog.at_level(logging.DEBUG, logger="core.contract_generator"):
        path = Path(generator.fill_from_template(str(sample), make_data(),
                                                 str(out_dir)))
    try:
        messages = "\n".join(
            record.getMessage() for record in caplog.records
            if record.name == "core.contract_generator"
        )
        for fragment in PII_FRAGMENTS:
            assert fragment not in messages, f"в логе есть «{fragment}»"
    finally:
        drop(path)


def test_logs_report_the_work_done(generator, saved, caplog):
    """В логе видно, что сделано: сколько машин, сколько ячеек, сколько строк."""
    with caplog.at_level(logging.INFO, logger="core.contract_generator"):
        path = saved(make_data(vehicles=make_vehicles(3)))
        read_back(generator, path)

    messages = "\n".join(
        record.getMessage() for record in caplog.records
        if record.name == "core.contract_generator"
    )
    assert "машин=3" in messages
    assert "Заявка Хавалов" in messages
    assert "Прочитан бланк" in messages


# ─────────────────────────────────────────────────────────────
# Заголовки: стык бланка и генератора
# ─────────────────────────────────────────────────────────────

def test_all_form_columns_are_covered(generator):
    """
    Каждая колонка формы либо заполняется, либо осознанно не используется.

    Стык «бланк ↔ генератор»: 32 заголовка формы против полей генератора.
    Неиспользованной остаётся ровно одна колонка — «Наименование
    транспортной компании» (K): в схеме промпта для неё нет поля.
    """
    unused = set(HEADERS) - set(HEADER_OF.values())

    assert unused == {HEADERS[10]}
    assert len(HEADERS) == 32
    assert HEADER_OF["price_with_vat"] == HEADERS[31]


def test_column_order_of_template_is_fixed(generator, saved):
    """Данные встают в те колонки бланка, что и в образце заказчика."""
    path = saved(make_data(vehicles=make_vehicles(1)))
    sheet = load_workbook(path)[SHEET_NAME]

    expected = {
        "A": "LOT-42",
        "B": make_vehicles(1)[0]["vin"],
        "C": make_vehicles(1)[0]["brand"],
        "E": "Калуга",
        "Q": "Тестов",
        "T": "9901 123456",
        "AE": "+7 (999) 123-45-67",
    }
    for column, value in expected.items():
        assert sheet[f"{column}7"].value == value, column
    assert sheet["AF7"].value == pytest.approx(123456.78)
