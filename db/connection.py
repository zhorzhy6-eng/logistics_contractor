#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Соединение с локальной базой SQLite.

Модуль отвечает ровно за одно: открыть файл базы с нужными PRAGMA.
Схема — в db/schema.py, миграции — в db/migrations.py, запросы — в db/crud/.

Путь к базе
-----------
Владелец пути — фасад `db/database.py` (константа `DB_PATH`): её подменяют
тесты и рабочие инструменты на уровне модуля (`db.database.DB_PATH = …`).
Чтобы подмена продолжала действовать, фасад регистрирует здесь источник
пути (`set_db_path_provider`), а соединение каждый раз спрашивает путь у него
(`current_db_path`). Поэтому модули db/crud/* НЕ читают DB_PATH напрямую.

Права на запись (дефект «attempt to write a readonly database»)
--------------------------------------------------------------
SQLite открывает файл с атрибутом «только чтение» как read-only и падает
только на первой записи — то есть в момент сохранения, когда оператор уже
заполнил форму. Поэтому перед КАЖДЫМ открытием соединения проверяются права
(`db/write_access.py`):

  * атрибут «только чтение» с базы и её спутников `-wal`/`-shm` снимается
    (`os.chmod(..., S_IWRITE)`) — до `sqlite3.connect`, иначе поздно;
  * один раз на путь проверяется, что в папке данных можно создавать файлы
    (иначе SQLite не создаст журнал), и делается ПРОБА ЗАПИСИ: настоящая
    транзакция `CREATE TABLE` + `ROLLBACK` (база не меняется);
  * если запись всё равно невозможна, причина уходит в журнал уровнем ERROR
    и запоминается для окна: `main.py` показывает её оператору после
    создания `QApplication` (у собранного .exe консоли нет).

Соединение при этом не рвётся: читать базу можно, а сохранение покажет
понятное объяснение (см. `write_access.save_error_text`).
"""

import logging
import sqlite3
from pathlib import Path
from typing import Set

# Логирование SQL-запросов (Часть 2 задания): фабрика соединения пишет
# в logs/debug.log запрос, число строк и время, а медленные запросы (>1 с)
# помечает SLOW в logs/app.log.
from core import data_paths
from db import write_access
from db.query_logging import LoggingConnection

logger = logging.getLogger("db.connection")

#: Путь к файлу базы по умолчанию — в ПАПКЕ ДАННЫХ пользователя.
#:
#: Не рядом с программой: папку установки замена при обновлении, и база
#: пропадала. В .exe путь ведёт в %APPDATA%\LogisticsContractor (см.
#: core/data_paths.py), в разработке — в tests/_tmp/_appdata.
DB_PATH = str(data_paths.db_path())

#: Источник пути к базе: функция без аргументов, возвращающая путь.
#: Ставится фасадом db.database (см. модуль-документацию).
_db_path_provider = None

#: Пути, для которых уже проверены папка данных и атрибуты файлов.
_prepared_paths: Set[str] = set()

#: Пути, для которых уже сделана проба записи (на процесс — один раз).
_probed_paths: Set[str] = set()


def set_db_path_provider(provider) -> None:
    """
    Регистрирует источник пути к базе (вызывает фасад db.database).

    :param provider: функция без аргументов, возвращающая путь к файлу базы.
    """
    global _db_path_provider
    _db_path_provider = provider


def current_db_path() -> str:
    """
    Путь к файлу базы, с которым работает приложение сейчас.

    Если фасад зарегистрировал источник, берётся путь из него — так
    действует подмена `db.database.DB_PATH` в тестах. Если источника нет
    (модуль соединения импортирован в одиночку), возвращается DB_PATH.
    """
    provider = _db_path_provider
    if provider is not None:
        try:
            path = provider()
        except Exception as e:  # noqa: BLE001 — путь не должен ронять соединение
            logger.warning(f"Не удалось получить путь к базе: {e}")
            path = None
        if path:
            return str(path)
    return DB_PATH


def _check_write_access(path: str) -> None:
    """
    Снимает «только чтение» и (один раз на путь) проверяет права на запись.

    Полная проверка — при первом соединении с путём (это и есть старт
    программы: `init_database()` зовёт `get_connection()` первым). На
    следующих соединениях — только дешёвая починка атрибута: антивирус
    или переустановка могут пометить файл «только чтение» и посреди сеанса.
    """
    if path in _prepared_paths:
        _repair_readonly(path)
        return

    _prepared_paths.add(path)
    try:
        report = write_access.prepare_database(path)
    except Exception as e:  # noqa: BLE001 — проверка прав не важнее соединения
        logger.warning(f"Не удалось проверить права на базу {path}: {e}")
        return

    if report.repaired:
        logger.warning(
            "Снят атрибут «только чтение» (запись восстановлена): %s",
            ", ".join(report.repaired),
        )
    if report.problem:
        logger.error(report.problem)
        write_access.record_startup_problem(report.problem)


def _repair_readonly(path: str) -> None:
    """Снимает атрибут «только чтение», если он появился во время работы."""
    try:
        repaired = write_access.clear_readonly_files(path)
    except Exception as e:  # noqa: BLE001 — починка не должна ронять соединение
        logger.warning(f"Не удалось проверить атрибуты базы {path}: {e}")
        return
    if repaired:
        logger.warning(
            "Снят атрибут «только чтение» (запись восстановлена): %s",
            ", ".join(repaired),
        )


def _probe_write(conn: sqlite3.Connection, path: str) -> None:
    """
    Проба настоящей записи на первом соединении с базой (один раз на путь).

    Проба идёт транзакцией с откатом, поэтому база не меняется. Отказ не
    рвёт соединение: читать можно, но причина попадает в журнал и в окно
    после старта — до того, как оператор начнёт сохранять данные.
    """
    if path in _probed_paths:
        return
    _probed_paths.add(path)

    try:
        write_access.probe_write(conn)
        return
    except sqlite3.DatabaseError as error:
        kind = write_access.classify_error(error)
        text = str(error)

    if kind == "readonly":
        message = write_access.readonly_message(
            path, f"проверка записи при старте: {text}"
        )
        logger.error(message)
        write_access.record_startup_problem(message)
    elif kind == "locked":
        logger.warning(write_access.locked_message(path))
    else:
        logger.warning(f"Проверка записи в базу не прошла: {text}")


def _open_connection(path: str, foreign_keys: bool) -> sqlite3.Connection:
    """
    Открывает файл базы и настраивает PRAGMA.

    Шаг 1 оптимизации производительности:
      * journal_mode=WAL  — читатели не блокируют писателя и наоборот
        (режим сохраняется в файле БД, поэтому включается один раз);
      * busy_timeout=5000 — при блокировке ждать до 5 секунд вместо
        мгновенной ошибки «database is locked»;
      * foreign_keys=ON   — контроль ссылочной целостности (как было).
    """
    conn = sqlite3.connect(path, timeout=30, factory=LoggingConnection)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        if foreign_keys:
            conn.execute("PRAGMA foreign_keys = ON")

        # WAL может быть недоступен (например, БД на сетевом диске или
        # файл только для чтения) — тогда работаем в прежнем режиме.
        try:
            mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()
            logger.debug(f"journal_mode: {mode[0] if mode else 'неизвестно'}")
        except sqlite3.DatabaseError as e:
            logger.warning(f"Не удалось включить WAL, работаем в обычном режиме: {e}")
    except Exception:
        # Настройка не удалась — соединение всё равно закрываем, иначе
        # файл базы останется занятым до сборки мусора.
        conn.close()
        raise
    return conn


def get_connection(foreign_keys: bool = True) -> sqlite3.Connection:
    """
    Возвращает подключение к базе данных с настроенными PRAGMA.

    Перед открытием проверяются и починяются права на запись, после
    открытия (один раз на процесс) делается проба записи — см.
    документацию модуля и `db/write_access.py`.
    """
    path = current_db_path()

    # Папку для файла создаём ДО открытия: sqlite3.connect создаёт файл, но
    # не каталог. В свежей установке папки данных ещё нет (%APPDATA%\
    # LogisticsContractor), и первый запуск падал бы с «unable to open
    # database file». Каталог берётся из самого пути, поэтому подмена
    # DB_PATH в тестах тоже работает.
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    # Атрибут «только чтение» снимается ДО sqlite3.connect: SQLite открывает
    # такой файл read-only, и починить это на открытом соединении нельзя.
    _check_write_access(path)

    conn = _open_connection(path, foreign_keys)
    _probe_write(conn, path)
    return conn


__all__ = [
    "DB_PATH",
    "current_db_path",
    "get_connection",
    "set_db_path_provider",
]
