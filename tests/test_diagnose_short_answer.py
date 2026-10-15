#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Часть 2: диагностика отвечает КОРОТКО и не роняет запрос (задание, A–D).

Что здесь проверяется
---------------------
1. **Причина 422** (A). Отчёт диагностики уходил модели целиком (4 120
   символов) и по дороге обрезался до 4 000 с многоточием
   (``core/agent/memory.py``). Оборванный JSON GigaChat не принимает и
   отвечает на ВЕСЬ запрос оператора:

       422 INVALID_PARAMS: invalid function result for function diagnose

   Тест `test_memory_add_truncates_tool_result_validly` держит именно этот
   случай: в истории не должно появляться обрезанного JSON.
2. **Короткий ответ** (B.1, вариант A). `diagnose` отдаёт модели счётчики и
   названия проблем — без системной информации, без подробностей, без пути
   к архиву, и гарантированно короче лимита памяти.
3. **Диалог открывает интерфейс** (B.1, вариант C). Инструмент просит
   панель открыть диалог диагностики; отчёт читает ОПЕРАТОР. Проверяется и
   путь просьбы (сигнал), и ответ «диагностика открыта».
4. **Запрос не падает** (D.2). Полный путь «запрос оператора → модель →
   инструмент → ответ модели» проходит с настоящим `Agent`, подставной
   моделью и подставным транспортом GigaChat, который отвергает запрос
   ровно так, как это делает GigaChat: если результат функции — не JSON,
   отвечает 422.

Тесты сети не трогают: транспорт подменён.
"""

import json
import logging
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import diagnostics  # noqa: E402
from core.agent.tools import AgentContext, build_registry  # noqa: E402
from core.json_text import (  # noqa: E402
    TRUNCATED_NOTE, is_valid_json, shorten_json_text,
)

#: Лимит памяти агента — тот самый, на котором ломался отчёт.
MEMORY_LIMIT = 4000


# ─────────────────────────────────────────────────────────────
# Заглушки
# ─────────────────────────────────────────────────────────────

class OpenedDialogs:
    """Подставной интерфейс: считает просьбы открыть диагностику."""

    def __init__(self, result: bool = True):
        self.result = result
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.result


def make_registry(opener=None):
    """Реестр инструментов с подставным интерфейсом (или без него)."""
    return build_registry(AgentContext(open_diagnostics=opener))


def payload_size(result) -> int:
    """Сколько символов займёт результат инструмента в запросе к модели."""
    return len(json.dumps(result, ensure_ascii=False, default=str))


# ─────────────────────────────────────────────────────────────
# A. Причина: обрезанный JSON
# ─────────────────────────────────────────────────────────────

def test_full_report_would_not_fit_memory_limit(isolated_db):
    """Причина воспроизводится: полный отчёт в лимит памяти не влезает."""
    report = diagnostics.run_diagnostics(live_network=False)
    full = json.dumps({"ok": True, "data": report.to_dict()},
                      ensure_ascii=False, default=str)

    assert len(full) > MEMORY_LIMIT, (
        "если отчёт стал короче лимита, эту защиту можно пересмотреть"
    )


def test_memory_add_truncates_tool_result_validly(isolated_db):
    """
    В истории инструмента не бывает оборванного JSON (та самая причина 422).

    Проверяется настоящий ``AgentMemory.add`` с настоящим отчётом: раньше
    здесь получался ``…``-обрыв, и GigaChat отвечал «invalid function
    result», роняя весь запрос оператора.
    """
    from core.agent.memory import AgentMemory

    report = diagnostics.run_diagnostics(live_network=False)
    payload = json.dumps({"ok": True, "data": report.to_dict()},
                         ensure_ascii=False, default=str)

    memory = AgentMemory(repository=_NoRepository())
    message = memory.add("tool", payload, tool_name="diagnose")

    assert len(message.content) <= MEMORY_LIMIT
    assert is_valid_json(message.content), (
        "содержимое сообщения role=tool обязано остаться JSON"
    )
    data = json.loads(message.content)
    assert data["truncated"] is True
    assert data["note"] == TRUNCATED_NOTE
    assert "…" not in message.content[-20:], "обрыв многоточием недопустим"


def test_long_text_message_is_truncated_as_before(isolated_db):
    """Обычный текст (не JSON) режется как прежде — многоточием."""
    from core.agent.memory import AgentMemory

    memory = AgentMemory(repository=_NoRepository())
    message = memory.add("user", "а" * 5000)

    assert len(message.content) == MEMORY_LIMIT + 1
    assert message.content.endswith("…")


class _NoRepository:
    """Репозиторий-заглушка: история остаётся в памяти теста."""

    def append(self, *args, **kwargs):
        return None

    def load(self, *args, **kwargs):
        return []

    def clear_session(self, *args, **kwargs):
        return 0

    def purge_other_sessions(self, *args, **kwargs):
        return 0


# ─────────────────────────────────────────────────────────────
# core/json_text.py — общая защита
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("limit", [120, 200, 500, 1000, 4000])
def test_shorten_keeps_json_valid(limit):
    """На любом пределе результат остаётся корректным JSON."""
    payload = json.dumps({"ok": True, "message": "и" * 600,
                          "data": {"items": ["x" * 400] * 40}},
                         ensure_ascii=False)

    short = shorten_json_text(payload, limit)

    assert len(short) <= limit
    assert is_valid_json(short), short[:200]


def test_shorten_keeps_short_text_untouched():
    """Короткий текст не трогается вовсе."""
    payload = '{"ok": true, "count": 3}'

    assert shorten_json_text(payload, 4000) == payload


def test_shorten_reports_key_fields():
    """Из длинного ответа сохраняются важные поля, а не только пометка."""
    payload = json.dumps({"ok": False, "error": "нет соединения",
                          "data": {"poll": "п" * 9000}}, ensure_ascii=False)

    data = json.loads(shorten_json_text(payload, 500))

    assert data["ok"] is False
    assert "соединения" in data["error"]
    assert data["truncated"] is True


def test_shorten_marks_truncation_for_the_model():
    """Модель видит, что подробности не влезли, и не выдумывает их."""
    payload = json.dumps({"ok": True, "data": {"x": "я" * 5000}},
                         ensure_ascii=False)

    data = json.loads(shorten_json_text(payload, 300))

    assert data["truncated"] is True
    assert data["note"] == TRUNCATED_NOTE


def test_shorten_non_json_becomes_valid_json():
    """Не-JSON на входе тоже превращается в корректный JSON."""
    short = shorten_json_text("<html>ошибка сервера</html>" * 300, 400)

    assert len(short) <= 400
    assert is_valid_json(short)
    assert json.loads(short)["ok"] is False


def test_shorten_handles_compactable_payload():
    """«Полотно» из отступов достаточно сжать — данные не теряются."""
    payload = json.dumps({"ok": True, "data": {"a": [1, 2, 3] * 40}},
                         ensure_ascii=False, indent=4)
    limit = len(json.dumps({"ok": True, "data": {"a": [1, 2, 3] * 40}},
                           ensure_ascii=False)) + 5

    short = shorten_json_text(payload, limit)

    assert len(short) <= limit
    assert json.loads(short)["ok"] is True


# ─────────────────────────────────────────────────────────────
# B.1, D.1. Короткий ответ инструмента
# ─────────────────────────────────────────────────────────────

def test_diagnose_returns_short_result(isolated_db):
    """D.1. Результат диагностики короткий и без системной информации."""
    registry = make_registry()
    result = registry.call("diagnose", {})

    assert result["ok"] is True
    data = result["data"]
    assert set(data) == {"checks", "counts", "worst", "summary", "problems"}
    assert isinstance(data["checks"], int)
    assert "system" not in data
    assert "archive" not in data
    assert payload_size(result) < 1200, "ответ модели должен быть коротким"


def test_diagnose_result_fits_memory_limit(isolated_db):
    """Результат заведомо короче лимита памяти — обрезки не будет вовсе."""
    registry = make_registry()

    result = registry.call("diagnose", {})

    assert payload_size(result) < MEMORY_LIMIT


def test_diagnose_problems_have_no_details(isolated_db):
    """У проблем — имя и одна строка, без подробностей проверки."""
    registry = make_registry()
    result = registry.call("diagnose", {})

    for problem in result["data"]["problems"]:
        assert "details" not in problem
        assert problem["status"] in ("warn", "fail")


def test_diagnose_message_tells_where_the_report_is(isolated_db):
    """В ответе сказано, где оператор видит полный отчёт."""
    registry = make_registry()
    result = registry.call("diagnose", {})

    assert "диагностик" in result["message"].lower()


# ─────────────────────────────────────────────────────────────
# B.1 (вариант C): диалог диагностики открывает интерфейс
# ─────────────────────────────────────────────────────────────

def test_diagnose_asks_to_open_dialog(isolated_db):
    """C. Инструмент просит интерфейс открыть диалог диагностики."""
    opener = OpenedDialogs()
    registry = make_registry(opener)

    result = registry.call("diagnose", {})

    assert opener.calls == 1
    assert result["opened_diagnostics"] is True
    assert "диагностик" in result["message"].lower()
    assert "открыл" in result["message"].lower()


def test_diagnose_says_where_to_look_without_interface(isolated_db):
    """Интерфейса рядом нет — инструмент подсказывает пункт меню."""
    registry = make_registry(None)

    result = registry.call("diagnose", {})

    assert result["opened_diagnostics"] is False
    assert "Справка" in result["message"]


def test_diagnose_survives_broken_interface(isolated_db):
    """Сломанный интерфейс не роняет диагностику (B.2)."""
    def broken() -> bool:
        raise RuntimeError("окно уже закрыто")

    registry = make_registry(broken)

    result = registry.call("diagnose", {})

    assert result["ok"] is True
    assert result["opened_diagnostics"] is False


class _SignalCollector:
    """
    Приёмник сигнала БЕЗ аргументов.

    ``pyqtSignal()`` не передаёт значений, поэтому обычный ``list.append``
    здесь не годится: он ждёт аргумент, а исключение внутри слота PyQt5
    превращает в падение процесса.
    """

    def __init__(self):
        self.calls = 0

    def on_request(self) -> None:
        self.calls += 1


def test_diagnostics_signal_is_connected():
    """
    Панель Максимилиана открывает диалог по сигналу инструмента.

    Слот диалога отключается: настоящий `on_open_diagnostics` открывает
    модальное окно и ждёт оператора (`exec_()`), а тест проверяет доставку
    сигнала, а не окно.
    """
    from PyQt5.QtWidgets import QApplication, QMainWindow

    from core.settings_service import get_settings_service
    from ui.agent_widget import AgentWidget

    app = QApplication.instance() or QApplication([])
    assert hasattr(AgentWidget, "diagnostics_requested")

    window = QMainWindow()
    get_settings_service().update_agent({"enabled": True,
                                         "show_floating": False})
    widget = AgentWidget(window, client_factory=lambda: None)
    assert widget._context().open_diagnostics == widget.request_diagnostics, (
        "инструмент diagnose должен получать этот канал из контекста"
    )

    collector = _SignalCollector()
    widget.diagnostics_requested.disconnect()
    widget.diagnostics_requested.connect(collector.on_request)

    assert widget.request_diagnostics() is True
    for _ in range(3):
        app.processEvents()

    assert collector.calls == 1, "сигнал должен дойти до слота диалога"
    assert widget.on_open_diagnostics is not None
    widget.shutdown()
    window.deleteLater()


# ─────────────────────────────────────────────────────────────
# D.2. Полный путь запроса: 422 не появляется
# ─────────────────────────────────────────────────────────────

class StrictGigaChat:
    """
    Подставной GigaChat, который ведёт себя как настоящий.

    Настоящий endpoint разбирает результат функции как JSON и отвергает
    запрос целиком, если это не JSON:

        422 INVALID_PARAMS: invalid function result for function diagnose

    Заглушка повторяет эту проверку — с ней видно, что запрос оператора
    проходит (раньше здесь было падение).
    """

    def __init__(self, tool_name: str = "diagnose", arguments: str = "{}",
                 final_text: str = "Проверил: проблем нет."):
        self.tool_name = tool_name
        self.arguments = arguments
        self.final_text = final_text
        self.requests = 0
        self.tool_payloads = []

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.requests += 1
        for message in messages:
            if message.get("role") == "tool":
                content = message.get("content") or ""
                self.tool_payloads.append(content)
                try:
                    json.loads(content)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        "GigaChat вернул ошибку 422: INVALID_PARAMS: "
                        "invalid function result for function "
                        f"{self.tool_name} json string `{str(content)[:80]}"
                    ) from exc

        if self.requests == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": self.tool_name,
                                 "arguments": self.arguments},
                }],
            }
        return {"role": "assistant", "content": self.final_text,
                "tool_calls": []}


def test_full_agent_request_does_not_fail(isolated_db):
    """D.2. «Что-то сломалось?» — запрос проходит, а не падает с 422."""
    from core.agent.agent import Agent
    from core.agent.memory import AgentMemory

    client = StrictGigaChat()
    agent = Agent(client=client, context=AgentContext(),
                  memory=AgentMemory(repository=_NoRepository()))

    reply = agent.send("Что-то сломалось?")

    assert reply.text
    assert "diagnose" in reply.tool_names
    assert client.tool_payloads, "результат инструмента должен уйти модели"
    assert all(is_valid_json(payload) for payload in client.tool_payloads)
    assert all(len(payload) < MEMORY_LIMIT for payload in client.tool_payloads)


def test_agent_request_with_big_tool_result_is_trimmed(isolated_db):
    """
    Любой инструмент с большим результатом больше не ломает запрос.

    Это и есть класс ошибки: оборвать JSON мог не только `diagnose`.
    """
    from core.agent.agent import Agent
    from core.agent.memory import AgentMemory
    from core.agent.tools import Tool, _json_schema

    client = StrictGigaChat(tool_name="big_tool", final_text="готово")
    agent = Agent(client=client, context=AgentContext(),
                  memory=AgentMemory(repository=_NoRepository()))
    agent.registry.register(Tool(
        name="big_tool",
        description="Инструмент с очень большим результатом (для теста).",
        parameters=_json_schema({}),
        function=lambda **_: {"ok": True, "data": {"полотно": "я" * 9000}},
        summary="Большой результат",
    ))

    reply = agent.send("дай большой результат")

    assert reply.text
    payload = client.tool_payloads[-1]
    assert is_valid_json(payload)
    assert len(payload) <= MEMORY_LIMIT
    assert json.loads(payload)["truncated"] is True


# ─────────────────────────────────────────────────────────────
# Промпт и описание инструмента
# ─────────────────────────────────────────────────────────────

def test_prompt_explains_that_report_is_visible_to_operator():
    """Промпт: подробности отчёта оператор видит сам, выдумывать нельзя."""
    from core.agent.prompt import build_system_prompt

    prompt = build_system_prompt()

    assert "СЛОМАЛОСЬ" in prompt
    assert "выдумыв" in prompt.lower()
    assert "открыт" in prompt.lower()


def test_tool_description_promises_short_answer():
    """Описание инструмента обещает короткий итог и открытый диалог."""
    registry = make_registry()
    description = registry.get("diagnose").description

    assert "КОРОТКИЙ" in description
    assert "диалог" in description.lower()


def test_diagnostics_module_logger_untouched(caplog):
    """Диагностика пишет в свой логгер, а не в корневой (правило проекта)."""
    with caplog.at_level(logging.INFO, logger="core.diagnostics"):
        diagnostics.run_diagnostics(live_network=False, checks=["version"])

    assert all(record.name.startswith("core.diagnostics")
               or record.name.startswith("core")
               for record in caplog.records)
