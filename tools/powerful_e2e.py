#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Мощные E2E-сценарии (промпт «Фиксы + мощные тесты», подэтап 1.D).

Это РАЗОВЫЙ скрипт проверки живой программы, а не pytest-тест: он поднимает
настоящие генераторы, настоящую SQLite-базу и (для сценария 5) настоящее
окно Qt в режиме offscreen. Место ему — ``tools/``, и в прогон ``pytest`` он
не попадает сознательно: у него своя цена по времени (генерация пяти
договоров + окна) и он не должен влиять на числа слоёв.

Сценарии
--------
1. **Генерация договора всех пяти типов** — файл создаётся, размер в норме,
   плейсхолдеров ``{{…}}`` и нераскрытых масок ``<<PERSON_1>>`` в тексте нет.
2. **База: сохранение → поиск → сверка** — на ИЗОЛИРОВАННОЙ базе (рабочая
   ``contracts.db`` не открывается). Удаление договора не проверяется:
   функции ``delete_contract`` в ``db/crud`` НЕТ (см. отчёт).
3. **Распознавание** — сначала локальный путь (``extract_local_fields`` на
   тексте ``tests/data/golden/ooo.docx``), затем, если задан ``--gigachat``
   и есть ключ, один запрос через настоящий ``DocumentImportService``.
4. **Максимилиан** — 20 запросов через настоящий цикл агента и настоящий
   реестр из 40 инструментов; модель подставная (как в тестах агента),
   поэтому токены GigaChat не тратятся.
5. **Переключение типов договоров** — ``WindowManager`` на offscreen;
   проверяется, что окно переживает переключение и данные вкладки целы,
   затем ``close_all()``.

Запуск::

    python tools/powerful_e2e.py               # без сети, всё локально
    python tools/powerful_e2e.py --gigachat    # + один живой запрос в GigaChat

Код возврата: 0 — все сценарии прошли, 1 — есть провал (список печатается).
"""

import argparse
import gc
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: Qt в сценарии 5 поднимается без экрана: та же настройка, что в тестах.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT_ROOT = PROJECT_ROOT / "tests" / "_tmp" / "powerful_tests"
DOCX_MIN_BYTES = 10_000
#: XLSX пустого бланка заметно меньше DOCX: 10 КБ там недостижимо даже у
#: настоящего договора. Порог задан по формату, а не «на глаз».
XLSX_MIN_BYTES = 5_000

#: Признаки нераскрытого шаблона в готовом документе.
JINJA_MARKS = ("{{", "}}", "{%", "%}")
#: Признаки утечки маски в документ: псевдонимы не должны попадать в бланк.
MASK_PREFIX = "<<"


# ─────────────────────────────────────────────────────────────
# Результат одного сценария
# ─────────────────────────────────────────────────────────────

@dataclass
class Result:
    """Итог сценария: имя, успех, короткая пометка, время."""

    name: str
    ok: bool
    note: str = ""
    seconds: float = 0.0
    details: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# Синтетические данные договоров
# ─────────────────────────────────────────────────────────────
# Значения ВЫМЫШЛЕННЫЕ, но реквизиты — с верными контрольными суммами:
# иначе валидатор добавил бы к каждому договору замечание, и «корректный
# набор данных» перестал бы быть корректным (те же числа, что в
# tests/conftest.py::organization_data).

DRIVER = {
    "full_name": "Абрамян Ашот Григорьевич",
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

ORGANIZATION = {
    "full_name": "ООО «Ромашка»",
    "short_name": "ООО «Ромашка»",
    "entity_type": "ООО",
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

TRACTOR = {"brand_model": "Foton Auman", "plate_number": "О844ХУ196",
           "color": "Белый", "year": 2023}
TRAILER = {"brand_model": "YANGMINDA", "plate_number": "71АВF18",
           "color": "Серый", "year": 2020}
VEHICLES = [
    {"vin": "EC3TEUMB0T0002608", "brand_model": "JETOUR T2",
     "plate_number": "А123ВС77", "year": 2024, "color": "Белый",
     "vehicle_type": "Легковой автомобиль"},
    {"vin": "XTC651150N0001001", "brand_model": "HAVAL DASHING",
     "plate_number": "В456ЕК77", "year": 2025, "color": "Чёрный",
     "vehicle_type": "Легковой автомобиль"},
]


def _customer() -> Dict[str, Any]:
    """Заказчик — своя организация, свой ИНН (иначе замечание валидатора)."""
    customer = dict(ORGANIZATION)
    customer["full_name"] = "ООО «Заказчик»"
    customer["short_name"] = "ООО «Заказчик»"
    customer["inn"] = "7701234592"
    return customer


def payload_perevozka() -> Dict[str, Any]:
    """Договор-заявка на перевозку (публичный тип, рабочий)."""
    return {
        "driver": dict(DRIVER),
        "carrier": dict(ORGANIZATION),
        "customer": _customer(),
        "vehicles": [dict(v) for v in VEHICLES],
        "tractor": dict(TRACTOR),
        "trailer": dict(TRAILER),
        "contract": {
            "number": "ПВ-2026-101",
            "date": "2026-10-17",
            "route": "Мурманск - Пятигорск",
            "carrier_type": "ООО (с НДС)",
            "vat_rate": "22%",
            "vat_rate_num": 22,
            "price_without_vat": 180300.0,
            "price_with_vat": 219966.0,
            "payment_days": 10,
        },
        "loadings": [{"address": "183052, г. Мурманск, пр. Кольский, д. 53",
                      "date": "2026-09-24", "time_window": "09:00-18:00"}],
        "unloadings": [{"address": "г. Пятигорск, Бештаугорское шоссе 17",
                        "date": "2026-09-27", "time_window": ""}],
    }


def payload_formika() -> Dict[str, Any]:
    """Приложение к генеральному договору перевозки (клиентский тип)."""
    data = payload_perevozka()
    data["contract"].update({
        "number": "ФМ-2026-101",
        "route": "г. Воронеж - г. Москва",
        "loading_plan_date": "2026-10-19",
    })
    data["loadings"] = [{"address": "г. Воронеж, ул. Остужева 52Б",
                         "date": "2026-10-19", "time_window": "09:00-15:00"}]
    data["unloadings"] = [{"address": "г. Москва, Перерва 19 стр 3",
                           "date": "2026-10-22", "time_window": ""}]
    return data


def payload_logistiks_rus() -> Dict[str, Any]:
    """
    Приложение к генеральному договору экспедиции (клиентский тип).

    У этого типа точки маршрута лежат ВНУТРИ ``contract`` — так их отдаёт
    распознавание, и так их ждёт генератор.
    """
    return {
        "driver": dict(DRIVER),
        "carrier": dict(ORGANIZATION),
        "customer": _customer(),
        "vehicles": [dict(v) for v in VEHICLES],
        "tractor": dict(TRACTOR),
        "trailer": dict(TRAILER),
        "contract": {
            "number": "ЛР-2026-101",
            "date": "2026-10-17",
            "carrier_type": "ООО (с НДС)",
            "vat_rate_num": 22,
            "price_without_vat": 221099.18,
            "loading_date": "2026-10-19",
            "loading_time_from": "08:00",
            "loading_time_to": "20:00",
            "unloading_date": "2026-10-24",
            "unloading_time_from": "08:00",
            "unloading_time_to": "20:00",
            "loadings": [{"address": "г. Воронеж, ул. Остужева 52Б",
                          "date": "2026-10-19", "time_from": "08:00",
                          "time_to": "20:00"}],
            "unloadings": [{"address": "г. Москва, Перерва 19 стр 3",
                            "date": "2026-10-24"}],
        },
    }


def payload_arenda_ts() -> Dict[str, Any]:
    """Договор аренды ТС с экипажем (клиентский тип): стороны — не те, что выше."""
    lessor = dict(ORGANIZATION)
    lessor["full_name"] = "ООО «Арендодатель Тест»"
    lessor["short_name"] = "ООО «АДТ»"
    lessor["inn"] = "7709876543"
    lessor["ogrn"] = "1027700132196"

    lessee = dict(ORGANIZATION)
    lessee["full_name"] = "ООО «Арендатор Тест»"
    lessee["short_name"] = "ООО «АТ»"

    return {
        "lessee": lessee,
        "lessor": lessor,
        "tractor": {"brand_model": "Тягач-Модель", "plate_number": "А001АА01",
                    "vehicle_type": "грузовой тягач седельный"},
        "trailer": {"brand_model": "Прицеп-Модель", "plate_number": "Б002ББ02"},
        "lease_start_date": "2026-10-18",
        "lease_end_date": "2026-10-26",
        "route": "Москва — Калуга — Чехов",
        "vehicles": [
            {"brand_model": f"МОДЕЛЬ {n}", "vin": f"TESTVIN000000000{n:02d}",
             "loading_point": f"Точка погрузки {n}",
             "unloading_point": f"Точка выгрузки {n}"}
            for n in (1, 2)
        ],
        "loadings": [{"address": "Адрес погрузки 1", "date": "2026-10-19",
                      "time_from": "08:00", "time_to": "18:00"}],
        "unloadings": [{"address": "Адрес выгрузки 1", "date": "2026-10-25"}],
        "driver": {
            "full_name": DRIVER["full_name"],
            "birth_date": "1980-01-01",
            "passport": "18 22 926830",
            "passport_issuer": "Отделом УФМС России по г. Москве",
            "passport_issue_date": "2023-01-30",
            "license": "99 36 123456",
            "license_issue_date": "2020-01-01",
            "address": "г. Москва, ул. Водительская, д. 3",
            "phone": "+7 (999) 123-45-67",
        },
        "contract": {
            "number": "АБ-2026-101",
            "date": "2026-10-17",
            "carrier_type": "ООО",
            "vat_rate": "22%",
            "sum_wo_vat": 221099.18,
            "sum_vat": 48641.82,
            "sum_total": 269741.00,
        },
    }


def payload_zayavka_excel() -> Dict[str, Any]:
    """Заявка на перевозку в Excel-формате (клиентский тип, XLSX)."""
    return {
        "zayavka": {
            "date": "17.10.2026",
            "lot_number": "LOT-42",
            "loading_city": "Калуга",
            "loading_point": "ул. Промышленная, 12, стр. 3",
            "unloading_city": "Москва",
            "unloading_point": "Складской проезд, 5",
            "carrier_name": ORGANIZATION["full_name"],
            "customer_name": "ООО «Заказчик»",
            "tractor_brand": TRACTOR["brand_model"],
            "tractor_color": TRACTOR["color"],
            "tractor_plate": "О844ХУ196",
            "trailer_brand": TRAILER["brand_model"],
            "trailer_plate": "71АВF18",
            "driver_last_name": "Абрамян",
            "driver_first_name": "Ашот",
            "driver_middle_name": "Григорьевич",
            "driver_license_number": "9901 123456",
            "driver_license_issue_date": "12.03.2019",
            "driver_passport_series": "1822",
            "driver_passport_number": "926830",
            "driver_passport_issuer": DRIVER["passport_issuer"],
            "driver_passport_issue_date": "30.01.2023",
            "driver_citizenship": "РФ",
            "driver_birth_date": "01.01.1980",
            "driver_registration": "г. Москва, ул. Тестовая, д. 1, кв. 2",
            "driver_phone": "+7 (999) 123-45-67",
            "loading_plan_date": "19.10.2026",
            "loading_plan_time": "09:00",
            "price_with_vat": 219966.0,
            "vat_rate": "22%",
        },
        "vehicles": [
            {"vin": "EC3TEUMB0T0002608", "brand": "JETOUR", "model": "T2",
             "dealer": "Дилер-Первый", "dealer_code": "D-001"},
            {"vin": "XTC651150N0001001", "brand": "HAVAL", "model": "DASHING",
             "dealer": "Дилер-Второй", "dealer_code": "D-002"},
        ],
    }


#: Тип → (построитель данных, расширение готового файла).
PAYLOADS = {
    "perevozka": (payload_perevozka, ".docx"),
    "formika": (payload_formika, ".docx"),
    "logistiks_rus": (payload_logistiks_rus, ".docx"),
    "arenda_ts": (payload_arenda_ts, ".docx"),
    "zayavka_excel": (payload_zayavka_excel, ".xlsx"),
}


# ─────────────────────────────────────────────────────────────
# Чтение готового документа: текст для проверки плейсхолдеров
# ─────────────────────────────────────────────────────────────

def docx_text(path: Path) -> str:
    """Весь текст DOCX: абзацы, таблицы и колонтитулы."""
    from docx import Document

    document = Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    for section in document.sections:
        for block in (section.header, section.footer):
            for paragraph in block.paragraphs:
                parts.append(paragraph.text)
    return "\n".join(parts)


def xlsx_text(path: Path) -> str:
    """Весь текст XLSX: значения всех листов."""
    from openpyxl import load_workbook

    workbook = load_workbook(str(path), data_only=True)
    try:
        parts: List[str] = []
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.value is not None:
                        parts.append(str(cell.value))
        return "\n".join(parts)
    finally:
        workbook.close()


def template_leaks(text: str) -> List[str]:
    """Найденные признаки нераскрытого шаблона и утечки маски."""
    leaks: List[str] = [mark for mark in JINJA_MARKS if mark in text]
    if MASK_PREFIX in text:
        leaks.append(MASK_PREFIX)
    return leaks


# ─────────────────────────────────────────────────────────────
# Сценарий 1. Генерация договора всех типов
# ─────────────────────────────────────────────────────────────

def scenario_generation() -> Result:
    """Пять типов: файл есть, размер в норме, плейсхолдеров нет."""
    started = time.time()
    from core.contracts.factory import GeneratorFactory
    from core.contracts.registry import ContractTypeRegistry

    details: List[str] = []
    failures: List[str] = []
    generated: Dict[str, str] = {}

    available = set(ContractTypeRegistry.known_types())
    for contract_type, (build, suffix) in PAYLOADS.items():
        if contract_type not in available:
            details.append(f"{contract_type}: ПРОПУСК — тип не зарегистрирован "
                           f"(нет приватной части?)")
            continue
        out_dir = OUT_ROOT / "output" / contract_type
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            generator = GeneratorFactory.get_generator(contract_type, strict=True)
            path = Path(generator.generate(build(), output_dir=str(out_dir)))
        except Exception as exc:                            # noqa: BLE001
            failures.append(f"{contract_type}: {type(exc).__name__}: {exc}")
            details.append(f"{contract_type}: ПРОВАЛ генерации — "
                           f"{type(exc).__name__}: {exc}")
            continue

        if not path.exists():
            failures.append(f"{contract_type}: файл не создан")
            details.append(f"{contract_type}: ПРОВАЛ — файл не создан ({path})")
            continue

        size = path.stat().st_size
        floor = XLSX_MIN_BYTES if suffix == ".xlsx" else DOCX_MIN_BYTES
        if size < floor:
            failures.append(f"{contract_type}: размер {size} < {floor}")
            details.append(f"{contract_type}: ПРОВАЛ — размер {size} Б "
                           f"меньше порога {floor} Б")
            continue

        try:
            text = xlsx_text(path) if suffix == ".xlsx" else docx_text(path)
        except Exception as exc:                            # noqa: BLE001
            failures.append(f"{contract_type}: чтение не удалось: {exc}")
            details.append(f"{contract_type}: ПРОВАЛ чтения — "
                           f"{type(exc).__name__}: {exc}")
            continue

        leaks = template_leaks(text)
        if leaks:
            failures.append(f"{contract_type}: плейсхолдеры {leaks}")
            details.append(f"{contract_type}: ПРОВАЛ — в тексте найдено "
                           f"{leaks}")
            continue

        generated[contract_type] = str(path)
        details.append(f"{contract_type}: OK — {path.name}, "
                       f"{size / 1024:.1f} КБ, знаков {len(text)}")

    note = (f"сгенерировано {len(generated)} из {len(PAYLOADS)}; "
            f"провалов {len(failures)}")
    return Result("Генерация 5 типов", not failures, note,
                  time.time() - started, details)


# ─────────────────────────────────────────────────────────────
# Сценарий 2. База: сохранение → поиск → сверка
# ─────────────────────────────────────────────────────────────

def scenario_database(work_dir: Path) -> Result:
    """
    Сохранение договора с точками и машинами, затем поиск и сверка.

    База берётся ИЗОЛИРОВАННАЯ: её путь выставляет ``main()`` (см.
    ``_use_isolated_database``), как это делает фикстура ``isolated_db``.
    Рабочая ``contracts.db`` не открывается вовсе, поэтому данные оператора
    сценарий не видит и не меняет.
    """
    started = time.time()
    from db.crud.contracts import (
        load_contract_points, save_contract_with_details, search_contracts,
    )

    details: List[str] = []
    failures: List[str] = []

    contract = {
        "number": "E2E-2026-001",
        "date": "2026-10-17",
        "start_date": "2026-10-18",
        "end_date": "2026-10-25",
        "route": "Мурманск - Пятигорск",
        "price_without_vat": 180300.0,
        "vat_rate": "22%",
        "price_with_vat": 219966.0,
        "currency": "RUB",
    }
    loadings = [{"name": "Салон-1", "address": "г. Мурманск, пр. Кольский, 53",
                 "date": "2026-10-19", "time_window": "09:00-18:00"}]
    unloadings = [{"name": "", "address": "г. Пятигорск, шоссе 17",
                   "date": "2026-10-24", "time_window": ""}]
    vehicles = [dict(VEHICLES[0])]

    contract_id = save_contract_with_details(
        contract, loadings, unloadings, vehicles
    )
    details.append(f"сохранён договор ID={contract_id}")

    found = search_contracts("E2E-2026-001", limit=5)
    if not found:
        failures.append("поиск не нашёл сохранённый договор")
        details.append("ПРОВАЛ — поиск вернул пустой список")
    else:
        row = found[0]
        checks: Tuple[Tuple[str, Any, Any], ...] = (
            ("номер", row.get("contract_number"), contract["number"]),
            ("дата", row.get("contract_date"), contract["date"]),
            ("маршрут", row.get("route"), contract["route"]),
            ("цена с НДС", float(row.get("price_with_vat") or 0),
             contract["price_with_vat"]),
            ("валюта", row.get("currency"), contract["currency"]),
        )
        for name, got, expected in checks:
            if got != expected:
                failures.append(f"{name}: {got!r} != {expected!r}")
                details.append(f"ПРОВАЛ сверки — {name}: {got!r} != "
                               f"{expected!r}")
            else:
                details.append(f"сверка {name}: OK")

    points = load_contract_points(contract_id)
    if len(points["loadings"]) != 1 or len(points["unloadings"]) != 1:
        failures.append(
            f"точки маршрута: {len(points['loadings'])} погрузок, "
            f"{len(points['unloadings'])} выгрузок"
        )
        details.append("ПРОВАЛ — точки маршрута не совпали")
    elif points["loadings"][0]["name"] != "Салон-1":
        failures.append("имя салона в точке не сохранилось")
        details.append("ПРОВАЛ — имя салона в точке потерялось")
    else:
        details.append("точки маршрута и имя салона: OK")

    details.append("удаление: не проверялось — функции delete_contract "
                   "в db/crud нет")
    note = f"ID={contract_id}, провалов {len(failures)}"
    return Result("Сохранение в БД + загрузка", not failures, note,
                  time.time() - started, details)


# ─────────────────────────────────────────────────────────────
# Сценарий 3. Распознавание документа
# ─────────────────────────────────────────────────────────────

def _count_fields(data: Dict[str, Any]) -> int:
    """Сколько непустых значений в ответе распознавания (без служебных)."""
    total = 0
    for section, payload in (data or {}).items():
        if section.startswith("_"):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            for key, value in item.items():
                if key.startswith("_") or not value:
                    continue
                total += 1
    return total


def scenario_recognition(use_gigachat: bool) -> Result:
    """
    Распознавание golden-документа: локальный путь, затем (по флагу) GigaChat.

    Локальный путь идёт через тот же конвейер, что приложение:
    ``read_document`` → ``extract_local_fields``. Живой запрос — через
    настоящий ``DocumentImportService``; он платный, поэтому только по
    флагу ``--gigachat``.
    """
    started = time.time()
    import threading

    from core.document_reader import read_document
    from services.recognition.import_service import extract_local_fields

    details: List[str] = []
    failures: List[str] = []
    golden = PROJECT_ROOT / "tests" / "data" / "golden" / "ooo.docx"
    if not golden.exists():
        return Result("Распознавание", False, f"нет файла {golden.name}",
                      time.time() - started, ["ПРОВАЛ — golden-файл не найден"])

    cancel = threading.Event()
    pages = list(read_document(golden, cancel, ""))
    text = pages[0].text if pages else ""
    if not text.strip():
        failures.append("текстовый слой пуст")
        details.append("ПРОВАЛ — документ не отдал текст")
        data: Dict[str, Any] = {}
    else:
        data = extract_local_fields(text) or {}

    local_fields = _count_fields(data)
    details.append(f"локально: страниц {len(pages)}, знаков {len(text)}, "
                   f"полей {local_fields}")
    if local_fields < 3:
        failures.append(f"локально найдено полей {local_fields} < 3")
        details.append("ПРОВАЛ — локальный разбор нашёл меньше трёх полей")
    else:
        details.append("локальный разбор: OK (≥3 поля)")

    if use_gigachat:
        cloud_fields, cloud_note = _recognize_with_gigachat(golden)
        details.append(f"GigaChat: {cloud_note}")
        if cloud_fields is not None and cloud_fields < 3:
            failures.append(f"GigaChat нашёл полей {cloud_fields} < 3")
    else:
        details.append("GigaChat: пропущен (нужен флаг --gigachat)")

    note = f"локально полей {local_fields}"
    return Result("Распознавание", not failures, note,
                  time.time() - started, details)


def _recognize_with_gigachat(golden: Path) -> Tuple[Optional[int], str]:
    """
    Один живой запрос к GigaChat через штатный сервис импорта.

    Возвращает (число найденных полей или None, чем закончилось).

    ``None`` — это «облако не ответило», а НЕ провал сценария: сеть и ключ от
    нас не зависят, и промпт прямо велит в этом случае идти локальным путём.
    Признак «облако не ответило»: сервис вызвал ``on_result`` с пустым
    доказательством и предупреждением — так он сообщает об отказе Vision.
    """
    import threading

    try:
        from core.secrets_store import has_gigachat_key

        if not has_gigachat_key():
            return None, "пропущен — нет ключа"
    except Exception as exc:                                # noqa: BLE001
        return None, f"пропущен — хранилище ключей недоступно ({exc})"

    try:
        from services.recognition.import_service import DocumentImportService

        collected: List[Tuple[str, List[Any], str]] = []

        def on_result(source, evidence, warnings):
            """Штатный callback сервиса: (источник, доказательства, замечания)."""
            collected.append((source, list(evidence or ()), str(warnings or "")))

        client = _gigachat_client()
        service = DocumentImportService(
            settings={"document_cloud_enabled": True}, client=client
        )
        service.process([str(golden)], threading.Event(), on_result)

        fields = 0
        methods = set()
        warnings: List[str] = []
        for _source, evidence, warning in collected:
            if warning:
                warnings.append(warning)
            for item in evidence:
                methods.add(str(getattr(item, "method", "") or ""))
                values = getattr(item, "values", None) or {}
                fields += sum(1 for value in values.values()
                              if str(value or "").strip())
        if not fields and warnings:
            # Облако отказало: это состояние среды, а не провал программы.
            return None, f"недоступен — {warnings[0][:120]}"
        used = ", ".join(sorted(name for name in methods if name)) or "нет"
        note = f"полей {fields}, страниц {len(collected)}, методы: {used}"
        if warnings:
            note += f"; замечание: {warnings[0][:80]}"
        return fields, note
    except Exception as exc:                                # noqa: BLE001
        return None, f"ошибка вызова: {type(exc).__name__}: {str(exc)[:160]}"


def _gigachat_client():
    """Живой клиент GigaChat (None — сервис пойдёт локальным путём)."""
    try:
        from core.gigachat_client import GigaChatClient

        return GigaChatClient()
    except Exception:                                       # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────
# Сценарий 4. Максимилиан: 20 запросов на подставной модели
# ─────────────────────────────────────────────────────────────

class StubTab:
    """Вкладка-заглушка окна: тот же контракт, что у настоящей."""

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self.data = dict(data or {})

    def get_data(self) -> Dict[str, Any]:
        return dict(self.data)

    def fill_data(self, values=None, *args) -> None:
        self.data.update(values or {})


class StubWindow:
    """Окно-заглушка: вкладки по ключам плана, как у окна типа."""

    CONTRACT_TYPE = "perevozka"

    def __init__(self):
        self.driver_tab = StubTab({"full_name": DRIVER["full_name"],
                                   "phone": DRIVER["phone"]})
        self.carrier_tab = StubTab({"full_name": ORGANIZATION["full_name"],
                                    "inn": ORGANIZATION["inn"]})
        self.customer_tab = StubTab({"full_name": "ООО «Заказчик»"})
        self.vehicle_tab = StubTab({})
        self.trailer_tab = StubTab({})
        self.contract_tab = StubTab({"number": "ПВ-2026-101"})

    def _tab_keys(self) -> Tuple[str, ...]:
        return ("driver_tab", "carrier_tab", "customer_tab",
                "vehicle_tab", "trailer_tab", "contract_tab")

    def _tab_by_key(self, key: str):
        return getattr(self, key, None)


def text_reply(content: str) -> Dict[str, Any]:
    """Ответ модели текстом."""
    return {"role": "assistant", "content": content, "tool_calls": []}


def tool_reply(name: str, arguments: Optional[Dict[str, Any]] = None,
               call_id: str = "call_1") -> Dict[str, Any]:
    """Ответ модели с вызовом инструмента."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments or {},
                                                 ensure_ascii=False)},
        }],
    }


class ScriptedChat:
    """
    Подставная модель: на каждый запрос отдаёт заранее заданный ответ.

    Так проверяется ЦИКЛ агента (инструменты, предохранители, сборка
    ответа), а не качество модели. Токены GigaChat не тратятся — это
    осознанно: 20 живых запросов стоили бы тысячи токенов и дали бы
    нестабильный результат.
    """

    def __init__(self):
        self.requests: List[Dict[str, Any]] = []
        self._queue: List[Dict[str, Any]] = []

    def plan(self, replies: List[Dict[str, Any]]) -> None:
        """Задаёт очередь ответов на один запрос оператора."""
        self._queue = list(replies)

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.requests.append({"messages": [dict(m) for m in messages],
                              "tools": list(tools or ())})
        if not self._queue:
            return text_reply("Ответы закончились")
        return self._queue.pop(0)


#: Двадцать запросов: категория, слова оператора и план модели.
#: Инструменты выбраны только читающие (или с подтверждением), чтобы
#: сценарий не ходил в сеть и не менял форму.
AGENT_PLAN: Tuple[Tuple[str, str, str, Dict[str, Any]], ...] = (
    ("поиск", "найди Иванова", "search_driver", {"query": "Абрамян"}),
    ("поиск", "покажи договоры за октябрь", "search_contract",
     {"query": "2026-10"}),
    ("поиск", "найди перевозчика Ромашка", "search_carrier",
     {"query": "Ромашка"}),
    ("поиск", "найди заказчика Заказчик", "search_customer",
     {"query": "Заказчик"}),
    ("поиск", "найди машину EC3TEUMB0T0002608", "search_vehicle",
     {"query": "EC3TEUMB0T0002608"}),
    ("поиск", "найди что-нибудь про Пятигорск", "search_anything",
     {"query": "Пятигорск"}),
    ("поиск", "какие договоры скоро истекают", "list_expiring_contracts",
     {"days": 30}),
    ("поиск", "сколько водителей в базе", "count_records",
     {"entity_type": "driver"}),
    ("справка", "что такое ИНН", "explain_field", {"field": "inn"}),
    ("справка", "как заполнить VIN", "explain_field", {"field": "vin"}),
    ("справка", "покажи справку по работе с программой", "get_help", {}),
    ("справка", "покажи оглавление обучения", "tutorial", {}),
    ("справка", "какие поля обязательны", "check_required", {}),
    ("аналитика", "сколько договоров за октябрь", "aggregate_contracts",
     {"period": "2026-10"}),
    ("аналитика", "топ перевозчиков", "top_contractors", {"limit": 5}),
    ("аналитика", "сравни сентябрь и октябрь", "compare_periods",
     {"period_a": "2026-09", "period_b": "2026-10"}),
    ("правовая база", "ответственность за просрочку", "search_legal",
     {"query": "просрочка"}),
    ("форма", "что не заполнено", "check_required", {}),
    ("форма", "проверь форму", "validate_current_form", {}),
    ("форма", "прочитай состояние формы", "get_form_state", {}),
)

#: Дополнительные запросы с опасным инструментом: проверяется отказ
#: оператора (подтверждение отвечает «нет»), а не только чтение.
AGENT_DECLINE_PLAN: Tuple[Tuple[str, str, str, Dict[str, Any]], ...] = (
    ("форма", "заполни Ромашку", "fill_field",
     {"field": "carrier_name", "value": "ООО «Ромашка»"}),
)


def scenario_agent() -> Result:
    """Двадцать запросов через настоящий цикл агента и реестр инструментов."""
    started = time.time()
    from services.agent.core.agent import Agent
    from services.agent.tools.registry import AgentContext, build_registry

    details: List[str] = []
    failures: List[str] = []
    timings: List[float] = []
    steps: List[int] = []

    window = StubWindow()
    asked: List[Tuple[str, Dict[str, Any]]] = []
    context = AgentContext(
        window=window,
        contract_type="perevozka",
        confirm=_decline(asked),
    )
    registry = build_registry(context)
    details.append(f"инструментов в реестре: {len(registry.names())}")

    chat = ScriptedChat()
    agent = Agent(client=chat, context=context, registry=registry,
                  max_tool_steps=8, max_request_seconds=0)

    for category, query, tool_name, arguments in AGENT_PLAN:
        chat.plan([tool_reply(tool_name, arguments), text_reply(
            f"Готово: {tool_name} по запросу «{query}».")])
        query_started = time.time()
        try:
            reply = agent.send(query)
        except Exception as exc:                            # noqa: BLE001
            failures.append(f"«{query}»: {type(exc).__name__}: {exc}")
            details.append(f"ПРОВАЛ [{category}] «{query}» — "
                           f"{type(exc).__name__}: {exc}")
            continue
        timings.append(time.time() - query_started)
        steps.append(reply.steps)

        problem = _check_reply(reply)
        if problem:
            failures.append(f"«{query}»: {problem}")
            details.append(f"ПРОВАЛ [{category}] «{query}» — {problem}")
        else:
            details.append(f"OK [{category}] «{query}» → steps={reply.steps}, "
                           f"инструменты={reply.tool_names}")

    for category, query, tool_name, arguments in AGENT_DECLINE_PLAN:
        chat.plan([tool_reply(tool_name, arguments), text_reply(
            "Заполнение отменено оператором.")])
        try:
            reply = agent.send(query)
        except Exception as exc:                            # noqa: BLE001
            failures.append(f"«{query}»: {type(exc).__name__}: {exc}")
            details.append(f"ПРОВАЛ [{category}] «{query}» — "
                           f"{type(exc).__name__}: {exc}")
            continue
        if not reply.text.strip():
            failures.append(f"«{query}»: пустой ответ")
            details.append(f"ПРОВАЛ [{category}] «{query}» — пустой ответ")
        elif not asked:
            failures.append(f"«{query}»: подтверждение не спрашивали")
            details.append(f"ПРОВАЛ [{category}] «{query}» — опасный "
                           f"инструмент выполнен без подтверждения")
        else:
            details.append(f"OK [{category}] «{query}» → отказ обработан, "
                           f"спрошено {len(asked)}")

    over_limit = [s for s in steps if s > agent.max_tool_steps]
    if over_limit:
        failures.append(f"шагов больше лимита: {over_limit}")
    slowest = max(timings) if timings else 0.0
    details.append(f"запросов: {len(timings) + len(asked)}; "
                   f"среднее время {(sum(timings) / len(timings) if timings else 0):.3f} с; "
                   f"самый долгий {slowest:.3f} с")
    note = (f"{len(timings)} чтения + {len(asked)} подтверждений; "
            f"провалов {len(failures)}")
    return Result("Максимилиан (20 запросов)", not failures, note,
                  time.time() - started, details)


def _decline(asked: List[Tuple[str, Dict[str, Any]]]):
    """Диалог подтверждения, который запоминает вопрос и отвечает «нет»."""

    def confirm(tool, arguments) -> bool:
        asked.append((tool.name, dict(arguments)))
        return False

    return confirm


def _check_reply(reply) -> str:
    """Проверка ответа агента: пустой текст или упор в предохранитель."""
    if not reply.text.strip():
        return "пустой текст ответа"
    if reply.step_limit_reached:
        return f"упёрлись в предохранитель ({reply.stop_reason})"
    if reply.timed_out:
        return "таймаут запроса"
    return ""


# ─────────────────────────────────────────────────────────────
# Сценарий 5. Переключение типов договоров (offscreen)
# ─────────────────────────────────────────────────────────────

#: Ключ типа → (модуль окна, класс окна). Копия карты из main.py: импорт
#: main.py потянул бы разбор аргументов командной строки и логирование.
TYPE_WINDOWS = {
    "arenda_ts": ("ui.windows.arenda_ts", "ArendaTsWindow"),
    "formika": ("ui.windows.formika", "FormikaWindow"),
    "logistiks_rus": ("ui.windows.logistiks_rus", "LogistiksRusWindow"),
    "zayavka_excel": ("ui.windows.havaly", "HavalyWindow"),
}


def scenario_windows() -> Result:
    """
    Окна всех доступных типов в одном менеджере: создание, переключение,
    сохранность данных вкладки, закрытие.
    """
    started = time.time()
    from PyQt5.QtWidgets import QApplication

    from ui.main_window import MainWindow
    from ui.windows import WindowManager

    details: List[str] = []
    failures: List[str] = []

    app = QApplication.instance() or QApplication([])
    factories: Dict[str, Any] = {
        "perevozka": _factory(MainWindow),
    }
    for contract_type, (module_name, class_name) in TYPE_WINDOWS.items():
        window_class = _optional_window_class(module_name, class_name)
        if window_class is None:
            details.append(f"{contract_type}: окно недоступно (нет приватной "
                           f"части) — пропуск")
            continue
        factories[contract_type] = _factory(window_class)

    manager = WindowManager(factories)
    opened: List[str] = []
    try:
        for contract_type in factories:
            window = manager.window_for(contract_type)
            if window is None:
                failures.append(f"{contract_type}: окно не создано")
                details.append(f"ПРОВАЛ — {contract_type}: окно не создано")
                continue
            opened.append(contract_type)
            details.append(f"{contract_type}: окно создано "
                           f"({window.windowTitle()})")

        # Маркер кладётся в поле ПЕРВОЙ вкладки, у которой fill_data принимает
        # один словарь: у вкладок прицепа и машин подпись другая, и лезть в них
        # «наугад» — значит проверять не то.
        markers: Dict[str, Tuple[Any, str, str]] = {}
        for contract_type in opened:
            window = manager.window_for(contract_type)
            placed = _place_marker(window)
            if placed is None:
                details.append(f"{contract_type}: поле для маркера не найдено "
                               f"(вкладки без словарного fill_data)")
            else:
                markers[contract_type] = placed
                details.append(f"{contract_type}: маркер в поле {placed[1]!r}")

        if len(opened) < 2:
            details.append("переключение проверять не на чем: открыт один тип")
        else:
            for contract_type in opened:
                if manager.switch_to(contract_type) is None:
                    failures.append(f"{contract_type}: переключение не удалось")
                    continue
                if manager.current_type() != contract_type:
                    failures.append(
                        f"{contract_type}: текущий тип "
                        f"{manager.current_type()!r}"
                    )
            # Обходим окна второго раза: данные должны быть на месте.
            for contract_type, (tab, field, expected) in markers.items():
                window = manager.window_for(contract_type)
                if window is None:
                    failures.append(f"{contract_type}: окно пропало")
                    continue
                got = str(_tab_data_of(window, tab).get(field, ""))
                if got != expected:
                    failures.append(
                        f"{contract_type}: данные поля {field!r} потерялись "
                        f"({got!r} != {expected!r})"
                    )
                    details.append(f"ПРОВАЛ — {contract_type}: данные поля "
                                   f"{field!r} потерялись ({got!r})")
                else:
                    details.append(f"{contract_type}: данные поля {field!r} "
                                   f"целы после переключений")
            details.append(f"открыто окон: {len(manager.windows())}")
    finally:
        manager.close_all()
        details.append("close_all(): все окна закрыты")
        gc.collect()
        app.processEvents()

    note = f"типов открыто {len(opened)}, провалов {len(failures)}"
    return Result("Переключение окон", not failures, note,
                  time.time() - started, details)


def _factory(window_class):
    """Фабрика окна: именованная функция, без lambda (AGENTS.md § 5.1)."""

    def make_window():
        return window_class()

    return make_window


def _optional_window_class(module_name: str, class_name: str):
    """Класс окна из приватной части или None, если её нет."""
    from core.private_path import optional_module

    module = optional_module(module_name)
    if module is None:
        return None
    return getattr(module, class_name, None)


def _tab_of(window, key: str):
    """
    Вкладка окна по ключу.

    У окон типов есть ``_tab_by_key``, а у ``MainWindow`` вкладки — обычные
    атрибуты (у него только ``_tab_keys``). Читаем одинаково там и тут.
    """
    getter = getattr(window, "_tab_by_key", None)
    if callable(getter):
        return getter(key)
    return getattr(window, key, None)


def _tab_data_of(window, tab) -> Dict[str, Any]:
    """Данные вкладки: через окно (``_tab_data``), если метод есть, иначе свои."""
    reader = getattr(window, "_tab_data", None)
    if callable(reader):
        return reader(tab) or {}
    getter = getattr(tab, "get_data", None)
    if callable(getter):
        return getter() or {}
    return {}


def _place_marker(window) -> Optional[Tuple[Any, str, str]]:
    """
    Кладёт маркер в первое строковое поле первой «словарной» вкладки окна.

    Возвращает (вкладка, имя поля, что должны прочитать обратно) или None,
    если подходящей вкладки нет. Смысл: после переключения окон маркер
    обязан остаться прежним — иначе данные формы теряются.
    """
    import inspect

    marker = f"E2E-{window.CONTRACT_TYPE or 'тип'}"
    for key in window._tab_keys():
        tab = _tab_of(window, key)
        if tab is None or not hasattr(tab, "fill_data") \
                or not hasattr(tab, "get_data"):
            continue
        try:
            signature = inspect.signature(tab.fill_data)
        except (TypeError, ValueError):
            continue
        positional = [
            parameter for parameter in signature.parameters.values()
            if parameter.kind in (parameter.POSITIONAL_ONLY,
                                  parameter.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) != 1:
            continue
        data = _tab_data_of(window, tab)
        field = next((name for name, value in data.items()
                      if isinstance(value, str)), "")
        if not field:
            continue
        try:
            tab.fill_data({field: marker})
        except Exception:                                   # noqa: BLE001
            continue
        read_back = str(_tab_data_of(window, tab).get(field, ""))
        if read_back != marker:
            # Поле нормализуется (дата, телефон) — маркер тут не годится.
            continue
        return tab, field, marker
    return None


# ─────────────────────────────────────────────────────────────
# Запуск
# ─────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(
        description="Мощные E2E-сценарии живой программы"
    )
    parser.add_argument("--gigachat", action="store_true",
                        help="один живой запрос распознавания в GigaChat")
    parser.add_argument("--keep-temp", action="store_true",
                        help="не удалять временную папку с базой")
    parser.add_argument("--only", default="",
                        help="ключи сценариев через запятую: gen, db, "
                             "recognition, agent, windows (по умолчанию все)")
    args = parser.parse_args(argv)
    selected = {part.strip() for part in args.only.split(",") if part.strip()}

    def wanted(key: str) -> bool:
        """Выбран ли сценарий (--only; пусто — все)."""
        return not selected or key in selected

    print("=" * 72)
    print("МОЩНЫЕ E2E: живая программа, пять сценариев")
    print("=" * 72)

    from core.private_path import attach_private_root
    from core.contracts.registry import ContractTypeRegistry

    private_root = attach_private_root()
    print(f"Приватная часть: {private_root or 'не найдена'}")
    ContractTypeRegistry.load_builtin()
    print(f"Типы договоров: {', '.join(ContractTypeRegistry.known_types())}")
    failures = ContractTypeRegistry.failures()
    if failures:
        print(f"Типы не загрузились: {failures}")

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="e2e_", dir=str(OUT_ROOT)))
    # База одна на весь прогон и она ИЗОЛИРОВАННАЯ: и сценарий 2, и
    # инструменты агента в сценарии 4 работают с ней, а не с contracts.db
    # оператора. Удаляется вместе с временной папкой.
    _use_isolated_database(work_dir / "e2e_contracts.db")

    results: List[Result] = []
    try:
        if wanted("gen"):
            results.append(_run("Генерация 5 типов", scenario_generation))
        if wanted("db"):
            results.append(_run("Сохранение в БД + загрузка",
                                lambda: scenario_database(work_dir)))
        if wanted("recognition"):
            results.append(_run("Распознавание",
                                lambda: scenario_recognition(args.gigachat)))
        if wanted("agent"):
            results.append(_run("Максимилиан (20 запросов)", scenario_agent))
        if wanted("windows"):
            results.append(_run("Переключение окон", scenario_windows))
    finally:
        if not args.keep_temp:
            shutil.rmtree(work_dir, ignore_errors=True)

    print()
    print("-" * 72)
    width = max(len(r.name) for r in results)
    for result in results:
        mark = "OK  " if result.ok else "FAIL"
        print(f"[{mark}] {result.name:<{width}}  {result.note}  "
              f"({result.seconds:.1f} с)")
    # Подробности печатаются по ВСЕМ сценариям: лог целиком уходит в отчёт,
    # а не пересказывается в чат.
    for result in results:
        print()
        print(f"### {result.name}")
        for line in result.details:
            print(f"  - {line}")

    failed = [r.name for r in results if not r.ok]
    print()
    print("-" * 72)
    if failed:
        print(f"ИТОГ: провалено {len(failed)} из {len(results)}: "
              f"{', '.join(failed)}")
        return 1
    print(f"ИТОГ: {_passed_word(len(results))}")
    return 0


def _passed_word(count: int) -> str:
    """«все 5 сценариев прошли» / «оба сценария прошли» — по-русски."""
    if count == 1:
        return "сценарий пройден"
    if count == 2:
        return "оба сценария прошли"
    tail = count % 10
    if tail == 1 and count % 100 != 11:
        return f"все {count} сценарий прошёл"
    if tail in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return f"все {count} сценария прошли"
    return f"все {count} сценариев прошли"


def _use_isolated_database(db_file: Path) -> None:
    """
    Переводит приложение на ИЗОЛИРОВАННУЮ базу до первого обращения к ней.

    Подменяется ``DB_PATH`` у фасада ``db.database`` — ровно так это делает
    фикстура ``isolated_db`` в тестах, потому что ``db/connection.py``
    спрашивает путь у фасада при каждом соединении.
    """
    import db.database as database

    db_file.parent.mkdir(parents=True, exist_ok=True)
    database.DB_PATH = str(db_file)
    database.init_database()
    print(f"База (изолированная): {db_file}")


def _run(name: str, function) -> Result:
    """Выполняет сценарий, превращая исключение в провал с причиной."""
    print(f"\n>>> {name}")
    try:
        return function()
    except Exception as exc:                                # noqa: BLE001
        detail = traceback.format_exc().strip().splitlines()[-1]
        print(f"!!! {name}: {type(exc).__name__}: {exc}")
        return Result(name, False, f"{type(exc).__name__}: {exc}", 0.0,
                      [f"ПРОВАЛ — {detail}"])


if __name__ == "__main__":
    sys.exit(main())
