#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Старт приложения: окно «База данных доступна только для чтения».

Почему это отдельный тест
-------------------------
Проверка прав на базу идёт при первом соединении (`init_database()` →
`db/connection.py`), ДО создания `QApplication`: там же причина пишется в
журнал уровнем ERROR. Но у собранного `.exe` консоли нет, а окно показывать
ещё нельзя — Qt не создан. Поэтому `main.py::_warn_if_database_readonly`
забирает причину уже после старта Qt (`db/write_access.take_startup_problem`)
и показывает одно окно на запуск.

Что проверяется:

1. проблем нет — окна нет (нормальный запуск ничего лишнего не показывает);
2. проблема есть — окно с заголовком и текстом-подсказкой показывается
   РОВНО один раз (второй вызов молчит: причина уже изъята);
3. текст окна — тот же, что ушёл в журнал (одна правда, не две).
"""

import importlib
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

# Корень проекта — по маркеру (pytest.ini), а не по числу parents.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class WarningRecorder:
    """Подмена QMessageBox.warning: помнит заголовок и текст, окно не показывает."""

    def __init__(self):
        self.calls = []

    def __call__(self, parent, title, text, *args, **kwargs):
        self.calls.append({"parent": parent, "title": title, "text": text})
        return QMessageBox.Ok


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def main_module(monkeypatch):
    """
    `main.py`, импортированный с подменённым argv и заглушкой логирования.

    Так же, как tests/unit/core/test_main_user_data.py: настоящая настройка
    логирования здесь не нужна, а глобальные хендлеры помешали бы остальным
    тестам. Модуль убирается из `sys.modules`: иначе следующий тест получил
    бы кэш с чужим `_ARGS`.
    """
    import config.logging_config as logging_config

    monkeypatch.setattr(logging_config, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    sys.modules.pop("main", None)

    module = importlib.import_module("main")
    sys.modules.pop("main", None)
    return module


@pytest.fixture(autouse=True)
def clean_startup_problem():
    """Запомненная причина не протекает между тестами."""
    from db import write_access

    write_access.reset_startup_problem()
    yield
    write_access.reset_startup_problem()


def test_no_problem_no_window(main_module, monkeypatch, qt_app):
    """Нормальный запуск: причины нет — окна нет."""
    recorder = WarningRecorder()
    monkeypatch.setattr(QMessageBox, "warning", recorder)

    assert main_module._warn_if_database_readonly() is False
    assert recorder.calls == []


def test_problem_is_shown_once(main_module, monkeypatch, qt_app):
    """Причина показывается ровно один раз и с заголовком окна."""
    from db import write_access

    message = write_access.readonly_message(
        r"C:\Users\Оператор\AppData\Roaming\LogisticsContractor\contracts.db",
        "проверка записи при старте: attempt to write a readonly database",
    )
    write_access.record_startup_problem(message)

    recorder = WarningRecorder()
    monkeypatch.setattr(QMessageBox, "warning", recorder)

    assert main_module._warn_if_database_readonly() is True
    assert main_module._warn_if_database_readonly() is False, (
        "второе окно за запуск не показывается"
    )

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["title"] == main_module.DATABASE_PROBLEM_TITLE
    assert call["title"] == "База данных доступна только для чтения"
    # Оператор видит шаги, а не сырой текст SQLite.
    assert "вторую копию" in call["text"]
    assert "Только чтение" in call["text"]
    assert call["text"] == message, "окно и журнал говорят одно и то же"
