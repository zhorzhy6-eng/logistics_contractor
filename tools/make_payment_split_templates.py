#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Правит бланки перевозки: условный пункт оплаты (предоплата + расчёт).

Что делает
----------
П. 4.4 трёх бланков перевозки становится условным блоком из ПЯТИ абзацев:

    {%p if has_prepayment %}
    4.4. Оплата услуг осуществляется Заказчиком в следующем порядке:
         — <сумма> руб. (<прописью>) — предоплата в размере <N>% от
           стоимости услуг, на погрузке;
         — <сумма> руб. (<прописью>) — окончательный расчёт в размере
           <M>% от стоимости услуг, в течение K (<прописью>) банковских
           дней с даты завершения выгрузки … (прежний хвост пункта);
    {%p else %}
    4.4. Оплата производится в течение {{payment_days}} … (ПРЕЖНИЙ ТЕКСТ);
    {%p endif %}

Номер пункта — 4.4 в ОБЕИХ ветках: он не зависит от наличия предоплаты,
меняется только текст внутри пункта. Ветка `else` — дословно прежний текст
пункта: при предоплате 0 договор печатается ровно как раньше.

Бланк, правленный ПЕРВОЙ версией скрипта (разбивка как п. 4.2), скрипт не
пересобирает заново — это потеряло бы уже сделанную правку, — а
перенумеровывает: `renumber_clause()` меняет заголовок пункта на
`CLAUSE_HEAD` (см. `OLD_CLAUSE_HEAD`).

Почему тег стоит отдельным абзацем
----------------------------------
docxtpl распознаёт `{%p ... %}` только тогда, когда тег в абзаце ОДИН, и
вырезает такой абзац ЦЕЛИКОМ — вместе с текстом. Поэтому `{%p if %} ТЕКСТ`
теряет текст на обеих ветках, а два тега в одном абзаце ломают разбор
(«unknown tag 'endif'»). Рабочая раскладка (проверена на синтетическом
документе): тег своим абзацем, текст между тегами — обычными абзацами.
Вёрстка не меняется: соседние пункты 4.3 и 4.5 тоже стоят каждый своим
абзацем.

Номер нового пункта — 4.2: прежний 4.4 занят веткой `else`, и двух «4.4»
в одном договоре быть не должно.

Почему часть архива, а не python-docx
-------------------------------------
В бланки перевозки встроены шрифты (`word/fonts/`, по 3 записи), а
`Document.save()` переписывает ВСЕ части пакета. Здесь перезаписывается
РОВНО одна запись архива — `word/document.xml`, остальные копируются
побайтово вместе с ZipInfo (тот же приём, что в
`tools/fix_perevozka_template.py` и `tools/enable_hyphenation.py`).

Почему абзац ищется по тексту
-----------------------------
Номер абзаца в пакетах разный (140, 140, 138), поэтому жёсткий индекс
молча попал бы в чужой абзац. Пункт находится по своему тексту
(«4.4. Оплата производится в течение …»), а оформление новых абзацев
копируется с него: `<w:pPr>` и `<w:rPr>` берутся из исходного абзаца.

Идемпотентность
---------------
Повторный запуск ничего не меняет: если в бланке уже есть
`{%p if has_prepayment %}`, он пропускается.

Запуск:
    python tools/make_payment_split_templates.py --check   # план
    python tools/make_payment_split_templates.py           # применить
"""

import argparse
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Бланки перевозки: выбор идёт по типу перевозчика.
TEMPLATES = (
    "shablon_ooo.docx",
    "shablon_ip_with_vat.docx",
    "shablon_ip_without_vat.docx",
)

#: Часть пакета, в которой живёт п. 4.4.
DOCUMENT_PART = "word/document.xml"

#: Метки условного абзаца docxtpl.
IF_TAG = "{%p if has_prepayment %}"
ELSE_TAG = "{%p else %}"
ENDIF_TAG = "{%p endif %}"

#: Начало пункта: по нему абзац и находится (номера абзацев в бланках разные).
CLAUSE_START = "4.4. "

#: Начало и хвост существующей формулировки срока оплаты. Между ними стоят
#: плейсхолдеры срока (каждый — в своём `<w:t>`), поэтому текст абзаца
#: разбирается ПО КУСКАМ, а не одной строкой.
TERM_HEAD = "Оплата производится в течение "
TERM_TAIL_START = "банковских дней "

#: Что печатается, если предоплаты нет: прежний текст пункта целиком.
#: Собирается из тех же кусков, что стоят в бланке, — формулировка не
#: переписывается.
LEGACY_PARTS = (
    "Оплата производится в течение ",
    "{{payment_days}}",
    " ({{payment_days_words}}) ",
    "банковских дней ",
)

#: Строка предоплаты — дословно формулировка ТЗ шага, включая «на погрузке».
#: В бланках перевозки договор назван договором-заявкой на перевозку, и хотя
#: погрузка в нём есть не всегда, менять формулировку по своей воле нельзя:
#: это текст договора.
PREPAY_LINE = (
    "— {{prepayment_amount}} руб. ({{prepayment_amount_words}}) — "
    "предоплата в размере {{prepayment_percent}}% от стоимости услуг, "
    "на погрузке; "
)

#: Строка окончательного расчёта: срок берётся из прежней формулировки
#: (`{{payment_days}}`, `{{payment_days_words}}` и хвост пункта остаются
#: на месте, поэтому здесь только связка «… услуг, в течение »).
BALANCE_LINE = (
    "— {{balance_amount}} руб. ({{balance_amount_words}}) — "
    "окончательный расчёт в размере {{balance_percent}}% от стоимости "
    "услуг, в течение "
)

#: Заголовок пункта: номер и текст ТЗ. Номер — 4.4, ТОТ ЖЕ, что у прежнего
#: пункта в ветке `else`: номер пункта не должен зависеть от того, есть
#: предоплата или нет (уточнение ТЗ шага «Предоплата: двусторонний ввод +
#: номер пункта»). Меняется только текст внутри пункта.
CLAUSE_HEAD = "4.4. Оплата услуг осуществляется Заказчиком в следующем порядке:"

#: Заголовок пункта в бланках, правленных ПЕРВОЙ версией этого скрипта:
#: разбивка печаталась как п. 4.2. Такой бланк скрипт не пересобирает
#: заново, а перенумеровывает — правка ровно одной строки.
OLD_CLAUSE_HEAD = "4.2. Оплата услуг осуществляется Заказчиком в следующем порядке:"

#: Связка между плейсхолдером срока и прежним хвостом пункта. Она ПУСТАЯ:
#: хвост (`tail`) начинается словами «банковских дней с даты завершения
#: выгрузки …», поэтому любая добавка дала бы «банковских дней банковских
#: дней». Константа оставлена, чтобы место связки было видно в сборке.
TERM_MIDDLE = ""

#: Один абзац верхнего уровня.
PARAGRAPH_RE = re.compile(r"<w:p(?:\s[^>]*)?>.*?</w:p>", re.DOTALL)

#: Открывающий тег абзаца и его свойства (`<w:pPr>…</w:pPr>`).
PARAGRAPH_OPEN_RE = re.compile(r"<w:p(?:\s[^>]*)?>")
PARAGRAPH_PROPS_RE = re.compile(r"<w:pPr>.*?</w:pPr>", re.DOTALL)

#: Первый run абзаца целиком: из него берётся шрифт для новых абзацев.
FIRST_RUN_RE = re.compile(r"<w:r(?:\s[^>]*)?>.*?</w:r>", re.DOTALL)

#: Свойства шрифта внутри run (`<w:rPr>…</w:rPr>`).
RUN_PROPS_RE = re.compile(r"<w:rPr>.*?</w:rPr>", re.DOTALL)

#: Текстовые узлы абзаца. Пустой узел Word пишет самозакрывающимся
#: (`<w:t/>`), поэтому альтернатива обязательна: без неё совпадение
#: «съедает» разметку вместе с закрывающими тегами и ломает абзац.
TEXT_RE = re.compile(
    r"<w:t(?P<attrs>(?:\s[^>]*?)?)(?:/>|>(?P<text>.*?)</w:t>)",
    re.DOTALL,
)


def _node_text(match: "re.Match[str]") -> str:
    """Текст узла `<w:t>`: у самозакрывающегося узла текста нет."""
    return match.group("text") or ""


def split_paragraphs(xml: str):
    """Все абзацы верхнего уровня в порядке следования."""
    return PARAGRAPH_RE.findall(xml)


def split_with_gaps(xml: str):
    """
    XML документа кусками: (зазоры, абзацы).

    gaps[0] — текст до первого абзаца, gaps[i + 1] — текст после абзаца i;
    сборка обратно: "".join(gaps[0], p[0], gaps[1], p[1], …, gaps[-1]).
    Нужна там, где абзац заменяется НА НЕСКОЛЬКО абзацев: `str.replace`
    по образцу мог бы попасть не в то место, если такой абзац не один.
    """
    gaps = []
    paragraphs = []
    position = 0

    for match in PARAGRAPH_RE.finditer(xml):
        gaps.append(xml[position:match.start()])
        paragraphs.append(match.group(0))
        position = match.end()
    gaps.append(xml[position:])

    return gaps, paragraphs


def paragraph_text(paragraph: str) -> str:
    """Весь текст абзаца (склейка `<w:t>`), как его видит пользователь."""
    return "".join(_node_text(match) for match in TEXT_RE.finditer(paragraph))


def find_clause_index(paragraphs) -> int:
    """
    Номер абзаца с п. 4.4. -1, если пункт не найден.

    Ищем по тексту, а не по номеру: в трёх бланках перевозки абзац стоит
    на разных позициях (140, 140 и 138), и жёсткий индекс молча попал бы
    в чужой абзац.
    """
    for index, paragraph in enumerate(paragraphs):
        text = paragraph_text(paragraph)
        if text.startswith(CLAUSE_START) and TERM_HEAD in text:
            return index
    return -1


def find_parts(paragraph: str) -> tuple:
    """
    Абзац разбирается на «номер», «префикс срока» и «хвост срока».

    Возвращает (тексты_узлов, индекс_узла_префикса, индекс_узла_хвоста,
    номер_пункта, хвост) или (None, -1, -1, "", "") для чужого абзаца.

    Префикс — «Оплата производится в течение » в узле, который начинается
    с номера пункта («4.4. Оплата производится в течение »); хвост —
    «банковских дней с даты завершения выгрузки …». Между ними стоят
    `{{payment_days}}` и «({{payment_days_words}})»: каждый — в своём узле
    `<w:t>`, и оба остаются в документе нетронутыми, чтобы docxtpl их
    подставил.

    Ищем по ТЕКСТУ абзаца (`paragraph_text`), а не по точному совпадению
    отдельного узла: при сохранении Word текст может оказаться разбит
    по runs иначе, чем в текущем бланке.
    """
    texts = [_node_text(match) for match in TEXT_RE.finditer(paragraph)]
    text = "".join(texts)

    if not text.startswith(CLAUSE_START):
        return None, -1, -1, "", ""

    prefix_at = text.find(TERM_HEAD)
    if prefix_at < 0:
        return None, -1, -1, "", ""
    prefix_index = _node_index_at(texts, prefix_at)
    if prefix_index < 0:
        return None, -1, -1, "", ""

    # Хвост — ВСЁ, что идёт после плейсхолдеров срока: «банковских дней
    # с даты завершения выгрузки …». В бланке он разбит на несколько узлов,
    # поэтому tail_index — первый из них, а не единственный.
    tail_at = text.find(TERM_TAIL_START, prefix_at)
    if tail_at < 0:
        return None, -1, -1, "", ""
    tail_index = _node_index_at(texts, tail_at)
    if tail_index <= prefix_index:
        return None, -1, -1, "", ""

    return texts, prefix_index, tail_index, "", text[tail_at:]


def _node_index_at(texts: list, position: int) -> int:
    """Номер узла `<w:t>`, в который попадает позиция склеенного текста."""
    offset = 0
    for index, text in enumerate(texts):
        if offset <= position < offset + len(text):
            return index
        offset += len(text)
    return -1


def _paragraph_props(paragraph: str) -> str:
    """Открывающий тег абзаца и его `<w:pPr>` — оформление абзаца целиком."""
    open_tag = PARAGRAPH_OPEN_RE.match(paragraph).group(0)
    props = PARAGRAPH_PROPS_RE.search(paragraph)
    return open_tag + (props.group(0) if props else "")


def _text_run_props(paragraph: str) -> str:
    """`<w:rPr>` первого run абзаца — образец шрифта для новых абзацев."""
    run = FIRST_RUN_RE.search(paragraph)
    if run is None:
        return ""
    props = RUN_PROPS_RE.search(run.group(0))
    return props.group(0) if props else ""


def build_clause_paragraphs(paragraph: str) -> tuple:
    """
    Абзац п. 4.4 превращается в блок из четырёх абзацев.

    Возвращает (список_абзацев, текст_нового_пункта) или (None, "") для
    чужого абзаца. Абзацы:

      1. `{%p if has_prepayment %}` — тег, и только он;
      2. новый пункт 4.2 с разбивкой оплаты;
      3. `{%p else %}` — тег, и только он;
      4. ПРЕЖНИЙ пункт 4.4 без единого изменения;
      5. `{%p endif %}` — тег, и только он.

    Почему тег стоит ОТДЕЛЬНЫМ абзацем, а не вместе с текстом: docxtpl
    вырезает абзац с тегом `{%p ... %}` ЦЕЛИКОМ, вместе с текстом. Поэтому
    `{%p if %} НОВЫЙ ТЕКСТ` теряет текст на обоих ветках, а `{%p endif %}`
    в одном абзаце с `{%p if %}` ломает разбор («unknown tag 'endif'»).
    Проверено на синтетическом документе: работающая раскладка — тег своим
    абзацем, текст между тегами обычными абзацами. Вёрстка при этом та же,
    что и была: пункты 4.3 и 4.5 тоже стоят каждый своим абзацем.

    Оформление всех абзацев копируется с исходного: `<w:pPr>` и `<w:rPr>`
    берутся из него, новых свойств не выдумывается.
    """
    texts, prefix_index, tail_index, _number, tail = find_parts(paragraph)
    if texts is None:
        return None, ""

    # Прежний пункт целиком: номер («4.4. »), формулировка срока и её
    # продолжение. Куски берутся из самого бланка — формулировка не
    # переписывается.
    legacy = (
        texts[prefix_index - 1]
        + texts[prefix_index]
        + "".join(texts[prefix_index + 1:tail_index])
        + tail
    )

    # Строка расчёта продолжается прежним хвостом пункта: «банковских дней
    # с даты завершения выгрузки …». Слово «банковских» и его хвост берутся
    # из связки и `tail` — формулировка не переписывается.
    between = "".join(texts[prefix_index + 1:tail_index])
    new_clause = (
        f"{CLAUSE_HEAD} "
        f"{PREPAY_LINE}"
        f"{BALANCE_LINE}"
        f"{between}"
        f"{TERM_MIDDLE}"
        f"{tail}"
    )

    props = _paragraph_props(paragraph)
    run_props = _text_run_props(paragraph)

    def make_paragraph(text: str) -> str:
        """Абзац с одним run: текст и оформление исходного абзаца."""
        if not text:
            return f"{props}</w:p>"
        return (
            f"{props}<w:r>{run_props}"
            f'<w:t xml:space="preserve">{escape(text)}</w:t>'
            "</w:r></w:p>"
        )

    return (
        [
            make_paragraph(IF_TAG),
            make_paragraph(new_clause),
            make_paragraph(ELSE_TAG),
            make_paragraph(legacy),
            make_paragraph(ENDIF_TAG),
        ],
        new_clause,
    )


def rewrite_clause(paragraph: str) -> tuple:
    """
    Переписывает абзац п. 4.4: разбивка оплаты вместо одного срока.

    Возвращает (новый_фрагмент_документа, число_абзацев). 0 — абзац не
    похож на пункт об оплате (тогда его не трогаем: лучше без правки, чем
    чужая). Новый фрагмент — ТРИ абзаца вместо одного: см.
    build_clause_paragraphs.
    """
    paragraphs, _new_clause = build_clause_paragraphs(paragraph)
    if paragraphs is None:
        return paragraph, 0
    return "\n".join(paragraphs), len(paragraphs)


def renumber_clause(xml: str) -> tuple:
    """
    Перенумеровывает пункт с разбивкой в уже правленом бланке.

    Бланки, правленные первой версией скрипта, печатали разбивку как
    п. 4.2, а прежний текст — как 4.4. Номер пункта не должен зависеть от
    наличия предоплаты, поэтому такой бланк не пересобирается заново
    (это потеряло бы уже сделанную правку), а перенумеровывается: строка
    `OLD_CLAUSE_HEAD` заменяется на `CLAUSE_HEAD`.

    Возвращает (новый_xml, число_замен): 0 — либо бланк уже с нужным
    номером, либо он ещё не правлен (тогда работает обычная сборка).
    """
    if IF_TAG not in xml or OLD_CLAUSE_HEAD not in xml:
        return xml, 0

    count = xml.count(OLD_CLAUSE_HEAD)
    return xml.replace(OLD_CLAUSE_HEAD, CLAUSE_HEAD), count


def check_template(path: Path) -> str:
    """
    Состояние бланка: правлен он уже или нет и какой в нём номер пункта.

    Признак правки — тег `{%p if has_prepayment %}` в документе, а не
    «пункт 4.4 начинается с тега»: после правки пункт 4.4 уходит в ветку
    `else` и начинается как обычно, а тег стоит в СВОЁМ абзаце перед ним.
    Ищем тег в тексте всего документа — так проверка остаётся верной и
    для ещё не правленного бланка, и для правленного.
    """
    if not path.exists():
        return "НЕ НАЙДЕН"

    with zipfile.ZipFile(str(path)) as archive:
        xml = archive.read(DOCUMENT_PART).decode("utf-8")

    if IF_TAG in xml:
        if OLD_CLAUSE_HEAD in xml:
            return "правлен, но пункт 4.2 — нужна перенумерация"
        return "уже правлен (пункт 4.4) — пропуск"

    paragraphs = split_paragraphs(xml)
    index = find_clause_index(paragraphs)
    if index < 0:
        return "ОШИБКА: пункт 4.4 не найден"
    return f"не правлен (абзац {index})"


def edit_template(path: Path, apply: bool) -> str:
    """Правит один бланк. Возвращает отчёт одной строкой."""
    if not path.exists():
        return "НЕ НАЙДЕН"

    with zipfile.ZipFile(str(path)) as archive:
        infos = [(info, archive.read(info.filename)) for info in archive.infolist()]

    parts = {info.filename: data for info, data in infos}
    if DOCUMENT_PART not in parts:
        return f"ОШИБКА: в пакете нет {DOCUMENT_PART}"

    xml = parts[DOCUMENT_PART].decode("utf-8")

    # Бланк уже правлен: остаётся только перенумеровать пункт (если номер
    # в нём от первой версии скрипта).
    if IF_TAG in xml:
        renumbered, changes = renumber_clause(xml)
        if not changes:
            return "уже правлен (пункт 4.4) — пропуск"
        if not apply:
            return (
                f"перенумерация: {OLD_CLAUSE_HEAD[:4]} → {CLAUSE_HEAD[:4]} "
                f"(замен: {changes})"
            )
        parts[DOCUMENT_PART] = renumbered.encode("utf-8")
        with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as target:
            for info, data in infos:
                target.writestr(info, parts[info.filename])
        return (
            f"перенумерован: {OLD_CLAUSE_HEAD[:4]} → {CLAUSE_HEAD[:4]} "
            f"(замен: {changes})"
        )

    paragraphs = split_paragraphs(xml)
    index = find_clause_index(paragraphs)
    if index < 0:
        return "ОШИБКА: пункт 4.4 не найден"

    rebuilt, count = rewrite_clause(paragraphs[index])
    if not count:
        return (
            "ОШИБКА: абзац 4.4 не разобран "
            f"(узлов <w:t>: {len(list(TEXT_RE.finditer(paragraphs[index])))})"
        )

    # Абзац заменяется тремя (`if` / `else` / `endif`). Собираем XML по
    # кускам, а не `str.replace`: подстановка по образцу могла бы попасть
    # в чужое место, если такой абзац в документе не один.
    chunks, document_paragraphs = split_with_gaps(xml)
    pieces = [chunks[0]]
    for position, value in enumerate(document_paragraphs):
        pieces.append(rebuilt if position == index else value)
        pieces.append(chunks[position + 1])
    new_xml = "".join(pieces)

    if new_xml == xml:
        return "ОШИБКА: текст не изменился"

    if not apply:
        return (
            f"абзац {index}: пункт 4.4 станет условным "
            f"(абзацев вместо одного: {count})"
        )

    parts[DOCUMENT_PART] = new_xml.encode("utf-8")
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as target:
        for info, data in infos:
            target.writestr(info, parts[info.filename])

    return (
        f"абзац {index}: пункт 4.4 стал условным "
        f"(абзацев вместо одного: {count})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="только показать план, ничего не менять")
    parser.add_argument("--templates", default=None,
                        help="папка с шаблонами (по умолчанию templates/)")
    parser.add_argument("--no-backup", action="store_true",
                        help="не делать резервную копию шаблонов")
    args = parser.parse_args()

    templates_dir = (
        Path(args.templates) if args.templates else PROJECT_ROOT / "templates"
    )
    apply = not args.check

    if apply and not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = (
            PROJECT_ROOT / "backup" / f"templates_before_payment_split_{stamp}"
        )
        backup_dir.mkdir(parents=True, exist_ok=True)
        for name in TEMPLATES:
            source = templates_dir / name
            if source.exists():
                shutil.copy2(source, backup_dir / name)
        print(f"Резервная копия: {backup_dir}\n")

    for name in TEMPLATES:
        path = templates_dir / name
        print(f"{name}:")
        if args.check:
            print(f"  состояние: {check_template(path)}")
        else:
            print(f"  {edit_template(path, apply=True)}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
