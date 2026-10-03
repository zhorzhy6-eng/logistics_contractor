# -*- coding: utf-8 -*-
"""Валидатор приложения к генеральному договору перевозки (Формика, заглушка)."""

from core.contract_data import ContractData
from core.contracts.base_validator import BaseValidator
from core.contracts.contract_types import ContractType
from core.validator import ValidationReport


class FormikaValidator(BaseValidator):
    """Минимальный валидатор: правила появятся вместе с реализацией типа."""

    CONTRACT_TYPE = ContractType.FORMIKA.value

    def check_specific(self, cd: ContractData, report: ValidationReport) -> None:
        # Обязательные поля приложения (номер генерального договора, маршрут,
        # ставка) будут проверяться здесь при реализации типа.
        return None
