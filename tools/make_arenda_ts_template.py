#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка шаблонов договора аренды ТС с экипажем (ЭТАП 3.1.D.A.1).

Создаёт ТРИ пустых бланка с плейсхолдерами {{...}} для docxtpl:

  * templates/shablon_arenda_ts_ooo.docx             — Арендатор ООО
    (ИНН + КПП + ОГРН, «именуемое», три суммы: без НДС / НДС / итого);
  * templates/shablon_arenda_ts_ip_with_vat.docx    — Арендатор ИП,
    плательщик НДС (ИНН + ОГРНИП, «именуемый», три суммы);
  * templates/shablon_arenda_ts_ip_without_vat.docx — Арендатор ИП,
    НДС не облагается (одна сумма без НДС).

Каждый бланк — это ОДИН файл: основной договор (9 разделов) и
Приложение № 1 «Акт приема-передачи и возврата транспортного средства
с экипажем» (начинается с новой страницы).

Данные из образца
(templates/Договор_аренды_ТС_с_экипажем_ТЛ-574_Технологистика_ЛЦ_обновленный.docx)
сюда НЕ переносятся: ни марки, ни VIN, ни ФИО, ни госномера, ни адреса,
ни ИНН/ОГРН, ни банковские реквизиты, ни суммы, ни номер договора.
Образец читается только как источник структуры, формулировок и оформления
и НЕ изменяется (см. tests/test_arenda_ts_template.py).

Что воспроизводится по образцу:
  * поля страницы — российский стандарт: левое 2,0 см, правое 1,5 см,
    верхнее и нижнее по 2,0 см; формат страницы A4 (21,0 × 29,7 см),
    а НЕ Letter образца (21,59 × 27,94 см) (см. «Что сделано иначе»);
  * шрифт Times New Roman: основной текст 10,5 pt (как Normal образца,
    w:sz = 21), заголовок договора 13 pt bold, «(на один рейс)» 11 pt bold,
    заголовок Акта 12 pt bold, блок реквизитов и таблицы Акта 9 pt,
    шапка Приложения № 1 — 8,5 pt справа с разрывом страницы;
  * шапка: заголовок в две строки, «(на один рейс)», строка «г. Москва …
    дата договора» таблицей 1×2 без рамок;
  * разделы 1–8 и их формулировки — дословно из образца (кроме мест,
    где в образце стоят данные: они заменены плейсхолдерами);
  * таблица автомобилей п. 3.1: шапка «№ / Марка, модель / VIN-номер /
    Точка погрузки / Точка выгрузки», ширины колонок 453 / 1360 / 2382 /
    1984 / 2552 twips, «Table Grid», повтор шапки на новой странице;
  * раздел 9 «Реквизиты и подписи сторон» — таблица 1×2 с блоками
    АРЕНДАТОР / АРЕНДОДАТЕЛЬ (9 pt, как в образце);
  * Приложение № 1: шапка справа, заголовок Акта, вводный абзац, две
    таблицы (передача и возврат ТС) и таблица подписей 1×2.

Что сделано иначе, чем в образце (осознанные решения):
  * геометрия страницы — A4 (21,0 × 29,7 см) с полями российского
    стандарта (2,0 / 1,5 / 2,0 / 2,0 см) вместо Letter образца
    (21,59 × 27,94 см) с полями 2,0 / 1,7 см: договор российский,
    печатают его на A4. Ширины таблиц, которые в образце занимают всю
    полосу набора (реквизиты сторон и подписи Акта), пересчитаны под
    полосу набора A4 — иначе на A4 они вылезали бы в правое поле;
  * в образце номер договора (ТЛ-574), город и дата подставлены данными —
    здесь номер и дата договора плейсхолдеры, город остаётся «г. Москва»;
  * п. 4.3 в «ИП без НДС» сформулирован про УСН: оставлять в этом варианте
    утверждение «является плательщиком НДС» рядом с «НДС не облагается»
    нельзя — документ противоречил бы сам себе;
  * п. 2.3 не повторяет маршрут значением (маршрут раскрыт в п. 3.4),
    поэтому плейсхолдер {{route}} встречается в договоре один раз;
  * в Акте поля для ручного заполнения оставлены пустыми ячейками
    (в образце там подчёркивания);
  * строки «Водительское удостоверение» и «Дата выдачи» в п. 3.5 разведены
    на два плейсхолдера (driver_license / driver_license_issue_date);
  * убраны опечатки образца (пропущенная запятая после ФИО в п. 1.2,
    «Точка выгрузки № 1-», хвостовые пробелы).

Плейсхолдер всегда лежит в ОДНОМ run: docxtpl (Jinja) не склеивает
переменные, разорванные по runs.

Запуск:  python tools/make_arenda_ts_template.py

ВНИМАНИЕ: шаблоны закреплены по SHA256 в tests/test_arenda_ts_template.py
(TEMPLATE_SHA256). После осознанной пересборки обнови эти три константы,
иначе тест test_templates_match_pinned_sha упадёт (шаг 3.1.D.A.1-A.3-fix:
именно так поймали ручную правку .docx мимо сборщика).
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

#: Шрифт образца: весь договор набран Times New Roman.
FONT = "Times New Roman"

#: Кегли образца: Normal — 10,5 pt (w:sz 21), заголовок договора — 13 pt,
#: «(на один рейс)» — 11 pt, заголовок Акта — 12 pt, реквизиты и таблицы
#: Акта — 9 pt, шапка Приложения № 1 — 8,5 pt.
TEXT_SIZE = 10.5
TITLE_SIZE = 13
SUBTITLE_SIZE = 11
ACT_TITLE_SIZE = 12
SMALL_SIZE = 9
APPENDIX_SIZE = 8.5

#: Геометрия страницы: A4 (21,0 × 29,7 см = 11906 × 16838 twips) и поля
#: российского стандарта — левое 2,0 см, правое 1,5 см, верхнее и нижнее
#: по 2,0 см (1134 / 851 / 1134 / 1134 twips). Образец свёрстан в формате
#: Letter и с полями 2,0 / 1,7 см: его геометрия НЕ воспроизводится
#: (шаг 3.1.D.A.1-A.3-fix).
PAGE_WIDTH_TWIPS = 11906
PAGE_HEIGHT_TWIPS = 16838
MARGIN_LEFT_TWIPS = 1134
MARGIN_RIGHT_TWIPS = 851
MARGIN_TOP_TWIPS = 1134
MARGIN_BOTTOM_TWIPS = 1134

#: Ширина текстового блока A4: 11906 − 1134 − 851 = 9921 twips.
TEXT_WIDTH_TWIPS = (PAGE_WIDTH_TWIPS - MARGIN_LEFT_TWIPS
                    - MARGIN_RIGHT_TWIPS)

#: Ширины колонок таблицы автомобилей п. 3.1 — ровно как в образце.
CAR_COLUMN_WIDTHS = (453, 1360, 2382, 1984, 2552)

#: Ширины колонок таблиц реквизитов и подписей: в образце такая таблица
#: занимает всю полосу набора (4986 + 4986 = 9972 twips при Letter) —
#: на A4 делим полосу набора A4. Полоса набора A4 нечётная (9921 twips),
#: поэтому лишний twip достаётся правой колонке, а сумма колонок ровно
#: равна полосе набора: иначе таблица шире текста на 1 twip.
SIGN_COLUMN_WIDTHS = (TEXT_WIDTH_TWIPS // 2, TEXT_WIDTH_TWIPS
                      - TEXT_WIDTH_TWIPS // 2)

#: Пропорции колонок таблиц Акта из образца: 4572 / 5580 twips.
ACT_COLUMN_RATIO = (4572, 5580)

#: Ширины колонок таблиц Акта: пропорции образца (4572/5580), приведённые
#: к ширине текстового блока A4 (в образце таблица шире полосы набора).
_ACT_FIRST_WIDTH = round(
    TEXT_WIDTH_TWIPS * ACT_COLUMN_RATIO[0] / sum(ACT_COLUMN_RATIO)
)
ACT_COLUMN_WIDTHS = (_ACT_FIRST_WIDTH, TEXT_WIDTH_TWIPS - _ACT_FIRST_WIDTH)

#: Заливка шапки таблицы автомобилей (как в шаблонах Формики и перевозки).
HEADER_FILL = "E5E5E5"

#: Размер таблицы автомобилей: бланк рассчитан на 12 машин, лишние строки
#: удаляет постобработка генератора.
CAR_ROWS = 12

#: Максимум точек погрузки/выгрузки: неиспользованные блоки удаляет
#: постобработка генератора.
MAX_POINTS = 10

#: Интервалы: у обычных строк — 3 pt после (spacing after образца),
#: у заголовков разделов — 4 pt до.
BODY_SPACE_AFTER = 3
SECTION_SPACE_BEFORE = 4

#: Три варианта арендатора. Ключи совпадают с TEMPLATE_NAMES перевозки:
#: «ООО» / «ИП с НДС» / «ИП без НДС» — так же будет выбирать шаблон
#: генератор договора аренды.
VARIANTS: Dict[str, Dict[str, Any]] = {
    "ООО": {
        "filename": "shablon_arenda_ts_ooo.docx",
        # «...», именуемое в дальнейшем «Арендатор» — форма для юрлица.
        "lessee_verbal": "именуемое",
        # Раздел 9: ИНН + КПП + ОГРН, подпись генерального директора.
        "with_kpp": True,
        # Раздел 4: три суммы (без НДС / НДС по ставке / итого).
        "with_vat": True,
    },
    "ИП с НДС": {
        "filename": "shablon_arenda_ts_ip_with_vat.docx",
        # «...», именуемый в дальнейшем «Арендатор» — форма для ИП.
        "lessee_verbal": "именуемый",
        # Раздел 9: ИНН + ОГРНИП, без КПП.
        "with_kpp": False,
        "with_vat": True,
    },
    "ИП без НДС": {
        "filename": "shablon_arenda_ts_ip_without_vat.docx",
        "lessee_verbal": "именуемый",
        "with_kpp": False,
        # Раздел 4: одна сумма без НДС, «НДС не облагается».
        "with_vat": False,
    },
}


def resolve_variant(carrier_type: str) -> str:
    """
    Ключ варианта по типу арендатора.

    Правило то же, что у договора-заявки на перевозку
    (core/contracts/perevozka/generator.py::_get_template_path):
    «ИП без НДС» → ИП без НДС, «ИП с НДС» → ИП с НДС, всё остальное → ООО.
    """
    text = str(carrier_type)
    if "ИП без НДС" in text:
        return "ИП без НДС"
    if "ИП с НДС" in text:
        return "ИП с НДС"
    return "ООО"


def template_path(carrier_type: str) -> Path:
    """Путь шаблона варианта: templates/<имя файла из VARIANTS>."""
    return TEMPLATES_DIR / VARIANTS[resolve_variant(carrier_type)]["filename"]


def twips(value: int) -> Emu:
    """twips → EMU (1 twip = 635 EMU)."""
    return Emu(int(value) * 635)


# ─────────────────────────────────────────────────────────────
# Примитивы оформления
# ─────────────────────────────────────────────────────────────

def _set_run_font(run, size: float, bold: bool, underline: bool) -> None:
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


def _write_parts(paragraph, parts: Sequence[Any], size: float = TEXT_SIZE) -> None:
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


def _append_lines(paragraph, lines: Sequence[Sequence[Any]],
                  size: float) -> None:
    """
    Дописывает в абзац НЕСКОЛЬКО строк, разделённых разрывом (w:br).

    Разрыв ставится перед каждой строкой, кроме первой в пустом абзаце.
    Так в образце оформлены заголовок договора, шапка Приложения № 1,
    заголовок Акта и блоки реквизитов: это один абзац с разрывами строк,
    а не несколько абзацев.
    """
    for line in lines:
        if paragraph.runs:
            paragraph.runs[-1].add_break()
        _write_parts(paragraph, line, size)


def _prepare_paragraph(paragraph, align, size: float,
                       space_before: int = 0, space_after: int = 0):
    """Единые интервалы абзаца: одиночный, без лишних отбивок."""
    if align is not None:
        paragraph.alignment = align
    paragraph.paragraph_format.space_before = Pt(space_before)
    paragraph.paragraph_format.space_after = Pt(space_after)
    paragraph.paragraph_format.line_spacing = 1.0
    return paragraph


def add_paragraph(doc, parts: Sequence[Any], *, align=None, size: float = TEXT_SIZE,
                  space_before: int = 0, space_after: int = BODY_SPACE_AFTER,
                  page_break_before: bool = False):
    """Абзац из списка частей (каждая часть — отдельный run)."""
    paragraph = doc.add_paragraph()
    _prepare_paragraph(paragraph, align, size, space_before, space_after)
    if page_break_before:
        paragraph.paragraph_format.page_break_before = True

    _write_parts(paragraph, parts, size)
    return paragraph


def add_two_line_paragraph(doc, first_parts: Sequence[Any],
                           second_parts: Sequence[Any], **kwargs):
    """Абзац из двух строк, разделённых разрывом (w:br)."""
    paragraph = add_paragraph(doc, first_parts, **kwargs)
    _append_lines(paragraph, [second_parts], kwargs.get("size", TEXT_SIZE))
    return paragraph


def add_multi_line_paragraph(doc, lines: Sequence[Sequence[Any]], **kwargs):
    """Абзац из нескольких строк, разделённых разрывом (w:br)."""
    first = list(lines[0]) if lines else []
    paragraph = add_paragraph(doc, first, **kwargs)
    _append_lines(paragraph, lines[1:], kwargs.get("size", TEXT_SIZE))
    return paragraph


def shade_cell(cell, fill: str) -> None:
    """Заливка ячейки (w:shd)."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcPr.append(shd)


def set_cell_borders(cell, size_eighth_pt: int) -> None:
    """Границы ячейки в 1/8 pt (8 = 1 pt). Вставляются сразу после tcW."""
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), str(size_eighth_pt))
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), "000000")
        borders.append(element)

    tcW = tcPr.find(qn("w:tcW"))
    if tcW is not None:
        tcW.addnext(borders)
    else:
        tcPr.insert(0, borders)


def set_repeat_header(row) -> None:
    """Повтор строки таблицы на новой странице (w:tblHeader)."""
    trPr = row._tr.get_or_add_trPr()
    tblHeader = OxmlElement("w:tblHeader")
    tblHeader.set(qn("w:val"), "true")
    trPr.append(tblHeader)


def write_cell(cell, parts: Sequence[Any], *, align=WD_ALIGN_PARAGRAPH.CENTER,
               size: float = TEXT_SIZE) -> None:
    """Текст в первую (единственную) строку ячейки: одиночный интервал."""
    paragraph = _prepare_paragraph(cell.paragraphs[0], align, size)
    _write_parts(paragraph, parts, size)


def write_cell_lines(cell, lines: Sequence[Sequence[Any]], *,
                     align=WD_ALIGN_PARAGRAPH.LEFT, size: float = SMALL_SIZE):
    """Несколько строк в одну ячейку: один абзац, строки через w:br."""
    paragraph = _prepare_paragraph(cell.paragraphs[0], align, size)
    _append_lines(paragraph, lines, size)
    return paragraph


def prepare_empty_cell(cell, *, align=WD_ALIGN_PARAGRAPH.LEFT) -> None:
    """
    Пустая ячейка для ручного заполнения.

    Абзац внутри уже есть (python-docx создаёт его вместе с ячейкой) —
    приводим его к тому же виду, что и заполненные ячейки.
    """
    _prepare_paragraph(cell.paragraphs[0], align, TEXT_SIZE)


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
    """Страница и базовый шрифт документа — как в образце."""
    section = doc.sections[0]
    section.page_width = twips(PAGE_WIDTH_TWIPS)
    section.page_height = twips(PAGE_HEIGHT_TWIPS)
    section.left_margin = twips(MARGIN_LEFT_TWIPS)
    section.right_margin = twips(MARGIN_RIGHT_TWIPS)
    section.top_margin = twips(MARGIN_TOP_TWIPS)
    section.bottom_margin = twips(MARGIN_BOTTOM_TWIPS)

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


def build_title(doc) -> None:
    """Шапка: заголовок договора, «(на один рейс)» и строка «г. Москва … дата»."""
    add_two_line_paragraph(
        doc,
        [("ДОГОВОР АРЕНДЫ ТРАНСПОРТНОГО СРЕДСТВА",
          {"bold": True, "size": TITLE_SIZE})],
        [("С ЭКИПАЖЕМ № {{contract_number}}",
          {"bold": True, "size": TITLE_SIZE})],
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_before=6,
        space_after=6,
    )
    add_paragraph(
        doc,
        [("(на один рейс)", {"bold": True, "size": SUBTITLE_SIZE})],
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_after=7,
    )

    # Город и дата договора — таблица 1×2 без рамок, как в образце.
    table = doc.add_table(rows=1, cols=2)
    set_table_widths(table, SIGN_COLUMN_WIDTHS)
    write_cell(table.rows[0].cells[0], ["г. Москва"],
               align=WD_ALIGN_PARAGRAPH.LEFT)
    write_cell(table.rows[0].cells[1], ["{{contract_date}} г."],
               align=WD_ALIGN_PARAGRAPH.RIGHT)


def build_parties_section(doc, variant: Dict[str, Any]) -> None:
    """Раздел 1 «Стороны, правовая природа и статус»."""
    add_paragraph(doc, [("1. СТОРОНЫ, ПРАВОВАЯ ПРИРОДА И СТАТУС",
                         {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE)

    add_paragraph(doc, [
        "1.1. Арендатор: {{lessee_full_name}} ({{lessee_short_name}}), ",
        f"{variant['lessee_verbal']} в дальнейшем «Арендатор», в лице ",
        "{{lessee_director_position}} {{lessee_director_name}}, действующего "
        "на основании {{lessee_basis}}.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "1.2. Арендодатель: {{lessor_full_name}} ({{lessor_short_name}}), "
        "ИНН {{lessor_inn}}, ОГРН {{lessor_ogrn}}, в лице "
        "{{lessor_director_position}} {{lessor_director_name}}, действующего "
        "на основании {{lessor_basis}}, именуемое в дальнейшем "
        "«Арендодатель».",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "1.3. Настоящий Договор заключен в соответствии со статьями 632–641 "
        "Гражданского кодекса Российской Федерации. Арендодатель "
        "предоставляет Арендатору за плату указанное в настоящем Договоре "
        "транспортное средство во временное владение и пользование и своими "
        "силами оказывает услуги по управлению транспортным средством и его "
        "технической эксплуатации.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "1.4. Настоящий Договор регулирует отношения аренды транспортного "
        "средства с экипажем и не является договором перевозки груза между "
        "Арендатором и Арендодателем. Арендодатель не принимает на себя "
        "статус перевозчика по отношению к Арендатору. Арендатор "
        "осуществляет коммерческую эксплуатацию транспортного средства от "
        "своего имени и вправе заключать с третьими лицами договоры "
        "перевозки и иные договоры, соответствующие целям использования "
        "транспортного средства.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "1.5. Упоминание в настоящем Договоре маршрута, автомобилей, мест "
        "погрузки/выгрузки и сроков рейса определяет согласованные цели и "
        "пределы коммерческого использования арендованного транспортного "
        "средства и само по себе не означает принятия Арендодателем "
        "обязательства перевозчика по доставке груза.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)


def build_subject_section(doc) -> None:
    """Раздел 2 «Предмет договора и объект аренды»."""
    add_paragraph(doc, [("2. ПРЕДМЕТ ДОГОВОРА И ОБЪЕКТ АРЕНДЫ",
                         {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE)

    add_paragraph(doc, [
        "2.1. Арендодатель обязуется передать Арендатору на срок одного "
        "согласованного рейса во временное владение и пользование автопоезд "
        "(далее совместно — «Транспортное средство» или «ТС») и предоставить "
        "экипаж для управления и технической эксплуатации:",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "– тягач: {{tractor_brand}}, государственный регистрационный знак "
        "{{tractor_plate}}, тип ТС — {{tractor_type}};",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "– прицеп/полуприцеп: {{trailer_brand}}, государственный "
        "регистрационный знак {{trailer_plate}}.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "2.2. Идентифицирующие сведения из свидетельств о регистрации ТС, "
        "включая VIN/номер шасси при наличии, подтверждаются копиями "
        "регистрационных документов, передаваемыми Арендатору до начала "
        "рейса. Такие копии являются неотъемлемой частью подтверждения "
        "объекта аренды.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "2.3. Цель аренды — коммерческое использование ТС Арендатором для "
        "выполнения одного согласованного рейса по маршруту, указанному "
        "в п. 3.4 настоящего Договора, с возможностью погрузки и выгрузки "
        "автомобилей в нескольких согласованных точках. Перечень "
        "автомобилей и распределение каждого автомобиля по точке погрузки "
        "и точке выгрузки определяются п. 3.1 настоящего Договора.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "2.4. Срок аренды начинается с момента фактического предоставления "
        "ТС Арендатору в первой согласованной точке коммерческого "
        "использования и передачи ТС во временное владение и пользование, "
        "что подтверждается Актом приема-передачи, а при его отсутствии — "
        "совокупностью документов и электронных сообщений, достоверно "
        "подтверждающих фактическое предоставление ТС. Срок аренды "
        "заканчивается после завершения коммерческого использования "
        "в последней согласованной точке выгрузки и возврата ТС "
        "Арендодателю, подтвержденного Актом возврата.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "2.5. Плановый период аренды: с {{lease_start_date}} г. по "
        "{{lease_end_date}} г. включительно. Если фактический рейс по "
        "причинам, не зависящим от Арендатора, завершается позднее, Договор "
        "продолжает действовать до фактического возврата ТС; арендная плата "
        "при этом не увеличивается, если иное письменно не согласовано "
        "Сторонами.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "2.6. Арендодатель гарантирует, что является собственником ТС либо "
        "обладает иным законным правом, позволяющим передать ТС в аренду "
        "с экипажем, и что передача ТС Арендатору не нарушает прав третьих "
        "лиц. По требованию Арендатора Арендодатель предоставляет "
        "документы, подтверждающие соответствующее право.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "2.7. Замена тягача, прицепа/полуприцепа или члена экипажа "
        "допускается только с предварительного письменного согласия "
        "Арендатора, кроме аварийной ситуации, когда немедленная замена "
        "необходима для безопасности. В таком случае Арендодатель обязан "
        "незамедлительно уведомить Арендатора и предоставить эквивалентное "
        "по назначению и техническим характеристикам ТС.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)


def build_car_table(doc):
    """Таблица автомобилей п. 3.1: шапка + 12 строк с плейсхолдерами."""
    table = doc.add_table(rows=1 + CAR_ROWS, cols=5)
    table.style = "Table Grid"
    set_table_widths(table, CAR_COLUMN_WIDTHS)

    headers = ("№", "Марка, модель", "VIN-номер", "Точка погрузки",
               "Точка выгрузки")
    for cell, title in zip(table.rows[0].cells, headers):
        write_cell(cell, [(title, {"bold": True})])
        shade_cell(cell, HEADER_FILL)
    set_repeat_header(table.rows[0])

    for number in range(1, CAR_ROWS + 1):
        row = table.rows[number]
        write_cell(row.cells[0], [str(number)])
        write_cell(row.cells[1], [f"{{{{car_{number}_brand}}}}"],
                   align=WD_ALIGN_PARAGRAPH.LEFT)
        write_cell(row.cells[2], [f"{{{{car_{number}_vin}}}}"])
        write_cell(row.cells[3], [f"{{{{car_{number}_loading_point}}}}"],
                   align=WD_ALIGN_PARAGRAPH.LEFT)
        write_cell(row.cells[4], [f"{{{{car_{number}_unloading_point}}}}"],
                   align=WD_ALIGN_PARAGRAPH.LEFT)

    return table


def build_route_points(doc) -> None:
    """Пункты 3.2 и 3.3: до 10 точек погрузки и до 10 точек выгрузки."""
    add_paragraph(doc, ["3.2. Согласованные точки погрузки:"])

    for number in range(1, MAX_POINTS + 1):
        add_paragraph(doc, [
            f"3.2.{number}. Точка погрузки № {number} — ",
            f"{{{{loading_{number}_address}}}}. Плановая дата и время подачи "
            f"ТС: {{{{loading_{number}_date}}}} г., с "
            f"{{{{loading_{number}_time_from}}}} до "
            f"{{{{loading_{number}_time_to}}}}.",
        ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, ["3.3. Согласованные точки выгрузки:"])

    for number in range(1, MAX_POINTS + 1):
        add_paragraph(doc, [
            f"3.3.{number}. Точка выгрузки № {number} — ",
            f"{{{{unloading_{number}_address}}}}. Плановая дата завершения: "
            f"{{{{unloading_{number}_date}}}} г.",
        ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)


def build_driver_block(doc) -> None:
    """Пункт 3.5: член экипажа Арендодателя (водитель) — построчно."""
    add_paragraph(doc, ["3.5. Член экипажа Арендодателя (водитель):"])

    lines = (
        "ФИО: {{driver_full_name}}",
        "Дата рождения: {{driver_birth_date}}",
        "Паспорт: {{driver_passport}}",
        "Выдан: {{driver_passport_issuer}}",
        "Дата выдачи: {{driver_passport_issue_date}}",
        "Водительское удостоверение: {{driver_license}}",
        "Дата выдачи: {{driver_license_issue_date}}",
        "Адрес регистрации: {{driver_address}}",
        "Телефон: {{driver_phone}}",
    )
    for line in lines:
        add_paragraph(doc, [line], align=WD_ALIGN_PARAGRAPH.JUSTIFY)


def build_usage_section(doc) -> None:
    """Раздел 3 «Условия коммерческого использования на рейс»."""
    add_paragraph(doc, [("3. УСЛОВИЯ КОММЕРЧЕСКОГО ИСПОЛЬЗОВАНИЯ НА РЕЙС",
                         {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE)

    add_paragraph(doc, ["3.1. Автомобили, размещаемые Арендатором на ТС "
                        "в рамках коммерческой эксплуатации:"])
    build_car_table(doc)
    add_paragraph(doc, ["Общее количество: {{cargo_count}} шт."],
                  space_before=BODY_SPACE_AFTER)

    add_paragraph(doc, [
        "3.1.1. По каждому автомобилю до начала соответствующей погрузки "
        "Стороны фиксируют точку погрузки и точку выгрузки путем отметки "
        "в таблице п. 3.1 либо в письменном коммерческом распоряжении "
        "Арендатора, направленном по ЭДО, электронной почте или "
        "в согласованном Сторонами мессенджере. Такое распоряжение "
        "является частью условий коммерческого использования ТС по "
        "настоящему Договору. Изменение распределения конкретного "
        "автомобиля между согласованными точками в пределах маршрута само "
        "по себе не образует нового рейса и не изменяет арендную плату, "
        "если Стороны письменно не согласовали иное.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    build_route_points(doc)

    add_paragraph(doc, ["3.4. Согласованный маршрут: {{route}}."],
                  align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "Рейс может включать последовательную погрузку автомобилей в одной "
        "или нескольких точках и последовательную выгрузку автомобилей "
        "в одной или нескольких точках согласно распределению по п. 3.1. "
        "Арендатор вправе давать экипажу обязательные распоряжения, "
        "относящиеся к коммерческой эксплуатации ТС, включая "
        "последовательность подачи, конкретные автомобили/VIN для погрузки "
        "и выгрузки, места заезда, стоянки и маршрут в пределах разумного "
        "и законного использования ТС. Распоряжения, касающиеся "
        "непосредственно управления, безопасности и технической "
        "эксплуатации, относятся к компетенции Арендодателя и экипажа.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    build_driver_block(doc)

    add_paragraph(doc, [
        "3.6. Арендодатель гарантирует, что указанный водитель является его "
        "работником в смысле п. 2 ст. 635 ГК РФ, допущен к управлению "
        "соответствующей категорией ТС, обладает необходимой квалификацией "
        "и соблюдает обязательные требования к режиму труда и отдыха. Все "
        "расчеты с экипажем, включая оплату труда, суточные и иные расходы "
        "на его содержание, производит Арендодатель, если Стороны письменно "
        "не согласовали иное.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "3.7. Арендодатель обеспечивает оформление путевого листа "
        "в случаях и порядке, предусмотренных законодательством, а также "
        "необходимые предрейсовые/предсменные мероприятия в отношении "
        "водителя и технического состояния ТС. Путевой лист оформляется "
        "Арендодателем как лицом, предоставившим ТС во временное владение "
        "и пользование по договору аренды с экипажем.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "3.8. Если для исполнения договоров Арендатора с третьими лицами "
        "требуется подписание водителем транспортных или сопроводительных "
        "документов от имени Арендатора, такое подписание допускается "
        "только в пределах выданного Арендатором поручения/доверенности "
        "либо иного подтвержденного полномочия. Сам по себе факт подписи "
        "водителя не изменяет правовую природу настоящего Договора и не "
        "делает Арендодателя перевозчиком по отношению к Арендатору.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "3.9. Обмен адресами, временными окнами, распределением конкретных "
        "автомобилей/VIN по точкам погрузки и выгрузки, коммерческими "
        "распоряжениями, подтверждениями, контактами и иными рабочими "
        "данными допускается по электронной почте и в мессенджерах. Такая "
        "переписка признается Сторонами допустимым доказательством "
        "согласования условий исполнения Договора.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)


def build_price_section(doc, variant: Dict[str, Any]) -> None:
    """
    Раздел 4 «Арендная плата, НДС и порядок оплаты».

    ООО и ИП с НДС — три суммы (без НДС, НДС по ставке, итого), каждая
    цифрами и прописью; ИП без НДС — одна сумма и «НДС не облагается»
    (плейсхолдеров sum_wo_vat / vat_rate / sum_vat в этом бланке нет).
    """
    add_paragraph(doc, [("4. АРЕНДНАЯ ПЛАТА, НДС И ПОРЯДОК ОПЛАТЫ",
                         {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE)

    add_paragraph(doc, [
        "4.1. Арендная плата за предоставление ТС с экипажем на весь "
        "согласованный рейс составляет:",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    if variant["with_vat"]:
        add_paragraph(doc, [
            "– {{sum_wo_vat}} руб. ({{sum_wo_vat_words}}) — стоимость "
            "без НДС;",
        ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)
        add_paragraph(doc, [
            "– НДС {{vat_rate}} — {{sum_vat}} руб. ({{sum_vat_words}});",
        ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)
        add_paragraph(doc, [
            "Итого с НДС: {{sum_total}} руб. ({{sum_total_words}}).",
        ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    else:
        add_paragraph(doc, [
            "{{sum_total}} руб. ({{sum_total_words}}).",
        ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)
        add_paragraph(doc, [
            "НДС не облагается (упрощённая система налогообложения).",
        ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "4.2. Стороны специально согласовали иное распределение расходов, "
        "чем предусмотрено диспозитивным правилом ст. 636 ГК РФ: "
        "в указанную фиксированную арендную плату включены расходы "
        "Арендодателя на топливо и иные расходуемые материалы, оплату "
        "труда и содержание экипажа, техническое обслуживание и ремонт ТС, "
        "обязательные сборы, платные автомобильные дороги, необходимые "
        "стоянки и иные обычные расходы, непосредственно связанные "
        "с предоставлением и эксплуатацией ТС на согласованном рейсе, если "
        "иное заранее письменно не согласовано Сторонами.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    if variant["with_vat"]:
        add_paragraph(doc, [
            "4.3. Арендодатель подтверждает, что применяет общую систему "
            "налогообложения и является плательщиком НДС. При изменении "
            "обязательной ставки НДС налог определяется по ставке, "
            "действующей в соответствии с законодательством на момент "
            "определения налоговой базы; изменение общей суммы допускается "
            "только в случаях, прямо предусмотренных законодательством или "
            "отдельным письменным соглашением Сторон.",
        ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    else:
        add_paragraph(doc, [
            "4.3. Арендодатель подтверждает, что применяет упрощённую "
            "систему налогообложения и не является плательщиком НДС; "
            "арендная плата указана без НДС и НДС не облагается. Если "
            "в течение срока действия Договора у Арендодателя возникнет "
            "обязанность по исчислению НДС, Стороны согласуют порядок "
            "расчетов дополнительным письменным соглашением.",
        ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "4.4. Основанием для оплаты являются: настоящий Договор; счет "
        "Арендодателя; УПД и/или акт оказанных услуг по управлению "
        "и технической эксплуатации/акт аренды; Акт приема-передачи "
        "и возврата ТС. Сведения из электронной транспортной накладной "
        "(ЭТрН), ее визуализация либо иной перевозочный документ по "
        "внешнему договору Арендатора могут использоваться исключительно "
        "как дополнительное подтверждение фактического завершения рейса "
        "и не являются документом об оказании Арендодателем услуг "
        "перевозки. Перевозочные документы оформляются в форме, "
        "обязательной по законодательству на дату перевозки.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "4.5. Оплата производится в течение 30 (тридцати) банковских дней "
        "с даты фактического возврата ТС и получения Арендатором последнего "
        "из обязательных надлежащим образом оформленных расчетных "
        "и закрывающих документов, в том числе через ЭДО.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "4.6. При наличии ошибок в счетах-фактурах, УПД или иных "
        "документах, препятствующих их отражению в бухгалтерском/налоговом "
        "учете, течение срока оплаты приостанавливается до устранения "
        "ошибок и предоставления исправленных документов.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    add_paragraph(doc, [
        "4.7. Арендатор вправе произвести зачет документально "
        "подтвержденных встречных денежных требований к Арендодателю "
        "с соблюдением требований законодательства.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY)


def build_operation_section(doc) -> None:
    """Раздел 5 «Передача, эксплуатация, содержание и возврат ТС»."""
    add_paragraph(doc, [("5. ПЕРЕДАЧА, ЭКСПЛУАТАЦИЯ, СОДЕРЖАНИЕ И ВОЗВРАТ ТС",
                         {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE)

    paragraphs = (
        "5.1. Передача ТС в аренду и его возврат оформляются Актом по форме "
        "Приложения № 1. В Акте фиксируются дата, время, место, пробег, "
        "внешнее состояние ТС, наличие документов и при необходимости иные "
        "сведения. Допускается подписание акта скан-копиями, посредством "
        "ЭДО или иным способом, позволяющим достоверно установить волю "
        "Сторон.",

        "5.2. До передачи ТС Арендодатель предоставляет Арендатору для "
        "проверки копии регистрационных документов на тягач "
        "и прицеп/полуприцеп, действующий полис ОСАГО, документы водителя "
        "и иные обязательные документы, необходимые для законной "
        "эксплуатации ТС.",

        "5.3. Арендодатель обязан в течение всего срока аренды поддерживать "
        "ТС в надлежащем, технически исправном и безопасном состоянии, "
        "осуществлять за свой счет текущий и капитальный ремонт "
        "и предоставлять необходимые принадлежности, если иное прямо "
        "не предусмотрено настоящим Договором.",

        "5.4. Экипаж обеспечивает безопасное управление и техническую "
        "эксплуатацию ТС, соблюдение обязательных правил движения, "
        "требований к режиму труда и отдыха, а также выполнение "
        "технических операций по размещению и креплению автомобилей "
        "на автовозе в той части, в которой такие операции относятся "
        "к безопасной эксплуатации ТС. Арендатор обязан предоставить "
        "достоверные сведения о размещаемых автомобилях и не давать "
        "распоряжений, противоречащих законодательству, техническим "
        "ограничениям или требованиям безопасности.",

        "5.5. При технической неисправности ТС либо иной невозможности "
        "продолжить его использование по причинам, относящимся к сфере "
        "ответственности Арендодателя, Арендодатель обязан незамедлительно "
        "устранить неисправность либо, с согласия Арендатора, предоставить "
        "эквивалентное исправное ТС с экипажем. Срок замены — не более "
        "48 часов с момента возникновения препятствия, если более короткий "
        "срок объективно возможен.",

        "5.6. Без согласия Арендатора не допускаются замена "
        "тягача/прицепа, передача управления иному водителю, перецепка "
        "либо иные изменения состава автопоезда, кроме действий, объективно "
        "необходимых для предотвращения аварии, вреда людям или имуществу. "
        "Обо всех таких действиях Арендодатель уведомляет Арендатора "
        "незамедлительно.",

        "5.7. По заданию Арендатора экипаж осуществляет фотофиксацию "
        "автомобилей при их размещении на ТС и снятии с ТС и передает "
        "материалы Арендатору. Такая фотофиксация является дополнительной "
        "обязанностью в рамках эксплуатации ТС и не означает принятия "
        "Арендодателем автомобилей к перевозке в качестве перевозчика.",

        "5.8. Если фактическое использование ТС временно невозможно "
        "из-за технической неисправности, отсутствия экипажа или иного "
        "нарушения со стороны Арендодателя, Арендатор не несет "
        "дополнительных расходов за соответствующий период; фиксированная "
        "плата за рейс не увеличивается.",
    )
    for text in paragraphs:
        add_paragraph(doc, [text], align=WD_ALIGN_PARAGRAPH.JUSTIFY)


def build_liability_section(doc) -> None:
    """Раздел 6 «Страхование и ответственность сторон»."""
    add_paragraph(doc, [("6. СТРАХОВАНИЕ И ОТВЕТСТВЕННОСТЬ СТОРОН",
                         {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE)

    paragraphs = (
        "6.1. Арендодатель обязан обеспечить наличие в течение всего срока "
        "аренды обязательного страхования ТС и ответственности в случаях, "
        "когда такое страхование требуется законом или настоящим Договором, "
        "и предоставить подтверждающие документы по требованию Арендатора.",

        "6.2. Арендодатель отвечает за действия и бездействие экипажа как "
        "за свои собственные, включая нарушения правил управления "
        "и технической эксплуатации ТС, а также за техническую исправность "
        "ТС в пределах обязанностей, возложенных на Арендодателя законом "
        "и настоящим Договором.",

        "6.3. За вред, причиненный третьим лицам арендованным ТС, его "
        "механизмами, устройствами или оборудованием, ответственность "
        "распределяется в соответствии со ст. 640 ГК РФ. Право Арендодателя "
        "на регресс к Арендатору возникает при наличии и доказанности вины "
        "Арендатора в предусмотренных законом случаях.",

        "6.4. В случае гибели или повреждения арендованного ТС Арендатор "
        "возмещает Арендодателю убытки только при условии, что Арендодатель "
        "докажет обстоятельства, за которые Арендатор отвечает "
        "в соответствии с законом или настоящим Договором.",

        "6.5. Арендодатель не несет ответственность за сохранность "
        "размещенных на ТС автомобилей на условиях презумпции "
        "ответственности перевозчика. Вместе с тем Арендодатель обязан "
        "возместить Арендатору документально подтвержденный реальный "
        "ущерб, причиненный таким автомобилям, если доказана причинная "
        "связь ущерба с:",

        "– технической неисправностью или ненадлежащим состоянием ТС, "
        "за которое отвечает Арендодатель;",

        "– виновными действиями/бездействием экипажа при управлении или "
        "технической эксплуатации ТС;",

        "– нарушением экипажем обязательных требований к безопасному "
        "размещению или креплению автомобилей, если соответствующие "
        "действия выполнялись экипажем;",

        "– самовольным отступлением экипажа от законных коммерческих "
        "распоряжений Арендатора, если такое отступление явилось причиной "
        "ущерба.",

        "6.6. При наличии оснований по п. 6.5 размер подлежащего возмещению "
        "реального ущерба может включать стоимость восстановительного "
        "ремонта, документально подтвержденную утрату товарной стоимости, "
        "а также суммы, фактически и обоснованно уплаченные Арендатором его "
        "клиенту вследствие того же повреждения, при условии отсутствия "
        "двойного возмещения и доказанности прямой причинной связи.",

        "6.7. Арендодатель обязан незамедлительно уведомлять Арендатора "
        "о ДТП, поломке, возгорании, противоправных действиях третьих лиц "
        "и иных событиях, способных повлиять на безопасность ТС, "
        "автомобилей или сроки рейса, а при необходимости обращаться "
        "в компетентные органы и обеспечивать оформление предусмотренных "
        "законом документов.",

        "6.8. За непредоставление ТС в согласованные дату и временное окно "
        "по вине Арендодателя Арендодатель уплачивает Арендатору штраф "
        "в размере 20% от арендной платы по настоящему Договору "
        "и возмещает документально подтвержденные реальные убытки в части, "
        "не покрытой штрафом.",

        "6.9. За нарушение планового срока завершения рейса по причинам, "
        "вызванным неисправностью ТС, действиями/бездействием экипажа или "
        "иным нарушением Арендодателя, Арендодатель уплачивает Арендатору "
        "неустойку в размере 0,5% от арендной платы за каждый полный день "
        "просрочки, но не более 30% от арендной платы, а также возмещает "
        "документально подтвержденные реальные убытки в части, не покрытой "
        "неустойкой.",

        "6.10. Арендодатель обязан передать Арендатору предусмотренные "
        "настоящим Договором закрывающие и подтверждающие документы "
        "в течение 15 (пятнадцати) календарных дней после возврата ТС. "
        "За просрочку — неустойка 0,5% от арендной платы за каждый день, "
        "но не более 20% от арендной платы.",

        "6.11. Арендодатель отвечает за корректность выставленных им "
        "счетов-фактур, УПД и иных документов в части своих налоговых "
        "обязательств. Если отказ в вычете НДС, доначисление налога, пени "
        "или штраф возникли непосредственно вследствие документально "
        "подтвержденной ошибки Арендодателя, он возмещает Арендатору "
        "соответствующие реальные убытки после вступления решения "
        "налогового органа в силу либо их признания Арендодателем.",

        "6.12. Арендатор обязан своевременно оплачивать арендную плату, "
        "использовать ТС в согласованных целях, соблюдать установленные "
        "технические ограничения и не требовать от экипажа совершения "
        "незаконных или заведомо небезопасных действий.",
    )
    for text in paragraphs:
        add_paragraph(doc, [text], align=WD_ALIGN_PARAGRAPH.JUSTIFY)


def build_personal_data_section(doc) -> None:
    """Раздел 7 «Сервис „ТехноЩит“ и персональные данные»."""
    add_paragraph(doc, [("7. СЕРВИС «ТЕХНОЩИТ» И ПЕРСОНАЛЬНЫЕ ДАННЫЕ",
                         {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE)

    paragraphs = (
        "7.1. Арендатор вправе использовать внутренний сервис контроля "
        "«ТехноЩит» для проверки Арендодателя, экипажа и ТС, мониторинга "
        "маршрута и стоянок, а также фиксации событий, связанных "
        "с использованием ТС.",

        "7.2. Арендодатель обязуется по разумным запросам Арендатора "
        "предоставлять копии документов на Арендодателя, ТС и водителя, "
        "а также сведения, необходимые для проверки и мониторинга, включая "
        "номера ТС и контактные данные.",

        "7.3. Арендодатель гарантирует наличие законных оснований для "
        "передачи Арендатору персональных данных членов экипажа и, когда "
        "это требуется законодательством, получение необходимых согласий, "
        "в том числе на использование данных в сервисе «ТехноЩит», "
        "фото/видеофиксацию и геолокацию.",

        "7.4. Стороны обязуются соблюдать требования Федерального закона "
        "№ 152-ФЗ «О персональных данных» и использовать полученные "
        "персональные данные только в объеме, необходимом для заключения "
        "и исполнения Договора, контроля использования ТС, соблюдения "
        "законодательства и защиты прав и законных интересов Сторон "
        "и клиентов Арендатора.",
    )
    for text in paragraphs:
        add_paragraph(doc, [text], align=WD_ALIGN_PARAGRAPH.JUSTIFY)


def build_final_section(doc) -> None:
    """Раздел 8 «Срок действия, форс-мажор, споры и заключительные положения»."""
    add_paragraph(doc, [
        ("8. СРОК ДЕЙСТВИЯ, ФОРС-МАЖОР, СПОРЫ И ЗАКЛЮЧИТЕЛЬНЫЕ ПОЛОЖЕНИЯ",
         {"bold": True}),
    ], space_before=SECTION_SPACE_BEFORE)

    paragraphs = (
        "8.1. Договор вступает в силу с момента его подписания Сторонами, "
        "в том числе путем обмена подписанными скан-копиями или через ЭДО, "
        "и действует до полного исполнения обязательств, но в любом случае "
        "в части расчетов и ответственности — до их завершения.",

        "8.2. Стороны освобождаются от ответственности за неисполнение "
        "обязательств вследствие обстоятельств непреодолимой силы при "
        "наличии предусмотренных законом признаков таких обстоятельств "
        "и причинной связи с неисполнением. Сторона обязана уведомить "
        "другую Сторону в разумный срок.",

        "8.3. Претензионный порядок обязателен. Срок ответа на письменную "
        "претензию — 10 (десять) календарных дней с даты ее получения.",

        "8.4. При недостижении соглашения спор подлежит рассмотрению "
        "в Арбитражном суде по месту нахождения Арендатора, если такая "
        "договорная подсудность допустима действующим законодательством.",

        "8.5. Изменения существенных условий настоящего Договора, включая "
        "замену ТС, экипажа, существенное изменение маршрута или арендной "
        "платы, допускаются в письменной форме, включая ЭДО, электронную "
        "почту и мессенджеры, если из переписки достоверно следует "
        "согласованная воля обеих Сторон.",

        "8.6. Договор составлен в письменной форме. Стороны признают "
        "юридическую силу подписанных скан-копий до момента обмена "
        "оригиналами, если обмен оригиналами необходим. Договор аренды ТС "
        "с экипажем заключается в письменной форме независимо от срока.",

        "8.7. Неотъемлемой частью Договора является Приложение № 1 — «Акт "
        "приема-передачи и возврата транспортного средства с экипажем». "
        "Распределение автомобилей/VIN по точкам погрузки и выгрузки "
        "в таблице п. 3.1 и последующие письменные коммерческие "
        "распоряжения Арендатора, согласованные в порядке п. 3.9, "
        "применяются совместно с настоящим Договором.",
    )
    for text in paragraphs:
        add_paragraph(doc, [text], align=WD_ALIGN_PARAGRAPH.JUSTIFY)


def _requisites_lines(prefix: str, variant: Dict[str, Any],
                      is_lessee: bool) -> List[List[Any]]:
    """
    Строки блока реквизитов одной стороны.

    У Арендатора-ООО добавляется строка КПП, у ИП её нет; заканчивается
    блок подписью (должность, фамилия с инициалами) и местом печати.
    """
    lines: List[List[Any]] = [
        [(f"{{{{{prefix}_full_name}}}}", {"bold": True, "size": SMALL_SIZE})],
        [(f"ИНН {{{{{prefix}_inn}}}}", {"bold": True, "size": SMALL_SIZE})],
    ]
    if is_lessee and variant["with_kpp"]:
        lines.append([(f"КПП {{{{{prefix}_kpp}}}}",
                       {"bold": True, "size": SMALL_SIZE})])
    lines.append([(f"{{{{{prefix}_ogrn_label}}}} {{{{{prefix}_ogrn}}}}",
                   {"bold": True, "size": SMALL_SIZE})])
    for line in (
        f"Юридический адрес: {{{{{prefix}_address}}}}",
        f"р/с {{{{{prefix}_account}}}} в {{{{{prefix}_bank}}}}",
        f"БИК {{{{{prefix}_bik}}}}",
        f"к/с {{{{{prefix}_corr_account}}}}",
        f"E-mail: {{{{{prefix}_email}}}}",
        f"ЭДО: {{{{{prefix}_edo}}}}",
        "",
        f"{{{{{prefix}_director_position}}}}______________/ "
        f"{{{{{prefix}_director_short}}}} /",
        "М.П.",
    ):
        lines.append([(line, {"bold": True, "size": SMALL_SIZE})])
    return lines


def build_requisites_section(doc, variant: Dict[str, Any]) -> None:
    """Раздел 9 «Реквизиты и подписи сторон» — таблица 1×2, как в образце."""
    add_paragraph(doc, [("9. РЕКВИЗИТЫ И ПОДПИСИ СТОРОН", {"bold": True})],
                  space_before=SECTION_SPACE_BEFORE)

    table = doc.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    set_table_widths(table, SIGN_COLUMN_WIDTHS)

    columns = (
        ("АРЕНДАТОР:", "lessee", True),
        ("АРЕНДОДАТЕЛЬ:", "lessor", False),
    )
    for cell, (title, prefix, is_lessee) in zip(table.rows[0].cells, columns):
        set_cell_borders(cell, 8)
        lines = [[(title, {"bold": True, "size": SMALL_SIZE})]]
        lines += _requisites_lines(prefix, variant, is_lessee)
        write_cell_lines(cell, lines)


def _build_act_table(doc, rows):
    """Таблица Акта 2×N: подпись поля слева, значение справа."""
    table = doc.add_table(rows=len(rows), cols=2)
    table.style = "Table Grid"
    set_table_widths(table, ACT_COLUMN_WIDTHS)

    for row, (label, value) in zip(table.rows, rows):
        set_cell_borders(row.cells[0], 8)
        set_cell_borders(row.cells[1], 8)
        write_cell(row.cells[0], [label], align=WD_ALIGN_PARAGRAPH.LEFT,
                   size=SMALL_SIZE)
        if value is None:
            prepare_empty_cell(row.cells[1])
        else:
            write_cell(row.cells[1], [value], align=WD_ALIGN_PARAGRAPH.LEFT,
                       size=SMALL_SIZE)
    return table


def build_appendix(doc) -> None:
    """Приложение № 1 — Акт приема-передачи и возврата ТС (новая страница)."""
    add_multi_line_paragraph(
        doc,
        [
            [("Приложение № 1", {"size": APPENDIX_SIZE})],
            [("к Договору аренды транспортного средства",
              {"size": APPENDIX_SIZE})],
            [("с экипажем № {{contract_number}} от {{contract_date}} г.",
              {"size": APPENDIX_SIZE})],
        ],
        align=WD_ALIGN_PARAGRAPH.RIGHT,
        size=APPENDIX_SIZE,
        space_after=6,
        page_break_before=True,
    )

    add_two_line_paragraph(
        doc,
        [("АКТ ПРИЕМА-ПЕРЕДАЧИ И ВОЗВРАТА",
          {"bold": True, "size": ACT_TITLE_SIZE})],
        [("ТРАНСПОРТНОГО СРЕДСТВА С ЭКИПАЖЕМ",
          {"bold": True, "size": ACT_TITLE_SIZE})],
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_after=6,
    )

    add_paragraph(doc, [
        "Настоящий Акт составлен во исполнение Договора аренды транспортного "
        "средства с экипажем № {{contract_number}} от {{contract_date}} г. "
        "и фиксирует фактическое предоставление ТС Арендатору во временное "
        "владение и пользование на согласованный рейс, а также его "
        "последующий возврат Арендодателю.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=6)

    # 1. Передача ТС в аренду.
    add_paragraph(doc, [("1. ПЕРЕДАЧА ТС В АРЕНДУ", {"bold": True})],
                  space_after=3)
    _build_act_table(doc, (
        ("Место передачи", None),
        ("Фактические дата и время передачи", None),
        ("Тягач", "{{tractor_brand}}, гос. номер {{tractor_plate}}"),
        ("Прицеп/полуприцеп",
         "{{trailer_brand}}, гос. номер {{trailer_plate}}"),
        ("Пробег на момент передачи", None),
        ("Внешнее состояние / замечания", None),
        ("Переданные документы",
         "СТС на тягач и прицеп/полуприцеп; ОСАГО; иные:"),
        ("Экипаж", "{{driver_full_name}}"),
    ))

    add_paragraph(doc, [
        "Арендодатель передал, а Арендатор принял ТС во временное владение "
        "и пользование. Арендатору предоставлена возможность осуществлять "
        "коммерческую эксплуатацию ТС в пределах Договора; управление "
        "и техническая эксплуатация осуществляются экипажем Арендодателя.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_before=BODY_SPACE_AFTER,
        space_after=6)

    # 2. Возврат ТС.
    add_paragraph(doc, [("2. ВОЗВРАТ ТС", {"bold": True})], space_after=3)
    _build_act_table(doc, (
        ("Место возврата", None),
        ("Фактические дата и время возврата", None),
        ("Пробег на момент возврата", None),
        ("Состояние ТС / замечания", None),
        ("Иные отметки", None),
    ))

    add_paragraph(doc, [
        "С момента, указанного в строке «Фактические дата и время возврата», "
        "ТС считается возвращенным Арендодателю, если в настоящем Акте "
        "не указано иное.",
    ], align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_before=BODY_SPACE_AFTER,
        space_after=8)

    # Подписи сторон в Акте.
    table = doc.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    set_table_widths(table, SIGN_COLUMN_WIDTHS)

    columns = (("АРЕНДАТОР:", "lessee"), ("АРЕНДОДАТЕЛЬ:", "lessor"))
    for cell, (title, prefix) in zip(table.rows[0].cells, columns):
        set_cell_borders(cell, 8)
        write_cell_lines(cell, [
            [(title, {"bold": True, "size": SMALL_SIZE})],
            [(f"{{{{{prefix}_short_name}}}}", {"size": SMALL_SIZE})],
            [(f"______________/ {{{{{prefix}_director_short}}}} /",
              {"size": SMALL_SIZE})],
            [("М.П.", {"size": SMALL_SIZE})],
        ])


def collect_placeholders(doc) -> List[str]:
    """Все плейсхолдеры документа (абзацы и таблицы) в порядке появления."""
    texts = [p.text for p in doc.paragraphs]
    texts += [c.text for t in doc.tables for r in t.rows for c in r.cells]
    return re.findall(r"\{\{[^{}]*\}\}", "\n".join(texts))


def build_template(carrier_type: str) -> Path:
    """
    Собирает бланк одного варианта («ООО» / «ИП с НДС» / «ИП без НДС»).

    Возвращает путь сохранённого шаблона.
    """
    variant_key = resolve_variant(carrier_type)
    variant = VARIANTS[variant_key]

    doc = Document()
    configure_document(doc)

    build_title(doc)
    build_parties_section(doc, variant)
    build_subject_section(doc)
    build_usage_section(doc)
    build_price_section(doc, variant)
    build_operation_section(doc)
    build_liability_section(doc)
    build_personal_data_section(doc)
    build_final_section(doc)
    build_requisites_section(doc, variant)
    build_appendix(doc)

    path = TEMPLATES_DIR / variant["filename"]
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def describe(path: Path) -> Tuple[int, int, int, int, int]:
    """Сводка по собранному бланку: абзацы, таблицы, строки машин, плейсхолдеры."""
    doc = Document(str(path))
    placeholders = collect_placeholders(doc)
    car_rows = len(doc.tables[1].rows) if len(doc.tables) > 1 else 0
    return (len(doc.paragraphs), len(doc.tables), car_rows,
            len(placeholders), len(set(placeholders)))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    for carrier_type in VARIANTS:
        path = build_template(carrier_type)
        paragraphs, tables, car_rows, total, unique = describe(path)
        print(f"[OK] {carrier_type}: {path}")
        print(f"     абзацев={paragraphs} таблиц={tables} "
              f"строк таблицы авто={car_rows}")
        print(f"     плейсхолдеров={total} уникальных={unique}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
