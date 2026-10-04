#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сводные (интеграционные) тесты бэкенда заявки «Логистикс Рус» (ЭТАП 3.1.C.A.6).

Проверяется вся цепочка целиком — ContractData → валидатор → генератор →
постобработка → готовый DOCX, — а не отдельные модули (те у себя уже покрыты:
tests/test_logistiks_rus_validator.py, tests/test_logistiks_rus_generator.py,
tests/test_logistiks_rus_postprocess.py, tests/test_prompts_logistiks_rus.py).
Цель этих тестов — стыки: там, где модули договариваются об именах полей и о
форме данных, расхождение не видно ни одному модульному тесту.

Стыки, которые проверяются здесь:

  1. «данные формы → генератор»: поля собраны ровно так, как их кладёт
     сборщик вкладок окна (образец раскладки —
     ui/windows/formika/data.py: вкладка «Точки маршрута» кладёт точки
     ВНУТРЬ contract.loadings / contract.unloadings, вкладка «Стоимость» —
     price_without_vat / vat_rate_num). Генератор читает ровно эти имена.
  2. «промпт → генератор»: имена сумм. Промпт
     (core/prompts/logistiks_rus.py) кладёт contract.sum_wo_vat / sum_vat /
     sum_total, а генератор читает price_without_vat / price_input. Стык
     СЕЙЧАС НЕ РАБОТАЕТ: см. test_recognition_names_are_not_read_by_generator
     — он фиксирует текущее поведение и ловит регресс до 3.1.C.B.1. Пока
     стык не починен, генератор сообщает о нём WARNING'ом — см.
     test_logs_warn_when_recognized_sums_are_not_mapped.
  3. «валидатор → генератор»: валидатор не портит данные, генератор не падает
     на проверенных данных и печатает те же значения.
  4. «генератор → постобработка»: пустые строки таблицы машин и пустые блоки
     грузоотправителей/грузополучателей действительно исчезают из ГОТОВОГО
     файла (а не только из программно собранного Document()).
  5. «ООО ↔ ИП»: смена carrier_type меняет бланк, раздел 5 «Стоимость» и
     экспедитора в подписях.

Все данные синтетические, реальных ПДн нет. Выходные файлы создаются в
work_dir (tests/_tmp) и удаляются в finally.
"""

import copy
import gc
import logging
import re
from pathlib import Path

import pytest
from docx import Document

from core.contract_data import ContractData
from core.contracts.logistiks_rus.generator import LogistiksRusGenerator
from core.contracts.logistiks_rus.validator import LogistiksRusValidator
from core.contracts.registry import ContractTypeRegistry

# ─────────────────────────────────────────────────────────────
# Константы тестовых данных
# ─────────────────────────────────────────────────────────────

CUSTOMER_NAME = "ООО «ДжейСиСиТиЭс Интернейшнл Логистикс Рус»"

#: Экспедитор печатается шаблоном: у ООО и ИП он свой.
OOO_EXPEDITER = "ООО «ТЕХНОЛОГИСТИКА»"
IP_EXPEDITER_FULL = "ИП Хейгетян Елена Валентиновна"
IP_EXPEDITER_SHORT = "Е.В.Хейгетян"

#: Суммы ООО-варианта: 221 099,18 + 22% = 48 641,82 → 269 741,00.
OOO_PRICE_WITHOUT_VAT = 221099.18
OOO_SUM_WO_VAT_TEXT = "221\u00a0099,18"
OOO_SUM_VAT_TEXT = "48\u00a0641,82"
OOO_SUM_TOTAL_TEXT = "269\u00a0741,00"

#: Сумма ИП-варианта: одна строка «Без НДС», ставка 0.
IP_PRICE_WITHOUT_VAT = 135833.00
IP_SUM_TOTAL_TEXT = "135\u00a0833,00"

#: Остальные суммы распознавания: их кладёт промпт (см. стык 2).
RECOGNIZED_SUM_WO_VAT = 221099.18
RECOGNIZED_SUM_VAT = 48641.82
RECOGNIZED_SUM_TOTAL = 269741.00

#: Сколько машин и точек помещается в бланк.
TEMPLATE_CARS = 12
TEMPLATE_POINTS = 10

#: Сколько машин и точек в «полных» данных: 3 из 12 и 2 из 10 — так видно,
#: что постобработка действительно удаляет пустые строки и блоки.
FILLED_CARS = 3
FILLED_SHIPPERS = 2
FILLED_CONSIGNEES = 1

CARGO_HEADERS = ("№", "Марка, модель", "VIN-номер")

PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}")

#: Варианты бланка: ключ — carrier_type, значение — файл шаблона.
CARRIER_TYPES = {
    "ООО": "shablon_logistiks_rus_ooo.docx",
    "ИП": "shablon_logistiks_rus_ip.docx",
}


#: Валидатор разрешает печатать заявку, но одно замечание на полных данных
#: формы есть: заказчика заявки вкладка отдаёт одним наименованием, а короткое
#: имя (customer.short_name) не заполняет — см. test_ooo_data_passes_validator.
EXPECTED_CUSTOMER_WARNING = "Не заполнено краткое наименование заказчика"


@pytest.fixture(autouse=True)
def _release_documents():
    """Word-файлы на Windows освобождаем до удаления временных копий."""
    yield
    gc.collect()


@pytest.fixture(scope="module", autouse=True)
def _builtin_types_loaded():
    """Регистрация типов: без неё фабрика не знает про logistiks_rus."""
    ContractTypeRegistry.load_builtin()


@pytest.fixture
def generator(templates_dir) -> LogistiksRusGenerator:
    return LogistiksRusGenerator(templates_dir=str(templates_dir))


@pytest.fixture
def validator() -> LogistiksRusValidator:
    return LogistiksRusValidator()


# ─────────────────────────────────────────────────────────────
# Тестовые данные
# ─────────────────────────────────────────────────────────────

def _vehicle(number: int) -> dict:
    """Синтетическая перевозимая машина: VIN ровно 17 символов (ISO 3779)."""
    return {
        "vin": f"XTC651150N0001{number:03d}",
        "brand_model": f"МОДЕЛЬ {number}",
        "vehicle_type": "Легковой автомобиль",
    }


def _point(kind: str, number: int) -> dict:
    """Точка маршрута: грузоотправитель (1. ПОГРУЗКА) или грузополучатель."""
    label = "Грузоотправитель" if kind == "shipper" else "Грузополучатель"
    return {
        "name": f"ООО «{label} {number}»",
        "address": f"г. Тестоград, ул. Складская, д. {number}",
        "date": "2026-09-26" if kind == "shipper" else "2026-10-01",
        "time_window": "08:00-20:00",
    }


def _contract(carrier_type: str, price_without_vat: float, vat_rate_num: int,
              cars: int, shippers: int, consignees: int) -> dict:
    """
    Условия заявки — ровно те имена полей, что кладёт сборщик формы.

    Точки маршрута лежат внутри contract (loadings / unloadings): только этот
    путь сохраняет название грузоотправителя — при приведении данных через
    ContractData поле name у точек отбрасывается
    (core.contract_data._as_point_list), см. докстринг
    core/contracts/logistiks_rus/generator.py. Так же раскладывает данные и
    сборщик вкладок окна «Логистикс Рус».

    cargo_count печатается в бланке как есть и поэтому всегда согласован с
    числом машин: иначе валидатор справедливо выдаст замечание.
    """
    return {
        "number": "ЛР-2026-17",
        "date": "2026-09-24",
        "carrier_type": carrier_type,
        "cargo_count": cars,
        "price_without_vat": price_without_vat,
        "vat_rate_num": vat_rate_num,
        "special_conditions": "Погрузка круглосуточно, простой не более 24 часов.",
        "loading_date": "2026-09-26",
        "loading_time_from": "08:00",
        "loading_time_to": "20:00",
        "unloading_date": "2026-10-01",
        "unloading_time_from": "08:00",
        "unloading_time_to": "20:00",
        "loadings": [_point("shipper", n) for n in range(1, shippers + 1)],
        "unloadings": [_point("consignee", n) for n in range(1, consignees + 1)],
    }


def _make_data(carrier_type: str, price_without_vat: float, vat_rate_num: int,
               cars: int = FILLED_CARS, shippers: int = FILLED_SHIPPERS,
               consignees: int = FILLED_CONSIGNEES) -> ContractData:
    """Полный ContractData заявки: водитель, заказчик, машины, автовоз, условия."""
    return ContractData(
        driver={"full_name": "Иванов Иван Иванович"},
        customer={"full_name": CUSTOMER_NAME},
        # Тягач и полуприцеп лежат в справочнике машин, но грузом не являются:
        # у них другой vehicle_type, и в таблицу автомобилей они не попадают.
        vehicles=[_vehicle(number) for number in range(1, cars + 1)] + [
            {"brand_model": "DAF XF 95.430", "plate_number": "М342СА761",
             "vehicle_type": "Тягач"},
            {"brand_model": "KRONE SD", "plate_number": "ВК123478",
             "vehicle_type": "Полуприцеп"},
        ],
        tractor={"brand_model": "DAF XF 95.430", "plate_number": "М342СА761"},
        trailer={"brand_model": "KRONE SD", "plate_number": "ВК123478"},
        contract=_contract(carrier_type, price_without_vat, vat_rate_num,
                           cars, shippers, consignees),
    )


@pytest.fixture
def valid_ooo_data() -> ContractData:
    """Заполненные данные заявки для экспедитора-ООО (НДС 22%)."""
    return _make_data("ООО", OOO_PRICE_WITHOUT_VAT, 22)


@pytest.fixture
def valid_ip_data() -> ContractData:
    """Заполненные данные заявки для экспедитора-ИП (без НДС)."""
    return _make_data("ИП", IP_PRICE_WITHOUT_VAT, 0)


# ─────────────────────────────────────────────────────────────
# Утилиты тестов
# ─────────────────────────────────────────────────────────────

def _document_text(doc) -> str:
    """Весь текст документа: абзацы и таблицы (включая вложенные)."""
    parts = [p.text for p in doc.paragraphs]

    def table_text(table):
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
                for nested in cell.tables:
                    table_text(nested)

    for table in doc.tables:
        table_text(table)

    return "\n".join(parts)


def _body_texts(doc):
    """Тексты абзацев верхнего уровня без крайних пробелов."""
    return [p.text.strip() for p in doc.paragraphs]


def _cargo_table(doc):
    """Таблица автомобилей — по заголовкам колонок, а не по индексу."""
    for table in doc.tables:
        if not table.rows:
            continue
        headers = tuple(cell.text.strip() for cell in table.rows[0].cells)
        if headers == CARGO_HEADERS:
            return table
    return None


def _generate(generator, data, work_dir, name: str) -> Path:
    """
    Генерирует заявку в отдельную папку work_dir и возвращает путь к DOCX.

    Папка у каждого теста своя: файлы одного теста не мешают другому, а
    готовый документ не уходит в общий output/ проекта.
    """
    output_dir = work_dir / name
    output_dir.mkdir(parents=True, exist_ok=True)

    path = Path(generator.generate(data, output_dir=str(output_dir)))
    assert path.exists(), f"файл не создан: {path}"
    assert path.parent == output_dir, "файл ушёл мимо изолированной папки"
    return path


@pytest.fixture
def generated_ooo(generator, valid_ooo_data, work_dir):
    """Готовый DOCX ООО-варианта; файл удаляется после теста."""
    path = _generate(generator, valid_ooo_data, work_dir, "e2e_ooo")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


@pytest.fixture
def generated_ip(generator, valid_ip_data, work_dir):
    """Готовый DOCX ИП-варианта; файл удаляется после теста."""
    path = _generate(generator, valid_ip_data, work_dir, "e2e_ip")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────
# Стык 1: данные формы → валидатор и генератор
# ─────────────────────────────────────────────────────────────

def test_ooo_data_passes_validator(validator, valid_ooo_data):
    """
    Полные данные ООО проходят валидатор: ошибок нет.

    Замечание одно и оно ожидаемое: заказчик заявки в бланке печатается одним
    наименованием ({{customer_name}}), поэтому короткое имя в данные не
    кладётся — валидатор честно отмечает его отсутствие замечанием, а не
    ошибкой. Печатать заявку это не мешает.
    """
    report = validator.check(valid_ooo_data)

    assert report.errors == [], f"неожиданные ошибки: {report.errors}"
    assert report.warnings == [EXPECTED_CUSTOMER_WARNING], (
        f"неожиданные замечания: {report.warnings}"
    )


def test_ip_data_passes_validator(validator, valid_ip_data):
    """Полные данные ИП проходят валидатор без ошибок (замечание то же)."""
    report = validator.check(valid_ip_data)

    assert report.errors == [], f"неожиданные ошибки: {report.errors}"
    assert report.warnings == [EXPECTED_CUSTOMER_WARNING], (
        f"неожиданные замечания: {report.warnings}"
    )


def test_generate_validate_hook_accepts_valid_data(generator, valid_ooo_data):
    """Хук validate() самого генератора согласован с данными формы."""
    report = generator.validate(valid_ooo_data)

    assert report.errors == [], f"неожиданные ошибки: {report.errors}"


def test_ooo_generates_docx(generated_ooo):
    """ООО: generate() возвращает путь к существующему непустому DOCX."""
    assert generated_ooo.exists()
    assert generated_ooo.suffix == ".docx"
    assert generated_ooo.stat().st_size > 10_000, "готовый документ подозрительно пуст"

    doc = Document(str(generated_ooo))
    assert _cargo_table(doc) is not None, "в документе нет таблицы автомобилей"


def test_ip_generates_docx(generated_ip):
    """ИП: generate() возвращает путь к существующему непустому DOCX."""
    assert generated_ip.exists()
    assert generated_ip.suffix == ".docx"
    assert generated_ip.stat().st_size > 10_000

    doc = Document(str(generated_ip))
    assert _cargo_table(doc) is not None


def test_generated_docx_has_no_placeholders(generated_ooo, generated_ip):
    """В готовых документах обоих вариантов не остаётся {{...}}."""
    for path in (generated_ooo, generated_ip):
        text = _document_text(Document(str(path)))

        assert "{{" not in text, f"в {path.name} остался плейсхолдер"
        assert "}}" not in text
        assert PLACEHOLDER_RE.search(text) is None


def test_no_pd_in_logs(caplog, generator, valid_ooo_data, valid_ip_data, work_dir):
    """
    Логи генерации не содержат персональных данных.

    В лог попадают только имена полей, количества и суммы (суммы — это данные
    самого договора, они видны в готовом документе, см.
    test_logs_contain_amounts_are_substituted); ФИО, адреса, VIN, названия
    сторон и марок машин — нет. Проверяются оба варианта сразу: строка
    стоимости у них одна и та же по форме, различается только составом сумм.
    """
    with caplog.at_level(logging.INFO, logger="core.contract_generator"):
        _generate(generator, valid_ooo_data, work_dir, "e2e_logs_ooo")
        _generate(generator, valid_ip_data, work_dir, "e2e_logs_ip")

    messages = "\n".join(
        record.getMessage() for record in caplog.records
        if record.name == "core.contract_generator"
    )
    assert messages, "генерация ничего не записала в core.contract_generator"

    # Данные, которых в логе быть не должно.
    for fragment in (
        "ДжейСиСиТиЭс",          # заказчик
        "ТЕХНОЛОГИСТИКА",        # экспедитор
        "Иванов",                # ФИО водителя
        "Тестоград",             # адрес точки
        "Складская",
        "XTC651150N0001",        # VIN
        "МОДЕЛЬ",                # марка машины
        "DAF",                   # тягач
        "KRONE",                 # прицеп
    ):
        assert fragment not in messages, f"в логе есть «{fragment}»"

    # Номер заявки в логе допустим: это реквизит документа, а не персональные
    # данные, и по нему строится имя готового файла (generate() печатает путь).
    assert "ЛР-2026-17" in messages

    # Имена полей, количества и суммы — можно.
    assert "машин в заявке — 3" in messages
    assert "удалено пустых строк таблицы груза: 9" in messages
    assert "Логистикс Рус [ООО]: в бланк подставлено" in messages
    assert "Логистикс Рус [ИП]: в бланк подставлено" in messages


def test_logs_contain_amounts_are_substituted(caplog, generator,
                                              valid_ooo_data, valid_ip_data,
                                              work_dir):
    """
    INFO про стоимость — ровно одна строка, и в ней фактические значения.

    В лог идут те же строки, что ушли в шаблон: те же разряды неразрывным
    пробелом и запятая перед копейками (сверяется с картой замен, а не
    с пересчитанными числами). У ИП плейсхолдеров sum_wo_vat / sum_vat /
    vat_rate в бланке нет — там «—», и это тоже видно в логе.
    """
    with caplog.at_level(logging.INFO, logger="core.contract_generator"):
        _generate(generator, valid_ooo_data, work_dir, "e2e_logs_amounts_ooo")
        _generate(generator, valid_ip_data, work_dir, "e2e_logs_amounts_ip")

    cost_lines = [
        record.getMessage() for record in caplog.records
        if record.name == "core.contract_generator"
        and "в бланк подставлено" in record.getMessage()
    ]

    # Одна строка на генерацию, а не несколько разрозненных.
    assert len(cost_lines) == 2, cost_lines

    ooo_line = next(line for line in cost_lines if "[ООО]" in line)
    ip_line = next(line for line in cost_lines if "[ИП]" in line)

    # Формат строки целиком: суммы ООО совпадают с тем, что печатает бланк
    # (см. test_ooo_docx_has_vat_lines), ставка — «22%».
    assert ooo_line == (
        "Логистикс Рус [ООО]: в бланк подставлено — "
        f"без НДС={OOO_SUM_WO_VAT_TEXT}, НДС={OOO_SUM_VAT_TEXT}, "
        f"итого={OOO_SUM_TOTAL_TEXT}, ставка=22%"
    )
    assert ip_line == (
        "Логистикс Рус [ИП]: в бланк подставлено — "
        f"без НДС=—, НДС=—, итого={IP_SUM_TOTAL_TEXT}, ставка=—"
    )

    # И то же самое сверяется с картой замен: логируется подставленное,
    # а не вычисленное заново.
    ooo_replacements = generator.build_replacements(valid_ooo_data)
    assert f"без НДС={ooo_replacements['sum_wo_vat']}" in ooo_line
    assert f"НДС={ooo_replacements['sum_vat']}" in ooo_line
    assert f"итого={ooo_replacements['sum_total']}" in ooo_line
    assert f"ставка={ooo_replacements['vat_rate']}" in ooo_line

    ip_replacements = generator.build_replacements(valid_ip_data)
    assert f"итого={ip_replacements['sum_total']}" in ip_line
    for absent in ("sum_wo_vat", "sum_vat", "vat_rate"):
        assert absent not in ip_replacements, f"в ИП-вариант положен {absent!r}"


def test_logs_warn_when_recognized_sums_are_not_mapped(caplog, generator,
                                                       work_dir):
    """
    Распознанная стоимость не замаплена — об этом есть WARNING.

    Суммы распознавания лежат в contract.sum_total, а генератор читает
    price_without_vat / price_input (TODO 3.1.C.B.1), поэтому в бланк уйдёт
    0,00. Лог обязан сказать это явно: иначе расхождение видно только
    в готовом документе — см. test_recognition_names_are_not_read_by_generator.
    """
    data = ContractData(contract={
        "number": "ЛР-2026-19",
        "date": "2026-09-24",
        "carrier_type": "ООО",
        "sum_total": RECOGNIZED_SUM_TOTAL,
        "vat_rate": "22%",
    })
    assert "price_without_vat" not in data.contract
    assert "price_input" not in data.contract

    with caplog.at_level(logging.WARNING, logger="core.contract_generator"):
        path = _generate(generator, data, work_dir, "e2e_logs_unmapped_sums")

    try:
        warnings = [
            record.getMessage() for record in caplog.records
            if record.name == "core.contract_generator"
            and record.levelno == logging.WARNING
        ]
        assert any("в бланк уйдёт 0,00" in text for text in warnings), warnings
        assert any(
            "price_without_vat / price_input отсутствуют" in text
            for text in warnings
        ), warnings
        assert any("Логистикс Рус [ООО]" in text for text in warnings), warnings
        assert any("TODO 3.1.C.B.1" in text for text in warnings), warnings

        # Предупреждение не врёт: в бланке действительно 0,00.
        assert "Стоимость услуг: 0,00 руб." in _document_text(Document(str(path)))
    finally:
        path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────
# ООО-специфичные проверки готового документа
# ─────────────────────────────────────────────────────────────

def test_ooo_docx_has_vat_lines(generated_ooo):
    """ООО: в разделе 5 три суммы — без НДС, НДС по ставке и итого."""
    texts = _body_texts(Document(str(generated_ooo)))

    assert any(line.startswith("Стоимость услуг:") for line in texts)
    assert f"Стоимость услуг: {OOO_SUM_WO_VAT_TEXT} руб." in "\n".join(texts)
    assert f"НДС 22%: {OOO_SUM_VAT_TEXT} руб." in "\n".join(texts)
    assert f"Итого: {OOO_SUM_TOTAL_TEXT} руб." in "\n".join(texts)


def test_ooo_docx_has_technology_name(generated_ooo):
    """ООО: экспедитора печатает бланк — в подписях ООО «ТЕХНОЛОГИСТИКА»."""
    text = _document_text(Document(str(generated_ooo)))

    assert OOO_EXPEDITER in text
    assert f"Экспедитор: {OOO_EXPEDITER}" in text
    assert IP_EXPEDITER_FULL not in text, "в ООО-бланк попал ИП-экспедитор"


# ─────────────────────────────────────────────────────────────
# ИП-специфичные проверки готового документа
# ─────────────────────────────────────────────────────────────

def test_ip_docx_has_single_sum_line(generated_ip):
    """ИП: одна сумма «Без НДС», отдельных строк НДС и «Итого» нет."""
    texts = _body_texts(Document(str(generated_ip)))

    assert f"Стоимость услуг: {IP_SUM_TOTAL_TEXT} руб." in "\n".join(texts)
    assert "Без НДС" in "\n".join(texts)
    assert not [t for t in texts if t.startswith("НДС ")], "у ИП появилась строка НДС"
    assert not [t for t in texts if t.startswith("Итого:")], "у ИП появилась строка «Итого»"


def test_ip_docx_has_hejgetyan_name(generated_ip):
    """ИП: экспедитор в шапке и в подписях — ИП Хейгетян Е.В."""
    text = _document_text(Document(str(generated_ip)))

    assert IP_EXPEDITER_FULL in text
    assert IP_EXPEDITER_SHORT in text
    assert OOO_EXPEDITER not in text, "в ИП-бланк попал ООО-экспедитор"


def test_ip_docx_has_no_vat_line(generated_ip):
    """ИП: строки «НДС 22%» в документе нет — стоимость без НДС."""
    text = _document_text(Document(str(generated_ip)))

    assert "НДС 22%" not in text
    assert "НДС 22 %" not in text


# ─────────────────────────────────────────────────────────────
# Стык 2: генератор → постобработка (в готовом документе)
# ─────────────────────────────────────────────────────────────

def test_empty_vehicle_rows_removed(generator, work_dir, valid_ooo_data):
    """3 машины из 12: в таблице автомобилей 3 строки данных, а не 12."""
    path = _generate(generator, valid_ooo_data, work_dir, "e2e_empty_cars")
    try:
        doc = Document(str(path))
        table = _cargo_table(doc)

        assert table is not None, "таблица автомобилей не найдена"
        assert len(table.rows) == FILLED_CARS + 1, (
            f"машин {FILLED_CARS}, а строк в таблице {len(table.rows)} "
            f"(в бланке {TEMPLATE_CARS} + шапка)"
        )
        for number in range(1, FILLED_CARS + 1):
            cells = [cell.text.strip() for cell in table.rows[number].cells]
            assert cells == [str(number), f"МОДЕЛЬ {number}",
                             f"XTC651150N0001{number:03d}"]

        # Последняя строка бланка (12-я) удалена вместе с остальными пустыми:
        # ни её номера в колонке «№», ни остатка плейсхолдера в документе нет.
        text = _document_text(doc)
        assert "car_12" not in text
        assert "{{car_12_brand}}" not in text
        assert sum(1 for row in table.rows
                   if row.cells[2].text.strip() == "XTC651150N0001012") == 0
    finally:
        path.unlink(missing_ok=True)


def test_empty_shipper_blocks_removed(generator, work_dir, valid_ooo_data):
    """
    2 грузоотправителя из 10: в готовом документе нет блоков 3..10.

    Проверяются именно МЕТКИ бланка («Грузоотправитель: ООО «Грузоотправитель
    3»»), а не подстрока shipper_3: имени плейсхолдера в готовом документе уже
    нет, и такая проверка была бы всегда истинной.
    """
    path = _generate(generator, valid_ooo_data, work_dir, "e2e_empty_shippers")
    try:
        doc = Document(str(path))
        texts = _body_texts(doc)

        assert texts.count("Грузоотправитель: ООО «Грузоотправитель 1»") == 1
        assert texts.count("Грузоотправитель: ООО «Грузоотправитель 2»") == 1
        assert sum(1 for t in texts if t.startswith("Грузоотправитель:")) == FILLED_SHIPPERS
        assert sum(1 for t in texts if t.startswith("Адрес погрузки:")) == FILLED_SHIPPERS

        for number in range(FILLED_SHIPPERS + 1, TEMPLATE_POINTS + 1):
            label = f"Грузоотправитель: ООО «Грузоотправитель {number}»"
            assert label not in texts, f"в документе остался пустой блок {number}"

        # Строки плана и раздел 2 при удалении блоков не задеты.
        assert [t for t in texts if t.startswith("Дата / время погрузки:")]
        assert sum(1 for t in texts if t.startswith("Грузополучатель №")) == FILLED_CONSIGNEES
        assert sum(1 for t in texts if t.startswith("Адрес выгрузки:")) == FILLED_CONSIGNEES
    finally:
        path.unlink(missing_ok=True)


def test_fields_from_data_survive_full_pipeline(generated_ooo):
    """
    Данные формы доходят до готового документа целиком.

    Тот же набор значений, что проверяют тесты генератора, но здесь он прошёл
    через реальный путь: ContractData → build_replacements() → рендер бланка
    (docxtpl) → постобработка → файл на диске.
    """
    doc = Document(str(generated_ooo))
    texts = _body_texts(doc)

    assert "ЗАЯВКА № ЛР-2026-17" in _document_text(doc)
    assert "Заказчик: " + CUSTOMER_NAME in "\n".join(texts)
    assert "Общее количество: 3 шт." in texts
    assert "Тягач: DAF XF 95.430 гос. №: М342СА761" in texts
    assert "Прицеп: KRONE SD гос. №: ВК123478" in texts
    assert "Водитель: Иванов Иван Иванович" in texts
    assert "Погрузка круглосуточно, простой не более 24 часов." in texts


# ─────────────────────────────────────────────────────────────
# Стык 3: валидатор → генератор
# ─────────────────────────────────────────────────────────────

def test_validator_does_not_change_data(validator, valid_ooo_data):
    """Валидатор только читает: после проверки данные те же."""
    before = repr(valid_ooo_data)

    validator.check(valid_ooo_data)

    assert repr(valid_ooo_data) == before
    assert valid_ooo_data.contract["price_without_vat"] == OOO_PRICE_WITHOUT_VAT
    assert len(valid_ooo_data.vehicles) == FILLED_CARS + 2  # машины + тягач + прицеп
    assert len(valid_ooo_data.contract["loadings"]) == FILLED_SHIPPERS


def test_generator_survives_data_after_validator(generator, validator, work_dir,
                                                valid_ooo_data):
    """Данные после валидатора генератор печатает без потерь."""
    assert validator.check(valid_ooo_data).errors == []

    path = _generate(generator, valid_ooo_data, work_dir, "e2e_after_validator")
    try:
        text = _document_text(Document(str(path)))

        assert PLACEHOLDER_RE.search(text) is None
        assert f"Итого: {OOO_SUM_TOTAL_TEXT} руб." in text
        assert CUSTOMER_NAME in text
    finally:
        path.unlink(missing_ok=True)


def test_validator_accepts_empty_cargo_count(generator, validator, work_dir):
    """
    cargo_count необязателен: без него валидатор молчит, а бланк считает
    машины сам (поле печатается из фактического списка машин).
    """
    data = _make_data("ООО", OOO_PRICE_WITHOUT_VAT, 22)
    data.contract.pop("cargo_count")

    report = validator.check(data)
    assert report.errors == []
    assert [w for w in report.warnings if "cargo_count" in w] == [], (
        "без cargo_count замечание о расхождении количества выдавать не за что"
    )

    path = _generate(generator, data, work_dir, "e2e_no_cargo_count")
    try:
        assert "Общее количество: 3 шт." in _body_texts(Document(str(path)))
    finally:
        path.unlink(missing_ok=True)


def test_data_without_route_names_does_not_break_chain(generator, validator,
                                                       work_dir):
    """
    Точки без названий (распознавание их не нашло) — цепочка не рвётся.

    Так выглядит стык валидатора и генератора, когда name у точек нет вовсе:
    оба считают адрес достаточным, чтобы точка существовала. Валидатор ругается
    только замечанием, а генератор печатает блоки с одними адресами — и,
    что важнее, постобработка НЕ принимает их за пустые и не удаляет: адреса
    точек доживают до готового документа все до одного.
    """
    data = _make_data("ООО", OOO_PRICE_WITHOUT_VAT, 22)
    # Копия перед правкой: данные фикстуры не должны меняться «на будущее» —
    # так же (не мутируя) ведёт себя и валидатор.
    points = copy.deepcopy(data.contract["loadings"])
    for point in points:
        point.pop("name")
    data.contract["loadings"] = points

    report = validator.check(data)
    assert report.errors == [], f"неожиданные ошибки: {report.errors}"

    path = _generate(generator, data, work_dir, "e2e_no_point_names")
    try:
        texts = _body_texts(Document(str(path)))

        # Оба адреса на месте: безымянный блок с адресом не выброшен.
        for number in range(1, FILLED_SHIPPERS + 1):
            address = f"Адрес погрузки: г. Тестоград, ул. Складская, д. {number}"
            assert address in texts, f"потерян адрес точки {number}"
        assert sum(1 for t in texts if t.startswith("Грузоотправитель:")) == FILLED_SHIPPERS
        assert sum(1 for t in texts if t.startswith("Адрес погрузки:")) == FILLED_SHIPPERS

        # Выгрузка (имя есть) не пострадала от соседних пустых имён.
        assert "Грузополучатель №1: ООО «Грузополучатель 1»" in texts
        assert "Адрес выгрузки: г. Тестоград, ул. Складская, д. 1" in texts
    finally:
        path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────
# Стык 4: ООО ↔ ИП (бланк, суммы и подписи)
# ─────────────────────────────────────────────────────────────

def _stub(carrier_type: str) -> ContractData:
    """Минимальные данные только для выбора бланка (без генерации файла)."""
    return ContractData(contract={"carrier_type": carrier_type})


def test_carrier_type_switches_template_and_expediter(generator):
    """Смена carrier_type переключает бланк, а вместе с ним экспедитора."""
    ooo_path = generator.get_template_path(_stub("ООО"))
    ip_path = generator.get_template_path(_stub("ИП"))

    assert ooo_path.endswith(CARRIER_TYPES["ООО"])
    assert ip_path.endswith(CARRIER_TYPES["ИП"])
    assert ooo_path != ip_path
    # Пустой carrier_type — это ООО: вариант по умолчанию, как у валидатора.
    assert generator.get_template_path(_stub("")).endswith(CARRIER_TYPES["ООО"])


def test_carrier_type_switches_cost_section(generator, valid_ooo_data, valid_ip_data):
    """ООО — три суммы, ИП — одна: расчёт зависит от carrier_type."""
    ooo = generator.build_replacements(valid_ooo_data)
    ip = generator.build_replacements(valid_ip_data)

    assert ooo["sum_total"] == OOO_SUM_TOTAL_TEXT
    assert ooo["sum_wo_vat"] == OOO_SUM_WO_VAT_TEXT
    assert ooo["sum_vat"] == OOO_SUM_VAT_TEXT
    assert ooo["vat_rate"] == "22%"

    assert ip["sum_total"] == IP_SUM_TOTAL_TEXT
    for name in ("sum_wo_vat", "sum_vat", "vat_rate"):
        assert name not in ip, f"в ИП-вариант положен лишний ключ {name!r}"


def test_ooo_and_ip_documents_differ(generated_ooo, generated_ip):
    """Готовые документы двух вариантов различаются бланком, суммами и подписями."""
    ooo_text = _document_text(Document(str(generated_ooo)))
    ip_text = _document_text(Document(str(generated_ip)))

    assert OOO_EXPEDITER in ooo_text and OOO_EXPEDITER not in ip_text
    assert IP_EXPEDITER_FULL in ip_text and IP_EXPEDITER_FULL not in ooo_text

    assert f"Итого: {OOO_SUM_TOTAL_TEXT} руб." in ooo_text
    assert "Итого:" not in ip_text
    assert f"Стоимость услуг: {IP_SUM_TOTAL_TEXT} руб." in ip_text
    assert IP_SUM_TOTAL_TEXT not in ooo_text

    # Номер заявки и заказчик у вариантов общие — данные одни и те же.
    assert "ЗАЯВКА № ЛР-2026-17" in ooo_text
    assert "ЗАЯВКА № ЛР-2026-17" in ip_text
    assert CUSTOMER_NAME in ooo_text and CUSTOMER_NAME in ip_text


# ─────────────────────────────────────────────────────────────
# Стык 5: промпт распознавания → генератор (имена сумм)
# ─────────────────────────────────────────────────────────────

def _recognized_payload() -> dict:
    """
    Данные в форме ответа распознавания (core/prompts/logistiks_rus.py).

    Промпт кладёт суммы в contract.sum_wo_vat / sum_vat / sum_total, ставку —
    строкой vat_rate, а точки маршрута — в массивы shippers / consignees
    (НЕ в loadings / unloadings, как ждёт генератор).
    """
    return {
        "customer": {"full_name": CUSTOMER_NAME, "short_name": CUSTOMER_NAME},
        "shippers": [{"name": "ООО «Грузоотправитель 1»",
                      "address": "г. Тестоград, ул. Складская, д. 1"}],
        "consignees": [{"name": "ООО «Грузополучатель 1»",
                        "address": "г. Тестоград, ул. Приёмная, д. 1"}],
        "vehicles": [{"brand_model": "МОДЕЛЬ 1", "vin": "XTC651150N0001001"}],
        "tractor": {"brand_model": "DAF XF 95.430", "plate_number": "М342СА761"},
        "trailer": {"brand_model": "KRONE SD", "plate_number": "ВК123478"},
        "driver": {"full_name": "Иванов Иван Иванович"},
        "contract": {
            "number": "ЛР-2026-18",
            "date": "2026-09-24",
            "carrier_type": "ООО",
            "cargo_count": 1,
            "loading_date": "2026-09-26",
            "loading_time_from": "08:00",
            "loading_time_to": "20:00",
            "unloading_date": "2026-10-01",
            "unloading_time_from": "08:00",
            "unloading_time_to": "20:00",
            "sum_wo_vat": RECOGNIZED_SUM_WO_VAT,
            "sum_vat": RECOGNIZED_SUM_VAT,
            "sum_total": RECOGNIZED_SUM_TOTAL,
            "vat_rate": "22%",
            "special_conditions": "",
        },
    }


def test_recognition_names_are_not_read_by_generator(generator, work_dir):
    """
    EXPECTED CURRENT BEHAVIOUR (не баг этого шага).

    Стык «промпт → генератор» сейчас НЕ РАБОТАЕТ: распознавание кладёт суммы
    в contract.sum_wo_vat / sum_vat / sum_total, а генератор читает
    price_without_vat / price_input (LogistiksRusGenerator._price_without_vat).
    Имена не совпадают — в бланк уходит 0,00, хотя суммы в данных есть.
    Валидатор при этом ошибки не выдаёт: sum_* перечислены в его PRICE_FIELDS
    (LogistiksRusValidator.PRICE_FIELDS), то есть стоимость «есть» — и
    расхождение видно только в готовом документе.

    TODO(3.1.C.B.1): этот стык чинится в 3.1.C.B.1 — data builder окна
    «Логистикс Рус» маппит sum_total → price_without_vat (и разбирает
    vat_rate строкой «22%»). Сейчас тест ловит регресс: как только маппинг
    появится, ожидание 0,00 придётся заменить на реальную сумму.
    """
    payload = _recognized_payload()
    assert ContractData.coerce(payload).contract["sum_total"] == RECOGNIZED_SUM_TOTAL

    replacements = generator.build_replacements(payload)

    # Суммы из распознавания генератор не видит: он читает другие имена полей.
    assert "price_without_vat" not in payload["contract"]
    assert replacements["sum_wo_vat"] == "0,00"
    assert replacements["sum_vat"] == "0,00"
    assert replacements["sum_total"] == "0,00"
    # Ставка берётся из строки vat_rate — этот стык работает.
    assert replacements["vat_rate"] == "22%"

    # То же самое видно в готовом документе, а не только в карте замен.
    path = _generate(generator, payload, work_dir, "e2e_recognized_names")
    try:
        text = _document_text(Document(str(path)))

        assert "Стоимость услуг: 0,00 руб." in text
        assert "Итого: 0,00 руб." in text
        assert OOO_SUM_TOTAL_TEXT not in text
    finally:
        path.unlink(missing_ok=True)


def test_recognition_point_names_shippers_are_not_read_by_generator(
    generator, work_dir
):
    """
    EXPECTED CURRENT BEHAVIOUR (не баг этого шага).

    Вторая половина того же стыка: промпт кладёт точки в shippers / consignees,
    а генератор (и валидатор) читают loadings / unloadings. Валидатор на таких
    данных выдаёт ошибку «Укажите хотя бы одного грузоотправителя с адресом»,
    хотя точки в данных есть.

    TODO(3.1.C.B.1): чинится там же, где имена сумм — data builder раскладывает
    shippers / consignees в loadings / unloadings.
    """
    payload = _recognized_payload()

    report = LogistiksRusValidator().check(payload)
    assert "Укажите хотя бы одного грузоотправителя с адресом" in report.errors
    assert "Укажите хотя бы одного грузополучателя с адресом" in report.errors

    replacements = generator.build_replacements(payload)
    assert replacements["shipper_1_name"] == ""
    assert replacements["shipper_1_address"] == ""
    assert replacements["consignee_1_name"] == ""

    path = _generate(generator, payload, work_dir, "e2e_recognized_points")
    try:
        text = _document_text(Document(str(path)))

        assert "Грузоотправитель 1" not in text
        assert "Складская" not in text
    finally:
        path.unlink(missing_ok=True)
