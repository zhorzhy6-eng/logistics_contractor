#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Правовая база для Максимилиана (задание, часть 3, пункт A).

Что здесь есть
--------------
Два инструмента — юридические вопросы и пополнение базы:

    search_legal(query)   — найти нормы в правовой базе (RAG): top-5
                            фрагментов с указанием источника;
    download_legal(query) — найти акт на pravo.gov.ru, скачать, разложить
                            на фрагменты и добавить в базу.

Почему отдельный модуль
-----------------------
Поиск по нормам — это не «ещё один поиск по базе»: у него другой источник
(правовая база и её RAG-индекс, а не справочники), другая цена ошибки
(выдуманная норма в договоре) и другой сетевой путь (портал опубликования).
Здесь собрано ровно это, а `core/agent/tools.py` только регистрирует
инструменты — как и с DaData (`core/agent/dadata_tools.py`).

Три правила, из которых всё следует
-----------------------------------
1. **Источник обязателен.** Каждый фрагмент отдаётся с названием акта,
   статьёй и ссылкой на портал: юридическая ссылка без источника — не
   ссылка. Ответ модели всегда содержит ``source``.
2. **«В базе нет» — это ответ.** Пустой результат не превращается в
   догадку: инструмент прямо говорит «в правовой базе нет нормы по такому
   запросу», и промпт требует сказать это оператору как есть.
3. **Ответ короткий.** Пять фрагментов по 500 символов — это ~3 000
   символов, меньше лимита памяти агента (4 000). Длинный результат
   обрезался бы по дороге к модели (см. ``core/json_text.py``).

Сеть в тестах не трогается: парсер и RAG подставляются
(``set_legal_parser``, ``set_legal_rag``).
"""

import logging
from typing import Any, Dict, List, Optional, Sequence

from core import app_paths
from core.utils import text_truncate

logger = logging.getLogger("core.agent.legal_tools")

#: Сколько фрагментов отдавать модели по умолчанию (задание: top-5).
LEGAL_TOP_K = 5

#: Сколько символов цитаты уходит модели в одном фрагменте. Фрагмент в
#: индексе — 900 символов; для ответа достаточно начала нормы, а остальное
#: оператор читает в диалоге правовой базы.
#:
#: 450 выбрано замером: пять фрагментов с реальными названиями актов и
#: ссылками дают ~3,4 КБ — с запасом меньше лимита памяти агента (4 КБ).
#: С 500 символами замер давал 3 787 символов: проходило, но без запаса на
#: длинные названия и ссылки.
MAX_CHUNK_CHARS = 450

#: Сколько символов ссылки на портал оставлять. Настоящие ссылки
#: pravo.gov.ru — около 55 символов; 120 — это уже с большим запасом.
MAX_URL_CHARS = 120

#: Сколько кандидатов показывать при поиске акта на портале.
LEGAL_SEARCH_LIMIT = 5

#: Сколько источников перечислять в коротком сообщении.
MAX_SOURCES_IN_MESSAGE = 3

#: Готовый RAG и парсер: создаются при первом обращении (дорого) и
#: переиспользуются. Подменяются в тестах.
_rag: Optional[Any] = None
_parser: Optional[Any] = None


# ─────────────────────────────────────────────────────────────
# Доступ к RAG и парсеру
# ─────────────────────────────────────────────────────────────

def set_legal_rag(rag: Optional[Any]) -> Optional[Any]:
    """
    Подставляет готовый RAG (тесты, пробы, будущий общий экземпляр).

    :return: прежний RAG (чтобы вызывающий мог вернуть его обратно).
    """
    global _rag
    previous, _rag = _rag, rag
    return previous


def get_legal_rag() -> Any:
    """RAG правовой базы (создаётся при первом обращении)."""
    global _rag
    if _rag is None:
        from services.agent.tools.rag import RAG

        _rag = RAG()
    return _rag


def set_legal_parser(parser: Optional[Any]) -> Optional[Any]:
    """Подставляет готовый парсер правовой базы (тесты)."""
    global _parser
    previous, _parser = _parser, parser
    return previous


def get_legal_parser() -> Any:
    """Парсер правовой базы (создаётся при первом обращении)."""
    global _parser
    if _parser is None:
        from core.legal_parser import LegalParser

        _parser = LegalParser()
    return _parser


def reset_legal_clients() -> None:
    """
    Забывает созданные RAG, парсер и обработчик модели.

    Нужно тестам (подменить на подставные объекты и вернуть как было) и
    перезапуску после смены ключа или настроек модели.
    """
    global _rag, _parser
    _rag = None
    _parser = None
    reset_legal_llm()


# ─────────────────────────────────────────────────────────────
# Общие помощники
# ─────────────────────────────────────────────────────────────

def _short(value: Any, limit: int = 200) -> str:
    """Значение строкой с ограничением длины (по границе слова)."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    return text_truncate.shorten(text, limit)


def _error(message: str, **extra: Any) -> Dict[str, Any]:
    """Ответ «не получилось» с пояснением для модели."""
    result: Dict[str, Any] = {"ok": False, "error": message}
    result.update(extra)
    return result


def _ok(data: Any = None, **extra: Any) -> Dict[str, Any]:
    """Ответ «получилось»."""
    result: Dict[str, Any] = {"ok": True, "data": data}
    result.update(extra)
    return result


def _source_title(chunk: Dict[str, Any]) -> str:
    """
    Название источника так, как его назовёт оператор.

    Короткое имя акта («ГК РФ ч.2», «УАТ») лучше полного: модель цитирует
    его в ответе, и длинное официальное название в ответе оператору мешает.
    """
    return _short(
        chunk.get("doc_short") or chunk.get("doc_title")
        or chunk.get("doc_id") or "документ", 80,
    )


def _article_text(chunk: Dict[str, Any]) -> str:
    """Статья (или глава) фрагмента: «ст. 793», «Глава 40»."""
    article = _short(chunk.get("article"), 60)
    if article:
        return article if article.lower().startswith("статья") else f"ст. {article}"
    return _short(chunk.get("part"), 60)


def _chunk_for_model(chunk: Dict[str, Any], number: int) -> Dict[str, Any]:
    """
    Фрагмент в том виде, в каком он уходит модели.

    Значения короткие и БЕЗ подробностей индекса (оценки близости,
    служебных полей): модели нужны норма и источник, остальное — шум.
    """
    return {
        "n": number,
        "source": _source_title(chunk),
        "article": _article_text(chunk),
        "number": _short(chunk.get("doc_number"), 40),
        "date": _short(chunk.get("doc_date"), 20),
        "url": _short(chunk.get("doc_url"), MAX_URL_CHARS),
        "text": _short(chunk.get("text"), MAX_CHUNK_CHARS),
    }


def _index_state(rag: Any) -> Dict[str, Any]:
    """Короткая сводка индекса: сколько документов и фрагментов, чем ищем."""
    try:
        data = rag.stats()
    except Exception as e:  # noqa: BLE001 — сводка не должна ронять поиск
        logger.warning("Правовая база: сводка индекса недоступна (%s)",
                       type(e).__name__)
        return {"documents": 0, "chunks": 0, "mode": "неизвестно"}
    return {
        "documents": int(data.get("documents") or 0),
        "chunks": int(data.get("chunks") or 0),
        "mode": ("по смыслу" if data.get("semantic") else "по словам"),
    }


def _sources_brief(chunks: List[Dict[str, Any]]) -> str:
    """Короткое перечисление источников для сообщения оператору."""
    parts: List[str] = []
    for chunk in chunks[:MAX_SOURCES_IN_MESSAGE]:
        title = _source_title(chunk)
        article = _article_text(chunk)
        parts.append(f"{title} — {article}" if article else title)
    return "; ".join(parts)


def _document_card(document: Any) -> Dict[str, Any]:
    """Карточка акта для ответа: что именно нашли и скачали."""
    return {
        "title": _short(getattr(document, "display_title", "")
                        or getattr(document, "title", ""), 200),
        "number": _short(getattr(document, "number", ""), 40),
        "date": _short(getattr(document, "doc_date", ""), 20),
        "type": _short(getattr(document, "doc_type", ""), 60),
        "url": _short(getattr(document, "url", "") or getattr(document, "file_url", ""), 200),
    }


# ─────────────────────────────────────────────────────────────
# 1. search_legal — поиск нормы в правовой базе
# ─────────────────────────────────────────────────────────────

def search_legal(query: str = "", top_k: int = 0, **_) -> Dict[str, Any]:
    """
    Ищет нормы в правовой базе (RAG) и отдаёт их с источниками.

    Что получает модель: до пяти фрагментов, у каждого — название акта,
    статья, реквизиты, ссылка на портал и текст нормы (начало). Этого
    достаточно, чтобы процитировать норму точно и указать источник.

    Чего инструмент НЕ делает: не отвечает на вопрос своими словами и не
    достраивает норму. Не нашлось — так и говорит: «в правовой базе нет
    нормы по такому запросу».

    :param query: вопрос оператора («ответственность за просрочку доставки
        груза», «что такое договор перевозки»);
    :param top_k: сколько фрагментов вернуть (0 — пять по умолчанию).
    """
    text = _short(query, 300)
    if not text:
        return _error("не указан вопрос: передайте query — например "
                      "«ответственность за просрочку доставки груза»")

    rag = get_legal_rag()
    try:
        indexed = bool(rag.available())
    except Exception as e:  # noqa: BLE001 — состояние базы не должно ронять поиск
        logger.warning("Правовая база: состояние не прочитано (%s)",
                       type(e).__name__)
        indexed = False

    if not indexed:
        # Это не «нормы нет», а «база не собрана» — разные вещи, и оператору
        # нужно сказать именно второе.
        return _error(
            "правовая база не собрана: индекс пуст. Откройте "
            "«Настройки → Правовая база» и нажмите «Индексировать» "
            "(или скачайте документы).",
            indexed=False,
            query=text,
        )

    try:
        found = rag.search(text, top_k=int(top_k or LEGAL_TOP_K))
    except Exception as e:  # noqa: BLE001 — внешний слой не роняет запрос
        logger.warning("Правовая база: поиск не выполнен (%s)",
                       type(e).__name__)
        return _error(f"поиск по правовой базе не выполнился ({type(e).__name__})",
                      indexed=True, query=text)

    if not found:
        return _ok(
            {"chunks": [], "index": _index_state(rag)},
            found=False, count=0, query=text,
            message=(f"в правовой базе нет нормы по запросу «{text}». "
                     f"Скажи оператору: в базе нет"),
        )

    chunks = [_chunk_for_model(chunk, number)
              for number, chunk in enumerate(found, start=1)]
    logger.info("Правовая база: поиск «%s» — фрагментов %s",
                text[:40], len(chunks))
    return _ok(
        {"chunks": chunks, "index": _index_state(rag)},
        found=True, count=len(chunks), query=text,
        source="правовая база (pravo.gov.ru)",
        message=(f"найдено фрагментов: {len(chunks)}. Источники: "
                 f"{_sources_brief(found)}"),
    )


# ─────────────────────────────────────────────────────────────
# 2. download_legal — скачать акт и добавить в базу
# ─────────────────────────────────────────────────────────────

def download_legal(query: str = "", force: bool = False,
                   index: bool = True, **_) -> Dict[str, Any]:
    """
    Находит акт на pravo.gov.ru, скачивает и добавляет в правовую базу.

    Порядок: поиск на портале (``LegalParser.search``) → выбор акта →
    скачивание (``download_document``) → индексация (только новый документ:
    индексатор пропускает неизменившиеся) → короткий ответ.

    Если акт уже в базе — ничего не перекачивается, а возвращается вопрос
    «Документ уже в базе. Обновить?»: решает оператор. Скачать заново можно
    повторным вызовом с ``force=True``.

    :param query: название или номер акта («ФЗ-259», «Правила перевозок
        грузов», «Устав автомобильного транспорта»);
    :param force: True — перекачать и переиндексировать, даже если акт есть;
    :param index: False — только скачать, без индексации (для отладки).
    """
    text = _short(query, 300)
    if not text:
        return _error("не указано, что скачать: передайте query — например "
                      "«ФЗ-259» или «Правила перевозок грузов»")

    parser = get_legal_parser()
    try:
        found = list(parser.search(text, limit=LEGAL_SEARCH_LIMIT))
    except Exception as e:  # noqa: BLE001 — портал может быть недоступен
        logger.warning("Правовая база: поиск на портале не выполнен (%s)",
                       type(e).__name__)
        return _error(
            f"не удалось найти акт на pravo.gov.ru ({type(e).__name__}). "
            f"Проверьте интернет и повторите.",
            query=text,
        )

    if not found:
        return _ok(
            {"candidates": []},
            found=False, downloaded=False, query=text,
            message=(f"на портале pravo.gov.ru ничего не нашлось по запросу "
                     f"«{text}» — уточните название или номер акта"),
        )

    document = found[0]
    already = _is_in_base(parser, document)
    if already and not force:
        return _ok(
            {"document": _document_card(document),
             "candidates": [_document_card(item) for item in found]},
            found=True, downloaded=False, already=True, needs_update=True,
            query=text,
            message=f"Документ «{_document_card(document)['title']}» уже в базе. Обновить?",
            hint=("спроси оператора; если он подтвердит — вызови "
                  "download_legal(query=…, force=true)"),
        )

    try:
        result = parser.download_document(document)
    except Exception as e:  # noqa: BLE001 — портал может отказать
        logger.warning("Правовая база: скачивание не выполнено (%s)",
                       type(e).__name__)
        return _error(
            f"скачать акт не удалось ({type(e).__name__}). Повторите позже.",
            query=text, document=_document_card(document),
        )

    card = _document_card(document)
    if getattr(result, "skipped", False) and not force:
        # Парсер сам увидел, что файл на месте и не изменился.
        return _ok(
            {"document": card},
            found=True, downloaded=False, already=True, needs_update=False,
            query=text,
            message=f"Документ «{card['title']}» уже в базе и не изменился.",
        )
    if getattr(result, "unavailable", False):
        return _ok(
            {"document": card},
            found=True, downloaded=False, unavailable=True, query=text,
            message=(f"«{card['title']}» на портале недоступен для скачивания "
                     f"(акты до 2011 года там не публикуются). Файл можно "
                     f"положить в папку правовой базы вручную."),
        )
    if not getattr(result, "ok", False):
        return _error(
            f"скачать «{card['title']}» не удалось: "
            f"{_short(getattr(result, 'error', '') or 'портал не отдал файл', 200)}",
            query=text, document=card,
        )

    chunks = 0
    if index:
        try:
            summary = get_legal_rag().index_documents()
            chunks = int((summary or {}).get("chunks") or 0)
        except Exception as e:  # noqa: BLE001 — индексация не отменяет скачивание
            logger.warning("Правовая база: индексация не выполнена (%s)",
                           type(e).__name__)
            return _ok(
                {"document": card, "text_chars": int(getattr(result, "text_chars", 0) or 0)},
                found=True, downloaded=True, indexed=False, query=text,
                message=(f"Скачал «{card['title']}», но в индекс добавить не "
                         f"удалось — соберите индекс в настройках "
                         f"(«Настройки → Правовая база»)."),
            )

    logger.info("Правовая база: скачан «%s», фрагментов добавлено %s",
                card["title"][:60], chunks)
    return _ok(
        {"document": card,
         "candidates": [_document_card(item) for item in found],
         "text_chars": int(getattr(result, "text_chars", 0) or 0),
         "chunks": chunks, "index": _index_state(get_legal_rag())},
        found=True, downloaded=True, already=False, indexed=bool(index),
        query=text,
        message=(f"Скачал «{card['title']}». Добавил в базу."
                 + (f" Фрагментов в индексе: {chunks}." if index else "")),
    )


def _is_in_base(parser: Any, document: Any) -> bool:
    """
    Есть ли акт в правовой базе (по метаданным или по скачанному файлу).

    Проверяются два признака: запись в базе метаданных с этим номером и
    датой и наличие самого оригинала на диске. Первое надёжнее (акт могли
    скачать и удалить файл), поэтому ошибка чтения базы считается «есть» —
    лишний вопрос оператору лучше повторного скачивания.
    """
    try:
        if getattr(document, "is_downloaded", False):
            return True
    except Exception:  # noqa: BLE001 — проверка файла не должна ронять поиск
        pass

    number = str(getattr(document, "number", "") or "").strip()
    doc_date = str(getattr(document, "doc_date", "") or "").strip()
    try:
        from db.crud import legal_metadata

        row = legal_metadata.find_by_number(number, doc_date) if number else None
        if row:
            return True
        doc_id = str(getattr(document, "id", "") or "")
        return bool(doc_id and legal_metadata.get_document(doc_id))
    except Exception as e:  # noqa: BLE001 — база может быть занята
        logger.warning("Правовая база: проверка «уже скачан» не удалась (%s)",
                       type(e).__name__)
        return False




# ─────────────────────────────────────────────────────────────
# Модель для объяснений (RAG + запрос к GigaChat)
# ─────────────────────────────────────────────────────────────
#: Папка промптов агента: оттуда берутся тексты юридических помощников.
#: Путь — через ресурсы сборки (core/app_paths.py): в .exe модули лежат
#: в архиве PyInstaller, и папки промптов рядом с ними на диске нет.
PROMPTS_DIR = app_paths.resource_path("services/agent/core/prompts")

#: Промпт «объясни последствия» (три пункта: норма, последствия, что делать).
EXPLAIN_PROMPT_FILE = "legal_explain.txt"

#: Промпт «юридический советник» (цитата, источник, применимость).
ADVISOR_PROMPT_FILE = "legal_advisor.txt"

#: Обязательная фраза для сложных вопросов. Требование задания: совет
#: проверки у юриста — не опция, а часть ответа.
LAWYER_NOTE = "Проверьте с юристом."

#: Признаки СЛОЖНОГО вопроса: спор, суд, деньги, ответственность. По ним
#: ответ обязательно заканчивается советом проверить у юриста.
COMPLEX_MARKERS = (
    "суд", "иск", "спор", "штраф", "убыт", "расторж", "неустойк",
    "претензи", "арест", "уголовн", "ответствен", "взыск", "пени",
)

#: Сколько норм считать «сложным» вопросом, даже если слов-маркеров нет.
COMPLEX_SOURCES = 3

#: Предел длины ответа: длинный текст в панели и в диалоге не читают.
MAX_ANSWER_CHARS = 4000

#: Готовый обработчик запросов к модели: (messages) -> текст ответа.
#: Создаётся при первом обращении и подменяется в тестах.
_llm: Optional[Any] = None


def set_legal_llm(llm: Optional[Any]) -> Optional[Any]:
    """
    Подставляет обработчик запроса к модели (тесты, пробы).

    :param llm: вызываемый объект, принимающий список сообщений и
        возвращающий текст ответа; None — «как было».
    :return: прежний обработчик (чтобы вернуть его обратно).
    """
    global _llm
    previous, _llm = _llm, llm
    return previous


def get_legal_llm() -> Any:
    """
    Обработчик запроса к модели (создаётся при первом обращении).

    Сеть и ключ живут в клиенте GigaChat, как и у остальных инструментов:
    здесь только один запрос с готовыми сообщениями.
    """
    global _llm
    if _llm is None:
        _llm = _default_llm
    return _llm


def _default_llm(messages: List[Dict[str, str]]) -> str:
    """
    Один запрос к GigaChat по текущим настройкам.

    Ключ клиент берёт из системного хранилища сам (core/secrets_store.py).
    Исключение отсюда не глотается: вызывающий код решает, показать
    оператору нормы без объяснения или честно сказать «модель недоступна».
    """
    from core.gigachat_client import GigaChatClient
    from core.settings_service import get_settings_service

    settings = get_settings_service()
    client = GigaChatClient(
        scope=settings.get_str("gigachat_scope", "GIGACHAT_API_PERS"),
        model=settings.get_str("gigachat_model", "GigaChat-3-Pro"),
        timeout=settings.get_int("gigachat_timeout", 150),
        verify_ssl=settings.get_bool("gigachat_verify_ssl", True),
        ca_bundle=settings.get_str("gigachat_ca_bundle", "") or None,
    )
    reply = client.complete_chat(messages, tools=None, temperature=0.1)
    content = reply.get("content") if isinstance(reply, dict) else None
    return str(content or "")


def reset_legal_llm() -> None:
    """Забывает созданный обработчик модели (тесты, перезапуск)."""
    global _llm
    _llm = None


# ─────────────────────────────────────────────────────────────
# Промпты и текст ответа
# ─────────────────────────────────────────────────────────────

#: Запасной промпт «объясни последствия»: если файл потерялся, объяснение
#: всё равно остаётся с источниками и без выдуманных норм.
FALLBACK_EXPLAIN_PROMPT = (
    "Ты — юридический помощник. Объясни: 1) какая норма относится к "
    "вопросу и что она требует; 2) какие последствия; 3) что делать. "
    "Цитируй нормы дословно и указывай источник. Нормы не выдумывай: если "
    "ответа в нормах нет — скажи «в правовой базе нет»."
)

#: Запасной промпт советника: те же границы, короче.
FALLBACK_ADVISOR_PROMPT = (
    "Ты — юридический помощник. Отвечай только на основе данных норм. "
    "Цитируй дословно, указывай источник. Если нормы нет — скажи «в базе "
    "нет». Не выдумывай."
)


def load_legal_prompt(name: str, fallback: str = "") -> str:
    """
    Читает текст промпта из папки промптов агента.

    Нет файла или он пуст — берётся запасной текст: юридический ответ не
    должен пропадать из-за потерянного файла.
    """
    target = PROMPTS_DIR / str(name)
    try:
        text = target.read_text(encoding="utf-8").strip()
    except OSError as error:
        logger.warning("Промпт %s не прочитан: %s", name, type(error).__name__)
        return fallback
    return text or fallback


def _sources_of(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Источники найденных норм в том виде, в каком их видит оператор.

    Каждая запись несёт цитату: без неё ответ нельзя сверить с актом.
    """
    sources: List[Dict[str, Any]] = []
    for chunk in chunks:
        sources.append({
            "source": _source_title(chunk),
            "article": _article_text(chunk),
            "number": _short(chunk.get("doc_number"), 40),
            "date": _short(chunk.get("doc_date"), 20),
            "url": _short(chunk.get("doc_url"), MAX_URL_CHARS),
            "quote": _short(chunk.get("text"), MAX_CHUNK_CHARS),
        })
    return sources


def _sources_line(sources: List[Dict[str, Any]]) -> str:
    """Строка «ГК РФ ч.2 — ст. 790; УАТ — ст. 34» для конца ответа."""
    parts: List[str] = []
    for item in sources:
        text = str(item.get("source") or "")
        article = str(item.get("article") or "")
        parts.append(f"{text} — {article}" if article else text)
    return "; ".join(part for part in parts if part)


def _norms_block(sources: List[Dict[str, Any]]) -> str:
    """
    Нормы для модели и для запасного ответа: цитаты с реквизитами.

    Формат тот же, что у RAG.get_context: модель видит норму и источник
    рядом, поэтому не может «потерять» ссылку.
    """
    lines: List[str] = []
    for number, item in enumerate(sources, start=1):
        head = f"[{number}] {item.get('source')}"
        if item.get("article"):
            head += f" — {item['article']}"
        requisites = []
        if item.get("number"):
            requisites.append(str(item["number"]))
        if item.get("date"):
            requisites.append(f"от {item['date']}")
        if requisites:
            head += f" ({' '.join(requisites)})"
        lines.append(head)
        if item.get("url"):
            lines.append(f"    Источник: {item['url']}")
        lines.append(f"    «{item.get('quote')}»")
    return "\n".join(lines)


def is_complex_question(question: str, sources: Sequence[Dict[str, Any]] = ()) -> bool:
    """
    Сложный ли вопрос: спор, деньги, ответственность, несколько норм.

    Для сложного ответа обязателен совет «Проверьте с юристом»: программа
    не заменяет юриста там, где решается судьба денег.
    """
    text = str(question or "").casefold()
    if any(marker in text for marker in COMPLEX_MARKERS):
        return True
    return len(list(sources)) >= COMPLEX_SOURCES


def _ask_llm(system: str, user: str) -> Dict[str, Any]:
    """
    Один запрос к модели: текст ответа или причина отказа.

    :return: {"answer": текст, "error": причина}; ровно одно из двух непустое.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        answer = get_legal_llm()(messages)
    except Exception as error:  # noqa: BLE001 — сеть и ключ бывают недоступны
        logger.warning("Юридический ответ: модель недоступна (%s)",
                       type(error).__name__)
        return {"answer": "", "error": type(error).__name__}

    text = str(answer or "").strip()
    if not text:
        return {"answer": "", "error": "модель вернула пустой ответ"}
    return {"answer": text, "error": ""}


def _answer_names_source(answer: str,
                         sources: List[Dict[str, Any]]) -> bool:
    """Назван ли в ответе хотя бы один источник из найденных."""
    text = answer.casefold()
    for item in sources:
        title = str(item.get("source") or "").casefold()
        if title and title in text:
            return True
    return False


def _quotes_present(answer: str, sources: List[Dict[str, Any]]) -> bool:
    """Есть ли в ответе дословная цитата хотя бы одной найденной нормы."""
    text = " ".join(answer.split())
    for item in sources:
        quote = " ".join(str(item.get("quote") or "").split())
        # Сравниваем заметный кусок: модель вправе привести норму не целиком,
        # но не вправе переписать её в кавычках.
        probe = quote[:60]
        if len(probe) >= 20 and probe in text:
            return True
    return False


def _finalize_answer(answer: str, sources: List[Dict[str, Any]],
                     needs_lawyer: bool) -> str:
    """
    Приводит ответ модели к виду, который можно показать оператору.

    Три проверки: длина, источник и (для дословных цитат — в советнике)
    наличие цитаты. Всё, чего не хватает, дописывается программой:
    юридический ответ без источника — не юридический ответ.
    """
    text = str(answer or "").strip()

    # Хвост (совет юриста и источники) собирается ДО обрезки: иначе длинный
    # ответ обрезался бы вместе с источником, а юридический ответ без
    # источника — не ответ. Обрезается только тело ответа модели.
    tail_parts: List[str] = []
    if needs_lawyer and "с юристом" not in text.casefold():
        tail_parts.append(LAWYER_NOTE)
    if sources and not _answer_names_source(text, sources):
        tail_parts.append(f"Источники: {_sources_line(sources)}")
    tail = "\n\n".join(tail_parts)

    budget = max(200, MAX_ANSWER_CHARS - len(tail) - 2)
    if len(text) > budget:
        # Режется по концу предложения: совет юриста — связный текст, и
        # оборванный на полуслове он читается как сбой программы.
        text = text_truncate.shorten(text, budget, mode=text_truncate.SENTENCE)

    return (f"{text}\n\n{tail}" if tail else text).strip()


def _fallback_answer(sources: List[Dict[str, Any]], error: str) -> str:
    """
    Ответ, когда модель недоступна: только нормы и их источники.

    Ничего не додумывается — оператор получает цитаты и сам решает. Это
    честнее, чем правдоподобный, но ничем не подтверждённый текст.
    """
    reason = f" ({error})" if error else ""
    return (
        f"Модель недоступна{reason}, поэтому объяснения нет — вот нормы "
        f"по вопросу, как они есть в правовой базе:\n\n"
        f"{_norms_block(sources)}\n\n{LAWYER_NOTE}"
    )


def _no_norms_answer(question: str) -> str:
    """Ответ «в базе нет»: пустой результат не превращается в догадку."""
    return (
        f"В правовой базе нет нормы по вопросу «{question}». "
        f"Не додумывайте ответ: скажите оператору, что в базе нет, и "
        f"предложите скачать нужный акт (инструмент download_legal). "
        f"{LAWYER_NOTE}"
    )


def _search_norms(query: str) -> Dict[str, Any]:
    """
    Ищет нормы и возвращает состояние базы.

    Общий шаг двух инструментов (объяснение и советник): иначе проверки
    «собрана ли база» и «нашлось ли что-нибудь» разъехались бы.

    :return: {"ok": bool, "error": ответ инструмента, "sources": [...],
        "chunks": [...]}.
    """
    rag = get_legal_rag()
    try:
        indexed = bool(rag.available())
    except Exception as error:  # noqa: BLE001 — состояние базы не роняет ответ
        logger.warning("Правовая база: состояние не прочитано (%s)",
                       type(error).__name__)
        indexed = False

    if not indexed:
        return {
            "ok": False,
            "error": _error(
                "правовая база не собрана: индекс пуст. Откройте "
                "«Настройки → Правовая база» и нажмите «Индексировать».",
                indexed=False, query=query,
            ),
            "sources": [], "chunks": [],
        }

    try:
        found = list(rag.search(query, top_k=LEGAL_TOP_K))
    except Exception as error:  # noqa: BLE001 — внешний слой не роняет ответ
        logger.warning("Правовая база: поиск не выполнен (%s)",
                       type(error).__name__)
        return {
            "ok": False,
            "error": _error(
                f"поиск по правовой базе не выполнился ({type(error).__name__})",
                indexed=True, query=query,
            ),
            "sources": [], "chunks": [],
        }

    return {
        "ok": True, "error": None,
        "sources": _sources_of(found), "chunks": found,
    }


# ─────────────────────────────────────────────────────────────
# 3. explain_consequences — объяснение последствий
# ─────────────────────────────────────────────────────────────

def explain_consequences(scenario: str = "", contract_type: str = "",
                         **_) -> Dict[str, Any]:
    """
    Объясняет последствия ситуации: норма, последствия, что делать.

    Ответ строится ТОЛЬКО на нормах правовой базы: они ищутся RAG-поиском,
    затем модель пересказывает их, сохраняя цитаты и источник. Если нормы
    не нашлись — «в базе нет»; если модель недоступна — цитаты как есть.
    Для сложного вопроса (спор, суд, деньги) в конце обязательная фраза
    «Проверьте с юристом.»

    :param scenario: ситуация словами оператора («срок оплаты 45 дней,
        заказчик не платит»);
    :param contract_type: тип договора (perevozka, arenda_ts…) — сужает круг
        применимых норм;
    :return: {"ok": True, "answer": …, "sources": […], "needs_lawyer": bool,
        "found": bool, "llm_used": bool}.
    """
    text = _short(scenario, 500)
    if not text:
        return _error(
            "не указана ситуация: передайте scenario — например "
            "«срок оплаты 45 дней, заказчик задерживает платёж»"
        )

    query = f"{text} {contract_type}".strip() if contract_type else text
    search = _search_norms(query)
    if not search["ok"]:
        return search["error"]

    sources = search["sources"]
    if not sources:
        logger.info("Юридическое объяснение: норм не найдено (запрос %s)",
                    text[:40])
        return _ok(
            {"answer": _no_norms_answer(text), "sources": [],
             "needs_lawyer": True, "found": False, "llm_used": False},
            found=False, count=0, query=text,
            message="в правовой базе нет нормы по этому вопросу",
        )

    needs_lawyer = is_complex_question(text, sources)
    system = load_legal_prompt(EXPLAIN_PROMPT_FILE, FALLBACK_EXPLAIN_PROMPT)
    user = (
        f"Вопрос оператора: {text}\n"
        f"Тип договора: {contract_type or 'не указан'}\n\n"
        f"НОРМЫ ИЗ ПРАВОВОЙ БАЗЫ:\n{_norms_block(sources)}"
    )
    reply = _ask_llm(system, user)
    answer = reply["answer"]
    llm_used = bool(answer)
    if not answer:
        answer = _fallback_answer(sources, reply["error"])

    answer = _finalize_answer(answer, sources, needs_lawyer)
    logger.info(
        "Юридическое объяснение: норм %s, модель %s, юрист %s",
        len(sources), "использована" if llm_used else "недоступна", needs_lawyer,
    )
    return _ok(
        {"answer": answer, "sources": sources, "needs_lawyer": needs_lawyer,
         "found": True, "llm_used": llm_used},
        found=True, count=len(sources), query=text,
        message=f"нормы найдены: {len(sources)}. Источники: "
                f"{_sources_brief(search['chunks'])}",
    )


# ─────────────────────────────────────────────────────────────
# 4. legal_advice — юридический советник
# ─────────────────────────────────────────────────────────────

def legal_advice(question: str = "", contract_type: str = "",
                 **_) -> Dict[str, Any]:
    """
    Отвечает на юридический вопрос по нормам правовой базы.

    Формат ответа задан промптом (legal_advisor.txt) и проверяется здесь:
    норма с цитатой, источник, применимость, а для сложного вопроса —
    «Проверьте с юристом.» Если модель переписала цитату или не назвала
    источник, программой дописываются нормы дословно: оператор обязан
    видеть текст, который сможет сверить с актом.

    :param question: вопрос оператора («можно ли не платить, если груз
        пришёл на день позже?»);
    :param contract_type: тип договора — для пункта «применимость»;
    :return: {"ok": True, "answer": …, "sources": […], "needs_lawyer": bool,
        "found": bool, "llm_used": bool}.
    """
    text = _short(question, 500)
    if not text:
        return _error(
            "не указан вопрос: передайте question — например "
            "«можно ли не платить, если груз пришёл на день позже?»"
        )

    search = _search_norms(text)
    if not search["ok"]:
        return search["error"]

    sources = search["sources"]
    if not sources:
        logger.info("Юридический совет: норм не найдено (запрос %s)", text[:40])
        return _ok(
            {"answer": _no_norms_answer(text), "sources": [],
             "needs_lawyer": True, "found": False, "llm_used": False},
            found=False, count=0, query=text,
            message="в правовой базе нет нормы по этому вопросу",
        )

    needs_lawyer = is_complex_question(text, sources)
    system = load_legal_prompt(ADVISOR_PROMPT_FILE, FALLBACK_ADVISOR_PROMPT)
    user = (
        f"Вопрос оператора: {text}\n"
        f"Тип договора: {contract_type or 'не указан'}\n\n"
        f"НАЙДЕННЫЕ НОРМЫ:\n{_norms_block(sources)}"
    )
    reply = _ask_llm(system, user)
    answer = reply["answer"]
    llm_used = bool(answer)
    if not answer:
        answer = _fallback_answer(sources, reply["error"])

    # Цитата дословно — главное правило советника. Если модель переписала
    # норму «своими словами», оператор получит текст акта отдельным блоком.
    if llm_used and not _quotes_present(answer, sources):
        answer = (f"{answer.rstrip()}\n\nНормы дословно:\n"
                  f"{_norms_block(sources)}")

    answer = _finalize_answer(answer, sources, needs_lawyer)
    logger.info(
        "Юридический совет: норм %s, модель %s, юрист %s",
        len(sources), "использована" if llm_used else "недоступна", needs_lawyer,
    )
    return _ok(
        {"answer": answer, "sources": sources, "needs_lawyer": needs_lawyer,
         "found": True, "llm_used": llm_used},
        found=True, count=len(sources), query=text,
        message=f"нормы найдены: {len(sources)}. Источники: "
                f"{_sources_brief(search['chunks'])}",
    )

__all__ = [
    "ADVISOR_PROMPT_FILE",
    "COMPLEX_MARKERS",
    "EXPLAIN_PROMPT_FILE",
    "LAWYER_NOTE",
    "LEGAL_SEARCH_LIMIT",
    "LEGAL_TOP_K",
    "MAX_ANSWER_CHARS",
    "MAX_CHUNK_CHARS",
    "MAX_URL_CHARS",
    "PROMPTS_DIR",
    "download_legal",
    "explain_consequences",
    "get_legal_llm",
    "get_legal_parser",
    "get_legal_rag",
    "is_complex_question",
    "legal_advice",
    "load_legal_prompt",
    "reset_legal_clients",
    "reset_legal_llm",
    "search_legal",
    "set_legal_llm",
    "set_legal_parser",
    "set_legal_rag",
]
