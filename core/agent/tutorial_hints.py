#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Контекстные подсказки режима «Обучение» (задание: система обучения).

Режим «Обучение» — это кнопка в панели Максимилиана: когда она включена,
помощник подсказывает на КАЖДОМ шаге заполнения, что делать сейчас. Здесь —
«мозг» подсказки, чистая логика без Qt и без базы:

  * ``tutorial_step_hint(context)`` — по открытой форме (тип договора и
    заполненные поля) возвращает короткую подсказку о следующем шаге;
  * ``_build_hint(sections, contract_type)`` — то же самое по уже собранным
    данным (проверяется тестами без поднятия окна).

Откуда берётся точность подсказки
---------------------------------
Подсказка строится на том же источнике, по которому писались файлы
инструкций в ``data/knowledge/tutorial/``: обязательные поля и порядок
заполнения из ``core/agent/proactivity.py`` (REQUIRED / ORDER). Поэтому
подсказка «что заполнить сейчас» совпадает с разделом «Обязательные поля»
инструкции по этому типу — инструкции и подсказки не расходятся.

Подсказка — предложение, а не действие: форму она не меняет. Показом
управляет панель (``ui/agent_widget.py``), здесь только текст.
"""

import logging
from typing import Any, Dict, Optional

from core.agent import proactivity

logger = logging.getLogger("core.agent.tutorial_hints")

#: Разделы, чью запись обычно не вводят руками, а подтягивают из базы.
#: Подсказка напоминает об этом один раз, не повторяя на каждом поле.
PULLABLE_SECTIONS = {
    "Перевозчик", "Заказчик", "Водитель", "Арендодатель", "Арендатор",
    "Экипаж", "Транспорт", "Перевозимые ТС", "Груз",
}


def _form_sections(context: Any) -> Dict[str, Any]:
    """Данные открытой формы разделами (пусто — формы нет)."""
    from core.agent.tools import collect_form_data

    window = context.current_window() if context is not None else None
    if window is None:
        return {}
    try:
        return collect_form_data(window) or {}
    except Exception as e:  # noqa: BLE001 — подсказка не должна ломать панель
        logger.warning("Форма не прочитана: %s", e)
        return {}


def _contract_type(context: Any, window: Any) -> str:
    """Тип договора открытого окна (пусто — не определили)."""
    if context is not None:
        value = str(getattr(context, "contract_type", "") or "").strip()
        if value:
            return value
    if window is not None:
        return str(getattr(window, "CONTRACT_TYPE", "") or "").strip()
    return ""


def _build_hint(sections: Dict[str, Any], contract_type: str = "") -> str:
    """
    Текст подсказки по данным формы и типу договора.

    :param sections: разделы формы (``collect_form_data`` или заглушка);
    :param contract_type: ключ типа договора (из core/contracts).
    """
    step = proactivity.next_field(sections, contract_type) if sections else {}
    next_item = step.get("next")

    if next_item is not None:
        section = str(next_item.get("section_title") or "")
        title = str(next_item.get("title") or "")
        hint = f"Сейчас заполните: «{title}» — вкладка «{section}»."
        if section in PULLABLE_SECTIONS:
            hint += " Эту запись можно подтянуть из справочника."
        return hint

    if sections:
        return "Обязательные поля заполнены. Проверьте данные и создайте документ."

    return "Откройте форму нужного типа документа и заполняйте её по вкладкам."


def tutorial_step_hint(context: Any = None) -> str:
    """
    Подсказка режима «Обучение» для показа оператору.

    Смотрит на открытую форму и возвращает одну короткую фразу о следующем
    шаге. Пустая строка не возвращается: всегда есть что сказать — либо
    «что заполнить сейчас», либо «форма готова». Любой сбой (чужой контекст,
    недоступная форма) глушится и даёт общую подсказку: подсказка не должна
    ронять панель.
    """
    try:
        window = context.current_window() if context is not None else None
        contract_type = _contract_type(context, window)
        sections = _form_sections(context)
        return _build_hint(sections, contract_type)
    except Exception as e:  # noqa: BLE001 — подсказка не должна ронять панель
        logger.warning("Подсказка обучения не собрана: %s", e)
        return "Откройте форму нужного типа документа и заполняйте её по вкладкам."


def onboarding_decision(agent_settings: Dict[str, Any]) -> str:
    """
    Нужно ли предложить обучение при первом запуске.

    Решение зависит от двух настроек: ``tutorial_seen`` (показано ли уже
    предложение) и ``tutorial_mode`` (включён ли сам режим).

    :return: ``"offer"`` — предложить включить обучение; ``"mark_seen"`` —
        режим уже включён, осталось только поставить отметку; ``"none"`` —
        предложение уже показывалось, ничего делать не нужно.
    """
    settings = agent_settings if isinstance(agent_settings, dict) else {}
    if bool(settings.get("tutorial_seen", False)):
        return "none"
    if bool(settings.get("tutorial_mode", False)):
        return "mark_seen"
    return "offer"


__all__ = [
    "PULLABLE_SECTIONS",
    "_build_hint",
    "onboarding_decision",
    "tutorial_step_hint",
]
