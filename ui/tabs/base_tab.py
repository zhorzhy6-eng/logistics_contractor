#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Общие методы вкладок (Шаг 3 рефакторинга архитектуры).

Раньше каждая вкладка несла свои копии одного и того же кода:
  * _on_recognize_requested — проброс текста в MainWindow (6 копий);
  * _set_date — установка даты в QDateEdit/PasteableDateEdit (3 копии);
  * count_filled_fields — 6 копий, не использовались нигде;
  * get_all_text — 5 копий, не использовались нигде.

Первые два переехали сюда, неиспользуемые удалены.

Миксин НЕ наследует QWidget, поэтому в объявлении вкладки он идёт первым:

    class DriverTab(DadataDriverMixin, QWidget):
        recognize_requested = pyqtSignal(str)

Здесь же живут миксины DaData (core/dadata_client.py):

    DadataFillMixin    — кнопка «🔎» у поля ИНН: реквизиты организации;
    DadataBankMixin    — кнопка «🔎» у поля БИК: банк и корр. счёт;
    DadataDriverMixin  — кнопка «🔎» у «Кем выдан паспорт»: подразделение ФМС.

Вся механика (пул потоков, кнопка, курсор ожидания, подтверждение
перезаписи, итоговые диалоги) живёт в общем предке _DadataChannelMixin,
а миксины лишь объявляют свой канал и способ применения результата —
никакого дублирования на 200 строк. Запрос уходит ТОЛЬКО по нажатию
кнопки: обработчиков textChanged у полей ИНН/БИК/кода нет.
"""

import logging
import re
import weakref
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import QDate, QObject, QRunnable, QSize, Qt, QThreadPool, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QInputDialog, QMessageBox, QWidget,
)

from core.dadata_client import (
    FMS_CODE_MIN_DIGITS,
    DadataClient,
    bank_status_warning,
    status_warning,
)
from core.dates import parse_date
from ui import theme
from ui.icons import action_icon

logger = logging.getLogger("ui.tabs.base_tab")


class TabMixin:
    """Общее поведение вкладок: распознавание, даты и статистика заполнения."""

    def count_filled_fields(self) -> Tuple[int, int]:
        """
        Сколько полей вкладки заполнено: (заполнено, всего).

        Нужно статус-бару («Заполнено 12 из 20»). Считаем по get_data(),
        который есть у каждой вкладки: списки/словари заполнены, если непусты,
        остальные значения — если после strip() что-то осталось.

        Метод только читает данные: интерфейс не меняется (никаких setText).
        """
        try:
            data = self.get_data()
        except Exception as e:  # noqa: BLE001 — счётчик не должен ломать UI
            logger.debug(f"{type(self).__name__}: подсчёт полей не удался: {e}")
            return (0, 0)

        if not isinstance(data, dict):
            return (0, 0)

        filled = 0
        for value in data.values():
            if isinstance(value, (list, tuple, dict, set)):
                filled += 1 if value else 0
            elif str(value or "").strip():
                filled += 1

        return (filled, len(data))

    def _on_recognize_requested(self, text: str) -> None:
        """Пробрасывает текст вкладки в MainWindow для распознавания."""
        logger.debug(
            f"{type(self).__name__}: запрошено распознавание, "
            f"{len(text or '')} символов"
        )
        self.recognize_requested.emit(text)

    def _set_date(self, date_edit, value: Any) -> bool:
        """
        Устанавливает дату в QDateEdit или PasteableDateEdit.

        Понимает форматы 2023-01-26, 26.01.2023, 26/01/2023, 2023.01.26,
        26-01-2023, 20230126, 26 01 2023, а также даты со временем.

        :return: True, если дата установлена; False, если разобрать не удалось
        """
        dt = parse_date(value)
        if dt is None:
            # Значение приходит из распознанных данных: без самого текста.
            shown = (
                f"<строка, {len(value)} символов>"
                if isinstance(value, str) else repr(value)
            )
            logger.warning(
                f"{type(self).__name__}: не удалось установить дату {shown}"
            )
            return False

        date_edit.setDate(QDate(dt.year, dt.month, dt.day))
        return True

    # ---------------------------------------------------------
    # Панель действий вкладки (ЭТАП 2B)
    # ---------------------------------------------------------
    def _build_tab_actions(self) -> QFrame:
        """
        Панель действий внизу вкладки: «Создать договор» + «Очистить форму».

        Раньше эти кнопки жили в шапке окна и действовали на все вкладки.
        Теперь они есть на каждой вкладке: «Создать» собирает данные со ВСЕХ
        вкладок окна (одна кнопка — один документ), «Очистить» чистит только
        текущую вкладку.

        Не QGroupBox: тест test_carrier_tab_has_semantic_sections сравнивает
        точный список QGroupBox в CarrierTab.

        Сигналы create_contract_requested / clear_requested объявляются
        в каждой вкладке отдельно: TabMixin — не QObject, pyqtSignal здесь
        невозможен.
        """
        frame = QFrame()
        frame.setObjectName("actionBar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        self.btn_create_contract = theme.accent_button(
            "Создать договор",
            tooltip="Проверить данные и сформировать договор DOCX",
        )
        self.btn_create_contract.setIcon(action_icon("contract.svg"))
        self.btn_create_contract.setIconSize(QSize(18, 18))
        self.btn_create_contract.clicked.connect(self._on_tab_create_clicked)
        layout.addWidget(self.btn_create_contract)

        self.btn_clear_form = theme.secondary_button(
            "Очистить форму",
            tooltip="Очистить поля только этой вкладки",
        )
        self.btn_clear_form.setIcon(action_icon("clear.svg"))
        self.btn_clear_form.setIconSize(QSize(18, 18))
        self.btn_clear_form.clicked.connect(self._on_tab_clear_clicked)
        layout.addWidget(self.btn_clear_form)

        layout.addStretch()
        return frame

    def _on_tab_create_clicked(self) -> None:
        """Пробрасывает запрос «Создать договор» в окно."""
        logger.debug(f"{type(self).__name__}: запрошено создание договора")
        self.create_contract_requested.emit()

    def _on_tab_clear_clicked(self) -> None:
        """Пробрасывает запрос «Очистить форму» в окно."""
        logger.debug(f"{type(self).__name__}: запрошена очистка вкладки")
        self.clear_requested.emit()


# ─────────────────────────────────────────────────────────────
# DaData: каналы запросов
# ─────────────────────────────────────────────────────────────
#: Свойство кнопки «🔎»: какой канал она запускает. Нужно, чтобы слот кнопки
#: был обычным методом вкладки: lambda в connect, захватывающая вкладку, даёт
#: цикл ссылок Python ↔ Qt (грабли 2B.7).
DADATA_CHANNEL_PROPERTY = "dadata_channel"

#: Описание каналов: что искать, из какого поля, как это называть в UI.
#: Значения не содержат персональных данных — только подписи и тексты.
DADATA_CHANNELS: Dict[str, Dict[str, str]] = {
    "party": {
        "method": "find_party_by_inn",
        "query_field": "inn",
        "query_title": "ИНН",
        "action": "Заполнить по ИНН",
        "tooltip": "Заполнить реквизиты по ИНН через DaData",
        "button_attr": "btn_fill_by_inn",
        "start_log": "по ИНН",
        "not_found_title": "Ничего не найдено",
        "not_found": (
            "Организация с таким ИНН не найдена.\n\n"
            "Проверьте номер или заполните поля вручную."
        ),
    },
    "bank": {
        "method": "find_bank_by_bic",
        # Имя поля формы — именно bik (b-i-k), иначе читается пустое значение
        "query_field": "bik",
        "query_title": "БИК",
        "action": "Заполнить банк по БИК",
        "tooltip": "Заполнить банк и корр. счёт по БИК через DaData",
        "button_attr": "btn_fill_by_bic",
        "start_log": "банка по БИК",
        "not_found_title": "Банк не найден",
        "not_found": (
            "Банк с таким БИК не найден.\n\n"
            "Проверьте номер или заполните поля вручную."
        ),
    },
    "fms": {
        "method": "suggest_fms_unit",
        "query_field": "passport_code",
        "query_title": "код подразделения",
        "action": "Заполнить подразделение ФМС",
        "tooltip": "Найти подразделение ФМС по коду подразделения",
        "button_attr": "btn_fill_fms",
        "start_log": "подразделения ФМС",
        "not_found_title": "Ничего не найдено",
        "not_found": (
            "Подразделение ФМС с таким кодом не найдено.\n\n"
            "Проверьте код подразделения или заполните поле вручную."
        ),
    },
}


class DadataSignals(QObject):
    """Сигналы задачи поиска в DaData (создаются в потоке интерфейса)."""

    #: найдено: плоский словарь реквизитов либо список подсказок (ФМС)
    finished = pyqtSignal(object)
    #: ничего не найдено — это не ошибка
    not_found = pyqtSignal()
    #: текст ошибки и признак «проблема с ключом DaData»
    error = pyqtSignal(str, bool)

    def __init__(self, task=None):
        super().__init__()
        # Ссылка на задачу-владельца СЛАБАЯ: сильная дала бы цикл
        # «задача → сигналы → задача», а связь слота живёт в C++ объекте
        # сигналов. Слот берёт задачу у отправителя: self.sender().owner().
        self._task_ref = weakref.ref(task) if task is not None else None

    def owner(self):
        """Задача-владелец сигналов (None, если её уже собрал сборщик мусора)."""
        return self._task_ref() if self._task_ref is not None else None


class DadataLookupTask(QRunnable):
    """
    Запрос к DaData в отдельном потоке (по образцу RecognitionTask).

    Сеть не должна блокировать интерфейс: запрос идёт в пуле потоков,
    а результат приходит в поток интерфейса через сигналы. Метод клиента
    выбирается по каналу (организация / банк / ФМС).
    """

    def __init__(self, query: str, method_name: str = "find_party_by_inn",
                 channel: str = "party", client_factory=None):
        super().__init__()
        self.query = query
        self.method_name = method_name
        self.channel = channel
        self.signals = DadataSignals(self)
        self._client_factory = client_factory or DadataClient

    def run(self) -> None:
        try:
            # Значение запроса (ИНН/БИК/код) в лог не попадает — только
            # длина, см. core/dadata_client.py.
            client = self._client_factory()
            finder = getattr(client, self.method_name)
            data = finder(self.query)
        except ValueError as exc:
            # Нет ключа DaData или некорректное значение: подсказка иная.
            logger.error("DaData: запрос невозможен (%s)", type(exc).__name__)
            self.signals.error.emit(str(exc), True)
        except RuntimeError as exc:
            logger.error("DaData: запрос не выполнен (%s)", type(exc).__name__)
            self.signals.error.emit(str(exc), False)
        except Exception as exc:  # noqa: BLE001 — поток не должен падать молча
            logger.error("DaData: непредвиденная ошибка (%s)", type(exc).__name__)
            self.signals.error.emit(
                f"DaData: непредвиденная ошибка ({type(exc).__name__}).\n\n"
                f"Подробности — в logs/errors.log.",
                False,
            )
        else:
            if data:
                self.signals.finished.emit(data)
            else:
                self.signals.not_found.emit()


class _DadataChannelMixin(TabMixin):
    """
    Общая инфраструктура запросов к DaData.

    Здесь живёт всё, что одинаково для ИНН, БИК и кода ФМС: кнопка «🔎»,
    пул потоков, состояние «идёт запрос», подтверждение перезаписи полей
    и итоговые диалоги. Наследники задают только канал и применение
    результата — дублирования кода нет.
    """

    #: Название вкладки для сообщений и логов
    DADATA_TAB_TITLE = "вкладка"
    #: Канал по умолчанию (переопределяется в конкретных миксинах)
    DADATA_CHANNEL = "party"

    #: Поля, которые заполняются только если пустые (введённое вручную не трогаем)
    DADATA_EMPTY_ONLY_FIELDS: Tuple[str, ...] = ()

    #: Какие каналы умеет вкладка и чем применяется результат
    DADATA_APPLIERS: Dict[str, str] = {
        "party": "_apply_dadata",
        "bank": "_apply_dadata_bank",
        "fms": "_apply_dadata_fms",
    }
    #: Проверки значений по каналам
    DADATA_VALIDATORS: Dict[str, str] = {
        "party": "_validate_inn",
        "bank": "_validate_bic",
        "fms": "_validate_fms_code",
    }

    #: Человекочитаемые названия полей для диалогов
    DADATA_FIELD_TITLES: Dict[str, str] = {
        "full_name": "Полное наименование",
        "short_name": "Сокращённое наименование",
        "inn": "ИНН",
        "kpp": "КПП",
        "ogrn": "ОГРН / ОГРНИП",
        "legal_address": "Юридический адрес",
        "director_name": "ФИО руководителя",
        "director_position": "Должность руководителя",
        "phone": "Телефон",
        "email": "E-mail",
        "carrier_type": "Тип перевозчика",
        "bic": "БИК",
        "bank_name": "Наименование банка",
        "correspondent_account": "Корр. счёт",
        "passport_code": "Код подразделения",
        "passport_issuer": "Кем выдан паспорт",
    }

    # ---------------------------------------------------------
    # Состояние: пул потоков, задачи, курсор ожидания
    # ---------------------------------------------------------
    def _ensure_dadata_state(self) -> None:
        """Создаёт пул потоков и словари состояния при первом обращении."""
        if getattr(self, "_dadata_pool", None) is not None:
            return
        self._dadata_pool = QThreadPool(self)
        self._dadata_pool.setMaxThreadCount(1)
        self._dadata_tasks: Dict[str, Optional[DadataLookupTask]] = {}
        #: Виджет-источник значения по каналу: заполняется при создании кнопки
        #: (_setup_dadata_button). Именно он и читается при нажатии — так
        #: значение берётся из того поля, у которого стоит кнопка, даже если
        #: имя в DADATA_CHANNELS когда-нибудь разойдётся с полем формы.
        self._dadata_query_widgets: Dict[str, QWidget] = {}
        self._dadata_busy_channels: set = set()
        self._dadata_cursor_shown = False

    @property
    def _dadata_task(self):
        """Текущая задача канала «организация» (для обратной совместимости)."""
        self._ensure_dadata_state()
        return self._dadata_tasks.get("party")

    def _dadata_button(self, channel: str):
        """Кнопка «🔎» указанного канала (или None, если её нет)."""
        spec = DADATA_CHANNELS.get(channel) or {}
        attr = spec.get("button_attr", "")
        return getattr(self, attr, None) if attr else None

    def _dadata_query_widget(self, channel: str):
        """
        Поле-источник значения для канала.

        Приоритет — имя из DADATA_CHANNELS[channel]["query_field"]: источник
        не всегда то поле, у которого стоит кнопка (у канала fms кнопка
        у «Кем выдан паспорт», а код берётся из «Код подразделения»).
        Виджет, запомненный при создании кнопки, — запасной вариант на случай,
        если поля с таким именем у вкладки нет.
        """
        spec = DADATA_CHANNELS.get(channel) or {}
        field = spec.get("query_field", "")
        widget = getattr(self, field, None) if field else None
        if widget is not None:
            return widget

        self._ensure_dadata_state()
        return self._dadata_query_widgets.get(channel)

    # ---------------------------------------------------------
    # Построение интерфейса
    # ---------------------------------------------------------
    def _setup_dadata_button(self, channel: str,
                             field_widget: QWidget) -> QWidget:
        """
        Создаёт кнопку «🔎» рядом с полем и возвращает контейнер для addRow.

        Кнопка вешается на поле-источник (ИНН, БИК, «Кем выдан паспорт»)
        и срабатывает только по нажатию: никаких обработчиков ввода.

        :param channel: "party" | "bank" | "fms"
        :param field_widget: виджет поля (PasteableLineEdit)
        :return: контейнер «поле + кнопка» для QFormLayout.addRow
        """
        self._ensure_dadata_state()
        spec = DADATA_CHANNELS[channel]

        # Поле-источник запоминаем вместе с кнопкой: при нажатии значение
        # читается именно из него (см. _dadata_query_widget).
        self._dadata_query_widgets[channel] = field_widget
        if getattr(self, spec["query_field"], None) is None:
            # Опечатка в имени поля в DADATA_CHANNELS (например, «bic» вместо
            # «bik»): значение будет прочитано из поля у кнопки, но
            # декларация канала неверна — это видно в логе.
            logger.warning(
                "DaData: канал %s ссылается на поле %r, которого нет "
                "у вкладки %s — значение возьмётся из поля у кнопки",
                channel, spec["query_field"], self.DADATA_TAB_TITLE,
            )

        # Оформление — как у кнопок 📋/🧠 у полей (ui/theme.py, ghost).
        button = theme.ghost_button("🔎", tooltip=spec["tooltip"])
        setattr(self, spec["button_attr"], button)
        # Канал лежит свойством на самой кнопке, а слот — метод вкладки:
        # lambda, захватывающая вкладку, создаёт цикл Python ↔ Qt, который
        # роняет процесс при выходе (грабли 2B.7).
        button.setProperty(DADATA_CHANNEL_PROPERTY, channel)
        button.clicked.connect(self._on_dadata_button_pressed)

        container = QWidget(self)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        row.addWidget(field_widget, 1)
        row.addWidget(button)
        return container

    # ---------------------------------------------------------
    # Нажатие кнопки
    # ---------------------------------------------------------
    def _on_dadata_button_pressed(self, _checked: bool = False) -> None:
        """
        Нажата кнопка «🔎»: канал берётся у отправителя сигнала.

        Кнопка помнит свой канал свойством (DADATA_CHANNEL_PROPERTY), поэтому
        в connect не нужны ни lambda, ни замыкание на вкладку.
        """
        button = self.sender()
        channel = button.property(DADATA_CHANNEL_PROPERTY) if button is not None else None
        if not isinstance(channel, str) or channel not in DADATA_CHANNELS:
            logger.warning(
                "DaData: у нажатой кнопки не определён канал (%r) — запрос пропущен",
                channel,
            )
            return
        self._on_dadata_button_clicked(channel)

    def _on_dadata_button_clicked(self, channel: str) -> None:
        """Проверяет значение поля и запускает запрос (без автозапуска)."""
        spec = DADATA_CHANNELS[channel]
        logger.info(
            "UI: нажата кнопка «%s» на вкладке %s",
            spec["action"], self.DADATA_TAB_TITLE,
        )

        self._ensure_dadata_state()
        if self._dadata_tasks.get(channel) is not None:
            logger.info("DaData: запрос уже выполняется — повторный не запускаем")
            return

        query = self._dadata_field_text(self._dadata_query_widget(channel))
        problem = self._validate_dadata_query(channel, query)
        if problem:
            logger.info(
                "DaData: запрос не отправлен (канал=%s, длина=%s)",
                channel, len(query),
            )
            QMessageBox.warning(self, f"Проверьте {spec['query_title']}", problem)
            return

        self._start_dadata_lookup(channel, query)

    def _validate_dadata_query(self, channel: str, value: str) -> str:
        """Текст проблемы со значением или пустая строка, если всё в порядке."""
        name = self.DADATA_VALIDATORS.get(channel)
        validator = getattr(self, name, None) if name else None
        if validator is None:
            logger.warning("DaData: для канала %s нет валидатора", channel)
            return ""
        return validator(value)

    def _start_dadata_lookup(self, channel: str, query: str) -> None:
        """Запускает запрос к DaData в пуле потоков."""
        spec = DADATA_CHANNELS[channel]
        logger.info(
            "DaData: старт поиска %s (длина=%s), вкладка %s",
            spec["start_log"], len(query), self.DADATA_TAB_TITLE,
        )
        self._ensure_dadata_state()
        self._set_dadata_busy(True, channel)

        task = DadataLookupTask(query, spec["method"], channel)
        self._dadata_tasks[channel] = task
        # Задачу слот находит у отправителя сигнала (DadataSignals.owner): ни
        # lambda, ни functools.partial — оба держат сильную ссылку на вкладку,
        # а связь живёт в C++ объекте сигналов (цикл Python ↔ Qt, грабли 2B.7).
        task.signals.finished.connect(self._on_dadata_finished)
        task.signals.not_found.connect(self._on_dadata_not_found)
        task.signals.error.connect(self._on_dadata_error)
        self._dadata_pool.start(task)

    # ---------------------------------------------------------
    # Состояние «идёт запрос»
    # ---------------------------------------------------------
    def _set_dadata_busy(self, busy: bool,
                         channel: Optional[str] = None) -> None:
        """
        Блокирует кнопку канала и держит курсор ожидания, пока идёт запрос.

        Курсор снимается только тогда, когда завершились все параллельные
        запросы вкладки: setOverrideCursor/restoreOverrideCursor парные.
        """
        self._ensure_dadata_state()
        channel = channel or self.DADATA_CHANNEL

        button = self._dadata_button(channel)
        if button is not None:
            button.setEnabled(not busy)

        if busy:
            self._dadata_busy_channels.add(channel)
            if not self._dadata_cursor_shown:
                QApplication.setOverrideCursor(Qt.WaitCursor)
                self._dadata_cursor_shown = True
        else:
            self._dadata_busy_channels.discard(channel)
            if self._dadata_cursor_shown and not self._dadata_busy_channels:
                QApplication.restoreOverrideCursor()
                self._dadata_cursor_shown = False

    def _dadata_channel_for(self, task) -> str:
        """Канал задачи (или канал по умолчанию, если задачи нет)."""
        return getattr(task, "channel", None) or self.DADATA_CHANNEL

    def _dadata_task_of_sender(self):
        """
        Задача DaData, чьи сигналы пришли в слот.

        Задача берётся у отправителя сигнала: Qt отдаёт в sender() объект
        DadataSignals, а он помнит своего владельца слабой ссылкой
        (см. DadataSignals.owner). Так слот знает КОНКРЕТНУЮ задачу — связать
        её через lambda или partial нельзя (цикл Python ↔ Qt, грабли 2B.7).
        """
        signals = self.sender()
        owner = getattr(signals, "owner", None)
        task = owner() if callable(owner) else None
        if task is not None:
            return task

        self._ensure_dadata_state()
        for candidate in self._dadata_tasks.values():
            if candidate is not None and candidate.signals is signals:
                return candidate
        return None

    def _dadata_task_from(self, task):
        """Задача из аргумента слота; без него — по отправителю сигнала."""
        return task if task is not None else self._dadata_task_of_sender()

    def _is_current_dadata_task(self, task) -> bool:
        if task is None:
            return True
        self._ensure_dadata_state()
        return self._dadata_tasks.get(self._dadata_channel_for(task)) is task

    def _finish_dadata_lookup(self, task=None) -> None:
        """Снимает состояние «идёт запрос» для канала задачи."""
        self._ensure_dadata_state()
        channel = self._dadata_channel_for(task)
        self._dadata_tasks[channel] = None
        self._set_dadata_busy(False, channel)

    # ---------------------------------------------------------
    # Результаты запроса
    # ---------------------------------------------------------
    def _on_dadata_finished(self, data: Any, task=None) -> None:
        task = self._dadata_task_from(task)
        if not self._is_current_dadata_task(task):
            return
        channel = self._dadata_channel_for(task)
        self._finish_dadata_lookup(task)
        self._apply_dadata_result(channel, data)

    def _on_dadata_not_found(self, task=None) -> None:
        task = self._dadata_task_from(task)
        if not self._is_current_dadata_task(task):
            return
        channel = self._dadata_channel_for(task)
        self._finish_dadata_lookup(task)
        spec = DADATA_CHANNELS[channel]
        logger.info(
            "DaData: ничего не найдено (канал=%s, вкладка %s)",
            channel, self.DADATA_TAB_TITLE,
        )
        QMessageBox.information(self, spec["not_found_title"], spec["not_found"])

    def _on_dadata_error(self, message: str, key_problem: bool,
                         task=None) -> None:
        task = self._dadata_task_from(task)
        if not self._is_current_dadata_task(task):
            return
        self._finish_dadata_lookup(task)
        logger.error(
            "DaData: поиск не выполнен (вкладка %s)", self.DADATA_TAB_TITLE
        )
        if key_problem:
            message += (
                "\n\nКлюч DaData хранится в системном хранилище Windows: "
                "запустите save_dadata_key.bat"
            )
        QMessageBox.critical(self, "Ошибка DaData", message)

    def _apply_dadata_result(self, channel: str, data: Any) -> None:
        """Передаёт результат канала его обработчику на вкладке."""
        name = self.DADATA_APPLIERS.get(channel)
        applier = getattr(self, name, None) if name else None
        if applier is None:
            logger.error(
                "DaData: канал %s не поддержан на вкладке %s",
                channel, self.DADATA_TAB_TITLE,
            )
            return
        applier(data)

    # ---------------------------------------------------------
    # Заполнение формы
    # ---------------------------------------------------------
    def _fill_dadata_fields(
        self,
        values: Dict[str, str],
        empty_only: Tuple[str, ...] = (),
    ) -> Tuple[List[str], List[str], List[str]]:
        """
        Заполняет поля формы значениями из DaData.

        Пустые поля заполняются всегда; уже заполненные — только после
        явного подтверждения пользователя и никогда для empty_only-полей
        (телефон и e-mail, введённые вручную, не трогаем).

        :return: (заполненные, заменённые, пропущенные) поля
        """
        conflicts: List[Tuple[str, str]] = []
        for field, value in values.items():
            if field in empty_only:
                continue
            current = self._dadata_field_text(getattr(self, field, None))
            if current and current != value:
                conflicts.append((field, current))

        overwrite = bool(conflicts) and self._confirm_dadata_overwrite(
            conflicts, values
        )

        filled: List[str] = []
        replaced: List[str] = []
        skipped: List[str] = []

        for field, value in values.items():
            current = self._dadata_field_text(getattr(self, field, None))
            if field in empty_only and current:
                skipped.append(field)
                continue
            if current and not overwrite:
                skipped.append(field)
                continue

            if not current:
                filled.append(field)
            elif current != value:
                replaced.append(field)

            self._apply_dadata_field(field, value)

        return filled, replaced, skipped

    def _confirm_dadata_overwrite(self, conflicts: List[Tuple[str, str]],
                                  values: Dict[str, str]) -> bool:
        """Спрашивает разрешение заменить уже заполненные поля."""
        rows = "\n".join(
            f"• {self._field_title(field)}: сейчас «{_shorten(current)}» → "
            f"станет «{_shorten(values[field])}»"
            for field, current in conflicts
        )
        answer = QMessageBox.question(
            self, "Перезаписать поля?",
            "Эти поля уже заполнены, и данные DaData отличаются:\n\n"
            f"{rows}\n\nПерезаписать их данными DaData?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        logger.info(
            "DaData: подтверждение перезаписи — полей %s, ответ %s",
            len(conflicts), "да" if answer == QMessageBox.Yes else "нет",
        )
        return answer == QMessageBox.Yes

    def _dadata_summary_lines(self, filled: List[str], replaced: List[str],
                              skipped: List[str]) -> List[str]:
        """Строки итогового диалога: что заполнено, заменено и пропущено."""
        lines: List[str] = []

        if filled:
            lines.append(
                "Заполнены пустые поля: " + ", ".join(self._field_titles(filled)) + "."
            )
        if replaced:
            lines.append(
                "Заменены поля: " + ", ".join(self._field_titles(replaced)) + "."
            )
        if skipped:
            lines.append(
                "Оставлены без изменений (там уже были данные): "
                + ", ".join(self._field_titles(skipped)) + "."
            )
        if not filled and not replaced:
            lines.append("Новые данные не заполнены.")
        return lines

    def _show_dadata_summary(self, lines: List[str],
                             warning: str = "") -> None:
        """Итоговый диалог; при предупреждении о статусе — с иконкой Warning."""
        text = "\n".join(lines)
        if warning:
            text = f"{text}\n\n{warning}"
            QMessageBox.warning(self, "Данные из DaData", text)
        else:
            QMessageBox.information(self, "Данные из DaData", text)

    # ---------------------------------------------------------
    # Мелкие помощники
    # ---------------------------------------------------------
    @staticmethod
    def _dadata_field_text(widget) -> str:
        """Текущий текст поля (PasteableLineEdit / PasteableTextEdit)."""
        if widget is None:
            return ""
        if hasattr(widget, "toPlainText"):
            return str(widget.toPlainText() or "").strip()
        if hasattr(widget, "text"):
            return str(widget.text() or "").strip()
        return ""

    def _apply_dadata_field(self, field: str, value: str) -> None:
        """
        Устанавливает значение в поле формы.

        Переопределяется на вкладках для нестандартных полей (например,
        выпадающего списка «Тип перевозчика»).
        """
        widget = getattr(self, field, None)
        if widget is None:
            return
        if hasattr(widget, "setPlainText"):
            widget.setPlainText(value)
        elif hasattr(widget, "setText"):
            widget.setText(value)

    def _field_title(self, field: str) -> str:
        return self.DADATA_FIELD_TITLES.get(field, field)

    def _field_titles(self, fields: List[str]) -> List[str]:
        return [self._field_title(field) for field in fields]


class DadataFillMixin(_DadataChannelMixin):
    """
    Кнопка «🔎» у поля ИНН: реквизиты организации или ИП из DaData.

    Правила (см. задание):
      * запрос уходит ТОЛЬКО по нажатию кнопки;
      * пустые поля заполняются, уже введённые — не перезаписываются
        (перезапись возможна лишь после явного подтверждения);
      * телефон и e-mail заполняются, только если поля пустые;
      * банковские реквизиты этой кнопкой не заполняются (для банка есть
        отдельная кнопка у поля БИК);
      * при статусе организации, отличном от ACTIVE, показывается
        предупреждение (ликвидация, банкротство и т.п.);
      * в лог идут только названия вкладок, длины строк и количества полей.
    """

    DADATA_CHANNEL = "party"

    #: Эти поля заполняются, только если пустые (введённое вручную не трогаем)
    DADATA_EMPTY_ONLY_FIELDS = ("phone", "email")

    def _setup_dadata_fill(self, inn_field: QWidget) -> QWidget:
        """
        Создаёт кнопку «🔎» рядом с полем ИНН.

        :param inn_field: виджет поля ИНН (PasteableLineEdit)
        :return: контейнер «поле + кнопка» для QFormLayout.addRow
        """
        return self._setup_dadata_button("party", inn_field)

    def _on_fill_by_inn_clicked(self) -> None:
        """Проверяет ИНН и запускает поиск (без автозапуска по вводу)."""
        self._on_dadata_button_clicked("party")

    @staticmethod
    def _validate_inn(inn: str) -> str:
        """Текст проблемы с ИНН или пустая строка, если ИНН корректен."""
        if not inn:
            return (
                "Поле ИНН пустое.\n\n"
                "Введите ИНН организации (10 цифр) или индивидуального "
                "предпринимателя (12 цифр)."
            )
        if not re.fullmatch(r"\d{10}|\d{12}", inn):
            return (
                "ИНН должен состоять из 10 цифр (организация) или "
                "12 цифр (ИП).\n\nПроверьте введённый номер."
            )
        return ""

    def _dadata_values(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Соответствие «поле формы → значение из DaData» (пустые — пропуск)."""
        mapping = {
            "full_name": data.get("full_name"),
            "short_name": data.get("short_name"),
            "inn": data.get("inn"),
            "kpp": data.get("kpp"),
            "ogrn": data.get("ogrn"),
            "legal_address": data.get("legal_address"),
            "director_name": data.get("director_name"),
            "director_position": data.get("director_position"),
            "phone": data.get("phone"),
            "email": data.get("email"),
        }

        values: Dict[str, str] = {}
        for field, raw in mapping.items():
            value = str(raw or "").strip()
            if value and hasattr(self, field):
                values[field] = value
        return values

    def _apply_dadata_extra(self, data: Dict[str, Any]) -> List[str]:
        """
        Дополнительные (не текстовые) поля вкладки.

        Переопределяется в «Перевозчике» для типа перевозчика.
        :return: список изменённых полей
        """
        return []

    def _apply_dadata(self, data: Dict[str, Any]) -> None:
        """Заполняет форму, не затирая введённое вручную без подтверждения."""
        values = self._dadata_values(data)

        filled, replaced, skipped = self._fill_dadata_fields(
            values, self.DADATA_EMPTY_ONLY_FIELDS
        )
        filled.extend(self._apply_dadata_extra(data))

        # Только количество полей — без самих значений и без ИНН.
        logger.info(
            "DaData: организация найдена, заполнено полей: %s", len(filled)
        )

        lines = self._dadata_summary_lines(filled, replaced, skipped)
        lines.append("")
        lines.append(
            "Банковские реквизиты: банк и корр. счёт можно заполнить "
            "кнопкой «🔎» у поля БИК, расчётный счёт — только вручную."
        )

        # ── Статус организации: критично для юридической безопасности ──
        warning = status_warning(str(data.get("status") or ""))
        if warning:
            logger.warning(
                "DaData: организация со статусом, отличным от ACTIVE "
                "(вкладка %s)",
                self.DADATA_TAB_TITLE,
            )
        self._show_dadata_summary(lines, warning)


class DadataBankMixin(_DadataChannelMixin):
    """
    Кнопка «🔎» у поля БИК: банк и корреспондентский счёт из DaData.

    Заполняются только bank_name и correspondent_account: расчётный счёт
    DaData не возвращает. Непустые поля перезаписываются лишь после
    подтверждения. При статусе банка, отличном от ACTIVE, показывается
    предупреждение (ликвидация, банкротство и т.п.).
    """

    DADATA_CHANNEL = "bank"

    #: Что забираем из ответа findById/bank
    DADATA_BANK_FIELDS = ("bank_name", "correspondent_account")

    def _setup_dadata_bank_fill(self, bic_field: QWidget) -> QWidget:
        """
        Создаёт кнопку «🔎» рядом с полем БИК.

        :param bic_field: виджет поля БИК (PasteableLineEdit)
        :return: контейнер «поле + кнопка» для QFormLayout.addRow
        """
        return self._setup_dadata_button("bank", bic_field)

    def _on_fill_by_bic_clicked(self) -> None:
        """Проверяет БИК и запускает поиск банка (без автозапуска по вводу)."""
        self._on_dadata_button_clicked("bank")

    @staticmethod
    def _validate_bic(bic: str) -> str:
        """Текст проблемы с БИК или пустая строка, если БИК корректен."""
        if not bic:
            return (
                "Поле БИК пустое.\n\n"
                "Введите БИК банка: 9 цифр (например, 044525225)."
            )
        if not re.fullmatch(r"\d{9}", bic):
            return (
                "БИК должен состоять из 9 цифр.\n\n"
                "Проверьте введённый номер."
            )
        return ""

    def _apply_dadata_bank(self, data: Dict[str, Any]) -> None:
        """Заполняет название банка и корреспондентский счёт."""
        values: Dict[str, str] = {}
        for field in self.DADATA_BANK_FIELDS:
            value = str(data.get(field) or "").strip()
            if value and hasattr(self, field):
                values[field] = value

        filled, replaced, skipped = self._fill_dadata_fields(values)

        # Только количество полей — без БИК и названия банка.
        logger.info("DaData: банк найден, заполнено полей: %s", len(filled))

        lines = self._dadata_summary_lines(filled, replaced, skipped)
        lines.append("")
        lines.append(
            "Расчётный счёт DaData не возвращает — заполните его вручную."
        )

        warning = bank_status_warning(str(data.get("state") or ""))
        if warning:
            logger.warning(
                "DaData: банк со статусом, отличным от ACTIVE (вкладка %s)",
                self.DADATA_TAB_TITLE,
            )
        self._show_dadata_summary(lines, warning)


class DadataDriverMixin(_DadataChannelMixin):
    """
    Кнопка «🔎» у поля «Кем выдан паспорт»: подразделение ФМС из DaData.

    Источник запроса — код подразделения (passport_code), цель — поле
    passport_issuer. Одна подсказка подставляется сразу, несколько —
    пользователь выбирает нужную из списка, ни одной — сообщение
    «не найдено». Значение подсказки берётся из value (data может
    отсутствовать вовсе).
    """

    DADATA_CHANNEL = "fms"

    #: Поле, в которое попадает выбранное подразделение
    DADATA_FMS_TARGET_FIELD = "passport_issuer"

    def _setup_dadata_fms_fill(self, issuer_field: QWidget) -> QWidget:
        """
        Создаёт кнопку «🔎» рядом с полем «Кем выдан паспорт».

        :param issuer_field: виджет поля passport_issuer (PasteableLineEdit)
        :return: контейнер «поле + кнопка» для QFormLayout.addRow
        """
        return self._setup_dadata_button("fms", issuer_field)

    def _on_fill_by_fms_clicked(self) -> None:
        """Проверяет код подразделения и запускает поиск ФМС."""
        self._on_dadata_button_clicked("fms")

    @staticmethod
    def _validate_fms_code(code: str) -> str:
        """Текст проблемы с кодом подразделения или пустая строка."""
        if not code:
            return (
                "Сначала заполните код подразделения "
                "(поле «Код подразделения»).\n\n"
                "Он указан в паспорте: 6 цифр вида 500-123."
            )
        if len(re.sub(r"\D", "", code)) < FMS_CODE_MIN_DIGITS:
            return (
                f"Код подразделения должен содержать {FMS_CODE_MIN_DIGITS} цифр "
                f"(например, 500-123).\n\nПроверьте введённый код."
            )
        return ""

    def _apply_dadata_fms(self, data: Any) -> None:
        """Показывает найденные подразделения и заполняет «Кем выдан»."""
        suggestions = [item for item in (data or []) if isinstance(item, dict)]
        # Часть подсказок ФМС приходит без data, а иногда и без value:
        # работаем только со значением и не падаем на пустых элементах.
        values = [str(item.get("value") or "").strip() for item in suggestions]
        values = [value for value in values if value]

        chosen = ""
        if len(values) == 1:
            # Одна подсказка — подставляем сразу, без лишнего вопроса.
            chosen = values[0]
        elif len(values) > 1:
            chosen, accepted = QInputDialog.getItem(
                self, "Подразделение ФМС",
                "Найдено несколько подразделений — выберите нужное:",
                values, 0, False,
            )
            if not accepted:
                chosen = ""

        logger.info(
            "DaData: подразделение ФМС найдено, подсказок: %s, выбрано: %s",
            len(suggestions), bool(chosen),
        )

        if not chosen:
            if not values:
                spec = DADATA_CHANNELS["fms"]
                QMessageBox.information(
                    self, spec["not_found_title"], spec["not_found"]
                )
            return

        filled, replaced, skipped = self._fill_dadata_fields(
            {self.DADATA_FMS_TARGET_FIELD: chosen}
        )
        lines = self._dadata_summary_lines(filled, replaced, skipped)
        self._show_dadata_summary(lines)


def _shorten(value: str, limit: int = 60) -> str:
    """Обрезает длинное значение для диалога подтверждения."""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"
