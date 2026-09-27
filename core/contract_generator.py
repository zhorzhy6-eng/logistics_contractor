#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор договора в форматах DOCX и PDF.
Выбирает шаблон по типу перевозчика:
  - ООО (с НДС)     → templates/shablon_ooo.docx
  - ИП с НДС        → templates/shablon_ip_with_vat.docx
  - ИП без НДС      → templates/shablon_ip_without_vat.docx

Поддерживает:
  - НЕСКОЛЬКО мест погрузки и выгрузки (до 10)
  - Привязку каждой машины к конкретной точке погрузки/выгрузки
    через поля vehicle["loading_index"], vehicle["unloading_index"]
    (0 = «— (все)» → машина попадает во все точки)

Тип перевозчика и ставка НДС берутся ИЗ УСЛОВИЙ ДОГОВОРА (contract),
а не из карточки перевозчика.
"""

import logging
import os
import re
from datetime import datetime
from typing import Dict, Any, List, Optional

from core.contract_data import ContractData
from core.num_to_words import amount_to_words
from core.trace import trace

logger = logging.getLogger("core.contract_generator")


class ContractGenerator:
    """Заменяет переменные в шаблоне договора и генерирует итоговый документ."""

    # Папка по умолчанию для готовых договоров (Шаг 6 оптимизации).
    # Раньше файлы по 2,7 МБ падали в корень проекта и мешались с кодом.
    DEFAULT_OUTPUT_DIRNAME = "output"

    def __init__(self, templates_dir: str):
        self.templates_dir = templates_dir

        self.templates = {
            "ООО":         os.path.join(templates_dir, "shablon_ooo.docx"),
            "ИП с НДС":    os.path.join(templates_dir, "shablon_ip_with_vat.docx"),
            "ИП без НДС":  os.path.join(templates_dir, "shablon_ip_without_vat.docx"),
        }

        logger.info(f"ContractGenerator инициализирован: templates_dir={templates_dir}")
        for key, path in self.templates.items():
            if os.path.exists(path):
                logger.info(f"  ✓ Шаблон [{key}]: {path}")
            else:
                logger.warning(f"  ✗ Шаблон [{key}] НЕ НАЙДЕН: {path}")

    @classmethod
    def default_output_dir(cls) -> str:
        """Папка для готовых договоров: <корень проекта>/output."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(project_root, cls.DEFAULT_OUTPUT_DIRNAME)

    # ─────────────────────────────────────────────────────────
    # ВЫБОР ШАБЛОНА
    # ─────────────────────────────────────────────────────────

    def _get_template_path(self, carrier_type: str) -> str:
        if "ИП без НДС" in carrier_type:
            return self.templates["ИП без НДС"]
        elif "ИП с НДС" in carrier_type:
            return self.templates["ИП с НДС"]
        else:
            return self.templates["ООО"]

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
            self._postprocess_document(output_path)

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
    # РЕНДЕР ШАБЛОНА  ← Шаг 6 рефакторинга
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

    def _postprocess_document(self, path: str) -> None:
        """
        Дополняет готовый документ тем, что не умеет движок шаблонов:

          * переносы строк внутри значения («\\n» в loading_block) →
            разрывы строк Word: docxtpl вставляет «\\n» как обычный текст,
            и без этого многострочные блоки склеились бы в одну строку;
          * удаление пустых строк таблицы ТС (в шаблоне их 12, заполняются
            только нужные).
        """
        try:
            from docx import Document

            doc = Document(path)
            self._convert_newlines_to_breaks(doc)
            self._remove_empty_vehicle_rows(doc)
            doc.save(path)
        except Exception as e:
            logger.warning(f"Не удалось выполнить постобработку документа: {e}")

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
    # УДАЛЕНИЕ ПУСТЫХ СТРОК В ТАБЛИЦЕ ТС
    # ─────────────────────────────────────────────────────────

    def _remove_empty_vehicle_rows(self, doc) -> None:
        for table in doc.tables:
            if not self._is_vehicle_table(table):
                continue

            for i in range(len(table.rows) - 1, 0, -1):
                row = table.rows[i]
                if len(row.cells) >= 2:
                    brand_text = row.cells[1].text.strip()
                    vin_text = row.cells[2].text.strip() if len(row.cells) >= 3 else ""
                    if not brand_text and not vin_text:
                        self._delete_row(table, row)
                        logger.debug(f"Удалена пустая строка {i} из таблицы ТС")

    def _is_vehicle_table(self, table) -> bool:
        if not table.rows:
            return False
        headers = [cell.text.strip() for cell in table.rows[0].cells]
        for h in headers:
            h_norm = h.replace("–", "-").replace("—", "-").strip()
            if h_norm in ("VIN-номер", "VIN- номер", "Марка/Модель"):
                return True
        return False

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
    # БЛОК ТОЧЕК С ПРИВЯЗКОЙ МАШИН
    # ─────────────────────────────────────────────────────────

    def _build_points_block_with_vehicles(
        self,
        points: List[Dict[str, Any]],
        title: str,
        vehicles: List[Dict[str, Any]],
        index_field: str,
    ) -> str:
        if not points:
            return ""

        point_vehicles = {i: [] for i in range(1, len(points) + 1)}

        for v in vehicles:
            vtype = v.get("vehicle_type", "")
            if vtype in ("Тягач", "Полуприцеп", "Прицеп"):
                continue

            vin = (v.get("vin") or "").strip()
            if not vin:
                continue

            try:
                idx = int(v.get(index_field, 0) or 0)
            except (ValueError, TypeError):
                idx = 0

            if idx == 0:
                for p_idx in point_vehicles:
                    point_vehicles[p_idx].append(vin)
            elif 1 <= idx <= len(points):
                point_vehicles[idx].append(vin)
            else:
                for p_idx in point_vehicles:
                    point_vehicles[p_idx].append(vin)

        lines = []
        for i, p in enumerate(points, 1):
            parts = [f"{title} {i}: {p.get('address', '').strip()}"]
            date_str = self._format_date_full(p.get("date", ""))
            if date_str:
                parts.append(date_str)
            time_str = (p.get("time_window", "") or "").strip()
            if time_str:
                parts.append(time_str)
            lines.append(" — ".join(parts))

            vins_here = point_vehicles.get(i, [])
            if vins_here:
                vins_str = ", ".join(vins_here)
                lines.append(f"  Машины ({len(vins_here)} шт.): {vins_str}")
            else:
                lines.append("  Машины: —")

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────
    # КАРТА ЗАМЕН
    # ─────────────────────────────────────────────────────────

    def _build_replacements_map(self, data: Dict[str, Any]) -> Dict[str, str]:
        contract_data = ContractData.coerce(data)
        replacements: Dict[str, str] = {}

        # ── Данные договора ──
        contract = contract_data.contract
        replacements["contract_number"] = contract.get("number", "")

        date_iso = contract.get("date", "")
        replacements["contract_date"] = self._day_of_month(date_iso) if date_iso else ""
        replacements["contract_month"] = self._month_name(date_iso)
        replacements["contract_year"] = str(datetime.now().year)

        # Город заключения: явное поле → город первой погрузки →
        # юр. адрес перевозчика → «Москва» (историческое поведение, баг 2.9).
        city = contract_data.resolved_city()
        replacements["city"] = city.replace("г. ", "").replace("г.", "").strip()

        # ── Перевозчик ──
        carrier = contract_data.carrier

        carrier_type_ui = contract.get("carrier_type") or carrier.get("carrier_type", "ООО (с НДС)")

        vat_rate_num = contract.get("vat_rate_num")
        if vat_rate_num is None:
            vat_rate_str = contract.get("vat_rate", "22%")
            try:
                vat_rate_num = float(str(vat_rate_str).replace("%", "").strip())
            except (ValueError, TypeError):
                vat_rate_num = 22.0

        is_ooo = "ООО" in carrier_type_ui
        is_ip_with_vat = "ИП с НДС" in carrier_type_ui
        is_ip_without_vat = "ИП без НДС" in carrier_type_ui

        if is_ooo:
            legal_form = "Общество с ограниченной ответственностью"
            basis = "Устава"
            pronoun = "именуемое"
            director_position_full = carrier.get("director_position", "директора") or "директора"
            director_position_short = "Директор"
            kpp = carrier.get("kpp", "")
            ogrn_label = "ОГРН"
            nds_status_text = ("Перевозчик подтверждает, что применяет общую систему "
                               "налогообложения и является плательщиком НДС.")
        else:
            legal_form = "Индивидуальный предприниматель"
            basis = "свидетельства о государственной регистрации"
            pronoun = "именуемый"
            director_position_full = "Индивидуального предпринимателя"
            director_position_short = "Индивидуальный предприниматель"
            kpp = ""
            ogrn_label = "ОГРНИП"
            if is_ip_without_vat:
                vat_rate_num = 0.0
                nds_status_text = ("Перевозчик подтверждает, что применяет упрощённую "
                                   "систему налогообложения и не является плательщиком НДС.")
            else:
                nds_status_text = ("Перевозчик подтверждает, что применяет общую систему "
                                   "налогообложения и является плательщиком НДС.")

        logger.info(
            f"ContractGenerator: тип={carrier_type_ui}, "
            f"НДС={vat_rate_num}%, ООО={is_ooo}, "
            f"ИП_с_НДС={is_ip_with_vat}, ИП_без_НДС={is_ip_without_vat}"
        )

        replacements["carrier_legal_form"] = legal_form
        replacements["carrier_pronoun"] = pronoun
        replacements["carrier_basis"] = basis
        replacements["carrier_full_name"] = carrier.get("full_name", "")
        replacements["carrier_name"] = carrier.get("short_name", "") or carrier.get("full_name", "")
        replacements["carrier_inn"] = carrier.get("inn", "")
        replacements["carrier_kpp"] = kpp
        replacements["carrier_kpp_line"] = f"КПП {kpp}" if kpp else ""
        replacements["carrier_ogrn_label"] = ogrn_label
        replacements["carrier_ogrn"] = carrier.get("ogrn", "")
        replacements["carrier_address"] = carrier.get("legal_address", "")
        replacements["carrier_actual_address"] = carrier.get("actual_address", "")
        replacements["carrier_account"] = carrier.get("bank_account", "")
        replacements["carrier_bik"] = carrier.get("bik", "")
        replacements["carrier_bank"] = carrier.get("bank_name", "")
        replacements["carrier_corr_account"] = carrier.get("correspondent_account", "")
        replacements["carrier_director"] = carrier.get("director_name", "")
        replacements["carrier_director_position"] = director_position_full
        replacements["carrier_director_position_short"] = director_position_short
        replacements["carrier_phone"] = carrier.get("phone", "")
        replacements["carrier_email"] = carrier.get("email", "")

        # ── Заказчик ──
        # ВАЖНО (Шаг 2): здесь больше нет выдуманных значений по умолчанию
        # («ООО ТЕХНОЛОГИСТИКА», ИНН 9709112631, «Ахмедова Т.А.»).
        # Если заказчик не заполнен — подставляется пустая строка, а
        # core.validator сообщает об этом пользователю до генерации.
        customer = contract_data.customer
        customer_director = customer.get("director_name", "")
        replacements["client_full_name"] = customer.get("full_name", "")
        replacements["client_name"] = customer.get("short_name", "") or customer.get("full_name", "")
        replacements["client_inn"] = customer.get("inn", "")
        replacements["client_kpp"] = customer.get("kpp", "")
        replacements["client_ogrn"] = customer.get("ogrn", "")
        replacements["client_address"] = customer.get("legal_address", "")
        replacements["client_account"] = customer.get("bank_account", "")
        replacements["client_bik"] = customer.get("bik", "")
        replacements["client_bank"] = customer.get("bank_name", "")
        replacements["client_corr_account"] = customer.get("correspondent_account", "")
        replacements["client_director"] = customer_director
        replacements["client_director_position"] = customer.get("director_position", "")
        replacements["client_director_position_short"] = customer.get("director_position", "")
        replacements["client_director_short"] = self._short_fio(customer_director)
        replacements["client_basis"] = "Устава"
        replacements["client_phone"] = customer.get("phone", "")
        replacements["client_email"] = customer.get("email", "")

        # ── Водитель ──
        driver = contract_data.driver
        replacements["driver_name"] = driver.get("full_name", "")
        replacements["driver_birth_date"] = self._format_date_full(driver.get("birth_date", ""))
        replacements["driver_birth_place"] = driver.get("birth_place", "")

        passport_series = driver.get("passport_series", "")
        passport_number = driver.get("passport_number", "")
        replacements["driver_passport"] = f"{passport_series} {passport_number}".strip()

        replacements["driver_passport_date"] = self._format_date_full(driver.get("passport_issue_date", ""))
        replacements["driver_passport_issuer"] = driver.get("passport_issuer", "")
        replacements["driver_address"] = driver.get("registration_address", "")

        license_series = driver.get("license_series", "")
        license_number = driver.get("license_number", "")
        replacements["driver_license"] = f"{license_series} {license_number}".strip()

        replacements["driver_license_date"] = self._format_date_full(driver.get("license_issue_date", ""))
        replacements["driver_license_expiry"] = self._format_date_full(driver.get("license_expiry_date", ""))
        replacements["driver_license_categories"] = driver.get("license_categories", "")
        replacements["driver_phone"] = driver.get("phone", "")

        # ── Тягач / Прицеп ──
        # Исправление бага 2.2: UI отдаёт tractor/trailer ПЛОСКО
        # (MainWindow._collect_data → ContractData), а не в data["trailer"]["tractor"].
        tractor = contract_data.tractor
        trailer = contract_data.trailer

        replacements["tractor_brand"] = tractor.get("brand_model", "")
        replacements["tractor_plate"] = tractor.get("plate_number", "")
        replacements["tractor_color"] = tractor.get("color", "")
        replacements["tractor_year"] = str(tractor.get("year", ""))
        replacements["trailer_brand"] = trailer.get("brand_model", "")
        replacements["trailer_plate"] = trailer.get("plate_number", "")
        replacements["trailer_color"] = trailer.get("color", "")
        replacements["trailer_year"] = str(trailer.get("year", ""))

        # ── Груз и ТС ──
        all_vehicles = contract_data.vehicles
        vehicles = [
            v for v in all_vehicles
            if v.get("vehicle_type") not in ("Тягач", "Полуприцеп", "Прицеп")
        ]
        replacements["cargo_count"] = str(len(vehicles))

        for i in range(1, 13):
            if i <= len(vehicles):
                v = vehicles[i - 1]
                replacements[f"car_{i}_brand"] = v.get("brand_model", "")
                replacements[f"car_{i}_vin"] = v.get("vin", "")
                replacements[f"car_{i}_loading"] = self._get_point_label(
                    v.get("loading_index", 0), contract_data.loadings, "Погрузка"
                )
                replacements[f"car_{i}_unloading"] = self._get_point_label(
                    v.get("unloading_index", 0), contract_data.unloadings, "Выгрузка"
                )
            else:
                replacements[f"car_{i}_brand"] = ""
                replacements[f"car_{i}_vin"] = ""
                replacements[f"car_{i}_loading"] = ""
                replacements[f"car_{i}_unloading"] = ""

        # ── Погрузки / выгрузки ──
        loadings = contract_data.loadings
        unloadings = contract_data.unloadings

        if not loadings and contract.get("loading_address"):
            loadings = [{
                "address": contract.get("loading_address", ""),
                "date": contract.get("loading_date", ""),
                "time_window": contract.get("loading_time_window", ""),
            }]
        if not unloadings:
            legacy = []
            if contract.get("unloading_address_1"):
                legacy.append({
                    "address": contract.get("unloading_address_1", ""),
                    "date": contract.get("unloading_date", ""),
                    "time_window": contract.get("unloading_time_window", ""),
                })
            if contract.get("unloading_address_2"):
                legacy.append({
                    "address": contract.get("unloading_address_2", ""),
                    "date": contract.get("unloading_date", ""),
                    "time_window": contract.get("unloading_time_window", ""),
                })
            unloadings = legacy

        replacements["loading_block"] = self._build_points_block_with_vehicles(
            loadings, "Погрузка", all_vehicles, "loading_index"
        )
        replacements["unloading_block"] = self._build_points_block_with_vehicles(
            unloadings, "Выгрузка", all_vehicles, "unloading_index"
        )

        # В лог пишем только размеры блоков: содержимое содержит адреса
        # погрузки/выгрузки, которым в логах не место (Шаг 4 задания).
        logger.debug(
            f"loading_block сформирован: погрузок {len(loadings)}, "
            f"символов {len(replacements['loading_block'])}"
        )
        logger.debug(
            f"unloading_block сформирован: выгрузок {len(unloadings)}, "
            f"символов {len(replacements['unloading_block'])}"
        )

        if loadings:
            replacements["loading_address"] = loadings[0].get("address", "")
            replacements["loading_date"] = self._format_date_dot(loadings[0].get("date", ""))
            replacements["loading_time_window"] = loadings[0].get("time_window", "")
        else:
            replacements["loading_address"] = ""
            replacements["loading_date"] = ""
            replacements["loading_time_window"] = ""

        replacements["unloading_address_1"] = unloadings[0].get("address", "") if len(unloadings) > 0 else ""
        replacements["unloading_address_2"] = unloadings[1].get("address", "") if len(unloadings) > 1 else ""
        if unloadings:
            replacements["unloading_date"] = self._format_date_dot(unloadings[-1].get("date", ""))
            replacements["unloading_time_window"] = unloadings[-1].get("time_window", "")
        else:
            replacements["unloading_date"] = ""
            replacements["unloading_time_window"] = ""

        replacements["route"] = contract.get("route", "")

        # ── Плановые даты ──
        loading_plan_date = contract.get("loading_plan_date", "")
        unloading_plan_date = contract.get("unloading_plan_date", "")
        loading_plan_time_from = contract.get("loading_plan_time_from", "")
        loading_plan_time_to = contract.get("loading_plan_time_to", "")

        replacements["loading_plan_date"] = self._format_date_full(loading_plan_date)
        replacements["unloading_plan_date"] = self._format_date_full(unloading_plan_date)
        replacements["loading_plan_time_from"] = loading_plan_time_from
        replacements["loading_plan_time_to"] = loading_plan_time_to
        replacements["loading_plan_time_full"] = (
            f"с {loading_plan_time_from} по {loading_plan_time_to}"
            if loading_plan_time_from and loading_plan_time_to else ""
        )

        # ── Стоимость и НДС ──
        price_without_vat = float(contract.get("price_without_vat", 0) or 0)

        if is_ip_without_vat or vat_rate_num <= 0:
            nds_amount = 0.0
            total_amount = price_without_vat
            replacements["sum_wo_nds"] = f"{price_without_vat:.2f}"
            replacements["sum_wo_nds_words"] = amount_to_words(price_without_vat)
            replacements["nds_text"] = "НДС не облагается"
            replacements["sum_nds"] = ""
            replacements["sum_nds_words"] = ""
            replacements["sum_total"] = f"{total_amount:.2f}"
            replacements["sum_total_words"] = amount_to_words(total_amount)
        else:
            nds_amount = round(price_without_vat * vat_rate_num / 100, 2)
            total_amount = round(price_without_vat + nds_amount, 2)

            replacements["sum_wo_nds"] = f"{price_without_vat:.2f}"
            replacements["sum_wo_nds_words"] = amount_to_words(price_without_vat)

            replacements["sum_nds"] = f"{nds_amount:.2f}"
            replacements["sum_nds_words"] = amount_to_words(nds_amount)

            replacements["nds_text"] = (
                f"НДС по ставке, действующей на дату оказания услуг "
                f"(в настоящее время {vat_rate_num:.0f}%) — {nds_amount:.2f} руб."
            )

            replacements["sum_total"] = f"{total_amount:.2f}"
            replacements["sum_total_words"] = amount_to_words(total_amount)

        replacements["nds_status_text"] = nds_status_text
        replacements["vat_rate"] = f"{vat_rate_num:.0f}%"

        logger.info(
            f"Итоговые суммы: без НДС={price_without_vat:.2f}, "
            f"НДС={nds_amount:.2f} ({vat_rate_num:.0f}%), итого={total_amount:.2f}"
        )

        payment_days = contract.get("payment_days", 10)
        replacements["payment_days"] = str(payment_days)
        replacements["payment_days_words"] = self._days_to_words(payment_days)
        replacements["penalty_rate"] = "5000"

        logger.debug(f"Сформировано {len(replacements)} плейсхолдеров")
        return replacements

    # ─────────────────────────────────────────────────────────
    # Краткая метка точки для таблицы ТС
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _get_point_label(index: Any, points: List[Dict[str, Any]], title: str) -> str:
        try:
            idx = int(index or 0)
        except (ValueError, TypeError):
            idx = 0

        if idx == 0:
            return "—"
        if 1 <= idx <= len(points):
            return f"{title} {idx}"
        return "—"