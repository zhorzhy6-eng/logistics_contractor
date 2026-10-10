#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ставки НДС и форма стороны — правила, общие для интерфейса и генератора.

Модуль — часть ЯДРА: ни Qt, ни базы, ни шаблонов он не знает. Здесь лежат
только правила, по которым интерфейс считает суммы, а генератор выбирает
бланк и печатает оговорку про налог.

Ставки (с 01.01.2026, Федеральный закон от 28.11.2025 № 425-ФЗ):

  * «22%»      — базовая (ООО и ИП на ОСН);
  * «10%»      — социально значимые товары;
  * «0%»       — экспорт и международные перевозки (ставка ЕСТЬ, налог 0);
  * «5%»       — УСН, доход 20–272,5 млн ₽ (п. 8 ст. 164 НК);
  * «7%»       — УСН, доход 272,5–490,5 млн ₽ (п. 8 ст. 164 НК);
  * «Без НДС»  — УСН с доходом до 20 млн ₽ (п. 1 ст. 145 НК): налогом
                 услуги НЕ облагаются, в договоре пишется «НДС не облагается».

Разница между «0%» и «Без НДС» — не формальность: в первом случае
перевозчик плательщик НДС и печатает ставку, во втором — не плательщик, и
ставку печатать нечем. Поэтому это два разных пункта списка, а не одно
значение «ноль».
"""

from typing import Any, Optional, Tuple

__all__ = [
    "VAT_FREE",
    "VAT_RATES",
    "DEFAULT_VAT_RATE",
    "ENTITY_TYPES",
    "DEFAULT_ENTITY_TYPE",
    "is_vat_free",
    "vat_rate_number",
    "vat_rate_label",
    "normalize_vat_rate",
    "compute_vat",
    "compute_carrier_type",
    "split_carrier_type",
    "normalize_entity_type",
]

#: «Налогом не облагается» — отдельное значение, а не ставка 0 %.
VAT_FREE = "Без НДС"

#: Порядок пунктов в выпадающем списке: от «без налога» к базовой ставке.
VAT_RATES = (VAT_FREE, "0%", "5%", "7%", "10%", "22%")

#: Ставка по умолчанию — базовая.
DEFAULT_VAT_RATE = "22%"

#: Форма стороны: от неё зависит бланк договора перевозки.
ENTITY_TYPES = ("ООО", "ИП")
DEFAULT_ENTITY_TYPE = "ООО"

#: Виды перевозчика, которые понимает генератор (исторические значения
#: вкладки «Перевозчик» и справочника).
CARRIER_TYPE_OOO = "ООО (с НДС)"
CARRIER_TYPE_OOO_FREE = "ООО (без НДС)"
CARRIER_TYPE_IP = "ИП с НДС"
CARRIER_TYPE_IP_FREE = "ИП без НДС"

#: Базовая ставка числом — запасное значение для непонятных данных.
DEFAULT_VAT_RATE_NUM = 22.0


def _text(value: Any) -> str:
    """Значение строкой без пробелов по краям (None → пустая строка)."""
    return "" if value is None else str(value).strip()


def is_vat_free(vat_rate: Any) -> bool:
    """
    True, если ставка — «Без НДС» (налогом не облагается).

    Сравнение нечувствительно к регистру и лишним пробелам: значение
    приходит из интерфейса, из справочника и из распознавания.
    """
    return _text(vat_rate).lower().replace("ё", "е") == VAT_FREE.lower()


def _rate_or_none(vat_rate: Any) -> Optional[float]:
    """
    Ставка числом или None, если разобрать не удалось.

    «Без НДС» — это 0.0 (налога нет), «22%» — 22.0. Тонкость нужна там,
    где «непонятное значение» и «ноль» — РАЗНЫЕ случаи: ноль прибавляет к
    договору ставку, а непонятное значение — нет.
    """
    if is_vat_free(vat_rate):
        return 0.0

    text = _text(vat_rate).replace("%", "").replace(",", ".").strip()
    if not text:
        return None

    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def vat_rate_number(vat_rate: Any, default: float = DEFAULT_VAT_RATE_NUM) -> float:
    """
    Ставка числом: «22%» → 22.0, «Без НДС» → 0.0.

    Непонятное значение (пусто, «мусор», «20%» из старых данных) даёт
    `default`: подставлять ноль вместо неизвестной ставки нельзя — в
    договоре появилась бы нулевая ставка вместо ошибки в данных.
    """
    number = _rate_or_none(vat_rate)
    return default if number is None else number


def vat_rate_label(vat_rate: Any, default: str = DEFAULT_VAT_RATE) -> str:
    """
    Ставка подписью для бланка: «22%», «10%», «5%», «7%», «0%», «Без НДС».

    Известные значения возвращаются как есть, неизвестные числа приводятся
    к виду «N%», пустое значение — к ставке по умолчанию.
    """
    text = _text(vat_rate)
    if text in VAT_RATES:
        return text
    if not text:
        return default
    number = _rate_or_none(text)
    if number is None:  # разобрать не удалось
        return default
    return f"{number:.0f}%"


def normalize_vat_rate(value: Any) -> str:
    """
    Значение из данных → пункт списка ставок; непонятное — пустая строка.

    Принимаются «22%», «22», « 5 % », «без НДС», «НДС не облагается».
    Возвращается ровно один из `VAT_RATES`, чтобы его можно было поставить
    в выпадающий список; пустая строка означает «в данных ставки нет» —
    вызывающий код оставляет поле как есть.
    """
    text = _text(value)
    if not text:
        return ""

    lowered = text.lower().replace("ё", "е")
    if lowered in (VAT_FREE.lower(), "ндс не облагается", "не облагается"):
        return VAT_FREE

    number = _rate_or_none(text)
    if number is None:  # разобрать не удалось
        return ""
    label = f"{number:.0f}%"
    return label if label in VAT_RATES else ""


def compute_vat(price_without_vat: Any, vat_rate: Any) -> Tuple[float, float]:
    """
    НДС и итог от базы без НДС: (НДС, сумма с НДС).

    База округляется до копеек (в поле стоимости копейки и так есть), НДС
    считается от неё и тоже округляется — ровно так же считает генератор,
    поэтому итог в форме и в договоре совпадает до копейки.
    """
    base = round(float(price_without_vat or 0), 2)
    rate = vat_rate_number(vat_rate)
    nds = round(base * rate / 100, 2)
    return nds, round(base + nds, 2)


def normalize_entity_type(value: Any) -> str:
    """
    Форма стороны из данных: «ООО» или «ИП»; непонятное — пустая строка.

    Принимаются «ООО», «ИП», «Индивидуальный предприниматель», «ООО (с НДС)»
    (так вид записан в старых договорах и в переключателе вкладки
    «Перевозчик»).
    """
    text = _text(value).upper()
    if not text:
        return ""
    base = text.split(" (")[0].strip()
    if base.startswith("ИП") or base.startswith("ИНДИВИДУАЛЬНЫЙ ПРЕДПРИНИМАТЕЛЬ"):
        return "ИП"
    if base.startswith("ООО") or base.startswith("ОБЩЕСТВО С ОГРАНИЧЕННОЙ"):
        return "ООО"
    return ""


def compute_carrier_type(entity_type: Any, vat_rate: Any) -> str:
    """
    Вид перевозчика для бланка — из формы стороны и ставки НДС.

    Возвращается одно из четырёх значений; именно его читает генератор,
    чтобы выбрать бланк и ветвь пункта 1.2:

      * «ООО (с НДС)» / «ООО (без НДС)» → shablon_ooo.docx;
      * «ИП с НДС»                      → shablon_ip_with_vat.docx;
      * «ИП без НДС»                    → shablon_ip_without_vat.docx.

    «ООО без НДС» — не ошибка: у ООО на УСН с доходом до 20 млн ₽ налога
    нет. Форма без вида (пусто) считается ООО — так же, как раньше вёл себя
    переключатель вкладки «Договор».
    """
    free = is_vat_free(vat_rate)
    if normalize_entity_type(entity_type) == "ИП":
        return CARRIER_TYPE_IP_FREE if free else CARRIER_TYPE_IP
    return CARRIER_TYPE_OOO_FREE if free else CARRIER_TYPE_OOO


def split_carrier_type(carrier_type: Any) -> Tuple[str, str]:
    """
    Вид перевозчика → (форма, ставка НДС); непонятное — две пустые строки.

    Нужно для записей, сохранённых ДО этого шага: в них есть только
    `carrier_type`, а отдельных полей формы и ставки нет. Обратное
    преобразование к `compute_carrier_type`:

      * «ООО (с НДС)»  → («ООО», «22%»);
      * «ИП с НДС»     → («ИП», «22%»);
      * «ИП без НДС»   → («ИП», «Без НДС»);
      * «ООО (без НДС)» → («ООО», «Без НДС»).

    Ставка для «с НДС» берётся базовая: в старых записях ставка жила
    отдельным числовым полем, и восстановить её из вида нельзя.
    """
    text = _text(carrier_type)
    if not text:
        return "", ""

    entity_type = normalize_entity_type(text)
    if not entity_type:
        return "", ""

    return entity_type, (VAT_FREE if "без НДС" in text else DEFAULT_VAT_RATE)
