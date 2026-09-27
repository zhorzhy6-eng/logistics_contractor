#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль валидации данных перед генерацией договора.

Шаг 2 рефакторинга архитектуры:
  * валидатор перестал быть мёртвым кодом — его вызывает MainWindow перед
    генерацией договора;
  * на вход принимается ContractData (или совместимый dict) — тот же объект,
    что уходит в генератор DOCX, поэтому проверяется ровно то, что печатается;
  * правила приведены в соответствие с реальными данными: у ИП нет КПП,
    а «заказчик по умолчанию» больше не выдумывается (см. contract_generator).

Проверки разделены на два уровня:
  * errors   — без этого договор-заявка не имеет смысла (водитель, номер,
               дата, маршрут, точки, стоимость, ТС, идентифицирующие реквизиты);
  * warnings — неполные или нестандартные реквизиты: документ напечатать можно,
               но соответствующие поля останутся пустыми.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

from core.contract_data import ContractData

logger = logging.getLogger("core.validator")


# ─────────────────────────────────────────────────────────────
# Результат проверки
# ─────────────────────────────────────────────────────────────

@dataclass
class ValidationReport:
    """Результат проверки: критические ошибки и замечания."""

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def is_clean(self) -> bool:
        return not self.errors and not self.warnings

    def format_text(self, max_items: int = 10) -> str:
        """Текст для диалога: сначала ошибки, затем замечания."""
        blocks: List[str] = []

        if self.errors:
            blocks.append(self._block("Ошибки — нужно исправить:", self.errors, max_items))
        if self.warnings:
            blocks.append(self._block("Замечания:", self.warnings, max_items))

        return "\n\n".join(blocks)

    @staticmethod
    def _block(title: str, items: Sequence[str], max_items: int) -> str:
        lines = [title]
        lines.extend(f"    • {item}" for item in items[:max_items])
        if len(items) > max_items:
            lines.append(f"    … и ещё {len(items) - max_items}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Валидатор
# ─────────────────────────────────────────────────────────────

class Validator:
    """Валидатор данных договора."""

    INN_PATTERN_10 = re.compile(r"^\d{10}$")
    INN_PATTERN_12 = re.compile(r"^\d{12}$")
    KPP_PATTERN = re.compile(r"^\d{9}$")
    OGRN_PATTERN = re.compile(r"^\d{13}$|^\d{15}$")
    BIK_PATTERN = re.compile(r"^\d{9}$")
    BANK_ACCOUNT_PATTERN = re.compile(r"^\d{20}$")
    PASSPORT_SERIES_PATTERN = re.compile(r"^\d{4}$")
    PASSPORT_NUMBER_PATTERN = re.compile(r"^\d{6}$")
    PHONE_PATTERN = re.compile(r"^[\d\s()+-]{10,20}$")
    EMAIL_PATTERN = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")
    VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

    # Поля, которые печатаются в договоре, но не блокируют генерацию
    DRIVER_OPTIONAL_FIELDS: Tuple[Tuple[str, str], ...] = (
        ("birth_date", "дата рождения"),
        ("birth_place", "место рождения"),
        ("passport_series", "серия паспорта"),
        ("passport_number", "номер паспорта"),
        ("passport_issue_date", "дата выдачи паспорта"),
        ("passport_issuer", "кем выдан паспорт"),
        ("passport_code", "код подразделения"),
        ("registration_address", "адрес регистрации"),
        ("license_series", "серия ВУ"),
        ("license_number", "номер ВУ"),
        ("license_issue_date", "дата выдачи ВУ"),
        ("license_expiry_date", "срок действия ВУ"),
        ("license_categories", "категории ВУ"),
        ("phone", "телефон"),
    )

    CARRIER_OPTIONAL_FIELDS: Tuple[Tuple[str, str], ...] = (
        ("legal_address", "юридический адрес"),
        ("actual_address", "фактический адрес"),
        ("ogrn", "ОГРН/ОГРНИП"),
        ("correspondent_account", "корреспондентский счёт"),
        ("bank_name", "наименование банка"),
        ("director_name", "ФИО руководителя"),
        ("director_position", "должность руководителя"),
        ("phone", "телефон"),
        ("email", "e-mail"),
    )

    CUSTOMER_OPTIONAL_FIELDS: Tuple[Tuple[str, str], ...] = (
        ("legal_address", "юридический адрес"),
        ("actual_address", "фактический адрес"),
        ("ogrn", "ОГРН"),
        ("bank_account", "расчётный счёт"),
        ("bik", "БИК"),
        ("bank_name", "наименование банка"),
        ("correspondent_account", "корреспондентский счёт"),
        ("director_name", "ФИО руководителя"),
        ("director_position", "должность руководителя"),
        ("phone", "телефон"),
        ("email", "e-mail"),
    )

    # ─────────────────────────────────────────────────────────
    # Публичный API
    # ─────────────────────────────────────────────────────────

    @classmethod
    def check(cls, data: Any) -> ValidationReport:
        """
        Полная проверка данных: ошибки + замечания.

        :param data: ContractData или совместимый dict
        """
        contract_data = ContractData.coerce(data)
        report = ValidationReport()

        cls._check_driver(contract_data, report)
        cls._check_organization(
            contract_data.carrier, "перевозчика", contract_data, report, is_carrier=True
        )
        cls._check_organization(
            contract_data.customer, "заказчика", contract_data, report, is_carrier=False
        )
        cls._check_contract(contract_data, report)
        cls._check_vehicles(contract_data, report)
        cls._check_tractor_trailer(contract_data, report)

        if report.has_errors:
            logger.warning(
                f"Валидация не пройдена: ошибок={len(report.errors)}, "
                f"замечаний={len(report.warnings)}"
            )
            for error in report.errors:
                logger.debug(f"  ошибка: {error}")
        else:
            logger.info(
                f"Валидация пройдена: ошибок=0, замечаний={len(report.warnings)}"
            )

        return report

    @classmethod
    def validate(cls, data: Any) -> List[str]:
        """
        Обратная совместимость: только критические ошибки.

        Использовать check(), если нужны и замечания.
        """
        return cls.check(data).errors

    # ─────────────────────────────────────────────────────────
    # Проверки по группам
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _check_driver(cls, cd: ContractData, report: ValidationReport) -> None:
        driver = cd.driver

        if not cls._text(driver.get("full_name")):
            report.errors.append("Не заполнено ФИО водителя")

        # Серия паспорта в интерфейсе хранится как «18 22», поэтому
        # сравниваем только цифры (иначе проверка всегда падала бы).
        series = cls._digits(driver.get("passport_series"))
        if series and not cls.PASSPORT_SERIES_PATTERN.match(series):
            report.errors.append("Серия паспорта водителя должна содержать 4 цифры")

        number = cls._digits(driver.get("passport_number"))
        if number and not cls.PASSPORT_NUMBER_PATTERN.match(number):
            report.errors.append("Номер паспорта водителя должен содержать 6 цифр")

        missing = cls._missing(driver, cls.DRIVER_OPTIONAL_FIELDS)
        if missing:
            report.warnings.append(
                "Не заполнены данные водителя: " + ", ".join(missing)
            )

        phone = cls._text(driver.get("phone"))
        if phone and not cls.PHONE_PATTERN.match(phone):
            report.warnings.append(f"Телефон водителя выглядит нестандартно: {phone}")

    @classmethod
    def _check_organization(
        cls,
        org: Dict[str, Any],
        role: str,
        cd: ContractData,
        report: ValidationReport,
        is_carrier: bool,
    ) -> None:
        # Перевозчик — обязательная сторона: его реквизиты печатаются как
        # исполнителя, поэтому пустые поля считаются ошибками.
        # Заказчик обычно подставляется из справочника и в их процессе
        # постоянный, поэтому его незаполненные поля — предупреждения:
        # договор напечатать можно, но пользователь увидит замечание.
        target = report.errors if is_carrier else report.warnings

        name = cls._text(org.get("full_name")) or cls._text(org.get("short_name"))
        inn = cls._digits(org.get("inn"))

        if not name:
            target.append(f"Не заполнено наименование {role}")

        if not inn:
            target.append(f"Не заполнен ИНН {role}")
        elif not (cls.INN_PATTERN_10.match(inn) or cls.INN_PATTERN_12.match(inn)):
            target.append(f"ИНН {role} должен содержать 10 или 12 цифр")

        # КПП: у ИП его не существует — проверка пропускается.
        # (Раньше валидатор требовал КПП у всех, из-за чего был непригоден.)
        is_ip = cls._is_ip(org, cd.contract if is_carrier else {})
        if is_ip:
            logger.debug(f"{role}: правовая форма ИП — КПП не проверяется")
        else:
            kpp = cls._digits(org.get("kpp"))
            if not kpp:
                target.append(f"Не заполнен КПП {role}")
            elif not cls.KPP_PATTERN.match(kpp):
                target.append(f"КПП {role} должен содержать 9 цифр")

        ogrn = cls._digits(org.get("ogrn"))
        if ogrn and not cls.OGRN_PATTERN.match(ogrn):
            target.append(f"ОГРН {role} должен содержать 13 или 15 цифр")

        if is_carrier:
            # Реквизиты для оплаты — обязательны
            cls._require_digits(org, "bank_account", 20, f"расчётный счёт {role}", report)
            cls._require_digits(org, "bik", 9, f"БИК {role}", report)
            optional_fields = cls.CARRIER_OPTIONAL_FIELDS
        else:
            # У заказчика реквизиты обычно подтягиваются из справочника:
            # неверный формат — ошибка, пустое поле — замечание.
            for key, length, label in (
                ("bank_account", 20, f"расчётный счёт {role}"),
                ("bik", 9, f"БИК {role}"),
            ):
                digits = cls._digits(org.get(key))
                if digits and len(digits) != length:
                    report.errors.append(f"{label.capitalize()}: должно быть {length} цифр")
            optional_fields = cls.CUSTOMER_OPTIONAL_FIELDS

        # Если организация не заполнена вовсе — одна понятная строка
        # вместо перечисления десятка пустых полей.
        if not name and not inn:
            report.warnings.append(f"Реквизиты {role} не заполнены")
        else:
            missing = cls._missing(org, optional_fields)
            if missing:
                report.warnings.append(
                    f"Не заполнены реквизиты {role}: " + ", ".join(missing)
                )

        phone = cls._text(org.get("phone"))
        if phone and not cls.PHONE_PATTERN.match(phone):
            report.warnings.append(f"Телефон {role} выглядит нестандартно: {phone}")

        email = cls._text(org.get("email"))
        if email and not cls.EMAIL_PATTERN.match(email):
            report.warnings.append(f"E-mail {role} выглядит нестандартно: {email}")

    @classmethod
    def _check_contract(cls, cd: ContractData, report: ValidationReport) -> None:
        contract = cd.contract

        if not cls._text(contract.get("number")):
            report.errors.append("Не заполнен номер договора")
        if not cls._text(contract.get("date")):
            report.errors.append("Не заполнена дата договора")
        if not cls._text(contract.get("route")):
            report.errors.append("Не заполнен маршрут перевозки")

        if not cd.loadings:
            report.errors.append("Укажите хотя бы одно место погрузки")
        if not cd.unloadings:
            report.errors.append("Укажите хотя бы одно место выгрузки")

        try:
            price = float(contract.get("price_without_vat", 0) or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            report.errors.append("Стоимость должна быть больше нуля")

        try:
            payment_days = int(contract.get("payment_days", 0) or 0)
            if payment_days <= 0:
                report.warnings.append("Срок оплаты не указан")
        except (TypeError, ValueError):
            report.warnings.append("Срок оплаты указан не числом")

    @classmethod
    def _check_vehicles(cls, cd: ContractData, report: ValidationReport) -> None:
        if not cd.vehicles:
            report.errors.append("Добавьте хотя бы одно транспортное средство")
            return

        for i, vehicle in enumerate(cd.vehicles, 1):
            missing = []
            if not cls._text(vehicle.get("vin")):
                missing.append("VIN")
            if not cls._text(vehicle.get("brand_model")):
                missing.append("марка/модель")
            if not cls._text(vehicle.get("plate_number")):
                missing.append("госномер")
            if missing:
                report.warnings.append(
                    f"ТС №{i}: не заполнено — " + ", ".join(missing)
                )

            vin = cls._text(vehicle.get("vin")).upper()
            if vin and not cls.VIN_PATTERN.match(vin):
                report.warnings.append(f"ТС №{i}: VIN не похож на стандартный (17 символов)")

    @classmethod
    def _check_tractor_trailer(cls, cd: ContractData, report: ValidationReport) -> None:
        tractor_filled = bool(
            cls._text(cd.tractor.get("plate_number")) or cls._text(cd.tractor.get("brand_model"))
        )
        trailer_filled = bool(
            cls._text(cd.trailer.get("plate_number")) or cls._text(cd.trailer.get("brand_model"))
        )

        if not tractor_filled:
            report.warnings.append("Не заполнены данные тягача")
        if not trailer_filled:
            report.warnings.append("Не заполнены данные полуприцепа")

    # ─────────────────────────────────────────────────────────
    # Вспомогательные методы
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _digits(value: Any) -> str:
        return re.sub(r"\D", "", str(value or ""))

    @classmethod
    def _missing(
        cls,
        source: Dict[str, Any],
        fields: Sequence[Tuple[str, str]],
    ) -> List[str]:
        """Названия незаполненных полей (в порядке перечисления)."""
        return [label for key, label in fields if not cls._text(source.get(key))]

    @classmethod
    def _require_digits(
        cls,
        org: Dict[str, Any],
        key: str,
        length: int,
        label: str,
        report: ValidationReport,
    ) -> None:
        digits = cls._digits(org.get(key))
        if not digits:
            report.errors.append(f"Не заполнен {label}")
        elif len(digits) != length:
            report.errors.append(f"{label.capitalize()}: должно быть {length} цифр")

    @staticmethod
    def _is_ip(org: Dict[str, Any], contract: Dict[str, Any]) -> bool:
        """
        Определяет, что организация — ИП.

        Смотрим: явный entity_type → наименование → тип перевозчика
        из вкладки «Договор» (contract.carrier_type).
        """
        entity = str(org.get("entity_type", "") or "").strip().upper()
        if entity.startswith("ИП"):
            return True

        full_name = str(org.get("full_name", "") or "").strip().lower()
        if full_name.startswith("ип ") or full_name.startswith("ип.") \
                or "индивидуальный предприниматель" in full_name:
            return True

        carrier_type = str((contract or {}).get("carrier_type", "") or "")
        return "ИП" in carrier_type
