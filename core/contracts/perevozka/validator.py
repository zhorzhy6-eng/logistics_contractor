# -*- coding: utf-8 -*-
"""
Валидатор договора-заявки на перевозку (Шаг 2 рефакторинга архитектуры).

Делегирует существующему core.validator.Validator 1:1: поведение UI
(MainWindow._on_create_contract) и tests/test_validator.py не меняются.
Перенос общих правил в BaseValidator — отдельная поздняя задача,
core/validator.py не трогаем.
"""

from typing import Any, List

from core.contract_data import ContractData
from core.contracts.base_validator import BaseValidator
from core.contracts.contract_types import ContractType
from core.validator import ValidationReport, Validator


class PerevozkaValidator(BaseValidator):
    """Валидатор договора-заявки на перевозку — обёртка над Validator."""

    CONTRACT_TYPE = ContractType.PEREVOZKA.value

    def check(self, data: Any) -> ValidationReport:
        """Полная проверка: ровно то, что делал старый Validator.check()."""
        return Validator.check(data)

    def validate(self, data: Any) -> List[str]:
        """Только критические ошибки (совместимость с Validator.validate)."""
        return Validator.validate(data)

    def check_specific(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Специфика типа уже покрыта core.validator.Validator:
        там и правила для перевозчика, и маршрут, и ТС. Дублировать не нужно.
        """
        return None
