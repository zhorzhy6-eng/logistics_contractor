#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Эмоциональная поддержка оператора (задание, часть 2: A, B, D).

Что проверяется
---------------
1. **Диагностика (A.1).** Фразы-триггеры распознаются: «устал», «заколебало»,
   «бесит», «не могу больше», «как ты меня достал», «я сдаюсь»,
   «сколько можно», мат.
2. **Спокойный оператор не получает блок поддержки** — правило работает
   только там, где нужно.
3. **Фикс промпта (B.1).** В промпте есть запреты (советы отдохнуть,
   нотации, лишние извинения) и требования (признать эмоцию, предложить
   конкретную помощь).
4. **Тон (B.2, B.3).** Смайлики — только если оператор их использует.
5. **Связка с агентом.** Блок уходит в системный промпт каждого шага
   запроса, а не только в первый.

Модель — подставная (``RecordingChat``): проверяется, ЧТО уходит в запрос,
а не качество ответа GigaChat. Живой прогон с настоящей моделью —
``tests/_tmp/_emotional_support/_live_check.py``.
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

from core.agent import emotional  # noqa: E402
from core.agent.agent import Agent  # noqa: E402
from core.agent.emotional import detect, emotional_block, support_block  # noqa: E402
from core.agent.tools import AgentContext  # noqa: E402

#: Десять фраз-триггеров из задания (C.1) вместе с ожидаемым состоянием.
TRIGGERS = (
    ("устал от этого договора", "tired"),
    ("заколебало всё", "tired"),
    ("бесит эта форма", "angry"),
    ("не могу больше", "tired"),
    ("как ты меня достал", "tired"),
    ("я сдаюсь", "tired"),
    ("сколько можно", "tired"),
    ("выгораю от этой работы", "tired"),
    ("ничего не получается", "despair"),
    ("задолбало всё это", "tired"),
)


class RecordingChat:
    """Подставная модель: запоминает системные промпты и отвечает текстом."""

    def __init__(self, answer: str = "Понял."):
        self.answer = answer
        self.prompts = []
        self.calls = 0

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.calls += 1
        for message in messages:
            if message.get("role") == "system":
                self.prompts.append(message.get("content") or "")
        return {"role": "assistant", "content": self.answer}


@pytest.fixture(autouse=True)
def clean_history():
    """
    Чистит историю диалога агента до и после каждого теста.

    История живёт в таблице `agent_history` (общая база), а сессия — одна на
    процесс: без уборки сообщения из этих тестов всплывали в чужой панели —
    `tests/test_agent_widget.py::test_messages_appear_in_history` видел
    10 пузырей вместо 2 (найдено общим прогоном).
    """
    from core.agent import memory as agent_memory
    from db.crud import agent_history

    def sweep() -> None:
        """Стирает историю текущей сессии и меняет ID сессии."""
        try:
            session = agent_memory.current_session_id()
            agent_history.clear_session(session)
            agent_history.purge_other_sessions(session)
        except Exception:  # noqa: BLE001 — уборка не должна ронять тест
            pass
        agent_memory.reset_session_id()

    sweep()
    yield
    sweep()


# ─────────────────────────────────────────────────────────────
# D.1. Триггеры распознаются
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase,state", TRIGGERS)
def test_trigger_phrases_are_detected(phrase, state):
    """Десять фраз-триггеров из задания распознаются."""
    result = detect(phrase)
    assert result.is_emotional, f"не распознано: {phrase}"
    assert state in result.states, f"{phrase}: ожидалось {state}, " \
                                   f"получено {result.states}"


def test_profanity_is_detected_as_state():
    """Мат — тоже состояние: человеку плохо, а не «запрос не распознан»."""
    result = detect("да что это за хуйня сегодня")
    assert emotional.STATE_PROFANITY in result.states
    assert "ругается" in result.titles()


def test_calm_message_is_not_emotional():
    """Спокойная рабочая просьба состоянием не считается."""
    for phrase in ("найди Ромашку", "заполни перевозчика", "Что такое ИНН?",
                   "Привет", "спасибо", "покажи договоры за октябрь"):
        assert not detect(phrase).is_emotional, phrase


def test_states_are_combined():
    """Усталость и раздражение могут быть вместе."""
    result = detect("устал, и всё бесит")
    assert "tired" in result.states
    assert "angry" in result.states


def test_trigger_found_inside_a_longer_phrase():
    """Фраза ловится внутри длинного сообщения."""
    result = detect("Слушай, я уже заколебал с этим договором, "
                    "давай что-нибудь сделаем")
    assert result.is_emotional
    assert "заколебал" in result.phrases


def test_yo_letter_is_normalized():
    """«ё» и «е» — одно и то же («всё бесит» / «все бесит»)."""
    assert detect("всё бесит").is_emotional
    assert detect("все бесит").is_emotional


def test_work_request_with_emotion():
    """«устал, найди Ромашку» — и эмоция, и поручение одновременно."""
    result = detect("устал, найди Ромашку")
    assert result.has_work is True
    assert result.needs_support is False


def test_emotion_without_work_needs_support():
    """Чистая эмоция — поддержка без работы."""
    result = detect("устал от этого договора")
    assert result.has_work is False
    assert result.needs_support is True


def test_state_to_dict():
    """Состояние передаётся словарём (для журнала и тестов)."""
    payload = detect("бесит").to_dict()
    json.dumps(payload, ensure_ascii=False)
    assert payload["states"] == ["angry"]
    assert payload["titles"] == ["раздражение"]


def test_log_has_no_message_text(caplog):
    """В журнал идут состояния, а не текст оператора (правило «логи без ПДн»)."""
    with caplog.at_level("INFO", logger="core.agent.emotional"):
        emotional_block("устал от договора с Ивановым")
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "Иванов" not in text
    assert "усталость" in text


# ─────────────────────────────────────────────────────────────
# D.2. Предложение помощи, а не нотация
# ─────────────────────────────────────────────────────────────

def test_support_block_forbids_advice():
    """Блок поддержки запрещает советы «отдохните» и нотации."""
    block = support_block(detect("устал"))
    lowered = block.casefold()
    assert "не давай советов отдохнуть" in lowered
    assert "не читай нотаций" in lowered
    assert "не извиняйся больше одного раза" in lowered


def test_support_block_offers_concrete_help():
    """При усталости блок просит предложить КОНКРЕТНУЮ помощь."""
    block = support_block(detect("устал от этого договора"))
    assert "взять на себя" in block.casefold()
    assert "конкретн" in block.casefold()


def test_angry_state_asks_what_is_wrong():
    """При раздражении блок просит выяснить, что именно не так."""
    block = support_block(detect("бесит эта форма"))
    assert "что именно не так" in block.casefold()


def test_despair_state_suggests_one_task():
    """При отчаянии блок предлагает разбить работу на один шаг."""
    block = support_block(detect("я не справлюсь"))
    assert "по одной задаче" in block.casefold()


def test_work_request_block_asks_to_do_the_job():
    """С поручением блок требует сделать работу, а не только посочувствовать."""
    block = support_block(detect("устал, найди Ромашку"))
    assert "поручени" in block.casefold()


def test_profanity_rule_in_block():
    """Про мат в блоке сказано: не повторять и не искать в базе."""
    block = support_block(detect("да заебало всё"))
    lowered = block.casefold()
    assert "не повторяй ругательства" in lowered
    assert "не ищи их в базе" in lowered


def test_calm_message_has_no_block():
    """Спокойному оператору блок не подкладывается."""
    assert support_block(detect("найди Ромашку")) == ""
    assert emotional_block("найди Ромашку") == ("", None)


# ─────────────────────────────────────────────────────────────
# D.3. Смайлики — только вслед за оператором
# ─────────────────────────────────────────────────────────────

def test_no_emoji_when_operator_has_none():
    """Оператор без смайликов — помощник тоже без смайликов."""
    state = detect("устал от этого договора")
    assert state.uses_emoji is False
    assert "Смайлики НЕ используй" in support_block(state)


def test_emoji_allowed_when_operator_uses_them():
    """Оператор пишет смайлики — помощнику они уместны."""
    state = detect("устал от этого договора 🙂")
    assert state.uses_emoji is True
    assert "Смайлики уместны" in support_block(state)


def test_no_emoji_in_our_own_text():
    """В самом блоке правил смайликов нет — иначе модель их повторит."""
    block = support_block(detect("устал"))
    assert not emotional.EMOJI_RE.search(block)


# ─────────────────────────────────────────────────────────────
# D.1. Связка с агентом
# ─────────────────────────────────────────────────────────────

def test_agent_sends_support_block_to_model():
    """Агент подкладывает блок поддержки в системный промпт."""
    chat = RecordingChat()
    agent = Agent(client=chat, context=AgentContext())
    agent.send("устал от этого договора")

    assert chat.prompts, "промпт не ушёл"
    assert "СОСТОЯНИЕ ОПЕРАТОРА" in chat.prompts[0]
    assert "усталость" in chat.prompts[0]


def test_agent_does_not_send_block_to_calm_operator():
    """Спокойному оператору блок не уходит."""
    chat = RecordingChat()
    agent = Agent(client=chat, context=AgentContext())
    agent.send("Что такое ИНН?")

    assert chat.prompts
    assert "СОСТОЯНИЕ ОПЕРАТОРА" not in chat.prompts[0]


def test_agent_keeps_emotional_state():
    """Состояние последнего сообщения доступно снаружи (панель, тесты)."""
    chat = RecordingChat()
    agent = Agent(client=chat, context=AgentContext())
    agent.send("бесит всё")
    assert agent.last_emotional_state is not None
    assert "angry" in agent.last_emotional_state.states


def test_agent_block_is_the_same_on_every_step():
    """Блок уходит в промпт КАЖДОГО шага, а не только первого."""
    chat = StepChat()
    agent = Agent(client=chat, context=AgentContext())
    agent.send("устал, найди Ромашку")

    assert chat.calls >= 2, "ожидался вызов инструмента и финальный ответ"
    assert all("СОСТОЯНИЕ ОПЕРАТОРА" in prompt for prompt in chat.prompts)


class StepChat:
    """Подставная модель: сначала вызов инструмента, потом ответ."""

    def __init__(self):
        self.calls = 0
        self.prompts = []

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.calls += 1
        for message in messages:
            if message.get("role") == "system":
                self.prompts.append(message.get("content") or "")
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "get_help",
                                 "arguments": json.dumps({"topic": ""})},
                }],
            }
        return {"role": "assistant", "content": "Готово."}


# ─────────────────────────────────────────────────────────────
# B.1. Промпт: правила эмоциональной поддержки
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def prompt():
    """Собранный системный промпт Максимилиана."""
    from core.agent.prompt import build_system_prompt

    return build_system_prompt()


def test_prompt_has_emotional_section(prompt):
    """В промпте есть раздел про эмоциональную поддержку."""
    assert "ЭМОЦИОНАЛЬНАЯ ПОДДЕРЖКА" in prompt


def test_prompt_forbids_rest_advice(prompt):
    """Промпт запрещает советы «отдохните» и «попейте чаю»."""
    lowered = prompt.casefold()
    assert "отдохните" in lowered
    assert "попейте чаю" in lowered
    assert "не читай нотаций" in lowered


def test_prompt_forbids_many_apologies(prompt):
    """Промпт запрещает извиняться по нескольку раз."""
    assert "не извиняйся по нескольку раз" in prompt.casefold()


def test_prompt_requires_acknowledgement_and_help(prompt):
    """Промпт требует признать эмоцию и предложить конкретную помощь."""
    lowered = prompt.casefold()
    assert "признай состояние" in lowered
    assert "конкретную помощь" in lowered


def test_prompt_says_no_emoji_unless_operator(prompt):
    """Промпт: смайлики — только если оператор их пишет (B.3)."""
    assert "не используй смайлики, если оператор их не пишет" in \
        prompt.casefold()


def test_prompt_has_examples_from_task(prompt):
    """В промпте есть примеры из задания (B.1)."""
    for phrase in ("устал от этого договора", "заколебало всё",
                   "не могу больше", "ты меня достал"):
        assert phrase in prompt, phrase


def test_support_block_is_added_to_prompt_memory():
    """Блок поддержки доходит до промпта и не ломает подстановки."""
    from core.agent.prompt import build_system_prompt

    block = support_block(detect("устал"))
    prompt = build_system_prompt(memory=block)
    assert "СОСТОЯНИЕ ОПЕРАТОРА" in prompt
    assert "{persona}" not in prompt
    assert "{tools}" not in prompt
