#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Точка входа в приложение «Умный конструктор договоров».
Создаёт QApplication, инициализирует БД, спрашивает тип договора и открывает
соответствующее окно: «Экспедиторство» (договор-заявка на перевозку) —
рабочее MainWindow, клиентские типы (приватная часть проекта) — свои окна
с вкладками.

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

# ── Перехват необработанных исключений (главный поток и рабочие) ──
# Импорт без побочных действий: хук ставится вызовом ниже, уже после
# настройки логирования (иначе писать будет некуда).
from core.crash_handler import install_crash_handler

# ── Диагностика падений НИЖЕ уровня Python (faulthandler + сообщения Qt) ──
# Падение при выходе (0xC0000005/0xC0000409 в Qt5Core.dll) идёт мимо
# sys.excepthook, поэтому нужны инструменты уровнем ниже. Импорт также без
# побочных действий: всё включается вызовом после setup_logging().
from core.qt_crash_handler import install_crash_diagnostics, mark_normal_exit

# ── Приватная часть проекта (клиентские типы договоров) ──
# Вызов обязан идти ДО первого импорта пакетов типов: attach_private_root()
# не только добавляет приватную папку в sys.path, но и расширяет __path__
# публичных пакетов core/ui — иначе подпакеты вида core.contracts.<тип>
# не нашлись бы (подробности в докстроке core/private_path.py). Приватной
# папки нет — работаем в режиме ядра, только «Экспедиторство».
from core.private_path import attach_private_root, optional_module

attach_private_root()


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
    parser.add_argument(
        "--selftest", action="store_true",
        help=(
            "самопроверка сборки без запуска окна: шаблоны, правовая база, "
            "приватная часть, сертификат, база, ключи; отчёт — в selftest.txt "
            "рядом с программой (код возврата 0 — всё в порядке)"
        ),
    )
    return parser.parse_args(argv)


_ARGS = _parse_args()
setup_logging(debug=_ARGS.debug)

# ── Необработанные исключения — в logs/errors.log ──
# Ставится сразу после логирования и ДО QApplication, а также до импорта
# db.database: падение на старте тоже должно попасть в журнал. У pythonw.exe
# stderr нет, поэтому без этого хука traceback исчезал бесследно.
install_crash_handler()

# ── Диагностика аварий: faulthandler, сообщения Qt, маркер выхода ──
# faulthandler и обработчик сообщений Qt включаются только в режиме --debug
# (обычная работа не должна замедляться). Строка «Диагностика аварий: pid=…»
# и запись «Приложение завершается нормально» пишутся всегда: именно они
# отличают штатный выход от аварийного, когда падение случится без --debug.
install_crash_diagnostics(debug=_ARGS.debug)

logger = logging.getLogger("main")


def _prepare_user_data() -> None:
    """
    Готовит папку данных пользователя ДО первого обращения к базе.

    Что делает, по порядку:

    1. **переносит данные старых версий** — базу и настройки, лежавшие
       рядом с программой, в папку данных (`migrate_old_data`). Копирует и
       не удаляет: старая папка остаётся как была, решает пользователь;
    2. создаёт папку данных и подпапки (`config/`, `logs/`, `output/`);
    3. кладёт `config/settings.json` из образца, если его ещё нет.

    Зачем отдельным шагом. До перехода на `%APPDATA%` данные лежали рядом с
    программой и терялись при обновлении, а в свежей установке папки данных
    нет вовсе: `sqlite3.connect` создаёт файл базы, но не папку. Данные
    теперь живут в `%APPDATA%\\LogisticsContractor` (см. core/data_paths.py).

    Сбой подготовки не валит запуск: причина пишется в журнал, программа
    продолжает работу (умолчания настроек работают и без файла, а понятная
    ошибка про базу появится при инициализации).
    """
    from core import data_paths

    try:
        # 1. Старые данные — ПЕРВЫМИ: папку данных создаёт сама миграция,
        #    а переносить нужно до первого открытия базы, иначе приложение
        #    создаст пустую базу и старая уже не понадобится.
        copied = data_paths.migrate_old_data(data_paths.app_root_for_migration())
        if copied:
            logger.info(
                "Данные старой версии перенесены (%d): %s",
                len(copied), "; ".join(copied),
            )

        # 2. Папка данных и подпапки.
        data_paths.ensure_data_root()
        logger.info("Папка данных: %s", data_paths.describe())

        # 3. Настройки из образца. Переменная LOGISTICS_SETTINGS_PATH
        #    главнее папки данных: при портативном запуске и в тестах файл
        #    настроек лежит не здесь, и лишняя копия только путала бы.
        if os.environ.get("LOGISTICS_SETTINGS_PATH", "").strip():
            logger.info(
                "Настройки заданы переменной LOGISTICS_SETTINGS_PATH — "
                "образец не копируется"
            )
            return

        settings_path = data_paths.ensure_settings_file()
        if settings_path.is_file():
            logger.info("Настройки: %s", settings_path)
    except Exception as error:  # noqa: BLE001 — запуск важнее подготовки
        logger.error(f"Не удалось подготовить папку данных: {error}", exc_info=True)


#: Ключ типа договора → (модуль окна, класс окна). Модули живут в приватной
#: части и подключаются через core.private_path.optional_module: тип без
#: модуля в список не попадает. Ключи — значения ContractType.
_TYPE_WINDOW_MODULES = {
    "arenda_ts": ("ui.windows.arenda_ts", "ArendaTsWindow"),
    "formika": ("ui.windows.formika", "FormikaWindow"),
    "logistiks_rus": ("ui.windows.logistiks_rus", "LogistiksRusWindow"),
    "zayavka_excel": ("ui.windows.havaly", "HavalyWindow"),
}


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


def _ask_keys_if_missing() -> bool:
    """
    Первый запуск: спрашивает ключи API, если ключа GigaChat нет.

    Окно (ui/keys_dialog.py) показывается САМО и только тогда, когда ключа
    нет: настроенный компьютер не получает лишнего шага при каждом запуске.
    Ключи хранятся в системном хранилище Windows (core.secrets_store),
    поэтому в файлах проекта их по-прежнему нет.

    Отказ оператора — не ошибка: программа работает в ограниченном режиме
    (генерация по шаблонам и ручное заполнение формы), а ключи можно
    ввести позже из настроек кнопкой «Ключи API…».

    :return: True, если ключи введены (или уже были).
    """
    from core import secrets_store

    # Хранилище недоступно — спрашивать некуда сохранять: окно только
    # пообещало бы сохранить ключ и не смогло. Причина уже в журнале
    # (infrastructure/secrets/store.py пишет её сам).
    if not secrets_store.keyring_available():
        logger.warning(
            "Системное хранилище ключей недоступно — окно ввода ключей "
            "не показывается"
        )
        return False

    if secrets_store.has_gigachat_key():
        return True

    # Qt импортируется внутри функции: main.py импортируется и в тестах
    # (tests/.../test_logging_config.py), где поднимать Qt не нужно.
    from PyQt5.QtWidgets import QDialog

    from ui.keys_dialog import KeysDialog, should_prompt_on_startup

    if not should_prompt_on_startup():
        return False

    dialog = KeysDialog(require_gigachat=True)
    try:
        accepted = dialog.exec_() == QDialog.Accepted
    finally:
        dialog.deleteLater()

    if accepted:
        logger.info("Ключи API введены при первом запуске")
    else:
        logger.info(
            "Ключи API не введены — программа работает в ограниченном режиме"
        )
    return accepted


def _load_contract_types() -> None:
    """
    Прогревает реестр типов договоров (загрузка изолирована по типам).

    Сбой импорта одного типа не мешает остальным: причина попадает
    в failures() и логируется, приложение продолжает работу.

    После загрузки типов пересобирается список промптов распознавания:
    модули промптов ищутся по ключам реестра, а он наполняется именно
    здесь (см. core/prompts/__init__.py::refresh_prompts).
    """
    from core.contracts.registry import ContractTypeRegistry
    from core.prompts import refresh_prompts

    ContractTypeRegistry.load_builtin()
    failures = ContractTypeRegistry.failures()
    if failures:
        logger.warning(f"Типы договоров загружены не полностью: {failures}")

    prompt_modules = refresh_prompts()
    logger.info(f"Промпты распознавания найдены: {sorted(prompt_modules)}")


def _optional_class(module_name: str, class_name: str):
    """
    Класс окна из приватной части или None, если её нет.

    Приватная часть подключается вызовом ``attach_private_root()`` в начале
    модуля: там же объяснено, почему обычного ``sys.path`` мало. Нет
    приватной папки — приложение работает в режиме ядра, и клиентские типы
    просто не попадают в список окон.
    """
    module = optional_module(module_name)
    if module is None:
        return None
    return getattr(module, class_name, None)


def _window_factory(window_class):
    """
    Фабрика окна типа договора.

    Именованная функция вместо ``lambda``: замыкание, захватывающее окно
    или менеджер, создаёт цикл ссылок Python ↔ Qt (грабли 2B.7), а лямбда
    в connect запрещена вовсе (AGENTS.md § 5.1).
    """
    def make_window():
        return window_class()

    return make_window


def _make_factories():
    """
    Фабрики окон по типам договоров (ЭТАП 2C).

    Окна создаются лениво: WindowManager вызовет нужную фабрику только
    при первом открытии типа. Импорты — внутри функции: main.py
    импортируется и в тестах (tests/test_logging_config.py), где поднимать
    Qt не нужно.

    «Экспедиторство» — рабочее окно (MainWindow). Окна клиентских типов
    живут в приватной части проекта и подключаются по имени модуля: их
    список — ``_TYPE_WINDOW_MODULES``. Приватной части нет — в списке
    остаётся только «Экспедиторство», и это нормальный режим ядра,
    а не ошибка.

    Ключи берутся из ContractType, а не из литералов: переименование типа
    не сломает запуск.
    """
    from core.contracts.contract_types import DEFAULT_CONTRACT_TYPE
    from ui.main_window import MainWindow

    factories = {DEFAULT_CONTRACT_TYPE.value: _window_factory(MainWindow)}

    for contract_type, (module_name, class_name) in _TYPE_WINDOW_MODULES.items():
        window_class = _optional_class(module_name, class_name)
        if window_class is None:
            logger.info(
                "Окно типа %r недоступно: нет приватного модуля %s",
                contract_type, module_name,
            )
            continue
        factories[contract_type] = _window_factory(window_class)

    return factories


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

    Отдельно закрываются окна Максимилиана, живущие ВНЕ окна программы:
    плавающий кот — самостоятельное окно верхнего уровня, и без явного
    закрытия он остаётся на экране после выхода (баг #18). Окна типов
    зовут `AgentWidget.shutdown()` сами, но страховка нужна: кот мог быть
    создан в окне, которое до этого момента не закрывали.
    """
    logger.info("UI: выход из приложения")

    from ui.agent_widget import close_floating_cats

    close_floating_cats(app)

    if manager is not None:
        manager.close_all()
    if app is not None:
        app.quit()


#: Завершать процесс сразу после выхода из цикла событий, не давая
#: интерпретатору разбирать объекты Python и Qt «вперемешку».
#:
#: Почему так. Выход из приложения падал `0xC0000005` (access violation)
#: ПЛАВАЮЩЕ: примерно в половине запусков, независимо от того, что делал
#: оператор перед выходом. Замер пробой (offscreen, 6 запусков на режим,
#: `tests/_tmp/_diag_exit/plain_probe.py`):
#:
#:   * `app.quit()` без всего прочего          — 4 падения из 6;
#:   * `main._do_exit`                         — 5 падений из 6;
#:   * выход с разбором отложенных событий      — 5 падений из 6;
#:   * `main._do_exit` + немедленный выход      — **0 падений из 6**.
#:
#: Падение живёт не в закрытии окон (оно проверено) и не в рабочих потоках
#: (к моменту выхода живых потоков Python — ноль), а в ЗАВЕРШЕНИИ
#: интерпретатора: Qt и Python разрушают одни и те же объекты в разном
#: порядке. Поэтому процесс завершается явно — после того, как работа
#: действительно закончена.
#:
#: Что при этом НЕ теряется: журнал сбрасывается на диск (`logging.shutdown`
#: закрывает обработчики), база — уже записана, окна и потоки — закрыты
#: циклом выше. Терять нечего: к этому моменту живы только объекты Qt.
EXIT_IMMEDIATELY = True


def _finish_process(exit_code: int) -> None:
    """
    Завершает процесс кодом `exit_code`, минуя разбор объектов Python и Qt.

    Обычный `return` из `main()` оставляет завершение интерпретатору, а тот
    падает (см. ``EXIT_IMMEDIATELY``). Перед выходом журнал сбрасывается на
    диск: `os._exit` не выполняет обработчики завершения, и без сброса
    последние строки журнала остались бы в буфере.
    """
    try:
        # Различитель «штатный выход / аварийный» пишется здесь: `os._exit`
        # не выполняет atexit-хуки, и без этого вызова диагностика считала
        # бы нормальный выход падением.
        mark_normal_exit()
        logging.shutdown()
    except Exception as e:  # noqa: BLE001 — журнал не важнее выхода
        print(f"Не удалось закрыть журнал: {e}", file=sys.stderr)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001 — потока может уже не быть
            pass

    os._exit(int(exit_code))


def main() -> int:
    """
    Главная функция. Создаёт приложение и запускает его.
    Возвращает код выхода (0 — успех, 1 — ошибка).
    """
    exit_code = 1
    try:
        logger.info("=== Запуск приложения ===")
        if _ARGS.debug:
            logger.info("Режим отладки: в лог пишутся DEBUG-сообщения")

        # ── Папка данных (база, настройки, журналы, договоры) ──
        # ДО первого обращения к базе и настройкам: в свежей установке
        # подпапок ещё нет. Данные живут в %APPDATA%\\LogisticsContractor и
        # при обновлении программы не пропадают (core/data_paths.py).
        _prepare_user_data()

        # ── Самопроверка сборки (--selftest) ──
        # Выполняется ДО создания QApplication: это проверка путей и
        # ресурсов, Qt для неё не нужен, а консоли у собранного .exe нет —
        # отчёт пишется файлом рядом с программой (core/selftest.py).
        if _ARGS.selftest:
            from core.selftest import run_selftest

            exit_code = run_selftest()
            logger.info("Самопроверка завершена: код=%s", exit_code)
            return _return_or_exit(exit_code)

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

        # ── Первый запуск: ключи API (Часть 2 задания от 18.10.2026) ──
        # Окно появляется само, только если ключа GigaChat нет; отказ
        # ничего не ломает — программа работает в ограниченном режиме.
        _ask_keys_if_missing()

        # ── Реестр типов договоров (ЭТАП 1) ──
        _load_contract_types()

        # ── Тип договора: спрашиваем ДО открытия окна ──
        # «Экспедиторство» — текущий рабочий тип (MainWindow), остальные
        # типы показывают окно-заглушку «в разработке».
        contract_type = _choose_contract_type()
        if contract_type is None:
            logger.info("Тип договора не выбран — приложение закрывается")
            return _return_or_exit(0)

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
            return _return_or_exit(1)

        logger.info(f"Открыт тип договора: {contract_type}")
        logger.info("Приложение успешно запущено")

        # Запускаем цикл событий
        exit_code = app.exec_()
        logger.info("Приложение закрыто: код=%s", exit_code)

    except Exception as e:
        logger.error(f"Критическая ошибка при запуске: {e}", exc_info=True)
        exit_code = 1

    return _return_or_exit(exit_code)


def _return_or_exit(exit_code: int) -> int:
    """
    Одна дверь для всех выходов из `main()`.

    При ``EXIT_IMMEDIATELY`` процесс завершается здесь и `main()` не
    возвращается вовсе — иначе завершение интерпретатора роняет его
    (см. ``EXIT_IMMEDIATELY``). Возврат нужен тестам и пробам, которым
    важен код выхода, а не сам процесс.
    """
    if EXIT_IMMEDIATELY:
        _finish_process(exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
