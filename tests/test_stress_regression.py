#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Регрессия по стресс-тесту генерации DOCX (ЧАСТЬ 3 и 4 стресс-теста).

Здесь закреплены ДЕФЕКТЫ, найденные стресс-прогоном на 100 сценариях
(`tools/make_test_scenarios.py`, `tools/stress_check.py`), и правила, которые
не должны сломаться снова:

  1. даты договора принимаются в любом из семи форматов, а печатаются
     всегда в одном виде (иначе в договоре было «2026.09.24 г.»);
  2. косметика пробелов: двойной пробел и пробел перед знаком препинания
     в готовом документе не остаются;
  3. матрица покрытия 100 сценариев — ровно такая, как в задании;
  4. сценарии собираются воспроизводимо (одно зерно — один набор);
  5. проверки качества документа ловят подложенные дефекты.

Тесты НЕ гоняют 100 сценариев через шаблон: это минуты работы. Матрица,
воспроизводимость и проверки качества тестируются на данных, а правки
генератора — на реальном шаблоне с одним-двумя сценариями.

Все данные синтетические, реальных ПДн нет.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest
from docx import Document

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.contracts.base_generator import NormalizeSpacesStep  # noqa: E402
from core.contracts.perevozka.generator import PerevozkaGenerator  # noqa: E402
from core.dates import DATE_FORMATS, to_iso  # noqa: E402
from tools.make_golden import strip_embedded_fonts  # noqa: E402
from tools.make_test_scenarios import (  # noqa: E402
    COVERAGE_MATRIX, SCENARIO_COUNT, build_scenarios, collect_coverage,
)
from tools.stress_check import (  # noqa: E402
    check_document, check_unit, document_units,
)

TEMPLATES_DIR = PROJECT_ROOT / "templates"


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def scenarios():
    """100 сценариев БЛОКА 1 (собираются на месте, без файлов)."""
    return build_scenarios()


@pytest.fixture(scope="module")
def coverage(scenarios):
    return collect_coverage(scenarios)


@pytest.fixture(scope="module")
def lightweight_templates(tmp_path_factory):
    """Облегчённые копии шаблонов: 2,7 МБ встроенных шрифтов не нужны."""
    target = tmp_path_factory.mktemp("stress_tpl")
    for name in ("shablon_ooo.docx", "shablon_ip_with_vat.docx",
                 "shablon_ip_without_vat.docx"):
        strip_embedded_fonts(TEMPLATES_DIR / name, target / name)
    return target


@pytest.fixture
def payload():
    """Один сценарий с ООО-перевозчиком: полный набор полей."""
    from tools.make_test_scenarios import _assemble
    import random

    rng = random.Random(12345)
    scenario = _assemble(
        rng=rng, index=0, party_kind="ООО", gender="male", kind="plain",
        vehicles_count=1, loadings_count=1, unloadings_count=1,
        flags={}, amount=180300.0, date_fmt_index=0,
    )
    return scenario["payload"]


def _render(payload, templates_dir, tmp_path, name="out.docx"):
    """Рендер договора и чтение его обратно как документа."""
    generator = PerevozkaGenerator(templates_dir=str(templates_dir))
    target = tmp_path / name
    generator.generate_docx(payload, str(target))
    return Document(str(target)), target


def _text(doc) -> str:
    parts = [paragraph.text for paragraph in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────
# 1. Матрица покрытия (ЧАСТЬ 1A)
# ─────────────────────────────────────────────────────────────

def test_scenario_count_is_100(scenarios):
    assert len(scenarios) == SCENARIO_COUNT == 100


@pytest.mark.parametrize("key", ["party_kind", "driver_gender",
                                 "vehicles_count", "loadings_count",
                                 "unloadings_count"])
def test_matrix_dimensions_match_task(coverage, key):
    """Размерности раздаются ровно по матрице, а не «примерно»."""
    assert coverage[key] == COVERAGE_MATRIX[key]


@pytest.mark.parametrize("key", ["hyphen_surname", "foreign_name", "long_name",
                                 "empty_fields", "special_chars",
                                 "long_company_names", "short_name",
                                 "amount_extreme", "amount_with_kopecks"])
def test_matrix_flags_match_task(coverage, key):
    """Особые признаки есть ровно у стольких сценариев, сколько в задании."""
    assert coverage[key] == COVERAGE_MATRIX[key]


def test_all_date_formats_are_used(coverage):
    """Все семь форматов дат из core/dates.py встречаются в сценариях."""
    used = {fmt for fmt in coverage["date_format"] if fmt.startswith("%")}
    assert used == set(DATE_FORMATS)


def test_scenarios_are_reproducible_by_seed():
    """Одно зерно — один и тот же набор (иначе регрессия «поедет»)."""
    first = build_scenarios()
    second = build_scenarios()
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)


def test_scenarios_have_all_contract_data_sections(scenarios):
    """Сценарий — полный набор полей ContractData, без пропусков."""
    required = {"driver", "carrier", "customer", "vehicles", "tractor",
                "trailer", "contract", "loadings", "unloadings", "city"}
    for scenario in scenarios:
        assert required <= set(scenario["payload"]), scenario["description"]


def test_vin_in_scenarios_is_valid(scenarios):
    """VIN в сценариях: 17 знаков, без I, O и Q."""
    import re

    for scenario in scenarios:
        for vehicle in scenario["payload"]["vehicles"]:
            vin = vehicle["vin"]
            assert len(vin) == 17, vin
            assert not re.search(r"[IOQ]", vin), vin


def test_special_chars_scenarios_contain_ampersand(scenarios):
    """Сценарии со спецсимволами несут «&» и «<»: проверка автоэкранирования."""
    marked = [s for s in scenarios if s["description"]["flags"]["special_chars"]]
    assert len(marked) == 10
    assert any("&" in s["payload"]["carrier"]["full_name"] for s in marked)
    assert any("<" in s["payload"]["carrier"]["full_name"] for s in marked)


def test_empty_fields_scenarios_are_really_empty(scenarios):
    """Сценарий «пустые поля» несёт незаполненные необязательные поля."""
    marked = [s for s in scenarios if s["description"]["flags"]["empty_fields"]]
    assert len(marked) == 20
    for scenario in marked:
        carrier = scenario["payload"]["carrier"]
        driver = scenario["payload"]["driver"]
        assert carrier["actual_address"] == ""
        assert carrier["bank_account"] == ""
        assert driver["license_categories"] == ""
        assert driver["birth_place"] == ""


# ─────────────────────────────────────────────────────────────
# 2. Даты в договоре (найденный дефект № 1)
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt", DATE_FORMATS)
def test_contract_dates_accept_every_format(fmt, payload, lightweight_templates,
                                            tmp_path):
    """
    Дата в любом из семи форматов печатается в договоре как ДД.ММ.ГГГГ.

    Дефект, найденный стресс-прогоном: бланк подставляет
    `{{loading_plan_date}}` как есть, поэтому «2026.09.24» из распознанного
    документа попадало в договор буквально — «2026.09.24 г.».
    """
    from datetime import date

    data = json.loads(json.dumps(payload))
    source = date(2026, 9, 23)
    for key in ("date", "loading_plan_date", "unloading_plan_date"):
        data["contract"][key] = source.strftime(fmt)

    # Формат содержит «/» и «%» — в имени файла они недопустимы.
    safe = fmt.replace("%", "").replace("/", "_").replace("-", "_")
    doc, _ = _render(data, lightweight_templates, tmp_path, f"{safe}.docx")
    text = _text(doc)

    assert "23.09.2026" in text, f"дата не напечатана в ДД.ММ.ГГГГ ({fmt})"
    # ISO-вид и разделители «/», «-» в документе остаться не должны.
    assert "2026-09-23" not in text
    assert "2026.09.23" not in text
    assert "23/09/2026" not in text
    assert "23-09-2026" not in text


def test_unparseable_contract_date_is_not_invented(lightweight_templates,
                                                   payload, tmp_path):
    """
    Неразобранная дата не превращается в другую и не подменяется пустой.

    Правило: мусор в дате должен быть ВИДЕН (и пойматься валидатором), а не
    молча исчезнуть. Поэтому генератор не трогает значение, которое не
    разобрал.
    """
    data = json.loads(json.dumps(payload))
    data["contract"]["loading_plan_date"] = "не дата"
    doc, _ = _render(data, lightweight_templates, tmp_path, "bad_date.docx")
    assert "не дата" in _text(doc)


def test_normalize_contract_dates_keeps_iso(payload):
    """ISO-дата остаётся той же: нормализация — тождество на своём формате."""
    contract = {"date": "2026-09-23"}
    PerevozkaGenerator._normalize_contract_dates(contract)
    assert contract["date"] == "2026-09-23"


def test_normalize_contract_dates_skips_empty():
    """Пустая дата и отсутствующий ключ не создаются."""
    contract = {"date": "", "loading_plan_date": None}
    PerevozkaGenerator._normalize_contract_dates(contract)
    assert contract == {"date": "", "loading_plan_date": None}


@pytest.mark.parametrize("value,expected", [
    ("26.01.2023", "2023-01-26"),
    ("26/01/2023", "2023-01-26"),
    ("20230126", "2023-01-26"),
    ("26 01 2023", "2023-01-26"),
])
def test_to_iso_matches_normalizer_expectations(value, expected):
    """Нормализатор опирается на core.dates — фиксируем сам разбор."""
    assert to_iso(value) == expected


# ─────────────────────────────────────────────────────────────
# 3. Косметика пробелов (найденный дефект № 2)
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source,expected", [
    ("Заказчику  убытки", "Заказчику убытки"),
    ("при условии , что", "при условии, что"),
    ("и мессенджерам.  Такая переписка", "и мессенджерам. Такая переписка"),
    ("Генеральный директор  ________", "Генеральный директор ________"),
    ("строка\tс табуляцией", "строка с табуляцией"),
    ("nbsp\u00a0здесь", "nbsp здесь"),
    ("скобка( пробел)", "скобка(пробел)"),
    ("кавычка« пробел»", "кавычка«пробел»"),
])
def test_normalize_spaces(source, expected):
    """Правила косметики: двойной пробел, пробел перед знаком, табуляция."""
    assert PerevozkaGenerator.normalize_spaces(source) == expected


def test_normalize_spaces_keeps_correct_text():
    """Корректный текст не меняется — иначе правка портила бы документы."""
    good = "ООО «Ромашка», ИНН 7701234567, тел. +7 (900) 123-45-67."
    assert PerevozkaGenerator.normalize_spaces(good) == good


def test_normalize_spaces_keeps_single_leading_space():
    """Ведущий одиночный пробел абзаца сохраняется (отступ списка бланка)."""
    assert PerevozkaGenerator.normalize_spaces(" список") == " список"


def test_space_step_is_last_in_pipeline(tmp_path):
    """Косметика пробелов идёт последней: она правит уже собранный документ."""
    steps = PerevozkaGenerator(templates_dir=str(tmp_path)).postprocess_steps(None)
    assert steps[-1].name == "normalize_spaces"
    assert isinstance(steps[-1], NormalizeSpacesStep)


def test_space_step_applies_to_document(tmp_path):
    """Шаг действительно правит документ, а не только объявлен."""
    doc = Document()
    doc.add_paragraph("двойной  пробел и запятая , вот")
    NormalizeSpacesStep(PerevozkaGenerator(templates_dir=str(tmp_path))).apply(
        doc, None)
    assert doc.paragraphs[0].text == "двойной пробел и запятая, вот"


def test_space_step_keeps_line_break_paragraphs(tmp_path):
    """
    Абзац с <w:br/> не трогается: пробел перед разрывом может быть нужен.

    Разрыв создаётся на уровне XML: `add_break()` в python-docx ставит
    <w:cr/>, а шаг пропускает именно <w:br/> — тот тег, которым
    ConvertNewlinesStep заменяет «\\n» из данных. Разрыв и текст — в ОДНОМ
    run'е: текст второго run'а остался бы за разрывом и не попал бы в
    `paragraph.text`.
    """
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls, qn

    doc = Document()
    doc.add_paragraph("")
    paragraph = doc.paragraphs[0]
    run = paragraph.add_run("первая строка")
    # Разрыв добавляется готовым XML: у CT_R нет публичного метода именно для
    # <w:br/> (add_break() ставит <w:cr/>).
    run._r.append(parse_xml(f"<w:br {nsdecls('w')}/>"))
    run.add_text("вторая  строка")

    # `.//` — потому что <w:br/> лежит ВНУТРИ run'а, а не прямо в абзаце.
    break_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}br"
    assert paragraph._p.findall(f".//{break_tag}")

    NormalizeSpacesStep(PerevozkaGenerator(templates_dir=str(tmp_path))).apply(
        doc, None)

    # Разрыв на месте, двойной пробел не тронут — абзац пропущен целиком.
    assert paragraph._p.findall(f".//{break_tag}"), "разрыв строки потерян"
    assert "вторая  строка" in paragraph.text


def test_no_double_spaces_in_rendered_document(payload, lightweight_templates,
                                               tmp_path):
    """В готовом договоре двойных пробелов нет — проверка БЛОКА 1, пункт «в»."""
    doc, _ = _render(payload, lightweight_templates, tmp_path, "spaces.docx")
    text = _text(doc)
    assert "  " not in text


def test_no_space_before_punctuation_in_document(payload, lightweight_templates,
                                                 tmp_path):
    """Пробела перед знаком препинания в договоре нет — пункт «г»."""
    import re

    doc, _ = _render(payload, lightweight_templates, tmp_path, "punct.docx")
    assert not re.search(r"\s+[,.;:!?]", _text(doc))


# ─────────────────────────────────────────────────────────────
# 4. Проверки качества ловят дефекты (ЧАСТЬ 1C)
# ─────────────────────────────────────────────────────────────

def _unit(text, index=0):
    return {"kind": "p", "index": index, "text": text, "table": None,
            "row": None, "cell": None}


def _document_problems(text, driver_name="Тестов Тест Тестович"):
    """
    Дефекты ОДНОГО абзаца, найденные проверками уровня документа.

    Часть проверок (реквизиты, телефоны, суммы) работает по всему тексту:
    VIN в таблице и VIN в абзаце должны проверяться одинаково. Поэтому
    тестам нужен и вход уровня документа, а не только `check_unit`.
    """
    scenario = {
        "driver": {"full_name": driver_name},
        "carrier": {}, "customer": {},
    }
    return check_document(Path("x.docx"), scenario, [_unit(text)])


@pytest.mark.parametrize("text,expected", [
    ("Договор {{contract_number}} от", "плейсхолдер"),
    ("Сумма: None руб.", "заглушка"),
    ("двойной  пробел", "двойных пробелов"),
    ("запятая , вот", "пробел перед знаком препинания"),
    ("дата 2026-09-24 г.", "ISO-формате"),
    ("дата 24/09/2026", "через «/»"),
    ("г. Москва, г. Москва", "двойной префикс города"),
    ("адрес: д. д. 5", "двойной префикс дома"),
    ("КПП ,", "пустое поле «КПП»"),
    ("ИП Общество с ограниченной ответственностью «Ромашка»", "и «Индивидуальный предприниматель»"),
])
def test_checker_catches_defect(text, expected):
    """Каждая проверка ловит свой дефект — иначе она бесполезна."""
    problems = check_unit(_unit(text))
    assert any(expected in problem["problem"] for problem in problems), problems


def test_checker_misses_nothing_on_clean_text():
    """Чистый абзац дефектов не даёт."""
    clean = ("Общество с ограниченной ответственностью «Ромашка», "
             "ИНН 7701234567, ОГРН 1027700132195, в лице Генерального "
             "директора Петрова Петра Петровича, действующего на основании "
             "Устава, именуемое в дальнейшем «Заказчик».")
    assert check_unit(_unit(clean)) == []


def test_checker_flags_broken_amount_words():
    """
    Расхождение суммы прописью с числом — дефект уровня high.

    Проверка уровня документа ищет и другие дефекты (в том числе «ФИО
    водителя не найдено»), поэтому смотрим именно на сообщение о сумме.
    """
    text = ("Итоговая сумма: 180300.00 руб. (Сто восемьдесят тысяч триста "
            "рублей 00 копеек).")
    assert not any("сумма прописью не совпадает" in p["problem"]
                   for p in _document_problems(text))

    broken = text.replace("восемьдесят", "девяносто")
    problems = _document_problems(broken)
    assert any("сумма прописью не совпадает" in p["problem"]
               for p in problems)
    assert any(p["severity"] == "high" for p in problems)


def test_checker_reports_vin_with_forbidden_letters():
    """VIN с буквой I — дефект: буквы I, O, Q в VIN недопустимы."""
    problems = _document_problems("VIN: XI3TEUMB0T0002608")
    assert any("I, O или Q" in p["problem"] for p in problems)


def test_checker_does_not_invent_vin_in_account_number():
    """20-значный счёт не считается «VIN неверной длины»."""
    problems = _document_problems("р/с 40702810000000000001 в ПАО Сбербанк")
    assert not any("VIN" in p["problem"] for p in problems)


def test_checker_does_not_treat_passport_series_as_phone():
    """Серия и номер паспорта телефоном не считаются."""
    problems = _document_problems("Паспорт: 45 12 345678")
    assert not any("телефон" in p["problem"] for p in problems)


def test_checker_accepts_uniform_phone():
    """Телефон в едином формате дефектом не считается."""
    problems = _document_problems("Телефон: +7 (900) 123-45-67")
    assert not any("телефон" in p["problem"] for p in problems)


def test_checker_flags_odd_phone_format():
    """Телефон в другом формате — дефект «единый формат» (пункт «л»)."""
    problems = _document_problems("Телефон: 8 900 123 45 67")
    assert any("телефон не в едином формате" in p["problem"] for p in problems)


def test_document_units_reads_paragraphs_and_tables(tmp_path):
    """Сбор текста документа включает таблицы — иначе проверки их не увидят."""
    doc = Document()
    doc.add_paragraph("абзац")
    table = doc.add_table(rows=1, cols=1)
    table.rows[0].cells[0].text = "ячейка"
    path = tmp_path / "units.docx"
    doc.save(str(path))
    units = document_units(path)
    assert any(u["text"] == "абзац" for u in units)
    assert any(u["text"] == "ячейка" and u["kind"] == "cell" for u in units)


# ─────────────────────────────────────────────────────────────
# 5. Целостность сгенерированного документа
# ─────────────────────────────────────────────────────────────

def test_document_opens_with_python_docx(payload, lightweight_templates,
                                         tmp_path):
    """Документ открывается: это проверка «документ не открывается» (high)."""
    doc, path = _render(payload, lightweight_templates, tmp_path, "open.docx")
    assert path.stat().st_size > 1000
    assert doc.paragraphs


def test_special_chars_are_escaped_in_document(lightweight_templates, tmp_path):
    """«&» и «<» из наименования не ломают XML и печатаются как есть."""
    from tools.make_test_scenarios import _assemble
    import random

    rng = random.Random(777)
    scenario = _assemble(
        rng=rng, index=90, party_kind="ООО", gender="male", kind="plain",
        vehicles_count=1, loadings_count=1, unloadings_count=1,
        flags={"special_chars": True}, amount=1000.0, date_fmt_index=0,
    )
    doc, _ = _render(scenario["payload"], lightweight_templates, tmp_path,
                     "amp.docx")
    text = _text(doc)
    assert "Ромашка & Ко" in text
    assert "&amp;" not in text
    assert "<Транс>" in text


def test_amount_words_match_number(payload, lightweight_templates, tmp_path):
    """Сумма прописью в документе совпадает с числом (пункт «ж»)."""
    data = json.loads(json.dumps(payload))
    data["contract"]["price_without_vat"] = 123456.78
    data["contract"]["price_with_vat"] = 150617.27
    doc, _ = _render(data, lightweight_templates, tmp_path, "sum.docx")

    from tools.stress_check import _amount_bad_pairs

    assert _amount_bad_pairs(_text(doc)) == []


def test_amount_with_kopecks_is_printed(payload, lightweight_templates,
                                        tmp_path):
    """Копейки печатаются прописью, а не отбрасываются."""
    data = json.loads(json.dumps(payload))
    data["contract"]["vat_rate"] = "0%"
    data["contract"]["vat_rate_num"] = 0
    data["contract"]["carrier_type"] = "ИП без НДС"
    data["contract"]["price_without_vat"] = 46885.25
    data["carrier"]["entity_type"] = "ИП"
    data["carrier"]["full_name"] = "Индивидуальный предприниматель Тестов Тест Тестович"
    doc, _ = _render(data, lightweight_templates, tmp_path, "kop.docx")
    text = _text(doc)
    assert "46885.25" in text
    assert "двадцать пять копеек" in text


def test_generated_document_has_no_leftover_placeholders(
        payload, lightweight_templates, tmp_path):
    """Плейсхолдеров в документе не остаётся — проверка «а», уровень high."""
    doc, _ = _render(payload, lightweight_templates, tmp_path, "ph.docx")
    text = _text(doc)
    for marker in ("{{", "}}", "{%", "%}"):
        assert marker not in text
