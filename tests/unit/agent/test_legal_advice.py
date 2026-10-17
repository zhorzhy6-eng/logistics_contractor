#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты юридических ответов: explain_consequences и legal_advice (legal.py).

Что здесь важно (и почему тесты именно такие):

* ответ НЕ выдумывает нормы: без результатов RAG — «в базе нет», а не
  рассуждение «обычно считают»;
* источник обязателен: если модель его не назвала, программа дописывает;
* цитата — дословная: если модель переписала норму, оператор получает
  текст акта отдельным блоком;
* сложный вопрос (суд, спор, деньги) заканчивается «Проверьте с юристом.»;
* модель недоступна — ответ из цитат, а не пустой экран и не выдумка.

Сеть не трогается: RAG и модель подставляются.
"""

from __future__ import annotations

import pytest

from services.agent.tools import legal

#: Норма, которую «находит» подставной RAG.
VAT_CHUNK = {
    "text": ("Налогообложение производится по налоговой ставке 22 процента в "
             "случаях, не указанных в пунктах 1, 2 и 4 настоящей статьи."),
    "article": "164",
    "doc_short": "НК РФ ч.2",
    "doc_number": "117-ФЗ",
    "doc_date": "05.08.2000",
    "doc_url": "http://publication.pravo.gov.ru/document/0001202008050001",
}

#: Вторая норма: для «несколько норм — сложный вопрос».
DELAY_CHUNK = {
    "text": ("Перевозчик уплачивает грузополучателю штраф за просрочку "
             "доставки груза в размере девяти процентов провозной платы за "
             "каждые сутки просрочки, если иное не установлено договором "
             "перевозки груза."),
    "article": "34",
    "doc_short": "УАТ",
    "doc_number": "259-ФЗ",
    "doc_date": "08.11.2007",
    "doc_url": "http://publication.pravo.gov.ru/document/0001200711090001",
}

#: Третья норма: делает вопрос сложным по количеству норм.
CLAIM_CHUNK = {
    "text": ("уведомить в письменной форме заявителей в течение тридцати "
             "дней со дня получения соответствующей претензии"),
    "article": "40",
    "doc_short": "УАТ",
    "doc_number": "259-ФЗ",
    "doc_date": "08.11.2007",
    "doc_url": "",
}


class FakeRAG:
    """Подставной RAG: отдаёт заданные фрагменты, сеть не нужна."""

    def __init__(self, chunks=None, indexed=True, error=None):
        self.chunks = list(chunks or [])
        self.indexed = indexed
        self.error = error
        self.queries = []

    def available(self):
        return self.indexed

    def search(self, query, top_k=5):
        self.queries.append(query)
        if self.error:
            raise self.error
        return list(self.chunks)

    def stats(self):
        return {"documents": 1, "chunks": len(self.chunks), "semantic": False}


class FakeLLM:
    """Подставная модель: запоминает сообщения и возвращает заданный текст."""

    def __init__(self, answer="", error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def __call__(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture(autouse=True)
def _clean_clients():
    """Каждый тест начинает без подставных RAG и модели."""
    legal.reset_legal_clients()
    yield
    legal.reset_legal_clients()


def install(chunks=(VAT_CHUNK,), indexed=True, llm=None, error=None):
    """Ставит подставные RAG и модель; возвращает (rag, llm)."""
    rag = FakeRAG(chunks=chunks, indexed=indexed, error=error)
    legal.set_legal_rag(rag)
    model = llm if llm is not None else FakeLLM(
        answer="По НК РФ ч.2 ст. 164 ставка НДС — 22%: «Налогообложение "
               "производится по налоговой ставке 22 процента»."
    )
    legal.set_legal_llm(model)
    return rag, model


# ─────────────────────────────────────────────────────────────
# 1. explain_consequences
# ─────────────────────────────────────────────────────────────

def test_explain_answers_from_norms():
    """Объяснение строится на найденных нормах и содержит источник."""
    install()
    result = legal.explain_consequences(scenario="какая ставка НДС в 2026 году",
                                        contract_type="perevozka")
    assert result["ok"] is True
    data = result["data"]
    assert data["found"] is True
    assert data["llm_used"] is True
    assert "22 процента" in data["answer"]
    assert data["sources"][0]["source"] == "НК РФ ч.2"
    assert data["sources"][0]["article"] == "ст. 164"


def test_explain_calls_model_with_norms_and_contract_type():
    """В запрос к модели попадают нормы и тип договора."""
    _, model = install()
    legal.explain_consequences(scenario="просрочка доставки", contract_type="arenda_ts")
    messages = model.calls[0]
    assert messages[0]["role"] == "system"
    assert "ПОСЛЕДСТВИЯ" in messages[0]["content"].upper()
    user = messages[1]["content"]
    assert "просрочка доставки" in user
    assert "arenda_ts" in user
    assert "22 процента" in user


def test_explain_search_includes_contract_type():
    """Тип договора участвует в поиске: нормы ищутся уже в его контексте."""
    rag, _ = install()
    legal.explain_consequences(scenario="срок оплаты", contract_type="perevozka")
    assert "perevozka" in rag.queries[0]


def test_explain_no_norms_says_there_is_none():
    """Норм нет — «в базе нет», и модель НЕ вызывается."""
    _, model = install(chunks=())
    result = legal.explain_consequences(scenario="можно ли не платить")
    data = result["data"]
    assert data["found"] is False
    assert "в правовой базе нет" in data["answer"].casefold()
    assert data["sources"] == []
    assert model.calls == []


def test_explain_requires_scenario():
    """Пустая ситуация — понятная ошибка, а не пустой ответ."""
    install()
    result = legal.explain_consequences(scenario="")
    assert result["ok"] is False
    assert "scenario" in result["error"]


def test_explain_reports_empty_index():
    """Индекс не собран — сказано, что идти в настройки правовой базы."""
    install(indexed=False)
    result = legal.explain_consequences(scenario="срок оплаты")
    assert result["ok"] is False
    assert "Правовая база" in result["error"]


def test_explain_survives_search_error():
    """Ошибка поиска не роняет ответ: понятное сообщение оператору."""
    install(error=RuntimeError("база занята"))
    result = legal.explain_consequences(scenario="срок оплаты")
    assert result["ok"] is False
    assert "не выполнился" in result["error"]


def test_explain_complex_question_gets_lawyer_note():
    """Сложный вопрос (суд) заканчивается «Проверьте с юристом.»."""
    install(llm=FakeLLM(answer="Норма есть, но дело сложное."))
    result = legal.explain_consequences(scenario="пойдём в суд и взыщем штраф")
    assert result["data"]["needs_lawyer"] is True
    assert "Проверьте с юристом." in result["data"]["answer"]


def test_explain_simple_question_has_no_lawyer_note():
    """Простой вопрос без спора — без совета идти к юристу."""
    install()
    result = legal.explain_consequences(scenario="какая ставка НДС")
    assert result["data"]["needs_lawyer"] is False
    assert "с юристом" not in result["data"]["answer"].casefold()


def test_explain_adds_source_when_model_forgot():
    """Модель не назвала источник — программа дописывает его сама."""
    install(llm=FakeLLM(answer="Ставка — 22 процента."))
    result = legal.explain_consequences(scenario="какая ставка НДС")
    answer = result["data"]["answer"]
    assert "НК РФ ч.2" in answer
    assert "Источники" in answer


def test_explain_falls_back_to_quotes_when_model_down():
    """Модель недоступна — ответ из цитат, а не пустой экран."""
    install(llm=FakeLLM(error=RuntimeError("нет сети")))
    result = legal.explain_consequences(scenario="какая ставка НДС")
    data = result["data"]
    assert data["llm_used"] is False
    assert data["found"] is True
    assert "22 процента" in data["answer"]
    assert "Модель недоступна" in data["answer"]
    assert "Проверьте с юристом." in data["answer"]


def test_explain_empty_model_answer_falls_back():
    """Пустой ответ модели — тот же запасной путь с цитатами."""
    install(llm=FakeLLM(answer="   "))
    result = legal.explain_consequences(scenario="какая ставка НДС")
    assert result["data"]["llm_used"] is False
    assert "22 процента" in result["data"]["answer"]


def test_explain_long_answer_is_shortened():
    """Слишком длинный ответ обрезается: панель не резиновая."""
    install(llm=FakeLLM(answer="Подробно. " * 400))
    result = legal.explain_consequences(scenario="какая ставка НДС")
    assert len(result["data"]["answer"]) <= legal.MAX_ANSWER_CHARS


def test_explain_prompt_comes_from_file():
    """Текст промпта — из файла legal_explain.txt, а не из кода."""
    text = legal.load_legal_prompt(legal.EXPLAIN_PROMPT_FILE)
    assert "ПОСЛЕДСТВИЯ" in text.upper()
    assert "в правовой базе нет" in text.casefold()


# ─────────────────────────────────────────────────────────────
# 2. legal_advice
# ─────────────────────────────────────────────────────────────

def test_advice_answers_with_quote_and_source():
    """Совет содержит цитату нормы и источник."""
    install()
    result = legal.legal_advice(question="какая ставка НДС", contract_type="perevozka")
    data = result["data"]
    assert data["found"] is True
    assert data["llm_used"] is True
    assert "22 процента" in data["answer"]
    assert data["sources"][0]["source"] == "НК РФ ч.2"


def test_advice_prompt_demands_verbatim_quote():
    """Промпт советника требует дословной цитаты и «в базе нет»."""
    text = legal.load_legal_prompt(legal.ADVISOR_PROMPT_FILE)
    assert "дословно" in text.casefold()
    assert "в базе нет" in text.casefold()
    assert "Не выдумывай" in text


def test_advice_no_norms_says_there_is_none():
    """Норм нет — «в базе нет» и никакой модели."""
    _, model = install(chunks=())
    result = legal.legal_advice(question="можно ли не платить")
    assert result["data"]["found"] is False
    assert "в правовой базе нет" in result["data"]["answer"].casefold()
    assert model.calls == []


def test_advice_requires_question():
    """Пустой вопрос — понятная ошибка."""
    install()
    result = legal.legal_advice(question="")
    assert result["ok"] is False
    assert "question" in result["error"]


def test_advice_reports_empty_index():
    """Индекс не собран — сказано, что делать."""
    install(indexed=False)
    result = legal.legal_advice(question="срок оплаты")
    assert result["ok"] is False
    assert "Индексировать" in result["error"]


def test_advice_readds_norms_when_quote_is_rewritten():
    """Модель переписала цитату — программа показывает норму дословно."""
    install(llm=FakeLLM(answer="Норма говорит, что ставка двадцать два процента."))
    result = legal.legal_advice(question="какая ставка НДС")
    answer = result["data"]["answer"]
    assert "Нормы дословно" in answer
    assert "Налогообложение производится по налоговой ставке 22 процента" in answer


def test_advice_keeps_answer_when_quote_is_verbatim():
    """Дословная цитата — ничего не дописывается."""
    install()  # подставная модель цитирует норму дословно
    result = legal.legal_advice(question="какая ставка НДС")
    assert "Нормы дословно" not in result["data"]["answer"]


def test_advice_complex_question_gets_lawyer_note():
    """Спор и убытки — обязательный совет проверить у юриста."""
    install(llm=FakeLLM(answer="Цитата: «Налогообложение производится по "
                               "налоговой ставке 22 процента». НК РФ ч.2."))
    result = legal.legal_advice(question="требуем убытки через суд")
    assert result["data"]["needs_lawyer"] is True
    assert "Проверьте с юристом." in result["data"]["answer"]


def test_advice_several_norms_make_question_complex():
    """Три нормы в ответе — вопрос считается сложным сам по себе."""
    install(chunks=(VAT_CHUNK, DELAY_CHUNK, CLAIM_CHUNK))
    result = legal.legal_advice(question="какая ставка НДС")
    assert result["data"]["needs_lawyer"] is True


def test_advice_falls_back_to_quotes_when_model_down():
    """Модель недоступна — ответ из цитат с источником."""
    install(llm=FakeLLM(error=TimeoutError("таймаут")))
    result = legal.legal_advice(question="какая ставка НДС")
    data = result["data"]
    assert data["llm_used"] is False
    assert "TimeoutError" in data["answer"]
    assert "22 процента" in data["answer"]


def test_advice_passes_contract_type_to_model():
    """Тип договора уходит в запрос — из него берётся «применимость»."""
    _, model = install()
    legal.legal_advice(question="срок оплаты", contract_type="arenda_ts")
    assert "arenda_ts" in model.calls[0][1]["content"]


def test_advice_sources_carry_quote_and_url():
    """У источника есть цитата и ссылка на акт — оператор сверит сам."""
    install()
    result = legal.legal_advice(question="какая ставка НДС")
    source = result["data"]["sources"][0]
    assert source["quote"].startswith("Налогообложение производится")
    assert source["url"].startswith("http")


def test_advice_answer_names_source_or_gets_it_appended():
    """Источник в ответе есть всегда: либо от модели, либо от программы."""
    install(llm=FakeLLM(answer="Ставка — 22 процента."))
    result = legal.legal_advice(question="какая ставка НДС")
    assert "НК РФ ч.2" in result["data"]["answer"]


# ─────────────────────────────────────────────────────────────
# 3. Сложность вопроса
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "пойдём в суд?",
    "какие штрафы за просрочку",
    "можно ли взыскать убытки",
    "расторжение договора аренды",
    "претензия от заказчика",
    "грозит ли уголовная ответственность",
])
def test_complex_markers(question):
    """Слова про спор и ответственность делают вопрос сложным."""
    assert legal.is_complex_question(question, []) is True


def test_simple_question_is_not_complex():
    """Вопрос о ставке налога — не повод отправлять к юристу."""
    assert legal.is_complex_question("какая ставка НДС", [VAT_CHUNK]) is False


def test_many_norms_make_it_complex():
    """Три и больше норм — вопрос сложный даже без слов-маркеров."""
    assert legal.is_complex_question("ставка", [VAT_CHUNK, DELAY_CHUNK]) is False
    assert legal.is_complex_question(
        "ставка", [VAT_CHUNK, DELAY_CHUNK, CLAIM_CHUNK]
    ) is True


# ─────────────────────────────────────────────────────────────
# 4. Двадцать юридических вопросов оператора
# ─────────────────────────────────────────────────────────────
#: Реальные вопросы, с которыми приходят в программу. Для каждого проверяется
#: одно и то же правило: ответ либо опирается на найденную норму с
#: источником, либо честно говорит «в базе нет» — третьего не дано.
OPERATOR_QUESTIONS = [
    "какая ставка НДС в 2026 году",
    "сколько дней даётся на оплату по договору перевозки",
    "какой штраф за просрочку доставки груза",
    "обязательна ли транспортная накладная",
    "можно ли не платить, если груз пришёл на день позже",
    "что будет за перевозку без путевого листа",
    "имеет ли право перевозчик удерживать груз до оплаты",
    "обязателен ли маршрут в заявке",
    "что делать, если заказчик не подписал акт",
    "в какой срок отвечают на претензию",
    "сколько лет срок исковой давности по перевозке",
    "кто платит за простой транспорта под погрузкой",
    "можно ли взыскать убытки сверх штрафа",
    "нужно ли указывать VIN в договоре аренды",
    "что грозит за управление без водительского удостоверения",
    "оформляется ли путевой лист при аренде с экипажем",
    "какие документы нужны для перевозки опасного груза",
    "какой штраф за перегруз",
    "можно ли изменить договор перевозки после подписания",
    "что считается моментом подачи машины под погрузку",
]


@pytest.mark.parametrize("question", OPERATOR_QUESTIONS)
def test_every_answer_has_source_or_says_none(question):
    """
    На каждый вопрос оператора — либо норма с источником, либо «в базе нет».

    В этом тесте RAG отдаёт норму, поэтому ответ обязан её назвать: в
    тексте есть акт из источника, а сложные вопросы заканчиваются
    советом проверить у юриста.
    """
    install(llm=FakeLLM(
        answer="По НК РФ ч.2 ст. 164: «Налогообложение производится по "
               "налоговой ставке 22 процента»."
    ))
    result = legal.legal_advice(question=question, contract_type="perevozka")
    data = result["data"]
    assert data["found"] is True
    assert "НК РФ ч.2" in data["answer"], "в ответе нет источника"
    if data["needs_lawyer"]:
        assert "Проверьте с юристом." in data["answer"]


@pytest.mark.parametrize("question", OPERATOR_QUESTIONS)
def test_every_question_says_none_without_norms(question):
    """Пустая база: на любой из двадцати вопросов — «в базе нет», не выдумка."""
    install(chunks=())
    result = legal.legal_advice(question=question)
    assert result["data"]["found"] is False
    assert "в правовой базе нет" in result["data"]["answer"].casefold()


# ─────────────────────────────────────────────────────────────
# 5. Служебное
# ─────────────────────────────────────────────────────────────

def test_load_legal_prompt_falls_back(tmp_path, monkeypatch):
    """Потерянный файл промпта — запасной текст, а не исключение."""
    monkeypatch.setattr(legal, "PROMPTS_DIR", tmp_path)
    text = legal.load_legal_prompt("нет_такого.txt", "запасной")
    assert text == "запасной"


def test_reset_legal_clients_forgets_model():
    """reset_legal_clients забывает и модель (тесты не тянут прежнюю)."""
    install()
    legal.reset_legal_clients()
    assert legal._llm is None and legal._rag is None
