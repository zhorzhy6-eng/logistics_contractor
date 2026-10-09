#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диагностика аварийного завершения: faulthandler, сообщения Qt, маркер выхода.

Шаг «диагностика и починка падения при выходе». Падение при выходе
(0xC0000005 access violation / 0xC0000409 stack buffer overrun) приходит
МИМО Python: `sys.excepthook` (см. core/crash_handler.py) его не видит —
в logs/errors.log за всю историю нет ни одной записи о таком падении.
Значит, нужны инструменты, которые смотрят ниже уровня Python.

Три инструмента, все вместе включаются одной функцией
`install_crash_diagnostics()` (её зовёт main.py сразу после setup_logging):

  а) faulthandler.enable() — при падении на уровне C (segfault, access
     violation) Python успевает напечатать стек ВСЕХ потоков в
     logs/faulthandler.log. Это единственный способ увидеть, какой кадр
     Python жил в момент падения. Файл открыт всё время работы процесса,
     поэтому ссылка на него хранится в модуле: иначе сборщик мусора закроет
     файл, и дамп уйдёт в никуда.
  б) qInstallMessageHandler — перехват сообщений Qt. Критические сообщения
     Qt идут ПЕРЕД qFatal()/abort() — это то, что видно перед падением
     (у pythonw.exe нет stderr, поэтому раньше они пропадали). Для
     QtFatalMsg дополнительно пишется стек всех потоков Python.
  в) atexit-хук — строка «приложение завершается нормально». Она и есть
     различитель: есть строка — выход штатный, нет строки — процесс умер
     аварийно (или его сняли извне). Без неё отличить одно от другого по
     logs/app.log невозможно.

Решения приняты осознанно:

  * faulthandler и обработчик Qt включаются ТОЛЬКО в режиме --debug
    (требование шага: обычная работа не должна замедляться). Строка
    «Диагностика аварий: pid=…» и маркер выхода пишутся всегда: это две
    дешёвые записи, и именно они дают след, когда падение случится в
    обычном запуске;
  * прежний обработчик сообщений Qt НЕ вызывается: он пишет в stderr,
    которого у pythonw.exe нет;
  * обработчик Qt ничего не бросает: исключение внутри него Qt не ловит;
  * в записи попадают только тип сообщения, текст Qt и место в исходниках
    Qt. Значений из форм там нет; стеки Python печатаются без локальных
    переменных (traceback.format_stack их не захватывает) — ПДн не утекают;
  * установка идемпотентна: повторный вызов ничего не дублирует.

Модуль лежит в core/, но Qt импортируется ВНУТРИ функций: core/ не должен
зависеть от PyQt5 на импорте (правило проекта), а без Qt модуль обязан
работать — просто без обработчика сообщений.
"""

import atexit
import faulthandler
import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)

#: Имя логгера для сообщений Qt (требование шага).
QT_LOGGER_NAME = "qt.messages"

#: Файл с дампом faulthandler при падении на уровне C.
FAULTHANDLER_FILENAME = "faulthandler.log"

#: Состояние процесса: файл faulthandler, флаги установки, время старта.
#: Файл faulthandler держится здесь намеренно — иначе его закроет GC.
_state = {
    "faulthandler_file": None,
    "faulthandler_path": None,
    "qt_handler_installed": False,
    "qt_handler_error_logged": False,
    "exit_marker_installed": False,
    "started_at": None,
    "qt_levels": None,
}

_lock = threading.Lock()

#: Уровни для пяти типов сообщений Qt (имена — как в QtMsgType).
_QT_LEVELS_BY_NAME = {
    "QtDebugMsg": logging.DEBUG,
    "QtInfoMsg": logging.INFO,
    "QtWarningMsg": logging.WARNING,
    "QtCriticalMsg": logging.ERROR,
    "QtFatalMsg": logging.CRITICAL,
}


# ─────────────────────────────────────────────────────────────
# Пути
# ─────────────────────────────────────────────────────────────
def default_logs_dir() -> Path:
    """Папка логов проекта (<корень>/logs) — как в config/logging_config.py."""
    return Path(__file__).resolve().parents[1] / "logs"


def _resolve_logs_dir(logs_dir=None) -> Path:
    """Папка для faulthandler.log: явная или папка логов проекта."""
    if logs_dir is not None:
        return Path(logs_dir)
    return default_logs_dir()


# ─────────────────────────────────────────────────────────────
# а) faulthandler — стек Python при падении на уровне C
# ─────────────────────────────────────────────────────────────
def install_faulthandler(logs_dir=None):
    """
    Включает faulthandler с выводом в logs/faulthandler.log.

    Возвращает путь к файлу дампа или None, если включить не удалось
    (нет прав, нет faulthandler в этой сборке Python). Диагностика не имеет
    права ронять приложение, поэтому сбой только логируется.

    Файл открывается в режиме добавления: дампы прошлых падений не теряются.
    """
    path = _resolve_logs_dir(logs_dir) / FAULTHANDLER_FILENAME

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(path, "a", encoding="utf-8")
    except OSError as e:
        logger.warning("faulthandler не включён (не открыть %s): %s", path, e)
        return None

    try:
        faulthandler.enable(file=stream, all_threads=True)
    except (RuntimeError, ValueError, AttributeError) as e:
        stream.close()
        logger.warning("faulthandler не включён: %s", e)
        return None

    with _lock:
        # Прежний файл (если был) закрываем: держать два открытых незачем.
        previous = _state.get("faulthandler_file")
        _state["faulthandler_file"] = stream
        _state["faulthandler_path"] = str(path)
    if previous is not None and previous is not stream:
        try:
            previous.close()
        except Exception:  # noqa: BLE001 — закрытие прошлого файла не важно
            pass

    logger.info("faulthandler включён: %s (дамп при падении на уровне C)", path)
    return str(path)


def disable_faulthandler() -> None:
    """
    Выключает faulthandler и закрывает файл дампа.

    Нужно тестам: faulthandler — настройка всего процесса, и оставлять её
    включённой после теста нельзя.
    """
    try:
        faulthandler.disable()
    except Exception:  # noqa: BLE001 — выключение не должно падать
        pass

    with _lock:
        stream = _state.get("faulthandler_file")
        _state["faulthandler_file"] = None
        _state["faulthandler_path"] = None
    if stream is not None:
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass


def faulthandler_path():
    """Путь к файлу дампа (None, если faulthandler не включён)."""
    return _state.get("faulthandler_path")


# ─────────────────────────────────────────────────────────────
# Стек всех потоков Python (для QtFatalMsg и для маркера выхода)
# ─────────────────────────────────────────────────────────────
def format_all_threads_traceback() -> str:
    """
    Стеки всех живых потоков Python одной строкой-блоком.

    Локальные переменные кадров НЕ печатаются (traceback.format_stack их не
    захватывает) — значения из форм в журнал не попадут.
    """
    frames = sys._current_frames()  # noqa: SLF001 — единственный способ увидеть все потоки
    parts = []
    for thread in threading.enumerate():
        frame = frames.get(thread.ident)
        if frame is None:
            continue
        stack = "".join(traceback.format_stack(frame))
        parts.append(f"--- поток {thread.name} ---\n{stack}")
    return "".join(parts) if parts else "(стеков потоков нет)"


# ─────────────────────────────────────────────────────────────
# б) сообщения Qt
# ─────────────────────────────────────────────────────────────
def _msg_type_key(msg_type):
    """Числовой ключ типа сообщения Qt (PyQt5 отдаёт int, но бывает и enum)."""
    if isinstance(msg_type, int):
        return msg_type
    value = getattr(msg_type, "value", None)
    return value if isinstance(value, int) else None


def _level_for(msg_type):
    """
    Пара (уровень logging, имя типа сообщения Qt).

    Карта строится один раз и по ЧИСЛАМ: PyQt5 передаёт в обработчик числовое
    значение QtMsgType. В PyQt5 5.15 члены QtMsgType сами являются int и
    атрибута .value у них НЕТ (проверено), поэтому значение берётся через
    _msg_type_key, а не через .value напрямую.
    """
    levels = _state.get("qt_levels")
    if levels is None:
        from PyQt5.QtCore import QtMsgType

        levels = {}
        for name, level in _QT_LEVELS_BY_NAME.items():
            key = _msg_type_key(getattr(QtMsgType, name, None))
            if key is not None:
                levels[key] = (level, name)
        _state["qt_levels"] = levels

    key = _msg_type_key(msg_type)
    if key is not None and key in levels:
        return levels[key]
    return logging.WARNING, f"QtMsgType({msg_type!r})"


def _qt_context_text(context) -> str:
    """
    Место в исходниках Qt: «файл:строка, функция».

    У release-сборки Qt полей может не быть вовсе — тогда пустая строка.
    Значений из форм здесь нет и быть не может: это данные Qt о самой себе.
    """
    if context is None:
        return ""
    file_name = getattr(context, "file", None)
    function = getattr(context, "function", None)
    line = getattr(context, "line", None)

    where = ""
    if file_name:
        where = str(file_name)
        if isinstance(line, int) and line > 0:
            where += f":{line}"
    if function:
        where = f"{where}, {function}" if where else str(function)
    return where


def _warn_handler_broken(error) -> None:
    """
    Один раз сообщает, что обработчик Qt сам сломался.

    Молча глотать свои сбои диагностике нельзя: именно так провал в
    обработчике и остался бы незамеченным (на этом уже спотыкались — карта
    уровней строилась через .value, которого у QtMsgType в PyQt5 нет).
    """
    with _lock:
        if _state.get("qt_handler_error_logged"):
            return
        _state["qt_handler_error_logged"] = True
    try:
        logger.warning(
            "Обработчик сообщений Qt не смог записать сообщение: %r", error
        )
    except Exception:  # noqa: BLE001 — предупреждение не важнее самого выхода
        pass


def handle_qt_message(msg_type, context, message) -> None:
    """
    Обработчик сообщений Qt (ставится через qInstallMessageHandler).

    Пишет запись в логгер «qt.messages»; для QtFatalMsg — ещё и стек всех
    потоков Python (Qt после этого зовёт abort(), дальше писать будет некому).
    Исключений не бросает: обработчик Qt — не место для падений. Но и молчать
    о своём сбое не должен — о нём один раз пишет _warn_handler_broken().
    """
    try:
        level, name = _level_for(msg_type)
        where = _qt_context_text(context)
        text = f"{name}: {message}"
        if where:
            text += f" | {where}"

        # Логгер qt.messages входит в APP_LOGGERS (config/logging_config.py),
        # поэтому запись получает те же хендлеры, что и остальные: logs/app.log,
        # logs/debug.log (в режиме --debug) и logs/errors.log — от ERROR.
        qt_logger = logging.getLogger(QT_LOGGER_NAME)
        qt_logger.log(level, text)

        if level >= logging.CRITICAL:
            qt_logger.critical(
                "Стек всех потоков Python на момент QtFatalMsg:\n%s",
                format_all_threads_traceback(),
            )
    except Exception as e:  # noqa: BLE001 — обработчик Qt не имеет права падать
        _warn_handler_broken(e)


def install_qt_message_handler() -> bool:
    """
    Ставит свой обработчик сообщений Qt.

    Старый обработчик не сохраняется и не вызывается: он писал в stderr,
    которого у pythonw.exe нет (а при живом stderr дал бы вторую копию).
    Возвращает False, если PyQt5 недоступен — без Qt модуль тоже обязан жить.
    """
    with _lock:
        if _state["qt_handler_installed"]:
            return True

    try:
        from PyQt5.QtCore import qInstallMessageHandler
    except ImportError as e:  # pragma: no cover — зависит от окружения
        logger.warning("Обработчик сообщений Qt не установлен: %s", e)
        return False

    try:
        qInstallMessageHandler(handle_qt_message)
    except Exception as e:  # noqa: BLE001 — диагностика не должна ронять запуск
        logger.warning("Обработчик сообщений Qt не установлен: %s", e)
        return False

    with _lock:
        _state["qt_handler_installed"] = True
    logger.info("Обработчик сообщений Qt установлен (логгер %s)", QT_LOGGER_NAME)
    return True


def uninstall_qt_message_handler() -> None:
    """Снимает обработчик (нужно тестам: настройка общая на весь процесс)."""
    try:
        from PyQt5.QtCore import qInstallMessageHandler
    except ImportError:  # pragma: no cover
        return

    try:
        qInstallMessageHandler(None)
    except Exception:  # noqa: BLE001
        pass

    with _lock:
        _state["qt_handler_installed"] = False


# ─────────────────────────────────────────────────────────────
# в) маркер нормального выхода
# ─────────────────────────────────────────────────────────────
def _loggers_with_handlers():
    """Все логгеры, у которых есть свои хендлеры, плюс корневой."""
    found = []
    for name in list(logging.Logger.manager.loggerDict):
        obj = logging.getLogger(name)
        if isinstance(obj, logging.Logger) and obj.handlers:
            found.append(obj)
    root = logging.getLogger()
    if root.handlers:
        found.append(root)
    return found


class _console_detached:
    """
    Временно снимает консольные хендлеры.

    Нужно на atexit: к этому моменту потоки вывода уже закрыты, и попытка
    записи в консоль даёт «--- Logging error ---» в stderr (замечено в
    прогоне pytest). Файловые хендлеры живы — logging.shutdown вызывается
    позже, — поэтому запись в logs/app.log не страдает.
    """

    def __enter__(self):
        self._saved = []
        for lg in _loggers_with_handlers():
            for handler in list(lg.handlers):
                is_file = hasattr(handler, "baseFilename")
                if isinstance(handler, logging.StreamHandler) and not is_file:
                    self._saved.append((lg, handler))
                    lg.removeHandler(handler)
        return self

    def __exit__(self, *_exc):
        for lg, handler in self._saved:
            lg.addHandler(handler)
        return False


def _log_normal_exit() -> None:
    """
    atexit: процесс дошёл до нормального завершения.

    Запись и есть различитель: если в logs/app.log после запуска нет этой
    строки — процесс умер аварийно (или его сняли извне). atexit срабатывает
    раньше logging.shutdown (обработчики добавляются позже и потому
    вызываются первыми), поэтому файловые хендлеры ещё открыты и запись не
    теряется.
    """
    try:
        started = _state.get("started_at")
        uptime = time.monotonic() - started if started else 0.0
        with _console_detached():
            logger.info(
                "Приложение завершается нормально: pid=%s, отработано %.1f с",
                os.getpid(),
                uptime,
            )
    except Exception:  # noqa: BLE001 — маркер не имеет права падать
        pass


def install_exit_marker() -> None:
    """Ставит atexit-хук с записью «приложение завершается нормально»."""
    with _lock:
        if _state["exit_marker_installed"]:
            return
        _state["exit_marker_installed"] = True
    if _state.get("started_at") is None:
        _state["started_at"] = time.monotonic()
    atexit.register(_log_normal_exit)


# ─────────────────────────────────────────────────────────────
# Единая точка входа
# ─────────────────────────────────────────────────────────────
def install_crash_diagnostics(debug: bool = False, logs_dir=None) -> dict:
    """
    Включает диагностику аварийного завершения.

    Вызывается из main.py сразу после setup_logging() и
    install_crash_handler(): тогда в журнал попадёт и падение при запуске.

    :param debug: режим --debug — включает faulthandler и обработчик Qt
                  (в обычном режиме они выключены, чтобы не замедлять работу)
    :param logs_dir: папка для faulthandler.log (по умолчанию logs проекта)
    :return: словарь с тем, что удалось включить (для тестов и отчёта)
    """
    _state["started_at"] = time.monotonic()
    install_exit_marker()

    result = {"debug": bool(debug), "faulthandler": None, "qt_handler": False}
    if debug:
        result["faulthandler"] = install_faulthandler(logs_dir)
        result["qt_handler"] = install_qt_message_handler()

    logger.info(
        "Диагностика аварий: pid=%s, режим=%s, faulthandler=%s, "
        "обработчик Qt=%s, маркер выхода установлен",
        os.getpid(),
        "debug" if debug else "обычный",
        result["faulthandler"] or "выключен",
        "установлен" if result["qt_handler"] else "выключен",
    )
    return result


def reset_for_tests() -> None:
    """
    Снимает всё, что поставила install_crash_diagnostics.

    Нужно тестам: faulthandler и обработчик Qt — настройки всего процесса,
    и оставлять их после теста нельзя (повлияет на остальные тесты прогона).
    atexit-хук снять нельзя, поэтому снимается только пометка о нём.
    """
    disable_faulthandler()
    uninstall_qt_message_handler()
    with _lock:
        _state["exit_marker_installed"] = False
        _state["started_at"] = None
        _state["qt_levels"] = None
        _state["qt_handler_error_logged"] = False


__all__ = [
    "FAULTHANDLER_FILENAME",
    "QT_LOGGER_NAME",
    "default_logs_dir",
    "disable_faulthandler",
    "faulthandler_path",
    "format_all_threads_traceback",
    "handle_qt_message",
    "install_crash_diagnostics",
    "install_exit_marker",
    "install_faulthandler",
    "install_qt_message_handler",
    "reset_for_tests",
    "uninstall_qt_message_handler",
]
