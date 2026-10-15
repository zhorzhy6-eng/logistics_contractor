#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Маскирование VIN в core/pseudonymizer.py (задача 2).

Задание исходило из того, что VIN не маскируется. **Проверка показала
обратное**: VIN маскируется с самого первого снимка проекта
(`git log -S"_VIN_RE"` → коммит `c1f4f11`), и в `tests/test_pseudonymizer.py`
он уже входит в набор проверяемых типов.

Что есть в коде:

  * регулярка ``_VIN_RE`` — 17 символов, латиница без ``I``/``O``/``Q``
    (класс ``[A-HJ-NPR-Za-hj-npr-z0-9]``) плюс цифры, с запретом буквы или
    цифры сразу до и после совпадения;
  * ``_find_vin`` требует хотя бы одну БУКВУ: 17-значное число — не VIN;
  * приоритет ``VIN = 85`` — выше паспорта, ФИО, адреса и телефона, но ниже
    СНИЛС и почты (специализированные форматы перебивают общие);
  * ``VIN`` есть в ``TOKEN_TYPES``, поэтому в журнал уходит только счётчик
    («VIN=1»), а не значение.

Поэтому задача свелась к тому, чтобы ЗАКРЕПИТЬ поведение тестами, а не
менять `core/pseudonymizer.py` (он в списке «Ask first» по `AGENTS.md` § 4).
Отдельно зафиксировано, чего маскирование НЕ делает — это осознанные
границы, а не недоделки:

  * **VIN, разорванный пробелами** (``XW8 ZZZ 61Z JG0 12345``) не
    маскируется: пробелы внутри VIN в документах не встречаются, а
    «собрать обратно» 17 символов из текста с произвольными пробелами —
    значит маскировать чужие числа;
  * **VIN, вклеенный в латинское слово** (``codeEC3TEUMB0T0002608end``) не
    маскируется: границы проверяются по латинице и цифрам — ровно тем
    символам, из которых VIN состоит. Кириллица рядом границей не считается
    (``СловоEC3TEUMB0T0002608слово`` маскируется) — иначе строгая проверка
    ломала бы разбор соседних русских слов.

Если решение изменится (маскировать VIN не нужно), выключается одной
строкой — см. ``test_vin_type_is_in_token_types`` и
``Pseudonymizer.anonymize``: VIN уходит из ``_DETECTORS``.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.pseudonymizer import Pseudonymizer


#: Корректные VIN: 17 символов, без I/O/Q.
VIN_JETOUR = "EC3TEUMB0T0002608"
VIN_VW = "XW8ZZZ61ZJG012345"
VIN_LOWER = "xw8zzz61zjg012345"


@pytest.fixture(autouse=True)
def natasha_off(monkeypatch):
    """Только регулярки: слой Natasha в тестах выключен (см. conftest)."""
    import core.pseudonymizer as module

    monkeypatch.setattr(module, "_NATASHA_AVAILABLE", False)
    monkeypatch.setattr(module, "_NATASHA", {})


@pytest.fixture
def mask():
    """Псевдонимизатор с маскированием адресов (по умолчанию)."""
    return Pseudonymizer()


def _tokens(mapping):
    """Имена типов токенов без номеров: {'VIN', 'PERSON'}."""
    return {
        token.strip("<>").rsplit("_", 1)[0]
        for token in mapping
    }


# ─────────────────────────────────────────────────────────────
# VIN маскируется
# ─────────────────────────────────────────────────────────────
def test_vin_after_keyword_is_masked(mask):
    """VIN в тексте документа закрывается плейсхолдером."""
    text = "Автомобиль EC3TEUMB0T0002608, цвет белый"

    masked, mapping = mask.anonymize(text)

    assert VIN_JETOUR not in masked
    assert "<<VIN_1>>" in masked
    assert mapping == {"<<VIN_1>>": VIN_JETOUR}


def test_lowercase_vin_is_masked(mask):
    """VIN строчными буквами маскируется так же, как заглавными."""
    masked, mapping = mask.anonymize(f"vin: {VIN_LOWER}")

    assert VIN_LOWER not in masked
    assert _tokens(mapping) == {"VIN"}


def test_several_vins_get_separate_tokens(mask):
    """Несколько VIN в одном тексте — разные плейсхолдеры."""
    text = f"ТС1 {VIN_VW}, ТС2 {VIN_JETOUR}"

    masked, mapping = mask.anonymize(text)

    assert VIN_VW not in masked and VIN_JETOUR not in masked
    assert len(mapping) == 2
    assert _tokens(mapping) == {"VIN"}


def test_masked_vin_is_restored_back(mask):
    """Восстановление возвращает исходный текст символ в символ."""
    text = f"VIN {VIN_VW}, госномер А123ВС77, водитель Иванов Иван Иванович"

    masked, mapping = mask.anonymize(text)

    assert mask.restore(masked, mapping) == text


# ─────────────────────────────────────────────────────────────
# Границы: что НЕ маскируется и почему
# ─────────────────────────────────────────────────────────────
def test_vin_broken_by_spaces_is_not_masked(mask):
    """
    VIN с пробелами внутри не маскируется — осознанная граница.

    В документах VIN идёт одной строкой; «собрать» 17 символов через
    произвольные пробелы значило бы маскировать чужие числа.
    """
    text = "VIN XW8 ZZZ 61Z JG0 12345"

    masked, mapping = mask.anonymize(text)

    assert mapping == {}
    assert masked == text


def test_vin_inside_a_latin_word_is_not_masked(mask):
    """
    VIN, вклеенный в латинское слово, не маскируется.

    Границы проверяются по латинице и цифрам
    (``(?<![A-Za-z0-9])``): это ровно те символы, из которых состоит VIN.
    """
    text = f"Слово{VIN_JETOUR}слово"  # кириллица по краям — см. ниже

    masked, mapping = mask.anonymize(f"code{VIN_JETOUR}end")

    assert mapping == {}, f"латинские края должны мешать: {masked}"
    assert masked == f"code{VIN_JETOUR}end"


def test_vin_glued_to_cyrillic_is_masked(mask):
    """
    Кириллица вплотную к VIN границей НЕ считается — и это ожидаемо.

    VIN состоит из латиницы и цифр, поэтому «склеенным со словом» он может
    быть только через латиницу; кириллица рядом — обычный текст («VIN:…»,
    «Автомобиль …»). Проверка границ по ``[A-Za-z0-9]`` оставляет такие
    случаи маскируемыми, и это осознанный выбор: строгий ``\\w`` ловил бы
    кириллицу и ломал бы разбор рядом стоящих слов.
    """
    text = f"Слово{VIN_JETOUR}слово"

    masked, mapping = mask.anonymize(text)

    assert mapping == {"<<VIN_1>>": VIN_JETOUR}
    assert masked == "Слово<<VIN_1>>слово"


def test_seventeen_digits_are_not_a_vin(mask):
    """17-значное число — не VIN: у VIN обязана быть хотя бы одна буква."""
    text = "Номер 12345678901234567 в реестре"

    masked, mapping = mask.anonymize(text)

    assert _tokens(mapping) == set()


def test_vin_with_forbidden_letters_is_not_masked(mask):
    """Буквы I, O, Q в VIN запрещены стандартом — такое не маскируется."""
    for bad in ("XW8ZZZ61ZJG0I2345", "XW8ZZZ61ZJG0O2345", "XW8ZZZ61ZJG0Q2345"):
        masked, mapping = mask.anonymize(f"VIN {bad}")

        assert mapping == {}, f"замаскировано то, что VIN не является: {bad}"


# ─────────────────────────────────────────────────────────────
# VIN и соседние сущности
# ─────────────────────────────────────────────────────────────
def test_vin_does_not_swallow_neighbour_entities(mask):
    """VIN не съедает стоящие рядом ИНН, СНИЛС и госномер."""
    text = (
        f"VIN {VIN_VW}, ИНН 7707083893, "
        f"СНИЛС 112-233-445 95, госномер А123ВС77"
    )

    masked, mapping = mask.anonymize(text)

    assert _tokens(mapping) == {"VIN", "INN", "SNILS", "GOSNOMER"}
    assert len(mapping) == 4


def test_vin_type_is_in_token_types():
    """
    VIN — объявленный тип токена.

    Это и есть «одна строка» для решения: убрать ``"VIN"`` из
    ``TOKEN_TYPES`` и ``_DETECTORS`` — маскирование выключится.
    """
    from core.pseudonymizer import TOKEN_TYPES, _PRIORITY

    assert "VIN" in TOKEN_TYPES
    # Приоритет выше паспорта и ФИО, ниже СНИЛС: специализированное важнее.
    assert _PRIORITY["VIN"] > _PRIORITY["PERSON"]
    assert _PRIORITY["VIN"] > _PRIORITY["PASSPORT"]
    assert _PRIORITY["VIN"] < _PRIORITY["SNILS"]


def test_vin_never_gets_into_the_log(mask, caplog):
    """
    В журнал уходит только счётчик типов, значения VIN — никогда.

    Требование `AGENTS.md` § 3: логи без ПДн. VIN — идентификатор
    конкретной машины, поэтому он в том же списке, что ФИО и паспорт.
    """
    import logging

    with caplog.at_level(logging.DEBUG, logger="core.pseudonymizer"):
        mask.anonymize(f"VIN {VIN_VW} и {VIN_JETOUR}")

    assert VIN_VW not in caplog.text
    assert VIN_JETOUR not in caplog.text
    assert "VIN=2" in caplog.text
