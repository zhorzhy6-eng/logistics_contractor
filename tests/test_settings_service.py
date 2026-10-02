#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты сервиса настроек (core/settings_service.py).

Проверяется жизненный цикл: загрузка, перечитывание, обновление, сохранение,
устойчивость к битому JSON и защита секретов.
"""

import json
import os

import pytest

from core.settings_service import (
    SettingsService,
    default_settings_path,
    get_settings_service,
    reset_settings_service,
)


@pytest.fixture
def settings_path(work_file):
    return work_file("settings.json")


@pytest.fixture
def service(settings_path):
    return SettingsService(str(settings_path))


# ─────────────────────────────────────────────────────────────
# Загрузка
# ─────────────────────────────────────────────────────────────

def test_defaults_when_file_missing(service):
    assert service.get_str("provider") == "gigachat"
    assert service.get_str("gigachat_model") == "GigaChat-2"
    assert service.get_int("gigachat_timeout") == 150
    assert service.get_bool("gigachat_verify_ssl") is True


def test_loads_existing_file(settings_path):
    settings_path.write_text(json.dumps({
        "provider": "gigachat",
        "gigachat_model": "GigaChat-2-Pro",
        "gigachat_timeout": 300,
        "gigachat_verify_ssl": False,
    }, ensure_ascii=False), encoding="utf-8")

    service = SettingsService(str(settings_path))
    assert service.get_str("gigachat_model") == "GigaChat-2-Pro"
    assert service.get_int("gigachat_timeout") == 300
    assert service.get_bool("gigachat_verify_ssl") is False


def test_missing_keys_fall_back_to_defaults(settings_path):
    settings_path.write_text(json.dumps({"gigachat_model": "Своя"}), encoding="utf-8")
    service = SettingsService(str(settings_path))
    assert service.get_str("gigachat_model") == "Своя"
    assert service.get_int("gigachat_timeout") == 150       # из DEFAULTS
    assert service.get_str("provider") == "gigachat"


def test_broken_json_does_not_crash(settings_path, caplog):
    settings_path.write_text("{ это не JSON ", encoding="utf-8")
    with caplog.at_level("ERROR", logger="core.settings_service"):
        service = SettingsService(str(settings_path))
    assert service.get_str("gigachat_model") == "GigaChat-2"
    assert "Ошибка разбора" in caplog.text


def test_json_array_instead_of_object(settings_path):
    settings_path.write_text("[1, 2, 3]", encoding="utf-8")
    service = SettingsService(str(settings_path))
    assert service.get_str("provider") == "gigachat"


# ─────────────────────────────────────────────────────────────
# Перечитывание и сохранение
# ─────────────────────────────────────────────────────────────

def test_reload_picks_up_external_changes(service, settings_path):
    settings_path.write_text(json.dumps({"gigachat_model": "Изменено снаружи"}),
                             encoding="utf-8")
    assert service.get_str("gigachat_model") == "GigaChat-2"   # до перечитывания
    service.reload()
    assert service.get_str("gigachat_model") == "Изменено снаружи"


def test_update_writes_file(service, settings_path):
    assert service.update({"gigachat_model": "GigaChat-2-Max"}) is True
    written = json.loads(settings_path.read_text(encoding="utf-8"))
    assert written["gigachat_model"] == "GigaChat-2-Max"


def test_update_without_save(service, settings_path):
    service.update({"gigachat_model": "Только в памяти"}, save=False)
    assert service.get_str("gigachat_model") == "Только в памяти"
    assert not settings_path.exists()


def test_unknown_keys_are_preserved(settings_path):
    settings_path.write_text(json.dumps({"custom_key": 42, "custom_flag": True}),
                             encoding="utf-8")
    service = SettingsService(str(settings_path))
    assert service.get("custom_key") == 42
    service.update({"gigachat_timeout": 200})
    written = json.loads(settings_path.read_text(encoding="utf-8"))
    assert written["custom_key"] == 42
    assert written["custom_flag"] is True


def test_new_instance_sees_saved_values(service, settings_path):
    service.update({"gigachat_scope": "GIGACHAT_API_B2B"})
    again = SettingsService(str(settings_path))
    assert again.get_str("gigachat_scope") == "GIGACHAT_API_B2B"


def test_save_failure_returns_false(service, monkeypatch):
    """Ошибка записи не роняет приложение, а возвращает False."""
    import builtins

    original_open = builtins.open

    def failing_open(file, mode="r", *args, **kwargs):
        if "w" in mode:
            raise OSError("диск только для чтения")
        return original_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failing_open)
    assert service.save() is False


def test_update_rejects_non_dict(service):
    assert service.update("строка") is False


# ─────────────────────────────────────────────────────────────
# Секреты не должны попадать в файл
# ─────────────────────────────────────────────────────────────

def test_secret_key_is_not_saved(service, settings_path):
    service.update({"gigachat_credentials": "СЕКРЕТ", "gigachat_model": "GigaChat-2"})
    written = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "gigachat_credentials" not in written
    assert "СЕКРЕТ" not in settings_path.read_text(encoding="utf-8")


def test_legacy_secret_is_removed_from_file(settings_path):
    """Ключ, оставшийся в старом settings.json, вычищается при загрузке."""
    settings_path.write_text(json.dumps({
        "gigachat_credentials": "СТАРЫЙ-КЛЮЧ",
        "provider": "gigachat",
    }), encoding="utf-8")

    service = SettingsService(str(settings_path))
    written = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "gigachat_credentials" not in written
    assert "СТАРЫЙ-КЛЮЧ" not in settings_path.read_text(encoding="utf-8")
    assert service.get_str("provider") == "gigachat"


def test_legacy_suffixed_secret_is_removed_from_file(settings_path):
    """Унаследованные ключи вида *_api_key тоже вычищаются при загрузке."""
    settings_path.write_text(json.dumps({
        "legacy_cloud_api_key": "СТАРЫЙ-КЛЮЧ",
        "provider": "gigachat",
    }), encoding="utf-8")

    SettingsService(str(settings_path))
    written = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "legacy_cloud_api_key" not in written
    assert "СТАРЫЙ-КЛЮЧ" not in settings_path.read_text(encoding="utf-8")


def test_defaults_have_no_secret_fields():
    assert "gigachat_credentials" not in SettingsService.DEFAULTS
    assert "gigachat_credentials" in SettingsService.SECRET_KEYS


# ─────────────────────────────────────────────────────────────
# Типизация значений
# ─────────────────────────────────────────────────────────────

def test_get_str_strips_and_handles_none(settings_path):
    settings_path.write_text(json.dumps({"x": "  значение  ", "y": None}),
                             encoding="utf-8")
    service = SettingsService(str(settings_path))
    assert service.get_str("x") == "значение"
    assert service.get_str("y") == ""
    assert service.get_str("missing", "по умолчанию") == "по умолчанию"


def test_get_int_with_bad_value(settings_path):
    settings_path.write_text(json.dumps({"gigachat_timeout": "не число"}),
                             encoding="utf-8")
    service = SettingsService(str(settings_path))
    assert service.get_int("gigachat_timeout", 150) == 150


@pytest.mark.parametrize("value, expected", [
    (True, True), (False, False),
    ("true", True), ("yes", True), ("да", True), ("1", True), ("on", True),
    ("false", False), ("no", False), ("0", False), ("", False),
])
def test_get_bool_variants(settings_path, value, expected):
    settings_path.write_text(json.dumps({"flag": value}), encoding="utf-8")
    service = SettingsService(str(settings_path))
    assert service.get_bool("flag") is expected


def test_as_dict_returns_copy(service):
    data = service.as_dict()
    data["gigachat_model"] = "Испорчено"
    assert service.get_str("gigachat_model") == "GigaChat-2"


def test_path_property(service, settings_path):
    assert service.path == str(settings_path)


# ─────────────────────────────────────────────────────────────
# Общий экземпляр и путь по умолчанию
# ─────────────────────────────────────────────────────────────

def test_singleton_returns_same_instance(work_file, monkeypatch):
    reset_settings_service()
    monkeypatch.setenv("LOGISTICS_SETTINGS_PATH", str(work_file("shared.json")))
    try:
        first = get_settings_service()
        second = get_settings_service()
        assert first is second
    finally:
        reset_settings_service()


def test_env_var_overrides_path(work_file, monkeypatch):
    custom = work_file("custom.json")
    monkeypatch.setenv("LOGISTICS_SETTINGS_PATH", str(custom))
    assert default_settings_path() == str(custom)


def test_default_path_without_env(monkeypatch, project_root):
    monkeypatch.delenv("LOGISTICS_SETTINGS_PATH", raising=False)
    assert default_settings_path() == str(project_root / "config" / "settings.json")


def test_real_settings_file_is_valid(project_root):
    """Рабочий config/settings.json читается и не содержит ключа."""
    path = project_root / "config" / "settings.json"
    if not path.exists():
        pytest.skip("config/settings.json отсутствует")
    service = SettingsService(str(path))
    assert service.get_str("provider") in ("gigachat", "ollama")
    assert "gigachat_credentials" not in service.as_dict()
