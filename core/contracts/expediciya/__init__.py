# -*- coding: utf-8 -*-
"""
Тип договора «Экспедиторская заявка» (заглушка, Шаг 8).

При импорте пакета тип регистрируется в реестре.
"""

from core.contracts.contract_types import ContractType
from core.contracts.expediciya.generator import TITLE, ExpediciyaGenerator
from core.contracts.expediciya.validator import ExpediciyaValidator
from core.contracts.registry import register_contract_type

register_contract_type(
    ContractType.EXPEDICIYA,
    TITLE,
    generator_class=ExpediciyaGenerator,
    validator_class=ExpediciyaValidator,
)

__all__ = ["ExpediciyaGenerator", "ExpediciyaValidator"]
