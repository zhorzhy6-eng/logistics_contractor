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
import weakref

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
    Фабрики окон по типам договоров (ЭТАП 2C).

    Окна создаются лениво: WindowManager вызовет нужную фабрику только
    при первом открытии типа. Импорты — внутри функции: main.py
    импортируется и в тестах (tests/test_logging_config.py), где поднимать
    Qt не нужно.

    «Экспедиторство» — рабочее окно (MainWindow), у остальных типов теперь
    свои окна-каркасы (Формика, Логистикс Рус, Разовая аренда, Хавалы).
    Ключи берутся из ContractType и picker_order, а не из литералов:
    переименование типа не сломает запуск.
    """
    from core.contracts.contract_types import DEFAULT_CONTRACT_TYPE
    from ui.main_window import MainWindow
    from ui.windows.arenda_ts import ArendaTsWindow
    from ui.windows.formika import FormikaWindow
    from ui.windows.havaly import HavalyWindow
    from ui.windows.logistiks_rus import LogistiksRusWindow

    def make_main_window():
        return MainWindow()

    def make_formika_window():
        return FormikaWindow()

    def make_logistiks_rus_window():
        return LogistiksRusWindow()

    def make_arenda_ts_window():
        return ArendaTsWindow()

    def make_havaly_window():
        return HavalyWindow()

    return {
        DEFAULT_CONTRACT_TYPE.value: make_main_window,
        "formika": make_formika_window,
        "logistiks_rus": make_logistiks_rus_window,
        "arenda_ts": make_arenda_ts_window,
        "zayavka_excel": make_havaly_window,
    }


def _make_signals_receiver(manager, app):
    """
    Создаёт приёмник сигналов окон (ЭТАП 2C).

    Приёмник — QObject со слотами «переключить тип» и «выйти». Ссылки на
    менеджер окон и приложение в нём СЛАБЫЕ: сильные дали бы цикл
    «окно → сигнал → приёмник → менеджер → окно», из-за которого процесс
    падал при завершении (грабли ЭТАПА 2B, шаг 2B.7).

    Qt импортируется внутри функции: main.py импортируется и в тестах
    (tests/test_logging_config.py), где поднимать Qt не нужно.
    """
    from PyQt5.QtCore import QObject

    class _WindowSignals(QObject):
        """Слоты сигналов окон типов договоров."""

        def __init__(self):
            super().__init__()
            self._manager_ref = weakref.ref(manager)
            self._app_ref = weakref.ref(app)

        def switch_to_type(self, contract_type: str) -> None:
            """Окно сообщило о выборе другого типа договора."""
            target = self._manager_ref()
            if target is None:
                logger.warning(
                    "Менеджер окон недоступен: переключение на %r пропущено",
                    contract_type,
                )
                return
            _switch(target, self, contract_type)

        def exit_application(self) -> None:
            """Окно сообщило о выходе из программы."""
            logger.info("UI: получен сигнал выхода из окна")
            _do_exit(self._manager_ref(), self._app_ref())

    return _WindowSignals()


def _wire_window(receiver, window) -> None:
    """
    Подключает сигналы окна к приёмнику (один раз на окно).

    Слоты — методы приёмника, без lambda: замыкание, захватывающее окно
    или менеджер, создаёт цикл ссылок Python ↔ Qt (грабли 2B.7).
    """
    if window is None or getattr(window, "_type_signals_wired", False):
        return
    window._type_signals_wired = True

    switch = getattr(window, "switch_to_type_requested", None)
    if switch is not None:
        switch.connect(receiver.switch_to_type)
        logger.debug("Сигналы окна подключены: %s", type(window).__name__)

    exit_signal = getattr(window, "exit_requested", None)
    if exit_signal is not None:
        exit_signal.connect(receiver.exit_application)


def _switch(manager, receiver, contract_type: str):
    """
    Показывает окно типа договора и подключает его сигналы (ЭТАП 2C).

    Окна создаются лениво, поэтому подключать сигналы нужно при каждом
    переключении: у только что созданного окна связи с приёмником ещё нет.
    Повторное подключение исключено флагом на самом окне.

    Возвращает показанное окно или None, если окна для типа нет.
    """
    previous_type = manager.current_type()
    window = manager.switch_to(contract_type)
    if window is None:
        # Окна для типа нет: возвращаем селектор к прежнему типу, иначе
        # пользователь увидит в списке тип, который не открылся.
        selector = getattr(manager.current_window(), "selector", None)
        if selector is not None and previous_type:
            selector.set_current_type(previous_type)
        return None

    _wire_window(receiver, window)

    selector = getattr(window, "selector", None)
    if selector is not None:
        # Окно могли открыть повторно (возврат к типу): список типов должен
        # показывать его тип, а не прошлый выбор пользователя.
        selector.set_current_type(contract_type)

    return window


def _do_exit(manager, app) -> None:
    """
    Штатный выход из приложения (ЭТАП 2C).

    Крестик окна только прячет его, поэтому выход выполняет эта функция:
    окна закрываются по-настоящему (WindowManager.close_all()), затем
    завершается цикл событий.
    """
    logger.info("UI: выход из приложения")
    if manager is not None:
        manager.close_all()
    if app is not None:
        app.quit()


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

        # Приёмник сигналов окон (ЭТАП 2C): селектор типа в шапке просит
        # переключить окно, кнопка «Выход» — завершить приложение.
        receiver = _make_signals_receiver(manager, app)

        window = _switch(manager, receiver, contract_type)
        if window is None:
            logger.error(f"Не удалось открыть окно типа договора: {contract_type}")
            return 1

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
