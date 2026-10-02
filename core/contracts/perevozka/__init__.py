# -*- coding: utf-8 -*-
"""
Тип договора «Договор-заявка на перевозку» (текущий, рабочий).

При импорте пакета тип регистрируется в реестре. Регистрация выполняется
один раз (importlib кэширует модуль), поэтому повторный импорт — из shim
core/contract_generator.py или из registry.load_builtin() — дубля не даёт.
"""

from core.contracts.contract_types import ContractType
from core.contracts.perevozka.generator import PerevozkaGenerator
from core.contracts.perevozka.validator import PerevozkaValidator
from core.contracts.registry import register_contract_type

register_contract_type(
    ContractType.PEREVOZKA,
    "Договор-заявка на перевозку",
    generator_class=PerevozkaGenerator,
    validator_class=PerevozkaValidator,
)

__all__ = ["PerevozkaGenerator", "PerevozkaValidator"]
