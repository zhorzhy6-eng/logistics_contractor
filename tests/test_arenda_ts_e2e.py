#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сводные (интеграционные) тесты бэкенда договора аренды ТС с экипажем
(ЭТАП 3.1.D.A.6).

Проверяется вся цепочка целиком — ContractData → валидатор → генератор →
постобработка → готовый DOCX, — а не отдельные модули: те у себя уже покрыты
(tests/test_arenda_ts_validator.py, tests/test_arenda_ts_generator.py,
tests/test_arenda_ts_postprocess.py, tests/test_prompts_arenda_ts.py,
tests/test_arenda_ts_template.py). Цель этих тестов — стыки: там, где модули
договариваются об именах полей и о форме данных, расхождение не видно ни
одному модульному тесту.

Стыки, которые проверяются здесь:

  1. «ContractData → генератор»: фикстуры собраны ровно по той раскладке,
     которую ждёт ArendaTsGenerator._build_replacements_map — блоки сторон
     lessee / lessor, срок аренды lease_start_date / lease_end_date, маршрут
     route и точки loadings / unloadings лежат ВНУТРИ contract. Отдельно
     проверяется, что генератор дочитывает время подачи ТС из time_window,
     если распознавание не разложило его на time_from / time_to.
  2. «Валидатор → генератор»: валидатор не портит данные, генератор не падает
     на проверенных данных, а отсутствие необязательных полей не рвёт цепочку.
  3. «Генератор → постобработка»: пустые строки таблицы машин и
     незаполненные точки маршрута исчезают из файла НА ДИСКЕ (а не только из
     программно собранного Document()).
  4. «ООО ↔ ИП»: три варианта Арендатора — три бланка, разный состав сумм и
     разный набор реквизитов: КПП и «Устава» у ООО, свидетельство о
     госрегистрации у ИП, «НДС не облагается» у ИП без НДС.
  5. «Приложение № 1 (Акт)»: Акт — часть того же файла во всех трёх
     вариантах; тягач, прицеп и экипаж в нём заполнены, а поля для ручного
     заполнения остались пустыми ячейками.
  6. «промпт → генератор» (TODO 3.1.D.B.1): распознавание кладёт lessee /
     lessor / route / lease_start_date / lease_end_date в КОРЕНЬ ответа, а
     суммы — в contract.sum_wo_vat / sum_total, тогда как ContractData
     хранит корневые поля только внутри contract, а генератор читает базу
     арендной платы из своего _base_price. Тесты фиксируют текущее
     поведение и ловят регресс; чинится это в 3.1.D.B.1 сборщиком UI.

Все данные синтетические, реальных ПДн нет. Выходные файлы создаются в
work_dir (tests/_tmp) и удаляются в finally.
"""

import copy
import gc
import logging
import re
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
from docx import Document

from core.contract_data import ContractData
from core.contracts.arenda_ts.generator import (
    MAX_CARS,
    MAX_POINTS,
    ArendaTsGenerator,
)
from core.contracts.arenda_ts.postprocess import (
    RemoveEmptyLoadingUnloadingBlocksStep,
    RemoveEmptyVehicleRowsStep,
)
from core.contracts.arenda_ts.validator import ArendaTsValidator
from core.contracts.base_generator import ConvertNewlinesStep
from core.contracts.factory import GeneratorFactory

# ─────────────────────────────────────────────────────────────
# Константы тестовых данных
# ─────────────────────────────────────────────────────────────

#: Варианты бланка: ключ — вид Арендатора, значение — файл шаблона.
TEMPLATE_NAMES = {
    "ООО": "shablon_arenda_ts_ooo.docx",
    "ИП с НДС": "shablon_arenda_ts_ip_with_vat.docx",
    "ИП без НДС": "shablon_arenda_ts_ip_without_vat.docx",
}

VARIANTS = tuple(TEMPLATE_NAMES)

#: Заголовки таблицы машин п. 3.1 — по ним таблица ищется в готовом документе.
CAR_HEADERS = ("№", "Марка, модель", "VIN-номер", "Точка погрузки",
               "Точка выгрузки")

#: Сколько машин и точек в «полных» данных: 3 из 12 и 2+1 из 10. Так видно,
#: что постобработка действительно удаляет пустые строки и блоки.
FILLED_CARS = 3

#: Машин и точек в бланке (столько строк оставляет docxtpl до постобработки).
TEMPLATE_CARS = MAX_CARS
TEMPLATE_POINTS = MAX_POINTS

PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}")

#: Строки точек маршрута: «3.2.N. …» / «3.3.N. …».
LOADING_LINE_RE = re.compile(r"^3\.2\.\d+\.")
UNLOADING_LINE_RE = re.compile(r"^3\.3\.\d+\.")

#: Договор, город и срок аренды (одинаковы во всех трёх вариантах).
CONTRACT_NUMBER = "ТЛ-574"
CONTRACT_DATE = "2026-09-19"
CONTRACT_DATE_TEXT = "19.09.2026"
LEASE_START = "2026-09-21"
LEASE_END = "2026-09-27"
LEASE_START_TEXT = "21.09.2026"
LEASE_END_TEXT = "27.09.2026"
ROUTE = "г. Москва — г. Калуга — г. Чехов"

#: Планируемая дата завершения рейса (п. 3.3.2) — ТРЕТЬЯ дата договора, не
#: совпадающая ни с концом срока аренды, ни с датой точки выгрузки: в образце
#: ТЛ-574 это тоже разные даты (шаг FIX-1/T).
PLANNED_COMPLETION = "2026-09-26"
PLANNED_COMPLETION_TEXT = "26.09.2026"

#: Срок оплаты (п. 4.5), банковских дней: значение по умолчанию из вкладки
#: «Стоимость» и другое число — оба печатаются цифрами и прописью.
PAYMENT_DAYS_DEFAULT = 30
PAYMENT_DAYS_CUSTOM = 45
PAYMENT_DAYS_DEFAULT_WORDS = "тридцати"
PAYMENT_DAYS_CUSTOM_WORDS = "сорока пяти"

#: Стороны. Арендатор — наша сторона (три варианта), Арендодатель — всегда
#: ООО: на это рассчитаны все три бланка.
LESSEE_OOO_NAME = "ООО «Арендатор-Тест»"
LESSEE_OOO_SHORT = "ООО «АТ»"
LESSEE_OOO_INN = "7701234567"
LESSEE_OOO_KPP = "770101001"
LESSEE_OOO_OGRN = "1027700132195"
LESSEE_OOO_DIRECTOR = "Петров Пётр Петрович"

LESSEE_IP_NAME = ("Индивидуальный предприниматель "
                  "Смирнов Сергей Сергеевич")
LESSEE_IP_SHORT = "ИП Смирнов С.С."
LESSEE_IP_INN = "770123456789"
LESSEE_IP_OGRNIP = "321770000123456"
LESSEE_IP_DIRECTOR = "Смирнов Сергей Сергеевич"

LESSEE_OOO_SHORT_FIO = "П.П. Петров"
LESSEE_IP_SHORT_FIO = "С.С. Смирнов"

LESSOR_NAME = "ООО «Арендодатель-Тест»"
LESSOR_SHORT = "ООО «АДТ»"
LESSOR_INN = "7709876543"
LESSOR_OGRN = "1027700132196"
LESSOR_DIRECTOR = "Сидоров Сидор Сидорович"
LESSOR_SHORT_FIO = "С.С. Сидоров"

#: Объект аренды (п. 2.1) — тягач и прицеп, они же строки Акта.
TRACTOR_BRAND = "Тягач-Модель 5440"
TRACTOR_PLATE = "А001АА01"
TRACTOR_TYPE = "грузовой тягач седельный"
TRAILER_BRAND = "Прицеп-Модель 9"
TRAILER_PLATE = "Б002ББ02"

DRIVER_NAME = "Иванов Иван Иванович"

#: Суммы. ООО / ИП с НДС: 221 099,18 + 22% (48 641,82) = 269 741,00.
OOO_BASE_SUM = 221099.18
OOO_VAT_SUM = 48641.82
OOO_TOTAL_SUM = 269741.00

#: ИП без НДС: одна сумма, НДС не облагается. Ни её саму, ни «налоговые»
#: суммы этого варианта не должно быть в документах с НДС.
IP_WITHOUT_VAT_SUM = 135833.00
IP_WITHOUT_VAT_VAT = 29883.26
IP_WITHOUT_VAT_TOTAL = 165716.26


def _money(amount: float) -> str:
    """
    Сумма в формате документа: «221 099,18» (разряды разделены, запятая).

    Разделитель разрядов — ОБЫЧНЫЙ пробел: генератор печатает неразрывный
    (ArendaTsGenerator._format_money), а чтение документа в этих тестах
    нормализует пробелы (см. _flatten), поэтому и ожидаемые строки берутся
    в обычных пробелах.
    """
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",")


OOO_BASE_TEXT = _money(OOO_BASE_SUM)
OOO_VAT_TEXT = _money(OOO_VAT_SUM)
OOO_TOTAL_TEXT = _money(OOO_TOTAL_SUM)
IP_SUM_TEXT = _money(IP_WITHOUT_VAT_SUM)
IP_VAT_TEXT = _money(IP_WITHOUT_VAT_VAT)
IP_TOTAL_TEXT = _money(IP_WITHOUT_VAT_TOTAL)

#: Сборщик формы кладёт в блок стороны и юридический, и фактический адрес
#: (actual_address есть в схеме промпта), но плейсхолдера под фактический адрес
#: нет ни в одном из трёх бланков: печатается только legal_address. Валидатор
#: считает адрес стороны по legal_address, поэтому фактический адрес для него
#: невидим — на печать это не влияет и замечанием не сопровождается.


@pytest.fixture(autouse=True)
def _release_documents():
    """Word-файлы на Windows освобождаем до удаления временных копий."""
    yield
    gc.collect()


@pytest.fixture(scope="module", autouse=True)
def _builtin_types_loaded():
    """Регистрация типов: без неё фабрика не знает про arenda_ts."""
    from core.contracts.registry import ContractTypeRegistry

    ContractTypeRegistry.load_builtin()


@pytest.fixture
def generator(templates_dir) -> ArendaTsGenerator:
    return ArendaTsGenerator(templates_dir=str(templates_dir))


@pytest.fixture
def validator() -> ArendaTsValidator:
    return ArendaTsValidator()


# ─────────────────────────────────────────────────────────────
# Тестовые данные (раскладка сборщика формы: всё внутри contract)
# ─────────────────────────────────────────────────────────────

def _vehicle(number: int, loading_point: str, unloading_point: str) -> dict:
    """
    Машина таблицы п. 3.1 с точками погрузки и выгрузки в той же строке.

    VIN — 17 символов без букв I, O, Q (ISO 3779), иначе валидатор выдаст
    замечание «VIN не похож на стандартный».
    """
    return {
        "brand_model": f"МОДЕЛЬ {number}",
        "vin": f"XTC651150N0001{number:03d}",
        "loading_point": loading_point,
        "unloading_point": unloading_point,
    }


def _lessee(variant: str) -> dict:
    """
    Арендатор — наша сторона: ООО, ИП с НДС или ИП без НДС.

    Форма блока — ровно те имена полей, которые читает
    ArendaTsGenerator._fill_lessee (и те же, что отдаёт распознавание,
    core/prompts/arenda_ts.py). Фактический адрес (actual_address) в бланк не
    печатается, поэтому legal_address обязателен.
    """
    if variant == "ООО":
        return {
            "entity_type": "ООО",
            "full_name": LESSEE_OOO_NAME,
            "short_name": LESSEE_OOO_SHORT,
            "inn": LESSEE_OOO_INN,
            "kpp": LESSEE_OOO_KPP,
            "ogrn": LESSEE_OOO_OGRN,
            "legal_address": "г. Москва, ул. Арендаторская, д. 1",
            "actual_address": "г. Москва, ул. Арендаторская, д. 1",
            "bank_account": "40702810000000000001",
            "bank_name": "ПАО Сбербанк",
            "bik": "044525225",
            "corr_account": "30101810400000000225",
            "director_name": LESSEE_OOO_DIRECTOR,
            "director_position": "Генеральный директор",
            "email": "arenda@example.ru",
            "edo": "2AE-7F31-4C50",
        }

    return {
        "entity_type": "ИП",
        "full_name": LESSEE_IP_NAME,
        "short_name": LESSEE_IP_SHORT,
        "inn": LESSEE_IP_INN,
        # У индивидуального предпринимателя КПП не бывает: промпт отдаёт "".
        "kpp": "",
        "ogrn": LESSEE_IP_OGRNIP,
        "legal_address": "г. Москва, ул. Предпринимательская, д. 7",
        "actual_address": "г. Москва, ул. Предпринимательская, д. 7",
        "bank_account": "40802810000000000011",
        "bank_name": "АО «Банк Тест»",
        "bik": "044525227",
        "corr_account": "30101810400000000227",
        "director_name": LESSEE_IP_DIRECTOR,
        "director_position": "Индивидуальный предприниматель",
        "email": "ip@example.ru",
        "edo": "1BC-6E20-3B40",
    }


def _lessor() -> dict:
    """Арендодатель — вторая сторона; во всех трёх бланках это ООО."""
    return {
        "entity_type": "ООО",
        "full_name": LESSOR_NAME,
        "short_name": LESSOR_SHORT,
        "inn": LESSOR_INN,
        "kpp": "770901001",
        "ogrn": LESSOR_OGRN,
        "legal_address": "г. Москва, ул. Арендодательская, д. 2",
        "actual_address": "г. Москва, ул. Арендодательская, д. 2",
        "bank_account": "40702810000000000002",
        "bank_name": "АО «Банк Второй»",
        "bik": "044525226",
        "corr_account": "30101810400000000226",
        "director_name": LESSOR_DIRECTOR,
        "director_position": "Директор",
        "email": "lessor@example.ru",
        "edo": "3CD-8A42-5D60",
    }


def _driver() -> dict:
    """Экипаж (п. 3.5): паспорт и удостоверение — как в справочнике водителя."""
    return {
        "full_name": DRIVER_NAME,
        "birth_date": "1980-01-01",
        "passport_series": "18 22",
        "passport_number": "926830",
        "passport_issuer": "Отделом УФМС России по г. Москве",
        "passport_issue_date": "2023-01-30",
        "license_series": "99 36",
        "license_number": "123456",
        "license_issue_date": "2020-01-01",
        "registration_address": "г. Москва, ул. Водительская, д. 3",
        "phone": "+7 (999) 123-45-67",
    }


def _loadings(count: int = 2) -> list:
    """Точки погрузки: адрес, дата и окно подачи ТС (time_window бланка)."""
    addresses = ("г. Москва, ул. Складская, д. 1",
                 "г. Калуга, ул. Промышленная, д. 5")
    return [
        {
            "address": addresses[number - 1],
            "date": f"2026-09-2{number}",
            "time_window": "08:00-18:00",
        }
        for number in range(1, count + 1)
    ]


def _unloadings(count: int = 1, planned_completion: str = "") -> list:
    """
    Точки выгрузки: адрес и плановая дата завершения.

    Дата последней точки — планируемая дата завершения рейса (в бланке это
    п. 3.3.2, где стоит {{planned_completion_date}}). Если она передана, у
    последней точки дата своя, отличная от неё: так видно, что в п. 3.3.2
    печатается поле договора, а не дата точки. Остальные точки — со своей
    датой. Порядок аргументов хвостом, чтобы не ломать вызовы вида
    _unloadings(2).
    """
    addresses = ("г. Чехов, ул. Приёмная, д. 9",
                 "г. Калуга, ул. Конечная, д. 1")
    points = []
    for number in range(1, count + 1):
        date = f"2026-09-2{number + 3}"
        if planned_completion and number == count:
            date = "2026-09-25"
        points.append({
            "address": addresses[number - 1],
            "date": date,
            "time_window": "",
        })
    return points


def _vehicles(count: int = FILLED_CARS) -> list:
    """Машины п. 3.1: точки в строке берутся из точек маршрута договора."""
    loadings = _loadings()
    unloadings = _unloadings()
    return [
        _vehicle(
            number,
            loading_point=loadings[0]["address"],
            unloading_point=unloadings[0]["address"],
        )
        for number in range(1, count + 1)
    ]


def _sums(variant: str) -> dict:
    """Суммы п. 4.1 в форме распознавания (sum_wo_vat / sum_vat / sum_total)."""
    if variant == "ИП без НДС":
        return {"vat_rate": "0%", "sum_wo_vat": 0.0, "sum_vat": 0.0,
                "sum_total": OOO_BASE_SUM}
    return {"vat_rate": "22%", "sum_wo_vat": OOO_BASE_SUM, "sum_vat": OOO_VAT_SUM,
            "sum_total": OOO_TOTAL_SUM}


def _contract_payload(variant: str, carrier_type: str = "auto") -> dict:
    """
    Блок contract — ровно та раскладка, которую ждёт генератор.

    Блоки сторон и срок аренды лежат ВНУТРИ contract: так их кладёт сборщик
    формы, и именно оттуда их читают ArendaTsGenerator._party_block /
    _root_value и ArendaTsValidator. carrier_type заполняет интерфейс;
    carrier_type=None — поля нет (проверка стыка с промптом, который его не
    отдаёт).

    Три даты и срок оплаты — как их отдаёт вкладка: lease_start_date /
    lease_end_date (п. 2.5), planned_completion_date (п. 3.3.2) и
    payment_days (п. 4.5). Ни одна из дат не выводится из другой.
    """
    contract = {
        "number": CONTRACT_NUMBER,
        "date": CONTRACT_DATE,
        "lessee": _lessee(variant),
        "lessor": _lessor(),
        "lease_start_date": LEASE_START,
        "lease_end_date": LEASE_END,
        "planned_completion_date": PLANNED_COMPLETION,
        "route": ROUTE,
        "payment_days": PAYMENT_DAYS_DEFAULT,
    }
    if carrier_type == "auto":
        carrier_type = variant
    if carrier_type:
        contract["carrier_type"] = carrier_type

    contract.update(_sums(variant))
    return contract


def _make_data(variant: str, carrier_type: str = "auto") -> ContractData:
    """Полный ContractData договора аренды ТС с экипажем."""
    return ContractData(
        driver=_driver(),
        vehicles=_vehicles(),
        tractor={"brand_model": TRACTOR_BRAND, "plate_number": TRACTOR_PLATE,
                 "vehicle_type": TRACTOR_TYPE},
        trailer={"brand_model": TRAILER_BRAND, "plate_number": TRAILER_PLATE},
        contract=_contract_payload(variant, carrier_type),
        loadings=_loadings(),
        unloadings=_unloadings(),
    )


@pytest.fixture
def valid_ooo_data() -> ContractData:
    """Заполненные данные договора для Арендатора-ООО (НДС 22%)."""
    return _make_data("ООО")


@pytest.fixture
def valid_ip_with_vat_data() -> ContractData:
    """Заполненные данные договора для Арендатора-ИП с НДС (НДС 22%)."""
    return _make_data("ИП с НДС")


@pytest.fixture
def valid_ip_without_vat_data() -> ContractData:
    """Заполненные данные договора для Арендатора-ИП без НДС (УСН)."""
    return _make_data("ИП без НДС")


@pytest.fixture(params=VARIANTS)
def valid_data_of_variant(request) -> ContractData:
    """Данные каждого из трёх вариантов по очереди (для общих проверок)."""
    return _make_data(request.param)


@pytest.fixture
def generated_docs(generator, valid_data_of_variant, work_dir):
    """
    Готовый DOCX текущего варианта; файл удаляется после теста.

    Данные передаются в generate() как ContractData — так их отдаёт
    оркестрация интерфейса (MainWindow._collect_data()).
    """
    with _generate(generator, valid_data_of_variant, work_dir,
                   f"e2e_{valid_data_of_variant.contract['carrier_type']}") as path:
        yield path


# ─────────────────────────────────────────────────────────────
# Чтение готового документа
# ─────────────────────────────────────────────────────────────

def _document_text(doc) -> str:
    """
    Весь текст документа: абзацы и таблицы (включая вложенные).

    Пробелы нормализованы так же, как в _body_texts (неразрывный разрядный
    пробел сумм → обычный).
    """
    parts = [_flatten(p.text) for p in doc.paragraphs]

    def table_text(table):
        for row in table.rows:
            for cell in row.cells:
                parts.append(_flatten(cell.text))
                for nested in cell.tables:
                    table_text(nested)

    for table in doc.tables:
        table_text(table)

    return "\n".join(parts)


def _flatten(text: str) -> str:
    """
    Текст одной строкой: любые пробелы (в том числе неразрывные) — по одному.

    Неразрывный пробел в бланке разрядный: «221\\u00a0099,18» — так сумму
    печатает генератор (ArendaTsGenerator._format_money), и так же её печатают
    шаблоны перевозки. Для сравнений с ожидаемыми строками удобнее обычный
    пробел.

    Задвоенные пробелы схлопываются не для красоты: docxtpl подставляет
    значение вместе с пробелами вокруг Jinja-тега, и в абзаце на месте
    «{{route}}» остаётся лишний пробел («3.4. … маршрут:  Москва — Калуга»).
    Word его не показывает, но сравнение абзаца целиком ломалось бы на нём,
    а не на данных.
    """
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _body_texts(doc) -> list:
    """Тексты абзацев верхнего уровня (нормализованные, см. _flatten)."""
    return [_flatten(p.text) for p in doc.paragraphs]


def _body_text(doc) -> str:
    """
    Тексты абзацев верхнего уровня одной строкой.

    Для проверок ПОДСТРОКОЙ. Абзацы разделены переводом строки: метка
    «3.5. …» и её строки данных не сливаются в одну псевдостроку.
    """
    return "\n".join(_body_texts(doc))


def _repl(replacements: dict) -> dict:
    """
    Карта замен в том же виде, в каком читается документ (см. _flatten).

    Генератор печатает суммы с неразрывным разрядным пробелом, а документ
    в этих тестах читается с обычными пробелами: без приведения карты замен
    сравнение значений расходилось бы на невидимом символе.
    """
    return {name: _flatten(value) for name, value in replacements.items()}


def _lines(doc, prefix: str) -> list:
    """Абзацы документа с заданным началом."""
    return [text for text in _body_texts(doc) if text.startswith(prefix)]


def _car_table(doc):
    """Таблица машин п. 3.1 — по заголовкам колонок, а не по индексу."""
    for table in doc.tables:
        if not table.rows:
            continue
        headers = tuple(cell.text.strip() for cell in table.rows[0].cells)
        if headers == CAR_HEADERS:
            return table
    return None


def _act_table(doc, label: str):
    """Таблица Акта по метке первой ячейки (метки — из бланка Приложения № 1)."""
    for table in doc.tables:
        if not table.rows:
            continue
        if table.rows[0].cells[0].text.strip() == label:
            return table
    return None


def _act_values(doc, label: str) -> dict:
    """{метка строки: значение} таблицы Акта."""
    table = _act_table(doc, label)
    assert table is not None, f"в Акте нет таблицы «{label}»"
    return {
        row.cells[0].text.strip(): row.cells[1].text.strip()
        for row in table.rows
    }


def _act_signature_table(doc):
    """Таблица подписей Акта: 1×2, в левой ячейке нет реквизитов (ИНН)."""
    for table in doc.tables:
        if len(table.rows) != 1 or len(table.columns) != 2:
            continue
        left, right = table.rows[0].cells
        if (left.text.startswith("АРЕНДАТОР:")
                and right.text.startswith("АРЕНДОДАТЕЛЬ:")
                and "ИНН" not in left.text):
            return table
    return None


# ─────────────────────────────────────────────────────────────
# Утилиты тестов
# ─────────────────────────────────────────────────────────────

@contextmanager
def _generate(generator, data, work_dir, name: str):
    """
    Контекстный менеджер: генерирует договор в отдельную папку work_dir.

    Отдаёт путь к готовому DOCX. Папка у каждого теста своя: файлы одного
    теста не мешают другому, а готовый документ не уходит в общий output/
    проекта. Созданные файлы удаляются в finally — в том числе когда проверки
    внутри теста упали.
    """
    output_dir = work_dir / name
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        path = Path(generator.generate(data, output_dir=str(output_dir)))

        assert path.exists(), f"файл не создан: {path}"
        # Внутри изолированной папки — папка рейса, документ уже в ней
        # (ШАГ «Папка на рейс»: имя из водителя, маршрута и даты договора).
        assert path.parent.parent == output_dir, "файл ушёл мимо изолированной папки"

        yield path
    finally:
        # За тестом убираем и папку рейса: файлов в ней не остаётся.
        for folder in output_dir.iterdir():
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)


def _car_row_numbers(table) -> list:
    """Номера строк таблицы машин: у оставшихся строк колонка «№» заполнена."""
    return [row.cells[0].text.strip() for row in table.rows[1:]]


# ─────────────────────────────────────────────────────────────
# Стык 1: ContractData → генератор
# ─────────────────────────────────────────────────────────────

def test_full_data_passes_validator(validator, valid_data_of_variant):
    """
    Полные данные проходят валидатор без единого замечания — во всех вариантах.

    Пустых замечаний быть и не должно: сборщик формы отдаёт реквизиты обеих
    сторон целиком (наименование, ИНН, ОГРН, юридический адрес, руководитель),
    у ООО заполнен КПП, а у ИП — нет, как и требует бланк.
    """
    report = validator.check(valid_data_of_variant)

    assert report.errors == [], f"неожиданные ошибки: {report.errors}"
    assert report.warnings == [], f"неожиданные замечания: {report.warnings}"


def test_generate_validate_hook_accepts_valid_data(generator,
                                                   valid_data_of_variant):
    """Хук validate() самого генератора согласован с данными формы."""
    report = generator.validate(valid_data_of_variant)

    assert report.errors == [], f"неожиданные ошибки: {report.errors}"


def test_replacements_map_matches_template_placeholders(generator,
                                                        templates_dir,
                                                        valid_data_of_variant):
    """
    Карта замен ровно совпадает с плейсхолдерами своего бланка.

    Это главная проверка стыка «данные → бланк»: лишний или недостающий ключ
    означает, что генератор и шаблон разошлись, — а в готовом документе это
    видно только как «{{…}}» в тексте или как пропущенная строка.

    Единственное исключение — unloading_2_date: он остаётся в карте замен
    парным ключом к unloading_2_address (дата точки из вкладки «Маршрут»), а
    в бланке его место занимает {{planned_completion_date}} (шаг FIX-1-T).
    """
    variant = valid_data_of_variant.contract["carrier_type"]
    template = templates_dir / TEMPLATE_NAMES[variant]
    names = {
        match.strip("{} ")
        for match in PLACEHOLDER_RE.findall(_document_text(Document(str(template))))
    }
    assert names, "в шаблоне не найдено ни одного плейсхолдера"

    replacements = generator.build_replacements(valid_data_of_variant)

    assert not (names - set(replacements)), (
        f"плейсхолдеры бланка без значений ({variant}): "
        f"{sorted(names - set(replacements))}"
    )
    assert set(replacements) - names == {"unloading_2_date"}, (
        f"лишние ключи карты замен ({variant}): "
        f"{sorted(set(replacements) - names - {'unloading_2_date'})}"
    )

    # Дата точки № 2 в карте есть (её читает вкладка), но в бланк не идёт:
    # в п. 3.3.2 печатается планируемая дата завершения рейса.
    assert replacements["unloading_2_address"] == ""
    assert replacements["unloading_2_date"] == ""
    assert replacements["planned_completion_date"] == PLANNED_COMPLETION_TEXT


def test_party_blocks_reach_the_document(generator, valid_data_of_variant,
                                         work_dir):
    """Реквизиты обеих сторон из contract доходят до готового документа."""
    variant = valid_data_of_variant.contract["carrier_type"]
    lessee = valid_data_of_variant.contract["lessee"]

    with _generate(generator, valid_data_of_variant, work_dir,
                   f"e2e_parties_{variant}") as path:
        doc = Document(str(path))
        text = _document_text(doc)

        # Раздел 1: стороны названы своими именами.
        assert lessee["full_name"] in text
        assert lessee["short_name"] in _lines(doc, "1.1. Арендатор:")[0]
        assert LESSOR_NAME in _lines(doc, "1.2. Арендодатель:")[0]

        # Раздел 9: реквизиты и подписи — данные, а не пустые строки.
        requisites = next(
            table for table in doc.tables
            if len(table.rows) == 1 and len(table.columns) == 2
            and table.rows[0].cells[0].text.startswith("АРЕНДАТОР:")
            and "ИНН" in table.rows[0].cells[0].text
        )
        left = requisites.rows[0].cells[0].text
        right = requisites.rows[0].cells[1].text
        assert lessee["full_name"] in left
        assert f"ИНН {lessee['inn']}" in left
        assert f"Юридический адрес: {lessee['legal_address']}" in left
        assert f"ЭДО: {lessee['edo']}" in left
        assert LESSOR_NAME in right
        assert f"ИНН {LESSOR_INN}" in right
        assert f"ОГРН {LESSOR_OGRN}" in right


def test_lessee_requisites_match_variant(generated_docs):
    """
    Стык «вид Арендатора → реквизиты блока»: три варианта — три набора.

    ООО: КПП и метка «ОГРН» плюс основание «Устава».
    ИП: без КПП, метка «ОГРНИП», основание — свидетельство о госрегистрации.
    """
    doc = Document(str(generated_docs))
    text = _document_text(doc)
    variant = generated_docs.parent.parent.name.removeprefix("e2e_")
    # Основание полномочий печатается в п. 1.1 — и только там.
    lessee_line = _lines(doc, "1.1. Арендатор:")[0]

    if variant == "ООО":
        assert f"КПП {LESSEE_OOO_KPP}" in text
        assert f"ОГРН {LESSEE_OOO_OGRN}" in text
        assert "ОГРНИП" not in text
        assert "на основании Устава" in lessee_line
        assert "именуемое в дальнейшем «Арендатор»" in lessee_line
    else:
        assert "КПП" not in text, "у индивидуального предпринимателя КПП не бывает"
        assert f"ОГРНИП {LESSEE_IP_OGRNIP}" in text
        assert ("на основании свидетельства о государственной регистрации"
                in lessee_line)
        assert "на основании Устава" not in lessee_line
        assert "именуемый в дальнейшем «Арендатор»" in lessee_line

    # Арендодатель — всегда ООО: устав и метка «ОГРН» независимо от варианта.
    lessor_line = _lines(doc, "1.2. Арендодатель:")[0]
    assert "на основании Устава" in lessor_line


def test_lease_dates_and_route_reach_the_document(generator,
                                                  valid_data_of_variant,
                                                  work_dir):
    """Срок аренды (п. 2.5) и маршрут (п. 3.4) доходят до бланка датами."""
    variant = valid_data_of_variant.contract["carrier_type"]

    with _generate(generator, valid_data_of_variant, work_dir,
                   f"e2e_lease_{variant}") as path:
        texts = _body_texts(Document(str(path)))

        assert any(
            text.startswith(
                f"2.5. Плановый период аренды: с {LEASE_START_TEXT} г. "
                f"по {LEASE_END_TEXT} г. включительно"
            )
            for text in texts
        ), "нет планового периода аренды"
        assert f"3.4. Согласованный маршрут: {ROUTE}." in texts


def test_planned_completion_date_is_not_confused_with_lease_end(
    generator, work_dir
):
    """
    Планируемая дата завершения рейса и три остальные даты не путаются.

    Три даты договора — разные поля: lease_start_date / lease_end_date
    (п. 2.5, срок аренды) и planned_completion_date (п. 3.3.2, завершение
    рейса). Здесь у каждой СВОЁ значение, и все проверяются по местам: если
    генератор возьмёт дату не из того поля, договор напечатает не ту дату,
    что введена в интерфейсе.
    """
    data = _make_data("ООО")
    # Точки выгрузки — две штуки, у каждой своя дата; п. 3.3.2 при этом
    # печатает дату завершения рейса из contract.
    points = _unloadings(2, planned_completion=PLANNED_COMPLETION)
    data.unloadings = points
    data.contract["unloadings"] = [dict(point) for point in points]

    first_unloading_text = "24.09.2026"   # дата первой точки
    second_unloading_text = "25.09.2026"  # дата второй точки — своя
    assert second_unloading_text != PLANNED_COMPLETION_TEXT

    with _generate(generator, data, work_dir, "e2e_completion_date") as path:
        doc = Document(str(path))
        texts = _body_texts(doc)
        period = next(t for t in texts if t.startswith("2.5. Плановый период"))
        first = next(t for t in texts if t.startswith("3.3.1."))
        second = next(t for t in texts if t.startswith("3.3.2."))

    # П. 2.5 — срок аренды; даты завершения рейса там нет.
    assert LEASE_START_TEXT in period and LEASE_END_TEXT in period
    assert PLANNED_COMPLETION_TEXT not in period

    # П. 3.3.1 — дата первой точки, а не дата завершения рейса.
    assert first_unloading_text in first
    assert PLANNED_COMPLETION_TEXT not in first

    # П. 3.3.2 — планируемая дата завершения рейса, а не дата самой точки.
    assert PLANNED_COMPLETION_TEXT in second
    assert second_unloading_text not in second


def test_payment_days_custom_value_reaches_the_document(generator, work_dir):
    """Срок оплаты 45 дней печатается цифрами и прописью (п. 4.5)."""
    data = _make_data("ООО")
    data.contract["payment_days"] = PAYMENT_DAYS_CUSTOM

    with _generate(generator, data, work_dir, "e2e_payment_days_45") as path:
        texts = _body_texts(Document(str(path)))

    clause = next(t for t in texts if t.startswith("4.5. Оплата производится"))
    assert "в течение 45 (сорока пяти) банковских дней" in clause
    assert "30 (тридцати)" not in clause


def test_payment_days_default_value_reaches_the_document(generator,
                                                         work_dir):
    """Срок оплаты по умолчанию (30 дней) печатается теми же плейсхолдерами."""
    data = _make_data("ООО")
    assert data.contract["payment_days"] == PAYMENT_DAYS_DEFAULT

    with _generate(generator, data, work_dir, "e2e_payment_days_30") as path:
        texts = _body_texts(Document(str(path)))

    clause = next(t for t in texts if t.startswith("4.5. Оплата производится"))
    assert "в течение 30 (тридцати) банковских дней" in clause


def test_payment_days_missing_is_printed_as_empty(generator, work_dir):
    """
    Срок оплаты не задан — в п. 4.5 пустое место, а не выдуманное число.

    Ноль и отрицательное значение означают «срок не задан» (так их читает и
    сборщик окна); отсутствие ключа — то же самое. О незаполненном сроке
    сообщает валидатор, а генератор ничего не додумывает.
    """
    for value in (0, -5, None):
        data = _make_data("ООО")
        data.contract["payment_days"] = value

        name = f"e2e_payment_days_{value}".replace("-", "minus")
        with _generate(generator, data, work_dir, name) as path:
            texts = _body_texts(Document(str(path)))

        clause = next(t for t in texts if t.startswith("4.5. Оплата производится"))
        # Числа в сроке нет: docxtpl оставляет от плейсхолдеров пустое место.
        assert clause.startswith("4.5. Оплата производится в течение (")
        assert "банковских дней" in clause
        assert "30 (тридцати)" not in clause
        assert PLACEHOLDER_RE.search(clause) is None


def test_point_time_is_read_from_time_window(generator, work_dir):
    """
    Время подачи ТС читается из time_window, если распознавание не разложило
    его на time_from / time_to.

    ContractData хранит точку только как {address, date, time_window}: поля
    time_from / time_to распознавания он отбрасывает. Сборщик формы кладёт
    окно одной строкой «08:00-18:00» — генератор обязан разобрать его сам
    (_parse_time_window), иначе в бланке останется «с  до».
    """
    data = _make_data("ООО")
    for point in data.loadings:
        assert "time_from" not in point and "time_to" not in point
        assert point["time_window"] == "08:00-18:00"

    with _generate(generator, data, work_dir, "e2e_time_window") as path:
        texts = _body_texts(Document(str(path)))

        assert ("3.2.1. Точка погрузки № 1 — г. Москва, ул. Складская, д. 1. "
                "Плановая дата и время подачи ТС: 21.09.2026 г., "
                "с 08:00 до 18:00.") in texts
        assert ("3.2.2. Точка погрузки № 2 — г. Калуга, ул. Промышленная, д. 5. "
                "Плановая дата и время подачи ТС: 22.09.2026 г., "
                "с 08:00 до 18:00.") in texts


def test_point_time_is_read_from_raw_time_from_and_time_to(generator, work_dir):
    """Время из исходных time_from / time_to важнее значения справочника."""
    payload = {
        "contract": _contract_payload("ООО"),
        "driver": _driver(),
        "vehicles": _vehicles(1),
        "tractor": {"brand_model": TRACTOR_BRAND, "plate_number": TRACTOR_PLATE,
                    "vehicle_type": TRACTOR_TYPE},
        "trailer": {"brand_model": TRAILER_BRAND, "plate_number": TRAILER_PLATE},
        "loadings": [{"address": "г. Москва, ул. Складская, д. 1",
                      "date": "2026-09-21", "time_from": "07:30",
                      "time_to": "12:45"}],
        "unloadings": _unloadings(),
    }

    with _generate(generator, payload, work_dir, "e2e_raw_time") as path:
        texts = _body_texts(Document(str(path)))

        assert ("3.2.1. Точка погрузки № 1 — г. Москва, ул. Складская, д. 1. "
                "Плановая дата и время подачи ТС: 21.09.2026 г., "
                "с 07:30 до 12:45.") in texts


def test_vehicles_route_points_reach_the_car_table(generator,
                                                   valid_data_of_variant,
                                                   work_dir):
    """Точки погрузки и выгрузки машин из справочника доходят до таблицы 3.1."""
    variant = valid_data_of_variant.contract["carrier_type"]

    with _generate(generator, valid_data_of_variant, work_dir,
                   f"e2e_vehicle_points_{variant}") as path:
        table = _car_table(Document(str(path)))

        assert table is not None, "таблица машин не найдена"
        for number in range(1, FILLED_CARS + 1):
            cells = [cell.text.strip() for cell in table.rows[number].cells]
            assert cells == [
                str(number),
                f"МОДЕЛЬ {number}",
                f"XTC651150N0001{number:03d}",
                "г. Москва, ул. Складская, д. 1",
                "г. Чехов, ул. Приёмная, д. 9",
            ]


# ─────────────────────────────────────────────────────────────
# Стык 2: валидатор → генератор
# ─────────────────────────────────────────────────────────────

def test_validator_does_not_change_data(validator, valid_data_of_variant):
    """Валидатор только читает: после проверки данные те же."""
    before = repr(valid_data_of_variant)
    contract_before = copy.deepcopy(valid_data_of_variant.contract)

    validator.check(valid_data_of_variant)

    assert repr(valid_data_of_variant) == before
    assert valid_data_of_variant.contract == contract_before
    assert len(valid_data_of_variant.vehicles) == FILLED_CARS
    assert len(valid_data_of_variant.loadings) == 2
    assert len(valid_data_of_variant.unloadings) == 1


def test_generator_survives_data_after_validator(generator, validator,
                                                 valid_data_of_variant,
                                                 work_dir):
    """Данные после валидатора генератор печатает без потерь."""
    variant = valid_data_of_variant.contract["carrier_type"]
    assert validator.check(valid_data_of_variant).errors == []

    with _generate(generator, valid_data_of_variant, work_dir,
                   f"e2e_after_validator_{variant}") as path:
        text = _document_text(Document(str(path)))

        assert PLACEHOLDER_RE.search(text) is None
        assert CONTRACT_NUMBER in text
        assert valid_data_of_variant.contract["lessee"]["full_name"] in text
        assert ROUTE in text


def test_data_with_missing_optional_fields_does_not_break_chain(
    generator, validator, work_dir
):
    """
    Необязательные поля данных не рвут цепочку: печатать можно.

    Убираем ровно то, чего в договоре может не быть: cargo_count (генератор
    считает машины по фактическому списку), фактические адреса сторон, ЭДО и
    время подачи ТС (окно подачи осталось пустым — «с  до»). Валидатор в этом
    случае даёт только замечания об адресах, а генератор заполняет
    освободившиеся места пустыми строками — ничего не выдумывая.
    """
    data = _make_data("ООО")
    data.contract.pop("cargo_count", None)
    for field in ("lessee", "lessor"):
        data.contract[field].pop("actual_address", None)
        data.contract[field].pop("edo", None)
    for point in data.loadings:
        point.pop("time_window", None)
    # Точки лежат и в contract (путь сборщика), и в ContractData: правим оба
    # списка, иначе генератор дочитает окно подачи из contract.
    data.contract["loadings"] = [dict(point) for point in data.loadings]

    report = validator.check(data)
    assert report.errors == [], f"неожиданные ошибки: {report.errors}"

    with _generate(generator, data, work_dir, "e2e_optional_fields") as path:
        doc = Document(str(path))
        texts = _body_texts(doc)
        body = _body_text(doc)
        text = _document_text(doc)

        # Машины посчитаны по фактическому списку, а не по полю cargo_count.
        assert f"Общее количество: {FILLED_CARS} шт." in body
        # Время подачи не выдумано: строка осталась без времени.
        assert ("3.2.1. Точка погрузки № 1 — г. Москва, ул. Складская, д. 1. "
                "Плановая дата и время подачи ТС: 21.09.2026 г., "
                "с до .") in body
        # ЭДО пуст — печатается пустая строка, а не выдуманный идентификатор.
        assert "ЭДО: " in text
        assert "2AE-7F31-4C50" not in text
        assert "3CD-8A42-5D60" not in text


def test_data_without_route_does_not_break_chain(generator, validator,
                                                 work_dir):
    """Маршрут не указан — валидатор говорит об этом ошибкой, генератор печатает."""
    data = _make_data("ООО")
    data.contract.pop("route")

    report = validator.check(data)
    assert "Не указан маршрут аренды" in report.errors

    with _generate(generator, data, work_dir, "e2e_no_route") as path:
        doc = Document(str(path))
        body = _body_text(doc)

        # Строка маршрута на месте, но пустая: генератор не выдумывает данные.
        assert "3.4. Согласованный маршрут: ." in body
        assert ROUTE not in body
        assert PLACEHOLDER_RE.search(_document_text(doc)) is None


def test_empty_points_do_not_break_chain(generator, validator, work_dir):
    """Точек маршрута нет — валидатор сообщает, а бланк печатается без них."""
    data = _make_data("ООО")
    data.contract["loadings"] = []
    data.contract["unloadings"] = []
    data.loadings = []
    data.unloadings = []

    report = validator.check(data)
    assert "Укажите хотя бы одну точку погрузки" in report.errors
    assert "Укажите хотя бы одну точку выгрузки" in report.errors

    with _generate(generator, data, work_dir, "e2e_no_points") as path:
        doc = Document(str(path))
        texts = _body_texts(doc)

        assert not _lines(doc, "3.2.")
        assert not _lines(doc, "3.3.")
        assert f"3.4. Согласованный маршрут: {ROUTE}." in texts


# ─────────────────────────────────────────────────────────────
# Стык 3: генератор → постобработка (проверки в файле на диске)
# ─────────────────────────────────────────────────────────────

def test_generated_docx_is_written_to_work_dir(generated_docs, work_dir):
    """
    Готовый DOCX лежит в изолированной папке внутри work_dir, а не в output/.

    Сам документ — на уровень глубже: внутри изолированной папки лежит папка
    рейса (ШАГ «Папка на рейс»).
    """
    assert generated_docs.parent.parent.parent == work_dir
    assert generated_docs.parent.parent.name.startswith("e2e_")
    assert generated_docs.parent.is_dir()
    assert generated_docs.exists()
    assert generated_docs.suffix == ".docx"
    assert generated_docs.stat().st_size > 10_000, "документ подозрительно пуст"
    assert generated_docs.name.startswith(
        f"{ArendaTsGenerator.FILE_PREFIX}_{CONTRACT_NUMBER}_"
    )


def test_generated_docx_has_no_placeholders(generated_docs):
    """В готовом документе не остаётся ни одного «{{…}}»."""
    doc = Document(str(generated_docs))
    text = _document_text(doc)

    assert "{{" not in text, "в документе остался плейсхолдер"
    assert "}}" not in text
    assert PLACEHOLDER_RE.search(text) is None


def test_car_table_has_only_filled_rows(generated_docs):
    """3 машины из 12: в таблице п. 3.1 ровно 3 строки данных плюс шапка."""
    doc = Document(str(generated_docs))
    table = _car_table(doc)

    assert table is not None, "в документе нет таблицы машин"
    assert len(table.rows) == FILLED_CARS + 1, (
        f"машин {FILLED_CARS}, а строк в таблице {len(table.rows)} "
        f"(в бланке {TEMPLATE_CARS} + шапка)"
    )
    assert _car_row_numbers(table) == [str(number)
                                       for number in range(1, FILLED_CARS + 1)]

    # Последняя строка бланка удалена вместе с остальными пустыми.
    text = _document_text(doc)
    assert "{{car_12_brand}}" not in text
    assert f"XTC651150N0001{FILLED_CARS:03d}" in text
    assert f"XTC651150N0001{FILLED_CARS + 1:03d}" not in text


def test_empty_point_blocks_are_removed(generated_docs):
    """2 точки погрузки и 1 выгрузки из 10: строки 3..10 удалены из документа."""
    doc = Document(str(generated_docs))
    texts = _body_texts(doc)

    loadings = [text for text in texts if LOADING_LINE_RE.match(text)]
    unloadings = [text for text in texts if UNLOADING_LINE_RE.match(text)]
    assert len(loadings) == 2, loadings
    assert len(unloadings) == 1, unloadings

    assert "3.2. Согласованные точки погрузки:" in texts
    assert "3.3. Согласованные точки выгрузки:" in texts
    for number in range(3, TEMPLATE_POINTS + 1):
        assert not [t for t in texts if t.startswith(f"3.2.{number}.")], (
            f"в документе остался пустой блок погрузки {number}"
        )
    for number in range(2, TEMPLATE_POINTS + 1):
        assert not [t for t in texts if t.startswith(f"3.3.{number}.")], (
            f"в документе остался пустой блок выгрузки {number}"
        )

    # Соседние разделы не задеты удалением блоков.
    assert f"3.4. Согласованный маршрут: {ROUTE}." in texts
    assert "3.5. Член экипажа Арендодателя (водитель):" in texts


def test_document_matches_the_replacements_map(generator,
                                               valid_data_of_variant, work_dir):
    """
    Готовый файл — это карта замен, донесённая до диска без потерь.

    Сверяются значения, которые легко теряются по дороге: срок аренды, точки
    маршрута, экипаж и арендная плата. Расхождение здесь означало бы, что
    подстановка или постобработка испортили уже правильно собранные значения.
    """
    variant = valid_data_of_variant.contract["carrier_type"]
    replacements = _repl(generator.build_replacements(valid_data_of_variant))

    with _generate(generator, valid_data_of_variant, work_dir,
                   f"e2e_map_{variant}") as path:
        doc = Document(str(path))
        texts = _body_texts(doc)
        body = _body_text(doc)
        text = _document_text(doc)

        # Срок аренды: обе даты — уже в формате документа (ДД.ММ.ГГГГ).
        assert (f"2.5. Плановый период аренды: с "
                f"{replacements['lease_start_date']} г. по "
                f"{replacements['lease_end_date']} г. включительно") in body

        for number in (1, 2):
            assert (f"3.2.{number}. Точка погрузки № {number} — "
                    f"{replacements[f'loading_{number}_address']}. "
                    f"Плановая дата и время подачи ТС: "
                    f"{replacements[f'loading_{number}_date']} г., с "
                    f"{replacements[f'loading_{number}_time_from']} до "
                    f"{replacements[f'loading_{number}_time_to']}.") in body
        assert (f"3.3.1. Точка выгрузки № 1 — "
                f"{replacements['unloading_1_address']}. Плановая дата "
                f"завершения: {replacements['unloading_1_date']} г.") in body

        # Планируемая дата завершения рейса (п. 3.3.2) — своё поле договора:
        # это НЕ конец срока аренды (п. 2.5). В «полных» данных точка выгрузки
        # одна, поэтому строки 3.3.2 в документе нет (пустой адрес удаляет
        # постобработка), а дата завершения рейса печатается отдельным тестом
        # ниже — там точек две.
        assert replacements["planned_completion_date"] == PLANNED_COMPLETION_TEXT
        assert replacements["planned_completion_date"] != replacements[
            "lease_end_date"
        ]
        assert not [t for t in texts if t.startswith("3.3.2.")]

        # Срок оплаты (п. 4.5) — цифрами и прописью.
        assert (f"Оплата производится в течение "
                f"{replacements['payment_days']} "
                f"({replacements['payment_days_words']}) банковских дней"
                ) in body

        assert f"ФИО: {replacements['driver_full_name']}" in body
        assert f"Паспорт: {replacements['driver_passport']}" in body
        assert (f"Водительское удостоверение: "
                f"{replacements['driver_license']}") in body
        assert f"Телефон: {replacements['driver_phone']}" in body

        # Арендная плата: в вариантах с НДС — три строки, в варианте без НДС —
        # одна; состав строк проверяется по фактическим ключам карты замен.
        assert f"{replacements['sum_total']} руб." in text
        if "sum_wo_vat" in replacements:
            assert (f"– {replacements['sum_wo_vat']} руб. "
                    f"({replacements['sum_wo_vat_words']}) — стоимость "
                    f"без НДС;") in body
            assert (f"– НДС {replacements['vat_rate']} — "
                    f"{replacements['sum_vat']} руб. "
                    f"({replacements['sum_vat_words']});") in body
            assert (f"Итого с НДС: {replacements['sum_total']} руб. "
                    f"({replacements['sum_total_words']}).") in body
        else:
            assert (f"{replacements['sum_total']} руб. "
                    f"({replacements['sum_total_words']}).") in body
            assert not [t for t in texts if t.startswith("– НДС")]
            assert not [t for t in texts if t.startswith("Итого с НДС:")]


def test_postprocess_pipeline_is_declared_by_generator(generator):
    """Конвейер постобработки — тот же, что выполняет generate()."""
    steps = generator.postprocess_steps(None)

    assert [type(step) for step in steps] == [
        ConvertNewlinesStep,
        RemoveEmptyVehicleRowsStep,
        RemoveEmptyLoadingUnloadingBlocksStep,
    ]


def test_hooks_of_factory_share_the_generator(generator):
    """Генератор фабрики — тот же класс и с теми же бланками."""
    built = GeneratorFactory.get_generator(
        "arenda_ts", strict=True, templates_dir=str(generator.templates_dir)
    )

    assert isinstance(built, ArendaTsGenerator)
    assert built.TEMPLATE_NAMES == TEMPLATE_NAMES
    assert set(built.templates) == set(VARIANTS)


def test_generation_logs_have_no_personal_data(caplog, generator,
                                               valid_ooo_data, work_dir):
    """Логи генерации — только количества и суммы, без ПДн."""
    with caplog.at_level(logging.DEBUG, logger="core.contract_generator"):
        with _generate(generator, valid_ooo_data, work_dir, "e2e_logs") as path:
            assert path.exists()

    messages = "\n".join(
        record.getMessage() for record in caplog.records
        if record.name == "core.contract_generator"
    )
    assert messages, "генерация ничего не записала в core.contract_generator"

    for fragment in ("Иванов", "Петров", "Сидоров", "Смирнов",
                     "18 22 926830", "99 36 123456",
                     "XTC651150N0001001", "МОДЕЛЬ 1",
                     "А001АА01", "Б002ББ02",
                     "Складская", "Приёмная", "Промышленная",
                     "Арендатор-Тест", "Арендодатель-Тест"):
        assert fragment not in messages, f"в логе есть «{fragment}»"

    assert f"машин в таблице 3.1 — {FILLED_CARS}" in messages
    assert "удалено пустых строк таблицы машин:" in messages
    assert "удалено пустых точек погрузки:" in messages
    assert "Договор аренды ТС с экипажем [с НДС]: в бланк подставлено" in messages


# ─────────────────────────────────────────────────────────────
# Стык 4: три варианта Арендатора — бланк, реквизиты и суммы
# ─────────────────────────────────────────────────────────────

def test_variant_selects_template(generator, valid_data_of_variant, work_dir):
    """
    Вид Арендатора выбирает бланк: три варианта — три файла.

    Здесь же проверяется, что бланк действительно отрендерился: get_template_path
    только выбирает файл, а расхождение имени с содержимым видно лишь в готовом
    документе.
    """
    variant = valid_data_of_variant.contract["carrier_type"]

    path = generator.get_template_path(valid_data_of_variant)

    assert path.endswith(TEMPLATE_NAMES[variant])
    assert generator._carrier_type_of(valid_data_of_variant) == variant

    with _generate(generator, valid_data_of_variant, work_dir,
                   f"e2e_template_{variant}") as generated:
        assert generated.stat().st_size > 10_000
        assert PLACEHOLDER_RE.search(
            _document_text(Document(str(generated)))
        ) is None


@pytest.mark.parametrize("variant", VARIANTS)
def test_generated_files_of_variants_differ(generator, work_dir, variant):
    """Файлы трёх вариантов — разные документы, а не копии одного бланка."""
    data = _make_data(variant)

    with _generate(generator, data, work_dir,
                   f"e2e_diff_{variant}") as path:
        doc = Document(str(path))
        text = _document_text(doc)
        texts = _body_texts(doc)

        if variant == "ИП без НДС":
            assert "НДС не облагается" in text
            assert not [t for t in texts if t.startswith("Итого с НДС:")]
        else:
            assert f"– {OOO_BASE_TEXT} руб." in text
            assert f"– НДС 22% — {OOO_VAT_TEXT} руб." in text
            assert f"Итого с НДС: {OOO_TOTAL_TEXT} руб." in text
            assert "НДС не облагается" not in text


@pytest.mark.parametrize("variant", ("ООО", "ИП с НДС"))
def test_vat_variants_have_three_sums(generator, work_dir, variant):
    """ООО и ИП с НДС: три суммы, каждая цифрами и прописью."""
    data = _make_data(variant)
    replacements = _repl(generator.build_replacements(data))

    assert replacements["sum_wo_vat"] == OOO_BASE_TEXT
    assert replacements["sum_vat"] == OOO_VAT_TEXT
    assert replacements["sum_total"] == OOO_TOTAL_TEXT
    assert replacements["vat_rate"] == "22%"

    with _generate(generator, data, work_dir, f"e2e_sums_{variant}") as path:
        doc = Document(str(path))
        texts = _body_texts(doc)
        body = _body_text(doc)

        # Прописью — ровно то, что положила карта замен.
        assert replacements["sum_wo_vat_words"] in body
        assert replacements["sum_vat_words"] in body
        assert replacements["sum_total_words"] in body

        # Три строки раздела 4.1: каждая со своей суммой и подписью.
        assert (f"– {OOO_BASE_TEXT} руб. "
                f"({replacements['sum_wo_vat_words']}) — стоимость "
                f"без НДС;") in body
        assert (f"– НДС 22% — {OOO_VAT_TEXT} руб. "
                f"({replacements['sum_vat_words']});") in body
        assert (f"Итого с НДС: {OOO_TOTAL_TEXT} руб. "
                f"({replacements['sum_total_words']}).") in body
        assert ("НДС не облагается" not in body), "в вариант с НДС попала УСН"


def test_ip_without_vat_has_single_sum(generator,
                                       valid_ip_without_vat_data, work_dir):
    """ИП без НДС: одна сумма, «НДС не облагается», сумм НДС в бланке нет."""
    replacements = _repl(generator.build_replacements(valid_ip_without_vat_data))

    assert replacements["sum_total"] == OOO_BASE_TEXT
    for name in ("sum_wo_vat", "sum_vat", "vat_rate",
                 "sum_wo_vat_words", "sum_vat_words"):
        assert name not in replacements, f"в вариант без НДС попал {name!r}"

    with _generate(generator, valid_ip_without_vat_data, work_dir,
                   "e2e_ip_without_vat") as path:
        doc = Document(str(path))
        texts = _body_texts(doc)
        body = _body_text(doc)
        text = _document_text(doc)

        assert "НДС не облагается (упрощённая система налогообложения)." in body
        # Единственная сумма документа — с прописью из карты замен.
        assert f"{OOO_BASE_TEXT} руб." in body
        assert replacements["sum_total_words"] in body
        assert not [t for t in texts if t.startswith("– НДС")]
        assert not [t for t in texts if t.startswith("Итого с НДС:")]
        assert "не является плательщиком НДС" in text


@pytest.mark.parametrize(
    "variant, filename",
    [
        ("ООО", "shablon_arenda_ts_ooo.docx"),
        ("ИП с НДС", "shablon_arenda_ts_ip_with_vat.docx"),
        ("ИП без НДС", "shablon_arenda_ts_ip_without_vat.docx"),
    ],
)
def test_ooo_and_ip_documents_differ(generator, work_dir, variant, filename):
    """
    Суммы одного варианта не встречаются в документе другого.

    Суммы 221 099,18 / 48 641,82 / 269 741,00 — принадлежность вариантов с
    НДС (ООО и ИП), суммы 135 833,00 / 29 883,26 / 165 716,26 — варианта
    «ИП без НДС». У ИП без НДС арендная плата одна и без НДС, поэтому ни одна
    из «налоговых» сумм в его документе появиться не должна; и наоборот,
    суммы без НДС не должны попасть в документы с НДС. Совпадение означало бы,
    что генератор посчитал арендную плату не по тем данным.
    """
    with _generate(generator, _make_data(variant), work_dir,
                   f"e2e_compare_{variant}") as path:
        assert path.name.endswith(".docx")
        doc = Document(str(path))
        text = _document_text(doc)
        texts = _body_texts(doc)

        if variant == "ИП без НДС":
            # Одна сумма, и это сумма без НДС: она же — база арендной платы
            # (в этом варианте sum_wo_vat = 0, а сумма документа лежит
            # в sum_total, см. _sums и ArendaTsGenerator._base_price).
            assert f"{OOO_BASE_TEXT} руб." in text
            for amount in (OOO_VAT_TEXT, IP_VAT_TEXT, IP_TOTAL_TEXT):
                assert amount not in text, f"в документ без НДС попала сумма {amount}"
            assert not [t for t in texts if t.startswith("– НДС")]
            assert not [t for t in texts if t.startswith("Итого с НДС:")]
        else:
            # Три суммы: без НДС, НДС по ставке и итого.
            assert OOO_BASE_TEXT in text
            assert OOO_VAT_TEXT in text
            assert OOO_TOTAL_TEXT in text
            for amount in (IP_SUM_TEXT, IP_VAT_TEXT, IP_TOTAL_TEXT):
                assert amount not in text, f"в документ с НДС попала сумма {amount}"

        # Бланк выбран по варианту, а не по умолчанию.
        assert ("НДС не облагается" in text) == (variant == "ИП без НДС")


def test_ooo_and_ip_ip_variants_differ_in_requisites(generator, work_dir):
    """ООО- и ИП-документы различаются и по реквизитам Арендатора."""
    with _generate(generator, _make_data("ООО"), work_dir,
                   "e2e_req_ooo") as ooo_path:
        ooo_text = _document_text(Document(str(ooo_path)))

    with _generate(generator, _make_data("ИП с НДС"), work_dir,
                   "e2e_req_ip") as ip_path:
        ip_text = _document_text(Document(str(ip_path)))

    assert f"КПП {LESSEE_OOO_KPP}" in ooo_text
    assert "КПП" not in ip_text
    assert LESSEE_OOO_NAME in ooo_text and LESSEE_OOO_NAME not in ip_text
    assert LESSEE_IP_NAME in ip_text and LESSEE_IP_NAME not in ooo_text
    assert f"ОГРНИП {LESSEE_IP_OGRNIP}" in ip_text
    assert LESSEE_OOO_SHORT_FIO in ooo_text
    assert LESSEE_IP_SHORT_FIO in ip_text


# ─────────────────────────────────────────────────────────────
# Стык 5: Приложение № 1 (Акт приема-передачи и возврата ТС)
# ─────────────────────────────────────────────────────────────

def test_appendix_is_present_in_every_variant(generated_docs):
    """Акт — часть того же файла во всех трёх вариантах."""
    doc = Document(str(generated_docs))
    texts = _body_texts(doc)
    text = _document_text(doc)

    assert "Приложение № 1" in text
    assert "АКТ ПРИЕМА-ПЕРЕДАЧИ И ВОЗВРАТА" in text
    assert "1. ПЕРЕДАЧА ТС В АРЕНДУ" in text
    assert "2. ВОЗВРАТ ТС" in text
    assert any(
        text.startswith("Настоящий Акт составлен во исполнение Договора")
        for text in texts
    )

    # Номер и дата договора в Акте — те же, что в шапке договора.
    assert (f"с экипажем № {CONTRACT_NUMBER} от {CONTRACT_DATE_TEXT} г."
            in text)


def test_appendix_transfer_table_is_filled(generated_docs):
    """Акт: тягач, прицеп и экипаж заполнены, поля для ручного ввода — пусты."""
    doc = Document(str(generated_docs))
    transfer = _act_values(doc, "Место передачи")

    assert transfer["Тягач"] == f"{TRACTOR_BRAND}, гос. номер {TRACTOR_PLATE}"
    assert transfer["Прицеп/полуприцеп"] == (
        f"{TRAILER_BRAND}, гос. номер {TRAILER_PLATE}"
    )
    assert transfer["Экипаж"] == DRIVER_NAME

    # Ячейки для заполнения при передаче ТС остались пустыми.
    for label in ("Место передачи", "Фактические дата и время передачи",
                  "Пробег на момент передачи", "Внешнее состояние / замечания"):
        assert transfer[label] == "", f"ячейка «{label}» заполнена не из данных"


def test_appendix_return_table_is_empty(generated_docs):
    """Акт: таблица возврата ТС целиком пуста — её заполняют от руки."""
    returning = _act_values(Document(str(generated_docs)), "Место возврата")

    assert set(returning.values()) == {""}, returning
    assert "Пробег на момент возврата" in returning
    assert "Состояние ТС / замечания" in returning


def test_appendix_signatures_use_party_names(generated_docs):
    """Подписи Акта: краткие наименования сторон и фамилии с инициалами."""
    doc = Document(str(generated_docs))
    table = _act_signature_table(doc)

    assert table is not None, "нет таблицы подписей Акта"
    lessee_cell, lessor_cell = table.rows[0].cells
    variant = generated_docs.parent.parent.name.removeprefix("e2e_")

    lessee_short = LESSEE_OOO_SHORT if variant == "ООО" else LESSEE_IP_SHORT
    lessee_fio = (LESSEE_OOO_SHORT_FIO if variant == "ООО"
                  else LESSEE_IP_SHORT_FIO)

    assert lessee_cell.text.split("\n") == [
        "АРЕНДАТОР:",
        lessee_short,
        f"______________/ {lessee_fio} /",
        "М.П.",
    ]
    assert lessor_cell.text.split("\n") == [
        "АРЕНДОДАТЕЛЬ:",
        LESSOR_SHORT,
        f"______________/ {LESSOR_SHORT_FIO} /",
        "М.П.",
    ]


# ─────────────────────────────────────────────────────────────
# Стык 6: «промпт → генератор» (TODO 3.1.D.B.1)
# ─────────────────────────────────────────────────────────────

def _recognized_payload(variant: str = "ООО") -> dict:
    """
    Данные в форме ответа распознавания (core/prompts/arenda_ts.py).

    Отличия от раскладки сборщика формы, которые здесь и проверяются:
      * lessee / lessor / route / lease_start_date / lease_end_date лежат
        В КОРНЕ ответа, а не внутри contract;
      * поля carrier_type промпт не отдаёт вовсе — вид Арендатора выводится из
        lessee["entity_type"] и ставки НДС;
      * у точки погрузки время лежит в time_from / time_to, а не в time_window.
    """
    payload = {
        "lessee": _lessee(variant),
        "lessor": _lessor(),
        "tractor": {"brand_model": TRACTOR_BRAND, "plate_number": TRACTOR_PLATE,
                    "vehicle_type": TRACTOR_TYPE},
        "trailer": {"brand_model": TRAILER_BRAND, "plate_number": TRAILER_PLATE},
        "lease_start_date": LEASE_START,
        "lease_end_date": LEASE_END,
        "route": ROUTE,
        "vehicles": _vehicles(),
        "loadings": [
            {"address": "г. Москва, ул. Складская, д. 1",
             "date": "2026-09-21", "time_from": "08:00", "time_to": "18:00"},
        ],
        "unloadings": _unloadings(),
        "driver": _driver(),
        "contract": {
            "number": CONTRACT_NUMBER,
            "date": CONTRACT_DATE,
            **_sums(variant),
        },
    }
    assert "carrier_type" not in payload["contract"], (
        "промпт не отдаёт carrier_type: поле заполняет интерфейс"
    )
    return payload


def test_coerce_drops_root_fields_of_recognition(generator):
    """
    TODO(3.1.D.B.1): ContractData.coerce корневые поля распознавания теряет.

    Схема промпта кладёт lessee / lessor / route / lease_start_date /
    lease_end_date в корень ответа, а ContractData.coerce переносит в contract
    только loadings / unloadings (core/contract_data.py на этом шаге не
    трогаем). Поэтому у приведённых данных блоков сторон в contract нет —
    а генератор читает их оттуда.

    Тест ловит регресс: как только сборщик UI начнёт раскладывать данные
    правильно (3.1.D.B.1), ожидание придётся заменить.
    """
    payload = _recognized_payload()

    coerced = ContractData.coerce(payload)

    assert coerced.contract["number"] == CONTRACT_NUMBER
    for field in ("lessee", "lessor", "route",
                  "lease_start_date", "lease_end_date"):
        assert field not in coerced.contract, (
            f"поле {field!r} неожиданно дожило до ContractData.contract"
        )
    # Точки маршрута — исключение: их coerce переносит в contract сам.
    assert len(coerced.loadings) == 1
    assert coerced.loadings[0]["address"] == "г. Москва, ул. Складская, д. 1"


def test_direct_replacements_map_reads_root_fields_of_recognition(generator):
    """
    Прямой вызов карты замен на «сырых» данных распознавания работает.

    _party_block и _root_value умеют читать блоки сторон, маршрут и срок
    аренды ещё и из корня исходного словаря — это страховка того же стыка:
    без неё пустой ContractData дал бы пустой бланк.
    """
    payload = _recognized_payload()

    replacements = _repl(generator.build_replacements(payload))

    assert replacements["lessee_full_name"] == LESSEE_OOO_NAME
    assert replacements["lessor_full_name"] == LESSOR_NAME
    assert replacements["route"] == ROUTE
    assert replacements["lease_start_date"] == LEASE_START_TEXT
    assert replacements["lease_end_date"] == LEASE_END_TEXT
    assert replacements["loading_1_time_from"] == "08:00"
    assert replacements["loading_1_time_to"] == "18:00"
    # Суммы распознавания генератор читает из своего _base_price.
    assert replacements["sum_wo_vat"] == OOO_BASE_TEXT
    assert replacements["sum_total"] == OOO_TOTAL_TEXT


def test_generate_hoists_root_fields_of_recognition(generator, work_dir):
    """
    generate() поднимает корневые поля распознавания в contract.

    Промпт отдаёт lessee / lessor / route и срок аренды в корне ответа —
    ArendaTsGenerator._hoist_contract_fields переносит их в contract, и без
    этого сборщика генератор получил бы пустой ContractData и напечатал пустой
    бланк. Здесь проверяется результат на файле: реквизиты, маршрут и срок
    аренды в документе.
    """
    payload = _recognized_payload()

    with _generate(generator, payload, work_dir, "e2e_prompt_path") as path:
        doc = Document(str(path))
        text = _document_text(doc)
        texts = _body_texts(doc)

        assert LESSEE_OOO_NAME in text
        assert LESSOR_NAME in text
        assert f"3.4. Согласованный маршрут: {ROUTE}." in texts
        assert any(
            text.startswith(
                f"2.5. Плановый период аренды: с {LEASE_START_TEXT} г. "
                f"по {LEASE_END_TEXT} г. включительно"
            )
            for text in texts
        )
        assert PLACEHOLDER_RE.search(text) is None
        # Время подачи ТС взято из time_from / time_to распознавания.
        assert ("3.2.1. Точка погрузки № 1 — г. Москва, ул. Складская, д. 1. "
                "Плановая дата и время подачи ТС: 21.09.2026 г., "
                "с 08:00 до 18:00.") in texts


def test_recognized_entity_type_selects_variant(generator, work_dir):
    """Без carrier_type вид Арендатора выводится из lessee.entity_type и ставки."""
    cases = [
        ("ООО", "shablon_arenda_ts_ooo.docx", "22%"),
        ("ИП с НДС", "shablon_arenda_ts_ip_with_vat.docx", "22%"),
        ("ИП без НДС", "shablon_arenda_ts_ip_without_vat.docx", "0%"),
    ]

    for variant, filename, vat_rate in cases:
        payload = _recognized_payload(variant)
        # get_template_path принимает ContractData — приводим так же, как это
        # делает generate() (через ContractData.coerce).
        coerced = ContractData.coerce(
            ArendaTsGenerator._hoist_contract_fields(payload)
        )

        assert generator.get_template_path(coerced).endswith(filename), variant
        replacements = _repl(generator.build_replacements(payload))
        if vat_rate == "0%":
            assert replacements["sum_total"] == OOO_BASE_TEXT
            assert "vat_rate" not in replacements
        else:
            assert replacements["vat_rate"] == "22%"

    # И то же самое видно в готовом файле: файл ИП без НДС печатает оговорку.
    with _generate(generator, _recognized_payload("ИП без НДС"), work_dir,
                   "e2e_prompt_ip_without_vat") as path:
        text = _document_text(Document(str(path)))

        assert "НДС не облагается" in text
        assert "Итого с НДС:" not in text


def test_recognized_sums_are_read_by_base_price(generator,
                                               valid_ip_without_vat_data):
    """
    Суммы из contract.sum_wo_vat / sum_total генератор читает сам.

    Стык имён сумм на этом шаге РАБОТАЕТ: промпт кладёт стоимость в sum_wo_vat
    (вариант с НДС) и в sum_total (ИП без НДС), а генератор берёт базу из этих
    же ключей (_base_price). Падение теста означало бы, что арендная плата
    снова читается откуда-то ещё.
    """
    payload = _recognized_payload("ООО")
    assert payload["contract"]["sum_wo_vat"] == OOO_BASE_SUM

    ooo = _repl(generator.build_replacements(payload))
    assert ooo["sum_wo_vat"] == OOO_BASE_TEXT
    assert ooo["sum_total"] == OOO_TOTAL_TEXT

    # Вариант без НДС: единственная сумма документа лежит в sum_total,
    # а sum_wo_vat остаётся нулём.
    ip_payload = _recognized_payload("ИП без НДС")
    assert ip_payload["contract"]["sum_total"] == OOO_BASE_SUM
    assert ip_payload["contract"]["sum_wo_vat"] == 0.0

    ip = _repl(generator.build_replacements(ip_payload))
    assert ip["sum_total"] == OOO_BASE_TEXT

    # Та же сумма пришла и из данных формы (price_without_vat-путь вкладки).
    form = _repl(generator.build_replacements(valid_ip_without_vat_data))
    assert form["sum_total"] == ip["sum_total"]


def test_recognized_sum_total_is_read_as_the_total(generator, caplog, work_dir):
    """
    Стык «распознавание → генератор»: sum_total — это ИТОГ договора.

    Единое правило «НДС в том числе»: главная величина — итог, база без НДС
    вынимается из него (core/vat.py). Раньше генератор читал только базу
    (sum_wo_vat / price_without_vat), и документ с одной суммой «Итого с НДС»
    печатал 0,00 с предупреждением в лог.
    """
    payload = _recognized_payload("ООО")
    payload["contract"].pop("sum_wo_vat")
    assert "price_without_vat" not in payload["contract"]

    with caplog.at_level(logging.WARNING, logger="core.contract_generator"):
        replacements = _repl(generator.build_replacements(payload))

    # 269 741,00 при 22% → 221 099,18 без НДС и 48 641,82 налога.
    assert replacements["sum_wo_vat"] == OOO_BASE_TEXT
    assert replacements["sum_vat"] == OOO_VAT_TEXT
    assert replacements["sum_total"] == OOO_TOTAL_TEXT
    assert "в бланк уйдёт 0,00" not in caplog.text

    with _generate(generator, payload, work_dir, "e2e_sum_total_as_total") as path:
        text = _document_text(Document(str(path)))

        assert f"– {OOO_BASE_TEXT} руб." in text
        assert f"Итого с НДС: {OOO_TOTAL_TEXT} руб." in text
        assert "– 0,00 руб." not in text


# ─────────────────────────────────────────────────────────────
# Изоляция: изолированные данные, папка и удаление файлов
# ─────────────────────────────────────────────────────────────

def test_generate_does_not_touch_the_source_data(generator, work_dir):
    """generate() не мутирует данные: работает с копией (ContractData неизменна)."""
    data = _make_data("ООО")
    before = copy.deepcopy(data.contract)
    payload_before = copy.deepcopy(data.to_generator_dict())

    with _generate(generator, data, work_dir, "e2e_no_mutation") as path:
        assert path.exists()

    assert data.contract == before
    assert data.to_generator_dict() == payload_before


def test_generated_files_are_removed_after_test(generator, work_dir):
    """
    Файлы теста лежат в своей папке work_dir и удаляются за тестом.

    Вместе с документом убирается и папка рейса: иначе следующая проверка
    «папка вывода пуста» спотыкалась бы о пустой каталог от прошлого прогона.
    """
    output_dir = work_dir / "e2e_cleanup_probe"

    with _generate(generator, _make_data("ООО"), work_dir,
                   "e2e_cleanup_probe") as path:
        created = path
        assert created.exists()
        assert created.parent.parent == output_dir
        assert created.parent.parent.parent == work_dir

    assert not created.exists(), "готовый документ остался после теста"
    assert not list(output_dir.rglob("*.docx"))
    assert [item.name for item in output_dir.iterdir()] == []
