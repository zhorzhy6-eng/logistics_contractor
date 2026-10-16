#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Регресс-тест: в `connect` — только связанные методы.

Правило проекта (AGENTS.md § 4, грабли 2B.7): `lambda` в `connect` запрещена,
потому что связь живёт в C++ объекте сигнала, а замыкание держит Python-объект.
Получается цикл Python ↔ Qt: объект не освобождается при выходе, и процесс
падает по access violation (`0xC0000005`).

Здесь проверяется и `functools.partial` — у него ровно тот же дефект: он тоже
держит СИЛЬНУЮ ссылку на получателя (связанный метод окна/вкладки). Именно так
были связаны сигналы задач распознавания в четырёх окнах типов и сигналы задач
DaData на вкладках — то есть цикл жил ровно в том месте, которое работает
перед выходом (запуск распознавания → выход).

Проверка сделана разбором AST, а не поиском по строкам: `connect(` и `lambda`
часто стоят на разных строках, и построчный grep их не видит (на этом уже
спотыкались — 12 связей нашлись только после перехода на AST).

Модуль тестов не трогаем: там lambda в connect — обычное дело (проверки
сигналов), и на работу приложения они не влияют.
"""

import ast
import sys
from pathlib import Path

import pytest

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.private_path import private_path, private_root  # noqa: E402

#: Что сканируем: точка входа и весь рабочий код интерфейса.
SCAN_FILES = (PROJECT_ROOT / "main.py",)
SCAN_DIRS = ["ui"]

#: Окна клиентских типов лежат в приватной части проекта. Пути собираются
#: из ключей типов, а не зашиты списком: нет приватной папки — список пуст,
#: и проверка этих файлов пропускается.
#: Ключ типа и имя папки окна совпадают не всегда (ключ описывает документ,
#: папка — форму), поэтому связь задана явно.
CLIENT_WINDOW_KEYS = (
    ("arenda_ts", "arenda_ts"),
    ("formika", "formika"),
    ("logistiks_rus", "logistiks_rus"),
    ("zayavka_excel", "havaly"),
)
CLIENT_WINDOW_MODULES = tuple(
    f"ui/windows/{dirname}/window.py" for _key, dirname in CLIENT_WINDOW_KEYS
)

#: Корни сканирования: публичный интерфейс и (если есть) приватный.
_private_ui = private_path("ui")
if _private_ui is not None and _private_ui.is_dir():
    SCAN_DIRS.append(str(_private_ui))


def hotspot_path(relative: str) -> Path:
    """Путь «горячего» модуля: публичный проект, затем приватная часть."""
    path = PROJECT_ROOT / relative
    if path.exists():
        return path
    root = private_root()
    return (root / relative) if root is not None else path

#: Сколько связей должно найтись: страховка от «пустого» прохода, когда
#: сканер молча ничего не находит и тест зелёный ни за что.
MIN_EXPECTED_CONNECTIONS = 100


def _ui_files():
    """Файлы рабочего кода, где вообще бывают связи сигналов."""
    files = [path for path in SCAN_FILES if path.exists()]
    for dirname in SCAN_DIRS:
        files.extend(sorted((PROJECT_ROOT / dirname).rglob("*.py")))
    return files


def _callable_name(node) -> str:
    """Имя вызываемого: `connect` у `.connect(...)`, `partial` у `partial(...)`."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _connect_calls(tree):
    """Все вызовы `....connect(...)` в разобранном файле."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callable_name(node.func) == "connect":
            yield node


def _bad_target(argument):
    """
    Чем плох первый аргумент connect: 'lambda', 'functools.partial' или None.

    None значит «связанный метод» — то, что и требуется.
    """
    if isinstance(argument, ast.Lambda):
        return "lambda"
    if isinstance(argument, ast.Call) and _callable_name(argument.func) == "partial":
        return "functools.partial"
    return None


def _scan():
    """Возвращает (число связей, список нарушений) по всему рабочему коду."""
    total = 0
    problems = []
    for path in _ui_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _connect_calls(tree):
            for argument in call.args:
                total += 1
                bad = _bad_target(argument)
                if bad is not None:
                    rel = path.relative_to(PROJECT_ROOT).as_posix()
                    problems.append(f"{rel}:{call.lineno} — {bad}")
    return total, problems


def test_scan_actually_sees_connections():
    """
    Сканер действительно видит связи сигналов.

    Без этой проверки сломанный сканер (не тот каталог, не тот разбор) дал бы
    зелёный тест «нарушений нет» — и правило осталось бы без защиты.
    """
    total, _ = _scan()
    assert total >= MIN_EXPECTED_CONNECTIONS, (
        f"найдено всего {total} связей — сканер сломан, а не код чист"
    )


def test_connect_has_no_lambda_and_no_partial():
    """
    Ни одна связь сигнала не идёт через lambda или functools.partial.

    Замыкание держит Python-объект (окно, вкладку, задачу), а связь живёт в
    C++ объекте сигнала: получается цикл Python ↔ Qt, из-за которого процесс
    падает при выходе. Оба вида — lambda и partial — запрещены.
    """
    _, problems = _scan()
    assert not problems, (
        "в connect найдены замыкания (цикл Python ↔ Qt, падение при выходе):\n  "
        + "\n  ".join(problems)
    )


@pytest.mark.parametrize(
    "relative",
    [
        "ui/main_window.py",
        "ui/action_logging.py",
        "ui/navigation.py",
        "ui/tabs/base_tab.py",
        "ui/tabs/contract_tab.py",
        "ui/document_import_dialog.py",
    ]
    + list(CLIENT_WINDOW_MODULES),
)
def test_known_hotspots_have_connections(relative):
    """
    В «горячих» модулях связи есть, и все — на методы.

    Это места, где цикл Python ↔ Qt уже находили: сигналы задач распознавания,
    DaData, кнопки-замыкания. Список фиксирует, что модуль не выпал из скана
    (например, при переименовании файла).

    Окна клиентских типов живут в приватной части проекта, поэтому их пути
    ищутся и там: нет приватной папки — проверка пропускается явным skip,
    а не падением на отсутствующем файле.
    """
    path = hotspot_path(relative)
    if not path.exists():
        pytest.skip(
            f"{relative}: файл вне публичной части проекта "
            f"(клиентский тип не установлен)"
        )

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = list(_connect_calls(tree))
    assert calls, f"{relative}: не найдено ни одной связи — проверьте сканер"

    problems = [
        f"строка {call.lineno}: {bad}"
        for call in calls
        for argument in call.args
        for bad in (_bad_target(argument),)
        if bad is not None
    ]
    assert not problems, f"{relative}: замыкания в connect — {problems}"
