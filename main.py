#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Точка входа в приложение «Умный конструктор договоров».
Создаёт QApplication, инициализирует БД, спрашивает тип договора и открывает
соответствующее окно: «Экспедиторство» (договор-заявка на перевозку) —
рабочее MainWindow, остальные типы — окно-заглушка «в разработке».

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


def _choose_contract_type():
    """
    Спрашивает тип договора при запуске (ЭТАП 1: инфраструктура типов).

    Возвращает ключ ContractType или None, если пользователь отказался
    от выбора (тогда приложение закрывается). Вызывается после настройки
    оформления: диалогу нужен созданный QApplication с применённой темой.

    PyQt5 импортируется внутри функции: main.py импортируется и в тестах
    (tests/test_logging_config.py), где поднимать Qt не нужно.
    """
    from PyQt5.QtWidgets import QDialog

    from ui.contract_picker import ContractPickerDialog

    dialog = ContractPickerDialog()
    if dialog.exec_() != QDialog.Accepted:
        return None
    return dialog.selected_type()


def _load_contract_types() -> None:
    """
    Прогревает реестр типов договоров (загрузка изолирована по типам).

    Сбой импорта одного типа не мешает остальным: причина попадает
    в failures() и логируется, приложение продолжает работу.
    """
    from core.contracts.registry import ContractTypeRegistry

    ContractTypeRegistry.load_builtin()
    failures = ContractTypeRegistry.failures()
    if failures:
        logger.warning(f"Типы договоров загружены не полностью: {failures}")


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

        # Создаём приложение
        app = QApplication(sys.argv)

        from ui.action_logging import install_action_logging
        install_action_logging(app)

        # Устанавливаем стиль
        app.setStyle("Fusion")

        # Тема оформления: режим Windows (если включено следование за ним)
        # или выбор пользователя из config/settings.json — ui/system_theme.py
        from core.settings_service import get_settings_service
        from ui.system_theme import preferred_theme
        from ui.theme import apply_theme
        apply_theme(app, preferred_theme(get_settings_service()))

        # ── Реестр типов договоров (ЭТАП 1) ──
        _load_contract_types()

        # ── Тип договора: спрашиваем ДО открытия окна ──
        # «Экспедиторство» — текущий рабочий тип (MainWindow), остальные
        # типы показывают окно-заглушку «в разработке».
        from core.contracts.contract_types import DEFAULT_CONTRACT_TYPE
        from ui.contract_picker import picker_title

        contract_type = _choose_contract_type()
        if contract_type is None:
            logger.info("Тип договора не выбран — приложение закрывается")
            return 0

        if contract_type == DEFAULT_CONTRACT_TYPE.value:
            # Импорт главного окна — только для рабочего типа: заглушке
            # вкладки, вкладки распознавания и клиент GigaChat не нужны.
            from ui.main_window import MainWindow
            window = MainWindow()
        else:
            from ui.placeholder_window import PlaceholderWindow
            window = PlaceholderWindow(contract_type, picker_title(contract_type))

        # Создаём и показываем окно
        window.show()
        logger.info(f"Открыт тип договора: {contract_type}")

        logger.info("Приложение успешно запущено")

        # Запускаем цикл событий
        exit_code = app.exec_()
        logger.info("Приложение закрыто: код=%s", exit_code)
        return exit_code

    except Exception as e:
        logger.error(f"Критическая ошибка при запуске: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
