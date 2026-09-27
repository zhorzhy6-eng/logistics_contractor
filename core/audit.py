#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Аудит-лог: кто, что и когда делал — без персональных данных (Шаг 4 задания).

Отдельный файл logs/audit.log:
    * основной logs/app.log — техническая диагностика;
    * logs/audit.log       — журнал действий (создание договора, сохранение
      в базу, удаление записей, работа с ключом).

Защита от утечки ПДн двойная:
  1) в аудит передаются только безопасные поля (см. ALLOWED_FIELDS);
  2) неизвестные поля записываются как <скрыто>, значения обрезаются.

Пример строки:
    2026-09-24 12:30:05 | audit | contract_created | contract_number='74', file='Договор-заявка_74.docx'
"""

import logging
from typing import Any, Dict

logger = logging.getLogger("audit")

# Поля, которые разрешено писать в аудит-лог.
# Никаких ФИО, паспортов, адресов, телефонов и самих ключей.
ALLOWED_FIELDS = frozenset({
    "contract_number", "contract_id", "driver_id", "customer_id", "carrier_id",
    "addr_id", "vehicle_id", "org_id", "count", "added", "file", "path",
    "model", "provider", "entities", "result", "size_kb", "db", "action",
    "source", "mode", "entity", "point_type", "deleted",
})

MAX_FIELD_LEN = 80
HIDDEN = "<скрыто>"


def _safe_field(name: str, value: Any) -> str:
    """Готовит одно поле аудита: только разрешённые имена, значения обрезаются."""
    if name not in ALLOWED_FIELDS:
        return f"{name}={HIDDEN}"

    if value is None or isinstance(value, (bool, int, float)):
        return f"{name}={value!r}"

    text = str(value)
    if len(text) > MAX_FIELD_LEN:
        text = text[:MAX_FIELD_LEN - 3] + "..."
    return f"{name}={text!r}"


def log_event(action: str, **fields: Any) -> None:
    """
    Пишет событие в аудит-лог.

    :param action: короткое имя действия (contract_created, key_saved, ...)
    :param fields: только безопасные поля (см. ALLOWED_FIELDS)
    """
    parts = [_safe_field(name, value) for name, value in fields.items()]
    message = action if not parts else f"{action} | " + ", ".join(parts)
    logger.info(message)


def log_denied(action: str, reason: str = "") -> None:
    """Пишет в аудит отказ/блокировку (например, отсутствие ключа)."""
    message = f"{action} | result='denied'"
    if reason:
        reason_text = reason[:MAX_FIELD_LEN]
        message += f", reason={reason_text!r}"
    logger.info(message)


def allowed_fields() -> Dict[str, str]:
    """Справочник разрешённых полей (для документации и тестов)."""
    return {name: "разрешено" for name in sorted(ALLOWED_FIELDS)}
