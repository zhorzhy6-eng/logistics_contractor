# -*- coding: utf-8 -*-
"""Валидатор заявки на перевозку авто (Excel) (заглушка, Шаг 8)."""

from core.contract_data import ContractData
from core.contracts.base_validator import BaseValidator
from core.contracts.contract_types import ContractType
from core.validator import ValidationReport


class ZayavkaExcelValidator(BaseValidator):
    """Минимальный валидатор: правила появятся вместе с реализацией типа."""

    CONTRACT_TYPE = ContractType.ZAYAVKA_EXCEL.value

    def check_specific(self, cd: ContractData, report: ValidationReport) -> None:
        # Обязательные колонки Excel-шаблона будут проверяться здесь
        # при реализации типа.
        return None
