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
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.contract_data import ContractData
from core.contracts.base_generator import (
    BaseContractGenerator,
    ConvertNewlinesStep,
    PostprocessStep,
)
from core.contracts.contract_types import ContractType
from core.contracts.perevozka.postprocess import (
    RemoveEmptyVehicleRowsStep,
    RouteTablesStep,
)
from core.contracts.perevozka.validator import PerevozkaValidator
from core.num_to_words import amount_to_words

logger = logging.getLogger("core.contract_generator")


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
        """
        steps: List[PostprocessStep] = [ConvertNewlinesStep(self)]
        if data is not None:
            steps.append(RouteTablesStep(self))
        steps.append(RemoveEmptyVehicleRowsStep(self))
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
                "address": contract.get("loading_address", ""),
                "date": contract.get("loading_date", ""),
                "time_window": contract.get("loading_time_window", ""),
            }]

        if not unloadings:
            legacy = []
            if contract.get("unloading_address_1"):
                legacy.append({
                    "address": contract.get("unloading_address_1", ""),
                    "date": contract.get("unloading_date", ""),
                    "time_window": contract.get("unloading_time_window", ""),
                })
            if contract.get("unloading_address_2"):
                legacy.append({
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
                f"{title_prefix} {shown}: {address}".strip(),
            ))
            elements.append(self._make_vehicle_table(doc, assigned))
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
            elements.append(self._make_vehicle_table(doc, unassigned))
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
            f"без привязки {len(unassigned)}, "
            f"скрыто из-за пустых точек {len(hidden_ids)}"
        )
        return shown

    def _make_vehicle_table(self, doc, vehicles: List[Dict[str, Any]]):
        """
        Таблица «№ | Марка/Модель | VIN-номер» по списку машин.

        Порядок строк — порядок машин в исходном массиве, без сортировки.
        Оформление повторяет таблицу машин из шаблона (блок 3.1): и шрифт,
        и заливку ячейки, и границы. Копировать один шрифт нельзя — в
        шаблоне шапка оформлена белым текстом на тёмной заливке, и без
        заливки белый текст становится невидимым (белое на белом).
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

        for number, vehicle in enumerate(vehicles, 1):
            row = table.add_row()
            values = (
                str(number),
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
        for i, p in enumerate(points, 1):
            parts = [f"{title} {i}: {p.get('address', '').strip()}"]
            date_str = self._format_date_full(p.get("date", ""))
            if date_str:
                parts.append(date_str)
            time_str = (p.get("time_window", "") or "").strip()
            if time_str:
                parts.append(time_str)
            lines.append(" — ".join(parts))

            vins_here = point_vehicles.get(i, [])
            if vins_here:
                vins_str = ", ".join(vins_here)
                lines.append(f"  Машины ({len(vins_here)} шт.): {vins_str}")
            else:
                lines.append("  Машины: —")

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────
    # КАРТА ЗАМЕН
    # ─────────────────────────────────────────────────────────

    def _build_replacements_map(self, data: Dict[str, Any]) -> Dict[str, str]:
        contract_data = ContractData.coerce(data)
        replacements: Dict[str, str] = {}

        # ── Данные договора ──
        contract = contract_data.contract
        replacements["contract_number"] = contract.get("number", "")

        date_iso = contract.get("date", "")
        replacements["contract_date"] = self._day_of_month(date_iso) if date_iso else ""
        replacements["contract_month"] = self._month_name(date_iso)
        replacements["contract_year"] = str(datetime.now().year)

        # Город заключения: явное поле → город первой погрузки →
        # юр. адрес перевозчика → «Москва» (историческое поведение, баг 2.9).
        city = contract_data.resolved_city()
        replacements["city"] = city.replace("г. ", "").replace("г.", "").strip()

        # ── Перевозчик ──
        carrier = contract_data.carrier

        carrier_type_ui = contract.get("carrier_type") or carrier.get("carrier_type", "ООО (с НДС)")

        vat_rate_num = contract.get("vat_rate_num")
        if vat_rate_num is None:
            vat_rate_str = contract.get("vat_rate", "22%")
            try:
                vat_rate_num = float(str(vat_rate_str).replace("%", "").strip())
            except (ValueError, TypeError):
                vat_rate_num = 22.0

        is_ooo = "ООО" in carrier_type_ui
        is_ip_with_vat = "ИП с НДС" in carrier_type_ui
        is_ip_without_vat = "ИП без НДС" in carrier_type_ui

        if is_ooo:
            legal_form = "Общество с ограниченной ответственностью"
            basis = "Устава"
            pronoun = "именуемое"
            director_position_full = carrier.get("director_position", "директора") or "директора"
            director_position_short = "Директор"
            kpp = carrier.get("kpp", "")
            ogrn_label = "ОГРН"
            nds_status_text = ("Перевозчик подтверждает, что применяет общую систему "
                               "налогообложения и является плательщиком НДС.")
        else:
            legal_form = "Индивидуальный предприниматель"
            basis = "свидетельства о государственной регистрации"
            pronoun = "именуемый"
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

        replacements["carrier_legal_form"] = legal_form
        replacements["carrier_pronoun"] = pronoun
        replacements["carrier_basis"] = basis
        replacements["carrier_full_name"] = carrier.get("full_name", "")
        replacements["carrier_name"] = carrier.get("short_name", "") or carrier.get("full_name", "")
        replacements["carrier_inn"] = carrier.get("inn", "")
        replacements["carrier_kpp"] = kpp
        replacements["carrier_kpp_line"] = f"КПП {kpp}" if kpp else ""
        replacements["carrier_ogrn_label"] = ogrn_label
        replacements["carrier_ogrn"] = carrier.get("ogrn", "")
        replacements["carrier_address"] = carrier.get("legal_address", "")
        replacements["carrier_actual_address"] = carrier.get("actual_address", "")
        replacements["carrier_account"] = carrier.get("bank_account", "")
        replacements["carrier_bik"] = carrier.get("bik", "")
        replacements["carrier_bank"] = carrier.get("bank_name", "")
        replacements["carrier_corr_account"] = carrier.get("correspondent_account", "")
        replacements["carrier_director"] = carrier.get("director_name", "")
        replacements["carrier_director_position"] = director_position_full
        replacements["carrier_director_position_short"] = director_position_short
        replacements["carrier_phone"] = carrier.get("phone", "")
        replacements["carrier_email"] = carrier.get("email", "")

        # ── Заказчик ──
        # ВАЖНО (Шаг 2): здесь больше нет выдуманных значений по умолчанию
        # («ООО ТЕХНОЛОГИСТИКА», ИНН 9709112631, «Ахмедова Т.А.»).
        # Если заказчик не заполнен — подставляется пустая строка, а
        # core.validator сообщает об этом пользователю до генерации.
        customer = contract_data.customer
        customer_director = customer.get("director_name", "")
        replacements["client_full_name"] = customer.get("full_name", "")
        replacements["client_name"] = customer.get("short_name", "") or customer.get("full_name", "")
        replacements["client_inn"] = customer.get("inn", "")
        replacements["client_kpp"] = customer.get("kpp", "")
        replacements["client_ogrn"] = customer.get("ogrn", "")
        replacements["client_address"] = customer.get("legal_address", "")
        replacements["client_account"] = customer.get("bank_account", "")
        replacements["client_bik"] = customer.get("bik", "")
        replacements["client_bank"] = customer.get("bank_name", "")
        replacements["client_corr_account"] = customer.get("correspondent_account", "")
        replacements["client_director"] = customer_director
        replacements["client_director_position"] = customer.get("director_position", "")
        replacements["client_director_position_short"] = customer.get("director_position", "")
        replacements["client_director_short"] = self._short_fio(customer_director)
        replacements["client_basis"] = "Устава"
        replacements["client_phone"] = customer.get("phone", "")
        replacements["client_email"] = customer.get("email", "")

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

        replacements["nds_status_text"] = nds_status_text
        replacements["vat_rate"] = f"{vat_rate_num:.0f}%"

        logger.info(
            f"Итоговые суммы: без НДС={price_without_vat:.2f}, "
            f"НДС={nds_amount:.2f} ({vat_rate_num:.0f}%), итого={total_amount:.2f}"
        )

        payment_days = contract.get("payment_days", 10)
        replacements["payment_days"] = str(payment_days)
        replacements["payment_days_words"] = self._days_to_words(payment_days)
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
