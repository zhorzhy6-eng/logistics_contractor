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

Зависимости: GigaChat приходит снаружи (клиент или подставной объект с
методом ``complete_chat``), Qt не нужен вовсе — цикл проверяется тестами
с мок-моделью.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.agent.llm import SafeChat
from core.agent.long_memory import (
    extract, memory_block, recall, remember_facts, remember_search_result,
    search_terms,
)
from core.agent.memory import DEFAULT_MAX_HISTORY, AgentMemory
from core.agent.personas import DEFAULT_PERSONA
from core.agent.prompt import build_system_prompt
from core.agent.tools import AgentContext, Tool, ToolRegistry, build_registry

logger = logging.getLogger("core.agent.agent")

#: Лимит шагов вызова инструментов на один запрос (задание, часть G.5).
DEFAULT_MAX_TOOL_STEPS = 5

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
    #: Упёрлись в лимит шагов?
    step_limit_reached: bool = False
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
    ):
        self.context = context if context is not None else AgentContext()
        self.persona_key = persona_key
        self.max_tool_steps = max(1, int(max_tool_steps))
        self.memory = memory if memory is not None else AgentMemory(
            max_history=max_history
        )
        self.registry = registry if registry is not None else build_registry(self.context)
        self.chat = SafeChat(client) if client is not None else None

    # ---------------------------------------------------------
    # Промпт
    # ---------------------------------------------------------
    def system_prompt(self, query: str = "") -> str:
        """
        Системный промпт: файл + образ + список инструментов + память.

        :param query: сообщение оператора: по нему отбирается TOP-5
            релевантных записей памяти (задание, часть 5). Без запроса
            память в промпт не подкладывается — иначе в контекст попадут
            случайные факты.

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
        return build_system_prompt(
            tools=self.registry.prompt_lines(),
            persona_key=self.persona_key,
            max_tool_steps=self.max_tool_steps,
            memory=block,
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

        # Что помощник помнит об этом операторе (задание, часть 5): TOP-5
        # релевантных записей. Отбор идёт ДО запроса к модели и попадает в
        # системный промпт, а не в историю диалога.
        recalled = self.memory_facts(text)
        remembered = self._remember_from(text)

        calls: List[ToolCall] = []
        steps = 0
        limit_reached = False

        self.chat.begin_turn()
        try:
            while True:
                steps += 1
                prompt = self.system_prompt(text)
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

                for raw_call in parsed:
                    call = self._execute(raw_call, text)
                    calls.append(call)
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

                if len(calls) >= self.max_tool_steps:
                    limit_reached = True
                    logger.info(
                        "Агент: достигнут лимит шагов инструментов (%s)",
                        self.max_tool_steps,
                    )
                    answer = self._final_text_after_limit(calls)
                    self.memory.add("assistant", answer)
                    return AgentReply(
                        text=answer, calls=calls, step_limit_reached=True,
                        steps=steps, remembered=remembered, recalled=recalled,
                    )
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
    def _final_text_after_limit(self, calls: Sequence[ToolCall]) -> str:
        """
        Ответ, когда инструментов вызвано больше лимита.

        Данные уже собраны — оператору они полезнее, чем извинение модели.
        Текст формируется БЕЗ обращения к GigaChat: лишний запрос на
        последнем шаге не нужен.
        """
        done = [call for call in calls if call.result and call.result.get("ok")]
        failed = [call for call in calls if not call.result or not call.result.get("ok")]
        lines = [
            f"Дошёл до лимита в {self.max_tool_steps} шагов — "
            f"дальше звать инструменты не буду."
        ]
        if done:
            lines.append(f"Успешно выполнено: {len(done)}. Что нашёл:")
            for call in done[-3:]:
                data = call.result.get("data")
                lines.append(f"• {call.name}: {_brief(data)}")
        if failed:
            lines.append(
                "Не получилось: " + ", ".join(
                    f"{call.name}"
                    + (" (отменено вами)" if call.declined else "")
                    for call in failed
                )
            )
        lines.append("Уточните запрос — отвечу точнее.")
        return "\n".join(lines)

    def clear_history(self) -> int:
        """Очищает историю диалога (кнопка «Очистить историю»)."""
        return self.memory.clear()


def _brief(data: Any, limit: int = 200) -> str:
    """Короткое представление данных инструмента для служебного ответа."""
    if isinstance(data, list):
        if not data:
            return "ничего не найдено"
        return f"найдено {len(data)}"
    if isinstance(data, dict):
        return ", ".join(f"{key}={value}" for key, value in list(data.items())[:4])
    text = "" if data is None else str(data)
    return text if len(text) <= limit else text[:limit] + "…"


__all__ = [
    "DEFAULT_MAX_TOOL_STEPS",
    "Agent",
    "AgentReply",
    "ToolCall",
]
