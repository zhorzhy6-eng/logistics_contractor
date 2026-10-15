#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Задача 5: «уточнение вместо инструмента» там, где объект НАЗВАН.

Что было в стресс-тесте (200 запросов). Три незакрытые находки:

  * №23 «Покажи всё» (категория B.2 «Команды без объекта», подсказка
    «поиск») — модель вызывала инструменты формы (`get_open_tabs`,
    `get_current_window`, `get_form_state`) и упиралась в лимит шагов;
  * №79 «Сколько убыточных договоров» (B.4 «Аналитика», «количество») —
    ни одного вызова инструмента;
  * №80 «Покажи статистику» (B.4) — ни одного вызова инструмента, вопрос
    вместо ответа.

**Важно про №79.** Поля «убыточность» в базе НЕТ: в таблице `contracts`
(`db/schema.py`) есть только суммы, НДС, предоплата, даты и маршрут;
`FIELD_CATALOG` в `db/crud/analytics.py` знает операции sum/avg/max/min/
count и не знает расходов. Поэтому правильное поведение — не «посчитать
убыточные», а СКАЗАТЬ, ЧЕГО НЕТ, и сразу показать то, что есть
(`aggregate_contracts(operation="sum"/"min")`, `top_contractors()`,
`check_data_quality`). Выдуманное число убыточных договоров было бы хуже
текущего ответа: промпт прямо запрещает выдумывать данные.

Что было дырой в промпте. Правило «КОМАНДА БЕЗ ОБЪЕКТА» безусловно велело
уточнять, и объект в нём не проверялся вовсе: «Покажи» → «Что показать?».
Плюс «Единственный законный вопрос — выбор ОДНОЙ записи из НЕСКОЛЬКИХ» в
разделе запрещённых ответов противоречило разделу «НЕПОНЯТНЫЙ ЗАПРОС».

Что добавлено (`core/agent/prompts/maximilian.txt`):

  * «ОБЪЕКТ ЕСТЬ — ДЕЛАЙ, ОБЪЕКТА НЕТ — СПРОСИ» — критерий: объект назван →
    вызывай инструмент; объекта нет вовсе → ОДИН короткий вопрос; слова-роли
    («водитель», «договор») объектом не являются;
  * «ПОКАЖИ ВСЁ» — обзор числами через `aggregate_contracts(operation=
    "count", field="")` по видам, а не вопрос «что показать?»;
  * «ПОКАЖИ СТАТИСТИКУ» — count + sum + топ перевозчиков за всё время;
  * «ПОЛЯ В БАЗЕ НЕТ — ЭТО ОТВЕТ» — назвать, чего нет, и ВЫПОЛНИТЬ
    ближайшее осмысленное действие;
  * снято противоречие в «ЗАПРЕЩЁННЫХ ОТВЕТАХ» и в примере «Покажи».

Тесты идут БЕЗ живого GigaChat: проверяется текст промпта (то, что модель
читает) и классификатор находок стресс-теста (то, чем находка измеряется).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.agent.prompt import build_system_prompt, load_prompt_template
from tools.stress_test_agent import (
    HINT_COUNT, HINT_SEARCH, Query, classify, hint_satisfied,
)


@pytest.fixture(scope="module")
def template() -> str:
    """Сырой шаблон промпта (до подстановок)."""
    return load_prompt_template()


@pytest.fixture(scope="module")
def prompt() -> str:
    """Собранный системный промпт — то, что реально уходит модели."""
    return build_system_prompt([], persona_key="cat_logist", max_tool_steps=20)


def make_record(**overrides):
    """Запись прогона с разумными значениями по умолчанию."""
    record = {
        "number": 1, "category": "B.4", "query": "Покажи статистику",
        "answer": "Готово.", "answer_len": 7, "tools": [],
        "step_limit": False, "errors": [], "seconds": 3.0, "status": "ok",
    }
    record.update(overrides)
    return record


# ─────────────────────────────────────────────────────────────
# 1. Промпт: критерий «объект есть → делай»
# ─────────────────────────────────────────────────────────────
def test_prompt_has_object_rule(template):
    """Появилось правило «объект есть — делай, объекта нет — спроси»."""
    assert "ОБЪЕКТ ЕСТЬ — ДЕЛАЙ, ОБЪЕКТА НЕТ — СПРОСИ" in template
    assert "ВЫЗЫВАЙ инструмент" in template
    assert "Объекта нет вовсе" in template


def test_prompt_covers_show_everything(template):
    """«Покажи всё» — запрос обзора, а не повод спросить «что показать?»."""
    assert "«ПОКАЖИ ВСЁ» — ЭТО ЗАПРОС, А НЕ ВОПРОС" in template
    assert "aggregate_contracts(entity_type=\"contracts\", operation=\"count\"" \
        in template
    assert "check_data_quality" in template


def test_prompt_covers_show_statistics(template):
    """«Покажи статистику» — счёт, а не уточнение."""
    assert "«ПОКАЖИ СТАТИСТИКУ» — СЧИТАЙ, А НЕ СПРАШИВАЙ" in template
    assert 'aggregate_contracts(operation="count")' in template
    assert 'aggregate_contracts(operation="sum")' in template
    assert "top_contractors()" in template
    assert "ЗА ВСЁ\nВРЕМЯ" in template or "за всё время" in template.lower()


def test_prompt_covers_missing_field(template):
    """Чего в базе нет — это ответ, а не повод спросить оператора."""
    assert "ПОЛЯ В БАЗЕ НЕТ — ЭТО ОТВЕТ, А НЕ ПОВОД СПРОСИТЬ" in template
    assert "убыточность" in template
    assert "НЕ выдумывай метрику" in template


def test_prompt_keeps_objectless_rule(template):
    """
    Правило «команда без объекта — уточняем» осталось.

    Его нельзя потерять: без него модель начнёт искать глагол в базе.
    """
    assert "КОМАНДА БЕЗ ОБЪЕКТА" in template
    assert "НЕ ИЩИ ГЛАГОЛ В БАЗЕ" in template
    for word in ("Заполни", "Найди", "Покажи", "Отмени", "Сверь"):
        assert word in template


def test_prompt_keeps_question_rule(template):
    """
    Правило «вопрос ≠ команда» (коммит 2da602a) не тронуто.

    Это то самое правило, которое задача велела не сломать.
    """
    assert "ВОПРОС ИЛИ КОМАНДА — РАЗЛИЧАЙ" in template
    assert "СОМНЕВАЕШЬСЯ — СПРОСИ" in template
    assert "Искать в базе НЕЛЬЗЯ" in template
    assert "«Сможешь…?», «Можешь…?», «Умеешь…?»" in template


def test_prompt_keeps_other_sections(template):
    """Правка не вытеснила разделы, которые вели другие шаги."""
    for marker in (
        "АНАЛИТИКА: СЧИТАЙ, А НЕ ПЕРЕЧИСЛЯЙ",
        "НЕ СПРАШИВАЙ «ЧТО СДЕЛАТЬ СНАЧАЛА»",
        "ПРАВОВАЯ БАЗА",
        "ОБУЧЕНИЕ",
        "ЗАПРЕЩЁННЫЕ ОТВЕТЫ",
    ):
        assert marker in template, f"пропал раздел: {marker}"


def test_new_sections_have_no_curly_braces(template):
    """
    В новых разделах нет фигурных скобок.

    Шаблон прогоняется через `str.format`: чужая скобка ломает подстановку,
    и промпт молча теряет список служебных слов (см. `build_system_prompt`).
    Примеры аргументов поэтому пишутся без JSON-скобок.
    """
    start = template.index("ОБЪЕКТ ЕСТЬ — ДЕЛАЙ")
    end = template.index("КОМАНДА БЕЗ ОБЪЕКТА", start)
    section = template[start:end]

    assert "{" not in section and "}" not in section


def test_prompt_is_formatted_without_leftovers(prompt):
    """
    Собранный промпт не содержит неподставленных мест.

    Если бы форматирование упало, в тексте остались бы литералы
    `{noise_words}`, `{persona}`, `{tools}`, `{tool_steps}` — и список
    служебных слов исчез бы из промпта (это и была бы тихая деградация).
    """
    for leftover in ("{noise_words}", "{persona}", "{tools}", "{tool_steps}"):
        assert leftover not in prompt, f"подстановка не выполнена: {leftover}"

    # Список служебных слов на месте и не пуст.
    assert "команды без объекта" in prompt
    assert "заполни" in prompt.lower()


# ─────────────────────────────────────────────────────────────
# 2. Классификатор стресс-теста: находки закрываются
# ─────────────────────────────────────────────────────────────
#: Три сценария из отчёта стресс-теста: (номер, категория, запрос, подсказка).
SCENARIOS = (
    (23, "B.2", "Покажи всё", HINT_SEARCH),
    (79, "B.4", "Сколько убыточных договоров", HINT_COUNT),
    (80, "B.4", "Покажи статистику", HINT_COUNT),
)


def scenario_query(number: int, category: str, text: str, hint: str) -> Query:
    """Запрос каталога стресс-теста с подсказкой «какой инструмент уместен»."""
    return Query(number=number, category=category, text=text, hint=hint)


@pytest.mark.parametrize("number, category, query, hint, tools", [
    # №23: «Покажи всё» — подсказка «поиск», закрывается счётным инструментом.
    (23, "B.2", "Покажи всё", HINT_SEARCH,
     ["aggregate_contracts", "aggregate_contracts"]),
    # №79: «Сколько убыточных договоров» — поля нет, но ближайшее действие
    # выполнено, и находки «ответ текстом вместо инструмента» быть не должно.
    (79, "B.4", "Сколько убыточных договоров", HINT_COUNT,
     ["aggregate_contracts", "check_data_quality"]),
    # №80: «Покажи статистику».
    (80, "B.4", "Покажи статистику", HINT_COUNT,
     ["aggregate_contracts", "top_contractors"]),
])
def test_scenario_is_closed_when_tool_is_called(number, category, query,
                                                hint, tools):
    """Сценарий из отчёта больше не даёт находки, если инструмент вызван."""
    record = make_record(number=number, category=category, query=query,
                         tools=tools,
                         answer="За всё время: договоров 24 на сумму 1 234 567.")

    assert classify(record, scenario_query(number, category, query, hint)) == [], (
        f"№{number} «{query}» всё ещё даёт находку"
    )


@pytest.mark.parametrize("number, category, query, hint", SCENARIOS[:1] + SCENARIOS[2:])
def test_scenario_still_found_without_tool(number, category, query, hint):
    """
    Без вызова инструмента находка ОСТАЁТСЯ.

    Иначе тест выше ничего не проверял бы: он должен ловить именно
    «уточнение вместо инструмента», а не быть всегда зелёным.
    """
    record = make_record(number=number, category=category, query=query,
                         tools=[], answer="Покажи, что именно тебе нужно?")

    findings = classify(record, scenario_query(number, category, query, hint))

    assert findings, f"№{number}: находка должна остаться без инструмента"
    assert "ответ текстом вместо инструмента" in {
        item["kind"] for item in findings
    }


def test_missing_field_still_needs_a_tool():
    """
    «Поля нет» — не отговорка от вызова инструмента.

    Ответ «поля «убыточность» в базе нет» БЕЗ единого вызова инструмента
    остаётся находкой: оператору не показали даже того, что есть.
    """
    query, hint = "Сколько убыточных договоров", HINT_COUNT
    record = make_record(
        number=79, category="B.4", query=query, tools=[],
        answer="Поля «убыточность» в базе нет: расходы не хранятся.",
    )

    findings = classify(record, scenario_query(79, "B.4", query, hint))

    assert findings, "ответ без инструмента не должен считаться закрытием"
    assert "ответ текстом вместо инструмента" in {
        item["kind"] for item in findings
    }


def test_hints_accept_the_tools_prompt_teaches():
    """
    Инструменты, которым учит промпт, закрывают подсказки стресс-теста.

    Промпт называет `aggregate_contracts`, `top_contractors` и
    `check_data_quality` — все три должны приниматься и подсказкой «поиск»,
    и подсказкой «количество», иначе правка промпта не закроет находку.
    """
    taught = ("aggregate_contracts", "top_contractors", "check_data_quality")

    for hint in (HINT_SEARCH, HINT_COUNT):
        for tool in taught:
            assert hint_satisfied(hint, [tool]) is True, (
                f"{tool} не закрывает подсказку {hint!r}"
            )
