# -*- coding: utf-8 -*-
"""
Разовый инструмент: санитизация справочника салонов (ШАГ FIX-2.2, часть C).

Копирует рабочий файл «Места выгрузок.xlsx» в data/spravochnik_mest_vygruzki.xlsx,
оставляя только деловые графы:

    №, КОД, ИНН, КПП, Юр. Лицо, КОД (дубль), Город, Юридический адрес,
    Адрес доставки автомобилей, Комментарии (график и время приемки)

Графы с персональными данными (e-mail получателей уведомлений, контактные
телефоны, региональный менеджер) в файл проекта НЕ попадают: AGENTS.md § 4
запрещает коммитить ПДн. Оригинал остаётся локально в data/private/.
"""

import sys
from pathlib import Path

import openpyxl

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "private" / "Места выгрузок (оригинал).xlsx"
TARGET = ROOT / "data" / "spravochnik_mest_vygruzki.xlsx"

#: Индексы (с нуля) граф, которые переносятся в файл проекта.
KEEP_COLUMNS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12)


def main() -> int:
    source = openpyxl.load_workbook(str(SOURCE), data_only=True)
    source_sheet = source.active

    target = openpyxl.Workbook()
    target_sheet = target.active
    target_sheet.title = source_sheet.title

    rows = 0
    for row in source_sheet.iter_rows(values_only=True):
        values = [row[i] if i < len(row) else None for i in KEEP_COLUMNS]
        if not any(value is not None and str(value).strip() for value in values):
            continue
        target_sheet.append(values)
        rows += 1

    # Ширины по «Адресу доставки» и «Юр. Лицу», чтобы файл читался глазами.
    for column, width in (("B", 12), ("E", 32), ("G", 16), ("I", 60), ("J", 40)):
        target_sheet.column_dimensions[column].width = width

    target.save(str(TARGET))
    print(f"[OK] {TARGET}")
    print(f"     строк (с шапкой): {rows + 1}, граф: {len(KEEP_COLUMNS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
