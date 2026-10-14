#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Инструменты Максимилиана (задание, часть H.1).

Что проверяется
---------------
1. **Формат ответа.** Каждый инструмент возвращает ``{"ok": True, "data": …}``
   или ``{"ok": False, "error": …}`` — третьего не дано (задание, часть B.6).
2. **Имена и состав реестра.** Инструментов не меньше двенадцати, имена
   уникальны, у каждого есть описание и схема аргументов.
3. **Поиск находит существующие сущности.** Водитель, перевозчик, заказчик,
   ТС и договор ищутся в изолированной базе по своим ключам.
4. **Опасные действия помечены.** ``fill_field``, ``fill_entity_fields`` и
   ``apply_recognized_fields`` требуют подтверждения; чтение — нет.
5. **Ошибки не роняют агента.** Пустой запрос, неизвестный инструмент,
   неверные аргументы и отсутствие окна дают ``ok: False`` с понятным
   текстом, а не исключение.

Окно подставляется заглушкой (``StubWindow``): инструменты формы работают
с любым объектом, у которого есть вкладки с ``get_data``/``fill_data``,
поэтому Qt для этих тестов не нужен.
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import tools as agent_tools  # noqa: E402
from core.agent.tools import AgentContext, ToolRegistry, build_registry  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Заглушка окна: вкладки с get_data / fill_data
# ─────────────────────────────────────────────────────────────

class StubTab:
    """Вкладка-заглушка: хранит словарь полей и умеет его заполнять."""

    def __init__(self, data=None):
        self.data = dict(data or {})
        self.calls = []

    def get_data(self):
        return dict(self.data)

    def fill_data(self, values=None, *args):
        self.calls.append(dict(values or {}))
        self.data.update(values or {})


class StubTrailerTab(StubTab):
    """Вкладка «Тягач/прицеп»: два блока вместо одного словаря."""

    def __init__(self):
        super().__init__({})
        self.tractor = {}
        self.trailer = {}

    def get_tractor_data(self):
        return dict(self.tractor)

    def get_trailer_data(self):
        return dict(self.trailer)

    def fill_data(self, tractor=None, trailer=None):
        self.calls.append((dict(tractor or {}), dict(trailer or {})))
        self.tractor.update(tractor or {})
        self.trailer.update(trailer or {})


class StubWindow:
    """
    Окно-заглушка с раскладкой MainWindow.

    Имена вкладок — те же, что в ``ui/main_window.py`` (``_tab_keys``), и
    доступ к ним идёт через ``_tab_by_key``: инструменты агента читают
    форму именно так.
    """

    CONTRACT_TYPE = "perevozka"

    def __init__(self):
        self.carrier_tab = StubTab({"full_name": "ООО «Ромашка»", "inn": "7701234567"})
        self.driver_tab = StubTab({"full_name": "Иванов Иван Иванович",
                                   "phone": "+7 900 000-00-00"})
        self.trailer_tab = StubTrailerTab()
        self.vehicles_tab = StubTab([])
        self.contract_tab = StubTab({"number": "23092026-74", "route": "Москва — Пермь"})
        self.customer_tab = StubTab({})

    def _tab_keys(self):
        return (
            "carrier_tab", "driver_tab", "trailer_tab",
            "vehicles_tab", "contract_tab", "customer_tab",
        )

    def _tab_by_key(self, key):
        return getattr(self, key, None)


@pytest.fixture
def window():
    """Окно-заглушка, подставленное инструментам как «открытая форма»."""
    win = StubWindow()
    previous = agent_tools.set_context_window(win)
    yield win
    agent_tools.set_context_window(previous)


@pytest.fixture
def registry(window):
    """Реестр инструментов с окном-заглушкой."""
    return build_registry(AgentContext(window=window, contract_type="perevozka"))


@pytest.fixture
def confirmations():
    """Записывает вопросы подтверждения и отвечает «да» или «нет»."""

    class Recorder:
        def __init__(self):
            self.asked = []
            self.answer = True

        def __call__(self, tool, arguments):
            self.asked.append((tool.name, dict(arguments)))
            return self.answer

    return Recorder()


# ─────────────────────────────────────────────────────────────
# 1. Состав реестра
# ─────────────────────────────────────────────────────────────

def test_registry_has_required_tools(registry):
    """В версии 1 не меньше двенадцати инструментов (задание, часть B)."""
    names = registry.names()
    assert len(names) >= 12, f"инструментов всего {len(names)}"

    expected = {
        "search_driver", "search_carrier", "search_customer", "search_vehicle",
        "search_contract", "list_expiring_contracts",
        "get_current_form", "get_schema", "validate_current_form",
        "fill_field", "fill_from_entity", "fill_entity_fields",
        "recognize_document", "apply_recognized_fields",
        "explain_field", "get_help",
    }
    assert expected.issubset(set(names)), sorted(expected - set(names))
    assert len(names) == len(set(names)), "имена инструментов повторяются"


def test_every_tool_has_description_and_schema(registry):
    """У каждого инструмента есть описание и JSON Schema аргументов."""
    for tool in registry:
        assert tool.description.strip(), f"{tool.name}: пустое описание"
        schema = tool.schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == tool.name
        parameters = schema["function"]["parameters"]
        assert parameters["type"] == "object"
        assert isinstance(parameters.get("properties"), dict)


def test_only_fill_tools_require_confirmation(registry):
    """Подтверждения требуют только инструменты заполнения формы."""
    dangerous = {
        name for name in registry.names()
        if registry.get(name).requires_confirmation
    }
    assert dangerous == {
        "fill_field", "fill_entity_fields", "apply_recognized_fields",
        #: Разбор текста оператора тоже пишет в форму — и подтверждается
        #: диалогом превью (задание, часть 1: «Заполнить? [Да] [Нет]»).
        "parse_and_fill",
    }
    # Чтение и поиск подтверждения не требуют: они ничего не меняют.
    for name in ("search_driver", "get_current_form", "validate_current_form",
                 "explain_field", "get_help", "fill_from_entity",
                 #: Диагностика только читает (задание, часть 3): она не
                 #: «чинит» программу, а собирает отчёт.
                 "diagnose"):
        assert not registry.get(name).requires_confirmation, name


# ─────────────────────────────────────────────────────────────
# 2. Формат ответа и обработка ошибок
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,arguments", [
    ("search_driver", {"query": "Иванов"}),
    ("search_carrier", {"query": "Ромашка"}),
    ("search_customer", {"query": "Заказчик"}),
    ("search_vehicle", {"query": "А123ВС77"}),
    ("search_contract", {"query": "23092026-74"}),
    ("list_expiring_contracts", {"days": 30}),
    ("get_current_form", {}),
    ("get_schema", {}),
    ("validate_current_form", {}),
    ("fill_from_entity", {"entity_type": "driver", "entity_id": 1}),
    ("explain_field", {"field": "inn"}),
    ("get_help", {}),
])
def test_tool_returns_uniform_dict(isolated_db, registry, name, arguments):
    """Каждый инструмент отвечает словарём с ключом ok (задание, часть B.6)."""
    result = registry.call(name, arguments)
    assert isinstance(result, dict)
    assert "ok" in result
    assert result["ok"] is True or "error" in result


def test_unknown_tool_reports_error(registry):
    """Неизвестный инструмент — понятная ошибка, а не исключение."""
    result = registry.call("launch_rocket", {})
    assert result["ok"] is False
    assert "не существует" in result["error"]
    assert "search_driver" in result["available"]


def test_bad_arguments_report_error(registry):
    """Лишний аргумент от модели не роняет инструмент."""
    result = registry.call("explain_field", {"field": "inn", "colour": "рыжий"})
    assert result["ok"] is False
    assert "аргументы" in result["error"]


def test_unknown_entity_type_reports_error(isolated_db, registry):
    """Неизвестный тип сущности — ошибка с подсказкой о допустимых."""
    result = registry.call("fill_from_entity",
                           {"entity_type": "космолёт", "entity_id": 1})
    assert result["ok"] is False
    assert "driver" in result["error"]


def test_form_tools_without_window_report_error():
    """Без открытой формы инструменты формы отвечают ошибкой, не падают."""
    previous = agent_tools.set_context_window(None)
    try:
        registry = build_registry(AgentContext(window=None))
        for name, arguments in (
            ("get_current_form", {}),
            ("validate_current_form", {}),
            ("fill_field", {"field": "inn", "value": "7701234567"}),
        ):
            result = registry.call(name, arguments)
            assert result["ok"] is False, name
            assert "форма" in result["error"] or "открыт" in result["error"]
    finally:
        agent_tools.set_context_window(previous)


# ─────────────────────────────────────────────────────────────
# 3. Поиск находит существующие сущности
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def filled_db(isolated_db, driver_data, organization_data):
    """
    База с одним водителем, перевозчиком, заказчиком и ТС.

    Фикстура сама подставляет «открытое окно» инструментам формы: агент
    достаёт окно через core.agent.tools (_CONTEXT_WINDOW), и без этого
    инструменты формы отвечали бы «форма не открыта». Предыдущее значение
    восстанавливается — окно мог подставить другой тест.
    """
    from db.crud.drivers import save_driver
    from db.crud.organizations import save_organization
    from db.crud.vehicles import save_driver_vehicle, save_vehicles

    driver_id = save_driver(dict(driver_data))
    carrier_id = save_organization(dict(organization_data), is_carrier=True)
    customer = dict(organization_data)
    customer["full_name"] = "ООО «Заказчик»"
    customer["short_name"] = "ООО «Заказчик»"
    customer["inn"] = "7709876543"
    customer_id = save_organization(customer, is_carrier=False)
    save_driver_vehicle(driver_id, {
        "tractor_brand": "Foton Auman", "tractor_plate": "O844XY196",
        "tractor_color": "Белый", "tractor_year": "2023",
    })
    save_vehicles([{
        "vin": "EC3TEUMB0T0002608", "brand_model": "JETOUR T2",
        "plate_number": "А123ВС77", "year": 2024, "color": "Белый",
        "vehicle_type": "Легковой автомобиль",
    }], carrier_id=carrier_id)

    previous = agent_tools.set_context_window(StubWindow())
    try:
        yield {"driver": driver_id, "carrier": carrier_id, "customer": customer_id}
    finally:
        agent_tools.set_context_window(previous)


@pytest.fixture
def db_registry(filled_db):
    """Реестр инструментов для тестов, которым нужна база с записями."""
    return build_registry(AgentContext(contract_type="perevozka"))


def test_search_driver_finds_by_name(isolated_db, registry, filled_db):
    """Водитель находится по фамилии, и у записи есть id для fill_from_entity."""
    result = registry.call("search_driver", {"query": "Иванов"})
    assert result["ok"] is True
    assert result["count"] >= 1
    found = result["data"][0]
    assert found["id"] == filled_db["driver"]
    assert "Иванов" in found["full_name"]


def test_search_carrier_finds_by_inn(isolated_db, registry, filled_db):
    """Перевозчик находится по ИНН."""
    result = registry.call("search_carrier", {"query": "7701234567"})
    assert result["ok"] is True
    assert result["data"][0]["id"] == filled_db["carrier"]


def test_search_customer_finds_by_name(isolated_db, registry, filled_db):
    """Заказчик находится по названию и не путается с перевозчиком."""
    result = registry.call("search_customer", {"query": "Заказчик"})
    assert result["ok"] is True
    assert result["data"][0]["id"] == filled_db["customer"]


def test_search_vehicle_finds_contract_and_driver_vehicles(isolated_db, registry,
                                                           filled_db):
    """ТС ищется и в машинах договора, и в тягаче водителя."""
    contract_car = registry.call("search_vehicle", {"query": "А123ВС77"})
    assert contract_car["ok"] is True
    assert contract_car["data"][0]["source"] == "vehicles"

    tractor = registry.call("search_vehicle", {"query": "O844XY196"})
    assert tractor["ok"] is True
    assert tractor["data"][0]["source"] == "driver_vehicles"


def test_search_contract_by_number(isolated_db, registry, filled_db):
    """Договор находится по номеру и отдаёт имена сторон."""
    from db.crud.contracts import save_contract

    save_contract({
        "number": "23092026-74", "date": "2026-09-23", "route": "Мурманск — Пятигорск",
        "driver_id": filled_db["driver"], "carrier_id": filled_db["carrier"],
        "customer_id": filled_db["customer"],
    })
    result = registry.call("search_contract", {"query": "23092026-74"})
    assert result["ok"] is True
    assert result["data"][0]["number"] == "23092026-74"
    assert "Иванов" in result["data"][0]["driver"]


def test_empty_query_reports_error(isolated_db, registry):
    """Пустой поисковый запрос — ошибка с объяснением, что указать."""
    result = registry.call("search_driver", {"query": "   "})
    assert result["ok"] is False
    assert "пустой запрос" in result["error"]


# ─────────────────────────────────────────────────────────────
# 4. Форма: чтение
# ─────────────────────────────────────────────────────────────

def test_get_current_form_reads_filled_fields_only(registry):
    """Чтение формы отдаёт заполненные поля и не показывает пустые."""
    result = registry.call("get_current_form", {})
    assert result["ok"] is True
    sections = result["data"]
    assert sections["driver_tab"]["full_name"] == "Иванов Иван Иванович"
    # Пустой раздел не превращается в набор пустых строк.
    assert "customer_tab" not in sections or sections["customer_tab"] == {}


def test_get_current_form_reads_one_section(registry):
    """Раздел можно назвать по-русски: «водитель»."""
    result = registry.call("get_current_form", {"section": "водитель"})
    assert result["ok"] is True
    assert result["data"]["full_name"] == "Иванов Иван Иванович"


def test_get_current_form_unknown_section(registry):
    """Неизвестный раздел — ошибка со списком доступных."""
    result = registry.call("get_current_form", {"section": "бухгалтерия"})
    assert result["ok"] is False
    assert "available" in result


def test_validate_current_form_reports_missing_fields(registry):
    """Проверка формы отвечает разделами errors/warnings (только чтение)."""
    result = registry.call("validate_current_form", {})
    assert result["ok"] is True
    data = result["data"]
    assert set(data) >= {"errors", "warnings", "is_clean"}
    assert isinstance(data["errors"], list)
    # Форма заведомо неполная: водитель без паспорта не пройдёт проверку.
    assert data["is_clean"] is False


def test_get_schema_lists_sections(registry, window):
    """Схема показывает разделы открытой формы и известные типы."""
    result = registry.call("get_schema", {"contract_type": "perevozka"})
    assert result["ok"] is True
    data = result["data"]
    assert "driver_tab" in data["sections"]
    assert "perevozka" in data["known_types"]


# ─────────────────────────────────────────────────────────────
# 5. Форма: заполнение
# ─────────────────────────────────────────────────────────────

def test_fill_field_writes_value(registry, window):
    """fill_field записывает значение в поле формы."""
    result = registry.call("fill_field", {"field": "driver_tab.full_name",
                                          "value": "Петров Пётр Петрович"})
    assert result["ok"] is True
    assert window.driver_tab.data["full_name"] == "Петров Пётр Петрович"


def test_fill_field_without_section_finds_field(registry, window):
    """Имя поля можно дать без раздела: инструмент найдёт его сам."""
    result = registry.call("fill_field", {"field": "route", "value": "Москва — Казань"})
    assert result["ok"] is True
    assert window.contract_tab.data["route"] == "Москва — Казань"


def test_fill_field_unknown_field_reports_error(registry):
    """Несуществующее поле — ошибка, форма не меняется."""
    result = registry.call("fill_field", {"field": "driver_tab.magic", "value": "x"})
    assert result["ok"] is False
    assert "details" in result


def test_fill_entity_fields_fills_many(registry, window):
    """Набор полей раскладывается по вкладкам одной операцией."""
    result = registry.call("fill_entity_fields", {"fields": {
        "driver_tab.full_name": "Сидоров Сидор Сидорович",
        "carrier_tab.inn": "7712345678",
        "route": "Пермь — Сочи",
    }})
    assert result["ok"] is True
    assert window.driver_tab.data["full_name"] == "Сидоров Сидор Сидорович"
    assert window.carrier_tab.data["inn"] == "7712345678"
    assert window.contract_tab.data["route"] == "Пермь — Сочи"


def test_fill_entity_fields_accepts_tractor_and_trailer_blocks(registry, window):
    """
    Блоки тягача и полуприцепа из fill_from_entity уходят на свою вкладку.

    Это стык двух инструментов: fill_from_entity отдаёт их отдельными
    ключами, fill_entity_fields принимает отдельными аргументами.
    """
    result = registry.call("fill_entity_fields", {
        "fields": {"driver_tab.full_name": "Сидоров Сидор Сидорович"},
        "tractor": {"plate_number": "O844XY196"},
        "trailer": {"plate_number": "71ABF18"},
    })
    assert result["ok"] is True
    assert window.trailer_tab.tractor["plate_number"] == "O844XY196"
    assert window.trailer_tab.trailer["plate_number"] == "71ABF18"


def test_fill_tractor_block_goes_to_trailer_tab(registry, window):
    """Поля тягача уходят во вкладку «Тягач/прицеп», а не в водителя."""
    result = registry.call("fill_entity_fields", {"fields": {
        "tractor.plate_number": "O844XY196",
        "trailer.plate_number": "71ABF18",
    }})
    assert result["ok"] is True
    assert window.trailer_tab.tractor["plate_number"] == "O844XY196"
    assert window.trailer_tab.trailer["plate_number"] == "71ABF18"


def test_fill_from_entity_returns_fields_without_touching_form(isolated_db, db_registry,
                                                               filled_db):
    """fill_from_entity только готовит набор полей: форма не меняется."""
    window = agent_tools._CONTEXT_WINDOW
    before = dict(window.driver_tab.data)
    result = db_registry.call("fill_from_entity", {
        "entity_type": "driver", "entity_id": filled_db["driver"],
    })
    assert result["ok"] is True
    data = result["data"]
    assert data["target"] == "driver_tab"
    assert data["fields"]["full_name"] == "Иванов Иван Иванович"
    assert window.driver_tab.data == before, "форма не должна меняться до подтверждения"


def test_fill_from_entity_includes_vehicle_of_driver(isolated_db, db_registry, filled_db):
    """Вместе с водителем приходит его тягач — отдельным блоком."""
    result = db_registry.call("fill_from_entity", {
        "entity_type": "driver", "entity_id": filled_db["driver"],
    })
    data = result["data"]
    assert data["tractor"]["plate_number"] == "O844XY196"


def test_fill_from_entity_carrier(isolated_db, db_registry, filled_db):
    """Перевозчик подтягивается вместе с реквизитами."""
    result = db_registry.call("fill_from_entity", {
        "entity_type": "carrier", "entity_id": filled_db["carrier"],
    })
    assert result["ok"] is True
    fields = result["data"]["fields"]
    assert fields["inn"] == "7701234567"
    assert result["data"]["target"] == "carrier_tab"


def test_fill_from_entity_missing_record(isolated_db, db_registry):
    """Нет записи с таким ID — честная ошибка."""
    result = db_registry.call("fill_from_entity", {"entity_type": "driver",
                                                   "entity_id": 999999})
    assert result["ok"] is False
    assert "не найден" in result["error"]


# ─────────────────────────────────────────────────────────────
# 6. Справочные инструменты
# ─────────────────────────────────────────────────────────────

def test_explain_field_knows_known_fields(registry):
    """Объяснение есть для ключевых полей формы."""
    for field in ("inn", "passport_series", "route", "vehicle_type"):
        result = registry.call("explain_field", {"field": field})
        assert result["ok"] is True, field
        assert len(result["data"]["explanation"]) > 20


def test_explain_field_accepts_russian_title(registry):
    """Поле можно назвать по-русски, как в интерфейсе."""
    result = registry.call("explain_field", {"field": "Расчётный счёт"})
    assert result["ok"] is True
    assert result["data"]["field"] == "bank_account"


def test_explain_field_unknown_reports_error(registry):
    """Неизвестное поле — ошибка со списком известных."""
    result = registry.call("explain_field", {"field": "печать_директора"})
    assert result["ok"] is False
    assert "known" in result


def test_get_help_returns_topics(registry):
    """Справка отдаёт обзор тем и текст по теме."""
    overview = registry.call("get_help", {})
    assert overview["ok"] is True
    assert "типы" in overview["data"]["topics"]

    topic = registry.call("get_help", {"topic": "распознавание"})
    assert topic["ok"] is True
    assert "PDF" in topic["data"]["text"]


# ─────────────────────────────────────────────────────────────
# 7. Документы
# ─────────────────────────────────────────────────────────────

def test_recognize_document_missing_file(registry):
    """Несуществующий файл — ошибка, а не исключение."""
    result = registry.call("recognize_document",
                           {"file_path": "нет-такого-файла.pdf"})
    assert result["ok"] is False
    assert "не найден" in result["error"]


def test_recognize_document_without_path(registry):
    """Пустой путь — ошибка с объяснением."""
    result = registry.call("recognize_document", {"file_path": "  "})
    assert result["ok"] is False
    assert "не указан путь" in result["error"]


def test_apply_recognized_fields_accepts_sections(registry, window):
    """Распознанные поля переносятся в форму по разделам."""
    result = registry.call("apply_recognized_fields", {"sections": {
        "driver": {"full_name": "Кузнецов Кузьма Кузьмич"},
        "contract": {"number": "01102026-5"},
    }})
    assert result["ok"] is True
    assert window.driver_tab.data["full_name"] == "Кузнецов Кузьма Кузьмич"
    assert window.contract_tab.data["number"] == "01102026-5"


def test_apply_recognized_fields_without_data(registry):
    """Пустой перенос — ошибка, форму не трогаем."""
    result = registry.call("apply_recognized_fields", {})
    assert result["ok"] is False
    assert "не переданы поля" in result["error"]


# ─────────────────────────────────────────────────────────────
# 8. Договоры: сроки
# ─────────────────────────────────────────────────────────────

def test_list_expiring_contracts_filters_by_end_date(isolated_db, registry):
    """Инструмент истекающих договоров отдаёт только попадающие в горизонт."""
    from datetime import date, timedelta

    from db.crud.contracts import save_contract

    today = date.today()
    save_contract({"number": "СКОРО-1",
                   "end_date": (today + timedelta(days=5)).isoformat()})
    save_contract({"number": "ПОТОМ-1",
                   "end_date": (today + timedelta(days=400)).isoformat()})
    save_contract({"number": "ПРОШЛО-1",
                   "end_date": (today - timedelta(days=5)).isoformat()})

    result = registry.call("list_expiring_contracts", {"days": 30})
    assert result["ok"] is True
    numbers = [row["number"] for row in result["data"]]
    assert numbers == ["СКОРО-1"]


def test_list_expiring_contracts_rejects_bad_days(registry):
    """Некорректный горизонт — ошибка с примером."""
    result = registry.call("list_expiring_contracts", {"days": "много"})
    assert result["ok"] is False
    assert "числом дней" in result["error"]


# ─────────────────────────────────────────────────────────────
# 9. Подтверждение: связка с агентом (проверяется и в test_agent_core.py)
# ─────────────────────────────────────────────────────────────

def test_confirmation_text_hides_long_values(registry):
    """Вопрос подтверждения не пересказывает значение поля целиком."""
    tool = registry.get("fill_field")
    tool_name, arguments = "fill_field", {
        "field": "special_conditions",
        "value": "очень длинное значение " * 20,
    }
    text = tool.confirmation_text(arguments)
    assert "special_conditions" in text
    assert len(text) < 300, "в диалоге не должно быть полотна значения"
