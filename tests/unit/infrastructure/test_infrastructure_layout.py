#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Раскладка инфраструктуры после Этапа 3 рефакторинга (16.10.2026).

Проверяет то, что нельзя проверить обычными тестами модулей:

1. Пять модулей ДЕЙСТВИТЕЛЬНО переехали: файлов в корне `core/` больше нет,
   а в `infrastructure/api/`, `infrastructure/secrets/` и `core/utils/` — есть.
2. Старые импорты (`from core.gigachat_client import GigaChatClient`) работают
   и ведут к ТОМУ ЖЕ объекту модуля, что и новые пути. Это не копия: если
   импорты разъедутся на два разных модуля, тесты начнут видеть два разных
   `GigaChatClient` и «плавающие» ошибки атрибутов.
3. Публичный API не изменился: имена, которыми пользуются UI, агент,
   инструменты и приватная часть, доступны из обоих путей и из самого пакета
   `core` (ленивый реэкспорт через module-level `__getattr__`).
4. Имена логгеров остались прежними и попадают в настройку
   `config/logging_config.py` (`APP_LOGGERS`).
5. ЛЮБОЙ порядок импорта работает — и это отдельные процессы: в общем прогоне
   pytest `core` уже загружен `tests/conftest.py`, поэтому «инфраструктура
   первым» там не проверяется никогда.

Зачем отдельный файл: без него удаление блока алиасов в `core/__init__.py`
не покраснит НИ ОДИН тест проекта — импорты внутри `tests/` идут через
`conftest.py`, который поднимает пакеты целиком.

Проба, которая запускает оба порядка в свежих процессах, лежит рядом:
`tools/check_stage3_aliases.py` (режимы `old` и `new`).
"""

import importlib
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
CORE = PROJECT_ROOT / "core"

#: (старое имя модуля, новый путь файла) — ровно то, что переехало в Этапе 3.
MOVED = [
    ("gigachat_client", "infrastructure/api/gigachat.py"),
    ("dadata_client", "infrastructure/api/dadata.py"),
    ("tls_config", "infrastructure/api/tls.py"),
    ("secrets_store", "infrastructure/secrets/store.py"),
    ("json_text", "core/utils/json_text.py"),
]

#: Пакеты, которые обязан создать Этап 3.
NEW_PACKAGES = ["infrastructure", "infrastructure.api", "infrastructure.secrets"]

#: Публичный API: (новый путь модуля, имя) — обязано быть доступно из обоих.
PUBLIC_API = [
    ("infrastructure.api.gigachat", "GigaChatClient"),
    ("infrastructure.api.gigachat", "normalized_source_text"),
    ("infrastructure.api.dadata", "DadataClient"),
    ("infrastructure.api.dadata", "bank_status_warning"),
    ("infrastructure.api.dadata", "normalize_position"),
    ("infrastructure.api.tls", "describe_tls"),
    ("infrastructure.api.tls", "resolve_ca_bundle"),
    ("infrastructure.api.tls", "ssl_error_hint"),
    ("infrastructure.api.tls", "default_ca_bundle"),
    ("infrastructure.secrets.store", "get_gigachat_key"),
    ("infrastructure.secrets.store", "set_gigachat_key"),
    ("infrastructure.secrets.store", "MISSING_KEY_MESSAGE"),
    ("infrastructure.secrets.store", "MISSING_DADATA_KEY_MESSAGE"),
    ("core.utils.json_text", "shorten_json_text"),
]

#: Имена, которые обязан отдавать ленивый реэкспорт пакета `core`.
CORE_REEXPORTED = [
    "GigaChatClient",
    "DadataClient",
    "MISSING_KEY_MESSAGE",
    "get_gigachat_key",
    "set_gigachat_key",
    "has_gigachat_key",
    "delete_gigachat_key",
    "describe_tls",
    "resolve_ca_bundle",
    "ssl_error_hint",
    "shorten_json_text",
]

#: Модули корня `core/`, которых переезд НЕ касался (соседи остались на месте).
CORE_NEIGHBOURS = ["import_cancel.py", "pseudonymizer.py", "trace.py", "audit.py"]

#: Имя логгера каждого переехавшего модуля — часть внешнего контракта.
EXPECTED_LOGGERS = {
    "core.gigachat_client": "infrastructure.api.gigachat",
    "core.dadata_client": "infrastructure.api.dadata",
    "core.tls_config": "infrastructure.api.tls",
    "core.secrets_store": "infrastructure.secrets.store",
    "core.utils.json_text": "core.utils.json_text",
}


@pytest.mark.parametrize("old_name,new_path", MOVED, ids=[m[0] for m in MOVED])
def test_file_moved_out_of_core_root(old_name, new_path):
    """Старого файла в корне `core/` нет, новый файл на месте."""
    assert not (CORE / f"{old_name}.py").exists(), (
        f"core/{old_name}.py вернулся в корень — переезд Этапа 3 отменён?"
    )
    assert (PROJECT_ROOT / new_path).is_file(), f"нет файла {new_path}"


@pytest.mark.parametrize("old_name,new_path", MOVED, ids=[m[0] for m in MOVED])
def test_old_import_path_is_the_same_module(old_name, new_path):
    """`import core.<старое_имя>` и новый путь — ОДИН объект модуля."""
    old_module = importlib.import_module(f"core.{old_name}")
    new_module = importlib.import_module(new_path[:-3].replace("/", "."))
    assert old_module is new_module, (
        f"core.{old_name} и {new_path} — разные модули: "
        "разъехавшиеся импорты дадут два разных класса с одним именем"
    )


@pytest.mark.parametrize("old_name,new_path", MOVED, ids=[m[0] for m in MOVED])
def test_old_module_is_attribute_of_core(old_name, new_path):
    """
    `core.<старое_имя>` доступно и как атрибут пакета.

    Одной регистрации в `sys.modules` для этого НЕ ХВАТАЕТ: `import core.X`
    для уже загруженного модуля родительский атрибут не выставляет. Первая
    версия Этапа 3 регистрировала только `sys.modules` — и `core.gigachat_client`
    падало с AttributeError.
    """
    import core

    assert getattr(core, old_name) is importlib.import_module(f"core.{old_name}")


@pytest.mark.parametrize("old_name,new_path", MOVED, ids=[m[0] for m in MOVED])
def test_old_name_is_in_sys_modules(old_name, new_path):
    """
    Старое имя лежит в `sys.modules` — именно это читает `from core.X import Y`.

    Регистрация обязана идти через `setdefault`, как в Этапах 1–2: живой
    модуль, подложенный приватной частью, затирать нельзя.
    """
    import core  # noqa: F401 — регистрация алиасов живёт в его импорте

    assert sys.modules.get(f"core.{old_name}") is importlib.import_module(
        new_path[:-3].replace("/", ".")
    )


@pytest.mark.parametrize("package", NEW_PACKAGES)
def test_new_packages_are_packages_with_docstrings(package):
    """`infrastructure` и её подпакеты — обычные пакеты с пояснением."""
    module = importlib.import_module(package)
    assert module.__doc__ and module.__doc__.strip(), (
        f"у пакета {package} нет докстроки"
    )


@pytest.mark.parametrize(
    "package,old_path,new_path",
    [
        ("infrastructure.api", "core.gigachat_client", "infrastructure.api.gigachat"),
        ("infrastructure.secrets", "core.secrets_store", "infrastructure.secrets.store"),
    ],
)
def test_package_docstring_points_to_both_paths(package, old_path, new_path):
    """Докстрока пакета объясняет оба пути импорта — иначе её смысл потерян."""
    doc = importlib.import_module(package).__doc__
    assert new_path in doc
    assert old_path in doc


@pytest.mark.parametrize(
    "module_path,name", PUBLIC_API, ids=[f"{m.split('.')[-1]}-{n}" for m, n in PUBLIC_API]
)
def test_public_api_is_available_from_new_path(module_path, name):
    """Публичный API на месте по НОВОМУ пути."""
    module = importlib.import_module(module_path)
    assert hasattr(module, name), f"{module_path} потерял {name}"


def test_public_api_is_available_from_old_path():
    """Тот же публичный API доступен и по СТАРОМУ пути (через алиасы)."""
    from core.dadata_client import DadataClient  # noqa: F401
    from core.gigachat_client import GigaChatClient, normalized_source_text  # noqa: F401
    from core.json_text import shorten_json_text  # noqa: F401
    from core.secrets_store import (  # noqa: F401
        MISSING_KEY_MESSAGE,
        get_gigachat_key,
        set_gigachat_key,
    )
    from core.tls_config import describe_tls, resolve_ca_bundle, ssl_error_hint  # noqa: F401

    # Импорты выше — и есть проверка: не найдя имя, они падают.
    assert callable(get_gigachat_key) and callable(set_gigachat_key)
    assert callable(describe_tls) and callable(resolve_ca_bundle)
    assert callable(ssl_error_hint) and callable(shorten_json_text)
    assert isinstance(MISSING_KEY_MESSAGE, str)


@pytest.mark.parametrize("name", CORE_REEXPORTED)
def test_names_are_reexported_from_core(name):
    """
    Имена инфраструктуры видны через `from core import <имя>`.

    Это ленивый реэкспорт: `core/__init__.py` отдаёт имена через module-level
    `__getattr__`, а не статическим импортом, — иначе на обратном порядке
    импорта ловится ImportError на частичном модуле.
    """
    import core

    assert hasattr(core, name), f"core.{name} пропало из реэкспорта"
    assert name in core.__all__, f"{name} нет в core.__all__"


def test_all_names_in_dunder_all_exist():
    """`__all__` не обещает того, чего в пакете нет."""
    import core

    missing = [name for name in core.__all__ if not hasattr(core, name)]
    assert missing == [], f"в core.__all__ лишние имена: {missing}"


def test_lazy_unknown_name_raises_attribute_error():
    """Ленивая дверь не превращает опечатку в тихое None."""
    import core

    with pytest.raises(AttributeError):
        core.НетТакогоИмени


def test_lazy_module_names_do_not_leak_into_core_all():
    """Старые ИМЕНА МОДУЛЕЙ в `__all__` не попадают: это не имена, а модули."""
    import core

    for old_name, _new_path in MOVED:
        assert old_name not in core.__all__, (
            f"{old_name} — имя модуля, ему не место в core.__all__"
        )


@pytest.mark.parametrize(
    "logger_name,module_path",
    list(EXPECTED_LOGGERS.items()),
    ids=list(EXPECTED_LOGGERS),
)
def test_logger_names_are_unchanged(logger_name, module_path):
    """
    Имена логгеров остались прежними — это часть внешнего контракта.

    `config/logging_config.py` настраивает логгеры только из списка
    `APP_LOGGERS` ("core", "ui", "db", "config", "main", "qt"). Логгер вида
    `infrastructure.*` в него не входит и потерял бы обработчики и уровень, а
    `caplog` в наборах тестов (публичных и приватных) ищет логи по старому имени.
    """
    module = importlib.import_module(module_path)
    assert module.logger.name == logger_name, (
        f"{module_path}: логгер называется {module.logger.name!r}, "
        f"а должен {logger_name!r}"
    )


@pytest.mark.parametrize(
    "module_path",
    [path for path in EXPECTED_LOGGERS.values()],
    ids=[path for path in EXPECTED_LOGGERS.values()],
)
def test_moved_loggers_are_covered_by_logging_config(module_path):
    """
    Логгер переехавшего модуля попадает в настройку логирования.

    `logging_config` настраивает логгеры верхнего уровня из `APP_LOGGERS`,
    а дочерние (`core.gigachat_client`, `core.utils.json_text`) наследуют
    обработчики по ИМЕНИ — иерархия в `logging` строится по точкам. Если имя
    окажется вне этих деревьев (`infrastructure.api.gigachat`), логи уедут в
    «последнюю надежду» и в `logs/app.log` их не будет.
    """
    from config.logging_config import APP_LOGGERS

    name = importlib.import_module(module_path).logger.name
    assert any(
        name == root or name.startswith(root + ".") for root in APP_LOGGERS
    ), (
        f"логгер {name!r} не входит ни в одно дерево из APP_LOGGERS "
        f"{APP_LOGGERS} — логи выпадут из настройки"
    )


@pytest.mark.parametrize(
    "module_path",
    [path for path in EXPECTED_LOGGERS.values() if path != "core.utils.json_text"],
    ids=[
        path
        for path in EXPECTED_LOGGERS.values()
        if path != "core.utils.json_text"
    ],
)
def test_moved_logger_records_reach_the_core_root(module_path, caplog):
    """
    Запись через логгер переехавшего модуля реально доходит до корня `core`.

    Проверка не по имени, а по факту: включаем уровень на логгере `core` и
    смотрим, что запись, сделанная через `module.logger`, до него добралась.
    Имена, начинающиеся с `core.`, проходят это по построению; имена вида
    `infrastructure.*` — нет.
    """
    module = importlib.import_module(module_path)

    with caplog.at_level(logging.INFO, logger="core"):
        module.logger.info("проверка распространения логов")

    assert any(
        record.name == module.logger.name for record in caplog.records
    ), f"запись через {module.logger.name} не дошла до логгера 'core'"


def test_module_name_is_new():
    """
    При этом `__name__` модулей — настоящие, новые.

    Иначе это была бы копия модуля под старым именем, а не переехавший файл.
    """
    for old_name, new_path in MOVED:
        module = importlib.import_module(f"core.{old_name}")
        assert module.__name__ == new_path[:-3].replace("/", "."), (
            f"core.{old_name} всё ещё зовётся {module.__name__}"
        )


def test_core_root_neighbours_stayed_in_place():
    """Переезд не задел соседей по корню `core/`."""
    for name in CORE_NEIGHBOURS:
        assert (CORE / name).is_file(), f"core/{name} пропал"


def test_store_audit_is_lazy_and_patchable():
    """
    `store.audit` доступен как атрибут, но не импортируется на уровне модуля.

    Две вещи сразу:
      * тесты подменяют его через `monkeypatch.setattr(secrets_store.audit, …)`,
        значит имя обязано читаться как АТРИБУТ модуля;
      * модульный импорт `core` замыкал бы цикл store → core → gigachat → store
        и ронял `import infrastructure.secrets.store` первым.
    """
    import core.secrets_store as store

    assert hasattr(store, "audit"), "store.audit перестал читаться как атрибут"
    assert store.audit is importlib.import_module("core.audit")
    # В собственном словаре модуля имени быть НЕ должно — иначе импорт
    # случился бы на уровне модуля, то есть цикл вернулся.
    assert "audit" not in vars(store), (
        "audit снова попал в модуль статическим импортом — цикл вернётся"
    )


def test_store_audit_patch_is_honoured(monkeypatch):
    """Подмена `store.audit` реально доходит до записи события аудита."""
    import core.secrets_store as store

    calls = []

    class FakeAudit:
        @staticmethod
        def log_event(action, **fields):
            calls.append((action, fields))

    monkeypatch.setattr(store, "audit", FakeAudit)
    store._log_audit("проверка_подмены")

    assert calls == [("проверка_подмены", {"source": "keyring"})]


@pytest.mark.parametrize("mode", ["old", "new"])
def test_both_import_orders_work_in_a_fresh_process(mode):
    """
    ЛЮБОЙ порядок импорта работает — и это отдельный процесс.

    Режим `new` ловит цикл импорта, который не виден в общем прогоне:
    в pytest `core` уже загружен `conftest.py`, поэтому «инфраструктура
    первым» никогда не проверяется. Именно на нём падал первый заход Этапа 3.
    """
    probe = PROJECT_ROOT / "tools" / "check_stage3_aliases.py"
    # Проба печатает по-русски, а stdout дочернего процесса — труба: на
    # англоязычном раннере кодировка по умолчанию cp1252, кириллица в неё
    # не влезает, и print() роняет пробу через UnicodeEncodeError. UTF-8 для
    # дочернего процесса — то же, что делает
    # tests/ui/main_window/test_qt_shutdown_agent_pool.py.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, str(probe), mode],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"проба режима {mode} упала:\n{result.stdout}\n{result.stderr}"
    )


@pytest.mark.parametrize(
    "first_import",
    [
        "import infrastructure.secrets.store",
        "import infrastructure.api.dadata",
        "import infrastructure.api.tls",
        "import infrastructure.api.gigachat",
        "import core.utils.json_text",
    ],
)
def test_aliases_survive_any_first_import(first_import):
    """
    С какого бы модуля ни начался процесс, все пять алиасов доезжают.

    Это не придирка: первый заход Этапа 3 терял `core.gigachat_client` и
    `core.dadata_client`, когда процесс начинался с
    `import infrastructure.secrets.store` — `store` замыкал цикл, и
    `gigachat` падал на частичном модуле. Ошибку глотал `try/except` в
    `core/__init__.py`, поэтому наружу она не выходила: старые пути просто
    переставали работать в этом процессе.
    """
    aliases = [f"core.{old}" for old, _ in MOVED]
    code = (
        f"{first_import}\n"
        "import core\n"
        "import sys\n"
        f"missing = [n for n in {aliases!r} if n not in sys.modules]\n"
        "print('MISSING:' + ','.join(missing))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode == 0, f"импорт упал:\n{result.stdout}\n{result.stderr}"
    line = [ln for ln in result.stdout.splitlines() if ln.startswith("MISSING:")]
    assert line, f"проба не ответила:\n{result.stdout}\n{result.stderr}"
    assert line[0] == "MISSING:", (
        f"после {first_import} не зарегистрированы алиасы: {line[0][8:]}"
    )
