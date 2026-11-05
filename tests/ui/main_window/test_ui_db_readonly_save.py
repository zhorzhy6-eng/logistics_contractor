#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сохранение в базу: понятное объяснение вместо сырого текста SQLite.

Дефект (v1.4.1): при файле базы с атрибутом «только чтение» оператор видел
в окне сохранения техническую строку «attempt to write a readonly
database» — и не знал, что делать. Теперь диалог объясняет причину словами
и предлагает шаги: закрыть вторую копию программы, снять «Только чтение»,
добавить папку данных в исключения антивируса.

Проверяется именно ТЕКСТ диалога, который увидит оператор: ошибка
подставляется в `save_contract_with_details` (реальную запись не ломаем),
форма пустая — до базы дело не доходит.
"""

import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtCore import QEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

# Корень проекта — по маркеру (pytest.ini), а не по числу parents.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class CriticalRecorder:
    """Подмена QMessageBox.critical: помнит тексты, окно не показывает."""

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
def window(qt_app, isolated_db, monkeypatch):
    """Главное окно «Экспедиторства» без GigaChat и без блокирующих окон."""
    from ui.main_window import MainWindow
    from ui.windows.base_window import clear_source_windows

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    win = MainWindow()
    yield win

    try:
        win.system_theme_watcher.stop()
    except Exception:  # noqa: BLE001 — наблюдателя может не быть вовсе
        pass
    win.force_close()
    win.deleteLater()
    app = QApplication.instance()
    if app is not None:
        app.sendPostedEvents(None, QEvent.DeferredDelete)
    clear_source_windows()


@pytest.fixture
def failing_save(monkeypatch):
    """Ошибка сохранения: реальная запись в базу подменена исключением."""

    def _install(error):
        from ui.main_window import save as save_module

        def boom(*args, **kwargs):
            raise error

        monkeypatch.setattr(save_module, "save_contract_with_details", boom)
        recorder = CriticalRecorder()
        monkeypatch.setattr(QMessageBox, "critical", recorder)
        return recorder

    return _install


# ─────────────────────────────────────────────────────────────
# 1. База «только для чтение»
# ─────────────────────────────────────────────────────────────

def test_readonly_error_explains_what_to_do(window, failing_save):
    """Вместо «attempt to write a readonly database» — шаги для оператора."""
    recorder = failing_save(
        sqlite3.OperationalError("attempt to write a readonly database")
    )

    window._on_save_to_db()

    assert recorder.calls, "окно ошибки не показано"
    text = recorder.calls[-1]["text"]
    assert "Не удалось сохранить данные" in text
    assert "только для чтения" in text.lower()
    assert "вторую копию" in text
    assert "Только чтение" in text
    assert "антивирус" in text
    # Техническая причина не выброшена: по ней ищут в журнале и в интернете.
    assert "attempt to write a readonly database" in text


def test_readonly_error_names_the_database(window, failing_save):
    """Оператор видит, ГДЕ лежит база: в %APPDATA% он искать не догадается."""
    recorder = failing_save(
        sqlite3.OperationalError("attempt to write a readonly database")
    )

    window._on_save_to_db()

    text = recorder.calls[-1]["text"]
    assert "Файл базы" in text
    assert "contracts.db" in text


# ─────────────────────────────────────────────────────────────
# 2. Занятая база и прочие ошибки
# ─────────────────────────────────────────────────────────────

def test_locked_database_points_to_second_copy(window, failing_save):
    """«database is locked» — подсказка про вторую копию программы."""
    recorder = failing_save(sqlite3.OperationalError("database is locked"))

    window._on_save_to_db()

    text = recorder.calls[-1]["text"]
    assert "занята другой программой" in text
    assert "вторую копию" in text


def test_other_error_keeps_technical_text(window, failing_save):
    """Прочие ошибки не переписываются: прежний текст плюс причина."""
    recorder = failing_save(sqlite3.OperationalError("disk I/O error"))

    window._on_save_to_db()

    text = recorder.calls[-1]["text"]
    assert text == "Не удалось сохранить данные:\ndisk I/O error"
