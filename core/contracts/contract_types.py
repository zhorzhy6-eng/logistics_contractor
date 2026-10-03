# -*- coding: utf-8 -*-
"""
Константы типов договоров (Шаг 1 рефакторинга архитектуры контрактов).

Тип договора — строковый Enum: значения можно писать в БД, конфиги и
логи без дополнительных преобразований.
"""

from enum import Enum


class ContractType(str, Enum):
    """Типы договоров, которые умеет создавать приложение."""

    PEREVOZKA = "perevozka"
    """Договор-заявка на перевозку (текущий, рабочий; в UI — «Экспедиторство»)."""

    FORMIKA = "formika"
    """Приложение к генеральному договору перевозки («Формика»)."""

    LOGISTIKS_RUS = "logistiks_rus"
    """Приложение к генеральному договору экспедиции («Логистикс Рус»)."""

    ARENDA_TS = "arenda_ts"
    """Договор аренды транспортного средства с экипажем."""

    EXPEDICIYA = "expediciya"
    """Экспедиторская заявка."""

    ZAYAVKA_EXCEL = "zayavka_excel"
    """Заявка на перевозку авто (Excel-формат)."""


#: Тип по умолчанию: историческое поведение — все старые вызовы без
#: указания типа генерируют договор-заявку на перевозку.
DEFAULT_CONTRACT_TYPE = ContractType.PEREVOZKA


def normalize_contract_type(value) -> ContractType:
    """
    Приводит произвольное значение (None / ContractType / str) к ContractType.

    Неизвестная строка не падает здесь: решение (ошибка или fallback)
    принимает реестр — он знает, какие типы реально зарегистрированы.
    """
    if value is None:
        return DEFAULT_CONTRACT_TYPE
    if isinstance(value, ContractType):
        return value
    return ContractType(str(value).strip())
