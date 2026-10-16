#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Слова-команды в маске ФИО (задача 4): «Удали» ≠ <<PERSON_1>>.

Симптом из задания: слово-команда принимается за ФИО и закрывается
плейсхолдером. Проверка на живом коде показала, ЧТО именно происходило:

  * «Удали» САМО ПО СЕБЕ не маскировалось и раньше — одиночное слово без
    отчества и без маркера ФИО общее правило не принимает;
  * ломалось ДРУГОЕ, и это хуже: рядом с командой стояла настоящая фамилия,
    регулярка ФИО брала ДВА слова подряд, и маркер контекста («ФИО:»,
    «Водитель», «Директор») делал такую пару допустимой. Маска закрывала
    командой и фамилией ОДИН токен:
    «ФИО: Удали Иванов» → «ФИО: <<PERSON_1>>».
    То есть фамилия уезжала в облако под чужим именем, а оператор в ответе
    видел «Удали» — обе стороны теряли смысл.

Что сделано (core/pseudonymizer.py):

  * ``_service_words()`` — берёт команды, вопросы и слова-роли из ОБЩЕЙ
    таблицы `core/agent/words.py` (она уже одна на весь проект);
  * ``_SERVICE_NOUNS`` — разделы программы и реквизиты («Договор», «Заявка»,
    «Аренда», «Машина», «Клиент», «Груз», «Маршрут», «Сумма», «Цена»,
    «Дата», «Номер», «Адрес») вместе с падежными формами;
  * ``person_stop_words()`` — стоп-лист из обоих наборов;
  * ``_is_service_word()`` — сверяет ПЕРВОЕ слово совпадения;
  * ``_tail_after_service_word()`` + ``_accept_tail()`` — если совпадение
    начинается со служебного слова, маскируется только ХВОСТ («Удали
    Иванова» → «<<PERSON_1>>» на одну «Иванова»);
  * то же для слоя Natasha (`_find_person_natasha`): NER тоже склеивал
    команду с фамилией в один span.

Сверка идёт по слову ЦЕЛИКОМ, а не по основе: основы команд ловят
настоящие фамилии («Показеев» → «покаж», «Заполянский» → «заполн»,
«Удальцов» → «удал»), и пропущенная фамилия дороже лишнего токена.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.pseudonymizer import (
    Pseudonymizer,
    _is_service_word,
    person_stop_words,
    reset_person_stop_words,
)


#: Слова-команды из задания.
COMMANDS = (
    "Удали", "Удалите", "Создай", "Создайте", "Найди", "Найдите",
    "Покажи", "Покажите", "Заполни", "Заполните", "Открой", "Откройте",
    "Закрой", "Закройте", "Сохрани", "Сохраните", "Проверь", "Проверьте",
    "Сравни", "Сравните", "Ищи", "Ищите", "Дай", "Дайте", "Добавь",
    "Добавьте", "Убери", "Уберите", "Переделай", "Переделайте",
    "Измени", "Измените",
)

#: Существительные из задания: тоже похожи на фамилию, но ею не являются.
NOUNS = (
    "Договор", "Заявка", "Аренда", "Машина", "Водитель", "Клиент",
    "Груз", "Маршрут", "Сумма", "Цена", "Дата", "Номер", "Адрес",
)

#: Маркеры, которые включают маскирование ФИО рядом с ними.
MARKERS = ("ФИО:", "Водитель", "Директор", "Заказчик", "Перевозчик")


@pytest.fixture(autouse=True)
def natasha_off(monkeypatch):
    """Только регулярки: слой Natasha в тестах выключен (см. conftest)."""
    import core.pseudonymizer as module

    monkeypatch.setattr(module, "_NATASHA_AVAILABLE", False)
    monkeypatch.setattr(module, "_NATASHA", {})
    reset_person_stop_words()
    yield
    reset_person_stop_words()


@pytest.fixture
def mask():
    return Pseudonymizer()


# ─────────────────────────────────────────────────────────────
# Симптом из задания
# ─────────────────────────────────────────────────────────────
def test_delete_alone_is_not_masked(mask):
    """«Удали» — не ФИО: закрывать нечего."""
    masked, mapping = mask.anonymize("Удали")

    assert mapping == {}
    assert masked == "Удали"


def test_command_with_surname_masks_only_the_surname(mask):
    """
    «Удали Иванова» → маскируется ТОЛЬКО «Иванова».

    Раньше оба слова закрывались ОДНИМ токеном: оператор в восстановленном
    ответе видел «Удали» вместо фамилии, а модель — токен вместо команды.
    """
    masked, mapping = mask.anonymize("Удали Иванова")

    assert masked == "Удали <<PERSON_1>>"
    assert list(mapping.values()) == ["Иванова"]


def test_marker_does_not_glue_command_to_surname(mask):
    """Маркер ФИО рядом — не повод закрыть команду вместе с фамилией."""
    masked, mapping = mask.anonymize("ФИО: Удали Иванов")

    assert masked == "ФИО: Удали <<PERSON_1>>"
    assert list(mapping.values()) == ["Иванов"]


def test_full_name_after_command_is_masked_as_one(mask):
    """Настоящее ФИО после команды закрывается ЦЕЛИКОМ."""
    masked, mapping = mask.anonymize("Удали Иванов Иван Иванович")

    assert masked == "Удали <<PERSON_1>>"
    assert list(mapping.values()) == ["Иванов Иван Иванович"]


# ─────────────────────────────────────────────────────────────
# Служебное слово не маскируется НИ В КАКОМ окружении
# ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("word", COMMANDS)
def test_command_word_is_never_masked(mask, word):
    """Каждое слово-команда остаётся открытым текстом при любом маркере."""
    for marker in MARKERS:
        masked, mapping = mask.anonymize(f"{marker} {word} Иванов")

        assert word in masked, f"команда замаскирована: {masked}"
        for value in mapping.values():
            assert not value.casefold().startswith(word.casefold()), (
                f"«{word}» уехало в маску вместе с «{value}»"
            )


@pytest.mark.parametrize("word", NOUNS)
def test_service_noun_is_never_masked(mask, word):
    """«Договор № 5», «Адрес доставки», «Сумма 80000» — не люди."""
    for marker in MARKERS:
        masked, mapping = mask.anonymize(f"{marker} {word} Иванов")

        assert word in masked, f"существительное замаскировано: {masked}"
        for value in mapping.values():
            assert not value.casefold().startswith(word.casefold()), (
                f"«{word}» уехало в маску вместе с «{value}»"
            )


def test_service_word_is_recognised_by_first_word_only(mask):
    """
    Служебным считается ПЕРВОЕ слово, а не любое.

    «Иванов Иван» — человек, даже если второе слово похоже на роль;
    «Сумма Договора» — реквизит, потому что первое слово реквизит.
    """
    assert _is_service_word("Удали Иванов") is True
    assert _is_service_word("Сумма Договора") is True
    assert _is_service_word("Иванов Удали") is False
    assert _is_service_word("Иванов Иван Иванович") is False


# ─────────────────────────────────────────────────────────────
# Настоящие ФИО не пострадали
# ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text, value", [
    ("Иванов Иван Иванович", "Иванов Иван Иванович"),
    ("Водитель Сергеев Сергей", "Сергеев Сергей"),
    ("Директор Петров Пётр Петрович", "Петров Пётр Петрович"),
    ("ФИО: Сидоров Сидор", "Сидоров Сидор"),
])
def test_real_names_are_still_masked(mask, text, value):
    """Стоп-лист не задел настоящие ФИО."""
    masked, mapping = mask.anonymize(text)

    assert value in mapping.values(), f"{text!r} -> {masked!r} {mapping}"
    assert value not in masked


def test_surname_alone_without_context_is_not_masked(mask):
    """
    Одиночная фамилия без команды и без маркера не маскируется — как раньше.

    Это НЕ регресс задачи: правило «одно слово — только с контекстом»
    действовало до неё, и здесь ничего не менялось. Контекст появляется
    либо как маркер («Водитель Иванов»), либо как отчество.
    """
    masked, mapping = mask.anonymize("Приехал Иванов")

    assert mapping == {}
    assert masked == "Приехал Иванов"


# ─────────────────────────────────────────────────────────────
# Стоп-лист: состав и устойчивость к ошибкам
# ─────────────────────────────────────────────────────────────
def test_stop_list_covers_shared_word_table():
    """
    Стоп-лист включает общую таблицу служебных слов агента.

    Таблица одна на проект (`core/agent/words.py`): если её пополнят,
    стоп-лист маскирования пополнится сам — расхождение поймает этот тест.
    Модуль берётся тем же способом, что и в ядре (``load_words_module``):
    обычный импорт `core.agent.words` тянет за собой весь пакет агента.
    """
    import core.pseudonymizer as module

    words_module = module.load_words_module()
    assert words_module is not None, "таблица служебных слов не загрузилась"

    stop = person_stop_words()
    missing = [
        word for word in (
            tuple(words_module.COMMAND_WORDS) + tuple(words_module.QUESTION_WORDS)
            + tuple(words_module.ASK_WORDS) + tuple(words_module.ROLE_WORDS)
        )
        if word.casefold() not in stop
    ]

    assert not missing, f"в стоп-листе нет служебных слов: {missing}"


def test_stop_list_survives_missing_word_module(monkeypatch):
    """
    Без таблицы агента маскирование продолжает работать.

    Ядро обезличивания не должно зависеть от пакета агента: если таблицу не
    удалось загрузить, остаётся собственный набор (разделы и реквизиты).
    """
    import core.pseudonymizer as module

    monkeypatch.setattr(module, "load_words_module", lambda: None)
    reset_person_stop_words()

    stop = person_stop_words()

    assert "сумма" in stop and "договор" in stop
    assert module.Pseudonymizer().anonymize("Сумма 80000")[1] == {}


def test_round_trip_is_intact(mask):
    """Восстановление возвращает исходный текст, включая команды."""
    text = "ФИО: Удали Иванов, сумма 80000, адрес г. Москва, ул. Ленина, д. 5"

    masked, mapping = mask.anonymize(text)

    assert mask.restore(masked, mapping) == text
