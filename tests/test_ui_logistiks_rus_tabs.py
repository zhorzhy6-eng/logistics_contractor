#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты шести вкладок «Логистикс Рус» (ЭТАП 3.1.C.B.2).

Проверяется то, на что опирается сборка данных
(ui/windows/logistiks_rus/data.py::collect_logistiks_rus_data): набор ключей
get_data(), заполнение fill_data(), очистка clear(), сигналы вкладки и
панель действий. Отдельно — особенности этого типа заявки: таблица груза на
12 машин с заголовком «Марка, модель», таблицы грузоотправителей и
грузополучателей по 10 блоков, водитель с одним ФИО, автовоз без типа,
цвета и года и стоимость с переключением «ООО ↔ ИП».

Последний раздел собирает ContractData из настоящих вкладок и проверяет,
что валидатор типа не находит в них ни ошибок, ни замечаний.

Qt — в offscreen-режиме. Модальные диалоги подменяются: ни один тест
не должен останавливаться на QMessageBox.
"""

import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtCore import QDate, QTime  # noqa: E402
from PyQt5.QtTest import QSignalSpy  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QDoubleSpinBox, QFrame, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QWidget,
)

from core.contract_data import ContractData  # noqa: E402
from ui.tabs.base_tab import TabMixin  # noqa: E402
from ui.widgets import RecognitionPanel  # noqa: E402
from ui.windows.logistiks_rus import data as data_module  # noqa: E402
from ui.windows.logistiks_rus.data import build  # noqa: E402
from ui.windows.logistiks_rus.tabs import (  # noqa: E402
    CargoTab, CustomerTab, DriverTab, PriceTab, RouteTab, VehicleTab,
)
from ui.windows.logistiks_rus.tabs import cargo_tab as cargo_tab_module  # noqa: E402
from ui.windows.logistiks_rus.tabs import route_tab as route_tab_module  # noqa: E402
from ui.windows.logistiks_rus.tabs.customer_tab import (  # noqa: E402
    DEFAULT_CUSTOMER_NAME,
)

# ─────────────────────────────────────────────────────────────
# Данные тестов
# ─────────────────────────────────────────────────────────────

#: VIN из conftest (17 символов, ISO 3779) — на нём данных хватает валидатору.
VIN_1 = "EC3TEUMB0T0002608"
VIN_2 = "XTC651150N0001001"

#: Суммы ООО-варианта: 221 099,18 + 22% = 48 641,82 → 269 741,00.
AMOUNT_WITHOUT_VAT = 221099.18
AMOUNT_WITH_VAT = 269741.00
VAT_AMOUNT = 48641.82

#: Все вкладки окна: (имя для отчёта, класс).
TAB_FACTORIES = [
    ("customer", CustomerTab),
    ("cargo", CargoTab),
    ("route", RouteTab),
    ("driver", DriverTab),
    ("vehicle", VehicleTab),
    ("price", PriceTab),
]

#: Ключи get_data() каждой вкладки — ровно те, что читает сборка данных.
EXPECTED_KEYS = {
    CustomerTab: {"number", "date", "name"},
    CargoTab: {"vehicles"},
    RouteTab: {
        "route", "shippers", "consignees",
        "loading_date", "loading_time_from", "loading_time_to",
        "unloading_date", "unloading_time_from", "unloading_time_to",
    },
    DriverTab: {"full_name"},
    VehicleTab: {
        "tractor_brand", "tractor_plate", "trailer_brand", "trailer_plate",
    },
    PriceTab: {
        "carrier_type", "amount_without_vat", "amount_with_vat",
        "vat_rate", "vat_rate_num", "special_conditions",
    },
}

#: Образец заполнения для каждой вкладки: то, что мог бы дать распознаватель.
SAMPLE_DATA = {
    CustomerTab: {
        "number": "ЛР-2026-17",
        "date": "2026-09-24",
        "name": "ООО «Ромашка»",
    },
    CargoTab: {
        "vehicles": [{"brand_model": "JETOUR T2", "vin": VIN_1}],
    },
    RouteTab: {
        "route": "Москва - Казань",
        "shippers": [
            {"name": "ООО «Склад Север»",
             "address": "г. Москва, ул. Складская, д. 1"},
        ],
        "consignees": [
            {"name": "ООО «Приёмка»", "address": "г. Казань, ул. Приёмная, д. 3"},
        ],
        "loading_date": "2026-09-26",
        "loading_time_from": "08:00",
        "loading_time_to": "20:00",
        "unloading_date": "2026-10-01",
        "unloading_time_from": "09:00",
        "unloading_time_to": "18:00",
    },
    DriverTab: {"full_name": "Иванов Иван Иванович"},
    VehicleTab: {
        "tractor_brand": "DAF XF 95.430",
        "tractor_plate": "М342СА761",
        "trailer_brand": "KRONE SD",
        "trailer_plate": "ВК123478",
    },
    PriceTab: {
        "carrier_type": "ООО",
        "amount_without_vat": AMOUNT_WITHOUT_VAT,
        "vat_rate": "22%",
        "special_conditions": "Простой не более 24 часов",
    },
}

#: Поля-значения по умолчанию: их clear() не обнуляет, а возвращает к норме.
#: Даты показывают сегодняшний день (выгрузка — с запасом в несколько дней),
#: время — окно из бланка, ставка НДС — 22%.
DEFAULT_VALUE_KEYS = {
    "date", "name", "loading_date", "unloading_date",
    "loading_time_from", "loading_time_to",
    "unloading_time_from", "unloading_time_to",
    "carrier_type", "vat_rate", "vat_rate_num",
}


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(params=TAB_FACTORIES, ids=[name for name, _ in TAB_FACTORIES])
def tab(qt_app, request):
    """Свежая вкладка каждого типа."""
    _, factory = request.param
    widget = factory()
    yield widget
    widget.deleteLater()


@pytest.fixture
def quiet_dialogs(monkeypatch):
    """Ни один тест не должен останавливаться на модальном диалоге."""
    for name in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *args, **kwargs: QMessageBox.Ok),
        )


@pytest.fixture
def filled_tabs(qt_app):
    """Все шесть вкладок окна, заполненные как в жизни."""
    widgets = [
        CustomerTab(), CargoTab(), RouteTab(),
        DriverTab(), VehicleTab(), PriceTab(),
    ]
    for widget in widgets:
        widget.fill_data(SAMPLE_DATA[type(widget)])

    yield widgets

    for widget in widgets:
        widget.deleteLater()


# ─────────────────────────────────────────────────────────────
# Вспомогательное
# ─────────────────────────────────────────────────────────────

def _build_from(widgets) -> ContractData:
    """Собирает ContractData из шести вкладок в порядке разделов."""
    customer, cargo, route, driver, vehicle, price = widgets
    return build(customer, cargo, route, driver, vehicle, price)


def _readonly_line_edits(widget) -> list:
    """QLineEdit-ы вкладки, помеченные как «только для чтения»."""
    return [
        child for child in widget.findChildren(QLineEdit)
        if child.isReadOnly()
    ]


# ─────────────────────────────────────────────────────────────
# Общее для всех вкладок
# ─────────────────────────────────────────────────────────────

def test_tab_is_created_without_arguments(qt_app):
    for _, factory in TAB_FACTORIES:
        widget = factory()
        try:
            assert isinstance(widget, QWidget)
        finally:
            widget.deleteLater()


def test_tab_is_qwidget_with_tab_mixin(tab):
    assert isinstance(tab, QWidget)
    assert isinstance(tab, TabMixin)


def test_tab_has_required_api(tab):
    for name in ("get_data", "fill_data", "clear"):
        assert callable(getattr(tab, name)), name


def test_tab_declares_three_signals(tab):
    for name in ("recognize_requested", "create_contract_requested", "clear_requested"):
        assert hasattr(tab, name), name


def test_get_data_returns_dict(tab):
    data = tab.get_data()

    assert isinstance(data, dict)
    assert data  # у каждой вкладки есть хотя бы одно поле


def test_get_data_returns_expected_keys(tab):
    assert set(tab.get_data()) == EXPECTED_KEYS[type(tab)]


def test_fill_data_fills_fields(tab):
    """fill_data(sample) заполняет вкладку значениями образца."""
    sample = SAMPLE_DATA[type(tab)]
    before = tab.get_data()

    tab.fill_data(sample)
    after = tab.get_data()

    assert after != before, f"{type(tab).__name__}: fill_data ничего не изменил"
    for key, value in sample.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            assert after[key] == pytest.approx(value), key
        else:
            assert after[key] == value, key


def test_fill_data_with_empty_dict_changes_nothing(tab):
    tab.fill_data(SAMPLE_DATA[type(tab)])
    before = tab.get_data()

    tab.fill_data({})

    assert tab.get_data() == before


def test_clear_returns_fresh_state(qt_app, tab):
    """После clear() вкладка выглядит как только что созданная."""
    tab.fill_data(SAMPLE_DATA[type(tab)])
    tab.clear()

    fresh = type(tab)()
    try:
        assert tab.get_data() == fresh.get_data()
    finally:
        fresh.deleteLater()


def test_clear_empties_text_fields(tab):
    """Текстовые поля вкладки после clear() пусты (кроме значений по умолчанию)."""
    tab.fill_data(SAMPLE_DATA[type(tab)])
    tab.clear()

    for key, value in tab.get_data().items():
        if key in DEFAULT_VALUE_KEYS:
            continue
        if isinstance(value, str):
            assert value == "", f"{type(tab).__name__}: поле {key} не очищено"
        elif isinstance(value, list):
            assert value == [], f"{type(tab).__name__}: список {key} не очищен"


def test_tab_has_action_buttons(tab):
    assert isinstance(tab.btn_create_contract, QPushButton)
    assert tab.btn_create_contract.text() == "Создать договор"
    assert isinstance(tab.btn_clear_form, QPushButton)
    assert tab.btn_clear_form.text() == "Очистить форму"


def test_action_panel_is_action_bar(tab):
    assert isinstance(tab._tab_actions, QFrame)
    assert tab._tab_actions.objectName() == "actionBar"


def test_create_button_emits_signal(tab):
    spy = QSignalSpy(tab.create_contract_requested)

    tab.btn_create_contract.click()

    assert len(spy) == 1


def test_clear_button_emits_signal(tab):
    spy = QSignalSpy(tab.clear_requested)

    tab.btn_clear_form.click()

    assert len(spy) == 1


def test_action_buttons_do_not_change_fields(tab):
    """Кнопки только сообщают о намерении: чистит и создаёт окно, не вкладка."""
    tab.fill_data(SAMPLE_DATA[type(tab)])
    before = tab.get_data()

    tab.btn_clear_form.click()
    tab.btn_create_contract.click()

    assert tab.get_data() == before


def test_recognition_panel_relays_signal(tab):
    assert isinstance(tab.recognition_panel, RecognitionPanel)
    spy = QSignalSpy(tab.recognize_requested)

    tab.recognition_panel.recognize_requested.emit("текст документа")

    assert len(spy) == 1
    assert spy[0][0] == "текст документа"


def test_tab_has_recognition_panel_at_top(tab):
    layout = tab.layout()
    scroll = layout.itemAt(0).widget()
    content = scroll.widget()
    first = content.layout().itemAt(0).widget()

    assert first is tab.recognition_panel


def test_tab_connect_does_not_use_lambda(tab):
    """
    В connect — только метод класса (грабли 2B.7).

    lambda в connect создаёт цикл ссылок Python ↔ Qt и роняет процесс
    при завершении.
    """
    source = inspect.getsource(type(tab).__init__)

    assert "lambda" not in source, f"{type(tab).__name__}: lambda в connect"


def test_tab_actions_panel_is_last_widget(tab):
    """Панель действий внизу: она последняя в layout вкладки."""
    layout = tab.layout()
    assert layout is not None

    last = layout.itemAt(layout.count() - 1).widget()
    assert last is tab._tab_actions


# ─────────────────────────────────────────────────────────────
# «Заказчик»
# ─────────────────────────────────────────────────────────────

def test_customer_has_default_customer_name(qt_app):
    """Заказчик этой заявки фиксирован — поле заполнено сразу."""
    tab = CustomerTab()

    assert tab.get_data()["name"] == DEFAULT_CUSTOMER_NAME


def test_customer_fill_and_read_back(qt_app):
    tab = CustomerTab()

    tab.fill_data({"number": "ЛР-2026-17", "date": "2026-09-23", "name": "ООО «Ромашка»"})

    assert tab.get_data()["number"] == "ЛР-2026-17"
    assert tab.get_data()["date"] == "2026-09-23"
    assert tab.get_data()["name"] == "ООО «Ромашка»"


def test_customer_date_is_iso(qt_app):
    """Дата отдаётся в ISO: её читает сборка данных и генератор."""
    tab = CustomerTab()

    tab.fill_data({"date": "24.09.2026"})

    assert tab.get_data()["date"] == "2026-09-24"


def test_customer_empty_name_keeps_manual_input(qt_app):
    """Пустое значение распознавания не стирает введённое наименование."""
    tab = CustomerTab()
    tab.fill_data({"name": "ООО «Ромашка»"})

    tab.fill_data({"name": "   "})

    assert tab.get_data()["name"] == "ООО «Ромашка»"


def test_customer_name_accepts_full_name_key(qt_app):
    """Распознавание отдаёт реквизиты заказчика ключами ContractData."""
    tab = CustomerTab()

    tab.fill_data({"full_name": "ООО «Ромашка»"})

    assert tab.get_data()["name"] == "ООО «Ромашка»"


def test_customer_clear_resets_number_date_and_name(qt_app):
    tab = CustomerTab()
    tab.fill_data({"number": "ЛР-2026-17", "date": "2026-09-23", "name": "ООО «Ромашка»"})

    tab.clear()

    assert tab.get_data()["number"] == ""
    assert tab.get_data()["date"] == QDate.currentDate().toString("yyyy-MM-dd")
    assert tab.get_data()["name"] == DEFAULT_CUSTOMER_NAME


# ─────────────────────────────────────────────────────────────
# «Груз»
# ─────────────────────────────────────────────────────────────

def test_cargo_has_three_columns_with_comma_in_header(qt_app):
    """Заголовок второй колонки — «Марка, модель» (в бланке с запятой)."""
    tab = CargoTab()

    assert tab.vehicles_table.columnCount() == 3
    headers = [
        tab.vehicles_table.horizontalHeaderItem(i).text()
        for i in range(tab.vehicles_table.columnCount())
    ]
    assert headers == ["№", "Марка, модель", "VIN"]


def test_cargo_max_cars_matches_data_module(qt_app):
    """Ограничение вкладки совпадает с тем, что читает сборка данных."""
    assert cargo_tab_module.MAX_CARS == data_module.MAX_CARS == 12


def test_cargo_starts_with_min_rows(qt_app):
    tab = CargoTab()

    assert tab.vehicles_table.rowCount() == 3
    assert tab.get_data()["vehicles"] == []


def test_cargo_fill_and_read_back(qt_app):
    tab = CargoTab()

    tab.fill_data({"vehicles": [{"brand_model": "JETOUR T2", "vin": VIN_1}]})

    assert tab.get_data()["vehicles"] == [{"brand_model": "JETOUR T2", "vin": VIN_1}]


def test_cargo_row_can_hold_only_brand(qt_app):
    """Строка с одной маркой тоже данные: VIN бывает не распознан."""
    tab = CargoTab()

    tab.fill_data({"vehicles": [{"brand_model": "Lada Vesta", "vin": ""}]})

    assert tab.get_data()["vehicles"] == [{"brand_model": "Lada Vesta", "vin": ""}]


def test_cargo_fill_over_limit_is_truncated(qt_app):
    """Больше 12 машин в бланк не помещается: лишние отбрасываются."""
    tab = CargoTab()
    vehicles = [{"brand_model": f"Машина {i}", "vin": VIN_1} for i in range(20)]

    tab.fill_data({"vehicles": vehicles})

    assert len(tab.get_data()["vehicles"]) == data_module.MAX_CARS
    assert tab.get_data()["vehicles"][0]["brand_model"] == "Машина 0"


def test_cargo_add_row(qt_app):
    tab = CargoTab()

    tab.btn_add_vehicle.click()

    assert tab.vehicles_table.rowCount() == 4


def test_cargo_add_row_stops_at_max_cars(qt_app, quiet_dialogs):
    """Сверх 12 машин строк не появляется — бланк их не вмещает."""
    tab = CargoTab()

    for _ in range(data_module.MAX_CARS + 3):
        tab.btn_add_vehicle.click()

    assert tab.vehicles_table.rowCount() == data_module.MAX_CARS


def test_cargo_remove_row(qt_app, quiet_dialogs):
    tab = CargoTab()
    tab.fill_data({"vehicles": [{"brand_model": "JETOUR T2", "vin": VIN_1}]})
    tab.vehicles_table.setCurrentCell(0, 1)

    tab.btn_remove_vehicle.click()

    assert tab.vehicles_table.rowCount() == 0
    assert tab.get_data()["vehicles"] == []


def test_cargo_clear_returns_min_rows(qt_app):
    tab = CargoTab()
    tab.fill_data({"vehicles": [{"brand_model": "JETOUR T2", "vin": VIN_1}]})

    tab.clear()

    assert tab.vehicles_table.rowCount() == 3
    assert tab.get_data()["vehicles"] == []


# ─────────────────────────────────────────────────────────────
# «Маршрут»
# ─────────────────────────────────────────────────────────────

def test_route_tables_have_two_columns(qt_app):
    tab = RouteTab()

    for table in (tab.shippers_table, tab.consignees_table):
        assert isinstance(table, QTableWidget)
        assert table.columnCount() == 2
        headers = [
            table.horizontalHeaderItem(i).text()
            for i in range(table.columnCount())
        ]
        assert headers == ["Наименование", "Адрес"]


def test_route_tables_start_with_one_row(qt_app):
    tab = RouteTab()

    assert tab.shippers_table.rowCount() == route_tab_module.MIN_ROWS == 1
    assert tab.consignees_table.rowCount() == route_tab_module.MIN_ROWS == 1
    assert tab.get_data()["shippers"] == []
    assert tab.get_data()["consignees"] == []


def test_route_max_points_matches_data_module():
    assert route_tab_module.MAX_POINTS == data_module.MAX_POINTS == 10


def test_route_fill_and_read_back(qt_app):
    tab = RouteTab()
    sample = SAMPLE_DATA[RouteTab]

    tab.fill_data(sample)
    data = tab.get_data()

    assert data["route"] == "Москва - Казань"
    assert data["loading_date"] == "2026-09-26"
    assert data["loading_time_from"] == "08:00"
    assert data["loading_time_to"] == "20:00"
    assert data["unloading_date"] == "2026-10-01"
    assert data["unloading_time_from"] == "09:00"
    assert data["unloading_time_to"] == "18:00"


def test_route_shippers_and_consignees_carry_name_and_address(qt_app):
    tab = RouteTab()

    tab.fill_data(SAMPLE_DATA[RouteTab])
    data = tab.get_data()

    assert data["shippers"] == [
        {"name": "ООО «Склад Север»", "address": "г. Москва, ул. Складская, д. 1"},
    ]
    assert data["consignees"] == [
        {"name": "ООО «Приёмка»", "address": "г. Казань, ул. Приёмная, д. 3"},
    ]
    # Ключи точки — ровно те, что читает сборка данных.
    assert set(data["shippers"][0]) == {"name", "address"}


def test_route_empty_rows_are_not_returned(qt_app):
    """Пустая строка таблицы точкой не считается."""
    tab = RouteTab()
    tab.fill_data(SAMPLE_DATA[RouteTab])

    tab.btn_add_shipper.click()
    tab.btn_add_consignee.click()

    assert len(tab.get_data()["shippers"]) == 1
    assert len(tab.get_data()["consignees"]) == 1


def test_route_row_with_address_only_is_kept(qt_app):
    """Наименование может не распознаться: адрес всё равно данные."""
    tab = RouteTab()
    tab.fill_data({"shippers": [{"name": "", "address": "г. Москва, ул. Южная, д. 2"}]})

    assert tab.get_data()["shippers"] == [
        {"name": "", "address": "г. Москва, ул. Южная, д. 2"},
    ]


def test_route_add_row_in_both_tables(qt_app):
    tab = RouteTab()

    tab.btn_add_shipper.click()
    tab.btn_add_consignee.click()

    assert tab.shippers_table.rowCount() == 2
    assert tab.consignees_table.rowCount() == 2


def test_route_add_row_stops_at_max_points(qt_app, quiet_dialogs):
    tab = RouteTab()

    for _ in range(data_module.MAX_POINTS + 3):
        tab.btn_add_shipper.click()
        tab.btn_add_consignee.click()

    assert tab.shippers_table.rowCount() == data_module.MAX_POINTS
    assert tab.consignees_table.rowCount() == data_module.MAX_POINTS


def test_route_remove_row_in_both_tables(qt_app, quiet_dialogs):
    tab = RouteTab()
    tab.fill_data(SAMPLE_DATA[RouteTab])
    tab.btn_add_shipper.click()
    tab.btn_add_consignee.click()

    tab.shippers_table.setCurrentCell(1, 0)
    tab.btn_remove_shipper.click()
    tab.consignees_table.setCurrentCell(1, 0)
    tab.btn_remove_consignee.click()

    assert tab.shippers_table.rowCount() == 1
    assert tab.consignees_table.rowCount() == 1


def test_route_last_row_cannot_be_removed(qt_app, quiet_dialogs):
    """Последняя строка остаётся: пустая таблица точку не печатает."""
    tab = RouteTab()
    tab.shippers_table.setCurrentCell(0, 0)

    tab.btn_remove_shipper.click()

    assert tab.shippers_table.rowCount() == 1


def test_route_fill_over_limit_is_truncated(qt_app):
    """Точек больше 10 в бланк не помещается: лишние отбрасываются."""
    tab = RouteTab()
    shippers = [
        {"name": f"Склад {i}", "address": f"г. Москва, ул. Складская, д. {i}"}
        for i in range(15)
    ]

    tab.fill_data({"shippers": shippers})

    assert tab.shippers_table.rowCount() == data_module.MAX_POINTS
    assert tab.get_data()["shippers"][0]["name"] == "Склад 0"


def test_route_empty_points_list_keeps_manual_input(qt_app):
    """Пустой список — «точек не распознано», а не «стереть введённое»."""
    tab = RouteTab()
    tab.fill_data(SAMPLE_DATA[RouteTab])

    tab.fill_data({"shippers": [], "consignees": []})

    assert len(tab.get_data()["shippers"]) == 1
    assert len(tab.get_data()["consignees"]) == 1


def test_route_default_times(qt_app):
    """Окно погрузки — 08:00-20:00, выгрузки — 09:00-18:00, как в бланке."""
    tab = RouteTab()
    data = tab.get_data()

    assert data["loading_time_from"] == "08:00"
    assert data["loading_time_to"] == "20:00"
    assert data["unloading_time_from"] == "09:00"
    assert data["unloading_time_to"] == "18:00"


def test_route_time_is_set_from_string(qt_app):
    tab = RouteTab()

    tab.fill_data({"loading_time_from": "06:30", "unloading_time_to": "23:45"})

    assert tab.get_data()["loading_time_from"] == "06:30"
    assert tab.get_data()["unloading_time_to"] == "23:45"


def test_route_broken_time_is_ignored(qt_app):
    """Нераспознанное время не трогает поле и не поднимает исключение."""
    tab = RouteTab()

    tab.fill_data({"loading_time_from": "утро"})

    assert tab.get_data()["loading_time_from"] == "08:00"


def test_route_clear_resets_tables_and_plan(qt_app):
    tab = RouteTab()
    tab.fill_data(SAMPLE_DATA[RouteTab])

    tab.clear()
    data = tab.get_data()

    assert data["route"] == ""
    assert data["shippers"] == []
    assert data["consignees"] == []
    assert tab.shippers_table.rowCount() == 1
    assert tab.consignees_table.rowCount() == 1
    assert data["loading_date"] == QDate.currentDate().toString("yyyy-MM-dd")
    assert data["loading_time_from"] == "08:00"
    assert data["unloading_time_to"] == "18:00"


def test_route_times_are_hh_mm(qt_app):
    tab = RouteTab()

    assert isinstance(tab.loading_time_from, type(tab.unloading_time_from))
    assert tab.loading_time_from.displayFormat() == "HH:mm"
    assert tab.unloading_time_to.displayFormat() == "HH:mm"
    # Дата погрузки — с календарём и без «колёсика».
    assert isinstance(tab.loading_date.date(), QDate)
    assert tab.loading_date.date() == QDate.currentDate()
    assert QTime.fromString(tab.get_data()["loading_time_to"], "HH:mm").isValid()


# ─────────────────────────────────────────────────────────────
# «Водитель»
# ─────────────────────────────────────────────────────────────

def test_driver_has_only_full_name(qt_app):
    """В бланке печатается только ФИО — других полей у вкладки нет."""
    tab = DriverTab()

    assert set(tab.get_data()) == {"full_name"}
    for name in (
        "birth_date", "birth_place", "passport_series", "passport_number",
        "passport_issue_date", "passport_issuer", "passport_code",
        "registration_address", "license_series", "license_number",
        "license_issue_date", "license_expiry_date", "license_categories",
        "phone",
    ):
        assert not hasattr(tab, name), f"лишнее поле водителя: {name}"


def test_driver_fill_and_clear(qt_app):
    tab = DriverTab()

    tab.fill_data({"full_name": "Иванов Иван Иванович"})
    assert tab.get_data()["full_name"] == "Иванов Иван Иванович"

    tab.clear()
    assert tab.get_data()["full_name"] == ""


def test_driver_empty_name_keeps_manual_input(qt_app):
    tab = DriverTab()
    tab.fill_data({"full_name": "Иванов Иван Иванович"})

    tab.fill_data({"full_name": "  "})

    assert tab.get_data()["full_name"] == "Иванов Иван Иванович"


# ─────────────────────────────────────────────────────────────
# «ТС»
# ─────────────────────────────────────────────────────────────

def test_vehicle_has_only_four_fields(qt_app):
    """Тип, цвет и год в бланке не печатаются — полей для них нет."""
    tab = VehicleTab()

    assert set(tab.get_data()) == {
        "tractor_brand", "tractor_plate", "trailer_brand", "trailer_plate",
    }
    for name in (
        "tractor_type", "tractor_color", "tractor_year",
        "trailer_type", "trailer_color", "trailer_year",
    ):
        assert not hasattr(tab, name), f"лишнее поле ТС: {name}"


def test_vehicle_fill_and_clear(qt_app):
    tab = VehicleTab()

    tab.fill_data(SAMPLE_DATA[VehicleTab])
    data = tab.get_data()
    assert data["tractor_brand"] == "DAF XF 95.430"
    assert data["tractor_plate"] == "М342СА761"
    assert data["trailer_brand"] == "KRONE SD"
    assert data["trailer_plate"] == "ВК123478"

    tab.clear()
    assert tab.get_data() == {
        "tractor_brand": "", "tractor_plate": "",
        "trailer_brand": "", "trailer_plate": "",
    }


def test_vehicle_empty_value_keeps_manual_input(qt_app):
    tab = VehicleTab()
    tab.fill_data({"tractor_plate": "М342СА761"})

    tab.fill_data({"tractor_plate": ""})

    assert tab.get_data()["tractor_plate"] == "М342СА761"


# ─────────────────────────────────────────────────────────────
# «Стоимость»
# ─────────────────────────────────────────────────────────────

def test_price_defaults(qt_app):
    """По умолчанию — ООО и ставка 22%."""
    tab = PriceTab()

    assert isinstance(tab.carrier_type, QComboBox)
    assert [tab.carrier_type.itemText(i) for i in range(tab.carrier_type.count())] == [
        "ООО", "ИП",
    ]
    assert tab.carrier_type.currentText() == "ООО"
    assert isinstance(tab.vat_rate, QComboBox)
    assert [tab.vat_rate.itemText(i) for i in range(tab.vat_rate.count())] == [
        "22%", "20%", "10%", "0%",
    ]
    assert tab.vat_rate.currentText() == "22%"
    assert tab.get_data()["vat_rate_num"] == 22.0
    assert isinstance(tab.amount_without_vat, QDoubleSpinBox)
    assert tab.amount_without_vat.isEnabled()


def test_price_calculated_fields_are_readonly(qt_app):
    tab = PriceTab()

    assert tab.amount_with_vat.isReadOnly()
    assert tab.vat_amount.isReadOnly()
    readonly = _readonly_line_edits(tab)
    assert tab.amount_with_vat in readonly
    assert tab.vat_amount in readonly


def test_price_amounts_are_calculated_for_ooo(qt_app):
    """ООО: 221 099,18 + 22% = 48 641,82 → 269 741,00."""
    tab = PriceTab()

    tab.amount_without_vat.setValue(AMOUNT_WITHOUT_VAT)

    assert tab.get_data()["amount_with_vat"] == pytest.approx(AMOUNT_WITH_VAT)
    assert tab.vat_amount_value() == pytest.approx(VAT_AMOUNT)
    assert "269741.00" in tab.amount_with_vat.text()
    assert "48641.82" in tab.vat_amount.text()


def test_price_recalculates_on_amount_change(qt_app):
    tab = PriceTab()
    tab.amount_without_vat.setValue(100000)

    assert tab.get_data()["amount_with_vat"] == pytest.approx(122000.0)
    assert tab.vat_amount_value() == pytest.approx(22000.0)

    tab.amount_without_vat.setValue(200000)

    assert tab.get_data()["amount_with_vat"] == pytest.approx(244000.0)
    assert tab.vat_amount_value() == pytest.approx(44000.0)


def test_price_recalculates_on_vat_rate_change(qt_app):
    tab = PriceTab()
    tab.amount_without_vat.setValue(100000)

    tab.vat_rate.setCurrentText("20%")
    assert tab.get_data()["amount_with_vat"] == pytest.approx(120000.0)
    assert tab.vat_amount_value() == pytest.approx(20000.0)

    tab.vat_rate.setCurrentText("10%")
    assert tab.get_data()["amount_with_vat"] == pytest.approx(110000.0)
    assert tab.vat_amount_value() == pytest.approx(10000.0)


def test_price_ip_disables_vat_rate_and_zeroes_it(qt_app):
    """ИП: стоимость без НДС — ставка заблокирована и равна «0%»."""
    tab = PriceTab()
    tab.amount_without_vat.setValue(AMOUNT_WITHOUT_VAT)

    tab.carrier_type.setCurrentText("ИП")

    assert tab.vat_rate.currentText() == "0%"
    assert not tab.vat_rate.isEnabled()
    assert tab.carrier_type.currentText() == "ИП"
    # Сумма без НДС остаётся редактируемой — её и вводит пользователь.
    assert tab.amount_without_vat.isEnabled()


def test_price_ip_has_no_amounts_with_vat(qt_app):
    tab = PriceTab()
    tab.amount_without_vat.setValue(AMOUNT_WITHOUT_VAT)

    tab.carrier_type.setCurrentText("ИП")
    data = tab.get_data()

    assert data["amount_with_vat"] == ""
    assert tab.amount_with_vat.text() == ""
    assert tab.vat_amount.text() == ""
    assert tab.amount_with_vat_value() is None
    assert tab.vat_amount_value() is None


def test_price_ip_get_data_gives_zero_vat_rate(qt_app):
    tab = PriceTab()
    tab.carrier_type.setCurrentText("ИП")
    data = tab.get_data()

    assert data["carrier_type"] == "ИП"
    assert data["vat_rate"] == "0%"
    assert data["vat_rate_num"] == 0


def test_price_back_to_ooo_enables_vat_rate_and_restores_default(qt_app):
    """Возврат к ООО: ставка снова доступна, нулевая сменяется на 22%."""
    tab = PriceTab()
    tab.amount_without_vat.setValue(AMOUNT_WITHOUT_VAT)
    tab.carrier_type.setCurrentText("ИП")

    tab.carrier_type.setCurrentText("ООО")

    assert tab.vat_rate.isEnabled()
    assert tab.vat_rate.currentText() == "22%"
    assert tab.get_data()["amount_with_vat"] == pytest.approx(AMOUNT_WITH_VAT)
    assert tab.vat_amount_value() == pytest.approx(VAT_AMOUNT)


def test_price_fill_data_maps_contract_keys(qt_app):
    """Распознавание отдаёт суммы ключами ContractData — они тоже принимаются."""
    tab = PriceTab()

    tab.fill_data({"price_without_vat": 150000.0, "vat_rate": "20%"})

    data = tab.get_data()
    assert data["amount_without_vat"] == pytest.approx(150000.0)
    assert data["vat_rate"] == "20%"
    assert data["amount_with_vat"] == pytest.approx(180000.0)


def test_price_fill_data_switches_to_ip(qt_app):
    tab = PriceTab()
    tab.amount_without_vat.setValue(100000)

    tab.fill_data({"carrier_type": "ИП", "amount_without_vat": 135833.0})

    data = tab.get_data()
    assert data["carrier_type"] == "ИП"
    assert data["amount_without_vat"] == pytest.approx(135833.0)
    assert data["vat_rate_num"] == 0
    assert data["amount_with_vat"] == ""
    assert not tab.vat_rate.isEnabled()


def test_price_zero_amount_does_not_erase_manual_input(qt_app):
    """Ноль у промпта — «суммы в документе не было»: введённое не стираем."""
    tab = PriceTab()
    tab.amount_without_vat.setValue(100000)

    tab.fill_data({"amount_without_vat": 0, "sum_total": 0.0})

    assert tab.get_data()["amount_without_vat"] == pytest.approx(100000.0)


def test_price_special_conditions_round_trip(qt_app):
    tab = PriceTab()

    tab.fill_data({"special_conditions": "Простой не более 24 часов"})
    assert tab.get_data()["special_conditions"] == "Простой не более 24 часов"

    tab.clear()
    assert tab.get_data()["special_conditions"] == ""


def test_price_clear_resets_to_ooo_22(qt_app):
    tab = PriceTab()
    tab.fill_data({
        "carrier_type": "ИП",
        "amount_without_vat": AMOUNT_WITHOUT_VAT,
        "special_conditions": "Простой не более 24 часов",
    })

    tab.clear()
    data = tab.get_data()

    assert data["carrier_type"] == "ООО"
    assert data["vat_rate"] == "22%"
    assert data["vat_rate_num"] == 22.0
    assert data["amount_without_vat"] == 0
    assert data["special_conditions"] == ""
    assert tab.vat_rate.isEnabled()


# ─────────────────────────────────────────────────────────────
# Вкладки и сборка данных: полный сценарий
# ─────────────────────────────────────────────────────────────

def test_tabs_data_maps_expected_fields(filled_tabs):
    """Собранные из настоящих вкладок данные раскладываются как ждёт бланк."""
    cd = _build_from(filled_tabs)

    assert isinstance(cd, ContractData)
    assert cd.contract["number"] == "ЛР-2026-17"
    assert cd.contract["date"] == "2026-09-24"
    assert cd.contract["route"] == "Москва - Казань"
    assert cd.contract["loading_date"] == "2026-09-26"
    assert cd.contract["loading_time_from"] == "08:00"
    assert cd.contract["unloading_date"] == "2026-10-01"
    assert cd.contract["price_without_vat"] == pytest.approx(AMOUNT_WITHOUT_VAT)
    assert cd.contract["vat_rate_num"] == 22.0
    assert cd.contract["special_conditions"] == "Простой не более 24 часов"

    assert cd.customer["full_name"] == "ООО «Ромашка»"
    assert cd.customer["short_name"] == cd.customer["full_name"]
    assert cd.driver == {"full_name": "Иванов Иван Иванович"}
    assert cd.tractor == {"brand_model": "DAF XF 95.430", "plate_number": "М342СА761"}
    assert cd.trailer == {"brand_model": "KRONE SD", "plate_number": "ВК123478"}
    assert len(cd.vehicles) == 1

    # Точки маршрута: наименование, адрес и общее окно времени раздела.
    assert cd.contract["loadings"][0]["name"] == "ООО «Склад Север»"
    assert cd.contract["loadings"][0]["address"] == "г. Москва, ул. Складская, д. 1"
    assert cd.contract["loadings"][0]["time_window"] == "08:00-20:00"
    assert cd.contract["unloadings"][0]["name"] == "ООО «Приёмка»"
    assert cd.contract["unloadings"][0]["time_window"] == "09:00-18:00"


def test_tabs_data_passes_logistiks_rus_validator(filled_tabs):
    """Данных с вкладок достаточно, чтобы валидатор не нашёл ни ошибок, ни замечаний."""
    from core.contracts.logistiks_rus.validator import LogistiksRusValidator

    report = LogistiksRusValidator().check(_build_from(filled_tabs))

    assert report.errors == [], report.errors
    assert report.warnings == [], report.warnings


def test_tabs_data_ip_variant_passes_validator(filled_tabs):
    """Вариант ИП: одна сумма без НДС — валидатор тоже доволен."""
    from core.contracts.logistiks_rus.validator import LogistiksRusValidator

    price_tab = filled_tabs[5]
    price_tab.carrier_type.setCurrentText("ИП")
    cd = _build_from(filled_tabs)

    assert cd.contract["carrier_type"] == "ИП"
    assert cd.contract["vat_rate_num"] == 0.0
    assert "price_with_vat" not in cd.contract
    assert cd.contract["price_without_vat"] == pytest.approx(AMOUNT_WITHOUT_VAT)

    report = LogistiksRusValidator().check(cd)
    assert report.errors == [], report.errors
    assert report.warnings == [], report.warnings


def test_customer_default_name_goes_to_contract(qt_app):
    """Заказчик по умолчанию попадает в данные: пользователь его не вводит."""
    tab = CustomerTab()
    try:
        cd = build(tab, None, None, None, None, None)
    finally:
        tab.deleteLater()

    assert cd.customer["full_name"] == DEFAULT_CUSTOMER_NAME
    assert cd.customer["short_name"] == DEFAULT_CUSTOMER_NAME
