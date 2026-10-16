#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Инструменты DaData (задание, Часть 2).

Что проверяется
---------------
1. СОДЕРЖАНИЕ. По ИНН возвращаются реквизиты, по названию — список
   вариантов, по адресу — стандартный вид, и всё это с оговорками, когда
   данные разобраны не полностью.
2. БЕЗОПАСНОСТЬ. В DaData не может уйти ничего, кроме публичных данных
   ЕГРЮЛ. Проверка ИНН отсекает всё, что на ИНН не похоже, поэтому ФИО,
   телефон или паспорт водителя физически не становятся запросом.
3. КЭШ. Повторный вопрос за час не тратит запрос тарифа; истёкшая запись
   запрашивается заново.
4. СВЕРКА. compare_with_dadata находит расхождения с ЕГРЮЛ и НЕ меняет
   запись базы.

Сеть не используется: клиент DaData подменяется двойником, который считает
обращения. Живая проверка — отдельным скриптом (tests/_tmp/_p2_live.py).
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import dadata_tools  # noqa: E402
from core.agent.dadata_tools import (  # noqa: E402
    CACHE_TTL_SECONDS,
    cache_size,
    cached,
    check_address_dadata,
    check_inn_dadata,
    clear_cache,
    compare_with_dadata,
    search_company_dadata,
)
from core.agent.tools import AgentContext, build_registry  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Двойник клиента DaData
# ─────────────────────────────────────────────────────────────

#: Карточка организации, которую отдаёт ЕГРЮЛ в тестах.
EGRUL_CARD = {
    "full_name": 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ФАССТРАНС"',
    "short_name": 'ООО "ФАССТРАНС"',
    "inn": "7701234567",
    "kpp": "770101001",
    "ogrn": "1027700132195",
    "legal_address": "г Москва, ул Тестовая, д 1",
    "director_name": "Петров Пётр Петрович",
    "director_position": "Генеральный директор",
    "entity_type": "LEGAL",
    "status": "ACTIVE",
    "phone": "",
    "email": "",
}


class FakeDadata:
    """
    Двойник DadataClient: считает обращения и отдаёт заданные ответы.

    Ни один тест не ходит в сеть: и адрес, и поведение задаются здесь,
    поэтому проверяется именно логика инструментов.
    """

    def __init__(self, card=None, parties=None, address=None,
                 inn_error=None, address_error=None):
        self.card = card if card is not None else dict(EGRUL_CARD)
        self.parties = list(parties or [dict(EGRUL_CARD)])
        self.address = address
        self.inn_error = inn_error
        self.address_error = address_error
        #: Сколько раз спросили реквизиты по ИНН и очистку адреса.
        self.inn_calls = 0
        self.name_calls = 0
        self.address_calls = 0
        #: Последние аргументы обращений — по ним проверяется безопасность.
        self.seen_inn: list = []
        self.seen_name: list = []
        self.seen_address: list = []

    def find_party_by_inn(self, inn):
        """Реквизиты по ИНН."""
        self.inn_calls += 1
        self.seen_inn.append(inn)
        if self.inn_error:
            raise self.inn_error
        return dict(self.card) if self.card else None

    def suggest_parties(self, query, count=5, active_only=True):
        """Поиск организации по названию."""
        self.name_calls += 1
        self.seen_name.append(query)
        return [dict(item) for item in self.parties]

    def clean_address(self, address):
        """Стандартизация адреса."""
        self.address_calls += 1
        self.seen_address.append(address)
        if self.address_error:
            raise self.address_error
        return dict(self.address) if self.address else None


@pytest.fixture(autouse=True)
def clean_cache():
    """Каждый тест начинается с пустого кэша DaData."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def fake(monkeypatch):
    """Подставляет двойника клиента вместо настоящего DadataClient."""
    client = FakeDadata()
    monkeypatch.setattr(dadata_tools, "_client", lambda: client)
    return client


# ─────────────────────────────────────────────────────────────
# 1. check_inn_dadata
# ─────────────────────────────────────────────────────────────

def test_check_inn_returns_requisites(fake):
    """По ИНН возвращаются реквизиты организации."""
    result = check_inn_dadata(inn="7701234567")

    assert result["ok"] is True
    assert result["found"] is True
    data = result["data"]
    assert data["inn"] == "7701234567"
    assert data["ogrn"] == "1027700132195"
    assert data["kpp"] == "770101001"
    assert "ФАССТРАНС" in data["full_name"]
    assert fake.inn_calls == 1


def test_check_inn_normalizes_input(fake):
    """ИНН с пробелами и подписью приводится к цифрам."""
    check_inn_dadata(inn="ИНН 7701 234 567")

    assert fake.seen_inn == ["7701234567"]


@pytest.mark.parametrize("bad", ["", "123", "12345678901", "нет цифр"])
def test_check_inn_rejects_wrong_length(fake, bad):
    """
    Неверный ИНН — понятная ошибка, и запрос НЕ уходит.

    Это же и защита: то, что не похоже на ИНН, в DaData не отправляется.
    """
    result = check_inn_dadata(inn=bad)

    assert result["ok"] is False
    assert fake.inn_calls == 0


def test_check_inn_not_found(fake):
    """Организации нет в ЕГРЮЛ — честный ответ без выдумок."""
    fake.card = None

    result = check_inn_dadata(inn="7701234567")

    assert result["ok"] is True
    assert result["found"] is False
    assert result["data"] == {}


def test_check_inn_reports_missing_key(fake):
    """Нет ключа DaData — модель объясняет это, а не падает."""
    fake.inn_error = ValueError("Ключ DaData не найден.")

    result = check_inn_dadata(inn="7701234567")

    assert result["ok"] is False
    assert "DaData" in result["error"]


def test_check_inn_reports_network_error(fake):
    """Сетевая ошибка превращается в текст для оператора."""
    fake.inn_error = RuntimeError("DaData: нет соединения с dadata.ru.")

    result = check_inn_dadata(inn="7701234567")

    assert result["ok"] is False
    assert "соединения" in result["error"]


# ─────────────────────────────────────────────────────────────
# 2. search_company_dadata
# ─────────────────────────────────────────────────────────────

def test_search_company_returns_variants(fake):
    """По названию возвращается список вариантов с ИНН."""
    fake.parties = [
        {"full_name": 'ООО "РОМАШКА"', "inn": "7707654321",
         "short_name": 'ООО "РОМАШКА"', "kpp": "770701001",
         "ogrn": "1027700132195", "legal_address": "г Москва",
         "director_name": "Иванов И.И.", "entity_type": "LEGAL",
         "status": "ACTIVE"},
        {"full_name": 'АО "РОМАШКА"', "inn": "7709999999",
         "short_name": 'АО "РОМАШКА"', "kpp": "", "ogrn": "",
         "legal_address": "", "director_name": "", "entity_type": "LEGAL",
         "status": "ACTIVE"},
    ]

    result = search_company_dadata(query="Ромашка")

    assert result["ok"] is True
    assert result["count"] == 2
    assert [row["inn"] for row in result["data"]] == ["7707654321", "7709999999"]


def test_search_company_empty_query(fake):
    """Пустое название — ошибка, запрос не уходит."""
    result = search_company_dadata(query="   ")

    assert result["ok"] is False
    assert fake.name_calls == 0


def test_search_company_nothing_found(fake):
    """Ничего не найдено — пустой список и пояснение."""
    fake.parties = []

    result = search_company_dadata(query="неттакойфирмы")

    assert result["ok"] is True
    assert result["count"] == 0
    assert result["data"] == []


# ─────────────────────────────────────────────────────────────
# 3. check_address_dadata
# ─────────────────────────────────────────────────────────────

def test_check_address_returns_standard_form(fake):
    """Адрес приводится к стандартному виду."""
    fake.address = {
        "value": "Ставропольский край, г Пятигорск, пл Ленина, д 5",
        "city": "Пятигорск", "street": "пл Ленина", "house": "5",
        "flat": "", "region": "Ставропольский край", "postal_code": "357500",
        "fias_id": "829b6c1f", "geo_lat": "44.05", "geo_lon": "43.06",
        "qc_geo": 2,
    }

    result = check_address_dadata(address="Пятигорск Ленина 5")

    assert result["ok"] is True
    assert result["data"]["city"] == "Пятигорск"
    assert result["data"]["street"] == "пл Ленина"
    assert result["unchanged"] is False


def test_check_address_warns_about_partial_parse(fake):
    """
    Частично разобранный адрес сопровождается предупреждением.

    DaData на «г москва ул тестовая д 1» уверенно отдаёт только город;
    показать это как готовый адрес — значит подсунуть оператору обрезанные
    данные вместо честного «улица не распознана».
    """
    fake.address = {"value": "г Москва", "city": "Москва", "street": "",
                    "house": "", "qc_geo": 4}

    result = check_address_dadata(address="г москва ул тестовая д 1")

    assert result["ok"] is True
    assert result["data"]["value"] == "г Москва"
    assert any("улица" in text for text in result["warnings"])
    assert any("дом" in text for text in result["warnings"])


def test_check_address_marks_unchanged(fake):
    """Если адрес уже в стандартном виде, инструмент это отмечает."""
    fake.address = {"value": "г Москва, ул Тестовая, д 1", "city": "Москва",
                    "street": "ул Тестовая", "house": "1", "qc_geo": 0}

    result = check_address_dadata(address="г Москва, ул Тестовая, д 1")

    assert result["unchanged"] is True
    assert result["warnings"] == []


def test_check_address_without_secret_explains_itself(fake):
    """Нет секретного ключа — понятная инструкция вместо исключения."""
    fake.address_error = RuntimeError(
        "Ключ DaData не найден. Запустите set_dadata_key.py для его добавления."
    )

    result = check_address_dadata(address="Мурманск, Складская 7")

    assert result["ok"] is False
    assert "DaData" in result["error"]


def test_check_address_empty_query(fake):
    """Пустой адрес — ошибка, запрос не уходит."""
    result = check_address_dadata(address="")

    assert result["ok"] is False
    assert fake.address_calls == 0


# ─────────────────────────────────────────────────────────────
# 4. compare_with_dadata
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Временная база с одним перевозчиком."""
    import db.database as database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "contracts.db"))
    monkeypatch.setattr(database, "restrict_to_current_user", lambda *a, **k: True)
    monkeypatch.setattr(database, "backup_database", lambda *a, **k: None)
    database.init_database()
    carrier_id = database.save_organization({
        "full_name": "ООО «ФассТранс»",
        "inn": "7701234567",
        "kpp": "770101001",
        "ogrn": "1027700132195",
        "legal_address": "г Москва, ул Тестовая, д 1",
    }, is_carrier=True)
    return database, carrier_id


def _find_carrier_id(database):
    """ID единственного перевозчика во временной базе."""
    from db.crud.organizations import get_all_organizations

    rows = get_all_organizations(is_carrier=True)
    assert rows, "перевозчик не сохранён"
    return int(rows[0]["id"])


def test_compare_finds_no_differences(fake, seeded_db):
    """
    Совпадающие значения расхождением не считаются.

    Карточка ЕГРЮЛ повторяет то, что лежит в базе, но записана иначе:
    кавычки «ёлочки» против прямых, другой регистр, «ё» против «е». Это
    одно и то же название, и ругаться на такое различие бессмысленно —
    ровно ради этого сравнение и нормализует значения.
    """
    database, _saved = seeded_db
    carrier_id = _find_carrier_id(database)
    fake.card = dict(
        EGRUL_CARD,
        full_name="ООО «ФАССТРАНС»",      # другие кавычки и регистр
        short_name="",
        legal_address="г Москва, ул Тестовая, д 1",
        director_name="",                  # в базе этого поля тоже нет
        director_position="",
    )

    result = compare_with_dadata(entity_type="carrier", entity_id=carrier_id)

    assert result["ok"] is True
    assert result["data"]["difference_count"] == 0, (
        "совпадающие значения отмечены как расхождения: "
        f"{result['data']['differences']}"
    )
    assert "inn" in result["data"]["matched"]
    assert "full_name" in result["data"]["matched"]
    assert result["message"] == "расхождений нет"


def test_compare_finds_differences(fake, seeded_db):
    """Расхождение по адресу попадает в отчёт вместе с данными ЕГРЮЛ."""
    database, _saved = seeded_db
    carrier_id = _find_carrier_id(database)
    fake.card = dict(EGRUL_CARD, legal_address="г Москва, ул Другая, д 9")

    result = compare_with_dadata(entity_type="carrier", entity_id=carrier_id)

    assert result["ok"] is True
    fields = {item["field"] for item in result["data"]["differences"]}
    assert "legal_address" in fields
    item = next(row for row in result["data"]["differences"]
                if row["field"] == "legal_address")
    assert item["in_egrul"] == "г Москва, ул Другая, д 9"
    assert item["in_base"] == "г Москва, ул Тестовая, д 1"


def test_compare_reports_empty_base_field(fake, seeded_db):
    """Пустое поле в базе — тоже расхождение, и оно объяснено."""
    database, _saved = seeded_db
    carrier_id = _find_carrier_id(database)
    # В базе у этого перевозчика нет короткого наименования, а в ЕГРЮЛ есть.
    fake.card = dict(EGRUL_CARD, legal_address="г Москва, ул Тестовая, д 1",
                     short_name='ООО "ФАССТРАНС"')
    from db.crud.organizations import update_organization, load_organization

    record = load_organization(carrier_id, is_carrier=True)
    record.pop("id", None)
    record["short_name"] = ""
    update_organization(carrier_id, record, is_carrier=True)

    result = compare_with_dadata(entity_type="carrier", entity_id=carrier_id)

    empty = [row for row in result["data"]["differences"]
             if row.get("reason") == "поле в базе пустое"]
    assert empty, "пустое поле в базе не отмечено"
    assert any(row["field"] == "short_name" for row in empty)


def test_compare_resolves_record_by_name(fake, seeded_db):
    """
    Запись можно указать НАЗВАНИЕМ, а не ID.

    ID оператор не знает: «сверь Ромашку» — нормальный запрос. Искать
    запись должен инструмент, иначе модель отвечает «дай мне ID», и работа
    не делается.
    """
    _database, _saved = seeded_db
    fake.card = dict(EGRUL_CARD, legal_address="г Москва, ул Тестовая, д 1",
                     short_name="", director_name="", director_position="")
    fake.card["full_name"] = "ООО «ФассТранс»"

    result = compare_with_dadata(entity_type="carrier", query="ФассТранс")

    assert result["ok"] is True, result
    assert result["data"]["entity_id"] > 0
    assert "ФассТранс" in result["data"]["title"]


def test_compare_without_id_and_without_name_explains(fake):
    """Ни ID, ни названия — понятная подсказка, что передать."""
    result = compare_with_dadata(entity_type="carrier")

    assert result["ok"] is False
    assert "query" in result["hint"]
    assert fake.inn_calls == 0


def test_compare_unknown_name_explains(fake, seeded_db):
    """Название не нашлось в базе — сверять нечего, и это сказано словами."""
    _database, _saved = seeded_db

    result = compare_with_dadata(entity_type="carrier", query="НеттакойФирмы")

    assert result["ok"] is False
    assert "не нашлось" in result["error"]
    assert fake.inn_calls == 0


def test_compare_does_not_change_the_database(fake, seeded_db):
    """Сверка только читает: запись базы остаётся прежней."""
    database, _saved = seeded_db
    carrier_id = _find_carrier_id(database)
    from db.crud.organizations import load_organization

    before = load_organization(carrier_id, is_carrier=True)
    fake.card = dict(EGRUL_CARD, legal_address="г Москва, ул Другая, д 9")

    compare_with_dadata(entity_type="carrier", entity_id=carrier_id)

    assert load_organization(carrier_id, is_carrier=True) == before


def test_compare_rejects_driver_and_vehicle(fake, seeded_db):
    """
    Сверять можно только организации.

    У водителя нет ЕГРЮЛ, а его персональные данные в DaData отправлять
    нельзя — поэтому инструмент отказывается, а не «пробует».
    """
    for kind in ("driver", "vehicle"):
        result = compare_with_dadata(entity_type=kind, entity_id=1)
        assert result["ok"] is False
        assert "carrier" in result["error"]
    assert fake.inn_calls == 0


def test_compare_without_inn_in_record(fake, tmp_path, monkeypatch):
    """Запись без ИНН сверять не с чем — понятный отказ."""
    import db.database as database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "empty.db"))
    monkeypatch.setattr(database, "restrict_to_current_user", lambda *a, **k: True)
    monkeypatch.setattr(database, "backup_database", lambda *a, **k: None)
    database.init_database()
    carrier_id = database.save_organization(
        {"full_name": "ООО «Без ИНН»"}, is_carrier=True
    )

    result = compare_with_dadata(entity_type="carrier", entity_id=carrier_id)

    assert result["ok"] is False
    assert "ИНН" in result["error"]
    assert fake.inn_calls == 0


def test_compare_warns_about_liquidated_status(fake, seeded_db):
    """Ликвидированная организация — предупреждение в ответе."""
    database, _saved = seeded_db
    carrier_id = _find_carrier_id(database)
    fake.card = dict(EGRUL_CARD, status="LIQUIDATED")

    result = compare_with_dadata(entity_type="carrier", entity_id=carrier_id)

    assert result["status"] == "LIQUIDATED"
    assert any("LIQUIDATED" in text for text in result["warnings"])


# ─────────────────────────────────────────────────────────────
# 5. Кэш на час
# ─────────────────────────────────────────────────────────────

def test_repeated_request_uses_cache(fake):
    """Повторный вопрос за час не тратит запрос тарифа."""
    check_inn_dadata(inn="7701234567")
    check_inn_dadata(inn="7701234567")
    check_inn_dadata(inn="7701234567")

    assert fake.inn_calls == 1, "кэш не сработал: ушло больше одного запроса"
    assert cache_size() == 1


def test_cache_separates_different_inn(fake):
    """Разные ИНН кэшируются раздельно."""
    check_inn_dadata(inn="7701234567")
    check_inn_dadata(inn="7707654321")

    assert fake.inn_calls == 2


def test_expired_cache_asks_again(fake, monkeypatch):
    """
    Истёкшая запись запрашивается заново.

    Время подменяется: ждать час в тесте нельзя, а поведение проверить
    нужно.
    """
    moment = {"now": 1000.0}
    monkeypatch.setattr(dadata_tools.time, "monotonic", lambda: moment["now"])

    check_inn_dadata(inn="7701234567")
    assert fake.inn_calls == 1

    # Через полчаса — ещё из кэша.
    moment["now"] += CACHE_TTL_SECONDS / 2
    check_inn_dadata(inn="7701234567")
    assert fake.inn_calls == 1

    # Через час с небольшим — запись протухла.
    moment["now"] += CACHE_TTL_SECONDS
    check_inn_dadata(inn="7701234567")
    assert fake.inn_calls == 2


def test_cache_does_not_grow_forever(fake, monkeypatch):
    """Кэш ограничен по размеру: долгая сессия не съест память."""
    monkeypatch.setattr(dadata_tools, "CACHE_MAX_ENTRIES", 3)

    for index in range(6):
        cached(f"key:{index}", lambda index=index: index)

    assert cache_size() <= 3


def test_cached_returns_producer_value():
    """Кэш возвращает именно то, что вернул источник."""
    calls = []

    def producer():
        """Считает обращения и отдаёт словарь."""
        calls.append(1)
        return {"answer": 42}

    first = cached("probe", producer)
    second = cached("probe", producer)

    assert first == second == {"answer": 42}
    assert len(calls) == 1


# ─────────────────────────────────────────────────────────────
# 6. Инструменты зарегистрированы
# ─────────────────────────────────────────────────────────────

DADATA_TOOLS = (
    "check_inn_dadata", "search_company_dadata",
    "check_address_dadata", "compare_with_dadata",
)


@pytest.mark.parametrize("name", DADATA_TOOLS)
def test_dadata_tool_is_registered(name):
    """Все четыре инструмента части есть в реестре агента."""
    registry = build_registry(AgentContext())

    assert registry.get(name) is not None, f"инструмент {name} не зарегистрирован"


@pytest.mark.parametrize("name", DADATA_TOOLS)
def test_dadata_tool_needs_no_confirmation(name):
    """DaData ничего не меняет — подтверждение не нужно."""
    registry = build_registry(AgentContext())

    assert registry.get(name).requires_confirmation is False


def test_dadata_arguments_are_public_only():
    """
    Инструменты принимают только публичные данные.

    Проверяется по схеме аргументов: у запроса к ЕГРЮЛ нет и не может быть
    аргументов вроде «паспорт», «телефон», «фио» — иначе персональные
    данные водителей могли бы уйти в DaData по инициативе модели.
    """
    registry = build_registry(AgentContext())
    forbidden = ("passport", "phone", "driver", "license", "birth", "fio")

    for name in DADATA_TOOLS:
        tool = registry.get(name)
        properties = tool.parameters.get("properties") or {}
        for argument in properties:
            assert not any(word in argument.casefold() for word in forbidden), (
                f"{name}: аргумент {argument!r} выглядит как персональные данные"
            )
