#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Пустые необязательные поля в договоре перевозки (ШАГ «Фикс сохранения +
динамические стороны в шаблонах», часть C.6).

Что было
--------
Строка необязательного поля печаталась ВСЕГДА, даже с пустым значением:
«Цвет:», «Год выпуска:», «Место рождения:», «Категории:», «Срок действия: до»,
«E-mail:», «Фактический адрес:», «КПП ,». В договоре это выглядит как
незаполненный бланк.

Что стало
---------
Такие строки обёрнуты в `{%p if has_... %}`: значения нет — строки нет.
Флаги считает генератор (`PerevozkaGenerator._filled`), «0» и «0.0» считаются
пустыми — так интерфейс отдаёт незаполненный год выпуска.

Данные синтетические, ПДн нет.
"""

import re

import pytest
from docx import Document

from core.contract_generator import ContractGenerator

DRIVER_FULL = {
    "full_name": "Иванов Иван Иванович", "birth_date": "1980-01-01",
    "birth_place": "г. Москва", "passport_series": "18 22",
    "passport_number": "926830", "passport_issue_date": "2023-01-30",
    "passport_issuer": "ОВД", "passport_code": "500-123",
    "registration_address": "г. Москва, ул. Тестовая, д. 1",
    "license_series": "99 36", "license_number": "123456",
    "license_issue_date": "2020-01-01", "license_expiry_date": "2030-01-01",
    "license_categories": "B, C, E", "phone": "+7 (999) 123-45-67",
}

#: Пустые необязательные поля водителя: место рождения, срок действия ВУ,
#: категории и телефон.
DRIVER_EMPTY = dict(
    DRIVER_FULL, birth_place="", license_expiry_date="",
    license_categories="", phone="",
)

CUSTOMER = {
    "full_name": "ООО «Заказчик»", "short_name": "ООО «Заказчик»",
    "inn": "7707654321", "kpp": "770701001", "ogrn": "1027700132195",
    "legal_address": "г. Москва", "actual_address": "",
    "bank_account": "40702810000000000001", "bik": "044525225",
    "correspondent_account": "30101810400000000225", "bank_name": "ПАО Сбербанк",
    "director_name": "Петров Пётр Петрович",
    "director_position": "Генеральный директор",
    "phone": "", "email": "",
}

OOO_CARRIER = {
    "full_name": "ООО «Перевозчик»", "short_name": "ООО «Перевозчик»",
    "inn": "7701234567", "kpp": "770101001", "ogrn": "1027700132195",
    "legal_address": "г. Москва", "actual_address": "",
    "bank_account": "40702810000000000002", "bik": "044525225",
    "correspondent_account": "30101810400000000225", "bank_name": "ПАО Сбербанк",
    "director_name": "Сидоров Сидор Сидорович",
    "director_position": "Генеральный директор",
    "phone": "", "email": "", "entity_type": "ООО",
}

TRACTOR_FULL = {"brand_model": "Foton Auman", "plate_number": "O844XY196",
                "color": "Белый", "year": 2023}
TRAILER_FULL = {"brand_model": "YANGMINDA", "plate_number": "71ABF18",
                "color": "Серый", "year": 2020}

#: Пустые цвет и год у обоих ТС: интерфейс отдаёт год пустой строкой,
#: а необязательный «0» приходит от старых записей.
TRACTOR_EMPTY = dict(TRACTOR_FULL, color="", year="")
TRAILER_EMPTY = dict(TRAILER_FULL, color="", year=0)


@pytest.fixture
def generator(templates_dir) -> ContractGenerator:
    return ContractGenerator(templates_dir=str(templates_dir))


def _payload(driver=None, tractor=None, trailer=None, carrier=None,
             customer=None, carrier_type="ООО (с НДС)") -> dict:
    return {
        "driver": dict(DRIVER_FULL if driver is None else driver),
        "carrier": dict(OOO_CARRIER if carrier is None else carrier),
        "customer": dict(CUSTOMER if customer is None else customer),
        "vehicles": [{"vin": "EC3TEUMB0T0002608", "brand_model": "JETOUR T2",
                      "vehicle_type": "Легковой автомобиль"}],
        "tractor": dict(TRACTOR_FULL if tractor is None else tractor),
        "trailer": dict(TRAILER_FULL if trailer is None else trailer),
        "contract": {
            "number": "OPT-1", "date": "2026-10-09",
            "carrier_type": carrier_type,
            "vat_rate": "22%" if carrier_type.startswith("ООО") else "0%",
            "vat_rate_num": 22 if carrier_type.startswith("ООО") else 0,
            "price_without_vat": 180300.0, "payment_days": 10,
        },
        "loadings": [{"address": "г. Воронеж", "date": "2026-10-10",
                      "time_window": ""}],
        "unloadings": [{"address": "г. Москва", "date": "2026-10-13",
                        "time_window": ""}],
    }


def _render(generator, output, **kwargs) -> Document:
    generator.generate_docx(_payload(**kwargs), str(output))
    assert output.exists(), f"файл не создан: {output}"
    return Document(str(output))


def _flatten(text: str) -> str:
    """Текст одной строкой: пробелы (в том числе неразрывные) по одному."""
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _document_text(doc) -> str:
    parts = [_flatten(p.text) for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(_flatten(cell.text))
    return "\n".join(parts)


def _clause_1_2(doc) -> str:
    """Текст п. 1.2: абзац сразу за строкой-меткой «1.2. Перевозчик:»."""
    texts = [p.text for p in doc.paragraphs]
    for index, text in enumerate(texts):
        if text.strip().startswith("1.2. Перевозчик:"):
            for following in texts[index + 1:]:
                if following.strip():
                    return _flatten(following)
    raise AssertionError("абзац «1.2. Перевозчик:» не найден")


def _cell_lines(doc, first_line: str):
    """Непустые строки ячейки, которая начинается с заданной строки."""
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                lines = [
                    _flatten(line) for line in cell.text.split("\n")
                    if line.strip()
                ]
                if lines and lines[0].startswith(first_line):
                    return lines
    raise AssertionError(f"ячейка {first_line!r} не найдена")


def _vehicle_lines(doc):
    """(строки тягача, строки прицепа) из ячейки «Транспортное средство»."""
    lines = _cell_lines(doc, "Тягач:")
    split = next(
        index for index, line in enumerate(lines)
        if line.startswith("Прицеп")
    )
    return lines[:split], lines[split:]


def _labels(lines) -> list:
    """Метки строк («Цвет», «Год выпуска», …) без значений."""
    return [line.split(":")[0] for line in lines if ":" in line]


# ─────────────────────────────────────────────────────────────
# Тягач и прицеп
# ─────────────────────────────────────────────────────────────

def test_tractor_color_absent_not_printed(generator, work_file):
    """Пустой цвет тягача — строки «Цвет:» у тягача нет."""
    doc = _render(generator, work_file("opt_tractor_color.docx"),
                  tractor=dict(TRACTOR_FULL, color=""))

    tractor_lines, trailer_lines = _vehicle_lines(doc)
    assert "Цвет" not in _labels(tractor_lines)
    assert "Цвет: Серый" in trailer_lines, "цвет прицепа не тронут"


def test_tractor_year_absent_not_printed(generator, work_file):
    """Пустой год тягача — строки «Год выпуска:» у тягача нет."""
    doc = _render(generator, work_file("opt_tractor_year.docx"),
                  tractor=dict(TRACTOR_FULL, year=""))

    tractor_lines, trailer_lines = _vehicle_lines(doc)
    assert "Год выпуска" not in _labels(tractor_lines)
    assert "Год выпуска: 2020" in trailer_lines


def test_trailer_color_absent_not_printed(generator, work_file):
    """Пустой цвет прицепа — строки «Цвет:» у прицепа нет."""
    doc = _render(generator, work_file("opt_trailer_color.docx"),
                  trailer=dict(TRAILER_FULL, color=""))

    tractor_lines, trailer_lines = _vehicle_lines(doc)
    assert "Цвет" not in _labels(trailer_lines)
    assert "Цвет: Белый" in tractor_lines


def test_trailer_year_zero_not_printed(generator, work_file):
    """Год прицепа «0» — то же, что пусто: строка не печатается."""
    doc = _render(generator, work_file("opt_trailer_year.docx"),
                  trailer=dict(TRAILER_FULL, year=0))

    tractor_lines, trailer_lines = _vehicle_lines(doc)
    assert "Год выпуска" not in _labels(trailer_lines)
    assert "Год выпуска: 2023" in tractor_lines


def test_vehicle_fields_printed_when_filled(generator, work_file):
    """Заполненные цвет и год печатаются у обоих ТС — как раньше."""
    doc = _render(generator, work_file("opt_vehicle_full.docx"))

    tractor_lines, trailer_lines = _vehicle_lines(doc)
    assert "Цвет: Белый" in tractor_lines
    assert "Год выпуска: 2023" in tractor_lines
    assert "Цвет: Серый" in trailer_lines
    assert "Год выпуска: 2020" in trailer_lines


def test_all_vehicle_values_absent_gives_no_labels(generator, work_file):
    """Оба ТС без цвета и года — ни одной «дырки» в блоке ТС."""
    doc = _render(generator, work_file("opt_vehicle_empty.docx"),
                  tractor=TRACTOR_EMPTY, trailer=TRAILER_EMPTY)

    tractor_lines, trailer_lines = _vehicle_lines(doc)
    for lines in (tractor_lines, trailer_lines):
        assert "Цвет" not in _labels(lines)
        assert "Год выпуска" not in _labels(lines)


# ─────────────────────────────────────────────────────────────
# Водитель
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("label", [
    "Место рождения", "Срок действия", "Категории", "Телефон",
])
def test_driver_optional_label_absent_not_printed(generator, work_file, label):
    """Пустые необязательные поля водителя не печатаются (4 метки)."""
    doc = _render(generator, work_file(f"opt_driver_{label}.docx"),
                  driver=DRIVER_EMPTY)

    lines = _cell_lines(doc, "ФИО:")
    assert label not in _labels(lines), f"строка «{label}:» напечатана пустой"


def test_driver_optional_fields_printed_when_filled(generator, work_file):
    """Заполненные поля водителя печатаются — правка ничего не потеряла."""
    doc = _render(generator, work_file("opt_driver_full.docx"))

    lines = _cell_lines(doc, "ФИО:")
    assert "Место рождения: г. Москва" in lines
    assert "Срок действия: до 01.01.2030" in lines
    assert "Категории: B, C, E" in lines
    assert "Телефон: +7 (999) 123-45-67" in lines


# ─────────────────────────────────────────────────────────────
# Перевозчик и заказчик
# ─────────────────────────────────────────────────────────────

def test_carrier_email_absent_not_printed(generator, work_file):
    """Пустой E-mail перевозчика — строки нет (ни «E-mail:», ни «E‑mail:»)."""
    doc = _render(generator, work_file("opt_carrier_email.docx"))

    text = _document_text(doc)
    assert "E-mail:" not in text
    assert "E\u2011mail:" not in text


def test_carrier_kpp_absent_not_printed(generator, work_file):
    """
    Пустой КПП ПЕРЕВОЗЧИКА — в его ветви нет ни «КПП», ни «КПП ,».

    Проверяется п. 1.2, а не весь документ: у ЗАКАЗЧИКА КПП заполнен, и в
    п. 1.1 он печатается законно (ШАГ «Полные стороны + склонение с учётом
    рода» добавил реквизиты и в п. 1.1).
    """
    carrier = dict(OOO_CARRIER, kpp="")
    doc = _render(generator, work_file("opt_carrier_kpp.docx"), carrier=carrier)

    clause = _clause_1_2(doc)
    assert "КПП" not in clause
    assert "КПП ," not in _document_text(doc)


def test_carrier_actual_address_absent_not_printed(generator, work_file):
    """Пустой фактический адрес — строки нет (бланк ИП)."""
    doc = _render(generator, work_file("opt_carrier_addr.docx"),
                  carrier_type="ИП без НДС")

    assert "Фактический адрес:" not in _document_text(doc)


def test_client_email_absent_not_printed(generator, work_file):
    """Пустой E-mail заказчика — строки нет."""
    doc = _render(generator, work_file("opt_client_email.docx"))

    assert "mail:" not in _document_text(doc)


def test_carrier_email_printed_when_filled(generator, work_file):
    """Заполненный E-mail перевозчика на месте."""
    carrier = dict(OOO_CARRIER, email="carrier@example.ru")
    doc = _render(generator, work_file("opt_carrier_email_full.docx"),
                  carrier=carrier)

    assert "E-mail: carrier@example.ru" in _document_text(doc)


# ─────────────────────────────────────────────────────────────
# Флаги в карте замен
# ─────────────────────────────────────────────────────────────

HAS_FLAGS = (
    "has_tractor_color", "has_tractor_year",
    "has_trailer_color", "has_trailer_year",
    "has_driver_birth_place", "has_driver_passport_code",
    "has_driver_license_categories", "has_driver_license_expiry",
    "has_driver_phone",
    "has_carrier_kpp", "has_carrier_license_number", "has_carrier_license_date",
    "has_client_actual_address", "has_client_phone", "has_client_email",
)


def test_flags_are_present_and_boolean(generator):
    """Все флаги необязательных полей есть в карте замен и это bool."""
    replacements = generator._build_replacements_map(_payload())

    for flag in HAS_FLAGS:
        assert flag in replacements, f"нет ключа {flag}"
        assert isinstance(replacements[flag], bool), f"{flag} не bool"


def test_flags_are_false_for_empty_and_zero(generator):
    """Пустое значение и «0» — флаг False; заполненное — True."""
    replacements = generator._build_replacements_map(_payload(
        driver=DRIVER_EMPTY, tractor=TRACTOR_EMPTY, trailer=TRAILER_EMPTY,
        carrier=dict(OOO_CARRIER, kpp="", license_number="", license_date=""),
        customer=dict(CUSTOMER, email="", phone="", actual_address=""),
    ))

    for flag in ("has_tractor_color", "has_tractor_year", "has_trailer_color",
                 "has_trailer_year", "has_driver_birth_place",
                 "has_driver_license_categories", "has_driver_license_expiry",
                 "has_driver_phone", "has_carrier_kpp",
                 "has_carrier_license_number", "has_carrier_email",
                 "has_client_phone", "has_client_email",
                 "has_client_actual_address"):
        assert replacements[flag] is False, f"{flag} должен быть False"


def test_optional_keys_are_not_removed(generator):
    """Старые ключи значений остаются: шаблоны и внешний код их читают."""
    replacements = generator._build_replacements_map(_payload())

    for key in ("tractor_color", "tractor_year", "trailer_color",
                "trailer_year", "driver_birth_place", "driver_phone",
                "driver_license_categories", "driver_license_expiry",
                "carrier_kpp", "carrier_email", "client_email"):
        assert key in replacements, f"ключ {key} пропал из карты замен"
