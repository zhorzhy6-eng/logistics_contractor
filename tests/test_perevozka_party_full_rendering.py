#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Полные стороны в п. 1.1 и 1.2 договора перевозки (ШАГ «Полные стороны +
склонение с учётом рода»).

Что было
--------
Сторона печаталась неполно и в именительном падеже:

  * у ООО не было ИНН, КПП и ОГРН — они лежали только в п. 9;
  * «в лице директора Ахмедов Тимур Артурович, действующего» — именительный
    падеж вместо родительного;
  * у директора-женщины причастие оставалось мужским («действующего»);
  * у ИП-женщины — «именуемый» вместо «именуемая»;
  * у ИП-заказчика не было приставки «Индивидуальный предприниматель»;
  * КПП печатался у ИП (у него КПП не существует), а у ИП-перевозчика с
    устаревшим типом «ООО (с НДС)» в форме печатались ОГРН и «именуемое».

Проверяется ГОТОВЫЙ документ: тесты читают п. 1.1 и 1.2 из .docx и сверяют
их целиком. Данные синтетические, ПДн нет.
"""

import re

import pytest
from docx import Document

from core.contract_generator import ContractGenerator

#: Перевозчик — ООО с директором-мужчиной.
CARRIER_OOO = {
    "full_name": "ООО «Перевозчик»", "short_name": "ООО «Перевозчик»",
    "inn": "7701234567", "kpp": "770101001", "ogrn": "1027700132195",
    "legal_address": "г. Москва", "actual_address": "",
    "bank_account": "40702810000000000002", "bik": "044525225",
    "correspondent_account": "30101810400000000225", "bank_name": "ПАО Сбербанк",
    "director_name": "Петров Пётр Петрович",
    "director_position": "Генеральный директор",
    "phone": "", "email": "", "entity_type": "ООО (с НДС)",
}

#: Перевозчик — ООО с директором-женщиной.
CARRIER_OOO_FEMALE = dict(
    CARRIER_OOO, director_name="Сидорова Анна Петровна",
    director_position="Директор",
)

#: Перевозчик — ИП-мужчина.
CARRIER_IP_MALE = {
    "full_name": "Индивидуальный предприниматель Добросоцкий Алексей Николаевич",
    "short_name": "", "inn": "770123456780", "kpp": "",
    "ogrn": "315770000000013", "legal_address": "г. Тверь",
    "actual_address": "", "bank_account": "40702810123456789013",
    "bik": "044525225", "correspondent_account": "30101810400000000225",
    "bank_name": "ПАО Сбербанк", "director_name": "",
    "director_position": "", "phone": "", "email": "", "entity_type": "ИП",
}

#: Перевозчик — ИП-женщина.
CARRIER_IP_FEMALE = {
    "full_name": "Индивидуальный предприниматель Хейгетян Елена Валентиновна",
    "short_name": "ИП Хейгетян Е.В.", "inn": "770123456789", "kpp": "",
    "ogrn": "315770000000012", "legal_address": "г. Волгоград",
    "actual_address": "", "bank_account": "40702810123456789012",
    "bik": "044525225", "correspondent_account": "30101810400000000225",
    "bank_name": "ПАО Сбербанк", "director_name": "",
    "director_position": "", "phone": "", "email": "", "entity_type": "ИП",
}

#: Заказчик — ООО с директором-мужчиной.
CUSTOMER_OOO_MALE = {
    "full_name": "ООО «Заказчик»", "short_name": "ООО «Заказчик»",
    "inn": "7707654321", "kpp": "770701001", "ogrn": "1027700132195",
    "legal_address": "г. Москва", "actual_address": "",
    "bank_account": "40702810000000000001", "bik": "044525225",
    "correspondent_account": "30101810400000000225", "bank_name": "ПАО Сбербанк",
    "director_name": "Ахмедов Тимур Артурович",
    "director_position": "Генеральный директор",
    "phone": "", "email": "",
}

#: Заказчик — ООО с директором-женщиной.
CUSTOMER_OOO_FEMALE = dict(
    CUSTOMER_OOO_MALE, full_name="ООО «Василёк»", short_name="ООО «Василёк»",
    director_name="Сидорова Анна Петровна", director_position="Директор",
)

#: Заказчик — ИП-женщина.
CUSTOMER_IP_FEMALE = {
    "full_name": "Индивидуальный предприниматель Хейгетян Елена Валентиновна",
    "short_name": "ИП Хейгетян Е.В.", "inn": "770123456789", "kpp": "",
    "ogrn": "315770000000012", "legal_address": "г. Волгоград",
    "actual_address": "", "bank_account": "", "bik": "",
    "correspondent_account": "", "bank_name": "", "director_name": "",
    "director_position": "", "phone": "", "email": "",
}

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


@pytest.fixture
def generator(templates_dir) -> ContractGenerator:
    return ContractGenerator(templates_dir=str(templates_dir))


def _payload(carrier, customer, carrier_type="ООО (с НДС)") -> dict:
    return {
        "driver": DRIVER, "carrier": carrier, "customer": customer,
        "vehicles": [{"vin": "EC3TEUMB0T0002608", "brand_model": "JETOUR T2",
                      "vehicle_type": "Легковой автомобиль"}],
        "tractor": {"brand_model": "Foton Auman", "plate_number": "O844XY196",
                    "color": "Белый", "year": 2023},
        "trailer": {"brand_model": "YANGMINDA", "plate_number": "71ABF18",
                    "color": "Серый", "year": 2020},
        "contract": {
            "number": "PARTY-1", "date": "2026-10-09",
            "carrier_type": carrier_type,
            "vat_rate": "22%" if carrier_type.startswith("ООО") else "0%",
            "vat_rate_num": 22 if carrier_type.startswith("ООО") else 0,
            "price_without_vat": 180300.0, "payment_days": 10,
        },
        "loadings": [{"address": "г. Воронеж, ул. Остужева 52Б",
                      "date": "2026-10-10", "time_window": "09:00-15:00"}],
        "unloadings": [{"address": "г. Москва, Перерва 19 стр 3",
                        "date": "2026-10-13", "time_window": ""}],
    }


def _render(generator, output, carrier=None, customer=None, carrier_type="ООО (с НДС)"):
    generator.generate_docx(
        _payload(carrier or CARRIER_OOO, customer or CUSTOMER_OOO_MALE, carrier_type),
        str(output),
    )
    assert output.exists(), f"файл не создан: {output}"
    return Document(str(output))


def _flatten(text: str) -> str:
    """Текст одной строкой: пробелы (в том числе неразрывные) по одному."""
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _clause_text(doc, label: str) -> str:
    """
    Текст пункта: абзац сразу за строкой-меткой («1.1. Заказчик:»).

    Пункт ищется по метке, а не по номеру абзаца: в трёх бланках он стоит на
    разных позициях, и жёсткий индекс молча попал бы в чужую строку.
    """
    texts = [p.text for p in doc.paragraphs]
    for index, text in enumerate(texts):
        if text.strip().startswith(label):
            for following in texts[index + 1:]:
                if following.strip():
                    return _flatten(following)
    raise AssertionError(f"абзац {label!r} не найден")


def _document_text(doc) -> str:
    """Весь текст документа: абзацы и таблицы."""
    parts = [_flatten(p.text) for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(_flatten(cell.text))
    return "\n".join(parts)


def _clause_1_1(doc) -> str:
    return _clause_text(doc, "1.1. Заказчик:")


def _clause_1_2(doc) -> str:
    return _clause_text(doc, "1.2. Перевозчик:")


# ─────────────────────────────────────────────────────────────
# ООО + директор-мужчина
# ─────────────────────────────────────────────────────────────

def test_ooo_client_male_director_full(generator, work_file):
    """
    ООО-заказчик: полные реквизиты, должность и ФИО в родительном падеже.

    Приставки «Общество с ограниченной ответственностью» перед «ООО
    «Заказчик»» нет намеренно: сокращение в наименовании уже называет вид
    лица, и вторая приставка читалась бы как ошибка (см.
    `test_ooo_legal_form_prefix_added` — у КОРОТКОГО наименования она есть).
    """
    doc = _render(generator, work_file("party_ooo_male.docx"))

    assert _clause_1_1(doc) == (
        "ООО «Заказчик», "
        "ИНН 7707654321, КПП 770701001, ОГРН 1027700132195, "
        "именуемое в дальнейшем «Заказчик», "
        "в лице Генерального директора Ахмедова Тимура Артуровича, "
        "действующего на основании Устава."
    )


def test_ooo_client_inn_kpp_ogrn_in_1_1(generator, work_file):
    """Реквизиты ООО стоят в САМОМ п. 1.1, а не только в п. 9."""
    doc = _render(generator, work_file("party_ooo_req.docx"))

    clause = _clause_1_1(doc)
    assert "ИНН 7707654321" in clause
    assert "КПП 770701001" in clause
    assert "ОГРН 1027700132195" in clause


def test_ooo_client_imenuemoe(generator, work_file):
    """У ООО — «именуемое» (средний род), не «именуемый»."""
    doc = _render(generator, work_file("party_ooo_im.docx"))

    clause = _clause_1_1(doc)
    assert "именуемое в дальнейшем" in clause
    assert "именуемый" not in clause
    assert "именуемая" not in clause


def test_ooo_client_no_nominative_case(generator, work_file):
    """Именительного падежа в п. 1.1 больше нет."""
    doc = _render(generator, work_file("party_ooo_case.docx"))

    clause = _clause_1_1(doc)
    assert "в лице Генеральный директор" not in clause
    assert "Ахмедов Тимур Артурович" not in clause


def test_ooo_carrier_male_director(generator, work_file):
    """ООО-перевозчик: реквизиты, падеж должности и ФИО."""
    doc = _render(generator, work_file("party_ooo_carrier.docx"))

    assert _clause_1_2(doc) == (
        "ООО «Перевозчик», "
        "ИНН 7701234567, КПП 770101001, ОГРН 1027700132195, "
        "в лице Генерального директора Петрова Пётра Петровича, "
        "действующего на основании Устава, "
        "именуемое в дальнейшем «Перевозчик»."
    )


# ─────────────────────────────────────────────────────────────
# ООО + директор-женщина
# ─────────────────────────────────────────────────────────────

def test_ooo_client_female_director_full(generator, work_file):
    """Директор-женщина: «Сидоровой Анны Петровны» и «действующей»."""
    doc = _render(
        generator, work_file("party_ooo_f.docx"), customer=CUSTOMER_OOO_FEMALE
    )

    assert _clause_1_1(doc) == (
        "ООО «Василёк», "
        "ИНН 7707654321, КПП 770701001, ОГРН 1027700132195, "
        "именуемое в дальнейшем «Заказчик», "
        "в лице Директора Сидоровой Анны Петровны, "
        "действующей на основании Устава."
    )


def test_ooo_client_female_acting_deystvuyushchey(generator, work_file):
    """Причастие в п. 1.1 согласовано с полом директора."""
    doc = _render(
        generator, work_file("party_ooo_f2.docx"), customer=CUSTOMER_OOO_FEMALE
    )

    clause = _clause_1_1(doc)
    assert "действующей на основании" in clause
    assert "действующего" not in clause
    # «именуемое» остаётся: это род ОРГАНИЗАЦИИ, а не директора.
    assert "именуемое в дальнейшем" in clause


def test_ooo_carrier_female_director(generator, work_file):
    """Перевозчик-ООО с директором-женщиной: падеж и род причастия."""
    doc = _render(
        generator, work_file("party_ooo_cf.docx"), carrier=CARRIER_OOO_FEMALE
    )

    clause = _clause_1_2(doc)
    assert "в лице Директора Сидоровой Анны Петровны, действующей" in clause
    assert "именуемое в дальнейшем «Перевозчик»" in clause


# ─────────────────────────────────────────────────────────────
# ИП-мужчина
# ─────────────────────────────────────────────────────────────

def test_ip_carrier_male(generator, work_file):
    """Перевозчик-ИП (мужчина): действует сам, «именуемый»."""
    doc = _render(
        generator, work_file("party_ip_m.docx"), carrier=CARRIER_IP_MALE,
        carrier_type="ИП без НДС",
    )

    assert _clause_1_2(doc) == (
        "Индивидуальный предприниматель Добросоцкий Алексей Николаевич, "
        "ИНН 770123456780, ОГРНИП 315770000000013, "
        "действующий на основании свидетельства о государственной "
        "регистрации, именуемый в дальнейшем «Перевозчик»."
    )


def test_ip_carrier_male_no_kpp(generator, work_file):
    """У ИП КПП не печатается вовсе — ни в п. 1.2, ни в п. 9."""
    doc = _render(
        generator, work_file("party_ip_mk.docx"), carrier=CARRIER_IP_MALE,
        carrier_type="ИП без НДС",
    )

    clause = _clause_1_2(doc)
    assert "КПП" not in clause
    assert "КПП ," not in _document_text(doc)


def test_ip_carrier_male_ogrnip_label(generator, work_file):
    """У ИП метка — ОГРНИП, а не ОГРН."""
    doc = _render(
        generator, work_file("party_ip_mo.docx"), carrier=CARRIER_IP_MALE,
        carrier_type="ИП без НДС",
    )

    clause = _clause_1_2(doc)
    assert "ОГРНИП 315770000000013" in clause
    assert "ОГРН 315770000000013" not in clause


def test_ip_carrier_male_with_stale_ooo_type(generator, work_file):
    """
    ИП-перевозчик с устаревшим типом «ООО (с НДС)» печатается как ИП.

    Так бывает после загрузки записи из справочника: приставка в
    наименовании и `entity_type` говорят «ИП», а переключатель типа остался
    прежним. Раньше в договоре выходили ОГРН, КПП и «именуемое».
    """
    doc = _render(
        generator, work_file("party_ip_stale.docx"), carrier=CARRIER_IP_MALE,
        carrier_type="ООО (с НДС)",
    )

    clause = _clause_1_2(doc)
    assert "Индивидуальный предприниматель Добросоцкий" in clause
    assert "ОГРНИП" in clause
    # Хвост фразы у ИП: «…, именуемый в дальнейшем «Перевозчик».».
    # Пробел перед закрывающей кавычкой — из самого бланка (так в нём
    # набран этот абзац), в тексте договора он схлопывается.
    assert clause.endswith("именуемый в дальнейшем «Перевозчик».")
    assert "КПП" not in clause
    assert "именуемое" not in clause


# ─────────────────────────────────────────────────────────────
# ИП-женщина
# ─────────────────────────────────────────────────────────────

def test_ip_female_carrier(generator, work_file):
    """Перевозчик-ИП (женщина): «действующая» и «именуемая»."""
    doc = _render(
        generator, work_file("party_ip_f.docx"), carrier=CARRIER_IP_FEMALE,
        carrier_type="ИП без НДС",
    )

    assert _clause_1_2(doc) == (
        "Индивидуальный предприниматель Хейгетян Елена Валентиновна, "
        "ИНН 770123456789, ОГРНИП 315770000000012, "
        "действующая на основании свидетельства о государственной "
        "регистрации, именуемая в дальнейшем «Перевозчик»."
    )


def test_ip_client_female(generator, work_file):
    """Заказчик-ИП (женщина): полные реквизиты, «именуемая»."""
    doc = _render(
        generator, work_file("party_ip_cf.docx"), customer=CUSTOMER_IP_FEMALE
    )

    assert _clause_1_1(doc) == (
        "Индивидуальный предприниматель Хейгетян Елена Валентиновна, "
        "ИНН 770123456789, ОГРНИП 315770000000012, "
        "действующая на основании свидетельства о государственной "
        "регистрации, именуемая в дальнейшем «Заказчик»."
    )


def test_ip_client_female_no_director_block(generator, work_file):
    """У ИП-заказчика нет «в лице», должности директора и Устава."""
    doc = _render(
        generator, work_file("party_ip_cf2.docx"), customer=CUSTOMER_IP_FEMALE
    )

    clause = _clause_1_1(doc)
    assert "в лице" not in clause
    assert "Устава" not in clause
    assert "Генеральный директор" not in clause


def test_ip_client_female_no_akhmedov(generator, work_file):
    """Чужого директора из старой константы бланка в договоре нет."""
    doc = _render(
        generator, work_file("party_ip_cf3.docx"), customer=CUSTOMER_IP_FEMALE
    )

    assert "Ахмедов" not in _document_text(doc)


def test_ip_client_female_no_kpp(generator, work_file):
    """У заказчика-ИП КПП тоже нет — ни «КПП ,», ни пустой строки."""
    doc = _render(
        generator, work_file("party_ip_cf4.docx"), customer=CUSTOMER_IP_FEMALE
    )

    clause = _clause_1_1(doc)
    assert "КПП" not in clause
    assert "КПП ," not in _document_text(doc)


# ─────────────────────────────────────────────────────────────
# Общие проверки бланка
# ─────────────────────────────────────────────────────────────

def test_ooo_legal_form_prefix_added(generator, work_file):
    """Короткое наименование («ООО «Ромашка»») печатается с полной приставкой."""
    customer = dict(CUSTOMER_OOO_MALE, full_name="Ромашка",
                    short_name="ООО «Ромашка»")
    doc = _render(generator, work_file("party_prefix.docx"), customer=customer)

    clause = _clause_1_1(doc)
    assert clause.startswith(
        "Общество с ограниченной ответственностью Ромашка (ООО «Ромашка»), ИНН"
    )


def test_ooo_legal_form_prefix_not_doubled(generator, work_file):
    """Приставка не удваивается, если она уже есть в наименовании."""
    customer = dict(CUSTOMER_OOO_MALE, full_name="ООО «Заказчик»",
                    short_name="ООО «Заказчик»")
    doc = _render(generator, work_file("party_prefix2.docx"), customer=customer)

    clause = _clause_1_1(doc)
    assert clause.count("Общество с ограниченной ответственностью") == 0
    assert clause.startswith("ООО «Заказчик», ИНН")


def test_ip_legal_form_prefix_not_doubled(generator, work_file):
    """ИП-приставка тоже не печатается дважды."""
    doc = _render(
        generator, work_file("party_prefix3.docx"), customer=CUSTOMER_IP_FEMALE
    )

    text = _document_text(doc)
    assert "Индивидуальный предприниматель Индивидуальный предприниматель" \
        not in text


def test_replacement_keys_are_kept(generator):
    """Старые ключи карты замен не удалены — их читает внешний код."""
    replacements = generator._build_replacements_map(
        _payload(CARRIER_OOO, CUSTOMER_OOO_MALE)
    )

    for key in (
        "client_full_name", "client_name", "client_director",
        "client_director_position", "client_director_position_short",
        "client_legal_form", "client_basis", "client_pronoun",
        "client_acting", "client_ogrn_label", "client_name_in_parens",
        "carrier_full_name", "carrier_name", "carrier_director",
        "carrier_director_position", "carrier_director_position_short",
        "carrier_legal_form", "carrier_basis", "carrier_pronoun",
        "carrier_acting", "carrier_ogrn_label", "carrier_name_in_parens",
        "carrier_kpp_line",
    ):
        assert key in replacements, f"пропал ключ {key}"


def test_new_genitive_keys_are_present(generator):
    """Новые ключи падежа заполнены для ООО и для ИП."""
    replacements = generator._build_replacements_map(
        _payload(CARRIER_OOO, CUSTOMER_OOO_MALE)
    )
    assert replacements["client_director_position_genitive"] == \
        "Генерального директора"
    assert replacements["client_director_genitive"] == \
        "Ахмедова Тимура Артуровича"
    assert replacements["client_acting_genitive"] == "действующего"
    assert replacements["client_kpp_line"] == "КПП 770701001"

    female = generator._build_replacements_map(
        _payload(CARRIER_OOO_FEMALE, CUSTOMER_OOO_FEMALE)
    )
    assert female["client_director_position_genitive"] == "Директора"
    assert female["client_director_genitive"] == "Сидоровой Анны Петровны"
    assert female["client_acting_genitive"] == "действующей"


def test_logs_have_no_personal_data(generator, work_file, caplog):
    """В лог не попадают ФИО: только тип, ставка и счётчики."""
    import logging

    with caplog.at_level(logging.DEBUG):
        _render(generator, work_file("party_log.docx"))

    for record in caplog.records:
        message = record.getMessage()
        assert "Ахмедов" not in message
        assert "Петров" not in message
        assert "7707654321" not in message


def test_legal_form_prefix_kills_ooo_prefix_once(generator):
    """
    Приставка вида лица не удваивается ни у ООО, ни у ИП.

    Проверяются сами ключи: `*_legal_form_prefix` пуст, если наименование
    уже начинается с сокращения вида лица («ООО …», «ИП …»), и заполнен,
    если наименование короткое («Ромашка»).
    """
    ooo = generator._build_replacements_map(
        _payload(CARRIER_OOO, CUSTOMER_OOO_MALE)
    )
    assert ooo["client_legal_form_prefix"] == ""
    assert ooo["carrier_legal_form_prefix"] == ""

    short = generator._build_replacements_map(
        _payload(
            dict(CARRIER_OOO, full_name="Перевозчик"),
            dict(CUSTOMER_OOO_MALE, full_name="Ромашка"),
        )
    )
    assert short["client_legal_form_prefix"] == \
        "Общество с ограниченной ответственностью "
    assert short["carrier_legal_form_prefix"] == \
        "Общество с ограниченной ответственностью "

    ip = generator._build_replacements_map(
        _payload(CARRIER_OOO, CUSTOMER_IP_FEMALE)
    )
    # У ИП ветвь п. 1.1 начинается с {{client_legal_form}} — приставка вида
    # лица целиком; суффиксный ключ ей не нужен и остаётся пустым, иначе
    # «Индивидуальный предприниматель» печаталось бы дважды.
    assert ip["client_legal_form"] == "Индивидуальный предприниматель"
    assert ip["client_legal_form_prefix"] == ""
