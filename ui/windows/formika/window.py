#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Окно типа «Формика» на реальных вкладках (ЭТАП 3.1.B.3).

Тип — приложение к генеральному договору перевозки. Каркас окна (шапка,
сайдбар, селектор типа, «Выход», закрытие через hide()) остался общим:
он живёт в ui/windows/base_window.py. Здесь — только то, что у Формики
своё:

  * вкладки: шесть настоящих вкладок из ui/windows/formika/tabs/ вместо
    заглушек «в разработке» (хук _make_tab);
  * сбор данных: data.build(...) → ContractData (ui/windows/formika/data.py);
  * генерация: FormikaValidator → диалог подтверждения → генератор типа
    из GeneratorFactory → диалог успеха;
  * распознавание: свой промпт Формики (get_prompt("formika")), пул
    потоков QThreadPool + QRunnable, разбор ответа через filled_only /
    filled_only_list — пустые поля ответа не стирают ручной ввод;
  * «Очистить форму»: чистит ТОЛЬКО ту вкладку, из которой пришёл сигнал.

Две особенности, которые легко потерять при правках:

  * клиент GigaChat создаётся лениво (в _ensure_gigachat при первом
    нажатии) и свой: чужой экземпляр переиспользовать нельзя, а держать
    клиента в __init__ — значит требовать ключ при простом открытии окна;
  * в connect нет lambda, захватывающих вкладку или окно: цикл ссылок
    Python ↔ Qt роняет процесс при выходе. Задачу распознавания слоты
    находят через sender() (см. _task_of_sender).
"""

import logging
import os
from functools import partial
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import QObject, QRunnable, QSize, QThreadPool, pyqtSignal
from PyQt5.QtWidgets import QMainWindow, QMessageBox, QWidget

from core.contracts.factory import GeneratorFactory
from core.contracts.formika.validator import FormikaValidator
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
from ui.windows.formika import data as formika_data
from ui.windows.formika.tabs import (
    CargoTab, CustomerTab, DriverTab, PriceTab, RouteTab, VehicleTab,
)

logger = logging.getLogger("ui.windows.formika.window")


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
        logger.info("Формика: задача распознавания помечена как отменённая")

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

            self.signals.progress.emit(
                20, f"Отправка в GigaChat ({len(self.text)} символов)..."
            )
            data = self.client.recognize_text(self.text, prompt=self.prompt)

            if self._cancelled:
                logger.info(
                    "Формика: распознавание отменено — результат не применяется"
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
            logger.exception("Формика: ошибка в задаче распознавания")
            self.signals.error.emit(str(e))


class FormikaWindow(BaseContractWindow):
    """Окно типа «Формика»: шесть рабочих вкладок, генерация и распознавание."""

    CONTRACT_TYPE = "formika"
    WINDOW_TITLE = "Формика"
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
    _RECOGNITION_SECTIONS = (
        ("driver", "водитель"),
        ("customer", "заказчик"),
        ("carrier", "перевозчик"),
        ("vehicles", "перевозимые ТС"),
        ("tractor", "тягач"),
        ("trailer", "полуприцеп"),
        ("contract", "договор"),
    )

    #: Ключи блока «trailer» ответа модели → имена полей вкладки «ТС».
    #: Схема промпта Формики у прицепа без префикса ("year", "color"),
    #: а вкладка ждёт trailer_year / trailer_color. У тягача префикс уже
    #: есть (brand_model / plate_number / vehicle_type), поэтому в карте
    #: только прицеп.
    _TRAILER_KEYS: Dict[str, str] = {
        "brand_model": "trailer_brand",
        "plate_number": "trailer_plate",
        "color": "trailer_color",
        "year": "trailer_year",
    }

    def __init__(self, parent: Optional[QMainWindow] = None):
        super().__init__(parent)

        # Реестр типов: без него GeneratorFactory не знает «formika».
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

        # Кнопки на вкладках уже подключены — сигналами заведует _init_ui
        # каркаса; здесь остаётся шапка и ссылки на вкладки по именам.
        self._build_header_actions()
        self._bind_tabs()

        logger.info(
            "Формика: окно собрано на реальных вкладках (%s)",
            ", ".join(type(tab).__name__ for tab in self._tabs()),
        )

    # ---------------------------------------------------------
    # Построение интерфейса
    # ---------------------------------------------------------
    def _make_tab(self, title: str, icon_key: str) -> QWidget:
        """
        Фабрика вкладки Формики (переопределение хука каркаса).

        Заголовок, которого нет в _TAB_BY_TITLE, отдаётся каркасу: окно
        не должно оставаться без страницы из-за опечатки в TAB_CONFIGS.
        """
        tab_class = self._TAB_BY_TITLE.get(title)
        if tab_class is None:
            logger.error(
                "Формика: неизвестный заголовок вкладки %r — взята заглушка",
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
        Прогревает реестр типов: без него GeneratorFactory не знает «formika».

        В приложении реестр наполняет main.py (_load_contract_types), но окно
        типа не должно зависеть от того, кто его создал: тест, менеджер окон
        или точка входа. Загрузка идемпотентна — модули типов импортируются
        один раз, повторный вызов только проверяет наличие ключа.
        """
        key = FormikaWindow.CONTRACT_TYPE
        if ContractTypeRegistry.find(key) is not None:
            return
        ContractTypeRegistry.load_builtin()
        if ContractTypeRegistry.find(key) is None:
            logger.error(
                "Формика: тип %r не зарегистрирован — генерация будет "
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

        Раскладка полей живёт в ui/windows/formika/data.py и проверяется
        отдельно; окно только передаёт вкладки. Метод ничего не меняет в
        интерфейсе, поэтому вызывается многократно.
        """
        contract_data = formika_data.build(
            self.customer_tab,
            self.cargo_tab,
            self.route_tab,
            self.driver_tab,
            self.vehicle_tab,
            self.price_tab,
        )
        logger.info("Формика: данные собраны — %s", contract_data.summary())
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
            logger.info("Формика: проверка данных пройдена без замечаний")
            return True

        logger.warning(
            "Формика: проверка данных — ошибок=%s, замечаний=%s",
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
        logger.info("Формика: нажата кнопка «Создать договор»")
        try:
            contract_data = self._collect_data()

            report = FormikaValidator().check(contract_data)
            if not self._confirm_validation(report):
                logger.info(
                    "Формика: генерация отменена пользователем после проверки"
                )
                self.statusBar().showMessage(
                    "Договор не создан — исправьте данные", 5000
                )
                return

            generator = GeneratorFactory.get_generator(self.CONTRACT_TYPE)
            path = generator.generate(contract_data)
            logger.info("Формика: договор создан (%s)", os.path.basename(path))
            self._show_contract_created(path)
        except Exception as e:  # noqa: BLE001 — окно не должно падать целиком
            logger.exception("Формика: ошибка при создании договора")
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
        box.setText("Договор создан")
        box.setInformativeText(f"{os.path.basename(path)}\n\nПапка: {folder}")

        open_button = box.addButton("Открыть папку", QMessageBox.ActionRole)
        box.addButton("OK", QMessageBox.AcceptRole)

        box.exec_()

        if box.clickedButton() is open_button:
            logger.info("Формика: диалог создания договора — «Открыть папку»")
            self._open_folder(folder)

    def _open_folder(self, folder: str) -> bool:
        """Открывает папку с готовым договором в системном файловом менеджере."""
        if not folder or not os.path.isdir(folder):
            logger.warning("Формика: папка для открытия не найдена")
            return False

        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtGui import QDesktopServices

            if QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
                logger.info("Формика: папка с договором открыта")
                return True
            logger.warning("Формика: QDesktopServices не смог открыть папку")
        except Exception as e:  # noqa: BLE001 — открытие папки не критично
            logger.warning("Формика: ошибка открытия папки (%s)", type(e).__name__)

        try:
            os.startfile(folder)  # type: ignore[attr-defined]
            logger.info("Формика: папка открыта через os.startfile")
            return True
        except Exception as e:  # noqa: BLE001 — последний рубеж
            logger.error("Формика: не удалось открыть папку (%s)", type(e).__name__)
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
            logger.warning("Формика: запрос очистки без отправителя — пропущен")
            return

        logger.info("Формика: очистка вкладки %s", type(tab).__name__)
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
            logger.error("Формика: %s", MISSING_KEY_MESSAGE)
            return None

        try:
            auth_key.encode("ascii")
        except UnicodeEncodeError as e:
            logger.error(
                "Формика: ключ GigaChat содержит не-ASCII символ (позиция %s)",
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
            logger.exception("Формика: не удалось создать клиент GigaChat")
            QMessageBox.critical(
                self, "Ошибка GigaChat",
                f"Не удалось инициализировать GigaChat:\n{e}",
            )
            return None

        logger.info("Формика: клиент GigaChat создан (ленивая инициализация)")
        return self.gigachat

    def _on_recognize_requested(self, text: str) -> None:
        """Запрос распознавания со вкладки: проверки и старт задачи."""
        logger.info(
            "Формика: запрос распознавания (%s символов)",
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

        Промпт берётся у типа (get_prompt("formika")) и передаётся вторым
        аргументом: у Формики своя раскладка ответа, дефолтный системный
        промпт клиента для неё не подходит.
        """
        prompt = get_prompt(self.CONTRACT_TYPE)
        logger.info(
            "Формика: старт распознавания (символов=%s, свой промпт=%s)",
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
            "Формика: задача распознавания запущена в пуле "
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
            logger.info("Формика: отмена устаревшей задачи проигнорирована")
            return
        logger.info("Формика: распознавание отменено")
        self._finish_recognition_ui()

    def _log_recognition_result(self, data: Dict[str, Any]) -> None:
        """
        Пишет в debug.log, что распознано, а что нет.

        Только имена разделов, имена полей и количества: значения (ФИО,
        адреса, паспорт, VIN) в лог не попадают.
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
            "Формика, распознавание: разделы получены — "
            + ("; ".join(recognized) if recognized else "нет")
        )
        logger.debug(
            "Формика, распознавание: НЕ распознано — "
            + (", ".join(missing) if missing else "нет")
        )
        logger.debug(
            "Формика, распознавание: все ключи ответа: %s", sorted(data.keys())
        )

    # ── Заполнение по разделам ──
    def _fill_customer(self, section: Any) -> None:
        """Номер и дата заявки; пустой раздел вкладку не трогает."""
        filled = filled_only(section)
        if not filled:
            logger.info("Формика: заявка не распознана — оставляем как есть")
            return
        self.customer_tab.fill_data(filled)

    def _fill_cargo(self, section: Any) -> None:
        """Перевозимые машины: строки без данных ручной ввод не стирают."""
        rows = filled_only_list(section)
        if not rows:
            logger.info("Формика: машин в ответе нет — таблица как есть")
            return
        self.cargo_tab.fill_data({"vehicles": rows})
        logger.info("Формика: таблица машин заполнена (%s записей)", len(rows))

    def _fill_route(self, section: Any) -> None:
        """Маршрут и план погрузки; точки маршрута берём из contract."""
        filled = filled_only(section)
        if not filled:
            logger.info("Формика: маршрут не распознан — оставляем как есть")
            return
        self.route_tab.fill_data(filled)

    def _fill_driver(self, section: Any) -> None:
        """Данные водителя (ФИО, паспорт, ВУ, телефон)."""
        filled = filled_only(section)
        if not filled:
            logger.info("Формика: водитель не распознан — оставляем как есть")
            return
        self.driver_tab.fill_data(filled)

    @classmethod
    def _vehicle_tab_data(cls, section: Any) -> Dict[str, Any]:
        """
        Поля вкладки «ТС» из блоков tractor / trailer ответа модели.

        Ключи вкладки (tractor_brand, trailer_year, ...) имеют приоритет:
        если модель вернула и их, и краткие имена, берётся явное значение.
        """
        if not isinstance(section, dict):
            return {}

        tractor = filled_only(section.get("tractor"))
        trailer = filled_only(section.get("trailer"))

        data: Dict[str, Any] = {
            key: value for key, value in tractor.items() if key.startswith("tractor_")
        }
        for key, value in trailer.items():
            data.setdefault(cls._TRAILER_KEYS.get(key, key), value)

        data.update(
            {key: value for key, value in tractor.items() if not key.startswith("tractor_")}
        )
        return data

    def _fill_vehicle(self, section: Any) -> None:
        """Тягач и полуприцеп: блоки ответа раскладываются по полям вкладки."""
        filled = self._vehicle_tab_data(section)
        if not filled:
            logger.info("Формика: ТС не распознаны — оставляем как есть")
            return
        self.vehicle_tab.fill_data(filled)

    def _fill_price(self, section: Any) -> None:
        """
        Стоимость: сумма, НДС, срок оплаты, особые условия.

        Вкладка принимает и ключи ответа модели (price_input,
        price_with_vat), и свои (amount) — см. PriceTab.fill_data.
        """
        filled = filled_only(section)
        if not filled:
            logger.info("Формика: стоимость не распознана — оставляем как есть")
            return
        self.price_tab.fill_data(filled)

    def _on_recognition_finished(
        self, data: Dict[str, Any], task: Optional[RecognitionTask] = None
    ) -> None:
        """
        Раскладывает распознанные данные по вкладкам.

        Пустые поля ответа не должны стирать ручной ввод, поэтому каждая
        вкладка получает раздел, прошедший через filled_only /
        filled_only_list.

        :param task: задача, чей сигнал пришёл (см. _task_from).
        """
        task = self._task_from(task)
        if not self._is_current_task(task):
            logger.info("Формика: результат устаревшей задачи проигнорирован")
            return

        logger.info("Формика: распознавание завершено, разделы: %s", sorted(data.keys()))
        self._log_recognition_result(data)

        try:
            self._fill_customer(data.get("contract"))
            self._fill_cargo(data.get("vehicles"))
            self._fill_route(data.get("contract"))
            self._fill_driver(data.get("driver"))
            self._fill_vehicle({
                "tractor": data.get("tractor"),
                "trailer": data.get("trailer"),
            })
            self._fill_price(data.get("contract"))

            self.statusBar().showMessage("Распознавание завершено", 5000)
            QMessageBox.information(
                self, "Успех", "Данные успешно распознаны и заполнены!"
            )
        except Exception as e:  # noqa: BLE001 — вкладка не должна ронять окно
            logger.exception("Формика: ошибка при заполнении вкладок")
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
            logger.info("Формика: ошибка устаревшей задачи проигнорирована")
            return

        logger.error("Формика: ошибка распознавания (%s)", type(error_msg).__name__)
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
        заголовки вкладок. Значения данных (ФИО, адреса, паспорта, VIN)
        в лог не попадают.
        """
        tail = "".join(f" | {key}={value}" for key, value in details.items())
        logger.info("Формика, UI: %s%s", action, tail)


__all__ = ["FormikaWindow", "RecognitionTask", "RecognitionSignals"]
