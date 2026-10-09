#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Глобальный перехват необработанных исключений (шаг «sys.excepthook → errors.log»).

Приложение запускается через pythonw.exe: консоли у него нет, sys.stderr
равен None, поэтому traceback необработанного исключения уходил в никуда.
В logs/errors.log попадало только то, что залогировали явно
(logger.error(..., exc_info=True)). Падение 09.10.2026 (код 0xC0000409 в
Qt5Core.dll) именно поэтому и удалось разобрать только по журналу Windows.

install_crash_handler() ставит два хука:

  * sys.excepthook       — исключение главного потока. Сюда же приходит
                           исключение из слота Qt: PyQt5 зовёт sys.excepthook
                           перед qFatal(), то есть запись появляется ДО
                           аварийного завершения процесса;
  * threading.excepthook — исключение рабочего потока, не пойманное внутри
                           него (Python 3.8+).

Оба пишут в тот же журнал ошибок, что и остальное приложение: логгер
«core.crash_handler» — наследник настроенного логгера «core», поэтому запись
получает хендлеры logs/errors.log и logs/app.log из config/logging_config.py
вместе с полным traceback (exc_info).

Решения приняты осознанно:

  * старый хук НЕ вызывается: он пишет в stderr, которого у pythonw.exe нет,
    а при живом stderr дал бы вторую, бесхозную копию traceback;
  * хук ничего не «чинит» и процесс не завершает — только пишет запись;
  * если логирование ещё не настроено (хендлеров нет ни у наших логгеров,
    ни у корневого), traceback уходит в stderr — последний доступный канал;
  * установка идемпотентна: повторный вызов не оборачивает хук в хук, иначе
    одно исключение дало бы две записи;
  * значения из форм в запись не добавляются: пишутся текст самого исключения
    и traceback — это диагностика, а не данные договора.

Вызывается из main.py сразу после setup_logging() и ДО создания QApplication.
"""

import logging
import sys
import threading
import traceback

logger = logging.getLogger(__name__)

#: Метка на функции-хуке: «это уже наш обработчик».
_OWN_HOOK_FLAG = "_logistics_crash_handler"

#: Имя главного потока в записи (у threading.main_thread() имя то же).
MAIN_THREAD_NAME = "MainThread"

#: Имя потока, когда его не удалось определить.
UNKNOWN_THREAD_NAME = "unknown"


def _mark_own(hook):
    """Помечает хук меткой, по которой install_crash_handler узнаёт своего."""
    setattr(hook, _OWN_HOOK_FLAG, True)
    return hook


def _is_own_hook(hook) -> bool:
    """Хук уже наш? Тогда повторная установка не нужна."""
    return bool(getattr(hook, _OWN_HOOK_FLAG, False))


def format_exception(exc_type, exc_value, exc_tb) -> str:
    """
    Полный traceback одной строкой.

    Локальные переменные кадров в текст не попадают (Python их по умолчанию
    не захватывает) — значит, значения из форм в журнал не утекут.
    """
    return "".join(traceback.format_exception(exc_type, exc_value, exc_tb))


def _summary(exc_type, exc_value) -> str:
    """
    Шапка записи: «ТипИсключения: текст».

    Переводы строк схлопываются: запись в журнале должна оставаться одной
    строкой, иначе её неудобно искать. Тип берётся и у «мусорного» входа
    (хук обязан пережить любой вызов).
    """
    name = getattr(exc_type, "__name__", None)
    if not name:
        name = str(exc_type) if exc_type is not None else "NoneType"

    text = "" if exc_value is None else " ".join(str(exc_value).split())
    return f"{name}: {text}" if text else name


def _write_stderr(exc_type, exc_value, exc_tb, header: str) -> None:
    """
    Последний канал: stderr.

    У pythonw.exe stderr равен None — тогда молча выходим: писать некуда,
    а падать диагностике нельзя.
    """
    stream = sys.stderr or getattr(sys, "__stderr__", None)
    if stream is None:
        return
    try:
        stream.write(header + "\n")
        stream.write(format_exception(exc_type, exc_value, exc_tb))
        stream.flush()
    except Exception:  # noqa: BLE001 — хук не имеет права ронять процесс
        pass


def _log_exception(exc_type, exc_value, exc_tb, thread_name: str) -> None:
    """Пишет исключение в журнал ошибок, а без настроенного логирования — в stderr."""
    header = (
        f"Необработанное исключение: {_summary(exc_type, exc_value)} "
        f"| поток={thread_name}"
    )

    if logger.hasHandlers():
        try:
            # exc_info тройкой (а не sys.exc_info()): хук зовут и тогда, когда
            # «текущего» исключения в этом потоке уже нет.
            logger.error(header, exc_info=(exc_type, exc_value, exc_tb))
            return
        except Exception:  # noqa: BLE001 — ниже есть куда отступить
            pass

    _write_stderr(exc_type, exc_value, exc_tb, header)


@_mark_own
def handle_uncaught_exception(exc_type, exc_value, exc_tb) -> None:
    """sys.excepthook: необработанное исключение главного потока."""
    _log_exception(exc_type, exc_value, exc_tb, MAIN_THREAD_NAME)


@_mark_own
def handle_thread_exception(args) -> None:
    """
    threading.excepthook: исключение рабочего потока.

    :param args: threading.ExceptHookArgs (exc_type, exc_value, exc_traceback,
                 thread). Поля читаются через getattr: хук не должен падать
                 даже на неполном объекте.
    """
    thread = getattr(args, "thread", None)
    name = getattr(thread, "name", None)
    thread_name = name if isinstance(name, str) and name else UNKNOWN_THREAD_NAME

    _log_exception(
        getattr(args, "exc_type", None),
        getattr(args, "exc_value", None),
        getattr(args, "exc_traceback", None),
        thread_name,
    )


def _install_threading_hook() -> bool:
    """
    Ставит threading.excepthook.

    Возвращает False, если Python его не поддерживает (3.7 и старше): это
    не повод падать — исключения главного потока перехватываются в любом
    случае, а о неперехваченных потоках честно пишем в журнал.
    """
    if not hasattr(threading, "excepthook"):
        logger.warning(
            "threading.excepthook недоступен (Python %s): исключения рабочих "
            "потоков в журнал не попадут",
            ".".join(str(part) for part in sys.version_info[:3]),
        )
        return False

    if not _is_own_hook(threading.excepthook):
        threading.excepthook = handle_thread_exception
    return True


def install_crash_handler():
    """
    Включает глобальный перехват необработанных исключений.

    Вызывается из main.py сразу после setup_logging() и до создания
    QApplication: тогда в журнал попадает и падение при запуске.

    Возвращает установленный sys.excepthook; повторный вызов возвращает тот же
    хук и ничего не переустанавливает (дублей в журнале не будет).
    """
    hook = getattr(sys, "excepthook", None)
    if not _is_own_hook(hook):
        hook = handle_uncaught_exception
        sys.excepthook = hook

    threads_covered = _install_threading_hook()
    logger.info(
        "Перехват необработанных исключений включён: главный поток%s",
        " и рабочие потоки" if threads_covered else " (рабочие потоки — нет)",
    )
    return hook
