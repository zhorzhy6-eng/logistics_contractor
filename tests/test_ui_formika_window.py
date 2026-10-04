#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты окна типа «Формика» на реальных вкладках (ЭТАП 3.1.B.3).

Проверяют то, что появилось на этом шаге: окно собирается на шести
настоящих вкладках (а не на заглушках «в разработке»), сигналы вкладок
подключены к слотам окна, данные всех вкладок уходят в один ContractData,
«Очистить форму» чистит только свою вкладку, «Создать договор» проходит
путь валидатор → диалог → генератор → диалог успеха, а распознавание
раскладывает ответ модели по вкладкам, не стирая ручной ввод.

Qt — в offscreen-режиме. Сеть и системное хранилище ключей не трогаются:
клиент GigaChat подменяется заглушкой, а промпт типа проверяется по
аргументам, с которыми окно позвало recognize_text.

Все данные синтетические, реальных ПДн нет.
"""

import inspect
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtCore import QObject  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

import ui.windows.formika.window as formika_window_module  # noqa: E402
from core.contract_data import ContractData  # noqa: E402
from core.contracts.factory import GeneratorFactory  # noqa: E402
from core.contracts.formika.generator import FormikaGenerator  # noqa: E402
from core.contracts.formika.validator import FormikaValidator  # noqa: E402
from core.prompts import get_prompt  # noqa: E402
from ui.windows.formika import FormikaWindow  # noqa: E402
from ui.windows.formika.tabs import (  # noqa: E402
    CargoTab, CustomerTab, DriverTab, PriceTab, RouteTab, VehicleTab,
)
from ui.windows.formika.window import RecognitionTask  # noqa: E402

#: Сколько машин помещается в таблицу бланка (см. CargoTab.MAX_CARS).
MAX_CARS = 12

#: Ожидаемый состав вкладок: (заголовок, класс) — порядок как в окне.
TAB_SPECS = (
    ("Заказчик", CustomerTab),
    ("Груз", CargoTab),
    ("Маршрут", RouteTab),
    ("Водитель", DriverTab),
    ("ТС", VehicleTab),
    ("Стоимость", PriceTab),
)


# ─────────────────────────────────────────────────────────────
# Фикстуры и помощники
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def quiet_messages(monkeypatch):
    """
    Глушит диалоги QMessageBox и запоминает их тексты.

    :return: словарь со списками текстов information / warning / critical.
    """
    seen = {"information": [], "warning": [], "critical": []}

    def recorder(kind):
        def _record(parent, title, text, *args, **kwargs):
            seen[kind].append(text)
            return QMessageBox.Ok
        return staticmethod(_record)

    for kind in seen:
        monkeypatch.setattr(QMessageBox, kind, recorder(kind))
    return seen


@pytest.fixture
def window(qt_app, quiet_messages):
    """
    Свежее окно Формики; по завершении теста закрывается по-настоящему.

    Перед закрытием ждём пул распознавания: поток, переживший окно, роняет
    процесс при разрушении QThreadPool (access violation на выходе).
    """
    win = FormikaWindow()
    yield win
    win.thread_pool.waitForDone(5000)
    qt_app.processEvents()
    win.force_close()


class FakeClient:
    """Заглушка клиента GigaChat: в сеть не ходит, помнит аргументы вызова."""

    def __init__(self, answer=None):
        self.answer = answer if answer is not None else {}
        self.calls = []

    def recognize_text(self, text, prompt=None):
        self.calls.append({"text": text, "prompt": prompt})
        return self.answer


def _fill_all_tabs(win, cars=1):
    """Заполняет все шесть вкладок данными, которых хватает валидатору."""
    win.customer_tab.fill_data({"number": "ФМ-2026-1", "date": "2026-09-23"})
    win.cargo_tab.fill_data({"vehicles": [
        {"brand_model": f"МОДЕЛЬ {number}",
         "vin": f"EC3TEUMB0T000{number:04d}"}
        for number in range(1, cars + 1)
    ]})
    win.route_tab.fill_data({
        "route": "Мурманск - Пятигорск",
        "loading_address": "183052, г. Мурманск, пр. Кольский, д. 53",
        "unloading_address": "г. Пятигорск, Бештаугорское шоссе 17",
        "loading_plan_date": "2026-09-24",
        "loading_plan_time_from": "09:00",
        "loading_plan_time_to": "18:00",
    })
    win.driver_tab.fill_data({
        "full_name": "Иванов Иван Иванович",
        "passport_series": "1822",
        "passport_number": "926830",
    })
    win.vehicle_tab.fill_data({
        "tractor_brand": "Foton Auman",
        "tractor_plate": "O844XY196",
        "tractor_type": "Седельный тягач",
        "trailer_brand": "YANGMINDA",
        "trailer_plate": "71ABF18",
    })
    win.price_tab.fill_data({"amount": 122000.0, "payment_days": 10})


def _answer_recognition(window, monkeypatch, data):
    """
    Отдаёт окну ответ модели так же, как это сделала бы задача распознавания.

    Задача передаётся аргументом слота: именно так её связывает
    _start_recognition (functools.partial), и так слот понимает, чей
    результат пришёл.
    """
    task = RecognitionTask(window.gigachat or FakeClient(), "текст", prompt="промпт")
    window.recognition_task = task

    window._on_recognition_finished(data, task=task)
    return task


# ─────────────────────────────────────────────────────────────
# 1. Окно и его вкладки
# ─────────────────────────────────────────────────────────────

def test_window_builds_with_six_tabs(window):
    assert window.CONTRACT_TYPE == "formika"
    assert window.windowTitle() == "Формика"
    assert window.tabs.count() == 6
    assert window.tab_titles() == [title for title, _ in TAB_SPECS]
    assert window.side_nav.count() == 6


def test_make_tab_returns_real_tab_classes(window):
    """Хук _make_tab отдаёт настоящие вкладки, а не заглушки «в разработке»."""
    for index, (title, tab_class) in enumerate(TAB_SPECS):
        tab = window.tabs.widget(index)
        assert isinstance(tab, tab_class), title

    for tab in window._tabs():
        assert hasattr(tab, "recognition_panel"), type(tab).__name__
        assert hasattr(tab, "get_data"), type(tab).__name__


def test_tabs_are_named_attributes(window):
    """Окно держит вкладки и по именам — на них опираются слоты."""
    assert isinstance(window.customer_tab, CustomerTab)
    assert isinstance(window.cargo_tab, CargoTab)
    assert isinstance(window.route_tab, RouteTab)
    assert isinstance(window.driver_tab, DriverTab)
    assert isinstance(window.vehicle_tab, VehicleTab)
    assert isinstance(window.price_tab, PriceTab)


def test_unknown_tab_title_falls_back_to_placeholder(window):
    """Опечатка в TAB_CONFIGS не оставляет окно без страницы."""
    tab = window._make_tab("Такой вкладки нет", "contract.svg")

    assert tab is not None
    assert not hasattr(tab, "get_data")


def test_header_has_create_contract_button(window):
    """«Создать договор» есть и в шапке, и на вкладках (как в MainWindow)."""
    assert window.btn_create_contract.text() == "Создать договор"
    assert window.btn_create_contract.parent() is not None
    for tab in window._tabs():
        assert tab.btn_create_contract.text() == "Создать договор"


def test_gigachat_is_not_created_in_init(window):
    """Клиент GigaChat — ленивый: простое открытие окна ключа не требует."""
    assert window.gigachat is None
    assert window.recognition_task is None


# ─────────────────────────────────────────────────────────────
# 2. Сигналы вкладок подключены к слотам окна
# ─────────────────────────────────────────────────────────────
# Сигнал подключён к связанному методу окна, поэтому доказательство
# связи — вызов этого же пути: клик по кнопке вкладки доходит до
# _collect_data() и _confirm_validation(). Подменять сами слоты после
# создания окна бессмысленно: связь уже держит исходный метод.

def test_tab_create_signal_is_connected_to_window(window, monkeypatch):
    reports = []
    monkeypatch.setattr(
        FormikaWindow, "_confirm_validation",
        lambda self, report: reports.append(report) and False,
    )

    for tab in window._tabs():
        reports.clear()
        tab.btn_create_contract.click()
        assert len(reports) == 1, type(tab).__name__


def test_header_create_button_uses_the_same_slot_as_tabs(window, monkeypatch):
    """
    «Создать договор» в шапке и на вкладке ведут в один слот окна.

    Проверяется по исходнику: обе связи идут на _on_create_contract, а не
    на разные обработчики — иначе шапка и вкладка давали бы разный результат.
    """
    assert "_on_create_contract" in inspect.getsource(
        FormikaWindow._build_header_actions
    )
    assert "_on_create_contract" in inspect.getsource(FormikaWindow._bind_tabs)

    reports = []
    monkeypatch.setattr(
        FormikaWindow, "_confirm_validation",
        lambda self, report: reports.append(report) and False,
    )

    window.btn_create_contract.click()

    assert len(reports) == 1


def test_tab_recognize_signal_is_connected_to_window(window, monkeypatch):
    """Кнопка «Распознать вкладку» доходит до слота окна."""
    clients = []
    monkeypatch.setattr(FormikaWindow, "_ensure_gigachat",
                        lambda self: clients.append(FakeClient()) or clients[-1])

    window.route_tab.recognition_panel.text_edit.setPlainText("текст маршрута")
    window.route_tab.recognition_panel.btn_recognize.click()

    assert len(clients) == 1
    assert window.recognition_task is not None
    assert window.recognition_task.text == "текст маршрута"


def test_window_connect_does_not_use_lambda():
    """
    В connect — методы и partial, но не lambda: lambda, захватывающая окно
    или вкладку, создаёт цикл ссылок Python ↔ Qt и роняет процесс при
    завершении.
    """
    for method in (
        FormikaWindow.__init__,
        FormikaWindow._build_header_actions,
        FormikaWindow._bind_tabs,
        FormikaWindow._start_recognition,
    ):
        source = inspect.getsource(method)
        assert ".connect(lambda" not in source.replace(" ", ""), source.splitlines()[0]


# ─────────────────────────────────────────────────────────────
# 3. Сбор данных
# ─────────────────────────────────────────────────────────────

def test_collect_data_returns_contract_data_from_all_tabs(window):
    _fill_all_tabs(window, cars=2)

    data = window._collect_data()

    assert isinstance(data, ContractData)
    assert data.contract["number"] == "ФМ-2026-1"
    assert data.contract["date"] == "2026-09-23"
    assert data.contract["route"] == "Мурманск - Пятигорск"
    assert data.contract["vat_rate"] == "22%"
    assert data.contract["vat_rate_num"] == 22.0
    assert data.contract["price_input"] == 122000.0
    assert round(data.contract["price_without_vat"]) == 100000
    assert data.contract["payment_days"] == 10

    assert [point["address"] for point in data.loadings] == [
        "183052, г. Мурманск, пр. Кольский, д. 53"
    ]
    assert data.loadings[0]["time_window"] == "09:00-18:00"
    assert [point["address"] for point in data.unloadings] == [
        "г. Пятигорск, Бештаугорское шоссе 17"
    ]

    assert len(data.vehicles) == 2
    assert data.vehicles[0]["vin"] == "EC3TEUMB0T0000001"
    assert data.vehicles[0]["vehicle_type"] == "Легковой автомобиль"

    assert data.tractor == {
        "brand_model": "Foton Auman",
        "plate_number": "O844XY196",
        "vehicle_type": "Седельный тягач",
    }
    assert data.trailer["brand_model"] == "YANGMINDA"
    assert data.trailer["plate_number"] == "71ABF18"

    assert data.driver["full_name"] == "Иванов Иван Иванович"
    assert data.driver["passport_series"] == "18 22"

    # Стороны в бланке Формики фиксированы — окно их не выдумывает.
    assert data.customer == {}
    assert data.carrier == {}


def test_collect_data_is_repeatable_and_does_not_change_ui(window):
    """_collect_data только читает: повторный вызов даёт тот же результат."""
    _fill_all_tabs(window, cars=2)

    first = window._collect_data()
    second = window._collect_data()

    assert first.to_generator_dict() == second.to_generator_dict()
    assert window.customer_tab.get_data()["number"] == "ФМ-2026-1"


def test_more_than_twelve_cars_are_capped_in_contract_data(window):
    """В бланк помещается 12 машин: лишние в ContractData не попадают."""
    _fill_all_tabs(window, cars=MAX_CARS + 3)
    assert len(window.cargo_tab.get_data()["vehicles"]) == MAX_CARS

    data = window._collect_data()

    assert len(data.vehicles) == MAX_CARS
    assert data.vehicles[-1]["vin"] == "EC3TEUMB0T0000012"


def test_empty_tabs_give_empty_contract_data(window):
    data = window._collect_data()

    assert data.vehicles == []
    assert data.loadings == []
    assert data.unloadings == []
    assert data.tractor == {}
    assert data.trailer == {}
    # Вкладка водителя отдаёт поля всегда (шаблон печатает их напрямую),
    # но незаполненные приходят пустыми строками.
    assert data.driver.get("full_name", "") == ""
    assert data.contract.get("number") is None
    assert data.contract.get("route") is None


# ─────────────────────────────────────────────────────────────
# 4. Очистка: только своя вкладка
# ─────────────────────────────────────────────────────────────

def test_clear_tab_clears_only_sender_tab(window):
    _fill_all_tabs(window, cars=1)

    window.driver_tab.btn_clear_form.click()

    assert window.driver_tab.get_data()["full_name"] == ""
    assert window.customer_tab.get_data()["number"] == "ФМ-2026-1"
    assert window.route_tab.get_data()["route"] == "Мурманск - Пятигорск"
    assert window.price_tab.get_data()["amount"] == 122000.0
    assert window.vehicle_tab.get_data()["tractor_brand"] == "Foton Auman"


def test_clear_slot_without_sender_is_safe(window):
    """Прямой вызов слота без отправителя ничего не чистит и не падает."""
    _fill_all_tabs(window, cars=1)

    window._on_clear_tab()

    assert window.customer_tab.get_data()["number"] == "ФМ-2026-1"
    assert window.driver_tab.get_data()["full_name"] == "Иванов Иван Иванович"


# ─────────────────────────────────────────────────────────────
# 5. Закрытие окна
# ─────────────────────────────────────────────────────────────

def test_close_hides_window_without_destroying_it(qt_app):
    win = FormikaWindow()
    win.show()
    _fill_all_tabs(win, cars=1)
    try:
        win.close()

        assert win.isVisible() is False
        assert win.customer_tab.get_data()["number"] == "ФМ-2026-1"
    finally:
        win.force_close()


def test_force_close_really_closes_window(qt_app):
    win = FormikaWindow()
    win.show()
    win.force_close()

    assert win.isVisible() is False
    assert win._force_close is True


# ─────────────────────────────────────────────────────────────
# 6. «Создать договор»: валидатор → диалог → генератор → диалог успеха
# ─────────────────────────────────────────────────────────────

def test_empty_form_fails_validation_and_does_not_generate(
    window, monkeypatch, quiet_messages
):
    """Пустая форма: валидатор находит ошибки, генерация не запускается."""
    report = FormikaValidator().check(ContractData())

    assert report.has_errors is True
    assert "Не заполнен номер договора-заявки" in report.errors
    assert "Стоимость перевозки должна быть больше нуля" in report.errors
    assert "Не заполнено ФИО водителя" in report.errors

    started = []
    monkeypatch.setattr(
        GeneratorFactory, "get_generator",
        classmethod(lambda cls, *args, **kwargs: started.append(args)),
    )
    # Диалог проверки данных: пользователь выбрал «Исправить».
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: QMessageBox.No)

    window._on_create_contract()

    assert started == []
    assert window.statusBar().currentMessage() == "Договор не создан — исправьте данные"


def test_create_contract_can_be_cancelled_in_dialog(
    window, monkeypatch, quiet_messages
):
    """Заполненная форма, но пользователь нажал «Исправить» — генерации нет."""
    _fill_all_tabs(window, cars=1)

    started = []
    monkeypatch.setattr(
        GeneratorFactory, "get_generator",
        classmethod(lambda cls, *args, **kwargs: started.append(args)),
    )
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: QMessageBox.No)

    window._on_create_contract()

    assert started == []
    assert window.statusBar().currentMessage() == "Договор не создан — исправьте данные"


def test_create_contract_generates_docx_end_to_end(
    window, monkeypatch, quiet_messages, work_dir, templates_dir
):
    """Заполненная форма: диалог → DOCX в папке вывода → диалог успеха."""
    _fill_all_tabs(window, cars=2)

    generator = FormikaGenerator(templates_dir=str(templates_dir))
    output_dir = work_dir / "formika_window_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        GeneratorFactory, "get_generator",
        classmethod(lambda cls, *args, **kwargs: generator),
    )
    monkeypatch.setattr(
        generator, "default_output_dir", lambda: str(output_dir)
    )

    created = []
    monkeypatch.setattr(
        FormikaWindow, "_show_contract_created",
        lambda self, path: created.append(path),
    )
    # Диалог проверки данных: пользователь выбрал «Создать договор».
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: QMessageBox.Yes)

    window._on_create_contract()

    assert len(created) == 1
    path = Path(created[0])
    assert path.exists() and path.suffix == ".docx"
    assert "ФМ-2026-1" in path.name
    assert path.parent == output_dir

    # За собой убираем: папка вывода теста не должна пухнуть от прогонов.
    path.unlink()
    output_dir.rmdir()


def test_clean_data_skips_confirmation_dialog(
    window, monkeypatch, quiet_messages, templates_dir
):
    """
    Данные без замечаний: диалог проверки не показывается вовсе.

    Ответ модели подставляется напрямую — так виден именно шаг проверки,
    а не то, что вкладки успели заполнить.
    """
    data = ContractData(
        driver={"full_name": "Иванов Иван Иванович",
                "passport_series": "18 22", "passport_number": "926830",
                "passport_issue_date": "2023-01-30",
                "passport_issuer": "Отделом УФМС России по г. Москве",
                "registration_address": "г. Москва, ул. Тестовая, д. 1",
                "license_series": "99 36", "license_number": "123456",
                "license_issue_date": "2020-01-01",
                "license_expiry_date": "2030-01-01",
                "phone": "+7 (999) 123-45-67"},
        vehicles=[{"vin": "EC3TEUMB0T0002608", "brand_model": "JETOUR T2",
                   "vehicle_type": "Легковой автомобиль"}],
        tractor={"brand_model": "Foton Auman", "plate_number": "O844XY196",
                 "vehicle_type": "Седельный тягач"},
        trailer={"brand_model": "YANGMINDA", "plate_number": "71ABF18"},
        contract={"number": "ФМ-2026-1", "date": "2026-09-23",
                  "route": "Мурманск - Пятигорск", "vat_rate": "22%",
                  "vat_rate_num": 22, "price_input": 122000.0,
                  "payment_days": 10, "loading_plan_date": "2026-09-24",
                  "loading_plan_time_from": "09:00",
                  "loading_plan_time_to": "18:00",
                  "special_conditions": ""},
        loadings=[{"address": "183052, г. Мурманск, пр. Кольский, д. 53",
                   "date": "2026-09-24", "time_window": "09:00-18:00"}],
        unloadings=[{"address": "г. Пятигорск, Бештаугорское шоссе 17",
                     "date": "2026-09-27", "time_window": ""}],
    )
    report = FormikaValidator().check(data)
    assert report.is_clean is True, report.format_text()

    generator = FormikaGenerator(templates_dir=str(templates_dir))
    monkeypatch.setattr(
        GeneratorFactory, "get_generator",
        classmethod(lambda cls, *args, **kwargs: generator),
    )
    monkeypatch.setattr(FormikaWindow, "_collect_data", lambda self: data)

    generated = []
    monkeypatch.setattr(
        FormikaWindow, "_show_contract_created",
        lambda self, path: generated.append(path),
    )
    monkeypatch.setattr(
        generator, "generate", lambda payload: generated.append(payload) and "output/x.docx"
    )

    dialogs = []
    monkeypatch.setattr(
        QMessageBox, "exec_", lambda self: dialogs.append(self.windowTitle()) or 0
    )

    window._on_create_contract()

    assert dialogs == []
    assert generated == [data]


def _make_contract_file(work_dir, name: str):
    """
    Создаёт файл-заготовку договора и убирает его после теста.

    Диалогу достаточно существующего пути: содержимое DOCX не читается,
    а мусор в tests/_tmp после прогона не нужен.
    """
    path = work_dir / name
    path.write_bytes(b"PK\x03\x04")
    return path


def test_show_contract_created_offers_open_folder(
    window, monkeypatch, quiet_messages, work_dir
):
    """Диалог успеха: кнопка «Открыть папку» и «OK», как в MainWindow."""
    path = _make_contract_file(
        work_dir, "Договор-заявка_Формика_ФМ-2026-1_20260923.docx"
    )

    buttons = []
    origin_add_button = QMessageBox.addButton

    def spy_add_button(self, *args, **kwargs):
        button = origin_add_button(self, *args, **kwargs)
        buttons.append(button.text())
        return button

    monkeypatch.setattr(QMessageBox, "addButton", spy_add_button)
    # Диалог не показываем: интересна его начинка, а не модальное окно.
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)

    opened = []
    monkeypatch.setattr(
        FormikaWindow, "_open_folder", lambda self, folder: opened.append(folder)
    )
    try:
        window._show_contract_created(str(path))

        assert buttons == ["Открыть папку", "OK"]
        assert opened == []  # нажали «OK»: папку не открываем
    finally:
        path.unlink(missing_ok=True)


def test_show_contract_created_opens_folder_on_request(
    window, monkeypatch, quiet_messages, work_dir
):
    """Кнопка «Открыть папку» открывает папку готового договора."""
    path = _make_contract_file(
        work_dir, "Договор-заявка_Формика_ФМ-2026-2_20260923.docx"
    )

    opened = []
    monkeypatch.setattr(
        FormikaWindow, "_open_folder", lambda self, folder: opened.append(folder)
    )

    # Запоминаем кнопку «Открыть папку»: в диалоге она первая.
    open_button = None
    origin_add_button = QMessageBox.addButton

    def spy_add_button(self, *args, **kwargs):
        nonlocal open_button
        button = origin_add_button(self, *args, **kwargs)
        if button.text() == "Открыть папку":
            open_button = button
        return button

    monkeypatch.setattr(QMessageBox, "addButton", spy_add_button)

    def spy_exec(box):
        box._clicked = open_button   # нажата «Открыть папку»
        return 0

    monkeypatch.setattr(QMessageBox, "exec_", spy_exec)
    monkeypatch.setattr(
        QMessageBox, "clickedButton", lambda self: getattr(self, "_clicked", None)
    )

    try:
        window._show_contract_created(str(path))

        assert open_button is not None
        assert opened == [str(work_dir)]
    finally:
        path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────
# 7. Распознавание
# ─────────────────────────────────────────────────────────────

def test_recognize_request_starts_task_with_formika_prompt(
    window, monkeypatch, qt_app
):
    """Промпт типа передаётся в recognize_text вторым аргументом."""
    client = FakeClient()
    monkeypatch.setattr(FormikaWindow, "_ensure_gigachat", lambda self: client)

    prompt = get_prompt("formika")
    assert prompt, "у типа «Формика» должен быть свой промпт"

    window._on_recognize_requested("текст договора-заявки")
    task = window.recognition_task
    assert isinstance(task, RecognitionTask)
    assert task.prompt == prompt

    # Ждём пул: задача уже выполняется в потоке. Сигнал из потока приходит
    # в поток интерфейса, поэтому очередь событий доводим до конца —
    # иначе слот не успеет сбросить состояние задачи.
    window.thread_pool.waitForDone(5000)
    qt_app.processEvents()

    assert len(client.calls) == 1
    assert client.calls[0]["text"] == "текст договора-заявки"
    assert client.calls[0]["prompt"] == prompt

    # Связь сигналов с partial(task=...) действительно сработала.
    assert window.recognition_task is None


def test_recognition_without_key_shows_message(window, monkeypatch, quiet_messages):
    """Нет ключа — показываем MISSING_KEY_MESSAGE, задачу не запускаем."""
    monkeypatch.setattr(FormikaWindow, "_ensure_gigachat", lambda self: None)

    window._on_recognize_requested("текст договора-заявки")

    assert window.recognition_task is None
    assert any("set_key.py" in text for text in quiet_messages["critical"])


def test_empty_text_is_not_sent_to_recognition(window, monkeypatch, quiet_messages):
    called = []
    monkeypatch.setattr(FormikaWindow, "_ensure_gigachat",
                        lambda self: called.append("client"))

    window._on_recognize_requested("   ")

    assert called == []
    assert quiet_messages["warning"]


def test_recognition_fills_tabs(window, monkeypatch, quiet_messages):
    """Ответ модели раскладывается по вкладкам: у каждой — свои поля."""
    _fill_all_tabs(window, cars=1)

    _answer_recognition(window, monkeypatch, {
        "driver": {"passport_issuer": "Отделом УФМС России по г. Москве"},
        "vehicles": [{"brand_model": "JETOUR T2", "vin": "EC3TEUMB0T0002608"}],
        "tractor": {"brand_model": "Foton Auman", "plate_number": "O844XY196",
                    "vehicle_type": "Седельный тягач", "color": "Белый"},
        "trailer": {"brand_model": "YANGMINDA", "plate_number": "71ABF18",
                    "color": "Серый", "year": "2020"},
        "contract": {"route": "Мурманск - Пятигорск (обновлено)",
                     "special_conditions": "Без дозагрузки"},
    })

    assert window.driver_tab.get_data()["passport_issuer"].startswith("Отделом УФМС")

    # Таблица груза заменяется данными ответа: было 1 ТС, стало одно другое.
    assert window.cargo_tab.get_data()["vehicles"] == [
        {"brand_model": "JETOUR T2", "vin": "EC3TEUMB0T0002608"}
    ]

    # Тягач и прицеп: ключи ответа — ровно те, что обещает схема промпта
    # Формики (core/prompts/formika.py): brand_model / plate_number /
    # vehicle_type, БЕЗ префиксов tractor_ и trailer_. Раскладывает их карта
    # ключей в _vehicle_tab_data.
    vehicle = window.vehicle_tab.get_data()
    assert vehicle["tractor_brand"] == "Foton Auman"
    assert vehicle["tractor_plate"] == "O844XY196"
    assert vehicle["tractor_type"] == "Седельный тягач"
    assert vehicle["trailer_brand"] == "YANGMINDA"
    assert vehicle["trailer_plate"] == "71ABF18"
    assert vehicle["trailer_year"] == "2020"

    assert window.route_tab.get_data()["route"] == "Мурманск - Пятигорск (обновлено)"
    assert window.price_tab.get_data()["special_conditions"] == "Без дозагрузки"


def test_vehicle_tab_keys_win_over_prompt_keys(window, monkeypatch, quiet_messages):
    """
    Ключи вкладки (tractor_brand) важнее ключей схемы промпта (brand_model).

    Ответ может прийти в обоих видах: справочник машин и повторное
    распознавание отдают имена полей вкладки, а схема промпта Формики —
    краткие имена. Явное значение должно побеждать.
    """
    _answer_recognition(window, monkeypatch, {
        "tractor": {"tractor_brand": "Явная марка", "brand_model": "Из промпта",
                    "plate_number": "O844XY196"},
        "trailer": {"brand_model": "YANGMINDA"},
    })

    vehicle = window.vehicle_tab.get_data()
    assert vehicle["tractor_brand"] == "Явная марка"
    assert vehicle["tractor_plate"] == "O844XY196"
    assert vehicle["trailer_brand"] == "YANGMINDA"


def test_vehicle_tab_data_maps_prompt_schema():
    """
    Прямая проверка раскладки блока tractor / trailer (схема промпта).

    Отдельно от окна: так видно саму карту ключей, а не её косвенное
    действие через вкладку.
    """
    data = FormikaWindow._vehicle_tab_data({
        "tractor": {"brand_model": "Foton Auman", "plate_number": "O844XY196",
                    "vehicle_type": "Седельный тягач", "color": "Белый",
                    "year": "2023"},
        "trailer": {"brand_model": "YANGMINDA", "plate_number": "71ABF18",
                    "color": "Серый", "year": "2020"},
    })

    assert data == {
        "tractor_brand": "Foton Auman",
        "tractor_plate": "O844XY196",
        "tractor_type": "Седельный тягач",
        "tractor_color": "Белый",
        "tractor_year": "2023",
        "trailer_brand": "YANGMINDA",
        "trailer_plate": "71ABF18",
        "trailer_color": "Серый",
        "trailer_year": "2020",
    }

    # Пустые значения схема обещает как "" — они не должны ничего затирать.
    assert FormikaWindow._vehicle_tab_data({
        "tractor": {"brand_model": "", "plate_number": ""},
        "trailer": {},
    }) == {}

    # ts_type — запасной ключ типа ТС (как в ui/windows/formika/data.py).
    assert FormikaWindow._vehicle_tab_data({
        "tractor": {"ts_type": "Автопоезд"},
    }) == {"tractor_type": "Автопоезд"}


def test_recognition_does_not_overwrite_manual_input(window, monkeypatch, quiet_messages):
    """Пустые поля ответа не стирают то, что пользователь ввёл руками."""
    _fill_all_tabs(window, cars=1)

    _answer_recognition(window, monkeypatch, {
        # Модель вернула разделы целиком, но с пустыми строками.
        "driver": {"full_name": "", "passport_series": "", "passport_number": ""},
        "vehicles": [{"brand_model": "", "vin": ""}],
        "tractor": {"brand_model": "", "plate_number": ""},
        "contract": {"number": "", "route": "", "amount": ""},
    })

    assert window.driver_tab.get_data()["full_name"] == "Иванов Иван Иванович"
    assert window.driver_tab.get_data()["passport_number"] == "926830"
    assert window.customer_tab.get_data()["number"] == "ФМ-2026-1"
    assert window.route_tab.get_data()["route"] == "Мурманск - Пятигорск"
    assert window.vehicle_tab.get_data()["tractor_brand"] == "Foton Auman"
    assert window.price_tab.get_data()["amount"] == 122000.0
    assert len(window.cargo_tab.get_data()["vehicles"]) == 1


def test_error_slot_without_sender_is_safe(window, monkeypatch, quiet_messages):
    """Ошибка без отправителя: сообщение показываем, состояние сбрасываем."""
    window._on_recognition_error("Модель недоступна")

    assert any("Модель недоступна" in text for text in quiet_messages["critical"])
    assert window.recognition_task is None


def test_recognition_error_is_reported(window, monkeypatch, quiet_messages):
    """Ошибка распознавания: QMessageBox.critical и сброс состояния."""
    task = RecognitionTask(FakeClient(), "текст", prompt="промпт")
    window.recognition_task = task

    window._on_recognition_error("Модель недоступна", task=task)

    assert any("Модель недоступна" in text for text in quiet_messages["critical"])
    assert window.recognition_task is None


def test_result_of_stale_task_is_ignored(window, monkeypatch, quiet_messages):
    """Результат устаревшей (отменённой) задачи к вкладкам не применяется."""
    _fill_all_tabs(window, cars=1)
    stale = RecognitionTask(FakeClient(), "текст", prompt="промпт")

    window._on_recognition_finished(
        {"contract": {"number": "ЧУЖОЙ-НОМЕР"}}, task=stale
    )

    assert window.customer_tab.get_data()["number"] == "ФМ-2026-1"


# ─────────────────────────────────────────────────────────────
# 8. Задача распознавания (своя, а не из MainWindow)
# ─────────────────────────────────────────────────────────────

def test_recognition_task_passes_prompt_to_client():
    """Своя задача типа: промпт уходит в клиент вторым аргументом."""
    client = FakeClient({"contract": {"number": "ФМ-1"}})
    task = RecognitionTask(client, "исходный текст", prompt="свой промпт")

    received = []
    task.signals.finished.connect(received.append)
    task.run()

    assert received == [{"contract": {"number": "ФМ-1"}}]
    assert client.calls[0]["prompt"] == "свой промпт"


def test_cancelled_recognition_task_does_not_apply_result():
    """Отменённая задача результат не отдаёт — интерфейс не тронут."""
    client = FakeClient({"contract": {"number": "ФМ-1"}})
    task = RecognitionTask(client, "исходный текст", prompt="свой промпт")
    task.cancel()

    finished, cancelled = [], []
    task.signals.finished.connect(finished.append)
    task.signals.cancelled.connect(lambda: cancelled.append(True))  # noqa: B023
    task.run()

    assert finished == []
    assert cancelled == [True]
