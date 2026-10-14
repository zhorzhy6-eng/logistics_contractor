#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проактивность Максимилиана (задание, часть 4, тесты).

Что проверяется
---------------
1. **Обязательные поля.** Списки по типам договоров, «чего не хватает»,
   проверка только тех вкладок, которые форма реально отдаёт.
2. **Что дальше.** Порядок подсказок: сначала обязательные, потом полезные
   необязательные; пустая форма даёт понятный текст.
3. **Сроки.** Предупреждения о ВУ водителей и договорах — из готовых
   списков базы (сами списки проверяются в tests/test_agent_analytics_tools.py
   и в тестах db/crud).
4. **Привычки.** «Что обычно делаешь тут»: последнее действие, привычки
   раздела, пустая история — пустая подсказка.
5. **Ненавязчивость.** Не больше одной подсказки в интервал; выключенные
   подсказки не показываются; один и тот же текст не повторяется.
6. **Инструменты агента.** Четыре инструмента зарегистрированы, только
   чтение, отвечают в общем формате и не роняют агента без формы.

База — изолированная (фикстура ``isolated_db``): рабочая contracts.db не
читается и не пишется.
"""

import os
import sys
from datetime import date
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import proactivity, proactivity_tools  # noqa: E402
from core.agent.proactivity import HabitMemory, SuggestionEngine  # noqa: E402
from core.agent.tools import AgentContext, build_registry  # noqa: E402
from db.connection import get_connection  # noqa: E402

PROACTIVE_TOOLS = (
    "suggest_next_field", "check_required", "warn_expiring",
    "suggest_from_history",
)


class StubTab:
    """Вкладка-заглушка: отдаёт словарь полей."""

    def __init__(self, data=None):
        self.data = dict(data or {})

    def get_data(self):
        return dict(self.data)


class StubWindow:
    """Окно-заглушка «Экспедиторства»: раскладка как у MainWindow."""

    CONTRACT_TYPE = "perevozka"

    def __init__(self, **sections):
        self.carrier_tab = StubTab(sections.get("carrier_tab", {}))
        self.driver_tab = StubTab(sections.get("driver_tab", {}))
        self.trailer_tab = StubTab(sections.get("trailer_tab", {}))
        self.vehicles_tab = StubTab(sections.get("vehicles_tab", []))
        self.contract_tab = StubTab(sections.get("contract_tab", {}))
        self.customer_tab = StubTab(sections.get("customer_tab", {}))

    def _tab_keys(self):
        return (
            "carrier_tab", "driver_tab", "trailer_tab",
            "vehicles_tab", "contract_tab", "customer_tab",
        )

    def _tab_by_key(self, key):
        return getattr(self, key, None)


@pytest.fixture
def context():
    """Контекст агента с окном-заглушкой."""
    window = StubWindow(
        carrier_tab={"full_name": "ООО «Ромашка»"},
        driver_tab={"full_name": "Иванов Иван Иванович"},
        contract_tab={"number": "1", "route": {"route": "Москва — Пермь"}},
    )
    return AgentContext(window=window, contract_type="perevozka")


@pytest.fixture
def isolated_memory_db(isolated_db):
    """Изолированная база (нужна инструментам сроков и привычек)."""
    return isolated_db


# ─────────────────────────────────────────────────────────────
# 1. Обязательные поля
# ─────────────────────────────────────────────────────────────

def test_required_fields_perevozka_has_carrier_and_driver():
    """У Экспедиторства обязательны перевозчик, водитель и договор."""
    fields = proactivity.required_fields("perevozka")
    assert "carrier_tab" in fields
    assert "driver_tab" in fields
    assert "contract_tab" in fields
    names = [name for name, _title in fields["driver_tab"]]
    assert "full_name" in names


def test_required_fields_are_per_contract_type():
    """У аренды свои обязательные разделы (арендодатель, экипаж)."""
    fields = proactivity.required_fields("arenda_ts")
    assert "lessor_tab" in fields
    assert "crew_tab" in fields
    assert "carrier_tab" not in fields


def test_required_fields_unknown_type_falls_back():
    """Неизвестный тип договора получает общий набор, а не пустоту."""
    assert proactivity.required_fields("такого-типа-нет") == \
        proactivity.required_fields("perevozka")


def test_missing_required_finds_empty_fields():
    """Незаполненные обязательные поля видны списком с подписью."""
    sections = {
        "carrier_tab": {"full_name": "ООО «Ромашка»", "inn": ""},
        "contract_tab": {"number": "1", "date": "", "route": {"route": ""}},
    }
    missing = proactivity.missing_required(sections, "perevozka")
    titles = [item["title"] for item in missing]
    assert "ИНН перевозчика" in titles
    assert "дата договора" in titles
    assert "маршрут" in titles
    assert "наименование перевозчика" not in titles


def test_missing_required_ignores_absent_sections():
    """Раздела нет в форме — его поля не считаются пропущенными."""
    sections = {"carrier_tab": {"full_name": "ООО «Ромашка»", "inn": "7701234567"}}
    missing = proactivity.missing_required(sections, "perevozka")
    assert [item["section"] for item in missing] == []


def test_missing_required_only_sections():
    """Проверку можно ограничить одним разделом."""
    sections = {
        "carrier_tab": {"full_name": "", "inn": ""},
        "driver_tab": {"full_name": "", "license_number": "", "phone": ""},
    }
    missing = proactivity.missing_required(
        sections, "perevozka", only_sections=["driver_tab"]
    )
    assert {item["section"] for item in missing} == {"driver_tab"}


def test_missing_required_empty_vehicle_list():
    """Пустая таблица машин — это тоже незаполненные обязательные поля."""
    sections = {"vehicles_tab": []}
    missing = proactivity.missing_required(sections, "perevozka")
    assert {item["field"] for item in missing} == {"vin", "brand_model"}


def test_missing_required_non_empty_vehicle_list():
    """Машины есть — обязательные поля ТС считаются заполненными."""
    sections = {"vehicles_tab": [{"vin": "XTA21150053946127"}]}
    assert proactivity.missing_required(sections, "perevozka") == []


def test_missing_required_zero_is_empty():
    """Ноль в денежном поле — «не заполнено» (оператор не вводит его сам)."""
    sections = {
        "contract_tab": {"number": "1", "date": "2026-10-01",
                         "route": {"route": "Москва — Пермь",
                                   "price_with_vat": 0}},
    }
    missing = proactivity.missing_required(sections, "perevozka")
    assert [item["title"] for item in missing] == ["сумма договора"]


# ─────────────────────────────────────────────────────────────
# 2. Что дальше
# ─────────────────────────────────────────────────────────────

def test_next_field_prefers_required():
    """Сначала предлагается обязательное поле, а не полезное необязательное."""
    sections = {
        "driver_tab": {"full_name": "Иванов", "license_number": "",
                       "phone": ""},
    }
    result = proactivity.next_field(sections, "perevozka")
    assert result["next"]["section"] == "driver_tab"
    assert result["next"]["reason"] == "обязательное поле"
    assert result["next"]["title"] in ("номер водительского удостоверения",
                                       "телефон водителя")


def test_next_field_order_follows_form():
    """Разделы идут в порядке заполнения формы: перевозчик раньше договора."""
    sections = {
        "carrier_tab": {"full_name": "", "inn": ""},
        "contract_tab": {"number": "", "date": "", "route": {}},
    }
    result = proactivity.next_field(sections, "perevozka")
    assert result["next"]["section"] == "carrier_tab"


def test_next_field_text_is_human():
    """Текст подсказки называет поле и раздел по-русски."""
    sections = {"driver_tab": {"full_name": "", "license_number": "", "phone": ""}}
    text = proactivity.next_field(sections, "perevozka")["text"]
    assert "Дальше логично заполнить" in text
    assert "Водитель" in text


def test_next_field_complete_form():
    """Когда всё заполнено, подсказка это и говорит."""
    sections = {
        "carrier_tab": {"full_name": "ООО «Ромашка»", "inn": "7701234567",
                        "kpp": "770101001", "legal_address": "Москва",
                        "director_name": "Иванов"},
        "driver_tab": {"full_name": "Иванов", "license_number": "77 АА 123456",
                       "phone": "+79000000000", "passport_number": "2815 634512",
                       "license_expiry_date": "2030-01-01"},
        "customer_tab": {"full_name": "ООО «Заказчик»", "inn": "7712345678",
                         "kpp": "771201001", "legal_address": "Тверь"},
        "contract_tab": {"number": "1", "date": "2026-10-01",
                         "start_date": "2026-10-02", "end_date": "2026-10-20",
                         "route": {"route": "Москва — Пермь",
                                   "price_with_vat": 120000}},
    }
    result = proactivity.next_field(sections, "perevozka")
    assert result["next"] is None
    assert "заполнено всё" in result["text"]


def test_recommended_missing_lists_optional_fields():
    """Необязательные, но полезные поля тоже предлагаются."""
    sections = {"driver_tab": {"full_name": "Иванов", "license_number": "77",
                               "phone": "+7", "passport_number": "",
                               "license_expiry_date": ""}}
    recommended = proactivity.recommended_missing(sections)
    assert {item["field"] for item in recommended} == {
        "passport_number", "license_expiry_date"
    }
    assert all(item["reason"] == "обычно заполняют" for item in recommended)


def test_section_title_russian():
    """Названия разделов — как в интерфейсе."""
    assert proactivity.section_title("driver_tab") == "Водитель"
    assert proactivity.section_title("vehicles_tab") == "Перевозимые ТС"
    assert proactivity.section_title("неизвестный") == "неизвестный"


# ─────────────────────────────────────────────────────────────
# 3. Сроки
# ─────────────────────────────────────────────────────────────

def test_warn_items_empty():
    """Ничего не истекает — так и написано."""
    result = proactivity.warn_items([], [], days=30)
    assert result["count"] == 0
    assert "Ничего не истекает" in result["text"]


def test_warn_items_licence_and_contract():
    """ВУ и договоры попадают в предупреждения разными видами."""
    licences = [{"id": 1, "driver_name": "Иванов Иван",
                 "license_expiry": "2026-10-20"}]
    contracts = [{"id": 7, "contract_number": "42", "end_date": "2026-10-30",
                  "route": "Москва — Пермь"}]
    result = proactivity.warn_items(licences, contracts, days=30)
    kinds = {item["kind"]: item for item in result["warnings"]}
    assert kinds["driver_license"]["count"] == 1
    assert kinds["contract"]["count"] == 1
    assert result["count"] == 2
    assert "водительские удостоверения" in result["text"]
    assert "Иванов Иван" in result["text"]


def test_warn_expiring_tool_drivers_only(isolated_memory_db):
    """Инструмент отвечает по водителям, когда так и просят."""
    result = proactivity_tools.warn_expiring(
        days=30, entity_type="drivers", today="2026-10-15"
    )
    assert result["ok"] is True
    assert result["data"]["count"] == 0
    assert all(item["kind"] == "driver_license"
               for item in result["data"]["warnings"])


def test_warn_expiring_rejects_unknown_kind():
    """Непонятно, что проверять — ошибка со списком вариантов."""
    result = proactivity_tools.warn_expiring(entity_type="погода")
    assert result["ok"] is False
    assert result["available"] == ["all", "drivers", "contracts"]


# ─────────────────────────────────────────────────────────────
# 4. Привычки и история
# ─────────────────────────────────────────────────────────────

def test_habit_memory_counts_frequent():
    """Привычка: считает, что оператор делает здесь чаще всего."""
    memory = HabitMemory()
    for _ in range(3):
        memory.remember("Загрузить в форму", section="driver_tab")
    memory.remember("Распознать", section="driver_tab")
    frequent = memory.frequent(section="driver_tab")
    assert frequent[0] == {"action": "Загрузить в форму", "count": 3}


def test_habit_memory_ignores_empty_actions():
    """Пустая надпись в привычки не попадает."""
    memory = HabitMemory()
    memory.remember("", section="driver_tab")
    memory.remember("   ", section="driver_tab")
    assert memory.frequent(section="driver_tab") == []


def test_habit_memory_falls_back_to_window():
    """Нет привычек раздела — берутся привычки окна."""
    memory = HabitMemory()
    memory.remember("Создать договор", window="MainWindow")
    assert memory.frequent(section="contract_tab",
                           window="MainWindow")[0]["action"] == "Создать договор"


def test_habit_memory_limits_size():
    """Список привычек не растёт бесконечно."""
    memory = HabitMemory()
    for index in range(HabitMemory.LIMIT + 10):
        memory.remember(f"Действие {index}", section="driver_tab")
    assert len(memory._by_section["driver_tab"]) == HabitMemory.LIMIT


def test_suggest_from_history_uses_last_action():
    """Последнее действие оператора даёт подсказку «что дальше»."""
    actions = [
        {"action": "Открыть панель", "window": "MainWindow"},
        {"action": "Загрузить в форму", "window": "MainWindow"},
    ]
    result = proactivity.suggest_from_history(actions, section="driver_tab")
    assert "следующие поля" in result["step"]
    assert "Загрузить в форму" in result["text"]
    assert result["source"] == "последнее действие оператора"


def test_suggest_from_history_section_step():
    """Без журнала подсказка берётся из этапа работы с формой."""
    result = proactivity.suggest_from_history([], section="driver_tab")
    assert "тягач" in result["step"]


def test_suggest_from_history_empty():
    """Нечего сказать — подсказка пустая, а не выдуманная."""
    result = proactivity.suggest_from_history([], section="", window="")
    assert result["step"] == ""
    assert "не видно" in result["text"]


def test_suggest_from_history_habits():
    """Привычки раздела дают подсказку, когда шага по разделу нет."""
    memory = HabitMemory()
    for _ in range(2):
        memory.remember("Добавить ТС", section="act_places_tab")
    result = proactivity.suggest_from_history(
        [], section="act_places_tab", memory=memory
    )
    assert "Добавить ТС" in result["step"]
    assert result["source"] == "привычки оператора"


# ─────────────────────────────────────────────────────────────
# 5. Ненавязчивость
# ─────────────────────────────────────────────────────────────

def test_engine_disabled_says_nothing():
    """Выключенные подсказки не показываются."""
    engine = SuggestionEngine(enabled=False)
    assert engine.offer("Не заполнен ИНН") == ""


def test_engine_shows_first_suggestion():
    """Первая подсказка показывается сразу."""
    engine = SuggestionEngine(enabled=True, interval=300)
    assert engine.offer("Не заполнен ИНН") == "Не заполнен ИНН"


def test_engine_waits_interval():
    """Вторая подсказка — не раньше интервала (5 минут по умолчанию)."""
    moment = [1000.0]
    engine = SuggestionEngine(enabled=True, interval=300,
                              clock=lambda: moment[0])
    engine.offer("Первая подсказка")
    moment[0] += 100
    assert engine.offer("Вторая подсказка") == ""
    moment[0] += 250
    assert engine.offer("Вторая подсказка") == "Вторая подсказка"


def test_engine_does_not_repeat_same_text():
    """Один и тот же текст дважды не показывается (это и есть навязчивость)."""
    moment = [0.0]
    engine = SuggestionEngine(enabled=True, interval=0,
                              clock=lambda: moment[0])
    assert engine.offer("Не заполнен ИНН")
    moment[0] += 1000
    assert engine.offer("Не заполнен ИНН") == ""


def test_engine_skips_empty_text():
    """Пустая подсказка не считается показом."""
    engine = SuggestionEngine(enabled=True, interval=300)
    assert engine.offer("") == ""
    assert engine.last_text == ""


def test_engine_configure_and_reset():
    """Настройки меняются на ходу, reset снимает паузу."""
    moment = [0.0]
    engine = SuggestionEngine(enabled=True, interval=300,
                              clock=lambda: moment[0])
    engine.offer("Раз")
    engine.configure(False)
    moment[0] += 1000
    assert engine.offer("Два") == ""
    engine.configure(True)
    engine.reset()
    assert engine.offer("Два") == "Два"


def test_default_interval_is_five_minutes():
    """Задание: не больше одной подсказки в 5 минут."""
    assert proactivity.SUGGESTION_INTERVAL == 300.0
    assert proactivity_tools.engine_from_settings({}).interval == 300.0


def test_engine_from_settings():
    """Движок собирается из раздела настроек агента."""
    engine = proactivity_tools.engine_from_settings(
        {"proactive": True, "proactive_interval": 60}
    )
    assert engine.enabled is True
    assert engine.interval == 60.0


def test_engine_from_settings_bad_interval():
    """Мусор в настройке интервала не ломает движок."""
    engine = proactivity_tools.engine_from_settings(
        {"proactive": True, "proactive_interval": "часто"}
    )
    assert engine.interval == proactivity.SUGGESTION_INTERVAL


# ─────────────────────────────────────────────────────────────
# 6. Инструменты агента
# ─────────────────────────────────────────────────────────────

def test_proactive_tools_registered():
    """Четыре инструмента части зарегистрированы и описаны."""
    registry = build_registry(AgentContext())
    for name in PROACTIVE_TOOLS:
        tool = registry.get(name)
        assert tool is not None, f"{name} не зарегистрирован"
        assert tool.description.strip()
        assert tool.parameters["type"] == "object"


def test_proactive_tools_are_read_only():
    """Подсказки ничего не меняют — подтверждения не требуют."""
    registry = build_registry(AgentContext())
    for name in PROACTIVE_TOOLS:
        assert registry.get(name).requires_confirmation is False, name


def test_suggest_next_field_tool(context):
    """Инструмент «что дальше» отвечает по открытой форме."""
    result = proactivity_tools.suggest_next_field(context)
    assert result["ok"] is True
    assert result["data"]["next"]["section"] in ("driver_tab", "carrier_tab")
    assert result["data"]["missing_count"] >= 1


def test_check_required_tool(context):
    """Инструмент «чего не хватает» перечисляет обязательные поля."""
    result = proactivity_tools.check_required(context)
    assert result["ok"] is True
    assert result["data"]["is_complete"] is False
    assert "Не хватает обязательных полей" in result["data"]["text"]


def test_check_required_tool_section(context):
    """Проверку можно ограничить разделом."""
    result = proactivity_tools.check_required(context, section="водитель")
    assert result["ok"] is True
    assert {item["section"] for item in result["data"]["missing"]} == {"driver_tab"}


def test_check_required_tool_unknown_section(context):
    """Неизвестный раздел — ошибка со списком доступных."""
    result = proactivity_tools.check_required(context, section="погода")
    assert result["ok"] is False
    assert result["available"]


def test_tools_without_form_are_honest():
    """Без открытой формы инструменты честно отвечают, а не падают."""
    empty = AgentContext()
    for tool in (proactivity_tools.suggest_next_field,
                 proactivity_tools.check_required):
        result = tool(empty)
        assert result["ok"] is False
        assert result["error"]

    # «Что обычно делаешь тут» форме не требует: без формы и без журнала
    # инструмент отвечает «не видно», но не ошибкой.
    history = proactivity_tools.suggest_from_history(empty)
    assert history["ok"] is True
    assert history["data"]["step"] == ""


def test_proactive_text_none_without_form(isolated_db):
    """Подсказка без формы и без сроков пустая: панель молчит."""
    from core.agent.tools import set_context_window

    set_context_window(None)
    assert proactivity_tools.proactive_text(AgentContext()) == ""


def test_proactive_text_about_missing_fields(context):
    """Подсказка без вопроса говорит, чего не хватает в форме."""
    text = proactivity_tools.proactive_text(context)
    assert "Не заполнено" in text
    assert "Заполнить" in text


def test_registry_call_suggest_next_field(context):
    """Инструмент работает и через реестр (как его зовёт агент)."""
    registry = build_registry(context)
    result = registry.call("suggest_next_field", {})
    assert result["ok"] is True
    assert "next" in result["data"]
