#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сравнение формы с ТИПОВЫМ договором (уровень 5 юридической помощи).

Зачем отдельно от проверки норм
-------------------------------
Проверка формы (legal_check.py) отвечает: «что нарушает закон». Типовой
договор отвечает на другой вопрос: «чем форма отличается от того, что мы
обычно подписываем». Это разные вещи, и смешивать их нельзя: 30 дней
оплаты — не требование закона, а условие типового бланка. Если назвать
его «нарушением», оператор поверит, что закон нарушен.

Поэтому типовые значения живут отдельными файлами
(data/knowledge/legal/templates/typical_*.json) и в них прямо написано,
что это практика. Норма указывается только там, где она действительно
есть в правовой базе (ставка НДС — НК РФ ст. 164 п. 3).

Что делает инструмент
---------------------
1. Загружает типовой договор по типу (перевозка/аренда).
2. Сравнивает числа и обязательные поля с текущей формой.
3. Показывает расхождения — НЕ меняя форму.

Форму инструмент только читает: исправляет оператор.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import app_paths, legal_rules
from services.agent.tools import legal_check

logger = logging.getLogger("services.agent.tools.legal_compare")

#: Папка типовых договоров проекта. Читается из ресурсов сборки в .exe
#: (см. core/app_paths.py), поэтому типовые договоры находятся и там.
TEMPLATES_DIR = app_paths.resource_path("data/knowledge/legal/templates")

#: Какой типовой файл соответствует типу договора. Клиентские типы
#: перевозки (Формика, Логистикс Рус) идут по одному типовому договору
#: перевозки: бланк один, меняются только реквизиты сторон.
TYPICAL_FILES: Dict[str, str] = {
    "perevozka": "typical_perevozka.json",
    "formika": "typical_perevozka.json",
    "logistiks_rus": "typical_perevozka.json",
    "arenda_ts": "typical_arenda.json",
}

#: С какой точностью сравнивать числа (дни и проценты — без дробного шума).
NUMBER_TOLERANCE = 1e-9

#: Значение «поле не заполнено» для обязательных полей типового.
BLANK_WORDS = ("", "0", "0.0")


def typical_path(contract_type: str) -> Optional[Path]:
    """
    Файл типового договора для типа (None — типового нет).

    Неизвестный тип не получает чужой типовой договор: сравнение с чужим
    бланком хуже отсутствия сравнения.
    """
    name = TYPICAL_FILES.get(str(contract_type or "").strip().casefold())
    if not name:
        return None
    return TEMPLATES_DIR / name


def load_typical(contract_type: str) -> Optional[Dict[str, Any]]:
    """
    Типовой договор типа: значения, реквизиты, пояснения.

    Ошибка чтения — None и запись в журнал: сравнение не должно ронять
    проверку формы.
    """
    path = typical_path(contract_type)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("Типовой договор не прочитан (%s): %s",
                       path.name, type(error).__name__)
        return None
    if not isinstance(data, dict):
        logger.warning("Типовой договор %s — не объект JSON", path.name)
        return None
    return data


def _field_paths(spec: Dict[str, Any]) -> List[str]:
    """Пути поля типового договора: основное имя и синонимы."""
    paths: List[str] = []
    primary = str(spec.get("field") or "").strip()
    if primary:
        paths.append(primary)
    for alias in spec.get("field_aliases") or []:
        alias_text = str(alias or "").strip()
        if alias_text and alias_text not in paths:
            paths.append(alias_text)
    return paths


def _value_for(values: Dict[str, Any],
               spec: Dict[str, Any]) -> Tuple[str, Any]:
    """Первое найденное значение поля (путь, значение) или ("", None)."""
    for path in _field_paths(spec):
        if path in values:
            return path, values[path]
    return "", None


def _number(value: Any) -> Optional[float]:
    """Число из значения формы (та же разборка, что у правил права)."""
    return legal_rules.as_number(value)


def _is_blank(value: Any) -> bool:
    """Пустое ли значение (None, пробелы, ноль у числа)."""
    return legal_rules.is_blank(value)


def compare_with_typical(contract_type: str = "",
                         window: Any = None,
                         form_data: Optional[Dict[str, Any]] = None,
                         values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Сравнивает открытую форму с типовым договором.

    :param contract_type: тип договора (perevozka, arenda_ts…);
    :param window: окно формы (объект с вкладками get_data);
    :param form_data: разделы формы словарём (тесты);
    :param values: плоские значения «раздел.поле» (тесты);
    :return: словарь вида::

        {
          "ok": True,
          "contract_type": "perevozka",
          "typical": "Типовой договор перевозки груза",
          "source": "типовой договор проекта (…)",   # практика, не норма
          "discrepancies": [
            {"field": "contract_tab.payment_days", "title": "срок оплаты",
             "typical": 30, "current": 45, "unit": "дней",
             "note": "в типовом договоре — 30 дней",
             "message": "Срок оплаты 45 дней, а в типовом договоре — 30."}
          ],
          "matched": 3,
          "skipped": [{"field": "contract_tab.penalty_rate",
                       "reason": "поля в форме нет"}],
          "summary": "Расхождений с типовым: 1 (проверено 4)."
        }

    Форма НЕ меняется: инструмент только читает значения.
    """
    type_key = legal_check.contract_type_of(contract_type, window)
    typical = load_typical(type_key)
    if typical is None:
        return {
            "ok": True,
            "contract_type": type_key,
            "typical": "",
            "source": "",
            "discrepancies": [],
            "matched": 0,
            "skipped": [],
            "summary": (f"Типового договора для типа «{type_key or 'не указан'}» "
                        f"нет — сравнивать не с чем."),
        }

    values_map = legal_check.form_values(window=window, form_data=form_data,
                                         values=values)
    if not values_map:
        return {
            "ok": True,
            "contract_type": type_key,
            "typical": str(typical.get("title") or ""),
            "source": str(typical.get("source") or ""),
            "discrepancies": [],
            "matched": 0,
            "skipped": [],
            "summary": ("Форма пуста или не открыта: сравнивать нечего."),
        }

    severity = str(typical.get("severity") or "warning")
    fields = typical.get("_fields") or {}
    discrepancies: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    matched = 0

    for key, spec in fields.items():
        typical_value = typical.get(key)
        if typical_value is None:
            continue
        path, current = _value_for(values_map, spec)
        title = str(spec.get("title") or key)
        if not path:
            skipped.append({
                "field": str(spec.get("field") or key),
                "title": title,
                "reason": "поля в этой форме нет",
            })
            continue

        current_number = _number(current)
        typical_number = _number(typical_value)
        if current_number is None or typical_number is None:
            skipped.append({
                "field": path, "title": title,
                "reason": "значение не число — сравнивать не с чем",
            })
            continue

        if abs(current_number - typical_number) <= NUMBER_TOLERANCE:
            matched += 1
            continue

        unit = str(spec.get("unit") or "")
        discrepancies.append({
            "field": path,
            "title": title,
            "typical": typical_value,
            "current": current,
            "unit": unit,
            "note": str(spec.get("note") or ""),
            "severity": severity,
            "source": str(typical.get("source") or ""),
            "message": _difference_text(title, current_number, typical_number,
                                        unit, spec),
        })

    # Обязательные поля типового: пустые — тоже расхождение.
    for name, spec in (typical.get("_required_fields") or {}).items():
        if name not in (typical.get("required_fields") or []):
            continue
        path, current = _value_for(values_map, spec)
        title = str(spec.get("title") or name)
        if not path:
            skipped.append({
                "field": str(spec.get("field") or name),
                "title": title,
                "reason": "поля в этой форме нет",
            })
            continue
        if not _is_blank(current):
            matched += 1
            continue
        discrepancies.append({
            "field": path,
            "title": title,
            "typical": "заполнено",
            "current": current,
            "unit": "",
            "note": "обязательное поле типового договора",
            "severity": severity,
            "source": str(typical.get("source") or ""),
            "message": (f"Не заполнено: {title} — обязательное поле "
                        f"типового договора."),
        })

    logger.info(
        "Сравнение с типовым (%s): расхождений %s, совпало %s",
        type_key, len(discrepancies), matched,
    )
    return {
        "ok": True,
        "contract_type": type_key,
        "typical": str(typical.get("title") or ""),
        "source": str(typical.get("source") or ""),
        "note": str(typical.get("note") or ""),
        "discrepancies": discrepancies,
        "matched": matched,
        "skipped": skipped,
        "summary": _summary(discrepancies, matched, skipped),
    }


def _difference_text(title: str, current: float, typical: float,
                     unit: str, spec: Dict[str, Any]) -> str:
    """
    Текст расхождения: «Срок оплаты 45 дней, а в типовом договоре — 30.»

    Формулировка намеренно не обвинительная: отличие от типового — не
    нарушение закона.
    """
    # Процент пишется слитно с числом (22%), остальные единицы — через пробел.
    unit_text = "" if not unit else ("%" if unit == "%" else f" {unit}")
    #: Пояснение (note) в текст не вставляется: оно повторяло бы сравнение
    #: («…а в типовом договоре — 30 дней. в типовом договоре — 30 дней»).
    #: Оно остаётся отдельным полем для подсказки и диалога.
    return (f"{str(title)}: {current:g}{unit_text}, "
            f"а в типовом договоре — {typical:g}{unit_text}.")


def _summary(discrepancies: List[Dict[str, Any]], matched: int,
             skipped: List[Dict[str, Any]]) -> str:
    """Итог сравнения одной строкой."""
    checked = matched + len(discrepancies)
    if not discrepancies:
        return f"Отличий от типового договора нет. Проверено полей: {checked}."
    word = "расхождение" if len(discrepancies) == 1 else "расхождений"
    return (f"{word.capitalize()} с типовым: {len(discrepancies)} "
            f"(проверено полей: {checked}). Это отличия от обычного бланка, "
            f"а не нарушения закона.")


def format_comparison(result: Dict[str, Any]) -> str:
    """
    Текст сравнения для панели и диалога.

    В шапке прямо сказано, что типовой договор — практика, а не норма:
    иначе оператор прочитает «45 дней» как нарушение закона.
    """
    result = result or {}
    lines = [str(result.get("summary") or "")]
    source = str(result.get("source") or "").strip()
    if source:
        lines.append(f"Источник значений: {source} (практика, не норма права).")
    for item in result.get("discrepancies") or []:
        lines.append("")
        lines.append(f"• {item.get('message')}")
    return "\n".join(line for line in lines if line).strip()


def typical_digest(contract_type: str) -> str:
    """Короткое описание типового договора: значения и откуда они."""
    typical = load_typical(contract_type)
    if typical is None:
        return f"Типового договора для типа «{contract_type}» нет."
    lines = [str(typical.get("title") or "Типовой договор")]
    for key, spec in (typical.get("_fields") or {}).items():
        value = typical.get(key)
        if value is None:
            continue
        unit = str(spec.get("unit") or "")
        lines.append(f"• {str(spec.get('title') or key)}: {value} "
                     f"{unit}".rstrip())
    required = typical.get("required_fields") or []
    if required:
        lines.append("• обязательные поля: " + ", ".join(required))
    note = str(typical.get("note") or "").strip()
    if note:
        lines.append(note)
    return "\n".join(lines)


__all__ = [
    "BLANK_WORDS",
    "NUMBER_TOLERANCE",
    "TEMPLATES_DIR",
    "TYPICAL_FILES",
    "compare_with_typical",
    "format_comparison",
    "load_typical",
    "typical_digest",
    "typical_path",
]
