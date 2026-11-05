#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Права на файл базы: починка «только чтение» и понятные ошибки записи.

Почему эти тесты важны
----------------------
Дефект из v1.4.1 («Не удалось сохранить данные: attempt to write a readonly
database») воспроизводится файлом с атрибутом «Только чтение»: SQLite
ОТКРЫВАЕТ такую базу (чтение работает), поэтому ошибка появляется только в
момент сохранения — после того, как оператор заполнил форму. Проверяется:

1. атрибут снимается ДО открытия соединения и запись после этого проходит;
2. настоящая проба записи (`CREATE TABLE` + `ROLLBACK`) в базе не оставляет
   следа — ни таблицы, ни транзакции;
3. если права починить нельзя, причина уходит в журнал уровнем ERROR и
   запоминается для окна (`main.py::_warn_if_database_readonly`);
4. ошибки SQLite переводятся в понятный текст: вторая копия программы,
   «Только чтение», антивирус.

База — временная (`work_file`), данные синтетические. Именно поэтому
проверять `os.access` недостаточно: списки доступа (ACL) Windows он не
видит, их ловит настоящая запись.
"""

import logging
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

# Корень проекта — по маркеру (pytest.ini), а не по числу parents.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import db.connection as connection  # noqa: E402
import db.database as database  # noqa: E402
from db import write_access  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Помощники
# ─────────────────────────────────────────────────────────────

def make_database(path: Path) -> Path:
    """
    Настоящая база в режиме WAL: как после первого запуска приложения.

    После закрытия SQLite сворачивает журнал, поэтому спутников -wal/-shm
    рядом не остаётся — тест сам создаёт их, где это нужно.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("CREATE TABLE probe (id INTEGER)")
        conn.execute("INSERT INTO probe (id) VALUES (1)")
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
def readonly_file():
    """Помечает файл «только чтение» и возвращает права после теста."""
    made = []

    def _make(path: Path) -> Path:
        os.chmod(str(path), stat.S_IREAD)
        made.append(Path(str(path)))
        return made[-1]

    yield _make

    for path in made:
        for candidate in (path, path.with_name(path.name + "-wal"),
                          path.with_name(path.name + "-shm")):
            try:
                os.chmod(str(candidate), stat.S_IWRITE)
            except OSError:
                pass


@pytest.fixture(autouse=True)
def clean_startup_problem():
    """Запомненная причина старта не должна протекать между тестами."""
    write_access.reset_startup_problem()
    yield
    write_access.reset_startup_problem()


# ─────────────────────────────────────────────────────────────
# 1. Пути: файл базы, спутники, особые пути
# ─────────────────────────────────────────────────────────────

def test_local_file_skips_memory_and_uri():
    """`:memory:` и URI — не файлы: права к ним не применяются."""
    assert write_access.local_file(":memory:") is None
    assert write_access.local_file("file:contracts.db?mode=ro") is None
    assert write_access.local_file("") is None
    assert write_access.local_file(None) is None
    assert write_access.local_file("contracts.db") == Path("contracts.db")


def test_related_files_include_existing_sidecars(work_file):
    """В список попадают база и существующие -wal/-shm, несуществующих нет."""
    db_file = make_database(work_file("related.db"))
    wal = db_file.with_name(db_file.name + "-wal")
    wal.write_bytes(b"")

    found = write_access.related_files(db_file)

    assert db_file in found
    assert wal in found
    assert db_file.with_name(db_file.name + "-shm") not in found
    assert write_access.related_files(":memory:") == ()


# ─────────────────────────────────────────────────────────────
# 2. Починка атрибута «только чтение»
# ─────────────────────────────────────────────────────────────

def test_clear_readonly_returns_write_access(work_file, readonly_file):
    """После починки файл снова доступен для записи."""
    db_file = readonly_file(make_database(work_file("fix_one.db")))
    assert not write_access.is_writable(db_file), "атрибут не выставился"

    assert write_access.clear_readonly(db_file) is True
    assert write_access.is_writable(db_file)
    assert os.stat(db_file).st_mode & stat.S_IWRITE


def test_clear_readonly_files_reports_repaired_paths(work_file, readonly_file):
    """Функция называет ровно те файлы, которым вернула запись."""
    db_file = readonly_file(make_database(work_file("fix_list.db")))

    repaired = write_access.clear_readonly_files(db_file)

    assert repaired == (str(db_file),)
    assert write_access.is_writable(db_file)


def test_clear_readonly_files_repairs_readonly_sidecar(work_file, readonly_file):
    """Спутник -wal тоже чинится: с ним SQLite не сможет писать."""
    db_file = make_database(work_file("fix_sidecar.db"))
    wal_path = db_file.with_name(db_file.name + "-wal")
    wal_path.write_bytes(b"")
    wal = readonly_file(wal_path)

    repaired = write_access.clear_readonly_files(db_file)

    assert str(wal) in repaired
    assert write_access.is_writable(wal)


def test_clear_readonly_files_ignores_writable_and_missing(work_file):
    """Здоровую и несуществующую базу трогать не нужно."""
    db_file = make_database(work_file("healthy.db"))
    missing = work_file("missing.db")

    assert write_access.clear_readonly_files(db_file) == ()
    assert write_access.clear_readonly_files(missing) == ()
    assert write_access.clear_readonly_files(":memory:") == ()


# ─────────────────────────────────────────────────────────────
# 3. Проверка прав при старте (prepare_database)
# ─────────────────────────────────────────────────────────────

def test_prepare_database_repairs_readonly_file(work_file, readonly_file):
    """Отчёт: атрибут снят, причина записи нет (problem пуст)."""
    db_file = readonly_file(make_database(work_file("prepare_fix.db")))

    report = write_access.prepare_database(db_file)

    assert report.ok, report.problem
    assert report.repaired == (str(db_file),)
    assert report.problem == ""
    assert write_access.is_writable(db_file)


def test_prepare_database_accepts_healthy_base(work_file):
    """Здоровая база: ничего не чинилось, проблемы нет."""
    db_file = make_database(work_file("prepare_ok.db"))

    report = write_access.prepare_database(db_file)

    assert report.ok
    assert report.repaired == ()


def test_prepare_database_accepts_missing_file(work_file):
    """Свежая установка: файла ещё нет — папка writable, проблемы нет."""
    report = write_access.prepare_database(work_file("not_created_yet.db"))

    assert report.ok
    assert report.repaired == ()


def test_prepare_database_skips_special_paths():
    """`:memory:` проверять нечем — отчёт пустой и без проблемы."""
    report = write_access.prepare_database(":memory:")

    assert report.ok
    assert report.repaired == ()


def test_directory_problem_explains_missing_folder(work_dir):
    """Несуществующая папка названа словами, а не OSError."""
    problem = write_access.directory_problem(work_dir / "нет_такой_папки")

    assert "не найдена" in problem
    assert str(work_dir / "нет_такой_папки") in problem


def test_prepare_database_reports_unwritable_folder(work_file, monkeypatch):
    """Запрет создания файлов в папке — понятная причина, а не трассировка."""
    db_file = make_database(work_file("folder_denied.db"))

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(write_access.tempfile, "mkstemp", denied)

    report = write_access.prepare_database(db_file)

    assert not report.ok
    assert "нельзя создавать файлы" in report.problem
    assert "contracts.db-wal" in report.problem
    assert "только для чтения" in report.problem.lower()
    assert "вторую копию" in report.problem


# ─────────────────────────────────────────────────────────────
# 4. Проба записи: ловит «только чтение», но не меняет базу
# ─────────────────────────────────────────────────────────────

def test_probe_write_leaves_no_trace(work_file):
    """После пробы в базе нет ни таблицы, ни открытой транзакции."""
    db_file = make_database(work_file("probe_trace.db"))

    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        before = conn.execute(
            "SELECT type, name FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        write_access.probe_write(conn)
        after = conn.execute(
            "SELECT type, name FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        assert after == before
        assert write_access.PROBE_TABLE not in {row[1] for row in after}
        assert conn.in_transaction is False
    finally:
        conn.close()


def test_probe_write_detects_readonly_database(work_file, readonly_file):
    """Настоящая запись упирается в атрибут — SQLite отдаёт SQLITE_READONLY."""
    db_file = readonly_file(make_database(work_file("probe_ro.db")))

    conn = sqlite3.connect(str(db_file))
    try:
        with pytest.raises(sqlite3.DatabaseError) as info:
            write_access.probe_write(conn)
    finally:
        conn.close()

    assert write_access.classify_error(info.value) == "readonly"
    assert "readonly" in str(info.value).lower()


def test_probe_write_skips_connection_inside_transaction(work_file):
    """Чужую транзакцию проба не откатывает — молча пропускает."""
    db_file = make_database(work_file("probe_tx.db"))

    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute("BEGIN")
        write_access.probe_write(conn)
        assert conn.in_transaction is True
        conn.rollback()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# 5. get_connection: починка при старте и понятная ошибка
# ─────────────────────────────────────────────────────────────

def test_get_connection_repairs_readonly_database(work_file, readonly_file,
                                                  monkeypatch, caplog):
    """Главный сценарий жалобы: файл «только чтение», сохранение работает."""
    db_file = readonly_file(make_database(work_file("startup_fix.db")))
    monkeypatch.setattr(database, "DB_PATH", str(db_file))

    with caplog.at_level(logging.WARNING, logger="db.connection"):
        conn = database.get_connection()
    try:
        conn.execute("INSERT INTO probe (id) VALUES (2)")
        conn.commit()
        saved = conn.execute("SELECT COUNT(*) FROM probe").fetchone()[0]
    finally:
        conn.close()

    assert saved == 2, "запись в починенную базу не прошла"
    assert write_access.is_writable(db_file)
    assert any("только чтение" in record.getMessage().lower()
               for record in caplog.records), "починка не попала в журнал"
    assert write_access.take_startup_problem() == "", (
        "починка не должна пугать оператора окном"
    )


def test_get_connection_logs_clear_error_when_repair_impossible(
    work_file, readonly_file, monkeypatch, caplog
):
    """
    Права починить нельзя: ERROR в журнале + причина для окна.

    `clear_readonly_files` подменена на «ничего не сделала» — так выглядит
    запрет доступа (ACL): атрибут снять можно, а писать всё равно нельзя.
    """
    db_file = readonly_file(make_database(work_file("startup_denied.db")))
    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    monkeypatch.setattr(write_access, "clear_readonly_files", lambda *a, **k: ())

    with caplog.at_level(logging.ERROR, logger="db.connection"):
        conn = database.get_connection()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO probe (id) VALUES (3)")
    finally:
        conn.close()

    problem = write_access.startup_problem()
    assert "только для чтения" in problem.lower()
    assert str(db_file) in problem
    assert "вторую копию" in problem
    assert write_access.take_startup_problem() == problem, (
        "окну достаётся та же причина, что попала в журнал"
    )
    assert write_access.take_startup_problem() == "", "окно показывается один раз"

    errors = [record.getMessage() for record in caplog.records
              if record.levelno >= logging.ERROR]
    assert errors, "причина не попала в журнал уровнем ERROR"
    assert any("только для чтения" in message.lower() for message in errors)


def test_get_connection_does_not_warn_on_healthy_database(isolated_db):
    """Здоровая база не даёт ни ошибок, ни окна при старте."""
    conn = isolated_db.get_connection()
    conn.close()

    assert write_access.take_startup_problem() == ""


# ─────────────────────────────────────────────────────────────
# 6. Тексты для оператора
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "message, expected",
    [
        ("attempt to write a readonly database", "readonly"),
        ("database is locked", "locked"),
        ("disk I/O error", "other"),
    ],
)
def test_classify_error(message, expected):
    """Вид ошибки определяется и по тексту, и по коду SQLite."""
    assert write_access.classify_error(sqlite3.OperationalError(message)) == expected


def test_classify_error_uses_sqlite_error_code():
    """Код SQLite важнее текста: сообщение может быть локализованным."""
    error = sqlite3.OperationalError("что-то пошло не так")
    error.sqlite_errorname = "SQLITE_READONLY"  # подставляем код явно
    assert write_access.classify_error(error) == "readonly"


def test_save_error_text_readonly_has_steps():
    """Диалог сохранения: те же шаги, что в журнале, и понятная причина."""
    text = write_access.save_error_text(
        sqlite3.OperationalError("attempt to write a readonly database"),
        "C:/данные/contracts.db",
    )

    for part in ("Не удалось сохранить данные", "только для чтения",
                 "C:/данные/contracts.db", "вторую копию",
                 "Только чтение", "антивирус"):
        assert part in text, part


def test_save_error_text_locked_mentions_second_copy():
    """«База занята» — подсказка про вторую копию, а не сырой текст SQLite."""
    text = write_access.save_error_text(sqlite3.OperationalError("database is locked"))

    assert "занята другой программой" in text
    assert "вторую копию" in text


def test_save_error_text_keeps_other_errors():
    """Остальные ошибки не переписываются: прежний текст и причина."""
    text = write_access.save_error_text(sqlite3.OperationalError("disk I/O error"))

    assert text == "Не удалось сохранить данные:\ndisk I/O error"


def test_explain_error_is_empty_for_unrelated_error():
    """Чужую ошибку объяснение не подменяет."""
    assert write_access.explain_error(ValueError("не про базу")) == ""
