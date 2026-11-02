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

    Проверка та же, что у хранилища и клиента GigaChat
    (``secrets_store.gigachat_key_problem``): ключ — это Base64 от пары
    client_id:client_secret, и ничего другого в заголовок Authorization
    положить нельзя. Значение ключа в текст предупреждения не попадает —
    только причина.

    :return: текст причины или None, если ключ выглядит нормально
    """
    return secrets_store.gigachat_key_problem(key)


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
        problem = secrets_store.gigachat_key_problem(key)
        if problem is not None:
            # Ключ есть, но им нельзя авторизоваться: молчать об этом нельзя,
            # иначе «OK» в консоли и ошибка 400 в программе выглядят
            # несвязанными событиями.
            print("ОШИБКА: " + secrets_store.gigachat_key_error_text(key))
            print(f"    длина значения: {len(key)} символов (значение не показывается)")
            return 1
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
    print("   проект GigaChat API → «Настройки API» → «Получить ключ».")
    print(" Ключ — это «Authorization key», строка Base64 (латиница, цифры,")
    print("   «+», «/», «=»). Ключи других сервисов (например, «sk-…») не подходят.")
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

    key = secrets_store.normalize_gigachat_key(key)
    problem = _validate_key(key)
    if problem:
        # Формат — не «вопрос вкуса»: сервер декодирует ключ из Base64 и
        # другой ключ принять не может. Поэтому не «всё равно сохранить?»,
        # а отказ: иначе в хранилище появится значение, с которым программа
        # падает на авторизации.
        print("ОШИБКА: " + secrets_store.INVALID_GIGACHAT_KEY_MESSAGE)
        print(f"Причина: {problem}.")
        print("Ключ не сохранён.")
        return 1

    try:
        saved_ok = secrets_store.set_gigachat_key(key)
    except ValueError as error:
        print(f"ОШИБКА: {error}")
        print("Ключ не сохранён.")
        return 1

    if not saved_ok:
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
