#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка шаблонов заявки «Логистикс Рус» (ЭТАП 3.1.C.A.1).

Создаёт ДВА пустых бланка с плейсхолдерами {{...}} для docxtpl:

  * templates/shablon_logistiks_rus_ooo.docx — ООО «ТЕХНОЛОГИСТИКА»
    (генеральный договор № ТЭ0909/01), стоимость с НДС;
  * templates/shablon_logistiks_rus_ip.docx  — ИП Хейгетян Е.В.
    (генеральный договор № ТЭ0909/02), стоимость без НДС.

Заявка — приложение № 1 к Генеральному договору транспортной экспедиции,
заказчик в обоих вариантах один и тот же: ООО «ДжейСиСиТиЭс Интернейшнл
Логистикс Рус» (подписант Гао Фанфан).

Образцы (только чтение, данные оттуда НЕ переносятся — ни марок, ни VIN,
ни адресов, ни ФИО, ни госномеров, ни сумм):

  * templates/Приложение № 1 Заявка_600_ООО «ДжейСиСиТиЭс Интернейшнл
    Логистикс Рус».docx
  * templates/Приложение № 1 Заявка_359_ИП_ «ДжейСиСиТиЭс Интернейшнл
    Логистикс Рус».docx

Что воспроизводится по образцу:
  * A4 (11909×16834 twips) и поля 2,54 см со всех сторон;
  * шапка: «Приложение № 1», ссылка на генеральный договор в две строки,
    «ЗАЯВКА № …» по центру, «на организацию перевозки транспортных
    средств», «Дата Заявки: «…» … года.», блок «Заказчик/Экспедитор»;
  * разделы 1–6 и подписи в том же порядке и с теми же формулировками;
  * таблица автомобилей: ширины колонок 1000 / 5091 / 3932 twips, заливка
    шапки E5E5E5, повтор шапки на новой странице, текст по центру,
    одиночный интервал;
  * блок подписей — обычные абзацы (таблицы подписей в образце нет).

Что сделано иначе, чем в образце (осознанные решения):
  * шрифт — Times New Roman 12 pt, как в рабочих шаблонах shablon_ooo.docx
    и shablon_formika.docx (в образцах Arial 12 pt). Размеры и геометрия
    взяты из образца, семейство шрифта — проектное;
  * строка «Дата / время погрузки» одна на раздел 1 (в образце она стоит
    после блока грузоотправителя), строка «Плановая дата / время завершения
    выгрузки» — одна на раздел 2 (как в образце с четырьмя
    грузополучателями). Плейсхолдеры loading_*/unloading_* в шаблоне
    одиночные, поэтому дублировать строку в каждом блоке не нужно;
  * в разделе 5 ООО-варианта три суммы (без НДС / НДС по ставке / итого),
    в ИП-варианте одна («Стоимость услуг: … руб. Без НДС») — как в образцах;
  * ссылка на генеральный договор в разделе 6 пишется с пробелами
    («№ ТЭ 0909/01»), как в образце; в шапке — как в образце без пробела
    («№ТЭ0909/01»).

Плейсхолдер всегда лежит в ОДНОМ run: docxtpl (Jinja) не склеивает
переменные, разорванные по runs.

Запуск:  python tools/make_logistiks_rus_template.py
"""

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Pt

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"

FONT = "Times New Roman"
TEXT_SIZE = 12

#: Геометрия страницы образца: A4, поля 2,54 см (1440 twips) со всех сторон.
PAGE_WIDTH_TWIPS = 11909
PAGE_HEIGHT_TWIPS = 16834
MARGIN_TWIPS = 1440

#: Ширины колонок таблицы автомобилей в twips — ровно как в образце.
CARGO_COLUMN_WIDTHS = (1000, 5091, 3932)

#: Заливка шапки таблицы автомобилей из образца.
HEADER_FILL = "E5E5E5"

#: Размер таблицы автомобилей: бланк рассчитан на 12 машин, лишние строки
#: удаляет постобработка генератора (RemoveEmptyVehicleRowsStep).
CAR_ROWS = 12

#: Максимум точек погрузки/выгрузки: блоки неиспользованных номеров
#: удаляет постобработка (RemoveEmptyShipperConsigneeBlocksStep).
MAX_POINTS = 10

#: Интервалы: у обычных строк — 6 pt после, у заголовков разделов — 12 pt до.
BODY_SPACE_AFTER = 6
SECTION_SPACE_BEFORE = 12

#: Фиксированные стороны заявки: заказчик один для обоих вариантов,
#: экспедитор и ссылка на генеральный договор зависят от варианта.
CUSTOMER_SIGNER = "Гао Фанфан"

#: Два варианта экспедитора: внутритиповое переключение по carrier_type
#: (как «ООО (с НДС)» / «ИП без НДС» у договора-заявки на перевозку).
VARIANTS: Dict[str, Dict[str, Any]] = {
    "ООО": {
        "filename": "shablon_logistiks_rus_ooo.docx",
        # Номер генерального договора в шапке (в образце — без пробела).
        "contract_header": "№ТЭ0909/01",
        # Тот же номер в разделе 6 (в образце — с пробелами).
        "contract_reference": "№ ТЭ 0909/01",
        # Строка «Экспедитор: …» в шапке.
        "expeditor_line": "ООО «ТЕХНОЛОГИСТИКА»",
        # Подписи экспедитора: наименование и строка подписи.
        "signature_name": "ООО «ТЕХНОЛОГИСТИКА»",
        "signature_line": "________________ / Т.А. Ахмедов /",
        # Раздел 5: три суммы — без НДС, НДС по ставке, итого.
        "cost_with_vat": True,
    },
    "ИП": {
        "filename": "shablon_logistiks_rus_ip.docx",
        "contract_header": "№ТЭ0909/02",
        "contract_reference": "№ ТЭ 0909/02",
        "expeditor_line": "ИП Хейгетян Е.В.",
        "signature_name": "ИП Хейгетян Елена Валентиновна",
        "signature_line": "________________ / Е.В.Хейгетян /",
        # Раздел 5: одна сумма, «Без НДС».
        "cost_with_vat": False,
    },
}


def resolve_variant(carrier_type: str) -> str:
    """
    Ключ варианта по типу экспедитора.

    Правило то же, что будет у генератора: «ИП» в строке типа — вариант
    индивидуального предпринимателя, всё остальное — ООО.
    """
    return "ИП" if "ИП" in str(carrier_type) else "ООО"


def template_path(carrier_type: str) -> Path:
    """Путь шаблона варианта: templates/<имя файла из VARIANTS>."""
    return TEMPLATES_DIR / VARIANTS[resolve_variant(carrier_type)]["filename"]


def twips(value: int) -> Emu:
    """twips → EMU (1 twip = 635 EMU)."""
    return Emu(int(value) * 635)


# ─────────────────────────────────────────────────────────────
# Примитивы оформления
# ─────────────────────────────────────────────────────────────

def _set_run_font(run, size: int, bold: bool, underline: bool) -> None:
    """Шрифт run: имя для всех алфавитов (ascii/hAnsi/cs/eastAsia)."""
    run.font.name = FONT
    run.font.size = Pt(size)
    run.bold = bold
    run.underline = underline

    rPr = run._r.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rFonts.set(qn(attr), FONT)


def _write_parts(paragraph, parts: Sequence[Any], size: int = TEXT_SIZE) -> None:
    """
    Дописывает в абзац части текста.

    Часть — строка (обычный текст) либо кортеж (текст, {bold/underline/size}).
    Каждая часть становится отдельным run, поэтому плейсхолдер целиком
    остаётся в одном run.
    """
    for part in parts:
        text, opts = (part, {}) if isinstance(part, str) else part
        run = paragraph.add_run(text)
        _set_run_font(
            run,
            size=opts.get("size", size),
            bold=opts.get("bold", False),
            underline=opts.get("underline", False),
        )


def add_paragraph(doc, parts: Sequence[Any], *, align=None, size: int = TEXT_SIZE,
                  space_before: int = 0, space_after: int = 0):
    """Абзац из списка частей (каждая часть — отдельный run)."""
    paragraph = doc.add_paragraph()
    if align is not None:
        paragraph.alignment = align
    paragraph.paragraph_format.space_before = Pt(space_before)
    paragraph.paragraph_format.space_after = Pt(space_after)

    _write_parts(paragraph, parts, size)
    return paragraph


def add_two_line_paragraph(doc, first_parts: Sequence[Any],
                           second_parts: Sequence[Any], *, align=None,
                           size: int = TEXT_SIZE, space_before: int = 0,
                           space_after: int = 0):
    """
    Абзац из двух строк, разделённых разрывом (w:br).

    В образце так оформлены ссылка на генеральный договор в шапке и блок
    «Заказчик: … / Экспедитор: …»: это один абзац с разрывом строки, а не
    два абзаца.
    """
    paragraph = add_paragraph(doc, first_parts, align=align, size=size,
                              space_before=space_before, space_after=space_after)
    paragraph.runs[-1].add_break()
    _write_parts(paragraph, second_parts, size)
    return paragraph


def shade_cell(cell, fill: str) -> None:
    """Заливка ячейки (w:shd)."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcPr.append(shd)


def set_repeat_header(row) -> None:
    """Повтор строки таблицы на новой странице (w:tblHeader), как в образце."""
    trPr = row._tr.get_or_add_trPr()
    tblHeader = OxmlElement("w:tblHeader")
    tblHeader.set(qn("w:val"), "true")
    trPr.append(tblHeader)


def write_cell(cell, parts: Sequence[Any], *, align=WD_ALIGN_PARAGRAPH.CENTER,
               size: int = TEXT_SIZE) -> None:
    """Текст в первую (единственную) строку ячейки: по центру, одиночный интервал."""
    paragraph = cell.paragraphs[0]
    paragraph.alignment = align
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = 1.0

    _write_parts(paragraph, parts, size)


def set_table_widths(table, widths_twips: Sequence[int]) -> None:
    """Фиксированные ширины колонок: tblW + tblGrid + tcW."""
    table.autofit = False

    tblPr = table._tbl.tblPr
    tblW = tblPr.find(qn("w:tblW"))
    if tblW is None:
        tblW = OxmlElement("w:tblW")
        tblPr.insert(0, tblW)
    tblW.set(qn("w:type"), "dxa")
    tblW.set(qn("w:w"), str(sum(widths_twips)))

    for column, width in zip(table.columns, widths_twips):
        column.width = twips(width)
    for row in table.rows:
        for cell, width in zip(row.cells, widths_twips):
            cell.width = twips(width)


# ─────────────────────────────────────────────────────────────
# Сборка документа
# ─────────────────────────────────────────────────────────────

def configure_document(doc) -> None:
    """Страница и базовый шрифт документа."""
    section = doc.sections[0]
    section.page_width = twips(PAGE_WIDTH_TWIPS)
    section.page_height = twips(PAGE_HEIGHT_TWIPS)
    section.left_margin = twips(MARGIN_TWIPS)
    section.right_margin = twips(MARGIN_TWIPS)
    section.top_margin = twips(MARGIN_TWIPS)
    section.bottom_margin = twips(MARGIN_TWIPS)

    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(TEXT_SIZE)
    rPr = normal.element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rFonts.set(qn(attr), FONT)


def build_header(doc, variant: Dict[str, Any]) -> None:
    """Шапка: приложение, ссылка на договор, номер заявки, дата, стороны."""
    add_paragraph(doc, [("Приложение № 1", {"bold": True})], space_before=6)

    add_two_line_paragraph(
        doc,
        [f"к Генеральному договору транспортной экспедиции "
         f"{variant['contract_header']}"],
        ["от «09» сентября 2026 г."],
    )

    add_paragraph(
        doc,
        [("ЗАЯВКА № ", {"bold": True}), ("{{contract_number}}", {"bold": True})],
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_before=6,
    )
    add_paragraph(
        doc,
        [("на организацию перевозки транспортных средств", {"bold": True})],
        align=WD_ALIGN_PARAGRAPH.CENTER,
    )
    add_paragraph(
        doc,
        [
            "Дата Заявки: «", "{{contract_date_day}}", "» ",
            "{{contract_date_month}}", " ", "{{contract_date_year}}", " года.",
        ],
        space_before=6,
        space_after=BODY_SPACE_AFTER,
    )

    add_two_line_paragraph(
        doc,
        [("Заказчик:", {"bold": True}), " {{customer_name}}"],
        [("Экспедитор:", {"bold": True}), f" {variant['expeditor_line']}"],
        space_after=BODY_SPACE_AFTER,
    )


def build_loading_section(doc) -> None:
    """
    Раздел 1 «Погрузка»: до 10 блоков грузоотправителей и строка даты/времени.

    В бланке MAX_POINTS блоков; блоки сверх фактического числа точек
    удаляет постобработка (RemoveEmptyShipperConsigneeBlocksStep).
    """
    add_paragraph(doc, [("1. ПОГРУЗКА", {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE, space_after=BODY_SPACE_AFTER)

    for number in range(1, MAX_POINTS + 1):
        add_paragraph(doc, [
            ("Грузоотправитель:", {"bold": True}),
            f" {{{{shipper_{number}_name}}}}",
        ], space_after=BODY_SPACE_AFTER)
        add_paragraph(doc, [
            ("Адрес погрузки:", {"bold": True}),
            f" {{{{shipper_{number}_address}}}}",
        ], space_after=BODY_SPACE_AFTER)

    add_paragraph(doc, [
        ("Дата / время погрузки:", {"bold": True}),
        " {{loading_date}} г. Время с {{loading_time_from}} "
        "по {{loading_time_to}}",
    ], space_after=BODY_SPACE_AFTER)


def build_unloading_section(doc) -> None:
    """Раздел 2 «Выгрузка»: до 10 грузополучателей и строка даты/времени."""
    add_paragraph(doc, [("2. ВЫГРУЗКА", {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE, space_after=BODY_SPACE_AFTER)

    for number in range(1, MAX_POINTS + 1):
        add_paragraph(doc, [
            (f"Грузополучатель №{number}:", {"bold": True}),
            f" {{{{consignee_{number}_name}}}}",
        ], space_after=BODY_SPACE_AFTER)
        add_paragraph(doc, [
            ("Адрес выгрузки:", {"bold": True}),
            f" {{{{consignee_{number}_address}}}}",
        ], space_after=BODY_SPACE_AFTER)

    add_paragraph(doc, [
        ("Плановая дата / время завершения выгрузки:", {"bold": True}),
        " {{unloading_date}} г. Время с {{unloading_time_from}} "
        "по {{unloading_time_to}}",
    ], space_after=BODY_SPACE_AFTER)


def build_cargo_table(doc):
    """Таблица автомобилей: шапка + 12 строк с плейсхолдерами."""
    table = doc.add_table(rows=1 + CAR_ROWS, cols=3)
    table.style = "Table Grid"
    set_table_widths(table, CARGO_COLUMN_WIDTHS)

    headers = ("№", "Марка, модель", "VIN-номер")
    for cell, title in zip(table.rows[0].cells, headers):
        write_cell(cell, [(title, {"bold": True})])
        shade_cell(cell, HEADER_FILL)
    set_repeat_header(table.rows[0])

    for number in range(1, CAR_ROWS + 1):
        row = table.rows[number]
        write_cell(row.cells[0], [str(number)])
        write_cell(row.cells[1], [f"{{{{car_{number}_brand}}}}"])
        write_cell(row.cells[2], [f"{{{{car_{number}_vin}}}}"])

    return table


def build_cargo_section(doc) -> None:
    """Раздел 3 «Перевозимые автомобили»: таблица и общее количество."""
    add_paragraph(doc, [("3. ПЕРЕВОЗИМЫЕ АВТОМОБИЛИ", {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE, space_after=BODY_SPACE_AFTER)
    build_cargo_table(doc)
    add_paragraph(doc, [
        ("Общее количество:", {"bold": True}),
        " {{cargo_count}} шт.",
    ], space_before=BODY_SPACE_AFTER, space_after=BODY_SPACE_AFTER)


def build_vehicle_section(doc) -> None:
    """Раздел 4 «Автовоз и водитель»: тягач, прицеп, водитель, замена."""
    add_paragraph(doc, [("4. АВТОВОЗ И ВОДИТЕЛЬ", {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE, space_after=BODY_SPACE_AFTER)

    add_paragraph(doc, [
        ("Тягач:", {"bold": True}),
        " {{tractor_brand}} ",
        ("гос. №:", {"bold": True}),
        " {{tractor_plate}}",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=BODY_SPACE_AFTER)

    add_paragraph(doc, [
        ("Прицеп:", {"bold": True}),
        " {{trailer_brand}} ",
        ("гос. №:", {"bold": True}),
        " {{trailer_plate}}",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=BODY_SPACE_AFTER)

    add_paragraph(doc, [
        ("Водитель:", {"bold": True}),
        " {{driver_name}}",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=BODY_SPACE_AFTER)

    add_paragraph(doc, [
        "Замена водителя, тягача или прицепа допускается в порядке, "
        "предусмотренном Генеральным договором.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=BODY_SPACE_AFTER)


def build_cost_section(doc, variant: Dict[str, Any]) -> None:
    """
    Раздел 5 «Стоимость».

    ООО — три суммы (без НДС, НДС по ставке, итого), ИП — одна сумма
    «Без НДС»: плейсхолдеров sum_wo_vat и sum_vat в ИП-шаблоне нет.
    """
    add_paragraph(doc, [("5. СТОИМОСТЬ", {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE, space_after=BODY_SPACE_AFTER)

    if variant["cost_with_vat"]:
        add_paragraph(doc, [
            ("Стоимость услуг:", {"bold": True}),
            " {{sum_wo_vat}} руб.",
        ], align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=BODY_SPACE_AFTER)
        add_paragraph(doc, [
            ("НДС {{vat_rate}}:", {"bold": True}),
            " {{sum_vat}} руб.",
        ], align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=BODY_SPACE_AFTER)
        add_paragraph(doc, [
            ("Итого:", {"bold": True}),
            " {{sum_total}} руб.",
        ], align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=BODY_SPACE_AFTER)
    else:
        add_paragraph(doc, [
            ("Стоимость услуг:", {"bold": True}),
            " {{sum_total}} руб. Без НДС",
        ], align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=BODY_SPACE_AFTER)


def build_conditions_section(doc, variant: Dict[str, Any]) -> None:
    """Раздел 6 «Особые условия»: условия рейса и ссылка на договор."""
    add_paragraph(doc, [("6. ОСОБЫЕ УСЛОВИЯ", {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE, space_after=BODY_SPACE_AFTER)

    add_paragraph(doc, ["{{special_conditions}}"], space_after=BODY_SPACE_AFTER)

    add_paragraph(doc, [
        "Все остальные условия оказания услуг, расчетов, документооборота, "
        "простоя, приемки и выдачи груза, ответственности Сторон и "
        "разрешения споров определяются Генеральным договором транспортной "
        f"экспедиции {variant['contract_reference']} от «09» сентября 2026 г.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=BODY_SPACE_AFTER)

    add_paragraph(doc, [
        "Настоящая Заявка является неотъемлемой частью Генерального договора.",
    ], space_after=BODY_SPACE_AFTER)


def build_signatures(doc, variant: Dict[str, Any]) -> None:
    """
    Подписи сторон: заказчик фиксирован (Гао Фанфан), экспедитор — по варианту.

    В образце подписи — обычные абзацы (таблицы подписей нет).
    """
    add_paragraph(doc, [("ЗАКАЗЧИК", {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE, space_after=BODY_SPACE_AFTER)
    add_paragraph(doc, ["{{customer_name}}"], space_after=BODY_SPACE_AFTER)
    add_paragraph(doc, [f"________________ / {CUSTOMER_SIGNER} /"],
                  space_after=BODY_SPACE_AFTER)

    add_paragraph(doc, [("ЭКСПЕДИТОР", {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE, space_after=BODY_SPACE_AFTER)
    add_paragraph(doc, [variant["signature_name"]], space_after=BODY_SPACE_AFTER)
    add_paragraph(doc, [variant["signature_line"]], space_after=BODY_SPACE_AFTER)


def collect_placeholders(doc) -> List[str]:
    """Все плейсхолдеры документа (абзацы и таблицы) в порядке появления."""
    texts = [p.text for p in doc.paragraphs]
    texts += [c.text for t in doc.tables for r in t.rows for c in r.cells]
    return re.findall(r"\{\{[^{}]*\}\}", "\n".join(texts))


def build_template(carrier_type: str) -> Path:
    """
    Собирает бланк одного варианта («ООО» или «ИП») и сохраняет его.

    Возвращает путь сохранённого шаблона.
    """
    variant_key = resolve_variant(carrier_type)
    variant = VARIANTS[variant_key]

    doc = Document()
    configure_document(doc)

    build_header(doc, variant)
    build_loading_section(doc)
    build_unloading_section(doc)
    build_cargo_section(doc)
    build_vehicle_section(doc)
    build_cost_section(doc, variant)
    build_conditions_section(doc, variant)
    build_signatures(doc, variant)

    path = TEMPLATES_DIR / variant["filename"]
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def describe(path: Path) -> Tuple[int, int, int, int, int]:
    """Сводка по собранному бланку: абзацы, таблицы, строки груза, плейсхолдеры."""
    doc = Document(str(path))
    placeholders = collect_placeholders(doc)
    cargo_rows = len(doc.tables[0].rows) if doc.tables else 0
    return (len(doc.paragraphs), len(doc.tables), cargo_rows,
            len(placeholders), len(set(placeholders)))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    for carrier_type in VARIANTS:
        path = build_template(carrier_type)
        paragraphs, tables, cargo_rows, total, unique = describe(path)
        print(f"[OK] {carrier_type}: {path}")
        print(f"     абзацев={paragraphs} таблиц={tables} "
              f"строк таблицы авто={cargo_rows}")
        print(f"     плейсхолдеров={total} уникальных={unique}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
