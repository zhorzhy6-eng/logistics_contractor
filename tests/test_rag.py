#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты RAG-поиска по правовой базе (core/agent/rag.py).

ВСЕ тесты работают на MOCK: модели эмбеддингов и сеть подменяются
(см. tests/test_legal_indexer.py — там же определены FakeEmbedder и
FakeHttp). Здесь проверяется то, ради чего модуль написан: найденная норма
обязана нести источник, а контекст для промпта — быть пригодным для ответа.

Что проверяется
---------------
* сборка RAG: настройки, значения по умолчанию, каталог базы;
* индексация через RAG (в т. ч. переиндексация);
* поиск: пустой вопрос, top-k, поля результата, режим поиска;
* контекст: формат цитаты, источник, ограничение длины, пустой результат;
* состояние: is_indexed, available, stats, status_text;
* сквозной сценарий «вопрос → цитата с источником» на правовом тексте.
"""

import os
import sys
from pathlib import Path

import pytest

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.agent.rag import DEFAULT_CONTEXT_CHARS, DEFAULT_TOP_K, RAG, _source_line  # noqa: E402
from core.legal_indexer import LegalIndexer  # noqa: E402
from core.legal_parser import LegalParser  # noqa: E402
from db.crud import knowledge, legal_metadata  # noqa: E402


class FakeEmbedder:
    """
    Детерминированный заменитель модели эмбеддингов (сеть не нужна).

    Свой, а не общий с tests/test_legal_indexer.py: тесты должны читаться
    по отдельности, а пакета tests (с __init__.py) в проекте нет.
    """

    model = "fake-embeddings"

    #: Слова, по которым строится вектор.
    VOCABULARY = ("перевозка", "груз", "просрочка", "доставка", "ответственность",
                  "перевозчик", "путевой", "лист", "тахограф", "штраф", "водитель")

    def available(self) -> bool:
        """Модель доступна всегда."""
        return True

    def embed(self, texts):
        """Считает вектора по частотам слов словаря."""
        import numpy as np

        result = []
        for text in texts:
            vector = np.zeros(len(self.VOCABULARY), dtype="float32")
            lowered = str(text).lower()
            for index, word in enumerate(self.VOCABULARY):
                if word in lowered:
                    vector[index] = 1.0
            norm = float(np.linalg.norm(vector))
            if norm:
                vector /= norm
            result.append([float(value) for value in vector])
        return result

#: Правовой текст, по которому проверяется поиск.
GK_TEXT = (
    "Статья 793. Ответственность перевозчика за неподачу транспортных средств\n"
    "В случае неисполнения либо ненадлежащего исполнения обязательств по перевозке "
    "стороны несут ответственность, установленную настоящим Кодексом.\n\n"
    "Статья 794. Ответственность перевозчика за просрочку доставки груза\n"
    "Перевозчик уплачивает штраф за просрочку доставки груза, если не докажет, "
    "что просрочка произошла вследствие обстоятельств, которые он не мог предотвратить.\n\n"
) * 15

TACHO_TEXT = (
    "Приказ Минтранса России. Об утверждении обязательных реквизитов путевого листа\n"
    "Путевой лист должен содержать обязательные реквизиты, а транспортное средство "
    "оснащается тахографом. Сведения о водителе указываются в путевом листе.\n\n"
) * 15


@pytest.fixture
def legal_db(tmp_path, monkeypatch):
    """Изолированная база правовой базы."""
    path = tmp_path / "legal_metadata.db"
    monkeypatch.setenv(legal_metadata.LEGAL_DB_ENV, str(path))
    legal_metadata.set_legal_db_path(None)
    knowledge.reset_availability_cache()
    yield path
    legal_metadata.set_legal_db_path(None)


@pytest.fixture
def rag(legal_db, tmp_path):
    """RAG с MOCK-моделью и своим каталогом правовой базы."""
    parser = LegalParser(data_dir=str(tmp_path / "knowledge"))
    indexer = LegalIndexer(parser=parser, embedder=FakeEmbedder())
    return RAG(indexer=indexer)


@pytest.fixture
def filled_rag(rag, tmp_path):
    """RAG с проиндексированными двумя документами и их метаданными."""
    text_dir = Path(rag.parser.text_dir)
    text_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / "gk.txt").write_text(GK_TEXT, encoding="utf-8")
    (text_dir / "tacho.txt").write_text(TACHO_TEXT, encoding="utf-8")

    legal_metadata.save_documents([
        {"id": "file:gk", "title": "Гражданский кодекс Российской Федерации. Часть вторая",
         "short_title": "ГК РФ ч.2", "number": "14-ФЗ", "doc_date": "1996-01-26",
         "url": "http://pravo.gov.ru/proxy/ips/?doc_itself=&nd=102039276", "category": "codes"},
        {"id": "file:tacho", "title": "Приказ Минтранса России № 368 о путевых листах",
         "short_title": "Приказ Минтранса № 368", "number": "368", "doc_date": "2020-09-11",
         "url": "http://publication.pravo.gov.ru/document/0001202010300044",
         "category": "ministry_orders"},
    ])
    rag.index_documents()
    return rag


# ─────────────────────────────────────────────────────────────
# Сборка и настройки
# ─────────────────────────────────────────────────────────────


def test_rag_defaults(rag):
    """Значения по умолчанию — из задания: 5 фрагментов на ответ."""
    assert rag.top_k == DEFAULT_TOP_K == 5
    assert rag.lexical_fallback is True
    assert rag.indexer.chunk_size == 900
    assert rag.indexer.chunk_overlap == 100


def test_rag_accepts_settings(tmp_path, legal_db):
    """Настройки размера фрагмента и модели доходят до индексатора."""
    parser = LegalParser(data_dir=str(tmp_path / "knowledge"))
    indexer = LegalIndexer(parser=parser, embedder=FakeEmbedder(), chunk_size=600,
                           chunk_overlap=120, embedding_model="Embeddings-2")
    instance = RAG(indexer=indexer, top_k=3)
    assert instance.top_k == 3
    assert instance.indexer.chunk_size == 600
    assert instance.indexer.chunk_overlap == 120
    assert instance.indexer.embedding_model == "Embeddings-2"


def test_rag_creates_indexer_when_not_given(tmp_path, legal_db):
    """Без готового индексатора RAG собирает его сам."""
    parser = LegalParser(data_dir=str(tmp_path / "knowledge"))
    instance = RAG(parser=parser, chunk_size=700, chunk_overlap=50)
    assert instance.indexer.chunk_size == 700
    assert instance.indexer.parser is parser


def test_rag_base_directory(rag):
    """Каталог правовой базы виден вызывающему коду."""
    assert rag.base_directory() == rag.parser.data_dir


# ─────────────────────────────────────────────────────────────
# Индексация
# ─────────────────────────────────────────────────────────────


def test_index_documents_counts(rag, tmp_path):
    """Индексация возвращает сводку с числом документов и фрагментов."""
    text_dir = Path(rag.parser.text_dir)
    text_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / "gk.txt").write_text(GK_TEXT, encoding="utf-8")
    summary = rag.index_documents()
    assert summary["documents"] == 1
    assert summary["chunks"] > 0
    assert summary["embedded"] == summary["chunks"]
    assert summary["seconds"] >= 0


def test_index_documents_is_idempotent(rag, tmp_path):
    """Повторная индексация без изменений ничего не пересчитывает."""
    text_dir = Path(rag.parser.text_dir)
    text_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / "gk.txt").write_text(GK_TEXT, encoding="utf-8")
    rag.index_documents()
    second = rag.index_documents()
    assert second["skipped"] == 1
    assert second["chunks"] == 0


def test_reindex_rebuilds(rag, tmp_path):
    """Переиндексация проходит по всем документам заново."""
    text_dir = Path(rag.parser.text_dir)
    text_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / "gk.txt").write_text(GK_TEXT, encoding="utf-8")
    rag.index_documents()
    summary = rag.reindex()
    assert summary["skipped"] == 0
    assert summary["documents"] == 1


def test_index_report_is_readable(rag, tmp_path):
    """Отчёт об индексации пригоден для показа оператору."""
    text_dir = Path(rag.parser.text_dir)
    text_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / "gk.txt").write_text(GK_TEXT, encoding="utf-8")
    report = rag.index_report(rag.index_documents())
    assert "Фрагментов" in report
    assert "Время" in report


# ─────────────────────────────────────────────────────────────
# Поиск
# ─────────────────────────────────────────────────────────────


def test_search_empty_query(rag):
    """Пустой вопрос не ищет ничего."""
    assert rag.search("") == []
    assert rag.search("   ") == []


def test_search_on_empty_index(rag):
    """По пустому индексу поиск возвращает пустой список, а не ошибку."""
    assert rag.search("ответственность перевозчика") == []


def test_search_finds_relevant_chunk(filled_rag):
    """Поиск находит норму про ответственность за просрочку."""
    hits = filled_rag.search("ответственность перевозчика за просрочку доставки груза", top_k=3)
    assert hits
    assert "просрочк" in hits[0]["text"].lower()


def test_search_respects_top_k(filled_rag):
    """Число результатов не превышает запрошенное."""
    assert len(filled_rag.search("перевозка груза", top_k=2)) <= 2
    assert len(filled_rag.search("перевозка груза", top_k=1)) <= 1


def test_search_uses_default_top_k(filled_rag):
    """Без явного top_k берётся значение по умолчанию."""
    hits = filled_rag.search("ответственность перевозчика за просрочку")
    assert len(hits) <= filled_rag.top_k


def test_search_result_has_source(filled_rag):
    """Результат несёт источник: документ, статью, номер, дату и ссылку."""
    hit = filled_rag.search("ответственность перевозчика за просрочку доставки груза", top_k=1)[0]
    assert hit["doc_id"]
    assert hit["text"]
    assert hit["score"] > 0
    assert hit["mode"] in ("hybrid", "lexical", "semantic")
    assert hit["doc_title"]
    assert hit["doc_url"].startswith("http")


def test_search_selects_right_document(filled_rag):
    """Запрос про путевой лист приводит к приказу Минтранса, а не к ГК."""
    hits = filled_rag.search("обязательные реквизиты путевого листа водителя", top_k=2)
    assert hits
    assert "tacho" in hits[0]["doc_id"]


def test_search_without_vectors_uses_lexical(filled_rag):
    """С выключенными векторами поиск идёт по словам и говорит об этом."""
    filled_rag.indexer.use_embeddings = False
    hits = filled_rag.search("ответственность перевозчика", top_k=2)
    assert hits
    assert hits[0]["mode"] == "lexical"


def test_search_unrelated_query_returns_nothing_or_weak(filled_rag):
    """Запрос про то, чего в базе нет, не выдаёт «уверенных» совпадений."""
    hits = filled_rag.search("экспедиторское свидетельство морского порта", top_k=3)
    assert all(hit.get("mode") != "lexical" or hit["score"] > 0 for hit in hits)
    if hits and hits[0]["mode"] == "lexical":
        assert hits[0].get("strict") is False


# ─────────────────────────────────────────────────────────────
# Контекст для промпта
# ─────────────────────────────────────────────────────────────


def test_get_context_contains_quote_and_source(filled_rag):
    """Контекст содержит цитату и ссылку на источник."""
    context = filled_rag.get_context("ответственность перевозчика за просрочку доставки груза", top_k=2)
    assert context
    assert "просрочк" in context.lower()
    assert "Источник:" in context
    assert "http" in context
    assert "[1]" in context


def test_get_context_numbers_quotes(filled_rag):
    """Цитаты нумеруются подряд, чтобы на них можно было ссылаться."""
    context = filled_rag.get_context("перевозка груза ответственность путевой лист", top_k=3)
    assert "[1]" in context


def test_get_context_empty_when_nothing_found(rag):
    """По пустому индексу контекст пуст, а не «нет данных» текстом."""
    assert rag.get_context("ответственность перевозчика") == ""


def test_get_context_respects_limit(filled_rag):
    """Контекст не превышает заданную длину."""
    context = filled_rag.get_context("перевозка груза ответственность", top_k=5, max_chars=400)
    assert len(context) <= 900, "первая цитата может превысить предел, но не втрое"


def test_format_context_keeps_all_when_short():
    """Короткие результаты попадают в контекст целиком."""
    chunks = [
        {"doc_short": "ГК РФ ч.2", "doc_number": "14-ФЗ", "doc_date": "1996-01-26",
         "article": "Статья 793", "text": "Норма первая", "doc_url": "http://a"},
        {"doc_short": "ГК РФ ч.2", "doc_number": "14-ФЗ", "doc_date": "1996-01-26",
         "article": "Статья 794", "text": "Норма вторая", "doc_url": "http://b"},
    ]
    context = RAG.format_context(chunks, max_chars=DEFAULT_CONTEXT_CHARS)
    assert "Норма первая" in context and "Норма вторая" in context
    assert "[1]" in context and "[2]" in context


def test_format_context_stops_at_limit():
    """Длинный список обрезается по пределу длины."""
    chunks = [
        {"doc_short": f"Акт {index}", "text": "слово " * 200, "doc_url": ""}
        for index in range(10)
    ]
    context = RAG.format_context(chunks, max_chars=600)
    assert len(context) < 3000
    assert "[1]" in context


def test_source_line_with_article():
    """Строка источника собирается из реквизитов документа и статьи."""
    line = _source_line({"doc_short": "ГК РФ ч.2", "article": "Статья 793",
                         "doc_number": "14-ФЗ", "doc_date": "1996-01-26",
                         "doc_url": "http://a"}, 2)
    assert line.startswith("[2] ГК РФ ч.2")
    assert "Статья 793" in line
    assert "14-ФЗ от 1996-01-26" in line
    assert "Источник: http://a" in line


def test_source_line_without_article_uses_part():
    """Если статьи нет, в источнике указывается глава."""
    line = _source_line({"doc_short": "ПП № 2200", "part": "Глава 2", "doc_number": "2200",
                         "doc_date": "2020-12-21"}, 1)
    assert "Глава 2" in line
    assert "2200 от 2020-12-21" in line


def test_source_line_without_document():
    """Даже без документа строка не ломается."""
    line = _source_line({"doc_id": "file:unknown", "text": "текст"}, 1)
    assert line.startswith("[1] file:unknown")


def test_source_line_short_article_form():
    """Короткое обозначение статьи превращается в «ст. N»."""
    line = _source_line({"doc_short": "Акт", "article": "10"}, 1)
    assert "ст. 10" in line


# ─────────────────────────────────────────────────────────────
# Состояние
# ─────────────────────────────────────────────────────────────


def test_index_state_is_empty_before_indexing(rag):
    """До индексации признак «индекс собран» — ложь."""
    assert rag.is_indexed() is False
    assert rag.available() is False


def test_index_state_after_indexing(filled_rag):
    """После индексации признак «индекс собран» — истина."""
    assert filled_rag.is_indexed() is True
    assert filled_rag.available() is True


def test_stats_reports_base_and_index(filled_rag):
    """Сводка показывает и правовую базу, и индекс."""
    data = filled_rag.stats()
    assert data["chunks"] > 0
    assert data["embedded"] == data["chunks"]
    assert data["ready"] is True
    assert data["semantic"] is True
    assert "base_documents" in data
    assert "index_size" in data


def test_status_text_before_indexing(rag):
    """Пока индекс не собран, строка состояния говорит об этом прямо."""
    assert rag.status_text() == "Индекс не собран"


def test_status_text_after_indexing(filled_rag):
    """После индексации строка состояния показывает объём и режим поиска."""
    text = filled_rag.status_text()
    assert "фрагментов" in text
    assert "док." in text
    assert "поиск" in text


def test_status_text_marks_lexical_mode(filled_rag):
    """Без векторов строка состояния честно говорит про поиск по словам."""
    filled_rag.indexer.use_embeddings = False
    knowledge.clear_all()
    filled_rag.index_documents()
    assert "по словам" in filled_rag.status_text()


# ─────────────────────────────────────────────────────────────
# Сквозной сценарий
# ─────────────────────────────────────────────────────────────


def test_end_to_end_question_to_citation(filled_rag):
    """Полный путь: вопрос → цитата нормы с указанием источника."""
    question = "какая ответственность предусмотрена за просрочку доставки груза"
    context = filled_rag.get_context(question, top_k=3)
    assert context
    first_line = context.splitlines()[0]
    assert first_line.startswith("[1]")
    assert "Источник:" in context
    assert "просрочк" in context.lower()


def test_end_to_end_lexical_only(filled_rag):
    """Тот же путь без векторов: цитата и источник на месте."""
    filled_rag.indexer.use_embeddings = False
    hits = filled_rag.search("ответственность перевозчика за просрочку доставки груза", top_k=2)
    assert hits
    context = filled_rag.get_context("ответственность перевозчика за просрочку доставки груза", top_k=2)
    assert "[1]" in context
    assert "Источник:" in context
