#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Раскладка базового генератора после разбиения (Этап 4, 16.10.2026).

Проверяет то, чего не видит обычный прогон тестов модулей:

1. `core/contracts/base_generator.py` ДЕЙСТВИТЕЛЬНО разбит: старого файла
   нет, семь модулей на месте.
2. Старый путь `core.contracts.base_generator` работает через алиасы и
   ведёт к ТОМУ ЖЕ объекту модуля, что и `core.contracts.base`.
3. Публичный API `BaseContractGenerator` не изменился: НИ ОДИН метод
   старого класса не потерян. Это главная страховка шага — подклассы
   (`PerevozkaGenerator` в публичной части и четыре клиентских генератора
   в приватной) вызывают и публичные хуки, и приватные помощники
   (`self._split_payment`, `cls._days_to_words_genitive`,
   `self._make_point_heading`), и без делегатов они падали бы с
   `AttributeError` — метод физически в другом файле.
4. `core.contracts` остался пакетом-ПРОСТРАНСТВОМ ИМЁН. Если завести
   `core/contracts/__init__.py`, обычный пакет «выиграет» поиск у
   одноимённой папки на `sys.path`, и клиентские типы приватной части
   перестанут находиться — отвалится половина программы. Здесь это
   зафиксировано явно.

Зачем отдельный файл: без него удаление блока алиасов в `core/__init__.py`
не покраснит НИ ОДИН тест проекта — импорты внутри `tests/` поднимаются
`conftest.py`, который импортирует пакеты целиком.

Проба, которая ловит цикл импорта в ОБРАТНОМ порядке (новый путь первым),
лежит рядом: `tools/check_stage4_aliases.py` (режимы `old` и `new`).
"""

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
CONTRACTS = PROJECT_ROOT / "core" / "contracts"
BASE_DIR = CONTRACTS / "base"
OLD_FILE = CONTRACTS / "base_generator.py"

#: Семь модулей пакета `core/contracts/base/` и то, за что каждый отвечает.
BASE_MODULES = [
    "__init__.py",
    "generator.py",
    "render.py",
    "tables.py",
    "formatting.py",
    "postprocess.py",
    "payments.py",
    "dates.py",
]

#: Публичный API пакета (доступен из обоих путей).
PUBLIC_API = [
    "BaseContractGenerator",
    "PostprocessStep",
    "ConvertNewlinesStep",
    "NormalizeSpacesStep",
]

#: Методы `BaseContractGenerator` ДО разбиения — снято `ast` с коммита
#: `127e838` (последний коммит, где `core/contracts/base_generator.py` цел).
#: Порядок алфавитный; список заморожен НАМЕРЕННО: он и есть проверка
#: «публичный API не изменился».
OLD_METHODS = (
    "_apply_sample_cell_format",
    "_build_replacements_map",
    "_carrier_type_of",
    "_convert_newlines_to_breaks",
    "_copy_paragraph_format",
    "_copy_run_format",
    "_day_of_month",
    "_days_to_words",
    "_days_to_words_genitive",
    "_delete_row",
    "_fill_table_cell",
    "_find_marker_paragraph",
    "_flatten_replacements",
    "_format_date_dot",
    "_format_date_full",
    "_get_template_path",
    "_insert_route_tables",
    "_iter_paragraphs",
    "_iter_table_paragraphs",
    "_lower_first",
    "_make_point_heading",
    "_make_spacer",
    "_month_name",
    "_normalize_document_spaces",
    "_normalized_point_address",
    "_outline_paragraph",
    "_plural_form",
    "_postprocess_document",
    "_qname",
    "_remove_empty_vehicle_rows",
    "_render_manual",
    "_render_template",
    "_replace_in_document",
    "_replace_in_paragraph",
    "_sample_cell",
    "_set_cell_text",
    "_set_grid_widths",
    "_set_paragraph_text",
    "_short_fio",
    "_single_line",
    "_split_payment",
    "_triple_to_words",
    "build_replacements",
    "default_output_dir",
    "generate",
    "generate_docx",
    "generate_pdf",
    "get_filename",
    "get_template_path",
    "normalize_spaces",
    "postprocess_steps",
    "preprocess",
    "validate",
)

#: Константы контракта типа — должны остаться на месте.
CONTRACT_CONSTANTS = (
    "CONTRACT_TYPE",
    "TEMPLATE_NAMES",
    "FILE_PREFIX",
    "VALIDATOR_CLASS",
    "DEFAULT_OUTPUT_DIRNAME",
    "MULTILINE_PLACEHOLDERS",
)

#: Приватные методы, которые ЗОВУТ подклассы (проверено `git grep` по
#: публичной и приватной частям). Каждый обязан остаться в классе.
SUBclass_CALLED = (
    ("PerevozkaGenerator", ("_find_marker_paragraph", "_outline_paragraph",
                            "_make_point_heading", "_make_spacer",
                            "_fill_table_cell", "_sample_cell",
                            "_set_grid_widths", "_delete_row",
                            "_normalized_point_address", "_split_payment",
                            "_days_to_words_genitive", "_format_date_full",
                            "_format_date_dot", "_day_of_month", "_month_name",
                            "_short_fio", "_single_line",
                            "_flatten_replacements")),
    ("ArendaTsGenerator", ("_delete_row", "_flatten_replacements",
                           "_single_line", "_short_fio", "_format_date_full",
                           "_days_to_words_genitive")),
    ("FormikaGenerator", ("_delete_row", "_flatten_replacements",
                          "_single_line", "_format_date_full",
                          "_day_of_month", "_month_name",
                          "_days_to_words_genitive")),
    ("LogistiksRusGenerator", ("_delete_row", "_flatten_replacements",
                               "_single_line", "_carrier_type_of",
                               "_format_date_full", "_day_of_month",
                               "_month_name")),
)


# ─────────────────────────────────────────────────────────
# 1. Файлы и пакет
# ─────────────────────────────────────────────────────────

def test_old_file_is_gone():
    """Старого файла в пакете типов больше нет."""
    assert not OLD_FILE.exists(), (
        "core/contracts/base_generator.py вернулся — разбиение Этапа 4 отменено?"
    )


@pytest.mark.parametrize("name", BASE_MODULES)
def test_base_module_file_exists(name):
    """Все семь модулей пакета на месте (плюс `__init__.py`)."""
    assert (BASE_DIR / name).is_file(), f"нет файла core/contracts/base/{name}"


def test_base_is_a_regular_package_with_docstring():
    """`core.contracts.base` — обычный пакет с докстрокой."""
    module = importlib.import_module("core.contracts.base")
    assert module.__doc__ and module.__doc__.strip(), "у пакета нет докстроки"


def test_base_package_file_is_not_empty():
    """Файл `__init__.py` не пустой — иначе это не пакет, а совпадение."""
    text = (BASE_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "BaseContractGenerator" in text


def test_contracts_has_no_init_file():
    """
    `core/contracts/__init__.py` НЕ заводить.

    `core.contracts` — пакет-пространство имён. Обычный пакет «выигрывает»
    поиск у одноимённой папки на `sys.path`, и подпакеты клиентских типов
    из приватной части (`core/contracts/formika/` и три соседних) перестали
    бы находиться. Проверено чтением `core/private_path.py`: расширение
    `__path__` рассчитано ровно на namespace-пакет.
    """
    assert not (CONTRACTS / "__init__.py").exists(), (
        "core/contracts/__init__.py появился — приватные типы договоров "
        "перестанут импортироваться"
    )


def test_contracts_is_a_namespace_package():
    """`core.contracts` — именно namespace-пакет (без `__init__.py`)."""
    import core.contracts as contracts

    assert hasattr(contracts, "__path__")
    assert contracts.__file__ is None, (
        "у core.contracts появился __file__ — значит, это обычный пакет"
    )


def test_contracts_namespace_path_contains_public_contracts_dir():
    """Публичная папка типов лежит в `__path__` пакета."""
    import core.contracts as contracts

    assert str(CONTRACTS) in list(contracts.__path__)


# ─────────────────────────────────────────────────────────
# 2. Оба пути дают ОДИН модуль
# ─────────────────────────────────────────────────────────

def test_old_and_new_paths_are_the_same_module():
    """`core.contracts.base_generator` и `core.contracts.base` — один объект."""
    old = importlib.import_module("core.contracts.base_generator")
    new = importlib.import_module("core.contracts.base")
    assert old is new, (
        "старый и новый путь разъехались на два модуля: подклассы увидят "
        "два разных BaseContractGenerator"
    )


def test_old_name_is_in_sys_modules():
    """Старое имя лежит в `sys.modules` — это читает `from ... import ...`."""
    import core.contracts.base as base  # noqa: F401 — поднимает `core`

    assert sys.modules.get("core.contracts.base_generator") is sys.modules[
        "core.contracts.base"
    ]


def test_old_name_is_attribute_of_contracts_package():
    """
    `core.contracts.base_generator` доступно и как АТРИБУТ пакета.

    Одного `sys.modules` мало: инструкция `import core.contracts.base_generator`
    после загрузки читает последнее имя как атрибут родителя и без
    `setattr` падает с AttributeError. Ровно на этом споткнулся Этап 3.
    """
    import core  # noqa: F401
    import core.contracts as contracts
    import core.contracts.base as base

    assert getattr(contracts, "base_generator") is base


def test_module_name_is_the_new_one():
    """`__name__` модуля — новый: это переехавший файл, а не копия."""
    module = importlib.import_module("core.contracts.base_generator")

    assert module.__name__ == "core.contracts.base"


# ─────────────────────────────────────────────────────────
# 3. Публичный API
# ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", PUBLIC_API)
def test_public_api_from_new_path(name):
    """Публичный API доступен по НОВОМУ пути."""
    module = importlib.import_module("core.contracts.base")
    assert hasattr(module, name), f"core.contracts.base потерял {name}"


@pytest.mark.parametrize("name", PUBLIC_API)
def test_public_api_from_old_path(name):
    """Тот же публичный API доступен и по СТАРОМУ пути (через алиасы)."""
    module = importlib.import_module("core.contracts.base_generator")
    assert hasattr(module, name), f"core.contracts.base_generator потерял {name}"


def test_public_api_is_reexported_from_submodules():
    """Имена приходят из `generator` и `postprocess`, а не объявлены в __init__."""
    from core.contracts.base.generator import BaseContractGenerator
    from core.contracts.base.postprocess import (
        ConvertNewlinesStep,
        NormalizeSpacesStep,
        PostprocessStep,
    )

    assert BaseContractGenerator.__module__ == "core.contracts.base.generator"
    assert PostprocessStep.__module__ == "core.contracts.base.postprocess"
    assert ConvertNewlinesStep.__module__ == "core.contracts.base.postprocess"
    assert NormalizeSpacesStep.__module__ == "core.contracts.base.postprocess"


@pytest.mark.parametrize("name", PUBLIC_API)
def test_public_api_is_in_dunder_all(name):
    """`__all__` пакета перечисляет публичный API."""
    module = importlib.import_module("core.contracts.base")
    assert name in module.__all__


# ─────────────────────────────────────────────────────────
# 4. Совместимость с подклассами — ГЛАВНАЯ проверка шага
# ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", OLD_METHODS)
def test_old_method_survived_the_split(name):
    """
    НИ ОДИН метод старого класса не потерян.

    Список снят `ast` с коммита до разбиения. Переехавшие методы остались
    в классе тонкими делегатами — иначе подклассы падали бы с
    AttributeError, и никакие алиасы `sys.modules` не помогли бы.
    """
    from core.contracts.base import BaseContractGenerator

    assert hasattr(BaseContractGenerator, name), (
        f"BaseContractGenerator потерял {name} — подклассы упадут"
    )
    assert callable(getattr(BaseContractGenerator, name))


def test_no_old_method_was_renamed_or_removed():
    """Обратная проверка: в классе не осталось ЛИШНИХ старых имён из списка."""
    from core.contracts.base import BaseContractGenerator

    missing = [name for name in OLD_METHODS if not hasattr(BaseContractGenerator, name)]
    assert missing == []


@pytest.mark.parametrize("name", CONTRACT_CONSTANTS)
def test_contract_constants_survived(name):
    """Константы контракта типа остались в классе."""
    from core.contracts.base import BaseContractGenerator

    assert hasattr(BaseContractGenerator, name), f"потеряна константа {name}"


@pytest.mark.parametrize(
    "klass,methods",
    SUBclass_CALLED,
    ids=[item[0] for item in SUBclass_CALLED],
)
def test_methods_called_by_subclasses_exist(klass, methods):
    """Каждый метод, который зовут подклассы, есть в базовом классе."""
    from core.contracts.base import BaseContractGenerator

    absent = [name for name in methods if not hasattr(BaseContractGenerator, name)]
    assert absent == [], f"{klass} зовёт отсутствующие методы: {absent}"


def test_classmethod_and_staticmethod_kinds_are_preserved():
    """
    Вид метода сохранён там, где подклассы зовут его без экземпляра.

    `cls._split_payment(...)`, `cls._days_to_words_genitive(...)` и
    `cls._plural_form(...)` — вызовы ЧЕРЕЗ КЛАСС. Если сделать их обычными
    методами, подклассы получат TypeError вместо результата.
    """
    from core.contracts.base import BaseContractGenerator

    assert isinstance(
        BaseContractGenerator.__dict__["_split_payment"], classmethod
    ), "_split_payment перестал быть classmethod — test_prepayment.py зовёт его от класса"
    assert isinstance(
        BaseContractGenerator.__dict__["_days_to_words_genitive"], classmethod
    )
    assert isinstance(BaseContractGenerator.__dict__["_triple_to_words"], classmethod)
    assert isinstance(BaseContractGenerator.__dict__["_plural_form"], staticmethod)
    assert isinstance(BaseContractGenerator.__dict__["_qname"], staticmethod)
    assert isinstance(BaseContractGenerator.__dict__["normalize_spaces"], staticmethod)


def test_split_payment_works_through_the_class():
    """`BaseContractGenerator._split_payment` работает без экземпляра."""
    from core.contracts.base import BaseContractGenerator

    split = BaseContractGenerator._split_payment(210000.0, 63000.0)
    assert split["has_prepayment"] is True
    assert split["prepayment_percent"] == "30"
    assert split["balance_percent"] == "70"


def test_default_output_dir_points_at_project_root():
    """
    Папка договоров — `<корень проекта>/output`, а не `core/contracts/output`.

    После переезда файл стал на папку глубже, и `parents[2]` дал бы
    `core/contracts`. Проверять это обязательно: ошибка тихая — договоры
    просто падают не туда.
    """
    from core.contracts.base import BaseContractGenerator

    assert BaseContractGenerator.default_output_dir() == str(PROJECT_ROOT / "output")


def test_pere_vozka_generator_still_inherits():
    """`PerevozkaGenerator` по-прежнему наследуется от базы."""
    from core.contracts.base import BaseContractGenerator
    from core.contracts.perevozka.generator import PerevozkaGenerator

    assert issubclass(PerevozkaGenerator, BaseContractGenerator)


def test_expediciya_generator_still_inherits():
    """`ExpediciyaGenerator` — тоже (заглушка, но наследование живое)."""
    from core.contracts.base import BaseContractGenerator
    from core.contracts.expediciya.generator import ExpediciyaGenerator

    assert issubclass(ExpediciyaGenerator, BaseContractGenerator)


def test_pere_vozka_overrides_are_still_seen_through_the_base():
    """
    Переопределения перевозки работают через базовый класс.

    Это проверка того, что делегаты передают `self.*`, а не зовут функции
    модуля напрямую: `_sample_vehicle_table` и `_is_vehicle_table` у
    перевозки свои, и модуль `tables.py` обязан получить ИХ.
    """
    from core.contracts.base import BaseContractGenerator
    from core.contracts.perevozka.generator import PerevozkaGenerator

    assert "_sample_vehicle_table" in vars(PerevozkaGenerator)
    assert "_is_vehicle_table" in vars(PerevozkaGenerator)
    assert (
        PerevozkaGenerator._sample_vehicle_table
        is not BaseContractGenerator._sample_vehicle_table
    )


def test_postprocess_steps_are_built_from_the_new_module():
    """`postprocess_steps()` базового класса отдаёт шаг из `base/postprocess.py`."""
    from core.contracts.base import BaseContractGenerator, ConvertNewlinesStep

    class Stub(BaseContractGenerator):
        pass

    stub = Stub(templates_dir=str(PROJECT_ROOT / "templates"))
    steps = stub.postprocess_steps(None)

    assert len(steps) == 1
    assert isinstance(steps[0], ConvertNewlinesStep)
    assert steps[0].generator is stub


# ─────────────────────────────────────────────────────────
# 5. Порядок импорта — отдельным процессом
# ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["old", "new"])
def test_both_import_orders_work_in_a_fresh_process(mode):
    """
    ЛЮБОЙ порядок импорта работает — и это отдельный процесс.

    В общем прогоне pytest обратный порядок не проверяется: `core` уже
    поднят `conftest.py`. Именно на «новом пути первым» падали первые
    заходы Этапов 2 и 3.
    """
    probe = PROJECT_ROOT / "tools" / "check_stage4_aliases.py"
    # Проба печатает по-русски, а stdout дочернего процесса — труба: на
    # англоязычном раннере кодировка по умолчанию cp1252, кириллица в неё
    # не влезает, и print() роняет пробу через UnicodeEncodeError. UTF-8 для
    # дочернего процесса — то же, что делает
    # tests/ui/main_window/test_qt_shutdown_agent_pool.py.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, str(probe), mode],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"проба режима {mode} упала:\n{result.stdout}\n{result.stderr}"
    )


def test_private_contracts_dir_joins_namespace_path():
    """
    Приватная папка типов попадает в `__path__` пакета-пространства имён.

    Это и есть причина, по которой `core/contracts/__init__.py` заводить
    нельзя: расширение `__path__` работает только для namespace-пакета.
    """
    import core.contracts as contracts
    from core import private_path

    root = private_path.find_private_root()
    if root is None:
        pytest.skip("приватная часть не подключена — проверять нечего")

    private_contracts = root / "core" / "contracts"
    assert private_contracts.is_dir()

    private_path.attach_private_root()
    import core.contracts as contracts_after  # noqa: F401 — тот же объект пакета

    assert str(private_contracts) in list(contracts.__path__), (
        "приватная папка типов не попала в __path__ пакета core.contracts"
    )


def test_private_generators_import_through_the_alias():
    """
    Клиентские генераторы приватной части импортируются и наследуются.

    Самая дорогая проверка шага: если бы делегаты не сохранили приватные
    методы, сломалось бы ровно здесь.
    """
    from core import private_path

    if private_path.find_private_root() is None:
        pytest.skip("приватная часть не подключена — проверять нечего")

    private_path.attach_private_root()
    from core.contracts.base_generator import BaseContractGenerator

    imported = []
    for key in ("formika", "logistiks_rus", "arenda_ts"):
        module = importlib.import_module(f"core.contracts.{key}.generator")
        generator_class = next(
            value
            for name, value in vars(module).items()
            if isinstance(value, type) and name.endswith("Generator")
        )
        assert issubclass(generator_class, BaseContractGenerator), (
            f"{key}: генератор больше не наследуется от BaseContractGenerator"
        )
        imported.append(key)

    assert len(imported) == 3


# ─────────────────────────────────────────────────────────
# 6. Исходники разбиения — ссылок на старый файл не осталось
# ─────────────────────────────────────────────────────────

def _code_identifiers(path):
    """
    Имена и атрибуты из КОДА файла — без докстрок и без строк-литералов.

    Проверять «встречается ли строка base_generator в тексте» нельзя:
    каждый модуль пакета объясняет в докстроке, ОТКУДА он переехал, и такая
    проверка краснела бы на здоровом файле. Здесь собираются только
    `ast.Name` и `ast.Attribute` — то, из чего состоит настоящий импорт.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # Докстрока модуля — тоже строковый литерал, из разбора её убираем.
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(
        body[0].value, ast.Constant
    ):
        body = body[1:]

    tokens = set()
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Name):
            tokens.add(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.add(node.attr)
        elif isinstance(node, ast.alias):
            tokens.add(node.name)
            if node.asname:
                tokens.add(node.asname)
        elif isinstance(node, ast.ImportFrom) and node.module:
            # `from core.contracts.base_generator import X` — имя подмодуля
            # лежит в `module`, а не в `alias.name`. Без этой ветки проверка
            # пропускала бы ровно тот импорт, ради которого написана.
            tokens.update(node.module.split("."))
    return tokens


def test_no_source_file_imports_the_old_path_inside_contracts():
    """
    Внутри пакета `core/contracts/base/` никто не импортирует старый путь.

    Снаружи он работает через алиасы (это отдельные тесты выше), но новый
    код обязан идти по новому пути. Проверяются ИМЕНА из кода, а не текст:
    упоминание старого пути в докстроке («переехало из
    core/contracts/base_generator.py») — это документация, а не импорт.
    """
    offenders = [
        path.name
        for path in sorted(BASE_DIR.glob("*.py"))
        if "base_generator" in _code_identifiers(path)
    ]
    assert offenders == [], f"старый путь в КОДЕ модулей пакета: {offenders}"


def test_code_identifiers_check_actually_detects_an_import(tmp_path):
    """
    Проверка проверки: настоящий импорт старого пути она ПОЙМАЕТ.

    Без этого теста предыдущий мог бы «проходить» просто потому, что ничего
    не ищет: он был бы зелёным и на сломанном коде.
    """
    sample = tmp_path / "broken.py"
    sample.write_text(
        "from core.contracts.base_generator import BaseContractGenerator\n",
        encoding="utf-8",
    )

    assert "base_generator" in _code_identifiers(sample)

    docstring_only = tmp_path / "fine.py"
    docstring_only.write_text(
        '"""Переехало из core/contracts/base_generator.py."""\n'
        "from core.contracts.base import BaseContractGenerator\n",
        encoding="utf-8",
    )

    assert "base_generator" not in _code_identifiers(docstring_only)


def test_base_modules_have_docstrings():
    """У каждого модуля пакета есть докстрока — иначе непонятно, зачем он."""
    without = []
    for path in sorted(BASE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not ast.get_docstring(tree):
            without.append(path.name)
    assert without == [], f"без докстроки: {without}"
