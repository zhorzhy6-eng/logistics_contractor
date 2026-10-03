# -*- coding: utf-8 -*-
"""
Шаги постобработки договора-заявки «Формика» (ЭТАП 3.1.A.3).

Каждый шаг — тонкая обёртка над методом генератора: логика живёт в
FormikaGenerator, а состав и порядок конвейера объявляются в
postprocess_steps(). Шаги тестируются отдельно на программно собранном
Document() — без обращения к шаблону.
"""

from core.contracts.base_generator import PostprocessStep


class RemoveEmptyVehicleRowsStep(PostprocessStep):
    """
    Удаление незаполненных строк таблицы груза.

    Таблица в бланке рассчитана на 12 машин, а в конкретном договоре их
    может быть от 1 до 12: строки, где пусты и марка, и VIN, из готового
    документа убираются (docxtpl подставляет пустые значения, но сами
    строки таблицы не удаляет).
    """

    name = "remove_empty_vehicle_rows"

    def apply(self, doc, data) -> None:
        self.generator._remove_empty_vehicle_rows(doc)


__all__ = ["RemoveEmptyVehicleRowsStep"]
