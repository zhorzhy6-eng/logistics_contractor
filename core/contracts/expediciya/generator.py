# -*- coding: utf-8 -*-
"""
Тип договора «Экспедиторская заявка» (заглушка, Шаг 8).

Реализация — отдельная задача после перевозки.
"""

import logging
from typing import Any, Dict, Optional

from core.contracts.base_generator import BaseContractGenerator
from core.contracts.contract_types import ContractType

logger = logging.getLogger("core.contract_generator")

TITLE = "Экспедиторская заявка"


class ExpediciyaGenerator(BaseContractGenerator):
    """Заглушка: экспедиторская заявка ещё не реализована."""

    CONTRACT_TYPE = ContractType.EXPEDICIYA.value

    def generate(self, data: Dict[str, Any], output_dir: Optional[str] = None) -> str:
        raise NotImplementedError(
            f"{TITLE} ещё не реализована (заглушка, шаг 8). "
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
