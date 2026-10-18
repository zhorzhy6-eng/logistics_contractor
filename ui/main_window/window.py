# -*- coding: utf-8 -*-
"""
Каркас главного окна Экспедиторства.

Этап 6 (16.10.2026), подэтап 1.M: класс `MainWindow` собран здесь из
девяти миксинов + `DiagnosticsMenuMixin` + `QMainWindow`. Логика не
менялась — это тот же класс, что и раньше, только методы живут по модулям.

ПОРЯДОК НАСЛЕДОВАНИЯ (MRO) — не косметика:

    LayoutMixin        создаёт виджеты и вкладки, на которые ссылаются все
                       остальные миксины (вкладки, сайдбар, статус-бар,
                       кнопки, прогресс-бар)
    SettingsMixin      создаёт клиента GigaChat и открывает диалог настроек
    ThemeMixin         применяет тему к УЖЕ созданным виджетам
    DataMixin          сбор данных и проверка перед генерацией
    ContractMixin      создание договора
    SaveMixin          сохранение в справочники и договоры
    LoadMixin          загрузка записей из справочника
    DriverMatchMixin   согласование перевозчика с привязкой водителя
    RecognitionMixin   распознавание: задача, сигналы, слоты прогресса
    CloseMixin         закрытие окна (скрытие крестиком, force_close)
    DiagnosticsMenuMixin «Справка → Диагностика»
    QMainWindow        база Qt — ВСЕГДА последняя

Имён методов, которые встречаются в двух миксинах, нет, поэтому порядок
влияет только на читаемость. Он всё равно зафиксирован тестом
(`tests/test_ui_main_window_layout.py`): перестановка миксина ломает
порядок создания виджетов и роняет UI-тесты.

У МИКСИНОВ НЕТ `__init__` — и это осознанно. Всю инициализацию делает
`MainWindow.__init__` (как и до разбиения), поэтому MRO-цепочка `__init__`
не может пойти не в том порядке.

ВАЖНО: вызов `install_agent_safely(self)` обязан остаться ПРЯМО В ТЕЛЕ
`MainWindow.__init__`. Приватный тест
`tests/test_agent_all_windows.py::test_main_window_installs_agent` читает
исходный текст метода через `inspect.getsource(MainWindow.__init__)` и ищет
там эту строку. Перенос вызова в миксин или в `init_agent()` сломает тест
приватной части.
"""

import logging
from typing import Optional

from PyQt5.QtCore import pyqtSignal, QThreadPool
from PyQt5.QtWidgets import QMainWindow

from core import app_paths
from core.contract_generator import ContractGenerator
from core.gigachat_client import GigaChatClient
from core.mirror import SOURCE_TYPE
from core.settings_service import get_settings_service

from ui.diagnostics_menu import DiagnosticsMenuMixin
from ui.main_window.close import CloseMixin
from ui.main_window.contract import ContractMixin
from ui.main_window.data import DataMixin
from ui.main_window.driver_match import DriverMatchMixin
from ui.main_window.layout import LayoutMixin
from ui.main_window.load import LoadMixin
from ui.main_window.recognition import RecognitionMixin, RecognitionTask
from ui.main_window.save import SaveMixin
from ui.main_window.settings import SettingsMixin
from ui.main_window.theme import ThemeMixin
from ui.system_theme import SystemThemeWatcher

# Имя логгера задано ЯВНО (а не __name__): иначе после переезда записи
# выпали бы из настройки `config/logging_config.py`
# (APP_LOGGERS = "core", "ui", "db", "config", "main", "qt").
logger = logging.getLogger("ui.main_window")

#: Папка шаблонов DOCX. Читается из ресурсов сборки (core/app_paths.py):
#: в .exe шаблоны лежат в `_MEIPASS`, а не рядом с модулем в архиве.
TEMPLATES_DIR = str(app_paths.resource_path("templates"))

#: Прежнее имя корня — оставлено ради старых импортов
#: (`from ui.main_window import _PROJECT_ROOT`). Значение прежнее по смыслу:
#: это корень РЕСУРСОВ, от которого считается `templates/`. Владелец путей
#: теперь один — core/app_paths.py, второй счёт корня здесь не заводится.
_PROJECT_ROOT = str(app_paths.resource_path())


class MainWindow(
    LayoutMixin,
    SettingsMixin,
    ThemeMixin,
    DataMixin,
    ContractMixin,
    SaveMixin,
    LoadMixin,
    DriverMatchMixin,
    RecognitionMixin,
    CloseMixin,
    DiagnosticsMenuMixin,
    QMainWindow,
):
    """Главное окно Экспедиторства («Договоры перевозки»)."""

    # ── Переключение типа и выход (ЭТАП 2C) ──
    # Окно «Экспедиторство» — не подкласс BaseContractWindow (нельзя ломать
    # рабочую вкладку-логику), но сигналы у него те же: main.py подключает
    # их к WindowManager и завершению приложения.
    #: Пользователь выбрал другой тип договора в селекторе шапки.
    switch_to_type_requested = pyqtSignal(str)
    #: Пользователь нажал «Выход».
    exit_requested = pyqtSignal()

    #: Ключ типа договора (ШАГ FIX-4). У окон типов это атрибут
    #: BaseContractWindow; здесь он нужен зеркалу данных: окно-цель ищет
    #: источник по значению CONTRACT_TYPE, а не по имени класса —
    #: ui/windows не должен зависеть от ui/main_window.
    CONTRACT_TYPE = SOURCE_TYPE

    # Ожидаемые разделы ответа модели. Нужны для debug-лога: видно не только
    # то, что распознано, но и то, что модель вообще не вернула.
    _RECOGNITION_SECTIONS = (
        ("driver", "водитель"),
        ("customer", "заказчик"),
        ("carrier", "перевозчик"),
        ("vehicles", "перевозимые ТС"),
        ("tractor", "тягач"),
        ("trailer", "полуприцеп"),
        ("contract", "договор"),
    )

    #: Имена иконок вкладок в порядке добавления (см. _init_ui). Нужны, чтобы
    #: пересобрать иконки сайдбара при переключении на тёмную тему.
    _TAB_ICON_NAMES = (
        "carrier.svg", "driver.svg", "trailer.svg",
        "vehicles.svg", "contract.svg", "customer.svg",
    )

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Генератор договоров перевозки")
        self.resize(1200, 900)

        # База данных инициализируется один раз в main.py (P0-фикс:
        # раньше init_database() вызывался и здесь, и в точке входа).

        # ── Настройки: единый сервис с перечитыванием (Шаг 5) ──
        self.settings_service = get_settings_service()

        self.gigachat: Optional[GigaChatClient] = None
        self._init_gigachat_client(show_dialog=True)

        self.contract_generator = ContractGenerator(templates_dir=TEMPLATES_DIR)
        logger.info("ContractGenerator инициализирован")

        # ── Распознавание в пуле потоков (Шаг 8 оптимизации) ──
        # Пул управляет жизненным циклом задач: повторный запуск больше не
        # перезаписывает ссылку на поток, а отменённая задача не применяет
        # свой результат к интерфейсу.
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(2)
        self.recognition_task: Optional[RecognitionTask] = None

        self._init_ui()

        # ── Тема Windows: следим, пока пользователь не выбрал тему сам ──
        self.system_theme_watcher = SystemThemeWatcher(self)
        self.system_theme_watcher.theme_changed.connect(self._on_system_theme_changed)
        self._sync_system_theme_watcher()

        # ── Зеркало данных (ШАГ FIX-4): окно сообщает о себе ──
        # MainWindow — источник переноса в окна клиентских типов. Окна типов
        # находят его по реестру (ui/windows/base_window.py), а не по классу:
        # им нужен только CONTRACT_TYPE и методы вкладок.
        from ui.windows.base_window import register_source_window

        register_source_window(self)

        # ── Максимилиан: иконка в углу и панель (задание, часть D.4) ──
        # Импорт и установка внутри метода: агент — надстройка, и его сбой
        # не должен мешать открытию окна (см. install_agent_safely).
        from ui.agent_widget import install_agent_safely

        install_agent_safely(self)

        # ── Диагностика: «Справка → Диагностика» (задание, часть 3, A.1) ──
        self.init_diagnostics_menu()

        logger.info("MainWindow инициализировано")

    def _tab_keys(self) -> tuple:
        """
        Ключи вкладок окна в порядке добавления.

        У окон типов раскладка объявлена классом (`_TAB_KEYS`), а здесь
        вкладки создаются отдельными атрибутами — метод приводит их к тому
        же виду, чтобы инструменты агента читали форму одинаково у всех окон.
        """
        return (
            "carrier_tab", "driver_tab", "trailer_tab",
            "vehicles_tab", "contract_tab", "customer_tab",
        )
