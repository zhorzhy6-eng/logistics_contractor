#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Условный блок «Без НДС» в бланке ООО (шаг «Ставки НДС в UI»).

Что чиним
---------
П. 4.1 бланка `templates/shablon_ooo.docx` всегда печатал две суммы и НДС:

    – {{sum_wo_nds}} руб. ({{sum_wo_nds_words}}) — стоимость услуг без НДС;
    – НДС по ставке, действующей на дату оказания услуг (в настоящее время
      {{vat_rate}}) {{sum_nds}} руб. ({{sum_nds_words}}).
    Итоговая сумма Договора (стоимость услуг с НДС): {{sum_total}} руб. ...

У ООО на УСН с доходом до 20 млн ₽ налога нет вовсе (п. 1 ст. 145 НК), и
такая печать прямо противоречит договору: в п. 4.2 стоит «не является
плательщиком НДС», а в п. 4.1 — ставка и сумма налога.

Что получается
--------------
Три абзаца п. 4.1 обёрнуты в условный блок docxtpl; ветвь `else` — прежний
текст БЕЗ ЕДИНОГО ИЗМЕНЕНИЯ (договоры с НДС печатаются как печатались):

    {%p if is_vat_free %}
    {{sum_total}} руб. ({{sum_total_words}}).
    НДС не облагается (упрощённая система налогообложения).
    {%p else %}
    …прежние три абзаца…
    {%p endif %}

Формулировка и оформление новых абзацев повторяют бланк ИП без НДС
(`shablon_ip_without_vat.docx`, п. 4.1): один и тот же случай должен
выглядеть в договорах одинаково.

Признак `is_vat_free` вычисляет генератор перевозки
(`core/vat.py::is_vat_free` → замена в карте замен).

Почему не python-docx
---------------------
`Document.save()` переписывает все части пакета: изменяются встроенные
шрифты (`word/fonts/`), идентификаторы и порядок элементов. Здесь же
перезаписывается РОВНО одна запись архива — `word/document.xml`, остальные
копируются побайтово (`ZipInfo` сохраняется как есть). Для бланков
перевозки это важно: в них встроены шрифты, а состав пакета закреплён
тестом `test_templates_change_only_in_place`.

Запуск:
    python tools/fix_ooo_template_vat_free.py --check   # только состояние
    python tools/fix_ooo_template_vat_free.py           # применить
    python tools/fix_ooo_template_vat_free.py --templates <папка>
"""

import argparse
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Бланк ООО: у ИП ветвь «без НДС» живёт в отдельном бланке.
TEMPLATE = "shablon_ooo.docx"

#: Часть пакета, в которой живёт п. 4.1.
DOCUMENT_PART = "word/document.xml"

#: Признак, по которому генератор выбирает ветвь (ставится в карте замен).
FLAG = "is_vat_free"

#: Опознавательные куски трёх абзацев п. 4.1 — по ним они и находятся.
ANCHORS = {
    "sum_wo_nds": "— стоимость услуг без НДС;",
    "sum_nds": "НДС по ставке, действующей на дату оказания услуг",
    "sum_total": "Итоговая сумма Договора (стоимость услуг с НДС)",
}

#: Тексты новой ветви — дословно как в бланке ИП без НДС.
TOTAL_FREE_TEXT = "{{sum_total}} руб. ({{sum_total_words}})."
VAT_FREE_TEXT = "НДС не облагается (упрощённая система налогообложения)."

#: Теги ветвления. `{%p ... %}` docxtpl понимает, только когда тег стоит в
#: абзаце ОДИН: тогда абзац с ним удаляется, а тег остаётся в XML.
IF_TAG = "{%p if " + FLAG + " %}"
ELSE_TAG = "{%p else %}"
ENDIF_TAG = "{%p endif %}"

PARAGRAPH_RE = re.compile(r"<w:p\b.*?</w:p>|<w:p\b[^>]*/>", re.S)


def _plain(paragraph_xml: str) -> str:
    """Текст абзаца без разметки — по нему ищем нужные абзацы."""
    return re.sub(r"<[^>]+>", "", paragraph_xml)


def _find_clause(paragraphs) -> tuple:
    """
    Индексы трёх абзацев п. 4.1: (без НДС, НДС, итог).

    Абзацы обязаны идти ПОДРЯД: условный блок оборачивает ровно их, и
    врозь они смысла не имеют. Не нашлись или идут не подряд — ошибка.
    """
    found = {}
    for index, paragraph in enumerate(paragraphs):
        text = _plain(paragraph)
        for key, anchor in ANCHORS.items():
            if key not in found and anchor in text:
                found[key] = index

    missing = [key for key in ANCHORS if key not in found]
    if missing:
        raise ValueError(f"в бланке не найдены абзацы п. 4.1: {missing}")

    order = (found["sum_wo_nds"], found["sum_nds"], found["sum_total"])
    if order != (order[0], order[0] + 1, order[0] + 2):
        raise ValueError(f"абзацы п. 4.1 идут не подряд: {order}")

    return order


def _run_properties(paragraph_xml: str) -> str:
    """
    Оформление run'а из образца — БЕЗ rPr абзацной метки.

    В `<w:pPr>` лежит свой `<w:rPr>` (оформление знака абзаца, кегль 24).
    Новые абзацы печатаются тем же шрифтом, что и «Итоговая сумма»: 11 pt,
    Times New Roman, поэтому rPr берётся ПОСЛЕ pPr.
    """
    ppr = re.search(r"<w:pPr>.*?</w:pPr>", paragraph_xml, re.S)
    tail = paragraph_xml[ppr.end():] if ppr else paragraph_xml
    rpr = re.search(r"<w:rPr>.*?</w:rPr>", tail, re.S)
    return rpr.group(0) if rpr else ""


def _paragraph_properties(paragraph_xml: str) -> str:
    """`<w:pPr>` образца: отступ первой строки и выравнивание как в п. 4.1."""
    ppr = re.search(r"<w:pPr>.*?</w:pPr>", paragraph_xml, re.S)
    return ppr.group(0) if ppr else ""


def _paragraph(paragraph_properties: str, run_properties: str, text: str) -> str:
    """
    Новый абзац бланка с оформлением образца.

    Атрибуты `<w:p>` (w14:paraId и прочие) не нужны: это расширения Word,
    без них абзац — обычный абзац WordprocessingML. Копировать их нельзя:
    одинаковые идентификаторы в одном документе — уже дефект.
    """
    return (
        f"<w:p>{paragraph_properties}"
        f"<w:r>{run_properties}<w:t>{text}</w:t></w:r></w:p>"
    )


def build_conditional_block(paragraphs, order: tuple) -> str:
    """Собирает условный блок целиком: теги + новая ветвь + прежние абзацы."""
    first, _second, last = order
    sample = paragraphs[last]

    ppr = _paragraph_properties(sample)
    rpr = _run_properties(sample)

    parts = [
        _paragraph(ppr, rpr, IF_TAG),
        _paragraph(ppr, rpr, TOTAL_FREE_TEXT),
        _paragraph(ppr, rpr, VAT_FREE_TEXT),
        _paragraph(ppr, rpr, ELSE_TAG),
    ]
    parts.extend(paragraphs[index] for index in order)
    parts.append(_paragraph(ppr, rpr, ENDIF_TAG))
    return "".join(parts)


def fix_document_xml(xml: str) -> tuple:
    """
    Оборачивает п. 4.1 в условный блок `{%p if is_vat_free %}`.

    Возвращает (новый_xml, число_правок). Правка ровно одна на документ;
    если блок уже стоит — 0 (скрипт идемпотентен).
    """
    if FLAG in xml:
        return xml, 0

    paragraphs = PARAGRAPH_RE.findall(xml)
    order = _find_clause(paragraphs)

    first = order[0]
    last = order[-1]

    matches = list(PARAGRAPH_RE.finditer(xml))
    start = matches[first].start()
    end = matches[last].end()

    block = build_conditional_block(
        [match.group(0) for match in matches], order
    )
    return xml[:start] + block + xml[end:], 1


def _rewrite_document_part(path: Path, new_xml: bytes) -> None:
    """
    Перезаписывает `word/document.xml`, не трогая остальные части пакета.

    ZipInfo каждой записи переносится как есть (дата, атрибуты, способ
    сжатия), поэтому встроенные шрифты и связи остаются побайтово теми же.
    """
    with zipfile.ZipFile(str(path)) as source:
        items = [(info, source.read(info.filename)) for info in source.infolist()]

    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as target:
        for info, data in items:
            if info.filename == DOCUMENT_PART:
                data = new_xml
            target.writestr(info, data)


def _parts(path: Path) -> dict:
    """Записи архива: имя → байты (для сверки «кроме document.xml»)."""
    with zipfile.ZipFile(str(path)) as archive:
        return {info.filename: archive.read(info.filename)
                for info in archive.infolist()}


def verify_other_parts(before: dict, after: dict) -> list:
    """
    Сверяет пакет после правки: все записи, КРОМЕ document.xml, побайтово те же.

    Возвращает список расхождений (пустой — всё совпало). Состав пакета и
    имена частей тоже проверяются: пропавший встроенный шрифт — это уже
    другой документ.
    """
    problems = []
    names_before = set(before) - {DOCUMENT_PART}
    names_after = set(after) - {DOCUMENT_PART}

    if names_before != names_after:
        problems.append(
            f"состав пакета изменился: пропало {sorted(names_before - names_after)}, "
            f"добавилось {sorted(names_after - names_before)}"
        )

    for name in sorted(names_before & names_after):
        if before[name] != after[name]:
            problems.append(f"запись {name} изменилась побайтово")

    return problems


def check_state(path: Path) -> str:
    """Состояние бланка: стоит ли условный блок и сколько ветвей в п. 4.1."""
    if not path.exists():
        return "НЕ НАЙДЕН"

    with zipfile.ZipFile(str(path)) as archive:
        xml = archive.read(DOCUMENT_PART).decode("utf-8")

    if FLAG not in xml:
        return "условного блока нет — п. 4.1 печатается всегда"

    parts = []
    for tag, title in ((IF_TAG, "if"), (ELSE_TAG, "else"), (ENDIF_TAG, "endif")):
        parts.append(f"{title}: {xml.count(tag)}")
    free = "НДС не облагается (упрощённая система налогообложения)." in xml
    return f"блок есть ({', '.join(parts)}), текст «НДС не облагается»: {free}"


def fix_template(path: Path, apply: bool) -> str:
    """Правит бланк. Возвращает отчёт одной строкой."""
    if not path.exists():
        return "НЕ НАЙДЕН"

    with zipfile.ZipFile(str(path)) as archive:
        xml = archive.read(DOCUMENT_PART).decode("utf-8")

    try:
        fixed, changes = fix_document_xml(xml)
    except ValueError as error:
        return f"ОШИБКА: {error}"

    if not changes:
        return "условный блок уже стоит — пропуск"

    if not apply:
        return "будет добавлен блок {%p if is_vat_free %} вокруг п. 4.1"

    before = _parts(path)
    _rewrite_document_part(path, fixed.encode("utf-8"))
    after = _parts(path)

    problems = verify_other_parts(before, after)
    if problems:
        raise RuntimeError("; ".join(problems))

    return (
        "добавлен блок {%p if is_vat_free %}: ветвь if — «НДС не облагается», "
        "ветвь else — прежний текст; остальные записи пакета побайтово те же"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="только показать состояние бланка")
    parser.add_argument("--templates", default=None,
                        help="папка с шаблонами (по умолчанию templates/)")
    parser.add_argument("--no-backup", action="store_true",
                        help="не делать резервную копию бланка")
    args = parser.parse_args()

    templates_dir = Path(args.templates) if args.templates else PROJECT_ROOT / "templates"
    path = templates_dir / TEMPLATE

    if args.check:
        print(f"{TEMPLATE}: {check_state(path)}")
        return 0

    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = PROJECT_ROOT / "backup" / f"templates_before_vat_free_{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, backup_dir / TEMPLATE)
            print(f"Резервная копия: {backup_dir / TEMPLATE}\n")

    print(f"{TEMPLATE}: {fix_template(path, apply=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
