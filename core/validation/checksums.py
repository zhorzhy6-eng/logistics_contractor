#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Контрольные суммы реквизитов: ИНН, ОГРН, ОГРНИП, СНИЛС, расчётный счёт.

Зачем отдельный модуль
----------------------
Алгоритмы уже были в проекте, но жили в `core/pseudonymizer.py` и решали
одну задачу: маскировать только те номера, которые похожи на настоящие
(у вымышленного ИНН контрольная сумма не сходится, и тратить на него
плейсхолдер незачем). Валидатору договора нужен ровно тот же расчёт —
и держать его копию значит однажды получить два расходящихся правила.
Поэтому расчёт живёт здесь, а `core/pseudonymizer.py` и
`core/validation/validator.py` берут его отсюда.

Формат и контрольная сумма — РАЗНЫЕ проверки
--------------------------------------------
Формат («10 цифр», «13 цифр») проверяет валидатор регуляркой и считает
нарушение ошибкой. Контрольная сумма — только ЗАМЕЧАНИЕ: в ЕГРЮЛ
встречаются нестандартные реквизиты, и оператор обязан иметь возможность
создать договор с ними (`AGENTS.md` § 4). Поэтому функции этого модуля
отвечают на один вопрос — «сходится ли сумма?» — и ничего не печатают и не
логируют.

Значения нормализуются: пробелы, дефисы и скобки отбрасываются, поэтому
`inn_checksum_ok("770-123-4560")` и `inn_checksum_ok("7701234560")` —
это один и тот же вопрос. Пустое значение и `None` дают `False`.
"""

from typing import Any

__all__ = [
    "account_checksum_ok",
    "inn_checksum_ok",
    "ogrn_checksum_ok",
    "ogrnip_checksum_ok",
    "snils_checksum_ok",
]


def _digits(value: Any) -> str:
    """Только цифры значения: «770 123-4560» → «7701234560»."""
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def inn_checksum_ok(inn: str) -> bool:
    """
    Контрольная сумма ИНН (алгоритм ФНС) — 10 или 12 цифр.

    10 цифр — организация: сумма первых девяти цифр с весами
    (2, 4, 10, 3, 5, 9, 4, 6, 8), остаток по модулю 11, младшая цифра —
    десятая цифра ИНН.
    12 цифр — ИП: две суммы (веса на 11 и на 12 цифр), одиннадцатая и
    двенадцатая цифры проверяются обе.
    """
    digits = _digits(inn)

    if len(digits) == 10:
        weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        check = sum(int(d) * w for d, w in zip(digits, weights)) % 11 % 10
        return check == int(digits[9])

    if len(digits) == 12:
        weights_11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        weights_12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        check_11 = sum(int(d) * w for d, w in zip(digits, weights_11)) % 11 % 10
        check_12 = sum(int(d) * w for d, w in zip(digits, weights_12)) % 11 % 10
        return check_11 == int(digits[10]) and check_12 == int(digits[11])

    return False


def ogrn_checksum_ok(ogrn: str) -> bool:
    """
    Контрольная сумма ОГРН — 13 цифр.

    Первые 12 цифр как число, остаток по модулю 11, младшая цифра остатка —
    тринадцатая цифра. Проверка нужна потому, что ОГРН и ИНН часто путают:
    оба длинные и оба из цифр.
    """
    digits = _digits(ogrn)
    if len(digits) != 13:
        return False
    check = int(digits[:12]) % 11 % 10
    return check == int(digits[12])


def ogrnip_checksum_ok(ogrnip: str) -> bool:
    """Контрольная сумма ОГРНИП — 15 цифр: остаток по модулю 13."""
    digits = _digits(ogrnip)
    if len(digits) != 15:
        return False
    check = int(digits[:14]) % 13 % 10
    return check == int(digits[14])


def snils_checksum_ok(snils: str) -> bool:
    """
    Контрольная сумма СНИЛС — 11 цифр.

    Девять цифр номера складываются с весами от 9 до 1; остаток по модулю
    101 — контрольное число (100 превращается в 0). СНИЛС бывает записан
    как «112-233-445 95», поэтому дефисы и пробел значения не меняют.
    """
    digits = _digits(snils)
    if len(digits) != 11:
        return False

    total = sum(int(d) * (9 - i) for i, d in enumerate(digits[:9]))
    check = total % 101
    if check == 100:
        check = 0
    return check == int(digits[9:])


#: Веса для контрольного разряда счёта (Положение Банка России о правилах
#: ведения бухгалтерского учёта): 23 разряда — три из БИК и двадцать из счёта.
_ACCOUNT_WEIGHTS = (
    7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1,
)


def account_checksum_ok(account: str, bik: str,
                        correspondent: bool = False) -> bool:
    """
    Контрольная сумма расчётного счёта по БИК.

    Проверяются 23 разряда: три разряда БИК плюс двадцать разрядов счёта.
    Для расчётного счёта берутся ПОСЛЕДНИЕ три цифры БИК, для
    корреспондентского (`correspondent=True`) — «0» и 5–6-я цифры БИК.
    Сумма произведений разрядов на веса должна делиться на 10.

    :param account: 20 цифр счёта (пробелы допустимы).
    :param bik: 9 цифр БИК.
    :param correspondent: True — проверяется корреспондентский счёт.
    :return: False, если длины не те или сумма не сходится.
    """
    account_digits = _digits(account)
    bik_digits = _digits(bik)
    if len(account_digits) != 20 or len(bik_digits) != 9:
        return False

    if correspondent:
        prefix = "0" + bik_digits[4:6]
    else:
        prefix = bik_digits[-3:]

    payload = prefix + account_digits
    total = sum(int(d) * w for d, w in zip(payload, _ACCOUNT_WEIGHTS))
    return total % 10 == 0
