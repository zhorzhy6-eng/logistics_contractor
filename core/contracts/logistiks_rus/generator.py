#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор заявки «Логистикс Рус» (ЭТАП 3.1.C.A.3).

«Логистикс Рус» — Приложение № 1 (Заявка № …) к Генеральному договору
транспортной экспедиции на организацию перевозки транспортных средств.
Заказчик в этом типе фиксирован распознаванием, экспедитор — шаблоном,
а от типа перевозчика зависит вариант бланка:

    templates/shablon_logistiks_rus_ooo.docx — ООО «ТЕХНОЛОГИСТИКА»;
    templates/shablon_logistiks_rus_ip.docx  — ИП Хейгетян Е.В.

Шаблон выбирается по carrier_type («ИП» в строке типа → ИП-бланк), и вместе
с бланком меняется раздел 5 «Стоимость»:

  * ООО — три суммы: без НДС, НДС по ставке и итого (в образце
    221 099,18 + 22% = 48 641,82 → 269 741,00);
  * ИП — одна сумма «Без НДС»: плейсхолдеров sum_wo_vat / sum_vat в
    ИП-бланке нет, поэтому в карту замен они не кладутся.

Чем ещё этот тип отличается от Формики (core/contracts/formika/generator.py):

  * раздел 1 «Погрузка» и раздел 2 «Выгрузка» — до 10 блоков
    «Грузоотправитель/Адрес погрузки» и «Грузополучатель №N/Адрес выгрузки»:
    незаполненные блоки удаляет постобработка
    (RemoveEmptyShipperConsigneeBlocksStep);
  * таблица автомобилей на 12 машин с шапкой «№ / Марка, модель /
    VIN-номер» (запятая, не слэш) — лишние строки удаляет
    RemoveEmptyVehicleRowsStep;
  * реквизитов сторон, банковских полей и паспорта водителя в бланке нет:
    только наименование заказчика, автовоз, водитель и стоимость.

О названиях грузоотправителей и грузополучателей. ContractData хранит точки
маршрута как {address, date, time_window} — поле name при приведении данных
отбрасывается (core.contract_data._as_point_list). Поэтому название блока
генератор берёт из точек в том виде, в каком они пришли:

  * contract["loadings"] / contract["unloadings"] — этот путь работает и
    через публичный generate(): вложенный в contract список сохраняется в
    ContractData.contract без изменений;
  * исходный словарь (data["loadings"] / data["unloadings"]) — работает при
    прямом вызове build_replacements()/_build_replacements_map().

Если точки переданы только на верхнем уровне ({"loadings": [...]}), то по
пути generate() до бланка доживают лишь адреса: name теряется в coerce.
Чтобы имя доживало и там, нужно расширить _as_point_list в
core/contract_data.py — на этом шаге файл не трогаем.

Логгер остаётся «core.contract_generator»: тесты и UI ловят сообщения
генерации именно по этому имени (см. base_generator.py). В логи попадают
только имена полей, количества и суммы — без ФИО, адресов, VIN и названий
организаций.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.contract_data import ContractData
from core.contracts.base_generator import (
    BaseContractGenerator,
    ConvertNewlinesStep,
    PostprocessStep,
)
from core.contracts.contract_types import ContractType
from core.contracts.logistiks_rus.postprocess import (
    RemoveEmptyShipperConsigneeBlocksStep,
    RemoveEmptyVehicleRowsStep,
)
from core.contracts.logistiks_rus.validator import LogistiksRusValidator
from core.num_to_words import amount_to_words

logger = logging.getLogger("core.contract_generator")

TITLE = "Логистикс Рус"

#: Заголовки таблицы автомобилей — ровно как в бланке («Марка, модель»
#: с запятой; слэш-вариант здесь не принимается, в отличие от Формики).
CARGO_TABLE_NUMBER_HEADER = "№"
CARGO_TABLE_BRAND_HEADERS = ("Марка, модель",)
CARGO_TABLE_VIN_HEADER = "VIN-номер"

#: Метки абзацев-блоков погрузки и выгрузки. По ним постобработка находит
#: блоки с незаполненными значениями (см. _remove_empty_point_blocks).
SHIPPER_NAME_RE = re.compile(r"^Грузоотправитель\s*:\s*(?P<value>.*)$")
SHIPPER_ADDRESS_RE = re.compile(r"^Адрес\s+погрузки\s*:\s*(?P<value>.*)$")
CONSIGNEE_NAME_RE = re.compile(r"^Грузополучатель\s*№\s*\d+\s*:\s*(?P<value>.*)$")
CONSIGNEE_ADDRESS_RE = re.compile(r"^Адрес\s+выгрузки\s*:\s*(?P<value>.*)$")

#: Типы ТС из справочника машин, которые грузом не являются: тягач и
#: прицеп описаны отдельными строками бланка (раздел 4).
NON_CARGO_VEHICLE_TYPES = ("Тягач", "Полуприцеп", "Прицеп")

#: Размер таблицы автомобилей в бланке — больше машин в документ не влезет.
MAX_CARS = 12

#: Блоков грузоотправителей и грузополучателей в бланке — по 10 каждого.
MAX_POINTS = 10

#: Отписка бланка на случай, когда особых условий в заявке нет (так в
#: образцах: «При отсутствии записей особые условия рейса не установлены.»).
DEFAULT_SPECIAL_CONDITIONS = (
    "При отсутствии записей особые условия рейса не установлены."
)


class LogistiksRusGenerator(BaseContractGenerator):
    """
    Заявка (Приложение № 1) к генеральному договору экспедиции.

    Наследование — напрямую от BaseContractGenerator: общая механика
    (docxtpl, постобработка, форматирование) берётся из базы, а специфика
    договора-заявки на перевозку (мультимаршрут, таблицы погрузок, НДС по
    ставке перевозчика) не тянется.
    """

    CONTRACT_TYPE = ContractType.LOGISTIKS_RUS.value

    #: Варианты бланка: экспедитор ООО или ИП (ключ — часть carrier_type).
    TEMPLATE_NAMES: Mapping[str, str] = {
        "ООО": "shablon_logistiks_rus_ooo.docx",
        "ИП": "shablon_logistiks_rus_ip.docx",
    }

    #: Префикс имени файла: «Заявка_Логистикс_Рус_<номер>_<ГГГГММДД>.docx».
    FILE_PREFIX = "Заявка_Логистикс_Рус"

    #: Валидатор типа (хук validate в базе). Правила — ЭТАП 3.1.C.A.5.
    VALIDATOR_CLASS = LogistiksRusValidator

    #: Ставка НДС по умолчанию, если в данных её нет (НДС в РФ).
    DEFAULT_VAT_RATE = 22.0

    # ─────────────────────────────────────────────────────────
    # ВЫБОР ШАБЛОНА
    # ─────────────────────────────────────────────────────────

    def _get_template_path(self, carrier_type: str) -> str:
        """
        Путь бланка по типу экспедитора.

        «ИП» в строке типа («ИП», «ИП без НДС», «ИП Хейгетян Е.В.») —
        ИП-вариант, всё остальное (в том числе пустое значение) — ООО:
        так же выбирает вариант сборщик шаблонов
        (tools/make_logistiks_rus_template.py::resolve_variant).
        """
        if "ИП" in str(carrier_type):
            return self.templates["ИП"]
        return self.templates["ООО"]

    # ─────────────────────────────────────────────────────────
    # КОНВЕЙЕР ПОСТОБРАБОТКИ
    # ─────────────────────────────────────────────────────────

    def postprocess_steps(self, data) -> List[PostprocessStep]:
        """
        Шаги постобработки: переносы строк → пустые строки груза → пустые
        блоки грузоотправителей и грузополучателей.

        Таблиц по погрузкам/выгрузкам в этом бланке нет (маршрут выводится
        плейсхолдерами), поэтому шаг RouteTablesStep не подключается.
        """
        return [
            ConvertNewlinesStep(self),
            RemoveEmptyVehicleRowsStep(self),
            RemoveEmptyShipperConsigneeBlocksStep(self),
        ]

    def _insert_route_tables(self, doc, contract_data: ContractData) -> None:
        """
        Не используется: в заявке нет таблиц по точкам маршрута — блоки
        погрузки и выгрузки выводятся плейсхолдерами.

        Метод объявлен, потому что этого имени требует контракт базы
        (BaseContractGenerator._insert_route_tables).
        """
        return None

    # ─────────────────────────────────────────────────────────
    # УДАЛЕНИЕ ПУСТЫХ СТРОК ТАБЛИЦЫ АВТОМОБИЛЕЙ
    # ─────────────────────────────────────────────────────────

    def _remove_empty_vehicle_rows(self, doc) -> None:
        """
        Убирает из таблицы автомобилей строки без марки и без VIN.

        В бланке 12 строк данных; в заявке машин может быть меньше, и тогда
        docxtpl оставляет строки с пустыми значениями. Строка удаляется,
        только если ПУСТЫ обе колонки — заполненная хотя бы одним значением
        строка сохраняется (данные не теряем).

        Строка заголовка (индекс 0) не рассматривается.
        """
        for table in doc.tables:
            if not self._is_cargo_table(table):
                continue

            removed = 0
            for index in range(len(table.rows) - 1, 0, -1):
                row = table.rows[index]
                cells = row.cells
                if len(cells) < 3:
                    continue
                brand = cells[1].text.strip()
                vin = cells[2].text.strip()
                if brand or vin:
                    continue
                self._delete_row(table, row)
                removed += 1

            if removed:
                logger.info(
                    f"{TITLE}: удалено пустых строк таблицы груза: {removed}"
                )

    def _is_cargo_table(self, table) -> bool:
        """True, если таблица — перечень перевозимых автомобилей."""
        if not table.rows:
            return False

        headers = [
            cell.text.replace("–", "-").replace("—", "-").strip()
            for cell in table.rows[0].cells
        ]
        has_number = CARGO_TABLE_NUMBER_HEADER in headers
        has_brand = any(header in CARGO_TABLE_BRAND_HEADERS for header in headers)
        has_vin = CARGO_TABLE_VIN_HEADER in headers
        return has_number and has_brand and has_vin

    # ─────────────────────────────────────────────────────────
    # УДАЛЕНИЕ ПУСТЫХ БЛОКОВ ПОГРУЗКИ И ВЫГРУЗКИ
    # ─────────────────────────────────────────────────────────

    def _remove_empty_point_blocks(self, doc) -> None:
        """
        Убирает блоки грузоотправителей и грузополучателей без данных.

        В бланке по 10 блоков погрузки и выгрузки, а точек в заявке может
        быть меньше: строки сверх фактического числа остаются в документе с
        пустыми значениями («Грузоотправитель:»). Блок (пара абзацев
        «название + адрес») удаляется, только если ПУСТЫ обе его строки:
        блок, заполненный хотя бы одной из них, сохраняется — данные не
        теряем.

        Просматриваются абзацы верхнего уровня: в бланке блоки погрузки и
        выгрузки — обычные абзацы, а не строки таблицы.
        """
        removed = 0
        for name_re, address_re in (
            (SHIPPER_NAME_RE, SHIPPER_ADDRESS_RE),
            (CONSIGNEE_NAME_RE, CONSIGNEE_ADDRESS_RE),
        ):
            removed += self._remove_empty_blocks_of_kind(doc, name_re, address_re)

        if removed:
            logger.info(
                f"{TITLE}: удалено пустых блоков грузоотправителей и "
                f"грузополучателей: {removed}"
            )

    def _remove_empty_blocks_of_kind(self, doc, name_re, address_re) -> int:
        """
        Удаляет пары абзацев «название + адрес» с пустыми значениями.

        Возвращает число удалённых блоков. Абзацы просматриваются в порядке
        документа; строка адреса берётся только у СЛЕДУЮЩЕГО абзаца и только
        если он подходит под метку адреса — иначе это уже другой блок, и
        трогать его нельзя.
        """
        paragraphs = list(doc.paragraphs)
        removed = 0
        index = 0

        while index < len(paragraphs):
            name_paragraph = paragraphs[index]
            if self._paragraph_is_deleted(name_paragraph):
                index += 1
                continue

            match = name_re.match(self._paragraph_text(name_paragraph))
            if match is None:
                index += 1
                continue

            address_paragraph = None
            address_value = ""
            if index + 1 < len(paragraphs):
                candidate = paragraphs[index + 1]
                if not self._paragraph_is_deleted(candidate):
                    address_match = address_re.match(self._paragraph_text(candidate))
                    if address_match is not None:
                        address_paragraph = candidate
                        address_value = address_match.group("value")

            step = 2 if address_paragraph is not None else 1
            index += step

            if match.group("value").strip() or address_value.strip():
                continue

            self._delete_paragraph(name_paragraph)
            if address_paragraph is not None:
                self._delete_paragraph(address_paragraph)
            removed += 1

        return removed

    @staticmethod
    def _paragraph_text(paragraph) -> str:
        """
        Текст абзаца одной строкой без крайних пробелов.

        Значения из справочников приходят с переносами строк, а Word после
        рендера отдаёт их как «\\n» внутри текста абзаца: для поиска метки и
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

        Порядок ключей соответствует бланку: шапка → заказчик →
        грузоотправители → грузополучатели → план погрузки/выгрузки →
        груз → автовоз и водитель → стоимость → особые условия.
        """
        contract_data = ContractData.coerce(data)
        contract = contract_data.contract
        replacements: Dict[str, str] = {}

        carrier_type = self._carrier_type_of(contract_data)
        is_ip = "ИП" in carrier_type
        loadings, unloadings = self._resolve_route_points(data, contract_data)
        vehicles = self._cargo_vehicles(contract_data.vehicles)

        self._fill_header(replacements, contract)
        self._fill_customer(replacements, contract_data)
        self._fill_shippers(replacements, loadings)
        self._fill_consignees(replacements, unloadings)
        self._fill_route(replacements, contract, loadings, unloadings)
        self._fill_cargo(replacements, vehicles)
        self._fill_vehicles(replacements, vehicles)
        self._fill_driver(replacements, contract_data)
        self._fill_cost(replacements, contract, is_ip)
        self._fill_conditions(replacements, contract)

        # Переносы строк и задвоенные пробелы из справочников в бланке не
        # нужны (по ширине строки дают «рваное» выравнивание).
        self._flatten_replacements(replacements)

        logger.debug(
            f"{TITLE}: сформировано {len(replacements)} плейсхолдеров, "
            f"вариант={'ИП' if is_ip else 'ООО'}"
        )
        return replacements

    def _fill_header(self, replacements: Dict[str, str], contract: Dict[str, Any]) -> None:
        """Шапка: номер заявки и дата (день, месяц прописью, год)."""
        replacements["contract_number"] = str(contract.get("number", "") or "")

        date_iso = contract.get("date", "")
        replacements["contract_date_day"] = self._day_of_month(date_iso) if date_iso else ""
        replacements["contract_date_month"] = self._month_name(date_iso) if date_iso else ""
        replacements["contract_date_year"] = self._contract_year(date_iso)

    def _fill_customer(
        self, replacements: Dict[str, str], contract_data: ContractData
    ) -> None:
        """
        Заказчик: только наименование (стороны заявки фиксированы).

        Пустой customer — не ошибка генерации: в бланке останется пустая
        строка, а о незаполненном заказчике сообщит валидатор типа.
        """
        customer = contract_data.customer
        replacements["customer_name"] = self._single_line(customer.get("full_name") or "")

    def _fill_shippers(
        self, replacements: Dict[str, str], loadings: List[Dict[str, Any]]
    ) -> None:
        """Раздел 1: до 10 блоков «грузоотправитель + адрес погрузки»."""
        self._fill_points(replacements, loadings, "shipper")

    def _fill_consignees(
        self, replacements: Dict[str, str], unloadings: List[Dict[str, Any]]
    ) -> None:
        """Раздел 2: до 10 блоков «грузополучатель + адрес выгрузки»."""
        self._fill_points(replacements, unloadings, "consignee")

    def _fill_points(
        self,
        replacements: Dict[str, str],
        points: List[Dict[str, Any]],
        prefix: str,
    ) -> None:
        """
        Блоки точек маршрута: <prefix>_N_name / <prefix>_N_address (N = 1..10).

        Точек больше, чем блоков в бланке, — лишние не выводятся, в лог
        уходит предупреждение с количеством (названий и адресов в логе нет).
        """
        if len(points) > MAX_POINTS:
            logger.warning(
                f"{TITLE}: точек маршрута {len(points)}, в бланк помещается "
                f"{MAX_POINTS} — лишние не выводятся"
            )

        for number in range(1, MAX_POINTS + 1):
            point = points[number - 1] if number <= len(points) else None
            replacements[f"{prefix}_{number}_name"] = (
                self._single_line(point.get("name") or "") if point else ""
            )
            replacements[f"{prefix}_{number}_address"] = (
                self._single_line(point.get("address") or "") if point else ""
            )

        logger.info(
            f"{TITLE}: блоков «{prefix}» выведено — {min(len(points), MAX_POINTS)}"
        )

    def _fill_route(
        self,
        replacements: Dict[str, str],
        contract: Dict[str, Any],
        loadings: List[Dict[str, Any]],
        unloadings: List[Dict[str, Any]],
    ) -> None:
        """
        План погрузки и выгрузки: даты и интервалы времени.

        Приоритет — явные поля contract (их заполняет распознавание), затем
        дата и окно времени крайних точек маршрута. Даты печатаются в
        формате бланка «ДД.ММ.ГГГГ».
        """
        first_loading = loadings[0] if loadings else None
        last_unloading = unloadings[-1] if unloadings else None

        replacements["loading_date"] = self._format_date_full(
            contract.get("loading_date") or (first_loading.get("date") if first_loading else "")
        )
        loading_from, loading_to = self._plan_times(contract, "loading", first_loading)
        replacements["loading_time_from"] = loading_from
        replacements["loading_time_to"] = loading_to

        replacements["unloading_date"] = self._format_date_full(
            contract.get("unloading_date")
            or (last_unloading.get("date") if last_unloading else "")
        )
        unloading_from, unloading_to = self._plan_times(
            contract, "unloading", last_unloading
        )
        replacements["unloading_time_from"] = unloading_from
        replacements["unloading_time_to"] = unloading_to

    def _fill_cargo(
        self, replacements: Dict[str, str], vehicles: List[Dict[str, Any]]
    ) -> None:
        """Раздел 3: общее количество перевозимых автомобилей."""
        replacements["cargo_count"] = str(len(vehicles))
        logger.info(f"{TITLE}: машин в заявке — {len(vehicles)}")

    def _fill_vehicles(
        self, replacements: Dict[str, str], vehicles: List[Dict[str, Any]]
    ) -> None:
        """
        Таблица автомобилей: car_1..car_12 (пусто, если машин меньше).

        Тягач и прицеп в таблицу не попадают — они в разделе 4. Машин
        больше 12 в бланк не влезет: лишние отбрасываются с предупреждением
        (номеров VIN в лог не пишем).
        """
        if len(vehicles) > MAX_CARS:
            logger.warning(
                f"{TITLE}: машин {len(vehicles)}, в бланк помещается {MAX_CARS} — "
                f"лишние не выводятся"
            )

        for number in range(1, MAX_CARS + 1):
            vehicle = vehicles[number - 1] if number <= len(vehicles) else None
            replacements[f"car_{number}_brand"] = (
                self._single_line(vehicle.get("brand_model") or "") if vehicle else ""
            )
            replacements[f"car_{number}_vin"] = (
                self._single_line(vehicle.get("vin") or "") if vehicle else ""
            )

    def _fill_driver(
        self, replacements: Dict[str, str], contract_data: ContractData
    ) -> None:
        """
        Раздел 4 «Автовоз и водитель»: тягач, прицеп и ФИО водителя.

        Паспорт, водительское удостоверение, телефон и адрес в этом бланке
        не печатаются — в шаблоне для них нет плейсхолдеров.
        """
        tractor = contract_data.tractor
        trailer = contract_data.trailer
        driver = contract_data.driver

        replacements["tractor_brand"] = self._single_line(
            tractor.get("brand_model") or ""
        )
        replacements["tractor_plate"] = self._single_line(
            tractor.get("plate_number") or ""
        )
        replacements["trailer_brand"] = self._single_line(
            trailer.get("brand_model") or ""
        )
        replacements["trailer_plate"] = self._single_line(
            trailer.get("plate_number") or ""
        )
        replacements["driver_name"] = self._single_line(driver.get("full_name") or "")

    def _fill_cost(
        self,
        replacements: Dict[str, str],
        contract: Dict[str, Any],
        is_ip: bool,
    ) -> None:
        """
        Раздел 5 «Стоимость».

        Вариант ООО: три суммы — без НДС, НДС по ставке и итого, каждая
        цифрами и прописью. Вариант ИП: одна сумма «Без НДС», ставка НДС
        считается нулевой, а sum_wo_vat / sum_vat в карту замен НЕ кладутся:
        в ИП-бланке таких плейсхолдеров нет.
        """
        vat_rate_num = self._resolve_vat_rate_num(contract)
        price_wo_vat = self._price_without_vat(contract)

        if is_ip:
            total = round(price_wo_vat, 2)
            replacements["sum_total"] = self._format_money(total)
            replacements["sum_total_words"] = amount_to_words(total)

            logger.info(f"{TITLE}: стоимость (ИП, без НДС)={total:.2f}")
            return

        vat_amount = round(price_wo_vat * vat_rate_num / 100, 2)
        total = round(price_wo_vat + vat_amount, 2)

        replacements["sum_wo_vat"] = self._format_money(price_wo_vat)
        replacements["sum_wo_vat_words"] = amount_to_words(price_wo_vat)
        replacements["sum_vat"] = self._format_money(vat_amount)
        replacements["sum_vat_words"] = amount_to_words(vat_amount)
        replacements["sum_total"] = self._format_money(total)
        replacements["sum_total_words"] = amount_to_words(total)
        replacements["vat_rate"] = f"{vat_rate_num:.0f}%"

        logger.info(
            f"{TITLE}: стоимость (ООО) без НДС={price_wo_vat:.2f}, "
            f"НДС={vat_amount:.2f} ({vat_rate_num:.0f}%), итого={total:.2f}"
        )

    def _fill_conditions(
        self, replacements: Dict[str, str], contract: Dict[str, Any]
    ) -> None:
        """
        Раздел 6 «Особые условия».

        Если распознавание не нашло условий, печатается отписка образца —
        иначе раздел остался бы пустой строкой.
        """
        conditions = self._single_line(contract.get("special_conditions") or "")
        replacements["special_conditions"] = conditions or DEFAULT_SPECIAL_CONDITIONS

    # ─────────────────────────────────────────────────────────
    # Точки маршрута (грузоотправители и грузополучатели)
    # ─────────────────────────────────────────────────────────

    def _resolve_route_points(
        self, data: Any, contract_data: ContractData
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Точки маршрута с названиями грузоотправителя/грузополучателя.

        Названия берутся из исходных точек (см. докстринг модуля), адреса и
        даты — из приведённых ContractData.loadings/unloadings. Если массив
        точек пуст, но в contract остались исторические поля адресов
        (loading_address, unloading_address_1/2), точка собирается из них —
        как в договоре-заявке на перевозку
        (core/contracts/perevozka/generator.py::_resolve_route_points).
        """
        contract = contract_data.contract
        loadings = self._points_with_names(data, contract_data, "loadings")
        unloadings = self._points_with_names(data, contract_data, "unloadings")

        if not loadings:
            address = self._single_line(contract.get("loading_address") or "")
            if address:
                loadings = [{
                    "name": "",
                    "address": address,
                    "date": str(contract.get("loading_date") or ""),
                    "time_window": str(contract.get("loading_time_window") or ""),
                }]

        if not unloadings:
            for field in ("unloading_address_1", "unloading_address_2"):
                address = self._single_line(contract.get(field) or "")
                if not address:
                    continue
                unloadings.append({
                    "name": "",
                    "address": address,
                    "date": str(contract.get("unloading_date") or ""),
                    "time_window": str(contract.get("unloading_time_window") or ""),
                })

        return loadings, unloadings

    @classmethod
    def _points_with_names(
        cls, data: Any, contract_data: ContractData, field: str
    ) -> List[Dict[str, Any]]:
        """Приведённые точки маршрута + название из исходных данных."""
        points = [dict(point) for point in getattr(contract_data, field)]
        names = cls._raw_point_names(data, contract_data, field)

        for index, point in enumerate(points):
            name = str(point.get("name") or "")
            if not name:
                name = names[index] if index < len(names) else ""
            point["name"] = name

        return points

    @classmethod
    def _raw_point_names(
        cls, data: Any, contract_data: ContractData, field: str
    ) -> List[str]:
        """
        Названия точек из исходных данных (до приведения через ContractData).

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

        return [
            str(item.get("name", "") or "") if isinstance(item, Mapping) else ""
            for item in raw
        ]

    # ─────────────────────────────────────────────────────────
    # Вспомогательные вычисления
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _cargo_vehicles(cls, vehicles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Перевозимые машины: без тягача/прицепа и без полностью пустых строк."""
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
    def _plan_times(
        cls,
        contract: Dict[str, Any],
        prefix: str,
        point: Optional[Dict[str, Any]],
    ) -> Tuple[str, str]:
        """
        Интервал времени из строки «Время с {{from}} по {{to}}».

        Сначала явные поля contract (loading_time_from / unloading_time_from
        и парные _to), затем разбор окна времени точки («09:00-18:00» или
        «с 09:00 до 15:00»). Если ничего нет, оба значения пустые — бланк
        напечатает «Время с  по » без выдуманных часов.
        """
        time_from = str(contract.get(f"{prefix}_time_from") or "").strip()
        time_to = str(contract.get(f"{prefix}_time_to") or "").strip()

        if time_from and time_to:
            return time_from, time_to

        window = str(point.get("time_window") or "").strip() if point else ""
        if not window:
            return time_from, time_to

        parsed_from, parsed_to = cls._parse_time_window(window)
        return time_from or parsed_from, time_to or parsed_to

    @staticmethod
    def _parse_time_window(window: str) -> Tuple[str, str]:
        """«09:00-18:00» / «с 09:00 до 15:00» → («09:00», «15:00»)."""
        times = re.findall(r"\d{1,2}[:.]\d{2}", window)
        if len(times) >= 2:
            return times[0].replace(".", ":"), times[1].replace(".", ":")
        return "", ""

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
    def _price_without_vat(cls, contract: Dict[str, Any]) -> float:
        """
        Сумма без НДС (у ИП — единственная сумма заявки).

        Источники: price_without_vat (поле вкладки «Стоимость»), затем
        price_input — сумма из распознанного документа. Отсутствующие данные
        дают 0.00: суммы не выдумываются.
        """
        price = cls._to_float(contract.get("price_without_vat"), default=0.0)
        if price <= 0:
            price = cls._to_float(contract.get("price_input"), default=0.0)
        return round(price, 2)

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
    def _format_money(amount: float) -> str:
        """
        Сумма в формате образца: «221 099,18» (неразрывный пробел между
        разрядами, запятая перед копейками).
        """
        return f"{amount:,.2f}".replace(",", "\u00a0").replace(".", ",")

    @classmethod
    def _contract_year(cls, date_iso: Any) -> str:
        """
        Год заявки из её даты; если даты нет — текущий год.

        (В перевозке год всегда текущий — историческое поведение; здесь год
        берётся из даты: бланк печатает «… {{contract_date_year}} года.»)
        """
        if date_iso:
            try:
                return str(datetime.strptime(str(date_iso)[:10], "%Y-%m-%d").year)
            except (ValueError, TypeError):
                pass
        return str(datetime.now().year)
