#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты контрольных сумм реквизитов (core/validation/checksums.py).

Что проверяется: ИНН (10 и 12 цифр), ОГРН, ОГРНИП, СНИЛС, расчётный и
корреспондентский счёт. Значения в тестах СИНТЕТИЧЕСКИЕ: верные собраны
расчётом, неверные отличаются от них на одну цифру — так видно, что
функция проверяет именно сумму, а не длину или «похожесть».

Отдельно проверяется нормализация: реквизиты в жизни приходят с пробелами
и дефисами («770-123-4560», «112-233-445 95»), и проверка не должна от
этого зависеть.
"""

import pytest

from core.validation.checksums import (
    account_checksum_ok,
    inn_checksum_ok,
    ogrn_checksum_ok,
    ogrnip_checksum_ok,
    snils_checksum_ok,
)

#: Синтетические значения с ВЕРНОЙ контрольной суммой.
VALID_INN_10 = "7701234560"
VALID_INN_12 = "500100732259"
VALID_OGRN = "1027700132195"
VALID_OGRNIP = "315770000100014"
VALID_SNILS = "112-233-445 95"
VALID_ACCOUNT = "40702810000000000007"
VALID_CORRESPONDENT = "30101810400000000225"
BIK = "044525225"


# ─────────────────────────────────────────────────────────────
# ИНН
# ─────────────────────────────────────────────────────────────

def test_inn_10_digits_with_valid_checksum():
    assert inn_checksum_ok(VALID_INN_10) is True


def test_inn_10_digits_with_wrong_last_digit():
    assert inn_checksum_ok("7701234561") is False


def test_inn_12_digits_with_valid_checksum():
    """12 цифр — ИНН индивидуального предпринимателя."""
    assert inn_checksum_ok(VALID_INN_12) is True


def test_inn_12_digits_with_wrong_eleventh_digit():
    assert inn_checksum_ok("500100732269") is False


def test_inn_12_digits_with_wrong_twelfth_digit():
    assert inn_checksum_ok("500100732258") is False


@pytest.mark.parametrize("value", ["", None, "123", "123456789", "12345678901",
                                   "1234567890123", "не ИНН"])
def test_inn_wrong_length_is_false(value):
    """Не 10 и не 12 цифр — контрольной суммы нет, ответ False."""
    assert inn_checksum_ok(value) is False


def test_inn_ignores_spaces_and_dashes():
    """ИНН из документа приходит с разделителями — это тот же номер."""
    assert inn_checksum_ok("770-123-4560") is True
    assert inn_checksum_ok("770 123 4560") is True
    assert inn_checksum_ok("770-123-4561") is False


def test_inn_zeroes_pass_checksum_but_stay_a_number():
    """Нулевой ИНН сумму «проходит» — отсекать его должен формат, не сумма."""
    assert inn_checksum_ok("0000000000") is True


# ─────────────────────────────────────────────────────────────
# ОГРН и ОГРНИП
# ─────────────────────────────────────────────────────────────

def test_ogrn_with_valid_checksum():
    assert ogrn_checksum_ok(VALID_OGRN) is True


def test_ogrn_with_wrong_last_digit():
    assert ogrn_checksum_ok("1027700132196") is False


def test_ogrnip_with_valid_checksum():
    assert ogrnip_checksum_ok(VALID_OGRNIP) is True


def test_ogrnip_with_wrong_last_digit():
    """Частая ошибка: ОГРНИП взят с чужой последней цифрой."""
    assert ogrnip_checksum_ok("315770000100012") is False


def test_ogrn_and_ogrnip_do_not_swap_lengths():
    """13 и 15 цифр — разные алгоритмы: модуль 11 против модуля 13."""
    assert ogrn_checksum_ok(VALID_OGRNIP) is False
    assert ogrnip_checksum_ok(VALID_OGRN) is False


@pytest.mark.parametrize("value", ["", None, "123", "102770013219", "10277001321951"])
def test_ogrn_wrong_length_is_false(value):
    assert ogrn_checksum_ok(value) is False


@pytest.mark.parametrize("value", ["", None, "123", "31577000010001", "3157700001000141"])
def test_ogrnip_wrong_length_is_false(value):
    assert ogrnip_checksum_ok(value) is False


def test_ogrn_ignores_spaces():
    assert ogrn_checksum_ok("1 027 700 132 195") is True


# ─────────────────────────────────────────────────────────────
# СНИЛС
# ─────────────────────────────────────────────────────────────

def test_snils_with_valid_checksum():
    assert snils_checksum_ok(VALID_SNILS) is True


def test_snils_with_wrong_checksum():
    assert snils_checksum_ok("112-233-445 94") is False


def test_snils_without_separators():
    assert snils_checksum_ok("11223344595") is True


def test_snils_control_number_zero():
    """Сумма 100 превращается в контрольное число «00», а не в «100»."""
    assert snils_checksum_ok("100-356-355 00") is True
    assert snils_checksum_ok("100-356-355 100") is False


@pytest.mark.parametrize("value", ["", None, "1122334459", "112233445955", "123"])
def test_snils_wrong_length_is_false(value):
    assert snils_checksum_ok(value) is False


# ─────────────────────────────────────────────────────────────
# Расчётный и корреспондентский счёт
# ─────────────────────────────────────────────────────────────

def test_account_with_valid_checksum():
    assert account_checksum_ok(VALID_ACCOUNT, BIK) is True


def test_account_with_wrong_last_digit():
    assert account_checksum_ok("40702810000000000001", BIK) is False


def test_account_checksum_depends_on_bik():
    """Тот же счёт при другом БИК сумму не проходит: проверка идёт по БИК."""
    assert account_checksum_ok(VALID_ACCOUNT, "044525226") is False


def test_account_with_wrong_length_is_false():
    assert account_checksum_ok("4070281000000000000", BIK) is False
    assert account_checksum_ok("407028100000000000071", BIK) is False


def test_account_with_wrong_bik_length_is_false():
    assert account_checksum_ok(VALID_ACCOUNT, "04452522") is False
    assert account_checksum_ok(VALID_ACCOUNT, "") is False


def test_account_ignores_spaces():
    assert account_checksum_ok("4070 2810 0000 0000 0007", BIK) is True


def test_correspondent_account_with_valid_checksum():
    assert account_checksum_ok(VALID_CORRESPONDENT, BIK,
                               correspondent=True) is True


def test_correspondent_account_with_wrong_checksum():
    assert account_checksum_ok("30101810400000000226", BIK,
                               correspondent=True) is False


def test_correspondent_and_settlement_use_different_prefixes():
    """Один и тот же счёт нельзя проверять двумя формулами сразу."""
    assert account_checksum_ok(VALID_CORRESPONDENT, BIK) is False
    assert account_checksum_ok(VALID_ACCOUNT, BIK, correspondent=True) is False


# ─────────────────────────────────────────────────────────────
# Общие свойства
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("check", [inn_checksum_ok, ogrn_checksum_ok,
                                   ogrnip_checksum_ok, snils_checksum_ok])
def test_empty_values_are_not_valid(check):
    """Пустое значение — не «реквизит с верной суммой»."""
    assert check("") is False
    assert check(None) is False
    assert check("   ") is False


def test_checksums_do_not_raise_on_garbage():
    """Мусор из OCR не должен ронять проверку исключением."""
    for value in ("—", "б/н", "№123", "77О1234567", "0" * 40):
        assert inn_checksum_ok(value) is False
        assert ogrn_checksum_ok(value) is False
        assert ogrnip_checksum_ok(value) is False
        assert snils_checksum_ok(value) is False


def test_pseudonymizer_uses_the_shared_checksums():
    """
    Второй копии алгоритма в псевдонимизаторе нет.

    Раньше расчёт жил только там; теперь и он, и валидатор берут общий
    модуль. Проверка держит это: копия алгоритма однажды разошлась бы с
    оригиналом, а расхождение здесь — это либо немаркированный паспорт
    в облаке, либо ложное замечание оператору.
    """
    import core.pseudonymizer as pseudonymizer

    assert pseudonymizer._inn_checksum_ok is inn_checksum_ok
    assert pseudonymizer._snils_checksum_ok is snils_checksum_ok


def test_scenario_generator_uses_the_shared_checksums():
    """Генератор синтетических текстов берёт те же правила, а не свои."""
    from tools import make_text_scenarios

    assert make_text_scenarios.inn_checksum_ok is inn_checksum_ok
    assert make_text_scenarios.ogrn_checksum_ok("1027700132195") is True
    assert make_text_scenarios.ogrn_checksum_ok("315770000100014") is True
    assert make_text_scenarios.ogrn_checksum_ok("1027700132196") is False
