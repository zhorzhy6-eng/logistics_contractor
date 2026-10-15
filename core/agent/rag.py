#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG по правовой базе: поиск норм и контекст для ответа.

Зачем модуль
------------
Правовая база (core/legal_parser.py) и её индекс (core/legal_indexer.py)
умеют хранить и искать документы. Этот модуль отвечает на вопрос
ассистента: «дай мне нормы, на которые можно опереться, и покажи, откуда
они взяты».

Три метода — ровно то, что нужно вызывающему:
    * ``index_documents(dir)`` — проиндексировать скачанное;
    * ``search(query, top_k=5)`` — найти релевантные фрагменты;
    * ``get_context(query)`` — собрать из них текст для промпта.

Что важно в ответе
------------------
Каждый результат несёт источник: название акта, номер, дату, статью и
ссылку на портал. Без этого ответ ассистента нельзя проверить, а
юридическая ссылка без источника — не ссылка. Поэтому ``get_context``
печатает источник перед каждой цитатой, а ``search`` возвращает его
полями.

Интеграция с ассистентом
------------------------
Модуль НЕ подключён к Максимилиану: инструменты
(``core/agent/tools.py``) и промпт (``core/agent/prompts/maximilian.txt``)
ведёт другой разработчик, в этом шаге они не тронуты. Подключение —
отдельный шаг: инструмент ``search_legal(query)`` поверх ``get_context``.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from core.legal_indexer import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_EMBEDDING_MODEL,
    LegalIndexer,
    index_summary_text,
)
from core.legal_parser import LegalParser

logger = logging.getLogger("core.agent.rag")

#: Сколько символов контекста отдавать по умолчанию (примерно 1000–1500 слов —
#: столько ещё не вытесняет из ответа саму задачу).
DEFAULT_CONTEXT_CHARS = 6000

#: Сколько фрагментов подставлять по умолчанию.
DEFAULT_TOP_K = 5


class RAG:
    """
    Поиск по правовой базе с указанием источника.

    Модуль тонкий намеренно: вся тяжёлая работа (разбиение, эмбеддинги,
    хранение) лежит в core/legal_indexer.py и db/crud/knowledge.py. Здесь —
    только то, чем пользуется вызывающий код: индексация, поиск, контекст.
    """

    def __init__(self, indexer: Optional[LegalIndexer] = None,
                 parser: Optional[LegalParser] = None,
                 top_k: int = DEFAULT_TOP_K,
                 chunk_size: int = DEFAULT_CHUNK_SIZE,
                 chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
                 embedding_model: str = DEFAULT_EMBEDDING_MODEL,
                 lexical_fallback: bool = True,
                 local_fallback: bool = True,
                 progress: Optional[Callable[[str, int, int], None]] = None):
        """
        :param indexer: готовый индексатор (в тестах — со своим MOCK-эмбеддером);
        :param parser: парсер правовой базы (для путей и статистики);
        :param top_k: сколько фрагментов подставлять по умолчанию;
        :param chunk_size: размер фрагмента при индексации;
        :param chunk_overlap: перекрытие фрагментов;
        :param embedding_model: модель эмбеддингов GigaChat;
        :param lexical_fallback: разрешён ли поиск по словам, если векторов нет;
        :param local_fallback: переходить ли на локальные вектора, когда
            GigaChat-эмбеддинги недоступны (нет ключа или тариф без них);
        :param progress: обратный вызов «(этап, сделано, всего)».
        """
        self.parser = parser or (indexer.parser if indexer else None) or LegalParser()
        self.top_k = max(1, int(top_k))
        self.lexical_fallback = bool(lexical_fallback)
        self.progress = progress
        self.indexer = indexer or LegalIndexer(
            parser=self.parser,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embedding_model=embedding_model,
            local_fallback=local_fallback,
            progress=progress,
        )

    # ── индексация ──

    def index_documents(self, directory: Optional[str] = None,
                        reindex: bool = False) -> Dict[str, Any]:
        """
        Индексирует скачанные правовые документы.

        :param directory: каталог текстов (None — каталог правовой базы);
        :param reindex: True — пересобрать индекс с нуля;
        :return: сводка «documents / chunks / embedded / skipped / errors / seconds».
        """
        summary = self.indexer.index_documents(directory=directory, reindex=reindex)
        logger.info(
            "RAG: проиндексировано документов %s, фрагментов %s, с эмбеддингами %s",
            summary.get("documents"), summary.get("chunks"), summary.get("embedded"),
        )
        return summary

    def reindex(self, directory: Optional[str] = None) -> Dict[str, Any]:
        """
        Пересобирает индекс с нуля.

        :param directory: каталог текстов (None — каталог правовой базы);
        :return: сводка индексации.
        """
        return self.index_documents(directory=directory, reindex=True)

    # ── поиск ──

    def search(self, query: str, top_k: int = 0,
               min_score: float = 0.0) -> List[Dict[str, Any]]:
        """
        Ищет релевантные фрагменты правовых норм.

        :param query: вопрос («ответственность за просрочку доставки груза»);
        :param top_k: сколько фрагментов вернуть (0 — значение по умолчанию);
        :param min_score: нижняя граница оценки;
        :return: список словарей с полями ``text``, ``score``, ``mode``,
            ``article``, ``doc_title``, ``doc_number``, ``doc_date``, ``doc_url``.
        """
        term = (query or "").strip()
        if not term:
            return []
        found = self.indexer.search(term, top_k=top_k or self.top_k, min_score=min_score)
        if found or not self.lexical_fallback:
            return found
        # Векторов в индексе нет — модуль индексатора уже ушёл в словесный
        # поиск; здесь остаётся только честно пометить результат.
        return found

    def get_context(self, query: str, top_k: int = 0,
                    max_chars: int = DEFAULT_CONTEXT_CHARS) -> str:
        """
        Собирает контекст для промпта: цитаты с указанием источника.

        :param query: вопрос;
        :param top_k: сколько фрагментов включить (0 — значение по умолчанию);
        :param max_chars: предел длины контекста;
        :return: текст контекста (пустая строка — ничего не нашлось).
        """
        found = self.search(query, top_k=top_k)
        if not found:
            return ""
        return self.format_context(found, max_chars=max_chars)

    @staticmethod
    def format_context(chunks: List[Dict[str, Any]], max_chars: int = DEFAULT_CONTEXT_CHARS) -> str:
        """
        Оформляет найденные фрагменты как контекст с источниками.

        Формат каждого блока::

            [1] ГК РФ ч.2 — ст. 793 (14-ФЗ от 26.01.1996)
                Источник: http://publication.pravo.gov.ru/document/...
                «текст нормы»

        :param chunks: результаты search();
        :param max_chars: предел длины контекста;
        :return: текст контекста.
        """
        lines: List[str] = []
        used = 0
        for number, chunk in enumerate(chunks, start=1):
            header = _source_line(chunk, number)
            body = str(chunk.get("text") or "").strip()
            block = f"{header}\n{body}"
            if used + len(block) > max_chars and lines:
                break
            lines.append(block)
            used += len(block) + 2
        return "\n\n".join(lines)

    # ── состояние ──

    def is_indexed(self) -> bool:
        """Есть ли в индексе хотя бы один фрагмент."""
        try:
            return int(self.indexer.stats().get("chunks") or 0) > 0
        except Exception as e:  # noqa: BLE001 — база может быть занята
            logger.warning(f"Состояние индекса не прочитано: {e}")
            return False

    def stats(self) -> Dict[str, Any]:
        """
        Сводка по индексу и правовой базе — для раздела настроек.

        :return: словарь «документов, фрагментов, эмбеддингов, размер, база».
        """
        index = self.indexer.stats()
        try:
            base = self.parser.stats()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Сводка правовой базы не прочитана: {e}")
            base = {"documents": 0, "downloaded": 0, "text_chars": 0, "data_dir": ""}
        index["base_documents"] = base.get("documents", 0)
        index["base_downloaded"] = base.get("downloaded", 0)
        index["base_text_chars"] = base.get("text_chars", 0)
        index["data_dir"] = base.get("data_dir", "")
        index["ready"] = int(index.get("chunks") or 0) > 0
        index["semantic"] = int(index.get("embedded") or 0) > 0
        return index

    def status_text(self) -> str:
        """
        Строка состояния для интерфейса.

        :return: текст «N документов, M фрагментов, поиск по смыслу/по словам».
        """
        data = self.stats()
        if not data["chunks"]:
            return "Индекс не собран"
        mode = "по смыслу (эмбеддинги)" if data["semantic"] else "по словам (эмбеддинги недоступны)"
        return (f"{data['documents']} док. / {data['chunks']} фрагментов, "
                f"поиск {mode}, размер индекса {data.get('index_size', '—')}")

    def index_report(self, summary: Dict[str, Any]) -> str:
        """
        Текстовый отчёт об индексации (для диалога).

        :param summary: сводка index_documents();
        :return: многострочный текст.
        """
        return index_summary_text(summary)

    def available(self) -> bool:
        """
        Есть ли что искать: собран ли индекс.

        :return: True, если в индексе есть фрагменты.
        """
        return self.is_indexed()

    def base_directory(self) -> str:
        """Каталог правовой базы (откуда берутся тексты)."""
        return self.parser.data_dir


def _source_line(chunk: Dict[str, Any], number: int) -> str:
    """
    Собирает строку источника для цитаты.

    :param chunk: найденный фрагмент;
    :param number: номер цитаты в контексте;
    :return: строка вида «[1] ГК РФ ч.2 — ст. 793 (14-ФЗ от 26.01.1996)».
    """
    title = str(chunk.get("doc_short") or chunk.get("doc_title") or chunk.get("doc_id") or "документ")
    article = str(chunk.get("article") or "").strip()
    part = str(chunk.get("part") or "").strip()
    number_text = str(chunk.get("doc_number") or "").strip()
    date_text = str(chunk.get("doc_date") or "").strip()

    where = []
    if article:
        where.append(article if article.lower().startswith("статья") else f"ст. {article}")
    elif part:
        where.append(part)
    detail = f" — {', '.join(where)}" if where else ""
    requisites = f" ({number_text} от {date_text})" if number_text and date_text else \
        (f" ({number_text})" if number_text else "")
    url = str(chunk.get("doc_url") or "").strip()
    line = f"[{number}] {title}{detail}{requisites}"
    if url:
        line += f"\n    Источник: {url}"
    return line


__all__ = [
    "DEFAULT_CONTEXT_CHARS",
    "DEFAULT_TOP_K",
    "RAG",
]
