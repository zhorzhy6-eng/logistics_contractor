# -*- coding: utf-8 -*-
"""
Тип договора «Логистикс Рус» — приложение к генеральному договору экспедиции
(заглушка, шаг 2 инфраструктуры типов договоров).

Реализация генератора — отдельная задача. Заглушка зарегистрирована
в реестре и поднимает NotImplementedError с понятным текстом, не влияя
на остальные типы (образец — core/contracts/arenda_ts/generator.py).
"""

import logging
from typing import Any, Dict, Mapping, Optional

from core.contracts.base_generator import BaseContractGenerator
from core.contracts.contract_types import ContractType

logger = logging.getLogger("core.contract_generator")

TITLE = "Логистикс Рус"


class LogistiksRusGenerator(BaseContractGenerator):
    """Заглушка: приложение к генеральному договору экспедиции ещё не реализовано."""

    CONTRACT_TYPE = ContractType.LOGISTIKS_RUS.value

    #: Шаблон появится отдельной задачей вместе с реализацией типа.
    TEMPLATE_NAMES: Mapping[str, str] = {}

    FILE_PREFIX = "Заявка_Логистикс_Рус"

    def generate(self, data: Dict[str, Any],
                 output_dir: Optional[str] = None) -> str:
        raise NotImplementedError(
            f"{TITLE} ещё не реализована (заглушка, шаг 2). "
            f"Реализация — отдельная задача после перевозки."
        )

    def _get_template_path(self, carrier_type: str) -> str:
        raise NotImplementedError(
            f"{TITLE}: шаблон ещё не подготовлен (заглушка, шаг 2)."
        )

    def _build_replacements_map(self, data: Any) -> Dict[str, str]:
        raise NotImplementedError(
            f"{TITLE}: карта замен ещё не написана (заглушка, шаг 2)."
        )
