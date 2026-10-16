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
# Тесты (и приватная часть) читают у модуля его собственные имена, а не
# только класс: `module.logger` и подмена функций базы через
# `monkeypatch.setattr(module, "load_organization_by_id", …)`. Логгер выше —
# ТОТ ЖЕ объект, что и в миксинах (одно имя "ui.main_window"), поэтому
# `caplog.set_level(logging.DEBUG, logger="ui.main_window")` продолжает
# видеть записи окна.
from db.database import load_organization_by_id  # noqa: F401

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
