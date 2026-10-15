#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Часть 3: правовая база у Максимилиана (задание, A–E).

Что проверяется
---------------
1. **Инструменты** (A). `search_legal(query)` отдаёт до пяти фрагментов норм
   С ИСТОЧНИКОМ (акт, статья, ссылка) и отвечает «в базе нет», когда ничего
   не нашлось. `download_legal(query)` ищет акт на портале, скачивает,
   индексирует и отвечает «Добавил в базу»; если акт уже есть — спрашивает
   «Обновить?», а перекачивает только по `force=true`.
2. **Промпт** (B). Раздел про правовую базу: цитировать точно, указывать
   источник, не выдумывать нормы, «в базе нет» — это ответ.
3. **Интерфейс** (C). Раздел «Правовая база» в настройках Максимилиана:
   [Обновить всё], [Скачать документ], [Скачать категорию], список
   скачанных документов, дата последнего обновления, размер базы.
4. **Живой путь** (D). Разговор оператора с моделью, которая зовёт
   `search_legal`, — без сети: и портал, и RAG подставные.

Сеть не трогается ни одним тестом: парсер и RAG подменяются
(`core/agent/legal_tools.py`).
"""

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import legal_tools  # noqa: E402
from core.agent.tools import AgentContext, build_registry  # noqa: E402

CONTRACT_TEXT = (
    "1. По договору перевозки груза перевозчик обязуется доставить "
    "вверенный ему отправителем груз в пункт назначения и выдать его "
    "управомоченному на получение груза лицу, а отправитель обязуется "
    "уплатить за перевозку груза установленную плату."
)

FINE_TEXT = (
    "За просрочку доставки груза перевозчик уплачивает грузополучателю "
    "штраф в размере девяти процентов провозной платы за каждые сутки "
    "просрочки, если иное не установлено договором перевозки груза."
)


# ─────────────────────────────────────────────────────────────
# Заглушки: RAG и парсер правовой базы
# ─────────────────────────────────────────────────────────────

class FakeRag:
    """Подставной RAG: поиск по заранее заданным фрагментам."""

    def __init__(self, chunks=None, indexed=True, error=None):
        self.chunks = list(chunks or ())
        self.indexed = indexed
        self.error = error
        self.queries = []
        self.index_calls = 0

    def available(self) -> bool:
        if self.error:
            raise self.error
        return self.indexed

    def search(self, query, top_k=5, min_score=0.0):
        self.queries.append({"query": query, "top_k": top_k})
        if self.error:
            raise self.error
        return list(self.chunks)[: int(top_k or 5)]

    def stats(self):
        return {
            "documents": 13, "chunks": len(self.chunks) or 1929,
            "embedded": 0, "semantic": False, "index_size": "3,8 МБ",
        }

    def index_documents(self, directory=None, reindex=False):
        self.index_calls += 1
        return {"documents": 1, "chunks": 42, "embedded": 0, "skipped": 0,
                "errors": {}, "seconds": 0.1}


def make_chunk(article: str = "793", text: str = CONTRACT_TEXT,
               title: str = "ГК РФ ч.2", **extra):
    """Фрагмент нормы в том виде, в каком его отдаёт индексатор."""
    chunk = {
        "text": text, "score": 0.9, "mode": "lexical",
        "article": article, "part": "",
        "doc_title": f"Гражданский кодекс Российской Федерации (часть вторая)",
        "doc_short": title, "doc_number": "14-ФЗ", "doc_date": "26.01.1996",
        "doc_url": "http://publication.pravo.gov.ru/document/0001202601010001",
        "doc_id": "gk-rf-2",
    }
    chunk.update(extra)
    return chunk


class FakeDocument:
    """Подставной документ портала (то, что возвращает LegalParser.search)."""

    def __init__(self, title="Правила перевозок грузов", number="2200",
                 doc_date="2021-11-21", downloaded=False):
        self.id = f"doc-{number}"
        self.title = title
        self.short_title = title
        self.number = number
        self.doc_date = doc_date
        self.publish_date = doc_date
        self.doc_type = "Постановление"
        self.url = "http://publication.pravo.gov.ru/document/0001202111220001"
        self.file_url = ""
        self.category = "Перевозки"
        self.original_path = "/tmp/fake.pdf" if downloaded else ""
        self.text_path = "/tmp/fake.txt" if downloaded else ""
        self.checksum = ""

    @property
    def display_title(self):
        return self.short_title or self.title

    @property
    def is_downloaded(self):
        return bool(self.original_path)


class FakeDownloadResult:
    """Итог скачивания документа (как core.legal_parser.DownloadResult)."""

    def __init__(self, document, ok=True, skipped=False, unavailable=False,
                 error="", text_chars=12345):
        self.document = document
        self.original_path = "/tmp/fake.pdf" if ok else ""
        self.text_path = "/tmp/fake.txt" if ok else ""
        self.text_chars = text_chars if ok else 0
        self.ocr_used = False
        self.skipped = skipped
        self.unavailable = unavailable
        self.error = error

    @property
    def ok(self):
        return bool(self.original_path) and not self.error


class FakeParser:
    """Подставной парсер правовой базы (никакой сети)."""

    def __init__(self, found=None, result=None, error=None,
                 in_base_number=None):
        self.found = list(found or ())
        self.result = result
        self.error = error
        self.in_base_number = in_base_number
        self.search_calls = []
        self.download_calls = []

    def search(self, query, limit=20, **_):
        self.search_calls.append({"query": query, "limit": limit})
        if self.error:
            raise self.error
        return list(self.found)[:limit]

    def download_document(self, document, save_dir=None):
        self.download_calls.append(document)
        if self.error:
            raise self.error
        if self.result is not None:
            return self.result
        return FakeDownloadResult(document)

    def documents(self, only_downloaded=False, limit=None, **_):
        return [{"id": "doc-2200", "title": "Правила перевозок грузов",
                 "short_title": "Правила перевозок грузов", "number": "2200",
                 "downloaded_at": "2026-10-15 12:00:00",
                 "original_bytes": 2_400_000, "original_path": "/tmp/a.pdf"}]

    def stats(self):
        return {"documents": 18, "downloaded": 13, "text_chars": 1_283_936,
                "original_bytes": 13_600_000, "text_bytes": 2_300_000,
                "last_update": "2026-10-15", "last_check": "2026-10-15",
                "data_dir": "data/knowledge/legal"}


@pytest.fixture
def fake_rag():
    """RAG с двумя нормами: договор перевозки и просрочка."""
    rag = FakeRag(chunks=[
        make_chunk(article="785", text=CONTRACT_TEXT),
        make_chunk(article="793", text=FINE_TEXT, doc_short="УАТ",
                   doc_number="259-ФЗ", doc_date="08.11.2007"),
    ])
    previous = legal_tools.set_legal_rag(rag)
    yield rag
    legal_tools.set_legal_rag(previous)


@pytest.fixture
def fake_parser():
    """Парсер портала: один документ, скачивание успешно."""
    parser = FakeParser(found=[FakeDocument()])
    previous = legal_tools.set_legal_parser(parser)
    yield parser
    legal_tools.set_legal_parser(previous)


@pytest.fixture(autouse=True)
def isolated_legal_db(work_file):
    """
    Пустая база метаданных правовой базы на время теста.

    Без неё проверки читали бы рабочую ``db/legal_metadata.db``: там уже
    лежат скачанные акты, и `download_legal` честно отвечал бы «уже в
    базе» — тест зависел бы от того, что оператор скачал в программе.
    """
    from db.crud import legal_metadata

    path = work_file("legal_metadata.db")
    legal_metadata.set_legal_db_path(str(path))
    conn = legal_metadata.get_connection()
    legal_metadata.init_db(conn)
    conn.close()
    yield
    legal_metadata.set_legal_db_path(None)


@pytest.fixture(autouse=True)
def clean_legal_clients():
    """После каждого теста подставные клиенты забываются."""
    yield
    legal_tools.reset_legal_clients()


def payload_size(result) -> int:
    """Сколько символов займёт результат инструмента в запросе к модели."""
    return len(json.dumps(result, ensure_ascii=False, default=str))


# ─────────────────────────────────────────────────────────────
# A.1, E.1. search_legal
# ─────────────────────────────────────────────────────────────

def test_search_legal_returns_chunks_with_sources(fake_rag):
    """E.1. Поиск отдаёт фрагменты с источником: акт, статья, ссылка."""
    result = legal_tools.search_legal(query="ответственность за просрочку")

    assert result["ok"] is True
    assert result["found"] is True
    assert result["count"] == 2
    chunk = result["data"]["chunks"][0]
    assert chunk["source"] == "ГК РФ ч.2"
    assert chunk["article"] == "ст. 785"
    assert chunk["number"] == "14-ФЗ"
    assert chunk["url"].startswith("http")
    assert "договору перевозки груза" in chunk["text"]


def test_search_legal_passes_query_and_top_k(fake_rag):
    """Запрос и top-k уходят в RAG как есть."""
    legal_tools.search_legal(query="просрочка доставки", top_k=3)

    assert fake_rag.queries[0]["query"] == "просрочка доставки"
    assert fake_rag.queries[0]["top_k"] == 3


def test_search_legal_default_top_k_is_five(fake_rag):
    """По умолчанию — пять фрагментов (задание, A.1)."""
    legal_tools.search_legal(query="договор перевозки")

    assert fake_rag.queries[0]["top_k"] == legal_tools.LEGAL_TOP_K == 5


def test_search_legal_empty_query_is_refused(fake_rag):
    """Пустой вопрос — понятная ошибка, в RAG не идём."""
    result = legal_tools.search_legal(query="   ")

    assert result["ok"] is False
    assert "query" in result["error"]
    assert fake_rag.queries == []


def test_search_legal_says_base_is_empty_not_norm_missing():
    """Индекс не собран — это не «нормы нет», а «база не собрана»."""
    previous = legal_tools.set_legal_rag(FakeRag(indexed=False))
    try:
        result = legal_tools.search_legal(query="просрочка")
    finally:
        legal_tools.set_legal_rag(previous)

    assert result["ok"] is False
    assert result["indexed"] is False
    assert "не собрана" in result["error"]
    assert "Правовая база" in result["error"]


def test_search_legal_says_no_norm_in_base():
    """Ничего не нашлось — прямой ответ «в базе нет» (B.1)."""
    previous = legal_tools.set_legal_rag(FakeRag(chunks=[]))
    try:
        result = legal_tools.search_legal(query="страхование космических грузов")
    finally:
        legal_tools.set_legal_rag(previous)

    assert result["ok"] is True
    assert result["found"] is False
    assert result["count"] == 0
    assert "в правовой базе нет" in result["message"]
    assert "в базе нет" in result["message"]


def test_search_legal_result_is_short(fake_rag):
    """Результат короче лимита памяти агента — обрезки не будет."""
    chunks = [make_chunk(article=str(700 + index), text=FINE_TEXT * 2)
              for index in range(5)]
    fake_rag.chunks = chunks

    result = legal_tools.search_legal(query="штраф за просрочку")

    assert len(result["data"]["chunks"]) == 5
    assert payload_size(result) < 4000


def test_search_legal_truncates_long_text(fake_rag):
    """Один фрагмент не превращается в «полотно»."""
    fake_rag.chunks = [make_chunk(text="я" * 5000)]

    result = legal_tools.search_legal(query="норма")

    text = result["data"]["chunks"][0]["text"]
    assert len(text) <= legal_tools.MAX_CHUNK_CHARS
    assert text.endswith("…")


def test_search_legal_survives_broken_rag():
    """Сбой поиска не роняет запрос оператора (правило внешнего слоя)."""
    previous = legal_tools.set_legal_rag(FakeRag(error=RuntimeError("база занята")))
    try:
        result = legal_tools.search_legal(query="просрочка")
    finally:
        legal_tools.set_legal_rag(previous)

    assert result["ok"] is False
    assert "не выполнился" in result["error"] or "не собрана" in result["error"]


def test_search_legal_reports_index_state(fake_rag):
    """В ответе видно, чем искали и сколько всего в индексе."""
    result = legal_tools.search_legal(query="договор")

    index = result["data"]["index"]
    assert index["documents"] == 13
    assert index["mode"] == "по словам"


def test_search_legal_does_not_touch_portal(fake_parser, fake_rag):
    """Поиск по базе в сеть не ходит: парсер не вызывается."""
    legal_tools.search_legal(query="договор перевозки")

    assert fake_parser.search_calls == []


# ─────────────────────────────────────────────────────────────
# A.2, A.3, E.2. download_legal
# ─────────────────────────────────────────────────────────────

def test_download_legal_downloads_and_indexes(fake_parser, fake_rag):
    """E.2. Акт скачивается, индексируется и попадает в базу."""
    result = legal_tools.download_legal(query="Правила перевозок грузов")

    assert result["ok"] is True
    assert result["downloaded"] is True
    assert result["already"] is False
    assert "Скачал" in result["message"]
    assert "Добавил в базу" in result["message"]
    assert fake_rag.index_calls == 1
    assert result["data"]["chunks"] == 42


def test_download_legal_reports_document(fake_parser, fake_rag):
    """В ответе есть карточка акта: название, номер, дата, ссылка."""
    result = legal_tools.download_legal(query="2200")

    card = result["data"]["document"]
    assert card["title"] == "Правила перевозок грузов"
    assert card["number"] == "2200"
    assert card["url"].startswith("http")


def test_download_legal_empty_query(fake_parser, fake_rag):
    """Пустой запрос — ошибка, на портал не идём."""
    result = legal_tools.download_legal(query="")

    assert result["ok"] is False
    assert fake_parser.search_calls == []


def test_download_legal_nothing_found_on_portal(fake_parser, fake_rag):
    """Ничего не нашлось на портале — честный ответ."""
    fake_parser.found = []

    result = legal_tools.download_legal(query="что-то несуществующее")

    assert result["ok"] is True
    assert result["found"] is False
    assert result["downloaded"] is False
    assert "ничего не нашлось" in result["message"]


def test_download_legal_asks_to_update_when_already_in_base(
        fake_parser, fake_rag, monkeypatch):
    """A.3. Акт уже в базе — «Документ уже в базе. Обновить?»."""
    from db.crud import legal_metadata

    monkeypatch.setattr(
        legal_metadata, "find_by_number",
        lambda number, doc_date="", conn=None: {"id": "doc-2200"},
    )

    result = legal_tools.download_legal(query="Правила перевозок грузов")

    assert result["ok"] is True
    assert result["downloaded"] is False
    assert result["already"] is True
    assert result["needs_update"] is True
    assert "уже в базе" in result["message"]
    assert "Обновить?" in result["message"]
    assert "force" in result["hint"]
    assert fake_parser.download_calls == [], "перекачивать нельзя без согласия"
    assert fake_rag.index_calls == 0


def test_download_legal_already_in_base_by_downloaded_file(fake_parser,
                                                           fake_rag):
    """Акт считается имеющимся и по скачанному файлу."""
    fake_parser.found = [FakeDocument(downloaded=True)]

    result = legal_tools.download_legal(query="Правила перевозок грузов")

    assert result["already"] is True
    assert fake_parser.download_calls == []


def test_download_legal_force_redownloads(fake_parser, fake_rag, monkeypatch):
    """force=true перекачивает акт, даже если он уже в базе."""
    from db.crud import legal_metadata

    monkeypatch.setattr(
        legal_metadata, "find_by_number",
        lambda number, doc_date="", conn=None: {"id": "doc-2200"},
    )

    result = legal_tools.download_legal(query="Правила перевозок грузов",
                                       force=True)

    assert result["downloaded"] is True
    assert result["already"] is False
    assert len(fake_parser.download_calls) == 1
    assert fake_rag.index_calls == 1


def test_download_legal_skipped_means_already_in_base(fake_parser, fake_rag):
    """Парсер пропустил скачивание (файл не изменился) — так и говорим."""
    document = FakeDocument()
    fake_parser.found = [document]
    fake_parser.result = FakeDownloadResult(document, ok=False, skipped=True)

    result = legal_tools.download_legal(query="Правила перевозок грузов")

    assert result["ok"] is True
    assert result["already"] is True
    assert "не изменился" in result["message"]


def test_download_legal_unavailable_explains_manual_path(fake_parser, fake_rag):
    """Акт недоступен на портале — объясняем, что делать."""
    document = FakeDocument(number="259-ФЗ", title="Устав автомобильного транспорта")
    fake_parser.found = [document]
    fake_parser.result = FakeDownloadResult(document, ok=False, unavailable=True)

    result = legal_tools.download_legal(query="УАТ")

    assert result["ok"] is True
    assert result["unavailable"] is True
    assert "вручную" in result["message"]


def test_download_legal_portal_failure_is_not_a_crash(fake_parser, fake_rag):
    """Портал упал — понятная ошибка, а не падение запроса."""
    fake_parser.error = RuntimeError("нет соединения")

    result = legal_tools.download_legal(query="ФЗ-259")

    assert result["ok"] is False
    assert "pravo.gov.ru" in result["error"]


def test_download_legal_download_failure_is_not_a_crash(fake_parser, fake_rag):
    """Скачивание не удалось — ошибка с названием акта."""
    document = FakeDocument()
    fake_parser.found = [document]
    fake_parser.result = FakeDownloadResult(document, ok=False,
                                            error="портал не отдал файл")

    result = legal_tools.download_legal(query="Правила перевозок грузов")

    assert result["ok"] is False
    assert "не удалось" in result["error"]


def test_download_legal_index_failure_keeps_download(fake_parser, fake_rag):
    """Индексация упала — акт всё равно скачан, и это сказано прямо."""
    def boom(directory=None, reindex=False):
        raise RuntimeError("индекс занят")

    fake_rag.index_documents = boom

    result = legal_tools.download_legal(query="Правила перевозок грузов")

    assert result["ok"] is True
    assert result["downloaded"] is True
    assert result["indexed"] is False
    assert "Скачал" in result["message"]


def test_download_legal_result_is_short(fake_parser, fake_rag):
    """Ответ короткий: без текста документа и без служебных полей."""
    result = legal_tools.download_legal(query="Правила перевозок грузов")

    assert payload_size(result) < 1200


def test_download_legal_picks_first_candidate_and_lists_all(fake_parser,
                                                            fake_rag):
    """Скачивается первый (самый релевантный) акт, остальные видны."""
    fake_parser.found = [FakeDocument(title="Основной акт", number="1"),
                         FakeDocument(title="Похожий акт", number="2")]

    result = legal_tools.download_legal(query="акт")

    assert result["data"]["document"]["title"] == "Основной акт"
    assert len(result["data"]["candidates"]) == 2


# ─────────────────────────────────────────────────────────────
# Регистрация инструментов
# ─────────────────────────────────────────────────────────────

def test_legal_tools_are_registered():
    """Оба инструмента есть в реестре Максимилиана."""
    registry = build_registry(AgentContext())

    assert registry.get("search_legal") is not None
    assert registry.get("download_legal") is not None


def test_legal_tools_need_no_confirmation():
    """Чтение и скачивание не меняют форму — подтверждение не нужно."""
    registry = build_registry(AgentContext())

    assert registry.get("search_legal").requires_confirmation is False
    assert registry.get("download_legal").requires_confirmation is False


def test_search_legal_description_forbids_inventing_norms():
    """Описание инструмента требует источник и запрещает выдумывать."""
    description = build_registry(AgentContext()).get("search_legal").description

    assert "ИСТОЧНИК" in description or "источник" in description
    assert "НЕ" in description and "выдумывай" in description.lower()
    assert "в базе нет" in description.lower()


def test_download_legal_description_mentions_force():
    """Описание объясняет вопрос «Обновить?» и повторный вызов."""
    description = build_registry(AgentContext()).get("download_legal").description

    assert "Обновить?" in description
    assert "force" in description


def test_registry_calls_search_legal_with_mock(fake_rag):
    """Полный путь «реестр → инструмент» работает с подставным RAG."""
    registry = build_registry(AgentContext())

    result = registry.call("search_legal", {"query": "просрочка доставки"})

    assert result["ok"] is True
    assert result["data"]["chunks"]


# ─────────────────────────────────────────────────────────────
# B. Промпт
# ─────────────────────────────────────────────────────────────

def test_prompt_has_legal_section():
    """B.1–B.2. В промпте есть раздел про правовую базу."""
    from core.agent.prompt import build_system_prompt

    prompt = build_system_prompt()

    assert "ПРАВОВАЯ БАЗА" in prompt
    assert "search_legal" in prompt
    assert "download_legal" in prompt


def test_prompt_demands_exact_quotes_and_sources():
    """Промпт: цитировать точно, указывать источник, не выдумывать."""
    from core.agent.prompt import build_system_prompt

    prompt = build_system_prompt()

    assert "Цитируй ТОЧНО" in prompt
    assert "Указывай источник" in prompt
    assert "НЕ ВЫДУМЫВАЙ" in prompt
    assert "в базе нет" in prompt.lower()


def test_prompt_explains_download_request():
    """Промпт: «скачай закон» — это download_legal."""
    from core.agent.prompt import build_system_prompt

    prompt = build_system_prompt()

    assert "скачай закон" in prompt.lower()
    assert "download_legal(query)" in prompt


def test_prompt_keeps_previous_sections():
    """Правки промпта не потеряли прежние разделы."""
    from core.agent.prompt import build_system_prompt

    prompt = build_system_prompt()

    assert "ЭМОЦИОНАЛЬНАЯ ПОДДЕРЖКА" in prompt
    assert "СЛОМАЛОСЬ" in prompt
    assert "АНАЛИТИКА" in prompt


# ─────────────────────────────────────────────────────────────
# D. Живой путь: разговор с моделью
# ─────────────────────────────────────────────────────────────

class ScriptedLegalChat:
    """Подставная модель: зовёт search_legal, затем отвечает по результату."""

    def __init__(self, tool_name: str = "search_legal",
                 arguments: str = '{"query": "ответственность за просрочку"}'):
        self.tool_name = tool_name
        self.arguments = arguments
        self.calls = 0
        self.tool_result = ""

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.calls += 1
        for message in messages:
            if message.get("role") == "tool":
                self.tool_result = message.get("content") or ""
        if self.calls == 1:
            return {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": self.tool_name, "arguments": self.arguments},
            }]}
        return {"role": "assistant", "content": "Ответ по норме", "tool_calls": []}


class _NoRepository:
    """Репозиторий-заглушка: история остаётся в памяти теста."""

    def append(self, *args, **kwargs):
        return None

    def load(self, *args, **kwargs):
        return []

    def clear_session(self, *args, **kwargs):
        return 0

    def purge_other_sessions(self, *args, **kwargs):
        return 0


def test_agent_calls_search_legal(fake_rag):
    """D.1. Модель зовёт search_legal, результат уходит с источниками."""
    from core.agent.agent import Agent
    from core.agent.memory import AgentMemory

    client = ScriptedLegalChat()
    agent = Agent(client=client, context=AgentContext(),
                  memory=AgentMemory(repository=_NoRepository()))

    reply = agent.send("Какая ответственность за просрочку доставки?")

    assert "search_legal" in reply.tool_names
    payload = json.loads(client.tool_result)
    assert payload["ok"] is True
    assert payload["data"]["chunks"][0]["source"] == "ГК РФ ч.2"


def test_agent_gets_short_payload_for_legal_search(fake_rag):
    """Результат поиска норм влезает в лимит памяти агента."""
    from core.agent.agent import Agent
    from core.agent.memory import AgentMemory

    client = ScriptedLegalChat()
    agent = Agent(client=client, context=AgentContext(),
                  memory=AgentMemory(repository=_NoRepository()))
    agent.send("Что такое договор перевозки?")

    assert len(client.tool_result) < 4000
    assert json.loads(client.tool_result)["ok"] is True


def test_agent_handles_download_request(fake_parser, fake_rag):
    """D.3. «Скачай ФЗ-259» — модель зовёт download_legal."""
    from core.agent.agent import Agent
    from core.agent.memory import AgentMemory

    client = ScriptedLegalChat(tool_name="download_legal",
                               arguments='{"query": "ФЗ-259"}')
    agent = Agent(client=client, context=AgentContext(),
                  memory=AgentMemory(repository=_NoRepository()))

    reply = agent.send("Скачай ФЗ-259")

    assert "download_legal" in reply.tool_names
    payload = json.loads(client.tool_result)
    assert payload["ok"] is True
    assert "Скачал" in payload["message"]


# ─────────────────────────────────────────────────────────────
# C. Интерфейс: раздел «Правовая база»
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def qt_app():
    """QApplication для проверок интерфейса (offscreen)."""
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def legal_section(qt_app, fake_parser, isolated_db):
    """Раздел «Правовая база» с подставным парсером (без сети)."""
    from ui.legal_settings import LegalSettingsSection

    section = LegalSettingsSection(parser=fake_parser)
    yield section
    section.deleteLater()


def test_legal_section_has_documents_list(legal_section):
    """C.1. В разделе есть список скачанных документов."""
    items = [legal_section.documents_list.item(index).text()
             for index in range(legal_section.documents_list.count())]

    assert items, "список документов должен быть заполнен"
    assert any("Правила перевозок грузов" in item for item in items)
    assert any("2,3 МБ" in item or "МБ" in item for item in items)


def test_legal_section_shows_last_update_and_size(legal_section):
    """C.1. Видны дата последнего обновления и размер базы."""
    text = legal_section.status_label.text()

    assert "Последнее обновление: 2026-10-15" in text
    assert "Документов:" in text
    assert "13,0 МБ" in text or "МБ" in text


def test_legal_section_has_three_action_buttons(legal_section):
    """C.1. Три кнопки: [Обновить всё], [Скачать документ], [Скачать категорию]."""
    assert legal_section.update_all_button.text() == "Обновить всё"
    assert legal_section.download_document_button.text() == "Скачать документ"
    assert legal_section.download_category_button.text() == "Скачать категорию"


def test_legal_section_index_buttons_present(legal_section):
    """Индексация доступна из того же раздела."""
    assert legal_section.index_button.text() == "Индексировать правовую базу"
    assert legal_section.reindex_button.text() == "Переиндексировать"


@pytest.mark.parametrize("method,expected", [
    ("update_all", "download_all"),
    ("download_document", "download_document"),
    ("download_category", "download_categories"),
])
def test_legal_section_buttons_open_dialog_with_action(
        legal_section, monkeypatch, method, expected):
    """C.1. Кнопки открывают диалог правовой базы и запускают нужное действие."""
    from ui import legal_base_dialog, legal_settings

    calls = []

    class FakeDialog:
        def __init__(self, parent=None, parser=None):
            self.parser = parser

        def __getattr__(self, name):
            def action(*_args, **_kwargs):
                calls.append(name)
            return action

        def exec_(self):
            calls.append("exec")
            return 0

    monkeypatch.setattr(legal_base_dialog, "LegalBaseDialog", FakeDialog)
    #: Диалог запускается через QTimer.singleShot(0) — в тесте вызываем сразу.
    monkeypatch.setattr(legal_settings, "QTimer",
                        type("T", (), {"singleShot": staticmethod(
                            lambda _ms, func: func())}))

    getattr(legal_section, method)()
    legal_section.refresh()

    assert expected in calls, calls
    assert "exec" in calls


def test_agent_settings_dialog_has_legal_section(qt_app, fake_parser,
                                                isolated_db, monkeypatch):
    """C.2. Раздел «Правовая база» есть в настройках Максимилиана."""
    from ui import legal_settings
    from ui.agent_settings_dialog import AgentSettingsDialog
    from core.settings_service import get_settings_service

    real_section = legal_settings.LegalSettingsSection
    monkeypatch.setattr(
        legal_settings, "LegalSettingsSection",
        lambda parent=None: real_section(parent=parent, parser=fake_parser),
    )

    dialog = AgentSettingsDialog(get_settings_service().as_dict())
    try:
        assert dialog.legal_section is not None
        assert dialog.legal_section.title() == "Правовая база"
        assert dialog.legal_section.documents_list.count() >= 1
    finally:
        dialog.deleteLater()


def test_agent_settings_dialog_survives_broken_legal_section(
        qt_app, isolated_db, monkeypatch):
    """Сбой раздела не роняет окно настроек (правило границ)."""
    from ui import legal_settings
    from ui.agent_settings_dialog import AgentSettingsDialog
    from core.settings_service import get_settings_service

    def boom(parent=None, parser=None):
        raise RuntimeError("правовая база недоступна")

    monkeypatch.setattr(legal_settings, "LegalSettingsSection", boom)

    dialog = AgentSettingsDialog(get_settings_service().as_dict())
    try:
        assert dialog.legal_section is None
        assert dialog.btn_save is not None, "окно собрано целиком"
    finally:
        dialog.deleteLater()
