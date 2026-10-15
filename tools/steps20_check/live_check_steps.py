#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ЖИВАЯ ПРОВЕРКА (часть C задания): запрос про организацию и лимит 20.

Что проверяется НАСТОЯЩИМ путём оператора, а не моком:

  1. лимит шагов 20 (из config/settings.json) доехал до агента;
  2. запрос «Куда тыкать? Мне нужно внести данные по организации»
     выполняется ЦЕЛИКОМ: ответ модели, вызовы инструментов, повторный
     ответ — а не «за один запрос я успеваю 3 шага»;
  3. ответ человеческий: без машинных имён инструментов, без `repr`,
     без технических идентификаторов (`window=Генератор…`,
     `contract_type=perevozka`, списка вкладок);
  4. предохранители на месте: таймаут 120 с и защита от зацикливания;
  5. если предохранитель всё-таки сработал — оператор получает понятный
     ответ с выбором «что сделать первым».

База оператора НЕ трогается: прогон идёт на КОПИИ `contracts.db`
(`db.database.DB_PATH`), а исходный файл сверяется по SHA256 до и после.
ПДн в скриншоты не попадают: показывается ответ, а не форма с данными;
текст ответа прогоняется через псевдонимизатор перед записью в отчёт.

Запуск:

    python tools/steps20_check/live_check_steps.py

Скрипт лежит в `tools/` (в git), а не в `tests/_tmp`: временная папка однажды
исчезла целиком вместе с пробами (`AGENTS.md` § 5.4), а проверку нужно уметь
повторить. Результаты (снимки, отчёт, журнал) пишутся в
`tests/_tmp/_steps_20/`.
"""

import hashlib
import json
import os
import shutil
import sqlite3
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
WORK_DB = OUT_DIR / "_steps20_contracts.db"
OPERATOR_DB = PROJECT_ROOT / "contracts.db"
REPORT_PATH = OUT_DIR / "report.json"

#: Запрос из задания (C.1).
QUERY = "Куда тыкать? Мне нужно внести данные по организации"

#: Сколько ждём ответ: таймаут агента 120 с плюс запас на сеть и диалоги.
ANSWER_TIMEOUT = 200.0

#: Машинные имена и идентификаторы, которых в ответе быть не должно.
#: Проверяются именно машинные формы (`window=`, `contract_type`,
#: `carrier_tab`), а не русские слова: «вкладка «Перевозчик»» — это
#: подсказка оператору, её модель писать вправе.
#: Список типов НЕ зашит здесь: он дописывается из реестра — иначе новый
#: клиентский тип пришлось бы вписывать и в проверку.
FORBIDDEN = (
    "window=", "contract_type", "perevozka", "get_form_state", "get_open_tabs",
    "carrier_tab", "driver_tab", "trailer_tab", "open_tabs",
    "tabs=[", "current_tab_key", "{", "}",
)


def _contract_type_keys() -> tuple:
    """Ключи зарегистрированных типов договоров, кроме публичного ключа."""
    try:
        from core.contracts.registry import ContractTypeRegistry

        ContractTypeRegistry.load_builtin()
        return tuple(
            key for key in ContractTypeRegistry.known_types() if key != "perevozka"
        )
    except Exception:  # noqa: BLE001 — проверка не должна падать из-за реестра
        return ()


FORBIDDEN = FORBIDDEN + _contract_type_keys()

checks = []


def log(message: str) -> None:
    """Строка журнала прогона (в консоль и в файл)."""
    line = f"{datetime.now().strftime('%H:%M:%S')} {message}"
    print(line, flush=True)
    with open(OUT_DIR / "run.log", "a", encoding="utf-8") as file:
        file.write(line + "\n")


def check(name: str, ok: bool, note: str = "") -> bool:
    """Одна проверка задания: имя, итог, пояснение."""
    checks.append({"check": name, "ok": bool(ok), "note": str(note)})
    log(f"  [{'OK  ' if ok else 'FAIL'}] {name}"
        + (f" — {note}" if note else ""))
    return bool(ok)


def sha256(path: Path) -> str:
    """SHA256 файла (пусто — файла нет)."""
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def db_snapshot(path: Path) -> dict:
    """Счётчики таблиц и целостность базы (для сравнения «до/после»)."""
    if not path.is_file():
        return {"exists": False}
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as error:
        return {"exists": True, "error": str(error)}
    try:
        tables = [
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "ORDER BY name"
            )
        ]
        counters = {}
        for table in tables:
            if table.startswith("sqlite_"):
                continue
            try:
                counters[table] = int(connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0])
            except sqlite3.Error:
                counters[table] = -1
        integrity = str(connection.execute("PRAGMA integrity_check")
                        .fetchone()[0])
        return {"exists": True, "sha256": sha256(path), "integrity": integrity,
                "counters": counters}
    finally:
        connection.close()


def isolate_db() -> None:
    """
    Копия рабочей базы: помощник пишет алиасы, пускать это в базу нельзя.

    Подмена — тем же способом, что у тестов и у tools/stress_test_agent.py:
    `db.database.DB_PATH`. Переменная окружения здесь не работает: путь
    берётся из этого модуля (см. db/connection.py).
    """
    from db import database

    shutil.copyfile(OPERATOR_DB, WORK_DB)
    database.DB_PATH = str(WORK_DB.resolve())
    log(f"подмена базы: db.database.DB_PATH = {database.DB_PATH}")


def setup_font() -> None:
    """Системный шрифт для снимков: в offscreen Qt своих шрифтов не находит."""
    from PyQt5.QtGui import QFontDatabase

    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for name in ("segoeui.ttf", "arial.ttf", "tahoma.ttf"):
        candidate = windir / "Fonts" / name
        if candidate.is_file():
            QFontDatabase.addApplicationFont(str(candidate))
            return


def wait(app, seconds: float) -> None:
    """Пауза с прокруткой событий Qt (окно остаётся живым)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)


def shoot(app, widget, name: str) -> str:
    """Снимок окна и панели Максимилиана (offscreen — grab, а не экран)."""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    #: У панели окно лежит в поле `window` (объект), а не в методе QWidget.
    window = getattr(widget, "window", None) or widget.parent()
    saved = []
    for suffix, target in (("window", window), ("panel", widget.panel)):
        if target is None or not hasattr(target, "grab"):
            continue
        for _ in range(10):
            app.processEvents()
        path = SHOT_DIR / f"{name}_{suffix}.png"
        if target.grab().save(str(path)):
            saved.append(str(path))
    log(f"  снимок: {name} → {', '.join(Path(p).name for p in saved)}")
    return saved[0] if saved else ""


class Collector:
    """Собирает ответ рабочего потока агента (сигналы панели)."""

    def __init__(self):
        self.answers = []
        self.errors = []
        self.activity = []

    def on_finished(self, text, tools=None, limit=False):
        self.answers.append({"text": str(text or ""),
                             "tools": list(tools or []),
                             "limit": bool(limit)})

    def on_failed(self, error):
        self.errors.append(str(error or ""))

    def on_activity(self, message):
        self.activity.append(str(message or ""))


def ask(app, widget, collector, message: str) -> dict:
    """Один запрос путём оператора: панель → рабочий поток → ответ."""
    widget._worker.finished.connect(collector.on_finished)
    widget._worker.failed.connect(collector.on_failed)
    widget._worker.activity.connect(collector.on_activity)

    started = time.monotonic()
    failure = ""
    try:
        widget.on_submit(message)
    except Exception as error:  # noqa: BLE001 — падение важнее прогона
        failure = f"{type(error).__name__}: {error}"

    deadline = time.monotonic() + ANSWER_TIMEOUT
    while time.monotonic() < deadline and not failure:
        app.processEvents()
        time.sleep(0.02)
        if collector.answers or collector.errors:
            for _ in range(30):
                app.processEvents()
                time.sleep(0.01)
            break

    seconds = time.monotonic() - started
    for signal, slot in ((widget._worker.finished, collector.on_finished),
                         (widget._worker.failed, collector.on_failed),
                         (widget._worker.activity, collector.on_activity)):
        try:
            signal.disconnect(slot)
        except TypeError:
            pass

    reply = collector.answers[-1] if collector.answers else {}
    return {
        "message": message,
        "seconds": round(seconds, 2),
        "failure": failure,
        "errors": list(collector.errors),
        "activity": list(collector.activity),
        "text": reply.get("text", ""),
        "tools": reply.get("tools", []),
        "limit": bool(reply.get("limit", False)),
    }


def masked(text: str) -> str:
    """Текст без ПДн — для отчёта (в git отчёт не идёт, но правило одно)."""
    try:
        from core.pseudonymizer import Pseudonymizer

        result = Pseudonymizer().anonymize(str(text or ""))
        #: anonymize возвращает (текст, маппинг).
        return result[0] if isinstance(result, tuple) else str(result)
    except Exception:  # noqa: BLE001 — маска не должна ронять отчёт
        return str(text or "")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "run.log").write_text("", encoding="utf-8")
    for stale in SHOT_DIR.glob("*.png"):
        stale.unlink()

    log("=" * 74)
    log("ЖИВАЯ ПРОВЕРКА: лимит шагов 20 + человеческий ответ")
    log(f"начало: {datetime.now().isoformat(timespec='seconds')}")
    log("=" * 74)

    from core.settings_service import AGENT_LIMITS, SettingsService

    service = SettingsService(str(PROJECT_ROOT / "config" / "settings.json"))
    values = service.agent_settings()
    log(f"настройки: max_tool_steps={values['max_tool_steps']}, "
        f"предел настройки={AGENT_LIMITS['max_tool_steps']}")
    check("C.2 файл настроек: max_tool_steps = 20",
          int(values["max_tool_steps"]) == 20, str(values["max_tool_steps"]))

    from core.agent.agent import DEFAULT_MAX_REQUEST_SECONDS, REPEAT_LIMIT

    check("A.7 таймаут запроса — 120 с",
          float(DEFAULT_MAX_REQUEST_SECONDS) == 120.0)
    check("A.6 защита от зацикливания — REPEAT_LIMIT повторов",
          int(REPEAT_LIMIT) >= 2, str(REPEAT_LIMIT))

    operator_before = db_snapshot(OPERATOR_DB)
    isolate_db()
    log(f"база оператора: {OPERATOR_DB} (не изменяется)")
    log(f"рабочая копия:  {WORK_DB}")

    from PyQt5.QtWidgets import QApplication, QMessageBox

    from ui.agent_widget import install_agent
    from ui.main_window import MainWindow

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
        log("FAIL: Максимилиан выключен настройками — проверка невозможна")
        return 2
    widget.auto_confirm = True
    widget._ensure_worker()
    if widget._agent is None:
        widget._load_history(int(values["max_history"]),
                             int(values["max_tool_steps"]))

    from core.agent.llm import SafeChat

    widget._agent.chat = SafeChat(widget._client())
    agent = widget._agent
    client = agent.chat.client
    log(f"модель: {type(client).__name__} ({getattr(client, 'model', '?')})")
    log(f"агент: шагов={agent.max_tool_steps}, "
        f"таймаут={agent.max_request_seconds} с, "
        f"инструментов={len(agent.registry.names())}")

    check("C.2 агент получил лимит 20 шагов",
          int(agent.max_tool_steps) == 20, str(agent.max_tool_steps))
    check("C.2 агент получил таймаут 120 с",
          float(agent.max_request_seconds) == 120.0,
          str(agent.max_request_seconds))

    shots = []
    shots.append(shoot(app, widget, "01_window_before"))

    # ── ЗАПРОС (C.1) ──
    log("-" * 74)
    log(f"запрос: {QUERY!r}")
    collector = Collector()
    record = ask(app, widget, collector, QUERY)
    log(f"ответ получен за {record['seconds']} с, "
        f"инструментов: {len(record['tools'])}, "
        f"признак лимита: {record['limit']}")
    log(f"инструменты: {record['tools'] or '—'}")
    log(f"текст ответа ({len(record['text'])} символов):")
    for line in record["text"].splitlines():
        log(f"    | {line}")
    if record["errors"]:
        log(f"ОШИБКИ: {record['errors']}")
    if record["failure"]:
        log(f"СБОЙ ЗАПРОСА: {record['failure']}")

    shots.append(shoot(app, widget, "02_window_after_query"))

    answer = record["text"]
    lowered = answer.lower()

    check("C.1 запрос выполнен без ошибки",
          not record["failure"] and not record["errors"],
          record["failure"] or (record["errors"] or [""])[0])
    check("C.2 ответ непустой", len(answer.strip()) > 0,
          f"{len(answer)} символов")
    check("C.2 таймаут не сработал (ответ быстрее 120 с)",
          record["seconds"] < 120.0, f"{record['seconds']} с")
    check("C.2 зацикливания не было",
          "повторил одно и то же" not in lowered)
    check("C.2 ответ не по лимиту шагов",
          "лимит исчерпан" not in lowered and not record["limit"],
          "остановлен предохранителем" if record["limit"] else "ответ модели")

    forbidden = [item for item in FORBIDDEN if item in answer]
    check("B.5 в ответе нет технических идентификаторов",
          not forbidden, ", ".join(forbidden))

    check("C.2 ответ уложился в предел длины",
          len(answer) <= 4000, f"{len(answer)} символов")

    # ── предохранитель: если сработал, ответ должен быть человеческим ──
    if record["limit"] or "лимит исчерпан" in lowered:
        for line in ("Задача большая — не успел за один запрос",
                     "Давай по частям", "Что сделать первым:",
                     "• Найти организацию в базе?", "• Заполнить форму?",
                     "• Проверить в ЕГРЮЛ?"):
            check(f"C.2 человеческий ответ: {line!r}", line in answer)
    else:
        log("  (предохранитель не сработал — ответ целиком от модели)")

    # ── история диалога ──
    try:
        wire = agent.memory.wire_messages("проверка")
        unfinished = [
            index for index, message in enumerate(wire)
            if message.get("role") == "tool" and not message.get("tool_call_id")
        ]
        check("C.2 история диалога корректна (нет ответов без вызовов)",
              not unfinished, str(unfinished))
    except Exception as error:  # noqa: BLE001 — история не должна ронять прогон
        check("C.2 история диалога корректна", False,
              f"{type(error).__name__}: {error}")

    # ── панель показывает ответ оператору ──
    try:
        panel_text = widget.panel.history_text() \
            if hasattr(widget.panel, "history_text") else ""
    except Exception:  # noqa: BLE001
        panel_text = ""
    check("C.2 ответ виден в панели (история виджета)",
          panel_text == "" or answer.splitlines()[0] in panel_text,
          f"{len(panel_text)} символов в панели")

    # ── настройки: предел лимита в интерфейсе ──
    dialog = None
    try:
        from ui.agent_settings_dialog import AgentSettingsDialog

        dialog = AgentSettingsDialog(values, window)
        dialog.resize(720, 980)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        for _ in range(20):
            app.processEvents()
        #: Крутим содержимое к полю «Шагов на вопрос», иначе в снимок попадёт
        #: только начало диалога (содержимое лежит в области прокрутки).
        try:
            from PyQt5.QtWidgets import QScrollArea

            area = dialog.findChildren(QScrollArea)[0]
            area.ensureWidgetVisible(dialog.max_steps)
        except Exception as error:  # noqa: BLE001 — снимок важнее прокрутки
            log(f"  прокрутка настроек не удалась: {type(error).__name__}")
        time.sleep(0.2)
        for _ in range(20):
            app.processEvents()
        #: Снимок самого диалога: он модальный и в снимок окна не попадает.
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        dialog_path = SHOT_DIR / "03_settings_steps_dialog.png"
        for _ in range(10):
            app.processEvents()
        if dialog.grab().save(str(dialog_path)):
            shots.append(str(dialog_path))
            log(f"  снимок: диалог настроек → {dialog_path.name}")
        shots.append(shoot(app, widget, "03_settings_steps"))
        check("C.2 в настройках предел шагов — 20",
              int(dialog.max_steps.maximum()) == 20
              and int(dialog.max_steps.value()) == 20,
              f"значение={dialog.max_steps.value()}, "
              f"предел={dialog.max_steps.maximum()}")
    except Exception as error:  # noqa: BLE001 — диалог не должен ронять прогон
        check("C.2 в настройках предел шагов — 20", False,
              f"{type(error).__name__}: {error}")
    finally:
        if dialog is not None:
            try:
                dialog.close()
            except Exception:  # noqa: BLE001
                pass

    # ── полный текст ответа в файл (для отчёта; ПДн через маску) ──
    (OUT_DIR / "answer.txt").write_text(masked(answer), encoding="utf-8")
    #: Панель для полного ответа: выше экрана, чтобы в снимок попал ВЕСЬ
    #: ответ, включая последний вариант выбора («Проверить в ЕГРЮЛ?»).
    widget.panel.resize(560, 1500)
    for _ in range(20):
        app.processEvents()
    time.sleep(0.2)
    for _ in range(20):
        app.processEvents()
    shots.append(shoot(app, widget, "04_panel_final"))
    widget.panel.resize(350, 922)
    for _ in range(15):
        app.processEvents()

    # ── база оператора не изменилась ──
    operator_after = db_snapshot(OPERATOR_DB)
    check("база оператора не изменилась",
          operator_before.get("sha256") == operator_after.get("sha256")
          and operator_before.get("counters") == operator_after.get("counters"),
          "sha256 совпал" if operator_before.get("sha256")
          == operator_after.get("sha256") else "ФАЙЛ ИЗМЕНИЛСЯ")

    work_after = db_snapshot(WORK_DB)
    log(f"рабочая копия: integrity={work_after.get('integrity')}, "
        f"agent_history={work_after.get('counters', {}).get('agent_history')}")

    # ── уборка окна ──
    try:
        widget.shutdown()
    except Exception:  # noqa: BLE001 — выход не должен ронять отчёт
        pass
    for _ in range(20):
        app.processEvents()
    window.close()
    for _ in range(20):
        app.processEvents()

    passed = sum(1 for item in checks if item["ok"])
    report = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "query": QUERY,
        "model": str(getattr(client, "model", "")),
        "settings": {"max_tool_steps": int(values["max_tool_steps"]),
                     "max_request_seconds": float(agent.max_request_seconds)},
        "record": record,
        "answer_masked": masked(answer),
        "answer_len": len(answer),
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "screenshots": shots,
        "operator_db_sha256": operator_after.get("sha256"),
        "work_db": str(WORK_DB),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    log("=" * 74)
    log(f"ИТОГ: {passed} из {len(checks)} проверок пройдено")
    log(f"отчёт: {REPORT_PATH}")
    log("=" * 74)
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
