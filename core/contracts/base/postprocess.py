#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Постобработка готового документа: `PostprocessStep` и штатные шаги.

Конвейер шагов — точка расширения постобработки: тип договора объявляет
свой список в `postprocess_steps()`, не дописывая чужой код. Каждый шаг
получает генератор (для его документных утилит) и тестируется отдельно на
пустом `Document()` — без тяжёлого шаблона.

Переехало из `core/contracts/base_generator.py` (Этап 4, 16.10.2026) без
изменения логики. `BaseContractGenerator` держит на функции тонкие
делегаты, поэтому подклассы, вызывающие `self._convert_newlines_to_breaks`
или `self._normalize_document_spaces`, работают без правок.

Цикл с `generator.py` разорван строковой аннотацией: здесь нет импорта
базового генератора даже под `TYPE_CHECKING`.
"""

import logging
import re
from abc import ABC, abstractmethod
from typing import ClassVar

from core.contracts.base.formatting import Formatting, W_BR_TAG

logger = logging.getLogger("core.contract_generator")


class PostprocessStep(ABC):
    """
    Шаг постобработки документа (Шаг 5 рефакторинга).

    Конвейер шагов — точка расширения постобработки: тип договора
    объявляет свой список в postprocess_steps(), не дописывая чужой код.
    Каждый шаг получает генератор (для его документных утилит) и
    тестируется отдельно на пустом Document() — без тяжёлого шаблона.
    """

    #: Короткое имя шага (для логов и диагностики).
    name: ClassVar[str] = ""

    def __init__(self, generator: "BaseContractGenerator"):
        self.generator = generator

    @abstractmethod
    def apply(self, doc, data) -> None:
        """Применяет шаг к открытому документу."""


class ConvertNewlinesStep(PostprocessStep):
    """«\\n» внутри runs → разрывы строк Word."""

    name = "convert_newlines"

    def apply(self, doc, data) -> None:
        self.generator._convert_newlines_to_breaks(doc)


class NormalizeSpacesStep(PostprocessStep):
    """
    Косметика пробелов в готовом документе (БЛОК 1 стресс-теста, пункты «в», «г»).

    В бланках и в собранных значениях встречаются два дефекта вёрстки:

      * двойной пробел — «Заказчику  убытки», «и мессенджерам.  Такая»;
      * пробел перед знаком препинания — «при условии , что».

    Оба видны в напечатанном договоре и оба — чистая косметика: смысл текста
    не меняется. Чинятся ОДНИМ проходом по готовому документу, а не правкой
    бланков: бланки `templates/*.docx` лежат под запретом (AGENTS.md § 4), а
    дефект живёт и в них, и в значениях, приходящих из справочника.
    Соответственно и правка здесь одна на все типы договоров — в базовом
    классе, а не в каждом генераторе.

    Чего шаг НЕ делает: не трогает переводы строк (в колонтитулах и таблицах
    они разделяют абзацы), не убирает одиночные пробелы в начале абзаца
    (это отступ в списках бланка) и не пытается «починить» текст по смыслу.
    """

    name = "normalize_spaces"

    def apply(self, doc, data) -> None:
        self.generator._normalize_document_spaces(doc)


# ─────────────────────────────────────────────────────────
# Функции шагов (то, что раньше было методами генератора)
# ─────────────────────────────────────────────────────────

def normalize_spaces(text: str) -> str:
    """
    Двойные пробелы — в один, пробел перед знаком препинания — убран.

    Функция чистая: ни документа, ни данных не касается, поэтому её
    поведение проверяется тестом без сборки договора.

    Табуляции и неразрывный пробел приводятся к обычному пробелу: в
    договоре они не несут смысла, а в проверке «двойных пробелов»
    выглядят как дефект.
    """
    if not text:
        return text
    result = text.replace("\t", " ").replace("\u00a0", " ")
    result = re.sub(r" {2,}", " ", result)
    # Пробел перед «, . ; : ! ?» — ошибка. Перед закрывающей скобкой и
    # кавычкой тоже: «(подпись) », «Договор ».
    result = re.sub(r"\s+([,.;:!?»)\u201d])", r"\1", result)
    # Пробел СРАЗУ после открывающей скобки или кавычки — тоже ошибка.
    result = re.sub(r"([«(\u201c])\s+", r"\1", result)
    return result


def set_paragraph_text(paragraph, text: str) -> None:
    """
    Заменяет текст абзаца, сохраняя оформление первого run'а.

    Оформление остальных run'ов теряется — но правка касается ТОЛЬКО
    пробелов, а значит, менять его нечем: если пробелов в абзаце не было,
    текст возвращается тем же, и мы выходим, ничего не тронув. Пустой
    абзац не трогаем вовсе — в нём нет run'ов, и вставлять их незачем.

    Абзац с разрывом строки (<w:br/>) не трогается: `paragraph.text`
    склеивает строки ВОКРУГ разрыва, и запись этого текста в первый run
    стёрла бы сам разрыв. Пробел на стыке строк дефектом не является,
    поэтому терять структуру ради него нельзя.
    """
    if paragraph.text == text or not paragraph.runs:
        return
    if paragraph._p.findall(f".//{W_BR_TAG}"):
        return
    paragraph.runs[0].text = text
    for run in paragraph.runs[1:]:
        run.text = ""


def normalize_document_spaces(doc) -> None:
    """
    Прогоняет косметику пробелов по всему документу.

    Абзац правится целиком, а не по run'ам: run — это кусок текста с
    одинаковым оформлением, и пробел перед запятой легко оказывается на
    стыке двух run'ов («…Заказчику  убытки» одним, «при условии ,» —
    другим). Текст абзаца при этом остаётся ЛИТЕРАЛЬНЫМ: то, что было
    текстом, текстом и остаётся, поэтому риск что-то испортить в XML
    минимален.

    Абзацы с разрывом строки (<w:br/>) пропускаются: в них пробел перед
    разрывом может нести смысл (висячий отступ списка), а «двойной
    пробел» на стыке строк — не дефект.
    """
    for paragraph in Formatting.iter_paragraphs(doc):
        if paragraph._p.findall(W_BR_TAG):
            continue
        set_paragraph_text(paragraph, normalize_spaces(paragraph.text))


def convert_newlines_to_breaks(doc) -> None:
    """Превращает «\\n» внутри runs в разрывы строк Word."""
    converted = 0
    for paragraph in Formatting.iter_paragraphs(doc):
        for run in list(paragraph.runs):
            if "\n" not in run.text:
                continue

            parts = run.text.split("\n")
            run.text = parts[0]
            for part in parts[1:]:
                run.add_break()
                run.add_text(part)
            converted += 1

    if converted:
        logger.debug(f"Разрывы строк добавлены в {converted} runs")
