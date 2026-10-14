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
from core.agent.memory import DEFAULT_MAX_HISTORY, AgentMemory
from core.agent.personas import DEFAULT_PERSONA
from core.agent.prompt import build_system_prompt
from core.agent.tools import AgentContext, Tool, ToolRegistry, build_registry

logger = logging.getLogger("core.agent.agent")

#: Лимит шагов вызова инструментов на один запрос (задание, часть G.5).
DEFAULT_MAX_TOOL_STEPS = 5


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
    def system_prompt(self) -> str:
        """Системный промпт: файл + образ + список инструментов."""
        return build_system_prompt(
            tools=self.registry.prompt_lines(),
            persona_key=self.persona_key,
            max_tool_steps=self.max_tool_steps,
        )

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

        calls: List[ToolCall] = []
        steps = 0
        limit_reached = False

        self.chat.begin_turn()
        try:
            while True:
                steps += 1
                prompt = self.system_prompt()
                message_data = self.chat.complete(
                    self.memory.wire_messages(prompt),
                    tools=self.registry.schemas(),
                )
                parsed = SafeChat.parse_tool_calls(message_data)
                content = str(message_data.get("content") or "")

                if not parsed:
                    answer = self.chat.restore(content)
                    self.memory.add("assistant", answer)
                    return AgentReply(text=answer, calls=calls, steps=steps)

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
                    call = self._execute(raw_call)
                    calls.append(call)
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
                        steps=steps,
                    )
        finally:
            self.chat.end_turn()

    # ---------------------------------------------------------
    # Выполнение инструмента
    # ---------------------------------------------------------
    def _execute(self, raw_call: Dict[str, Any]) -> ToolCall:
        """
        Выполняет один вызов инструмента (с подтверждением, если нужно).

        Подтверждение — единственное место, где агент спрашивает оператора.
        Отказ оператора НЕ ошибка: модель получает ``ok: False`` с текстом
        «отменено оператором» и объясняет это в ответе.
        """
        name = str(raw_call.get("name") or "")
        call = ToolCall(
            name=name,
            arguments=dict(raw_call.get("arguments") or {}),
            call_id=str(raw_call.get("id") or ""),
            tool=self.registry.get(name),
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

        self.context.notify(f"Выполняю: {call.tool.summary or name}")
        call.result = self.registry.call(name, call.arguments)
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
