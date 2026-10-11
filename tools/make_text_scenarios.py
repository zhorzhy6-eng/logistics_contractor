#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор 1000 синтетических ТЕКСТОВ для стресс-теста распознавания.

    python tools/make_text_scenarios.py --write-seeds      # записать зёрна
    python tools/make_text_scenarios.py --show 5           # посмотреть тексты
    python tools/make_text_scenarios.py --write-seeds --no-db-check

Зачем этот инструмент
---------------------
Стресс-тест `tools/make_synthetic_docs.py` проверяет конвейер
«картинка → OCR → поля»: там ошибки чтения букв смешаны с ошибками разбора.
Здесь проверяется ЧИСТЫЙ РАЗБОР ТЕКСТА: картинок нет, OCR не участвует,
текст сразу уходит в модель (и параллельно — в локальный разборщик).
Так видно, где именно ошибается GigaChat, а где — локальные правила.

Что генерируется
----------------
1000 сценариев по видам (drivers / vehicles / organizations / addresses /
documents) и четырём уровням сложности:

    simple   — стандартная запись: «ФИО: Иванов Иван Иванович»,
               паспорт «18 22 926830», VIN из 17 допустимых символов;
    medium   — лишние пробелы, разделители, номера через дефис;
    hard     — дефисные ФИО, двойные имена, слитные реквизиты
               («Паспорт 1822926830»), реквизиты банка подряд;
    extreme  — апострофы (О'Брайен), нерусские имена (Ли Пэн), кириллица
               и буквы I/O/Q в VIN, опечатки в метках полей.

ВЫМЫШЛЕННЫЕ ДАННЫЕ
------------------
ФИО собираются из словарей `tools.make_synthetic_docs` (те же, что у
картиночного стресс-теста). ИНН и ОГРН считаются с ВАЛИДНОЙ контрольной
суммой — это не украшение: `core.pseudonymizer` обезличивает ИНН только
тогда, когда контрольная сумма сходится, иначе номер уходит в облако
открытым текстом. Телефоны — только диапазон 900–999. Адреса — реальные
улицы из словаря, дома и квартиры вымышленные.

Чего здесь НЕТ
--------------
Ни одного реального значения: ни из `contracts.db`, ни из папки оператора.
Сборщик зёрен дополнительно сверяет каждый сценарий с рабочей базой
(`--db-check`, включён по умолчанию) и перебирает зерно, если
сгенерированное ФИО/паспорт/ИНН случайно совпало с записью в базе.
"""

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.make_synthetic_docs import (  # noqa: E402
    BANKS, CAR_BRANDS, CATEGORIES, CITIES, COLORS, FEMALE_NAMES, ISSUERS,
    MALE_NAMES, ORGANIZATIONS, PATRONYMICS_FEMALE, PATRONYMICS_MALE, STREETS,
    SURNAMES,
)

#: Куда пишутся зёрна (в git; только числа и раскладка).
SEEDS_PATH = PROJECT_ROOT / "tests" / "data" / "synthetic_docs" / "text_seeds.json"

#: Базовое зерно раскладки: план сценариев воспроизводим.
BASE_SEED = 20261011

#: Сколько сценариев каждого вида (требование шага).
KIND_PLAN = {
    "drivers": 250,
    "vehicles": 250,
    "organizations": 250,
    "addresses": 100,
    "documents": 150,
}

#: Раскладка по уровням сложности внутри вида.
LEVEL_PLAN = {
    "drivers":       {"simple": 75, "medium": 90, "hard": 65, "extreme": 20},
    "vehicles":      {"simple": 75, "medium": 90, "hard": 65, "extreme": 20},
    "organizations": {"simple": 75, "medium": 90, "hard": 65, "extreme": 20},
    "addresses":     {"simple": 40, "medium": 35, "hard": 20, "extreme": 5},
    "documents":     {"simple": 50, "medium": 50, "hard": 35, "extreme": 15},
}

LEVELS = ("simple", "medium", "hard", "extreme")
KINDS = tuple(KIND_PLAN)

#: Типы ТС, которые принимает схема приложения (core.document_import_service).

#: Нерусские и апострофные имена — уровень extreme. Фамилия, имя, отчество.
FOREIGN_NAMES = [
    ("О'Брайен", "Шон", "Патрикович"),
    ("О'Коннелл", "Брендан", "Шонович"),
    ("Д'Артаньян", "Пётр", "Иванович"),
    ("Мак'Дональд", "Иван", "Петрович"),
    ("Ли", "Пэн", "Артёмович"),
    ("Ким", "Дин", "Сергеевич"),
    ("Чжан", "Вэй", "Андреевич"),
    ("Ван", "Лэй", "Николаевич"),
    ("Абдуллаев", "Азиз", "Рустамович"),
    ("Мамедов", "Эльчин", "Тимурович"),
    ("Гасанов", "Рустам", "Ильдарович"),
    ("Назарбаев", "Ерлан", "Маратович"),
]

#: Двойные имена для уровня hard: имя и отчество пишутся через дефис.
DOUBLE_NAMES = [
    ("Иван-Сергей", "Петрович"),
    ("Тимур-Али", "Валерьевич"),
    ("Анна-Мария", "Ивановна"),
    ("Пётр-Павел", "Сергеевич"),
    ("Лейла-Мария", "Ахмедовна"),
]

#: Метки полей для текстов сценариев.
LABELS = {
    "full_name": "ФИО",
    "birth_date": "Дата рождения",
    "birth_place": "Место рождения",
    "passport": "Паспорт",
    "passport_issue_date": "Дата выдачи",
    "passport_issuer": "Кем выдан",
    "passport_code": "Код подразделения",
    "registration_address": "Адрес регистрации",
    "license": "Водительское удостоверение",
    "license_issue_date": "Дата выдачи ВУ",
    "license_expiry_date": "Действительно до",
    "license_categories": "Категории",
    "phone": "Телефон",
    "vin": "VIN",
    "brand_model": "Марка, модель",
    "plate_number": "Гос. номер",
    "year": "Год выпуска",
    "color": "Цвет",
    "vehicle_type": "Тип ТС",
    "inn": "ИНН",
    "kpp": "КПП",
    "ogrn": "ОГРН",
    "legal_address": "Юридический адрес",
    "actual_address": "Фактический адрес",
    "bank_account": "Расчетный счет",
    "bik": "БИК",
    "correspondent_account": "Корреспондентский счет",
    "bank_name": "Банк",
    "director_name": "Директор",
    "director_position": "Должность",
    "email": "E-mail",
    "loading_address": "Адрес погрузки",
    "unloading_address": "Адрес выгрузки",
    "contract_number": "Номер договора",
    "contract_date": "Дата договора",
    "route": "Маршрут",
    "vat_rate": "Ставка НДС",
    "payment_days": "Срок оплаты",
}

#: Опечатки в метках — только уровень extreme. Значение остаётся читаемым
#: человеком: «Поспорт 47 12 553107» понятно и без словаря.
LABEL_TYPOS = {
    "vin": ("ВИН", "VIN-код", "VIN №"),
    "inn": ("ИНН.", "ИНН№", "ИHH"),
    "ogrn": ("ОГРН.", "ОГРН№"),
    "plate_number": ("Госномер", "Г/н", "Гос.номер"),
    "phone": ("Тел.", "Тел", "Телефон."),
    "brand_model": ("Марка/модель", "Марка модель"),
    "vehicle_type": ("Тип транспорта", "Вид ТС"),
    "bank_account": ("Р/с", "Расч. счет"),
    "correspondent_account": ("К/с", "Кор. счет"),
    "registration_address": ("Адрес регистрации.", "Адрес рег."),
    "director_name": ("Руководитель", "Директор."),
    "loading_address": ("Адрес погрузки.", "Пункт погрузки"),
    "unloading_address": ("Адрес выгрузки.", "Пункт выгрузки"),
}

#: Типы ТС, которые принимает схема приложения (core.document_import_service).
VEHICLE_TYPES = ("Легковой автомобиль", "Тягач", "Прицеп", "Фургон", "Автобус")

#: Должности руководителя.
POSITIONS = ("Генеральный директор", "Директор", "Исполняющий обязанности директора")

#: Адресные детали: корпус, строение, владение — «сложные» уровни.
ADDRESS_TAILS = ("", "кв. {}", "офис {}", "помещение {}", "кв. {}, подъезд 3")
BUILDING_WORDS = ("д.", "дом", "стр.", "влд.")


# ─────────────────────────────────────────────────────────────
# Контрольные суммы: ИНН и ОГРН
# ─────────────────────────────────────────────────────────────

def inn_checksum_ok(digits: str) -> bool:
    """Проверка контрольной суммы ИНН (алгоритм ФНС) — 10 или 12 цифр."""
    if len(digits) == 10:
        weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        return sum(int(d) * w for d, w in zip(digits, weights)) % 11 % 10 == int(digits[9])
    if len(digits) == 12:
        weights_11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        weights_12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        check_11 = sum(int(d) * w for d, w in zip(digits, weights_11)) % 11 % 10
        check_12 = sum(int(d) * w for d, w in zip(digits, weights_12)) % 11 % 10
        return check_11 == int(digits[10]) and check_12 == int(digits[11])
    return False


def ogrn_checksum_ok(digits: str) -> bool:
    """Проверка контрольной суммы ОГРН (13 цифр) и ОГРНИП (15 цифр)."""
    if len(digits) == 13:
        return int(digits[12]) == (int(digits[:12]) % 11) % 10
    if len(digits) == 15:
        return int(digits[14]) == (int(digits[:14]) % 13) % 10
    return False


# ─────────────────────────────────────────────────────────────
# Генераторы значений
# ─────────────────────────────────────────────────────────────

def _digits(rng: random.Random, length: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(length))


def _inn(rng: random.Random, is_ip: bool) -> str:
    """ИНН с валидной контрольной суммой: 12 цифр у ИП, 10 у организации."""
    if is_ip:
        base = _digits(rng, 10)
        weights_11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        d11 = sum(int(d) * w for d, w in zip(base, weights_11)) % 11 % 10
        weights_12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        d12 = sum(int(d) * w for d, w in zip(base + str(d11), weights_12)) % 11 % 10
        return base + str(d11) + str(d12)
    base = _digits(rng, 9)
    weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
    check = sum(int(d) * w for d, w in zip(base, weights)) % 11 % 10
    return base + str(check)


def _ogrn(rng: random.Random, is_ip: bool) -> str:
    """ОГРН (13 цифр) или ОГРНИП (15 цифр) с валидной контрольной суммой."""
    if is_ip:
        base = "3" + _digits(rng, 13)
        return base + str((int(base) % 13) % 10)
    base = "1" + _digits(rng, 11)
    return base + str((int(base) % 11) % 10)


def _kpp(rng: random.Random, inn: str) -> str:
    """КПП: первые 4 цифры ИНН + код причины + 3 цифры."""
    return inn[:4] + rng.choice(("01", "02", "43", "50")) + _digits(rng, 3)


def _bank_account(rng: random.Random) -> str:
    return "40" + _digits(rng, 18)


def _phone(rng: random.Random) -> str:
    """Мобильный из диапазона 900–999 (требование шага)."""
    return (f"+7 ({rng.randint(900, 999)}) "
            f"{rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(10, 99)}")


def _vin(rng: random.Random) -> str:
    """VIN из 17 допустимых символов: без I, O, Q."""
    alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    return "X" + "".join(rng.choice(alphabet) for _ in range(16))


def _plate(rng: random.Random) -> str:
    letters = "АВЕКМНОРСТУХ"
    return (f"{rng.choice(letters)}{rng.randint(100, 999)}"
            f"{rng.choice(letters)}{rng.choice(letters)}"
            f"{rng.choice(('77', '78', '50', '66', '23', '161', '196', '799'))}")


def _date(rng: random.Random, start_year: int, end_year: int) -> str:
    return (f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}."
            f"{rng.randint(start_year, end_year)}")


def _person(rng: random.Random, level: str) -> str:
    """ФИО, записанное так, как его пишут на разных уровнях сложности."""
    if level == "extreme":
        surname, name, patronymic = rng.choice(FOREIGN_NAMES)
        return f"{surname} {name} {patronymic}"
    gender = "female" if rng.random() < 0.5 else "male"
    if gender == "female":
        surname = rng.choice([s for s in SURNAMES if s.endswith(("а", "я"))])
        name = rng.choice(FEMALE_NAMES)
        patronymic = rng.choice(PATRONYMICS_FEMALE)
    else:
        surname = rng.choice([s for s in SURNAMES
                              if not s.endswith(("а", "я")) and "-" not in s])
        name = rng.choice(MALE_NAMES)
        patronymic = rng.choice(PATRONYMICS_MALE)
    if level == "hard":
        surname = rng.choice([s for s in SURNAMES if "-" in s])
        double_name, patronymic = rng.choice(DOUBLE_NAMES)
        name = double_name
    if level == "medium":
        # Двойные пробелы внутри значения — так выглядят вставки из PDF.
        return f"{surname}  {name}   {patronymic}"
    return f"{surname} {name} {patronymic}"


def _passport(rng: random.Random, level: str) -> Tuple[str, str, str]:
    """
    Паспорт: (серия, номер, как напечатано в тексте).

    Формат записи подобран так, чтобы `core.pseudonymizer` его ОБЕЗЛИЧИЛ:
    детектор ждёт «4 цифры серии + 6 цифр номера» рядом. Формат
    «серия 47 12 номер 553107» детектор не ловит — в стресс-тесте он не
    используется (см. README_TEXT.md, раздел «Известные пробелы»).
    """
    series = f"{rng.randint(10, 99)} {rng.randint(10, 99)}"
    number = _digits(rng, 6)
    if level == "simple":
        return series, number, f"{series} {number}"
    if level == "medium":
        return series, number, f"{series.replace(' ', '-')} №{number}"
    if level == "hard":
        return series, number, f"{series.replace(' ', '')}{number}"
    return series, number, f"{series.replace(' ', '')}№{number}"


def _license(rng: random.Random, level: str) -> Tuple[str, str, str]:
    """ВУ: (серия, номер, как напечатано). Формат — как у паспорта."""
    series = f"{rng.randint(10, 99)} {rng.randint(10, 99)}"
    number = _digits(rng, 6)
    if level in ("simple", "medium"):
        return series, number, f"{series} {number}"
    if level == "hard":
        return series, number, f"{series.replace(' ', '')}{number}"
    return series, number, f"{series.replace(' ', '-')}-{number}"


def _address(rng: random.Random, level: str) -> str:
    """Адрес: реальная улица из словаря, вымышленный дом."""
    city = rng.choice(CITIES)
    street = rng.choice(STREETS)
    house = rng.randint(1, 240)
    tail = rng.choice(ADDRESS_TAILS)
    tail = tail.format(rng.randint(1, 400)) if "{}" in tail else tail
    if level == "simple":
        return f"{city}, {street}, д. {house}" + (f", {tail}" if tail else "")
    if level == "medium":
        return f"{city}, {street}, {house}" + (f", {tail}" if tail else "")
    if level == "hard":
        building = rng.choice(BUILDING_WORDS)
        return (f"{rng.randint(100000, 699999)}, {city}, {street}, "
                f"{building} {house}" + (f", {tail}" if tail else ""))
    return (f"{city}, {street.replace('ул. ', 'ул.')}, "
            f"{rng.choice(BUILDING_WORDS)}{house}, "
            f"корп. {rng.randint(1, 8)}" + (f", {tail}" if tail else ""))


def _org_name(rng: random.Random, level: str) -> Tuple[str, str, bool]:
    """(полное имя, краткое имя, это ИП)."""
    if level == "extreme" and rng.random() < 0.35:
        surname = rng.choice([s for s in SURNAMES if not s.endswith(("а", "я"))])
        name = rng.choice(MALE_NAMES)
        patronymic = rng.choice(PATRONYMICS_MALE)
        full = f"Индивидуальный предприниматель {surname} {name} {patronymic}"
        return full, f"ИП {surname} {name[0]}.{patronymic[0]}.", True
    full = rng.choice(ORGANIZATIONS)
    short = full.replace("ООО «", "ООО ").replace("»", "")
    if level == "hard":
        full = full.replace("ООО «", "Общество с ограниченной ответственностью «")
    return full, short, False


def _render(label_key: str, value: str, level: str, rng: random.Random) -> str:
    """Строка «Метка: значение» с учётом уровня сложности."""
    label = LABELS[label_key]
    if level == "extreme":
        options = LABEL_TYPOS.get(label_key)
        if options:
            label = rng.choice(options)
    if level == "simple":
        return f"{label}: {value}"
    if level == "medium":
        return f"{label} : {value}"
    if level == "hard":
        return f"{label}:{value}"
    return f"{label} {value}"


def _bank(rng: random.Random) -> Tuple[str, str, str]:
    return rng.choice(BANKS)


# ─────────────────────────────────────────────────────────────
# Сборка сценария
# ─────────────────────────────────────────────────────────────

def build_scenario(rng: random.Random, kind: str, level: str) -> Dict[str, Any]:
    """
    Один сценарий: {"text": ..., "ground_truth": {...}}.

    ground_truth — в той же форме, что отдаёт `extract_local_fields`:
    разделы driver / carrier / customer / vehicles / tractor / trailer /
    contract, внутри — имена полей из `core.document_import_service.SCHEMA`.
    Только так метрику можно свести одной формулой (`tools.synthetic_stress.score`).

    В ground_truth попадают ТОЛЬКО те поля, которые есть в тексте: пустое
    поле нечего «вспоминать», и оно не должно портить recall.
    """
    if kind == "drivers":
        lines, gt = _build_drivers(rng, level)
    elif kind == "vehicles":
        lines, gt = _build_vehicles(rng, level)
    elif kind == "organizations":
        lines, gt = _build_organizations(rng, level)
    elif kind == "addresses":
        lines, gt = _build_addresses(rng, level)
    elif kind == "documents":
        lines, gt = _build_documents(rng, level)
    else:
        raise ValueError(f"неизвестный вид сценария: {kind!r}")
    return {"text": "\n".join(lines), "ground_truth": gt}


def _driver_fields(rng: random.Random, level: str, lines: List[str],
                   full: bool = True) -> Dict[str, Any]:
    """Блок водителя: строки текста и ground truth раздела driver."""
    driver: Dict[str, Any] = {}
    fio = _person(rng, level)
    driver["full_name"] = fio.strip()
    lines.append("ВОДИТЕЛЬ")
    lines.append(_render("full_name", fio, level, rng))

    if level != "simple" or rng.random() < 0.5:
        birth = _date(rng, 1960, 2003)
        driver["birth_date"] = birth
        lines.append(_render("birth_date", birth, level, rng))

    series, number, shown = _passport(rng, level)
    driver["passport_series"] = series
    driver["passport_number"] = number
    lines.append(_render("passport", shown, level, rng))

    if level in ("hard", "extreme") or rng.random() < 0.5:
        driver["passport_issue_date"] = _date(rng, 2005, 2024)
        lines.append(_render("passport_issue_date",
                             driver["passport_issue_date"], level, rng))
    if level in ("medium", "hard", "extreme"):
        driver["passport_issuer"] = rng.choice(ISSUERS)
        lines.append(_render("passport_issuer", driver["passport_issuer"], level, rng))
    if level in ("hard", "extreme"):
        driver["passport_code"] = f"{rng.randint(100, 999)}-{rng.randint(100, 999)}"
        lines.append(_render("passport_code", driver["passport_code"], level, rng))
        driver["birth_place"] = f"{rng.choice(CITIES)}, {rng.choice(STREETS)}"
        lines.append(_render("birth_place", driver["birth_place"], level, rng))

    if level in ("medium", "hard", "extreme"):
        driver["phone"] = _phone(rng)
        lines.append(_render("phone", driver["phone"], level, rng))

    if full and level in ("hard", "extreme"):
        driver["registration_address"] = _address(rng, level)
        lines.append(_render("registration_address",
                             driver["registration_address"], level, rng))
    elif level == "medium" and rng.random() < 0.5:
        driver["registration_address"] = _address(rng, level)
        lines.append(_render("registration_address",
                             driver["registration_address"], level, rng))

    if full and level in ("hard", "extreme"):
        license_series, license_number, license_shown = _license(rng, level)
        driver["license_series"] = license_series
        driver["license_number"] = license_number
        lines.append("ВОДИТЕЛЬСКОЕ УДОСТОВЕРЕНИЕ")
        lines.append(_render("license", license_shown, level, rng))
        driver["license_issue_date"] = _date(rng, 2010, 2024)
        lines.append(_render("license_issue_date",
                             driver["license_issue_date"], level, rng))
        driver["license_expiry_date"] = _date(rng, 2027, 2036)
        lines.append(_render("license_expiry_date",
                             driver["license_expiry_date"], level, rng))
        driver["license_categories"] = rng.choice(CATEGORIES)
        lines.append(_render("license_categories",
                             driver["license_categories"], level, rng))
    return driver


def _vehicle_fields(rng: random.Random, level: str, lines: List[str],
                    vin: Optional[str] = None) -> Dict[str, Any]:
    """Блок автомобиля: строка текста и ground truth одной машины."""
    value = vin or _vin(rng)
    if level == "extreme" and rng.random() < 0.5:
        # Кириллица и запрещённые в VIN буквы I/O/Q — так выглядит ввод
        # оператора и распознавание бумажного ПТС.
        cyrillic = {"A": "А", "B": "В", "E": "Е", "K": "К", "M": "М",
                    "H": "Н", "P": "Р", "C": "С", "T": "Т", "X": "Х"}
        value = "".join(cyrillic.get(ch, ch) for ch in value)
        if rng.random() < 0.5:
            value = value[:8] + rng.choice("IOQ") + value[9:]
    elif level == "hard" and rng.random() < 0.5:
        value = value.lower()
    vehicle: Dict[str, Any] = {"vin": value}
    lines.append("АВТОМОБИЛЬ")
    if level == "medium":
        lines.append(f"{LABELS['vin']}: {value[:3]} {value[3:9]} {value[9:]}")
    else:
        lines.append(_render("vin", value, level, rng))
    vehicle["brand_model"] = rng.choice(CAR_BRANDS)
    lines.append(_render("brand_model", vehicle["brand_model"], level, rng))
    vehicle["plate_number"] = _plate(rng)
    lines.append(_render("plate_number", vehicle["plate_number"], level, rng))
    if level != "simple" or rng.random() < 0.5:
        vehicle["year"] = rng.randint(2005, 2025)
        lines.append(_render("year", str(vehicle["year"]), level, rng))
    if level in ("medium", "hard", "extreme"):
        vehicle["color"] = rng.choice(COLORS)
        lines.append(_render("color", vehicle["color"], level, rng))
        vehicle["vehicle_type"] = rng.choice(VEHICLE_TYPES)
        lines.append(_render("vehicle_type", vehicle["vehicle_type"], level, rng))
    return vehicle


def _tractor_or_trailer(rng: random.Random, level: str, lines: List[str],
                        title: str) -> Dict[str, Any]:
    """Блок тягача или полуприцепа (в схеме у них нет VIN)."""
    lines.append(title)
    data = {
        "brand_model": rng.choice(CAR_BRANDS),
        "plate_number": _plate(rng),
        "color": rng.choice(COLORS),
        "year": rng.randint(2008, 2025),
    }
    for key in ("brand_model", "plate_number", "color", "year"):
        lines.append(_render(key, str(data[key]), level, rng))
    return data


def _organization_fields(rng: random.Random, level: str, lines: List[str],
                         title: str, section: str) -> Dict[str, Any]:
    """Блок организации: строки текста и ground truth carrier/customer."""
    data: Dict[str, Any] = {}
    full, _short, is_ip = _org_name(rng, level)
    data["full_name"] = full
    data["entity_type"] = "ИП" if is_ip else "ООО"
    lines.append(title)
    lines.append(full)

    inn = _inn(rng, is_ip)
    data["inn"] = inn
    lines.append(_render("inn", inn, level, rng))
    if not is_ip:
        data["kpp"] = _kpp(rng, inn)
        lines.append(_render("kpp", data["kpp"], level, rng))
    data["ogrn"] = _ogrn(rng, is_ip)
    lines.append(_render("ogrn", data["ogrn"], level, rng))

    if level in ("medium", "hard", "extreme"):
        data["legal_address"] = _address(rng, level)
        lines.append(_render("legal_address", data["legal_address"], level, rng))
    if level in ("hard", "extreme"):
        data["actual_address"] = _address(rng, level)
        lines.append(_render("actual_address", data["actual_address"], level, rng))
        bank_name, bik, corr = _bank(rng)
        data["bank_name"] = bank_name
        data["bik"] = bik
        data["correspondent_account"] = corr
        data["bank_account"] = _bank_account(rng)
        if level == "hard":
            # Реквизиты подряд, как в выписке: метки идут слитно.
            lines.append(f"Р/с {data['bank_account']} БИК {bik} К/с {corr} "
                         f"Банк {bank_name}")
        else:
            for key in ("bank_account", "bik", "correspondent_account", "bank_name"):
                lines.append(_render(key, str(data[key]), level, rng))
        data["director_name"] = _person(rng, "simple")
        lines.append(_render("director_name", data["director_name"], level, rng))
        data["director_position"] = rng.choice(POSITIONS)
        lines.append(_render("director_position", data["director_position"], level, rng))
        if level == "extreme" or rng.random() < 0.5:
            data["phone"] = _phone(rng)
            lines.append(_render("phone", data["phone"], level, rng))
            data["email"] = (f"office{rng.randint(10, 999)}@"
                             f"{rng.choice(('trans', 'cargo', 'logistik', 'auto'))}"
                             f"-{rng.choice(('sever', 'yug', 'ural', 'volga'))}.ru")
            lines.append(_render("email", data["email"], level, rng))
    return data


def _build_drivers(rng: random.Random, level: str) -> Tuple[List[str], Dict[str, Any]]:
    lines: List[str] = []
    driver = _driver_fields(rng, level, lines)
    return lines, {"driver": driver}


def _build_vehicles(rng: random.Random, level: str) -> Tuple[List[str], Dict[str, Any]]:
    lines: List[str] = []
    gt: Dict[str, Any] = {}
    if level == "simple":
        gt["vehicles"] = [_vehicle_fields(rng, level, lines)]
    elif level == "medium":
        gt["vehicles"] = [_vehicle_fields(rng, level, lines)]
        gt["tractor"] = _tractor_or_trailer(rng, level, lines, "СЕДЕЛЬНЫЙ ТЯГАЧ")
    elif level == "hard":
        gt["vehicles"] = [_vehicle_fields(rng, level, lines)]
        gt["tractor"] = _tractor_or_trailer(rng, level, lines, "ТЯГАЧ")
        gt["trailer"] = _tractor_or_trailer(rng, level, lines, "ПОЛУПРИЦЕП")
    else:
        gt["vehicles"] = [_vehicle_fields(rng, level, lines)]
        gt["tractor"] = _tractor_or_trailer(rng, level, lines, "Седельный тягач")
    return lines, gt


def _build_organizations(rng: random.Random,
                         level: str) -> Tuple[List[str], Dict[str, Any]]:
    lines: List[str] = []
    # Каждый пятый сценарий — «Заказчик»: проверяется правило системного
    # промпта «реквизиты по умолчанию перевозчика, кроме слова Заказчик».
    as_customer = level == "extreme" and rng.random() < 0.5
    if as_customer:
        section, title = "customer", "ЗАКАЗЧИК"
    else:
        section, title = "carrier", "ПЕРЕВОЗЧИК"
    data = _organization_fields(rng, level, lines, title, section)
    return lines, {section: data}


def _build_addresses(rng: random.Random, level: str) -> Tuple[List[str], Dict[str, Any]]:
    """
    Адресные сценарии.

    «Адрес погрузки» / «Адрес выгрузки» лежат в разделе contract. Локальный
    разборщик меток этих полей не знает вовсе (`core.document_fields`), так
    что здесь заранее видно, где облако сильнее правил, — это и есть предмет
    сравнения в отчёте.
    """
    lines: List[str] = []
    gt: Dict[str, Any] = {}
    pool = ["driver.registration_address", "carrier.legal_address",
            "carrier.actual_address"]
    if level in ("hard", "extreme"):
        pool += ["contract.loading_address", "contract.unloading_address"]
    count = {"simple": 1, "medium": 2, "hard": 3, "extreme": 4}[level]
    picked = rng.sample(pool, min(count, len(pool)))

    driver: Dict[str, Any] = {}
    carrier: Dict[str, Any] = {}
    contract: Dict[str, Any] = {}
    for item in picked:
        section, key = item.split(".")
        value = _address(rng, level)
        {"driver": driver, "carrier": carrier, "contract": contract}[section][key] = value

    # Блоки идут в том же порядке, что и в жизни: перевозчик, водитель,
    # маршрут. Заголовок ставится только перед своим блоком — иначе адрес
    # водителя попал бы под заголовок «ПЕРЕВОЗЧИК».
    if carrier:
        lines.append("ПЕРЕВОЗЧИК")
        for key, value in carrier.items():
            lines.append(_render(key, value, level, rng))
        gt["carrier"] = carrier
    if driver:
        lines.append("ВОДИТЕЛЬ")
        for key, value in driver.items():
            lines.append(_render(key, value, level, rng))
        gt["driver"] = driver
    if contract:
        lines.append("МАРШРУТ")
        for key, value in contract.items():
            lines.append(_render(key, value, level, rng))
        gt["contract"] = contract
    return lines, gt


def _build_documents(rng: random.Random, level: str) -> Tuple[List[str], Dict[str, Any]]:
    """Смешанный документ: несколько блоков в одном тексте."""
    lines: List[str] = []
    gt: Dict[str, Any] = {}
    has_driver = True
    has_vehicle = True
    has_org = level in ("medium", "hard", "extreme")
    has_contract = level in ("hard", "extreme")

    if has_org and rng.random() < 0.5:
        data = _organization_fields(rng, level, lines, "ПЕРЕВОЗЧИК", "carrier")
        gt["carrier"] = data
        if rng.random() < 0.4:
            gt["customer"] = _organization_fields(
                rng, level, lines, "ЗАКАЗЧИК", "customer")
    if has_driver:
        gt["driver"] = _driver_fields(rng, level, lines, full=level != "simple")
    if has_vehicle:
        if level in ("hard", "extreme"):
            gt["vehicles"] = [_vehicle_fields(rng, level, lines)]
            gt["tractor"] = _tractor_or_trailer(rng, level, lines, "ТЯГАЧ")
        else:
            gt["vehicles"] = [_vehicle_fields(rng, level, lines)]
    if has_contract:
        contract: Dict[str, Any] = {}
        lines.append("ДОГОВОР")
        contract["number"] = f"АР-{rng.randint(100, 9999)}/{rng.randint(2024, 2026)}"
        lines.append(_render("contract_number", contract["number"], level, rng))
        contract["date"] = _date(rng, 2024, 2026)
        lines.append(_render("contract_date", contract["date"], level, rng))
        contract["route"] = (f"{rng.choice(CITIES)} — {rng.choice(CITIES)}")
        lines.append(_render("route", contract["route"], level, rng))
        contract["vat_rate"] = "22%"
        lines.append(_render("vat_rate", "22%", level, rng))
        contract["payment_days"] = rng.choice((5, 10, 14, 30))
        lines.append(_render("payment_days", f"{contract['payment_days']} дней",
                             level, rng))
        gt["contract"] = contract
    return lines, gt


# ─────────────────────────────────────────────────────────────
# План сценариев и зёрна
# ─────────────────────────────────────────────────────────────

def build_plan(seed: int = BASE_SEED) -> List[Tuple[str, str]]:
    """
    Раскладка «вид × уровень» в порядке прогона.

    Порядок ПЕРЕМЕШАН детерминированно: прогон 1000 сценариев идёт пять
    часов и может оборваться на лимите запросов. Если бы первые 250 были
    только «водителями», частичные метрики описывали бы один вид; при
    перемешивании выборка первых N сценариев представительна.
    """
    plan: List[Tuple[str, str]] = []
    for kind in KINDS:
        for level in LEVELS:
            plan.extend([(kind, level)] * LEVEL_PLAN[kind][level])
    rng = random.Random(seed)
    rng.shuffle(plan)
    return plan


def scenario_seed(rng: random.Random) -> int:
    """Зерно одного сценария: положительное целое (как в seeds.json)."""
    return rng.randint(1, 2 ** 31 - 1)


def scenario_from_seed(seed: int, kind: str, level: str) -> Dict[str, Any]:
    """Сценарий по зерну: одно зерно — один текст, побайтово воспроизводимо."""
    return build_scenario(random.Random(seed), kind, level)


def _pii_values(scenario: Dict[str, Any]) -> List[str]:
    """Значения сценария, которые нельзя отправлять в облако открытыми."""
    keys = ("full_name", "passport_series", "passport_number", "license_series",
            "license_number", "inn", "ogrn", "vin", "plate_number", "phone",
            "registration_address", "legal_address", "actual_address",
            "bank_account", "correspondent_account", "birth_place")
    found: List[str] = []
    for section, payload in scenario["ground_truth"].items():
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in keys:
                value = str(item.get(key) or "").strip()
                if value:
                    found.append(value)
    return found


def build_seeds(seed: int = BASE_SEED,
                collision_check: Optional[Callable[[Dict[str, Any]], List[str]]] = None,
                max_attempts: int = 40) -> Tuple[List[int], List[Dict[str, Any]]]:
    """
    Зёрна всех сценариев.

    :param collision_check: необязательная проверка «сценарий совпал с рабочей
        базой». Возвращает список совпадений; непустой список — зерно
        отбрасывается и берётся следующее. Так в зёрнах не остаётся
        сценария, который упрётся в проверку безопасности на прогоне.
    """
    plan = build_plan(seed)
    chooser = random.Random(seed)
    seeds: List[int] = []
    scenarios: List[Dict[str, Any]] = []
    for kind, level in plan:
        picked, last_collisions = None, []
        for _ in range(max_attempts):
            candidate = scenario_seed(chooser)
            scenario = scenario_from_seed(candidate, kind, level)
            collisions = collision_check(scenario) if collision_check else []
            if not collisions:
                picked = candidate
                break
            last_collisions = collisions
        else:
            raise RuntimeError(
                f"{kind}/{level}: не нашлось зерна без совпадений с базой "
                f"за {max_attempts} попыток (последние: {last_collisions[:3]})"
            )
        seeds.append(picked)
        scenarios.append({"number": len(seeds), "kind": kind, "level": level})
    return seeds, scenarios


def write_seeds(path: Path = SEEDS_PATH, seed: int = BASE_SEED,
                collision_check: Optional[Callable[[Dict[str, Any]], List[str]]] = None
                ) -> Dict[str, Any]:
    """Записывает text_seeds.json: только числа и раскладка, без значений."""
    seeds, scenarios = build_seeds(seed, collision_check)
    payload = {
        "version": 1,
        "seed": seed,
        "count": len(seeds),
        "kinds": dict(KIND_PLAN),
        "levels": {kind: dict(LEVEL_PLAN[kind]) for kind in KINDS},
        "seeds": seeds,
        "scenarios": scenarios,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return payload


def load_seeds(path: Path = SEEDS_PATH) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────
# Сверка с рабочей базой
# ─────────────────────────────────────────────────────────────

#: Таблицы и колонки рабочей базы, в которых могут лежать значения сценария.
DB_COLUMNS = {
    "drivers": ("full_name", "passport_series", "passport_number",
                "registration_address", "phone", "license_series",
                "license_number", "birth_place"),
    "carriers": ("full_name", "short_name", "inn", "kpp", "ogrn",
                 "legal_address", "actual_address", "bank_account", "bik",
                 "correspondent_account", "bank_name", "director_name", "phone",
                 "email"),
    "customers": ("full_name", "short_name", "inn", "kpp", "ogrn",
                  "legal_address", "actual_address", "bank_account", "bik",
                  "correspondent_account", "bank_name", "director_name", "phone",
                  "email"),
    "vehicles": ("vin", "plate_number"),
    "driver_vehicles": ("tractor_plate", "trailer_plate"),
    "contract_points": ("address",),
}


def db_haystack(db_path: Optional[Path] = None) -> List[str]:
    """
    Строки рабочей базы, по которым ищутся совпадения.

    Каждая запись — склейка значений строки плюс их «только цифры» вариант:
    SQL LIKE не нашёл бы «47 12» в «4712», а проверка безопасности обязана
    быть строже, а не мягче. Файл открывается ТОЛЬКО на чтение (`mode=ro`),
    содержимое наружу не отдаётся — возвращаются строки для сравнения.
    """
    import sqlite3

    db_path = Path(db_path or (PROJECT_ROOT / "contracts.db"))
    if not db_path.exists():
        return []
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows: List[str] = []
    try:
        cursor = connection.cursor()
        for table, columns in DB_COLUMNS.items():
            try:
                cursor.execute(f'SELECT {", ".join(columns)} FROM "{table}"')
            except sqlite3.Error:
                continue
            for row in cursor.fetchall():
                parts = [str(value) for value in row if value not in (None, "")]
                if not parts:
                    continue
                joined = " ".join(parts)
                digits = "".join(ch for ch in joined if ch.isdigit())
                rows.append(joined.casefold())
                if digits:
                    rows.append(digits)
    finally:
        connection.close()
    return rows


def find_db_collisions(scenario: Dict[str, Any],
                       haystack: Optional[List[str]] = None,
                       db_path: Optional[Path] = None) -> List[str]:
    """
    Значения сценария, которые встречаются в рабочей базе.

    :return: список найденных значений (пустой — совпадений нет). Сами
        значения синтетические, но если такое же есть в базе оператора,
        сценарий в прогон не идёт: проверка безопасности должна быть
        строгой (`AGENTS.md` § 4).
    """
    rows = haystack if haystack is not None else db_haystack(db_path)
    if not rows:
        return []
    collisions: List[str] = []
    for value in _pii_values(scenario):
        needle = value.casefold().strip()
        if len(needle) < 4:
            continue
        digits = "".join(ch for ch in needle if ch.isdigit())
        hit = any(needle in row for row in rows)
        if not hit and len(digits) >= 4:
            hit = any(digits in row for row in rows)
        if hit:
            collisions.append(value)
    return collisions


# ─────────────────────────────────────────────────────────────
# Командная строка
# ─────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Генератор 1000 синтетических текстов для стресс-теста.")
    parser.add_argument("--write-seeds", action="store_true",
                        help="записать tests/data/synthetic_docs/text_seeds.json")
    parser.add_argument("--show", type=int, default=0,
                        help="показать N первых сценариев (текст и эталон)")
    parser.add_argument("--seed", type=int, default=BASE_SEED,
                        help="базовое зерно раскладки")
    parser.add_argument("--no-db-check", action="store_true",
                        help="не сверять сценарии с рабочей contracts.db")
    args = parser.parse_args(argv)

    check = None
    if not args.no_db_check:
        rows = db_haystack()
        if rows:
            print(f"Сверка с рабочей базой: строк для поиска {len(rows)}")
            check = lambda scenario: find_db_collisions(scenario, rows)  # noqa: E731
        else:
            print("Рабочая база не найдена — сверка пропущена")

    if args.write_seeds:
        payload = write_seeds(SEEDS_PATH, args.seed, check)
        print(f"Записано {payload['count']} зёрен: {SEEDS_PATH}")

    if args.show:
        seeds, scenarios = build_seeds(args.seed, check)
        for number in range(min(args.show, len(seeds))):
            meta = scenarios[number]
            scenario = scenario_from_seed(seeds[number], meta["kind"], meta["level"])
            print("=" * 68)
            print(f"#{meta['number']} | {meta['kind']} | {meta['level']} | "
                  f"зерно {seeds[number]}")
            print(scenario["text"])
            print("--- эталон ---")
            print(json.dumps(scenario["ground_truth"], ensure_ascii=False, indent=1))
    if not args.write_seeds and not args.show:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
