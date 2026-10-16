#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Человеческий язык вместо жаргона, вопросы вместо команд (задание, часть 1).

Двенадцать проблем из задания сводятся к трём:

  * #5, #16 — в ответах оператору видны имена служебных команд и
    технический жаргон. Оператор не программист: «search_anything вернул
    carrier=1» для него — шум;
  * #11–#14, #17 — вопросы и команды без объекта принимаются за данные:
    «Сможешь заполнить договор?» искало «Смождешь», «Заполни» — «Заполни»,
    мат — ругательство как фамилию;
  * #15 — пустой запрос отдавал десять последних договоров.

Первая и вторая лечатся промптом, но полагаться только на модель нельзя:
под запретом должна быть и САМА возможность отправить служебное слово в
поиск. Поэтому здесь проверяются обе стороны — и текст промпта, и код
инструментов.
"""

import os
import re
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

from core.agent.prompt import load_prompt_template  # noqa: E402
from core.agent.tools import (  # noqa: E402
    AgentContext,
    _get_help,
    _search_anything,
    _search_carrier,
    _search_contract,
    _search_customer,
    _search_driver,
    _search_vehicle,
    build_registry,
    looks_like_command_or_noise,
)
from core.agent.words import COMMAND_WORDS, QUESTION_WORDS  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 1. Промпт: запрет жаргона, имён команд и правил-исключений
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def prompt():
    """Текст системного промпта, как его видит модель."""
    return load_prompt_template()


def test_prompt_forbids_tool_names(prompt):
    """Промпт прямо запрещает называть служебные команды в ответе."""
    assert "ЗАПРЕЩЕНО" in prompt
    lowered = prompt.lower()
    assert "имена служебных команд" in lowered or "имена инструментов" in lowered
    # Перечислены сами шаблоны имён — по ним модель и опознаёт запрет.
    assert "search_" in prompt
    assert "fill_" in prompt


def test_prompt_forbids_technical_jargon(prompt):
    """Технический жаргон перечислен поименно."""
    for word in ("API", "JSON", "entity", "field", "валидатор"):
        assert word in prompt, f"жаргон {word!r} не попал в запрет"


def test_prompt_asks_plain_language(prompt):
    """Есть требование говорить простыми словами и показывать пример."""
    lowered = prompt.lower()
    assert "простые человеческие слова" in lowered
    assert "пример плохого ответа" in lowered
    assert "пример хорошего ответа" in lowered


def test_prompt_separates_questions_from_commands(prompt):
    """Правило «вопрос ≠ команда» есть, и оно названо самым важным."""
    assert "ВОПРОС ИЛИ КОМАНДА" in prompt
    for marker in ("Сможешь", "Можешь", "Умеешь"):
        assert marker in prompt
    assert "СОМНЕВАЕШЬСЯ — СПРОСИ" in prompt


def test_prompt_has_commands_without_object(prompt):
    """Команды без объекта разобраны по одной, с готовыми ответами."""
    assert "КОМАНДА БЕЗ ОБЪЕКТА" in prompt
    for command in ("Заполни", "Найди", "Покажи", "Отмени", "Сверь"):
        assert command in prompt
    assert "НЕ ИЩИ ГЛАГОЛ В БАЗЕ" in prompt


def test_prompt_handles_profanity(prompt):
    """Про мат есть отдельное правило: не искать и не реагировать."""
    lowered = prompt.lower()
    assert "ругается" in lowered or "ругательства" in lowered
    assert "не ищи" in lowered


def test_prompt_asks_clarification_for_numbers(prompt):
    """«12345» — повод уточнить, а не искать вслепую."""
    assert "12345" in prompt
    assert "НЕПОНЯТНЫЙ ЗАПРОС" in prompt


def test_prompt_examples_have_no_real_names(prompt):
    """
    В примерах промпта нет настоящих данных из базы.

    Примеры уходят в модель в системном сообщении и не маскируются, а
    маскирование ПДн проверяется тестами отдельно. Поэтому конкретные
    ФИО и названия в примерах заменены заглушками: смысл примера от этого
    не теряется, а обещание «в модель уходят только плейсхолдеры»
    остаётся правдой.

    Исключение — вымышленный «ФассТранс»: он нужен в правиле про
    опечатки, потому что показывает принцип «лишняя буква не мешает
    поиску». Данных из базы в нём нет.
    """
    for name in ("Иванов Иван Иванович", "Иванов Пётр Сергеевич",
                 "ООО «ФассТранс»", "7701234567"):
        assert name not in prompt, f"в примерах промпта осталось реальное {name!r}"
    assert "<фамилия>" in prompt
    assert "<название>" in prompt


def test_prompt_typo_example_is_allowed(prompt):
    """
    «Ромашка» в примере — это ОБРАЗЕЦ названия, а не данные из базы.

    Фраза «Ромашка, покажи» показывает главное правило фильтра: название
    вместе со служебным словом всё ещё остаётся запросом. Убрать пример —
    потерять правило, поэтому он остаётся; проверка следит лишь за тем,
    чтобы рядом не появлялось конкретных реквизитов.
    """
    assert "«Ромашка, покажи»" in prompt


def test_prompt_may_use_typo_examples(prompt):
    """
    Примеры опечаток остаются — но как ОБРАЗЕЦ, а не как данные.

    «ФассТранс» из задания — вымышленное название, и оно нужно, чтобы
    показать принцип «лишняя буква не мешает поиску». Разрешены только
    короткие вымышленные образцы в правилах про опечатки.
    """
    assert "лишние и пропущенные буквы" in prompt


def test_prompt_requires_confirmation_phrase_for_data(prompt):
    """Промпт напоминает: заполнение формы идёт через подтверждение."""
    lowered = prompt.lower()
    assert "подтверждени" in lowered


# ─────────────────────────────────────────────────────────────
# 2. Служебные слова не попадают в поиск (код, не промпт)
# ─────────────────────────────────────────────────────────────

NOISE_QUERIES = (
    "Заполни", "Найди", "Отмени", "Покажи", "Сверь",
    "Смождешь", "Сможешь", "можешь", "умеешь",
    "блядь", "заебал", "блядь, заебал",
    "привет", "спасибо", "помоги",
    "и", "ну", "ок",
)


@pytest.mark.parametrize("query", NOISE_QUERIES)
def test_noise_query_is_rejected(query):
    """Служебное слово распознаётся как команда, а не как название."""
    reason = looks_like_command_or_noise(query)

    assert reason, f"«{query}» пропущено в поиск как название"
    assert "команда" in reason or "вопрос" in reason


NAME_QUERIES = (
    "Фасстранс", "королёв", "иваноф", "Иванова",
    "ООО Ромашка", "Иванов И.И.", "7701234567", "А123ВС77",
    "Ромашка", "Ромашка, покажи", "найди водителя Иванова",
)


@pytest.mark.parametrize("query", NAME_QUERIES)
def test_real_name_is_not_rejected(query):
    """
    Настоящее название проходит фильтр.

    «Ромашка, покажи» — запрос с названием и служебным словом: искать
    нужно, иначе оператор не найдёт то, что просил.
    """
    assert looks_like_command_or_noise(query) == ""


@pytest.mark.parametrize("query", NOISE_QUERIES)
def test_search_tools_do_not_run_for_noise(query):
    """Ни один поисковый инструмент не выполняется на служебное слово."""
    for function in (_search_driver, _search_carrier, _search_customer,
                     _search_vehicle, _search_contract):
        result = function(query=query)

        assert result["ok"] is False, f"{function.__name__} искал «{query}»"
        assert result.get("looks_like_command") is True


def test_search_anything_does_not_run_for_noise():
    """Поиск «сразу везде» тоже отказывается искать служебное слово."""
    from core.agent.tools import _search_anything

    result = _search_anything(query="Заполни")

    assert result["ok"] is False
    assert result.get("looks_like_command") is True


def test_empty_query_is_rejected_too():
    """Пустой запрос — ошибка с подсказкой, а не поиск по пустой строке."""
    result = _search_anything(query="")

    assert result["ok"] is False
    assert "пустой запрос" in result["error"] or "не указано" in result["error"]


@pytest.mark.parametrize("query", sorted(
    set(COMMAND_WORDS) | set(QUESTION_WORDS)
))
def test_every_service_word_is_refused_by_every_search_tool(query):
    """
    КАЖДОЕ служебное слово отклоняется КАЖДЫМ поисковым инструментом.

    Список служебных слов растёт (живой прогон добавил «посчитай»: оно
    попало в память ключом алиаса «посчитай → ООО «Ромашка»»), и проверка
    «по списку» ловит такое сразу, а не после следующего диалога.
    """
    from core.agent.tools import _search_anything

    tools = (_search_driver, _search_carrier, _search_customer,
             _search_vehicle, _search_contract, _search_anything)
    for function in tools:
        result = function(query=query)
        assert result["ok"] is False, f"{function.__name__} искал «{query}»"
        assert result.get("looks_like_command") is True, (
            f"{function.__name__} на «{query}» ответил {result}"
        )


@pytest.mark.parametrize("query", sorted(
    set(COMMAND_WORDS) | set(QUESTION_WORDS)
))
def test_service_word_never_becomes_alias_key(query):
    """Служебное слово не становится ключом алиаса в памяти (часть 2)."""
    from core.agent.long_memory import _meaningful_key

    assert _meaningful_key(query) is False, f"«{query}» — не название"


# ─────────────────────────────────────────────────────────────
# 3. Проблема #15: пустой запрос не отдаёт последние договоры
# ─────────────────────────────────────────────────────────────

def test_empty_contract_query_returns_nothing(tmp_path, monkeypatch):
    """
    Пустой запрос к договорам больше НЕ отдаёт десять последних.

    Десять случайных строк на непонятный запрос выглядят как выдумка:
    оператор не понимает, почему показали именно эти договоры.
    """
    import db.database as database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "contracts.db"))
    monkeypatch.setattr(database, "restrict_to_current_user", lambda *a, **k: True)
    monkeypatch.setattr(database, "backup_database", lambda *a, **k: None)
    database.init_database()
    database.save_contract({"contract_number": "1", "contract_date": "2026-01-01",
                            "end_date": "2026-12-31", "route": "Москва — Тверь"})

    result = _search_contract(query="")

    assert result["ok"] is False
    assert "не указано" in result["error"]
    assert "hint" in result


def test_contract_tool_description_has_no_promise_of_list():
    """Описание поиска договоров не обещает «последние договоры»."""
    registry = build_registry(AgentContext())
    description = registry.get("search_contract").description

    assert "Пустой запрос вернёт последние" not in description


# ─────────────────────────────────────────────────────────────
# 4. Текст справки — тоже без жаргона
# ─────────────────────────────────────────────────────────────

JARGON_RE = re.compile(r"\b(?:search_|fill_|check_|get_|validate_|explain_|"
                       r"list_)[a-z_]+\b")


def test_help_text_has_no_tool_names():
    """
    Справка не содержит имён служебных команд.

    Её текст модель часто пересказывает оператору почти дословно, поэтому
    имена команд здесь — прямая утечка жаргона.
    """
    result = _get_help(topic="")
    text = result["data"]["text"]

    assert not JARGON_RE.search(text), f"в справке остались имена команд: {text!r}"


@pytest.mark.parametrize("topic", ["максимилиан", "база", "формы", "типы",
                                   "распознавание", "настройки"])
def test_help_topics_have_no_jargon(topic):
    """Ни одна тема справки не содержит технического жаргона."""
    text = _get_help(topic=topic)["data"]["text"]

    assert not JARGON_RE.search(text)
    for word in ("SQLite", "API", "JSON", "entity"):
        assert word not in text, f"тема {topic!r}: жаргон {word!r} в справке"


@pytest.mark.parametrize("name", ["search_anything", "search_driver",
                                  "search_carrier", "search_customer",
                                  "search_vehicle", "search_contract"])
def test_search_tool_description_is_human_readable(name):
    """
    Описания поисковых инструментов читаются как фраза, а не как код.

    Описание уходит в модель вместе со схемой, и модель иногда
    пересказывает его оператору. Латинские имена полей и названия других
    команд в описании — тоже утечка: оператор их не поймёт.
    """
    description = build_registry(AgentContext()).get(name).description

    assert not JARGON_RE.search(description), f"{name}: имя команды в описании"
    assert "JSON" not in description
    # Латинское «id» как отдельное слово — техническая деталь, а не речь.
    assert "id" not in description.replace(",", " ").replace(".", " ").split(), (
        f"{name}: техническое «id» в описании"
    )
