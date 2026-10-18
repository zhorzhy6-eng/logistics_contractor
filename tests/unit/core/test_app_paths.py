#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты путей приложения (core/app_paths.py) — Часть 3 задания 18.10.2026.

Что проверяется
---------------
1. **Разработка.** `app_root()`, `bundle_root()` и все пути — корень проекта:
   поведение тестов и обычного запуска не меняется.
2. **Сборка.** Подменяются `sys.frozen`, `sys.executable` и `sys._MEIPASS`;
   проверяется главное правило: ЧИТАЕМОЕ берётся из `_MEIPASS`, а
   ЗАПИСЫВАЕМОЕ — рядом с `.exe`. Путать их нельзя: папка ресурсов сборки
   заменяется при обновлении, и записанное в неё пропадёт (а в
   `Program Files` и не запишется).
3. **Ни один модуль проекта больше не считает корень от `__file__`.**
   Именно этот счёт ломался в .exe: модули лежат в архиве PyInstaller, и
   папки рядом с «файлом» на диске нет.

Тесты не создают настоящих сборок: `sys` подменяется, как в
tests/unit/core/test_tls_config.py.
"""

import sys
from pathlib import Path

import pytest

from core import app_paths

#: Корень проекта — по маркеру, а не по числу parents.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)

#: Модули, которые отдают пути приложению. Корень они обязаны брать из
#: app_paths: счёт уровней от `__file__` в .exe указывает в архив.
PATH_OWNER_MODULES = (
    "core/contracts/paths.py",
    "core/legal_rules.py",
    "core/legal_parser.py",
    "core/private_path.py",
    "core/settings_service.py",
    "core/diagnostics.py",
    "core/qt_crash_handler.py",
    "core/contracts/base/generator.py",
    "config/logging_config.py",
    "db/connection.py",
    "db/salons.py",
    "db/crud/legal_metadata.py",
    "services/agent/core/prompt.py",
    "services/agent/tools/legal.py",
    "services/agent/tools/legal_compare.py",
    "services/agent/tools/tutorial.py",
    "services/agent/tools/template_analyzer.py",
    "ui/icons.py",
    "ui/main_window/window.py",
)


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """
    Программа «собрана PyInstaller»: проверяются оба корня.

    Раскладка `--onedir`, как у настоящей сборки::

        <tmp>/LogisticsContractor/
            LogisticsContractor.exe     ← папка программы (писать сюда)
            _internal/                  ← sys._MEIPASS (читать отсюда)
    """
    program_dir = tmp_path / "LogisticsContractor"
    internal_dir = program_dir / "_internal"
    internal_dir.mkdir(parents=True)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(program_dir / "LogisticsContractor.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(internal_dir), raising=False)
    return program_dir, internal_dir


@pytest.fixture
def not_frozen(monkeypatch):
    """Обычный запуск из исходников: признаков сборки нет."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    return PROJECT_ROOT


# ─────────────────────────────────────────────────────────────
# 1. Разработка: всё — корень проекта
# ─────────────────────────────────────────────────────────────

def test_dev_root_is_project_root(not_frozen):
    """Корень проекта находится по маркеру (`pytest.ini`, `main.py`)."""
    assert app_paths.dev_root() == PROJECT_ROOT


def test_dev_mode_reports_not_frozen(not_frozen):
    """Признак сборки выключен — программа считает себя запущенной из исходников."""
    assert app_paths.is_frozen() is False


def test_dev_app_root_is_project_root(not_frozen):
    """В разработке папка программы — корень проекта."""
    assert app_paths.app_root() == PROJECT_ROOT


def test_dev_resource_path_points_into_project(not_frozen):
    """В разработке ресурсы читаются из корня проекта."""
    assert app_paths.resource_path("templates") == PROJECT_ROOT / "templates"
    assert app_paths.bundle_root() == PROJECT_ROOT


def test_dev_writable_path_points_into_project(not_frozen):
    """В разработке настройки и база пишутся в корень проекта."""
    assert (
        app_paths.writable_path("config/settings.json")
        == PROJECT_ROOT / "config" / "settings.json"
    )


# ─────────────────────────────────────────────────────────────
# 2. Сборка: читаем из _MEIPASS, пишем рядом с .exe
# ─────────────────────────────────────────────────────────────

def test_frozen_is_detected(frozen):
    """Признак сборки виден."""
    assert app_paths.is_frozen() is True


def test_frozen_app_root_is_exe_folder(frozen):
    """В сборке папка программы — папка с .exe, а не _internal."""
    program_dir, _internal = frozen
    assert app_paths.app_root() == program_dir


def test_frozen_bundle_root_is_meipass(frozen):
    """В сборке ресурсы читаются из `sys._MEIPASS`."""
    _program, internal = frozen
    assert app_paths.bundle_root() == internal


def test_frozen_resource_path_uses_meipass(frozen):
    """Шаблоны и правовая база берутся из папки ресурсов сборки."""
    _program, internal = frozen
    assert app_paths.resource_path("templates") == internal / "templates"
    assert app_paths.resource_path("data/knowledge/legal") == (
        internal / "data" / "knowledge" / "legal"
    )


def test_frozen_writable_path_uses_exe_folder(frozen):
    """
    Настройки, база, журналы и договоры пишутся рядом с .exe.

    Запись в `_MEIPASS` означала бы: настройки и договоры исчезнут при
    обновлении сборки, а в `Program Files` их вообще не записать.
    """
    program_dir, internal = frozen
    for relative in ("config/settings.json", "contracts.db", "logs", "output"):
        path = app_paths.writable_path(relative)
        assert path == program_dir / relative
        assert internal not in path.parents


def test_frozen_without_meipass_falls_back_to_app_root(monkeypatch, frozen):
    """Признак сборки есть, а `_MEIPASS` нет — ресурсы берутся рядом с .exe."""
    program_dir, _internal = frozen
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    assert app_paths.bundle_root() == program_dir


def test_frozen_without_executable_falls_back_to_project(monkeypatch, frozen):
    """Пустой `sys.executable` не роняет программу: корнем становится проект."""
    monkeypatch.setattr(sys, "executable", "")

    assert app_paths.app_root() == PROJECT_ROOT


# ─────────────────────────────────────────────────────────────
# 3. Общие правила сборки путей
# ─────────────────────────────────────────────────────────────

def test_empty_relative_returns_base(not_frozen):
    """Пустой путь — сама база (пригодится для корня ресурсов)."""
    assert app_paths.resource_path("") == PROJECT_ROOT
    assert app_paths.resource_path(None) == PROJECT_ROOT


def test_absolute_path_is_returned_as_is(not_frozen):
    """
    Абсолютный путь не приклеивается к базе.

    Иначе сертификат из настроек («C:/certs/ca.cer») превратился бы в
    «<корень>/C:/certs/ca.cer» — и «файл не найден» на живом пути.
    """
    absolute = PROJECT_ROOT / "resources" / "certs" / "ca.cer"

    assert app_paths.resource_path(absolute) == absolute
    assert app_paths.writable_path(absolute) == absolute


def test_path_accepts_path_object(not_frozen):
    """Относительный путь можно передать и как Path."""
    assert app_paths.resource_path(Path("templates")) == PROJECT_ROOT / "templates"


def test_ensure_dir_creates_nested_folder(tmp_path):
    """`ensure_dir` создаёт папку вместе с родителями."""
    target = tmp_path / "a" / "b" / "c"

    assert app_paths.ensure_dir(target) == target
    assert target.is_dir()


def test_describe_names_mode_and_paths(not_frozen):
    """Строка диагностики называет режим, программу и папку ресурсов."""
    text = app_paths.describe()

    assert "разработка" in text
    assert str(PROJECT_ROOT) in text


def test_describe_marks_exe_mode(frozen):
    """В сборке диагностика пишет «exe», а не «разработка»."""
    assert "режим: exe" in app_paths.describe()


# ─────────────────────────────────────────────────────────────
# 4. Модули проекта больше не считают корень от __file__
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("relative", PATH_OWNER_MODULES)
def test_module_has_no_file_based_root(relative):
    """
    В модуле нет счёта корня от `__file__` и есть обращение к app_paths.

    Счёт уровней (`parents[3]`, `dirname(dirname(__file__))`) — ровно то,
    что ломалось в сборке: модуль лежит в архиве PyInstaller, и папки
    рядом с ним на диске нет. Проверка сторожит, чтобы при следующей
    правке старый способ не вернулся.
    """
    source = (PROJECT_ROOT / relative).read_text(encoding="utf-8")

    assert "app_paths" in source, f"{relative}: корень считается не через app_paths"
    forbidden = (
        "resolve().parents[",
        "dirname(os.path.dirname(os.path.abspath(__file__))",
        "dirname(dirname(os.path.abspath(__file__))",
    )
    for pattern in forbidden:
        assert pattern not in source, f"{relative}: старый счёт корня ({pattern})"


# ─────────────────────────────────────────────────────────────
# 5. Живые пути проекта указывают на существующие папки
# ─────────────────────────────────────────────────────────────

def test_templates_dir_exists(not_frozen):
    """Папка шаблонов проекта находится и это папка."""
    from core.contracts import paths as contract_paths

    assert contract_paths.TEMPLATES_DIR.is_dir()
    assert contract_paths.TEMPLATES_DIR == PROJECT_ROOT / "templates"


def test_output_dir_is_writable_side(not_frozen):
    """Готовые договоры пишутся в `output/` рядом с программой."""
    from core.contracts import paths as contract_paths

    assert contract_paths.OUTPUT_DIR == PROJECT_ROOT / "output"


def test_settings_path_is_writable_side(not_frozen, monkeypatch):
    """Настройки читаются и пишутся в `config/settings.json` у программы."""
    monkeypatch.delenv("LOGISTICS_SETTINGS_PATH", raising=False)

    from core.settings_service import default_settings_path

    assert Path(default_settings_path()) == PROJECT_ROOT / "config" / "settings.json"


def test_logs_dir_is_writable_side(not_frozen):
    """Журналы пишутся в `logs/` рядом с программой."""
    from core.qt_crash_handler import default_logs_dir

    assert default_logs_dir() == PROJECT_ROOT / "logs"


def test_database_path_is_writable_side(not_frozen):
    """База лежит рядом с программой, а не в папке кода."""
    from db import connection

    assert Path(connection.DB_PATH) == PROJECT_ROOT / "contracts.db"


def test_private_locations_include_bundle_and_program(monkeypatch):
    """
    Приватная часть ищется и внутри сборки, и рядом с .exe.

    В `--onedir` это разные папки, и приватная часть может приехать в
    любую из них (см. spec-файл сборки).
    """
    monkeypatch.delenv("LOGISTICS_PRIVATE_ROOT", raising=False)

    from core import private_path

    locations = [str(path) for path in private_path.default_locations()]

    assert str(app_paths.resource_path("logistics_contractor_private")) in locations
    assert str(app_paths.writable_path("logistics_contractor_private")) in locations
