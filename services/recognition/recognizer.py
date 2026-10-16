#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль распределения распознанных данных по полям интерфейса.
Поддерживает МНОЖЕСТВЕННЫЕ места погрузки и выгрузки.
Обрабатывает привязку машин к точкам (loading_index / unloading_index).

Год выпуска ТС: если не указан — 0 (UI сам подставит текущий год).

FALLBACK: если Ollama потеряла VIN при генерации JSON,
добираем их регексом из исходного текста.
"""

import logging
import re
from typing import Dict, Any, Optional, List

from core.dates import to_iso
from core.trace import filled_fields_summary, trace

logger = logging.getLogger("core.recognizer")

#: Кириллические буквы, неотличимые на вид от латинских, и их латинская пара.
#:
#: Зачем: VIN набирают то латиницей, то кириллицей, и в одном VIN встречаются
#: обе раскладки. Последствия видны в самом начале пути: обезличиватель
#: (core/pseudonymizer.py) ищет VIN латинским шаблоном, поэтому VIN с
#: кириллической буквой в модель уходит ОТКРЫТЫМ, плейсхолдер ему не выдаётся.
#: Дальше модель видит шесть строк, но пять плейсхолдеров — и отвечает пятью
#: машинами со съехавшими марками ровно так, как это видел оператор.
#:
#: Кириллических «похожих» букв больше, чем латинских букв в VIN, поэтому
#: таблица ведёт часть кириллицы в цифры: «О» читается и как O, и как 0,
#: но латинская O в VIN запрещена, а «З» — это 3.
_VIN_LOOKALIKES = {
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M",
    "Р": "P", "Т": "T", "У": "Y", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S",
    "О": "0", "З": "3", "Ч": "4", "Б": "6",
}

#: Символы, которые в VIN запрещены стандартом (ISO 3779: I, O, Q).
_VIN_FORBIDDEN = frozenset("IOQ")

#: Служебные символы, которые модель или OCR могут вставить внутрь VIN.
_VIN_SEPARATORS = re.compile(r"[\s\-–—_.,;:|/\\]+")


def _vin_char_class() -> str:
    """Класс символов «буква VIN»: латиница + кириллические двойники."""
    return "A-HJ-NPR-Za-hj-npr-z0-9" + "".join(_VIN_LOOKALIKES)


#: Кандидат в VIN в ИСХОДНОМ тексте: 17 символов, латиница ИЛИ кириллица.
#: Границы — не буква/цифра: иначе «…TC008647LX» склеилось бы в один токен.
_VIN_SOURCE_RE = re.compile(
    rf"(?<![A-Za-z0-9{''.join(_VIN_LOOKALIKES)}])"
    rf"([{_vin_char_class()}]{{17}})"
    rf"(?![A-Za-z0-9{''.join(_VIN_LOOKALIKES)}])"
)


class DataMapper:
    """Преобразует JSON от Ollama в заполненные поля интерфейса."""

    DRIVER_FIELDS = {
        "full_name": "full_name",
        "birth_date": "birth_date",
        "birth_place": "birth_place",
        "passport_series": "passport_series",
        "passport_number": "passport_number",
        "passport_issue_date": "passport_issue_date",
        "passport_issuer": "passport_issuer",
        "passport_code": "passport_code",
        "registration_address": "registration_address",
        "license_series": "license_series",
        "license_number": "license_number",
        "license_issue_date": "license_issue_date",
        "license_expiry_date": "license_expiry_date",
        "license_categories": "license_categories",
        "phone": "phone",
    }

    ORGANIZATION_FIELDS = [
        "full_name", "short_name", "inn", "kpp", "ogrn",
        "legal_address", "actual_address", "bank_account",
        "bik", "correspondent_account", "bank_name",
        "director_name", "director_position", "phone", "email",
    ]

    CARRIER_EXTRA_FIELDS = ["license_number", "license_date"]

    # VIN-паттерн (стандартный)
    VIN_PATTERN = re.compile(r'\b([A-HJ-NPR-Z0-9]{17})\b')

    @staticmethod
    def _clean_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            if value.lower() in ("null", "none", "nan", "undefined", "nil"):
                return ""
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
        return str(value).strip()

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> str:
        """
        Дата в ISO-формате (ГГГГ-ММ-ДД).

        Разбор общий — core.dates.parse_date (раньше здесь был пятый
        дубль парсера дат). Пустое/неразобранное значение → "".
        """
        if not date_str:
            return ""
        return to_iso(date_str)

    @staticmethod
    def _parse_inn(inn_str: Optional[str]) -> str:
        if not inn_str:
            return ""
        digits = "".join(c for c in str(inn_str) if c.isdigit())
        if len(digits) in (10, 12):
            return digits
        # Реквизиты в лог не пишем: только длины.
        logger.warning(
            f"ИНН имеет неверную длину: было {len(str(inn_str))}, "
            f"осталось цифр {len(digits)} (ожидается 10 или 12)"
        )
        return digits if digits else ""

    @staticmethod
    def _parse_kpp(kpp_str: Optional[str]) -> str:
        if not kpp_str:
            return ""
        digits = "".join(c for c in str(kpp_str) if c.isdigit())
        if len(digits) == 9:
            return digits
        logger.warning(
            f"КПП имеет неверную длину: было {len(str(kpp_str))}, "
            f"осталось цифр {len(digits)} (ожидается 9)"
        )
        return digits if digits else ""

    @staticmethod
    def _parse_ogrn(ogrn_str: Optional[str]) -> str:
        if not ogrn_str:
            return ""
        digits = "".join(c for c in str(ogrn_str) if c.isdigit())
        if len(digits) in (13, 15):
            return digits
        logger.warning(
            f"ОГРН/ОГРНИП имеет неверную длину: было {len(str(ogrn_str))}, "
            f"осталось цифр {len(digits)} (ожидается 13 или 15)"
        )
        return digits if digits else ""

    @staticmethod
    def _parse_plate(plate_str: Optional[str]) -> str:
        if not plate_str:
            return ""
        plate = str(plate_str).upper().replace(" ", "").replace("-", "")
        if plate.lower() in ("null", "none"):
            return ""
        return plate

    @staticmethod
    def _parse_year(year_val: Any) -> int:
        """
        Парсит год выпуска.
        Возвращает 0, если год не указан — UI сам подставит текущий год.
        """
        if year_val is None:
            return 0
        if isinstance(year_val, bool):
            return 0
        if isinstance(year_val, str):
            s = year_val.strip()
            if s.lower() in ("null", "none", "undefined", "0", ""):
                return 0
            digits = "".join(c for c in s if c.isdigit())
            if digits:
                try:
                    y = int(digits[:4])
                    return y if 1900 <= y <= 2100 else 0
                except ValueError:
                    return 0
        if isinstance(year_val, (int, float)):
            y = int(year_val)
            return y if 1900 <= y <= 2100 else 0
        return 0

    @staticmethod
    def _parse_index(value: Any) -> int:
        """
        Парсит индекс точки (0..10).
        0 = не привязано (машина во всех точках).
        """
        if value is None or value == "":
            return 0
        if isinstance(value, bool):
            return 0
        if isinstance(value, (int, float)):
            i = int(value)
            return i if 0 <= i <= 10 else 0
        if isinstance(value, str):
            s = value.strip()
            if s.isdigit():
                i = int(s)
                return i if 0 <= i <= 10 else 0
        return 0

    @staticmethod
    def _normalize_vehicle_type(vt: Optional[str]) -> str:
        if not vt:
            return "Легковой автомобиль"
        vt_lower = str(vt).lower().strip()
        if vt_lower in ("null", "none", "undefined"):
            return "Легковой автомобиль"
        if "прицеп" in vt_lower:
            return "Прицеп"
        if "фургон" in vt_lower or "автобус" in vt_lower:
            return "Фургон"
        if "тягач" in vt_lower or "седл" in vt_lower:
            return "Тягач"
        return "Легковой автомобиль"

    @staticmethod
    def _parse_vat_rate(vat_str: Any) -> str:
        if vat_str is None:
            return "22%"
        vat = str(vat_str).replace("%", "").strip()
        if vat.lower() in ("null", "none", "undefined"):
            return "22%"
        if vat.isdigit():
            return f"{vat}%"
        return "22%"

    @staticmethod
    def _split_brand_and_vin(brand_model: str, vin: str = "") -> tuple:
        if not brand_model:
            return brand_model, vin
        if vin:
            return brand_model, vin
        if " - " in brand_model:
            parts = brand_model.split(" - ")
            brand = parts[0].strip()
            if len(parts) > 1:
                vin = parts[1].strip()
            return brand, vin
        vin_pattern = r'([A-HJ-NPR-Z0-9]{17})$'
        match = re.search(vin_pattern, brand_model)
        if match:
            vin = match.group(1)
            brand = brand_model[:match.start()].strip()
            return brand, vin
        return brand_model, vin

    @staticmethod
    def _split_multi_address(point: Dict[str, str]) -> List[Dict[str, str]]:
        address = point.get("address", "")
        if not address:
            return [point]

        parts = re.split(
            r"[\n;|•]+|\s*[Пп]огрузка\s*\d+[:.]|\s*[Вв]ыгрузка\s*\d+[:.]|\s*[Мм]есто\s*\d+[:.]",
            address
        )
        parts = [p.strip(" -–—:") for p in parts if p and p.strip(" -–—:")]

        if len(parts) <= 1:
            return [point]

        result = []
        for p in parts:
            result.append({
                "address": p,
                "date": point.get("date", ""),
                "time_window": point.get("time_window", ""),
            })
        return result

    # ─────────────────────────────────────────────────────────
    # FALLBACK ПО VIN
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def normalize_vin_lookalikes(text: Optional[str]) -> str:
        """
        Приводит VIN в тексте к латинице: «LVТDD24B0TDC48177» → «LVTDD24B0TDC48177».

        Заменяются ТОЛЬКО 17-символьные токены, похожие на VIN, — остальной
        текст не трогается. Дефис и пробелы внутри такого токена убираются:
        так VIN, разорванный переносом или набранный «группами», снова целый.

        Вызывается ДО отправки текста в модель: иначе VIN с кириллической
        буквой не маскируется (шаблон обезличивателя латинский), уходит в
        модель открытым, плейсхолдера не получает — и строк в ответе модели
        оказывается меньше, чем строк в тексте (проверено живьём: 6 строк →
        5 машин и сдвиг марок на строку вверх).
        """
        if not text:
            return "" if text is None else str(text)

        def replace(match: "re.Match") -> str:
            return DataMapper.normalize_vin(match.group(1))

        return _VIN_SOURCE_RE.sub(replace, str(text))

    @staticmethod
    def normalize_vin(value: Any) -> str:
        """
        Нормализует один VIN: верхний регистр, без пробелов и разделителей.

        Буквы-двойники переводятся в латиницу: русская «Т» в VIN — это
        латинская «T», а не отдельная буква. Если после чистки остаётся
        ровно 17 символов, значение и есть VIN; иначе возвращается то, что
        получилось (проверка «похоже ли на VIN» — отдельно, см. vin_is_valid).
        """
        if value is None:
            return ""
        cleaned = _VIN_SEPARATORS.sub("", str(value)).upper()
        return "".join(_VIN_LOOKALIKES.get(char, char) for char in cleaned)

    @staticmethod
    def vin_is_valid(vin: Any) -> bool:
        """
        Похоже ли значение на настоящий VIN.

        VIN — ровно 17 символов латиницы и цифр, без I, O и Q. Всё остальное
        (короче, длиннее, с русскими буквами или пробелами) — ненадёжно:
        такую ячейку программа подсвечивает оператору, а не молча печатает
        в договор.
        """
        value = "" if vin is None else str(vin).strip().upper()
        if len(value) != 17:
            return False
        if _VIN_FORBIDDEN & set(value):
            return False
        return bool(re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", value))

    @classmethod
    def _find_vins_in_text(cls, text: str) -> List[str]:
        """
        Находит все уникальные VIN в исходном тексте, сохраняя порядок.

        VIN с кириллическими буквами тоже находятся: без этого строка
        терялась целиком (её не видел ни шаблон, ни fallback).
        """
        if not text:
            return []

        found = [cls.normalize_vin(match) for match in _VIN_SOURCE_RE.findall(text)]

        seen = set()
        unique = []
        for v in found:
            if v not in seen:
                seen.add(v)
                unique.append(v)

        return unique

    @classmethod
    def _extract_brand_for_vin(cls, text: str, vin: str) -> str:
        """
        Пытается найти марку/модель машины рядом с VIN в исходном тексте.
        Поддерживает форматы:
          "JETOUR T2 2.0Т 7DCT Престиж\tEC3TEUMB0T0002608"
          "JETOUR T2 2.0Т 7DCT Престиж - EC3TEUMB0T0002608"
          "1. JETOUR T2 - EC3TEUMB0T0002608"
          "EC3TEUMB0T0002608 — JETOUR T2"
        """
        if not text or not vin:
            return ""

        # И текст, и искомый VIN приводятся к латинице: иначе строка с
        # кириллической буквой не находится вовсе.
        vin_upper = cls.normalize_vin(vin)
        lines = cls.normalize_vin_lookalikes(text).split("\n")

        for line in lines:
            if vin_upper not in line.upper():
                continue

            line_clean = line.strip()

            # ── Вариант: "Марка\tVIN" или "Марка;VIN" или "Марка|VIN" ──
            parts = re.split(r'[\t;|]', line_clean)
            if len(parts) >= 2:
                for part in parts:
                    part_stripped = part.strip()
                    if not part_stripped:
                        continue
                    if vin_upper in part_stripped.upper():
                        # Это VIN — не марка
                        continue
                    # Это марка
                    brand = re.sub(r'^\d+[.)]\s*', '', part_stripped).strip()
                    brand = brand.strip(" -–—:")
                    if brand:
                        return brand

            # ── Вариант: "Марка - VIN" ──
            if " - " in line_clean:
                parts = line_clean.split(" - ")
                for part in parts:
                    part_stripped = part.strip()
                    if vin_upper in part_stripped.upper():
                        continue
                    brand = re.sub(r'^\d+[.)]\s*', '', part_stripped).strip()
                    brand = brand.strip(" -–—:")
                    if brand:
                        return brand

            # ── Вариант: "VIN — Марка" ──
            if " — " in line_clean or " – " in line_clean:
                parts = re.split(r'\s[—–]\s', line_clean)
                for part in parts:
                    part_stripped = part.strip()
                    if vin_upper in part_stripped.upper():
                        continue
                    brand = re.sub(r'^\d+[.)]\s*', '', part_stripped).strip()
                    brand = brand.strip(" -–—:")
                    if brand:
                        return brand

            # ── Вариант: "Марка   VIN" (несколько пробелов) ──
            parts = re.split(r'\s{2,}', line_clean)
            if len(parts) >= 2:
                for part in parts:
                    part_stripped = part.strip()
                    if vin_upper in part_stripped.upper():
                        continue
                    brand = re.sub(r'^\d+[.)]\s*', '', part_stripped).strip()
                    brand = brand.strip(" -–—:")
                    if brand:
                        return brand

            # ── Вариант: VIN в конце строки, всё до него — марка ──
            idx = line_clean.upper().find(vin_upper)
            if idx > 0:
                brand = line_clean[:idx].strip()
                brand = brand.strip(" -–—:\t;|")
                brand = re.sub(r'^\d+[.)]\s*', '', brand).strip()
                if brand:
                    return brand

            # ── Вариант: VIN в начале строки, всё после него — марка ──
            if idx == 0:
                brand = line_clean[len(vin):].strip()
                brand = brand.strip(" -–—:\t;|")
                if brand:
                    return brand

        return ""

    @classmethod
    def _recover_missing_vins(
        cls,
        recognized: List[Dict[str, Any]],
        source_text: str,
    ) -> List[Dict[str, Any]]:
        """
        FALLBACK: если Ollama потеряла VIN при генерации JSON,
        добираем их из исходного текста регексом.
        """
        if not source_text:
            return recognized

        # VIN'ы из исходного текста (в порядке появления)
        source_vins = cls._find_vins_in_text(source_text)

        if not source_vins:
            return recognized

        # VIN'ы, которые уже распознаны. Нормализуем так же, как исходный
        # текст: «LVТDD24B0TDC48177» с кириллицей — это тот же VIN, что и
        # латиницей, и терять его как «отсутствующий» нельзя.
        recognized_vins = {
            cls.normalize_vin(v.get("vin")) for v in recognized if v.get("vin")
        }

        # Каких не хватает
        missing_vins = [v for v in source_vins if v not in recognized_vins]

        if not missing_vins:
            logger.debug(f"Fallback по VIN: все {len(source_vins)} VIN на месте")
            return recognized

        logger.warning(
            f"Модель потеряла {len(missing_vins)} VIN из {len(source_vins)}. "
            f"Добираем из исходного текста."
        )

        for vin in missing_vins:
            brand = cls._extract_brand_for_vin(source_text, vin)
            recognized.append({
                "vin": vin,
                "brand_model": brand,
                "plate_number": "",
                "year": 0,
                "color": "",
                # Тип ТС в тексте не назван — оставляем пустым. Подставлять
                # «Легковой автомобиль» нельзя: это выглядело бы как данные,
                # введённые оператором (ШАГ FIX-6, часть C).
                "vehicle_type": "",
                "loading_index": 0,
                "unloading_index": 0,
            })

        # ── Сортируем в порядке появления в исходном тексте ──
        order_map = {v: i for i, v in enumerate(source_vins)}
        recognized.sort(
            key=lambda v: order_map.get(cls.normalize_vin(v.get("vin")), 9999)
        )

        logger.info(f"После fallback: {len(recognized)} машин")
        return recognized

    @classmethod
    def vehicle_rows_from_text(cls, source_text: str) -> List[Dict[str, str]]:
        """
        Список ТС, собранный ИЗ САМОГО ТЕКСТА: «вин» и «марка» построчно.

        Строка считается строкой списка ТС, только если в ней есть VIN:
        марка — всё, что стоит в этой же строке ДО него. Строки без VIN
        (заголовки, примечания, реквизиты) пропускаются.

        Это первоисточник для выравнивания ответа модели: и порядок строк,
        и марка берутся отсюда, а не из ответа. Так марка физически не может
        съехать на строку выше — она привязана к своей строке.
        """
        rows: List[Dict[str, str]] = []
        if not source_text:
            return rows

        for line in cls.normalize_vin_lookalikes(source_text).splitlines():
            match = _VIN_SOURCE_RE.search(line)
            if not match:
                continue
            brand = line[:match.start()].strip(" \t-–—:;|,.")
            # Нумерация строк («1.», «12)») — не часть марки.
            brand = re.sub(r"^\d+[.)]\s*", "", brand).strip()
            rows.append({"vin": cls.normalize_vin(match.group(1)),
                         "brand_model": brand})
        return rows

    @classmethod
    def repair_vehicle_list(
        cls,
        recognized: List[Dict[str, Any]],
        source_text: str,
    ) -> List[Dict[str, Any]]:
        """
        Приводит список ТС в соответствие с исходным текстом.

        Что делает (по порядку):
          1. VIN каждой строки — на латиницу, без пробелов и разделителей;
          2. если исходный текст — список ТС по строкам, марка берётся ИЗ
             ТОЙ ЖЕ строки текста (по VIN), а не из ответа модели;
          3. VIN, которых в ответе нет, добавляются на своё место.

        Почему марка берётся из текста. Модель привязывает марку не к VIN,
        а к порядковому номеру строки: потеряв одну машину, она сдвигает
        марки всех следующих строк на строку вверх — оператор видит в строке 3
        чужую марку. VIN же в ответе остаётся верным (это отдельный
        плейсхолдер), поэтому VIN — надёжный ключ, а порядок — нет.

        Текст без VIN (обычный договор, реквизиты) возвращается как есть:
        списка строк там нет, и восстанавливать нечего.
        """
        if not recognized:
            return recognized

        # ── 1. Нормализация VIN ──
        normalized: List[Dict[str, Any]] = []
        for vehicle in recognized:
            item = dict(vehicle)
            vin = cls._clean_value(item.get("vin"))
            if vin:
                item["vin"] = cls.normalize_vin(vin)
            normalized.append(item)

        repaired = cls._recover_missing_vins(normalized, source_text)
        if not source_text:
            return repaired

        rows = cls.vehicle_rows_from_text(source_text)
        if not rows:
            # VIN в тексте не нашлось — выравнивать не по чему.
            return repaired

        order_map = {row["vin"]: index for index, row in enumerate(rows)}
        brand_map = {row["vin"]: row["brand_model"] for row in rows}

        # ── 2. Марка — из строки своего VIN ──
        # Порядок строк приводим к тексту, когда он сопоставим: у каждой
        # машины из ответа есть свой VIN в тексте, и машин не больше, чем
        # строк. Если модель выдумала VIN, которого в тексте нет, порядок
        # не трогаем — сопоставлять его с текстом больше не с чем.
        recognized_vins = {cls.normalize_vin(v.get("vin")) for v in recognized}
        if (recognized_vins
                and recognized_vins <= set(order_map)
                and len(repaired) <= len(rows)):
            repaired.sort(
                key=lambda v: order_map.get(cls.normalize_vin(v.get("vin")), 9999)
            )
            logger.info(
                "Список ТС выровнен по строкам текста: строк %d, машин в ответе %d",
                len(rows), len(recognized_vins),
            )

        changed_brands = 0
        for vehicle in repaired:
            vin = cls.normalize_vin(vehicle.get("vin"))
            brand = brand_map.get(vin, "")
            if not brand:
                continue
            if cls._clean_value(vehicle.get("brand_model")) == brand:
                continue
            if cls._clean_value(vehicle.get("brand_model")):
                changed_brands += 1
            vehicle["brand_model"] = brand

        if len(repaired) != len(rows):
            logger.warning(
                "Список ТС: строк в тексте %d, машин в ответе %d — проверьте таблицу",
                len(rows), len(repaired),
            )
        if changed_brands:
            logger.info(
                "Список ТС: марка восстановлена по строке текста у %d машин "
                "(в ответе модели марки были сдвинуты)",
                changed_brands,
            )
        return repaired

    @classmethod
    def vehicle_line_count(cls, source_text: str) -> int:
        """
        Сколько непустых строк в исходном тексте.

        Нужно для проверки «строк в тексте N — машин должно быть N»:
        и в постобработке, и в интерфейсе (подсказка оператору).
        """
        if not source_text:
            return 0
        return len([line for line in str(source_text).splitlines() if line.strip()])

    # ─────────────────────────────────────────────────────────
    # Водитель
    # ─────────────────────────────────────────────────────────

    @classmethod
    def map_driver_data(cls, driver_data: Dict[str, Any]) -> Dict[str, Any]:
        if not driver_data:
            logger.warning("Данные водителя пусты")
            return {}

        result = {}
        for json_key, ui_key in cls.DRIVER_FIELDS.items():
            value = driver_data.get(json_key, "")
            result[ui_key] = cls._clean_value(value)

        result["birth_date"] = cls._parse_date(result.get("birth_date"))
        result["passport_issue_date"] = cls._parse_date(result.get("passport_issue_date"))
        result["license_issue_date"] = cls._parse_date(result.get("license_issue_date"))
        result["license_expiry_date"] = cls._parse_date(result.get("license_expiry_date"))
        result["license_categories"] = cls._clean_value(driver_data.get("license_categories"))

        passport = cls._clean_value(driver_data.get("passport_series")) + " " + cls._clean_value(driver_data.get("passport_number"))
        if passport.strip():
            result["passport_full"] = passport.strip()

        logger.info(f"Данные водителя распознаны: {len(result)} полей")
        logger.debug(
            f"Распознавание водителя: {filled_fields_summary(result)}"
        )
        return result

    # ─────────────────────────────────────────────────────────
    # Организации
    # ─────────────────────────────────────────────────────────

    @classmethod
    def map_organization_data(cls, org_data: Dict[str, Any], is_carrier: bool = False) -> Dict[str, Any]:
        if not org_data:
            logger.warning("Данные организации пусты")
            return {}

        result = {}
        for field in cls.ORGANIZATION_FIELDS:
            value = org_data.get(field, "")
            result[field] = cls._clean_value(value)

        result["inn"] = cls._parse_inn(result.get("inn"))
        result["kpp"] = cls._parse_kpp(result.get("kpp"))
        result["ogrn"] = cls._parse_ogrn(result.get("ogrn"))

        entity_type = cls._clean_value(org_data.get("entity_type", ""))
        full_name = result.get("full_name", "").lower()

        if not entity_type:
            if "ип " in full_name or full_name.startswith("ип") or "индивидуальный предприниматель" in full_name:
                entity_type = "ИП"
            elif "ооо" in full_name or "общество с ограниченной ответственностью" in full_name:
                entity_type = "ООО"

        result["entity_type"] = entity_type

        if entity_type == "ИП":
            ogrnip = cls._clean_value(org_data.get("ogrnip", ""))
            ogrnip = cls._parse_ogrn(ogrnip)

            if ogrnip:
                result["ogrn"] = ogrnip
            else:
                ogrn = result.get("ogrn", "")
                if ogrn and len(ogrn) == 15:
                    result["ogrn"] = ogrn

            result["kpp"] = ""

            if not result.get("director_name"):
                director_name = result.get("full_name", "")
                for prefix in ["Индивидуальный предприниматель ", "ИП "]:
                    if director_name.startswith(prefix):
                        director_name = director_name[len(prefix):]
                        break
                result["director_name"] = director_name.strip()

            if not result.get("director_position"):
                result["director_position"] = "Индивидуальный предприниматель"

        if is_carrier:
            result["license_number"] = cls._clean_value(org_data.get("license_number"))
            result["license_date"] = cls._parse_date(org_data.get("license_date"))

        logger.info(f"Данные организации распознаны: {len(result)} полей, тип={entity_type}")
        logger.debug(
            f"Распознавание организации (is_carrier={is_carrier}): "
            f"{filled_fields_summary(result)}"
        )
        return result

    # ─────────────────────────────────────────────────────────
    # Транспортные средства
    # ─────────────────────────────────────────────────────────

    @classmethod
    def map_vehicles_data(cls, vehicles_data: list) -> list:
        if not vehicles_data:
            logger.warning("Данные ТС пусты")
            return []

        vehicles = []
        for vehicle in vehicles_data:
            if not isinstance(vehicle, dict):
                continue

            brand_model = cls._clean_value(vehicle.get("brand_model"))
            vin = cls._clean_value(vehicle.get("vin"))

            brand_model, vin = cls._split_brand_and_vin(brand_model, vin)

            loading_index = cls._parse_index(vehicle.get("loading_index"))
            unloading_index = cls._parse_index(vehicle.get("unloading_index"))

            v = {
                "vin": vin,
                "brand_model": brand_model,
                "plate_number": cls._parse_plate(vehicle.get("plate_number")),
                "year": cls._parse_year(vehicle.get("year")),
                "color": cls._clean_value(vehicle.get("color")),
                "vehicle_type": cls._normalize_vehicle_type(vehicle.get("vehicle_type")),
                "loading_index": loading_index,
                "unloading_index": unloading_index,
            }

            if v["vin"] or v["brand_model"]:
                vehicles.append(v)

        logger.info(f"Распознано ТС: {len(vehicles)}")
        for i, v in enumerate(vehicles, 1):
            logger.debug(
                f"  ТС {i}: VIN={v['vin']!r}, brand={v['brand_model']!r}, "
                f"year={v['year']}, "
                f"loading_index={v['loading_index']}, unloading_index={v['unloading_index']}"
            )
        return vehicles

    # ─────────────────────────────────────────────────────────
    # Тягач / Прицеп
    # ─────────────────────────────────────────────────────────

    @classmethod
    def map_tractor_data(cls, tractor_data: Dict[str, Any]) -> Dict[str, Any]:
        if not tractor_data:
            return {}
        return {
            "brand_model": cls._clean_value(tractor_data.get("brand_model")),
            "plate_number": cls._parse_plate(tractor_data.get("plate_number")),
            "color": cls._clean_value(tractor_data.get("color")),
            "year": cls._parse_year(tractor_data.get("year")),
        }

    @classmethod
    def map_trailer_data(cls, trailer_data: Dict[str, Any]) -> Dict[str, Any]:
        if not trailer_data:
            return {}
        return {
            "brand_model": cls._clean_value(trailer_data.get("brand_model")),
            "plate_number": cls._parse_plate(trailer_data.get("plate_number")),
            "color": cls._clean_value(trailer_data.get("color")),
            "year": cls._parse_year(trailer_data.get("year")),
        }

    # ─────────────────────────────────────────────────────────
    # Договор
    # ─────────────────────────────────────────────────────────

    @classmethod
    @trace
    def map_contract_data(cls, contract_data: Dict[str, Any]) -> Dict[str, Any]:
        if not contract_data:
            return {}

        logger.debug(
            f"Вход в map_contract_data: полей {len(contract_data)} "
            f"({sorted(contract_data.keys())})"
        )

        result = {}
        result["number"] = cls._clean_value(contract_data.get("number"))
        result["date"] = cls._parse_date(contract_data.get("date"))
        result["route"] = cls._clean_value(contract_data.get("route"))

        # ── Погрузки ──
        raw_loadings = contract_data.get("loadings")
        loadings: List[Dict[str, str]] = []

        if isinstance(raw_loadings, list):
            for l in raw_loadings:
                if not isinstance(l, dict):
                    continue
                addr = cls._clean_value(l.get("address"))
                if not addr:
                    continue
                loadings.append({
                    "address": addr,
                    "date": cls._parse_date(l.get("date")),
                    "time_window": cls._clean_value(l.get("time_window")),
                })

        if not loadings and contract_data.get("loading_address"):
            loadings = [{
                "address": cls._clean_value(contract_data.get("loading_address")),
                "date": cls._parse_date(contract_data.get("loading_date")),
                "time_window": cls._clean_value(contract_data.get("loading_time_window")),
            }]

        if len(loadings) == 1 and loadings[0]["address"]:
            split = cls._split_multi_address(loadings[0])
            if len(split) > 1:
                loadings = split

        seen = set()
        loadings_unique = []
        for l in loadings:
            key = l["address"].strip().lower()
            if key and key not in seen:
                seen.add(key)
                loadings_unique.append(l)
        loadings = loadings_unique

        result["loadings"] = loadings

        # ── Выгрузки ──
        raw_unloadings = contract_data.get("unloadings")
        unloadings: List[Dict[str, str]] = []

        if isinstance(raw_unloadings, list):
            for u in raw_unloadings:
                if not isinstance(u, dict):
                    continue
                addr = cls._clean_value(u.get("address"))
                if not addr:
                    continue
                unloadings.append({
                    "address": addr,
                    "date": cls._parse_date(u.get("date")),
                    "time_window": cls._clean_value(u.get("time_window")),
                })

        if not unloadings:
            legacy = []
            for key in ("unloading_address_1", "unloading_address_2"):
                addr = cls._clean_value(contract_data.get(key))
                if addr:
                    legacy.append({
                        "address": addr,
                        "date": cls._parse_date(contract_data.get("unloading_date")),
                        "time_window": cls._clean_value(contract_data.get("unloading_time_window")),
                    })
            unloadings = legacy

        if len(unloadings) == 1 and unloadings[0]["address"]:
            split = cls._split_multi_address(unloadings[0])
            if len(split) > 1:
                unloadings = split

        seen = set()
        unloadings_unique = []
        for u in unloadings:
            key = u["address"].strip().lower()
            if key and key not in seen:
                seen.add(key)
                unloadings_unique.append(u)
        unloadings = unloadings_unique

        result["unloadings"] = unloadings

        # ── Алиасы ──
        if loadings:
            result["loading_address"] = loadings[0]["address"]
            result["loading_date"] = loadings[0]["date"]
            result["loading_time_window"] = loadings[0]["time_window"]
        else:
            result["loading_address"] = ""
            result["loading_date"] = ""
            result["loading_time_window"] = ""

        result["unloading_address_1"] = unloadings[0]["address"] if len(unloadings) > 0 else ""
        result["unloading_address_2"] = unloadings[1]["address"] if len(unloadings) > 1 else ""
        if unloadings:
            result["unloading_address"] = unloadings[-1]["address"]
            result["unloading_date"] = unloadings[-1]["date"]
            result["unloading_time_window"] = unloadings[-1]["time_window"]
        else:
            result["unloading_address"] = ""
            result["unloading_date"] = ""
            result["unloading_time_window"] = ""

        # ── Стоимость ──
        try:
            price = float(contract_data.get("price_without_vat", 0) or 0)
            result["price_without_vat"] = price
        except (ValueError, TypeError):
            result["price_without_vat"] = 0.0

        result["vat_rate"] = cls._parse_vat_rate(contract_data.get("vat_rate"))

        payment_days = contract_data.get("payment_days", 10)
        try:
            result["payment_days"] = int(payment_days)
        except (ValueError, TypeError):
            result["payment_days"] = 10

        result["special_conditions"] = cls._clean_value(contract_data.get("special_conditions"))

        # Адреса погрузок/выгрузок — персональные данные, в лог идут только
        # количества и заполненность полей.
        logger.info(
            f"Погрузки после парсинга: {len(loadings)} | "
            f"выгрузки: {len(unloadings)}"
        )
        logger.debug(
            f"Погрузки: {[filled_fields_summary(p) for p in loadings]} | "
            f"выгрузки: {[filled_fields_summary(p) for p in unloadings]}"
        )
        logger.info(
            f"Данные договора распознаны: {len(result)} полей, "
            f"погрузок={len(loadings)}, выгрузок={len(unloadings)}"
        )
        return result

    # ─────────────────────────────────────────────────────────
    # Полная обработка
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _section(value: Any) -> Dict[str, Any]:
        """
        Раздел ответа модели к словарю.

        Модель (и GigaChat Vision, и текстовые промпты) отдаёт ФИО, реквизиты
        и ТС СПИСКОМ: «carrier»: [{...}], «driver»: [{...}] — так удобнее
        описывать несколько людей и машин в одном документе. Маппер же ждёт
        словарь. Раньше на списке вызов падал с
        «AttributeError: 'list' object has no attribute 'get'» посреди
        разбора: результат терялся целиком, а запрос к модели уже был
        потрачен. Здесь форма приводится к ожидаемой: список из одного
        элемента разворачивается, список из нескольких — первый элемент
        (вызывающий код разбирает остальные сам), пустой список и мусор —
        пустой словарь.
        """
        if isinstance(value, dict):
            return value
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, dict):
                    return item
        return {}

    @classmethod
    @trace
    def process_full_response(
        cls,
        data: Dict[str, Any],
        source_text: str = "",
    ) -> Dict[str, Any]:
        """
        Обрабатывает полный ответ от Ollama.
        source_text — исходный текст (для fallback по VIN).
        """
        if isinstance(data, (list, tuple)):
            # Ответ целиком списком: у Ollama такое бывает при нескольких
            # документах в одном запросе.
            data = cls._section(data)
        if not isinstance(data, dict):
            data = {}

        vehicles = cls.map_vehicles_data(data.get("vehicles", []))

        # ── Выравнивание по исходному тексту: VIN на латиницу, потерянные
        # строки — на своё место (иначе марки съезжают на строку выше) ──
        if source_text:
            vehicles = cls.repair_vehicle_list(vehicles, source_text)

        result = {
            "driver": cls.map_driver_data(cls._section(data.get("driver"))),
            "customer": cls.map_organization_data(
                cls._section(data.get("customer")), is_carrier=False),
            "carrier": cls.map_organization_data(
                cls._section(data.get("carrier")), is_carrier=True),
            "vehicles": vehicles,
            "tractor": cls.map_tractor_data(cls._section(data.get("tractor"))),
            "trailer": cls.map_trailer_data(cls._section(data.get("trailer"))),
            "contract": cls.map_contract_data(cls._section(data.get("contract"))),
        }

        logger.info(
            f"Полные данные распознаны: driver={len(result['driver'])} полей, "
            f"customer={len(result['customer'])} полей, "
            f"carrier={len(result['carrier'])} полей, "
            f"vehicles={len(result['vehicles'])} шт, "
            f"contract={len(result['contract'])} полей"
        )
        return result


# =============================================================
# Защита ручного ввода от «пустого» распознавания
# =============================================================

def has_content(value: Any) -> bool:
    """Есть ли в значении что-то, кроме пустоты (строки, пробелов, None)."""
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(str(value).strip())


def filled_only(section: Any) -> Dict[str, Any]:
    """
    Оставляет в разделе только непустые поля.

    Зачем: модель по системному промпту возвращает блок «customer» со всеми
    пустыми строками («все реквизиты из текста — это перевозчик»). Раньше
    такой блок считался заполненным, и распознавание стирало заказчика,
    которого пользователь ввёл вручную. Теперь пустые поля отбрасываются,
    и вкладка получает только реально распознанные значения.
    """
    if not isinstance(section, dict):
        return {}
    return {key: value for key, value in section.items() if has_content(value)}


def filled_only_list(items: Any) -> List[Dict[str, Any]]:
    """
    Оставляет только те записи списка, где есть хотя бы одно непустое поле.

    Нужно для таблиц (перевозимые ТС): ответ модели вида [{}] или
    [{«vin»: ""}] не должен очищать уже заполненные строки.
    """
    if not isinstance(items, (list, tuple)):
        return []

    result: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and any(has_content(v) for v in item.values()):
            result.append(item)
    return result
