#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
База знаний «Обучение» для Максимилиана (задание: система обучения).

Что здесь есть
--------------
Инструкции для оператора лежат в ``data/knowledge/tutorial/*.md`` — их пишет
агент по РЕАЛЬНОМУ коду (окна, вкладки, поля, валидаторы), поэтому текст
совпадает с тем, что оператор видит в программе. Модуль читает эти файлы,
ищет по теме и ключевым словам и возвращает ЦИТАТЫ для ответа.

Зачем отдельный модуль
----------------------
Это не «ещё один поиск по базе» и не «ещё одна зашитая справка»: знания о
том, как заполнять форму, — отдельный слой, который можно переписать без
правки кода (если интерфейс изменится, агент перепишет файлы, а не модуль).
Здесь — только чтение и поиск; инструмент ``tutorial(topic)`` в
``core/agent/tools.py`` регистрирует результат, как у правовой базы.

Формат файла
------------
Каждый файл — Markdown с короткой шапкой::

    # Заголовок инструкции

    Тема: Человекочитаемое имя темы
    Ключевые слова: слово1, слово2, слово3

    ## Раздел
    текст раздела (абзацы и списки — как удобно читать)

Правила, из которых всё следует
--------------------------------
1. **Источник обязателен.** Цитата возвращается вместе с именем темы и
   заголовком раздела: оператор должен видеть, откуда взято объяснение.
2. **«Инструкции нет» — это ответ.** Пустой результат не превращается в
   догадку: инструмент честно говорит «инструкции по такой теме нет» и
   перечисляет темы, которые есть.
3. **Ответ короткий.** Разделы обрезаются, а итог укладывается в лимит
   памяти агента (4 000 символов, см. ``core/json_text.py``): длинный ответ
   обрезался бы по дороге к модели.

Тесты не трогают файлы: путь к папке подменяется (``set_tutorial_dir``), а
найденное проверяется по составу цитат.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("core.agent.tutorial")

#: Путь к папке инструкций относительно корня проекта.
TUTORIAL_RELATIVE_PATH = os.path.join("data", "knowledge", "tutorial")

#: Лимит длины итогового текста для модели (лимит памяти агента — 4 000,
#: с запасом на служебные поля берём меньше).
MAX_ANSWER_CHARS = 3200

#: Максимальная длина одного раздела в ответе (остальное режется многоточием).
MAX_SECTION_CHARS = 900

#: Сколько разделов цитировать в ответе максимум.
MAX_SECTIONS = 4

#: Ключ-строки шапки файла (пишутся по-русски, как и комментарии проекта).
_TOPIC_KEY = "Тема:"
_KEYWORDS_KEY = "Ключевые слова:"

#: Символ-заголовок раздела внутри файла.
_SECTION_HEADING = "## "

#: Папка, которую читает модуль. Подменяется в тестах.
_tutorial_dir_override: Optional[str] = None

#: Кэш прочитанных файлов (сбрасывается при смене папки и в тестах).
_cache: Optional[List[Dict[str, Any]]] = None


# ─────────────────────────────────────────────────────────────
# Пути и чтение
# ─────────────────────────────────────────────────────────────

def project_root() -> str:
    """Корень проекта: services/agent/tools/tutorial.py → 4 уровня вверх.

    ⚠️ Этап 5: файл лежал в `core/agent/` (три уровня до корня) и переехал
    на уровень глубже. Без правки корнем считалась бы папка `services/`,
    и публичные инструкции оператора перестали бы находиться — ошибка
    тихая: список папок просто оказался бы пустым.
    """
    return os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
    )


def tutorial_dir() -> str:
    """
    Основная папка инструкций (первая в списке; для тестов и подсказок).

    Инструкции клиентских типов лежат в приватной части проекта и стоят
    в списке ПЕРВЫМИ, публичные — вторыми. Так «Экспедиторство» с его
    публичными инструкциями работает и без приватной папки, а клиентские
    темы появляются вместе с ней.
    """
    if _tutorial_dir_override is not None:
        return _tutorial_dir_override

    directories = tutorial_dirs()
    return directories[0] if directories else ""


def tutorial_dirs() -> List[str]:
    """
    Все папки инструкций по порядку приоритета.

    Приватная часть (если подключена) → публичная папка проекта. Папки,
    которых нет на диске, в список не попадают; список может быть пустым —
    тогда инструкций просто нет.
    """
    if _tutorial_dir_override is not None:
        return [_tutorial_dir_override] if _tutorial_dir_override else []

    directories: List[str] = []

    from core.private_path import private_path

    private_tutorial = private_path("data", "knowledge", "tutorial")
    if private_tutorial is not None and private_tutorial.is_dir():
        directories.append(str(private_tutorial))

    public_tutorial = os.path.join(project_root(), TUTORIAL_RELATIVE_PATH)
    if os.path.isdir(public_tutorial):
        directories.append(public_tutorial)

    return directories


def set_tutorial_dir(path: Optional[str]) -> Optional[str]:
    """
    Подставляет папку инструкций (тесты, пробы).

    :return: прежняя папка (чтобы вызывающий мог вернуть её обратно).
    """
    global _tutorial_dir_override, _cache
    previous, _tutorial_dir_override = _tutorial_dir_override, path
    _cache = None
    return previous


def reset_tutorial_cache() -> None:
    """Забывает прочитанные файлы (смена папки, пересоздание файлов)."""
    global _cache
    _cache = None


def _read_file(path: str) -> Optional[str]:
    """Читает файл UTF-8; ошибки не роняют вызов, а логируются."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        logger.warning("Инструкция не прочитана %s: %s", path, e)
        return None


def _split_keywords(raw: str) -> Tuple[str, ...]:
    """Разбирает строку ключевых слов через запятую, убирая пустое."""
    parts = [part.strip() for part in str(raw or "").split(",")]
    return tuple(part for part in parts if part)


def _parse_guide(text: str, source: str) -> Optional[Dict[str, Any]]:
    """
    Разбирает один файл инструкции в словарь.

    Ожидается шапка ``# Заголовок`` + строки «Тема: …» / «Ключевые слова: …»,
    затем разделы через ``## ``. Файл без заголовка или темы не ломает модуль:
    он просто не попадает в поиск (записывается предупреждение).
    """
    body = str(text or "").strip()
    if not body:
        return None

    lines = body.splitlines()

    # Заголовок — первая строка, начинающаяся с «# ».
    title = ""
    topic = ""
    keywords: Tuple[str, ...] = ()
    rest: List[str] = []

    for i, line in enumerate(lines):
        if not title and line.startswith("# "):
            title = line[2:].strip()
            continue
        if line.startswith(_TOPIC_KEY):
            topic = line[len(_TOPIC_KEY):].strip()
            continue
        if line.startswith(_KEYWORDS_KEY):
            keywords = _split_keywords(line[len(_KEYWORDS_KEY):])
            continue
        rest.append(line)

    if not title or not topic:
        logger.warning("Инструкция %s: нет заголовка или темы — пропущена", source)
        return None

    sections = _parse_sections("\n".join(rest))
    return {
        "source": source,
        "title": title,
        "topic": topic,
        "keywords": keywords,
        "sections": sections,
    }


def _parse_sections(body: str) -> List[Dict[str, str]]:
    """Делит текст на разделы по строкам ``## Заголовок``."""
    sections: List[Dict[str, str]] = []
    current_title: Optional[str] = None
    current_lines: List[str] = []

    for line in body.splitlines():
        if line.startswith(_SECTION_HEADING):
            if current_title is not None:
                sections.append({
                    "title": current_title,
                    "text": "\n".join(current_lines).strip(),
                })
            current_title = line[len(_SECTION_HEADING):].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_title is not None:
        sections.append({
            "title": current_title,
            "text": "\n".join(current_lines).strip(),
        })
    return [s for s in sections if s["text"]]


def load_guides() -> List[Dict[str, Any]]:
    """
    Все инструкции из папок (кэшируются до сброса).

    Папок может быть несколько (см. ``tutorial_dirs``): сначала приватная
    часть проекта — инструкции клиентских типов, затем публичная —
    «Экспедиторство» и общие темы. Одноимённые файлы читаются один раз:
    приоритет у первой папки.
    """
    global _cache
    if _cache is not None:
        return _cache

    guides: List[Dict[str, Any]] = []
    seen: set = set()

    for directory in tutorial_dirs():
        try:
            names = sorted(name for name in os.listdir(directory)
                           if name.endswith(".md"))
        except OSError as e:
            logger.warning("Папка инструкций не читается %s: %s", directory, e)
            continue

        for name in names:
            if name in seen:
                continue
            text = _read_file(os.path.join(directory, name))
            if text is None:
                continue
            guide = _parse_guide(text, name)
            if guide is not None:
                guides.append(guide)
                seen.add(name)

    _cache = guides
    return guides


def list_topics() -> List[str]:
    """Имена тем в алфавитном порядке (для обзора и подсказок)."""
    return sorted(guide["topic"] for guide in load_guides())


# ─────────────────────────────────────────────────────────────
# Поиск
# ─────────────────────────────────────────────────────────────

def _contains(haystack: str, needle: str) -> bool:
    """Вхождение без учёта регистра (пустой иглы не бывает)."""
    return needle in haystack.casefold()


#: Минимальная длина общей основы слова, при которой слова считаются одной
#: лексемой в разных формах: «перевозка»/«перевозку»/«перевозки» — «перевозк»
#: (7 букв), а случайное «как»/«какой» (3 буквы) уже не совпадает.
STEM_PREFIX = 5

#: Слова для сравнения: буквы и цифры (кириллица и латиница), без знаков.
_WORD_RE = re.compile(r"[а-яёa-z0-9]+")


def _words(text: Any) -> List[str]:
    """Токены текста в нижнем регистре (для сравнения по основе слова)."""
    return _WORD_RE.findall(str(text or "").casefold())


def _common_prefix(a: str, b: str) -> int:
    """Длина общей начальной части двух слов."""
    limit = min(len(a), len(b))
    for i in range(limit):
        if a[i] != b[i]:
            return i
    return limit


def _stem_overlap(query_words: Sequence[str],
                  target: Any) -> int:
    """
    Максимальная общая основа слова между словами запроса и текстом цели.

    Русские словоформы различаются окончаниями, а не основой, поэтому
    «перевозку» находит «перевозка». Возвращает длину самой длинной общей
    основы (0 — ничего общего).
    """
    best = 0
    for qw in query_words:
        for tw in _words(target):
            best = max(best, _common_prefix(qw, tw))
    return best


def _score(guide: Dict[str, Any], query: str) -> int:
    """
    Насколько инструкция подходит запросу (больше — лучше, 0 — не подходит).

    Порядок ценности: точное имя темы, ключевое слово, заголовок файла,
    заголовок раздела, затем простое вхождение в текст. Точному совпадению и
    вхождению доверяется больше, чем совпадению по основе слова.
    """
    q = query.casefold()
    q_words = _words(query)
    score = 0

    if guide["topic"].casefold() == q:
        score += 100
    elif _contains(guide["topic"], q) or _contains(q, guide["topic"]):
        score += 70
    elif _stem_overlap(q_words, guide["topic"]) >= STEM_PREFIX:
        score += 60

    for keyword in guide["keywords"]:
        kw = keyword.casefold()
        if kw == q:
            score += 90
        elif _contains(kw, q) or _contains(q, kw):
            score += 55
        elif _stem_overlap(q_words, keyword) >= STEM_PREFIX:
            score += 45

    if _contains(guide["title"], q):
        score += 40
    elif _stem_overlap(q_words, guide["title"]) >= STEM_PREFIX:
        score += 30

    for section in guide["sections"]:
        if _contains(section["title"], q):
            score += 25
        elif _stem_overlap(q_words, section["title"]) >= STEM_PREFIX:
            score += 20
        if _contains(section["text"], q):
            score += 8
        elif _stem_overlap(q_words, section["text"]) >= STEM_PREFIX:
            score += 6

    return score


def _find_guides(query: str) -> List[Tuple[int, Dict[str, Any]]]:
    """Инструкции, подходящие запросу, по убыванию релевантности."""
    scored = [(score, guide)
              for guide in load_guides()
              if (score := _score(guide, query)) > 0]
    scored.sort(key=lambda pair: (-pair[0], pair[1]["topic"].casefold()))
    return scored


def _truncate(text: str, limit: int) -> str:
    """Обрезает текст до ``limit`` символов с многоточием."""
    body = str(text or "").strip()
    if len(body) <= limit:
        return body
    return body[: max(0, limit - 3)].rstrip() + "…"


def _quoted_sections(guide: Dict[str, Any],
                     max_sections: int = MAX_SECTIONS) -> List[Dict[str, str]]:
    """Разделы инструкции с обрезанным текстом (для ответа модели)."""
    quoted = []
    for section in guide["sections"][:max_sections]:
        quoted.append({
            "title": section["title"],
            "text": _truncate(section["text"], MAX_SECTION_CHARS),
        })
    return quoted


def _answer_text(topic: str, title: str, sections: Sequence[Dict[str, str]],
                 max_chars: int = MAX_ANSWER_CHARS) -> str:
    """Собирает читаемый текст-цитату: тема, заголовки разделов и текст."""
    lines = [f"Инструкция «{topic}» ({title})."]
    for section in sections:
        lines.append(f"— {section['title']}: {section['text']}")
    return _truncate("\n\n".join(lines), max_chars)


def search_tutorial(query: str = "", *, max_chars: int = MAX_ANSWER_CHARS,
                    max_sections: int = MAX_SECTIONS) -> Dict[str, Any]:
    """
    Ищет инструкцию по теме или ключевым словам и возвращает цитату.

    Пустой запрос — это просьба перечислить темы: возвращается обзор без
    цитат. Не найденное — честный ответ ``found = False`` со списком тем.

    :return: словарь «topic», «title», «found», «sections», «text» и, при
        отсутствии совпадения, «topics».
    """
    query = str(query or "").strip()
    topics = list_topics()

    if not query:
        return {
            "found": False,
            "topic": "",
            "title": "",
            "sections": [],
            "topics": topics,
            "text": (
                "Доступные темы обучения: " + ", ".join(topics) + "."
                if topics else
                "Инструкций пока нет — база знаний пуста."
            ),
        }

    matches = _find_guides(query)
    if not matches:
        return {
            "found": False,
            "topic": query,
            "title": "",
            "sections": [],
            "topics": topics,
            "text": (
                f"Инструкции по теме «{query}» нет. Доступные темы: "
                + (", ".join(topics) if topics else "нет") + "."
            ),
        }

    _, guide = matches[0]
    sections = _quoted_sections(guide, max_sections=max_sections)
    return {
        "found": True,
        "topic": guide["topic"],
        "title": guide["title"],
        "keywords": list(guide["keywords"]),
        "sections": sections,
        "text": _answer_text(guide["topic"], guide["title"], sections,
                             max_chars=max_chars),
    }


def tutorial(topic: str = "", **_: Any) -> Dict[str, Any]:
    """
    Инструмент ``tutorial(topic)``: цитата из базы знаний «Обучение».

    Возвращает словарь одного вида с ``ok`` — как остальные инструменты
    (см. ``core/agent/tools.py``). Ошибки поиска не поднимаются: любой сбой
    превращается в понятный ответ с флагом ``found = False``.
    """
    try:
        result = search_tutorial(topic)
    except Exception as e:  # noqa: BLE001 — инструмент не должен ронять агента
        logger.exception("Поиск по инструкциям упал")
        return {
            "ok": False,
            "error": f"не удалось найти инструкцию: {type(e).__name__}: {e}",
        }
    return {"ok": True, "data": result}


__all__ = [
    "MAX_ANSWER_CHARS",
    "MAX_SECTION_CHARS",
    "MAX_SECTIONS",
    "TUTORIAL_RELATIVE_PATH",
    "list_topics",
    "load_guides",
    "project_root",
    "reset_tutorial_cache",
    "search_tutorial",
    "set_tutorial_dir",
    "tutorial",
    "tutorial_dir",
    "tutorial_dirs",
]
