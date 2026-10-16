#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Три предохранителя на один запрос: шаги, таймаут, зацикливание.

ЗАЧЕМ. Лимит шагов поднят с 3 до 20 («Куда тыкать? Мне нужно внести данные
по организации» не влезало в три шага). Чем больше лимит, тем нужнее
остальные ограничители:

  1. ``max_tool_steps = 20`` — сколько вызовов инструментов на запрос;
  2. таймаут запроса (``max_request_seconds``) — общее время ожидания;
  3. ``REPEAT_LIMIT`` — остановка, если модель зовёт один и тот же инструмент
     с одними и теми же аргументами и не получает новых данных.

Всё на MOCK: сеть не трогается ни одним тестом, GigaChat подменяется
объектом с методом ``complete_chat``, часы — подставным ``time.monotonic``.
"""

import json
import sys
from pathlib import Path

import pytest

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.agent.agent as agent_module  # noqa: E402
from core.agent.agent import (  # noqa: E402
    DEFAULT_MAX_REQUEST_SECONDS, DEFAULT_MAX_TOOL_STEPS, HELP_CHOICES,
    REPEAT_LIMIT, Agent, ToolCall, _call_signature, _tracks_repeat,
)
from core.agent.memory import AgentMemory  # noqa: E402
from core.agent.tools import AgentContext, Tool, ToolRegistry  # noqa: E402
from core.settings_service import (  # noqa: E402
    AGENT_LIMITS, DEFAULT_AGENT_SETTINGS, normalize_agent_settings,
)


class NullRepository:
    """История без базы: тесту нужна только память диалога."""

    def append(self, *args, **kwargs):
        return 1

    def load(self, *args, **kwargs):
        return []

    def count(self, *args, **kwargs):
        return 0

    def clear_session(self, *args, **kwargs):
        return 0

    def purge_other_sessions(self, *args, **kwargs):
        return 0


class ScriptedClient:
    """
    Подставная модель: отвечает по сценарию.

    Каждый элемент сценария — либо вызов инструмента (``("tool", имя,
    аргументы)``), либо текст (``("text", "готово")``). Когда сценарий
    кончился, отвечает текстом: так тест не уходит в бесконечный цикл.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.seen_messages = []

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.seen_messages.append(list(messages))
        index = self.calls
        self.calls += 1
        if index >= len(self.script):
            return {"role": "assistant", "content": "готово"}
        step = self.script[index]
        if step[0] == "text":
            return {"role": "assistant", "content": step[1]}
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"call_{index}",
                "type": "function",
                "function": {
                    "name": step[1],
                    "arguments": json.dumps(step[2], ensure_ascii=False),
                },
            }],
        }


def build_registry(answer=None, calls_log=None):
    """Реестр с одним инструментом-«смотрелкой», который зовётся по сценарию."""
    def function(**arguments):
        if calls_log is not None:
            calls_log.append(arguments)
        return answer if answer is not None else {"ok": True, "data": []}

    return ToolRegistry([
        Tool(name="look_at_form", description="Смотрит форму",
             summary="Поля формы",
             parameters={"type": "object", "properties": {}},
             function=function),
    ])


def build_agent(script, answer=None, max_tool_steps=DEFAULT_MAX_TOOL_STEPS,
                max_request_seconds=None, calls_log=None):
    """
    Агент с подставной моделью.

    Таймаут по умолчанию НЕ передаётся: агент берёт своё умолчание (две
    минуты). Тесты, которым нужен свой срок, задают его явно.
    """
    memory = AgentMemory(session_id="safeguards", repository=NullRepository())
    client = ScriptedClient(script)
    if max_request_seconds is None:
        return Agent(client=client, context=AgentContext(), memory=memory,
                     registry=build_registry(answer, calls_log),
                     max_tool_steps=max_tool_steps)
    return Agent(client=client, context=AgentContext(), memory=memory,
                 registry=build_registry(answer, calls_log),
                 max_tool_steps=max_tool_steps,
                 max_request_seconds=max_request_seconds)


# ─────────────────────────────────────────────────────────────
# D.1. Лимит шагов = 20
# ─────────────────────────────────────────────────────────────

def test_default_step_limit_is_twenty():
    """Лимит по умолчанию — 20 (было 3 в рабочем файле, 5 в умолчаниях)."""
    assert DEFAULT_MAX_TOOL_STEPS == 20
    assert DEFAULT_AGENT_SETTINGS["max_tool_steps"] == 20


def test_twenty_is_the_highest_allowed_value():
    """20 — верхняя граница настройки: больше через файл не поставить."""
    low, high = AGENT_LIMITS["max_tool_steps"]
    assert low >= 1 and high == 20
    assert normalize_agent_settings({"max_tool_steps": 500})["max_tool_steps"] \
        == DEFAULT_AGENT_SETTINGS["max_tool_steps"]


def test_agent_uses_twenty_steps_by_default():
    """Агент без аргументов делает ровно 20 вызовов, а не 3 и не 5."""
    calls_log = []
    script = [("tool", "look_at_form", {"field": "inn"})] * 30
    agent = build_agent(script, answer={"ok": True, "data": [1]},
                        calls_log=calls_log)
    reply = agent.send("Покажи всё")

    assert reply.step_limit_reached is True
    assert reply.stop_reason == "steps"
    #: Данные приходят каждый раз новые (непустой список) — значит это не
    #: цикл, а именно лимит шагов.
    assert len(reply.calls) == 20
    assert "лимит исчерпан" in reply.text


def test_project_settings_file_asks_for_twenty_steps():
    """Рабочий файл настроек проекта: max_tool_steps = 20."""
    data = json.loads((PROJECT_ROOT / "config" / "settings.json")
                      .read_text(encoding="utf-8"))
    assert data["agent"]["max_tool_steps"] == 20


def test_example_settings_file_asks_for_twenty_steps():
    """Файл-образец не отстаёт от рабочего файла."""
    data = json.loads((PROJECT_ROOT / "config" / "settings.example.json")
                      .read_text(encoding="utf-8"))
    assert data["agent"]["max_tool_steps"] == 20


# ─────────────────────────────────────────────────────────────
# D.2. Человеческий ответ вместо отладочного дампа
# ─────────────────────────────────────────────────────────────

def test_stopped_answer_is_human_and_offers_next_steps():
    """Ответ по лимиту — предложение разбить задачу, а не отчёт."""
    script = [("tool", "look_at_form", {"field": "inn"})] * 30
    agent = build_agent(script, answer={"ok": True, "data": [1, 2, 3]},
                        max_tool_steps=3)
    reply = agent.send("Куда тыкать? Мне нужно внести данные по организации")

    for line in HELP_CHOICES:
        assert line in reply.text


def test_stopped_answer_has_no_technical_ids():
    """
    Ни машинных имён, ни операторов, ни идентификаторов интерфейса.

    Проверяются ровно те строки, которые задание просит убрать:
    ``window=Генератор…``, ``contract_type=perevozka`` и список вкладок.
    """
    script = [("tool", "look_at_form", {"field": "inn"})] * 5
    agent = build_agent(
        script,
        answer={"ok": True, "data": {"window": "Генератор договоров",
                                     "contract_type": "perevozka",
                                     "tabs": ["Контракт", "Машины", "Водители"]}},
        max_tool_steps=3,
    )
    reply = agent.send("Покажи всё")

    assert "window=" not in reply.text
    assert "contract_type" not in reply.text
    assert "perevozka" not in reply.text
    assert "look_at_form" not in reply.text
    assert "{" not in reply.text and "}" not in reply.text
    #: Идентификаторы интерфейса в ответе не перечисляются вовсе.
    assert "Генератор" not in reply.text
    assert "Контракт" not in reply.text


def test_stopped_answer_mentions_what_failed_without_internals():
    """О неудаче сказано действием, без внутреннего текста ошибки."""
    script = [("tool", "look_at_form", {"field": "inn"})] * 5
    agent = build_agent(script, answer={"ok": False, "error": "нет доступа"},
                        max_tool_steps=2)
    reply = agent.send("Покажи всё")

    assert "Не получилось" in reply.text
    assert "нет доступа" not in reply.text


def test_stopped_answer_is_written_to_history():
    """
    Ответ по лимиту попадает в историю диалога.

    Иначе следующий вопрос оператора продолжил бы разговор, будто этого
    ответа не было.
    """
    script = [("tool", "look_at_form", {"field": "inn"})] * 5
    agent = build_agent(script, answer={"ok": True, "data": []},
                        max_tool_steps=2)
    reply = agent.send("Покажи всё")

    assert agent.memory.messages[-1].role == "assistant"
    assert agent.memory.messages[-1].content == reply.text


def test_stopped_answer_fits_the_length_limit():
    """Ответ короткий: две тысячи символов оператору не нужны."""
    from core.agent.agent import ANSWER_LIMIT_AFTER_LIMIT

    script = [("tool", "look_at_form", {"field": "inn"})] * 5
    agent = build_agent(script, answer={"ok": True, "data": [1]},
                        max_tool_steps=3)
    reply = agent.send("Покажи всё")
    assert len(reply.text) <= ANSWER_LIMIT_AFTER_LIMIT


def test_stopped_answer_does_not_repeat_the_same_line():
    """
    Одинаковые результаты не повторяются в строке «Успел».

    Живая проба (15.10.2026): двадцать одинаковых вызовов давали
    «список открытых вкладок — найдено 1» трижды подряд, и строка вытесняла
    из ответа сам выбор дальнейших шагов.
    """
    calls_log = []
    script = [("tool", "look_at_form", {"field": "inn"})] * 40
    agent = build_agent(script, answer={"ok": True, "data": [1]},
                        calls_log=calls_log)
    reply = agent.send("Покажи всё")

    assert reply.stop_reason == "steps"
    assert reply.text.count("найдено 1") == 1


# ─────────────────────────────────────────────────────────────
# D.3. Зацикливание останавливается
# ─────────────────────────────────────────────────────────────

def test_loop_stops_the_request():
    """Один и тот же вызов без результата повторяется — стоп на третьем."""
    calls_log = []
    script = [("tool", "look_at_form", {"field": "inn"})] * 30
    agent = build_agent(script, answer={"ok": True, "data": []},
                        calls_log=calls_log)
    reply = agent.send("Найди организацию")

    assert reply.loop_detected is True
    assert reply.step_limit_reached is True
    assert reply.stop_reason == "loop"
    #: Ровно REPEAT_LIMIT обращений: на третьем повторе остановились.
    assert len(calls_log) == REPEAT_LIMIT
    #: Модель спросили столько раз, сколько вызовов сделано и ещё раз после
    #: третьего (именно её ответ и был признан повтором).
    assert reply.steps == REPEAT_LIMIT + 1
    assert "повторил одно и то же действие" in reply.text


def test_loop_answer_offers_choices_too():
    """Даже при зацикливании оператор получает выбор, а не извинение."""
    script = [("tool", "look_at_form", {"field": "inn"})] * 30
    agent = build_agent(script, answer={"ok": True, "data": []})
    reply = agent.send("Найди организацию")
    for line in HELP_CHOICES:
        assert line in reply.text


def test_loop_needs_three_repeats_not_two():
    """Два повтора подряд — ещё не цикл: модель могла просто уточнить."""
    calls_log = []
    script = [("tool", "look_at_form", {"field": "inn"})] * 2
    script.append(("text", "вот что нашёл"))
    agent = build_agent(script, answer={"ok": True, "data": []},
                        calls_log=calls_log)
    reply = agent.send("Найди организацию")

    assert reply.loop_detected is False
    assert reply.step_limit_reached is False
    assert reply.text == "вот что нашёл"
    assert len(calls_log) == 2


def test_different_arguments_are_not_a_loop():
    """Разные аргументы — разные вызовы: 20 поисков по разным словам можно."""
    calls_log = []
    script = [("tool", "look_at_form", {"field": f"слово{index}"})
              for index in range(6)]
    agent = build_agent(script, answer={"ok": True, "data": []},
                        calls_log=calls_log)
    reply = agent.send("Найди организацию")

    assert reply.loop_detected is False
    assert reply.step_limit_reached is False
    assert len(calls_log) == 6


def test_progress_resets_the_repeat_counter():
    """
    Нашёл данные — счётчик повторов обнуляется.

    Так выглядит законная работа: инструмент зовётся снова и снова, но
    каждый раз приносит новое (поиск нашёл запись, форма прочитана).
    """
    calls_log = []
    script = [("tool", "look_at_form", {"field": "Ромашка"})] * 25
    agent = build_agent(script, answer={"ok": True, "data": [{"id": 1}]},
                        calls_log=calls_log)
    reply = agent.send("Найди Ромашку")

    assert reply.loop_detected is False
    assert reply.stop_reason == "steps"          #: остановил лимит, а не цикл
    assert len(calls_log) == DEFAULT_MAX_TOOL_STEPS


def test_declined_calls_count_as_repeats():
    """
    Оператор отменяет одно и то же действие — это тоже цикл.

    Инструмент, требующий подтверждения, каждый раз возвращает «оператор
    отменил», и без этого правила модель просила бы одно и то же двадцать раз.
    """
    def confirm(tool, arguments):
        return False

    def function(**_):
        return {"ok": True, "data": []}

    registry = ToolRegistry([
        Tool(name="fill_it", description="Заполняет", summary="Заполнение формы",
             parameters={"type": "object", "properties": {}},
             function=function, requires_confirmation=True),
    ])
    memory = AgentMemory(session_id="declined", repository=NullRepository())
    client = ScriptedClient([("tool", "fill_it", {"field": "inn"})] * 30)
    agent = Agent(client=client, context=AgentContext(confirm=confirm),
                  memory=memory, registry=registry)

    reply = agent.send("Заполни ИНН")

    assert reply.loop_detected is True
    assert len(reply.calls) == REPEAT_LIMIT
    assert all(call.declined for call in reply.calls)


def test_same_arguments_in_another_order_are_the_same_call():
    """Порядок ключей в аргументах значения не имеет."""
    first = ToolCall(name="look_at_form",
                     arguments={"field": "inn", "tab": "carrier"})
    second = ToolCall(name="look_at_form",
                      arguments={"tab": "carrier", "field": "inn"})
    assert _call_signature(first) == _call_signature(second)


def test_two_tools_are_not_a_loop():
    """Чередование двух инструментов циклом не считается."""
    registry = ToolRegistry([
        Tool(name=name, description="", summary=name, parameters={},
             function=lambda **_: {"ok": True, "data": []})
        for name in ("first", "second")
    ])
    memory = AgentMemory(session_id="two", repository=NullRepository())
    client = ScriptedClient([("tool", "first", {}), ("tool", "second", {}),
                             ("tool", "first", {}), ("tool", "second", {}),
                             ("text", "готово")])
    agent = Agent(client=client, context=AgentContext(), memory=memory,
                  registry=registry)
    reply = agent.send("Сделай два дела")

    assert reply.loop_detected is False
    assert reply.tool_names == ["first", "second", "first", "second"]


def test_tracks_repeat_rules():
    """Правило повтора: те же аргументы + нет новых данных."""
    call = ToolCall(name="look_at_form", arguments={"field": "inn"},
                    result={"ok": True, "data": []})
    signature = _call_signature(call)

    assert _tracks_repeat(call, signature, signature) is True
    assert _tracks_repeat(call, signature, "") is False
    assert _tracks_repeat(call, signature, "другой|{}") is False

    with_data = ToolCall(name="look_at_form", arguments={"field": "inn"},
                         result={"ok": True, "data": [{"id": 1}]})
    assert _tracks_repeat(with_data, signature, signature) is False

    failed = ToolCall(name="look_at_form", arguments={"field": "inn"},
                      result={"ok": False, "error": "нет доступа"})
    assert _tracks_repeat(failed, signature, signature) is True


# ─────────────────────────────────────────────────────────────
# D.4. Таймаут запроса
# ─────────────────────────────────────────────────────────────

class FakeClock:
    """Подставные часы: время двигается только тогда, когда тест велит."""

    def __init__(self, monkeypatch, start=1000.0):
        self.now = float(start)
        monkeypatch.setattr(agent_module.time, "monotonic", self.monotonic)

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def test_timeout_stops_before_the_next_step(monkeypatch):
    """Срок вышел — новый шаг не начинается, ответ формируется сразу."""
    clock = FakeClock(monkeypatch)
    calls_log = []

    def function(**arguments):
        calls_log.append(arguments)
        clock.advance(85)                  #: каждый инструмент — 85 секунд
        return {"ok": True, "data": [1]}

    registry = ToolRegistry([
        Tool(name="look_at_form", description="", summary="Поля формы",
             parameters={}, function=function),
    ])
    memory = AgentMemory(session_id="timeout", repository=NullRepository())
    client = ScriptedClient([("tool", "look_at_form", {"field": "inn"})] * 30)
    agent = Agent(client=client, context=AgentContext(), memory=memory,
                  registry=registry, max_request_seconds=120.0)

    reply = agent.send("Внеси данные по организации")

    assert reply.timed_out is True
    assert reply.stop_reason == "timeout"
    #: 85 + 85 = 170 > 120: третий вызов уже не делается.
    assert len(calls_log) == 2
    assert reply.steps == 2                    #: и к модели больше не ходили
    assert "120 секунд" in reply.text
    assert "Не уложился" in reply.text
    #: Сделанное названо: два вызова принесли данные.
    assert "Успел" in reply.text


def test_timeout_answer_is_human():
    """Ответ по таймауту — человеческий, с выбором дальнейшего шага."""
    script = [("tool", "look_at_form", {"field": "inn"})] * 30
    agent = build_agent(script, answer={"ok": True, "data": []},
                        max_request_seconds=1e-9)
    reply = agent.send("Куда тыкать?")

    assert reply.timed_out is True
    assert "Не уложился в 1 секунд" in reply.text   #: срок показывается честно
    for line in HELP_CHOICES:
        assert line in reply.text


def test_timeout_already_expired_stops_before_the_model():
    """Срок истёк до первого шага — модель не дёргается вовсе, шагов ноль."""
    script = [("tool", "look_at_form", {"field": "inn"})] * 30
    agent = build_agent(script, answer={"ok": True, "data": []},
                        max_request_seconds=1e-9)
    reply = agent.send("Куда тыкать?")

    assert reply.timed_out is True
    assert reply.stop_reason == "timeout"
    assert reply.steps == 0
    assert reply.calls == []
    assert "Не уложился" in reply.text
    for line in HELP_CHOICES:
        assert line in reply.text


def test_timeout_after_the_model_reply_drops_the_unfinished_block(
        monkeypatch):
    """
    Таймаут между ответом модели и вызовом инструмента: мусор не остаётся.

    Ответ модели с вызовами уже лёг в историю, а инструменты выполнять уже
    не будем. Незакрытый блок из памяти убирается — иначе он висел бы до
    следующего запроса оператора.
    """
    clock = FakeClock(monkeypatch)

    def project_state():
        """Модель отвечает мгновенно, но время уже вышло (например, долгая сеть)."""
        clock.advance(500)
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "look_at_form",
                             "arguments": json.dumps({"field": "inn"})},
            }],
        }

    class SlowClient:
        def complete_chat(self, messages, tools=None, temperature=0.2):
            return project_state()

    calls_log = []
    memory = AgentMemory(session_id="timeout_block", repository=NullRepository())
    agent = Agent(client=SlowClient(), context=AgentContext(), memory=memory,
                  registry=build_registry(answer={"ok": True, "data": []},
                                          calls_log=calls_log),
                  max_request_seconds=120.0)
    reply = agent.send("Внеси данные")

    assert reply.timed_out is True
    assert calls_log == []                     #: инструмент не вызывался
    #: Последнее сообщение истории — ответ оператору, а не «вызовы без ответов».
    assert memory.messages[-1].role == "assistant"
    assert memory.messages[-1].content == reply.text
    assert all(not message.tool_calls for message in memory.messages)


def test_timeout_gives_a_clean_history_for_the_next_question(monkeypatch):
    """
    После таймаута история пригодна для следующего вопроса.

    Долгий инструмент съел весь срок, но шаги закрыты: следующий вопрос
    уходит в модель и получает обычный ответ (а не «вызовы без ответов»,
    которые GigaChat отвергает).
    """
    clock = FakeClock(monkeypatch)

    def function(**arguments):
        clock.advance(200)
        return {"ok": True, "data": [1]}

    registry = ToolRegistry([
        Tool(name="look_at_form", description="", summary="Поля формы",
             parameters={}, function=function),
    ])
    memory = AgentMemory(session_id="timeout_next", repository=NullRepository())
    client = ScriptedClient([("tool", "look_at_form", {"field": "inn"}),
                             ("text", "Готово")])
    agent = Agent(client=client, context=AgentContext(), memory=memory,
                  registry=registry, max_request_seconds=120.0)

    first = agent.send("Внеси данные")
    second = agent.send("Спасибо")

    assert first.timed_out is True
    assert second.timed_out is False
    assert second.steps >= 1            #: новый запрос дошёл до модели
    assert second.text == "Готово"

    wire = client.seen_messages[-1]
    #: Все ответы инструментов в запросе — со своим вызовом.
    for index, message in enumerate(wire):
        if message.get("role") != "tool":
            continue
        assert message.get("tool_call_id"), f"ответ инструмента без id: {index}"
        assert wire[index - 1].get("role") in ("assistant", "tool")
    #: Незакрытых блоков в истории нет.
    assert all(not message.tool_calls or message.role == "assistant"
               for message in memory.messages)


def test_zero_timeout_means_no_limit():
    """0 — таймаут выключен: так агент ведёт себя в тестах и разборах."""
    script = [("tool", "look_at_form", {"field": "inn"})] * 3
    agent = build_agent(script, answer={"ok": True, "data": []},
                        max_request_seconds=0.0)
    reply = agent.send("Найди организацию")
    assert reply.timed_out is False


def test_broken_timeout_setting_does_not_break_the_agent():
    """Мусор в настройке таймаута — это «без таймаута», а не исключение."""
    agent = build_agent([("text", "готово")], max_request_seconds="много")
    assert agent.max_request_seconds == 0.0
    assert agent.send("Привет").text == "готово"


def test_default_timeout_is_two_minutes():
    """Умолчание — 120 секунд (две минуты ожидания на запрос)."""
    assert DEFAULT_MAX_REQUEST_SECONDS == 120.0
    agent = build_agent([("text", "готово")])
    assert agent.max_request_seconds == DEFAULT_MAX_REQUEST_SECONDS


# ─────────────────────────────────────────────────────────────
# Предохранители вместе
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("reason", ["steps", "timeout", "loop"])
def test_every_safeguard_sets_its_own_flag(reason):
    """
    У каждой причины остановки — свой флаг в ответе.

    Три разных сценария: лимит шагов (данные приходят каждый раз, значит
    циклом это не считается), таймаут (срок вышел до первого шага) и
    зацикливание (тот же вызов без данных).
    """
    script = [("tool", "look_at_form", {"field": "inn"})] * 40
    if reason == "steps":
        agent = build_agent(script, answer={"ok": True, "data": [1]},
                            max_tool_steps=3)
    elif reason == "timeout":
        agent = build_agent(script, answer={"ok": True, "data": [1]},
                            max_request_seconds=1e-9)
    else:
        agent = build_agent(script, answer={"ok": True, "data": []})
    reply = agent.send("Внеси данные")

    assert reply.stop_reason == reason
    assert reply.step_limit_reached is True
    assert reply.timed_out is (reason == "timeout")
    assert reply.loop_detected is (reason == "loop")
    assert reply.text


def test_a_normal_reply_has_no_safeguard_flags():
    """Обычный ответ модели: ни одного флага остановки — предохранители спят."""
    agent = build_agent([("text", "Готово, ИНН заполнен")])
    reply = agent.send("Заполни ИНН")

    assert reply.text == "Готово, ИНН заполнен"
    assert reply.step_limit_reached is False
    assert reply.stop_reason == ""
    assert reply.loop_detected is False
    assert reply.timed_out is False
