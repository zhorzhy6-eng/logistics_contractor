# -*- coding: utf-8 -*-
"""
Тесты шагов постобработки (Шаг 5 рефакторинга архитектуры контрактов).

Каждый шаг проверяется отдельно на программно собранном Document() —
без тяжёлых шаблонов templates/*.docx.
"""

import gc

import pytest
from docx import Document

from core.contract_data import ContractData
from core.contracts.base_generator import (
    BaseContractGenerator,
    ConvertNewlinesStep,
    ExpandVehicleRowsStep,
)
from core.contracts.perevozka.generator import PerevozkaGenerator
from core.contracts.perevozka.postprocess import (
    RemoveEmptyVehicleRowsStep,
    RouteTablesStep,
)

NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@pytest.fixture(autouse=True)
def _release_documents():
    yield
    gc.collect()


@pytest.fixture
def generator(tmp_path) -> PerevozkaGenerator:
    """Генератор без реальных шаблонов: шаги их не используют."""
    return PerevozkaGenerator(templates_dir=str(tmp_path))


def _save_doc(doc, path):
    doc.save(str(path))
    return Document(str(path))


# ─────────────────────────────────────────────────────────────
# ConvertNewlinesStep
# ─────────────────────────────────────────────────────────────

def test_convert_newlines_step_turns_newline_into_break(tmp_path):
    gen = PerevozkaGenerator(templates_dir=str(tmp_path))
    doc = Document()
    paragraph = doc.add_paragraph("первая строка\nвторая строка")

    ConvertNewlinesStep(gen).apply(doc, None)

    assert "вторая строка" in paragraph.text
    assert paragraph._p.findall(f".//{NS}br"), "ожидался <w:br/>"


def test_convert_newlines_step_keeps_plain_text(tmp_path):
    gen = PerevozkaGenerator(templates_dir=str(tmp_path))
    doc = Document()
    paragraph = doc.add_paragraph("без переносов")

    ConvertNewlinesStep(gen).apply(doc, None)

    assert not paragraph._p.findall(f".//{NS}br")


# ─────────────────────────────────────────────────────────────
# RouteTablesStep
# ─────────────────────────────────────────────────────────────

def _route_doc_with_marker():
    """Документ с абзацем-меткой LOADING_TABLE_HERE (как после docxtpl)."""
    doc = Document()
    doc.add_paragraph("3.2. Погрузка:")
    doc.add_paragraph("LOADING_TABLE_HERE")
    doc.add_paragraph("3.3. Выгрузка:")
    return doc


def test_route_tables_step_replaces_marker_with_heading_and_table(
    generator, tmp_path
):
    doc = _route_doc_with_marker()
    data = ContractData(
        vehicles=[
            {"brand_model": "JETOUR T2", "vin": "EC3TEUMB0T0002608",
             "vehicle_type": "Легковой автомобиль",
             "loading_index": 1, "unloading_index": 0},
        ],
        loadings=[{"address": "г. Мурманск, ул. Тестовая, д. 1",
                   "date": "2026-09-24", "time_window": "09:00"}],
    )

    RouteTablesStep(generator).apply(doc, data)

    paragraphs = [p.text for p in doc.paragraphs]
    assert "Погрузка 1: г. Мурманск, ул. Тестовая, д. 1" in paragraphs
    assert "LOADING_TABLE_HERE" not in paragraphs

    route_table = doc.tables[0]
    headers = [cell.text for cell in route_table.rows[0].cells]
    assert headers == ["№", "Марка/Модель", "VIN-номер"]
    data_row = [cell.text for cell in route_table.rows[1].cells]
    assert data_row == ["1", "JETOUR T2", "EC3TEUMB0T0002608"]


def test_route_tables_step_without_marker_is_noop(generator):
    doc = Document()
    doc.add_paragraph("обычный текст без меток")

    RouteTablesStep(generator).apply(doc, ContractData())

    assert not doc.tables
    assert [p.text for p in doc.paragraphs] == ["обычный текст без меток"]


# ─────────────────────────────────────────────────────────────
# RemoveEmptyVehicleRowsStep
# ─────────────────────────────────────────────────────────────

def _vehicle_table_doc():
    doc = Document()
    table = doc.add_table(rows=3, cols=3)
    for cell, text in zip(table.rows[0].cells, ("№", "Марка/Модель", "VIN-номер")):
        cell.text = text
    for cell, text in zip(table.rows[1].cells, ("1", "JETOUR T2", "EC3TEUMB0T0002608")):
        cell.text = text
    # третья строка — пустая (как незаполненная строка шаблона)
    return doc


def test_remove_empty_vehicle_rows_step_removes_empty_row(generator, tmp_path):
    doc = _vehicle_table_doc()
    assert len(doc.tables[0].rows) == 3

    RemoveEmptyVehicleRowsStep(generator).apply(doc, None)

    table = doc.tables[0]
    assert len(table.rows) == 2
    assert [cell.text for cell in table.rows[1].cells] == [
        "1", "JETOUR T2", "EC3TEUMB0T0002608",
    ]


def test_remove_empty_vehicle_rows_step_ignores_foreign_tables(generator, tmp_path):
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "что-то другое"
    table.rows[1].cells[0].text = "данные"

    RemoveEmptyVehicleRowsStep(generator).apply(doc, None)

    assert len(doc.tables[0].rows) == 2  # чужая таблица не тронута


# ─────────────────────────────────────────────────────────────
# ExpandVehicleRowsStep: таблица машин под фактическое число машин
# ─────────────────────────────────────────────────────────────

def _cars(count: int) -> list:
    """Синтетические машины: марка CAR N, VIN из 17 символов."""
    return [
        {"brand_model": f"CAR {n}", "vin": f"EC3TEUMB0T{n:07d}",
         "vehicle_type": "Легковой автомобиль",
         "loading_index": 0, "unloading_index": 0}
        for n in range(1, count + 1)
    ]


def _template_vehicle_doc(rows: int = 12):
    """
    Таблица машин бланка после docxtpl: шапка и rows заполненных строк.

    Именно в таком виде шаг видит документ: плейсхолдеры уже заменены
    значениями, пустые строки ещё не удалены.
    """
    doc = Document()
    table = doc.add_table(rows=rows + 1, cols=3)
    for cell, text in zip(table.rows[0].cells, ("№", "Марка/Модель", "VIN-номер")):
        cell.text = text
    for index in range(1, rows + 1):
        values = (str(index), f"CAR {index}", f"EC3TEUMB0T{index:07d}")
        for cell, text in zip(table.rows[index].cells, values):
            cell.text = text
    return doc


def _row_texts(table) -> list:
    return [[cell.text for cell in row.cells] for row in table.rows]


def test_expand_vehicle_rows_adds_rows_for_nineteen_cars(generator):
    """19 машин: к 12 строкам бланка дописываются 13…19, номер — в колонке «№»."""
    doc = _template_vehicle_doc()

    added = generator._expand_vehicle_rows(doc, ContractData(vehicles=_cars(19)))

    table = doc.tables[0]
    assert added == 7
    assert len(table.rows) == 20
    assert [row.cells[0].text for row in table.rows[1:]] == [
        str(number) for number in range(1, 20)
    ]
    assert [row.cells[2].text for row in table.rows[1:]] == [
        f"EC3TEUMB0T{number:07d}" for number in range(1, 20)
    ]


def test_expanded_row_replaces_text_instead_of_appending(generator):
    """
    В клоне строки текст ЗАМЕНЯЕТСЯ, а не дописывается.

    `Formatting.fill_table_cell` добавляет run к существующим: на строке
    шаблона это дало бы «12 13» в колонке «№».
    """
    doc = _template_vehicle_doc()

    generator._expand_vehicle_rows(doc, ContractData(vehicles=_cars(13)))

    assert [cell.text for cell in doc.tables[0].rows[13].cells] == [
        "13", "CAR 13", "EC3TEUMB0T0000013",
    ]


def test_expanded_table_repeats_header_and_keeps_rows_whole(generator):
    """Шапка повторяется на новой странице, строки между страницами не рвутся."""
    doc = _template_vehicle_doc()

    generator._expand_vehicle_rows(doc, ContractData(vehicles=_cars(19)))

    table = doc.tables[0]
    header_props = table.rows[0]._tr.trPr
    assert header_props.find(f"{NS}tblHeader") is not None
    assert header_props.find(f"{NS}cantSplit") is not None

    for row in table.rows[1:]:
        assert row._tr.trPr.find(f"{NS}cantSplit") is not None
        assert row._tr.trPr.find(f"{NS}tblHeader") is None


@pytest.mark.parametrize("count", [1, 2, 3, 12])
def test_twelve_or_fewer_cars_leave_table_untouched(generator, count):
    """Договоры на 1–12 машин: ни одной новой строки, содержимое прежнее."""
    doc = _template_vehicle_doc()
    before = _row_texts(doc.tables[0])

    added = generator._expand_vehicle_rows(doc, ContractData(vehicles=_cars(count)))

    assert added == 0
    assert _row_texts(doc.tables[0]) == before


def test_expand_without_data_is_noop(generator):
    """Постобработка без данных (старый путь) таблицу не трогает."""
    doc = _template_vehicle_doc()

    assert generator._expand_vehicle_rows(doc, None) == 0
    assert len(doc.tables[0].rows) == 13


def test_expand_without_vehicle_table_is_noop(generator):
    """В документе нет таблицы машин — шаг молча ничего не делает."""
    doc = Document()
    doc.add_paragraph("документ без таблиц")

    assert generator._expand_vehicle_rows(doc, ContractData(vehicles=_cars(19))) == 0


def test_truck_is_not_counted_as_cargo(generator):
    """
    Тягач в таблицу 3.1 не попадает, и нумерация машин сквозная.

    Иначе строка тягача съела бы номер, и машина №13 получила бы
    нумерацию соседней строки шаблона.
    """
    doc = _template_vehicle_doc()
    vehicles = [{"brand_model": "FOTON", "vin": "", "vehicle_type": "Тягач"}]
    vehicles += _cars(13)

    added = generator._expand_vehicle_rows(doc, ContractData(vehicles=vehicles))

    assert added == 1
    assert [cell.text for cell in doc.tables[0].rows[13].cells] == [
        "13", "CAR 13", "EC3TEUMB0T0000013",
    ]


def test_expand_step_is_thin_wrapper(generator):
    """Шаг — обёртка над методом генератора (стиль остальных шагов)."""
    doc = _template_vehicle_doc()

    ExpandVehicleRowsStep(generator).apply(doc, ContractData(vehicles=_cars(15)))

    assert len(doc.tables[0].rows) == 16


# ─────────────────────────────────────────────────────────────
# Состав конвейера
# ─────────────────────────────────────────────────────────────

def test_perevozka_steps_with_data(generator):
    steps = generator.postprocess_steps(ContractData())
    assert [step.name for step in steps] == [
        "convert_newlines", "route_tables", "expand_vehicle_rows",
        "remove_empty_vehicle_rows", "normalize_spaces",
    ]


def test_perevozka_steps_without_data_keeps_old_behaviour(generator):
    """Без данных таблицы маршрута не строятся — как в старом коде."""
    steps = generator.postprocess_steps(None)
    assert [step.name for step in steps] == [
        "convert_newlines", "remove_empty_vehicle_rows", "normalize_spaces",
    ]


def test_base_generator_default_steps(tmp_path):
    base = BaseContractGenerator(templates_dir=str(tmp_path))
    steps = base.postprocess_steps(ContractData())
    assert [step.name for step in steps] == ["convert_newlines"]
