#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Общие фикстуры для тестов проекта logistics_contractor.

Запуск всех тестов:
    pytest

Запуск одного файла:
    pytest tests/test_dates.py -v
"""

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

# ── Корень проекта в sys.path, чтобы работали импорты core/db/ui ──
# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Приватная часть проекта (клиентские типы договоров) ──
# Строго ПОСЛЕ добавления корня проекта (иначе core.private_path не
# импортируется) и ДО первого импорта пакетов типов: вызов расширяет
# __path__ публичных пакетов core/ui/tools, без чего private-подпакеты
# не находятся. Приватной папки нет — тесты ядра работают как обычно,
# а тесты клиентских типов пропускаются маркером needs_private.
from core.private_path import (  # noqa: E402
    attach_private_root,
    is_private_available,
    private_path,
)

PRIVATE_ROOT = attach_private_root()

# ── Реестр типов и список промптов ──
# Типы регистрируются при импорте своих пакетов, а промпты ищутся по ключам
# реестра. В приложении это делает main.py при старте; в тестах тот же шаг
# повторяется один раз на прогон — иначе селектор типа и список промптов
# видели бы пустой реестр, а тесты проверяли бы не то поведение.
# Приватной папки нет — грузятся только публичные типы, и это нормально.
from core.contracts.registry import ContractTypeRegistry  # noqa: E402
from core.prompts import refresh_prompts  # noqa: E402

ContractTypeRegistry.load_builtin()
refresh_prompts()

#: Рабочая папка тестов не должна собираться как тесты ──
# (приватная папка лежит вне корня проекта, но pytest.ini её тоже
# исключает: см. norecursedirs).
collect_ignore_glob = ["_tmp/*"]


def pytest_configure(config):
    """Регистрирует маркеры, которых нет в pytest.ini (иначе PytestUnknownMark)."""
    config.addinivalue_line(
        "markers",
        "needs_private: тест требует приватной части проекта "
        "(клиентские типы договоров)",
    )
    config.addinivalue_line(
        "markers",
        "needs_tesseract: тест требует движка Tesseract OCR "
        "(tesseract.exe в PATH)",
    )
    config.addinivalue_line(
        "markers",
        "runner_crash: тест не проходит на раннере GitHub из-за нативного "
        "обрыва Qt при закрытии главного окна (локально идёт; разбор — "
        "docs/STATE.md, ЧАСТЬ 4)",
    )


# ── Движок Tesseract OCR ──
# Офлайн-OCR требует НЕ пакета pytesseract (он в requirements.txt), а самого
# движка `tesseract.exe` в PATH. В CI движок не ставится — это отдельная
# программа, а не пакет pip, — поэтому тесты, которые реально распознают
# картинку, обязаны ПРОПУСКАТЬСЯ, а не падать. Проверка одна на весь прогон.
def tesseract_available() -> bool:
    """Есть ли движок Tesseract OCR (не только пакет pytesseract)."""
    try:
        import pytesseract
    except ImportError:
        return False
    try:
        pytesseract.get_tesseract_version()
    except Exception:  # noqa: BLE001 — любая беда с движком значит «OCR нет»
        return False
    return True


TESSERACT_AVAILABLE = tesseract_available()


def pytest_collection_modifyitems(config, items):
    """
    Пропускает тесты, которым нужна приватная часть или движок Tesseract.

    Публичный репозиторий самодостаточен: без клиентских типов тесты ядра
    обязаны проходить, а тесты, которым нужен клиентский тип или движок
    OCR, — честно пропускаться с причиной, а не падать на импорте или на
    вызове внешней программы.

    Отдельно — маркер `runner_crash`: тесты, которые РОНЯЮТ процесс нативно
    на раннере GitHub (Windows Server, offscreen) при построении и закрытии
    настоящего главного окна. Локально они идут и проходят; пропускаются
    только в CI (`CI=true`), с причиной в отчёте. Разбор — docs/STATE.md,
    ЧАСТЬ 4.
    """
    if not is_private_available():
        skip_private = pytest.mark.skip(
            reason="нет приватной части проекта (клиентские типы договоров)"
        )
        for item in items:
            if "needs_private" in item.keywords:
                item.add_marker(skip_private)

    if not TESSERACT_AVAILABLE:
        skip_tesseract = pytest.mark.skip(
            reason="нет движка Tesseract OCR (tesseract.exe не найден в PATH)"
        )
        for item in items:
            if "needs_tesseract" in item.keywords:
                item.add_marker(skip_tesseract)

    if os.environ.get("CI", "").strip().lower() == "true":
        skip_runner = pytest.mark.skip(
            reason="на раннере GitHub этот тест роняет процесс нативно; "
                   "локально идёт (docs/STATE.md, ЧАСТЬ 4)"
        )
        for item in items:
            if "runner_crash" in item.keywords:
                item.add_marker(skip_runner)


# ─────────────────────────────────────────────────────────────
# Пути и данные
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# Рабочая папка тестов
# ─────────────────────────────────────────────────────────────

#: Сюда складываются временные файлы тестов (папка в .gitignore).
WORK_ROOT = PROJECT_ROOT / "tests" / "_tmp"


@pytest.fixture
def work_dir() -> Path:
    """
    Рабочая папка тестов (tests/_tmp).

    Штатный tmp_path/pytest basetemp не используется: в ограниченных средах
    (жёсткие права на TEMP, песочницы) создание новых подкаталогов может быть
    недоступно. Здесь файлы создаются в одной существующей папке, а
    уникальность имён обеспечивает фикстура work_file.
    """
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    return WORK_ROOT


@pytest.fixture
def work_file(work_dir):
    """
    Фабрика уникальных путей для временных файлов теста.

    Использование: path = work_file("settings.json")
    Все созданные файлы (и их SQLite-спутники -wal/-shm) удаляются после теста.
    """
    created = []

    def _make(name: str) -> Path:
        path = work_dir / f"{uuid.uuid4().hex[:8]}_{name}"
        created.append(path)
        return path

    yield _make

    for path in created:
        for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Корень проекта."""
    return PROJECT_ROOT


# ─────────────────────────────────────────────────────────────
# Эталоны и данные тестов (Этап 7)
# ─────────────────────────────────────────────────────────────
# Пути к данным считаются от ЭТОГО файла, а не от файла теста: после
# Этапа 7 тесты лежат в tests/<слой>/<группа>/, и `Path(__file__).parent`
# из теста указывал бы не туда. Константа PROJECT_ROOT в самих тестах
# ищется вверх по маркеру pytest.ini (те же грабли, см. docs/STATE.md).

@pytest.fixture(scope="session")
def tests_data_dir() -> Path:
    """Папка данных тестов (tests/data)."""
    return Path(__file__).resolve().parent / "data"


@pytest.fixture(scope="session")
def golden_dir(tests_data_dir) -> Path:
    """Эталоны договоров (tests/data/golden, см. tools/make_golden.py)."""
    return tests_data_dir / "golden"


@pytest.fixture(scope="session")
def synthetic_docs_dir(tests_data_dir) -> Path:
    """Синтетические документы и базовые линии (tests/data/synthetic_docs)."""
    return tests_data_dir / "synthetic_docs"


@pytest.fixture(scope="session")
def templates_dir() -> Path:
    """
    Папка с шаблонами договоров.

    Клиентские бланки уехали в приватную часть, поэтому при её наличии
    фикстура отдаёт приватную папку (там лежат ВСЕ бланки: и клиентские,
    и «Экспедиторства» — генераторы получают один и тот же templates_dir).
    Приватной части нет — публичная папка с тремя бланками Экспедиторства.
    """
    private_templates = private_path("templates")
    if private_templates is not None and private_templates.is_dir():
        return private_templates
    return PROJECT_ROOT / "templates"


@pytest.fixture(scope="session")
def real_db_path() -> Path:
    """Путь к рабочей базе contracts.db (может отсутствовать)."""
    return PROJECT_ROOT / "contracts.db"


@pytest.fixture
def driver_data() -> Dict[str, Any]:
    """Полностью заполненный водитель."""
    return {
        "full_name": "Иванов Иван Иванович",
        "birth_date": "1980-01-01",
        "birth_place": "г. Москва",
        "passport_series": "18 22",
        "passport_number": "926830",
        "passport_issue_date": "2023-01-30",
        "passport_issuer": "Отделом УФМС России по г. Москве",
        "passport_code": "500-123",
        "registration_address": "г. Москва, ул. Тестовая, д. 1",
        "license_series": "99 36",
        "license_number": "123456",
        "license_issue_date": "2020-01-01",
        "license_expiry_date": "2030-01-01",
        "license_categories": "B, C, E",
        "phone": "+7 (999) 123-45-67",
    }


@pytest.fixture
def organization_data() -> Dict[str, Any]:
    """
    Полностью заполненная организация (ООО).

    Реквизиты СИНТЕТИЧЕСКИЕ, но с верными контрольными суммами: ИНН
    «7701234560» и счёт «…0007» — это те же числа, что и раньше, с
    исправленным контрольным разрядом. Иначе валидатор (часть 3.2
    пост-рефакторинга) добавлял бы к каждому договору замечание про
    контрольную сумму, и «полностью корректный набор данных» перестал бы
    быть корректным.
    """
    return {
        "full_name": "ООО «Ромашка»",
        "short_name": "ООО «Ромашка»",
        "inn": "7701234560",
        "kpp": "770101001",
        "ogrn": "1027700132195",
        "legal_address": "г. Москва, ул. Тестовая, д. 1",
        "actual_address": "г. Москва, ул. Тестовая, д. 1",
        "bank_account": "40702810000000000007",
        "bik": "044525225",
        "correspondent_account": "30101810400000000225",
        "bank_name": "ПАО Сбербанк",
        "director_name": "Петров Пётр Петрович",
        "director_position": "Генеральный директор",
        "phone": "+7 (495) 123-45-67",
        "email": "info@example.ru",
    }


@pytest.fixture
def contract_payload(driver_data, organization_data) -> Dict[str, Any]:
    """Полный корректный набор данных договора (dict-формат)."""
    carrier = dict(organization_data)
    carrier["entity_type"] = "ООО"
    customer = dict(organization_data)
    customer["full_name"] = "ООО «Заказчик»"
    customer["short_name"] = "ООО «Заказчик»"
    #: Свой ИНН: совпадающие ИНН сторон валидатор считает замечанием
    #: («договор организации сама с собой»), а этот набор данных —
    #: эталон корректного договора.
    customer["inn"] = "7701234592"

    return {
        "driver": driver_data,
        "carrier": carrier,
        "customer": customer,
        "vehicles": [
            {"vin": "EC3TEUMB0T0002608", "brand_model": "JETOUR T2",
             "plate_number": "А123ВС77", "year": 2024, "color": "Белый",
             "vehicle_type": "Легковой автомобиль"},
        ],
        "tractor": {"brand_model": "Foton Auman", "plate_number": "O844XY196",
                    "color": "Белый", "year": 2023},
        "trailer": {"brand_model": "YANGMINDA", "plate_number": "71ABF18",
                    "color": "Серый", "year": 2020},
        "contract": {
            "number": "23092026-74", "date": "2026-09-23",
            "route": "Мурманск - Пятигорск", "carrier_type": "ООО (с НДС)",
            "vat_rate": "22%", "vat_rate_num": 22,
            "price_without_vat": 180300.0, "price_with_vat": 219966.0,
            "payment_days": 10,
        },
        "loadings": [
            {"address": "183052, г.Мурманск, пр.Кольский, д.53",
             "date": "2026-09-24", "time_window": "09:00-18:00"},
        ],
        "unloadings": [
            {"address": "г. Пятигорск, Бештаугорское шоссе 17",
             "date": "2026-09-27", "time_window": ""},
        ],
    }


# ─────────────────────────────────────────────────────────────
# Изолированная база данных
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_db(work_file, monkeypatch):
    """
    Временная база SQLite вместо рабочей contracts.db.

    Права через icacls в тестах не применяются: это медленно и не относится
    к проверяемой логике.
    """
    import db.database as database

    db_file = work_file("contracts.db")
    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    monkeypatch.setattr(database, "restrict_to_current_user", lambda *a, **k: True)
    monkeypatch.setattr(database, "backup_database", lambda *a, **k: None)

    database.init_database()
    yield database


# ─────────────────────────────────────────────────────────────
# Слой Natasha (core/pseudonymizer.py)
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def natasha_warmed_up():
    """
    Прогревает модели Natasha один раз на прогон.

    Первый вызов ``_natasha_pipeline()`` строит segmenter, NER-теггер и
    извлекатели; без прогрева это делал бы первый медленный тест и платил
    за это секундами. Если библиотеки нет — фикстура молчит: маскирование
    обязано работать на регулярках.
    """
    try:
        from core.pseudonymizer import _natasha_pipeline
        _natasha_pipeline()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def natasha_layer_off(request, monkeypatch):
    """
    По умолчанию слой Natasha выключен.

    Тесты проверяют логику маскирования, а не качество чужой NER-модели:
    с включённым слоем результат зависел бы от того, установлена ли
    библиотека, скачаны ли модели и что именно нашла сеть. Поэтому
    Natasha выключена для всего прогона, а тесты самого слоя просят
    фикстуру ``natasha_layer`` — она включает его обратно.
    """
    if "natasha_layer" in request.fixturenames:
        yield
        return

    import core.pseudonymizer as module

    monkeypatch.setattr(module, "_NATASHA_AVAILABLE", False)
    monkeypatch.setattr(module, "_NATASHA", {})
    yield


# ─────────────────────────────────────────────────────────────
# Настройки интерфейса (QSettings)
# ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_qsettings(work_dir):
    """
    QSettings — в файл в tests/_tmp, а не в реестр Windows.

    Ширины колонок таблиц (ШАГ FIX-5) живут в QSettings. Тесты не должны
    ни читать рабочие настройки оператора, ни перезаписывать их: раскладка
    из теста попала бы в настоящий интерфейс. Хранилище чистится перед
    каждым тестом, иначе ширины одного теста «протекли» бы в другой.
    """
    try:
        from PyQt5.QtCore import QSettings
    except ImportError:  # Qt не установлен — этим тестам он и не нужен
        yield
        return

    directory = work_dir / "qsettings"
    directory.mkdir(parents=True, exist_ok=True)

    # Перенаправляем ровно то хранилище, которым пользуется
    # ui/widgets/table_helpers.py: INI-файл в папке настроек пользователя.
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(directory))

    settings = QSettings(
        QSettings.IniFormat, QSettings.UserScope,
        "logistics_contractor", "logistics_contractor",
    )
    settings.clear()
    settings.sync()

    yield
