#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Общие фикстуры для тестов проекта logistics_contractor.

Запуск всех тестов:
    pytest

Запуск одного файла:
    pytest tests/test_dates.py -v
"""

import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

# ── Корень проекта в sys.path, чтобы работали импорты core/db/ui ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Рабочая папка тестов не должна собираться как тесты ──
collect_ignore_glob = ["_tmp/*"]


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


@pytest.fixture(scope="session")
def templates_dir() -> Path:
    """Папка с шаблонами договоров."""
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
    """Полностью заполненная организация (ООО)."""
    return {
        "full_name": "ООО «Ромашка»",
        "short_name": "ООО «Ромашка»",
        "inn": "7701234567",
        "kpp": "770101001",
        "ogrn": "1027700132195",
        "legal_address": "г. Москва, ул. Тестовая, д. 1",
        "actual_address": "г. Москва, ул. Тестовая, д. 1",
        "bank_account": "40702810000000000001",
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
