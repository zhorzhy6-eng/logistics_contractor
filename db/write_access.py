#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Права на файл базы: починка «только чтение» и понятные ошибки записи.

Зачем модуль
------------
Жалоба «Не удалось сохранить данные: attempt to write a readonly database» —
это не дефект кода, а состояние файлов: после переезда данных в
`%APPDATA%\\LogisticsContractor` (v1.4.1), копирования базы или «помощи»
антивируса `contracts.db` может получить атрибут «Только чтение». SQLite
такую базу ОТКРЫВАЕТ (чтение работает), а отказывает только на первой
записи: оператор заполняет форму и узнаёт о проблеме в самом конце.

Что делает модуль
-----------------
1. **Починка.** Снимает атрибут «Только чтение» (`os.chmod(..., S_IWRITE)`)
   с файла базы и его спутников `-wal`/`-shm`; проверяет, что в папке
   данных можно создавать файлы — иначе SQLite не создаст журнал.
2. **Проба записи.** На первом соединении пробует НАСТОЯЩУЮ запись и сразу
   её откатывает (`CREATE TABLE` + `ROLLBACK`). База при этом не меняется,
   а отказ виден до того, как оператор что-то заполнил.
3. **Понятный текст.** Ошибки SQLite переводятся в объяснение для
   оператора: что случилось, что нажать, при чём тут вторая копия
   программы и антивирус. Текст один на журнал и на окно — см.
   `main.py::_warn_if_database_readonly` и `ui/main_window/save.py`.

Почему проба — `CREATE TABLE` + откат, а не `PRAGMA query_only`
--------------------------------------------------------------
`PRAGMA query_only` — настройка САМОГО соединения (мы её не включаем),
поэтому на базу «только для чтения» она не реагирует: на файле с атрибутом
«только чтение» по-прежнему отдаёт 0. `PRAGMA journal_mode = WAL` тоже
проходит молча (база уже в WAL). `BEGIN IMMEDIATE` берёт блокировку, но
ничего не пишет. Ошибка `SQLITE_READONLY` появляется только на РЕАЛЬНОЙ
записи — проверено пробой на этой машине (Python 3.14, SQLite 3.50.4).
Поэтому проба делает настоящую запись и откатывает её.

Чего модуль НЕ делает
---------------------
* не меняет списки доступа (ACL) и не запускает `icacls`: сужение прав —
  работа `core/security.py`, а расширять права автоматически нельзя;
* не удаляет, не переименовывает и не копирует файлы базы;
* не блокирует запуск: причина пишется в журнал, а окно с подсказкой
  показывает `main.py` — у собранного `.exe` консоли нет, и `logger.error`
  оператору увидеть негде.

Как проверяется
---------------
`tests/integration/db/test_db_write_access.py` — починка атрибута на
настоящем файле, проба записи без следа в базе, отказ при запрете записи,
тексты ошибок. `tests/ui/...` — окно при старте и диалог сохранения.
"""

import logging
import os
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union

logger = logging.getLogger("db.write_access")

#: Имя пробы записи. В базе не остаётся: транзакция всегда откатывается.
PROBE_TABLE = "lc_write_probe"

#: Спутники базы: в режиме WAL SQLite пишет журнал и общую память рядом.
SIDECARS = ("-wal", "-shm")

#: Признаки ошибки «только для чтения» в тексте SQLite (англ. сообщение).
READONLY_MARKERS = ("readonly", "read-only", "read only")

#: Признаки занятой базы: её держит другая копия программы или антивирус.
LOCKED_MARKERS = ("database is locked", "database table is locked",
                  "database is busy", "database schema is locked")

#: Место базы по умолчанию — для текста, когда путь не передан.
DEFAULT_DB_HINT = "%APPDATA%\\LogisticsContractor\\contracts.db"

#: Причина, которую нужно показать оператору после старта Qt (см. ниже).
_startup_problem = ""


@dataclass(frozen=True)
class WriteAccessReport:
    """
    Итог проверки прав на файл базы (см. `prepare_database`).

    :param path: проверенный путь (как передан).
    :param repaired: файлы, у которых снят атрибут «только чтение».
    :param problem: объяснение для оператора; пусто — писать можно.
    """

    path: str
    repaired: Tuple[str, ...] = ()
    problem: str = ""

    @property
    def ok(self) -> bool:
        """True — запись в базу возможна (после починки, если она была нужна)."""
        return not self.problem


# ─────────────────────────────────────────────────────────────
# Пути и проверки доступа
# ─────────────────────────────────────────────────────────────

def local_file(path: Union[str, Path, None]) -> Optional[Path]:
    """
    Путь к обычному файлу или None для путей без файла на диске.

    `":memory:"` и URI вида `file:...?mode=ro` — не файлы: права и атрибуты
    к ним не применяются, проверять нечего.
    """
    text = str(path or "").strip()
    if not text or text == ":memory:" or text.startswith("file:"):
        return None
    try:
        return Path(text)
    except (TypeError, ValueError):  # pragma: no cover — str() всегда валиден
        return None


def related_files(path: Union[str, Path, None]) -> Tuple[Path, ...]:
    """Файл базы и его спутники `-wal`/`-shm` (только существующие)."""
    base = local_file(path)
    if base is None:
        return ()
    candidates = [base]
    candidates.extend(base.with_name(base.name + suffix) for suffix in SIDECARS)
    return tuple(candidate for candidate in candidates if candidate.is_file())


def is_writable(path: Union[str, Path]) -> bool:
    """
    Можно ли писать в файл (атрибут «только чтение» и права доступа).

    На Windows `os.access(..., os.W_OK)` отдаёт False именно для файла с
    атрибутом «Только чтение» (проверено). Списки доступа (ACL) этой
    проверкой не видны — их ловит проба записи.
    """
    try:
        return os.access(str(path), os.W_OK)
    except OSError:  # pragma: no cover — путь мог исчезнуть между вызовами
        return False


def clear_readonly(path: Union[str, Path]) -> bool:
    """
    Снимает атрибут «только чтение» с одного файла.

    :return: True, если файл стал доступен для записи.
    """
    target = Path(str(path))
    try:
        mode = os.stat(target).st_mode
        os.chmod(target, mode | stat.S_IWRITE)
    except OSError as error:
        logger.warning(
            "Не удалось снять атрибут «только чтение» с %s: %s", target, error
        )
        return False
    return is_writable(target)


def clear_readonly_files(path: Union[str, Path, None]) -> Tuple[str, ...]:
    """
    Снимает «только чтение» с файла базы и его спутников, если они есть.

    Вызывается ДО `sqlite3.connect`: SQLite открывает файл с атрибутом
    «только чтение» как read-only, и снять атрибут на уже открытом
    соединении бесполезно — нужен новый дескриптор.

    :return: пути файлов, которым вернули право записи.
    """
    repaired: List[str] = []
    for candidate in related_files(path):
        if is_writable(candidate):
            continue
        logger.warning("Файл помечен «только чтение»: %s", candidate)
        if clear_readonly(candidate):
            repaired.append(str(candidate))
            logger.warning("Атрибут «только чтение» снят: %s", candidate)
    return tuple(repaired)


def directory_problem(directory: Union[str, Path]) -> str:
    """
    Можно ли создавать файлы в папке базы (SQLite кладёт туда журнал).

    Проверяется настоящим файлом, а не `os.access`: списки доступа Windows
    `os.access` не видит, а `-wal`/`-shm` без права создания не появятся.

    :return: причина словами или пустая строка, если писать можно.
    """
    folder = Path(str(directory))
    if not folder.is_dir():
        return f"Папка данных не найдена: {folder}"

    try:
        descriptor, probe = tempfile.mkstemp(
            prefix=".lc_write_check_", suffix=".tmp", dir=str(folder)
        )
    except OSError as error:
        return (
            f"В папке данных нельзя создавать файлы: {folder} "
            f"({error}). SQLite не создаст журнал базы (contracts.db-wal)."
        )

    os.close(descriptor)
    try:
        os.remove(probe)
    except OSError:  # pragma: no cover — удалить нечем, места всё равно нет
        logger.debug("Проверочный файл не удалён: %s", probe)
    return ""


# ─────────────────────────────────────────────────────────────
# Проба записи
# ─────────────────────────────────────────────────────────────

def probe_write(connection: sqlite3.Connection) -> None:
    """
    Проверяет запись в базу настоящей транзакцией и откатывает её.

    Создаёт служебную таблицу `lc_write_probe` внутри транзакции и делает
    `ROLLBACK`: изменение в базу не попадает, но SQLite успевает упереться
    в атрибут «только чтение», запрет прав или блокировку.

    :raises sqlite3.DatabaseError: запись невозможна (ошибка SQLite как есть).
    """
    if connection.in_transaction:
        # Соединение занято чужой транзакцией: откатывать её нельзя.
        logger.debug("Проба записи пропущена: соединение внутри транзакции")
        return

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(f"CREATE TABLE {PROBE_TABLE} (x INTEGER)")
    except sqlite3.OperationalError as error:
        # След от прошлой аварии — не повод считать базу здоровой, но и не
        # ошибка: запись уже была проверена самим CREATE.
        if "already exists" in str(error).lower():
            return
        raise
    finally:
        connection.rollback()


# ─────────────────────────────────────────────────────────────
# Причины и тексты для оператора
# ─────────────────────────────────────────────────────────────

def classify_error(error: BaseException) -> str:
    """
    Вид ошибки записи: ``"readonly"``, ``"locked"`` или ``"other"``.

    Опираемся и на код SQLite (`sqlite_errorname`), и на текст: код есть
    не у всех исключений (например, у собранных вручную `OperationalError`).
    """
    code = str(getattr(error, "sqlite_errorname", "") or "").upper()
    text = str(error).lower()
    if code.startswith("SQLITE_READONLY") or any(
        marker in text for marker in READONLY_MARKERS
    ):
        return "readonly"
    if code in ("SQLITE_BUSY", "SQLITE_LOCKED") or any(
        marker in text for marker in LOCKED_MARKERS
    ):
        return "locked"
    return "other"


def readonly_message(path: Union[str, Path, None] = None, reason: str = "") -> str:
    """
    Объяснение «база доступна только для чтения» с шагами для оператора.

    :param path: файл базы (для журнала и для поиска в проводнике).
    :param reason: техническая причина (например, ошибка SQLite).
    """
    target = str(path or DEFAULT_DB_HINT)
    lines = [
        "База данных доступна только для чтения — сохранить изменения нельзя.",
        "",
        f"Файл базы: {target}",
    ]
    if reason:
        lines.append(f"Причина: {reason}")
    lines += [
        "",
        "Что сделать:",
        "1. Закройте вторую копию программы, если она запущена: два "
        "экземпляра на одной базе мешают записи.",
        "2. Снимите атрибут «Только чтение» с файла contracts.db и папки "
        "LogisticsContractor: проводник → правая кнопка → «Свойства» → "
        "снять галочку «Только чтение» → «Применить».",
        "3. Если установлен антивирус — добавьте папку данных "
        "(%APPDATA%\\LogisticsContractor) в исключения: он может держать "
        "файлы contracts.db-wal и contracts.db-shm.",
        "4. Если атрибут «Только чтение» уже снят, а ошибка осталась — "
        "проверьте права: проводник → «Свойства» → «Безопасность» → у вашей "
        "учётной записи должен быть «Полный доступ». Нет — нужен администратор.",
        "5. Запустите программу снова: доступ к записи проверяется при "
        "старте, а не в момент сохранения.",
    ]
    return "\n".join(lines)


def locked_message(path: Union[str, Path, None] = None) -> str:
    """Объяснение «база занята»: чаще всего это вторая копия программы."""
    lines = [
        "База данных занята другой программой.",
        "",
        f"Файл базы: {path or DEFAULT_DB_HINT}",
        "",
        "Что сделать:",
        "1. Закройте вторую копию программы, если она запущена, и "
        "повторите сохранение.",
        "2. Если копия одна — повторите через несколько секунд: файл мог "
        "занять антивирус или резервное копирование.",
    ]
    return "\n".join(lines)


def explain_error(
    error: BaseException, path: Union[str, Path, None] = None
) -> str:
    """
    Объяснение ошибки SQLite для оператора или пустая строка.

    Пустая строка значит «это не про права и не про занятость» — вызывающий
    показывает исходный текст, как раньше.
    """
    kind = classify_error(error)
    if kind == "readonly":
        return readonly_message(path, f"SQLite: {error}")
    if kind == "locked":
        return locked_message(path)
    return ""


def save_error_text(
    error: BaseException, path: Union[str, Path, None] = None
) -> str:
    """
    Текст диалога для `ui/main_window/save.py` («Не удалось сохранить данные»).

    Для «только чтения» и «занята» — объяснение с шагами; для остальных
    ошибок — прежний текст с технической причиной.
    """
    explanation = explain_error(error, path)
    if not explanation:
        return f"Не удалось сохранить данные:\n{error}"
    return f"Не удалось сохранить данные.\n\n{explanation}"


# ─────────────────────────────────────────────────────────────
# Проверка при старте
# ─────────────────────────────────────────────────────────────

def prepare_database(path: Union[str, Path, None]) -> WriteAccessReport:
    """
    Проверяет (и по возможности починяет) права на файл базы.

    Порядок: снять «только чтение» с базы и спутников → убедиться, что
    файл базы доступен для записи → убедиться, что в папке можно создавать
    файлы (журнал WAL). Причину отказа возвращает готовым текстом.

    :return: отчёт с путями починенных файлов и объяснением проблемы.
    """
    target = local_file(path)
    if target is None:
        return WriteAccessReport(path=str(path or ""))

    repaired = clear_readonly_files(target)

    problems: List[str] = []
    if target.is_file() and not is_writable(target):
        problems.append(
            f"файл базы недоступен для записи: {target} "
            "(атрибут «Только чтение» или запрет доступа)"
        )

    folder_problem = directory_problem(target.parent)
    if folder_problem:
        problems.append(folder_problem)

    problem = ""
    if problems:
        problem = readonly_message(target, "; ".join(problems))

    return WriteAccessReport(path=str(path), repaired=repaired, problem=problem)


def record_startup_problem(message: str) -> None:
    """Запоминает причину, о которой нужно сказать оператору после старта Qt."""
    global _startup_problem
    _startup_problem = str(message or "")


def take_startup_problem() -> str:
    """
    Отдаёт запомненную причину ОДИН раз (окно показывается на запуск одно).

    :return: текст для окна или пустая строка, если проблем нет.
    """
    global _startup_problem
    message = _startup_problem
    _startup_problem = ""
    return message


def startup_problem() -> str:
    """Причина старта без изъятия (журнал, диагностика, тесты)."""
    return _startup_problem


def reset_startup_problem() -> None:
    """Сбрасывает запомненную причину (изоляция тестов)."""
    global _startup_problem
    _startup_problem = ""


__all__ = [
    "DEFAULT_DB_HINT",
    "PROBE_TABLE",
    "READONLY_MARKERS",
    "SIDECARS",
    "WriteAccessReport",
    "classify_error",
    "clear_readonly",
    "clear_readonly_files",
    "directory_problem",
    "explain_error",
    "is_writable",
    "local_file",
    "locked_message",
    "prepare_database",
    "probe_write",
    "readonly_message",
    "record_startup_problem",
    "related_files",
    "reset_startup_problem",
    "save_error_text",
    "startup_problem",
    "take_startup_problem",
]
