#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты шага «Предоплата» — только тип «Экспедиторство» (perevozka).

Предоплата нужна ТОЛЬКО по договорам вкладки «Экспедиторство». У Формики,
Логистикса, аренды и Хавалов полей предоплаты нет, и их файлы этот шаг не
трогает.

Что проверяется:

  * `BaseContractGenerator._split_payment` — общий помощник расчёта:
    сумма предоплаты, остаток и проценты; предоплаты нет (0, пусто,
    больше стоимости, стоимость 0) — все ключи пустые, has_prepayment=False;
  * карта замен перевозки несёт восемь ключей разбивки, при 0 % — пустые;
  * валидатор перевозки ловит отрицательную предоплату и предоплату больше
    стоимости (ошибки) и предоплату 100 % (замечание);
  * вкладка «Договор»: поле «Предоплата, ₽», расчётный «Предоплата (%)»,
    процент считается от ИТОГА (суммы с НДС) и НЕ меняет введённую сумму;
  * в трёх бланках перевозки стоит условный блок `{%p if has_prepayment %}`,
    а ветка `else` — прежний текст пункта об оплате;
  * e2e: договор с предоплатой 30 % печатает разбивку, договор без
    предоплаты — прежний текст без единого упоминания предоплаты;
  * база: миграция добавляет колонки contracts.prepayment_amount /
    prepayment_percent, `save_contract` их пишет.

Данные синтетические, ПДн нет. Бланки НЕ пересобираются: тесты только
читают `templates/*.docx`.
"""

import os
import re
import sqlite3
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.contracts.base_generator import BaseContractGenerator
from core.contracts.perevozka.generator import PerevozkaGenerator
from core.contracts.perevozka.validator import PerevozkaValidator

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from ui.tabs.contract_tab import ContractTab  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PROJECT_ROOT / "templates"

#: Бланки перевозки: в них стоит условный пункт оплаты.
PEREVOZKA_TEMPLATES = (
    "shablon_ooo.docx",
    "shablon_ip_with_vat.docx",
    "shablon_ip_without_vat.docx",
)

#: Ключи разбивки оплаты — ровно те, что читает шаблон.
SPLIT_KEYS = (
    "has_prepayment",
    "prepayment_amount",
    "prepayment_amount_words",
    "prepayment_percent",
    "balance_amount",
    "balance_amount_words",
    "balance_percent",
)


def make_payload(prepayment: float = 0.0, price_with_vat: float = 210000.0,
                 payment_days: int = 15) -> dict:
    """
    Синтетический договор перевозки для проверок шага.

    Стоимость с НДС — база процента предоплаты; сумма предоплаты вводится
    в рублях, как её вводит оператор.
    """
    price_without_vat = round(price_with_vat / 1.22, 2)

    return {
        "contract": {
            "number": "ПРЕДОПЛАТА-ТЕСТ",
            "date": "2026-10-08",
            "route": "Москва — Казань",
            "carrier_type": "ООО (с НДС)",
            "vat_rate": "22%",
            "vat_rate_num": 22,
            "price_without_vat": price_without_vat,
            "price_with_vat": price_with_vat,
            "payment_days": payment_days,
            "prepayment_amount": prepayment,
        },
        "carrier": {"full_name": "ООО «Перевозчик»", "short_name": "ООО «Перевозчик»",
                    "inn": "7707654321", "kpp": "770701001",
                    "ogrn": "1027700261234",
                    "legal_address": "г. Москва, ул. Складская, д. 5",
                    "bank_account": "40702810000000000002", "bik": "044525226",
                    "bank_name": "АО «Банк»",
                    "correspondent_account": "30101810400000000226",
                    "director_name": "Петров Пётр Петрович"},
        "customer": {"full_name": "ООО «Заказчик»", "short_name": "ООО «Заказчик»",
                     "inn": "7701234567", "kpp": "770701001",
                     "legal_address": "г. Москва, ул. Тестовая, д. 1",
                     "bank_account": "40702810000000000001", "bik": "044525225",
                     "bank_name": "ПАО Сбербанк",
                     "correspondent_account": "30101810400000000225",
                     "director_name": "Иванов Иван Иванович"},
        "driver": {"full_name": "Сидоров Сидор Сидорович"},
        "vehicles": [{"brand_model": "JETOUR T2", "vin": "TESTVIN0000000001",
                      "vehicle_type": "Легковой автомобиль",
                      "loading_index": 1, "unloading_index": 1}],
        "tractor": {"brand_model": "DAF XF", "plate_number": "А001АА77"},
        "trailer": {"brand_model": "KRONE", "plate_number": "ВК123477"},
        "loadings": [{"name": "Салон",
                      "address": "г. Москва, ул. Погрузочная, д. 1",
                      "date": "2026-10-09", "time_window": "09:00-18:00"}],
        "unloadings": [{"name": "",
                        "address": "г. Казань, ул. Выгрузочная, д. 2",
                        "date": "2026-10-11", "time_window": "10:00-17:00"}],
    }


def document_paragraphs(path: Path):
    """Абзацы готового .docx (текст без переносов)."""
    from docx import Document

    return [paragraph.text for paragraph in Document(str(path)).paragraphs]


def payment_text(path: Path) -> str:
    """Текст пункта об оплате в готовом документе одной строкой."""
    parts = [
        text for text in document_paragraphs(path)
        if "Оплата" in text and ("4.2" in text or "4.4" in text)
    ]
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────
# F.2: общий помощник расчёта
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("total, prepayment, percent, balance, balance_percent", [
    (100.0, 30.0, "30", "70.00", "70"),
    (100.0, 33.33, "33.33", "66.67", "66.67"),
    (100.0, 1.0, "1", "99.00", "99"),
    (210000.0, 63000.0, "30", "147000.00", "70"),
    (100.0, 100.0, "100", "0.00", "0"),
])
def test_split_payment_divides_the_sum(total, prepayment, percent, balance,
                                       balance_percent):
    """Предоплата + остаток = итог, проценты в сумме дают 100."""
    split = BaseContractGenerator._split_payment(total, prepayment)

    assert split["has_prepayment"] is True
    assert split["prepayment_amount"] == f"{prepayment:.2f}"
    assert split["prepayment_percent"] == percent
    assert split["balance_amount"] == balance
    assert split["balance_percent"] == balance_percent
    assert (
        float(split["prepayment_amount"]) + float(split["balance_amount"])
        == pytest.approx(total)
    )
    assert (
        float(split["prepayment_percent"]) + float(split["balance_percent"])
        == pytest.approx(100.0)
    )


def test_split_payment_amounts_are_in_words():
    """Суммы печатаются прописью, первая буква — строчная (середина фразы)."""
    split = BaseContractGenerator._split_payment(210000.0, 63000.0)

    assert split["prepayment_amount_words"] == (
        "шестьдесят три тысячи рублей 00 копеек"
    )
    assert split["balance_amount_words"] == (
        "сто сорок семь тысяч рублей 00 копеек"
    )


@pytest.mark.parametrize("total, prepayment", [
    (100.0, 0.0),
    (100.0, -5.0),
    (100.0, 150.0),
    (0.0, 50.0),
    (100.0, None),
    (100.0, ""),
    (100.0, "мусор"),
    (None, 50.0),
])
def test_split_payment_returns_empty_without_prepayment(total, prepayment):
    """
    Предоплаты нет — ключи пустые, has_prepayment False.

    Сюда же попадает предоплата БОЛЬШЕ стоимости: валидатор её ловит,
    но и генератор не должен падать или печатать отрицательный остаток.
    """
    split = BaseContractGenerator._split_payment(total, prepayment)

    assert split["has_prepayment"] is False
    assert split["prepayment_amount"] == ""
    assert split["prepayment_amount_words"] == ""
    assert split["balance_amount"] == ""
    assert split["balance_amount_words"] == ""
    assert split["prepayment_percent"] == "0"
    assert split["balance_percent"] == "0"


def test_split_payment_keeps_eight_keys():
    """Набор ключей одинаков и при предоплате, и без неё."""
    assert set(BaseContractGenerator._split_payment(100.0, 30.0)) == set(SPLIT_KEYS)
    assert set(BaseContractGenerator._split_payment(100.0, 0.0)) == set(SPLIT_KEYS)


# ─────────────────────────────────────────────────────────────
# F.3: карта замен генератора
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def generator() -> PerevozkaGenerator:
    return PerevozkaGenerator(templates_dir=str(TEMPLATES))


def test_replacements_contain_split_keys(generator):
    """Карта замен несёт все восемь ключей разбивки."""
    replacements = generator.build_replacements(make_payload(63000.0))

    for key in SPLIT_KEYS:
        assert key in replacements, key

    assert replacements["has_prepayment"] is True
    assert replacements["prepayment_amount"] == "63000.00"
    assert replacements["prepayment_percent"] == "30"
    assert replacements["balance_amount"] == "147000.00"
    assert replacements["balance_percent"] == "70"


def test_replacements_split_is_empty_without_prepayment(generator):
    """Без предоплаты ключи пустые — шаблон печатает прежний текст."""
    replacements = generator.build_replacements(make_payload(0.0))

    assert replacements["has_prepayment"] is False
    assert replacements["prepayment_amount"] == ""
    assert replacements["balance_amount"] == ""
    assert replacements["prepayment_percent"] == "0"
    assert replacements["balance_percent"] == "0"


def test_prepayment_percent_follows_price_not_the_amount(generator):
    """
    Процент считается от стоимости: сумма предоплаты не пересчитывается.

    Оператор ввёл 63 000 ₽ при стоимости 210 000 ₽ (30 %). Стоимость
    изменили на 126 000 ₽ — сумма осталась 63 000 ₽, а процент стал 50.
    """
    replacements = generator.build_replacements(
        make_payload(63000.0, price_with_vat=126000.0)
    )

    assert replacements["prepayment_amount"] == "63000.00"
    assert replacements["prepayment_percent"] == "50"
    assert replacements["balance_amount"] == "63000.00"
    assert replacements["balance_percent"] == "50"


def test_split_log_has_percents_only(generator, caplog):
    """В лог уходят только проценты — сумм договора там быть не должно."""
    with caplog.at_level("INFO", logger="core.contract_generator"):
        generator.build_replacements(make_payload(63000.0))

    messages = [record.getMessage() for record in caplog.records]
    prepayment_logs = [text for text in messages if "Предоплата" in text]

    assert prepayment_logs, messages
    assert any("30%" in text and "70%" in text for text in prepayment_logs)
    assert not any("63000" in text or "147000" in text for text in messages)


def test_split_log_says_no_prepayment(generator, caplog):
    with caplog.at_level("INFO", logger="core.contract_generator"):
        generator.build_replacements(make_payload(0.0))

    messages = [record.getMessage() for record in caplog.records]
    assert any("Предоплата не предусмотрена" in text for text in messages)


# ─────────────────────────────────────────────────────────────
# F.5: валидатор
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def validator() -> PerevozkaValidator:
    return PerevozkaValidator()


def _prepayment_errors(report) -> list:
    return [text for text in report.errors if "предоплат" in text.lower()]


def _prepayment_warnings(report) -> list:
    return [text for text in report.warnings if "Предоплата" in text]


def test_prepayment_above_price_is_an_error(validator):
    report = validator.check(make_payload(300000.0, price_with_vat=210000.0))

    errors = _prepayment_errors(report)
    assert any("больше стоимости" in text for text in errors), errors


def test_full_prepayment_is_a_warning(validator):
    report = validator.check(make_payload(210000.0, price_with_vat=210000.0))

    warnings = _prepayment_warnings(report)
    assert any("100%" in text for text in warnings), warnings
    assert not _prepayment_errors(report), "100 % — замечание, а не ошибка"


def test_negative_prepayment_is_an_error(validator):
    report = validator.check(make_payload(-1000.0))

    assert any("отрицательной" in text for text in _prepayment_errors(report))


def test_normal_prepayment_is_silent(validator):
    report = validator.check(make_payload(63000.0, price_with_vat=210000.0))

    assert _prepayment_errors(report) == []
    assert _prepayment_warnings(report) == []


def test_zero_prepayment_is_silent(validator):
    """Предоплата 0 — проверять нечего: ошибок и замечаний о ней нет."""
    report = validator.check(make_payload(0.0))

    assert _prepayment_errors(report) == []
    assert _prepayment_warnings(report) == []


def test_prepayment_checks_do_not_break_old_payloads(validator, contract_payload):
    """Договор без ключа предоплаты проверяется как раньше."""
    report = validator.check(contract_payload)

    assert _prepayment_errors(report) == []
    assert _prepayment_warnings(report) == []


# ─────────────────────────────────────────────────────────────
# F.1: вкладка «Договор»
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qapp, isolated_db) -> ContractTab:
    return ContractTab()


def test_tab_has_prepayment_fields(tab):
    """Оба поля на месте, процент — только для чтения."""
    assert tab.prepayment_amount.value() == 0
    assert tab.prepayment_percent.isReadOnly()


def test_tab_percent_is_computed_from_total(tab):
    """Стоимость 100 000 ₽ без НДС, предоплата 30 000 ₽ → 24.59 % (с НДС 22 %)."""
    tab.price_input.setValue(100000.0)
    tab.prepayment_amount.setValue(30000.0)

    data = tab.get_data()

    assert data["prepayment_amount"] == 30000.0
    assert data["prepayment_percent"] == pytest.approx(24.59)
    assert tab.prepayment_percent.text() == "24.59 %"


def test_tab_percent_empty_without_prepayment(tab):
    tab.price_input.setValue(100000.0)

    assert tab.get_data()["prepayment_percent"] == 0.0
    assert tab.prepayment_percent.text() == ""


def test_tab_percent_is_dash_when_price_is_zero(tab):
    """Предоплата есть, а стоимости ещё нет — процент считать не от чего."""
    tab.prepayment_amount.setValue(5000.0)

    assert tab.prepayment_percent.text() == "—"
    assert tab.get_data()["prepayment_percent"] == 0.0


def test_tab_price_change_keeps_amount_and_recomputes_percent(tab):
    """
    Сумму предоплаты оператор ввёл руками — стоимость её не меняет.

    Стоимость 100 000 ₽ без НДС → итог 122 000 ₽ → 30 000 ₽ это 24.59 %.
    Стоимость удвоили: сумма осталась 30 000 ₽, а процент стал 12.3 %
    (30 000 / 244 000). Проценты считаются от ИТОГА с НДС, поэтому и
    ожидание берётся по итогу, а не делением прошлого процента.
    """
    tab.price_input.setValue(100000.0)
    tab.prepayment_amount.setValue(30000.0)

    assert tab.get_data()["prepayment_percent"] == pytest.approx(24.59)

    tab.price_input.setValue(200000.0)
    second = tab.get_data()

    assert second["prepayment_amount"] == 30000.0
    assert second["prepayment_percent"] == pytest.approx(
        round(30000.0 / 244000.0 * 100, 2)
    )


def test_tab_fill_data_accepts_prepayment_amount(tab):
    tab.fill_data({"prepayment_amount": 45000.0, "price_input": 150000.0})

    assert tab.get_data()["prepayment_amount"] == 45000.0


def test_tab_fill_data_zero_sets_zero(tab):
    """
    Ответ распознавания без предоплаты обнуляет поле — как в ТЗ шага.

    Виджет `QDoubleSpinBox` пустоты не знает: у него нет состояния
    «не заполнено», есть только 0. Поэтому ноль из данных — это «предоплаты
    нет», и поле приводится к нему. Ручной ввод так не защитить: признака
    «поле не трогали» у вкладки нет ни для одного поля (это относится ко
    всем суммам вкладки, а не только к предоплате).
    """
    tab.prepayment_amount.setValue(45000.0)

    tab.fill_data({"prepayment_amount": 0})

    assert tab.get_data()["prepayment_amount"] == 0.0
    assert tab.prepayment_percent.text() == ""


def test_tab_fill_data_ignores_garbage(tab):
    """Мусор в поле предоплаты не роняет вкладку и не меняет значение."""
    tab.prepayment_amount.setValue(45000.0)

    tab.fill_data({"prepayment_amount": "мусор"})

    assert tab.get_data()["prepayment_amount"] == 45000.0


def test_tab_clear_resets_prepayment(tab):
    tab.price_input.setValue(100000.0)
    tab.prepayment_amount.setValue(30000.0)

    tab.clear()

    assert tab.get_data()["prepayment_amount"] == 0.0
    assert tab.get_data()["prepayment_percent"] == 0.0
    assert tab.prepayment_percent.text() == ""


# ─────────────────────────────────────────────────────────────
# F.6: бланки
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", PEREVOZKA_TEMPLATES)
def test_template_has_conditional_payment_clause(name):
    """
    В бланке стоит условный блок: тег `if`, ветка `else`, тег `endif`.

    `{%p ... %}` docxtpl распознаёт, только когда тег стоит в абзаце ОДИН;
    поэтому теги — отдельные абзацы, а тексты (новый пункт и прежний) лежат
    между ними обычными абзацами.
    """
    with zipfile.ZipFile(str(TEMPLATES / name)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")

    assert "{%p if has_prepayment %}" in xml, name
    assert "{%p else %}" in xml, name
    assert "{%p endif %}" in xml, name


@pytest.mark.parametrize("name", PEREVOZKA_TEMPLATES)
def test_template_else_branch_keeps_old_payment_text(name):
    """Ветка `else` — дословно прежний пункт 4.4 с плейсхолдерами срока."""
    with zipfile.ZipFile(str(TEMPLATES / name)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")

    position = xml.find("{%p else %}")
    assert position > 0, name

    tail = xml[position:]
    for fragment in ("4.4. Оплата производится в течение", "{{payment_days}}",
                     "{{payment_days_words}}", "банковских дней"):
        assert fragment in tail, f"{name}: в ветке else нет {fragment!r}"


@pytest.mark.parametrize("name", PEREVOZKA_TEMPLATES)
def test_template_if_branch_has_report_wording(name):
    """Ветка `if` — формулировка из ТЗ со всеми восемью плейсхолдерами."""
    with zipfile.ZipFile(str(TEMPLATES / name)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")

    position = xml.find("{%p if has_prepayment %}")
    end = xml.find("{%p else %}")
    branch = xml[position:end]

    assert "Оплата услуг осуществляется Заказчиком в следующем порядке" in branch
    assert "предоплата в размере {{prepayment_percent}}%" in branch
    assert "окончательный расчёт в размере {{balance_percent}}%" in branch

    for placeholder in ("prepayment_amount", "prepayment_amount_words",
                        "balance_amount", "balance_amount_words"):
        assert "{{" + placeholder + "}}" in branch, f"{name}: {placeholder}"


@pytest.mark.parametrize("name", PEREVOZKA_TEMPLATES)
def test_template_keeps_single_number_for_clause(name):
    """Номер пункта в ветке `if` — 4.2: прежний 4.4 занят веткой `else`."""
    with zipfile.ZipFile(str(TEMPLATES / name)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")

    start = xml.find("{%p if has_prepayment %}")
    end = xml.find("{%p else %}")
    branch = xml[start:end]

    assert "4.2. Оплата услуг" in branch, name
    assert "4.4." not in branch, f"{name}: в ветке if остался номер 4.4"


def test_templates_change_only_in_place(templates_dir):
    """
    Правка бланка — часть архива: остальные записи пакета не тронуты.

    В бланках перевозки встроены шрифты (`word/fonts/`, 3 записи), и
    `python-docx` их переписывает. Скрипт правки меняет ровно
    `word/document.xml`, поэтому число записей и имена остальных частей
    должны совпадать у всех трёх бланков.
    """
    listings = []
    for name in PEREVOZKA_TEMPLATES:
        with zipfile.ZipFile(str(TEMPLATES / name)) as archive:
            names = archive.namelist()
        listings.append((name, sorted(names), sum(n.startswith("word/fonts/") for n in names)))

    for name, names, fonts in listings:
        assert fonts == 3, f"{name}: встроенных шрифтов {fonts}, ожидалось 3"

    first = listings[0][1]
    for name, names, _fonts in listings[1:]:
        assert names == first, f"{name}: состав пакета отличается"


# ─────────────────────────────────────────────────────────────
# F.4: e2e — договор с предоплатой и без
# ─────────────────────────────────────────────────────────────

def test_contract_with_prepayment_prints_the_split(generator, work_file):
    """Договор с предоплатой 30 % печатает разбивку оплаты."""
    path = Path(generator.generate(make_payload(63000.0),
                                   output_dir=str(work_file("out"))))

    text = payment_text(path)

    assert "4.2. Оплата услуг осуществляется Заказчиком в следующем порядке" in text
    assert "63000.00 руб." in text
    assert "шестьдесят три тысячи рублей 00 копеек" in text
    assert "предоплата в размере 30% от стоимости услуг" in text
    assert "147000.00 руб." in text
    assert "окончательный расчёт в размере 70% от стоимости услуг" in text
    assert "в течение 15 (пятнадцати) банковских дней" in text
    assert "{%p" not in text, "служебные теги шаблона попали в документ"


def test_contract_without_prepayment_keeps_the_old_text(generator, work_file):
    """Без предоплаты договор печатается ровно как раньше."""
    path = Path(generator.generate(make_payload(0.0),
                                   output_dir=str(work_file("out"))))

    text = payment_text(path)

    assert "4.4. Оплата производится в течение 15 (пятнадцати) банковских дней" in text
    assert "с даты завершения выгрузки последнего из перевозимых автомобилей" in text
    assert "предоплат" not in text.lower(), "упоминание предоплаты осталось"
    assert "{%p" not in text


def test_prepayment_amounts_add_up_in_document(generator, work_file):
    """X + Y = итог, N + M = 100 — проверяем по тексту документа."""
    path = Path(generator.generate(make_payload(63000.0, price_with_vat=210000.0),
                                   output_dir=str(work_file("out"))))

    text = payment_text(path)
    amounts = [float(value) for value in re.findall(r"(\d+\.\d\d) руб\.", text)]
    percents = [float(value) for value in re.findall(r"размере (\d+(?:\.\d+)?)%", text)]

    assert len(amounts) == 2, text
    assert sum(amounts) == pytest.approx(210000.0)
    assert len(percents) == 2, text
    assert sum(percents) == pytest.approx(100.0)


def test_document_without_prepayment_has_no_amount_from_split(generator, work_file):
    """В договоре без предоплаты нет ни сумм разбивки, ни её процентов."""
    path = Path(generator.generate(make_payload(0.0),
                                   output_dir=str(work_file("out"))))

    text = payment_text(path)

    assert "предоплата в размере" not in text
    assert "окончательный расчёт" not in text


# ─────────────────────────────────────────────────────────────
# E: база данных
# ─────────────────────────────────────────────────────────────

OLD_CONTRACTS_SCHEMA = """
    CREATE TABLE contracts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_number TEXT,
        contract_date TEXT
    );
"""


@pytest.fixture
def old_contracts_db(work_file, monkeypatch):
    """Временная база прежней схемы: в contracts нет колонок предоплаты."""
    import db.database as database

    path = work_file("contracts.db")
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(OLD_CONTRACTS_SCHEMA)
        conn.execute(
            "INSERT INTO contracts (id, contract_number, contract_date) "
            "VALUES (1, 'OLD-1', '2026-09-23')"
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(database, "DB_PATH", str(path))
    monkeypatch.setattr(database, "restrict_to_current_user", lambda *a, **k: True)
    monkeypatch.setattr(database, "backup_database", lambda *a, **k: None)
    monkeypatch.setattr(
        database, "salons_xlsx_path", lambda: "tests/_tmp/no-such-salons.xlsx"
    )
    return database, path


def _columns(path, table: str):
    conn = sqlite3.connect(str(path))
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def test_migration_adds_prepayment_columns(old_contracts_db):
    """После init_database у contracts есть обе колонки предоплаты."""
    database, path = old_contracts_db

    assert "prepayment_amount" not in _columns(path, "contracts")

    database.init_database()

    columns = _columns(path, "contracts")
    assert "prepayment_amount" in columns
    assert "prepayment_percent" in columns


def test_migration_keeps_existing_contracts(old_contracts_db):
    """Миграция не теряет строки: договор до миграции остаётся на месте."""
    database, path = old_contracts_db

    database.init_database()

    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT contract_number, prepayment_amount, prepayment_percent "
            "FROM contracts"
        ).fetchall()
    finally:
        conn.close()

    assert rows == [("OLD-1", 0, 0)]


def test_needs_migration_sees_prepayment_columns(old_contracts_db):
    """Колонки в списке обязательных: перед миграцией делается бэкап базы."""
    database, path = old_contracts_db

    conn = sqlite3.connect(str(path))
    try:
        assert database._needs_migration(conn.cursor()) is True
    finally:
        conn.close()


def test_save_contract_stores_prepayment(isolated_db):
    """save_contract пишет сумму и процент предоплаты."""
    contract_id = isolated_db.save_contract({
        "number": "ПРЕДОПЛАТА-1",
        "date": "2026-10-08",
        "price_with_vat": 210000.0,
        "prepayment_amount": 63000.0,
        "prepayment_percent": 30.0,
    })

    conn = isolated_db.get_connection()
    try:
        row = conn.execute(
            "SELECT prepayment_amount, prepayment_percent FROM contracts "
            "WHERE id = ?", (contract_id,)
        ).fetchone()
    finally:
        conn.close()

    assert row == (63000.0, 30.0)


def test_save_contract_without_prepayment_writes_zeros(isolated_db):
    """Старые вызовы без ключей предоплаты сохраняются как раньше."""
    contract_id = isolated_db.save_contract({
        "number": "БЕЗ-ПРЕДОПЛАТЫ",
        "date": "2026-10-08",
    })

    conn = isolated_db.get_connection()
    try:
        row = conn.execute(
            "SELECT prepayment_amount, prepayment_percent FROM contracts "
            "WHERE id = ?", (contract_id,)
        ).fetchone()
    finally:
        conn.close()

    assert row == (0, 0)


def test_to_db_dict_carries_prepayment_from_the_tab():
    """
    Предоплата доходит до базы БЕЗ правки core/contract_data.py.

    `to_db_dict()` отдаёт шаг `contract` как есть, а ключ кладёт вкладка
    «Договор» — значит, отдельные строки в неприкосновенном модуле не
    нужны: `contract_data.py` не менялся.
    """
    from core.contract_data import ContractData

    data = ContractData(contract={"number": "N-1", "prepayment_amount": 63000.0,
                                  "prepayment_percent": 30.0})

    payload = data.to_db_dict()

    assert payload["prepayment_amount"] == 63000.0
    assert payload["prepayment_percent"] == 30.0
