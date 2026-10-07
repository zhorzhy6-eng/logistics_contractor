#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты шести вкладок Формики (ЭТАП 3.1.B.2).

Проверяется то, на что опирается сборка данных (ui/windows/formika/data.py):
набор ключей get_data(), заполнение fill_data(), очистка clear(),
сигналы вкладки и панель действий, а также расчёты вкладки «Стоимость»
и ограничение таблицы груза в 12 машин.

Qt — в offscreen-режиме. Модальные диалоги подменяются: ни один тест
не должен останавливаться на QMessageBox.
"""

import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtCore import QDate, QTime  # noqa: E402
from PyQt5.QtTest import QSignalSpy  # noqa: E402
from PyQt5.QtWidgets import QApplication, QFrame, QMessageBox, QPushButton  # noqa: E402

from core.contract_data import ContractData  # noqa: E402
from ui.tabs.base_tab import TabMixin  # noqa: E402
from ui.widgets import RecognitionPanel  # noqa: E402
from ui.windows.formika.data import (  # noqa: E402
    DRIVER_FIELDS, MAX_CARS, collect_formika_data,
)
from ui.windows.formika.tabs import (  # noqa: E402
    CargoTab, CustomerTab, DriverTab, PriceTab, RouteTab, VehicleTab,
)

#: VIN из conftest (17 символов, ISO 3779).
VIN_OK = "EC3TEUMB0T0002608"

#: Все вкладки окна: (имя для отчёта, класс).
TAB_FACTORIES = [
    ("customer", CustomerTab),
    ("cargo", CargoTab),
    ("route", RouteTab),
    ("driver", DriverTab),
    ("vehicle", VehicleTab),
    ("price", PriceTab),
]


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


# ─────────────────────────────────────────────────────────────
# Вспомогательное
# ─────────────────────────────────────────────────────────────

def _fill_cargo_table(tab: CargoTab, vehicles) -> None:
    """Кладёт машины прямо в таблицу (как это сделал бы пользователь)."""
    tab.vehicles_table.setRowCount(len(vehicles))
    for row, brand in enumerate(vehicles):
        tab._init_row(row)
        tab.vehicles_table.item(row, 1).setText(brand)
        tab.vehicles_table.item(row, 2).setText(VIN_OK)


def _readonly_line_edits(widget) -> list:
    """QLineEdit-ы вкладки, помеченные как «только для чтения»."""
    from PyQt5.QtWidgets import QLineEdit

    return [
        child for child in widget.findChildren(QLineEdit)
        if child.isReadOnly()
    ]


# ─────────────────────────────────────────────────────────────
# Общее для всех вкладок
# ─────────────────────────────────────────────────────────────

def test_tab_is_qwidget_with_tab_mixin(tab):
    from PyQt5.QtWidgets import QWidget

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


def test_fill_data_with_empty_dict_changes_nothing(tab):
    before = tab.get_data()

    tab.fill_data({})

    assert tab.get_data() == before


#: Поля-значения по умолчанию: их clear() не обнуляет, а возвращает к норме.
#: Даты показывают сегодняшний день, ставка НДС — 22%, срок оплаты — 10 дней.
DEFAULT_VALUE_KEYS = {
    "date", "birth_date", "passport_issue_date", "license_issue_date",
    "license_expiry_date", "loading_plan_date", "unloading_plan_date",
    "loading_plan_time_from", "loading_plan_time_to",
    "vat_rate", "vat_rate_num", "payment_days", "amount",
    "amount_without_vat", "amount_with_vat",
}


def test_clear_empties_text_fields(tab):
    """Текстовые поля вкладки после clear() пусты."""
    tab.fill_data(tab.get_data())  # данные не меняются, но путь заполнения пройден
    tab.clear()

    for key, value in tab.get_data().items():
        if key in DEFAULT_VALUE_KEYS:
            continue  # у этих полей после clear() значение по умолчанию
        if isinstance(value, str):
            assert value == "", f"{type(tab).__name__}: поле {key} не очищено"
        elif isinstance(value, list):
            assert value == [], f"{type(tab).__name__}: список {key} не очищен"


def test_clear_returns_fresh_state(qt_app, tab):
    """После clear() вкладка выглядит как только что созданная."""
    tab.fill_data(tab.get_data())
    tab.clear()

    fresh = type(tab)()
    try:
        cleaned = {k: v for k, v in tab.get_data().items() if k in DEFAULT_VALUE_KEYS}
        expected = {k: v for k, v in fresh.get_data().items() if k in DEFAULT_VALUE_KEYS}
        assert cleaned == expected
    finally:
        fresh.deleteLater()


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


def test_clear_button_does_not_clear_fields(tab):
    """Кнопка только сообщает о намерении: чистит окно, а не вкладка."""
    if isinstance(tab, CargoTab):
        _fill_cargo_table(tab, ["Lada Vesta"])
    else:
        tab.fill_data(tab.get_data())
    before = tab.get_data()

    tab.btn_clear_form.click()

    assert tab.get_data() == before


def test_recognition_panel_relays_signal(tab):
    assert isinstance(tab.recognition_panel, RecognitionPanel)
    spy = QSignalSpy(tab.recognize_requested)

    tab.recognition_panel.recognize_requested.emit("текст документа")

    assert len(spy) == 1
    assert spy[0][0] == "текст документа"


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

def test_customer_keys(qt_app):
    data = CustomerTab().get_data()

    assert set(data) == {"number", "date"}


def test_customer_fill_and_read_back(qt_app):
    tab = CustomerTab()

    tab.fill_data({"number": "ТЛ-447", "date": "2026-09-23"})

    assert tab.get_data()["number"] == "ТЛ-447"
    assert tab.get_data()["date"] == "2026-09-23"


def test_customer_date_is_iso(qt_app):
    """Дата отдаётся в ISO: её читает сборка данных и генератор."""
    tab = CustomerTab()

    tab.fill_data({"date": "24.09.2026"})

    assert tab.get_data()["date"] == "2026-09-24"


def test_customer_clear_resets_number_and_date(qt_app):
    tab = CustomerTab()
    tab.fill_data({"number": "ТЛ-447", "date": "2026-09-23"})

    tab.clear()

    assert tab.get_data()["number"] == ""
    assert tab.get_data()["date"] == QDate.currentDate().toString("yyyy-MM-dd")


# ─────────────────────────────────────────────────────────────
# «Груз»
# ─────────────────────────────────────────────────────────────

def test_cargo_has_three_columns(qt_app):
    tab = CargoTab()

    assert tab.vehicles_table.columnCount() == 3
    headers = [
        tab.vehicles_table.horizontalHeaderItem(i).text()
        for i in range(tab.vehicles_table.columnCount())
    ]
    assert headers == ["№", "Марка/модель", "VIN"]


def test_cargo_get_data_has_vehicles_key(qt_app):
    assert set(CargoTab().get_data()) == {"vehicles"}


def test_cargo_empty_rows_are_not_returned(qt_app):
    """Пустые строки таблицы в данные не попадают."""
    tab = CargoTab()

    assert tab.get_data()["vehicles"] == []


def test_cargo_fill_and_read_back(qt_app):
    tab = CargoTab()

    tab.fill_data({"vehicles": [{"brand_model": "JETOUR T2", "vin": VIN_OK}]})

    assert tab.get_data()["vehicles"] == [{"brand_model": "JETOUR T2", "vin": VIN_OK}]


def test_cargo_row_can_hold_only_brand(qt_app):
    """Строка с одной маркой тоже данные: VIN бывает не распознан."""
    tab = CargoTab()

    tab.fill_data({"vehicles": [{"brand_model": "Lada Vesta", "vin": ""}]})

    assert tab.get_data()["vehicles"] == [
        {"brand_model": "Lada Vesta", "vin": ""},
    ]


def test_cargo_fill_over_limit_is_truncated(qt_app):
    """Больше 12 машин в бланк не помещается: лишние отбрасываются."""
    tab = CargoTab()
    vehicles = [
        {"brand_model": f"Машина {i}", "vin": VIN_OK} for i in range(20)
    ]

    tab.fill_data({"vehicles": vehicles})

    assert tab.vehicles_table.rowCount() == MAX_CARS
    assert len(tab.get_data()["vehicles"]) == MAX_CARS
    assert tab.get_data()["vehicles"][-1]["brand_model"] == f"Машина {MAX_CARS - 1}"


def test_cargo_add_button_stops_at_limit(qt_app, quiet_dialogs):
    """Кнопка «Добавить ТС» сверх 12 строк новую строку не создаёт."""
    tab = CargoTab()
    while tab.vehicles_table.rowCount() < MAX_CARS:
        tab._on_add_vehicle()
    assert tab.vehicles_table.rowCount() == MAX_CARS

    # Следим за вставкой строк: при достижении предела её быть не должно.
    inserted = QSignalSpy(tab.vehicles_table.model().rowsInserted)

    tab.btn_add_vehicle.click()

    assert tab.vehicles_table.rowCount() == MAX_CARS
    assert len(inserted) == 0


def test_cargo_remove_button_removes_selected_row(qt_app, quiet_dialogs):
    tab = CargoTab()
    _fill_cargo_table(tab, ["Первая", "Вторая", "Третья"])
    tab.vehicles_table.setCurrentCell(1, 1)

    tab.btn_remove_vehicle.click()

    assert tab.vehicles_table.rowCount() == 2
    brands = [v["brand_model"] for v in tab.get_data()["vehicles"]]
    assert brands == ["Первая", "Третья"]


def test_cargo_rows_are_renumbered_after_removal(qt_app, quiet_dialogs):
    tab = CargoTab()
    _fill_cargo_table(tab, ["Первая", "Вторая", "Третья"])
    tab.vehicles_table.setCurrentCell(0, 1)

    tab._on_remove_vehicle()

    numbers = [
        tab.vehicles_table.item(row, 0).text()
        for row in range(tab.vehicles_table.rowCount())
    ]
    assert numbers == ["1", "2"]


def test_cargo_remove_without_selection_is_safe(qt_app, quiet_dialogs):
    """Без выбранной строки кнопка ничего не удаляет и не падает."""
    tab = CargoTab()
    _fill_cargo_table(tab, ["Первая", "Вторая"])
    tab.vehicles_table.clearSelection()
    tab.vehicles_table.setCurrentCell(-1, -1)

    tab._on_remove_vehicle()

    assert tab.vehicles_table.rowCount() == 2


def test_cargo_clear_leaves_empty_rows(qt_app):
    tab = CargoTab()
    tab.fill_data({"vehicles": [{"brand_model": "JETOUR T2", "vin": VIN_OK}]})

    tab.clear()

    assert tab.get_data()["vehicles"] == []
    assert tab.vehicles_table.rowCount() > 0


# ─────────────────────────────────────────────────────────────
# «Маршрут»
# ─────────────────────────────────────────────────────────────

def test_route_keys(qt_app):
    data = RouteTab().get_data()

    assert set(data) == {
        "route",
        "loading_address",
        "unloading_address",
        "loading_plan_date",
        "loading_plan_time_from",
        "loading_plan_time_to",
        "unloading_plan_date",
    }


def test_route_fill_and_read_back(qt_app):
    tab = RouteTab()

    tab.fill_data({
        "route": "Мурманск - Пятигорск",
        "loading_address": "183052, г. Мурманск, пр. Кольский, д. 53",
        "unloading_address": "г. Пятигорск, Бештаугорское шоссе 17",
        "loading_plan_date": "2026-09-24",
    })

    data = tab.get_data()
    assert data["route"] == "Мурманск - Пятигорск"
    assert data["loading_address"] == "183052, г. Мурманск, пр. Кольский, д. 53"
    assert data["unloading_address"] == "г. Пятигорск, Бештаугорское шоссе 17"
    assert data["loading_plan_date"] == "2026-09-24"


def test_route_time_is_hh_mm(qt_app):
    """Время отдаётся строкой «HH:mm» — так его ждёт окно времени погрузки."""
    tab = RouteTab()

    tab.fill_data({"loading_plan_time_from": "09:00", "loading_plan_time_to": "18:00"})

    data = tab.get_data()
    assert data["loading_plan_time_from"] == "09:00"
    assert data["loading_plan_time_to"] == "18:00"


def test_route_time_from_edit_is_converted(qt_app):
    """QTimeEdit с ведущим нулём тоже даёт «HH:mm»."""
    tab = RouteTab()

    tab.loading_plan_time_from.setTime(QTime(7, 5))

    assert tab.get_data()["loading_plan_time_from"] == "07:05"


def test_route_broken_time_does_not_change_field(qt_app):
    tab = RouteTab()
    tab.loading_plan_time_from.setTime(QTime(8, 0))

    tab.fill_data({"loading_plan_time_from": "не время"})

    assert tab.get_data()["loading_plan_time_from"] == "08:00"


def test_route_unloading_plan_date_is_read_only(qt_app):
    """Плановая дата выгрузки — только для отображения."""
    tab = RouteTab()

    assert tab.unloading_plan_date.isReadOnly()
    assert tab.unloading_plan_date.property("readonlyField") is True


def test_route_clear_resets_route_and_addresses(qt_app):
    tab = RouteTab()
    tab.fill_data({
        "route": "Мурманск - Пятигорск",
        "loading_address": "Адрес погрузки",
        "unloading_address": "Адрес выгрузки",
    })

    tab.clear()

    data = tab.get_data()
    assert data["route"] == ""
    assert data["loading_address"] == ""
    assert data["unloading_address"] == ""


# ─────────────────────────────────────────────────────────────
# «Водитель»
# ─────────────────────────────────────────────────────────────

def test_driver_keys_match_data_module(qt_app):
    """Набор ключей вкладки — ровно DRIVER_FIELDS из сборки данных."""
    assert set(DriverTab().get_data()) == set(DRIVER_FIELDS)


def test_driver_series_is_normalized(qt_app):
    """Серия паспорта нормализуется: «6024» → «60 24»."""
    tab = DriverTab()

    tab.passport_series.setText("6024")

    assert tab.get_data()["passport_series"] == "60 24"


def test_driver_series_is_normalized_on_fill(qt_app):
    tab = DriverTab()

    tab.fill_data({"passport_series": "9916", "license_series": "9936"})

    data = tab.get_data()
    assert data["passport_series"] == "99 16"
    assert data["license_series"] == "99 36"


def test_driver_fill_and_read_back(qt_app, driver_data):
    tab = DriverTab()

    tab.fill_data(driver_data)

    data = tab.get_data()
    for field, expected in driver_data.items():
        assert data[field] == expected, field


def test_driver_birth_date_default_is_past(qt_app):
    """По умолчанию дата рождения — 30 лет назад, а не сегодня."""
    tab = DriverTab()

    expected = QDate.currentDate().addYears(-30).toString("yyyy-MM-dd")

    assert tab.get_data()["birth_date"] == expected


def test_driver_clear_empties_all_text_fields(qt_app, driver_data):
    tab = DriverTab()
    tab.fill_data(driver_data)

    tab.clear()

    data = tab.get_data()
    for field in DRIVER_FIELDS:
        if field.endswith("_date"):
            continue  # даты сбрасываются к значениям по умолчанию
        assert data[field] == "", field


# ─────────────────────────────────────────────────────────────
# «ТС»
# ─────────────────────────────────────────────────────────────

def test_vehicle_keys(qt_app):
    data = VehicleTab().get_data()

    assert set(data) == {
        "tractor_brand", "tractor_plate", "tractor_type",
        "tractor_color", "tractor_year",
        "trailer_brand", "trailer_plate", "trailer_color", "trailer_year",
    }


def test_vehicle_fill_and_read_back(qt_app):
    tab = VehicleTab()

    tab.fill_data({
        "tractor_brand": "Foton Auman",
        "tractor_plate": "O844XY196",
        "tractor_type": "Седельный тягач",
        "tractor_color": "Белый",
        "tractor_year": "2023",
        "trailer_brand": "YANGMINDA",
        "trailer_plate": "71ABF18",
        "trailer_color": "Серый",
        "trailer_year": "2020",
    })

    data = tab.get_data()
    assert data["tractor_brand"] == "Foton Auman"
    assert data["tractor_type"] == "Седельный тягач"
    assert data["trailer_brand"] == "YANGMINDA"
    assert data["trailer_year"] == "2020"


def test_vehicle_fill_ignores_empty_values(qt_app):
    tab = VehicleTab()
    tab.fill_data({"tractor_brand": "Foton Auman"})

    tab.fill_data({"tractor_plate": "", "trailer_brand": ""})

    data = tab.get_data()
    assert data["tractor_brand"] == "Foton Auman"
    assert data["tractor_plate"] == ""


def test_vehicle_clear_empties_both_blocks(qt_app):
    tab = VehicleTab()
    tab.fill_data({"tractor_brand": "Foton Auman", "trailer_brand": "YANGMINDA"})

    tab.clear()

    data = tab.get_data()
    assert data["tractor_brand"] == ""
    assert data["trailer_brand"] == ""


# ─────────────────────────────────────────────────────────────
# «Стоимость»
# ─────────────────────────────────────────────────────────────

def test_price_keys(qt_app):
    data = PriceTab().get_data()

    assert set(data) == {
        "amount", "amount_without_vat", "amount_with_vat",
        "vat_rate", "vat_rate_num", "payment_days", "special_conditions",
    }


def test_price_default_vat_rate_is_22(qt_app):
    tab = PriceTab()

    assert tab.get_data()["vat_rate"] == "22%"
    assert tab.get_data()["vat_rate_num"] == 22.0


def test_price_amount_is_user_input(qt_app):
    """Ровно та сумма, что ввёл пользователь: она уже с НДС."""
    tab = PriceTab()

    tab.amount.setValue(122000.0)

    data = tab.get_data()
    assert data["amount"] == 122000.0
    assert data["amount_with_vat"] == 122000.0


def test_price_amount_without_vat_recalculated(qt_app):
    tab = PriceTab()
    tab.amount.setValue(122000.0)

    assert tab.get_data()["amount_without_vat"] == 100000.0


def test_price_vat_rate_change_recalculates(qt_app):
    """Смена ставки НДС пересчитывает сумму без НДС."""
    tab = PriceTab()
    tab.amount.setValue(122000.0)

    tab.vat_rate.setCurrentText("10%")

    data = tab.get_data()
    assert data["vat_rate"] == "10%"
    assert data["vat_rate_num"] == 10.0
    assert data["amount_without_vat"] == 110909.09


def test_price_zero_vat_keeps_amount(qt_app):
    tab = PriceTab()
    tab.amount.setValue(150000.0)

    tab.vat_rate.setCurrentText("0%")

    data = tab.get_data()
    assert data["amount_without_vat"] == 150000.0
    assert data["amount_with_vat"] == 150000.0


def test_price_vat_rates_available(qt_app):
    tab = PriceTab()

    rates = [tab.vat_rate.itemText(i) for i in range(tab.vat_rate.count())]

    assert rates == ["22%", "20%", "10%", "0%"]


def test_price_words_updated_on_input(qt_app):
    """Сумма прописью обновляется при вводе и считается по сумме с НДС."""
    tab = PriceTab()

    tab.amount.setValue(122000.0)

    assert tab.amount_words.text() == "Сто двадцать две тысячи рублей 00 копеек"


def test_price_words_empty_amount(qt_app):
    tab = PriceTab()

    assert tab.amount_words.text() == "Ноль рублей 00 копеек"


def test_price_readonly_fields_are_readonly(qt_app):
    """Расчётные поля вкладки недоступны для ручного ввода."""
    tab = PriceTab()

    assert tab.amount_without_vat.isReadOnly()
    assert tab.amount_with_vat.isReadOnly()
    assert tab.amount_words.isReadOnly()

    for field in (tab.amount_without_vat, tab.amount_with_vat, tab.amount_words):
        assert field.property("readonlyField") is True

    assert len(_readonly_line_edits(tab)) >= 3


def test_price_amount_field_is_editable(qt_app):
    tab = PriceTab()

    assert not tab.amount.isReadOnly()
    assert tab.amount.suffix().strip() == "₽"
    assert tab.amount.decimals() == 2


def test_price_payment_days_default_and_range(qt_app):
    tab = PriceTab()

    assert tab.payment_days.value() == 10
    assert tab.payment_days.minimum() == 0
    assert tab.payment_days.maximum() == 365


def test_price_fill_and_read_back(qt_app):
    tab = PriceTab()

    tab.fill_data({
        "amount": 219966.0,
        "vat_rate": "20%",
        "payment_days": 5,
        "special_conditions": "Оплата по оригиналам накладных.",
    })

    data = tab.get_data()
    assert data["amount"] == 219966.0
    assert data["vat_rate"] == "20%"
    assert data["vat_rate_num"] == 20.0
    assert data["payment_days"] == 5
    assert data["special_conditions"] == "Оплата по оригиналам накладных."
    assert data["amount_without_vat"] == 183305.0


def test_price_fill_accepts_contract_keys(qt_app):
    """Распознавание отдаёт блок contract: price_input тоже принимается."""
    tab = PriceTab()

    tab.fill_data({"price_input": 120000.0, "vat_rate": "22"})

    data = tab.get_data()
    assert data["amount"] == 120000.0
    assert data["vat_rate"] == "22%"


def test_price_clear_resets_defaults(qt_app):
    tab = PriceTab()
    tab.fill_data({
        "amount": 219966.0,
        "vat_rate": "10%",
        "payment_days": 5,
        "special_conditions": "Особые условия",
    })

    tab.clear()

    data = tab.get_data()
    assert data["amount"] == 0.0
    assert data["vat_rate"] == "22%"
    assert data["payment_days"] == 10
    assert data["special_conditions"] == ""


# ─────────────────────────────────────────────────────────────
# Вкладки вместе: то, ради чего они и делались
# ─────────────────────────────────────────────────────────────

def test_all_tabs_feed_collect_formika_data(qt_app):
    """
    Шесть вкладок отдают данные, которые сборка раскладывает по ContractData.

    Это сквозная проверка 3.1.B: вкладки — единственный источник данных
    для генератора Формики.
    """
    customer, cargo, route, driver, vehicle, price = (
        CustomerTab(), CargoTab(), RouteTab(), DriverTab(), VehicleTab(), PriceTab(),
    )

    customer.fill_data({"number": "ТЛ-447", "date": "2026-09-23"})
    cargo.fill_data({"vehicles": [{"brand_model": "JETOUR T2", "vin": VIN_OK}]})
    route.fill_data({
        "route": "Мурманск - Пятигорск",
        "loading_address": "183052, г. Мурманск, пр. Кольский, д. 53",
        "unloading_address": "г. Пятигорск, Бештаугорское шоссе 17",
        "loading_plan_date": "2026-09-24",
        "loading_plan_time_from": "09:00",
        "loading_plan_time_to": "18:00",
    })
    driver.fill_data({"full_name": "Иванов Иван Иванович", "passport_series": "1822"})
    vehicle.fill_data({"tractor_brand": "Foton Auman", "trailer_brand": "YANGMINDA"})
    price.fill_data({"amount": 122000.0, "payment_days": 10})

    data = collect_formika_data({
        "customer": customer, "cargo": cargo, "route": route,
        "driver": driver, "vehicle": vehicle, "price": price,
    })

    assert isinstance(data, ContractData)
    assert data.contract["number"] == "ТЛ-447"
    assert data.contract["date"] == "2026-09-23"
    assert data.contract["route"] == "Мурманск - Пятигорск"
    assert data.contract["loading_plan_time_from"] == "09:00"
    assert data.contract["loading_plan_time_to"] == "18:00"
    assert data.contract["price_input"] == 122000.0
    assert data.contract["price_without_vat"] == 100000.0
    assert data.contract["price_with_vat"] == 122000.0
    assert data.contract["vat_rate"] == "22%"
    assert data.contract["vat_rate_num"] == 22.0
    assert data.contract["payment_days"] == 10

    assert len(data.vehicles) == 1
    assert data.vehicles[0]["brand_model"] == "JETOUR T2"
    assert data.vehicles[0]["vin"] == VIN_OK

    assert data.loadings == [{
        "name": "",
        "address": "183052, г. Мурманск, пр. Кольский, д. 53",
        "date": "2026-09-24",
        "time_window": "09:00-18:00",
    }]
    # Выгрузка получает плановую дату выгрузки вкладки «Маршрут»: в бланке
    # Формики она печатается, хотя и не вводится вручную. Вкладка показывает
    # её по умолчанию как «сегодня + 3 дня»; окна времени у выгрузки нет.
    # Ключ name у точки есть всегда (ШАГ FIX-2.5) — пустой строкой.
    assert data.unloadings == [{
        "name": "",
        "address": "г. Пятигорск, Бештаугорское шоссе 17",
        "date": QDate.currentDate().addDays(3).toString("yyyy-MM-dd"),
        "time_window": "",
    }]

    assert data.driver["full_name"] == "Иванов Иван Иванович"
    assert data.driver["passport_series"] == "18 22"
    assert data.tractor["brand_model"] == "Foton Auman"
    assert data.trailer["brand_model"] == "YANGMINDA"
    assert data.city == ""


def test_tabs_count_filled_fields(qt_app):
    """count_filled_fields из TabMixin работает на новых вкладках."""
    customer = CustomerTab()
    customer.fill_data({"number": "ТЛ-447"})

    filled, total = customer.count_filled_fields()

    assert total == 2
    assert filled == 2  # дата заполнена всегда (сегодняшняя)
    assert CustomerTab().count_filled_fields() == (1, 2)  # только дата
