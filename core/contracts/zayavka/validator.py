# -*- coding: utf-8 -*-
"""
Валидатор заявки Хавалов — Excel-формы «ЗАЯВКА на перевозку автомобилей»
(ЭТАП 3.1.E.A.3).

ГЛАВНОЕ ПРАВИЛО: ВАЛИДАТОР НЕ МЕШАЕТ РАБОТЕ
-------------------------------------------
Заявка приходит от заказчика готовой формой, и почти всё, что в ней может
быть «не так», печатать не мешает: машин может быть одна или одиннадцать,
VIN может быть неизвестен («Vin по факту погрузки»), ставка может быть
не заполнена. Поэтому ошибок (``report.errors``) здесь почти нет —
только структурные, то есть случаи, когда файл невозможно даже открыть
или в нём нет самой формы:

  * файл не открывается (не xlsx, битый, нет доступа);
  * в файле нет листа TDSheet;
  * в листе не найдена строка шапки (нет колонки «Номер Лота»).

Всё остальное — предупреждения (``report.warnings``):

  * машин больше 10 — в бланк помещается 10 строк, лишние не попадут;
  * в строке заполнены марка/модель/дилер, а VIN пуст;
  * ставка с НДС не заполнена (0.0);
  * в заявке не заполнены фиксированные стороны (Заказчик, Перевозчик);
  * в присланном файле нет блока сторон «Заказчик / Перевозчик»;
  * VIN не похож на стандартный (17 символов, без I, O, Q);
  * мало данных: не заполнены маршрут, автовоз, прицеп, водитель;
  * даты не похожи на дату, а время — на «ЧЧ:ММ» (как их ждёт бланк).

ПОЧЕМУ НЕ ВЫЗЫВАЕТСЯ core.validator.Validator
---------------------------------------------
Общий валидатор проверяет договор-заявку на перевозку: номер и дату
договора, стоимость без НДС, срок оплаты, тягач и полуприцеп отдельными
блоками. У Excel-формы Хавалов ничего этого нет — в схеме промпта
(core/prompts/havaly.py) всего два блока, ``zayavka`` и ``vehicles``, и
сторон с реквизитами в ней не бывает. Поэтому ``check_common`` здесь
пустой: общие правила к этому типу неприменимы, а ``core/validator.py``
не трогаем (он общий для остальных типов).

ЧТО ПРИНИМАЕТСЯ НА ВХОД
-----------------------
``check()`` понимает три вида входа, потому что валидатор вызывают и по
данным распознавания, и по присланному файлу:

  * путь к .xlsx (строка или ``pathlib.Path``) — тогда сначала делаются
    структурные проверки файла, а потом проверяются прочитанные данные;
  * словарь ``{"zayavka": {...}, "vehicles": [...]}`` — схема промпта A.2;
  * плоский словарь или ``ContractData`` — блоки берутся из ``contract``
    (так данные придут из сборщика UI на этапе B).

ПОЧЕМУ ДАННЫЕ ПЕРЕНОСЯТСЯ В ``contract``
----------------------------------------
``ContractData.coerce`` (core/contract_data.py) знает только свои поля —
``driver``, ``carrier``, ``customer``, ``vehicles``, ``tractor``,
``trailer``, ``contract``, — и блок ``zayavka`` в них не входит: если
отдать схеме промпта объект ``ContractData`` как есть, общие сведения
заявки потеряются. Поэтому ``_extract`` кладёт поля ``zayavka`` ВНУТРЬ
``contract`` — так же, как это делает распознавание аренды
(``ArendaTsGenerator._hoist_contract_fields``), — а ``check_specific``
читает их оттуда. ``ContractData`` при этом не меняется.

ЛОГИ
----
Только счётчики и имена проверок: ни ФИО, ни VIN, ни номеров, ни адресов
в логе не бывает (как и в остальных валидаторах проекта).
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from core.contract_data import ContractData
from core.contracts.base_validator import BaseValidator
from core.contracts.contract_types import ContractType
from core.contracts.zayavka.generator import (
    CARRIER_NAME,
    CUSTOMER_NAME,
    MAX_VEHICLES,
    PARTY_LABEL,
    PARTY_LABELS,
    VEHICLE_KEYS,
    ZAYAVKA_KEYS,
    ZayavkaExcelGenerator,
    ZayavkaTemplateError,
    is_document_time,
)
from core.dates import parse_date
from core.validator import ValidationReport

logger = logging.getLogger("core.contracts.zayavka.validator")


class ZayavkaExcelValidator(BaseValidator):
    """
    Проверки Excel-заявки Хавалов перед сохранением готового файла.

    Валидатор ничего не блокирует: генератор заполняет бланк независимо от
    отчёта, а отчёт нужен, чтобы пользователь видел, что стоит дозаполнить.
    """

    CONTRACT_TYPE = ContractType.ZAYAVKA_EXCEL.value

    #: Максимум машин в бланке (10 строк данных) — как в генераторе.
    MAX_VEHICLES = MAX_VEHICLES

    #: VIN: 17 символов, без букв I, O, Q (стандарт ISO 3779).
    VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

    #: Поля заявки, без которых форма остаётся почти пустой. Проверяются
    #: группой: одна строка «не заполнено: …» вместо десятка замечаний.
    ROUTE_FIELDS: Tuple[Tuple[str, str], ...] = (
        ("loading_city", "город погрузки"),
        ("loading_point", "пункт погрузки"),
        ("unloading_city", "город доставки"),
        ("unloading_point", "пункт разгрузки"),
        ("loading_plan_date", "планируемая дата погрузки"),
        ("loading_plan_time", "время погрузки"),
    )

    #: Поля автовоза и прицепа (та же логика: одна строка на группу).
    VEHICLE_UNIT_FIELDS: Tuple[Tuple[str, str], ...] = (
        ("tractor_brand", "марка автовоза"),
        ("tractor_plate", "номер автовоза"),
        ("trailer_brand", "марка прицепа"),
        ("trailer_plate", "номер прицепа"),
    )

    #: Поля водителя.
    DRIVER_FIELDS: Tuple[Tuple[str, str], ...] = (
        ("driver_last_name", "фамилия"),
        ("driver_first_name", "имя"),
        ("driver_license_number", "номер В/У"),
        ("driver_passport_series", "серия паспорта"),
        ("driver_passport_number", "номер паспорта"),
        ("driver_birth_date", "дата рождения"),
        ("driver_phone", "телефон"),
    )

    #: Поля-даты: значения должны разбираться как дата.
    DATE_FIELDS: Tuple[Tuple[str, str], ...] = (
        ("date", "дата заявки"),
        ("driver_license_issue_date", "дата выдачи В/У"),
        ("driver_passport_issue_date", "дата выдачи паспорта"),
        ("driver_birth_date", "дата рождения"),
        ("loading_plan_date", "планируемая дата погрузки"),
    )

    #: Стороны заявки: в схеме промпта фиксированы, но в данных их может и
    #: не быть — распознавание вернуло бы их, а вот произвольный словарь
    #: нет. Отсутствие — замечание, не ошибка: заявку всё равно можно
    #: сохранить, стороны в бланке напечатаны.
    FIXED_PARTY_KEYS: Tuple[Tuple[str, str], ...] = (
        ("customer_name", "Заказчик"),
        ("carrier_name", "Перевозчик"),
    )

    # ─────────────────────────────────────────────────────────
    # Публичный API
    # ─────────────────────────────────────────────────────────

    def check(self, data: Any) -> ValidationReport:
        """
        Полная проверка: структурные проверки файла + проверки данных.

        Перекрывает ``BaseValidator.check`` только ради одной вещи:
        структурные проверки ходят в файловую систему и могут упасть
        (файла нет, нет доступа, битый архив). Валидатор не имеет права
        ронять вызывающий код — такая неудача становится ошибкой отчёта.
        """
        report = ValidationReport()
        source = Path(data) if self._is_path(data) else None

        if source is not None:
            self._check_file(source, report)

        try:
            cd = ContractData.coerce(self._extract(data))
        except Exception as error:  # noqa: BLE001 — отчёт вместо падения
            logger.error(
                "Валидация заявки: не удалось разобрать данные (%s)",
                type(error).__name__,
            )
            report.errors.append(
                f"Не удалось разобрать данные заявки: {type(error).__name__}"
            )
            return report

        try:
            self.check_specific(cd, report)
        except Exception as error:  # noqa: BLE001 — отчёт вместо падения
            logger.error(
                "Валидация заявки: проверка не выполнена (%s)", type(error).__name__
            )
            report.errors.append(
                f"Проверка заявки не выполнена: {type(error).__name__}"
            )

        self._log(cd, report)
        return report

    # ─────────────────────────────────────────────────────────
    # Точки расширения базового валидатора
    # ─────────────────────────────────────────────────────────

    def check_common(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Общие правила договора-заявки к Excel-форме неприменимы.

        ``core.validator.Validator`` (его вызывает перевозка) требует
        номер и дату договора, стоимость без НДС, срок оплаты, тягач и
        прицеп отдельными блоками. У Хавалов ничего этого нет: заявка — это
        таблица машин и общие сведения о перевозке, а стороны фиксированы.
        """
        return None

    def check_specific(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Все проверки типа: машины → ставка → стороны → качество данных.

        Порядок задан так, чтобы важное было в начале отчёта: сначала то,
        что мешает заполнить таблицу, потом то, что стоит дозаполнить.
        """
        zayavka = self._zayavka_of(cd)
        vehicles = self._vehicles_of(cd)

        self._check_vehicles(vehicles, report)
        self._check_price(zayavka, report)
        self._check_parties(zayavka, report)
        self._check_route(zayavka, report)
        self._check_vehicle_unit(zayavka, report)
        self._check_driver(zayavka, report)
        self._check_date_formats(zayavka, report)

        logger.info(
            "Валидация заявки Хавалов: машин=%d, полей заявки заполнено=%d из %d",
            len(vehicles),
            sum(1 for value in zayavka.values() if self._text(value)),
            len(ZAYAVKA_KEYS),
        )

    # ─────────────────────────────────────────────────────────
    # Структурные проверки (единственные, что дают ошибки)
    # ─────────────────────────────────────────────────────────

    def _check_file(self, path: Path, report: ValidationReport) -> None:
        """
        Структура присланного файла: открывается, лист TDSheet, шапка.

        Это единственные критические ошибки типа: без формы заполнять
        нечего. Всё содержимое файла проверяется отдельно, по данным.
        """
        try:
            generator = self._generator()
            sheet = generator._load_sheet(path)
            generator._header_columns(sheet, strict=False)
        except ZayavkaTemplateError as error:
            report.errors.append(str(error))
            logger.warning("Валидация заявки: файл непригоден (%s)", type(error).__name__)
            return
        except Exception as error:  # noqa: BLE001 — отчёт вместо падения
            report.errors.append(
                f"Не удалось прочитать файл заявки {path.name}: "
                f"{type(error).__name__}"
            )
            logger.warning(
                "Валидация заявки: чтение файла не удалось (%s)", type(error).__name__
            )
            return

        self._check_party_block(sheet, report)
        logger.debug("Валидация заявки: структура файла в порядке")

    @classmethod
    def _check_party_block(cls, sheet: Any, report: ValidationReport) -> None:
        """
        Есть ли в файле нижний блок сторон и те ли в нём стороны.

        Проверяются и подписи («Заказчик», «Перевозчик»), и значения рядом
        с ними: форма чужого заказчика может быть похожа на эту таблицей,
        но стороны в ней другие. Сохранению это не мешает — стороны в
        бланке напечатаны и в файл не пишутся, — поэтому здесь только
        замечания.

        Значение берётся из ячейки СПРАВА от подписи. Если её нет, про
        сторону ничего не говорим: у некоторых заказчиков названия стоят
        строкой ниже, и выдумывать по этому поводу замечание незачем.
        """
        labels = {label.lower() for label in PARTY_LABELS}
        found = False

        for row in sheet.iter_rows():
            for cell in row:
                value = cls._text(cell.value)
                if value.lower() not in labels:
                    continue
                # Подпись — это подпись, а не значение: «Заказчик» в строке
                # данных машиной не считается (см. _is_party_row).
                found = True
                name = cls._text(
                    sheet.cell(row=cell.row, column=cell.column + 1).value
                )
                if name and not cls._party_matches(name, value):
                    report.warnings.append(
                        f"В файле под «{value}» указана другая организация"
                    )

        if not found:
            report.warnings.append(
                f"В файле нет блока сторон («{PARTY_LABEL}» / «Перевозчик»)"
            )

    @classmethod
    def _party_matches(cls, name: str, label: str) -> bool:
        """
        Та ли это сторона, что напечатана в бланке.

        Сравнение по словам: в форме заказчика перевозчик напечатан как
        ООО "ТЕХНОЛОГИСТИКА", а в схеме промпта — как
        ООО ТЕХНОЛОГИСТИКА (кавычки, лишние пробелы и регистр не важны).
        """
        expected = (
            CUSTOMER_NAME if label.lower() == PARTY_LABELS[0].lower()
            else CARRIER_NAME
        )
        words = re.findall(r"[^\W_]+", name.lower())
        return cls._text(expected).lower() in " ".join(words)

    # ─────────────────────────────────────────────────────────
    # Проверки данных (замечания)
    # ─────────────────────────────────────────────────────────

    def _check_vehicles(
        self,
        vehicles: List[Dict[str, Any]],
        report: ValidationReport,
    ) -> None:
        """
        Машины: количество и VIN.

        Жёсткого требования «ровно 10» нет: одна машина — одна, десять —
        десять. Больше десяти — замечание: в бланк помещается 10 строк.
        """
        if not vehicles:
            report.warnings.append("В заявке нет ни одной машины")
            return

        if len(vehicles) > self.MAX_VEHICLES:
            report.warnings.append(
                f"Машин {len(vehicles)} — в бланк помещается "
                f"{self.MAX_VEHICLES}, лишние в файл не попадут"
            )

        for number, vehicle in enumerate(vehicles, 1):
            vin = self._text(vehicle.get("vin"))
            described = any(
                self._text(vehicle.get(key))
                for key in VEHICLE_KEYS if key != "vin"
            )
            if not vin:
                if described:
                    report.warnings.append(
                        f"Машина №{number}: не заполнен VIN "
                        f"(марка, модель или дилер указаны)"
                    )
                continue
            if not self.VIN_PATTERN.match(vin.upper()):
                report.warnings.append(
                    f"Машина №{number}: VIN не похож на стандартный"
                )

    def _check_price(
        self,
        zayavka: Mapping[str, Any],
        report: ValidationReport,
    ) -> None:
        """Ставка с НДС: пустая или нулевая — замечание (печатать можно)."""
        if self._money(zayavka.get("price_with_vat")) <= 0:
            report.warnings.append("Не заполнена ставка с НДС")

    def _check_parties(
        self,
        zayavka: Mapping[str, Any],
        report: ValidationReport,
    ) -> None:
        """
        Фиксированные стороны заявки в данных.

        В схеме промпта они заполнены всегда (Заказчик — «Сюрлогистик»,
        Перевозчик — «ООО ТЕХНОЛОГИСТИКА»), и чтение формы подставляет их
        само. Если сторон нет — значит, данные пришли не от распознавания
        формы: замечание, не ошибка.
        """
        for key, title in self.FIXED_PARTY_KEYS:
            if not self._text(zayavka.get(key)):
                report.warnings.append(
                    f"В заявке не заполнена фиксированная сторона «{title}»"
                )

    def _check_route(
        self,
        zayavka: Mapping[str, Any],
        report: ValidationReport,
    ) -> None:
        """Маршрут и план погрузки: одной строкой на всю группу полей."""
        missing = self._missing(zayavka, self.ROUTE_FIELDS)
        if missing:
            report.warnings.append("Не заполнено в заявке: " + ", ".join(missing))

    def _check_vehicle_unit(
        self,
        zayavka: Mapping[str, Any],
        report: ValidationReport,
    ) -> None:
        """Автовоз и прицеп: поля печатаются в каждой строке таблицы."""
        missing = self._missing(zayavka, self.VEHICLE_UNIT_FIELDS)
        if missing:
            report.warnings.append("Не заполнено в заявке: " + ", ".join(missing))

    def _check_driver(
        self,
        zayavka: Mapping[str, Any],
        report: ValidationReport,
    ) -> None:
        """Водитель: ФИО, документы, телефон — тоже одной строкой."""
        missing = self._missing(zayavka, self.DRIVER_FIELDS)
        if missing:
            report.warnings.append(
                "Не заполнены данные водителя: " + ", ".join(missing)
            )

    def _check_date_formats(
        self,
        zayavka: Mapping[str, Any],
        report: ValidationReport,
    ) -> None:
        """
        Даты и время: то, что не разбирается, в бланк попадёт как есть.

        Ошибкой это не считается: документ сохранится, но в колонке с
        числовым форматом даты текст будет выглядеть нестандартно —
        пользователю стоит об этом знать.
        """
        for key, title in self.DATE_FIELDS:
            value = self._text(zayavka.get(key))
            if value and not parse_date(value, warn=False):
                report.warnings.append(f"Не разобрана дата: {title}")

        moment = self._text(zayavka.get("loading_plan_time"))
        if moment and not is_document_time(moment):
            report.warnings.append("Время погрузки не в формате «ЧЧ:ММ»")

    # ─────────────────────────────────────────────────────────
    # Вход: путь, словарь или ContractData
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _is_path(data: Any) -> bool:
        """
        Похоже ли значение на путь к файлу.

        Строка-путь отличается от строки-текста наличием буквы диска или
        разделителя и расширения .xlsx — случайный текст файлом не сочтём.
        """
        if isinstance(data, Path):
            return True
        if not isinstance(data, str):
            return False
        return data.lower().endswith((".xlsx", ".xlsm")) and (
            ":" in data or "/" in data or "\\" in data
        )

    def _extract(self, data: Any) -> Dict[str, Any]:
        """
        Приводит вход к виду, который переживёт ``ContractData.coerce``.

        Поля схемы промпта переносятся ВНУТРЬ ``contract``: ``coerce``
        знает только свои поля, и блок ``zayavka`` без этого потерялся бы
        (см. docstring модуля). Машины остаются списком ``vehicles`` — его
        ``coerce`` понимает.

        Путь читается генератором (он же отвечает за разбор формы); файл
        может быть и битым — тогда структурная ошибка уже лежит в отчёте,
        а сюда вернётся пустая схема, и проверки данных не сработают.
        """
        if self._is_path(data):
            try:
                payload = self._generator().read_template(str(data))
            except ZayavkaTemplateError as error:
                logger.debug(
                    "Валидация заявки: файл не прочитан (%s)", type(error).__name__
                )
                payload = {"zayavka": {}, "vehicles": []}
            except Exception as error:  # noqa: BLE001 — отчёт вместо падения
                logger.debug(
                    "Валидация заявки: чтение файла не удалось (%s)",
                    type(error).__name__,
                )
                payload = {"zayavka": {}, "vehicles": []}
            return self._as_contract(payload["zayavka"], payload["vehicles"])

        if isinstance(data, ContractData):
            return self._as_contract(data.contract, data.vehicles)

        if isinstance(data, Mapping):
            nested = data.get("zayavka")
            if isinstance(nested, Mapping):
                return self._as_contract(nested, data.get("vehicles"))

            # Плоский вход: поля заявки лежат в корне или внутри contract.
            contract = data.get("contract")
            flat = {
                **(dict(contract) if isinstance(contract, Mapping) else {}),
                **data,
            }
            return self._as_contract(flat, data.get("vehicles"))

        logger.warning(
            "Валидация заявки: неожиданный вход %s — проверять нечего",
            type(data).__name__,
        )
        return self._as_contract({}, [])

    @staticmethod
    def _as_contract(zayavka: Any, vehicles: Any) -> Dict[str, Any]:
        """
        Схема промпта в форме, которую понимает ``ContractData.coerce``.

        Поля заявки уезжают в ``contract``, машины — в ``vehicles``:
        только эти имена ``coerce`` переносит в ``ContractData``.
        """
        return {
            "contract": dict(zayavka) if isinstance(zayavka, Mapping) else {},
            "vehicles": [
                dict(item) for item in (vehicles or [])
                if isinstance(item, Mapping)
            ],
        }

    @staticmethod
    def _generator() -> ZayavkaExcelGenerator:
        """Генератор для чтения формы: валидатор и он смотрят на один бланк."""
        return ZayavkaExcelGenerator()

    @staticmethod
    def _zayavka_of(cd: ContractData) -> Dict[str, Any]:
        """Блок общих сведений из ContractData (coerce кладёт его в contract)."""
        return dict(cd.contract)

    @staticmethod
    def _vehicles_of(cd: ContractData) -> List[Dict[str, Any]]:
        """Машины из ContractData."""
        return [dict(vehicle) for vehicle in cd.vehicles]

    # ─────────────────────────────────────────────────────────
    # Приведение значений
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _text(value: Any) -> str:
        """
        Строка без крайних пробелов; None — пустая строка.

        Числовой ноль остаётся «0»: пустое значение и явный ноль — разные
        вещи (правило проекта, грабли 2B.4).
        """
        return "" if value is None else str(value).strip()

    @classmethod
    def _missing(
        cls,
        source: Mapping[str, Any],
        fields: Tuple[Tuple[str, str], ...],
    ) -> List[str]:
        """Названия незаполненных полей (в порядке перечисления)."""
        return [title for key, title in fields if not cls._text(source.get(key))]

    @classmethod
    def _money(cls, value: Any) -> float:
        """
        Число из значения любого вида: «1 234,56 руб.», 1234.56, 0.

        Пустое и непонятное значение даёт 0.0 — как и в генераторе.
        """
        if value is None or isinstance(value, bool):
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)

        text = str(value).strip().lower()
        for noise in ("\u00a0", "\u202f", " ", "₽", "р.", "руб.", "руб", "%"):
            text = text.replace(noise, "")
        if not text:
            return 0.0
        text = text.replace(",", ".")
        if text.count(".") > 1:
            head, _, tail = text.rpartition(".")
            text = head.replace(".", "") + "." + tail
        try:
            return float(text)
        except ValueError:
            return 0.0


__all__ = ["ZayavkaExcelValidator"]
