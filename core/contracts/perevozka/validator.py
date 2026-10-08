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

Второе исключение — предоплата (ШАГ «Предоплата»): сумма предоплаты не
может быть отрицательной или больше стоимости услуг, а предоплата 100 %
означает оплату до оказания услуг — об этом бухгалтерию стоит спросить.
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
    #: Разница сумм, ниже которой предоплата считается равной стоимости:
    #: копейки округления не должны превращать 100 % в «меньше стоимости».
    PREPAYMENT_EPSILON = 0.01

    def check(self, data: Any) -> ValidationReport:
        """
        Полная проверка: ровно то, что делал старый Validator.check().

        После общих правил добавляются замечания этого валидатора
        (реквизиты перевозчика и предоплата — check_specific).
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
        БИК из 9 цифр), и разбивка оплаты на предоплату и остаток.
        """
        self._check_bank_requisites(cd, report)
        self._check_prepayment(cd, report)

    # ─────────────────────────────────────────────────────────
    # Предоплата
    # ─────────────────────────────────────────────────────────

    def _check_prepayment(self, cd: ContractData, report: ValidationReport) -> None:
        """
        Проверяет сумму предоплаты против стоимости услуг.

        Стоимость берётся та же, что печатается в договоре как итог, —
        `price_with_vat` (генератор считает от неё разбивку оплаты).
        Предоплата не может быть отрицательной или больше стоимости;
        предоплата в полную стоимость — замечание, а не ошибка: договор
        напечатать можно, но оплата до оказания услуг — вопрос к
        бухгалтерии, и оператор должен его увидеть.

        Ноль и незаполненное поле — предоплаты нет: проверять нечего.
        """
        contract = cd.contract if isinstance(cd.contract, dict) else {}

        try:
            prepay = float(contract.get("prepayment_amount", 0) or 0)
            total = float(contract.get("price_with_vat", 0) or 0)
        except (ValueError, TypeError):
            prepay = total = 0.0

        if prepay < 0:
            report.errors.append("Предоплата не может быть отрицательной")
            return

        if prepay <= 0 or total <= 0:
            return

        if prepay > total:
            report.errors.append(
                f"Предоплата ({prepay:.2f} ₽) больше стоимости "
                f"({total:.2f} ₽)"
            )
        elif abs(prepay - total) < self.PREPAYMENT_EPSILON:
            report.warnings.append(
                "Предоплата 100% — оплата до оказания услуг, "
                "проверьте условие с бухгалтерией"
            )

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
