# -*- coding: utf-8 -*-
"""
Тип договора «Договор аренды ТС с экипажем» (заглушка, Шаг 8).

При импорте пакета тип регистрируется в реестре.
"""

from core.contracts.arenda_ts.generator import ArendaTsGenerator, TITLE
from core.contracts.arenda_ts.validator import ArendaTsValidator
from core.contracts.contract_types import ContractType
from core.contracts.registry import register_contract_type

register_contract_type(
    ContractType.ARENDA_TS,
    TITLE,
    generator_class=ArendaTsGenerator,
    validator_class=ArendaTsValidator,
)

__all__ = ["ArendaTsGenerator", "ArendaTsValidator"]
