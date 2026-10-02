#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сохранение секретного ключа DaData в системное хранилище.

Запуск (достаточно один раз):

    python set_dadata_secret.py

Секретный ключ (SECRET_KEY) нужен для «чистящих» методов DaData
(clean/passport и т.п.) и передаётся в заголовке X-Secret.
Он НЕ записывается в файлы проекта: попадает в системное хранилище учётных
данных (Windows Credential Manager) через библиотеку keyring —
service = "logistics_contractor", username = "dadata_secret_key".
В проекте остаётся только код — см. core/secrets_store.py.

Дополнительные режимы:

    python set_dadata_secret.py --check    проверить, сохранён ли ключ
    python set_dadata_secret.py --delete   удалить сохранённый ключ

Это отдельный ключ: обычный API-ключ DaData сохраняется скриптом
set_dadata_key.py и здесь не затрагивается.

Логирование (config.logging_config) здесь намеренно НЕ подключается:
скрипт работает только с консолью и никогда не пишет ключ в logs/.
Ни в одном режиме значение ключа не выводится на экран.
"""

import argparse
import getpass
import logging
import sys
from typing import List, Optional

from core import secrets_store

YES_ANSWERS = ("y", "yes", "д", "да")

#: Минимальная разумная длина секретного ключа (у DaData ~40 символов).
MIN_KEY_LENGTH = 20

#: Хранилища, которые означают «keyring установлен, но не работает».
BROKEN_BACKENDS = ("недоступно", "fail.Keyring", "fail.KeyringBackend")


def _silence_module_logging() -> None:
    """
    Не дублировать сообщения в stderr.

    core.secrets_store пишет предупреждения через logging, а без настроенных
    обработчиков Python выводит их в stderr (lastResort). В консольном скрипте
    всё нужное и так печатается в stdout, а logs/ здесь не подключается.
    """
    logging.getLogger("core.secrets_store").addHandler(logging.NullHandler())


def _make_output_safe() -> None:
    """
    Не падать, если консоль/файл не поддерживает какой-то символ.

    При запуске из cmd.exe вывод идёт в кодовую страницу (cp866/cp1251),
    и один «неудобный» символ раньше приводил к UnicodeEncodeError вместо
    понятного сообщения. Теперь такой символ заменяется на «?».
    """
    stream = getattr(sys, "stdout", None)
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(errors="replace")
    except (ValueError, OSError):  # поток уже перенаправлен/закрыт
        pass


def _keyring_ready() -> bool:
    """Доступно ли системное хранилище секретов."""
    if not secrets_store.keyring_available():
        return False
    return secrets_store.backend_name() not in BROKEN_BACKENDS


def _validate_key(key: str) -> Optional[str]:
    """
    Проверяет ключ на очевидные проблемы.

    :return: текст предупреждения или None, если ключ выглядит нормально
    """
    if len(key) < MIN_KEY_LENGTH:
        return (
            f"секретный ключ выглядит слишком коротким "
            f"(меньше {MIN_KEY_LENGTH} символов). Проверьте, что скопирована "
            f"вся строка из личного кабинета DaData"
        )

    try:
        key.encode("ascii")
    except UnicodeEncodeError:
        return (
            "секретный ключ содержит не-ASCII символы: проверьте, что "
            "скопирована вся строка из личного кабинета DaData"
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
    if not _keyring_ready():
        print(secrets_store.KEYRING_UNAVAILABLE_MESSAGE)
        return 1

    if secrets_store.has_dadata_secret():
        key = secrets_store.get_dadata_secret() or ""
        print(f"OK: {secrets_store.describe_dadata_secret_state()}")
        print(f"    длина ключа: {len(key)} символов (значение не показывается)")
        return 0

    print(secrets_store.MISSING_DADATA_SECRET_MESSAGE)
    return 1


def _delete_mode() -> int:
    if secrets_store.delete_dadata_secret():
        print("Секретный ключ DaData удалён из системного хранилища.")
        return 0
    print("Секретный ключ DaData не найден или удалить его не удалось.")
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Сохранение секретного ключа DaData в системное хранилище"
    )
    parser.add_argument("--check", action="store_true",
                        help="проверить, сохранён ли ключ")
    parser.add_argument("--delete", action="store_true",
                        help="удалить сохранённый ключ")
    args = parser.parse_args(argv)

    _silence_module_logging()
    _make_output_safe()

    if args.check:
        return _check_mode()
    if args.delete:
        return _delete_mode()

    print("=" * 66)
    print(" Сохранение секретного ключа DaData в системное хранилище")
    print("=" * 66)
    print(f" Хранилище: {secrets_store.backend_name()}")
    print(" Где взять ключ: личный кабинет DaData, раздел «API-ключи», "
          "колонка «Секретный ключ».")
    print(" Секретный ключ нужен для «чистящих» методов (clean/passport).")
    print(" Он не сохраняется в файлы проекта и не выводится на экран.")
    print("=" * 66)

    if not _keyring_ready():
        print(secrets_store.KEYRING_UNAVAILABLE_MESSAGE)
        return 1

    if secrets_store.has_dadata_secret():
        answer = _ask("Секретный ключ уже сохранён. Заменить его? [y/N]: ")
        if answer not in YES_ANSWERS:
            print("Отменено — прежний ключ не изменён.")
            return 0

    try:
        key = getpass.getpass(
            "Вставьте секретный ключ (ввод не отображается): "
        ).strip()
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

    if not secrets_store.set_dadata_secret(key):
        print(secrets_store.KEYRING_UNAVAILABLE_MESSAGE)
        return 1

    saved = secrets_store.get_dadata_secret()
    if saved == key:
        print(f"Готово: секретный ключ DaData сохранён ({len(key)} символов) "
              f"и прочитан обратно.")
        print("Файлы проекта ключ не содержат — он понадобится для "
              "cleaner-методов DaData.")
        return 0

    print("Ключ сохранён, но прочитать его обратно не удалось. "
          "Проверьте системное хранилище.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
