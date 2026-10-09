#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Род ФИО по отчеству (ШАГ «Полные стороны + склонение с учётом рода»).

Зачем
-----
В бланках перевозки причастия и местоимения стоят в мужском роде жёстко:
«в лице … действующего», «именуемый в дальнейшем «Заказчик»». У директора
или предпринимателя-женщины в договоре выходило «Сидорова Анна Петровна,
действующего…» и «именуемый».

Пол берётся из ОТЧЕСТВА: «-овна / -евна / -ична / -инична» — женщина,
иначе мужчина. Отчества нет — мужской род (форма по умолчанию в бланке).

Данные синтетические, ПДн нет: имена выдуманы для теста.
"""

import pytest

from core.contracts.ru_morphology import (
    acting_by_gender,
    detect_gender,
    pronoun_by_gender,
)


# ─────────────────────────────────────────────────────────────
# Пол по отчеству
# ─────────────────────────────────────────────────────────────

def test_male_by_patronymic_ovich():
    """«-ович» — мужское отчество."""
    assert detect_gender("Ахмедов Тимур Артурович") == "male"


def test_male_by_patronymic_evich():
    """«-евич» — тоже мужское."""
    assert detect_gender("Смирнов Сергей Сергеевич") == "male"


def test_female_by_patronymic_ovna():
    """«-овна» — женское отчество."""
    assert detect_gender("Иванова Мария Ивановна") == "female"


def test_female_by_patronymic_evna():
    """«-евна» — женское."""
    assert detect_gender("Соловьёва Софья Андреевна") == "female"


def test_female_by_patronymic_ichna():
    """«-ична» — женское (редкое, но встречается)."""
    assert detect_gender("Кузьмина Ольга Ильинична") == "female"
    assert detect_gender("Петрова Анна Лукична") == "female"


def test_female_by_patronymic_inichna():
    """«-инична» — женское."""
    assert detect_gender("Никитина Дарья Ильинична") == "female"


def test_male_by_default():
    """Отчества нет — мужской род: форма по умолчанию в бланке."""
    assert detect_gender("Петров Пётр") == "male"
    assert detect_gender("Иванов") == "male"


def test_empty_returns_male():
    """Пустое значение — тоже мужской род, без исключений."""
    assert detect_gender("") == "male"
    assert detect_gender("   ") == "male"
    assert detect_gender(None) == "male"


def test_male_with_ip_prefix_stripped():
    """Приставка «ИП » снимается до разбора: отчество остаётся третьим словом."""
    assert detect_gender("ИП Смирнов Алексей Николаевич") == "male"


def test_female_with_ip_prefix_stripped():
    """Полная приставка снимается так же, как короткая."""
    assert detect_gender(
        "Индивидуальный предприниматель Смирнова Елена Владимировна"
    ) == "female"
    assert detect_gender("ИП Смирнова Елена Владимировна") == "female"


def test_ip_prefix_is_not_a_patronymic():
    """Приставка «ИП» не мешает: «Смирнова Елена Владимировна» — женщина.

    Без снятия приставки третьим словом было бы «Елена», и род вышел бы
    мужским — ровно тот баг, из-за которого в договоре печаталось
    «именуемый».
    """
    full = "Индивидуальный предприниматель Смирнова Елена Владимировна"
    assert detect_gender(full) == "female"

    # Приставка из двух слов: отчества в ФИО нет, поэтому род — по умолчанию.
    assert detect_gender("ИП Смирнова Елена") == "male"


# ─────────────────────────────────────────────────────────────
# Причастия по роду
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("gender,expected", [
    ("male", "именуемый"),
    ("female", "именуемая"),
    ("", "именуемое"),
    ("неизвестно", "именуемое"),
])
def test_pronoun_by_gender(gender, expected):
    """«именуемый» / «именуемая» / «именуемое» (нейтральная форма)."""
    assert pronoun_by_gender(gender) == expected


@pytest.mark.parametrize("gender,expected", [
    ("male", "действующий"),
    ("female", "действующая"),
    ("", "действующее"),
    ("неизвестно", "действующее"),
])
def test_acting_by_gender(gender, expected):
    """«действующий» / «действующая» / «действующее»."""
    assert acting_by_gender(gender) == expected
