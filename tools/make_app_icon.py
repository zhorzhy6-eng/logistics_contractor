#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Иконка приложения для .exe, ярлыков и установщика: resources/icon.ico.

Зачем
-----
У PyInstaller без `icon=` и у установщика без `SetupIconFile` в ярлыке
стоит стандартная пустая иконка Windows — программа выглядит чужой.
Своего файла .ico в проекте не было, а исходник есть: значок вкладки
«Договор» (resources/icons/tabs/contract.png, 64×64).

Что делает скрипт
-----------------
Берёт этот PNG и собирает из него многоразмерный .ico (16/24/32/48/64).
Один раз выполняется вручную; результат — resources/icon.ico — лежит в
git, поэтому для сборки установщика запускать его не нужно.

Запуск:
    python tools/make_app_icon.py

Если исходник когда-нибудь заменят своим логотипом, скрипт достаточно
запустить снова (или положить готовый .ico с тем же именем).
"""

import sys
from pathlib import Path

from PIL import Image

#: Корень проекта: tools/make_app_icon.py → tools/ → корень.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Откуда берётся рисунок (значок вкладки «Договор»).
SOURCE_PNG = PROJECT_ROOT / "resources" / "icons" / "tabs" / "contract.png"

#: Куда пишется иконка (её ждут logistics_contractor.spec и installer.iss).
TARGET_ICO = PROJECT_ROOT / "resources" / "icon.ico"

#: Размеры внутри .ico. 256 не добавляем: исходник 64×64, и растянутый
#: до 256 значок выглядит мыльным — лучше честные размеры.
ICON_SIZES = (16, 24, 32, 48, 64)


def main() -> int:
    """Собирает resources/icon.ico из PNG и печатает размеры."""
    if not SOURCE_PNG.is_file():
        print(f"нет исходника: {SOURCE_PNG}", file=sys.stderr)
        return 1

    source = Image.open(SOURCE_PNG).convert("RGBA")
    TARGET_ICO.parent.mkdir(parents=True, exist_ok=True)
    source.save(TARGET_ICO, format="ICO", sizes=[(size, size) for size in ICON_SIZES])

    print(f"иконка записана: {TARGET_ICO}")
    print(f"  исходник: {SOURCE_PNG.name} {source.size[0]}x{source.size[1]}")
    print(f"  размеры в .ico: {', '.join(f'{size}x{size}' for size in ICON_SIZES)}")

    with Image.open(TARGET_ICO) as check:
        print(f"  проверка: {check.format}, размеров {len(check.info.get('sizes', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
