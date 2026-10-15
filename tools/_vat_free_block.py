#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Движок правки бланка: условный блок «НДС не облагается».

Зачем он нужен
--------------
У трёх типов договоров (аренда ТС и два клиентских типа) в разделе
«Стоимость» три суммы печатались ВСЕГДА:

    без НДС / НДС по ставке / итого.

При ставке «Без НДС» (не плательщик НДС, п. 1 ст. 145 НК) это даёт
«НДС 0%» и «0,00 руб.» вместо честной формулировки «НДС не облагается».
В аренде правка сделана шагом «Форма ООО/ИП» (10.10.2026); здесь та же
правка обобщена, чтобы клиентские бланки правились ОДНИМ кодом.

Что делает движок
-----------------
Оборачивает абзацы раздела «Стоимость» в условный блок docxtpl:

    {%p if is_vat_free %}
    <ветвь if:  ветка 1 — сумма (и подпись «НДС не облагается»)>
    {%p else %}
    <ветвь else: прежние абзацы БАЙТ В БАЙТ>
    {%p endif %}

Ветвь `else` переносится из документа как есть, поэтому договоры с НДС
печатаются ровно так, как печатались: правка не может изменить ни одну
из прежних формулировок.

Ловушка docxtpl
---------------
`{%p ... %}` понимается, только когда тег стоит в абзаце ОДИН: тогда
абзац с тегом вырезается целиком, а сам тег остаётся в XML. Поэтому блок
занимает N абзацев: тег if / текст ветви / тег else / прежние абзацы /
тег endif. Тег, склеенный с текстом, docxtpl проигнорирует — в документ
попадёт сырое «{%p if … %}».

Почему не python-docx
---------------------
`Document.save()` переписывает ВСЕ части пакета: метки времени, порядок и
идентификаторы элементов. Здесь перезаписывается РОВНО одна запись архива
— `word/document.xml`, остальные копируются побайтово (`ZipInfo`
сохраняется как есть), и это проверяется методом `verify_other_parts`.

Оформление новых абзацев
------------------------
`pPr` и `rPr` берутся у абзаца-образца (обычно последнего абзаца ветви
`else`) и ставятся ДО `<w:t>`: новый абзац выглядит как соседние.
`rPr` читается ПОСЛЕ `pPr`: в `<w:pPr>` бывает свой `<w:rPr>` (оформление
знака абзаца), и оттуда в текст ушёл бы чужой кегль — ловушка шага
«Ставки НДС в UI».

Идемпотентность
---------------
Если флаг уже упомянут в XML, движок не пишет НИ БАЙТА: повторный прогон
безопасен. Поэтому порядок для будущих шагов такой: пересборка бланка
сборщиком → прогон инструмента с `--apply` → обновление SHA256 в тестах.
"""

import re
import zipfile
from pathlib import Path

#: Часть пакета, в которой живёт раздел «Стоимость».
DOCUMENT_PART = "word/document.xml"

#: Признак, по которому генератор выбирает ветвь (кладётся в карту замен).
FLAG = "is_vat_free"

#: Теги ветвления.
IF_TAG = "{%p if " + FLAG + " %}"
ELSE_TAG = "{%p else %}"
ENDIF_TAG = "{%p endif %}"

PARAGRAPH_RE = re.compile(r"<w:p\b.*?</w:p>|<w:p\b[^>]*/>", re.S)


def plain(paragraph_xml: str) -> str:
    """Текст абзаца без разметки — по нему ищем нужные абзацы."""
    return re.sub(r"<[^>]+>", "", paragraph_xml)


def paragraph_xml_texts(xml: str) -> list:
    """Тексты абзацев документа (по разметке `<w:p>`), в порядке бланка."""
    return [plain(match.group(0)) for match in PARAGRAPH_RE.finditer(xml)]


def find_paragraphs(xml: str, anchors) -> tuple:
    """
    Индексы абзацев по их опознавательным кускам текста.

    Абзацы обязаны идти ПОДРЯД и в том же порядке, что и `anchors`:
    условный блок оборачивает ровно их, и врозь они смысла не имеют.
    Не нашлись или идут не подряд — `ValueError` (бланк не портится).
    """
    paragraphs = [match.group(0) for match in PARAGRAPH_RE.finditer(xml)]

    found = {}
    for index, paragraph in enumerate(paragraphs):
        text = plain(paragraph)
        for key, anchor in anchors:
            if key not in found and anchor in text:
                found[key] = index

    missing = [key for key, _anchor in anchors if key not in found]
    if missing:
        raise ValueError(f"в бланке не найдены абзацы раздела: {missing}")

    order = tuple(found[key] for key, _anchor in anchors)
    expected = tuple(range(order[0], order[0] + len(order)))
    if order != expected:
        raise ValueError(f"абзацы раздела идут не подряд: {order}")

    return order


def run_properties(paragraph_xml: str) -> str:
    """
    Оформление run'а из образца — БЕЗ `rPr` абзацной метки.

    В `<w:pPr>` бывает свой `<w:rPr>` (оформление знака абзаца) — тогда
    новые абзацы получили бы чужой кегль, поэтому `rPr` берётся ПОСЛЕ `pPr`.
    """
    ppr = re.search(r"<w:pPr>.*?</w:pPr>", paragraph_xml, re.S)
    tail = paragraph_xml[ppr.end():] if ppr else paragraph_xml
    rpr = re.search(r"<w:rPr>.*?</w:rPr>", tail, re.S)
    return rpr.group(0) if rpr else ""


def paragraph_properties(paragraph_xml: str) -> str:
    """`<w:pPr>` образца: отступы, интервалы и выравнивание как у соседей."""
    ppr = re.search(r"<w:pPr>.*?</w:pPr>", paragraph_xml, re.S)
    return ppr.group(0) if ppr else ""


def new_paragraph(paragraph_properties_xml: str, run_properties_xml: str,
                  text: str) -> str:
    """
    Новый абзац бланка с оформлением образца.

    Атрибуты `<w:p>` (w14:paraId и прочие) не нужны: это расширения Word,
    без них абзац — обычный абзац WordprocessingML. Копировать их нельзя:
    одинаковые идентификаторы в одном документе — уже дефект.
    """
    return (
        f"<w:p>{paragraph_properties_xml}"
        f"<w:r>{run_properties_xml}<w:t>{text}</w:t></w:r></w:p>"
    )


def build_conditional_block(paragraphs, order: tuple, branch_texts,
                            tag_texts=()) -> str:
    """
    Собирает условный блок целиком: теги + ветвь `if` + прежние абзацы.

    Прежние абзацы переносятся БАЙТ В БАЙТ (они берутся из документа), а
    новые строятся по образцу последнего из них: он стоит в том же разделе,
    поэтому оформление совпадает.

    `branch_texts` — тексты ветви `if` сверху вниз; `tag_texts` — тексты,
    которые должны стоять ВНУТРИ ветви `if`, но не являются её абзацами
    (например, собственный тег `{%p if … %}` движка не нужен и остаётся
    пустым).
    """
    sample = paragraphs[order[-1]]
    ppr = paragraph_properties(sample)
    rpr = run_properties(sample)

    parts = [new_paragraph(ppr, rpr, IF_TAG)]
    parts.extend(new_paragraph(ppr, rpr, text) for text in tag_texts)
    parts.extend(new_paragraph(ppr, rpr, text) for text in branch_texts)
    parts.append(new_paragraph(ppr, rpr, ELSE_TAG))
    parts.extend(paragraphs[index] for index in order)
    parts.append(new_paragraph(ppr, rpr, ENDIF_TAG))
    return "".join(parts)


def fix_document_xml(xml: str, anchors, branch_texts) -> tuple:
    """
    Оборачивает абзацы раздела в условный блок `{%p if is_vat_free %}`.

    Возвращает `(новый_xml, число_правок)`. Правка ровно одна на документ;
    если флаг уже упомянут — 0 (инструмент идемпотентен).
    """
    if FLAG in xml:
        return xml, 0

    paragraphs = [match.group(0) for match in PARAGRAPH_RE.finditer(xml)]
    order = find_paragraphs(xml, anchors)

    matches = list(PARAGRAPH_RE.finditer(xml))
    start = matches[order[0]].start()
    end = matches[order[-1]].end()

    block = build_conditional_block(paragraphs, order, branch_texts)
    return xml[:start] + block + xml[end:], 1


def rewrite_document_part(path: Path, new_xml: bytes) -> None:
    """
    Перезаписывает `word/document.xml`, не трогая остальные части пакета.

    ZipInfo каждой записи переносится как есть (дата, атрибуты, способ
    сжатия), поэтому шрифты, связи и стили остаются побайтово теми же.
    """
    with zipfile.ZipFile(str(path)) as source:
        items = [(info, source.read(info.filename)) for info in source.infolist()]

    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as target:
        for info, data in items:
            if info.filename == DOCUMENT_PART:
                data = new_xml
            target.writestr(info, data)


def archive_parts(path: Path) -> dict:
    """Записи архива: имя → байты (для сверки «кроме document.xml»)."""
    with zipfile.ZipFile(str(path)) as archive:
        return {info.filename: archive.read(info.filename)
                for info in archive.infolist()}


def verify_other_parts(before: dict, after: dict) -> list:
    """
    Сверяет пакет после правки: все записи, КРОМЕ document.xml, те же.

    Возвращает список расхождений (пустой — всё совпало). Состав пакета и
    имена частей тоже проверяются: пропавший стиль или связь — это уже
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


def document_xml(path: Path) -> str:
    """`word/document.xml` бланка текстом."""
    with zipfile.ZipFile(str(path)) as archive:
        return archive.read(DOCUMENT_PART).decode("utf-8")


def check_state(path: Path, branch_texts) -> str:
    """Состояние бланка: стоит ли условный блок и что в ветви `if`."""
    if not path.exists():
        return "НЕ НАЙДЕН"

    if FLAG not in document_xml(path):
        return "условного блока нет — раздел печатается всегда"

    xml = document_xml(path)
    parts = []
    for tag, title in ((IF_TAG, "if"), (ELSE_TAG, "else"), (ENDIF_TAG, "endif")):
        parts.append(f"{title}: {xml.count(tag)}")
    free = all(text in xml for text in branch_texts)
    return f"блок есть ({', '.join(parts)}), ветвь if на месте: {free}"


def fix_template(path: Path, apply: bool, anchors, branch_texts,
                 description: str = "раздел «Стоимость»") -> str:
    """
    Правит бланк. Возвращает отчёт одной строкой.

    :param apply: False — сухой прогон: бланк только проверяется, файл не
        перезаписывается.
    """
    if not path.exists():
        return "НЕ НАЙДЕН"

    xml = document_xml(path)

    try:
        fixed, changes = fix_document_xml(xml, anchors, branch_texts)
    except ValueError as error:
        return f"ОШИБКА: {error}"

    if not changes:
        return "условный блок уже стоит — пропуск"

    if not apply:
        return f"будет добавлен блок {{%p if {FLAG} %}} вокруг: {description}"

    before = archive_parts(path)
    rewrite_document_part(path, fixed.encode("utf-8"))
    after = archive_parts(path)

    problems = verify_other_parts(before, after)
    if problems:
        raise RuntimeError("; ".join(problems))

    return (
        f"добавлен блок {{%p if {FLAG} %}}: ветвь if — «НДС не облагается», "
        f"ветвь else — прежний текст; остальные записи пакета побайтово те же"
    )
