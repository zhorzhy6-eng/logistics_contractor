#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Нормализация справочника организаций: должность капсом → «Директор».

Зачем: должность попадает в базу из внешних источников — DaData отдаёт
`management.post` как «ГЕНЕРАЛЬНЫЙ ДИРЕКТОР», выписки ЕГРЮЛ и OCR — как
«ДИРЕКТОР». В договоре печатается то, что лежит в базе, поэтому капс из
справочника уходит в бланк: «в лице ДИРЕКТОРА».

Что правит скрипт (только таблицы `carriers` и `customers`):

  * `director_position` — «ДИРЕКТОР» → «Директор», «ГЕНЕРАЛЬНЫЙ ДИРЕКТОР» →
    «Генеральный директор»;
  * значения в обычном регистре, пустые и короткие сокращения («ИП», «ООО»,
    «АО») не меняются вовсе.

НАИМЕНОВАНИЯ НЕ ТРОГАЮТСЯ (`full_name`, `short_name`): для них авторитетный
источник — DaData (данные ЕГРЮЛ). «ООО "АВАТЭК"» капсом — это запись реестра,
а «ООО "Аватэк"» было бы уже другим наименованием. Если такая правка уже
случилась, её возвращает `tools/restore_organization_names.py`.

Режимы:
    python tools/normalize_organizations.py            # сухой прогон (по умолчанию)
    python tools/normalize_organizations.py --apply    # запись + резервная копия

Перед записью делается резервная копия базы
(`core.security.backup_database`, VACUUM INTO — работает и при включённом WAL).

Числовые реквизиты (ИНН, КПП, ОГРН, счета, БИК) и ФИО руководителя скрипт НЕ
трогает: у ФИО регистр значим («Иванов» ≠ «иванов»), а реквизиты — предмет
другого инструмента (`tools/fix_bank_requisites.py`).

Логи — БЕЗ ПДн: печатаются идентификаторы записей, имена полей, длины
«было → стало» и признаки (капс), но не сами значения. Показать значения
можно явно, флагом `--show-values`, — это вывод на экран оператора, не в лог.

Записи обновляются через `db.crud.organizations.update_organization`: она
держит в согласии таблицу и полнотекстовый индекс FTS5 (иначе поиск нашёл бы
запись по строке, которой в таблице уже нет).
"""

import argparse
import logging
import os
import sys
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.security import backup_database  # noqa: E402
from core.text_normalize import normalize_organization_fields  # noqa: E402

logger = logging.getLogger("tools.normalize_organizations")

#: Таблицы организаций: перевозчики и заказчики.
TABLES: Tuple[str, ...] = ("carriers", "customers")

#: Имя записи для журнала (в `carriers` это перевозчик, в `customers` — заказчик).
ROLE_BY_TABLE = {"carriers": "перевозчик", "customers": "заказчик"}

#: Поля, в которых капс правится. Сейчас это ОДНО поле — должность
#: руководителя: наименования приходят из ЕГРЮЛ через DaData, и капс в них
#: это данные, а не формат источника (см. документацию модуля).
FIELDS: Tuple[Tuple[str, str], ...] = (
    ("director_position", "Должность руководителя"),
)


class Fix(NamedTuple):
    """Что предлагается поправить в одной записи."""

    table: str
    row_id: int
    field: str
    label: str
    before: str
    after: str

    @property
    def role(self) -> str:
        return ROLE_BY_TABLE.get(self.table, self.table)


def normalized_values(row: Dict[str, Any]) -> Dict[str, str]:
    """
    Новые значения полей записи (только те, что реально меняются).

    Правила регистра — общие для всего проекта (`core/text_normalize.py`),
    поэтому «Директор» и «директор» остаются как есть, а «ДИРЕКТОР»
    становится «Директором».
    """
    fixed = normalize_organization_fields(dict(row))
    changed: Dict[str, str] = {}
    for field, _label in FIELDS:
        before = "" if row.get(field) is None else str(row.get(field))
        after = "" if fixed.get(field) is None else str(fixed.get(field))
        if before != after:
            changed[field] = after
    return changed


def collect_fixes(conn) -> Tuple[List[Fix], Dict[str, int]]:
    """
    Обходит обе таблицы и собирает список правок.

    :return: (правки, счётчики) — счётчики нужны для журнала
             «проверено X → исправлено Y → пропущено Z».
    """
    fixes: List[Fix] = []
    stats = {"checked": 0, "fixed": 0}
    labels = dict(FIELDS)

    for table in TABLES:
        columns = ", ".join(["id"] + [field for field, _ in FIELDS])
        cursor = conn.execute(f"SELECT {columns} FROM {table}")
        names = [description[0] for description in cursor.description]

        for row in cursor.fetchall():
            record = dict(zip(names, row))
            stats["checked"] += 1
            changed = normalized_values(record)
            if not changed:
                continue
            stats["fixed"] += 1
            for field, _label in FIELDS:
                if field not in changed:
                    continue
                fixes.append(Fix(
                    table=table,
                    row_id=int(record["id"]),
                    field=field,
                    label=labels[field],
                    before=str(record.get(field) or ""),
                    after=changed[field],
                ))
    return fixes, stats


def apply_fixes(fixes: Sequence[Fix]) -> int:
    """
    Записывает правки через слой справочника (таблица + индекс FTS5).

    :return: сколько записей обновлено
    """
    from db.crud.organizations import (
        load_organization,
        update_organization,
    )

    by_record: Dict[Tuple[str, int], Dict[str, str]] = {}
    for fix in fixes:
        by_record.setdefault((fix.table, fix.row_id), {})[fix.field] = fix.after

    updated = 0
    for (table, row_id), fields in by_record.items():
        is_carrier = table == "carriers"
        # Полная запись: update_organization пишет ВСЕ колонки, и передать
        # ей только изменённые поля значило бы стереть остальные.
        record = load_organization(row_id, is_carrier=is_carrier)
        if not record:
            logger.warning(
                "Запись не найдена при записи правки: %s ID=%s", table, row_id
            )
            continue
        record.update(fields)
        if update_organization(row_id, record, is_carrier=is_carrier):
            updated += 1
        else:
            logger.error("Не удалось обновить запись: %s ID=%s", table, row_id)
    return updated


def _preview(value: str, show_values: bool) -> str:
    """
    Представление значения для журнала.

    По умолчанию — только длина: в справочнике лежат ПДн (ФИО руководителя,
    адреса), и печатать их в журнал нельзя. `--show-values` печатает
    значение осознанно — это вывод на экран оператора, а не запись в лог.
    """
    text = value or ""
    return repr(text) if show_values else f"{len(text)} симв."


def report(fixes: Sequence[Fix], stats: Dict[str, int], applied: bool,
           show_values: bool = False, limit: int = 0) -> None:
    """Журнал: «проверено X → исправлено Y → пропущено Z» и список правок."""
    shown = fixes if not limit else fixes[:limit]
    for fix in shown:
        print(
            f"  {fix.role} ID={fix.row_id}: {fix.label} | "
            f"капс {_preview(fix.before, show_values)} → "
            f"{_preview(fix.after, show_values)}"
        )
    if limit and len(fixes) > limit:
        print(f"  … ещё {len(fixes) - limit} правок (см. --limit)")

    checked = stats.get("checked", 0)
    fixed = stats.get("fixed", 0)
    skipped = checked - fixed
    print()
    print(f"Проверено записей: {checked} → исправлено: {fixed} → пропущено: {skipped}")
    print(f"Полей к правке: {len(fixes)}")
    print("Записано: да" if applied else "Записано: нет (сухой прогон)")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Приводит должности и наименования организаций из капса "
            "к обычному регистру («ДИРЕКТОР» → «Директор»). "
            "По умолчанию — сухой прогон."
        ),
    )
    parser.add_argument(
        "--db",
        default=os.path.join(PROJECT_ROOT, "contracts.db"),
        help="путь к базе (по умолчанию contracts.db в корне проекта)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="записать исправления (без флага — только показать)",
    )
    parser.add_argument(
        "--show-values",
        action="store_true",
        help="показать сами значения (по умолчанию — только длины: в базе ПДн)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="сколько правок печатать (0 — все)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="подробный лог (DEBUG)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    db_path = os.path.abspath(args.db)
    if not os.path.exists(db_path):
        print(f"База не найдена: {db_path}")
        return 2

    # Путь к базе задаём ДО импорта слоя справочника: соединение спрашивает
    # его у фасада db.database (см. db/connection.py).
    import db.database as database

    database.DB_PATH = db_path

    print(f"База: {db_path}")

    conn = database.get_connection()
    try:
        fixes, stats = collect_fixes(conn)
    finally:
        conn.close()

    if not fixes:
        report(fixes, stats, applied=False, show_values=args.show_values,
               limit=args.limit)
        print("Капс в справочнике не найден — править нечего.")
        return 0

    if not args.apply:
        report(fixes, stats, applied=False, show_values=args.show_values,
               limit=args.limit)
        print("\nДля записи запустите: "
              "python tools/normalize_organizations.py --apply")
        return 0

    backup = backup_database(db_path, reason="normalize_organizations")
    if backup is None:
        print("Резервную копию сделать не удалось — запись отменена.")
        return 3
    print(f"Резервная копия: {backup}")

    updated = apply_fixes(fixes)
    logger.info("Записей обновлено: %s", updated)

    report(fixes, stats, applied=True, show_values=args.show_values,
           limit=args.limit)
    print(f"Обновлено записей: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
