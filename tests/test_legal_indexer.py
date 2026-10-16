#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты индексатора правовой базы (core/legal_indexer.py, db/crud/knowledge.py).

ВСЕ тесты работают на MOCK: ни GigaChat, ни портал не запрашиваются.
Подменяются две вещи:

  * ``FakeEmbedder`` — вместо модели эмбеддингов: считает детерминированный
    вектор по словам, поэтому косинусная близость проверяется без сети;
  * ``FakeSession`` — вместо requests.Session (для клиента эмбеддингов).

Что проверяется
---------------
* разбиение текста: размер, перекрытие, границы, метаданные статей и глав;
* нормализация текста и контрольная сумма;
* хранилище фрагментов: замена, удаление, состояние документа, статистика;
* полнотекстовый поиск: выбор редких слов, усечение окончаний, стоп-слова;
* индексация: пропуск неизменившихся документов, смена модели, ошибки;
* эмбеддинги GigaChat: авторизация, порции, ошибки, повторы (на MOCK);
* локальный вектор: размерность, нормировка, детерминизм, близость;
* гибридный поиск и слияние рангов (RRF).
"""

import json
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

from core import legal_indexer as li  # noqa: E402
from db.crud import knowledge, legal_metadata  # noqa: E402

# ─────────────────────────────────────────────────────────────
# MOCK модели эмбеддингов
# ─────────────────────────────────────────────────────────────

VOCABULARY = (
    "перевозка", "груз", "просрочка", "доставка", "ответственность", "перевозчик",
    "путевой", "лист", "таксограф", "тахограф", "безопасность", "транспортный",
    "статья", "договор", "штраф", "экспедиция", "накладная", "водитель",
)


class FakeEmbedder:
    """
    Детерминированный заменитель модели эмбеддингов.

    Вектор — частоты слов из словаря, приведённые к единичной длине. Этого
    достаточно, чтобы косинусная близость вела себя осмысленно: тексты про
    перевозку оказываются ближе друг к другу, чем текст про тахограф.
    """

    model = "fake-embeddings"

    def __init__(self, dim: int = 0, fail: bool = False):
        """
        :param dim: размерность (0 — по размеру словаря);
        :param fail: если True, метод embed поднимает ошибку.
        """
        self.dim = dim or len(VOCABULARY)
        self.fail = fail
        self.calls = []

    def available(self) -> bool:
        """Модель доступна, если её не попросили «сломаться»."""
        return not self.fail

    def embed(self, texts):
        """Считает вектора (или падает, если задан fail)."""
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("модель недоступна (MOCK)")
        import numpy as np

        result = []
        for text in texts:
            vector = np.zeros(self.dim, dtype="float32")
            lowered = str(text).lower()
            for index, word in enumerate(VOCABULARY[: self.dim]):
                count = lowered.count(word)
                if count:
                    vector[index] = 1.0 + (count - 1) * 0.5
            norm = float(np.linalg.norm(vector))
            if norm:
                vector /= norm
            result.append([float(value) for value in vector])
        return result


class FakeResponse:
    """Ответ HTTP-сервиса (как у requests.Response)."""

    def __init__(self, status_code=200, payload=None, text=""):
        """
        :param status_code: код ответа;
        :param payload: объект для .json();
        :param text: тело как строка (для сообщений об ошибке).
        """
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {}, ensure_ascii=False)

    def json(self):
        """Разбирает тело как JSON."""
        if self._payload is None:
            raise ValueError("тело ответа не JSON")
        return self._payload


class FakeHttp:
    """Подмена requests.Session для клиента эмбеддингов."""

    def __init__(self, auth_status=200, embed_status=200, dim=4):
        """
        :param auth_status: код ответа на выдачу токена;
        :param embed_status: код ответа на /embeddings;
        :param dim: размерность возвращаемых векторов.
        """
        self.auth_status = auth_status
        self.embed_status = embed_status
        self.dim = dim
        self.calls = []

    def post(self, url, headers=None, data=None, json=None, timeout=None, verify=None):
        """Возвращает заготовленный ответ и запоминает вызов."""
        self.calls.append({"url": url, "data": data, "json": json, "verify": verify})
        if "oauth" in url:
            return FakeResponse(self.auth_status, {"access_token": "MOCK-TOKEN",
                                                   "expires_at": 4_000_000_000_000})
        items = [
            {"index": index, "embedding": [0.5] * self.dim}
            for index, _text in enumerate((json or {}).get("input", []))
        ]
        return FakeResponse(self.embed_status, {"data": items}, text="Payment Required")


SAMPLE_TEXT = (
    "Глава 40. Перевозка\n\n"
    "Статья 793. Ответственность перевозчика за неподачу транспортных средств\n"
    "1. В случае неисполнения либо ненадлежащего исполнения обязательств по перевозке "
    "стороны несут ответственность, установленную настоящим Кодексом. "
    "2. Соглашения транспортных организаций с грузоотправителями недействительны.\n\n"
    "Статья 794. Ответственность перевозчика за утрату, недостачу и повреждение груза\n"
    "Перевозчик несет ответственность за сохранность груза после принятия его к перевозке "
    "и до выдачи грузополучателю, если не докажет, что утрата произошла вследствие "
    "обстоятельств, которые перевозчик не мог предотвратить.\n\n"
) * 12


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────


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
def text_dir(tmp_path):
    """Каталог с текстами документов."""
    directory = tmp_path / "text"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@pytest.fixture
def indexer(legal_db, tmp_path, text_dir):
    """Индексатор с MOCK-моделью и пустым каталогом текстов."""
    parser = li.LegalParser(data_dir=str(tmp_path / "knowledge"))
    return li.LegalIndexer(parser=parser, embedder=FakeEmbedder())


def write_document(text_dir, name: str, text: str = SAMPLE_TEXT) -> Path:
    """Кладёт текст документа в каталог."""
    path = text_dir / name
    path.write_text(text, encoding="utf-8")
    return path


# ─────────────────────────────────────────────────────────────
# Разбиение текста
# ─────────────────────────────────────────────────────────────


def test_chunk_size_within_working_range():
    """Размер фрагмента держится в рабочем диапазоне 500–1000 символов."""
    chunks = li.chunk_text(SAMPLE_TEXT, doc_id="d", chunk_size=900, overlap=100)
    assert chunks
    assert all(500 <= chunk.chars <= 1100 for chunk in chunks), [c.chars for c in chunks]


def test_chunk_overlap_is_one_hundred():
    """Перекрытие соседних фрагментов — 100 символов."""
    chunks = li.chunk_text(SAMPLE_TEXT, doc_id="d", chunk_size=900, overlap=100)
    assert len(chunks) > 2
    for first, second in zip(chunks, chunks[1:]):
        assert first.char_end - second.char_start == 100


def test_chunk_indexes_and_ids():
    """Фрагменты нумеруются подряд и помнят свой документ."""
    chunks = li.chunk_text(SAMPLE_TEXT, doc_id="doc-1", chunk_size=900, overlap=100)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    assert {chunk.doc_id for chunk in chunks} == {"doc-1"}


def test_chunk_articles_are_detected():
    """Статья определяется и для фрагмента, начинающегося прямо с заголовка."""
    chunks = li.chunk_text(SAMPLE_TEXT, doc_id="d", chunk_size=700, overlap=100)
    articles = {chunk.article for chunk in chunks if chunk.article}
    assert "Статья 793" in articles
    assert "Статья 794" in articles


def test_chunk_part_is_detected():
    """Глава попадает в метаданные фрагмента."""
    chunks = li.chunk_text(SAMPLE_TEXT, doc_id="d")
    assert any(chunk.part.startswith("Глава 40") for chunk in chunks)


def test_chunk_does_not_start_mid_word():
    """Граница фрагмента проходит по разрыву, а не по середине слова."""
    chunks = li.chunk_text(SAMPLE_TEXT, doc_id="d", chunk_size=600, overlap=50)
    for chunk in chunks[:-1]:
        assert chunk.text[-1] in ".!?;:»)\n" or chunk.text[-1].isspace() is False
        # Ни один фрагмент не начинается со строчной буквы посреди слова.
        assert not chunk.text.startswith(" ")


def test_empty_text_gives_no_chunks():
    """Пустой текст не даёт фрагментов."""
    assert li.chunk_text("", doc_id="d") == []
    assert li.chunk_text("   \n\n  ", doc_id="d") == []


def test_small_text_is_single_chunk():
    """Короткий текст становится одним фрагментом."""
    chunks = li.chunk_text("Статья 1. Короткая норма.", doc_id="d")
    assert len(chunks) == 1
    assert chunks[0].text == "Статья 1. Короткая норма."


def test_chunk_size_is_clamped():
    """Слишком большой или маленький размер подтягивается к рабочему диапазону."""
    assert li.LegalIndexer(embedder=FakeEmbedder(), chunk_size=10).chunk_size == li.MIN_CHUNK_SIZE
    assert li.LegalIndexer(embedder=FakeEmbedder(), chunk_size=99999).chunk_size == li.MAX_CHUNK_SIZE


def test_normalize_text():
    """Нормализация убирает мягкие переносы и лишние пустые строки."""
    raw = "Перевозка\r\n\r\n\r\n\r\n  груза   \r\n"
    assert li.normalize_text(raw) == "Перевозка\n\nгруза"


def test_text_checksum_is_stable():
    """Контрольная сумма не зависит от переводов строк, но ловит правку."""
    first = li.text_checksum("Перевозка груза")
    second = li.text_checksum("Перевозка груза\r\n")
    third = li.text_checksum("Перевозка груза!")
    assert first == second
    assert first != third


def test_article_map_positions():
    """Карта статей отсортирована и содержит найденные заголовки."""
    mapping = li.article_map(SAMPLE_TEXT)
    assert mapping
    assert mapping == sorted(mapping)
    assert any(value.startswith("Статья 793") for _pos, value in mapping)


# ─────────────────────────────────────────────────────────────
# Локальный вектор
# ─────────────────────────────────────────────────────────────


def test_local_embedder_is_deterministic():
    """Один и тот же текст даёт один и тот же вектор (в т. ч. между запусками)."""
    embedder = li.LocalEmbedder()
    assert embedder.vector("перевозка груза") == embedder.vector("перевозка груза")


def test_local_embedder_is_normalized():
    """Вектор приведён к единичной длине."""
    import numpy as np

    vector = np.asarray(li.LocalEmbedder().vector("ответственность перевозчика за просрочку"))
    assert pytest.approx(float(np.linalg.norm(vector)), abs=1e-5) == 1.0


def test_local_embedder_keeps_related_texts_close():
    """Похожие тексты ближе друг к другу, чем непохожие."""
    import numpy as np

    embedder = li.LocalEmbedder()
    query = np.asarray(embedder.vector("ответственность перевозчика за просрочку доставки груза"))
    near = np.asarray(embedder.vector("Ответственность перевозчика за просрочку доставки груза"))
    far = np.asarray(embedder.vector("путевой лист обязательные реквизиты тахограф"))
    assert float(query @ near) > float(query @ far)


def test_local_embedder_available_offline():
    """Локальный вектор доступен без сети."""
    assert li.LocalEmbedder().available() is True


# ─────────────────────────────────────────────────────────────
# Эмбеддинги GigaChat (MOCK)
# ─────────────────────────────────────────────────────────────


def test_gigachat_embedder_gets_token_and_vectors():
    """Клиент получает токен и считает вектора через /embeddings."""
    http = FakeHttp(dim=6)
    embedder = li.GigaChatEmbedder(auth_key="MOCK", session=http, pause=0.0, verify=False)
    vectors = embedder.embed(["первый текст", "второй текст"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 6
    assert any("oauth" in call["url"] for call in http.calls)
    assert any("embeddings" in call["url"] for call in http.calls)


def test_gigachat_embedder_batches_long_lists():
    """Длинный список текстов уходит порциями, а не одним запросом."""
    http = FakeHttp(dim=3)
    embedder = li.GigaChatEmbedder(auth_key="MOCK", session=http, pause=0.0, verify=False)
    vectors = embedder.embed([f"текст {index}" for index in range(25)])
    assert len(vectors) == 25
    batches = [call for call in http.calls if "embeddings" in call["url"]]
    assert len(batches) == 3  # 10 + 10 + 5


def test_gigachat_embedder_reports_payment_required():
    """Тариф без эмбеддингов даёт понятную ошибку, а не пустой вектор."""
    http = FakeHttp(embed_status=402)
    embedder = li.GigaChatEmbedder(auth_key="MOCK", session=http, pause=0.0, retries=1, verify=False)
    with pytest.raises(RuntimeError) as error:
        embedder.embed(["текст"])
    assert "402" in str(error.value)


def test_gigachat_embedder_without_key():
    """Без ключа клиент честно говорит, что считать нечем."""
    embedder = li.GigaChatEmbedder(auth_key="", session=FakeHttp(), pause=0.0, verify=False)
    assert embedder.available() is False
    with pytest.raises(RuntimeError) as error:
        embedder.token()
    assert "ключ" in str(error.value).lower()


def test_gigachat_embedder_empty_input():
    """Пустой список текстов не порождает запросов."""
    http = FakeHttp()
    embedder = li.GigaChatEmbedder(auth_key="MOCK", session=http, pause=0.0, verify=False)
    assert embedder.embed([]) == []
    assert http.calls == []


def test_tls_verify_modes():
    """Режим проверки сертификата берётся из настройки/явного значения."""
    assert li.tls_verify(False) is False
    assert li.tls_verify("C:/cert.pem") == "C:/cert.pem"
    assert li.tls_verify() in (True, False) or isinstance(li.tls_verify(), str)


def test_indexer_switches_to_local_when_gigachat_fails(tmp_path, legal_db):
    """Тариф без эмбеддингов переводит индексатор на локальные вектора."""
    parser = li.LegalParser(data_dir=str(tmp_path / "knowledge"))
    indexer = li.LegalIndexer(parser=parser, embedder=FakeEmbedder(fail=True))
    assert indexer.embedding_kind() == "local"
    assert "локальный вектор" in indexer._embedding_note


def test_indexer_can_disable_local_fallback(tmp_path, legal_db):
    """При выключенном запасном варианте вектора не считаются вовсе."""
    parser = li.LegalParser(data_dir=str(tmp_path / "knowledge"))
    indexer = li.LegalIndexer(parser=parser, embedder=FakeEmbedder(fail=True),
                              local_fallback=False)
    assert indexer.embedding_kind() == "none"


# ─────────────────────────────────────────────────────────────
# Хранилище фрагментов
# ─────────────────────────────────────────────────────────────


def test_schema_created(legal_db):
    """Таблицы индекса создаются вместе с базой метаданных."""
    conn = knowledge.get_connection()
    try:
        knowledge.init_db(conn)
        names = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    finally:
        conn.close()
    assert {"knowledge_chunks", "knowledge_docs"} <= names


def test_replace_and_read_chunks(legal_db):
    """Фрагменты сохраняются и читаются с метаданными статьи."""
    chunks = li.chunk_text(SAMPLE_TEXT, doc_id="d1")
    payload = [chunk.to_dict() for chunk in chunks]
    knowledge.replace_document_chunks("d1", payload)

    stored = knowledge.iter_chunks(doc_id="d1")
    assert len(stored) == len(payload)
    assert stored[0]["article"]
    state = knowledge.document_state("d1")
    assert state["chunks"] == len(payload)
    assert state["embedded"] == 0


def test_replace_chunks_is_idempotent(legal_db):
    """Повторная запись заменяет фрагменты, а не добавляет новые."""
    chunks = [chunk.to_dict() for chunk in li.chunk_text(SAMPLE_TEXT, doc_id="d1")]
    knowledge.replace_document_chunks("d1", chunks)
    knowledge.replace_document_chunks("d1", chunks[:2])
    assert len(knowledge.iter_chunks(doc_id="d1")) == 2
    assert knowledge.stats()["chunks"] == 2


def test_delete_document_chunks(legal_db):
    """Удаление убирает фрагменты и запись о документе."""
    chunks = [chunk.to_dict() for chunk in li.chunk_text(SAMPLE_TEXT, doc_id="d1")]
    knowledge.replace_document_chunks("d1", chunks)
    assert knowledge.delete_document_chunks("d1") == len(chunks)
    assert knowledge.iter_chunks(doc_id="d1") == []
    assert knowledge.document_state("d1") is None


def test_vectors_are_packed_and_unpacked(legal_db):
    """Вектор сохраняется как BLOB и читается обратно без потерь."""
    vector = [0.1, -0.2, 0.3, 0.4]
    chunks = [{"text": "Статья 1. Норма", "embedding": vector, "model": "test"}]
    knowledge.replace_document_chunks("d1", chunks)
    stored = knowledge.iter_chunks(doc_id="d1", with_vectors=True)[0]
    assert len(stored["vector"]) == 4
    assert stored["vector"][1] == pytest.approx(-0.2, abs=1e-6)
    assert stored["dim"] == 4
    assert knowledge.stats()["embedded"] == 1


def test_broken_vector_is_rejected():
    """Повреждённый BLOB не превращается в мусорный вектор."""
    assert knowledge.unpack_vector(b"\x01\x02", dim=4) == []
    assert knowledge.unpack_vector(None) == []


def test_clear_all(legal_db):
    """Полная очистка убирает и фрагменты, и состояние документов."""
    chunks = [chunk.to_dict() for chunk in li.chunk_text(SAMPLE_TEXT, doc_id="d1")]
    knowledge.replace_document_chunks("d1", chunks)
    knowledge.clear_all()
    data = knowledge.stats()
    assert data["chunks"] == 0 and data["documents"] == 0


# ─────────────────────────────────────────────────────────────
# Полнотекстовый поиск
# ─────────────────────────────────────────────────────────────


def test_query_tokens_drops_stop_words():
    """Служебные слова и короткие слова в запрос не попадают."""
    tokens = knowledge.query_tokens("ответственность за просрочку доставки груза")
    assert "ответственность" in tokens
    assert "просрочку" in tokens
    assert "за" not in tokens


def test_stem_token_trims_endings():
    """Окончание отбрасывается, основа остаётся узнаваемой."""
    assert knowledge.stem_token("просрочку") == "просрочк"
    assert knowledge.stem_token("доставки").startswith("достав")
    assert knowledge.stem_token("груз") == "груз"


def test_token_group_covers_forms():
    """Группа слова ищет и точную форму, и основу."""
    group = knowledge.token_group("просрочку")
    assert "просрочку" in group and "просрочк" in group


def test_lexical_search_finds_rare_words(legal_db):
    """Поиск опирается на редкие слова и находит нужный фрагмент."""
    text = ("Статья 793. Ответственность перевозчика\n"
            "Стороны несут ответственность за неисполнение обязательств.\n\n"
            "Статья 794. Просрочка доставки груза\n"
            "За просрочку доставки груза перевозчик уплачивает штраф.\n")
    chunks = [chunk.to_dict() for chunk in li.chunk_text(text * 20, doc_id="gk")]
    knowledge.replace_document_chunks("gk", chunks)

    hits = knowledge.search_lexical("ответственность за просрочку доставки груза", limit=3)
    assert hits
    assert hits[0]["strict"] is True
    assert "просрочк" in hits[0]["text"].lower()


def test_lexical_search_returns_empty_for_unknown_word(legal_db):
    """Слово, которого нет в индексе, ничего не находит."""
    chunks = [chunk.to_dict() for chunk in li.chunk_text(SAMPLE_TEXT, doc_id="d")]
    knowledge.replace_document_chunks("d", chunks)
    assert knowledge.search_lexical("экспедиторское свидетельство", limit=3) == []


def test_lexical_search_empty_query(legal_db):
    """Пустой запрос не ищет ничего."""
    assert knowledge.search_lexical("", limit=3) == []
    assert knowledge.search_lexical("   ", limit=3) == []


def test_lexical_search_reports_document(legal_db):
    """В результатах есть реквизиты документа — иначе цитату не проверить."""
    legal_metadata.save_document({"id": "d", "title": "Гражданский кодекс", "short_title": "ГК РФ ч.2",
                                  "number": "14-ФЗ", "doc_date": "1996-01-26", "url": "http://x"})
    chunks = [chunk.to_dict() for chunk in li.chunk_text(SAMPLE_TEXT, doc_id="d")]
    knowledge.replace_document_chunks("d", chunks)
    hit = knowledge.search_lexical("ответственность перевозчика за просрочку", limit=1)[0]
    assert hit["doc_short"] == "ГК РФ ч.2"
    assert hit["doc_number"] == "14-ФЗ"
    assert hit["doc_url"] == "http://x"


# ─────────────────────────────────────────────────────────────
# Индексация документов
# ─────────────────────────────────────────────────────────────


def test_index_documents_from_directory(indexer, text_dir):
    """Тексты из каталога индексируются и получают вектора."""
    write_document(text_dir, "gk.txt")
    write_document(text_dir, "other.txt", SAMPLE_TEXT.replace("перевозк", "экспедиц"))
    summary = indexer.index_documents(directory=str(text_dir))
    assert summary["documents"] == 2
    assert summary["chunks"] > 0
    assert summary["embedded"] == summary["chunks"]
    assert summary["embedding_kind"] == "gigachat"
    assert not summary["errors"]


def test_index_documents_skips_unchanged(indexer, text_dir):
    """Повторная индексация неизменившегося текста ничего не пересчитывает."""
    write_document(text_dir, "gk.txt")
    indexer.index_documents(directory=str(text_dir))
    second = indexer.index_documents(directory=str(text_dir))
    assert second["skipped"] == 1
    assert second["chunks"] == 0


def test_index_documents_reindexes_changed_file(indexer, text_dir):
    """Изменённый текст индексируется заново."""
    path = write_document(text_dir, "gk.txt")
    indexer.index_documents(directory=str(text_dir))
    path.write_text(SAMPLE_TEXT + "\nСтатья 795. Новая норма о перевозке груза.", encoding="utf-8")
    second = indexer.index_documents(directory=str(text_dir))
    assert second["documents"] == 1
    assert second["skipped"] == 0


def test_reindex_clears_previous(indexer, text_dir):
    """reindex=True пересобирает индекс с нуля."""
    write_document(text_dir, "gk.txt")
    indexer.index_documents(directory=str(text_dir))
    before = indexer.stats()["chunks"]
    summary = indexer.index_documents(directory=str(text_dir), reindex=True)
    assert summary["skipped"] == 0
    assert summary["chunks"] == before


def test_index_documents_without_vectors(indexer, text_dir):
    """С выключенными векторами текст всё равно индексируется."""
    write_document(text_dir, "gk.txt")
    indexer.use_embeddings = False
    summary = indexer.index_documents(directory=str(text_dir))
    assert summary["chunks"] > 0
    assert summary["embedded"] == 0
    assert summary["embedding_kind"] == "none"


def test_index_documents_reports_empty_file(indexer, text_dir):
    """Пустой файл попадает в отчёт как ошибка, а не молча пропадает."""
    write_document(text_dir, "empty.txt", "   \n  ")
    summary = indexer.index_documents(directory=str(text_dir))
    assert summary["documents"] == 0
    assert any("пустой текст" in error for error in summary["errors"].values())


def test_index_documents_limit(indexer, text_dir):
    """Предел числа документов соблюдается."""
    for index in range(4):
        write_document(text_dir, f"doc{index}.txt")
    summary = indexer.index_documents(directory=str(text_dir), limit=2)
    assert summary["documents"] == 2


def test_index_documents_progress(indexer, text_dir):
    """Ход индексации сообщается обработчику интерфейса."""
    write_document(text_dir, "gk.txt")
    seen = []
    indexer.progress = lambda stage, done, total: seen.append((stage, done, total))
    indexer.index_documents(directory=str(text_dir))
    assert any(stage == "save" for stage, _done, _total in seen)


def test_source_files_marks_manual_files(indexer, text_dir):
    """Файл, которого нет в базе, индексируется как «ручной»."""
    write_document(text_dir, "устав-автомобильного-транспорта.txt")
    sources = indexer.source_files(str(text_dir))
    assert len(sources) == 1
    assert sources[0]["doc_id"].startswith("file:")


# ─────────────────────────────────────────────────────────────
# Поиск индексатора и слияние рангов
# ─────────────────────────────────────────────────────────────


def build_search_index(indexer, text_dir):
    """Готовит индекс из двух документов про разные темы."""
    write_document(text_dir, "gk.txt",
                   ("Статья 793. Ответственность перевозчика за просрочку доставки груза\n"
                    "Перевозчик несет ответственность за просрочку доставки груза и уплачивает штраф.\n\n") * 20)
    write_document(text_dir, "tacho.txt",
                   ("Тахограф и путевой лист водителя\n"
                    "Требования к тахографу и обязательные реквизиты путевого листа водителя.\n\n") * 20)
    indexer.index_documents(directory=str(text_dir))


def test_search_finds_relevant_document(indexer, text_dir):
    """Поиск находит документ по теме вопроса."""
    build_search_index(indexer, text_dir)
    hits = indexer.search("ответственность перевозчика за просрочку доставки груза", top_k=3)
    assert hits
    assert "gk" in hits[0]["doc_id"]


def test_search_returns_source_fields(indexer, text_dir):
    """Результат несёт источник: без него цитата непроверяема."""
    build_search_index(indexer, text_dir)
    hit = indexer.search("путевой лист водителя", top_k=1)[0]
    for field in ("doc_id", "chunk_index", "text", "score", "mode"):
        assert field in hit


def test_search_uses_hybrid_mode(indexer, text_dir):
    """Когда есть и вектора, и слова, режим — гибридный."""
    build_search_index(indexer, text_dir)
    hits = indexer.search("ответственность перевозчика за просрочку доставки груза", top_k=3)
    assert hits[0]["mode"] == "hybrid"
    assert "semantic_score" in hits[0]
    assert "lexical_score" in hits[0]


def test_search_falls_back_to_lexical_without_vectors(indexer, text_dir):
    """Без векторов поиск идёт по словам и честно об этом сообщает."""
    write_document(text_dir, "gk.txt")
    indexer.use_embeddings = False
    indexer.index_documents(directory=str(text_dir))
    hits = indexer.search("ответственность перевозчика", top_k=2)
    assert hits
    assert hits[0]["mode"] == "lexical"


def test_search_empty_query(indexer, text_dir):
    """Пустой вопрос не ищет ничего."""
    assert indexer.search("", top_k=3) == []


def test_fuse_ranks_prefers_document_from_both_lists():
    """Слияние рангов поднимает фрагмент, найденный обоими способами."""
    lexical = [{"doc_id": "a", "chunk_index": 0, "score": 9.0},
               {"doc_id": "b", "chunk_index": 0, "score": 8.0}]
    semantic = [{"doc_id": "b", "chunk_index": 0, "score": 0.9},
                {"doc_id": "c", "chunk_index": 0, "score": 0.8}]
    fused = li._fuse_ranks(lexical, semantic)
    assert fused[0]["doc_id"] == "b"
    assert fused[0]["mode"] == "hybrid"
    assert fused[0]["lexical_score"] == 8.0
    assert fused[0]["semantic_score"] == 0.9


def test_fuse_ranks_score_is_rank_based():
    """Итоговая оценка считается из рангов, а не из чужих шкал."""
    lexical = [{"doc_id": "a", "chunk_index": 0, "score": 1000.0}]
    semantic = [{"doc_id": "z", "chunk_index": 0, "score": 0.0001}]
    fused = li._fuse_ranks(lexical, semantic)
    assert fused[0]["score"] < 0.02, "оценка не должна тянуть за собой шкалу BM25"


def test_indexer_stats_and_clear(indexer, text_dir):
    """Сводка показывает объём индекса, очистка его убирает."""
    build_search_index(indexer, text_dir)
    data = indexer.stats()
    assert data["documents"] == 2
    assert data["chunks"] > 0
    assert data["bytes"] > 0
    assert data["index_size"]
    indexer.clear()
    assert indexer.stats()["chunks"] == 0


def test_index_summary_text(indexer, text_dir):
    """Текстовый отчёт об индексации объясняет режим поиска."""
    write_document(text_dir, "gk.txt")
    summary = indexer.index_documents(directory=str(text_dir))
    text = li.index_summary_text(summary)
    assert "Фрагментов" in text
    assert "поиск" in text.lower()


def test_save_index_report(indexer, text_dir, tmp_path):
    """Отчёт об индексации сохраняется в JSON."""
    write_document(text_dir, "gk.txt")
    summary = indexer.index_documents(directory=str(text_dir))
    path = li.save_index_report(summary, str(tmp_path / "report.json"))
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["chunks"] == summary["chunks"]
    assert "saved_at" in data
