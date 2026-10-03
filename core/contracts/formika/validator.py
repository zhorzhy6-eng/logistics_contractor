# -*- coding: utf-8 -*-
"""
Валидатор договора-заявки «Формика» (ЭТАП 3.1.A.4).

Проверяет обязательный минимум этого типа договора — то, без чего документ
печатать нельзя:

  * номер, дата и маршрут договора-заявки;
  * пункты погрузки и выгрузки;
  * хотя бы одна перевозимая машина, у КАЖДОЙ — VIN
    (в Формике VIN обязателен: машина без VIN в договоре бессмысленна);
  * ФИО водителя, серия и номер паспорта;
  * марка и госномер тягача, марка и госномер прицепа;
  * стоимость перевозки (одна сумма, включающая НДС).

Всё остальное (данные ВУ, адрес регистрации, телефон, тип ТС, плановое
время погрузки, «нестандартный» VIN, машин больше 12) — замечания: договор
печатается, но пользователь видит, что стоит дозаполнить.

Валидатор работает поверх BaseValidator (check_common + check_specific).
core.validator.Validator здесь НЕ вызывается: у Формики другой набор
обязательных полей (нет реквизитов сторон, КПП, банковских счетов и
расчёта НДС по ставке) — core/validator.py не трогаем.

Генератор (core/contracts/formika/generator.py) импортирует этот модуль,
поэтому обратного импорта быть не должно: расчёт суммы здесь не
повторяется, проверяется только наличие положительной суммы.
"""

import logging
import re
from typing import Any, Dict, List

from core.contract_data import ContractData
from core.contracts.base_validator import BaseValidator
from core.contracts.contract_types import ContractType
from core.validator import ValidationReport

logger = logging.getLogger("core.contracts.formika.validator")


class FormikaValidator(BaseValidator):
    """Проверки договора-заявки «Формика» перед печатью."""

    CONTRACT_TYPE = ContractType.FORMIKA.value

    #: Максимум машин в таблице бланка.
    MAX_CARS = 12

    #: VIN: 17 символов, без букв I, O, Q (стандарт ISO 3779).
    VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
    PASSPORT_SERIES_PATTERN = re.compile(r"^\d{4}$")
    PASSPORT_NUMBER_PATTERN = re.compile(r"^\d{6}$")

    #: Типы ТС из справочника машин, которые грузом не являются.
    NON_CARGO_VEHICLE_TYPES = ("Тягач", "Полуприцеп", "Прицеп")

    #: Поля суммы: любое положительное значение означает, что стоимость есть.
    PRICE_FIELDS = ("price_with_vat", "price_without_vat", "price_input")

    #: Необязательные поля водителя — их отсутствие только замечание.
    DRIVER_OPTIONAL_FIELDS = (
        ("passport_issuer", "кем выдан паспорт"),
        ("passport_issue_date", "дата выдачи паспорта"),
        ("registration_address", "адрес регистрации"),
        ("license_series", "серия ВУ"),
        ("license_number", "номер ВУ"),
        ("phone", "телефон"),
    )

    # ─────────────────────────────────────────────────────────
    # Точка расширения базового валидатора
    # ─────────────────────────────────────────────────────────

    def check_specific(self, cd: ContractData, report: ValidationReport) -> None:
        """Все проверки Формики: договор → груз → водитель → ТС → стоимость."""
        self._check_contract(cd, report)
        self._check_cargo(cd, report)
        self._check_driver(cd, report)
        self._check_tractor_trailer(cd, report)
        self._check_cost(cd, report)

    # ─────────────────────────────────────────────────────────
    # Проверки по группам
    # ─────────────────────────────────────────────────────────

    def _check_contract(self, cd: ContractData, report: ValidationReport) -> None:
        """Номер, дата, маршрут и адреса из шапки и блока 2."""
        contract = cd.contract

        if not self._text(contract.get("number")):
            report.errors.append("Не заполнен номер договора-заявки")
        if not self._text(contract.get("date")):
            report.errors.append("Не заполнена дата договора-заявки")
        if not self._text(contract.get("route")):
            report.errors.append("Не заполнен маршрут перевозки")

        if not self._loading_address(cd):
            report.errors.append("Укажите пункт погрузки")
        if not self._unloading_address(cd):
            report.errors.append("Укажите пункт выгрузки")

        if not self._text(contract.get("loading_plan_date")) and not self._loading_date(cd):
            report.warnings.append("Не указана плановая дата погрузки")
        if not self._plan_time_text(cd):
            report.warnings.append("Не указано время погрузки")

    def _check_cargo(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Перевозимые машины: минимум одна, у каждой обязателен VIN.

        Тягач и полуприцеп грузом не являются — они проверяются отдельно.
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
                    f"Машина №{number}: VIN не похож на стандартный (17 символов)"
                )

            if not self._text(vehicle.get("brand_model")):
                report.warnings.append(f"Машина №{number}: не заполнена марка/модель")

    def _check_driver(self, cd: ContractData, report: ValidationReport) -> None:
        """ФИО, серия и номер паспорта обязательны; остальное — замечания."""
        driver = cd.driver

        if not self._text(driver.get("full_name")):
            report.errors.append("Не заполнено ФИО водителя")

        series = self._digits(driver.get("passport_series"))
        if not series:
            report.errors.append("Не заполнена серия паспорта водителя")
        elif not self.PASSPORT_SERIES_PATTERN.match(series):
            report.errors.append("Серия паспорта водителя должна содержать 4 цифры")

        number = self._digits(driver.get("passport_number"))
        if not number:
            report.errors.append("Не заполнен номер паспорта водителя")
        elif not self.PASSPORT_NUMBER_PATTERN.match(number):
            report.errors.append("Номер паспорта водителя должен содержать 6 цифр")

        missing = [
            label for key, label in self.DRIVER_OPTIONAL_FIELDS
            if not self._text(driver.get(key))
        ]
        if missing:
            report.warnings.append(
                "Не заполнены данные водителя: " + ", ".join(missing)
            )

    def _check_tractor_trailer(self, cd: ContractData, report: ValidationReport) -> None:
        """Марка и госномер тягача и прицепа — обязательны."""
        for unit, title in ((cd.tractor, "тягача"), (cd.trailer, "прицепа")):
            if not self._text(unit.get("brand_model")):
                report.errors.append(f"Не заполнена марка {title}")
            if not self._text(unit.get("plate_number")):
                report.errors.append(f"Не заполнен госномер {title}")

        if not self._text(cd.tractor.get("vehicle_type")):
            report.warnings.append(
                "Не заполнен тип ТС тягача (в договоре будет пустая строка)"
            )

    def _check_cost(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Стоимость перевозки: сумма, включающая НДС, должна быть больше нуля.

        Точный расчёт делает генератор; здесь достаточно понять, есть ли
        сумма хотя бы в одном из полей (price_with_vat → price_without_vat
        → price_input).
        """
        if not any(
            self._money(cd.contract.get(key)) > 0 for key in self.PRICE_FIELDS
        ):
            report.errors.append("Стоимость перевозки должна быть больше нуля")

        if not self._text(cd.contract.get("vat_rate")) and cd.contract.get("vat_rate_num") in (None, ""):
            report.warnings.append("Не указана ставка НДС")

    # ─────────────────────────────────────────────────────────
    # Вспомогательные методы
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _cargo_vehicles(cls, cd: ContractData) -> List[Dict[str, Any]]:
        """Перевозимые машины: без тягача/прицепа и без пустых строк."""
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
    def _loading_address(cls, cd: ContractData) -> str:
        """Адрес погрузки: точка маршрута, иначе историческое поле contract."""
        if cd.loadings:
            return cls._text(cd.loadings[0].get("address"))
        return cls._text(cd.contract.get("loading_address"))

    @classmethod
    def _unloading_address(cls, cd: ContractData) -> str:
        """Адрес выгрузки: точка маршрута, иначе исторические поля contract."""
        if cd.unloadings:
            return cls._text(cd.unloadings[0].get("address"))
        return cls._text(
            cd.contract.get("unloading_address_1")
            or cd.contract.get("unloading_address")
        )

    @classmethod
    def _loading_date(cls, cd: ContractData) -> str:
        if cd.loadings:
            return cls._text(cd.loadings[0].get("date"))
        return cls._text(cd.contract.get("loading_date"))

    @classmethod
    def _plan_time_text(cls, cd: ContractData) -> str:
        """Время погрузки: явные поля contract или окно времени точки."""
        contract = cd.contract
        time_from = cls._text(contract.get("loading_plan_time_from"))
        time_to = cls._text(contract.get("loading_plan_time_to"))
        if time_from or time_to:
            return f"{time_from} {time_to}".strip()

        if cd.loadings:
            return cls._text(cd.loadings[0].get("time_window"))
        return cls._text(contract.get("loading_time_window"))

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _digits(value: Any) -> str:
        return re.sub(r"\D", "", str(value or ""))

    @staticmethod
    def _money(value: Any) -> float:
        """Сумма из значения любого вида («219 966,00» → 219966.0)."""
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)

        text = str(value).strip().replace("\u00a0", "").replace(" ", "")
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


__all__ = ["FormikaValidator"]
