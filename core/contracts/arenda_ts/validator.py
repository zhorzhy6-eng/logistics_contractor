# -*- coding: utf-8 -*-
"""
Валидатор договора аренды ТС с экипажем (ЭТАП 3.1.D.A.5).

Проверяет обязательный минимум типа — то, без чего договор аренды печатать
нельзя:

  * номер и дата договора, срок аренды (начало, окончание и их порядок);
  * реквизиты обеих сторон: Арендатора (наша сторона) и Арендодателя;
  * объект аренды (п. 2.1): тягач — марка, госномер, тип ТС; прицеп — марка
    и госномер;
  * хотя бы одна перевозимая машина (п. 3.1), у КАЖДОЙ — VIN;
  * хотя бы одна точка погрузки и хотя бы одна точка выгрузки с адресом;
  * маршрут аренды (п. 3.4);
  * экипаж (п. 3.5): ФИО, дата рождения, паспорт, водительское
    удостоверение, адрес регистрации, телефон;
  * арендная плата (п. 4.1): сумма без НДС и — в вариантах с НДС — ставка.

Вид Арендатора (`contract["carrier_type"]`: «ООО» / «ИП с НДС» /
«ИП без НДС») определяет две вещи:

  * КПП: плейсхолдер `lessee_kpp` есть только в ООО-бланке. Пустой КПП
    у ООО — ошибка, заполненный КПП у ИП — замечание («у индивидуального
    предпринимателя КПП не бывает»);
  * арендная плата: у ООО и ИП с НДС три суммы (без НДС / НДС по ставке /
    итого) и ставка НДС обязательна, у ИП без НДС сумма одна, ставка не
    применяется, а её ненулевое значение — замечание.

Пустое значение `carrier_type` трактуется как «ООО». Если поле не заполнено,
но в блоке Арендатора распознан `entity_type` = «ИП», вид выводится из него
и из ставки НДС — ровно так же выбирает бланк генератор
(`ArendaTsGenerator._resolve_carrier_type`).

Всё остальное — замечания, а не ошибки: краткие наименования сторон, ЭДО,
«нестандартный» VIN, машин больше 12, точек больше 10, точка с датой, но без
адреса, маршрут не в виде «откуда — куда», ненулевая ставка НДС у ИП без НДС,
основание полномочий ООО без «Устава». Договор с такими данными печатается,
но пользователь видит, что стоит дозаполнить.

Валидатор работает поверх BaseValidator (check_common + check_specific).
`core.validator.Validator` здесь НЕ вызывается: у аренды свой набор
обязательных полей (обе стороны с реквизитами, срок аренды, экипаж,
арендная плата) — `core/validator.py` не трогаем.

Генератор (`core/contracts/arenda_ts/generator.py`) импортирует этот модуль,
поэтому обратного импорта быть не должно: суммы здесь не считаются, как в
генераторе, проверяется только наличие базы арендной платы (порядок полей
повторяет `ArendaTsGenerator._base_price`).

Вход — `ContractData`: блоки сторон, маршрут и срок аренды лежат в `contract`
(распознавание отдаёт их в корне ответа, в `contract` их переносит
`ArendaTsGenerator._hoist_contract_fields`). Если блок стороны в `contract`
пуст, данные берутся из `ContractData.customer` (Арендатор) и
`ContractData.carrier` (Арендодатель) — так приходят данные вкладок
интерфейса.

Логи — только счётчики и вид Арендатора, без ПДн (ФИО, адресов, VIN,
наименований сторон и сумм).
"""

import logging
import re
from typing import Any, Dict, List, Mapping, Tuple

from core.contract_data import ContractData
from core.contracts.base_validator import BaseValidator
from core.contracts.contract_types import ContractType
from core.dates import parse_date
from core.validator import ValidationReport

logger = logging.getLogger("core.contracts.arenda_ts.validator")


class ArendaTsValidator(BaseValidator):
    """Проверки договора аренды ТС с экипажем перед печатью."""

    CONTRACT_TYPE = ContractType.ARENDA_TS.value

    #: Максимум машин в таблице п. 3.1 бланка (как в генераторе).
    MAX_CARS = 12

    #: Точек погрузки и выгрузки в бланке — по 10 каждого вида.
    MAX_POINTS = 10

    #: VIN: 17 символов, без букв I, O, Q (стандарт ISO 3779).
    VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

    #: Типы ТС из справочника машин, которые машинами п. 3.1 не являются:
    #: тягач и прицеп описаны отдельными строками бланка (п. 2.1).
    NON_CARGO_VEHICLE_TYPES = ("Тягач", "Полуприцеп", "Прицеп")

    #: Вид Арендатора по умолчанию и варианты ИП: от них зависят КПП и состав
    #: сумм (ключи совпадают со значениями contract["carrier_type"]).
    OOO = "ООО"
    IP_WITH_VAT = "ИП с НДС"
    IP_WITHOUT_VAT = "ИП без НДС"

    #: Поля базы арендной платы — порядок как в ArendaTsGenerator._base_price:
    #: в вариантах с НДС это сумма без НДС документа (sum_wo_vat), затем сумма
    #: вкладки «Стоимость»; у ИП без НДС первой идёт единственная сумма
    #: документа (sum_total → sum_wo_vat → price_without_vat).
    BASE_PRICE_FIELDS = ("sum_wo_vat", "price_without_vat")
    BASE_PRICE_FIELDS_WITHOUT_VAT = ("sum_total", "sum_wo_vat", "price_without_vat")

    #: Тире в маршруте («Москва — Калуга»). Кроме длинного тире принимаются
    #: короткое тире и дефис: в документах встречаются все три вида.
    ROUTE_DASHES = ("—", "–", "-")

    #: Откуда брать блок стороны, если в contract его нет: вкладки интерфейса
    #: кладут данные Арендатора в customer, Арендодателя — в carrier.
    PARTY_FALLBACKS: Mapping[str, str] = {"lessee": "customer", "lessor": "carrier"}

    # ─────────────────────────────────────────────────────────
    # Точка расширения базового валидатора
    # ─────────────────────────────────────────────────────────

    def check_specific(self, cd: ContractData, report: ValidationReport) -> None:
        """Все проверки аренды: договор → стороны → ТС → машины → точки →
        маршрут → экипаж → стоимость."""
        self._check_contract(cd, report)
        self._check_parties(cd, report)
        self._check_vehicle(cd, report)
        self._check_cargo(cd, report)
        self._check_points(cd, report)
        self._check_route(cd, report)
        self._check_driver(cd, report)
        self._check_cost(cd, report)

        # В лог — только вид Арендатора и количества: наименования сторон,
        # адреса, VIN и суммы в логах не нужны.
        logger.info(
            "Валидация аренды ТС: вариант=%s, машин=%d, точек погрузки=%d, "
            "точек выгрузки=%d",
            self._carrier_type(cd),
            len(self._cargo_vehicles(cd)),
            len(self._points(cd, "loadings")),
            len(self._points(cd, "unloadings")),
        )

    # ─────────────────────────────────────────────────────────
    # Проверки по группам
    # ─────────────────────────────────────────────────────────

    def _check_contract(self, cd: ContractData, report: ValidationReport) -> None:
        """Шапка договора и плановый срок аренды (п. 2.5)."""
        contract = cd.contract

        if not self._text(contract.get("number")):
            report.errors.append("Не заполнен номер договора аренды")
        if not self._text(contract.get("date")):
            report.errors.append("Не заполнена дата договора")

        start = self._text(contract.get("lease_start_date"))
        end = self._text(contract.get("lease_end_date"))

        if not start:
            report.errors.append("Не указана дата начала аренды")
        if not end:
            report.errors.append("Не указана дата окончания аренды")

        # Нераспознанная дата сравнению не подлежит: о ней уже сказано выше
        # («не указана»), выдумывать порядок дат из мусора не нужно.
        if start and end:
            start_dt = parse_date(start, warn=False)
            end_dt = parse_date(end, warn=False)
            if start_dt and end_dt and end_dt < start_dt:
                report.errors.append(
                    "Дата окончания аренды раньше даты начала аренды"
                )

    def _check_parties(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Разделы 1.1, 1.2 и 9: реквизиты Арендатора и Арендодателя.

        КПП проверяется только у ООО-Арендатора: в ИП-бланках такого
        плейсхолдера нет. У ИП заполненный КПП — замечание: печатать можно,
        но данные противоречат варианту бланка.
        """
        lessee = self._party_block(cd, "lessee")
        lessor = self._party_block(cd, "lessor")

        is_ooo, _is_ip_with_vat, _is_ip_without_vat = self._variant_flags(
            self._carrier_type(cd, lessee)
        )

        self._party_required(
            report,
            lessee,
            title="Арендатора",
            ogrn_label="ОГРН(ИП)",
            kpp_required=is_ooo,
        )
        self._party_required(
            report,
            lessor,
            title="Арендодателя",
            ogrn_label="ОГРН",
            kpp_required=False,
        )

        if not is_ooo and self._text(lessee.get("kpp")):
            report.warnings.append("У ИП не бывает КПП")

        # Краткие наименования печатаются в разделе 9 и в Приложении № 1.
        if not self._text(lessee.get("short_name")):
            report.warnings.append("Не заполнено краткое наименование Арендатора")
        if not self._text(lessor.get("short_name")):
            report.warnings.append("Не заполнено краткое наименование Арендодателя")

        # ЭДО необязателен, но если он есть в договоре, генератор его печатает
        # (lessee_edo / lessor_edo).
        if not self._edo(lessee, cd.contract.get("lessee_edo")):
            report.warnings.append("Не заполнен ЭДО Арендатора")
        if not self._edo(lessor, cd.contract.get("lessor_edo")):
            report.warnings.append("Не заполнен ЭДО Арендодателя")

        # У ООО основание полномочий — устав (генератор подставляет «Устава»
        # константой). Расхождение возможно только в распознанных данных, и это
        # замечание: печатать договор можно.
        basis = self._text(lessee.get("basis"))
        if is_ooo and basis and "устав" not in basis.lower():
            report.warnings.append(
                "Основание полномочий Арендатора не содержит «Устава»"
            )

    def _check_vehicle(self, cd: ContractData, report: ValidationReport) -> None:
        """Раздел 2.1: объект аренды — тягач (марка, госномер, тип) и прицеп."""
        tractor = cd.tractor
        if not self._text(tractor.get("brand_model")):
            report.errors.append("Не заполнена марка тягача")
        if not self._text(tractor.get("plate_number")):
            report.errors.append("Не заполнен госномер тягача")
        if not self._text(tractor.get("vehicle_type") or tractor.get("ts_type")):
            report.errors.append("Не заполнен тип ТС тягача")

        trailer = cd.trailer
        if not self._text(trailer.get("brand_model")):
            report.errors.append("Не заполнена марка прицепа")
        if not self._text(trailer.get("plate_number")):
            report.errors.append("Не заполнен госномер прицепа")

    def _check_cargo(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Раздел 3.1: перевозимые машины — минимум одна, у каждой обязателен VIN.

        Тягач и прицеп машинами таблицы 3.1 не являются: они описаны
        отдельными строками бланка (п. 2.1) и проверяются в _check_vehicle.
        """
        cargo = self._cargo_vehicles(cd)
        if not cargo:
            report.errors.append("Добавьте хотя бы одну перевозимую машину")
            return

        if len(cargo) > self.MAX_CARS:
            report.warnings.append(
                f"Машин {len(cargo)} — в бланк помещается {self.MAX_CARS}, "
                f"лишние в договор не попадут"
            )

        for number, vehicle in enumerate(cargo, 1):
            vin = self._text(vehicle.get("vin")).upper()
            if not vin:
                report.errors.append(f"Машина №{number}: не заполнен VIN")
            elif not self.VIN_PATTERN.match(vin):
                report.warnings.append(
                    f"Машина №{number}: VIN не похож на стандартный"
                )

    def _check_points(self, cd: ContractData, report: ValidationReport) -> None:
        """Разделы 3.2 и 3.3: точки погрузки и точки выгрузки."""
        loadings = self._points(cd, "loadings")
        unloadings = self._points(cd, "unloadings")

        if not any(self._text(point.get("address")) for point in loadings):
            report.errors.append("Укажите хотя бы одну точку погрузки")
        if not any(self._text(point.get("address")) for point in unloadings):
            report.errors.append("Укажите хотя бы одну точку выгрузки")

        self._check_point_details(report, loadings, "погрузки")
        self._check_point_details(report, unloadings, "выгрузки")

    def _check_route(self, cd: ContractData, report: ValidationReport) -> None:
        """Раздел 3.4: согласованный маршрут аренды."""
        route = self._text(cd.contract.get("route"))

        if not route:
            report.errors.append("Не указан маршрут аренды")
        elif not any(dash in route for dash in self.ROUTE_DASHES):
            report.warnings.append('Маршрут не похож на "откуда — куда"')

    def _check_driver(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Раздел 3.5: член экипажа Арендодателя (водитель).

        Паспорт и удостоверение принимаются в обоих видах: одной строкой
        (passport / license — так их отдаёт распознавание) или серией и
        номером отдельно (так их хранит справочник водителя).
        """
        driver = cd.driver

        if not self._text(driver.get("full_name")):
            report.errors.append("Не заполнено ФИО водителя (экипажа)")
        if not self._text(driver.get("birth_date")):
            report.errors.append("Не заполнена дата рождения водителя")
        if not self._document(driver, "passport"):
            report.errors.append("Не заполнены паспортные данные водителя")
        if not self._document(driver, "license"):
            report.errors.append("Не заполнены данные водительского удостоверения")
        if not self._text(driver.get("registration_address") or driver.get("address")):
            report.errors.append("Не заполнен адрес регистрации")
        if not self._text(driver.get("phone")):
            report.errors.append("Не заполнен телефон водителя")

    def _check_cost(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Раздел 4.1 «Арендная плата, НДС и порядок оплаты».

        База арендной платы должна быть больше нуля: в вариантах с НДС это
        сумма без НДС, у ИП без НДС — единственная сумма документа (порядок
        полей повторяет ArendaTsGenerator._base_price). Ставка НДС нужна
        только там, где НДС считается по ставке: у ООО и ИП с НДС её отсутствие
        — ошибка, у ИП без НДС ненулевая ставка — замечание.

        Точный расчёт сумм делает генератор; здесь достаточно понять, есть ли
        база арендной платы.
        """
        contract = cd.contract
        _is_ooo, _is_ip_with_vat, is_ip_without_vat = self._variant_flags(
            self._carrier_type(cd)
        )

        if self._base_price(contract, is_ip_without_vat) <= 0:
            report.errors.append("Стоимость без НДС должна быть больше нуля")

        rate = self._vat_rate_num(contract)
        if is_ip_without_vat:
            if rate > 0:
                report.warnings.append("У ИП без НДС ставка НДС не применяется")
        elif rate <= 0:
            report.errors.append("Не указана ставка НДС")

    # ─────────────────────────────────────────────────────────
    # Вспомогательные методы: стороны, точки, ТС
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _party_block(cls, cd: ContractData, field: str) -> Dict[str, Any]:
        """
        Блок стороны (lessee / lessor).

        Сначала contract: по этому пути блок приходит и от распознавания
        (промпт кладёт lessee / lessor в корень ответа, в contract их переносит
        ArendaTsGenerator._hoist_contract_fields), и от интерфейса. Если в
        contract блока нет, он берётся из ContractData: Арендатор — customer,
        Арендодатель — carrier (вкладки интерфейса).
        """
        nested = cd.contract.get(field)
        if isinstance(nested, Mapping) and nested:
            return dict(nested)

        fallback = getattr(cd, cls.PARTY_FALLBACKS.get(field, ""), None)
        if isinstance(fallback, Mapping) and fallback:
            return dict(fallback)

        return {}

    @classmethod
    def _party_required(
        cls,
        report: ValidationReport,
        party: Mapping[str, Any],
        *,
        title: str,
        ogrn_label: str,
        kpp_required: bool,
    ) -> None:
        """
        Обязательные реквизиты стороны.

        Наименование, ИНН, госрегистрация, адрес и ФИО руководителя печатаются
        в разделах 1.1 / 1.2 и 9 бланка, поэтому проверяются у обеих сторон.
        Метка госрегистрации у Арендатора-ИП — «ОГРНИП», поэтому она приходит
        параметром. КПП есть только в ООО-бланке (см. _check_parties).
        """
        if not cls._text(party.get("full_name")):
            report.errors.append(f"Не заполнено наименование {title}")
        if not cls._text(party.get("inn")):
            report.errors.append(f"Не заполнен ИНН {title}")
        if not cls._text(party.get("ogrn") or party.get("ogrnip")):
            report.errors.append(f"Не заполнен {ogrn_label} {title}")
        if not cls._address(party):
            report.errors.append(f"Не заполнен адрес {title}")
        if not cls._text(party.get("director_name")):
            report.errors.append(f"Не заполнено ФИО руководителя {title}")
        if kpp_required and not cls._text(party.get("kpp")):
            report.errors.append(f"Не заполнен КПП {title}")

    @classmethod
    def _address(cls, party: Mapping[str, Any]) -> str:
        """Адрес стороны: legal_address бланка, иначе address справочника."""
        return cls._text(party.get("legal_address") or party.get("address"))

    @classmethod
    def _edo(cls, party: Mapping[str, Any], flat: Any = "") -> str:
        """ЭДО стороны: поле edo блока, иначе одноимённое поле contract."""
        return cls._text(party.get("edo") or flat)

    @classmethod
    def _points(cls, cd: ContractData, field: str) -> List[Dict[str, Any]]:
        """Точки маршрута: приведённые ContractData + исходный список contract."""
        return cls._point_list(getattr(cd, field), cd.contract.get(field))

    @classmethod
    def _point_list(cls, *sources: Any) -> List[Dict[str, Any]]:
        """
        Точки маршрута из перечисленных источников.

        ContractData хранит точку как {address, date, time_window} и поля
        time_from / time_to распознавания отбрасывает
        (core.contract_data._as_point_list), поэтому точки собираются из всех
        источников по порядку: за основу берётся первая запись, а недостающие
        поля доливаются из следующих источников по тому же номеру точки — тем
        же приёмом, что в генераторе (ArendaTsGenerator._resolve_points).

        Пустая строка таблицы (все поля пустые) точкой не считается.
        """
        points: List[Dict[str, Any]] = []

        for source in sources:
            if not isinstance(source, (list, tuple)):
                continue

            for index, item in enumerate(source):
                if not isinstance(item, Mapping):
                    continue

                if index >= len(points):
                    points.append(dict(item))
                    continue

                for key, value in item.items():
                    if not cls._text(points[index].get(key)) and cls._text(value):
                        points[index][key] = value

        return [
            point for point in points
            if any(cls._text(value) for value in point.values())
        ]

    @classmethod
    def _check_point_details(
        cls,
        report: ValidationReport,
        points: List[Dict[str, Any]],
        title: str,
    ) -> None:
        """
        Подробности по точкам: сколько их и не потерялся ли адрес.

        Точка с датой (или временем) и без адреса — замечание: в бланке
        печатается адрес, и строка «3.2.N. Точка погрузки № N — . Плановая
        дата…» будет выглядеть недозаполненной, но печатать можно.
        """
        if len(points) > cls.MAX_POINTS:
            report.warnings.append(
                f"Точек {title} {len(points)} — в бланк помещается "
                f"{cls.MAX_POINTS}, лишние в договор не попадут"
            )

        for number, point in enumerate(points, 1):
            if cls._text(point.get("address")):
                continue
            if cls._point_has_time(point):
                report.warnings.append(f"Точка №{number}: адрес пуст")

    @classmethod
    def _point_has_time(cls, point: Mapping[str, Any]) -> bool:
        """Заполнено ли у точки хоть что-то, кроме адреса: дата или время."""
        return any(
            cls._text(point.get(key))
            for key in ("date", "time_from", "time_to", "time_window")
        )

    @classmethod
    def _cargo_vehicles(cls, cd: ContractData) -> List[Dict[str, Any]]:
        """Машины таблицы 3.1: без тягача/прицепа и без полностью пустых строк."""
        result = []
        for vehicle in cd.vehicles:
            if vehicle.get("vehicle_type") in cls.NON_CARGO_VEHICLE_TYPES:
                continue
            if not (
                cls._text(vehicle.get("vin")) or cls._text(vehicle.get("brand_model"))
            ):
                continue
            result.append(vehicle)
        return result

    @classmethod
    def _document(cls, driver: Mapping[str, Any], field: str) -> str:
        """
        Паспорт / водительское удостоверение.

        Одной строкой (passport / license — так их отдаёт распознавание) или
        серией и номером отдельно (так их хранит справочник водителя) — как
        в генераторе (ArendaTsGenerator._fill_driver).
        """
        single = cls._text(driver.get(field))
        if single:
            return single

        series = cls._digits(driver.get(f"{field}_series"))
        number = cls._digits(driver.get(f"{field}_number"))
        return f"{series} {number}".strip()

    # ─────────────────────────────────────────────────────────
    # Вспомогательные методы: вид Арендатора и суммы
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _carrier_type(
        cls, cd: ContractData, lessee: Mapping[str, Any] = None
    ) -> str:
        """
        Вид Арендатора: «ООО» / «ИП с НДС» / «ИП без НДС».

        Явное contract["carrier_type"] (его заполняет интерфейс) важнее
        догадки. Если поле пустое, вид выводится из распознавания ровно как в
        генераторе (ArendaTsGenerator._resolve_carrier_type): entity_type блока
        Арендатора («ООО» / «ИП»), а для ИП ещё и ставка НДС — «0%» означает
        вариант без НДС. Пустое значение (в том числе пустой блок Арендатора) —
        «ООО»: этот бланк и расчёт по умолчанию.
        """
        explicit = cls._text(cd.contract.get("carrier_type"))
        if explicit:
            return explicit

        if lessee is None:
            lessee = cls._party_block(cd, "lessee")

        entity = cls._text(lessee.get("entity_type")).upper()
        if "ИП" in entity or "ПРЕДПРИНИМАТЕЛЬ" in entity:
            if cls._vat_rate_num(cd.contract) <= 0:
                return cls.IP_WITHOUT_VAT
            return cls.IP_WITH_VAT

        return cls.OOO

    @classmethod
    def _variant_flags(cls, carrier_type: str) -> Tuple[bool, bool, bool]:
        """
        Признаки варианта бланка: (ООО, ИП с НДС, ИП без НДС).

        Правило совпадает с ArendaTsGenerator._variant_flags и с выбором файла
        бланка (_get_template_path): «ИП без НДС» — вариант без НДС,
        «ИП с НДС» — ИП с НДС, всё остальное (в том числе пустое значение) —
        ООО-бланк.
        """
        is_ip_without_vat = cls.IP_WITHOUT_VAT in carrier_type
        is_ip_with_vat = cls.IP_WITH_VAT in carrier_type
        is_ooo = not (is_ip_without_vat or is_ip_with_vat)
        return is_ooo, is_ip_with_vat, is_ip_without_vat

    @classmethod
    def _base_price(
        cls, contract: Mapping[str, Any], is_ip_without_vat: bool
    ) -> float:
        """
        База арендной платы: сумма без НДС (вариант с НДС) или единственная
        сумма документа (ИП без НДС). Ключи и порядок — как в генераторе.
        """
        keys = (
            cls.BASE_PRICE_FIELDS_WITHOUT_VAT if is_ip_without_vat
            else cls.BASE_PRICE_FIELDS
        )
        for key in keys:
            value = cls._money(contract.get(key))
            if value > 0:
                return value
        return 0.0

    @classmethod
    def _vat_rate_num(cls, contract: Mapping[str, Any]) -> float:
        """
        Ставка НДС числом: vat_rate_num → разбор строки vat_rate («22%») → 0.0.

        Пустое значение и явный ноль здесь не различаются: у ООО и ИП с НДС и
        то и другое означает «ставка не заполнена», а у ИП без НДС ставка и
        должна быть нулевой.
        """
        number = contract.get("vat_rate_num")
        if cls._text(number) == "":
            number = contract.get("vat_rate")
        return cls._money(number)

    # ─────────────────────────────────────────────────────────
    # Приведение значений
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _text(value: Any) -> str:
        """
        Строка без крайних пробелов; None — пустая строка.

        Числовой ноль остаётся «0»: пустое значение и явный ноль — разные
        вещи (например, ставка НДС 0% — это «без НДС», а не «ставка не
        указана»).
        """
        return "" if value is None else str(value).strip()

    @staticmethod
    def _digits(value: Any) -> str:
        """Только цифры значения («18 22» → «1822»); пустое — пустая строка."""
        return re.sub(r"\D", "", str(value or ""))

    @classmethod
    def _money(cls, value: Any) -> float:
        """
        Число из значения любого вида: «221 099,18», «22%», 180300.

        Разделители тысяч, неразрывный пробел, знак процента и запятая как
        десятичный разделитель — обычный формат документов и справочников.
        Пустое и непонятное значение даёт 0.0.
        """
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)

        text = (
            str(value).strip()
            .replace("%", "")
            .replace("\u00a0", "")
            .replace(" ", "")
        )
        if not text:
            return 0.0
        text = text.replace(",", ".")
        if text.count(".") > 1:
            # «1.234.567» — точки как разделители тысяч.
            head, _, tail = text.rpartition(".")
            text = head.replace(".", "") + "." + tail
        try:
            return float(text)
        except ValueError:
            return 0.0


__all__ = ["ArendaTsValidator"]
