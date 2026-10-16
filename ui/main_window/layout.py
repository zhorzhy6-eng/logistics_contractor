# -*- coding: utf-8 -*-
"""
Раскладка окна Экспедиторства и вспомогательные слоты.

Этап 6 (16.10.2026), подэтап 1.K: сюда переехал из прежнего
`ui/main_window.py` метод `_init_ui` (300 строк) и «обвязка» окна —
логирование действий, переключение вкладок, сайдбар, статус-бар, синхронизация
списков точек маршрута с таблицей ТС, очистка формы и открытие диалога
импорта документов. Логика не менялась — только переезд.

`_init_ui` разбит на пять секций, порядок вызовов ЖЁСТКИЙ (менять нельзя):

    _build_header()      → шапка: название, селектор типа, «Создать договор»,
                           «Выход». Создаёт `self.selector`,
                           `self.btn_create_contract`, `self.btn_exit`
    _build_action_bar()  → панель действий: распознать, загрузить документы,
                           отменить, сохранить, база, настройки
    _build_tabs()        → вкладки, их сигналы, сайдбар, первичная
                           синхронизация списков точек маршрута
    _build_status_bar()  → постоянные зоны статус-бара и таймер обновления
    _build_progress()    → индикатор прогресса распознавания

Почему порядок именно такой: `_build_status_bar` сразу вызывает
`_refresh_status_indicators`, а тот читает `self.tabs.currentWidget()` —
значит, вкладки обязаны существовать раньше. Таймер статус-бара (1 с)
начинает тикать в `_build_status_bar`, поэтому его зоны-метки тоже должны
быть созданы до `start()`.

ОГРАНИЧЕНИЕ: `lambda` и `functools.partial` в `connect` ЗАПРЕЩЕНЫ
(цикл ссылок Python ↔ Qt, падение 0xC0000005). Вкладку для очистки слот
берёт у отправителя сигнала (`sender()`), а не из замыкания.
"""

import logging

from PyQt5.QtCore import QSize, QTimer
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QTabWidget, QVBoxLayout,
    QWidget,
)

from core.contracts.contract_types import DEFAULT_CONTRACT_TYPE

from ui import theme
from ui.controls.contract_type_selector import ContractTypeSelector
from ui.icons import action_icon as _action_icon
from ui.icons import tab_icon as _tab_icon
from ui.navigation import SideNav
from ui.tabs.carrier_tab import CarrierTab
from ui.tabs.contract_tab import ContractTab
from ui.tabs.customer_tab import CustomerTab
from ui.tabs.driver_tab import DriverTab
from ui.tabs.trailer_tab import TrailerTab
from ui.tabs.vehicles_tab import VehiclesTab

# Имя логгера задано ЯВНО (а не __name__): после переезда в пакет
# `ui.main_window.layout` прежние записи выпали бы из настройки
# `config/logging_config.py` (APP_LOGGERS = "core", "ui", "db", ...).
logger = logging.getLogger("ui.main_window")


class LayoutMixin:
    """
    Раскладка окна и вспомогательные слоты.

    ЭТОТ МИКСИН ИДЁТ ПЕРВЫМ В MRO `MainWindow`: он создаёт виджеты
    (`self.tabs`, вкладки, `self.side_nav`, кнопки, статус-бар), на которые
    ссылаются все остальные миксины. Своего `__init__` у него нет — иначе
    MRO-цепочка `__init__` пошла бы не в том порядке; всю инициализацию
    делает `MainWindow.__init__`.
    """

    # --------------------------------------------------------
    # РАСКЛАДКА ОКНА  ← ЭТАП 2B/2C
    # --------------------------------------------------------
    def _init_ui(self):
        """Собирает окно целиком: пять секций в жёстком порядке."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 16, 20, 8)
        main_layout.setSpacing(12)

        self._build_header(main_layout)
        self._build_action_bar(main_layout)
        self._build_tabs(main_layout)
        self._build_status_bar()
        self._build_progress()

    def _build_header(self, parent_layout) -> None:
        """
        Шапка окна: название слева, финальное действие справа.

        Создаёт: `self.selector` (селектор типа договора),
        `self.btn_create_contract`, `self.btn_exit`. Крестик окна только
        прячет его (данные в формах не теряются), поэтому штатный выход —
        кнопка «Выход»: сигнал ловит main.py.
        """
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

        # ── Селектор типа договора (ЭТАП 2C) ──
        # Виджет только сообщает о выборе: переключает окна main.py через
        # WindowManager. Тип окна берётся из ContractType, а не из литерала.
        self.selector = ContractTypeSelector(DEFAULT_CONTRACT_TYPE.value)
        self.selector.contract_type_selected.connect(self.switch_to_type_requested)
        header.addWidget(self.selector)

        self.btn_create_contract = theme.accent_button(
            "Создать договор",
            tooltip="Проверить данные и сформировать договор DOCX",
        )
        self.btn_create_contract.setIcon(_action_icon("contract.svg"))
        self.btn_create_contract.setIconSize(QSize(18, 18))
        self.btn_create_contract.clicked.connect(self._on_create_contract)
        header.addWidget(self.btn_create_contract)

        # ── «Выход» (ЭТАП 2C) ──
        # Крестик окна только прячет его (данные в формах не теряются),
        # поэтому штатный выход из программы — эта кнопка: сигнал ловит
        # main.py, закрывает окна через WindowManager и завершает цикл.
        self.btn_exit = theme.secondary_button(
            "Выход", tooltip="Закрыть программу"
        )
        self.btn_exit.clicked.connect(self.exit_requested)
        header.addWidget(self.btn_exit)
        parent_layout.addWidget(header_frame)

    def _build_action_bar(self, parent_layout) -> None:
        """
        Панель действий окна.

        Создаёт: `self.btn_recognize`, `self.btn_import_documents`,
        `self.btn_cancel`, `self.btn_save_db`, `self.btn_open_db`,
        `self.btn_settings`.

        Вспомогательные действия — приглушённые кнопки с рамкой. Главная
        кнопка живёт на вкладке («Распознать вкладку»), а финальное действие
        — «Создать договор» (accent): так видно, что за чем.
        """
        action_frame = QFrame()
        action_frame.setObjectName("actionBar")
        top_bar = QHBoxLayout(action_frame)
        top_bar.setContentsMargins(12, 8, 12, 8)
        top_bar.setSpacing(8)

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

        # «Очистить форму» из шапки убрана (ЭТАП 2B): теперь у каждой вкладки
        # своя кнопка, и очищается только она (см. _on_clear_tab). Кнопка
        # «Создать договор» осталась в шапке — она действует на всё окно.

        top_bar.addStretch()

        parent_layout.addWidget(action_frame)

    def _build_tabs(self, parent_layout) -> None:
        """
        Вкладки, их сигналы и сайдбар.

        Создаёт: `self.tabs`, шесть вкладок (`self.driver_tab`,
        `self.customer_tab`, `self.carrier_tab`, `self.vehicles_tab`,
        `self.trailer_tab`, `self.contract_tab`), `self.side_nav`.

        Порядок вкладок на экране задан `addTab`: Перевозчик, Водитель,
        Тягач и полуприцеп, Перевозимые авто, Договор, Заказчик. Верхние
        ярлыки QTabWidget скрыты, но сам виджет остаётся: страницы, их
        порядок, сигналы и программные переходы (`setCurrentWidget`) не
        меняются — сайдбар лишь переключает те же страницы.

        ВАЖНО про connect: `tab.clear_requested` подключается к слоту БЕЗ
        замыкания — вкладку слот берёт у отправителя сигнала. `lambda`,
        захватывающая вкладку, создаёт цикл ссылок Python ↔ Qt и роняет
        процесс при выходе.
        """
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

        # ── Действия вкладок (ЭТАП 2B) ──
        # Кнопка «Создать договор» есть и на вкладках, и в шапке: она собирает
        # данные со ВСЕХ вкладок — документ один. «Очистить форму» очищает
        # только свою вкладку; вкладку слот берёт у отправителя сигнала
        # (sender()), а не из замыкания: lambda, захватывающая вкладку,
        # создаёт цикл ссылок Python ↔ Qt и роняет процесс при выходе.
        for tab in (
            self.carrier_tab, self.driver_tab, self.trailer_tab,
            self.vehicles_tab, self.contract_tab, self.customer_tab,
        ):
            tab.create_contract_requested.connect(self._on_create_contract)
            tab.clear_requested.connect(self._on_tab_clear_requested)

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
        parent_layout.addLayout(body)

        self._sync_side_nav(self.tabs.currentIndex())

        self._sync_loadings_to_vehicles()
        self._sync_unloadings_to_vehicles()

        self.statusBar().showMessage("Готово")

        logger.debug(
            f"UI построен: вкладок={self.tabs.count()} "
            f"({[self.tabs.tabText(i) for i in range(self.tabs.count())]})"
        )

    def _build_status_bar(self) -> None:
        """
        Постоянные зоны статус-бара: вкладка, заполненность, сохранение.

        Создаёт `self._last_save_at`, `self._status_tab_label`,
        `self._status_fields_label`, `self._status_saved_label` и таймер
        `self._status_timer`.

        Счётчик полей обновляется по таймеру: сигналы от каждого поля
        заставили бы дёргать `get_data()` на каждое нажатие клавиши. Таймер
        запускается ПОСЛЕ создания меток — иначе первый же тик упал бы на
        отсутствующем атрибуте.
        """
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

    def _build_progress(self) -> None:
        """Индикатор прогресса распознавания (Шаг 8 оптимизации)."""
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress_bar)

    # --------------------------------------------------------
    # ОБВЯЗКА ОКНА
    # --------------------------------------------------------
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

    # --------------------------------------------------------
    # СИНХРОНИЗАЦИЯ ТОЧЕК МАРШРУТА С ТАБЛИЦЕЙ ТС
    # --------------------------------------------------------
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

    # --------------------------------------------------------
    # ОЧИСТКА ФОРМЫ  ← ЭТАП 2B
    # --------------------------------------------------------
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

    def _on_tab_clear_requested(self) -> None:
        """
        Слот кнопки «Очистить форму» на вкладке (ЭТАП 2B).

        Вкладка берётся у отправителя сигнала: держать её в замыкании
        (lambda t=tab: ...) нельзя — получается цикл ссылок Python ↔ Qt,
        из-за которого процесс падает при завершении.
        """
        tab = self.sender()
        if tab is None:
            logger.warning("Запрос очистки вкладки без отправителя — пропущен")
            return
        self._on_clear_tab(tab)

    def _on_clear_tab(self, tab) -> None:
        """
        Очищает ТОЛЬКО указанную вкладку (ЭТАП 2B).

        Раньше кнопка «Очистить форму» в шапке чистила все вкладки сразу.
        Теперь у каждой вкладки своя кнопка: пользователь очищает только то,
        что ему нужно. Старый _on_clear_form остаётся (для совместимости),
        но из UI больше не вызывается.
        """
        logger.info("Очистка вкладки: %s", type(tab).__name__)
        self._log_ui_action(
            "нажата кнопка «Очистить форму» на вкладке",
            tab=type(tab).__name__,
        )
        tab.clear()
        if tab is self.contract_tab:
            self._sync_loadings_to_vehicles()
            self._sync_unloadings_to_vehicles()
        self._refresh_status_indicators()
        self.statusBar().showMessage("Вкладка очищена", 3000)
