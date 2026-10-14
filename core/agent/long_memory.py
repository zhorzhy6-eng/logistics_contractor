#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Что Максимилиан помнит (задание, часть 5).

Память помощника — это три вида записей в таблице ``agent_memory``
(db/crud/agent_memory.py): **алиасы** («Ромашка» = ООО «Ромашка»),
**привычки** («всегда НДС 20 % для Ромашки») и **исправления** («ИНН
с пробелами → нормализовать»). Здесь живёт работа с ними на уровне смысла:

  * ``recall()`` — что подложить в промпт: TOP-5 релевантных записей, а не
    вся память (контекст модели не резиновый);
  * ``remember_*()`` — записать алиас, привычку или исправление;
  * ``extract()`` — вытащить из фразы оператора то, что стоит запомнить
    («запомни: Ромашка — это ООО Ромашка»);
  * ``feedback()`` — 👍/👎 на ответ помощника.

Почему отдельный модуль: правила отбора и разбора фраз — это знание о
работе оператора, а не о базе и не о Qt. Их проверяют тестами без поднятия
интерфейса и без GigaChat.

Что в память НЕ попадает: пароли, ключи, паспорта, телефоны и адреса.
Память — подсказка для модели, а не хранилище персональных данных
(правило проекта: логи и подсказки без ПДн).
"""

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from db.crud import agent_memory

logger = logging.getLogger("core.agent.long_memory")

#: Сколько записей памяти подкладывается в промпт (задание, часть 5).
TOP_RELEVANT = 5

#: Сколько записей показывать в настройках («Что Максимилиан помнит»).
LIST_LIMIT = 200

#: Русские названия видов записей — то, что видит оператор.
KIND_TITLES: Dict[str, str] = {
    "alias": "Алиас",
    "habit": "Привычка",
    "correction": "Исправление",
}

#: Подсказки «что это значит» в списке памяти.
KIND_HINTS: Dict[str, str] = {
    "alias": "сокращение, которое вы называете вместо полного имени",
    "habit": "правило по умолчанию для этого контрагента или типа договора",
    "correction": "как исправлять значение, если оно пришло неверно",
}

#: Слова, по которым видно, что оператор просит запомнить.
REMEMBER_CUES = (
    "запомни", "запомнить", "запомни, что", "учти", "учитывай",
    "всегда", "в дальнейшем", "на будущее", "имей в виду", "имей ввиду",
)

#: Слова, по которым видно, что оператор поправляет помощника.
CORRECTION_CUES = (
    "неправильно", "не так", "ошибка", "ошибся", "исправь", "поправь",
    "на самом деле", "вообще-то", "неверно", "это не",
)

#: Служебные слова в НАЧАЛЕ фразы: в ключ и значение они не входят.
#: Длинные идут первыми, чтобы «не так» не съело «не так-то».
LEADING_CUES: Tuple[str, ...] = (
    "запомни, что", "запомни:", "запомни", "учти, что", "учти",
    "имей в виду", "имей ввиду", "на будущее", "в дальнейшем",
    "всегда", "по умолчанию",
    "это не", "неправильно", "неверно", "не так", "ошибка", "ошибся",
    "исправь", "поправь", "на самом деле", "вообще-то",
    "надо", "нужно",
)

#: Шаблон «X — это Y» / «X = Y»: так оператор объясняет алиас.
_ALIAS_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"^(?P<key>[^:=]{2,80}?)\s*(?:—|–|-|=|это)\s*(?P<value>.{2,})$"),
    re.compile(r"^(?P<key>.{2,80}?)\s+означает\s+(?P<value>.{2,})$"),
)

#: Что запрещено класть в память (по виду значения — ПДн и секреты).
_FORBIDDEN = (
    re.compile(r"\b\d{4}\s?\d{6}\b"),                 # паспорт: 28 15 634512
    re.compile(r"\b\d{2}\s?\d{2}\s?\d{6}\b"),         # паспорт: 28 15 634512
    re.compile(r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}"),
    re.compile(r"\b\d{10,12}\b"),                     # ИНН/ОГРН/карта
    re.compile(r"\b(?:sk|pk|api|token|key)[-_][A-Za-z0-9]{8,}\b", re.IGNORECASE),
    re.compile(r"\bпароль\b", re.IGNORECASE),
)

#: Виды привычек, которые память понимает (ключ привычки).
HABIT_KINDS = ("vat", "payment", "prepayment", "route", "currency", "other")


def kind_title(kind: str) -> str:
    """Русское название вида записи."""
    return KIND_TITLES.get(str(kind or "").strip().lower(), str(kind or ""))


def kind_hint(kind: str) -> str:
    """Пояснение вида записи (для настроек)."""
    return KIND_HINTS.get(str(kind or "").strip().lower(), "")


# ─────────────────────────────────────────────────────────────
# Отбор того, что показать модели
# ─────────────────────────────────────────────────────────────

#: Короткие слова, которые не считаются «релевантными»: по «и», «в», «за»
#: память искать нельзя — иначе в промпт попадут случайные записи.
_STOP_WORDS = frozenset({
    "и", "в", "во", "на", "за", "по", "с", "со", "к", "ко", "о", "об", "от",
    "до", "для", "что", "как", "где", "это", "мне", "мы", "ты", "он", "она",
    "the", "a", "of", "for", "to",
})


def _tokens(text: str) -> List[str]:
    """
    Значимые слова запроса (в нижнем регистре, без служебных).

    Кириллица в SQLite сравнивается регистрозависимо, поэтому и поиск, и
    сравнение идут по нижнему регистру: так «Ромашка» и «ромашка» — одно.
    """
    words = re.findall(r"[^\W\d_]{3,}", str(text or "").lower(), re.UNICODE)
    return [word for word in words if word not in _STOP_WORDS]


def relevance(record: Dict[str, Any], tokens: Sequence[str]) -> float:
    """
    Насколько запись памяти подходит к запросу (0 — не подходит).

    Считается просто и предсказуемо: совпадение в ключе весит больше, чем в
    значении; за каждое использование запись получает небольшой плюс —
    подтверждённое оператором полезнее случайного.

    Падежи сравниваются ПАДЕЖНОЙ ОСНОВОЙ (`core/fuzzy.py::stem`), той же,
    что и нечёткий поиск по базе: «Ромашка» находит запись «Ромашки».
    Сначала проверяется слово как есть — точное совпадение всегда сильнее
    «догадки», — и только потом основа.
    """
    if not tokens:
        return 0.0
    key = str(record.get("key") or "").lower()
    value = str(record.get("value") or "").lower()

    score = 0.0
    for token in tokens:
        stem = _stem(token)
        if token in key or (stem and stem in key):
            score += 3.0
        elif token in value or (stem and stem in value):
            score += 1.0
    if score == 0:
        return 0.0

    hits = record.get("hits")
    try:
        score += min(2.0, float(hits or 0) * 0.2)
    except (TypeError, ValueError):
        pass

    # Свежее чуть полезнее старого: правки оператора важнее давних записей.
    kind_bonus = 0.3 if str(record.get("kind")) == "correction" else 0.0
    return score + kind_bonus


def _stem(word: str) -> str:
    """Падежная основа слова (пусто — основа не отличается от слова)."""
    try:
        from core.fuzzy import stem

        base = str(stem(word) or "")
    except Exception:  # noqa: BLE001 — без морфологии память работает как есть
        return ""
    return base if base and base != word else ""


def rank(records: Sequence[Dict[str, Any]], query: str,
         limit: int = TOP_RELEVANT) -> List[Dict[str, Any]]:
    """
    TOP-N релевантных записей памяти на запрос (релевантные — сверху).

    Записи без совпадений не возвращаются вовсе: пустая память в промпте
    лучше, чем случайная.
    """
    tokens = _tokens(query)
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for record in records or ():
        score = relevance(record, tokens)
        if score <= 0:
            continue
        scored.append((score, record))
    scored.sort(key=lambda pair: (-pair[0], -int(pair[1].get("id") or 0)))
    return [record for _score, record in scored[: max(1, int(limit))]]


def recall(
    query: str,
    *,
    limit: int = TOP_RELEVANT,
    records: Optional[Sequence[Dict[str, Any]]] = None,
    mark_hits: bool = True,
) -> List[Dict[str, Any]]:
    """
    Что помощник вспоминает на этот запрос: TOP-N релевантных записей.

    :param query: сообщение оператора (может быть пустым — тогда пусто);
    :param records: готовый список записей (тесты; иначе читается база);
    :param mark_hits: отмечать ли использование (счётчик hits).
    """
    data = list(records) if records is not None else agent_memory.load(
        limit=LIST_LIMIT
    )
    if not data:
        return []

    found = rank(data, query, limit=limit)
    if found and mark_hits and records is None:
        try:
            agent_memory.touch([int(item["id"]) for item in found
                                if item.get("id") is not None])
        except Exception as e:  # noqa: BLE001 — счётчик не должен ломать ответ
            logger.debug("Память: счётчик обращений не обновлён (%s)", e)
    return found


def memory_block(found: Sequence[Dict[str, Any]]) -> str:
    """
    Блок памяти для промпта: «что я помню о работе оператора».

    Пусто — пустая строка: если вспомнить нечего, в промпт ничего не
    добавляется (лишний текст только мешает модели).
    """
    items = [item for item in (found or ()) if str(item.get("key") or "").strip()]
    if not items:
        return ""
    lines = [
        "ЧТО ТЫ ПОМНИШЬ (проверенные факты прошлых разговоров — используй их,",
        "но не выдумывай новых и не спорь с оператором):",
    ]
    for item in items:
        kind = kind_title(item.get("kind"))
        lines.append(f"- {kind}: «{item.get('key')}» → {item.get('value')}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Запись в память
# ─────────────────────────────────────────────────────────────

def remember_alias(key: str, value: str, source: str = "оператор") -> Optional[int]:
    """Запоминает алиас: «Ромашка» = ООО «Ромашка»."""
    return _remember("alias", key, value, source)


def remember_habit(key: str, value: str, source: str = "оператор") -> Optional[int]:
    """Запоминает привычку: «всегда НДС 20 % для Ромашки»."""
    return _remember("habit", key, value, source)


def remember_correction(key: str, value: str,
                        source: str = "исправление оператора") -> Optional[int]:
    """Запоминает исправление: «ИНН с пробелами → нормализовать»."""
    return _remember("correction", key, value, source)


def _remember(kind: str, key: str, value: str, source: str) -> Optional[int]:
    """
    Общая запись в память с проверкой на ПДн и секреты.

    Значение с паспортом, телефоном, длинным числом (ИНН, карта) или
    словом «пароль» не запоминается: память уходит в промпт, а промпт — во
    внешний сервис. Для оператора это выглядит как «помощник забыл», и
    причина пишется в лог.
    """
    text = " ".join(str(value or "").split())
    found = look_sensitive(text) or look_sensitive(str(key or ""))
    if found:
        logger.info(
            "Память: значение похоже на персональные данные (%s) — не запоминаю",
            found,
        )
        return None
    return agent_memory.append(kind, key, text, source=source)


def look_sensitive(text: str) -> str:
    """
    Похоже ли значение на персональные данные или секрет.

    :return: описание находки (пусто — чисто)
    """
    body = str(text or "")
    for pattern in _FORBIDDEN:
        if pattern.search(body):
            return pattern.pattern
    return ""


# ─────────────────────────────────────────────────────────────
# Разбор фразы оператора
# ─────────────────────────────────────────────────────────────

def extract(message: str) -> List[Dict[str, str]]:
    """
    Что из фразы оператора стоит запомнить.

    Понимает два случая (задание, часть 5):

      * «запомни: Ромашка — это ООО Ромашка» → алиас;
      * «всегда НДС 20 % для Ромашки» → привычка;
      * «нет, ИНН с пробелами — надо без пробелов» → исправление.

    Возвращается список «вид, ключ, значение» — БЕЗ записи в базу: записывает
    вызывающий код (агент пишет только после подтверждения, часть 5).

    :return: список найденных фактов (пусто — запоминать нечего)
    """
    text = " ".join(str(message or "").split())
    if not text or len(text) > 500:
        return []
    lowered = text.lower()

    is_correction = any(cue in lowered for cue in CORRECTION_CUES)
    is_habit = "всегда" in lowered or "по умолчанию" in lowered
    is_remember = any(cue in lowered for cue in REMEMBER_CUES)
    if not (is_correction or is_habit or is_remember):
        return []

    facts: List[Dict[str, str]] = []
    body = _strip_cues(text)
    if not body:
        return []

    key, value = _split_fact(body)
    if not key or not value:
        return []
    if look_sensitive(value):
        return []

    # `_strip_cues` уже убрал служебные слова из начала, поэтому «Всегда НДС
    # 20 % для Ромашки» разбирается как «НДС 20 %» (значение) и «Ромашки»
    # (ключ), а не как фраза целиком.
    cleaned = clean_key(key)
    if is_correction:
        facts.append({"kind": "correction", "key": cleaned, "value": value})
    elif is_habit:
        facts.append({"kind": "habit", "key": cleaned, "value": value})
    else:
        facts.append({"kind": "alias", "key": cleaned, "value": value})
    return facts


def _strip_cues(text: str) -> str:
    """
    Убирает служебные слова из начала фразы.

    «Всегда НДС 20 % для Ромашки» → «НДС 20 % для Ромашки»;
    «Неправильно, ИНН с пробелами — без пробелов» → «ИНН с пробелами —
    без пробелов». Без этого служебное слово попадало в ключ или значение
    записи памяти, и подсказка помощнику звучала бы странно.
    """
    body = " ".join(str(text or "").split()).strip(" ,:—-")
    changed = True
    while changed and body:
        changed = False
        lowered = body.lower()
        for cue in LEADING_CUES:
            if lowered.startswith(cue):
                body = body[len(cue):].strip(" ,:—-")
                changed = True
                break
    return body


def clean_key(key: str) -> str:
    """
    Приводит ключ записи к аккуратному виду.

    Падежи здесь НЕ приводятся: «Ромашки» и «Ромашка» сводятся друг к другу
    при ПОИСКЕ — той же падежной основой, что и нечёткий поиск по базе
    (core/fuzzy.py::stem). Так память и поиск используют одно правило, и
    отдельная морфология (pymorphy2 не работает на Python 3.14) не нужна.
    """
    return " ".join(str(key or "").split()).strip(" ,:—-")


def _split_fact(text: str) -> Tuple[str, str]:
    """
    Делит фразу на «ключ» и «значение».

    Сначала пробуются шаблоны «X — это Y», «X = Y», «X означает Y». Если ни
    один не подошёл (например, «всегда НДС 20 % для Ромашки»), фраза
    разбирается по словам-маркерам: «для <кого>» — это ключ, остальное —
    значение.
    """
    body = " ".join(str(text or "").split()).strip(" .")
    if not body:
        return "", ""

    for pattern in _ALIAS_PATTERNS:
        match = pattern.match(body)
        if match:
            key = match.group("key").strip(" ,:—-")
            value = match.group("value").strip(" ,:—-")
            # «Ромашка — это ООО Ромашка»: связка «это» в значение не входит.
            if value.lower().startswith("это "):
                value = value[4:].strip()
            if key and value and key.lower() != value.lower():
                return key, value

    for marker in (" для ", " по ", " у "):
        if marker in f" {body} ":
            left, _, right = body.partition(marker)
            left, right = left.strip(), right.strip()
            if left and right:
                return right.strip(" ,:—-"), left
    return "", ""


def remember_facts(facts: Sequence[Dict[str, str]],
                   source: str = "диалог") -> List[int]:
    """
    Записывает найденные факты в память.

    :return: ID записанных строк (пусто — ничего не записали)
    """
    written: List[int] = []
    for fact in facts or ():
        kind = str(fact.get("kind") or "").strip().lower()
        record_id = _remember(kind, str(fact.get("key") or ""),
                              str(fact.get("value") or ""), source)
        if record_id is not None:
            written.append(record_id)
    return written


# ─────────────────────────────────────────────────────────────
# Оценки и настройки
# ─────────────────────────────────────────────────────────────

def feedback(rating: int, *, session_id: str = "", message_id: Optional[int] = None,
             comment: str = "") -> Optional[int]:
    """Записывает 👍/👎 на ответ помощника (1 / -1)."""
    return agent_memory.add_feedback(
        rating, session_id=session_id, message_id=message_id, comment=comment
    )


def list_records(kind: str = "", limit: int = LIST_LIMIT) -> List[Dict[str, Any]]:
    """
    Записи памяти для окна «Что Максимилиан помнит».

    К каждой записи добавляются русские подписи вида и пояснение — окно не
    должно само знать про «alias» и «correction».
    """
    records = agent_memory.load(kind=kind, limit=limit)
    for item in records:
        item["kind_title"] = kind_title(item.get("kind"))
        item["kind_hint"] = kind_hint(item.get("kind"))
    return records


def forget_all() -> int:
    """«Забыть всё»: очищает память (оценки остаются — это журнал)."""
    return agent_memory.clear()


def forget(record_id: int) -> bool:
    """Удаляет одну запись памяти."""
    return agent_memory.delete(record_id)


def stats() -> Dict[str, Any]:
    """Сколько записей помним и что за оценки получали."""
    records = list_records()
    by_kind: Dict[str, int] = {}
    for item in records:
        kind = str(item.get("kind") or "")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "total": len(records),
        "by_kind": by_kind,
        "feedback": agent_memory.feedback_stats(),
    }


__all__ = [
    "CORRECTION_CUES",
    "HABIT_KINDS",
    "KIND_HINTS",
    "KIND_TITLES",
    "LEADING_CUES",
    "LIST_LIMIT",
    "REMEMBER_CUES",
    "TOP_RELEVANT",
    "clean_key",
    "extract",
    "feedback",
    "forget",
    "forget_all",
    "kind_hint",
    "kind_title",
    "list_records",
    "look_sensitive",
    "memory_block",
    "rank",
    "recall",
    "relevance",
    "remember_alias",
    "remember_correction",
    "remember_facts",
    "remember_habit",
    "stats",
]
