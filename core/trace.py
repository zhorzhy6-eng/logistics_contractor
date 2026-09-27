#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Декоратор @trace для детального трейсинга функций (без персональных данных).

Шаг 4 задания по безопасности
-----------------------------
Раньше декоратор писал в лог repr() аргументов целиком, поэтому в logs/app.log
попадали паспортные данные, ФИО, адреса и телефоны.

Теперь логируются только безопасные вещи:
  * имя функции и время выполнения;
  * типы и размеры данных (сколько символов в строке, сколько полей в словаре,
    какие ключи, сколько элементов в списке);
  * числовые идентификаторы и короткие скалярные значения.

Значения строк не пишутся вообще: вместо них указывается длина.
"""

import functools
import logging
import time
import traceback
from typing import Any, Dict, List

# Поля, значения которых считаются безопасными и пишутся как есть
# (идентификаторы, счётчики, суммы). Всё остальное маскируется.
SAFE_SCALAR_NAMES = frozenset({
    "id", "driver_id", "customer_id", "carrier_id", "contract_id", "addr_id",
    "vehicle_id", "org_id", "index", "attempt", "limit", "offset", "count",
    "year", "price", "vat_rate_num", "payment_days", "percent", "added",
})

MAX_KEYS_IN_LOG = 15


def safe_value(value: Any, name: str = "") -> str:
    """
    Безопасное представление значения для лога.

    Строки не раскрываются: показывается только длина. Словари — только
    количество и ключи. Списки — только количество элементов.
    """
    if value is None or isinstance(value, bool):
        return repr(value)

    if isinstance(value, (int, float)):
        return repr(value)

    if isinstance(value, str):
        if name in SAFE_SCALAR_NAMES and len(value) <= 32:
            return repr(value)
        return f"<строка, {len(value)} симв.>"

    if isinstance(value, dict):
        keys: List[Any] = list(value.keys())[:MAX_KEYS_IN_LOG]
        tail = ", …" if len(value) > MAX_KEYS_IN_LOG else ""
        return f"<dict: {len(value)} полей, ключи: {keys}{tail}>"

    if isinstance(value, (list, tuple, set)):
        return f"<{type(value).__name__}[{len(value)}]>"

    return f"<{type(value).__name__}>"


def safe_result(result: Any) -> str:
    """Безопасное представление результата функции."""
    if isinstance(result, dict):
        keys = list(result.keys())[:MAX_KEYS_IN_LOG]
        return f"dict с ключами {keys}"
    if isinstance(result, (list, tuple, set)):
        return f"{type(result).__name__}[{len(result)}]"
    if isinstance(result, str):
        return f"строка, {len(result)} симв."
    if result is None or isinstance(result, (bool, int, float)):
        return repr(result)
    return f"<{type(result).__name__}>"


def filled_fields_summary(data: Any, max_missing: int = 8) -> str:
    """
    Сводка заполненности полей: сколько заполнено и какие пустые.

    Значения НЕ раскрываются — только имена полей. Используется в debug-логе
    при распознавании и сохранении, чтобы видеть, что пришло от модели,
    а что осталось пустым.
    """
    if not isinstance(data, dict):
        return f"<{type(data).__name__}>"

    filled = [key for key, value in data.items() if str(value or "").strip()]
    empty = [key for key, value in data.items() if not str(value or "").strip()]

    text = f"заполнено {len(filled)} из {len(data)}"
    if empty:
        shown = ", ".join(empty[:max_missing])
        tail = ", …" if len(empty) > max_missing else ""
        text += f" | пусто: {shown}{tail}"
    return text


def trace(func):
    """
    Декоратор: логирует вход, выход и ошибки функции без персональных данных.

    Использование:
        @trace
        def my_function(a, b):
            ...
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger = logging.getLogger(func.__module__)

        # ── Безопасное описание аргументов ──
        arg_strs = []
        for i, a in enumerate(args):
            if i == 0 and hasattr(a, "__dict__"):
                # self / cls — не логируем
                continue
            arg_strs.append(safe_value(a))
        for k, v in kwargs.items():
            arg_strs.append(f"{k}={safe_value(v, k)}")
        args_str = ", ".join(arg_strs) if arg_strs else "(без аргументов)"

        logger.debug(f"→ {func.__name__}({args_str})")

        start = time.time()
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start
            logger.debug(
                f"← {func.__name__} завершена за {elapsed:.3f} сек → {safe_result(result)}"
            )
            return result

        except Exception as e:
            elapsed = time.time() - start
            logger.error(
                f"✗ {func.__name__} упала за {elapsed:.3f} сек: "
                f"{type(e).__name__}: {e}"
            )
            logger.debug(f"Traceback:\n{traceback.format_exc()}")
            raise

    return wrapper
