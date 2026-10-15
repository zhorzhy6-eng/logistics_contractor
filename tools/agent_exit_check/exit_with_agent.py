#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проба выхода из программы: с агентом, с распознаванием и «на пустом месте».

Зачем эта папка. Падение при выходе (`0xC0000005` в `sip.cp314-win_amd64.pyd`
или `Qt5Core.dll`) плавающее: оно зависит от того, что успело произойти до
выхода. Проверять такое одной командой нельзя — нужен счёт по запускам,
иначе вывод будет случайным. Проба запускает приложение целиком (offscreen),
выходит тем же путём, что кнопка «Выход», и печатает код выхода процесса.

Как запускать (из корня проекта):

    $env:QT_QPA_PLATFORM = "offscreen"
    foreach ($m in @("none","panel","thread","agent","reclong")) {
        $env:PROBE_MODE = $m
        foreach ($i in 1..10) {
            python tools/agent_exit_check/exit_with_agent.py > $null
            "$m $i -> $LASTEXITCODE"
        }
    }

Ожидается `0` в каждой строке. Код `-1073741819` (он же `3221225477`,
он же `0xC0000005`) — то самое падение.

Режимы (`PROBE_MODE`):

  * `none`    — приложение поднялось и сразу вышло (земля для сравнения);
  * `panel`   — панель Максимилиана открыта, запроса не было;
  * `thread`  — рабочий поток агента создан, запроса не было;
  * `agent`   — ЗАПРОС к агенту, модель отвечает через `PROBE_SLEEP` секунд
                (по умолчанию 5), выход через `PROBE_DELAY` мс (300);
  * `reclong` — распознавание документа: медленный клиент GigaChat.

Что было найдено этой пробой (задача 1, коммит с фиксом выхода):

  * падало НЕ только с агентом: `none` и `panel` падали в 4–5 запусках из 6.
    То есть дело не в пулах и не в потоке агента, а в завершении программы;
  * падение случалось ПОСЛЕ штатного выхода: строка «Приложение закрыто:
    код=0» в журнале есть, окна закрыты, живых потоков Python — ноль;
  * причина — завершение интерпретатора: Qt и Python разрушают одни и те же
    объекты в разном порядке. Явный выход процессом после закрытия окон
    (`main._finish_process`) убирает падение полностью: `0` в 10 запусках
    из 10 в каждом режиме.
"""

import faulthandler
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: Дамп стека при падении ниже уровня Python — единственный след аварии.
DIAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faulthandler.log")
_stream = open(DIAG, "w", encoding="utf-8")
faulthandler.enable(file=_stream, all_threads=True)

PROBE_MODE = os.environ.get("PROBE_MODE", "agent")
PROBE_SLEEP = float(os.environ.get("PROBE_SLEEP", "5.0"))
PROBE_DELAY = int(os.environ.get("PROBE_DELAY", "300"))

from PyQt5.QtCore import QTimer  # noqa: E402
import PyQt5.QtWidgets as QtWidgets  # noqa: E402
from PyQt5.QtWidgets import QApplication, QDialog  # noqa: E402

import ui.contract_picker as picker  # noqa: E402


def _auto_accept(self):
    """Тип договора выбирается сам: проба идёт без оператора."""
    self.select_type("perevozka")
    return QDialog.Accepted


picker.ContractPickerDialog.exec_ = _auto_accept

import main  # noqa: E402

#: Менеджер окон нужен, чтобы выйти тем же путём, что кнопка «Выход».
_captured = {}
_original_receiver = main._make_signals_receiver


def _capturing_receiver(manager, app):
    _captured["manager"] = manager
    return _original_receiver(manager, app)


main._make_signals_receiver = _capturing_receiver


class SlowClient:
    """GigaChat не нужен: модель «думает» заданное время."""

    def __init__(self, seconds):
        self.seconds = seconds

    def complete_chat(self, messages=None, tools=None, temperature=0.2):
        import time

        time.sleep(self.seconds)
        return {"role": "assistant", "content": "Готово.", "tool_calls": []}

    def recognize_text(self, text, prompt=None):
        import time

        time.sleep(self.seconds)
        return {"driver": {}, "vehicles": [], "customer": {}, "carrier": {}}


def _make_client():
    return SlowClient(PROBE_SLEEP)


def _exit():
    """Выход тем же путём, что кнопка «Выход» в окне."""
    print("EXIT-CALLED", flush=True)
    main._do_exit(_captured["manager"], QApplication.instance())


def _go():
    window = _captured["manager"].current_window()
    widget = getattr(window, "agent_widget", None)
    print(f"WIDGET={widget is not None}", flush=True)

    if PROBE_MODE == "panel":
        widget.show_panel()
        print("PANEL-ONLY", flush=True)
    elif PROBE_MODE == "thread":
        widget._ensure_worker()
        print("THREAD-ONLY", flush=True)
    elif PROBE_MODE == "agent":
        widget._client_factory = _make_client
        widget.show_panel()
        widget.on_submit("Покажи, что открыто")
        print("AGENT-REQUESTED", flush=True)
    elif PROBE_MODE == "reclong":
        window.gigachat = SlowClient(PROBE_SLEEP)
        window._start_recognition("проба: выход во время распознавания")
        print("RECOGNITION-STARTED", flush=True)
    else:
        print("NOTHING", flush=True)

    QTimer.singleShot(PROBE_DELAY, _exit)


class StoppingApplication(QApplication):
    """Выход по таймеру: он ставится, когда цикл событий уже создан."""

    def exec_(self):
        QTimer.singleShot(1200, _go)
        return super().exec_()


QtWidgets.QApplication = StoppingApplication

sys.exit(main.main())
