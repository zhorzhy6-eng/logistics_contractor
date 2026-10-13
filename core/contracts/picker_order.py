# -*- coding: utf-8 -*-
"""Порядок типов договоров в выпадающем списке (ЭТАП 2C).

Ключи — значения ContractType. Названия — человекочитаемые для UI.
Порядок задан явно: ContractTypeRegistry.known_types() сортирует
по алфавиту, а в списке нужен свой порядок.

expediciya в список не входит — это служебный тип (не показывается
пользователю), см. ЭТАП 2A.

Модуль не зависит от Qt: порядок нужен и главному окну, и селектору
типа в шапке (ui/controls/contract_type_selector.py), и диалогу выбора
при запуске (ui/contract_picker.py реэкспортирует эти имена).
"""

from typing import Dict, List, Tuple

#: Пункты выпадающего списка: (ключ ContractType, название для пользователя).
#: Порядок — рабочий, а не алфавитный: «клиентский тип D» идёт сразу после
#: «Экспедиторства», выше «клиентский тип B» и «клиентского типа A».
PICKER_ORDER: Tuple[Tuple[str, str], ...] = (
    ("perevozka", "Экспедиторство"),
    ("arenda_ts", "клиентский тип D"),
    ("logistiks_rus", "клиентский тип B"),
    ("formika", "клиентский тип A"),
    ("zayavka_excel", "клиентский тип C"),
)

#: Тип, выбранный в списке по умолчанию (историческое поведение программы).
DEFAULT_PICKER_TYPE = "perevozka"


def picker_items() -> List[Tuple[str, str]]:
    """Копия списка пунктов (для тестов и внешнего кода)."""
    return list(PICKER_ORDER)


def picker_titles() -> Dict[str, str]:
    """Словарь «ключ типа → название для пользователя»."""
    return {key: title for key, title in PICKER_ORDER}


def picker_title(contract_type: str) -> str:
    """Название типа для заголовков окон; неизвестный ключ возвращается как есть."""
    key = str(contract_type or "")
    return picker_titles().get(key, key)


__all__ = [
    "DEFAULT_PICKER_TYPE",
    "PICKER_ORDER",
    "picker_items",
    "picker_title",
    "picker_titles",
]
