#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты подготовки папки данных при старте (main.py::_prepare_user_data).

Что проверяется
---------------
Данные пользователя (`contracts.db`, `config/settings.json`, журналы,
готовые договоры) живут в папке данных — `%APPDATA%\\LogisticsContractor`
в сборке (см. core/data_paths.py), а не рядом с программой. При старте
`main.py` обязан ДО первого обращения к базе:

1. перенести данные старых версий из папки программы (копированием);
2. создать папку данных и подпапки;
3. положить `settings.json` из образца, если файла ещё нет.

Почему это отдельный тест, а не часть тестов data_paths: здесь проверяется
СВЯЗКА — что main.py действительно зовёт эти функции в нужном порядке и не
теряет старую базу. Ошибка в связке тихая: программа запускается с пустой
базой, и «договоры пропали» обнаруживается через неделю.

`main.py` импортируется с подменённым `sys.argv` и заглушкой
`setup_logging` — как в tests/integration/infrastructure/test_logging_config.py:
настоящая настройка логирования здесь не нужна, а глобальные хендлеры
помешали бы остальным тестам.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

from core import data_paths

#: Корень проекта — по маркеру, а не по числу parents (см. docs/STATE.md).
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """Папка данных во временной папке теста (настоящий `%APPDATA%` не трогаем)."""
    root = tmp_path / "appdata" / data_paths.APP_NAME
    monkeypatch.setenv(data_paths.ENV_DATA_ROOT, str(root))
    monkeypatch.delenv("LOGISTICS_SETTINGS_PATH", raising=False)
    return root


@pytest.fixture
def old_program_dir(tmp_path):
    """Папка программы старой версии: база и настройки лежали рядом с .exe."""
    root = tmp_path / "program"
    (root / "config").mkdir(parents=True)
    with sqlite3.connect(str(root / "contracts.db")) as conn:
        conn.execute("CREATE TABLE contracts (name TEXT)")
        conn.execute("INSERT INTO contracts VALUES ('старый договор')")
        conn.commit()
    (root / "config" / "settings.json").write_text(
        '{"provider": "старый"}', encoding="utf-8"
    )
    return root


@pytest.fixture
def main_module(monkeypatch):
    """
    `main.py`, импортированный с подменённым argv и заглушкой логирования.

    Модуль удаляется из `sys.modules` после импорта: иначе следующий тест
    получил бы кэш с чужим `_ARGS`, а тесты логирования не смогли бы
    импортировать main.py заново.
    """
    import importlib

    import config.logging_config as logging_config

    monkeypatch.setattr(logging_config, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    sys.modules.pop("main", None)

    module = importlib.import_module("main")
    sys.modules.pop("main", None)
    return module


@pytest.fixture
def migration_source(monkeypatch, old_program_dir):
    """
    «Папка программы» для миграции подменяется временной.

    В разработке `app_root_for_migration()` — корень проекта, и без подмены
    тест копировал бы рабочую `contracts.db` оператора.
    """
    monkeypatch.setattr(
        data_paths, "app_root_for_migration", lambda: old_program_dir
    )
    return old_program_dir


@pytest.fixture
def no_old_data(monkeypatch, tmp_path):
    """Старых данных нет: проверяем только папку данных, без миграции."""
    monkeypatch.setattr(
        data_paths, "app_root_for_migration", lambda: tmp_path / "нет_такой"
    )


# ─────────────────────────────────────────────────────────────
# 1. Папка данных и настройки
# ─────────────────────────────────────────────────────────────

def test_prepare_creates_data_folders(main_module, data_root):
    """После подготовки папка данных и подпапки существуют."""
    main_module._prepare_user_data()

    assert data_root.is_dir()
    for name in data_paths.SUBDIRS:
        assert (data_root / name).is_dir(), name


def test_prepare_copies_settings_from_example(main_module, data_root, no_old_data):
    """Первый запуск: settings.json появляется из образца."""
    assert not data_paths.config_path().exists()

    main_module._prepare_user_data()

    assert data_paths.config_path().is_file()
    assert data_paths.config_path().read_text(encoding="utf-8") == (
        data_paths.settings_example_path().read_text(encoding="utf-8")
    )


def test_prepare_keeps_existing_settings(main_module, data_root, no_old_data):
    """Существующие настройки оператора не перезаписываются."""
    data_paths.config_path().parent.mkdir(parents=True)
    data_paths.config_path().write_text('{"provider": "ollama"}', encoding="utf-8")

    main_module._prepare_user_data()

    assert data_paths.config_path().read_text(encoding="utf-8") == (
        '{"provider": "ollama"}'
    )


def test_prepare_skips_example_when_path_is_overridden(
    main_module, data_root, no_old_data, monkeypatch, tmp_path
):
    """LOGISTICS_SETTINGS_PATH задан — в папке данных настроек не создаём."""
    monkeypatch.setenv("LOGISTICS_SETTINGS_PATH", str(tmp_path / "custom.json"))

    main_module._prepare_user_data()

    assert not data_paths.config_path().exists()


# ─────────────────────────────────────────────────────────────
# 2. Миграция данных старой версии при старте
# ─────────────────────────────────────────────────────────────

def test_prepare_migrates_old_database(main_module, data_root, migration_source):
    """Старая база из папки программы оказывается в папке данных."""
    main_module._prepare_user_data()

    assert data_paths.db_path().is_file()
    with sqlite3.connect(str(data_paths.db_path())) as conn:
        names = [row[0] for row in conn.execute("SELECT name FROM contracts")]
    assert names == ["старый договор"]


def test_prepare_migrates_old_settings(main_module, data_root, migration_source):
    """Старые настройки переезжают, если новых ещё нет."""
    main_module._prepare_user_data()

    assert data_paths.config_path().read_text(encoding="utf-8") == '{"provider": "старый"}'


def test_prepare_keeps_old_files(main_module, data_root, migration_source):
    """Старая папка программы не чистится: решает пользователь."""
    main_module._prepare_user_data()

    assert (migration_source / "contracts.db").is_file()
    assert (migration_source / "config" / "settings.json").is_file()


def test_prepare_does_not_overwrite_new_database(main_module, data_root,
                                                 migration_source):
    """Есть база в папке данных — старая её не затирает."""
    data_paths.ensure_data_root()
    with sqlite3.connect(str(data_paths.db_path())) as conn:
        conn.execute("CREATE TABLE contracts (name TEXT)")
        conn.execute("INSERT INTO contracts VALUES ('новый договор')")
        conn.commit()

    main_module._prepare_user_data()

    with sqlite3.connect(str(data_paths.db_path())) as conn:
        names = [row[0] for row in conn.execute("SELECT name FROM contracts")]
    assert names == ["новый договор"]


def test_prepare_twice_is_safe(main_module, data_root, migration_source):
    """Повторный запуск ничего не дублирует и не падает."""
    main_module._prepare_user_data()
    main_module._prepare_user_data()

    assert data_paths.db_path().is_file()
    assert data_paths.config_path().is_file()


def test_prepare_survives_missing_old_data(main_module, data_root, monkeypatch,
                                           tmp_path):
    """Старых данных нет — программа всё равно получает папку данных."""
    monkeypatch.setattr(
        data_paths, "app_root_for_migration", lambda: tmp_path / "нет_такой"
    )

    main_module._prepare_user_data()

    assert data_root.is_dir()
    assert data_paths.config_path().is_file()


def test_prepare_logs_data_location(main_module, data_root, migration_source,
                                    caplog):
    """В журнал попадает, где именно лежат данные: без этого не разобрать."""
    import logging

    with caplog.at_level(logging.INFO, logger="main"):
        main_module._prepare_user_data()

    assert any("Папка данных" in record.getMessage()
               for record in caplog.records if record.name == "main")
