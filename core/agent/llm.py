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
import re
from typing import Any, Dict, List, Optional, Sequence

from core.pseudonymizer import Pseudonymizer

logger = logging.getLogger("core.agent.llm")

#: Роли, содержимое которых уходит во внешнюю модель как текст документа.
#: Системный промпт — это правила программы (в нём нет данных оператора),
#: assistant-сообщения агент формирует сам.
MASKED_ROLES = ("user", "tool")

#: Форма плейсхолдера: <<PERSON_1>>. По ней строится разбор имени токена и
#: замена столкнувшихся имён (см. SafeChat._absorb_mapping).
TOKEN_RE = re.compile(r"<<([A-ZА-Я_]+)_(\d+)>>")


def ensure_client(client: Any) -> Any:
    """
    Проверяет, что пришёл ЭКЗЕМПЛЯР клиента, а не класс.

    Ловушка, из-за которой Максимилиан отвечал «missing 1 required
    positional argument: 'self'»: в сборку попадал сам класс
    ``GigaChatClient``. У класса метод ``complete_chat`` остаётся функцией,
    и первый аргумент ``self`` никто не подставляет — вызов падает уже
    в момент запроса, когда оператор ждёт ответ.

    Проверка стоит на входе в диалоговый слой: ошибиться в сборке (ui)
    дёшево, а диагностировать потом дорого, поэтому падаем сразу и с
    рецептом.

    :return: тот же объект, если он пригоден для запроса.
    """
    if isinstance(client, type):
        raise TypeError(
            f"Передан класс {client.__name__} вместо экземпляра. "
            f"Создайте: client = {client.__name__}(...)"
        )
    if not callable(getattr(client, "complete_chat", None)):
        raise TypeError(
            f"У клиента {type(client).__name__} нет метода complete_chat — "
            "нужен экземпляр GigaChatClient или подставной объект с этим методом"
        )
    return client


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
        self.client = ensure_client(client)
        # Адреса в диалоге НЕ маскируются (в отличие от распознавания
        # документов): адрес — то, с чем Максимилиан работает (проверка
        # через DaData, подстановка в форму). В маске инструмент вернул бы
        # модели «<<ADDRESS_1>>, <<ADDRESS_2>>» вместо адреса, и ответ
        # оператору стал бы неразборчивым. ФИО, паспорта и телефоны
        # маскируются как прежде.
        self._pseudonymizer = Pseudonymizer(mask_addresses=False)
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
        """
        Маскирует текст и запоминает маппинг до конца запроса.

        Маппинг СОГЛАСОВАН с уже замаскированными сообщениями этого запроса
        (см. ``_absorb_mapping``): один и тот же плейсхолдер не может
        означать два разных значения. Без этого «Заполни» и «Отмени» —
        два разных сообщения оператора — уходили в модель одним и тем же
        текстом ``<<PERSON_1>>``, а инструмент получал то значение, которое
        попало в маппинг последним (живой случай на стресс-тесте: на
        «Найди» агент отвечал разбором слова «Заполни»).
        """
        safe_text, mapping = self._pseudonymizer.anonymize(text)
        return self._absorb_mapping(safe_text, mapping)

    def _absorb_mapping(self, text: str,
                        mapping: Dict[str, str]) -> str:
        """
        Вливает маппинг одного сообщения в маппинг всего запроса.

        Три правила:

          * значение уже известно — берётся ЕГО токен: одно и то же имя во
            всех сообщениях выглядит для модели одинаково;
          * имя токена свободно — берётся как есть;
          * имя занято ДРУГИМ значением — токен переименовывается
            (``<<PERSON_1>>`` → ``<<PERSON_2>>``) и запоминается под новым
            именем. Так у каждого значения свой токен, и ``restore``
            возвращает именно то, что было замаскировано.

        Переименование идёт ОДНИМ проходом по исходному тексту: иначе новая
        замена поймала бы токен, переименованный предыдущей.
        """
        if not mapping:
            return text

        plan: Dict[str, str] = {}
        reserved = set(self._mapping) | set(mapping)
        for token, value in mapping.items():
            same = self._token_for_value(value)
            if same is not None and same != token:
                plan[token] = same
                continue
            existing = self._mapping.get(token)
            if existing is None:
                self._mapping[token] = value
                continue
            if existing == value:
                continue
            fresh = self._free_token(token, reserved)
            reserved.add(fresh)
            plan[token] = fresh
            self._mapping[fresh] = value

        if not plan:
            return text
        return TOKEN_RE.sub(
            lambda match: plan.get(match.group(0), match.group(0)), text
        )

    def _token_for_value(self, value: str) -> Optional[str]:
        """Токен, которым это значение уже замаскировано в этом запросе."""
        for token, known in self._mapping.items():
            if known == value:
                return token
        return None

    @staticmethod
    def _free_token(token: str, reserved: set) -> str:
        """
        Свободное имя токена того же вида.

        Номер ищется от единицы: ``<<PERSON_1>>`` занят — берётся
        ``<<PERSON_2>>``. Форма остаётся канонической, поэтому
        ``Pseudonymizer.restore`` находит такой токен в маппинге.
        """
        match = TOKEN_RE.fullmatch(token)
        kind = match.group(1) if match else "TOKEN"
        number = 1
        while f"<<{kind}_{number}>>" in reserved:
            number += 1
        return f"<<{kind}_{number}>>"

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


__all__ = ["MASKED_ROLES", "SafeChat", "ensure_client"]
