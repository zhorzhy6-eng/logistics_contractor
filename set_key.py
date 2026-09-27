#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сохранение ключа GigaChat в системное хранилище (Шаг 2 задания по безопасности).

Запуск (достаточно один раз):

    python set_key.py

Ключ НЕ записывается в файлы проекта: он попадает в системное хранилище
учётных данных (Windows Credential Manager) через библиотеку keyring.
В проекте остаётся только код — см. core/secrets_store.py.

Дополнительные режимы:

    python set_key.py --check    проверить, сохранён ли ключ
    python set_key.py --delete   удалить сохранённый ключ
"""

import argparse
import getpass
import sys
from typing import List, Optional

from config.logging_config import setup_logging
from core import secrets_store

YES_ANSWERS = ("y", "yes", "д", "да")


def _validate_key(key: str) -> Optional[str]:
    """
    Проверяет ключ на очевидные проблемы.

    :return: текст предупреждения или None, если ключ выглядит нормально
    """
    if len(key) < 20:
        return "ключ выглядит слишком коротким (обычно это длинная строка Base64)"

    try:
        key.encode("ascii")
    except UnicodeEncodeError:
        return (
            "ключ содержит не-ASCII символы. Authorization key — это Base64 "
            "(латиница, цифры, +, /, =): проверьте, что скопирована вся строка"
        )

    return None


def _ask(prompt: str) -> str:
    """Читает ответ пользователя (устойчиво к отсутствию интерактивного ввода)."""
    try:
        return input(prompt).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return ""


def _check_mode() -> int:
    if secrets_store.has_gigachat_key():
        key = secrets_store.get_gigachat_key() or ""
        print(f"OK: {secrets_store.describe_key_state()}")
        print(f"    длина ключа: {len(key)} символов (значение не показывается)")
        return 0

    print(secrets_store.MISSING_KEY_MESSAGE)
    return 1


def _delete_mode() -> int:
    if secrets_store.delete_gigachat_key():
        print("Ключ удалён из системного хранилища.")
        return 0
    print("Ключ не найден или удалить его не удалось.")
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Сохранение ключа GigaChat в системное хранилище"
    )
    parser.add_argument("--check", action="store_true",
                        help="проверить, сохранён ли ключ")
    parser.add_argument("--delete", action="store_true",
                        help="удалить сохранённый ключ")
    args = parser.parse_args(argv)

    setup_logging()

    if args.check:
        return _check_mode()
    if args.delete:
        return _delete_mode()

    print("=" * 66)
    print(" Сохранение ключа GigaChat в системное хранилище")
    print("=" * 66)
    print(f" Хранилище: {secrets_store.backend_name()}")
    print(" Где взять ключ: личный кабинет Sber Developer Studio →")
    print("   «Authorization key» (строка Base64, обычно вида MD...==).")
    print(" Ключ не сохраняется в файлы проекта и не выводится на экран.")
    print("=" * 66)

    if secrets_store.has_gigachat_key():
        answer = _ask("Ключ уже сохранён. Заменить его? [y/N]: ")
        if answer not in YES_ANSWERS:
            print("Отменено — прежний ключ не изменён.")
            return 0

    try:
        key = getpass.getpass("Вставьте ключ (ввод не отображается): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nОтменено — ключ не сохранён.")
        return 1

    if not key:
        print("Пустой ввод — ключ не сохранён.")
        return 1

    warning = _validate_key(key)
    if warning:
        print(f"ВНИМАНИЕ: {warning}")
        if _ask("Всё равно сохранить? [y/N]: ") not in YES_ANSWERS:
            print("Отменено — ключ не сохранён.")
            return 1

    if not secrets_store.set_gigachat_key(key):
        print("Не удалось сохранить ключ.")
        print("Проверьте, что установлена библиотека keyring: pip install keyring")
        return 1

    saved = secrets_store.get_gigachat_key()
    if saved == key:
        print(f"Готово: ключ сохранён ({len(key)} символов) и прочитан обратно.")
        print("Файлы проекта ключ не содержат — можно запускать main.py")
        return 0

    print("Ключ сохранён, но прочитать его обратно не удалось. "
          "Проверьте системное хранилище.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
