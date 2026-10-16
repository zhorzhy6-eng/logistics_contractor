#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Раскладка слоя распознавания после Этапа 2 рефакторинга (16.10.2026).

Проверяет то, что нельзя проверить обычными тестами модулей:

1. Пять модулей ДЕЙСТВИТЕЛЬНО переехали: файлов в корне `core/` больше нет,
   а в `services/recognition/` — есть.
2. Старые импорты (`from core.document_reader import read_document`) работают
   и ведут к ТОМУ ЖЕ объекту модуля, что и новые пути. Это не копия: если
   импорты разъедутся на два разных модуля, тесты проекта начнут видеть два
   разных `DataMapper` и «плавающие» ошибки атрибутов.
3. Публичный API не изменился: имена, которыми пользуются UI, агент и
   приватная часть, доступны из обоих путей.
4. Ленивое взятие соседей внутри `import_service` не сломало ни `SCHEMA`,
   ни подмену имён через `monkeypatch` (тесты проекта патчат
   `module.read_document` — имя обязано быть обычным глобальным).

Зачем отдельный файл: без него удаление блока алиасов в `core/__init__.py`
не покраснит НИ ОДИН тест проекта — импорты внутри `tests/` идут через
`conftest.py`, который поднимает пакеты целиком.

Проба, которая ловит цикл импорта в ОБРАТНОМ порядке (новый путь первым),
лежит рядом: `tools/check_stage2_aliases.py` (режимы `old` и `new`).
"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE = PROJECT_ROOT / "core"

#: (старое имя модуля, новый путь файла) — ровно то, что переехало в Этапе 2.
MOVED = [
    ("document_reader", "services/recognition/reader.py"),
    ("document_ocr", "services/recognition/ocr.py"),
    ("document_fields", "services/recognition/fields.py"),
    ("recognizer", "services/recognition/recognizer.py"),
    ("document_import_service", "services/recognition/import_service.py"),
]

#: Публичный API: (новый путь модуля, имя) — обязано быть доступно из обоих.
PUBLIC_API = [
    ("services.recognition.reader", "read_document"),
    ("services.recognition.reader", "DocumentPage"),
    ("services.recognition.reader", "text_layer_is_suspicious"),
    ("services.recognition.ocr", "recognize_image"),
    ("services.recognition.fields", "extract_document_fields"),
    ("services.recognition.recognizer", "DataMapper"),
    ("services.recognition.recognizer", "filled_only"),
    ("services.recognition.recognizer", "filled_only_list"),
    ("services.recognition.recognizer", "has_content"),
    ("services.recognition.import_service", "DocumentImportService"),
    ("services.recognition.import_service", "extract_local_fields"),
    ("services.recognition.import_service", "compare_fields"),
    ("services.recognition.import_service", "SCHEMA"),
    ("services.recognition.import_service", "LABELS"),
    ("services.recognition.import_service", "Evidence"),
    ("services.recognition.import_service", "FieldResult"),
    ("services.recognition.import_service", "valid_value"),
    ("services.recognition.import_service", "LOCAL_OCR_FALLBACK_METHOD"),
    ("services.recognition.import_service", "LOCAL_OCR_LIMIT_NOTE"),
]

#: Модули корня `core/`, которых переезд НЕ касался (соседи остались на месте).
CORE_NEIGHBOURS = ["import_cancel.py", "trace.py", "pseudonymizer.py", "audit.py"]


@pytest.mark.parametrize("old_name,new_path", MOVED, ids=[m[0] for m in MOVED])
def test_file_moved_out_of_core_root(old_name, new_path):
    """Старого файла в корне `core/` нет, новый файл на месте."""
    assert not (CORE / f"{old_name}.py").exists(), (
        f"core/{old_name}.py вернулся в корень — переезд Этапа 2 отменён?"
    )
    assert (PROJECT_ROOT / new_path).is_file(), f"нет файла {new_path}"


@pytest.mark.parametrize("old_name,new_path", MOVED, ids=[m[0] for m in MOVED])
def test_old_import_path_is_the_same_module(old_name, new_path):
    """`import core.<старое_имя>` и новый путь — ОДИН объект модуля."""
    old_module = importlib.import_module(f"core.{old_name}")
    new_module = importlib.import_module(new_path[:-3].replace("/", "."))
    assert old_module is new_module, (
        f"core.{old_name} и {new_path} — разные модули: "
        "разъехавшиеся импорты дадут два разных класса с одним именем"
    )


@pytest.mark.parametrize("old_name,new_path", MOVED, ids=[m[0] for m in MOVED])
def test_old_module_is_attribute_of_core(old_name, new_path):
    """`core.<старое_имя>` доступно и как атрибут пакета."""
    import core

    assert getattr(core, old_name) is importlib.import_module(f"core.{old_name}")


@pytest.mark.parametrize("old_name,new_path", MOVED, ids=[m[0] for m in MOVED])
def test_old_name_is_in_sys_modules(old_name, new_path):
    """
    Старое имя лежит в `sys.modules` — именно это читает `from core.X import Y`.

    Регистрация обязана идти через `setdefault`, как в Этапе 1: живой модуль,
    подложенный приватной частью, затирать нельзя.
    """
    import core  # noqa: F401 — регистрация алиасов живёт в его импорте

    assert sys.modules.get(f"core.{old_name}") is importlib.import_module(
        new_path[:-3].replace("/", ".")
    )


@pytest.mark.parametrize("module_path,name", PUBLIC_API, ids=[f"{m.split('.')[-1]}-{n}" for m, n in PUBLIC_API])
def test_public_api_is_available_from_new_path(module_path, name):
    """Публичный API на месте по НОВОМУ пути."""
    module = importlib.import_module(module_path)
    assert hasattr(module, name), f"{module_path} потерял {name}"


def test_public_api_is_available_from_old_path():
    """Тот же публичный API доступен и по СТАРОМУ пути (через алиасы)."""
    from core.document_import_service import (  # noqa: F401
        LOCAL_OCR_FALLBACK_METHOD,
        LOCAL_OCR_LIMIT_NOTE,
        SCHEMA,
        DocumentImportService,
        Evidence,
        FieldResult,
        compare_fields,
        extract_local_fields,
        valid_value,
    )
    from core.document_ocr import recognize_image  # noqa: F401
    from core.document_reader import DocumentPage, read_document  # noqa: F401
    from core.document_fields import extract_document_fields  # noqa: F401
    from core.recognizer import (  # noqa: F401
        DataMapper,
        filled_only,
        filled_only_list,
        has_content,
    )

    # Импорты выше — и есть проверка: не найдя имя, они падают.
    assert callable(read_document) and callable(recognize_image)
    assert callable(extract_document_fields) and callable(extract_local_fields)


def test_services_subpackages_are_packages_with_docstrings():
    """`services` и `services.recognition` — обычные пакеты с пояснением."""
    for package in ("services", "services.recognition"):
        module = importlib.import_module(package)
        assert module.__doc__ and module.__doc__.strip(), (
            f"у пакета {package} нет докстроки"
        )


def test_recognition_package_docstring_points_to_both_paths():
    """Докстрока пакета объясняет оба пути импорта — иначе её смысл потерян."""
    doc = importlib.import_module("services.recognition").__doc__
    assert "services.recognition.reader" in doc
    assert "core.document_reader" in doc


def test_core_root_neighbours_stayed_in_place():
    """Переезд не задел соседей по корню `core/`."""
    for name in CORE_NEIGHBOURS:
        assert (CORE / name).is_file(), f"core/{name} пропал"


def test_lazy_sibling_still_lands_in_module_globals():
    """
    `read_document` и `DataMapper` остаются ОБЫЧНЫМИ глобальными именами.

    Это не придирка: тесты проекта подменяют их через
    `monkeypatch.setattr(module, "read_document", ...)`, а такая подмена
    требует имени, которое реально лежит в модуле. Ленивое взятие соседей
    (нужно против цикла импорта) обязано класть результат в `globals()`.
    """
    module = importlib.import_module("services.recognition.import_service")

    for name in ("read_document", "DataMapper"):
        value = getattr(module, name)
        assert name in vars(module), f"{name} не попало в globals() модуля"
        assert vars(module)[name] is value


def test_lazy_unknown_name_raises_attribute_error():
    """Ленивая дверь не превращает опечатку в тихое None."""
    module = importlib.import_module("services.recognition.import_service")

    with pytest.raises(AttributeError):
        module.НетТакогоИмени


def test_schema_is_built_from_the_same_data_mapper():
    """`SCHEMA` собран из того же `DataMapper`, что и раньше (не копия списков)."""
    from core.document_import_service import SCHEMA
    from core.recognizer import DataMapper

    assert SCHEMA["driver"] == list(DataMapper.DRIVER_FIELDS)
    assert SCHEMA["customer"] == DataMapper.ORGANIZATION_FIELDS
    assert SCHEMA["carrier"] == (
        DataMapper.ORGANIZATION_FIELDS
        + ["carrier_type", "vat_rate"]
        + DataMapper.CARRIER_EXTRA_FIELDS
    )


def test_logger_names_of_moved_modules_are_unchanged():
    """
    Имена логгеров остались прежними — это часть внешнего контракта.

    `config/logging_config.py` настраивает логгеры только из списка
    APP_LOGGERS ("core", "ui", "db", "config", "main", "qt"). Логгер вида
    `services.*` в него не входит и потерял бы обработчики и уровень, а
    `caplog` в трёх наборах тестов ищет логи по старому имени.
    """
    import services.recognition.import_service as service

    assert service.logger.name == "core.document_import_service"

    import core.recognizer as recognizer

    assert recognizer.logger.name == "core.recognizer"


def test_import_service_module_name_is_new():
    """
    При этом `__name__` модуля — настоящий, новый.

    Иначе это была бы копия модуля под старым именем, а не переехавший файл.
    """
    module = importlib.import_module("core.document_import_service")

    assert module.__name__ == "services.recognition.import_service"


@pytest.mark.parametrize("mode", ["old", "new"])
def test_new_path_imported_first_does_not_break(mode):
    """
    ЛЮБОЙ порядок импорта работает — и это отдельный процесс.

    Режим `new` ловит цикл импорта, который не виден в общем прогоне:
    в pytest `core` уже загружен `conftest.py`, поэтому «новый путь первым»
    никогда не проверяется. Именно на нём падал первый заход Этапа 2.
    """
    probe = PROJECT_ROOT / "tools" / "check_stage2_aliases.py"
    result = subprocess.run(
        [sys.executable, str(probe), mode],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode == 0, (
        f"проба режима {mode} упала:\n{result.stdout}\n{result.stderr}"
    )
