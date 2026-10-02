# -*- coding: utf-8 -*-
"""
Тип документа «Заявка на перевозку авто (Excel)» (заглушка, Шаг 8).

В отличие от остальных типов НЕ наследуется от BaseContractGenerator:
выход — .xlsx (openpyxl уже в зависимостях), а не DOCX. Общий с другими
типами — только реестр и валидатор. Рендерер-абстракцию не строим
(решение по пункту 4 ответов на аудит).
"""

import logging
from typing import Any, Dict, Optional

from core.contracts.contract_types import ContractType

logger = logging.getLogger("core.contract_generator")

TITLE = "Заявка на перевозку авто (Excel)"


class ZayavkaExcelGenerator:
    """
    Заглушка Excel-генератора.

    Соблюдает контракт фабрики (принимает templates_dir), но для DOCX-базы
    не подходит и не наследуется. Реализация (openpyxl) — отдельная задача.
    """

    CONTRACT_TYPE = ContractType.ZAYAVKA_EXCEL.value

    def __init__(self, templates_dir: str = ""):
        self.templates_dir = templates_dir

    def generate(self, data: Dict[str, Any], output_dir: Optional[str] = None) -> str:
        raise NotImplementedError(
            f"{TITLE} ещё не реализована (заглушка, шаг 8). "
            f"Генерация .xlsx через openpyxl — отдельная задача."
        )
