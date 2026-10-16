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

Публичный API не изменился: `from ui.main_window import MainWindow`
работает по-прежнему и ведёт к ТОМУ ЖЕ классу.

ЧАСТЬ 0 (16.10.2026) — совместимость на время разбиения:
    пока модули миксинов наполняются по одному (1.C–1.L), `MainWindow`
    берётся из прежнего файла `ui/_main_window_legacy.py`. В 1.N этот
    модуль удаляется, и остаётся единственная строка
    `from ui.main_window.window import MainWindow`.
"""

# Импорты добавятся в 1.N (и временная подстановка — в 1.B).
#
# ВРЕМЕННО (до 1.N): класс берётся из прежнего файла, чтобы окно и все
# 30+ UI-тестов продолжали работать, пока миксины наполняются по одному.
from ui._main_window_legacy import (  # noqa: F401
    BulkPasteDialog,
    MainWindow,
    RecognitionSignals,
    RecognitionTask,
    logger,
)

__all__ = [
    "MainWindow",
    "RecognitionTask",
    "RecognitionSignals",
    "BulkPasteDialog",
]

