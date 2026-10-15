#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Память диалога Максимилиана (задание, части A.4 и F).

Две стороны одной вещи:

  * в ПАМЯТИ — список последних сообщений (по умолчанию 20), из которого
    собирается запрос к GigaChat;
  * в БАЗЕ — таблица ``agent_history`` (db/crud/agent_history.py), чтобы
    история пережила перезапуск программы.

Сессия = один запуск программы (``session_id``). Новая сессия — новая
история; чистка старых сессий — отдельная операция (кнопка в настройках).

ОДИН РАЗГОВОР НА ВСЮ ПРОГРАММУ. Максимилиан живёт в каждом окне (пять
окон типов + Экспедиторство), и у каждого свой ``AgentWidget`` со своей
``AgentMemory``. Если бы каждая память жила сама по себе, разговор
обрывался бы при переключении окна: оператор спросил в Экспедиторстве,
перешёл в Аренду — и там пустая панель, будто помощник всё забыл. Поэтому
история — ОДНА на сессию, и перед каждым запросом память сверяется с базой
(``sync()``): если в другом окне добавились сообщения, они подтягиваются.

Важное про персональные данные: в истории хранятся НАСТОЯЩИЕ значения —
это данные оператора в его собственной локальной базе, и без них панель
не смогла бы показать диалог после перезапуска. Наружу (в GigaChat) уходит
только маска: её ставит core/agent/llm.py перед каждым запросом, а
восстановление оригиналов в ответе делает он же. В логи ни текст диалога,
ни значения полей не пишутся.
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from core.json_text import shorten_json_text

logger = logging.getLogger("core.agent.memory")

#: Сколько сообщений помним по умолчанию (задание, часть A.4).
DEFAULT_MAX_HISTORY = 20

#: Роли, которые понимает GigaChat.
WIRE_ROLES = ("system", "user", "assistant", "tool")


def new_session_id() -> str:
    """
    Идентификатор сессии — новый на каждый запуск программы.

    Случайный, а не по времени: две сессии в одну секунду (перезапуск)
    не должны склеиться в одну историю.
    """
    return uuid.uuid4().hex[:12]


#: Сессия текущего запуска программы. Одна на процесс: панель может жить в
#: нескольких окнах (Экспедиторство и окна типов), а история диалога должна
#: быть одна — иначе «продолжить разговор» после переключения окна не выйдет.
_SESSION_ID = new_session_id()


def current_session_id() -> str:
    """Идентификатор сессии этого запуска программы (не меняется)."""
    return _SESSION_ID


def reset_session_id() -> str:
    """Начинает новую сессию (нужно тестам и ручной проверке)."""
    global _SESSION_ID
    _SESSION_ID = new_session_id()
    return _SESSION_ID


@dataclass
class Message:
    """Одно сообщение истории."""

    role: str
    content: str = ""
    tool_name: Optional[str] = None
    #: Вызовы инструментов в ответе модели (только для role="assistant").
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    #: ID вызова, на который отвечает сообщение role="tool".
    tool_call_id: Optional[str] = None

    def wire(self) -> Dict[str, Any]:
        """
        Сообщение в формате запроса к GigaChat.

        Пустые необязательные поля не добавляются: GigaChat строг к составу
        сообщения, а лишний ``tool_calls: []`` у пользовательского сообщения
        ломает запрос.
        """
        item: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.role == "assistant" and self.tool_calls:
            item["tool_calls"] = self.tool_calls
        if self.role == "tool":
            item["name"] = self.tool_name or "tool"
            if self.tool_call_id:
                item["tool_call_id"] = self.tool_call_id
        return item


def _as_text(value: Any, limit: int = 4000, role: str = "") -> str:
    """
    Строка из любого значения с ограничением длины (защита от «полотна»).

    Для результата инструмента (``role="tool"``) ограничение идёт через
    ``core/json_text.shorten_json_text``: содержимое уходит в GigaChat
    сообщением ``role="function"``, и оборванный JSON GigaChat отвергает
    целиком — «422 invalid function result» (см. ``core/json_text.py``).
    Обычный текст режется как прежде: JSON в нём нет.
    """
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    if role == "tool":
        return shorten_json_text(text, limit)
    return text[:limit] + "…"


class AgentMemory:
    """
    История диалога: в памяти — последние N сообщений, в базе — все.

    Репозиторий (``repository``) — необязательный объект с методами
    ``append``/``load``/``clear_session``/``purge_other_sessions``. По
    умолчанию берётся db/crud/agent_history.py, но его можно подменить
    (нужно тестам, чтобы не поднимать базу).

    Любая ошибка записи в базу не прерывает диалог: история остаётся
    доступной в памяти (в лог идёт предупреждение).
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        max_history: int = DEFAULT_MAX_HISTORY,
        repository: Any = None,
        autoload: bool = True,
    ):
        self.session_id = str(session_id or current_session_id())
        self.max_history = max(1, int(max_history))
        self._repository = repository
        self._messages: List[Message] = []
        #: Сколько записей сессии было в базе на момент последней сверки.
        #: По нему видно, что в ДРУГОМ окне прошёл разговор (см. sync).
        self._synced_count = 0

        if autoload:
            self.load()

    # ---------------------------------------------------------
    # Репозиторий
    # ---------------------------------------------------------
    @property
    def repository(self) -> Any:
        """Репозиторий истории: подставной или таблица agent_history."""
        if self._repository is None:
            from db.crud import agent_history

            self._repository = agent_history
        return self._repository

    # ---------------------------------------------------------
    # Чтение
    # ---------------------------------------------------------
    def load(self) -> List[Message]:
        """
        Загружает историю текущей сессии из базы.

        В память берутся последние ``max_history`` записей. Загруженные
        сообщения возвращаются, а не только кладутся в память: панели нужно
        сразу показать диалог после перезапуска.
        """
        try:
            rows = self.repository.load(self.session_id, self.max_history + 1)
        except Exception as e:  # noqa: BLE001 — история не должна ронять панель
            logger.warning("История агента не загружена: %s", e)
            return []

        restored = [self._from_row(row) for row in rows or ()]
        # Хвост мог оборваться на промежуточном шаге работы инструментов
        # (историю обрезали по лимиту или закрыли программу посреди ответа):
        # «висячие» результаты инструментов в начало не ставим — GigaChat
        # такой истории не примет (см. wire_messages).
        self._messages = restored[-self.max_history:]
        self._messages = self._complete_tail(self._messages)
        self._remember_synced()
        return list(self._messages)

    def _remember_synced(self) -> None:
        """
        Запоминает, сколько записей сессии сейчас в базе.

        Считается ДО обрезки по лимиту: сравнение идёт с базой, а не с
        памятью окна. Ошибка счётчика не важна — сверка просто повторится.
        """
        try:
            self._synced_count = int(self.repository.count(self.session_id))
        except Exception as e:  # noqa: BLE001 — счётчик не должен ронять диалог
            logger.debug("История агента: счётчик не прочитан (%s)", e)
            self._synced_count = len(self._messages)

    def sync(self) -> List[Message]:
        """
        Подтягивает сообщения, добавленные в ДРУГИХ окнах программы.

        Максимилиан установлен в каждое окно, и у каждого своя память, но
        разговор — один (см. описание модуля). Перед запросом память
        сверяется с базой: если записей в сессии стало больше, значит в
        другом окне уже спрашивали, и продолжать надо с той же историей.

        :return: история после сверки (пусто — если меняться нечему)
        """
        try:
            current = int(self.repository.count(self.session_id))
        except Exception as e:  # noqa: BLE001 — сверка не должна ронять запрос
            logger.debug("История агента: сверка не удалась (%s)", e)
            return []
        if current <= self._synced_count:
            return []

        loaded = self.load()
        if loaded:
            logger.info(
                "История Максимилиана продолжена из другого окна "
                "(сообщений в сессии: %s)", current
            )
        return loaded

    def _complete_tail(self, messages: List[Message]) -> List[Message]:
        """
        Убирает из начала «висячие» сообщения инструментов.

        Сообщение role="tool" обязано идти после вызова инструмента моделью.
        Если начало истории обрезано и первым идёт результат инструмента,
        запрос к GigaChat будет отвергнут — такие сообщения отбрасываются.
        """
        index = 0
        while index < len(messages) and messages[index].role == "tool":
            index += 1
        return messages[index:]

    @staticmethod
    def _from_row(row: Dict[str, Any]) -> Message:
        """Запись базы → сообщение. Формат записи описан в db/crud/agent_history.py."""
        role = str(row.get("role") or "").strip()
        content = str(row.get("content") or "")
        tool_name = row.get("tool_name") or None

        # Запись ассистента с вызовами инструментов писалась JSON-ом в
        # tool_name — так таблица осталась в схеме задания (F.1).
        tool_calls: List[Dict[str, Any]] = []
        if role == "assistant" and tool_name:
            import json

            try:
                loaded = json.loads(str(tool_name))
            except (TypeError, ValueError):
                loaded = None
            if isinstance(loaded, list):
                tool_calls = [call for call in loaded if isinstance(call, dict)]
                tool_name = None

        return Message(role=role, content=content, tool_name=tool_name,
                       tool_calls=tool_calls)

    @property
    def messages(self) -> List[Message]:
        """История в памяти (копия списка)."""
        return list(self._messages)

    @property
    def is_empty(self) -> bool:
        """Диалог ещё не начинался."""
        return not self._messages

    def wire_messages(self, system_prompt: str = "") -> List[Dict[str, Any]]:
        """
        История в формате запроса к GigaChat.

        Системный промпт идёт первым (если задан). Незавершённый хвост
        отбрасывается: ответ ассистента с вызовами инструментов без
        результатов этих вызовов GigaChat не принимает, а обрыв случается
        штатно — например, оператор закрыл программу посреди ответа.

        Начало блока инструментов хранится ДВАЖДЫ, и это не дублирование:

          * ``block_start`` — место в СПИСКЕ ЗАПРОСА (``wire``): по нему
            блок вырезается;
          * ``block_assistant`` — САМО сообщение ассистента: по нему
            проверяется, все ли вызовы получили ответ.

        Раньше для проверки бралось ``self._messages[block_start]`` — то
        есть индекс из запроса применялся к истории. Списки не совпадают
        (системный промпт добавляет элемент, «висячие» результаты
        инструментов пропускаются), поэтому проверялось ЧУЖОЕ сообщение:
        незакрытый блок оставался в запросе и следующий запрос оператора
        отвергался вместе с ним. Нашлось перебором историй
        (tests/test_agent_memory_wire.py).
        """
        wire: List[Dict[str, Any]] = []
        if system_prompt:
            wire.append({"role": "system", "content": system_prompt})

        #: Начало незавершённого блока: ответ ассистента с вызовами и ответы
        #: инструментов на них. Если блок не закрылся — он в запрос не идёт.
        block_start: Optional[int] = None
        block_assistant: Optional[Message] = None
        answered: set = set()

        for message in self._messages:
            if block_assistant is not None and message.role != "tool":
                # Блок закрылся: все вызовы получили ответы (иначе — выбросить).
                if not self._all_answered(block_assistant, answered):
                    del wire[block_start:]
                block_start = None
                block_assistant = None
                answered = set()

            if message.role == "assistant" and message.tool_calls:
                block_start = len(wire)
                block_assistant = message
                answered = set()
            elif message.role == "tool" and block_start is None:
                # Результат инструмента без своего вызова: так выглядит
                # история, обрезанная по лимиту. GigaChat такое не примет.
                continue

            if message.role == "tool" and message.tool_call_id:
                answered.add(message.tool_call_id)

            wire.append(message.wire())

        if block_assistant is not None and not self._all_answered(
            block_assistant, answered
        ):
            del wire[block_start:]
        return wire

    @staticmethod
    def _all_answered(assistant: Optional[Message], answered: set) -> bool:
        """Все ли вызовы инструментов этого ответа получили результат."""
        if assistant is None or not assistant.tool_calls:
            return True
        expected = {
            str(call.get("id") or "")
            for call in assistant.tool_calls
            if isinstance(call, dict)
        }
        expected.discard("")
        return expected.issubset(answered)

    # ---------------------------------------------------------
    # Запись
    # ---------------------------------------------------------
    def add(self, role: str, content: str, tool_name: Optional[str] = None,
            tool_calls: Optional[Sequence[Dict[str, Any]]] = None,
            tool_call_id: Optional[str] = None,
            persist: bool = True) -> Message:
        """
        Добавляет сообщение в память (и, по умолчанию, в базу).

        :param persist: False — сообщение нужно только для текущего запроса
            (промежуточные результаты инструментов в истории не сохраняются).
        """
        role_value = str(role or "").strip()
        if role_value not in WIRE_ROLES:
            logger.warning("Память агента: неизвестная роль %r — пропущено", role)
            return Message(role="assistant", content="")

        message = Message(
            role=role_value,
            content=_as_text(content, role=role_value),
            tool_name=tool_name,
            tool_calls=[dict(call) for call in (tool_calls or ())],
            tool_call_id=tool_call_id,
        )
        self._messages.append(message)
        self._trim()

        if persist:
            self._persist(message)
            #: Сообщение записали сами — счётчик сверки растёт вместе с базой,
            #: иначе следующий sync() принял бы свою же запись за чужую.
            self._synced_count += 1
        return message

    def _persist(self, message: Message) -> None:
        """Пишет сообщение в базу; ошибка не прерывает диалог."""
        tool_name = message.tool_name
        if message.role == "assistant" and message.tool_calls:
            import json

            tool_name = json.dumps(message.tool_calls, ensure_ascii=False)
        try:
            self.repository.append(
                self.session_id, message.role, message.content, tool_name=tool_name
            )
        except Exception as e:  # noqa: BLE001 — база не должна ронять диалог
            logger.warning("Сообщение агента не сохранено: %s", e)

    def _trim(self) -> None:
        """Оставляет последние ``max_history`` сообщений."""
        if len(self._messages) > self.max_history:
            self._messages = self._messages[-self.max_history:]

    # ---------------------------------------------------------
    # Очистка
    # ---------------------------------------------------------
    def clear(self) -> int:
        """
        Очищает историю ТЕКУЩЕЙ сессии (кнопка «Очистить историю»).

        :return: сколько записей удалено из базы (-1 — не удалось)
        """
        self._messages = []
        try:
            removed = int(self.repository.clear_session(self.session_id))
            self._synced_count = 0
            return removed
        except Exception as e:  # noqa: BLE001
            logger.warning("История агента не очищена: %s", e)
            return -1

    def purge_other_sessions(self) -> int:
        """Удаляет истории прошлых сессий (кнопка в настройках)."""
        try:
            return int(self.repository.purge_other_sessions(self.session_id))
        except Exception as e:  # noqa: BLE001
            logger.warning("Старые сессии агента не удалены: %s", e)
            return -1


__all__ = [
    "DEFAULT_MAX_HISTORY",
    "WIRE_ROLES",
    "AgentMemory",
    "Message",
    "current_session_id",
    "new_session_id",
    "reset_session_id",
]