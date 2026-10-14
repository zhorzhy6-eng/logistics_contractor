#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Инструменты проактивности Максимилиана (задание, часть 4).

Четыре инструмента этой части отвечают на «что дальше»:

  * ``suggest_next_field``   — что логично заполнить в открытой форме;
  * ``check_required``       — какие обязательные поля пусты;
  * ``warn_expiring``        — что истекает (ВУ водителей, договоры);
  * ``suggest_from_history`` — что оператор обычно делает в этом месте.

Правила части, которые здесь соблюдаются:

  * **только чтение.** Ни один инструмент не заполняет форму и не меняет
    базу: подсказка — это предложение, решение принимает оператор;
  * **готовые фразы.** У каждого ответа есть ``data.text`` — готовая
    подсказка, её можно показать почти дословно;
  * **без персональных данных сверх нужного.** В подсказках — имена и
    сроки, без паспортов и адресов: логи и подсказки без ПДн (правило
    проекта).

SQL здесь нет: сроки берутся из ``db/crud/drivers.py`` и
``db/crud/contracts.py``, логика «что обязательно» — из
``core/agent/proactivity.py``.
"""

import logging
from typing import Any, Dict, List, Optional, Sequence

from core.agent import proactivity
from core.agent.proactivity import (
    DEFAULT_WARNING_DAYS, SuggestionEngine, habits,
)

logger = logging.getLogger("core.agent.proactivity_tools")


def _ok(data: Any = None, **extra: Any) -> Dict[str, Any]:
    """Ответ «получилось»."""
    result: Dict[str, Any] = {"ok": True, "data": data}
    result.update(extra)
    return result


def _error(message: str, **extra: Any) -> Dict[str, Any]:
    """Ответ «не получилось» с пояснением для модели."""
    result: Dict[str, Any] = {"ok": False, "error": message}
    result.update(extra)
    return result


def _form_sections(context: Any) -> Dict[str, Any]:
    """Данные открытой формы разделами (пусто — формы нет)."""
    from core.agent.tools import collect_form_data

    window = context.current_window() if context is not None else None
    if window is None:
        return {}
    return collect_form_data(window) or {}


def _contract_type(context: Any, window: Any = None) -> str:
    """Тип договора открытого окна (пусто — не определили)."""
    if context is not None:
        value = str(getattr(context, "contract_type", "") or "").strip()
        if value:
            return value
    if window is not None:
        return str(getattr(window, "CONTRACT_TYPE", "") or "").strip()
    return ""


# ─────────────────────────────────────────────────────────────
# 1. suggest_next_field
# ─────────────────────────────────────────────────────────────

def suggest_next_field(context: Any = None, **_) -> Dict[str, Any]:
    """
    Что логично заполнить в открытой форме следующим.

    Смотрит на заполненные и пустые поля, знает обязательные поля каждого
    типа договора и порядок, в котором форму обычно заполняют. Возвращает
    ОДНУ главную подсказку и несколько следующих — чтобы можно было
    предложить выбор.
    """
    sections = _form_sections(context)
    if not sections:
        return _error(
            "открытая форма не найдена: откройте окно нужного типа договора"
        )

    window = context.current_window() if context is not None else None
    result = proactivity.next_field(sections, _contract_type(context, window))
    return _ok(result)


# ─────────────────────────────────────────────────────────────
# 2. check_required
# ─────────────────────────────────────────────────────────────

def check_required(context: Any = None, section: str = "", **_) -> Dict[str, Any]:
    """
    Какие обязательные поля открытой формы пусты.

    Обязательные поля заданы по типам договоров (у «Экспедиторства» — свои,
    у «Аренды» — свои). Проверяются только те разделы, которые форма
    отдаёт: если вкладки в этом окне нет, её поля не считаются пропущенными.

    :param section: проверить только один раздел («водитель», «договор»).
    """
    sections = _form_sections(context)
    if not sections:
        return _error(
            "открытая форма не найдена: откройте окно нужного типа договора"
        )

    only: Optional[Sequence[str]] = None
    wanted = str(section or "").strip()
    if wanted:
        from core.agent.tools import _section_alias

        key = wanted if wanted in sections else _section_alias(wanted, sections)
        if not key:
            return _error(
                f"раздела «{wanted}» в открытой форме нет",
                available=sorted(sections),
            )
        only = [key]

    window = context.current_window() if context is not None else None
    missing = proactivity.missing_required(
        sections, _contract_type(context, window), only_sections=only
    )
    checked = sum(
        len(fields) for name, fields in proactivity.required_fields(
            _contract_type(context, window)
        ).items()
        if name in sections and (only is None or name in only)
    )

    return _ok({
        "missing": missing,
        "missing_count": len(missing),
        "checked_count": checked,
        "is_complete": not missing,
        "text": _required_text(missing, checked),
    })


def _required_text(missing: Sequence[Dict[str, str]], checked: int) -> str:
    """Фраза «чего не хватает» для оператора."""
    if not missing:
        return f"Все обязательные поля заполнены (проверено {checked})."
    parts = [f"{item['title']} ({item['section_title']})" for item in missing[:5]]
    tail = "" if len(missing) <= 5 else f" и ещё {len(missing) - 5}"
    return (f"Не хватает обязательных полей: " + ", ".join(parts) + tail
            + f". Всего не заполнено: {len(missing)}.")


# ─────────────────────────────────────────────────────────────
# 3. warn_expiring
# ─────────────────────────────────────────────────────────────

def warn_expiring(
    days: int = DEFAULT_WARNING_DAYS,
    *,
    entity_type: str = "all",
    limit: int = 5,
    today: Optional[str] = None,
    **_,
) -> Dict[str, Any]:
    """
    Что истекает в ближайшие ``days`` дней: ВУ водителей и договоры.

    :param entity_type: ``all`` (по умолчанию), ``drivers`` или ``contracts``;
    :param today: «сегодня» в ISO — нужно тестам.
    """
    kind = str(entity_type or "all").strip().casefold()
    horizon = max(0, int(days or DEFAULT_WARNING_DAYS))

    licences: List[Dict[str, Any]] = []
    contracts: List[Dict[str, Any]] = []

    if kind in ("all", "все", "drivers", "водители", "driver", "license"):
        from db.crud.drivers import get_expiring_licenses

        licences = get_expiring_licenses(days=horizon, today=today, limit=50)
    elif kind not in ("contracts", "договоры", "contract"):
        return _error(
            f"не понимаю, что проверять: «{entity_type}». "
            f"Можно all, drivers или contracts",
            available=["all", "drivers", "contracts"],
        )

    if kind in ("all", "все", "contracts", "договоры", "contract"):
        from db.crud.contracts import get_expiring_contracts

        contracts = get_expiring_contracts(days=horizon, today=today, limit=50)

    result = proactivity.warn_items(
        licences, contracts, days=horizon, today=today, limit=limit
    )
    return _ok(result)


# ─────────────────────────────────────────────────────────────
# 4. suggest_from_history
# ─────────────────────────────────────────────────────────────

def suggest_from_history(context: Any = None, **_) -> Dict[str, Any]:
    """
    Что оператор обычно делает в этом месте формы.

    Источники: последние нажатия кнопок (журнал действий) и привычки этого
    раздела. Значения из полей формы в журнал не попадают — подсказка
    строится только на надписях действий.
    """
    from ui.action_logging import recent_actions

    window = context.current_window() if context is not None else None
    section = _current_section(window)
    window_name = type(window).__name__ if window is not None else ""

    actions: List[Dict[str, Any]] = []
    try:
        actions = list(recent_actions(600.0))
    except Exception as e:  # noqa: BLE001 — журнал не должен ронять агента
        logger.warning("Журнал действий недоступен: %s", e)

    memory = habits()
    for item in actions:
        memory.remember(str(item.get("action") or ""),
                        section=section, window=window_name)

    result = proactivity.suggest_from_history(
        actions, section=section, window=window_name, memory=memory
    )
    result["section"] = section
    result["section_title"] = proactivity.section_title(section)
    return _ok(result)


def _current_section(window: Any) -> str:
    """Ключ открытой вкладки окна (пусто — не определить)."""
    tabs = getattr(window, "tabs", None)
    keys = ()
    try:
        keys = tuple(window._tab_keys())
    except Exception:  # noqa: BLE001 — окно-заглушка может не уметь
        return ""
    if tabs is None:
        return ""
    try:
        index = int(tabs.currentIndex())
    except Exception:  # noqa: BLE001
        return ""
    if 0 <= index < len(keys):
        return str(keys[index])
    return ""


# ─────────────────────────────────────────────────────────────
# Подсказки «сами»: движок и текст для панели
# ─────────────────────────────────────────────────────────────

def proactive_text(context: Any = None) -> str:
    """
    Готовая подсказка для показа оператору без вопроса (часть 4).

    Собирается из трёх источников по важности: обязательные поля формы,
    истекающие сроки, следующий логичный шаг. Возвращается пустая строка,
    если сказать нечего — тогда панель молчит.
    """
    sections = _form_sections(context)
    window = context.current_window() if context is not None else None
    contract_type = _contract_type(context, window)

    if sections:
        missing = proactivity.missing_required(sections, contract_type)
        if missing:
            titles = ", ".join(item["title"] for item in missing[:3])
            tail = "" if len(missing) <= 3 else f" и ещё {len(missing) - 3}"
            return (f"Не заполнено: {titles}{tail}. "
                    f"Заполнить «{missing[0]['title']}»?")

    try:
        expiring = warn_expiring()
    except Exception as e:  # noqa: BLE001 — подсказка не должна ронять панель
        logger.warning("Сроки не проверены: %s", e)
        expiring = {"ok": False}

    if expiring.get("ok") and expiring["data"].get("count"):
        return str(expiring["data"].get("text") or "")

    step = proactivity.next_field(sections, contract_type) if sections else {}
    text = str(step.get("text") or "")
    if text and not step.get("next"):
        return ""
    return text if step.get("next") else ""


def engine_from_settings(settings: Any) -> SuggestionEngine:
    """
    Движок подсказок по разделу настроек агента.

    Настройки: ``proactive`` (включены ли подсказки) и
    ``proactive_interval`` (пауза между ними, секунды).
    """
    values = settings if isinstance(settings, dict) else {}
    enabled = bool(values.get("proactive", False))
    interval = values.get("proactive_interval", proactivity.SUGGESTION_INTERVAL)
    try:
        seconds = float(interval)
    except (TypeError, ValueError):
        seconds = proactivity.SUGGESTION_INTERVAL
    return SuggestionEngine(enabled=enabled, interval=seconds)


__all__ = [
    "check_required",
    "engine_from_settings",
    "proactive_text",
    "suggest_from_history",
    "suggest_next_field",
    "warn_expiring",
]
