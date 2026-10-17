#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Умная обрезка текста: по границе слова и по концу предложения.

Зачем отдельный модуль
----------------------
До 17.10.2026 обрезка была скопирована в шести местах одной строкой::

    return text if len(text) <= limit else text[:limit - 1] + "…"

Счёт символов не знает, где кончается слово. Оператор видел в панели
юридическое предупреждение, оборванное на полуслове: «…Пустой маршрут
оставляет дого…». Такой текст читается как ошибка программы: непонятно,
что именно хотел сказать помощник.

Правила (их проверяет `tests/unit/agent/test_agent_text_truncation.py`)
----------------------------------------------------------------------
1. Короче лимита — не трогаем, многоточие не добавляем. Текст ровно по
   лимиту тоже считается целым.
2. Длиннее — режем по границе: конец предложения (`SENTENCE`), слово
   (`WORD`, по умолчанию) или, если границы нет вовсе, по символу.
3. Результат НИКОГДА не длиннее лимита.
4. Плейсхолдер шаблона (`{{…}}`, `{%…%}`, `<<…>>`) не разрывается:
   половинка плейсхолдера — это испорченный шаблон, а не текст.
5. Многоточие появляется только тогда, когда текст действительно обрезан.

Граница предложения — не любая точка: «п. 6», «ст. 8 ч. 6», «ПП РФ
№ 2200» — это сокращения, а не концы фраз. Поэтому концом предложения
считается знак, после которого идёт пробел и ЗАГЛАВНАЯ буква (или
открывающая кавычка/скобка). Аббревиатуры с числом после себя отсекаются
сами: «п. 6» — цифра, а не заглавная.

Модуль чистый: ни Qt, ни БД, ни знаний о полях формы. Поэтому им
пользуются и агент, и инструменты, и юридические подсказки.
"""

from __future__ import annotations

import re
from typing import Any

#: Чем помечается обрезанный текст. Один символ, а не три точки: в панели
#: «…» читается как знак обрезки, а «...» — как часть текста.
ELLIPSIS = "…"

#: Резать по границе слова (по умолчанию).
WORD = "word"
#: Резать по концу предложения — для связных текстов (юридические
#: предупреждения, объяснения). Если целого предложения в лимит не влезает,
#: обрезка откатывается к границе слова.
SENTENCE = "sentence"

#: Знаки, которые в конце обрезанного куска выглядят обрубком.
_TAIL_PUNCTUATION = " \t\r\n.,;:—–-"
#: Открывающие символы: если обрезка пришлась сразу после них, это обрубок.
_OPENERS = "«([{"

#: Конец предложения: знак, закрывающие кавычки/скобки, затем пробел и
#: заглавная буква (или «, ( — начало реплики). Число «п. 6» не считается:
#: цифра после точки — это пункт, а не новое предложение.
_SENTENCE_END_RE = re.compile(r"[.!?…]+[»\"')\]]*(?=\s+[A-ZА-ЯЁ«(\[])")

#: Конструкции, которые нельзя разрывать посередине: подстановка шаблона,
#: тег условного блока/цикла и маска персональных данных.
_PROTECTED_TOKENS = (
    re.compile(r"\{\{.*?\}\}"),
    re.compile(r"\{%.*?%\}"),
    re.compile(r"<<.*?>>"),
)


def shorten(text: Any, limit: int, *, mode: str = WORD,
            ellipsis: str = ELLIPSIS, min_ratio: float = 0.5) -> str:
    """
    Обрезает текст до ``limit`` символов по границе слова.

    :param text: что обрезать (``None`` — пустая строка);
    :param limit: предел длины результата в символах;
    :param mode: ``WORD`` — по границе слова, ``SENTENCE`` — по концу
        предложения (с откатом к слову);
    :param ellipsis: чем помечать обрезку («…»);
    :param min_ratio: какую часть лимита разрешено потерять ради границы.
        При 0.5 граница ищется только во второй половине строки: иначе
        ради «красивой» обрезки осталось бы два слова из двухсот.
    :return: текст не длиннее ``limit`` символов.

    Примеры::

        shorten("Раз два три", 5)            # "Ра…"  — границы нет
        shorten("Раз два три", 8)            # "Раз…" — по пробелу
        shorten("Коротко", 60)               # "Коротко" — не тронут
        shorten(текст, 400, mode=SENTENCE)   # по концу предложения
    """
    if mode not in (WORD, SENTENCE):
        raise ValueError(f"неизвестный режим обрезки: {mode!r}")

    body = "" if text is None else str(text)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"лимит должен быть числом: {limit!r}") from None

    if limit <= 0:
        return ""
    if len(body) <= limit:
        return body
    if limit <= len(ellipsis):
        # Места на многоточие нет вовсе — только жёсткая обрезка.
        return body[:limit]

    room = limit - len(ellipsis)
    head = body[: _cut_index(body, room, mode, min_ratio)]
    head = _clean_tail(head)
    if not head:
        # Резать нечего (строка начинается с плейсхолдера) — отдаём знак.
        return ellipsis[:limit]
    return head + ellipsis


# ─────────────────────────────────────────────────────────────
# Где резать
# ─────────────────────────────────────────────────────────────

def _cut_index(body: str, room: int, mode: str, min_ratio: float) -> int:
    """Индекс, до которого можно взять текст (сам индекс не входит)."""
    room = max(1, min(room, len(body)))

    index = None
    if mode == SENTENCE:
        index = _sentence_index(body, room, min_ratio)
    if index is None:
        index = _word_index(body, room, min_ratio)
    if index is None:
        index = room
    return _avoid_protected(body, index)


def _sentence_index(body: str, room: int, min_ratio: float):
    """Конец последнего целого предложения в пределах ``room`` (или None)."""
    last = None
    for match in _SENTENCE_END_RE.finditer(body):
        if match.end() > room:
            break
        last = match.end()
    if last is None:
        return None
    # Предложение из двух слов ради «красивой» обрезки не годится: если
    # целая мысль кончается в самом начале, лучше резать по слову.
    if last < room * max(0.0, min(1.0, float(min_ratio))):
        return None
    return last


def _word_index(body: str, room: int, min_ratio: float):
    """Последний пробел в пределах ``room`` (или None, если его нет)."""
    floor = int(room * max(0.0, min(1.0, float(min_ratio))))
    for index in range(room - 1, floor - 1, -1):
        if body[index].isspace():
            return index
    return None


def _avoid_protected(body: str, index: int) -> int:
    """
    Отодвигает обрезку назад, если она попала ВНУТРЬ плейсхолдера.

    Половинка `{{contract_tab.route}}` в подсказке выглядит как сбой
    программы, а половинка `<<PERSON_1>>` — ещё и как утечка вида маски.
    """
    for pattern in _PROTECTED_TOKENS:
        for match in pattern.finditer(body):
            if match.start() < index < match.end():
                return match.start()
    return index


def _clean_tail(head: str) -> str:
    """Убирает с конца обрубки: пробелы, запятые, тире, открытые скобки."""
    text = head.rstrip()
    while text and text[-1] in _TAIL_PUNCTUATION:
        text = text[:-1]
    text = text.rstrip()
    while text and text[-1] in _OPENERS:
        text = text[:-1].rstrip()
    return text


__all__ = [
    "ELLIPSIS",
    "SENTENCE",
    "WORD",
    "shorten",
]
