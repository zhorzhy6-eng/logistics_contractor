#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Централизованная настройка логирования (Часть 2 задания).

Четыре независимых файла:

  logs/app.log     — INFO: запуск, действия интерфейса, этапы операций,
                     результаты, предупреждения и ошибки. Пишется всегда.
  logs/debug.log   — DEBUG: всё подряд (GigaChat, БД, распознавание, UI).
                     Пишется ТОЛЬКО при запуске с флагом --debug,
                     чтобы обычная работа не замедлялась.
  logs/errors.log  — ERROR и выше, с traceback и стеком вызова.
                     Пишется всегда: по нему быстро ищут, что сломалось.
  logs/audit.log   — только действия (кто/что/когда), без персональных данных.

Формат строк:
    2026-09-24 22:15:33 | DEBUG | core.gigachat_client | recognize_text | Отправка запроса, длина промпта: 1523 символов

Ротация: RotatingFileHandler, 5 МБ × 5 файлов (errors/audit — 2 МБ × 3).

Что в логи не попадает никогда:
  * ключ GigaChat и любые секреты (см. core/secrets_store.py);
  * значения персональных данных — только имена полей, длины и ключи
    (см. core/trace.py, core/audit.py);
  * содержимое договоров.
"""

import logging
import logging.config
import logging.handlers
import os
import sys
import traceback

APP_LOG_FILENAME = "app.log"
DEBUG_LOG_FILENAME = "debug.log"
ERRORS_LOG_FILENAME = "errors.log"
AUDIT_LOG_FILENAME = "audit.log"

# ── Форматы ──
MAIN_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(funcName)s | %(message)s"
ERRORS_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)-30s | "
    "%(funcName)s:%(lineno)d | %(message)s"
)
AUDIT_FORMAT = "%(asctime)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ── Ротация ──
MAIN_MAX_BYTES = 5 * 1024 * 1024
MAIN_BACKUPS = 5
SMALL_MAX_BYTES = 2 * 1024 * 1024
SMALL_BACKUPS = 3

# ── Логгеры приложения (им, кроме прочего, добавляется debug.log) ──
APP_LOGGERS = ("core", "ui", "db", "config", "main")


class StackInfoFilter(logging.Filter):
    """
    Добавляет стек вызова к записям ERROR, у которых нет исключения.

    Нужно для logs/errors.log: по стеку сразу видно, откуда пришла ошибка,
    даже если её залогировали без exc_info.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 (API logging)
        if record.levelno >= logging.ERROR and not record.exc_info and not record.stack_info:
            record.stack_info = "".join(traceback.format_stack(limit=12)[:-1])
        return True


class SafeConsoleHandler(logging.StreamHandler):
    """
    Консольный хендлер, устойчивый к кодировке Windows.

    Русская консоль работает в cp1251, поэтому символы вроде «×», «✓» или «→»
    роняли вывод с UnicodeEncodeError («--- Logging error ---» в stderr).
    Здесь сообщение кодируется в кодировку потока с заменой непередаваемых
    символов на «?». Запись в файлы (там всегда utf-8) не меняется.

    emit() написан целиком: стандартный StreamHandler.emit сам гасит
    UnicodeEncodeError через handleError, поэтому «обернуть» его нельзя.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            stream = self.stream
            encoding = getattr(stream, "encoding", None)
            if encoding:
                message = message.encode(encoding, "replace").decode(encoding, "replace")
            stream.write(message + self.terminator)
            self.flush()
        except Exception:  # noqa: BLE001 — как и в logging, не роняем приложение
            self.handleError(record)


def setup_logging(log_dir: str = None, log_level: str = "INFO", debug: bool = False) -> None:
    """
    Настраивает логирование всего приложения.

    :param log_dir: папка для логов. По умолчанию <корень_проекта>/logs
    :param log_level: уровень файла app.log (по умолчанию INFO)
    :param debug: включает logs/debug.log с уровнем DEBUG (флаг --debug)
    """
    # ── Папка логов ──
    if log_dir is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_dir = os.path.join(project_root, "logs")

    os.makedirs(log_dir, exist_ok=True)

    # Права: логи могут содержать рабочие данные — только текущий пользователь
    try:
        from core.security import restrict_to_current_user
        restrict_to_current_user(log_dir, recursive=True)
    except Exception as e:  # noqa: BLE001 — логирование не должно падать
        print(f"Не удалось ограничить права на папку логов: {e}", file=sys.stderr)

    app_log = os.path.join(log_dir, APP_LOG_FILENAME)
    debug_log = os.path.join(log_dir, DEBUG_LOG_FILENAME)
    errors_log = os.path.join(log_dir, ERRORS_LOG_FILENAME)
    audit_log = os.path.join(log_dir, AUDIT_LOG_FILENAME)

    app_level = str(log_level).upper()

    handlers = {
        # ── Основной лог: только важное ──
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": app_level,
            "formatter": "main",
            "filename": app_log,
            "maxBytes": MAIN_MAX_BYTES,
            "backupCount": MAIN_BACKUPS,
            "encoding": "utf-8",
        },
        # ── Консоль ──
        "console": {
            "class": "config.logging_config.SafeConsoleHandler",
            "level": app_level,
            "formatter": "main",
            "stream": sys.stdout,
        },
        # ── Ошибки: всегда, с traceback и стеком ──
        "errors_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "ERROR",
            "formatter": "errors",
            "filename": errors_log,
            "maxBytes": SMALL_MAX_BYTES,
            "backupCount": SMALL_BACKUPS,
            "encoding": "utf-8",
            "filters": ["stack_info"],
        },
        # ── Аудит действий ──
        "audit_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "INFO",
            "formatter": "audit",
            "filename": audit_log,
            "maxBytes": SMALL_MAX_BYTES,
            "backupCount": SMALL_BACKUPS,
            "encoding": "utf-8",
        },
    }

    app_handlers = ["file", "console", "errors_file"]

    # ── Отладочный файл: только с --debug ──
    if debug:
        handlers["debug_file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "main",
            "filename": debug_log,
            "maxBytes": MAIN_MAX_BYTES,
            "backupCount": MAIN_BACKUPS,
            "encoding": "utf-8",
        }
        app_handlers.append("debug_file")

    loggers = {
        # Наши модули: уровень DEBUG, но app.log отфильтрует всё ниже INFO
        name: {"level": "DEBUG", "handlers": list(app_handlers), "propagate": False}
        for name in APP_LOGGERS
    }
    # Аудит: отдельный файл, в app.log не дублируется
    loggers["audit"] = {"level": "INFO", "handlers": ["audit_file"], "propagate": False}
    # Сторонние библиотеки: без спама
    for noisy in ("requests", "urllib3", "PIL", "docx", "matplotlib", "keyring", "asyncio"):
        loggers[noisy] = {"level": "WARNING"}

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "main": {"format": MAIN_FORMAT, "datefmt": DATE_FORMAT},
            "errors": {"format": ERRORS_FORMAT, "datefmt": DATE_FORMAT},
            "audit": {"format": AUDIT_FORMAT, "datefmt": DATE_FORMAT},
        },
        "filters": {
            "stack_info": {"()": StackInfoFilter},
        },
        "handlers": handlers,
        "loggers": loggers,
        "root": {
            "level": "DEBUG",
            "handlers": ["file", "console", "errors_file"],
        },
    }

    logging.config.dictConfig(config)

    # ── Шапка в логе ──
    logger = logging.getLogger("config.logging_config")
    logger.info("=" * 90)
    logger.info(f"Логирование инициализировано: файл={app_log}, уровень={app_level}")
    logger.info(
        f"Ротация: app/debug — {MAIN_MAX_BYTES // (1024 * 1024)} МБ x {MAIN_BACKUPS}, "
        f"errors/audit — {SMALL_MAX_BYTES // (1024 * 1024)} МБ x {SMALL_BACKUPS}"
    )
    logger.info(f"Журнал ошибок: {errors_log} (с traceback и стеком вызова)")
    logger.info(f"Аудит действий: {audit_log} (персональные данные не записываются)")
    if debug:
        logger.info(f"Отладочный лог включён (--debug): {debug_log}")
    else:
        logger.info("Отладочный лог выключен; для подробностей: python main.py --debug")
    logger.info("=" * 90)

    logging.getLogger("audit").info("audit_log_started")
