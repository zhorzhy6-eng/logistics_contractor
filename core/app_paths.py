#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Пути приложения: корень программы и ресурсы (подготовка сборки .exe).

Зачем модуль
------------
В исходниках всё просто: `templates/`, `resources/`, `data/`, `config/`
лежат в корне проекта, и любой модуль может посчитать корень от своего
`__file__`. В собранном PyInstaller-приложении это ломается по двум
причинам, и обе тихие:

1. **Код живёт в архиве, а не в файлах.** Модули упакованы в PYZ, поэтому
   `Path(__file__).parent` указывает на путь, которого на диске НЕТ.
   Рядом с таким «файлом» ничего не лежит, и поиск ресурса молча даёт
   пустоту.
2. **Папок две, и они разные.** `sys._MEIPASS` — папка с РЕСУРСАМИ
   (в `--onedir` это `<сборка>/_internal`), а `sys.executable` лежит в
   папке ПРОГРАММЫ. Читать надо из первой, писать — во вторую: папка
   сборки при переустановке заменяется целиком, и всё записанное в неё
   пропадёт (а если программа стоит в `Program Files`, то и записать
   не даст).

Отсюда две разные функции, и путать их нельзя:

* ``resource_path("templates")`` — ЧИТАТЬ шаблоны, иконки, правовую базу;
* ``writable_path("config/settings.json")`` — ПИСАТЬ настройки, базу,
  журналы, готовые договоры.

В разработке обе возвращают корень проекта, поэтому поведение тестов и
обычного запуска не меняется: `pytest` идёт по тем же путям, что раньше.

Как проверяется
---------------
`tests/unit/core/test_app_paths.py` — сборка подменяется переменными
`sys.frozen` / `sys._MEIPASS` / `sys.executable`, и проверяется, что
читаемое берётся из `_MEIPASS`, а записываемое — рядом с `.exe`.

Модуль без зависимостей проекта: только стандартная библиотека. Его
импортируют и `core/contracts/paths.py`, и `ui/icons.py`, и настройки —
циклов быть не должно.
"""

import logging
import sys
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger("core.app_paths")

#: Маркеры корня проекта в режиме разработки. Тот же приём, что в
#: `infrastructure/api/tls.py`: маркер надёжнее счёта уровней вверх — при
#: переезде модуля счёт молча ломается, а маркер остаётся на месте.
DEV_ROOT_MARKERS = ("pytest.ini", "main.py")

#: Сколько уровней вверх искать маркер корня проекта.
_ROOTS_TO_CHECK = 6


def is_frozen() -> bool:
    """Запущена ли программа из собранного .exe (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def _meipass() -> Optional[Path]:
    """Папка ресурсов сборки (`sys._MEIPASS`) или None в разработке."""
    raw = getattr(sys, "_MEIPASS", "")
    if not raw:
        return None
    try:
        return Path(str(raw))
    except (OSError, ValueError):  # pragma: no cover — кривой путь в env
        return None


def dev_root() -> Path:
    """
    Корень проекта при запуске из исходников.

    Ищется по маркеру (`pytest.ini` или `main.py`) вверх от этого файла.
    Нет маркера (например, проект скопировали без `pytest.ini`) — берётся
    папка двумя уровнями выше: `core/app_paths.py` → `core/` → корень.
    """
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:_ROOTS_TO_CHECK]:
        if any((parent / marker).exists() for marker in DEV_ROOT_MARKERS):
            return parent
    return here.parent.parent


def app_root() -> Path:
    """
    Папка ПРОГРАММЫ — та, рядом с которой можно писать.

    * .exe — папка с `LogisticsContractor.exe`;
    * исходники — корень проекта.

    Здесь живут `config/settings.json`, база `contracts.db`, `output/` и
    `logs/`: это изменяемые файлы, и в папке сборки (`_MEIPASS`) им не
    место — её заменяют при обновлении.
    """
    if is_frozen():
        executable = str(getattr(sys, "executable", "") or "")
        if executable:
            return Path(executable).resolve().parent
        logger.warning(
            "Признак сборки есть, а sys.executable пуст — берём корень проекта"
        )
    return dev_root()


def bundle_root() -> Path:
    """
    Папка РЕСУРСОВ — та, откуда только читают.

    * .exe — `sys._MEIPASS` (в `--onedir` это `<сборка>/_internal`);
    * исходники — корень проекта.

    Здесь лежат `templates/`, `resources/`, `data/`, правовая база и
    тексты промптов: всё, что приезжает вместе со сборкой и не меняется
    в работе.
    """
    meipass = _meipass()
    if meipass is not None:
        return meipass
    return app_root()


def _join(base: Path, relative: Union[str, Path, None]) -> Path:
    """Приклеивает относительный путь к базе (пустой — сама база)."""
    if relative is None or str(relative) == "":
        return base
    path = Path(relative)
    if path.is_absolute():
        # Абсолютный путь уже полный: приклеивать его к базе нельзя —
        # «C:/x» превратился бы в «<база>/C:/x».
        return path
    return base / path


def resource_path(relative: Union[str, Path, None] = "") -> Path:
    """
    Путь к ЧИТАЕМОМУ ресурсу: `resource_path("templates")`.

    Абсолютный путь возвращается как есть: вызывающий уже знает, что
    делает (например, сертификат из настроек).

    :param relative: путь внутри сборки/проекта; пусто — корень ресурсов.
    """
    return _join(bundle_root(), relative)


def writable_path(relative: Union[str, Path, None] = "") -> Path:
    """
    Путь к ИЗМЕНЯЕМОМУ файлу: `writable_path("config/settings.json")`.

    :param relative: путь внутри папки программы; пусто — сама папка.
    """
    return _join(app_root(), relative)


def ensure_dir(path: Union[str, Path]) -> Path:
    """Создаёт папку (вместе с родителями) и возвращает её как Path."""
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def describe() -> str:
    """
    Строка для журнала и диагностики: где программа и где ресурсы.

    Пишется один раз при старте: при разборе «почему не нашёлся шаблон»
    первая строка журнала сразу показывает раскладку.
    """
    mode = "exe" if is_frozen() else "разработка"
    return (
        f"режим: {mode}; программа: {app_root()}; "
        f"ресурсы: {bundle_root()}; python: {sys.version.split()[0]}"
    )


#: Внимание: константы корня здесь НЕТ намеренно. Любой путь считается
#: функцией в момент вызова — иначе тест, подменивший `sys.frozen` или
#: `sys._MEIPASS`, получил бы значение, посчитанное при импорте.

__all__ = [
    "DEV_ROOT_MARKERS",
    "app_root",
    "bundle_root",
    "describe",
    "dev_root",
    "ensure_dir",
    "is_frozen",
    "resource_path",
    "writable_path",
]
