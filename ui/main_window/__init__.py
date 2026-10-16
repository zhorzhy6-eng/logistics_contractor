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
"""

# ── Распознавание: классы задачи уже переехали (подэтап 1.C) ──
from ui.main_window.recognition import (  # noqa: F401
    BulkPasteDialog,
    RecognitionMixin,
    RecognitionSignals,
    RecognitionTask,
)

# ── Привязка водителя к перевозчику (подэтап 1.D) ──
from ui.main_window.driver_match import DriverMatchMixin  # noqa: F401

# ── Сохранение в базу (подэтап 1.E) ──
from ui.main_window.save import SaveMixin  # noqa: F401

# ── ВРЕМЕННО (до 1.N): каркас пока берётся из прежнего файла ──
# Пока миксины наполняются по одному (1.C–1.L), класс MainWindow живёт в
# `ui/_main_window_legacy.py` — так окно и 30+ UI-тестов работают на каждом
# шаге. В 1.N этот импорт заменяется на `ui.main_window.window`, а сам файл
# удаляется.
from ui._main_window_legacy import MainWindow  # noqa: F401

__all__ = [
    "MainWindow",
    "RecognitionTask",
    "RecognitionSignals",
    "BulkPasteDialog",
    "RecognitionMixin",
    "DriverMatchMixin",
    "SaveMixin",
]
