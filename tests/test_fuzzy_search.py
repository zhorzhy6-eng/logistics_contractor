#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Нечёткий поиск: опечатки, дефисы, двойные буквы, падежи (Часть 0, D.2).

Что здесь проверяется
---------------------
Случаи из живого диалога, который НЕ работал:

  * «Фасстранс» → «ООО «ФассТранс»» (лишняя «с» и отсутствие пробела);
  * «Фас-Транс» / «фас транс» → та же запись (дефис вместо пробела);
  * «ромашка» → «ООО «Ромашка»» (без организационной формы);
  * «иванова» → «Иванов Иван Иванович» (падеж в запросе, которого в базе нет).

Отдельно проверяются ловушки, из-за которых нечёткий поиск легко сделать
вредным:

  * SQLite сравнивает кириллицу РЕГИСТРОЗАВИСИМО (`COLLATE NOCASE` берёт
    только латиницу), поэтому варианты регистра строятся явно;
  * «ё» в базе и «е» в запросе — разные строки, нужны варианты написания;
  * одиночные буквы («Иванов И.И.») не должны превращаться в условие
    «%и%», которое совпадает почти со всем;
  * точное совпадение не должно вытесняться «догадкой»: уровни идут от
    строгого к мягкому.

База — временная: рабочая база оператора не читается и не меняется.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import fuzzy  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 1. Чистые правила: нормализация, варианты, основы
# ─────────────────────────────────────────────────────────────

def test_normalize_strips_case_punctuation_and_yo():
    """Запрос приводится к нижнему регистру, без пунктуации и «ё»."""
    assert fuzzy.normalize_term("ООО «Фасс-Транс»") == "ооо фасс транс"
    assert fuzzy.normalize_term("Королёв") == "королев"
    assert fuzzy.normalize_term("  Иванов   И.И.  ") == "иванов и и"


def test_words_drops_single_letters():
    """
    Однобуквенные слова отбрасываются.

    «Иванов И.И.» — это поиск по «Иванов». Оставь мы «и» — в SQL ушло бы
    условие «%и%», и поиск нашёл бы вообще всех водителей.
    """
    assert fuzzy.words("Иванов И.И.") == ["иванов"]
    assert fuzzy.words("а") == []
    assert fuzzy.words("!!!") == []


def test_collapse_doubles_removes_one_letter_per_pair():
    """Удвоения снимаются по одной букве на пару."""
    assert fuzzy.collapse_doubles("фасстранс") == "фастранс"
    assert fuzzy.collapse_doubles("ооо") == "оо"
    assert fuzzy.collapse_doubles("ромашка") == "ромашка"


def test_stem_cuts_case_endings_but_keeps_short_words():
    """Падежное окончание снимается, короткие слова не усекаются."""
    assert fuzzy.stem("иванова") == "иванов"
    assert fuzzy.stem("галушкина") == "галушкин"
    assert fuzzy.stem("королев") == "корол"
    # Слишком короткие — не трогаем, иначе получится «т» вместо «том».
    assert fuzzy.stem("том") == "том"
    assert fuzzy.stem("дом") == "дом"


def test_spelling_variants_collapse_extra_letters():
    """«Фасстранс» даёт вариант «фастранс» — ради него всё и затевалось."""
    variants = fuzzy.spelling_variants("фасстранс")

    assert variants[0] == "фасстранс"
    assert "фастранс" in variants


def test_spelling_variants_do_not_double_single_letters_in_short_words():
    """Одиночная буква в коротком слове не удваивается вслепую."""
    assert fuzzy.spelling_variants("и") == ["и"]
    assert "ии" not in fuzzy.spelling_variants("и")


def test_case_variants_cover_typical_spelling():
    """
    Варианты регистра покрывают обычное написание в базе.

    Без этого «фастранс» не нашёл бы «ФассТранс»: SQLite сравнивает
    кириллицу регистрозависимо.
    """
    variants = fuzzy.case_variants("фастранс")

    assert "Фастранс" in variants
    assert "ФАСТРАНС" in variants


def test_e_yo_variants_both_directions():
    """
    «королев» и «королёв» — варианты одного слова.

    Функция работает с НОРМАЛИЗОВАННЫМ словом (после ``normalize_term``
    «ё» уже заменена на «е»), поэтому на вход подаётся «королев»: она
    добавляет вариант с «ё», чтобы найти запись, заведённую как «Королёв».
    """
    assert "королёв" in fuzzy.e_yo_variants("королев")
    # Нормализация запроса с «ё» даёт ровно то же слово.
    assert fuzzy.normalize_term("Королёв") == "королев"


def test_levels_go_from_strict_to_soft():
    """Уровни объявлены от строгого к мягкому — это и есть поведение поиска."""
    assert fuzzy.SEARCH_LEVELS[0] == "exact"
    assert fuzzy.SEARCH_LEVELS[-1] == "glued"


def test_exact_level_keeps_words_together():
    """
    На уровне exact слова ищутся по отдельности и соединяются через AND.

    Это важно: «фас транс» должно найти «Фасс Транс» (два слова), а не
    только склеенное «ФассТранс». Условие первого варианта — два слова,
    каждое со своими вариантами регистра и «е/ё».
    """
    conditions = fuzzy.level_conditions("фас транс", "exact", "full_name")

    assert conditions, "условия уровня exact не построены"
    sql, params = conditions[0]

    # Одно AND на слово, и каждая группа параметров начинается с точного
    # написания — «%фас%», затем «%транс%».
    assert sql.count(" AND ") == 1
    assert params[:1] == ["%фас%"]
    assert params[-1] == "%транс%" or "%транс%" in params
    assert all(pattern.startswith("%") and pattern.endswith("%")
               for pattern in params)


def test_glued_level_glues_spaces_and_hyphens():
    """Склейка убирает пробел и дефис: «фас транс» = «фасстранс»."""
    conditions = fuzzy.level_conditions("Фас-Транс", "glued", "full_name")
    flat = [value for _sql, values in conditions for value in values]

    assert "%фастранс%" in flat


def test_search_skips_pure_single_letter_query():
    """Запрос из одной буквы не порождает условий вовсе."""
    assert fuzzy.search_levels("а") == []


# ─────────────────────────────────────────────────────────────
# 2. Поиск на настоящей базе (временной)
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """
    Временная база с данными из живого диалога.

    Подменяется только путь: `db.database.DB_PATH` — владелец пути, его
    читает соединение через источник (см. db/connection.py).
    """
    import db.database as database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "contracts.db"))
    monkeypatch.setattr(database, "restrict_to_current_user", lambda *a, **k: True)
    monkeypatch.setattr(database, "backup_database", lambda *a, **k: None)
    database.init_database()

    database.save_organization(
        {"full_name": "ООО «ФассТранс»", "inn": "7701234567"}, is_carrier=True
    )
    database.save_organization(
        {"full_name": "ООО «Ромашка»", "inn": "7707654321"}, is_carrier=True
    )
    database.save_organization(
        {"full_name": "ИП Королёв А.А.", "inn": "770999999999"}, is_carrier=True
    )
    database.save_driver({"full_name": "Иванов Иван Иванович"})
    database.save_driver({"full_name": "Иванов Пётр Сергеевич"})
    database.save_driver({"full_name": "Галушкин Пётр Михайлович"})
    return database


def _carrier_names(query):
    """Названия перевозчиков, найденных по запросу."""
    from db.crud.organizations import search_organizations

    return [row["full_name"] for row in search_organizations(query, is_carrier=True)]


def _driver_names(query):
    """ФИО водителей, найденных по запросу."""
    from db.crud.drivers import search_drivers

    return [row["full_name"] for row in search_drivers(query)]


@pytest.mark.parametrize("query", [
    "Фасстранс",     # лишняя «с» — случай из живого диалога
    "фас транс",     # пробел там, где его нет в базе
    "Фас-Транс",     # дефис там, где его нет в базе
    "ФассТранс",     # точное написание — должно работать как раньше
    "Фасстран",      # потеряна последняя буква
])
def test_fuzzy_finds_carrier_by_typo(seeded_db, query):
    """Перевозчик «ООО «ФассТранс»» находится по любому из написаний."""
    assert "ООО «ФассТранс»" in _carrier_names(query)


def test_fuzzy_finds_carrier_without_org_form(seeded_db):
    """«ромашка» находит «ООО «Ромашка»» — организационную форму писать не нужно."""
    assert "ООО «Ромашка»" in _carrier_names("ромашка")


@pytest.mark.parametrize("query", ["королев", "Королёв", "королёв"])
def test_fuzzy_finds_carrier_with_yo(seeded_db, query):
    """«е» и «ё» — варианты написания, а не разные люди."""
    assert "ИП Королёв А.А." in _carrier_names(query)


@pytest.mark.parametrize("query", [
    "иванова",        # падеж, которого в базе нет
    "Иванов",
    "иван",
    "Иванов И.И.",    # инициалы не должны ломать поиск
])
def test_fuzzy_finds_driver_by_case(seeded_db, query):
    """ФИО водителя находится и в падеже, и с инициалами."""
    assert "Иванов Иван Иванович" in _driver_names(query)


def test_fuzzy_finds_driver_by_feminine_form(seeded_db):
    """«Галушкина» (как говорят о водителе) находит «Галушкин»."""
    assert "Галушкин Пётр Михайлович" in _driver_names("Галушкина")


def test_exact_match_is_not_replaced_by_guess(seeded_db):
    """
    Точное совпадение не вытесняется «догадкой».

    Запрос «Иванов» обязан вернуть обоих Ивановых и не подмешивать
    Галушкина: мягкие уровни включаются, только если строгий не дал
    ничего.
    """
    names = _driver_names("Иванов")

    assert set(names) == {"Иванов Иван Иванович", "Иванов Пётр Сергеевич"}


def test_unknown_query_finds_nothing(seeded_db):
    """Несуществующее название не должно «находиться» из вежливости."""
    assert _carrier_names("неттакогоооо") == []
    assert _driver_names("петровичюженко") == []


def test_driver_search_with_carrier_name_has_no_ambiguous_column(seeded_db):
    """
    Нечёткий поиск водителей работает и с названием перевозчика.

    Регресс: при ``with_carrier_name=True`` в запросе появляется JOIN с
    ``carriers``, где тоже есть ``full_name``. Без псевдонима SQLite
    отвечает «ambiguous column name: full_name», и инструмент агента
    ``search_driver`` падал с ошибкой вместо результата.
    """
    from db.crud.drivers import search_drivers

    rows = search_drivers("иванова", with_carrier_name=True)

    assert rows, "поиск с названием перевозчика ничего не вернул"
    assert "carrier_name" in rows[0]


def test_soft_deleted_records_stay_hidden(seeded_db):
    """Нечёткий путь тоже уважает мягкое удаление."""
    from db.crud.organizations import delete_organization, get_all_organizations

    carrier_id = None
    for row in get_all_organizations(is_carrier=True):
        if row["full_name"] == "ООО «Ромашка»":
            carrier_id = int(row["id"])
    assert carrier_id is not None, "перевозчик не найден в тестовой базе"

    delete_organization(carrier_id, is_carrier=True)

    assert "ООО «Ромашка»" not in _carrier_names("ромашка")


def test_search_by_levels_stops_at_first_hit():
    """
    Обход уровней прекращается на первом непустом результате.

    Проверяется на подставной функции: важно, что мягкие уровни не
    выполняются вовсе, когда строгий уже дал ответ.
    """
    from db.crud.search import search_by_levels

    seen = []

    def fetch(condition, params):
        seen.append(condition)
        return [{"id": 1, "full_name": "Иванов Иван Иванович"}]

    rows = search_by_levels(fetch, "иванова", ("full_name",), 10)

    assert len(rows) == 1
    assert len(seen) == 1, "поиск не остановился на первом уровне с результатом"


def test_search_by_levels_returns_empty_for_empty_query():
    """Пустой запрос не ищет ничего и не выполняет SQL."""
    from db.crud.search import search_by_levels

    def fetch(condition, params):  # pragma: no cover — не должен вызваться
        raise AssertionError("SQL выполнен для пустого запроса")

    assert search_by_levels(fetch, "", ("full_name",), 10) == []
