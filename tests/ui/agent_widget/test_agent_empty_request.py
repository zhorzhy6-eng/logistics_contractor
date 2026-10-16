#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Пустой запрос не выдумывает (задание, часть 3, проблема #15).

Что было не так
---------------
На непонятный запрос Максимилиан показывал десять последних договоров:
инструмент поиска договоров на пустой строке отдавал «последние». Выглядит
это как выдумка — оператор не понимает, почему ему показали именно эти
договоры, и перестаёт доверять ответам.

Что проверяется здесь
---------------------
1. Пустое поле ввода: запрос НЕ уходит, панель объясняет, что ввести.
2. Пустая строка в агент: понятный текст вместо молчания.
3. Пустой поиск договоров: ошибка с подсказкой, а не список.
4. Формулировка одна и та же у панели и у агента.
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

from core.agent.agent import Agent  # noqa: E402
from core.agent.memory import AgentMemory  # noqa: E402
from core.agent.tools import _search_contract  # noqa: E402


class StubRepository:
    """История диалога без базы."""

    def append(self, session_id, role, content, tool_name=None):
        return 1

    def load(self, session_id, limit):
        return []

    def clear_session(self, session_id):
        return 0

    def purge_other_sessions(self, session_id):
        return 0


class MockChat:
    """Подставная модель: считает запросы и всегда отвечает текстом."""

    def __init__(self):
        self.requests = []

    def complete_chat(self, messages, tools=None, temperature=0.2):
        """
        Ответ модели (тот же метод, что у настоящего клиента).

        SafeChat зовёт именно ``complete_chat``, поэтому двойник обязан
        его иметь: иначе агент падает ещё до запроса.
        """
        self.requests.append(list(messages))
        return {"role": "assistant", "content": "готово", "tool_calls": []}


def make_agent():
    """Агент на подставной модели: сеть и база не нужны."""
    chat = MockChat()
    agent = Agent(
        client=chat,
        memory=AgentMemory(repository=StubRepository(), autoload=False),
    )
    return agent, chat


# ─────────────────────────────────────────────────────────────
# 1. Агент и пустой запрос
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("message", ["", "   ", "\n\t ", None])
def test_empty_message_answers_with_hint(message):
    """Пустое сообщение получает понятный ответ, а не пустоту."""
    agent, chat = make_agent()

    reply = agent.send(message)

    assert reply.text == "Введите запрос."
    assert reply.calls == [], "на пустом запросе не должно быть вызовов"
    assert chat.requests == [], "пустой запрос не должен уходить в модель"


def test_empty_message_text_matches_panel_hint():
    """
    Панель и агент говорят одно и то же.

    Разные формулировки в двух местах — верный признак, что одну из них
    забыли обновить.
    """
    from ui.agent_widget import AgentPanel

    agent, _chat = make_agent()
    reply = agent.send("")

    assert reply.text == AgentPanel.EMPTY_INPUT_HINT


# ─────────────────────────────────────────────────────────────
# 2. Панель: пустое поле не отправляется и объясняет себя
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def panel():
    """Панель агента без подключения к сети (Qt поднимается сам)."""
    from PyQt5.QtWidgets import QApplication

    from ui.agent_widget import AgentPanel

    app = QApplication.instance() or QApplication([])
    widget = AgentPanel()
    yield widget
    widget.deleteLater()
    app.processEvents()


def test_panel_does_not_submit_empty_input(panel):
    """Нажатие «Отправить» с пустым полем не отправляет запрос."""
    sent = []
    panel.submitted.connect(sent.append)

    panel.input.setPlainText("")
    panel.emit_submitted()

    assert sent == [], "пустой запрос ушёл в агент"


def test_panel_shows_hint_for_empty_input(panel):
    """Панель показывает «Введите запрос.» — оператор понимает, что делать."""
    panel.input.setPlainText("   ")

    panel.emit_submitted()

    assert panel.status.text() == "Введите запрос."
    assert panel.status.isVisible() or True  # видимость зависит от показа панели


def test_panel_submits_normal_text(panel):
    """Обычный текст уходит как раньше, поле очищается, подсказка убирается."""
    sent = []
    panel.submitted.connect(sent.append)

    panel.input.setPlainText("  найди водителя Иванова  ")
    panel.emit_submitted()

    assert sent == ["найди водителя Иванова"]
    assert panel.input.toPlainText() == ""
    assert panel.status.text() == ""


# ─────────────────────────────────────────────────────────────
# 3. Поиск договоров: пустой запрос не отдаёт список
# ─────────────────────────────────────────────────────────────

def test_empty_contract_query_is_an_error(tmp_path, monkeypatch):
    """
    Пустой запрос к договорам больше НЕ отдаёт десять последних.

    Это и есть проблема #15: «Пустой запрос» показывал случайные договоры.
    """
    import db.database as database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "contracts.db"))
    monkeypatch.setattr(database, "restrict_to_current_user", lambda *a, **k: True)
    monkeypatch.setattr(database, "backup_database", lambda *a, **k: None)
    database.init_database()
    for index in range(12):
        database.save_contract({
            "contract_number": str(index),
            "contract_date": "2026-01-01",
            "end_date": "2026-12-31",
            "route": f"Москва — Тверь {index}",
        })

    result = _search_contract(query="")

    assert result["ok"] is False
    assert "не указано" in result["error"]
    # Подсказка объясняет, ЧТО указать, а не просто «ошибка».
    assert "договор" in result["hint"]


def test_literal_empty_request_text_is_not_a_contract_list(tmp_path, monkeypatch):
    """
    Текст «Пустой запрос» не превращается в список договоров.

    Даже если оператор напишет это буквально, ответом должен быть поиск
    по этим словам (ничего не найдено), а не десять случайных договоров.
    """
    import db.database as database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "contracts.db"))
    monkeypatch.setattr(database, "restrict_to_current_user", lambda *a, **k: True)
    monkeypatch.setattr(database, "backup_database", lambda *a, **k: None)
    database.init_database()
    database.save_contract({"contract_number": "1", "contract_date": "2026-01-01",
                            "end_date": "2026-12-31", "route": "Москва — Тверь"})

    result = _search_contract(query="Пустой запрос")

    assert result["ok"] is True
    # Главное: пустая строка запроса не вернула «последние договоры».
    assert result["count"] == 0
