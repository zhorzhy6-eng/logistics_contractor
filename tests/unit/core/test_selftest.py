#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты самопроверки сборки (core/selftest.py) — Часть 4 задания 18.10.2026.

Зачем проверять отчёт
---------------------
Самопроверку запускает КОЛЛЕГА на своей машине, когда программа ведёт
себя странно. Если она сама падает или врёт («всё в порядке» при
отсутствующем шаблоне), толку от неё нет. Поэтому проверяется:

* каждая проверка возвращает допустимый статус и не роняет отчёт;
* исключение внутри проверки становится строкой «ОШИБ», а не падением;
* «сведение» (нет ключа, нет базы) НЕ считается отказом;
* отчёт пишется файлом и читается как UTF-8;
* код возврата 0/1 соответствует наличию отказов.

Настоящий `selftest.txt` в корень проекта тесты не пишут: путь отчёта
подменяется на временную папку.
"""

import sys
from pathlib import Path

import pytest

from core import selftest

#: Корень проекта — по маркеру, а не по числу parents.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)


@pytest.fixture
def report_dir(tmp_path, monkeypatch):
    """Отчёт пишется во временную папку, а не в корень проекта."""
    target = tmp_path / selftest.REPORT_FILENAME
    monkeypatch.setattr(selftest, "report_path", lambda: target)
    return target


# ─────────────────────────────────────────────────────────────
# 1. Проверки в разработке
# ─────────────────────────────────────────────────────────────

def test_all_checks_return_known_status():
    """Каждая проверка отвечает одним из трёх статусов и текстом."""
    results = selftest.run_checks()

    assert len(results) == len(selftest.CHECKS)
    for name, status, detail in results:
        assert name, "у проверки нет названия"
        assert status in (selftest.OK, selftest.WARN, selftest.FAIL), status
        assert isinstance(detail, str) and detail.strip(), f"{name}: пустые подробности"


def test_dev_machine_has_no_failures():
    """
    На рабочей машине отказов быть не должно.

    «Сведения» допустимы и ожидаемы: на чистой машине нет ключей, на
    чужой — база ещё не создана. Отказ здесь означал бы, что сломаны
    шаблоны, правовая база или приватная часть.
    """
    failures = [
        (name, detail) for name, status, detail in selftest.run_checks()
        if status == selftest.FAIL
    ]
    assert failures == [], f"отказы самопроверки: {failures}"


def test_templates_check_sees_project_templates():
    """Проверка шаблонов находит файлы проекта (иначе генерация не работает)."""
    status, detail = selftest._check_templates()

    assert status == selftest.OK
    assert "docx" in detail.lower() or "шт." in detail


def test_legal_rules_check_reports_count():
    """Проверка правовой базы сообщает число загруженных правил."""
    status, detail = selftest._check_legal_rules()

    assert status == selftest.OK
    assert "правил:" in detail


def test_private_part_check_reports_types():
    """Проверка приватной части называет число типов договоров."""
    status, detail = selftest._check_private_part()

    assert status in (selftest.OK, selftest.WARN)
    if status == selftest.OK:
        assert "типов договоров:" in detail


def test_generation_check_creates_document():
    """
    Проверка генерации действительно создаёт договор и удаляет его за собой.

    Это единственная проверка, которая проходит путь «шаблон → docxtpl →
    файл» целиком: именно она ловит сборку без шаблона или без docx.
    """
    status, detail = selftest._check_generation()

    assert status == selftest.OK, detail
    assert "документ" in detail
    assert "знаков" in detail


def test_checks_include_generation_and_windows():
    """Ключевые для сборки проверки не выпали из списка."""
    titles = [title for title, _check in selftest.CHECKS]

    assert "Генерация договора" in titles
    assert "Окна типов" in titles
    assert "Приватная часть" in titles
    assert len(titles) == len(set(titles)), "названия проверок повторяются"


# ─────────────────────────────────────────────────────────────
# 2. Отчёт и код возврата
# ─────────────────────────────────────────────────────────────

def test_report_contains_every_check():
    """В отчёте есть строка на каждую проверку."""
    results = selftest.run_checks()
    text = selftest.format_report(results)

    for name, _status, _detail in results:
        assert name in text
    assert "ИТОГ:" in text


def test_report_marks_failure():
    """Отказ виден и в строке, и в итоге."""
    results = [
        ("Шаблоны", selftest.OK, "4 шт."),
        ("Правовая база", selftest.FAIL, "каталог не прочитан"),
    ]
    text = selftest.format_report(results)

    assert "[ОШИБ] Правовая база" in text
    assert "отказов 1" in text


def test_report_counts_warnings_separately():
    """«Сведение» — не отказ: итог так и говорит."""
    results = [("Ключи API", selftest.WARN, "ключа нет")]
    text = selftest.format_report(results)

    assert "отказов нет" in text
    assert "сведений 1" in text


def test_run_selftest_returns_zero_without_failures(monkeypatch, report_dir):
    """Ни одного отказа — код 0, отчёт записан."""
    monkeypatch.setattr(
        selftest, "run_checks",
        lambda: [("Пути", selftest.OK, "в порядке")],
    )

    assert selftest.run_selftest() == 0
    assert report_dir.is_file()
    assert "ИТОГ: всё в порядке." in report_dir.read_text(encoding="utf-8")


def test_run_selftest_returns_one_on_failure(monkeypatch, report_dir):
    """Есть отказ — код 1 (и отчёт всё равно записан)."""
    monkeypatch.setattr(
        selftest, "run_checks",
        lambda: [("Шаблоны", selftest.FAIL, "нет файлов")],
    )

    assert selftest.run_selftest() == 1
    assert "нет файлов" in report_dir.read_text(encoding="utf-8")


def test_broken_check_does_not_stop_others(monkeypatch, report_dir):
    """
    Упавшая проверка — строка «ОШИБ», а остальные всё равно выполняются.

    Коллеге нужен полный список, а не первая ошибка.
    """
    def _boom():
        raise RuntimeError("проверка сломалась")

    monkeypatch.setattr(
        selftest, "CHECKS",
        (("Сломанная", _boom), ("Живая", lambda: (selftest.OK, "работает"))),
    )

    results = selftest.run_checks()

    assert [name for name, _s, _d in results] == ["Сломанная", "Живая"]
    assert results[0][1] == selftest.FAIL
    assert "RuntimeError" in results[0][2]
    assert results[1][1] == selftest.OK


def test_report_path_is_next_to_program():
    """Отчёт по умолчанию лежит рядом с программой (там, где можно писать)."""
    from core import app_paths

    assert selftest.report_path() == app_paths.writable_path(selftest.REPORT_FILENAME)


# ─────────────────────────────────────────────────────────────
# 3. Связка с main.py: флаг --selftest
# ─────────────────────────────────────────────────────────────

def test_main_parses_selftest_flag(monkeypatch):
    """
    `main.py --selftest` разбирается в флаг selftest.

    Сам `main()` здесь не вызывается: на выходе он завершает процесс
    (`os._exit`), и тест убил бы прогон.
    """
    monkeypatch.setattr(sys, "argv", ["main.py", "--selftest"])
    import main

    assert main._parse_args(["--selftest"]).selftest is True
    assert main._parse_args([]).selftest is False
    assert main._parse_args(["--debug"]).debug is True


def test_selftest_flag_documented_in_help(monkeypatch, capsys):
    """
    Флаг описан в справке — иначе о нём никто не узнает.

    `--help` у argparse завершает процесс через SystemExit; его и ловим,
    а напечатанный текст читаем из перехвата.
    """
    monkeypatch.setattr(sys, "argv", ["main.py"])
    import main

    with pytest.raises(SystemExit):
        main._parse_args(["--help"])

    printed = capsys.readouterr().out
    assert "--selftest" in printed
    assert "самопроверка" in printed.lower()
