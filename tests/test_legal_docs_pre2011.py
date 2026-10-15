#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Акты ДО 2011 ГОДА в правовой базе (задача 6.3).

Что было. Пять актов в `config/legal_sources.json` помечены
``"unavailable": true``: НК РФ ч.1 (1998), НК РФ ч.2 (2000), КоАП (2001),
ФЗ-259 УАТ (2007), ФЗ-16 (2007). Портал `pravo.gov.ru` ведётся с 2011 года,
и в нём этих актов нет; банк «Законодательство России» отдаёт старые акты
по ``nd``, но идентификаторы для них неизвестны, а поиск по банку устроен на
JavaScript (см. `docs/LEGAL_BASE.md` § 2.3 и § 7). В базе проекта у всех пяти
``text_chars = 0`` — то есть в RAG-поиске их не было вовсе.

Что сделано. Источник — **официальный банк документов Президента России**
`kremlin.ru/acts/bank/<номер>` (тот же официальный правовой ресурс; с этой
машины `pravo.gov.ru` недоступен — таймаут, а `kremlin.ru` отвечает 200).
Новый инструмент `tools/download_legal_pre2011.py`:

  * берёт записи с ``unavailable`` и полем ``kremlin_bank`` (номера банка
    добавлены в `config/legal_sources.json`);
  * скачивает страницу акта и вытаскивает текст;
  * кладёт оригинал в ``original/``, текст — в ``text/``;
  * пишет метаданные в базу правовой базы ПОД ТЕМ ЖЕ идентификатором
    ``cfg:<id>``, что и реестр, — связь с реестром не теряется.

Скачано 5 из 5: НК РФ ч.1 — 1 556 201 знак, НК РФ ч.2 — 5 391 203,
КоАП — 2 711 214, УАТ — 163 366, ФЗ-16 — 136 007. Тексты — ДЕЙСТВУЮЩИЕ
редакции (только в КоАП 2052 пометки «В редакции», есть статьи 11.18, 11.19,
12.1, 12.8, глава 27). Индекс вырос с 1929 фрагментов (13 документов) до
**16 581 фрагмента (19 документов)**.

ПРО ПРОПУСК. Скачанные акты в git НЕ хранятся
(`data/knowledge/legal/.gitignore`): это десятки мегабайт, они официально
доступны и восстанавливаются одной командой. Если актов нет — тесты
метаданных и индекса ПРОПУСКАЮТСЯ с подсказкой, что запустить; проверки
инструмента и списка источников (они в git) идут всегда.
"""

import json
import os
import sqlite3
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.download_legal_pre2011 import (  # noqa: E402
    documents_to_fetch, html_to_act_text, kremlin_number,
)

SOURCES = os.path.join(PROJECT_ROOT, "config", "legal_sources.json")
LEGAL_DB = os.path.join(PROJECT_ROOT, "db", "legal_metadata.db")

#: Пять актов до 2011 года: идентификатор → (примета текста, номер в банке).
PRE2011 = {
    "cfg:nk_rf_p1": ("Налоговый кодекс", "12755"),
    "cfg:nk_rf_p2": ("Налоговый кодекс", "15925"),
    "cfg:koap_rf": ("административных правонарушениях", "17704"),
    "cfg:fz_259": ("Устав автомобильного транспорта", "26442"),
    "cfg:fz_87": ("транспортной безопасности", "24993"),
}

#: Команда восстановления базы (её же подсказывает тест при пропуске).
RESTORE_HINT = (
    "python tools/download_legal_pre2011.py --all && "
    "python tools/update_legal.py --index"
)


def source_id(doc_id: str) -> str:
    """Идентификатор записи в списке источников: ``cfg:nk_rf_p1`` → ``nk_rf_p1``."""
    return doc_id.split(":", 1)[1] if ":" in doc_id else doc_id


def read_sources() -> dict:
    with open(SOURCES, encoding="utf-8") as handle:
        return json.load(handle)


def sources_by_id() -> dict:
    """Все записи списка источников по идентификатору."""
    result = {}
    for items in (read_sources().get("categories") or {}).values():
        for item in items or []:
            if item.get("id"):
                result[item["id"]] = item
    return result


def text_path_of(doc_id: str) -> str:
    """Путь к тексту акта из базы метаданных (пустая строка, если нет базы)."""
    if not os.path.exists(LEGAL_DB):
        return ""
    connection = sqlite3.connect(LEGAL_DB)
    try:
        row = connection.execute(
            "SELECT text_path FROM legal_documents WHERE id = ?", (doc_id,)
        ).fetchone()
    except sqlite3.Error:
        return ""
    finally:
        connection.close()
    return str(row[0]) if row and row[0] else ""


def has_downloaded_acts() -> bool:
    """Скачаны ли все пять актов (иначе проверять нечего)."""
    for doc_id in PRE2011:
        path = text_path_of(doc_id)
        if not path or not os.path.exists(path):
            return False
    return True


requires_acts = pytest.mark.skipif(
    not has_downloaded_acts(),
    reason=f"акты до 2011 года не скачаны; восстановить: {RESTORE_HINT}",
)


@pytest.fixture(scope="module")
def legal_db():
    """Соединение с базой правовой базы (только чтение)."""
    if not os.path.exists(LEGAL_DB):
        pytest.skip(f"нет базы правовой базы; восстановить: {RESTORE_HINT}")
    connection = sqlite3.connect(f"file:{LEGAL_DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


# ─────────────────────────────────────────────────────────────
# 1. Список источников (в git — проверяется всегда)
# ─────────────────────────────────────────────────────────────
def test_all_five_acts_are_marked_unavailable():
    """Пять актов до 2011 года помечены как отсутствующие на портале."""
    listed = sources_by_id()

    for doc_id in PRE2011:
        key = source_id(doc_id)
        assert key in listed, f"{doc_id} пропал из списка источников"
        assert listed[key].get("unavailable") is True, (
            f"{doc_id}: акт до 2011 года должен быть помечен unavailable"
        )


def test_every_unavailable_act_has_kremlin_bank_number():
    """
    У каждого акта до 2011 года есть номер в банке Президента.

    Без номера скачивать нечего: именно по нему инструмент находит акт.
    """
    listed = sources_by_id()

    for doc_id, (_marker, bank) in PRE2011.items():
        item = listed.get(source_id(doc_id)) or {}
        assert kremlin_number(item) == bank, (
            f"{doc_id}: ожидался банк №{bank}, "
            f"в списке {kremlin_number(item)!r}"
        )


def test_tool_selects_exactly_the_pre2011_acts():
    """Инструмент видит ровно пять актов с номером в банке."""
    from core.legal_parser import LegalParser

    items = documents_to_fetch(LegalParser(sources_path=SOURCES))

    assert {item["id"] for item in items} == {
        source_id(doc_id) for doc_id in PRE2011
    }


def test_tool_can_be_limited_to_one_act():
    """`--only` оставляет только названные акты."""
    from core.legal_parser import LegalParser

    items = documents_to_fetch(LegalParser(sources_path=SOURCES),
                              only=["koap_rf"])

    assert [item["id"] for item in items] == ["koap_rf"]


# ─────────────────────────────────────────────────────────────
# 2. Разбор страницы банка (без сети)
# ─────────────────────────────────────────────────────────────
def test_html_to_act_text_extracts_paragraphs():
    """Текст акта собирается из абзацев, теги и скрипты не попадают."""
    page = (
        "<html><head><style>p { color: red }</style>"
        "<script>var x = 1;</script></head><body>"
        "<p>Федеральный закон от 08.11.2007 г. № 259-ФЗ</p>"
        "<p>Статья 1. &laquo;Устав&raquo; определяет &amp; порядок</p>"
        "<div></div><p>   </p></body></html>"
    )

    text = html_to_act_text(page)

    assert "Федеральный закон от 08.11.2007" in text
    assert "«Устав» определяет & порядок" in text   # сущности развёрнуты
    assert "color: red" not in text                 # стили выброшены
    assert "var x" not in text                      # скрипты выброшены
    assert "<p>" not in text and "&laquo;" not in text
    assert "\n\n" not in text                       # пустых строк нет


# ─────────────────────────────────────────────────────────────
# 3. Скачанные акты: метаданные и текст
# ─────────────────────────────────────────────────────────────
@requires_acts
@pytest.mark.parametrize("doc_id", sorted(PRE2011))
def test_act_has_text_and_file(legal_db, doc_id):
    """
    У акта есть текст, файл на диске и ОЖИДАЕМАЯ примета.

    Примета нужна, чтобы поймать подмену: страница банка легко может отдать
    не тот акт (или пустую страницу), и тогда в базе оказался бы чужой текст
    с правильным названием.
    """
    marker = PRE2011[doc_id][0]
    row = legal_db.execute(
        "SELECT short_title, text_path, text_chars FROM legal_documents "
        "WHERE id = ?", (doc_id,)
    ).fetchone()

    assert row is not None, f"{doc_id} нет в базе правовой базы"
    assert int(row["text_chars"] or 0) > 1000, (
        f"{doc_id}: текста всего {row['text_chars']} знаков"
    )
    assert row["text_path"] and os.path.exists(row["text_path"]), (
        f"{doc_id}: файла текста нет по пути {row['text_path']!r}"
    )

    with open(row["text_path"], encoding="utf-8", errors="replace") as handle:
        head = handle.read(200000)

    assert marker in head, (
        f"{doc_id}: в тексте нет приметы {marker!r} — похоже, скачан не тот акт"
    )


@requires_acts
def test_codes_are_consolidated_versions(legal_db):
    """
    Кодексы скачаны в ДЕЙСТВУЮЩЕЙ редакции, а не первым опубликованием.

    Проверка по пометкам «В редакции…»: у КоАП их тысячи (акт 2001 года
    менялся сотни раз). Без этого в базе лежал бы текст 2001 года, и ссылка
    на норму была бы неверной.
    """
    path = text_path_of("cfg:koap_rf")
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()

    assert text.count("В редакции") > 100, (
        "КоАП без следов редакций — вероятно, скачано первое опубликование"
    )
    # Глава 12 «Административные правонарушения в области дорожного движения»
    assert "Статья 12.1" in text
    assert "Статья 12.8" in text
    # Глава 11 «на транспорте»
    assert "Статья 11.18" in text


@requires_acts
def test_uat_contains_carrier_duties(legal_db):
    """В УАТ есть профильные для перевозчика статьи."""
    path = text_path_of("cfg:fz_259")
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()

    assert "Устав автомобильного транспорта" in text
    assert "перевозк" in text
    assert "Статья 1." in text


# ─────────────────────────────────────────────────────────────
# 4. Индекс: фрагменты актов в базе знаний
# ─────────────────────────────────────────────────────────────
@requires_acts
@pytest.mark.parametrize("doc_id", sorted(PRE2011))
def test_act_is_indexed(legal_db, doc_id):
    """
    У акта есть фрагменты в базе знаний — то есть он участвует в RAG-поиске.

    Без этого акт лежал бы на диске «мёртвым грузом»: Максимилиан его не
    находит (именно так и было до задачи: ``text_chars = 0``).
    """
    row = legal_db.execute(
        "SELECT COUNT(*) FROM knowledge_chunks WHERE doc_id = ?", (doc_id,)
    ).fetchone()

    assert row[0] > 0, f"{doc_id}: фрагментов нет, акт не проиндексирован"


@requires_acts
def test_index_grew_after_download(legal_db):
    """Индекс стал заметно больше прежних 1929 фрагментов."""
    chunks = legal_db.execute(
        "SELECT COUNT(*) FROM knowledge_chunks"
    ).fetchone()[0]
    documents = legal_db.execute(
        "SELECT COUNT(DISTINCT doc_id) FROM knowledge_chunks"
    ).fetchone()[0]

    assert chunks > 5000, f"фрагментов всего {chunks} — акты не проиндексированы"
    assert documents >= 19, f"документов в индексе {documents}"


# ─────────────────────────────────────────────────────────────
# 5. Живой поиск: норма из акта до 2011 года НАХОДИТСЯ
# ─────────────────────────────────────────────────────────────
@requires_acts
@pytest.mark.parametrize("question, doc_id", [
    ("права и обязанности перевозчика по уставу автомобильного транспорта",
     "cfg:fz_259"),
    ("требования транспортной безопасности", "cfg:fz_87"),
    ("ставка налога на добавленную стоимость", "cfg:nk_rf_p2"),
    ("административная ответственность за нарушение правил дорожного движения",
     "cfg:koap_rf"),
])
def test_search_finds_norm_from_pre2011_act(question, doc_id):
    """
    RAG находит норму ИЗ акта до 2011 года, а не только упоминание.

    Проверяется попадание документа в первые пять результатов: акт должен
    быть доступен Максимилиану, а не лежать в базе для вида.
    """
    from core.agent.rag import RAG

    hits = RAG().search(question, top_k=5)
    found = {hit.get("doc_id") for hit in hits}

    assert found, f"по вопросу {question!r} поиск ничего не нашёл"
    assert doc_id in found, (
        f"по вопросу {question!r} в топ-5 нет {doc_id}: {sorted(found)}"
    )
