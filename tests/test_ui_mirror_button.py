#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты кнопки «Отзеркалить из Экспедиторства» в окнах типов (ШАГ FIX-4).

Проверяется UI-часть зеркала данных:

  * кнопка есть у типов-целей (Формика, Логистикс Рус) и её нет у Аренды,
    Хавалов и самого Экспедиторства;
  * нажатие без источника показывает предупреждение и ничего не меняет;
  * нажатие с источником раскладывает данные источника по вкладкам;
  * заполненная цель даёт диалог конфликтов, и все три ответа —
    «Перезаписать всё» / «Не перезаписывать заполненное» / «Отмена» —
    работают так, как обещано пользователю.

Qt — в offscreen-режиме. Источник подменяется заглушкой: настоящий
MainWindow тянет за собой базу, настройки и справочники, а зеркалу от него
нужны ровно семь методов вкладок (см. tests/test_mirror.py). Заглушка
регистрируется в реестре окна-источника так же, как это делает MainWindow.

Все данные синтетические, реальных ПДн нет.
"""

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtCore import QDate  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core.mirror import SOURCE_TYPE, SUPPORTED_TARGETS  # noqa: E402
from ui.windows.base_window import (  # noqa: E402
    MIRROR_BUTTON_TITLE,
    clear_source_windows,
    find_expedition_window,
    register_source_window,
)
from ui.windows.formika import FormikaWindow  # noqa: E402
from ui.windows.logistiks_rus import LogistiksRusWindow  # noqa: E402
from ui.windows.logistiks_rus.tabs.route_tab import (  # noqa: E402
    DEFAULT_SHIPPER_NAME,
)

#: Цели зеркала: класс окна и его ключ типа.
TARGETS = (
    (FormikaWindow, "formika"),
    (LogistiksRusWindow, "logistiks_rus"),
)

#: Данные источника, которых достаточно для непустого плана.
SOURCE_DATA = {
    "driver": {
        "full_name": "Иванов Иван Иванович",
        "birth_date": "1980-01-01",
        "passport_series": "18 22",
        "passport_number": "926830",
        "license_series": "99 36",
        "license_number": "123456",
        "phone": "+7 (999) 123-45-67",
    },
    "vehicles": [
        {"brand_model": "JETOUR T2", "vin": "EC3TEUMB0T0002608"},
    ],
    "loadings": [
        {
            "name": "ООО «Салон Север»",
            "address": "183052, г. Мурманск, пр. Кольский, д. 53",
            "date": "2026-09-24",
            "time_window": "09:00-18:00",
        },
        {
            "name": "ООО «Салон Юг»",
            "address": "г. Мурманск, ул. Портовая, д. 7",
            "date": "2026-09-24",
            "time_window": "10:00-12:00",
        },
    ],
    "unloadings": [
        {
            "name": "ООО «Салон Кавказ»",
            "address": "г. Пятигорск, Бештаугорское шоссе 17",
            "date": "2026-09-27",
            "time_window": "",
        },
    ],
    "tractor": {
        "brand_model": "Foton Auman",
        "plate_number": "O844XY196",
        "vehicle_type": "Седельный тягач",
    },
    "trailer": {"brand_model": "YANGMINDA", "plate_number": "71ABF18"},
    "contract": {
        "number": "23092026-74",
        "date": "2026-09-23",
        "route": "Мурманск - Пятигорск",
        "price_without_vat": 180300.0,
        "vat_rate": "22%",
        "payment_days": 10,
        "special_conditions": "Погрузка по звонку",
    },
}


# ─────────────────────────────────────────────────────────────
# Заглушки источника
# ─────────────────────────────────────────────────────────────

class _SimpleTab:
    """Вкладка-заглушка с одним методом на все случаи."""

    def __init__(self, value):
        self.value = value

    def get_data(self):
        return self.value

    def get_loadings(self):
        return self.value

    def get_unloadings(self):
        return self.value

    def get_tractor_data(self):
        return self.value

    def get_trailer_data(self):
        return self.value


class FakeExpedition:
    """Окно «Экспедиторство» с данными (или без них)."""

    CONTRACT_TYPE = SOURCE_TYPE

    def __init__(self, data=None):
        data = data or {}
        self.driver_tab = _SimpleTab(data.get("driver") or {})
        self.vehicles_tab = _SimpleTab(data.get("vehicles") or [])
        self.trailer_tab = self._TrailerTab(
            data.get("tractor") or {}, data.get("trailer") or {}
        )
        self.contract_tab = self._ContractTab(
            data.get("contract") or {},
            data.get("loadings") or [],
            data.get("unloadings") or [],
        )

    class _TrailerTab:
        def __init__(self, tractor, trailer):
            self._tractor = tractor
            self._trailer = trailer

        def get_tractor_data(self):
            return self._tractor

        def get_trailer_data(self):
            return self._trailer

    class _ContractTab:
        def __init__(self, contract, loadings, unloadings):
            self._contract = contract
            self._loadings = loadings
            self._unloadings = unloadings

        def get_data(self):
            return self._contract

        def get_loadings(self):
            return self._loadings

        def get_unloadings(self):
            return self._unloadings


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def clean_registry():
    """Реестр окна-источника не должен переживать тест."""
    clear_source_windows()
    yield
    clear_source_windows()


@pytest.fixture
def quiet_messages(monkeypatch):
    """
    Глушит диалоги QMessageBox и запоминает их тексты.

    Модальный QMessageBox в offscreen-режиме роняет прогон (ШАГ FIX-5),
    поэтому все тесты нажатия идут через эту фикстуру.
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
def source_factory(monkeypatch):
    """
    Подменяет поиск окна «Экспедиторство» и возвращает фабрику источников.

    Патчится именно find_expedition_window: по нему работают и подсказка
    кнопки, и сам перенос (core/mirror.py зовёт эту же функцию).
    """
    import ui.windows.base_window as base_window

    def _make(data=None):
        window = FakeExpedition(data if data is not None else SOURCE_DATA)
        monkeypatch.setattr(base_window, "find_expedition_window", lambda: window)
        return window

    # По умолчанию источника нет вовсе.
    monkeypatch.setattr(base_window, "find_expedition_window", lambda: None)
    return _make


def _make_window(qt_app, cls):
    """Создаёт окно типа; вызывающий обязан его закрыть (force_close)."""
    return cls()


@pytest.fixture
def windows(qt_app, quiet_messages):
    """Фабрика окон типов с гарантированным закрытием после теста."""
    created = []

    def _factory(cls):
        window = cls()
        created.append(window)
        return window

    yield _factory

    for window in created:
        pool = getattr(window, "thread_pool", None)
        if pool is not None:
            pool.waitForDone(5000)
        qt_app.processEvents()
        window.force_close()


@pytest.fixture
def no_dialog(qt_app, monkeypatch):
    """
    Заглушает показ QMessageBox, не запоминая содержимое.

    Нужен тестам, которые вызывают слот диалога напрямую и проверяют только
    его ответ: подменяется ровно exec_ (возврат 0 = диалог закрыт), а
    clickedButton остаётся пустым — это и есть «закрыли крестиком».
    """
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)
    return True


@pytest.fixture
def popup(qt_app, monkeypatch):
    """
    Подменяет показ диалога конфликтов и запоминает его содержимое.

    Настоящий модальный QMessageBox в offscreen-режиме роняет прогон
    (access violation — грабли ШАГА FIX-5), поэтому окно строится по-настоящему
    (класс QMessageBox), а показ и «нажатие» кнопки подменяются так же, как
    в тестах окон типов: патчем методов класса, а не подменой объекта.

    :return: объект с полями text, info, buttons, events и click(label).
    """

    class _Popup:
        def __init__(self):
            self.text = ""
            self.info = ""
            self.title = ""
            self.buttons = []
            self.exec_calls = 0
            self.events = []
            self.label = None

        def click(self, label, kind="click") -> None:
            """
            Что «нажал» пользователь.

            :param label: текст кнопки или None (закрытие диалога крестиком).
            :param kind: "click" — обычное нажатие, "escape" — Esc/крестик.
            """
            self.label = label
            self.kind = kind

    state = _Popup()
    state.kind = "click"

    origin_add_button = QMessageBox.addButton

    def spy_add_button(self, *args, **kwargs):
        button = origin_add_button(self, *args, **kwargs)
        state.buttons.append(button.text())
        return button

    def spy_set_text(self, value):
        state.text = value
        return None

    def spy_set_info(self, value):
        state.info = value
        return None

    def spy_set_title(self, value):
        state.title = value
        return None

    def spy_exec(self) -> int:
        state.exec_calls += 1
        if state.label is None:
            self._clicked = None
        else:
            self._clicked = next(
                (button for button in self.buttons()
                 if button.text() == state.label),
                None,
            )
        state.events.append(
            (state.kind, state.label, state.exec_calls)
        )
        return 0

    monkeypatch.setattr(QMessageBox, "addButton", spy_add_button)
    monkeypatch.setattr(QMessageBox, "exec_", spy_exec)
    monkeypatch.setattr(QMessageBox, "setText", spy_set_text)
    monkeypatch.setattr(QMessageBox, "setInformativeText", spy_set_info)
    monkeypatch.setattr(QMessageBox, "setWindowTitle", spy_set_title)
    monkeypatch.setattr(
        QMessageBox, "clickedButton", lambda self: getattr(self, "_clicked", None)
    )
    return state


# ─────────────────────────────────────────────────────────────
# B.6. Кнопка: наличие и отсутствие
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cls,target_type", TARGETS)
def test_button_present_in_target_windows(windows, cls, target_type):
    """У Формики и Логистикса кнопка зеркала есть, и она активна."""
    window = windows(cls)

    assert hasattr(window, "btn_mirror")
    assert window.btn_mirror.text() == MIRROR_BUTTON_TITLE
    assert window.CONTRACT_TYPE == target_type
    assert window.CONTRACT_TYPE in SUPPORTED_TARGETS
    # Кнопка стоит в шапке рядом с «Выход», а не на вкладке.
    assert window.btn_mirror.parentWidget() is window.btn_exit.parentWidget()
    assert window.btn_mirror.isEnabled() is True


def test_button_present_in_formika_window(windows):
    """Формика — цель зеркала."""
    assert hasattr(windows(FormikaWindow), "btn_mirror")


def test_button_present_in_logistiks_window(windows):
    """Логистикс Рус — цель зеркала."""
    assert hasattr(windows(LogistiksRusWindow), "btn_mirror")


def test_button_absent_in_arenda_window(windows):
    """Аренда зеркало не поддерживает — кнопки нет."""
    from ui.windows.arenda_ts import ArendaTsWindow

    window = windows(ArendaTsWindow)

    assert getattr(window, "btn_mirror", None) is None
    assert window.CONTRACT_TYPE not in SUPPORTED_TARGETS


def test_button_absent_in_havaly_window(windows):
    """Хавалы зеркало не поддерживают — кнопки нет."""
    from ui.windows.havaly import HavalyWindow

    window = windows(HavalyWindow)

    assert getattr(window, "btn_mirror", None) is None
    assert window.CONTRACT_TYPE not in SUPPORTED_TARGETS


def test_button_absent_in_expedition_window(qt_app):
    """
    Экспедиторство — ИСТОЧНИК, а не цель: кнопки у него нет.

    Настоящий MainWindow здесь не поднимаем (база, настройки, справочники):
    проверяется контракт, от которого зависит зеркало, — тип окна и то, что
    шапку с кнопками типов-целей оно не наследует.
    """
    from ui.main_window import MainWindow
    from ui.windows.base_window import BaseContractWindow

    assert MainWindow.CONTRACT_TYPE == SOURCE_TYPE
    assert SOURCE_TYPE not in SUPPORTED_TARGETS
    # Кнопка живёт в _build_header базового окна типа; MainWindow — не его
    # подкласс, поэтому и кнопки, и слота зеркала у него быть не может.
    assert not issubclass(MainWindow, BaseContractWindow)
    assert not hasattr(MainWindow, "_on_mirror_clicked")


def test_button_state_follows_source(windows, source_factory, monkeypatch):
    """
    Кнопка остаётся активной, а её подсказка говорит о состоянии источника.

    Активность не меняется намеренно: окно-источник живёт в другом окне,
    и выключенная кнопка не объяснила бы оператору, чего не хватает.
    """
    window = windows(FormikaWindow)

    window._refresh_mirror_button()
    assert window.btn_mirror.isEnabled() is True
    assert "не открывалось" in window.btn_mirror.toolTip()

    empty = source_factory({})
    window._refresh_mirror_button()
    assert window.btn_mirror.isEnabled() is True
    assert "нет данных" in window.btn_mirror.toolTip()
    assert empty is not None

    source_factory(SOURCE_DATA)
    window._refresh_mirror_button()
    assert window.btn_mirror.isEnabled() is True
    assert "Перенести данные рейса" in window.btn_mirror.toolTip()


def test_source_registration_is_found_by_registry(qt_app):
    """
    Реестр окна-источника работает без monkeypatch.

    MainWindow регистрирует себя в конце __init__ — этот тест проверяет
    сам механизм: зарегистрированное окно находится, забытое (без слабой
    ссылки и с другим типом) — нет.
    """
    window = FakeExpedition(SOURCE_DATA)
    assert find_expedition_window() is None

    register_source_window(window)
    assert find_expedition_window() is window

    foreign = FakeExpedition(SOURCE_DATA)
    foreign.CONTRACT_TYPE = "formika"
    clear_source_windows()
    register_source_window(foreign)
    assert find_expedition_window() is None


def test_button_disabled_when_source_empty_is_tooltip_only(windows, source_factory):
    """
    Пустой источник: кнопка нажимается, но подсказка предупреждает.

    Отдельная проверка на «нет источника» и «источник пуст» — два разных
    сообщения: оператору важно понимать, открывать окно или заполнять.
    """
    source_factory({})
    window = windows(LogistiksRusWindow)

    window._refresh_mirror_button()
    assert "нет данных" in window.btn_mirror.toolTip()

    source_factory(SOURCE_DATA)
    window._refresh_mirror_button()
    assert "нет данных" not in window.btn_mirror.toolTip()


def test_button_enabled_when_source_has_data(windows, source_factory):
    """Источник с данными: кнопка активна и подсказка обычная."""
    source_factory(SOURCE_DATA)
    window = windows(FormikaWindow)

    window._refresh_mirror_button()
    assert window.btn_mirror.isEnabled() is True
    assert window.btn_mirror.toolTip().startswith("Перенести данные рейса")


# ─────────────────────────────────────────────────────────────
# B.6. Нажатие: без источника и с источником
# ─────────────────────────────────────────────────────────────

def _align_target_defaults(window) -> None:
    """
    Приводит значения цели по умолчанию к источнику — до зеркала.

    Часть полей цели заполнена всегда, без участия оператора: дата документа
    (сегодня), дата рождения водителя (30 лет назад), грузоотправитель
    Логистикса (в этой заявке он постоянный). Без выравнивания конфликты по
    ним будут в каждом переносе — см. test_date_field_conflicts_on_fresh_form.
    Здесь же нужен чистый случай «цель не спорит с источником».
    """
    window.customer_tab.fill_data({"date": SOURCE_DATA["contract"]["date"]})
    window.driver_tab.fill_data({"birth_date": SOURCE_DATA["driver"]["birth_date"]})

    route_tab = getattr(window, "route_tab", None)
    if route_tab is not None and "shipper_name" in route_tab.get_data():
        route_tab.fill_data({"shipper_name": SOURCE_DATA["loadings"][0]["name"]})


def _fill_target(window, data):
    """Заполняет вкладки цели до зеркала (ручной ввод оператора)."""
    window.driver_tab.fill_data(data["driver_tab"])
    window.customer_tab.fill_data(data["customer_tab"])
    window.route_tab.fill_data(data["route_tab"])


def _tab_snapshot(window):
    """Снимок данных всех вкладок окна — для проверки «ничего не изменилось»."""
    return {
        key: window._tab_data(window._tab_by_key(key)) for key in window._TAB_KEYS
    }

def test_click_with_no_source_shows_warning(windows, quiet_messages):
    """Нет источника — предупреждение и ни одного изменения в форме."""
    window = windows(FormikaWindow)
    before = {key: window._tab_data(window._tab_by_key(key))
              for key in window._TAB_KEYS}

    window.btn_mirror.click()

    assert quiet_messages["warning"], "предупреждение не показано"
    assert "Не удалось получить данные из Экспедиторства" in quiet_messages["warning"][0]
    assert quiet_messages["information"] == []

    after = {key: window._tab_data(window._tab_by_key(key))
             for key in window._TAB_KEYS}
    assert after == before


def test_click_with_empty_source_shows_warning(windows, quiet_messages,
                                               source_factory):
    """Источник есть, но пуст — то же предупреждение, форма не меняется."""
    source_factory({})
    window = windows(LogistiksRusWindow)
    before = window.route_tab.get_data()

    window.btn_mirror.click()

    assert quiet_messages["warning"]
    assert "Не удалось получить данные из Экспедиторства" in quiet_messages["warning"][0]
    assert window.route_tab.get_data() == before


def test_click_fills_tabs_from_source_formika(windows, quiet_messages,
                                              source_factory, popup):
    """
    Формика: данные источника расходятся по пяти вкладкам.

    У свежей формы дата заявки уже стоит (сегодняшняя), поэтому диалог
    конфликтов появляется и здесь — это и есть штатный путь оператора.
    Отвечаем «Не перезаписывать заполненное»: дата остаётся своя, пустые
    поля заполняются. Что дату можно и переписать, проверяет
    test_click_choose_overwrite_all.
    """
    source_factory(SOURCE_DATA)
    window = windows(FormikaWindow)

    popup.click("Не перезаписывать заполненное")
    window.btn_mirror.click()

    assert popup.exec_calls == 1, "конфликт по дате должен быть показан"
    assert quiet_messages["warning"] == []
    assert "Данные перенесены из Экспедиторства" in quiet_messages["information"][0]

    customer = window.customer_tab.get_data()
    assert customer["number"] == "23092026-74"

    assert window.cargo_tab.get_data()["vehicles"] == [
        {"brand_model": "JETOUR T2", "vin": "EC3TEUMB0T0002608"},
    ]

    route = window.route_tab.get_data()
    assert route["route"] == "Мурманск - Пятигорск"
    assert route["loading_address"] == "183052, г. Мурманск, пр. Кольский, д. 53"
    assert route["unloading_address"] == "г. Пятигорск, Бештаугорское шоссе 17"

    assert window.driver_tab.get_data()["full_name"] == "Иванов Иван Иванович"

    vehicle = window.vehicle_tab.get_data()
    assert vehicle["tractor_brand"] == "Foton Auman"
    assert vehicle["tractor_plate"] == "O844XY196"
    assert vehicle["tractor_type"] == "Седельный тягач"
    assert vehicle["trailer_plate"] == "71ABF18"

    # Стоимость не переносится: в вкладке одни её собственные значения по
    # умолчанию (ставка 22%, срок 10 дней), а суммы источника — 180300.0
    # и «Погрузка по звонку» — сюда не попали.
    price = window.price_tab.get_data()
    assert price["amount"] == 0
    assert price["amount_without_vat"] == 0.0
    assert price["amount_with_vat"] == 0.0
    assert not price.get("special_conditions")


def test_click_fills_tabs_from_source_logistiks(windows, quiet_messages,
                                                source_factory, popup):
    """
    Логистикс: адреса погрузки — все, грузополучатели — с наименованиями.

    У свежей формы уже заполнены дата, дата рождения водителя и
    грузоотправитель (он у этого типа постоянный), поэтому диалог конфликтов
    будет; отвечаем «Не перезаписывать заполненное» — так оператор и работает,
    пока не решит иначе.
    """
    source_factory(SOURCE_DATA)
    window = windows(LogistiksRusWindow)

    popup.click("Не перезаписывать заполненное")
    window.btn_mirror.click()

    assert popup.exec_calls == 1
    assert quiet_messages["warning"] == []
    assert "Данные перенесены из Экспедиторства" in quiet_messages["information"][0]

    customer = window.customer_tab.get_data()
    assert customer["number"] == "23092026-74"

    route = window.route_tab.get_data()
    assert route["route"] == "Мурманск - Пятигорск"
    assert route["loading_addresses"] == [
        "183052, г. Мурманск, пр. Кольский, д. 53",
        "г. Мурманск, ул. Портовая, д. 7",
    ]
    assert route["consignees"] == [
        {
            "name": "ООО «Салон Кавказ»",
            "address": "г. Пятигорск, Бештаугорское шоссе 17",
        },
    ]
    # Грузоотправитель оставлен свой: у этой заявки он постоянный, и ответ
    # «не перезаписывать» его не трогает (что имя источника всё-таки
    # переносится, проверяет test_logistiks_shipper_name_is_mirrored).
    assert route["shipper_name"] == DEFAULT_SHIPPER_NAME

    assert window.driver_tab.get_data()["full_name"] == "Иванов Иван Иванович"
    assert window.vehicle_tab.get_data()["tractor_plate"] == "O844XY196"

    # Стоимость Логистикса — тоже не зеркало: суммы источника (180300.0)
    # и его особые условия во вкладку не попали.
    price = window.price_tab.get_data()
    assert not price.get("amount_without_vat")
    assert not price.get("amount_with_vat")
    assert not price.get("special_conditions")


def test_logistiks_shipper_name_is_mirrored(windows, source_factory, popup):
    """Грузоотправитель источника переносится, если разрешена перезапись."""
    source_factory(SOURCE_DATA)
    window = windows(LogistiksRusWindow)

    popup.click("Перезаписать всё")
    window.btn_mirror.click()

    route = window.route_tab.get_data()
    assert route["shipper_name"] == "ООО «Салон Север»"
    assert route["loading_addresses"] == [
        "183052, г. Мурманск, пр. Кольский, д. 53",
        "г. Мурманск, ул. Портовая, д. 7",
    ]
    assert [point["name"] for point in route["consignees"]] == [
        "ООО «Салон Кавказ»",
    ]


@pytest.mark.parametrize("cls,target_type", TARGETS)
def test_date_field_conflicts_on_fresh_form(windows, source_factory, popup,
                                            cls, target_type):
    """
    Нетронутая форма тоже спорит с источником — своими значениями по умолчанию.

    У вкладки «Заказчик» дата заполнена всегда (сегодняшним числом), у
    вкладки «Водитель» — дата рождения (30 лет назад), у маршрута Логистикса
    — грузоотправитель. Это ФОРМА, а не ввод оператора, но диалог конфликтов
    показывается и здесь — и это правильно: пользователь должен знать, что
    даты документа и грузоотправитель будут заменены данными источника.
    """
    source_factory(SOURCE_DATA)
    window = windows(cls)

    popup.click("Перезаписать всё")
    window.btn_mirror.click()

    assert popup.exec_calls == 1
    assert re.fullmatch(r"В целевом окне уже заполнено полей: \d+", popup.text)
    assert "Заказчик.Дата" in popup.info
    # Ответ «Перезаписать всё»: дата источника доехала до вкладки.
    assert window.customer_tab.get_data()["date"] == "2026-09-23"
    assert window.customer_tab.get_data()["date"] != QDate.currentDate().toString(
        "yyyy-MM-dd"
    )


@pytest.mark.parametrize("cls,target_type", TARGETS)
def test_click_on_non_conflicting_target_has_no_dialog(windows, quiet_messages,
                                                       source_factory, popup,
                                                       cls, target_type):
    """
    Цель не спорит с источником — диалога нет, данные раскладываются молча.

    Значения по умолчанию цели (дата, дата рождения, грузоотправитель)
    выравниваем по источнику до зеркала: без этого конфликт был бы всегда
    (см. test_date_field_conflicts_on_fresh_form).
    """
    source_factory(SOURCE_DATA)
    window = windows(cls)
    _align_target_defaults(window)

    window.btn_mirror.click()

    assert popup.exec_calls == 0, "диалог конфликтов не должен показываться"
    assert quiet_messages["information"] == ["Данные перенесены из Экспедиторства."]
    assert window.customer_tab.get_data()["number"] == "23092026-74"
    assert window.customer_tab.get_data()["date"] == "2026-09-23"
    assert window.vehicle_tab.get_data()["tractor_plate"] == "O844XY196"


# ─────────────────────────────────────────────────────────────
# B.6. Диалог конфликтов: три ответа
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def filled_target():
    """Заполненная цель: номер заявки, ФИО и адрес погрузки уже введены."""
    return {
        "driver_tab": {"full_name": "Петров Пётр Петрович"},
        "customer_tab": {"number": "ФМ-2026-1"},
        "route_tab": {"loading_address": "старый адрес погрузки"},
    }


def test_click_shows_conflicts_dialog_when_target_filled(
    windows, quiet_messages, source_factory, popup, filled_target
):
    """Заполненная цель: диалог показывает конфликты с парой «было/станет»."""
    source_factory(SOURCE_DATA)
    window = windows(FormikaWindow)
    _fill_target(window, filled_target)

    popup.click("Не перезаписывать заполненное")
    window.btn_mirror.click()

    assert popup.exec_calls == 1, "диалог конфликтов не показан"

    # В диалоге видно, сколько полей уже заполнено и что с ними будет.
    assert re.fullmatch(r"В целевом окне уже заполнено полей: \d+", popup.text)
    assert "Водитель.ФИО водителя" in popup.info
    assert "Было: Петров Пётр Петрович" in popup.info
    assert "Станет: Иванов Иван Иванович" in popup.info
    assert "Заказчик.Номер" in popup.info
    assert "Маршрут.Адрес погрузки" in popup.info
    assert popup.info.endswith(
        "Перезаписать эти поля данными из Экспедиторства?"
    )


def test_conflicts_list_names_real_tabs_and_values(windows, source_factory,
                                                   filled_target, no_dialog):
    """Список конфликтов называет вкладки окна и их подписи полей."""
    from core.mirror import (
        collect_conflicts, collect_source, plan_for_formika,
    )

    source = source_factory(SOURCE_DATA)
    window = windows(FormikaWindow)
    _fill_target(window, filled_target)

    plan = plan_for_formika(collect_source(source))
    conflicts = collect_conflicts(window, plan, target_type="formika")

    found = {(tab, field) for tab, field, _old, _new in conflicts}
    assert ("driver_tab", "full_name") in found
    assert ("customer_tab", "number") in found
    assert ("route_tab", "loading_address") in found

    # Диалог конфликтов вызывается напрямую (показ заглушён): проверяем
    # и ответ по «крестику», и текст, который увидит оператор.
    assert window._ask_mirror_conflicts(conflicts) is None
    rendered = window._format_mirror_conflicts(conflicts)
    assert "Водитель.ФИО водителя" in rendered
    assert "Было: Петров Пётр Петрович" in rendered
    assert "Станет: Иванов Иван Иванович" in rendered


def test_click_choose_overwrite_all(windows, quiet_messages, source_factory,
                                    popup, filled_target):
    """«Перезаписать всё»: заполненное заменяется данными источника."""
    source_factory(SOURCE_DATA)
    window = windows(FormikaWindow)
    _fill_target(window, filled_target)

    popup.click("Перезаписать всё")
    window.btn_mirror.click()

    assert window.driver_tab.get_data()["full_name"] == "Иванов Иван Иванович"
    assert window.customer_tab.get_data()["number"] == "23092026-74"
    assert window.route_tab.get_data()["loading_address"] == (
        "183052, г. Мурманск, пр. Кольский, д. 53"
    )
    assert "Данные перенесены из Экспедиторства" in quiet_messages["information"][0]


def test_click_choose_keep_manual(windows, quiet_messages, source_factory,
                                  popup, filled_target):
    """«Не перезаписывать заполненное»: ручной ввод остаётся, пустое — нет."""
    source_factory(SOURCE_DATA)
    window = windows(FormikaWindow)
    _fill_target(window, filled_target)

    popup.click("Не перезаписывать заполненное")
    window.btn_mirror.click()

    # Заполненное вручную не тронуто.
    assert window.driver_tab.get_data()["full_name"] == "Петров Пётр Петрович"
    assert window.customer_tab.get_data()["number"] == "ФМ-2026-1"
    assert window.route_tab.get_data()["loading_address"] == "старый адрес погрузки"

    # А пустые поля вкладок заполнены — перенос не отменён, а смягчён.
    # Дата цели не перезаписана: у свежей формы там сегодняшнее число.
    customer = window.customer_tab.get_data()
    assert customer["date"] == QDate.currentDate().toString("yyyy-MM-dd")
    assert window.route_tab.get_data()["route"] == "Мурманск - Пятигорск"
    assert window.vehicle_tab.get_data()["tractor_plate"] == "O844XY196"
    assert "Данные перенесены из Экспедиторства" in quiet_messages["information"][0]


def test_click_choose_cancel_does_nothing(windows, quiet_messages, source_factory,
                                          popup, filled_target):
    """«Отмена»: форма не меняется вовсе, успеха не сообщают."""
    source_factory(SOURCE_DATA)
    window = windows(LogistiksRusWindow)
    _fill_target(window, filled_target)

    before = {
        key: window._tab_data(window._tab_by_key(key)) for key in window._TAB_KEYS
    }

    popup.click("Отмена")
    window.btn_mirror.click()

    after = {
        key: window._tab_data(window._tab_by_key(key)) for key in window._TAB_KEYS
    }
    assert after == before
    assert quiet_messages["information"] == []
    assert quiet_messages["warning"] == []
    assert window.driver_tab.get_data()["full_name"] == "Петров Пётр Петрович"


@pytest.mark.parametrize("label,expected", (
    ("Перезаписать всё", True),
    ("Не перезаписывать заполненное", False),
    ("Отмена", None),
    (None, None),   # закрытие диалога крестиком или Esc
))
def test_ask_mirror_conflicts_answers(windows, popup, label, expected):
    """Слот диалога возвращает True / False / None по нажатой кнопке."""
    window = windows(FormikaWindow)
    conflicts = [("driver_tab", "full_name", "Петров", "Иванов")]

    popup.click(label)
    assert window._ask_mirror_conflicts(conflicts) is expected

    # В диалоге есть все три кнопки выбора.
    assert popup.buttons == [
        "Перезаписать всё", "Не перезаписывать заполненное", "Отмена",
    ]
    assert "В целевом окне уже заполнено полей: 1" in popup.text
    assert popup.info.endswith(
        "Перезаписать эти поля данными из Экспедиторства?"
    )


def test_ask_mirror_conflicts_escape_returns_none(windows, popup):
    """Esc или крестик закрывают диалог: перенос отменяется."""
    window = windows(FormikaWindow)

    popup.click(None, kind="escape")
    assert window._ask_mirror_conflicts(
        [("driver_tab", "full_name", "Петров", "Иванов")]
    ) is None
    assert popup.events == [("escape", None, 1)]


def test_conflict_dialog_shortens_long_values(windows, popup):
    """Длинные значения в диалоге обрезаются, списки — считаются."""
    window = windows(FormikaWindow)
    conflicts = [
        ("cargo_tab", "vehicles", [1, 2, 3], [4, 5]),
        ("route_tab", "loading_address", "а" * 200, ""),
    ]

    popup.click("Отмена")
    window._ask_mirror_conflicts(conflicts)

    assert "Было: 3 записей" in popup.info
    assert "Станет: 2 записей" in popup.info
    assert "Станет: —" in popup.info
    assert "а" * 200 not in popup.info


# ─────────────────────────────────────────────────────────────
# Раскладка плана по вкладкам (вспомогательные методы окна)
# ─────────────────────────────────────────────────────────────

def test_tab_by_key_returns_real_tabs(windows):
    """_tab_by_key находит вкладки по ключам плана, а не по именам атрибутов."""
    window = windows(FormikaWindow)

    assert window._tab_by_key("customer_tab") is window.customer_tab
    assert window._tab_by_key("cargo_tab") is window.cargo_tab
    assert window._tab_by_key("route_tab") is window.route_tab
    assert window._tab_by_key("driver_tab") is window.driver_tab
    assert window._tab_by_key("vehicle_tab") is window.vehicle_tab
    assert window._tab_by_key("price_tab") is window.price_tab

    assert window._tab_by_key("нет_такой_вкладки") is None


def test_is_filled_rules(windows):
    """Пустота поля цели: None, "", [], bool — пусто; 0 — значение."""
    window = windows(FormikaWindow)

    assert window._is_filled(None) is False
    assert window._is_filled("") is False
    assert window._is_filled("   ") is False
    assert window._is_filled([]) is False
    assert window._is_filled(True) is False
    assert window._is_filled(False) is False

    assert window._is_filled(0) is True
    assert window._is_filled("0") is True
    assert window._is_filled("адрес") is True
    assert window._is_filled([{"vin": "X"}]) is True


def test_apply_mirror_plan_skips_missing_tab(windows, source_factory,
                                             quiet_messages):
    """Вкладки, которой нет в окне, план не ломает."""
    from core.mirror import MirrorPlan

    window = windows(FormikaWindow)
    plan = MirrorPlan(tabs={
        "driver_tab": {"full_name": "Иванов Иван Иванович"},
        "нет_такой_вкладки": {"поле": "значение"},
    })

    window._apply_mirror_plan(plan, overwrite_existing=False)

    assert window.driver_tab.get_data()["full_name"] == "Иванов Иван Иванович"


def test_mirror_value_text_shortens_lists_and_long_text(windows):
    """Значения в диалоге: списки — количеством, длинные строки — обрезкой."""
    window = windows(FormikaWindow)

    assert window._mirror_value_text([]) == "0 записей"
    assert window._mirror_value_text([1, 2, 3]) == "3 записей"
    assert window._mirror_value_text("") == "—"
    assert window._mirror_value_text(None) == "—"
    assert window._mirror_value_text("короткое") == "короткое"

    long_value = "а" * 200
    rendered = window._mirror_value_text(long_value)
    assert len(rendered) == 60
    assert rendered.endswith("…")


def test_mirror_field_titles_cover_plan(windows, source_factory, popup):
    """Диалог конфликтов переводит имена полей плана на русский."""
    source_factory(SOURCE_DATA)
    window = windows(FormikaWindow)

    popup.click("Не перезаписывать заполненное")
    window.btn_mirror.click()

    for field in ("number", "date", "route", "loading_address",
                  "unloading_address", "vehicles", "full_name"):
        assert window._mirror_field_title(field) != field

    # Незнакомое поле печатается своим ключом, а не пустой строкой.
    assert window._mirror_field_title("неизвестное_поле") == "неизвестное_поле"

    # И в самом диалоге стоят русские подписи, а не ключи.
    assert "Заказчик.Дата" in popup.info
    assert "Водитель.Дата рождения" in popup.info
    assert "customer_tab" not in popup.info
    assert "birth_date" not in popup.info
