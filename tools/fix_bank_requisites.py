#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Разовая правка банковских реквизитов в базе (ШАГ FIX-6, часть A2/A3).

Зачем: в готовом договоре в блоке реквизитов появилось
«Корреспондентский счет БИК 044030786» — подпись поля приехала ВМЕСТЕ со
значением (распознавание или перенос из чужого документа), а у одного
перевозчика в корр. счёте стояла 21 цифра. Программа такие значения
печатала как есть, и проверки на длину корр. счёта не было вовсе.

Что делает скрипт (только таблицы `carriers` и `customers`):

  * ИНН (10 или 12 цифр), КПП (9), ОГРН (13 или 15), БИК (9),
    расчётный счёт (20), корр. счёт (20) — оставляет ТОЛЬКО цифры:
    пробелы, дефисы и слова («Корреспондентский счет БИК», «ИНН»);
  * «Наименование банка» — срезает служебную подпись, прилипшую с конца
    («…АО "АЛЬФА-БАНК" г. Санкт-Петербург Корреспондентский счет»);
  * если после очистки длина НЕ совпадает со стандартом — запись НЕ
    меняется и попадает в журнал как «требует ручной проверки»:
    осмысленные значения скрипт не трогает и нули не додумывает.

Режимы:
    python tools/fix_bank_requisites.py            # сухой прогон (по умолчанию)
    python tools/fix_bank_requisites.py --apply    # запись + резервная копия

Перед записью делается резервная копия базы
(`core.security.backup_database`, VACUUM INTO — работает и при включённом WAL).

Логи — без ПДн: печатаются идентификаторы записей, имена полей, длины
«было → стало» и сами значения реквизитов (ФИО, адреса и телефоны не
выводятся вовсе).
"""

import argparse
import logging
import os
import re
import sqlite3
import sys
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.security import backup_database  # noqa: E402

logger = logging.getLogger("tools.fix_bank_requisites")

#: Таблицы организаций: перевозчики и заказчики.
TABLES = ("carriers", "customers")

#: Имя записи для журнала (в `carriers` это перевозчик, в `customers` — заказчик).
ROLE_BY_TABLE = {"carriers": "перевозчик", "customers": "заказчик"}

#: Поля и допустимые длины. Пустое значение — «не заполнено», это не ошибка.
#: Число длин больше одной там, где стандарт допускает варианты
#: (ИНН: 10 у организации, 12 у ИП; ОГРН: 13 у ООО, 15 у ИП).
FIELDS: Tuple[Tuple[str, str, Tuple[int, ...]], ...] = (
    ("inn", "ИНН", (10, 12)),
    ("kpp", "КПП", (9,)),
    ("ogrn", "ОГРН / ОГРНИП", (13, 15)),
    ("bank_account", "Расчётный счёт", (20,)),
    ("correspondent_account", "Корр. счёт", (20,)),
    ("bik", "БИК", (9,)),
)

#: Текстовые реквизиты, у которых с конца срезается служебная подпись.
#: Подпись следующего поля прилипает к имени банка, когда документ
#: разбирали построчно: «…АО "АЛЬФА-БАНК" г. Санкт-Петербург
#: Корреспондентский счет» + отдельно правильный БИК — в договоре это
#: читается как «Корреспондентский счетБИК 044030786».
TEXT_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("bank_name", "Наименование банка"),
)

#: Служебные подписи, которые могли прилипнуть к текстовому реквизиту.
SERVICE_LABELS: Tuple[str, ...] = (
    "Корреспондентский счет",
    "Корреспондентский счёт",
    "Корр. счет",
    "Корр. счёт",
    "Расчетный счет",
    "Расчётный счет",
    "Расчётный счёт",
    "К/с",
    "Р/с",
    "БИК",
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


class Problem(NamedTuple):
    """Запись, которую скрипт НЕ правит: нужна ручная проверка."""

    table: str
    row_id: int
    field: str
    label: str
    value: str
    reason: str

    @property
    def role(self) -> str:
        return ROLE_BY_TABLE.get(self.table, self.table)


def digits_only(value: Any) -> str:
    """Только цифры из значения (пустое значение → пустая строка)."""
    return re.sub(r"\D", "", "" if value is None else str(value))


def _looks_like_bik_prefix_glued(value: str) -> bool:
    """
    Признак «склейки»: цифры БИК приписаны к корр. счёту слева.

    У корреспондентского счёта первые три цифры — 301. Если в поле 21
    цифра и она убирается отбрасыванием ЛИШНИХ НУЛЕЙ (а не цифр вообще),
    скрипт это не исправляет: он только сообщает. Отдельная функция
    оставлена, чтобы причина в журнале была внятной.
    """
    return len(value) == 21 and value.startswith("301")


def trim_service_label(value: str) -> Optional[str]:
    """
    Срезает служебную подпись с конца текстового реквизита.

    «Филиал "АЛЬФА-БАНК" г. Санкт-Петербург\\nКорреспондентский счет»
    → «Филиал "АЛЬФА-БАНК" г. Санкт-Петербург». Возвращает None, если
    подписи нет или после срезки не остаётся названия: тогда запись
    уходит в «требует ручной проверки», а не правится вслепую.
    """
    text = value.strip()
    lowered = text.lower()

    for label in SERVICE_LABELS:
        if not lowered.endswith(label.lower()):
            continue

        trimmed = text[: len(text) - len(label)].strip(" \t\r\n,;:—-")
        if trimmed and re.search(r"[A-Za-zА-Яа-яЁё]", trimmed):
            return trimmed

    return None


def inspect_value(
    table: str,
    row_id: int,
    field: str,
    label: str,
    raw: Any,
    expected: Sequence[int],
) -> Tuple[Optional[Fix], Optional[Problem]]:
    """
    Разбирает одно поле: либо правка, либо запись «нужна ручная проверка».

    Пустое значение и значение, уже состоящее только из цифр нужной длины,
    не возвращают ничего: править нечего.
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None, None

    digits = digits_only(text)
    if digits == text and len(digits) in expected:
        return None, None

    # Цифр столько, сколько нужно, а лишнее — слова и разделители: правим.
    if len(digits) in expected:
        return Fix(table, row_id, field, label, text, digits), None

    if not digits:
        reason = "в поле нет ни одной цифры"
    elif len(digits) < min(expected):
        reason = f"цифр меньше, чем нужно ({len(digits)} вместо {expected[0]})"
    else:
        reason = f"цифр больше, чем нужно ({len(digits)})"

    if field == "correspondent_account" and _looks_like_bik_prefix_glued(digits):
        reason += "; похоже на склейку с БИК — проверить вручную"

    return None, Problem(table, row_id, field, label, text, reason)


def collect_fixes(conn: sqlite3.Connection) -> Tuple[List[Fix], List[Problem]]:
    """Проходит по таблицам и собирает правки и проблемные записи."""
    fixes: List[Fix] = []
    problems: List[Problem] = []

    for table in TABLES:
        if not _table_exists(conn, table):
            logger.warning("Таблицы %r нет в базе — пропущена", table)
            continue

        columns = _table_columns(conn, table)
        fields = [
            (field, label, expected)
            for field, label, expected in FIELDS
            if field in columns
        ]
        text_fields = [
            (field, label) for field, label in TEXT_FIELDS if field in columns
        ]
        if not fields and not text_fields:
            logger.warning("В таблице %r нет ни одного реквизита", table)
            continue

        selected = ", ".join(
            ["id"]
            + [field for field, _label, _exp in fields]
            + [field for field, _label in text_fields]
        )
        for row in conn.execute(f"SELECT {selected} FROM {table} ORDER BY id"):
            row_id = row[0]

            position = 1
            for field, label, expected in fields:
                fix, problem = inspect_value(
                    table, row_id, field, label, row[position], expected
                )
                position += 1
                if fix is not None:
                    fixes.append(fix)
                if problem is not None:
                    problems.append(problem)

            for field, label in text_fields:
                fix, problem = inspect_text_value(
                    table, row_id, field, label, row[position]
                )
                position += 1
                if fix is not None:
                    fixes.append(fix)
                if problem is not None:
                    problems.append(problem)

    return fixes, problems


def inspect_text_value(
    table: str,
    row_id: int,
    field: str,
    label: str,
    raw: Any,
) -> Tuple[Optional[Fix], Optional[Problem]]:
    """
    Разбирает текстовый реквизит: срезает прилипшую служебную подпись.

    Значение без подписи не трогается вовсе. Если после срезки не
    остаётся названия — это «требует ручной проверки».
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None, None

    trimmed = trim_service_label(text)
    if trimmed is None:
        return None, None

    if not re.search(r"[A-Za-zА-Яа-яЁё]", trimmed):
        return None, Problem(
            table, row_id, field, label, text,
            "после срезки служебной подписи не остаётся названия",
        )

    return Fix(table, row_id, field, label, text, trimmed), None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def apply_fixes(conn: sqlite3.Connection, fixes: Sequence[Fix]) -> int:
    """Записывает правки; возвращает число выполненных UPDATE."""
    changed = 0
    for fix in fixes:
        conn.execute(
            f"UPDATE {fix.table} SET {fix.field} = ? WHERE id = ?",
            (fix.after, fix.row_id),
        )
        changed += 1
    conn.commit()
    return changed


def report(fixes: Sequence[Fix], problems: Sequence[Problem], applied: bool) -> None:
    """Журнал: сколько проверено, что правится, что требует ручной проверки."""
    print("=" * 72)
    print("Правка банковских реквизитов в базе (ШАГ FIX-6)")
    print("Режим:", "ЗАПИСЬ" if applied else "сухой прогон (--apply не указан)")
    print("=" * 72)

    print(f"\nПредлагается исправить записей: {len(fixes)}")
    for fix in fixes:
        print(
            f"  [{fix.table} id={fix.row_id}, {fix.role}] {fix.label}: "
            f"{len(fix.before)} симв. → {fix.after!r}"
        )
        if fix.before != fix.after:
            print(f"      было: {fix.before!r}")

    print(f"\nТребует ручной проверки: {len(problems)}")
    for problem in problems:
        print(
            f"  [{problem.table} id={problem.row_id}, {problem.role}] "
            f"{problem.label}: {problem.value!r} — {problem.reason}"
        )

    if not fixes and not problems:
        print("\nНичего править не нужно: все реквизиты в порядке.")

    print("\nЗначения реквизитов в журнал попадают (они и так лежат в договоре),")
    print("наименования, адреса, телефоны и ФИО не выводятся.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Оставляет в числовых реквизитах организаций только цифры "
            "(ИНН, КПП, ОГРН, счета, БИК). По умолчанию — сухой прогон."
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
        "--verbose",
        action="store_true",
        help="подробный лог (DEBUG)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    db_path = os.path.abspath(args.db)
    if not os.path.exists(db_path):
        print(f"База не найдена: {db_path}")
        return 2

    print(f"База: {db_path}")
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        fixes, problems = collect_fixes(conn)

        if not args.apply or not fixes:
            report(fixes, problems, applied=False)
            if fixes and not args.apply:
                print("\nДля записи запустите: python tools/fix_bank_requisites.py --apply")
            return 0

        backup = backup_database(db_path, reason="fix_bank_requisites")
        if backup is None:
            print("Резервную копию сделать не удалось — запись отменена.")
            return 3
        print(f"Резервная копия: {backup}")

        changed = apply_fixes(conn, fixes)
        logger.info("Исправлено полей: %s", changed)

        report(fixes, problems, applied=True)
        print(f"\nЗаписано исправлений: {changed}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
