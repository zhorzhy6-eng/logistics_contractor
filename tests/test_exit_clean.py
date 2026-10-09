#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты выхода из приложения без падения (шаг «диагностика падения при выходе»).

Что здесь проверяется и почему именно это:

  * `ui/qt_shutdown.wait_for_thread_pools` действительно ЖДЁТ задачи пула.
    Пул потоков — Qt-ребёнок окна: закрытие окна уничтожает и его, а задача
    (распознавание GigaChat, поиск DaData) в этот момент ещё работает. Поток
    остаётся с освобождённой памятью — это и есть падение `0xC0000005` при
    выходе. В тестах проекта ожидание стояло давно (`waitForDone(5000)` перед
    `force_close`), в самом приложении его не было;
  * `force_close()` окна ждёт свои пулы и отменяет текущее распознавание;
  * окно после `force_close()` закрыто по-настоящему (не спрятано);
  * отдельный процесс: `main.py` в offscreen-режиме выходит, ПОКА в пуле
    работает задача, и завершается кодом 0. Это тот самый сценарий, который
    падал плавающе («зависит от того, что делал пользователь до выхода»).
"""

import os
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import QRunnable, QThreadPool
from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget

from ui.qt_shutdown import (
    DEFAULT_TIMEOUT_MS,
    cancel_running_tasks,
    thread_pools_of,
    wait_for_thread_pools,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ─────────────────────────────────────────────────────────────
# Фикстуры и помощники
# ─────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def quiet_messages(monkeypatch):
    """Глушит модальные окна: в offscreen они вешают прогон."""
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))


class SlowTask(QRunnable):
    """
    Задача, которая живёт заданное время.

    Отмечается в общем списке `done` — по нему видно, успела ли она
    завершиться ДО того, как вернулось ожидание.
    """

    def __init__(self, seconds=0.3, done=None):
        super().__init__()
        self.seconds = seconds
        self.done = done if done is not None else []

    def run(self):
        time.sleep(self.seconds)
        self.done.append(True)


class _Task:
    """Заглушка задачи распознавания: помнит, что её отменили."""

    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _Holder:
    """Объект с задачей в атрибуте (как окно с recognition_task)."""

    def __init__(self, task):
        self.recognition_task = task


# ─────────────────────────────────────────────────────────────
# wait_for_thread_pools
# ─────────────────────────────────────────────────────────────
def test_thread_pools_of_finds_nested_pool(qt_app):
    """Пул находится и на самом объекте, и внутри его детей."""
    parent = QWidget()
    child = QWidget(parent)
    pool = QThreadPool(child)

    assert thread_pools_of(parent) == [pool]


def test_thread_pools_of_handles_none(qt_app):
    """Без объекта ждать нечего — и падать не на чем."""
    assert thread_pools_of(None) == []
    assert wait_for_thread_pools(None) == 0


def test_wait_for_thread_pools_waits_for_running_task(qt_app):
    """Ожидание не возвращается, пока задача пула не закончилась."""
    owner = QWidget()
    pool = QThreadPool(owner)
    done = []
    pool.start(SlowTask(0.3, done))

    waited = wait_for_thread_pools(owner, timeout_ms=5000)

    assert waited == 1
    assert done == [True], "ожидание вернулось раньше, чем задача завершилась"


def test_wait_for_thread_pools_skips_idle_pool(qt_app):
    """Пул без активных потоков не считается ожиданием."""
    owner = QWidget()
    QThreadPool(owner)

    assert wait_for_thread_pools(owner, timeout_ms=5000) == 0


def test_wait_for_thread_pools_reports_timeout(qt_app, caplog):
    """
    Превышение таймаута — предупреждение в журнал, а не исключение.

    Вечное ожидание на выходе хуже предупреждения: выход обязан состояться.
    """
    owner = QWidget()
    pool = QThreadPool(owner)
    pool.start(SlowTask(1.0))

    waited = wait_for_thread_pools(owner, timeout_ms=50)

    assert waited == 1
    assert any("не завершились" in record.getMessage() for record in caplog.records)
    assert pool.waitForDone(5000) is True


def test_default_timeout_matches_project_tests():
    """Таймаут по умолчанию — тот же, что в тестах проекта (5 с)."""
    assert DEFAULT_TIMEOUT_MS == 5000


# ─────────────────────────────────────────────────────────────
# cancel_running_tasks
# ─────────────────────────────────────────────────────────────
def test_cancel_running_tasks_cancels_task():
    """Задача распознавания помечается отменённой перед ожиданием пула."""
    task = _Task()
    holder = _Holder(task)

    assert cancel_running_tasks(holder, "recognition_task") == 1
    assert task.cancelled is True


def test_cancel_running_tasks_without_task_is_noop():
    """Нет задачи — нечего отменять, и это не ошибка."""
    holder = _Holder(None)

    assert cancel_running_tasks(holder, "recognition_task") == 0
    assert cancel_running_tasks(object(), "recognition_task") == 0


def test_cancel_running_tasks_survives_broken_cancel(caplog):
    """Сломанный cancel() не мешает выходу: пишем предупреждение и идём дальше."""

    class _Broken:
        def cancel(self):
            raise RuntimeError("имитация сбоя")

    holder = _Holder(_Broken())

    assert cancel_running_tasks(holder, "recognition_task") == 0
    assert any("Не удалось отменить" in record.getMessage() for record in caplog.records)


# ─────────────────────────────────────────────────────────────
# force_close() окон типов
# ─────────────────────────────────────────────────────────────
@pytest.fixture
def formika_window(qt_app, quiet_messages, isolated_db):
    """Свежее окно Формики (пул распознавания — его Qt-ребёнок)."""
    from ui.windows.formika import FormikaWindow

    window = FormikaWindow()
    yield window
    window.force_close()


def test_force_close_waits_for_running_task(formika_window):
    """
    `force_close()` дожидается задачи пула, а не бросает её.

    Раньше окно закрывалось сразу: пул разрушался вместе с окном, а поток
    продолжал работать с освобождённой памятью — отсюда плавающее падение.
    """
    done = []
    formika_window.thread_pool.start(SlowTask(0.4, done))
    assert formika_window.thread_pool.activeThreadCount() > 0

    formika_window.force_close()

    assert done == [True], "окно закрылось, не дождавшись своей задачи"


def test_force_close_closes_window_for_real(formika_window):
    """Окно после force_close() закрыто по-настоящему, а не спрятано."""
    formika_window.force_close()

    assert formika_window._force_close is True
    assert formika_window.isVisible() is False


def test_force_close_cancels_running_recognition(formika_window):
    """Текущее распознавание перед выходом помечается отменённым."""
    task = _Task()
    formika_window.recognition_task = task

    formika_window.force_close()

    assert task.cancelled is True


def test_main_window_force_close_waits_for_pool(qt_app, quiet_messages, isolated_db):
    """Главное окно «Экспедиторство» закрывается так же — дождавшись пула."""
    from ui.main_window import MainWindow

    window = MainWindow()
    try:
        done = []
        window.thread_pool.start(SlowTask(0.4, done))

        window.force_close()

        assert done == [True], "окно закрылось, не дождавшись своей задачи"
        assert window._force_close is True
    finally:
        window.thread_pool.waitForDone(5000)


def test_manager_close_all_waits_for_pools(qt_app, quiet_messages, isolated_db):
    """WindowManager.close_all() (путь кнопки «Выход») тоже ждёт пулы."""
    from ui.windows import WindowManager
    from ui.windows.formika import FormikaWindow

    manager = WindowManager({"formika": FormikaWindow})
    window = manager.switch_to("formika")
    done = []
    window.thread_pool.start(SlowTask(0.4, done))

    manager.close_all()

    assert done == [True], "менеджер закрыл окно, не дождавшись задачи"


# ─────────────────────────────────────────────────────────────
# Отдельный процесс: выход во время распознавания
# ─────────────────────────────────────────────────────────────
LAUNCH_SCRIPT = '''
# -*- coding: utf-8 -*-
"""main.py offscreen: выход, пока в пуле распознавания работает задача."""
import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5.QtCore import QTimer
import PyQt5.QtWidgets as QtWidgets
from PyQt5.QtWidgets import QApplication, QDialog

import ui.contract_picker as picker


def _auto_accept(self):
    self.select_type("perevozka")
    return QDialog.Accepted


picker.ContractPickerDialog.exec_ = _auto_accept

import main

_manager = {}
_original_receiver = main._make_signals_receiver


def _capturing_receiver(manager, app):
    _manager["value"] = manager
    return _original_receiver(manager, app)


main._make_signals_receiver = _capturing_receiver


class SlowClient:
    """GigaChat не нужен: ответ медленный, задача живёт в пуле при выходе."""

    def recognize_text(self, text, prompt=None):
        time.sleep(3.0)
        return {"driver": {}, "vehicles": [], "customer": {}, "carrier": {}}


def _exit():
    """Выход тем же путём, что кнопка «Выход»."""
    main._do_exit(_manager["value"], QApplication.instance())


def _start_then_exit():
    window = _manager["value"].current_window()
    window.gigachat = SlowClient()
    window._start_recognition("проба: выход во время распознавания")
    print("RECOGNITION-STARTED", flush=True)
    QTimer.singleShot(150, _exit)


class StoppingApplication(QApplication):
    """Выход по таймеру: таймер ставится, когда цикл событий уже создан."""

    def exec_(self):
        QTimer.singleShot(1200, _start_then_exit)
        return super().exec_()


QtWidgets.QApplication = StoppingApplication

sys.exit(main.main())
'''


def _run_launch_script(tmp_path) -> subprocess.CompletedProcess:
    path = tmp_path / "exit_while_busy_launch.py"
    path.write_text(LAUNCH_SCRIPT, encoding="utf-8")

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = PROJECT_ROOT

    return subprocess.run(
        [sys.executable, str(path)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def test_exit_while_recognition_runs_is_clean(tmp_path):
    """
    Выход во время распознавания: код 0 и никаких следов падения.

    Это главный сценарий шага. Раньше окно закрывалось, не дожидаясь задачи
    пула, — процесс падал плавающе (0xC0000005 в sip/Qt5Core).
    """
    try:
        result = _run_launch_script(tmp_path)
    except subprocess.TimeoutExpired:
        pytest.fail("приложение не завершилось само за 180 с — выход повис")

    combined = f"{result.stdout}\n{result.stderr}"
    assert "RECOGNITION-STARTED" in result.stdout, (
        f"задача распознавания не запустилась, сценарий не воспроизведён:\n{combined}"
    )
    assert result.returncode == 0, (
        f"выход во время распознавания завершился кодом {result.returncode} "
        f"(3221225477 = 0xC0000005):\n{combined}"
    )
    assert "Traceback" not in combined, combined
