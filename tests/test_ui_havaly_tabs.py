#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты шести вкладок окна Хавалов (ЭТАП 3.1.E.B.2).

Проверяется то, на что опирается сборка данных
(ui/windows/havaly/data.py::collect_havaly_data):

  * вкладка создаётся без ошибок и отдаёт РОВНО ключи схемы промпта
    (core/prompts/havaly.py) — не свои имена (соглашение ЭТАПА 3.1.E.B.1);
  * fill_data() заполняет поля, а пустые значения не затирают введённое;
  * clear() возвращает вкладку в свежее состояние;
  * панель распознавания сверху, панель действий внизу, lambda в connect нет.

Отдельно — особенности Хавалов: стороны заявки фиксированы и не
редактируются, машина описана пятью полями (марка и модель раздельно, VIN
может быть пустым), у водителя ФИО тремя полями и паспорт серией с номером,
ставка с НДС одна на всю заявку, а ставка НДС в пустой форме ключа не создаёт.

Последний раздел собирает окно из настоящих вкладок и прогоняет через
collect_havaly_data: стык «вкладка → сборщик B.1» проверяется на живых
вкладках, а не на словарях-заглушках.

Qt — в offscreen-режиме. Модальные диалоги подменяются: ни один тест
не должен останавливаться на QMessageBox.

Все данные синтетические, реальных ПДн нет.
"""

import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtCore import QDate, QTime, Qt  # noqa: E402
from PyQt5.QtTest import QSignalSpy  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QDoubleSpinBox, QFrame, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTimeEdit, QWidget,
)

from core.contracts.zayavka.generator import (  # noqa: E402
    CARRIER_NAME,
    CUSTOMER_NAME,
    VEHICLE_KEYS,
    ZAYAVKA_SCHEMA_ORDER,
)
from core.contracts.zayavka.validator import ZayavkaExcelValidator  # noqa: E402
from core.prompts.havaly import PROMPT  # noqa: E402
from ui.tabs.base_tab import TabMixin  # noqa: E402
from ui.widgets import PasteableDateEdit, PasteableLineEdit, RecognitionPanel  # noqa: E402
from ui.windows.havaly import data as hav_data  # noqa: E402
from ui.windows.havaly.data import collect_havaly_data  # noqa: E402
from ui.windows.havaly.tabs import (  # noqa: E402
    CargoTab, CustomerTab, DriverTab, PriceTab, RouteTab, VehicleTab,
)
from ui.windows.havaly.tabs import cargo_tab as cargo_tab_module  # noqa: E402
from ui.windows.havaly.tabs import driver_tab as driver_tab_module  # noqa: E402
from ui.windows.havaly.tabs import price_tab as price_tab_module  # noqa: E402
from ui.windows.havaly.tabs import route_tab as route_tab_module  # noqa: E402

# ─────────────────────────────────────────────────────────────
# Данные тестов (синтетические, реальных ПДн нет)
# ─────────────────────────────────────────────────────────────

#: Дата заявки и план погрузки: вкладки отдают ISO, схема ждёт ДД.ММ.ГГГГ.
DATE_ISO = "2026-10-06"
DATE_DOC = "06.10.2026"
PLAN_DATE_ISO = "2026-10-08"
PLAN_DATE_DOC = "08.10.2026"
PLAN_TIME = "09:00"

#: Номер лота: в бланке шапка таблицы, значение одно на всю заявку.
LOT_NUMBER = "ЛОТ-2026-001"

#: Маршрут: город и пункт — разные поля, склеивать их нельзя.
LOADING_CITY = "г. Москва"
LOADING_POINT = "Склад Север, ул. Складская, д. 1"
UNLOADING_CITY = "г. Казань"
UNLOADING_POINT = "Площадка Юг, ул. Промышленная, д. 5"

#: Автовоз и прицеп: пять плоских полей блока zayavka.
TRACTOR_BRAND = "КАМАЗ-5490"
TRACTOR_COLOR = "Белый"
TRACTOR_PLATE = "А001АА77"
TRAILER_BRAND = "Тонар-9741"
TRAILER_PLATE = "БВ002277"

#: Водитель: ФИО тремя полями, паспорт серией и номером.
DRIVER_LAST_NAME = "Иванов"
DRIVER_FIRST_NAME = "Иван"
DRIVER_MIDDLE_NAME = "Иванович"
DRIVER_LICENSE_NUMBER = "99 АА 123456"
DRIVER_LICENSE_ISSUE_DATE_ISO = "2020-01-01"
DRIVER_LICENSE_ISSUE_DATE_DOC = "01.01.2020"
DRIVER_PASSPORT_SERIES = "18 22"
DRIVER_PASSPORT_NUMBER = "926830"
DRIVER_PASSPORT_ISSUER = "Отделом УФМС России по г. Москве"
DRIVER_PASSPORT_ISSUE_DATE_ISO = "2023-01-30"
DRIVER_PASSPORT_ISSUE_DATE_DOC = "30.01.2023"
DRIVER_CITIZENSHIP = "Российская Федерация"
DRIVER_BIRTH_DATE_ISO = "1980-01-01"
DRIVER_BIRTH_DATE_DOC = "01.01.1980"
DRIVER_REGISTRATION = "г. Москва, ул. Водительская, д. 3"
DRIVER_PHONE = "+7 (999) 123-45-67"

#: Перевозимые машины: одна строка таблицы — одна машина.
VIN_1 = "XTC651150N0001001"
BRAND_1 = "Haval"
MODEL_1 = "Jolion"
DEALER_1 = "АвтоДилер-Тест"
DEALER_CODE_1 = "D-001"
BRAND_2 = "Haval"
MODEL_2 = "F7"
DEALER_2 = "АвтоДилер-Юг"
DEALER_CODE_2 = "D-002"

#: Ставка с НДС (одна на всю заявку) и ставка НДС.
PRICE_WITH_VAT = 1234.56
VAT_RATE = "22%"

#: Все вкладки окна: (имя раздела, класс).
TAB_FACTORIES = [
    ("zayavka", CustomerTab),
    ("cargo", CargoTab),
    ("route", RouteTab),
    ("driver", DriverTab),
    ("vehicle", VehicleTab),
    ("price", PriceTab),
]

#: Ключи get_data() каждой вкладки — ровно те, что читает сборщик.
#: Список сверен со схемой промпта (ZAYAVKA_SCHEMA_ORDER / VEHICLE_KEYS).
EXPECTED_KEYS = {
    CustomerTab: {"date", "lot_number", "customer_name", "carrier_name"},
    CargoTab: {"lot_number", "vehicles"},
    RouteTab: {
        "loading_city", "loading_point", "unloading_city", "unloading_point",
        "loading_plan_date", "loading_plan_time",
    },
    DriverTab: {
        "driver_last_name", "driver_first_name", "driver_middle_name",
        "driver_license_number", "driver_license_issue_date",
        "driver_passport_series", "driver_passport_number",
        "driver_passport_issuer", "driver_passport_issue_date",
        "driver_citizenship", "driver_birth_date", "driver_registration",
        "driver_phone",
    },
    VehicleTab: {
        "tractor_brand", "tractor_color", "tractor_plate",
        "trailer_brand", "trailer_plate",
    },
    PriceTab: {"price_with_vat", "vat_rate"},
}

#: Образец заполнения: то, что мог бы дать распознаватель. Ключи — схемы
#: промпта, поэтому образец можно скормить и вкладке, и сборщику.
SAMPLE_DATA = {
    CustomerTab: {
        "date": "2026-09-24",
        "lot_number": LOT_NUMBER,
        "customer_name": CUSTOMER_NAME,
        "carrier_name": CARRIER_NAME,
    },
    CargoTab: {
        "lot_number": LOT_NUMBER,
        "vehicles": [
            {"vin": VIN_1, "brand": BRAND_1, "model": MODEL_1,
             "dealer": DEALER_1, "dealer_code": DEALER_CODE_1},
            {"vin": "", "brand": BRAND_2, "model": MODEL_2,
             "dealer": DEALER_2, "dealer_code": DEALER_CODE_2},
        ],
    },
    RouteTab: {
        "loading_city": LOADING_CITY,
        "loading_point": LOADING_POINT,
        "unloading_city": UNLOADING_CITY,
        "unloading_point": UNLOADING_POINT,
        "loading_plan_date": PLAN_DATE_ISO,
        "loading_plan_time": PLAN_TIME,
    },
    DriverTab: {
        "driver_last_name": DRIVER_LAST_NAME,
        "driver_first_name": DRIVER_FIRST_NAME,
        "driver_middle_name": DRIVER_MIDDLE_NAME,
        "driver_license_number": DRIVER_LICENSE_NUMBER,
        "driver_license_issue_date": DRIVER_LICENSE_ISSUE_DATE_ISO,
        "driver_passport_series": DRIVER_PASSPORT_SERIES,
        "driver_passport_number": DRIVER_PASSPORT_NUMBER,
        "driver_passport_issuer": DRIVER_PASSPORT_ISSUER,
        "driver_passport_issue_date": DRIVER_PASSPORT_ISSUE_DATE_ISO,
        "driver_citizenship": DRIVER_CITIZENSHIP,
        "driver_birth_date": DRIVER_BIRTH_DATE_ISO,
        "driver_registration": DRIVER_REGISTRATION,
        "driver_phone": DRIVER_PHONE,
    },
    VehicleTab: {
        "tractor_brand": TRACTOR_BRAND,
        "tractor_color": TRACTOR_COLOR,
        "tractor_plate": TRACTOR_PLATE,
        "trailer_brand": TRAILER_BRAND,
        "trailer_plate": TRAILER_PLATE,
    },
    PriceTab: {"price_with_vat": PRICE_WITH_VAT, "vat_rate": VAT_RATE},
}

#: Ключи вкладок, которых нет в схеме промпта: у «Груза» это контейнер
#: машин (массив "vehicles" схемы), остальные вкладки отдают только ключи
#: схемы. Список закрыт тестом — новый ключ мимо схемы здесь не пройдёт.
CONTAINER_KEYS = {"vehicles"}

#: Поля-значения по умолчанию: clear() их не обнуляет, а возвращает к норме.
#: Даты вкладок показывают сегодняшний день (рождение — тридцать лет назад),
#: время погрузки — 9:00, стороны заявки — фиксированные константы.
DEFAULT_VALUE_KEYS = {
    "date", "loading_plan_date", "loading_plan_time",
    "driver_license_issue_date", "driver_passport_issue_date",
    "driver_birth_date", "customer_name", "carrier_name",
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

class WindowStub:
    """
    Окно-заглушка: вкладки лежат атрибутами ``<раздел>_tab``.

    Так их будет держать окно типа (ЭТАП 3.1.E.B.3) — и так же ищет их
    сборщик (ui/windows/havaly/data.py::_tabs_of).
    """

    def __init__(self, tabs):
        for (name, _factory), widget in zip(TAB_FACTORIES, tabs):
            setattr(self, f"{name}_tab", widget)


class TabContainerStub:
    """Контейнер вкладок каркасного окна: поиск по заголовку (indexOf)."""

    def __init__(self, tabs):
        self._tabs = list(tabs)

    def indexOf(self, title: str) -> int:  # noqa: N802 — имя метода Qt
        for index, (name, _tab) in enumerate(self._tabs):
            if name == title:
                return index
        return -1

    def widget(self, index: int):
        return self._tabs[index][1]


def _schema_keys_of(cls):
    """Ключи вкладки, которые обязаны быть в схеме промпта."""
    return EXPECTED_KEYS[cls] - CONTAINER_KEYS


def _date_edit_of(widget) -> PasteableDateEdit:
    """Первый PasteableDateEdit вкладки (для проверки формата ввода)."""
    found = widget.findChildren(PasteableDateEdit)
    assert found, f"{type(widget).__name__}: даты нет вовсе"
    return found[0]


# ─────────────────────────────────────────────────────────────
# Общее для всех шести вкладок
# ─────────────────────────────────────────────────────────────

def test_tab_is_created_without_arguments(qt_app):
    """Каждая вкладка создаётся без аргументов и является QWidget."""
    for _name, factory in TAB_FACTORIES:
        widget = factory()
        try:
            assert isinstance(widget, QWidget)
        finally:
            widget.deleteLater()


def test_tab_is_qwidget_with_tab_mixin(tab):
    """Вкладка — QWidget + TabMixin (порядок баз важен, см. base_tab)."""
    assert isinstance(tab, QWidget)
    assert isinstance(tab, TabMixin)


def test_tab_has_required_api(tab):
    for name in ("get_data", "fill_data", "clear"):
        assert callable(getattr(tab, name)), name


def test_tab_declares_three_signals(tab):
    for name in ("recognize_requested", "create_contract_requested", "clear_requested"):
        assert hasattr(tab, name), name


def test_get_data_returns_dict(tab):
    """
    Каждая вкладка отдаёт словарь.

    Пустым он бывает только у «Стоимости» и только на нетронутой форме:
    суммы и ставки НДС там ещё нет, а выдумывать ноль или «22%» вкладка
    не имеет права (см. test_price_starts_without_rate_and_vat_key).
    """
    data = tab.get_data()

    assert isinstance(data, dict)
    if type(tab) is PriceTab:
        assert data == {}
        return
    assert data, f"{type(tab).__name__}: вкладка не отдала ни одного поля"


def test_get_data_returns_expected_keys(tab):
    """Ключи get_data() — ровно те, что читает сборщик B.1."""
    keys = set(tab.get_data())

    if type(tab) is PriceTab:
        # Незаполненные поля ключей не создают — это и есть контракт вкладки.
        assert keys <= EXPECTED_KEYS[PriceTab]
        return

    assert keys == EXPECTED_KEYS[type(tab)]


def test_get_data_keys_are_in_prompt_schema(tab):
    """
    Ключи get_data() есть в схеме промпта — сверка не по памяти.

    Имена ключей берутся из генератора (ZAYAVKA_SCHEMA_ORDER / VEHICLE_KEYS):
    он читает и пишет бланк по этой схеме, а его тесты сверяют схему
    с текстом промпта. Исключение одно — контейнер машин ``vehicles``.
    """
    data = tab.get_data()

    for key in data:
        if key in CONTAINER_KEYS:
            continue
        assert key in ZAYAVKA_SCHEMA_ORDER, (
            f"{type(tab).__name__}: ключа {key!r} нет в схеме промпта"
        )
        assert f'"{key}"' in PROMPT, f"поля {key!r} нет в тексте промпта"


def test_cargo_vehicle_fields_are_schema_keys():
    """Поля машины вкладки — ровно пять ключей схемы, в её порядке."""
    assert CargoTab.VEHICLE_FIELDS == tuple(VEHICLE_KEYS)
    assert tuple(cargo_tab_module.COLUMN_OF) == tuple(VEHICLE_KEYS)


def test_customer_and_vehicle_fixed_parties_match_generator():
    """Фиксированные стороны вкладок — те же константы, что у генератора."""
    tab = CustomerTab()
    try:
        data = tab.get_data()
        assert data["customer_name"] == CUSTOMER_NAME
        assert data["carrier_name"] == CARRIER_NAME
    finally:
        tab.deleteLater()

    vehicle = VehicleTab()
    try:
        assert vehicle.transport_company.text() == CARRIER_NAME
    finally:
        vehicle.deleteLater()


def test_fill_data_fills_fields(tab):
    """fill_data(образец) заполняет вкладку значениями образца."""
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


def test_fill_data_with_empty_values_keeps_manual_input(tab):
    """
    Пустые значения не затирают введённое.

    Распознавание часто отдаёт поля схемы целиком, с пустыми строками:
    уже введённые данные от них исчезать не должны.
    """
    tab.fill_data(SAMPLE_DATA[type(tab)])
    before = tab.get_data()

    empty = {key: ([] if isinstance(value, list) else "") for key, value in before.items()}
    tab.fill_data(empty)

    assert tab.get_data() == before, f"{type(tab).__name__}: пустое затирает данные"


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
    """Текстовые поля после clear() пусты (даты и подписи — по умолчанию)."""
    tab.fill_data(SAMPLE_DATA[type(tab)])
    tab.clear()

    for key, value in tab.get_data().items():
        if key in DEFAULT_VALUE_KEYS:
            continue  # даты, время и фиксированные стороны живут по умолчанию
        if isinstance(value, str):
            assert value == "", f"{type(tab).__name__}: поле {key} не очищено"
        elif isinstance(value, list):
            assert value == [], f"{type(tab).__name__}: список {key} не очищен"


def test_clear_clears_recognition_panel(tab):
    tab.recognition_panel.text_edit.setPlainText("текст документа")

    tab.clear()

    assert tab.recognition_panel.get_text() == ""


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
    В connect — только метод класса (AGENTS.md § 5.1).

    lambda в connect создаёт цикл ссылок Python ↔ Qt и роняет процесс
    при завершении (0xC0000005).
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
# «Заявка»
# ─────────────────────────────────────────────────────────────

def test_customer_parties_are_readonly(qt_app):
    """Стороны заявки фиксированы: поля только для чтения и без фокуса."""
    tab = CustomerTab()
    try:
        for field in (tab.customer_name, tab.carrier_name):
            assert isinstance(field, QLineEdit)
            assert field.isReadOnly()
            assert field.focusPolicy() == Qt.NoFocus
    finally:
        tab.deleteLater()


def test_customer_parties_cannot_be_overwritten(qt_app):
    """Стороны не перезаписываются ни данными, ни вставкой текста."""
    tab = CustomerTab()
    try:
        tab.fill_data({"customer_name": "ООО «Другой»", "carrier_name": "ИП Другой"})
        tab.customer_name.setText("подмена")
        tab.customer_name.insert("подмена")

        data = tab.get_data()
        assert data["customer_name"] == CUSTOMER_NAME
        assert data["carrier_name"] == CARRIER_NAME
    finally:
        tab.deleteLater()


def test_customer_fill_and_read_back(qt_app):
    tab = CustomerTab()
    try:
        tab.fill_data(SAMPLE_DATA[CustomerTab])
        data = tab.get_data()

        assert data["lot_number"] == LOT_NUMBER
        assert data["date"] == "2026-09-24"
    finally:
        tab.deleteLater()


def test_customer_date_accepts_document_format(qt_app):
    """Дата принимается в формате документа и отдаётся ISO."""
    tab = CustomerTab()
    try:
        tab.fill_data({"date": DATE_DOC})

        assert tab.get_data()["date"] == DATE_ISO
    finally:
        tab.deleteLater()


def test_customer_date_widget_shows_document_format(qt_app):
    """В поле дата видна как ДД.ММ.ГГГГ, в данные уходит ISO."""
    tab = CustomerTab()
    try:
        tab.fill_data({"date": DATE_ISO})
        shown = tab.date.date().toString("dd.MM.yyyy")

        assert shown == DATE_DOC
        assert tab.get_data()["date"] == DATE_ISO
    finally:
        tab.deleteLater()


def test_customer_clear_resets_lot_and_date(qt_app):
    tab = CustomerTab()
    try:
        tab.fill_data(SAMPLE_DATA[CustomerTab])
        tab.clear()
        data = tab.get_data()

        assert data["lot_number"] == ""
        assert data["date"] == QDate.currentDate().toString("yyyy-MM-dd")
        assert data["customer_name"] == CUSTOMER_NAME
        assert data["carrier_name"] == CARRIER_NAME
    finally:
        tab.deleteLater()


# ─────────────────────────────────────────────────────────────
# «Груз»
# ─────────────────────────────────────────────────────────────

def test_cargo_table_headers_are_form_columns(qt_app):
    """Колонки таблицы — как в шапке бланка: марка и модель раздельно."""
    tab = CargoTab()
    try:
        headers = [
            tab.vehicles_table.horizontalHeaderItem(column).text()
            for column in range(tab.vehicles_table.columnCount())
        ]

        assert headers == ["№", "VIN", "Марка", "Модель", "Дилер", "Код дилера"]
        assert tab.vehicles_table.columnCount() == len(VEHICLE_KEYS) + 1
    finally:
        tab.deleteLater()


def test_cargo_max_cars_matches_data_module():
    """Столько же машин, сколько помещается в бланк и ждёт сборщик."""
    assert cargo_tab_module.MAX_CARS == hav_data.MAX_VEHICLES == 10
    assert CargoTab.MAX_CARS == cargo_tab_module.MAX_CARS


def test_cargo_starts_with_min_rows(qt_app):
    tab = CargoTab()
    try:
        assert tab.vehicles_table.rowCount() == cargo_tab_module.MIN_ROWS
        assert tab.get_data()["vehicles"] == []
    finally:
        tab.deleteLater()


def test_cargo_fill_and_read_back(qt_app):
    tab = CargoTab()
    try:
        tab.fill_data(SAMPLE_DATA[CargoTab])
        vehicles = tab.get_data()["vehicles"]

        assert len(vehicles) == 2
        assert vehicles[0] == {
            "vin": VIN_1, "brand": BRAND_1, "model": MODEL_1,
            "dealer": DEALER_1, "dealer_code": DEALER_CODE_1,
        }
        assert list(vehicles[0]) == list(VEHICLE_KEYS)
        assert vehicles[1]["vin"] == ""
        assert vehicles[1]["brand"] == BRAND_2
    finally:
        tab.deleteLater()


def test_cargo_vehicle_without_vin_is_kept(qt_app):
    """
    Машина без VIN остаётся в списке.

    «Vin по факту погрузки» — обычное дело в заявках Хавалов: промпт требует
    такую строку вернуть, машину выдают марка и модель.
    """
    tab = CargoTab()
    try:
        tab.fill_data({"vehicles": [
            {"vin": "", "brand": BRAND_1, "model": MODEL_1,
             "dealer": DEALER_1, "dealer_code": DEALER_CODE_1},
        ]})
        vehicles = tab.get_data()["vehicles"]

        assert len(vehicles) == 1
        assert vehicles[0]["vin"] == ""
        assert vehicles[0]["brand"] == BRAND_1
    finally:
        tab.deleteLater()


def test_cargo_empty_rows_are_not_returned(qt_app):
    """Пустые строки таблицы машинами не считаются."""
    tab = CargoTab()
    try:
        tab.fill_data({"vehicles": [
            {"vin": "", "brand": "", "model": "", "dealer": "", "dealer_code": ""},
        ]})
        assert tab.get_data()["vehicles"] == []
    finally:
        tab.deleteLater()


def test_cargo_row_with_brand_only_is_kept(qt_app):
    """Заполнено одно поле — строка уже машина."""
    tab = CargoTab()
    try:
        tab.vehicles_table.item(0, cargo_tab_module.COL_MODEL).setText("Jolion")

        vehicles = tab.get_data()["vehicles"]
        assert len(vehicles) == 1
        assert vehicles[0]["model"] == "Jolion"
        assert vehicles[0]["vin"] == ""
    finally:
        tab.deleteLater()


def test_cargo_fill_over_limit_is_truncated(qt_app):
    """Машин больше десяти — лишние в таблицу не попадают (в бланке 10 строк)."""
    tab = CargoTab()
    try:
        raw = [
            {"vin": f"XTC651150N000{n:04d}", "brand": BRAND_1, "model": MODEL_1,
             "dealer": DEALER_1, "dealer_code": DEALER_CODE_1}
            for n in range(1, 13)
        ]
        tab.fill_data({"vehicles": raw})
        vehicles = tab.get_data()["vehicles"]

        assert tab.vehicles_table.rowCount() == CargoTab.MAX_CARS
        assert len(vehicles) == CargoTab.MAX_CARS
        assert vehicles[-1]["vin"] == "XTC651150N0000010"
    finally:
        tab.deleteLater()


def test_cargo_empty_vehicles_list_keeps_manual_input(qt_app):
    """Пустой список машин не стирает уже введённую таблицу."""
    tab = CargoTab()
    try:
        tab.fill_data(SAMPLE_DATA[CargoTab])
        before = tab.get_data()

        tab.fill_data({"vehicles": []})

        assert tab.get_data() == before
    finally:
        tab.deleteLater()


def test_cargo_add_and_remove_rows(qt_app, quiet_dialogs):
    tab = CargoTab()
    try:
        start = tab.vehicles_table.rowCount()

        tab.btn_add_vehicle.click()
        assert tab.vehicles_table.rowCount() == start + 1

        tab.vehicles_table.setCurrentCell(start, cargo_tab_module.COL_VIN)
        tab.btn_remove_vehicle.click()
        assert tab.vehicles_table.rowCount() == start
    finally:
        tab.deleteLater()


def test_cargo_rows_are_renumbered(qt_app, quiet_dialogs):
    tab = CargoTab()
    try:
        tab.btn_add_vehicle.click()
        tab.vehicles_table.setCurrentCell(0, cargo_tab_module.COL_VIN)
        tab.btn_remove_vehicle.click()

        numbers = [
            tab.vehicles_table.item(row, cargo_tab_module.COL_NUMBER).text()
            for row in range(tab.vehicles_table.rowCount())
        ]
        assert numbers == [str(row + 1) for row in range(len(numbers))]
    finally:
        tab.deleteLater()


def test_cargo_add_row_stops_at_max_cars(qt_app, quiet_dialogs, monkeypatch):
    """Сверх десяти машин кнопка не добавляет строку и предупреждает."""
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args, **kwargs: warnings.append(args)),
    )

    tab = CargoTab()
    try:
        while tab.vehicles_table.rowCount() < CargoTab.MAX_CARS:
            tab.btn_add_vehicle.click()

        tab.btn_add_vehicle.click()

        assert tab.vehicles_table.rowCount() == CargoTab.MAX_CARS
        assert warnings, "пользователь не предупреждён об ограничении"
    finally:
        tab.deleteLater()


def test_cargo_fill_keeps_min_rows_for_new_machines(qt_app):
    """Меньше MIN_ROWS машин — пустые строки всё равно остаются для ввода."""
    tab = CargoTab()
    try:
        tab.fill_data({"vehicles": [
            {"vin": VIN_1, "brand": BRAND_1, "model": MODEL_1,
             "dealer": DEALER_1, "dealer_code": DEALER_CODE_1},
        ]})

        assert tab.vehicles_table.rowCount() == cargo_tab_module.MIN_ROWS
        assert len(tab.get_data()["vehicles"]) == 1
    finally:
        tab.deleteLater()


def test_cargo_clear_returns_min_rows(qt_app):
    tab = CargoTab()
    try:
        tab.fill_data(SAMPLE_DATA[CargoTab])
        tab.clear()

        assert tab.vehicles_table.rowCount() == cargo_tab_module.MIN_ROWS
        assert tab.get_data() == {"lot_number": "", "vehicles": []}
    finally:
        tab.deleteLater()


def test_cargo_lot_number_matches_zayavka_field(qt_app):
    """Номер лота на «Грузе» — тот же ключ схемы, что и на «Заявке»."""
    tab = CargoTab()
    try:
        tab.fill_data({"lot_number": LOT_NUMBER})

        assert tab.get_data()["lot_number"] == LOT_NUMBER
        assert "lot_number" in ZAYAVKA_SCHEMA_ORDER
    finally:
        tab.deleteLater()


# ─────────────────────────────────────────────────────────────
# «Маршрут»
# ─────────────────────────────────────────────────────────────

def test_route_city_and_point_are_different_fields(qt_app):
    """
    Город и пункт — разные поля (требование промпта).

    Склейки «город, адрес» в одном поле здесь нет: иначе адрес уехал бы
    в колонку города и обратно его уже не разделить.
    """
    tab = RouteTab()
    try:
        tab.fill_data(SAMPLE_DATA[RouteTab])
        data = tab.get_data()

        assert data["loading_city"] == LOADING_CITY
        assert data["loading_point"] == LOADING_POINT
        assert data["loading_city"] != data["loading_point"]
        assert data["unloading_city"] == UNLOADING_CITY
        assert data["unloading_point"] == UNLOADING_POINT
    finally:
        tab.deleteLater()


def test_route_loading_city_does_not_leak_into_point(qt_app):
    """Город не подставляется в пункт: поля независимы."""
    tab = RouteTab()
    try:
        tab.fill_data({"loading_city": LOADING_CITY})
        data = tab.get_data()

        assert data["loading_point"] == ""
        assert data["unloading_city"] == ""
    finally:
        tab.deleteLater()


def test_route_plan_date_and_time_round_trip(qt_app):
    tab = RouteTab()
    try:
        tab.fill_data(SAMPLE_DATA[RouteTab])
        data = tab.get_data()

        assert data["loading_plan_date"] == PLAN_DATE_ISO
        assert data["loading_plan_time"] == PLAN_TIME
    finally:
        tab.deleteLater()


def test_route_plan_date_accepts_document_format(qt_app):
    tab = RouteTab()
    try:
        tab.fill_data({"loading_plan_date": PLAN_DATE_DOC})

        assert tab.get_data()["loading_plan_date"] == PLAN_DATE_ISO
        assert tab.loading_plan_date.date().toString("dd.MM.yyyy") == PLAN_DATE_DOC
    finally:
        tab.deleteLater()


def test_route_time_widget_is_time_edit(qt_app):
    """Время погрузки — QTimeEdit в формате «ЧЧ:ММ»."""
    tab = RouteTab()
    try:
        assert isinstance(tab.loading_plan_time, QTimeEdit)
        assert tab.loading_plan_time.displayFormat() == "HH:mm"
    finally:
        tab.deleteLater()


@pytest.mark.parametrize("value,expected", [
    ("09:00", "09:00"),
    ("09:00:00", "09:00"),
    ("8:30", "08:30"),
])
def test_route_time_is_set_from_string(qt_app, value, expected):
    """Время из распознавания принимается и с секундами, и без ведущего нуля."""
    tab = RouteTab()
    try:
        tab.fill_data({"loading_plan_time": value})

        assert tab.get_data()["loading_plan_time"] == expected
    finally:
        tab.deleteLater()


def test_route_broken_time_is_ignored(qt_app):
    """Непонятное время не трогает поле (о нём скажет валидатор)."""
    tab = RouteTab()
    try:
        before = tab.get_data()["loading_plan_time"]
        tab.fill_data({"loading_plan_time": "утром"})

        assert tab.get_data()["loading_plan_time"] == before
    finally:
        tab.deleteLater()


def test_route_default_time_is_morning(qt_app):
    tab = RouteTab()
    try:
        assert tab.loading_plan_time.time() == route_tab_module.DEFAULT_LOADING_TIME
        assert tab.loading_plan_time.time() == QTime(9, 0)
    finally:
        tab.deleteLater()


def test_route_clear_resets_plan(qt_app):
    tab = RouteTab()
    try:
        tab.fill_data(SAMPLE_DATA[RouteTab])
        tab.clear()
        data = tab.get_data()

        assert data["loading_city"] == ""
        assert data["loading_point"] == ""
        assert data["unloading_city"] == ""
        assert data["unloading_point"] == ""
        assert data["loading_plan_date"] == QDate.currentDate().toString("yyyy-MM-dd")
        assert data["loading_plan_time"] == PLAN_TIME
    finally:
        tab.deleteLater()


# ─────────────────────────────────────────────────────────────
# «Водитель»
# ─────────────────────────────────────────────────────────────

def test_driver_has_thirteen_fields(qt_app):
    tab = DriverTab()
    try:
        assert len(DriverTab.FIELDS) + len(DriverTab.DATE_FIELDS) == 13
        assert set(tab.get_data()) == EXPECTED_KEYS[DriverTab]
    finally:
        tab.deleteLater()


def test_driver_name_is_three_separate_fields(qt_app):
    """
    Фамилия, имя и отчество — три поля (требование промпта).

    Одной строки «Иванов Иван Иванович» здесь нет: разбирать её на части
    на вкладке запрещено, разделение делает распознавание.
    """
    tab = DriverTab()
    try:
        tab.fill_data(SAMPLE_DATA[DriverTab])
        data = tab.get_data()

        assert data["driver_last_name"] == DRIVER_LAST_NAME
        assert data["driver_first_name"] == DRIVER_FIRST_NAME
        assert data["driver_middle_name"] == DRIVER_MIDDLE_NAME
    finally:
        tab.deleteLater()


def test_driver_passport_series_and_number_are_separate(qt_app):
    """Серия и номер паспорта — тоже разные поля, как в колонках бланка."""
    tab = DriverTab()
    try:
        tab.fill_data(SAMPLE_DATA[DriverTab])
        data = tab.get_data()

        assert data["driver_passport_series"] == DRIVER_PASSPORT_SERIES
        assert data["driver_passport_number"] == DRIVER_PASSPORT_NUMBER
    finally:
        tab.deleteLater()


def test_driver_has_three_independent_dates(qt_app):
    """Три даты вкладки не связаны: заполняются каждая своим ключом."""
    tab = DriverTab()
    try:
        tab.fill_data(SAMPLE_DATA[DriverTab])
        data = tab.get_data()

        assert data["driver_license_issue_date"] == DRIVER_LICENSE_ISSUE_DATE_ISO
        assert data["driver_passport_issue_date"] == DRIVER_PASSPORT_ISSUE_DATE_ISO
        assert data["driver_birth_date"] == DRIVER_BIRTH_DATE_ISO
        assert len({data[field] for field in DriverTab.DATE_FIELDS}) == 3
    finally:
        tab.deleteLater()


def test_driver_dates_are_shown_in_document_format(qt_app):
    """В полях даты видны как ДД.ММ.ГГГГ, в данные уходят ISO."""
    tab = DriverTab()
    try:
        tab.fill_data(SAMPLE_DATA[DriverTab])

        assert (
            tab.driver_license_issue_date.date().toString("dd.MM.yyyy")
            == DRIVER_LICENSE_ISSUE_DATE_DOC
        )
        assert (
            tab.driver_passport_issue_date.date().toString("dd.MM.yyyy")
            == DRIVER_PASSPORT_ISSUE_DATE_DOC
        )
        assert (
            tab.driver_birth_date.date().toString("dd.MM.yyyy")
            == DRIVER_BIRTH_DATE_DOC
        )
    finally:
        tab.deleteLater()


def test_driver_empty_name_part_keeps_manual_input(qt_app):
    """Пустая часть ФИО не затирает введённое (распознавание отдаёт схему целиком)."""
    tab = DriverTab()
    try:
        tab.fill_data({"driver_last_name": DRIVER_LAST_NAME})
        tab.fill_data({"driver_first_name": "", "driver_middle_name": ""})

        assert tab.get_data()["driver_last_name"] == DRIVER_LAST_NAME
    finally:
        tab.deleteLater()


def test_driver_clear_keeps_default_dates(qt_app):
    """clear() чистит текстовые поля, даты возвращает к значениям по умолчанию."""
    tab = DriverTab()
    try:
        tab.fill_data(SAMPLE_DATA[DriverTab])
        tab.clear()
        data = tab.get_data()

        for field in DriverTab.FIELDS:
            assert data[field] == "", f"поле {field} не очищено"

        today = QDate.currentDate()
        assert data["driver_license_issue_date"] == today.toString("yyyy-MM-dd")
        assert data["driver_passport_issue_date"] == today.toString("yyyy-MM-dd")
        assert data["driver_birth_date"] == today.addYears(
            -driver_tab_module.DEFAULT_BIRTH_YEARS_AGO
        ).toString("yyyy-MM-dd")
    finally:
        tab.deleteLater()


# ─────────────────────────────────────────────────────────────
# «ТС»
# ─────────────────────────────────────────────────────────────

def test_vehicle_has_five_fields(qt_app):
    """Автовоз и прицеп: пять плоских полей схемы — отдельных блоков нет."""
    tab = VehicleTab()
    try:
        assert set(tab.get_data()) == EXPECTED_KEYS[VehicleTab]
        assert len(VehicleTab.FIELDS) == 5
    finally:
        tab.deleteLater()


def test_vehicle_transport_company_is_readonly(qt_app):
    """Наименование транспортной компании фиксировано и не редактируется."""
    tab = VehicleTab()
    try:
        assert tab.transport_company.text() == CARRIER_NAME
        assert tab.transport_company.isReadOnly()
        assert tab.transport_company.focusPolicy() == Qt.NoFocus
    finally:
        tab.deleteLater()


def test_vehicle_transport_company_is_not_in_data(qt_app):
    """
    Колонку «Наименование транспортной компании» вкладка в данные не отдаёт.

    Промпт запрещает переносить её в ответ: перевозчика называет carrier_name
    блока zayavka. Лишнего ключа схема не знает.
    """
    tab = VehicleTab()
    try:
        data = tab.get_data()

        assert "transport_company" not in data
        assert "carrier_name" not in data
        for key in data:
            assert key in ZAYAVKA_SCHEMA_ORDER
    finally:
        tab.deleteLater()


def test_vehicle_fill_and_read_back(qt_app):
    tab = VehicleTab()
    try:
        tab.fill_data(SAMPLE_DATA[VehicleTab])
        data = tab.get_data()

        assert data == {
            "tractor_brand": TRACTOR_BRAND,
            "tractor_color": TRACTOR_COLOR,
            "tractor_plate": TRACTOR_PLATE,
            "trailer_brand": TRAILER_BRAND,
            "trailer_plate": TRAILER_PLATE,
        }
    finally:
        tab.deleteLater()


def test_vehicle_empty_value_keeps_manual_input(qt_app):
    tab = VehicleTab()
    try:
        tab.fill_data({"tractor_plate": TRACTOR_PLATE})
        tab.fill_data({"tractor_plate": "", "trailer_plate": ""})

        assert tab.get_data()["tractor_plate"] == TRACTOR_PLATE
    finally:
        tab.deleteLater()


def test_vehicle_clear_keeps_company(qt_app):
    tab = VehicleTab()
    try:
        tab.fill_data(SAMPLE_DATA[VehicleTab])
        tab.clear()

        assert tab.get_data() == {field: "" for field in VehicleTab.FIELDS}
        assert tab.transport_company.text() == CARRIER_NAME
    finally:
        tab.deleteLater()


# ─────────────────────────────────────────────────────────────
# «Стоимость»
# ─────────────────────────────────────────────────────────────

def test_price_starts_without_rate_and_vat_key(qt_app):
    """
    Пустая вкладка ставку не выдумывает.

    Ноль означает «ставка не указана» (так её читают промпт, генератор
    и валидатор), а ставка НДС в пустой форме ключа не создаёт: подставлять
    «22%» за пользователя нельзя — иначе пустая форма перестала бы быть
    пустой (эту проверку держит tests/test_ui_havaly_data.py).
    """
    tab = PriceTab()
    try:
        data = tab.get_data()

        assert data == {}
        assert "vat_rate" not in data
        assert "price_with_vat" not in data
    finally:
        tab.deleteLater()


def test_price_widget_is_spin_box(qt_app):
    """Ставка с НДС — числовое поле с копейками и подписью рублей."""
    tab = PriceTab()
    try:
        assert isinstance(tab.price_with_vat, QDoubleSpinBox)
        assert tab.price_with_vat.decimals() == 2
        assert tab.price_with_vat.suffix() == price_tab_module.AMOUNT_SUFFIX
        assert tab.price_with_vat.maximum() == price_tab_module.MAX_AMOUNT
    finally:
        tab.deleteLater()


def test_price_fill_and_read_back(qt_app):
    tab = PriceTab()
    try:
        tab.fill_data(SAMPLE_DATA[PriceTab])
        data = tab.get_data()

        assert data["price_with_vat"] == pytest.approx(PRICE_WITH_VAT)
        assert data["vat_rate"] == VAT_RATE
    finally:
        tab.deleteLater()


def test_price_accepts_document_money_text(qt_app):
    """Ставка из документа: «1 234,56 руб.» → 1234.56."""
    tab = PriceTab()
    try:
        tab.fill_data({"price_with_vat": "1 234,56 руб."})

        assert tab.get_data()["price_with_vat"] == pytest.approx(PRICE_WITH_VAT)
    finally:
        tab.deleteLater()


def test_price_broken_amount_is_ignored(qt_app):
    """Непонятная сумма поле не трогает."""
    tab = PriceTab()
    try:
        tab.fill_data({"price_with_vat": PRICE_WITH_VAT})
        tab.fill_data({"price_with_vat": "по договорённости"})

        assert tab.get_data()["price_with_vat"] == pytest.approx(PRICE_WITH_VAT)
    finally:
        tab.deleteLater()


def test_price_zero_amount_does_not_erase_manual_input(qt_app):
    """Ноль в распознанных данных ставку не сбрасывает."""
    tab = PriceTab()
    try:
        tab.fill_data({"price_with_vat": PRICE_WITH_VAT})
        tab.fill_data({"price_with_vat": 0.0})

        assert tab.get_data()["price_with_vat"] == pytest.approx(PRICE_WITH_VAT)
    finally:
        tab.deleteLater()


def test_price_accepts_contract_keys(qt_app):
    """Распознавание отдаёт блок contract целиком: сумма берётся и оттуда."""
    tab = PriceTab()
    try:
        tab.fill_data({"amount_with_vat": PRICE_WITH_VAT})

        assert tab.get_data()["price_with_vat"] == pytest.approx(PRICE_WITH_VAT)
    finally:
        tab.deleteLater()


def test_price_accepts_vat_rate_as_number(qt_app):
    """
    Ставка НДС числом (vat_rate_num) превращается в строку «22%».

    Так её отдаёт вкладка Логистикс Рус, и этот же вид принимает сборщик
    (ui/windows/havaly/data.py::VAT_RATE_NUM_FIELD).
    """
    tab = PriceTab()
    try:
        tab.fill_data({"vat_rate_num": 22.0})

        assert tab.get_data()["vat_rate"] == VAT_RATE
    finally:
        tab.deleteLater()


def test_price_vat_rate_is_optional_combo(qt_app):
    """Ставка НДС — список с пустым первым пунктом; текст можно вписать руками."""
    tab = PriceTab()
    try:
        assert isinstance(tab.vat_rate, QComboBox)
        assert tab.vat_rate.isEditable()
        assert tab.vat_rate.itemText(0) == ""
        assert tab.vat_rate.currentText() == price_tab_module.DEFAULT_VAT_RATE
        assert VAT_RATE in price_tab_module.VAT_RATES
    finally:
        tab.deleteLater()


def test_price_unknown_vat_rate_is_kept_as_text(qt_app):
    """Незнакомая ставка не теряется: уходит в данные как напечатана."""
    tab = PriceTab()
    try:
        tab.vat_rate.setCurrentText("Без НДС")

        assert tab.get_data()["vat_rate"] == "Без НДС"
    finally:
        tab.deleteLater()


def test_price_vat_rate_gets_percent_sign(qt_app):
    """Ставка без знака процента приводится к виду бланка."""
    tab = PriceTab()
    try:
        tab.vat_rate.setCurrentText("20")

        assert tab.get_data()["vat_rate"] == "20%"
    finally:
        tab.deleteLater()


def test_price_clear_resets_rate_and_vat(qt_app):
    tab = PriceTab()
    try:
        tab.fill_data(SAMPLE_DATA[PriceTab])
        tab.clear()

        assert tab.get_data() == {}
    finally:
        tab.deleteLater()


# ─────────────────────────────────────────────────────────────
# Стык с B.1: настоящие вкладки → collect_havaly_data
# ─────────────────────────────────────────────────────────────

def test_collect_reads_real_tabs(filled_tabs):
    """
    Шесть настоящих вкладок → сборщик B.1 → словарь схемы промпта.

    Проверяется тот самый стык, ради которого имена полей вкладок совпадают
    с ключами схемы: сборщик ничего не переводит и не должен потерять ни
    одного заполненного поля.
    """
    data = collect_havaly_data(WindowStub(filled_tabs))
    zayavka = data["zayavka"]

    assert zayavka["lot_number"] == LOT_NUMBER
    assert zayavka["loading_city"] == LOADING_CITY
    assert zayavka["loading_point"] == LOADING_POINT
    assert zayavka["unloading_city"] == UNLOADING_CITY
    assert zayavka["unloading_point"] == UNLOADING_POINT
    assert zayavka["tractor_brand"] == TRACTOR_BRAND
    assert zayavka["tractor_color"] == TRACTOR_COLOR
    assert zayavka["tractor_plate"] == TRACTOR_PLATE
    assert zayavka["trailer_brand"] == TRAILER_BRAND
    assert zayavka["trailer_plate"] == TRAILER_PLATE
    assert zayavka["driver_last_name"] == DRIVER_LAST_NAME
    assert zayavka["driver_first_name"] == DRIVER_FIRST_NAME
    assert zayavka["driver_middle_name"] == DRIVER_MIDDLE_NAME
    assert zayavka["driver_passport_series"] == DRIVER_PASSPORT_SERIES
    assert zayavka["driver_passport_number"] == DRIVER_PASSPORT_NUMBER
    assert zayavka["driver_phone"] == DRIVER_PHONE
    assert zayavka["price_with_vat"] == pytest.approx(PRICE_WITH_VAT)
    assert zayavka["vat_rate"] == VAT_RATE


def test_collect_normalizes_dates_and_time_from_real_tabs(filled_tabs):
    """Даты из QDateEdit доезжают до схемы в формате документа, время — «ЧЧ:ММ»."""
    zayavka = collect_havaly_data(WindowStub(filled_tabs))["zayavka"]

    assert zayavka["date"] == "24.09.2026"
    assert zayavka["loading_plan_date"] == PLAN_DATE_DOC
    assert zayavka["loading_plan_time"] == PLAN_TIME
    assert zayavka["driver_license_issue_date"] == DRIVER_LICENSE_ISSUE_DATE_DOC
    assert zayavka["driver_passport_issue_date"] == DRIVER_PASSPORT_ISSUE_DATE_DOC
    assert zayavka["driver_birth_date"] == DRIVER_BIRTH_DATE_DOC


def test_collect_vehicles_from_real_tabs(filled_tabs):
    """Машины таблицы «Груз» доезжают до массива vehicles схемы промпта."""
    vehicles = collect_havaly_data(WindowStub(filled_tabs))["vehicles"]

    assert len(vehicles) == 2
    assert list(vehicles[0]) == list(VEHICLE_KEYS)
    assert vehicles[0]["vin"] == VIN_1
    assert vehicles[0]["brand"] == BRAND_1
    assert vehicles[0]["model"] == MODEL_1
    assert vehicles[1]["vin"] == ""
    assert vehicles[1]["dealer"] == DEALER_2


def test_collect_fixed_parties_from_real_tabs(filled_tabs):
    """Стороны заявки приходят с вкладки «Заявка» и совпадают с константами."""
    zayavka = collect_havaly_data(WindowStub(filled_tabs))["zayavka"]

    assert zayavka["customer_name"] == CUSTOMER_NAME
    assert zayavka["carrier_name"] == CARRIER_NAME


def test_collect_contract_block_from_real_tabs(filled_tabs):
    """Форма ContractData (§ 5.2) собирается из тех же вкладок."""
    contract = collect_havaly_data(WindowStub(filled_tabs))["contract"]

    assert contract["contract"]["lot_number"] == LOT_NUMBER
    assert contract["tractor"]["plate_number"] == TRACTOR_PLATE
    assert contract["trailer"]["brand_model"] == TRAILER_BRAND
    assert contract["driver"]["last_name"] == DRIVER_LAST_NAME
    assert len(contract["vehicles"]) == 2


def test_collect_real_tabs_pass_type_validator(filled_tabs):
    """Данных с настоящих вкладок достаточно: ошибок у валидатора нет."""
    report = ZayavkaExcelValidator().check(
        collect_havaly_data(WindowStub(filled_tabs))
    )

    assert report.errors == [], report.errors


def test_collect_every_schema_field_from_real_tabs(qt_app):
    """
    Ни одно поле схемы промпта не потерялось на пути «вкладка → сборщик».

    Собирается заявка, где заполнено КАЖДОЕ поле схемы (кроме vat_rate
    и сторон: у них своя логика), и проверяется, что в ответе они все.
    """
    customer, cargo, route, driver, vehicle, price = (
        CustomerTab(), CargoTab(), RouteTab(),
        DriverTab(), VehicleTab(), PriceTab(),
    )
    widgets = [customer, cargo, route, driver, vehicle, price]

    try:
        customer.fill_data({
            "date": DATE_ISO, "lot_number": LOT_NUMBER,
            "customer_name": CUSTOMER_NAME, "carrier_name": CARRIER_NAME,
        })
        cargo.fill_data(SAMPLE_DATA[CargoTab])
        route.fill_data(SAMPLE_DATA[RouteTab])
        driver.fill_data(SAMPLE_DATA[DriverTab])
        vehicle.fill_data(SAMPLE_DATA[VehicleTab])
        price.fill_data({"price_with_vat": PRICE_WITH_VAT, "vat_rate": VAT_RATE})

        zayavka = collect_havaly_data(WindowStub(widgets))["zayavka"]
        missing = [key for key in ZAYAVKA_SCHEMA_ORDER if key not in zayavka]

        assert not missing, f"сборщик не получил поля: {missing}"
    finally:
        for widget in widgets:
            widget.deleteLater()


def test_collect_empty_real_tabs_keeps_form_empty(qt_app):
    """
    Пустое окно на настоящих вкладках не выдумывает данных.

    Заполнены только фиксированные стороны: их промпт требует заполнять
    всегда, в бланке они напечатаны. Ставка НДС, дата и номер лота остаются
    незаданными — вкладки их не подставляют.
    """
    widgets = [
        CustomerTab(), CargoTab(), RouteTab(),
        DriverTab(), VehicleTab(), PriceTab(),
    ]
    try:
        data = collect_havaly_data(WindowStub(widgets))

        assert data["vehicles"] == []
        assert "vat_rate" not in data["zayavka"]
        assert "price_with_vat" not in data["zayavka"]
        assert "lot_number" not in data["zayavka"]
        assert "driver_last_name" not in data["zayavka"]
        assert "tractor_plate" not in data["zayavka"]
        assert data["zayavka"]["customer_name"] == CUSTOMER_NAME
        assert data["zayavka"]["carrier_name"] == CARRIER_NAME
    finally:
        for widget in widgets:
            widget.deleteLater()


def test_collect_reads_tabs_from_container(qt_app):
    """
    Тот же стык через контейнер вкладок каркасного окна.

    Каркас (ui/windows/havaly/window.py) держит вкладки в одном QTabWidget:
    сборщик ищет их по заголовкам разделов (indexOf → widget), и настоящие
    вкладки должны находиться так же, как находились заглушки.
    """
    widgets = [
        CustomerTab(), CargoTab(), RouteTab(),
        DriverTab(), VehicleTab(), PriceTab(),
    ]
    container = TabContainerStub([
        (hav_data.SECTION_TITLES[name], widget)
        for (name, _factory), widget in zip(TAB_FACTORIES, widgets)
    ])

    class Skeleton:
        def __init__(self, tabs):
            self.tabs = tabs

    try:
        widgets[0].fill_data({"lot_number": LOT_NUMBER})
        widgets[1].fill_data(SAMPLE_DATA[CargoTab])

        data = collect_havaly_data(Skeleton(container))

        assert data["zayavka"]["lot_number"] == LOT_NUMBER
        assert len(data["vehicles"]) == 2
    finally:
        for widget in widgets:
            widget.deleteLater()


def test_collect_zayavka_tab_wins_for_shared_fields(qt_app):
    """
    Номер лота есть на двух вкладках — значение берётся с «Заявки».

    В бланке это шапка таблицы: номер лота и дата заявки стоят над строками.
    Заполнено в двух местах — правда за шапкой
    (ui/windows/havaly/data.py::_SHARED_ZAYAVKA_FIELDS).
    """
    customer, cargo = CustomerTab(), CargoTab()
    widgets = [customer, cargo, RouteTab(), DriverTab(), VehicleTab(), PriceTab()]
    try:
        customer.fill_data({"lot_number": LOT_NUMBER})
        cargo.fill_data({"lot_number": "ЛОТ-ИЗ-ТАБЛИЦЫ"})

        zayavka = collect_havaly_data(WindowStub(widgets))["zayavka"]

        assert zayavka["lot_number"] == LOT_NUMBER
    finally:
        for widget in widgets:
            widget.deleteLater()


def test_collect_cargo_lot_number_used_when_zayavka_silent(qt_app):
    """Шапка молчит — номер лота подхватывается с вкладки «Груз»."""
    widgets = [
        CustomerTab(), CargoTab(), RouteTab(),
        DriverTab(), VehicleTab(), PriceTab(),
    ]
    try:
        widgets[0].fill_data({"date": DATE_ISO})
        widgets[1].fill_data({"lot_number": LOT_NUMBER})

        zayavka = collect_havaly_data(WindowStub(widgets))["zayavka"]

        assert zayavka["lot_number"] == LOT_NUMBER
    finally:
        for widget in widgets:
            widget.deleteLater()
