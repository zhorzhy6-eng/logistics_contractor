#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты глобального перехвата необработанных исключений (шаг «sys.excepthook →
errors.log»).

Проверяется ровно то, о чём договорились:

  * install_crash_handler() заменяет sys.excepthook своим обработчиком;
  * необработанное исключение главного потока попадает в logs/errors.log
    записью уровня ERROR с типом исключения и полным traceback;
  * хук переживает исключение без traceback и «мусорный» вызов;
  * прежний (чужой) sys.excepthook НЕ вызывается: он писал бы в stderr,
    которого у pythonw.exe нет;
  * threading.excepthook установлен и пишет исключение рабочего потока
    вместе с именем потока;
  * повторная установка не даёт дублей в журнале;
  * пока логирование не настроено, traceback уходит в stderr (fallback).

ВАЖНО: setup_logging() настраивает логирование глобально, а хуки — общие на
весь процесс pytest, поэтому каждый тест подменяет icacls-ограничение прав,
ставит на место хуков заведомо «чужие» заглушки, а после себя снимает
хендлеры. Иначе следы теста повлияли бы на остальные тесты прогона.
"""

import logging
import re
import shutil
import sys
import threading
import types
import uuid

import pytest

import config.logging_config as logging_config
import core.crash_handler as crash_handler
from core.crash_handler import install_crash_handler

#: Запись хука в errors.log: «дата | ERROR | core.crash_handler | функция:строка | …»
#: Функция в записи — _log_exception: запись делает общий помощник хуков.
ERROR_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| ERROR\s*\| core\.crash_handler\s*\| "
    r"_log_exception:\d+ \| "
)

#: Заголовок записи о падении главного потока.
MAIN_HEADER = "Необработанное исключение: ValueError: тестовое исключение | поток=MainThread"


def _noop() -> None:
    """Пусковая функция потока: сам поток в тесте не запускается."""


# ── «Чужие» хуки-заглушки ──
# По ним видно две вещи: что установка ЗАМЕНИЛА прежний хук и что прежний хук
# после этого не вызывается (иначе traceback ушёл бы в stderr и потерялся).

_foreign_sys_calls = []
_foreign_thread_calls = []


def _foreign_sys_excepthook(exc_type, exc_value, exc_tb) -> None:
    _foreign_sys_calls.append(exc_type)


def _foreign_thread_excepthook(args) -> None:
    _foreign_thread_calls.append(args)


def _captured_error(message: str = "тестовое исключение"):
    """Тройка (тип, значение, traceback) настоящего исключения."""
    try:
        raise ValueError(message)
    except ValueError:
        return sys.exc_info()


def _reset_logging() -> None:
    """
    Снимает и закрывает хендлеры setup_logging().

    Заодно возвращает propagate логгерам приложения: setup_logging() ставит
    его в False, и без возврата записи этих логгеров перестали бы доходить до
    корневого, то есть до caplog соседних тестов прогона.
    """
    for name in list(logging.Logger.manager.loggerDict):
        obj = logging.getLogger(name)
        if not isinstance(obj, logging.Logger):
            continue
        for handler in list(obj.handlers):
            obj.removeHandler(handler)
            handler.close()
        if name in logging_config.APP_LOGGERS:
            obj.propagate = True

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.WARNING)


def _flush() -> None:
    for name in list(logging.Logger.manager.loggerDict) + [""]:
        obj = logging.getLogger(name) if name else logging.getLogger()
        if not isinstance(obj, logging.Logger):
            continue
        for handler in obj.handlers:
            try:
                handler.flush()
            except Exception:  # noqa: BLE001 — тест не должен падать на flush
                pass


def _read(path) -> str:
    _flush()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


@pytest.fixture(autouse=True)
def isolated_hooks(monkeypatch):
    """
    Чужие хуки на время теста и чистые хендлеры после него.

    Начинать с «оригинального» sys.excepthook нельзя: main.py ставит наш хук
    ещё при импорте модуля, а импортируют его тесты соседних файлов. Тогда
    проверка «хук заменён» зависела бы от порядка тестов в прогоне.
    """
    _foreign_sys_calls.clear()
    _foreign_thread_calls.clear()
    monkeypatch.setattr(sys, "excepthook", _foreign_sys_excepthook)
    monkeypatch.setattr(threading, "excepthook", _foreign_thread_excepthook, raising=False)

    yield

    _reset_logging()


@pytest.fixture
def logs_dir(work_dir, monkeypatch):
    """Отдельная папка логов на каждый тест + отключённый icacls."""
    monkeypatch.setattr(
        "core.security.restrict_to_current_user", lambda *a, **k: True
    )

    path = work_dir / f"{uuid.uuid4().hex[:8]}_crash_logs"
    path.mkdir(parents=True, exist_ok=True)

    yield path

    shutil.rmtree(path, ignore_errors=True)


# ─────────────────────────────────────────────────────────────
# Установка хуков
# ─────────────────────────────────────────────────────────────

def test_install_replaces_sys_excepthook():
    install_crash_handler()

    assert sys.excepthook is not _foreign_sys_excepthook
    assert sys.excepthook is crash_handler.handle_uncaught_exception


def test_repeated_install_keeps_same_hook():
    first = install_crash_handler()
    second = install_crash_handler()

    assert first is second
    assert sys.excepthook is first


def test_threading_excepthook_installed():
    assert hasattr(threading, "excepthook"), "Python 3.14 поддерживает threading.excepthook"

    install_crash_handler()

    assert threading.excepthook is crash_handler.handle_thread_exception


def test_install_without_threading_support(monkeypatch, logs_dir):
    """Python без threading.excepthook: хук главного потока всё равно ставится."""
    logging_config.setup_logging(str(logs_dir), debug=False)
    # Модуль threading без excepthook — как в Python 3.7 и старше.
    monkeypatch.setattr(crash_handler, "threading", types.SimpleNamespace())

    install_crash_handler()   # не должно бросить

    assert sys.excepthook is crash_handler.handle_uncaught_exception
    app_text = _read(logs_dir / logging_config.APP_LOG_FILENAME)
    assert "threading.excepthook недоступен" in app_text


# ─────────────────────────────────────────────────────────────
# Исключение главного потока → logs/errors.log
# ─────────────────────────────────────────────────────────────

def test_uncaught_exception_written_to_errors_log(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=False)
    install_crash_handler()

    sys.excepthook(*_captured_error())

    text = _read(logs_dir / logging_config.ERRORS_LOG_FILENAME)

    assert MAIN_HEADER in text
    assert "Traceback (most recent call last)" in text
    assert "ValueError: тестовое исключение" in text
    # Оформление записи — как у остальных ошибок журнала
    lines = [line for line in text.splitlines() if "Необработанное исключение" in line]
    assert lines, "записи о падении нет в errors.log"
    assert ERROR_LINE_RE.match(lines[0]), lines[0]


def test_old_excepthook_not_called(logs_dir):
    """Старый хук не зовётся: его traceback ушёл бы в stderr (у pythonw его нет)."""
    logging_config.setup_logging(str(logs_dir), debug=False)
    install_crash_handler()

    sys.excepthook(*_captured_error())

    assert _foreign_sys_calls == []


def test_handler_survives_exception_without_traceback(logs_dir):
    """Исключение без traceback: запись всё равно появляется, хук не падает."""
    logging_config.setup_logging(str(logs_dir), debug=False)
    install_crash_handler()

    sys.excepthook(ValueError, ValueError("исключение без traceback"), None)

    text = _read(logs_dir / logging_config.ERRORS_LOG_FILENAME)

    assert "Необработанное исключение: ValueError: исключение без traceback" in text
    assert "поток=MainThread" in text


def test_handler_survives_empty_call(logs_dir):
    """«Мусорный» вызов (None вместо исключения) не должен ронять процесс."""
    logging_config.setup_logging(str(logs_dir), debug=False)
    install_crash_handler()

    sys.excepthook(None, None, None)

    text = _read(logs_dir / logging_config.ERRORS_LOG_FILENAME)

    assert "Необработанное исключение: NoneType | поток=MainThread" in text


def test_double_install_writes_one_record(logs_dir):
    """Двойная установка не дублирует запись: хук не оборачивает сам себя."""
    logging_config.setup_logging(str(logs_dir), debug=False)
    install_crash_handler()
    install_crash_handler()

    sys.excepthook(*_captured_error())

    text = _read(logs_dir / logging_config.ERRORS_LOG_FILENAME)

    assert text.count("Traceback (most recent call last)") == 1
    assert text.count("Необработанное исключение") == 1


# ─────────────────────────────────────────────────────────────
# Исключение рабочего потока → logs/errors.log
# ─────────────────────────────────────────────────────────────

def test_thread_exception_written_to_errors_log(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=False)
    install_crash_handler()

    exc_type, exc_value, exc_tb = _captured_error("сбой в рабочем потоке")
    thread = threading.Thread(target=_noop, name="probe-worker")
    threading.excepthook(threading.ExceptHookArgs((exc_type, exc_value, exc_tb, thread)))

    text = _read(logs_dir / logging_config.ERRORS_LOG_FILENAME)

    assert "Необработанное исключение: ValueError: сбой в рабочем потоке" in text
    assert "поток=probe-worker" in text
    assert "Traceback (most recent call last)" in text
    # Прежний хук потока тоже не вызывался
    assert _foreign_thread_calls == []


def test_thread_exception_without_thread_name(logs_dir):
    """Неполный объект потока: имя подставляется, хук не падает."""
    logging_config.setup_logging(str(logs_dir), debug=False)
    install_crash_handler()

    exc_type, exc_value, exc_tb = _captured_error("поток без имени")
    threading.excepthook(
        threading.ExceptHookArgs((exc_type, exc_value, exc_tb, None))
    )

    text = _read(logs_dir / logging_config.ERRORS_LOG_FILENAME)

    assert "поток=unknown" in text


# ─────────────────────────────────────────────────────────────
# Связка main.py ↔ установка хука
# ─────────────────────────────────────────────────────────────

def test_main_installs_crash_handler_after_logging(monkeypatch):
    """
    main.py ставит хук при импорте: сразу после setup_logging().

    QApplication создаётся внутри main(), то есть заведомо позже — значит,
    падение при запуске приложения тоже попадает в журнал.
    """
    import importlib

    events = []
    # Настоящая установка — до подмены: иначе spy позовёт сам себя.
    real_install = crash_handler.install_crash_handler

    monkeypatch.setattr(sys, "argv", ["main.py"])
    # Логирование в тесте не поднимаем: проверяется порядок вызовов, а не файлы.
    monkeypatch.setattr(
        logging_config, "setup_logging", lambda *a, **k: events.append("logging")
    )

    def spy_install():
        events.append("crash")
        return real_install()

    monkeypatch.setattr(crash_handler, "install_crash_handler", spy_install)

    sys.modules.pop("main", None)
    try:
        importlib.import_module("main")
    finally:
        sys.modules.pop("main", None)

    assert events == ["logging", "crash"]
    assert sys.excepthook is crash_handler.handle_uncaught_exception
    assert threading.excepthook is crash_handler.handle_thread_exception


# ─────────────────────────────────────────────────────────────
# Логирование ещё не настроено — fallback в stderr
# ─────────────────────────────────────────────────────────────

def test_fallback_writes_to_stderr_without_logging(capsys):
    _reset_logging()
    root = logging.getLogger()
    saved_root_handlers = list(root.handlers)
    root.handlers[:] = []      # убираем и хендлер pytest, иначе логирование «настроено»

    try:
        install_crash_handler()
        sys.excepthook(*_captured_error("логирование ещё не настроено"))
    finally:
        root.handlers[:] = saved_root_handlers

    err = capsys.readouterr().err

    assert "Необработанное исключение: ValueError: логирование ещё не настроено" in err
    assert "Traceback (most recent call last)" in err
