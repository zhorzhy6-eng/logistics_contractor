#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Банковские строки п. 9: пустые реквизиты не печатаются
(ШАГ «Три блока хвостов: срочное + баги + банковские строки»).

Что было
--------
Блок реквизитов в п. 9 печатался ЦЕЛИКОМ, даже когда банковских данных нет:

    р/с
    БИК
    к/с
    Банк:
    Корр. счёт:

Заказчик без банковских реквизитов (обычное дело для ИП, который платит
с карты) оставлял в договоре пять пустых строк — договор выглядел
незаполненным бланком.

Что стало
---------
Каждая банковская строка обёрнута в `{%p if has_* %}`: значения нет —
строки нет. У заказчика счёт и банк стоят в ОДНОЙ строке («р/с … в …»),
поэтому «в {{client_bank}}» — вложенный инлайновый `{% if %}`: счёт без
банка печатается, хвост «в » без названия — нет.

Флаги считает генератор (`PerevozkaGenerator._filled`) по НАПЕЧАТАННОМУ
значению: счета и БИК печатаются только цифрами (`_digits_only`), поэтому
«мусорное» поле (одни слова) строки не даёт.

Все данные синтетические, реальных ПДн нет.
"""

import re

import pytest
from docx import Document

from core.contract_generator import ContractGenerator

# ─────────────────────────────────────────────────────────────
# Синтетические данные
# ─────────────────────────────────────────────────────────────

DRIVER = {
    "full_name": "Иванов Иван Иванович", "birth_date": "1980-01-01",
    "birth_place": "г. Москва", "passport_series": "18 22",
    "passport_number": "926830", "passport_issue_date": "2023-01-30",
    "passport_issuer": "ОВД", "passport_code": "500-123",
    "registration_address": "г. Москва, ул. Тестовая, д. 1",
    "license_series": "99 36", "license_number": "123456",
    "license_issue_date": "2020-01-01", "license_expiry_date": "2030-01-01",
    "license_categories": "B, C, E", "phone": "+7 (999) 123-45-67",
}

CUSTOMER_ACCOUNT = "40702810000000000001"
CUSTOMER_BIK = "044525225"
CUSTOMER_CORR = "30101810400000000225"
CUSTOMER_BANK = "ПАО Сбербанк"

CARRIER_ACCOUNT = "40702810000000000002"
CARRIER_BIK = "044525226"
CARRIER_CORR = "30101810400000000226"
CARRIER_BANK = "АО «Банк Второй»"

#: Заказчик со ВСЕМИ банковскими реквизитами.
CUSTOMER_FULL = {
    "full_name": "ООО «Заказчик»", "short_name": "ООО «Заказчик»",
    "inn": "7707654321", "kpp": "770701001", "ogrn": "1027700132195",
    "legal_address": "г. Москва", "actual_address": "",
    "bank_account": CUSTOMER_ACCOUNT, "bik": CUSTOMER_BIK,
    "correspondent_account": CUSTOMER_CORR, "bank_name": CUSTOMER_BANK,
    "director_name": "Петров Пётр Петрович",
    "director_position": "Генеральный директор",
    "phone": "", "email": "",
}

#: Заказчик БЕЗ банковских реквизитов: ни счёта, ни БИК, ни корр. счёта,
#: ни названия банка. Ровно тот случай, ради которого правка делалась.
CUSTOMER_NO_BANK = dict(
    CUSTOMER_FULL,
    bank_account="", bik="", correspondent_account="", bank_name="",
)

CARRIER_FULL = {
    "full_name": "ООО «Перевозчик»", "short_name": "ООО «Перевозчик»",
    "inn": "7701234567", "kpp": "770101001", "ogrn": "1027700132195",
    "legal_address": "г. Москва", "actual_address": "",
    "bank_account": CARRIER_ACCOUNT, "bik": CARRIER_BIK,
    "correspondent_account": CARRIER_CORR, "bank_name": CARRIER_BANK,
    "director_name": "Сидоров Сидор Сидорович",
    "director_position": "Генеральный директор",
    "phone": "", "email": "", "entity_type": "ООО",
}

TRACTOR = {"brand_model": "Foton Auman", "plate_number": "O844XY196",
           "color": "Белый", "year": 2023}
TRAILER = {"brand_model": "YANGMINDA", "plate_number": "71ABF18",
           "color": "Серый", "year": 2020}

#: Три бланка перевозки: (тип перевозчика, ставка НДС, ставка числом).
TEMPLATE_VARIANTS = (
    ("ООО (с НДС)", "22%", 22),
    ("ИП с НДС", "22%", 22),
    ("ИП без НДС", "0%", 0),
)


@pytest.fixture
def generator(templates_dir) -> ContractGenerator:
    return ContractGenerator(templates_dir=str(templates_dir))


def _payload(customer=None, carrier=None, variant=0) -> dict:
    carrier_type, vat_rate, vat_rate_num = TEMPLATE_VARIANTS[variant]
    return {
        "driver": dict(DRIVER),
        "carrier": dict(CARRIER_FULL, entity_type=(
            "ИП" if carrier_type.startswith("ИП") else "ООО"
        )) if carrier is None else carrier,
        "customer": dict(CUSTOMER_FULL if customer is None else customer),
        "vehicles": [{"vin": "EC3TEUMB0T0002608", "brand_model": "JETOUR T2",
                      "vehicle_type": "Легковой автомобиль"}],
        "tractor": dict(TRACTOR),
        "trailer": dict(TRAILER),
        "contract": {
            "number": "BANK-1", "date": "2026-10-09",
            "carrier_type": carrier_type,
            "vat_rate": vat_rate, "vat_rate_num": vat_rate_num,
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


def _client_lines(doc):
    """Строки ячейки «ЗАКАЗЧИК:» п. 9."""
    return _cell_lines(doc, "ЗАКАЗЧИК:")


def _carrier_lines(doc):
    """Строки ячейки «ПЕРЕВОЗЧИК» п. 9 (у неё первая строка без двоеточия)."""
    return _cell_lines(doc, "ПЕРЕВОЗЧИК")


def _lines_starting(lines, prefix: str):
    return [line for line in lines if line.startswith(prefix)]


def _document_text(doc) -> str:
    parts = [_flatten(p.text) for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(_flatten(cell.text))
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────
# Заказчик: пустые банковские строки не печатаются
# ─────────────────────────────────────────────────────────────

def test_no_empty_account_line_when_account_empty(generator, work_file):
    """
    Нет счёта заказчика — нет строки «р/с ».

    Название банка при этом заполнено: строка счёта всё равно не печатается
    (счёт — её главная часть), и «в ПАО Сбербанк» в договоре не остаётся.
    """
    customer = dict(CUSTOMER_NO_BANK, bank_name=CUSTOMER_BANK)
    doc = _render(generator, work_file("bank_client_account.docx"),
                  customer=customer)

    lines = _client_lines(doc)
    assert _lines_starting(lines, "р/с") == []
    assert "Сбербанк" not in "\n".join(lines), "остался хвост «в <банк>»"
    # Остальные реквизиты заказчика на месте — правка их не тронула.
    assert "ИНН 7707654321" in lines
    assert "ОГРН 1027700132195" in lines


def test_no_empty_bik_line_when_bik_empty(generator, work_file):
    """Нет БИК заказчика — нет строки «БИК »."""
    customer = dict(CUSTOMER_NO_BANK, bank_account=CUSTOMER_ACCOUNT)
    doc = _render(generator, work_file("bank_client_bik.docx"),
                  customer=customer)

    lines = _client_lines(doc)
    assert _lines_starting(lines, "БИК") == []
    assert _lines_starting(lines, "р/с") == [f"р/с {CUSTOMER_ACCOUNT}"]


def test_no_empty_corr_line_when_corr_empty(generator, work_file):
    """Нет корреспондентского счёта заказчика — нет строки «к/с »."""
    customer = dict(CUSTOMER_NO_BANK, bank_account=CUSTOMER_ACCOUNT,
                    bik=CUSTOMER_BIK)
    doc = _render(generator, work_file("bank_client_corr.docx"),
                  customer=customer)

    lines = _client_lines(doc)
    assert _lines_starting(lines, "к/с") == []
    assert _lines_starting(lines, "БИК") == [f"БИК {CUSTOMER_BIK}"]


def test_no_empty_bank_line_when_bank_empty(generator, work_file):
    """
    Счёт есть, названия банка нет — строка «р/с … в » не печатается.

    Хвост «в » без названия — такая же «дырка», как пустая строка целиком:
    в старой раскладке он оставался от «р/с {{client_account}} в
    {{client_bank}}».
    """
    customer = dict(CUSTOMER_NO_BANK, bank_account=CUSTOMER_ACCOUNT)
    doc = _render(generator, work_file("bank_client_bank.docx"),
                  customer=customer)

    lines = _client_lines(doc)
    account_lines = _lines_starting(lines, "р/с")
    assert account_lines == [f"р/с {CUSTOMER_ACCOUNT}"], account_lines
    assert " в" not in account_lines[0], "остался хвост «в » без банка"


def test_account_and_bank_printed_together(generator, work_file):
    """Счёт и банк заполнены — печатаются вместе, одной строкой."""
    doc = _render(generator, work_file("bank_client_account_bank.docx"))

    lines = _client_lines(doc)
    assert f"р/с {CUSTOMER_ACCOUNT} в {CUSTOMER_BANK}" in lines, lines


def test_account_printed_without_bank(generator, work_file):
    """Счёт без банка печатается — терять реквизит из-за пустого банка нельзя."""
    customer = dict(CUSTOMER_FULL, bank_name="")
    doc = _render(generator, work_file("bank_client_no_bank.docx"),
                  customer=customer)

    lines = _client_lines(doc)
    assert f"р/с {CUSTOMER_ACCOUNT}" in lines, lines
    assert f"р/с {CUSTOMER_ACCOUNT} в" not in lines


def test_all_bank_fields_filled_prints_all(generator, work_file):
    """Все банковские реквизиты заказчика заполнены — печатаются все три строки."""
    doc = _render(generator, work_file("bank_client_full.docx"))

    lines = _client_lines(doc)
    assert f"р/с {CUSTOMER_ACCOUNT} в {CUSTOMER_BANK}" in lines
    assert f"БИК {CUSTOMER_BIK}" in lines
    assert f"к/с {CUSTOMER_CORR}" in lines


def test_all_bank_fields_empty_prints_no_bank_lines(generator, work_file):
    """Ни одного банковского реквизита — ни одной банковской строки."""
    doc = _render(generator, work_file("bank_client_none.docx"),
                  customer=CUSTOMER_NO_BANK)

    lines = _client_lines(doc)
    assert not [line for line in lines
                if line.startswith(("р/с", "БИК", "к/с"))], lines
    # Проверка именно ячейки заказчика: у перевозчика реквизиты заполнены,
    # и его «р/с …» в документе есть на законном основании.
    assert CUSTOMER_ACCOUNT not in "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Перевозчик: то же правило
# ─────────────────────────────────────────────────────────────

def test_carrier_no_empty_account_line(generator, work_file):
    """Нет счёта перевозчика — нет строки «р/с »."""
    doc = _render(generator, work_file("bank_carrier_account.docx"),
                  carrier=dict(CARRIER_FULL, bank_account=""))

    lines = _carrier_lines(doc)
    assert _lines_starting(lines, "р/с") == []
    assert _lines_starting(lines, "Банк:") == [f"Банк: {CARRIER_BANK}"]


def test_carrier_no_empty_bik_line(generator, work_file):
    """Нет БИК перевозчика — нет строки «БИК »."""
    doc = _render(generator, work_file("bank_carrier_bik.docx"),
                  carrier=dict(CARRIER_FULL, bik=""))

    lines = _carrier_lines(doc)
    assert _lines_starting(lines, "БИК") == []
    assert f"р/с {CARRIER_ACCOUNT}" in lines


def test_carrier_no_empty_corr_line(generator, work_file):
    """Нет корр. счёта перевозчика — нет строки «Корр. счёт:»."""
    doc = _render(generator, work_file("bank_carrier_corr.docx"),
                  carrier=dict(CARRIER_FULL, correspondent_account=""))

    lines = _carrier_lines(doc)
    assert _lines_starting(lines, "Корр.") == []
    assert f"БИК {CARRIER_BIK}" in lines


def test_carrier_no_empty_bank_line(generator, work_file):
    """Нет названия банка перевозчика — нет строки «Банк:»."""
    doc = _render(generator, work_file("bank_carrier_bank.docx"),
                  carrier=dict(CARRIER_FULL, bank_name=""))

    lines = _carrier_lines(doc)
    assert _lines_starting(lines, "Банк") == []
    assert f"р/с {CARRIER_ACCOUNT}" in lines


def test_carrier_all_bank_fields_filled_prints_all(generator, work_file):
    """Все банковские реквизиты перевозчика заполнены — печатаются все четыре."""
    doc = _render(generator, work_file("bank_carrier_full.docx"))

    lines = _carrier_lines(doc)
    assert f"р/с {CARRIER_ACCOUNT}" in lines
    assert f"Банк: {CARRIER_BANK}" in lines
    assert f"БИК {CARRIER_BIK}" in lines
    assert f"Корр. счёт: {CARRIER_CORR}" in lines


def test_carrier_all_bank_fields_empty_prints_no_bank_lines(generator, work_file):
    """Пустые банковские реквизиты перевозчика — ни одной банковской строки."""
    doc = _render(
        generator, work_file("bank_carrier_none.docx"),
        carrier=dict(CARRIER_FULL, bank_account="", bik="",
                     correspondent_account="", bank_name=""),
    )

    lines = _carrier_lines(doc)
    assert not [line for line in lines
                if line.startswith(("р/с", "Банк", "БИК", "Корр."))], lines


# ─────────────────────────────────────────────────────────────
# Все три бланка: правило одно
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("variant", range(len(TEMPLATE_VARIANTS)))
def test_no_bank_lines_in_each_template(generator, work_file, variant):
    """
    В КАЖДОМ из трёх бланков пустые банковские строки не печатаются.

    Бланков три (ООО, ИП с НДС, ИП без НДС), и правка идёт в каждый: проверка
    только на ООО-бланке пропустила бы недоведённый бланк ИП.
    """
    carrier_type = TEMPLATE_VARIANTS[variant][0]
    doc = _render(
        generator, work_file(f"bank_none_v{variant}.docx"),
        customer=CUSTOMER_NO_BANK,
        carrier=dict(CARRIER_FULL, bank_account="", bik="",
                     correspondent_account="", bank_name=""),
        variant=variant,
    )

    text = _document_text(doc)
    for marker in ("р/с", "БИК", "к/с", "Корр. счёт:", "Банк:"):
        assert marker not in text, f"{carrier_type}: осталась строка «{marker}»"


@pytest.mark.parametrize("variant", range(len(TEMPLATE_VARIANTS)))
def test_all_bank_lines_in_each_template(generator, work_file, variant):
    """Заполненные реквизиты в каждом бланке печатаются полностью."""
    carrier_type = TEMPLATE_VARIANTS[variant][0]
    doc = _render(generator, work_file(f"bank_full_v{variant}.docx"),
                  variant=variant)

    client = _client_lines(doc)
    carrier = _carrier_lines(doc)
    assert f"р/с {CUSTOMER_ACCOUNT} в {CUSTOMER_BANK}" in client, carrier_type
    assert f"БИК {CUSTOMER_BIK}" in client, carrier_type
    assert f"к/с {CUSTOMER_CORR}" in client, carrier_type
    assert f"р/с {CARRIER_ACCOUNT}" in carrier, carrier_type
    assert f"Банк: {CARRIER_BANK}" in carrier, carrier_type
    assert f"БИК {CARRIER_BIK}" in carrier, carrier_type
    assert f"Корр. счёт: {CARRIER_CORR}" in carrier, carrier_type


# ─────────────────────────────────────────────────────────────
# Карта замен: флаги и целые ключи
# ─────────────────────────────────────────────────────────────

BANK_FLAGS = (
    "has_client_account", "has_client_bik", "has_client_corr_account",
    "has_client_bank",
    "has_carrier_account", "has_carrier_bik", "has_carrier_corr_account",
    "has_carrier_bank",
)


def test_bank_flags_are_present_and_boolean(generator):
    """Все восемь банковских флагов есть в карте замен и это bool."""
    replacements = generator._build_replacements_map(_payload())

    for flag in BANK_FLAGS:
        assert flag in replacements, f"нет ключа {flag}"
        assert isinstance(replacements[flag], bool), f"{flag} не bool"
        assert replacements[flag] is True, f"{flag} должен быть True"


def test_bank_flags_are_false_for_empty_values(generator):
    """Пустые банковские поля — все восемь флагов False."""
    replacements = generator._build_replacements_map(_payload(
        customer=CUSTOMER_NO_BANK,
        carrier=dict(CARRIER_FULL, bank_account="", bik="",
                     correspondent_account="", bank_name=""),
    ))

    for flag in BANK_FLAGS:
        assert replacements[flag] is False, f"{flag} должен быть False"


@pytest.mark.parametrize("junk", ["нет данных", "—", "0"])
def test_bank_flags_ignore_junk_values(generator, junk):
    """
    «Мусор» вместо реквизита — флаг False: печатать нечего.

    Счета и БИК печатаются только цифрами (`_digits_only`), поэтому у поля
    «нет данных» значения в договоре нет, и строка «р/с » не нужна. «0» —
    то же пустое значение, что и у остальных необязательных полей.
    """
    replacements = generator._build_replacements_map(_payload(
        customer=dict(CUSTOMER_NO_BANK, bank_account=junk, bik=junk,
                      correspondent_account=junk),
    ))

    assert replacements["has_client_account"] is False
    assert replacements["has_client_bik"] is False
    assert replacements["has_client_corr_account"] is False


def test_bank_value_keys_are_not_removed(generator):
    """Ключи значений остались: их читают бланки и внешний код."""
    replacements = generator._build_replacements_map(_payload())

    for key in ("client_account", "client_bik", "client_corr_account",
                "client_bank", "carrier_account", "carrier_bik",
                "carrier_corr_account", "carrier_bank"):
        assert key in replacements, f"ключ {key} пропал из карты замен"
