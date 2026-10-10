#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Нормализация ДОЛЖНОСТИ: капс из внешних источников → обычный регистр.
(ШАГ «Ставки НДС + нормализация DaData + формулировки», с правкой отката.)

Зачем
-----
DaData отдаёт `management.post` капсом («ГЕНЕРАЛЬНЫЙ ДИРЕКТОР») — это их
формат хранения поля, а не общепринятый. В документах принято «Директор»,
«Генеральный директор». Капс чинится на входе (DaData, справочник), а
последний барьер — `genitive_position`: если капс проскочил из справочника,
в договор всё равно уйдёт «Директора», а не «ДИРЕКТОРА».

Правила (общие для всех входов, `core/text_normalize.py`):
  * «ДИРЕКТОР» → «Директор», «ГЕНЕРАЛЬНЫЙ ДИРЕКТОР» → «Генеральный директор»;
  * сокращения («ИП», «ООО», «АО») не трогаются;
  * «Директор» и «директор» остаются как есть.

Что НЕ нормализуется
--------------------
НАИМЕНОВАНИЯ организаций (`full_name`, `short_name`): для них авторитетный
источник — DaData (данные ЕГРЮЛ). «ООО "АВАТЭК"» капсом — это запись реестра,
и «ООО "Аватэк"» было бы уже другим наименованием. По той же причине не
трогаются `director_name`, адреса и название банка. Проверки «не меняется» —
ниже: без них правило тихо разъехалось бы обратно.

Данные синтетические, ПДн нет. Сеть не трогается: клиент DaData — двойник.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.contracts.ru_morphology import genitive_position  # noqa: E402
from core.text_normalize import (  # noqa: E402
    normalize_caps_phrase,
    normalize_organization_fields,
    normalize_position,
)


# ─────────────────────────────────────────────────────────────
# 1. Нормализация должности (Часть B, п. 1)
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("post,expected", [
    ("ДИРЕКТОР", "Директор"),
    ("ГЕНЕРАЛЬНЫЙ ДИРЕКТОР", "Генеральный директор"),
    ("ГЕНЕРАЛЬНЫЙ ДИРЕКТОР ", "Генеральный директор"),
    ("  ДИРЕКТОР  ", "Директор"),
    ("ДИРЕКТОР ПО РАЗВИТИЮ", "Директор по развитию"),
    ("ЗАМЕСТИТЕЛЬ ГЕНЕРАЛЬНОГО ДИРЕКТОРА ПО ЭКОНОМИКЕ И ФИНАНСАМ",
     "Заместитель генерального директора по экономике и финансам"),
    ("ДИРЕКТОР-РАСПОРЯДИТЕЛЬ", "Директор-распорядитель"),
])
def test_caps_position_is_normalized(post, expected):
    """Капс должности → «Первая заглавная, остальные строчные»."""
    assert normalize_position(post) == expected


@pytest.mark.parametrize("post", ["ИП", "ООО", "АО", "ГУП"])
def test_abbreviation_position_is_kept(post):
    """Сокращения из заглавных букв не трогаются: у них капс — норма."""
    assert normalize_position(post) == post


@pytest.mark.parametrize("post", [
    "Директор", "директор", "Генеральный директор", "Индивидуальный предприниматель",
])
def test_usual_case_position_is_kept(post):
    """Значение в обычном регистре остаётся как есть (и «директор» тоже)."""
    assert normalize_position(post) == post


@pytest.mark.parametrize("post", ["", "   ", None])
def test_empty_position_stays_empty(post):
    """Пустое значение → пустая строка, без «None»."""
    assert normalize_position(post) == ""


def test_abbreviation_does_not_take_the_capital():
    """
    Аббревиатура не перехватывает заглавную у первого слова.

    «ООО РОМАШКА» — это ОПФ плюс название, и заглавная нужна названию;
    иначе в договоре вышло бы «ООО ромашка». Правило общее: им пользуется
    нормализация должности, а наименования полей организации по умолчанию
    не нормализуются вовсе (проверено ниже).
    """
    assert normalize_caps_phrase("ООО РОМАШКА") == "ООО Ромашка"
    assert normalize_caps_phrase("ПАО СБЕРБАНК") == "ПАО Сбербанк"


def test_quoted_name_gets_its_own_capital():
    """У названия в кавычках своя заглавная: «ООО «РОМАШКА»» → «ООО «Ромашка»»."""
    assert normalize_caps_phrase("ООО «РОМАШКА»") == "ООО «Ромашка»"
    assert normalize_caps_phrase('ООО "РОМАШКА"') == 'ООО "Ромашка"'
    assert normalize_caps_phrase(
        "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ «РОМАШКА»"
    ) == "Общество с ограниченной ответственностью «Ромашка»"


# ─────────────────────────────────────────────────────────────
# 2. Клиент DaData отдаёт нормализованную должность (Часть B, п. 1)
# ─────────────────────────────────────────────────────────────

def test_dadata_normalizes_position(monkeypatch):
    """
    `_parse_suggestion` отдаёт «Генеральный директор», а не капс DaData.

    Ответ API — двойник: сеть не трогается, ключ не нужен.
    """
    from core import dadata_client
    from core.dadata_client import DadataClient

    payload = {
        "suggestions": [{
            "value": "ООО «РОМАШКА»",
            "data": {
                "inn": "7701234567",
                "type": "LEGAL",
                "name": {"full_with_opf": "ООО «РОМАШКА»",
                         "short_with_opf": "ООО «РОМАШКА»"},
                "management": {"name": "Петров Пётр Петрович",
                               "post": "ГЕНЕРАЛЬНЫЙ ДИРЕКТОР"},
            },
        }],
    }

    class StubResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return payload

    monkeypatch.setattr(
        dadata_client.requests, "post",
        lambda *args, **kwargs: StubResponse(),
    )

    data = DadataClient(api_key="test-key").find_party_by_inn("7701234567")

    assert data is not None
    assert data["director_position"] == "Генеральный директор"
    # ФИО и наименование не искажены: капс в них — вопрос другого поля.
    assert data["director_name"] == "Петров Пётр Петрович"
    assert data["inn"] == "7701234567"


def test_dadata_keeps_ip_position(monkeypatch):
    """У ИП должность «Индивидуальный предприниматель» (не капс, не трогается)."""
    from core import dadata_client
    from core.dadata_client import DadataClient

    payload = {
        "suggestions": [{
            "value": "ИП Иванов Иван Иванович",
            "data": {
                "inn": "770123456789",
                "type": "INDIVIDUAL",
                "fio": {"surname": "Иванов", "name": "Иван",
                        "patronymic": "Иванович"},
            },
        }],
    }

    class StubResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return payload

    monkeypatch.setattr(
        dadata_client.requests, "post",
        lambda *args, **kwargs: StubResponse(),
    )

    data = DadataClient(api_key="test-key").find_party_by_inn("770123456789")

    assert data["director_position"] == "Индивидуальный предприниматель"
    assert data["kpp"] == ""


# ─────────────────────────────────────────────────────────────
# 3. Справочник: нормализация при сохранении (Часть B, п. 2)
# ─────────────────────────────────────────────────────────────

def test_normalize_organization_fields_copies_dict():
    """Правится только должность, исходный словарь не меняется."""
    org = {
        "full_name": "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ «РОМАШКА»",
        "short_name": "ООО «РОМАШКА»",
        "director_position": "ДИРЕКТОР",
        "director_name": "Петров Пётр Петрович",
        "inn": "7701234567",
    }

    fixed = normalize_organization_fields(org)

    assert fixed["director_position"] == "Директор"
    # Наименования — как из источника: для них авторитетен DaData (ЕГРЮЛ).
    assert fixed["full_name"] == \
        "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ «РОМАШКА»"
    assert fixed["short_name"] == "ООО «РОМАШКА»"
    # ФИО — не наше поле: регистр в именах значим.
    assert fixed["director_name"] == "Петров Пётр Петрович"
    assert fixed["inn"] == "7701234567"
    # Исходный словарь не тронут (форма продолжает работать со своими данными).
    assert org["director_position"] == "ДИРЕКТОР"


def test_normalization_touches_exactly_one_field():
    """
    Меняется ровно одно поле — `director_position`.

    Проверка «на всё сразу»: если однажды в нормализацию снова попадёт
    наименование, адрес или банк, тест это покажет, а не промолчит.
    """
    org = {
        "full_name": 'ООО "АВАТЭК"',
        "short_name": 'ООО "АВАТЭК"',
        "director_name": "ПЕТРОВ ПЁТР ПЕТРОВИЧ",
        "director_position": "ГЕНЕРАЛЬНЫЙ ДИРЕКТОР",
        "legal_address": "Г МОСКВА, УЛ ТЕСТОВАЯ, Д 1",
        "actual_address": "Г МОСКВА, УЛ ТЕСТОВАЯ, Д 1",
        "bank_name": "ПАО СБЕРБАНК",
        "inn": "7701234567",
    }

    changed = [
        key for key, value in normalize_organization_fields(org).items()
        if value != org[key]
    ]

    assert changed == ["director_position"]


@pytest.mark.parametrize("name", [
    'ООО "АВАТЭК"',
    'АО "МИДАС"',
    'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "АВТОТРАНС"',
    "ООО «АВАТЭК»",
    "ООО «Ромашка»",
])
def test_organization_name_is_not_normalized(name):
    """
    Наименование не меняется — ни полное, ни сокращённое.

    «ООО "АВАТЭК"» капсом — это запись ЕГРЮЛ, которую отдаёт DaData;
    «ООО "Аватэк"» было бы уже другим наименованием.
    """
    fixed = normalize_organization_fields(
        {"full_name": name, "short_name": name}
    )

    assert fixed["full_name"] == name
    assert fixed["short_name"] == name


@pytest.mark.parametrize("field,value", [
    ("director_name", "ПЕТРОВ ПЁТР ПЕТРОВИЧ"),
    ("legal_address", "Г МОСКВА, УЛ ТЕСТОВАЯ, Д 1"),
    ("actual_address", "Г МОСКВА, УЛ ТЕСТОВАЯ, Д 1"),
    ("bank_name", "ПАО СБЕРБАНК"),
])
def test_other_organization_fields_are_not_normalized(field, value):
    """Остальные текстовые поля организации остаются как пришли."""
    assert normalize_organization_fields({field: value})[field] == value


def test_normalize_organization_fields_does_not_add_missing_keys():
    """Поля, которых нет, не появляются: справочник принимает разные наборы."""
    fixed = normalize_organization_fields({"policy_holder": "ООО «РОМАШКА»"})
    assert set(fixed) == {"policy_holder"}


def test_save_organization_normalizes_position_only(isolated_db):
    """
    `save_organization` нормализует должность и НЕ трогает наименование.

    Капс наименования — данные реестра, он и должен лежать в базе как есть.
    """
    org_id = isolated_db.save_organization({
        "full_name": "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ «РОМАШКА»",
        "short_name": "ООО «РОМАШКА»",
        "inn": "7701234567",
        "director_name": "Петров Пётр Петрович",
        "director_position": "ДИРЕКТОР",
    }, is_carrier=True)

    record = isolated_db.load_organization(org_id, is_carrier=True)
    assert record["director_position"] == "Директор"
    assert record["full_name"] == \
        "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ «РОМАШКА»"
    assert record["short_name"] == "ООО «РОМАШКА»"
    assert record["director_name"] == "Петров Пётр Петрович"


def test_update_organization_normalizes_position_only(isolated_db):
    """`update_organization` нормализует так же, как сохранение."""
    org_id = isolated_db.save_organization({
        "full_name": 'ООО "АВАТЭК"',
        "short_name": 'ООО "АВАТЭК"',
        "inn": "7701234567",
        "director_position": "Директор",
    }, is_carrier=False)

    assert isolated_db.update_organization(org_id, {
        "full_name": 'ООО "АВАТЭК"',
        "short_name": 'ООО "АВАТЭК"',
        "inn": "7701234567",
        "director_position": "ГЕНЕРАЛЬНЫЙ ДИРЕКТОР",
    }, is_carrier=False)

    record = isolated_db.load_organization(org_id, is_carrier=False)
    assert record["director_position"] == "Генеральный директор"
    assert record["full_name"] == 'ООО "АВАТЭК"'
    assert record["short_name"] == 'ООО "АВАТЭК"'


def test_saved_position_is_found_by_search(isolated_db):
    """Индекс FTS5 получает то же значение, что таблица: поиск находит запись."""
    isolated_db.save_organization({
        "full_name": "ООО «РОМАШКА»",
        "short_name": "ООО «РОМАШКА»",
        "inn": "7701234567",
        "director_position": "ДИРЕКТОР",
    }, is_carrier=True)

    found = isolated_db.search_organizations("Ромашка", is_carrier=True)
    assert len(found) == 1
    assert found[0]["director_position"] == "Директор"
    # Наименование в базе — как пришло (капсом), и поиск его всё равно
    # находит: FTS5 по кириллице не различает регистр.
    assert found[0]["full_name"] == "ООО «РОМАШКА»"
    assert isolated_db.search_organizations("ромашка", is_carrier=True)


# ─────────────────────────────────────────────────────────────
# 4. Последний барьер: склонение должности (Часть B, п. 4)
# ─────────────────────────────────────────────────────────────

def test_genitive_position_normalizes_caps():
    """
    Капс, проскочивший из справочника, не попадает в договор.

    Это последний барьер: даже если нормализация на входе не сработала,
    в бланк уйдёт «Директора», а не «ДИРЕКТОРА».
    """
    assert genitive_position("ДИРЕКТОР") == "Директора"
    assert genitive_position("ГЕНЕРАЛЬНЫЙ ДИРЕКТОР") == "Генерального директора"
    assert genitive_position("ДИРЕКТОР ПО РАЗВИТИЮ") == "Директор по развитию"


def test_genitive_position_keeps_abbreviations():
    """Сокращения не склоняются и не нормализуются: «ИП» остаётся «ИП»."""
    assert genitive_position("ИП") == "ИП"
    assert genitive_position("ООО") == "ООО"
    assert genitive_position("Заместитель ИП") == "Заместитель ИП"


def test_genitive_position_usual_case_unchanged():
    """Обычный регистр склоняется как раньше (нормализация его не трогает)."""
    assert genitive_position("Директор") == "Директора"
    assert genitive_position("Генеральный директор") == "Генерального директора"
