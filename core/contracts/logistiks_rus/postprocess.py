# -*- coding: utf-8 -*-
"""
Шаги постобработки заявки «Логистикс Рус» (ЭТАП 3.1.C.A.3).

Каждый шаг — тонкая обёртка над методом генератора: логика живёт в
LogistiksRusGenerator, а состав и порядок конвейера объявляются в
postprocess_steps(). Шаги тестируются отдельно на программно собранном
Document() — без обращения к шаблону.

Полноценная постобработка типа — ЭТАП 3.1.C.A.4; здесь шаги уже рабочие,
чтобы генератор рендерил документ без пустых строк и блоков.
"""

from core.contracts.base_generator import PostprocessStep


class RemoveEmptyVehicleRowsStep(PostprocessStep):
    """
    Удаление незаполненных строк таблицы автомобилей.

    Таблица в бланке рассчитана на 12 машин, а в конкретной заявке их может
    быть от 1 до 12: строки, где пусты и марка, и VIN, из готового документа
    убираются (docxtpl подставляет пустые значения, но сами строки таблицы
    не удаляет). Таблица ищется по шапке «№ / Марка, модель / VIN-номер».
    """

    name = "remove_empty_vehicle_rows"

    def apply(self, doc, data) -> None:
        self.generator._remove_empty_vehicle_rows(doc)


class RemoveEmptyShipperConsigneeBlocksStep(PostprocessStep):
    """
    Удаление пустых блоков грузоотправителей и грузополучателей.

    В бланке по 10 блоков погрузки и выгрузки; блоки сверх фактического
    числа точек остаются в документе пустыми («Грузоотправитель:»,
    «Адрес погрузки:»). Такой блок удаляется целиком, а блок, заполненный
    хотя бы одной из двух строк, сохраняется.
    """

    name = "remove_empty_shipper_consignee_blocks"

    def apply(self, doc, data) -> None:
        self.generator._remove_empty_point_blocks(doc)


__all__ = [
    "RemoveEmptyShipperConsigneeBlocksStep",
    "RemoveEmptyVehicleRowsStep",
]
