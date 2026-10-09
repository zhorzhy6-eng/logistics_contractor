#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Стресс-проверка генерации DOCX (БЛОК 1 стресс-теста): генерация и контроль качества.

Две операции:

    python tools/stress_check.py generate    # сценарии → DOCX
    python tools/stress_check.py check       # DOCX → problems.json
    python tools/stress_check.py all         # и то, и другое

Результаты:

    tests/_tmp/stress_output/<номер>.docx     — документы
    tests/_tmp/stress_output/errors.log       — исключения с traceback
    tests/_tmp/stress_output/problems.json    — найденные дефекты
    tests/_tmp/stress_output/summary.json     — сводка по severity и видам

ЧТО ИМЕННО ПРОВЕРЯЕТСЯ
----------------------
Список проверок — из задания БЛОКА 1, пункты «а»…«м», плюс структурные
(таблицы, пустые строки) и «пустые поля»: незаполненное необязательное поле
не должно оставлять в документе строку-«дырку» вида «КПП ,».

ВАЖНО ПРО ПЕРСОНАЛЬНЫЕ ДАННЫЕ
-----------------------------
Все сценарии БЛОКА 1 синтетические. В problems.json попадает только ВИД
дефекта и обезличенный фрагмент: длинные последовательности цифр заменяются
на «<цифры: N>», поэтому ни серия паспорта, ни счёт, ни VIN в отчёт целиком
не попадают. Это перестраховка на случай, если сценарий когда-нибудь
соберут из реальных данных.
"""

import argparse
import json
import re
import shutil
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.num_to_words import amount_to_words  # noqa: E402

SCENARIO_DIR = PROJECT_ROOT / "tests" / "_tmp" / "stress_scenarios"
OUT_DIR = PROJECT_ROOT / "tests" / "_tmp" / "stress_output"
TEMPLATES_DIR = PROJECT_ROOT / "templates"

#: Шаблоны с встроенными шрифтами (~2,7 МБ). Для проверок текста шрифты не
#: нужны, а копирование 100 раз по 2,7 МБ — это 270 МБ на диске и лишние
#: минуты. Облегчённые копии делает tools/make_golden.strip_embedded_fonts;
#: текст документа от них не меняется (это проверено golden-тестами).
TEMPLATE_NAMES = ("shablon_ooo.docx", "shablon_ip_with_vat.docx",
                  "shablon_ip_without_vat.docx")

#: Уровни важности. high — документ нельзя отдавать клиенту, medium — заметный
#: брак, low — косметика.
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

#: Разделители, перед которыми пробел — ошибка вёрстки.
SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")

#: Предлоги и союзы, которые чаще всего «приклеиваются» к следующему слову.
#: «не» и «ни» здесь НЕТ намеренно: это приставки («несоблюдения»,
#: «непредоставления»), и разбор «не + соблюдения» давал бы ложные
#: срабатывания на каждом втором абзаце договора.
GLUE_PREFIXES = ("в", "во", "на", "за", "из", "от", "до", "по", "при",
                 "под", "над", "об", "о", "с", "со", "к", "ко", "у", "для")

#: Слова, которые сами начинаются с предлога, и потому разбор «предлог +
#: остаток» давал бы ложное срабатывание. Список короткий и закрытый: это
#: именно те слова, что встречаются в бланках перевозки.
GLUE_FALSE_WORDS = {
    "восток", "вопрос", "воля", "вокруг", "вместе", "вправе", "вводе",
    "настоящее", "настоящий", "настоящего", "настоящему", "настоящим",
    "настоящих", "например", "наличии", "накладной", "наименование",
    "наименования", "насчет", "наилучшим",
    "порядок", "порядке", "помощью", "поручения", "поставки", "полноте",
    "изменения", "изменение", "изготовителя", "извещение", "издержки",
    "ответственность", "ответственности", "отчет", "отчета", "отметки",
    "договор", "договора", "договору", "документов", "документы",
    "доставки", "допускается", "должен", "должна", "добровольно",
    "приемки", "приема", "приложения", "приложение", "причин", "причем",
    "подписания", "подписанный", "подтверждает", "подтверждены",
    "надлежащим", "надлежащего",
    "срок", "срока", "сроки", "суммы", "сумма", "стороны", "сторона",
    "соглашения", "согласованным", "соответствии", "соответствующем",
    "соответствующему", "собственника", "сообщения", "составе",
    "контрагента", "компенсации", "качестве", "которых", "которые",
    "условия", "условий", "услуг", "услуги", "убытки", "убытков",
    "указанием", "указанного", "урегулирования",
    "недостачу", "неисправности", "невозможности", "незамедлительно",
    "независимо", "необходимые", "неотъемлемой",
    "ниже", "них", "ним", "объеме", "обмена", "обработку",
    "законом", "законодательства", "заказчика", "заказчику", "заключения",
    "доначисления", "законных", "защиты", "завершения", "замену",
    "взыскания", "возмещает", "возместить", "возникает", "восстановительного",
    "вступает", "вправе", "включая", "включения", "внутренний",
    "взыскать", "выдачи", "выявленным", "выплате", "вычета",
    "отказа", "отказ", "отсутствие", "отсутствия",
    "причиной", "приведенные", "предъявить", "предоставления",
    "сведения", "своевременную", "связанные", "следствием",
    "подтвержденного", "получения", "полученных", "получен",
    "котором", "которой", "которого", "которая",
    "услугам", "указанный", "уплаченных", "уплачивает",
    "направленные", "нарушения", "нарушение", "находящиеся",
    "поставленную", "поставленного",
    # Числительные, начинающиеся с предлога: «восемьдесят» = «во» + «семьдесят».
    "восемьдесят", "восемьсот", "восемнадцать", "восьмой",
    "сорок", "сорока", "семьдесят", "семьсот", "семнадцать",
    "пятьдесят", "пятьсот", "пятнадцать",
    "двести", "двухсот", "триста", "трёхсот",
}

#: Признаки незаполненных плейсхолдеров и «мусорных» значений.
PLACEHOLDER_MARKERS = ("{{", "}}", "{%", "%}")
JUNK_VALUES = ("none", "null", "undefined", "nan", "nil")

#: Форматы дат, допустимые В ДОКУМЕНТЕ. ISO-вид в договоре не печатается:
#: там дата всегда ДД.ММ.ГГГГ (полная) или ДД.ММ (день подачи).
ISO_DATE = re.compile(r"\b\d{4}[-./]\d{1,2}[-./]\d{1,2}\b")

#: Кандидат в VIN: 17 знаков, среди них есть и буквы, и цифры — так номер
#: отличается от счёта, ОГРН и прочих числовых реквизитов. Границы слова
#: заданы явно: «\b» не работает между двумя цифрами и буквами без пробела.
VIN_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Z0-9]{17}(?![A-Za-z0-9]))"
    r"(?=[A-Z0-9]*[0-9])(?=[A-Z0-9]*[A-Z])[A-Z0-9]{17}"
)

#: Телефон: только то, что начинается с «+7» или «8» и похоже на номер.
#: Без ведущей семёрки проверка ловила серии паспорта («27 79 123456») —
#: это не телефон, а совсем другой реквизит. Хвост ограничен разделителями
#: «пробел, дефис, точка»: без этого в номер «затягивалась» следующая за
#: ним серия паспорта («89 14 123456»).
PHONE_CANDIDATE = re.compile(
    r"(?:\+7|\b8)[\s\-.]?\(?\d{3}\)?"
    r"(?:[\s\-.]?\d){7}"
    r"(?![\s\-.]?\d)"
)

#: Эталонный вид телефона в документе.
PHONE_GOOD = re.compile(r"^\+7 \(\d{3}\) \d{3}-\d{2}-\d{2}$")

#: Сумма в документе: «180300.00 руб. (Сто восемьдесят тысяч триста рублей
#: 00 копеек)». Пары «число + прописью» сверяются между собой.
MONEY_PAIR = re.compile(
    r"(\d[\d\s\u00a0]*\.\d{2})\s*руб\.\s*\(([^)]{3,200})\)"
)

#: Необязательные поля, отсутствие значения у которых не должно оставлять
#: в документе «висящую» подпись поля: «КПП ,», «E-mail:», «Фактический
#: адрес:». Образцы намеренно узкие: между меткой и знаком препинания
#: допускается НЕ БОЛЕЕ ОДНОГО пробела — иначе проверка срабатывала бы на
#: нормальном тексте («…ИНН 7701…, КПП 770101001, ОГРН …»).
EMPTY_LABEL_PATTERNS = [
    (r"КПП ?(?:[,;.]|$)", "пустое поле «КПП» напечатано без значения"),
    (r"E-mail:? ?(?:[,;.]|$)", "пустое поле «E-mail» напечатано без значения"),
    (r"Фактический адрес:? ?(?:[,;.]|$)",
     "пустой «Фактический адрес» напечатан без значения"),
    (r"Место рождения:? ?(?:[,;.]|$)",
     "пустое «Место рождения» напечатано без значения"),
    (r"Категории:? ?(?:[,;.]|$)",
     "пустые «Категории» ВУ напечатаны без значения"),
    (r"Цвет:? ?(?:[,;.]|$)", "пустой «Цвет» ТС напечатан без значения"),
    (r"Год выпуска:? ?(?:[,;.]|$)",
     "пустой «Год выпуска» напечатан без значения"),
    (r"Банк:? ?(?:[,;.]|$)", "пустое «Банк» напечатано без значения"),
    (r"р/с ?(?:[,;.]|$)", "пустой «р/с» напечатан без значения"),
    (r"БИК ?(?:[,;.]|$)", "пустой «БИК» напечатан без значения"),
    (r"к/с ?(?:[,;.]|$)", "пустой «к/с» напечатан без значения"),
    (r"Корр\. счёт:? ?(?:[,;.]|$)",
     "пустой «Корр. счёт» напечатан без значения"),
    (r"Срок действия:? до ?(?:[,;.]|$)",
     "пустой «Срок действия ВУ» напечатан без значения"),
    (r"^Погрузка \d+: ?$", "точка маршрута напечатана без адреса"),
    (r"^Выгрузка \d+: ?$", "точка маршрута напечатана без адреса"),
]

#: Сколько цифр подряд считается персональными данными и обезличивается.
DIGIT_RUN = re.compile(r"\d{6,}")

#: Признаки вида лица стороны. Оба сразу в одном наименовании — дефект:
#: «Индивидуальный предприниматель Общество с ограниченной
#: ответственностью «Ромашка»» (вид стороны разошёлся с наименованием).
CARRIER_IP_PREFIX = re.compile(
    r"Индивидуальн(?:ый|ого)\s+предпринимател|(?:^|\s)ИП(?:\s|$)"
)
OOO_PREFIX = re.compile(r"Обществ[ао]\s+с\s+ограниченной\s+ответственность?ю")


# ─────────────────────────────────────────────────────────────
# Обезличивание фрагментов
# ─────────────────────────────────────────────────────────────

def redact(text: str, limit: int = 90) -> str:
    """
    Фрагмент для отчёта: длинные числа скрыты.

    Серия и номер паспорта, счёт, БИК и VIN — это персональные данные. В
    problems.json они не нужны: вид дефекта и так понятен по сообщению, а
    сам фрагмент нужен лишь для того, чтобы найти место в документе. Поэтому
    все последовательности от шести цифр заменяются на «<цифры: N>».
    """
    collapsed = re.sub(r"\s+", " ", str(text)).strip()
    collapsed = DIGIT_RUN.sub(lambda m: f"<цифры: {len(m.group())}>", collapsed)
    return collapsed[:limit]


# ─────────────────────────────────────────────────────────────
# Сбор текста документа
# ─────────────────────────────────────────────────────────────

def document_units(path: Path) -> List[Dict[str, Any]]:
    """
    Все текстовые единицы документа: абзацы тела и абзацы внутри таблиц.

    Возвращает [{"kind", "index", "text", "table", "row", "cell"}] — по
    одной записи на абзац. Порядок — как в документе: так «строка» проблемы
    в отчёте совпадает с порядком чтения.
    """
    from docx import Document

    doc = Document(str(path))
    units: List[Dict[str, Any]] = []

    for index, paragraph in enumerate(doc.paragraphs):
        units.append({"kind": "p", "index": index, "text": paragraph.text,
                      "table": None, "row": None, "cell": None})

    for t_index, table in enumerate(doc.tables):
        for r_index, row in enumerate(table.rows):
            for c_index, cell in enumerate(row.cells):
                for paragraph in cell.paragraphs:
                    units.append({
                        "kind": "cell", "index": len(units),
                        "text": paragraph.text,
                        "table": t_index, "row": r_index, "cell": c_index,
                    })
    return units


def full_text(units: List[Dict[str, Any]]) -> str:
    return "\n".join(unit["text"] for unit in units)


# ─────────────────────────────────────────────────────────────
# Проверки уровня отдельного абзаца
# ─────────────────────────────────────────────────────────────

def _problem(unit: Dict[str, Any], problem: str, severity: str,
             where: str = "") -> Dict[str, Any]:
    """Запись о дефекте: место в документе, вид, важность, обезличенный фрагмент."""
    return {
        "kind": unit["kind"],
        "line": unit["index"],
        "table": unit["table"],
        "row": unit["row"],
        "cell": unit["cell"],
        "where": where,
        "problem": problem,
        "severity": severity,
        "fragment": redact(unit["text"]),
    }


def build_vocabulary(units: List[Dict[str, Any]], skip: int = -1) -> set:
    """
    Словарь слов документа — эталон для проверки «слипшихся» слов.

    Идея проверки: слово вроде «всоответствии» не существует, но его часть
    «соответствии» в документе ЕСТЬ (в других абзацах). Значит, пробел
    потерян. Словарь строится по самому документу, поэтому проверка не
    зависит от внешнего словаря русского языка и не требует зависимостей.

    :param skip: индекс абзаца, который в словарь НЕ попадает. Проверяемый
        абзац исключается обязательно: иначе испорченное слово попадёт в
        словарь (оно же есть в тексте) и проверка сама себя выключит.
    """
    vocabulary = set()
    for index, unit in enumerate(units):
        if index == skip:
            continue
        for word in re.findall(r"[а-яё]{3,}", unit["text"].lower()):
            vocabulary.add(word)
    return vocabulary


def check_glued_words(unit: Dict[str, Any], vocabulary: set) -> List[Dict[str, Any]]:
    """
    Ищет слова, у которых потерян пробел после предлога или союза.

    Разбор: слово начинается с одного из GLUE_PREFIXES, а его остаток —
    слово, которое в документе встречается отдельно. Слово целиком при этом
    отдельно не встречается (иначе это нормальное слово вроде
    «соответствии», и разбор был бы ложным).

    Проверка заведомо неполная — она не найдёт «пропущен пробел» там, где
    остаток тоже нигде не встречается отдельно. Зато найденное — настоящий
    дефект, а не догадка.
    """
    found: List[Dict[str, Any]] = []
    for raw in re.findall(r"[А-Яа-яЁё]{9,}", unit["text"]):
        word = raw.lower()
        if word in GLUE_FALSE_WORDS or word in vocabulary:
            continue
        for prefix in GLUE_PREFIXES:
            if not word.startswith(prefix) or len(word) - len(prefix) < 5:
                continue
            remainder = word[len(prefix):]
            if remainder in vocabulary or remainder in GLUE_FALSE_WORDS:
                found.append(_problem(
                    unit,
                    f"пропущен пробел после «{prefix}»: "
                    f"«{redact(raw, 40)}»",
                    "medium",
                ))
                break
        if found and found[-1]["line"] == unit["index"]:
            break
    return found


def check_unit(unit: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Проверки одного абзаца: плейсхолдеры, мусор, пробелы, формат дат."""
    text = unit["text"]
    found: List[Dict[str, Any]] = []
    if not text.strip():
        return found

    # ── а) незаполненные плейсхолдеры ──
    for marker in PLACEHOLDER_MARKERS:
        if marker in text:
            found.append(_problem(
                unit, f"остался плейсхолдер шаблона «{marker}»", "high",
            ))
            break

    # ── б) значения-заглушки вместо данных ──
    lowered = text.lower()
    for junk in JUNK_VALUES:
        if re.search(rf"(?<![а-яёa-z]){junk}(?![а-яёa-z])", lowered):
            found.append(_problem(
                unit, f"в документе напечатано значение-заглушка «{junk}»",
                "high",
            ))

    # ── в) двойные пробелы ──
    # Отдельно от «г») — частая косметика шаблона, поэтому уровень low.
    doubles = len(re.findall(r"[ \t\u00a0]{2,}", text))
    if doubles:
        found.append(_problem(
            unit, f"двойных пробелов: {doubles}", "low",
        ))

    # ── г) пробел перед знаком препинания ──
    for match in SPACE_BEFORE_PUNCT.finditer(text):
        found.append(_problem(
            unit,
            f"пробел перед знаком препинания «{match.group(1)}»", "medium",
        ))
        break

    # ── д) слипшиеся слова проверяются отдельно: нужен словарь документа
    # (см. check_glued_words) ──

    # ── з) дата не в том формате ──
    for match in ISO_DATE.finditer(text):
        found.append(_problem(
            unit, f"дата в ISO-формате «{match.group()}» (нужно ДД.ММ.ГГГГ)",
            "medium",
        ))
        break

    # ── з) дата со слэшами или дефисами ──
    if re.search(r"\b\d{2}[/-]\d{2}[/-]\d{4}\b", text):
        found.append(_problem(
            unit, "дата напечатана через «/» или «-» вместо точек", "medium",
        ))

    # ── м) двойной префикс города и «д. д.» ──
    for match in re.finditer(r"\bг\.\s*([А-ЯЁ][а-яё\-]+)\s*,?\s*г\.\s*\1\b", text):
        found.append(_problem(
            unit, f"двойной префикс города: «г. {match.group(1)}, г. {match.group(1)}»",
            "medium",
        ))
        break
    for match in re.finditer(r"\bд\.\s*д\.\s*\d", text):
        found.append(_problem(unit, "двойной префикс дома: «д. д.»", "medium"))
        break

    # ── пустые необязательные поля ──
    for pattern, message in EMPTY_LABEL_PATTERNS:
        if re.search(pattern, text):
            found.append(_problem(unit, message, "medium"))
            break

    # ── вид лица стороны: ИП и ООО в одном наименовании ──
    # «Индивидуальный предприниматель Общество с ограниченной
    # ответственностью «Ромашка»» — сторона названа двумя видами лица
    # сразу. Так выглядит расхождение вида стороны с её наименованием
    # (у ИП в full_name лежит название организации или наоборот).
    if CARRIER_IP_PREFIX.search(text) and OOO_PREFIX.search(text):
        found.append(_problem(
            unit,
            "наименование стороны содержит и «Индивидуальный предприниматель», "
            "и «Общество с ограниченной ответственностью»",
            "high",
        ))

    # ── ФИО: дубль отчества в женском ФИО — типичная ошибка склейки ──
    for match in re.finditer(r"\b([А-ЯЁ][а-яё]+(?:овна|евна|ична|инична))\s+\1\b",
                             text):
        found.append(_problem(
            unit, f"отчество напечатано дважды: «{redact(match.group(), 40)}»",
            "medium",
        ))
        break

    return found


# ─────────────────────────────────────────────────────────────
# Проверки уровня всего документа
# ─────────────────────────────────────────────────────────────

def _amount_bad_pairs(text: str) -> List[str]:
    """
    Сверяет числа и суммы прописью рядом с ними.

    «180300.00 руб. (Сто восемьдесят тысяч триста рублей 00 копеек)» —
    число и пропись обязаны говорить об одном. Расхождение означает, что
    генератор посчитал одно, а напечатал другое.
    """
    problems = []
    for match in MONEY_PAIR.finditer(text):
        number_text, words = match.group(1), match.group(2)
        try:
            number = float(number_text.replace(" ", "").replace("\u00a0", ""))
        except ValueError:
            continue
        expected = amount_to_words(number)
        # Сравниваем без регистра и лишних пробелов: регистр первой буквы
        # зависит от места в предложении.
        if re.sub(r"\s+", " ", words).strip().lower() != \
                re.sub(r"\s+", " ", expected).strip().lower():
            problems.append(
                f"сумма прописью не совпадает с числом: {redact(number_text, 20)} "
                f"→ напечатано «{redact(words, 60)}», ожидалось "
                f"«{redact(expected, 60)}»"
            )
    return problems


def _fio_genitive_problems(text: str, payload: Dict[str, Any]) -> List[str]:
    """
    ФИО в падежах: «в лице X Y Z» — фамилия и отчество в родительном.

    Проверка не выдумывает падеж заново (для этого есть
    `core.contracts.ru_morphology`), а сверяет документ с ТЕМ ЖЕ модулем:
    если генератор и морфология разойдутся — это и есть дефект. Отдельно
    ловятся явные ошибки вроде «Ивановы Марии» (женская фамилия мужского
    рода и наоборот).
    """
    problems = []
    driver = payload.get("driver") or {}
    full_name = str(driver.get("full_name") or "").strip()
    if not full_name:
        return problems

    # ФИО водителя печатается в именительном падеже (карточка «Информация об
    # исполнителе»): родительный там был бы ошибкой.
    if full_name in text:
        return problems

    # Если фамилия вообще не встречается — значит, документ напечатал ФИО в
    # другом падеже; проверяем, что это родительный, а не мусор.
    from core.contracts.ru_morphology import detect_gender, genitive_fio

    expected = genitive_fio(full_name, detect_gender(full_name))
    if expected and expected != full_name and expected in text:
        return problems
    problems.append(
        "ФИО водителя не найдено в документе ни в именительном, ни в "
        "родительном падеже"
    )
    return problems


def _fio_genitive_party_problems(text: str, party: Dict[str, Any],
                                 label: str) -> List[str]:
    """
    ФИО подписанта стороны в родительном падеже («в лице … Иванова И.И.»).

    У ИП подписант — он сам, и падеж не меняется: «Индивидуальный
    предприниматель Иванов Иван Иванович, действующий…». Для ООО фамилия
    директора обязана стоять в родительном падеже.
    """
    from core.contracts.ru_morphology import detect_gender, genitive_fio

    is_ip = (str(party.get("entity_type") or "").upper().startswith("ИП")
             or str(party.get("full_name") or "").lower().startswith(
                 ("индивидуальный предприниматель", "ип ")))
    if is_ip:
        return []

    director = str(party.get("director_name") or "").strip()
    if not director:
        return []

    plain = director.split()[-1] if len(director.split()) >= 2 else director
    genitive = genitive_fio(director, detect_gender(director))
    if genitive in text:
        return []

    # Фамилия в родительном падеже в тексте есть, но собрана иначе —
    # собираем ожидаемую фамилию в родительном падеже и ищем её.
    expected_surname = genitive.split()[0] if genitive else ""
    if expected_surname and expected_surname in text:
        return []
    if plain and plain in text:
        # Фамилия в исходном виде: значит, падеж НЕ применён.
        return [f"{label}: ФИО подписанта напечатано в именительном падеже "
                f"вместо родительного"]
    return [f"{label}: ФИО подписанта не найдено в документе"]


def _check_identifiers(unit: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Длины и формы реквизитов в ОДНОМ абзаце: ИНН, КПП, ОГРН, БИК, счета,
    паспорт, ВУ, код подразделения, VIN.

    Проверяется абзац, а не весь документ: в склейке абзацев границы слов
    теряются, и проверка начинала «находить» VIN в строке счетов, а
    телефон — в серии паспорта.
    """
    text = unit["text"]
    found: List[Dict[str, Any]] = []

    def add(message: str, severity: str = "medium") -> None:
        found.append(_problem(unit, message, severity))

    # ── VIN: ровно 17 знаков, без I, O и Q ──
    # Кандидат обязан содержать и буквы, и цифры: у чисто цифровых
    # реквизитов (счёт, ОГРН) проверять VIN нечего.
    for token in VIN_CANDIDATE.findall(text):
        if re.search(r"[IOQ]", token):
            add("VIN содержит недопустимые буквы I, O или Q")
        elif not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", token):
            add("VIN содержит символы вне стандарта")
    # Отдельно — VIN, набранный строчными (OCR иногда так его отдаёт).
    for token in re.findall(r"(?<![A-Za-z0-9])[a-z0-9]{17}(?![A-Za-z0-9])",
                            text):
        if re.search(r"[a-z]", token) and re.search(r"\d", token):
            add("VIN набран строчными буквами")

    for match in re.finditer(r"ОГРНИП\s+(\d+)", text):
        if len(match.group(1)) != 15:
            add(f"ОГРНИП неверной длины: {len(match.group(1))} вместо 15")
    for match in re.finditer(r"ОГРН\s+(\d+)", text):
        if len(match.group(1)) != 13:
            add(f"ОГРН неверной длины: {len(match.group(1))} вместо 13")
    for match in re.finditer(r"ИНН\s+(\d+)", text):
        if len(match.group(1)) not in (10, 12):
            add(f"ИНН неверной длины: {len(match.group(1))} вместо 10 или 12")
    for match in re.finditer(r"КПП\s+(\d+)", text):
        if len(match.group(1)) != 9:
            add(f"КПП неверной длины: {len(match.group(1))} вместо 9")
    for match in re.finditer(r"БИК\s+(\d+)", text):
        if len(match.group(1)) != 9:
            add(f"БИК неверной длины: {len(match.group(1))} вместо 9")
    for match in re.finditer(r"(?:р/с|к/с)\s*:?\s*(\d+)", text):
        if len(match.group(1)) != 20:
            add(f"счёт неверной длины: {len(match.group(1))} вместо 20")
    for match in re.finditer(r"Паспорт:\s*([\d\s]+)", text):
        digits = re.sub(r"\D", "", match.group(1))
        if len(digits) != 10:
            add(f"серия и номер паспорта: {len(digits)} цифр вместо 10")
    for match in re.finditer(r"ВУ:?\s*([\d\s]{9,})", text):
        digits = re.sub(r"\D", "", match.group(1))
        if digits and len(digits) != 10:
            add(f"серия и номер ВУ: {len(digits)} цифр вместо 10")
    for match in re.finditer(r"Код подразделения:?\s*([\d\s\-–]+)", text):
        digits = re.sub(r"\D", "", match.group(1))
        if digits and len(digits) != 6:
            add(f"код подразделения: {len(digits)} цифр вместо 6")
    return found


def _check_phones(unit: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Телефоны — в едином формате «+7 (XXX) XXX-XX-XX».

    Кандидат обязан начинаться с «+7» или «8» и иметь 10–11 цифр: без этого
    проверка принимала за телефон серию паспорта и номер счёта.
    """
    found: List[Dict[str, Any]] = []
    for match in PHONE_CANDIDATE.finditer(unit["text"]):
        value = match.group().strip()
        digits = re.sub(r"\D", "", value)
        if len(digits) not in (10, 11):
            continue
        if not PHONE_GOOD.match(value):
            found.append(_problem(
                unit, f"телефон не в едином формате: {redact(value, 30)}",
                "medium",
            ))
    return found


def check_document(path: Path, payload: Dict[str, Any],
                   units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Все проверки одного документа: по абзацам и по тексту целиком."""
    problems: List[Dict[str, Any]] = []
    for index, unit in enumerate(units):
        problems.extend(check_unit(unit))
        problems.extend(check_glued_words(unit, build_vocabulary(units, skip=index)))
        problems.extend(_check_identifiers(unit))
        problems.extend(_check_phones(unit))

    text = full_text(units)
    whole = {"kind": "doc", "index": -1, "table": None, "row": None,
             "cell": None, "text": text}

    for message in _amount_bad_pairs(text):
        problems.append(_problem(whole, message, "high"))

    for message in _fio_genitive_problems(text, payload):
        problems.append(_problem(whole, message, "high"))
    for label, key in (("Заказчик", "customer"), ("Перевозчик", "carrier")):
        party = payload.get(key) or {}
        for message in _fio_genitive_party_problems(text, party, label):
            problems.append(_problem(whole, message, "medium"))

    return problems


# ─────────────────────────────────────────────────────────────
# Генерация документов
# ─────────────────────────────────────────────────────────────

def _prepare_templates(work_dir: Path) -> Path:
    """Облегчённые копии шаблонов (без встроенных шрифтов) для проверок."""
    from tools.make_golden import strip_embedded_fonts

    target = work_dir / "templates"
    target.mkdir(parents=True, exist_ok=True)
    for name in TEMPLATE_NAMES:
        strip_embedded_fonts(TEMPLATES_DIR / name, target / name)
    return target


def generate_documents(scenario_dir: Path = SCENARIO_DIR,
                       out_dir: Path = OUT_DIR) -> Dict[str, Any]:
    """
    Прогоняет все сценарии через ContractGenerator.

    Исключения не прерывают прогон: сценарий попадает в errors.log с
    traceback и в счётчик failed. Документ, который не сгенерировался, —
    это и есть результат проверки, а не повод остановиться.
    """
    from core.contract_generator import ContractGenerator

    out_dir.mkdir(parents=True, exist_ok=True)
    templates = _prepare_templates(out_dir)
    generator = ContractGenerator(templates_dir=str(templates))

    scenario_files = sorted(
        (p for p in scenario_dir.glob("*.json") if p.stem != "index"),
        key=lambda p: int(p.stem),
    )

    generated: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    log_lines: List[str] = []

    for scenario_file in scenario_files:
        number = scenario_file.stem
        payload = json.loads(scenario_file.read_text(encoding="utf-8"))
        target = out_dir / f"{number}.docx"
        try:
            generator.generate_docx(payload, str(target))
            generated.append({"scenario": int(number), "file": target.name,
                              "size": target.stat().st_size})
        except Exception as exc:                      # noqa: BLE001
            errors.append({"scenario": int(number), "type": type(exc).__name__,
                           "message": str(exc)[:300]})
            log_lines.append(
                f"=== сценарий {number}: {type(exc).__name__}: {exc}\n"
                + traceback.format_exc()
            )
            target.unlink(missing_ok=True)

    (out_dir / "errors.log").write_text("\n".join(log_lines), encoding="utf-8")
    result = {
        "scenarios": len(scenario_files),
        "generated": len(generated),
        "failed": len(errors),
        "errors": errors,
    }
    (out_dir / "generate.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


# ─────────────────────────────────────────────────────────────
# Проверка документов
# ─────────────────────────────────────────────────────────────

def check_documents(scenario_dir: Path = SCENARIO_DIR,
                    out_dir: Path = OUT_DIR) -> Dict[str, Any]:
    """Проверяет все сгенерированные документы и собирает problems.json."""
    problems: List[Dict[str, Any]] = []
    checked = 0
    empty_documents: List[int] = []

    docs = sorted((p for p in out_dir.glob("*.docx")),
                  key=lambda p: int(p.stem))
    for docx in docs:
        number = docx.stem
        scenario_file = scenario_dir / f"{number}.json"
        payload = (json.loads(scenario_file.read_text(encoding="utf-8"))
                   if scenario_file.exists() else {})

        units = document_units(docx)
        if not full_text(units).strip():
            empty_documents.append(int(number))
            continue

        checked += 1
        for problem in check_document(docx, payload, units):
            problem["scenario"] = int(number)
            problem["file"] = docx.name
            problems.append(problem)

    problems.sort(key=lambda p: (p["scenario"], SEVERITY_ORDER[p["severity"]],
                                 p["line"]))

    by_severity = Counter(p["severity"] for p in problems)
    by_problem = Counter(p["problem"] for p in problems)
    by_scenario = Counter(p["scenario"] for p in problems)
    documents_with_high = sorted({
        p["scenario"] for p in problems if p["severity"] == "high"
    })

    summary = {
        "documents_checked": checked,
        "documents_empty": empty_documents,
        "problems_total": len(problems),
        "by_severity": dict(by_severity),
        "top_problems": by_problem.most_common(10),
        "worst_scenarios": by_scenario.most_common(10),
        "documents_with_high": documents_with_high,
    }

    (out_dir / "problems.json").write_text(
        json.dumps(problems, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


# ─────────────────────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(
        description="Стресс-проверка генерации DOCX (БЛОК 1)"
    )
    parser.add_argument("mode", choices=("generate", "check", "all"))
    parser.add_argument("--scenarios", default=str(SCENARIO_DIR))
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument("--clean", action="store_true",
                        help="удалить папку вывода перед прогоном")
    args = parser.parse_args(argv)

    scenario_dir = Path(args.scenarios)
    out_dir = Path(args.out)
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not scenario_dir.exists():
        print(f"Нет папки сценариев: {scenario_dir}")
        print("Сначала: python tools/make_test_scenarios.py")
        return 2

    if args.mode in ("generate", "all"):
        result = generate_documents(scenario_dir, out_dir)
        print(f"Сценариев: {result['scenarios']}, "
              f"документов: {result['generated']}, "
              f"ошибок: {result['failed']}")
        for error in result["errors"][:5]:
            print(f"  сценарий {error['scenario']}: "
                  f"{error['type']}: {error['message'][:120]}")

    if args.mode in ("check", "all"):
        summary = check_documents(scenario_dir, out_dir)
        print(f"Проверено документов: {summary['documents_checked']}")
        print(f"Проблем: {summary['problems_total']} "
              f"{summary['by_severity']}")
        print("Топ проблем:")
        for problem, count in summary["top_problems"]:
            print(f"  {count:4d}  {problem}")
        if summary["documents_with_high"]:
            print(f"Документов с high: {len(summary['documents_with_high'])}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
