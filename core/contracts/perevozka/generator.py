#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор договора-заявки на перевозку (Шаг 2 рефакторинга архитектуры).

Механический перенос класса ContractGenerator из core/contract_generator.py
(до shim) в подкласс PerevozkaGenerator(BaseContractGenerator). Вся логика
перенесена без изменений, кроме трёх точечных правок:

  S1. Жёсткие ссылки «ContractGenerator.» внутри тел методов заменены на
      cls (classmethod): после переименования класса имени ContractGenerator
      в этом модуле больше нет, и старые ссылки дали бы NameError.
  S2. Логгер остался «core.contract_generator» — тесты ловят сообщения
      генерации через caplog именно по этому имени.
  S3. default_output_dir() считает корень проекта от нового местоположения
      файла: parents[3] = core/contracts/perevozka/generator.py → корень.

Общая часть (категория A) продублирована намеренно: до шага 4 подкласс
должен вести себя 1:1 как старый класс, независимо от базового.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.contract_data import ContractData
from core.contracts.base_generator import (
    BaseContractGenerator,
    ConvertNewlinesStep,
    NormalizeSpacesStep,
    PostprocessStep,
)
from core.contracts.contract_types import ContractType
from core.contracts.perevozka.postprocess import (
    RemoveEmptyVehicleRowsStep,
    RouteTablesStep,
)
from core.contracts.perevozka.validator import PerevozkaValidator
from core.contracts.ru_morphology import (
    acting_by_gender,
    detect_gender,
    genitive_fio,
    genitive_position,
    pronoun_by_gender,
)
from core.dates import to_iso
from core.num_to_words import amount_to_words

logger = logging.getLogger("core.contract_generator")

#: Вид лица и его сокращение: по сокращению узнаётся, что наименование уже
#: содержит приставку («ООО «Ромашка»»), и второй раз её печатать не надо.
LEGAL_FORM_SHORT = {
    "общество с ограниченной ответственностью": "ООО",
    "индивидуальный предприниматель": "ИП",
}


class PerevozkaGenerator(BaseContractGenerator):
    """
    Договор-заявка на перевозку.

    Выбирает шаблон по типу перевозчика:
      - ООО (с НДС)     → templates/shablon_ooo.docx
      - ИП с НДС        → templates/shablon_ip_with_vat.docx
      - ИП без НДС      → templates/shablon_ip_without_vat.docx

    Поддерживает несколько мест погрузки и выгрузки (до 10), привязку
    каждой машины к точке через loading_index / unloading_index, таблицы
    машин вместо меток {{LOADING_TABLE_HERE}} / {{UNLOADING_TABLE_HERE}}
    и legacy-плоские блоки {{loading_block}} / {{unloading_block}}.
    """

    CONTRACT_TYPE = ContractType.PEREVOZKA.value

    TEMPLATE_NAMES = {
        "ООО": "shablon_ooo.docx",
        "ИП с НДС": "shablon_ip_with_vat.docx",
        "ИП без НДС": "shablon_ip_without_vat.docx",
    }

    #: Префикс имени файла (хук get_filename в базе).
    FILE_PREFIX = "Договор-заявка"

    #: Валидатор типа (хук validate в базе).
    VALIDATOR_CLASS = PerevozkaValidator

    # Папка по умолчанию для готовых договоров (Шаг 6 оптимизации).
    # Раньше файлы по 2,7 МБ падали в корень проекта и мешались с кодом.
    DEFAULT_OUTPUT_DIRNAME = "output"

    # ── Метки таблиц погрузок/выгрузок (блоки 3.2 / 3.3) ──
    # Имена совпадают с {{...}} в шаблоне, значения — текстовые маркеры,
    # которые docxtpl оставляет в документе как есть. По ним постобработка
    # и находит место вставки таблиц (docxtpl сам таблицы в середину
    # документа вставлять не умеет).
    LOADING_TABLE_PLACEHOLDER = "LOADING_TABLE_HERE"
    UNLOADING_TABLE_PLACEHOLDER = "UNLOADING_TABLE_HERE"
    LOADING_TABLE_MARKER = "LOADING_TABLE_HERE"
    UNLOADING_TABLE_MARKER = "UNLOADING_TABLE_HERE"

    #: Плейсхолдеры, у которых перенос строки несёт смысл (старые шаблоны
    #: с плоским блоком). Все остальные значения приводятся к одной строке.
    MULTILINE_PLACEHOLDERS = ("loading_block", "unloading_block")

    #: Типы ТС, которые в таблицы погрузок/выгрузок не попадают:
    #: тягач и прицеп/полуприцеп описаны в блоке 3.1 (карточка ТС).
    NON_CARGO_VEHICLE_TYPES = ("Тягач", "Полуприцеп", "Прицеп")

    #: Строка-заглушка, если ни одна погрузка/выгрузка не получила машин.
    NO_VEHICLES_TEXT = "(машины не указаны)"

    #: Заголовки колонок таблиц погрузок/выгрузок.
    VEHICLE_TABLE_HEADERS = ("№", "Марка/Модель", "VIN-номер")

    #: Ширина колонок таблиц погрузок/выгрузок: № ≈ 1 см,
    #: Марка/Модель ≈ 9 см, VIN-номер ≈ 5 см.
    VEHICLE_TABLE_COLUMN_WIDTHS_CM = (1.0, 9.0, 5.0)

    #: Стиль таблиц погрузок/выгрузок (границы — как у таблицы ТС в шаблоне).
    VEHICLE_TABLE_STYLE = "Table Grid"

    # ─────────────────────────────────────────────────────────
    # ВЫБОР ШАБЛОНА
    # ─────────────────────────────────────────────────────────

    def _get_template_path(self, carrier_type: str) -> str:
        if "ИП без НДС" in carrier_type:
            return self.templates["ИП без НДС"]
        elif "ИП с НДС" in carrier_type:
            return self.templates["ИП с НДС"]
        else:
            return self.templates["ООО"]

    # ─────────────────────────────────────────────────────────
    # КОНВЕЙЕР ПОСТОБРАБОТКИ
    # ─────────────────────────────────────────────────────────

    def postprocess_steps(self, data) -> List[PostprocessStep]:
        """
        Шаги постобработки заявки.

        Порядок и состав повторяют прежний код _postprocess_document:
        переносы строк → таблицы маршрута (только с данными) → удаление
        пустых строк таблицы ТС. Без данных (data=None) таблицы маршрута
        не строятся — историческое поведение сохранено.

        Последним идёт шаг косметики пробелов (NormalizeSpacesStep): он
        должен видеть документ уже собранным, вместе с таблицами маршрута —
        двойные пробелы и «при условии , что» есть и в бланке, и в
        заголовках этих таблиц.
        """
        steps: List[PostprocessStep] = [ConvertNewlinesStep(self)]
        if data is not None:
            steps.append(RouteTablesStep(self))
        steps.append(RemoveEmptyVehicleRowsStep(self))
        steps.append(NormalizeSpacesStep(self))
        return steps

    # ─────────────────────────────────────────────────────────
    # ТАБЛИЦЫ ПОГРУЗОК / ВЫГРУЗОК В БЛОКАХ 3.2 И 3.3
    # ─────────────────────────────────────────────────────────

    def _insert_route_tables(self, doc, contract_data: ContractData) -> None:
        """
        Подставляет таблицы машин вместо меток в блоках 3.2 и 3.3.

        Точки маршрута берутся ровно в том же виде, что и для старого
        плоского блока: сначала loadings/unloadings из данных, затем
        исторические поля contract["loading_address"] /
        contract["unloading_address_1..2"].

        Если метки в шаблоне нет, _replace_marker ничего не делает —
        работает старый путь ({{loading_block}} / {{unloading_block}}).

        Важно про нумерацию и привязку (исправление бага «Выгрузка 1, 2, 4»):
          * номер блока в заголовке — порядковый номер ВЫВЕДЕННОГО блока
            (1, 2, 3… без пропусков);
          * loading_index / unloading_index машины — исходный индекс точки
            в массиве UI (1-based) и с нумерацией блоков не связан.

        Перед вставкой убираются старые таблицы машин из блоков 3.2 и 3.3
        ({{car_1..12_*}}): их роль теперь выполняют таблицы по погрузкам,
        и без удаления одни и те же VIN печатались дважды.
        """
        loadings, unloadings = self._resolve_route_points(contract_data)
        vehicles = contract_data.vehicles

        # В шаблоне нового образца таблица машин стоит сразу после метки —
        # это дубль таблиц по погрузкам. В старом шаблоне меток нет, поэтому
        # ничего не удаляется и fallback работает как раньше.
        removed = self._remove_legacy_vehicle_tables(doc)
        if removed:
            logger.info(
                f"Удалено старых таблиц машин (дубли в блоках 3.2/3.3): {removed}"
            )

        if loadings:
            inserted = self._replace_marker(
                doc,
                self.LOADING_TABLE_MARKER,
                loadings,
                vehicles,
                index_field="loading_index",
                title_prefix="Погрузка",
                no_point_title="Машины без привязки к конкретной погрузке",
            )
            logger.info(
                f"Блок 3.2: точек {len(loadings)}, выведено блоков {inserted}"
            )
        if unloadings:
            inserted = self._replace_marker(
                doc,
                self.UNLOADING_TABLE_MARKER,
                unloadings,
                vehicles,
                index_field="unloading_index",
                title_prefix="Выгрузка",
                no_point_title="Машины без привязки к конкретной выгрузке",
            )
            logger.info(
                f"Блок 3.3: точек {len(unloadings)}, выведено блоков {inserted}"
            )

    def _remove_legacy_vehicle_tables(self, doc) -> int:
        """
        Убирает старые таблицы машин из блоков 3.2 «Погрузка» и 3.3 «Выгрузка».

        В шаблоне таблиц машин две: в 3.1 «Груз» (перечень груза) и сразу
        после метки {{LOADING_TABLE_HERE}} в 3.2. После перехода на таблицы
        по погрузкам/выгрузкам вторая стала дублем: одни и те же VIN
        печатались и в ней, и в таблицах погрузок. Таблицу в 3.1 не трогаем.

        Возвращает число удалённых таблиц. Если меток в шаблоне нет
        (старый шаблон) — возвращает 0 и ничего не меняет.
        """
        removed = 0
        for marker in (self.LOADING_TABLE_MARKER, self.UNLOADING_TABLE_MARKER):
            paragraph = self._find_marker_paragraph(doc, marker)
            if paragraph is None:
                continue
            removed += self._remove_vehicle_tables_after(doc, paragraph)

        return removed

    def _remove_vehicle_tables_after(self, doc, paragraph) -> int:
        """
        Удаляет таблицы машин, идущие сразу после абзаца-метки.

        Пустые абзацы между меткой и таблицей пропускаются. Просмотр
        прекращается на первом непустом абзаце или на таблице другого вида
        (реквизиты, шапка договора) — чужие таблицы не затрагиваются.
        """
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        removed = 0
        node = paragraph._p.getnext()

        while node is not None:
            next_node = node.getnext()
            tag = node.tag.split("}")[1]

            if tag == "tbl":
                if not self._is_vehicle_table(Table(node, doc)):
                    break
                node.getparent().remove(node)
                removed += 1
            elif tag == "p":
                if Paragraph(node, doc).text.strip():
                    break
            else:
                break

            node = next_node

        return removed

    def _resolve_route_points(self, contract_data: ContractData):
        """
        Возвращает (loadings, unloadings) — точки маршрута с учётом
        исторических полей в contract (как в _build_replacements_map).
        """
        contract = contract_data.contract
        loadings = list(contract_data.loadings)
        unloadings = list(contract_data.unloadings)

        if not loadings and contract.get("loading_address"):
            loadings = [{
                "name": contract.get("loading_name", ""),
                "address": contract.get("loading_address", ""),
                "date": contract.get("loading_date", ""),
                "time_window": contract.get("loading_time_window", ""),
            }]

        if not unloadings:
            legacy = []
            if contract.get("unloading_address_1"):
                legacy.append({
                    "name": contract.get("unloading_name_1", ""),
                    "address": contract.get("unloading_address_1", ""),
                    "date": contract.get("unloading_date", ""),
                    "time_window": contract.get("unloading_time_window", ""),
                })
            if contract.get("unloading_address_2"):
                legacy.append({
                    "name": contract.get("unloading_name_2", ""),
                    "address": contract.get("unloading_address_2", ""),
                    "date": contract.get("unloading_date", ""),
                    "time_window": contract.get("unloading_time_window", ""),
                })
            unloadings = legacy

        return loadings, unloadings

    def _replace_marker(
        self,
        doc,
        marker_text: str,
        points: List[Dict[str, Any]],
        vehicles: List[Dict[str, Any]],
        index_field: str,
        title_prefix: str,
        no_point_title: str,
    ) -> int:
        """
        Заменяет абзац-метку на последовательность «абзац-заголовок +
        таблица + пустой абзац» для каждой непустой точки маршрута.

        Возвращает число выведенных блоков. Если метки в документе нет —
        возвращает 0, ничего не меняя: это шаблон старого образца, где
        работает плоский {{loading_block}}.

        Два разных номера, которые нельзя путать:
          * ``index`` — исходный номер точки в массиве UI (1-based). Именно
            он лежит в vehicle[index_field] и только по нему машина
            привязывается к точке;
          * ``shown`` — порядковый номер выведенного блока (1, 2, 3… без
            пропусков), он и попадает в заголовок «Погрузка N: адрес».
        Пропуск точки с пустым адресом больше не сдвигает нумерацию и не
        переносит машины в чужую таблицу.

        Порядок работ:
          1. Находим абзац с текстом-меткой (с точностью до пробелов).
          2. Собираем элементы в конце документа (doc.add_paragraph /
             doc.add_table — другого способа python-docx не даёт).
          3. Переносим их на место метки через lxml addnext.
          4. Удаляем абзац с меткой.
        """
        paragraph = self._find_marker_paragraph(doc, marker_text)
        if paragraph is None:
            return 0

        # Шаблон абзаца-заголовка: метка стоит там, где должен быть
        # заголовок, поэтому берём её же (до переноса элементов).
        head_template = self._outline_paragraph(paragraph)

        elements = []
        cargo = self._cargo_vehicles(vehicles)
        shown = 0          # порядковый номер выведенного блока
        numbered = 0       # сколько машин уже пронумеровано в разделе
        hidden_ids = set()  # машины, чья точка пропущена из-за пустого адреса

        for index, point in enumerate(points, 1):
            # Привязка — строго по исходному индексу точки в UI.
            assigned = [
                v for v in cargo
                if self._matches_index(v, index_field, index)
            ]
            if not assigned:
                # Пустая погрузка/выгрузка: ни заголовка, ни таблицы.
                logger.debug(f"{title_prefix} {index}: машин нет — блок пропущен")
                continue

            address = self._normalized_point_address(point)
            if not address:
                # Машины привязаны, но адреса нет — выводить нечего.
                hidden_ids.update(id(v) for v in assigned)
                logger.warning(
                    f"Машины привязаны к точке «{title_prefix} {index}», "
                    f"но адрес точки пуст — строк не выведено: {len(assigned)}"
                )
                continue

            shown += 1
            elements.append(self._make_point_heading(
                doc, head_template,
                self._point_title(title_prefix, shown, point),
            ))
            # Нумерация машин сквозная по разделу (1, 2, 3, 4, 5…), а не
            # своя в каждой таблице: см. _make_vehicle_table.
            elements.append(self._make_vehicle_table(
                doc, assigned, start_number=numbered + 1
            ))
            numbered += len(assigned)
            elements.append(self._make_spacer(doc))

        # Машины без привязки к конкретной точке («— (все)» в интерфейсе) и
        # машины с несуществующей точкой. Машины пропущенных по адресу точек
        # исключены: про них уже сказано в предупреждении выше — в чужую
        # таблицу они попасть не должны.
        unassigned = [
            v for v in cargo
            if not self._has_point(v, index_field, points)
            and id(v) not in hidden_ids
        ]
        if unassigned:
            elements.append(self._make_point_heading(
                doc, head_template, no_point_title
            ))
            elements.append(self._make_vehicle_table(
                doc, unassigned, start_number=numbered + 1
            ))
            elements.append(self._make_spacer(doc))

        if not elements:
            # Ни одной непустой точки и ни одной машины без привязки —
            # оставляем явную пометку вместо пустого места.
            elements.append(self._make_point_heading(
                doc, head_template, self.NO_VEHICLES_TEXT
            ))

        # addnext вставляет каждый следующий элемент после предыдущего,
        # поэтому порядок elements сохраняется.
        anchor = paragraph._p
        for element in elements:
            anchor.addnext(element)
            anchor = element

        paragraph._p.getparent().remove(paragraph._p)

        # В лог — только счётчики: адреса, VIN и марки в логах не место.
        logger.info(
            f"Метка {marker_text}: точек {len(points)}, выведено блоков {shown}, "
            f"машин пронумеровано {numbered + len(unassigned)}, "
            f"без привязки {len(unassigned)}, "
            f"скрыто из-за пустых точек {len(hidden_ids)}"
        )
        return shown

    def _make_vehicle_table(self, doc, vehicles: List[Dict[str, Any]],
                            start_number: int = 1):
        """
        Таблица «№ | Марка/Модель | VIN-номер» по списку машин.

        Порядок строк — порядок машин в исходном массиве, без сортировки.
        Оформление повторяет таблицу машин из шаблона (блок 3.1): и шрифт,
        и заливку ячейки, и границы. Копировать один шрифт нельзя — в
        шаблоне шапка оформлена белым текстом на тёмной заливке, и без
        заливки белый текст становится невидимым (белое на белом).

        ``start_number`` — номер первой строки таблицы. Нумерация машин
        СКВОЗНАЯ по всем точкам раздела: три точки по 2 + 2 + 1 машине
        дают 1, 2, 3, 4, 5, а не 1, 2 / 1, 2 / 1. Раньше счёт начинался
        заново в каждой таблице, и в договоре пять машин выглядели как три
        разные группы.
        """
        sample = self._sample_vehicle_table(doc)

        table = doc.add_table(rows=1, cols=3)
        table.style = self.VEHICLE_TABLE_STYLE
        table.autofit = False

        header_samples = list(sample.rows[0].cells) if sample is not None else []
        data_samples = (
            list(sample.rows[1].cells)
            if (sample is not None and len(sample.rows) > 1)
            else []
        )

        for index, (cell, title) in enumerate(
            zip(table.rows[0].cells, self.VEHICLE_TABLE_HEADERS)
        ):
            self._fill_table_cell(
                cell, title,
                self._sample_cell(header_samples, index),
                bold=True,
            )

        for offset, vehicle in enumerate(vehicles):
            row = table.add_row()
            values = (
                str(start_number + offset),
                self._get_vehicle_brand(vehicle),
                self._get_vehicle_vin(vehicle),
            )
            for index, (cell, value) in enumerate(zip(row.cells, values)):
                self._fill_table_cell(
                    cell, value,
                    self._sample_cell(data_samples, index)
                    or self._sample_cell(header_samples, index),
                    bold=False,
                )

        self._set_grid_widths(table, self.VEHICLE_TABLE_COLUMN_WIDTHS_CM)

        return table._tbl

    def _sample_vehicle_table(self, doc):
        """
        Таблица машин из шаблона — образец оформления.

        Это таблица блока 3.1 «Груз»: она остаётся в документе, в отличие
        от таблицы в 3.2, которую генератор удаляет как дубль.
        """
        for existing in doc.tables:
            if self._is_vehicle_table(existing):
                return existing
        return None

    # ─────────────────────────────────────────────────────────
    # Форматирование и данные строк таблиц
    # ─────────────────────────────────────────────────────────

    def _cargo_vehicles(self, vehicles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Машины, которые попадают в таблицы погрузок/выгрузок.

        Тягач, полуприцеп и прицеп исключаются: они описаны в блоке 3.1.
        Строки без VIN и без марки (пустые) тоже не выводятся.
        """
        result = []
        for vehicle in vehicles:
            if vehicle.get("vehicle_type") in self.NON_CARGO_VEHICLE_TYPES:
                continue
            if not (self._get_vehicle_vin(vehicle) or self._get_vehicle_brand(vehicle)):
                continue
            result.append(vehicle)
        return result

    @classmethod
    def _is_route_vehicle(cls, vehicle: Dict[str, Any]) -> bool:
        """
        True, если машину вообще можно выводить в таблицах блоков 3.2 / 3.3.

        Тягач, полуприцеп и прицеп описываются в блоке 3.1, а строка без
        VIN и без марки — это пустая строка таблицы UI.

        S1: classmethod вместо staticmethod — раньше здесь стояли жёсткие
        ссылки ContractGenerator.NON_CARGO_VEHICLE_TYPES и т.п., которые
        после переименования класса дали бы NameError.
        """
        if vehicle.get("vehicle_type") in cls.NON_CARGO_VEHICLE_TYPES:
            return False
        return bool(
            cls._get_vehicle_vin(vehicle)
            or cls._get_vehicle_brand(vehicle)
        )

    @staticmethod
    def _raw_point_index(vehicle: Dict[str, Any], index_field: str) -> int:
        """
        Исходный индекс точки из vehicle[index_field]: 1-based, 0 — нет привязки.

        Никаких фильтров по типу ТС и заполненности здесь нет: это чистый
        разбор значения, одинаково нужный и привязке (``_matches_index``),
        и проверке «есть ли вообще привязка» (``_has_point``).
        """
        raw = vehicle.get(index_field, 0)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return 0

        try:
            value = int(raw)
        except (ValueError, TypeError):
            return 0

        return value if value > 0 else 0

    def _matches_index(self, vehicle: Dict[str, Any], index_field: str, index: int) -> bool:
        """
        True, если машина привязана именно к точке с ИСХОДНЫМ номером index.

        index — номер точки в массиве UI (1-based), а не порядковый номер
        выведенного блока: пропуск пустой точки его не меняет.
        """
        if not self._is_route_vehicle(vehicle):
            return False
        return self._raw_point_index(vehicle, index_field) == index

    def _has_point(self, vehicle: Dict[str, Any], index_field: str,
                   points: Optional[List[Dict[str, Any]]] = None) -> bool:
        """
        True, если машина привязана к реально выводимой точке маршрута.

        0 / None / пустая строка («— (все)» в интерфейсе) означают, что
        привязки нет.

        Если передан массив points, дополнительно проверяется, что точка
        с таким номером существует и её адрес не пуст. Машина, привязанная
        к несуществующей точке, попадёт в блок «Машины без привязки…»;
        машина, чья точка пропущена из-за пустого адреса, не выводится
        вовсе — про неё пишется предупреждение, а не строка в чужой
        таблице.

        Вызывается для строк, уже отфильтрованных через _cargo_vehicles,
        поэтому повторно тип ТС и наличие VIN здесь не проверяются.
        """
        index = self._raw_point_index(vehicle, index_field)
        if index <= 0:
            return False

        if points is not None:
            if index > len(points):
                return False
            point = points[index - 1]
            if not self._normalized_point_address(point):
                return False

        return True

    @classmethod
    def _get_vehicle_brand(cls, vehicle: Dict[str, Any]) -> str:
        return cls._single_line(vehicle.get("brand_model") or "")

    @classmethod
    def _get_vehicle_vin(cls, vehicle: Dict[str, Any]) -> str:
        return cls._single_line(vehicle.get("vin") or "")

    # ─────────────────────────────────────────────────────────
    # Числовые реквизиты
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _digits_only(value: Any) -> str:
        """
        Только цифры из значения — для ИНН, КПП, ОГРН, БИК и счетов.

        Числовые реквизиты в бланке печатаются без слов, пробелов и знаков:
        «Корреспондентский счет БИК 044030786» в поле БИК — это мусор из
        исходного документа (распознавание или вставка), а не значение.
        Пустое поле остаётся пустым: ничего не выдумываем.
        """
        return re.sub(r"\D", "", "" if value is None else str(value))

    # ─────────────────────────────────────────────────────────
    # УДАЛЕНИЕ ПУСТЫХ СТРОК В ТАБЛИЦЕ ТС
    # ─────────────────────────────────────────────────────────

    def _remove_empty_vehicle_rows(self, doc) -> None:
        for table in doc.tables:
            if not self._is_vehicle_table(table):
                continue

            for i in range(len(table.rows) - 1, 0, -1):
                row = table.rows[i]
                if len(row.cells) >= 2:
                    brand_text = row.cells[1].text.strip()
                    vin_text = row.cells[2].text.strip() if len(row.cells) >= 3 else ""
                    if not brand_text and not vin_text:
                        self._delete_row(table, row)
                        logger.debug(f"Удалена пустая строка {i} из таблицы ТС")

    def _is_vehicle_table(self, table) -> bool:
        if not table.rows:
            return False
        headers = [cell.text.strip() for cell in table.rows[0].cells]
        for h in headers:
            h_norm = h.replace("–", "-").replace("—", "-").strip()
            if h_norm in ("VIN-номер", "VIN- номер", "Марка/Модель"):
                return True
        return False

    # ─────────────────────────────────────────────────────────
    # НАИМЕНОВАНИЕ САЛОНА В ЗАГОЛОВКЕ ТОЧКИ
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _normalized_point_name(cls, point: Dict[str, Any]) -> str:
        """
        Наименование салона точки одной строкой.

        Ключ называется `name` (так его отдаёт вкладка «Условия договора»),
        но принимается и `salon_name`: так поле называется в справочнике
        адресов и в блоке грузополучателя Логистикса — если точка пришла
        оттуда, имя не потеряется. Поле необязательное: точку можно ввести
        руками, и тогда наименования у неё просто нет.
        """
        name = point.get("name")
        if not name:
            name = point.get("salon_name")
        return re.sub(r"\s+", " ", str(name or "")).strip()

    @classmethod
    def _point_title(cls, prefix: str, number: int, point: Dict[str, Any]) -> str:
        """
        Заголовок блока точки: «Выгрузка 1: ООО «Салон» г. Москва, Перерва 19».

        Наименование салона идёт ПЕРЕД адресом через пробел — как в заявке
        заказчика. Если наименования нет (записи в справочнике не нашлось
        или точку вводили руками), печатается только адрес, как раньше:
        ни прочерка, ни пустого места вместо имени в бланке не будет.
        """
        address = cls._normalized_point_address(point)
        name = cls._normalized_point_name(point)

        body = f"{name} {address}".strip() if name else address
        return f"{prefix} {number}: {body}".strip()

    # ─────────────────────────────────────────────────────────
    # БЛОК ТОЧЕК С ПРИВЯЗКОЙ МАШИН
    # ─────────────────────────────────────────────────────────

    def _build_points_block_with_vehicles(
        self,
        points: List[Dict[str, Any]],
        title: str,
        vehicles: List[Dict[str, Any]],
        index_field: str,
    ) -> str:
        if not points:
            return ""

        point_vehicles = {i: [] for i in range(1, len(points) + 1)}

        for v in vehicles:
            vtype = v.get("vehicle_type", "")
            if vtype in ("Тягач", "Полуприцеп", "Прицеп"):
                continue

            vin = (v.get("vin") or "").strip()
            if not vin:
                continue

            try:
                idx = int(v.get(index_field, 0) or 0)
            except (ValueError, TypeError):
                idx = 0

            if idx == 0:
                for p_idx in point_vehicles:
                    point_vehicles[p_idx].append(vin)
            elif 1 <= idx <= len(points):
                point_vehicles[idx].append(vin)
            else:
                for p_idx in point_vehicles:
                    point_vehicles[p_idx].append(vin)

        lines = []
        numbered = 0  # сквозная нумерация машин по всем точкам раздела
        for i, p in enumerate(points, 1):
            # Заголовок точки — тем же помощником, что и таблицы по точкам:
            # наименование салона перед адресом, без имени — только адрес.
            parts = [self._point_title(title, i, p)]
            date_str = self._format_date_full(p.get("date", ""))
            if date_str:
                parts.append(date_str)
            time_str = (p.get("time_window", "") or "").strip()
            if time_str:
                parts.append(time_str)
            lines.append(" — ".join(parts))

            vins_here = point_vehicles.get(i, [])
            if vins_here:
                # Номера сквозные по разделу — как в таблицах по точкам:
                # «Машины (2 шт.): 3. VIN…, 4. VIN…». Иначе плоский блок и
                # таблицы нумеровали бы одни машины по-разному.
                marked = [
                    f"{numbered + offset}. {vin}"
                    for offset, vin in enumerate(vins_here, 1)
                ]
                numbered += len(vins_here)
                lines.append(f"  Машины ({len(vins_here)} шт.): {', '.join(marked)}")
            else:
                lines.append("  Машины: —")

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────
    # ДИНАМИЧЕСКИЕ СТОРОНЫ: ИП ИЛИ ООО
    # ─────────────────────────────────────────────────────────
    #
    # Бланк перевозки печатал сторону одной жёсткой формулировкой: в п. 1.1
    # стояло «в лице Генерального директора Ахмедова Тимура Артуровича»
    # (константа, а не поле), в п. 9 — «Генеральный директор /Т.А. Ахмедов /»,
    # а ОГРНИП был захардкожен. У заказчика-ИП в договоре печатался чужой
    # директор. Ниже считаются ключи, по которым бланк выбирает ветку.

    #: Приставки, которые справочник хранит в full_name индивидуального
    #: предпринимателя («Индивидуальный предприниматель Добросоцкий А.Н.»).
    #: В бланке приставку печатает сам шаблон ({{*_legal_form}}), поэтому из
    #: ФИО её надо убрать — иначе выходит «Индивидуальный предприниматель
    #: Индивидуальный предприниматель Добросоцкий…». «ООО » стоит здесь же:
    #: запись могла остаться от прежнего вида стороны, а в ветви ИП это
    #: чужое сокращение («Индивидуальный предприниматель ООО «Ромашка»»).
    IP_NAME_PREFIXES = ("Индивидуальный предприниматель ", "ИП ", "ООО ")

    #: Окончания женских отчеств — по ним определяется род ФИО.
    FEMALE_PATRONYMIC_ENDINGS = ("овна", "евна", "ична", "инична")

    @classmethod
    def _clean_ip_name(cls, full_name: Any) -> str:
        """
        ФИО индивидуального предпринимателя без приставки вида лица.

        Приставка не часть имени, а вид лица: её печатает шаблон отдельным
        плейсхолдером ({{carrier_legal_form}} / {{client_legal_form}}).
        Значение без приставки остаётся как есть — у ООО наименование не
        трогается вовсе (метод вызывается только для ИП).
        """
        text = str(full_name or "").strip()
        for prefix in cls.IP_NAME_PREFIXES:
            if text.lower().startswith(prefix.lower()):
                return text[len(prefix):].strip()
        return text

    @classmethod
    def _gender_from_name(cls, full_name: Any) -> str:
        """
        Род («male» / «female») по отчеству ФИО.

        Тонкая обёртка над `core.contracts.ru_morphology.detect_gender`:
        правила живут в одном месте (там же склонение ФИО и должности),
        а имя метода оставлено прежним — на него ссылаются тесты и
        внешний код.
        """
        return detect_gender(full_name)

    @staticmethod
    def _legal_form_prefix(legal_form: Any, full_name: Any) -> str:
        """
        «Общество с ограниченной ответственностью » к наименованию стороны.

        Приставка нужна, потому что в справочнике наименование ООО часто
        лежит коротким («ООО «Ромашка»»). Но если полное наименование УЖЕ
        начинается с этой приставки — целиком или сокращённо («ООО»), —
        второй раз она не печатается: «Общество с ограниченной
        ответственностью ООО «Ромашка»» читается как ошибка.

        У ИП приставка не добавляется никогда: приставку вида лица снимает
        `_clean_ip_name` (иначе в договоре выходило «Индивидуальный
        предприниматель Индивидуальный предприниматель Добросоцкий…»), а
        саму приставку печатает ветка ИП своим ключом `{{*_legal_form}}`.
        """
        form = str(legal_form or "").strip()
        if form.lower() == "индивидуальный предприниматель":
            return ""

        name = str(full_name or "").strip()
        if not form or not name:
            return form + " " if form else ""

        if name.lower().startswith(form.lower()):
            return ""

        # Сокращение вида лица: «ООО». Составлять его из первых букв слов
        # нельзя — «Общество с ограниченной ответственностью» дало бы «О»
        # (со строчных слов буквы не берутся), поэтому пара задана явно.
        # Сокращение сверяется и с пробелом, и с кавычкой: в справочнике
        # встречается «ООО «Ромашка»».
        short_form = LEGAL_FORM_SHORT.get(form.lower(), "")
        if short_form and name.upper().startswith(short_form.upper()):
            return ""

        return form + " "

    @staticmethod
    def _resolve_carrier_type(carrier_type: Any, is_carrier_ip_hint: bool) -> str:
        """
        Вид перевозчика: «ООО (с НДС)» / «ИП с НДС» / «ИП без НДС».

        Тип приходит из вкладки «Договор» и выбирает бланк, но он может
        разойтись с данными стороны: в справочнике ИП записан как
        «Индивидуальный предприниматель Пестряев А.Н.», а тип в форме
        остался прежним «ООО (с НДС)». Тогда ветвь бланка выбирается по
        ФАКТУ (приставка «ИП» или entity_type из справочника), а не по
        устаревшему переключателю: иначе ИП печатался как ООО — с ОГРН,
        КПП и «именуемое».

        Ставка НДС при этом сохраняется: «ООО (с НДС)» + ИП → «ИП с НДС»,
        «ООО (без НДС)» + ИП → «ИП без НДС».
        """
        text = str(carrier_type or "").strip()
        if not is_carrier_ip_hint:
            return text or "ООО (с НДС)"
        return "ИП без НДС" if "без НДС" in text else "ИП с НДС"

    @staticmethod
    def _paren_suffix(full_name: Any, short_name: Any) -> str:
        """
        « (сокращённое наименование)» — только если оно отличается от полного.

        В справочнике short_name часто повторяет full_name (в рабочей базе
        так у ИП и у части ООО) — тогда в договоре печаталось
        «ООО «Ромашка» (ООО «Ромашка»)». Пустая скобка не выводится.
        """
        full = str(full_name or "").strip()
        short = str(short_name or "").strip()
        if short and short != full:
            return f" ({short})"
        return ""

    @staticmethod
    def _filled(value: Any) -> bool:
        """
        True, если значение непустое — для флагов «печатать строку или нет».

        «0» и «0.0» считаются пустыми: так интерфейс отдаёт незаполненный
        год выпуска (ШАГ FIX-6, часть C), и строка «Год выпуска: 0» в бланке
        не нужна. Для КПП это же правило закрывает запись с КПП = «0».
        """
        text = str(value if value is not None else "").strip()
        return bool(text) and text not in ("0", "0.0")

    # ─────────────────────────────────────────────────────────
    # КАРТА ЗАМЕН
    # ─────────────────────────────────────────────────────────

    #: Ключи дат в условиях договора. Значения приходят из UI (ISO), из
    #: распознавания и из справочников — в любом из форматов core.dates.
    DATE_KEYS = ("date", "loading_plan_date", "unloading_plan_date")

    @staticmethod
    def _normalize_contract_dates(contract: Dict[str, Any]) -> None:
        """
        Даты договора — в ISO, как их ждёт бланк.

        Бланк печатает дату как «{{loading_plan_date}} г.», то есть
        ПОДСТАВЛЯЕТ значение как есть. Пока в это поле попадала только дата
        из QDateEdit (ISO), вопросов не было; но распознанный документ и
        справочники отдают дату в любом из семи форматов core/dates
        («24.09.2026», «2026.09.24», «24/09/2026», «20260924»…), и в договоре
        печаталось «2026.09.24 г.» — документ с датой не в российском виде.

        Приводим к ISO ЗДЕСЬ, а не в каждом бланке: правило одно для всех
        ключей дат, а разбор уже есть — core.dates.to_iso.

        Что НЕ трогаем: значение, которое разобрать не удалось. Оно остаётся
        как есть — пусть мусор виден в договоре и ловится валидатором. Это
        лучше, чем молча напечатать пустое место вместо даты.
        """
        for key in PerevozkaGenerator.DATE_KEYS:
            value = contract.get(key)
            if not value:
                continue
            iso = to_iso(value)
            if iso:
                contract[key] = iso

    def _build_replacements_map(self, data: Dict[str, Any]) -> Dict[str, str]:
        contract_data = ContractData.coerce(data)
        replacements: Dict[str, str] = {}

        # ── Данные договора ──
        contract = contract_data.contract
        self._normalize_contract_dates(contract)
        replacements["contract_number"] = contract.get("number", "")

        date_iso = contract.get("date", "")
        replacements["contract_date"] = self._day_of_month(date_iso) if date_iso else ""
        replacements["contract_month"] = self._month_name(date_iso)
        # Год договора — из ЕГО даты, а не из сегодняшнего числа: договор
        # от 23.09.2026 не должен печатать «2027», если его распечатали
        # в январе. Даты нет или она не разбирается — текущий год.
        replacements["contract_year"] = self._contract_year(date_iso)

        # Город заключения: явное поле → город первой погрузки →
        # юр. адрес перевозчика → «Москва» (историческое поведение, баг 2.9).
        city = contract_data.resolved_city()
        replacements["city"] = city.replace("г. ", "").replace("г.", "").strip()

        # ── Перевозчик ──
        carrier = contract_data.carrier

        # Вид перевозчика. Главный признак — тип из вкладки «Договор» (он же
        # выбирает бланк), но есть и запасные: `entity_type` из справочника,
        # распознавания или DaData и приставка «ИП» в наименовании.
        # Это не украшение: в справочнике ИП записан как «Индивидуальный
        # предприниматель Пестряев А.Н.», а тип в форме мог остаться
        # прежним «ООО (с НДС)» — тогда ИП печатался как ООО (в договоре
        # выходило «ОГРН», «КПП» и «именуемое»).
        carrier_full_raw = str(carrier.get("full_name", "") or "").strip()
        carrier_full_lower = carrier_full_raw.lower()
        is_carrier_ip_hint = (
            str(carrier.get("entity_type") or "").strip().upper().startswith("ИП")
            or carrier_full_lower.startswith("индивидуальный предприниматель")
            or carrier_full_lower.startswith("ип ")
        )

        carrier_type_ui = self._resolve_carrier_type(
            contract.get("carrier_type")
            or carrier.get("carrier_type")
            or ("ИП с НДС" if is_carrier_ip_hint else "ООО (с НДС)"),
            is_carrier_ip_hint,
        )

        vat_rate_num = contract.get("vat_rate_num")
        if vat_rate_num is None:
            vat_rate_str = contract.get("vat_rate", "22%")
            try:
                vat_rate_num = float(str(vat_rate_str).replace("%", "").strip())
            except (ValueError, TypeError):
                vat_rate_num = 22.0

        is_ip_type = "ИП" in carrier_type_ui
        # Вид ООО/ИП считается по ФАКТУ (приставка в наименовании или
        # entity_type из справочника), а не только по переключателю в форме:
        # они могут разойтись, если оператор загрузил ИП в форму, где тип
        # остался прежним. Раньше в этом случае в договоре печатались ОГРН,
        # КПП и «именуемое» — ИП выглядел как ООО.
        is_carrier_ip = is_carrier_ip_hint or is_ip_type
        is_ooo = not is_carrier_ip
        is_ip_with_vat = is_carrier_ip and "без НДС" not in carrier_type_ui
        is_ip_without_vat = is_carrier_ip and "без НДС" in carrier_type_ui

        carrier_gender = detect_gender(carrier_full_raw)
        # Приставку «Индивидуальный предприниматель» печатает сам бланк
        # ({{carrier_legal_form}}) — в ФИО она не нужна, иначе в договоре
        # выходит «Индивидуальный предприниматель Индивидуальный
        # предприниматель Добросоцкий…».
        carrier_full = (
            self._clean_ip_name(carrier_full_raw) if is_carrier_ip
            else carrier_full_raw
        )
        carrier_director_name = str(carrier.get("director_name", "") or "").strip()
        # Пол подписанта: у ООО это директор, у ИП — он сам (у ИП в
        # `director_name` справочника пусто, род берётся из ФИО).
        carrier_signer_gender = detect_gender(
            carrier_director_name or carrier_full_raw
        )
        # Подписант ООО: должность и ФИО из данных. Пустая должность —
        # «Директор» (так печатал прежний бланк константой).
        carrier_position = (
            str(carrier.get("director_position", "") or "").strip() or "Директор"
        )
        carrier_basis = (
            str(carrier.get("basis", "") or "").strip()
            or ("Устава" if not is_carrier_ip
                else "свидетельства о государственной регистрации")
        )

        if is_ooo:
            legal_form = "Общество с ограниченной ответственностью"
            pronoun = "именуемое"
            director_position_full = genitive_position(carrier_position)
            director_position_short = carrier_position
            kpp = self._digits_only(carrier.get("kpp"))
            ogrn_label = "ОГРН"
            nds_status_text = ("Перевозчик подтверждает, что применяет общую систему "
                               "налогообложения и является плательщиком НДС.")
        else:
            legal_form = "Индивидуальный предприниматель"
            pronoun = pronoun_by_gender(carrier_gender)
            director_position_full = "Индивидуального предпринимателя"
            director_position_short = "Индивидуальный предприниматель"
            kpp = ""
            ogrn_label = "ОГРНИП"
            if is_ip_without_vat:
                vat_rate_num = 0.0
                nds_status_text = ("Перевозчик подтверждает, что применяет упрощённую "
                                   "систему налогообложения и не является плательщиком НДС.")
            else:
                nds_status_text = ("Перевозчик подтверждает, что применяет общую систему "
                                   "налогообложения и является плательщиком НДС.")

        logger.info(
            f"ContractGenerator: тип={carrier_type_ui}, "
            f"НДС={vat_rate_num}%, ООО={is_ooo}, "
            f"ИП_с_НДС={is_ip_with_vat}, ИП_без_НДС={is_ip_without_vat}"
        )

        replacements["is_carrier_ip"] = is_carrier_ip
        replacements["carrier_legal_form"] = legal_form
        # «Общество с ограниченной ответственностью » — отдельным ключом:
        # повтор приставки в наименовании бланк не должен печатать дважды.
        replacements["carrier_legal_form_prefix"] = self._legal_form_prefix(
            legal_form, carrier_full
        )
        # Род причастия: у ИП «действующий» («действующая» — женщина),
        # у ООО «действующего» — «в лице директора …, действующего…».
        replacements["carrier_acting"] = (
            "действующего" if is_ooo else acting_by_gender(carrier_gender)
        )
        # Род причастия в п. 1.2 — по полу ПОДПИСАНТА (директора у ООО,
        # самого ИП у ИП): у директора-женщины «действующей».
        replacements["carrier_acting_genitive"] = (
            "действующей" if carrier_signer_gender == "female" else "действующего"
        )
        replacements["carrier_pronoun"] = pronoun
        replacements["carrier_basis"] = carrier_basis
        replacements["carrier_full_name"] = carrier_full
        replacements["carrier_name"] = carrier.get("short_name", "") or carrier.get("full_name", "")
        # Скобки с сокращённым наименованием — только если оно ОТЛИЧАЕТСЯ от
        # полного: в рабочей базе short_name часто его повторяет, и в договоре
        # печаталось «ООО «Ромашка» (ООО «Ромашка»)».
        replacements["carrier_name_in_parens"] = self._paren_suffix(
            carrier.get("full_name", ""),
            self._clean_ip_name(carrier.get("short_name", "")) if is_carrier_ip
            else carrier.get("short_name", ""),
        )
        # Реквизиты печатаются ТОЛЬКО цифрами. В поля ИНН / КПП / ОГРН / БИК /
        # корр. счёт могло попасть словосочетание из исходного документа
        # («Корреспондентский счет БИК 044030786») — из распознавания или
        # вставки из чужого файла; служебных слов в договоре быть не должно.
        replacements["carrier_inn"] = self._digits_only(carrier.get("inn"))
        replacements["carrier_kpp"] = kpp
        replacements["carrier_kpp_line"] = f"КПП {kpp}" if kpp else ""
        replacements["carrier_ogrn_label"] = ogrn_label
        replacements["carrier_ogrn"] = self._digits_only(carrier.get("ogrn"))
        replacements["carrier_address"] = carrier.get("legal_address", "")
        replacements["carrier_actual_address"] = carrier.get("actual_address", "")
        replacements["carrier_account"] = self._digits_only(carrier.get("bank_account"))
        replacements["carrier_bik"] = self._digits_only(carrier.get("bik"))
        replacements["carrier_bank"] = carrier.get("bank_name", "")
        replacements["carrier_corr_account"] = self._digits_only(
            carrier.get("correspondent_account")
        )
        replacements["carrier_director"] = carrier_director_name
        # ИП подписывает договор сам, а «директора» у него нет: в п. 9 бланка
        # стоит «{{carrier_director_position_short}} ________
        # /{{carrier_director}}/», и с пустым director_name справочника
        # печаталось «Индивидуальный предприниматель ________ //».
        if is_carrier_ip and carrier_full:
            replacements["carrier_director"] = carrier_full
        replacements["carrier_director_position"] = director_position_full
        replacements["carrier_director_position_short"] = director_position_short
        # Падежи п. 1.2: «в лице директора Иванова Ивана Ивановича,
        # действующего на основании Устава» — именительный в справочнике
        # и вкладке, родительный в бланке.
        replacements["carrier_director_position_genitive"] = (
            director_position_full if is_ooo
            else "Индивидуального предпринимателя"
        )
        replacements["carrier_director_genitive"] = (
            genitive_fio(carrier_director_name, carrier_signer_gender)
            if not is_carrier_ip else carrier_full
        )
        replacements["carrier_phone"] = carrier.get("phone", "")
        replacements["carrier_email"] = carrier.get("email", "")

        # ── Флаги «печатать строку или нет» ──
        # Пустое необязательное поле бланк печатал «дыркой»: «КПП ,»,
        # «Фактический адрес:», «E-mail:». Шаблон по этим ключам выводит
        # строку только с заполненным значением ({%p if has_... %}).
        replacements["has_carrier_kpp"] = self._filled(kpp)
        replacements["has_carrier_actual_address"] = self._filled(
            carrier.get("actual_address")
        )
        replacements["has_carrier_email"] = self._filled(carrier.get("email"))
        replacements["has_carrier_phone"] = self._filled(carrier.get("phone"))
        replacements["has_carrier_license_number"] = self._filled(
            carrier.get("license_number")
        )
        replacements["has_carrier_license_date"] = self._filled(
            carrier.get("license_date")
        )
        # Банковские реквизиты перевозчика (ШАГ «Банковские строки без
        # значения в п. 9»): пустое поле печатало строку-«дырку» — «р/с »,
        # «Банк: », «БИК », «Корр. счёт: ». Флаг считается по НАПЕЧАТАННОМУ
        # значению (счета и БИК печатаются только цифрами, `_digits_only`):
        # у «мусорного» поля значения в договоре нет, и строки быть не должно.
        replacements["has_carrier_account"] = self._filled(
            replacements["carrier_account"]
        )
        replacements["has_carrier_bik"] = self._filled(
            replacements["carrier_bik"]
        )
        replacements["has_carrier_corr_account"] = self._filled(
            replacements["carrier_corr_account"]
        )
        replacements["has_carrier_bank"] = self._filled(carrier.get("bank_name"))

        # ── Заказчик ──
        # ВАЖНО (Шаг 2): здесь больше нет выдуманных значений по умолчанию
        # («ООО ТЕХНОЛОГИСТИКА», ИНН 9709112631, «Ахмедова Т.А.»).
        # Если заказчик не заполнен — подставляется пустая строка, а
        # core.validator сообщает об этом пользователю до генерации.
        #
        # ШАГ «Динамические стороны»: заказчик бывает и ИП, и ООО, а бланк
        # печатал для обоих одно и то же — «в лице Генерального директора
        # Ахмедова Тимура Артуровича» (КОНСТАНТА шаблона, не поле) и подпись
        # «Генеральный директор /Т.А. Ахмедов /». Ветвь в бланке выбирают
        # ключи, которые считаются здесь.
        customer = contract_data.customer
        customer_full_raw = str(customer.get("full_name", "") or "").strip()
        customer_full_lower = customer_full_raw.lower()
        is_client_ip = (
            str(customer.get("entity_type") or "").strip().upper().startswith("ИП")
            or customer_full_lower.startswith("индивидуальный предприниматель")
            or customer_full_lower.startswith("ип ")
        )
        # Приставка — не часть имени: её печатает {{client_legal_form}}.
        customer_full = (
            self._clean_ip_name(customer_full_raw) if is_client_ip else customer_full_raw
        )
        client_director = str(customer.get("director_name", "") or "").strip()
        # Род — по ФИО ПОДПИСАНТА: у ООО это директор, у ИП — он сам
        # (у ИП в director_name справочника пусто, и род берётся из ФИО).
        client_gender = detect_gender(client_director or customer_full_raw)
        # Должность подписанта: у ООО это «Генеральный директор» (или своё
        # значение из справочника), пустое поле печатало бы «в лице  Иванов».
        client_position = (
            str(customer.get("director_position", "") or "").strip()
            or "Генеральный директор"
        )
        client_basis = (
            str(customer.get("basis", "") or "").strip()
            or ("свидетельства о государственной регистрации" if is_client_ip
                else "Устава")
        )

        replacements["is_client_ip"] = is_client_ip
        replacements["client_legal_form_prefix"] = self._legal_form_prefix(
            "Индивидуальный предприниматель" if is_client_ip
            else "Общество с ограниченной ответственностью",
            customer_full,
        )
        replacements["client_full_name"] = customer_full
        replacements["client_name"] = customer.get("short_name", "") or customer.get("full_name", "")
        replacements["client_name_in_parens"] = self._paren_suffix(
            customer.get("full_name", ""),
            self._clean_ip_name(customer.get("short_name", "")) if is_client_ip
            else customer.get("short_name", ""),
        )
        # У ИП вместо ОГРН — ОГРНИП (15 цифр), метка зависит от вида заказчика.
        replacements["client_ogrn_label"] = "ОГРНИП" if is_client_ip else "ОГРН"

        if is_client_ip:
            # Индивидуальный предприниматель действует сам, «в лице»
            # директора у него не бывает: основание — свидетельство
            # о государственной регистрации.
            replacements["client_legal_form"] = "Индивидуальный предприниматель"
            replacements["client_pronoun"] = pronoun_by_gender(client_gender)
            replacements["client_acting"] = acting_by_gender(client_gender)
            replacements["client_director_position_short"] = "Индивидуальный предприниматель"
            replacements["client_director"] = customer_full
            replacements["client_director_short"] = customer_full
        else:
            replacements["client_legal_form"] = (
                "Общество с ограниченной ответственностью"
            )
            replacements["client_pronoun"] = "именуемое"
            replacements["client_acting"] = "действующее"
            replacements["client_acting_genitive"] = (
                "действующей" if client_gender == "female" else "действующего"
            )
            replacements["client_director_position_short"] = client_position
            replacements["client_director"] = client_director
            replacements["client_director_short"] = self._short_fio(client_director)

        replacements["client_basis"] = client_basis
        # Падежи п. 1.1: «в лице директора Ахмедова Тимура Артуровича,
        # действующего на основании Устава» — в справочнике и на вкладке
        # именительный, в бланке родительный.
        replacements["client_director_position_genitive"] = (
            genitive_position(client_position) if not is_client_ip
            else "Индивидуального предпринимателя"
        )
        replacements["client_director_genitive"] = (
            genitive_fio(client_director, client_gender) if not is_client_ip
            else customer_full
        )
        # Род причастия — по полу ДИРЕКТОРА (ООО) или самого ИП.
        replacements["client_acting_genitive"] = (
            "действующей" if client_gender == "female" else "действующего"
        )

        replacements["client_inn"] = self._digits_only(customer.get("inn"))
        replacements["client_kpp"] = self._digits_only(customer.get("kpp"))
        replacements["client_kpp_line"] = (
            f"КПП {self._digits_only(customer.get('kpp'))}"
            if self._filled(self._digits_only(customer.get("kpp"))) else ""
        )
        replacements["client_ogrn"] = self._digits_only(customer.get("ogrn"))
        replacements["client_address"] = customer.get("legal_address", "")
        replacements["client_account"] = self._digits_only(customer.get("bank_account"))
        replacements["client_bik"] = self._digits_only(customer.get("bik"))
        replacements["client_bank"] = customer.get("bank_name", "")
        replacements["client_corr_account"] = self._digits_only(
            customer.get("correspondent_account")
        )
        # Историческое имя ключа (было = director_name): оставлено, чтобы
        # шаблоны и внешний код не сломались. В бланке печатается
        # client_director — он зависит от вида заказчика.
        replacements["client_director_position"] = customer.get("director_position", "")
        replacements["client_phone"] = customer.get("phone", "")
        replacements["client_email"] = customer.get("email", "")

        # ── Необязательные поля заказчика: пустые не печатаются ──
        replacements["has_client_kpp"] = self._filled(
            replacements["client_kpp"]
        )
        replacements["has_client_actual_address"] = self._filled(
            customer.get("actual_address")
        )
        replacements["has_client_phone"] = self._filled(customer.get("phone"))
        replacements["has_client_email"] = self._filled(customer.get("email"))

        # ── Банковские реквизиты заказчика: пустые строки не печатаются ──
        # Правило то же, что у перевозчика (см. выше): флаг — по напечатанному
        # значению. У заказчика строк две части: счёт и банк в одной строке
        # («р/с … в …»), поэтому банк проверяется отдельным ключом
        # `has_client_bank` — счёт без банка печатается, «в » без названия нет.
        replacements["has_client_account"] = self._filled(
            replacements["client_account"]
        )
        replacements["has_client_bik"] = self._filled(
            replacements["client_bik"]
        )
        replacements["has_client_corr_account"] = self._filled(
            replacements["client_corr_account"]
        )
        replacements["has_client_bank"] = self._filled(customer.get("bank_name"))

        # ── Водитель ──
        driver = contract_data.driver
        replacements["driver_name"] = driver.get("full_name", "")
        replacements["driver_birth_date"] = self._format_date_full(driver.get("birth_date", ""))
        replacements["driver_birth_place"] = driver.get("birth_place", "")

        passport_series = driver.get("passport_series", "")
        passport_number = driver.get("passport_number", "")
        replacements["driver_passport"] = f"{passport_series} {passport_number}".strip()

        replacements["driver_passport_date"] = self._format_date_full(driver.get("passport_issue_date", ""))
        replacements["driver_passport_issuer"] = driver.get("passport_issuer", "")
        replacements["driver_address"] = driver.get("registration_address", "")

        license_series = driver.get("license_series", "")
        license_number = driver.get("license_number", "")
        replacements["driver_license"] = f"{license_series} {license_number}".strip()

        replacements["driver_license_date"] = self._format_date_full(driver.get("license_issue_date", ""))
        replacements["driver_license_expiry"] = self._format_date_full(driver.get("license_expiry_date", ""))
        replacements["driver_license_categories"] = driver.get("license_categories", "")
        replacements["driver_phone"] = driver.get("phone", "")

        # ── Необязательные поля водителя: пустые строки не печатаются ──
        replacements["has_driver_birth_place"] = self._filled(driver.get("birth_place"))
        replacements["has_driver_passport_code"] = self._filled(
            driver.get("passport_code")
        )
        replacements["has_driver_license_categories"] = self._filled(
            driver.get("license_categories")
        )
        replacements["has_driver_license_expiry"] = self._filled(
            driver.get("license_expiry_date")
        )
        replacements["has_driver_phone"] = self._filled(driver.get("phone"))

        # ── Тягач / Прицеп ──
        # Исправление бага 2.2: UI отдаёт tractor/trailer ПЛОСКО
        # (MainWindow._collect_data → ContractData), а не в data["trailer"]["tractor"].
        tractor = contract_data.tractor
        trailer = contract_data.trailer

        replacements["tractor_brand"] = tractor.get("brand_model", "")
        replacements["tractor_plate"] = tractor.get("plate_number", "")
        replacements["tractor_color"] = tractor.get("color", "")
        replacements["tractor_year"] = str(tractor.get("year", ""))
        replacements["trailer_brand"] = trailer.get("brand_model", "")
        replacements["trailer_plate"] = trailer.get("plate_number", "")
        replacements["trailer_color"] = trailer.get("color", "")
        replacements["trailer_year"] = str(trailer.get("year", ""))

        # ── Необязательные поля ТС: пустые строки не печатаются ──
        replacements["has_tractor_color"] = self._filled(tractor.get("color"))
        replacements["has_tractor_year"] = self._filled(tractor.get("year"))
        replacements["has_trailer_color"] = self._filled(trailer.get("color"))
        replacements["has_trailer_year"] = self._filled(trailer.get("year"))

        # ── Груз и ТС ──
        all_vehicles = contract_data.vehicles
        vehicles = [
            v for v in all_vehicles
            if v.get("vehicle_type") not in ("Тягач", "Полуприцеп", "Прицеп")
        ]
        replacements["cargo_count"] = str(len(vehicles))

        for i in range(1, 13):
            if i <= len(vehicles):
                v = vehicles[i - 1]
                replacements[f"car_{i}_brand"] = v.get("brand_model", "")
                replacements[f"car_{i}_vin"] = v.get("vin", "")
                replacements[f"car_{i}_loading"] = self._get_point_label(
                    v.get("loading_index", 0), contract_data.loadings, "Погрузка"
                )
                replacements[f"car_{i}_unloading"] = self._get_point_label(
                    v.get("unloading_index", 0), contract_data.unloadings, "Выгрузка"
                )
            else:
                replacements[f"car_{i}_brand"] = ""
                replacements[f"car_{i}_vin"] = ""
                replacements[f"car_{i}_loading"] = ""
                replacements[f"car_{i}_unloading"] = ""

        # ── Погрузки / выгрузки ──
        # Единый разбор точек (включая исторические поля loading_address /
        # unloading_address_1..2) — см. _resolve_route_points: раньше эта
        # логика была продублирована здесь и в _insert_route_tables.
        loadings, unloadings = self._resolve_route_points(contract_data)

        # legacy: используется старыми шаблонами без метки
        # LOADING_TABLE_HERE / UNLOADING_TABLE_HERE — плоский текст с
        # погрузками/выгрузками и VIN-ами. Оставлен как fallback, пока в
        # шаблоне стоит {{loading_block}} вместо {{LOADING_TABLE_HERE}}.
        replacements["loading_block"] = self._build_points_block_with_vehicles(
            loadings, "Погрузка", all_vehicles, "loading_index"
        )
        replacements["unloading_block"] = self._build_points_block_with_vehicles(
            unloadings, "Выгрузка", all_vehicles, "unloading_index"
        )

        # Метки под таблицы погрузок/выгрузок (блоки 3.2 и 3.3).
        # docxtpl не умеет вставлять таблицы, поэтому {{LOADING_TABLE_HERE}}
        # превращается в текстовый маркер LOADING_TABLE_HERE, который
        # постобработка (_postprocess_document) находит и заменяет на
        # последовательность «жирный заголовок + таблица». В шаблонах без
        # метки эти ключи просто не используются.
        replacements[self.LOADING_TABLE_PLACEHOLDER] = self.LOADING_TABLE_MARKER
        replacements[self.UNLOADING_TABLE_PLACEHOLDER] = self.UNLOADING_TABLE_MARKER

        # В лог пишем только размеры блоков: содержимое содержит адреса
        # погрузки/выгрузки, которым в логах не место (Шаг 4 задания).
        logger.debug(
            f"loading_block сформирован: погрузок {len(loadings)}, "
            f"символов {len(replacements['loading_block'])}"
        )
        logger.debug(
            f"unloading_block сформирован: выгрузок {len(unloadings)}, "
            f"символов {len(replacements['unloading_block'])}"
        )

        if loadings:
            replacements["loading_address"] = loadings[0].get("address", "")
            replacements["loading_date"] = self._format_date_dot(loadings[0].get("date", ""))
            replacements["loading_time_window"] = loadings[0].get("time_window", "")
        else:
            replacements["loading_address"] = ""
            replacements["loading_date"] = ""
            replacements["loading_time_window"] = ""

        replacements["unloading_address_1"] = unloadings[0].get("address", "") if len(unloadings) > 0 else ""
        replacements["unloading_address_2"] = unloadings[1].get("address", "") if len(unloadings) > 1 else ""
        if unloadings:
            replacements["unloading_date"] = self._format_date_dot(unloadings[-1].get("date", ""))
            replacements["unloading_time_window"] = unloadings[-1].get("time_window", "")
        else:
            replacements["unloading_date"] = ""
            replacements["unloading_time_window"] = ""

        replacements["route"] = contract.get("route", "")

        # ── Плановые даты ──
        loading_plan_date = contract.get("loading_plan_date", "")
        unloading_plan_date = contract.get("unloading_plan_date", "")
        loading_plan_time_from = contract.get("loading_plan_time_from", "")
        loading_plan_time_to = contract.get("loading_plan_time_to", "")

        replacements["loading_plan_date"] = self._format_date_full(loading_plan_date)
        replacements["unloading_plan_date"] = self._format_date_full(unloading_plan_date)
        replacements["loading_plan_time_from"] = loading_plan_time_from
        replacements["loading_plan_time_to"] = loading_plan_time_to
        replacements["loading_plan_time_full"] = (
            f"с {loading_plan_time_from} по {loading_plan_time_to}"
            if loading_plan_time_from and loading_plan_time_to else ""
        )

        # ── Стоимость и НДС ──
        price_without_vat = float(contract.get("price_without_vat", 0) or 0)

        if is_ip_without_vat or vat_rate_num <= 0:
            nds_amount = 0.0
            total_amount = price_without_vat
            replacements["sum_wo_nds"] = f"{price_without_vat:.2f}"
            replacements["sum_wo_nds_words"] = amount_to_words(price_without_vat)
            replacements["nds_text"] = "НДС не облагается"
            replacements["sum_nds"] = ""
            replacements["sum_nds_words"] = ""
            replacements["sum_total"] = f"{total_amount:.2f}"
            replacements["sum_total_words"] = amount_to_words(total_amount)
        else:
            nds_amount = round(price_without_vat * vat_rate_num / 100, 2)
            total_amount = round(price_without_vat + nds_amount, 2)

            replacements["sum_wo_nds"] = f"{price_without_vat:.2f}"
            replacements["sum_wo_nds_words"] = amount_to_words(price_without_vat)

            replacements["sum_nds"] = f"{nds_amount:.2f}"
            replacements["sum_nds_words"] = amount_to_words(nds_amount)

            replacements["nds_text"] = (
                f"НДС по ставке, действующей на дату оказания услуг "
                f"(в настоящее время {vat_rate_num:.0f}%) — {nds_amount:.2f} руб."
            )

            replacements["sum_total"] = f"{total_amount:.2f}"
            replacements["sum_total_words"] = amount_to_words(total_amount)

        # Синонимы ключей сумм: «wo_nds» — историческое имя перевозки,
        # «wo_vat» — имя сумм в остальных типах (аренда, Логистикс).
        # Бланки перевозки читают ключи с «nds», но если в бланк когда-нибудь
        # добавят сумму прописью из общего ряда — значение уже готово,
        # и оно ПРОПИСЬЮ, а не цифрами.
        replacements["sum_wo_vat"] = replacements["sum_wo_nds"]
        replacements["sum_wo_vat_words"] = replacements["sum_wo_nds_words"]
        replacements["sum_vat"] = replacements["sum_nds"]
        replacements["sum_vat_words"] = replacements["sum_nds_words"]
        replacements["price_without_vat_words"] = replacements["sum_wo_nds_words"]
        replacements["price_with_vat_words"] = replacements["sum_total_words"]

        replacements["nds_status_text"] = nds_status_text
        replacements["vat_rate"] = f"{vat_rate_num:.0f}%"

        # ── Предоплата: разбивка оплаты на предоплату и окончательный расчёт ──
        # База процента — итог договора (`total_amount`, сумма с НДС):
        # именно её видит заказчик в п. 4.1 бланка. Сумма предоплаты введена
        # оператором в рублях, поэтому при изменении стоимости
        # пересчитывается только процент (см.
        # BaseContractGenerator._split_payment).
        prepayment = float(contract.get("prepayment_amount", 0) or 0)
        split = self._split_payment(total_amount, prepayment)
        replacements.update(split)
        self._log_split_payment(split)

        logger.info(
            f"Итоговые суммы: без НДС={price_without_vat:.2f}, "
            f"НДС={nds_amount:.2f} ({vat_rate_num:.0f}%), итого={total_amount:.2f}"
        )

        payment_days = contract.get("payment_days", 10)
        replacements["payment_days"] = str(payment_days)
        # Срок оплаты прописью — в родительном падеже и для ЛЮБОГО числа:
        # «10 (десяти)», «25 (двадцати пяти)», «45 (сорока пяти)».
        # Прежний _days_to_words знает только 1…20 и 30, а на 45 печатал
        # цифры — в поле «..._words» цифр быть не должно.
        replacements["payment_days_words"] = self._days_to_words_genitive(payment_days)
        replacements["penalty_rate"] = "5000"

        # Данные из справочников и импорта приходят с переносами строк и
        # задвоенными пробелами: «Общество с ограниченной ответственностью\n
        # "ТЕХНОЛОГИСТИКА"». В договоре такой перенос превращался в <w:br/>,
        # и Word при выравнивании по ширине растягивал строку перед разрывом:
        # «Общество      с      ограниченной      ответственностью».
        self._flatten_replacements(replacements)

        logger.debug(f"Сформировано {len(replacements)} плейсхолдеров")
        return replacements

    # ─────────────────────────────────────────────────────────
    # Предоплата
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _log_split_payment(split: Dict[str, Any]) -> None:
        """
        Пишет в лог факт разбивки оплаты — БЕЗ сумм, только проценты.

        Суммы договора в логе не место (AGENTS.md § 3.3), а проценты ничего
        не раскрывают: по ним видно только пропорцию.
        """
        if split.get("has_prepayment"):
            logger.info(
                f"Предоплата: {split['prepayment_percent']}%, "
                f"остаток: {split['balance_percent']}%"
            )
        else:
            logger.info("Предоплата не предусмотрена")

    # ─────────────────────────────────────────────────────────
    # Год договора
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _contract_year(date_iso: Any) -> str:
        """
        Год договора из его даты; если даты нет — текущий год.

        Дата приходит из UI в ISO («2026-09-23»), из справочников и
        распознавания — в любом виде, поэтому строку разбирает только
        строгий ISO-разбор, а остальное («23.09.2026», мусор, None) даёт
        текущий год. Год из даты всегда четыре цифры, как в бланке.

        Раньше здесь стояло `datetime.now().year`: договор, подготовленный
        в декабре, а напечатанный в январе, уезжал в новый год.
        """
        if date_iso:
            try:
                return str(datetime.strptime(str(date_iso)[:10], "%Y-%m-%d").year)
            except (ValueError, TypeError):
                pass
        return str(datetime.now().year)

    # ─────────────────────────────────────────────────────────
    # Краткая метка точки для таблицы ТС
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _get_point_label(index: Any, points: List[Dict[str, Any]], title: str) -> str:
        try:
            idx = int(index or 0)
        except (ValueError, TypeError):
            idx = 0

        if idx == 0:
            return "—"
        if 1 <= idx <= len(points):
            return f"{title} {idx}"
        return "—"


#: Историческое имя класса — страховка от ссылок вида
#: «ContractGenerator.CONST» в стороннем коде и при отладке.
ContractGenerator = PerevozkaGenerator
