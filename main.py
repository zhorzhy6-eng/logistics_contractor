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


def _make_factories():
    """
    Фабрики окон по типам договоров (ЭТАП 2B).

    Окна создаются лениво: WindowManager вызовет нужную фабрику только
    при первом открытии типа. Импорты — внутри функции: main.py
    импортируется и в тестах (tests/test_logging_config.py), где поднимать
    Qt не нужно.

    «Экспедиторство» — рабочий тип (MainWindow), остальные типы (Формика,
    Логистикс Рус, Разовая аренда, Хавалы) показывают окно-заглушку
    «в разработке». Ключ рабочего типа берётся из ContractType, а не из
    литерала: переименование типа не сломает запуск.
    """
    from core.contracts.contract_types import DEFAULT_CONTRACT_TYPE
    from ui.contract_picker import picker_items, picker_title
    from ui.main_window import MainWindow
    from ui.placeholder_window import PlaceholderWindow

    def make_main_window():
        return MainWindow()

    def make_placeholder_factory(contract_type: str):
        def factory():
            return PlaceholderWindow(contract_type, picker_title(contract_type))
        return factory

    factories = {DEFAULT_CONTRACT_TYPE.value: make_main_window}
    for contract_type, _title in picker_items():
        # setdefault: рабочий тип остаётся MainWindow, даже если он есть в списке
        factories.setdefault(contract_type, make_placeholder_factory(contract_type))
    return factories


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

        # Окна типов договоров живут всё время приложения, а закрытие окна —
        # это hide() (данные в формах не теряются). Поэтому «последнее окно
        # закрыто» не должно завершать программу: реальный выход — только
        # явный (WindowManager.close_all()).
        app.setQuitOnLastWindowClosed(False)

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
        contract_type = _choose_contract_type()
        if contract_type is None:
            logger.info("Тип договора не выбран — приложение закрывается")
            return 0

        # ── Окна типов: лениво, через менеджер (ЭТАП 2B) ──
        # Окна не уничтожаются при переключении типа: пользователь может
        # вернуться к уже заполненной форме.
        from ui.windows import WindowManager

        manager = WindowManager(_make_factories())
        window = manager.switch_to(contract_type)
        if window is None:
            logger.error(f"Не удалось открыть окно типа договора: {contract_type}")
            return 1

        logger.info(f"Открыт тип договора: {contract_type}")

        # TODO (ЭТАП 2C): переключение типа из уже открытого окна —
        # по сигналу окна вызывать manager.switch_to(<новый тип>).
        # Пока такого сигнала нет: тип выбирается один раз при запуске.

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
