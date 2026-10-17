#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты TLS-конфигурации: поиск CA-сертификата (infrastructure/api/tls.py).

Регресс на баг Этапа 3: модуль переехал из `core/tls_config.py` в
`infrastructure/api/tls.py`, а `certs_dir()` продолжала считать корень
проекта двумя `dirname` от `__file__`. После переезда это давало
`infrastructure/resources/certs` вместо `resources/certs`, и сертификат
«пропадал»: `resolve_ca_bundle()` возвращал None, requests падал с
`SSLCertVerificationError` на токене GigaChat.

Главная проверка здесь — не «путь сейчас верный», а «поиск корня не
зависит от глубины модуля»: `test_find_project_root_survives_module_move`
переносит `__file__` на два уровня глубже и требует тот же корень.
Именно эта проверка ловит повторение бага при следующем переезде.
"""

import os
from pathlib import Path

import pytest

from infrastructure.api import tls as tls_module
from infrastructure.api.tls import (
    CA_ENV_VAR,
    CA_FILENAMES,
    CERTS_SUBDIR,
    certs_dir,
    default_ca_bundle,
    resolve_ca_bundle,
)


def _project_root() -> Path:
    """
    Корень проекта — по маркеру, а не по числу `parents[N]`.

    Тот же приём, что в `tests/test_tests_layout.py`: счёт уровней вверх
    ломается при переезде файла ровно так же, как он сломал `certs_dir()`.
    """
    return next(
        path for path in Path(__file__).resolve().parents
        if (path / "pytest.ini").is_file()
    )


#: Корень проекта — по маркеру.
PROJECT_ROOT = _project_root()


# ─────────────────────────────────────────────────────────────
# 1. Корень проекта и папка сертификатов
# ─────────────────────────────────────────────────────────────

def test_project_root_marker_exists():
    """Маркер main.py есть — поиск корня по маркеру сработает."""
    assert (PROJECT_ROOT / "main.py").exists()
    assert (PROJECT_ROOT / "pytest.ini").exists()


def test_certs_dir_is_in_project_root():
    """
    `certs_dir()` указывает на `<корень>/resources/certs`.

    Без фикса здесь было `<корень>/infrastructure/resources/certs`.
    """
    expected = PROJECT_ROOT / CERTS_SUBDIR
    assert Path(certs_dir()).resolve() == expected.resolve()


def test_certs_dir_contains_certificate():
    """В `certs_dir()` лежит хотя бы один файл из CA_FILENAMES."""
    directory = Path(certs_dir())
    assert directory.is_dir(), f"нет папки {directory}"
    found = [name for name in CA_FILENAMES if (directory / name).exists()]
    assert found, f"в {directory} нет ни одного из {CA_FILENAMES}"


def test_find_project_root_survives_module_move(monkeypatch):
    """
    Поиск корня не зависит от глубины модуля — это и есть регресс.

    `__file__` подменяется на путь НА ДВА УРОВНЯ ГЛУБЖЕ настоящего
    (`infrastructure/api/deep/nested/tls.py`). Старый счёт «два dirname от
    __file__» вернул бы `infrastructure/api/deep`, поиск по маркеру обязан
    вернуть корень проекта.
    """
    deeper = Path(tls_module.__file__).parent / "deep" / "nested" / "tls.py"
    monkeypatch.setattr(tls_module, "__file__", str(deeper))

    assert Path(tls_module._find_project_root()).resolve() == PROJECT_ROOT.resolve()
    assert Path(certs_dir()).resolve() == (PROJECT_ROOT / CERTS_SUBDIR).resolve()


def test_find_project_root_fallback_without_marker(monkeypatch):
    """
    Без маркера работает старый fallback — два `dirname` от `__file__`.

    Это путь собранного exe (PyInstaller): в `_MEIPASS` ни `main.py`, ни
    `pytest.ini` нет. Путь намеренно длиннее шести уровней, поэтому маркер
    не может найтись случайно ни в одном предке.
    """
    fake = Path("C:/build/inner/a/b/c/d/e/f/g/tls.py")
    monkeypatch.setattr(tls_module, "__file__", str(fake))

    expected = Path(os.path.dirname(os.path.dirname(str(fake))))
    assert Path(tls_module._find_project_root()) == expected


# ─────────────────────────────────────────────────────────────
# 2. Поиск CA-файла
# ─────────────────────────────────────────────────────────────

def test_resolve_ca_bundle_finds_certificate():
    """
    `resolve_ca_bundle()` возвращает путь к существующему файлу.

    Это и есть рабочий отказ: без фикса `certs_dir()` уходила в
    `infrastructure/resources/certs`, функция возвращала None, и requests
    падал с `SSLCertVerificationError` на токене GigaChat.
    """
    resolved = resolve_ca_bundle()
    assert resolved is not None, (
        "resolve_ca_bundle() вернул None — сертификат не найден. "
        "Скорее всего, certs_dir() снова считает путь от __file__ "
        "и уходит в infrastructure/resources/certs."
    )
    assert os.path.isfile(resolved), f"файл не существует: {resolved}"


def test_resolve_ca_bundle_explicit_path():
    """Явный существующий путь из настроек возвращается как есть."""
    existing = resolve_ca_bundle()
    assert existing is not None
    assert resolve_ca_bundle(explicit=existing) == existing


def test_resolve_ca_bundle_ignores_bad_explicit_path():
    """Несуществующий явный путь — fallback на автопоиск, не исключение."""
    resolved = resolve_ca_bundle(explicit="E:\\no\\such\\file.cer")
    if resolved is not None:
        assert os.path.isfile(resolved)


def test_default_ca_bundle_returns_none_when_nothing_found(monkeypatch):
    """
    Сертификата нет нигде — None, а не исключение.

    Отсутствие НУЦ Минцифры в проекте не означает, что его нет в хранилище
    Windows, поэтому это штатный ответ (requests пойдёт с системным
    набором), а не ошибка.
    """
    monkeypatch.setattr(tls_module, "_settings_ca_bundle", lambda: "")
    monkeypatch.setattr(tls_module, "_search_dirs", lambda: [])
    monkeypatch.delenv(CA_ENV_VAR, raising=False)

    assert default_ca_bundle() is None


def test_env_var_bundle_used_when_nothing_else_found(monkeypatch, tmp_path):
    """
    `LOGISTICS_CA_BUNDLE` — последний источник поиска (тесты, CI, сборки).

    Остальные источники отключены, поэтому видно именно вклад переменной.
    """
    cert = tmp_path / "russian_trusted_root_ca.cer"
    cert.write_bytes(b"test ca")
    monkeypatch.setattr(tls_module, "_settings_ca_bundle", lambda: "")
    monkeypatch.setattr(tls_module, "_search_dirs", lambda: [])
    monkeypatch.setenv(CA_ENV_VAR, str(cert))

    assert default_ca_bundle() == str(cert)


def test_bad_env_var_bundle_is_ignored(monkeypatch):
    """Переменная окружения с несуществующим файлом не подменяет результат."""
    monkeypatch.setattr(tls_module, "_settings_ca_bundle", lambda: "")
    monkeypatch.setattr(tls_module, "_search_dirs", lambda: [])
    monkeypatch.setenv(CA_ENV_VAR, "E:\\no\\such\\file.cer")

    assert default_ca_bundle() is None


@pytest.mark.parametrize("name", CA_FILENAMES)
def test_ca_filenames_are_plain_file_names(name):
    """
    CA_FILENAMES — простые имена файлов, а не пути.

    `_find_ca_file` склеивает их с папкой поиска; путь внутри имени дал бы
    молчаливый промах при любом расположении сертификата.
    """
    assert os.path.basename(name) == name
    assert not os.path.isabs(name)
