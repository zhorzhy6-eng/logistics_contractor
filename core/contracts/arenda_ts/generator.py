#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор договора аренды ТС с экипажем (ЭТАП 3.1.D.A.3).

Тип «Разовая аренда» — договор аренды транспортного средства с экипажем
(на один рейс). Арендатор в этом типе — НАША сторона, и она бывает трёх
видов, а от вида зависит бланк:

    templates/shablon_arenda_ts_ooo.docx             — Арендатор ООО;
    templates/shablon_arenda_ts_ip_with_vat.docx     — Арендатор ИП, с НДС;
    templates/shablon_arenda_ts_ip_without_vat.docx  — Арендатор ИП, без НДС.

Вид Арендатора читается из contract["carrier_type"] («ООО» / «ИП с НДС» /
«ИП без НДС») и определяет не только файл бланка, но и содержимое:

  * КПП — плейсхолдер lessee_kpp есть только в ООО-бланке: в ИП-вариантах
    ключ в карту замен НЕ кладётся;
  * метка госрегистрации: «ОГРН» у ООО, «ОГРНИП» у ИП;
  * основание: «Устава» у ООО, «свидетельства о государственной
    регистрации» у ИП;
  * арендная плата: у ООО и ИП с НДС — три суммы (без НДС / НДС по ставке /
    итого), у ИП без НДС — одна сумма, а плейсхолдеров НДС в бланке нет.

Арендодатель — вторая сторона, в бланках всегда ООО: метка «ОГРН» и
основание «Устава» подставляются константами. Если в данных Арендодатель
оказался ИП — это расхождение данных (о нём предупредит валидатор типа,
ЭТАП 3.1.D.A.5), генератор подставляет реквизиты как есть и пишет в лог
предупреждение без наименований.

Чем этот тип отличается от «Логистикс Рус»
(core/contracts/logistiks_rus/generator.py — образец структуры класса):

  * сторон две и обе с полными реквизитами (блоки lessee / lessor);
  * таблица машин п. 3.1 — пять колонок: «№ / Марка, модель / VIN-номер /
    Точка погрузки / Точка выгрузки» (в заявке было три);
  * точки маршрута — не пары абзацев, а по одной нумерованной строке
    «3.2.N. Точка погрузки № N — …» и «3.3.N. Точка выгрузки № N — …»;
    незаполненные строки (и заголовок раздела, если точек не осталось)
    удаляет постобработка (RemoveEmptyLoadingUnloadingBlocksStep);
  * Приложение № 1 (Акт приема-передачи и возврата ТС) — часть того же
    файла; его плейсхолдеры (номер и дата договора, тягач, прицеп, экипаж,
    краткие наименования сторон) заполняются теми же значениями, что и
    основной договор: отдельной логики для Акта нет.

Стык «распознавание → генератор» (промпт core/prompts/arenda_ts.py):

  * промпт возвращает блоки lessee / lessor, а также route,
    lease_start_date и lease_end_date В КОРНЕ ответа, тогда как генератору
    они нужны внутри contract. ContractData.coerce корневые ключи не хранит
    (core/contract_data.py на этом шаге не трогаем), поэтому generate()
    переносит их в contract сам (_hoist_contract_fields), а
    _build_replacements_map() умеет читать их и из корня — прямой вызов
    карты замен работает на «сырых» данных распознавания;
  * тип Арендатора в ответе распознавания отдельным полем не приходит:
    он выводится из lessee["entity_type"] («ООО» / «ИП») и ставки НДС
    («0%» → ИП без НДС). Явный contract["carrier_type"] (его заполняет
    интерфейс) всегда важнее этой догадки;
  * время подачи ТС у промпта — поля time_from / time_to точки, а
    ContractData хранит точку как {address, date, time_window} и эти поля
    отбрасывает (core.contract_data._as_point_list). Поэтому точки маршрута
    читаются из исходных данных, а если там только time_window — интервал
    разбирается из него;
  * суммы у промпта: sum_wo_vat / sum_vat / sum_total и vat_rate. У ИП без
    НДС единственная сумма документа лежит в sum_total, а sum_wo_vat равен
    нулю — поэтому в варианте без НДС база берётся из sum_total;
  * ЭДО промпт извлекает: lessee_edo / lessor_edo — из поля edo блока стороны.

Логгер остаётся «core.contract_generator»: тесты и UI ловят сообщения
генерации именно по этому имени (см. base_generator.py). В логи попадают
только количества и суммы — без ФИО, паспортов, адресов, VIN, госномеров и
наименований сторон.
"""

import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.contract_data import ContractData
from core.contracts.arenda_ts.postprocess import (
    RemoveEmptyLoadingUnloadingBlocksStep,
    RemoveEmptyVehicleRowsStep,
)
from core.contracts.arenda_ts.validator import ArendaTsValidator
from core.contracts.base_generator import (
    BaseContractGenerator,
    ConvertNewlinesStep,
    PostprocessStep,
)
from core.contracts.contract_types import ContractType
from core.num_to_words import amount_to_words

logger = logging.getLogger("core.contract_generator")

TITLE = "Договор аренды ТС с экипажем"

# ── Таблица машин п. 3.1 ──
#: Заголовки колонок: по ним таблица ищется в готовом документе (а не по
#: индексу — в бланке шесть таблиц). Вариант «Марка/Модель» принимается
#: наравне с «Марка, модель», как в Формике.
CAR_TABLE_NUMBER_HEADER = "№"
CAR_TABLE_BRAND_HEADERS = ("Марка, модель", "Марка/Модель")
CAR_TABLE_VIN_HEADER = "VIN-номер"
CAR_TABLE_LOADING_HEADER = "Точка погрузки"
CAR_TABLE_UNLOADING_HEADER = "Точка выгрузки"

#: Размер таблицы машин в бланке — больше машин в документ не влезет.
MAX_CARS = 12

#: Точек погрузки и выгрузки в бланке — по 10 каждого вида.
MAX_POINTS = 10

#: Строки точек маршрута в готовом документе. Ищутся по номеру пункта
#: (3.2.N. / 3.3.N.) и метке; адрес берётся между тире и фразой «Плановая
#: дата…». Пустой адрес — признак незаполненной точки: docxtpl подставляет
#: пустое значение, но саму строку не удаляет.
#:
#: У точки выгрузки метка закрывается двоеточием: в п. 3.3.2 стоит плановая
#: дата завершения рейса (шаг FIX-1-T), и после «:» в строке либо пустота,
#: либо «<дата> г.». Адрес — всё до двоеточия, поэтому хвост с датой
#: разбирать не нужно: у незаполненной точки он тоже пустой.
LOADING_POINT_RE = re.compile(
    r"^3\.2\.(?P<number>\d+)\.\s*Точка\s+погрузки\s*№\s*\d+\s*[—–-]\s*"
    r"(?P<address>.*?)\.\s*Плановая\s+дата",
    re.DOTALL,
)
UNLOADING_POINT_RE = re.compile(
    r"^3\.3\.(?P<number>\d+)\.\s*Точка\s+выгрузки\s*№\s*\d+\s*[—–-]\s*"
    r"(?P<address>.*?)\.\s*Плановая\s+дата\s+завершения\s*:",
    re.DOTALL,
)

#: Заголовки разделов точек: «3.2. Согласованные точки погрузки:».
LOADING_SECTION_RE = re.compile(r"^3\.2\.\s*Согласованные\s+точки\s+погрузки")
UNLOADING_SECTION_RE = re.compile(r"^3\.3\.\s*Согласованные\s+точки\s+выгрузки")

#: Типы ТС из справочника машин, которые машинами п. 3.1 не являются:
#: тягач и прицеп/полуприцеп описаны отдельными строками бланка (п. 2.1).
NON_CARGO_VEHICLE_TYPES = ("Тягач", "Полуприцеп", "Прицеп")

#: Основание полномочий: у ООО — устав, у ИП — свидетельство о регистрации.
LESSEE_OOO_BASIS = "Устава"
LESSEE_IP_BASIS = "свидетельства о государственной регистрации"

#: Арендодатель в бланках всегда ООО (см. докстринг модуля).
LESSOR_BASIS = "Устава"
OGRN_LABEL = "ОГРН"
OGRNIP_LABEL = "ОГРНИП"

#: Перечень документов, передаваемых вместе с ТС. В бланке эта строка стоит
#: плейсхолдером {{transfer_documents}} (шаг FIX-3): у неё одно место правки
#: вместо трёх бланков, а у формы — своё поле, если пользователь допишет
#: «иные:». Значение по умолчанию повторяет формулировку образца.
TRANSFER_DOCUMENTS = "СТС на тягач и прицеп/полуприцеп; ОСАГО; иные:"


class ArendaTsGenerator(BaseContractGenerator):
    """
    Договор аренды ТС с экипажем: три бланка по типу Арендатора.

    Наследование — напрямую от BaseContractGenerator: общая механика
    (docxtpl, постобработка, форматирование дат и ФИО) берётся из базы, а
    специфика договора-заявки на перевозку (мультимаршрут, таблицы погрузок,
    НДС перевозчика) не тянется.
    """

    CONTRACT_TYPE = ContractType.ARENDA_TS.value

    #: Варианты бланка: ключ — вид Арендатора (значение contract.carrier_type).
    TEMPLATE_NAMES: Mapping[str, str] = {
        "ООО": "shablon_arenda_ts_ooo.docx",
        "ИП с НДС": "shablon_arenda_ts_ip_with_vat.docx",
        "ИП без НДС": "shablon_arenda_ts_ip_without_vat.docx",
    }

    #: Префикс имени файла: «Договор_аренды_ТС_<номер>_<ГГГГММДД>.docx».
    FILE_PREFIX = "Договор_аренды_ТС"

    #: Валидатор типа (хук validate в базе). Правила — ЭТАП 3.1.D.A.5.
    VALIDATOR_CLASS = ArendaTsValidator

    #: Ставка НДС по умолчанию, если в данных её нет (НДС в РФ).
    DEFAULT_VAT_RATE = 22.0

    #: Поля распознавания, которые живут в корне ответа, а нужны внутри
    #: contract (см. _hoist_contract_fields). loadings / unloadings — потому
    #: что ContractData хранит точку только как {address, date, time_window}
    #: и поля time_from / time_to распознавания теряет.
    ROOT_FIELDS_IN_CONTRACT = (
        "lessee",
        "lessor",
        "route",
        "lease_start_date",
        "lease_end_date",
        "loadings",
        "unloadings",
    )

    # ─────────────────────────────────────────────────────────
    # ВЫБОР ШАБЛОНА
    # ─────────────────────────────────────────────────────────

    def _get_template_path(self, carrier_type: str) -> str:
        """
        Путь бланка по виду Арендатора.

        Правило — как у перевозки и у сборщика шаблонов
        (tools/make_arenda_ts_template.py::resolve_variant): сначала «ИП без
        НДС», затем «ИП с НДС», всё остальное (в том числе пустое значение) —
        ООО-бланк.
        """
        if "ИП без НДС" in carrier_type:
            return self.templates["ИП без НДС"]
        elif "ИП с НДС" in carrier_type:
            return self.templates["ИП с НДС"]
        else:
            return self.templates["ООО"]

    @classmethod
    def _carrier_type_of(cls, data: ContractData) -> str:
        """
        Вид Арендатора из условий договора (хук базы get_template_path).

        Явный contract["carrier_type"] (его заполняет интерфейс), а если его
        нет — вывод из блока lessee: см. _resolve_carrier_type.
        """
        contract = data.contract
        lessee = contract.get("lessee")
        return cls._resolve_carrier_type(
            contract, dict(lessee) if isinstance(lessee, Mapping) else {}
        )

    # ─────────────────────────────────────────────────────────
    # СТЫК С РАСПОЗНАВАНИЕМ: КОРНЕВЫЕ ПОЛЯ → CONTRACT
    # ─────────────────────────────────────────────────────────

    def generate(self, data: Dict[str, Any], output_dir: Optional[str] = None) -> str:
        """
        Генерация договора: корневые блоки распознавания переносятся в contract.

        Промпт отдаёт lessee / lessor / route / lease_start_date /
        lease_end_date / loadings / unloadings в корне ответа, а генератору
        (и ContractData) они нужны внутри contract. Уже заполненные значения
        contract не перетираются: данные интерфейса важнее. Остальные поля
        (driver, tractor, trailer, vehicles) ContractData понимает и в корне.
        """
        return super().generate(self._hoist_contract_fields(data), output_dir)

    @classmethod
    def _hoist_contract_fields(cls, data: Any) -> Any:
        """
        Копия данных с корневыми полями распознавания внутри contract.

        Не-Mapping (в том числе готовый ContractData) возвращается как есть.
        """
        if not isinstance(data, Mapping):
            return data

        payload = dict(data)
        contract = payload.get("contract")
        contract = dict(contract) if isinstance(contract, Mapping) else {}

        for field in cls.ROOT_FIELDS_IN_CONTRACT:
            if cls._filled(contract.get(field)):
                continue
            if cls._filled(payload.get(field)):
                contract[field] = payload[field]

        payload["contract"] = contract
        return payload

    @staticmethod
    def _filled(value: Any) -> bool:
        """True, если значение считается заполненным (не None и не пустое)."""
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, dict)):
            return bool(value)
        return True

    # ─────────────────────────────────────────────────────────
    # КОНВЕЙЕР ПОСТОБРАБОТКИ
    # ─────────────────────────────────────────────────────────

    def postprocess_steps(self, data) -> List[PostprocessStep]:
        """
        Шаги постобработки: переносы строк → пустые строки таблицы машин →
        незаполненные точки погрузки и выгрузки.

        Таблиц по точкам маршрута в бланке нет (каждая точка — абзац),
        поэтому шаг RouteTablesStep (перевозка) не подключается.
        """
        return [
            ConvertNewlinesStep(self),
            RemoveEmptyVehicleRowsStep(self),
            RemoveEmptyLoadingUnloadingBlocksStep(self),
        ]

    def _insert_route_tables(self, doc, contract_data: ContractData) -> None:
        """
        Не используется: точки маршрута выводятся плейсхолдерами, а не
        таблицами по погрузкам/выгрузкам.

        Метод объявлен, потому что этого имени требует контракт базы
        (BaseContractGenerator._insert_route_tables).
        """
        return None

    # ─────────────────────────────────────────────────────────
    # УДАЛЕНИЕ ПУСТЫХ СТРОК ТАБЛИЦЫ МАШИН
    # ─────────────────────────────────────────────────────────

    def _remove_empty_vehicle_rows(self, doc) -> None:
        """
        Убирает из таблицы машин строки без марки, VIN и точек маршрута.

        В бланке 12 строк данных; в договоре машин может быть меньше, и тогда
        docxtpl оставляет строки с пустыми значениями. Строка удаляется,
        только если ПУСТЫ все четыре колонки данных — заполненная хотя бы
        одной строку сохраняем (данные не теряем).

        Колонка «№» не проверяется: её номер в бланке — статичный текст, и
        пустой она не бывает. Строка заголовка (индекс 0) не рассматривается.
        """
        for table in doc.tables:
            if not self._is_car_table(table):
                continue

            removed = 0
            for index in range(len(table.rows) - 1, 0, -1):
                row = table.rows[index]
                cells = row.cells
                if len(cells) < 5:
                    continue
                values = [cells[column].text.strip() for column in (1, 2, 3, 4)]
                if any(values):
                    continue
                self._delete_row(table, row)
                removed += 1

            if removed:
                logger.info(
                    f"{TITLE}: удалено пустых строк таблицы машин: {removed}"
                )

    def _is_car_table(self, table) -> bool:
        """True, если таблица — перечень машин п. 3.1 (по заголовкам колонок)."""
        if not table.rows:
            return False

        headers = [
            cell.text.replace("–", "-").replace("—", "-").strip()
            for cell in table.rows[0].cells
        ]
        has_number = CAR_TABLE_NUMBER_HEADER in headers
        has_brand = any(header in CAR_TABLE_BRAND_HEADERS for header in headers)
        has_vin = CAR_TABLE_VIN_HEADER in headers
        has_points = (
            CAR_TABLE_LOADING_HEADER in headers
            and CAR_TABLE_UNLOADING_HEADER in headers
        )
        return has_number and has_brand and has_vin and has_points

    # ─────────────────────────────────────────────────────────
    # УДАЛЕНИЕ НЕЗАПОЛНЕННЫХ ТОЧЕК МАРШРУТА
    # ─────────────────────────────────────────────────────────

    def _remove_empty_point_blocks(self, doc, data=None) -> None:
        """
        Убирает незаполненные точки погрузки и выгрузки.

        В бланке по 10 строк каждого вида, а точек в договоре может быть
        меньше: строки сверх фактического числа остаются в документе с пустым
        адресом («3.2.4. Точка погрузки № 4 — . Плановая дата…»). Строка
        считается незаполненной, если пуст её адрес: строка с адресом
        сохраняется, даже если дата или окно времени не заполнены.

        Если у раздела не осталось ни одной точки, удаляется и его заголовок
        («3.2. Согласованные точки погрузки:»): пустой раздел в договоре не
        нужен.

        data не используется: признак незаполненной точки виден по самому
        документу (аргумент оставлен ради контракта PostprocessStep).
        """
        removed = self._remove_empty_points_of_kind(
            doc, LOADING_POINT_RE, LOADING_SECTION_RE, "погрузки"
        )
        removed += self._remove_empty_points_of_kind(
            doc, UNLOADING_POINT_RE, UNLOADING_SECTION_RE, "выгрузки"
        )

        if removed:
            logger.info(f"{TITLE}: удалено пустых точек маршрута: {removed}")

    @classmethod
    def _remove_empty_points_of_kind(
        cls, doc, point_re, section_re, label: str
    ) -> int:
        """
        Удаляет строки одного вида точек с пустым адресом.

        Возвращает число удалённых строк. Абзацы просматриваются в порядке
        документа: заголовок раздела запоминается, чтобы убрать его, если
        точек не осталось.
        """
        removed = 0
        kept = 0
        headers = []

        for paragraph in list(doc.paragraphs):
            if cls._paragraph_is_deleted(paragraph):
                continue

            text = cls._paragraph_text(paragraph)

            if section_re.match(text):
                headers.append(paragraph)
                continue

            match = point_re.match(text)
            if match is None:
                continue

            if match.group("address").strip():
                kept += 1
                continue

            cls._delete_paragraph(paragraph)
            removed += 1

        if removed and not kept:
            for header in headers:
                if not cls._paragraph_is_deleted(header):
                    cls._delete_paragraph(header)

        if removed:
            logger.info(f"{TITLE}: удалено пустых точек {label}: {removed}")

        return removed

    @staticmethod
    def _paragraph_text(paragraph) -> str:
        """
        Текст абзаца одной строкой без крайних пробелов.

        Значения из справочников приходят с переносами строк, а Word после
        рендера отдаёт их разрывами внутри текста абзаца: для поиска метки и
        проверки на пустоту такой текст приводится к одной строке.
        """
        return re.sub(r"\s+", " ", paragraph.text).strip()

    @staticmethod
    def _paragraph_is_deleted(paragraph) -> bool:
        """True, если абзац уже удалён из документа (нет родителя)."""
        return paragraph._p.getparent() is None

    @staticmethod
    def _delete_paragraph(paragraph) -> None:
        """Удаляет абзац из документа целиком (вместе с его свойствами)."""
        element = paragraph._p
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)

    # ─────────────────────────────────────────────────────────
    # КАРТА ЗАМЕН
    # ─────────────────────────────────────────────────────────

    def _build_replacements_map(self, data: Any) -> Dict[str, str]:
        """
        Значения всех плейсхолдеров бланка.

        Порядок ключей соответствует бланку: шапка и срок аренды → Арендатор
        (в том числе подписант: должность в родительном падеже и причастие по
        роду, шаг FIX-3) → Арендодатель → тягач, прицеп и машины → маршрут →
        экипаж → арендная плата → Акт приёма-передачи. Набор ключей — ровно
        плейсхолдеры своего варианта: у ИП нет lessee_kpp, у ИП без НДС нет
        сумм НДС. Совпадение набора с бланком проверяет тест
        test_replacements_map_covers_all_template_placeholders.
        """
        contract_data = ContractData.coerce(data)
        contract = contract_data.contract
        replacements: Dict[str, str] = {}

        lessee = self._party_block(data, contract_data, "lessee")
        lessor = self._party_block(data, contract_data, "lessor")
        carrier_type = self._resolve_carrier_type(contract, lessee)
        is_ooo, is_ip_with_vat, is_ip_without_vat = self._variant_flags(carrier_type)

        loadings = self._resolve_points(data, contract_data, "loadings")
        unloadings = self._resolve_points(data, contract_data, "unloadings")

        self._fill_header(
            replacements,
            contract,
            self._root_value(data, contract_data, "lease_start_date"),
            self._root_value(data, contract_data, "lease_end_date"),
            self._root_value(data, contract_data, "planned_completion_date"),
        )
        self._fill_lessee(replacements, lessee, is_ooo)
        self._fill_lessor(replacements, lessor)
        self._fill_vehicles(replacements, contract_data)
        self._fill_route(
            replacements,
            self._root_value(data, contract_data, "route"),
            loadings,
            unloadings,
        )
        self._fill_driver(replacements, contract_data)
        self._fill_cost(replacements, contract, is_ip_without_vat)
        self._fill_act(replacements, contract)

        # Переносы строк и задвоенные пробелы из справочников в бланке не
        # нужны (по ширине строки дают «рваное» выравнивание).
        self._flatten_replacements(replacements)

        logger.debug(
            f"{TITLE}: сформировано {len(replacements)} плейсхолдеров, "
            f"вариант={carrier_type or 'ООО'}, ООО={is_ooo}, "
            f"ИП_с_НДС={is_ip_with_vat}, ИП_без_НДС={is_ip_without_vat}"
        )
        return replacements

    def _fill_header(
        self,
        replacements: Dict[str, str],
        contract: Dict[str, Any],
        lease_start_date: Any,
        lease_end_date: Any,
        planned_completion_date: Any,
    ) -> None:
        """
        Шапка и сроки: номер и дата договора, плановый период аренды (п. 2.5)
        и планируемая дата завершения рейса (п. 3.3.2).

        Даты не выводятся одна из другой: lease_start_date / lease_end_date —
        период аренды, planned_completion_date — отдельное поле интерфейса
        (шаг FIX-1; в образце ТЛ-574 это разные даты). Пустое значение
        печатается пустой строкой — дату не выдумываем.
        """
        replacements["contract_number"] = self._single_line(
            contract.get("number") or ""
        )
        replacements["contract_date"] = self._format_date_full(
            contract.get("date") or ""
        )
        replacements["lease_start_date"] = self._format_date_full(lease_start_date or "")
        replacements["lease_end_date"] = self._format_date_full(lease_end_date or "")
        replacements["planned_completion_date"] = self._format_date_full(
            planned_completion_date or ""
        )

    def _fill_lessee(
        self,
        replacements: Dict[str, str],
        lessee: Dict[str, Any],
        is_ooo: bool,
    ) -> None:
        """
        Разделы 1.1 и 9: реквизиты Арендатора (наша сторона).

        Вид Арендатора определяет КПП (только ООО), метку госрегистрации
        (ОГРН / ОГРНИП) и основание полномочий (устав / свидетельство).
        Незаполненные поля дают пустые строки: значения не выдумываем, о
        пропусках сообщит валидатор типа.
        """
        director_name = self._single_line(lessee.get("director_name") or "")

        replacements["lessee_full_name"] = self._single_line(
            lessee.get("full_name") or ""
        )
        replacements["lessee_short_name"] = self._single_line(
            lessee.get("short_name") or lessee.get("full_name") or ""
        )
        replacements["lessee_inn"] = self._single_line(lessee.get("inn") or "")

        if is_ooo:
            # КПП у индивидуального предпринимателя не бывает, и в ИП-бланке
            # плейсхолдера нет: ключ туда не кладём.
            replacements["lessee_kpp"] = self._single_line(lessee.get("kpp") or "")

        replacements["lessee_ogrn_label"] = OGRN_LABEL if is_ooo else OGRNIP_LABEL
        replacements["lessee_ogrn"] = self._single_line(lessee.get("ogrn") or "")
        replacements["lessee_address"] = self._single_line(
            lessee.get("legal_address") or ""
        )
        replacements["lessee_account"] = self._single_line(
            lessee.get("bank_account") or ""
        )
        replacements["lessee_bank"] = self._single_line(lessee.get("bank_name") or "")
        replacements["lessee_bik"] = self._single_line(lessee.get("bik") or "")
        replacements["lessee_corr_account"] = self._single_line(
            lessee.get("corr_account") or ""
        )
        replacements["lessee_email"] = self._single_line(lessee.get("email") or "")
        # ЭДО промпт извлекает (edo блока стороны): если в документе его нет,
        # значение пустое и печатается пустая строка — ничего не выдумываем.
        replacements["lessee_edo"] = self._single_line(lessee.get("edo") or "")
        replacements["lessee_director_position"] = self._single_line(
            lessee.get("director_position") or ""
        )
        replacements["lessee_director_name"] = director_name
        replacements["lessee_basis"] = LESSEE_OOO_BASIS if is_ooo else LESSEE_IP_BASIS
        replacements["lessee_director_short"] = self._short_fio(director_name)
        # Подписант в п. 1.1: должность в родительном падеже и причастие по
        # роду (шаг FIX-3) — в бланке стоит «в лице <должность> <ФИО>,
        # <причастие> на основании <основание>».
        replacements["lessee_director_position_rod"] = self._genitive_position(
            lessee.get("director_position") or ""
        )
        replacements["lessee_director_acting_rod"] = self._acting_rod(director_name)
        # Раздел 9 печатает фактический адрес стороны (плейсхолдер появился
        # на шаге FIX-3): пустое значение даёт пустое место в документе.
        replacements["lessee_actual_address"] = self._single_line(
            lessee.get("actual_address") or ""
        )

        # В лог — только «есть/нет»: наименование, ИНН и адрес стороны в логах
        # не нужны (в ИП наименование содержит ФИО).
        logger.info(
            f"{TITLE} [%s]: реквизиты Арендатора — %s, подписант — %s",
            "ООО" if is_ooo else "ИП",
            "есть" if (replacements["lessee_inn"] and replacements["lessee_ogrn"])
            else "нет",
            "есть" if director_name else "нет",
        )

    def _fill_lessor(self, replacements: Dict[str, str], lessor: Dict[str, Any]) -> None:
        """
        Разделы 1.2 и 9: реквизиты Арендодателя (вторая сторона).

        Бланки рассчитаны на Арендодателя-ООО, поэтому метка «ОГРН» и
        основание «Устава» — константы. Если в данных сторона оказалась ИП,
        это расхождение данных: реквизиты подставляются как есть, а в лог
        уходит предупреждение без наименования (проверит валидатор, ЭТАП
        A.5).
        """
        if "ИП" in str(lessor.get("entity_type") or "").upper():
            logger.warning(
                f"{TITLE}: Арендодатель в данных — ИП, а бланк рассчитан на "
                f"ООО: реквизиты подставлены как есть"
            )

        director_name = self._single_line(lessor.get("director_name") or "")

        replacements["lessor_full_name"] = self._single_line(
            lessor.get("full_name") or ""
        )
        replacements["lessor_short_name"] = self._single_line(
            lessor.get("short_name") or lessor.get("full_name") or ""
        )
        replacements["lessor_inn"] = self._single_line(lessor.get("inn") or "")
        replacements["lessor_ogrn_label"] = OGRN_LABEL
        replacements["lessor_ogrn"] = self._single_line(lessor.get("ogrn") or "")
        replacements["lessor_address"] = self._single_line(
            lessor.get("legal_address") or ""
        )
        replacements["lessor_account"] = self._single_line(
            lessor.get("bank_account") or ""
        )
        replacements["lessor_bank"] = self._single_line(lessor.get("bank_name") or "")
        replacements["lessor_bik"] = self._single_line(lessor.get("bik") or "")
        replacements["lessor_corr_account"] = self._single_line(
            lessor.get("corr_account") or ""
        )
        replacements["lessor_email"] = self._single_line(lessor.get("email") or "")
        replacements["lessor_edo"] = self._single_line(lessor.get("edo") or "")
        replacements["lessor_director_position"] = self._single_line(
            lessor.get("director_position") or ""
        )
        replacements["lessor_director_name"] = director_name
        replacements["lessor_basis"] = LESSOR_BASIS
        replacements["lessor_director_short"] = self._short_fio(director_name)
        # П. 1.2 и раздел 9 — те же поля FIX-3, что и у Арендатора.
        replacements["lessor_director_position_rod"] = self._genitive_position(
            lessor.get("director_position") or ""
        )
        replacements["lessor_director_acting_rod"] = self._acting_rod(director_name)
        replacements["lessor_actual_address"] = self._single_line(
            lessor.get("actual_address") or ""
        )

    def _fill_vehicles(
        self, replacements: Dict[str, str], contract_data: ContractData
    ) -> None:
        """
        Объект аренды (п. 2.1) и таблица машин (п. 3.1).

        Тягач и прицеп — отдельные строки бланка; машины таблицы 3.1 идут
        строками car_1..car_12 (пусто, если машин меньше). Тягач и
        полуприцеп из справочника машин в таблицу не попадают — они уже
        описаны объектом аренды. Машин больше 12 в бланк не влезет: лишние
        отбрасываются с предупреждением (VIN и госномеров в лог не пишем).
        """
        tractor = contract_data.tractor
        trailer = contract_data.trailer

        replacements["tractor_brand"] = self._single_line(
            tractor.get("brand_model") or ""
        )
        replacements["tractor_plate"] = self._single_line(
            tractor.get("plate_number") or ""
        )
        replacements["tractor_type"] = self._single_line(
            tractor.get("vehicle_type") or tractor.get("ts_type") or ""
        )
        replacements["trailer_brand"] = self._single_line(
            trailer.get("brand_model") or ""
        )
        replacements["trailer_plate"] = self._single_line(
            trailer.get("plate_number") or ""
        )

        vehicles = self._cargo_vehicles(contract_data.vehicles)

        if len(vehicles) > MAX_CARS:
            logger.warning(
                f"{TITLE}: машин {len(vehicles)}, в бланк помещается {MAX_CARS} — "
                f"лишние не выводятся"
            )

        replacements["cargo_count"] = str(len(vehicles))

        for number in range(1, MAX_CARS + 1):
            vehicle = vehicles[number - 1] if number <= len(vehicles) else None
            replacements[f"car_{number}_brand"] = (
                self._single_line(vehicle.get("brand_model") or "") if vehicle else ""
            )
            replacements[f"car_{number}_vin"] = (
                self._single_line(vehicle.get("vin") or "") if vehicle else ""
            )
            replacements[f"car_{number}_loading_point"] = (
                self._single_line(vehicle.get("loading_point") or "")
                if vehicle else ""
            )
            replacements[f"car_{number}_unloading_point"] = (
                self._single_line(vehicle.get("unloading_point") or "")
                if vehicle else ""
            )

        logger.info(f"{TITLE}: машин в таблице 3.1 — {len(vehicles)}")

    def _fill_route(
        self,
        replacements: Dict[str, str],
        route: Any,
        loadings: List[Dict[str, Any]],
        unloadings: List[Dict[str, Any]],
    ) -> None:
        """Разделы 3.2–3.4: точки погрузки, точки выгрузки и маршрут."""
        replacements["route"] = self._single_line(route or "")

        self._fill_points(replacements, loadings, "loading", with_time=True)
        self._fill_points(replacements, unloadings, "unloading", with_time=False)

    def _fill_points(
        self,
        replacements: Dict[str, str],
        points: List[Dict[str, Any]],
        prefix: str,
        with_time: bool,
    ) -> None:
        """
        Точки маршрута: <prefix>_N_* (N = 1..10).

        У точки погрузки есть окно подачи ТС (time_from / time_to), у точки
        выгрузки — только плановая дата. Точек больше, чем строк в бланке, —
        лишние не выводятся, в лог уходит предупреждение без адресов.
        """
        if len(points) > MAX_POINTS:
            logger.warning(
                f"{TITLE}: точек маршрута {len(points)}, в бланк помещается "
                f"{MAX_POINTS} — лишние не выводятся"
            )

        for number in range(1, MAX_POINTS + 1):
            point = points[number - 1] if number <= len(points) else {}
            replacements[f"{prefix}_{number}_address"] = self._single_line(
                point.get("address") or ""
            )
            replacements[f"{prefix}_{number}_date"] = self._format_date_full(
                point.get("date") or ""
            )
            if not with_time:
                continue
            replacements[f"{prefix}_{number}_time_from"] = self._single_line(
                point.get("time_from") or ""
            )
            replacements[f"{prefix}_{number}_time_to"] = self._single_line(
                point.get("time_to") or ""
            )

        logger.info(
            f"{TITLE}: точек «{prefix}» выведено — {min(len(points), MAX_POINTS)}"
        )

    def _fill_driver(
        self, replacements: Dict[str, str], contract_data: ContractData
    ) -> None:
        """
        Раздел 3.5 «Член экипажа Арендодателя (водитель)».

        Промпт отдаёт паспорт и удостоверение одной строкой (passport,
        license), а справочник водителя в интерфейсе хранит серию и номер
        отдельно — принимаются оба вида. Адрес регистрации: поле address
        распознавания, затем registration_address справочника.
        """
        driver = contract_data.driver

        passport = self._single_line(driver.get("passport") or "")
        if not passport:
            series = self._single_line(driver.get("passport_series") or "")
            number = self._single_line(driver.get("passport_number") or "")
            passport = f"{series} {number}".strip()

        license_number = self._single_line(driver.get("license") or "")
        if not license_number:
            series = self._single_line(driver.get("license_series") or "")
            number = self._single_line(driver.get("license_number") or "")
            license_number = f"{series} {number}".strip()

        replacements["driver_full_name"] = self._single_line(
            driver.get("full_name") or ""
        )
        replacements["driver_birth_date"] = self._format_date_full(
            driver.get("birth_date") or ""
        )
        replacements["driver_passport"] = passport
        replacements["driver_passport_issuer"] = self._single_line(
            driver.get("passport_issuer") or ""
        )
        replacements["driver_passport_issue_date"] = self._format_date_full(
            driver.get("passport_issue_date") or ""
        )
        replacements["driver_license"] = license_number
        replacements["driver_license_issue_date"] = self._format_date_full(
            driver.get("license_issue_date") or ""
        )
        replacements["driver_address"] = self._single_line(
            driver.get("address") or driver.get("registration_address") or ""
        )
        replacements["driver_phone"] = self._single_line(driver.get("phone") or "")

    def _fill_cost(
        self,
        replacements: Dict[str, str],
        contract: Dict[str, Any],
        is_ip_without_vat: bool,
    ) -> None:
        """
        Раздел 4 «Арендная плата, НДС и порядок оплаты»: суммы п. 4.1 и срок
        оплаты п. 4.5.

        Вариант ООО и ИП с НДС: три суммы — без НДС, НДС по ставке и итого,
        каждая цифрами и прописью. Вариант ИП без НДС: одна сумма, а
        sum_wo_vat / sum_vat / vat_rate в карту замен НЕ кладутся — в
        ИП-бланке без НДС таких плейсхолдеров нет (оговорка «НДС не
        облагается» напечатана в самом бланке).

        Оба варианта пишут в INFO ровно одну строку, и в неё попадают
        ФАКТИЧЕСКИ подставленные значения — они читаются из карты замен уже
        после присваивания, а не пересчитываются повторно. Там, где
        плейсхолдера в бланке нет (ИП без НДС: sum_wo_vat / sum_vat /
        vat_rate), печатается «—».
        """
        self._fill_payment_days(replacements, contract)

        vat_rate_num = self._resolve_vat_rate_num(contract)
        base = self._base_price(contract, is_ip_without_vat)

        if is_ip_without_vat:
            total = round(base, 2)
            replacements["sum_total"] = self._format_money(total)
            replacements["sum_total_words"] = amount_to_words(total)
        else:
            vat_amount = round(base * vat_rate_num / 100, 2)
            total = round(base + vat_amount, 2)

            replacements["sum_wo_vat"] = self._format_money(base)
            replacements["sum_wo_vat_words"] = amount_to_words(base)
            replacements["sum_vat"] = self._format_money(vat_amount)
            replacements["sum_vat_words"] = amount_to_words(vat_amount)
            replacements["sum_total"] = self._format_money(total)
            replacements["sum_total_words"] = amount_to_words(total)
            replacements["vat_rate"] = f"{vat_rate_num:.0f}%"

        logger.info(
            f"{TITLE} [%s]: в бланк подставлено — "
            "без НДС=%s, НДС=%s, итого=%s, ставка=%s",
            "ИП без НДС" if is_ip_without_vat else "с НДС",
            replacements.get("sum_wo_vat", "—"),
            replacements.get("sum_vat", "—"),
            replacements["sum_total"],
            replacements.get("vat_rate", "—"),
        )

        # Диагностика стыка «распознавание → генератор»: суммы в contract есть,
        # а читать их генератору нечем — значит, в бланк уйдёт 0,00. У ООО и
        # ИП с НДС база — сумма БЕЗ НДС (sum_wo_vat): если распознавание
        # нашло только сумму с НДС (sum_total), арендная плата в бланке
        # обнулится. Проверяем по фактически посчитанной базе, а не только по
        # наличию ключей: сообщение обещает именно 0,00 в бланке.
        recognized_sums = (
            contract.get("sum_wo_vat")
            or contract.get("sum_total")
            or contract.get("sum_vat")
        )
        if base <= 0 and self._to_float(recognized_sums, default=0.0) > 0:
            logger.warning(
                f"{TITLE} [%s]: в contract есть распознанные суммы (sum_*), "
                "но суммы без НДС среди них нет — в бланк уйдёт 0,00. "
                "Маппинг суммы — TODO 3.1.D.B.1",
                "ИП без НДС" if is_ip_without_vat else "с НДС",
            )

    def _fill_act(
        self, replacements: Dict[str, str], contract: Dict[str, Any]
    ) -> None:
        """
        Приложение № 1 (Акт приёма-передачи и возврата ТС): поля FIX-3.

        До этого шага значения Акта заполнялись только вручную в Word —
        бланк печатал пустые ячейки. Теперь каждое поле идёт плейсхолдером
        из contract, а перечень переданных документов — константой
        TRANSFER_DOCUMENTS, если своего значения в данных нет.

        Поля Акта в схеме распознавания отсутствуют (промпт их не извлекает:
        Акт — форма для заполнения при передаче ТС), поэтому источник —
        интерфейс. Пустое значение печатается пустым местом, ничего не
        выдумываем.
        """
        replacements["transfer_place"] = self._single_line(
            contract.get("transfer_place") or ""
        )
        replacements["transfer_datetime"] = self._single_line(
            contract.get("transfer_datetime") or ""
        )
        replacements["transfer_mileage"] = self._single_line(
            contract.get("transfer_mileage") or ""
        )
        replacements["transfer_condition"] = self._single_line(
            contract.get("transfer_condition") or ""
        )
        replacements["transfer_documents"] = self._single_line(
            contract.get("transfer_documents") or TRANSFER_DOCUMENTS
        )
        replacements["return_place"] = self._single_line(
            contract.get("return_place") or ""
        )
        replacements["return_datetime"] = self._single_line(
            contract.get("return_datetime") or ""
        )
        replacements["return_mileage"] = self._single_line(
            contract.get("return_mileage") or ""
        )
        replacements["return_condition"] = self._single_line(
            contract.get("return_condition") or ""
        )
        replacements["return_notes"] = self._single_line(
            contract.get("return_notes") or ""
        )

        # В лог — только «есть/нет» по группам: сами места, пробег и
        # замечания это данные документа.
        filled = sum(
            1 for key in (
                "transfer_place", "transfer_datetime", "transfer_mileage",
                "transfer_condition", "return_place", "return_datetime",
                "return_mileage", "return_condition", "return_notes",
            )
            if replacements[key]
        )
        logger.info(f"{TITLE}: полей Акта заполнено — {filled} из 9")

    def _fill_payment_days(
        self, replacements: Dict[str, str], contract: Dict[str, Any]
    ) -> None:
        """
        Пункт 4.5: срок оплаты в банковских днях — «{{payment_days}}
        ({{payment_days_words}}) банковских дней».

        Логика как у перевозки (core/contracts/perevozka/generator.py,
        _build_replacements_map): срок берётся из contract и печатается
        цифрами плюс прописью в родительном падеже — «45 (сорока пяти)».
        Срок не выдумывается: нет ключа (распознавание его не нашло, поле
        вкладки пустое) — оба плейсхолдера пустые, о незаполненном сроке
        сообщит валидатор. Строку пишет ТОЛЬКО этот INFO: он же несёт и
        срок, и признак «срок не задан», и в лог попадают одни числа.
        """
        raw_days = contract.get("payment_days")
        days = self._payment_days(raw_days)

        if days is None:
            replacements["payment_days"] = ""
            replacements["payment_days_words"] = ""
            logger.info(
                f"{TITLE}: срок оплаты (п. 4.5) — не задан: в бланке пустая строка"
            )
            return

        replacements["payment_days"] = str(days)
        replacements["payment_days_words"] = self._days_to_words_genitive(days)
        logger.info(f"{TITLE}: срок оплаты (п. 4.5) — {days} банковских дней")

    @staticmethod
    def _payment_days(value: Any) -> Optional[int]:
        """
        Срок оплаты целым числом банковских дней: 45, «45», «45 дн.» → 45.

        Ноль и отрицательное значение читаются как «срок не задан» (None) —
        ровно так же, как в сборщике окна
        (ui/windows/arenda_ts/data.py::_payment_days_value): в п. 4.5 бланка
        печатается число банковских дней, и ноль там смысла не имеет. Если
        значение не разобралось, тоже None: мусор в бланк не попадает.
        """
        if value is None or isinstance(value, bool):
            return None

        try:
            if isinstance(value, str):
                number = float(value.replace(" ", "").replace(",", ".").strip())
            else:
                number = float(value)
        except (ValueError, TypeError):
            return None

        days = int(number)
        return days if days > 0 else None

    # ─────────────────────────────────────────────────────────
    # Стороны, точки маршрута и значения из исходных данных
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _party_block(
        cls, data: Any, contract_data: ContractData, field: str
    ) -> Dict[str, Any]:
        """
        Блок стороны (lessee / lessor).

        Сначала contract: по этому пути блок доживает до генератора и в
        вызове generate() (там корневые поля переносятся в contract —
        _hoist_contract_fields). Если в contract блока нет, он берётся из
        корня исходных данных: так работает прямой вызов карты замен на
        «сыром» ответе распознавания.
        """
        nested = contract_data.contract.get(field)
        if isinstance(nested, Mapping) and nested:
            return dict(nested)

        if isinstance(data, Mapping):
            raw = data.get(field)
            if isinstance(raw, Mapping) and raw:
                return dict(raw)

        return {}

    @classmethod
    def _root_value(cls, data: Any, contract_data: ContractData, key: str) -> Any:
        """
        Значение из contract, а при его отсутствии — из корня исходных данных.

        Промпт кладёт route и lease_start_date / lease_end_date в корень
        ответа; ContractData их не хранит, поэтому при прямом вызове карты
        замен значение ищется ещё и в исходном словаре.
        """
        value = contract_data.contract.get(key)
        if cls._filled(value):
            return value

        if isinstance(data, Mapping):
            raw = data.get(key)
            if cls._filled(raw):
                return raw

        return "" if value is None else value

    @classmethod
    def _resolve_carrier_type(
        cls, contract: Dict[str, Any], lessee: Dict[str, Any]
    ) -> str:
        """
        Вид Арендатора: «ООО» / «ИП с НДС» / «ИП без НДС».

        Явное поле contract["carrier_type"] (его заполняет интерфейс) важнее
        всего. Если его нет — вид выводится из распознавания: entity_type
        блока lessee («ООО» / «ИП»), а для ИП ещё и ставка НДС: «0%» означает
        вариант без НДС (в документе напечатано «НДС не облагается», см.
        core/prompts/arenda_ts.py). Не угаданный тип даёт ООО-бланк —
        поведение по умолчанию у всех остальных типов договоров.
        """
        explicit = str(contract.get("carrier_type") or "").strip()
        if explicit:
            return explicit

        entity = str(lessee.get("entity_type") or "").strip().upper()
        if "ИП" in entity or "ПРЕДПРИНИМАТЕЛЬ" in entity:
            return "ИП без НДС" if cls._vat_rate_is_zero(contract) else "ИП с НДС"
        return "ООО"

    @classmethod
    def _vat_rate_is_zero(cls, contract: Dict[str, Any]) -> bool:
        """True, если ставка НДС в данных нулевая («0%», 0, 0.0)."""
        number = contract.get("vat_rate_num")
        if number is None:
            raw = str(contract.get("vat_rate") or "").replace("%", "").strip()
            number = cls._to_float(raw, default=None) if raw else None

        if number is None:
            return False
        return float(number) <= 0

    @staticmethod
    def _variant_flags(carrier_type: str) -> Tuple[bool, bool, bool]:
        """
        Признаки варианта бланка: (ООО, ИП с НДС, ИП без НДС).

        Правило совпадает с _get_template_path: «ИП без НДС» → вариант без
        НДС, «ИП с НДС» → ИП с НДС, всё остальное — ООО.
        """
        is_ip_without_vat = "ИП без НДС" in carrier_type
        is_ip_with_vat = "ИП с НДС" in carrier_type
        is_ooo = not (is_ip_without_vat or is_ip_with_vat)
        return is_ooo, is_ip_with_vat, is_ip_without_vat

    @classmethod
    def _resolve_points(
        cls, data: Any, contract_data: ContractData, field: str
    ) -> List[Dict[str, Any]]:
        """
        Точки маршрута: адрес и дата из ContractData, время — из исходных данных.

        ContractData хранит точку как {address, date, time_window}
        (core.contract_data._as_point_list) и поля time_from / time_to
        распознавания отбрасывает, поэтому время подачи ТС читается из
        исходного словаря (data или вложенного contract) — тем же приёмом, что
        названия точек в logistiks_rus. Если времени в данных нет, а
        time_window заполнено («09:00-18:00»), интервал разбирается из него.
        """
        points = [dict(point) for point in getattr(contract_data, field)]
        raw = cls._raw_points(data, contract_data, field)

        if len(raw) > len(points):
            points.extend(dict(point) for point in raw[len(points):])

        for index, point in enumerate(points):
            source = raw[index] if index < len(raw) else {}
            for key in ("address", "date", "time_from", "time_to"):
                if not str(point.get(key) or "").strip() and key in source:
                    point[key] = str(source.get(key) or "")

            window_from, window_to = cls._parse_time_window(
                str(point.get("time_window") or "")
            )
            if not str(point.get("time_from") or "").strip():
                point["time_from"] = window_from
            if not str(point.get("time_to") or "").strip():
                point["time_to"] = window_to

        return points

    @classmethod
    def _raw_points(
        cls, data: Any, contract_data: ContractData, field: str
    ) -> List[Dict[str, Any]]:
        """
        Точки маршрута из исходных данных (до приведения через ContractData).

        Порядок источников повторяет порядок самого ContractData.coerce:
        сначала точки верхнего уровня, затем вложенные в contract; если
        данных-словаря уже нет (в генератор пришёл ContractData), остаётся
        только contract — в нём исходный список лежит без изменений.
        """
        raw = None
        if isinstance(data, Mapping):
            candidate = data.get(field)
            if isinstance(candidate, (list, tuple)) and candidate:
                raw = candidate
            else:
                nested = data.get("contract")
                if isinstance(nested, Mapping):
                    candidate = nested.get(field)
                    if isinstance(candidate, (list, tuple)) and candidate:
                        raw = candidate

        if raw is None:
            candidate = contract_data.contract.get(field)
            if isinstance(candidate, (list, tuple)) and candidate:
                raw = candidate

        if raw is None:
            return []

        return [dict(item) for item in raw if isinstance(item, Mapping)]

    @staticmethod
    def _parse_time_window(window: str) -> Tuple[str, str]:
        """«09:00-18:00» / «с 09:00 до 15:00» → («09:00», «15:00»)."""
        times = re.findall(r"\d{1,2}[:.]\d{2}", window)
        if len(times) >= 2:
            return times[0].replace(".", ":"), times[1].replace(".", ":")
        return "", ""

    # ─────────────────────────────────────────────────────────
    # Вспомогательные вычисления
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _cargo_vehicles(cls, vehicles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Машины таблицы 3.1: без тягача/прицепа и без полностью пустых строк."""
        result = []
        for vehicle in vehicles:
            if vehicle.get("vehicle_type") in NON_CARGO_VEHICLE_TYPES:
                continue
            if not (
                str(vehicle.get("vin") or "").strip()
                or str(vehicle.get("brand_model") or "").strip()
            ):
                continue
            result.append(vehicle)
        return result

    @classmethod
    def _resolve_vat_rate_num(cls, contract: Dict[str, Any]) -> float:
        """
        Ставка НДС числом для расчёта сумм.

        Источники: vat_rate_num → разбор строки vat_rate («22%») →
        DEFAULT_VAT_RATE. Явный ноль уважается: 0 — это «без НДС», а не
        отсутствие ставки.
        """
        number = contract.get("vat_rate_num")

        if number is None:
            raw = contract.get("vat_rate")
            if raw:
                number = cls._to_float(str(raw).replace("%", "").strip(), default=None)

        if number is None:
            number = cls.DEFAULT_VAT_RATE

        return float(number)

    @classmethod
    def _base_price(cls, contract: Dict[str, Any], is_ip_without_vat: bool) -> float:
        """
        Сумма, от которой считается арендная плата.

        Вариант с НДС: сначала сумма без НДС распознавания (sum_wo_vat), затем
        price_without_vat из вкладки «Стоимость». Вариант «ИП без НДС»: НДС не
        облагается, поэтому первым берётся единственная сумма документа —
        sum_total (промпт кладёт её именно туда, а sum_wo_vat оставляет
        нулём), затем sum_wo_vat и price_without_vat. Отсутствующие данные
        дают 0.00: суммы не выдумываются.
        """
        keys = (
            ("sum_total", "sum_wo_vat", "price_without_vat")
            if is_ip_without_vat
            else ("sum_wo_vat", "price_without_vat")
        )

        for key in keys:
            value = cls._to_float(contract.get(key), default=0.0)
            if value > 0:
                return round(value, 2)

        return 0.0

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> Optional[float]:
        """
        Число из значения любого вида: «221 099,18», «269741.00», 180300.

        Разделители тысяч и запятая как десятичный разделитель — обычный
        формат документов; None при пустом значении превращается в default.
        """
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)

        text = str(value).strip().replace("\u00a0", "").replace(" ", "")
        if not text:
            return default
        text = text.replace(",", ".")
        if text.count(".") > 1:
            # «1.234.567» — точки как разделители тысяч.
            head, _, tail = text.rpartition(".")
            text = head.replace(".", "") + "." + tail

        try:
            return float(text)
        except ValueError:
            return default

    @staticmethod
    def _gender_from_name(full_name: str) -> str:
        """
        Пол по отчеству: «-овна / -евна / -ична / -инична» → female,
        иначе male. Если отчества нет — возвращает male (по умолчанию).

        Нужен для согласования причастия в п. 1.1 / 1.2 бланка: там стоит
        «{{*_director_acting_rod}} на основании …», и для директора-женщины
        это «действующей», а не «действующего» (шаг FIX-3). Отчество —
        третий элемент ФИО; если его в данных нет, пол не угадывается,
        берётся форма по умолчанию (мужская).
        """
        parts = str(full_name or "").split()
        if len(parts) >= 3:
            patronymic = parts[2].lower()
            if patronymic.endswith(("овна", "евна", "ична", "инична")):
                return "female"
        return "male"

    @classmethod
    def _acting_rod(cls, full_name: str) -> str:
        """«действующего» (муж.) / «действующей» (жен.)."""
        return (
            "действующей"
            if cls._gender_from_name(full_name) == "female"
            else "действующего"
        )

    @classmethod
    def _genitive_position(cls, position: str) -> str:
        """
        Родительный падеж должности: «Генеральный директор» →
        «Генерального директора».

        В бланке подписант назван в родительном падеже («в лице Генерального
        директора Иванова Ивана Ивановича»), а распознавание и вкладка
        отдают должность в именительном («Генеральный директор»). Падеж
        подставляет генератор — в одном месте, а не в трёх бланках.

        Правила:
          - «-ый/-ий» и «-ой» → «-ого»: «Генеральный» → «Генерального»,
            «Главный» → «Главного»;
          - «-ая/-яя» → «-ой/-ей»: «Финансовая» → «Финансовой»;
          - последнее слово-существительное на согласную получает «-а»:
            «директор» → «директора», «предприниматель» → «предпринимателя»;
          - «ИП» и другие сокращения (только заглавные) не меняются;
          - слово, уже стоящее в родительном падеже («директора»), не
            трогается: иначе получилось бы «директораа».

        Пустое значение → пустая строка.
        """
        text = str(position or "").strip()
        if not text:
            return ""

        words = text.split()
        converted = [
            cls._genitive_word(word, is_last=(index == len(words) - 1))
            for index, word in enumerate(words)
        ]
        return " ".join(converted)

    @staticmethod
    def _genitive_word(word: str, is_last: bool = False) -> str:
        """
        Одно слово должности в родительном падеже.

        Прилагательное и существительное склоняются по разным правилам,
        поэтому решение принимается по окончанию, а не по части речи:
        разбирать должность морфологически здесь нечем (pymorphy в проекте
        нет), и словарь должностей — такая же догадка, только длиннее.
        """
        lower = word.lower()

        # Прилагательное: «-ый / -ий / -ой» → «-ого».
        for ending in ("ый", "ий", "ой"):
            if lower.endswith(ending) and len(word) > len(ending):
                return word[: -len(ending)] + "ого"

        # Прилагательное женского рода: «-ая» → «-ой», «-яя» → «-ей».
        if lower.endswith("ая") and len(word) > 2:
            return word[:-2] + "ой"
        if lower.endswith("яя") and len(word) > 2:
            return word[:-2] + "ей"

        if not is_last:
            # Не последнее слово — существительное здесь не склоняем:
            # падеж несёт последнее слово должности.
            return word

        # Сокращения («ИП», «ООО») не склоняются.
        if word.isupper():
            return word

        # Мужской род на согласную: «директор» → «директора».
        if lower.endswith("ь"):
            return word[:-1] + "я"
        if lower[-1].isalpha() and lower[-1] not in "аеёиоуыэюяй":
            return word + "а"

        return word

    @staticmethod
    def _format_money(amount: float) -> str:
        """
        Сумма в формате образца: «221 099,18» (неразрывный пробел между
        разрядами, запятая перед копейками).
        """
        return f"{amount:,.2f}".replace(",", "\u00a0").replace(".", ",")

