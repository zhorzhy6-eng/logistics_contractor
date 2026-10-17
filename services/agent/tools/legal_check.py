#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Юридическая проверка открытой формы (уровень 1 юридической помощи).

Что делает
----------
Берёт текущие значения формы, каталог правил (core/legal_rules.py) и
проверяет каждое правило, у которого есть поле в этой форме. Возвращает
СПИСОК нарушений с источником — не меняя форму ни на йоту.

Почему отдельный модуль
-----------------------
Проверка формы — это чтение формы плюс каталог правил. Ни Qt, ни модели
здесь не нужно: инструмент проверяется тестами на обычных словарях, а
панель и Максимилиан получают готовый ответ. Заполнением формы занимается
другой код (core/agent/tools.py::fill_fields) — здесь только чтение.

Три правила, из которых всё следует
-----------------------------------
1. **Форма не меняется.** Ни один вызов не пишет в вкладки; инструмент
   только читает (get_data).
2. **Пропуск — не нарушение.** Если поля в форме нет (в аренде нет
   вкладки водителя) или значение пустое, правило уходит в «не проверено»
   с причиной: оператор не должен видеть замечание о том, чего нет.
3. **Источник обязателен.** У каждого нарушения есть source и quote из
   правовой базы: замечание без нормы — это мнение, а не проверка.
"""

import logging
from typing import Any, Dict, List, Optional, Sequence

from core import legal_rules
from services.agent.tools.registry import collect_form_data

logger = logging.getLogger("services.agent.tools.legal_check")

#: Русские подписи важности — их видит оператор в диалоге и панели.
SEVERITY_TITLES = {
    "error": "ошибка",
    "warning": "замечание",
    "info": "справка",
}

#: Порядок вывода нарушений: сначала то, что важнее.
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


# ─────────────────────────────────────────────────────────────
# Значения формы
# ─────────────────────────────────────────────────────────────

def flatten_form(sections: Dict[str, Any]) -> Dict[str, Any]:
    """
    Плоский словарь «раздел.поле» → значение.

    Раздел — ключ вкладки (contract_tab, driver_tab…), как его отдаёт
    collect_form_data. Вложенные словари разворачиваются на один уровень:
    именно так у «Договора» лежат route и price. Списки (машины, погрузки)
    сохраняются как есть — правила их пока не проверяют, но оператор видит
    форму целиком.
    """
    values: Dict[str, Any] = {}
    for section, data in (sections or {}).items():
        if not isinstance(data, dict):
            continue
        for field, value in data.items():
            if isinstance(value, dict):
                for sub_field, sub_value in value.items():
                    values[f"{section}.{field}.{sub_field}"] = sub_value
                continue
            values[f"{section}.{field}"] = value
    return values


def form_values(window: Any = None,
                form_data: Optional[Dict[str, Any]] = None,
                values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Значения формы в плоском виде.

    Порядок источников: готовые values → готовые разделы → окно. Окно
    читается через collect_form_data (только get_data вкладок), поэтому
    подставной объект без Qt подходит так же, как настоящее окно.
    """
    if values:
        return dict(values)
    if form_data is not None:
        return flatten_form(form_data)
    if window is None:
        return {}
    return flatten_form(collect_form_data(window))


def contract_type_of(contract_type: str = "", window: Any = None) -> str:
    """Тип договора: аргумент → свойство окна (CONTRACT_TYPE) → пусто."""
    key = str(contract_type or "").strip()
    if key:
        return key
    return str(getattr(window, "CONTRACT_TYPE", "") or "").strip()


# ─────────────────────────────────────────────────────────────
# Проверка
# ─────────────────────────────────────────────────────────────

def _rules_for(contract_type: str) -> Dict[str, Dict[str, Any]]:
    """
    Правила для проверки: по типу договора, а для неизвестного типа — все.

    Неизвестный тип (окно без CONTRACT_TYPE, проба) не должен означать
    «проверять нечем»: проверяются все правила, а правила без поля в этой
    форме честно уходят в пропущенные.
    """
    rules = legal_rules.rules_for_type(contract_type)
    if rules:
        return rules
    return legal_rules.load_rules()


def check_contract_legal(contract_type: str = "",
                         window: Any = None,
                         form_data: Optional[Dict[str, Any]] = None,
                         values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Проверяет открытую форму на соответствие нормам права.

    :param contract_type: ключ типа договора (perevozka, arenda_ts…);
        пусто — берётся из окна;
    :param window: окно формы (объект с вкладками get_data);
    :param form_data: разделы формы готовым словарём (тесты, пробы);
    :param values: плоские значения «раздел.поле» (тесты, пробы);
    :return: словарь вида::

        {
          "ok": True,
          "contract_type": "perevozka",
          "violations": [
            {"rule": "vat_rate", "title": "Ставка НДС",
             "severity": "error", "message": "Значение 20%, а по НК РФ …",
             "source": "НК РФ ст. 164 п. 3", "field": "contract_tab.vat_rate",
             "current": "20%", "required": 22, "quote": "…"}
          ],
          "checked": 4,
          "skipped": [{"rule": "driver_license_number",
                       "reason": "поля driver_tab.license_number в форме нет"}],
          "summary": "Найдено нарушений: 1 (ошибок 1, замечаний 0)."
        }

    Форма НЕ меняется: инструмент только читает значения.
    """
    type_key = contract_type_of(contract_type, window)
    values_map = form_values(window=window, form_data=form_data, values=values)

    if not values_map:
        return {
            "ok": True,
            "contract_type": type_key,
            "violations": [],
            "checked": 0,
            "skipped": [],
            "summary": ("Форма пуста или не открыта: проверять нечего. "
                        "Откройте окно договора и заполните поля."),
        }

    rules = _rules_for(type_key)
    if not rules:
        return {
            "ok": True,
            "contract_type": type_key,
            "violations": [],
            "checked": 0,
            "skipped": [],
            "summary": ("Каталог юридических правил пуст — проверять нечем. "
                        "Проверьте файл data/knowledge/legal/rules.json."),
        }

    violations: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    checked = 0

    for name, rule in rules.items():
        paths = legal_rules.field_paths(rule)
        if not paths:
            skipped.append({
                "rule": name,
                "title": str(rule.get("title") or name),
                "reason": "норма справочная: поля формы у неё нет",
            })
            continue

        path, value = legal_rules.resolve_field(values_map, rule)
        if not path:
            skipped.append({
                "rule": name,
                "title": str(rule.get("title") or name),
                "reason": f"поля {' / '.join(paths)} в этой форме нет",
            })
            continue

        result = legal_rules.check_rule(name, value)
        if not result.get("checked"):
            skipped.append({
                "rule": name,
                "title": str(rule.get("title") or name),
                "reason": str(result.get("message") or "проверить не удалось"),
            })
            continue

        checked += 1
        if result.get("ok"):
            continue

        violations.append({
            "rule": name,
            "title": str(rule.get("title") or name),
            "severity": result.get("severity") or "warning",
            "message": str(result.get("message") or ""),
            "source": str(result.get("source") or ""),
            "field": path,
            "current": result.get("current"),
            "required": result.get("required"),
            "quote": str(result.get("quote") or ""),
        })

    violations.sort(key=lambda item: SEVERITY_ORDER.get(item["severity"], 3))
    logger.info(
        "Юридическая проверка формы (%s): проверено %s, нарушений %s",
        type_key or "тип не указан", checked, len(violations),
    )
    return {
        "ok": True,
        "contract_type": type_key,
        "violations": violations,
        "checked": checked,
        "skipped": skipped,
        "summary": summary_text(violations, checked),
    }


def summary_text(violations: Sequence[Dict[str, Any]], checked: int = 0) -> str:
    """
    Строка итога: сколько нарушений и какой важности.

    Оператору нужен ответ одной строкой — подробности он читает в списке.
    """
    if not violations:
        return (f"Нарушений нет. Проверено правил: {checked}."
                if checked else "Нарушений нет.")

    errors = sum(1 for item in violations if item.get("severity") == "error")
    warnings = sum(1 for item in violations if item.get("severity") == "warning")
    infos = len(violations) - errors - warnings

    parts = [f"ошибок {errors}", f"замечаний {warnings}"]
    if infos:
        parts.append(f"справок {infos}")
    word = "нарушение" if len(violations) == 1 else "нарушений"
    return (f"Найдено {word}: {len(violations)} ({', '.join(parts)}). "
            f"Проверено правил: {checked}.")


def has_errors(result: Dict[str, Any]) -> bool:
    """Есть ли в результате проверки ошибки (а не только замечания)."""
    return any(
        item.get("severity") == "error"
        for item in (result or {}).get("violations") or []
    )


def format_violations(result: Dict[str, Any]) -> str:
    """
    Текст проверки для панели и диалога: итог и по одному пункту на нарушение.

    Формат пункта::

        [замечание] Маршрут перевозки
        Маршрут: …
        Источник: Правила перевозок грузов (ПП РФ № 2200), приложение № 6, п. 6
    """
    result = result or {}
    lines: List[str] = [str(result.get("summary") or "")]
    for item in result.get("violations") or []:
        severity = SEVERITY_TITLES.get(str(item.get("severity")), str(item.get("severity")))
        lines.append("")
        lines.append(f"[{severity}] {item.get('title') or item.get('rule')}")
        lines.append(str(item.get("message") or ""))
        source = str(item.get("source") or "").strip()
        if source:
            lines.append(f"Источник: {source}")
    return "\n".join(line for line in lines if line is not None).strip()


def legal_rules_digest(contract_type: str = "") -> str:
    """
    Короткое описание того, что проверяется у типа договора.

    Нужно диалогу: перед проверкой оператор видит, какие нормы применяются
    и откуда они взяты.
    """
    rules = legal_rules.rules_for_type(contract_type) or legal_rules.load_rules()
    checkable = [name for name, rule in rules.items() if legal_rules.field_paths(rule)]
    if not checkable:
        return "Проверяемых правил нет."
    lines = [f"Проверяется правил: {len(checkable)}."]
    for name in checkable:
        rule = rules[name]
        lines.append(
            f"• {rule.get('title') or name} — {rule.get('source')}"
        )
    return "\n".join(lines)


__all__ = [
    "SEVERITY_ORDER",
    "SEVERITY_TITLES",
    "check_contract_legal",
    "contract_type_of",
    "flatten_form",
    "format_violations",
    "form_values",
    "has_errors",
    "legal_rules_digest",
    "summary_text",
]
