# -*- coding: utf-8 -*-
"""
Тип договора «Логистикс Рус» — приложение к генеральному договору экспедиции
(заглушка, шаг 2 инфраструктуры типов договоров).

При импорте пакета тип регистрируется в реестре (как у остальных типов).
"""

from core.contracts.contract_types import ContractType
from core.contracts.logistiks_rus.generator import TITLE, LogistiksRusGenerator
from core.contracts.logistiks_rus.validator import LogistiksRusValidator
from core.contracts.registry import register_contract_type

register_contract_type(
    ContractType.LOGISTIKS_RUS,
    TITLE,
    generator_class=LogistiksRusGenerator,
    validator_class=LogistiksRusValidator,
)

__all__ = ["LogistiksRusGenerator", "LogistiksRusValidator"]
