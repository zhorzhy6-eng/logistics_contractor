#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Защита файлов приложения (Шаг 5 задания по безопасности).

Что делает модуль:
  1. restrict_to_current_user() — ограничивает доступ к файлу/папке текущим
     пользователем Windows (icacls: снять наследование, выдать полный доступ
     только владельцу). На других ОС используется chmod 600/700.
  2. backup_database() — делает резервную копию SQLite через VACUUM INTO
     (безопасно для работающей базы: копия консистентна) и хранит последние N.

Зачем: contracts.db содержит персональные данные водителей, а logs/ — следы
работы. По умолчанию Windows-профиль может давать доступ другим пользователям
машины, поэтому доступ сужается явно.
"""

import glob
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger("core.security")

BACKUP_DIRNAME = "backup"
DEFAULT_KEEP_BACKUPS = 10
ICACLS_TIMEOUT = 30


def is_windows() -> bool:
    return os.name == "nt"


def current_user() -> str:
    """Имя текущего пользователя (с доменом, если он есть)."""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    if domain and user:
        return f"{domain}\\{user}"
    return user


def restrict_to_current_user(path: str, recursive: bool = False) -> bool:
    """
    Ограничивает доступ к файлу или папке текущим пользователем.

    Windows: icacls — снимаем наследование и выдаём полный доступ только
    владельцу (остальные записи ACL удаляются ключом /inheritance:r и
    последующим /grant:r).

    :param path: файл или папка
    :param recursive: обработать папку со всем содержимым
    :return: True, если ограничения применены
    """
    if not path or not os.path.exists(path):
        logger.debug(f"Ограничение прав: путь не найден ({path!r})")
        return False

    if is_windows():
        return _restrict_windows(path, recursive)
    return _restrict_posix(path, recursive)


def _run_quiet(command: List[str]) -> int:
    """
    Запускает команду и возвращает код возврата.

    Сначала пробуем перехватить вывод (полезно для диагностики), но в
    ограниченных средах создание каналов может быть запрещено — тогда
    команда выполняется без перехвата. Это не влияет на результат,
    icacls важен нам кодом возврата.
    """
    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            timeout=ICACLS_TIMEOUT, check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()[:200]
            if message:
                logger.debug(f"{command[0]}: {message}")
        return result.returncode
    except (OSError, PermissionError) as e:
        logger.debug(f"Перехват вывода недоступен ({type(e).__name__}), запускаем без него")
        result = subprocess.run(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=ICACLS_TIMEOUT, check=False,
        )
        return result.returncode


def _restrict_windows(path: str, recursive: bool) -> bool:
    user = current_user()
    if not user:
        logger.warning("Не удалось определить текущего пользователя — права не изменены")
        return False

    commands = [
        ["icacls", path, "/inheritance:r"],
        ["icacls", path, "/grant:r", f"{user}:(F)"],
    ]
    if recursive and os.path.isdir(path):
        commands.append(["icacls", path, "/T", "/grant:r", f"{user}:(F)"])

    ok = True
    for command in commands:
        try:
            code = _run_quiet(command)
            if code != 0:
                ok = False
                logger.warning(
                    f"icacls вернул код {code} для {path} — права применены не полностью"
                )
        except FileNotFoundError:
            logger.warning("Утилита icacls не найдена — права не изменены")
            return False
        except subprocess.TimeoutExpired:
            logger.warning(f"icacls не успел обработать {path} за {ICACLS_TIMEOUT} сек")
            return False
        except Exception as e:
            logger.warning(f"Не удалось изменить права на {path}: {type(e).__name__}: {e}")
            return False

    if ok:
        logger.info(f"Права ограничены текущим пользователем ({user}): {path}")
    return ok


def _restrict_posix(path: str, recursive: bool) -> bool:
    try:
        mode = 0o700 if os.path.isdir(path) else 0o600
        os.chmod(path, mode)
        if recursive and os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                for name in dirs:
                    os.chmod(os.path.join(root, name), 0o700)
                for name in files:
                    os.chmod(os.path.join(root, name), 0o600)
        logger.info(f"Права ограничены ({oct(mode)}): {path}")
        return True
    except OSError as e:
        logger.warning(f"Не удалось изменить права на {path}: {e}")
        return False


def backup_dir(db_path: str) -> str:
    """Папка backup/ рядом с базой данных."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), BACKUP_DIRNAME)


def _prune_backups(directory: str, pattern: str, keep: int) -> None:
    """Оставляет только `keep` последних копий."""
    if keep <= 0:
        return
    files = sorted(glob.glob(os.path.join(directory, pattern)))
    for old in files[:-keep]:
        try:
            os.remove(old)
            logger.debug(f"Старая резервная копия удалена: {old}")
        except OSError as e:
            logger.warning(f"Не удалось удалить старую копию {old}: {e}")


def backup_database(
    db_path: str,
    keep: int = DEFAULT_KEEP_BACKUPS,
    reason: str = "manual",
) -> Optional[str]:
    """
    Делает резервную копию базы через VACUUM INTO.

    VACUUM INTO создаёт консистентный файл даже при включённом WAL и не
    требует остановки приложения.

    :return: путь к копии или None, если копию сделать не удалось
    """
    if not db_path or not os.path.exists(db_path):
        logger.debug(f"Резервное копирование пропущено: нет файла {db_path!r}")
        return None

    directory = backup_dir(db_path)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as e:
        logger.warning(f"Не удалось создать папку backup: {e}")
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.splitext(os.path.basename(db_path))[0]
    target = os.path.join(directory, f"{base}_{stamp}.db")

    if os.path.exists(target):
        logger.debug(f"Копия с такой меткой уже есть: {target}")
        return target

    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("VACUUM INTO ?", (target,))
        logger.info(f"Резервная копия создана ({reason}): {target}")
        _prune_backups(directory, f"{base}_*.db", keep)
        return target
    except sqlite3.Error as e:
        logger.warning(f"Не удалось создать резервную копию: {type(e).__name__}: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


def backup_size_kb(path: Optional[str]) -> int:
    """Размер файла копии в КБ (для аудита)."""
    if not path or not os.path.exists(path):
        return 0
    return int(os.path.getsize(path) / 1024)
