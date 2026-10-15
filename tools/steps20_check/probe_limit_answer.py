#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ЖИВАЯ ПРОВЕРКА ПРЕДОХРАНИТЕЛЯ: что оператор видит, когда лимит исчерпан.

Зачем отдельная проба. Живой запрос из задания («Куда тыкать? Мне нужно
внести данные по организации») выполняется ЦЕЛИКОМ за один вызов инструмента
— лимит шагов его не задевает, и в живом прогоне человеческий ответ по
лимиту не увидеть (это и есть цель правки). Поэтому здесь тот же ПУТЬ
ОПЕРАТОРА (настоящее окно «Экспедиторство», настоящие инструменты, настоящая
панель), но модель подставная: она упорно зовёт один и тот же инструмент,
как это делает GigaChat, когда «застревает».

Проверяется ровно одно: при исчерпании лимита оператор получает понятный
ответ с выбором «что сделать первым», а не отладочный дамп.

    python tools/steps20_check/probe_limit_answer.py

Копию базы готовит `live_check_steps.py`; если её нет, проба пойдёт по
рабочей базе (об этом пишется предупреждение в журнал).
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "tests" / "_tmp" / "_steps_20"
SHOT_DIR = OUT_DIR / "shots"

QUERY = "Покажи всё, что есть по организации"

HELP_LINES = (
    "Задача большая — не успел за один запрос. Давай по частям.",
    "Что сделать первым:",
    "• Найти организацию в базе?",
    "• Заполнить форму?",
    "• Проверить в ЕГРЮЛ?",
)


def log(message: str) -> None:
    """Строка журнала пробы."""
    line = f"{datetime.now().strftime('%H:%M:%S')} {message}"
    print(line, flush=True)
    with open(OUT_DIR / "probe.log", "a", encoding="utf-8") as file:
        file.write(line + "\n")


class LoopingClient:
    """
    Подставная модель: на каждый шаг зовёт один и тот же инструмент.

    Так ведёт себя настоящий GigaChat, когда «застревает»: он снова и снова
    просит одно и то же действие, и без предохранителя запрос шёл бы до
    конца лимита (а до правки — упирался в три шага и отвечал отладочным
    дампом).
    """

    model = "stub-looping-model"

    def __init__(self, tool: str = "get_open_tabs"):
        self.tool = tool
        self.calls = 0

    def complete_chat(self, messages, tools=None, temperature=0.2):
        self.calls += 1
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"call_{self.calls}",
                "type": "function",
                "function": {"name": self.tool, "arguments": "{}"},
            }],
        }


def setup_font() -> None:
    """Системный шрифт для снимков (в offscreen Qt своих шрифтов не находит)."""
    from PyQt5.QtGui import QFontDatabase

    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for name in ("segoeui.ttf", "arial.ttf", "tahoma.ttf"):
        candidate = windir / "Fonts" / name
        if candidate.is_file():
            QFontDatabase.addApplicationFont(str(candidate))
            return


def main() -> int:
    from PyQt5.QtWidgets import QApplication, QMessageBox

    from ui.agent_widget import install_agent
    from ui.main_window import MainWindow

    (OUT_DIR / "probe.log").write_text("", encoding="utf-8")
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    #: База — КОПИЯ: помощник пишет туда алиасы, рабочий файл оператора не
    #: трогаем. Подмена та же, что у тестов: db.database.DB_PATH. Копию
    #: делает _live_check_steps.py; если её нет — работаем как есть.
    from db import database

    work_db = OUT_DIR / "_steps20_contracts.db"
    if work_db.is_file():
        database.DB_PATH = str(work_db.resolve())
        log(f"подмена базы: db.database.DB_PATH = {database.DB_PATH}")
    else:
        log("ВНИМАНИЕ: копии базы нет — проба пойдёт по рабочей базе")

    log("=" * 74)
    log("ПРОБА: человеческий ответ, когда лимит шагов исчерпан")
    log("=" * 74)

    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    MainWindow._init_gigachat_client = lambda self, show_dialog=True: False

    app = QApplication.instance() or QApplication([])
    setup_font()

    window = MainWindow()
    window.resize(1680, 1000)
    window.show()
    for _ in range(30):
        app.processEvents()

    widget = install_agent(window, auto_confirm=True, open_on_start=True)
    if widget is None:
        log("FAIL: Максимилиан выключен настройками")
        return 2
    widget.auto_confirm = True
    widget._ensure_worker()
    if widget._agent is None:
        widget._load_history(20, 20)

    agent = widget._agent
    from core.agent.llm import SafeChat

    agent.chat = SafeChat(LoopingClient())
    log(f"агент: шагов={agent.max_tool_steps}, "
        f"таймаут={agent.max_request_seconds} с, "
        f"память={agent.max_tool_steps}")

    widget.panel.show()
    for _ in range(15):
        app.processEvents()

    log(f"запрос: {QUERY!r}")
    started = datetime.now()
    reply = agent.send(QUERY)
    log(f"ответ получен за {(datetime.now() - started).total_seconds():.1f} с")
    log(f"причина остановки: {reply.stop_reason!r}, шагов: {reply.steps}")
    log(f"вызовов инструментов: {len(reply.calls)}")
    log(f"текст ответа ({len(reply.text)} символов):")
    for line in reply.text.splitlines():
        log(f"    | {line}")

    #: Панель показывает тот же ответ, что получил бы оператор.
    widget.panel.append_message(QUERY, "user")
    widget.panel.append_message(reply.text, "agent", reply.tool_names)
    widget.panel.set_status("Остановился — уточните, с чего начать", False)
    #: Панель побольше: длинный ответ не должен упираться в нижний край,
    #: иначе последний вариант выбора остаётся за кадром.
    widget.panel.resize(520, 1180)
    for _ in range(25):
        app.processEvents()
    time.sleep(0.3)
    for _ in range(25):
        app.processEvents()
    shot = SHOT_DIR / "05_limit_answer_panel.png"
    widget.panel.grab().save(str(shot))
    log(f"  снимок: панель {widget.panel.width()}x{widget.panel.height()} "
        f"→ {shot.name}")
    shot_window = SHOT_DIR / "05_limit_answer_window.png"
    window.grab().save(str(shot_window))

    checks = []
    for name, ok in (
        ("остановлен предохранителем", bool(reply.step_limit_reached)),
        ("причина — лимит шагов", reply.stop_reason == "steps"),
        ("текст не пустой", bool(reply.text.strip())),
        ("есть предложение разбить задачу", HELP_LINES[0] in reply.text),
        ("есть выбор первого шага", HELP_LINES[1] in reply.text),
        ("предложены все три варианта",
         all(line in reply.text for line in HELP_LINES[2:])),
        ("нет «Что успел узнать»", "Что успел узнать" not in reply.text),
        ("нет технических идентификаторов",
         not any(item in reply.text for item in
                 ("window=", "contract_type", "perevozka", "get_open_tabs",
                  "carrier_tab", "{", "}"))),
        ("нет машинного имени инструмента", "get_open_tabs" not in reply.text),
    ):
        checks.append({"check": name, "ok": bool(ok)})
        log(f"  [{'OK  ' if ok else 'FAIL'}] {name}")

    passed = sum(1 for item in checks if item["ok"])
    (OUT_DIR / "probe.json").write_text(json.dumps({
        "query": QUERY,
        "stop_reason": reply.stop_reason,
        "steps": reply.steps,
        "calls": len(reply.calls),
        "text": reply.text,
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "screenshot": str(shot),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    log("=" * 74)
    log(f"ИТОГ: {passed} из {len(checks)} проверок пройдено")
    log("=" * 74)

    try:
        widget.shutdown()
    except Exception:  # noqa: BLE001
        pass
    for _ in range(20):
        app.processEvents()
    window.close()
    for _ in range(20):
        app.processEvents()
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
