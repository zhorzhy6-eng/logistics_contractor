#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор стресс-сценариев для договора перевозки (БЛОК 1 стресс-теста).

Создаёт 100 синтетических сценариев — полных наборов полей ContractData
(driver, carrier, customer, vehicles, tractor, trailer, contract, loadings,
unloadings, city) — и раскладывает их в:

    tests/_tmp/stress_scenarios/<номер>.json     — сам сценарий
    tests/_tmp/stress_scenarios/index.json       — описания и покрытие

ЗАЧЕМ ТАКОЙ РАЗБРОС
-------------------
Каждый сценарий — не «ещё один договор», а проверка одного класса дефектов
шаблона и генератора: падежи ФИО, суммы прописью, пустые необязательные
поля, спецсимволы, длинные наименования, разное число машин и точек
маршрута. Матрица покрытия описана в COVERAGE_MATRIX и сверяется тестом
`tests/test_stress_regression.py` — иначе «100 сценариев» превратились бы в
сто почти одинаковых документов.

РАСПРЕДЕЛЕНИЕ ЗАДАНО ЯВНО, А НЕ СЛУЧАЕМ
---------------------------------------
Число машин, точек и род водителя раздаются по заранее собранным спискам
(20/40/40 машин, 50/25/25 точек, 50/50 род) и перемешиваются фиксированным
зерном. Случайное распределение дало бы нужные числа «примерно» и
разъехалось бы при малейшей правке генератора.

ДАННЫЕ ТОЛЬКО СИНТЕТИЧЕСКИЕ. Ничего из папки реальных документов сюда не
попадает: ФИО, реквизиты, адреса и VIN вымышлены. Файлы сценариев лежат в
tests/_tmp/ (в .gitignore), но правило AGENTS.md § 4 относится и к ним.

Запуск:
    python tools/make_test_scenarios.py            # все 100
    python tools/make_test_scenarios.py --limit 10 # первые 10 (отладка)
"""

import argparse
import json
import random
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: Куда складываются сценарии. Папка в .gitignore целиком.
OUT_DIR = PROJECT_ROOT / "tests" / "_tmp" / "stress_scenarios"

#: Фиксированное зерно: сценарии воспроизводимы от прогона к прогону, иначе
#: регрессия по baseline.json «поехала» бы на каждом запуске.
SEED = 20261010

BASE_DATE = date(2026, 9, 23)

#: Итоговое число сценариев (требование БЛОКА 1).
SCENARIO_COUNT = 100


# ─────────────────────────────────────────────────────────────
# Словари тестовых данных (все значения вымышлены)
# ─────────────────────────────────────────────────────────────

#: Мужские ФИО водителей: (фамилия, имя, отчество).
MALE_DRIVERS = [
    ("Иванов", "Иван", "Иванович"),
    ("Кузнецов", "Пётр", "Сергеевич"),
    ("Смирнов", "Алексей", "Николаевич"),
    ("Волков", "Дмитрий", "Андреевич"),
    ("Соколов", "Артём", "Викторович"),
    ("Морозов", "Егор", "Павлович"),
    ("Новиков", "Максим", "Олегович"),
    ("Фёдоров", "Роман", "Юрьевич"),
    ("Егоров", "Кирилл", "Игоревич"),
    ("Павлов", "Никита", "Валерьевич"),
]

#: Женские ФИО водителей: (фамилия, имя, отчество).
FEMALE_DRIVERS = [
    ("Иванова", "Мария", "Ивановна"),
    ("Кузнецова", "Анна", "Сергеевна"),
    ("Смирнова", "Ольга", "Николаевна"),
    ("Волкова", "Елена", "Андреевна"),
    ("Соколова", "Татьяна", "Викторовна"),
    ("Морозова", "Ирина", "Павловна"),
    ("Новикова", "Светлана", "Олеговна"),
    ("Фёдорова", "Наталья", "Юрьевна"),
    ("Егорова", "Дарья", "Игоревна"),
    ("Павлова", "Юлия", "Валерьевна"),
]

#: Дефисные фамилии: (муж., жен., имя муж., имя жен., отчество муж., жен.).
HYPHEN_DRIVERS = [
    ("Иванов-Петров", "Иванова-Петрова", "Сергей", "Сергеевна",
     "Олегович", "Олеговна"),
    ("Кузнецов-Младший", "Кузнецова-Младшая", "Артём", "Алина",
     "Львович", "Львовна"),
    ("Смирнов-Заречный", "Смирнова-Заречная", "Пётр", "Полина",
     "Ильич", "Ильинична"),
    ("Волков-Донской", "Волкова-Донская", "Никита", "Нина",
     "Матвеевич", "Матвеевна"),
    ("Морозов-Северный", "Морозова-Северная", "Денис", "Диана",
     "Тимурович", "Тимуровна"),
]

#: Нерусские ФИО (вымышленные, распространённые в перевозках формы).
FOREIGN_DRIVERS = [
    ("Ахмедов", "Ахмед", "Ахмедович"),
    ("Гасанов", "Гасан", "Гасанович"),
    ("Мирзоев", "Мирзо", "Мирзоевич"),
    ("Ким", "Сергей", "Владимирович"),
    ("Пак", "Дмитрий", "Олегович"),
    ("Юсупов", "Рустам", "Рустамович"),
    ("Каримов", "Карим", "Каримович"),
    ("Шакиров", "Ильдар", "Ильдарович"),
    ("Абдуллаев", "Абдулла", "Абдуллаевич"),
    ("Тен", "Артур", "Артурович"),
]

#: Длинные (4 и более слова) ФИО: составные фамилии, двойные имена.
LONG_DRIVERS = [
    "Иванов-Петров-Смирнов Сергей Олегович",
    "Абдуллаев-Мирзоев Абдулла Рустамович",
    "Ким-Пак Сергей Владимирович",
    "Павловский-Заречный-Северный Дмитрий Игоревич",
    "Соколов-Морозов-Волков Пётр Андреевич",
]

#: Организации-стороны: (полное, краткое, город).
ORGANIZATIONS = [
    ("Общество с ограниченной ответственностью «Аврора Логистик»",
     "ООО «Аврора Логистик»", "г. Москва"),
    ("Общество с ограниченной ответственностью «Северный Ветер»",
     "ООО «Северный Ветер»", "г. Санкт-Петербург"),
    ("Общество с ограниченной ответственностью «Транс-Урал»",
     "ООО «Транс-Урал»", "г. Екатеринбург"),
    ("Общество с ограниченной ответственностью «Волга-Карго»",
     "ООО «Волга-Карго»", "г. Нижний Новгород"),
    ("Общество с ограниченной ответственностью «Юг-Экспресс»",
     "ООО «Юг-Экспресс»", "г. Краснодар"),
    ("Общество с ограниченной ответственностью «Сибирь-Транс»",
     "ООО «Сибирь-Транс»", "г. Новосибирск"),
]

#: Длинное (80+ символов) наименование стороны.
LONG_ORGANIZATION = (
    "Общество с ограниченной ответственностью «Северо-Западная "
    "Транспортно-Логистическая Компания Международных Перевозок»"
)
LONG_ORGANIZATION_SHORT = "ООО «СЗ ТЛК Международных Перевозок»"

#: Длинное наименование ЗАКАЗЧИКА — другой текст, чтобы в одном документе
#: не оказалось двух одинаковых сторон.
LONG_CUSTOMER_ORGANIZATION = (
    "Общество с ограниченной ответственностью «Уральский Завод "
    "Крупногабаритного Машиностроительного Оборудования»"
)
LONG_CUSTOMER_ORGANIZATION_SHORT = "ООО «УЗ Крупногабаритного Оборудования»"

#: Длинное ФИО индивидуального предпринимателя (4 слова, 49 символов) —
#: составная фамилия плюс двойное имя.
LONG_IP_PERSON = "Александров-Кузнецов-Смирнов Владимир Ростиславович"

#: ФИО руководителей ООО: (мужская форма, женская форма).
DIRECTORS = [
    ("Петров Пётр Петрович", "Петрова Полина Петровна"),
    ("Сидоров Семён Семёнович", "Сидорова Светлана Семёновна"),
    ("Алексеев Андрей Алексеевич", "Алексеева Алла Алексеевна"),
    ("Дмитриев Денис Дмитриевич", "Дмитриева Дина Дмитриевна"),
    ("Николаев Никита Николаевич", "Николаева Нина Николаевна"),
]

#: Должности руководителя (проверяется склонение в п. 1.1/1.2).
POSITIONS = [
    "Генеральный директор",
    "Директор",
    "Исполнительный директор",
    "Управляющий",
    "Президент",
]

#: Банки: (название, БИК, корр. счёт).
BANKS = [
    ("ПАО Сбербанк", "044525225", "30101810400000000225"),
    ("АО «Альфа-Банк»", "044525593", "30101810200000000593"),
    ("ВТБ (ПАО)", "044525187", "30101810700000000187"),
    ("ПАО «Промсвязьбанк»", "044525555", "30101810400000000555"),
    ("АО «Тинькофф Банк»", "044525974", "30101810145250000974"),
]

#: Города: (город для договора, регион, индекс).
#: В названии региона НЕТ префикса «г. »: город печатается отдельно, и
#: «г. Москва, г. Москва» — это уже дефект «двойной префикс города»
#: (БЛОК 1, пункт «м»). Данные сценариев обязаны быть чистыми, иначе
#: проверка ловила бы саму себя.
CITIES = [
    ("Москва", "Москва", "101000"),
    ("Санкт-Петербург", "Санкт-Петербург", "190000"),
    ("Екатеринбург", "Свердловская обл.", "620000"),
    ("Новосибирск", "Новосибирская обл.", "630000"),
    ("Казань", "Республика Татарстан", "420000"),
    ("Мурманск", "Мурманская обл.", "183000"),
    ("Пятигорск", "Ставропольский край", "357500"),
    ("Воронеж", "Воронежская обл.", "394000"),
    ("Краснодар", "Краснодарский край", "350000"),
    ("Владивосток", "Приморский край", "690000"),
]

#: Марки ТС для таблицы груза (составные названия — проверка «не разбивать»).
CAR_BRANDS = [
    "JETOUR T2", "HAVAL Jolion", "CHERY Tiggo 7 Pro", "GEELY Monjaro",
    "LADA Vesta SW Cross", "MOSKVICH 3", "UAZ Patriot", "GAZelle NEXT",
    "KAMAZ 54901", "MAZ 5440M9", "SCANIA R440", "VOLVO XC90",
]

#: Марки тягачей и полуприцепов.
TRACTOR_BRANDS = ["Foton Auman", "VOLVO FH", "DAF XF", "MAN TGX", "KAMAZ 5490"]
TRAILER_BRANDS = ["YANGMINDA", "KRONE SD", "SCHMITZ CARGOBULL", "TONAR", "WIELTON"]

#: Категории ВУ: как приходят в данных.
LICENSE_CATEGORIES = ["B, C, E", "B, C, CE, D", "A, B, C, CE", "B, BE, C, CE", "C, CE"]

#: Суммы БЕЗ копеек: база для обычных сценариев. Копейки добавляются
#: отдельным списком ниже — иначе «сумма с копейками» оказалась бы у
#: половины сценариев, и проверка копеек прописью потеряла бы смысл.
AMOUNTS = [0.0, 1.0, 11.0, 21.0, 100.0, 1000.0, 1_000_000.0,
           180_300.0, 46_885.0, 219_966.0]

#: Крайние суммы отдельным списком — для сценариев «только крайние суммы».
#: Все значения БЕЗ копеек: сценарии с копейками считаются отдельным
#: признаком (amount_with_kopecks), и пересечение двух списков сделало бы
#: покрытие непроверяемым.
EXTREME_AMOUNTS = [0.0, 1.0, 2.0, 10.0, 11.0, 21.0, 100.0, 1000.0,
                   999_999.0, 1_000_000.0, 10_000_000.0, 999_998.0]

#: Суммы с ненулевыми копейками: проверка копеек прописью («25 копеек»).
KOPECK_AMOUNTS = [1.01, 11.11, 21.21, 100.99, 1000.05, 180_300.55,
                  219_966.55, 46_885.25, 123_456.78, 999_999.99]

#: Все форматы дат из core/dates.py.
DATE_FORMATS = [
    "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y.%m.%d", "%d-%m-%Y", "%Y%m%d", "%d %m %Y",
]


# ─────────────────────────────────────────────────────────────
# Матрица покрытия
# ─────────────────────────────────────────────────────────────

#: Что именно проверяет матрица и в каком объёме. Числа — требование задания;
#: тест `test_scenario_matrix_coverage` сверяет их с реальным index.json,
#: поэтому «примерно» здесь не годится: распределение раздаётся явно.
COVERAGE_MATRIX = {
    "party_kind": {"ООО": 33, "ИП с НДС": 33, "ИП без НДС": 34},
    "driver_gender": {"male": 50, "female": 50},
    "vehicles_count": {"1": 20, "6": 40, "12": 40},
    "loadings_count": {"1": 50, "5": 25, "10": 25},
    "unloadings_count": {"1": 50, "5": 25, "10": 25},
    "hyphen_surname": 10,
    "foreign_name": 10,
    "long_name": 10,
    "empty_fields": 20,
    "special_chars": 10,
    "long_company_names": 10,
    "short_name": 10,
    "amount_extreme": 12,
    "amount_with_kopecks": 10,
}


# ─────────────────────────────────────────────────────────────
# Вспомогательные построители
# ─────────────────────────────────────────────────────────────

def _round_robin(values: List[Any], count: int) -> List[Any]:
    """Повторяет список по кругу до нужной длины, сохраняя порядок."""
    return [values[i % len(values)] for i in range(count)]


def _counts(values: List[Any]) -> Dict[str, int]:
    """Сколько раз встречается каждое значение (для отчёта о покрытии)."""
    result: Dict[str, int] = {}
    for value in values:
        result[str(value)] = result.get(str(value), 0) + 1
    return result


def _fix_distribution(rng: random.Random, values: List[Any],
                      target: Dict[str, int], name: str,
                      leftover: Any = None) -> List[Any]:
    """
    Доводит распределение до целевого, переставляя значения местами.

    Списки размерностей собираются по требуемым числам и перемешиваются, но
    у признаков, которые ещё и «набираются» расписанием по индексу (вид
    стороны, вид ФИО), фактический расклад может разойтись с матрицей на
    единицы. Здесь расхождение чинится перестановкой ЛИШНИХ значений в
    недостающие: общее число сценариев не меняется, ни одно значение не
    теряется, а покрытие становится ровно таким, как в матрице.

    :param leftover: значение-остаток («plain» у вида ФИО, False у флагов).
        Всё, что не перечислено в target, считается остатком и добирает
        разницу до len(values). Если None, набор значений обязан совпадать
        с набором целей — иначе это ошибка матрицы, а не «недостача».
    :raises AssertionError: если задачу невозможно решить перестановкой
        (значения вне матрицы, или целей больше, чем сценариев).
    """
    actual = _counts(values)
    # Ключи приводятся к строкам: _counts считает по str(значение), и
    # «12»/True в целях должны сравниваться с тем же ключом, что и в факте.
    # Само значение при перестановке берётся из values — тип сохраняется.
    target = {str(k): v for k, v in target.items()}
    if leftover is not None and str(leftover) not in target:
        named = sum(target.values())
        if named > len(values):
            raise AssertionError(
                f"{name}: целей {named} больше, чем сценариев {len(values)}"
            )
        target[str(leftover)] = len(values) - named
        actual.setdefault(str(leftover), 0)

    extra = set(actual) - set(target)
    if extra:
        raise AssertionError(f"{name}: значения вне матрицы: {sorted(extra)}")

    for want, need in target.items():
        have = actual.get(want, 0)
        if have <= need:
            continue
        # Лишние — в те значения, которых не хватает.
        for _ in range(have - need):
            donors = [k for k in target
                      if k != want and actual.get(k, 0) < target[k]]
            if not donors:
                raise AssertionError(
                    f"{name}: не хватает мест для «{want}» "
                    f"(факт {actual}, цель {target})"
                )
            donor = donors[0]
            # Замена по позиции: первое значение с ключом want меняется на
            # любое значение с ключом donor, уже присутствующее в списке.
            # Тип значения сохраняется — берём его из самих данных.
            position = next(i for i, v in enumerate(values) if str(v) == want)
            values[position] = next(v for v in values if str(v) == donor)
            actual[want] -= 1
            actual[donor] = actual.get(donor, 0) + 1

    rng.shuffle(values)
    return values


def _vin(rng: random.Random, index: int) -> str:
    """
    Синтетический VIN: 17 знаков, без I, O и Q (требование стандарта).

    Проверяется в БЛОКЕ 1, пункт «к»: буквы I, O, Q в VIN недопустимы —
    их легко спутать с 1 и 0 при чтении скана.
    """
    alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    body = "".join(rng.choice(alphabet) for _ in range(16))
    return f"X{index % 10}{body}"[:17]


def _plate(rng: random.Random) -> str:
    """Российский госномер: А123ВС77 (буквы только из разрешённого набора)."""
    letters = "АВЕКМНОРСТУХ"
    return (f"{rng.choice(letters)}{rng.randint(100, 999)}"
            f"{rng.choice(letters)}{rng.choice(letters)}"
            f"{rng.choice(['77', '78', '50', '66', '23', '161', '196'])}")


def _digits(rng: random.Random, length: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(length))


def _bank_account(rng: random.Random) -> str:
    """Расчётный счёт: 20 цифр, начинается с 407 (как у юрлиц)."""
    return "40702" + _digits(rng, 15)


def _phone(rng: random.Random) -> str:
    return (f"+7 ({rng.randint(900, 999)}) "
            f"{rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(10, 99)}")


def _driver_person(rng: random.Random, kind: str, gender: str) -> Dict[str, Any]:
    """
    Водитель со всеми полями паспорта и ВУ.

    :param kind: "plain" | "hyphen" | "foreign" | "long" | "short"
    :param gender: "male" | "female"
    """
    if kind == "hyphen":
        male, female, name_m, name_f, pat_m, pat_f = rng.choice(HYPHEN_DRIVERS)
        full_name = (f"{male if gender == 'male' else female} "
                     f"{name_m if gender == 'male' else name_f} "
                     f"{pat_m if gender == 'male' else pat_f}")
    elif kind == "foreign":
        surname, name, patronymic = rng.choice(FOREIGN_DRIVERS)
        full_name = f"{surname} {name} {patronymic}"
    elif kind == "long":
        full_name = rng.choice(LONG_DRIVERS)
    elif kind == "short":
        # Короткое ФИО: только фамилия и имя (бывает у иностранных водителей).
        surname, name, _ = rng.choice(
            MALE_DRIVERS if gender == "male" else FEMALE_DRIVERS
        )
        full_name = f"{surname} {name}"
    else:
        surname, name, patronymic = rng.choice(
            MALE_DRIVERS if gender == "male" else FEMALE_DRIVERS
        )
        full_name = f"{surname} {name} {patronymic}"

    birth = date(rng.randint(1965, 2000), rng.randint(1, 12), rng.randint(1, 28))
    passport_issue = date(rng.randint(2010, 2024), rng.randint(1, 12), rng.randint(1, 28))
    license_issue = date(rng.randint(2012, 2023), rng.randint(1, 12), rng.randint(1, 28))
    license_expiry = date(license_issue.year + 10, license_issue.month, license_issue.day)

    return {
        "full_name": full_name,
        "birth_date": birth.isoformat(),
        "birth_place": rng.choice([
            "г. Москва", "Республика Татарстан, г. Казань",
            "Свердловская обл., г. Екатеринбург", "Мурманская обл., г. Мурманск",
        ]),
        "passport_series": f"{rng.randint(10, 99)} {rng.randint(10, 99)}",
        "passport_number": _digits(rng, 6),
        "passport_issue_date": passport_issue.isoformat(),
        "passport_issuer": rng.choice([
            "Отделом УФМС России по г. Москве",
            "ГУ МВД России по Свердловской области",
            "Отделом МВД России по Мурманской области",
        ]),
        "passport_code": f"{rng.randint(100, 999)}-{rng.randint(100, 999)}",
        "registration_address": rng.choice([
            "г. Москва, ул. Тестовая, д. 1, кв. 2",
            "Республика Татарстан, г. Казань, ул. Баумана, д. 12",
            "Свердловская обл., г. Екатеринбург, ул. Ленина, д. 5",
        ]),
        "license_series": f"{rng.randint(10, 99)} {rng.randint(10, 99)}",
        "license_number": _digits(rng, 6),
        "license_issue_date": license_issue.isoformat(),
        "license_expiry_date": license_expiry.isoformat(),
        "license_categories": rng.choice(LICENSE_CATEGORIES),
        "phone": _phone(rng),
    }


def _person_fio(rng: random.Random, gender: str) -> str:
    """Случайное ФИО взрослого человека (для ИП)."""
    if rng.random() < 0.5:
        male, female, name_m, name_f, pat_m, pat_f = rng.choice(HYPHEN_DRIVERS)
        return (f"{male if gender == 'male' else female} "
                f"{name_m if gender == 'male' else name_f} "
                f"{pat_m if gender == 'male' else pat_f}")
    surname, name, patronymic = rng.choice(
        MALE_DRIVERS if gender == "male" else FEMALE_DRIVERS
    )
    return f"{surname} {name} {patronymic}"


def _organization(rng: random.Random, index: int, party_kind: str,
                  gender: str) -> Dict[str, Any]:
    """
    Сторона договора: ООО, ИП с НДС или ИП без НДС.

    У ИП наименование — это ФИО с приставкой; пол подписанта берётся из
    ФИО, а не из рода названия организации (см. `detect_gender`).
    """
    full, short, city = ORGANIZATIONS[index % len(ORGANIZATIONS)]
    bank_name, bik, corr = rng.choice(BANKS)
    director = rng.choice(DIRECTORS)[0 if gender == "male" else 1]

    if party_kind == "ООО":
        house = rng.randint(1, 90)
        return {
            "full_name": full,
            "short_name": short,
            "entity_type": "ООО",
            "inn": _digits(rng, 10),
            "kpp": f"{rng.randint(100000000, 999999999)}",
            "ogrn": _digits(rng, 13),
            "legal_address": f"{city}, ул. Промышленная, д. {house}",
            "actual_address": f"{city}, ул. Промышленная, д. {house}",
            "bank_account": _bank_account(rng),
            "bik": bik,
            "correspondent_account": corr,
            "bank_name": bank_name,
            "director_name": director,
            "director_position": rng.choice(POSITIONS),
            "phone": _phone(rng),
            "email": f"office{index}@example.ru",
        }

    # ИП: наименование = приставка + ФИО. ОГРНИП — 15 цифр, КПП нет.
    person = _person_fio(rng, gender)
    prefix = "Индивидуальный предприниматель"
    return {
        "full_name": f"{prefix} {person}",
        "short_name": f"ИП {person}",
        "entity_type": "ИП",
        "inn": _digits(rng, 12),
        "kpp": "",
        "ogrn": _digits(rng, 15),
        "legal_address": f"{city}, ул. Заводская, д. {rng.randint(1, 90)}",
        "actual_address": "",
        "bank_account": _bank_account(rng),
        "bik": bik,
        "correspondent_account": corr,
        "bank_name": bank_name,
        "director_name": "",
        "director_position": "",
        "phone": _phone(rng),
        "email": "",
    }


def _points(rng: random.Random, count: int, city_index: int, base: date,
            title: str) -> List[Dict[str, str]]:
    """
    Точки маршрута: наименование салона, адрес, дата, окно времени.

    В адресе город стоит БЕЗ префикса «г. »: префикс уже есть в названии
    региона («г. Москва»), и «г. Москва, г. Москва» — это тот самый дефект
    «двойной префикс города», который проверяет БЛОК 1, пункт «м». Данные
    сценария дефектными быть не должны, иначе проверка ловила бы себя.
    """
    points: List[Dict[str, str]] = []
    for i in range(count):
        city, region, postcode = CITIES[(city_index + i) % len(CITIES)]
        points.append({
            "name": f"ООО «Салон {title} {i + 1}»" if i % 3 else "",
            "address": (f"{postcode}, {region}, г. {city}, "
                        f"ул. Складская, д. {rng.randint(1, 120)}"),
            "date": (base + timedelta(days=i)).isoformat(),
            "time_window": rng.choice(["09:00-18:00", "09:00-15:00", "10:00-16:00", ""]),
        })
    return points


def _vehicles(rng: random.Random, count: int, loading_count: int,
              unloading_count: int) -> List[Dict[str, Any]]:
    """Перевозимые машины с привязкой к точкам погрузки и выгрузки."""
    vehicles = []
    for i in range(count):
        vehicles.append({
            "vin": _vin(rng, i + 1),
            "brand_model": CAR_BRANDS[i % len(CAR_BRANDS)],
            "plate_number": _plate(rng),
            "year": rng.randint(2018, 2025),
            "color": rng.choice(["Белый", "Серый", "Чёрный", "Синий", "Красный"]),
            "vehicle_type": "Легковой автомобиль",
            "loading_index": (i % loading_count) + 1 if loading_count else 0,
            "unloading_index": (i % unloading_count) + 1 if unloading_count else 0,
        })
    return vehicles


def _apply_date_format(contract: Dict[str, Any], fmt: str) -> Dict[str, Any]:
    """
    Переписывает даты договора в один из форматов core/dates.py.

    Генератор обязан принимать все семь форматов — это и проверяет БЛОК 1,
    пункт «з» (на выходе даты одного вида при любом формате на входе).
    """
    result = dict(contract)
    for key in ("date", "loading_plan_date", "unloading_plan_date"):
        value = result.get(key)
        if not value:
            continue
        try:
            parsed = date.fromisoformat(str(value)[:10])
        except (ValueError, TypeError):
            continue
        result[key] = parsed.strftime(fmt)
    return result


# ─────────────────────────────────────────────────────────────
# Сборка сценариев
# ─────────────────────────────────────────────────────────────

def _dimensions(rng: random.Random, count: int) -> Dict[str, List[Any]]:
    """
    Раздаёт размерности сценариям по матрице покрытия.

    Списки собираются ПО ТРЕБУЕМЫМ ЧИСЛАМ и перемешиваются: доля каждого
    значения получается ровно такой, как в COVERAGE_MATRIX, а не «примерно».
    """
    vehicles = ([1] * 20 + [6] * 40 + [12] * 40)
    loadings = ([1] * 50 + [5] * 25 + [10] * 25)
    unloadings = ([1] * 50 + [5] * 25 + [10] * 25)
    rng.shuffle(vehicles)
    rng.shuffle(loadings)
    rng.shuffle(unloadings)

    # Род: 50/50, но и внутри каждого вида стороны — поровну (33/17 и т. п.),
    # иначе у ООО оказались бы только мужчины и ветвь «женщина-директор» не
    # проверилась бы вовсе.
    gender = _round_robin(["male", "female"], count)
    rng.shuffle(gender)

    return {
        "vehicles": vehicles[:count],
        "loadings": loadings[:count],
        "unloadings": unloadings[:count],
        "gender": gender[:count],
    }


def build_scenarios(seed: int = SEED) -> List[Dict[str, Any]]:
    """
    Строит 100 сценариев с покрытием матрицы.

    Особые признаки раздаются индексам по расписанию: «каждый пятнадцатый —
    короткое ФИО», «индексы 75…84 — дефисные фамилии» и так далее. Это
    делает покрытие проверяемым: сдвиг на один сценарий виден в index.json.
    """
    rng = random.Random(seed)
    count = SCENARIO_COUNT
    dims = _dimensions(rng, count)

    # ── Вид ФИО: расписание по индексу + два явных блока ──
    name_shape = ["plain"] * count
    for i in range(count):
        if i % 15 == 0:
            name_shape[i] = "short"
        elif i % 10 == 3:
            name_shape[i] = "foreign"
        elif i % 10 == 7:
            name_shape[i] = "long"
    for j in range(10):
        name_shape[75 + j] = "hyphen"
    for j in range(5):
        name_shape[85 + j] = "foreign"
        name_shape[90 + j] = "long"
    _fix_distribution(rng, name_shape, {
        "hyphen": 10, "foreign": 10, "long": 10, "short": 10,
    }, "name_shape", leftover="plain")

    # ── Вид стороны: круг ООО → ИП с НДС → ИП без НДС, доведённый до 33/33/34 ──
    party_kind = _round_robin(["ООО", "ИП с НДС", "ИП без НДС"], count)
    _fix_distribution(rng, party_kind, COVERAGE_MATRIX["party_kind"], "party_kind")

    # ── Особые блоки признаков ──
    # Спецсимволы: 85…89 (блок дефисных фамилий) и 95…99 (блок «хвост»).
    special_chars = [False] * count
    for j in range(5):
        special_chars[85 + j] = True
        special_chars[95 + j] = True

    # Длинные наименования сторон: 84…88 и 95…99 — итого 10.
    long_company = [False] * count
    for j in range(5):
        long_company[84 + j] = True
        long_company[95 + j] = True

    # Пустые необязательные поля: ровно 20 сценариев подряд (80…99).
    empty_fields = [False] * count
    for j in range(20):
        empty_fields[80 + j] = True

    # Крайние суммы: расписание по индексу (i % 11 == 5) доводится до 12.
    extreme_amount = [i % 11 == 5 for i in range(count)]
    _fix_distribution(rng, extreme_amount, {True: 12, False: 88},
                      "amount_extreme", leftover=False)

    # Суммы с копейками: у каждого пятого сценария — сумма с ненулевыми
    # копейками (проверка «прописью» на копейках), итого ровно 10.
    kopecks = [i % 10 == 2 for i in range(count)]

    scenarios: List[Dict[str, Any]] = []
    for i in range(count):
        kind = name_shape[i]
        flags = {
            "hyphen_surname": kind == "hyphen",
            "foreign_name": kind == "foreign",
            "long_name": kind == "long",
            "short_name": kind == "short",
            "empty_fields": empty_fields[i],
            "special_chars": special_chars[i],
            "long_company_names": long_company[i],
        }
        amount = AMOUNTS[i % len(AMOUNTS)]
        if extreme_amount[i]:
            amount = EXTREME_AMOUNTS[i % len(EXTREME_AMOUNTS)]
        elif kopecks[i]:
            amount = KOPECK_AMOUNTS[i % len(KOPECK_AMOUNTS)]
        scenarios.append(_assemble(
            rng=rng,
            index=i,
            party_kind=party_kind[i],
            gender=dims["gender"][i],
            kind=kind,
            vehicles_count=dims["vehicles"][i],
            loadings_count=dims["loadings"][i],
            unloadings_count=dims["unloadings"][i],
            flags=flags,
            amount=amount,
            date_fmt_index=i % len(DATE_FORMATS),
            empty_case=empty_fields[i],
            extreme=bool(extreme_amount[i]),
        ))

    return scenarios


def _assemble(rng: random.Random, index: int, party_kind: str, gender: str,
              kind: str, vehicles_count: int, loadings_count: int,
              unloadings_count: int, flags: Dict[str, bool], amount: float,
              date_fmt_index: int, empty_case: bool = False,
              extreme: bool = False) -> Dict[str, Any]:
    """Собирает один сценарий и его описание."""
    carrier = _organization(rng, index, party_kind, gender)
    # Заказчик — ДРУГОЙ вид лица, чем перевозчик: так на одном документе
    # проверяются обе ветви п. 1.1 и 1.2 бланка, а не только ветвь перевозчика.
    customer_kind = {"ООО": "ИП без НДС", "ИП с НДС": "ООО",
                     "ИП без НДС": "ООО"}[party_kind]
    customer = _organization(rng, index + 3, customer_kind, gender)

    driver = _driver_person(rng, kind, gender)

    city_index = index % len(CITIES)
    city = CITIES[city_index][0]
    loadings = _points(rng, loadings_count, city_index,
                       BASE_DATE + timedelta(days=1), "Погрузки")
    unloadings = _points(rng, unloadings_count, (city_index + 3) % len(CITIES),
                         BASE_DATE + timedelta(days=4), "Выгрузки")

    tractor = {
        "brand_model": rng.choice(TRACTOR_BRANDS),
        "plate_number": _plate(rng),
        "color": rng.choice(["Белый", "Серый", "Синий"]),
        "year": rng.randint(2016, 2024),
    }
    trailer = {
        "brand_model": rng.choice(TRAILER_BRANDS),
        "plate_number": (f"{rng.randint(10, 99)}{rng.choice('АВЕКМНОРСТУХ')}"
                         f"{rng.choice('АВЕКМНОРСТУХ')}{rng.randint(10, 99)}"),
        "color": rng.choice(["Серый", "Белый", "Чёрный"]),
        "year": rng.randint(2014, 2023),
    }

    price = float(amount)
    vat_rate_num = 0 if party_kind == "ИП без НДС" else rng.choice([20, 22])
    contract: Dict[str, Any] = {
        "number": f"{index + 1:03d}-{BASE_DATE.year}",
        "date": BASE_DATE.isoformat(),
        "route": (f"{CITIES[city_index][0]} - "
                  f"{CITIES[(city_index + 3) % len(CITIES)][0]}"),
        "carrier_type": party_kind,
        "vat_rate": f"{vat_rate_num}%",
        "vat_rate_num": vat_rate_num,
        "price_without_vat": price,
        "price_with_vat": round(price * (1 + vat_rate_num / 100), 2),
        "payment_days": rng.choice([5, 10, 14, 20, 30, 45]),
        "prepayment_amount": 0.0,
        "loading_plan_date": (BASE_DATE + timedelta(days=1)).isoformat(),
        "unloading_plan_date": (BASE_DATE + timedelta(days=4)).isoformat(),
        "loading_plan_time_from": "09:00",
        "loading_plan_time_to": "18:00",
    }
    contract = _apply_date_format(contract, DATE_FORMATS[date_fmt_index])

    if flags.get("special_chars"):
        # Спецсимволы: амперсанд ломает XML, если не экранирован; кавычки,
        # угловые скобки и длинное тире проверяют автозамену и переносы.
        carrier["full_name"] = "ООО «Ромашка & Ко» <Транс>"
        carrier["short_name"] = "ООО «Ромашка & Ко»"
        contract["route"] = "Москва — Санкт-Петербург (через «Валдай»)"
        loadings[0]["name"] = "ООО «Салон №1» & Партнёр"
        unloadings[0]["name"] = "Склад «Юг» <основной>"

    if flags.get("long_company_names"):
        # Сценарий проверяет ДЛИННОЕ (80+ символов) наименование стороны.
        # Вид лица при этом не подменяется: у ИП наименование — это ФИО, и
        # подставить туда название ООО значило бы получить дефектный
        # сценарий («Индивидуальный предприниматель Общество с ограниченной
        # ответственностью …»), который проверка справедливо пометит как
        # расхождение вида стороны и наименования.
        if carrier.get("entity_type") == "ООО":
            carrier["full_name"] = LONG_ORGANIZATION
            carrier["short_name"] = LONG_ORGANIZATION_SHORT
            carrier["director_position"] = "Генеральный директор"
        else:
            carrier["full_name"] = f"Индивидуальный предприниматель {LONG_IP_PERSON}"
            carrier["short_name"] = f"ИП {LONG_IP_PERSON}"
        if customer.get("entity_type") == "ООО":
            customer["full_name"] = LONG_CUSTOMER_ORGANIZATION
            customer["short_name"] = LONG_CUSTOMER_ORGANIZATION_SHORT

    if empty_case:
        # Пустые необязательные поля: банк, e-mail, КПП, категории ВУ, срок
        # действия ВУ, место рождения, цвет и год ТС, фактический адрес.
        for key in ("actual_address", "email", "bank_account", "bik",
                    "correspondent_account", "bank_name", "kpp"):
            carrier[key] = ""
        trailer["color"] = ""
        trailer["year"] = 0
        tractor["color"] = ""
        tractor["year"] = 0
        driver["birth_place"] = ""
        driver["license_categories"] = ""
        driver["license_expiry_date"] = ""
        driver["phone"] = ""

    payload = {
        "driver": driver,
        "carrier": carrier,
        "customer": customer,
        "vehicles": _vehicles(rng, vehicles_count, loadings_count, unloadings_count),
        "tractor": tractor,
        "trailer": trailer,
        "contract": contract,
        "loadings": loadings,
        "unloadings": unloadings,
        "city": city,
    }

    amount_text = f"{price:.2f}"
    description = {
        "index": index,
        "party_kind": party_kind,
        "carrier_entity_type": carrier.get("entity_type"),
        "customer_entity_type": customer.get("entity_type"),
        "driver_gender": gender,
        "name_shape": kind,
        "vehicles_count": vehicles_count,
        "loadings_count": loadings_count,
        "unloadings_count": unloadings_count,
        "amount": amount_text,
        "vat_rate": vat_rate_num,
        "date_format": DATE_FORMATS[date_fmt_index],
        "flags": {
            **flags,
            "amount_extreme": bool(extreme),
            "amount_with_kopecks": amount_text.split(".")[1] != "00",
        },
    }
    return {"payload": payload, "description": description}


# ─────────────────────────────────────────────────────────────
# Запись на диск
# ─────────────────────────────────────────────────────────────

def collect_coverage(scenarios: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Считает фактическое покрытие по описаниям сценариев.

    Возвращает словари «значение → сколько раз» для размерностей и
    «признак → сколько раз» для флагов. Именно этот результат сверяется
    с COVERAGE_MATRIX, а не то, что генератор «обещал» раздать.
    """
    counts: Dict[str, Dict[str, int]] = {
        "party_kind": {}, "driver_gender": {}, "vehicles_count": {},
        "loadings_count": {}, "unloadings_count": {}, "date_format": {},
    }
    flags: Dict[str, int] = {}
    for scenario in scenarios:
        description = scenario["description"]
        for key in counts:
            value = str(description[key])
            counts[key][value] = counts[key].get(value, 0) + 1
        for flag, value in description["flags"].items():
            if value:
                flags[flag] = flags.get(flag, 0) + 1
    return {**counts, **flags}


def write_scenarios(scenarios: List[Dict[str, Any]],
                    out_dir: Path = OUT_DIR) -> dict:
    """Раскладывает сценарии по файлам и собирает index.json с покрытием."""
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = []
    for scenario in scenarios:
        number = scenario["description"]["index"] + 1
        (out_dir / f"{number}.json").write_text(
            json.dumps(scenario["payload"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary.append(scenario["description"])

    index = {
        "seed": SEED,
        "count": len(scenarios),
        "coverage": collect_coverage(scenarios),
        "matrix": COVERAGE_MATRIX,
        "scenarios": summary,
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return index


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(description="100 сценариев договора перевозки")
    parser.add_argument("--limit", type=int, default=SCENARIO_COUNT,
                        help="сколько сценариев сгенерировать (по умолчанию все)")
    parser.add_argument("--out", default=str(OUT_DIR), help="папка для сценариев")
    parser.add_argument("--clean", action="store_true",
                        help="удалить папку сценариев перед записью")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)

    scenarios = build_scenarios()[: args.limit]
    index = write_scenarios(scenarios, out_dir)

    print(f"Сценариев: {index['count']}")
    print("Покрытие (факт):")
    for key, value in index["coverage"].items():
        print(f"  {key}: {value}")
    print(f"Папка: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
