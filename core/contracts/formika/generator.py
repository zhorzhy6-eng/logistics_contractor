# -*- coding: utf-8 -*-
"""
Генератор договора-заявки «Формика» (ЭТАП 3.1.A.3).

Формика — приложение к Генеральному договору на перевозку грузов
автотранспортом № 1 от «28» ноября 2025 г. между ООО «ТЕХНОЛОГИСТИКА»
(Экспедитор) и ООО «Формика» (Заказчик). Шаблон — templates/shablon_formika.docx
(пустой бланк с плейсхолдерами, ЭТАП 3.1.A.1).

Чем Формика отличается от договора-заявки на перевозку
(core/contracts/perevozka/generator.py — образец, от которого этот класс
СОЗНАТЕЛЬНО не наследуется):

  * один шаблон независимо от типа перевозчика: стороны фиксированы;
  * таблица груза на 12 машин, лишние строки удаляются постобработкой;
  * точек маршрута ровно по одной (погрузка и выгрузка) — таблиц
    по погрузкам/выгрузкам нет, поэтому _insert_route_tables() пуст;
  * стоимость в документе — ОДНА сумма, уже включающая НДС;
  * срок оплаты берётся из поля UI «Срок оплаты (дней)»: бланк печатает
    «в течение {{payment_days}} ({{payment_days_words}}) банковских дней»;
  * нет реквизитов сторон, банковских полей и блока НДС по ставке.

Логгер остаётся «core.contract_generator»: тесты и UI ловят сообщения
генерации именно по этому имени (см. base_generator.py).
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from core.contract_data import ContractData
from core.contracts.base_generator import (
    BaseContractGenerator,
    ConvertNewlinesStep,
    PostprocessStep,
)
from core.contracts.contract_types import ContractType
from core.contracts.formika.postprocess import RemoveEmptyVehicleRowsStep
from core.contracts.formika.validator import FormikaValidator
from core.num_to_words import amount_to_words

logger = logging.getLogger("core.contract_generator")

TITLE = "Формика"

#: Заголовки таблицы груза — по ним таблица ищется в готовом документе.
#: «Марка/Модель» принимается наравне со «Марка, модель» на случай, если
#: бланк когда-нибудь выровняют по шаблону перевозки.
CARGO_TABLE_BRAND_HEADERS = ("Марка, модель", "Марка/Модель")
CARGO_TABLE_VIN_HEADER = "VIN-номер"

#: Типы ТС из справочника машин, которые грузом не являются: тягач и
#: прицеп описаны отдельными блоками шаблона.
NON_CARGO_VEHICLE_TYPES = ("Тягач", "Полуприцеп", "Прицеп")

#: Размер таблицы груза в бланке — больше машин в документ не влезет.
MAX_CARS = 12


class FormikaGenerator(BaseContractGenerator):
    """
    Договор-заявка «Формика»: один шаблон, до 12 машин, сумма с НДС.

    Наследование — напрямую от BaseContractGenerator: общая механика
    (docxtpl, постобработка, форматирование) берётся из базы, а специфика
    перевозки (мультимаршрут, таблицы погрузок, НДС по ставке) не тянется.
    """

    CONTRACT_TYPE = ContractType.FORMIKA.value

    TEMPLATE_NAMES: Mapping[str, str] = {
        "formika": "shablon_formika.docx",
    }

    #: Префикс имени файла: «Договор-заявка_Формика_<номер>_<ГГГГММДД>.docx».
    FILE_PREFIX = "Договор-заявка_Формика"

    #: Валидатор типа (хук validate в базе).
    VALIDATOR_CLASS = FormikaValidator

    #: Ставка НДС по умолчанию, если в данных её нет (НДС в РФ).
    DEFAULT_VAT_RATE = 22.0

    # ─────────────────────────────────────────────────────────
    # ВЫБОР ШАБЛОНА
    # ─────────────────────────────────────────────────────────

    def _get_template_path(self, carrier_type: str) -> str:
        """
        Путь шаблона Формики.

        Тип перевозчика на выбор шаблона не влияет: стороны в этом типе
        договора фиксированы, бланк один.
        """
        return self.templates["formika"]

    # ─────────────────────────────────────────────────────────
    # КОНВЕЙЕР ПОСТОБРАБОТКИ
    # ─────────────────────────────────────────────────────────

    def postprocess_steps(self, data) -> List[PostprocessStep]:
        """
        Шаги постобработки: переносы строк → удаление пустых строк груза.

        Таблиц по погрузкам/выгрузкам у Формики нет, поэтому шаг
        RouteTablesStep (перевозка) не подключается.
        """
        return [ConvertNewlinesStep(self), RemoveEmptyVehicleRowsStep(self)]

    def _insert_route_tables(self, doc, contract_data: ContractData) -> None:
        """
        Не используется: в Формике маршрут — одна пара «погрузка/выгрузка»,
        она выводится плейсхолдерами, а не таблицами по точкам.

        Метод объявлен, потому что этого имени требует контракт базы
        (BaseContractGenerator._insert_route_tables).
        """
        return None

    # ─────────────────────────────────────────────────────────
    # УДАЛЕНИЕ ПУСТЫХ СТРОК ТАБЛИЦЫ ГРУЗА
    # ─────────────────────────────────────────────────────────

    def _remove_empty_vehicle_rows(self, doc) -> None:
        """
        Убирает из таблицы груза строки без марки и без VIN.

        В бланке 12 строк данных; в договоре машин может быть меньше, и
        тогда docxtpl оставляет строки с пустыми значениями. Строка
        удаляется, только если ПУСТЫ обе колонки — заполненная хотя бы
        одним значением строка сохраняется (данные не теряем).

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
                    f"Формика: удалено пустых строк таблицы груза: {removed}"
                )

    def _is_cargo_table(self, table) -> bool:
        """True, если таблица — перечень груза (по заголовкам колонок)."""
        if not table.rows:
            return False

        headers = [
            cell.text.replace("–", "-").replace("—", "-").strip()
            for cell in table.rows[0].cells
        ]
        has_brand = any(h in CARGO_TABLE_BRAND_HEADERS for h in headers)
        has_vin = CARGO_TABLE_VIN_HEADER in headers
        return has_brand and has_vin

    # ─────────────────────────────────────────────────────────
    # КАРТА ЗАМЕН
    # ─────────────────────────────────────────────────────────

    def _build_replacements_map(self, data: Any) -> Dict[str, str]:
        """
        Значения всех плейсхолдеров шаблона Формики.

        Порядок ключей соответствует бланку: шапка → груз → маршрут →
        исполнитель → стоимость.
        """
        contract_data = ContractData.coerce(data)
        contract = contract_data.contract
        replacements: Dict[str, str] = {}

        self._fill_header(replacements, contract)
        self._fill_cargo(replacements, contract_data)
        self._fill_route(replacements, contract_data)
        self._fill_driver_and_vehicles(replacements, contract_data)
        self._fill_cost(replacements, contract)

        # Переносы строк и задвоенные пробелы из справочников в бланке не
        # нужны (по ширине строки дают «рваное» выравнивание).
        self._flatten_replacements(replacements)

        logger.debug(f"Формика: сформировано {len(replacements)} плейсхолдеров")
        return replacements

    def _fill_header(self, replacements: Dict[str, str], contract: Dict[str, Any]) -> None:
        """Шапка: номер и дата договора-заявки."""
        replacements["contract_number"] = str(contract.get("number", "") or "")

        date_iso = contract.get("date", "")
        replacements["contract_date"] = self._day_of_month(date_iso) if date_iso else ""
        replacements["contract_month"] = self._month_name(date_iso) if date_iso else ""
        replacements["contract_year"] = self._contract_year(date_iso)

    def _fill_cargo(self, replacements: Dict[str, str], contract_data: ContractData) -> None:
        """
        Таблица груза: car_1..car_12 (пусто, если машин меньше).

        Тягач и прицеп в таблицу не попадают — они в блоке исполнителя.
        Машин больше 12 в бланк не влезет: лишние отбрасываются с
        предупреждением в лог (номеров VIN в лог не пишем).
        """
        vehicles = self._cargo_vehicles(contract_data.vehicles)

        if len(vehicles) > MAX_CARS:
            logger.warning(
                f"Формика: машин {len(vehicles)}, в бланк помещается {MAX_CARS} — "
                f"лишние не выводятся"
            )

        replacements["cargo_count"] = str(len(vehicles))

        for number in range(1, MAX_CARS + 1):
            if number <= len(vehicles):
                vehicle = vehicles[number - 1]
                replacements[f"car_{number}_brand"] = self._single_line(
                    vehicle.get("brand_model") or ""
                )
                replacements[f"car_{number}_vin"] = self._single_line(
                    vehicle.get("vin") or ""
                )
            else:
                replacements[f"car_{number}_brand"] = ""
                replacements[f"car_{number}_vin"] = ""

        logger.info(f"Формика: машин в договоре — {len(vehicles)}")

    def _fill_route(self, replacements: Dict[str, str], contract_data: ContractData) -> None:
        """
        Маршрут: адреса, плановые дата и время погрузки.

        В бланке по одной строке на погрузку и выгрузку, поэтому берётся
        первая точка; если точек больше — пишем предупреждение (адреса в
        лог не попадают).
        """
        contract = contract_data.contract
        loadings = self._resolve_points(
            contract_data.loadings,
            contract.get("loading_address"),
            contract.get("loading_date"),
            contract.get("loading_time_window"),
        )
        unloadings = self._resolve_points(
            contract_data.unloadings,
            contract.get("unloading_address_1") or contract.get("unloading_address"),
            contract.get("unloading_date"),
            contract.get("unloading_time_window"),
        )

        for title, points in (("погрузки", loadings), ("выгрузки", unloadings)):
            if len(points) > 1:
                logger.warning(
                    f"Формика: точек {title} — {len(points)}, "
                    f"в бланк попадает первая"
                )

        replacements["route"] = self._single_line(contract.get("route", "") or "")
        replacements["loading_address"] = (
            self._single_line(loadings[0]["address"]) if loadings else ""
        )
        replacements["unloading_address"] = (
            self._single_line(unloadings[0]["address"]) if unloadings else ""
        )

        # Плановая дата погрузки: явное поле, иначе дата первой погрузки.
        plan_date = contract.get("loading_plan_date") or (
            loadings[0]["date"] if loadings else ""
        )
        replacements["loading_plan_date"] = self._format_date_full(plan_date)

        time_from, time_to = self._plan_times(contract, loadings)
        replacements["loading_plan_time_from"] = time_from
        replacements["loading_plan_time_to"] = time_to

    def _fill_driver_and_vehicles(
        self, replacements: Dict[str, str], contract_data: ContractData
    ) -> None:
        """Блок исполнителя: водитель, тягач, прицеп."""
        driver = contract_data.driver
        replacements["driver_name"] = self._single_line(driver.get("full_name") or "")
        replacements["driver_birth_date"] = self._format_date_full(
            driver.get("birth_date", "")
        )

        passport_series = str(driver.get("passport_series", "") or "").strip()
        passport_number = str(driver.get("passport_number", "") or "").strip()
        replacements["driver_passport"] = f"{passport_series} {passport_number}".strip()

        replacements["driver_passport_issuer"] = self._single_line(
            driver.get("passport_issuer") or ""
        )
        replacements["driver_passport_date"] = self._format_date_full(
            driver.get("passport_issue_date", "")
        )

        license_series = str(driver.get("license_series", "") or "").strip()
        license_number = str(driver.get("license_number", "") or "").strip()
        replacements["driver_license"] = f"{license_series} {license_number}".strip()

        replacements["driver_address"] = self._single_line(
            driver.get("registration_address") or ""
        )
        replacements["driver_phone"] = self._single_line(driver.get("phone") or "")

        tractor = contract_data.tractor
        trailer = contract_data.trailer

        replacements["tractor_brand"] = self._single_line(
            tractor.get("brand_model") or ""
        )
        replacements["tractor_plate"] = self._single_line(
            tractor.get("plate_number") or ""
        )
        # Тип ТС (например «Грузовой тягач седельный») приходит из карточки
        # тягача. Вкладка «Тягач и полуприцеп» его пока не собирает, поэтому
        # без данных поле остаётся пустым: значения не выдумываем.
        replacements["tractor_type"] = self._single_line(
            tractor.get("vehicle_type") or tractor.get("ts_type") or ""
        )
        replacements["trailer_brand"] = self._single_line(
            trailer.get("brand_model") or ""
        )
        replacements["trailer_plate"] = self._single_line(
            trailer.get("plate_number") or ""
        )

    def _fill_cost(self, replacements: Dict[str, str], contract: Dict[str, Any]) -> None:
        """
        Стоимость: одна сумма, УЖЕ ВКЛЮЧАЮЩАЯ НДС, ставка и срок оплаты.

        В образце Формики «Стоимость перевозки составляет: 75 000,00 руб.
        (семьдесят пять тысяч рублей 00 копеек), включая НДС 22%» — то есть
        в бланк идёт сумма с НДС, а не ставка без НДС, как в перевозке.

        Срок оплаты (п. «Порядок оплаты»): число банковских дней из поля UI
        и то же число прописью в родительном падеже — «45 (сорока пяти)».
        Пока в бланке стояла константа «3 (трех)», поле вкладки на документ
        не влияло; теперь незаданный срок печатается пустым местом, и о нём
        скажет валидатор — число генератор не выдумывает.
        """
        vat_rate_num, vat_rate_text = self._resolve_vat(contract)
        total = self._total_with_vat(contract, vat_rate_num)

        replacements["sum_total"] = self._format_money(total)
        replacements["sum_total_words"] = amount_to_words(total)
        replacements["vat_rate"] = vat_rate_text

        replacements["payment_days"] = str(contract.get("payment_days") or "")
        replacements["payment_days_words"] = self._days_to_words_genitive(
            contract.get("payment_days")
        )

        logger.info(
            f"Формика: сумма с НДС={total:.2f}, НДС={vat_rate_text}, "
            f"срок оплаты (дней)={replacements['payment_days'] or '—'}"
        )

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
    def _resolve_points(
        cls,
        points: List[Dict[str, Any]],
        legacy_address: Optional[str],
        legacy_date: Optional[str],
        legacy_time_window: Optional[str],
    ) -> List[Dict[str, Any]]:
        """
        Точки маршрута с учётом исторических полей contract.

        Как и в перевозке: если массив точек пуст, но в contract есть
        старое поле адреса — точка собирается из него.
        """
        if points:
            return list(points)

        address = str(legacy_address or "").strip()
        if not address:
            return []

        return [{
            "address": address,
            "date": legacy_date or "",
            "time_window": legacy_time_window or "",
        }]

    def _plan_times(
        self, contract: Dict[str, Any], loadings: List[Dict[str, Any]]
    ) -> tuple:
        """
        Интервал времени погрузки: «с {{from}} до {{to}}».

        Сначала явные поля contract, затем разбор окна времени первой
        погрузки («09:00-18:00» или «с 09:00 до 15:00»). Если ничего нет,
        оба значения пустые — бланк напечатает «с  до » без выдуманных часов.
        """
        time_from = str(contract.get("loading_plan_time_from") or "").strip()
        time_to = str(contract.get("loading_plan_time_to") or "").strip()

        if time_from and time_to:
            return time_from, time_to

        window = ""
        if loadings:
            window = str(loadings[0].get("time_window") or "").strip()
        if not window:
            return time_from, time_to

        parsed = self._parse_time_window(window)
        return time_from or parsed[0], time_to or parsed[1]

    @staticmethod
    def _parse_time_window(window: str) -> tuple:
        """«09:00-18:00» / «с 09:00 до 15:00» → («09:00», «15:00»)."""
        times = re.findall(r"\d{1,2}[:.]\d{2}", window)
        if len(times) >= 2:
            return times[0].replace(".", ":"), times[1].replace(".", ":")
        return "", ""

    @classmethod
    def _resolve_vat(cls, contract: Dict[str, Any]) -> tuple:
        """
        Ставка НДС: (число, текст).

        Число нужно для расчёта суммы с НДС, текст — для бланка
        («включая НДС 22%»). Если ставки нет, берётся DEFAULT_VAT_RATE.
        """
        raw = contract.get("vat_rate")
        number = contract.get("vat_rate_num")

        if number is None and raw:
            number = cls._to_float(str(raw).replace("%", "").strip(), default=None)

        if number is None:
            number = cls.DEFAULT_VAT_RATE

        text = str(raw).strip() if raw else f"{number:.0f}%"
        return float(number), text

    @classmethod
    def _total_with_vat(cls, contract: Dict[str, Any], vat_rate_num: float) -> float:
        """
        Сумма договора, включающая НДС.

        Источники по приоритету:
          1. price_with_vat — готовая сумма с НДС (её считает интерфейс);
          2. price_without_vat + ставка;
          3. price_input — сумма из распознанного документа (в Формике она
             уже включает НДС, см. core/prompts/formika.py).
        Отсутствующие данные дают 0.00 — сумма не выдумывается.
        """
        price_with_vat = cls._to_float(contract.get("price_with_vat"), default=0.0)
        if price_with_vat > 0:
            return round(price_with_vat, 2)

        price_without_vat = cls._to_float(contract.get("price_without_vat"), default=0.0)
        if price_without_vat > 0:
            if vat_rate_num > 0:
                return round(price_without_vat * (1 + vat_rate_num / 100), 2)
            return round(price_without_vat, 2)

        return round(cls._to_float(contract.get("price_input"), default=0.0), 2)

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> Optional[float]:
        """
        Число из значения любого вида: «75 000,00», «219966.0», 180300.

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
        Сумма в формате образца: «75 000,00» (неразрывный пробел между
        разрядами, запятая перед копейками).
        """
        return f"{amount:,.2f}".replace(",", "\u00a0").replace(".", ",")

    @classmethod
    def _contract_year(cls, date_iso: Any) -> str:
        """
        Год договора из его даты; если даты нет — текущий год.

        (В перевозке год всегда текущий — историческое поведение; для
        Формики год берётся из даты: бланк печатает «…» {{contract_year}} г.»)
        """
        if date_iso:
            try:
                return str(datetime.strptime(str(date_iso)[:10], "%Y-%m-%d").year)
            except (ValueError, TypeError):
                pass
        return str(datetime.now().year)
