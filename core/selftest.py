#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Самопроверка сборки: `LogisticsContractor.exe --selftest`.

Зачем
-----
У собранного приложения нет консоли (`console=False`): если ресурс не
доехал в сборку, программа не скажет об этом — просто не найдёт шаблон
или правовую базу, а оператор увидит «не работает». Самопроверка отвечает
на вопрос «сборка целая или нет» ДО того, как коллега начнёт работать.

Что проверяется
---------------
Не поведение программы (его проверяют 10 000 тестов), а именно то, что
ломается ТОЛЬКО в собранном виде:

* пути: где программа, где ресурсы (core/app_paths.py);
* шаблоны договоров: все имена из генератора на месте и открываются;
* правовая база: каталог правил читается, правила есть;
* инструкции оператора и промпт Максимилиана: файлы найдены и не пусты;
* приватная часть: папка подключена, типы договоров зарегистрированы;
* сертификат НУЦ Минцифры: без него GigaChat не отвечает;
* запись рядом с программой: настройки, база и договоры должны лежать там;
* база: файл читается как SQLite;
* ключи: хранилище Windows доступно, ключи на месте (это СВЕДЕНИЕ, а не
  ошибка: программа работает и без них).

Как запускать
-------------
    LogisticsContractor.exe --selftest

Отчёт пишется в `selftest.txt` рядом с программой (консоли у сборки нет)
и, если консоль всё-таки есть, печатается в неё. Код возврата: 0 — всё
критичное в порядке, 1 — есть отказ.

Модуль без Qt: его можно звать в любом режиме, в том числе без экрана.
"""

import logging
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Tuple

from core import app_paths

logger = logging.getLogger("core.selftest")

#: Имя файла отчёта рядом с программой.
REPORT_FILENAME = "selftest.txt"

#: Статусы проверки. «Отказ» роняет код возврата, «сведение» — нет.
OK = "ok"
WARN = "warn"
FAIL = "fail"

#: Метки для отчёта (латиницей — читается в любой консоли и в блокноте).
STATUS_MARKS = {OK: "OK  ", WARN: "ВНИМ", FAIL: "ОШИБ"}

#: Сколько типов договоров ожидается в полной сборке (публичный и четыре
#: клиентских). Меньше — приватная часть не доехала или сломана.
EXPECTED_CONTRACT_TYPES = 5

#: Тип одной проверки: (статус, подробности).
CheckResult = Tuple[str, str]


def _check_paths() -> CheckResult:
    """Пути приложения: программа и ресурсы должны существовать."""
    detail = f"программа: {app_paths.app_root()}; ресурсы: {app_paths.bundle_root()}"
    if app_paths.app_root().is_dir() and app_paths.bundle_root().is_dir():
        return OK, detail
    return FAIL, detail


def _check_templates() -> CheckResult:
    """Все шаблоны договоров из генератора на месте и открываются."""
    from core.contracts.factory import default_templates_dir
    from core.contract_generator import ContractGenerator

    try:
        generator = ContractGenerator(default_templates_dir())
    except Exception as error:  # noqa: BLE001 — отчёт важнее исключения
        return FAIL, f"генератор не создан: {type(error).__name__}: {error}"

    missing = [name for name, path in generator.templates.items()
               if not Path(path).is_file()]
    if missing:
        return FAIL, f"нет файлов: {', '.join(missing)}"

    # Открытие проверяет не наличие, а что это настоящий DOCX: битый файл
    # даст ошибку здесь, а не при первом договоре у оператора.
    from docx import Document

    for name, path in generator.templates.items():
        try:
            Document(path)
        except Exception as error:  # noqa: BLE001
            return FAIL, f"{name}: {type(error).__name__}: {error}"

    return (
        OK,
        f"{len(generator.templates)} шт., папка {default_templates_dir()}",
    )


def _check_legal_rules() -> CheckResult:
    """Каталог правовых правил читается и не пуст."""
    from core import legal_rules

    rules = legal_rules.load_rules(force=True)
    if not rules:
        return FAIL, f"каталог правил не прочитан: {legal_rules.default_rules_path()}"
    return OK, f"правил: {len(rules)}"


def _check_tutorial() -> CheckResult:
    """Инструкции оператора найдены."""
    from services.agent.tools import tutorial

    folder = Path(tutorial.tutorial_dir())
    if not folder.is_dir():
        return FAIL, f"нет папки: {folder}"
    files = [path for path in folder.glob("*.md") if path.is_file()]
    if not files:
        return FAIL, f"в папке нет *.md: {folder}"
    return OK, f"файлов: {len(files)}, папка {folder}"


def _check_prompts() -> CheckResult:
    """Системный промпт Максимилиана читается."""
    from services.agent.core import prompt as prompt_module

    path = Path(prompt_module.PROMPTS_DIR) / prompt_module.PROMPT_FILE
    if not path.is_file():
        return FAIL, f"нет файла: {path}"
    size = path.stat().st_size
    if size < 1000:
        return FAIL, f"файл подозрительно мал: {size} байт"
    return OK, f"{size} байт"


def _check_private_part() -> CheckResult:
    """Приватная часть подключена, типы договоров зарегистрированы."""
    from core.contracts.registry import ContractTypeRegistry
    from core.private_path import private_root

    root = private_root()
    ContractTypeRegistry.load_builtin()
    types = sorted(ContractTypeRegistry.known_types())
    failures = ContractTypeRegistry.failures()

    if root is None:
        return WARN, "не найдена — доступно только «Экспедиторство»"
    if failures:
        return FAIL, f"сбои загрузки типов: {failures}"
    if len(types) < EXPECTED_CONTRACT_TYPES:
        return (
            FAIL,
            f"типов {len(types)} из {EXPECTED_CONTRACT_TYPES}: {', '.join(types)}",
        )
    return OK, f"{root}; типов договоров: {len(types)}"


def _check_type_windows() -> CheckResult:
    """
    Окна типов договоров импортируются.

    Это проверка «5 типов договоров» из сборки: приватные окна лежат в
    сборке обычными файлами, и их импорты публичного кода PyInstaller не
    видит. Если какой-то модуль не доехал, окно типа откроется пустым или
    не откроется вовсе — у коллеги, а не здесь.
    """
    import importlib

    from core.private_path import private_root

    root = private_root()
    if root is None:
        return WARN, "приватной части нет — открывается только «Экспедиторство»"

    windows_dir = Path(root) / "ui" / "windows"
    if not windows_dir.is_dir():
        return FAIL, f"нет папки окон: {windows_dir}"

    packages = sorted(
        path.name for path in windows_dir.iterdir()
        if (path / "__init__.py").is_file()
    )
    broken = []
    for name in packages:
        try:
            importlib.import_module(f"ui.windows.{name}")
        except Exception as error:  # noqa: BLE001 — нужен полный список
            broken.append(f"{name}: {type(error).__name__}: {error}")

    if broken:
        return FAIL, "; ".join(broken)
    return OK, (
        f"окон: {len(packages) + 1} (с «Экспедиторством»): "
        f"{', '.join(packages)}"
    )


#: Данные для пробной генерации договора: СИНТЕТИЧЕСКИЕ (как в
#: tests/conftest.py::contract_payload). Ни одного настоящего ФИО, паспорта
#: или адреса: самопроверку запускают на машине коллеги, и договор из неё
#: попадает в его папку на несколько секунд.
SAMPLE_CONTRACT_DATA = {
    "driver": {
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
    },
    "carrier": {
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
    },
    "customer": {
        "full_name": "ООО «Заказчик»",
        "short_name": "ООО «Заказчик»",
        "entity_type": "ООО",
        "inn": "7701234592",
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
    },
    "vehicles": [{
        "vin": "EC3TEUMB0T0002608",
        "brand_model": "JETOUR T2",
        "plate_number": "А123ВС77",
        "year": 2024,
        "color": "Белый",
        "vehicle_type": "Легковой автомобиль",
    }],
    "tractor": {
        "brand_model": "Foton Auman",
        "plate_number": "O844XY196",
        "color": "Белый",
        "year": 2023,
    },
    "trailer": {
        "brand_model": "YANGMINDA",
        "plate_number": "71ABF18",
        "color": "Серый",
        "year": 2020,
    },
    "contract": {
        "number": "23092026-74",
        "date": "2026-09-23",
        "route": "Мурманск - Пятигорск",
        "carrier_type": "ООО (с НДС)",
        "vat_rate": "22%",
        "vat_rate_num": 22,
        "price_without_vat": 180300.0,
        "price_with_vat": 219966.0,
        "payment_days": 10,
    },
    "loadings": [{
        "address": "183052, г.Мурманск, пр.Кольский, д.53",
        "date": "2026-09-24",
        "time_window": "09:00-18:00",
    }],
    "unloadings": [{
        "address": "г. Пятигорск, Бештаугорское шоссе 17",
        "date": "2026-09-27",
        "time_window": "",
    }],
}


def _check_generation() -> CheckResult:
    """
    Договор ГЕНЕРИРУЕТСЯ: шаблон, docxtpl и данные работают вместе.

    Это та проверка, ради которой самопроверка и нужна: она проходит путь
    «шаблон → подстановка → файл» целиком, во временной папке, и удаляет
    результат за собой. Если в сборке нет части ресурсов, docxtpl упадёт
    здесь, а не в первый рабочий день коллеги.
    """
    from docx import Document

    from core.contract_generator import ContractGenerator
    from core.contracts.factory import default_templates_dir

    try:
        with tempfile.TemporaryDirectory(prefix="selftest_contract_") as folder:
            generator = ContractGenerator(default_templates_dir())
            path = Path(generator.generate(SAMPLE_CONTRACT_DATA, output_dir=folder))
            if not path.is_file():
                return FAIL, f"файл не создан: {path}"

            size = path.stat().st_size
            if size < 10000:
                return FAIL, f"файл подозрительно мал: {size} байт"

            text = "\n".join(
                paragraph.text for paragraph in Document(path).paragraphs
            )
    except Exception as error:  # noqa: BLE001 — отчёт важнее исключения
        return FAIL, f"{type(error).__name__}: {error}"

    if "ДОГОВОР" not in text.upper():
        return FAIL, "в документе нет текста договора"
    return OK, f"документ {size} байт, текста {len(text)} знаков"


def _check_certificates() -> CheckResult:
    """Сертификат НУЦ Минцифры найден (без него GigaChat не работает)."""
    from infrastructure.api import tls

    bundle = tls.default_ca_bundle()
    if bundle is None:
        return (
            WARN,
            "не найден — GigaChat будет проверять системное хранилище Windows",
        )
    return OK, str(bundle)


def _check_writable() -> CheckResult:
    """Рядом с программой можно писать: там живут настройки и договоры."""
    root = app_paths.app_root()
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="selftest_", suffix=".tmp",
            dir=str(root), delete=True,
        ) as handle:
            handle.write("проверка записи")
    except OSError as error:
        return FAIL, f"{root}: {error}"
    return OK, str(root)


def _check_database() -> CheckResult:
    """Файл базы читается как SQLite (или ещё не создан — это норма)."""
    from db.connection import DB_PATH

    path = Path(DB_PATH)
    if not path.is_file():
        return WARN, f"ещё не создана: {path}"

    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            tables = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
    except sqlite3.Error as error:
        return FAIL, f"{path}: {error}"
    return OK, f"таблиц: {tables}, {path}"


def _check_keys() -> CheckResult:
    """
    Хранилище ключей Windows и наличие ключей.

    Отсутствие ключа — сведение, а не отказ: программа запускается и без
    него (генерация по шаблонам и ручное заполнение формы).
    """
    from core import secrets_store

    if not secrets_store.keyring_available():
        return FAIL, "системное хранилище недоступно — ключи ввести нельзя"

    backend = secrets_store.backend_name()
    gigachat = secrets_store.has_gigachat_key()
    dadata = secrets_store.has_dadata_key()
    if gigachat:
        return OK, (
            f"{backend}; GigaChat есть, DaData {'есть' if dadata else 'нет'}"
        )
    return WARN, (
        f"{backend}; ключа GigaChat нет — программа работает в ограниченном "
        "режиме (ввести: Настройки → «Ключи API…»)"
    )


#: Все проверки в порядке отчёта: (название, функция).
CHECKS: Tuple[Tuple[str, Callable[[], CheckResult]], ...] = (
    ("Пути", _check_paths),
    ("Шаблоны", _check_templates),
    ("Генерация договора", _check_generation),
    ("Правовая база", _check_legal_rules),
    ("Инструкции оператора", _check_tutorial),
    ("Промпт Максимилиана", _check_prompts),
    ("Приватная часть", _check_private_part),
    ("Окна типов", _check_type_windows),
    ("Сертификат GigaChat", _check_certificates),
    ("Запись рядом с программой", _check_writable),
    ("База", _check_database),
    ("Ключи API", _check_keys),
)


def run_checks() -> List[Tuple[str, str, str]]:
    """
    Выполняет все проверки.

    Сбой одной проверки не мешает остальным: коллеге нужен ПОЛНЫЙ список,
    а не первая ошибка. Исключение внутри проверки считается отказом этой
    проверки, а не падением программы.

    :return: список (название, статус, подробности).
    """
    results: List[Tuple[str, str, str]] = []
    for title, check in CHECKS:
        try:
            status, detail = check()
        except Exception as error:  # noqa: BLE001 — отчёт важнее исключения
            logger.exception("Самопроверка «%s» упала", title)
            status, detail = FAIL, f"{type(error).__name__}: {error}"
        results.append((title, status, detail))
    return results


def format_report(results: List[Tuple[str, str, str]]) -> str:
    """Текст отчёта для файла и консоли."""
    failures = sum(1 for _name, status, _detail in results if status == FAIL)
    warnings = sum(1 for _name, status, _detail in results if status == WARN)

    lines = [
        "Самопроверка Logistics Contractor",
        "=" * 60,
        app_paths.describe(),
        "",
    ]
    for name, status, detail in results:
        lines.append(f"[{STATUS_MARKS.get(status, status)}] {name}: {detail}")

    lines.append("")
    if failures:
        lines.append(
            f"ИТОГ: отказов {failures}, сведений {warnings}. "
            "Сборка не готова к работе — см. строки «ОШИБ»."
        )
    elif warnings:
        lines.append(
            f"ИТОГ: отказов нет, сведений {warnings}. "
            "Основа работает; сведения выше — про ключи и данные."
        )
    else:
        lines.append("ИТОГ: всё в порядке.")
    return "\n".join(lines)


def report_path() -> Path:
    """Куда писать отчёт: рядом с программой (там, где можно писать)."""
    return app_paths.writable_path(REPORT_FILENAME)


def run_selftest() -> int:
    """
    Выполняет самопроверку, пишет отчёт и возвращает код выхода.

    :return: 0 — критичных отказов нет; 1 — есть отказ.
    """
    results = run_checks()
    text = format_report(results)
    failures = sum(1 for _name, status, _detail in results if status == FAIL)

    # Консоли у сборки нет (console=False), поэтому печать — не главное:
    # если stdout недоступен, это не ошибка.
    try:
        if sys.stdout is not None:
            print(text)
    except Exception:  # noqa: BLE001 — консоль не обязательна
        pass

    path = report_path()
    try:
        path.write_text(text + "\n", encoding="utf-8")
        logger.info("Самопроверка: отчёт записан в %s", path)
    except OSError as error:
        logger.error("Самопроверка: отчёт не записан (%s): %s", path, error)

    return 1 if failures else 0


__all__ = [
    "CHECKS",
    "EXPECTED_CONTRACT_TYPES",
    "FAIL",
    "OK",
    "REPORT_FILENAME",
    "STATUS_MARKS",
    "WARN",
    "format_report",
    "report_path",
    "run_checks",
    "run_selftest",
]
