#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Самодиагностика программы и сбор логов (задание, часть 3).

Зачем модуль
------------
Когда «что-то сломалось», оператор не может (и не должен) ходить по папкам и
собирать логи руками. Здесь живёт всё, что для этого нужно: проверки
состояния, сбор логов за последние дни, маскирование персональных данных и
упаковка отчёта в ZIP.

Правила, которые здесь соблюдаются
----------------------------------
1. **Только чтение.** Диагностика ничего не чинит и не меняет: ни базы, ни
   настроек, ни формы (задание: «не менять код программы — только
   диагностика»). Единственный файл, который создаётся, — ZIP с отчётом.
2. **Без ключей API.** Значения `*_api_key` и `*_credentials` в отчёт не
   попадают вовсе — ни целиком, ни частями.
3. **Без ПДн.** Отчёт и логи проходят через ``core/pseudonymizer.py``: ФИО,
   телефоны, паспорта, адреса заменяются плейсхолдерами (``<<PERSON_1>>``).
4. **Проверка не роняет программу.** Любая проверка при сбое возвращает
   статус «ошибка» с причиной, а не исключение: диагностика нужна как раз
   тогда, когда что-то уже сломано.

Проверки (задание, часть 3):
   * БД — целостность (PRAGMA integrity_check), размер, последние записи;
   * настройки — все ключи на месте, битых нет;
   * логи — свежие ошибки за последние дни;
   * GigaChat — доступность (есть ключ, проходит авторизация);
   * DaData — доступность (есть ключи);
   * память Максимилиана — размер и аномалии;
   * свободное место на диске;
   * версия программы.
"""

import logging
import os
import platform
import re
import shutil
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("core.diagnostics")

# ─────────────────────────────────────────────────────────────
# Версия программы
# ─────────────────────────────────────────────────────────────

#: Версия программы. Одно место: раньше её не было вовсе, и в отчёте
#: диагностики нечего было написать. Меняется вместе с выпуском.
APP_VERSION = "1.0.0"

#: Название программы (в отчёте и в теме письма разработчику).
APP_NAME = "Logistics Contractor"

#: Статусы проверок (✅ / ⚠ / ❌ из задания, часть B.1).
STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

#: Знак статуса для интерфейса и текстового отчёта.
STATUS_MARKS = {STATUS_OK: "OK", STATUS_WARN: "!", STATUS_FAIL: "X"}

#: Файлы логов, которые собираются в отчёт (задание, часть C.1:
#: «логи за последние 3 дня»).
LOG_FILES: Tuple[str, ...] = (
    "app.log", "errors.log", "audit.log", "debug.log",
)

#: Сколько дней логов забирать.
LOG_DAYS = 3

#: Размер базы и логов, после которого проверка даёт предупреждение.
DB_WARN_BYTES = 512 * 1024 * 1024          # 512 МБ
LOG_WARN_BYTES = 40 * 1024 * 1024          # 40 МБ
MEMORY_WARN_ROWS = 5000                    # записей памяти агента

#: Сколько свободного места считается нормой.
DISK_WARN_BYTES = 1024 * 1024 * 1024       # 1 ГБ

#: Предел на один файл лога в отчёте (логи бывают огромными).
LOG_PART_LIMIT_BYTES = 2 * 1024 * 1024

#: Сколько текста МАСКИРУЕТСЯ в одном файле отчёта. Обезличиватель —
#: не бесплатный: замер на живых логах дал ~4.3 с на 16 КБ (разбор текста
#: целиком), поэтому отчёт маскируется хвостом, а не файлом за месяц.
MASK_LIMIT_BYTES = 32 * 1024

#: Предел на ВСЕ логи вместе: они маскируются одним проходом (см.
#: build_archive) — так дорогой разбор текста делается один раз.
MASK_LOGS_TOTAL_BYTES = 64 * 1024

#: Сколько строк лога показывать в подробностях проверки.
LOG_TAIL_LINES = 400

#: Регулярка строки лога: «2026-10-15 21:33:04 | ...».
_LOG_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")

#: Регулярка «похоже на секрет»: длинные строки без пробелов, base64 и т.п.
_SECRET_VALUE_RE = re.compile(r"^[A-Za-z0-9+/=_-]{24,}$")


# ─────────────────────────────────────────────────────────────
# Результат одной проверки
# ─────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    """
    Одна проверка: что проверяли, что нашли, чем это грозит.

    :param key: машинный ключ проверки (для тестов);
    :param title: название для оператора;
    :param status: ok / warn / fail;
    :param summary: одна строка вывода;
    :param details: подробности (показываются по клику);
    """

    key: str
    title: str
    status: str
    summary: str
    details: List[str] = field(default_factory=list)

    @property
    def mark(self) -> str:
        """Знак статуса для таблицы («OK» / «!» / «X»)."""
        return STATUS_MARKS.get(self.status, "?")

    @property
    def ok(self) -> bool:
        """Проверка пройдена без замечаний."""
        return self.status == STATUS_OK

    def to_dict(self) -> Dict[str, Any]:
        """Проверка словарём — для интерфейса и отчёта."""
        return {
            "key": self.key, "title": self.title, "status": self.status,
            "mark": self.mark, "summary": self.summary,
            "details": list(self.details),
        }


@dataclass
class DiagnosticReport:
    """Итог диагностики: проверки, сведения о системе и пути к файлам."""

    checks: List[CheckResult] = field(default_factory=list)
    system: Dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    duration_ms: int = 0
    #: Куда сохранён ZIP (пусто — отчёт ещё не собирался).
    archive_path: str = ""
    #: Что не удалось положить в отчёт (файлы, проверки).
    problems: List[str] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        """Сколько проверок в каждом статусе."""
        result = {STATUS_OK: 0, STATUS_WARN: 0, STATUS_FAIL: 0}
        for check in self.checks:
            result[check.status] = result.get(check.status, 0) + 1
        return result

    @property
    def worst_status(self) -> str:
        """Худший статус из всех проверок (его и показывает кнопка)."""
        if any(check.status == STATUS_FAIL for check in self.checks):
            return STATUS_FAIL
        if any(check.status == STATUS_WARN for check in self.checks):
            return STATUS_WARN
        return STATUS_OK

    def summary_line(self) -> str:
        """Короткая строка для оператора: сколько чего нашли."""
        counts = self.counts()
        parts = [f"проверок: {len(self.checks)}"]
        if counts[STATUS_FAIL]:
            parts.append(f"ошибок: {counts[STATUS_FAIL]}")
        if counts[STATUS_WARN]:
            parts.append(f"предупреждений: {counts[STATUS_WARN]}")
        if not counts[STATUS_FAIL] and not counts[STATUS_WARN]:
            parts.append("всё в порядке")
        return ", ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        """Отчёт словарём (для интерфейса, тестов и отчёта-файла)."""
        return {
            "system": dict(self.system),
            "checks": [check.to_dict() for check in self.checks],
            "counts": self.counts(),
            "worst": self.worst_status,
            "summary": self.summary_line(),
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "archive": self.archive_path,
            "problems": list(self.problems),
            "version": APP_VERSION,
        }


# ─────────────────────────────────────────────────────────────
# Пути
# ─────────────────────────────────────────────────────────────

def project_root() -> str:
    """Корень проекта (рядом с пакетом core)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def logs_dir() -> str:
    """Папка логов (та же, что у config/logging_config.py — по умолчанию)."""
    return os.path.join(project_root(), "logs")


def database_path() -> str:
    """Путь к базе: у db.database (его подменяют тесты), иначе — по умолчанию."""
    try:
        import db.database as database

        return str(database.DB_PATH)
    except Exception as e:  # noqa: BLE001 — диагностика не должна падать
        logger.warning("Путь к базе не получен: %s", type(e).__name__)
        return os.path.join(project_root(), "contracts.db")


def settings_file() -> str:
    """Путь к файлу настроек (config/settings.json или переопределённый)."""
    try:
        from core.settings_service import default_settings_path

        return default_settings_path()
    except Exception:  # noqa: BLE001
        return os.path.join(project_root(), "config", "settings.json")


def desktop_path() -> str:
    """
    Рабочий стол оператора.

    Отдельная функция, потому что её подменяют тесты: писать отчёты в
    настоящий рабочий стол при прогоне нельзя.
    """
    home = os.path.expanduser("~")
    candidate = os.path.join(home, "Desktop")
    if os.path.isdir(candidate):
        return candidate
    #: OneDrive переносит рабочий стол: путь из USERPROFILE тогда неверен.
    for name in os.listdir(home) if os.path.isdir(home) else ():
        if name.lower().startswith("onedrive"):
            onedrive_desktop = os.path.join(home, name, "Desktop")
            if os.path.isdir(onedrive_desktop):
                return onedrive_desktop
    return home


def archive_name(moment: Optional[datetime] = None) -> str:
    """Имя файла отчёта: logistics_diagnostics_ГГГГММДД_ЧЧММСС.zip."""
    stamp = (moment or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"logistics_diagnostics_{stamp}.zip"


# ─────────────────────────────────────────────────────────────
# Проверка 1: база данных
# ─────────────────────────────────────────────────────────────

#: Таблицы, количество записей в которых показывает состояние базы.
COUNTED_TABLES: Tuple[str, ...] = (
    "carriers", "customers", "drivers", "contracts", "vehicles",
    "addresses", "agent_memory", "agent_history",
)


def check_database() -> CheckResult:
    """
    База: целостность, размер, количество записей, последние записи.

    Целостность проверяется тем же SQLite, что и работа с данными:
    ``PRAGMA integrity_check``. Ошибка чтения — это уже результат проверки
    (статус «ошибка»), а не исключение наружу.
    """
    path = database_path()
    details: List[str] = [f"Файл: {path}"]

    if not os.path.isfile(path):
        return CheckResult(
            key="database", title="База данных", status=STATUS_FAIL,
            summary="файл базы не найден",
            details=details + ["База создаётся при первом запуске программы."],
        )

    size = os.path.getsize(path)
    details.append(f"Размер: {_human_size(size)}")

    try:
        from db.connection import get_connection

        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            integrity = [row[0] for row in cursor.fetchall()]
            details.append("Целостность: " + ("ok" if integrity == ["ok"]
                                              else "; ".join(integrity[:5])))

            counts: Dict[str, int] = {}
            for table in COUNTED_TABLES:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    counts[table] = int(cursor.fetchone()[0])
                except Exception:  # noqa: BLE001 — таблицы может не быть
                    continue
            details.append("Записей: " + ", ".join(
                f"{name}={count}" for name, count in counts.items()
            ) or "Записей: нет данных")

            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "ORDER BY name"
            )
            tables = [row[0] for row in cursor.fetchall()]
            details.append(f"Таблиц: {len(tables)}")
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — база может быть занята
        logger.warning("База не проверена: %s", type(e).__name__)
        return CheckResult(
            key="database", title="База данных", status=STATUS_FAIL,
            summary=f"база не читается ({type(e).__name__})",
            details=details,
        )

    if integrity != ["ok"]:
        return CheckResult(
            key="database", title="База данных", status=STATUS_FAIL,
            summary="нарушена целостность базы",
            details=details,
        )

    if size >= DB_WARN_BYTES:
        return CheckResult(
            key="database", title="База данных", status=STATUS_WARN,
            summary=f"база большая: {_human_size(size)}",
            details=details + ["Файл не мешает работе, но растёт."],
        )

    return CheckResult(
        key="database", title="База данных", status=STATUS_OK,
        summary=f"{_human_size(size)}, целостность в порядке",
        details=details,
    )


# ─────────────────────────────────────────────────────────────
# Проверка 2: настройки
# ─────────────────────────────────────────────────────────────

#: Ключи настроек, без которых программа работает не полностью.
REQUIRED_SETTINGS: Tuple[str, ...] = (
    "provider", "gigachat_model", "gigachat_scope", "gigachat_timeout",
    "agent",
)

#: Ключи раздела агента, обязательные к наличию.
REQUIRED_AGENT_KEYS: Tuple[str, ...] = (
    "enabled", "name", "avatar", "mode", "hotkey", "max_history",
    "max_tool_steps",
)


def check_settings() -> CheckResult:
    """
    Настройки: файл читается, все ключи на месте, битых значений нет.

    Значения секретов в отчёт НЕ попадают: проверяется только их наличие
    (задание, часть 3, пункт C.4 — «настройки без ключей API»).
    """
    path = settings_file()
    details: List[str] = [f"Файл: {path}"]

    if not os.path.isfile(path):
        return CheckResult(
            key="settings", title="Настройки", status=STATUS_WARN,
            summary="файла настроек нет — работают значения по умолчанию",
            details=details,
        )

    if os.path.getsize(path) == 0:
        return CheckResult(
            key="settings", title="Настройки", status=STATUS_FAIL,
            summary="файл настроек пустой",
            details=details,
        )

    try:
        import json

        from core.settings_service import SettingsService

        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("ожидался объект JSON")
        #: Свой экземпляр сервиса на ЭТОТ файл, а не общий на процесс:
        #: диагностика обязана прочитать именно файл настроек (и не подменять
        #: настройки работающей программы своим кэшем). Значения сверяются с
        #: умолчаниями — так видно, каких ключей в файле нет.
        service = SettingsService(path)
        data = service.as_dict()
        present = set(raw)
    except Exception as e:  # noqa: BLE001 — битый JSON не должен ронять окно
        logger.warning("Настройки не прочитаны: %s", type(e).__name__)
        return CheckResult(
            key="settings", title="Настройки", status=STATUS_FAIL,
            summary=f"настройки не читаются ({type(e).__name__})",
            details=details + ["Проверьте файл: возможно, он повреждён."],
        )

    #: «Нет ключа» — это отсутствие его В ФАЙЛЕ: сервис дополняет настройки
    #: умолчаниями, и по его словарю пропажу не увидеть.
    missing = [key for key in REQUIRED_SETTINGS if key not in present]
    agent_present = raw.get("agent") if isinstance(raw.get("agent"), dict) else {}
    agent = data.get("agent") or {}
    missing_agent = [key for key in REQUIRED_AGENT_KEYS
                     if key not in agent_present]
    secrets = [key for key in present if SettingsService.is_secret_key(key)]

    details.append(f"Ключей в файле: {len(present)}")
    details.append(
        "Секретов в файле: " + (", ".join(secrets) if secrets else "нет")
    )
    details.append("Раздел «Максимилиан»: " + (
        f"включён={agent.get('enabled')}, образ={agent.get('avatar')}, "
        f"режим={agent.get('mode')}" if agent else "отсутствует"
    ))

    problems: List[str] = []
    if missing:
        problems.append("нет ключей: " + ", ".join(missing))
    if missing_agent:
        problems.append("в разделе агента нет: " + ", ".join(missing_agent))
    if secrets:
        problems.append("в файле лежат секреты: " + ", ".join(secrets))

    if problems:
        return CheckResult(
            key="settings", title="Настройки", status=STATUS_WARN,
            summary="; ".join(problems),
            details=details,
        )

    return CheckResult(
        key="settings", title="Настройки", status=STATUS_OK,
        summary=f"{len(data)} ключей, все обязательные на месте",
        details=details,
    )


# ─────────────────────────────────────────────────────────────
# Проверка 3: логи
# ─────────────────────────────────────────────────────────────

def read_log_tail(path: str, days: int = LOG_DAYS,
                  limit: int = LOG_PART_LIMIT_BYTES) -> str:
    """
    Последние строки файла лога за N дней.

    Читается ХВОСТ файла (не больше ``limit`` байт), а строки фильтруются по
    дате: логи с ротацией бывают на сотни мегабайт, и грузить их целиком в
    память ради отчёта нельзя. Строки без даты (продолжение traceback)
    остаются рядом со своей записью.
    """
    if not os.path.isfile(path):
        return ""

    size = os.path.getsize(path)
    threshold = datetime.now() - timedelta(days=max(1, int(days)))

    with open(path, "rb") as handle:
        if size > limit:
            handle.seek(size - limit)
            handle.readline()  # неполная первая строка не нужна
        raw = handle.read()

    text = raw.decode("utf-8", errors="replace")
    kept: List[str] = []
    for line in text.splitlines():
        match = _LOG_DATE_RE.match(line)
        if match:
            try:
                moment = datetime.strptime(
                    f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M:%S"
                )
            except ValueError:
                moment = None
            if moment is not None and moment < threshold:
                continue
        kept.append(line)

    #: Слишком длинный хвост тоже обрезаем: отчёт должен остаться письмом.
    if len(kept) > LOG_TAIL_LINES * 4:
        kept = kept[-LOG_TAIL_LINES * 4:]
    return "\n".join(kept)


def check_logs(directory: Optional[str] = None) -> CheckResult:
    """
    Логи: какие файлы есть, свежие ошибки за последние дни.

    Ошибкой это не считается: свежие ошибки в логе — повод посмотреть
    отчёт, а не «сломанная программа». Файлы, которых нет, просто
    перечисляются в подробностях.
    """
    directory = directory or logs_dir()
    details: List[str] = [f"Папка: {directory}"]
    if not os.path.isdir(directory):
        return CheckResult(
            key="logs", title="Логи", status=STATUS_WARN,
            summary="папки с логами нет",
            details=details,
        )

    found: List[str] = []
    errors: List[str] = []
    total = 0
    for name in LOG_FILES:
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        size = os.path.getsize(path)
        total += size
        found.append(f"{name} — {_human_size(size)}")
        tail = read_log_tail(path)
        for line in tail.splitlines():
            if re.search(r"\|\s*(ERROR|CRITICAL)\s*\|", line):
                errors.append(f"{name}: {line.strip()[:300]}")

    if not found:
        return CheckResult(
            key="logs", title="Логи", status=STATUS_WARN,
            summary="файлов логов нет",
            details=details,
        )

    details.append("Файлы: " + "; ".join(found))
    details.append(f"За {LOG_DAYS} дн.: {_human_size(total)}")
    if errors:
        details.append(f"Свежих ошибок: {len(errors)}")
        details.extend(errors[:20])

    if errors:
        return CheckResult(
            key="logs", title="Логи", status=STATUS_WARN,
            summary=f"свежих ошибок: {len(errors)} (см. подробности)",
            details=details,
        )
    if total >= LOG_WARN_BYTES:
        return CheckResult(
            key="logs", title="Логи", status=STATUS_WARN,
            summary=f"логи большие: {_human_size(total)}",
            details=details,
        )
    return CheckResult(
        key="logs", title="Логи", status=STATUS_OK,
        summary=f"{len(found)} файлов, свежих ошибок нет",
        details=details,
    )


# ─────────────────────────────────────────────────────────────
# Проверка 4: GigaChat
# ─────────────────────────────────────────────────────────────

def check_gigachat(live: bool = False) -> CheckResult:
    """
    GigaChat: есть ли ключ и проходит ли авторизация.

    :param live: True — сходить за токеном (это сетевой запрос, поэтому по
        умолчанию проверяется только наличие ключа и настройки TLS: кнопку
        диагностики оператор нажимает и тогда, когда сети нет вовсе).
    """
    details: List[str] = []
    message = ""

    try:
        from core.secrets_store import describe_key_state, get_gigachat_key

        key = get_gigachat_key() or ""
        has_key = bool(key.strip())
        details.append("Ключ: " + ("есть" if has_key else "нет"))
        details.append(f"Хранилище: {describe_key_state()}")
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            key="gigachat", title="GigaChat", status=STATUS_FAIL,
            summary=f"хранилище ключей недоступно ({type(e).__name__})",
            details=details,
        )

    try:
        from core.settings_service import get_settings_service

        service = get_settings_service()
        details.append(f"Модель: {service.get_str('gigachat_model')}")
        details.append(f"Таймаут: {service.get_int('gigachat_timeout')} с")
        details.append(
            "Проверка TLS: " + ("включена" if service.get_bool(
                "gigachat_verify_ssl", True) else "ВЫКЛЮЧЕНА")
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Настройки GigaChat не прочитаны: %s", type(e).__name__)

    if not has_key:
        return CheckResult(
            key="gigachat", title="GigaChat", status=STATUS_FAIL,
            summary="ключа нет — распознавание и Максимилиан не работают",
            details=details + ["Запустите set_key.py и вставьте ключ."],
        )

    if not live:
        return CheckResult(
            key="gigachat", title="GigaChat", status=STATUS_OK,
            summary="ключ есть, настройки на месте",
            details=details + ["Авторизация не проверялась (нужна сеть)."],
        )

    # ── Живая проверка: запрос токена ──
    started = time.monotonic()
    try:
        from core.gigachat_client import GigaChatClient

        client = GigaChatClient()
        token = client._get_token()
        message = f"авторизация прошла за {time.monotonic() - started:.1f} с"
        details.append("Токен получен")
        status = STATUS_OK
    except Exception as e:  # noqa: BLE001 — сеть бывает недоступна
        message = f"авторизация не прошла ({type(e).__name__})"
        details.append("Проверьте интернет и срок действия ключа.")
        status = STATUS_FAIL

    return CheckResult(
        key="gigachat", title="GigaChat", status=status,
        summary=message, details=details,
    )


# ─────────────────────────────────────────────────────────────
# Проверка 5: DaData
# ─────────────────────────────────────────────────────────────

def check_dadata(live: bool = False) -> CheckResult:
    """
    DaData: есть ли ключ (и, если попросили, отвечает ли сервис).

    DaData — вспомогательный сервис: без него проверка адресов и поиск
    организации по названию недоступны, но программа работает.
    """
    details: List[str] = []
    try:
        from core.secrets_store import (
            describe_dadata_key_state, get_dadata_key, get_dadata_secret,
        )

        key = (get_dadata_key() or "").strip()
        secret = (get_dadata_secret() or "").strip()
        details.append("Ключ: " + ("есть" if key else "нет"))
        details.append("Секретный ключ: " + ("есть" if secret else "нет"))
        details.append(f"Хранилище: {describe_dadata_key_state()}")
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            key="dadata", title="DaData", status=STATUS_WARN,
            summary=f"ключи не прочитаны ({type(e).__name__})",
            details=details,
        )

    if not key:
        return CheckResult(
            key="dadata", title="DaData", status=STATUS_WARN,
            summary="ключа нет — проверка реквизитов и адресов недоступна",
            details=details,
        )

    if not live:
        return CheckResult(
            key="dadata", title="DaData", status=STATUS_OK,
            summary="ключ есть",
            details=details + ["Обращение к сервису не проверялось."],
        )

    started = time.monotonic()
    try:
        from core.dadata_client import DadataClient

        client = DadataClient()
        client.find_party_by_inn("7707083893")  # ПАО Сбербанк: публичные данные
        return CheckResult(
            key="dadata", title="DaData", status=STATUS_OK,
            summary=f"сервис отвечает ({time.monotonic() - started:.1f} с)",
            details=details,
        )
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            key="dadata", title="DaData", status=STATUS_WARN,
            summary=f"сервис не ответил ({type(e).__name__})",
            details=details,
        )


# ─────────────────────────────────────────────────────────────
# Проверка 6: память Максимилиана
# ─────────────────────────────────────────────────────────────

#: Сколько записей алиасов считается аномалией (дубли, ошибки обучения).
MEMORY_WARN_ROWS = 2000


def check_agent_memory() -> CheckResult:
    """
    Память Максимилиана: сколько записей, нет ли аномалий.

    Аномалии, которые ищутся: дубли ключей алиасов, ключ-команда («заполни»)
    вместо названия, ключ длиной в целую фразу. Это следы ошибок обучения, и
    по ним видно, что память «замусорена».
    """
    details: List[str] = []
    try:
        from db.connection import get_connection

        conn = get_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT COUNT(*) FROM agent_memory")
                rows = int(cursor.fetchone()[0])
            except Exception:  # noqa: BLE001 — таблицы может не быть
                return CheckResult(
                    key="agent_memory", title="Память Максимилиана",
                    status=STATUS_WARN, summary="таблиц памяти нет",
                    details=details + ["Память создаётся при первом запуске."],
                )
            try:
                cursor.execute(
                    "SELECT COUNT(*) FROM agent_history"
                )
                history = int(cursor.fetchone()[0])
            except Exception:  # noqa: BLE001
                history = 0

            cursor.execute(
                "SELECT kind, COUNT(*) FROM agent_memory GROUP BY kind"
            )
            by_kind = {str(row[0]): int(row[1]) for row in cursor.fetchall()}

            bad_keys: List[str] = []
            try:
                from core.agent.words import NON_NAME_WORDS

                cursor.execute("SELECT key FROM agent_memory WHERE kind='alias'")
                for (key,) in cursor.fetchall():
                    text = str(key or "").strip().casefold()
                    if not text:
                        continue
                    if text in NON_NAME_WORDS or len(text.split()) > 6:
                        bad_keys.append(str(key))
            except Exception as e:  # noqa: BLE001
                logger.debug("Аномалии памяти не проверены: %s", type(e).__name__)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            key="agent_memory", title="Память Максимилиана",
            status=STATUS_WARN,
            summary=f"память не проверена ({type(e).__name__})",
            details=details,
        )

    details.append("Записей памяти: " + str(rows))
    details.append("Сообщений диалога: " + str(history))
    if by_kind:
        details.append("По видам: " + ", ".join(
            f"{kind}={count}" for kind, count in sorted(by_kind.items())
        ))
    if bad_keys:
        details.append(f"Подозрительных ключей: {len(bad_keys)}")
        details.extend(bad_keys[:10])

    if bad_keys:
        return CheckResult(
            key="agent_memory", title="Память Максимилиана",
            status=STATUS_WARN,
            summary=f"подозрительных ключей: {len(bad_keys)}",
            details=details,
        )
    if rows >= MEMORY_WARN_ROWS:
        return CheckResult(
            key="agent_memory", title="Память Максимилиана",
            status=STATUS_WARN, summary=f"записей много: {rows}",
            details=details,
        )
    return CheckResult(
        key="agent_memory", title="Память Максимилиана", status=STATUS_OK,
        summary=f"{rows} записей, аномалий нет", details=details,
    )


# ─────────────────────────────────────────────────────────────
# Проверка 7: диск
# ─────────────────────────────────────────────────────────────

def check_disk(path: Optional[str] = None) -> CheckResult:
    """Свободное место на диске, где лежат база, логи и отчёты."""
    target = path or project_root()
    details: List[str] = [f"Диск: {os.path.splitdrive(target)[0] or target}"]
    try:
        usage = shutil.disk_usage(target)
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            key="disk", title="Свободное место", status=STATUS_WARN,
            summary=f"место не проверено ({type(e).__name__})",
            details=details,
        )

    details.append(f"Всего: {_human_size(usage.total)}")
    details.append(f"Свободно: {_human_size(usage.free)}")
    details.append(f"Занято: {usage.used * 100 // max(1, usage.total)} %")

    if usage.free < DISK_WARN_BYTES:
        return CheckResult(
            key="disk", title="Свободное место", status=STATUS_FAIL,
            summary=f"мало места: {_human_size(usage.free)}",
            details=details + ["Освободите место: база и логи растут."],
        )
    if usage.free < 5 * DISK_WARN_BYTES:
        return CheckResult(
            key="disk", title="Свободное место", status=STATUS_WARN,
            summary=f"свободно {_human_size(usage.free)}",
            details=details,
        )
    return CheckResult(
        key="disk", title="Свободное место", status=STATUS_OK,
        summary=f"свободно {_human_size(usage.free)}", details=details,
    )


# ─────────────────────────────────────────────────────────────
# Проверка 8: версия программы и окружение
# ─────────────────────────────────────────────────────────────

def system_info() -> Dict[str, Any]:
    """
    Сведения о системе для отчёта (задание, часть C.2).

    Ключей API и путей с логином пользователя здесь нет: в отчёт уходит
    только то, что нужно разработчику для разбора.
    """
    info: Dict[str, Any] = {
        "version": APP_VERSION,
        "app": APP_NAME,
        "python": platform.python_version(),
        "python_bits": 64 if sys.maxsize > 2 ** 32 else 32,
        "os": f"{platform.system()} {platform.release()}",
        "os_version": platform.version(),
        "machine": platform.machine(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    try:
        from PyQt5.QtCore import QT_VERSION_STR, PYQT_VERSION_STR

        info["qt"] = QT_VERSION_STR
        info["pyqt"] = PYQT_VERSION_STR
    except Exception:  # noqa: BLE001 — Qt может быть не установлен
        info["qt"] = "нет"
        info["pyqt"] = "нет"

    try:
        usage = shutil.disk_usage(project_root())
        info["disk_free"] = _human_size(usage.free)
        info["disk_total"] = _human_size(usage.total)
    except Exception:  # noqa: BLE001
        pass

    db = database_path()
    info["db_size"] = _human_size(os.path.getsize(db)) \
        if os.path.isfile(db) else "нет файла"
    return info


def check_version() -> CheckResult:
    """Версия программы и окружение — справочная проверка (всегда OK)."""
    info = system_info()
    details = [f"{key}: {value}" for key, value in info.items()]
    return CheckResult(
        key="version", title="Версия программы", status=STATUS_OK,
        summary=f"{info['version']} · Python {info['python']} · "
                f"Qt {info['qt']} · {info['os']}",
        details=details,
    )


# ─────────────────────────────────────────────────────────────
# Все проверки вместе
# ─────────────────────────────────────────────────────────────

#: Порядок проверок в интерфейсе (стабильный — по нему идут тесты).
CHECK_ORDER: Tuple[str, ...] = (
    "database", "settings", "logs", "gigachat", "dadata",
    "agent_memory", "disk", "version",
)


def check_factories(live: bool = False) -> Dict[str, Callable[[], CheckResult]]:
    """Список проверок по ключам (одно место — и для запуска, и для тестов)."""
    return {
        "database": check_database,
        "settings": check_settings,
        "logs": check_logs,
        "gigachat": lambda: check_gigachat(live=live),
        "dadata": lambda: check_dadata(live=live),
        "agent_memory": check_agent_memory,
        "disk": check_disk,
        "version": check_version,
    }


def run_diagnostics(live_network: bool = False,
                    progress: Optional[Callable[[int, int, str], None]] = None,
                    checks: Sequence[str] = CHECK_ORDER,
                    factories: Optional[Dict[str, Callable[[], CheckResult]]] = None
                    ) -> DiagnosticReport:
    """
    Проводит все проверки и возвращает отчёт (задание, часть B.2).

    :param live_network: проверять ли GigaChat и DaData сетевыми запросами;
    :param progress: обратный вызов «(шаг, всего, название)» — по нему
        диалог двигает полосу прогресса;
    :param checks: какие проверки выполнять (по умолчанию все);
    :param factories: свой набор проверок (нужен тестам: подменить одну
        проверку, не трогая остальные).
    """
    report = DiagnosticReport(started_at=datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"))
    available = factories if factories is not None \
        else check_factories(live=live_network)
    started = time.monotonic()

    for step, key in enumerate(checks, start=1):
        factory = available.get(key)
        if factory is None:
            continue
        if progress is not None:
            try:
                progress(step, len(checks), key)
            except Exception as e:  # noqa: BLE001 — интерфейс не должен ломать
                logger.debug("Полоса прогресса не обновлена: %s", type(e).__name__)
        try:
            report.checks.append(factory())
        except Exception as e:  # noqa: BLE001 — проверка не должна ронять окно
            logger.exception("Проверка %s упала", key)
            report.checks.append(CheckResult(
                key=key, title=key, status=STATUS_FAIL,
                summary=f"проверка не выполнена ({type(e).__name__})",
            ))

    report.system = system_info()
    report.duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "Диагностика: %s (за %s мс)", report.summary_line(),
        report.duration_ms,
    )
    return report


# ─────────────────────────────────────────────────────────────
# Маскирование: ПДн и ключи
# ─────────────────────────────────────────────────────────────

#: Как секреты выглядят в отчёте.
SECRET_PLACEHOLDER = "‹скрыто›"


def mask_secrets(text: str) -> str:
    """
    Убирает из текста значения секретов (задание, часть D.3).

    Ищутся строки вида ``"gigachat_api_key": "…"`` и любые длинные значения
    без пробелов у ключей, похожих на секрет. Сам текст секрета в отчёт не
    попадает НИКОГДА — даже частично.
    """
    if not text:
        return ""

    #: JSON-вид: "ключ": "значение".
    def json_replacer(match: "re.Match") -> str:
        return f'{match.group(1)}: "{SECRET_PLACEHOLDER}"'

    text = re.sub(
        r'("?[\w-]*(?:api_key|credentials|secret|password|token)[\w-]*"?)\s*:\s*"([^"]*)"',
        json_replacer, text, flags=re.IGNORECASE,
    )
    #: Вид «ключ = значение».
    text = re.sub(
        r"([\w-]*(?:api_key|credentials|secret|password|token)[\w-]*)\s*[=:]\s*(\S+)",
        lambda match: f"{match.group(1)} = {SECRET_PLACEHOLDER}",
        text, flags=re.IGNORECASE,
    )
    return text


def mask_personal_data(text: str, mask_addresses: bool = True,
                       limit: int = MASK_LIMIT_BYTES) -> Tuple[str, int]:
    """
    Заменяет персональные данные плейсхолдерами (задание, часть D.2).

    Используется тот же обезличиватель, что и при отправке текста в модель
    (``core/pseudonymizer.py``) — второго понимания ПДн в проекте быть не
    должно. Ошибка маскирования не отдаёт текст как есть: в этом случае
    возвращается пустая строка (лучше пустой отчёт, чем отчёт с паспортом).

    Маскируется ХВОСТ текста длиной не больше ``limit``: обезличиватель
    разбирает текст целиком, а логи бывают на мегабайты. Обрезка идёт от
    начала (хвост важнее: в нём свежие события).

    :return: ``(замаскированный текст, сколько замен)``.
    """
    source = str(text or "")
    if not source.strip():
        return "", 0

    clipped = ""
    if len(source) > limit:
        clipped = (f"… (начало файла опущено: "
                   f"{_human_size(len(source))} целиком)\n")
        source = source[-limit:]
        #: Разрез по границе строки: обрывок строки читать нельзя.
        source = source.split("\n", 1)[-1]

    try:
        from core.pseudonymizer import Pseudonymizer

        anonymizer = Pseudonymizer(mask_addresses=mask_addresses)
        masked, mapping = anonymizer.anonymize(source)
        return clipped + masked, len(mapping)
    except Exception as e:  # noqa: BLE001 — маскирование обязательно
        logger.error("Маскирование отчёта не удалось: %s", type(e).__name__)
        return "", 0


# ─────────────────────────────────────────────────────────────
# Текстовые файлы отчёта
# ─────────────────────────────────────────────────────────────

def report_text(report: DiagnosticReport) -> str:
    """Отчёт о проверках в текстовом виде (diagnostics_report.txt)."""
    lines: List[str] = [
        f"{APP_NAME} — отчёт диагностики",
        "=" * 60,
        f"Когда: {report.started_at}",
        f"Длительность: {report.duration_ms} мс",
        f"Итог: {report.summary_line()}",
        "",
    ]

    for check in report.checks:
        lines.append(f"[{check.mark}] {check.title}: {check.summary}")
        for detail in check.details:
            lines.append(f"      {detail}")
        lines.append("")

    if report.problems:
        lines.append("Чего не хватило в отчёте:")
        lines.extend(f"  - {problem}" for problem in report.problems)
        lines.append("")

    lines.append("Проверки ничего не меняют: это только чтение.")
    return "\n".join(lines)


def system_text(info: Optional[Dict[str, Any]] = None) -> str:
    """Сведения о системе в текстовом виде (system_info.txt)."""
    data = info if info is not None else system_info()
    lines = [f"{APP_NAME} — сведения о системе", "=" * 60, ""]
    titles = {
        "version": "Версия программы", "app": "Программа",
        "python": "Python", "python_bits": "Разрядность",
        "qt": "Qt", "pyqt": "PyQt", "os": "Операционная система",
        "os_version": "Сборка ОС", "machine": "Архитектура",
        "frozen": "Собранная версия", "disk_free": "Свободно на диске",
        "disk_total": "Всего на диске", "db_size": "Размер базы",
    }
    for key, value in data.items():
        lines.append(f"{titles.get(key, key)}: {value}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Сборка ZIP
# ─────────────────────────────────────────────────────────────

def build_archive(report: DiagnosticReport, target_dir: Optional[str] = None,
                  moment: Optional[datetime] = None,
                  log_dir: Optional[str] = None,
                  mask: bool = True) -> str:
    """
    Собирает отчёт в ZIP (задание, части D.1–D.4).

    Состав архива:
        diagnostics_report.txt — что проверяли и что нашли;
        system_info.txt        — версия, Python, Qt, ОС, место на диске;
        logs/*.log             — логи за последние дни (замаскированные).

    :param target_dir: куда положить архив (по умолчанию рабочий стол);
    :param mask: маскировать ли ПДн (выключается только тестами).
    :return: путь к созданному архиву.
    """
    directory = target_dir or desktop_path()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, archive_name(moment))

    report.problems = []
    report_path = report_text(report)
    info_path = system_text(report.system)

    #: Логи собираются заранее: их маскируют ОДНИМ проходом (см. ниже).
    source = log_dir or logs_dir()
    logs: Dict[str, str] = {}
    for name in LOG_FILES:
        log_path = os.path.join(source, name)
        if not os.path.isfile(log_path):
            continue
        tail = read_log_tail(log_path)
        if tail.strip():
            logs[name] = tail

    if mask:
        report_path, replaced = mask_personal_data(report_path)
        info_path, info_replaced = mask_personal_data(info_path,
                                                      mask_addresses=False)
        masked_logs, log_replaced = _mask_logs(logs)
        logs = masked_logs
        if replaced or info_replaced or log_replaced:
            logger.info("Отчёт: замаскировано значений %s",
                        replaced + info_replaced + log_replaced)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics_report.txt",
                         mask_secrets(report_path) + "\n")
        archive.writestr("system_info.txt", mask_secrets(info_path) + "\n")

        for name, text in logs.items():
            archive.writestr(f"logs/{name}", mask_secrets(text) + "\n")

        archive.writestr(
            "README.txt",
            "\n".join([
                f"{APP_NAME} — отчёт диагностики",
                "",
                "Что внутри:",
                "  diagnostics_report.txt — результаты проверок;",
                "  system_info.txt — версия программы и окружение;",
                "  logs/ — логи за последние дни.",
                "",
                "Персональные данные заменены плейсхолдерами",
                "(например, <<PERSON_1>>), ключи доступа удалены.",
                "",
                "Отчёт создан автоматически, программа при этом",
                "ничего не меняла.",
            ]) + "\n",
        )

    report.archive_path = path
    logger.info("Отчёт диагностики собран: %s", os.path.basename(path))
    return path


# ─────────────────────────────────────────────────────────────
# Вспомогательное
# ─────────────────────────────────────────────────────────────

def _mask_logs(logs: Dict[str, str]) -> Tuple[Dict[str, str], int]:
    """
    Маскирует логи одним проходом обезличивателя.

    Почему одним: обезличиватель разбирает текст целиком, и вызов на каждый
    файл умножал бы время сборки отчёта на их число (в рабочей папке лежат
    и app.log, и errors.log, и audit.log). Логи склеиваются с заголовками,
    маскируются вместе и разбираются обратно по тем же заголовкам.

    :return: ``(логи, сколько замен)``.
    """
    if not logs:
        return {}, 0

    marker = "\n### FILE: %s ###\n"
    combined = "".join(marker % name + text for name, text in logs.items())
    if len(combined) > MASK_LOGS_TOTAL_BYTES:
        combined = combined[-MASK_LOGS_TOTAL_BYTES:]

    masked, replaced = mask_personal_data(combined)

    result: Dict[str, str] = {}
    current: Optional[str] = None
    buffer: List[str] = []
    for line in masked.splitlines():
        match = re.match(r"^### FILE: (.+?) ###$", line.strip())
        if match:
            if current:
                result[current] = "\n".join(buffer).strip()
            current = match.group(1)
            buffer = []
            continue
        if current:
            buffer.append(line)
    if current:
        result[current] = "\n".join(buffer).strip()

    #: Маскирование не удалось (пустой результат) — логи не кладём вовсе:
    #: лучше отчёт без логов, чем отчёт с паспортом.
    return result, replaced


def human_report_path(path: Optional[str] = None) -> str:
    """Папка, куда сохраняются отчёты (для кнопки «Открыть папку»)."""
    return os.path.dirname(path) if path else desktop_path()


def _human_size(size: Any) -> str:
    """Размер по-человечески: «12,3 МБ»."""
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "—"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            if unit == "Б":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return f"{value:.1f} ГБ"


def temp_archive_dir() -> str:
    """Временная папка для отчёта (нужна тестам и пробам)."""
    return tempfile.mkdtemp(prefix="diagnostics_")


__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "CHECK_ORDER",
    "COUNTED_TABLES",
    "CheckResult",
    "DiagnosticReport",
    "LOG_DAYS",
    "LOG_FILES",
    "REQUIRED_SETTINGS",
    "SECRET_PLACEHOLDER",
    "STATUS_FAIL",
    "STATUS_MARKS",
    "STATUS_OK",
    "STATUS_WARN",
    "archive_name",
    "build_archive",
    "check_agent_memory",
    "check_dadata",
    "check_database",
    "check_disk",
    "check_factories",
    "check_gigachat",
    "check_logs",
    "check_settings",
    "check_version",
    "database_path",
    "desktop_path",
    "human_report_path",
    "logs_dir",
    "mask_personal_data",
    "mask_secrets",
    "read_log_tail",
    "report_text",
    "run_diagnostics",
    "system_info",
    "system_text",
    "temp_archive_dir",
]
