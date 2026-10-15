#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Задача 1: выход из программы, когда перед выходом работал АГЕНТ.

Симптом (регресс): плавающее падение `0xC0000005` в
`sip.cp314-win_amd64.pyd` / `Qt5Core.dll` при выходе, если оператор успел
запустить распознавание (в том числе через панель Максимилиана).

ДИАГНОЗ (проверен A/B-пробой, а не догадка).

Что здесь проверяется:

  * **Пулов у агента нет вовсе.** Диагностика A.1–A.3: `QThreadPool` в
    проекте создаётся в четырёх местах (окно «Экспедиторство» и три окна
    типов — пул распознавания) и в `ui/tabs/base_tab.py` (поиск в DaData).
    Все они созданы как `QThreadPool(self)`, то есть Qt-дети окна, и
    `wait_for_thread_pools` их находит. **Пул агента не при чём**;
  * **виновник у агента — рабочий поток.** `AgentWidget._ensure_worker`
    создавал `QThread()` БЕЗ родителя: бесхозный поток держится только
    ссылкой Python, и Qt разрушал его вместе с обработчиком, пока тот ещё
    работал. Теперь поток — Qt-ребёнок виджета;
  * **потоки ждут там же, где пулы**: `wait_for_threads` в
    `ui/qt_shutdown.py` — та же дверь, что `wait_for_thread_pools`, для
    `QThread`;
  * **ГЛАВНАЯ ПРИЧИНА ПАДЕНИЯ — ЗАВЕРШЕНИЕ ПРОГРАММЫ, а не агент.**
    Проба `tools/agent_exit_check/exit_min.py` (приложение поднялось и
    сразу вышло, без агента и без распознавания) падала в 4–5 запусках
    из 6 — с тем же `0xC0000005`, уже ПОСЛЕ строки «Приложение закрыто:
    код=0». Падение жило в завершении интерпретатора: Qt и Python
    разрушали одни и те же объекты в разном порядке. Лечится явным
    выходом процессом после закрытия окон (`main._finish_process`).
    Тест `test_exit_is_clean_without_any_activity` проверяет этот случай
    отдельным процессом, `test_exit_after_agent_request_is_clean` —
    сценарий из задания.

Тесты идут в offscreen; сети нет — модель подставная (`SlowClient`).
Пробы в `tools/agent_exit_check/` запускаются вручную (счёт по запускам:
падение плавающее, одной попыткой его не поймать).
"""

import os
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import QThread
from PyQt5.QtWidgets import QApplication, QWidget

from ui.qt_shutdown import threads_of, wait_for_threads

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ─────────────────────────────────────────────────────────────
# Фикстуры и помощники
# ─────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


class _Holder:
    """Объект с задачей в атрибуте (как окно с recognition_task)."""

    def __init__(self, task=None):
        self.recognition_task = task


# ─────────────────────────────────────────────────────────────
# wait_for_threads: та же дверь, что wait_for_thread_pools
# ─────────────────────────────────────────────────────────────
def test_threads_of_finds_nested_thread(qt_app):
    """Поток находится и на самом объекте, и внутри его детей."""
    parent = QWidget()
    child = QWidget(parent)
    thread = QThread(child)
    thread.setObjectName("probeThread")

    assert threads_of(parent) == [thread]


def test_threads_of_handles_none(qt_app):
    """Без объекта ждать нечего — и падать не на чем."""
    assert threads_of(None) == []
    assert wait_for_threads(None) == 0


def test_wait_for_threads_waits_for_running_thread(qt_app):
    """
    Ожидание не возвращается, пока поток не закончился.

    Поток здесь настоящий: он спит в `run()` и отмечается в списке `done`.
    """
    from PyQt5.QtCore import QThread as _QThread

    parent = QWidget()
    done = []

    class Sleeper(_QThread):
        def run(self):
            time.sleep(0.3)
            done.append(True)

    thread = Sleeper(parent)
    thread.start()

    waited = wait_for_threads(parent, timeout_ms=5000)

    assert waited == 1
    assert done == [True], "ожидание вернулось раньше, чем поток завершился"


def test_wait_for_threads_skips_finished_thread(qt_app):
    """
    Завершённый поток ожиданием не считается.

    Поток берётся короткоживущий (свой `run`): у голого `QThread` в `run`
    крутится цикл событий, и он не завершится сам.
    """
    from PyQt5.QtCore import QThread as _QThread

    parent = QWidget()

    class Quick(_QThread):
        def run(self):
            return

    thread = Quick(parent)
    thread.start()
    assert thread.wait(5000) is True

    assert wait_for_threads(parent, timeout_ms=5000) == 0


def test_wait_for_threads_reports_timeout(qt_app, caplog):
    """Превышение таймаута — предупреждение в журнал, а не исключение."""
    from PyQt5.QtCore import QThread as _QThread

    parent = QWidget()

    class Sleeper(_QThread):
        def run(self):
            time.sleep(1.0)

    thread = Sleeper(parent)
    thread.start()

    waited = wait_for_threads(parent, timeout_ms=50)

    assert waited == 1
    assert any("не завершился" in record.getMessage() for record in caplog.records)
    assert thread.wait(5000) is True


# ─────────────────────────────────────────────────────────────
# Поток агента: Qt-ребёнок виджета, а не «бесхозный»
# ─────────────────────────────────────────────────────────────
@pytest.fixture
def agent_widget(qt_app, isolated_db, monkeypatch):
    """
    Настоящая панель Максимилиана в настоящем окне «Экспедиторство».

    Настройки — изолированные; помощник включён, чтобы виджет создался
    полностью (иконка, панель).
    """
    from ui.main_window import MainWindow

    window = MainWindow()
    widget = getattr(window, "agent_widget", None)
    if widget is None:
        pytest.skip("Максимилиан не установлен в это окно")

    yield window, widget

    widget.shutdown()
    try:
        window.force_close()
    except RuntimeError:
        pass


def test_agent_thread_has_parent(agent_widget):
    """
    Рабочий поток агента — Qt-ребёнок виджета.

    Бесхозный `QThread()` держится только ссылкой Python: сборщик мусора
    уничтожает его вместе с `AgentWorker`, пока поток ещё работает, — это и
    есть падение при выходе (`0xC0000005`). Родитель делает время жизни
    потока временем жизни виджета и попадает в дерево `findChildren`.
    """
    _window, widget = agent_widget
    widget._ensure_worker()
    thread = widget._thread

    assert thread is not None
    assert thread.parent() is widget, "у потока агента нет родителя"
    assert thread in threads_of(widget), "поток не виден через findChildren"


def test_agent_thread_is_found_from_window(agent_widget):
    """Поток агента видно и от окна: одного дерева достаточно."""
    window, widget = agent_widget
    widget._ensure_worker()

    assert widget._thread in threads_of(window)


def test_shutdown_stops_agent_thread(agent_widget):
    """`shutdown()` останавливает поток и не оставляет его висеть."""
    _window, widget = agent_widget
    widget._ensure_worker()
    thread = widget._thread

    widget.shutdown()

    assert widget._thread is None
    assert thread.isRunning() is False


def test_wait_for_threads_stops_agent_thread(agent_widget):
    """
    Ожидание потоков из `qt_shutdown` работает и для потока агента.

    Это страховка на выходе: если окно закрывают не через `shutdown()`
    (например, разрушением родителя), поток всё равно дожидается.
    """
    window, widget = agent_widget
    widget._ensure_worker()

    assert wait_for_threads(window, timeout_ms=5000) == 1
    assert widget._thread.isRunning() is False


# ─────────────────────────────────────────────────────────────
# ГЛАВНОЕ: выход во время запроса к агенту (отдельный процесс)
# ─────────────────────────────────────────────────────────────
LAUNCH_SCRIPT = r'''
# -*- coding: utf-8 -*-
"""main.py offscreen: запрос к Максимилиану и выход, не дожидаясь ответа."""

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
    """GigaChat не нужен: модель «думает», запрос живёт дольше выхода."""

    def complete_chat(self, messages=None, tools=None, temperature=0.2):
        time.sleep(5.0)
        return {"role": "assistant", "content": "Готово.", "tool_calls": []}


def _exit():
    main._do_exit(_manager["value"], QApplication.instance())


def _start_then_exit():
    """Выход тем же путём, что кнопка «Выход» в окне."""
    window = _manager["value"].current_window()
    widget = getattr(window, "agent_widget", None)
    scenario = os.environ.get("PROBE_SCENARIO", "agent")

    if scenario == "agent" and widget is not None:
        widget._client_factory = SlowClient
        widget.show_panel()
        widget.on_submit("Покажи, что открыто")
        print("AGENT-REQUESTED", flush=True)
    else:
        print("NOTHING-STARTED", flush=True)

    # Ссылку на окно НЕ удерживаем: именно так окно и уходит сборщику
    # мусора после закрытия — на этом старый код падал.
    QTimer.singleShot(250, _exit)


class StoppingApplication(QApplication):
    def exec_(self):
        QTimer.singleShot(1200, _start_then_exit)
        return super().exec_()


QtWidgets.QApplication = StoppingApplication

sys.exit(main.main())
'''


def _run_exit_scenario(tmp_path, scenario: str, expected_mark: str):
    """
    Запускает `main.py` отдельным процессом и возвращает его результат.

    Отдельный процесс обязателен: проверяется именно код завершения
    процесса, а `QApplication` — синглтон (второй в одном процессе не
    поднять).
    """
    script = tmp_path / f"exit_scenario_{scenario}.py"
    script.write_text(LAUNCH_SCRIPT, encoding="utf-8")

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = PROJECT_ROOT
    env["PROBE_SCENARIO"] = scenario

    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("приложение не завершилось само за 180 с — выход повис")

    combined = f"{result.stdout}\n{result.stderr}"
    assert expected_mark in result.stdout, (
        f"сценарий не воспроизведён ({expected_mark}):\n{combined}"
    )
    assert result.returncode == 0, (
        f"выход завершился кодом {result.returncode} "
        f"(3221225477 = 0xC0000005):\n{combined}"
    )
    assert "Traceback" not in combined, combined
    return result


def test_exit_after_agent_request_is_clean(tmp_path):
    """
    Выход, пока агент ещё думает: код 0 и никаких следов падения.

    Регресс: перед выходом оператор успел спросить Максимилиана — процесс
    падал `0xC0000005` в sip/Qt5Core.
    """
    _run_exit_scenario(tmp_path, "agent", "AGENT-REQUESTED")


def test_exit_is_clean_without_any_activity(tmp_path):
    """
    Выход «на пустом месте»: ничего не запускали, просто закрыли.

    Именно этот случай и оказался главным: проба
    `tools/agent_exit_check/exit_min.py` падала в 4–5 запусках из 6 БЕЗ
    агента и без распознавания. Падение жило в завершении интерпретатора
    (Qt и Python разрушали одни и те же объекты в разном порядке), а не в
    пулах и не в потоке агента.
    """
    _run_exit_scenario(tmp_path, "none", "NOTHING-STARTED")
