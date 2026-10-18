#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты путей к данным пользователя (core/data_paths.py).

Зачем шаг
---------
Данные (`contracts.db`, `config/settings.json`, `logs/`, `output/`) больше не
лежат рядом с программой: при обновлении папку программы заменяют, и данные
пропадали. Теперь они в `%APPDATA%\\LogisticsContractor`, а этот файл
сторожит три вещи:

1. **Путь правильный.** В сборке — `%APPDATA%`, в разработке —
   `tests/_tmp/_appdata`, `LOGISTICS_DATA_ROOT` главнее всего. Ошибка здесь
   тихая: программа работает, но пишет не туда.
2. **Папки создаются.** `sqlite3.connect` создаёт файл, но не папку — без
   `ensure_data_root()` первый запуск падал бы на пустом месте.
3. **Миграция не теряет и не затирает.** Старая база копируется (включая
   данные из журнала WAL), существующие данные не перезаписываются, а
   старые файлы не удаляются.

Все тесты работают во временных папках: настоящие `%APPDATA%` и
`config/settings.json` оператора не трогаются.
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
# Фикстуры: режимы запуска и изолированная папка данных
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def not_frozen(monkeypatch):
    """Запуск из исходников: признаков сборки нет."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    return PROJECT_ROOT


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """
    Собранный `.exe`: `sys.frozen` есть, программа лежит в своей папке.

    Раскладка `--onedir`, как у настоящей сборки: `.exe` в папке программы,
    ресурсы — в `_internal`.
    """
    program_dir = tmp_path / "LogisticsContractor"
    internal_dir = program_dir / "_internal"
    internal_dir.mkdir(parents=True)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(program_dir / "LogisticsContractor.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(internal_dir), raising=False)
    return program_dir


@pytest.fixture
def no_env(monkeypatch):
    """Переменная портативного режима не задана."""
    monkeypatch.delenv(data_paths.ENV_DATA_ROOT, raising=False)


@pytest.fixture
def data_root_in_tmp(monkeypatch, tmp_path):
    """
    Папка данных во временной папке теста.

    Подменяется `LOGISTICS_DATA_ROOT`: и настоящий `%APPDATA%`, и
    `tests/_tmp/_appdata` остаются нетронутыми.
    """
    root = tmp_path / "appdata" / data_paths.APP_NAME
    monkeypatch.setenv(data_paths.ENV_DATA_ROOT, str(root))
    return root


@pytest.fixture
def old_program_dir(tmp_path):
    """
    Папка программы СТАРОЙ версии: данные лежали рядом с `.exe`.

    Ровно так выглядит установленная v1.4.0: `contracts.db`,
    `config/settings.json`, `logs/`, `output/`.
    """
    root = tmp_path / "program"
    (root / "config").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "output").mkdir()
    return root


# ─────────────────────────────────────────────────────────────
# 1. data_root(): стандартное место данных
# ─────────────────────────────────────────────────────────────

def test_app_name_is_used_as_folder_name():
    """Имя папки данных — ровно `LogisticsContractor`."""
    assert data_paths.APP_NAME == "LogisticsContractor"


def test_data_root_on_windows_is_appdata(monkeypatch, tmp_path):
    """Windows: `%APPDATA%\\LogisticsContractor`."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))

    assert data_paths.data_root() == tmp_path / "Roaming" / "LogisticsContractor"


def test_data_root_without_appdata_uses_roaming(monkeypatch, tmp_path):
    """`APPDATA` не задана — берётся тот же путь через домашнюю папку."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    expected = Path.home() / "AppData" / "Roaming" / "LogisticsContractor"
    assert data_paths.data_root() == expected


def test_data_root_on_linux_uses_local_share(monkeypatch, tmp_path):
    """Linux/macOS: `~/.local/share/LogisticsContractor` (стандарт XDG)."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    expected = Path.home() / ".local" / "share" / "LogisticsContractor"
    assert data_paths.data_root() == expected


# ─────────────────────────────────────────────────────────────
# 2. resolved_data_root(): что действует СЕЙЧАС
# ─────────────────────────────────────────────────────────────

def test_dev_root_is_tests_tmp_appdata(not_frozen, no_env):
    """Разработка: данные — в `tests/_tmp/_appdata`, а не в `%APPDATA%`."""
    root = data_paths.resolved_data_root()

    assert root == data_paths.data_dev_root()
    assert root.parts[-3:] == ("tests", "_tmp", "_appdata")
    assert root.is_relative_to(PROJECT_ROOT)


def test_frozen_root_is_appdata(frozen, no_env, monkeypatch, tmp_path):
    """Сборка: данные — в `%APPDATA%\\LogisticsContractor`, не рядом с `.exe`."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))

    root = data_paths.resolved_data_root()

    assert root == data_paths.data_root()
    assert root != frozen
    assert "Roaming" in root.parts


def test_env_overrides_dev_mode(data_root_in_tmp, not_frozen):
    """`LOGISTICS_DATA_ROOT` главнее режима разработки."""
    assert data_paths.resolved_data_root() == data_root_in_tmp
    assert data_paths.resolved_data_root() != data_paths.data_dev_root()


def test_env_overrides_frozen_mode(data_root_in_tmp, frozen):
    """`LOGISTICS_DATA_ROOT` главнее папки `%APPDATA%` — портативный режим."""
    assert data_paths.resolved_data_root() == data_root_in_tmp
    assert data_paths.resolved_data_root() != data_paths.data_root()


def test_empty_env_is_ignored(frozen, monkeypatch):
    """Пустая переменная — это «не задана», а не папка текущего каталога."""
    monkeypatch.setenv(data_paths.ENV_DATA_ROOT, "   ")

    assert data_paths.resolved_data_root() == data_paths.data_root()


# ─────────────────────────────────────────────────────────────
# 3. Конкретные пути внутри папки данных
# ─────────────────────────────────────────────────────────────

def test_db_path(data_root_in_tmp):
    """База: `<папка данных>/contracts.db`."""
    assert data_paths.db_path() == data_root_in_tmp / "contracts.db"


def test_config_path(data_root_in_tmp):
    """Настройки: `<папка данных>/config/settings.json`."""
    assert data_paths.config_path() == (
        data_root_in_tmp / "config" / "settings.json"
    )


def test_logs_dir(data_root_in_tmp):
    """Журналы: `<папка данных>/logs`."""
    assert data_paths.logs_dir() == data_root_in_tmp / "logs"


def test_output_dir(data_root_in_tmp):
    """Готовые договоры: `<папка данных>/output`."""
    assert data_paths.output_dir() == data_root_in_tmp / "output"


def test_all_paths_share_one_root(data_root_in_tmp):
    """Все пути — в ОДНОЙ папке данных: второго места для данных нет."""
    paths = (
        data_paths.db_path(),
        data_paths.config_path(),
        data_paths.logs_dir(),
        data_paths.output_dir(),
    )

    for path in paths:
        assert path.is_relative_to(data_root_in_tmp)


# ─────────────────────────────────────────────────────────────
# 4. ensure_data_root(): папки создаются до первого запуска
# ─────────────────────────────────────────────────────────────

def test_ensure_data_root_creates_folders(data_root_in_tmp):
    """Папка данных и обязательные подпапки создаются."""
    assert not data_root_in_tmp.exists()

    root = data_paths.ensure_data_root()

    assert root == data_root_in_tmp
    assert root.is_dir()
    for name in data_paths.SUBDIRS:
        assert (root / name).is_dir(), name


def test_ensure_data_root_keeps_existing_files(data_root_in_tmp):
    """Повторный вызов ничего не стирает и не падает."""
    data_root_in_tmp.mkdir(parents=True)
    marker = data_root_in_tmp / "contracts.db"
    marker.write_text("старые данные", encoding="utf-8")

    data_paths.ensure_data_root()
    data_paths.ensure_data_root()

    assert marker.read_text(encoding="utf-8") == "старые данные"


# ─────────────────────────────────────────────────────────────
# 5. settings.json: образец из сборки и первый запуск
# ─────────────────────────────────────────────────────────────

def test_settings_example_is_read_from_resources(not_frozen):
    """Образец настроек читается из ресурсов проекта (в .exe — из сборки)."""
    example = data_paths.settings_example_path()

    assert example == PROJECT_ROOT / "config" / "settings.example.json"
    assert example.is_file()


def test_ensure_settings_file_copies_example(data_root_in_tmp, not_frozen):
    """Первый запуск: `settings.json` появляется из образца."""
    target = data_paths.ensure_settings_file()

    assert target == data_paths.config_path()
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == (
        data_paths.settings_example_path().read_text(encoding="utf-8")
    )


def test_ensure_settings_file_does_not_overwrite(data_root_in_tmp):
    """Существующие настройки оператора не перезаписываются образцом."""
    target = data_paths.config_path()
    target.parent.mkdir(parents=True)
    target.write_text('{"provider": "ollama"}', encoding="utf-8")

    data_paths.ensure_settings_file()

    assert target.read_text(encoding="utf-8") == '{"provider": "ollama"}'


def test_ensure_settings_file_without_example_creates_nothing(
    data_root_in_tmp, tmp_path
):
    """Нет образца — файла нет: сервис настроек работает на умолчаниях."""
    missing = tmp_path / "nowhere" / "settings.example.json"

    target = data_paths.ensure_settings_file(missing)

    assert target == data_paths.config_path()
    assert not target.exists()


# ─────────────────────────────────────────────────────────────
# 6. app_root_for_migration(): где данные СТАРЫХ версий
# ─────────────────────────────────────────────────────────────

def test_migration_source_in_dev_is_project_root(not_frozen):
    """В разработке старые данные лежали в корне проекта."""
    assert data_paths.app_root_for_migration() == PROJECT_ROOT


def test_migration_source_in_build_is_exe_folder(frozen):
    """В сборке старые данные лежали рядом с `.exe`."""
    assert data_paths.app_root_for_migration() == frozen


# ─────────────────────────────────────────────────────────────
# 7. migrate_old_data(): копирование без потерь
# ─────────────────────────────────────────────────────────────

def _make_old_db(path: Path, value: str = "договор") -> None:
    """Старая база с одной таблицей и одной записью."""
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE contracts (name TEXT)")
        conn.execute("INSERT INTO contracts VALUES (?)", (value,))
        conn.commit()


def test_migration_copies_db_and_settings(old_program_dir, data_root_in_tmp):
    """База и настройки переезжают в папку данных, пути возвращаются."""
    _make_old_db(old_program_dir / "contracts.db")
    (old_program_dir / "config" / "settings.json").write_text(
        '{"provider": "gigachat"}', encoding="utf-8"
    )

    copied = data_paths.migrate_old_data(old_program_dir)

    assert len(copied) == 2
    assert str(old_program_dir / "contracts.db") in copied
    assert str(old_program_dir / "config" / "settings.json") in copied
    assert data_paths.db_path().is_file()
    assert data_paths.config_path().is_file()

    with sqlite3.connect(str(data_paths.db_path())) as conn:
        names = [row[0] for row in conn.execute("SELECT name FROM contracts")]
    assert names == ["договор"]


def test_migration_does_not_overwrite_new_data(old_program_dir, data_root_in_tmp):
    """Есть данные в новой папке — старые их не затирают."""
    _make_old_db(old_program_dir / "contracts.db", value="старый договор")
    data_paths.ensure_data_root()
    _make_old_db(data_paths.db_path(), value="новый договор")

    copied = data_paths.migrate_old_data(old_program_dir)

    assert copied == []
    with sqlite3.connect(str(data_paths.db_path())) as conn:
        names = [row[0] for row in conn.execute("SELECT name FROM contracts")]
    assert names == ["новый договор"]


def test_migration_keeps_existing_settings(old_program_dir, data_root_in_tmp):
    """Настройки новой версии главнее старых."""
    (old_program_dir / "config" / "settings.json").write_text(
        '{"provider": "старый"}', encoding="utf-8"
    )
    data_paths.config_path().parent.mkdir(parents=True)
    data_paths.config_path().write_text('{"provider": "новый"}', encoding="utf-8")

    data_paths.migrate_old_data(old_program_dir)

    assert data_paths.config_path().read_text(encoding="utf-8") == '{"provider": "новый"}'


def test_migration_does_not_copy_logs_and_output(old_program_dir, data_root_in_tmp):
    """Журналы и готовые договоры — не данные: их не копируем."""
    (old_program_dir / "logs" / "app.log").write_text("журнал", encoding="utf-8")
    (old_program_dir / "output" / "договор.docx").write_text("документ", encoding="utf-8")
    _make_old_db(old_program_dir / "contracts.db")

    data_paths.migrate_old_data(old_program_dir)

    assert not (data_root_in_tmp / "logs" / "app.log").exists()
    assert not (data_root_in_tmp / "output" / "договор.docx").exists()


def test_migration_keeps_old_files_in_place(old_program_dir, data_root_in_tmp):
    """Старые файлы не удаляются: их судьбу решает пользователь."""
    _make_old_db(old_program_dir / "contracts.db")

    data_paths.migrate_old_data(old_program_dir)

    assert (old_program_dir / "contracts.db").is_file()


def test_migration_missing_old_root_is_noop(tmp_path, data_root_in_tmp):
    """Старой папки нет — переносить нечего, ошибки нет."""
    assert data_paths.migrate_old_data(tmp_path / "нет_такой_папки") == []


def test_migration_same_root_is_noop(not_frozen, monkeypatch):
    """Старая и новая папка совпадают — копировать файл в себя нельзя."""
    monkeypatch.setenv(data_paths.ENV_DATA_ROOT, str(PROJECT_ROOT))

    assert data_paths.migrate_old_data(PROJECT_ROOT) == []


def test_migration_keeps_data_from_wal(old_program_dir, data_root_in_tmp):
    """
    Данные из журнала WAL не теряются.

    В режиме WAL последние транзакции лежат не в `contracts.db`, а в
    `contracts.db-wal`. Побайтовое копирование одного файла потеряло бы их,
    поэтому база переносится через SQLite backup.
    """
    old_db = old_program_dir / "contracts.db"
    conn = sqlite3.connect(str(old_db))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE contracts (name TEXT)")
        conn.execute("INSERT INTO contracts VALUES ('из журнала WAL')")
        conn.commit()
        assert old_db.with_name(old_db.name + "-wal").is_file()

        data_paths.migrate_old_data(old_program_dir)
    finally:
        conn.close()

    with sqlite3.connect(str(data_paths.db_path())) as check:
        names = [row[0] for row in check.execute("SELECT name FROM contracts")]
    assert names == ["из журнала WAL"]


def test_migration_creates_config_folder(old_program_dir, data_root_in_tmp):
    """Подпапка `config` создаётся до копирования настроек."""
    (old_program_dir / "config" / "settings.json").write_text("{}", encoding="utf-8")

    data_paths.migrate_old_data(old_program_dir)

    assert data_paths.config_path().is_file()


# ─────────────────────────────────────────────────────────────
# 8. describe(): строка для журнала
# ─────────────────────────────────────────────────────────────

def test_describe_mentions_env_source(data_root_in_tmp):
    """При портативном режиме видно, откуда взят путь."""
    text = data_paths.describe()

    assert str(data_root_in_tmp) in text
    assert data_paths.ENV_DATA_ROOT in text


def test_describe_mentions_dev_mode(not_frozen, no_env):
    """В разработке видно, что данные — не в `%APPDATA%`."""
    text = data_paths.describe()

    assert str(data_paths.data_dev_root()) in text
    assert "разработка" in text
