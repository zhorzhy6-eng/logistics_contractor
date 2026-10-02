#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Базовый генератор DOCX-договоров (Шаг 1 рефакторинга архитектуры контрактов).

Здесь живёт всё, что не зависит от конкретного типа договора (категория A):

  * оркестрация: generate() → generate_docx() → рендер → постобработка;
  * движок рендера: docxtpl с fallback на ручную замену плейсхолдеров
    (оба пути работают для любого DOCX-типа);
  * форматирование: копирование стилей абзацев/runs/ячеек, ширины колонок;
  * работа с датами, суммы прописью, склонение дней, сокращение ФИО;
  * обход и правка документа: абзацы, таблицы, колонтитулы.

Методы, специфичные для договора-заявки на перевозку (категория B),
живут в подклассе core/contracts/perevozka/generator.py. Имена, которые
оркестрация вызывает у подкласса (_get_template_path,
_build_replacements_map, _insert_route_tables, _remove_empty_vehicle_rows),
объявлены ниже как заглушки с NotImplementedError: так контракт виден
сразу, а прямые пользователи базы получают понятную ошибку.

Логгер намеренно называется «core.contract_generator»: тесты
(tests/test_contract_generator.py) ловят сообщения генерации через
caplog по этому имени, и переезд кода не должен их разорвать.
"""

import logging
import os
import re
from abc import ABC
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional

from core.contract_data import ContractData
from core.num_to_words import amount_to_words
from core.trace import trace

logger = logging.getLogger("core.contract_generator")


class BaseContractGenerator(ABC):
    """Общая часть генераторов DOCX-договоров (движок, утилиты, оркестрация)."""

    # ── Контракт типа ──
    #: Идентификатор типа договора (значение core.contracts.ContractType).
    CONTRACT_TYPE: ClassVar[str] = ""

    #: Имена файлов шаблонов: {ключ: имя файла в templates_dir}.
    #: Подкласс перевозки задаёт {"ООО": "shablon_ooo.docx", ...}.
    TEMPLATE_NAMES: ClassVar[Mapping[str, str]] = {}

    # Папка по умолчанию для готовых договоров (Шаг 6 оптимизации).
    # Раньше файлы по 2,7 МБ падали в корень проекта и мешались с кодом.
    DEFAULT_OUTPUT_DIRNAME = "output"

    #: Плейсхолдеры, у которых перенос строки несёт смысл. Подкласс
    #: перевозки перекрывает: ("loading_block", "unloading_block").
    MULTILINE_PLACEHOLDERS: ClassVar[tuple] = ()

    def __init__(self, templates_dir: str):
        self.templates_dir = templates_dir

        self.templates = {
            key: os.path.join(templates_dir, filename)
            for key, filename in self.TEMPLATE_NAMES.items()
        }

        logger.info(f"ContractGenerator инициализирован: templates_dir={templates_dir}")
        for key, path in self.templates.items():
            if os.path.exists(path):
                logger.info(f"  ✓ Шаблон [{key}]: {path}")
            else:
                logger.warning(f"  ✗ Шаблон [{key}] НЕ НАЙДЕН: {path}")

    @classmethod
    def default_output_dir(cls) -> str:
        """
        Папка для готовых договоров: <корень проекта>/output.

        Корень считается от местоположения этого файла
        (core/contracts/base_generator.py): parents[2] — корень проекта.
        Старая формула из core/contract_generator.py (два dirname) здесь
        дала бы core/, а не корень.
        """
        return str(Path(__file__).resolve().parents[2] / cls.DEFAULT_OUTPUT_DIRNAME)

    # ─────────────────────────────────────────────────────────
    # Точки расширения, которые обязан реализовать подкласс
    # ─────────────────────────────────────────────────────────

    def _get_template_path(self, carrier_type: str) -> str:
        """Путь шаблона по типу перевозчика. Реализует подкласс."""
        raise NotImplementedError(
            f"{type(self).__name__} должен реализовать _get_template_path()"
        )

    def _build_replacements_map(self, data: Any) -> Dict[str, str]:
        """Карта замен плейсхолдеров шаблона. Реализует подкласс."""
        raise NotImplementedError(
            f"{type(self).__name__} должен реализовать _build_replacements_map()"
        )

    def _insert_route_tables(self, doc, contract_data: ContractData) -> None:
        """Таблицы маршрута вместо меток. Реализует подкласс."""
        raise NotImplementedError(
            f"{type(self).__name__} должен реализовать _insert_route_tables()"
        )

    def _remove_empty_vehicle_rows(self, doc) -> None:
        """Удаление пустых строк таблицы ТС шаблона. Реализует подкласс."""
        raise NotImplementedError(
            f"{type(self).__name__} должен реализовать _remove_empty_vehicle_rows()"
        )

    # ─────────────────────────────────────────────────────────
    # УНИВЕРСАЛЬНЫЙ МЕТОД ГЕНЕРАЦИИ
    # ─────────────────────────────────────────────────────────

    def generate(self, data: Dict[str, Any], output_dir: Optional[str] = None) -> str:
        """
        Универсальный метод генерации договора.
        Формирует имя файла, вызывает generate_docx() и возвращает путь.

        Принимает ContractData или совместимый dict (см. ContractData.coerce).
        """
        contract_data = ContractData.coerce(data)
        contract = contract_data.contract
        number = (contract.get("number") or "без-номера").strip()

        safe_number = re.sub(r'[^\w\-]+', '-', number)
        if not safe_number:
            safe_number = "без-номера"

        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"Договор-заявка_{safe_number}_{date_str}.docx"

        if output_dir is None:
            # Шаг 6 оптимизации: готовые договоры складываются в output/,
            # а не в корень проекта рядом с исходниками.
            output_dir = self.default_output_dir()

        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, filename)

        logger.info(f"generate(): output_path={output_path}")

        return self.generate_docx(contract_data, output_path)

    # ─────────────────────────────────────────────────────────
    # ГЕНЕРАЦИЯ DOCX
    # ─────────────────────────────────────────────────────────

    @trace
    def generate_docx(self, data: Dict[str, Any], output_path: str) -> str:
        try:
            contract_data = ContractData.coerce(data)
            carrier = contract_data.carrier
            contract = contract_data.contract
            carrier_type = (
                contract.get("carrier_type")
                or carrier.get("carrier_type")
                or "ООО (с НДС)"
            )
            template_path = self._get_template_path(carrier_type)

            if not os.path.exists(template_path):
                raise FileNotFoundError(f"Шаблон не найден: {template_path}")

            logger.info(f"Используем шаблон [{carrier_type}]: {template_path}")

            replacements = self._build_replacements_map(contract_data)
            engine = self._render_template(template_path, replacements, output_path)
            # contract_data нужен постобработке: она собирает таблицы
            # погрузок/выгрузок из точек маршрута и списка машин.
            self._postprocess_document(output_path, contract_data)

            logger.info(f"DOCX сохранён ({engine}): {output_path}")
            return output_path

        except ImportError as e:
            logger.error(f"Не установлена библиотека для генерации DOCX: {e}")
            raise RuntimeError(
                "Для генерации DOCX установите python-docx и docxtpl: "
                "pip install python-docx docxtpl"
            )

    def generate_pdf(self, data: Dict[str, Any], output_path: str) -> str:
        raise RuntimeError("Генерация PDF пока не реализована. Используйте DOCX.")

    # ─────────────────────────────────────────────────────────
    # РЕНДЕР ШАБЛОНА
    # ─────────────────────────────────────────────────────────

    def _render_template(
        self,
        template_path: str,
        replacements: Dict[str, str],
        output_path: str,
    ) -> str:
        """
        Рендерит шаблон и возвращает имя использованного движка.

        Основной путь — docxtpl (Jinja2): он подставляет значения прямо в XML,
        поэтому сохраняется форматирование runs (жирный, курсив), корректно
        обрабатываются таблицы, вложенные таблицы и колонтитулы.
        Прежняя ручная замена оставлена резервом: она включается, если docxtpl
        недоступен или не смог обработать конкретный шаблон, — генерация
        договора не должна падать из-за движка.

        autoescape=True обязателен: без него docxtpl вставляет значения как
        XML, и символы «&» и «<» из реквизитов («ООО «Ромашка & Ко»») ломают
        документ. Если когда-нибудь понадобится вставлять готовую разметку,
        для конкретного значения нужно использовать фильтр |safe.
        """
        try:
            from docxtpl import DocxTemplate
        except ImportError:
            logger.warning(
                "docxtpl не установлен — используется ручная замена плейсхолдеров"
            )
            return self._render_manual(template_path, replacements, output_path)

        try:
            template = DocxTemplate(template_path)
            template.render(replacements, autoescape=True)
            template.save(output_path)
            logger.info(
                f"Шаблон отрендерен через docxtpl ({len(replacements)} переменных)"
            )
            return "docxtpl"
        except Exception as e:
            logger.error(
                f"docxtpl не смог отрендерить шаблон ({type(e).__name__}: {e}) — "
                f"переходим на ручную замену",
                exc_info=True,
            )
            return self._render_manual(template_path, replacements, output_path)

    def _render_manual(
        self,
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
        self._replace_in_document(doc, replacements)
        doc.save(output_path)
        logger.info("Шаблон отрендерен ручной заменой плейсхолдеров")
        return "manual"

    # ─────────────────────────────────────────────────────────
    # ПОСТОБРАБОТКА ДОКУМЕНТА
    # ─────────────────────────────────────────────────────────

    def _postprocess_document(self, path: str, contract_data: Any = None) -> None:
        """
        Дополняет готовый документ тем, что не умеет движок шаблонов:

          * переносы строк внутри значения («\\n» в loading_block) →
            разрывы строк Word: docxtpl вставляет «\\n» как обычный текст,
            и без этого многострочные блоки склеились бы в одну строку;
          * подстановка таблиц погрузок/выгрузок вместо меток
            {{LOADING_TABLE_HERE}} / {{UNLOADING_TABLE_HERE}} (если они есть
            в шаблоне) — см. подкласс перевозки;
          * удаление пустых строк таблицы ТС (в шаблоне их 12, заполняются
            только нужные).

        contract_data — ContractData (или совместимый dict) с точками маршрута
        и списком машин. Необязателен: без него таблицы по погрузкам не
        строятся, остальная постобработка работает как раньше.
        """
        try:
            from docx import Document

            doc = Document(path)
            self._convert_newlines_to_breaks(doc)

            if contract_data is not None:
                data = ContractData.coerce(contract_data)
                self._insert_route_tables(doc, data)

            self._remove_empty_vehicle_rows(doc)
            doc.save(path)
        except Exception as e:
            logger.warning(f"Не удалось выполнить постобработку документа: {e}")

    # ─────────────────────────────────────────────────────────
    # Работа с абзацами-метками (вставка блоков в середину документа)
    # ─────────────────────────────────────────────────────────

    def _find_marker_paragraph(self, doc, marker_text: str):
        """
        Абзац, текст которого равен метке (с точностью до пробелов).

        Абзацы внутри таблиц и колонтитулов тоже просматриваются: метку
        можно поставить в любом месте документа.
        """
        for paragraph in self._iter_paragraphs(doc):
            if paragraph.text.strip() == marker_text:
                return paragraph
        return None

    def _outline_paragraph(self, paragraph):
        """
        «Образец» форматирования абзаца-заголовка — сам абзац с меткой.

        Копируем pPr (отступы, выравнивание, стиль, интервалы) и один run
        как образец шрифта: заголовки погрузок должны выглядеть как
        остальной текст документа.
        """
        from copy import deepcopy

        props = paragraph._p.find(self._qname("pPr"))
        sample_run = paragraph.runs[0]._r if paragraph.runs else None

        return (
            deepcopy(props) if props is not None else None,
            deepcopy(sample_run) if sample_run is not None else None,
        )

    def _make_point_heading(self, doc, head_template: Any, text: str):
        """Абзац-заголовок «Погрузка N: адрес» — жирный, вне таблицы."""
        props, sample_run = head_template

        paragraph = doc.add_paragraph()
        if props is not None:
            paragraph._p.insert(0, props)

        run = paragraph.add_run(text)
        self._copy_run_format(sample_run, run)

        # Заголовок всегда жирный, даже если образец был обычным.
        run.bold = True
        if run.font.size is None and sample_run is None:
            run.font.name = "Times New Roman"

        return paragraph._p

    def _make_spacer(self, doc):
        """Пустой абзац-отступ после таблицы."""
        paragraph = doc.add_paragraph()
        return paragraph._p

    # ─────────────────────────────────────────────────────────
    # Форматирование таблиц и ячеек
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _sample_cell(sample_cells, index: int):
        """Ячейка-образец с нужным номером колонки (или None)."""
        if sample_cells and index < len(sample_cells):
            return sample_cells[index]
        return None

    def _fill_table_cell(self, cell, text: str, sample_cell, bold: bool) -> None:
        """
        Пишет текст в ячейку, повторяя оформление ячейки-образца.

        Из образца переносятся границы, заливка и отступы (tcPr без ширины)
        плюс формат абзаца и шрифта. Ширину колонки задаёт генератор.
        """
        from copy import deepcopy

        if sample_cell is not None:
            self._apply_sample_cell_format(cell, sample_cell)

        paragraph = cell.paragraphs[0]
        sample_paragraph = (
            sample_cell.paragraphs[0]
            if (sample_cell is not None and sample_cell.paragraphs)
            else None
        )
        # Сначала формат абзаца из образца (интервалы, отступы), потом своё
        # выравнивание: иначе копия pPr перетёрла бы центр.
        self._copy_paragraph_format(sample_paragraph, paragraph)
        paragraph.alignment = 1  # WD_ALIGN_PARAGRAPH.CENTER

        sample_run = (
            sample_paragraph.runs[0]._r
            if (sample_paragraph is not None and sample_paragraph.runs)
            else None
        )
        self._set_cell_text(paragraph, text, sample_run, bold=bold)

    @classmethod
    def _apply_sample_cell_format(cls, cell, sample_cell) -> None:
        """
        Копирует tcPr образца (заливка, границы, отступы) в нашу ячейку.

        Ширина и объединения не переносятся: у наших колонок свои размеры,
        а gridSpan/vMerge из образца сломали бы сетку таблицы.
        """
        from copy import deepcopy

        sample_props = sample_cell._tc.find(cls._qname("tcPr"))
        if sample_props is None:
            return

        copied = deepcopy(sample_props)
        for tag in ("tcW", "gridSpan", "vMerge", "hMerge"):
            node = copied.find(cls._qname(tag))
            if node is not None:
                copied.remove(node)

        existing = cell._tc.find(cls._qname("tcPr"))
        if existing is not None:
            cell._tc.remove(existing)
        cell._tc.insert(0, copied)

    @classmethod
    def _set_grid_widths(cls, table, widths_cm) -> None:
        """
        Фиксирует ширину колонок таблицы: tblGrid + tblW + tblLayout.

        Одного cell.width мало: Word считает колонки по tblGrid, а его
        python-docx при создании таблицы делает равномерным.
        """
        from docx.shared import Cm, Emu

        emu_widths = [Cm(width) for width in widths_cm]
        # Cm() возвращает Length (подкласс int), а sum() даёт обычный int —
        # приводим обратно, чтобы пользоваться .twips.
        total = Emu(sum(emu_widths))

        props = table._tbl.tblPr
        layout = props.get_or_add_tblLayout()
        layout.type = "fixed"

        tbl_width = props.find(cls._qname("tblW"))
        if tbl_width is None:
            tbl_width = props.makeelement(cls._qname("tblW"), {})
            props.insert(0, tbl_width)
        tbl_width.set(cls._qname("type"), "dxa")
        tbl_width.set(cls._qname("w"), str(int(total.twips)))

        for grid_col, width in zip(table._tbl.tblGrid, emu_widths):
            grid_col.set(cls._qname("w"), str(int(Emu(width).twips)))

        for row in table.rows:
            for cell, width in zip(row.cells, emu_widths):
                cell.width = width

    @classmethod
    def _set_cell_text(cls, paragraph, text: str, sample_run, bold: bool = False) -> None:
        """Пишет текст в абзац ячейки, копируя шрифт образца."""
        run = paragraph.add_run(text)
        cls._copy_run_format(sample_run, run)
        run.bold = bold
        if run.font.size is None and sample_run is None:
            run.font.name = "Times New Roman"

    # ─────────────────────────────────────────────────────────
    # Копирование форматирования (без новых зависимостей)
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _qname(tag: str) -> str:
        """Имя элемента WordprocessingML с пространством имён."""
        return "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}" + tag

    @classmethod
    def _copy_paragraph_format(cls, sample, paragraph) -> None:
        """
        Копирует форматирование абзаца-образца (если образец найден).

        Существующий pPr заменяется: двух pPr в одном абзаце быть не должно,
        иначе Word берёт первый и наш выравнивание/отступы теряются.
        """
        if sample is None:
            return

        from copy import deepcopy

        props = sample._p.find(cls._qname("pPr"))
        if props is None:
            return

        existing = paragraph._p.find(cls._qname("pPr"))
        if existing is not None:
            paragraph._p.remove(existing)

        paragraph._p.insert(0, deepcopy(props))

    @classmethod
    def _copy_run_format(cls, sample_run, run) -> None:
        """Копирует шрифт (имя, размер, начертание) из образца."""
        if sample_run is None:
            return

        from copy import deepcopy

        props = sample_run.find(cls._qname("rPr"))
        if props is None:
            return

        run._r.insert(0, deepcopy(props))

    # ─────────────────────────────────────────────────────────
    # Обход документа и переносы строк
    # ─────────────────────────────────────────────────────────

    def _convert_newlines_to_breaks(self, doc) -> None:
        """Превращает «\\n» внутри runs в разрывы строк Word."""
        converted = 0
        for paragraph in self._iter_paragraphs(doc):
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

    def _iter_paragraphs(self, doc):
        """Все абзацы документа, включая таблицы (в т.ч. вложенные) и колонтитулы."""
        for paragraph in doc.paragraphs:
            yield paragraph

        for table in doc.tables:
            yield from self._iter_table_paragraphs(table)

        for section in doc.sections:
            for paragraph in section.header.paragraphs:
                yield paragraph
            for paragraph in section.footer.paragraphs:
                yield paragraph

    def _iter_table_paragraphs(self, table):
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    yield paragraph
                for nested in cell.tables:
                    yield from self._iter_table_paragraphs(nested)

    # ─────────────────────────────────────────────────────────
    # ЗАМЕНА ПЛЕЙСХОЛДЕРОВ (резервный путь)
    # ─────────────────────────────────────────────────────────

    def _replace_in_document(self, doc, replacements: Dict[str, str]) -> None:
        for paragraph in self._iter_paragraphs(doc):
            self._replace_in_paragraph(paragraph, replacements)

        logger.info(f"Замены в документе выполнены ({len(replacements)} плейсхолдеров)")

    def _replace_in_paragraph(self, paragraph, replacements: Dict[str, str]) -> None:
        """
        [DEPRECATED] Ручная замена плейсхолдеров в одном абзаце.

        Оставлен только для резервного пути _render_manual: он включается,
        если docxtpl недоступен или не смог обработать конкретный шаблон.
        Основной рендер — docxtpl (см. _render_template): он подставляет
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
    # Таблицы: общие операции
    # ─────────────────────────────────────────────────────────

    def _delete_row(self, table, row) -> None:
        tbl = table._tbl
        tr = row._tr
        tbl.remove(tr)

    # ─────────────────────────────────────────────────────────
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ДЛЯ ДАТ
    # ─────────────────────────────────────────────────────────

    def _format_date_full(self, date_str: str) -> str:
        if not date_str:
            return ""
        try:
            dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
            return dt.strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            return str(date_str)

    def _format_date_dot(self, date_str: str) -> str:
        if not date_str:
            return ""
        try:
            dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
            return dt.strftime("%d.%m")
        except (ValueError, TypeError):
            return str(date_str)

    def _day_of_month(self, date_str: str) -> str:
        if not date_str:
            return ""
        try:
            dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
            return f"{dt.day:02d}"
        except (ValueError, TypeError):
            return str(date_str)

    def _month_name(self, date_str: str) -> str:
        if not date_str:
            return "сентября"
        try:
            dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
            months = ["января", "февраля", "марта", "апреля", "мая", "июня",
                      "июля", "августа", "сентября", "октября", "ноября", "декабря"]
            return months[dt.month - 1]
        except (ValueError, TypeError):
            return "сентября"

    def _short_fio(self, full_name: str) -> str:
        if not full_name:
            return ""
        parts = full_name.strip().split()
        if len(parts) >= 3:
            return f"{parts[1][0]}.{parts[2][0]}. {parts[0]}"
        elif len(parts) == 2:
            return f"{parts[1][0]}. {parts[0]}"
        return full_name

    def _days_to_words(self, days) -> str:
        try:
            days = int(days)
        except (ValueError, TypeError):
            return "десяти"
        days_map = {
            1: "одного", 2: "двух", 3: "трёх", 4: "четырёх", 5: "пяти",
            6: "шести", 7: "семи", 8: "восьми", 9: "девяти", 10: "десяти",
            11: "одиннадцати", 12: "двенадцати", 13: "тринадцати", 14: "четырнадцати",
            15: "пятнадцати", 16: "шестнадцати", 17: "семнадцати", 18: "восемнадцати",
            19: "девятнадцати", 20: "двадцати", 30: "тридцати",
        }
        return days_map.get(days, str(days))

    # ─────────────────────────────────────────────────────────
    # Нормализация значений
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _single_line(value: Any) -> str:
        """
        Значение одной строкой: переносы строк, табуляции и лишние пробелы —
        в один обычный пробел.
        """
        return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()

    @staticmethod
    def _normalized_point_address(point: Dict[str, Any]) -> str:
        """Адрес точки одной строкой: переносы строк в заголовке не нужны."""
        address = str(point.get("address", "") or "")
        return re.sub(r"\s+", " ", address).strip()

    @classmethod
    def _flatten_replacements(cls, replacements: Dict[str, str]) -> None:
        """
        Убирает переносы строк из значений, которые должны быть однострочными.

        Многострочные блоки ({{loading_block}} / {{unloading_block}}, legacy
        для старых шаблонов) не трогаем: там переносы строк несут смысл.
        """
        for name, value in replacements.items():
            if name in cls.MULTILINE_PLACEHOLDERS:
                continue
            if not isinstance(value, str) or not value:
                continue
            if re.search(r"[\r\n\t]|\s{2,}", value):
                replacements[name] = cls._single_line(value)
