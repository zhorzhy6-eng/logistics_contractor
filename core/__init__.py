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

Этап 3 (16.10.2026) — инфраструктура: внешние API, секреты, TLS:
  * infrastructure/api/gigachat.py          (был core/gigachat_client.py);
  * infrastructure/api/dadata.py            (был core/dadata_client.py);
  * infrastructure/api/tls.py               (был core/tls_config.py);
  * infrastructure/secrets/store.py         (был core/secrets_store.py);
  * core/utils/json_text.py                 (был core/json_text.py).

Чтобы не менять импорты по всему проекту (Этапы 1–2 — 227 адресов,
Этап 3 — ещё 68: 50 в публичной части и 18 в приватной), этот файл делает
ДВЕ вещи:

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
    from infrastructure.api.gigachat import GigaChatClient
    from infrastructure.secrets.store import get_gigachat_key

Старые пути помечены как deprecated: они работают, но новый код должен идти
через подпапки (ядро), через `services/` (слой распознавания) и через
`infrastructure/` (внешние API и секреты).

ЧЕГО ЗДЕСЬ НЕТ
--------------
`ru_morphology` в Этап 1 не переезжал: файла `core/ru_morphology.py` в
проекте нет, модуль живёт в `core/contracts/ru_morphology.py` (пакет типов
договоров), и его импортируют генератор Экспедиторства и четыре набора
тестов. Переезд этого модуля — отдельное решение (см. `docs/STATE.md`).

`core/import_cancel.py`, `core/pseudonymizer.py` и `core/trace.py` остались
в корне: их импортируют и `services/recognition/*`, и `infrastructure/api/*`
по старому пути.

Проверка совместимости — `tests/test_core_layout.py`,
`tests/test_services_layout.py` и `tests/test_infrastructure_layout.py`.
Пробы обоих порядков импорта — `tools/check_stage2_aliases.py` и
`tools/check_stage3_aliases.py`.
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

# ── Инфраструктура: GigaChat, DaData, секреты, TLS (Этап 3, 16.10.2026) ──
# Пять модулей переехали в `infrastructure/` и `core/utils/`. Для обратной
# совместимости регистрируем их в `sys.modules` под старыми именами — тем же
# приёмом `setdefault`, что и в Этапах 1–2.
#
# ПОРЯДОК РЕГИСТРАЦИИ — ЧАСТЬ РЕШЕНИЯ, а не вкусовщина. `infrastructure/*`
# импортирует `core.*` на уровне модуля (`store` → `core.audit`, `gigachat` →
# `core.import_cancel` и `core.pseudonymizer`), поэтому связь двусторонняя:
# `core` → `gigachat` → `core`. Разрывается она только порядком:
#
#   1. `core.utils.json_text` — его подтягивает `gigachat` на уровне модуля
#      (`from core.utils.json_text import shorten_json_text`). Файла
#      `core/json_text.py` больше нет, и без алиаса это ModuleNotFoundError,
#      поэтому утилита регистрируется ПЕРВОЙ;
#   2. `core.secrets_store` — от него на уровне модуля зависят и `gigachat`,
#      и `dadata`;
#   3. `core.tls_config` — от него на уровне модуля зависит `gigachat`;
#   4. `core.gigachat_client` и `core.dadata_client` — листья, их черёд
#      последний.
#
# ОБРАТНЫЙ ПОРЯДОК (инфраструктура импортируется ПЕРВОЙ, когда `core` ещё не
# загружен) в общем прогоне pytest не виден: `core` там уже поднят
# `tests/conftest.py`. Его проверяет проба `tools/check_stage3_aliases.py`
# с двумя режимами — так же, как `check_stage2_aliases.py` для Этапа 2.
def _register_infrastructure_aliases():
    """
    Регистрирует старые имена модулей `infrastructure` в `sys.modules`
    и как атрибуты пакета `core`.

    Регистраций ДВЕ на каждый модуль, и обе обязательны — ровно как в
    Этапах 1–2:

      * `sys.modules["core.gigachat_client"]` — чтобы работал импорт
        ПОДМОДУЛЯ (`from core.gigachat_client import GigaChatClient`);
      * атрибут `core.gigachat_client` — чтобы работало `core.gigachat_client`
        после `import core`. Одного `sys.modules` мало: `import core.X` для
        уже загруженного модуля родительский атрибут не выставляет, и
        обращение к нему падало бы с AttributeError (проверено на живой
        пробе — первая версия Этапа 3 регистрировала только `sys.modules`).

    Объект модуля при этом ОДИН: не копия и не обёртка, поэтому `isinstance`,
    пикинг и сравнение классов ведут себя как раньше.
    """
    # Порядок — от зависимостей к зависимым (см. пояснение выше).
    from core.utils import json_text as _json_text
    _sys.modules.setdefault(__name__ + ".json_text", _json_text)

    from infrastructure.secrets import store as _store
    _sys.modules.setdefault(__name__ + ".secrets_store", _store)

    from infrastructure.api import tls as _tls
    _sys.modules.setdefault(__name__ + ".tls_config", _tls)

    from infrastructure.api import gigachat as _gigachat
    _sys.modules.setdefault(__name__ + ".gigachat_client", _gigachat)

    from infrastructure.api import dadata as _dadata
    _sys.modules.setdefault(__name__ + ".dadata_client", _dadata)

    # Атрибуты пакета — ПОСЛЕ всех импортов: к этому моменту объекты модулей
    # уже получены, и порядок присваивания ни на что не влияет.
    _package = _sys.modules[__name__]
    for _old_name, _module in (
        ("json_text", _json_text),
        ("secrets_store", _store),
        ("tls_config", _tls),
        ("gigachat_client", _gigachat),
        ("dadata_client", _dadata),
    ):
        setattr(_package, _old_name, _module)


try:
    _register_infrastructure_aliases()
except Exception:  # noqa: BLE001 — алиасы нужны для совместимости,
    # а не для работы: новый код идёт через `infrastructure.*` напрямую.
    # Молчать нельзя: без алиасов старые пути падают с ModuleNotFoundError,
    # и причина должна быть видна в логе.
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "Алиасы infrastructure не зарегистрированы", exc_info=True
    )


# ── base_generator → core/contracts/base/ (Этап 4, 16.10.2026) ──
# `core/contracts/base_generator.py` разбит на пакет `core/contracts/base/`.
# Старое имя модуля регистрируется в `sys.modules` — тем же приёмом
# `setdefault`, что и в Этапах 1–3, — поэтому работают ОБА написания:
#
#     from core.contracts.base_generator import BaseContractGenerator
#     from core.contracts.base import BaseContractGenerator
#
# Объект модуля при этом ОДИН: `core.contracts.base_generator is
# core.contracts.base` → True.
#
# ПОЧЕМУ АЛИАС ЗДЕСЬ, А НЕ В `core/contracts/__init__.py`. Файла
# `core/contracts/__init__.py` в проекте НЕТ и заводить его нельзя:
# `core.contracts` — пакет-ПРОСТРАНСТВО ИМЁН (namespace package). Обычный
# пакет «выигрывает» поиск у одноимённой папки на `sys.path`, и подпакеты
# клиентских типов из приватной части (`core/contracts/formika/` и три
# соседних в `logistics_contractor_private`) перестали бы находиться —
# приватная часть отвалилась бы целиком. Проверено чтением
# `core/private_path.py`: расширение `__path__` рассчитано ровно на
# namespace-пакет. Реэкспорт имён достигается тем же результатом:
# `from core.contracts.base import BaseContractGenerator` работает и без
# `__init__.py` — это импорт подмодуля.
#
# РЕГИСТРАЦИЙ ДВЕ, и обе обязательны — как в Этапах 1–3:
#   * `sys.modules["core.contracts.base_generator"]` — чтобы работал импорт
#     ПОДМОДУЛЯ (`from core.contracts.base_generator import …`);
#   * атрибут `base_generator` у пакета `core.contracts` — чтобы работало
#     `import core.contracts.base_generator` и обращение
#     `core.contracts.base_generator`. Одного `sys.modules` МАЛО: инструкция
#     `import a.b.c` после загрузки читает `c` как АТРИБУТ `a.b`, и без
#     `setattr` падает с `AttributeError: module 'core.contracts' has no
#     attribute 'base_generator'` (проверено пробой на временном пакете,
#     повторяет граблю Этапа 3).
import sys as _sys  # noqa: F811 — уже импортирован выше, нужен явно рядом
from core.contracts import base as _contracts_base

_sys.modules.setdefault("core.contracts.base_generator", _contracts_base)
setattr(_sys.modules["core.contracts"], "base_generator", _contracts_base)


# ── Агент Максимилиан (Этап 5, 16.10.2026) ──
# Пакет `core/agent/` разбит на три группы в `services/agent/`:
#
#   * `services/agent/core/`    — ядро: agent, llm, memory, long_memory,
#     prompt (+ `prompts/maximilian.txt`);
#   * `services/agent/tools/`   — инструменты: registry (был `tools.py`),
#     analytics, dadata, legal, rag, context, proactivity,
#     proactivity_tools, fill_parser, tutorial, tutorial_hints;
#   * `services/agent/persona/` — личность: personas, emotional.
#
# В `core/agent/` остались ДВА файла: `__init__.py` (shim, отдаёт имена из
# `services.agent`) и `words.py` (общая таблица служебных слов — её читает
# и агент, и `core/pseudonymizer.py` ФАЙЛОМ по пути `core/agent/words.py`;
# переезд увёл бы её из-под загрузчика, поэтому `core.agent.words`
# намеренно НЕ алиасится — модуль живёт там же).
#
# РЕГИСТРАЦИЙ ДВЕ, и обе обязательны — та же грабля, что в Этапах 3 и 4,
# и ровно её закрывает общая функция `register_module_alias`:
#
#   * `sys.modules["core.agent.llm"]` — чтобы работал импорт ПОДМОДУЛЯ
#     (`from core.agent.llm import SafeChat` и `import core.agent.llm`);
#   * атрибут `llm` у пакета `core.agent` — чтобы работало обращение
#     `core.agent.llm` после загрузки. Одного `sys.modules` МАЛО: инструкция
#     `import a.b.c` читает последнее имя как АТРИБУТ родителя и падает с
#     `AttributeError: module 'core.agent' has no attribute 'llm'`.
#
# ПОРЯДОК — от зависимостей к зависимым, как в блоке инфраструктуры:
# `services/agent/core/agent.py` тянет llm, persona, memory, long_memory,
# prompt и `tools/registry`, а `tools/registry` — `fill_parser`, `context`,
# `dadata`, `legal` (они, в свою очередь, — `rag`).
#
# ЧЕГО ЗДЕСЬ НЕТ. `core.agent.diagnostics` — модуля `diagnostics.py` в
# пакете агента НЕ БЫЛО и нет; этот адрес ведёт к `core.diagnostics` (так
# его и понимал старый код: `from core.agent import diagnostics` → пакет
# агента без такого модуля → ImportError → ветка «диагностика недоступна»).
#
# ПОЧЕМУ НЕ ПЕРЕХВАТЧИК ИМПОРТА. Проверялся и отброшен: `sys.meta_path`-
# finder для этих имён порождал ВТОРОЙ объект модуля —
# `sys.modules["core.agent.tutorial_hints"]` и
# `sys.modules["services.agent.tools.tutorial_hints"]` оказывались разными
# объектами. Ломалось тихо ровно то, ради чего алиасы и делаются:
# `monkeypatch.setattr(core.agent.tutorial_hints, …)` перестаёт быть виден
# коду, который импортирует тот же модуль по новому пути (поймано тестом
# `tests/test_agent_tutorial_ui.py`). Прямая регистрация даёт ОДИН объект.
#
# ЦЕНА: `core.agent` и `services.agent.*` поднимаются при импорте `core`
# (как и было до Этапа 5, когда агент жил в `core/agent/`). Приватный сосед
# `core/agent/labels.py` при этом не теряется: пакет `core.agent` лежит в
# `PACKAGE_BOOTSTRAP` (`core/private_path.py`), и `attach_private_root()`
# дополняет его `__path__` приватной папкой — плюс та же страховка стоит в
# самом shim (`core/agent/__init__.py`).
_AGENT_ALIASES = {
    "core.agent.agent": "services.agent.core.agent",
    "core.agent.llm": "services.agent.core.llm",
    "core.agent.memory": "services.agent.core.memory",
    "core.agent.long_memory": "services.agent.core.long_memory",
    "core.agent.prompt": "services.agent.core.prompt",
    "core.agent.personas": "services.agent.persona.personas",
    "core.agent.emotional": "services.agent.persona.emotional",
    "core.agent.tools": "services.agent.tools.registry",
    "core.agent.analytics_tools": "services.agent.tools.analytics",
    "core.agent.dadata_tools": "services.agent.tools.dadata",
    "core.agent.legal_tools": "services.agent.tools.legal",
    "core.agent.rag": "services.agent.tools.rag",
    "core.agent.context_tools": "services.agent.tools.context",
    "core.agent.proactivity": "services.agent.tools.proactivity",
    "core.agent.proactivity_tools": "services.agent.tools.proactivity_tools",
    "core.agent.fill_parser": "services.agent.tools.fill_parser",
    "core.agent.tutorial": "services.agent.tools.tutorial",
    "core.agent.tutorial_hints": "services.agent.tools.tutorial_hints",
    "core.agent.diagnostics": "core.diagnostics",
}


def _register_agent_aliases():
    """
    Регистрирует старые имена модулей агента (`core.agent.*`).

    Регистраций ДВЕ на каждое имя, и обе делает `register_module_alias`
    (см. пояснение выше). `setdefault`, как у алиасов Этапов 1–3: живой
    модуль по старому имени не затирается — важно для `words.py`, который
    остался в `core/agent/`, и на случай, если приватная часть положит
    свой модуль по старому пути.
    """
    import importlib

    from core.private_path import register_module_alias

    for old_name, new_name in _AGENT_ALIASES.items():
        if old_name in _sys.modules:
            continue
        module = importlib.import_module(new_name)
        register_module_alias(old_name, module)


try:
    _register_agent_aliases()
except Exception:  # noqa: BLE001 — алиасы нужны для совместимости,
    # а не для работы: новый код идёт через `services.agent.*` напрямую.
    # Молчать нельзя: без алиасов старые пути падают с ModuleNotFoundError
    # (в общем прогоне pytest этого не видно — там пакет агента поднимает
    # `conftest.py` через `ui/`), и причина должна быть видна в логе.
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "Алиасы core.agent не зарегистрированы", exc_info=True
    )


# ── Реэкспорт ИМЁН инфраструктуры (для `from core import GigaChatClient`) ──
# Делается ЛЕНИВО, через module-level `__getattr__`, а не статическим
# импортом: на момент выполнения `core/__init__.py` модули infrastructure
# могут быть ещё не готовы (обратный порядок импорта), и статический импорт
# поймал бы ImportError на частичном модуле.
#
#: Имя в пакете `core` → (модуль на новом месте, имя в нём).
_INFRASTRUCTURE_NAMES = {
    # Клиенты внешних сервисов
    "GigaChatClient": ("infrastructure.api.gigachat", "GigaChatClient"),
    "DadataClient": ("infrastructure.api.dadata", "DadataClient"),
    # Секреты (Windows Credential Manager)
    "MISSING_KEY_MESSAGE": ("infrastructure.secrets.store", "MISSING_KEY_MESSAGE"),
    "get_gigachat_key": ("infrastructure.secrets.store", "get_gigachat_key"),
    "set_gigachat_key": ("infrastructure.secrets.store", "set_gigachat_key"),
    "has_gigachat_key": ("infrastructure.secrets.store", "has_gigachat_key"),
    "delete_gigachat_key": ("infrastructure.secrets.store", "delete_gigachat_key"),
    # TLS
    "describe_tls": ("infrastructure.api.tls", "describe_tls"),
    "resolve_ca_bundle": ("infrastructure.api.tls", "resolve_ca_bundle"),
    "ssl_error_hint": ("infrastructure.api.tls", "ssl_error_hint"),
    # Утилита ядра
    "shorten_json_text": ("core.utils.json_text", "shorten_json_text"),
}


def __getattr__(name):
    """
    Ленивый реэкспорт имён Этапа 3 через пакет `core`.

    Вызывается только на доступ к АТРИБУТУ пакета (`core.GigaChatClient`) или
    на `from core import GigaChatClient`. Импорт ПОДМОДУЛЯ
    (`from core.gigachat_client import GigaChatClient`) идёт мимо: его
    обслуживает регистрация в `sys.modules` выше.
    """
    target = _INFRASTRUCTURE_NAMES.get(name)
    if target is None:
        raise AttributeError(f"module 'core' has no attribute {name!r}")
    import importlib as _importlib

    return getattr(_importlib.import_module(target[0]), target[1])


def __dir__():
    """Имена, которые видны в `dir(core)`: свои плюс ленивые (Этап 3)."""
    return sorted(set(globals()) | set(_INFRASTRUCTURE_NAMES))


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
    # Инфраструктура (Этап 3) — резолвятся лениво через __getattr__
    "GigaChatClient", "DadataClient",
    "MISSING_KEY_MESSAGE", "get_gigachat_key", "set_gigachat_key",
    "has_gigachat_key", "delete_gigachat_key",
    "describe_tls", "resolve_ca_bundle", "ssl_error_hint",
    "shorten_json_text",
]
