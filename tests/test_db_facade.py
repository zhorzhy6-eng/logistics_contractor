#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Точка входа базы данных: фасад db/database.py после разбиения на пакет db/.

Что проверяется
---------------
1. **Совместимость имён.** Всё, что проект импортирует из `db.database`
   (ui/, core/, тесты), по-прежнему доступно — и через `__all__`, и как
   атрибут модуля. Разбиение файла не должно менять ни одного вызова.
2. **Нет колец импортов.** Модули `db/crud/*.py` не тянут фасад
   `db.database` (иначе получилось бы кольцо: фасад → crud → фасад),
   а граф импортов внутри пакета `db/` ацикличен.
3. **Схема не изменилась.** `init_database()` на чистой базе создаёт ровно
   те же объекты `sqlite_master`, что и до разбиения: сверяется sha256
   снимка (тип, имя и SQL каждого объекта) и структура таблиц.
4. **Фасад остался тонким.** `db/database.py` — не больше 200 строк: вся
   работа лежит в модулях пакета.

Как пересоздать снимок схемы (если схема меняется НАМЕРЕННО)
------------------------------------------------------------
    python tests/_tmp/_make_schema_snapshot.py db/database.py
и перенести напечатанный SHA256 и числа в константы ниже. Снимок «до
разбиения» делается так же, но по старому файлу:
    git show <коммит>:db/database.py > tests/_tmp/database_before_split.py
    python tests/_tmp/_make_schema_snapshot.py tests/_tmp/database_before_split.py
"""

import ast
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import db.database as database  # noqa: E402

#: Имена, которые импортируют приложение и тесты (собраны по проекту:
#: ui/main_window.py, ui/db_manager_dialog.py, ui/address_book_dialog.py,
#: ui/driver_carrier_dialog.py, ui/carrier_drivers_dialog.py,
#: ui/tabs/*, ui/windows/arenda_ts/contacts.py, core/gigachat_client.py,
#: main.py и файлы tests/test_db_*.py, tests/test_database*.py).
REQUIRED_NAMES = (
    # соединение и инициализация
    "DB_PATH", "get_connection", "init_database",
    # водители
    "save_driver", "update_driver", "load_driver", "search_drivers",
    "get_all_drivers", "delete_driver", "restore_driver",
    "save_driver_vehicle", "load_driver_vehicle",
    # привязка водителей к перевозчикам
    "link_driver_to_carrier", "unlink_driver_from_carrier",
    "get_driver_carriers", "set_default_carrier",
    # стороны договора
    "save_organization", "update_organization", "load_organization",
    "load_organization_by_id", "find_organization_id",
    "get_all_organizations", "search_organizations",
    "delete_organization", "restore_organization",
    # договоры
    "save_contract", "save_contract_points", "save_contract_with_details",
    "load_contract_points",
    # ТС
    "save_vehicles",
    # справочник адресов и салоны
    "save_address", "update_address", "delete_address", "get_addresses",
    "count_addresses", "import_addresses_from_list",
    "read_salons_rows", "salons_xlsx_path", "SALONS_XLSX_NAME",
    "rebuild_fts_index",
    # имена, которые зовут тесты напрямую
    "_needs_migration", "_load_salons_if_empty",
)

#: Схема чистой базы после init_database(): снимок ДО разбиения файла
#: (см. «Как пересоздать снимок» в docstring модуля).
SCHEMA_SHA256 = "876068222aeeb206629ef97d51a8ee1ae6664973b4bbc57967bbd041db244305"
SCHEMA_OBJECTS = 57
SCHEMA_COLUMNS = {
    "drivers": 19, "customers": 20, "carriers": 22, "vehicles": 9,
    "driver_vehicles": 10, "driver_carriers": 6, "contracts": 17,
    "contract_points": 8, "address_book": 12, "counterparties": 20,
}

#: Модули пакета db/ (кроме фасада) — в них не должно быть импорта фасада.
DB_MODULES = (
    "connection", "schema", "migrations", "salons", "fts",
    "crud.addresses", "crud.carriers", "crud.contracts", "crud.customers",
    "crud.counterparties", "crud.drivers", "crud.organizations",
    "crud.search", "crud.vehicles",
)

#: Максимальный размер фасада (строк): вся работа — в модулях пакета.
FACADE_MAX_LINES = 200


def _module_path(name: str) -> Path:
    return PROJECT_ROOT / "db" / (name.replace(".", "/") + ".py")


def _imported_modules(path: Path):
    """Имена модулей, которые импортирует файл (включая импорты внутри функций)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            # `from db import database` — это тоже импорт фасада
            found.update(node.module + "." + alias.name for alias in node.names)
            if node.level:  # относительный импорт пакета db/
                found.add("db." + node.module)
    return found


# ─────────────────────────────────────────────────────────────
# 1. Совместимость имён
# ─────────────────────────────────────────────────────────────

def test_facade_has_all_attribute():
    """У фасада есть __all__ со списком публичных имён."""
    assert hasattr(database, "__all__"), (
        "db/database.py потерял __all__ — точка входа должна объявлять имена"
    )
    assert len(database.__all__) == len(set(database.__all__)), "дубли в __all__"


def test_all_declared_names_exist():
    """Каждое имя из __all__ действительно есть в модуле."""
    missing = [name for name in database.__all__ if not hasattr(database, name)]
    assert missing == [], f"объявлены, но отсутствуют: {missing}"


def test_names_used_by_project_are_available():
    """Все имена, которые импортирует проект, доступны из db.database."""
    missing = [name for name in REQUIRED_NAMES if not hasattr(database, name)]
    assert missing == [], (
        "после разбиения на пакет пропали имена: " + ", ".join(missing)
    )


def test_project_names_are_declared_in_all():
    """Публичные имена проекта перечислены в __all__ (кроме запасных)."""
    public = {name for name in REQUIRED_NAMES if not name.startswith("_")}
    declared = set(database.__all__)
    assert public <= declared, (
        "не объявлены в __all__: " + ", ".join(sorted(public - declared))
    )


def test_public_functions_come_from_package():
    """Реализация живёт в модулях пакета, а не в фасаде."""
    own = {
        "init_database", "get_connection", "rebuild_fts_index",
        "read_salons_rows", "salons_xlsx_path", "_load_salons_if_empty",
        "_needs_migration",
    }
    in_facade = []
    for name in REQUIRED_NAMES:
        if name in own:
            continue
        obj = getattr(database, name, None)
        if not callable(obj):
            continue
        if getattr(obj, "__module__", "db.database") == "db.database":
            in_facade.append(name)
    assert in_facade == [], (
        "эти функции остались в фасаде, хотя должны быть в db/crud/: "
        + ", ".join(in_facade)
    )


def test_facade_is_thin():
    """db/database.py — тонкий фасад (не больше 200 строк)."""
    path = PROJECT_ROOT / "db" / "database.py"
    lines = len(path.read_text(encoding="utf-8").splitlines())
    assert lines <= FACADE_MAX_LINES, (
        f"db/database.py разросся: {lines} строк (предел {FACADE_MAX_LINES})"
    )


# ─────────────────────────────────────────────────────────────
# 2. Импорты: нет колец
# ─────────────────────────────────────────────────────────────

def test_crud_modules_do_not_import_facade():
    """Ни один модуль db/crud/*.py не импортирует db.database (нет колец)."""
    offenders = []
    for name in DB_MODULES:
        if not name.startswith("crud."):
            continue
        path = _module_path(name)
        assert path.exists(), f"нет модуля {path}"
        for imported in _imported_modules(path):
            if imported == "db.database" or imported.startswith("db.database."):
                offenders.append(f"{path.name} → {imported}")
    assert offenders == [], "модули справочников тянут фасад: " + "; ".join(offenders)


def test_package_modules_do_not_import_facade():
    """И остальные модули пакета (кроме самого фасада) не тянут db.database."""
    offenders = []
    for name in DB_MODULES:
        if name.startswith("crud."):
            continue
        path = _module_path(name)
        assert path.exists(), f"нет модуля {path}"
        for imported in _imported_modules(path):
            if imported == "db.database":
                offenders.append(f"{path.name} → {imported}")
    assert offenders == []


def test_import_graph_of_db_package_is_acyclic():
    """Граф импортов внутри пакета db/ ацикличен (обход в глубину)."""
    graph = {}
    for name in ("database",) + DB_MODULES:
        path = _module_path(name)
        edges = set()
        for imported in _imported_modules(path):
            if imported == "db" or imported.startswith("db."):
                target = imported[3:] or ""
                if target and target != name:
                    edges.add(target)
        graph[name] = edges

    visited, stack = set(), []

    def walk(node):
        if node in stack:
            cycle = stack[stack.index(node):] + [node]
            raise AssertionError("кольцо импортов: " + " → ".join(cycle))
        if node in visited:
            return
        stack.append(node)
        for target in sorted(graph.get(node, ())):
            if target in graph:
                walk(target)
        stack.pop()
        visited.add(node)

    for node in graph:
        walk(node)


def test_db_modules_are_importable_without_facade():
    """Каждый модуль пакета импортируется сам по себе (без фасада в цепочке)."""
    for name in DB_MODULES + ("crud",):
        module = __import__("db." + name, fromlist=["__all__"])
        assert module is not None
        assert getattr(module, "__all__", None), f"db.{name} без __all__"


# ─────────────────────────────────────────────────────────────
# 3. Схема базы не изменилась
# ─────────────────────────────────────────────────────────────

def _schema_rows(db_path: str):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT type, name, COALESCE(sql, '') FROM sqlite_master "
            "ORDER BY type, name"
        ).fetchall()
    finally:
        conn.close()


def test_schema_matches_snapshot(isolated_db):
    """init_database() создаёт ту же схему, что до разбиения файла."""
    rows = _schema_rows(isolated_db.DB_PATH)

    assert len(rows) == SCHEMA_OBJECTS, (
        f"объектов в sqlite_master: {len(rows)}, ожидалось {SCHEMA_OBJECTS}"
    )

    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert digest == SCHEMA_SHA256, (
        "схема отличается от снимка ДО разбиения: "
        "если схема менялась намеренно — пересоздай снимок "
        "(см. docstring tests/test_db_facade.py)"
    )


def test_schema_tables_and_columns(isolated_db):
    """Таблицы и колонки на месте (понятная диагностика к проверке выше)."""
    conn = sqlite3.connect(str(isolated_db.DB_PATH))
    try:
        for table, expected in SCHEMA_COLUMNS.items():
            columns = [
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            ]
            assert len(columns) == expected, (
                f"{table}: колонок {len(columns)}, ожидалось {expected}"
            )
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
    finally:
        conn.close()

    for table in SCHEMA_COLUMNS:
        assert table in names, f"нет таблицы {table}"
    for fts_table in ("fts_addresses", "fts_drivers", "fts_customers",
                      "fts_carriers", "app_meta"):
        assert fts_table in names, f"нет таблицы поиска {fts_table}"


# ─────────────────────────────────────────────────────────────
# 4. Путь к базе подменяется через фасад (как в тестах проекта)
# ─────────────────────────────────────────────────────────────

def test_db_path_patch_reaches_connection(work_file, monkeypatch):
    """
    Подмена db.database.DB_PATH действует на соединение пакета.

    tests/conftest.py::isolated_db меняет путь именно у фасада, поэтому
    db/connection.py обязан спрашивать путь у него, а не хранить свой.
    """
    import db.connection as connection

    db_file = work_file("facade_path.db")
    monkeypatch.setattr(database, "DB_PATH", str(db_file))

    assert connection.current_db_path() == str(db_file)

    conn = database.get_connection()
    try:
        conn.execute("CREATE TABLE probe (id INTEGER)")
        conn.commit()
    finally:
        conn.close()

    assert Path(str(db_file)).exists(), "соединение открыло не тот файл"


@pytest.mark.parametrize("name", ["fts", "schema", "migrations", "salons", "connection"])
def test_package_module_kept_in_facade_namespace(name):
    """Модули пакета доступны из фасада (инструменты и тесты берут их так)."""
    assert hasattr(database, name), f"db.database.{name} пропал"
