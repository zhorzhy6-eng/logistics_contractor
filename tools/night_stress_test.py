#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ночной стресс-тест программы ЦЕЛИКОМ — отдельный инструмент, не pytest.

    python tools/night_stress_test.py                # полный прогон (100/100/50)
    python tools/night_stress_test.py --quick        # быстрая проверка инструмента
    python tools/night_stress_test.py --resume       # продолжить запросы GigaChat
    python tools/night_stress_test.py --no-gigachat  # без сети

Зачем
-----
`pytest` проверяет по одному узлу за раз. Этот инструмент гоняет программу
так, как её гоняет оператор: живой GigaChat, живое окно (offscreen), живые
генераторы, живая SQLite — и всё это подряд, сотнями итераций, с замером
времени и памяти. Место ему — `tools/`, в прогон pytest он не попадает.

Что проверяется
---------------
1. **Подготовка.** Закрываются запущенные копии программы, рабочая база
   `contracts.db` копируется через `VACUUM INTO` во временную папку, все
   пути (`db.database.DB_PATH`, `core.data_paths` — через
   `LOGISTICS_DATA_ROOT`) переводятся на копию. Рабочие данные оператора
   не открываются на запись и не меняются. Ключ GigaChat проверяется до
   первого запроса.
2. **Распознавание текста (GigaChat), 100 текстов.** Тексты берутся из
   генератора `tools/make_text_scenarios.py` — по 25 на каждый уровень
   сложности (simple / medium / hard / extreme). Каждый ответ идёт в
   настоящий слот окна «Распознавание данных»
   (`MainWindow._on_recognition_finished`), замеряется время ответа и
   считается recall / precision по полям (`tools/synthetic_stress.score`).
   Пауза между запросами ~19 с; 429 — пауза 60 с и повтор (до 3 раз);
   сетевая ошибка — сценарий пропускается.
3. **Генерация и сохранение (UI), 100 договоров.** Цикл «только
   Экспедиторство» / «Экспедиторство + Формика» / «Экспедиторство +
   Логистикс»: заполняются вкладки настоящего окна, генерируется DOCX
   настоящим генератором, связанный тип получает зеркало и свой документ,
   затем вызывается логика кнопки «Сохранить в базу»
   (`MainWindow._on_save_to_db`). Проверяется, что файлы физически лежат в
   папках рейсов, в базе есть запись `contracts`, а суммы и VIN в DOCX
   совпадают с базой.
4. **Интерфейс.** Программное переключение типов (Экспедиторство →
   Логистикс → Аренда → Формика и назад) 50 раз с проверкой, что
   заполненные данные не потерялись; открытие/закрытие диалогов
   «Справочник», «Настройки», «Импорт».
5. **Память и выход.** RSS процесса замеряется каждые 10 итераций;
   рост — предупреждение. Затем программа запускается отдельными
   процессами и закрывается штатным путём (`main._do_exit`); код выхода
   проверяется (0 — норма, 0xC0000005 — известное падение Qt).
6. **Отчёт.** Консоль + `docs/night_stress_report.md`.

Чего инструмент НЕ делает
-------------------------
* Не меняет рабочую базу: пишет только в копию (`tests/_tmp/night_stress_*`).
* Не трогает шаблоны, настройки оператора и `git`.
* Не заменяет pytest: это отдельный прогон «как у оператора».

Журнал без ПДн: в лог и отчёт попадают только имена полей, числа, длины и
метки — ни ФИО, ни паспортов, ни адресов, ни строк документов.
"""

import argparse
import gc
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ─────────────────────────────────────────────────────────────
# Константы прогона
# ─────────────────────────────────────────────────────────────

#: Сколько итераций по умолчанию (распознавание и генерация — поровну).
DEFAULT_ITERATIONS = 100

#: Сколько циклов переключения типов по умолчанию.
DEFAULT_CYCLES = 50

#: Пауза между запросами GigaChat: ~190 запросов в час при лимите Freemium ~200.
DEFAULT_PAUSE = 19.0

#: Пауза при ответе 429 (лимит запросов).
RATE_LIMIT_PAUSE = 60.0

#: Сколько раз повторить запрос после 429.
MAX_RATE_LIMIT_RETRIES = 3

#: Каждые сколько итераций замеряется память.
MEMORY_EVERY = 10

#: Порог предупреждения об утечке: рост RSS в процентах и в мегабайтах.
MEMORY_GROWTH_PERCENT = 40.0
MEMORY_GROWTH_MB = 250.0

#: Минимальный разумный размер готового DOCX (бланк весит заметно больше).
DOCX_MIN_BYTES = 10_000

#: Уровни сложности сценариев — по 25 текстов на каждый.
LEVELS = ("simple", "medium", "hard", "extreme")

#: Режимы генерации договора.
MODE_ONLY = "only"
MODE_FORMIKA = "formika"
MODE_LOGISTIKS = "logistiks_rus"
GENERATION_MODES = (MODE_ONLY, MODE_FORMIKA, MODE_LOGISTIKS)

#: Подписи режимов для отчёта.
MODE_TITLES = {
    MODE_ONLY: "только Экспедиторство",
    MODE_FORMIKA: "Экспедиторство + Формика",
    MODE_LOGISTIKS: "Экспедиторство + Логистикс",
}

#: Порядок переключения типов (требование задания).
SWITCH_ORDER = ("perevozka", "logistiks_rus", "arenda_ts", "formika")

#: Подписи типов для отчёта (без приватных модулей).
TYPE_TITLES = {
    "perevozka": "Экспедиторство",
    "logistiks_rus": "Логистикс",
    "arenda_ts": "Аренда ТС",
    "formika": "Формика",
    "zayavka_excel": "Заявка Excel",
}

#: Признаки нераскрытого шаблона в готовом документе.
TEMPLATE_LEAKS = ("{{", "}}", "{%", "%}", "<<")

#: Латинский алфавит VIN (без I, O, Q — они запрещены стандартом).
VIN_ALPHABET = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"

#: Куда пишется отчёт по умолчанию.
DEFAULT_REPORT = PROJECT_ROOT / "docs" / "night_stress_report.md"

#: Код исключения Windows 0xC0000005 (access violation) в виде кода возврата.
ACCESS_VIOLATION = 0xC0000005


# ─────────────────────────────────────────────────────────────
# Журнал: консоль + файл
# ─────────────────────────────────────────────────────────────

class StressLog:
    """
    Двойной вывод: строка сразу в консоль и в файл прогона.

    Файл нужен потому, что ночью консоль никто не смотрит: если прогон
    упадёт, причина останется в `stress.log` рядом с базой-копией.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a", encoding="utf-8", buffering=1)
        self._errors = 0
        self._warnings = 0

    def _emit(self, level: str, message: str) -> None:
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {level:<5} | {message}"
        print(line, flush=True)
        try:
            self._handle.write(line + "\n")
        except OSError:
            pass

    def info(self, message: str) -> None:
        self._emit("INFO", message)

    def warn(self, message: str) -> None:
        self._warnings += 1
        self._emit("WARN", message)

    def error(self, message: str) -> None:
        self._errors += 1
        self._emit("ERR", message)

    def section(self, title: str) -> None:
        self._emit("STEP", "")
        self._emit("STEP", "=" * 78)
        self._emit("STEP", title)
        self._emit("STEP", "=" * 78)

    @property
    def counters(self) -> Dict[str, int]:
        return {"warnings": self._warnings, "errors": self._errors}

    def close(self) -> None:
        try:
            self._handle.close()
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────
# Память процесса
# ─────────────────────────────────────────────────────────────

class MemoryTracker:
    """
    Замер потребления памяти процессом (psutil).

    Замер делается ПОСЛЕ `gc.collect()`: иначе «рост» показывал бы мусор,
    который сборщик ещё не успел убрать, а не настоящую утечку.
    """

    def __init__(self, log: StressLog):
        self.log = log
        self.samples: List[Dict[str, Any]] = []
        self.available = False
        self._process = None
        try:
            import psutil  # noqa: PLC0415 — необязательная зависимость

            self._process = psutil.Process()
            self.available = True
        except Exception as exc:                                 # noqa: BLE001
            self.log.warn(f"psutil недоступен — замер памяти выключен: {exc}")

    def sample(self, label: str) -> Optional[Dict[str, Any]]:
        """Снимает RSS процесса и запоминает точку замера."""
        if not self.available:
            return None
        try:
            gc.collect()
            rss_mb = self._process.memory_info().rss / (1024 * 1024)
        except Exception as exc:                                 # noqa: BLE001
            self.log.warn(f"Замер памяти не удался: {exc}")
            return None

        item = {
            "label": label,
            "rss_mb": round(rss_mb, 1),
            "at": datetime.now().strftime("%H:%M:%S"),
        }
        self.samples.append(item)
        self.log.info(f"Память [{label}]: {rss_mb:.1f} МБ")
        return item

    def mark_baseline(self) -> None:
        """
        Помечает последний замер как «базу» для оценки роста.

        Так первый замер (до поднятия Qt и окон) не выдаётся за утечку:
        одноразовая инициализация занимает сотни мегабайт, но это не рост
        при работе.
        """
        if self.samples:
            self.samples[-1]["baseline"] = True

    def summary(self) -> Dict[str, Any]:
        """
        Итог: первый/последний/пиковый замер и вердикт об утечке.

        Рост считается от ПОСЛЕДНЕГО помеченного замера (базы), иначе от
        первого: инициализация Qt не должна выглядеть утечкой.
        """
        if not self.available:
            return {"available": False}
        if not self.samples:
            return {"available": True, "samples": []}

        baseline_index = 0
        for index, item in enumerate(self.samples):
            if item.get("baseline"):
                baseline_index = index
        base = self.samples[baseline_index]

        first = base["rss_mb"]
        last = self.samples[-1]["rss_mb"]
        peak = max(item["rss_mb"] for item in self.samples[baseline_index:])
        growth_mb = round(last - first, 1)
        growth_percent = round((growth_mb / first) * 100, 1) if first else 0.0
        leak = growth_mb >= MEMORY_GROWTH_MB or growth_percent >= MEMORY_GROWTH_PERCENT

        if leak:
            self.log.warn(
                f"Память растёт: {first:.1f} → {last:.1f} МБ "
                f"(+{growth_mb:.1f} МБ, +{growth_percent:.1f} %), пик {peak:.1f} МБ"
            )

        return {
            "available": True,
            "samples": self.samples,
            "baseline_index": baseline_index,
            "baseline_label": base.get("label", ""),
            "first_mb": first,
            "last_mb": last,
            "peak_mb": peak,
            "growth_mb": growth_mb,
            "growth_percent": growth_percent,
            "leak_suspected": leak,
        }


# ─────────────────────────────────────────────────────────────
# Подготовка окружения
# ─────────────────────────────────────────────────────────────

def _make_work_dir(args) -> Path:
    """Папка прогона: своя на каждый запуск (или переданная --work-dir)."""
    if args.work_dir:
        work = Path(args.work_dir).expanduser().resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        work = PROJECT_ROOT / "tests" / "_tmp" / f"night_stress_{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    return work


def _resolve_working_db(log: StressLog) -> Path:
    """
    Находит РАБОЧУЮ базу оператора.

    Порядок: папка данных приложения (её видит собранный `.exe`) → корень
    проекта (старые версии) → папка данных разработки.
    """
    appdata = os.environ.get("APPDATA", "").strip()
    candidates = []
    if appdata:
        candidates.append(Path(appdata) / "LogisticsContractor" / "contracts.db")
    candidates.append(PROJECT_ROOT / "contracts.db")
    candidates.append(PROJECT_ROOT / "tests" / "_tmp" / "_appdata" / "contracts.db")

    for path in candidates:
        if path.is_file():
            log.info(f"Рабочая база найдена: {path}")
            return path
    raise FileNotFoundError(
        "рабочая contracts.db не найдена ни в %APPDATA%, ни в корне проекта"
    )


def _copy_database(source: Path, target: Path, log: StressLog) -> Dict[str, Any]:
    """
    Изолированная копия базы через `VACUUM INTO`.

    Источник открывается ТОЛЬКО НА ЧТЕНИЕ (`mode=ro`): рабочая база
    оператора не должна измениться даже случайно. `VACUUM INTO` пишет
    новый файл и сам учитывает журнал WAL, поэтому копия полная — в отличие
    от `shutil.copy2`, который потерял бы последние транзакции.
    """
    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)

    started = time.perf_counter()
    method = "VACUUM INTO"
    uri = "file:" + source.as_posix() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=20)
        try:
            connection.execute("VACUUM INTO ?", (str(target),))
        finally:
            connection.close()
    except sqlite3.Error as exc:
        log.warn(f"VACUUM INTO не удался ({exc}) — копирую через backup API")
        method = "backup API"
        target.unlink(missing_ok=True)
        source_connection = sqlite3.connect(uri, uri=True, timeout=20)
        try:
            destination = sqlite3.connect(str(target))
            try:
                source_connection.backup(destination)
            finally:
                destination.close()
        finally:
            source_connection.close()

    elapsed = time.perf_counter() - started
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError(f"копия базы не создана: {target}")

    tables = contracts = 0
    check = sqlite3.connect(str(target))
    try:
        tables = check.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        try:
            contracts = check.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
        except sqlite3.Error:
            contracts = -1
    finally:
        check.close()

    log.info(
        f"Копия базы ({method}): {target.name}, {target.stat().st_size / 1024:.0f} КБ, "
        f"таблиц {tables}, договоров {contracts}, {elapsed:.2f} с"
    )
    return {
        "source": str(source),
        "target": str(target),
        "method": method,
        "size": target.stat().st_size,
        "tables": tables,
        "contracts": contracts,
        "seconds": round(elapsed, 2),
    }


def _kill_running_apps(log: StressLog) -> List[Dict[str, Any]]:
    """
    Закрывает запущенные копии программы — они держат `logs/app.log` и базу.

    Убиваются ТОЛЬКО процессы самой программы: собранный
    `LogisticsContractor.exe` и `python/pythonw`, у которых в командной
    строке есть `main.py` этого проекта. Чужие python-процессы (серверы
    инструментов, IDE) не трогаются.
    """
    killed: List[Dict[str, Any]] = []
    try:
        import psutil  # noqa: PLC0415 — необязательная зависимость
    except Exception as exc:                                     # noqa: BLE001
        log.warn(f"psutil недоступен — проверка запущенных копий пропущена: {exc}")
        return killed

    project = str(PROJECT_ROOT).lower().replace("/", "\\")
    current_pid = os.getpid()
    candidates = []
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = process.info
            if info["pid"] == current_pid:
                continue
            name = (info.get("name") or "").lower()
            cmdline = " ".join(info.get("cmdline") or []).lower().replace("/", "\\")
            is_executable = name.startswith("logisticscontractor")
            is_sources = (
                name.startswith("python")
                and "main.py" in cmdline
                and project in cmdline
            )
            if is_executable or is_sources:
                candidates.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    for process in candidates:
        try:
            pid = process.pid
            name = process.name()
            process.terminate()
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            killed.append({"pid": pid, "name": name})
            log.warn(f"Закрыта запущенная копия программы: {name} (pid {pid})")
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired) as exc:
            log.warn(f"Не удалось закрыть процесс: {exc}")

    if not killed:
        log.info("Запущенных копий программы нет")
    time.sleep(0.5)  # файлы журналов освобождаются не мгновенно
    return killed


def _check_gigachat_key(log: StressLog) -> Dict[str, Any]:
    """
    Проверяет ключ GigaChat ДО первой сети: есть ли он и годен ли формат.

    Сетевую проверку (401/403) делает первый запрос распознавания: тратить
    отдельный запрос «в пустоту» незачем.
    """
    report: Dict[str, Any] = {"present": False, "format_ok": False, "reason": ""}
    try:
        from core.secrets_store import (                        # noqa: PLC0415
            describe_key_state,
            get_gigachat_key,
            gigachat_key_problem,
            has_gigachat_key,
        )
    except Exception as exc:                                     # noqa: BLE001
        report["reason"] = f"хранилище секретов недоступно: {exc}"
        log.error(f"Ключ GigaChat: {report['reason']}")
        return report

    try:
        present = bool(has_gigachat_key())
    except Exception as exc:                                     # noqa: BLE001
        report["reason"] = f"чтение хранилища не удалось: {exc}"
        log.error(f"Ключ GigaChat: {report['reason']}")
        return report

    report["present"] = present
    if not present:
        report["reason"] = "ключа GigaChat нет в системном хранилище"
        log.error(
            "Ключ GigaChat: " + report["reason"]
            + ". Распознавание пропускается (шаг 2 отчёта будет пустым)."
        )
        return report

    try:
        key = get_gigachat_key() or ""
        problem = gigachat_key_problem(key)
        report["length"] = len(key)
        report["state"] = describe_key_state()
    except Exception as exc:                                     # noqa: BLE001
        report["reason"] = f"ключ не прочитан: {exc}"
        log.error(f"Ключ GigaChat: {report['reason']}")
        return report

    if problem:
        report["reason"] = str(problem)
        log.error(f"Ключ GigaChat: формат неверный — {problem}")
        return report

    report["format_ok"] = True
    log.info(
        f"Ключ GigaChat: OK, формат верный, длина {report['length']} символов "
        f"(значение в журнал не попадает)"
    )
    return report


def _copy_operator_settings(source_root: Optional[Path], data_root: Path,
                            log: StressLog) -> None:
    """Копирует настройки оператора в изолированную папку данных (если есть)."""
    target = data_root / "config" / "settings.json"
    if target.exists():
        return
    candidates = []
    if source_root is not None:
        candidates.append(Path(source_root) / "config" / "settings.json")
    candidates.append(PROJECT_ROOT / "config" / "settings.json")
    for path in candidates:
        try:
            if path.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                log.info(f"Настройки скопированы: {path}")
                return
        except OSError as exc:
            log.warn(f"Настройки не скопированы из {path}: {exc}")
    log.info("Настройки оператора не найдены — программа возьмёт умолчания")


# ─────────────────────────────────────────────────────────────
# Общие помощники для Qt-части
# ─────────────────────────────────────────────────────────────

def _patch_message_boxes() -> None:
    """
    Убирает модальные окна: в offscreen они блокируют прогон.

    Заглушки возвращают «ОК»/«Да». Это не «отключение проверок»: сами
    проверки выполняются, диалог подтверждения просто не ждёт оператора.
    """
    from PyQt5.QtWidgets import QMessageBox                    # noqa: PLC0415

    def _silent(*_args, **_kwargs):
        return QMessageBox.Ok

    QMessageBox.information = staticmethod(_silent)
    QMessageBox.warning = staticmethod(_silent)
    QMessageBox.critical = staticmethod(_silent)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    # `exec_()` окна сообщений (свои кнопки) ждали бы нажатия вечно.
    QMessageBox.exec_ = lambda self, *a, **k: 0


def _patch_overwrite_question(window, counters: Dict[str, Any]) -> None:
    """
    Отвечает «Да» на вопрос «Перезаписать данные в окне типа?».

    Вопрос возникает, когда зеркало изменит поля, уже заполненные в окне
    цели. В живом прогоне оператор обновлял бы окно под новый рейс, и ответ
    был бы «Да»; отвечаем так же и считаем, сколько раз вопрос задавался —
    это метрика конфликтов зеркала, а не отключённая проверка.
    """
    original = getattr(window, "_ask_overwrite_target", None)
    if not callable(original):
        return

    def _ask(title):                                             # noqa: ARG001
        counters["overwrite_questions"] += 1
        return True

    window._ask_overwrite_target = _ask


def _patch_validation(window, counters: Dict[str, Any]) -> None:
    """
    Подменяет диалог подтверждения проверки данных на «создать».

    Замечания валидатора при этом СЧИТАЮТСЯ: в отчёт попадает, сколько раз
    данные не проходили проверку без замечаний — это реальная метрика
    качества тестовых данных и валидатора, а не «отключённая проверка».
    """
    original = getattr(window, "_confirm_validation", None)
    if not callable(original):
        return

    def _confirm(report):
        try:
            counters["checks"] += 1
            counters["warnings"] += len(getattr(report, "warnings", []) or [])
            counters["errors"] += len(getattr(report, "errors", []) or [])
            if not getattr(report, "is_clean", True):
                counters["not_clean"] += 1
        except Exception:                                        # noqa: BLE001
            pass
        return True

    window._confirm_validation = _confirm


def _tab_of(window, key: str):
    """Вкладка окна по ключу: у окон типов и у MainWindow одинаково."""
    getter = getattr(window, "_tab_by_key", None)
    if callable(getter):
        return getter(key)
    return getattr(window, key, None)


def _tab_data_of(window, tab) -> Dict[str, Any]:
    """Данные вкладки: через окно (`_tab_data`), если метод есть."""
    reader = getattr(window, "_tab_data", None)
    if callable(reader):
        return reader(tab) or {}
    getter = getattr(tab, "get_data", None)
    if callable(getter):
        try:
            return getter() or {}
        except Exception:                                        # noqa: BLE001
            return {}
    return {}


def _fill_main_window(window, payload: Dict[str, Any]) -> None:
    """
    Заполняет вкладки главного окна данными (путь распознавания/зеркала).

    Тот же набор вызовов, что делает `_on_recognition_finished`: каждая
    вкладка получает свой раздел. Автотест не «пишет в поля напрямую» —
    он идёт через публичный `fill_data`, как настоящий сценарий.
    """
    window.driver_tab.fill_data(payload.get("driver") or {})
    window.carrier_tab.fill_data(payload.get("carrier") or {})
    window.customer_tab.fill_data(payload.get("customer") or {})
    window.trailer_tab.fill_data(payload.get("tractor") or {},
                                 payload.get("trailer") or {})
    window.vehicles_tab.fill_data(payload.get("vehicles") or [])
    window.contract_tab.fill_data(payload.get("contract") or {})


def _text_of_docx(path: Path) -> str:
    """Весь текст DOCX: абзацы + таблицы (ячейки через пробел)."""
    from docx import Document                                # noqa: PLC0415

    document = Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


def _digits(text: str) -> str:
    """Только цифры: «180 300,00» → «18030000» (суммы в бланке пишутся так)."""
    return re.sub(r"\D", "", text)


def _money_digits(value: Any) -> str:
    """Сумма в виде «как её пишут в бланке», в цифрах без разделителей."""
    try:
        return f"{float(value):.2f}".replace(".", "")
    except (TypeError, ValueError):
        return ""


# ─────────────────────────────────────────────────────────────
# Шаг 1: подготовка окружения и Qt
# ─────────────────────────────────────────────────────────────

def prepare(args, work: Path, log: StressLog) -> Dict[str, Any]:
    """
    Готовит изолированное окружение и поднимает Qt.

    ВАЖНО: `LOGISTICS_DATA_ROOT` выставляется ДО импорта модулей проекта —
    `core/contracts/paths.py` вычисляет OUTPUT_DIR на импорте, и от
    переменной зависит, куда пойдут готовые договоры.
    """
    log.section("ШАГ 1. ПОДГОТОВКА")

    killed = _kill_running_apps(log)
    working_db = _resolve_working_db(log)

    data_root = work / "data"
    (data_root / "config").mkdir(parents=True, exist_ok=True)
    (data_root / "logs").mkdir(parents=True, exist_ok=True)
    (data_root / "output").mkdir(parents=True, exist_ok=True)

    # ── Переключение всех путей на изолированную папку ──
    os.environ["LOGISTICS_DATA_ROOT"] = str(data_root)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["PYTHONIOENCODING"] = "utf-8"
    log.info(f"Папка прогона: {work}")
    log.info(f"LOGISTICS_DATA_ROOT = {data_root}")

    db_info = _copy_database(working_db, data_root / "contracts.db", log)
    _copy_operator_settings(working_db.parent, data_root, log)

    import db.database as database                             # noqa: PLC0415

    database.DB_PATH = str(data_root / "contracts.db")
    database.init_database()
    log.info(f"База прогона инициализирована: {database.DB_PATH}")

    key = _check_gigachat_key(log)

    # ── Qt: приложение, реестр типов, приватная часть ──
    from PyQt5.QtWidgets import QApplication                   # noqa: PLC0415

    app = QApplication.instance() or QApplication([])
    _patch_message_boxes()

    from core.private_path import attach_private_root          # noqa: PLC0415

    private_roots = attach_private_root()
    log.info(f"Приватная часть: {private_roots or 'нет (режим ядра)'}")

    from core.contracts.registry import ContractTypeRegistry   # noqa: PLC0415
    from core.prompts import refresh_prompts                   # noqa: PLC0415

    ContractTypeRegistry.load_builtin()
    known = tuple(ContractTypeRegistry.known_types())
    failures = dict(ContractTypeRegistry.failures())
    prompts = sorted(refresh_prompts())
    log.info(f"Типы договоров: {known}")
    log.info(f"Промпты распознавания: {prompts}")
    if failures:
        log.warn(f"Типы загружены не полностью: {failures}")

    # `main.py` разбирает аргументы командной строки УЖЕ НА ИМПОРТЕ
    # (`_ARGS = _parse_args()`), поэтому свои ключи инструмента на время
    # импорта прячем: иначе argparse падает на `--quick` и прочих.
    saved_argv = sys.argv
    sys.argv = [saved_argv[0]]
    try:
        import main as app_main                                # noqa: PLC0415
    finally:
        sys.argv = saved_argv

    factories = app_main._make_factories()
    from ui.windows import WindowManager                       # noqa: PLC0415
    from ui.windows.manager import register_window_manager     # noqa: PLC0415

    manager = WindowManager(factories)
    register_window_manager(manager)
    log.info(f"Фабрики окон: {sorted(factories)}")

    return {
        "work": work,
        "data_root": data_root,
        "output_dir": data_root / "output",
        "working_db": str(working_db),
        "db_copy": str(data_root / "contracts.db"),
        "db_info": db_info,
        "killed": killed,
        "gigachat_key": key,
        "app": app,
        "manager": manager,
        "window": manager.window_for("perevozka"),
        "known_types": known,
        "type_failures": failures,
        "private_roots": private_roots,
        "main_module": app_main,
        "validation": {"checks": 0, "warnings": 0, "errors": 0, "not_clean": 0,
                       "overwrite_questions": 0},
        "run_tag": datetime.now().strftime("%d%m"),
    }


# ─────────────────────────────────────────────────────────────
# Шаг 2: распознавание текста через GigaChat
# ─────────────────────────────────────────────────────────────

def _select_diverse_scenarios(seeds: Dict[str, Any], total: int) -> List[int]:
    """
    Равномерная выборка «самых разных» текстов.

    По `total // 4` сценариев на каждый уровень сложности; внутри уровня
    берутся равномерно по видам документов (drivers / vehicles /
    organizations / addresses / documents). Остаток добирается из общего
    пула — итог ровно `total`, без повторов.
    """
    metas = seeds["scenarios"]
    per_level = max(1, total // len(LEVELS))
    chosen: List[int] = []
    used: set = set()

    for level in LEVELS:
        pool = [index for index, meta in enumerate(metas)
                if meta["level"] == level]
        if not pool:
            continue
        step = max(1, len(pool) // per_level)
        picked = pool[::step][:per_level]
        for index in picked:
            if index not in used:
                used.add(index)
                chosen.append(index)

    for index in range(len(metas)):
        if len(chosen) >= total:
            break
        if index not in used:
            used.add(index)
            chosen.append(index)

    return chosen[:total]


class RateLimitStop(RuntimeError):
    """Слишком много 429 подряд: дальше идти бессмысленно."""


class AuthStop(RuntimeError):
    """401/403/400 авторизации: ключ не работает, сеть дальше не нужна."""


def _error_kind(exc: BaseException) -> str:
    """Короткая метка ошибки для статистики (без текста исключения)."""
    text = str(exc)
    for code in ("429", "401", "403", "400", "404", "500", "502", "503", "504"):
        if code in text:
            return code
    name = type(exc).__name__
    lowered = name.lower()
    if "timeout" in lowered:
        return "timeout"
    if "connect" in lowered or "network" in lowered or "ssl" in lowered:
        return "network"
    return name


def _sleep(seconds: float) -> None:
    """Пауза с точностью до 0.1 с (для отзывчивости Ctrl+C)."""
    remaining = max(0.0, float(seconds))
    while remaining > 0:
        step = min(0.25, remaining)
        time.sleep(step)
        remaining -= step


def _load_gigachat_progress(path: Path) -> Dict[str, Any]:
    """Уже сделанные запросы: по номерам сценариев."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_gigachat_progress(path: Path, data: Dict[str, Any]) -> None:
    """Атомарная запись прогресса: обрыв не портит файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    temporary.replace(path)


def _aggregate_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    recall / precision / F1 по группе ответов модели.

    Отдельно считается `precision_core` — без служебных подстановок
    промпта (`contract.vat_rate` = 22 %, год выпуска = 0 и т. п.). Модель
    обязана их возвращать, приложение их использует, но в «точности» они
    выглядят лишними полями; та же формула, что в
    `tools/run_text_stress_gigachat.py`.
    """
    expected = sum(row.get("expected", 0) for row in rows)
    found = sum(row.get("found", 0) for row in rows)
    true_positive = sum(row.get("true_positive", 0) for row in rows)
    service = sum(row.get("default_extras", 0) for row in rows)
    recall = true_positive / expected if expected else 0.0
    precision = true_positive / found if found else 0.0
    found_core = found - service
    precision_core = true_positive / found_core if found_core > 0 else 0.0
    f1 = (2 * recall * precision / (recall + precision)
          if (recall + precision) else 0.0)
    return {
        "documents": len(rows),
        "expected": expected,
        "found": found,
        "true_positive": true_positive,
        "service_filled": service,
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "precision_core": round(precision_core, 4),
        "f1": round(f1, 4),
    }


def _field_errors(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """Счётчики по полям: пропущено / неверно / лишнее (только имена полей)."""
    stats: Dict[str, Dict[str, int]] = {}
    for row in rows:
        for key in row.get("missed") or []:
            stats.setdefault(key, {"missed": 0, "wrong": 0, "extra": 0})["missed"] += 1
        for key in row.get("wrong") or []:
            stats.setdefault(key, {"missed": 0, "wrong": 0, "extra": 0})["wrong"] += 1
        for key in row.get("extra") or []:
            stats.setdefault(key, {"missed": 0, "wrong": 0, "extra": 0})["extra"] += 1
    return stats


def _top_fields(stats: Dict[str, Dict[str, int]], kind: str,
                limit: int = 10) -> List[Tuple[str, int]]:
    """Топ полей по одному счётчику (убывание, затем имя)."""
    items = [(name, bucket.get(kind, 0)) for name, bucket in stats.items()
             if bucket.get(kind, 0)]
    items.sort(key=lambda pair: (-pair[1], pair[0]))
    return items[:limit]


def _percentile(values: List[float], share: float) -> float:
    """Перцентиль простым порядковым методом (без внешних зависимостей)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(share * (len(ordered) - 1)))))
    return ordered[index]


def run_recognition(args, ctx: Dict[str, Any], log: StressLog,
                    memory: MemoryTracker) -> Dict[str, Any]:
    """
    Шаг 2: 100 текстов через настоящий GigaChat + настоящий слот окна.

    Метрики: время ответа модели, recall / precision / F1 по полям,
    количество ошибок по причинам, сколько ответов окно приняло.
    """
    log.section("ШАГ 2. РАСПОЗНАВАНИЕ ТЕКСТА (GigaChat)")

    result: Dict[str, Any] = {
        "enabled": True,
        "requests": 0,
        "from_cache": 0,
        "ok": 0,
        "failed": 0,
        "skipped": 0,
        "rate_limited": 0,
        "errors_by_kind": {},
        "ui_fill_failures": 0,
        "seconds": [],
        "rows": [],
        "by_level": {},
        "by_kind": {},
        "top_missed": [],
        "top_wrong": [],
        "top_extra": [],
        "abort_reason": "",
    }

    key = ctx.get("gigachat_key") or {}
    if not (key.get("present") and key.get("format_ok")):
        result["enabled"] = False
        result["abort_reason"] = key.get("reason") or "ключ GigaChat недоступен"
        log.error(
            "Распознавание ПРОПУЩЕНО: " + result["abort_reason"]
            + " (см. set_key.py). Остальные шаги выполняются."
        )
        return result

    from tools.make_text_scenarios import (                    # noqa: PLC0415
        SEEDS_PATH,
        load_seeds,
        scenario_from_seed,
    )
    from tools.synthetic_stress import score                   # noqa: PLC0415
    from core.gigachat_client import GigaChatClient            # noqa: PLC0415

    seeds = load_seeds(SEEDS_PATH)
    metas = seeds["scenarios"]
    total = len(seeds["seeds"])
    log.info(f"Сценариев в генераторе: {total}")

    selection = _select_diverse_scenarios(seeds, args.iterations)
    log.info(
        f"Выбрано текстов: {len(selection)} "
        f"(по уровням: { {level: sum(1 for i in selection if metas[i]['level'] == level) for level in LEVELS} })"
    )

    progress_path = Path(ctx["work"]) / "gigachat_progress.json"
    progress = _load_gigachat_progress(progress_path) if args.resume else {}
    if progress:
        log.info(f"Продолжение прогона: уже есть ответов {len(progress)}")

    client = GigaChatClient()
    log.info(f"Модель: {client.model} | scope: {client.scope} | "
             f"пауза {args.pause} с | повторов при 429: {MAX_RATE_LIMIT_RETRIES}")
    problem = client.key_problem()
    if problem:
        result["enabled"] = False
        result["abort_reason"] = f"ключ не принят клиентом: {problem}"
        log.error("Распознавание ПРОПУЩЕНО: " + result["abort_reason"])
        return result

    window = ctx.get("window")
    if window is None:
        window = ctx["manager"].window_for("perevozka")
    if window is None:
        result["enabled"] = False
        result["abort_reason"] = "окно Экспедиторства не создано"
        log.error("Распознавание ПРОПУЩЕНО: " + result["abort_reason"])
        return result

    started_all = time.perf_counter()
    auth_failures = 0

    for position, index in enumerate(selection, start=1):
        meta = metas[index]
        number = meta["number"]
        key_number = str(number)

        if key_number in progress:
            row = progress[key_number]
            result["rows"].append(row)
            result["seconds"].append(float(row.get("seconds", 0.0)))
            result["requests"] += 1
            result["from_cache"] += 1
            if row.get("status") == "ok":
                result["ok"] += 1
            continue

        result["requests"] += 1
        scenario = scenario_from_seed(
            seeds["seeds"][index], meta["kind"], meta["level"])

        row: Dict[str, Any] = {
            "number": number,
            "kind": meta["kind"],
            "level": meta["level"],
            "text_length": len(scenario["text"]),
        }

        elapsed = 0.0
        data = None
        status = "ok"
        error_kind = ""

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            attempt_started = time.perf_counter()
            try:
                data = client.recognize_text(scenario["text"], cancel=None)
                elapsed = time.perf_counter() - attempt_started
                break
            except Exception as exc:                             # noqa: BLE001
                elapsed = time.perf_counter() - attempt_started
                kind = _error_kind(exc)
                year_of_errors = result["errors_by_kind"]
                year_of_errors[kind] = year_of_errors.get(kind, 0) + 1

                if kind == "429":
                    result["rate_limited"] += 1
                    if attempt < MAX_RATE_LIMIT_RETRIES:
                        log.warn(
                            f"#{number}: 429 — пауза {RATE_LIMIT_PAUSE:.0f} с, "
                            f"повтор {attempt + 1}/{MAX_RATE_LIMIT_RETRIES}"
                        )
                        _sleep(RATE_LIMIT_PAUSE)
                        continue
                    status, error_kind = "failed", kind
                    break

                if kind in ("401", "403", "400"):
                    auth_failures += 1
                    status, error_kind = "failed", kind
                    if auth_failures >= 3:
                        result["abort_reason"] = (
                            f"авторизация GigaChat не проходит ({kind}) — "
                            f"запросы остановлены"
                        )
                        log.error(result["abort_reason"])
                    break

                status, error_kind = "failed", kind
                break

        if status == "ok" and data is not None:
            result["ok"] += 1
            result["seconds"].append(round(elapsed, 2))
            row["seconds"] = round(elapsed, 2)

            # ── Настоящий слот окна: вставка результата в форму ──
            try:
                before = window.driver_tab.get_data() if hasattr(
                    window.driver_tab, "get_data") else {}
                window._on_recognition_finished(data, scenario["text"])
                after = window.driver_tab.get_data() if hasattr(
                    window.driver_tab, "get_data") else {}
                has_driver = bool(str((data.get("driver") or {}).get("full_name") or "").strip())
                if has_driver and not str(after.get("full_name") or "").strip():
                    result["ui_fill_failures"] += 1
                    log.warn(f"#{number}: окно не приняло ФИО водителя из ответа")
                elif before.get("full_name") == after.get("full_name") and has_driver:
                    # Одинаковое ФИО могло совпасть — не считаем ошибкой.
                    pass
            except Exception as exc:                             # noqa: BLE001
                result["ui_fill_failures"] += 1
                log.warn(
                    f"#{number}: слот окна упал — {type(exc).__name__}: "
                    f"{str(exc)[:120]}"
                )

            # ── Метрика качества по полям ──
            try:
                metrics = score(scenario["ground_truth"], data)
                row.update({
                    "expected": metrics.get("expected", 0),
                    "found": metrics.get("found", 0),
                    "true_positive": metrics.get("true_positive", 0),
                    "missed": metrics.get("missed", []),
                    "wrong": metrics.get("wrong", []),
                    "extra": metrics.get("extra", []),
                    "recall": metrics.get("recall", 0.0),
                    "precision": metrics.get("precision", 0.0),
                    "f1": metrics.get("f1", 0.0),
                })
                try:
                    from tools.run_text_stress_gigachat import (  # noqa: PLC0415
                        prompt_default_extras,
                    )

                    row["default_extras"] = len(prompt_default_extras(
                        scenario["ground_truth"], data))
                except Exception:                            # noqa: BLE001
                    row["default_extras"] = 0
            except Exception as exc:                             # noqa: BLE001
                log.warn(f"#{number}: метрика не посчитана — {type(exc).__name__}")
                row.update({"expected": 0, "found": 0, "true_positive": 0,
                            "missed": [], "wrong": [], "extra": [],
                            "recall": 0.0, "precision": 0.0, "f1": 0.0})

            row["status"] = "ok"
            progress[key_number] = row
            _save_gigachat_progress(progress_path, progress)
            result["rows"].append(row)

            log.info(
                f"[{position:>3}/{len(selection)}] #{number} "
                f"{meta['kind']}/{meta['level']} OK {elapsed:.1f} с, "
                f"r={row['recall']:.2f} p={row['precision']:.2f} f1={row['f1']:.2f}"
            )
        else:
            row["status"] = "failed"
            row["error"] = error_kind
            if error_kind in ("429",):
                result["skipped"] += 1
            else:
                result["skipped"] += 1
            result["failed"] += 1
            progress[key_number] = row
            _save_gigachat_progress(progress_path, progress)
            result["rows"].append(row)
            log.warn(
                f"[{position:>3}/{len(selection)}] #{number} "
                f"{meta['kind']}/{meta['level']} ОШИБКА {error_kind} — пропуск"
            )

        # ── Память каждые 10 запросов ──
        if position % MEMORY_EVERY == 0:
            memory.sample(f"GigaChat {position} запросов")

        if result["abort_reason"]:
            break

        # ── Пауза до следующего запроса (от начала цикла, а не после ответа) ──
        if position < len(selection):
            cycle = elapsed
            _sleep(max(0.0, args.pause - cycle))

    total_elapsed = time.perf_counter() - started_all
    result["total_seconds"] = round(total_elapsed, 2)

    rows = [row for row in result["rows"] if row.get("status") == "ok"]
    result["metrics"] = _aggregate_metrics(rows)
    for level in LEVELS:
        subset = [row for row in rows if row.get("level") == level]
        if subset:
            result["by_level"][level] = _aggregate_metrics(subset)
    kinds = sorted({row.get("kind") for row in rows if row.get("kind")})
    for kind in kinds:
        subset = [row for row in rows if row.get("kind") == kind]
        result["by_kind"][kind] = _aggregate_metrics(subset)

    stats = _field_errors(rows)
    result["top_missed"] = _top_fields(stats, "missed")
    result["top_wrong"] = _top_fields(stats, "wrong")
    result["top_extra"] = _top_fields(stats, "extra")

    seconds = result["seconds"]
    if seconds:
        result["avg_seconds"] = round(sum(seconds) / len(seconds), 2)
        result["median_seconds"] = round(_percentile(seconds, 0.5), 2)
        result["p95_seconds"] = round(_percentile(seconds, 0.95), 2)
        result["max_seconds"] = round(max(seconds), 2)
        result["min_seconds"] = round(min(seconds), 2)

    log.info(
        f"Итог распознавания: ok={result['ok']}, ошибок={result['failed']}, "
        f"429={result['rate_limited']}, "
        f"среднее {result.get('avg_seconds', 0)} с, "
        f"recall={result['metrics']['recall']:.3f}, "
        f"precision={result['metrics']['precision']:.3f}"
    )
    return result


# ─────────────────────────────────────────────────────────────
# Шаг 3: генерация документов и сохранение в базу
# ─────────────────────────────────────────────────────────────

def _build_payload(index: int, mode: str, tag: str) -> Dict[str, Any]:
    """
    Собирает вымышленные данные договора для итерации.

    Значения берутся из словарей синтетического генератора
    (`tools/make_synthetic_docs`) — они вымышленные by design, поэтому в
    базу-копию и документы не попадают настоящие данные оператора.
    """
    from tools.powerful_e2e import payload_perevozka       # noqa: PLC0415
    from tools.make_synthetic_docs import (                # noqa: PLC0415
        CITIES,
        COLORS,
        ISSUERS,
        MALE_NAMES,
        PATRONYMICS_MALE,
        STREETS,
        SURNAMES,
    )

    payload = payload_perevozka()
    rnd = random.Random(20261106 + index * 7919)

    # ── Водитель ──
    driver = dict(payload["driver"])
    surname = SURNAMES[index % len(SURNAMES)]
    name = MALE_NAMES[(index * 7) % len(MALE_NAMES)]
    patronymic = PATRONYMICS_MALE[(index * 3) % len(PATRONYMICS_MALE)]
    driver["full_name"] = f"{surname} {name} {patronymic}"
    driver["passport_series"] = f"{10 + index % 80:02d} {20 + index % 9:02d}"
    driver["passport_number"] = f"{(100000 + index * 137) % 900000 + 100000:06d}"
    driver["passport_issuer"] = ISSUERS[index % len(ISSUERS)]
    driver["birth_place"] = f"г. {CITIES[index % len(CITIES)]}"
    driver["registration_address"] = (
        f"г. {CITIES[index % len(CITIES)]}, ул. {STREETS[index % len(STREETS)]}, "
        f"д. {1 + index % 99}, кв. {1 + (index * 3) % 200}"
    )
    driver["phone"] = f"+7 (9{10 + index % 88:02d}) {100 + index % 900:03d}-" \
                      f"{10 + index % 80:02d}-{20 + index % 70:02d}"
    driver["license_series"] = f"{90 + index % 9:02d} {30 + index % 9:02d}"
    driver["license_number"] = f"{(200000 + index * 317) % 900000:06d}"

    # ── Машины (VIN из разрешённых символов, 17 знаков) ──
    vehicles = []
    count = 1 + index % 3
    for position in range(count):
        seed = index * 100 + position
        vin = "".join(random.Random(seed).choice(VIN_ALPHABET) for _ in range(17))
        vehicles.append({
            "vin": vin,
            "brand_model": f"{rnd.choice(('LADA', 'HAVAL', 'JETOUR', 'GAZ', 'KAMAZ'))} "
                           f"{rnd.choice(('Vesta', 'Dashing', 'T2', 'Next', '5490'))}",
            "plate_number": f"{random.Random(seed).choice('АВЕКМНОРСТУХ')}"
                            f"{100 + seed % 900:03d}"
                            f"{random.Random(seed + 5).choice('АВЕКМНОРСТУХ')}"
                            f"{random.Random(seed + 6).choice('АВЕКМНОРСТУХ')}"
                            f"{10 + seed % 90:02d}",
            "year": 2019 + seed % 6,
            "color": COLORS[seed % len(COLORS)],
            "vehicle_type": "Легковой автомобиль",
        })

    # ── Маршрут и сумма ──
    city_from = CITIES[index % len(CITIES)]
    city_to = CITIES[(index * 3 + 1) % len(CITIES)]
    price = 120000.0 + index * 1373.0
    vat_rate = 22
    contract = dict(payload["contract"])
    contract.update({
        "number": f"НТ{tag}-{index:03d}",
        "date": (datetime(2026, 10, 1) + timedelta(days=index % 30)).strftime("%Y-%m-%d"),
        "route": f"{city_from} - {city_to}",
        "carrier_type": "ООО (с НДС)",
        "vat_rate": f"{vat_rate}%",
        "vat_rate_num": vat_rate,
        "price_without_vat": round(price, 2),
        "price_with_vat": round(price * (1 + vat_rate / 100), 2),
        "payment_days": 10,
    })

    payload.update({
        "driver": driver,
        "vehicles": vehicles,
        "contract": contract,
        "loadings": [{
            "address": f"{100000 + index}, г. {city_from}, "
                       f"ул. {STREETS[index % len(STREETS)]}, д. {1 + index % 50}",
            "date": (datetime(2026, 10, 2) + timedelta(days=index % 30)).strftime("%Y-%m-%d"),
            "time_window": "09:00-18:00",
        }],
        "unloadings": [{
            "address": f"г. {city_to}, ул. {STREETS[(index + 3) % len(STREETS)]}, "
                       f"д. {2 + index % 60}",
            "date": (datetime(2026, 10, 5) + timedelta(days=index % 30)).strftime("%Y-%m-%d"),
            "time_window": "",
        }],
    })
    return payload


def _db_contract_by_number(db_path: str, number: str) -> Optional[Dict[str, Any]]:
    """Запись `contracts` по номеру договора + связанные строки ТС."""
    try:
        connection = sqlite3.connect(db_path, timeout=20)
    except sqlite3.Error:
        return None
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, contract_number, contract_date, route, price_without_vat, "
            "price_with_vat, vat_rate FROM contracts WHERE contract_number = ? "
            "ORDER BY id DESC LIMIT 1",
            (number,),
        ).fetchone()
        if row is None:
            return None
        record = dict(row)
        vehicles = connection.execute(
            "SELECT vin, brand_model FROM vehicles WHERE contract_id = ?",
            (record["id"],),
        ).fetchall()
        record["vehicles"] = [dict(item) for item in vehicles]
        return record
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def run_generation(args, ctx: Dict[str, Any], log: StressLog,
                   memory: MemoryTracker) -> Dict[str, Any]:
    """
    Шаг 3: 100 итераций «заполнить форму → создать документы → сохранить».

    Каждый третий договор — пара со связанным типом (Формика / Логистикс):
    документ связанного типа создаётся настоящим механизмом зеркала.
    """
    log.section("ШАГ 3. ГЕНЕРАЦИЯ ДОКУМЕНТОВ И СОХРАНЕНИЕ В БАЗУ")

    result: Dict[str, Any] = {
        "enabled": True,
        "iterations": 0,
        "generated": 0,
        "linked_generated": 0,
        "files_found": 0,
        "db_rows": 0,
        "discrepancies": [],
        "by_mode": {},
        "failures": [],
        "validation": dict(ctx["validation"]),
        "sum_checks": 0,
        "sum_mismatches": 0,
        "vin_checks": 0,
        "vin_mismatches": 0,
        "seconds_total": 0.0,
    }

    manager = ctx["manager"]
    window = manager.window_for("perevozka")
    if window is None:
        result["enabled"] = False
        result["failures"].append("окно Экспедиторства не создано")
        log.error("Генерация ПРОПУЩЕНА: окно Экспедиторства не создано")
        return result

    _patch_validation(window, ctx["validation"])
    _patch_overwrite_question(window, ctx["validation"])
    for contract_type in GENERATION_MODES[1:]:
        target_window = manager.window_for(contract_type)
        if target_window is not None:
            _patch_validation(target_window, ctx["validation"])
            _patch_overwrite_question(target_window, ctx["validation"])

    from ui.main_window.contract import LinkedContractChoice  # noqa: PLC0415

    output_root = Path(ctx["output_dir"])
    tag = ctx["run_tag"]
    started_all = time.perf_counter()

    for index in range(args.iterations):
        mode = GENERATION_MODES[index % len(GENERATION_MODES)]
        iteration: Dict[str, Any] = {"index": index, "mode": mode, "problems": []}

        try:
            payload = _build_payload(index, mode, tag)
            number = payload["contract"]["number"]
            iteration["number"] = number

            # ── Зеркало-источник: заполняем форму Экспедиторства ──
            manager.switch_to("perevozka")
            window = manager.window_for("perevozka")
            _fill_main_window(window, payload)

            data = window._collect_data()
            data = window._check_driver_carrier_match(data)
            if data is None:
                iteration["problems"].append("перевозчики не совпали")
                raise RuntimeError("согласование перевозчика прервало создание")

            # Проверка данных валидатором ТИПА — как в кнопке «Создать
            # договор»: диалог подменён, но замечания считаются.
            from core.contracts.perevozka.validator import (   # noqa: PLC0415
                PerevozkaValidator,
            )

            report = PerevozkaValidator().check(data)
            if not window._confirm_validation(report):
                iteration["problems"].append("проверка данных не пройдена")
                raise RuntimeError("генерация отменена после проверки данных")

            path = Path(window.contract_generator.generate(data))
            iteration["path"] = str(path)
            result["generated"] += 1

            # ── Целевой документ (зеркало + сумма + генерация) ──
            linked_path: Optional[Path] = None
            if mode != MODE_ONLY:
                choice = LinkedContractChoice(
                    target_type=mode,
                    amount=float(payload["contract"]["price_with_vat"]),
                )
                try:
                    linked = window._create_linked_contract(
                        choice, source_window=window)
                    if linked.is_created():
                        linked_path = Path(linked.path)
                        iteration["linked_path"] = str(linked_path)
                        result["linked_generated"] += 1
                    else:
                        iteration["problems"].append(
                            f"связанный {mode}: {linked.reason}")
                except Exception as exc:                         # noqa: BLE001
                    iteration["problems"].append(
                        f"связанный {mode} упал: {type(exc).__name__}")

            # ── Логика кнопки «Сохранить в базу» ──
            window._on_save_to_db()

            # ── Проверки: файл, база, суммы, VIN ──
            if not path.is_file():
                iteration["problems"].append("DOCX не создан")
            else:
                size = path.stat().st_size
                iteration["size"] = size
                if size >= DOCX_MIN_BYTES:
                    result["files_found"] += 1
                else:
                    iteration["problems"].append(f"DOCX слишком мал ({size} Б)")
                try:
                    if path.parent.parent != output_root:
                        iteration["problems"].append(
                            "файл не в папке рейса внутри output")
                except (ValueError, OSError):
                    pass
                try:
                    text = _text_of_docx(path)
                except Exception as exc:                         # noqa: BLE001
                    text = ""
                    iteration["problems"].append(
                        f"DOCX не читается: {type(exc).__name__}")
                if text:
                    leaks = [mark for mark in TEMPLATE_LEAKS if mark in text]
                    if leaks:
                        iteration["problems"].append(
                            f"плейсхолдеры в DOCX: {leaks}")
                    iteration["docx_text"] = text
                else:
                    iteration["docx_text"] = ""

            record = _db_contract_by_number(ctx["db_copy"], number)
            if record is None:
                iteration["problems"].append("записи в contracts нет")
            else:
                result["db_rows"] += 1
                iteration["db_id"] = record["id"]
                expected_without = round(float(payload["contract"]["price_without_vat"]), 2)
                expected_with = round(float(payload["contract"]["price_with_vat"]), 2)
                actual_without = record.get("price_without_vat")
                actual_with = record.get("price_with_vat")
                result["sum_checks"] += 2
                if actual_without is None or abs(float(actual_without) - expected_without) > 0.01:
                    result["sum_mismatches"] += 1
                    iteration["problems"].append(
                        f"сумма без НДС в базе {actual_without} != {expected_without}")
                if actual_with is None or abs(float(actual_with) - expected_with) > 0.01:
                    result["sum_mismatches"] += 1
                    iteration["problems"].append(
                        f"сумма с НДС в базе {actual_with} != {expected_with}")

                db_vins = [str(item.get("vin") or "") for item in record["vehicles"]
                           if str(item.get("vin") or "").strip()]
                payload_vins = [item["vin"] for item in payload["vehicles"]]
                missing_in_db = [vin for vin in payload_vins if vin not in db_vins]
                result["vin_checks"] += len(payload_vins)
                if missing_in_db:
                    result["vin_mismatches"] += len(missing_in_db)
                    iteration["problems"].append(
                        f"VIN в базе не совпали: {len(missing_in_db)} из {len(payload_vins)}")

                text = iteration.get("docx_text") or ""
                if text:
                    compact = re.sub(r"\s+", "", text)
                    missing_in_docx = [vin for vin in db_vins if vin not in compact]
                    result["vin_checks"] += len(db_vins)
                    if missing_in_docx:
                        result["vin_mismatches"] += len(missing_in_docx)
                        iteration["problems"].append(
                            f"VIN в DOCX не совпали: {len(missing_in_docx)} из {len(db_vins)}")
                    digits = _digits(text)
                    if _money_digits(actual_without) and \
                            _money_digits(actual_without) not in digits:
                        result["sum_mismatches"] += 1
                        iteration["problems"].append("суммы без НДС в DOCX нет")
                    if _money_digits(actual_with) and \
                            _money_digits(actual_with) not in digits:
                        result["sum_mismatches"] += 1
                        iteration["problems"].append("суммы с НДС в DOCX нет")

            if linked_path is not None:
                if not linked_path.is_file():
                    iteration["problems"].append("связанный DOCX не создан")
                elif linked_path.parent != path.parent:
                    iteration["problems"].append(
                        "связанный DOCX в другой папке рейса")
                else:
                    result["files_found"] += 1
                    try:
                        linked_text = _text_of_docx(linked_path)
                        compact = re.sub(r"\s+", "", linked_text)
                        payload_vins = [item["vin"] for item in payload["vehicles"]]
                        lost = [vin for vin in payload_vins if vin not in compact]
                        if lost:
                            iteration["problems"].append(
                                f"VIN в связанном DOCX: потеряно {len(lost)}")
                    except Exception as exc:                     # noqa: BLE001
                        iteration["problems"].append(
                            f"связанный DOCX не читается: {type(exc).__name__}")

        except Exception as exc:                                 # noqa: BLE001
            iteration["problems"].append(f"итерация упала: {type(exc).__name__}: {str(exc)[:160]}")
            log.error(
                f"Итерация #{index}: {type(exc).__name__}: {str(exc)[:160]}"
            )
            log.error(traceback.format_exc().strip().splitlines()[-1])

        result["iterations"] += 1
        mode_bucket = result["by_mode"].setdefault(
            mode, {"iterations": 0, "problems": 0, "linked": 0})
        mode_bucket["iterations"] += 1
        if iteration.get("linked_path"):
            mode_bucket["linked"] += 1

        if iteration["problems"]:
            mode_bucket["problems"] += len(iteration["problems"])
            result["discrepancies"].append({
                "index": index,
                "number": iteration.get("number", ""),
                "mode": mode,
                "problems": iteration["problems"],
            })
            log.warn(
                f"[{index + 1:>3}/{args.iterations}] {number} — "
                f"{len(iteration['problems'])} проблем: "
                f"{'; '.join(iteration['problems'])[:200]}"
            )
        else:
            log.info(
                f"[{index + 1:>3}/{args.iterations}] {number} "
                f"({MODE_TITLES[mode]}) OK — DOCX {iteration.get('size', 0) // 1024} КБ, "
                f"база ID={iteration.get('db_id')}"
            )

        if (index + 1) % MEMORY_EVERY == 0:
            memory.sample(f"Генерация {index + 1} итераций")

    # ── Можно ли открыть базу прогона после сотни записей ──
    try:
        check = sqlite3.connect(ctx["db_copy"], timeout=20)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            contracts = check.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
        finally:
            check.close()
        result["integrity"] = integrity
        result["contracts_total"] = contracts
        log.info(f"База прогона: integrity={integrity}, договоров {contracts}")
        if integrity != "ok":
            result["discrepancies"].append({
                "index": -1, "number": "", "mode": "db",
                "problems": [f"PRAGMA integrity_check = {integrity}"],
            })
    except sqlite3.Error as exc:
        result["integrity"] = f"error: {exc}"
        log.error(f"База прогона не открывается после генерации: {exc}")

    result["validation"] = dict(ctx["validation"])
    result["seconds_total"] = round(time.perf_counter() - started_all, 2)
    log.info(
        f"Итог генерации: итераций {result['iterations']}, "
        f"документов {result['generated']}, связанных {result['linked_generated']}, "
        f"файлов в папках {result['files_found']}, записей в базе {result['db_rows']}, "
        f"расхождений {len(result['discrepancies'])}"
    )
    return result


# ─────────────────────────────────────────────────────────────
# Шаг 4: интерфейс — переключение типов и диалоги
# ─────────────────────────────────────────────────────────────

def _place_marker(window) -> Optional[Tuple[Any, str, str]]:
    """
    Кладёт маркер в первое строковое поле первой «словарной» вкладки окна.

    Возвращает (вкладка, поле, маркер). Смысл: после переключения окон
    маркер обязан остаться прежним — иначе данные формы теряются.
    """
    marker = f"NT-{window.CONTRACT_TYPE or 'тип'}"
    for key in window._tab_keys():
        tab = _tab_of(window, key)
        if tab is None or not hasattr(tab, "fill_data") or not hasattr(tab, "get_data"):
            continue
        data = _tab_data_of(window, tab)
        field = next((name for name, value in data.items()
                      if isinstance(value, str)), "")
        if not field:
            continue
        try:
            tab.fill_data({field: marker})
        except Exception:                                        # noqa: BLE001
            continue
        if str(_tab_data_of(window, tab).get(field, "")) == marker:
            return tab, field, marker
    return None


def _flush_deferred_deletes() -> None:
    """
    Выполняет отложенные удаления Qt-объектов (`deleteLater`).

    Зачем это нужно в инструменте: `QApplication.processEvents()` сам
    отложенные удаления НЕ выполняет — их делает только цикл событий
    (`exec_`). Программа в живом режиме объекты удаляет, а прогон без
    `exec_` копил бы закрытые диалоги и показывал ложную утечку памяти
    (проверено: без сброса +9 МБ на круг диалогов, с ним — +0.2 МБ).
    """
    try:
        from PyQt5.QtCore import QCoreApplication, QEvent  # noqa: PLC0415

        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    except Exception:                                            # noqa: BLE001
        pass


def _open_and_close_dialogs(window, app, log: StressLog) -> Dict[str, Any]:
    """
    Открывает и закрывает три диалога: «Справочник», «Настройки», «Импорт».

    Диалоги настоящие: конструктор читает данные, показ строит интерфейс.
    Закрываются без выполнения действий, как если бы оператор передумал.
    """
    outcome: Dict[str, Any] = {"ok": 0, "failed": 0, "errors": []}

    def _run(name: str, builder) -> None:
        try:
            dialog = builder()
            dialog.show()
            app.processEvents()
            dialog.close()
            dialog.deleteLater()
            app.processEvents()
            outcome["ok"] += 1
        except Exception as exc:                                 # noqa: BLE001
            outcome["failed"] += 1
            outcome["errors"].append(f"{name}: {type(exc).__name__}")
            log.warn(f"Диалог {name}: {type(exc).__name__}: {str(exc)[:140]}")

    from core.settings_service import get_settings_service    # noqa: PLC0415

    def _address_book():
        from ui.address_book_dialog import AddressBookDialog   # noqa: PLC0415

        return AddressBookDialog("loading", window)

    def _settings():
        from ui.settings_dialog import SettingsDialog          # noqa: PLC0415

        return SettingsDialog(get_settings_service().as_dict(), window)

    def _import():
        from ui.document_import_dialog import DocumentImportDialog  # noqa: PLC0415

        return DocumentImportDialog(window)

    _run("Справочник", _address_book)
    _run("Настройки", _settings)
    _run("Импорт", _import)
    return outcome


def run_ui(args, ctx: Dict[str, Any], log: StressLog,
           memory: MemoryTracker) -> Dict[str, Any]:
    """Шаг 4: 50 циклов переключения типов и открытие/закрытие диалогов."""
    log.section("ШАГ 4. ИНТЕРФЕЙС: ПЕРЕКЛЮЧЕНИЕ ТИПОВ И ДИАЛОГИ")

    result: Dict[str, Any] = {
        "enabled": True,
        "cycles": 0,
        "switches": 0,
        "switch_failures": 0,
        "data_losses": [],
        "dialogs": {"ok": 0, "failed": 0, "errors": []},
        "windows": [],
        "failures": [],
    }

    manager = ctx["manager"]
    app = ctx["app"]

    available = [contract_type for contract_type in SWITCH_ORDER
                 if manager.window_for(contract_type) is not None]
    result["windows"] = available
    log.info(
        "Окна для переключения: "
        + ", ".join(f"{TYPE_TITLES.get(t, t)}" for t in available)
    )
    missing = [t for t in SWITCH_ORDER if t not in available]
    if missing:
        log.warn(
            "Окна недоступны: " + ", ".join(TYPE_TITLES.get(t, t) for t in missing)
        )

    if len(available) < 2:
        result["enabled"] = False
        result["failures"].append("переключать нечего: доступно меньше двух типов")
        log.error("Переключение ПРОПУЩЕНО: " + result["failures"][0])
        return result

    # ── Маркеры данных: что должно пережить переключения ──
    markers: Dict[str, Tuple[Any, str, str]] = {}
    for contract_type in available:
        window = manager.window_for(contract_type)
        placed = _place_marker(window)
        if placed is None:
            log.warn(
                f"{TYPE_TITLES.get(contract_type, contract_type)}: "
                f"поле для маркера не найдено"
            )
        else:
            markers[contract_type] = placed
            log.info(
                f"{TYPE_TITLES.get(contract_type, contract_type)}: маркер в поле "
                f"{placed[1]!r}"
            )

    started_all = time.perf_counter()
    for cycle in range(args.cycles):
        for contract_type in available:
            window = manager.switch_to(contract_type)
            result["switches"] += 1
            if window is None:
                result["switch_failures"] += 1
                result["failures"].append(
                    f"цикл {cycle}: переключение на {contract_type} не удалось")
                continue
            if manager.current_type() != contract_type:
                result["switch_failures"] += 1
                result["failures"].append(
                    f"цикл {cycle}: текущий тип {manager.current_type()!r} "
                    f"вместо {contract_type!r}")
            app.processEvents()

        # ── Проверка сохранности данных после полного круга ──
        for contract_type, (tab, field, expected) in markers.items():
            window = manager.window_for(contract_type)
            if window is None:
                result["data_losses"].append(
                    f"цикл {cycle}: окно {contract_type} исчезло")
                continue
            actual = str(_tab_data_of(window, tab).get(field, ""))
            if actual != expected:
                result["data_losses"].append(
                    f"цикл {cycle}: {contract_type}.{field} = {actual!r}")
                log.warn(
                    f"Цикл {cycle}: потеряны данные {contract_type}.{field} "
                    f"({actual!r} вместо {expected!r})"
                )

        # ── Диалоги ──
        dialogs = _open_and_close_dialogs(
            manager.window_for("perevozka"), app, log)
        _flush_deferred_deletes()
        app.processEvents()
        result["dialogs"]["ok"] += dialogs["ok"]
        result["dialogs"]["failed"] += dialogs["failed"]
        result["dialogs"]["errors"].extend(dialogs["errors"])

        result["cycles"] += 1
        if (cycle + 1) % MEMORY_EVERY == 0:
            memory.sample(f"UI {cycle + 1} циклов")
            log.info(
                f"Циклов переключения: {cycle + 1}/{args.cycles}, "
                f"переключений {result['switches']}, "
                f"потерь данных {len(result['data_losses'])}"
            )

    result["seconds_total"] = round(time.perf_counter() - started_all, 2)
    log.info(
        f"Итог интерфейса: циклов {result['cycles']}, "
        f"переключений {result['switches']}, сбоев переключения "
        f"{result['switch_failures']}, потерь данных {len(result['data_losses'])}, "
        f"диалогов открыто {result['dialogs']['ok']} "
        f"(сбоев {result['dialogs']['failed']})"
    )
    return result


# ─────────────────────────────────────────────────────────────
# Шаг 5: проверка выхода (отдельные процессы)
# ─────────────────────────────────────────────────────────────

#: Сценарий выхода: поднимает настоящее окно offscreen и закрывает программу
#: штатным путём (`main._do_exit`). Код возврата процесса проверяет родитель.
EXIT_SCENARIO = r'''# -*- coding: utf-8 -*-
"""Сценарий выхода: поднимает программу и закрывает её штатным путём."""
import json
import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = sys.argv[1]
MODE = sys.argv[2]
PAYLOAD_PATH = sys.argv[3] if len(sys.argv) > 3 else ""

sys.path.insert(0, ROOT)

from PyQt5.QtCore import QTimer
import PyQt5.QtWidgets as QtWidgets
from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox

import ui.contract_picker as picker


def _auto_accept(self):
    self.select_type("perevozka")
    return QDialog.Accepted


picker.ContractPickerDialog.exec_ = _auto_accept

# Модальные окна в offscreen блокируют прогон — заменяем заглушками.
def _silent(*args, **kwargs):
    return QMessageBox.Ok


QMessageBox.information = staticmethod(_silent)
QMessageBox.warning = staticmethod(_silent)
QMessageBox.critical = staticmethod(_silent)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.exec_ = lambda self, *a, **k: 0

# `main.py` разбирает аргументы на импорте: свои аргументы сценария прячем.
sys.argv = [sys.argv[0]]
import main

_manager = {}
_original_receiver = main._make_signals_receiver


def _capturing_receiver(manager, app):
    _manager["value"] = manager
    return _original_receiver(manager, app)


main._make_signals_receiver = _capturing_receiver


def _window():
    manager = _manager.get("value")
    if manager is None:
        return None
    return manager.current_window()


def _fill(window, payload):
    window.driver_tab.fill_data(payload.get("driver") or {})
    window.carrier_tab.fill_data(payload.get("carrier") or {})
    window.customer_tab.fill_data(payload.get("customer") or {})
    window.trailer_tab.fill_data(payload.get("tractor") or {},
                                 payload.get("trailer") or {})
    window.vehicles_tab.fill_data(payload.get("vehicles") or [])
    window.contract_tab.fill_data(payload.get("contract") or {})


def _work():
    try:
        window = _window()
        if window is None:
            print("WORK-FAIL manager", flush=True)
        elif MODE == "recognition":
            from tools.make_text_scenarios import (SEEDS_PATH, load_seeds,
                                                   scenario_from_seed)
            seeds = load_seeds(SEEDS_PATH)
            scenario = scenario_from_seed(
                seeds["seeds"][0], seeds["scenarios"][0]["kind"],
                seeds["scenarios"][0]["level"])
            window._on_recognition_finished(
                scenario["ground_truth"], scenario["text"])
            print("WORK-OK recognition", flush=True)
        elif MODE == "generation":
            with open(PAYLOAD_PATH, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            _fill(window, payload)
            data = window._collect_data()
            path = window.contract_generator.generate(data)
            window._on_save_to_db()
            print("WORK-OK generation " + os.path.basename(str(path)), flush=True)
        elif MODE == "agent":
            widget = getattr(window, "agent_widget", None)
            if widget is not None:
                class SlowClient:
                    def complete_chat(self, messages=None, tools=None,
                                      temperature=0.2):
                        time.sleep(3.0)
                        return {"role": "assistant", "content": "Готово.",
                                "tool_calls": []}

                widget._client_factory = SlowClient
                widget.show_panel()
                widget.on_submit("Покажи, что открыто")
                print("WORK-OK agent", flush=True)
        else:
            print("WORK-OK clean", flush=True)
    except Exception as exc:
        print("WORK-FAIL " + type(exc).__name__ + ": " + str(exc)[:200], flush=True)
    QTimer.singleShot(150, _exit)


def _exit():
    main._do_exit(_manager.get("value"), QApplication.instance())


class StoppingApplication(QApplication):
    def exec_(self):
        QTimer.singleShot(400, _work)
        return super().exec_()


QtWidgets.QApplication = StoppingApplication

sys.exit(main.main())
'''


def _prepare_exit_data(work: Path, ctx: Dict[str, Any], log: StressLog) -> Path:
    """Отдельная папка данных для процессов выхода (чтобы не делить базу)."""
    data_root = work / "exit_data"
    (data_root / "config").mkdir(parents=True, exist_ok=True)
    (data_root / "logs").mkdir(parents=True, exist_ok=True)
    (data_root / "output").mkdir(parents=True, exist_ok=True)
    source = Path(ctx["working_db"])
    target = data_root / "contracts.db"
    if not target.is_file():
        _copy_database(source, target, log)
    settings_target = data_root / "config" / "settings.json"
    if not settings_target.is_file():
        _copy_operator_settings(source.parent, data_root, log)
    return data_root


def run_exit_tests(args, ctx: Dict[str, Any], log: StressLog,
                   work: Path) -> Dict[str, Any]:
    """
    Шаг 5: программа поднимается отдельным процессом и закрывается сама.

    Код возврата 0 — норма. 0xC0000005 (3221225477) — известное падение Qt
    при завершении интерпретатора; в консоль его не видно, поэтому код
    проверяет родитель.
    """
    log.section("ШАГ 5. ПРОВЕРКА ВЫХОДА (КОД ПРОЦЕССА)")

    result: Dict[str, Any] = {"enabled": True, "runs": [], "failures": []}

    script_path = work / "exit_scenario.py"
    script_path.write_text(EXIT_SCENARIO, encoding="utf-8")

    try:
        data_root = _prepare_exit_data(work, ctx, log)
    except Exception as exc:                                     # noqa: BLE001
        result["enabled"] = False
        result["failures"].append(f"данные для выхода не подготовлены: {exc}")
        log.error("Проверка выхода ПРОПУЩЕНА: " + result["failures"][0])
        return result

    payload_path = work / "exit_payload.json"
    try:
        payload = _build_payload(0, MODE_ONLY, ctx.get("run_tag", "00"))
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:                                     # noqa: BLE001
        log.warn(f"Данные для сценария выхода не собраны: {exc}")
        payload_path = Path("")

    environment = dict(os.environ)
    environment.update({
        "QT_QPA_PLATFORM": "offscreen",
        "LOGISTICS_DATA_ROOT": str(data_root),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(PROJECT_ROOT),
    })

    modes = [
        ("clean", "выход сразу после старта"),
        ("recognition", "выход после применения распознавания"),
        ("generation", "выход после генерации и сохранения"),
        ("agent", "выход после запроса к Максимилиану (известный риск)"),
    ]

    # Каждый сценарий повторяется `--exit-runs` раз: падение Qt при выходе
    # ПЛАВАЮЩЕЕ, одного запуска для вывода мало.
    plan = [(mode, title) for _round in range(max(1, args.exit_runs))
            for mode, title in modes]

    for mode, title in plan:
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [sys.executable, str(script_path), str(PROJECT_ROOT), mode,
                 str(payload_path)],
                cwd=str(PROJECT_ROOT),
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
            )
            code = completed.returncode
            output = (completed.stdout or "") + (completed.stderr or "")
            elapsed = round(time.perf_counter() - started, 2)
            crashed = code not in (0,)
            note = ""
            if code == ACCESS_VIOLATION or code == ACCESS_VIOLATION - 2 ** 32:
                note = "0xC0000005 (access violation)"
            elif code != 0:
                note = f"код {code}"

            ok_work = "WORK-OK" in output
            work_failed = [line for line in output.splitlines()
                           if line.startswith("WORK-FAIL")]

            run = {
                "mode": mode,
                "title": title,
                "returncode": code,
                "seconds": elapsed,
                "note": note,
                "work_ok": ok_work,
                "work_errors": work_failed[:3],
                "crash": crashed,
            }
            result["runs"].append(run)

            if crashed:
                result["failures"].append(
                    f"{title}: {note or code} (сценарий "
                    f"{'выполнен' if ok_work else 'не выполнен'})")
                log.error(f"Выход [{mode}]: {note or code} — ПАДЕНИЕ")
            elif not ok_work:
                result["failures"].append(f"{title}: сценарий не выполнился")
                log.warn(f"Выход [{mode}]: код 0, но сценарий не отработал")
            else:
                log.info(f"Выход [{mode}]: код 0 за {elapsed} с — норма")
        except subprocess.TimeoutExpired:
            result["runs"].append({
                "mode": mode, "title": title, "returncode": None,
                "seconds": round(time.perf_counter() - started, 2),
                "note": "таймаут 240 с", "work_ok": False,
                "work_errors": [], "crash": True,
            })
            result["failures"].append(f"{title}: процесс не завершился за 240 с")
            log.error(f"Выход [{mode}]: таймаут 240 с")
        except Exception as exc:                                 # noqa: BLE001
            result["failures"].append(
                f"{title}: запуск не удался — {type(exc).__name__}")
            log.error(f"Выход [{mode}]: {type(exc).__name__}: {str(exc)[:160]}")

    crashed = [run for run in result["runs"] if run.get("crash")]
    result["crashes"] = len(crashed)
    log.info(
        f"Итог выхода: запусков {len(result['runs'])}, "
        f"падений {len(crashed)}"
    )
    return result


# ─────────────────────────────────────────────────────────────
# Шаг 6: отчёт
# ─────────────────────────────────────────────────────────────

def _signed(value: float) -> str:
    """Подпись прироста: «+12.3» / «-4.5» (без «+-»)."""
    return f"+{value:.1f}" if value >= 0 else f"{value:.1f}"


def _memory_attribution(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Разбирает рост памяти на составляющие: разовый прогрев против утечки.

    Три источника роста, и они РАЗНЫЕ по смыслу:

      * первый десяток запросов в GigaChat поднимает модели обезличивания
        (`core/pseudonymizer.py` → Natasha + pymorphy) — единоразовые
        десятки мегабайт, это не утечка;
      * установившийся режим распознавания — рост на каждый запрос;
      * генерация DOCX — кэши `python-docx`/lxml и таблицы Qt.

    :return: `{"lines": [...], "steady": bool}` — готовые строки отчёта и
        признак «в установившемся режиме память не растёт».
    """
    by_label = {item.get("label"): item.get("rss_mb")
                for item in samples if item.get("label")}
    base = by_label.get("после подготовки (база)")
    first_ten = by_label.get("GigaChat 10 запросов")
    lines: List[str] = []
    steady = True

    if base is not None and first_ten is not None:
        delta = round(first_ten - base, 1)
        if delta >= 30:
            lines.append(
                f"Первые 10 запросов распознавания: **{_signed(delta)} МБ** — это "
                f"единоразовая загрузка моделей обезличивания (Natasha + "
                f"pymorphy в `core/pseudonymizer.py`), а не утечка."
            )
        else:
            lines.append(
                f"Первые 10 запросов распознавания: {_signed(delta)} МБ.")

    recognition_labels = [item for item in samples
                          if str(item.get("label", "")).startswith("GigaChat ")]
    if len(recognition_labels) >= 2 and first_ten is not None:
        last = recognition_labels[-1]
        delta = round(last["rss_mb"] - first_ten, 1)
        per_request = delta / max(1, len(recognition_labels) - 1) / 10
        count = str(last["label"]).replace("GigaChat ", "")
        lines.append(
            f"Установившийся режим распознавания ({count}): "
            f"**{_signed(delta)} МБ** от точки «10 запросов» "
            f"(около {per_request:.2f} МБ на запрос)."
        )
        if per_request > 1.0:
            steady = False

    generation_labels = [item for item in samples
                         if str(item.get("label", "")).startswith("Генерация ")]
    if len(generation_labels) >= 2:
        first = generation_labels[0]
        last = generation_labels[-1]
        delta = round(last["rss_mb"] - first["rss_mb"], 1)
        lines.append(
            f"Генерация документов: **{_signed(delta)} МБ** "
            f"между первой и последней точкой замера (кэши `python-docx`, "
            f"таблицы Qt)."
        )
        if delta > 120:
            steady = False

    ui_labels = [item for item in samples
                 if str(item.get("label", "")).startswith("UI ")]
    if len(ui_labels) >= 2:
        first = ui_labels[0]
        last = ui_labels[-1]
        delta = round(last["rss_mb"] - first["rss_mb"], 1)
        if abs(delta) <= 5:
            lines.append(
                f"Переключение типов и диалоги: **{_signed(delta)} МБ** "
                f"от первого замера до последнего — установившийся режим, "
                f"утечки нет."
            )
        else:
            steady = False
            lines.append(
                f"Переключение типов и диалоги: **{_signed(delta)} МБ** — "
                f"рост продолжается, стоит посмотреть отдельно."
            )
    return {"lines": lines, "steady": steady}


def _format_seconds(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.1f} с"


def _table(headers: List[str], rows: List[List[Any]]) -> str:
    """Markdown-таблица (пустые строки не печатаются)."""
    if not rows:
        return "_нет данных_"
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def _verdict_line(problems: List[str], notes: Optional[List[str]] = None) -> str:
    """Строка вердикта: провалы, замечания или «всё в порядке»."""
    if problems:
        return f"**ИТОГ: есть проблемы ({len(problems)})** — см. раздел «Сводка провалов»."
    if notes:
        return (f"**ИТОГ: провалов нет, есть замечания ({len(notes)})** — "
                f"см. «Замечания (не провалы)».")
    return "**ИТОГ: всё в порядке** — провалов нет."


def build_report(args, ctx: Dict[str, Any], sections: Dict[str, Any],
                 memory: Dict[str, Any], elapsed: float,
                 log: StressLog) -> Tuple[str, List[str]]:
    """
    Собирает markdown-отчёт и список провалов для консольной сводки.

    Никаких значений документов: только числа, имена полей и метки.
    """
    gigachat = sections.get("gigachat") or {}
    generation = sections.get("generation") or {}
    ui = sections.get("ui") or {}
    exit_tests = sections.get("exit") or {}
    problems: List[str] = []
    notes: List[str] = []

    memory_analysis = _memory_attribution(memory.get("samples") or [])

    if gigachat.get("enabled") is False:
        problems.append("Распознавание не выполнено: " + str(gigachat.get("abort_reason")))
    elif gigachat.get("failed"):
        problems.append(
            f"Распознавание: неудачных запросов {gigachat['failed']} "
            f"(429: {gigachat.get('rate_limited', 0)})"
        )
    if gigachat.get("ui_fill_failures"):
        problems.append(
            f"Распознавание: окно не приняло данных {gigachat['ui_fill_failures']} раз")
    if generation.get("discrepancies"):
        problems.append(
            f"Генерация: расхождений {len(generation['discrepancies'])}")
    if generation.get("enabled") is False:
        problems.append("Генерация не выполнена: "
                        + "; ".join(generation.get("failures", [])[:2]))
    if ui.get("enabled") is False:
        problems.append("Интерфейс не проверен: "
                        + "; ".join(ui.get("failures", [])[:2]))
    else:
        if ui.get("switch_failures"):
            problems.append(f"Интерфейс: сбоев переключения {ui['switch_failures']}")
        if ui.get("data_losses"):
            problems.append(f"Интерфейс: потерь данных {len(ui['data_losses'])}")
        if (ui.get("dialogs") or {}).get("failed"):
            problems.append(
                f"Интерфейс: диалогов со сбоем {ui['dialogs']['failed']}")
    if memory.get("leak_suspected") and not memory_analysis.get("steady"):
        problems.append(
            f"Память: рост {memory.get('growth_mb')} МБ "
            f"({memory.get('growth_percent')} %)")
    elif memory.get("leak_suspected"):
        notes.append(
            f"Память: суммарный рост {memory.get('growth_mb')} МБ — разовый "
            f"прогрев (модели обезличивания, кэши DOCX); в установившемся "
            f"режиме память не растёт, утечки нет.")
    if exit_tests.get("crashes"):
        problems.append(f"Выход: падений {exit_tests['crashes']}")
    if log.counters["errors"]:
        problems.append(f"Ошибок в журнале прогона: {log.counters['errors']}")

    lines: List[str] = []
    lines.append("# Ночной стресс-тест logistics_contractor")
    lines.append("")
    lines.append(f"**Дата:** {datetime.now():%Y-%m-%d %H:%M}")
    lines.append(f"**Прогон:** `{ctx['work']}`")
    lines.append(f"**Длительность:** {_format_seconds(elapsed)}")
    lines.append(f"**Режим:** {'быстрый (--quick)' if args.quick else 'полный'}")
    lines.append("")
    lines.append("## Вердикт")
    lines.append("")
    lines.append(_verdict_line(problems, notes))
    lines.append("")
    lines.append("### Ключевые числа")
    lines.append("")
    metrics_rows = [
        ["Запросов к GigaChat", gigachat.get("requests", 0)],
        ["  из них успешных", gigachat.get("ok", 0)],
        ["  из них с ошибкой", gigachat.get("failed", 0)],
        ["Средний ответ GigaChat", _format_seconds(gigachat.get("avg_seconds"))],
        ["Recall / precision (GigaChat)",
         f"{gigachat.get('metrics', {}).get('recall', 0):.3f} / "
         f"{gigachat.get('metrics', {}).get('precision', 0):.3f}"],
        ["Recall / precision без служебных полей",
         f"{gigachat.get('metrics', {}).get('recall', 0):.3f} / "
         f"{gigachat.get('metrics', {}).get('precision_core', 0):.3f}"],
        ["Договоров сгенерировано", generation.get("generated", 0)],
        ["  из них связанных", generation.get("linked_generated", 0)],
        ["DOCX в папках рейсов", generation.get("files_found", 0)],
        ["Записей в базе", generation.get("db_rows", 0)],
        ["Суммы сверены / расхождений",
         f"{generation.get('sum_checks', 0)} / {generation.get('sum_mismatches', 0)}"],
        ["VIN сверены / расхождений",
         f"{generation.get('vin_checks', 0)} / {generation.get('vin_mismatches', 0)}"],
        ["Расхождений DOCX/база", len(generation.get("discrepancies") or [])],
        ["Циклов переключения типов", ui.get("cycles", 0)],
        ["Потерь данных в UI", len(ui.get("data_losses") or [])],
        ["Пик памяти", f"{memory.get('peak_mb', '—')} МБ"],
        ["Рост памяти от базы", f"{memory.get('growth_mb', '—')} МБ"],
        ["Падений при выходе",
         f"{exit_tests.get('crashes', 0)} из {len(exit_tests.get('runs') or [])}"],
    ]
    lines.append(_table(["Метрика", "Значение"], metrics_rows))
    lines.append("")

    # ── Шаг 1 ──
    lines.append("## 1. Подготовка")
    lines.append("")
    db_info = ctx.get("db_info") or {}
    prep_rows = [
        ["Рабочая база", f"`{ctx.get('working_db')}`"],
        ["Копия для прогона", f"`{ctx.get('db_copy')}`"],
        ["Способ копирования", db_info.get("method", "—")],
        ["Размер копии", f"{db_info.get('size', 0) / 1024:.0f} КБ"],
        ["Таблиц в копии", db_info.get("tables", "—")],
        ["Договоров в копии на старте", db_info.get("contracts", "—")],
        ["Закрыто запущенных копий программы", len(ctx.get("killed") or [])],
        ["Ключ GigaChat",
         ("есть, формат верный" if (ctx.get("gigachat_key") or {}).get("format_ok")
          else ("ОТСУТСТВУЕТ" if not (ctx.get("gigachat_key") or {}).get("present")
                else "неверный формат"))],
        ["Типы договоров", ", ".join(ctx.get("known_types") or [])],
        ["Приватная часть", "да" if ctx.get("private_roots") else "нет (режим ядра)"],
        ["Рабочие данные оператора", "не изменялись (база только на чтение)"],
    ]
    lines.append(_table(["Пункт", "Значение"], prep_rows))
    lines.append("")

    # ── Шаг 2 ──
    lines.append("## 2. Распознавание текста (GigaChat)")
    lines.append("")
    if gigachat.get("enabled") is False:
        lines.append(f"**Шаг пропущен:** {gigachat.get('abort_reason')}")
        lines.append("")
    else:
        lines.append(
            f"Запросов: **{gigachat.get('requests', 0)}**, успешных "
            f"**{gigachat.get('ok', 0)}**, с ошибкой **{gigachat.get('failed', 0)}**. "
            f"Ответов 429: {gigachat.get('rate_limited', 0)}."
        )
        lines.append("")
        if gigachat.get("from_cache"):
            lines.append(
                f"Из них взято из кэша предыдущего прогона: "
                f"**{gigachat['from_cache']}** — для этих текстов сеть не "
                f"использовалась; скорость ответа и метрики показывают живой "
                f"ответ (он сохранён вместе с результатом)."
            )
            lines.append("")
        lines.append(
            f"Скорость ответа: среднее **{_format_seconds(gigachat.get('avg_seconds'))}**, "
            f"медиана {_format_seconds(gigachat.get('median_seconds'))}, "
            f"p95 {_format_seconds(gigachat.get('p95_seconds'))}, "
            f"минимум {_format_seconds(gigachat.get('min_seconds'))}, "
            f"максимум {_format_seconds(gigachat.get('max_seconds'))}."
        )
        lines.append("")
        metrics = gigachat.get("metrics") or {}
        level_rows = []
        for level in LEVELS:
            item = (gigachat.get("by_level") or {}).get(level)
            if item:
                level_rows.append([
                    level, item["documents"], item["recall"],
                    item["precision"], item["f1"]])
        lines.append("### Метрики по полям")
        lines.append("")
        lines.append(_table(
            ["Уровень", "Текстов", "Recall", "Precision", "F1"], level_rows))
        lines.append("")
        lines.append(
            f"Всего: recall **{metrics.get('recall', 0):.3f}**, "
            f"precision **{metrics.get('precision', 0):.3f}**, "
            f"precision без служебных подстановок промпта "
            f"**{metrics.get('precision_core', 0):.3f}**, "
            f"F1 **{metrics.get('f1', 0):.3f}** "
            f"(полей в эталоне {metrics.get('expected', 0)}, "
            f"верно {metrics.get('true_positive', 0)}, "
            f"пришло {metrics.get('found', 0)}, "
            f"из них служебных {metrics.get('service_filled', 0)})."
        )
        lines.append("")
        lines.append(
            "_Служебные подстановки промпта — `contract.vat_rate` (22 %), "
            "`contract.payment_days`, год выпуска `0`: модель обязана их "
            "возвращать, приложение их использует, поэтому в precision они "
            "выглядят лишними полями. Честная точность — `precision_core`._"
        )
        lines.append("")
        errors = gigachat.get("errors_by_kind") or {}
        if errors:
            lines.append("### Ошибки запросов")
            lines.append("")
            lines.append(_table(
                ["Причина", "Раз"],
                [[kind, count] for kind, count in sorted(errors.items())]))
            lines.append("")
        for title, key in (("Пропущено полей (топ)", "top_missed"),
                           ("Неверные значения (топ)", "top_wrong"),
                           ("Лишние поля (топ)", "top_extra")):
            items = gigachat.get(key) or []
            if items:
                lines.append(f"### {title}")
                lines.append("")
                lines.append(_table(
                    ["Поле", "Раз"],
                    [[name, count] for name, count in items]))
                lines.append("")
        lines.append(
            f"Окно «Распознавание данных»: сбоев применения "
            f"**{gigachat.get('ui_fill_failures', 0)}**."
        )
        lines.append("")

    # ── Шаг 3 ──
    lines.append("## 3. Генерация и сохранение")
    lines.append("")
    if generation.get("enabled") is False:
        lines.append("**Шаг пропущен:** "
                     + "; ".join(generation.get("failures") or []))
        lines.append("")
    else:
        lines.append(
            f"Итераций: **{generation.get('iterations', 0)}**, документов "
            f"**{generation.get('generated', 0)}**, связанных "
            f"**{generation.get('linked_generated', 0)}**, файлов в папках рейсов "
            f"**{generation.get('files_found', 0)}**, записей `contracts` "
            f"**{generation.get('db_rows', 0)}**."
        )
        lines.append("")
        mode_rows = []
        for mode in GENERATION_MODES:
            bucket = (generation.get("by_mode") or {}).get(mode)
            if bucket:
                mode_rows.append([
                    MODE_TITLES[mode], bucket["iterations"], bucket["linked"],
                    bucket["problems"]])
        lines.append(_table(
            ["Режим", "Итераций", "Связанных", "Проблем"], mode_rows))
        lines.append("")
        validation = generation.get("validation") or {}
        lines.append(
            f"Сверка с базой: сумм проверено **{generation.get('sum_checks', 0)}** "
            f"(расхождений {generation.get('sum_mismatches', 0)}), "
            f"VIN проверено **{generation.get('vin_checks', 0)}** "
            f"(расхождений {generation.get('vin_mismatches', 0)})."
        )
        lines.append("")
        lines.append(
            f"Проверка данных (валидатор типа): проверок {validation.get('checks', 0)}, "
            f"с замечаниями {validation.get('not_clean', 0)}, "
            f"предупреждений {validation.get('warnings', 0)}, "
            f"ошибок {validation.get('errors', 0)}. "
            f"Диалог подтверждения заменён заглушкой «создать» — замечания считались. "
            f"Вопросов зеркала «перезаписать окно?» было "
            f"{validation.get('overwrite_questions', 0)}, отвечено «Да»."
        )
        lines.append("")
        lines.append(
            f"Целостность базы после прогона: "
            f"**{generation.get('integrity', '—')}**, "
            f"договоров всего {generation.get('contracts_total', '—')}."
        )
        lines.append("")
        discrepancies = generation.get("discrepancies") or []
        if discrepancies:
            lines.append("### Расхождения")
            lines.append("")
            rows = []
            for item in discrepancies[:60]:
                rows.append([
                    item.get("index"), item.get("number") or "—",
                    item.get("mode", ""),
                    "; ".join(item.get("problems") or [])[:220]])
            lines.append(_table(
                ["#", "Номер", "Режим", "Проблемы"], rows))
            if len(discrepancies) > 60:
                lines.append("")
                lines.append(f"_Показаны первые 60 из {len(discrepancies)}._")
            lines.append("")

    # ── Шаг 4 ──
    lines.append("## 4. Интерфейс и переключение")
    lines.append("")
    if ui.get("enabled") is False:
        lines.append("**Шаг пропущен:** " + "; ".join(ui.get("failures") or []))
        lines.append("")
    else:
        lines.append(
            f"Циклов: **{ui.get('cycles', 0)}**, переключений "
            f"**{ui.get('switches', 0)}**, сбоев переключения "
            f"**{ui.get('switch_failures', 0)}**, потерь данных "
            f"**{len(ui.get('data_losses') or [])}**."
        )
        lines.append("")
        lines.append(
            "Окна в проверке: "
            + ", ".join(TYPE_TITLES.get(t, t) for t in (ui.get("windows") or []))
        )
        lines.append("")
        dialogs = ui.get("dialogs") or {}
        lines.append(
            f"Диалоги (Справочник, Настройки, Импорт): открыто/закрыто "
            f"**{dialogs.get('ok', 0)}**, сбоев **{dialogs.get('failed', 0)}**."
        )
        lines.append("")
        losses = ui.get("data_losses") or []
        if losses:
            lines.append("### Потери данных")
            lines.append("")
            lines.append(_table(["Что"], [[item] for item in losses[:40]]))
            lines.append("")

    # ── Шаг 5 ──
    lines.append("## 5. Память и выход")
    lines.append("")
    if memory.get("available") is False:
        lines.append("Замер памяти недоступен (psutil не установлен).")
    else:
        lines.append(
            f"RSS процесса: база замера (после подготовки) "
            f"**{memory.get('first_mb', '—')} МБ**, конец "
            f"**{memory.get('last_mb', '—')} МБ**, пик "
            f"**{memory.get('peak_mb', '—')} МБ**, рост "
            f"**{memory.get('growth_mb', 0)} МБ ({memory.get('growth_percent', 0)} %)**. "
            f"Точек замера: {len(memory.get('samples') or [])}."
        )
        lines.append("")
        if memory.get("leak_suspected") and not memory_analysis.get("steady"):
            lines.append(
                "**ВНИМАНИЕ: рост памяти выше порога "
                f"({MEMORY_GROWTH_MB:.0f} МБ / {MEMORY_GROWTH_PERCENT:.0f} %) — "
                "рост продолжается и в установившемся режиме, похоже на утечку.**"
            )
        elif memory.get("leak_suspected"):
            lines.append(
                "**Рост выше порога есть, но он РАЗОВЫЙ:** модели обезличивания "
                "и кэши DOCX. В установившемся режиме (последний прогон циклов) "
                "память не растёт — утечки не обнаружено. Разбор ниже."
            )
        else:
            lines.append("Утечки не обнаружено: рост в пределах порога.")
        lines.append("")
        lines.append(
            "_Замеры снимаются после `gc.collect()` и принудительного "
            "выполнения отложенных удалений Qt (`DeferredDelete`): без этого "
            "закрытые диалоги считались бы утечкой (+9 МБ на круг против "
            "реальных +0.2 МБ)._"
        )
        lines.append("")
        if gigachat.get("from_cache") and gigachat.get("enabled") is not False:
            lines.append(
                "_Поправка: распознавание в этом прогоне шло из кэша, поэтому "
                "модели обезличивания (Natasha + pymorphy) не поднимались. "
                "При живом прогоне первый десяток запросов добавляет "
                "~120-130 МБ единоразово — это не утечка._"
            )
            lines.append("")
        samples = memory.get("samples") or []
        if samples:
            step = max(1, len(samples) // 20)
            rows = [[item["at"], item["label"], item["rss_mb"]]
                    for item in samples[::step]]
            lines.append(_table(["Время", "Точка замера", "RSS, МБ"], rows))
            lines.append("")

        # Прирост по шагам: видно, на каком шаге память растёт.
        stage_samples = [item for item in (memory.get("samples") or [])
                         if item["label"].startswith("после шага ")
                         or item["label"].startswith("после подготовки")]
        if len(stage_samples) > 1:
            rows = []
            previous = None
            for item in stage_samples:
                delta = ("—" if previous is None
                         else _signed(round(item["rss_mb"] - previous, 1)))
                rows.append([item["label"], item["rss_mb"], delta])
                previous = item["rss_mb"]
            lines.append("### Прирост по шагам")
            lines.append("")
            lines.append(_table(["Точка замера", "RSS, МБ", "Прирост, МБ"], rows))
            lines.append("")

        # ── Разбор роста: модели обезличивания против установившегося режима ──
        attribution_lines = memory_analysis.get("lines") or []
        if attribution_lines:
            lines.append("### Из чего сложился рост")
            lines.append("")
            for item in attribution_lines:
                lines.append(f"- {item}")
            lines.append("")
    lines.append("### Выход")
    lines.append("")
    runs = exit_tests.get("runs") or []
    if runs:
        rows = []
        for run in runs:
            code = run.get("returncode")
            code_text = "0" if code == 0 else (
                f"{code} ({run.get('note')})" if run.get("note") else str(code))
            rows.append([
                run.get("title", ""), code_text,
                "да" if run.get("crash") else "нет",
                f"{run.get('seconds', 0)} с",
            ])
        lines.append(_table(
            ["Сценарий", "Код процесса", "Падение", "Время"], rows))
        lines.append("")
    if exit_tests.get("crashes"):
        lines.append(
            f"**Падений при выходе: {exit_tests['crashes']}.** "
            "0xC0000005 — известная проблема Qt при завершении интерпретатора "
            "(см. `docs/STATE.md`)."
        )
    else:
        lines.append("**Падений при выходе нет: все процессы завершились кодом 0.**")
    lines.append("")

    # ── Сводка провалов ──
    lines.append("## Сводка провалов")
    lines.append("")
    if problems:
        for item in problems:
            lines.append(f"- {item}")
    else:
        lines.append("Провалов нет.")
    lines.append("")
    if notes:
        lines.append("## Замечания (не провалы)")
        lines.append("")
        for item in notes:
            lines.append(f"- {item}")
        lines.append("")
    lines.append("## Журнал прогона")
    lines.append("")
    lines.append(
        f"Подробный журнал: `{Path(ctx['work']) / 'stress.log'}`. "
        f"Предупреждений в журнале: {log.counters['warnings']}, "
        f"ошибок: {log.counters['errors']}."
    )
    lines.append("")
    lines.append(
        "_Отчёт собран инструментом `tools/night_stress_test.py`. "
        "Персональных данных в нём нет: только числа, имена полей и метки._"
    )
    lines.append("")

    return "\n".join(lines), problems


def write_report(path: Path, content: str, log: StressLog) -> None:
    """Сохраняет отчёт: перезаписывает файл предыдущего прогона."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")
    log.info(f"Отчёт сохранён: {path} ({len(content)} знаков)")


# ─────────────────────────────────────────────────────────────
# Запуск
# ─────────────────────────────────────────────────────────────

def parse_args(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(
        description="Ночной стресс-тест всей программы (E2E + GigaChat + база)")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS,
                        help="итераций распознавания и генерации")
    parser.add_argument("--cycles", type=int, default=DEFAULT_CYCLES,
                        help="циклов переключения типов")
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE,
                        help="пауза между запросами GigaChat, с")
    parser.add_argument("--work-dir", default="",
                        help="папка прогона (по умолчанию tests/_tmp/night_stress_*)")
    parser.add_argument("--resume", action="store_true",
                        help="продолжить уже сделанные запросы GigaChat")
    parser.add_argument("--quick", action="store_true",
                        help="быстрый прогон для проверки инструмента")
    parser.add_argument("--no-gigachat", action="store_true",
                        help="без сети: шаг распознавания пропускается")
    parser.add_argument("--no-generation", action="store_true",
                        help="не генерировать договоры")
    parser.add_argument("--no-ui", action="store_true",
                        help="не переключать типы и не открывать диалоги")
    parser.add_argument("--no-exit", action="store_true",
                        help="не проверять код выхода отдельными процессами")
    parser.add_argument("--exit-runs", type=int, default=2,
                        help="сколько раз повторить каждую проверку выхода")
    parser.add_argument("--report", default=str(DEFAULT_REPORT),
                        help="куда сохранить отчёт")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass

    args = parse_args(argv)
    if args.quick:
        args.iterations = min(args.iterations, 6)
        args.cycles = min(args.cycles, 3)
        args.pause = min(args.pause, 1.0)

    started = time.perf_counter()
    work = _make_work_dir(args)
    log = StressLog(work / "stress.log")
    memory = MemoryTracker(log)
    sections: Dict[str, Any] = {}
    exit_code = 0

    log.section("НОЧНОЙ СТРЕСС-ТЕСТ LOGISTICS_CONTRACTOR")
    log.info(f"Папка прогона: {work}")
    log.info(
        f"Режим: {'быстрый' if args.quick else 'полный'} | "
        f"распознавание {args.iterations}, генерация {args.iterations}, "
        f"переключений {args.cycles} циклов | пауза {args.pause} с"
    )
    memory.sample("старт")

    try:
        ctx = prepare(args, work, log)
    except Exception as exc:                                     # noqa: BLE001
        log.error(f"Подготовка не удалась: {type(exc).__name__}: {exc}")
        log.error(traceback.format_exc())
        log.close()
        return 2

    # База для оценки роста памяти: Qt и окна уже подняты, дальше — работа.
    memory.sample("после подготовки (база)")
    memory.mark_baseline()

    steps = [
        ("gigachat", run_recognition, not args.no_gigachat),
        ("generation", run_generation, not args.no_generation),
        ("ui", run_ui, not args.no_ui),
    ]
    for name, function, enabled in steps:
        if not enabled:
            sections[name] = {"enabled": False,
                              "abort_reason": "шаг отключён ключом --no-*",
                              "failures": ["отключён ключом --no-*"]}
            log.info(f"Шаг «{name}» отключён ключом --no-*")
            continue
        try:
            sections[name] = function(args, ctx, log, memory)
        except Exception as exc:                                 # noqa: BLE001
            log.error(f"Шаг «{name}» упал: {type(exc).__name__}: {exc}")
            log.error(traceback.format_exc())
            sections[name] = {"enabled": False,
                              "failures": [f"{type(exc).__name__}: {str(exc)[:200]}"]}
        # Память по шагам: видно, какой шаг растёт, а какой стоит на месте.
        memory.sample(f"после шага {name}")

    memory.sample("конец основной части")
    memory_summary = memory.summary()

    if args.no_exit:
        sections["exit"] = {"enabled": False, "runs": [],
                            "failures": ["отключён ключом --no-exit"],
                            "crashes": 0}
    else:
        try:
            sections["exit"] = run_exit_tests(args, ctx, log, work)
        except Exception as exc:                                 # noqa: BLE001
            log.error(f"Проверка выхода упала: {type(exc).__name__}: {exc}")
            sections["exit"] = {"enabled": False, "runs": [],
                                "failures": [f"{type(exc).__name__}: {str(exc)[:200]}"],
                                "crashes": 0}

    elapsed = time.perf_counter() - started
    content, problems = build_report(args, ctx, sections, memory_summary,
                                     elapsed, log)

    log.section("ОТЧЁТ")
    for line in content.splitlines():
        if line.startswith("**") or line.startswith("- "):
            log.info(line)

    try:
        write_report(Path(args.report), content, log)
    except OSError as exc:
        log.error(f"Отчёт не сохранён: {exc}")
        exit_code = 1

    log.section("ИТОГ ПРОГОНА")
    log.info(f"Длительность: {_format_seconds(elapsed)}")
    log.info(f"Провалов: {len(problems)}")
    for item in problems:
        log.warn(item)
    if problems:
        exit_code = exit_code or 1

    # ── Закрытие Qt и выход ──
    try:
        manager = ctx.get("manager")
        if manager is not None:
            manager.close_all()
        app = ctx.get("app")
        if app is not None:
            _flush_deferred_deletes()
            gc.collect()
            app.processEvents()
            app.quit()
    except Exception as exc:                                     # noqa: BLE001
        log.warn(f"Закрытие окон при завершении: {type(exc).__name__}: {exc}")

    log.info("Готово. Отчёт: " + str(Path(args.report)))
    log.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
