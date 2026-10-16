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
from typing import Any, Dict, List, Optional

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
    """Забывает созданные RAG и парсер (нужно тестам и перезапуску)."""
    global _rag, _parser
    _rag = None
    _parser = None


# ─────────────────────────────────────────────────────────────
# Общие помощники
# ─────────────────────────────────────────────────────────────

def _short(value: Any, limit: int = 200) -> str:
    """Значение строкой с ограничением длины."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


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


__all__ = [
    "LEGAL_SEARCH_LIMIT",
    "LEGAL_TOP_K",
    "MAX_CHUNK_CHARS",
    "MAX_URL_CHARS",
    "download_legal",
    "get_legal_parser",
    "get_legal_rag",
    "reset_legal_clients",
    "search_legal",
    "set_legal_parser",
    "set_legal_rag",
]
