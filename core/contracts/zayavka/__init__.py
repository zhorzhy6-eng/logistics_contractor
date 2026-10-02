# -*- coding: utf-8 -*-
"""
Тип документа «Заявка на перевозку авто (Excel)» (заглушка, Шаг 8).

При импорте пакета тип регистрируется в реестре.
"""

from core.contracts.contract_types import ContractType
from core.contracts.registry import register_contract_type
from core.contracts.zayavka.generator import TITLE, ZayavkaExcelGenerator
from core.contracts.zayavka.validator import ZayavkaExcelValidator

register_contract_type(
    ContractType.ZAYAVKA_EXCEL,
    TITLE,
    generator_class=ZayavkaExcelGenerator,
    validator_class=ZayavkaExcelValidator,
)

__all__ = ["ZayavkaExcelGenerator", "ZayavkaExcelValidator"]
