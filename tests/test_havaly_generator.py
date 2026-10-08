#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты генератора Excel-заявки Хавалов (ЭТАП 3.1.E.A.3).

Проверяют три публичных метода ZayavkaExcelGenerator:

  * ``read_template`` — чтение ЛЮБОГО файла формы (нашего эталонного бланка,
    присланного образца или только что сгенерированного) в схему промпта
    ``{"zayavka": {...}, "vehicles": [...]}``;
  * ``generate`` — заполнение НАШЕГО бланка
    (templates/shablon_havaly.xlsx) с сохранением в
    ``output/Заявка_Хавалы_<дата ISO>.xlsx``;
  * ``fill_from_template`` — то же, но источник бланка — присланный .xlsx.

Что здесь закреплено:

  * эталонный бланк читается как пустой (ни данных, ни машин), а образец
    заказчика — как «дата заполнена, машин нет»: в присланном файле
    (templates/Хавалы_образец.xlsx) строки данных пустые, это не ошибка;
  * круговой прогон: generate → read_template → те же данные (кроме полей,
    которых в бланке нет: vat_rate; и lot_number, который относится
    к строке таблицы, а не к заявке);
  * матчинг по ЗАГОЛОВКАМ: файл с другим порядком колонок и лишними
    колонками читается и заполняется правильно;
  * границы 0 / 1 / 10 / 11 машин: одиннадцатая в бланк не попадает;
  * стык имён ключей с промптом A.2 (core/prompts/havaly.py) — единым
    источником истины;
  * стык с бланком: все 32 заголовка формы покрыты полями генератора;
  * оформление: накладывается только на заполняемые ячейки, рамки и шрифты
    присланного файла не переписываются;
  * в логах генератора нет ПДн — только имена полей, длины и количества;
  * исходный файл генератор не изменяет.

Все данные синтетические, реальных ПДн нет. Выходные файлы создаются
в tests/_tmp и удаляются в finally.
"""

import json
import logging
import re
import shutil
import sys
import uuid
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.contracts.paths import (  # noqa: E402
    contract_folder_name,
    folder_key_from_form,
)
from core.contracts.zayavka.generator import (  # noqa: E402
    ABSENT_VEHICLE_KEYS,
    ABSENT_ZAYAVKA_KEYS,
    CARRIER_NAME,
    CUSTOMER_NAME,
    DATE_FIELDS,
    DATE_FORMAT,
    FILE_PREFIX,
    HEADERS,
    HEADER_OF,
    MAX_VEHICLES,
    SHEET_NAME,
    TEMPLATE_NAME,
    TIME_FORMAT,
    UNMAPPED_ZAYAVKA_FIELDS,
    VEHICLE_KEYS,
    WRAP_HEADERS,
    ZAYAVKA_KEYS,
    ZayavkaExcelGenerator,
    ZayavkaTemplateError,
)
from core.prompts.havaly import PROMPT  # noqa: E402
from tools.make_havaly_template import HEADERS as TEMPLATE_HEADERS  # noqa: E402

SAMPLE_NAME = "Хавалы_образец.xlsx"

#: Папка тестовых форм внутри tests/_tmp и метка текущего прогона.
STAGING_DIR = Path(__file__).resolve().parent / "_tmp" / "_havaly_forms"
_RUN_ID = uuid.uuid4().hex[:8]

#: Секция промпта со схемой ответа — из неё берётся набор ключей A.2.
SCHEMA_HEADING = "СХЕМА ОТВЕТА"

#: Дата заявки и дата плана погрузки: разные даты, чтобы видеть,
#: что берётся именно та ячейка.
DATE = "05.10.2026"
LOADING_PLAN_DATE = "07.10.2026"

# ─────────────────────────────────────────────────────────────
# Синтетические данные
# ─────────────────────────────────────────────────────────────

ZAYAVKA: dict = {
    "date": DATE,
    "lot_number": "LOT-42",
    "loading_city": "Калуга",
    "loading_point": "ул. Промышленная, 12, стр. 3",
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
    "driver_registration": "г. Москва, ул. Тестовая, д. 1, кв. 2",
    "driver_phone": "+7 (999) 123-45-67",
    "loading_plan_date": LOADING_PLAN_DATE,
    "loading_plan_time": "09:00",
    "price_with_vat": 123456.78,
    "vat_rate": "22%",
}

VEHICLES: list = [
    {
        "vin": "EC3TEUMB0T0002608",
        "brand": "JETOUR",
        "model": "T2",
        "dealer": "Дилер-Первый",
        "dealer_code": "D-001",
    },
    {
        "vin": "XTC651150N0001001",
        "brand": "HAVAL",
        "model": "DASHING",
        "dealer": "Дилер-Второй",
        "dealer_code": "D-002",
    },
]

#: Отпечатки ПДн: их не должно быть ни в логах, ни в логе чтения.
PII_FRAGMENTS = (
    "Тестов", "Тестович", "9901", "926830", "1822",
    "EC3TEUMB0T0002608", "XTC651150N0001001",
    "Промышленная", "Складской", "Тестовая",
    "О844ХУ196", "71ABF18", "123456.78",
)


def make_data(vehicles=VEHICLES, **overrides) -> dict:
    """Данные заявки: копия фикстур с точечными правками."""
    zayavka = dict(ZAYAVKA)
    zayavka.update(overrides)
    return {"zayavka": zayavka, "vehicles": [dict(v) for v in vehicles]}


def make_vehicles(count: int) -> list:
    """Список из `count` машин с уникальными VIN."""
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


def form_keys() -> tuple:
    """Ключи схемы ответа из промпта A.2 (единый источник имён)."""
    start = PROMPT.index(SCHEMA_HEADING)
    tail = PROMPT[PROMPT.index("{", start):]
    for heading in ("ПЛЕЙСХОЛДЕРЫ", "Плейсхолдеры"):
        cut = tail.find(heading)
        if cut != -1:
            tail = tail[:cut]
    schema = json.loads(tail[:tail.rindex("}") + 1])
    return tuple(schema["zayavka"]), tuple(schema["vehicles"][0])


@pytest.fixture
def generator() -> ZayavkaExcelGenerator:
    """Генератор на штатной папке шаблонов проекта."""
    return ZayavkaExcelGenerator()


@pytest.fixture
def out_dir(work_dir) -> Path:
    """Папка вывода теста внутри tests/_tmp."""
    path = work_dir / "havaly_generator"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def saved(generator, out_dir):
    """Файлы, созданные тестом: удаляются после проверки."""
    created: list = []

    def _generate(data, method="generate", source=None, name=None):
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
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────
# Чтение эталона и образца
# ─────────────────────────────────────────────────────────────

def test_read_own_template_is_empty(generator):
    """Эталонный бланк — пустой: ни данных, ни машин, ни ошибок."""
    data = generator.read_template(generator.template_path())

    assert set(data) == {"zayavka", "vehicles"}
    assert data["vehicles"] == []

    for key in ZAYAVKA_KEYS:
        if key in ("price_with_vat",):
            continue
        assert data["zayavka"][key] == "", f"{key} в бланке не пусто"
    assert data["zayavka"]["price_with_vat"] == 0.0

    # Стороны фиксированы и подставляются чтением (как в промпте A.2).
    assert data["zayavka"]["customer_name"] == CUSTOMER_NAME
    assert data["zayavka"]["carrier_name"] == CARRIER_NAME


def test_read_template_returns_all_schema_keys(generator):
    """Чтение отдаёт ровно те ключи, что описаны схемой промпта."""
    zayavka_keys, vehicle_keys = form_keys()
    data = generator.read_template(generator.template_path())

    assert tuple(data["zayavka"]) == zayavka_keys
    assert set(data["zayavka"]) == set(zayavka_keys)
    # Блок заявки = поля бланка + фиксированные стороны и ставка НДС.
    assert set(ZAYAVKA_KEYS) | set(ABSENT_ZAYAVKA_KEYS) == set(zayavka_keys)
    assert set(VEHICLE_KEYS) | set(ABSENT_VEHICLE_KEYS) == set(vehicle_keys)


def test_read_sample_file(generator, templates_dir):
    """
    Присланный образец: дата заполнена, машин нет — это не ошибка.

    В templates/Хавалы_образец.xlsx строки данных пустые (6 размеченных
    строк, ни одного значения), поэтому список машин пустой, а дата
    заявки из подписи «Дата заявки:» читается.
    """
    sample = templates_dir / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    data = generator.read_template(str(sample))

    assert data["zayavka"]["date"] == "16.09.2026"
    assert data["vehicles"] == []
    assert data["zayavka"]["customer_name"] == CUSTOMER_NAME
    assert data["zayavka"]["carrier_name"] == CARRIER_NAME


def test_read_sample_and_template_share_geometry(generator, templates_dir):
    """Бланк и образец дают одну и ту же форму данных: расходится только дата."""
    sample = templates_dir / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    own = generator.read_template(generator.template_path())
    alien = generator.read_template(str(sample))

    assert own["vehicles"] == alien["vehicles"] == []
    assert set(own["zayavka"]) == set(alien["zayavka"])
    own["zayavka"]["date"] = alien["zayavka"]["date"] = ""
    assert own["zayavka"] == alien["zayavka"]


# ─────────────────────────────────────────────────────────────
# Круговой прогон
# ─────────────────────────────────────────────────────────────

def test_round_trip_generate_then_read(generator, saved):
    """generate → read_template → те же данные (кроме отсутствующих в бланке)."""
    path = saved(make_data())
    back = generator.read_template(str(path))

    expected = dict(ZAYAVKA)
    expected["vat_rate"] = ""          # в бланке нет ячейки для ставки НДС
    assert back["zayavka"] == expected
    assert back["vehicles"] == VEHICLES


def test_round_trip_keeps_row_order(generator, saved):
    """Порядок машин в файле — как в данных."""
    path = saved(make_data(vehicles=make_vehicles(5)))
    back = generator.read_template(str(path))

    assert [v["vin"] for v in back["vehicles"]] == [
        v["vin"] for v in make_vehicles(5)
    ]


def test_round_trip_one_vehicle(generator, saved):
    """Одна машина: строка заполнена, остальные обрезаны."""
    path = saved(make_data(vehicles=make_vehicles(1)))
    back = generator.read_template(str(path))

    assert back["vehicles"] == make_vehicles(1)
    assert back["zayavka"]["lot_number"] == ZAYAVKA["lot_number"]


def test_round_trip_dates_and_numbers(generator, saved):
    """Даты и числа читаются обратно теми же значениями, а не датами Excel."""
    path = saved(make_data())
    back = generator.read_template(str(path))
    zayavka = back["zayavka"]

    assert zayavka["driver_license_issue_date"] == ZAYAVKA["driver_license_issue_date"]
    assert zayavka["driver_passport_issue_date"] == ZAYAVKA["driver_passport_issue_date"]
    assert zayavka["driver_birth_date"] == ZAYAVKA["driver_birth_date"]
    assert zayavka["loading_plan_date"] == ZAYAVKA["loading_plan_date"]
    assert zayavka["loading_plan_time"] == ZAYAVKA["loading_plan_time"]
    assert zayavka["price_with_vat"] == ZAYAVKA["price_with_vat"]


def test_round_trip_iso_date_input(generator, saved):
    """Дата в формате ГГГГ-ММ-ДД превращается в дату документа ДД.ММ.ГГГГ."""
    path = saved(make_data(date="2026-10-05"))
    back = generator.read_template(str(path))

    assert back["zayavka"]["date"] == DATE


def test_round_trip_empty_vehicle_stays_a_row(generator, saved):
    """
    Машина без VIN остаётся строкой.

    Промпт требует: если VIN неизвестен, но марка или модель заполнены —
    строку всё равно вернуть, с пустым vin.
    """
    vehicles = [{
        "vin": "", "brand": "HAVAL", "model": "DASHING",
        "dealer": "Дилер", "dealer_code": "D-9",
    }]
    path = saved(make_data(vehicles=vehicles))
    back = generator.read_template(str(path))

    assert back["vehicles"] == vehicles


def test_round_trip_vin_stub_becomes_empty(generator, saved):
    """Заглушка вместо VIN («Vin по факту погрузки») даёт пустую строку."""
    vehicles = [{
        "vin": "Vin по факту погрузки", "brand": "HAVAL", "model": "DASHING",
        "dealer": "Дилер", "dealer_code": "D-9",
    }]
    path = saved(make_data(vehicles=vehicles))
    back = generator.read_template(str(path))

    assert back["vehicles"][0]["vin"] == ""


def test_round_trip_negative_price_and_zero(generator, saved):
    """
    Ноль в ставке — это «ставка не указана» (0.0), а не пустая ячейка.

    Проверяет грабли 2B.4: `value or ""` превратил бы 0.0 в пустую строку.
    """
    path = saved(make_data(price_with_vat=0.0))
    back = generator.read_template(str(path))

    assert back["zayavka"]["price_with_vat"] == 0.0


def test_round_trip_price_from_text(generator, saved):
    """Ставка в виде «1 234,56 руб.» разбирается в число."""
    path = saved(make_data(price_with_vat="1 234,56 руб."))
    back = generator.read_template(str(path))

    assert back["zayavka"]["price_with_vat"] == pytest.approx(1234.56)


# ─────────────────────────────────────────────────────────────
# Матчинг по заголовкам
# ─────────────────────────────────────────────────────────────

def _file_with_headers(path: Path, headers, rows) -> Path:
    """
    Собирает книгу TDSheet: подпись даты, шапка, строки данных, блок сторон.

    Число размеченных строк данных = число переданных строк, а блок сторон
    встаёт сразу под ними (как в образце заказчика: две пустые строки,
    «Заказчик / Перевозчик», две пустые под подписи, «ФИО, подпись,
    печать»). Высоты строк проставляются как в образце: только на них
    видно, что обрезка строк переносит оформление вместе с содержимым.
    """
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_NAME
    sheet["A5"] = "Дата заявки:"
    sheet["B5"] = DATE
    sheet.row_dimensions[5].height = 15.0
    sheet.row_dimensions[6].height = 23.25
    for index, header in enumerate(headers, start=1):
        sheet.cell(row=6, column=index, value=header)
    for offset, row in enumerate(rows):
        for index, value in enumerate(row, start=1):
            if value is not None:
                sheet.cell(row=7 + offset, column=index, value=value)

    first_data, last_data = 7, 7 + max(len(rows), 1) - 1
    for row in range(first_data, last_data + 1):
        sheet.row_dimensions[row].height = 11.25

    party = last_data + 3
    sheet.row_dimensions[party - 1].height = 12.75
    sheet.row_dimensions[party].height = 12.75
    sheet.row_dimensions[party + 1].height = 15.75
    sheet.row_dimensions[party + 4].height = 11.25
    sheet.cell(row=party, column=3, value="Заказчик")
    sheet.cell(row=party, column=10, value="Перевозчик")
    sheet.cell(row=party + 1, column=3, value="Сюрлогистик")
    sheet.cell(row=party + 1, column=10, value='ООО "ТЕХНОЛОГИСТИКА"')
    sheet.cell(row=party + 4, column=3, value="ФИО, подпись, печать")
    sheet.cell(row=party + 4, column=10, value="ФИО, подпись, печать")
    book.save(str(path))
    return path


#: Раскладка тестовой формы: как в образце заказчика при шести строках
#: данных. Высоты строк задаются намеренно — на них видно, что обрезка
#: переносит оформление вместе с содержимым.
FORM_FIRST_DATA_ROW = 7


def _file_like_form(path: Path, *, order=None, rename=None, rows=None) -> Path:
    """
    Собирает ПОЛНУЮ форму заказчика: все 32 колонки, затем правки.

    Полная шапка нужна потому, что генератор ищет строку шапки по
    заголовку «Номер Лота»: файл без него — не форма, и падать на нём
    генератор вправе (см. test_file_without_header_raises). Здесь же
    проверяется терпимость к ПЕРЕСТАНОВКЕ и лишним колонкам внутри
    настоящей формы.
    """
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_NAME
    sheet["A5"] = "Дата заявки:"
    sheet["B5"] = DATE

    headers = list(HEADERS)
    # Сначала перестановка: она работает с исходными текстами заголовков.
    if order:
        headers = order(headers)
    if rename:
        headers = [rename.get(header, header) for header in headers]

    sheet.row_dimensions[5].height = 15.0
    sheet.row_dimensions[6].height = 23.25
    for index, header in enumerate(headers, start=1):
        sheet.cell(row=6, column=index, value=header)

    for row in range(FORM_FIRST_DATA_ROW, 19):
        sheet.row_dimensions[row].height = 11.25

    for offset, row in enumerate(rows or []):
        for index, value in enumerate(row, start=1):
            if value is not None:
                cell = sheet.cell(row=FORM_FIRST_DATA_ROW + offset, column=index)
                cell.value = value
                if cell.row != 5 and cell.row >= FORM_FIRST_DATA_ROW:
                    sheet.row_dimensions[cell.row].height = 11.25

    sheet.row_dimensions[18].height = 12.75
    sheet.row_dimensions[19].height = 12.75
    sheet.row_dimensions[20].height = 15.75
    sheet.row_dimensions[23].height = 11.25
    sheet.cell(row=19, column=3, value="Заказчик")
    sheet.cell(row=19, column=10, value="Перевозчик")
    sheet.cell(row=20, column=3, value="Сюрлогистик")
    sheet.cell(row=20, column=10, value='ООО "ТЕХНОЛОГИСТИКА"')
    sheet.cell(row=23, column=3, value="ФИО, подпись, печать")
    sheet.cell(row=23, column=10, value="ФИО, подпись, печать")
    book.save(str(path))
    return path


@pytest.fixture(scope="session", autouse=True)
def clean_staging_dir():
    """
    Убирает папку тестовых форм после прогона.

    Формы собираются в tests/_tmp/_havaly_forms с УНИКАЛЬНЫМ для запуска
    именем: файл от прошлого прогона не должен выглядеть свежим. Папка
    убирается целиком в конце — она наша и создана этим тестом.
    """
    yield
    shutil.rmtree(STAGING_DIR, ignore_errors=True)


def _staging(name: str) -> Path:
    """Путь для тестовой формы: уникальное имя внутри папки прогона."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    return STAGING_DIR / f"{_RUN_ID}_{name}"


def _column_of(sheet, header: str) -> int:
    """Номер колонки по заголовку в шапке (строка 6)."""
    for index in range(1, sheet.max_column + 1):
        if sheet.cell(row=6, column=index).value == header:
            return index
    raise AssertionError(f"в тестовом файле нет колонки {header!r}")


def _rotate_headers(headers: list) -> list:
    """
    Шапка в ДРУГОМ порядке — с сохранением пар «заголовок ↔ поле».

    Просто перевернуть список нельзя: заголовки формы не уникальны
    («Город погрузки» = E6 и G6, «Марка Автомобиля» = C6 и L6), и
    перевёрнутый список сдвинул бы поле к чужому заголовку. Поэтому
    переставляются ЦЕЛЫЕ ПАРЫ: первые шесть колонок уезжают в конец.
    """
    return headers[6:] + headers[:6]


def _uniquify(headers: list) -> list:
    """
    Разводит одинаковые заголовки формы: второй и далее получают «(2)».

    Нужно там, где «переставленная шапка» проверяется по буквам колонок:
    с одинаковыми заголовками непонятно, в какую из двух колонок должно
    было попасть значение. Генератор такие заголовки узнаёт и так —
    сопоставление идёт по нормализованному тексту.
    """
    seen: dict = {}
    result = []
    for header in headers:
        seen[header] = seen.get(header, 0) + 1
        result.append(header if seen[header] == 1 else f"{header} ({seen[header]})")
    return result


def test_headers_are_matched_not_columns(generator, out_dir):
    """
    Другой порядок колонок: данные встают на свои места.

    Это главное решение шага: заказчики присылают файлы с разным порядком
    колонок, поэтому колонка ищется по заголовку, а не по букве.
    """
    source = _file_like_form(
        _staging("reordered.xlsx"),
        order=lambda headers: _uniquify(_rotate_headers(headers)),
        rows=[["значение"] * len(HEADERS)],
    )

    data = generator.read_template(str(source))
    book = load_workbook(source)
    sheet = book[SHEET_NAME]

    assert data["zayavka"]["loading_city"] == "значение"
    assert data["zayavka"]["driver_phone"] == "значение"
    assert data["vehicles"][0]["vin"] == "значение"
    # Заголовки A..F уехали в конец: VIN был колонкой B (2-я), стал 28-й.
    rotated = _uniquify(_rotate_headers(list(HEADERS)))
    assert _column_of(sheet, rotated[27]) == 28
    assert rotated[27] == HEADER_OF["vin"]


def test_fill_from_reordered_columns_keeps_other_columns_empty(generator,
                                                               out_dir):
    """
    Без машин заполняется только дата заявки.

    Общие сведения бланк раскладывает ПО СТРОКАМ таблицы, поэтому при
    нуле машин писать их некуда: строки остаются пустыми, и в файле
    появляется одна лишь дата. Это не потеря данных, а устройство формы
    (о незаполненном валидатор предупреждает).
    """
    source = _file_like_form(
        _staging("fill_reordered_empty.xlsx"),
        order=lambda headers: _uniquify(_rotate_headers(headers)),
    )

    path = Path(generator.fill_from_template(str(source), make_data(vehicles=[]),
                                            str(out_dir)))
    try:
        book = load_workbook(path)
        sheet = book[SHEET_NAME]
        for key in ("loading_city", "driver_phone", "price_with_vat", "vin"):
            column = _column_of(sheet, HEADER_OF[key])
            assert sheet.cell(row=7, column=column).value is None, key
        assert sheet["B5"].value is not None          # дата заявки записана
    finally:
        path.unlink(missing_ok=True)


def test_extra_columns_do_not_break_reading(generator, out_dir):
    """Лишние колонки в присланном файле чтению не мешают."""
    source = _file_like_form(
        _staging("extra.xlsx"),
        order=lambda headers: [headers[0], "Примечание заказчика"] + headers[1:],
        rows=[["x"] * (len(HEADERS) + 1)],
    )

    data = generator.read_template(str(source))

    # Все колонки сдвинулись на одну вправо из-за вставленного
    # «Примечания», но нашлись по заголовкам.
    assert data["vehicles"][0]["vin"] == "x"
    assert data["vehicles"][0]["brand"] == "x"
    assert data["zayavka"]["loading_city"] == "x"


def test_fill_from_template_with_reordered_columns(generator, out_dir):
    """
    Заполнение файла с другим порядком колонок: значения на своих местах.

    Колонки ищутся по ТЕКСТУ заголовка, а не по букве: в переставленной
    шапке «Город погрузки» стоит совсем не там, где в бланке, но поле
    находит свою колонку именно по нему.
    """
    source = _file_like_form(
        _staging("fill_reordered.xlsx"),
        order=_rotate_headers,
    )

    path = Path(generator.fill_from_template(
        str(source), make_data(vehicles=make_vehicles(1)), str(out_dir)
    ))
    try:
        book = load_workbook(path)
        sheet = book[SHEET_NAME]
        vehicle = make_vehicles(1)[0]
        for key, expected in (
            ("loading_city", ZAYAVKA["loading_city"]),
            ("driver_phone", ZAYAVKA["driver_phone"]),
            ("price_with_vat", ZAYAVKA["price_with_vat"]),
            ("vin", vehicle["vin"]),
            ("brand", vehicle["brand"]),
        ):
            column = _column_of(sheet, HEADER_OF[key])
            assert sheet.cell(row=7, column=column).value == expected, key
    finally:
        path.unlink(missing_ok=True)


def test_header_normalization_tolerates_spaces_and_colons(generator, out_dir):
    """Пробелы, BOM и двоеточие в заголовке не мешают сопоставлению."""
    source = _file_like_form(
        _staging("normalized.xlsx"),
        rename={
            HEADER_OF["vin"]: "\ufeff VIN ",
            HEADER_OF["brand"]: "Марка:",
            HEADER_OF["loading_city"]: "Город  погрузки",
        },
        rows=[["VIN-1"] * len(HEADERS)],
    )

    data = generator.read_template(str(source))

    assert data["vehicles"][0]["vin"] == "VIN-1"
    assert data["vehicles"][0]["brand"] == "VIN-1"
    assert data["zayavka"]["loading_city"] == "VIN-1"


def test_missing_headers_are_logged_not_fatal(generator, out_dir, caplog):
    """Чего в присланной шапке нет — то в логе, а не исключением."""
    source = _file_like_form(
        _staging("narrow.xlsx"),
        rename={HEADER_OF["vin"]: "VIN-номер по документу"},
        rows=[["VIN-1"] * len(HEADERS)],
    )

    with caplog.at_level(logging.WARNING, logger="core.contract_generator"):
        data = generator.read_template(str(source))

    assert data["vehicles"][0]["vin"] == ""       # колонка не распознана
    assert data["vehicles"][0]["brand"] == "VIN-1"  # остальные на месте
    assert any("не найдено" in record.getMessage() for record in caplog.records)


def test_own_template_headers_cover_all_columns(generator):
    """
    Все 32 заголовка формы покрыты полями генератора.

    Стык «бланк ↔ генератор»: каждый заголовок эталона либо заполняется,
    либо осознанно не используется. Расхождение — сигнал, что бланк
    изменился, а генератор нет.
    """
    used = set(HEADER_OF.values())
    unused = set(HEADERS) - used

    # «Наименование транспортной компании» (K) не используется осознанно:
    # в схеме промпта для неё нет поля (перевозчик — carrier_name).
    assert unused == {HEADERS[10]}
    assert len(HEADERS) == 32
    assert len(set(HEADERS)) == 32


def test_generator_headers_match_template_maker(generator):
    """Тексты заголовков генератора совпадают с исходным списком бланка."""
    from core.contracts.zayavka.generator import HEADERS as OWN

    assert OWN == TEMPLATE_HEADERS
    assert ZayavkaExcelGenerator.TEMPLATE_NAMES == {"zayavka": TEMPLATE_NAME}
    assert HEADER_OF["price_with_vat"] == OWN[-1]
    assert HEADER_OF["vin"] == OWN[1]


# ─────────────────────────────────────────────────────────────
# Границы: 0 / 1 / 10 / 11 машин
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("count", [0, 1, 10])
def test_vehicle_counts_within_limit(generator, saved, count):
    """0, 1 и 10 машин: все строки записаны, лишних нет."""
    vehicles = make_vehicles(count)
    path = saved(make_data(vehicles=vehicles))
    back = generator.read_template(str(path))

    assert back["vehicles"] == vehicles


@pytest.mark.parametrize("count", [0, 1, 10])
def test_trim_rows_matches_vehicle_count(generator, saved, count):
    """
    Число строк таблицы равно числу машин: пустых строк не осталось.

    Удаляются все строки таблицы ниже последней машины, поэтому между
    данными и блоком сторон ничего не остаётся, а сам блок стоит ровно
    там, куда его поставил бланк: две пустые строки ниже таблицы, потом
    «Заказчик». При 10 машинах (максимум бланка) не удаляется ничего —
    раскладка файла совпадает с раскладкой бланка до последней строки.
    """
    path = saved(make_data(vehicles=make_vehicles(count)))
    book = load_workbook(path)
    sheet = book[SHEET_NAME]

    labels = {
        cell.value: cell.row
        for row in sheet.iter_rows() for cell in row if cell.value is not None
    }
    # Пустыми остаются ровно две строки хвоста бланка — те, что стоят
    # между таблицей и подписью «Заказчик».
    blank = [
        row for row in range(7, labels["Заказчик"])
        if all(sheet.cell(row=row, column=col).value is None
               for col in range(1, len(HEADERS) + 1))
    ]
    assert blank == [7 + count, 8 + count]
    # Шапка (6) + данные (count) + хвост бланка: две пустые строки, стороны,
    # две пустые под подписи и строка «ФИО, подпись, печать».
    assert labels["Заказчик"] == 9 + count
    assert labels["Перевозчик"] == 9 + count
    assert labels["Сюрлогистик"] == 10 + count
    assert labels["ФИО, подпись, печать"] == 13 + count


def test_eleven_vehicles_are_truncated(generator, saved):
    """11 машин: записаны первые 10, одиннадцатая в файл не попала."""
    path = saved(make_data(vehicles=make_vehicles(11)))
    back = generator.read_template(str(path))

    assert len(back["vehicles"]) == MAX_VEHICLES
    assert back["vehicles"] == make_vehicles(10)
    assert make_vehicles(11)[-1]["vin"] not in {
        vehicle["vin"] for vehicle in back["vehicles"]
    }


def test_eleven_vehicles_warning_on_write(generator, out_dir, caplog):
    """Про обрезку машин генератор пишет в лог — без ПДн."""
    with caplog.at_level(logging.WARNING, logger="core.contract_generator"):
        path = Path(generator.generate(make_data(vehicles=make_vehicles(11)),
                                       str(out_dir)))
    try:
        messages = [
            record.getMessage() for record in caplog.records
            if record.name == "core.contract_generator"
        ]
        assert any("в бланк помещается" in message for message in messages)
        assert not any("МАРКА 11" in message for message in messages)
    finally:
        path.unlink(missing_ok=True)


def test_zero_vehicles_keeps_form_and_parties(generator, saved):
    """0 машин: таблица пустая, но бланк и блок сторон на месте."""
    path = saved(make_data(vehicles=[]))
    book = load_workbook(path)
    sheet = book[SHEET_NAME]

    assert sheet["A13"].value in (None,)
    values = {
        (cell.row, cell.column): cell.value
        for row in sheet.iter_rows() for cell in row if cell.value is not None
    }
    assert any(value == "Заказчик" for value in values.values())
    assert any(value == "Перевозчик" for value in values.values())
    assert any(value == "Сюрлогистик" for value in values.values())
    assert sheet["B5"].value is not None


def test_zero_vehicles_has_no_lot_number(generator, saved):
    """0 машин: номер лота писать некуда — строки таблицы пусты."""
    path = saved(make_data(vehicles=[]))
    back = generator.read_template(str(path))

    assert back["vehicles"] == []
    assert back["zayavka"]["lot_number"] == ""


def test_party_block_moves_up_with_table(generator, saved):
    """После обрезки строк блок сторон поднимается вслед за таблицей."""
    path = saved(make_data(vehicles=make_vehicles(2)))
    book = load_workbook(path)
    sheet = book[SHEET_NAME]

    rows = {
        cell.value: cell.row
        for row in sheet.iter_rows() for cell in row if cell.value is not None
    }
    # Шапка 6 + две машины (7, 8) + две пустые строки бланка = 11.
    assert rows["Заказчик"] == 11
    assert rows["Сюрлогистик"] == 12
    assert rows["ФИО, подпись, печать"] == 15


# ─────────────────────────────────────────────────────────────
# Имена ключей: стык с промптом A.2
# ─────────────────────────────────────────────────────────────

def test_generator_keys_match_prompt_schema(generator):
    """
    Стык «промпт ↔ генератор»: имена ключей JSON совпадают.

    Промпт (core/prompts/havaly.py) — единый источник истины. Если
    генератор заведёт своё имя поля, тест это покажет.
    """
    zayavka_keys, vehicle_keys = form_keys()

    assert set(ZAYAVKA_KEYS) | set(ABSENT_ZAYAVKA_KEYS) == set(zayavka_keys)
    assert set(VEHICLE_KEYS) | set(ABSENT_VEHICLE_KEYS) == set(vehicle_keys)
    assert not set(ZAYAVKA_KEYS) & set(ABSENT_ZAYAVKA_KEYS)
    assert len(zayavka_keys) == 30
    assert len(vehicle_keys) == 5


def test_generated_file_drops_only_documented_fields(generator, saved):
    """
    Круговой прогон теряет ровно объявленные поля — и ничего больше.

    Без машин бланк хранит только дату заявки: общие сведения стоят
    колонками таблицы, и без строк писать их некуда. Дата не теряется,
    ставка НДС в файл не пишется, стороны подставляет чтение, номер лота
    писать не в чем.
    """
    path = saved(make_data(vehicles=[]))
    back = generator.read_template(str(path))

    kept = {key for key, value in back["zayavka"].items() if value}
    assert kept == {"date", "customer_name", "carrier_name"}

    # Ставка НДС — единственное поле, для которого в бланке нет ячейки.
    assert back["zayavka"]["vat_rate"] == ""
    assert ZAYAVKA["vat_rate"] == "22%"
    assert set(UNMAPPED_ZAYAVKA_FIELDS) == {
        "vat_rate", "customer_name", "carrier_name"
    }


def test_all_shared_fields_survive_with_vehicles(generator, saved):
    """С машинами теряется ровно одно поле — vat_rate (нет ячейки)."""
    path = saved(make_data())
    back = generator.read_template(str(path))

    lost = {
        key for key, value in ZAYAVKA.items()
        if value not in ("", None, 0.0) and not back["zayavka"].get(key)
    }
    assert lost == {"vat_rate"}


def test_all_blank_columns_are_documented(generator):
    """Поля без колонки перечислены поимённо: их ровно три."""
    assert set(UNMAPPED_ZAYAVKA_FIELDS) == {
        "vat_rate", "customer_name", "carrier_name"
    }
    assert not (set(UNMAPPED_ZAYAVKA_FIELDS) & set(HEADER_OF))


def test_prompt_is_attached_to_generator(generator):
    """Генератор носит с собой промпт своего типа (единый источник полей)."""
    assert ZayavkaExcelGenerator.PROMPT is PROMPT
    assert ZayavkaExcelGenerator.CONTRACT_TYPE == "zayavka_excel"


# ─────────────────────────────────────────────────────────────
# Оформление
# ─────────────────────────────────────────────────────────────

def test_styles_applied_only_to_filled_cells(generator, saved):
    """Свои стили — только на заполненных ячейках: даты и перенос текста."""
    path = saved(make_data(vehicles=make_vehicles(1)))
    book = load_workbook(path)
    sheet = book[SHEET_NAME]

    # Дата заявки получила числовой формат, иначе Excel покажет число.
    assert sheet["B5"].number_format == DATE_FORMAT
    # Перенос включён у длинных полей и только у заполненных.
    assert sheet["F7"].alignment.wrap_text is True     # пункт погрузки
    assert sheet["X7"].alignment.wrap_text is True     # кем выдан паспорт
    assert sheet["Q7"].alignment.wrap_text is not True  # фамилия — короткая
    assert sheet["A7"].alignment.wrap_text is not True  # номер лота


def test_styles_not_applied_to_untouched_cells(generator, saved):
    """
    Присланный файл: свои стили — только на заполняемые ячейки.

    Номерные форматы ячеек из файла заказчика сохраняются: генератор их не
    переписывает. Исключение — только даты и время, которые он пишет
    значениями: без формата Excel показал бы 46397 вместо 05.10.2026.
    """
    sample = Path(generator.template_path()).parent / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    before = load_workbook(str(sample))[SHEET_NAME]
    # U, Y, AA, AC — даты; AD — время; AF — ставка. В образце у них
    # формат «General», кроме времени и ставки.
    formats = {
        coordinate: before[coordinate].number_format
        for coordinate in ("U7", "Y7", "AA7", "AC7", "AD7", "AF7")
    }
    money_format = formats["AF7"]

    data = make_data(vehicles=[{**make_vehicles(1)[0], "vin": ""}])
    path = saved(data, method="fill", source=str(sample))
    after = load_workbook(path)[SHEET_NAME]

    # Ставка и время — форматы образца не тронуты.
    assert after["AF7"].number_format == money_format
    assert after["AD7"].number_format == formats["AD7"]
    # Даты записаны значениями и получили формат даты документа.
    for coordinate in ("U7", "Y7", "AA7", "AC7"):
        assert after[coordinate].number_format == DATE_FORMAT, coordinate
    # Дата заявки — тоже дата документа.
    assert after["B5"].number_format == DATE_FORMAT

    # Рамки и шрифты первой строки данных — из образца, не наши.
    for coordinate in ("A7", "B7", "U7", "AF7"):
        assert after[coordinate].border.left.style == \
            before[coordinate].border.left.style
        assert after[coordinate].font.name == before[coordinate].font.name


def test_borders_and_fonts_of_blank_survive(generator, saved):
    """Рамки и шрифты бланка остаются бланковыми."""
    path = saved(make_data(vehicles=make_vehicles(2)))
    book = load_workbook(path)
    sheet = book[SHEET_NAME]
    template = load_workbook(generator.template_path())[SHEET_NAME]

    for coordinate in ("A7", "F7", "AF7", "A8", "F8"):
        assert sheet[coordinate].border.left.style == \
            template[coordinate].border.left.style
        assert sheet[coordinate].font.name == template[coordinate].font.name
        assert sheet[coordinate].font.size == template[coordinate].font.size


def test_merged_price_cell_is_written_once(generator, saved):
    """
    Ставка в присланном файле может быть объединена на все строки.

    В образце так и есть (AF7:AF12): писать в объединённую ячейку openpyxl
    не даёт, значение должно уйти в левую верхнюю ячейку диапазона.
    """
    path = saved(make_data(vehicles=make_vehicles(2)), method="fill",
                 source=str(Path(generator.template_path())))
    book = load_workbook(path)
    sheet = book[SHEET_NAME]

    assert sheet["AF7"].value == ZAYAVKA["price_with_vat"]


def test_fill_from_sample_keeps_its_formatting(generator, templates_dir, out_dir,
                                               saved):
    """Форматирование присланного файла сохраняется (шрифты и рамки)."""
    sample = templates_dir / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    before = load_workbook(str(sample))[SHEET_NAME]
    sample_fonts = {
        cell.coordinate: (cell.font.name, cell.font.size)
        for row in before.iter_rows(min_row=5, max_row=12)
        for cell in row
    }
    sample_borders = {
        cell.coordinate: cell.border.left.style
        for row in before.iter_rows(min_row=6, max_row=12)
        for cell in row
    }

    path = saved(make_data(vehicles=make_vehicles(2)), method="fill",
                 source=str(sample))
    after = load_workbook(path)[SHEET_NAME]

    # Данные записаны в строки 7-8, оформление этих ячеек — из образца.
    for coordinate in ("A7", "B7", "C7", "F7", "AF7", "A8", "Q8"):
        assert (after[coordinate].font.name, after[coordinate].font.size) == \
            sample_fonts[coordinate]
        assert after[coordinate].border.left.style == sample_borders[coordinate]


def test_fill_from_sample_keeps_column_widths(generator, templates_dir, out_dir,
                                             saved):
    """Ширины колонок присланного файла не переписываются."""
    sample = templates_dir / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    before = load_workbook(str(sample))[SHEET_NAME]
    widths = {key: item.width for key, item in before.column_dimensions.items()}

    path = saved(make_data(vehicles=make_vehicles(2)), method="fill",
                 source=str(sample))
    after = load_workbook(path)[SHEET_NAME]

    for key, width in widths.items():
        assert after.column_dimensions[key].width == width


def test_fill_from_sample_merges_price_on_all_rows(generator, templates_dir,
                                                   out_dir, saved):
    """
    Объединение «Ставка с НДС» образца (AF7:AF12) остаётся осмысленным.

    После обрезки строк объединение укорачивается ровно до числа машин:
    12 → 8 при двух машинах (строки 7-8). Значение — в первой ячейке.
    """
    sample = templates_dir / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    path = saved(make_data(vehicles=make_vehicles(2)), method="fill",
                 source=str(sample))
    book = load_workbook(path)
    sheet = book[SHEET_NAME]
    merged = {str(item) for item in sheet.merged_cells.ranges}

    assert "AF7:AF8" in merged
    assert "B2:C2" in merged
    assert sheet["AF7"].value == ZAYAVKA["price_with_vat"]


def test_time_cell_keeps_time_format(generator, saved):
    """Время погрузки пишется временем с форматом «h:mm»."""
    path = saved(make_data(vehicles=make_vehicles(1)))
    sheet = load_workbook(path)[SHEET_NAME]

    assert sheet["AD7"].number_format == TIME_FORMAT
    assert sheet["AD7"].value.strftime("%H:%M") == ZAYAVKA["loading_plan_time"]


def test_date_cells_use_document_format(generator, saved):
    """Все поля-даты получают числовой формат ДД.ММ.ГГГГ."""
    path = saved(make_data(vehicles=make_vehicles(1)))
    sheet = load_workbook(path)[SHEET_NAME]

    columns = {
        "driver_license_issue_date": "U",
        "driver_passport_issue_date": "Y",
        "driver_birth_date": "AA",
        "loading_plan_date": "AC",
    }
    for key in DATE_FIELDS:
        if key == "date":
            continue
        assert sheet[f"{columns[key]}7"].number_format == DATE_FORMAT, key


def test_wrap_headers_are_real_form_columns(generator):
    """Перенос включён у колонок формы (а не у выдуманных заголовков)."""
    for header in WRAP_HEADERS:
        assert header in HEADERS


# ─────────────────────────────────────────────────────────────
# Имя файла, папка вывода, безопасность
# ─────────────────────────────────────────────────────────────

def test_filename_uses_iso_date(generator, saved):
    """Имя готового файла: Заявка_Хавалы_<дата ISO>.xlsx."""
    path = saved(make_data())
    assert path.name == f"{FILE_PREFIX}_2026-10-05.xlsx"


def test_filename_falls_back_to_today(generator):
    """Без даты в данных имя файла всё равно осмысленное."""
    from datetime import datetime

    name = generator.get_filename({"zayavka": {}, "vehicles": []})
    assert re.fullmatch(
        rf"{FILE_PREFIX}_{datetime.now().strftime('%Y-%m-%d')}\.xlsx", name
    )


def test_output_dir_is_created(generator, out_dir, tmp_name="nested"):
    """Папка вывода создаётся, если её нет."""
    target = out_dir / "глубже" / "ещё"
    path = Path(generator.generate(make_data(vehicles=[]), str(target)))
    try:
        assert path.exists()
        # Внутри корня вывода — папка рейса, а файл уже в ней.
        assert path.parent.parent == target
        assert path.parent.is_dir()
    finally:
        path.unlink(missing_ok=True)
        for folder in (path.parent, target, target.parent):
            try:
                folder.rmdir()
            except OSError:
                pass


def test_havaly_generate_puts_file_in_named_folder(generator, out_dir):
    """
    Заявка ложится в папку рейса: <Фамилия_И.О.>_<маршрут>_<ДД.ММ.ГГГГ>.

    Данные у Хавалов плоские (схема промпта), поэтому имя папки собирает
    адаптер folder_key_from_form: водитель — из driver_*, маршрут — из
    городов погрузки и доставки. Имя ФАЙЛА при этом не меняется: оно и
    раньше зависело только от даты заявки.
    """
    data = make_data(vehicles=make_vehicles(1))
    folder_name = contract_folder_name(folder_key_from_form(data))

    path = Path(generator.generate(data, str(out_dir)))
    try:
        assert folder_name == "Тестов_Т.Т._Калуга-Москва_05.10.2026"
        assert path.parent == out_dir / folder_name
        assert path.parent.is_dir()
        assert path.parent.parent == out_dir
        # Имя файла прежнее: папка рейса его не трогает.
        assert path.name == "Заявка_Хавалы_2026-10-05.xlsx"
    finally:
        path.unlink(missing_ok=True)
        shutil.rmtree(path.parent, ignore_errors=True)


def test_havaly_same_trip_uses_same_folder(generator, out_dir):
    """
    Две заявки одного рейса (разные машины) ложатся в ОДНУ папку.

    Папка вторая не создаётся: имя рейса то же, значит и папка та же. Имя
    файла зависит только от даты заявки, поэтому файл один — заявка
    перезаписывается; проверяется именно ПАПКА.
    """
    first = Path(generator.generate(make_data(vehicles=make_vehicles(1)), str(out_dir)))
    second = Path(generator.generate(make_data(vehicles=make_vehicles(2)), str(out_dir)))
    try:
        assert first.parent == second.parent
        assert first.name == second.name
        assert first.exists()
    finally:
        shutil.rmtree(first.parent, ignore_errors=True)


def test_havaly_different_drivers_use_different_folders(generator, out_dir):
    """
    Разные водители — разные папки, даже при общем маршруте и дате.

    Водитель различается ФАМИЛИЕЙ, а не инициалами: у двух Ивановых папки
    совпали бы, и это осознанное правило имени (см. tests/test_paths.py).
    """
    other = make_data(vehicles=make_vehicles(1))
    other["zayavka"]["driver_last_name"] = "Петров"
    other["zayavka"]["driver_first_name"] = "Пётр"
    other["zayavka"]["driver_middle_name"] = "Петрович"

    first = Path(generator.generate(make_data(vehicles=make_vehicles(1)), str(out_dir)))
    second = Path(generator.generate(other, str(out_dir)))
    try:
        assert first.parent != second.parent
        assert first.parent.name.startswith("Тестов_Т.Т._")
        assert second.parent.name.startswith("Петров_П.П._")
    finally:
        shutil.rmtree(first.parent, ignore_errors=True)
        shutil.rmtree(second.parent, ignore_errors=True)


def test_havaly_log_has_no_folder_or_driver_name(generator, out_dir, caplog):
    """
    В логе заявки — только имя файла, без папки и без фамилии водителя.

    Имя папки рейса содержит фамилию водителя, поэтому в лог оно не идёт:
    логи проекта — без ПДн (AGENTS.md § 3).
    """
    with caplog.at_level(logging.INFO):
        path = Path(generator.generate(make_data(vehicles=[]), str(out_dir)))
    try:
        messages = [record.getMessage() for record in caplog.records
                    if record.getMessage().startswith("Заявка Хавалов:")]
        assert messages, "нет записи о создании заявки"
        assert f"файл={path.name}" in messages[-1]
        assert "папка=" not in messages[-1]
        assert "Тестов" not in messages[-1]
        assert str(out_dir) not in messages[-1]
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_default_output_dir_is_project_output(generator):
    """Папка по умолчанию — output/ в корне проекта."""
    assert Path(generator.default_output_dir()).name == "output"
    assert Path(generator.default_output_dir()).parent == \
        Path(generator.template_path()).parent.parent


def test_source_file_is_not_modified(generator, templates_dir, out_dir):
    """Присланный файл генератор не изменяет: он только читается."""
    import hashlib

    sample = templates_dir / SAMPLE_NAME
    if not sample.exists():
        pytest.skip(f"нет образца {SAMPLE_NAME}")

    digest = hashlib.sha256(sample.read_bytes()).hexdigest()
    path = Path(generator.fill_from_template(str(sample), make_data(),
                                             str(out_dir)))
    try:
        assert hashlib.sha256(sample.read_bytes()).hexdigest() == digest
    finally:
        path.unlink(missing_ok=True)


def test_generate_does_not_touch_template(generator, out_dir):
    """Эталонный бланк при генерации тоже не меняется."""
    import hashlib

    digest = hashlib.sha256(Path(generator.template_path()).read_bytes()).hexdigest()
    path = Path(generator.generate(make_data(), str(out_dir)))
    try:
        assert hashlib.sha256(
            Path(generator.template_path()).read_bytes()
        ).hexdigest() == digest
    finally:
        path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────
# Ошибки структуры
# ─────────────────────────────────────────────────────────────

def test_missing_file_raises_clear_error(generator, out_dir):
    """Нет файла — понятная ошибка, а не FileNotFoundError из openpyxl."""
    with pytest.raises(ZayavkaTemplateError, match="не найден"):
        generator.read_template(str(out_dir / "нет-такого.xlsx"))


def test_file_without_tdsheet_raises(generator, out_dir):
    """Нет листа TDSheet — ошибка с перечислением листов."""
    book = Workbook()
    book.active.title = "Лист1"
    path = _staging("no_tdsheet.xlsx")
    book.save(str(path))

    with pytest.raises(ZayavkaTemplateError, match="TDSheet"):
        generator.read_template(str(path))


def test_file_without_header_raises(generator, out_dir):
    """Нет строки шапки (нет «Номер Лота») — ошибка."""
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_NAME
    sheet["A1"] = "Совсем другой документ"
    path = _staging("no_header.xlsx")
    book.save(str(path))

    with pytest.raises(ZayavkaTemplateError, match="шапк"):
        generator.read_template(str(path))


def test_broken_zip_raises_clear_error(generator, out_dir):
    """Не-xlsx внутри .xlsx — понятная ошибка вместо исключения openpyxl."""
    path = _staging("broken.xlsx")
    path.write_bytes(b"not a zip at all")

    with pytest.raises(ZayavkaTemplateError, match="Не удалось открыть"):
        generator.read_template(str(path))


def test_generate_with_broken_source_raises(generator, out_dir):
    """fill_from_template на битом файле падает так же понятно."""
    path = _staging("broken2.xlsx")
    path.write_bytes(b"not a zip at all")

    with pytest.raises(ZayavkaTemplateError):
        generator.fill_from_template(str(path), make_data(), str(out_dir))


def test_own_template_headers_are_checked_strictly(generator, out_dir):
    """
    На своём бланке расхождение шапки — ошибка, а не запись в лог.

    Эталон собираем мы сами: если в нём пропал заголовок, это наша поломка,
    и тихо заполнять такой бланк нельзя. Пропадает НЕ «Номер Лота»: по нему
    шапка только ищется, и его отсутствие — уже другая ошибка (см.
    test_file_without_header_raises).
    """
    book = load_workbook(generator.template_path())
    sheet = book[SHEET_NAME]
    sheet.cell(row=6, column=HEADERS.index(HEADER_OF["vin"]) + 1,
               value="VIN-номер по документу")

    own_dir = out_dir / "broken_own"
    own_dir.mkdir(parents=True, exist_ok=True)
    book.save(str(own_dir / TEMPLATE_NAME))

    narrow = ZayavkaExcelGenerator(templates_dir=str(own_dir))
    with pytest.raises(ZayavkaTemplateError) as error:
        narrow.generate(make_data(), str(out_dir))

    message = str(error.value)
    assert "эталонный бланк" in message
    assert HEADER_OF["vin"] in message


def test_foreign_template_without_header_column_is_only_a_warning(generator,
                                                                 out_dir):
    """
    На присланном файле та же пропажа — только запись в логе.

    Заголовки у заказчиков свои: из-за одного непривычного названия колонки
    отказываться от работы нельзя, поле просто останется пустым. Остальные
    колонки при этом заполняются как обычно.
    """
    source = _file_like_form(
        _staging("rename_vin.xlsx"),
        rename={HEADER_OF["vin"]: "VIN-номер по документу"},
    )

    path = Path(generator.fill_from_template(
        str(source), make_data(vehicles=make_vehicles(1)), str(out_dir)
    ))
    try:
        book = load_workbook(path)
        sheet = book[SHEET_NAME]
        assert sheet.cell(
            row=7, column=_column_of(sheet, HEADER_OF["brand"])
        ).value == "МАРКА 1"
        # Колонка VIN не распознана — значение не записано никуда.
        assert "VIN-номер по документу" in {
            cell.value for cell in sheet[6]
        }
    finally:
        path.unlink(missing_ok=True)


def test_generator_accepts_templates_dir(out_dir):
    """Контракт фабрики: templates_dir принимается и используется."""
    generator = ZayavkaExcelGenerator(templates_dir=str(out_dir))

    assert generator.templates_dir == str(out_dir)
    assert generator.template_path() == str(out_dir / TEMPLATE_NAME)
    assert generator.templates == {"zayavka": str(out_dir / TEMPLATE_NAME)}


def test_generator_defaults_to_project_templates():
    """Без templates_dir берётся штатная папка шаблонов проекта."""
    generator = ZayavkaExcelGenerator()

    assert Path(generator.template_path()).exists()
    assert Path(generator.template_path()).name == TEMPLATE_NAME


def test_flat_payload_is_understood(generator, saved):
    """Плоский словарь (как от сборщика UI) генератор тоже понимает."""
    path = saved({"date": DATE, "loading_city": "Калуга", "vehicles": VEHICLES})
    back = generator.read_template(str(path))

    assert back["zayavka"]["date"] == DATE
    assert back["zayavka"]["loading_city"] == "Калуга"
    assert back["vehicles"] == VEHICLES


def test_payload_inside_contract_is_understood(generator, saved):
    """
    Поля заявки внутри contract — второй плоский вариант входа.

    Машина нужна: общие сведения бланк раскладывает ПО СТРОКАМ таблицы
    (в форме они повторяются в каждой строке), и без машин писать их
    некуда — в файле остаётся только дата заявки.
    """
    path = saved({"contract": {"date": DATE, "loading_city": "Тула"},
                  "vehicles": [make_vehicles(1)[0]]})
    back = generator.read_template(str(path))

    assert back["zayavka"]["date"] == DATE
    assert back["zayavka"]["loading_city"] == "Тула"
    assert back["vehicles"] == make_vehicles(1)


def test_no_vehicles_means_no_shared_fields(generator, saved):
    """
    0 машин: общие сведения в файл не пишутся — формы для них нет.

    Это следствие устройства бланка: город, автовоз и водитель стоят
    колонками ТАБЛИЦЫ, и без строк писать их некуда. Замечание об этом
    даёт валидатор (tests/test_havaly_validator.py).
    """
    path = saved({"contract": {"date": DATE, "loading_city": "Тула",
                               "driver_phone": "+7 (999) 000-00-00"},
                  "vehicles": []})
    back = generator.read_template(str(path))

    assert back["zayavka"]["date"] == DATE
    assert back["zayavka"]["loading_city"] == ""
    assert back["zayavka"]["driver_phone"] == ""
    assert back["vehicles"] == []


def test_date_label_is_found_by_text(generator, out_dir):
    """Подпись «Дата заявки:» ищется по тексту, а не по номеру строки."""
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_NAME
    sheet["C9"] = "Дата заявки:"
    sheet["D9"] = "01.02.2027"
    for index, header in enumerate(HEADERS, start=1):
        sheet.cell(row=11, column=index, value=header)
    path = _staging("date_elsewhere.xlsx")
    book.save(str(path))

    data = generator.read_template(str(path))

    assert data["zayavka"]["date"] == "01.02.2027"


def test_date_falls_back_to_loading_plan_date(generator, out_dir):
    """
    Нет подписи с датой — берётся планируемая дата погрузки.

    Так же поступает распознавание: в форме это одна и та же дата, а
    ячейка «Дата заявки» может быть и не заполнена.
    """
    source = _file_like_form(
        _staging("no_date_label.xlsx"),
        rows=[["x"] * len(HEADERS)],
    )
    book = load_workbook(source)
    sheet = book[SHEET_NAME]
    sheet["A5"] = None
    sheet["B5"] = None
    sheet.cell(
        row=7,
        column=_column_of(sheet, HEADER_OF["loading_plan_date"]),
        value="07.10.2026",
    )
    book.save(str(source))

    data = generator.read_template(str(source))

    assert data["zayavka"]["date"] == "07.10.2026"


def test_table_stops_at_empty_row(generator, out_dir):
    """Строка, где пусты лот, VIN и марка, заканчивает таблицу."""
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_NAME
    for index, header in enumerate(HEADERS, start=1):
        sheet.cell(row=6, column=index, value=header)
    sheet.cell(row=7, column=2, value="VIN-1")
    # Пустая строка 8, затем «мусор» в строке 9 — он машиной не считается.
    sheet.cell(row=9, column=2, value="VIN-2")
    path = _staging("gap.xlsx")
    book.save(str(path))

    data = generator.read_template(str(path))

    assert [v["vin"] for v in data["vehicles"]] == ["VIN-1"]


def test_table_stops_at_party_block(generator, out_dir):
    """Строка «Заказчик» тоже заканчивает таблицу."""
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_NAME
    for index, header in enumerate(HEADERS, start=1):
        sheet.cell(row=6, column=index, value=header)
    sheet.cell(row=7, column=2, value="VIN-1")
    sheet.cell(row=8, column=3, value="Заказчик")
    sheet.cell(row=9, column=3, value="Не машина")
    path = _staging("parties.xlsx")
    book.save(str(path))

    data = generator.read_template(str(path))

    assert [v["vin"] for v in data["vehicles"]] == ["VIN-1"]


# ─────────────────────────────────────────────────────────────
# Логи: без ПДн
# ─────────────────────────────────────────────────────────────

def test_generation_logs_have_no_personal_data(generator, out_dir, caplog):
    """Логи генерации — только координаты, длины и количества."""
    with caplog.at_level(logging.DEBUG, logger="core.contract_generator"):
        path = Path(generator.generate(make_data(), str(out_dir)))
    try:
        messages = "\n".join(
            record.getMessage() for record in caplog.records
            if record.name == "core.contract_generator"
        )
        assert messages, "генерация ничего не записала в core.contract_generator"

        for fragment in PII_FRAGMENTS:
            assert fragment not in messages, f"в логе есть «{fragment}»"

        assert "записано" in messages
        assert "машин=" in messages
    finally:
        path.unlink(missing_ok=True)


def test_reading_logs_have_no_personal_data(generator, saved, caplog):
    """Логи чтения — количества и длины, без значений ячеек."""
    path = saved(make_data())

    with caplog.at_level(logging.DEBUG, logger="core.contract_generator"):
        generator.read_template(str(path))

    messages = "\n".join(
        record.getMessage() for record in caplog.records
        if record.name == "core.contract_generator"
    )
    assert messages
    for fragment in PII_FRAGMENTS:
        assert fragment not in messages, f"в логе есть «{fragment}»"


def test_fill_from_template_logs_have_no_personal_data(generator, templates_dir,
                                                       out_dir, caplog):
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
        path.unlink(missing_ok=True)


def test_warnings_about_missing_headers_name_columns_not_values(generator,
                                                               out_dir, caplog):
    """В предупреждении о шапке — только названия колонок."""
    source = _file_like_form(
        _staging("warn.xlsx"),
        rename={HEADER_OF["vin"]: "VIN-номер по документу"},
        rows=[["VIN-1"] * len(HEADERS)],
    )
    with caplog.at_level(logging.WARNING, logger="core.contract_generator"):
        generator.read_template(str(source))

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert HEADER_OF["vin"] in text
    assert "VIN-1" not in text


# ─────────────────────────────────────────────────────────────
# Постобработка
# ─────────────────────────────────────────────────────────────

def test_postprocess_step_is_declared(generator):
    """Конвейер постобработки объявлен и состоит из шага обрезки строк."""
    from core.contracts.zayavka.postprocess import TrimVehicleRowsStep

    steps = generator.postprocess_steps()

    assert len(steps) == 1
    assert isinstance(steps[0], TrimVehicleRowsStep)
    assert steps[0].name == "trim_vehicle_rows"
    assert steps[0].generator is generator


def test_trim_removes_only_trailing_rows(generator, saved):
    """Обрезка удаляет пустые строки в конце таблицы, данные не трогает."""
    vehicles = make_vehicles(3)
    path = saved(make_data(vehicles=vehicles))
    book = load_workbook(path)
    sheet = book[SHEET_NAME]

    for index in range(3):
        row = 7 + index
        assert sheet.cell(row=row, column=2).value == vehicles[index]["vin"]
    assert sheet.cell(row=10, column=2).value is None


def test_trim_keeps_template_layout_for_ten_vehicles(generator, saved):
    """10 машин: раскладка бланка не меняется — обрезать нечего."""
    path = saved(make_data(vehicles=make_vehicles(MAX_VEHICLES)))
    book = load_workbook(path)
    sheet = book[SHEET_NAME]
    template = load_workbook(generator.template_path())[SHEET_NAME]

    rows = {
        cell.value: cell.row
        for row in sheet.iter_rows() for cell in row if cell.value is not None
    }
    template_rows = {
        cell.value: cell.row
        for row in template.iter_rows() for cell in row if cell.value is not None
    }
    for label in ("Заказчик", "Перевозчик", "Сюрлогистик", "ФИО, подпись, печать"):
        assert rows[label] == template_rows[label]

    # Высоты строк нижнего блока тоже на месте: они едут вместе со строками.
    for row in range(17, 24):
        assert sheet.row_dimensions[row].height == \
            template.row_dimensions[row].height, f"высота строки {row}"


def test_trim_moves_block_with_row_heights(generator, saved):
    """После обрезки нижний блок получает свои высоты на новом месте."""
    path = saved(make_data(vehicles=make_vehicles(2)))
    book = load_workbook(path)
    sheet = book[SHEET_NAME]
    template = load_workbook(generator.template_path())[SHEET_NAME]

    # Блок сторон переехал с 19-й строки на 11-ю: высоты 12.75 и 15.75.
    assert sheet.row_dimensions[11].height == template.row_dimensions[19].height
    assert sheet.row_dimensions[12].height == template.row_dimensions[20].height
    assert sheet.row_dimensions[15].height == template.row_dimensions[23].height
    # И пустых строк-разделителей перед блоком — тоже (9-я и 10-я).
    assert sheet.row_dimensions[9].height == template.row_dimensions[17].height
    assert sheet.row_dimensions[10].height == template.row_dimensions[18].height


def test_fill_from_template_trims_more_rows_than_data(generator, out_dir, saved):
    """
    Присланный файл с лишними строками: они удаляются.

    Готовится файл с шестью СТАРЫМИ строками данных, а машин в заявке две:
    в результате строк должно остаться две, значения прежней заявки не
    должны попасть в готовый файл, а блок сторон — подняться наверх.
    """
    source = _file_with_headers(
        _staging("six_old_rows.xlsx"),
        HEADERS,
        [[f"старое-{index}"] * len(HEADERS) for index in range(6)],
    )

    path = saved(make_data(vehicles=make_vehicles(2)), method="fill",
                 source=str(source))
    back = generator.read_template(str(path))
    book = load_workbook(path)
    sheet = book[SHEET_NAME]

    assert back["vehicles"] == make_vehicles(2)

    values = {
        cell.value: cell.row
        for row in sheet.iter_rows() for cell in row if cell.value is not None
    }
    # Значения прежней заявки уехали вместе с удалёнными строками: остались
    # только те, что перезаписаны новыми машинами.
    stale = sorted(
        str(value) for value in values if str(value).startswith("старое-")
    )
    assert stale == ["старое-0", "старое-1"]

    # Строк было 6, машин 2 — удалены 4 лишние, и низ бланка поднялся ровно
    # на столько же: блок сторон был в 15-й строке, стал в 11-й.
    assert values["Заказчик"] == 11
    assert values["Сюрлогистик"] == 12
    assert values["ФИО, подпись, печать"] == 15
