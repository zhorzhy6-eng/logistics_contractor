#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Пакет ядра logistics_contractor.

РЕЭКСПОРТ ДЛЯ ОБРАТНОЙ СОВМЕСТИМОСТИ
------------------------------------
После рефакторинга часть модулей уехала из корня `core/`:

Этап 1 (16.10.2026):
  * core/utils/       — dates, fuzzy, text_normalize, address_utils;
  * core/data/        — contract_data, vat;
  * core/validation/  — validator.

Этап 2 (16.10.2026) — слой распознавания документов:
  * services/recognition/reader.py          (был core/document_reader.py);
  * services/recognition/ocr.py             (был core/document_ocr.py);
  * services/recognition/fields.py          (был core/document_fields.py);
  * services/recognition/recognizer.py      (был core/recognizer.py);
  * services/recognition/import_service.py  (был
    core/document_import_service.py).

Чтобы не менять импорты по всему проекту (их 122: 87 в публичной части,
35 в приватной и в тестах), этот файл делает ДВЕ вещи:

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
    from services.recognition.reader import read_document

Старые пути помечены как deprecated: они работают, но новый код должен идти
через подпапки (ядро) и через `services/` (слой распознавания).

ЧЕГО ЗДЕСЬ НЕТ
--------------
`ru_morphology` в Этап 1 не переезжал: файла `core/ru_morphology.py` в
проекте нет, модуль живёт в `core/contracts/ru_morphology.py` (пакет типов
договоров), и его импортируют генератор Экспедиторства и четыре набора
тестов. Переезд этого модуля — отдельное решение (см. `docs/STATE.md`).

`core/import_cancel.py` и `core/trace.py` остались в корне: их Этап 2
не трогал, и `services/recognition/*` импортирует их по старому пути.

Проверка совместимости — `tests/test_core_layout.py` и
`tests/test_services_layout.py`.
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
# Регистрируются ДВА имени на каждый модуль:
#   * `sys.modules["core.dates"]` — чтобы работал импорт ПОДМОДУЛЯ
#     (`from core.dates import parse_date`, `import core.dates`);
#   * атрибут пакета `core.dates` — чтобы работало `core.dates.parse_date`.
# Объект модуля ОДИН и тот же: это не копия и не обёртка, поэтому
# `isinstance`, пикинг и сравнение классов ведут себя как раньше.
# `setdefault`, а не присваивание: если старый файл когда-нибудь вернётся
# (или его подложит приватная часть), живой модуль не будет затёрт.
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

# ── Распознавание документов (services/recognition/) ──
# Модули переехали в services/recognition/ (Этап 2, 16.10.2026). Ничего
# не реэкспортируется: имена у них длинные и в корне `core` не нужны.
# Регистрируются ТОЛЬКО старые имена модулей — ровно так же, как выше,
# ради `from core.document_reader import read_document`, который читает
# МОДУЛЬ из `sys.modules`, а не атрибут пакета. Объект модуля при этом
# ОДИН: `core.recognizer is services.recognition.recognizer`.
#
# ПОРЯДОК ИМПОРТОВ ЗДЕСЬ — ЧАСТЬ РЕШЕНИЯ, а не вкусовщина:
# reader и fields не зависят ни от кого, recognizer зависит от ядра
# (`core.dates`, `core.trace` — уже загружены выше), а import_service
# объявляет `SCHEMA` через `DataMapper`, то есть recognizer ему нужен
# ГОТОВЫМ. Нарушение порядка даёт `ImportError` на частичном модуле.
#
# ЦИКЛ, КОТОРЫЙ ЗДЕСЬ БЫЛ. Первый заход в Этапе 2 использовал статический
# импорт при обычных импортах в `import_service`, и это ломало ОБРАТНЫЙ
# порядок — проверено пробой `tools/check_stage2_aliases.py`, режим `new`:
#
#   from services.recognition.reader import read_document   # первым
#     → reader.py: from core.import_cancel import ...       # пакет `core`
#       → core/__init__.py: import services...import_service
#         → import_service.py: from services.recognition.reader import ...
#           → reader ещё не догружен → ImportError (частичный модуль)
#
# Разорван цикл не здесь, а в `import_service.py`: `read_document` и
# `DataMapper` берутся там лениво (`__getattr__` в конце файла). Так
# работает ЛЮБОЙ порядок импорта, и ни один модуль не грузится дважды.
from services.recognition import reader as _reader
from services.recognition import fields as _fields
from services.recognition import ocr as _ocr
from services.recognition import recognizer as _recognizer
from services.recognition import import_service as _import_service

#: Старое имя модуля → модуль на новом месте.
_MOVED_TO_SERVICES = {
    "document_reader": _reader,
    "document_ocr": _ocr,
    "document_fields": _fields,
    "recognizer": _recognizer,
    "document_import_service": _import_service,
}
for _old_name, _module in _MOVED_TO_SERVICES.items():
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
