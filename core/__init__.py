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
from core.utils import fuzzy as _fuzzy
from core.utils.fuzzy import (  # noqa: F401
    MAX_CASE_VARIANTS,
    MAX_PATTERNS,
    MAX_VARIANTS,
    SEARCH_LEVELS,
    case_variants,
    collapse_doubles,
    e_yo_variants,
    level_condition,
    level_conditions,
    normalize_term,
    search_levels,
    spelling_variants,
    stem,
    words,
)
from core.utils import text_normalize as _text_normalize
from core.utils.text_normalize import (  # noqa: F401
    ABBREVIATION_MAX_LENGTH,
    SERVICE_WORDS,
    SHORT_VALUE_MAX_LENGTH,
    is_abbreviation,
    is_all_caps,
    normalize_caps_phrase,
    normalize_organization_fields,
    normalize_position,
)
from core.utils import address_utils as _address_utils
from core.utils.address_utils import (  # noqa: F401
    city_for_document,
    clean_address_from_index,
    extract_city,
)

# ── Данные и правила (core/data/) ──
from core.data import contract_data as _contract_data
from core.data.contract_data import ContractData  # noqa: F401
from core.data import vat as _vat
from core.data.vat import (  # noqa: F401
    DEFAULT_ENTITY_TYPE,
    DEFAULT_VAT_RATE,
    ENTITY_TYPES,
    VAT_FREE,
    VAT_RATES,
    base_from_total,
    compute_carrier_type,
    compute_vat,
    is_vat_free,
    normalize_entity_type,
    normalize_vat_rate,
    split_carrier_type,
    total_from_base,
    vat_factor,
    vat_rate_label,
    vat_rate_number,
)

# ── Валидация (core/validation/) ──
from core.validation import validator as _validator
from core.validation.validator import (  # noqa: F401
    ValidationReport,
    Validator,
)

# ── Старые имена модулей: алиасы для `from core.<старое_имя> import ...` ──
# Ключ — имя модуля ДО переезда, значение — модуль на новом месте.
_MOVED_MODULES = {
    "dates": _dates,
    "fuzzy": _fuzzy,
    "text_normalize": _text_normalize,
    "address_utils": _address_utils,
    "contract_data": _contract_data,
    "vat": _vat,
    "validator": _validator,
}
for _old_name, _module in _MOVED_MODULES.items():
    _sys.modules.setdefault(__name__ + "." + _old_name, _module)
    setattr(_sys.modules[__name__], _old_name, _module)
del _old_name, _module

__all__ = [
    # Утилиты
    "DATE_FORMATS", "parse_date", "to_iso", "to_display", "to_day_month",
    "month_name", "day_of_month",
    "SEARCH_LEVELS", "normalize_term", "words", "stem",
    "collapse_doubles", "spelling_variants", "level_condition",
    "level_conditions", "search_levels",
    "case_variants", "e_yo_variants",
    "MAX_VARIANTS", "MAX_PATTERNS", "MAX_CASE_VARIANTS",
    "ABBREVIATION_MAX_LENGTH", "SHORT_VALUE_MAX_LENGTH", "SERVICE_WORDS",
    "is_abbreviation", "is_all_caps", "normalize_caps_phrase",
    "normalize_organization_fields", "normalize_position",
    "city_for_document", "clean_address_from_index", "extract_city",
    # Данные
    "ContractData",
    "VAT_FREE", "VAT_RATES", "DEFAULT_VAT_RATE", "ENTITY_TYPES",
    "DEFAULT_ENTITY_TYPE",
    "is_vat_free", "vat_rate_number", "vat_rate_label",
    "normalize_vat_rate", "vat_factor", "base_from_total",
    "total_from_base", "compute_vat", "compute_carrier_type",
    "split_carrier_type", "normalize_entity_type",
    # Валидация
    "Validator", "ValidationReport",
]
