#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Возврат наименований организаций из резервной копии базы.

Зачем
-----
Шаг «Нормализация DaData» нормализовал у организации ТРИ поля —
`director_position`, `full_name`, `short_name`. Для должности это верно
(«ДИРЕКТОР» — формат хранения DaData, а не название), для наименования —
нет: авторитетный источник наименования это DaData (данные ЕГРЮЛ). Если у них
«ООО "АВАТЭК"» капсом, значит, так и в реестре, и «ООО "Аватэк"» в договоре —
уже другое наименование, а не оформление.

Правило в коде уже исправлено: `core/text_normalize.py::
normalize_organization_fields` правит только `director_position`. Этот скрипт
возвращает то, что успело испортиться в базе.

Что делает
----------
Читает РЕЗЕРВНУЮ КОПИЮ и рабочую базу, сопоставляет записи по `id` в таблицах
`carriers` и `customers` и возвращает из копии `full_name` и `short_name`,
если текущее значение отличается от копии ТОЛЬКО регистром — то есть было
нормализовано этим шагом, а не изменено оператором.

  * `director_position` НЕ трогается: должность нормализована правильно;
  * мягко удалённые записи правятся наравне с остальными: справочник их
    скрывает, но ссылки договоров на них живут;
  * запись, которой нет в копии, пропускается (восстанавливать не из чего);
  * если наименование отличается НЕ только регистром, запись тоже
    пропускается и попадает в журнал: это ручная правка оператора, и
    затирать её копией нельзя.

Режимы:
    python tools/restore_organization_names.py                 # сухой прогон
    python tools/restore_organization_names.py --apply         # запись + бэкап

Пути по умолчанию: копия — САМАЯ СТАРАЯ из `backup/contracts_*.db` (она
заведомо снята до правки; свежие копии создаёт сам `--apply`), рабочая база —
`contracts.db` в корне проекта. Оба пути можно задать явно
(`--backup`, `--db`).

Перед записью делается резервная копия рабочей базы
(`core.security.backup_database`, VACUUM INTO — работает и при включённом WAL).

Логи — БЕЗ ПДн: печатаются идентификаторы записей, имена полей и длины
«было → стало», но не сами значения. Показать значения можно явно, флагом
`--show-values`: наименования организаций не ПДн, но вывод на экран оператора
и запись в лог — разные вещи.

Записи обновляются через `db.crud.organizations.update_organization`: она
держит в согласии таблицу и полнотекстовый индекс FTS5 (иначе поиск нашёл бы
запись по строке, которой в таблице уже нет).
"""

import argparse
import glob
import logging
import os
import sqlite3
import sys
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.security import backup_database  # noqa: E402

logger = logging.getLogger("tools.restore_organization_names")

#: Таблицы организаций: перевозчики и заказчики.
TABLES: Tuple[str, ...] = ("carriers", "customers")

#: Имя записи для журнала (в `carriers` это перевозчик, в `customers` — заказчик).
ROLE_BY_TABLE = {"carriers": "перевозчик", "customers": "заказчик"}

#: Поля, которые возвращаются из копии. `director_position` здесь НЕТ
#: осознанно: должность нормализована правильно и остаётся как есть.
FIELDS: Tuple[Tuple[str, str], ...] = (
    ("full_name", "Полное наименование"),
    ("short_name", "Сокращённое наименование"),
)

#: Маска резервных копий в папке backup/.
BACKUP_GLOB = "contracts_*.db"


class Restore(NamedTuple):
    """Что предлагается вернуть из копии в одной записи."""

    table: str
    row_id: int
    field: str
    label: str
    current: str
    original: str

    @property
    def role(self) -> str:
        return ROLE_BY_TABLE.get(self.table, self.table)


class Skipped(NamedTuple):
    """Запись, которую скрипт НЕ трогает: нужно посмотреть глазами."""

    table: str
    row_id: int
    field: str
    label: str
    reason: str

    @property
    def role(self) -> str:
        return ROLE_BY_TABLE.get(self.table, self.table)


def reference_backup(db_path: Optional[str] = None) -> Optional[str]:
    """
    Копия базы, снятая ДО правки наименований (или None, если копий нет).

    Берётся САМАЯ СТАРАЯ копия из папки backup/. Почему не самая свежая:
    каждый запуск `--apply` делает копию ПЕРЕД записью, и после отката самой
    свежей оказалась бы копия, снятая ДО отката — то есть с уже испорченными
    наименованиями. Сравнение с ней показало бы «есть что восстанавливать» на
    ровном месте, хотя база уже верна (проверено: именно так и вышло).
    Копии же, снятые до правки, наименования не портили — в самой старой из
    них они заведомо в исходном виде.

    Если нужна конкретная копия — её путь задаётся явно: `--backup`.

    :param db_path: путь к рабочей базе (для сообщений; на выбор не влияет)
    """
    directory = os.path.join(PROJECT_ROOT, "backup")
    files = glob.glob(os.path.join(directory, BACKUP_GLOB))
    if not files:
        return None
    return min(files, key=os.path.getmtime)


def _read_rows(conn: sqlite3.Connection, table: str) -> Dict[int, Dict[str, Any]]:
    """Записи таблицы по id (только нужные колонки)."""
    columns = ", ".join(["id"] + [field for field, _ in FIELDS])
    cursor = conn.execute(f"SELECT {columns} FROM {table}")
    names = [description[0] for description in cursor.description]
    return {int(row[0]): dict(zip(names, row)) for row in cursor.fetchall()}


def collect_restores(
    backup_conn: sqlite3.Connection, db_conn: sqlite3.Connection,
) -> Tuple[List[Restore], List[Skipped], Dict[str, int]]:
    """
    Сопоставляет рабочую базу с копией и собирает список возвратов.

    :return: (возвраты, пропуски, счётчики) — счётчики нужны для журнала
             «проверено X → восстановлено Y → пропущено Z».
    """
    restores: List[Restore] = []
    skipped: List[Skipped] = []
    stats = {"checked": 0, "restored": 0}
    labels = dict(FIELDS)

    for table in TABLES:
        original_rows = _read_rows(backup_conn, table)
        current_rows = _read_rows(db_conn, table)

        for row_id, current in current_rows.items():
            stats["checked"] += 1
            original = original_rows.get(row_id)
            if original is None:
                skipped.append(Skipped(
                    table, row_id, "", "", "нет записи в копии",
                ))
                continue

            row_restores: List[Restore] = []
            for field, label in FIELDS:
                current_value = "" if current.get(field) is None else str(current[field])
                original_value = "" if original.get(field) is None else str(original[field])
                if current_value == original_value:
                    continue
                if current_value.casefold() != original_value.casefold():
                    # Отличие не только регистром: это ручная правка
                    # оператора, копией её затирать нельзя.
                    skipped.append(Skipped(
                        table, row_id, field, label,
                        "отличие не только регистром",
                    ))
                    continue
                row_restores.append(Restore(
                    table=table,
                    row_id=row_id,
                    field=field,
                    label=label,
                    current=current_value,
                    original=original_value,
                ))

            if row_restores:
                stats["restored"] += 1
                restores.extend(row_restores)

    return restores, skipped, stats


def apply_restores(restores: Sequence[Restore]) -> int:
    """
    Записывает возвраты через слой справочника (таблица + индекс FTS5).

    :return: сколько записей обновлено
    """
    from db.crud.organizations import load_organization, update_organization

    by_record: Dict[Tuple[str, int], Dict[str, str]] = {}
    for restore in restores:
        by_record.setdefault(
            (restore.table, restore.row_id), {},
        )[restore.field] = restore.original

    updated = 0
    for (table, row_id), fields in by_record.items():
        is_carrier = table == "carriers"
        # Полная запись: update_organization пишет ВСЕ колонки, и передать
        # ей только изменённые поля значило бы стереть остальные.
        record = load_organization(row_id, is_carrier=is_carrier)
        if not record:
            logger.warning(
                "Запись не найдена при записи возврата: %s ID=%s", table, row_id
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

    По умолчанию — только длина: логи проекта идут без ПДн, и правило одно
    для всех инструментов. `--show-values` печатает значение осознанно —
    это вывод на экран оператора, а не запись в лог.
    """
    text = value or ""
    return repr(text) if show_values else f"{len(text)} симв."


def report(restores: Sequence[Restore], skipped: Sequence[Skipped],
           stats: Dict[str, int], applied: bool,
           show_values: bool = False, limit: int = 0) -> None:
    """Журнал: «проверено X → восстановлено Y → пропущено Z» и список правок."""
    shown = restores if not limit else restores[:limit]
    for restore in shown:
        print(
            f"  {restore.role} ID={restore.row_id}: {restore.label} | "
            f"нормализовано {_preview(restore.current, show_values)} → "
            f"из копии {_preview(restore.original, show_values)}"
        )
    if limit and len(restores) > limit:
        print(f"  … ещё {len(restores) - limit} возвратов (см. --limit)")

    for item in skipped:
        if not item.field:
            print(f"  пропуск {item.role} ID={item.row_id}: {item.reason}")
            continue
        print(f"  пропуск {item.role} ID={item.row_id}: {item.label} — {item.reason}")

    checked = stats.get("checked", 0)
    restored = stats.get("restored", 0)
    print()
    print(f"Проверено записей: {checked} → восстановлено: {restored} → "
          f"пропущено: {checked - restored}")
    print(f"Полей к возврату: {len(restores)}")
    print("Записано: да" if applied else "Записано: нет (сухой прогон)")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Возвращает наименования организаций из резервной копии базы "
            "(должность не трогает). По умолчанию — сухой прогон."
        ),
    )
    parser.add_argument(
        "--db",
        default=os.path.join(PROJECT_ROOT, "contracts.db"),
        help="путь к рабочей базе (по умолчанию contracts.db в корне проекта)",
    )
    parser.add_argument(
        "--backup",
        default=None,
        help="путь к резервной копии (по умолчанию — свежая из backup/)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="записать возвраты (без флага — только показать)",
    )
    parser.add_argument(
        "--show-values",
        action="store_true",
        help="показать сами наименования (по умолчанию — только длины)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="сколько возвратов печатать (0 — все)",
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
        print(f"Рабочая база не найдена: {db_path}")
        return 2

    backup_path = (
        os.path.abspath(args.backup) if args.backup else reference_backup(db_path)
    )
    if not backup_path or not os.path.exists(backup_path):
        print("Резервная копия не найдена — восстанавливать не из чего.")
        return 2

    print(f"Рабочая база: {db_path}")
    print(f"Резервная копия: {backup_path}")

    # Путь к базе задаём ДО импорта слоя справочника: соединение спрашивает
    # его у фасада db.database (см. db/connection.py).
    import db.database as database

    database.DB_PATH = db_path

    backup_conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    try:
        db_conn = sqlite3.connect(db_path, timeout=30)
        try:
            restores, skipped, stats = collect_restores(backup_conn, db_conn)
        finally:
            db_conn.close()
    finally:
        backup_conn.close()

    if not restores:
        report(restores, skipped, stats, applied=False,
               show_values=args.show_values, limit=args.limit)
        print("Наименования уже как в копии — возвращать нечего.")
        return 0

    if not args.apply:
        report(restores, skipped, stats, applied=False,
               show_values=args.show_values, limit=args.limit)
        print("\nДля записи запустите: "
              "python tools/restore_organization_names.py --apply")
        return 0

    backup = backup_database(db_path, reason="restore_organization_names")
    if backup is None:
        print("Резервную копию сделать не удалось — запись отменена.")
        return 3
    print(f"Резервная копия рабочей базы: {backup}")

    updated = apply_restores(restores)
    logger.info("Записей обновлено: %s", updated)

    report(restores, skipped, stats, applied=True,
           show_values=args.show_values, limit=args.limit)
    print(f"Обновлено записей: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
