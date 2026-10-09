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

Доводка (ШАГ «Полные стороны + склонение с учётом рода»)
-------------------------------------------------------
Прежняя правка оставила в п. 1.1 и 1.2 НЕПОЛНЫЕ стороны и именительный
падеж:

  * у ООО не печатались ИНН, КПП и ОГРН — сторона называлась одним
    наименованием, а реквизиты искать приходилось в п. 9;
  * должность и ФИО стояли в ИМЕНИТЕЛЬНОМ падеже: «в лице Генеральный
    директор Петров Пётр Петрович, действующего» вместо «в лице
    генерального директора Петрова Петра Петровича, действующего»;
  * у директора-женщины причастие было мужским («действующего»);
  * у ИП-женщины — «именуемый» вместо «именуемая»;
  * у ИП ветвь п. 1.1 не печатала приставку «Индивидуальный
    предприниматель» (её давала константа legal_form только в п. 1.2).

Падеж и род считает генератор (core/contracts/ru_morphology.py), бланк
берёт готовые ключи: `*_legal_form_prefix`, `*_director_position_genitive`,
`*_director_genitive`, `*_acting_genitive`, `*_kpp_line`.

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

Доводка проверяется тем же способом, но её «прежний вид» — это текст ПЕРВОЙ
правки (бланки уже правлены). Второй прогон доводки не добавляет вторую пару
`{%p if %}`: её абзацев в прежнем виде в бланке уже нет, а признак
(`{%p if is_client_ip %}`) есть.

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

    #: Чем ДОКАЗЫВАЕТСЯ, что правка сделана: строки, которые после неё
    #: появляются в документе. По умолчанию — её же маркер; у правок первой
    #: ступени доказательство берётся у второй ступени той же фразы: первая
    #: ступень заменяет абзац на четыре, и её собственный текст в документе
    #: целиком уже не стоит.
    evidence: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """
        Проверка вида полей — защита от тихой опечатки.

        `new` обязан быть КОРТЕЖЕМ строк. Строка вместо кортежа проходит
        проверку типов Python (аннотации не проверяются), а потом правка
        «вставляет» в абзац 335 абзацев по одному символу: склейка соседних
        литералов без завершающей запятой — `new=("раз" "два")` — это ровно
        одна строка «раздва». Ловится это только на готовом договоре, а
        причина не видна; поэтому проверяем сразу.
        """
        if isinstance(self.new, str):
            raise TypeError(
                f"Edit.new — строка, а нужен кортеж строк: {self.marker!r}. "
                f"Вероятно, забыта запятая после литерала."
            )
        for text in self.new:
            if not isinstance(text, str):
                raise TypeError(
                    f"Edit.new: элемент {text!r} не строка ({self.marker!r})"
                )

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
    П. 1.2 бланков ИП в СОСТОЯНИИ ДО ДОВОДКИ (историческая правка).

    В наборе правок не применяется: прежний вид ветви ИП переписывает
    доводка (`full_carrier_clause_ip`) — она добавляет приставку вида лица.
    Оставлена как документ о первом переходе (коммит a48a138) и как
    страховка для бланка без пары ветвей: там «абзац не найден» честно
    скажет, что раскладка другая.

    Проверяется тестом `tests/test_perevozka_template_parties.py`.
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
        evidence=CARRIER_CLAUSE_EVIDENCE,
    )


#: Признаки правок п. 1.1 и 1.2 ООО-ветвей: после ДОВОДКИ в абзаце
#: появляется падеж должности. Они же — доказательства для ранних ступеней
#: своих фраз: первая ступень заменяет абзац на несколько, своего текста в
#: документе целиком уже не оставляет, а ключ падежа появляется только
#: после доводки.
#:
#: Ключи СВОИ у каждой стороны (client_ / carrier_): общий ключ доказывал бы
#: чужую правку — доведённый п. 1.1 «закрывал» бы и п. 1.2.
#:
#: И у каждой ступени — СВОЙ набор: у бланков разная история. В ООО-бланке
#: ветка ООО п. 1.2 прошла первую ступень ещё до того, как появился её
#: прежний вид у бланков ИП, поэтому доведённый абзац в нём выглядит
#: иначе — и это тоже доказательство «правка сделана».
CLIENT_CLAUSE_EVIDENCE = ("{{client_director_position_genitive}}",)
CARRIER_CLAUSE_EVIDENCE = (
    "{{carrier_director_position_genitive}}",
    "{{carrier_legal_form_prefix}}{{carrier_full_name}}{{carrier_name_in_parens}}",
)


#: Прежний текст п. 1.2 ООО-бланка: ветка ООО в том виде, в каком она
#: пришла из бланка заказчика. Отсюда её переводит на данные первая ступень.
CARRIER_CLAUSE_OOO_OLD = (
    "{{carrier_full_name}} ({{carrier_name}}), ИНН {{carrier_inn}}, "
    "КПП {{carrier_kpp}}, ОГРН {{carrier_ogrn}}, в лице директора "
    "{{carrier_director}}, действующего на основании Устава, "
    "именуемое в дальнейшем «Перевозчик»."
)


def edit_carrier_clause_ooo() -> Edit:
    """
    П. 1.2 ООО-бланка в СОСТОЯНИИ ДО ДОВОДКИ (историческая правка).

    В наборе правок не применяется: прежний вид этого абзаца переписывает
    доводка (`full_carrier_clause_ooo_v0`) — она сразу делает пару ветвей
    «ИП / ООО» с полными реквизитами и падежом. Оставлена как документ о
    первом переходе (коммит a48a138) и как страховка для бланка, где ветвь
    ИП ещё не появилась: тогда «абзац не найден» честно скажет, что раскладка
    другая, вместо молча недоведённого п. 1.2.

    Проверяется тестом `tests/test_perevozka_template_parties.py`.
    """
    return Edit(
        old=CARRIER_CLAUSE_OOO_OLD,
        new=(
            "{{carrier_full_name}}{{carrier_name_in_parens}}, ИНН "
            "{{carrier_inn}}, {% if has_carrier_kpp %}КПП {{carrier_kpp}}, "
            "{% endif %}{{carrier_ogrn_label}} {{carrier_ogrn}}, в лице "
            "директора {{carrier_director}}, действующего на основании "
            "Устава, именуемое в дальнейшем «Перевозчик».",
        ),
        marker="{{carrier_name_in_parens}}",
        evidence=CARRIER_CLAUSE_EVIDENCE,
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


#: Правки ПЕРВОЙ ступени по именам: условные ветви, подписи, пустые поля.
#: Список — справочный (отчёт и тесты ссылаются на функции по имени); сам
#: набор собирает `edits_for`. Правки п. 1.2 в наборе НЕ применяются: их
#: абзац переписывает доводка (`full_party_edits`) сразу с парами ветвей и
#: реквизитами, а функции оставлены как документ первого перехода
#: (коммит a48a138).
V1_EDITS = (
    edit_client_clause,
    edit_client_signature,
    edit_client_ogrn_label,
    edit_carrier_clause_ip,
    edit_carrier_clause_ooo,
    edit_carrier_signature_ooo,
    edit_carrier_ogrn_label_ooo,
)


def edits_for(template_name: str) -> Tuple[Edit, ...]:
    """
    Ступень после доводки: подписи, метки ОГРН и строки необязательных полей.

    П. 1.1 и 1.2 здесь НЕ правятся: и исходный вид этих абзацев, и
    промежуточный переписывает доводка (`full_party_edits`) — сразу в
    окончательный вид, с парой ветвей «ИП / ООО», полными реквизитами и
    падежом. Ранние правки этих же абзацев оставлены в модуле как документ
    первого перехода, но в набор не входят: абзац ищется по всему тексту, и
    вторая правка под него только запутала бы выбор.
    """
    edits: List[Edit] = [
        edit_client_clause(),
        edit_client_signature(),
        edit_client_ogrn_label(),
    ]

    if template_name == "shablon_ooo.docx":
        edits += [
            edit_carrier_signature_ooo(),
            edit_carrier_ogrn_label_ooo(),
        ]
        optional_lines = COMMON_OPTIONAL_LINES + OOO_OPTIONAL_LINES
    else:
        optional_lines = COMMON_OPTIONAL_LINES + IP_OPTIONAL_LINES

    edits += [optional_line_edit(text, flag) for text, flag in optional_lines]
    return tuple(edits)


# ─────────────────────────────────────────────────────────────
# Вторая ступень: полные стороны и падеж
# ─────────────────────────────────────────────────────────────
#
# «Прежний вид» этих правок — результат ПЕРВОЙ ступени (бланки уже правлены),
# поэтому обе ступени живут в одном инструменте: первая отрабатывает на
# исходном бланке, вторая — на её результате. Обе идемпотентны, и порядок
# задаёт `combined_edits`.

def full_client_clause_ip() -> Edit:
    """П. 1.1, ветвь ИП: приставка вида лица, ИНН и ОГРНИП."""
    return Edit(
        old=(
            "{{client_legal_form}} {{client_full_name}}, {{client_pronoun}} "
            "в дальнейшем «Заказчик», {{client_acting}} на основании "
            "свидетельства о государственной регистрации."
        ),
        new=(
            "{{client_legal_form}} {{client_full_name}}, ИНН "
            "{{client_inn}}, {{client_ogrn_label}} {{client_ogrn}}, "
            "{{client_acting}} на основании свидетельства о государственной "
            "регистрации, {{client_pronoun}} в дальнейшем «Заказчик».",
        ),
        marker="{{client_legal_form}} {{client_full_name}}, ИНН",
        evidence=CLIENT_CLAUSE_EVIDENCE,
    )


def full_client_clause_ooo() -> Edit:
    """П. 1.1, ветвь ООО: ИНН, КПП, ОГРН и падеж должности с ФИО."""
    return Edit(
        old=(
            "{{client_full_name}}{{client_name_in_parens}}, именуемое в "
            "дальнейшем «Заказчик», в лице {{client_director_position_short}} "
            "{{client_director}}, действующего на основании Устава."
        ),
        new=(
            "{{client_legal_form_prefix}}{{client_full_name}}"
            "{{client_name_in_parens}}, ИНН {{client_inn}}, "
            "{% if has_client_kpp %}КПП {{client_kpp}}, {% endif %}"
            "{{client_ogrn_label}} {{client_ogrn}}, "
            "именуемое в дальнейшем «Заказчик», в лице "
            "{{client_director_position_genitive}} "
            "{{client_director_genitive}}, {{client_acting_genitive}} "
            "на основании {{client_basis}}.",
        ),
        marker="{{client_director_position_genitive}}",
        evidence=CLIENT_CLAUSE_EVIDENCE,
    )


def full_carrier_clause_ip() -> Edit:
    """П. 1.2, ветвь ИП (бланки ИП): приставка вида лица, ИНН и ОГРНИП."""
    return Edit(
        old=(
            "{{carrier_legal_form}} {{carrier_full_name}}, ИНН "
            "{{carrier_inn}}, {{carrier_ogrn_label}} {{carrier_ogrn}}, "
            "{{carrier_acting}} на основании {{carrier_basis}}, "
            "{{carrier_pronoun}} в дальнейшем «Перевозчик»."
        ),
        new=(
            "{{carrier_legal_form}} {{carrier_full_name}}, ИНН "
            "{{carrier_inn}}, {{carrier_ogrn_label}} {{carrier_ogrn}}, "
            "{{carrier_acting}} на основании {{carrier_basis}}, "
            "{{carrier_pronoun}} в дальнейшем «Перевозчик».",
        ),
        marker="{{carrier_legal_form}} {{carrier_full_name}}, ИНН",
    )


def _carrier_clause_ooo_body() -> Tuple[str, ...]:
    """
    Новые абзацы п. 1.2: пара ветвей «ИП / ООО» с полными реквизитами.

    Ветвь ИП начинается с `{{carrier_legal_form}}` — это «Индивидуальный
    предприниматель» целиком. Суффиксный ключ `{{*_legal_form_prefix}}` у
    ИП всегда пуст (приставку снимает `_clean_ip_name`), поэтому в ветви ИП
    он не используется: иначе приставки не было бы вовсе.

    Ветвь ООО, наоборот, полагается на суффиксный ключ: он печатает
    «Общество с ограниченной ответственностью» только тогда, когда
    сокращения нет в самом наименовании.
    """
    return (
        _tag_if("is_carrier_ip"),
        "{{carrier_legal_form}} {{carrier_full_name}}, ИНН "
        "{{carrier_inn}}, {{carrier_ogrn_label}} {{carrier_ogrn}}, "
        "{{carrier_acting}} на основании {{carrier_basis}}, "
        "{{carrier_pronoun}} в дальнейшем «Перевозчик».",
        ELSE_TAG,
        "{{carrier_legal_form_prefix}}{{carrier_full_name}}"
        "{{carrier_name_in_parens}}, ИНН {{carrier_inn}}, "
        "{% if has_carrier_kpp %}КПП {{carrier_kpp}}, {% endif %}"
        "{{carrier_ogrn_label}} {{carrier_ogrn}}, "
        "в лице {{carrier_director_position_genitive}} "
        "{{carrier_director_genitive}}, {{carrier_acting_genitive}} "
        "на основании {{carrier_basis}}, именуемое в дальнейшем "
        "«Перевозчик».",
        ENDIF_TAG,
    )


def full_carrier_clause_ooo_ip_branch() -> Edit:
    """
    П. 1.2: ветвь ООО становится парой ветвей «ИП / ООО» с падежом.

    Прежний вид ветви ООО в бланках — «{{carrier_legal_form}}
    {{carrier_full_name}}, … в лице директора {{carrier_director}},
    действующего на основании Устава, именуемое …». Так он выглядит и в
    бланках ИП, и в ООО-бланке ПОСЛЕ первой ступени.

    Приставку вида лица в окончательном виде ставит
    `{{carrier_legal_form_prefix}}`: он печатает «Общество с ограниченной
    ответственностью», если наименование записано коротко, и ничего не
    добавляет, если сокращение в наименовании уже есть. Ключ
    `{{carrier_legal_form}}` остаётся в карте замен для внешнего кода и
    старых бланков.
    """
    return Edit(
        old=(
            "{{carrier_legal_form}} {{carrier_full_name}}, ИНН "
            "{{carrier_inn}}, {% if has_carrier_kpp %}КПП {{carrier_kpp}}, "
            "{% endif %}{{carrier_ogrn_label}} {{carrier_ogrn}}, в лице "
            "{{carrier_director_position}} {{carrier_director}}, "
            "действующего на основании {{carrier_basis}}, именуемое в "
            "дальнейшем «Перевозчик»."
        ),
        new=_carrier_clause_ooo_body(),
        marker="{{carrier_director_position_genitive}}",
        evidence=CARRIER_CLAUSE_EVIDENCE,
    )


def full_carrier_clause_ooo_v0() -> Edit:
    """
    То же для ООО-бланка: у него ветвь ООО своя.

    Здесь ветвь начинается сразу с наименования (приставку вида лица
    поставила первая ступень), а должность, основание и причастие —
    константы бланка: «… в лице директора {{carrier_director}},
    действующего на основании Устава, именуемое …». Так выглядел п. 1.2
    ООО-бланка ПОСЛЕ первой ступени (коммит a48a138).
    """
    return Edit(
        old=(
            "{{carrier_full_name}}{{carrier_name_in_parens}}, ИНН "
            "{{carrier_inn}}, {% if has_carrier_kpp %}КПП {{carrier_kpp}}, "
            "{% endif %}{{carrier_ogrn_label}} {{carrier_ogrn}}, в лице "
            "директора {{carrier_director}}, действующего на основании "
            "Устава, именуемое в дальнейшем «Перевозчик»."
        ),
        new=_carrier_clause_ooo_body(),
        marker="{{carrier_full_name}}, ИНН",
        evidence=CARRIER_CLAUSE_EVIDENCE,
    )


def full_carrier_clause_ooo_pre_v1() -> Edit:
    """
    П. 1.2 ООО-бланка в СОСТОЯНИИ ДО ПЕРВОЙ СТУПЕНИ (историческая правка).

    У этого бланка ветка ООО п. 1.2 была переведена на данные РАНЬШЕ первой
    ступени, поэтому её прежний вид — уже с `{{carrier_name_in_parens}}`,
    условным КПП и `{{carrier_ogrn_label}}`. Так выглядели бланки до
    коммита a48a138; в текущем наборе эта правка не применяется (её прежний
    текст заменён вложенной ветвью в `full_carrier_clause_ooo_ip_branch`),
    но оставлена как страховка для бланка из старой ветки разработки:
    «абзац не найден» там лучше, чем недоведённый п. 1.2.

    Проверяется тестом `tests/test_perevozka_template_parties.py`.
    """
    return Edit(
        old=(
            "{{carrier_full_name}}{{carrier_name_in_parens}}, ИНН "
            "{{carrier_inn}}, {% if has_carrier_kpp %}КПП {{carrier_kpp}}, "
            "{% endif %}{{carrier_ogrn_label}} {{carrier_ogrn}}, в лице "
            "директора {{carrier_director}}, действующего на основании "
            "Устава, именуемое в дальнейшем «Перевозчик»."
        ),
        new=(
            "{{carrier_legal_form_prefix}}{{carrier_full_name}}"
            "{{carrier_name_in_parens}}, ИНН {{carrier_inn}}, "
            "{% if has_carrier_kpp %}КПП {{carrier_kpp}}, {% endif %}"
            "{{carrier_ogrn_label}} {{carrier_ogrn}}, "
            "в лице {{carrier_director_position_genitive}} "
            "{{carrier_director_genitive}}, {{carrier_acting_genitive}} "
            "на основании {{carrier_basis}}, именуемое в дальнейшем "
            "«Перевозчик».",
        ),
        marker="{{carrier_director_position_genitive}}",
        evidence=CARRIER_CLAUSE_EVIDENCE,
    )


def full_party_edits(template_name: str) -> Tuple[Edit, ...]:
    """
    Доводка п. 1.1 и 1.2: полные стороны и падеж.

    П. 1.1 во всех трёх бланках одинаков и уже условный (первая ступень),
    поэтому к нему добавляются ОБЕ ветви — ИП и ООО.

    П. 1.2: правок ДВЕ, и набор зависит от бланка — прежний вид ветви ООО
    у них разный:

      * ветвь ИП (`{{carrier_legal_form}} {{carrier_full_name}}, … на
        основании {{carrier_basis}}`) — есть только в бланках ИП, получает
        приставку вида лица;
      * ветвь ООО — превращается в пару ветвей «ИП / ООО» с полными
        реквизитами и падежом. В бланках ИП она начинается с
        `{{carrier_legal_form}}`, в ООО-бланке — сразу с наименования:
        приставку туда поставила первая ступень.
    """
    edits: List[Edit] = [
        full_client_clause_ip(),
        full_client_clause_ooo(),
    ]
    if template_name == "shablon_ooo.docx":
        # В ООО-бланке ветвь ООО одна и начинается сразу с наименования:
        # приставку вида лица туда поставила первая ступень. Ветви ИП в этом
        # бланке нет вовсе — доводка её создаёт.
        edits.append(full_carrier_clause_ooo_v0())
    else:
        # В бланках ИП ветвей две: ИП (начинается с {{carrier_legal_form}})
        # и ООО. Первой правится ветвь ИП, второй — ветвь ООО.
        edits += [
            full_carrier_clause_ip(),
            full_carrier_clause_ooo_ip_branch(),
        ]
    return tuple(edits)


#: Ступени, которые идут ПОСЛЕ первой: правки, дополняющие абзац, а не
#: заменяющие его. Для бланков ИП это ветвь ИП п. 1.2: доводка её не
#: трогает, и приставку вида лица ей ставит отдельная правка.
AFTER_FULL_EDITS = {
    "shablon_ip_with_vat.docx": (full_carrier_clause_ip,),
    "shablon_ip_without_vat.docx": (full_carrier_clause_ip,),
}


def after_full_edits(template_name: str) -> Tuple[Edit, ...]:
    """Правки, дополняющие абзац после доводки (для бланков ИП)."""
    return tuple(factory() for factory in AFTER_FULL_EDITS.get(template_name, ()))


def combined_edits(template_name: str) -> Tuple[Tuple[Edit, ...], ...]:
    """
    Правки тремя ступенями — от окончательного вида к промежуточному.

    Порядок ступеней не косметический: доводка (`full_party_edits`) пишет в
    ТОТ ЖЕ абзац, что и первая ступень, и «до» у них одно и то же — исходный
    вид бланка. Поэтому сначала идёт доводка: она переписывает абзац сразу в
    окончательный вид (полные реквизиты, падеж, пара ветвей «ИП / ООО»).
    После неё первой ступени в этом абзаце делать нечего — её прежнего текста
    там уже нет.

    Обратный порядок ломает бланк: на уже правленом файле первая ступень
    снова нашла бы свой прежний текст (он в бланке есть — это же её
    результат), вставила бы ВТОРУЮ пару `{%p if … %}` внутрь готового блока
    и выбросила ветку «иначе».

    Ветвь ИП п. 1.2 — исключение: доводка её только дополняет (приставка
    вида лица), абзац НЕ заменяет, и после неё он читается как обычно.
    Поэтому для неё третья ступень, и она не помечает прежний текст
    израсходованным (см. `Edit.consumes`).

    Один прогон инструмента доводит бланк ЛЮБОГО состояния: исходный, уже
    правленный первой ступенью или уже правленный обеими (тогда отчёт —
    сплошное «уже правлено», и файл не переписывается).
    """
    return (
        full_party_edits(template_name),
        edits_for(template_name),
        after_full_edits(template_name),
    )


# ─────────────────────────────────────────────────────────────
# Применение правок
# ─────────────────────────────────────────────────────────────

def apply_edits(
    xml: str,
    stages: Sequence[Sequence[Edit]],
) -> Tuple[str, List[str]]:
    """
    Применяет правки к XML документа ступенями.

    Возвращает (новый_xml, отчёт). Отчёт — по строке на правку: «правка»,
    «уже правлено» или «ОШИБКА: абзац не найден». Если нашлась хоть одна
    ОШИБКА, XML возвращается БЕЗ изменений: лучше без правки, чем чужая.

    Ступени нужны потому, что прежний текст второй ступени — это РЕЗУЛЬТАТ
    первой: п. 1.1 сначала становится условным (теги + две ветви), и только
    потом ветви получают полные реквизиты и падеж. Внутри одной ступени
    правки ищутся в исходном тексте и применяются одним проходом; абзац,
    который ступень уже заменила, следующей ступени не предлагается — её
    прежний текст в этом файле ещё не существует.

    Идемпотентность проверяется по САМОМУ документу, а не по счётчику
    запусков: строка необязательного поля остаётся в бланке и после правки
    (её лишь окружают теги), поэтому «уже правлено» — это соседний абзац-тег
    из этой же правки. Правки, которые абзац ЗАМЕНЯЮТ, узнаются проще:
    прежнего текста в бланке больше нет, а новые абзацы — есть.
    """
    gaps, paragraphs = split_with_gaps(xml)
    texts = [normalize(paragraph_text(paragraph)) for paragraph in paragraphs]
    text_set = set(texts)

    report: List[str] = []
    replaced: Dict[int, str] = {}
    applied = 0
    #: Правки, о которых уже отчитались — чтобы не повторяться.
    reported: set = set()
    #: Ключи правок, которые СРАБОТАЛИ в этом прогоне (по нормализованному
    #: прежнему тексту). Правка с таким же ключом из другой ступени молчит:
    #: абзац уже заменён, и конечный вид в бланке есть.
    handled_keys: set = set()

    for edits in stages:
        # Все ступени одной фразы работают с ОДНИМ И ТЕМ ЖЕ абзацем, поэтому
        # группируются по своему прежнему тексту: если абзац заменила любая
        # правка группы, группа считается сделанной целиком. Сравнение — по
        # нормализованному тексту: один и тот же пункт в трёх бланках набран
        # чуть по-разному.
        by_text: Dict[str, List[Edit]] = {}
        for edit in edits:
            by_text.setdefault(normalize(edit.old), []).append(edit)

        for index, paragraph in enumerate(paragraphs):
            key = texts[index]
            group = by_text.get(key)
            if not group or key in handled_keys:
                # Абзац в прежнем виде уже заменён более ранней правкой: её
                # результат и есть то, что эта ступень должна была сделать.
                continue

            # Побеждает САМЫЙ ДЛИННЫЙ совпавший прежний текст. Совпадение при
            # этом точное: абзац ищется в бланке целиком, по своему тексту.
            # Урезанных «прежних текстов» в наборе правок быть не должно —
            # короткий образец совпал бы и с чужим абзацем (у п. 1.2 бланков
            # ИП и у п. 1.2 ООО-бланка общее начало), и правка ушла бы не
            # туда.
            longest = max(group, key=lambda e: len(normalize(e.old)))
            edit = next(
                (e for e in group if key == normalize(e.old)), longest
            )

            if _already_applied(texts, index, edit):
                continue

            replaced[index] = "\n".join(
                make_paragraph(paragraph, new_text) for new_text in edit.new
            )
            handled_keys.add(key)
            applied += 1
            report.append(f"правка: {edit.marker} (абзац {index})")

    # Итог по каждой правке. Правки, сработавшие на абзаце, уже в отчёте; у
    # остальных либо бланк уже правлен — и тогда в документе есть все новые
    # абзацы правки, — либо раскладка другая, и это ОШИБКА. Правка, чей абзац
    # заменила ДРУГАЯ правка той же фразы (её прежний текст уже израсходован),
    # молчит: конечный вид в бланке достигнут.
    for edits in stages:
        for edit in edits:
            if id(edit) in reported:
                continue
            reported.add(id(edit))
            if normalize(edit.old) in handled_keys:
                continue
            if _marker_present(edit, text_set):
                report.append(f"уже правлено: {edit.marker}")
            else:
                # В отчёт — кусок, на котором правки расходятся: у п. 1.2
                # начало текста у нескольких правок одинаковое, и по первым
                # 60 символам не понять, какая не нашла свой абзац. Длина
                # прежнего текста различает и такие правки.
                report.append(
                    f"ОШИБКА: абзац не найден ({len(edit.old)} симв.) — "
                    f"{edit.old[:120]!r}"
                )

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


def _marker_present(edit: Edit, text_set: Sequence[str]) -> bool:

    """
    Есть ли в документе доказательство, что правка уже сделана.

    Доказательство — ВСЕ новые абзацы правки, найденные в тексте документа
    целиком. Проверка по одному абзацу врёт: текст соседней ветви может
    совпасть с новым текстом другой правки, и правка «нашлась» бы в ещё не
    правленом бланке (именно так в бланках ИП осталась недоведённой ветвь
    ООО п. 1.2 — её новый абзац ИП совпал с уже стоявшей ветвью ИП).

    Текст ищется ПОДСТРОКОЙ в склейке абзацев: ключ может стоять в абзаце
    не началом («{{carrier_director_position_genitive}}
    {{carrier_director_genitive}}») и целиком с абзацем не совпадёт. Склейка,
    а не каждый абзац: иначе ключ, разорванный границей абзаца, «нашёлся» бы
    там, где его нет.

    Ключ правки (`edit.marker`) — не доказательство, а подсказка: он входит
    и в новый текст, и в справочное «доказательство» ранних ступеней.
    """
    document_text = "\n".join(text_set)
    return all(
        normalize(text) and normalize(text) in document_text
        for text in edit.new
    )


def _already_applied(
    texts: Sequence[str],
    index: int,
    edit: Edit,
) -> bool:
    """
    Абзац уже в обработке этой правки?

    Правки-ОБЁРТКИ (строки необязательных полей) сам абзац оставляют в
    бланке и лишь окружают его тегами, поэтому признак сделанной работы —
    теги С ДВУХ СТОРОН: `{%p if … %}` перед абзацем и `{%p endif %}` после
    него. Одного тега перед абзацем мало: ветвь `else` условного блока стоит
    между `{%p else %}` и `{%p endif %}` — то есть внутри ЧУЖОГО блока, — и
    правка, у которой первым тегом тоже идёт `{%p else %}`, решила бы, что
    уже сделана. Ровно так в бланках ИП не доводилась ветвь ООО п. 1.2:
    её абзац пропускался, а в договор уходил старый текст с именительным
    падежом.
    """
    first = _first_tag(edit)
    if not first or index == 0 or index + 1 >= len(texts):
        return False

    last = _last_tag(edit)
    return texts[index - 1] == first and texts[index + 1] == last


def _tag_texts(edit: Edit) -> set:
    """
    Абзацы-теги docxtpl из новых абзацев правки.

    Тег узнаётся по началу абзаца: `{%p … %}` — единственное, что стоит
    в абзаце целиком, поэтому начало и есть весь абзац. Текст ветки между
    тегами в этот набор не попадает: он может совпасть с содержимым чужого
    абзаца, и правка «нашлась бы» в неотредактированном бланке.
    """
    return {normalize(text) for text in edit.new if _is_tag(text)}


def _first_tag(edit: Edit) -> str:
    """Первый абзац-тег правки."""
    tags = [normalize(text) for text in edit.new if _is_tag(text)]
    return tags[0] if tags else ""


def _last_tag(edit: Edit) -> str:
    """Последний абзац-тег правки — `{%p endif %}` у правок-замен."""
    tags = [normalize(text) for text in edit.new if _is_tag(text)]
    return tags[-1] if tags else ""


def _is_tag(text: str) -> bool:
    """
    Абзац целиком — тег docxtpl (`{%p … %}`), а не текст ветки.

    Нужны ОБА конца: одного начала «{%p » мало. В бланках ИП ветка ООО
    п. 1.2 начинается с вставки `{% if has_carrier_kpp %}` посреди текста,
    и по началу такой абзац сошёл бы за тег — правка решила бы, что уже
    сделана, и оставила ветку недоведённой (ИНН и падеж в договор не
    попали бы).
    """
    normalized = normalize(text)
    return normalized.startswith("{%p ") and normalized.endswith("%}")


def check_template(path: Path) -> List[str]:
    """Состояние бланка: какие правки уже сделаны, а какие ждут."""
    if not path.exists():
        return ["НЕ НАЙДЕН"]

    with zipfile.ZipFile(str(path)) as archive:
        xml = archive.read(DOCUMENT_PART).decode("utf-8")

    _, report = apply_edits(xml, combined_edits(path.name))
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
    new_xml, report = apply_edits(xml, combined_edits(path.name))

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
