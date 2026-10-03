#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Конвертер чисел в сумму прописью на русском языке.
Формат: "Четыреста тысяч рублей 00 копеек"

Копейки прописываются ТЕКСТОМ (женский род), а НОЛЬ копеек — цифрами
«00 копеек»: так печатают в договорах и так в образце Формики —
«75 000,00 руб. (семьдесят пять тысяч рублей 00 копеек)».
    "Сорок шесть тысяч восемьсот восемьдесят пять рублей двадцать пять копеек"
    "Двести шестьдесят тысяч рублей 00 копеек"
"""

import logging

logger = logging.getLogger("core.num_to_words")


# ─────────────────────────────────────────────────────────────
# Словари
# ─────────────────────────────────────────────────────────────

UNITS_MALE = [
    "", "один", "два", "три", "четыре", "пять",
    "шесть", "семь", "восемь", "девять",
]

UNITS_FEMALE = [
    "", "одна", "две", "три", "четыре", "пять",
    "шесть", "семь", "восемь", "девять",
]

TEENS = [
    "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать",
    "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать",
]

TENS = [
    "", "", "двадцать", "тридцать", "сорок", "пятьдесят",
    "шестьдесят", "семьдесят", "восемьдесят", "девяносто",
]

HUNDREDS = [
    "", "сто", "двести", "триста", "четыреста", "пятьсот",
    "шестьсот", "семьсот", "восемьсот", "девятьсот",
]

# Тысячи (женский род)
THOUSANDS_FORMS = {
    "one": "тысяча",
    "few": "тысячи",
    "many": "тысяч",
}

# Рубли
RUBLES_FORMS = {
    "one": "рубль",
    "few": "рубля",
    "many": "рублей",
}

# Копейки (женский род)
KOPEKS_FORMS = {
    "one": "копейка",
    "few": "копейки",
    "many": "копеек",
}


# ─────────────────────────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────────────────────────

def _plural_form(n: int, forms: dict) -> str:
    """
    Выбирает форму слова по числу.
    forms = {"one": ..., "few": ..., "many": ...}
    """
    n = abs(n) % 100
    if 11 <= n <= 19:
        return forms["many"]
    n = n % 10
    if n == 1:
        return forms["one"]
    if 2 <= n <= 4:
        return forms["few"]
    return forms["many"]


def _triple_to_words(n: int, female: bool = False) -> str:
    """
    Преобразует число от 0 до 999 в слова.
    female=True — женский род (одна, две).
    """
    if n == 0:
        return ""

    words = []

    # Сотни
    hundreds = n // 100
    if hundreds > 0:
        words.append(HUNDREDS[hundreds])

    # Десятки и единицы
    remainder = n % 100
    if 10 <= remainder <= 19:
        words.append(TEENS[remainder - 10])
    else:
        tens = remainder // 10
        units = remainder % 10

        if tens > 0:
            words.append(TENS[tens])
        if units > 0:
            if female:
                words.append(UNITS_FEMALE[units])
            else:
                words.append(UNITS_MALE[units])

    return " ".join(words)


def _integer_to_words(n: int) -> str:
    """
    Преобразует целое число (рубли) в пропись.
    Например: 1234567 → "Один миллион двести тридцать четыре тысячи пятьсот шестьдесят семь"
    """
    if n == 0:
        return "Ноль"

    parts = []

    # Миллионы
    millions = n // 1_000_000
    if millions > 0:
        millions_words = _triple_to_words(millions, female=False)
        millions_form = _plural_form(millions, {
            "one": "миллион",
            "few": "миллиона",
            "many": "миллионов",
        })
        parts.append(f"{millions_words} {millions_form}")

    # Тысячи
    thousands = (n // 1_000) % 1_000
    if thousands > 0:
        thousands_words = _triple_to_words(thousands, female=True)
        thousands_form = _plural_form(thousands, THOUSANDS_FORMS)
        parts.append(f"{thousands_words} {thousands_form}")

    # Единицы
    units = n % 1_000
    if units > 0:
        units_words = _triple_to_words(units, female=False)
        parts.append(units_words)

    result = " ".join(parts).strip()
    return result[0].upper() + result[1:] if result else ""


# ─────────────────────────────────────────────────────────────
# Основная функция
# ─────────────────────────────────────────────────────────────

def amount_to_words(amount: float, currency: str = "rub") -> str:
    """
    Преобразует сумму в пропись.

    Копейки прописываются ТЕКСТОМ (женский род), ноль копеек — цифрами:
    "Ноль рублей 00 копеек", "Сто рублей 00 копеек".

    :param amount: сумма, например 196721.31
    :param currency: "rub" — рубли/копейки
    :return: "Сто девяносто шесть тысяч семьсот двадцать один рубль тридцать одна копейка"
    """
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return ""

    # Округляем до копеек
    amount = round(amount, 2)

    # Знак
    negative = amount < 0
    amount = abs(amount)

    # Целая часть (рубли)
    rubles = int(amount)

    # Копейки (округляем во избежание 99.999999)
    kopeks = int(round((amount - rubles) * 100))
    if kopeks == 100:
        rubles += 1
        kopeks = 0

    # Рубли прописью
    if currency == "rub":
        rubles_words = _integer_to_words(rubles)
        rubles_form = _plural_form(rubles, RUBLES_FORMS)
        kopeks_form = _plural_form(kopeks, KOPEKS_FORMS)

        # ── Копейки прописью (женский род) ──
        # Ноль копеек печатается цифрами — «00 копеек»: так в образце
        # Формики и так принято в договорах. Ненулевые копейки остаются
        # прописью («двадцать пять копеек»).
        if kopeks == 0:
            kopeks_words = "00"
        else:
            kopeks_words = _triple_to_words(kopeks, female=True).lower()

        result = f"{rubles_words} {rubles_form} {kopeks_words} {kopeks_form}"
    else:
        # Универсальный режим — только число прописью
        result = _integer_to_words(rubles)

    if negative:
        result = "Минус " + result

    return result


# ─────────────────────────────────────────────────────────────
# Тестирование
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        0.00,
        1.00,
        2.00,
        5.00,
        11.00,
        21.00,
        22.00,
        25.00,
        100.00,
        101.00,
        1000.00,
        1001.00,
        5000.00,
        88000.00,
        196721.31,
        213114.75,
        260000.00,
        390000.50,
        400000.00,
        46885.25,
        488000.00,
        1234567.89,
    ]

    for t in tests:
        print(f"{t:>12.2f} → {amount_to_words(t)}")