#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалоговый слой агента: GigaChat + маскирование ПДн (задание, часть G.3).

Здесь живёт ровно одна ответственность: превратить историю диалога в
безопасный запрос к GigaChat и вернуть ответ модели. Сама логика сети
(адрес, токен, повторы, таймауты) остаётся в core/gigachat_client.py —
здесь только обёртка над ``GigaChatClient.complete_chat``.

Маскирование устроено так:

  * перед КАЖДЫМ запросом тексты сообщений ролей ``user`` и ``tool``
    обезличиваются (core/pseudonymizer.py): наружу уходят плейсхолдеры
    ``<<PERSON_1>>``, ``<<PHONE_1>>`` и т.д.;
  * ответ модели приходит с плейсхолдерами и восстанавливается обратно;
  * маппинг живёт в памяти ТОЛЬКО на время одного запроса пользователя
    (``begin_turn`` … ``end_turn``): на диск и в логи он не попадает.

Границы: агент не умеет ничего, кроме этого запроса. Вызов инструментов,
лимиты и подтверждения — в core/agent/agent.py.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from core.pseudonymizer import Pseudonymizer

logger = logging.getLogger("core.agent.llm")

#: Роли, содержимое которых уходит во внешнюю модель как текст документа.
#: Системный промпт — это правила программы (в нём нет данных оператора),
#: assistant-сообщения агент формирует сам.
MASKED_ROLES = ("user", "tool")


class SafeChat:
    """
    Обёртка над клиентом GigaChat: маскирование до запроса, снятие после.

    Использование на один запрос пользователя::

        chat.begin_turn()
        try:
            message = chat.complete(messages, tools)
        finally:
            chat.end_turn()

    Обёртка не создаёт клиента и не хранит ключ: клиент приходит снаружи
    (его создаёт приложение по настройкам), а в тестах — подставной объект
    с тем же методом ``complete_chat``.
    """

    def __init__(self, client: Any):
        self.client = client
        self._pseudonymizer = Pseudonymizer()
        self._mapping: Dict[str, str] = {}

    # ---------------------------------------------------------
    # Маскирование
    # ---------------------------------------------------------
    def begin_turn(self) -> None:
        """Начинает новый запрос пользователя: маппинг пуст."""
        self._mapping = {}

    def end_turn(self) -> None:
        """
        Завершает запрос: маппинг забывается.

        Маппинг нельзя хранить между запросами: он раскрывает ПДн, а
        восстанавливать чужие токены в следующем ответе незачем — история
        диалога уже хранит замаскированный текст.
        """
        self._mapping = {}

    @property
    def masked_count(self) -> int:
        """Сколько сущностей замаскировано в текущем запросе (для журнала)."""
        return len(self._mapping)

    def describe_masking(self) -> str:
        """Безопасное описание маппинга: «PERSON=1, PHONE=2»."""
        return Pseudonymizer.describe(self._mapping)

    def anonymize(self, text: str) -> str:
        """Маскирует текст и запоминает маппинг до конца запроса."""
        safe_text, mapping = self._pseudonymizer.anonymize(text)
        if mapping:
            self._mapping.update(mapping)
        return safe_text

    def restore(self, text: str) -> str:
        """
        Возвращает оригиналы в текст модели.

        Токены, которых нет в текущем маппинге, вычищаются: остаток
        ``<<PERSON_9>>`` в ответе оператору выглядел бы как мусор.
        """
        if not text or not self._mapping:
            return "" if text is None else str(text)
        return self._pseudonymizer.restore(text, self._mapping)

    # ---------------------------------------------------------
    # Запрос
    # ---------------------------------------------------------
    def _safe_messages(self, messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Копия истории с замаскированным содержимым.

        Входные сообщения НЕ изменяются: история диалога в памяти остаётся
        замаскированной сама по себе (её собирает агент), а словари
        аргументов не должны мутировать на месте — их читает вызывающий код.
        """
        safe: List[Dict[str, Any]] = []
        for message in messages:
            item = dict(message)
            if item.get("role") in MASKED_ROLES:
                content = item.get("content")
                if isinstance(content, str) and content:
                    item["content"] = self.anonymize(content)
            safe.append(item)
        return safe

    def complete(
        self,
        messages: Sequence[Dict[str, Any]],
        tools: Optional[Sequence[Dict[str, Any]]] = None,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        """
        Один запрос к GigaChat: возвращает сообщение модели.

        :return: ``{"role": "assistant", "content": str, "tool_calls": list}``
        """
        safe_messages = self._safe_messages(messages)
        if self._mapping:
            logger.info(
                "Агент: в запрос уходят только плейсхолдеры (%s)",
                self.describe_masking(),
            )

        message = self.client.complete_chat(
            messages=safe_messages, tools=list(tools or ()), temperature=temperature
        )
        if not isinstance(message, dict):
            raise RuntimeError(
                "GigaChat вернул ответ неизвестного вида "
                f"({type(message).__name__}) — ожидалось сообщение модели"
            )
        return message

    # ---------------------------------------------------------
    # Разбор вызовов инструментов
    # ---------------------------------------------------------
    @staticmethod
    def parse_tool_calls(message: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Приводит ``tool_calls`` модели к простому виду.

        GigaChat отдаёт аргументы СТРОКОЙ с JSON. Строка, которую не удалось
        разобрать, не теряется: она остаётся в ключе ``raw``, а ``arguments``
        пуст — агент сообщит модели об ошибке, а не упадёт.
        """
        parsed: List[Dict[str, Any]] = []
        for index, call in enumerate(message.get("tool_calls") or ()):
            function = call.get("function") or {}
            raw = function.get("arguments")
            arguments: Dict[str, Any] = {}
            error = ""
            if isinstance(raw, dict):
                arguments = dict(raw)
            elif isinstance(raw, str) and raw.strip():
                try:
                    loaded = json.loads(raw)
                except (TypeError, ValueError) as e:
                    error = f"аргументы инструмента не разобраны: {e}"
                    loaded = None
                if isinstance(loaded, dict):
                    arguments = loaded
                elif not error:
                    error = "аргументы инструмента не являются объектом JSON"
            elif raw not in (None, ""):
                error = "аргументы инструмента неизвестного вида"

            parsed.append({
                "id": str(call.get("id") or f"call_{index}"),
                "name": str(function.get("name") or ""),
                "arguments": arguments,
                "raw": raw if isinstance(raw, str) else "",
                "error": error,
            })
        return parsed


__all__ = ["MASKED_ROLES", "SafeChat"]
