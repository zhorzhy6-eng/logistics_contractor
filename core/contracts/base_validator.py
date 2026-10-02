# -*- coding: utf-8 -*-
"""
Базовый валидатор типа договора (Шаг 1 рефакторинга архитектуры).

Тонкий ABC поверх ValidationReport из core/validator.py (отчёт не
копируется — UI и его форматирование продолжают работать без правок).

Общие правила валидации (ИНН/КПП/ОГРН/БИК/счета/паспорт/ВУ/VIN) пока живут
в core/validator.Validator: перенос их сюда — отдельная поздняя задача,
core/validator.py не трогаем. Подклассы добавляют только специфику типа
в check_specific(), а перевозка (PerevozkaValidator) делегирует старому
Validator.check() целиком — поведение UI 1:1.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar, List

from core.contract_data import ContractData
from core.validator import ValidationReport

# Отдельный журнал валидации: канал «core.contract_generator» тесты
# проверяют на отсутствие ПДн, и сообщения валидатора там не место.
logger = logging.getLogger("core.contracts.base_validator")


class BaseValidator(ABC):
    """Базовый валидатор данных одного типа договора."""

    #: Ключ типа договора (значение ContractType), у подклассов обязателен.
    CONTRACT_TYPE: ClassVar[str] = ""

    # ─────────────────────────────────────────────────────────
    # Публичный API (шаблонный метод)
    # ─────────────────────────────────────────────────────────

    def check(self, data: Any) -> ValidationReport:
        """Полная проверка: общие правила + специфика типа."""
        cd = ContractData.coerce(data)
        report = ValidationReport()

        self.check_common(cd, report)
        self.check_specific(cd, report)
        self._log(cd, report)

        return report

    def validate(self, data: Any) -> List[str]:
        """Совместимость с core.validator.Validator.validate: только ошибки."""
        return self.check(data).errors

    # ─────────────────────────────────────────────────────────
    # Точки расширения
    # ─────────────────────────────────────────────────────────

    def check_common(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Общие для всех типов проверки.

        На данном этапе пусто: существующие общие правила — в
        core/validator.Validator (перенос сюда — отдельная задача).
        """
        return None

    @abstractmethod
    def check_specific(self, cd: ContractData, report: ValidationReport) -> None:
        """Специфичные для типа проверки. Обязан реализовать подкласс."""

    # ─────────────────────────────────────────────────────────
    # Логирование (только счётчики, без ПДн)
    # ─────────────────────────────────────────────────────────

    def _log(self, cd: ContractData, report: ValidationReport) -> None:
        contract_type = self.CONTRACT_TYPE or type(self).__name__
        if report.has_errors:
            logger.warning(
                f"Валидация [{contract_type}] не пройдена: "
                f"ошибок={len(report.errors)}, замечаний={len(report.warnings)}"
            )
        else:
            logger.info(
                f"Валидация [{contract_type}] пройдена: ошибок=0, "
                f"замечаний={len(report.warnings)}"
            )
