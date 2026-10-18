#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Базовый генератор DOCX-договоров: оркестрация и контракт для подклассов.

Здесь живёт то, что не зависит от конкретного типа договора (категория A),
но и не выносится в модули-помощники:

  * оркестрация: generate() → generate_docx() → рендер → постобработка;
  * контракт типа: CONTRACT_TYPE, TEMPLATE_NAMES, FILE_PREFIX,
    VALIDATOR_CLASS, MULTILINE_PLACEHOLDERS;
  * четыре заглушки с NotImplementedError, которые реализует подкласс
    (_get_template_path, _build_replacements_map, _insert_route_tables,
    _remove_empty_vehicle_rows) — так контракт виден сразу, а прямые
    пользователи базы получают понятную ошибку;
  * тонкие делегаты в модули-помощники (Этап 4, 16.10.2026).

Разбиение на модули (Этап 4):
  * `formatting.py`  — копирование стилей, обход абзацев, ширины колонок;
  * `render.py`      — движок рендера docxtpl и откат на ручную замену;
  * `tables.py`      — таблицы машин;
  * `postprocess.py` — PostprocessStep и штатные шаги;
  * `payments.py`    — разбивка оплаты, суммы прописью;
  * `dates.py`       — форматы дат для шаблонов.

**Публичный API не изменился.** Все переехавшие приватные методы остались
здесь тонкими делегатами с прежними подписями и прежними
`@classmethod`/`@staticmethod`: подклассы (PerevozkaGenerator и клиентские
из приватной части) вызывают их через `self.`/`cls.`, и без делегатов они
падали бы с AttributeError — метод физически в другом файле.

Логгер намеренно называется «core.contract_generator»: тесты
(tests/test_contract_generator.py) ловят сообщения генерации через
caplog по этому имени, и переезд кода не должен их разорвать.
"""

import logging
import os
import re
from abc import ABC
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Mapping, Optional

from core.contract_data import ContractData
from core.contracts.base import dates as base_dates
from core.contracts.base import payments as base_payments
from core.contracts.base.formatting import Formatting
from core.contracts.base.postprocess import (
    ConvertNewlinesStep,
    PostprocessStep,
    convert_newlines_to_breaks,
    normalize_document_spaces,
    normalize_spaces,
    set_paragraph_text,
)
from core.contracts.base.render import TemplateRenderer
from core.contracts.base.tables import TableBuilder
from core.contracts.paths import contract_output_path
from core.trace import trace
from core.validator import ValidationReport

logger = logging.getLogger("core.contract_generator")


class BaseContractGenerator(ABC):
    """Общая часть генераторов DOCX-договоров (движок, утилиты, оркестрация)."""

    # ── Контракт типа ──
    #: Идентификатор типа договора (значение core.contracts.ContractType).
    CONTRACT_TYPE: ClassVar[str] = ""

    #: Имена файлов шаблонов: {ключ: имя файла в templates_dir}.
    #: Подкласс перевозки задаёт {"ООО": "shablon_ooo.docx", ...}.
    TEMPLATE_NAMES: ClassVar[Mapping[str, str]] = {}

    #: Префикс имени генерируемого файла («Договор-заявка» у перевозки).
    FILE_PREFIX: ClassVar[str] = ""

    #: Класс валидатора типа (хук validate()); None — валидации нет.
    VALIDATOR_CLASS: ClassVar[Optional[type]] = None

    # Папка по умолчанию для готовых договоров (Шаг 6 оптимизации).
    # Раньше файлы по 2,7 МБ падали в корень проекта и мешались с кодом.
    DEFAULT_OUTPUT_DIRNAME = "output"

    #: Плейсхолдеры, у которых перенос строки несёт смысл. Подкласс
    #: перевозки перекрывает: ("loading_block", "unloading_block").
    MULTILINE_PLACEHOLDERS: ClassVar[tuple] = ()

    #: Оформление таблицы машин (блок 3.1 бланка). Значения задаёт подкласс
    #: перевозки; пустые по умолчанию — чтобы делегаты не падали с
    #: AttributeError у типов, которые таблиц машин не строят.
    VEHICLE_TABLE_HEADERS: ClassVar[tuple] = ()
    VEHICLE_TABLE_STYLE: ClassVar[str] = ""
    VEHICLE_TABLE_COLUMN_WIDTHS_CM: ClassVar[tuple] = ()

    def __init__(self, templates_dir: str):
        self.templates_dir = templates_dir

        # Помощники без состояния: создаются один раз, как и раньше создавались
        # бы на месте. Экземпляры нужны ради единообразия вызовов.
        self.formatting = Formatting()
        self.renderer = TemplateRenderer()
        self.tables = TableBuilder()

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
        Папка для готовых договоров: `<папка данных>/output`.

        Путь берётся из core/data_paths.py: в .exe это
        `%APPDATA%\\LogisticsContractor\\output`, в разработке —
        `tests/_tmp/_appdata/output`. Договоры — данные пользователя, и в
        папке установки им не место: её заменяют при обновлении.

        История: раньше корень считался от местоположения этого файла
        (core/contracts/base/generator.py → parents[3]). После разбиения
        (Этап 4) файл стал на папку глубже (было parents[2]), поэтому счёт
        уровней здесь больше не используется.
        """
        from core import data_paths

        return str(data_paths.output_dir())

    # ─────────────────────────────────────────────────────────
    # ХУКИ (точки расширения, переопределяемые подклассом)
    # ─────────────────────────────────────────────────────────

    def preprocess(self, data: ContractData) -> ContractData:
        """
        Подготовка данных перед генерацией.

        По умолчанию — identity: данные уже приведены через
        ContractData.coerce. Подкласс может дополнять/чинить данные.
        """
        return data

    @staticmethod
    def _carrier_type_of(data: ContractData) -> str:
        """Тип перевозчика из условий договора (историческая логика)."""
        contract = data.contract
        carrier = data.carrier
        return (
            contract.get("carrier_type")
            or carrier.get("carrier_type")
            or "ООО (с НДС)"
        )

    def get_template_path(self, data: ContractData) -> str:
        """
        Путь шаблона для данных договора.

        Дефолт — адаптер к старому приватному имени подкласса
        _get_template_path(carrier_type): перевозка его уже реализует,
        новые типы могут переопределить хук целиком.
        """
        return self._get_template_path(self._carrier_type_of(data))

    def build_replacements(self, data: ContractData) -> Dict[str, str]:
        """
        Карта замен плейсхолдеров шаблона.

        Дефолт — адаптер к старому приватному имени _build_replacements_map:
        перевозка его уже реализует, новые типы могут переопределить хук.
        """
        return self._build_replacements_map(data)

    def get_filename(self, data: ContractData) -> str:
        """Имя файла: <FILE_PREFIX>_<номер>_<ГГГГММДД>.docx (санитайзинг номера)."""
        contract = data.contract
        number = (contract.get("number") or "без-номера").strip()

        safe_number = re.sub(r'[^\w\-]+', '-', number)
        if not safe_number:
            safe_number = "без-номера"

        date_str = datetime.now().strftime("%Y%m%d")
        prefix = f"{self.FILE_PREFIX}_" if self.FILE_PREFIX else ""
        return f"{prefix}{safe_number}_{date_str}.docx"

    def validate(self, data: Any) -> ValidationReport:
        """
        Проверка данных типа своим валидатором.

        VALIDATOR_CLASS не задан → пустой отчёт (проверок нет, не ошибка).
        """
        validator_class = self.VALIDATOR_CLASS
        if validator_class is None:
            return ValidationReport()
        return validator_class().check(data)

    def postprocess_steps(self, data: Optional[ContractData]) -> List[PostprocessStep]:
        """
        Конвейер постобработки для типа.

        Дефолт — только переносы строк. Подкласс добавляет свои шаги
        (перевозка: таблицы маршрута + удаление пустых строк таблицы ТС).
        """
        return [ConvertNewlinesStep(self)]

    # ─────────────────────────────────────────────────────────
    # Старые приватные имена специфики (реализует подкласс)
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
        contract_data = self.preprocess(ContractData.coerce(data))
        filename = self.get_filename(contract_data)

        if output_dir is None:
            # Шаг 6 оптимизации: готовые договоры складываются в output/,
            # а не в корень проекта рядом с исходниками.
            output_dir = self.default_output_dir()

        os.makedirs(output_dir, exist_ok=True)

        # Документы одного рейса лежат в своей папке: имя считается из
        # водителя, маршрута и даты договора (core/contracts/paths.py).
        # Зеркалённая заявка другого типа получит то же имя папки — данные-то
        # те же, — и ляжет рядом с договором, а не в общий output/.
        output_path = contract_output_path(output_dir, filename, contract_data)

        # В лог идёт только имя файла: в имени папки — фамилия водителя, а
        # логи проекта идут без ПДн (AGENTS.md § 3).
        logger.info(f"generate(): файл={filename}")

        return self.generate_docx(contract_data, output_path)

    # ─────────────────────────────────────────────────────────
    # ГЕНЕРАЦИЯ DOCX
    # ─────────────────────────────────────────────────────────

    @trace
    def generate_docx(self, data: Dict[str, Any], output_path: str) -> str:
        try:
            contract_data = self.preprocess(ContractData.coerce(data))
            carrier_type = self._carrier_type_of(contract_data)
            template_path = self.get_template_path(contract_data)

            if not os.path.exists(template_path):
                raise FileNotFoundError(f"Шаблон не найден: {template_path}")

            logger.info(f"Используем шаблон [{carrier_type}]: {template_path}")

            replacements = self.build_replacements(contract_data)
            engine = self._render_template(template_path, replacements, output_path)
            # contract_data нужен постобработке: она собирает таблицы
            # погрузок/выгрузок из точек маршрута и списка машин.
            self._postprocess_document(output_path, contract_data)

            # В лог — имя файла, а не полный путь: в пути лежит имя папки
            # рейса с фамилией водителя, а логи идут без ПДН (AGENTS.md § 3).
            logger.info(f"DOCX сохранён ({engine}): {os.path.basename(output_path)}")
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
    # РЕНДЕР ШАБЛОНА (делегаты в base/render.py)
    # ─────────────────────────────────────────────────────────

    def _render_template(
        self,
        template_path: str,
        replacements: Dict[str, str],
        output_path: str,
    ) -> str:
        """
        Рендерит шаблон и возвращает имя использованного движка.

        Остаётся методом (а не статическим вызовом модуля), потому что
        зовёт `self._render_manual` через откат: если подкласс переопределит
        резервный путь, переопределение должно сработать.
        """
        try:
            return self.renderer.render_docxtpl(
                template_path, replacements, output_path
            )
        except ImportError:
            logger.warning(
                "docxtpl не установлен — используется ручная замена плейсхолдеров"
            )
            return self._render_manual(template_path, replacements, output_path)
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
        return self.renderer.render_manual(template_path, replacements, output_path)

    def _replace_in_document(self, doc, replacements: Dict[str, str]) -> None:
        return self.renderer.replace_in_document(doc, replacements)

    def _replace_in_paragraph(self, paragraph, replacements: Dict[str, str]) -> None:
        return self.renderer.replace_in_paragraph(paragraph, replacements)

    def _find_marker_paragraph(self, doc, marker_text: str):
        return self.renderer.find_marker_paragraph(doc, marker_text)

    def _outline_paragraph(self, paragraph):
        return self.renderer.outline_paragraph(paragraph)

    def _make_point_heading(self, doc, head_template: Any, text: str):
        return self.renderer.make_point_heading(doc, head_template, text)

    def _make_spacer(self, doc):
        return self.renderer.make_spacer(doc)

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
            data = ContractData.coerce(contract_data) if contract_data is not None else None

            # Шаги выполняются в одном try, как раньше: падение первого
            # шага отменяет остальные, а ошибка гасится в warning.
            for step in self.postprocess_steps(data):
                logger.debug(f"Постобработка: шаг {step.name}")
                step.apply(doc, data)

            doc.save(path)
        except Exception as e:
            logger.warning(f"Не удалось выполнить постобработку документа: {e}")

    # ─────────────────────────────────────────────────────────
    # Обход документа и переносы строк (делегаты в base/postprocess.py)
    # ─────────────────────────────────────────────────────────

    def _convert_newlines_to_breaks(self, doc) -> None:
        return convert_newlines_to_breaks(doc)

    def _normalize_document_spaces(self, doc) -> None:
        return normalize_document_spaces(doc)

    @staticmethod
    def _set_paragraph_text(paragraph, text: str) -> None:
        return set_paragraph_text(paragraph, text)

    @staticmethod
    def normalize_spaces(text: str) -> str:
        return normalize_spaces(text)

    # ─────────────────────────────────────────────────────────
    # Форматирование (делегаты в base/formatting.py)
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _qname(tag: str) -> str:
        return Formatting.qname(tag)

    @staticmethod
    def _single_line(value: Any) -> str:
        return Formatting.single_line(value)

    @staticmethod
    def _normalized_point_address(point: Dict[str, Any]) -> str:
        return Formatting.normalized_point_address(point)

    @staticmethod
    def _copy_paragraph_format(sample, paragraph) -> None:
        return Formatting.copy_paragraph_format(sample, paragraph)

    @staticmethod
    def _copy_run_format(sample_run, run) -> None:
        return Formatting.copy_run_format(sample_run, run)

    @staticmethod
    def _apply_sample_cell_format(cell, sample_cell) -> None:
        return Formatting.apply_sample_cell_format(cell, sample_cell)

    @staticmethod
    def _set_cell_text(paragraph, text: str, sample_run, bold: bool = False) -> None:
        return Formatting.set_cell_text(paragraph, text, sample_run, bold=bold)

    @staticmethod
    def _sample_cell(sample_cells, index: int):
        return Formatting.sample_cell(sample_cells, index)

    def _fill_table_cell(self, cell, text: str, sample_cell, bold: bool) -> None:
        return Formatting.fill_table_cell(cell, text, sample_cell, bold)

    @staticmethod
    def _set_grid_widths(table, widths_cm) -> None:
        return Formatting.set_grid_widths(table, widths_cm)

    def _iter_paragraphs(self, doc):
        return Formatting.iter_paragraphs(doc)

    def _iter_table_paragraphs(self, table):
        return Formatting.iter_table_paragraphs(table)

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
                replacements[name] = Formatting.single_line(value)

    # ─────────────────────────────────────────────────────────
    # Таблицы (делегаты в base/tables.py)
    # ─────────────────────────────────────────────────────────

    def _delete_row(self, table, row) -> None:
        return TableBuilder.delete_row(table, row)

    def _make_vehicle_table(self, doc, vehicles, start_number: int = 1):
        """
        Таблица машин: «№ | Марка/Модель | VIN-номер».

        Константы оформления и правило сборки строки задаёт подкласс
        (`VEHICLE_TABLE_HEADERS`, `VEHICLE_TABLE_STYLE`,
        `VEHICLE_TABLE_COLUMN_WIDTHS_CM`, `_vehicle_row_values`); сборка —
        в `base/tables.py`. `self._sample_vehicle_table` передаётся
        вызываемым, чтобы переопределение подкласса сработало.
        """
        return TableBuilder.vehicle_table(
            doc,
            vehicles,
            headers=self.VEHICLE_TABLE_HEADERS,
            style=self.VEHICLE_TABLE_STYLE,
            column_widths_cm=self.VEHICLE_TABLE_COLUMN_WIDTHS_CM,
            row_values=self._vehicle_row_values,
            sample_provider=self._sample_vehicle_table,
            start_number=start_number,
        )

    def _sample_vehicle_table(self, doc):
        """Таблица машин из шаблона — образец оформления (правило подкласса)."""
        return TableBuilder.sample_vehicle_table(doc, self._is_vehicle_table)

    def _vehicle_row_values(self, vehicle: Dict[str, Any], number: int):
        """
        Значения строки таблицы машин: (номер, марка, VIN).

        Переехало из тела `_make_vehicle_table` (Этап 4). Заглушка с внятной
        ошибкой: у типа без таблицы машин своей сборки строки нет.
        """
        raise NotImplementedError(
            f"{type(self).__name__} должен реализовать _vehicle_row_values()"
        )

    def _is_vehicle_table(self, table) -> bool:
        """
        Таблица машин из шаблона (блок 3.1) — образец оформления.

        По умолчанию образца нет: тип, который строит таблицы машин,
        переопределяет метод (так делает `PerevozkaGenerator`).
        """
        return False

    # ─────────────────────────────────────────────────────────
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ДЛЯ ДАТ (делегаты в base/dates.py)
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _format_date_full(date_str: str) -> str:
        return base_dates.format_date_full(date_str)

    @staticmethod
    def _format_date_dot(date_str: str) -> str:
        return base_dates.format_date_dot(date_str)

    @staticmethod
    def _day_of_month(date_str: str) -> str:
        return base_dates.day_of_month(date_str)

    @staticmethod
    def _month_name(date_str: str) -> str:
        return base_dates.month_name(date_str)

    # ─────────────────────────────────────────────────────────
    # Нормализация значений
    # ─────────────────────────────────────────────────────────

    def _short_fio(self, full_name: str) -> str:
        if not full_name:
            return ""
        parts = full_name.strip().split()
        if len(parts) >= 3:
            return f"{parts[1][0]}.{parts[2][0]}. {parts[0]}"
        elif len(parts) == 2:
            return f"{parts[1][0]}. {parts[0]}"
        return full_name

    # ─────────────────────────────────────────────────────────
    # Разбивка оплаты и числительные (делегаты в base/payments.py)
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _split_payment(cls, total: float, prepayment: float) -> Dict[str, Any]:
        """
        Разбивка суммы договора на предоплату и остаток.

        Остаётся classmethod'ом: и тесты (`test_prepayment.py`), и
        подклассы зовут его без экземпляра — `cls._split_payment(...)`.
        Реализация — `base/payments.py::split_payment`.
        """
        return base_payments.split_payment(total, prepayment)

    @staticmethod
    def _lower_first(text: str) -> str:
        return base_payments.lower_first(text)

    def _days_to_words(self, days) -> str:
        return base_payments.days_to_words(days)

    @classmethod
    def _days_to_words_genitive(cls, days: Any) -> str:
        """Количество дней прописью в родительном падеже («сорока пяти»)."""
        return base_payments.days_to_words_genitive(days)

    @classmethod
    def _triple_to_words(cls, number: int, *, gender: str = "male") -> str:
        """Число 1…999 прописью в родительном падеже."""
        return base_payments.triple_to_words(number, gender=gender)

    @staticmethod
    def _plural_form(number: int, forms) -> str:
        return base_payments.plural_form(number, forms)
