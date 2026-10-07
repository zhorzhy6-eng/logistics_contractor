#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Окно типа «Разовая аренда» на реальных вкладках (ЭТАП 3.1.D.B.3).

«Разовая аренда» — договор аренды транспортного средства с экипажем
(как правило, на один рейс). Каркас окна (шапка, сайдбар, селектор типа,
«Выход», закрытие через hide()) остался общим: он живёт в
ui/windows/base_window.py. Здесь — только то, что у этого типа своё:

  * вкладки: семь настоящих вкладок из ui/windows/arenda_ts/tabs/
    вместо заглушек «в разработке» (хук _make_tab);
  * сбор данных: data.build(...) → ContractData
    (ui/windows/arenda_ts/data.py);
  * генерация: ArendaTsValidator → диалог подтверждения → генератор типа
    из GeneratorFactory → диалог успеха;
  * распознавание: свой промпт типа (get_prompt("arenda_ts")), пул потоков
    QThreadPool + QRunnable, разбор ответа через filled_only /
    filled_only_list — пустые поля ответа не стирают ручной ввод;
  * «Очистить форму»: чистит ТОЛЬКО ту вкладку, из которой пришёл сигнал.

Раскладка ответа модели — своя у каждого типа. Промпт аренды
(core/prompts/arenda_ts.py) отдаёт блоки lessee / lessor / tractor /
trailer / vehicles / loadings / unloadings / driver / contract, а часть
полей (route, lease_start_date, lease_end_date, lessee, lessor) кладёт в
КОРЕНЬ ответа. Вкладки ждут СВОИ имена полей (см. docstring
ui/windows/arenda_ts/data.py), поэтому окно перекладывает блоки в словари
с именами полей вкладок методами _*_tab_data: оно не лезет во внутренности
вкладок и не повторяет раскладку сборщика данных.

Подъём корневых полей в contract — забота сборщика (data.build) и
генератора: окно просто передаёт данные из блоков в fill_data() вкладок, а
обратно читает их через get_data().

Две особенности, которые легко потерять при правках:

  * клиент GigaChat создаётся лениво (в _ensure_gigachat при первом
    нажатии) и свой: чужой экземпляр переиспользовать нельзя, а держать
    клиента в __init__ — значит требовать ключ при простом открытии окна;
  * в connect нет lambda, захватывающих вкладку или окно: цикл ссылок
    Python ↔ Qt роняет процесс при выходе. Задачу распознавания слоты
    находят через sender() (см. _task_of_sender).

В логи попадают только имена полей, количества и длины: ФИО, адреса, VIN,
названия организаций и суммы не пишутся.
"""

import logging
import os
from functools import partial
from typing import Any, Dict, List, Mapping, Optional, Tuple

from PyQt5.QtCore import QObject, QRunnable, QSize, QThreadPool, pyqtSignal
from PyQt5.QtWidgets import QMainWindow, QMessageBox, QWidget

from core.contracts.arenda_ts.validator import ArendaTsValidator
from core.contracts.factory import GeneratorFactory
from core.contracts.registry import ContractTypeRegistry
from core.gigachat_client import GigaChatClient
from core.prompts import get_prompt
from core.recognizer import filled_only, filled_only_list, has_content
from core.secrets_store import MISSING_KEY_MESSAGE
from core.settings_service import get_settings_service
from core.trace import filled_fields_summary
from core.validator import ValidationReport

from ui import theme
from ui.icons import action_icon
from ui.windows.arenda_ts import data as arenda_ts_data
from ui.windows.arenda_ts.tabs import (
    ActTab, CargoTab, CrewTab, LesseeTab, LessorTab, PriceTab, RouteTab,
    VehicleTab,
)
from ui.windows.arenda_ts.tabs.act_tab import ACT_FIELDS
from ui.windows.base_window import BaseContractWindow

logger = logging.getLogger("ui.windows.arenda_ts.window")


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
        logger.info("Разовая аренда: задача распознавания помечена как отменённая")

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

            # В лог — только длина текста: сам текст договора содержит ПДн.
            self.signals.progress.emit(
                20, f"Отправка в GigaChat ({len(self.text)} символов)..."
            )
            data = self.client.recognize_text(self.text, prompt=self.prompt)

            if self._cancelled:
                logger.info(
                    "Разовая аренда: распознавание отменено — "
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
            logger.exception("Разовая аренда: ошибка в задаче распознавания")
            self.signals.error.emit(str(e))


class ArendaTsWindow(BaseContractWindow):
    """Окно типа «Разовая аренда»: восемь рабочих вкладок, генерация и распознавание."""

    CONTRACT_TYPE = "arenda_ts"
    WINDOW_TITLE = "Разовая аренда"
    TAB_CONFIGS = [
        ("Арендатор", "customer.svg"),
        ("Арендодатель", "carrier.svg"),
        ("ТС", "vehicles.svg"),
        ("Маршрут", "trailer.svg"),
        ("Груз", "contract.svg"),
        ("Экипаж", "driver.svg"),
        ("Стоимость", "contract.svg"),
        ("Акт", "contract.svg"),
    ]

    #: Заголовок вкладки из TAB_CONFIGS → класс настоящей вкладки.
    #: Заголовки — ключ связи с каркасом: _make_tab получает именно их.
    _TAB_BY_TITLE: Dict[str, type] = {
        "Арендатор": LesseeTab,
        "Арендодатель": LessorTab,
        "ТС": VehicleTab,
        "Маршрут": RouteTab,
        "Груз": CargoTab,
        "Экипаж": CrewTab,
        "Стоимость": PriceTab,
        # Приложение № 1 — часть того же файла (шаг FIX-3).
        "Акт": ActTab,
    }

    #: Разделы ответа модели и подписи для debug-лога.
    #: Списки (vehicles / loadings / unloadings) считаются по количеству
    #: записей, блоки (lessee / tractor / ...) — по заполненным полям,
    #: строки (route, срок аренды) — по непустому значению.
    _RECOGNITION_SECTIONS = (
        ("customer", "заказчик"),
        ("lessee", "арендатор"),
        ("lessor", "арендодатель"),
        ("vehicle", "объект аренды"),
        ("tractor", "тягач"),
        ("trailer", "прицеп"),
        ("route", "маршрут"),
        ("lease_start_date", "начало аренды"),
        ("lease_end_date", "окончание аренды"),
        ("loadings", "точки погрузки"),
        ("unloadings", "точки выгрузки"),
        ("vehicles", "перевозимые автомобили"),
        ("driver", "экипаж"),
        ("contract", "условия договора"),
    )

    #: Ключи блока «tractor» ответа модели → имена полей вкладки «ТС».
    #: Схема промпта аренды отдаёт тягач БЕЗ префикса (brand_model,
    #: plate_number, vehicle_type), а вкладка ждёт tractor_brand /
    #: tractor_plate / tractor_type: без этой карты распознанный тягач не
    #: попадал ни в одно поле (та же грабля, что у Логистикс Рус).
    _TRACTOR_KEYS: Dict[str, str] = {
        "brand_model": "tractor_brand",
        "plate_number": "tractor_plate",
        "vehicle_type": "tractor_type",
        "ts_type": "tractor_type",
    }

    #: Ключи блока «trailer» ответа модели → имена полей вкладки «ТС».
    _TRAILER_KEYS: Dict[str, str] = {
        "brand_model": "trailer_brand",
        "plate_number": "trailer_plate",
    }

    #: Ключи блоков сторон (lessee / lessor) ответа модели → имена полей
    #: вкладок «Арендатор» и «Арендодатель». Промпт называет реквизиты так,
    #: как они напечатаны в договоре (bank_account, bank_name), а вкладка
    #: аренды читает свои имена (account, bank) — см. _LESSEE_FIELDS /
    #: _LESSOR_FIELDS в ui/windows/arenda_ts/data.py. Адреса переводятся
    #: вкладкой: она принимает и address, и legal_address.
    _PARTY_KEYS: Dict[str, str] = {
        "bank_account": "account",
        "bank_name": "bank",
    }

    #: Ключи блока «driver» ответа модели → имена полей вкладки «Экипаж».
    #: Вкладка принимает и краткие имена блока, и свои (driver_full_name):
    #: карта переводит их явно, чтобы раскладка была видна в окне, а не
    #: угадывалась вкладкой. Серия и номер документа склеиваются вкладкой
    #: (CrewTab._join_document), а строку разбирает сборщик данных.
    _CREW_KEYS: Dict[str, str] = {
        "full_name": "driver_full_name",
        "birth_date": "driver_birth_date",
        "passport": "driver_passport",
        "passport_series": "driver_passport_series",
        "passport_number": "driver_passport_number",
        "passport_issuer": "driver_passport_issuer",
        "passport_issue_date": "driver_passport_issue_date",
        "license": "driver_license",
        "license_series": "driver_license_series",
        "license_number": "driver_license_number",
        "license_issue_date": "driver_license_issue_date",
        "address": "driver_registration_address",
        "registration_address": "driver_registration_address",
        "phone": "driver_phone",
    }

    #: Что вкладка «Экипаж» умеет принять: её поля, поля-даты и раздельные
    #: серия с номером документа. Остальные ключи блока отбрасываются.
    _CREW_TARGETS = frozenset(CrewTab.FIELDS) | frozenset(CrewTab.DATE_FIELDS) | frozenset({
        "driver_passport_series", "driver_passport_number",
        "driver_license_series", "driver_license_number",
    })

    #: Поля блока «contract», которые читает вкладка «Стоимость».
    #: Суммы идут отдельно (см. _price_tab_data): нулевая сумма означает
    #: «в документе её не было» и вкладке не передаётся. payment_days —
    #: срок оплаты из п. 4.5 (промпт аренды его отдаёт, FIX-1-T); ноль
    #: у промпта значит «срок не указан», и вкладка такой ключ игнорирует
    #: (см. PriceTab.fill_data), оставляя значение по умолчанию.
    _PRICE_KEYS = ("vat_rate", "vat_rate_num", "special_conditions",
                   "payment_days")

    #: Ключи сумм блока «contract» в порядке приоритета вкладки: база
    #: арендной платы у ООО и ИП с НДС — сумма без НДС, у ИП без НДС
    #: единственная сумма документа лежит в sum_total.
    _AMOUNT_KEYS = ("sum_wo_vat", "sum_vat", "sum_total")

    def __init__(self, parent: Optional[QMainWindow] = None):
        super().__init__(parent)

        # Реестр типов: без него GeneratorFactory не знает «arenda_ts».
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
            "Разовая аренда: окно собрано на реальных вкладках (%s)",
            ", ".join(type(tab).__name__ for tab in self._tabs()),
        )

    # ---------------------------------------------------------
    # Построение интерфейса
    # ---------------------------------------------------------
    def _make_tab(self, title: str, icon_key: str) -> QWidget:
        """
        Фабрика вкладки «Разовой аренды» (переопределение хука каркаса).

        Заголовок, которого нет в _TAB_BY_TITLE, отдаётся каркасу: окно
        не должно оставаться без страницы из-за опечатки в TAB_CONFIGS.
        """
        tab_class = self._TAB_BY_TITLE.get(title)
        if tab_class is None:
            logger.error(
                "Разовая аренда: неизвестный заголовок вкладки %r — взята заглушка",
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
        «arenda_ts».

        В приложении реестр наполняет main.py (_load_contract_types), но окно
        типа не должно зависеть от того, кто его создал: тест, менеджер окон
        или точка входа. Загрузка идемпотентна — модули типов импортируются
        один раз, повторный вызов только проверяет наличие ключа.
        """
        key = ArendaTsWindow.CONTRACT_TYPE
        if ContractTypeRegistry.find(key) is not None:
            return
        ContractTypeRegistry.load_builtin()
        if ContractTypeRegistry.find(key) is None:
            logger.error(
                "Разовая аренда: тип %r не зарегистрирован — генерация будет "
                "выполнена генератором по умолчанию", key,
            )

    def _bind_tabs(self) -> None:
        """
        Ссылки на вкладки по именам и подключение их сигналов.

        Имена (lessee_tab, cargo_tab, ...) нужны слотам распознавания и
        тестам; сигналы подключаются к методам окна — тем же, что и кнопки
        в шапке, поэтому «Создать договор» с вкладки и из шапки даёт один
        и тот же результат.
        """
        self.lessee_tab = self.tabs.widget(0)
        self.lessor_tab = self.tabs.widget(1)
        self.vehicle_tab = self.tabs.widget(2)
        self.route_tab = self.tabs.widget(3)
        self.cargo_tab = self.tabs.widget(4)
        self.crew_tab = self.tabs.widget(5)
        self.price_tab = self.tabs.widget(6)
        self.act_tab = self.tabs.widget(7)

        for tab in self._tabs():
            tab.create_contract_requested.connect(self._on_create_contract)
            tab.clear_requested.connect(self._on_clear_tab)
            tab.recognize_requested.connect(self._on_recognize_requested)

    # ---------------------------------------------------------
    # Сбор данных
    # ---------------------------------------------------------
    def _collect_data(self):
        """
        Собирает ContractData со всех восьми вкладок.

        Раскладка полей живёт в ui/windows/arenda_ts/data.py и проверяется
        отдельно; окно только передаёт вкладки. Метод ничего не меняет в
        интерфейсе, поэтому вызывается многократно.
        """
        contract_data = arenda_ts_data.build(
            self.lessee_tab,
            self.lessor_tab,
            self.vehicle_tab,
            self.route_tab,
            self.cargo_tab,
            self.crew_tab,
            self.price_tab,
            self.act_tab,
        )
        logger.info("Разовая аренда: данные собраны — %s", contract_data.summary())
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
            logger.info("Разовая аренда: проверка данных пройдена без замечаний")
            return True

        logger.warning(
            "Разовая аренда: проверка данных — ошибок=%s, замечаний=%s",
            len(report.errors), len(report.warnings),
        )

        box = QMessageBox(self)
        box.setWindowTitle(
            "Проверьте данные" if report.has_errors else "Замечания к данным"
        )
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

    def _on_create_contract(self) -> None:
        """«Создать договор»: валидатор → диалог → генератор → диалог успеха."""
        logger.info("Разовая аренда: нажата кнопка «Создать договор»")
        try:
            contract_data = self._collect_data()

            report = ArendaTsValidator().check(contract_data)
            if not self._confirm_validation(report):
                logger.info(
                    "Разовая аренда: генерация отменена пользователем "
                    "после проверки"
                )
                self.statusBar().showMessage(
                    "Договор не создан — исправьте данные", 5000
                )
                return

            generator = GeneratorFactory.get_generator(self.CONTRACT_TYPE)
            path = generator.generate(contract_data)
            logger.info(
                "Разовая аренда: договор создан (%s)", os.path.basename(path)
            )
            self._show_contract_created(path)
        except Exception as e:  # noqa: BLE001 — окно не должно падать целиком
            logger.exception("Разовая аренда: ошибка при создании договора")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось создать договор:\n{e}"
            )

    def _show_contract_created(self, path: str) -> None:
        """
        Сообщает о созданном договоре и предлагает открыть папку.

        Кнопки те же, что в MainWindow: «Открыть папку» + «OK».
        """
        folder = os.path.dirname(os.path.abspath(path))

        box = QMessageBox(self)
        box.setWindowTitle("Успех")
        box.setIcon(QMessageBox.Information)
        box.setText("Договор аренды создан")
        box.setInformativeText(f"{os.path.basename(path)}\n\nПапка: {folder}")

        open_button = box.addButton("Открыть папку", QMessageBox.ActionRole)
        box.addButton("OK", QMessageBox.AcceptRole)

        box.exec_()

        if box.clickedButton() is open_button:
            logger.info("Разовая аренда: диалог создания договора — «Открыть папку»")
            self._open_folder(folder)

    def _open_folder(self, folder: str) -> bool:
        """Открывает папку с готовым договором в системном файловом менеджере."""
        if not folder or not os.path.isdir(folder):
            logger.warning("Разовая аренда: папка для открытия не найдена")
            return False

        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtGui import QDesktopServices

            if QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
                logger.info("Разовая аренда: папка с договором открыта")
                return True
            logger.warning("Разовая аренда: QDesktopServices не смог открыть папку")
        except Exception as e:  # noqa: BLE001 — открытие папки не критично
            logger.warning(
                "Разовая аренда: ошибка открытия папки (%s)", type(e).__name__
            )

        try:
            os.startfile(folder)  # type: ignore[attr-defined]
            logger.info("Разовая аренда: папка открыта через os.startfile")
            return True
        except Exception as e:  # noqa: BLE001 — последний рубеж
            logger.error(
                "Разовая аренда: не удалось открыть папку (%s)", type(e).__name__
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
            logger.warning(
                "Разовая аренда: запрос очистки без отправителя — пропущен"
            )
            return

        logger.info("Разовая аренда: очистка вкладки %s", type(tab).__name__)
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
            logger.error("Разовая аренда: %s", MISSING_KEY_MESSAGE)
            return None

        try:
            auth_key.encode("ascii")
        except UnicodeEncodeError as e:
            logger.error(
                "Разовая аренда: ключ GigaChat содержит не-ASCII символ (позиция %s)",
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
            logger.exception("Разовая аренда: не удалось создать клиент GigaChat")
            QMessageBox.critical(
                self, "Ошибка GigaChat",
                f"Не удалось инициализировать GigaChat:\n{e}",
            )
            return None

        logger.info("Разовая аренда: клиент GigaChat создан (ленивая инициализация)")
        return self.gigachat

    def _on_recognize_requested(self, text: str) -> None:
        """Запрос распознавания со вкладки: проверки и старт задачи."""
        logger.info(
            "Разовая аренда: запрос распознавания (%s символов)",
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

        Промпт берётся у типа (get_prompt("arenda_ts")) и передаётся вторым
        аргументом: у аренды своя раскладка ответа (блоки обеих сторон,
        срок аренды и точки в корне), дефолтный системный промпт клиента
        для неё не подходит.
        """
        prompt = get_prompt(self.CONTRACT_TYPE)
        logger.info(
            "Разовая аренда: старт распознавания (символов=%s, свой промпт=%s)",
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
            "Разовая аренда: задача распознавания запущена в пуле "
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
            logger.info("Разовая аренда: отмена устаревшей задачи проигнорирована")
            return
        logger.info("Разовая аренда: распознавание отменено")
        self._finish_recognition_ui()

    def _log_recognition_result(self, data: Dict[str, Any]) -> None:
        """
        Пишет в debug.log, что распознано, а что нет.

        Только имена разделов, имена полей и количества: значения (ФИО,
        адреса, VIN, названия организаций, суммы) в лог не попадают.
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

            if isinstance(value, dict):
                if filled_only(value):
                    recognized.append(f"{title}: {filled_fields_summary(value)}")
                else:
                    missing.append(title)
                continue

            if has_content(value):
                recognized.append(title)
            else:
                missing.append(title)

        logger.debug(
            "Разовая аренда, распознавание: разделы получены — "
            + ("; ".join(recognized) if recognized else "нет")
        )
        logger.debug(
            "Разовая аренда, распознавание: НЕ распознано — "
            + (", ".join(missing) if missing else "нет")
        )
        logger.debug(
            "Разовая аренда, распознавание: все ключи ответа: %s", sorted(data.keys())
        )

    # ── Раскладка блоков ответа по вкладкам ──
    # Каждый метод собирает словарь с именами полей СВОЕЙ вкладки и ничего
    # не пишет в интерфейс: так раскладку видно тестом без окна.
    @staticmethod
    def _merge_filled(*sections: Any) -> Dict[str, Any]:
        """
        Заполненные поля нескольких блоков ответа одним словарём.

        Пустые значения не попадают в результат, а поздний блок важнее
        раннего: у аренды заказчик приходит и блоком «customer»
        (наименования), и блоком «lessee» (реквизиты) — второе точнее.
        """
        merged: Dict[str, Any] = {}
        for section in sections:
            merged.update(filled_only(section))
        return merged

    @classmethod
    def _party_keys(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ключи блока стороны → имена полей вкладки стороны.

        Переводит bank_account / bank_name в account / bank (см.
        _PARTY_KEYS). Явное имя вкладки важнее: если пришли оба, остаётся
        account — второй слой не должен перебивать первый неизвестно чем.
        """
        result: Dict[str, Any] = {}
        for source, target in cls._PARTY_KEYS.items():
            if target in data:
                continue
            value = data.get(source)
            if value not in (None, ""):
                result[target] = value

        result.update(
            {key: value for key, value in data.items()
             if key not in cls._PARTY_KEYS}
        )
        return result

    @staticmethod
    def _is_ip(entity_type: Any) -> bool:
        """Похоже ли значение entity_type на индивидуального предпринимателя."""
        text = str(entity_type or "").strip().upper()
        return "ИП" in text or "ПРЕДПРИНИМАТЕЛЬ" in text

    @staticmethod
    def _vat_rate_is_zero(contract: Any) -> bool:
        """
        True, если ставка НДС в блоке «contract» нулевая.

        Правило разбора то же, что у сборщика данных (arenda_ts/data.py::
        _parse_vat_rate): «0%», 0, 0.0, а также «Без НДС» и «НДС не
        облагается» — нулевая ставка; для ИП это вариант бланка без НДС.
        """
        block = filled_only(contract)

        value = block.get("vat_rate_num")
        if value is None or isinstance(value, bool) or value == "":
            value = block.get("vat_rate")

        # 0.0 — это значение, а не пустота: «value or ""» превратил бы
        # нулевую ставку в пустую строку и вернул бы False.
        text = "" if value is None else str(value).strip().lower()
        if not text:
            return False
        if "без ндс" in text or "не облагается" in text:
            return True

        try:
            return float(text.replace("%", "").replace(",", ".")) <= 0
        except (TypeError, ValueError):
            return False

    @classmethod
    def _carrier_type_of(cls, entity_type: Any, contract: Any) -> str:
        """
        Вид Арендатора из ответа модели: entity_type + ставка НДС.

        Правило то же, что у сборщика данных (arenda_ts/data.py::
        _resolve_carrier_type) и генератора: «ИП» со ставкой «0%» — вариант
        без НДС, «ИП» с другой ставкой — с НДС, всё остальное — ООО. Пустое
        entity_type не навязывает ничего: у вкладки свой вид по умолчанию, и
        частичный ответ не должен стирать выбранный вручную «ИП».
        """
        text = str(entity_type or "").strip()
        if not text:
            return ""

        if not cls._is_ip(text):
            return arenda_ts_data.CARRIER_TYPE_OOO
        if cls._vat_rate_is_zero(contract):
            return arenda_ts_data.CARRIER_TYPE_IP_WITHOUT_VAT
        return arenda_ts_data.CARRIER_TYPE_IP_WITH_VAT

    @classmethod
    def _lessee_tab_data(cls, customer: Any, lessee: Any,
                         contract: Any = None) -> Dict[str, Any]:
        """
        Блоки «customer» + «lessee» → поля вкладки «Арендатор».

        Арендатор — наша сторона: промпт аренды отдаёт её блоком lessee
        (наименования, реквизиты, entity_type), а вторая сторона того же
        договора — блоком customer из чужой схемы. Блоки сливаются, lessee
        важнее; вид Арендатора выводится из entity_type и ставки НДС, а
        явно пришедший carrier_type не переписывается.
        """
        data = cls._party_keys(cls._merge_filled(customer, lessee))
        if not data:
            return {}

        if not data.get("carrier_type"):
            carrier_type = cls._carrier_type_of(data.get("entity_type"), contract)
            if carrier_type:
                data["carrier_type"] = carrier_type

        return data

    @classmethod
    def _lessor_tab_data(cls, lessor: Any) -> Dict[str, Any]:
        """Блок «lessor» → поля вкладки «Арендодатель» (вторая сторона)."""
        return cls._party_keys(cls._merge_filled(lessor))

    @staticmethod
    def _first_value(block: Mapping[str, Any], keys: Tuple[str, ...]) -> Any:
        """Первое непустое значение блока по списку имён ключей (иначе None)."""
        for key in keys:
            value = block.get(key)
            if value not in (None, ""):
                return value
        return None

    @classmethod
    def _vehicle_tab_data(
        cls,
        vehicle: Any,
        tractor: Any,
        trailer: Any,
        contract: Any,
        lease_start_date: Any = "",
        lease_end_date: Any = "",
    ) -> Dict[str, Any]:
        """
        Блоки «vehicle» / «tractor» / «trailer» / «contract» → вкладка «ТС».

        Номер и дата договора лежат в contract, плановая дата завершения рейса
        (п. 3.3.2, шаг FIX-1-T2) — тоже в contract (planned_completion_date),
        а срок аренды (п. 2.5) промпт отдаёт в КОРНЕ ответа: вкладка ждёт их
        одним словарём, поэтому корневые приходят отдельными аргументами.
        Тягач и прицеп принимаются и вложенными в vehicle, и отдельными
        блоками (схема промпта кладёт их в корень). Ключи вкладки
        (tractor_brand) важнее кратких имён схемы (brand_model): раскладку
        делает _TRACTOR_KEYS / _TRAILER_KEYS.
        """
        container = filled_only(vehicle)
        header = filled_only(contract)

        data: Dict[str, Any] = {}

        # Номер и дата договора: блок contract важнее контейнера vehicle —
        # это шапка документа. Внутри блока ключ вкладки (contract_number)
        # важнее краткого имени схемы промпта (number).
        for key, names in (
            ("contract_number", ("contract_number", "number")),
            ("contract_date", ("contract_date", "date")),
        ):
            value = cls._first_value(header, names)
            if value is None:
                value = cls._first_value(container, names)
            if value is not None:
                data[key] = value

        # Даты: плановая дата завершения рейса (п. 3.3.2) лежит в блоке
        # contract, срок аренды (п. 2.5) — в КОРНЕ ответа. Каждая дата
        # читается своим ключом и НЕ выводится из соседних (FIX-1).
        for key, root_value, names in (
            ("planned_completion_date", None, ("planned_completion_date",)),
            ("lease_start_date", lease_start_date,
             ("lease_start_date", "start_date")),
            ("lease_end_date", lease_end_date,
             ("lease_end_date", "end_date")),
        ):
            value = root_value
            if value in (None, ""):
                value = cls._first_value(header, names)
            if value in (None, ""):
                value = cls._first_value(container, names)
            if value not in (None, ""):
                data[key] = value

        for name, mapping, block in (
            ("tractor", cls._TRACTOR_KEYS, tractor),
            ("trailer", cls._TRAILER_KEYS, trailer),
        ):
            unit = filled_only(container.get(name)) or filled_only(block)
            for key, value in unit.items():
                field = mapping.get(key, key)
                if field not in VehicleTab.FIELDS:
                    continue
                if key == field:
                    # Явное имя вкладки не должно проигрывать краткому
                    # имени схемы, в каком бы порядке они ни пришли.
                    data[field] = value
                else:
                    data.setdefault(field, value)

        return data

    @classmethod
    def _route_tab_data(cls, data: Any, contract: Any = None) -> Dict[str, Any]:
        """
        Блоки «route» / «loadings» / «unloadings» + «contract» → «Маршрут».

        Точки у договора аренды называются loadings / unloadings, а у заявки
        на перевозку — shippers / consignees: принимаются оба имени, порядок
        «shippers, затем loadings» — как у чужой схемы, которая может прийти
        от модели вместо своей. Пустой список точек в словарь не попадает:
        вкладка не должна очищать введённое вручную.
        """
        answer = data if isinstance(data, Mapping) else {}
        header = filled_only(contract)

        result: Dict[str, Any] = {}

        route = str(answer.get("route") or header.get("route") or "").strip()
        if route:
            result["route"] = route

        for key, sources in (
            ("loadings", (answer.get("shippers"), answer.get("loadings"),
                          header.get("loadings"))),
            ("unloadings", (answer.get("consignees"), answer.get("unloadings"),
                            header.get("unloadings"))),
        ):
            points = cls._first_points(*sources)
            if points:
                result[key] = points

        return result

    @staticmethod
    def _first_points(*sources: Any) -> List[Dict[str, Any]]:
        """
        Первый непустой список точек из перечисленных источников.

        Пустые записи ([{"address": ""}]) отбрасываются: распознавание, не
        нашедшее точек, не должно стирать введённые вручную.
        """
        for source in sources:
            points = filled_only_list(source)
            if points:
                return points
        return []

    @classmethod
    def _cargo_tab_data(cls, vehicles: Any) -> Dict[str, Any]:
        """
        Блок «vehicles» → таблица вкладки «Груз».

        Пустые записи ответа ([{"brand_model": "", "vin": ""}]) отбрасываются:
        распознавание, не нашедшее машин, не должно стирать введённые вручную.
        """
        rows = filled_only_list(vehicles)
        return {"vehicles": rows} if rows else {}

    @classmethod
    def _crew_tab_data(cls, driver: Any) -> Dict[str, Any]:
        """
        Блок «driver» → поля вкладки «Экипаж».

        Имена блока (full_name, passport, license, ...) переводятся в имена
        вкладки (driver_full_name, driver_passport, ...) картой _CREW_KEYS:
        вкладка принимает оба вида, но раскладка должна быть видна здесь, а
        не угадываться вкладкой. Ключи, которых вкладка не знает, отбрасываются
        по _CREW_TARGETS; раздельные серия и номер документа в этот набор
        входят — вкладка склеивает их сама (CrewTab._join_document).
        """
        data: Dict[str, Any] = {}
        for key, value in filled_only(driver).items():
            field = cls._CREW_KEYS.get(key, key)
            if field in cls._CREW_TARGETS:
                data[field] = value
        return data

    @staticmethod
    def _amount_value(value: Any) -> Optional[float]:
        """
        Сумма из значения любого вида («221 099,18», 269741.0), если она > 0.

        Ноль и пустое значение дают None: у промпта 0.0 означает «суммы в
        документе не было», и такой суммой нельзя ни заполнять вкладку, ни
        затирать введённое вручную число. Заодно это не даёт нулю занять
        первое место в списке приоритетов вкладки и спрятать за собой
        настоящую сумму (у ИП без НДС она лежит в sum_total).
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
    def _price_tab_data(cls, contract: Any) -> Dict[str, Any]:
        """
        Блок «contract» → поля вкладки «Стоимость».

        Суммы (sum_wo_vat / sum_vat / sum_total) передаются только
        положительными: ноль у промпта означает «суммы в документе не было»,
        а вкладка берёт первую непустую сумму из списка приоритетов — ноль
        на первом месте спрятал бы за собой итог документа.

        Ставка НДС и особые условия уходят как есть: вкладка сама решает,
        что с ними делать (ставит пункт списка, пишет в поле), а пустое
        значение в словарь не попадает.

        Срок оплаты (payment_days) передаётся только положительным: ноль у
        промпта значит «срока в документе нет», а вкладка приняла бы его за
        введённое значение и сбросила своё «по умолчанию» в ноль.
        """
        filled = filled_only(contract)
        if not filled:
            return {}

        data: Dict[str, Any] = {}

        for key in cls._AMOUNT_KEYS:
            amount = cls._amount_value(filled.get(key))
            if amount is not None:
                data[key] = amount

        for key in cls._PRICE_KEYS:
            value = filled.get(key)
            if value in (None, ""):
                continue
            if key == "payment_days":
                days = cls._amount_value(value)
                if days is not None:
                    data[key] = int(days)
                continue
            data[key] = value

        return data

    # ── Заполнение вкладок ──
    def _fill_lessee(self, customer: Any, lessee: Any, contract: Any) -> None:
        """Арендатор: реквизиты нашей стороны и вид бланка."""
        data = self._lessee_tab_data(customer, lessee, contract)
        if not data:
            logger.info("Разовая аренда: арендатор не распознан — оставляем как есть")
            return
        self.lessee_tab.fill_data(data)

    def _fill_lessor(self, lessor: Any) -> None:
        """Арендодатель: реквизиты второй стороны."""
        data = self._lessor_tab_data(lessor)
        if not data:
            logger.info(
                "Разовая аренда: арендодатель не распознан — оставляем как есть"
            )
            return
        self.lessor_tab.fill_data(data)

    def _fill_vehicle(self, data: Mapping[str, Any], contract: Any) -> None:
        """Договор, срок аренды, тягач и прицеп."""
        payload = self._vehicle_tab_data(
            data.get("vehicle"),
            data.get("tractor"),
            data.get("trailer"),
            contract,
            lease_start_date=data.get("lease_start_date"),
            lease_end_date=data.get("lease_end_date"),
        )
        if not payload:
            logger.info("Разовая аренда: объект аренды не распознан — оставляем как есть")
            return
        self.vehicle_tab.fill_data(payload)

    def _fill_route(self, data: Mapping[str, Any], contract: Any) -> None:
        """Маршрут и таблицы точек погрузки и выгрузки."""
        payload = self._route_tab_data(data, contract)
        if not payload:
            logger.info("Разовая аренда: маршрут не распознан — оставляем как есть")
            return
        self.route_tab.fill_data(payload)

    def _fill_cargo(self, vehicles: Any) -> None:
        """Перевозимые автомобили: пустой список ручной ввод не стирает."""
        payload = self._cargo_tab_data(vehicles)
        if not payload:
            logger.info(
                "Разовая аренда: автомобилей в ответе нет — таблица как есть"
            )
            return
        self.cargo_tab.fill_data(payload)
        logger.info(
            "Разовая аренда: таблица автомобилей заполнена (%s записей)",
            len(payload["vehicles"]),
        )

    def _fill_crew(self, driver: Any) -> None:
        """Экипаж: девять полей бланка из блока driver."""
        payload = self._crew_tab_data(driver)
        if not payload:
            logger.info("Разовая аренда: экипаж не распознан — оставляем как есть")
            return
        self.crew_tab.fill_data(payload)

    def _fill_price(self, contract: Any) -> None:
        """Стоимость: суммы, ставка НДС и особые условия."""
        payload = self._price_tab_data(contract)
        if not payload:
            logger.info("Разовая аренда: стоимость не распознана — оставляем как есть")
            return
        self.price_tab.fill_data(payload)

    @staticmethod
    def _act_tab_data(contract: Any, answer: Any) -> Dict[str, Any]:
        """
        Поля Акта (Приложение № 1) → вкладка «Акт» (шаг FIX-3).

        Промпт аренды этих полей не извлекает: акт — форма для заполнения при
        передаче ТС, и в ответе модели их нет (core/prompts/arenda_ts.py,
        «ЧЕГО В ОТВЕТЕ БЫТЬ НЕ ДОЛЖНО»). Но если данные пришли (например, из
        сохранённого ответа или чужой раскладки), терять их не нужно —
        поэтому ключи ищутся и в блоке contract, и в корне ответа.

        Пустые значения не возвращаются: они не должны стирать ручной ввод.
        """
        data: Dict[str, Any] = {}
        for source in (contract, answer):
            if not isinstance(source, Mapping):
                continue
            for field in ACT_FIELDS:
                if field in data:
                    continue
                value = str(source.get(field) or "").strip()
                if value:
                    data[field] = value
        return data

    def _fill_act(self, contract: Any, answer: Any) -> None:
        """Акт приёма-передачи: десять полей Приложения № 1."""
        payload = self._act_tab_data(contract, answer)
        if not payload:
            logger.info(
                "Разовая аренда: полей акта в ответе нет — вкладка как есть"
            )
            return
        self.act_tab.fill_data(payload)

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
            logger.info("Разовая аренда: результат устаревшей задачи проигнорирован")
            return

        answer = data if isinstance(data, Mapping) else {}
        logger.info(
            "Разовая аренда: распознавание завершено, разделы: %s",
            sorted(answer.keys()),
        )
        self._log_recognition_result(answer)

        try:
            contract = answer.get("contract")
            self._fill_lessee(answer.get("customer"), answer.get("lessee"), contract)
            self._fill_lessor(answer.get("lessor"))
            self._fill_vehicle(answer, contract)
            self._fill_route(answer, contract)
            self._fill_cargo(answer.get("vehicles"))
            self._fill_crew(answer.get("driver"))
            self._fill_price(contract)
            self._fill_act(contract, answer)

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
            logger.exception("Разовая аренда: ошибка при заполнении вкладок")
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
            logger.info("Разовая аренда: ошибка устаревшей задачи проигнорирована")
            return

        logger.error(
            "Разовая аренда: ошибка распознавания (%s)", type(error_msg).__name__
        )
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
        организаций, суммы) в лог не попадают.
        """
        tail = "".join(f" | {key}={value}" for key, value in details.items())
        logger.info("Разовая аренда, UI: %s%s", action, tail)


__all__ = ["ArendaTsWindow", "RecognitionTask", "RecognitionSignals"]
