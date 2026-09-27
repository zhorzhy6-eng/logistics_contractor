#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единый разбор дат (Шаг 3 рефакторинга архитектуры).

До рефакторинга один и тот же парсер был скопирован пять раз:
  * ui/tabs/driver_tab.py::_set_date
  * ui/tabs/carrier_tab.py::_set_date
  * ui/tabs/contract_tab.py::_set_date
  * ui/widgets.py::PasteableDateEdit.setDate и ._on_paste_clicked
  * core/recognizer.py::_parse_date

Теперь разбор один — здесь. Модуль не зависит от PyQt5: он возвращает
datetime, а установку в виджет делает ui/tabs/base_tab.TabMixin.

Поддерживаемые форматы:
    2023-01-26, 26.01.2023, 26/01/2023, 2023.01.26,
    26-01-2023, 20230126, 26 01 2023
Даты со временем («2023-01-26T00:00:00», «2023-01-26 00:00:00») обрезаются.
"""

import logging
from datetime import date, datetime
from typing import Any, Optional

logger = logging.getLogger("core.dates")

DATE_FORMATS = (
    "%Y-%m-%d",   # 2023-01-26 (ISO)
    "%d.%m.%Y",   # 26.01.2023 (русский)
    "%d/%m/%Y",   # 26/01/2023
    "%Y.%m.%d",   # 2023.01.26
    "%d-%m-%Y",   # 26-01-2023
    "%Y%m%d",     # 20230126
    "%d %m %Y",   # 26 01 2023
)

# Значения, которые модели иногда присылают вместо пустой строки
_EMPTY_VALUES = {"", "null", "none", "nan", "undefined", "nil", "yyyy-mm-dd"}

_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _strip_time(value: str) -> str:
    """Убирает время: «2023-01-26T00:00:00» и «2023-01-26 00:00:00»."""
    text = value.strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    elif " " in text and ":" in text:
        text = text.split(" ", 1)[0]
    return text.strip()


def parse_date(value: Any, warn: bool = True) -> Optional[datetime]:
    """
    Разбирает дату из строки/даты/даты-времени.

    :param value: значение из UI, БД или ответа модели
    :param warn: писать ли предупреждение в лог при неудаче
    :return: datetime или None, если разобрать не удалось
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    text = str(value).strip().strip('"').strip()
    if text.lower() in _EMPTY_VALUES:
        return None

    candidate = _strip_time(text)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue

    if warn:
        # Значение может быть произвольной строкой из распознанных данных:
        # в лог идёт только описание, без самого текста.
        if isinstance(value, str):
            shown = f"<строка, {len(value)} символов>"
        else:
            shown = repr(value)
        logger.warning(f"Не удалось распознать дату: {shown}")
    return None


def to_iso(value: Any, default: str = "") -> str:
    """Дата в формате ГГГГ-ММ-ДД (для БД и QDateEdit)."""
    if value in (None, ""):
        return default
    dt = parse_date(value, warn=False)
    return dt.strftime("%Y-%m-%d") if dt else default


def to_display(value: Any, default: str = "") -> str:
    """Дата в формате ДД.ММ.ГГГГ (для отчётов и сообщений)."""
    if value in (None, ""):
        return default
    dt = parse_date(value, warn=False)
    return dt.strftime("%d.%m.%Y") if dt else default


def to_day_month(value: Any, default: str = "") -> str:
    """Дата в формате ДД.ММ (в шаблонах договора дата подачи — без года)."""
    if value in (None, ""):
        return default
    dt = parse_date(value, warn=False)
    return dt.strftime("%d.%m") if dt else default


def month_name(value: Any, default: str = "сентября") -> str:
    """Название месяца в родительном падеже («сентября»)."""
    dt = parse_date(value, warn=False) if value else None
    return _MONTHS_GENITIVE[dt.month - 1] if dt else default


def day_of_month(value: Any, default: str = "") -> str:
    """День месяца двузначной строкой («05»)."""
    dt = parse_date(value, warn=False) if value else None
    return f"{dt.day:02d}" if dt else default
