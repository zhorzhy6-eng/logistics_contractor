# -*- coding: utf-8 -*-
"""
Тип договора «Формика» — приложение к генеральному договору перевозки
(заглушка, шаг 2 инфраструктуры типов договоров).

При импорте пакета тип регистрируется в реестре (как у остальных типов).
"""

from core.contracts.contract_types import ContractType
from core.contracts.formika.generator import TITLE, FormikaGenerator
from core.contracts.formika.validator import FormikaValidator
from core.contracts.registry import register_contract_type

register_contract_type(
    ContractType.FORMIKA,
    TITLE,
    generator_class=FormikaGenerator,
    validator_class=FormikaValidator,
)

__all__ = ["FormikaGenerator", "FormikaValidator"]
