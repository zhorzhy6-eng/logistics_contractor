#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автозаполнение договора из текста (задание, часть 1: A, B, D, E).

Что проверяется
---------------
1. **Разбор по ключевым словам** (A.1): «Вины:», «Загрузка:», «Выгрузка:»,
   «Перевозчик:», «Заказчик:», «Сумма:», «НДС:», «Дата:», «Маршрут:»,
   «№», «Срок оплаты:» — и то, что ключ работает как заголовок раздела
   (столбик вин под «Вины:»).
2. **Разбор без ключевых слов** (A.2): VIN — 17 символов, адреса — по
   признакам «г.», «ул.», «д.», «обл.», сумма — большое число, даты —
   «ДД.ММ.ГГГГ».
3. **Обогащение** (A.3): VIN нормализуется в латиницу, перевозчик и
   заказчик находятся в базе нечётким поиском, адреса приводятся к
   стандартному виду, сумма становится числом.
4. **Превью** (A.4, B): строки «поле → значение → куда», правки оператора
   в ячейке, отмена.
5. **Заполнение** (A.5): текст → ``parse_and_fill`` → форма (договор,
   машины, стороны) через существующие fill_data/fill_fields.

Модель — подставная (``ScriptedChat``), база — изолированная, окно —
заглушка с вкладками. Живой прогон с настоящим GigaChat и настоящим окном —
``tests/_tmp/_parse_and_fill/_live_check.py``.
"""

import json
import os
import sys
from pathlib import Path

import pytest
from PyQt5.QtCore import Qt

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import fill_parser  # noqa: E402
from core.agent.fill_parser import (  # noqa: E402
    normalize_vat_rate, normalize_vehicles, normalize_vin,
    parse_date_iso, parse_int, parse_price, parse_text, vin_is_valid,
)
from core.agent.tools import AgentContext, build_registry, set_context_window  # noqa: E402

# Два настоящих VIN из задания: по ним и проверяется разбор.
VIN_1 = "EC37CUSM4TC008647"
VIN_2 = "EC37LUSM8TC007923"

OPERATOR_TEXT = (
    f"Вины: {VIN_1}, {VIN_2}\n"
    "Загрузка: Москва, Ленина 5, 15.10.2026\n"
    "Выгрузка: Казань, Пушкина 10, 17.10.2026\n"
    "Перевозчик: Ромашка\n"
    "Сумма: 80000, НДС 20"
)


# ─────────────────────────────────────────────────────────────
# Заглушки: окно с вкладками
# ─────────────────────────────────────────────────────────────

class StubTab:
    """Вкладка формы: словарь полей и список вызовов fill_data."""

    def __init__(self, data=None):
        self.data = dict(data or {})
        self.calls = []

    def get_data(self):
        return dict(self.data)

    def fill_data(self, values=None, *args):
        self.calls.append(values)
        if isinstance(values, dict):
            self.data.update(values)


class StubVehiclesTab(StubTab):
    """Таблица «Перевозимые ТС»: отдаёт и принимает СПИСОК машин."""

    def __init__(self):
        super().__init__()
        self.rows = []

    def get_data(self):
        return list(self.rows)

    def fill_data(self, vehicles=None, *args, **kwargs):
        self.calls.append(list(vehicles or []))
        self.rows = [dict(vehicle) for vehicle in (vehicles or [])]


class StubWindow:
    """Окно «Экспедиторство»: те же вкладки, что у MainWindow."""

    CONTRACT_TYPE = "perevozka"

    def __init__(self):
        self.carrier_tab = StubTab({"full_name": "", "inn": ""})
        self.driver_tab = StubTab({"full_name": ""})
        self.trailer_tab = None
        self.vehicles_tab = StubVehiclesTab()
        self.contract_tab = StubTab({
            "number": "", "route": "", "price_with_vat": 0.0, "vat_rate": "22%",
            "payment_days": 0, "date": "2026-01-01",
            "loadings": [], "unloadings": [],
        })
        self.customer_tab = StubTab({"full_name": ""})

    def _tab_keys(self):
        return ("carrier_tab", "driver_tab", "vehicles_tab",
                "contract_tab", "customer_tab")

    def _tab_by_key(self, key):
        return getattr(self, key, None)


@pytest.fixture
def window():
    """
    Окно-заглушка, подставленное инструментам как «открытая форма».

    Подстановка снимается на выходе: без этого следующий тест в общем
    прогоне получил бы ЧУЖОЕ окно и увидел бы в форме данные предыдущего.
    """
    win = StubWindow()
    previous = set_context_window(win)
    yield win
    set_context_window(previous)


@pytest.fixture(autouse=True)
def clean_agent_state():
    """
    Чистит состояние агента после каждого теста этого файла.

    Прогон идёт одним процессом: сессия диалога (`current_session_id`) и
    таблица истории — общие, поэтому сообщения, записанные агентом в одном
    тесте, видны в другом (это и ловит общий прогон: с новыми тестами
    «чужие» сообщения всплывали в tests/test_agent_widget.py). После теста
    история и сессия сбрасываются.
    """
    from core.agent import memory as agent_memory
    from db.crud import agent_history

    yield
    try:
        #: История диалога живёт в таблице agent_history и привязана к
        #: сессии процесса. Своего «очистить всё» у неё нет — чистим сессию
        #: и чужие сессии, а затем меняем ID сессии, чтобы следующий тест
        #: начинал с пустой истории.
        session = agent_memory.current_session_id()
        agent_history.clear_session(session)
        agent_history.purge_other_sessions(session)
    except Exception:  # noqa: BLE001 — уборка не должна ронять тест
        pass
    agent_memory.reset_session_id()


@pytest.fixture
def registry(window):
    """Реестр инструментов с окном-заглушкой."""
    return build_registry(AgentContext(window=window, contract_type="perevozka"))


@pytest.fixture
def carrier(isolated_db):
    """Перевозчик «ООО «Ромашка»» в изолированной базе (для A.3)."""
    from db.crud.organizations import save_organization

    return save_organization(
        {"full_name": "ООО «Ромашка»", "short_name": "Ромашка",
         "inn": "7701234567", "kpp": "770101001", "ogrn": "1027700132195",
         "legal_address": "г. Москва, ул. Тестовая, д. 1",
         "director_name": "Петров Пётр Петрович",
         "director_position": "Генеральный директор",
         "phone": "+7 (495) 123-45-67", "email": "info@example.ru"},
        is_carrier=True,
    )


@pytest.fixture
def customer(isolated_db):
    """Заказчик «ООО «Заказчик»» в изолированной базе."""
    from db.crud.organizations import save_organization

    return save_organization(
        {"full_name": "ООО «Заказчик»", "short_name": "Заказчик",
         "inn": "7709876543", "kpp": "770901001", "ogrn": "1027700999999"},
        is_carrier=False,
    )


def parse(text, **kwargs):
    """Разбор без DaData: внешний сервис в тестах не нужен."""
    kwargs.setdefault("use_dadata", False)
    return parse_text(text, **kwargs)


@pytest.fixture
def qt_app():
    """QApplication для диалога превью (offscreen)."""
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# ─────────────────────────────────────────────────────────────
# E.1. Разбор по ключевым словам
# ─────────────────────────────────────────────────────────────

def test_keywords_text_is_parsed():
    """Текст из задания разбирается целиком."""
    result = parse(OPERATOR_TEXT)

    assert result.vehicles, "машины не найдены"
    assert [row["vin"] for row in result.vehicles] == [VIN_1, VIN_2]
    assert result.contract_fields()["price_with_vat"] == 80000.0
    assert result.contract_fields()["vat_rate"] == "20%"
    assert result.point_rows("loading")[0]["address"] == "Москва, Ленина 5"
    assert result.point_rows("unloading")[0]["address"] == "Казань, Пушкина 10"


def test_keywords_are_reported():
    """Разбор сообщает, какие ключевые слова встретились."""
    result = parse(OPERATOR_TEXT)
    for word in ("вины", "загрузка", "выгрузка", "перевозчик", "сумма"):
        assert word in result.keywords, word


def test_loading_and_unloading_dates_are_separate():
    """Дата погрузки и дата выгрузки не путаются между собой."""
    result = parse(OPERATOR_TEXT)
    assert result.point_rows("loading")[0]["date"] == "2026-10-15"
    assert result.point_rows("unloading")[0]["date"] == "2026-10-17"


def test_route_date_is_not_contract_date():
    """Дата из адреса погрузки НЕ становится датой договора."""
    result = parse(OPERATOR_TEXT)
    assert "date" not in result.contract_fields()


def test_vin_key_is_a_section_header():
    """«Вины:» работает заголовком: вины столбиком под ним — один раздел."""
    result = parse("Вины:\n" + VIN_1 + "\n" + VIN_2 + "\nСумма: 80000")
    assert [row["vin"] for row in result.vehicles] == [VIN_1, VIN_2]


def test_vin_key_accepts_latin_and_russian():
    """«VIN:» и «Вины:» — один и тот же ключ."""
    latin = parse(f"VIN: {VIN_1}")
    russian = parse(f"Вины: {VIN_1}")
    assert latin.vehicles == russian.vehicles


def test_contract_number_and_date_by_keywords():
    """Номер и дата договора разбираются по ключевым словам."""
    result = parse("Договор № 23092026-74\nДата: 23.09.2026\nМаршрут: Москва — Пермь")
    fields = result.contract_fields()
    assert fields["number"] == "23092026-74"
    assert fields["date"] == "2026-09-23"
    assert fields["route"] == "Москва — Пермь"


def test_payment_days_keyword():
    """«Срок оплаты: 10 дней» → число дней."""
    result = parse("Срок оплаты: 10 дней")
    assert result.contract_fields()["payment_days"] == 10


def test_ignored_key_does_not_swallow_data():
    """Служебные подписи («Примечание:») не превращаются в раздел."""
    result = parse("Примечание: согласовано по телефону\nСумма: 80000")
    assert result.contract_fields()["price_with_vat"] == 80000.0
    assert "Примечание: согласовано по телефону" in result.unresolved


# ─────────────────────────────────────────────────────────────
# E.2. Разбор без ключевых слов
# ─────────────────────────────────────────────────────────────

def test_vin_without_keywords():
    """VIN без подписи находится по виду."""
    result = parse(f"Машина {VIN_1}\nВторая {VIN_2}")
    assert [row["vin"] for row in result.vehicles] == [VIN_1, VIN_2]


def test_address_without_keywords():
    """Адрес без подписи узнаётся по «г.», «ул.», «д.»."""
    result = parse("Москва, ул. Ленина, д. 5 — 15.10.2026")
    rows = result.point_rows("loading")
    assert rows and "Ленина" in rows[0]["address"]
    assert rows[0]["date"] == "2026-10-15"


def test_two_addresses_without_keywords_are_route_points():
    """
    Два адреса без подписей — погрузка и выгрузка, а не две погрузки.

    Без подписей раздел определяется порядком рейса: первый адрес —
    погрузка, остальные — выгрузки.
    """
    result = parse(
        "Москва, ул. Ленина, д. 5 — 15.10.2026\n"
        "Казань, ул. Пушкина, д. 10 — 17.10.2026"
    )
    assert len(result.point_rows("loading")) == 1
    assert len(result.point_rows("unloading")) == 1
    assert "Казань" in result.point_rows("unloading")[0]["address"]


def test_later_address_is_not_loading():
    """Адрес с поздней датой выгрузкой быть не может — он уходит в выгрузку."""
    result = parse(
        "Казань, ул. Пушкина, д. 10 — 17.10.2026\n"
        "Москва, ул. Ленина, д. 5 — 15.10.2026"
    )
    assert "Москва" in result.point_rows("loading")[0]["address"]
    assert "Казань" in result.point_rows("unloading")[0]["address"]


def test_amount_without_keywords():
    """Большое число без подписи считается суммой."""
    result = parse("80000")
    assert result.contract_fields()["price_with_vat"] == 80000.0


def test_small_number_is_not_amount():
    """Число меньше порога суммой не считается."""
    result = parse("10")
    assert "price_with_vat" not in result.contract_fields()


def test_single_date_without_keywords_is_contract_date():
    """Одна дата в тексте (и она не в адресе) — дата договора."""
    result = parse(f"Машина {VIN_1}, договор от 23.09.2026")
    assert result.contract_fields()["date"] == "2026-09-23"


def test_unparsed_text_is_reported():
    """Непонятный текст попадает в «Не понял», а не теряется."""
    result = parse("Привет, посмотри пожалуйста\nСозвонись с клиентом завтра")
    assert len(result.unresolved) == 2
    assert any("Привет" in line for line in result.unresolved)


def test_short_vin_is_reported_as_vin():
    """VIN неверной длины называется VIN, а не «непонятной строкой»."""
    result = parse("Вины: EC37CUSM4TC00864")
    assert result.unresolved
    assert "VIN" in result.unresolved[0]
    assert "16" in result.unresolved[0]


def test_table_header_is_skipped():
    """Шапка таблицы из Excel не попадает в «не понял»."""
    result = parse("VIN\tМарка\tГод\n" + f"{VIN_1}\tJETOUR T2\t2024")
    assert result.unresolved == []
    assert result.vehicles[0]["brand_model"] == "JETOUR T2"


# ─────────────────────────────────────────────────────────────
# E.3. VIN нормализуется
# ─────────────────────────────────────────────────────────────

def test_vin_lookalikes_are_normalized():
    """Кириллические буквы-двойники переводятся в латиницу."""
    assert normalize_vin("ЕС37СUSM4ТС008647") == VIN_1
    result = parse("Вины: ЕС37СUSM4ТС008647")
    assert result.vehicles[0]["vin"] == VIN_1


def test_vin_with_separators_is_normalized():
    """Пробелы и дефисы внутри VIN убираются."""
    assert normalize_vin("EC37-CUSM4 TC008647") == VIN_1


def test_vin_validity_check():
    """Проверка «настоящий ли VIN»: длина и запрещённые буквы."""
    assert vin_is_valid(VIN_1)
    assert not vin_is_valid(VIN_1[:-1])
    assert not vin_is_valid("EC37CUSM4TC00864I")


def test_short_vin_gets_warning():
    """Ненадёжный VIN остаётся в таблице, но помечается предупреждением."""
    result = parse("Вины: EC37CUSM4TC00864")  # 16 символов вместо 17
    assert result.vehicles, "почти-VIN не должен теряться"
    assert result.vehicles[0]["status"] == "warning"
    assert "17" in result.vehicles[0]["note"]
    assert result.unresolved, "о ненадёжном VIN надо сказать оператору"


def test_vehicle_row_with_brand_and_vin():
    """Марка берётся из той же строки, что VIN."""
    result = parse(f"JETOUR T2 {VIN_1}")
    assert result.vehicles[0]["brand_model"] == "JETOUR T2"
    assert result.vehicles[0]["vin"] == VIN_1


def test_two_vins_in_one_line_have_no_brand():
    """Два VIN в строке — две машины БЕЗ марки (марки там нет)."""
    result = parse(f"{VIN_1} {VIN_2}")
    assert [row["brand_model"] for row in result.vehicles] == ["", ""]


def test_vehicles_from_excel_table():
    """Вставка из таблицы: VIN, марка и год из своих колонок."""
    result = parse(f"{VIN_1}\tJETOUR T2\t2024\t15.10.2026")
    vehicle = result.vehicles[0]
    assert vehicle["vin"] == VIN_1
    assert vehicle["brand_model"] == "JETOUR T2"
    assert vehicle["year"] == 2024


def test_brand_is_added_to_known_vin():
    """Марка дописывается к машине, найденной раньше без марки."""
    result = parse(f"Вины: {VIN_1}\n{VIN_1} JETOUR T2")
    assert len(result.vehicles) == 1
    assert result.vehicles[0]["brand_model"] == "JETOUR T2"


def test_normalize_vehicles_accepts_dicts_and_lines():
    """normalize_vehicles принимает и словари, и строки."""
    rows = normalize_vehicles([
        {"vin": VIN_1, "brand_model": "JETOUR T2"},
        f"JETOUR T1 {VIN_2}",
    ])
    assert [row["vin"] for row in rows] == [VIN_1, VIN_2]
    assert rows[1]["brand_model"] == "JETOUR T1"


# ─────────────────────────────────────────────────────────────
# E.4. Перевозчик ищется в базе
# ─────────────────────────────────────────────────────────────

def test_carrier_is_found_in_db(isolated_db, carrier):
    """Перевозчик по короткому названию находится в базе (A.3)."""
    result = parse("Перевозчик: Ромашка\nСумма: 80000")
    record = result.entities.get("carrier")
    assert record, "перевозчик не найден"
    assert record["name"] == "ООО «Ромашка»"
    assert record["fields"]["inn"] == "7701234567"


def test_carrier_not_found_is_reported(isolated_db):
    """Ненайденный перевозчик сообщается отдельно."""
    result = parse("Перевозчик: Несуществующая-Фирма-999")
    assert not result.entities
    assert result.missing_entities[0]["name"] == "Несуществующая-Фирма-999"


def test_customer_is_found_separately(isolated_db, customer):
    """Заказчик ищется среди заказчиков, а не среди перевозчиков."""
    result = parse("Заказчик: Заказчик")
    assert result.entities.get("customer")
    assert not result.entities.get("carrier")


def test_entity_line_disappears_from_preview(isolated_db, carrier):
    """
    Найденная запись показывается полями, а не строкой «Перевозчик».

    Сама строка разбора остаётся (по ней пересобираются стороны после правок
    оператора), но в превью её заменяют поля найденной записи.
    """
    result = parse("Перевозчик: Ромашка")
    labels = [row["field"] for row in result.preview_rows()]
    assert "Перевозчик" not in labels
    assert any(label.startswith("Перевозчик: ИНН") for label in labels)
    #: Строка разбора сохранена — иначе исправленное название потерялось бы.
    assert ("carrier", "full_name") in {(item.section, item.field)
                                        for item in result.items}


def test_ambiguous_name_is_not_resolved(isolated_db):
    """Несколько похожих записей — выбор за оператором, а не догадка."""
    from db.crud.organizations import save_organization

    for inn in ("7701002001", "7701002002"):
        save_organization(
            {"full_name": "ООО «Ромашка-Плюс»", "inn": inn}, is_carrier=True
        )
    result = parse("Перевозчик: Ромашка-Плюс")
    assert not result.entities
    assert result.ambiguous_entities
    assert len(result.ambiguous_entities[0]["candidates"]) >= 2


def test_db_error_does_not_break_parsing(monkeypatch):
    """Недоступная база не роняет разбор."""
    import db.crud.organizations as organizations

    def boom(*args, **kwargs):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(organizations, "search_organizations", boom)
    result = parse("Перевозчик: Ромашка\nСумма: 80000")
    assert result.contract_fields()["price_with_vat"] == 80000.0
    assert result.missing_entities


def test_exact_name_wins_over_search_order(isolated_db):
    """
    Совпадений несколько — запись выбирается по имени, а не по порядку выдачи.

    «Ромашка» при записях «ООО «Ромашка»» и «ООО «Ромашка-Логистик»» — это
    первая: она отвечает названию целиком.
    """
    from db.crud.organizations import save_organization

    save_organization({"full_name": "ООО «Ромашка»", "short_name": "Ромашка",
                       "inn": "7701002001"}, is_carrier=True)
    save_organization({"full_name": "ООО «Ромашка-Логистик»",
                       "short_name": "Ромашка-Логистик",
                       "inn": "7701002002"}, is_carrier=True)

    result = parse("Перевозчик: Ромашка")
    record = result.entities.get("carrier")
    assert record, "точное совпадение должно быть выбрано"
    assert record["name"] == "ООО «Ромашка»"


# ─────────────────────────────────────────────────────────────
# E.5. Адреса стандартизируются
# ─────────────────────────────────────────────────────────────

def test_address_is_cleaned_with_dadata(monkeypatch):
    """DaData включена — адрес приходит стандартным (A.3)."""
    import core.agent.dadata_tools as dadata_tools

    monkeypatch.setattr(
        dadata_tools, "check_address_dadata",
        lambda address="", **_: {
            "ok": True, "data": {"address": "г Москва, ул Ленина, д 5"},
        },
    )
    result = parse_text("Загрузка: Москва, Ленина 5, 15.10.2026",
                        use_dadata=True)
    assert result.point_rows("loading")[0]["address"] == "г Москва, ул Ленина, д 5"


def test_address_kept_when_dadata_off(monkeypatch):
    """DaData выключена — адрес остаётся как написал оператор."""
    import core.agent.dadata_tools as dadata_tools

    def boom(*args, **kwargs):
        raise AssertionError("DaData не должна вызываться")

    monkeypatch.setattr(dadata_tools, "check_address_dadata", boom)
    result = parse("Загрузка: Москва, Ленина 5")
    assert result.point_rows("loading")[0]["address"] == "Москва, Ленина 5"


def test_address_time_window_is_parsed():
    """Окно времени вынимается из адреса отдельным полем."""
    result = parse("Загрузка: г. Москва, ул. Ленина, д. 5, 15.10.2026, "
                   "с 9:00 до 18:00")
    row = result.point_rows("loading")[0]
    assert row["time_window"] == "09:00-18:00"
    assert "9:00" not in row["address"]


def test_several_points_in_one_key():
    """Несколько точек в одном ключе делятся по «;»."""
    result = parse("Выгрузка: Казань, Пушкина 10; Уфа, Мира 7")
    rows = result.point_rows("unloading")
    assert len(rows) == 2
    assert "Казань" in rows[0]["address"]
    assert "Уфа" in rows[1]["address"]


# ─────────────────────────────────────────────────────────────
# E.6. Сумма приводится к числу
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("80000", 80000.0),
    ("80 000", 80000.0),
    ("80 000,50", 80000.5),
    ("80000.50", 80000.5),
    ("80 000 руб.", 80000.0),
    ("180300", 180300.0),
])
def test_price_is_converted(raw, expected):
    """Сумма приводится к числу в любом написании."""
    assert parse_price(raw) == expected


def test_price_without_digits():
    """Без цифр суммы нет."""
    assert parse_price("по договорённости") is None


def test_amount_in_text_is_number():
    """Сумма из текста — число, а не строка."""
    result = parse("Сумма: 80 000,50 руб.")
    assert result.contract_fields()["price_with_vat"] == 80000.5


@pytest.mark.parametrize("raw,expected", [
    ("20", "20%"), ("20%", "20%"), ("НДС 20 %", "20%"), ("22", "22%"),
    ("10%", "10%"), ("0", "0%"), ("без НДС", "без НДС"),
])
def test_vat_rate_is_normalized(raw, expected):
    """Ставка НДС приводится к виду формы."""
    assert normalize_vat_rate(raw) == expected


def test_amount_source_line_is_kept():
    """В превью видно, из какой строки взято значение."""
    result = parse("Сумма: 80000, НДС 20")
    price = next(item for item in result.items
                 if item.field == "price_with_vat")
    assert "80000" in price.source


def test_payment_days_conversion():
    """Срок оплаты — целое число."""
    assert parse_int("10 дней") == 10
    assert parse_int("нет") is None


def test_date_conversion():
    """Дата приводится к ISO."""
    assert parse_date_iso("15.10.2026") == "2026-10-15"
    assert parse_date_iso("2026-10-15") == "2026-10-15"
    assert parse_date_iso("не дата") == ""


# ─────────────────────────────────────────────────────────────
# E.7. Превью
# ─────────────────────────────────────────────────────────────

def test_preview_rows_have_field_value_target():
    """Строки превью: «поле → значение → куда»."""
    result = parse(OPERATOR_TEXT)
    rows = result.preview_rows()
    assert rows
    for row in rows:
        for key in ("field", "value", "target", "section", "status"):
            assert key in row, key


def test_preview_shows_amount_and_target():
    """Сумма видна в превью и адресована в договор."""
    result = parse("Сумма: 80000")
    row = next(item for item in result.preview_rows()
               if item["field"] == "Сумма")
    assert row["value"] == "80 000"
    assert row["target"] == "Договор"


def test_preview_lists_every_vehicle():
    """Каждая машина — своя строка превью: VIN и марка вместе."""
    result = parse(f"{VIN_1} JETOUR T2\n{VIN_2} JETOUR T1")
    rows = [row for row in result.preview_rows() if row["section"] == "vehicles"]
    assert len(rows) == 2
    assert VIN_1 in rows[0]["value"] and "JETOUR T2" in rows[0]["value"]
    assert VIN_2 in rows[1]["value"]


def test_preview_warns_about_bad_vin():
    """Ненадёжный VIN показан в превью со статусом «проверить»."""
    result = parse("Вины: EC37CUSM4TC00864")
    row = next(item for item in result.preview_rows()
               if item["section"] == "vehicles")
    assert row["status"] == "warning"
    assert "17" in row["note"]
    assert result.warnings, "предупреждение должно попасть в сводку"


def test_summary_lines_for_operator():
    """Сводка перечисляет то, что нашли."""
    result = parse(OPERATOR_TEXT)
    text = "\n".join(result.summary_lines())
    assert VIN_1 in text
    assert "80 000" in text


def test_counts_of_parsed():
    """Счётчики разбора: машины, поля договора, точки."""
    counts = parse(OPERATOR_TEXT).counts()
    assert counts["vehicles"] == 2
    assert counts["points"] == 2
    assert counts["contract"] >= 2


def test_tabs_of_parsed():
    """Разбор сообщает, какие вкладки будут заполнены."""
    tabs = parse(OPERATOR_TEXT).tabs_of()
    assert tabs[0] == "contract_tab"
    assert "vehicles_tab" in tabs


def test_empty_text_is_empty_result():
    """Пустой текст — пустой разбор, без исключений."""
    result = parse("")
    assert result.is_empty
    assert result.preview_rows() == []


def test_to_dict_is_json_serializable():
    """Результат разбора передаётся словарём (ответ инструмента)."""
    payload = parse(OPERATOR_TEXT).to_dict()
    json.dumps(payload, ensure_ascii=False)
    assert payload["counts"]["vehicles"] == 2


def test_preview_dialog_shows_rows(qt_app):
    """Диалог превью показывает таблицу и собирает правки (B.1–B.3)."""
    from ui.agent_fill_preview import FillPreviewDialog

    dialog = FillPreviewDialog(parse(OPERATOR_TEXT))
    try:
        assert dialog.table.rowCount() == len(dialog.parsed.preview_rows())
        assert dialog.table.horizontalHeaderItem(0).text() == "Поле"
        assert dialog.table.horizontalHeaderItem(2).text() == "Куда"
        assert dialog.overrides() == {}, "без правок правок и не должно быть"
    finally:
        dialog.deleteLater()


def test_preview_dialog_returns_edits(qt_app):
    """Правка ячейки возвращается как правка значения (B.2)."""
    from ui.agent_fill_preview import FillPreviewDialog

    parsed = parse("Сумма: 80000")
    dialog = FillPreviewDialog(parsed)
    try:
        rows = dialog.table_values()
        index = next(i for i, item in enumerate(rows)
                     if item["field"] == "Сумма")
        dialog.set_value(index, "95000")
        overrides = dialog.overrides()
        assert overrides["items"] == [
            {"section": "contract", "field": "price_with_vat", "value": "95000"}
        ]
    finally:
        dialog.deleteLater()


def test_preview_dialog_vehicle_rows_are_readonly(qt_app):
    """Строки машин в превью не правятся: VIN и марка живут вместе."""
    from ui.agent_fill_preview import FillPreviewDialog

    dialog = FillPreviewDialog(parse(f"{VIN_1} JETOUR T2"))
    try:
        rows = dialog.table_values()
        index = next(i for i, item in enumerate(rows)
                     if item["section"] == "vehicles")
        assert VIN_1 in rows[index]["new_value"]
        assert dialog.overrides() == {}
        item = dialog.table.item(index, 1)
        assert not (item.flags() & Qt.ItemIsEditable)
    finally:
        dialog.deleteLater()


def test_preview_dialog_vehicle_edit_is_ignored(qt_app):
    """Правка строки машины в превью в форму не уходит (её там нет)."""
    from ui.agent_fill_preview import FillPreviewDialog

    dialog = FillPreviewDialog(parse(f"{VIN_1} JETOUR T2"))
    try:
        rows = dialog.table_values()
        index = next(i for i, item in enumerate(rows)
                     if item["section"] == "vehicles")
        dialog.set_value(index, VIN_2)
        assert "vehicles" not in dialog.overrides()
    finally:
        dialog.deleteLater()


def test_preview_dialog_cancel_is_not_confirmed(qt_app):
    """«Отменить» — не подтверждено, заполнения не будет."""
    from ui.agent_fill_preview import FillPreviewDialog

    dialog = FillPreviewDialog(parse(OPERATOR_TEXT))
    try:
        assert dialog.is_confirmed() is False
        dialog.reject()
        assert dialog.is_confirmed() is False
    finally:
        dialog.deleteLater()


def test_preview_dialog_confirm_flag(qt_app):
    """«Подтвердить» ставит признак подтверждения."""
    from ui.agent_fill_preview import FillPreviewDialog

    dialog = FillPreviewDialog(parse(OPERATOR_TEXT))
    try:
        dialog.accept()
        assert dialog.is_confirmed() is True
    finally:
        dialog.deleteLater()


def test_preview_dialog_on_empty_parse(qt_app):
    """Нечего разбирать — диалог открывается и честно об этом говорит."""
    from ui.agent_fill_preview import FillPreviewDialog

    dialog = FillPreviewDialog(parse("Привет, как дела?"))
    try:
        assert dialog.table.rowCount() == len(dialog.parsed.preview_rows())
        assert dialog.overrides() == {}
    finally:
        dialog.deleteLater()


def test_status_marks():
    """Знак статуса: разобрано / требует внимания."""
    from ui.agent_fill_preview import status_mark

    assert status_mark("ok") == "OK"
    assert status_mark("warning") == "!"
    assert status_mark("") == "OK"


# ─────────────────────────────────────────────────────────────
# E.8. Заполнение формы
# ─────────────────────────────────────────────────────────────

def test_tool_is_registered_with_confirmation(registry):
    """Инструмент разбора текста есть в реестре и требует подтверждения."""
    tool = registry.get("parse_and_fill")
    assert tool is not None
    assert tool.requires_confirmation is True
    assert "text" in tool.parameters["properties"]


def test_parse_and_fill_fills_contract_and_vehicles(registry, window):
    """Текст заполняет договор, машины и точки маршрута (A.5)."""
    result = registry.call("parse_and_fill",
                           {"text": OPERATOR_TEXT, "use_dadata": False})
    assert result["ok"] is True, result

    assert window.contract_tab.data["price_with_vat"] == 80000.0
    assert window.contract_tab.data["vat_rate"] == "20%"
    assert len(window.vehicles_tab.rows) == 2
    assert [row["vin"] for row in window.vehicles_tab.rows] == [VIN_1, VIN_2]

    points = result["data"]["filled"]
    assert points["contract.loadings"] == 1
    assert points["contract.unloadings"] == 1


def test_parse_and_fill_reports_counts(registry):
    """Ответ инструмента сообщает, сколько чего заполнено."""
    result = registry.call("parse_and_fill",
                           {"text": OPERATOR_TEXT, "use_dadata": False})
    counts = result["data"]["counts"]
    assert counts["vehicles"] == 2
    assert counts["points"] == 2
    assert result["data"]["tabs"][0] == "contract_tab"


def test_parse_and_fill_fills_carrier_from_db(registry, window, isolated_db,
                                              carrier):
    """Перевозчик из базы раскладывается по полям формы (A.3, A.5)."""
    result = registry.call(
        "parse_and_fill",
        {"text": "Перевозчик: Ромашка\nСумма: 80000", "use_dadata": False},
    )
    assert result["ok"] is True, result
    print("DBG", result)
    assert window.carrier_tab.data["full_name"] == "ООО «Ромашка»"
    assert window.carrier_tab.data["inn"] == "7701234567"


def test_parse_and_fill_fills_customer(registry, window, isolated_db, customer):
    """Заказчик попадает во вкладку заказчика."""
    result = registry.call("parse_and_fill",
                           {"text": "Заказчик: Заказчик", "use_dadata": False})
    assert result["ok"] is True, result
    assert window.customer_tab.data["full_name"] == "ООО «Заказчик»"


def test_parse_and_fill_uses_vehicle_edits(registry, window):
    """Правка VIN из превью доходит до формы (B.2 → A.5)."""
    result = registry.call("parse_and_fill", {
        "text": f"{VIN_1} JETOUR T2",
        "use_dadata": False,
        "vehicles": [{"vin": VIN_2, "brand_model": "JETOUR T2"}],
    })
    assert result["ok"] is True, result
    assert [row["vin"] for row in window.vehicles_tab.rows] == [VIN_2]


def test_parse_and_fill_uses_field_edits(registry, window):
    """Правка суммы из превью доходит до формы."""
    result = registry.call("parse_and_fill", {
        "text": "Сумма: 80000",
        "use_dadata": False,
        "items": [{"section": "contract", "field": "price_with_vat",
                   "value": "95000"}],
    })
    assert result["ok"] is True, result
    assert window.contract_tab.data["price_with_vat"] == 95000.0


def test_edited_carrier_is_searched_again(registry, window, isolated_db,
                                          carrier):
    """Исправленное название перевозчика ищется в базе заново."""
    result = registry.call("parse_and_fill", {
        "text": "Перевозчик: Ромашкка",
        "use_dadata": False,
        "items": [{"section": "carrier", "field": "full_name",
                   "value": "Ромашка"}],
    })
    assert result["ok"] is True, result
    assert window.carrier_tab.data["inn"] == "7701234567"


def test_empty_text_returns_error(registry):
    """Пустой текст — понятная ошибка, а не пустое заполнение."""
    result = registry.call("parse_and_fill", {"text": "   "})
    assert result["ok"] is False
    assert "текст" in result["error"]


def test_junk_text_returns_error_with_unparsed(registry):
    """Текст без данных — ошибка со списком непонятого."""
    result = registry.call("parse_and_fill",
                           {"text": "Привет!\nКак жизнь?"})
    assert result["ok"] is False
    assert result["unparsed"]


def test_no_window_returns_error():
    """Форма не открыта — инструмент честно об этом говорит."""
    previous = set_context_window(None)
    try:
        reg = build_registry(AgentContext())
        result = reg.call("parse_and_fill",
                          {"text": f"Вины: {VIN_1}", "use_dadata": False})
        assert result["ok"] is False
    finally:
        set_context_window(previous)


def test_points_go_to_contract_tab_not_fields(registry, window):
    """Точки маршрута уходят в таблицы вкладки «Договор» (списки, не поля)."""
    registry.call("parse_and_fill",
                  {"text": OPERATOR_TEXT, "use_dadata": False})
    loadings = window.contract_tab.data["loadings"]
    assert isinstance(loadings, list) and loadings
    assert loadings[0]["address"] == "Москва, Ленина 5"
    assert loadings[0]["date"] == "2026-10-15"


def test_vehicles_are_not_written_field_by_field(registry, window):
    """Машины пишутся СПИСКОМ в таблицу, а не по одному полю."""
    registry.call("parse_and_fill",
                  {"text": OPERATOR_TEXT, "use_dadata": False})
    assert window.vehicles_tab.calls, "fill_data таблицы ТС не вызывался"
    assert isinstance(window.vehicles_tab.calls[-1], list)


# ─────────────────────────────────────────────────────────────
# Живой путь: агент → инструмент → форма
# ─────────────────────────────────────────────────────────────

class ScriptedChat:
    """Подставная модель: сначала вызов инструмента, потом текст."""

    def __init__(self, tool="parse_and_fill", arguments=None):
        self.tool = tool
        self.arguments = dict(arguments or {})
        self.calls = 0
        self.last_result = ""
        self.sent_prompts = []

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.calls += 1
        for message in messages:
            if message.get("role") == "system":
                self.sent_prompts.append(message.get("content") or "")
        for message in reversed(messages):
            if message.get("role") == "tool":
                self.last_result = message.get("content") or ""
                break
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": self.tool,
                        "arguments": json.dumps(self.arguments,
                                                ensure_ascii=False),
                    },
                }],
            }
        return {"role": "assistant", "content": self.last_result or "готово"}


def test_agent_calls_parse_and_fill(window, isolated_db, carrier):
    """Модель зовёт разбор текста — форма заполняется, диалог подтверждён."""
    from core.agent.agent import Agent

    chat = ScriptedChat("parse_and_fill",
                        {"text": OPERATOR_TEXT, "use_dadata": False})
    asked = []

    def confirm(tool, arguments):
        asked.append((tool.name, dict(arguments)))
        return True

    agent = Agent(client=chat,
                  context=AgentContext(window=window, confirm=confirm))
    reply = agent.send(OPERATOR_TEXT)

    assert asked and asked[0][0] == "parse_and_fill"
    assert reply.tool_names == ["parse_and_fill"]
    assert window.contract_tab.data["price_with_vat"] == 80000.0
    assert len(window.vehicles_tab.rows) == 2


def test_agent_declined_fill_changes_nothing(window, isolated_db):
    """Отказ оператора — форма не меняется."""
    from core.agent.agent import Agent

    chat = ScriptedChat("parse_and_fill",
                        {"text": OPERATOR_TEXT, "use_dadata": False})
    agent = Agent(client=chat,
                  context=AgentContext(window=window, confirm=lambda t, a: False))
    reply = agent.send(OPERATOR_TEXT)

    assert window.contract_tab.data["price_with_vat"] == 0.0
    assert window.vehicles_tab.rows == []
    assert reply.calls[0].declined is True


def test_agent_applies_edits_from_confirm(window, isolated_db):
    """
    Правки, добавленные диалогом в аргументы, доходят до формы.

    Именно так работает превью: диалог подтверждения дописывает в аргументы
    ``items`` и ``vehicles``, а инструмент заполняет форму по ним.
    """
    from core.agent.agent import Agent

    chat = ScriptedChat("parse_and_fill",
                        {"text": "Сумма: 80000", "use_dadata": False})

    def confirm(tool, arguments):
        arguments["items"] = [{"section": "contract",
                               "field": "price_with_vat", "value": "95000"}]
        return True

    agent = Agent(client=chat,
                  context=AgentContext(window=window, confirm=confirm))
    agent.send("Сумма: 80000")

    assert window.contract_tab.data["price_with_vat"] == 95000.0


def test_prompt_teaches_text_parsing():
    """Промпт объясняет: текст с данными — разбирать, а не переспрашивать."""
    from core.agent.prompt import build_system_prompt

    prompt = build_system_prompt()
    lowered = prompt.lower()
    assert "вины" in lowered
    assert "превью" in lowered
    assert "разбери" in lowered
    assert "что мне делать с этим текстом" in lowered
