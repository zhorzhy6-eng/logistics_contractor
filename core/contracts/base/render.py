#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Движок рендера шаблонов: docxtpl + откат на ручную замену.

Основной путь — docxtpl (Jinja2): он подставляет значения прямо в XML,
поэтому сохраняется форматирование runs (жирный, курсив), корректно
обрабатываются таблицы, вложенные таблицы и колонтитулы. Прежняя ручная
замена — резерв: она включается, если docxtpl недоступен или не смог
обработать конкретный шаблон, — генерация договора не должна падать
из-за движка.

Переехало из `core/contracts/base_generator.py` (Этап 4, 16.10.2026) без
изменения логики. Методы статические: документ и замены приходят
аргументами, своего состояния у рендера нет. `BaseContractGenerator`
держит на них тонкие делегаты, поэтому подклассы работают без правок.

Логгер — прежнее имя `core.contract_generator`: тесты ловят сообщения
генерации через `caplog` по этому имени, и переезд кода не должен их
разорвать.
"""

import logging
import re
from typing import Any, Dict

from core.contracts.base.formatting import Formatting

logger = logging.getLogger("core.contract_generator")


class TemplateRenderer:
    """Рендер бланка: docxtpl, ручная замена и вставка блоков по меткам."""

    # ─────────────────────────────────────────────────────────
    # Рендер шаблона
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def render_docxtpl(
        template_path: str,
        replacements: Dict[str, str],
        output_path: str,
    ) -> str:
        """
        Рендерит шаблон через docxtpl.

        autoescape=True обязателен: без него docxtpl вставляет значения как
        XML, и символы «&» и «<» из реквизитов («ООО «Ромашка & Ко»») ломают
        документ. Если когда-нибудь понадобится вставлять готовую разметку,
        для конкретного значения нужно использовать фильтр |safe.

        :raises Exception: любую ошибку docxtpl — решение об откате
            принимает вызывающий (`render`), здесь оно не прячется.
        """
        from docxtpl import DocxTemplate

        template = DocxTemplate(template_path)
        template.render(replacements, autoescape=True)
        template.save(output_path)
        logger.info(
            f"Шаблон отрендерен через docxtpl ({len(replacements)} переменных)"
        )
        return "docxtpl"

    @staticmethod
    def render_manual(
        template_path: str,
        replacements: Dict[str, str],
        output_path: str,
    ) -> str:
        """
        [DEPRECATED] Ручная замена {{переменных}} — резервный путь рендера.

        Используется только при недоступности docxtpl. Помечен как устаревший:
        при первой же возможности шаблон должен рендериться через docxtpl.
        """
        from docx import Document

        doc = Document(template_path)
        TemplateRenderer.replace_in_document(doc, replacements)
        doc.save(output_path)
        logger.info("Шаблон отрендерен ручной заменой плейсхолдеров")
        return "manual"

    # ─────────────────────────────────────────────────────────
    # Замена плейсхолдеров (резервный путь)
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def replace_in_document(doc, replacements: Dict[str, str]) -> None:
        for paragraph in Formatting.iter_paragraphs(doc):
            TemplateRenderer.replace_in_paragraph(paragraph, replacements)

        logger.info(f"Замены в документе выполнены ({len(replacements)} плейсхолдеров)")

    @staticmethod
    def replace_in_paragraph(paragraph, replacements: Dict[str, str]) -> None:
        """
        [DEPRECATED] Ручная замена плейсхолдеров в одном абзаце.

        Оставлен только для резервного пути render_manual: он включается,
        если docxtpl недоступен или не смог обработать конкретный шаблон.
        Основной рендер — docxtpl (см. render_docxtpl): он подставляет
        значения в XML и сохраняет форматирование runs.

        Новый код должен использовать docxtpl, а не этот метод: он сваливает
        весь текст абзаца в первый run и теряет жирный/курсив.
        """
        full_text = paragraph.text

        for var_name, value in replacements.items():
            pattern = r"\{\{\s*" + re.escape(var_name) + r"\s*\}\}"
            full_text = re.sub(pattern, str(value), full_text)

        if full_text == paragraph.text:
            return

        if paragraph.runs:
            base_run = paragraph.runs[0]
            base_run.text = ""
            for run in paragraph.runs[1:]:
                run.text = ""
        else:
            base_run = paragraph.add_run("")

        lines = full_text.split("\n")

        base_run.text = lines[0]
        for line in lines[1:]:
            base_run.add_break()
            base_run.add_text(line)

    # ─────────────────────────────────────────────────────────
    # Работа с абзацами-метками (вставка блоков в середину документа)
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def find_marker_paragraph(doc, marker_text: str):
        """
        Абзац, текст которого равен метке (с точностью до пробелов).

        Абзацы внутри таблиц и колонтитулов тоже просматриваются: метку
        можно поставить в любом месте документа.
        """
        for paragraph in Formatting.iter_paragraphs(doc):
            if paragraph.text.strip() == marker_text:
                return paragraph
        return None

    @staticmethod
    def outline_paragraph(paragraph):
        """
        «Образец» форматирования абзаца-заголовка — сам абзац с меткой.

        Копируем pPr (отступы, выравнивание, стиль, интервалы) и один run
        как образец шрифта: заголовки погрузок должны выглядеть как
        остальной текст документа.
        """
        from copy import deepcopy

        props = paragraph._p.find(Formatting.qname("pPr"))
        sample_run = paragraph.runs[0]._r if paragraph.runs else None

        return (
            deepcopy(props) if props is not None else None,
            deepcopy(sample_run) if sample_run is not None else None,
        )

    @staticmethod
    def make_point_heading(doc, head_template: Any, text: str):
        """Абзац-заголовок «Погрузка N: адрес» — жирный, вне таблицы."""
        props, sample_run = head_template

        paragraph = doc.add_paragraph()
        if props is not None:
            paragraph._p.insert(0, props)

        run = paragraph.add_run(text)
        Formatting.copy_run_format(sample_run, run)

        # Заголовок всегда жирный, даже если образец был обычным.
        run.bold = True
        if run.font.size is None and sample_run is None:
            run.font.name = "Times New Roman"

        return paragraph._p

    @staticmethod
    def make_spacer(doc):
        """Пустой абзац-отступ после таблицы."""
        paragraph = doc.add_paragraph()
        return paragraph._p
