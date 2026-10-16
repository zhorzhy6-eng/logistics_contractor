#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Память Максимилиана (задание, часть 5, тесты).

Что проверяется
---------------
1. **Таблицы.** ``agent_memory`` и ``agent_feedback`` создаются, индексы на
   месте, обе таблицы независимы от справочников.
2. **Запись памяти.** Алиасы, привычки и исправления записываются; повторная
   запись обновляет значение, а не плодит дубликаты; мусор не пишется.
3. **Персональные данные.** Значение с паспортом, телефоном, длинным числом
   (ИНН) или словом «пароль» в память не попадает: память уходит в промпт.
4. **Отбор.** TOP-5 релевантных записей на запрос: по ключу весит больше,
   чем по значению; нерелевантное не подкладывается.
5. **Промпт.** Блок «что помнишь» добавляется к системному промпту, пустая
   память промпт не меняет.
6. **Разбор фразы.** «Запомни: Ромашка — это ООО Ромашка» → алиас;
   «всегда НДС 20 % для Ромашки» → привычка; исправление → исправление.
7. **Оценки.** 👍/👎 записываются, мусорные оценки отклоняются.
8. **Управление.** Просмотр, удаление по одной записи и «Забыть всё».
9. **UI.** В диалоге настроек есть раздел «Что Максимилиан помнит», список
   заполняется, «Забыть всё» очищает память и НЕ трогает справочники.

База — изолированная (фикстура ``isolated_db``).
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

from core.agent import long_memory  # noqa: E402
from db.connection import get_connection  # noqa: E402
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


def _tables() -> set:
    """Имена таблиц в базе."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def _indexes() -> set:
    """Имена индексов в базе."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# 1. Таблицы и схема
# ─────────────────────────────────────────────────────────────

def test_memory_tables_created(memory_db):
    """Обе таблицы памяти создаются в базе."""
    tables = _tables()
    assert "agent_memory" in tables
    assert "agent_feedback" in tables


def test_memory_indexes_created(memory_db):
    """Индексы памяти и оценок на месте."""
    indexes = _indexes()
    assert agent_memory.MAIN_INDEX_NAME in indexes
    assert "idx_agent_feedback_message" in indexes


def test_ensure_tables_is_idempotent(memory_db):
    """Повторный вызов ничего не ломает."""
    assert agent_memory.ensure_tables() is True
    assert agent_memory.ensure_tables() is True
    assert "agent_memory" in _tables()


def test_memory_amounts_are_columns():
    """У записи памяти есть вид, ключ, значение, источник и счётчик."""
    conn = get_connection()
    try:
        columns = [row[1] for row in conn.execute(
            "PRAGMA table_info(agent_memory)"
        ).fetchall()]
    finally:
        conn.close()
    for name in ("kind", "key", "value", "source", "hits", "created_at",
                 "updated_at"):
        assert name in columns, name


# ─────────────────────────────────────────────────────────────
# 2. Запись и чтение памяти
# ─────────────────────────────────────────────────────────────

def test_remember_alias(memory_db):
    """Алиас записывается и читается."""
    record_id = long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    assert record_id is not None
    records = long_memory.list_records()
    assert len(records) == 1
    assert records[0]["kind"] == "alias"
    assert records[0]["key"] == "Ромашка"
    assert records[0]["value"] == "ООО «Ромашка»"


def test_remember_habit_and_correction(memory_db):
    """Привычка и исправление записываются своим видом."""
    long_memory.remember_habit("Ромашка", "всегда НДС 20 %")
    long_memory.remember_correction("ИНН с пробелами", "убрать пробелы")
    kinds = {item["kind"] for item in long_memory.list_records()}
    assert kinds == {"habit", "correction"}


def test_repeated_record_updates_value(memory_db):
    """Повторная запись обновляет значение, а не плодит строки."""
    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    long_memory.remember_alias("Ромашка", "ООО «Ромашка» (Москва)")
    records = long_memory.list_records()
    assert len(records) == 1
    assert records[0]["value"] == "ООО «Ромашка» (Москва)"


def test_memory_rejects_unknown_kind(memory_db):
    """Неизвестный вид записи отклоняется."""
    assert agent_memory.append("секрет", "ключ", "значение") is None
    assert long_memory.list_records() == []


def test_memory_rejects_empty_key_or_value(memory_db):
    """Пустые ключ и значение не записываются."""
    assert agent_memory.append("alias", "", "ООО") is None
    assert agent_memory.append("alias", "Ромашка", "   ") is None
    assert long_memory.list_records() == []


def test_memory_clips_long_values(memory_db):
    """Длинное значение обрезается: память не должна занимать весь контекст."""
    agent_memory.append("habit", "Ромашка", "а" * 2000)
    records = long_memory.list_records()
    assert len(records[0]["value"]) <= agent_memory.MAX_VALUE_CHARS


def test_memory_load_by_kind(memory_db):
    """Можно прочитать только один вид записей."""
    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    long_memory.remember_habit("Ромашка", "НДС 20 %")
    aliases = agent_memory.load(kind="alias")
    assert len(aliases) == 1
    assert aliases[0]["kind"] == "alias"


def test_memory_count(memory_db):
    """Счётчик записей памяти."""
    assert agent_memory.count() == 0
    long_memory.remember_alias("Раз", "Один")
    long_memory.remember_alias("Два", "Два")
    assert agent_memory.count() == 2


# ─────────────────────────────────────────────────────────────
# 3. Персональные данные
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "паспорт 28 15 634512",
    "телефон +7 900 111-22-33",
    "ИНН 7701234567",
    "пароль от базы",
])
def test_sensitive_values_are_not_remembered(memory_db, value):
    """Паспорт, телефон, ИНН и пароли в память не попадают."""
    assert long_memory.remember_alias("Запись", value) is None
    assert long_memory.list_records() == []


def test_sensitive_lookup_helper():
    """Проверка «похоже на ПДн» отвечает и на чистом значении."""
    assert long_memory.look_sensitive("ООО «Ромашка»") == ""
    assert long_memory.look_sensitive("7701234567") != ""


def test_sensitive_alias_key_is_rejected(memory_db):
    """ПДн в ключе записи тоже не запоминаются."""
    assert long_memory.remember_alias("7701234567", "ООО «Ромашка»") is None


# ─────────────────────────────────────────────────────────────
# 4. Отбор релевантного (TOP-5)
# ─────────────────────────────────────────────────────────────

def test_recall_finds_by_key(memory_db):
    """Запрос по названию находит алиас."""
    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    long_memory.remember_alias("Василёк", "ООО «Василёк»")
    found = long_memory.recall("заполни перевозчика Ромашка")
    assert [item["key"] for item in found] == ["Ромашка"]


def test_recall_ignores_irrelevant(memory_db):
    """Нерелевантное в промпт не подкладывается."""
    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    assert long_memory.recall("сколько договоров за октябрь") == []


def test_recall_empty_query(memory_db):
    """Пустой запрос ничего не вспоминает."""
    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    assert long_memory.recall("") == []


def test_recall_limits_to_top_five(memory_db):
    """В промпт уходит не больше пяти записей (задание, часть 5)."""
    for index in range(8):
        long_memory.remember_alias(f"Ромашка {index}", f"ООО «Ромашка {index}»")
    found = long_memory.recall("Ромашка", limit=long_memory.TOP_RELEVANT)
    assert len(found) == 5


def test_recall_marks_hits(memory_db):
    """Использованная запись получает плюс в счётчике обращений."""
    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    long_memory.recall("Ромашка")
    assert long_memory.list_records()[0]["hits"] == 1


def test_key_match_weighs_more_than_value():
    """Совпадение в ключе важнее совпадения в значении."""
    records = [
        {"id": 1, "kind": "alias", "key": "что-то", "value": "ромашка тут",
         "hits": 0},
        {"id": 2, "kind": "alias", "key": "Ромашка", "value": "ООО", "hits": 0},
    ]
    ranked = long_memory.rank(records, "Ромашка")
    assert ranked[0]["id"] == 2


def test_stop_words_do_not_match():
    """Служебные слова не тянут память в промпт."""
    records = [{"id": 1, "kind": "alias", "key": "и", "value": "в на", "hits": 0}]
    assert long_memory.rank(records, "и в на за") == []


# ─────────────────────────────────────────────────────────────
# 5. Промпт
# ─────────────────────────────────────────────────────────────

def test_memory_block_lists_facts():
    """Блок памяти перечисляет факты с видами."""
    block = long_memory.memory_block([
        {"kind": "alias", "key": "Ромашка", "value": "ООО «Ромашка»"},
    ])
    assert "ЧТО ТЫ ПОМНИШЬ" in block
    assert "Алиас" in block
    assert "ООО «Ромашка»" in block


def test_memory_block_empty():
    """Нечего вспомнить — блок пустой (промпт не меняется)."""
    assert long_memory.memory_block([]) == ""
    assert long_memory.memory_block([{"kind": "alias", "key": "", "value": ""}]) == ""


def test_system_prompt_with_and_without_memory():
    """Память добавляется к промпту и не ломает его без памяти."""
    from core.agent.prompt import build_system_prompt

    plain = build_system_prompt(tools=[("get_help", "справка")])
    with_memory = build_system_prompt(
        tools=[("get_help", "справка")],
        memory=long_memory.memory_block([
            {"kind": "habit", "key": "Ромашка", "value": "НДС 20 %"},
        ]),
    )
    assert "ЧТО ТЫ ПОМНИШЬ" not in plain
    assert "ЧТО ТЫ ПОМНИШЬ" in with_memory
    assert with_memory.startswith(plain)


def test_agent_prompt_includes_recalled_memory(memory_db):
    """Агент подкладывает в промпт найденную память."""
    from core.agent.agent import Agent

    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    agent = Agent(client=None)
    prompt = agent.system_prompt("заполни перевозчика Ромашка")
    assert "ООО «Ромашка»" in prompt

    # Без совпадений память в промпт не идёт.
    assert "ООО «Ромашка»" not in agent.system_prompt("сколько договоров")


def test_agent_memory_facts_method(memory_db):
    """Метод memory_facts отдаёт то, на что опирался ответ."""
    from core.agent.agent import Agent

    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    agent = Agent(client=None)
    facts = agent.memory_facts("Ромашка")
    assert [item["key"] for item in facts] == ["Ромашка"]


# ─────────────────────────────────────────────────────────────
# 6. Разбор фразы оператора
# ─────────────────────────────────────────────────────────────

def test_extract_alias_phrase():
    """«Запомни: Ромашка — это ООО Ромашка» даёт алиас."""
    facts = long_memory.extract("Запомни: Ромашка — это ООО Ромашка")
    assert facts == [{"kind": "alias", "key": "Ромашка",
                      "value": "ООО Ромашка"}]


def test_extract_habit_phrase():
    """«Всегда НДС 20 % для Ромашки» даёт привычку без служебного слова."""
    facts = long_memory.extract("Всегда НДС 20 % для Ромашки")
    assert len(facts) == 1
    assert facts[0]["kind"] == "habit"
    assert facts[0]["key"] == "Ромашки"
    # Служебное «Всегда» в значение не попадает.
    assert facts[0]["value"] == "НДС 20 %"


def test_extract_correction_phrase():
    """Исправление помечается своим видом и не тянет служебные слова."""
    facts = long_memory.extract("Неправильно, ИНН с пробелами — без пробелов")
    assert len(facts) == 1
    assert facts[0]["kind"] == "correction"
    assert facts[0]["key"] == "ИНН с пробелами"
    assert facts[0]["value"] == "без пробелов"


def test_extract_strips_leading_cues():
    """Служебные слова из начала фразы в запись не входят."""
    assert long_memory.extract("Учти, Фасстранс — это ООО «ФассТранс»")[0][
        "key"] == "Фасстранс"
    assert long_memory.extract("На будущее: Ромашка — это ООО Ромашка")[0][
        "value"] == "ООО Ромашка"


def test_recall_matches_cases_by_stem(memory_db):
    """Падежи сходятся: запись «для Ромашки» находится запросом «Ромашка»."""
    long_memory.remember_habit("Ромашки", "НДС 20 %")
    assert long_memory.recall("заполни Ромашка")
    assert long_memory.recall("что там по Ромашке")
    # Чужая запись не подтягивается.
    assert long_memory.recall("что там по Васильку") == []


def test_extract_plain_question_is_empty():
    """Обычный вопрос ничего не запоминает."""
    assert long_memory.extract("Сколько договоров за октябрь?") == []
    assert long_memory.extract("") == []


def test_extract_ignores_sensitive():
    """Фраза с паспортом не превращается в запись памяти."""
    assert long_memory.extract("Запомни: Иванов — паспорт 28 15 634512") == []


def test_extract_long_phrase_ignored():
    """Слишком длинная фраза не разбирается (это не инструкция, а текст)."""
    assert long_memory.extract("Запомни: " + "а" * 600) == []


def test_remember_facts_writes_and_reports(memory_db):
    """Найденные факты записываются, возвращаются их ID."""
    written = long_memory.remember_facts([
        {"kind": "alias", "key": "Ромашка", "value": "ООО «Ромашка»"},
    ])
    assert len(written) == 1
    assert long_memory.list_records()[0]["key"] == "Ромашка"


def test_agent_remembers_after_reply(memory_db):
    """Агент записывает факт из фразы и сообщает об этом в ответе."""
    from core.agent.agent import Agent

    class FakeChat:
        """Подставной клиент: сразу отвечает текстом."""

        def __init__(self):
            self.calls = 0

        def complete_chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            return {"content": "Запомнил."}

        def begin_turn(self):
            pass

        def end_turn(self):
            pass

        def restore(self, text):
            return text

    agent = Agent(client=FakeChat())
    reply = agent.send("Запомни: Ромашка — это ООО Ромашка")
    assert reply.remembered
    assert long_memory.list_records()[0]["key"] == "Ромашка"


# ─────────────────────────────────────────────────────────────
# 7. Оценки
# ─────────────────────────────────────────────────────────────

def test_feedback_like_and_dislike(memory_db):
    """👍 и 👎 записываются в agent_feedback."""
    assert long_memory.feedback(1, session_id="s1", message_id=5) is not None
    assert long_memory.feedback(-1, session_id="s1", message_id=6) is not None
    stats = agent_memory.feedback_stats()
    assert stats == {"liked": 1, "disliked": 1, "total": 2}


def test_feedback_rejects_bad_rating(memory_db):
    """Оценка вне списка (0, 5, текст) не записывается."""
    assert long_memory.feedback(0) is None
    assert long_memory.feedback(5) is None
    assert long_memory.feedback("отлично") is None
    assert agent_memory.feedback_stats()["total"] == 0


def test_feedback_list(memory_db):
    """Оценки читаются списком, свежие сверху."""
    long_memory.feedback(1, comment="полезно")
    long_memory.feedback(-1, comment="не то")
    records = agent_memory.load_feedback()
    assert len(records) == 2
    assert records[0]["rating"] == -1


# ─────────────────────────────────────────────────────────────
# 8. Управление памятью
# ─────────────────────────────────────────────────────────────

def test_forget_one_record(memory_db):
    """Запись удаляется по ID."""
    record_id = long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    long_memory.remember_alias("Василёк", "ООО «Василёк»")
    assert long_memory.forget(record_id) is True
    assert [item["key"] for item in long_memory.list_records()] == ["Василёк"]


def test_forget_unknown_record(memory_db):
    """Удаление несуществующей записи — False, без исключения."""
    assert long_memory.forget(99999) is False


def test_forget_all(memory_db):
    """«Забыть всё» очищает память целиком."""
    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    long_memory.remember_habit("Ромашка", "НДС 20 %")
    assert long_memory.forget_all() == 2
    assert long_memory.list_records() == []


def test_forget_all_keeps_reference_data(memory_db):
    """«Забыть всё» НЕ трогает справочники и договоры."""
    conn = get_connection()
    try:
        conn.execute("INSERT INTO carriers (full_name, inn)"
                     " VALUES ('ООО «Ромашка»', '7701234567')")
        conn.commit()
    finally:
        conn.close()

    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    long_memory.forget_all()

    conn = get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM carriers").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_memory_stats(memory_db):
    """Сводка по памяти: сколько чего помним и какие оценки."""
    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    long_memory.remember_habit("Ромашка", "НДС 20 %")
    long_memory.feedback(1)
    stats = long_memory.stats()
    assert stats["total"] == 2
    assert stats["by_kind"]["alias"] == 1
    assert stats["feedback"]["liked"] == 1


def test_kind_titles_are_russian(memory_db):
    """Виды записей подписаны по-русски — окно настроек их не переводит."""
    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    record = long_memory.list_records()[0]
    assert record["kind_title"] == "Алиас"
    assert record["kind_hint"]


def test_memory_survives_reopen(isolated_db):
    """Память переживает переоткрытие соединения (она в базе, не в памяти)."""
    agent_memory.ensure_tables()
    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    assert long_memory.list_records()[0]["key"] == "Ромашка"


# ─────────────────────────────────────────────────────────────
# 9. Интерфейс настроек
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    """Одно приложение Qt на модуль (offscreen)."""
    pytest.importorskip("PyQt5")
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_settings_dialog_shows_memory(qapp, memory_db):
    """В настройках есть раздел «Что Максимилиан помнит» со списком."""
    from ui.agent_settings_dialog import AgentSettingsDialog

    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    dialog = AgentSettingsDialog({})
    try:
        assert dialog.memory_table.rowCount() == 1
        assert dialog.memory_table.item(0, 0).text() == "Алиас"
        assert dialog.memory_table.item(0, 1).text() == "Ромашка"
        assert "Записей в памяти: 1" in dialog.memory_hint.text()
    finally:
        dialog.deleteLater()


def test_settings_dialog_memory_empty(qapp, memory_db):
    """Пустая память подписана понятно."""
    from ui.agent_settings_dialog import AgentSettingsDialog

    dialog = AgentSettingsDialog({})
    try:
        assert dialog.memory_table.rowCount() == 0
        assert "ничего не запомнил" in dialog.memory_hint.text()
    finally:
        dialog.deleteLater()


def test_settings_dialog_forgets_selected(qapp, memory_db):
    """Кнопка «Удалить запись» удаляет выбранную строку."""
    from ui.agent_settings_dialog import AgentSettingsDialog

    long_memory.remember_alias("Ромашка", "ООО «Ромашка»")
    long_memory.remember_alias("Василёк", "ООО «Василёк»")
    dialog = AgentSettingsDialog({})
    try:
        dialog.memory_table.setCurrentCell(0, 0)
        dialog._on_forget_selected()
        assert dialog.memory_table.rowCount() == 1
    finally:
        dialog.deleteLater()


def test_settings_dialog_has_no_name_field(qapp, memory_db):
    """Поля «Имя» в настройках нет: имя зафиксировано (часть B)."""
    from core.settings_service import AGENT_NAME
    from ui.agent_settings_dialog import AgentSettingsDialog

    dialog = AgentSettingsDialog({"name": "Вася"})
    try:
        assert not hasattr(dialog, "name_edit")
        assert dialog.name_label.text() == AGENT_NAME
        assert dialog.get_settings()["name"] == AGENT_NAME
    finally:
        dialog.deleteLater()


def test_settings_dialog_proactive_flag(qapp, memory_db):
    """Настройка «подсказывать» читается и пишется."""
    from ui.agent_settings_dialog import AgentSettingsDialog

    dialog = AgentSettingsDialog({"proactive": True, "proactive_interval": 120})
    try:
        assert dialog.proactive.isChecked() is True
        assert dialog.proactive_interval.value() == 120
        values = dialog.get_settings()
        assert values["proactive"] is True
        assert values["proactive_interval"] == 120
    finally:
        dialog.deleteLater()
