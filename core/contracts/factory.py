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
from typing import Optional

from core.contracts.paths import PROJECT_ROOT, TEMPLATES_DIR
from core.contracts.registry import ContractTypeError, ContractTypeRegistry

logger = logging.getLogger("core.contracts.factory")


def default_templates_dir(subdir: str = "") -> str:
    """
    Папка шаблонов по умолчанию.

    Если подключена приватная часть проекта, её папка шаблонов идёт ПЕРВОЙ:
    в ней лежат бланки ВСЕХ типов (клиентские и три бланка «Экспедиторства»),
    поэтому генератор любого типа находит свой бланк в одном месте. Публичная
    папка — запасной вариант: она работает и без приватной части.

    :param subdir: подпапка шаблонов типа (spec.templates_subdir), если задана.
    """
    from core.private_path import private_path

    relative = ("templates", subdir) if subdir else ("templates",)

    private_templates = private_path(*relative)
    if private_templates is not None and private_templates.is_dir():
        return str(private_templates)

    if subdir:
        return str(PROJECT_ROOT / "templates" / subdir)
    return str(TEMPLATES_DIR)


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

        templates_dir=None → папка шаблонов проекта (приватная часть, если
        подключена, иначе публичная), либо её подпапка, если тип объявил
        ``templates_subdir``.
        """
        spec = ContractTypeRegistry.get(contract_type, strict=strict)

        generator_class = spec.generator_class
        if generator_class is None:
            raise ContractTypeError(
                f"Тип договора {spec.contract_type!r}: генератор не зарегистрирован"
            )

        if templates_dir is None:
            templates_dir = default_templates_dir(spec.templates_subdir)

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
