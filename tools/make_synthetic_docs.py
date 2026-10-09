#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор 1000 синтетических документов и ground truth (ЧАСТЬ 2C стресс-теста).

    python tools/make_synthetic_docs.py                  # все 1000
    python tools/make_synthetic_docs.py --limit 20       # первые 20 (отладка)
    python tools/make_synthetic_docs.py --clean          # пересобрать с нуля

Результат:

    tests/_tmp/synthetic_docs/gt/<номер>.json      — ground truth сценария
    tests/_tmp/synthetic_docs/images/<номер>.png   — картинка документа
    tests/_tmp/synthetic_docs/index.json           — матрица покрытия

ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ. ФИО, серии, номера, адреса, реквизиты и VIN
собраны из словарей этого файла. Ничего из папки оператора сюда не попадает:
структура бланков описана в tools/make_doc_templates.py, а значения —
вымышленные. Правило — AGENTS.md § 4.

МАТРИЦА КАЧЕСТВА
----------------
10 % — идеально; 20 % — поворот 1–3°; 30 % — поворот 5–8°, размытие,
яркость ±10 %; 25 % — поворот 10–15°, размытие, шум, яркость ±30 %;
15 % — всё вместе плюс обрезка, блики и перспектива. Плюс «недописанные
буквы»: обрезанная буква, подмена похожих символов (О→0, З→3, В→8),
двойная экспозиция, стёртые буквы.
"""

import argparse
import json
import math
import random
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps  # noqa: E402

from tools.make_doc_templates import (  # noqa: E402
    all_templates, load_font, render_template,
)

OUT_DIR = PROJECT_ROOT / "tests" / "_tmp" / "synthetic_docs"

#: Зерно генератора: сценарии воспроизводимы, иначе регрессия по метрикам
#: «поехала» бы от прогона к прогону.
SEED = 20261010

#: Длина длинной стороны картинки. Выше 2200 px смысла нет: Tesseract
#: перестаёт выигрывать в точности, а PNG растёт линейно по площади.
MAX_SIDE = 2200

#: Сколько сценариев каждого вида документов (требование ЧАСТИ 2C).
KIND_PLAN = {
    "passport": 300,
    "license": 200,
    "bank": 200,
    "inn": 100,
    "sts": 100,
    "application": 100,
}

#: Уровни качества и их доли. Значения — доли от 1000.
QUALITY_PLAN = {
    "ideal": 100,      # 10 % — чистый рендер
    "good": 200,       # 20 % — поворот 1–3°
    "medium": 300,     # 30 % — поворот 5–8°, размытие, яркость ±10 %
    "poor": 250,       # 25 % — поворот 10–15°, размытие, шум, яркость ±30 %
    "very_poor": 150,  # 15 % — всё вместе + обрезка, блики, перспектива
}

#: Дополнительные дефекты «недописанных букв»: доля сценариев с каждым.
DEFECT_PLAN = {
    "cut_letter": 100,
    "lookalike_digits": 100,
    "double_exposure": 80,
    "erased_letters": 100,
}


# ─────────────────────────────────────────────────────────────
# Словари синтетических значений
# ─────────────────────────────────────────────────────────────

#: 100 фамилий: мужские, женские и дефисные.
SURNAMES = [
    "Иванов", "Иванова", "Смирнов", "Смирнова", "Кузнецов", "Кузнецова",
    "Попов", "Попова", "Васильев", "Васильева", "Петров", "Петрова",
    "Соколов", "Соколова", "Михайлов", "Михайлова", "Новиков", "Новикова",
    "Фёдоров", "Фёдорова", "Морозов", "Морозова", "Волков", "Волкова",
    "Алексеев", "Алексеева", "Лебедев", "Лебедева", "Семёнов", "Семёнова",
    "Егоров", "Егорова", "Павлов", "Павлова", "Козлов", "Козлова",
    "Степанов", "Степанова", "Николаев", "Николаева", "Орлов", "Орлова",
    "Андреев", "Андреева", "Макаров", "Макарова", "Никитин", "Никитина",
    "Захаров", "Захарова", "Зайцев", "Зайцева", "Соловьёв", "Соловьёва",
    "Борисов", "Борисова", "Яковлев", "Яковлева", "Григорьев", "Григорьева",
    "Романов", "Романова", "Воробьёв", "Воробьёва", "Сергеев", "Сергеева",
    "Фролов", "Фролова", "Александров", "Александрова", "Дмитриев",
    "Дмитриева", "Королёв", "Королёва", "Гусев", "Гусева", "Киселёв",
    "Киселёва", "Ильин", "Ильина", "Максимов", "Максимова", "Поляков",
    "Полякова", "Сорокин", "Сорокина", "Карпов", "Карпова", "Щербаков",
    "Щербакова", "Тимофеев", "Тимофеева", "Крылов", "Крылова", "Фомин",
    "Фомина", "Тарасов", "Тарасова", "Беляев", "Беляева",
    "Иванов-Петров", "Иванова-Петрова", "Кузнецов-Младший",
    "Кузнецова-Младшая", "Смирнов-Заречный", "Смирнова-Заречная",
]

#: 50 мужских имён и 50 женских.
MALE_NAMES = [
    "Иван", "Пётр", "Сергей", "Алексей", "Дмитрий", "Андрей", "Артём",
    "Виктор", "Егор", "Максим", "Роман", "Кирилл", "Никита", "Павел",
    "Олег", "Юрий", "Игорь", "Валерий", "Денис", "Тимур", "Ахмед", "Гасан",
    "Рустам", "Ильдар", "Артур", "Семён", "Антон", "Борис", "Вадим",
    "Владимир", "Вячеслав", "Геннадий", "Георгий", "Григорий", "Даниил",
    "Евгений", "Илья", "Константин", "Леонид", "Матвей", "Михаил", "Назар",
    "Николай", "Родион", "Станислав", "Степан", "Фёдор", "Эдуард", "Юлиан",
    "Ярослав",
]
FEMALE_NAMES = [
    "Мария", "Анна", "Ольга", "Елена", "Татьяна", "Ирина", "Светлана",
    "Наталья", "Дарья", "Юлия", "Алина", "Полина", "Нина", "Диана", "Алла",
    "Вера", "Галина", "Екатерина", "Жанна", "Зоя", "Инна", "Ксения",
    "Лариса", "Любовь", "Людмила", "Маргарита", "Надежда", "Оксана",
    "Раиса", "Регина", "Римма", "Роза", "Тамара", "Ульяна", "Фаина",
    "Эльвира", "Эмма", "Яна", "Александра", "Валентина", "Вероника",
    "Виктория", "Евгения", "Злата", "Карина", "Кристина", "Лидия",
    "Милана", "Нелли", "Софья",
]

#: 50 отчеств (мужские и женские формы вперемешку — род определяется именем).
PATRONYMICS_MALE = [
    "Иванович", "Петрович", "Сергеевич", "Алексеевич", "Дмитриевич",
    "Андреевич", "Артёмович", "Викторович", "Егорович", "Максимович",
    "Романович", "Кириллович", "Никитич", "Павлович", "Олегович",
    "Юрьевич", "Игоревич", "Валерьевич", "Денисович", "Тимурович",
    "Ахмедович", "Гасанович", "Рустамович", "Ильдарович", "Артурович",
]
PATRONYMICS_FEMALE = [
    "Ивановна", "Петровна", "Сергеевна", "Алексеевна", "Дмитриевна",
    "Андреевна", "Артёмовна", "Викторовна", "Егоровна", "Максимовна",
    "Романовна", "Кирилловна", "Никитична", "Павловна", "Олеговна",
    "Юрьевна", "Игоревна", "Валерьевна", "Денисовна", "Тимуровна",
    "Ахмедовна", "Гасановна", "Рустамовна", "Ильдаровна", "Артуровна",
]

#: 30 городов и 30 улиц.
CITIES = [
    "г. Москва", "г. Санкт-Петербург", "г. Екатеринбург", "г. Новосибирск",
    "г. Казань", "г. Мурманск", "г. Пятигорск", "г. Воронеж", "г. Краснодар",
    "г. Владивосток", "г. Самара", "г. Омск", "г. Челябинск", "г. Ростов-на-Дону",
    "г. Уфа", "г. Красноярск", "г. Пермь", "г. Волгоград", "г. Саратов",
    "г. Тюмень", "г. Тольятти", "г. Ижевск", "г. Барнаул", "г. Ульяновск",
    "г. Иркутск", "г. Хабаровск", "г. Ярославль", "г. Махачкала",
    "г. Томск", "г. Оренбург",
]
STREETS = [
    "ул. Ленина", "ул. Советская", "ул. Центральная", "ул. Молодёжная",
    "ул. Школьная", "ул. Садовая", "ул. Лесная", "ул. Набережная",
    "ул. Заречная", "ул. Полевая", "ул. Луговая", "ул. Новая",
    "ул. Зелёная", "ул. Северная", "ул. Южная", "ул. Восточная",
    "ул. Западная", "ул. Октябрьская", "ул. Первомайская", "ул. Кирова",
    "ул. Гагарина", "ул. Пушкина", "ул. Мира", "ул. Строителей",
    "ул. Космонавтов", "ул. Промышленная", "ул. Складская", "ул. Заводская",
    "ул. Тестовая", "ул. Дорожная",
]

#: 20 названий организаций.
ORGANIZATIONS = [
    "ООО «Аврора Логистик»", "ООО «Северный Ветер»", "ООО «Транс-Урал»",
    "ООО «Волга-Карго»", "ООО «Юг-Экспресс»", "ООО «Сибирь-Транс»",
    "ООО «Балт-Сервис»", "ООО «Урал-Карго»", "ООО «Дон-Логистик»",
    "ООО «Кама-Транс»", "ООО «Нева-Экспресс»", "ООО «Ока-Логистик»",
    "ООО «Вятка-Транс»", "ООО «Печора-Сервис»", "ООО «Амур-Карго»",
    "ООО «Лена-Транс»", "ООО «Обь-Логистик»", "ООО «Иртыш-Сервис»",
    "ООО «Кубань-Экспресс»", "ООО «Терек-Транс»",
]

#: 10 банков: (название, БИК, корр. счёт).
BANKS = [
    ("ПАО Сбербанк", "044525225", "30101810400000000225"),
    ("АО «Альфа-Банк»", "044525593", "30101810200000000593"),
    ("ВТБ (ПАО)", "044525187", "30101810700000000187"),
    ("ПАО «Промсвязьбанк»", "044525555", "30101810400000000555"),
    ("АО «Тинькофф Банк»", "044525974", "30101810145250000974"),
    ("ПАО «Росбанк»", "044525256", "30101810000000000256"),
    ("АО «Райффайзенбанк»", "044525700", "30101810200000000700"),
    ("ПАО «Совкомбанк»", "044525783", "30101810400000000783"),
    ("АО «Газпромбанк»", "044525823", "30101810200000000823"),
    ("ПАО «Открытие»", "044525999", "30101810300000000999"),
]

#: Марки ТС и цвет.
CAR_BRANDS = [
    "JETOUR T2", "HAVAL Jolion", "CHERY Tiggo 7 Pro", "GEELY Monjaro",
    "LADA Vesta", "MOSKVICH 3", "UAZ Patriot", "KAMAZ 5490", "VOLVO FH",
    "DAF XF", "MAN TGX", "SCANIA R440", "Foton Auman", "YANGMINDA", "KRONE SD",
]
COLORS = ["Белый", "Серый", "Чёрный", "Синий", "Красный", "Зелёный", "Коричневый"]

#: Подразделения, выдавшие паспорт.
ISSUERS = [
    "Отделом УФМС России по г. Москве",
    "ГУ МВД России по Свердловской области",
    "Отделом МВД России по Мурманской области",
    "ГУ МВД России по г. Санкт-Петербургу и Ленинградской области",
    "Отделом УФМС России по Республике Татарстан",
]

#: Категории водительского удостоверения.
CATEGORIES = ["B, C, E", "B, C, CE, D", "A, B, C, CE", "B, BE, C, CE", "C, CE"]

#: Подмены похожих символов: кириллица ↔ латиница и цифры.
#: Так выглядят и сканы (OCR путает О и 0), и «недописанные» документы.
LOOKALIKE_MAP = {
    "О": "0", "о": "0", "З": "3", "з": "3", "В": "8", "в": "8",
    "С": "C", "с": "c", "А": "A", "а": "a", "Е": "E", "е": "e",
    "Р": "P", "р": "p", "Н": "H", "К": "K", "М": "M", "Т": "T",
    "Х": "X", "У": "Y",
}


# ─────────────────────────────────────────────────────────────
# Данные одного сценария
# ─────────────────────────────────────────────────────────────

def _digits(rng: random.Random, length: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(length))


def _vin(rng: random.Random) -> str:
    alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    return "X" + "".join(rng.choice(alphabet) for _ in range(16))


def _plate(rng: random.Random) -> str:
    letters = "АВЕКМНОРСТУХ"
    return (f"{rng.choice(letters)}{rng.randint(100, 999)}"
            f"{rng.choice(letters)}{rng.choice(letters)}"
            f"{rng.choice(['77', '78', '50', '66', '23', '161', '196'])}")


def _phone(rng: random.Random) -> str:
    return (f"+7 ({rng.randint(900, 999)}) "
            f"{rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(10, 99)}")


def _person(rng: random.Random, gender: str) -> Tuple[str, str, str]:
    """(фамилия, имя, отчество) синтетического человека."""
    if gender == "female":
        surname = rng.choice([s for s in SURNAMES if s.endswith(("а", "я"))]
                             or SURNAMES)
        name = rng.choice(FEMALE_NAMES)
        patronymic = rng.choice(PATRONYMICS_FEMALE)
    else:
        surname = rng.choice([s for s in SURNAMES
                              if not s.endswith(("а", "я"))
                              and "-" not in s] or SURNAMES)
        name = rng.choice(MALE_NAMES)
        patronymic = rng.choice(PATRONYMICS_MALE)
    return surname, name, patronymic


def _person_any(rng: random.Random) -> Tuple[str, str, str, str]:
    """Человек любого пола: (фамилия, имя, отчество, пол)."""
    gender = "female" if rng.random() < 0.5 else "male"
    surname, name, patronymic = _person(rng, gender)
    return surname, name, patronymic, gender


def _date(rng: random.Random, start_year: int, end_year: int) -> str:
    return (f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}."
            f"{rng.randint(start_year, end_year)}")


def _address(rng: random.Random) -> str:
    return (f"{rng.choice(CITIES)}, {rng.choice(STREETS)}, "
            f"д. {rng.randint(1, 120)}, кв. {rng.randint(1, 300)}")


def build_values(rng: random.Random, kind: str) -> Dict[str, Any]:
    """
    Значения одного документа и его ground truth.

    Возвращает {"values": {ключ поля: значение}, "gt": {раздел: {...}}}.
    Ключи полей — те же, что в core.document_import_service.SCHEMA: ground
    truth и разбор обязаны говорить на одном языке, иначе метрику не свести.
    """
    surname, name, patronymic, gender = _person_any(rng)
    full_name = f"{surname} {name} {patronymic}"
    address = _address(rng)
    city = rng.choice(CITIES)
    street = rng.choice(STREETS)

    if kind == "passport":
        birth = _date(rng, 1960, 2002)
        issue = _date(rng, 2008, 2024)
        series = f"{rng.randint(10, 99)} {rng.randint(10, 99)}"
        number = _digits(rng, 6)
        code = f"{rng.randint(100, 999)}-{rng.randint(100, 999)}"
        issuer = rng.choice(ISSUERS)
        birth_place = f"{city}, {street}"
        values = {
            "passport_series": series, "passport_number": number,
            "surname": surname.upper(), "given_name": name,
            "patronymic": patronymic,
            "sex": "ЖЕН." if gender == "female" else "МУЖ.",
            "birth_date": birth, "birth_place": birth_place,
            "passport_issuer": issuer, "passport_issue_date": issue,
            "passport_code": code,
        }
        gt = {"driver": {
            "full_name": full_name, "birth_date": birth,
            "birth_place": birth_place,
            "passport_series": series, "passport_number": number,
            "passport_issue_date": issue, "passport_issuer": issuer,
            "passport_code": code,
        }}
        return {"values": values, "gt": gt}

    if kind == "license":
        birth = _date(rng, 1960, 2002)
        issue = _date(rng, 2012, 2023)
        year = int(issue[-4:])
        expiry = f"{issue[:6]}{year + 10}"
        series = f"{rng.randint(10, 99)} {rng.randint(10, 99)}"
        number = _digits(rng, 6)
        categories = rng.choice(CATEGORIES)
        values = {
            "surname": surname.upper(), "given_name": name,
            "patronymic": patronymic, "birth_date": birth,
            "license_issue_date": issue, "license_expiry_date": expiry,
            "license_series": series, "license_number": number,
            "license_categories": categories,
        }
        gt = {"driver": {
            "full_name": full_name, "birth_date": birth,
            "license_series": series, "license_number": number,
            "license_issue_date": issue, "license_expiry_date": expiry,
            "license_categories": categories,
        }}
        return {"values": values, "gt": gt}

    if kind == "bank":
        inn = _digits(rng, 12) if rng.random() < 0.5 else _digits(rng, 10)
        kpp = _digits(rng, 9) if len(inn) == 10 else ""
        bank_name, bik, corr = rng.choice(BANKS)
        account = "40702" + _digits(rng, 15)
        phone = _phone(rng)
        holder = (f"ИП {full_name}" if len(inn) == 12
                  else rng.choice(ORGANIZATIONS))
        values = {
            "full_name": holder, "inn": inn, "kpp": kpp,
            "bank_name": bank_name, "bik": bik,
            "correspondent_account": corr, "bank_account": account,
            "legal_address": address, "phone": phone,
        }
        gt = {"carrier": {
            "full_name": holder, "inn": inn,
            **({"kpp": kpp} if kpp else {}),
            "bank_name": bank_name, "bik": bik,
            "correspondent_account": corr, "bank_account": account,
        }}
        return {"values": values, "gt": gt}

    if kind == "inn":
        org = rng.choice(ORGANIZATIONS)
        inn = _digits(rng, 10)
        kpp = _digits(rng, 9)
        ogrn = _digits(rng, 13)
        director = f"{surname} {name} {patronymic}"
        position = rng.choice(["Генеральный директор", "Директор",
                               "Исполнительный директор"])
        values = {
            "full_name": org, "inn": inn, "kpp": kpp, "ogrn": ogrn,
            "legal_address": address, "director_name": director,
            "director_position": position,
        }
        gt = {"carrier": {
            "full_name": org, "inn": inn, "kpp": kpp, "ogrn": ogrn,
            "legal_address": address, "director_name": director,
            "director_position": position,
        }}
        return {"values": values, "gt": gt}

    if kind in ("sts", "pts"):
        plate = _plate(rng)
        vin = _vin(rng)
        brand = rng.choice(CAR_BRANDS)
        year = str(rng.randint(2012, 2025))
        color = rng.choice(COLORS)
        owner = (f"{surname} {name} {patronymic}" if rng.random() < 0.5
                 else rng.choice(ORGANIZATIONS))
        vehicle_type = ("тягач" if rng.random() < 0.4 else
                        ("полуприцеп" if rng.random() < 0.5 else ""))
        values = {
            "plate_number": plate, "vin": vin, "brand_model": brand,
            "year": year, "color": color, "owner_full_name": owner,
            "owner_address": address,
        }
        if kind == "pts":
            values["vehicle_type"] = vehicle_type
            values["notes"] = "без особых отметок"
        gt = {"vehicles": [{
            "plate_number": plate, "vin": vin, "brand_model": brand,
            "year": year, "color": color,
        }]}
        return {"values": values, "gt": gt}

    # «Заявка» — свободная запись водителя, тягача и полуприцепа: так
    # операторы и присылают данные, и так их разбирает `_driver_card`.
    birth = _date(rng, 1960, 2002)
    passport_series = f"{rng.randint(10, 99)} {rng.randint(10, 99)}"
    passport_number = _digits(rng, 6)
    license_series = f"{rng.randint(10, 99)} {rng.randint(10, 99)}"
    license_number = _digits(rng, 6)
    phone = _phone(rng)
    tractor_plate = _plate(rng)
    trailer_plate = (f"{rng.randint(10, 99)}{rng.choice('АВЕКМНОРСТУХ')}"
                     f"{rng.choice('АВЕКМНОРСТУХ')}{rng.randint(10, 99)}")
    tractor_brand = rng.choice(CAR_BRANDS)
    trailer_brand = rng.choice(CAR_BRANDS)
    values = {
        "full_name": full_name, "birth_date": birth,
        "birth_place": f"{city}, {street}",
        "passport_series": passport_series,
        "passport_number": passport_number,
        "passport_issue_date": _date(rng, 2008, 2024),
        "passport_issuer": rng.choice(ISSUERS),
        "passport_code": f"{rng.randint(100, 999)}-{rng.randint(100, 999)}",
        "registration_address": address,
        "license_series": license_series, "license_number": license_number,
        "license_issue_date": _date(rng, 2012, 2023),
        "license_expiry_date": _date(rng, 2026, 2033),
        "license_categories": rng.choice(CATEGORIES),
        "phone": phone,
        "tractor_brand": tractor_brand, "tractor_plate": tractor_plate,
        "tractor_color": rng.choice(COLORS),
        "trailer_brand": trailer_brand, "trailer_plate": trailer_plate,
    }
    gt = {
        "driver": {
            "full_name": full_name, "birth_date": birth,
            "passport_series": passport_series,
            "passport_number": passport_number,
            "license_series": license_series,
            "license_number": license_number,
        },
        "tractor": [{"brand_model": tractor_brand, "plate_number": tractor_plate}],
        "trailer": [{"brand_model": trailer_brand, "plate_number": trailer_plate}],
    }
    return {"values": values, "gt": gt}


# ─────────────────────────────────────────────────────────────
# Дефекты рендера
# ─────────────────────────────────────────────────────────────

def _rotate(image: Image.Image, rng: random.Random, limit: float) -> Image.Image:
    angle = rng.uniform(-limit, limit)
    return image.rotate(angle, resample=Image.BICUBIC, expand=True,
                        fillcolor=(250, 250, 248))


def _noise(image: Image.Image, rng: random.Random, amount: float) -> Image.Image:
    """
    Шум: пиксельные «соль и перец» и слабая зернистость.

    Реализовано без numpy: скорость здесь не критична (одна картинка на
    сценарий), а лишняя зависимость в проекте не нужна.
    """
    pixels = image.load()
    width, height = image.size
    count = int(width * height * amount * 0.002)
    for _ in range(count):
        x = rng.randrange(width)
        y = rng.randrange(height)
        level = 0 if rng.random() < 0.5 else 255
        pixels[x, y] = (level, level, level)
    return image


def _glare(image: Image.Image) -> Image.Image:
    """Блик: светлое пятно в углу, как от лампы при съёмке телефона."""
    overlay = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(overlay)
    width, height = image.size
    radius = int(min(width, height) * 0.35)
    draw.ellipse((width - radius, -radius // 2,
                  width + radius // 2, radius), fill=180)
    blurred = overlay.filter(ImageFilter.GaussianBlur(radius // 3))
    white = Image.new("RGB", image.size, (255, 255, 255))
    return Image.composite(white, image, blurred.point(lambda v: min(255, v)))


def _perspective(image: Image.Image, rng: random.Random) -> Image.Image:
    """Перспектива: небольшой сдвиг углов (съёмка под углом)."""
    width, height = image.size
    shift = 0.04
    dx = int(width * shift * rng.uniform(0.3, 1.0))
    dy = int(height * shift * rng.uniform(0.3, 1.0))
    corners = [
        (dx, dy), (width - dx // 2, 0),
        (width, height - dy), (0, height - dy // 2),
    ]
    # Обратное преобразование: PIL ищет исходную точку для каждого пикселя.
    source = [(0, 0), (width, 0), (width, height), (0, height)]
    coefficients = _find_coefficients(corners, source)
    return image.transform((width, height), Image.PERSPECTIVE, coefficients,
                           Image.BICUBIC)


def _find_coefficients(target, source):
    """
    Коэффициенты перспективного преобразования по четырём парам точек.

    Решение системы 8×8 методом Гаусса: numpy в проекте нет и добавлять его
    ради одной картинки не нужно.
    """
    matrix = []
    for (x, y), (u, v) in zip(target, source):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y, u])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y, v])

    size = 8
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(matrix[r][column]))
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        divisor = matrix[column][column] or 1e-9
        for j in range(column, size + 1):
            matrix[column][j] /= divisor
        for row in range(size):
            if row == column:
                continue
            factor = matrix[row][column]
            if factor:
                for j in range(column, size + 1):
                    matrix[row][j] -= factor * matrix[column][j]
    return [matrix[i][size] for i in range(size)]


def _cut_letter(image: Image.Image, rng: random.Random) -> Image.Image:
    """Обрезанная буква: часть строки затёрта полосой фона."""
    width, height = image.size
    draw = ImageDraw.Draw(image)
    y = rng.randint(int(height * 0.15), int(height * 0.85))
    h = rng.randint(4, max(5, height // 60))
    x = rng.randint(0, max(1, width - width // 6))
    draw.rectangle((x, y, x + width // 6, y + h),
                   fill=image.getpixel((min(width - 1, x + width // 6 + 4),
                                        min(height - 1, y + h + 4))))
    return image


def _erased_letters(image: Image.Image, rng: random.Random) -> Image.Image:
    """Стёртые буквы: несколько «ластиковых» пятен по строке."""
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for _ in range(rng.randint(2, 5)):
        x = rng.randint(0, max(1, width - width // 12))
        y = rng.randint(int(height * 0.1), max(int(height * 0.1) + 1,
                                               int(height * 0.9)))
        draw.rectangle((x, y, x + width // 12, y + max(4, height // 70)),
                       fill=(248, 248, 246))
    return image


def _double_exposure(image: Image.Image, rng: random.Random) -> Image.Image:
    """
    Двойная экспозиция: слабый сдвинутый отпечаток того же документа.

    Так выглядит скан, снятый дважды без протяжки: часть строк читается
    дважды со смещением.
    """
    width, height = image.size
    shift_x = rng.randint(-6, 6)
    shift_y = rng.randint(3, 10)
    ghost = ImageChops_offset(image, shift_x, shift_y)
    ghost = ghost.point(lambda v: int(v * 0.35 + 255 * 0.65))
    return Image.blend(image, ghost, 0.45)


def ImageChops_offset(image: Image.Image, dx: int, dy: int) -> Image.Image:
    """Сдвиг картинки без numpy: через аффинное преобразование PIL."""
    return image.transform(image.size, Image.AFFINE, (1, 0, -dx, 0, 1, -dy),
                           Image.BICUBIC)


def _lookalike(values: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    """
    Подмена похожих символов: О→0, З→3, В→8 и латинские двойники.

    Меняется РОВНО ОДИН символ в одном значении: цель — проверить, что
    разбор не «чинит» цифры самовольно, а не испортить документ целиком.
    Ground truth остаётся исходным: подмена — это дефект документа, и
    потеря поля на нём — ожидаемый результат, а не ошибка разбора.
    """
    text_keys = [k for k, v in values.items()
                 if isinstance(v, str) and v and "date" not in k]
    if not text_keys:
        return values
    key = rng.choice(text_keys)
    text = values[key]
    positions = [i for i, ch in enumerate(text) if ch in LOOKALIKE_MAP]
    if not positions:
        return values
    index = rng.choice(positions)
    updated = dict(values)
    updated[key] = text[:index] + LOOKALIKE_MAP[text[index]] + text[index + 1:]
    return updated


def apply_quality(image: Image.Image, quality: str,
                  rng: random.Random) -> Image.Image:
    """Приводит картинку к заданному уровню качества."""
    if quality == "ideal":
        return image

    if quality == "good":
        image = _rotate(image, rng, 3.0)
        return ImageEnhance.Brightness(image).enhance(rng.uniform(0.97, 1.03))

    if quality == "medium":
        image = _rotate(image, rng, 8.0)
        image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 0.9)))
        return ImageEnhance.Brightness(image).enhance(rng.uniform(0.90, 1.10))

    if quality == "poor":
        image = _rotate(image, rng, 15.0)
        image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.8, 1.6)))
        image = _noise(image, rng, 0.6)
        return ImageEnhance.Brightness(image).enhance(rng.uniform(0.70, 1.30))

    # very_poor: всё вместе плюс перспектива, блик и обрезка.
    image = _rotate(image, rng, 16.0)
    image = _perspective(image, rng)
    image = image.filter(ImageFilter.GaussianBlur(rng.uniform(1.2, 2.2)))
    image = _noise(image, rng, 1.0)
    image = ImageEnhance.Brightness(image).enhance(rng.uniform(0.60, 1.40))
    image = ImageEnhance.Contrast(image).enhance(rng.uniform(0.70, 1.30))
    if rng.random() < 0.6:
        image = _glare(image)
    if rng.random() < 0.5:
        width, height = image.size
        left = rng.randint(0, int(width * 0.06))
        top = rng.randint(0, int(height * 0.06))
        image = image.crop((left, top,
                            width - rng.randint(0, int(width * 0.06)),
                            height - rng.randint(0, int(height * 0.06))))
    return image


def apply_defect(image: Image.Image, defect: str,
                 rng: random.Random) -> Image.Image:
    """Дополнительный дефект «недописанных букв»."""
    if defect == "cut_letter":
        return _cut_letter(image, rng)
    if defect == "erased_letters":
        return _erased_letters(image, rng)
    if defect == "double_exposure":
        return _double_exposure(image, rng)
    return image


# ─────────────────────────────────────────────────────────────
# Сборка сценариев
# ─────────────────────────────────────────────────────────────

def _plan(count: int) -> List[Tuple[str, str, str]]:
    """
    План прогона: список (номер, вид документа, качество).

    Виды и качества раздаются по требуемым долям и перемешиваются зерном
    генератора: иначе первые 300 сценариев были бы только паспортами, и
    «нарезка по уровням качества» в метриках стала бы бессмысленной.
    """
    kinds: List[str] = []
    for kind, amount in KIND_PLAN.items():
        kinds.extend([kind] * amount)
    qualities: List[str] = []
    for quality, amount in QUALITY_PLAN.items():
        qualities.extend([quality] * amount)

    rng = random.Random(SEED)
    rng.shuffle(kinds)
    rng.shuffle(qualities)

    defects: List[Optional[str]] = []
    for defect, amount in DEFECT_PLAN.items():
        defects.extend([defect] * amount)
    defects.extend([None] * (count - len(defects)))
    rng.shuffle(defects)

    return [(str(i + 1), kinds[i], qualities[i], defects[i])
            for i in range(count)]


def _scale(image: Image.Image) -> Image.Image:
    """Приводит длинную сторону к MAX_SIDE (пропорции сохраняются)."""
    width, height = image.size
    longest = max(width, height)
    if longest <= MAX_SIDE:
        return image
    factor = MAX_SIDE / longest
    return image.resize((max(1, int(width * factor)),
                         max(1, int(height * factor))), Image.LANCZOS)


def build_document(template_name: str, values: Dict[str, Any],
                   quality: str, defect: Optional[str],
                   rng: Optional[random.Random] = None) -> Image.Image:
    """
    Рисует документ и портит его по плану сценария.

    :param rng: генератор случайных чисел для дефектов рендера. Если не
        передан, он собирается из НОМЕРА сценария в значениях (`number`):
        тогда картинка зависит только от сценария и не «плывёт» от порядка
        вызовов. Это важно для регрессии: повторный прогон обязан получить
        ровно те же картинки, что и первый, иначе разница метрик будет
        разницей рендера, а не кода.
    """
    if rng is None:
        rng = random.Random(f"render-{values.get('number', 0)}")

    template = all_templates()[template_name]
    image = render_template(template, values)
    image = _scale(image)
    image = apply_quality(image, quality, rng)
    if defect:
        image = apply_defect(image, defect, rng)
    return ImageOps.exif_transpose(image).convert("RGB")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(
        description="1000 синтетических документов и ground truth"
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    gt_dir = out_dir / "gt"
    image_dir = out_dir / "images"
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    gt_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    plan = _plan(args.limit)
    rng = random.Random(SEED)

    summary = []
    seeds = []
    for number, kind, quality, defect in plan:
        seed = rng.randint(1, 2 ** 31 - 1)
        seeds.append(seed)
        scenario_rng = random.Random(seed)

        built = build_values(scenario_rng, kind)
        values = built["values"]
        gt = built["gt"]

        # Подмена похожих символов — дефект документа, а не данных:
        # ground truth остаётся исходным.
        render_values = dict(values)
        if defect == "lookalike_digits":
            render_values = _lookalike(render_values, scenario_rng)
        render_values["number"] = int(number)

        image = build_document(kind, render_values, quality, defect)
        image.save(image_dir / f"{number}.png", optimize=True)

        record = {
            "number": int(number),
            "kind": kind,
            "quality": quality,
            "defect": defect or "",
            "seed": seed,
            "image": f"images/{number}.png",
            "values": values,
            # Ground truth — в ТОЙ ЖЕ форме, что отдаёт extract_local_fields:
            # сравнение идёт по одному правилу ключей (см.
            # tools/synthetic_stress.py::score).
            "gt": gt,
        }
        (gt_dir / f"{number}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        summary.append({
            "number": int(number), "kind": kind, "quality": quality,
            "defect": defect or "", "seed": seed,
        })

    index = {
        "seed": SEED,
        "count": len(summary),
        "kinds": KIND_PLAN,
        "qualities": QUALITY_PLAN,
        "defects": DEFECT_PLAN,
        "seeds": seeds,
        "scenarios": summary,
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    from collections import Counter

    print(f"Сценариев: {len(summary)}")
    print("Виды:", dict(Counter(s["kind"] for s in summary)))
    print("Качество:", dict(Counter(s["quality"] for s in summary)))
    print("Дефекты:", dict(Counter(s["defect"] for s in summary if s["defect"])))
    print(f"Папка: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
