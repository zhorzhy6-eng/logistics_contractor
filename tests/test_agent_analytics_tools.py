#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Аналитика Максимилиана (задание, часть 3, тесты).

Что проверяется
---------------
1. **Состав реестра.** Пять инструментов аналитики зарегистрированы, у
   каждого есть описание и схема, подтверждения они не требуют (только
   чтение).
2. **Сводки дают готовые числа.** sum / avg / max / min / count по
   договорам, водителям, машинам — с фильтром по контрагенту и периоду.
3. **Периоды понимаются по-русски.** «октябрь», «октябрь 2026», «3 месяца»,
   «2026», «с 01.09.2026 по 30.09.2026» — и «сегодня» подменяется, чтобы
   результат не зависел от дня прогона.
4. **Сравнение периодов.** Разница считается в числах и процентах,
   разбивка по контрагентам приходит отдельными группами.
5. **Топы.** Топ-N по сумме с ограничением и общим числом групп.
6. **Количество по условиям.** «Сколько водителей с истекающим ВУ», «без
   телефона», «без VIN» — готовые фильтры и фильтры из модели.
7. **Качество данных.** Пустые поля, дубликаты, просроченные сроки.
8. **Безопасность.** Имя таблицы, колонки и операции берутся из белых
   списков: SQL от модели не проходит, значения уходят параметрами.
9. **Ошибки не роняют агента.** Неизвестная сущность, поле, операция и
   период дают ``ok: False`` с понятным текстом.

База — изолированная (фикстура ``isolated_db``): рабочая contracts.db не
читается и не пишется.
"""

import os
import sys
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import analytics_tools  # noqa: E402
from core.agent.tools import AgentContext, build_registry  # noqa: E402
from db.connection import get_connection  # noqa: E402
from db.crud import analytics  # noqa: E402

#: «Сегодня» для тестов: октябрь 2026 — тот же месяц, что и в примерах
#: задания («сравни октябрь с сентябрём», «топ-5 за октябрь»).
TODAY = date(2026, 10, 15)

#: Данные: два перевозчика, заказчик, три водителя, шесть договоров
#: (сентябрь — 1, октябрь — 3, ноябрь — 2) и машины с VIN и без.
CARRIERS = (
    ("ООО «Ромашка»", "7701234567", "770101001", "Москва, Ленина 1", "Иванов И.И."),
    ("ООО «Василёк»", "", "", "", ""),
)
CONTRACTS = (
    # номер, дата, начало, конец, маршрут, без НДС, с НДС, перевозчик
    ("1", "2026-09-05", "2026-09-05", "2026-09-20", "Москва — Пермь", 100000, 120000, 0),
    ("2", "2026-10-03", "2026-10-03", "2026-10-25", "Пермь — Казань", 200000, 240000, 0),
    ("3", "2026-10-11", "2026-10-11", "2026-11-30", "Казань — Уфа", 90000, 108000, 1),
    ("4", "2026-10-14", "2026-10-14", "2026-12-31", "Уфа — Омск", 10000, 12000, 1),
    ("5", "2026-11-02", "2026-11-02", "2026-11-20", "Омск — Тюмень", 50000, 60000, 1),
    ("6", "2026-11-20", "2026-11-20", "2026-12-10", "Тюмень — Курган", 70000, 84000, 0),
)


@pytest.fixture
def analytics_db(isolated_db):
    """Изолированная база с данными для аналитики."""
    conn = get_connection()
    cursor = conn.cursor()

    for name, inn, kpp, address, director in CARRIERS:
        cursor.execute(
            "INSERT INTO carriers (full_name, inn, kpp, legal_address,"
            " director_name) VALUES (?, ?, ?, ?, ?)",
            (name, inn, kpp, address, director),
        )
    cursor.execute(
        "INSERT INTO customers (full_name, inn, kpp, legal_address,"
        " director_name) VALUES ('ООО «Заказчик»', '7712345678', '771201001',"
        " 'Тверь, Мира 5', 'Петров П.П.')"
    )
    customer_id = cursor.lastrowid

    drivers = (
        ("Иванов Иван Иванович", "2815 634512", "+79001112233", "2026-10-20", 1),
        ("Петров Пётр Петрович", "2815 634512", "", "2025-01-01", 1),
        ("Сидоров Сидор Сидорович", "", "", "", None),
    )
    driver_id = None
    for name, passport, phone, expiry, carrier in drivers:
        cursor.execute(
            "INSERT INTO drivers (full_name, passport_number, phone,"
            " license_expiry_date, default_carrier_id) VALUES (?, ?, ?, ?, ?)",
            (name, passport, phone, expiry, carrier),
        )
        if driver_id is None:
            driver_id = cursor.lastrowid

    for number, cdate, start, end, route, without, with_vat, carrier in CONTRACTS:
        cursor.execute(
            "INSERT INTO contracts (contract_number, contract_date, start_date,"
            " end_date, route, price_without_vat, price_with_vat, currency,"
            " driver_id, customer_id, carrier_id, prepayment_amount)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'RUB', ?, ?, ?, 0)",
            (number, cdate, start, end, route, without, with_vat,
             driver_id, customer_id, carrier + 1),
        )
        contract_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO vehicles (carrier_id, contract_id, vin, brand_model,"
            " plate_number, year) VALUES (?, ?, ?, ?, ?, ?)",
            (carrier + 1, contract_id, "XTA21150053946127", "JETROT X70",
             "А123ВС77", 2011),
        )
        cursor.execute(
            "INSERT INTO vehicles (carrier_id, contract_id, vin, brand_model)"
            " VALUES (?, ?, '', 'ГАЗель')",
            (carrier + 1, contract_id),
        )

    conn.commit()
    conn.close()
    return isolated_db


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    """«Сегодня» фиксировано: результат не зависит от дня прогона."""
    monkeypatch.setattr(analytics, "_today", lambda: TODAY)


# ─────────────────────────────────────────────────────────────
# 1. Реестр инструментов
# ─────────────────────────────────────────────────────────────

ANALYTICS_TOOLS = (
    "aggregate_contracts", "compare_periods", "top_contractors",
    "count_records", "check_data_quality",
)


def test_analytics_tools_are_registered():
    """Пять инструментов аналитики есть в реестре агента."""
    registry = build_registry(AgentContext())
    for name in ANALYTICS_TOOLS:
        tool = registry.get(name)
        assert tool is not None, f"{name} не зарегистрирован"
        assert tool.description.strip(), f"{name}: пустое описание"
        assert tool.parameters["type"] == "object"
        assert tool.parameters.get("properties") is not None


def test_analytics_tools_are_read_only():
    """Аналитика ничего не меняет — подтверждения не требует."""
    registry = build_registry(AgentContext())
    for name in ANALYTICS_TOOLS:
        assert registry.get(name).requires_confirmation is False, name


def test_tool_specs_match_registry():
    """Описания из analytics_tools и реестр не расходятся."""
    registry = build_registry(AgentContext())
    for spec in analytics_tools.tool_specs():
        tool = registry.get(spec["name"])
        assert tool is not None
        assert tool.description == spec["description"]
        assert tool.summary == spec["summary"]


# ─────────────────────────────────────────────────────────────
# 2. Сводки (aggregate_contracts)
# ─────────────────────────────────────────────────────────────

def test_aggregate_sum_all_contracts(analytics_db):
    """Сумма договоров за всё время — готовое число."""
    result = analytics_tools.aggregate_contracts(operation="sum")
    assert result["ok"] is True
    assert result["data"]["value"] == 624000
    assert result["data"]["operation"] == "sum"
    assert "сумма" in result["data"]["text"].casefold()


def test_aggregate_sum_for_counterparty(analytics_db):
    """Сумма по контрагенту: «с Ромашкой» — только её договоры."""
    result = analytics_tools.aggregate_contracts(
        counterparty="Ромашка", operation="sum"
    )
    assert result["ok"] is True
    assert result["data"]["value"] == 120000 + 240000 + 84000


def test_aggregate_average_for_period(analytics_db):
    """Средняя сумма за октябрь — по трём договорам октября."""
    result = analytics_tools.aggregate_contracts(
        period="октябрь", operation="avg"
    )
    assert result["ok"] is True
    assert result["data"]["value"] == (240000 + 108000 + 12000) / 3
    assert result["data"]["period"]["title"] == "октябрь 2026"


def test_aggregate_max_returns_examples(analytics_db):
    """Максимум называет число и показывает, на какой записи он получен."""
    result = analytics_tools.aggregate_contracts(
        period="октябрь", operation="max"
    )
    assert result["ok"] is True
    assert result["data"]["value"] == 240000
    assert result["data"]["examples"][0]["record_id"]


def test_aggregate_min(analytics_db):
    """Минимум за октябрь."""
    result = analytics_tools.aggregate_contracts(
        period="октябрь", operation="min"
    )
    assert result["ok"] is True
    assert result["data"]["value"] == 12000


def test_aggregate_count_without_field(analytics_db):
    """«Сколько договоров» — количество записей, поле не нужно."""
    result = analytics_tools.aggregate_contracts(operation="count", field="")
    assert result["ok"] is True
    assert result["data"]["value"] == len(CONTRACTS)


def test_aggregate_count_for_period(analytics_db):
    """Количество договоров за ноябрь."""
    result = analytics_tools.aggregate_contracts(
        entity_type="contracts", operation="count", field="", period="ноябрь"
    )
    assert result["ok"] is True
    assert result["data"]["value"] == 2


def test_aggregate_russian_field_and_operation(analytics_db):
    """Поле и операция понимаются по-русски: «средняя сумма»."""
    result = analytics_tools.aggregate_contracts(
        period="октябрь", field="сумма", operation="средняя"
    )
    assert result["ok"] is True
    assert result["data"]["field"] == "amount"
    assert result["data"]["operation"] == "avg"


def test_aggregate_month_with_year(analytics_db):
    """«октябрь 2026» — явный год, границы месяца верные."""
    result = analytics_tools.aggregate_contracts(
        period="октябрь 2026", operation="count", field=""
    )
    assert result["data"]["period"]["start"] == "2026-10-01"
    assert result["data"]["period"]["end"] == "2026-10-31"


def test_aggregate_last_months_period(analytics_db):
    """«3 месяца» — окно ровно в три месяца назад от сегодняшнего дня."""
    result = analytics_tools.aggregate_contracts(period="3 месяца")
    assert result["ok"] is True
    period = result["data"]["period"]
    assert period["start"] == "2026-07-17"
    assert period["end"] == "2026-10-15"
    # В окно попадают договоры сентября и октября, ноябрьские — нет.
    assert result["data"]["value"] == 120000 + 360000


def test_aggregate_explicit_range(analytics_db):
    """«с 01.09.2026 по 30.09.2026» — ровно сентябрь."""
    result = analytics_tools.aggregate_contracts(
        period="с 01.09.2026 по 30.09.2026"
    )
    assert result["ok"] is True
    assert result["data"]["value"] == 120000


def test_aggregate_drivers_count(analytics_db):
    """Считаем не только договоры: водителей трое."""
    result = analytics_tools.aggregate_contracts(
        entity_type="drivers", operation="count", field=""
    )
    assert result["ok"] is True
    assert result["data"]["value"] == 3


def test_aggregate_empty_period_gives_text(analytics_db):
    """За период без данных — понятный текст, а не пустое место."""
    result = analytics_tools.aggregate_contracts(period="март 2020")
    assert result["ok"] is True
    assert result["data"]["value"] is None
    assert "данных нет" in result["data"]["text"]


def test_month_without_year_is_current_year(analytics_db):
    """«Ноябрь» без года — ноябрь ТЕКУЩЕГО года, даже если он ещё не начался."""
    result = analytics_tools.aggregate_contracts(
        period="ноябрь", operation="count", field=""
    )
    assert result["ok"] is True
    assert result["data"]["period"]["title"] == "ноябрь 2026"
    assert result["data"]["value"] == 2


def test_aggregate_rejects_unknown_field(analytics_db):
    """Неизвестное поле — ошибка со списком доступных."""
    result = analytics_tools.aggregate_contracts(field="вес")
    assert result["ok"] is False
    assert "вес" in result["error"]
    assert result["available"]


def test_aggregate_rejects_unknown_entity(analytics_db):
    """Неизвестная сущность — ошибка со списком доступных."""
    result = analytics_tools.aggregate_contracts(entity_type="самолёты")
    assert result["ok"] is False
    assert "самолёты" in result["error"]


def test_aggregate_rejects_unknown_period(analytics_db):
    """Непонятный период — ошибка с примерами записи."""
    result = analytics_tools.aggregate_contracts(period="позапрошлый раз")
    assert result["ok"] is False
    assert "период" in result["error"].casefold()


def test_aggregate_rejects_sql_in_field(analytics_db):
    """SQL в имени поля отклоняется белым списком (инъекция невозможна)."""
    result = analytics_tools.aggregate_contracts(
        field="amount); DROP TABLE contracts;--"
    )
    assert result["ok"] is False
    # Таблица на месте: запрос даже не собирался.
    check = analytics_tools.aggregate_contracts(operation="count", field="")
    assert check["data"]["value"] == len(CONTRACTS)


def test_aggregate_period_as_dict(analytics_db):
    """Период, присланный объектом, тоже понимается."""
    result = analytics_tools.aggregate_contracts(
        period={"start": "2026-10-01", "end": "2026-10-31"}
    )
    assert result["ok"] is True
    assert result["data"]["value"] == 360000


# ─────────────────────────────────────────────────────────────
# 3. Сравнение периодов
# ─────────────────────────────────────────────────────────────

def test_compare_periods_numbers(analytics_db):
    """«Сравни октябрь с сентябрём»: оба числа и разница."""
    result = analytics_tools.compare_periods("октябрь", "сентябрь")
    assert result["ok"] is True
    data = result["data"]
    assert data["first"]["value"] == 360000
    assert data["second"]["value"] == 120000
    assert data["difference"] == 240000
    assert data["difference_percent"] == 200.0
    assert data["grew"] is True


def test_compare_periods_records_count(analytics_db):
    """Количество записей в каждом периоде тоже приходит."""
    result = analytics_tools.compare_periods("октябрь", "сентябрь")
    data = result["data"]
    assert data["first"]["records"] == 3
    assert data["second"]["records"] == 1


def test_compare_periods_by_counterparty(analytics_db):
    """Разбивка по контрагентам: у каждого периода свои группы."""
    result = analytics_tools.compare_periods(
        "октябрь", "сентябрь", group_by="перевозчики"
    )
    assert result["ok"] is True
    groups = result["data"]["first"]["groups"]
    labels = [item["label"] for item in groups]
    assert any("Ромашка" in label for label in labels)
    assert any("Василёк" in label for label in labels)


def test_compare_periods_count_operation(analytics_db):
    """Сравнивать можно и количество записей."""
    result = analytics_tools.compare_periods(
        "октябрь", "сентябрь", operation="count", field=""
    )
    assert result["ok"] is True
    assert result["data"]["first"]["value"] == 3
    assert result["data"]["difference"] == 2


def test_compare_periods_text_mentions_percents(analytics_db):
    """Текст сравнения говорит про рост в процентах."""
    result = analytics_tools.compare_periods("октябрь", "сентябрь")
    assert "200" in result["data"]["text"]


def test_compare_periods_shrink_is_negative(analytics_db):
    """Обратное сравнение показывает падение."""
    result = analytics_tools.compare_periods("сентябрь", "октябрь")
    assert result["data"]["difference"] == -240000
    assert result["data"]["grew"] is False


def test_compare_periods_rejects_bad_period(analytics_db):
    """Непонятный период — ошибка с указанием, какой именно."""
    result = analytics_tools.compare_periods("октябрь", "когда-то")
    assert result["ok"] is False
    assert "второй период" in result["error"]


# ─────────────────────────────────────────────────────────────
# 4. Топы
# ─────────────────────────────────────────────────────────────

def test_top_contractors_by_period(analytics_db):
    """«Топ-5 перевозчиков за октябрь» — отсортированный список."""
    result = analytics_tools.top_contractors(period="октябрь", limit=5)
    assert result["ok"] is True
    items = result["data"]["items"]
    assert len(items) == 2
    assert "Ромашка" in items[0]["label"]
    assert items[0]["value"] == 240000
    assert items[0]["rank"] == 1
    assert items[1]["value"] == 120000


def test_top_contractors_limit(analytics_db):
    """Лимит соблюдается: не больше запрошенного."""
    result = analytics_tools.top_contractors(limit=1)
    assert len(result["data"]["items"]) == 1


def test_top_contractors_totals(analytics_db):
    """Общее число групп и записей приходит вместе с топом."""
    result = analytics_tools.top_contractors(limit=5)
    data = result["data"]
    assert data["groups_total"] == 2
    assert data["records_total"] == len(CONTRACTS)
    assert data["value_total"] == 624000


def test_top_contractors_by_drivers(analytics_db):
    """Топ можно строить по водителям."""
    result = analytics_tools.top_contractors(group_by="водители", limit=5)
    assert result["ok"] is True
    assert result["data"]["group_by"] == "driver"


def test_top_contractors_entity_carriers(analytics_db):
    """Если считаем самих перевозчиков, топ — это их записи, не сторона."""
    result = analytics_tools.top_contractors(
        entity_type="carriers", group_by="перевозчики", operation="count",
        field="",
    )
    assert result["ok"] is True
    assert result["data"]["entity"] == "carriers"
    # Строки топа — перевозчики справочника, а не договоры.
    labels = [item["label"] for item in result["data"]["items"]]
    assert any("Ромашка" in label for label in labels)
    assert any("Василёк" in label for label in labels)


def test_top_contractors_empty_period(analytics_db):
    """За период без данных топ пуст, но ответ успешный."""
    result = analytics_tools.top_contractors(period="январь 2020")
    assert result["ok"] is True
    assert result["data"]["items"] == []
    assert "данных нет" in result["data"]["text"]


# ─────────────────────────────────────────────────────────────
# 5. Количество по условиям
# ─────────────────────────────────────────────────────────────

def test_count_records_expiring_license(analytics_db):
    """«Сколько водителей с истекающим ВУ» — считаем по сроку."""
    result = analytics_tools.count_records(
        "drivers", [{"field": "license_expiry", "days": 30}]
    )
    assert result["ok"] is True
    assert result["data"]["count"] == 1
    assert "Иванов" in result["data"]["examples"][0]["label"]


def test_count_records_expired_license(analytics_db):
    """Просроченное ВУ — отдельный фильтр (days = -1)."""
    result = analytics_tools.count_records(
        "водители", [{"field": "license_expiry", "days": -1}]
    )
    assert result["ok"] is True
    assert result["data"]["count"] == 1


def test_count_records_named_filter_empty_phone(analytics_db):
    """Готовый фильтр по имени: водители без телефона."""
    result = analytics_tools.count_records("drivers", ["empty_phone"])
    assert result["ok"] is True
    assert result["data"]["count"] == 2


def test_count_records_empty_vin(analytics_db):
    """Машины без VIN — готовый фильтр empty_vin."""
    result = analytics_tools.count_records("vehicles", ["empty_vin"])
    assert result["ok"] is True
    assert result["data"]["count"] == len(CONTRACTS)


def test_count_records_empty_inn(analytics_db):
    """Перевозчики без ИНН."""
    result = analytics_tools.count_records("carriers", ["empty_inn"])
    assert result["ok"] is True
    assert result["data"]["count"] == 1


def test_count_records_with_value_filter(analytics_db):
    """Фильтр сравнения: договоры дороже 100 000 — их три."""
    result = analytics_tools.count_records(
        "contracts", [{"field": "amount", "operator": "gt", "value": 100000}]
    )
    assert result["ok"] is True
    assert result["data"]["count"] == 3


def test_count_records_with_contains_filter(analytics_db):
    """Фильтр по части текста: маршруты про Казань."""
    result = analytics_tools.count_records(
        "contracts", [{"field": "route", "contains": "Казань"}]
    )
    assert result["ok"] is True
    assert result["data"]["count"] == 2


def test_count_records_with_period(analytics_db):
    """Считать можно и за период."""
    result = analytics_tools.count_records("contracts", period="октябрь")
    assert result["ok"] is True
    assert result["data"]["count"] == 3


def test_count_records_text_is_human(analytics_db):
    """Текст ответа понятен без полей базы."""
    result = analytics_tools.count_records("drivers", ["empty_phone"])
    assert "водители" in result["data"]["text"]
    assert "телефон" in result["data"]["text"]


def test_count_records_rejects_unknown_filter(analytics_db):
    """Неизвестный готовый фильтр — ошибка со списком доступных."""
    result = analytics_tools.count_records("drivers", ["empty_soul"])
    assert result["ok"] is False
    assert "empty_soul" in result["error"]


def test_count_records_rejects_bad_field(analytics_db):
    """Поле не из белого списка — ошибка, а не молчаливый ноль."""
    result = analytics_tools.count_records(
        "drivers", [{"field": "secret", "empty": True}]
    )
    assert result["ok"] is False
    assert "secret" in result["error"]


# ─────────────────────────────────────────────────────────────
# 6. Качество данных
# ─────────────────────────────────────────────────────────────

def test_check_data_quality_finds_gaps(analytics_db):
    """Пустые поля находятся и пересчитываются."""
    result = analytics_tools.check_data_quality("drivers")
    assert result["ok"] is True
    gaps = {item["field"]: item for item in result["data"]["gaps"]}
    assert gaps["phone"]["missing"] == 2
    assert gaps["passport"]["missing"] == 1
    assert result["data"]["is_clean"] is False


def test_check_data_quality_finds_duplicates(analytics_db):
    """Дубликаты по паспорту видны отдельным списком."""
    result = analytics_tools.check_data_quality("drivers")
    duplicates = result["data"]["duplicates"]
    assert duplicates, "дубликаты паспорта не найдены"
    assert duplicates[0]["field"] == "passport"
    assert duplicates[0]["items"][0]["value"] == "2815 634512"
    assert duplicates[0]["items"][0]["count"] == 2


def test_check_data_quality_expired(analytics_db):
    """Просроченные сроки попадают в отчёт."""
    result = analytics_tools.check_data_quality("drivers")
    expired = {item["field"]: item for item in result["data"]["expired"]}
    assert expired["license_expiry"]["count"] == 1


def test_check_data_quality_clean_entity(analytics_db):
    """У заказчика все обязательные поля заполнены."""
    result = analytics_tools.check_data_quality("customers")
    assert result["ok"] is True
    assert result["data"]["gaps"] == []
    assert result["data"]["is_clean"] is True


def test_check_data_quality_own_fields(analytics_db):
    """Свои обязательные поля: проверяем только телефон."""
    result = analytics_tools.check_data_quality(
        "drivers", required_fields=["телефон"]
    )
    assert result["ok"] is True
    assert [item["field"] for item in result["data"]["gaps"]] == ["phone"]


def test_check_data_quality_vehicles(analytics_db):
    """Машины: пустые VIN и повторы."""
    result = analytics_tools.check_data_quality("vehicles")
    fields = {item["field"] for item in result["data"]["gaps"]}
    assert "vin" in fields
    assert result["data"]["duplicates"], "повторы VIN не найдены"


def test_check_data_quality_text_summarises(analytics_db):
    """Текст отчёта перечисляет проблемы словами."""
    result = analytics_tools.check_data_quality("carriers")
    text = result["data"]["text"]
    assert "перевозчики" in text
    assert "ИНН" in text


def test_check_data_quality_rejects_unknown_entity(analytics_db):
    """Неизвестная сущность — ошибка со списком доступных."""
    result = analytics_tools.check_data_quality("звёзды")
    assert result["ok"] is False
    assert result["available"]


# ─────────────────────────────────────────────────────────────
# 7. Разбор аргументов
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("сумма", "amount"),
    ("Сумма", "amount"),
    ("сумма с НДС", "amount"),
    ("ИНН", "inn"),
    ("фио", "driver_name"),
    ("госномер", "plate_number"),
    ("ВУ", "license_expiry"),
    ("amount", "amount"),
])
def test_resolve_field_aliases(text, expected):
    """Русские названия полей переводятся в ключи каталога."""
    assert analytics_tools.resolve_field(text) == expected


def test_resolve_field_unknown_returns_as_is():
    """Неизвестное имя не подменяется: база сама скажет, что его нет."""
    assert analytics_tools.resolve_field("вес") == "вес"


@pytest.mark.parametrize("text,expected", [
    ("sum", "sum"), ("сумма", "sum"), ("средняя", "avg"),
    ("количество", "count"), ("сколько", "count"), ("максимум", "max"),
])
def test_resolve_operation_aliases(text, expected):
    """Русские названия операций переводятся в ключи."""
    assert analytics.aggregate("contracts", "amount", expected)["ok"] is True
    assert analytics.resolve_operation(text) == expected


def test_tool_specs_have_required_arguments():
    """У compare_periods оба периода обязательны, у остальных — по делу."""
    specs = {spec["name"]: spec for spec in analytics_tools.tool_specs()}
    assert specs["compare_periods"]["parameters"]["required"] == [
        "period1", "period2"
    ]
    assert specs["count_records"]["parameters"]["required"] == ["entity_type"]
    assert specs["check_data_quality"]["parameters"]["required"] == ["entity_type"]


def test_registry_call_through_tool_registry(analytics_db):
    """Инструменты работают и через реестр (как их зовёт агент)."""
    registry = build_registry(AgentContext())
    result = registry.call("aggregate_contracts", {
        "operation": "count", "field": "", "period": "октябрь",
    })
    assert result["ok"] is True
    assert result["data"]["value"] == 3
