# -*- coding: utf-8 -*-
"""
Фабрики генераторов и валидаторов типов договоров (Шаг 1 рефакторинга).

Правила:
  * contract_type=None → тип по умолчанию (перевозка): старые вызовы
    «без типа» продолжают работать;
  * неизвестный тип: strict=False (по умолчанию) → fallback на перевозку
    + warning в лог; strict=True → UnknownContractTypeError;
  * инстансы не кэшируются: генератор не хранит состояния между вызовами,
    а свежий инстанс исключает протечку состояния между типами.
"""

import logging
from typing import Any, Optional

from core.contracts.paths import PROJECT_ROOT, TEMPLATES_DIR
from core.contracts.registry import ContractTypeError, ContractTypeRegistry

logger = logging.getLogger("core.contracts.factory")


class GeneratorFactory:
    """Создаёт генераторы договоров по типу."""

    @classmethod
    def get_generator(
        cls,
        contract_type=None,
        templates_dir: Optional[str] = None,
        *,
        strict: bool = False,
    ):
        """
        Генератор для типа договора.

        templates_dir=None → templates/ (или templates/<templates_subdir>
        типа, если тип объявил подпапку шаблонов).
        """
        spec = ContractTypeRegistry.get(contract_type, strict=strict)

        generator_class = spec.generator_class
        if generator_class is None:
            raise ContractTypeError(
                f"Тип договора {spec.contract_type!r}: генератор не зарегистрирован"
            )

        if templates_dir is None:
            if spec.templates_subdir:
                templates_dir = str(PROJECT_ROOT / "templates" / spec.templates_subdir)
            else:
                templates_dir = str(TEMPLATES_DIR)

        return generator_class(templates_dir=templates_dir)

    @classmethod
    def get_validator(cls, contract_type=None, *, strict: bool = False):
        """Валидатор для типа договора (новый инстанс на каждый вызов)."""
        spec = ContractTypeRegistry.get(contract_type, strict=strict)

        validator_class = spec.validator_class
        if validator_class is None:
            raise ContractTypeError(
                f"Тип договора {spec.contract_type!r}: валидатор не зарегистрирован"
            )

        return validator_class()


#: Короткий алиас: высокоуровневый код обращается к фабрике, а не к
#: конкретным классам типов (Dependency Inversion).
ValidatorFactory = GeneratorFactory
