#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проба выхода БЕЗ агента: приложение поднялось и вышло.

Пара к `exit_with_agent.py`. Нужна как земля: если падает и она, значит дело
не в агенте, а в самом выходе из программы. Именно так и оказалось — проба
дала 4–5 падений из 6 при выходе «на пустом месте» (см. докстроку соседнего
файла).

Как запускать:

    $env:QT_QPA_PLATFORM = "offscreen"
    foreach ($i in 1..10) {
        python tools/agent_exit_check/exit_min.py > $null; "$i -> $LASTEXITCODE"
    }

Ожидается `0` в каждой строке.
"""

import faulthandler
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DIAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faulthandler_min.log")
_stream = open(DIAG, "w", encoding="utf-8")
faulthandler.enable(file=_stream, all_threads=True)

from PyQt5.QtCore import QTimer  # noqa: E402
import PyQt5.QtWidgets as QtWidgets  # noqa: E402
from PyQt5.QtWidgets import QApplication, QDialog  # noqa: E402

import ui.contract_picker as picker  # noqa: E402


def _auto_accept(self):
    self.select_type("perevozka")
    return QDialog.Accepted


picker.ContractPickerDialog.exec_ = _auto_accept

import main  # noqa: E402

_captured = {}
_original_receiver = main._make_signals_receiver


def _capturing_receiver(manager, app):
    _captured["manager"] = manager
    return _original_receiver(manager, app)


main._make_signals_receiver = _capturing_receiver


def _exit():
    print("EXIT-CALLED", flush=True)
    main._do_exit(_captured["manager"], QApplication.instance())


class StoppingApplication(QApplication):
    def exec_(self):
        QTimer.singleShot(1000, _exit)
        return super().exec_()


QtWidgets.QApplication = StoppingApplication

sys.exit(main.main())
