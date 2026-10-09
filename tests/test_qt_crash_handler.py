#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты диагностики аварийного завершения (core/qt_crash_handler.py).

Проверяется ровно то, о чём договорились на шаге «диагностика падения при
выходе»:

  * faulthandler включается, пишет в свой файл и НЕ включается в обычном
    режиме (только с --debug);
  * обработчик сообщений Qt ставится, пишет в логгер `qt.messages` с нужным
    уровнем для каждого из пяти типов сообщений и НЕ ставится в обычном
    режиме;
  * QtFatalMsg дополнительно даёт стек всех потоков Python — до того, как
    Qt вызовет abort();
  * обработчик переживает «мусорный» контекст и незнакомый тип сообщения,
    а о своём сбое честно пишет предупреждение, а не молчит;
  * маркер выхода попадает в logs/app.log — по нему и отличают штатный
    выход от аварийного;
  * модуль не тянет PyQt5 на импорте: core/ не должен зависеть от Qt;
  * в записи нет значений из форм (ПДн), а в стеке — локальных переменных.

ВАЖНО про изоляцию: faulthandler и обработчик сообщений Qt — настройки ВСЕГО
процесса pytest. Поэтому каждый тест снимает их за собой, а состояние
faulthandler возвращается к тому, что было до теста (его включает сам pytest).
"""

import faulthandler
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

import config.logging_config as logging_config
import core.qt_crash_handler as qt_handler

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Соответствие типа сообщения Qt уровню logging (см. _QT_LEVELS_BY_NAME).
QT_TYPES = [
    ("QtDebugMsg", 0, logging.DEBUG),
    ("QtInfoMsg", 4, logging.INFO),
    ("QtWarningMsg", 1, logging.WARNING),
    ("QtCriticalMsg", 2, logging.ERROR),
    ("QtFatalMsg", 3, logging.CRITICAL),
]


# ─────────────────────────────────────────────────────────────
# Изоляция и помощники
# ─────────────────────────────────────────────────────────────
def _reset_logging() -> None:
    """Снимает и закрывает хендлеры setup_logging() (как в test_crash_handler)."""
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


@pytest.fixture(autouse=True)
def isolated_diagnostics():
    """Чистая диагностика до и после теста; состояние faulthandler — как было."""
    was_enabled = faulthandler.is_enabled()

    qt_handler.reset_for_tests()
    # Записи qt.messages должны доходить до caplog (корневой логгер).
    logging.getLogger(qt_handler.QT_LOGGER_NAME).propagate = True

    yield

    _reset_logging()
    qt_handler.reset_for_tests()
    if was_enabled:
        faulthandler.enable()


@pytest.fixture
def logs_dir(work_dir, monkeypatch):
    """Отдельная папка логов на каждый тест + отключённый icacls."""
    monkeypatch.setattr(
        "core.security.restrict_to_current_user", lambda *a, **k: True
    )
    path = work_dir / f"{uuid.uuid4().hex[:8]}_qtdiag"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path) -> str:
    for name in list(logging.Logger.manager.loggerDict) + [""]:
        obj = logging.getLogger(name) if name else logging.getLogger()
        if not isinstance(obj, logging.Logger):
            continue
        for handler in obj.handlers:
            try:
                handler.flush()
            except Exception:  # noqa: BLE001 — тест не должен падать на flush
                pass
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _qt_records(caplog):
    return [r for r in caplog.records if r.name == qt_handler.QT_LOGGER_NAME]


# ─────────────────────────────────────────────────────────────
# faulthandler
# ─────────────────────────────────────────────────────────────
def test_faulthandler_writes_to_its_own_file(logs_dir):
    """faulthandler включается и пишет дамп в logs/faulthandler.log."""
    path = qt_handler.install_faulthandler(logs_dir)

    assert path == str(logs_dir / qt_handler.FAULTHANDLER_FILENAME)
    assert faulthandler.is_enabled()
    assert Path(path).exists()
    assert qt_handler.faulthandler_path() == path


def test_faulthandler_second_install_replaces_file(logs_dir):
    """Повторное включение не падает и оставляет ровно один рабочий файл."""
    first = qt_handler.install_faulthandler(logs_dir)
    second = qt_handler.install_faulthandler(logs_dir)

    assert first == second
    assert faulthandler.is_enabled()
    assert qt_handler.faulthandler_path() == second


def test_faulthandler_reports_failure_without_raising(logs_dir, caplog):
    """
    Недоступная папка — предупреждение в журнал, а не исключение.

    Диагностика не имеет права мешать запуску приложения: папка, на месте
    которой лежит файл, создать каталог не даст.
    """
    blocker = logs_dir / "not_a_dir"
    blocker.write_text("занято", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="core.qt_crash_handler"):
        result = qt_handler.install_faulthandler(blocker / "logs")

    assert result is None
    assert any("faulthandler не включён" in r.getMessage() for r in caplog.records)


def test_disable_faulthandler_clears_state(logs_dir):
    """disable_faulthandler выключает дамп и забывает путь."""
    qt_handler.install_faulthandler(logs_dir)

    qt_handler.disable_faulthandler()

    assert not faulthandler.is_enabled()
    assert qt_handler.faulthandler_path() is None


# ─────────────────────────────────────────────────────────────
# Сообщения Qt
# ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,msg_type,level", QT_TYPES)
def test_qt_message_levels(caplog, name, msg_type, level):
    """
    Каждому типу сообщения Qt — свой уровень записи.

    У QtFatalMsg записей две (само сообщение и стек потоков), поэтому
    проверяется первая: она и есть сообщение Qt.
    """
    with caplog.at_level(logging.DEBUG, logger=qt_handler.QT_LOGGER_NAME):
        qt_handler.handle_qt_message(msg_type, None, "проверка")

    records = _qt_records(caplog)
    assert records, "ни одной записи qt.messages"
    assert records[0].levelno == level
    assert records[0].getMessage() == f"{name}: проверка"
    if level < logging.CRITICAL:
        assert len(records) == 1, [r.getMessage() for r in records]


def test_qt_fatal_message_logs_all_thread_stacks(caplog):
    """
    QtFatalMsg даёт и само сообщение, и стек потоков.

    После QtFatalMsg Qt вызывает abort(), поэтому стек нужно успеть записать
    здесь же — другого шанса не будет.
    """
    with caplog.at_level(logging.DEBUG, logger=qt_handler.QT_LOGGER_NAME):
        qt_handler.handle_qt_message(3, None, "фатальное сообщение")

    records = _qt_records(caplog)
    assert len(records) == 2
    assert records[0].getMessage() == "QtFatalMsg: фатальное сообщение"
    assert "Стек всех потоков Python" in records[1].getMessage()


def test_qt_message_keeps_place_in_qt_sources(caplog):
    """Место в исходниках Qt попадает в запись (файл:строка, функция)."""

    class _Context:
        file = "qwidget.cpp"
        line = 1234
        function = "QWidget::show"

    with caplog.at_level(logging.DEBUG, logger=qt_handler.QT_LOGGER_NAME):
        qt_handler.handle_qt_message(1, _Context(), "предупреждение")

    assert _qt_records(caplog)[0].getMessage() == (
        "QtWarningMsg: предупреждение | qwidget.cpp:1234, QWidget::show"
    )


def test_qt_message_survives_garbage_context_and_type(caplog):
    """
    Мусор вместо контекста и незнакомый тип сообщения не роняют обработчик.

    Незнакомый тип пишется уровнем WARNING и с самим числом в тексте: по нему
    видно, что Qt прислала что-то, чего мы не знаем, — и это не потеряется.
    """
    with caplog.at_level(logging.DEBUG, logger=qt_handler.QT_LOGGER_NAME):
        qt_handler.handle_qt_message(1, "не контекст", "первое")
        qt_handler.handle_qt_message(99, object(), "второе")

    messages = [r.getMessage() for r in _qt_records(caplog)]
    assert messages == [
        "QtWarningMsg: первое",
        "QtMsgType(99): второе",
    ]


def test_broken_handler_reports_itself_once(caplog, monkeypatch):
    """
    Сбой внутри обработчика не молчит: одна запись-предупреждение.

    Молчание здесь уже стоило времени: карта уровней строилась через
    QtMsgType.value, которого в PyQt5 нет, и обработчик «ничего не делал».
    """
    def _broken(_msg_type):
        raise RuntimeError("имитация сбоя")

    monkeypatch.setattr(qt_handler, "_level_for", _broken)

    with caplog.at_level(logging.DEBUG, logger="core.qt_crash_handler"):
        qt_handler.handle_qt_message(1, None, "раз")
        qt_handler.handle_qt_message(1, None, "два")

    warnings = [
        r for r in caplog.records
        if r.name == "core.qt_crash_handler" and "не смог записать" in r.getMessage()
    ]
    assert len(warnings) == 1


def test_install_qt_message_handler_is_idempotent():
    """Повторная установка не переставляет обработчик и не падает."""
    assert qt_handler.install_qt_message_handler() is True
    assert qt_handler.install_qt_message_handler() is True

    from PyQt5.QtCore import qInstallMessageHandler  # noqa: F401 — наличие API

    assert qt_handler._state["qt_handler_installed"] is True


# ─────────────────────────────────────────────────────────────
# Стек потоков и ПДн
# ─────────────────────────────────────────────────────────────
def test_thread_stacks_have_no_local_variables():
    """
    В стеке нет локальных переменных, значит нет и значений из форм.

    traceback.format_stack локальные переменные не печатает — проверяем это
    на живом примере, а не на словах.
    """
    secret = "Иванов Иван Иванович 1234 567890"

    stack = qt_handler.format_all_threads_traceback()

    assert "MainThread" in stack
    assert "test_thread_stacks_have_no_local_variables" in stack
    assert secret not in stack
    assert "Иванов" not in stack


# ─────────────────────────────────────────────────────────────
# Единая точка входа и маркер выхода
# ─────────────────────────────────────────────────────────────
def test_normal_mode_keeps_heavy_tools_off(logs_dir):
    """Обычный режим: faulthandler и обработчик Qt выключены, маркер — есть."""
    result = qt_handler.install_crash_diagnostics(debug=False, logs_dir=logs_dir)

    assert result == {"debug": False, "faulthandler": None, "qt_handler": False}
    assert not faulthandler.is_enabled()
    assert not (logs_dir / qt_handler.FAULTHANDLER_FILENAME).exists()
    assert qt_handler._state["exit_marker_installed"] is True


def test_debug_mode_enables_both_tools(logs_dir):
    """Режим --debug: включены и faulthandler, и обработчик сообщений Qt."""
    result = qt_handler.install_crash_diagnostics(debug=True, logs_dir=logs_dir)

    assert result["debug"] is True
    assert result["faulthandler"] == str(
        logs_dir / qt_handler.FAULTHANDLER_FILENAME
    )
    assert result["qt_handler"] is True
    assert faulthandler.is_enabled()


def test_exit_marker_is_written_to_app_log(logs_dir):
    """
    Маркер выхода попадает в logs/app.log.

    Именно по этой записи и отличают штатный выход от аварийного: если после
    «=== Запуск приложения ===» её нет — процесс умер, не дойдя до atexit.
    """
    logging_config.setup_logging(log_dir=str(logs_dir))
    qt_handler.install_exit_marker()

    qt_handler._log_normal_exit()

    app_log = _read(logs_dir / logging_config.APP_LOG_FILENAME)
    assert "Приложение завершается нормально" in app_log
    assert f"pid={os.getpid()}" in app_log


def test_exit_marker_is_installed_once(logs_dir):
    """Повторная установка маркера не регистрирует второй atexit-хук."""
    qt_handler.install_exit_marker()
    started = qt_handler._state["started_at"]
    qt_handler.install_exit_marker()

    assert qt_handler._state["started_at"] == started


def test_reset_for_tests_returns_clean_state(logs_dir):
    """reset_for_tests снимает всё, что поставила установка."""
    qt_handler.install_crash_diagnostics(debug=True, logs_dir=logs_dir)
    assert faulthandler.is_enabled()

    qt_handler.reset_for_tests()

    assert not faulthandler.is_enabled()
    assert qt_handler.faulthandler_path() is None
    assert qt_handler._state == {
        "faulthandler_file": None,
        "faulthandler_path": None,
        "qt_handler_installed": False,
        "qt_handler_error_logged": False,
        "exit_marker_installed": False,
        "started_at": None,
        "qt_levels": None,
    }


# ─────────────────────────────────────────────────────────────
# core/ без Qt
# ─────────────────────────────────────────────────────────────
def test_module_does_not_import_pyqt5():
    """
    Импорт модуля не тянет PyQt5.

    core/ — доменная логика без Qt (правило проекта): модуль обязан
    импортироваться и в окружении без PyQt5, просто без обработчика сообщений.
    """
    script = (
        "import sys; import core.qt_crash_handler as m; "
        "assert 'PyQt5' not in sys.modules, 'PyQt5 загружен на импорте'; "
        "print('OK')"
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=300,
    )

    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_main_installs_diagnostics_after_logging():
    """
    main.py включает диагностику после логирования и до создания QApplication.

    Порядок важен: диагностика пишет в журнал, поэтому логирование должно
    быть уже настроено, а хуки — стоять до Qt, иначе падение на старте
    останется без следа.
    """
    source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

    logging_at = source.index("setup_logging(debug=_ARGS.debug)")
    handler_at = source.index("install_crash_handler()")
    diagnostics_at = source.index("install_crash_diagnostics(")
    qt_at = source.index("from PyQt5.QtWidgets import QApplication", logging_at)

    assert logging_at < handler_at < diagnostics_at < qt_at
    assert "install_crash_diagnostics(debug=_ARGS.debug)" in source
