# -*- coding: utf-8 -*-
"""
Шаги постобработки договора аренды ТС с экипажем (ЭТАП 3.1.D.A.4).

Здесь только объявление шагов: каждый — тонкая обёртка над своим методом
ArendaTsGenerator, а состав и порядок конвейера задаёт его
postprocess_steps(). Общей логики между шагами нет: удаление пустых строк
таблицы машин п. 3.1 и удаление незаполненных точек маршрута п. 3.2/3.3 —
независимые правки разных частей документа, и каждый шаг проверяется
отдельно на программно собранном Document() — без шаблона.

Отличие от шагов «Логистикс Рус»
(core/contracts/logistiks_rus/postprocess.py):

  * таблица машин ищется по ПЯТИ заголовкам («№ / Марка, модель /
    VIN-номер / Точка погрузки / Точка выгрузки»), а не по трём: строка
    удаляется, только если пусты все четыре колонки данных;
  * точки маршрута — не пары абзацев «название + адрес», а по одной
    нумерованной строке на точку («3.2.N. Точка погрузки № N — …»),
    поэтому шаг удаляет строки по пустому адресу и убирает заголовок
    раздела, если точек не осталось.
"""

from core.contracts.base_generator import PostprocessStep


class RemoveEmptyVehicleRowsStep(PostprocessStep):
    """
    Удаление незаполненных строк таблицы машин п. 3.1.

    Таблица в бланке рассчитана на 12 машин; строки, где пусты марка, VIN и
    обе точки маршрута, убирает ArendaTsGenerator._remove_empty_vehicle_rows
    (docxtpl подставляет пустые значения, но сами строки таблицы не
    удаляет).
    """

    name = "remove_empty_vehicle_rows"

    def apply(self, doc, data) -> None:
        self.generator._remove_empty_vehicle_rows(doc)


class RemoveEmptyLoadingUnloadingBlocksStep(PostprocessStep):
    """
    Удаление незаполненных точек погрузки и выгрузки.

    В бланке по 10 строк каждого вида («3.2.N. Точка погрузки № N — …» и
    «3.3.N. Точка выгрузки № N — …»), а точек в договоре может быть меньше.
    Строку с пустым адресом удаляет
    ArendaTsGenerator._remove_empty_point_blocks; если у раздела не осталось
    ни одной точки, удаляется и его заголовок.
    """

    name = "remove_empty_loading_unloading_blocks"

    def apply(self, doc, data) -> None:
        self.generator._remove_empty_point_blocks(doc, data)


__all__ = [
    "RemoveEmptyLoadingUnloadingBlocksStep",
    "RemoveEmptyVehicleRowsStep",
]
