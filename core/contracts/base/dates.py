#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Форматы дат для шаблонов договоров.

Свободные функции (не методы класса): ни документа, ни данных они не
касаются — только строку даты. Переехали из `core/contracts/
base_generator.py` (Этап 4, 16.10.2026) без изменения логики.

Модуль намеренно НЕ переиспользует `core.utils.dates`: там другой разбор
(список форматов, время, «nan»/«null» как пустота) и другой контракт на
ошибке. Здесь историческое поведение генератора: разбирается ТОЛЬКО
ГГГГ-ММ-ДД, а неразобранное значение возвращается как есть. Смена поведения
изменила бы текст готовых договоров.
"""

from datetime import datetime
from typing import Optional

#: Названия месяцев в родительном падеже — для «23 сентября 2026 г.».
MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

#: Месяц по умолчанию, если дата не задана (историческое поведение бланков).
DEFAULT_MONTH = "сентября"


def parse_iso(date_str) -> Optional[datetime]:
    """
    Разбирает ГГГГ-ММ-ДД (при необходимости обрезая время до 10 символов).

    :return: datetime или None, если значение пустое либо не разобралось
    """
    if not date_str:
        return None
    try:
        return datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def format_date_full(date_str: str) -> str:
    """Дата в формате ДД.ММ.ГГГГ; неразобранное значение — как есть."""
    dt = parse_iso(date_str)
    if dt is None:
        return "" if not date_str else str(date_str)
    return dt.strftime("%d.%m.%Y")


def format_date_dot(date_str: str) -> str:
    """Дата в формате ДД.ММ (год в шаблоне не печатается)."""
    dt = parse_iso(date_str)
    if dt is None:
        return "" if not date_str else str(date_str)
    return dt.strftime("%d.%m")


def day_of_month(date_str: str) -> str:
    """День месяца двузначной строкой («05»)."""
    dt = parse_iso(date_str)
    if dt is None:
        return "" if not date_str else str(date_str)
    return f"{dt.day:02d}"


def month_name(date_str: str) -> str:
    """Название месяца в родительном падеже; без даты — «сентября»."""
    dt = parse_iso(date_str)
    if dt is None:
        return DEFAULT_MONTH
    return MONTHS_GENITIVE[dt.month - 1]
