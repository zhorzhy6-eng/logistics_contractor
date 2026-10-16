#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Разбивка оплаты и суммы прописью.

Свободные функции: `split_payment` — сумма предоплаты и остаток для карты
замен шаблона, остальные — числительные прописью и служебные мелочи.

Переехали из `core/contracts/base_generator.py` (Этап 4, 16.10.2026) без
изменения логики. `BaseContractGenerator` держит на них тонкие делегаты с
прежними подписями, поэтому подклассы продолжают работать без правок
(`self._split_payment(...)`, `cls._days_to_words_genitive(...)`).
"""

import logging
from typing import Any, Dict, List, Sequence

from core.num_to_words import amount_to_words

logger = logging.getLogger("core.contract_generator")


def split_payment(total: float, prepayment: float) -> Dict[str, Any]:
    """
    Разбивает итоговую сумму договора на предоплату и остаток.

    Оператор вводит СУММУ предоплаты в рублях (вкладка «Стоимость»),
    а процент считается от итоговой стоимости — той самой, которая
    печатается в договоре как «Итого» / «Итого с НДС». Поэтому база
    процента у каждого типа своя, и передаёт её генератор.

    Второй аргумент — сумма, а НЕ процент: при изменении стоимости
    введённая руками сумма не пересчитывается, пересчитывается только
    процент. Ровно это и делает функция при каждом рендере.

    :param total: итоговая стоимость договора (база для процентов)
    :param prepayment: сумма предоплаты в рублях (0 — предоплаты нет)
    :return: словарь с восемью ключами для карты замен шаблона:

        has_prepayment          — переключатель ветки шаблона;
        prepayment_amount       — сумма предоплаты цифрами («63 000.00»);
        prepayment_amount_words — та же сумма прописью;
        prepayment_percent      — процент предоплаты («30»);
        balance_amount          — остаток цифрами («147 000.00»);
        balance_amount_words    — остаток прописью;
        balance_percent         — процент остатка («70»).

    Предоплаты нет (0, отрицательная, стоимость не задана, предоплата
    больше стоимости) — все ключи пустые, а has_prepayment = False:
    шаблон печатает старую формулировку пункта об оплате без изменений.

    Случай «предоплата больше стоимости» ловит валидатор типа, но и
    генератор не должен падать или печатать отрицательный остаток.
    """
    result: Dict[str, Any] = {
        "has_prepayment": False,
        "prepayment_amount": "",
        "prepayment_amount_words": "",
        "prepayment_percent": "0",
        "balance_amount": "",
        "balance_amount_words": "",
        "balance_percent": "0",
    }
    try:
        total = float(total or 0)
        prepayment = float(prepayment or 0)
    except (ValueError, TypeError):
        # Мусор в поле — считаем, что предоплаты нет: генерация договора
        # не должна падать из-за одного поля.
        return result

    if total <= 0 or prepayment <= 0:
        return result

    if prepayment > total:
        # Страховка: валидатор ловит, но и генератор не падает.
        logger.warning(
            "Предоплата больше стоимости — разбивка оплаты не печатается"
        )
        return result

    prepayment = round(prepayment, 2)
    balance = round(total - prepayment, 2)
    percent = round(prepayment / total * 100, 2)
    balance_percent = round(100 - percent, 2)

    def fmt_percent(value: float) -> str:
        """«30» / «33.33» — без лишних нулей."""
        return f"{value:.2f}".rstrip("0").rstrip(".")

    result.update({
        "has_prepayment": True,
        "prepayment_amount": f"{prepayment:.2f}",
        "prepayment_amount_words": lower_first(amount_to_words(prepayment)),
        "prepayment_percent": fmt_percent(percent),
        "balance_amount": f"{balance:.2f}",
        "balance_amount_words": lower_first(amount_to_words(balance)),
        "balance_percent": fmt_percent(balance_percent),
    })
    return result


def lower_first(text: str) -> str:
    """
    Первая буква — строчная.

    `core.num_to_words.amount_to_words` печатает сумму с заглавной
    («Шестьдесят три тысячи рублей 00 копеек»): в бланке сумма стоит
    после двоеточия и заглавная уместна. В строке разбивки оплаты сумма
    стоит в середине фразы («— 63 000.00 руб. (шестьдесят три тысячи
    рублей 00 копеек) — предоплата…»), поэтому здесь она приводится
    к строчной. Сама функция не меняется: на её выводе держатся бланки
    перевозки и всех клиентских типов.
    """
    if not text:
        return text
    return text[0].lower() + text[1:]


def days_to_words(days) -> str:
    """Количество дней прописью по короткому словарю (1…20, 30)."""
    try:
        days = int(days)
    except (ValueError, TypeError):
        return "десяти"
    days_map = {
        1: "одного", 2: "двух", 3: "трёх", 4: "четырёх", 5: "пяти",
        6: "шести", 7: "семи", 8: "восьми", 9: "девяти", 10: "десяти",
        11: "одиннадцати", 12: "двенадцати", 13: "тринадцати", 14: "четырнадцати",
        15: "пятнадцати", 16: "шестнадцати", 17: "семнадцати", 18: "восемнадцати",
        19: "девятнадцати", 20: "двадцати", 30: "тридцати",
    }
    return days_map.get(days, str(days))


def days_to_words_genitive(days: Any) -> str:
    """
    Количество дней (и вообще целое число) прописью в родительном падеже:
    45 → «сорока пяти», 30 → «тридцати».

    Незаменимо там, где число стоит перед считаемым словом в родительном
    падеже: «45 (сорока пяти) банковских дней». Отличие от
    days_to_words: там словарь на 1…20 и 30, а всё остальное печатается
    цифрами — для поля с границами 0…365 этого мало.

    Родительный падеж отличается только единицами, десятками и сотнями:
    «пять — пяти», «сорок — сорока», «девять — девяти», «двести —
    двухсот». Поэтому разряды собраны своими таблицами, а не выводятся из
    именительного падежа (в core.num_to_words таких форм нет).

    Больше 999 999 значение не разбирается: в бланке это заведомо не
    число дней, поэтому печатаются цифры как есть — значение не
    выдумывается. Не число (None, «мусор») — пустая строка.
    """
    try:
        number = abs(int(days))
    except (ValueError, TypeError):
        return ""

    if number > 999_999:
        # В бланке это заведомо не число дней: печатаем цифры как есть,
        # а не выдумываем форму слова.
        return str(number)
    if number == 0:
        return "ноль"

    words: List[str] = []

    #: Тысячи: «одной тысячи», «двух тысяч», «ста двадцати трёх тысяч».
    #: Само слово при числительном в родительном падеже тоже стоит в
    #: родительном: «одной тысячи», а не «одной тысяча».
    thousands = number // 1_000
    if thousands:
        words += triple_to_words(thousands, gender="female").split()
        words.append(plural_form(thousands, ("тысячи", "тысяч", "тысяч")))

    remainder = number % 1_000
    if remainder:
        words += triple_to_words(remainder).split()

    return " ".join(words)


def triple_to_words(number: int, *, gender: str = "male") -> str:
    """
    Число 1…999 прописью в РОДИТЕЛЬНОМ падеже — для
    days_to_words_genitive: «сорока пяти», «ста двадцати трёх».

    gender выбирает род единиц: «одного» / «одной» (тысячи женского
    рода). Десятки и сотни в родительном падеже от рода не зависят.
    """
    #: 0…19 — исключения: «ноль», «одного», «двух», …, «девятнадцати».
    units_male = ("ноль", "одного", "двух", "трёх", "четырёх", "пяти",
                  "шести", "семи", "восьми", "девяти", "десяти",
                  "одиннадцати", "двенадцати", "тринадцати",
                  "четырнадцати", "пятнадцати", "шестнадцати",
                  "семнадцати", "восемнадцати", "девятнадцати")
    units_female = ("ноль", "одной", "двух", "трёх", "четырёх", "пяти",
                    "шести", "семи", "восьми", "девяти", "десяти",
                    "одиннадцати", "двенадцати", "тринадцати",
                    "четырнадцати", "пятнадцати", "шестнадцати",
                    "семнадцати", "восемнадцати", "девятнадцати")
    units = units_female if gender == "female" else units_male

    #: Десятки 20…90 и сотни 100…900 — в родительном падеже.
    tens_genitive = ("", "", "двадцати", "тридцати", "сорока",
                     "пятидесяти", "шестидесяти", "семидесяти",
                     "восьмидесяти", "девяноста")
    hundreds_genitive = ("", "ста", "двухсот", "трёхсот", "четырёхсот",
                         "пятисот", "шестисот", "семисот", "восьмисот",
                         "девятисот")

    words: List[str] = []

    hundreds = number // 100
    if hundreds:
        words.append(hundreds_genitive[hundreds])

    remainder = number % 100
    if 10 <= remainder <= 19:
        # Подростковые числительные в родительном падеже не меняются.
        words.append(units[remainder])
    else:
        tens = remainder // 10
        if tens:
            words.append(tens_genitive[tens])
        if remainder % 10:
            words.append(units[remainder % 10])

    return " ".join(words)


def plural_form(number: int, forms: Sequence[str]) -> str:
    """
    Форма слова по числу: (1, «тысяча/тысячи/тысяч») → «тысяча».

    Порядок форм — «одна / две-четыре / пять и больше»; исключение —
    11…14, которые всегда идут по третьей форме.
    """
    remainder = abs(number) % 100
    if 11 <= remainder <= 14:
        return forms[2]
    remainder %= 10
    if remainder == 1:
        return forms[0]
    if 2 <= remainder <= 4:
        return forms[1]
    return forms[2]
