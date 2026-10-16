#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Симптомы 2–6 приёмки Максимилиана: поиск, память, DaData, план, контекст.

Почему один файл
----------------
Все пять симптомов оказались ОДНОЙ причиной и её следствиями, и проверять их
по отдельности значило бы потерять связь между ними:

  * **Модель передаёт в поиск фразу оператора целиком.** Живой прогон
    (`tests/_tmp/_live_6_dialogs.py`) показал дословно: на «Заполни Ромашку»
    в инструмент уходило ``query="Заполни Ромашку"``, и поиск честно искал
    подстроку «заполни ромашку» — не находил ничего. Отсюда «Не нашёл
    организацию "Ромашка" ни в одном справочнике» (часть 2) и «Не нашёл
    "Ромашку" в базе. Уточните название или передайте ID» (часть 3).
  * **Память не училась на поиске.** Алиасы записывались только по прямой
    просьбе («запомни: …»), поэтому повторный запрос ничего не менял.
  * **Сверка с ЕГРЮЛ искала запись по названию, но только по точному**:
    ID оператор не знает, и просить его нельзя.
  * **«Какой перевозчик открыт»** отдавал имя вкладки: значения ключевого
    поля в ответе инструмента не было вовсе.
  * **«Заполни 10 полей»** упиралось в отсутствие правила «посмотри форму
    и предложи конкретное».

Что проверяется
---------------
1. Разбор фразы поиска (`words.search_terms`) и его применение в
   инструментах: «Заполни Ромашку» → ищем «Ромашку».
2. Запись алиаса по успешному поиску и чтение алиаса при повторе.
3. Повтор неудачного поиска по памяти внутри цикла агента.
4. DaData: сверка находит запись по названию, в том числе по алиасу.
5. Промпт: правила «делай, не планируй», «смотри форму, а не вкладку»,
   «не проси ID», «сколько заполнить — посчитай пустые».
6. Контекст: `current_subject` в `get_form_state` и `get_current_window`.

База — изолированная (фикстура ``isolated_db``), модель — подставная.
"""

import json
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

from core.agent import long_memory  # noqa: E402
from core.agent.words import search_terms  # noqa: E402
from db.crud import agent_memory  # noqa: E402


@pytest.fixture
def memory_db(isolated_db):
    """Изолированная база с таблицами памяти."""
    assert agent_memory.ensure_tables()
    return isolated_db


@pytest.fixture(autouse=True)
def clean_memory(memory_db):
    """Перед каждым тестом память пуста."""
    agent_memory.clear()
    yield
    agent_memory.clear()


@pytest.fixture
def carrier(memory_db):
    """Перевозчик «ООО «Ромашка-Плюс»» в базе."""
    from db.crud.organizations import save_organization

    return save_organization(
        {"full_name": "ООО «Ромашка-Плюс»", "short_name": "Ромашка-Плюс",
         "inn": "7701002003", "kpp": "770101001", "ogrn": "1027700000001",
         "legal_address": "г Москва, ул Тестовая, д 1"}, is_carrier=True,
    )


@pytest.fixture
def customer(memory_db):
    """Заказчик «ООО «Ромашка-Плюс»» в базе (для поиска заказчиков)."""
    from db.crud.organizations import save_organization

    return save_organization(
        {"full_name": "ООО «Ромашка-Плюс»", "short_name": "Ромашка-Плюс",
         "inn": "7701002003", "kpp": "770101001", "ogrn": "1027700000001",
         "legal_address": "г Москва, ул Тестовая, д 1"}, is_carrier=False,
    )


class ScriptedChat:
    """
    Подставная модель: один вызов инструмента, затем текст.

    Так ведёт себя настоящий GigaChat на «Заполни Ромашку»: сначала
    `search_anything`, потом ответ словами. Аргументы задаются тестом —
    именно их и надо проверить.
    """

    def __init__(self, tool="search_anything", arguments=None):
        self.tool = tool
        self.arguments = dict(arguments or {})
        self.calls = 0
        self.last_result = ""
        #: Что ушло в модель (проверка маскирования памяти).
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


def ask(message, tool="search_anything", arguments=None):
    """
    Прогоняет один запрос через агента с подставной моделью.

    По умолчанию модель передаёт в инструмент ФРАЗУ ОПЕРАТОРА ЦЕЛИКОМ —
    именно так ведёт себя настоящий GigaChat (проверено живьём).
    """
    from core.agent.agent import Agent
    from core.agent.tools import AgentContext

    chat = ScriptedChat(tool, arguments if arguments is not None
                        else {"query": message})
    agent = Agent(client=chat, context=AgentContext(), max_tool_steps=6)
    reply = agent.send(message)
    return reply, chat


def tool_payload(chat):
    """Разбор ответа инструмента, который увидела модель."""
    try:
        return json.loads(chat.last_result)
    except (TypeError, ValueError):
        return {}


# ─────────────────────────────────────────────────────────────
# 1. Разбор фразы поиска (общая причина всех симптомов)
# ─────────────────────────────────────────────────────────────

def test_search_terms_drops_command_words():
    """«Заполни Ромашку» → искать надо «Ромашку», а не фразу целиком."""
    assert search_terms("Заполни Ромашку") == ["Ромашку"]
    assert search_terms("заполни Ромашку") == ["Ромашку"]
    assert search_terms("Ромашка") == ["Ромашка"]


def test_search_terms_puts_name_before_role_word():
    """«найди водителя Иванова» → сперва фамилия, потом слово-роль."""
    terms = search_terms("найди водителя Иванова")
    assert terms[0] == "Иванова"
    assert "водителя" in terms


def test_search_terms_drops_prepositions_and_fillers():
    """Предлоги и частицы в запрос не попадают."""
    assert search_terms("сверь Ромашку с ЕГРЮЛ")[0] == "Ромашку"
    terms = search_terms("договоры с Ромашкой")
    assert "с" not in terms
    assert terms[0] == "Ромашкой"


def test_search_terms_empty_for_pure_command():
    """Одна команда без названия — искать нечего."""
    for message in ("Отмени", "Заполни", "Найди", "Покажи", "ну", "и"):
        assert search_terms(message) == [], message


def test_search_anything_accepts_operator_phrase(carrier):
    """Инструмент поиска сам снимает слово-команду (часть 2)."""
    from core.agent.tools import build_registry, AgentContext

    registry = build_registry(AgentContext())
    result = registry.call("search_anything", {"query": "Заполни Ромашку"})
    assert result["ok"] is True
    assert result["count"] == 1, "фраза целиком не должна ломать поиск"
    assert result["query"] == "Ромашку"


def test_search_tools_clean_term(carrier, customer):
    """То же для остальных поисковых инструментов."""
    from core.agent.tools import build_registry, AgentContext

    registry = build_registry(AgentContext())
    for name in ("search_carrier", "search_customer"):
        result = registry.call(name, {"query": "найди Ромашку"})
        assert result["ok"] is True, name
        assert result["count"] == 1, f"{name}: {result}"
        assert result["query"] == "Ромашку", name


def test_pure_command_still_refused():
    """«Отмени» по-прежнему не ищется в базе (проблема #12 не вернулась)."""
    from core.agent.tools import build_registry, AgentContext

    registry = build_registry(AgentContext())
    result = registry.call("search_anything", {"query": "Отмени"})
    assert result["ok"] is False
    assert result.get("looks_like_command") is True


# ─────────────────────────────────────────────────────────────
# 2. Память: запись и чтение алиасов (часть 2)
# ─────────────────────────────────────────────────────────────

def test_agent_records_alias_from_successful_search(carrier):
    """Успешный поиск учит память: «Ромашку» → «ООО «Ромашка-Плюс»»."""
    reply, _chat = ask("Заполни Ромашку")
    assert reply.tool_names == ["search_anything"]

    records = long_memory.list_records()
    assert len(records) == 1, (records, agent_memory.count())
    assert records[0]["kind"] == "alias"
    assert records[0]["key"] == "Ромашку"
    assert records[0]["value"] == "ООО «Ромашка-Плюс»"


def test_alias_key_is_what_operator_said_not_what_model_sent(carrier):
    """
    Ключ алиаса — слово ОПЕРАТОРА, а не аргумент модели.

    Модель передала фразу целиком («Заполни Ромашку»), оператор сказал
    «Ромашку»: запоминать надо второе, иначе память засорится фразами.
    """
    ask("Заполни Ромашку", arguments={"query": "Заполни Ромашку"})
    keys = [item["key"] for item in long_memory.list_records()]
    assert keys == ["Ромашку"], keys


def test_alias_is_read_back(carrier):
    """Алиас читается по исходному слову и по падежу."""
    long_memory.remember_alias("Ромашку", "ООО «Ромашка-Плюс»")
    assert long_memory.find_alias("Ромашку") == "ООО «Ромашка-Плюс»"
    assert long_memory.find_alias("Ромашка") == "ООО «Ромашка-Плюс»"


def test_alias_unknown_word_is_empty(carrier):
    """Про незнакомое слово память молчит, а не выдумывает."""
    long_memory.remember_alias("Ромашку", "ООО «Ромашка-Плюс»")
    assert long_memory.find_alias("Фасстранс") == ""
    assert long_memory.search_terms("Фасстранс") == []


def test_search_terms_suggests_full_name(carrier):
    """Память подсказывает, чем ещё поискать."""
    long_memory.remember_alias("Ромашку", "ООО «Ромашка-Плюс»")
    assert long_memory.search_terms("Ромашка")[0] == "ООО «Ромашка-Плюс»"


def test_second_query_uses_memory_for_retry(carrier):
    """
    Повторный запрос находит запись по памяти (часть 2, B.2).

    Прямой запрос задан заведомо неудачным, а имя из сообщения память
    знает — значит, ответ должен прийти из повтора, а не «не нашёл».
    """
    long_memory.remember_alias("Ромашку", "ООО «Ромашка-Плюс»")
    reply, chat = ask("найди Ромашку", arguments={"query": "такой-фирмы-нет"})

    payload = tool_payload(chat)
    assert payload.get("count") == 1, payload
    assert payload.get("from_memory") is True
    assert payload.get("query") == "ООО «Ромашка-Плюс»"


def test_retry_without_memory_is_not_invented(carrier):
    """Нет подсказок в памяти — повтора нет, ответ честный."""
    reply, chat = ask("найди НеведомоеСлово",
                      arguments={"query": "НеведомоеСлово"})
    payload = tool_payload(chat)
    assert payload.get("count") == 0
    assert "from_memory" not in payload


def test_personal_data_never_recorded(carrier, memory_db):
    """Телефон и паспорт в память не попадают даже автоалиасом."""
    from db.crud.organizations import save_organization

    save_organization(
        {"full_name": "ООО «Тест-Логистик»", "phone": "+7 (999) 111-22-33"},
        is_carrier=False,
    )
    assert long_memory.remember_search_result(
        "Тест", {"full_name": "ООО «Тест-Логистик»",
                 "phone": "+7 (999) 111-22-33"}
    ) is not None, "название записаться должно"
    values = [item["value"] for item in long_memory.list_records()]
    assert all("999" not in value for value in values), values

    #: А вот значение С ТЕЛЕФОНОМ целиком не записывается.
    assert long_memory.remember_search_result(
        "звонок", {"name": "Иванов +7 (999) 111-22-33"}
    ) is None


def test_short_name_alias_is_not_duplicated(carrier):
    """«Ромашка → ООО «Ромашка»» не пишется: искать её и так находит."""
    assert long_memory.remember_search_result(
        "Ромашка", {"name": "ООО «Ромашка»"}) is None
    assert long_memory.list_records() == []


def test_memory_is_shared_between_queries(carrier):
    """Одна таблица памяти на всю программу: второй запрос видит первый."""
    ask("Заполни Ромашку")
    assert len(long_memory.list_records()) == 1

    reply, chat = ask("найди Ромашку", arguments={"query": "нет-такой"})
    assert tool_payload(chat).get("from_memory") is True


def test_memory_block_is_masked_before_sending(memory_db):
    """
    Память уходит в модель ЗАМАСКИРОВАННОЙ.

    В памяти могут оказаться ФИО, а промпт уходит во внешний сервис: без
    маски память стала бы обходным путём для персональных данных
    (тест tests/test_agent_core.py::test_tool_result_is_masked_before_sending
    это и поймал).
    """
    from core.agent.agent import Agent

    long_memory.remember_habit("созвон", "звонить Иванов Иван Иванович")
    chat = ScriptedChat(tool="get_help", arguments={})
    agent = Agent(client=chat, max_tool_steps=3)
    agent.send("созвон")

    sent = json.dumps(chat.sent_prompts, ensure_ascii=False)
    assert "Иванов Иван Иванович" not in sent, sent[:400]


def test_person_name_is_not_recorded(memory_db):
    """ФИО человека автоалиасом не запоминается (ПДн)."""
    assert long_memory.remember_search_result(
        "Иванов", {"full_name": "Иванов Иван Иванович"}) is None
    assert long_memory.remember_search_result(
        "Иванов", {"full_name": "Иванов И. И."}) is None
    assert long_memory.looks_like_person("ООО «Ромашка»") is False
    assert long_memory.looks_like_person("Иванов Иван Иванович") is True


# ─────────────────────────────────────────────────────────────
# 3. DaData по названию (часть 3)
# ─────────────────────────────────────────────────────────────

def test_compare_with_dadata_accepts_name(carrier, monkeypatch):
    """Сверка принимает НАЗВАНИЕ и находит запись сама (часть 3, D.1)."""
    from core.agent import dadata_tools

    monkeypatch.setattr(dadata_tools, "check_inn_dadata", lambda inn="": {
        "ok": True, "found": True,
        "data": {"inn": inn, "name": "ООО «Ромашка-Плюс»",
                 "address": "г Москва, ул Тестовая, д 1"},
    })
    result = dadata_tools.compare_with_dadata(entity_type="carrier",
                                              query="Ромашка")
    assert result["ok"] is True, result
    assert result["found"] is True
    assert result["data"]["inn"] == "7701002003"
    assert result["data"]["entity_id"] == carrier
    assert result["data"]["title"] == "ООО «Ромашка-Плюс»"


def test_compare_with_dadata_uses_memory(carrier, monkeypatch):
    """Сверка находит запись по алиасу памяти (часть 3)."""
    from core.agent import dadata_tools

    long_memory.remember_alias("Моя фирма", "ООО «Ромашка-Плюс»")
    monkeypatch.setattr(dadata_tools, "check_inn_dadata", lambda inn="": {
        "ok": True, "found": True, "data": {"inn": inn},
    })
    result = dadata_tools.compare_with_dadata(entity_type="carrier",
                                              query="Моя фирма")
    assert result["ok"] is True, result
    assert result["data"]["entity_id"] == carrier
    assert result["data"]["inn"] == "7701002003"


def test_compare_with_dadata_strips_command_words(carrier, monkeypatch):
    """«сверь Ромашку с ЕГРЮЛ» тоже находит запись, а не просит ID."""
    from core.agent import dadata_tools

    monkeypatch.setattr(dadata_tools, "check_inn_dadata", lambda inn="": {
        "ok": True, "found": True, "data": {"inn": inn},
    })
    result = dadata_tools.compare_with_dadata(
        entity_type="carrier", query="сверь Ромашку с ЕГРЮЛ")
    assert result["ok"] is True, result
    assert result["data"]["entity_id"] == carrier


def test_compare_with_dadata_without_name_explains(carrier):
    """Без названия и ID — понятная ошибка, а не «передайте ID»."""
    from core.agent import dadata_tools

    result = dadata_tools.compare_with_dadata(entity_type="carrier", query="")
    assert result["ok"] is False
    assert "название" in result["error"]
    assert "entity_id" in result["error"]


def test_compare_with_dadata_tool_schema_has_query():
    """У инструмента сверки есть параметр query (название)."""
    from core.agent.tools import build_registry, AgentContext

    registry = build_registry(AgentContext())
    tool = registry.get("compare_with_dadata")
    schema = tool.parameters if isinstance(tool.parameters, dict) else {}
    if "function" in schema:
        schema = schema["function"].get("parameters", {})
    properties = schema.get("properties", {})
    assert "query" in properties, schema
    assert "название" in properties["query"]["description"]


def test_compare_tool_description_forbids_asking_id():
    """Описание инструмента прямо запрещает просить ID у оператора."""
    from core.agent.tools import build_registry, AgentContext

    registry = build_registry(AgentContext())
    description = registry.get("compare_with_dadata").description
    assert "НЕ спрашивай" in description
    assert "query='Ромашка'" in description


# ─────────────────────────────────────────────────────────────
# 4. Промпт: план против дела, контекст, заполнение (части 3–6)
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def prompt_text():
    """Системный промпт Максимилиана (файл правил)."""
    from core.agent.prompt import build_system_prompt

    return build_system_prompt(tools=[("get_help", "справка")])


def test_prompt_forbids_planning_instead_of_doing(prompt_text):
    """Правило «делай, не планируй» есть в промпте (часть 4)."""
    assert "НЕ СПРАШИВАЙ «ЧТО СДЕЛАТЬ СНАЧАЛА»" in prompt_text
    assert "ВЫПОЛНИ их по очереди" in prompt_text
    assert "сделай" in prompt_text and "ОСТАЛЬНОЕ" in prompt_text


def test_prompt_has_summary_rule(prompt_text):
    """Требование сводки «что сделано, что нет» (часть 4, B.3)."""
    assert "сводка" in prompt_text


def test_prompt_tells_to_read_form_for_open_question(prompt_text):
    """Правило про «какой X открыт» (часть 5)."""
    assert "СМОТРИ В ФОРМУ, А НЕ В НАЗВАНИЕ ВКЛАДКИ" in prompt_text
    assert "get_form_state" in prompt_text
    assert "пустое" in prompt_text


def test_prompt_tells_to_count_empty_fields(prompt_text):
    """Правило про «заполни N полей» (часть 6)."""
    assert "СМОТРИ ФОРМУ, А НЕ СПРАШИВАЙ" in prompt_text
    assert "НЕ спрашивай «какие поля»" in prompt_text
    assert "пустых" in prompt_text
    assert "Форма пустая" in prompt_text


def test_prompt_tells_to_search_by_name_not_id(prompt_text):
    """Правило про сверку по названию (часть 3)."""
    assert "сверь Ромашку с" in prompt_text
    assert "Просить ID\n   запрещено" in prompt_text or "запрещено" in prompt_text


def test_prompt_has_memory_rules(prompt_text):
    """Правила памяти: искать по сказанному, повторять по памяти (часть 2)."""
    assert "ПАМЯТЬ: КАК ОПЕРАТОР НАЗЫВАЕТ ЗАПИСИ" in prompt_text
    assert "ищи по её значению" in prompt_text


def test_prompt_tells_to_strip_command_from_query(prompt_text):
    """Промпт требует класть в поиск название, а не фразу с командой."""
    assert "ТОЛЬКО НАЗВАНИЕ, БЕЗ СЛОВА-КОМАНДЫ" in prompt_text


# ─────────────────────────────────────────────────────────────
# 5. Контекст формы: значение вместо имени вкладки (часть 5)
# ─────────────────────────────────────────────────────────────

class StubTab:
    """Вкладка-заглушка: только get_data()."""

    def __init__(self, data):
        self._data = dict(data)

    def get_data(self):
        return dict(self._data)


class StubTabs:
    """Список вкладок с заголовками (как QTabWidget)."""

    def __init__(self, titles):
        self._titles = list(titles)
        self._index = 0

    def currentIndex(self):
        return self._index

    def setCurrentIndex(self, index):
        self._index = int(index)

    def tabText(self, index):
        try:
            return self._titles[index]
        except IndexError:
            return ""


class StubWindow:
    """Окно-заглушка: раскладка вкладок и доступ к ним по ключу."""

    CONTRACT_TYPE = "perevozka"

    def __init__(self, keys, data):
        self._keys = list(keys)
        self._tabs = {key: StubTab(data.get(key, {})) for key in keys}
        self.tabs = StubTabs([SECTION_TITLES.get(key, key) for key in keys])

    def _tab_keys(self):
        return tuple(self._keys)

    def _tab_by_key(self, key):
        return self._tabs.get(key)

    def windowTitle(self):
        return "Проба"

    def isVisible(self):
        return True


from core.agent.context_tools import SECTION_TITLES  # noqa: E402


def _context(window):
    from core.agent.tools import AgentContext

    return AgentContext(window=window)


def test_form_state_returns_subject_value():
    """get_form_state отдаёт значение ключевого поля вкладки (часть 5)."""
    from core.agent.context_tools import get_form_state

    window = StubWindow(
        ["carrier_tab", "driver_tab"],
        {"carrier_tab": {"full_name": "ООО «Ромашка-Плюс»", "inn": "7701002003"},
         "driver_tab": {"full_name": "Иванов Иван Иванович"}},
    )
    state = get_form_state(_context(window))
    assert state["ok"] is True
    assert state["current_tab_key"] == "carrier_tab"
    assert state["current_subject"] == "ООО «Ромашка-Плюс»"
    assert state["current_subject_title"] == "Перевозчик"


def test_current_window_returns_subject_value():
    """get_current_window отдаёт то же значение (часть 5, D.2)."""
    from core.agent.context_tools import get_current_window

    window = StubWindow(
        ["carrier_tab"],
        {"carrier_tab": {"full_name": "ООО «Ромашка-Плюс»"}},
    )
    answer = get_current_window(_context(window))
    assert answer["data"]["current_subject"] == "ООО «Ромашка-Плюс»"
    assert answer["data"]["current_tab"] == "Перевозчик"


def test_empty_subject_is_empty_string():
    """Пустое поле — пустая строка, а не название вкладки (часть 5, C.2)."""
    from core.agent.context_tools import get_current_window

    window = StubWindow(["carrier_tab"], {"carrier_tab": {"full_name": ""}})
    answer = get_current_window(_context(window))
    assert answer["data"]["current_subject"] == ""
    assert answer["data"]["current_subject_title"] == "Перевозчик"


def test_subject_follows_open_tab():
    """Значение берётся у ОТКРЫТОЙ вкладки, а не у первой."""
    from core.agent.context_tools import get_form_state

    window = StubWindow(
        ["carrier_tab", "driver_tab"],
        {"carrier_tab": {"full_name": "ООО «Ромашка-Плюс»"},
         "driver_tab": {"full_name": "Иванов Иван Иванович"}},
    )
    window.tabs.setCurrentIndex(1)
    state = get_form_state(_context(window))
    assert state["current_subject"] == "Иванов Иван Иванович"
    assert state["current_subject_title"] == "Водитель"


def test_subject_works_for_other_window_types():
    """Работает на разделах других типов: аренда, клиентский тип A, клиента B."""
    from core.agent.context_tools import get_current_window

    cases = (
        ("arenda_ts", "lessor_tab", "ООО «Арендодатель»"),
        ("formika", "customer_tab", "ООО «Заказчик»"),
        ("logistiks_rus", "cargo_tab", "Мороженое"),
    )
    for contract_type, key, value in cases:
        window = StubWindow([key], {key: {"full_name": value, "name": value,
                                          "cargo_name": value}})
        window.CONTRACT_TYPE = contract_type
        answer = get_current_window(_context(window))
        assert answer["data"]["current_subject"] == value, contract_type


def test_only_empty_lists_every_empty_field():
    """only_empty отдаёт имена пустых полей и их число (часть 6)."""
    from core.agent.context_tools import get_form_state

    window = StubWindow(
        ["carrier_tab"],
        {"carrier_tab": {"full_name": "ООО «Ромашка-Плюс»", "inn": "",
                         "kpp": "", "bik": ""}},
    )
    state = get_form_state(_context(window), only_empty=True)
    section = state["data"]["carrier_tab"]
    assert section["empty_count"] == 3
    assert "inn" in section["empty"]
    assert "full_name" not in section["empty"]


def test_get_form_state_tool_description_mentions_counting():
    """Описание инструмента учит считать пустые поля (часть 6)."""
    from core.agent.tools import build_registry, AgentContext

    registry = build_registry(AgentContext())
    description = registry.get("get_form_state").description
    assert "only_empty" in description
    assert "заполни 10 полей" in description
    assert "не спрашивай" in description.lower()
