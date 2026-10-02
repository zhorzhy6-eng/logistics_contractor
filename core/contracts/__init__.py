# -*- coding: utf-8 -*-
"""
Пакет типов договоров (Шаг 1 рефакторинга архитектуры контрактов).

Импортировать отсюда безопасно и дёшево: подпакеты конкретных типов
(perevozka, arenda_ts, ...) сюда НЕ импортируются — ошибка импорта одного
типа не должна ломать загрузку остальных (изоляция L1, см. registry.py).
"""

from core.contracts.contract_types import (
    DEFAULT_CONTRACT_TYPE,
    ContractType,
    normalize_contract_type,
)

__all__ = [
    "ContractType",
    "DEFAULT_CONTRACT_TYPE",
    "normalize_contract_type",
]
