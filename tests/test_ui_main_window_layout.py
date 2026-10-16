#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Раскладка пакета `ui/main_window/` после Этапа 6 (16.10.2026).

Прежний `ui/main_window.py` (2136 строк, один класс на двенадцать
ответственностей) разбит на пакет из 11 модулей: 9 миксинов + `recognition.py`
+ `window.py` (каркас) + `__init__.py` (реэкспорт).

Тесты проверяют СТРУКТУРУ разбиения, а не поведение окна: поведение ловят
`tests/test_ui_*.py` (они поднимают живой MainWindow). Здесь — то, что
ломается тихо:

  * пакет на месте, все 11 модулей существуют и импортируются;
  * `MainWindow` собирается из девяти миксинов + `DiagnosticsMenuMixin` +
    `QMainWindow`, и порядок MRO соблюдён (layout первым, QMainWindow
    последним);
  * у миксинов НЕТ `__init__` — иначе MRO-цепочка `__init__` пошла бы не в
    том порядке;
  * публичный API (`MainWindow`, `RecognitionTask`, `RecognitionSignals`,
    `BulkPasteDialog`) доступен и из `ui.main_window`, и из своих модулей;
  * сигналы `switch_to_type_requested` / `exit_requested` на месте;
  * каждый метод прежнего класса имеет ровно один «дом» — ни один не
    потерян и не продублирован;
  * в `connect` нет `lambda` и `functools.partial` (грабли: цикл ссылок
    Python ↔ Qt, падение 0xC0000005);
  * логгеры всех модулей — `ui.main_window` (иначе записи выпадают из
    `APP_LOGGERS` в `config/logging_config.py`).

Qt в тестах поднимается в offscreen-режиме; окно здесь НЕ создаётся —
только классы.
"""

import ast
import inspect
import logging
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
PACKAGE_DIR = PROJECT_ROOT / "ui" / "main_window"

#: Все модули пакета. `__init__.py` — реэкспорт, `window.py` — каркас,
#: остальные девять — миксины.
EXPECTED_MODULES = (
    "__init__.py",
    "window.py",
    "layout.py",
    "settings.py",
    "theme.py",
    "data.py",
    "contract.py",
    "save.py",
    "load.py",
    "driver_match.py",
    "recognition.py",
    "close.py",
)

#: Миксины в порядке наследования (он же порядок в MRO MainWindow).
MIXINS_IN_MRO_ORDER = (
    ("ui.main_window.layout", "LayoutMixin"),
    ("ui.main_window.settings", "SettingsMixin"),
    ("ui.main_window.theme", "ThemeMixin"),
    ("ui.main_window.data", "DataMixin"),
    ("ui.main_window.contract", "ContractMixin"),
    ("ui.main_window.save", "SaveMixin"),
    ("ui.main_window.load", "LoadMixin"),
    ("ui.main_window.driver_match", "DriverMatchMixin"),
    ("ui.main_window.recognition", "RecognitionMixin"),
    ("ui.main_window.close", "CloseMixin"),
)

#: Модуль → класс-владелец методов из `METHOD_HOMES`. Задан явно:
#: эвристика «единственный класс в модуле» ломается на `recognition.py`,
#: где рядом с `RecognitionMixin` живут `RecognitionSignals`,
#: `RecognitionTask` и `BulkPasteDialog`.
METHOD_OWNERS = {
    "ui.main_window.layout": "LayoutMixin",
    "ui.main_window.settings": "SettingsMixin",
    "ui.main_window.theme": "ThemeMixin",
    "ui.main_window.data": "DataMixin",
    "ui.main_window.contract": "ContractMixin",
    "ui.main_window.save": "SaveMixin",
    "ui.main_window.load": "LoadMixin",
    "ui.main_window.driver_match": "DriverMatchMixin",
    "ui.main_window.recognition": "RecognitionMixin",
    "ui.main_window.close": "CloseMixin",
}

#: Метод → модуль, в котором он обязан жить. Карта из плана подэтапа 1.A:
#: каждый метод прежнего класса получил ровно один дом.
METHOD_HOMES = {
    "ui.main_window.layout": (
        "_init_ui", "_build_header", "_build_action_bar", "_build_tabs",
        "_build_status_bar", "_build_progress", "_log_ui_action",
        "_on_tab_changed", "_sync_side_nav", "_refresh_status_indicators",
        "_refresh_status_fields", "_sync_loadings_to_vehicles",
        "_sync_unloadings_to_vehicles", "_on_import_documents",
        "_on_clear_form", "_on_tab_clear_requested", "_on_clear_tab",
    ),
    "ui.main_window.settings": ("_init_gigachat_client", "_on_open_settings"),
    "ui.main_window.theme": (
        "_apply_theme_choice", "_sync_system_theme_watcher",
        "_on_system_theme_changed", "_refresh_nav_icons",
    ),
    "ui.main_window.data": ("_collect_data", "_confirm_validation", "_mark_saved"),
    "ui.main_window.contract": (
        "_on_create_contract", "_show_contract_created", "_open_folder",
    ),
    "ui.main_window.save": (
        "_sync_organization_with_db", "_safe_organization_record",
        "_on_save_to_db",
    ),
    "ui.main_window.load": (
        "_on_open_db_manager", "_load_driver_from_db", "_load_customer_from_db",
        "_load_carrier_from_db", "_apply_party_type", "_sync_contract_form_type",
        "_party_entity_type",
    ),
    "ui.main_window.driver_match": (
        "_driver_carrier_id", "_carrier_form_is_empty", "_carrier_title",
        "_check_driver_carrier_match", "_safe_organization",
        "_confirm_carrier_mismatch",
    ),
    "ui.main_window.recognition": (
        "_on_recognize_clicked", "_on_tab_recognize_requested",
        "_start_recognition", "_cancel_recognition", "_task_of_sender",
        "_task_from", "_is_current_task", "_on_recognition_progress",
        "_on_recognition_cancelled", "_finish_recognition_ui",
        "_log_recognition_result", "_on_recognition_finished",
        "_on_recognition_error",
    ),
    "ui.main_window.close": (
        "closeEvent", "_close_floating_cat", "_save_all_column_widths",
        "force_close",
    ),
}


# ─────────────────────────────────────────────────────────────
# 1. Пакет и модули
# ─────────────────────────────────────────────────────────────

def test_package_directory_exists():
    assert PACKAGE_DIR.is_dir(), "ui/main_window/ — пакет, а не модуль"


def test_old_module_file_is_gone():
    """Прежний одиночный файл удалён: иначе пакет и модуль спорили бы."""
    assert not (PROJECT_ROOT / "ui" / "main_window.py").exists()


def test_package_has_no_init_free_module():
    """`ui.main_window` — пакет с `__init__.py`, а не «namespace package»."""
    assert (PACKAGE_DIR / "__init__.py").is_file()


@pytest.mark.parametrize("name", EXPECTED_MODULES)
def test_expected_module_exists(name):
    assert (PACKAGE_DIR / name).is_file(), f"нет модуля ui/main_window/{name}"


def test_module_count_is_exactly_expected():
    """Лишних модулей нет: разбиение сделано ровно по плану."""
    found = sorted(p.name for p in PACKAGE_DIR.glob("*.py"))
    assert found == sorted(EXPECTED_MODULES), found


@pytest.mark.parametrize("name", EXPECTED_MODULES)
def test_each_module_compiles(name):
    """Каждый модуль — синтаксически корректный Python."""
    source = (PACKAGE_DIR / name).read_text(encoding="utf-8")
    ast.parse(source, filename=str(PACKAGE_DIR / name))


@pytest.mark.parametrize("name", EXPECTED_MODULES)
def test_each_module_imports(name):
    """`ui.main_window.<name>` импортируется сам по себе, без окна."""
    module_name = "ui.main_window" if name == "__init__.py" else (
        f"ui.main_window.{name[:-3]}"
    )
    __import__(module_name)


# ─────────────────────────────────────────────────────────────
# 2. MainWindow и MRO
# ─────────────────────────────────────────────────────────────

def main_window_class():
    from ui.main_window import MainWindow
    return MainWindow


def test_main_window_importable_from_package():
    assert main_window_class().__name__ == "MainWindow"


def test_main_window_importable_from_window_module():
    from ui.main_window.window import MainWindow
    assert MainWindow is main_window_class()


def test_main_window_lives_in_window_module():
    assert main_window_class().__module__ == "ui.main_window.window"


def test_main_window_is_qmainwindow():
    from PyQt5.QtWidgets import QMainWindow
    assert issubclass(main_window_class(), QMainWindow)


@pytest.mark.parametrize("module_name,class_name", MIXINS_IN_MRO_ORDER)
def test_mixin_is_importable_and_is_class(module_name, class_name):
    module = __import__(module_name, fromlist=[class_name])
    assert inspect.isclass(getattr(module, class_name))


@pytest.mark.parametrize("module_name,class_name", MIXINS_IN_MRO_ORDER)
def test_mixin_is_in_main_window_mro(module_name, class_name):
    module = __import__(module_name, fromlist=[class_name])
    mixin = getattr(module, class_name)
    assert mixin in main_window_class().__mro__


@pytest.mark.parametrize("module_name,class_name", MIXINS_IN_MRO_ORDER)
def test_mixin_has_no_init(module_name, class_name):
    """
    У миксинов НЕТ `__init__`.

    Если он появится, MRO-цепочка `__init__` начнёт выполняться в порядке
    наследования, и инициализация пойдёт не в том порядке, что в
    `MainWindow.__init__`. Вся инициализация — в каркасе.
    """
    module = __import__(module_name, fromlist=[class_name])
    mixin = getattr(module, class_name)
    assert "__init__" not in vars(mixin), (
        f"{class_name} завёл свой __init__: инициализацию ведёт MainWindow"
    )


def test_layout_mixin_is_first_base():
    """LayoutMixin первым: он создаёт виджеты для остальных миксинов."""
    from ui.main_window.layout import LayoutMixin
    assert main_window_class().__bases__[0] is LayoutMixin


def test_qmainwindow_is_last_base():
    """QMainWindow последним: иначе его методы перекрыли бы миксины."""
    from PyQt5.QtWidgets import QMainWindow
    assert main_window_class().__bases__[-1] is QMainWindow


def test_diagnostics_menu_mixin_is_before_qmainwindow():
    from PyQt5.QtWidgets import QMainWindow
    from ui.diagnostics_menu import DiagnosticsMenuMixin
    bases = main_window_class().__bases__
    assert bases.index(DiagnosticsMenuMixin) == bases.index(QMainWindow) - 1


def test_mro_order_matches_plan():
    """Полный порядок миксинов в MRO — ровно как в плане подэтапа 1.A."""
    from ui.diagnostics_menu import DiagnosticsMenuMixin
    from PyQt5.QtWidgets import QMainWindow

    expected = [cls for _module, cls_name in MIXINS_IN_MRO_ORDER
                for cls in [_load_class(_module, cls_name)]]
    expected += [DiagnosticsMenuMixin, QMainWindow]

    mro = list(main_window_class().__mro__)
    positions = [mro.index(cls) for cls in expected]
    assert positions == sorted(positions), [
        f"{cls.__name__}@{mro.index(cls)}" for cls in expected
    ]


def _load_class(module_name: str, class_name: str):
    module = __import__(module_name, fromlist=[class_name])
    return getattr(module, class_name)


# ─────────────────────────────────────────────────────────────
# 3. Публичный API и сигналы
# ─────────────────────────────────────────────────────────────

def test_switch_to_type_requested_signal_declared():
    MainWindow = main_window_class()
    assert "switch_to_type_requested" in vars(MainWindow)


def test_exit_requested_signal_declared():
    MainWindow = main_window_class()
    assert "exit_requested" in vars(MainWindow)


def test_contract_type_is_source_type():
    from core.mirror import SOURCE_TYPE
    assert main_window_class().CONTRACT_TYPE == SOURCE_TYPE


def test_recognition_sections_declared():
    sections = main_window_class()._RECOGNITION_SECTIONS
    keys = [key for key, _title in sections]
    assert keys == [
        "driver", "customer", "carrier", "vehicles", "tractor", "trailer",
        "contract",
    ]


def test_tab_icon_names_count_matches_tabs():
    assert len(main_window_class()._TAB_ICON_NAMES) == 6


def test_tab_keys_returns_six_keys():
    keys = main_window_class()._tab_keys(None)
    assert keys == (
        "carrier_tab", "driver_tab", "trailer_tab",
        "vehicles_tab", "contract_tab", "customer_tab",
    )


def test_recognition_classes_available_from_package():
    from ui.main_window import RecognitionSignals, RecognitionTask
    from ui.main_window.recognition import (
        RecognitionSignals as S2, RecognitionTask as T2,
    )
    assert RecognitionSignals is S2
    assert RecognitionTask is T2


def test_recognition_classes_live_in_recognition_module():
    from ui.main_window.recognition import RecognitionSignals, RecognitionTask
    assert RecognitionTask.__module__ == "ui.main_window.recognition"
    assert RecognitionSignals.__module__ == "ui.main_window.recognition"


def test_bulk_paste_dialog_available_from_package():
    from ui.main_window import BulkPasteDialog
    from ui.main_window.recognition import BulkPasteDialog as B2
    assert BulkPasteDialog is B2


def test_recognition_task_has_signals_and_cancel():
    from ui.main_window.recognition import RecognitionTask
    task = RecognitionTask(None, "текст")
    assert task.is_cancelled is False
    task.cancel()
    assert task.is_cancelled is True
    assert task.signals.owner() is task


def test_recognition_signals_owner_is_weak():
    """Владелец у сигналов — СЛАБАЯ ссылка (иначе цикл Python ↔ Qt)."""
    import gc
    import weakref

    from ui.main_window.recognition import RecognitionTask

    task = RecognitionTask(None, "текст")
    signals = task.signals
    assert isinstance(signals._task_ref, weakref.ref)
    del task
    gc.collect()
    assert signals.owner() is None


def test_module_level_logger_is_available():
    """Прежний модуль отдавал `logger` — пакет отдаёт его же."""
    import ui.main_window as module
    assert isinstance(module.logger, logging.Logger)
    assert module.logger.name == "ui.main_window"


def test_module_level_db_helper_is_available():
    """
    Совместимость: тест публичной части подменяет функцию базы у модуля.

    `tests/test_ui_main_window_load_driver_carrier.py` делает
    `monkeypatch.setattr(module, "load_organization_by_id", broken)`, где
    `module` — это `ui.main_window`. Имя обязано быть у пакета.
    """
    import ui.main_window as module
    assert callable(module.load_organization_by_id)


def test_key_validation_helper_is_available():
    """`_validate_gigachat_key` переехал в settings.py и виден из пакета."""
    import ui.main_window as module
    from ui.main_window.settings import _validate_gigachat_key
    assert module._validate_gigachat_key is _validate_gigachat_key
    assert _validate_gigachat_key("") is not None
    assert _validate_gigachat_key("abc") is None


#: Имена, которые прежний ОДИНОЧНЫЙ модуль `ui/main_window.py` отдавал наружу.
#: Список — из `vars()` старого модуля (86 имён); здесь те, к которым есть
#: обращения из тестов и внешнего кода. Проверка нужна потому, что полный
#: прогон подэтапа 1.P нашёл ровно такой регресс:
#: `tests/test_icons.py::test_main_window_reexports_icon_helpers` упал, когда
#: пакет перестал реэкспортировать `_action_icon` и родню (ЭТАП 2B оставлял их
#: здесь алиасами на `ui.icons`).
OLD_MODULE_NAMES = (
    "_action_icon",
    "_tab_icon",
    "_themed_icon",
    "_resource_path",
    "_validate_gigachat_key",
    "_PROJECT_ROOT",
    "TEMPLATES_DIR",
    "logger",
    "load_organization_by_id",
    "MainWindow",
    "RecognitionTask",
    "RecognitionSignals",
    "BulkPasteDialog",
)


@pytest.mark.parametrize("name", OLD_MODULE_NAMES)
def test_old_module_name_still_available(name):
    """Имя из прежнего модуля доступно и после разбиения на пакет."""
    import ui.main_window as module
    assert hasattr(module, name), (
        f"ui.main_window.{name} пропало: внешний код и тесты на него смотрят"
    )


def test_icon_helpers_point_to_ui_icons():
    """Алиасы иконок ведут к ТЕМ ЖЕ функциям `ui.icons` (ЭТАП 2B)."""
    import ui.icons as icons
    import ui.main_window as module
    assert module._action_icon is icons.action_icon
    assert module._tab_icon is icons.tab_icon
    assert module._themed_icon is icons.themed_icon
    assert module._resource_path is icons.resource_path


def test_templates_dir_points_to_templates():
    import ui.main_window as module
    assert Path(module.TEMPLATES_DIR).name == "templates"
    assert Path(module.TEMPLATES_DIR).is_dir()


#: Коммит ПЕРЕД Этапом 6 — в нём `ui/main_window.py` ещё целый файл.
#: Список имён берётся из него, а не пишется руками: так страж не устареет
#: и поймает ЛЮБОЕ пропавшее имя, а не только те, что вспомнили.
PRE_STAGE6_COMMIT = "8362a67"
PRE_STAGE6_PATH = "ui/main_window.py"


def _old_module_names() -> set:
    """
    Имена уровня модуля из прежнего `ui/main_window.py` (по AST, без импорта).

    Импортировать старый файл нельзя: он конфликтует с пакетом. Разбираем
    исходник из истории git — нужны только имена, которые модуль отдавал
    наружу: импорты, определения верхнего уровня и присваивания.
    """
    import subprocess

    result = subprocess.run(
        ["git", "show", f"{PRE_STAGE6_COMMIT}:{PRE_STAGE6_PATH}"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        pytest.skip(f"нет доступа к {PRE_STAGE6_COMMIT}:{PRE_STAGE6_PATH}")

    names = set()
    for node in ast.parse(result.stdout).body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return {name for name in names if not name.startswith("__")}


def test_no_name_from_old_module_disappeared():
    """
    Страж обратной совместимости: ни одно имя прежнего модуля не пропало.

    Прежний `ui/main_window.py` был ОДНИМ файлом, поэтому для вызывающего
    кода его транзитные импорты (`ContractData`, `save_driver`, `QMessageBox`,
    `theme`, …) неотличимы от «своих» имён. Разбиение на пакет их молча
    убирает — и это уже случилось: полный прогон 1.P поймал падение
    `tests/test_icons.py::test_main_window_reexports_icon_helpers` на
    `_action_icon`. Этот тест ловит весь класс таких поломок разом.
    """
    import ui.main_window as module

    missing = sorted(
        name for name in _old_module_names() if not hasattr(module, name)
    )
    assert not missing, (
        "имена прежнего ui/main_window.py пропали из пакета: " + ", ".join(missing)
    )


# ─────────────────────────────────────────────────────────────
# 4. Дом для каждого метода: ничего не потеряно и не продублировано
# ─────────────────────────────────────────────────────────────

def test_every_documented_method_exists_once():
    MainWindow = main_window_class()
    seen = {}
    for module_name, methods in METHOD_HOMES.items():
        owner_name = METHOD_OWNERS[module_name]
        owner = _load_class(module_name, owner_name)
        for method in methods:
            assert method in vars(owner), f"{module_name}: нет метода {method}"
            assert method not in seen, (
                f"{method} объявлен и в {seen[method]}, и в {module_name}"
            )
            seen[method] = module_name
            assert hasattr(MainWindow, method), f"MainWindow не видит {method}"


def test_method_homes_cover_all_functions():
    """
    В модулях пакета нет «забытых» функций вне плана.

    `MainWindow`, `_tab_keys` и `__init__` живут в `window.py`, классы
    распознавания — в `recognition.py`, `_validate_gigachat_key` —
    модульная функция в `settings.py`. Всё остальное обязано быть в карте.
    """
    planned = {m for methods in METHOD_HOMES.values() for m in methods}
    planned |= {
        "__init__", "_tab_keys",                  # каркас, window.py
        "_validate_gigachat_key",                 # settings.py, модульная
        "RecognitionSignals", "RecognitionTask",  # recognition.py, классы
        "RecognitionTask.cancel", "RecognitionTask.run",
        "RecognitionTask.is_cancelled", "RecognitionSignals.owner",
        "RecognitionSignals.__init__", "RecognitionTask.__init__",
        "BulkPasteDialog", "BulkPasteDialog.__init__",
        "BulkPasteDialog._paste_from_clipboard", "BulkPasteDialog.get_text",
    }
    extra = []
    for name in EXPECTED_MODULES:
        if name == "__init__.py":
            continue
        module_name = f"ui.main_window.{name[:-3]}"
        tree = ast.parse((PACKAGE_DIR / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        qualified = f"{node.name}.{item.name}"
                        if item.name in planned or qualified in planned:
                            continue
                        if node.name in planned:
                            continue
                        extra.append(f"{module_name}:{qualified}")
    assert not extra, f"вне плана разбиения: {extra}"


# ─────────────────────────────────────────────────────────────
# 5. Грабли Qt: никаких lambda и partial в connect
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", EXPECTED_MODULES)
def test_no_lambda_in_connect(name):
    """
    `lambda` в `connect` запрещена: цикл ссылок Python ↔ Qt → 0xC0000005.

    Проверяется ПО ИСХОДНИКУ: `ast` не отличит слот-метод от lambda внутри
    вызова `connect`, поэтому ищем сам шаблон «`.connect(lambda`».
    """
    source = (PACKAGE_DIR / name).read_text(encoding="utf-8")
    assert ".connect(lambda" not in source.replace(" ", "")


@pytest.mark.parametrize("name", EXPECTED_MODULES)
def test_no_functools_partial_in_connect(name):
    source = (PACKAGE_DIR / name).read_text(encoding="utf-8")
    flat = source.replace(" ", "")
    assert ".connect(functools.partial" not in flat
    assert ".connect(partial" not in flat


@pytest.mark.parametrize("name", EXPECTED_MODULES)
def test_no_lambda_anywhere_in_package(name):
    """lambda в этих модулях не нужна вовсе — ни в connect, ни в данных."""
    tree = ast.parse((PACKAGE_DIR / name).read_text(encoding="utf-8"))
    lambdas = [node for node in ast.walk(tree) if isinstance(node, ast.Lambda)]
    assert not lambdas, f"{name}: lambda на строке(ах) {[n.lineno for n in lambdas]}"


# ─────────────────────────────────────────────────────────────
# 6. Логгеры: одно имя на весь пакет
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", EXPECTED_MODULES)
def test_logger_name_is_ui_main_window(name):
    """
    Имя логгера — `ui.main_window`, а не `__name__`.

    Иначе после переезда записи выпали бы из `APP_LOGGERS`
    (`config/logging_config.py`) и из `caplog`-проверок, которые ищут
    сообщения окна по имени `ui.main_window`.
    """
    source = (PACKAGE_DIR / name).read_text(encoding="utf-8")
    assert 'logging.getLogger(__name__)' not in source, f"{name}: нужен явный логгер"
    if "logging.getLogger(" in source:
        assert 'logging.getLogger("ui.main_window")' in source


def test_all_mixins_share_one_logger_object():
    import ui.main_window as package
    for module_name, _class_name in MIXINS_IN_MRO_ORDER:
        module = __import__(module_name, fromlist=["logger"])
        assert module.logger is package.logger, module_name
