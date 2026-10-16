#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Нечёткий поиск: опечатки, дефисы, двойные буквы, падежи (Часть 0, D).

Зачем это нужно
---------------
Оператор ищет так, как говорит: «Фасстранс» вместо «ФассТранс», «иванова»
вместо «Иванов», «ромашка» вместо «ООО «Ромашка»». Строгий поиск по
подстроке (`LIKE %иванова%`) такие запросы не находит, а FTS5 ищет целыми
токенами и промахивается тоже: «иванова» длиннее, чем «иванов».

Как это работает
----------------
1. Запрос нормализуется: регистр снимается, «ё» → «е», дефис, точка,
   скобка и кавычка выбрасываются ВНУТРИ слова. «Фас-Транс» и «ФасТранс»
   после этого неразличимы — а это и нужно.
2. Однобуквенные слова отбрасываются: в «Иванов И.И.» инициалы дали бы
   условие «%и%», которое совпадает почти с любой записью.
3. Для каждого слова строится набор вариантов написания: как написано,
   со снятыми удвоениями («фасстранс» → «фастранс»), с падежной основой
   («иванова» → «иванов»).
4. Варианты проверяются УРОВНЯМИ — от строгого к мягкому. Первый уровень,
   на котором что-то нашлось, и даёт ответ.

Зачем уровни
------------
Чтобы мягкое правило не портило точный результат. Пока «иванов» находится
буквально, поиск не опускается до усечения падежного окончания; пока
«фасс» находится как написано, не перебираются варианты с одной «с». Для
оператора это означает предсказуемость: чем точнее запрос, тем точнее
выдача, а «догадки» включаются только когда точного совпадения нет.

Важно: подстановочный знак ``%`` НИКОГДА не ставится между буквами одного
слова. Соблазн был («%фас%транс%» находит «ФассТранс»), но такой шаблон
теряет основное свойство поиска по подстроке — непрерывность, — и начинает
ловить мусор: «ромашка» → «%ром%ашка%» совпадает с «Шарова Марина».
Поэтому удвоения снимаются ВАРИАНТАМИ СЛОВА, а не «дырками» в шаблоне.

Модуль не знает ни про одну таблицу: он отдаёт готовое SQL-условие и
параметры к нему. Запросы выполняет вызывающий код (db/crud/*), поэтому
правила поиска живут в одном месте, а SQL — в слое базы.
"""

import re
from typing import Any, List, Optional, Sequence, Tuple

#: Внутри слова оставляем только буквы и цифры: дефис, точка, скобка,
#: кавычка и апостроф — разделители («Фас-Транс» = «ФасТранс»).
_NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)

#: Падежные и предложные окончания русских слов, которые можно отбросить.
#: Порядок важен: длинные окончания проверяются раньше коротких.
_ENDINGS: Tuple[str, ...] = (
    "ович", "евич", "овна", "евна",
    "ого", "его", "ому", "ему", "ыми", "ими",
    "ов", "ев", "ий", "ый", "ой", "ая", "яя", "ое", "ее",
    "ам", "ям", "ах", "ях", "ом", "ем", "ей",
    "а", "я", "у", "ю", "ы", "и", "е", "о",
)

#: Минимальная длина основы: «дом» усекать нельзя — получится «д».
_MIN_STEM = 4

#: Сколько вариантов одного слова перебираем (включая написание как есть).
MAX_VARIANTS = 4

#: Сколько условий LIKE допускается на один уровень поиска. Ограничение
#: защищает от разрастания SQL на длинных запросах вроде «ООО Ромашка плюс».
MAX_PATTERNS = 24

#: Сколько вариантов регистра допускается для одного слова. Три покрывают
#: живые случаи («фастранс» = «Фастранс» = «ФАСТРАНС»), остальное — уже
#: экзотика, ради которой не стоит раздувать запрос.
MAX_CASE_VARIANTS = 3


def case_variants(word: str) -> List[str]:
    """
    Варианты регистра слова — БЕЗ варианта «как написано».

    Зачем это нужно. Нормализация приводит запрос к нижнему регистру, а
    в базе наименования пишутся с заглавных. Сравнить их помог бы
    ``COLLATE NOCASE``, но он работает только для латиницы: проверено на
    SQLite 3.50.4 — ``'ООО «ФассТранс»' LIKE '%фастранс%'`` даёт 0 даже с
    NOCASE. Поэтому варианты регистра строятся явно:

      * «фастранс» → «Фастранс», «ФАСТРАНС» — обычное написание в базе;
      * «фастра» + «нс» → «Фастранс» (по заглавной на слово: «ФасТранс»).

    Порядок — от самого частого к самому редкому, поэтому первое же
    совпадение уровня обычно и срабатывает.
    """
    variants: List[str] = []
    if word:
        variants.append(word[:1].upper() + word[1:])
        upper = word.upper()
        if upper not in variants:
            variants.append(upper)
    if len(word) >= 4:
        middle = word[: len(word) // 2].capitalize() + word[len(word) // 2:].capitalize()
        if middle not in variants:
            variants.append(middle)
    return variants[:max(0, MAX_CASE_VARIANTS - 1)]


def e_yo_variants(word: str) -> List[str]:
    """
    Варианты написания через «е»/«ё»: «королев» → «королёв».

    В базе встречается и «Королёв», и «Королев» — смотря кто заводил
    запись. Запрос оператора тоже может быть любым, поэтому проверяются
    оба написания. Вариантов ровно столько, сколько букв «е» в слове
    (минимум один — с «ё» на месте первой «е»).
    """
    positions = [index for index, char in enumerate(word) if char == "е"]
    if not positions:
        return []

    variants: List[str] = []
    # Одна «ё» на слово — самый частый случай («королёв», «пётр»).
    for position in positions[:MAX_CASE_VARIANTS]:
        variants.append(word[:position] + "ё" + word[position + 1:])
    # И один вариант «все ё сразу»: «Пётр» → «Пётр», «перепёлка» → «пёрепёлка».
    if len(positions) > 1:
        chars = list(word)
        for position in positions:
            chars[position] = "ё"
        variants.append("".join(chars))
    return variants


def normalize_term(term: Any) -> str:
    """
    Нормализует поисковый запрос: регистр, «ё», пунктуация.

    «ООО «Фасс-Транс»» → «ооо фасс транс»; «Королёв» → «королев».
    Возвращается строка, где слова разделены ОДНИМ пробелом.
    """
    text = "" if term is None else str(term)
    text = text.replace("ё", "е").replace("Ё", "Е").casefold()
    # Пунктуация — разделитель; подчёркивание тоже (это не буква слова).
    text = _NON_WORD_RE.sub(" ", text)
    return " ".join(text.split())


def words(term: Any) -> List[str]:
    """
    Значимые слова запроса после нормализации.

    Слова из одной буквы отбрасываются: «Иванов И.И.» — это «Иванов», а
    не «иванов» + «и» + «и». Иначе поиск получал бы условие «%и%» и
    находил бы вообще всё.
    """
    return [word for word in normalize_term(term).split() if len(word) >= 2]


def collapse_doubles(word: str) -> str:
    """
    Снимает удвоенные буквы: «фасстранс» → «фастранс», «ооо» → «оо».

    Из каждой ПАРЫ одинаковых букв остаётся ОДНА. Пара определяется слева
    направо, поэтому тройка «ооо» даёт «оо» (первая пара схлопнута,
    третья буква осталась), а «сс» в «фасстранс» — одну «с».

    Однократное снятие, а не полное: снять все повторы сразу значило бы
    превратить «ооо» в «о» и потерять различие между «ООО» и «О».
    """
    result = []
    index = 0
    length = len(word)
    while index < length:
        char = word[index]
        if char.isalpha() and index + 1 < length and word[index + 1] == char:
            # Пара: оставляем одну букву и пропускаем вторую.
            result.append(char)
            index += 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


def stem(word: str) -> str:
    """
    Основа слова без падежного окончания: «иванова» → «иванов».

    Усечение осторожное: только если основа не короче ``_MIN_STEM``.
    Иначе «том» превратилось бы в «т», а поиск — в мусор.
    """
    if len(word) <= _MIN_STEM:
        return word
    for ending in _ENDINGS:
        if word.endswith(ending):
            base = word[: len(word) - len(ending)]
            if len(base) >= _MIN_STEM:
                return base
    return word


def spelling_variants(word: str, order: str = "spelled") -> List[str]:
    """
    Варианты написания слова — от самого точного к самому мягкому.

    :param order: "spelled" — сначала как написано (поиск по справочникам);
        "stemmed" — сначала основа без падежного окончания (поиск по ФИО).

    Перебираются оба направления ошибки в удвоениях:

      * оператор написал лишнюю: «Фасстранс» → «ФассТранс» (снятие пары);
      * в базе две, а оператор написал одну: «Фастранс» → «ФассТранс»
        (добавление пары, только для слов короче 6 букв — иначе варианты
        вроде «ффасстранс» только зашумляют выдачу).

    Одиночная буква не удваивается: «и» в «Иванов И.И.» не должна
    превращаться в «ии».
    """
    if not word:
        return []

    # ── Снятие удвоений ──
    # Индексы вторых букв в парах: «фасстранс» → [2, 6].
    pair_starts: List[int] = []
    index = 0
    length = len(word)
    while index < length:
        if index + 1 < length and word[index + 1] == word[index]:
            pair_starts.append(index + 1)
            index += 2
            continue
        index += 1

    collapsed: List[str] = [word]
    if pair_starts:
        for mask in range(1, 1 << len(pair_starts)):
            chars = list(word)
            for bit, position in enumerate(pair_starts):
                if mask & (1 << bit):
                    chars[position] = ""
            collapsed.append("".join(chars))

    # ── Добавление удвоения (обратное направление) ──
    # Только для коротких слов: «фас» → «фасс» (Фас Транс / ФассТранс),
    # «ромашка» удваивать не нужно — там ошибка не в этом.
    doubled: List[str] = []
    if 3 <= len(word) < 6:
        for position, char in enumerate(word):
            if not char.isalpha():
                continue
            if position and word[position - 1] == char:
                continue
            if position + 1 < length and word[position + 1] == char:
                continue
            doubled.append(word[: position + 1] + char + word[position + 1:])

    spelled = collapsed + doubled
    stemmed = [stem(item) for item in spelled]
    chain = stemmed + spelled if order == "stemmed" else spelled + stemmed

    result: List[str] = []
    for item in chain:
        if item and item not in result:
            result.append(item)
    return result[:MAX_VARIANTS]


#: Уровни поиска: от строгого к мягкому. Порядок — это и есть поведение
#: поиска, поэтому он объявлен явно, а не спрятан в коде.
#:
#:   exact  — запрос; слова ищутся как подстроки, все обязательны;
#:   stem   — то же, но с падежной основой («иванова» → «иванов»);
#:   glued  — склейка слов («фас транс» → «фасстранс», «ооо ромашка» →
#:            «оооромашка») и снятие удвоений.
SEARCH_LEVELS: Tuple[str, ...] = ("exact", "stem", "glued")


def _patterns_for(term: Any, level: str) -> List[List[str]]:
    """
    Наборы вариантов слов для одного уровня поиска.

    Каждый набор — это по одному варианту на каждое слово запроса. Список
    наборов проверяется по порядку, поэтому первый набор — самый точный.
    """
    base_words = words(term)
    if not base_words:
        return []

    order = "stemmed" if level == "stem" else "spelled"
    variants_per_word = [spelling_variants(word, order) for word in base_words]
    if level == "glued":
        # Склейка: у каждого слова берётся только «снятая» форма, иначе
        # вариантов становится слишком много, а пользы они не добавляют.
        variants_per_word = [
            [collapse_doubles(word), word] for word in base_words
        ]

    sets: List[List[str]] = []
    seen: set = set()

    def add(candidate: List[str]) -> None:
        """Добавляет набор, если такого ещё не было."""
        key = tuple(candidate)
        if key in seen or not all(candidate):
            return
        seen.add(key)
        sets.append(candidate)

    # Набор «всё самое точное».
    add([variants[0] for variants in variants_per_word])

    if level == "glued":
        # Склейка целиком: «фас» + «транс» → «фастранс».
        return sets

    # По одному слову за раз: остальные остаются самыми точными. Так
    # опечатка в одном слове не мешает найти запись по остальным.
    for index, variants in enumerate(variants_per_word):
        for variant in variants[1:]:
            candidate = [
                variant if position == index else items[0]
                for position, items in enumerate(variants_per_word)
            ]
            add(candidate)

    return sets


def level_conditions(term: Any, level: str, column: str) -> List[Tuple[str, List[str]]]:
    """
    SQL-условия одного уровня поиска по одной колонке (все варианты).

    Варианты регистра добавляются здесь: SQLite сравнивает кириллицу
    регистрозависимо (``COLLATE NOCASE`` помогает только латинице), а
    запрос нормализован в нижний регистр — без этих вариантов «фастранс»
    не нашёл бы «ФассТранс».

    :param term: запрос оператора (может быть с опечаткой);
    :param level: имя уровня из ``SEARCH_LEVELS``;
    :param column: имя колонки (подставляется как есть — не из ввода!);
    :return: список пар (SQL, параметры); пустой список — условий нет.
    """
    if level not in SEARCH_LEVELS:
        return []

    conditions: List[Tuple[str, List[str]]] = []
    seen: set = set()
    # Запрос приводится к «проводному» виду: без «ё» и в нижнем регистре.
    # Варианты написания строятся уже от него.
    folded = normalize_term(term)

    for candidate in _patterns_for(folded, level):
        if level == "glued":
            glued = "".join(candidate)
            if len(glued) < 3:
                continue
            patterns = [glued, *case_variants(glued), *e_yo_variants(glued)]
            values = [f"%{pattern}%" for pattern in dict.fromkeys(patterns)]
            key = tuple(values)
            if key in seen:
                continue
            seen.add(key)
            conditions.append((
                " OR ".join(f"{column} LIKE ?" for _ in values), values,
            ))
            continue

        # Варианты регистра и «е/ё» берутся для КАЖДОГО слова, а
        # соединяются через AND по словам: «фас транс» →
        # («фас»|«Фас»|«фас»…) И («транс»|«Транс»…).
        groups: List[str] = []
        values: List[str] = []
        for pattern in candidate:
            variants = list(dict.fromkeys([
                pattern, *case_variants(pattern), *e_yo_variants(pattern),
            ]))
            groups.append(
                "(" + " OR ".join(f"{column} LIKE ?" for _ in variants) + ")"
            )
            values.extend(f"%{variant}%" for variant in variants)

        key = tuple(values)
        if key in seen:
            continue
        seen.add(key)
        conditions.append((" AND ".join(groups), values))

    return conditions


def level_condition(term: Any, level: str, column: str) -> Optional[Tuple[str, List[str]]]:
    """Первое (самое точное) условие уровня — см. ``level_conditions``."""
    conditions = level_conditions(term, level, column)
    return conditions[0] if conditions else None


def search_levels(
    term: Any,
    columns: Sequence[str] = ("full_name",),
) -> List[Tuple[str, str, List[str]]]:
    """
    Все условия поиска для набора колонок, по уровням (строгий → мягкий).

    Условия одного уровня возвращаются ОДНОЙ строкой: варианты написания
    соединяются через OR, колонки — тоже. Так вызывающему коду достаточно
    выполнить один запрос на уровень.

    :param term: запрос оператора (может быть с опечаткой);
    :param columns: колонки, по которым идёт поиск (имена, не значения!);
    :return: список троек (уровень, SQL-условие, параметры). Пустой
        список означает «искать нечего» — например, запрос из одной буквы.
    """
    if not words(term):
        return []

    found: List[Tuple[str, str, List[str]]] = []
    for level in SEARCH_LEVELS:
        parts: List[str] = []
        params: List[str] = []
        for column in columns:
            for sql, values in level_conditions(term, level, column):
                parts.append(f"({sql})")
                params.extend(values)
        if parts:
            found.append((level, " OR ".join(parts), params))
    return found


__all__ = [
    "MAX_CASE_VARIANTS",
    "MAX_PATTERNS",
    "MAX_VARIANTS",
    "SEARCH_LEVELS",
    "case_variants",
    "collapse_doubles",
    "e_yo_variants",
    "level_condition",
    "level_conditions",
    "normalize_term",
    "search_levels",
    "spelling_variants",
    "stem",
    "words",
]
