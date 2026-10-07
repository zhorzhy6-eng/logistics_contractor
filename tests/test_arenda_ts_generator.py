#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты генератора договора аренды ТС с экипажем (ЭТАП 3.1.D.A.3).

Проверяют: тип больше не заглушка; три бланка (ООО / ИП с НДС / ИП без НДС)
рендерятся в изолированную папку; все плейсхолдеры своего варианта получают
значения, а в готовом документе не остаётся ни одного «{{…}}»; в вариантах с
НДС три суммы (без НДС, НДС по ставке, итого), в варианте ИП без НДС — одна
сумма и оговорка «НДС не облагается»; КПП и метка «ОГРНИП» — только у ИП;
таблица машин рассчитана на переменное число строк, а незаполненные точки
погрузки и выгрузки удаляются постобработкой; Приложение № 1 (Акт) заполнено
теми же значениями (тягач, прицеп, экипаж, подписи); логи идут в канал
«core.contract_generator» и не содержат персональных данных.

Данные теста — в том виде, в каком их отдаёт распознавание
(core/prompts/arenda_ts.py): блоки lessee / lessor, route, срок аренды и точки
маршрута лежат В КОРНЕ ответа, суммы — в contract. Так проверяется и стык
«промпт → генератор»: корневые поля обязаны дойти до бланка (generate()
переносит их в contract, см. докстринг core/contracts/arenda_ts/generator.py).

Шаги постобработки проверяются здесь на готовом документе (после generate());
отдельно, на программно собранном Document() без шаблона, те же шаги и их
краевые случаи разобраны в tests/test_arenda_ts_postprocess.py.

Все данные синтетические, реальных ПДн нет.
"""

import gc
import logging
import re
from pathlib import Path

import pytest
from docx import Document

from core.contracts.arenda_ts.generator import (
    MAX_CARS,
    MAX_POINTS,
    TRANSFER_DOCUMENTS,
    ArendaTsGenerator,
)
from core.contracts.arenda_ts.postprocess import (
    RemoveEmptyLoadingUnloadingBlocksStep,
    RemoveEmptyVehicleRowsStep,
)
from core.contracts.base_generator import ConvertNewlinesStep
from core.contracts.factory import GeneratorFactory
from core.contracts.registry import ContractTypeRegistry

#: Варианты бланка: ключ — contract["carrier_type"], значение — файл шаблона.
TEMPLATE_NAMES = {
    "ООО": "shablon_arenda_ts_ooo.docx",
    "ИП с НДС": "shablon_arenda_ts_ip_with_vat.docx",
    "ИП без НДС": "shablon_arenda_ts_ip_without_vat.docx",
}

VARIANTS = tuple(TEMPLATE_NAMES)

#: Варианты, в которых арендная плата облагается НДС (три суммы).
VAT_VARIANTS = ("ООО", "ИП с НДС")

#: Заголовки таблицы машин п. 3.1.
CAR_HEADERS = ("№", "Марка, модель", "VIN-номер", "Точка погрузки",
               "Точка выгрузки")

PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}")

#: Нумерованные строки точек маршрута: «3.2.N. …» / «3.3.N. …».
LOADING_LINE_RE = re.compile(r"^3\.2\.\d+\.")
UNLOADING_LINE_RE = re.compile(r"^3\.3\.\d+\.")

#: Суммы теста: 221 099,18 + 22% = 48 641,82 → итого 269 741,00.
BASE_SUM = 221099.18
VAT_SUM = 48641.82
TOTAL_SUM = 269741.00


@pytest.fixture(autouse=True)
def _release_documents():
    """Word-файлы на Windows освобождаем до удаления временных копий."""
    yield
    gc.collect()


@pytest.fixture(scope="module", autouse=True)
def _builtin_types_loaded():
    """Регистрация типов: без неё фабрика не знает про arenda_ts."""
    ContractTypeRegistry.load_builtin()


@pytest.fixture
def generator(templates_dir) -> ArendaTsGenerator:
    return ArendaTsGenerator(templates_dir=str(templates_dir))


# ─────────────────────────────────────────────────────────────
# Тестовые данные (формат ответа распознавания)
# ─────────────────────────────────────────────────────────────

def _lessee(variant: str = "ООО") -> dict:
    """Арендатор — наша сторона: ООО или ИП (от неё зависит бланк)."""
    if variant == "ООО":
        return {
            "entity_type": "ООО",
            "full_name": "ООО «Арендатор Тест»",
            "short_name": "ООО «АТ»",
            "inn": "7701234567",
            "kpp": "770101001",
            "ogrn": "1027700132195",
            "legal_address": "г. Москва, ул. Тестовая, д. 1",
            "actual_address": "г. Москва, ул. Фактическая, д. 11",
            "bank_account": "40702810000000000001",
            "bank_name": "ПАО Сбербанк",
            "bik": "044525225",
            "corr_account": "30101810400000000225",
            "director_name": "Петров Пётр Петрович",
            "director_position": "Генеральный директор",
            "phone": "+7 (495) 123-45-67",
            "email": "arenda@example.ru",
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
        "actual_address": "г. Москва, ул. Фактическая, д. 17",
        "bank_account": "40802810000000000011",
        "bank_name": "АО «Банк Тест»",
        "bik": "044525227",
        "corr_account": "30101810400000000227",
        "director_name": "Смирнов Сергей Сергеевич",
        "director_position": "Индивидуальный предприниматель",
        "phone": "+7 (495) 123-45-68",
        "email": "ip@example.ru",
    }


def _lessor() -> dict:
    """Арендодатель — вторая сторона (в бланках всегда ООО)."""
    return {
        "full_name": "ООО «Арендодатель Тест»",
        "short_name": "ООО «АДТ»",
        "inn": "7709876543",
        "ogrn": "1027700132196",
        "legal_address": "г. Москва, ул. Вторая, д. 2",
        "actual_address": "г. Москва, ул. Вторая фактическая, д. 22",
        "bank_account": "40702810000000000002",
        "bank_name": "АО «Банк Второй»",
        "bik": "044525226",
        "corr_account": "30101810400000000226",
        "director_name": "Сидоров Сидор Сидорович",
        "director_position": "Директор",
        "email": "lessor@example.ru",
    }


def _vehicle(number: int) -> dict:
    """Машина таблицы п. 3.1 (не тягач и не прицеп)."""
    return {
        "brand_model": f"МОДЕЛЬ {number}",
        "vin": f"TESTVIN000000000{number:02d}",
        "loading_point": f"Точка погрузки {number}",
        "unloading_point": f"Точка выгрузки {number}",
    }


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


def _lessee_signature(variant: str) -> str:
    """Строка подписи Арендатора: фамилия с инициалами (как в образце)."""
    short = "П.П. Петров" if variant == "ООО" else "С.С. Смирнов"
    return f"______________/ {short} /"


def _payload(
    variant: str = "ООО",
    cars: int = 2,
    loadings: int = 2,
    unloadings: int = 1,
    contract_extra=None,
    carrier_type: str = "auto",
) -> dict:
    """
    Синтетические данные договора аренды в формате ответа распознавания.

    carrier_type="auto" — поле contract["carrier_type"] равно варианту (его
    заполняет интерфейс); можно задать и явное значение. carrier_type=None —
    поля нет, и вид Арендатора выводится из lessee["entity_type"] и ставки НДС.
    """
    without_vat = variant == "ИП без НДС"

    contract = {
        "number": "АБ-123",
        "date": "2026-09-19",
        "vat_rate": "0%" if without_vat else "22%",
        "sum_wo_vat": 0.0 if without_vat else BASE_SUM,
        "sum_vat": 0.0 if without_vat else VAT_SUM,
        "sum_total": BASE_SUM if without_vat else TOTAL_SUM,
    }
    if carrier_type == "auto":
        carrier_type = variant
    if carrier_type:
        contract["carrier_type"] = carrier_type
    if contract_extra:
        contract.update(contract_extra)

    return {
        "lessee": _lessee(variant),
        "lessor": _lessor(),
        "tractor": {
            "brand_model": "Тягач-Модель",
            "plate_number": "А001АА01",
            "vehicle_type": "грузовой тягач седельный",
        },
        "trailer": {"brand_model": "Прицеп-Модель", "plate_number": "Б002ББ02"},
        "lease_start_date": "2026-09-20",
        "lease_end_date": "2026-09-28",
        "route": "Москва — Калуга — Чехов",
        "vehicles": [_vehicle(number) for number in range(1, cars + 1)],
        "loadings": [_loading(number) for number in range(1, loadings + 1)],
        "unloadings": [_unloading(number) for number in range(1, unloadings + 1)],
        "driver": {
            "full_name": "Иванов Иван Иванович",
            "birth_date": "1980-01-01",
            "passport": "18 22 926830",
            "passport_issuer": "Отделом УФМС России по г. Москве",
            "passport_issue_date": "2023-01-30",
            "license": "99 36 123456",
            "license_issue_date": "2020-01-01",
            "address": "г. Москва, ул. Водительская, д. 3",
            "phone": "+7 (999) 123-45-67",
        },
        "contract": contract,
    }


# ─────────────────────────────────────────────────────────────
# Чтение готового документа
# ─────────────────────────────────────────────────────────────

def _document_text(doc) -> str:
    """Весь текст документа: абзацы и таблицы (включая вложенные)."""
    parts = [p.text for p in doc.paragraphs]

    def table_text(table):
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
                for nested in cell.tables:
                    table_text(nested)

    for table in doc.tables:
        table_text(table)

    return "\n".join(parts)


def _body_texts(doc):
    """Тексты абзацев верхнего уровня без крайних пробелов."""
    return [p.text.strip() for p in doc.paragraphs]


def _car_table(doc):
    """Таблица машин п. 3.1 — по заголовкам колонок, а не по индексу."""
    for table in doc.tables:
        if not table.rows:
            continue
        if tuple(cell.text.strip() for cell in table.rows[0].cells) == CAR_HEADERS:
            return table
    return None


def _requisites_table(doc):
    """Таблица раздела 9: 1×2 с блоками «АРЕНДАТОР:» и «АРЕНДОДАТЕЛЬ:»."""
    for table in doc.tables:
        if len(table.rows) != 1 or len(table.columns) != 2:
            continue
        left, right = table.rows[0].cells
        if (left.text.startswith("АРЕНДАТОР:")
                and right.text.startswith("АРЕНДОДАТЕЛЬ:")):
            return table
    return None


def _act_table(doc, label: str):
    """Таблица Акта по тексту первой ячейки (метка поля)."""
    for table in doc.tables:
        if not table.rows:
            continue
        if table.rows[0].cells[0].text.strip() == label:
            return table
    return None


def _appendix_signature_table(doc):
    """Таблица подписей Акта: 1×2, в левой ячейке нет реквизитов (ИНН)."""
    for table in doc.tables:
        if len(table.rows) != 1 or len(table.columns) != 2:
            continue
        left, right = table.rows[0].cells
        if (left.text.startswith("АРЕНДАТОР:")
                and right.text.startswith("АРЕНДОДАТЕЛЬ:")
                and "ИНН" not in left.text):
            return table
    return None


def _act_values(doc, label: str) -> dict:
    """{метка строки: значение} таблицы Акта."""
    table = _act_table(doc, label)
    assert table is not None, f"в Акте нет таблицы «{label}»"
    return {
        row.cells[0].text.strip(): row.cells[1].text.strip()
        for row in table.rows
    }


def _point_lines(doc, pattern) -> list:
    """Нумерованные строки точек маршрута (без заголовка раздела)."""
    return [text for text in _body_texts(doc) if pattern.match(text)]


def _generate(generator, payload, output_dir) -> str:
    path = generator.generate(payload, output_dir=str(output_dir))
    assert Path(path).exists(), f"файл не создан: {path}"
    return path


def _coerced(payload):
    """Данные так, как их видит оркестрация базы: ContractData после generate()."""
    from core.contract_data import ContractData

    return ContractData.coerce(ArendaTsGenerator._hoist_contract_fields(payload))


def _template_placeholder_names(templates_dir, variant) -> set:
    """Имена плейсхолдеров бланка этого варианта."""
    name = ArendaTsGenerator.TEMPLATE_NAMES[variant]
    text = _document_text(Document(str(templates_dir / name)))
    return {match.strip("{} ") for match in PLACEHOLDER_RE.findall(text)}


# ─────────────────────────────────────────────────────────────
# Тип зарегистрирован и больше не заглушка
# ─────────────────────────────────────────────────────────────

def test_generator_is_registered_and_not_stub():
    spec = ContractTypeRegistry.get("arenda_ts", strict=True)
    assert spec.generator_class is ArendaTsGenerator
    assert isinstance(
        GeneratorFactory.get_generator("arenda_ts", strict=True),
        ArendaTsGenerator,
    )


def test_type_metadata_matches_templates(generator):
    assert ArendaTsGenerator.CONTRACT_TYPE == "arenda_ts"
    assert ArendaTsGenerator.FILE_PREFIX == "Договор_аренды_ТС"
    assert ArendaTsGenerator.TEMPLATE_NAMES == TEMPLATE_NAMES
    assert ArendaTsGenerator.DEFAULT_VAT_RATE == 22.0

    # Размеры бланка — те же, что проверяет tests/test_arenda_ts_template.py.
    assert MAX_CARS == 12
    assert MAX_POINTS == 10

    for key, filename in TEMPLATE_NAMES.items():
        assert generator.templates[key].endswith(filename)


def test_generate_does_not_raise_not_implemented(generator, work_dir):
    path = _generate(generator, _payload(cars=1), work_dir)
    assert path.endswith(".docx")


def test_filename_uses_prefix_number_and_date(generator, work_dir):
    path = Path(_generate(generator, _payload(cars=1), work_dir))
    assert path.name.startswith(f"{ArendaTsGenerator.FILE_PREFIX}_АБ-123_")


def test_default_output_dir_is_output():
    assert Path(ArendaTsGenerator.default_output_dir()).name == "output"


# ─────────────────────────────────────────────────────────────
# Выбор шаблона и конвейер постобработки
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "carrier_type, filename",
    [
        ("ООО", "shablon_arenda_ts_ooo.docx"),
        ("ООО (с НДС)", "shablon_arenda_ts_ooo.docx"),
        ("", "shablon_arenda_ts_ooo.docx"),
        ("что-то иное", "shablon_arenda_ts_ooo.docx"),
        ("ИП с НДС", "shablon_arenda_ts_ip_with_vat.docx"),
        ("ИП без НДС", "shablon_arenda_ts_ip_without_vat.docx"),
    ],
)
def test_template_path_selects_variant(generator, carrier_type, filename):
    assert generator._get_template_path(carrier_type).endswith(filename)


def test_postprocess_steps_order(generator):
    steps = generator.postprocess_steps(None)
    assert [type(step) for step in steps] == [
        ConvertNewlinesStep,
        RemoveEmptyVehicleRowsStep,
        RemoveEmptyLoadingUnloadingBlocksStep,
    ]
    assert [step.name for step in steps] == [
        "convert_newlines",
        "remove_empty_vehicle_rows",
        "remove_empty_loading_unloading_blocks",
    ]


def test_insert_route_tables_is_noop(generator):
    """Таблиц по точкам маршрута в бланке нет — метод обязан быть безопасным."""
    doc = Document()
    doc.add_paragraph("текст")
    assert generator._insert_route_tables(doc, None) is None
    assert len(doc.paragraphs) == 1


# ─────────────────────────────────────────────────────────────
# Рендер трёх шаблонов
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("variant", VARIANTS)
def test_all_templates_render_to_isolated_dir(generator, work_dir, variant):
    """Три бланка рендерятся в изолированную папку без исключений."""
    output_dir = work_dir / f"arenda_render_{variant.replace(' ', '_')}"
    output_dir.mkdir(parents=True, exist_ok=True)

    path = Path(_generate(generator, _payload(variant), output_dir))
    assert path.parent == output_dir, "файл ушёл мимо изолированной папки"

    text = _document_text(Document(path))
    assert "{{" not in text and "}}" not in text

    # Акт — часть того же файла.
    assert "Приложение № 1" in text
    assert "АКТ ПРИЕМА-ПЕРЕДАЧИ И ВОЗВРАТА" in text


@pytest.mark.parametrize("variant", VARIANTS)
def test_all_fields_are_substituted(generator, work_dir, variant):
    """Каждое поле данных попадает в документ своего варианта."""
    output_dir = work_dir / f"arenda_fields_{variant.replace(' ', '_')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _generate(generator, _payload(variant), output_dir)

    doc = Document(path)
    text = _document_text(doc)
    texts = _body_texts(doc)

    # Шапка и срок аренды.
    assert "С ЭКИПАЖЕМ № АБ-123" in text
    assert "19.09.2026" in text
    assert any(t.startswith("2.5. Плановый период аренды: с 20.09.2026 г. по "
                            "28.09.2026 г. включительно")
               for t in texts), "нет планового периода аренды"

    # Стороны.
    assert _lessee(variant)["full_name"] in text
    assert "ООО «Арендодатель Тест»" in text
    assert "ИНН 7709876543" in text

    # Объект аренды и машины.
    assert ("– тягач: Тягач-Модель, государственный регистрационный знак "
            "А001АА01, тип ТС — грузовой тягач седельный;") in texts
    assert ("– прицеп/полуприцеп: Прицеп-Модель, государственный "
            "регистрационный знак Б002ББ02.") in texts
    assert "Общее количество: 2 шт." in texts

    # Маршрут и точки.
    assert "3.4. Согласованный маршрут: Москва — Калуга — Чехов." in texts
    assert ("3.2.1. Точка погрузки № 1 — Адрес погрузки 1. Плановая дата и "
            "время подачи ТС: 21.09.2026 г., с 08:00 до 18:00.") in texts
    assert ("3.3.1. Точка выгрузки № 1 — Адрес выгрузки 1. Плановая дата "
            "завершения: 27.09.2026 г.") in texts

    # Экипаж.
    assert "ФИО: Иванов Иван Иванович" in texts
    assert "Дата рождения: 01.01.1980" in texts
    assert "Паспорт: 18 22 926830" in texts
    assert "Выдан: Отделом УФМС России по г. Москве" in texts
    assert "Дата выдачи: 30.01.2023" in texts
    assert "Водительское удостоверение: 99 36 123456" in texts
    assert "Адрес регистрации: г. Москва, ул. Водительская, д. 3" in texts
    assert "Телефон: +7 (999) 123-45-67" in texts

    # Машины в таблице п. 3.1 — вместе с точками погрузки и выгрузки.
    table = _car_table(doc)
    assert table is not None, "не найдена таблица машин"
    assert [cell.text.strip() for cell in table.rows[1].cells] == [
        "1", "МОДЕЛЬ 1", "TESTVIN00000000001",
        "Точка погрузки 1", "Точка выгрузки 1",
    ]
    assert [cell.text.strip() for cell in table.rows[2].cells] == [
        "2", "МОДЕЛЬ 2", "TESTVIN00000000002",
        "Точка погрузки 2", "Точка выгрузки 2",
    ]


@pytest.mark.parametrize("variant", VARIANTS)
def test_requisites_table_has_both_parties(generator, work_dir, variant):
    """Раздел 9: реквизиты обеих сторон, подписи и М.П."""
    output_dir = work_dir / f"arenda_req_{variant.replace(' ', '_')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _generate(generator, _payload(variant), output_dir)

    table = _requisites_table(Document(path))
    assert table is not None, "не найдена таблица реквизитов"

    lessee = table.rows[0].cells[0].text
    lessor = table.rows[0].cells[1].text
    lessee_data = _lessee(variant)

    assert lessee_data["full_name"] in lessee
    assert f"ИНН {lessee_data['inn']}" in lessee
    assert lessee_data["legal_address"] in lessee
    assert (f"р/с {lessee_data['bank_account']} в {lessee_data['bank_name']}"
            in lessee)
    assert f"БИК {lessee_data['bik']}" in lessee
    assert f"к/с {lessee_data['corr_account']}" in lessee
    assert f"E-mail: {lessee_data['email']}" in lessee
    assert _lessee_signature(variant) in lessee
    assert "М.П." in lessee

    assert "ООО «Арендодатель Тест»" in lessor
    assert "ИНН 7709876543" in lessor
    assert "ОГРН 1027700132196" in lessor
    assert "р/с 40702810000000000002 в АО «Банк Второй»" in lessor
    assert "______________/ С.С. Сидоров /" in lessor
    assert "М.П." in lessor


@pytest.mark.parametrize("variant", VARIANTS)
def test_replacements_map_covers_all_template_placeholders(
    generator, templates_dir, variant
):
    """
    Каждый плейсхолдер бланка получает значение, лишних ключей нет.

    Одно исключение — unloading_2_date: в бланке его больше нет (п. 3.3.2
    печатает {{planned_completion_date}}, шаг FIX-1-T), но ключ остаётся в
    карте замен: он парный к unloading_2_address и заполняется датой точки из
    вкладки «Маршрут». В документ он не идёт — бланк его не спрашивает.
    """
    names = _template_placeholder_names(templates_dir, variant)
    assert names, "в шаблоне не найдено ни одного плейсхолдера"

    replacements = generator._build_replacements_map(_payload(variant))

    assert set(replacements) - names == {"unloading_2_date"}, (
        f"лишние ключи карты замен ({variant}): "
        f"{sorted(set(replacements) - names - {'unloading_2_date'})}"
    )
    assert not (names - set(replacements)), (
        f"плейсхолдеры бланка без значений ({variant}): "
        f"{sorted(names - set(replacements))}"
    )


def test_replacements_map_covers_205_placeholders_of_ooo_template(
    generator, templates_dir
):
    """
    В ООО-бланке 205 плейсхолдеров — все они (кроме unloading_2_date) есть
    в карте замен.

    История числа: 187 → 189 (шаг FIX-1-T: п. 3.3.2 отдал {{unloading_2_date}}
    под {{planned_completion_date}} — минус один плейсхолдер и два новых
    в п. 4.5) → 205 (шаг FIX-3: шесть полей подписанта и фактического адреса
    в п. 1.1 / 1.2 и разделе 9, десять полей Акта вместо пустых ячеек).
    """
    name = ArendaTsGenerator.TEMPLATE_NAMES["ООО"]
    text = _document_text(Document(str(templates_dir / name)))
    occurrences = [match.strip("{} ") for match in PLACEHOLDER_RE.findall(text)]
    assert len(occurrences) == 205, (
        f"в ООО-бланке {len(occurrences)} плейсхолдеров, ожидалось 205"
    )

    replacements = generator._build_replacements_map(_payload("ООО"))
    assert not [item for item in occurrences if item not in replacements]


@pytest.mark.parametrize("variant", VARIANTS)
def test_generated_document_has_no_placeholders_left(generator, work_dir, variant):
    output_dir = work_dir / f"arenda_leftovers_{variant.replace(' ', '_')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _generate(generator, _payload(variant), output_dir)
    text = _document_text(Document(path))

    assert "{{" not in text, f"в документе {variant} остался плейсхолдер"
    assert "}}" not in text


def test_generate_with_empty_data_does_not_crash(generator, work_dir):
    """Пустые данные — пустой бланк без исключений (заглушки больше нет)."""
    path = _generate(generator, {"contract": {}}, work_dir)
    text = _document_text(Document(path))

    assert "ДОГОВОР АРЕНДЫ ТРАНСПОРТНОГО СРЕДСТВА" in text
    assert "{{" not in text
    # Сторон нет — пустые строки вместо выдуманных реквизитов.
    assert "ООО «Арендатор Тест»" not in text
    assert "Арендодатель:" in text


def test_missing_template_raises_file_not_found(work_dir):
    generator = ArendaTsGenerator(templates_dir=str(work_dir / "нет-такого"))
    with pytest.raises(FileNotFoundError):
        generator.generate(_payload(cars=1), output_dir=str(work_dir))


@pytest.mark.parametrize(
    "carrier_type, lessee_variant, filename",
    [
        ("ООО", "ООО", "shablon_arenda_ts_ooo.docx"),
        ("ООО (с НДС)", "ООО", "shablon_arenda_ts_ooo.docx"),
        ("ИП с НДС", "ИП с НДС", "shablon_arenda_ts_ip_with_vat.docx"),
        ("ИП без НДС", "ИП без НДС", "shablon_arenda_ts_ip_without_vat.docx"),
    ],
)
def test_template_path_follows_carrier_type(
    generator, carrier_type, lessee_variant, filename
):
    """Явное contract["carrier_type"] задаёт бланк (поле заполняет интерфейс)."""
    payload = _payload(lessee_variant, carrier_type=carrier_type)

    assert generator.get_template_path(_coerced(payload)).endswith(filename)
    assert generator._carrier_type_of(_coerced(payload)) == carrier_type


# ─────────────────────────────────────────────────────────────
# Стоимость: три суммы для ООО и ИП с НДС, одна для ИП без НДС
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("variant", VAT_VARIANTS)
def test_vat_variants_have_three_sums(generator, work_dir, variant):
    """ООО и ИП с НДС: без НДС + НДС по ставке = итого, каждая с прописью."""
    output_dir = work_dir / f"arenda_cost_{variant.replace(' ', '_')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _generate(generator, _payload(variant), output_dir)
    texts = _body_texts(Document(path))

    assert ("– 221\u00a0099,18 руб. (Двести двадцать одна тысяча девяносто "
            "девять рублей восемнадцать копеек) — стоимость без НДС;") in texts
    assert ("– НДС 22% — 48\u00a0641,82 руб. (Сорок восемь тысяч шестьсот "
            "сорок один рубль восемьдесят две копейки);") in texts
    assert ("Итого с НДС: 269\u00a0741,00 руб. (Двести шестьдесят девять "
            "тысяч семьсот сорок один рубль 00 копеек).") in texts


@pytest.mark.parametrize("variant", VAT_VARIANTS)
def test_vat_variants_values_in_replacements(generator, variant):
    replacements = generator._build_replacements_map(_payload(variant))

    assert replacements["vat_rate"] == "22%"
    assert replacements["sum_wo_vat"] == "221\u00a0099,18"
    assert replacements["sum_vat"] == "48\u00a0641,82"
    assert replacements["sum_total"] == "269\u00a0741,00"
    assert replacements["sum_wo_vat_words"].startswith("Двести двадцать одна тысяча")
    assert replacements["sum_vat_words"].startswith("Сорок восемь тысяч")
    assert replacements["sum_total_words"].startswith("Двести шестьдесят девять тысяч")


@pytest.mark.parametrize("variant", VAT_VARIANTS)
def test_vat_variants_state_vat_payer(generator, work_dir, variant):
    """В вариантах с НДС Арендодатель подтверждает общую систему."""
    output_dir = work_dir / f"arenda_vat_{variant.replace(' ', '_')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    text = _document_text(Document(_generate(generator, _payload(variant), output_dir)))

    assert "применяет общую систему налогообложения" in text
    assert "является плательщиком НДС" in text
    assert "НДС не облагается" not in text


def test_ip_without_vat_has_single_sum(generator, work_dir):
    """ИП без НДС: одна сумма, плейсхолдеров НДС в карте замен нет."""
    payload = _payload("ИП без НДС", carrier_type="auto")
    replacements = generator._build_replacements_map(payload)

    assert replacements["sum_total"] == "221\u00a0099,18"
    assert replacements["sum_total_words"].startswith("Двести двадцать одна тысяча")
    for name in ("sum_wo_vat", "sum_vat", "vat_rate",
                 "sum_wo_vat_words", "sum_vat_words"):
        assert name not in replacements, f"в вариант без НДС попал ключ {name!r}"

    output_dir = work_dir / "arenda_cost_ip_without_vat"
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = Document(_generate(generator, payload, output_dir))
    texts = _body_texts(doc)

    assert texts.count(
        "221\u00a0099,18 руб. (Двести двадцать одна тысяча девяносто девять "
        "рублей восемнадцать копеек)."
    ) == 1
    assert "НДС не облагается (упрощённая система налогообложения)." in texts
    assert not [t for t in texts if t.startswith("Итого с НДС:")]
    assert not [t for t in texts if t.startswith("– НДС")]

    text = _document_text(doc)
    assert "применяет упрощённую систему налогообложения" in text
    assert "не является плательщиком НДС" in text


def test_ip_without_vat_takes_single_recognized_sum(generator):
    """
    У ИП без НДС единственная сумма документа лежит в sum_total.

    Промпт (core/prompts/arenda_ts.py) для варианта «НДС не облагается»
    кладёт сумму в sum_total, а sum_wo_vat оставляет нулём: если читать
    только sum_wo_vat, в бланк уйдёт 0,00.
    """
    payload = _payload("ИП без НДС")
    assert payload["contract"]["sum_wo_vat"] == 0.0

    replacements = generator._build_replacements_map(payload)
    assert replacements["sum_total"] == "221\u00a0099,18"


def test_vat_rate_can_come_as_number(generator):
    """Ставка числом (vat_rate_num) работает наравне со строкой «22%»."""
    payload = _payload("ООО", contract_extra={"vat_rate_num": 22})
    payload["contract"].pop("vat_rate")

    replacements = generator._build_replacements_map(payload)
    assert replacements["vat_rate"] == "22%"
    assert replacements["sum_vat"] == "48\u00a0641,82"


def test_cost_falls_back_to_price_without_vat(generator):
    """Без распознанной суммы берётся price_without_vat вкладки «Стоимость»."""
    payload = _payload("ООО")
    payload["contract"].pop("sum_wo_vat")
    payload["contract"]["price_without_vat"] = BASE_SUM

    replacements = generator._build_replacements_map(payload)

    assert replacements["sum_wo_vat"] == "221\u00a0099,18"
    assert replacements["sum_total"] == "269\u00a0741,00"


def test_cost_missing_is_zero_not_invented(generator):
    payload = _payload("ООО")
    for key in ("sum_wo_vat", "sum_total", "sum_vat"):
        payload["contract"].pop(key)

    replacements = generator._build_replacements_map(payload)

    assert replacements["sum_wo_vat"] == "0,00"
    assert replacements["sum_vat"] == "0,00"
    assert replacements["sum_total"] == "0,00"
    assert replacements["vat_rate"] == "22%"


def test_vat_only_sum_total_warns_about_zero(generator, caplog):
    """
    Только сумма с НДС (sum_total) — арендная плата в бланке обнуляется.

    Сумма без НДС из суммы с НДС не выводится (не выдумываем данные), но о
    стыке сообщается предупреждением.
    """
    payload = _payload("ООО")
    payload["contract"].pop("sum_wo_vat")

    with caplog.at_level(logging.WARNING, logger="core.contract_generator"):
        replacements = generator._build_replacements_map(payload)

    assert replacements["sum_wo_vat"] == "0,00"
    assert "в бланк уйдёт 0,00" in caplog.text


# ─────────────────────────────────────────────────────────────
# Вид Арендатора, КПП и метка госрегистрации
# ─────────────────────────────────────────────────────────────

def test_ooo_has_kpp_and_ogrn(generator, work_dir):
    output_dir = work_dir / "arenda_ooo_requisites"
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = Document(_generate(generator, _payload("ООО"), output_dir))

    text = _document_text(doc)
    assert "КПП 770101001" in text
    assert "ОГРН 1027700132195" in text
    assert "ОГРНИП" not in text

    replacements = generator._build_replacements_map(_payload("ООО"))
    assert replacements["lessee_ogrn_label"] == "ОГРН"
    assert replacements["lessee_basis"] == "Устава"


@pytest.mark.parametrize("variant", ("ИП с НДС", "ИП без НДС"))
def test_ip_variants_have_no_kpp_but_ogrnip(generator, work_dir, variant):
    output_dir = work_dir / f"arenda_ip_req_{variant.replace(' ', '_')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = Document(_generate(generator, _payload(variant), output_dir))
    text = _document_text(doc)

    assert "КПП" not in text, "у ИП не бывает КПП"
    assert "ОГРНИП 321770000123456" in text

    replacements = generator._build_replacements_map(_payload(variant))
    assert "lessee_kpp" not in replacements
    assert replacements["lessee_ogrn_label"] == "ОГРНИП"
    assert replacements["lessee_basis"] == "свидетельства о государственной регистрации"


def test_lessee_wording_matches_variant(generator, work_dir):
    """ООО — «именуемое», ИП — «именуемый» (формулировка в бланке)."""
    for variant in VARIANTS:
        output_dir = work_dir / f"arenda_wording_{variant.replace(' ', '_')}"
        output_dir.mkdir(parents=True, exist_ok=True)
        texts = _body_texts(Document(_generate(generator, _payload(variant), output_dir)))
        line = next(t for t in texts if t.startswith("1.1. Арендатор:"))

        if variant == "ООО":
            assert "именуемое в дальнейшем «Арендатор»" in line
        else:
            assert "именуемый в дальнейшем «Арендатор»" in line


def test_lessor_is_always_ooo(generator):
    """Арендодатель — вторая сторона: ОГРН и устав, КПП в бланке нет."""
    replacements = generator._build_replacements_map(
        _payload("ИП без НДС")
    )

    assert replacements["lessor_ogrn_label"] == "ОГРН"
    assert replacements["lessor_basis"] == "Устава"
    assert "lessor_kpp" not in replacements


def test_lessor_ip_warns_but_substitutes(generator, caplog):
    """Арендодатель-ИП в данных — расхождение: реквизиты идут как есть."""
    payload = _payload()
    payload["lessor"]["entity_type"] = "ИП"

    with caplog.at_level(logging.WARNING, logger="core.contract_generator"):
        replacements = generator._build_replacements_map(payload)

    assert replacements["lessor_ogrn_label"] == "ОГРН"
    assert "ООО «Арендодатель Тест»" == replacements["lessor_full_name"]
    assert "Арендодатель в данных — ИП" in caplog.text


# ─────────────────────────────────────────────────────────────
# Таблица машин: переменное число строк
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cars", [1, 2, 3, 12])
def test_car_table_keeps_only_filled_rows(generator, work_dir, cars):
    output_dir = work_dir / f"arenda_cars_{cars}"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _generate(generator, _payload(cars=cars), output_dir)
    table = _car_table(Document(path))

    assert table is not None, "таблица машин не найдена в документе"
    assert len(table.rows) == cars + 1, (
        f"машин {cars}, а строк в таблице {len(table.rows)} (ожидалось {cars + 1})"
    )
    for number in range(1, cars + 1):
        assert [cell.text.strip() for cell in table.rows[number].cells] == [
            str(number),
            f"МОДЕЛЬ {number}",
            f"TESTVIN000000000{number:02d}",
            f"Точка погрузки {number}",
            f"Точка выгрузки {number}",
        ]


def test_generate_without_vehicles_keeps_header_only(generator, work_dir):
    """Ни одной машины — в документе остаётся только шапка таблицы."""
    output_dir = work_dir / "arenda_cars_none"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _generate(generator, _payload(cars=0), output_dir)
    table = _car_table(Document(path))

    assert len(table.rows) == 1
    assert "Общее количество: 0 шт." in _body_texts(Document(path))
    assert generator._build_replacements_map(_payload(cars=0))["cargo_count"] == "0"


def test_cargo_count_matches_vehicles(generator):
    for cars in (0, 1, 5, 12):
        replacements = generator._build_replacements_map(_payload(cars=cars))
        assert replacements["cargo_count"] == str(cars)


def test_tractor_and_trailer_are_not_in_car_table(generator, work_dir):
    """Тягач и полуприцеп из справочника машин в таблицу 3.1 не попадают."""
    payload = _payload(cars=2)
    payload["vehicles"] += [
        {"brand_model": "Тягач-Модель", "plate_number": "А001АА01",
         "vehicle_type": "Тягач"},
        {"brand_model": "Прицеп-Модель", "plate_number": "Б002ББ02",
         "vehicle_type": "Полуприцеп"},
    ]

    output_dir = work_dir / "arenda_cars_tractor"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _generate(generator, payload, output_dir)
    table = _car_table(Document(path))
    text = "\n".join(cell.text for row in table.rows for cell in row.cells)

    assert len(table.rows) == 3, "тягач или прицеп попали в таблицу машин"
    assert "Тягач-Модель" not in text
    assert "Прицеп-Модель" not in text
    assert generator._build_replacements_map(payload)["cargo_count"] == "2"


def test_more_than_twelve_cars_are_truncated(generator, caplog):
    with caplog.at_level(logging.WARNING, logger="core.contract_generator"):
        replacements = generator._build_replacements_map(_payload(cars=14))

    assert replacements["cargo_count"] == "14"
    assert replacements["car_12_brand"] == "МОДЕЛЬ 12"
    assert "car_13_brand" not in replacements
    assert "в бланк помещается 12" in caplog.text


def test_missing_cars_give_empty_placeholders(generator):
    replacements = generator._build_replacements_map(_payload(cars=1))

    assert replacements["car_1_brand"] == "МОДЕЛЬ 1"
    for number in range(2, 13):
        assert replacements[f"car_{number}_brand"] == ""
        assert replacements[f"car_{number}_vin"] == ""
        assert replacements[f"car_{number}_loading_point"] == ""
        assert replacements[f"car_{number}_unloading_point"] == ""


# ─────────────────────────────────────────────────────────────
# Точки погрузки и выгрузки
# ─────────────────────────────────────────────────────────────

def test_empty_points_removed_in_generated_document(generator, work_dir):
    """В готовом документе остаются только фактически заполненные точки."""
    output_dir = work_dir / "arenda_points"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _generate(
        generator, _payload(cars=1, loadings=2, unloadings=3), output_dir
    )
    doc = Document(path)
    texts = _body_texts(doc)

    assert len(_point_lines(doc, LOADING_LINE_RE)) == 2
    assert len(_point_lines(doc, UNLOADING_LINE_RE)) == 3
    assert not [t for t in texts if t.startswith("3.2.3.")]
    assert not [t for t in texts if t.startswith("3.3.4.")]

    # Заголовки разделов остаются: точки в них есть.
    assert "3.2. Согласованные точки погрузки:" in texts
    assert "3.3. Согласованные точки выгрузки:" in texts
    # Маршрут и экипаж — после точек и на месте.
    assert "3.4. Согласованный маршрут: Москва — Калуга — Чехов." in texts
    assert "3.5. Член экипажа Арендодателя (водитель):" in texts


def test_points_headers_removed_when_no_points(generator, work_dir):
    """Точек нет — нет и пустых разделов 3.2 / 3.3."""
    output_dir = work_dir / "arenda_points_none"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _generate(generator, _payload(loadings=0, unloadings=0), output_dir)
    doc = Document(path)
    texts = _body_texts(doc)

    assert not [t for t in texts if t.startswith("3.2.")]
    assert not [t for t in texts if t.startswith("3.3.")]
    assert "3.4. Согласованный маршрут: Москва — Калуга — Чехов." in texts


def test_one_point_of_kind_does_not_remove_other_header(generator, work_dir):
    """Пустой раздел выгрузки не трогает раздел погрузки и наоборот."""
    output_dir = work_dir / "arenda_points_half"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _generate(generator, _payload(loadings=1, unloadings=0), output_dir)
    doc = Document(path)
    texts = _body_texts(doc)

    assert "3.2. Согласованные точки погрузки:" in texts
    assert len(_point_lines(doc, LOADING_LINE_RE)) == 1
    assert not [t for t in texts if t.startswith("3.3.")]


def test_points_beyond_ten_are_truncated(generator, caplog):
    with caplog.at_level(logging.WARNING, logger="core.contract_generator"):
        replacements = generator._build_replacements_map(
            _payload(loadings=12, unloadings=12)
        )

    assert replacements["loading_10_address"] == "Адрес погрузки 10"
    assert replacements["unloading_10_address"] == "Адрес выгрузки 10"
    assert "loading_11_address" not in replacements
    assert "unloading_11_address" not in replacements
    assert "в бланк помещается 10" in caplog.text


def test_missing_points_give_empty_placeholders(generator):
    replacements = generator._build_replacements_map(
        _payload(loadings=1, unloadings=1)
    )

    assert replacements["loading_1_address"] == "Адрес погрузки 1"
    assert replacements["unloading_1_address"] == "Адрес выгрузки 1"
    for number in range(2, 11):
        assert replacements[f"loading_{number}_address"] == ""
        assert replacements[f"loading_{number}_date"] == ""
        assert replacements[f"loading_{number}_time_from"] == ""
        assert replacements[f"loading_{number}_time_to"] == ""
        assert replacements[f"unloading_{number}_address"] == ""
        assert replacements[f"unloading_{number}_date"] == ""


def test_point_time_falls_back_to_time_window(generator):
    """Время подачи ТС: поля time_from/time_to, иначе разбор time_window."""
    payload = _payload(loadings=1)
    payload["loadings"] = [{
        "address": "Адрес погрузки 1",
        "date": "2026-09-21",
        "time_window": "с 09:00 до 15:00",
    }]

    replacements = generator._build_replacements_map(payload)

    assert replacements["loading_1_time_from"] == "09:00"
    assert replacements["loading_1_time_to"] == "15:00"


def test_point_time_survives_generate_path(generator, work_dir):
    """
    time_from / time_to доходят до бланка через generate().

    ContractData хранит точку как {address, date, time_window} и эти поля
    распознавания отбрасывает — генератор читает их из исходных данных
    (см. _resolve_points).
    """
    output_dir = work_dir / "arenda_points_time"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _generate(generator, _payload(loadings=1), output_dir)
    texts = _body_texts(Document(path))

    assert ("3.2.1. Точка погрузки № 1 — Адрес погрузки 1. Плановая дата и "
            "время подачи ТС: 21.09.2026 г., с 08:00 до 18:00.") in texts


# ─────────────────────────────────────────────────────────────
# Приложение № 1 (Акт приема-передачи и возврата ТС)
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("variant", VARIANTS)
def test_appendix_is_filled(generator, work_dir, variant):
    """Акт заполнен теми же значениями, что и основной договор."""
    output_dir = work_dir / f"arenda_act_{variant.replace(' ', '_')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = Document(_generate(generator, _payload(variant), output_dir))
    text = _document_text(doc)
    texts = _body_texts(doc)

    assert any(t.startswith("Настоящий Акт составлен во исполнение Договора")
               for t in texts)
    assert "с экипажем № АБ-123 от 19.09.2026 г." in text
    assert "1. ПЕРЕДАЧА ТС В АРЕНДУ" in text
    assert "2. ВОЗВРАТ ТС" in text

    transfer = _act_values(doc, "Место передачи")
    assert transfer["Тягач"] == "Тягач-Модель, гос. номер А001АА01"
    assert transfer["Прицеп/полуприцеп"] == "Прицеп-Модель, гос. номер Б002ББ02"
    assert transfer["Экипаж"] == "Иванов Иван Иванович"
    # Поля Акта, которых нет в данных, остаются пустыми; перечень документов
    # печатает генератор своей константой (шаг FIX-3).
    assert transfer["Место передачи"] == ""
    assert transfer["Фактические дата и время передачи"] == ""
    assert transfer["Переданные документы"] == TRANSFER_DOCUMENTS

    returning = _act_values(doc, "Место возврата")
    assert set(returning.values()) == {""}


@pytest.mark.parametrize("variant", VARIANTS)
def test_appendix_signatures(generator, work_dir, variant):
    """Подписи Акта: краткие наименования сторон и фамилии с инициалами."""
    output_dir = work_dir / f"arenda_act_sign_{variant.replace(' ', '_')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = Document(_generate(generator, _payload(variant), output_dir))

    table = _appendix_signature_table(doc)
    assert table is not None, "нет таблицы подписей Акта"

    lessee, lessor = table.rows[0].cells
    lessee_lines = lessee.text.split("\n")
    lessor_lines = lessor.text.split("\n")

    assert lessee_lines == [
        "АРЕНДАТОР:",
        _lessee(variant)["short_name"],
        _lessee_signature(variant),
        "М.П.",
    ]
    assert lessor_lines == [
        "АРЕНДОДАТЕЛЬ:",
        "ООО «АДТ»",
        "______________/ С.С. Сидоров /",
        "М.П.",
    ]


# ─────────────────────────────────────────────────────────────
# Стык «распознавание → генератор»
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "entity_type, vat_rate, filename",
    [
        ("ООО", "22%", "shablon_arenda_ts_ooo.docx"),
        ("ИП", "22%", "shablon_arenda_ts_ip_with_vat.docx"),
        ("ИП", "0%", "shablon_arenda_ts_ip_without_vat.docx"),
        ("", "22%", "shablon_arenda_ts_ooo.docx"),
    ],
)
def test_carrier_type_derived_from_lessee(
    generator, entity_type, vat_rate, filename
):
    """
    Без contract["carrier_type"] вид Арендатора выводится из распознавания.

    Промпт не отдаёт поле carrier_type: тип стороны лежит в
    lessee["entity_type"], а «без НДС» виден по ставке «0%».
    """
    payload = _payload(carrier_type=None)
    payload["lessee"]["entity_type"] = entity_type
    payload["contract"]["vat_rate"] = vat_rate

    assert generator._carrier_type_of(_coerced(payload)) == (
        "ООО" if entity_type != "ИП"
        else ("ИП без НДС" if vat_rate == "0%" else "ИП с НДС")
    )
    assert generator.get_template_path(_coerced(payload)).endswith(filename)


def test_explicit_carrier_type_wins_over_entity_type(generator):
    """Явное поле contract["carrier_type"] важнее вывода из lessee."""
    payload = _payload("ООО", carrier_type="ИП без НДС")
    payload["contract"]["vat_rate"] = "0%"

    assert generator._carrier_type_of(_coerced(payload)) == "ИП без НДС"
    assert generator.get_template_path(_coerced(payload)).endswith(
        TEMPLATE_NAMES["ИП без НДС"]
    )


def test_root_blocks_reach_the_document_through_generate(generator, work_dir):
    """lessee / lessor / route / срок аренды из корня ответа доходят до бланка."""
    output_dir = work_dir / "arenda_root_blocks"
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = _payload(carrier_type=None)

    text = _document_text(Document(_generate(generator, payload, output_dir)))

    assert "ООО «Арендатор Тест»" in text
    assert "ООО «Арендодатель Тест»" in text
    assert "3.4. Согласованный маршрут: Москва — Калуга — Чехов." in text
    assert "с 20.09.2026 г. по 28.09.2026 г. включительно" in text
    assert "21.09.2026" in text


def test_blocks_nested_in_contract_work_too(generator, work_dir):
    """Блоки сторон внутри contract (путь интерфейса) работают так же."""
    payload = _payload()
    nested = {
        key: payload.pop(key)
        for key in ("lessee", "lessor", "route", "lease_start_date",
                    "lease_end_date")
    }
    payload["contract"].update(nested)

    output_dir = work_dir / "arenda_nested_blocks"
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = Document(_generate(generator, payload, output_dir))
    text = _document_text(doc)

    assert "ООО «Арендатор Тест»" in text
    assert "3.4. Согласованный маршрут: Москва — Калуга — Чехов." in text

    replacements = generator._build_replacements_map(payload)
    assert replacements["lessee_full_name"] == "ООО «Арендатор Тест»"


def test_hoist_does_not_overwrite_contract_values(generator):
    """Уже заполненный contract важнее корневого блока распознавания."""
    payload = _payload()
    payload["contract"]["lessee"] = dict(payload["lessee"])
    payload["contract"]["lessee"]["full_name"] = "ООО «Из Интерфейса»"
    payload["lessee"]["full_name"] = "ООО «Из Распознавания»"

    hoisted = ArendaTsGenerator._hoist_contract_fields(payload)

    assert hoisted["contract"]["lessee"]["full_name"] == "ООО «Из Интерфейса»"
    assert hoisted["lessee"]["full_name"] == "ООО «Из Распознавания»"
    # Исходный словарь не меняется: генератор работает с копией.
    assert "route" not in payload["contract"]


def test_driver_fields_from_registry_names(generator):
    """Справочник водителя хранит паспорт и удостоверение отдельными полями."""
    payload = _payload()
    payload["driver"] = {
        "full_name": "Иванов Иван Иванович",
        "birth_date": "1980-01-01",
        "passport_series": "18 22",
        "passport_number": "926830",
        "passport_issuer": "Отделом УФМС России по г. Москве",
        "passport_issue_date": "2023-01-30",
        "license_series": "99 36",
        "license_number": "123456",
        "license_issue_date": "2020-01-01",
        "registration_address": "г. Москва, ул. Водительская, д. 3",
        "phone": "+7 (999) 123-45-67",
    }

    replacements = generator._build_replacements_map(payload)

    assert replacements["driver_passport"] == "18 22 926830"
    assert replacements["driver_license"] == "99 36 123456"
    assert replacements["driver_address"] == "г. Москва, ул. Водительская, д. 3"


def test_short_fio_uses_surname_and_initials(generator):
    """Подписи — как в образце: «П.П. Петров» (фамилия последней)."""
    replacements = generator._build_replacements_map(_payload())

    assert replacements["lessee_director_short"] == "П.П. Петров"
    assert replacements["lessor_director_short"] == "С.С. Сидоров"
    assert replacements["lessee_director_name"] == "Петров Пётр Петрович"


def test_edo_placeholders_stay_empty(generator):
    """
    В данных теста ЭДО нет — плейсхолдеры остаются пустыми строками.

    Промпт ЭДО извлекает (поле edo блоков lessee / lessor), но в этих данных
    такого поля нет: генератор печатает пустую строку и ничего не выдумывает.
    """
    replacements = generator._build_replacements_map(_payload())

    assert replacements["lessee_edo"] == ""
    assert replacements["lessor_edo"] == ""


def test_multiline_values_are_flattened(generator):
    """Переносы строк из справочников в бланк не попадают."""
    payload = _payload()
    payload["lessee"]["legal_address"] = "г. Москва,\nул. Тестовая,  д. 1"
    payload["lessor"]["director_name"] = "Сидоров  Сидор\nСидорович"

    replacements = generator._build_replacements_map(payload)

    assert replacements["lessee_address"] == "г. Москва, ул. Тестовая, д. 1"
    assert replacements["lessor_director_name"] == "Сидоров Сидор Сидорович"


# ─────────────────────────────────────────────────────────────
# Подписант: род и падеж (шаг FIX-3)
# ─────────────────────────────────────────────────────────────

#: Директор-женщина: отчество на «-овна» — причастие обязано стать «действующей».
FEMALE_DIRECTOR = "Васильева Елизавета Юрьевна"

#: Директор-мужчина — контрольная пара к FEMALE_DIRECTOR.
MALE_DIRECTOR = "Иванов Иван Иванович"


def _payload_with_female_director() -> dict:
    """Данные, где ОБЕ стороны подписывает женщина."""
    payload = _payload()
    payload["lessee"] = dict(payload["lessee"], director_name=FEMALE_DIRECTOR)
    payload["lessor"] = dict(payload["lessor"], director_name=FEMALE_DIRECTOR)
    return payload


@pytest.mark.parametrize("full_name, expected", [
    (MALE_DIRECTOR, "действующего"),
    (FEMALE_DIRECTOR, "действующей"),
    ("Кузнецова Анна Ильинична", "действующей"),
    ("Петрова Мария Игоревна", "действующей"),
    ("Иванова Мария Ивановна", "действующей"),
    # Отчества нет — пол не угадывается, форма по умолчанию (мужская).
    ("Иванов Иван", "действующего"),
    ("", "действующего"),
    (None, "действующего"),
])
def test_acting_word_follows_gender(generator, full_name, expected):
    """«действующего» / «действующей» — по отчеству ФИО."""
    assert generator._acting_rod(full_name) == expected


@pytest.mark.parametrize("full_name, gender", [
    (MALE_DIRECTOR, "male"),
    (FEMALE_DIRECTOR, "female"),
    ("Кузнецова Анна Ильинична", "female"),
    ("Иванов Иван", "male"),
    ("", "male"),
])
def test_gender_is_read_from_patronymic(generator, full_name, gender):
    assert generator._gender_from_name(full_name) == gender


@pytest.mark.parametrize("position, expected", [
    ("Генеральный директор", "Генерального директора"),
    ("Директор", "Директора"),
    ("ИП", "ИП"),
    ("Индивидуальный предприниматель", "Индивидуального предпринимателя"),
    ("Главный бухгалтер", "Главного бухгалтера"),
    ("Исполнительный директор", "Исполнительного директора"),
    # Уже в родительном падеже — второй раз не склоняем.
    ("Генерального директора", "Генерального директора"),
    # Пустое значение даёт пустую строку: падеж не выдумывается.
    ("", ""),
    (None, ""),
    ("   ", ""),
])
def test_genitive_position(generator, position, expected):
    """Должность в родительном падеже — как её печатает п. 1.1 бланка."""
    assert generator._genitive_position(position) == expected


def test_short_fio_uses_surname_and_initials_female(generator):
    """Короткое ФИО работает и для директора-женщины."""
    payload = _payload_with_female_director()
    replacements = generator._build_replacements_map(payload)

    assert replacements["lessee_director_short"] == "Е.Ю. Васильева"
    assert replacements["lessor_director_short"] == "Е.Ю. Васильева"


@pytest.mark.parametrize("variant", VARIANTS)
def test_full_pipeline_female_director(generator, work_dir, variant):
    """В готовом договоре с директором-женщиной стоит «действующей»."""
    output_dir = work_dir / f"arenda_female_{variant.replace(' ', '_')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    text = _document_text(Document(_generate(
        generator, _payload_with_female_director(), output_dir
    )))

    assert f"в лице Генерального директора {FEMALE_DIRECTOR}, " \
           f"действующей на основании Устава." in text
    assert f"в лице Директора {FEMALE_DIRECTOR}, " \
           f"действующей на основании Устава," in text
    assert "действующего на основании" not in text


@pytest.mark.parametrize("variant", VARIANTS)
def test_full_pipeline_male_director(generator, work_dir, variant):
    """В готовом договоре с директором-мужчиной стоит «действующего»."""
    output_dir = work_dir / f"arenda_male_{variant.replace(' ', '_')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    text = _document_text(Document(_generate(generator, _payload(variant), output_dir)))

    is_ip = variant != "ООО"
    ogrn_label = "ОГРНИП" if is_ip else "ОГРН"
    # У ИП подписант — сам предприниматель: должность в родительном падеже
    # «Индивидуального предпринимателя», а не «Генерального директора».
    lessee_clause = (
        f"в лице Индивидуального предпринимателя {_lessee(variant)['director_name']}, "
        f"действующего на основании свидетельства о государственной регистрации."
        if is_ip else
        "в лице Генерального директора Петров Пётр Петрович, "
        "действующего на основании Устава."
    )
    assert lessee_clause in text
    assert "в лице Директора Сидоров Сидор Сидорович, " \
           "действующего на основании Устава," in text
    assert "действующей на основании" not in text
    assert f"{ogrn_label} {_lessee(variant)['ogrn']}" in text


def test_generated_document_has_no_hardcoded_acting_word(generator, work_dir):
    """Причастие в п. 1.1 / 1.2 всегда идёт из карты замен, а не из бланка."""
    replacements = generator._build_replacements_map(_payload())

    assert replacements["lessee_director_acting_rod"] == "действующего"
    assert replacements["lessor_director_acting_rod"] == "действующего"
    assert replacements["lessee_director_position_rod"] == \
        "Генерального директора"
    assert replacements["lessor_director_position_rod"] == "Директора"


def test_ip_position_stays_unchanged(generator):
    """ИП — сокращение: падеж его не меняет, причастие — мужское."""
    replacements = generator._build_replacements_map(_payload("ИП с НДС"))

    assert replacements["lessee_director_position"] == \
        "Индивидуальный предприниматель"
    assert replacements["lessee_director_position_rod"] == \
        "Индивидуального предпринимателя"
    assert replacements["lessee_director_acting_rod"] == "действующего"


def test_missing_director_gives_empty_placeholders(generator):
    """Нет ФИО — пустые строки, а не выдуманное «действующего» рядом с пустотой."""
    payload = _payload()
    payload["lessee"] = dict(payload["lessee"], director_name="",
                             director_position="")

    replacements = generator._build_replacements_map(payload)

    assert replacements["lessee_director_name"] == ""
    assert replacements["lessee_director_position_rod"] == ""
    assert replacements["lessee_director_short"] == ""
    assert replacements["lessee_director_acting_rod"] == "действующего"


# ─────────────────────────────────────────────────────────────
# Раздел 9 и Акт: поля шага FIX-3
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("variant", VARIANTS)
def test_requisites_block_filled(generator, work_dir, variant):
    """Блок 9 заполнен: краткое наименование, фактический адрес и реквизиты."""
    output_dir = work_dir / f"arenda_req_fix3_{variant.replace(' ', '_')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = Document(_generate(generator, _payload(variant), output_dir))

    table = _requisites_table(doc)
    assert table is not None, "не найдена таблица реквизитов"
    lessee = table.rows[0].cells[0].text
    lessor = table.rows[0].cells[1].text

    lessee_data = _lessee(variant)
    lessor_data = _lessor()

    for block, data in ((lessee, lessee_data), (lessor, lessor_data)):
        assert data["short_name"] in block, f"нет краткого наименования: {block}"
        assert f"Юридический адрес: {data['legal_address']}" in block
        assert f"Фактический адрес: {data['actual_address']}" in block
        assert f"ИНН {data['inn']}" in block
        assert f"БИК {data['bik']}" in block
        assert f"к/с {data['corr_account']}" in block
        assert f"E-mail: {data['email']}" in block

    # Метка госрегистрации зависит от вида Арендатора: ОГРН у ООО, ОГРНИП у ИП.
    if variant == "ООО":
        assert f"ОГРН {lessee_data['ogrn']}" in lessee
    else:
        assert f"ОГРНИП {lessee_data['ogrn']}" in lessee
    assert "ОГРН 1027700132196" in lessor


def test_actual_address_missing_gives_empty_string(generator, work_dir):
    """Нет фактического адреса — в блоке 9 пустое место, а не чужой адрес."""
    payload = _payload()
    payload["lessee"] = dict(payload["lessee"], actual_address="")

    replacements = generator._build_replacements_map(payload)

    assert replacements["lessee_actual_address"] == ""
    assert replacements["lessor_actual_address"] == _lessor()["actual_address"]


def test_act_fields_are_read_from_contract(generator, work_dir):
    """Десять полей Акта приходят из contract и попадают в свой документ."""
    payload = _payload(contract_extra={
        "transfer_place": "г. Москва, ул. Передающая, д. 1",
        "transfer_datetime": "21.09.2026 08:30",
        "transfer_mileage": "125 400 км",
        "transfer_condition": "Без замечаний",
        "transfer_documents": "СТС; ОСАГО; иные: доверенность № 5",
        "return_place": "г. Калуга, ул. Возвратная, д. 2",
        "return_datetime": "27.09.2026 19:00",
        "return_mileage": "128 130 км",
        "return_condition": "Царапина на левом борту",
        "return_notes": "Акт подписан без разногласий",
    })
    doc = Document(_generate(generator, payload, work_dir))

    transfer = _act_values(doc, "Место передачи")
    assert transfer == {
        "Место передачи": "г. Москва, ул. Передающая, д. 1",
        "Фактические дата и время передачи": "21.09.2026 08:30",
        "Тягач": "Тягач-Модель, гос. номер А001АА01",
        "Прицеп/полуприцеп": "Прицеп-Модель, гос. номер Б002ББ02",
        "Пробег на момент передачи": "125 400 км",
        "Внешнее состояние / замечания": "Без замечаний",
        "Переданные документы": "СТС; ОСАГО; иные: доверенность № 5",
        "Экипаж": "Иванов Иван Иванович",
    }

    returning = _act_values(doc, "Место возврата")
    assert returning == {
        "Место возврата": "г. Калуга, ул. Возвратная, д. 2",
        "Фактические дата и время возврата": "27.09.2026 19:00",
        "Пробег на момент возврата": "128 130 км",
        "Состояние ТС / замечания": "Царапина на левом борту",
        "Иные отметки": "Акт подписан без разногласий",
    }


def test_act_transfer_documents_default(generator, work_dir):
    """Без своего значения перечень документов печатает генератор."""
    doc = Document(_generate(generator, _payload(), work_dir))

    transfer = _act_values(doc, "Место передачи")
    assert transfer["Переданные документы"] == TRANSFER_DOCUMENTS
    assert TRANSFER_DOCUMENTS == "СТС на тягач и прицеп/полуприцеп; ОСАГО; иные:"


def test_act_empty_fields_give_empty_placeholders(generator):
    """Пустой contract — все десять полей Акта пустые, кроме документов."""
    replacements = generator._build_replacements_map({"contract": {}})

    for key in ("transfer_place", "transfer_datetime", "transfer_mileage",
                "transfer_condition", "return_place", "return_datetime",
                "return_mileage", "return_condition", "return_notes"):
        assert replacements[key] == "", key

    assert replacements["transfer_documents"] == TRANSFER_DOCUMENTS


def test_act_log_reports_filled_fields(generator, caplog):
    """В лог уходит только счётчик заполненных полей Акта — без самих данных."""
    payload = _payload(contract_extra={
        "transfer_place": "г. Москва, ул. Передающая, д. 1",
        "transfer_datetime": "21.09.2026 08:30",
    })

    with caplog.at_level(logging.INFO, logger="core.contract_generator"):
        generator._build_replacements_map(payload)

    messages = "\n".join(
        r.getMessage() for r in caplog.records
        if r.name == "core.contract_generator"
    )
    assert "полей Акта заполнено — 2 из 9" in messages
    assert "Передающая" not in messages


# ─────────────────────────────────────────────────────────────
# Логи
# ─────────────────────────────────────────────────────────────

def test_generation_logs_to_contract_generator(caplog, generator, work_dir):
    with caplog.at_level(logging.INFO, logger="core.contract_generator"):
        _generate(generator, _payload(cars=4, loadings=2, unloadings=3), work_dir)

    records = [r for r in caplog.records if r.name == "core.contract_generator"]
    assert records, "генерация ничего не записала в core.contract_generator"

    messages = "\n".join(r.getMessage() for r in records)
    assert "машин в таблице 3.1 — 4" in messages
    assert "удалено пустых строк таблицы машин: 8" in messages
    assert "удалено пустых точек погрузки: 8" in messages
    assert "удалено пустых точек выгрузки: 7" in messages
    assert "удалено пустых точек маршрута: 15" in messages
    assert "Договор аренды ТС с экипажем [с НДС]: в бланк подставлено" in messages


def test_logs_have_no_personal_data(caplog, generator, work_dir):
    """В логи не попадают ФИО, паспорт, адреса, VIN, госномера и стороны."""
    with caplog.at_level(logging.DEBUG, logger="core.contract_generator"):
        _generate(generator, _payload(cars=4, loadings=2, unloadings=2), work_dir)

    messages = "\n".join(
        r.getMessage() for r in caplog.records
        if r.name == "core.contract_generator"
    )
    for fragment in ("Иванов", "Петров", "Сидоров", "Смирнов",
                     "18 22 926830", "99 36 123456",
                     "TESTVIN00000000001", "МОДЕЛЬ 1",
                     "А001АА01", "Б002ББ02",
                     "Адрес погрузки 1", "Адрес выгрузки 1",
                     "Точка погрузки 1", "Москва",
                     "Арендатор Тест", "Арендодатель Тест"):
        assert fragment not in messages, f"в логе есть «{fragment}»"
