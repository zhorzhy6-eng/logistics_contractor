# -*- coding: utf-8 -*-
"""Валидатор договора аренды ТС с экипажем (заглушка, Шаг 8)."""

from core.contract_data import ContractData
from core.contracts.base_validator import BaseValidator
from core.contracts.contract_types import ContractType
from core.validator import ValidationReport


class ArendaTsValidator(BaseValidator):
    """Минимальный валидатор: правила появятся вместе с реализацией типа."""

    CONTRACT_TYPE = ContractType.ARENDA_TS.value

    def check_specific(self, cd: ContractData, report: ValidationReport) -> None:
        # Обязательные поля аренды (срок, ставка за сутки, экипаж) будут
        # проверяться здесь при реализации типа.
        return None
