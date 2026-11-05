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
import tempfile
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
# «qt» — сообщения самого Qt (qt.messages, см. core/qt_crash_handler.py):
# критические сообщения Qt идут перед qFatal(), и без них падение
# на уровне C++ не оставляет следов в журнале.
APP_LOGGERS = ("core", "ui", "db", "config", "main", "qt")

#: Резервная папка журналов: %TEMP%\LogisticsContractor_fallback_logs.
#: Нужна, когда штатная папка логов или сам файл журнала недоступны
#: (занят второй копией программы, антивирусом, правами).
FALLBACK_LOG_DIR = os.path.join(
    tempfile.gettempdir(), "LogisticsContractor_fallback_logs"
)


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


def _safe_stderr(message: str) -> None:
    """Печать в stderr, не роняя запуск (у pythonw stderr может быть None)."""
    try:
        print(message, file=sys.stderr)
    except Exception:  # noqa: BLE001 — диагностика не имеет права ронять старт
        pass


def _fallback_log_path(filename: str) -> str:
    """
    Резервный путь журнала в %TEMP%: <pid>_<имя файла>.

    PID в имени разделяет журналы двух одновременно запущенных копий: обе
    не мешают друг другу и обе что-то записывают.
    """
    name = os.path.basename(str(filename or "log.log")) or "log.log"
    return os.path.join(FALLBACK_LOG_DIR, f"{os.getpid()}_{name}")


class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """
    RotatingFileHandler, который не роняет запуск, если файл журнала занят.

    Штатный RotatingFileHandler открывает файл в конструкторе. PermissionError
    от антивируса или второй копии программы обрывал запуск внутри dictConfig
    («Unable to configure handler 'audit_file'»). Здесь:

      * при ошибке открытия файл переключается на резервный путь
        `%TEMP%\\LogisticsContractor_fallback_logs\\<pid>_<имя>`;
      * если недоступен и резервный — журнал пишется в NUL (или отключается),
        но приложение стартует;
      * emit() ловит ошибки записи и ротации: занятый файл не роняет
        приложение уже во время работы.
    """

    def __init__(self, filename, *args, **kwargs):
        self.original_filename = str(filename)
        self.fallback_used = False
        self.fallback_path = ""

        try:
            super().__init__(filename, *args, **kwargs)
            return
        except (PermissionError, OSError) as exc:
            _safe_stderr(
                f"Логирование: файл {filename} недоступен ({exc}); "
                "переключаюсь на резервный"
            )

        fallback = _fallback_log_path(filename)
        try:
            os.makedirs(os.path.dirname(fallback), exist_ok=True)
            super().__init__(fallback, *args, **kwargs)
            self.fallback_used = True
            self.fallback_path = fallback
            _safe_stderr(f"Логирование: резервный журнал {fallback}")
            return
        except (PermissionError, OSError) as exc:
            _safe_stderr(
                f"Логирование: резервный файл {fallback} тоже недоступен "
                f"({exc}); журнал отключён"
            )

        # Последний шанс: NUL и отложенное открытие. dictConfig не упадёт,
        # а ошибки emit() ловит переопределённый emit().
        super().__init__(
            os.devnull,
            maxBytes=0,
            backupCount=0,
            delay=True,
            encoding=kwargs.get("encoding", "utf-8"),
        )
        self.fallback_used = True
        self.fallback_path = os.devnull

    def emit(self, record: logging.LogRecord) -> None:
        """Запись в журнал не имеет права ронять приложение."""
        try:
            super().emit(record)
        except Exception:  # noqa: BLE001 — журнал не должен ронять программу
            self.handleError(record)


def _prepare_log_dir(log_dir: str) -> str:
    """Папка журналов: штатная, резервная в %TEMP% или системная временная."""
    try:
        os.makedirs(log_dir, exist_ok=True)
        return log_dir
    except OSError as exc:
        _safe_stderr(
            f"Логирование: папка {log_dir} недоступна ({exc}); "
            f"использую {FALLBACK_LOG_DIR}"
        )

    try:
        os.makedirs(FALLBACK_LOG_DIR, exist_ok=True)
        return FALLBACK_LOG_DIR
    except OSError:
        return tempfile.gettempdir()


def _reset_handlers() -> None:
    """Снимает и закрывает все хендлеры (после неудачного dictConfig)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass

    for obj in list(logging.Logger.manager.loggerDict.values()):
        if not isinstance(obj, logging.Logger):
            continue
        for handler in list(obj.handlers):
            obj.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # noqa: BLE001
                pass

    root.setLevel(logging.WARNING)


def _setup_stderr_logging() -> None:
    """Последний резерв: только stderr, но приложение запускается."""
    _reset_handlers()

    handler = SafeConsoleHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(MAIN_FORMAT, DATE_FORMAT))
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    for name in APP_LOGGERS + ("audit",):
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True

    _safe_stderr("Логирование: файлы журналов недоступны, вывод только в stderr")


def setup_logging(log_dir: str = None, log_level: str = "INFO", debug: bool = False) -> None:
    """
    Настраивает логирование всего приложения.

    :param log_dir: папка для логов. По умолчанию — `<папка данных>/logs`
        (см. core/data_paths.py: `%APPDATA%\\LogisticsContractor\\logs` в .exe,
        `tests/_tmp/_appdata/logs` в разработке)
    :param log_level: уровень файла app.log (по умолчанию INFO)
    :param debug: включает logs/debug.log с уровнем DEBUG (флаг --debug)
    """
    # ── Папка логов ──
    if log_dir is None:
        # Журналы ПИШУТСЯ в работе, поэтому живут в папке данных, а не рядом
        # с .exe: папку установки при обновлении заменяют, а в Program Files
        # обычный пользователь и вовсе писать не может (core/data_paths.py).
        try:
            from core import data_paths

            data_paths.ensure_data_root()
            log_dir = str(data_paths.logs_dir())
        except Exception as e:  # noqa: BLE001 — запуск важнее журнала
            _safe_stderr(
                f"Логирование: папка данных недоступна ({e}); "
                f"использую {FALLBACK_LOG_DIR}"
            )
            log_dir = FALLBACK_LOG_DIR

    log_dir = _prepare_log_dir(log_dir)

    # Права: логи могут содержать рабочие данные — только текущий пользователь
    try:
        from core.security import restrict_to_current_user
        restrict_to_current_user(log_dir, recursive=True)
    except Exception as e:  # noqa: BLE001 — логирование не должно падать
        _safe_stderr(f"Не удалось ограничить права на папку логов: {e}")

    app_log = os.path.join(log_dir, APP_LOG_FILENAME)
    debug_log = os.path.join(log_dir, DEBUG_LOG_FILENAME)
    errors_log = os.path.join(log_dir, ERRORS_LOG_FILENAME)
    audit_log = os.path.join(log_dir, AUDIT_LOG_FILENAME)

    app_level = str(log_level).upper()

    handlers = {
        # ── Основной лог: только важное ──
        "file": {
            "class": "config.logging_config.SafeRotatingFileHandler",
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
            "class": "config.logging_config.SafeRotatingFileHandler",
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
            "class": "config.logging_config.SafeRotatingFileHandler",
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
            "class": "config.logging_config.SafeRotatingFileHandler",
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

    try:
        logging.config.dictConfig(config)
    except Exception as exc:  # noqa: BLE001 — запуск важнее журнала
        # Сюда попадаем, только если не помог даже SafeRotatingFileHandler.
        # Приложение обязано запуститься: оставляем stderr.
        _safe_stderr(
            f"Логирование: не удалось настроить файлы журналов ({exc}); "
            "вывод только в stderr"
        )
        _setup_stderr_logging()
        return

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
