# -*- coding: utf-8 -*-
"""
Валидатор договора-заявки на перевозку (Шаг 2 рефакторинга архитектуры).

Делегирует существующему core.validator.Validator 1:1: поведение UI
(MainWindow._on_create_contract) и tests/test_validator.py не меняются.
Перенос общих правил в BaseValidator — отдельная поздняя задача,
core/validator.py не трогаем.

Одно исключение — банковские реквизиты перевозчика (ШАГ FIX-6, часть A3):
у корреспондентского счёта не было проверки вообще, поэтому «21 цифра»
в нём никого не смущало. Проверка живёт здесь, в check_specific, и даёт
ЗАМЕЧАНИЕ, а не ошибку.
"""

import re
from typing import Any, List

from core.contract_data import ContractData
from core.contracts.base_validator import BaseValidator
from core.contracts.contract_types import ContractType
from core.validator import ValidationReport, Validator


class PerevozkaValidator(BaseValidator):
    """Валидатор договора-заявки на перевозку — обёртка над Validator."""

    CONTRACT_TYPE = ContractType.PEREVOZKA.value

    #: Длина корреспондентского счёта (всегда ровно 20 цифр).
    CORR_ACCOUNT_LENGTH = 20
    #: Длина БИК (всегда ровно 9 цифр).
    BIK_LENGTH = 9

    def check(self, data: Any) -> ValidationReport:
        """
        Полная проверка: ровно то, что делал старый Validator.check().

        После общих правил добавляются замечания этого валидатора
        (реквизиты перевозчика — check_specific).
        """
        report = Validator.check(data)
        self.check_specific(ContractData.coerce(data), report)
        return report

    def validate(self, data: Any) -> List[str]:
        """Только критические ошибки (совместимость с Validator.validate)."""
        return Validator.validate(data)

    def check_specific(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Специфика типа, которой нет в core.validator.Validator.

        Общие правила (ИНН, КПП, ОГРН, расчётный счёт, БИК перевозчика)
        остаются там и не дублируются. Здесь только то, чего там нет:
        длина корреспондентского счёта и длины, перепроверенные в цифрах
        (общий валидатор смотрит на «грязное» значение целиком, поэтому
        «Корреспондентский счет БИК 044030786» он считает мусором, а не
        БИК из 9 цифр).
        """
        self._check_bank_requisites(cd, report)

    # ─────────────────────────────────────────────────────────
    # Банковские реквизиты перевозчика
    # ─────────────────────────────────────────────────────────

    def _check_bank_requisites(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Длины БИК и корреспондентского счёта перевозчика — замечания.

        Ошибкой это не считается: договор напечатать можно, и реквизиты
        печатаются как есть. Сообщаем один раз на поле, без повторов по
        точкам и без самих значений — только длины (логи и диалог без ПДн).
        """
        carrier = cd.carrier
        if not isinstance(carrier, dict):
            return

        for field, length, label in (
            ("bik", self.BIK_LENGTH, "БИК перевозчика"),
            ("correspondent_account", self.CORR_ACCOUNT_LENGTH,
             "Корреспондентский счёт перевозчика"),
        ):
            raw = str(carrier.get(field) or "").strip()
            if not raw:
                continue

            digits = re.sub(r"\D", "", raw)

            if not digits:
                report.warnings.append(
                    f"{label}: цифр не найдено — проверьте реквизит"
                )
                continue

            if len(digits) != length:
                report.warnings.append(
                    f"{label}: должно быть {length} цифр, а не {len(digits)}"
                )
                continue

            if digits != raw:
                # Цифр ровно столько, сколько нужно, но в поле есть лишнее:
                # в договор попадёт только число, о мусоре сообщаем.
                report.warnings.append(
                    f"{label}: в поле есть лишние символы, "
                    f"в договор пойдут только цифры"
                )
