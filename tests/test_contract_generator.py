#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты генератора договоров (core/contract_generator.py).

Главный тест — сверка плейсхолдеров шаблонов с картой замен: именно он
поймал баг, из-за которого в договор не попадали тягач и полуприцеп.

Отдельная группа тестов — таблицы погрузок/выгрузок (блоки 3.2 и 3.3):
в шаблон ставится метка {{LOADING_TABLE_HERE}} / {{UNLOADING_TABLE_HERE}},
а генератор собирает на её месте таблицы «№ / Марка-Модель / VIN-номер».

Все данные синтетические, реальные ПДн не используются.
"""

import gc
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest
from docx import Document

from core.contract_data import ContractData
from core.contract_generator import ContractGenerator

TEMPLATES = (
    "shablon_ooo.docx",
    "shablon_ip_with_vat.docx",
    "shablon_ip_without_vat.docx",
)

#: Метки таблиц погрузок/выгрузок: что стоит в шаблоне и что ищет генератор.
LOADING_PLACEHOLDER = "LOADING_TABLE_HERE"
UNLOADING_PLACEHOLDER = "UNLOADING_TABLE_HERE"

#: Плейсхолдеры старого (плоского) формата блоков 3.2 / 3.3.
LEGACY_BLOCK_NAMES = ("loading_block", "unloading_block")


def template_placeholders(path) -> set:
    """Все имена {{переменных}} из шаблона (по всем XML-частям)."""
    names = set()
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith(".xml"):
                continue
            xml = archive.read(name).decode("utf-8", "ignore")
            text = re.sub(r"<[^>]+>", "", xml)
            for match in re.finditer(r"\{\{(.*?)\}\}", text, re.S):
                names.add(re.sub(r"\s+", "", match.group(1)))
    return names


def document_text(path) -> str:
    """Весь текст документа, включая таблицы."""
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


def document_xml(path) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read("word/document.xml").decode("utf-8", "ignore")


def placeholders_left(path) -> list:
    text = re.sub(r"<[^>]+>", "", document_xml(path))
    return sorted(set(re.findall(r"\{\{[^{}]*\}\}", text)))


# ─────────────────────────────────────────────────────────────
# Помощники для разбора готового документа
# ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _release_documents():
    """
    Собирает мусор после каждого теста.

    Тесты разбирают и собирают договоры по 2,8 МБ (docxtpl строит XML всего
    документа в памяти), и без явной сборки накопленные объекты python-docx
    держат память до конца сессии: на слабых машинах это заканчивается
    MemoryError внутри docxtpl.
    """
    yield
    gc.collect()


def body_items(doc):
    """Элементы тела документа по порядку: ('p', Paragraph) или ('tbl', Table)."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    items = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.split("}")[1]
        if tag == "p":
            items.append(("p", Paragraph(child, doc)))
        elif tag == "tbl":
            items.append(("tbl", Table(child, doc)))
    return items


def vehicle_table_rows(table) -> list:
    """[(№, марка, VIN), ...] — строки таблицы без строки заголовка."""
    rows = []
    for row in table.rows[1:]:
        cells = [cell.text.strip() for cell in row.cells]
        rows.append(tuple(cells[:3]))
    return rows


def is_vehicle_table(table) -> bool:
    """Таблица формата ТС: три колонки, шапка «№ / Марка-Модель / VIN-номер»."""
    if len(table.columns) != 3 or not table.rows:
        return False
    return [cell.text.strip() for cell in table.rows[0].cells] == [
        "№", "Марка/Модель", "VIN-номер"
    ]


def is_route_table(table) -> bool:
    """
    Таблица, созданная генератором для погрузки/выгрузки.

    От таблицы ТС из шаблона (стиль Normal Table) отличается стилем:
    генератор ставит Table Grid — то есть таблицу с границами.
    """
    if not is_vehicle_table(table):
        return False
    return table.style is not None and table.style.name == ContractGenerator.VEHICLE_TABLE_STYLE


def section_items(doc, start_prefix: str, end_prefix: str) -> list:
    """Элементы тела между абзацами-заголовками start_prefix и end_prefix."""
    items = body_items(doc)
    start = end = None

    for index, (kind, item) in enumerate(items):
        if kind != "p":
            continue
        text = item.text.strip()
        if start is None and text.startswith(start_prefix):
            start = index
        elif start is not None and text.startswith(end_prefix):
            end = index
            break

    assert start is not None, f"не найден раздел {start_prefix}"
    assert end is not None, f"не найден раздел {end_prefix}"
    return items[start:end]


def vins_in(items) -> list:
    """VIN-ы из всех таблиц переданного среза тела документа."""
    vins = []
    for kind, item in items:
        if kind != "tbl":
            continue
        table = item
        vins.extend(row.cells[2].text.strip() for row in table.rows[1:])
    return vins


def tables_in(items) -> list:
    """Все таблицы переданного среза тела документа."""
    return [item for kind, item in items if kind == "tbl"]


#: Раздел 3.5 «Информация об исполнителе»: таблица «водитель | ТС».
EXECUTOR_SECTION = "3.5."
EXECUTOR_NEXT_SECTION = "3.6."


def usable_width_cm(doc) -> float:
    """Ширина полосы набора первой секции документа (страница минус поля)."""
    from docx.shared import Emu

    section = doc.sections[0]
    return Emu(section.page_width - section.left_margin - section.right_margin).cm


def executor_table(doc):
    """Таблица раздела 3.5 (слева водитель, справа транспортное средство)."""
    for kind, item in section_items(doc, EXECUTOR_SECTION, EXECUTOR_NEXT_SECTION):
        if kind == "tbl":
            return item
    return None


def table_after(doc, heading: str):
    """
    Таблица, идущая сразу после абзаца с указанным заголовком.

    Пустые абзацы между заголовком и таблицей пропускаются.
    """
    items = body_items(doc)
    for index, (kind, item) in enumerate(items):
        if kind != "p" or item.text.strip() != heading:
            continue
        for next_kind, next_item in items[index + 1:]:
            if next_kind == "tbl":
                return next_item
            if next_item.text.strip():
                break  # между заголовком и таблицей есть осмысленный текст
        break
    return None


#: Префиксы заголовков, после которых генератор ставит свои таблицы.
ROUTE_HEADING_PREFIXES = (
    "Погрузка ",
    "Выгрузка ",
    "Машины без привязки",
)


def route_tables(doc) -> list:
    """
    Таблицы, вставленные генератором в блоки 3.2 / 3.3.

    Ищем таблицу, которая идёт сразу после абзаца-заголовка («Погрузка 1: …»,
    «Машины без привязки…») и оформлена стилем Table Grid: таблица ТС из
    шаблона выглядит так же, но со стилем Normal Table.
    """
    return [table for _, table in route_table_positions(doc)]


def route_table_positions(doc) -> list:
    """[(позиция в теле документа, таблица), ...] для таблиц блоков 3.2 / 3.3."""
    items = body_items(doc)
    result = []
    for index, (kind, item) in enumerate(items):
        if kind != "p":
            continue
        heading = item.text.strip()
        if not heading.startswith(ROUTE_HEADING_PREFIXES) or heading.startswith("3."):
            continue
        for offset, (next_kind, next_item) in enumerate(items[index + 1:], start=index + 1):
            if next_kind == "tbl":
                if is_route_table(next_item):
                    result.append((offset, next_item))
                break
            if next_item.text.strip():
                break
    return result


def headings(doc, prefix: str) -> list:
    """Тексты абзацев, начинающихся с префикса."""
    return [
        paragraph.text.strip() for paragraph in doc.paragraphs
        if paragraph.text.strip().startswith(prefix)
    ]


def make_marker_template(source, target):
    """
    Копия шаблона, где старые плейсхолдеры {{loading_block}} и
    {{unloading_block}} заменены на метки {{LOADING_TABLE_HERE}} и
    {{UNLOADING_TABLE_HERE}}.

    Так проверяется новый путь, хотя сами templates/*.docx правит
    пользователь в Word.
    """
    doc = Document(str(source))

    replacements = {
        "{{loading_block}}": "{{%s}}" % LOADING_PLACEHOLDER,
        "{{unloading_block}}": "{{%s}}" % UNLOADING_PLACEHOLDER,
    }

    for paragraph in doc.paragraphs:
        marker = replacements.get(paragraph.text.strip())
        if marker is None:
            continue
        for position, run in enumerate(paragraph.runs):
            run.text = marker if position == 0 else ""

    target.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(target))
    return target


def make_legacy_template(source, target):
    """
    Копия шаблона со СТАРЫМИ плейсхолдерами {{loading_block}} /
    {{unloading_block}} (обратная замена меток таблиц).

    Нужна для проверки fallback: рабочие templates/*.docx уже переведены
    на метки, а шаблоны старых версий должны продолжать работать.
    """
    doc = Document(str(source))

    replacements = {
        "{{%s}}" % LOADING_PLACEHOLDER: "{{loading_block}}",
        "{{%s}}" % UNLOADING_PLACEHOLDER: "{{unloading_block}}",
    }

    for paragraph in doc.paragraphs:
        legacy = replacements.get(paragraph.text.strip())
        if legacy is None:
            continue
        for position, run in enumerate(paragraph.runs):
            run.text = legacy if position == 0 else ""

    target.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(target))
    return target


def strip_embedded_fonts(source, target):
    """
    Копия шаблона без встроенных шрифтов (word/fonts/*.odttf).

    В шаблоне 4,3 МБ из 5,3 МБ — это встроенные TTF. Каждый тест разбирает
    шаблон целиком, и на них уходит память и время; на проверяемую логику
    (метки, таблицы, привязка машин) шрифты не влияют. Оригинальные
    templates/*.docx не изменяются.

    Вместе со шрифтами вычищаются ссылки на них (document.xml.rels и
    fontTable.xml.rels): битая ссылка не даст python-docx открыть пакет.
    """
    import zipfile

    with zipfile.ZipFile(str(source)) as archive:
        items = [(info, archive.read(info.filename)) for info in archive.infolist()]

    with zipfile.ZipFile(str(target), "w", zipfile.ZIP_DEFLATED) as out:
        for info, data in items:
            name = info.filename
            if name.startswith("word/fonts/"):
                continue  # сами шрифты не нужны
            if name.endswith(".rels"):
                # убираем relationship'ы, ведущие в word/fonts/
                data = re.sub(
                    rb"<Relationship\b[^>]*Target=\"fonts/[^\"]*\"[^>]*/>", b"", data
                )
            if name == "word/settings.xml":
                # и признак «встраивать шрифты», иначе Word ищет удалённые части
                data = re.sub(rb"<w:embedTrueTypeFonts[^>]*/>", b"", data)
                data = re.sub(rb"<w:saveSubsetFonts[^>]*/>", b"", data)
            out.writestr(info, data)

    return target


def _isolated_template_dir(work_dir, request, prefix: str, source, builder):
    """Копия шаблона ООО в отдельной папке под штатным именем + уборка."""
    target_dir = work_dir / (prefix + re.sub(r"\W", "", request.node.name)[:40])
    shutil.rmtree(target_dir, ignore_errors=True)
    request.addfinalizer(lambda: shutil.rmtree(target_dir, ignore_errors=True))

    builder(source, target_dir / "shablon_ooo.docx")
    return target_dir


@pytest.fixture(scope="session")
def light_templates_dir(templates_dir):
    """
    Папка с облегчёнными копиями всех трёх шаблонов (без встроенных шрифтов).

    Готовится один раз на сессию: копия тяжёлая, а тестам нужна только
    структура документа.
    """
    target_dir = Path(tempfile.mkdtemp(prefix="light_templates_"))
    for name in TEMPLATES:
        strip_embedded_fonts(templates_dir / name, target_dir / name)
    yield target_dir
    shutil.rmtree(target_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def marker_templates_dir(light_templates_dir):
    """Папка с облегчённым шаблоном ООО, переведённым на метки таблиц."""
    target_dir = Path(tempfile.mkdtemp(prefix="marker_templates_"))
    make_marker_template(
        light_templates_dir / "shablon_ooo.docx", target_dir / "shablon_ooo.docx"
    )
    yield target_dir
    shutil.rmtree(target_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def legacy_templates_dir(light_templates_dir):
    """Папка с облегчённым шаблоном ООО старого образца (плоские блоки)."""
    target_dir = Path(tempfile.mkdtemp(prefix="legacy_templates_"))
    make_legacy_template(
        light_templates_dir / "shablon_ooo.docx", target_dir / "shablon_ooo.docx"
    )
    yield target_dir
    shutil.rmtree(target_dir, ignore_errors=True)


@pytest.fixture
def generator(templates_dir) -> ContractGenerator:
    return ContractGenerator(templates_dir=str(templates_dir))


@pytest.fixture
def light_generator(light_templates_dir) -> ContractGenerator:
    """Генератор на облегчённых копиях шаблонов — быстрее и легче по памяти."""
    return ContractGenerator(templates_dir=str(light_templates_dir))


@pytest.fixture
def marker_generator(marker_templates_dir) -> ContractGenerator:
    """
    Генератор на копии шаблона ООО с метками таблиц погрузок/выгрузок.

    Копия лежит в отдельной папке под штатным именем: генератор ищет
    шаблон по имени shablon_ooo.docx. Сами templates/*.docx не меняются.
    """
    return ContractGenerator(templates_dir=str(marker_templates_dir))


@pytest.fixture
def legacy_generator(legacy_templates_dir) -> ContractGenerator:
    """Генератор на копии шаблона ООО со старыми плоскими блоками."""
    return ContractGenerator(templates_dir=str(legacy_templates_dir))


@pytest.fixture
def route_payload() -> dict:
    """
    Синтетический маршрут: 3 погрузки (2+2+1), 2 выгрузки (3+2),
    одна машина без привязки, пустой адрес погрузки, тягач и полуприцеп.
    """
    return {
        "contract": {
            "number": "TEST-ROUTE",
            "date": "2026-09-23",
            "carrier_type": "ООО (с НДС)",
            "vat_rate_num": 22,
            "price_without_vat": 1000.0,
            "loading_plan_date": "2026-09-30",
            "loading_plan_time_from": "08:00",
            "loading_plan_time_to": "20:00",
        },
        "carrier": {"full_name": "ООО «Тест»", "short_name": "ООО «Тест»"},
        "customer": {"full_name": "ООО «Заказчик»", "short_name": "ООО «Заказчик»"},
        "vehicles": [
            {"brand_model": "JETOUR T1 2.0T 8AT Премиум", "vin": "LVTDD24B1TDC49340",
             "vehicle_type": "Легковой автомобиль", "loading_index": 1,
             "unloading_index": 2},
            {"brand_model": "JETOUR T1 2.0T 8AT Премиум", "vin": "LVTDD24B0TDC38491",
             "vehicle_type": "Легковой автомобиль", "loading_index": 1,
             "unloading_index": 2},
            {"brand_model": "JETOUR DASHING 1.5T 6DCT Комфорт", "vin": "EC3DCUFD9TC018068",
             "vehicle_type": "Легковой автомобиль", "loading_index": 2,
             "unloading_index": 2},
            {"brand_model": "JETOUR X70PLUS 1.6T 7DCT Комфорт", "vin": "EC37CUSM7TC008397",
             "vehicle_type": "Легковой автомобиль", "loading_index": 2,
             "unloading_index": 1},
            {"brand_model": "SOUEAST S06 1.6T 8AT Престиж", "vin": "EC3DCUGA6TC001331",
             "vehicle_type": "Легковой автомобиль", "loading_index": 3,
             "unloading_index": 1},
            {"brand_model": "SOUEAST S06 1.6T 8AT Престиж", "vin": "EC3DCUGA1TC001429",
             "vehicle_type": "Легковой автомобиль", "loading_index": 0,
             "unloading_index": 1},
        ],
        "tractor": {"brand_model": "Foton Auman", "plate_number": "O844XY196",
                    "color": "Белый", "year": 2023},
        "trailer": {"brand_model": "YANGMINDA", "plate_number": "71ABF18",
                    "color": "Серый", "year": 2020},
        "loadings": [
            {"address": "Московская область, г Чехов, д Люторецкое, влд. 4",
             "date": "2026-09-30", "time_window": "08:00-20:00"},
            {"address": "РФ, Московская область, г.Клин, кадастровый номер 50:03:0040280:8419",
             "date": "2026-09-30", "time_window": ""},
            {"address": "ТЛЦ «Белый Раст», Московская обл., Дмитровский район",
             "date": "2026-09-30", "time_window": ""},
            # Пустая погрузка: адрес есть, машин нет — блок пропускается.
            {"address": "Пустая погрузка без машин", "date": "2026-09-30",
             "time_window": ""},
        ],
        "unloadings": [
            {"address": "Выгрузка один, склад А", "date": "2026-10-02", "time_window": ""},
            {"address": "Выгрузка два, склад Б", "date": "2026-10-03", "time_window": ""},
        ],
    }


@pytest.fixture
def gap_payload(route_payload) -> dict:
    """
    Маршрут с «дыркой» — как в UI: точка с пустым адресом стоит в середине.

    Погрузки: 1) Точка 1, 2) пустая, 3) Точка 3 — машины привязаны к точкам
    1 и 3 (loading_index = 1 и 3).
    Выгрузки: 1, 2, 3 (пустая), 4 — машины привязаны к 1, 2 и 4
    (unloading_index = 4 у машины VIN ...004).

    На таком наборе видно и «дырку» в нумерации, и сдвиг привязки: если
    нумеровать блоки по исходному индексу, третий блок выгрузки получит
    номер 4, а машина из точки 4 уедет в чужую таблицу.
    """
    payload = dict(route_payload)

    payload["loadings"] = [
        {"address": "Точка 1", "date": "2026-09-30", "time_window": "08:00"},
        {"address": "", "date": "2026-09-30", "time_window": ""},
        {"address": "Точка 3", "date": "2026-09-30", "time_window": ""},
    ]
    payload["unloadings"] = [
        {"address": "Выгрузка 1", "date": "2026-10-02", "time_window": ""},
        {"address": "Выгрузка 2", "date": "2026-10-03", "time_window": ""},
        {"address": "", "date": "2026-10-04", "time_window": ""},
        {"address": "Выгрузка 4", "date": "2026-10-05", "time_window": ""},
    ]

    # VIN-ы синтетические, но валидные по формату: 17 символов, без I/O/Q.
    # Точки 1 и 3 — по одной машине; точка 3 выгрузки (исходный индекс 4) —
    # одна машина, чтобы сдвиг привязки был виден сразу.
    payload["vehicles"] = [
        {"brand_model": "МОДЕЛЬ 1", "vin": "EC3TEUMB0T0000001",
         "vehicle_type": "Легковой автомобиль", "loading_index": 1,
         "unloading_index": 1},
        {"brand_model": "МОДЕЛЬ 2", "vin": "EC3TEUMB0T0000002",
         "vehicle_type": "Легковой автомобиль", "loading_index": 3,
         "unloading_index": 2},
        {"brand_model": "МОДЕЛЬ 3", "vin": "EC3TEUMB0T0000003",
         "vehicle_type": "Легковой автомобиль", "loading_index": 3,
         "unloading_index": 4},
    ]

    return payload


# ─────────────────────────────────────────────────────────────
# Главный тест: покрытие плейсхолдеров шаблонов
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("template_name", TEMPLATES)
def test_all_template_placeholders_are_covered(generator, templates_dir, template_name):
    """
    Каждый плейсхолдер шаблона должен присутствовать в карте замен.

    Если в шаблон добавят переменную, а в генератор — нет, тест упадёт:
    именно так был найден баг с tractor_brand/trailer_brand.

    Работает и для шаблона нового образца с метками {{LOADING_TABLE_HERE}} /
    {{UNLOADING_TABLE_HERE}}: генератор отдаёт для них текстовые маркеры,
    по которым постобработка вставляет таблицы.
    """
    placeholders = template_placeholders(templates_dir / template_name)
    assert placeholders, f"в шаблоне {template_name} не найдено плейсхолдеров"

    replacements = generator._build_replacements_map(ContractData())
    missing = sorted(placeholders - set(replacements))
    assert not missing, f"{template_name}: нет значений для {missing}"

    # Если шаблон уже переведён на метки — значения обязаны быть маркерами,
    # а не пустой строкой: иначе постобработка не найдёт место вставки.
    if LOADING_PLACEHOLDER in placeholders:
        assert replacements[LOADING_PLACEHOLDER] == ContractGenerator.LOADING_TABLE_MARKER
    if UNLOADING_PLACEHOLDER in placeholders:
        assert replacements[UNLOADING_PLACEHOLDER] == ContractGenerator.UNLOADING_TABLE_MARKER


def test_marker_values_are_not_jinja_syntax(generator):
    """
    Маркеры таблиц не должны содержать {{ }}: иначе docxtpl попытается
    отрендерить их повторно и сломает шаблон.
    """
    replacements = generator._build_replacements_map(ContractData())

    for name in (LOADING_PLACEHOLDER, UNLOADING_PLACEHOLDER):
        value = replacements[name]
        assert "{{" not in value and "}}" not in value
        assert value.strip() == value != ""


def test_legacy_blocks_still_built(generator, contract_payload):
    """Старый путь (плоские блоки) не удалён — шаблоны без метки работают."""
    replacements = generator._build_replacements_map(contract_payload)
    for name in LEGACY_BLOCK_NAMES:
        assert name in replacements


# ─────────────────────────────────────────────────────────────
# Переносы в шаблонах: текст по ширине не растягивает пробелы
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("template_name", TEMPLATES)
def test_templates_have_hyphenation_enabled(light_templates_dir, template_name):
    """
    В шаблоне включена автоматическая расстановка переносов.

    Абзацы договора выровнены по ширине (w:jc="both"). Без переносов Word
    переносит длинное слово («"ТЕХНОЛОГИСТИКА"») целиком и растягивает
    пробелы в предыдущей строке: «Общество     с     ограниченной…».
    """
    doc = Document(str(light_templates_dir / template_name))
    settings = doc.settings.element

    auto = settings.find(W_NS + "autoHyphenation")
    assert auto is not None, "в шаблоне не включены автопереносы"
    assert auto.get(W_NS + "val") in ("true", "1", "on")

    assert settings.find(W_NS + "hyphenationZone") is not None, (
        "не задана зона переноса"
    )
    assert settings.find(W_NS + "consecutiveHyphenLimit") is not None, (
        "не ограничено число переносов подряд"
    )


def test_generated_document_keeps_hyphenation(marker_generator, route_payload,
                                              work_file):
    """Готовый договор наследует настройку переносов из шаблона."""
    output = work_file("hyphenation.docx")
    marker_generator.generate_docx(route_payload, str(output))

    auto = Document(output).settings.element.find(W_NS + "autoHyphenation")
    assert auto is not None, "в готовом договоре автопереносы потерялись"
    assert auto.get(W_NS + "val") in ("true", "1", "on")


def test_templates_keep_justified_text(light_templates_dir):
    """Выравнивание по ширине осталось — переносы его дополняют, а не заменяют."""
    doc = Document(str(light_templates_dir / "shablon_ooo.docx"))
    both = 0
    for paragraph in doc.paragraphs:
        paragraph_props = paragraph._p.find(W_NS + "pPr")
        align = (
            paragraph_props.find(W_NS + "jc") if paragraph_props is not None else None
        )
        if align is not None and align.get(W_NS + "val") == "both":
            both += 1
    assert both > 50, f"абзацев по ширине всего {both} — вёрстка шаблона изменилась"


# ─────────────────────────────────────────────────────────────
# Однострочные значения: переносы из справочников не рвут абзац
# ─────────────────────────────────────────────────────────────

def test_single_line_helper(generator):
    """Переносы, табуляции и лишние пробелы схлопываются в один пробел."""
    assert generator._single_line("Общество\nс ограниченной") == \
        "Общество с ограниченной"
    assert generator._single_line("ООО\r\n«Тест»") == "ООО «Тест»"
    assert generator._single_line("два  пробела") == "два пробела"
    assert generator._single_line("таб\tтаб") == "таб таб"
    assert generator._single_line("  обрезать  ") == "обрезать"
    assert generator._single_line(None) == ""
    assert generator._single_line(123) == "123"


def test_replacements_are_flattened(generator, contract_payload):
    """
    Карта замен не содержит переносов строк.

    Названия и адреса приходят из справочника/импорта с «\\n» внутри:
    «Общество с ограниченной ответственностью\\n"ТЕХНОЛОГИСТИКА"».
    """
    payload = dict(contract_payload)
    payload["customer"] = dict(
        contract_payload["customer"],
        full_name='Общество с ограниченной ответственностью\n"ТЕХНОЛОГИСТИКА"',
        short_name='ООО "ТЕХНОЛОГИСТИКА"',
        legal_address="101000, Город Москва,\nвн.тер.г. Красносельский, пер Уланский, д. 22",
    )
    payload["carrier"] = dict(
        contract_payload["carrier"],
        full_name="ООО  «Двойной  пробел»",
        bank_name="ПАО\tСбербанк",
    )

    replacements = generator._build_replacements_map(payload)

    for name in ("client_full_name", "client_short_name", "client_address",
                 "carrier_full_name", "carrier_bank"):
        value = replacements.get(name, "")
        assert "\n" not in value and "\r" not in value and "\t" not in value
        assert "  " not in value

    assert replacements["client_full_name"] == \
        'Общество с ограниченной ответственностью "ТЕХНОЛОГИСТИКА"'
    assert replacements["carrier_full_name"] == "ООО «Двойной пробел»"
    assert replacements["carrier_bank"] == "ПАО Сбербанк"

    # многострочный legacy-блок не тронут: там переносы несут смысл
    assert "\n" in replacements["loading_block"]


def test_client_name_has_no_line_break_in_document(marker_generator,
                                                   contract_payload, work_file):
    """
    В готовом договоре название заказчика — одной строкой.

    Регресс: перенос из значения docxtpl вставлял текстом, постобработка
    превращала его в <w:br/>, и Word при выравнивании по ширине растягивал
    строку перед разрывом — «Общество   с   ограниченной   ответственностью».
    """
    payload = dict(contract_payload)
    payload["customer"] = dict(
        contract_payload["customer"],
        full_name='Общество с ограниченной ответственностью\n"ТЕХНОЛОГИСТИКА"',
        short_name='ООО "ТЕХНОЛОГИСТИКА"',
    )

    output = work_file("flatten.docx")
    marker_generator.generate_docx(payload, str(output))

    doc = Document(output)
    paragraph = next(
        item for item in doc.paragraphs
        if item.text.strip().startswith("Общество с ограниченной")
    )

    assert "\n" not in paragraph.text
    assert "ответственностью ТЕХНОЛОГИСТИКА" in paragraph.text.replace('"', "")
    assert "<w:br" not in paragraph._p.xml, "в абзаце остался принудительный разрыв"


# ─────────────────────────────────────────────────────────────
# Метка в шаблоне → таблицы по погрузкам / выгрузкам
# ─────────────────────────────────────────────────────────────

def test_marker_template_renders_without_raw_placeholders(marker_generator,
                                                          route_payload, work_file):
    """Метки шаблона исчезают: сырых {{...}} и текстовых маркеров нет."""
    output = work_file("marker_clean.docx")
    marker_generator.generate_docx(route_payload, str(output))

    assert placeholders_left(output) == []
    xml = document_xml(output)
    assert LOADING_PLACEHOLDER not in xml
    assert UNLOADING_PLACEHOLDER not in xml


def test_loading_tables_by_points(marker_generator, route_payload, work_file):
    """Каждая непустая погрузка — отдельная таблица со своими машинами."""
    output = work_file("loading_tables.docx")
    marker_generator.generate_docx(route_payload, str(output))

    doc = Document(output)

    first = table_after(
        doc, "Погрузка 1: Московская область, г Чехов, д Люторецкое, влд. 4"
    )
    second = table_after(
        doc,
        "Погрузка 2: РФ, Московская область, г.Клин, кадастровый номер 50:03:0040280:8419",
    )
    third = table_after(
        doc, "Погрузка 3: ТЛЦ «Белый Раст», Московская обл., Дмитровский район"
    )

    assert first is not None, "нет таблицы после «Погрузка 1»"
    assert second is not None, "нет таблицы после «Погрузка 2»"
    assert third is not None, "нет таблицы после «Погрузка 3»"

    assert vehicle_table_rows(first) == [
        ("1", "JETOUR T1 2.0T 8AT Премиум", "LVTDD24B1TDC49340"),
        ("2", "JETOUR T1 2.0T 8AT Премиум", "LVTDD24B0TDC38491"),
    ]
    assert [row[2] for row in vehicle_table_rows(second)] == [
        "EC3DCUFD9TC018068", "EC37CUSM7TC008397",
    ]
    assert [row[2] for row in vehicle_table_rows(third)] == ["EC3DCUGA6TC001331"]


def test_empty_point_skipped(marker_generator, route_payload, work_file):
    """Погрузка без машин не выводится: ни заголовка, ни таблицы."""
    output = work_file("empty_point.docx")
    marker_generator.generate_docx(route_payload, str(output))

    text = document_text(output)
    assert "Пустая погрузка без машин" not in text
    assert "Погрузка 4" not in text


# ─────────────────────────────────────────────────────────────
# Нумерация блоков при пустых точках и привязка машин
# ─────────────────────────────────────────────────────────────

def test_skipped_empty_point_keeps_numbering(marker_generator, gap_payload, work_file):
    """
    Пропуск пустой точки не оставляет «дырки» в нумерации.

    Три погрузки, вторая пустая: заголовки должны быть «Погрузка 1» и
    «Погрузка 2» (то есть 1 и 2, а не 1 и 3).
    """
    output = work_file("numbering.docx")
    marker_generator.generate_docx(gap_payload, str(output))

    titles = headings(Document(output), "Погрузка ")
    assert [title.split(":")[0] for title in titles] == ["Погрузка 1", "Погрузка 2"]


def test_vehicle_bound_to_empty_point_not_shown(marker_generator, gap_payload,
                                                 work_file, caplog):
    """
    Машина, привязанная к точке с пустым адресом, не выводится ни в одной
    таблице погрузок, но о ней есть предупреждение в логе (без VIN).

    Выгрузки при этом не затрагиваются: у машины своя привязка по
    unloading_index, и её точка выгрузки адрес имеет.
    """
    payload = dict(gap_payload)
    # К пустой точке 2 привязаны две машины, к точке 1 — одна.
    payload["vehicles"] = [
        dict(gap_payload["vehicles"][0], loading_index=1),
        dict(gap_payload["vehicles"][1], loading_index=2),
        dict(gap_payload["vehicles"][2], loading_index=2),
    ]

    output = work_file("empty_address.docx")
    with caplog.at_level("DEBUG", logger="core.contract_generator"):
        marker_generator.generate_docx(payload, str(output))

    doc = Document(output)

    # таблицы блока 3.2: только машина точки 1
    loading_titles = headings(doc, "Погрузка ")
    assert [title.split(":")[0] for title in loading_titles] == ["Погрузка 1"]
    assert [row[2] for row in vehicle_table_rows(
        table_after(doc, loading_titles[0])
    )] == ["EC3TEUMB0T0000001"]

    # машины пустой точки не появились ни в одной таблице погрузок…
    assert "Машины без привязки к конкретной погрузке" not in document_text(output)

    # …но о них сообщено: количество без VIN
    assert "адрес точки пуст" in caplog.text
    assert "строк не выведено: 2" in caplog.text
    assert "EC3TEUMB0T0000002" not in caplog.text
    assert "EC3TEUMB0T0000003" not in caplog.text


def test_unloading_numbering_matches_ui_order(marker_generator, gap_payload, work_file):
    """
    Четыре выгрузки, третья пустая: заголовки идут 1, 2, 3 без пропусков.

    Машина, привязанная в UI к выгрузке 4, обязана оказаться в блоке
    «Выгрузка 3» — её исходный индекс 4, а третий видимый блок.
    """
    output = work_file("unloading_order.docx")
    marker_generator.generate_docx(gap_payload, str(output))

    doc = Document(output)
    titles = headings(doc, "Выгрузка ")
    assert [title.split(":")[0] for title in titles] == [
        "Выгрузка 1", "Выгрузка 2", "Выгрузка 3",
    ]

    # Выгрузка 3 в UI = четвёртая точка (после пустой третьей)
    third = table_after(doc, titles[2])
    assert third is not None
    assert [row[2] for row in vehicle_table_rows(third)] == ["EC3TEUMB0T0000003"]


def test_loading_index_not_shifted_by_empty_points(marker_generator, gap_payload,
                                                    work_file):
    """
    loading_index не сдвигается: машины с индексом 3 идут в точку 3,
    даже если точка 2 пустая и её блок не выведен.
    """
    output = work_file("not_shifted.docx")
    marker_generator.generate_docx(gap_payload, str(output))

    doc = Document(output)
    # Точка 3 (третий блок UI) выведена второй по счёту — «Погрузка 2»
    second = table_after(doc, "Погрузка 2: Точка 3")
    assert second is not None, "нет таблицы после «Погрузка 2: Точка 3»"
    assert [row[2] for row in vehicle_table_rows(second)] == [
        "EC3TEUMB0T0000002", "EC3TEUMB0T0000003",
    ]

    # и машины точки 3 не уехали в первую таблицу (точка 1)
    first = table_after(doc, "Погрузка 1: Точка 1")
    assert [row[2] for row in vehicle_table_rows(first)] == ["EC3TEUMB0T0000001"]


def test_vehicle_bound_to_missing_point_goes_to_unassigned(marker_generator,
                                                           gap_payload, work_file):
    """
    Привязка на несуществующую точку не теряет машину молча: она попадает
    в блок «Машины без привязки к конкретной погрузке».
    """
    payload = dict(gap_payload)
    payload["loadings"] = gap_payload["loadings"][:2]  # точек 2, а привязка — 3
    payload["vehicles"] = [
        dict(gap_payload["vehicles"][0], loading_index=1),
        dict(gap_payload["vehicles"][1], loading_index=3),
        dict(gap_payload["vehicles"][2], loading_index=3),
    ]

    output = work_file("missing_point.docx")
    marker_generator.generate_docx(payload, str(output))

    doc = Document(output)
    table = table_after(doc, "Машины без привязки к конкретной погрузке")
    assert table is not None, "машина с несуществующей точкой потерялась"
    assert [row[2] for row in vehicle_table_rows(table)] == [
        "EC3TEUMB0T0000002", "EC3TEUMB0T0000003",
    ]


def test_all_points_empty_message_keeps_numbering_free(marker_generator, gap_payload,
                                                        work_file):
    """Все точки пустые — одна строка «(машины не указаны)» в каждом блоке."""
    payload = dict(gap_payload)
    payload["vehicles"] = []

    output = work_file("all_empty.docx")
    marker_generator.generate_docx(payload, str(output))

    text = document_text(output)
    assert text.count(ContractGenerator.NO_VEHICLES_TEXT) == 2
    assert headings(Document(output), "Погрузка ") == []
    assert headings(Document(output), "Выгрузка ") == []


def test_vehicle_without_point_gets_own_block(marker_generator, route_payload, work_file):
    """loading_index == 0 («— (все)») — отдельный блок в конце 3.2."""
    output = work_file("unassigned.docx")
    marker_generator.generate_docx(route_payload, str(output))

    doc = Document(output)
    table = table_after(doc, "Машины без привязки к конкретной погрузке")

    assert table is not None, "нет блока «Машины без привязки к конкретной погрузке»"
    assert vehicle_table_rows(table) == [
        ("1", "SOUEAST S06 1.6T 8AT Престиж", "EC3DCUGA1TC001429"),
    ]


def test_unassigned_block_absent_when_all_vehicles_bound(marker_generator,
                                                         route_payload, work_file):
    """Если у всех машин задана погрузка — блока «без привязки» нет."""
    payload = dict(route_payload)
    payload["vehicles"] = [
        dict(vehicle, loading_index=1) for vehicle in route_payload["vehicles"]
    ]

    output = work_file("all_bound.docx")
    marker_generator.generate_docx(payload, str(output))

    assert "Машины без привязки к конкретной погрузке" not in document_text(output)


def test_all_points_empty_message(marker_generator, route_payload, work_file):
    """Все погрузки пустые — вместо таблиц строка «(машины не указаны)»."""
    payload = dict(route_payload)
    payload["vehicles"] = []  # ни привязанных машин, ни свободных

    output = work_file("no_vehicles.docx")
    marker_generator.generate_docx(payload, str(output))

    doc = Document(output)
    text = document_text(output)
    assert text.count(ContractGenerator.NO_VEHICLES_TEXT) == 2  # 3.2 и 3.3
    assert "Машины без привязки к конкретной погрузке" not in text
    assert route_tables(doc) == []


# ─────────────────────────────────────────────────────────────
# Пункт 3.5: водитель и транспортное средство в таблицах рядом
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("template_name", TEMPLATES)
def test_executor_section_is_two_column_table(light_templates_dir, template_name):
    """
    В разделе 3.5 каждого шаблона данные исполнителя лежат в таблице,
    а не отдельными абзацами: слева водитель, справа транспортное средство.
    """
    doc = Document(str(light_templates_dir / template_name))
    table = executor_table(doc)

    assert table is not None, "3.5: данные исполнителя не в таблице"
    assert len(table.columns) == 2, "в таблице 3.5 должно быть две колонки"
    assert table.rows[0].cells[0].text.strip() == "Водитель"
    assert "Транспортное средство" in table.rows[0].cells[1].text

    # слева — данные водителя, справа — тягач и прицеп
    left = table.rows[1].cells[0].text
    right = table.rows[1].cells[1].text
    for placeholder in ("{{driver_name}}", "{{driver_passport}}", "{{driver_phone}}"):
        assert placeholder in left, f"в левой колонке нет {placeholder}"
    for placeholder in ("{{tractor_brand}}", "{{trailer_brand}}", "{{trailer_plate}}"):
        assert placeholder in right, f"в правой колонке нет {placeholder}"


def test_executor_table_filled_with_driver_and_vehicle(marker_generator,
                                                       contract_payload, work_file):
    """В готовом договоре таблица 3.5 заполнена водителем и техникой."""
    output = work_file("executor.docx")
    marker_generator.generate_docx(contract_payload, str(output))

    doc = Document(output)
    table = executor_table(doc)
    assert table is not None, "3.5: таблица исполнителя не найдена"

    left = table.rows[1].cells[0].text
    right = table.rows[1].cells[1].text

    assert "Иванов Иван Иванович" in left
    assert "18 22 926830" in left          # паспорт
    assert "B, C, E" in left               # категории
    assert "+7 (999) 123-45-67" in left    # телефон

    assert "Foton Auman" in right          # тягач
    assert "O844XY196" in right
    assert "YANGMINDA" in right            # полуприцеп
    assert "71ABF18" in right

    # плоских абзацев с этими данными больше нет
    paragraphs = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    assert "Иванов Иван Иванович" not in paragraphs
    assert "Foton Auman" not in paragraphs
    assert "Водитель:" not in paragraphs


def test_executor_table_text_is_visible(marker_generator, contract_payload, work_file):
    """
    Таблица 3.5 читается: шапка — белая на тёмной заливке, данные — на светлой.

    Проверка та же, что и для таблиц погрузок: белый текст без заливки
    означал бы, что строку не видно.
    """
    output = work_file("executor_visible.docx")
    marker_generator.generate_docx(contract_payload, str(output))

    doc = Document(output)
    table = executor_table(doc)
    assert table is not None

    for row in table.rows:
        for cell in row.cells:
            color = cell_text_color(cell)
            if color is not None and color.upper() == "FFFFFF":
                assert cell_shading(cell), "белый текст в ячейке 3.5 без заливки"
            assert cell_has_borders(cell), "у ячейки 3.5 нет границ"

    for cell in table.rows[0].cells:
        assert cell.paragraphs[0].runs[0].bold, "шапка таблицы 3.5 должна быть жирной"

    # колонки одинаковой ширины и помещаются в полосу набора
    from docx.shared import Twips

    grid = table._tbl.find(W_NS + "tblGrid")
    widths = [int(col.get(W_NS + "w")) for col in grid]
    assert len(widths) == 2
    assert abs(widths[0] - widths[1]) <= 20, "колонки 3.5 разной ширины"
    assert Twips(sum(widths)).cm <= usable_width_cm(doc), "таблица 3.5 шире полосы набора"


# ─────────────────────────────────────────────────────────────
# Дубли VIN: старая таблица машин из шаблона в 3.2/3.3
# ─────────────────────────────────────────────────────────────

def test_legacy_vehicle_table_removed_from_route_sections(marker_generator,
                                                          route_payload, work_file):
    """
    В блоках 3.2/3.3 не остаётся таблицы машин из шаблона.

    В шаблоне таблиц машин две ({{car_1..12}}): в 3.1 «Груз» и сразу после
    метки в 3.2. Вторая дублировала таблицы по погрузкам — те же VIN
    печатались дважды в одном разделе.
    """
    output = work_file("dedup.docx")
    marker_generator.generate_docx(route_payload, str(output))

    doc = Document(output)
    route_section = section_items(doc, "3.2.", "3.4.")
    route_tables_found = tables_in(route_section)

    assert route_tables_found, "таблицы блоков 3.2/3.3 не найдены"
    for table in route_tables_found:
        assert is_route_table(table), (
            "в блоке 3.2/3.3 осталась таблица машин из шаблона (дубль)"
        )

    # и каждая машина блока 3.2 напечатана ровно один раз
    loading_vins = vins_in(section_items(doc, "3.2.", "3.3."))
    assert len(loading_vins) == len(set(loading_vins))


def test_cargo_vehicle_table_kept_in_section_31(marker_generator, route_payload,
                                                 work_file):
    """Таблица машин в 3.1 «Груз» остаётся: это перечень груза."""
    output = work_file("cargo_table.docx")
    marker_generator.generate_docx(route_payload, str(output))

    doc = Document(output)
    cargo_tables = [
        table for table in tables_in(section_items(doc, "3.1.", "3.2."))
        if is_vehicle_table(table)
    ]

    assert len(cargo_tables) == 1, "таблица перечня груза в 3.1 потеряна"
    assert not is_route_table(cargo_tables[0])

    # в перечне — все машины в порядке массива vehicles
    expected = [
        vehicle["vin"] for vehicle in route_payload["vehicles"]
    ]
    assert [row[2] for row in vehicle_table_rows(cargo_tables[0])] == expected


def test_unloading_section_has_no_legacy_table(marker_generator, gap_payload,
                                                work_file):
    """В блоке 3.3 тоже нет таблицы машин из шаблона."""
    output = work_file("unloading_dedup.docx")
    marker_generator.generate_docx(gap_payload, str(output))

    doc = Document(output)
    for table in tables_in(section_items(doc, "3.3.", "3.4.")):
        assert is_route_table(table), "в блоке 3.3 осталась таблица-дубль"

    unloading_vins = vins_in(section_items(doc, "3.3.", "3.4."))
    assert len(unloading_vins) == len(set(unloading_vins))


def test_legacy_table_removal_is_logged(marker_generator, route_payload, work_file,
                                        caplog):
    """Об удалении дублей сообщается в логе (без VIN и адресов)."""
    output = work_file("dedup_log.docx")

    with caplog.at_level("INFO", logger="core.contract_generator"):
        marker_generator.generate_docx(route_payload, str(output))

    assert "Удалено старых таблиц машин" in caplog.text
    for secret in ("Люторецкое", "LVTDD24B1TDC49340", "SOUEAST"):
        assert secret not in caplog.text, f"в лог попало: {secret}"


def test_fallback_keeps_legacy_vehicle_table(legacy_generator, contract_payload,
                                            work_file):
    """
    Старый шаблон (плоские блоки, без меток): таблица машин в 3.2 остаётся.

    Удалять её нельзя: там нет таблиц по погрузкам, и перечень машин иначе
    исчез бы из договора.
    """
    output = work_file("legacy_keeps.docx")
    legacy_generator.generate_docx(contract_payload, str(output))

    doc = Document(output)
    route_tables_found = [
        table for table in tables_in(section_items(doc, "3.2.", "3.3."))
        if is_vehicle_table(table)
    ]

    assert route_tables_found, "в старом шаблоне удалена таблица машин из 3.2"
    assert not is_route_table(route_tables_found[0])
    assert "EC3TEUMB0T0002608" in document_text(output)


def test_tractor_and_trailer_not_in_route_tables(marker_generator, route_payload,
                                                  work_file):
    """Тягач, полуприцеп и прицеп остаются в блоке 3.1, а не в таблицах."""
    payload = dict(route_payload)
    payload["vehicles"] = route_payload["vehicles"] + [
        {"brand_model": "Тягач Тест", "vin": "TRACTORVIN0000001",
         "vehicle_type": "Тягач", "loading_index": 1, "unloading_index": 1},
        {"brand_model": "Полуприцеп Тест", "vin": "TRAILERVIN0000001",
         "vehicle_type": "Полуприцеп", "loading_index": 1, "unloading_index": 1},
        {"brand_model": "Прицеп Тест", "vin": "PRITSEPVIN0000001",
         "vehicle_type": "Прицеп", "loading_index": 0, "unloading_index": 0},
    ]
    payload["tractor"] = {"brand_model": "Тягач Тест", "plate_number": "О001ТТ77"}
    payload["trailer"] = {"brand_model": "Полуприцеп Тест", "plate_number": "П002ПП77"}

    output = work_file("tractor.docx")
    marker_generator.generate_docx(payload, str(output))

    # ни в одной таблице блоков 3.2 / 3.3 нет техники из карточки ТС
    for table in route_tables(Document(output)):
        vins = [row[2] for row in vehicle_table_rows(table)]
        assert "TRACTORVIN0000001" not in vins
        assert "TRAILERVIN0000001" not in vins
        assert "PRITSEPVIN0000001" not in vins

    # а в блоке 3.5 (карточка ТС) тягач и полуприцеп на месте
    text = document_text(output)
    assert "Тягач: Тягач Тест" in text
    assert "Прицеп/Полуприцеп: Полуприцеп Тест" in text


def test_loading_table_preserves_vehicle_order(marker_generator, route_payload,
                                                work_file):
    """Машины в таблице идут в порядке массива vehicles, без сортировки."""
    payload = dict(route_payload)
    payload["vehicles"] = [
        {"brand_model": "МОДЕЛЬ Я", "vin": "ZZZZZZZZZZZZZZZ01",
         "vehicle_type": "Легковой автомобиль", "loading_index": 1},
        {"brand_model": "МОДЕЛЬ А", "vin": "AAAAAAAAAAAAAAA01",
         "vehicle_type": "Легковой автомобиль", "loading_index": 1},
        {"brand_model": "МОДЕЛЬ Б", "vin": "BBBBBBBBBBBBBBB01",
         "vehicle_type": "Легковой автомобиль", "loading_index": 1},
    ]

    output = work_file("order.docx")
    marker_generator.generate_docx(payload, str(output))

    table = table_after(
        Document(output),
        "Погрузка 1: Московская область, г Чехов, д Люторецкое, влд. 4",
    )
    assert [row[1] for row in vehicle_table_rows(table)] == [
        "МОДЕЛЬ Я", "МОДЕЛЬ А", "МОДЕЛЬ Б",
    ]


def test_unloading_tables_by_points(marker_generator, route_payload, work_file):
    """Блок 3.3 устроен так же, как 3.2, но по unloading_index."""
    output = work_file("unloading_tables.docx")
    marker_generator.generate_docx(route_payload, str(output))

    doc = Document(output)

    first = table_after(doc, "Выгрузка 1: Выгрузка один, склад А")
    second = table_after(doc, "Выгрузка 2: Выгрузка два, склад Б")

    assert first is not None, "нет таблицы после «Выгрузка 1»"
    assert second is not None, "нет таблицы после «Выгрузка 2»"

    assert [row[2] for row in vehicle_table_rows(first)] == [
        "EC37CUSM7TC008397", "EC3DCUGA6TC001331", "EC3DCUGA1TC001429",
    ]
    assert [row[2] for row in vehicle_table_rows(second)] == [
        "LVTDD24B1TDC49340", "LVTDD24B0TDC38491", "EC3DCUFD9TC018068",
    ]


def test_tables_belong_to_their_sections(marker_generator, route_payload, work_file):
    """Таблицы погрузок и выгрузок стоят внутри блоков 3.2 и 3.3."""
    output = work_file("sections.docx")
    marker_generator.generate_docx(route_payload, str(output))

    doc = Document(output)
    items = body_items(doc)
    positions = {}
    for index, (kind, item) in enumerate(items):
        if kind == "p" and item.text.strip()[:3] in ("3.2", "3.3", "3.4"):
            positions[item.text.strip()[:3]] = index

    assert set(positions) >= {"3.2", "3.3", "3.4"}, "не найдены разделы 3.2–3.4"

    placed = route_table_positions(doc)
    assert placed, "таблицы погрузок/выгрузок не найдены"

    for index, _table in placed:
        assert positions["3.2"] < index < positions["3.4"], (
            "таблица попала вне блоков 3.2 / 3.3"
        )


def test_route_table_columns_and_widths(marker_generator, route_payload, work_file):
    """Колонки таблиц: № / Марка-Модель / VIN-номер, ширины 1 / 9 / 5 см."""
    from docx.shared import Twips

    output = work_file("columns.docx")
    marker_generator.generate_docx(route_payload, str(output))

    doc = Document(output)
    tables = route_tables(doc)
    assert tables

    for table in tables:
        assert [cell.text.strip() for cell in table.rows[0].cells] == [
            "№", "Марка/Модель", "VIN-номер"
        ]
        for cell in table.rows[0].cells:
            run = cell.paragraphs[0].runs[0]
            assert run.bold, "шапка таблицы должна быть жирной"
            assert cell.paragraphs[0].alignment == 1, "шапка выровнена по центру"

        grid = table._tbl.find(W_NS + "tblGrid")
        widths = [int(col.get(W_NS + "w")) for col in grid]
        assert [round(Twips(w).cm, 2) for w in widths] == [1.0, 9.0, 5.0]
        # суммарная ширина не выходит за пределы полосы набора
        assert Twips(sum(widths)).cm <= usable_width_cm(doc)


#: Пространство имён WordprocessingML для разбора оформления ячеек.
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def cell_shading(cell):
    """Заливка ячейки (w:shd/@w:fill) или None."""
    tc_pr = cell._tc.find(W_NS + "tcPr")
    shd = tc_pr.find(W_NS + "shd") if tc_pr is not None else None
    return shd.get(W_NS + "fill") if shd is not None else None


def cell_text_color(cell):
    """Цвет текста в ячейке (w:color/@w:val) или None, если не задан."""
    if not cell.paragraphs[0].runs:
        return None
    rpr = cell.paragraphs[0].runs[0]._r.find(W_NS + "rPr")
    color = rpr.find(W_NS + "color") if rpr is not None else None
    return color.get(W_NS + "val") if color is not None else None


def cell_has_borders(cell) -> bool:
    """Заданы ли у ячейки свои границы (w:tcBorders)."""
    tc_pr = cell._tc.find(W_NS + "tcPr")
    return tc_pr is not None and tc_pr.find(W_NS + "tcBorders") is not None


def test_route_table_text_is_visible(marker_generator, route_payload, work_file):
    """
    Текст в таблицах погрузок/выгрузок виден: нет белого текста без заливки.

    Регресс: в шаблоне шапка таблицы машин — белый текст на тёмной заливке
    (1F3864), а строки данных — чёрный текст на светло-голубой (EDF2F9).
    Пока генератор копировал из шапки только шрифт, не перенося заливку,
    все ячейки таблиц 3.2/3.3 получали белый текст на белом фоне и
    выглядели пустыми — данные в них были, но их не было видно.
    """
    output = work_file("visible.docx")
    marker_generator.generate_docx(route_payload, str(output))

    tables = route_tables(Document(output))
    assert tables, "таблицы блоков 3.2/3.3 не найдены"

    for table in tables:
        for row in table.rows:
            for cell in row.cells:
                color = cell_text_color(cell)
                if color is None or color.upper() != "FFFFFF":
                    continue
                assert cell_shading(cell), (
                    "белый текст в ячейке без заливки — строку не видно"
                )


def test_route_table_copies_sample_formatting(marker_generator, route_payload,
                                              work_file):
    """Заливка и границы ячеек повторяют таблицу машин из блока 3.1."""
    output = work_file("format_copy.docx")
    marker_generator.generate_docx(route_payload, str(output))

    doc = Document(output)
    sample = [
        table for table in doc.tables
        if is_vehicle_table(table) and not is_route_table(table)
    ]
    assert sample, "таблица-образец (блок 3.1) не найдена"
    sample = sample[0]

    for table in route_tables(doc):
        # шапка — как шапка образца
        assert cell_shading(table.rows[0].cells[0]) == cell_shading(
            sample.rows[0].cells[0]
        )
        # данные — как строка данных образца
        assert cell_shading(table.rows[1].cells[0]) == cell_shading(
            sample.rows[1].cells[0]
        )
        # границы скопированы, а не взяты из стиля
        assert cell_has_borders(table.rows[1].cells[0])


def test_table_cells_have_single_paragraph_properties(marker_generator,
                                                      route_payload, work_file):
    """
    В ячейке ровно один pPr и один run.

    Два pPr в абзаце — невалидный XML: Word берёт первый и игнорирует
    наше выравнивание, из-за чего шапка уезжала влево.
    """
    output = work_file("single_ppr.docx")
    marker_generator.generate_docx(route_payload, str(output))

    for table in route_tables(Document(output)):
        for row in table.rows:
            for cell in row.cells:
                paragraph = cell.paragraphs[0]
                props = paragraph._p.findall(W_NS + "pPr")
                assert len(props) == 1, f"в ячейке {len(props)} pPr вместо одного"
                assert paragraph.alignment == 1, "ячейка выровнена не по центру"


def test_route_table_style_has_borders(marker_generator, route_payload, work_file):
    """Таблицы оформлены стилем Table Grid — то есть с границами."""
    output = work_file("style.docx")
    marker_generator.generate_docx(route_payload, str(output))

    tables = route_tables(Document(output))
    assert tables
    for table in tables:
        assert table.style.name == ContractGenerator.VEHICLE_TABLE_STYLE


def test_heading_is_paragraph_not_table_row(marker_generator, route_payload, work_file):
    """Заголовок «Погрузка N: адрес» — отдельный жирный абзац, не в таблице."""
    output = work_file("heading.docx")
    marker_generator.generate_docx(route_payload, str(output))

    doc = Document(output)
    headings = [
        paragraph for paragraph in doc.paragraphs
        if paragraph.text.strip().startswith("Погрузка 1:")
    ]
    assert len(headings) == 1
    assert all(run.bold for run in headings[0].runs if run.text.strip())

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                assert not cell.text.strip().startswith("Погрузка 1:")


def test_docx_openable_after_generation(marker_generator, route_payload, work_file):
    """Документ открывается python-docx, сырых плейсхолдеров в тексте нет."""
    output = work_file("openable.docx")
    marker_generator.generate_docx(route_payload, str(output))

    Document(output)  # не должно бросить исключение
    text = document_text(output)
    assert "{{" not in text and "}}" not in text
    assert placeholders_left(output) == []
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None


def test_postprocess_without_contract_data_keeps_old_behaviour(generator,
                                                               contract_payload,
                                                               work_file):
    """_postprocess_document(path) без данных по-прежнему работает."""
    output = work_file("no_data.docx")
    generator._render_template(
        generator.templates["ООО"],
        generator._build_replacements_map(contract_payload),
        str(output),
    )
    generator._postprocess_document(str(output))  # без contract_data

    assert route_tables(Document(output)) == []
    assert "Foton Auman" in document_text(output)


def test_fallback_on_old_template(legacy_generator, contract_payload, work_file):
    """Шаблон без метки: работает плоский {{loading_block}}, таблиц нет."""
    payload = dict(contract_payload)
    payload["loadings"] = [
        {"address": "Точка 1", "date": "2026-09-24", "time_window": "09:00"},
        {"address": "Точка 2", "date": "2026-09-24", "time_window": ""},
    ]

    output = work_file("fallback.docx")
    legacy_generator.generate_docx(payload, str(output))

    doc = Document(output)
    assert route_tables(doc) == []

    text = document_text(output)
    assert "Погрузка 1: Точка 1" in text
    assert "Погрузка 2: Точка 2" in text
    assert "EC3TEUMB0T0002608" in text
    assert LOADING_PLACEHOLDER not in text and UNLOADING_PLACEHOLDER not in text


def test_route_tables_log_contains_no_personal_data(marker_generator, route_payload,
                                                    work_file, caplog):
    """В логи не попадают адреса погрузок, VIN и марки машин."""
    output = work_file("logs.docx")

    with caplog.at_level("DEBUG", logger="core.contract_generator"):
        marker_generator.generate_docx(route_payload, str(output))

    logged = caplog.text
    for secret in ("Люторецкое", "50:03:0040280:8419", "Белый Раст",
                   "LVTDD24B1TDC49340", "SOUEAST", "TEST-ROUTE"):
        assert secret not in logged, f"в лог попало: {secret}"


def test_route_tables_log_reports_point_counters(marker_generator, gap_payload,
                                                 work_file, caplog):
    """
    В логе есть счётчики по точкам: всего, выведено блоков, без привязки
    и сколько машин скрыто из-за пустых точек. Без VIN и адресов.
    """
    payload = dict(gap_payload)
    # добавляем машину, привязанную к пустой точке 2 (её блок не выводится)
    payload["vehicles"] = gap_payload["vehicles"] + [
        {"brand_model": "МОДЕЛЬ 4", "vin": "EC3TEUMB0T0000004",
         "vehicle_type": "Легковой автомобиль", "loading_index": 2,
         "unloading_index": 2},
    ]

    output = work_file("counters.docx")

    with caplog.at_level("INFO", logger="core.contract_generator"):
        marker_generator.generate_docx(payload, str(output))

    logged = caplog.text
    # погрузки: 3 точки, выведено 2 блока (2-я без адреса), 1 машина скрыта
    assert "Метка LOADING_TABLE_HERE: точек 3, выведено блоков 2" in logged
    assert "скрыто из-за пустых точек 1" in logged
    # выгрузки: 4 точки, 3 блока, ничего не скрыто
    assert "Метка UNLOADING_TABLE_HERE: точек 4, выведено блоков 3" in logged
    assert "Блок 3.2: точек 3, выведено блоков 2" in logged
    assert "Блок 3.3: точек 4, выведено блоков 3" in logged

    for secret in ("Точка 1", "Точка 3", "Выгрузка 4", "МОДЕЛЬ",
                   "EC3TEUMB0T0000001", "EC3TEUMB0T0000002", "EC3TEUMB0T0000003"):
        assert secret not in logged, f"в лог попало: {secret}"


# ─────────────────────────────────────────────────────────────
# Вспомогательные методы разбора машин и точек
# ─────────────────────────────────────────────────────────────

def test_matches_index(generator):
    vehicle = {"vin": "EC3TEUMB0T0002608", "brand_model": "JETOUR T2",
               "loading_index": "2"}
    assert generator._matches_index(vehicle, "loading_index", 2)
    assert not generator._matches_index(vehicle, "loading_index", 1)
    assert not generator._matches_index(vehicle, "loading_index", 0)
    assert not generator._matches_index(dict(vehicle, loading_index="мусор"),
                                        "loading_index", 2)
    assert not generator._matches_index(dict(vehicle, vehicle_type="Тягач"),
                                        "loading_index", 2)
    assert not generator._matches_index({"vin": "", "brand_model": ""},
                                        "loading_index", 2)


def test_has_point(generator):
    # без массива точек проверяется только наличие привязки
    assert generator._has_point({"loading_index": 1}, "loading_index")
    assert generator._has_point({"loading_index": "3"}, "loading_index")
    assert not generator._has_point({"loading_index": 0}, "loading_index")
    assert not generator._has_point({"loading_index": "  "}, "loading_index")
    assert not generator._has_point({"loading_index": None}, "loading_index")
    assert not generator._has_point({}, "loading_index")
    assert not generator._has_point({"loading_index": "мусор"}, "loading_index")


def test_has_point_with_points_array(generator):
    """С массивом точек учитывается их количество и непустой адрес."""
    points = [
        {"address": "Точка 1"},
        {"address": ""},          # точка есть, но адрес пуст — блок пропущен
        {"address": "Точка 3"},
    ]

    assert generator._has_point({"loading_index": 1}, "loading_index", points)
    assert generator._has_point({"loading_index": 3}, "loading_index", points)
    # точка пропущена из-за пустого адреса — в вывод не попадёт
    assert not generator._has_point({"loading_index": 2}, "loading_index", points)
    # точки с таким номером в маршруте нет
    assert not generator._has_point({"loading_index": 4}, "loading_index", points)


def test_raw_point_index(generator):
    """Разбор индекса точки не зависит от типа ТС и заполненности строки."""
    assert generator._raw_point_index({"loading_index": 2}, "loading_index") == 2
    assert generator._raw_point_index({"loading_index": "2"}, "loading_index") == 2
    assert generator._raw_point_index({"loading_index": 0}, "loading_index") == 0
    assert generator._raw_point_index({"loading_index": None}, "loading_index") == 0
    assert generator._raw_point_index({"loading_index": -1}, "loading_index") == 0
    assert generator._raw_point_index({"loading_index": "мусор"}, "loading_index") == 0
    # тип ТС и пустая строка здесь не фильтруются: это чистый разбор значения
    assert generator._raw_point_index(
        {"loading_index": 3, "vehicle_type": "Тягач"}, "loading_index"
    ) == 3


def test_is_route_vehicle(generator):
    """Тягач, полуприцеп, прицеп и пустая строка в таблицы не попадают."""
    assert generator._is_route_vehicle({"vin": "EC3TEUMB0T0002608"})
    assert generator._is_route_vehicle({"brand_model": "JETOUR T2"})
    assert not generator._is_route_vehicle(
        {"vin": "TRACTORVIN0000001", "vehicle_type": "Тягач"})
    assert not generator._is_route_vehicle(
        {"vin": "TRAILERVIN0000001", "vehicle_type": "Полуприцеп"})
    assert not generator._is_route_vehicle(
        {"vin": "PRITSEPVIN0000001", "vehicle_type": "Прицеп"})
    assert not generator._is_route_vehicle({"vin": "", "brand_model": ""})


def test_cargo_vehicles_filters_technical_transport(generator):
    vehicles = [
        {"vin": "EC3TEUMB0T0002608", "brand_model": "JETOUR T2"},
        {"vin": "TRACTORVIN0000001", "brand_model": "Тягач", "vehicle_type": "Тягач"},
        {"vin": "TRAILERVIN0000001", "brand_model": "П/прицеп",
         "vehicle_type": "Полуприцеп"},
        {"vin": "PRITSEPVIN0000001", "brand_model": "Прицеп", "vehicle_type": "Прицеп"},
        {"vin": "", "brand_model": ""},
    ]
    assert [v["vin"] for v in generator._cargo_vehicles(vehicles)] == [
        "EC3TEUMB0T0002608"
    ]


def test_tractor_and_trailer_placeholders_filled(generator, contract_payload):
    """Регресс: тягач и полуприцеп обязаны попадать в карту замен."""
    replacements = generator._build_replacements_map(contract_payload)
    assert replacements["tractor_brand"] == "Foton Auman"
    assert replacements["tractor_plate"] == "O844XY196"
    assert replacements["tractor_color"] == "Белый"
    assert replacements["trailer_brand"] == "YANGMINDA"
    assert replacements["trailer_plate"] == "71ABF18"


def test_legacy_nested_tractor_structure(generator, contract_payload):
    """Старый формат trailer.tractor тоже поддерживается."""
    payload = {
        "tractor": {},
        "trailer": {
            "tractor": contract_payload["tractor"],
            "trailer": contract_payload["trailer"],
        },
        "contract": contract_payload["contract"],
    }
    replacements = generator._build_replacements_map(payload)
    assert replacements["tractor_plate"] == "O844XY196"
    assert replacements["trailer_plate"] == "71ABF18"


# ─────────────────────────────────────────────────────────────
# Город
# ─────────────────────────────────────────────────────────────

def test_city_from_first_loading(generator, contract_payload):
    replacements = generator._build_replacements_map(contract_payload)
    assert replacements["city"] == "Мурманск"


def test_city_default_moscow(generator):
    replacements = generator._build_replacements_map({"contract": {}})
    assert replacements["city"] == "Москва"


def test_city_from_explicit_field(generator, contract_payload):
    payload = dict(contract_payload, city="г. Тверь")
    replacements = generator._build_replacements_map(payload)
    assert replacements["city"] == "Тверь"


# ─────────────────────────────────────────────────────────────
# Суммы, НДС и оплата
# ─────────────────────────────────────────────────────────────

def test_vat_calculation_for_ooo(generator, contract_payload):
    replacements = generator._build_replacements_map(contract_payload)
    # 180300 * 22% = 39666; итого 219966
    assert replacements["sum_wo_nds"] == "180300.00"
    assert replacements["sum_nds"] == "39666.00"
    assert replacements["sum_total"] == "219966.00"
    assert "22%" in replacements["nds_text"]
    assert replacements["vat_rate"] == "22%"


def test_no_vat_for_ip_without_vat(generator, contract_payload):
    payload = dict(contract_payload)
    payload["contract"] = dict(contract_payload["contract"],
                               carrier_type="ИП без НДС", vat_rate_num=0)
    replacements = generator._build_replacements_map(payload)
    assert replacements["sum_nds"] == ""
    assert replacements["nds_text"] == "НДС не облагается"
    assert replacements["sum_total"] == replacements["sum_wo_nds"] == "180300.00"
    assert "не является плательщиком НДС" in replacements["nds_status_text"]


def test_sum_in_words(generator, contract_payload):
    replacements = generator._build_replacements_map(contract_payload)
    assert replacements["sum_total_words"].startswith("Двести девятнадцать тысяч")
    assert "рубл" in replacements["sum_total_words"]


def test_payment_days(generator, contract_payload):
    replacements = generator._build_replacements_map(contract_payload)
    assert replacements["payment_days"] == "10"
    assert replacements["payment_days_words"] == "десяти"
    assert replacements["penalty_rate"] == "5000"


# ─────────────────────────────────────────────────────────────
# Машины и точки маршрута
# ─────────────────────────────────────────────────────────────

def test_cars_filled_and_emptied(generator, contract_payload):
    replacements = generator._build_replacements_map(contract_payload)
    assert replacements["car_1_brand"] == "JETOUR T2"
    assert replacements["car_1_vin"] == "EC3TEUMB0T0002608"
    assert replacements["cargo_count"] == "1"
    # позиции сверх количества машин пустые, а не «None»
    for index in (2, 5, 12):
        assert replacements[f"car_{index}_brand"] == ""
        assert replacements[f"car_{index}_vin"] == ""


def test_twelve_cars_limit(generator, contract_payload):
    payload = dict(contract_payload)
    payload["vehicles"] = [
        {"vin": f"EC3TEUMB0T{i:06d}", "brand_model": f"CAR {i}",
         "vehicle_type": "Легковой автомобиль"}
        for i in range(15)
    ]
    replacements = generator._build_replacements_map(payload)
    assert replacements["car_12_brand"] == "CAR 11"
    assert replacements["cargo_count"] == "15"


def test_point_labels(generator, contract_payload):
    points = contract_payload["loadings"] + contract_payload["unloadings"]
    assert generator._get_point_label(0, points, "Погрузка") == "—"
    assert generator._get_point_label(1, points, "Погрузка") == "Погрузка 1"
    assert generator._get_point_label(2, points, "Выгрузка") == "Выгрузка 2"
    assert generator._get_point_label(99, points, "Погрузка") == "—"


def test_loading_and_unloading_blocks(generator, contract_payload):
    replacements = generator._build_replacements_map(contract_payload)
    assert "Погрузка 1: 183052" in replacements["loading_block"]
    assert "Выгрузка 1" in replacements["unloading_block"]
    assert "EC3TEUMB0T0002608" in replacements["loading_block"]


def test_vehicle_without_point_goes_to_all_points(generator, contract_payload):
    """loading_index = 0 — машина попадает во все точки."""
    payload = dict(contract_payload)
    payload["loadings"] = [
        {"address": "Точка 1", "date": "", "time_window": ""},
        {"address": "Точка 2", "date": "", "time_window": ""},
    ]
    replacements = generator._build_replacements_map(payload)
    block = replacements["loading_block"]
    assert block.count("EC3TEUMB0T0002608") == 2


def test_vehicle_bound_to_single_point(generator, contract_payload):
    payload = dict(contract_payload)
    payload["loadings"] = [
        {"address": "Точка 1", "date": "", "time_window": ""},
        {"address": "Точка 2", "date": "", "time_window": ""},
    ]
    payload["vehicles"] = [dict(contract_payload["vehicles"][0], loading_index=2)]
    block = generator._build_replacements_map(payload)["loading_block"]
    assert block.count("EC3TEUMB0T0002608") == 1


# ─────────────────────────────────────────────────────────────
# Вспомогательные методы
# ─────────────────────────────────────────────────────────────

def test_date_helpers(generator):
    assert generator._format_date_full("2026-09-23") == "23.09.2026"
    assert generator._format_date_dot("2026-09-23") == "23.09"
    assert generator._day_of_month("2026-09-05") == "05"
    assert generator._month_name("2026-01-15") == "января"
    assert generator._month_name(None) == "сентября"
    assert generator._format_date_full("") == ""


def test_short_fio(generator):
    assert generator._short_fio("Добросоцкий Алексей Николаевич") == "А.Н. Добросоцкий"
    assert generator._short_fio("Иванов Иван") == "И. Иванов"
    assert generator._short_fio("Иванов") == "Иванов"
    assert generator._short_fio("") == ""


def test_days_to_words(generator):
    assert generator._days_to_words(10) == "десяти"
    assert generator._days_to_words("5") == "пяти"
    assert generator._days_to_words(0) == "0"
    assert generator._days_to_words("мусор") == "десяти"
    assert generator._days_to_words(None) == "десяти"


def test_template_selection(generator, templates_dir):
    assert generator._get_template_path("ООО (с НДС)").endswith("shablon_ooo.docx")
    assert generator._get_template_path("ИП с НДС").endswith("shablon_ip_with_vat.docx")
    assert generator._get_template_path("ИП без НДС").endswith("shablon_ip_without_vat.docx")
    assert generator._get_template_path("что-то иное").endswith("shablon_ooo.docx")


def test_default_output_dir(generator, project_root):
    assert generator.default_output_dir() == str(project_root / "output")


# ─────────────────────────────────────────────────────────────
# Генерация файла
# ─────────────────────────────────────────────────────────────

def test_generate_creates_file(light_generator, contract_payload, work_dir):
    path = light_generator.generate(contract_payload, output_dir=str(work_dir))
    try:
        assert path.endswith(".docx")
        assert path.startswith(str(work_dir))
        assert "23092026-74" in path
    finally:
        Path(path).unlink(missing_ok=True)


def test_generate_sanitizes_contract_number(light_generator, contract_payload, work_dir):
    payload = dict(contract_payload)
    payload["contract"] = dict(contract_payload["contract"], number='74/2026 "тест"')
    path = light_generator.generate(payload, output_dir=str(work_dir))
    try:
        filename = Path(path).name
        assert "/" not in filename and '"' not in filename
    finally:
        Path(path).unlink(missing_ok=True)


@pytest.mark.parametrize("template_name, carrier_type", [
    ("shablon_ooo.docx", "ООО (с НДС)"),
    ("shablon_ip_with_vat.docx", "ИП с НДС"),
    ("shablon_ip_without_vat.docx", "ИП без НДС"),
])
def test_generate_docx_renders_all_templates(light_generator, contract_payload,
                                             work_file, template_name, carrier_type):
    """Полный цикл рендера: нет незамещённых плейсхолдеров, данные на месте."""
    payload = dict(contract_payload)
    payload["contract"] = dict(contract_payload["contract"], carrier_type=carrier_type)

    output = work_file(f"out_{template_name}")
    engine = light_generator._render_template(
        str(light_generator.templates["ООО" if "ooo" in template_name else
                                      ("ИП с НДС" if "with_vat" in template_name
                                       else "ИП без НДС")]),
        light_generator._build_replacements_map(payload),
        str(output),
    )
    light_generator._postprocess_document(str(output))

    assert engine == "docxtpl"
    assert placeholders_left(output) == []
    text = document_text(output)
    assert "Foton Auman" in text
    assert "71ABF18" in text
    assert "Мурманск" in text


def test_generate_docx_multiline_block_has_breaks(legacy_generator, contract_payload,
                                                  work_file):
    """
    Плоский блок старого шаблона рендерится с разрывами строк Word.

    Проверяется именно legacy-путь ({{loading_block}}): в новом шаблоне
    вместо плоского текста вставляются таблицы, и разрывов строк там нет.
    """
    payload = dict(contract_payload)
    payload["loadings"] = [
        {"address": "Точка 1", "date": "2026-09-24", "time_window": "09:00"},
        {"address": "Точка 2", "date": "2026-09-24", "time_window": ""},
    ]
    output = work_file("multi.docx")
    legacy_generator.generate_docx(payload, str(output))
    xml = document_xml(output)
    assert "<w:br" in xml
    text = document_text(output)
    assert "Точка 1" in text and "Точка 2" in text


def test_manual_render_fallback(legacy_generator, contract_payload, work_file, monkeypatch):
    """Если docxtpl недоступен, работает резервная ручная замена."""
    import sys

    monkeypatch.setitem(sys.modules, "docxtpl", None)
    output = work_file("manual.docx")
    engine = legacy_generator._render_template(
        legacy_generator.templates["ООО"],
        legacy_generator._build_replacements_map(contract_payload),
        str(output),
    )
    legacy_generator._postprocess_document(str(output))

    assert engine == "manual"
    assert placeholders_left(output) == []
    assert "Foton Auman" in document_text(output)


def test_empty_vehicle_rows_removed(light_generator, contract_payload, work_file):
    output = work_file("rows.docx")
    light_generator.generate_docx(contract_payload, str(output))

    doc = Document(output)
    vehicle_tables = [
        table for table in doc.tables
        if table.rows and any("VIN" in cell.text for cell in table.rows[0].cells)
    ]
    assert vehicle_tables, "таблица ТС не найдена"
    assert len(vehicle_tables[0].rows) < 13


def test_real_templates_still_render(generator, contract_payload, work_file):
    """
    Реальные templates/*.docx (с встроенными шрифтами) тоже рендерятся.

    Остальные тесты работают на облегчённых копиях ради памяти, поэтому
    хотя бы одна проверка должна идти по настоящим файлам: важно, что
    пользовательские шаблоны не сломаны.
    """
    payload = dict(contract_payload)
    output = work_file("real_template.docx")

    engine = generator._render_template(
        str(generator.templates["ООО"]),
        generator._build_replacements_map(payload),
        str(output),
    )
    generator._postprocess_document(str(output), payload)

    assert engine == "docxtpl"
    assert placeholders_left(output) == []
    assert "Foton Auman" in document_text(output)
