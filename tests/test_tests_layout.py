#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Раскладка тестов по слоям после Этапа 7 (16.10.2026).

Проверяет не поведение приложения, а СТРУКТУРУ набора тестов: три слоя
(`unit`, `integration`, `ui`), обязательные группы внутри каждого, отсутствие
забытых файлов в корне `tests/`, целостность путей и границ между слоями.

Зачем отдельный файл: переезд 142 тестов — операция механическая, и её
результат легко проверить глазами «вроде всё на месте». Этот файл проверяет
то же самое машиной и падает, если раскладка поехала.

Запуск:
    pytest tests/test_tests_layout.py -v -o addopts=""
"""

from __future__ import annotations

import ast
import configparser
import re
from pathlib import Path

import pytest

# ── Корень проекта: ищется по маркеру, как и в остальных тестах ──
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
TESTS_ROOT = PROJECT_ROOT / "tests"

#: Слои и обязательные группы внутри них (Этап 7, подэтап 1.A).
REQUIRED_GROUPS = {
    "unit": ("core", "utils", "contracts", "agent", "services",
             "infrastructure", "tools"),
    "integration": ("db", "services", "infrastructure", "tools"),
    "ui": ("main_window", "widgets", "windows", "agent_widget", "tabs"),
}

#: Сколько файлов легло в каждый слой (карта подэтапа 1.A, docs/STATE.md).
#: unit: 57 → 58 в ЧАСТИ 3 пост-рефакторинга — добавлен
#: `tests/unit/core/test_checksums.py` (общий модуль контрольных сумм);
#: 58 → 59 в фиксе TLS 17.10.2026 — добавлен
#: `tests/unit/core/test_tls_config.py` (регресс на поиск корня по маркеру).
EXPECTED_FILES = {"unit": 59, "integration": 25, "ui": 60}

#: Файлы, которым положено остаться в корне tests/.
ROOT_KEEP = {"conftest.py", "test_tests_layout.py"}

#: Старый способ считать корень проекта — после переезда он врёт.
STALE_ROOT_IDIOMS = (
    re.compile(r"Path\(__file__\)\.resolve\(\)\.parent\.parent"),
    re.compile(r"Path\(__file__\)\.resolve\(\)\.parents\[1\]"),
)


# ─────────────────────────────────────────────────────────────
# Помощники
# ─────────────────────────────────────────────────────────────
def layer_dir(layer: str) -> Path:
    return TESTS_ROOT / layer


def tests_in(directory: Path, recursive: bool = False) -> list[Path]:
    """Тестовые файлы в папке: `test_*.py`, при желании — со всеми подпапками."""
    source = directory.rglob if recursive else directory.glob
    return sorted(p for p in source("test_*.py") if p.is_file())


def all_test_files() -> list[Path]:
    """Все test_*.py во всём дереве tests/."""
    return sorted(p for p in TESTS_ROOT.rglob("test_*.py") if p.is_file())


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# 1. Слои существуют
# ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("layer", sorted(REQUIRED_GROUPS))
def test_layer_directory_exists(layer):
    """Папка слоя есть и это папка."""
    assert layer_dir(layer).is_dir(), f"нет папки tests/{layer}/"


@pytest.mark.parametrize(
    "layer,group",
    [(layer, group) for layer in sorted(REQUIRED_GROUPS)
     for group in REQUIRED_GROUPS[layer]],
)
def test_required_group_exists(layer, group):
    """Обязательная группа внутри слоя есть."""
    assert (layer_dir(layer) / group).is_dir(), f"нет папки tests/{layer}/{group}/"


@pytest.mark.parametrize("layer", sorted(REQUIRED_GROUPS))
def test_layer_has_tests(layer):
    """В слое есть хотя бы один тестовый файл."""
    found = all_test_files()
    inside = [p for p in found if layer_dir(layer) in p.parents]
    assert inside, f"в tests/{layer}/ нет ни одного test_*.py"


@pytest.mark.parametrize(
    "layer,group",
    [(layer, group) for layer in sorted(REQUIRED_GROUPS)
     for group in REQUIRED_GROUPS[layer]],
)
def test_group_has_tests(layer, group):
    """В каждой группе есть хотя бы один тестовый файл."""
    found = tests_in(layer_dir(layer) / group)
    assert found, f"в tests/{layer}/{group}/ нет ни одного test_*.py"


# ─────────────────────────────────────────────────────────────
# 2. В корне tests/ ничего не забыто
# ─────────────────────────────────────────────────────────────
def test_no_test_files_left_in_root():
    """В корне tests/ остались только conftest.py и этот файл."""
    leftovers = sorted(p.name for p in tests_in(TESTS_ROOT)
                       if p.name not in ROOT_KEEP)
    assert leftovers == [], f"в корне tests/ остались тесты: {leftovers}"


def test_root_has_conftest():
    """Корневой conftest.py на месте — на нём держатся общие фикстуры."""
    assert (TESTS_ROOT / "conftest.py").is_file()


def test_every_test_file_lives_in_a_layer():
    """Каждый тест лежит в одном из трёх слоёв, а не в корне или в data/."""
    layers = set(REQUIRED_GROUPS)
    outside = []
    for path in all_test_files():
        relative = path.relative_to(TESTS_ROOT)
        if relative.name in ROOT_KEEP:
            continue
        if relative.parts[0] not in layers:
            outside.append(relative.as_posix())
    assert outside == [], f"тесты вне слоёв: {outside}"


# ─────────────────────────────────────────────────────────────
# 3. Состав слоёв совпадает с картой
# ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("layer", sorted(EXPECTED_FILES))
def test_layer_file_count_matches_map(layer):
    """В слое ровно столько файлов, сколько записано в карте Этапа 7."""
    found = [p for p in all_test_files() if layer_dir(layer) in p.parents]
    assert len(found) == EXPECTED_FILES[layer], (
        f"tests/{layer}/: {len(found)} файлов, в карте {EXPECTED_FILES[layer]}"
    )


def test_total_file_count():
    """Всего тестовых файлов — 144 (без test_tests_layout.py)."""
    found = [p for p in all_test_files() if p.name != "test_tests_layout.py"]
    assert len(found) == 144, f"тестовых файлов {len(found)}, ожидается 144"


def test_no_test_module_name_collision():
    """
    Имена тестовых модулей уникальны по всему дереву.

    Папки тестов — не пакеты (`__init__.py` нет), поэтому pytest грузит
    модуль по имени файла: два `test_db_facade.py` в разных слоях дали бы
    «import file mismatch» вместо честного прогона.
    """
    by_name: dict[str, list[str]] = {}
    for path in all_test_files():
        by_name.setdefault(path.name, []).append(
            path.relative_to(TESTS_ROOT).as_posix()
        )
    duplicates = {name: paths for name, paths in by_name.items() if len(paths) > 1}
    assert duplicates == {}, f"дубли имён тестовых модулей: {duplicates}"


def test_test_directories_are_not_packages():
    """
    В папках тестов нет `__init__.py`.

    Если папка станет пакетом, pytest начнёт искать тесты по имени пакета,
    а не по пути, — и весь смысл раскладки по слоям пропадёт.
    """
    packages = sorted(
        p.relative_to(TESTS_ROOT).as_posix()
        for p in TESTS_ROOT.rglob("__init__.py")
        if "_tmp" not in p.parts and "data" not in p.parts
    )
    assert packages == [], f"папки тестов стали пакетами: {packages}"


# ─────────────────────────────────────────────────────────────
# 4. Пути: старый способ счёта корня проекта больше не используется
# ─────────────────────────────────────────────────────────────
def test_no_stale_project_root_idiom():
    """
    Ни один тест не считает корень проекта через `parent.parent`/`parents[1]`.

    После переезда такой счёт даёт `tests/` вместо корня проекта — тихо
    ломаются пути к `templates/`, `main.py` и `tests/data/` (проверено:
    именно так упал `test_recognition_bank_name.py` при переезде).
    Сам этот файл исключён: в нём эти образцы лежат как тексты шаблонов.
    """
    offenders = []
    for path in all_test_files():
        if path.name == Path(__file__).name:
            continue
        source = read(path)
        for pattern in STALE_ROOT_IDIOMS:
            if pattern.search(source):
                offenders.append(path.relative_to(TESTS_ROOT).as_posix())
                break
    assert offenders == [], f"старый счёт корня проекта: {offenders}"


def test_root_of_tests_holds_only_service_files():
    """
    В корне tests/ — только conftest.py и сам этот файл.

    Проверка страхует счётчики слоёв: если в корне появится ещё один тест,
    `test_total_file_count` и карта разъедутся.
    """
    names = sorted(p.name for p in tests_in(TESTS_ROOT))
    assert names == ["test_tests_layout.py"], names
    assert (TESTS_ROOT / "conftest.py").is_file()


# ─────────────────────────────────────────────────────────────
# 5. Фикстуры данных указывают на существующие папки
# ─────────────────────────────────────────────────────────────
def test_golden_dir_fixture(golden_dir):
    """Фикстура golden_dir ведёт к tests/data/golden с эталонами."""
    assert golden_dir.is_dir()
    assert (golden_dir / "ooo.json").is_file()


def test_tests_data_dir_fixture(tests_data_dir):
    """Фикстура tests_data_dir ведёт к tests/data."""
    assert tests_data_dir.is_dir()
    assert (tests_data_dir / "golden").is_dir()
    assert (tests_data_dir / "synthetic_docs").is_dir()


def test_synthetic_docs_dir_fixture(synthetic_docs_dir):
    """Фикстура synthetic_docs_dir ведёт к tests/data/synthetic_docs."""
    assert synthetic_docs_dir.is_dir()
    assert (synthetic_docs_dir / "seeds.json").is_file()


def test_project_root_fixture(project_root):
    """Фикстура project_root — это корень проекта, а не папка тестов."""
    assert (project_root / "pytest.ini").is_file()
    assert (project_root / "main.py").is_file()
    assert (project_root / "tests").is_dir()


# ─────────────────────────────────────────────────────────────
# 6. Границы слоёв: unit без Qt и без БД
# ─────────────────────────────────────────────────────────────
def _imported_modules(path: Path) -> set[str]:
    """Имена модулей, импортируемых файлом (верхний уровень инструкции)."""
    tree = ast.parse(read(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                found.add(node.module)
    return found


@pytest.mark.parametrize("path", tests_in(layer_dir("unit"), recursive=True),
                         ids=lambda p: p.name)
def test_unit_layer_has_no_direct_qt_import(path):
    """Ни один unit-тест не импортирует PyQt5 напрямую."""
    qt = {name for name in _imported_modules(path) if name.startswith("PyQt5")}
    assert qt == set(), f"{path.name} тянет Qt: {sorted(qt)}"


@pytest.mark.parametrize("path", tests_in(layer_dir("unit"), recursive=True),
                         ids=lambda p: p.name)
def test_unit_layer_does_not_use_isolated_db(path):
    """Ни один unit-тест не просит фикстуру isolated_db."""
    tree = ast.parse(read(path))
    users = [
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(arg.arg == "isolated_db" for arg in node.args.args)
    ]
    assert users == [], f"{path.name} использует isolated_db: {users}"


def test_ui_conftest_sets_offscreen_first():
    """
    `tests/ui/conftest.py` ставит QT_QPA_PLATFORM ДО импорта PyQt5.

    Иначе Qt поднимает настоящее окно, и слой не запускается на сервере.
    """
    source = read(layer_dir("ui") / "conftest.py")
    offset_platform = source.index('os.environ.setdefault("QT_QPA_PLATFORM"')
    offset_pyqt = source.index("from PyQt5")
    assert offset_platform < offset_pyqt


def test_ui_conftest_provides_qt_app():
    """В `tests/ui/conftest.py` объявлена фикстура qt_app."""
    source = read(layer_dir("ui") / "conftest.py")
    assert "def qt_app(" in source


def test_integration_conftest_does_not_duplicate_isolated_db():
    """
    `isolated_db` объявлена ОДИН раз — в корневом conftest.py.

    Её используют и интеграционные тесты, и 27 файлов слоя ui/ (диалоги и
    вкладки работают с временной базой), поэтому фикстура живёт на уровне
    tests/. Вторая копия в integration/conftest.py разошлась бы с первой.
    """
    source = read(layer_dir("integration") / "conftest.py")
    assert "def isolated_db(" not in source
    assert "def isolated_db(" in read(TESTS_ROOT / "conftest.py")


# ─────────────────────────────────────────────────────────────
# 7. Импорты между тестами и синтаксис
# ─────────────────────────────────────────────────────────────
def test_no_broken_test_to_test_imports():
    """
    Импорты вида `from tests.<...>` указывают на существующие файлы.

    Сейчас таких импортов нет вовсе (проверено на подэтапе 1.I), но проверка
    остаётся сторожем: если тест начнёт импортировать соседний тест, путь
    обязан быть верным после переезда.
    """
    broken: list[str] = []
    for path in all_test_files():
        for module in _imported_modules(path):
            if module != "tests" and not module.startswith("tests."):
                continue
            relative = module.split(".")[1:]
            candidate = TESTS_ROOT.joinpath(*relative)
            if not (candidate.with_suffix(".py").is_file() or candidate.is_dir()):
                broken.append(f"{path.name}: {module}")
    assert broken == [], f"битые импорты между тестами: {broken}"


@pytest.mark.parametrize("path", all_test_files(), ids=lambda p: p.name)
def test_every_test_file_compiles(path):
    """Каждый тестовый файл разбирается как корректный Python."""
    ast.parse(read(path))


# ─────────────────────────────────────────────────────────────
# 8. pytest.ini: пути и маркеры слоёв
# ─────────────────────────────────────────────────────────────
def _pytest_ini() -> configparser.ConfigParser:
    # interpolation=None: в значениях pytest.ini встречается символ «%»
    # (он же разделитель подстановок configparser), и разбор падал бы.
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(PROJECT_ROOT / "pytest.ini", encoding="utf-8")
    return parser


def test_pytest_ini_testpaths():
    """`testpaths` указывает на tests."""
    assert _pytest_ini().get("pytest", "testpaths").strip() == "tests"


@pytest.mark.parametrize("marker", ["slow", "needs_private", "needs_db", "needs_qt"])
def test_pytest_ini_declares_marker(marker):
    """Маркеры слоёв объявлены — иначе pytest ругается PytestUnknownMark."""
    markers = _pytest_ini().get("pytest", "markers")
    assert re.search(rf"^\s*{marker}:", markers, re.M), f"нет маркера {marker}"


def test_pytest_ini_ignores_working_dirs():
    """Рабочая папка тестов и эталоны не собираются как тесты."""
    ignored = _pytest_ini().get("pytest", "norecursedirs").split()
    assert "_tmp" in ignored
    assert "data" in ignored
