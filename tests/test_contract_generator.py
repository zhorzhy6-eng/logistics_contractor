#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты генератора договоров (core/contract_generator.py).

Главный тест — сверка плейсхолдеров шаблонов с картой замен: именно он
поймал баг, из-за которого в договор не попадали тягач и полуприцеп.
"""

import re
import zipfile
from pathlib import Path

import pytest
from docx import Document

from core.contract_data import ContractData
from core.contract_generator import ContractGenerator

TEMPLATES = (
    "shablon_ooo.docx",
    "shablon_ip_with_vat.docx",
    "shablon_ip_without_vat.docx",
)


def template_placeholders(path) -> set:
    """Все имена {{переменных}} из шаблона (по всем XML-частям)."""
    names = set()
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith(".xml"):
                continue
            xml = archive.read(name).decode("utf-8", "ignore")
            text = re.sub(r"<[^>]+>", "", xml)
            for match in re.finditer(r"\{\{(.*?)\}\}", text, re.S):
                names.add(re.sub(r"\s+", "", match.group(1)))
    return names


def document_text(path) -> str:
    """Весь текст документа, включая таблицы."""
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


def document_xml(path) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read("word/document.xml").decode("utf-8", "ignore")


def placeholders_left(path) -> list:
    text = re.sub(r"<[^>]+>", "", document_xml(path))
    return sorted(set(re.findall(r"\{\{[^{}]*\}\}", text)))


@pytest.fixture
def generator(templates_dir) -> ContractGenerator:
    return ContractGenerator(templates_dir=str(templates_dir))


# ─────────────────────────────────────────────────────────────
# Главный тест: покрытие плейсхолдеров шаблонов
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("template_name", TEMPLATES)
def test_all_template_placeholders_are_covered(generator, templates_dir, template_name):
    """
    Каждый плейсхолдер шаблона должен присутствовать в карте замен.

    Если в шаблон добавят переменную, а в генератор — нет, тест упадёт:
    именно так был найден баг с tractor_brand/trailer_brand.
    """
    placeholders = template_placeholders(templates_dir / template_name)
    assert placeholders, f"в шаблоне {template_name} не найдено плейсхолдеров"

    replacements = generator._build_replacements_map(ContractData())
    missing = sorted(placeholders - set(replacements))
    assert not missing, f"{template_name}: нет значений для {missing}"


def test_tractor_and_trailer_placeholders_filled(generator, contract_payload):
    """Регресс: тягач и полуприцеп обязаны попадать в карту замен."""
    replacements = generator._build_replacements_map(contract_payload)
    assert replacements["tractor_brand"] == "Foton Auman"
    assert replacements["tractor_plate"] == "O844XY196"
    assert replacements["tractor_color"] == "Белый"
    assert replacements["trailer_brand"] == "YANGMINDA"
    assert replacements["trailer_plate"] == "71ABF18"


def test_legacy_nested_tractor_structure(generator, contract_payload):
    """Старый формат trailer.tractor тоже поддерживается."""
    payload = {
        "tractor": {},
        "trailer": {
            "tractor": contract_payload["tractor"],
            "trailer": contract_payload["trailer"],
        },
        "contract": contract_payload["contract"],
    }
    replacements = generator._build_replacements_map(payload)
    assert replacements["tractor_plate"] == "O844XY196"
    assert replacements["trailer_plate"] == "71ABF18"


# ─────────────────────────────────────────────────────────────
# Город
# ─────────────────────────────────────────────────────────────

def test_city_from_first_loading(generator, contract_payload):
    replacements = generator._build_replacements_map(contract_payload)
    assert replacements["city"] == "Мурманск"


def test_city_default_moscow(generator):
    replacements = generator._build_replacements_map({"contract": {}})
    assert replacements["city"] == "Москва"


def test_city_from_explicit_field(generator, contract_payload):
    payload = dict(contract_payload, city="г. Тверь")
    replacements = generator._build_replacements_map(payload)
    assert replacements["city"] == "Тверь"


# ─────────────────────────────────────────────────────────────
# Суммы, НДС и оплата
# ─────────────────────────────────────────────────────────────

def test_vat_calculation_for_ooo(generator, contract_payload):
    replacements = generator._build_replacements_map(contract_payload)
    # 180300 * 22% = 39666; итого 219966
    assert replacements["sum_wo_nds"] == "180300.00"
    assert replacements["sum_nds"] == "39666.00"
    assert replacements["sum_total"] == "219966.00"
    assert "22%" in replacements["nds_text"]
    assert replacements["vat_rate"] == "22%"


def test_no_vat_for_ip_without_vat(generator, contract_payload):
    payload = dict(contract_payload)
    payload["contract"] = dict(contract_payload["contract"],
                               carrier_type="ИП без НДС", vat_rate_num=0)
    replacements = generator._build_replacements_map(payload)
    assert replacements["sum_nds"] == ""
    assert replacements["nds_text"] == "НДС не облагается"
    assert replacements["sum_total"] == replacements["sum_wo_nds"] == "180300.00"
    assert "не является плательщиком НДС" in replacements["nds_status_text"]


def test_sum_in_words(generator, contract_payload):
    replacements = generator._build_replacements_map(contract_payload)
    assert replacements["sum_total_words"].startswith("Двести девятнадцать тысяч")
    assert "рубл" in replacements["sum_total_words"]


def test_payment_days(generator, contract_payload):
    replacements = generator._build_replacements_map(contract_payload)
    assert replacements["payment_days"] == "10"
    assert replacements["payment_days_words"] == "десяти"
    assert replacements["penalty_rate"] == "5000"


# ─────────────────────────────────────────────────────────────
# Машины и точки маршрута
# ─────────────────────────────────────────────────────────────

def test_cars_filled_and_emptied(generator, contract_payload):
    replacements = generator._build_replacements_map(contract_payload)
    assert replacements["car_1_brand"] == "JETOUR T2"
    assert replacements["car_1_vin"] == "EC3TEUMB0T0002608"
    assert replacements["cargo_count"] == "1"
    # позиции сверх количества машин пустые, а не «None»
    for index in (2, 5, 12):
        assert replacements[f"car_{index}_brand"] == ""
        assert replacements[f"car_{index}_vin"] == ""


def test_twelve_cars_limit(generator, contract_payload):
    payload = dict(contract_payload)
    payload["vehicles"] = [
        {"vin": f"EC3TEUMB0T{i:06d}", "brand_model": f"CAR {i}",
         "vehicle_type": "Легковой автомобиль"}
        for i in range(15)
    ]
    replacements = generator._build_replacements_map(payload)
    assert replacements["car_12_brand"] == "CAR 11"
    assert replacements["cargo_count"] == "15"


def test_point_labels(generator, contract_payload):
    points = contract_payload["loadings"] + contract_payload["unloadings"]
    assert generator._get_point_label(0, points, "Погрузка") == "—"
    assert generator._get_point_label(1, points, "Погрузка") == "Погрузка 1"
    assert generator._get_point_label(2, points, "Выгрузка") == "Выгрузка 2"
    assert generator._get_point_label(99, points, "Погрузка") == "—"


def test_loading_and_unloading_blocks(generator, contract_payload):
    replacements = generator._build_replacements_map(contract_payload)
    assert "Погрузка 1: 183052" in replacements["loading_block"]
    assert "Выгрузка 1" in replacements["unloading_block"]
    assert "EC3TEUMB0T0002608" in replacements["loading_block"]


def test_vehicle_without_point_goes_to_all_points(generator, contract_payload):
    """loading_index = 0 — машина попадает во все точки."""
    payload = dict(contract_payload)
    payload["loadings"] = [
        {"address": "Точка 1", "date": "", "time_window": ""},
        {"address": "Точка 2", "date": "", "time_window": ""},
    ]
    replacements = generator._build_replacements_map(payload)
    block = replacements["loading_block"]
    assert block.count("EC3TEUMB0T0002608") == 2


def test_vehicle_bound_to_single_point(generator, contract_payload):
    payload = dict(contract_payload)
    payload["loadings"] = [
        {"address": "Точка 1", "date": "", "time_window": ""},
        {"address": "Точка 2", "date": "", "time_window": ""},
    ]
    payload["vehicles"] = [dict(contract_payload["vehicles"][0], loading_index=2)]
    block = generator._build_replacements_map(payload)["loading_block"]
    assert block.count("EC3TEUMB0T0002608") == 1


# ─────────────────────────────────────────────────────────────
# Вспомогательные методы
# ─────────────────────────────────────────────────────────────

def test_date_helpers(generator):
    assert generator._format_date_full("2026-09-23") == "23.09.2026"
    assert generator._format_date_dot("2026-09-23") == "23.09"
    assert generator._day_of_month("2026-09-05") == "05"
    assert generator._month_name("2026-01-15") == "января"
    assert generator._month_name(None) == "сентября"
    assert generator._format_date_full("") == ""


def test_short_fio(generator):
    assert generator._short_fio("Добросоцкий Алексей Николаевич") == "А.Н. Добросоцкий"
    assert generator._short_fio("Иванов Иван") == "И. Иванов"
    assert generator._short_fio("Иванов") == "Иванов"
    assert generator._short_fio("") == ""


def test_days_to_words(generator):
    assert generator._days_to_words(10) == "десяти"
    assert generator._days_to_words("5") == "пяти"
    assert generator._days_to_words(0) == "0"
    assert generator._days_to_words("мусор") == "десяти"
    assert generator._days_to_words(None) == "десяти"


def test_template_selection(generator, templates_dir):
    assert generator._get_template_path("ООО (с НДС)").endswith("shablon_ooo.docx")
    assert generator._get_template_path("ИП с НДС").endswith("shablon_ip_with_vat.docx")
    assert generator._get_template_path("ИП без НДС").endswith("shablon_ip_without_vat.docx")
    assert generator._get_template_path("что-то иное").endswith("shablon_ooo.docx")


def test_default_output_dir(generator, project_root):
    assert generator.default_output_dir() == str(project_root / "output")


# ─────────────────────────────────────────────────────────────
# Генерация файла
# ─────────────────────────────────────────────────────────────

def test_generate_creates_file(generator, contract_payload, work_dir):
    path = generator.generate(contract_payload, output_dir=str(work_dir))
    try:
        assert path.endswith(".docx")
        assert path.startswith(str(work_dir))
        assert "23092026-74" in path
    finally:
        Path(path).unlink(missing_ok=True)


def test_generate_sanitizes_contract_number(generator, contract_payload, work_dir):
    payload = dict(contract_payload)
    payload["contract"] = dict(contract_payload["contract"], number='74/2026 "тест"')
    path = generator.generate(payload, output_dir=str(work_dir))
    try:
        filename = Path(path).name
        assert "/" not in filename and '"' not in filename
    finally:
        Path(path).unlink(missing_ok=True)


@pytest.mark.parametrize("template_name, carrier_type", [
    ("shablon_ooo.docx", "ООО (с НДС)"),
    ("shablon_ip_with_vat.docx", "ИП с НДС"),
    ("shablon_ip_without_vat.docx", "ИП без НДС"),
])
def test_generate_docx_renders_all_templates(generator, contract_payload,
                                             work_file, template_name, carrier_type):
    """Полный цикл рендера: нет незамещённых плейсхолдеров, данные на месте."""
    payload = dict(contract_payload)
    payload["contract"] = dict(contract_payload["contract"], carrier_type=carrier_type)

    output = work_file(f"out_{template_name}")
    engine = generator._render_template(
        str(generator.templates["ООО" if "ooo" in template_name else
                                ("ИП с НДС" if "with_vat" in template_name else "ИП без НДС")]),
        generator._build_replacements_map(payload),
        str(output),
    )
    generator._postprocess_document(str(output))

    assert engine == "docxtpl"
    assert placeholders_left(output) == []
    text = document_text(output)
    assert "Foton Auman" in text
    assert "71ABF18" in text
    assert "Мурманск" in text


def test_generate_docx_multiline_block_has_breaks(generator, contract_payload, work_file):
    payload = dict(contract_payload)
    payload["loadings"] = [
        {"address": "Точка 1", "date": "2026-09-24", "time_window": "09:00"},
        {"address": "Точка 2", "date": "2026-09-24", "time_window": ""},
    ]
    output = work_file("multi.docx")
    generator.generate_docx(payload, str(output))
    xml = document_xml(output)
    assert "<w:br" in xml
    text = document_text(output)
    assert "Точка 1" in text and "Точка 2" in text


def test_manual_render_fallback(generator, contract_payload, work_file, monkeypatch):
    """Если docxtpl недоступен, работает резервная ручная замена."""
    import sys

    monkeypatch.setitem(sys.modules, "docxtpl", None)
    output = work_file("manual.docx")
    engine = generator._render_template(
        generator.templates["ООО"],
        generator._build_replacements_map(contract_payload),
        str(output),
    )
    generator._postprocess_document(str(output))

    assert engine == "manual"
    assert placeholders_left(output) == []
    assert "Foton Auman" in document_text(output)


def test_empty_vehicle_rows_removed(generator, contract_payload, work_file):
    output = work_file("rows.docx")
    generator.generate_docx(contract_payload, str(output))

    doc = Document(output)
    vehicle_tables = [
        table for table in doc.tables
        if table.rows and any("VIN" in cell.text for cell in table.rows[0].cells)
    ]
    assert vehicle_tables, "таблица ТС не найдена"
    assert len(vehicle_tables[0].rows) < 13
