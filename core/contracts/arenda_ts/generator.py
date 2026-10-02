# -*- coding: utf-8 -*-
"""
Тип договора «Договор аренды ТС с экипажем» (заглушка, Шаг 8).

Реализация — отдельная задача после перевозки. Заглушка зарегистрирована
в реестре и поднимает NotImplementedError с понятным текстом, не влияя
на остальные типы.
"""

import logging
from typing import Any, Dict, Optional

from core.contracts.base_generator import BaseContractGenerator
from core.contracts.contract_types import ContractType

logger = logging.getLogger("core.contract_generator")

TITLE = "Договор аренды ТС с экипажем"


class ArendaTsGenerator(BaseContractGenerator):
    """Заглушка: договор аренды ТС с экипажем ещё не реализован."""

    CONTRACT_TYPE = ContractType.ARENDA_TS.value

    def generate(self, data: Dict[str, Any], output_dir: Optional[str] = None) -> str:
        raise NotImplementedError(
            f"{TITLE} ещё не реализован (заглушка, шаг 8). "
            f"Реализация — отдельная задача после перевозки."
        )

    def _get_template_path(self, carrier_type: str) -> str:
        raise NotImplementedError(
            f"{TITLE}: шаблон ещё не создан (заглушка, шаг 8)."
        )

    def _build_replacements_map(self, data: Any) -> Dict[str, str]:
        raise NotImplementedError(
            f"{TITLE}: карта замен ещё не написана (заглушка, шаг 8)."
        )
