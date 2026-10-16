#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Работа с таблицами: удаление строк и сборка таблицы машин.

Переехало из `core/contracts/base_generator.py` (Этап 4, 16.10.2026) без
изменения логики. Методы статические: таблица и данные приходят
аргументами.

Два метода сознательно принимают строителей (`sample_builder`,
`sample_provider`) вместо `self`: подкласс `PerevozkaGenerator`
ПЕРЕОПРЕДЕЛЯЕТ `_sample_vehicle_table` и `_is_vehicle_table`, и если бы
модуль звал свои же функции напрямую, переопределения перестали бы
работать. Делегаты в `BaseContractGenerator` передают сюда `self.*`, и
цепочка переопределений сохраняется.
"""

import logging
from typing import Any, Callable, Dict, List

from core.contracts.base.formatting import Formatting

logger = logging.getLogger("core.contract_generator")


class TableBuilder:
    """Сборка и правка таблиц DOCX: строки, образцы оформления, колонки."""

    @staticmethod
    def delete_row(table, row) -> None:
        """Удаляет строку из таблицы по её XML-узлу."""
        tbl = table._tbl
        tr = row._tr
        tbl.remove(tr)

    @staticmethod
    def vehicle_table(
        doc,
        vehicles: List[Dict[str, Any]],
        *,
        headers: Any,
        style: str,
        column_widths_cm: Any,
        row_values: Callable[[Dict[str, Any], int], Any],
        sample_provider: Callable[[Any], Any],
        start_number: int = 1,
    ):
        """
        Таблица «№ | Марка/Модель | VIN-номер» по списку машин.

        Порядок строк — порядок машин в исходном массиве, без сортировки.
        Оформление повторяет таблицу машин из шаблона (блок 3.1): и шрифт,
        и заливку ячейки, и границы. Копировать один шрифт нельзя — в
        шаблоне шапка оформлена белым текстом на тёмной заливке, и без
        заливки белый текст становится невидимым (белое на белом).

        `start_number` — номер первой строки таблицы. Нумерация машин
        СКВОЗНАЯ по всем точкам раздела: три точки по 2 + 2 + 1 машине
        дают 1, 2, 3, 4, 5, а не 1, 2 / 1, 2 / 1. Раньше счёт начинался
        заново в каждой таблице, и в договоре пять машин выглядели как три
        разные группы.

        Что приходит из генератора: `headers`, `style`, `column_widths_cm`
        и `row_values` — его собственные константы и правило сборки строки
        (у перевозки это номер, марка и VIN). `sample_provider` — обычно
        `self._sample_vehicle_table`, чтобы переопределение подкласса
        сработало.
        """
        sample = sample_provider(doc)

        table = doc.add_table(rows=1, cols=3)
        table.style = style
        table.autofit = False

        header_samples = list(sample.rows[0].cells) if sample is not None else []
        data_samples = (
            list(sample.rows[1].cells)
            if (sample is not None and len(sample.rows) > 1)
            else []
        )

        for index, (cell, title) in enumerate(zip(table.rows[0].cells, headers)):
            Formatting.fill_table_cell(
                cell, title,
                Formatting.sample_cell(header_samples, index),
                bold=True,
            )

        for offset, vehicle in enumerate(vehicles):
            row = table.add_row()
            values = row_values(vehicle, start_number + offset)
            for index, (cell, value) in enumerate(zip(row.cells, values)):
                Formatting.fill_table_cell(
                    cell, value,
                    Formatting.sample_cell(data_samples, index)
                    or Formatting.sample_cell(header_samples, index),
                    bold=False,
                )

        Formatting.set_grid_widths(table, column_widths_cm)

        return table._tbl

    @staticmethod
    def sample_vehicle_table(doc, sample_builder: Callable[[Any], bool]):
        """
        Таблица машин из шаблона — образец оформления.

        Это таблица блока 3.1 «Груз»: она остаётся в документе, в отличие
        от таблицы в 3.2, которую генератор удаляет как дубль.

        `sample_builder` — обычно `self._is_vehicle_table`: у перевозки он
        свой, и образец должен искаться по её правилу, а не по общему.
        """
        for existing in doc.tables:
            if sample_builder(existing):
                return existing
        return None
