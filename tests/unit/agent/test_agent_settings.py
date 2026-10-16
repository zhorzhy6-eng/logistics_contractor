#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Настройки Максимилиана: чтение, запись, значения по умолчанию
(задание, часть H.4).

Что проверяется
---------------
1. **Дефолты.** Без файла и без раздела ``agent`` сервис отдаёт полный
   набор значений из задания (часть E.1).
2. **Чтение.** Значения из файла попадают в геттеры, включая ``enabled``.
3. **Запись.** ``update_agent`` меняет ТОЛЬКО раздел agent и сохраняет
   остальные настройки; файл остаётся валидным JSON.
4. **Нормализация.** Неизвестный режим, строка вместо числа, число вне
   границ и мусор в булевых полях не ломают программу: берутся значения
   по умолчанию.
5. **Совместимость.** Существующие ключи (провайдер, тема, таймауты) не
   затронуты, а раздел agent не мешает их читать.

Рабочий ``config/settings.json`` здесь не трогается: у каждого теста свой
файл в ``tests/_tmp``.
"""

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.settings_service import (  # noqa: E402
    AGENT_LIMITS, AGENT_MODES, DEFAULT_AGENT_SETTINGS, SettingsService,
    normalize_agent_settings,
)


@pytest.fixture
def settings_path(work_file):
    """Отдельный файл настроек для теста."""
    return work_file("settings.json")


@pytest.fixture
def service(settings_path):
    """Сервис настроек на пустом файле (значения по умолчанию)."""
    return SettingsService(str(settings_path))


# ─────────────────────────────────────────────────────────────
# 1. Значения по умолчанию
# ─────────────────────────────────────────────────────────────

def test_defaults_match_specification(service):
    """Дефолты раздела agent совпадают с заданием (часть E.1)."""
    values = service.agent_settings()
    assert values == DEFAULT_AGENT_SETTINGS
    assert values["enabled"] is True
    assert values["name"] == "Максимилиан"
    assert values["avatar"] == "cat_scientist"
    assert values["mode"] == "hybrid"
    assert values["show_floating"] is False
    assert values["show_panel_on_start"] is False
    assert values["hotkey"] == "Ctrl+M"
    assert values["max_history"] == 20
    assert values["max_tool_steps"] == 20


def test_agent_enabled_default_true(service):
    """Максимилиан включён по умолчанию."""
    assert service.agent_enabled() is True


def test_settings_file_contains_agent_section(settings_path):
    """Раздел agent появляется в файле после записи."""
    service = SettingsService(str(settings_path))
    service.update({"agent": {"avatar": "cat_logist"}})

    with open(settings_path, "r", encoding="utf-8") as file:
        raw = json.load(file)
    assert raw["agent"]["avatar"] == "cat_logist"
    assert raw["agent"]["mode"] == "hybrid"


def test_agent_setting_by_key(service):
    """Отдельные значения доступны по ключу."""
    assert service.agent_setting("hotkey") == "Ctrl+M"
    assert service.agent_setting("max_tool_steps") == 20
    # Неизвестный ключ — значение по умолчанию, без исключения.
    assert service.agent_setting("нет_такого_ключа", "запасное") == "запасное"


# ─────────────────────────────────────────────────────────────
# 2. Чтение из файла
# ─────────────────────────────────────────────────────────────

def test_reads_agent_section_from_file(settings_path):
    """Значения из файла читаются (включая выключенного агента)."""
    settings_path.write_text(json.dumps({
        "agent": {
            "enabled": False,
            "avatar": "cat_keeper",
            "mode": "icon",
            "show_floating": True,
            "hotkey": "Ctrl+Alt+M",
            "max_history": 7,
            "max_tool_steps": 2,
        }
    }, ensure_ascii=False), encoding="utf-8")

    service = SettingsService(str(settings_path))
    values = service.agent_settings()

    assert values["enabled"] is False
    assert values["avatar"] == "cat_keeper"
    assert values["mode"] == "icon"
    assert values["show_floating"] is True
    assert values["hotkey"] == "Ctrl+Alt+M"
    assert values["max_history"] == 7
    assert values["max_tool_steps"] == 2
    assert service.agent_enabled() is False


def test_partial_agent_section_is_completed(settings_path):
    """Частичный раздел дополняется умолчаниями, а не падает."""
    settings_path.write_text(json.dumps({"agent": {"avatar": "neutral"}}),
                             encoding="utf-8")
    service = SettingsService(str(settings_path))

    values = service.agent_settings()
    assert values["avatar"] == "neutral"
    assert values["mode"] == DEFAULT_AGENT_SETTINGS["mode"]
    assert values["max_history"] == DEFAULT_AGENT_SETTINGS["max_history"]


# ─────────────────────────────────────────────────────────────
# 3. Запись
# ─────────────────────────────────────────────────────────────

def test_update_agent_writes_file(settings_path, service):
    """update_agent сохраняет значения и возвращает True."""
    assert service.update_agent({"mode": "panel", "max_tool_steps": 3}) is True

    reloaded = SettingsService(str(settings_path))
    assert reloaded.agent_setting("mode") == "panel"
    assert reloaded.agent_setting("max_tool_steps") == 3


def test_update_agent_keeps_other_keys(settings_path):
    """Правка раздела agent не трогает остальные настройки."""
    settings_path.write_text(json.dumps({
        "provider": "gigachat",
        "ui_theme": "dark_pro",
        "gigachat_timeout": 42,
        "agent": {"enabled": True},
    }, ensure_ascii=False), encoding="utf-8")
    service = SettingsService(str(settings_path))

    service.update_agent({"avatar": "cat_logist"})

    reloaded = SettingsService(str(settings_path))
    assert reloaded.get_str("provider") == "gigachat"
    assert reloaded.get_str("ui_theme") == "dark_pro"
    assert reloaded.get_int("gigachat_timeout") == 42
    assert reloaded.agent_setting("avatar") == "cat_logist"


def test_update_agent_without_save(settings_path, service):
    """save=False меняет значения в памяти и не пишет файл."""
    assert service.update_agent({"mode": "icon"}, save=False) is True
    assert service.agent_setting("mode") == "icon"

    reloaded = SettingsService(str(settings_path))
    assert reloaded.agent_setting("mode") == DEFAULT_AGENT_SETTINGS["mode"]


def test_update_agent_rejects_non_dict(service):
    """Не словарь на входе — понятный отказ, а не исключение."""
    assert service.update_agent(["enabled"]) is False


def test_update_agent_ignores_secret_like_keys(settings_path, service):
    """В разделе agent не может появиться секрет по недосмотру."""
    service.update_agent({"gigachat_api_key": "12345", "mode": "icon"})

    with open(settings_path, "r", encoding="utf-8") as file:
        raw = json.load(file)
    assert "gigachat_api_key" not in raw["agent"]
    assert raw["agent"]["mode"] == "icon"


def test_reload_picks_up_manual_edit(settings_path, service):
    """Правка файла руками подхватывается reload() (без перезапуска)."""
    settings_path.write_text(json.dumps({"agent": {"hotkey": "Ctrl+J"}}),
                             encoding="utf-8")
    service.reload()
    assert service.agent_setting("hotkey") == "Ctrl+J"


# ─────────────────────────────────────────────────────────────
# 4. Нормализация мусора
# ─────────────────────────────────────────────────────────────

def test_unknown_mode_falls_back():
    """Неизвестный режим — режим по умолчанию."""
    assert normalize_agent_settings({"mode": "голограмма"})["mode"] == \
        DEFAULT_AGENT_SETTINGS["mode"]
    assert normalize_agent_settings({"mode": "PANEL"})["mode"] == "panel"


def test_modes_are_three():
    """Режимов ровно три: icon, panel, hybrid (задание, часть D.3)."""
    assert tuple(AGENT_MODES) == ("icon", "panel", "hybrid")


def test_numbers_out_of_range_fall_back():
    """Числа вне границ заменяются значениями по умолчанию."""
    low, high = AGENT_LIMITS["max_history"]
    assert normalize_agent_settings({"max_history": 0})["max_history"] == \
        DEFAULT_AGENT_SETTINGS["max_history"]
    assert normalize_agent_settings({"max_history": high + 1})["max_history"] == \
        DEFAULT_AGENT_SETTINGS["max_history"]
    assert normalize_agent_settings({"max_history": "много"})["max_history"] == \
        DEFAULT_AGENT_SETTINGS["max_history"]
    assert normalize_agent_settings({"max_history": 5})["max_history"] == 5
    # Границы объявлены и осмысленны.
    assert low >= 1 and high <= 200


def test_boolean_fields_accept_strings():
    """«нет»/«false» в булевых полях читаются как False."""
    values = normalize_agent_settings({
        "enabled": "нет", "show_floating": "false", "show_panel_on_start": "0",
    })
    assert values["enabled"] is False
    assert values["show_floating"] is False
    assert values["show_panel_on_start"] is False

    values = normalize_agent_settings({"enabled": "да", "show_floating": "on"})
    assert values["enabled"] is True
    assert values["show_floating"] is True


def test_empty_strings_fall_back():
    """Пустые строки заменяются значениями по умолчанию."""
    values = normalize_agent_settings({"name": "", "avatar": "  ", "hotkey": ""})
    assert values["name"] == DEFAULT_AGENT_SETTINGS["name"]
    assert values["avatar"] == DEFAULT_AGENT_SETTINGS["avatar"]
    assert values["hotkey"] == DEFAULT_AGENT_SETTINGS["hotkey"]


def test_agent_section_of_wrong_type_is_replaced(settings_path):
    """Раздел agent строкой вместо объекта не ломает настройки."""
    settings_path.write_text(json.dumps({"agent": "выключить"}),
                             encoding="utf-8")
    service = SettingsService(str(settings_path))
    assert service.agent_settings() == DEFAULT_AGENT_SETTINGS


def test_broken_file_uses_defaults(settings_path):
    """Битый JSON — значения по умолчанию, программа работает."""
    settings_path.write_text("{ это не json", encoding="utf-8")
    service = SettingsService(str(settings_path))
    assert service.agent_settings() == DEFAULT_AGENT_SETTINGS
    assert service.agent_enabled() is True


# ─────────────────────────────────────────────────────────────
# 5. Живой файл проекта
# ─────────────────────────────────────────────────────────────

def test_project_settings_file_has_agent_section():
    """
    В config/settings.json раздел agent есть, полон и читается (часть E.1).

    Значения НЕ сравниваются с умолчаниями: файл рабочий, оператор меняет его
    из диалога настроек (образ, режим, показ кота), и прогон не должен
    краснеть из-за выбранного образа. Что умолчания на месте — проверяет
    `test_example_settings_file_is_valid` по файлу-образцу, который лежит
    в репозитории.
    """
    from core.agent.personas import persona_keys
    from core.settings_service import AGENT_MODES

    project_settings = PROJECT_ROOT / "config" / "settings.json"
    data = json.loads(project_settings.read_text(encoding="utf-8"))
    assert "agent" in data, "раздел agent должен быть в рабочем файле настроек"

    agent = data["agent"]
    for key in DEFAULT_AGENT_SETTINGS:
        assert key in agent, f"в рабочем файле нет настройки {key!r}"

    assert agent["avatar"] in persona_keys(), agent["avatar"]
    assert agent["mode"] in AGENT_MODES, agent["mode"]
    assert isinstance(agent["max_history"], int)
    assert isinstance(agent["max_tool_steps"], int)


def test_example_settings_file_is_valid():
    """config/settings.example.json — валидный JSON с разделом agent."""
    example = PROJECT_ROOT / "config" / "settings.example.json"
    data = json.loads(example.read_text(encoding="utf-8"))
    assert "agent" in data
    for key, value in DEFAULT_AGENT_SETTINGS.items():
        assert data["agent"].get(key, value) == value, key


# ─────────────────────────────────────────────────────────────
# Режим «Обучение» (задание: система обучения)
# ─────────────────────────────────────────────────────────────

def test_tutorial_settings_default_off(service):
    """Режим «Обучение» и отметка первого запуска по умолчанию выключены."""
    values = service.agent_settings()
    assert values["tutorial_mode"] is False
    assert values["tutorial_seen"] is False


def test_tutorial_mode_bool_normalization():
    """Строки «да»/«1»/«true» превращаются в True, мусор — в False."""
    for raw in ("да", "1", "true", "yes", "on"):
        assert normalize_agent_settings({"tutorial_mode": raw})["tutorial_mode"] is True
    for raw in ("нет", "0", "false", "", "мусор"):
        assert normalize_agent_settings({"tutorial_mode": raw})["tutorial_mode"] is False


def test_tutorial_seen_bool_normalization():
    """Отметка первого запуска тоже приводится к булевому значению."""
    assert normalize_agent_settings({"tutorial_seen": "да"})["tutorial_seen"] is True
    assert normalize_agent_settings({"tutorial_seen": "0"})["tutorial_seen"] is False


def test_tutorial_settings_persist(service, settings_path):
    """update_agent сохраняет режим «Обучение» и отметку первого запуска."""
    assert service.update_agent({
        "tutorial_mode": True,
        "tutorial_seen": True,
    }) is True

    written = json.loads(settings_path.read_text(encoding="utf-8"))
    assert written["agent"]["tutorial_mode"] is True
    assert written["agent"]["tutorial_seen"] is True

    reloaded = SettingsService(str(settings_path))
    assert reloaded.agent_setting("tutorial_mode") is True
    assert reloaded.agent_setting("tutorial_seen") is True


def test_tutorial_keys_do_not_leak_into_other_sections(service, settings_path):
    """update_agent с режимом обучения не трогает остальные настройки."""
    service.update_agent({"tutorial_mode": True})
    assert service.get_str("provider") == "gigachat"
