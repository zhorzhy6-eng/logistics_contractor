# -*- coding: utf-8 -*-
"""
Окно Экспедиторства (MainWindow), разобранное на миксины.

Этап 6 (16.10.2026): прежний модуль `ui/main_window.py` (2136 строк, один
класс на двенадцать ответственностей) разбит на пакет из 11 модулей:

    window.py        — каркас: класс MainWindow, сигналы, константы, __init__
    layout.py        — раскладка окна (шапка, панель действий, вкладки,
                       сайдбар, статус-бар) и вспомогательные слоты
    settings.py      — настройки и клиент GigaChat
    theme.py         — тема оформления, watcher системы, иконки
    data.py          — сбор данных со вкладок и проверка перед генерацией
    contract.py      — создание договора и диалог успеха
    save.py          — сохранение в базу
    load.py          — загрузка записей из базы
    driver_match.py  — согласование перевозчика договора с привязкой водителя
    recognition.py   — распознавание: RecognitionTask, RecognitionSignals
                       и слоты прогресса
    close.py         — закрытие окна (скрытие, force_close)

Публичный API не изменился: `from ui.main_window import MainWindow` работает
по-прежнему и ведёт к ТОМУ ЖЕ классу.

Совместимость имён модуля (`ui.main_window`) сохранена намеренно: прежний
модуль был ОДНИМ файлом, поэтому внешний код и тесты обращались не только к
`MainWindow`, но и к именам внутри него — `logger`,
`monkeypatch.setattr(module, "load_organization_by_id", …)`. Эти имена
реэкспортируются ниже, чтобы разбиение не потребовало правок ни в публичных
тестах, ни в приватной части.
"""

#: Логгер окна — ТОТ ЖЕ объект, что и во всех миксинах (одно имя
#: "ui.main_window"), поэтому `caplog.set_level(logging.DEBUG,
#: logger="ui.main_window")` продолжает видеть записи окна, а `APP_LOGGERS`
#: в `config/logging_config.py` их не теряет.
from ui.main_window.window import MainWindow, logger  # noqa: F401
from ui.main_window.recognition import (  # noqa: F401
    BulkPasteDialog,
    RecognitionMixin,
    RecognitionSignals,
    RecognitionTask,
)
from ui.main_window.layout import LayoutMixin  # noqa: F401
from ui.main_window.settings import (  # noqa: F401
    SettingsMixin,
    _validate_gigachat_key,
)
from ui.main_window.theme import ThemeMixin  # noqa: F401
from ui.main_window.data import DataMixin  # noqa: F401
from ui.main_window.contract import ContractMixin  # noqa: F401
from ui.main_window.save import SaveMixin  # noqa: F401
from ui.main_window.load import LoadMixin  # noqa: F401
from ui.main_window.driver_match import DriverMatchMixin  # noqa: F401
from ui.main_window.close import CloseMixin  # noqa: F401

# ── Совместимость с прежним ОДИНОЧНЫМ модулем `ui/main_window.py` ──
# Прежний модуль был ОДНИМ файлом, поэтому внешний код и тесты обращались не
# только к `MainWindow`, но и к именам ВНУТРИ него: своим помощникам и, что
# важнее, к ТРАНЗИТНЫМ импортам (`ContractData`, `GigaChatClient`,
# `PerevozkaValidator`, `save_driver`, `QMessageBox`, `theme`, …). Для
# вызывающего кода такое имя неотличимо от «своего», и разбиение на пакет
# молча ломает обращение.
#
# Проверка полноты — в `tests/test_ui_main_window_layout.py`
# (`test_old_module_name_still_available`): список имён взят из `vars()`
# прежнего модуля, поэтому новый регресс такого рода будет пойман тестом, а
# не полным прогоном. Именно так нашёлся регресс с `_action_icon`
# (`tests/test_icons.py::test_main_window_reexports_icon_helpers`).
from datetime import datetime  # noqa: F401
from typing import Any, Dict, Optional  # noqa: F401
import logging  # noqa: F401
import os  # noqa: F401
import weakref  # noqa: F401

from PyQt5.QtCore import (  # noqa: F401
    Qt, pyqtSignal, QObject, QRunnable, QThreadPool, QTimer, QSize,
)
from PyQt5.QtWidgets import (  # noqa: F401
    QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

from core import audit, secrets_store  # noqa: F401
from core.contract_data import ContractData  # noqa: F401
from core.contracts.contract_types import DEFAULT_CONTRACT_TYPE  # noqa: F401
from core.contract_generator import (  # noqa: F401
    ContractGenerator,
    PerevozkaValidator,
)
from core.gigachat_client import (  # noqa: F401
    GigaChatClient,
    normalized_source_text,
)
from core.mirror import SOURCE_TYPE  # noqa: F401
from core.recognizer import DataMapper, filled_only, filled_only_list  # noqa: F401
from core.secrets_store import MISSING_KEY_MESSAGE  # noqa: F401
from core.settings_service import get_settings_service  # noqa: F401
from core.trace import filled_fields_summary  # noqa: F401
from core.validator import ValidationReport  # noqa: F401
from core.vat import split_carrier_type  # noqa: F401

from db.database import (  # noqa: F401
    init_database,
    save_driver,
    save_driver_vehicle,
    save_organization,
    update_organization,
    restore_organization,
    find_organization_id,
    load_organization,
    load_organization_by_id,
    save_contract_with_details,
    load_driver_vehicle,
)

from ui import system_theme, theme  # noqa: F401
from ui.controls.contract_type_selector import ContractTypeSelector  # noqa: F401
from ui.db_manager_dialog import DbManagerDialog  # noqa: F401
from ui.diagnostics_menu import DiagnosticsMenuMixin  # noqa: F401
from ui.icons import action_icon as _action_icon  # noqa: F401
from ui.icons import resource_path as _resource_path  # noqa: F401
from ui.icons import tab_icon as _tab_icon  # noqa: F401
from ui.icons import themed_icon as _themed_icon  # noqa: F401
from ui.navigation import SideNav  # noqa: F401
from ui.qt_shutdown import (  # noqa: F401
    cancel_running_tasks,
    wait_for_thread_pools,
    wait_for_threads,
)
from ui.settings_dialog import SettingsDialog  # noqa: F401
from ui.system_theme import SystemThemeWatcher  # noqa: F401
from ui.tabs.carrier_tab import CarrierTab  # noqa: F401
from ui.tabs.contract_tab import ContractTab  # noqa: F401
from ui.tabs.customer_tab import CustomerTab  # noqa: F401
from ui.tabs.driver_tab import DriverTab  # noqa: F401
from ui.tabs.trailer_tab import TrailerTab  # noqa: F401
from ui.tabs.vehicles_tab import VehiclesTab  # noqa: F401
from ui.widgets.table_helpers import save_all_column_widths  # noqa: F401
from ui.main_window.settings import _validate_gigachat_key  # noqa: F401
from ui.main_window.window import (  # noqa: F401
    TEMPLATES_DIR,
    _PROJECT_ROOT,
)

__all__ = [
    "MainWindow",
    "RecognitionTask",
    "RecognitionSignals",
    "BulkPasteDialog",
    "LayoutMixin",
    "SettingsMixin",
    "ThemeMixin",
    "DataMixin",
    "ContractMixin",
    "SaveMixin",
    "LoadMixin",
    "DriverMatchMixin",
    "RecognitionMixin",
    "CloseMixin",
]
