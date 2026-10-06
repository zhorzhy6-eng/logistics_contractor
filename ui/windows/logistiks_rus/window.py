#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Окно типа «Логистикс Рус» на реальных вкладках (ЭТАП 3.1.C.B.3).

«Логистикс Рус» — заявка (Приложение № 1) к генеральному договору
транспортной экспедиции. Каркас окна (шапка, сайдбар, селектор типа,
«Выход», закрытие через hide()) остался общим: он живёт в
ui/windows/base_window.py. Здесь — только то, что у этого типа своё:

  * вкладки: шесть настоящих вкладок из ui/windows/logistiks_rus/tabs/
    вместо заглушек «в разработке» (хук _make_tab);
  * сбор данных: data.build(...) → ContractData
    (ui/windows/logistiks_rus/data.py);
  * генерация: LogistiksRusValidator → диалог подтверждения → генератор
    типа из GeneratorFactory → диалог успеха;
  * распознавание: свой промпт типа (get_prompt("logistiks_rus")), пул
    потоков QThreadPool + QRunnable, разбор ответа через filled_only /
    filled_only_list — пустые поля ответа не стирают ручной ввод;
  * «Очистить форму»: чистит ТОЛЬКО ту вкладку, из которой пришёл сигнал.

Раскладка ответа модели — своя у каждого типа. Промпт Логистикс Рус
(core/prompts/logistiks_rus.py) отдаёт блоки customer / shipper_name /
loading_addresses / consignees / vehicles / tractor / trailer / driver /
contract, а вкладки ждут СВОИ имена полей (см. docstring
ui/windows/logistiks_rus/data.py). Перекладывают блоки в ключи вкладок
методы _*_tab_data: окно не лезет во внутренности вкладок и не повторяет
раскладку сборщика данных — оно только передаёт вкладке словарь с теми
именами, которые та читает.

Две особенности, которые легко потерять при правках:

  * клиент GigaChat создаётся лениво (в _ensure_gigachat при первом
    нажатии) и свой: чужой экземпляр переиспользовать нельзя, а держать
    клиента в __init__ — значит требовать ключ при простом открытии окна;
  * в connect нет lambda, захватывающих вкладку или окно: цикл ссылок
    Python ↔ Qt роняет процесс при выходе. Задачу распознавания слоты
    находят через sender() (см. _task_of_sender).

В логи попадают только имена полей, количества и длины: ФИО, адреса, VIN
и названия организаций не пишутся.
"""

import logging
import os
from functools import partial
from typing import Any, Dict, List, Mapping, Optional

from PyQt5.QtCore import QObject, QRunnable, QSize, QThreadPool, pyqtSignal
from PyQt5.QtWidgets import QMainWindow, QMessageBox, QWidget

from core.contracts.factory import GeneratorFactory
from core.contracts.logistiks_rus.validator import LogistiksRusValidator
from core.contracts.registry import ContractTypeRegistry
from core.gigachat_client import GigaChatClient
from core.prompts import get_prompt
from core.recognizer import filled_only, filled_only_list
from core.secrets_store import MISSING_KEY_MESSAGE
from core.settings_service import get_settings_service
from core.trace import filled_fields_summary
from core.validator import ValidationReport

from ui import theme
from ui.icons import action_icon
from ui.windows.base_window import BaseContractWindow
from ui.windows.logistiks_rus import data as logistiks_rus_data
from ui.windows.logistiks_rus.tabs import (
    CargoTab, CustomerTab, DriverTab, PriceTab, RouteTab, VehicleTab,
)

logger = logging.getLogger("ui.windows.logistiks_rus.window")


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
        logger.info("Логистикс Рус: задача распознавания помечена как отменённая")

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
                    "Логистикс Рус: распознавание отменено — "
                    "результат не применяется"
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
            logger.exception("Логистикс Рус: ошибка в задаче распознавания")
            self.signals.error.emit(str(e))


class LogistiksRusWindow(BaseContractWindow):
    """Окно типа «Логистикс Рус»: шесть рабочих вкладок, генерация и распознавание."""

    CONTRACT_TYPE = "logistiks_rus"
    WINDOW_TITLE = "Логистикс Рус"
    TAB_CONFIGS = [
        ("Заказчик", "customer.svg"),
        ("Груз", "contract.svg"),
        ("Маршрут", "trailer.svg"),
        ("Водитель", "driver.svg"),
        ("ТС", "vehicles.svg"),
        ("Стоимость", "contract.svg"),
    ]

    #: Заголовок вкладки из TAB_CONFIGS → класс настоящей вкладки.
    #: Заголовки — ключ связи с каркасом: _make_tab получает именно их.
    _TAB_BY_TITLE: Dict[str, type] = {
        "Заказчик": CustomerTab,
        "Груз": CargoTab,
        "Маршрут": RouteTab,
        "Водитель": DriverTab,
        "ТС": VehicleTab,
        "Стоимость": PriceTab,
    }

    #: Разделы ответа модели и подписи для debug-лога.
    #: Списки (loading_addresses / consignees / vehicles) считаются по
    #: количеству записей, блоки (customer / tractor / shipper_name / ...) —
    #: по заполненным полям.
    _RECOGNITION_SECTIONS = (
        ("customer", "заказчик"),
        ("shipper_name", "грузоотправитель"),
        ("loading_addresses", "адреса погрузки"),
        ("consignees", "грузополучатели"),
        ("vehicles", "перевозимые автомобили"),
        ("tractor", "тягач"),
        ("trailer", "прицеп"),
        ("driver", "водитель"),
        ("contract", "условия заявки"),
    )

    #: Поля плана погрузки и выгрузки: вкладка «Маршрут» ждёт их теми же
    #: именами, что и сборщик данных (logistiks_rus/data.py::_build_route).
    _ROUTE_PLAN_KEYS = (
        "loading_date",
        "loading_time_from",
        "loading_time_to",
        "unloading_date",
        "unloading_time_from",
        "unloading_time_to",
    )

    #: Суммы раздела 5 в порядке приоритета: у ООО сумма без НДС — это
    #: «Стоимость услуг» (sum_wo_vat), у ИП единственная сумма документа
    #: лежит в sum_total. price_without_vat — запасное имя на случай, если
    #: модель ответила ключами ContractData.
    _AMOUNT_KEYS = ("sum_wo_vat", "price_without_vat", "sum_total")

    #: Поля блока «contract», которые читает вкладка «Стоимость».
    #: carrier_type здесь нет: см. _price_tab_data.
    _PRICE_KEYS = ("vat_rate", "vat_rate_num", "special_conditions")

    #: Ключи блока «tractor» ответа модели → имена полей вкладки «ТС».
    #: Схема промпта Логистикс Рус отдаёт тягач БЕЗ префикса (brand_model,
    #: plate_number), а вкладка ждёт tractor_brand / tractor_plate: без этой
    #: карты распознанный тягач не попадал ни в одно поле.
    _TRACTOR_KEYS: Dict[str, str] = {
        "brand_model": "tractor_brand",
        "plate_number": "tractor_plate",
    }

    #: Ключи блока «trailer» ответа модели → имена полей вкладки «ТС».
    _TRAILER_KEYS: Dict[str, str] = {
        "brand_model": "trailer_brand",
        "plate_number": "trailer_plate",
    }

    def __init__(self, parent: Optional[QMainWindow] = None):
        super().__init__(parent)

        # Реестр типов: без него GeneratorFactory не знает «logistiks_rus».
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
            "Логистикс Рус: окно собрано на реальных вкладках (%s)",
            ", ".join(type(tab).__name__ for tab in self._tabs()),
        )

    # ---------------------------------------------------------
    # Построение интерфейса
    # ---------------------------------------------------------
    def _make_tab(self, title: str, icon_key: str) -> QWidget:
        """
        Фабрика вкладки Логистикс Рус (переопределение хука каркаса).

        Заголовок, которого нет в _TAB_BY_TITLE, отдаётся каркасу: окно
        не должно оставаться без страницы из-за опечатки в TAB_CONFIGS.
        """
        tab_class = self._TAB_BY_TITLE.get(title)
        if tab_class is None:
            logger.error(
                "Логистикс Рус: неизвестный заголовок вкладки %r — взята заглушка",
                title,
            )
            return self._make_placeholder_tab(title)
        return tab_class()

    def _build_header_actions(self) -> None:
        """
        Кнопка «Создать договор» в шапке — как в MainWindow.

        Каркас окна шапку не меняет (её вид общий для всех типов), поэтому
        кнопка добавляется здесь и встаёт перед «Выход». Действие то же,
        что у кнопок на вкладках: документ один, данные собираются со всех
        вкладок сразу.
        """
        header = self.btn_exit.parentWidget().layout()
        self.btn_create_contract = theme.accent_button(
            "Создать договор",
            tooltip="Проверить данные и сформировать договор DOCX",
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
        «logistiks_rus».

        В приложении реестр наполняет main.py (_load_contract_types), но окно
        типа не должно зависеть от того, кто его создал: тест, менеджер окон
        или точка входа. Загрузка идемпотентна — модули типов импортируются
        один раз, повторный вызов только проверяет наличие ключа.
        """
        key = LogistiksRusWindow.CONTRACT_TYPE
        if ContractTypeRegistry.find(key) is not None:
            return
        ContractTypeRegistry.load_builtin()
        if ContractTypeRegistry.find(key) is None:
            logger.error(
                "Логистикс Рус: тип %r не зарегистрирован — генерация будет "
                "выполнена генератором по умолчанию", key,
            )

    def _bind_tabs(self) -> None:
        """
        Ссылки на вкладки по именам и подключение их сигналов.

        Имена (customer_tab, cargo_tab, ...) нужны слотам распознавания и
        тестам; сигналы подключаются к методам окна — тем же, что и кнопки
        в шапке, поэтому «Создать договор» с вкладки и из шапки даёт один
        и тот же результат.
        """
        self.customer_tab = self.tabs.widget(0)
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
    def _collect_data(self):
        """
        Собирает ContractData со всех шести вкладок.

        Раскладка полей живёт в ui/windows/logistiks_rus/data.py и
        проверяется отдельно; окно только передаёт вкладки. Метод ничего не
        меняет в интерфейсе, поэтому вызывается многократно.
        """
        contract_data = logistiks_rus_data.build(
            self.customer_tab,
            self.cargo_tab,
            self.route_tab,
            self.driver_tab,
            self.vehicle_tab,
            self.price_tab,
        )
        logger.info("Логистикс Рус: данные собраны — %s", contract_data.summary())
        return contract_data

    # ---------------------------------------------------------
    # Генерация договора
    # ---------------------------------------------------------
    def _confirm_validation(self, report: ValidationReport) -> bool:
        """
        Показывает результат проверки данных (по образцу MainWindow).

        :return: True, если можно продолжать: замечаний нет — сразу, иначе
            только после явного «Создать договор» в диалоге.
        """
        if report.is_clean:
            logger.info("Логистикс Рус: проверка данных пройдена без замечаний")
            return True

        logger.warning(
            "Логистикс Рус: проверка данных — ошибок=%s, замечаний=%s",
            len(report.errors), len(report.warnings),
        )

        box = QMessageBox(self)
        box.setWindowTitle(
            "Проверьте данные" if report.has_errors else "Замечания к данным"
        )
        box.setIcon(QMessageBox.Critical if report.has_errors else QMessageBox.Warning)
        box.setText(
            "В данных есть ошибки. Заявку можно создать, но проверьте реквизиты."
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

    def _on_create_contract(self) -> None:
        """«Создать договор»: валидатор → диалог → генератор → диалог успеха."""
        logger.info("Логистикс Рус: нажата кнопка «Создать договор»")
        try:
            contract_data = self._collect_data()

            report = LogistiksRusValidator().check(contract_data)
            if not self._confirm_validation(report):
                logger.info(
                    "Логистикс Рус: генерация отменена пользователем "
                    "после проверки"
                )
                self.statusBar().showMessage(
                    "Заявка не создана — исправьте данные", 5000
                )
                return

            generator = GeneratorFactory.get_generator(self.CONTRACT_TYPE)
            path = generator.generate(contract_data)
            logger.info("Логистикс Рус: заявка создана (%s)", os.path.basename(path))
            self._show_contract_created(path)
        except Exception as e:  # noqa: BLE001 — окно не должно падать целиком
            logger.exception("Логистикс Рус: ошибка при создании заявки")
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
            logger.info("Логистикс Рус: диалог создания заявки — «Открыть папку»")
            self._open_folder(folder)

    def _open_folder(self, folder: str) -> bool:
        """Открывает папку с готовой заявкой в системном файловом менеджере."""
        if not folder or not os.path.isdir(folder):
            logger.warning("Логистикс Рус: папка для открытия не найдена")
            return False

        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtGui import QDesktopServices

            if QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
                logger.info("Логистикс Рус: папка с заявкой открыта")
                return True
            logger.warning("Логистикс Рус: QDesktopServices не смог открыть папку")
        except Exception as e:  # noqa: BLE001 — открытие папки не критично
            logger.warning(
                "Логистикс Рус: ошибка открытия папки (%s)", type(e).__name__
            )

        try:
            os.startfile(folder)  # type: ignore[attr-defined]
            logger.info("Логистикс Рус: папка открыта через os.startfile")
            return True
        except Exception as e:  # noqa: BLE001 — последний рубеж
            logger.error(
                "Логистикс Рус: не удалось открыть папку (%s)", type(e).__name__
            )
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
            logger.warning("Логистикс Рус: запрос очистки без отправителя — пропущен")
            return

        logger.info("Логистикс Рус: очистка вкладки %s", type(tab).__name__)
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
            logger.error("Логистикс Рус: %s", MISSING_KEY_MESSAGE)
            return None

        try:
            auth_key.encode("ascii")
        except UnicodeEncodeError as e:
            logger.error(
                "Логистикс Рус: ключ GigaChat содержит не-ASCII символ (позиция %s)",
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
            logger.exception("Логистикс Рус: не удалось создать клиент GigaChat")
            QMessageBox.critical(
                self, "Ошибка GigaChat",
                f"Не удалось инициализировать GigaChat:\n{e}",
            )
            return None

        logger.info("Логистикс Рус: клиент GigaChat создан (ленивая инициализация)")
        return self.gigachat

    def _on_recognize_requested(self, text: str) -> None:
        """Запрос распознавания со вкладки: проверки и старт задачи."""
        logger.info(
            "Логистикс Рус: запрос распознавания (%s символов)",
            len(text or ""),
        )

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

        Промпт берётся у типа (get_prompt("logistiks_rus")) и передаётся
        вторым аргументом: у Логистикс Рус своя раскладка ответа, дефолтный
        системный промпт клиента для неё не подходит.
        """
        prompt = get_prompt(self.CONTRACT_TYPE)
        logger.info(
            "Логистикс Рус: старт распознавания (символов=%s, свой промпт=%s)",
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
            "Логистикс Рус: задача распознавания запущена в пуле "
            "(активных потоков: %s)",
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
            logger.info("Логистикс Рус: отмена устаревшей задачи проигнорирована")
            return
        logger.info("Логистикс Рус: распознавание отменено")
        self._finish_recognition_ui()

    def _log_recognition_result(self, data: Dict[str, Any]) -> None:
        """
        Пишет в debug.log, что распознано, а что нет.

        Только имена разделов, имена полей и количества: значения (ФИО,
        адреса, VIN, названия организаций) в лог не попадают.
        """
        recognized, missing = [], []

        for key, title in self._RECOGNITION_SECTIONS:
            value = data.get(key)

            if isinstance(value, (list, tuple)):
                count = len(filled_only_list(value))
                if count:
                    recognized.append(f"{title}: {count} записей")
                else:
                    missing.append(title)
                continue

            if isinstance(value, str):
                # Строковое поле (shipper_name): значение в лог не пишем —
                # только факт «распознано / нет».
                if value.strip():
                    recognized.append(f"{title}: да")
                else:
                    missing.append(title)
                continue

            if isinstance(value, dict) and filled_only(value):
                recognized.append(f"{title}: {filled_fields_summary(value)}")
            else:
                missing.append(title)

        logger.debug(
            "Логистикс Рус, распознавание: разделы получены — "
            + ("; ".join(recognized) if recognized else "нет")
        )
        logger.debug(
            "Логистикс Рус, распознавание: НЕ распознано — "
            + (", ".join(missing) if missing else "нет")
        )
        logger.debug(
            "Логистикс Рус, распознавание: все ключи ответа: %s", sorted(data.keys())
        )

    # ── Раскладка блоков ответа по вкладкам ──
    # Каждый метод собирает словарь с именами полей СВОЕЙ вкладки и ничего
    # не пишет в интерфейс: так раскладку видно тестом без окна.
    @classmethod
    def _customer_tab_data(cls, section: Any, contract: Any) -> Dict[str, Any]:
        """
        Блок «customer» + шапка из «contract» → поля вкладки «Заказчик».

        Вкладка ждёт name (наименование заказчика), а номер и дату заявки —
        из блока contract: в промпте они лежат там, а не в customer.
        """
        customer = filled_only(section)
        header = filled_only(contract)

        data: Dict[str, Any] = {}

        name = str(
            customer.get("full_name") or customer.get("short_name")
            or customer.get("name") or ""
        ).strip()
        if name:
            data["name"] = name

        for key in ("number", "date"):
            value = header.get(key)
            if value not in (None, ""):
                data[key] = value

        return data

    @classmethod
    def _cargo_tab_data(cls, section: Any) -> Dict[str, Any]:
        """
        Блок «vehicles» → таблица вкладки «Груз».

        Пустые записи ответа ([{"brand_model": "", "vin": ""}]) отбрасываются:
        распознавание, не нашедшее машин, не должно стирать введённые вручную.
        """
        rows = filled_only_list(section)
        return {"vehicles": rows} if rows else {}

    @staticmethod
    def _address_lines(items: Any) -> List[str]:
        """
        Адреса погрузки из ответа модели — списком непустых строк.

        Схема промпта отдаёт loading_addresses массивом СТРОК (не словарей),
        поэтому общий filled_only_list здесь не подходит: он оставляет только
        словари с заполненными полями. Пустые строки и мусор отбрасываются:
        распознавание, не нашедшее адресов, не должно стирать ручной ввод.
        """
        if not isinstance(items, (list, tuple)):
            return []
        return [
            str(item).strip()
            for item in items
            if item is not None and str(item).strip()
        ]

    @classmethod
    def _route_tab_data(cls, answer: Any) -> Dict[str, Any]:
        """Блоки «shipper_name» / «loading_addresses» / «consignees» + план
        из «contract» → вкладка «Маршрут». Принимает и старый формат
        (массив shippers), и новый."""
        answer = answer if isinstance(answer, Mapping) else {}
        plan = filled_only(answer.get("contract")) or {}

        data: Dict[str, Any] = {}

        route = str(plan.get("route") or "").strip()
        if route:
            data["route"] = route
        for key in cls._ROUTE_PLAN_KEYS:
            value = plan.get(key)
            if value not in (None, ""):
                data[key] = value

        shipper_name = str(answer.get("shipper_name") or "").strip()
        if shipper_name:
            data["shipper_name"] = shipper_name

        addresses = cls._address_lines(answer.get("loading_addresses"))
        if addresses:
            data["loading_addresses"] = addresses

        if "shipper_name" not in data and "loading_addresses" not in data:
            old = filled_only_list(answer.get("shippers"))
            if old:
                names = [s.get("name", "") for s in old if s.get("name")]
                addresses = [s.get("address", "") for s in old if s.get("address")]
                if names:
                    data["shipper_name"] = str(names[0]).strip()
                if addresses:
                    data["loading_addresses"] = [str(a).strip() for a in addresses]

        consignees = filled_only_list(answer.get("consignees"))
        if consignees:
            data["consignees"] = consignees

        return data

    @classmethod
    def _driver_tab_data(cls, section: Any) -> Dict[str, Any]:
        """Блок «driver» → вкладка «Водитель» (в бланке печатается только ФИО)."""
        driver = filled_only(section)
        full_name = str(driver.get("full_name") or "").strip()
        return {"full_name": full_name} if full_name else {}

    @classmethod
    def _vehicle_tab_data(cls, section: Any) -> Dict[str, Any]:
        """
        Блоки «tractor» / «trailer» → поля вкладки «ТС».

        Ключи вкладки (tractor_brand, trailer_plate, ...) имеют приоритет:
        если модель вернула и их, и краткие имена из схемы промпта, берётся
        явное значение. Раскладку делают карты _TRACTOR_KEYS и
        _TRAILER_KEYS — схема промпта Логистикс Рус отдаёт тягач и прицеп
        БЕЗ префиксов, а вкладка ждёт имена с префиксом. Лишние ключи
        (цвет, год) отбрасываются: в бланке этого типа их нет.
        """
        if not isinstance(section, dict):
            return {}

        data: Dict[str, Any] = {}
        for source_key, mapping in (
            ("tractor", cls._TRACTOR_KEYS),
            ("trailer", cls._TRAILER_KEYS),
        ):
            unit = filled_only(section.get(source_key))
            for key, value in unit.items():
                field = mapping.get(key, key)
                if field in VehicleTab.FIELDS:
                    # Ключ вкладки (tractor_brand) важнее краткого
                    # (brand_model), но краткий не должен затирать уже
                    # разложенное значение.
                    data.setdefault(field, value)

        return data

    @staticmethod
    def _amount_value(value: Any) -> Optional[float]:
        """
        Сумма из значения любого вида («221 099,18», 269741.0), если она > 0.

        Ноль и пустое значение дают None: у промпта 0.0 означает «суммы в
        документе не было», и такой суммой нельзя ни заполнять вкладку, ни
        затирать введённое вручную число.
        """
        if value is None or isinstance(value, bool):
            return None

        text = (
            str(value)
            .replace("\u00a0", "")
            .replace(" ", "")
            .replace("₽", "")
            .replace(",", ".")
            .strip()
        )
        if not text:
            return None

        try:
            number = float(text)
        except ValueError:
            return None
        return number if number > 0 else None

    @classmethod
    def _price_tab_data(cls, section: Any) -> Dict[str, Any]:
        """
        Блок «contract» → поля вкладки «Стоимость».

        Суммы промпта (sum_wo_vat / sum_vat / sum_total) вкладка не знает:
        у неё одно поле ввода — amount_without_vat. У ООО его место занимает
        «Стоимость услуг» (sum_wo_vat), у ИП — единственная сумма документа
        (sum_total); если sum_wo_vat пуста (в документе указан только итог),
        берётся sum_total.

        Тип экспедитора (carrier_type) в схеме промпта отсутствует — блок
        «carrier» запрещён. Если модель всё же его вернула, значение уходит
        во вкладку как есть; если нет — ключ не подставляется: у вкладки
        свой тип по умолчанию (ООО), и частичный ответ не должен стирать
        выбранный вручную «ИП» — от него зависят и бланк, и расчёт сумм.
        """
        filled = filled_only(section)
        if not filled:
            return {}

        data: Dict[str, Any] = {}

        carrier_type = str(filled.get("carrier_type") or "").strip()
        if carrier_type:
            data["carrier_type"] = carrier_type

        for key in cls._AMOUNT_KEYS:
            amount = cls._amount_value(filled.get(key))
            if amount is not None:
                data["amount_without_vat"] = amount
                break

        for key in cls._PRICE_KEYS:
            value = filled.get(key)
            if value not in (None, ""):
                data[key] = value

        return data

    # ── Заполнение вкладок ──
    def _fill_customer(self, section: Any, contract: Any) -> None:
        """Заказчик и шапка заявки; пустой раздел вкладку не трогает."""
        data = self._customer_tab_data(section, contract)
        if not data:
            logger.info("Логистикс Рус: заказчик не распознан — оставляем как есть")
            return
        self.customer_tab.fill_data(data)

    def _fill_cargo(self, section: Any) -> None:
        """Перевозимые автомобили: пустой список ручной ввод не стирает."""
        data = self._cargo_tab_data(section)
        if not data:
            logger.info("Логистикс Рус: автомобилей в ответе нет — таблица как есть")
            return
        self.cargo_tab.fill_data(data)
        logger.info(
            "Логистикс Рус: таблица автомобилей заполнена (%s записей)",
            len(data["vehicles"]),
        )

    def _fill_route(self, answer: Any) -> None:
        """Грузоотправитель, адреса погрузки, грузополучатели и план."""
        data = self._route_tab_data(answer)
        if not data:
            logger.info("Логистикс Рус: маршрут не распознан — оставляем как есть")
            return
        self.route_tab.fill_data(data)

    def _fill_driver(self, section: Any) -> None:
        """Водитель: в этом бланке печатается только ФИО."""
        data = self._driver_tab_data(section)
        if not data:
            logger.info("Логистикс Рус: водитель не распознан — оставляем как есть")
            return
        self.driver_tab.fill_data(data)

    def _fill_vehicle(self, section: Any) -> None:
        """Автовоз: тягач и прицеп — блоки ответа раскладываются по полям вкладки."""
        data = self._vehicle_tab_data(section)
        if not data:
            logger.info("Логистикс Рус: автовоз не распознан — оставляем как есть")
            return
        self.vehicle_tab.fill_data(data)

    def _fill_price(self, section: Any) -> None:
        """Стоимость: сумма, ставка НДС и особые условия."""
        data = self._price_tab_data(section)
        if not data:
            logger.info("Логистикс Рус: стоимость не распознана — оставляем как есть")
            return
        self.price_tab.fill_data(data)

    def _on_recognition_finished(
        self, data: Dict[str, Any], task: Optional[RecognitionTask] = None
    ) -> None:
        """
        Раскладывает распознанные данные по вкладкам.

        Пустые поля ответа не должны стирать ручной ввод, поэтому каждая
        вкладка получает только заполненные значения: блоки проходят через
        filled_only, списки — через filled_only_list (см. _*_tab_data).

        :param task: задача, чей сигнал пришёл (см. _task_from).
        """
        task = self._task_from(task)
        if not self._is_current_task(task):
            logger.info("Логистикс Рус: результат устаревшей задачи проигнорирован")
            return

        logger.info(
            "Логистикс Рус: распознавание завершено, разделы: %s", sorted(data.keys())
        )
        self._log_recognition_result(data)

        try:
            contract = data.get("contract")
            self._fill_customer(data.get("customer"), contract)
            self._fill_cargo(data.get("vehicles"))
            self._fill_route(data)
            self._fill_driver(data.get("driver"))
            self._fill_vehicle({
                "tractor": data.get("tractor"),
                "trailer": data.get("trailer"),
            })
            self._fill_price(contract)

            self._log_ui_action(
                "распознавание: ответ разложен по вкладкам",
                sections=len(data),
                tabs=len(self.TAB_CONFIGS),
            )
            self.statusBar().showMessage("Распознавание завершено", 5000)
            QMessageBox.information(
                self, "Успех", "Данные успешно распознаны и заполнены!"
            )
        except Exception as e:  # noqa: BLE001 — вкладка не должна ронять окно
            logger.exception("Логистикс Рус: ошибка при заполнении вкладок")
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
            logger.info("Логистикс Рус: ошибка устаревшей задачи проигнорирована")
            return

        logger.error("Логистикс Рус: ошибка распознавания (%s)", type(error_msg).__name__)
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
        заголовки вкладок. Значения данных (ФИО, адреса, VIN, названия
        организаций) в лог не попадают.
        """
        tail = "".join(f" | {key}={value}" for key, value in details.items())
        logger.info("Логистикс Рус, UI: %s%s", action, tail)


__all__ = ["LogistiksRusWindow", "RecognitionTask", "RecognitionSignals"]
