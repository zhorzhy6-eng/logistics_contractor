# -*- coding: utf-8 -*-
"""Валидатор приложения к генеральному договору экспедиции (Логистикс Рус, заглушка)."""

from core.contract_data import ContractData
from core.contracts.base_validator import BaseValidator
from core.contracts.contract_types import ContractType
from core.validator import ValidationReport


class LogistiksRusValidator(BaseValidator):
    """Минимальный валидатор: правила появятся вместе с реализацией типа."""

    CONTRACT_TYPE = ContractType.LOGISTIKS_RUS.value

    def check_specific(self, cd: ContractData, report: ValidationReport) -> None:
        # Обязательные поля приложения (номер генерального договора, поручение
        # экспедитору, вознаграждение) будут проверяться здесь при реализации.
        return None
