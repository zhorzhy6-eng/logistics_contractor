# -*- coding: utf-8 -*-
"""
Шаги постобработки заявки «Логистикс Рус» (ЭТАП 3.1.C.A.4).

Здесь только объявление шагов: каждый — тонкая обёртка над своим методом
LogistiksRusGenerator, а состав и порядок конвейера задаёт его
postprocess_steps(). Общей логики между шагами нет: удаление строк таблицы
автомобилей и удаление блоков точек — независимые правки разных частей
документа, и каждый шаг проверяется отдельно на программно собранном
Document() — без шаблона (tests/test_logistiks_rus_postprocess.py).
"""

from core.contracts.base_generator import PostprocessStep


class RemoveEmptyVehicleRowsStep(PostprocessStep):
    """
    Удаление незаполненных строк таблицы автомобилей.

    Таблица в бланке рассчитана на 12 машин; строки, где пусты и марка, и
    VIN, убирает LogistiksRusGenerator._remove_empty_vehicle_rows (docxtpl
    подставляет пустые значения, но сами строки таблицы не удаляет).
    """

    name = "remove_empty_vehicle_rows"

    def apply(self, doc, data) -> None:
        self.generator._remove_empty_vehicle_rows(doc)


class RemoveEmptyShipperConsigneeBlocksStep(PostprocessStep):
    """
    Удаление пустых блоков грузоотправителей и грузополучателей.

    В бланке по 10 блоков погрузки и выгрузки; пару «название + адрес» без
    обоих значений удаляет LogistiksRusGenerator._remove_empty_point_blocks,
    а блок, заполненный хотя бы одной из двух строк, сохраняется.
    """

    name = "remove_empty_shipper_consignee_blocks"

    def apply(self, doc, data) -> None:
        self.generator._remove_empty_point_blocks(doc)


__all__ = [
    "RemoveEmptyShipperConsigneeBlocksStep",
    "RemoveEmptyVehicleRowsStep",
]
