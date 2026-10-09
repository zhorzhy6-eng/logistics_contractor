#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Правит бланки перевозки: динамические стороны (ИП/ООО), подписи, пустые поля.

Что чиним
---------
Четыре беды бланков, найденные на договоре с заказчиком-ИП:

  1. П. 1.1 «Заказчик» — ЗАХАРДКОЖЕННЫЙ текст: «…в лице Генерального
     директора Ахмедова Тимура Артуровича, действующего на основании
     Устава». У заказчика-ИП печатался чужой директор. Теперь ветвь
     выбирается по `is_client_ip`: у ИП — «Индивидуальный предприниматель
     <ФИО>, именуемая/именуемый … действующая/действующий на основании
     свидетельства о государственной регистрации», у ООО — прежняя
     формулировка, но с ДАННЫМИ заказчика, а не с константой.
  2. П. 9 «Реквизиты Заказчика» — ЗАХАРДКОЖЕННАЯ подпись «Генеральный
     директор /Т.А. Ахмедов /». Теперь — {{client_director_position_short}}
     и ФИО из данных; у ИП это «Индивидуальный предприниматель / ФИО /».
  3. П. 1.2 «Перевозчик» — «ОГРНИП» захардкожен (у ООО должен быть «ОГРН»),
     «действующего» без рода. Теперь условный блок по `is_carrier_ip`
     и метка {{carrier_ogrn_label}}; в ООО-бланке — только метка.
  4. Мелочи: «({{*_name}})» в скобках печаталось даже когда сокращённое
     наименование совпадает с полным; пустые необязательные поля
     печатались «дыркой» («Цвет:», «Год выпуска:», «КПП ,», «Место
     рождения:», «Категории:», «E-mail:», «Фактический адрес:»).

Как правим
----------
Бланк — это архив; правится РОВНО одна запись (`word/document.xml`),
остальные копируются побайтово вместе с ZipInfo. В бланках перевозки
встроены шрифты (`word/fonts/`, 3 записи), а `python-docx` переписывает
ВЕСЬ пакет — поэтому `Document.save()` здесь не используется (тот же приём,
что в `tools/fix_perevozka_template.py` и
`tools/make_payment_split_templates.py`).

Абзац находится по СВОЕМУ ТЕКСТУ, а не по номеру: в трёх бланках один и тот
же пункт стоит на разных позициях (236, 234 и 231 абзацев), и жёсткий индекс
молча попал бы в чужой абзац. Оформление новых абзацев копируется с
исходного: `<w:pPr>` и `<w:rPr>` берутся из него, новых свойств не
выдумывается.

Почему тег `{%p … %}` стоит отдельным абзацем
---------------------------------------------
docxtpl распознаёт `{%p … %}` только тогда, когда тег в абзаце ОДИН, и
вырезает такой абзац ЦЕЛИКОМ — вместе с текстом. Рабочая раскладка (та же,
что у блока предоплаты, п. 4.4): тег своим абзацем, текст ветки — обычными
абзацами между тегами. В ячейке таблицы это безопасно: у всех правленых
ячеек кроме целевой строки есть и другие абзацы, поэтому ячейка не остаётся
без единого `<w:p>`.

Идемпотентность
---------------
Повторный запуск ничего не меняет: если абзаца в прежнем виде в бланке уже
нет, а признак правки (например, `{%p if is_client_ip %}`) есть — правка
пропускается. Если нет ни того, ни другого — это ОШИБКА, и бланк не
переписывается: лучше без правки, чем чужая.

Запуск:
    python tools/fix_perevozka_dynamic_parties.py --check   # только план
    python tools/fix_perevozka_dynamic_parties.py           # применить
    python tools/fix_perevozka_dynamic_parties.py --templates <папка>
"""

import argparse
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
from xml.sax.saxutils import escape

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Бланки перевозки: выбор идёт по типу перевозчика.
TEMPLATES = (
    "shablon_ooo.docx",
    "shablon_ip_with_vat.docx",
    "shablon_ip_without_vat.docx",
)

#: Часть пакета, в которой живут пункты 1.1, 1.2, 3.1 и 9.
DOCUMENT_PART = "word/document.xml"

#: Теги условных абзацев docxtpl.
ELSE_TAG = "{%p else %}"
ENDIF_TAG = "{%p endif %}"

#: Неразрывный дефис (U+2011): им в бланке набран «E‑mail» заказчика.
NB_HYPHEN = "\u2011"

#: Неразрывный пробел: в бланке встречается в подписях.
NB_SPACE = "\u00a0"

# ── Разбор XML абзацами (тот же приём, что в make_payment_split_templates.py) ──

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


def paragraph_text(paragraph: str) -> str:
    """Весь текст абзаца (склейка `<w:t>`), как его видит пользователь."""
    return "".join(_node_text(match) for match in TEXT_RE.finditer(paragraph))


def normalize(text: str) -> str:
    """
    Текст абзаца для СРАВНЕНИЯ: неразрывные пробел и дефис — обычными,
    повторные пробелы — одним.

    Нужно потому, что один и тот же пункт в трёх бланках набран чуть
    по-разному («E-mail» и «E‑mail», двойной пробел перед подписью), а
    искать абзац приходится по всему тексту целиком.
    """
    text = text.replace(NB_SPACE, " ").replace(NB_HYPHEN, "-")
    return re.sub(r"\s+", " ", text).strip()


def split_with_gaps(xml: str) -> Tuple[List[str], List[str]]:
    """
    XML документа кусками: (зазоры, абзацы).

    gaps[0] — текст до первого абзаца, gaps[i + 1] — текст после абзаца i.
    Сборка обратно — точное равенство исходнику: `"".join(gaps[0], p[0],
    gaps[1], …, gaps[-1])`. Нужна там, где один абзац заменяется НЕСКОЛЬКИМИ:
    `str.replace` по образцу мог бы попасть не в то место, если такой абзац
    в документе не один, а по номерам абзацев ориентироваться нельзя (они
    в бланках разные).
    """
    gaps: List[str] = []
    paragraphs: List[str] = []
    position = 0

    for match in PARAGRAPH_RE.finditer(xml):
        gaps.append(xml[position:match.start()])
        paragraphs.append(match.group(0))
        position = match.end()
    gaps.append(xml[position:])

    return gaps, paragraphs


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


def make_paragraph(paragraph: str, text: str) -> str:
    """Новый абзац с оформлением исходного: один run, один узел текста."""
    props = _paragraph_props(paragraph)
    run_props = _text_run_props(paragraph)
    return (
        f"{props}<w:r>{run_props}"
        f'<w:t xml:space="preserve">{escape(text)}</w:t>'
        "</w:r></w:p>"
    )


# ─────────────────────────────────────────────────────────────
# Правки
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Edit:
    """Одна правка: абзац бланка → один или несколько новых абзацев."""

    #: Точный текст абзаца в бланке (сравнивается в нормализованном виде).
    old: str

    #: Тексты новых абзацев: теги `{%p … %}` стоят СВОИМИ абзацами.
    new: Tuple[str, ...]

    #: Признак «уже правлено» — ищется в XML всего документа.
    marker: str

    def __str__(self) -> str:
        return self.marker


def _tag_if(flag: str) -> str:
    """Абзац-тег `{%p if <флаг> %}`."""
    return f"{{%p if {flag} %}}"


# ── П. 1.1 «Заказчик» ──

def edit_client_clause() -> Edit:
    """
    П. 1.1 из константы становится условным: ИП или ООО.

    Ветка `else` — прежняя формулировка, но с ДАННЫМИ заказчика:
    «в лице {{client_director_position_short}} {{client_director}}».
    """
    return Edit(
        old=(
            "{{client_full_name}} ({{client_name}}), именуемое в дальнейшем "
            "«Заказчик», в лице Генерального директора Ахмедова Тимура "
            "Артуровича, действующего на основании Устава."
        ),
        new=(
            _tag_if("is_client_ip"),
            "{{client_legal_form}} {{client_full_name}}, {{client_pronoun}} "
            "в дальнейшем «Заказчик», {{client_acting}} на основании "
            "свидетельства о государственной регистрации.",
            ELSE_TAG,
            "{{client_full_name}}{{client_name_in_parens}}, именуемое в "
            "дальнейшем «Заказчик», в лице {{client_director_position_short}} "
            "{{client_director}}, действующего на основании Устава.",
            ENDIF_TAG,
        ),
        marker="{%p if is_client_ip %}",
    )


# ── П. 1.2 «Перевозчик» ──

def edit_carrier_clause_ip() -> Edit:
    """
    П. 1.2 бланков ИП: «ОГРНИП» и «действующего» перестают быть константами.

    Ветка `if` — индивидуальный предприниматель, ветка `else` — ООО (в бланк
    ИП такой перевозчик попадает только при явном выборе типа): у него
    печатается КПП и «в лице директора». КПП в ветке ООО обёрнут инлайновым
    `{% if %}`: пустое поле давало в договоре «КПП ,» посреди фразы.
    """
    return Edit(
        old=(
            "{{carrier_legal_form}} {{carrier_full_name}}, ИНН "
            "{{carrier_inn}}, ОГРНИП {{carrier_ogrn}}, в лице "
            "{{carrier_director_position}} {{carrier_director}}, действующего "
            "на основании {{carrier_basis}}, {{carrier_pronoun}} в дальнейшем "
            "«Перевозчик»."
        ),
        new=(
            _tag_if("is_carrier_ip"),
            "{{carrier_legal_form}} {{carrier_full_name}}, ИНН "
            "{{carrier_inn}}, {{carrier_ogrn_label}} {{carrier_ogrn}}, "
            "{{carrier_acting}} на основании {{carrier_basis}}, "
            "{{carrier_pronoun}} в дальнейшем «Перевозчик».",
            ELSE_TAG,
            "{{carrier_legal_form}} {{carrier_full_name}}, ИНН "
            "{{carrier_inn}}, {% if has_carrier_kpp %}КПП {{carrier_kpp}}, "
            "{% endif %}{{carrier_ogrn_label}} {{carrier_ogrn}}, в лице "
            "{{carrier_director_position}} {{carrier_director}}, действующего "
            "на основании {{carrier_basis}}, именуемое в дальнейшем "
            "«Перевозчик».",
            ENDIF_TAG,
        ),
        marker="{%p if is_carrier_ip %}",
    )


def edit_carrier_clause_ooo() -> Edit:
    """
    П. 1.2 ООО-бланка: ветка остаётся ООО-вариантом, меняются две мелочи —
    метка ОГРН берётся из данных, скобки с сокращённым наименованием
    печатаются только при отличии, а КПП исчезает вместе с запятой, если
    его нет.
    """
    return Edit(
        old=(
            "{{carrier_full_name}} ({{carrier_name}}), ИНН {{carrier_inn}}, "
            "КПП {{carrier_kpp}}, ОГРН {{carrier_ogrn}}, в лице директора "
            "{{carrier_director}}, действующего на основании Устава, "
            "именуемое в дальнейшем «Перевозчик»."
        ),
        new=(
            "{{carrier_full_name}}{{carrier_name_in_parens}}, ИНН "
            "{{carrier_inn}}, {% if has_carrier_kpp %}КПП {{carrier_kpp}}, "
            "{% endif %}{{carrier_ogrn_label}} {{carrier_ogrn}}, в лице "
            "директора {{carrier_director}}, действующего на основании "
            "Устава, именуемое в дальнейшем «Перевозчик».",
        ),
        marker="{{carrier_name_in_parens}}",
    )


# ── П. 9: подписи сторон ──

def edit_client_signature() -> Edit:
    """
    Подпись заказчика в п. 9: у ИП — «Индивидуальный предприниматель / ФИО /».

    Должность берётся из данных ({{client_director_position_short}}), а ФИО —
    полное у ИП и сокращённое у ООО ({{client_director_short}} — «Т.А. Ахмедов»).
    """
    return Edit(
        old="Генеральный директор  ________ /Т.А. Ахмедов /",
        new=(
            _tag_if("is_client_ip"),
            "{{client_director_position_short}}  ________ /{{client_director}} /",
            ELSE_TAG,
            "{{client_director_position_short}}  ________ /{{client_director_short}} /",
            ENDIF_TAG,
        ),
        marker="________ /{{client_director}} /",
    )


def edit_carrier_signature_ooo() -> Edit:
    """
    Подпись перевозчика в п. 9 ООО-бланка: «Директор» — из данных.

    Для ООО это по-прежнему «Директор», для ИП (если такой перевозчик попал
    в этот бланк) — «Индивидуальный предприниматель». В бланках ИП эта
    строка уже печатает {{carrier_director_position_short}}.
    """
    return Edit(
        old="Директор ________ /{{carrier_director}}/",
        new=("{{carrier_director_position_short}} ________ /{{carrier_director}}/",),
        marker="{{carrier_director_position_short}} ________",
    )


# ── П. 9: метки ОГРН/ОГРНИП ──

def edit_client_ogrn_label() -> Edit:
    """«ОГРН {{client_ogrn}}» → метка из данных: у ИП-заказчика это ОГРНИП."""
    return Edit(
        old="ОГРН {{client_ogrn}}",
        new=("{{client_ogrn_label}} {{client_ogrn}}",),
        marker="{{client_ogrn_label}}",
    )


def edit_carrier_ogrn_label_ooo() -> Edit:
    """То же для перевозчика в п. 9 ООО-бланка (в бланках ИП уже стоит)."""
    return Edit(
        old="ОГРН {{carrier_ogrn}}",
        new=("{{carrier_ogrn_label}} {{carrier_ogrn}}",),
        marker="{{carrier_ogrn_label}}",
    )


# ── Пустые необязательные поля ──

def optional_line_edit(text: str, flag: str) -> Edit:
    """
    Строка необязательного поля становится условной.

    Пустое значение печатало «дырку»: «Цвет:», «Год выпуска:», «Место
    рождения:», «Категории:», «Телефон:», «E-mail:», «Фактический адрес:».
    Теперь строки просто нет — вместе с подписью поля.
    """
    return Edit(
        old=text,
        new=(_tag_if(flag), text, ENDIF_TAG),
        marker=_tag_if(flag),
    )


#: Строки, которые есть во ВСЕХ трёх бланках.
COMMON_OPTIONAL_LINES: Tuple[Tuple[str, str], ...] = (
    ("Место рождения: {{driver_birth_place}}", "has_driver_birth_place"),
    ("Срок действия: до {{driver_license_expiry}}", "has_driver_license_expiry"),
    ("Категории: {{driver_license_categories}}", "has_driver_license_categories"),
    ("Телефон: {{driver_phone}}", "has_driver_phone"),
    ("Цвет: {{tractor_color}}", "has_tractor_color"),
    ("Год выпуска: {{tractor_year}}", "has_tractor_year"),
    ("Цвет: {{trailer_color}}", "has_trailer_color"),
    ("Год выпуска: {{trailer_year}}", "has_trailer_year"),
    (f"E{NB_HYPHEN}mail: {{{{client_email}}}}", "has_client_email"),
    ("E-mail: {{carrier_email}}", "has_carrier_email"),
)

#: Строки только бланков ИП: «Фактический адрес» печатается у перевозчика.
IP_OPTIONAL_LINES: Tuple[Tuple[str, str], ...] = (
    ("Фактический адрес: {{carrier_actual_address}}", "has_carrier_actual_address"),
)

#: Строка только ООО-бланка: КПП перевозчика в реквизитах.
OOO_OPTIONAL_LINES: Tuple[Tuple[str, str], ...] = (
    ("КПП {{carrier_kpp}}", "has_carrier_kpp"),
)


def edits_for(template_name: str) -> Tuple[Edit, ...]:
    """Набор правок для конкретного бланка."""
    edits: List[Edit] = [
        edit_client_clause(),
        edit_client_signature(),
        edit_client_ogrn_label(),
    ]

    if template_name == "shablon_ooo.docx":
        edits += [
            edit_carrier_clause_ooo(),
            edit_carrier_signature_ooo(),
            edit_carrier_ogrn_label_ooo(),
        ]
        optional_lines = COMMON_OPTIONAL_LINES + OOO_OPTIONAL_LINES
    else:
        edits += [edit_carrier_clause_ip()]
        optional_lines = COMMON_OPTIONAL_LINES + IP_OPTIONAL_LINES

    edits += [optional_line_edit(text, flag) for text, flag in optional_lines]
    return tuple(edits)


# ─────────────────────────────────────────────────────────────
# Применение правок
# ─────────────────────────────────────────────────────────────

def apply_edits(xml: str, edits: Sequence[Edit]) -> Tuple[str, List[str]]:
    """
    Применяет правки к XML документа.

    Возвращает (новый_xml, отчёт). Отчёт — по строке на правку: «правка»,
    «уже правлено» или «ОШИБКА: абзац не найден». Если нашлась хоть одна
    ОШИБКА, XML возвращается БЕЗ изменений: лучше без правки, чем чужая.

    Идемпотентность проверяется по САМОМУ документу, а не по счётчику
    запусков: строка необязательного поля остаётся в бланке и после правки
    (её лишь окружают теги), поэтому «уже правлено» — это соседний абзац-тег
    из этой же правки. Правки, которые абзац ЗАМЕНЯЮТ, узнаются проще:
    прежнего текста в бланке больше нет.
    """
    gaps, paragraphs = split_with_gaps(xml)

    # Абзац → правка. Сравнение по нормализованному тексту: один и тот же
    # пункт в трёх бланках набран чуть по-разному.
    by_text: Dict[str, Edit] = {normalize(edit.old): edit for edit in edits}
    texts = [normalize(paragraph_text(paragraph)) for paragraph in paragraphs]

    report: List[str] = []
    replaced: Dict[int, str] = {}
    handled: set = set()
    applied = 0

    for index, paragraph in enumerate(paragraphs):
        edit = by_text.get(texts[index])
        if edit is None:
            continue

        if _already_applied(paragraphs, texts, index, edit):
            handled.add(id(edit))
            report.append(f"уже правлено: {edit.marker}")
            continue

        replaced[index] = "\n".join(
            make_paragraph(paragraph, new_text) for new_text in edit.new
        )
        handled.add(id(edit))
        applied += 1
        report.append(f"правка: {edit.marker} (абзац {index})")

    # Правки, абзаца которых в документе не нашлось вовсе: либо бланк уже
    # правлен (прежний текст заменён новым), либо раскладка другая — второе
    # опаснее, поэтому это ОШИБКА.
    text_set = set(texts)
    for edit in edits:
        if id(edit) in handled:
            continue
        if any(normalize(new_text) in text_set for new_text in edit.new):
            report.append(f"уже правлено: {edit.marker}")
        else:
            report.append(f"ОШИБКА: абзац не найден — {edit.old[:60]!r}")

    if any(line.startswith("ОШИБКА") for line in report):
        return xml, report

    if not applied:
        return xml, report or ["нечего менять"]

    pieces = [gaps[0]]
    for index, paragraph in enumerate(paragraphs):
        pieces.append(replaced.get(index, paragraph))
        pieces.append(gaps[index + 1])
    new_xml = "".join(pieces)
    return new_xml, report


def _already_applied(
    paragraphs: Sequence[str],
    texts: Sequence[str],
    index: int,
    edit: Edit,
) -> bool:
    """
    Правка уже сделана? Признак — соседний абзац-тег из этой же правки.

    У правок-обёрток (необязательные поля) сам абзац остаётся в бланке — его
    лишь окружают `{%p if … %}` и `{%p endif %}`. Без этой проверки второй
    запуск обернул бы строку ВТОРОЙ парой тегов.
    """
    tags = {normalize(text) for text in edit.new}
    neighbours = []
    if index > 0:
        neighbours.append(texts[index - 1])
    if index + 1 < len(texts):
        neighbours.append(texts[index + 1])
    return any(neighbour in tags for neighbour in neighbours)


def check_template(path: Path) -> List[str]:
    """Состояние бланка: какие правки уже сделаны, а какие ждут."""
    if not path.exists():
        return ["НЕ НАЙДЕН"]

    with zipfile.ZipFile(str(path)) as archive:
        xml = archive.read(DOCUMENT_PART).decode("utf-8")

    _, report = apply_edits(xml, edits_for(path.name))
    return report


def edit_template(path: Path, apply: bool) -> List[str]:
    """Правит один бланк. Возвращает отчёт списком строк."""
    if not path.exists():
        return ["НЕ НАЙДЕН"]

    with zipfile.ZipFile(str(path)) as archive:
        infos = [
            (info, archive.read(info.filename)) for info in archive.infolist()
        ]

    parts = {info.filename: data for info, data in infos}
    if DOCUMENT_PART not in parts:
        return [f"ОШИБКА: в пакете нет {DOCUMENT_PART}"]

    xml = parts[DOCUMENT_PART].decode("utf-8")
    new_xml, report = apply_edits(xml, edits_for(path.name))

    if any(line.startswith("ОШИБКА") for line in report):
        return report

    if new_xml == xml:
        return report

    if not apply:
        return report

    parts[DOCUMENT_PART] = new_xml.encode("utf-8")
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as target:
        for info, data in infos:
            target.writestr(info, parts[info.filename])

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="только показать состояние бланков")
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
            PROJECT_ROOT / "backup" / f"templates_before_dynamic_parties_{stamp}"
        )
        backup_dir.mkdir(parents=True, exist_ok=True)
        for name in TEMPLATES:
            source = templates_dir / name
            if source.exists():
                shutil.copy2(source, backup_dir / name)
        print(f"Резервная копия: {backup_dir}\n")

    for name in TEMPLATES:
        print(f"{name}:")
        path = templates_dir / name
        lines = check_template(path) if args.check else edit_template(path, apply=True)
        for line in lines:
            print(f"  {line}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
