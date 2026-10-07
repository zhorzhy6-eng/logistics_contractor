#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Базовое окно для новых типов договоров (ЭТАП 2C).

Каркас:
  * шапка: название окна, селектор типа, кнопки «Отзеркалить из
    Экспедиторства» (у типов-целей, ШАГ FIX-4) и «Выход»;
  * body: SideNav слева + QTabWidget справа (по образцу MainWindow);
  * вкладки размечены в TAB_CONFIGS подкласса;
  * панель действий «Создать договор» / «Очистить форму» внизу каждой вкладки;
  * селектор эмитит switch_to_type_requested(str), main.py ловит и переключает.

Что НЕ делает:
  * не генерирует документы;
  * не вызывает GigaChat (распознавание — в окнах типов).

Зеркало данных (ШАГ FIX-4)
--------------------------
Оператор заполняет один рейс дважды: сначала в «Экспедиторстве» (договор
с перевозчиком), потом в «Формике» или «Логистиксе» (заявка генподрядчику).
Списки машин, точек маршрута, водитель и автовоз в этих документах
совпадают, поэтому в шапке окна-цели есть кнопка «Отзеркалить из
Экспедиторства»: она собирает данные источника, строит план переноса
(core/mirror.py) и раскладывает его по вкладкам.

Источник (MainWindow, «Экспедиторство») — НЕ подкласс BaseContractWindow,
поэтому он регистрируется здесь слабой ссылкой (register_source_window),
а цель находит его через find_expedition_window(). Ссылка слабая: окна
живут до выхода из приложения, и сильная ссылка не дала бы им закрыться.

Окна типов живут всё время приложения: закрытие крестиком — это hide()
(данные в форме не теряются), реальное закрытие — force_close(), его
вызывает WindowManager.close_all() при выходе.
"""

import logging
import weakref
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import QSize, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from core.contracts.picker_order import picker_title
from core.mirror import (
    SOURCE_TYPE, SUPPORTED_TARGETS, MirrorPlan, collect_source, is_empty,
    source_has_data, text,
)
from core.prompts import get_prompt
from ui import theme
from ui.controls.contract_type_selector import ContractTypeSelector
from ui.icons import action_icon, tab_icon
from ui.navigation import SideNav

logger = logging.getLogger("ui.windows.base_window")

#: Заголовок кнопки зеркала — один на все окна-цели.
MIRROR_BUTTON_TITLE = "Отзеркалить из Экспедиторства"

#: Заголовок окна-источника по умолчанию (MainWindow).
SOURCE_WINDOW_TITLE = "Генератор договоров перевозки"

#: Подписи полей плана для диалога конфликтов. Ключи — те же, что несёт
#: core/mirror.py; незнакомые поля печатаются своим ключом.
MIRROR_FIELD_TITLES: Dict[str, str] = {
    "number": "Номер",
    "date": "Дата",
    "route": "Направление",
    "loading_address": "Адрес погрузки",
    "unloading_address": "Адрес выгрузки",
    "shipper_name": "Грузоотправитель",
    "loading_addresses": "Адреса погрузки",
    "consignees": "Грузополучатели",
    "vehicles": "Перевозимые ТС",
    "full_name": "ФИО водителя",
    "birth_date": "Дата рождения",
    "tractor_brand": "Марка тягача",
    "tractor_plate": "Госномер тягача",
    "tractor_type": "Тип ТС",
    "trailer_brand": "Марка полуприцепа",
    "trailer_plate": "Госномер полуприцепа",
}

# ─────────────────────────────────────────────────────────────
# Реестр окна-источника (Экспедиторство)
# ─────────────────────────────────────────────────────────────
#: Слабые ссылки на окна «Экспедиторства». Список, а не одно значение:
#: в тестах окна создаются и закрываются пачками, и по закрытии ссылка
#: должна исчезнуть сама, без «уборки» из окна-цели.
_SOURCE_WINDOWS: List["weakref.ref"] = []


def register_source_window(window: Any) -> None:
    """
    Регистрирует окно-источник (MainWindow) для зеркала данных.

    Вызывается MainWindow в конце __init__: цель ищет источник по реестру,
    а не по типу класса — иначе ui/windows зависел бы от ui/main_window.
    """
    if window is None:
        return

    _SOURCE_WINDOWS[:] = [ref for ref in _SOURCE_WINDOWS if ref() is not None]
    if any(ref() is window for ref in _SOURCE_WINDOWS):
        return

    try:
        _SOURCE_WINDOWS.append(weakref.ref(window))
    except TypeError:
        # Объект без слабых ссылок (заглушка в тесте): регистрировать нечего.
        logger.warning("Зеркало: окно-источник не поддерживает слабые ссылки")
        return

    logger.debug(
        "Зеркало: окно-источник зарегистрировано (%s)", type(window).__name__
    )


def clear_source_windows() -> None:
    """Сбрасывает реестр окна-источника (нужно тестам)."""
    _SOURCE_WINDOWS.clear()


def _window_is_source(window: Any) -> bool:
    """
    Похоже ли окно на источник зеркала.

    Ищем по атрибуту CONTRACT_TYPE (он есть у BaseContractWindow и у
    MainWindow): значение читается лениво и молча — у заглушки в тесте
    атрибута может не быть вовсе.
    """
    try:
        contract_type = getattr(window, "CONTRACT_TYPE", None)
    except Exception:  # noqa: BLE001 — свойство заглушки может упасть
        return False
    return text(contract_type) == SOURCE_TYPE


def find_expedition_window() -> Optional[Any]:
    """
    Окно «Экспедиторство» — источник зеркала (или None, если его нет).

    Два пути: реестр (окно зарегистрировало себя при создании) и, как
    запасной, поиск среди окон приложения по CONTRACT_TYPE = "perevozka".
    Запасной путь нужен тестам и коду, который поднял MainWindow, не
    регистрируя его явно.
    """
    for ref in list(_SOURCE_WINDOWS):
        window = ref()
        if window is not None and _window_is_source(window):
            return window

    app = QApplication.instance()
    if app is None:
        return None

    for widget in app.topLevelWidgets():
        if _window_is_source(widget):
            return widget
    return None



class BaseContractWindow(QMainWindow):
    """Общий каркас окна типа договора: шапка, сайдбар, вкладки-заглушки."""

    #: Ключ типа (значение ContractType) — обязателен у подкласса.
    CONTRACT_TYPE: str = ""
    #: Заголовок окна — обязателен у подкласса.
    WINDOW_TITLE: str = ""
    #: Вкладки: (заголовок, ключ иконки в resources/icons/tabs/).
    TAB_CONFIGS: List[Tuple[str, str]] = []

    #: Ключи вкладок окна по порядку — те же имена, что у сборщиков данных
    #: окон типов (data.build) и у плана зеркала (core/mirror.py).
    _TAB_KEYS: Tuple[str, ...] = (
        "customer_tab", "cargo_tab", "route_tab",
        "driver_tab", "vehicle_tab", "price_tab",
    )

    #: Пользователь выбрал другой тип в селекторе.
    switch_to_type_requested = pyqtSignal(str)
    #: Пользователь нажал «Выход».
    exit_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(self.WINDOW_TITLE or picker_title(self.CONTRACT_TYPE))
        self.resize(1200, 900)
        self._init_ui()
        # Подсказка кнопки зеркала — по состоянию источника (ШАГ FIX-4):
        # кнопка живёт в шапке, а источник заполняют в другом окне.
        self._refresh_mirror_button()
        logger.info(
            "Окно типа %r создано: вкладок=%s",
            self.CONTRACT_TYPE, self.tabs.count(),
        )

    # ---------------------------------------------------------
    # Построение интерфейса
    # ---------------------------------------------------------
    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 16, 20, 8)
        main_layout.setSpacing(12)

        main_layout.addWidget(self._build_header())

        # ── Body: сайдбар + вкладки (как в MainWindow) ──
        self.tabs = QTabWidget()
        self.tabs.setIconSize(QSize(24, 24))
        # Верхние ярлыки скрыты: разделы переключает сайдбар. Сам QTabWidget
        # остаётся — страницы, их порядок и сигналы такие же, как в MainWindow.
        self.tabs.tabBar().setVisible(False)

        self.side_nav = SideNav("Разделы")
        for tab_title, icon_key in self.TAB_CONFIGS:
            tab = self._make_tab(tab_title, icon_key)
            self.tabs.addTab(tab, tab_icon(icon_key), tab_title)
            self.side_nav.add_item(tab_title, tab_icon(icon_key))

        self.side_nav.navigate.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self._sync_side_nav)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self.side_nav)
        body.addWidget(self.tabs, 1)
        main_layout.addLayout(body)

        self._sync_side_nav(self.tabs.currentIndex())
        self.statusBar().showMessage("Готово")

    def _build_header(self) -> QFrame:
        """
        Шапка: название окна, селектор типа и кнопки действий.

        Кнопка «Отзеркалить из Экспедиторства» (ШАГ FIX-4) появляется только
        у типов-целей зеркала (Формика, Логистикс Рус): у Аренды, Хавалов и
        самого Экспедиторства её нет — там переносить нечего.
        """
        header_frame = QFrame()
        header_frame.setObjectName("appHeader")
        header = QHBoxLayout(header_frame)
        header.setContentsMargins(16, 10, 16, 10)
        header.setSpacing(12)

        heading = QVBoxLayout()
        heading.setSpacing(2)
        title = QLabel(self.WINDOW_TITLE or picker_title(self.CONTRACT_TYPE))
        title.setObjectName("appHeading")
        subtitle = QLabel("Тип договора в разработке")
        subtitle.setObjectName("appSubheading")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()

        self.selector = ContractTypeSelector(self.CONTRACT_TYPE)
        # Сигнал в сигнал: логика переключения окон живёт в main.py,
        # виджет только сообщает о выборе пользователя.
        self.selector.contract_type_selected.connect(self.switch_to_type_requested)
        header.addWidget(self.selector)

        self.btn_mirror = self._build_mirror_button()
        if self.btn_mirror is not None:
            header.addWidget(self.btn_mirror)

        self.btn_exit = theme.secondary_button(
            "Выход", tooltip="Закрыть программу"
        )
        self.btn_exit.clicked.connect(self.exit_requested)
        header.addWidget(self.btn_exit)

        return header_frame

    def _build_mirror_button(self):
        """
        Кнопка «Отзеркалить из Экспедиторства» — или None для чужих типов.

        Кнопка живёт атрибутом btn_mirror и ВСЕГДА активна у типов-целей:
        перенос сам скажет, если источника нет или он пуст. Выключенная
        кнопка этого не объяснила бы — оператор видел бы серую кнопку без
        причины (состояние источника меняется в другом окне, а оно скрыто).
        """
        if self.CONTRACT_TYPE not in SUPPORTED_TARGETS:
            return None

        # Слот — метод окна, без lambda: замыкание на окно даёт цикл ссылок
        # Python ↔ Qt и роняет процесс при завершении (AGENTS.md § 5.1).
        button = theme.secondary_button(
            MIRROR_BUTTON_TITLE,
            tooltip=(
                "Перенести данные рейса из окна «Экспедиторство»: "
                "машины, точки маршрута, водителя и автовоз"
            ),
        )
        button.clicked.connect(self._on_mirror_clicked)
        return button

    def _refresh_mirror_button(self) -> None:
        """
        Обновляет подсказку кнопки зеркала по состоянию источника.

        Активность кнопки не меняется (см. _build_mirror_button), а вот
        подсказка полезна: она говорит, есть ли в источнике данные и куда
        он делся, если его ещё не открывали. Состояние источника живёт
        в другом окне, поэтому метод вызывается ещё и при показе окна.
        """
        button = getattr(self, "btn_mirror", None)
        if button is None:
            return

        source = find_expedition_window()
        if source is None:
            button.setToolTip(
                "Окно «Экспедиторство» ещё не открывалось: откройте его "
                "в селекторе типа договора и заполните данные"
            )
            return

        if not source_has_data(collect_source(source)):
            button.setToolTip(
                "В окне «Экспедиторство» пока нет данных (ВИН, адреса) — "
                "заполните его и повторите"
            )
            return

        button.setToolTip(
            "Перенести данные рейса из окна «Экспедиторство»: "
            "машины, точки маршрута, водителя и автовоз"
        )

    def showEvent(self, event) -> None:
        """
        При показе окна обновляем подсказку кнопки зеркала.

        Источник заполняют в другом окне, поэтому состояние кнопки надо
        перечитывать при каждом возврате к цели, а не один раз в __init__.
        """
        super().showEvent(event)
        self._refresh_mirror_button()

    def _make_tab(self, title: str, icon_key: str) -> QWidget:
        """
        Фабрика вкладки по заголовку. По умолчанию — заглушка.

        Окна типов, у которых вкладки уже написаны (ЭТАП 3.1.B, Формика),
        переопределяют этот хук и возвращают настоящую вкладку. Каркас окна
        при этом не меняется: порядок вкладок, сайдбар и сигналы остаются
        общими для всех типов.

        :param title: заголовок вкладки из TAB_CONFIGS.
        :param icon_key: ключ иконки вкладки (resources/icons/tabs/).
        :return: виджет страницы QTabWidget.
        """
        return self._make_placeholder_tab(title)

    def _make_placeholder_tab(self, title: str) -> QWidget:
        """Вкладка-заглушка: сообщение и панель действий."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        label = QLabel(f"Вкладка «{title}» в разработке")
        label.setObjectName("sectionHint")
        layout.addWidget(label)
        layout.addStretch()

        layout.addWidget(self._build_tab_actions(tab))

        return tab

    def _build_tab_actions(self, tab: QWidget) -> QFrame:
        """
        Панель «Создать договор» + «Очистить форму» (заглушки).

        Кнопки, как и у вкладок MainWindow (ЭТАП 2B), сохраняются атрибутами
        самой вкладки: btn_create_contract, btn_clear_form, btn_recognize.
        """
        frame = QFrame()
        frame.setObjectName("actionBar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        tab.btn_create_contract = theme.accent_button(
            "Создать договор",
            tooltip="Проверить данные и сформировать договор DOCX",
        )
        tab.btn_create_contract.setIcon(action_icon("contract.svg"))
        tab.btn_create_contract.setIconSize(QSize(18, 18))
        tab.btn_create_contract.clicked.connect(self._on_create_contract)
        layout.addWidget(tab.btn_create_contract)

        tab.btn_clear_form = theme.secondary_button(
            "Очистить форму",
            tooltip="Очистить поля только этой вкладки",
        )
        tab.btn_clear_form.setIcon(action_icon("clear.svg"))
        tab.btn_clear_form.setIconSize(QSize(18, 18))
        tab.btn_clear_form.clicked.connect(self._on_clear_tab)
        layout.addWidget(tab.btn_clear_form)

        tab.btn_recognize = theme.primary_button("Распознать данные")
        tab.btn_recognize.setIcon(action_icon("recognize.svg"))
        tab.btn_recognize.setIconSize(QSize(18, 18))
        tab.btn_recognize.clicked.connect(self._on_recognize)
        layout.addWidget(tab.btn_recognize)

        layout.addStretch()
        return frame

    def _sync_side_nav(self, index: int) -> None:
        """Подсвечивает пункт сайдбара при смене вкладки."""
        side_nav = getattr(self, "side_nav", None)
        if side_nav is not None:
            side_nav.set_current_index(index)

    # ---------------------------------------------------------
    # Вспомогательное (для тестов и внешнего кода)
    # ---------------------------------------------------------
    def tab_titles(self) -> List[str]:
        """Заголовки вкладок в порядке добавления."""
        return [self.tabs.tabText(i) for i in range(self.tabs.count())]

    def refresh_icons(self) -> None:
        """
        Пересобирает иконки под активную тему.

        Пока окна-заглушки создаются в текущей теме; метод нужен на будущее,
        когда окна типов станут рабочими и переживут смену оформления.
        """
        for index, (_title, icon_key) in enumerate(self.TAB_CONFIGS):
            icon = tab_icon(icon_key)
            self.tabs.setTabIcon(index, icon)
            self.side_nav.set_item_icon(index, icon)
        for tab in self._tabs():
            for attr, name in (
                ("btn_create_contract", "contract.svg"),
                ("btn_clear_form", "clear.svg"),
                ("btn_recognize", "recognize.svg"),
            ):
                button = getattr(tab, attr, None)
                if button is not None:
                    button.setIcon(action_icon(name))

    def _tabs(self) -> List[QWidget]:
        """Вкладки окна (страницы QTabWidget)."""
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def _sender_tab(self) -> Optional[QWidget]:
        """
        Вкладка, которой принадлежит отправитель сигнала.

        Кнопки панели действий лежат внутри вкладки, но подниматься к ней
        нужно по иерархии виджетов: sender() — это кнопка, а не страница
        QTabWidget.
        """
        widget = self.sender()
        while widget is not None and self.tabs.indexOf(widget) < 0:
            widget = widget.parentWidget()
        return widget

    # ---------------------------------------------------------
    # Зеркало данных: Экспедиторство → это окно (ШАГ FIX-4)
    # ---------------------------------------------------------
    def _on_mirror_clicked(self) -> None:
        """
        «Отзеркалить из Экспедиторства»: план → конфликты → раскладка.

        Импорт внутри метода: core.mirror знает про ui, а ui про него —
        только в момент нажатия; на импорте модулей это был бы цикл.
        """
        from core.mirror import mirror_from_expedition

        plan = mirror_from_expedition(self, self.CONTRACT_TYPE)
        if plan is None:
            QMessageBox.warning(
                self, "Зеркало",
                "Не удалось получить данные из Экспедиторства.\n"
                "Убедитесь, что окно Экспедиторства открыто и содержит "
                "данные (ВИН, адреса)."
            )
            return

        if plan.conflicts:
            answer = self._ask_mirror_conflicts(plan.conflicts)
            if answer is None:
                logger.info(
                    "Зеркало: перенос отменён пользователем (тип=%s)",
                    self.CONTRACT_TYPE,
                )
                return
            overwrite_existing = answer
        else:
            overwrite_existing = False

        self._apply_mirror_plan(plan, overwrite_existing)
        self._refresh_mirror_button()

        QMessageBox.information(
            self, "Зеркало", "Данные перенесены из Экспедиторства."
        )

    def _ask_mirror_conflicts(self, conflicts: List[tuple]) -> Optional[bool]:
        """
        Диалог конфликтов: что делать с уже заполненными полями цели.

        Показывается только тогда, когда такие поля есть (иначе вопроса не
        возникает). В списке — «Вкладка.Поле», старое и новое значение:
        пользователь видит, что именно потеряет, а что получит.

        :return: True — «Перезаписать всё»; False — «Не перезаписывать
            заполненное»; None — «Отмена» (перенос не выполняется).
        """
        box = QMessageBox(self)
        box.setWindowTitle("Зеркало")
        box.setIcon(QMessageBox.Question)
        box.setText(
            f"В целевом окне уже заполнено полей: {len(conflicts)}"
        )
        box.setInformativeText(
            self._format_mirror_conflicts(conflicts)
            + "\n\nПерезаписать эти поля данными из Экспедиторства?"
        )

        overwrite_button = box.addButton(
            "Перезаписать всё", QMessageBox.AcceptRole
        )
        keep_button = box.addButton(
            "Не перезаписывать заполненное", QMessageBox.DestructiveRole
        )
        cancel_button = box.addButton("Отмена", QMessageBox.RejectRole)
        box.setDefaultButton(keep_button)
        box.setEscapeButton(cancel_button)

        box.exec_()

        clicked = box.clickedButton()
        if clicked is overwrite_button:
            logger.info("Зеркало: пользователь выбрал «Перезаписать всё»")
            return True
        if clicked is keep_button:
            logger.info(
                "Зеркало: пользователь выбрал «Не перезаписывать заполненное»"
            )
            return False

        logger.info("Зеркало: пользователь отменил перенос")
        return None

    def _format_mirror_conflicts(self, conflicts: List[tuple]) -> str:
        """
        Список конфликтов текстом: «Вкладка.Поле: было → станет».

        Значения обрезаются: в диалоге нужен смысл, а не адрес целиком;
        полное значение пользователь видит в самой форме.
        """
        lines: List[str] = []
        for tab_key, name, old_value, new_value in conflicts:
            lines.append(
                f"• {self._mirror_tab_title(tab_key)}.{self._mirror_field_title(name)}:\n"
                f"    Было: {self._mirror_value_text(old_value)}\n"
                f"    Станет: {self._mirror_value_text(new_value)}"
            )
        return "\n".join(lines)

    def _mirror_tab_title(self, tab_key: str) -> str:
        """Заголовок вкладки по её ключу (для диалога и лога)."""
        index = self._TAB_KEYS.index(tab_key) if tab_key in self._TAB_KEYS else -1
        if 0 <= index < self.tabs.count():
            return self.tabs.tabText(index)
        return tab_key

    def _mirror_field_title(self, field: str) -> str:
        """
        Подпись поля для диалога.

        Своих подписей у полей плана нет, а имена ключей английские:
        небольшая карта переводит частые поля, остальные печатаются как есть
        (ключ понятен и в диалоге, и в отчёте).
        """
        return MIRROR_FIELD_TITLES.get(field, field)

    @staticmethod
    def _mirror_value_text(value: Any, limit: int = 60) -> str:
        """Значение для диалога: списки — по количеству, строки — обрезкой."""
        if isinstance(value, (list, tuple)):
            return f"{len(value)} записей"
        rendered = text(value)
        if not rendered:
            return "—"
        return rendered if len(rendered) <= limit else rendered[:limit - 1] + "…"

    def _apply_mirror_plan(self, plan: MirrorPlan,
                           overwrite_existing: bool) -> None:
        """
        Раскладывает данные плана по вкладкам окна.

        Каждая вкладка получает СВОЙ кусок плана: план собран ядром зеркала
        (core/mirror.py), а раскладка — это вызов fill_data() у вкладки.
        В режиме «не перезаписывать» из куска убираются поля, которые в цели
        уже заполнены, — тогда заполненное остаётся как есть.
        """
        for tab_key, data in plan.tabs.items():
            tab = self._tab_by_key(tab_key)
            if tab is None:
                logger.warning(
                    "Зеркало: вкладка %r не найдена — раздел пропущен", tab_key
                )
                continue

            payload = dict(data)
            if not overwrite_existing:
                current = self._tab_data(tab)
                payload = {
                    key: value for key, value in payload.items()
                    if not self._is_filled(current.get(key))
                }
                if not payload:
                    continue

            tab.fill_data(payload)

        logger.info(
            "Зеркало: данные перенесены (тип=%s, вкладок=%s, полей=%s, "
            "перезапись=%s)",
            self.CONTRACT_TYPE, len(plan.tabs), plan.field_count(),
            "да" if overwrite_existing else "нет",
        )

    def _tab_by_key(self, key: str):
        """
        Вкладка окна по ключу плана.

        Ключи те же, что у сборщиков данных окон типов: customer_tab,
        cargo_tab, route_tab, driver_tab, vehicle_tab, price_tab. Ищутся
        по порядку вкладок окна (_TAB_KEYS), а не по именам атрибутов:
        атрибуты поднимает _bind_tabs конкретного окна, и у каждого типа
        они свои.
        """
        if key not in self._TAB_KEYS:
            return None

        index = self._TAB_KEYS.index(key)
        if index >= self.tabs.count():
            return None
        return self.tabs.widget(index)

    @staticmethod
    def _tab_data(tab: Any) -> Dict[str, Any]:
        """Текущие данные вкладки: get_data() словарём (иначе пусто)."""
        getter = getattr(tab, "get_data", None)
        if getter is None:
            return {}
        try:
            data = getter()
        except Exception as e:  # noqa: BLE001 — вкладка не должна ронять перенос
            logger.error(
                "Зеркало: вкладка %s не отдала данные (%s)",
                type(tab).__name__, type(e).__name__,
            )
            return {}
        return dict(data) if isinstance(data, dict) else {}

    @staticmethod
    def _is_filled(value: Any) -> bool:
        """
        Заполнено ли поле цели.

        Пустое — None, пустая строка, пустой список, bool (в форме такого
        поля нет). Ноль заполненным считается: это ставка, сумма или год,
        а не пустое место (правило одно с core/mirror.py::is_empty).
        """
        return not is_empty(value)

    # ---------------------------------------------------------
    # Заглушки действий
    # ---------------------------------------------------------
    def _on_create_contract(self) -> None:
        logger.info(
            "UI: «Создать договор» для типа %s — ещё не реализовано",
            self.CONTRACT_TYPE,
        )
        QMessageBox.information(
            self, "В разработке",
            f"Генерация договора типа «{picker_title(self.CONTRACT_TYPE)}» "
            f"появится в следующих версиях."
        )

    def _on_clear_tab(self) -> None:
        """
        Очистка вкладки — заглушка (ЭТАП 2C).

        Вкладка берётся у отправителя сигнала (sender()), а не из замыкания:
        lambda, захватывающая вкладку, создаёт цикл ссылок Python ↔ Qt и
        роняет процесс при выходе (грабли ЭТАПА 2B).
        """
        tab = self._sender_tab()
        tab_title = self.tabs.tabText(self.tabs.indexOf(tab)) if tab is not None else ""
        logger.info(
            "UI: «Очистить форму» для типа %s, вкладка %r — ещё не реализовано",
            self.CONTRACT_TYPE, tab_title,
        )
        QMessageBox.information(
            self, "В разработке",
            f"Очистка вкладки для типа «{picker_title(self.CONTRACT_TYPE)}» "
            f"появится в следующих версиях."
        )

    def _on_recognize(self) -> None:
        """Набросок распознавания (ЭТАП 2C): вызывает get_prompt и логирует."""
        prompt = get_prompt(self.CONTRACT_TYPE)
        logger.info(
            "UI: набросок распознавания, тип=%s, промпт задан=%s",
            self.CONTRACT_TYPE, prompt is not None,
        )
        QMessageBox.information(
            self, "В разработке",
            f"Распознавание для типа «{picker_title(self.CONTRACT_TYPE)}» "
            f"появится в следующих версиях.\n\n"
            f"Промпт: {'задан' if prompt else 'ещё не написан'}."
        )

    # ---------------------------------------------------------
    # Закрытие окна — скрытие
    # ---------------------------------------------------------
    def closeEvent(self, event) -> None:
        """
        Закрытие окна = скрытие (ЭТАП 2C).

        Окна типов живут всё время приложения: крестик не должен терять
        данные формы. Реальное завершение — force_close() из
        WindowManager.close_all().
        """
        if getattr(self, "_force_close", False):
            event.accept()
            return

        logger.info("Окно типа %s скрыто (окно не уничтожено)", self.CONTRACT_TYPE)
        event.ignore()
        self.hide()

    def force_close(self) -> None:
        """Реальное закрытие (используется WindowManager.close_all())."""
        self._force_close = True
        self.blockSignals(True)
        self.close()
        self.deleteLater()


__all__ = [
    "BaseContractWindow",
    "MIRROR_BUTTON_TITLE",
    "MIRROR_FIELD_TITLES",
    "SOURCE_WINDOW_TITLE",
    "clear_source_windows",
    "find_expedition_window",
    "register_source_window",
]
