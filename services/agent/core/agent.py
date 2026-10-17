#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Главный цикл Максимилиана (задание, часть A.2).

Один запрос оператора проходит так:

    сообщение → GigaChat (промпт + история + инструменты)
             → ответ текстом  → готово
             → вызов инструмента → (подтверждение?) → вызов → результат
             → снова в GigaChat … не больше max_tool_steps шагов

Границы версии 1 (задание, часть G.4): агент не удаляет данные, не создаёт
договоры, не меняет настройки и не выполняет код. Единственные действия,
которые он может сделать, — заполнить поля открытой формы, и каждое такое
действие проходит через подтверждение оператора.

Три предохранителя (запрос «лимит шагов 3 → 20»). Лимит шагов поднят до 20,
поэтому одного его мало: длинный цикл надо чем-то ограничивать, кроме
счётчика вызовов.

  1. ``max_tool_steps`` — сколько вызовов инструментов разрешено на запрос;
  2. ``max_request_seconds`` — общий таймаут запроса: пока идут шаги,
     оператор ждёт ответа, и «20 шагов по 30 секунд» — это десять минут
     молчания. Проверка идёт ПЕРЕД каждым обращением к модели и ПЕРЕД каждым
     инструментом;
  3. ``REPEAT_LIMIT`` — защита от зацикливания: если модель зовёт ОДИН И ТОТ
     ЖЕ инструмент с теми же аргументами и результат не меняется (нет
     данных), шаги прекращаются.

Таймаут проверяется по часам, а не прерывает сетевой вызов: один запрос к
GigaChat может идти до ``gigachat_timeout`` (150 с), и оборвать его изнутри
нечем. Поэтому гарантия такая: НОВЫЙ шаг после истечения срока не начинается.

Зависимости: GigaChat приходит снаружи (клиент или подставной объект с
методом ``complete_chat``), Qt не нужен вовсе — цикл проверяется тестами
с мок-моделью.
"""

import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from core.utils import text_truncate
from services.agent.core.llm import SafeChat
from services.agent.persona.emotional import emotional_block
from services.agent.core.long_memory import (
    extract, memory_block, recall, remember_facts, remember_search_result,
    search_terms,
)
from services.agent.core.memory import DEFAULT_MAX_HISTORY, AgentMemory
from services.agent.persona.personas import DEFAULT_PERSONA
from services.agent.core.prompt import build_system_prompt
from services.agent.tools.registry import AgentContext, Tool, ToolRegistry, build_registry

logger = logging.getLogger("core.agent.agent")

#: Лимит шагов вызова инструментов на один запрос (задание, часть G.5).
#: Поднят с 5 до 20: на сложных запросах («внеси данные по организации»)
#: пяти шагов не хватало, и оператор получал ответ «лимит исчерпан» вместо
#: работы. Ровно это число стоит в config/settings.json и в верхней границе
#: AGENT_LIMITS (core/settings_service.py).
DEFAULT_MAX_TOOL_STEPS = 20

#: Общий таймаут одного запроса оператора, секунды. Вторая страховка после
#: лимита шагов: 20 шагов с ответами модели — это минуты ожидания, и в
#: какой-то момент лучше честно остановиться и сказать об этом.
DEFAULT_MAX_REQUEST_SECONDS = 120.0

#: Сколько РАЗ ПОДРЯД можно позвать один и тот же инструмент с теми же
#: аргументами, не получив новых данных, прежде чем это признается
#: зацикливанием. Три, а не два: модель законно повторяет поиск (память
#: подсказывает другое слово, оператор уточнил запрос).
REPEAT_LIMIT = 3

#: Инструменты поиска и имя поля, в котором им передаётся запрос. Память
#: работает именно с ними: учиться на «сколько договоров» нечему, а на
#: «Заполни Ромашку» — есть чему.
SEARCH_TOOLS: Dict[str, str] = {
    "search_anything": "query",
    "search_driver": "query",
    "search_carrier": "query",
    "search_customer": "query",
    "search_vehicle": "query",
    "search_contract": "query",
    "search_company_dadata": "query",
}

#: Поисковые инструменты, которые умеют повторить запрос по другому слову
#: (см. Agent._retry_search_with_memory). DaData сюда не входит: там ищут
#: в ЕГРЮЛ, а память оператора — про его собственную базу.
RETRYABLE_SEARCH_TOOLS = (
    "search_anything", "search_driver", "search_carrier", "search_customer",
    "search_vehicle", "search_contract",
)


@dataclass
class ToolCall:
    """Один вызов инструмента, который запросила модель."""

    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    tool: Optional[Tool] = None
    #: Итог выполнения (заполняется по ходу цикла).
    result: Optional[Dict[str, Any]] = None
    #: Оператор отказался выполнять действие.
    declined: bool = False
    #: Слова оператора, которыми он назвал предмет поиска («Заполни
    #: Ромашку» → «Ромашку»). Нужны памяти: она учится на том, что сказал
    #: ЧЕЛОВЕК, а не на том, что модель передала в инструмент.
    spoken: List[str] = field(default_factory=list)


@dataclass
class AgentReply:
    """Что агент вернул на запрос оператора."""

    #: Текст для оператора — БЕЗ плейсхолдеров (оригиналы восстановлены).
    text: str
    #: Выполненные вызовы инструментов (для журнала и тестов).
    calls: List[ToolCall] = field(default_factory=list)
    #: Упёрлись в предохранитель (шаги, таймаут или повтор)?
    step_limit_reached: bool = False
    #: Почему остановились: ``steps`` / ``timeout`` / ``loop`` — или пусто.
    #: Нужно панели и тестам: формулировка ответа одна, а причины разные.
    stop_reason: str = ""
    #: Модель звала один и тот же инструмент с теми же аргументами.
    loop_detected: bool = False
    #: Запрос не уложился в общий таймаут.
    timed_out: bool = False
    #: Сколько шагов (запросов к модели) понадобилось.
    steps: int = 0
    #: Что помощник запомнил из этой фразы (задание, часть 5).
    remembered: List[Dict[str, Any]] = field(default_factory=list)
    #: Что помощник вспомнил перед ответом (TOP-5 записей памяти).
    recalled: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def tool_names(self) -> List[str]:
        """Имена вызванных инструментов по порядку."""
        return [call.name for call in self.calls]


class Agent:
    """
    Ассистент «Максимилиан»: диалог + вызов инструментов.

    :param client: клиент GigaChat (или подставной объект с ``complete_chat``);
    :param context: AgentContext — окно, тип договора, диалог подтверждения;
    :param memory: история диалога (по умолчанию создаётся своя);
    :param registry: реестр инструментов (по умолчанию — 15 из tools.py);
    :param persona_key: образ (см. personas.py);
    :param max_tool_steps: лимит вызовов инструментов на один запрос.
    :param max_request_seconds: общий таймаут одного запроса (секунды).
        0 или меньше — таймаут выключен (нужно тестам и разбору).
    """

    def __init__(
        self,
        client: Any = None,
        context: Optional[AgentContext] = None,
        memory: Optional[AgentMemory] = None,
        registry: Optional[ToolRegistry] = None,
        persona_key: str = DEFAULT_PERSONA,
        max_tool_steps: int = DEFAULT_MAX_TOOL_STEPS,
        max_history: int = DEFAULT_MAX_HISTORY,
        max_request_seconds: float = DEFAULT_MAX_REQUEST_SECONDS,
    ):
        self.context = context if context is not None else AgentContext()
        self.persona_key = persona_key
        self.max_tool_steps = max(1, int(max_tool_steps))
        #: Ноль и меньше — «без таймаута»: так удобнее тестам, которым важна
        #: логика шагов, а не часы.
        self.max_request_seconds = _as_seconds(max_request_seconds)
        self.memory = memory if memory is not None else AgentMemory(
            max_history=max_history
        )
        self.registry = registry if registry is not None else build_registry(self.context)
        self.chat = SafeChat(client) if client is not None else None
        #: Состояние оператора из последнего сообщения (задание, часть 2):
        #: нужно панели и тестам, чтобы видеть, распознана ли усталость.
        self.last_emotional_state: Optional[Any] = None

    # ---------------------------------------------------------
    # Промпт
    # ---------------------------------------------------------
    def system_prompt(self, query: str = "", extra: str = "") -> str:
        """
        Системный промпт: файл + образ + список инструментов + память.

        :param query: сообщение оператора: по нему отбирается TOP-5
            релевантных записей памяти (задание, часть 5). Без запроса
            память в промпт не подкладывается — иначе в контекст попадут
            случайные факты.
        :param extra: дополнительный блок от вызывающего кода. Так в промпт
            попадают правила эмоциональной поддержки (задание, часть 2):
            блок зависит от СОСТОЯНИЯ оператора, а не от запроса, и решается
            в ``send`` — один раз на сообщение.

        Блок памяти МАСКИРУЕТСЯ перед отправкой (та же маска, что и у
        сообщений: см. core/agent/llm.py::SafeChat). В памяти могут лежать
        ФИО («Иванов» → «Иванов Иван Иванович»), а промпт уходит во
        внешний сервис; без маски память стала бы обходным путём для ПДн.
        """
        block = ""
        if query:
            try:
                block = memory_block(recall(query))
            except Exception as e:  # noqa: BLE001 — память не должна ронять ответ
                logger.warning("Память недоступна: %s", e)
                block = ""
            if block and self.chat is not None:
                try:
                    block = self.chat.anonymize(block)
                except Exception as e:  # noqa: BLE001 — маска не должна ронять
                    logger.warning("Память: маскирование не удалось (%s)", e)
                    block = ""
        #: Порядок блоков: сначала состояние оператора (эмоциональная
        #: поддержка), потом память — она меняется от запроса к запросу.
        parts = [str(extra or "").strip(), block.strip()]
        extra_block = "\n\n".join(part for part in parts if part)
        return build_system_prompt(
            tools=self.registry.prompt_lines(),
            persona_key=self.persona_key,
            max_tool_steps=self.max_tool_steps,
            memory=extra_block,
        )

    def memory_facts(self, query: str) -> List[Dict[str, Any]]:
        """
        Что помощник вспомнил на этот запрос (для панели и тестов).

        Отдельный метод, потому что панель показывает оператору, на что
        опирался ответ, а тесты проверяют отбор без запроса к модели.
        """
        try:
            return recall(query)
        except Exception as e:  # noqa: BLE001 — память не должна ронять ответ
            logger.warning("Память недоступна: %s", e)
            return []

    # ---------------------------------------------------------
    # Ответ на сообщение оператора
    # ---------------------------------------------------------
    def send(self, message: str) -> AgentReply:
        """
        Обрабатывает сообщение оператора и возвращает ответ агента.

        Исключения сети не глушатся: панель показывает оператору понятный
        текст ошибки GigaChat (тот же, что и при распознавании).
        """
        text = "" if message is None else str(message).strip()
        if not text:
            # Формулировка совпадает с подсказкой панели (AgentPanel.
            # EMPTY_INPUT_HINT): оператор видит один и тот же текст, откуда
            # бы ни пришёл пустой запрос — из панели или из скрипта.
            return AgentReply(text="Введите запрос.")
        if self.chat is None:
            return AgentReply(
                text="Максимилиан не подключён к GigaChat: "
                     "проверьте ключ и настройки распознавания."
            )

        self.memory.add("user", text)

        # Состояние оператора (задание, часть 2): устал, злится, выгорает.
        # Блок правил подкладывается в промпт КАЖДОГО шага этого запроса —
        # модель видит его и на вызове инструмента, и на финальном ответе.
        state_block, emotional_state = emotional_block(text)
        if emotional_state is not None:
            self.last_emotional_state = emotional_state

        # Что помощник помнит об этом операторе (задание, часть 5): TOP-5
        # релевантных записей. Отбор идёт ДО запроса к модели и попадает в
        # системный промпт, а не в историю диалога.
        recalled = self.memory_facts(text)
        remembered = self._remember_from(text)

        calls: List[ToolCall] = []
        steps = 0
        #: Момент, позже которого новый шаг не начинается. Считается от
        #: НАЧАЛА запроса: оператору важно общее время ожидания.
        deadline = (time.monotonic() + self.max_request_seconds
                    if self.max_request_seconds > 0 else None)
        #: Отпечаток ПОСЛЕДНЕГО ВЫПОЛНЕННОГО вызова и сколько раз он
        #: повторился подряд, не принеся новых данных (см. _tracks_repeat).
        previous = ""
        repeats = 0

        self.chat.begin_turn()
        try:
            while True:
                if _expired(deadline):
                    return self._stop("timeout", calls, steps, remembered,
                                      recalled)
                steps += 1
                prompt = self.system_prompt(text, extra=state_block)
                message_data = self.chat.complete(
                    self.memory.wire_messages(prompt),
                    tools=self.registry.schemas(),
                )
                parsed = SafeChat.parse_tool_calls(message_data)
                content = str(message_data.get("content") or "")

                if not parsed:
                    answer = self.chat.restore(content)
                    self.memory.add("assistant", answer)
                    return AgentReply(text=answer, calls=calls, steps=steps,
                                      remembered=remembered, recalled=recalled)

                # Модель просит инструменты: её ответ обязан попасть в
                # историю ДО результатов, иначе GigaChat отвергнет запрос.
                # Текст ответа восстанавливается здесь же: плейсхолдеры в
                # истории диалога оператору ни к чему, а маску перед
                # отправкой ставит SafeChat (и повторная маска безвредна).
                self.memory.add(
                    "assistant",
                    self.chat.restore(content),
                    tool_calls=[
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                # Аргументы — СТРОКОЙ (OpenAI-совместимый вид,
                                # как их хранит память и читают тесты).
                                # В проводной формат GigaChat их переводит
                                # SafeChat: там arguments становится объектом.
                                "arguments": call["raw"] or json.dumps(
                                    call["arguments"], ensure_ascii=False
                                ),
                            },
                        }
                        for call in parsed
                    ],
                )

                #: Проверка времени ДО работы инструментов. Заодно убирается
                #: недоигранный блок: ответ модели с вызовами уже в истории,
                #: а результатов не будет — обращение к модели с таким
                #: хвостом не имеет смысла.
                if _expired(deadline):
                    self.memory.rewind(1)
                    logger.info("Агент: таймаут запроса до вызова инструментов")
                    return self._stop("timeout", calls, steps, remembered,
                                      recalled)

                for raw_call in parsed:
                    #: Зацикливание проверяется ДО вызова: если предыдущий
                    #: ТАКОЙ ЖЕ вызов не принёс данных, а повторяется он уже
                    #: REPEAT_LIMIT-й раз, инструмент звать незачем —
                    #: иначе он вызовется на один раз больше лимита.
                    if _same_unproductive_call(raw_call, calls, previous,
                                               repeats):
                        return self._stop("loop", calls, steps, remembered,
                                          recalled, previous)

                    call = self._execute(raw_call, text)
                    calls.append(call)
                    signature = _call_signature(call)
                    #: Память учится на поиске (часть 5, B.1–B.2): «что
                    #: оператор сказал» → «что нашлось». Запись идёт ДО
                    #: ответа модели, чтобы повторный запрос в этом же
                    #: диалоге уже опирался на алиас.
                    self._learn_from_search(call, text)
                    payload = json.dumps(
                        call.result if call.result is not None
                        else {"ok": False, "error": "не выполнено"},
                        ensure_ascii=False,
                        default=str,
                    )
                    self.memory.add(
                        "tool",
                        payload,
                        tool_name=call.name,
                        tool_call_id=call.call_id,
                    )
                    repeats = (repeats + 1
                               if _tracks_repeat(call, signature, previous)
                               else 0)
                    previous = signature
                    if repeats >= REPEAT_LIMIT:
                        return self._stop("loop", calls, steps, remembered,
                                          recalled, signature)

                if len(calls) >= self.max_tool_steps:
                    return self._stop("steps", calls, steps, remembered,
                                      recalled)
        finally:
            self.chat.end_turn()

    def _remember_from(self, message: str) -> List[Dict[str, Any]]:
        """
        Запоминает то, о чём оператор прямо попросил (задание, часть 5).

        Записываются только явные просьбы («запомни…», «всегда…») и
        исправления — угадывать за оператора нельзя. Значения с паспортом,
        телефоном или длинным числом не запоминаются (см.
        core/agent/long_memory.py::look_sensitive).
        """
        try:
            facts = extract(message)
            if not facts:
                return []
            written = remember_facts(facts)
            if written:
                logger.info("Память: записано фактов %s", len(written))
            return facts if written else []
        except Exception as e:  # noqa: BLE001 — память не должна ронять ответ
            logger.warning("Память: запись не удалась (%s)", e)
            return []

    # ---------------------------------------------------------
    # Память на поиске (задание, часть 5)
    # ---------------------------------------------------------
    def _learn_from_search(self, call: ToolCall, message: str) -> None:
        """
        Учит память на результате поиска: «Ромашку» → «ООО «Ромашка»».

        Зачем. Оператор называет записи коротко («Ромашка», «Фасстранс»), а
        в базе они лежат полным именем. Пока помощник этого не знает, он
        каждый раз ищет заново и на неудаче отвечает «не нашёл» — ровно то,
        что было (задание, часть 2, симптом).

        Ключом алиаса берётся НАЗВАНИЕ, которое оператор произнёс в этом
        сообщении, а не то, что ушло в инструмент: «Заполни Ромашку» →
        ключ «Ромашку». Если название выделить не удалось, ключом становится
        сам запрос инструмента (когда он отличается от служебной фразы).

        :param message: сообщение оператора (нужно, когда модель позвала
            поиск с пустым или чужим аргументом: слово оператора всё равно
            известно).
        """
        if call.name not in SEARCH_TOOLS or call.declined:
            return
        result = call.result or {}
        if not result.get("ok"):
            return
        data = result.get("data")
        if not data:
            return

        argument = SEARCH_TOOLS[call.name]
        asked = str(call.arguments.get(argument) or "").strip()
        if not asked:
            #: Модель могла не заполнить поле поиска. Искать всё равно есть
            #: чем: инструмент вернул, по какому запросу нашёл («Ромашку»),
            #: а если и этого нет — берём слово оператора.
            asked = str(result.get("query") or "").strip()
            if not asked and call.spoken:
                asked = call.spoken[0]
        if not asked:
            return

        term = self._spoken_name(call, asked)
        if not term:
            return
        try:
            record_id = remember_search_result(term, data, source="поиск")
        except Exception as e:  # noqa: BLE001 — память не должна ронять ответ
            logger.warning("Память: алиас не записан (%s)", e)
            return
        if record_id is not None:
            logger.info("Память: алиас записан из поиска (id=%s)", record_id)

    def _spoken_names(self, message: str) -> List[str]:
        """
        Названия, которые оператор произнёс сам: «Заполни Ромашку» →
        «Ромашку». Из этого списка память берёт ключ алиаса.
        """
        from core.agent.words import search_terms

        if not message:
            return []
        try:
            return [word for word in search_terms(message) if len(word) >= 2]
        except Exception as e:  # noqa: BLE001 — разбор не должен ломать вызов
            logger.debug("Память: слова запроса не разобраны (%s)", e)
            return []

    def _spoken_name(self, call: ToolCall, asked: str) -> str:
        """
        Название, которое оператор произнёс сам (для ключа алиаса).

        Сначала пробуются слова из СООБЩЕНИЯ — «Заполни Ромашку» даёт
        «Ромашку». Если в сообщении названия нет (оператор сказал «заполни
        его»), берётся запрос инструмента — но только если он не состоит из
        служебных слов.
        """
        for candidate in call.spoken:
            #: Слово-роль («перевозчика», «водителя») ключом быть не может:
            #: «перевозчика → ООО «Ромашка»» — неверный алиас.
            if candidate.casefold() != asked.casefold():
                return candidate
        return asked

    def _retry_search_with_memory(self, call: ToolCall) -> None:
        """
        Повторяет неудачный поиск по памяти (часть 5, B.2).

        Прямой запрос ничего не дал — значит, оператор назвал запись так,
        как её нет в базе. Память подсказывает, чем ещё поискать («Ромашка»
        → «ООО «Ромашка»»), и инструмент вызывается ещё раз. Результат
        складывается в тот же ответ: модель видит и первую попытку, и
        удачную, и отвечает по существу, а не «не нашёл».

        Ищутся варианты и по словам ОПЕРАТОРА, и по тому, что ушло в
        инструмент: модель могла передать фразу целиком, и тогда память
        знает ответ только на имя из сообщения.

        Ошибка повтора не портит исходный ответ: оператор всё равно должен
        получить то, что уже нашлось.
        """
        if call.name not in RETRYABLE_SEARCH_TOOLS or call.declined:
            return
        result = call.result or {}
        if not result.get("ok") or result.get("data"):
            return

        argument = SEARCH_TOOLS.get(call.name)
        asked = str(call.arguments.get(argument) or "").strip()
        if not argument or not asked:
            #: Модель не заполнила поле поиска, но оператор название назвал:
            #: терять его нельзя — ищем по слову оператора.
            asked = call.spoken[0] if call.spoken else ""
        if not argument or not asked:
            return

        seen: List[str] = []
        for source in (call.spoken or []) + [asked]:
            if source and source.casefold() not in [item.casefold()
                                                    for item in seen]:
                seen.append(source)
        try:
            alternatives: List[str] = []
            for source in seen:
                alternatives.extend(search_terms(source, limit=3))
        except Exception as e:  # noqa: BLE001 — память не должна ронять поиск
            logger.warning("Память: подсказки не прочитаны (%s)", e)
            return

        attempts: List[Dict[str, Any]] = []
        tried: set = set()
        for term in alternatives:
            if term.casefold() == asked.casefold() or term.casefold() in tried:
                continue
            tried.add(term.casefold())
            arguments = dict(call.arguments)
            arguments[argument] = term
            try:
                again = self.registry.call(call.name, arguments)
            except Exception as e:  # noqa: BLE001 — один вариант не должен ломать
                logger.warning("Память: повторный поиск не удался (%s)", e)
                continue
            attempts.append({"query": term, "found": bool(again.get("data"))})
            if again.get("data"):
                result["data"] = again.get("data")
                result["count"] = again.get("count", 0)
                result["query"] = term
                result["from_memory"] = True
                result["memory_note"] = (
                    f"прямой запрос «{asked}» ничего не дал; по памяти "
                    f"найдено по «{term}»"
                )
                logger.info("Память: поиск повторён удачно (запросов=%s)",
                            len(attempts))
                return
        if attempts:
            result["memory_tried"] = attempts
            logger.info("Память: повторный поиск без результата (вариантов=%s)",
                        len(attempts))

    # ---------------------------------------------------------
    # Выполнение инструмента
    # ---------------------------------------------------------
    def _execute(self, raw_call: Dict[str, Any],
                 message: str = "") -> ToolCall:
        """
        Выполняет один вызов инструмента (с подтверждением, если нужно).

        Подтверждение — единственное место, где агент спрашивает оператора.
        Отказ оператора НЕ ошибка: модель получает ``ok: False`` с текстом
        «отменено оператором» и объясняет это в ответе.

        :param message: сообщение оператора: из него берутся слова, которыми
            он назвал предмет поиска (для памяти).
        """
        name = str(raw_call.get("name") or "")
        call = ToolCall(
            name=name,
            arguments=dict(raw_call.get("arguments") or {}),
            call_id=str(raw_call.get("id") or ""),
            tool=self.registry.get(name),
            spoken=self._spoken_names(message),
        )

        if raw_call.get("error"):
            call.result = {"ok": False, "error": str(raw_call["error"])}
            return call

        if call.tool is None:
            call.result = {
                "ok": False,
                "error": f"инструмента «{name}» не существует",
                "available": list(self.registry.names()),
            }
            return call

        # ── Снятие маски с аргументов ──
        # Модель видела результат прошлого инструмента в маске (`<<PERSON_1>>`
        # вместо ФИО) и возвращает её же в аргументах. Инструменты работают с
        # НАСТОЯЩИМИ данными: иначе в форму попадал бы плейсхолдер.
        call.arguments = self._restore_arguments(call.arguments)

        if call.tool.requires_confirmation:
            self.context.notify(f"Нужно подтверждение: {call.tool.summary or name}")
            if not self._ask_confirmation(call.tool, call.arguments):
                call.declined = True
                call.result = {
                    "ok": False,
                    "error": "оператор отменил действие",
                    "declined": True,
                }
                logger.info("Агент: оператор отменил %s", name)
                return call
            #: Оператор мог ПОПРАВИТЬ значения в диалоге (превью разбора
            #: текста, задание часть 1, пункт B.2): правки вносятся в
            #: аргументы до вызова инструмента, а не после.
            self._apply_edits(call.tool, call.arguments)

        self.context.notify(f"Выполняю: {call.tool.summary or name}")
        call.result = self.registry.call(name, call.arguments)
        #: Поиск ничего не нашёл — пробуем по памяти (часть 5, B.2):
        #: оператор мог назвать запись сокращённо, а в базе она лежит
        #: полным именем.
        self._retry_search_with_memory(call)
        logger.info(
            "Агент: инструмент %s → %s",
            name, "ок" if call.result.get("ok") else call.result.get("error"),
        )
        return call

    def _restore_arguments(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Возвращает оригиналы в аргументах вызова (снимает маску).

        Работает рекурсивно по строкам словаря и списка: значения бывают
        вложенными (набор полей для формы, блоки тягача и полуприцепа).
        """
        if self.chat is None or not self.chat.masked_count:
            return arguments

        def restore(value: Any) -> Any:
            if isinstance(value, str):
                return self.chat.restore(value)
            if isinstance(value, dict):
                return {key: restore(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [restore(item) for item in value]
            return value

        return restore(arguments)

    def _ask_confirmation(self, tool: Tool, arguments: Dict[str, Any]) -> bool:
        """
        Спрашивает оператора. Нет диалога — действие запрещено.

        Так безопаснее по умолчанию: если панель не подключила подтверждение
        (например, агент вызван из скрипта), заполнение формы не произойдёт.
        """
        confirm = self.context.confirm
        if confirm is None:
            logger.warning(
                "Подтверждение недоступно — опасное действие %s отменено",
                tool.name,
            )
            return False
        try:
            return bool(confirm(tool, arguments))
        except Exception:  # noqa: BLE001 — диалог не должен ронять агента
            logger.exception("Диалог подтверждения упал — действие отменено")
            return False

    def _apply_edits(self, tool: Tool, arguments: Dict[str, Any]) -> None:
        """
        Вносит правки оператора из диалога подтверждения (часть 1, B.2).

        Диалог превью разбора текста даёт исправить значения прямо в
        таблице: он складывает их в свои аргументы, а здесь они переносятся
        в вызов. Ошибка правки не должна отменять уже подтверждённое
        действие — в этом случае в форму уйдут исходные значения.
        """
        if tool.on_edit is None:
            return
        try:
            tool.on_edit(arguments)
        except Exception as e:  # noqa: BLE001 — правка не должна ронять агента
            logger.warning("Правки оператора не применены (%s): %s",
                           tool.name, type(e).__name__)

    # ---------------------------------------------------------
    # Маскирование
    # ---------------------------------------------------------
    def restore_for_user(self, text: str) -> str:
        """
        Снимает маску с текста, если он пришёл из модели.

        История диалога хранит настоящие значения (это данные оператора
        в его же базе), а наружу уходит только маска — её ставит SafeChat
        при каждом запросе. Метод нужен панели для ответов, в которых
        модель всё-таки вернула плейсхолдер.
        """
        if self.chat is None:
            return text
        return self.chat.restore(text)

    # ---------------------------------------------------------
    # Служебное
    # ---------------------------------------------------------
    def _stop(
        self,
        reason: str,
        calls: Sequence[ToolCall],
        steps: int,
        remembered: Sequence[Dict[str, Any]],
        recalled: Sequence[Dict[str, Any]],
        signature: str = "",
    ) -> AgentReply:
        """
        Останавливает запрос и возвращает ОПЕРАТОРУ человеческий ответ.

        Одна дверь для всех трёх предохранителей: какой бы из них ни
        сработал, оператор видит один и тот же понятный текст с выбором
        «что делать первым», а не машинный отчёт. Разница только в первой
        строке — она объясняет причину (``reason``).

        Ответ формируется БЕЗ обращения к GigaChat: шаг, который упёрся в
        лимит, — последний, и тратить его на «объясни оператору» нечего.
        Текст кладётся в историю: следующий запрос продолжает разговор с
        того, что оператору уже сказали.

        :param reason: ``steps`` (лимит шагов), ``timeout`` (общий таймаут),
            ``loop`` (зацикливание);
        :param steps: сколько ВЫЗОВОВ МОДЕЛИ реально сделано — по нему
            считается «успел N шагов»;
        :param signature: отпечаток зациклившегося вызова (для ``loop``).
        """
        answer = self._final_text_after_limit(calls, steps, reason, signature)
        self.memory.add("assistant", answer)
        logger.info(
            "Агент: остановлен (%s), шагов=%s, вызовов инструментов=%s",
            reason, steps, len(calls),
        )
        return AgentReply(
            text=answer,
            calls=list(calls),
            step_limit_reached=True,
            stop_reason=reason,
            loop_detected=reason == "loop",
            timed_out=reason == "timeout",
            steps=steps,
            remembered=list(remembered),
            recalled=list(recalled),
        )

    def _final_text_after_limit(
        self,
        calls: Sequence[ToolCall],
        spent: Optional[int] = None,
        reason: str = "steps",
        signature: str = "",
    ) -> str:
        """
        Ответ, когда запрос остановлен предохранителем.

        Оператору нужен выход из тупика, а не отчёт (стресс-тест 15.10.2026,
        запросы №22, №23, №31): раньше сюда попадали имена инструментов
        (`get_form_state`) и `repr` их данных — свалка вида
        ``carrier_tab={'filled': {...}}`` длиной больше двух тысяч символов.
        Теперь основа ответа — предложение разбить задачу на части, а из
        сделанного остаётся одна строка с количеством (см. ``_done_line``).

        Технических идентификаторов в ответе нет: ни имени окна, ни типа
        договора, ни списка вкладок. Только слова оператора.

        :param spent: сколько вызовов модели реально сделано. В тексте ответа
            число не повторяется (лимит уже назван), но параметр остаётся:
            по нему считается причина, и его читает ``_reason_line``;
        :param reason: причина остановки, см. ``_stop``;
        :param signature: отпечаток зациклившегося вызова.
        """
        if spent is None:
            spent = self.max_tool_steps
        seconds = (self.max_request_seconds
                   or DEFAULT_MAX_REQUEST_SECONDS)
        lines = [_reason_line(reason, spent, self.max_tool_steps, signature,
                              seconds)]
        done = _done_line(calls)
        if done:
            lines.append(done)
        failed = _failed_line(calls)
        if failed:
            lines.append(failed)
        lines.extend(HELP_CHOICES)
        return _cap("\n".join(lines), ANSWER_LIMIT_AFTER_LIMIT)

    def clear_history(self) -> int:
        """Очищает историю диалога (кнопка «Очистить историю»)."""
        return self.memory.clear()


#: Предел длины служебного ответа: оператору нужен итог, а не отчёт.
ANSWER_LIMIT_AFTER_LIMIT = 700

#: Что можно сделать первым — список из задания. Оператор выбирает шаг, а не
#: читает отчёт: «задача большая, давай по частям».
HELP_CHOICES = (
    "Задача большая — не успел за один запрос. Давай по частям.",
    "Что сделать первым:",
    "• Найти организацию в базе?",
    "• Заполнить форму?",
    "• Проверить в ЕГРЮЛ?",
)


def _reason_line(reason: str, spent: int, limit: int, signature: str,
                 seconds: float = DEFAULT_MAX_REQUEST_SECONDS) -> str:
    """
    Первая строка ответа: почему остановились — словами для оператора.

    Ни одного машинного имени: причина называется по-человечески, а не
    «stop_reason=loop». Вызов инструмента упоминается только тогда, когда
    без этого непонятно, что именно застряло. Число сделанных шагов в
    тексте не повторяется: лимит уже назван, а «сделано 20 из 20» ничего не
    добавляет.
    """
    if reason == "timeout":
        shown = max(1, int(math.ceil(float(seconds))))
        return (f"Не уложился в {shown} секунд — остановился, "
                f"чтобы не держать вас в ожидании.")
    if reason == "loop":
        name = _signature_tool(signature)
        if name:
            return (f"Я {REPEAT_LIMIT} раза подряд повторил одно и то же "
                    f"действие ({name}) и не продвинулся — остановился.")
        return (f"Я {REPEAT_LIMIT} раза подряд повторил одно и то же "
                f"действие и не продвинулся — остановился.")
    return (f"За один запрос я успеваю {limit} {_steps_word(limit)} — "
            f"лимит исчерпан.")


def _done_line(calls: Sequence[ToolCall]) -> str:
    """
    Одна строка о том, что успело получиться: количеством, без dump'ов.

    Список инструментов, имена окон и типы договоров сюда не попадают —
    оператору нужно знать «сколько нашлось», а не как это называется в коде.
    Одинаковые строки не повторяются: десять удачных поисков одного и того
    же — это один пункт, а не десять (иначе строка разрастается и вытесняет
    из ответа сам выбор дальнейших шагов).
    """
    done = [call for call in calls if call.result and call.result.get("ok")]
    if not done:
        return ""
    parts: List[str] = []
    seen: List[str] = []
    for call in done[-6:]:
        brief = _brief(call.result.get("data"))
        item = f"{_call_title(call)} — {brief}" if brief else _call_title(call)
        if item in seen:
            continue
        seen.append(item)
        parts.append(item)
    #: Показываем не больше трёх РАЗНЫХ результатов: ответ — не журнал.
    return "Успел: " + "; ".join(parts[-3:]) + "."


def _failed_line(calls: Sequence[ToolCall]) -> str:
    """
    Одна строка о том, что не получилось: названия действий, без причин.

    Внутренние тексты ошибок («нет доступа к таблице») оператору не
    пересказываются: это либо его отмена, либо сбой, о котором он узнает из
    логов и диагностики.
    """
    failed = [call for call in calls
              if not call.result or not call.result.get("ok")]
    if not failed:
        return ""
    items = [
        _call_title(call) + (" (отменено вами)" if call.declined else "")
        for call in failed
    ]
    return "Не получилось: " + ", ".join(items) + "."


def _as_seconds(value: Any) -> float:
    """
    Таймаут запроса из настройки: число секунд или 0.0 («без таймаута»).

    Мусор в настройке не должен ронять запрос: непонятное значение — это
    «без таймаута», а не исключение при создании агента.
    """
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0.0
    if seconds != seconds or seconds < 0:  # NaN и отрицательные
        return 0.0
    return seconds


def _expired(deadline: Optional[float]) -> bool:
    """Вышел ли срок запроса (``None`` — таймаут выключен)."""
    return deadline is not None and time.monotonic() >= deadline


def _call_signature(call: ToolCall) -> str:
    """
    Отпечаток вызова: имя инструмента + аргументы.

    Аргументы сортируются по ключам: ``{"a": 1, "b": 2}`` и ``{"b": 2,
    "a": 1}`` — один и тот же вызов, а модель может переставить ключи.
    """
    try:
        arguments = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True,
                               default=str)
    except (TypeError, ValueError):
        arguments = str(call.arguments)
    return f"{call.name}|{arguments}"


def _tracks_repeat(call: ToolCall, signature: str,
                   previous: str) -> bool:
    """
    Считать ли этот ВЫПОЛНЕННЫЙ вызов повтором предыдущего.

    Повтором считается только вызов с теми же аргументами, который к тому же
    не принёс новых данных: пустой результат или ошибка. Так под правило не
    попадают законные повторы — оператор уточнил запрос, память подсказала
    другое слово, поиск во второй раз нашёл запись.
    """
    if not previous or signature != previous:
        return False
    return _unproductive(call)


def _same_unproductive_call(raw_call: Dict[str, Any],
                            calls: Sequence[ToolCall], previous: str,
                            repeats: int) -> bool:
    """
    Пора ли остановиться, НЕ выполняя этот вызов.

    Условие ровно одно: следующий вызов — тот же самый, что и предыдущий,
    предыдущий не принёс данных, и этот повтор был бы уже
    ``REPEAT_LIMIT``-м по счёту. Тогда инструмент зовётся ровно
    ``REPEAT_LIMIT`` раз, а не на один больше.

    :param calls: уже выполненные вызовы (результат последнего и решает дело);
    :param previous: отпечаток последнего выполненного вызова;
    :param repeats: сколько раз подряд этот вызов повторился без данных.
    """
    if not calls or repeats + 1 < REPEAT_LIMIT:
        return False
    candidate = ToolCall(
        name=str(raw_call.get("name") or ""),
        arguments=dict(raw_call.get("arguments") or {}),
    )
    if _call_signature(candidate) != previous:
        return False
    return _unproductive(calls[-1])


def _unproductive(call: ToolCall) -> bool:
    """
    Вызов не принёс новых данных: ошибка, отмена или пустой результат.

    Продуктивный вызов (есть ``data``) повтором не считается никогда:
    оператор уточнил запрос, память подсказала другое слово — работа идёт.
    """
    if call.declined:
        return True
    result = call.result
    if result is None:
        #: Результата ещё нет — вызов только собирается выполняться.
        return True
    if not result.get("ok"):
        return True
    return not result.get("data")


def _signature_tool(signature: str) -> str:
    """Человеческое название инструмента из отпечатка (часть до «|»)."""
    name = str(signature or "").split("|", 1)[0].strip()
    return name.replace("_", " ")


def _steps_word(number: int) -> str:
    """Слово «шаг» в правильной форме: 1 шаг, 2 шага, 5 шагов."""
    value = abs(int(number)) % 100
    if 11 <= value <= 14:
        return "шагов"
    value %= 10
    if value == 1:
        return "шаг"
    if 2 <= value <= 4:
        return "шага"
    return "шагов"


def _call_title(call: ToolCall) -> str:
    """
    Человеческое название инструмента для служебного ответа.

    Берётся `Tool.summary` («Список открытых вкладок»), а не машинное имя
    (`get_open_tabs`): оператор не знает, что такое `get_form_state`.
    Первая буква опускается в нижний регистр — фраза идёт после «• ».
    """
    summary = ""
    if call.tool is not None:
        summary = str(call.tool.summary or "").strip()
    text = summary or str(call.name or "").replace("_", " ")
    if not text:
        return "инструмент"
    return text[0].lower() + text[1:]


def _brief(data: Any, limit: int = 160) -> str:
    """
    Короткое человеческое представление данных инструмента.

    Правила: список — количеством, словарь — счётчиками и короткими
    значениями, вложенные структуры — количеством, а не свалкой ключей.
    Строка обрезается: служебный ответ не место для полотна.

    Ключи с подчёркиваниями (``contract_type``, ``filled_count``) и поля
    интерфейса (``window``, ``tabs``) пропускаются: это имена из кода, и
    оператору они ничего не объясняют (задание: убрать технические ID).
    Так же пропускаются значения, похожие на код (``perevozka``): если
    инструмент вернул только их, вызывающий код строку не добавляет.
    """
    if isinstance(data, list):
        return f"найдено {len(data)}" if data else "ничего не найдено"
    if isinstance(data, dict):
        if not data:
            return "пусто"
        parts: List[str] = []
        for key, value in data.items():
            if _technical_key(key):
                continue
            if isinstance(value, dict):
                #: Так выглядит состояние формы: важно «сколько заполнено»,
                #: а не перечень всех полей со значениями.
                if "filled_count" in value:
                    parts.append(f"{key}: заполнено "
                                 f"{value.get('filled_count')}, "
                                 f"пусто {value.get('empty_count')}")
                continue
            if isinstance(value, (list, tuple)):
                parts.append(f"{key}: {len(value)}")
                continue
            text = _short_value(value)
            if text:
                parts.append(f"{key}={text}")
            if len(parts) >= 4:
                break
        return "; ".join(parts) or "пусто"
    text = "" if data is None else str(data)
    text = " ".join(text.split())
    if _technical_key(text):
        return ""
    return text_truncate.shorten(text, limit)


#: Поля, которыми инструменты описывают СЕБЯ, а не работу: имя окна, тип
#: договора, список вкладок. В ответе оператору они не нужны (задание).
_TECHNICAL_KEYS = frozenset({
    "window", "tabs", "tab", "contract_type", "type", "key", "contract_key",
})

#: Служебные имена полей, которые оператору ПОНЯТНЫ: «сколько нашлось»,
#: «по какому запросу». Они состоят из строчных букв, но техническими не
#: считаются — иначе из ответа пропало бы самое полезное.
_SERVICE_KEYS = frozenset({
    "count", "total", "found", "query", "name", "title", "result", "results",
    "status", "ok", "error", "note", "hint", "date", "sum", "amount", "items",
})


def _technical_key(key: Any) -> bool:
    """
    Похоже ли на технический идентификатор: ``window``, ``contract_type``,
    ``filled_count``, ``perevozka``.

    Признак: короткое слово из строчных латинских букв с подчёркиваниями
    (так пишут имена в коде) либо поле из списка ``_TECHNICAL_KEYS``.
    Человеческие данные («Ромашка», «г. Москва, Ленина 5», «поля формы»)
    под правило не попадают: там есть заглавные буквы, пробелы, цифры,
    точки и запятые. Служебные имена из ``_SERVICE_KEYS`` остаются.
    """
    text = " ".join(str(key or "").split())
    if not text or len(text) > 27:
        return False
    folded = text.casefold()
    if folded in _TECHNICAL_KEYS:
        return True
    if folded in _SERVICE_KEYS:
        return False
    return text.isascii() and text.islower() and text.replace("_", "").isalpha()


def _short_value(value: Any, limit: int = 120) -> str:
    """
    Короткое значение поля для служебного ответа.

    Режется по границе слова (``core.utils.text_truncate``): значение,
    оборванное на полуслове («Москва — Ту…»), оператор читает как сбой.
    Лимит 120 вместо 60: адрес, маршрут и наименование в 60 символов не
    влезают, а служебная строка — это ещё не полотно (их не больше
    четырёх, см. ``_brief``).
    """
    if value is None or isinstance(value, bool):
        return ""
    text = " ".join(str(value).split())
    if not text:
        return ""
    return text_truncate.shorten(text, limit)


def _cap(text: str, limit: int) -> str:
    """Ограничивает длину служебного ответа (обрезает по строкам)."""
    if len(text) <= limit:
        return text
    lines: List[str] = []
    size = 0
    for line in text.split("\n"):
        if size + len(line) + 1 > limit:
            break
        lines.append(line)
        size += len(line) + 1
    return "\n".join(lines)


__all__ = [
    "ANSWER_LIMIT_AFTER_LIMIT",
    "DEFAULT_MAX_REQUEST_SECONDS",
    "DEFAULT_MAX_TOOL_STEPS",
    "HELP_CHOICES",
    "REPEAT_LIMIT",
    "Agent",
    "AgentReply",
    "ToolCall",
]
