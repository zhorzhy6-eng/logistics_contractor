#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Точка входа в приложение «Умный конструктор договоров».
Создаёт QApplication, инициализирует БД и открывает главное окно.

Автор: Архитектор-разработчик (30 лет опыта)
Дата: 09.09.2026
"""

import sys
import logging
import os
import argparse

# ── Логирование — один раз, из центрального конфига ──
# Вызывается ДО всех остальных импортов, чтобы их логгеры унаследовали конфиг.
from config.logging_config import setup_logging


def _parse_args(argv=None):
    """Разбор аргументов командной строки (Шаг 4 задания по безопасности)."""
    parser = argparse.ArgumentParser(
        description="Умный конструктор договоров перевозки"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help=(
            "подробный лог: logs/debug.log с уровнем DEBUG "
            "(logs/app.log всегда остаётся INFO)"
        ),
    )
    return parser.parse_args(argv)


_ARGS = _parse_args()
setup_logging(debug=_ARGS.debug)

logger = logging.getLogger("main")


def main() -> int:
    """
    Главная функция. Создаёт приложение и запускает его.
    Возвращает код выхода (0 — успех, 1 — ошибка).
    """
    try:
        logger.info("=== Запуск приложения ===")
        if _ARGS.debug:
            logger.info("Режим отладки: в лог пишутся DEBUG-сообщения")

        # Импортируем PyQt5 здесь, чтобы логирование уже работало
        from PyQt5.QtWidgets import QApplication

        # Импортируем модуль БД — инициализация SQLite
        from db.database import init_database
        init_database()

        # Импортируем главное окно
        from ui.main_window import MainWindow

        # Создаём приложение
        app = QApplication(sys.argv)

        # Устанавливаем стиль
        app.setStyle("Fusion")

        # Единая светлая тема (палитра, блоки, кнопки, вкладки, статус-бар)
        from ui.theme import apply_theme
        apply_theme(app)

        # Создаём и показываем главное окно
        window = MainWindow()
        window.show()

        logger.info("Приложение успешно запущено")

        # Запускаем цикл событий
        return app.exec_()

    except Exception as e:
        logger.error(f"Критическая ошибка при запуске: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())