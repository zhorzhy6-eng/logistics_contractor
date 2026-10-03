import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional

from PyQt5.QtCore import Qt, pyqtSignal, QObject, QRunnable, QThreadPool, QTimer, QSize
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPushButton, QMessageBox, QDialog, QLabel, QFrame,
    QTextEdit, QDialogButtonBox, QApplication, QProgressBar,
)

from db.database import (
    init_database,
    save_driver,
    save_driver_vehicle,
    save_organization,
    save_contract_with_details,
    load_driver_vehicle,
)

from core import audit, secrets_store
from core.contract_data import ContractData
from core.contract_generator import ContractGenerator
from core.gigachat_client import GigaChatClient
from core.recognizer import filled_only, filled_only_list
from core.secrets_store import MISSING_KEY_MESSAGE
from core.settings_service import get_settings_service
from core.trace import filled_fields_summary
from core.validator import ValidationReport, Validator

from ui import theme
from ui import system_theme
from ui.icons import action_icon as _action_icon
from ui.icons import resource_path as _resource_path
from ui.icons import tab_icon as _tab_icon
from ui.icons import themed_icon as _themed_icon
from ui.system_theme import SystemThemeWatcher
from ui.tabs.driver_tab import DriverTab
from ui.tabs.customer_tab import CustomerTab
from ui.tabs.carrier_tab import CarrierTab
from ui.tabs.vehicles_tab import VehiclesTab
from ui.tabs.trailer_tab import TrailerTab
from ui.tabs.contract_tab import ContractTab
from ui.db_manager_dialog import DbManagerDialog
from ui.settings_dialog import SettingsDialog
from ui.navigation import SideNav

logger = logging.getLogger(__name__)


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(_PROJECT_ROOT, "templates")

# ── Иконки тем (ЭТАП 2B) ──
# Помощники _resource_path/_themed_icon/_tab_icon/_action_icon переехали
# в ui/icons.py: теперь их используют и вкладки (кнопки «Создать договор»
# и «Очистить форму»). Имена оставлены алиасами: внешний код, который
# импортировал их отсюда, продолжает работать.


def _validate_gigachat_key(key: str) -> Optional[str]:
    if not key:
        return (
            f"{MISSING_KEY_MESSAGE}\n\n"
            "Ключ хранится в системном хранилище Windows "
            "(см. core/secrets_store.py), а не в файлах проекта."
        )
    try:
        key.encode("ascii")
    except UnicodeEncodeError as e:
        return (
            f"Ключ GigaChat содержит не-ASCII символы (позиция {e.start}).\n\n"
            f"HTTP-заголовок Authorization допускает только latin-1. "
            f"Проверьте ключ: python set_key.py --check"
        )
    return None


class RecognitionSignals(QObject):
    """Сигналы задачи распознавания (Шаг 8 оптимизации)."""

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()
    progress = pyqtSignal(int, str)   # процент, текст статуса


class RecognitionTask(QRunnable):
    """
    Задача распознавания для QThreadPool (Шаг 8 оптимизации).

    Отличие от прежнего QThread:
      * пул потоков управляет жизненным циклом — объект не «теряется»
        при повторном запуске (раньше self.recognition_thread перезаписывался);
      * есть отмена: HTTP-запрос прервать нельзя, но результат отменённой
        задачи не применяется к интерфейсу;
      * есть прогресс: пользователь видит, на каком шаге распознавание.
    """

    def __init__(self, client: GigaChatClient, text: str):
        super().__init__()
        self.client = client
        self.text = text
        self.signals = RecognitionSignals()
        self._cancelled = False

    # ── Отмена ──
    def cancel(self) -> None:
        self._cancelled = True
        logger.info("Задача распознавания помечена как отменённая")

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self):
        try:
            self.signals.progress.emit(5, "Подготовка текста...")

            if self._cancelled:
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(
                20, f"Отправка в GigaChat ({len(self.text)} символов)..."
            )
            data = self.client.recognize_text(self.text)

            # Результат отменённой задачи не применяем
            if self._cancelled:
                logger.info("Распознавание отменено — результат не применяется")
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(90, "Разбор ответа модели...")

            if data is None:
                self.signals.error.emit("Модель вернула пустой ответ")
                return

            self.signals.progress.emit(100, "Распознавание завершено")
            self.signals.finished.emit(data)

        except Exception as e:
            if self._cancelled:
                self.signals.cancelled.emit()
                return
            logger.exception("Ошибка в задаче распознавания")
            self.signals.error.emit(str(e))


class MainWindow(QMainWindow):
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

        logger.info("MainWindow инициализировано")

    # --------------------------------------------------------
    # НАСТРОЙКИ  ← Шаг 5 рефакторинга
    # --------------------------------------------------------
    def _init_gigachat_client(self, show_dialog: bool = True) -> bool:
        """
        Создаёт (или пересоздаёт) клиента GigaChat по текущим настройкам.

        Ключ берётся из системного хранилища (Windows Credential Manager),
        а не из config/settings.json — см. Шаг 2 задания по безопасности.
        Вызывается при старте и после сохранения настроек, поэтому
        перезапуск приложения для смены ключа не нужен.
        """
        self.gigachat = None

        auth_key = secrets_store.get_gigachat_key() or ""
        key_error = _validate_gigachat_key(auth_key)
        if key_error:
            logger.error(f"Ключ GigaChat недоступен: {key_error}")
            audit.log_denied("gigachat_client_init", "ключ не найден в хранилище")
            if show_dialog:
                QMessageBox.critical(
                    self, "Ошибка GigaChat",
                    f"Не удалось инициализировать GigaChat:\n\n{key_error}"
                )
            return False

        try:
            self.gigachat = GigaChatClient(
                auth_key=auth_key,
                scope=self.settings_service.get_str("gigachat_scope", "GIGACHAT_API_PERS"),
                model=self.settings_service.get_str("gigachat_model", "GigaChat-2"),
                timeout=self.settings_service.get_int("gigachat_timeout", 150),
                verify_ssl=self.settings_service.get_bool("gigachat_verify_ssl", True),
                ca_bundle=self.settings_service.get_str("gigachat_ca_bundle", "") or None,
            )
            logger.info("GigaChatClient инициализирован")
            return True
        except Exception as e:
            logger.exception("Не удалось инициализировать GigaChat")
            if show_dialog:
                QMessageBox.critical(
                    self, "Ошибка GigaChat",
                    f"Не удалось инициализировать GigaChat:\n{e}"
                )
            return False

    def _on_open_settings(self):
        """Диалог настроек: провайдер и его параметры."""
        logger.info("Открытие диалога настроек")
        self._log_ui_action("нажата кнопка «Настройки»")
        audit.log_event("settings_opened")

        dialog = SettingsDialog(self.settings_service.as_dict(), self)
        if dialog.exec_() != QDialog.Accepted:
            logger.info("Настройки не изменены")
            self._log_ui_action("диалог настроек закрыт без изменений")
            return

        new_settings = dialog.get_settings()
        saved = self.settings_service.update(new_settings)

        if not saved:
            QMessageBox.warning(
                self, "Настройки",
                "Не удалось записать config/settings.json.\n\n"
                "Изменения действуют до перезапуска приложения."
            )

        # Применяем сразу: клиент пересоздаётся с новыми параметрами
        self._init_gigachat_client(show_dialog=False)

        # Тема могла измениться в диалоге — применяем без перезапуска.
        # Настройки уже записаны выше, поэтому повторно их не сохраняем.
        self._apply_theme_choice(
            new_settings.get("ui_theme", theme.active_theme()), save=False
        )
        # Пользователь мог включить или выключить следование за Windows
        self._sync_system_theme_watcher()

        logger.info(f"Настройки применены: провайдер={new_settings.get('provider')}")
        self.statusBar().showMessage("Настройки применены", 5000)
        QMessageBox.information(self, "Настройки", "Настройки применены.")

    # --------------------------------------------------------
    # ТЕМА ОФОРМЛЕНИЯ
    # --------------------------------------------------------
    def _apply_theme_choice(self, theme_key: str, save: bool = True) -> None:
        """
        Применяет тему оформления к приложению.

        Меняется только визуальный слой (QSS и палитра Qt): состав полей,
        данные формы и логика не затрагиваются. По умолчанию выбор сразу
        сохраняется в настройках — это ручной выбор пользователя, который
        приоритетнее системной темы Windows.
        """
        key = theme.normalize(theme_key)
        theme.apply_theme(QApplication.instance(), key)
        self._refresh_nav_icons()

        if save:
            self.settings_service.update({"ui_theme": key})

        self._log_ui_action("смена темы оформления", theme=key)
        self.statusBar().showMessage(
            f"Тема оформления: {theme.theme_label(key)}", 5000
        )

    # --------------------------------------------------------
    # СЛЕДОВАНИЕ ЗА ТЕМОЙ WINDOWS
    # --------------------------------------------------------
    def _sync_system_theme_watcher(self) -> None:
        """
        Включает или выключает слежение за темой Windows по настройке.

        Слежение работает, пока пользователь не выбрал тему сам (флажок
        «Следовать за темой Windows» в настройках): ручной выбор
        приоритетнее системного режима.
        """
        watcher = getattr(self, "system_theme_watcher", None)
        if watcher is None:
            return

        follow = self.settings_service.get_bool("ui_theme_follow_system", True)
        if follow and not watcher.is_running():
            watcher.start()
        elif not follow and watcher.is_running():
            watcher.stop()

    def _on_system_theme_changed(self, theme_key: str) -> None:
        """
        Windows сменил режим — переключаем оформление.

        В настройки тема не записывается: это не выбор пользователя, а
        следование за системой. При выключенном следовании ничего не делаем.
        """
        if not self.settings_service.get_bool("ui_theme_follow_system", True):
            return

        self._apply_theme_choice(theme_key, save=False)
        self._log_ui_action("тема Windows изменилась", theme=theme_key)
        self.statusBar().showMessage(
            f"Тема Windows: {theme.theme_label(theme_key)}", 5000
        )

    def _refresh_nav_icons(self) -> None:
        """
        Пересобирает иконки под активную тему.

        У тёмных тем свой набор значков (resources/icons/**/dark/), поэтому
        после смены оформления их нужно заменить, иначе светлые иконки
        останутся на тёмном фоне.
        """
        side_nav = getattr(self, "side_nav", None)
        if side_nav is not None:
            for index, name in enumerate(self._TAB_ICON_NAMES):
                side_nav.set_item_icon(index, _tab_icon(name))

        for button, name in (
            (getattr(self, "btn_create_contract", None), "contract.svg"),
            (getattr(self, "btn_recognize", None), "recognize.svg"),
            (getattr(self, "btn_save_db", None), "save.svg"),
            (getattr(self, "btn_open_db", None), "database.svg"),
            (getattr(self, "btn_settings", None), "settings.svg"),
            (getattr(self, "btn_clear", None), "clear.svg"),
        ):
            if button is not None:
                button.setIcon(_action_icon(name))

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 16, 20, 8)
        main_layout.setSpacing(12)

        # ── Шапка окна: название слева, финальное действие справа ──
        header_frame = QFrame()
        header_frame.setObjectName("appHeader")
        header = QHBoxLayout(header_frame)
        header.setContentsMargins(16, 10, 16, 10)
        header.setSpacing(16)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        title = QLabel("Договоры перевозки")
        title.setObjectName("appHeading")
        subtitle = QLabel("Подготовка данных и оформление документов")
        subtitle.setObjectName("appSubheading")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()

        self.btn_create_contract = theme.accent_button(
            "Создать договор",
            tooltip="Проверить данные и сформировать договор DOCX",
        )
        self.btn_create_contract.setIcon(_action_icon("contract.svg"))
        self.btn_create_contract.setIconSize(QSize(18, 18))
        self.btn_create_contract.clicked.connect(self._on_create_contract)
        header.addWidget(self.btn_create_contract)
        main_layout.addWidget(header_frame)

        action_frame = QFrame()
        action_frame.setObjectName("actionBar")
        top_bar = QHBoxLayout(action_frame)
        top_bar.setContentsMargins(12, 8, 12, 8)
        top_bar.setSpacing(8)

        # Вспомогательные действия — приглушённые кнопки с рамкой.
        # Главная кнопка живёт на вкладке («Распознать вкладку»), а финальное
        # действие — «Создать договор» (accent): так видно, что за чем.
        self.btn_recognize = theme.secondary_button(
            "Распознать данные",
            tooltip="Вставить текст целиком и распознать все данные",
        )
        self.btn_recognize.setIcon(_action_icon("recognize.svg"))
        self.btn_recognize.setIconSize(QSize(18, 18))
        self.btn_recognize.clicked.connect(self._on_recognize_clicked)
        top_bar.addWidget(self.btn_recognize)

        self.btn_import_documents = theme.secondary_button("Загрузить документы")
        self.btn_import_documents.clicked.connect(self._on_import_documents)
        top_bar.addWidget(self.btn_import_documents)

        self.btn_cancel = theme.secondary_button("Отменить распознавание")
        self.btn_cancel.clicked.connect(self._cancel_recognition)
        self.btn_cancel.setVisible(False)
        top_bar.addWidget(self.btn_cancel)

        self.btn_save_db = theme.secondary_button(
            "Сохранить в базу",
            tooltip="Сохранить заполненные данные в справочник",
        )
        self.btn_save_db.setIcon(_action_icon("save.svg"))
        self.btn_save_db.setIconSize(QSize(18, 18))
        self.btn_save_db.clicked.connect(self._on_save_to_db)
        top_bar.addWidget(self.btn_save_db)

        self.btn_open_db = theme.secondary_button("База данных")
        self.btn_open_db.setIcon(_action_icon("database.svg"))
        self.btn_open_db.setIconSize(QSize(18, 18))
        self.btn_open_db.clicked.connect(self._on_open_db_manager)
        top_bar.addWidget(self.btn_open_db)

        self.btn_settings = theme.secondary_button("Настройки")
        self.btn_settings.setIcon(_action_icon("settings.svg"))
        self.btn_settings.setIconSize(QSize(18, 18))
        self.btn_settings.clicked.connect(self._on_open_settings)
        top_bar.addWidget(self.btn_settings)

        self.btn_clear = theme.secondary_button("Очистить форму")
        self.btn_clear.setIcon(_action_icon("clear.svg"))
        self.btn_clear.setIconSize(QSize(18, 18))
        self.btn_clear.clicked.connect(self._on_clear_form)
        top_bar.addWidget(self.btn_clear)

        top_bar.addStretch()

        main_layout.addWidget(action_frame)

        self.tabs = QTabWidget()
        self.tabs.setIconSize(QSize(24, 24))

        self.driver_tab = DriverTab()
        self.customer_tab = CustomerTab()
        self.carrier_tab = CarrierTab()
        self.vehicles_tab = VehiclesTab()
        self.trailer_tab = TrailerTab()
        self.contract_tab = ContractTab()

        self.driver_tab.recognize_requested.connect(self._on_tab_recognize_requested)
        self.customer_tab.recognize_requested.connect(self._on_tab_recognize_requested)
        self.carrier_tab.recognize_requested.connect(self._on_tab_recognize_requested)
        self.vehicles_tab.recognize_requested.connect(self._on_tab_recognize_requested)
        self.trailer_tab.recognize_requested.connect(self._on_tab_recognize_requested)
        self.contract_tab.recognize_requested.connect(self._on_tab_recognize_requested)

        self.contract_tab.loadings_changed.connect(self._sync_loadings_to_vehicles)
        self.contract_tab.unloadings_changed.connect(self._sync_unloadings_to_vehicles)

        self.tabs.addTab(self.carrier_tab, _tab_icon("carrier.svg"), "Перевозчик")
        self.tabs.addTab(self.driver_tab, _tab_icon("driver.svg"), "Водитель")
        self.tabs.addTab(self.trailer_tab, _tab_icon("trailer.svg"), "Тягач и полуприцеп")
        self.tabs.addTab(self.vehicles_tab, _tab_icon("vehicles.svg"), "Перевозимые авто")
        self.tabs.addTab(self.contract_tab, _tab_icon("contract.svg"), "Договор")
        self.tabs.addTab(self.customer_tab, _tab_icon("customer.svg"), "Заказчик")

        # Углублённое логирование: переключения вкладок пользователем
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # ── Навигация: сайдбар слева (как в макетах) ──
        # Верхние ярлыки вкладок скрыты, но сам QTabWidget остаётся: страницы,
        # их порядок, сигналы и программные переходы (setCurrentWidget) не
        # меняются. Сайдбар — только переключатель этих же страниц.
        self.tabs.tabBar().setVisible(False)

        self.side_nav = SideNav("Разделы")
        for index in range(self.tabs.count()):
            self.side_nav.add_item(
                self.tabs.tabText(index),
                self.tabs.tabIcon(index),
                tooltip=self.tabs.tabToolTip(index) or self.tabs.tabText(index),
            )
        self.side_nav.navigate.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self._sync_side_nav)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self.side_nav)
        body.addWidget(self.tabs, 1)
        main_layout.addLayout(body)

        self._sync_side_nav(self.tabs.currentIndex())

        self._sync_loadings_to_vehicles()
        self._sync_unloadings_to_vehicles()

        self.statusBar().showMessage("Готово")

        # ── Статус-бар: где я, сколько заполнено, когда сохранял ──
        self._last_save_at = None
        self._status_tab_label = QLabel("")
        self._status_fields_label = QLabel("")
        self._status_saved_label = QLabel("Сохранений ещё не было")
        for widget in (
            self._status_tab_label,
            self._status_fields_label,
            self._status_saved_label,
        ):
            self.statusBar().addPermanentWidget(widget)

        # Счётчик полей обновляем по таймеру: сигналы от каждого поля
        # заставили бы дёргать get_data() на каждое нажатие клавиши.
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_status_indicators)
        self._status_timer.start()
        self._refresh_status_indicators()

        logger.debug(
            f"UI построен: вкладок={self.tabs.count()} "
            f"({[self.tabs.tabText(i) for i in range(self.tabs.count())]})"
        )

        # ── Индикатор прогресса распознавания (Шаг 8 оптимизации) ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress_bar)

    def _log_ui_action(self, action: str, **details):
        """
        Единая точка логирования действий пользователя (INFO).

        Пишем только служебные сведения: имена полей, количества, индексы
        вкладок. Значения данных (ФИО, адреса, номера) сюда не попадают —
        для них есть маскирование в core/trace.py.
        """
        if details:
            tail = " | " + " | ".join(f"{k}={v}" for k, v in details.items())
        else:
            tail = ""
        logger.info("UI: %s%s", action, tail)

    def _on_tab_changed(self, index: int):
        try:
            title = self.tabs.tabText(index)
        except Exception:  # вкладка уже удалена — не роняем UI из-за лога
            title = f"#{index}"
        self._log_ui_action("переключение вкладки", index=index, tab=title)
        self._refresh_status_indicators()

    def _sync_side_nav(self, index: int) -> None:
        """
        Сайдбар показывает текущую страницу.

        Смена вкладки может быть программной (загрузка записи из справочника
        вызывает setCurrentWidget), поэтому подсветку пункта синхронизируем по
        сигналу QTabWidget, а не только по клику в сайдбаре.
        """
        side_nav = getattr(self, "side_nav", None)
        if side_nav is not None:
            side_nav.set_current_index(index)

    # --------------------------------------------------------
    # СТАТУС-БАР: активная вкладка, заполненность, время сохранения
    # --------------------------------------------------------
    def _refresh_status_indicators(self) -> None:
        """Обновляет постоянные зоны статус-бара (только чтение данных)."""
        try:
            index = self.tabs.currentIndex()
            title = self.tabs.tabText(index) if index >= 0 else "—"
            self._status_tab_label.setText(f"Вкладка: {title}")
            self._refresh_status_fields()
        except Exception as e:  # noqa: BLE001 — индикатор не должен ломать UI
            logger.debug(f"Статус-бар: обновление не удалось: {e}")

    def _refresh_status_fields(self) -> None:
        """«Заполнено N из M» для текущей вкладки (TabMixin.count_filled_fields)."""
        widget = self.tabs.currentWidget()
        counter = getattr(widget, "count_filled_fields", None)
        if not callable(counter):
            self._status_fields_label.setText("")
            return

        try:
            filled, total = counter()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Статус-бар: подсчёт полей не удался: {e}")
            return

        self._status_fields_label.setText(f"Заполнено {filled} из {total}")

    def _mark_saved(self) -> None:
        """Отмечает в статус-баре время последнего сохранения в базу."""
        self._last_save_at = datetime.now()
        self._status_saved_label.setText(
            f"Сохранено: {self._last_save_at.strftime('%H:%M')}"
        )
        self._log_ui_action(
            "статус-бар: сохранение",
            time=self._last_save_at.strftime("%H:%M:%S"),
        )

    def _sync_loadings_to_vehicles(self):
        try:
            loadings = self.contract_tab.get_loadings()
            self.vehicles_tab.update_loadings_list(loadings)
        except Exception as e:
            logger.exception(f"Ошибка синхронизации погрузок: {e}")

    def _sync_unloadings_to_vehicles(self):
        try:
            unloadings = self.contract_tab.get_unloadings()
            self.vehicles_tab.update_unloadings_list(unloadings)
        except Exception as e:
            logger.exception(f"Ошибка синхронизации выгрузок: {e}")

    def _on_import_documents(self):
        from ui.document_import_dialog import DocumentImportDialog
        logger.info("Открыт диалог загрузки документов")
        result = DocumentImportDialog(self).exec_()
        logger.info("Диалог загрузки документов закрыт: результат=%s", result)

    def _on_recognize_clicked(self):
        self._log_ui_action("нажата кнопка «Распознать данные»")
        if self.gigachat is None:
            self._log_ui_action("распознавание невозможно: GigaChat не инициализирован")
            QMessageBox.critical(
                self, "Ошибка",
                "GigaChat не инициализирован.\n\n"
                "Проверьте ключ в разделе «⚙ Настройки»."
            )
            return

        dialog = BulkPasteDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            self._log_ui_action("диалог массовой вставки закрыт без распознавания")
            return

        text = dialog.get_text().strip()
        if not text:
            self._log_ui_action("распознавание отменено: пустой текст")
            QMessageBox.warning(self, "Внимание", "Текст пуст.")
            return

        self._log_ui_action("текст принят из диалога", length=len(text))
        self._start_recognition(text)

    def _on_tab_recognize_requested(self, text: str):
        self._log_ui_action(
            "запрос распознавания со вкладки",
            tab=self.tabs.tabText(self.tabs.currentIndex()),
            length=len(text) if text else 0,
        )
        logger.info(
            f"Получен запрос на распознавание со вкладки: "
            f"{len(text) if text else 0} символов"
        )

        if self.gigachat is None:
            QMessageBox.critical(
                self, "Ошибка",
                "GigaChat не инициализирован.\n\n"
                "Проверьте ключ в разделе «⚙ Настройки»."
            )
            return

        if not text or not text.strip():
            QMessageBox.warning(
                self, "Внимание",
                "На вкладке нет текста для распознавания.\n\n"
                "Вставьте данные в поле «Вставить из буфера» и нажмите "
                "«Распознать вкладку»."
            )
            return

        self._start_recognition(text)

    def _start_recognition(self, text: str):
        """Запускает распознавание в пуле потоков (Шаг 8 оптимизации)."""
        self._log_ui_action(
            "старт распознавания",
            length=len(text),
            provider=self.settings_service.get("provider"),
        )
        self.btn_recognize.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.statusBar().showMessage(
            f"Отправка в GigaChat ({len(text)} символов)..."
        )

        task = RecognitionTask(self.gigachat, text)
        self.recognition_task = task

        # Сигналы привязываем к конкретной задаче: результат «старой»
        # (отменённой) задачи не должен влиять на новую.
        task.signals.finished.connect(
            lambda data, t=task: self._on_recognition_finished(data, t)
        )
        task.signals.error.connect(
            lambda message, t=task: self._on_recognition_error(message, t)
        )
        task.signals.cancelled.connect(
            lambda t=task: self._on_recognition_cancelled(t)
        )
        task.signals.progress.connect(
            lambda percent, message, t=task: self._on_recognition_progress(percent, message, t)
        )

        self.thread_pool.start(task)
        logger.info(
            f"Задача распознавания запущена в пуле "
            f"(активных потоков: {self.thread_pool.activeThreadCount()})"
        )

    # --------------------------------------------------------
    # ОТМЕНА И ПРОГРЕСС  ← Шаг 8 оптимизации
    # --------------------------------------------------------
    def _is_current_task(self, task) -> bool:
        return task is None or task is self.recognition_task

    def _cancel_recognition(self):
        """
        Отменяет текущее распознавание.

        HTTP-запрос прервать нельзя, поэтому задача помечается отменённой
        (её результат не будет применён), а интерфейс освобождается сразу —
        пользователю не нужно ждать ответа сервера, чтобы начать заново.
        """
        task = self.recognition_task

        self._log_ui_action("нажата кнопка «Отменить распознавание»")

        if task is None or task.is_cancelled:
            logger.info("Отмена: активной задачи распознавания нет")
            return

        task.cancel()
        self.recognition_task = None
        self.btn_recognize.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.progress_bar.setVisible(False)
        self.statusBar().showMessage("Распознавание отменено", 5000)
        logger.info("Распознавание отменено пользователем")

    def _on_recognition_progress(self, percent: int, message: str, task=None):
        if not self._is_current_task(task):
            return
        self.progress_bar.setValue(max(0, min(100, int(percent))))
        logger.info("Распознавание: прогресс=%s%%", percent)
        if message:
            self.statusBar().showMessage(message)

    def _on_recognition_cancelled(self, task=None):
        logger.info("Задача распознавания завершилась с отменой")
        if not self._is_current_task(task):
            return
        self._finish_recognition_ui(task)

    def _finish_recognition_ui(self, task=None):
        """Возвращает интерфейс в исходное состояние после распознавания."""
        if not self._is_current_task(task):
            return

        self.recognition_task = None
        self.btn_recognize.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)

    def _log_recognition_result(self, data: Dict[str, Any]):
        """
        Подробно пишет в debug.log: что распознали, а что нет.

        Логируются только имена полей и количества записей — значения
        (ФИО, адреса, паспорт) в лог не попадают.
        """
        recognized, missing = [], []

        for key, title in self._RECOGNITION_SECTIONS:
            value = data.get(key)

            if key == "vehicles":
                count = len(value) if isinstance(value, list) else 0
                if count:
                    recognized.append(f"{title}: {count} записей")
                else:
                    missing.append(title)
                continue

            if isinstance(value, dict) and value:
                recognized.append(f"{title}: {filled_fields_summary(value)}")
            else:
                missing.append(title)

        logger.debug(
            "Распознавание: разделы получены — "
            + ("; ".join(recognized) if recognized else "нет")
        )
        logger.debug(
            "Распознавание: НЕ распознано — "
            + (", ".join(missing) if missing else "нет")
        )
        logger.debug(f"Распознавание: все ключи ответа: {sorted(data.keys())}")

    def _on_recognition_finished(self, data: Dict[str, Any], task=None):
        if not self._is_current_task(task):
            logger.info("Результат устаревшей задачи распознавания проигнорирован")
            return

        logger.info(f"Распознавание завершено: {list(data.keys())}")
        self._log_recognition_result(data)
        try:
            # Распознавание накладывается на форму, а не заменяет её целиком:
            # пустые поля ответа не должны стирать то, что пользователь ввёл
            # руками (модель по промпту отдаёт блок «customer» пустым).
            driver_data = filled_only(data.get("driver"))
            if driver_data:
                self.driver_tab.fill_data(driver_data)
                logger.info("Данные водителя заполнены")
                logger.debug(
                    f"Вкладка «Водитель»: {filled_fields_summary(driver_data)}"
                )
            else:
                logger.info(
                    "Вкладка «Водитель»: в ответе нет данных — оставляем как есть"
                )

            customer_data = filled_only(data.get("customer"))
            if customer_data:
                self.customer_tab.fill_data(customer_data)
                logger.info("Данные заказчика заполнены")
                logger.debug(
                    f"Вкладка «Заказчик»: {filled_fields_summary(customer_data)}"
                )
            else:
                logger.info(
                    "Вкладка «Заказчик»: в ответе нет данных — "
                    "введённое вручную сохранено"
                )

            carrier_data = filled_only(data.get("carrier"))
            if carrier_data:
                self.carrier_tab.fill_data(carrier_data)
                logger.info("Данные перевозчика заполнены")
                logger.debug(
                    f"Вкладка «Перевозчик»: {filled_fields_summary(carrier_data)}"
                )
            else:
                logger.info(
                    "Вкладка «Перевозчик»: в ответе нет данных — оставляем как есть"
                )

            vehicles_data = filled_only_list(data.get("vehicles"))
            if vehicles_data:
                self.vehicles_tab.fill_data(vehicles_data)
                logger.info(f"Таблица ТС заполнена: {len(vehicles_data)} записей")
                for i, vehicle in enumerate(vehicles_data, start=1):
                    logger.debug(
                        f"ТС #{i}: {filled_fields_summary(vehicle)}"
                    )
            else:
                logger.info(
                    "Таблица ТС: в ответе нет заполненных строк — оставляем как есть"
                )

            tractor_data = filled_only(data.get("tractor"))
            trailer_data = filled_only(data.get("trailer"))
            if tractor_data or trailer_data:
                self.trailer_tab.fill_data(tractor_data, trailer_data)
                logger.info("Данные тягача и полуприцепа заполнены")
                logger.debug(
                    f"Тягач: {filled_fields_summary(tractor_data)} | "
                    f"полуприцеп: {filled_fields_summary(trailer_data)}"
                )
            else:
                logger.info(
                    "Вкладка «Тягач и полуприцеп»: в ответе нет данных — "
                    "оставляем как есть"
                )

            contract_data = filled_only(data.get("contract"))
            if contract_data:
                self.contract_tab.fill_data(contract_data)
                logger.info("Данные договора заполнены")
                logger.debug(
                    f"Вкладка «Договор»: {filled_fields_summary(contract_data)}"
                )
            else:
                logger.info(
                    "Вкладка «Договор»: в ответе нет данных — оставляем как есть"
                )

            self._sync_loadings_to_vehicles()
            self._sync_unloadings_to_vehicles()

            self.statusBar().showMessage("Распознавание завершено", 5000)
            QMessageBox.information(
                self, "Успех", "Данные успешно распознаны и заполнены!"
            )
        except Exception as e:
            logger.exception("Ошибка при заполнении вкладок")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось заполнить вкладки:\n{e}"
            )
        finally:
            self._finish_recognition_ui(task)

    def _on_recognition_error(self, error_msg: str, task=None):
        if not self._is_current_task(task):
            logger.info("Ошибка устаревшей задачи распознавания проигнорирована")
            return

        logger.error(f"Ошибка распознавания: {error_msg}")
        self.statusBar().showMessage("Ошибка распознавания", 5000)
        self._finish_recognition_ui(task)
        QMessageBox.critical(
            self, "Ошибка распознавания",
            f"Не удалось распознать данные:\n\n{error_msg}"
        )

    # --------------------------------------------------------
    # ЕДИНЫЙ СБОР ДАННЫХ  ← Шаг 1 рефакторинга архитектуры
    # --------------------------------------------------------
    def _collect_data(self) -> ContractData:
        """
        Собирает данные со всех вкладок ОДИН раз и возвращает ContractData.

        Раньше данные собирались дважды и по-разному: в _on_create_contract
        (для DOCX) и в _on_save_to_db (для БД). Наборы ключей расходились, из-за
        чего в договор не попадали тягач, полуприцеп и город. Теперь оба
        потребителя получают один и тот же объект.

        ВАЖНО: метод не меняет состояние UI (никаких setText/clear), поэтому
        его можно вызывать многократно.
        """
        contract = self.contract_tab.get_data()

        data = ContractData(
            driver=self.driver_tab.get_data(),
            carrier=self.carrier_tab.get_data(),
            customer=self.customer_tab.get_data(),
            vehicles=self.vehicles_tab.get_data(),
            tractor=self.trailer_tab.get_tractor_data(),
            trailer=self.trailer_tab.get_trailer_data(),
            contract=contract,
            loadings=contract.get("loadings") or [],
            unloadings=contract.get("unloadings") or [],
            # city появится здесь автоматически, если вкладка «Договор» начнёт
            # его отдавать; сейчас город вычисляется в ContractData.resolved_city()
            city=str(contract.get("city", "") or ""),
        )

        logger.info(f"Данные собраны: {data.summary()}")
        return data

    # --------------------------------------------------------
    # ПРОВЕРКА ДАННЫХ ПЕРЕД ГЕНЕРАЦИЕЙ  ← Шаг 2 рефакторинга
    # --------------------------------------------------------
    def _confirm_validation(self, report: ValidationReport) -> bool:
        """
        Показывает результат проверки данных перед генерацией договора.

        Возвращает True, если можно продолжать:
          * замечаний нет — сразу True (диалогов не будет, как раньше);
          * есть ошибки или замечания — один диалог со списком и вопросом.
            Кнопка по умолчанию — «Исправить», но возможность напечатать
            документ сохраняется (поведение не заблокировано жёстко).
        """
        if report.is_clean:
            logger.info("Проверка данных пройдена без замечаний")
            return True

        logger.warning(
            f"Проверка данных: ошибок={len(report.errors)}, "
            f"замечаний={len(report.warnings)}"
        )

        box = QMessageBox(self)
        box.setWindowTitle("Проверьте данные" if report.has_errors else "Замечания к данным")
        box.setIcon(QMessageBox.Critical if report.has_errors else QMessageBox.Warning)
        box.setText(
            "В данных есть ошибки. Договор можно создать, но проверьте реквизиты."
            if report.has_errors
            else "В данных есть замечания. Поля, отмеченные ниже, останутся пустыми."
        )
        box.setInformativeText(report.format_text())
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)

        yes_button = box.button(QMessageBox.Yes)
        no_button = box.button(QMessageBox.No)
        if yes_button is not None:
            yes_button.setText("Создать договор")
        if no_button is not None:
            no_button.setText("Исправить")

        return box.exec_() == QMessageBox.Yes

    def _on_create_contract(self):
        logger.info("Нажата кнопка «Создать договор»")
        self._log_ui_action("нажата кнопка «Создать договор»")
        try:
            logger.info("Создание договора: сбор данных")
            data = self._collect_data()

            logger.info("Создание договора: проверка данных")
            report = Validator().check(data)
            if not self._confirm_validation(report):
                logger.info("Генерация договора отменена пользователем после проверки")
                audit.log_denied("contract_created", "проверка данных не пройдена")
                self.statusBar().showMessage("Договор не создан — исправьте данные", 5000)
                return

            logger.info("Создание договора: генерация файла")
            path = self.contract_generator.generate(data)
            logger.info(f"Договор создан: {path}")
            audit.log_event(
                "contract_created",
                contract_number=data.contract.get("number", ""),
                file=os.path.basename(path),
                mode=data.contract.get("carrier_type", ""),
            )
            self._show_contract_created(path)
        except Exception as e:
            logger.exception("Ошибка при создании договора")
            audit.log_event("contract_creation_failed", result="error")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось создать договор:\n{e}"
            )

    def _show_contract_created(self, path: str):
        """
        Сообщает о созданном договоре и предлагает открыть папку (Шаг 6).

        Вместо простого «ОК» добавлена кнопка «Открыть папку» — файлы лежат
        в output/, и до них больше не нужно добираться вручную.
        """
        folder = os.path.dirname(os.path.abspath(path))

        box = QMessageBox(self)
        box.setWindowTitle("Успех")
        box.setIcon(QMessageBox.Information)
        box.setText("Договор создан")
        box.setInformativeText(f"{os.path.basename(path)}\n\nПапка: {folder}")

        open_button = box.addButton("Открыть папку", QMessageBox.ActionRole)
        box.addButton("OK", QMessageBox.AcceptRole)

        box.exec_()

        if box.clickedButton() is open_button:
            self._log_ui_action("диалог создания договора: «Открыть папку»")
            self._open_folder(folder)
        else:
            self._log_ui_action("диалог создания договора закрыт")

    def _open_folder(self, folder: str) -> bool:
        """Открывает папку в системном файловом менеджере."""
        if not folder or not os.path.isdir(folder):
            logger.warning(f"Папка для открытия не найдена: {folder!r}")
            return False

        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtGui import QDesktopServices

            if QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
                logger.info(f"Открыта папка: {folder}")
                return True
            logger.warning(f"QDesktopServices не смог открыть папку: {folder}")
        except Exception as e:
            logger.warning(f"Не удалось открыть папку через QDesktopServices: {e}")

        # Резерв для Windows, если Qt не справился
        try:
            os.startfile(folder)  # type: ignore[attr-defined]
            logger.info(f"Открыта папка (os.startfile): {folder}")
            return True
        except Exception as e:
            logger.error(f"Не удалось открыть папку {folder}: {e}")
            return False

    # --------------------------------------------------------
    # СОХРАНЕНИЕ В БАЗУ  ← ГЛАВНОЕ ЗДЕСЬ
    # --------------------------------------------------------
    def _on_save_to_db(self):
        logger.info("Нажата кнопка «Сохранить в базу»")
        self._log_ui_action("нажата кнопка «Сохранить в базу»")
        try:
            logger.info("Сохранение в базу: сбор данных")
            data = self._collect_data()

            driver = data.driver
            customer = data.customer
            carrier = data.carrier
            tractor = data.tractor
            trailer = data.trailer

            # Углублённое логирование: по каждому разделу видно, сколько полей
            # заполнено и какие остались пустыми (только имена полей).
            logger.debug(
                f"Сохранение в БД: водитель — {filled_fields_summary(driver)}"
            )
            logger.debug(
                f"Сохранение в БД: заказчик — {filled_fields_summary(customer)}"
            )
            logger.debug(
                f"Сохранение в БД: перевозчик — {filled_fields_summary(carrier)}"
            )
            logger.debug(
                f"Сохранение в БД: тягач — {filled_fields_summary(tractor)} | "
                f"полуприцеп — {filled_fields_summary(trailer)}"
            )
            logger.debug(
                f"Сохранение в БД: перевозимых ТС — "
                f"{len(data.vehicles) if isinstance(data.vehicles, list) else 0} | "
                f"погрузок — {len(data.loadings)} | "
                f"выгрузок — {len(data.unloadings)}"
            )

            # ── Сохраняем водителя + тягач/прицеп ──
            # Вариант В: ID сохранённых записей сразу попадают в договор
            # (contracts.driver_id / customer_id / carrier_id). Благодаря
            # мягкому удалению ссылка не превратится в «ничто»: даже удалённый
            # из справочника водитель остаётся связанным с историей договоров.
            driver_id = customer_id = carrier_id = None

            if driver.get("full_name"):
                driver_id = save_driver(driver)
                logger.info(f"Водитель сохранён: ID={driver_id}")
                audit.log_event("driver_saved", driver_id=driver_id)

                # ── СОХРАНЯЕМ ТЯГАЧ/ПРИЦЕП ──
                tractor_plate = (tractor.get("plate_number") or "").strip()
                trailer_plate = (trailer.get("plate_number") or "").strip()
                tractor_brand = (tractor.get("brand_model") or "").strip()
                trailer_brand = (trailer.get("brand_model") or "").strip()

                if tractor_plate or trailer_plate or tractor_brand or trailer_brand:
                    save_driver_vehicle(driver_id, {
                        "tractor_brand": tractor_brand,
                        "tractor_plate": tractor_plate,
                        "tractor_color": (tractor.get("color") or "").strip(),
                        "tractor_year": str(tractor.get("year", "") or ""),
                        "trailer_brand": trailer_brand,
                        "trailer_plate": trailer_plate,
                        "trailer_color": (trailer.get("color") or "").strip(),
                        "trailer_year": str(trailer.get("year", "") or ""),
                    })
                    logger.info(
                        f"Тягач/прицеп сохранены для водителя ID={driver_id}"
                    )
                else:
                    logger.info(
                        "Тягач/прицеп НЕ сохранены — пустые поля "
                        "(марка/номер не заполнены)"
                    )
            else:
                logger.info("Водитель не сохранён — ФИО пустое")

            # ── Заказчик ──
            if customer.get("full_name") or customer.get("short_name"):
                customer_id = save_organization(customer, is_carrier=False)
                logger.info(f"Заказчик сохранён: ID={customer_id}")
            else:
                logger.info("Заказчик не сохранён — наименование пустое")

            # ── Перевозчик ──
            if carrier.get("full_name") or carrier.get("short_name"):
                carrier_id = save_organization(carrier, is_carrier=True)
                logger.info(f"Перевозчик сохранён: ID={carrier_id}")
            else:
                logger.info("Перевозчик не сохранён — наименование пустое")

            # ── Ссылки договора на справочник (вариант В) ──
            data.bind_reference_ids(
                driver_id=driver_id,
                customer_id=customer_id,
                carrier_id=carrier_id,
            )

            # ── Перевозимые ТС + тягач/прицеп (для общей таблицы) ──
            all_vehicles = data.to_vehicle_rows()

            # ── Договор ──
            contract = data.to_db_dict()
            contract_id = save_contract_with_details(
                contract, data.loadings, data.unloadings, all_vehicles
            )
            if all_vehicles:
                logger.info(f"Сохранено ТС: {len(all_vehicles)}")
            logger.info(
                f"Договор сохранён: ID={contract_id}, №={contract.get('number')}"
            )
            logger.debug(
                f"Сохранение в БД: договор — {filled_fields_summary(contract)} | "
                f"ID={contract_id} | строк ТС={len(all_vehicles)} | "
                f"ссылки: driver_id={data.driver_id}, "
                f"customer_id={data.customer_id}, carrier_id={data.carrier_id}"
            )
            audit.log_event(
                "saved_to_db",
                contract_id=contract_id,
                contract_number=contract.get("number", ""),
                entities="driver,customer,carrier,vehicles,contract",
                count=len(all_vehicles),
            )

            self._mark_saved()
            self._refresh_status_indicators()
            QMessageBox.information(self, "Успех", "Данные сохранены в базу!")
        except Exception as e:
            logger.exception("Ошибка при сохранении в базу")
            audit.log_event("database_save_failed", result="error")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось сохранить данные:\n{e}"
            )

    def _on_open_db_manager(self):
        logger.info("Открытие менеджера базы данных")
        self._log_ui_action("нажата кнопка «База данных»")
        try:
            dialog = DbManagerDialog(
                self,
                on_load_driver=self._load_driver_from_db,
                on_load_customer=self._load_customer_from_db,
                on_load_carrier=self._load_carrier_from_db,
            )
            logger.info(
                f"DbManagerDialog создан: "
                f"on_load_driver={dialog.on_load_driver is not None}, "
                f"on_load_customer={dialog.on_load_customer is not None}, "
                f"on_load_carrier={dialog.on_load_carrier is not None}"
            )
            dialog.exec_()
        except Exception as e:
            logger.exception("Ошибка при открытии менеджера БД")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось открыть базу:\n{e}"
            )

    def _load_driver_from_db(self, driver: Dict[str, Any]):
        logger.info(
            f"Загрузка водителя из БД: ID={driver.get('id')}"
        )
        self._log_ui_action("загрузка водителя из БД", id=driver.get("id"))

        try:
            # Полное соответствие записи из справочника (см. комментарий
            # в _load_customer_from_db про частичное заполнение).
            self.driver_tab.clear()
            self.driver_tab.fill_data(driver)
            self.trailer_tab.clear()

            driver_id = driver.get("id")
            if driver_id:
                try:
                    vehicle = load_driver_vehicle(driver_id)
                    if vehicle:
                        tractor = {
                            "brand_model": vehicle.get("tractor_brand", "") or "",
                            "plate_number": vehicle.get("tractor_plate", "") or "",
                            "color": vehicle.get("tractor_color", "") or "",
                            "year": vehicle.get("tractor_year", 0) or 0,
                        }
                        trailer = {
                            "brand_model": vehicle.get("trailer_brand", "") or "",
                            "plate_number": vehicle.get("trailer_plate", "") or "",
                            "color": vehicle.get("trailer_color", "") or "",
                            "year": vehicle.get("trailer_year", 0) or 0,
                        }
                        self.trailer_tab.fill_data(tractor, trailer)
                        logger.info(
                            f"Тягач/прицеп подгружены для водителя ID={driver_id}"
                        )
                    else:
                        logger.info(
                            f"Для водителя ID={driver_id} нет данных о ТС"
                        )
                except Exception as e:
                    logger.exception(
                        f"Не удалось загрузить ТС водителя ID={driver_id}: {e}"
                    )

            self.tabs.setCurrentWidget(self.driver_tab)

            self.statusBar().showMessage(
                f"Загружен водитель: {driver.get('full_name', '')}", 5000
            )
        except Exception as e:
            logger.exception("Ошибка загрузки водителя из БД")
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось загрузить водителя:\n{e}"
            )

    def _load_customer_from_db(self, customer: Dict[str, Any]):
        logger.info(
            f"Загрузка заказчика из БД: ID={customer.get('id')}"
        )
        self._log_ui_action("загрузка заказчика из БД", id=customer.get("id"))
        try:
            # Форма должна соответствовать записи из справочника, поэтому
            # сначала очищаем вкладку: fill_data обновляет только те поля,
            # которые пришли (это защищает ручной ввод при распознавании).
            self.customer_tab.clear()
            self.customer_tab.fill_data(customer)
            self.tabs.setCurrentWidget(self.customer_tab)
            self.statusBar().showMessage(
                f"Загружен заказчик: {customer.get('full_name', '')}", 5000
            )
        except Exception as e:
            logger.exception("Ошибка загрузки заказчика из БД")
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось загрузить заказчика:\n{e}"
            )

    def _load_carrier_from_db(self, carrier: Dict[str, Any]):
        logger.info(
            f"Загрузка перевозчика из БД: ID={carrier.get('id')}"
        )
        self._log_ui_action("загрузка перевозчика из БД", id=carrier.get("id"))
        try:
            self.carrier_tab.clear()
            self.carrier_tab.fill_data(carrier)
            self.tabs.setCurrentWidget(self.carrier_tab)
            self.statusBar().showMessage(
                f"Загружен перевозчик: {carrier.get('full_name', '')}", 5000
            )
        except Exception as e:
            logger.exception("Ошибка загрузки перевозчика из БД")
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось загрузить перевозчика:\n{e}"
            )

    def _on_clear_form(self):
        logger.info("Очистка формы")
        self._log_ui_action("нажата кнопка «Очистить форму»")
        self.driver_tab.clear()
        self.customer_tab.clear()
        self.carrier_tab.clear()
        self.vehicles_tab.clear()
        self.trailer_tab.clear()
        self.contract_tab.clear()

        self._sync_loadings_to_vehicles()
        self._sync_unloadings_to_vehicles()

        self.statusBar().showMessage("Форма очищена", 3000)


class BulkPasteDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Заполнение данных водителя и ТС")
        self.resize(900, 700)

        layout = QVBoxLayout(self)

        header = QLabel(
            "Вставьте данные водителя и транспортного средства\n"
            "GigaChat автоматически распознает и заполнит все поля"
        )
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 14px; font-weight: bold; padding: 10px;")
        layout.addWidget(header)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Вставьте сюда данные...")
        self.text_edit.setStyleSheet("font-size: 12px;")
        layout.addWidget(self.text_edit)

        btn_paste = theme.clipboard_button()
        btn_paste.clicked.connect(self._paste_from_clipboard)
        layout.addWidget(btn_paste)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _paste_from_clipboard(self):
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        logger.debug(
            f"UI: вставка из буфера в диалоге распознавания | "
            f"символов: {len(text) if text else 0}"
        )
        self.text_edit.insertPlainText(text)

    def get_text(self) -> str:
        return self.text_edit.toPlainText()


if __name__ == "__main__":
    import sys

    # Центральный конфиг логирования — как в main.py, чтобы отладочный запуск
    # модуля писал в те же logs/app.log, logs/errors.log и (с --debug) logs/debug.log.
    from config.logging_config import setup_logging

    setup_logging(debug="--debug" in sys.argv)

    # Отладочный запуск модуля: БД нужна, а main.py в этом сценарии не участвует
    init_database()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # Тема как при обычном запуске через main.py: системная, если включено
    # следование за Windows, иначе — сохранённый выбор пользователя
    theme.apply_theme(app, system_theme.preferred_theme(get_settings_service()))
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
