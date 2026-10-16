#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Раскладка ядра после Этапа 1 рефакторинга (16.10.2026).

Проверяет три вещи, которые нельзя проверить обычными тестами модулей:

1. Семь модулей ДЕЙСТВИТЕЛЬНО переехали: файлов в корне `core/` больше нет,
   а в `core/utils/`, `core/data/`, `core/validation/` — есть.
2. Старые импорты (`from core.dates import parse_date`) работают и ведут
   к ТОМУ ЖЕ объекту модуля, что и новые пути. Это не копия: если импорты
   разъедутся на два разных модуля, тесты проекта начнут видеть два разных
   `ContractData` и «плавающие» ошибки атрибутов.
3. Реэкспорт имён через `core/__init__.py` не разошёлся с `__all__`.

Зачем отдельный файл: без него удаление блока алиасов в `core/__init__.py`
не покраснит НИ ОДИН тест проекта — импорты внутри `tests/` идут через
`conftest.py`, который поднимает пакеты целиком.
"""

import importlib
from pathlib import Path

import pytest

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
CORE = PROJECT_ROOT / "core"

#: (старое имя модуля, новый путь файла) — ровно то, что переехало в Этапе 1.
MOVED = [
    ("dates", "core/utils/dates.py"),
    ("fuzzy", "core/utils/fuzzy.py"),
    ("text_normalize", "core/utils/text_normalize.py"),
    ("address_utils", "core/utils/address_utils.py"),
    ("contract_data", "core/data/contract_data.py"),
    ("vat", "core/data/vat.py"),
    ("validator", "core/validation/validator.py"),
]

#: Имена, которые после переезда обязаны быть видны из `core` напрямую.
REEXPORTED = [
    "parse_date",
    "to_iso",
    "DATE_FORMATS",
    "stem",
    "search_levels",
    "normalize_position",
    "extract_city",
    "ContractData",
    "compute_vat",
    "VAT_FREE",
    "Validator",
    "ValidationReport",
]


@pytest.mark.parametrize("old_name,new_path", MOVED, ids=[m[0] for m in MOVED])
def test_file_moved_out_of_core_root(old_name, new_path):
    """Старого файла в корне `core/` нет, новый файл на месте."""
    assert not (CORE / f"{old_name}.py").exists(), (
        f"core/{old_name}.py вернулся в корень — переезд Этапа 1 отменён?"
    )
    assert (PROJECT_ROOT / new_path).is_file(), f"нет файла {new_path}"


@pytest.mark.parametrize("old_name,new_path", MOVED, ids=[m[0] for m in MOVED])
def test_old_import_path_is_the_same_module(old_name, new_path):
    """`import core.<старое_имя>` и новый путь — ОДИН объект модуля."""
    old_module = importlib.import_module(f"core.{old_name}")
    new_module = importlib.import_module(
        new_path[:-3].replace("/", ".")
    )
    assert old_module is new_module, (
        f"core.{old_name} и {new_path} — разные модули: "
        "разъехавшиеся импорты дадут два разных класса с одним именем"
    )


@pytest.mark.parametrize("old_name,new_path", MOVED, ids=[m[0] for m in MOVED])
def test_old_module_is_attribute_of_core(old_name, new_path):
    """`core.<старое_имя>` доступно и как атрибут пакета (`core.dates.parse_date`)."""
    import core

    assert getattr(core, old_name) is importlib.import_module(f"core.{old_name}")


@pytest.mark.parametrize("name", REEXPORTED)
def test_names_are_reexported_from_core(name):
    """Имена из новых мест видны через `from core import <имя>`."""
    import core

    assert hasattr(core, name), f"core.{name} пропало из реэкспорта"
    assert name in core.__all__, f"{name} нет в core.__all__"


def test_all_names_in_dunder_all_exist():
    """`__all__` не обещает того, чего в пакете нет."""
    import core

    missing = [name for name in core.__all__ if not hasattr(core, name)]
    assert missing == [], f"в core.__all__ лишние имена: {missing}"


def test_new_subpackages_are_packages_with_docstrings():
    """Три новые подпапки — обычные пакеты с пояснением в докстроке."""
    for package in ("core.utils", "core.data", "core.validation"):
        module = importlib.import_module(package)
        assert module.__doc__ and module.__doc__.strip(), (
            f"у пакета {package} нет докстроки"
        )


def test_core_root_modules_stayed_in_place():
    """
    Переезд не задел остальные модули корня `core/`.

    Список сократился в Этапе 2 (16.10.2026): `recognizer.py` и
    `document_reader.py` (а также `document_ocr.py`, `document_fields.py`,
    `document_import_service.py`) уехали в `services/recognition/`. Их
    раскладку проверяет `tests/test_services_layout.py` — здесь остались
    только те модули, которых рефакторинг не касался.
    """
    for name in (
        "audit.py",
        "pseudonymizer.py",
        "num_to_words.py",
        "mirror.py",
        "import_cancel.py",
        "trace.py",
    ):
        assert (CORE / name).is_file(), f"core/{name} пропал"


def test_ru_morphology_stayed_in_contracts():
    """
    `ru_morphology` Этап 1 не трогал: он не в корне `core/`, а в пакете
    типов договоров, и его импортирует генератор Экспедиторства.

    Тест фиксирует, что модуль на месте и импортируется: если его всё-таки
    перенесут, тест нужно осознанно переписать вместе с импортами.
    """
    module = importlib.import_module("core.contracts.ru_morphology")
    assert (CORE / "contracts" / "ru_morphology.py").is_file()
    assert hasattr(module, "genitive_position")
