#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shim обратной совместимости (Шаг 3 рефакторинга архитектуры контрактов).

Исторический импорт «from core.contract_generator import ContractGenerator»
продолжает работать без изменений: ContractGenerator — псевдоним
PerevozkaGenerator из core/contracts/perevozka/generator.py. Логики здесь
нет, старый код перенесён в пакет core/contracts (коммиты шагов 1–2),
исходник сохранён в backup/contract_generator_before_contracts.py.

Побочный эффект импорта: пакет core.contracts.perevozka при первом импорте
регистрирует тип «perevozka» в реестре (см. core/contracts/perevozka/__init__.py).
"""

from core.contracts.perevozka.generator import PerevozkaGenerator
from core.contracts.perevozka.validator import PerevozkaValidator

#: Историческое имя класса — старые импорты, тесты и demo-скрипты
#: используют именно его.
ContractGenerator = PerevozkaGenerator

__all__ = ["ContractGenerator", "PerevozkaGenerator", "PerevozkaValidator"]
