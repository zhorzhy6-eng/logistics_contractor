#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Работа с таблицами: удаление строк, сборка таблицы машин и расширение
таблицы бланка под фактическое количество машин.

Переехало из `core/contracts/base_generator.py` (Этап 4, 16.10.2026) без
изменения логики. Методы статические: таблица и данные приходят
аргументами.

Два метода сознательно принимают строителей (`sample_builder`,
`sample_provider`) вместо `self`: подкласс `PerevozkaGenerator`
ПЕРЕОПРЕДЕЛЯЕТ `_sample_vehicle_table` и `_is_vehicle_table`, и если бы
модуль звал свои же функции напрямую, переопределения перестали бы
работать. Делегаты в `BaseContractGenerator` передают сюда `self.*`, и
цепочка переопределений сохраняется.

Расширение таблицы (`expand_vehicle_rows`) добавлено, когда выяснилось,
что бланки всех типов рассчитаны ровно на 12 машин: Word не умеет
растянуть таблицу по данным, и строки дописывает код — клонированием
последней строки шаблона (см. docstring метода).
"""

import logging
from copy import deepcopy
from typing import Any, Callable, Dict, List, Sequence

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
        # Заголовок таблицы повторяется, если таблица уедет на следующую
        # страницу: у таблиц по погрузкам строк бывает много.
        TableBuilder.set_page_break_flags(table)

        return table._tbl

    # ─────────────────────────────────────────────────────────
    # Расширение таблицы машин готового документа
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def expand_vehicle_rows(
        table,
        vehicles: List[Dict[str, Any]],
        *,
        row_values: Callable[[Dict[str, Any], int], Sequence[str]],
        start_number: int = 1,
    ) -> int:
        """
        Дописывает в таблицу машин строки под машины, которым не хватило
        строк бланка.

        Во всех бланках таблица машин фиксированная — шапка и 12 строк
        данных. Word не растягивает таблицу по данным: строки дописывает
        код. Образец — последняя строка данных шаблона: у неё уже
        настроены границы, заливка, отступы и ширина колонок, поэтому
        клон выглядит как соседние строки. Новая строка встаёт В КОНЕЦ
        таблицы, и таблица остаётся ОДНОЙ: нумерация машин сквозная (13,
        14, 15…), а на следующую страницу Word переносит её сам
        (`set_page_break_flags` включает повтор шапки).

        Почему не вторая таблица: две таблицы разрывают нумерацию и
        шапку, и в договоре это выглядит как два разных перечня груза.

        `row_values(vehicle, number)` — правило строки конкретного типа
        (у перевозки «номер, марка, VIN», у аренды ещё точки маршрута).
        `start_number` — номер первой строки данных (обычно 1).

        Возвращает число добавленных строк (0 — если строк хватает или
        таблица не найдена).
        """
        # Заголовок последней строкой не считается: данных в таблице
        # ровно на len(table.rows) - 1 машину.
        rows_available = len(table.rows) - 1
        if rows_available <= 0 or len(vehicles) <= rows_available:
            return 0

        # Оформление — из ПЕРВОЙ строки данных: при машинах сверх бланка
        # она заведомо заполнена, и её tcPr/pPr/rPr — это середина
        # таблицы, а не «последняя строка» с другой нижней границей.
        samples = list(table.rows[1].cells)
        template_tr = deepcopy(table.rows[-1]._tr)

        added = 0
        for offset in range(rows_available, len(vehicles)):
            table._tbl.append(deepcopy(template_tr))
            # Строка уже в теле таблицы: берём её обёртку у python-docx,
            # а не работаем с XML напрямую — так же, как в остальных
            # методах модуля.
            row = table.rows[-1]
            values = list(row_values(vehicles[offset], start_number + offset))
            for index, cell in enumerate(row.cells):
                value = values[index] if index < len(values) else ""
                TableBuilder.set_cell_value(
                    cell, value, Formatting.sample_cell(samples, index)
                )
            added += 1

        logger.info(
            f"Таблица машин расширена: строк в бланке {rows_available}, "
            f"машин {len(vehicles)}, добавлено строк {added}"
        )
        return added

    @staticmethod
    def set_cell_value(cell, text: str, sample_cell=None) -> None:
        """
        ЗАМЕНЯЕТ содержимое ячейки, сохраняя оформление.

        `Formatting.fill_table_cell` дописывает run к существующим — для
        чистой ячейки это то же самое, но у клона строки в ячейке уже
        стоит отрендеренное значение, и дописывание дало бы «12 13».
        Поэтому сначала удаляются лишние абзацы и все run'ы, затем текст
        пишется заново с оформлением образца (`sample_cell`) или прежним
        шрифтом самой ячейки.
        """
        paragraphs = cell.paragraphs
        first = paragraphs[0]
        # Значение строки таблицы всегда однострочное: второй абзац в
        # ячейке — остаток шаблона, а не данные.
        for extra in paragraphs[1:]:
            extra._p.getparent().remove(extra._p)

        # Шрифт ячейки запоминается ДО чистки: если у образца нет run'ов,
        # оформление берётся оттуда, а не «из воздуха».
        own_run = first.runs[0]._r if first.runs else None
        for run in list(first.runs):
            run._r.getparent().remove(run._r)

        if sample_cell is not None:
            Formatting.apply_sample_cell_format(cell, sample_cell)

        sample_paragraph = (
            sample_cell.paragraphs[0]
            if (sample_cell is not None and sample_cell.paragraphs)
            else None
        )
        # Формат абзаца (выравнивание, интервалы) — из образца; если у
        # образца его нет, остаётся формат клонированной строки.
        Formatting.copy_paragraph_format(sample_paragraph, first)

        sample_run = (
            sample_paragraph.runs[0]._r
            if (sample_paragraph is not None and sample_paragraph.runs)
            else own_run
        )
        Formatting.set_cell_text(first, text, sample_run, bold=False)

    @staticmethod
    def set_page_break_flags(table) -> None:
        """
        Готовит таблицу к переносу на следующую страницу.

        `w:cantSplit` — строка не разрывается между страницами (иначе VIN
        или марка окажутся над разрывом, а номер и остаток строки —
        под ним). `w:tblHeader` на строке шапки — Word повторяет шапку на
        новой странице, и на второй странице таблица снова читается.

        Ручной разрыв страницы (`w:br type="page"`) НЕ ставится: он
        разрезал бы раздел договора и сдвинул подписи, а где именно
        кончится страница, зависит от вёрстки — это работа Word.

        Флаги безвредны для короткой таблицы: пока она на одной странице,
        ни один из них не виден.
        """
        if not table.rows:
            return

        # OxmlElement тянет docx.oxml только ради создания двух пустых
        # элементов — импорт внутри метода, как в set_grid_widths.
        from docx.oxml import OxmlElement

        # Порядок детей w:trPr задаёт схема WordprocessingML; элементы
        # вставляются перед своими «последователями», а не в конец.
        after_cant_split = (
            "w:trHeight", "w:tblHeader", "w:tblCellSpacing", "w:jc",
            "w:hidden", "w:ins", "w:del", "w:trPrChange",
        )
        after_tbl_header = (
            "w:tblCellSpacing", "w:jc", "w:hidden", "w:ins", "w:del",
            "w:trPrChange",
        )

        for index, row in enumerate(table.rows):
            tr_pr = row._tr.get_or_add_trPr()
            if tr_pr.find(Formatting.qname("cantSplit")) is None:
                tr_pr.insert_element_before(
                    OxmlElement("w:cantSplit"), *after_cant_split
                )
            if index == 0 and tr_pr.find(Formatting.qname("tblHeader")) is None:
                tr_pr.insert_element_before(
                    OxmlElement("w:tblHeader"), *after_tbl_header
                )

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
