#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Подключение приватной части проекта (клиентские типы договоров, шаблоны,
промпты, окна ввода и инструкции оператора).

Приватная часть лежит отдельной папкой и не входит в git публичного
проекта. Программа добавляет её корень в ``sys.path``, поэтому импорты вида
``from core.prompts.<ключ> import PROMPT`` или
``from ui.windows.<ключ> import ClientWindow`` находят модуль в приватной
папке.

Одного ``sys.path`` мало, и это главная грабля модуля. Публичные пакеты
``core`` и ``ui`` объявлены обычными пакетами (у них есть ``__init__.py``),
а обычный пакет «выигрывает» поиск у одноимённой папки на ``sys.path``:
подпакет ``core.contracts.<тип>`` искался бы ТОЛЬКО внутри публичного
``core`` и не нашёлся бы. Поэтому ``attach_private_root()`` дополнительно
расширяет ``__path__`` пакетов-родителей:

  * уже импортированных — сразу (``_extend_package_paths``);
  * импортируемых позже — перехватчиком импорта (``_PrivatePathFinder``).

Перехватчик нужен именно для ``core.contracts``: он импортируется ПОСЛЕ
подключения приватной части (при загрузке типов), и без перехвата его
``__path__`` остался бы публичным. Ровно так же это делают setuptools
и pytest для своих плагинов.

Если приватной папки нет — программа работает в режиме ядра: только
«Экспедиторство». Ошибок при этом не возникает: реестр типов
(``core/contracts/registry.py``) изолирует сбой импорта каждого типа,
промпты возвращают ``None`` (дефолтный промпт), а список выбора не
показывает пункты, которых нет в реестре.

Расположение приватной папки задаётся переменной окружения
``LOGISTICS_PRIVATE_ROOT`` (она ГЛАВНЕЕ остальных мест поиска).
"""

import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Iterable, Optional, Tuple

logger = logging.getLogger("core.private_path")

#: Переменная окружения с путём к приватной папке.
ENV_VAR = "LOGISTICS_PRIVATE_ROOT"

#: Имя приватной папки рядом с публичным проектом (второй вариант поиска).
PRIVATE_DIRNAME = "logistics_contractor_private"

#: Пакеты, чей ``__path__`` расширяется приватным корнем. Проверяются
#: только УЖЕ импортированные: импортировать Qt-пакеты отсюда нельзя —
#: модуль подключается и в тестах, где Qt не нужен.
PACKAGE_BOOTSTRAP: Tuple[str, ...] = (
    "core",
    "core.agent",
    "core.contracts",
    "core.prompts",
    "ui",
    "ui.windows",
    "tools",
)

#: Корень приватной части после успешного подключения (иначе None).
_attached_root: Optional[Path] = None


def default_locations() -> Tuple[Path, ...]:
    """
    Где искать приватную папку.

    Переменная окружения ``LOGISTICS_PRIVATE_ROOT`` ГЛАВНЕЕ остальных мест:
    если она задана, ищется ровно указанный путь. Иначе — папка рядом
    с публичным проектом, домашняя папка и подпапка ``private`` внутри
    публичного проекта.
    """
    env_value = os.environ.get(ENV_VAR, "").strip()
    if env_value:
        return (Path(env_value),)

    project_root = Path(__file__).resolve().parent.parent

    return (
        project_root.parent / PRIVATE_DIRNAME,
        Path.home() / PRIVATE_DIRNAME,
        project_root / "private",
    )


def find_private_root() -> Optional[Path]:
    """
    Ищет существующую приватную папку.

    Признак приватной части — подпапка ``core`` внутри: случайная папка
    с подходящим именем не должна попадать в ``sys.path``.
    """
    for candidate in default_locations():
        try:
            if candidate.is_dir() and (candidate / "core").is_dir():
                return candidate
        except OSError:
            continue
    return None


def private_root() -> Optional[Path]:
    """Корень приватной части, если она подключена (иначе None)."""
    return _attached_root


def private_path(*parts: str) -> Optional[Path]:
    """
    Путь внутри приватной части или None, если её нет.

    Пример: ``private_path("data", "knowledge", "tutorial")``.
    """
    if _attached_root is None:
        return None
    return _attached_root.joinpath(*parts)


def _private_dir_for(name: str, root: Path) -> Optional[Path]:
    """Папка приватной части для пакета ``name`` (None, если её нет)."""
    candidate = root
    for part in name.split("."):
        candidate = candidate / part
    try:
        return candidate if candidate.is_dir() else None
    except OSError:
        return None


def _path_str_for(name: str, root: Path) -> Optional[str]:
    """Строка приватного пути для пакета ``name`` (None — папки нет)."""
    candidate = _private_dir_for(name, root)
    return str(candidate) if candidate is not None else None


def _extend_package_paths(root: Path) -> None:
    """
    Расширяет ``__path__`` уже импортированных пакетов приватным корнем.

    Пакет, которого нет в ``sys.modules``, пропускается: его расширит
    перехватчик импорта (``_PrivatePathFinder``) в момент импорта — иначе
    будет гонка: ``core.contracts`` импортируется ПОСЛЕ подключения
    приватной части (при загрузке типов), и без перехватчика его ``__path__``
    остался бы публичным.
    """
    for name in PACKAGE_BOOTSTRAP:
        module = sys.modules.get(name)
        if module is None:
            continue

        package_path = getattr(module, "__path__", None)
        if package_path is None:
            continue

        path_str = _path_str_for(name, root)
        if path_str is None:
            continue

        if path_str not in package_path:
            package_path.append(path_str)
            logger.debug("Приватный путь добавлен пакету %s: %s", name, path_str)


class _PrivatePathFinder:
    """
    Перехватчик импорта: расширяет ``__path__`` пакета ПЕРЕД его импортом.

    Нужен ровно для одного: публичный пакет ``core`` объявлен обычным
    пакетом, и его подпакеты (``core.contracts``, ``core.prompts``) ищутся
    только внутри публичной папки. Приватная часть добавляется в
    ``__path__`` в момент импорта пакета-родителя, поэтому подпакет типа
    из приватной папки находится.

    Finder только ДОПОЛНЯЕТ путь и возвращает None: поиск модуля выполняют
    обычные механизмы Python (``PathFinder``), и подменять их результаты
    этот класс не должен.
    """

    #: Имя в sys.meta_path (для диагностики и тестов).
    name = "logistics_contractor_private_path"

    def __init__(self, root: Path):
        self._root = root

    def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002
        self._prepare(fullname)
        for part in self._parents(fullname):
            self._prepare(part)
        return None

    @staticmethod
    def _parents(fullname: str) -> Tuple[str, ...]:
        """Имена пакетов-родителей: «a.b.c» → («a», «a.b»)."""
        parts = fullname.split(".")
        return tuple(".".join(parts[:index]) for index in range(1, len(parts)))

    def _prepare(self, name: str) -> None:
        """Добавляет приватную папку пакета в его ``__path__`` (если есть)."""
        if name not in PACKAGE_BOOTSTRAP:
            return

        module = sys.modules.get(name)
        if module is None:
            return

        package_path = getattr(module, "__path__", None)
        if package_path is None:
            return

        path_str = _path_str_for(name, self._root)
        if path_str is None or path_str in package_path:
            return

        package_path.append(path_str)
        logger.debug("Приватный путь добавлен пакету %s: %s", name, path_str)


def _install_import_hook(root: Path) -> None:
    """Ставит перехватчик импорта первым в ``sys.meta_path`` (один раз)."""
    for finder in sys.meta_path:
        if getattr(finder, "name", "") == _PrivatePathFinder.name:
            return

    sys.meta_path.insert(0, _PrivatePathFinder(root))


def attach_private_root(path: Optional[Iterable[str]] = None) -> Optional[Path]:
    """
    Подключает приватную часть проекта (повторный вызов безопасен).

    :param path: готовый корень приватной части (для тестов и проб);
        None — искать по стандартным местам.
    :return: корень приватной части или None, если её нет.
    """
    global _attached_root

    if _attached_root is not None:
        return _attached_root

    if path is not None:
        root = Path(*path).resolve()
        if not root.is_dir():
            logger.warning("Приватная часть не найдена: %s", root)
            return None
    else:
        found = find_private_root()
        if found is None:
            logger.info(
                "Приватная часть не найдена — работаем в режиме ядра "
                "(только «Экспедиторство»)"
            )
            return None
        root = found.resolve()

    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
        logger.info("Приватная часть подключена: %s", root)

    _extend_package_paths(root)
    _install_import_hook(root)
    _attached_root = root
    return root


def detach_private_root() -> None:
    """
    Забывает подключение (только для тестов и проб).

    Из ``sys.path`` путь НЕ убирается: к этому моменту из приватной части
    уже импортированы модули, и их удаление из пути ничего не исправит.
    """
    global _attached_root
    _attached_root = None


def is_private_available() -> bool:
    """Подключена ли приватная часть (короткий ответ для UI и тестов)."""
    return _attached_root is not None or find_private_root() is not None


def optional_module(module_name: str):
    """
    Модуль из ``sys.path`` или None, если его нет.

    Нужна там, где публичный код обязан работать и без приватной части
    (фабрики окон в ``main.py``, инструменты агента). Сломанный модуль,
    который ЕСТЬ, но не импортируется, ошибкой не глушится: его причина
    уходит в журнал через ``logger.exception``, а вызывающий получает None.
    """
    try:
        available = importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):  # нет родителя / пустой __path__
        available = False

    if not available:
        logger.info("Приватный модуль недоступен: %s", module_name)
        return None

    try:
        return importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 — сломанный тип не должен ронять программу
        logger.exception("Приватный модуль не импортирован: %s", module_name)
        return None


__all__ = [
    "ENV_VAR",
    "PRIVATE_DIRNAME",
    "attach_private_root",
    "default_locations",
    "detach_private_root",
    "find_private_root",
    "is_private_available",
    "optional_module",
    "private_path",
    "private_root",
]
