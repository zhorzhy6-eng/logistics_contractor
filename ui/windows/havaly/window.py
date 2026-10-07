#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Окно типа «Хавалы» на реальных вкладках (ЭТАП 3.1.E.B.3).

Тип — заявка на перевозку авто в Excel-формате (заказчик «Хавалы»).
Каркас окна (шапка, сайдбар, селектор типа, «Выход», закрытие через hide())
остался общим: он живёт в ui/windows/base_window.py. Здесь — только то, что
у этого типа своё:

  * вкладки: шесть настоящих вкладок из ui/windows/havaly/tabs/ вместо
    заглушек «в разработке» (хук _make_tab);
  * сбор данных: collect_havaly_data(window) → схема промпта
    (ui/windows/havaly/data.py, шаг B.1);
  * генерация: ZayavkaExcelValidator → диалог подтверждения → генератор типа
    из GeneratorFactory → .xlsx в output/ → диалог успеха;
  * распознавание: свой промпт типа (get_prompt("zayavka_excel")), пул потоков
    QThreadPool + QRunnable, разбор ответа через filled_only /
    filled_only_list — пустые поля ответа не стирают ручной ввод;
  * «Очистить форму»: чистит ТОЛЬКО ту вкладку, из которой пришёл сигнал.

ЧЕМ ХАВАЛЫ ОТЛИЧАЮТСЯ ОТ ОСТАЛЬНЫХ ТИПОВ
---------------------------------------
1. РАСКЛАДКА НЕ НУЖНА. Промпт Хавалов отдаёт ровно два блока — ``zayavka``
   (общие сведения, 30 полей) и ``vehicles`` (до 10 перевозимых машин), — и
   имена полей вкладок СОВПАДАЮТ с ключами этого блока (соглашение B.1:
   вкладка отдаёт ``loading_city``, а не ``loading_city_edit``). Поэтому
   окно ничего не переименовывает, как это делает аренда (её промпт отдаёт
   lessee / tractor / driver, а вкладки ждут свои имена): блок ``zayavka``
   разрезается по СПИСКАМ полей вкладок — _ZAYAVKA_FIELDS для вкладки
   «Заявка», _ROUTE_FIELDS для «Маршрута», _DRIVER_FIELDS для «Водителя»,
   _VEHICLE_FIELDS для «ТС», _PRICE_FIELDS для «Стоимости».
2. ДАННЫЕ — СЛОВАРЬ, А НЕ ContractData. Выход типа — .xlsx, и генератор
   (ZayavkaExcelGenerator) читает схему промпта напрямую; ContractData
   корневой блок ``zayavka`` хранить не умеет. Поэтому сборщик B.1 отдаёт
   ``{"zayavka": ..., "vehicles": ..., "contract": ...}``, и окно передаёт
   этот словарь генератору и валидатору как есть — без coerce.
3. ВЫХОДНОЙ ФАЙЛ — XLSX, и его имя зависит ТОЛЬКО от даты заявки
   (``output/Заявка_Хавалы_<дата ISO>.xlsx``). Повторная генерация за ту же
   дату перезаписывает файл — это нормальное поведение типа, а не потеря:
   документ собирается заново из формы.

Две особенности, которые легко потерять при правках:

  * клиент GigaChat создаётся лениво (в _ensure_gigachat при первом
    нажатии) и свой: чужой экземпляр переиспользовать нельзя, а держать
    клиента в __init__ — значит требовать ключ при простом открытии окна;
  * в connect нет lambda, захватывающих вкладку или окно: цикл ссылок
    Python ↔ Qt роняет процесс при выходе. Задачу распознавания слоты
    находят через sender() (см. _task_of_sender).

В логи попадают только имена полей, количества и длины: ФИО, VIN, номера,
адреса, телефоны и суммы не пишутся.
"""

import logging
import os
from functools import partial
from typing import Any, Dict, List, Mapping, Optional

from PyQt5.QtCore import QObject, QRunnable, QSize, QThreadPool, pyqtSignal
from PyQt5.QtWidgets import QMainWindow, QMessageBox, QWidget

from core.contracts.factory import GeneratorFactory
from core.contracts.registry import ContractTypeRegistry
from core.contracts.zayavka.validator import ZayavkaExcelValidator
from core.gigachat_client import GigaChatClient
from core.prompts import get_prompt
from core.recognizer import filled_only, filled_only_list, has_content
from core.secrets_store import MISSING_KEY_MESSAGE
from core.settings_service import get_settings_service
from core.trace import filled_fields_summary
from core.validator import ValidationReport

from ui import theme
from ui.icons import action_icon
from ui.windows.base_window import BaseContractWindow
from ui.windows.havaly import data as havaly_data
from ui.windows.havaly.tabs import (
    CargoTab, CustomerTab, DriverTab, PriceTab, RouteTab, VehicleTab,
)

logger = logging.getLogger("ui.windows.havaly.window")


class RecognitionSignals(QObject):
    """Сигналы задачи распознавания (по образцу MainWindow)."""

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()
    progress = pyqtSignal(int, str)   # процент, текст статуса


class RecognitionTask(QRunnable):
    """
    Задача распознавания для QThreadPool (по образцу MainWindow).

    Своя копия, а не импорт из ui/main_window.py: окно типа не должно
    зависеть от окна «Экспедиторство» — иначе правка одного типа ломает
    другой. Отличие от версии MainWindow одно: промпт типа передаётся
    вторым аргументом recognize_text(text, prompt=...).
    """

    def __init__(self, client: GigaChatClient, text: str,
                 prompt: Optional[str] = None):
        super().__init__()
        self.client = client
        self.text = text
        self.prompt = prompt
        self.signals = RecognitionSignals()
        self._cancelled = False

    # ── Отмена ──
    def cancel(self) -> None:
        """Помечает задачу отменённой: её результат не будет применён."""
        self._cancelled = True
        logger.info("Хавалы: задача распознавания помечена как отменённая")

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:
        """Запрос к GigaChat в потоке пула; результат уходит сигналами."""
        try:
            self.signals.progress.emit(5, "Подготовка текста...")

            if self._cancelled:
                self.signals.cancelled.emit()
                return

            # В лог — только длина текста: сам текст заявки содержит ПДн.
            self.signals.progress.emit(
                20, f"Отправка в GigaChat ({len(self.text)} символов)..."
            )
            data = self.client.recognize_text(self.text, prompt=self.prompt)

            if self._cancelled:
                logger.info(
                    "Хавалы: распознавание отменено — результат не применяется"
                )
                self.signals.cancelled.emit()
                return

            self.signals.progress.emit(90, "Разбор ответа модели...")

            if data is None:
                self.signals.error.emit("Модель вернула пустой ответ")
                return

            self.signals.progress.emit(100, "Распознавание завершено")
            self.signals.finished.emit(data)

        except Exception as e:  # noqa: BLE001 — поток не должен падать молча
            if self._cancelled:
                self.signals.cancelled.emit()
                return
            logger.exception("Хавалы: ошибка в задаче распознавания")
            self.signals.error.emit(str(e))


class HavalyWindow(BaseContractWindow):
    """Окно типа «Хавалы»: шесть рабочих вкладок, генерация и распознавание."""

    CONTRACT_TYPE = "zayavka_excel"
    WINDOW_TITLE = "Хавалы"
    TAB_CONFIGS = [
        ("Заявка", "contract.svg"),
        ("Груз", "contract.svg"),
        ("Маршрут", "trailer.svg"),
        ("Водитель", "driver.svg"),
        ("ТС", "vehicles.svg"),
        ("Стоимость", "contract.svg"),
    ]

    #: Заголовок вкладки из TAB_CONFIGS → класс настоящей вкладки.
    #: Заголовки — ключ связи с каркасом: _make_tab получает именно их.
    _TAB_BY_TITLE: Dict[str, type] = {
        "Заявка": CustomerTab,
        "Груз": CargoTab,
        "Маршрут": RouteTab,
        "Водитель": DriverTab,
        "ТС": VehicleTab,
        "Стоимость": PriceTab,
    }

    #: Разделы ответа модели верхнего уровня: (ключ, подпись для лога).
    #: Схема Хавалов короткая — блок общих сведений и массив машин; вкладок
    #: шесть, поэтому разбор полей блока ``zayavka`` идёт по спискам ниже,
    #: а не по разделам ответа.
    _RECOGNITION_SECTIONS = (
        ("zayavka", "общие сведения заявки"),
        ("vehicles", "перевозимые машины"),
    )

    #: Поля блока «zayavka» по вкладкам — имена ключей СОВПАДАЮТ с именами
    #: полей вкладок (соглашение B.1), поэтому карт перевода нет: список и
    #: есть раскладка. Поля заявки, которые могут прийти из двух вкладок
    #: (дата заявки, номер лота, автовоз и прицеп), разбирает сборщик B.1
    #: (data._SHARED_ZAYAVKA_FIELDS) — окну об этом думать не нужно.
    _ZAYAVKA_FIELDS = (
        "date", "lot_number", "customer_name", "carrier_name",
    )
    _ROUTE_FIELDS = (
        "loading_city", "loading_point", "unloading_city", "unloading_point",
        "loading_plan_date", "loading_plan_time",
    )
    _DRIVER_FIELDS = (
        "driver_last_name", "driver_first_name", "driver_middle_name",
        "driver_license_number", "driver_license_issue_date",
        "driver_passport_series", "driver_passport_number",
        "driver_passport_issuer", "driver_passport_issue_date",
        "driver_citizenship", "driver_birth_date", "driver_registration",
        "driver_phone",
    )
    _VEHICLE_FIELDS = (
        "tractor_brand", "tractor_color", "tractor_plate",
        "trailer_brand", "trailer_plate",
    )

    #: Поля вкладки «Стоимость»: ставка с НДС и ставка НДС. Ключи чужих схем
    #: (amount_with_vat / sum_total / price) вкладка тоже понимает, но из
    #: ответа Хавалов они не приходят, и подмешивать их в данные незачем.
    _PRICE_FIELDS = ("price_with_vat", "vat_rate")

    #: Ключи, под которыми машины могут лежать в ответе модели: в схеме
    #: Хавалов это массив верхнего уровня ``vehicles``; блок ``cargo``
    #: принимается как запасной вариант чужой раскладки.
    _CARGO_KEYS = ("vehicles", "cargo")

    def __init__(self, parent: Optional[QMainWindow] = None):
        super().__init__(parent)

        # Реестр типов: без него GeneratorFactory не знает «zayavka_excel».
        self._ensure_type_registered()

        # Клиент GigaChat — свой и ленивый: в __init__ он НЕ создаётся,
        # иначе окно требовало бы ключ при каждом открытии. QThreadPool и
        # прочие QObject-атрибуты создаются только ПОСЛЕ super().__init__:
        # до него базовый QMainWindow ещё не инициализирован.
        self.gigachat: Optional[GigaChatClient] = None

        # Пул потоков распознавания. Живёт в окне: пул ждёт свои активные
        # задачи сам, поэтому результат не приходит в удалённый объект.
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(2)
        self.recognition_task: Optional[RecognitionTask] = None

        # Кнопки на вкладках уже подключены — сигналами заведует
        # _build_tab_actions вкладок; здесь остаётся шапка и ссылки на
        # вкладки по именам.
        self._build_header_actions()
        self._bind_tabs()

        logger.info(
            "Хавалы: окно собрано на реальных вкладках (%s)",
            ", ".join(type(tab).__name__ for tab in self._tabs()),
        )

    # ---------------------------------------------------------
    # Построение интерфейса
    # ---------------------------------------------------------
    def _make_tab(self, title: str, icon_key: str) -> QWidget:
        """
        Фабрика вкладки Хавалов (переопределение хука каркаса).

        Заголовок, которого нет в _TAB_BY_TITLE, отдаётся каркасу: окно
        не должно оставаться без страницы из-за опечатки в TAB_CONFIGS.
        """
        tab_class = self._TAB_BY_TITLE.get(title)
        if tab_class is None:
            logger.error(
                "Хавалы: неизвестный заголовок вкладки %r — взята заглушка",
                title,
            )
            return self._make_placeholder_tab(title)
        return tab_class()

    def _build_header_actions(self) -> None:
        """
        Кнопка «Создать договор» в шапке — как в MainWindow.

        Каркас окна шапку не меняет (её вид общий для всех типов), поэтому
        кнопка добавляется здесь и встаёт перед «Выход». Действие то же,
        что у кнопок на вкладках: файл один, данные собираются со всех
        вкладок сразу.
        """
        header = self.btn_exit.parentWidget().layout()
        self.btn_create_contract = theme.accent_button(
            "Создать договор",
            tooltip="Проверить данные и сформировать заявку XLSX",
        )
        self.btn_create_contract.setIcon(action_icon("contract.svg"))
        self.btn_create_contract.setIconSize(QSize(18, 18))
        # Слот — метод окна, без lambda: замыкание на окно даёт цикл
        # ссылок Python ↔ Qt и роняет процесс при завершении.
        self.btn_create_contract.clicked.connect(self._on_create_contract)
        header.insertWidget(header.indexOf(self.btn_exit), self.btn_create_contract)

    @staticmethod
    def _ensure_type_registered() -> None:
        """
        Прогревает реестр типов: без него GeneratorFactory не знает
        «zayavka_excel».

        В приложении реестр наполняет main.py (_load_contract_types), но окно
        типа не должно зависеть от того, кто его создал: тест, менеджер окон
        или точка входа. Загрузка идемпотентна — модули типов импортируются
        один раз, повторный вызов только проверяет наличие ключа.
        """
        key = HavalyWindow.CONTRACT_TYPE
        if ContractTypeRegistry.find(key) is not None:
            return
        ContractTypeRegistry.load_builtin()
        if ContractTypeRegistry.find(key) is None:
            logger.error(
                "Хавалы: тип %r не зарегистрирован — генерация будет "
                "выполнена генератором по умолчанию", key,
            )

    def _bind_tabs(self) -> None:
        """
        Ссылки на вкладки по именам и подключение их сигналов.

        Имена (zayavka_tab, cargo_tab, route_tab, driver_tab, vehicle_tab,
        price_tab) — это ровно те атрибуты, по которым сборщик B.1 ищет
        вкладки (data.SECTION_TITLES): другого способа связать окно со
        сборщиком нет, и переименование здесь молча оставило бы разделы
        пустыми. Сигналы подключаются к методам окна — тем же, что и кнопка
        в шапке, поэтому «Создать договор» с вкладки и из шапки даёт один
        и тот же результат.
        """
        self.zayavka_tab = self.tabs.widget(0)
        self.cargo_tab = self.tabs.widget(1)
        self.route_tab = self.tabs.widget(2)
        self.driver_tab = self.tabs.widget(3)
        self.vehicle_tab = self.tabs.widget(4)
        self.price_tab = self.tabs.widget(5)

        for tab in self._tabs():
            tab.create_contract_requested.connect(self._on_create_contract)
            tab.clear_requested.connect(self._on_clear_tab)
            tab.recognize_requested.connect(self._on_recognize_requested)

    # ---------------------------------------------------------
    # Сбор данных
    # ---------------------------------------------------------
    def _collect_data(self) -> Dict[str, Any]:
        """
        Собирает данные заявки со всех шести вкладок — схему промпта.

        Возвращается словарь ``{"zayavka": ..., "vehicles": ...,
        "contract": ...}``, а НЕ ContractData: генератор .xlsx читает схему
        напрямую, а ContractData корневой блок ``zayavka`` не хранит
        (ui/windows/havaly/data.py, шаг B.1). Раскладка полей живёт в
        сборщике и проверяется отдельно; окно только передаёт себя.

        Метод ничего не меняет в интерфейсе, поэтому вызывается многократно.
        """
        payload = havaly_data.collect_havaly_data(self)
        logger.info(
            "Хавалы: данные собраны — полей заявки=%s, машин=%s",
            len(payload.get("zayavka") or {}),
            len(payload.get("vehicles") or []),
        )
        return payload

    # ---------------------------------------------------------
    # Генерация заявки
    # ---------------------------------------------------------
    def _confirm_validation(self, report: ValidationReport) -> bool:
        """
        Показывает результат проверки данных (по образцу MainWindow).

        У Хавалов ошибок почти не бывает: валидатор типа сообщает
        замечаниями то, что стоит дозаполнить (ставка, маршрут, водитель),
        а печатать заявку это не мешает.

        :return: True, если можно продолжать: замечаний нет — сразу, иначе
            только после явного «Создать заявку» в диалоге.
        """
        if report.is_clean:
            logger.info("Хавалы: проверка данных пройдена без замечаний")
            return True

        logger.warning(
            "Хавалы: проверка данных — ошибок=%s, замечаний=%s",
            len(report.errors), len(report.warnings),
        )

        box = QMessageBox(self)
        box.setWindowTitle(
            "Проверьте данные" if report.has_errors else "Замечания к данным"
        )
        box.setIcon(QMessageBox.Critical if report.has_errors else QMessageBox.Warning)
        box.setText(
            "В данных есть ошибки. Заявку можно создать, но проверьте сведения."
            if report.has_errors
            else "В данных есть замечания. Поля, отмеченные ниже, останутся пустыми "
                 "(у денежных полей будет 0.00, у текстовых — пусто)."
        )
        box.setInformativeText(report.format_text())
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)

        yes_button = box.button(QMessageBox.Yes)
        no_button = box.button(QMessageBox.No)
        if yes_button is not None:
            yes_button.setText("Создать заявку")
        if no_button is not None:
            no_button.setText("Исправить")

        return box.exec_() == QMessageBox.Yes

    def _on_create_contract(self) -> None:
        """«Создать договор»: валидатор → диалог → генератор → диалог успеха."""
        logger.info("Хавалы: нажата кнопка «Создать договор»")
        try:
            payload = self._collect_data()

            report = ZayavkaExcelValidator().check(payload)
            if not self._confirm_validation(report):
                logger.info(
                    "Хавалы: создание заявки отменено пользователем после проверки"
                )
                self.statusBar().showMessage(
                    "Заявка не создана — исправьте данные", 5000
                )
                return

            generator = GeneratorFactory.get_generator(self.CONTRACT_TYPE)
            path = generator.generate(payload)
            logger.info("Хавалы: заявка создана (%s)", os.path.basename(path))
            self._show_contract_created(path)
        except Exception as e:  # noqa: BLE001 — окно не должно падать целиком
            logger.exception("Хавалы: ошибка при создании заявки")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось создать заявку:\n{e}"
            )

    def _show_contract_created(self, path: str) -> None:
        """
        Сообщает о созданной заявке и предлагает открыть папку.

        Кнопки те же, что в MainWindow: «Открыть папку» + «OK».
        """
        folder = os.path.dirname(os.path.abspath(path))

        box = QMessageBox(self)
        box.setWindowTitle("Успех")
        box.setIcon(QMessageBox.Information)
        box.setText("Заявка создана")
        box.setInformativeText(f"{os.path.basename(path)}\n\nПапка: {folder}")

        open_button = box.addButton("Открыть папку", QMessageBox.ActionRole)
        box.addButton("OK", QMessageBox.AcceptRole)

        box.exec_()

        if box.clickedButton() is open_button:
            logger.info("Хавалы: диалог создания заявки — «Открыть папку»")
            self._open_folder(folder)

    def _open_folder(self, folder: str) -> bool:
        """Открывает папку с готовой заявкой в системном файловом менеджере."""
        if not folder or not os.path.isdir(folder):
            logger.warning("Хавалы: папка для открытия не найдена")
            return False

        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtGui import QDesktopServices

            if QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
                logger.info("Хавалы: папка с заявкой открыта")
                return True
            logger.warning("Хавалы: QDesktopServices не смог открыть папку")
        except Exception as e:  # noqa: BLE001 — открытие папки не критично
            logger.warning("Хавалы: ошибка открытия папки (%s)", type(e).__name__)

        try:
            os.startfile(folder)  # type: ignore[attr-defined]
            logger.info("Хавалы: папка открыта через os.startfile")
            return True
        except Exception as e:  # noqa: BLE001 — последний рубеж
            logger.error("Хавалы: не удалось открыть папку (%s)", type(e).__name__)
            return False

    # ---------------------------------------------------------
    # Очистка вкладки
    # ---------------------------------------------------------
    def _on_clear_tab(self) -> None:
        """
        «Очистить форму»: чистит ТОЛЬКО вкладку-отправителя.

        Вкладка берётся у sender() (или по иерархии виджетов — если сигнал
        придёт от кнопки), а не из замыкания: lambda, захватывающая вкладку,
        создаёт цикл ссылок Python ↔ Qt и роняет процесс при выходе.
        """
        tab = self._sender_tab()
        if tab is None:
            logger.warning("Хавалы: запрос очистки без отправителя — пропущен")
            return

        logger.info("Хавалы: очистка вкладки %s", type(tab).__name__)
        tab.clear()
        self.statusBar().showMessage("Вкладка очищена", 3000)

    # ---------------------------------------------------------
    # Распознавание
    # ---------------------------------------------------------
    def _ensure_gigachat(self) -> Optional[GigaChatClient]:
        """
        Ленивая инициализация клиента GigaChat.

        Клиент создаётся при первом обращении и переиспользуется дальше.
        Свой, а не родительского окна: у каждого типа свой промпт и свои
        настройки, а общий экземпляр связал бы окна между собой.

        :return: клиент или None, если ключ недоступен (сообщение покажет
            вызывающий код — MISSING_KEY_MESSAGE).
        """
        if self.gigachat is not None:
            return self.gigachat

        from core import secrets_store

        auth_key = (secrets_store.get_gigachat_key() or "").strip()
        if not auth_key:
            logger.error("Хавалы: %s", MISSING_KEY_MESSAGE)
            return None

        try:
            auth_key.encode("ascii")
        except UnicodeEncodeError as e:
            logger.error(
                "Хавалы: ключ GigaChat содержит не-ASCII символ (позиция %s)",
                e.start,
            )
            QMessageBox.critical(
                self, "Ошибка GigaChat",
                f"Ключ GigaChat содержит не-ASCII символ (позиция {e.start}).\n\n"
                f"HTTP-заголовок Authorization допускает только latin-1.",
            )
            return None

        settings = get_settings_service()
        try:
            self.gigachat = GigaChatClient(
                auth_key=auth_key,
                scope=settings.get_str("gigachat_scope", "GIGACHAT_API_PERS"),
                model=settings.get_str("gigachat_model", "GigaChat-2"),
                timeout=settings.get_int("gigachat_timeout", 150),
                verify_ssl=settings.get_bool("gigachat_verify_ssl", True),
                ca_bundle=settings.get_str("gigachat_ca_bundle", "") or None,
            )
        except Exception as e:  # noqa: BLE001 — ключ/сеть: показываем и живём дальше
            logger.exception("Хавалы: не удалось создать клиент GigaChat")
            QMessageBox.critical(
                self, "Ошибка GigaChat",
                f"Не удалось инициализировать GigaChat:\n{e}",
            )
            return None

        logger.info("Хавалы: клиент GigaChat создан (ленивая инициализация)")
        return self.gigachat

    def _on_recognize_requested(self, text: str) -> None:
        """Запрос распознавания со вкладки: проверки и старт задачи."""
        logger.info("Хавалы: запрос распознавания (%s символов)", len(text or ""))

        if not text or not text.strip():
            QMessageBox.warning(
                self, "Внимание",
                "На вкладке нет текста для распознавания.\n\n"
                "Вставьте данные в поле панели распознавания и нажмите "
                "«Распознать вкладку».",
            )
            return

        client = self._ensure_gigachat()
        if client is None:
            QMessageBox.critical(
                self, "Ошибка GigaChat",
                f"{MISSING_KEY_MESSAGE}\n\n"
                "Ключ хранится в системном хранилище Windows "
                "(см. core/secrets_store.py), а не в файлах проекта.",
            )
            return

        self._start_recognition(client, text)

    def _start_recognition(self, client: GigaChatClient, text: str) -> None:
        """
        Запускает распознавание в пуле потоков.

        Промпт берётся у типа (get_prompt("zayavka_excel")) и передаётся
        вторым аргументом: у Хавалов своя схема ответа (блок ``zayavka`` и
        массив ``vehicles``), дефолтный системный промпт клиента для неё
        не подходит.
        """
        prompt = get_prompt(self.CONTRACT_TYPE)
        logger.info(
            "Хавалы: старт распознавания (символов=%s, свой промпт=%s)",
            len(text), prompt is not None,
        )
        self.statusBar().showMessage(
            f"Отправка в GigaChat ({len(text)} символов)..."
        )

        task = RecognitionTask(client, text, prompt=prompt)
        self.recognition_task = task

        # Сигналы привязываем к конкретной задаче через functools.partial:
        # результат «старой» (отменённой) задачи не должен влиять на новую.
        # lambda здесь не годится — замыкание на окно даёт цикл ссылок
        # Python ↔ Qt и роняет процесс при завершении; partial же держит
        # только саму задачу, а она живёт до конца распознавания.
        task.signals.finished.connect(partial(self._on_recognition_finished, task=task))
        task.signals.error.connect(partial(self._on_recognition_error, task=task))
        task.signals.cancelled.connect(partial(self._on_recognition_cancelled, task=task))
        task.signals.progress.connect(self._on_recognition_progress)

        self.thread_pool.start(task)
        logger.info(
            "Хавалы: задача распознавания запущена в пуле (активных потоков: %s)",
            self.thread_pool.activeThreadCount(),
        )

    # ── Задача-отправитель ──
    def _task_of_sender(self) -> Optional[RecognitionTask]:
        """
        Задача распознавания, чьи сигналы пришли в слот.

        Рабочий способ передать задачу — именованный аргумент task (см.
        _start_recognition): он виден в подписи слота. sender() остаётся
        запасным вариантом для вызова из Qt: он отдаёт RecognitionSignals
        задачи, по которым задача и находится.
        """
        signals = self.sender()
        if signals is None:
            return None
        return next(
            (task for task in self._recognition_tasks() if task.signals is signals),
            None,
        )

    def _task_from(self, task: Optional[RecognitionTask]) -> Optional[RecognitionTask]:
        """Задача из аргумента слота; без него — по отправителю сигнала."""
        return task if task is not None else self._task_of_sender()

    def _recognition_tasks(self) -> List[RecognitionTask]:
        """Незавершённые задачи распознавания окна (для поиска по sender())."""
        return [self.recognition_task] if self.recognition_task is not None else []

    def _is_current_task(self, task: Optional[RecognitionTask]) -> bool:
        """Задача актуальна? Результат устаревшей задачи не применяется."""
        return task is None or task is self.recognition_task

    def _finish_recognition_ui(self) -> None:
        """Снимает состояние «идёт распознавание»."""
        self.recognition_task = None
        self.statusBar().showMessage("Готово")

    def _on_recognition_progress(self, percent: int, message: str) -> None:
        """Показывает ход распознавания в статус-баре."""
        self.statusBar().showMessage(f"{message} ({percent}%)")

    def _on_recognition_cancelled(self, task: Optional[RecognitionTask] = None) -> None:
        """Задача отменена: интерфейс освобождаем без диалогов."""
        if not self._is_current_task(self._task_from(task)):
            logger.info("Хавалы: отмена устаревшей задачи проигнорирована")
            return
        logger.info("Хавалы: распознавание отменено")
        self._finish_recognition_ui()

    def _log_recognition_result(self, data: Dict[str, Any]) -> None:
        """
        Пишет в debug.log, что распознано, а что нет.

        Схема Хавалов короткая: сведения заявки лежат ОДНИМ блоком, поэтому
        по разделам видно только «есть блок или нет». Полезное — во второй
        строке: имена заполненных полей заявки и число машин. Имена полей и
        количества пишутся, значения (ФИО, VIN, номера, адреса, телефоны,
        суммы) — никогда.
        """
        block = filled_only(data.get("zayavka"))
        vehicles = self._cargo_tab_data(self._cargo_source(data)).get("vehicles", [])

        recognized, missing = [], []
        for key, title in self._RECOGNITION_SECTIONS:
            value = data.get(key)

            # Список (машины) считается по количеству записей, блок — по
            # заполненным полям: пустой раздел в «получено» не попадает.
            if isinstance(value, (list, tuple)):
                if vehicles:
                    recognized.append(f"{title}: {len(vehicles)} записей")
                else:
                    missing.append(title)
                continue

            if has_content(value):
                recognized.append(f"{title}: {filled_fields_summary(value)}")
            else:
                missing.append(title)

        # Поля заявки лежат одним блоком, поэтому к разделам добавляется
        # разбивка по именам: так в логе видно, что именно распозналось.
        fields = sorted(block)
        logger.debug(
            "Хавалы, распознавание: разделы получены — "
            + ("; ".join(recognized) if recognized else "нет")
        )
        logger.debug(
            "Хавалы, распознавание: НЕ распознано — "
            + (", ".join(missing) if missing else "нет")
        )
        logger.debug(
            "Хавалы, распознавание: поля заявки (%s): %s",
            len(fields), ", ".join(fields) if fields else "нет",
        )
        logger.debug(
            "Хавалы, распознавание: все ключи ответа: %s", sorted(data.keys())
        )

    # ── Раскладка ответа по вкладкам ──
    # Каждый метод собирает словарь с именами полей СВОЕЙ вкладки и ничего
    # не пишет в интерфейс: так раскладку видно тестом без окна. Ключи
    # вкладок совпадают с ключами схемы промпта, поэтому методы не
    # переименовывают поля, а отбирают свои.
    @classmethod
    def _fields_of(cls, fields: Any, block: Any) -> Dict[str, Any]:
        """
        Поля вкладки из заполненного блока (порядок — как в списке полей).

        Пустые значения отбрасываются: распознавание возвращает схему
        целиком, и пустая строка не должна стирать введённое вручную
        (filled_only). Числовой ноль остаётся: это значение, а не пустота.
        """
        filled = filled_only(block)
        return {field: filled[field] for field in fields if field in filled}

    @classmethod
    def _zayavka_tab_data(cls, zayavka: Any) -> Dict[str, Any]:
        """Блок «zayavka» → поля вкладки «Заявка» (дата, лот, стороны)."""
        return cls._fields_of(cls._ZAYAVKA_FIELDS, zayavka)

    @classmethod
    def _route_tab_data(cls, zayavka: Any) -> Dict[str, Any]:
        """Блок «zayavka» → поля вкладки «Маршрут» (места и план погрузки)."""
        return cls._fields_of(cls._ROUTE_FIELDS, zayavka)

    @classmethod
    def _driver_tab_data(cls, zayavka: Any) -> Dict[str, Any]:
        """
        Блок «zayavka» → поля вкладки «Водитель».

        Тринадцать плоских полей: отдельного блока ``driver`` в схеме
        Хавалов нет, ФИО и паспорт лежат раздельными ключами (driver_last_name,
        driver_passport_series) — раскладывать их по частям не нужно, имена
        уже совпадают с полями вкладки.
        """
        return cls._fields_of(cls._DRIVER_FIELDS, zayavka)

    @classmethod
    def _vehicle_tab_data(cls, zayavka: Any) -> Dict[str, Any]:
        """
        Блок «zayavka» → поля вкладки «ТС» (автовоз и прицеп).

        Отдельных блоков ``tractor`` и ``trailer`` у Хавалов нет: пять
        полей автовоза и прицепа лежат плоскими ключами блока ``zayavka``,
        поэтому раскладка та же, что у остальных вкладок.
        """
        return cls._fields_of(cls._VEHICLE_FIELDS, zayavka)

    @classmethod
    def _price_tab_data(cls, zayavka: Any) -> Dict[str, Any]:
        """
        Блок «zayavka» → поля вкладки «Стоимость».

        Ставка с НДС передаётся как есть, включая 0.0: вкладка на ноль
        ничего не меняет (у промпта 0.0 значит «ставки не было»), а пустое
        значение в словарь не попадает вовсе. Ставка НДС в бланк не
        печатается, но вкладка её показывает — ключ остаётся.
        """
        return cls._fields_of(cls._PRICE_FIELDS, zayavka)

    @classmethod
    def _cargo_tab_data(cls, source: Any) -> Dict[str, Any]:
        """
        Машины ответа модели → таблица вкладки «Груз».

        Машины приходят массивом верхнего уровня (схема промпта Хавалов), но
        принимается и вложенный блок ``cargo`` с ключом ``vehicles`` — так их
        отдаёт сборщик B.1. Пустые записи ([{"vin": ""}]) отбрасываются:
        распознавание, не нашедшее машин, не должно стирать введённые вручную.
        """
        if isinstance(source, Mapping):
            rows = filled_only_list(source.get("vehicles"))
        else:
            rows = filled_only_list(source)
        return {"vehicles": rows} if rows else {}

    # ── Заполнение вкладок ──
    def _fill_zayavka(self, zayavka: Any) -> None:
        """Заявка: дата и номер лота (стороны на вкладке фиксированы)."""
        payload = self._zayavka_tab_data(zayavka)
        if not payload:
            logger.info("Хавалы: заявка не распознана — оставляем как есть")
            return
        self.zayavka_tab.fill_data(payload)

    def _fill_cargo(self, source: Any) -> None:
        """Перевозимые машины: пустой список ручной ввод не стирает."""
        payload = self._cargo_tab_data(source)
        if not payload:
            logger.info("Хавалы: машин в ответе нет — таблица как есть")
            return
        self.cargo_tab.fill_data(payload)
        logger.info(
            "Хавалы: таблица машин заполнена (%s записей)",
            len(payload["vehicles"]),
        )

    def _fill_route(self, zayavka: Any) -> None:
        """Маршрут: места погрузки и разгрузки, план погрузки."""
        payload = self._route_tab_data(zayavka)
        if not payload:
            logger.info("Хавалы: маршрут не распознан — оставляем как есть")
            return
        self.route_tab.fill_data(payload)

    def _fill_driver(self, zayavka: Any) -> None:
        """Водитель: ФИО, документы, гражданство, прописка и телефон."""
        payload = self._driver_tab_data(zayavka)
        if not payload:
            logger.info("Хавалы: водитель не распознан — оставляем как есть")
            return
        self.driver_tab.fill_data(payload)

    def _fill_vehicle(self, zayavka: Any) -> None:
        """ТС: автовоз и прицеп."""
        payload = self._vehicle_tab_data(zayavka)
        if not payload:
            logger.info("Хавалы: ТС не распознаны — оставляем как есть")
            return
        self.vehicle_tab.fill_data(payload)

    def _fill_price(self, zayavka: Any) -> None:
        """Стоимость: ставка с НДС и ставка НДС."""
        payload = self._price_tab_data(zayavka)
        if not payload:
            logger.info("Хавалы: стоимость не распознана — оставляем как есть")
            return
        self.price_tab.fill_data(payload)

    @classmethod
    def _cargo_source(cls, data: Mapping[str, Any]) -> Any:
        """
        Машины из ответа модели: массив верхнего уровня или блок ``cargo``.

        Первый непустой источник побеждает; пустой верхний массив не должен
        затирать заполненный блок чужой раскладки. Метод только читает, без
        интерфейса, — поэтому его видно тестом без окна.
        """
        for key in cls._CARGO_KEYS:
            value = data.get(key)
            rows = value.get("vehicles") if isinstance(value, Mapping) else value
            if filled_only_list(rows):
                return value
        return data.get("vehicles")

    def _on_recognition_finished(
        self, data: Dict[str, Any], task: Optional[RecognitionTask] = None
    ) -> None:
        """
        Раскладывает распознанные данные по вкладкам.

        Пустые поля ответа не должны стирать ручной ввод, поэтому каждая
        вкладка получает только заполненные значения: блок заявки проходит
        через filled_only, машины — через filled_only_list (см. _*_tab_data).

        :param task: задача, чей сигнал пришёл (см. _task_from).
        """
        task = self._task_from(task)
        if not self._is_current_task(task):
            logger.info("Хавалы: результат устаревшей задачи проигнорирован")
            return

        answer = data if isinstance(data, Mapping) else {}
        logger.info(
            "Хавалы: распознавание завершено, разделы: %s", sorted(answer.keys())
        )
        self._log_recognition_result(answer)

        try:
            zayavka = answer.get("zayavka")
            self._fill_zayavka(zayavka)
            self._fill_cargo(self._cargo_source(answer))
            self._fill_route(zayavka)
            self._fill_driver(zayavka)
            self._fill_vehicle(zayavka)
            self._fill_price(zayavka)

            self._log_ui_action(
                "распознавание: ответ разложен по вкладкам",
                sections=len(answer),
                tabs=len(self.TAB_CONFIGS),
            )
            self.statusBar().showMessage("Распознавание завершено", 5000)
            QMessageBox.information(
                self, "Успех", "Данные успешно распознаны и заполнены!"
            )
        except Exception as e:  # noqa: BLE001 — вкладка не должна ронять окно
            logger.exception("Хавалы: ошибка при заполнении вкладок")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось заполнить вкладки:\n{e}"
            )
        finally:
            self._finish_recognition_ui()

    def _on_recognition_error(
        self, error_msg: str, task: Optional[RecognitionTask] = None
    ) -> None:
        """Ошибка распознавания: сообщение пользователю и сброс состояния."""
        task = self._task_from(task)
        if not self._is_current_task(task):
            logger.info("Хавалы: ошибка устаревшей задачи проигнорирована")
            return

        logger.error("Хавалы: ошибка распознавания (%s)", type(error_msg).__name__)
        self._finish_recognition_ui()
        QMessageBox.critical(
            self, "Ошибка распознавания",
            f"Не удалось распознать данные:\n\n{error_msg}",
        )

    # ---------------------------------------------------------
    # Логирование действий пользователя
    # ---------------------------------------------------------
    def _log_ui_action(self, action: str, **details: Any) -> None:
        """
        Единая точка логирования действий пользователя.

        Пишутся только служебные сведения: имена полей, количества,
        заголовки вкладок. Значения данных (ФИО, VIN, адреса, номера,
        телефоны, суммы) в лог не попадают.
        """
        tail = "".join(f" | {key}={value}" for key, value in details.items())
        logger.info("Хавалы, UI: %s%s", action, tail)


__all__ = ["HavalyWindow", "RecognitionTask", "RecognitionSignals"]
