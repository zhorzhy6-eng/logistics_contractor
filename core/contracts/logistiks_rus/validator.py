# -*- coding: utf-8 -*-
"""
Валидатор заявки «Логистикс Рус» (ЭТАП 3.1.C.A.5).

Проверяет обязательный минимум этого типа договора: номер, дату, заказчика,
хотя бы одного грузоотправителя и грузополучателя с адресом, машины с VIN,
тягач и прицеп, ФИО водителя, стоимость. Различает ООО и ИП по
contract.carrier_type.

Чем этот тип отличается от Формики (core/contracts/formika/validator.py):

  * стороны заявки фиксированы — экспедитора печатает бланк, поэтому из
    реквизитов проверяется только наименование заказчика;
  * грузоотправители и грузополучатели — это точки маршрута: разделы 1 и 2
    бланка заполняются из loadings / unloadings (у каждой точки есть имя и
    адрес, см. core/contracts/logistiks_rus/generator.py);
  * у водителя печатается одно ФИО: паспорт, ВУ и телефон в бланке
    отсутствуют, поэтому их отсутствие не проверяется даже замечанием;
  * раздел 5 «Стоимость» зависит от типа экспедитора: у ООО три суммы
    (без НДС + НДС по ставке + итого), у ИП одна сумма «Без НДС».

Валидатор работает поверх BaseValidator (check_common + check_specific).
core.validator.Validator здесь НЕ вызывается: у Логистикс Рус другой набор
обязательных полей (нет реквизитов сторон, КПП, банковских счетов и
паспорта водителя) — core/validator.py не трогаем.

Генератор (core/contracts/logistiks_rus/generator.py) импортирует этот
модуль, поэтому обратного импорта быть не должно: расчёт сумм здесь не
повторяется, проверяется только наличие положительной суммы.
"""

import logging
import re
from typing import Any, Dict, List, Mapping, Tuple

from core.contract_data import ContractData
from core.contracts.base_validator import BaseValidator
from core.contracts.contract_types import ContractType
from core.validator import ValidationReport

logger = logging.getLogger("core.contracts.logistiks_rus.validator")


class LogistiksRusValidator(BaseValidator):
    """Проверки заявки «Логистикс Рус» перед печатью."""

    CONTRACT_TYPE = ContractType.LOGISTIKS_RUS.value

    #: Максимум машин в таблице бланка (раздел 3).
    MAX_CARS = 12

    #: VIN: 17 символов, без букв I, O, Q (стандарт ISO 3779).
    VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

    #: Типы ТС из справочника машин, которые грузом не являются: тягач и
    #: прицеп описаны отдельными строками бланка (раздел 4).
    NON_CARGO_VEHICLE_TYPES = ("Тягач", "Полуприцеп", "Прицеп")

    #: Поля суммы: любое положительное значение означает, что стоимость есть.
    #: price_without_vat — поле вкладки «Стоимость», price_input — сумма из
    #: распознанного документа, sum_wo_vat / sum_total — распознанные суммы
    #: раздела 5 (у ИП единственная сумма лежит в sum_total).
    PRICE_FIELDS = (
        "price_without_vat",
        "price_input",
        "sum_wo_vat",
        "sum_total",
        "sum_vat",
    )

    #: Необязательные поля водителя. У этого типа их нет: в бланке печатается
    #: только ФИО (константа — для единообразия с FormikaValidator).
    DRIVER_OPTIONAL_FIELDS: Tuple[Tuple[str, str], ...] = ()

    # ─────────────────────────────────────────────────────────
    # Точка расширения базового валидатора
    # ─────────────────────────────────────────────────────────

    def check_specific(self, cd: ContractData, report: ValidationReport) -> None:
        """Все проверки заявки: заявка → заказчик → точки → груз → ТС → стоимость."""
        self._check_contract(cd, report)
        self._check_customer(cd, report)
        self._check_shippers(cd, report)
        self._check_consignees(cd, report)
        self._check_cargo(cd, report)
        self._check_cargo_count(cd, report)
        self._check_tractor_trailer(cd, report)
        self._check_driver(cd, report)
        self._check_cost(cd, report)

    # ─────────────────────────────────────────────────────────
    # Проверки по группам
    # ─────────────────────────────────────────────────────────

    def _check_contract(self, cd: ContractData, report: ValidationReport) -> None:
        """Номер и дата заявки."""
        contract = cd.contract

        if not self._text(contract.get("number")):
            report.errors.append("Не заполнен номер заявки")
        if not self._text(contract.get("date")):
            report.errors.append("Не заполнена дата заявки")

    def _check_customer(self, cd: ContractData, report: ValidationReport) -> None:
        """Наименование заказчика — обязательно, краткое — замечание."""
        customer = cd.customer

        if not self._text(customer.get("full_name")):
            report.errors.append("Не заполнено наименование заказчика")
        if not self._text(customer.get("short_name")):
            report.warnings.append("Не заполнено краткое наименование заказчика")

    def _check_shippers(self, cd: ContractData, report: ValidationReport) -> None:
        """Раздел 1: минимум один грузоотправитель с адресом погрузки."""
        self._check_points(
            report,
            self._shipper_points(cd),
            "Укажите хотя бы одного грузоотправителя с адресом",
            "грузоотправителя",
            "Грузоотправители",
        )

    def _check_consignees(self, cd: ContractData, report: ValidationReport) -> None:
        """Раздел 2: минимум один грузополучатель с адресом выгрузки."""
        self._check_points(
            report,
            self._consignee_points(cd),
            "Укажите хотя бы одного грузополучателя с адресом",
            "грузополучателя",
            "Грузополучатели",
        )

    @classmethod
    def _shipper_points(cls, cd: ContractData) -> List[Dict[str, Any]]:
        """Заполненные грузоотправители: точки погрузки (loadings/shippers)."""
        return cls._points_with_names(
            cd.loadings,
            cd.contract.get("loadings"),
            cd.contract.get("shippers"),
        )

    @classmethod
    def _consignee_points(cls, cd: ContractData) -> List[Dict[str, Any]]:
        """Заполненные грузополучатели: точки выгрузки (unloadings/consignees)."""
        return cls._points_with_names(
            cd.unloadings,
            cd.contract.get("unloadings"),
            cd.contract.get("consignees"),
        )

    def _check_cargo(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Раздел 3: перевозимые машины — минимум одна, у каждой обязателен VIN.

        Тягач и прицеп грузом не являются — они проверяются отдельно
        (раздел 4 бланка).
        """
        cargo = self._cargo_vehicles(cd)
        if not cargo:
            report.errors.append("Добавьте хотя бы одну перевозимую машину")
            return

        if len(cargo) > self.MAX_CARS:
            report.warnings.append(
                f"Машин {len(cargo)} — в бланк помещается {self.MAX_CARS}, "
                f"лишние в заявку не попадут"
            )

        for number, vehicle in enumerate(cargo, 1):
            vin = self._text(vehicle.get("vin")).upper()
            if not vin:
                report.errors.append(f"Машина №{number}: не заполнен VIN")
            elif not self.VIN_PATTERN.match(vin):
                report.warnings.append(
                    f"Машина №{number}: VIN не похож на стандартный (17 символов)"
                )

    def _check_tractor_trailer(
        self, cd: ContractData, report: ValidationReport
    ) -> None:
        """Раздел 4: марка и госномер автовоза — тягача и прицепа."""
        for unit, title in ((cd.tractor, "тягача"), (cd.trailer, "прицепа")):
            if not self._text(unit.get("brand_model")):
                report.errors.append(f"Не заполнена марка {title}")
            if not self._text(unit.get("plate_number")):
                report.errors.append(f"Не заполнен госномер {title}")

    def _check_cargo_count(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Раздел 3: число машин в бланке против фактического списка машин.

        cargo_count приходит из распознавания («Общее количество: … шт.») и в
        бланк печатается как есть — если он расходится с таблицей, в заявке
        будут две разные цифры. Это замечание: печатать документ можно.
        """
        raw = cd.contract.get("cargo_count")
        if not self._text(raw):
            return

        declared = self._count(raw)
        actual = len(self._cargo_vehicles(cd))
        if declared != actual:
            report.warnings.append(
                f"cargo_count={declared}, а перевозимых машин в таблице — "
                f"{actual} (в бланк попадут обе цифры)"
            )

    def _check_driver(self, cd: ContractData, report: ValidationReport) -> None:
        """Раздел 4: в бланке печатается только ФИО водителя."""
        if not self._text(cd.driver.get("full_name")):
            report.errors.append("Не заполнено ФИО водителя")

    def _check_cost(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Раздел 5 «Стоимость» с учётом типа экспедитора.

        Сумма должна быть больше нуля хотя бы в одном из полей (см.
        PRICE_FIELDS). Ставка НДС проверяется только у ООО: у ИП стоимость
        всегда без НДС, и ненулевая ставка — повод показать замечание.

        Точный расчёт сумм делает генератор; здесь достаточно понять, есть ли
        сумма и не забыта ли ставка.
        """
        if not self._has_price(cd):
            report.errors.append("Стоимость услуг должна быть больше нуля")

        if self._is_ip(cd):
            self._check_cost_ip(cd, report)
        else:
            self._check_cost_ooo(cd, report)

    def _check_cost_ooo(self, cd: ContractData, report: ValidationReport) -> None:
        """ООО: ставка НДС нужна для расчёта НДС и итоговой суммы."""
        raw_rate = cd.contract.get("vat_rate_num")

        if raw_rate is None or self._text(raw_rate) == "":
            # Ставка может прийти строкой «22%» в поле vat_rate.
            if not self._text(cd.contract.get("vat_rate")):
                report.warnings.append("Не указана ставка НДС")
            return

        if self._empty_to_float(raw_rate) == 0:
            report.warnings.append("Ставка НДС = 0%, проверьте")

    def _check_cost_ip(self, cd: ContractData, report: ValidationReport) -> None:
        """ИП: ставка НДС не применяется — стоимость всегда без НДС."""
        if self._empty_to_float(cd.contract.get("vat_rate_num")) > 0:
            report.warnings.append("У ИП ставка НДС не применяется")

    # ─────────────────────────────────────────────────────────
    # Вспомогательные методы
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _check_points(
        cls,
        report: ValidationReport,
        points: List[Dict[str, Any]],
        required_message: str,
        title: str,
        section: str,
    ) -> None:
        """
        Общая проверка списка точек: адрес минимум у одной, имя и адрес —
        либо вместе, либо ни одного (половина блока в бланке выглядит
        недозаполненной, но печатать её можно).

        Замечание о пустом наименовании выдаётся, если у точки есть поле
        наименования (см. _point_name_is_known): оно подтягивается из
        справочника салонов, и пустое значение означает «не заполнено».

        Если наименования нет НИ У ОДНОЙ точки раздела, вместо перечисления
        по каждой точке печатается ОДНО общее замечание: пользователь,
        который справочником не пользуется, не получает строку на каждую
        точку. Как только наименование есть хотя бы у одной — замечания идут
        по каждой незаполненной точке: там это сигнал, что салон не подтянулся.
        """
        if not any(cls._point_address(point) for point in points):
            report.errors.append(required_message)

        unnamed: List[int] = []
        for number, point in enumerate(points, 1):
            name = cls._point_name(point)
            address = cls._point_address(point)
            if name and not address:
                report.warnings.append(f"{number}-й {title}: не указан адрес")
            elif address and not name and cls._point_name_is_known(point):
                unnamed.append(number)

        if not unnamed:
            return

        if len(unnamed) == len(points):
            report.warnings.append(
                f"{cls._case_title(section)}: не указаны наименования — "
                f"выберите точки из справочника салонов"
            )
            return

        for number in unnamed:
            report.warnings.append(f"{number}-й {title}: не указано наименование")

    @staticmethod
    def _case_title(title: str) -> str:
        """Название раздела с заглавной буквы: «грузоотправителя» → «Грузоотправителя»."""
        return title[:1].upper() + title[1:] if title else title

    @classmethod
    def _points_with_names(cls, *sources: Any) -> List[Dict[str, Any]]:
        """
        Точки маршрута вместе с наименованиями грузоотправителя/грузополучателя.

        Источники перебираются по порядку (приведённые точки ContractData,
        затем вложенные в contract списки), берётся первый непустой — как в
        core.contract_data.coerce.

        Наименования доливаются из исходных списков, если у приведённой точки
        своего имени нет: так делает и генератор
        (LogistiksRusGenerator._points_with_names). После ШАГА FIX-2.5
        ContractData имя точки сохраняет (core.contract_data._as_point_list),
        но запасной путь остаётся: он работает и на данных, где имя лежит
        только во вложенном списке contract.

        Если наименований в данных нет вовсе (ключа name нет ни в одной
        записи), проверка наименования не навязывается: значение считается
        неизвестным, а не пустым.
        """
        for source in sources:
            points = cls._point_list(source)
            if not points:
                continue

            names = cls._raw_point_names(*sources)
            result: List[Dict[str, Any]] = []

            for index, point in enumerate(points):
                item = dict(point)
                if not cls._text(item.get("name")) and index < len(names):
                    item["name"] = names[index]
                result.append(item)

            return result

        return []

    @classmethod
    def _raw_point_names(cls, *sources: Any) -> List[Any]:
        """
        Значения name из первого непустого источника.

        None (ключа name в записи нет) означает «наименование неизвестно»:
        такой источник дальше не рассматривается, а проверка наименования по
        этой точке молчит.
        """
        for source in sources:
            if not isinstance(source, (list, tuple)):
                continue

            names = [
                item.get("name") if isinstance(item, Mapping) else None
                for item in source
            ]
            if any(name is not None for name in names):
                return names

        return []

    @classmethod
    def _point_list(cls, source: Any) -> List[Dict[str, Any]]:
        """
        Список точек, где заполнено хотя бы одно значение.

        Пустая запись — строка-пустышка из таблицы (все поля пустые) —
        точкой не считается.
        """
        if not isinstance(source, (list, tuple)):
            return []

        result: List[Dict[str, Any]] = []
        for item in source:
            if not isinstance(item, Mapping):
                continue
            if any(cls._text(value) for value in item.values()):
                result.append(dict(item))
        return result

    @classmethod
    def _point_name(cls, point: Mapping[str, Any]) -> str:
        """Наименование грузоотправителя/грузополучателя в точке."""
        return cls._text(
            point.get("name")
            or point.get("shipper_name")
            or point.get("consignee_name")
        )

    @classmethod
    def _point_name_is_known(cls, point: Mapping[str, Any]) -> bool:
        """
        Есть ли у точки поле наименования — то есть можно ли о нём спросить.

        Ключ name (или его синонимы shipper_name / consignee_name) есть —
        наименование в этих данных предусмотрено, и пустое значение означает
        «не заполнено»: о нём и сообщается. Ключа нет вовсе — наименование
        в этих данных не предусмотрено, и замечание было бы навязанным.

        После ШАГА FIX-2.5 приведённые точки ContractData несут ключ name
        всегда (пустой строкой), поэтому наименование проверяется и у
        распознанных точек, где справочник салонов не сработал.
        """
        return (
            "name" in point
            or "shipper_name" in point
            or "consignee_name" in point
        )

    @classmethod
    def _point_address(cls, point: Mapping[str, Any]) -> str:
        """Адрес точки: address, иначе исторические адреса из contract."""
        return cls._text(
            point.get("address") or point.get("loading_address")
            or point.get("unloading_address")
        )

    @classmethod
    def _cargo_vehicles(cls, cd: ContractData) -> List[Dict[str, Any]]:
        """Перевозимые машины: без тягача/прицепа и без полностью пустых строк."""
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
    def _has_price(cls, cd: ContractData) -> bool:
        """Есть ли положительная сумма хотя бы в одном поле стоимости."""
        return any(cls._money(cd.contract.get(key)) > 0 for key in cls.PRICE_FIELDS)

    @classmethod
    def _count(cls, value: Any) -> int:
        """
        Количество машин из значения любого вида («3», 3, «4 шт.» → 4).

        Непонятное значение даёт 0: нераспознанное число не должно выглядеть
        как совпадение с фактическим количеством машин.
        """
        text = cls._text(value).replace(",", ".")
        match = re.search(r"\d+(?:\.\d+)?", text)
        if not match:
            return 0
        return int(round(float(match.group())))

    @classmethod
    def _is_ip(cls, cd: ContractData) -> bool:
        """
        Тип экспедитора — ИП?

        Источник — contract.carrier_type, затем carrier.carrier_type, как в
        генераторе (BaseContractGenerator._carrier_type_of). Пустое значение
        трактуется как ООО: этот вариант бланка и расчёта по умолчанию.
        """
        carrier_type = cd.contract.get("carrier_type") or cd.carrier.get("carrier_type")
        return "ИП" in cls._text(carrier_type)

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
    def _empty_to_float(value: Any) -> float:
        """Число из значения любого вида; пустое и непонятное — 0.0."""
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)

        text = (
            str(value)
            .strip()
            .replace("%", "")
            .replace("\u00a0", "")
            .replace(" ", "")
        )
        if not text:
            return 0.0
        text = text.replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return 0.0

    @classmethod
    def _money(cls, value: Any) -> float:
        """Сумма из значения любого вида («221 099,18» → 221099.18)."""
        return cls._empty_to_float(value)


__all__ = ["LogistiksRusValidator"]
