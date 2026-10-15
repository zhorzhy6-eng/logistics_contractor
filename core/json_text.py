#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Короткий текст, который остаётся КОРРЕКТНЫМ JSON (задание, часть 2).

Зачем это отдельным модулем
---------------------------
Результат инструмента уходит в GigaChat сообщением ``role="function"``.
GigaChat разбирает это содержимое как JSON: если JSON оборван, запрос
отвергается целиком —

    422 INVALID_PARAMS: invalid function result for function diagnose json string …

Именно это и случилось с инструментом ``diagnose``: отчёт диагностики
(4 120 символов) обрезался до 4 000 с многоточием
(``core/agent/memory.py::_as_text``), а обрезанный JSON перестаёт быть JSON.
GigaChat отвечает «invalid function result», и ЛЮБОЙ запрос оператора
заканчивается ошибкой — падал не инструмент, падал весь диалог.

Правило этого модуля: длину ограничиваем, но валидность JSON не теряем
никогда. Урезанный результат остаётся объектом с ключами ``ok``,
``message``/``error`` и пометкой ``truncated`` — модель видит, что подробности
не влезли, и не выдумывает их.

Модуль не знает ни про GigaChat, ни про агента: только JSON и длина.
"""

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

#: Сколько символов оставлять от одной строки при сжатии.
MAX_STRING_CHARS = 200

#: Сколько элементов оставлять от списка при сжатии.
MAX_LIST_ITEMS = 5

#: Пометка для модели: подробности не влезли.
TRUNCATED_NOTE = (
    "результат сокращён — подробности оператор видит в интерфейсе программы"
)

#: Ключи, которые при сжатии сохраняются в первую очередь.
KEEP_KEYS = ("ok", "error", "message", "count", "found", "truncated", "note")


def is_valid_json(text: Any) -> bool:
    """Является ли текст корректным JSON (проверка на всякий случай)."""
    if not isinstance(text, str):
        return False
    try:
        json.loads(text)
    except (TypeError, ValueError):
        return False
    return True


def _dump(value: Any) -> str:
    """Компактный JSON без лишних пробелов."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      default=str)


def _shrink(node: Any, depth: int = 0) -> Any:
    """
    Сжимает значение: длинные строки короче, длинные списки — короче.

    Словари сохраняются целиком (в них смысл ответа), но их значения тоже
    проходят сжатие. Глубина ограничена: на больших вложенных структурах
    рекурсия должна остановиться, а не уйти вглубь.
    """
    if depth > 6:
        return "…" if isinstance(node, str) else None
    if isinstance(node, str):
        if len(node) <= MAX_STRING_CHARS:
            return node
        return node[:MAX_STRING_CHARS - 1] + "…"
    if isinstance(node, list):
        items = [_shrink(item, depth + 1) for item in node[:MAX_LIST_ITEMS]]
        if len(node) > MAX_LIST_ITEMS:
            items.append(f"…ещё {len(node) - MAX_LIST_ITEMS}")
        return items
    if isinstance(node, dict):
        return {str(key): _shrink(value, depth + 1)
                for key, value in node.items()}
    return node


def _minimal(data: Any) -> Dict[str, Any]:
    """
    Последний рубеж: маленький объект, который точно влезет.

    Из ответа инструмента берутся только самые важные ключи — по ним модель
    понимает, получилось или нет, и что сказать оператору.
    """
    result: Dict[str, Any] = {"truncated": True, "note": TRUNCATED_NOTE}
    if isinstance(data, dict):
        for key in KEEP_KEYS:
            if key in data and key != "truncated":
                value = data[key]
                if isinstance(value, (str, int, float, bool)) or value is None:
                    result[key] = _shrink(value)
    return result


def shorten_json_text(text: str, limit: int = 4000) -> str:
    """
    Ограничивает длину JSON-текста, НЕ ломая JSON.

    Порядок попыток:

      1. текст и так короткий — возвращается как есть;
      2. текст разбирается и пересобирается компактно (без пробелов) —
         обычно этого достаточно: «полотно» из отступов исчезает;
      3. значения сжимаются (длинные строки и списки короче);
      4. остаётся минимальный объект с пометкой ``truncated``.

    :param text: JSON-текст (результат инструмента);
    :param limit: сколько символов допустимо;
    :return: текст длиной не больше ``limit``, который является JSON.
        Не-JSON на входе тоже превращается в корректный JSON: сообщение об
        этом важнее, чем сохранение исходной строки (иначе запрос к модели
        отвергается целиком).
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if len(text) <= limit:
        return text

    try:
        data: Any = json.loads(text)
    except (TypeError, ValueError):
        logger.warning(
            "Результат инструмента не является JSON (%s символов) — "
            "отдаём пояснение вместо него", len(text),
        )
        fallback = {"ok": False, "truncated": True,
                    "error": "результат инструмента не является JSON",
                    "note": TRUNCATED_NOTE, "size": len(text)}
        return _dump(fallback)[:limit]

    compact = _dump(data)
    if len(compact) <= limit:
        return compact

    #: Сжимаем и добавляем пометку: модель должна знать, что подробностей нет.
    marked = data if isinstance(data, dict) else {"ok": True, "data": data}
    marked = dict(marked)
    marked["truncated"] = True
    marked.setdefault("note", TRUNCATED_NOTE)

    shrunk = _dump(_shrink(marked))
    if len(shrunk) <= limit:
        return shrunk

    #: Не влезло даже сжатым: оставляем только самое важное.
    minimal = _minimal(marked)
    candidate = _dump(minimal)
    if len(candidate) <= limit:
        return candidate

    #: Предел совсем маленький: убираем пояснения по одному, но JSON
    #: остаётся JSON — иначе запрос к модели отвергается целиком.
    for key in ("note", "error", "message"):
        minimal.pop(key, None)
        candidate = _dump(minimal)
        if len(candidate) <= limit:
            return candidate

    return _dump({"truncated": True})[:limit]


__all__ = [
    "KEEP_KEYS",
    "MAX_LIST_ITEMS",
    "MAX_STRING_CHARS",
    "TRUNCATED_NOTE",
    "is_valid_json",
    "shorten_json_text",
]
