#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Пакет ядра logistics_contractor.

РЕЭКСПОРТ ДЛЯ ОБРАТНОЙ СОВМЕСТИМОСТИ
------------------------------------
После рефакторинга (Этап 1, 16.10.2026) часть модулей уехала в подпапки:
  * core/utils/       — dates, fuzzy, text_normalize, address_utils;
  * core/data/        — contract_data, vat;
  * core/validation/  — validator.

Чтобы не менять полторы сотни импортов по всему проекту, этот файл
делает ДВЕ вещи:

1. РЕЭКСПОРТИРУЕТ имена из новых мест — работает `from core import parse_date`.
2. РЕГИСТРИРУЕТ СТАРЫЕ ИМЕНА МОДУЛЕЙ в `sys.modules` — работает
   `from core.dates import parse_date` и `from core import fuzzy`.

Второй пункт обязателен и не заменяется первым. `from core.dates import X` —
это импорт ПОДМОДУЛЯ `core.dates`, а не чтение атрибута пакета: Python ищет
файл `core/dates.py` и без регистрации в `sys.modules` падает с
`ModuleNotFoundError`. Проверено пробой на временном пакете (16.10.2026).

Новый код должен импортировать ИЗ НОВЫХ МЕСТ:
    from core.utils.dates import parse_date
    from core.data.contract_data import ContractData
    from core.validation.validator import Validator

Старые пути помечены как deprecated: они работают, но новый код должен идти
через подпапки.
"""

import sys as _sys

# ── Утилиты (core/utils/) ──
from core.utils import dates as _dates
from core.utils.dates import (  # noqa: F401
    DATE_FORMATS,
    day_of_month,
    month_name,
    parse_date,
    to_day_month,
    to_display,
    to_iso,
)

# ── Старые имена модулей: алиасы для `from core.<старое_имя> import ...` ──
# Ключ — имя модуля ДО переезда, значение — модуль на новом месте.
_MOVED_MODULES = {
    "dates": _dates,
}
for _old_name, _module in _MOVED_MODULES.items():
    _sys.modules.setdefault(__name__ + "." + _old_name, _module)
    setattr(_sys.modules[__name__], _old_name, _module)
del _old_name, _module

__all__ = [
    # Утилиты
    "DATE_FORMATS", "parse_date", "to_iso", "to_display", "to_day_month",
    "month_name", "day_of_month",
]
