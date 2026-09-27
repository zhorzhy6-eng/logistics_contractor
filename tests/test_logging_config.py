#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты углублённого логирования (Часть 2 задания).

Проверяется ровно то, о чём договорились:

  * app.log пишется всегда и не содержит DEBUG;
  * debug.log появляется ТОЛЬКО при debug=True и содержит DEBUG;
  * errors.log получает ошибку с traceback/стеком, даже если её залогировали
    без exc_info;
  * audit.log изолирован: события аудита не дублируются в app.log;
  * формат строки совпадает с заявленным;
  * сводка заполненности не раскрывает значения (защита от ПДн).

ВАЖНО: setup_logging() настраивает логирование глобально, поэтому каждый тест
подменяет icacls-ограничение прав (в песочнице он недоступен и только тормозит)
и после себя снимает хендлеры — иначе файлы останутся открытыми и повлияют
на остальные тесты.
"""

import logging
import re
import shutil
import uuid

import pytest

import config.logging_config as logging_config
from core.audit import log_event
from core.trace import filled_fields_summary


#: Формат: 2026-09-24 22:15:33 | DEBUG | core.gigachat_client | func | текст
LOG_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| "
    r"(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\| "
    r"[\w.]+\s*\| \w+ \| "
)


def _reset_logging() -> None:
    """Снимает и закрывает все хендлеры, поставленные setup_logging()."""
    for name in list(logging.Logger.manager.loggerDict):
        obj = logging.getLogger(name)
        if not isinstance(obj, logging.Logger):
            continue
        for handler in list(obj.handlers):
            obj.removeHandler(handler)
            handler.close()

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


@pytest.fixture
def logs_dir(work_dir, monkeypatch):
    """Отдельная папка логов на каждый тест + отключённый icacls."""
    monkeypatch.setattr(
        "core.security.restrict_to_current_user", lambda *a, **k: True
    )

    path = work_dir / f"{uuid.uuid4().hex[:8]}_logs"
    path.mkdir(parents=True, exist_ok=True)

    yield path

    _reset_logging()
    shutil.rmtree(path, ignore_errors=True)


# ─────────────────────────────────────────────────────────────
# app.log — важное, всегда
# ─────────────────────────────────────────────────────────────

def test_app_log_written_without_debug_flag(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=False)

    logger = logging.getLogger("ui.main_window")
    logger.debug("ЭТО ОТЛАДКА, которой не должно быть в app.log")
    logger.info("важное событие")

    app_log = logs_dir / logging_config.APP_LOG_FILENAME
    text = _read(app_log)

    assert app_log.exists()
    assert "важное событие" in text
    assert "ЭТО ОТЛАДКА" not in text
    assert " | DEBUG " not in text


def test_debug_log_not_created_without_debug_flag(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=False)
    logging.getLogger("core.gigachat_client").debug("подробность")

    assert not (logs_dir / logging_config.DEBUG_LOG_FILENAME).exists()


# ─────────────────────────────────────────────────────────────
# debug.log — только с --debug
# ─────────────────────────────────────────────────────────────

def test_debug_log_created_with_debug_flag(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=True)

    logging.getLogger("core.gigachat_client").debug(
        "GigaChat: отправка запроса | попытка 1/3"
    )

    debug_log = logs_dir / logging_config.DEBUG_LOG_FILENAME
    text = _read(debug_log)

    assert debug_log.exists()
    assert "GigaChat: отправка запроса" in text
    assert " | DEBUG " in text


def test_debug_log_line_format(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=True)

    logging.getLogger("core.gigachat_client").debug("проверка формата")

    lines = [
        line for line in _read(logs_dir / logging_config.DEBUG_LOG_FILENAME).splitlines()
        if "проверка формата" in line
    ]

    assert lines, "строка с сообщением не найдена в debug.log"
    assert LOG_LINE_RE.match(lines[0]), lines[0]
    assert "core.gigachat_client" in lines[0]


def test_debug_entries_do_not_leak_into_app_log(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=True)

    logger = logging.getLogger("ui.main_window")
    logger.debug("только для debug.log")
    logger.info("и это в app.log")

    app_text = _read(logs_dir / logging_config.APP_LOG_FILENAME)
    debug_text = _read(logs_dir / logging_config.DEBUG_LOG_FILENAME)

    assert "только для debug.log" not in app_text
    assert "только для debug.log" in debug_text


# ─────────────────────────────────────────────────────────────
# errors.log — ERROR + traceback/стек
# ─────────────────────────────────────────────────────────────

def test_errors_log_gets_error_with_stack(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=False)

    logging.getLogger("db.database").error("не удалось выполнить запрос")

    text = _read(logs_dir / logging_config.ERRORS_LOG_FILENAME)

    assert "не удалось выполнить запрос" in text
    # StackInfoFilter добавляет стек вызова даже без exc_info
    assert 'File "' in text


def test_errors_log_gets_full_traceback_for_exception(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=False)

    try:
        raise ValueError("тестовое исключение")
    except ValueError:
        logging.getLogger("core.contract_generator").exception("генерация упала")

    text = _read(logs_dir / logging_config.ERRORS_LOG_FILENAME)

    assert "Traceback (most recent call last)" in text
    assert "ValueError: тестовое исключение" in text
    assert "генерация упала" in text


def test_warning_does_not_go_to_errors_log(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=False)

    logging.getLogger("ui.main_window").warning("просто замечание")

    errors_log = logs_dir / logging_config.ERRORS_LOG_FILENAME
    text = _read(errors_log)

    assert "просто замечание" not in text


# ─────────────────────────────────────────────────────────────
# audit.log — отдельно от app.log
# ─────────────────────────────────────────────────────────────

def test_audit_log_is_separate_from_app_log(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=False)

    log_event("saved_to_db", contract_id=42, count=3)

    audit_text = _read(logs_dir / logging_config.AUDIT_LOG_FILENAME)
    app_text = _read(logs_dir / logging_config.APP_LOG_FILENAME)

    assert "saved_to_db" in audit_text
    assert "saved_to_db" not in app_text


def test_audit_hides_unknown_fields(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=False)

    log_event("driver_saved", full_name="Иванов Иван Иванович", driver_id=7)

    audit_text = _read(logs_dir / logging_config.AUDIT_LOG_FILENAME)

    assert "Иванов" not in audit_text
    assert "<скрыто>" in audit_text
    assert "driver_id=7" in audit_text


# ─────────────────────────────────────────────────────────────
# SQL в debug.log
# ─────────────────────────────────────────────────────────────

def test_sql_queries_logged_without_pii(logs_dir, isolated_db):
    logging_config.setup_logging(str(logs_dir), debug=True)

    driver_id = isolated_db.save_driver(
        {"full_name": "Иванов Иван Иванович", "phone": "+79991234567"}
    )
    isolated_db.get_all_drivers()

    debug_text = _read(logs_dir / logging_config.DEBUG_LOG_FILENAME)

    assert "SQL INSERT" in debug_text
    assert "SQL SELECT" in debug_text
    assert "время:" in debug_text
    # Параметры запросов не логируются — ни ФИО, ни телефон
    assert "Иванов" not in debug_text
    assert "+79991234567" not in debug_text
    assert driver_id > 0

    # Запрос логируется ровно один раз (нет дублей Connection + Cursor)
    inserts = [
        line for line in debug_text.splitlines()
        if "SQL INSERT: INSERT INTO drivers" in line
    ]
    assert len(inserts) == 1, inserts


def test_slow_query_marked_and_visible_in_app_log(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=False)

    from db.query_logging import _log_query

    _log_query("SELECT * FROM drivers", 2.5, rows=10)   # медленный
    _log_query("SELECT 1", 0.001)                       # быстрый

    app_text = _read(logs_dir / logging_config.APP_LOG_FILENAME)

    assert "SLOW SQL SELECT" in app_text
    assert "время: 2500.0 мс" in app_text
    # быстрый запрос в app.log не попадает (он уходит в debug.log)
    assert "SQL SELECT: SELECT N" not in app_text


# ─────────────────────────────────────────────────────────────
# Шаги распознавания и действия UI в debug.log
# ─────────────────────────────────────────────────────────────

def test_recognition_steps_logged_without_pii(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=True)

    from core.recognizer import DataMapper

    # Реальные персональные данные на входе распознавания
    driver = DataMapper.map_driver_data(
        {
            "full_name": "Иванов Иван Иванович",
            "passport_series": "18 22",
            "passport_number": "926830",
            "registration_address": "г. Москва, ул. Тестовая, д. 1",
            "phone": "+7 (999) 123-45-67",
            "birth_date": "1980-01-01",
        }
    )
    assert driver["full_name"] == "Иванов Иван Иванович"

    debug_text = _read(logs_dir / logging_config.DEBUG_LOG_FILENAME)
    app_text = _read(logs_dir / logging_config.APP_LOG_FILENAME)

    # Шаг распознавания попал в оба лога…
    assert "Данные водителя распознаны" in app_text
    assert "Распознавание водителя: заполнено" in debug_text
    # …но значения персональных данных — нигде
    for leak in ("Иванов", "926830", "18 22", "Тестовая", "+7 (999)", "1980-01-01"):
        assert leak not in debug_text, f"утечка ПДн в debug.log: {leak}"
        assert leak not in app_text, f"утечка ПДн в app.log: {leak}"

    # Пустые поля перечислены именами (а не значениями)
    assert "пусто:" in debug_text


def test_ui_actions_logged_to_debug(logs_dir):
    logging_config.setup_logging(str(logs_dir), debug=True)

    from ui.main_window import MainWindow

    class FakeWindow:
        """Только метод логирования: Qt-окно в тестах не поднимаем."""

        _log_ui_action = MainWindow._log_ui_action

    FakeWindow()._log_ui_action("нажата кнопка «Создать договор»")
    FakeWindow()._log_ui_action("переключение вкладки", index=2, tab="Договор")

    debug_text = _read(logs_dir / logging_config.DEBUG_LOG_FILENAME)

    assert "UI: нажата кнопка «Создать договор»" in debug_text
    assert "UI: переключение вкладки | index=2 | tab=Договор" in debug_text

    # Действия UI — подробность: в app.log их нет
    app_text = _read(logs_dir / logging_config.APP_LOG_FILENAME)
    assert "UI: нажата кнопка" not in app_text


# ─────────────────────────────────────────────────────────────
# Связка main.py ↔ setup_logging (флаг --debug)
# ─────────────────────────────────────────────────────────────

def _import_main(monkeypatch, logs_dir, argv, calls):
    """Импортирует main.py с подменённым argv и перехваченным setup_logging."""
    import importlib
    import sys

    real_setup_logging = logging_config.setup_logging

    def spy(*args, **kwargs):
        calls.update(kwargs)
        real_setup_logging(str(logs_dir), **kwargs)

    monkeypatch.setattr(logging_config, "setup_logging", spy)
    monkeypatch.setattr(sys, "argv", argv)
    sys.modules.pop("main", None)

    module = importlib.import_module("main")
    sys.modules.pop("main", None)
    return module


def test_main_debug_flag_enables_debug_log(monkeypatch, logs_dir):
    calls = {}
    _import_main(monkeypatch, logs_dir, ["main.py", "--debug"], calls)

    assert calls.get("debug") is True
    assert (logs_dir / logging_config.DEBUG_LOG_FILENAME).exists()


def test_main_without_flag_keeps_debug_log_off(monkeypatch, logs_dir):
    calls = {}
    _import_main(monkeypatch, logs_dir, ["main.py"], calls)

    assert calls.get("debug") is False
    assert not (logs_dir / logging_config.DEBUG_LOG_FILENAME).exists()


def test_console_handler_survives_cp1251():
    """Русская консоль (cp1251) не должна ронять логирование на «×»/«✓»."""
    import io

    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1251")

    handler = logging_config.SafeConsoleHandler(stream=stream)
    handler.setFormatter(logging.Formatter("%(message)s"))

    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1,
        "Ротация: 5 МБ × 5, шаблон ✓ найден", None, None,
    )
    handler.emit(record)   # не должно бросить UnicodeEncodeError
    handler.flush()

    written = buffer.getvalue().decode("cp1251")
    assert "5 МБ" in written
    assert "×" not in written and "✓" not in written


# ─────────────────────────────────────────────────────────────
# GigaChat: время, статус, SLOW — и ни одного секрета/ПДн
# ─────────────────────────────────────────────────────────────

class _FakeResponse:
    """Ответ GigaChat с персональными данными внутри — их не должно быть в логе."""

    status_code = 200
    text = (
        '{"choices": [{"message": {"content": '
        '"{\\"driver\\": {\\"full_name\\": \\"Иванов Иван Иванович\\", '
        '\\"passport_number\\": \\"926830\\"}}"}}]}'
    )

    def json(self):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"driver": {"full_name": "Иванов Иван Иванович", '
                            '"passport_number": "926830"}}'
                        )
                    }
                }
            ]
        }


def test_gigachat_request_logged_without_secrets_or_pii(logs_dir, monkeypatch):
    logging_config.setup_logging(str(logs_dir), debug=True)

    import core.gigachat_client as giga

    secret_key = "c2VjcmV0LWtleS1mb3ItdGVzdA=="  # base64, как настоящий ключ

    monkeypatch.setattr(giga.requests, "post", lambda *a, **k: _FakeResponse())
    monkeypatch.setattr(giga.GigaChatClient, "_get_token", lambda self, force=False: "TOKEN-12345")

    client = giga.GigaChatClient(auth_key=secret_key, ca_bundle="")
    client.SLOW_REQUEST_SECONDS = 0.0   # любой ответ считается медленным

    parsed = client.recognize_text("Иванов Иван Иванович, паспорт 18 22 926830")
    assert parsed["driver"]["full_name"] == "Иванов Иван Иванович"

    debug_text = _read(logs_dir / logging_config.DEBUG_LOG_FILENAME)
    app_text = _read(logs_dir / logging_config.APP_LOG_FILENAME)
    for text in (debug_text, app_text):
        assert secret_key not in text
        assert "TOKEN-12345" not in text
        assert "Иванов" not in text
        assert "926830" not in text

    # Время, статус, объём и метка SLOW — на месте
    assert "GigaChat: ответ получен | статус=200" in debug_text
    assert "время=" in debug_text
    assert "длина ответа:" in debug_text
    assert "SLOW GigaChat" in app_text


# ─────────────────────────────────────────────────────────────
# Защита от ПДн в подробных логах
# ─────────────────────────────────────────────────────────────

def test_filled_fields_summary_has_no_values():
    summary = filled_fields_summary(
        {
            "full_name": "Иванов Иван Иванович",
            "passport_number": "926830",
            "phone": "",
        }
    )

    assert "Иванов" not in summary
    assert "926830" not in summary
    assert "заполнено 2 из 3" in summary
    assert "phone" in summary


def test_filled_fields_summary_handles_non_dict():
    assert filled_fields_summary(None).startswith("<")
    assert filled_fields_summary(["a", "b"]).startswith("<")
