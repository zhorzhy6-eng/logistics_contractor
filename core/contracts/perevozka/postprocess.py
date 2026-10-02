# -*- coding: utf-8 -*-
"""
Шаги постобработки договора-заявки на перевозку (Шаг 5 рефакторинга).

Каждый шаг — тонкая обёртка над соответствующим методом генератора:
логика осталась в PerevozkaGenerator, а состав и порядок конвейера
объявляются в postprocess_steps().
"""

from typing import Any, Dict

from core.contract_data import ContractData
from core.contracts.base_generator import PostprocessStep


class RouteTablesStep(PostprocessStep):
    """
    Таблицы машин вместо меток {{LOADING_TABLE_HERE}} /
    {{UNLOADING_TABLE_HERE}} в блоках 3.2 «Погрузка» и 3.3 «Выгрузка».
    """

    name = "route_tables"

    def apply(self, doc, data) -> None:
        self.generator._insert_route_tables(doc, data)


class RemoveEmptyVehicleRowsStep(PostprocessStep):
    """Удаление пустых строк таблицы ТС шаблона (в шаблоне их 12)."""

    name = "remove_empty_vehicle_rows"

    def apply(self, doc, data) -> None:
        self.generator._remove_empty_vehicle_rows(doc)


__all__ = ["RouteTablesStep", "RemoveEmptyVehicleRowsStep"]
