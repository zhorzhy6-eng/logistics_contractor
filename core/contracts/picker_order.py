# -*- coding: utf-8 -*-
"""Порядок типов договоров в выпадающем списке (ЭТАП 2C).

Ключи — значения ContractType. Порядок задан явно: ContractTypeRegistry.
known_types() сортирует по алфавиту, а в списке нужен свой порядок.

Человекочитаемые названия пунктов живут НЕ здесь, а в
``core/contracts/picker_labels.py`` — этот модуль лежит в приватной части
проекта, потому что названия клиентских типов (коммерческая информация)
не должны попадать в публичный репозиторий. Подключение — обычный импорт
пакета: приватный корень добавлен в ``sys.path`` вызовом
``core.private_path.attach_private_root()`` (см. main.py, tests/conftest.py).

Приватной части нет — работает режим ядра: названия берутся из
``DEFAULT_LABELS`` (для «Экспедиторства» — рабочее название, для
остальных — ключ типа).

``picker_items()`` дополнительно сверяется с реестром типов: пункт, чей тип
не зарегистрирован (нет приватного модуля), в списке не показывается —
иначе пользователь увидел бы пункт, который не открывается. Константа
``PICKER_ORDER`` остаётся полной: по ней сверяются тесты и внешний код.

expediciya в список не входит — это служебный тип (не показывается
пользователю), см. ЭТАП 2A.

Модуль не зависит от Qt: порядок нужен и главному окну, и селектору
типа в шапке (ui/controls/contract_type_selector.py), и диалогу выбора
при запуске (ui/contract_picker.py реэкспортирует эти имена).
"""

from typing import Dict, List, Optional, Tuple

try:  # приватная часть проекта: клиентские названия типов
    from core.contracts.picker_labels import PICKER_LABELS
except ImportError:  # режим ядра: приватной папки нет
    PICKER_LABELS = {}

#: Название типа по умолчанию (публичное: это рабочий тип программы).
DEFAULT_LABELS: Dict[str, str] = {
    "perevozka": "Экспедиторство",
}

#: Ключи типов в рабочем порядке. Названия подставляются из
#: PICKER_LABELS; чего там нет — берётся из DEFAULT_LABELS, а иначе
#: показывается сам ключ (лучше ключ, чем пустая строка в списке).
PICKER_KEYS: Tuple[str, ...] = (
    "perevozka",
    "arenda_ts",
    "logistiks_rus",
    "formika",
    "zayavka_excel",
)


def picker_label(contract_type: str) -> str:
    """Название типа для интерфейса: приватный словарь → дефолт → ключ."""
    key = str(contract_type or "")
    for source in (PICKER_LABELS, DEFAULT_LABELS):
        title = source.get(key)
        if title:
            return str(title)
    return key


def _registered_keys() -> Optional[set]:
    """
    Ключи зарегистрированных типов или None, если реестр недоступен.

    Реестр наполняется при импорте пакетов типов; к моменту открытия окна
    он уже загружен. Если импорт реестра не удался (нестандартный запуск),
    фильтрация не применяется: список показывается целиком — это лучше,
    чем пустой список типов.
    """
    try:
        from core.contracts.registry import ContractTypeRegistry
    except Exception:  # noqa: BLE001 — список типов не должен ломать запуск
        return None

    known = ContractTypeRegistry.known_types()
    return set(known) if known else None


#: Пункты выпадающего списка: (ключ ContractType, название для пользователя).
#: Порядок — рабочий, а не алфавитный (см. picker_items для фильтрации по
#: зарегистрированным типам).
PICKER_ORDER: Tuple[Tuple[str, str], ...] = tuple(
    (key, picker_label(key)) for key in PICKER_KEYS
)

#: Тип, выбранный в списке по умолчанию (историческое поведение программы).
DEFAULT_PICKER_TYPE = "perevozka"


def picker_items() -> List[Tuple[str, str]]:
    """
    Пункты списка для интерфейса: только зарегистрированные типы.

    Порядок — как в PICKER_ORDER. Если ни один тип не зарегистрирован
    (реестр пуст или недоступен), возвращается полный список: пустой
    выпадающий список хуже, чем список с недоступными пунктами.
    """
    registered = _registered_keys()
    if registered is None:
        return list(PICKER_ORDER)
    return [(key, title) for key, title in PICKER_ORDER if key in registered]


def picker_titles() -> Dict[str, str]:
    """Словарь «ключ типа → название для пользователя» (все известные типы)."""
    return {key: title for key, title in PICKER_ORDER}


def picker_title(contract_type: str) -> str:
    """
    Название типа для заголовков окон.

    Неизвестный ключ возвращается как есть: заголовок окна не должен
    оказаться пустым из-за отсутствия названия в словаре.
    """
    key = str(contract_type or "")
    return picker_titles().get(key, key)


__all__ = [
    "DEFAULT_LABELS",
    "DEFAULT_PICKER_TYPE",
    "PICKER_KEYS",
    "PICKER_LABELS",
    "PICKER_ORDER",
    "picker_items",
    "picker_label",
    "picker_title",
    "picker_titles",
]
