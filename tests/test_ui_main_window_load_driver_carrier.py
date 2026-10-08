#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Загрузка водителя из справочника: перевозчик подтягивается вместе с ним
(ШАГ «Дерево перевозчиков + двусторонняя загрузка водитель ↔ перевозчик»,
часть A).

Баг, который закрыт: двойной клик по водителю подтягивал ТОЛЬКО водителя.
Перевозчик лежал в карточке (`drivers.default_carrier_id`), но в форму не
попадал — оператор выбирал его вручную и мог ошибиться.

Правила шага:

  * водитель привязан — перевозчик из справочника встаёт в форму
    перевозчика (и в списке вкладки «Водитель»);
  * перевозчик мягко удалён — всё равно подтягивается (ссылка её переживает);
  * привязки нет или запись не найдена — форма перевозчика НЕ очищается
    (там может быть ручной ввод оператора);
  * ошибка чтения перевозчика не мешает загрузить самого водителя.

Qt поднимается в offscreen-режиме, база — временная (`isolated_db`),
данные синтетические, Пдн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging  # noqa: E402

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

DRIVER_NAME = "Галушкин Петр Михайлович"
CARRIER_NAME = "ООО «Фас Транс»"
OTHER_CARRIER_NAME = "ООО «Ромашка»"


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def quiet_dialogs(monkeypatch):
    """Модальные окна не показываем: offscreen их не переживает."""
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)


@pytest.fixture
def window(qt_app, isolated_db, quiet_dialogs, monkeypatch):
    """
    MainWindow без GigaChat и без блокирующих диалогов.

    Уборка — как в `test_ui_main_window_carrier_match.py`: окно закрывается
    ПО-НАСТОЯЩЕМУ и снимается с реестра окна-источника зеркала, иначе
    `find_expedition_window()` находит его в чужих тестах.
    """
    from PyQt5.QtCore import QEvent

    from ui.main_window import MainWindow
    from ui.windows.base_window import clear_source_windows

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    win = MainWindow()
    yield win

    try:
        win.system_theme_watcher.stop()
    except Exception:  # noqa: BLE001 — наблюдателя может не быть вовсе
        pass
    win.force_close()
    win.deleteLater()
    app = QApplication.instance()
    if app is not None:
        app.sendPostedEvents(None, QEvent.DeferredDelete)
    clear_source_windows()


@pytest.fixture
def carrier_a(isolated_db):
    """Перевозчик, за которым закреплён водитель."""
    return isolated_db.save_organization(
        {
            "full_name": CARRIER_NAME,
            "inn": "7701234567",
            "kpp": "770101001",
            "director_name": "Петров Пётр Петрович",
            "bank_name": "ПАО «Фас-банк»",
            "license_number": "АК-123456",
            "license_date": "2024-05-06",
        },
        is_carrier=True,
    )


@pytest.fixture
def carrier_b(isolated_db):
    """Другой перевозчик — им заполняют форму до загрузки водителя."""
    return isolated_db.save_organization(
        {
            "full_name": OTHER_CARRIER_NAME,
            "inn": "7707654321",
            "kpp": "770701001",
            "director_name": "Сидоров Сидор Сидорович",
            "bank_name": "ПАО «Ромашка-банк»",
            "license_number": "БК-654321",
        },
        is_carrier=True,
    )


def _driver_record(isolated_db, carrier_id=None, **extra):
    """Синтетический водитель; carrier_id — привязка к перевозчику."""
    payload = {
        "full_name": DRIVER_NAME,
        "passport_series": "18 22",
        "passport_number": "926830",
        "phone": "+7 (999) 111-22-33",
    }
    if carrier_id is not None:
        payload["default_carrier_id"] = carrier_id
    payload.update(extra)

    driver_id = isolated_db.save_driver(payload)
    return isolated_db.load_driver(driver_id)


def _carrier_form(window):
    """Данные формы перевозчика (то, что уйдёт в договор)."""
    return window.carrier_tab.get_data()


def _break_carrier_link(isolated_db, carrier_id: int) -> None:
    """
    Оставляет у водителя ссылку на перевозчика, которого в базе уже нет.

    Обычным путём такую связь не записать: FOREIGN KEY не даёт. Поэтому
    строку перевозчика убираем ФИЗИЧЕСКИ (не мягко) при выключенном
    контроле ссылок — так выглядит база, из которой запись вычистили
    в обход справочника.
    """
    conn = isolated_db.get_connection()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM carriers WHERE id = ?", (carrier_id,))
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# A.2: перевозчик подтягивается
# ─────────────────────────────────────────────────────────────

def test_load_driver_pulls_carrier_when_linked(window, isolated_db, carrier_a):
    """Водитель с привязкой: перевозчик встаёт в форму сам."""
    record = _driver_record(isolated_db, carrier_a)

    window._load_driver_from_db(record)

    form = _carrier_form(window)
    assert form["full_name"] == CARRIER_NAME
    assert form["inn"] == "7701234567"
    assert form["director_name"] == "Петров Пётр Петрович"


def test_load_driver_clears_previous_carrier_data(
    window, isolated_db, carrier_a, carrier_b
):
    """Прежний перевозчик не оставляет следов: форма пересобирается целиком."""
    window._load_carrier_from_db(
        isolated_db.load_organization(carrier_b, is_carrier=True)
    )
    assert _carrier_form(window)["full_name"] == OTHER_CARRIER_NAME

    window._load_driver_from_db(_driver_record(isolated_db, carrier_a))

    form = _carrier_form(window)
    assert form["full_name"] == CARRIER_NAME
    assert form["bank_name"] == "ПАО «Фас-банк»"
    assert form["kpp"] == "770101001"


def test_load_driver_does_not_clear_carrier_when_unlinked(
    window, isolated_db, carrier_b
):
    """У водителя привязки нет — форма перевозчика остаётся как была."""
    window._load_carrier_from_db(
        isolated_db.load_organization(carrier_b, is_carrier=True)
    )

    window._load_driver_from_db(_driver_record(isolated_db))

    assert _carrier_form(window)["full_name"] == OTHER_CARRIER_NAME


def test_load_driver_handles_missing_carrier(
    window, isolated_db, carrier_a, carrier_b
):
    """Привязка ведёт на несуществующую запись — форма не трогается."""
    record = _driver_record(isolated_db, carrier_a)
    _break_carrier_link(isolated_db, carrier_a)
    window._load_carrier_from_db(
        isolated_db.load_organization(carrier_b, is_carrier=True)
    )

    window._load_driver_from_db(record)

    assert _carrier_form(window)["full_name"] == OTHER_CARRIER_NAME
    assert window.driver_tab.full_name.text() == DRIVER_NAME


def test_load_driver_handles_soft_deleted_carrier(window, isolated_db, carrier_a):
    """Мягко удалённый перевозчик всё равно подтягивается (ссылка живёт)."""
    isolated_db.delete_organization(carrier_a, is_carrier=True)

    window._load_driver_from_db(_driver_record(isolated_db, carrier_a))

    form = _carrier_form(window)
    assert form["full_name"] == CARRIER_NAME
    assert form["inn"] == "7701234567"


def test_load_driver_switches_to_driver_tab(window, isolated_db, carrier_a):
    """После загрузки открыта вкладка «Водитель» — оператор видит результат."""
    window.tabs.setCurrentWidget(window.carrier_tab)

    window._load_driver_from_db(_driver_record(isolated_db, carrier_a))

    assert window.tabs.currentWidget() is window.driver_tab


def test_load_driver_keeps_status_message(window, isolated_db, carrier_a):
    """В строке состояния — имя загруженного водителя (как и раньше)."""
    window._load_driver_from_db(_driver_record(isolated_db, carrier_a))

    assert DRIVER_NAME in window.statusBar().currentMessage()


def test_load_driver_logs_carrier_pulled(
    window, isolated_db, carrier_a, caplog
):
    """В лог уходит ID перевозчика — и ничего лишнего (логи без Пдн)."""
    caplog.set_level(logging.DEBUG, logger="ui.main_window")

    window._load_driver_from_db(_driver_record(isolated_db, carrier_a))

    assert f"Перевозчик подтянут из водителя: ID={carrier_a}" in caplog.text
    assert "перевозчик подтянут из карточки водителя" in caplog.text
    assert DRIVER_NAME not in caplog.text, "ФИО в лог не пишем"


def test_load_driver_does_not_touch_other_tabs(window, isolated_db, carrier_a):
    """Заказчик, машины и договор загрузкой водителя не меняются."""
    window.customer_tab.full_name.setText("ООО «Заказчик»")
    window.customer_tab.inn.setText("7712345678")
    customer_before = window.customer_tab.get_data()
    vehicles_before = window.vehicles_tab.get_data()
    contract_before = window.contract_tab.get_data()

    window._load_driver_from_db(_driver_record(isolated_db, carrier_a))

    assert window.customer_tab.get_data() == customer_before
    assert window.vehicles_tab.get_data() == vehicles_before
    assert window.contract_tab.get_data() == contract_before


def test_load_driver_calls_bind_reference_ids(
    window, isolated_db, carrier_a, monkeypatch
):
    """
    Загруженная карточка связывается с записью справочника при сохранении.

    `_load_driver_from_db` ссылки не запоминает — их восстанавливает
    `_on_save_to_db`: перевозчика ищет `find_organization_id` по реквизитам
    формы и кладёт найденный ID в договор (`bind_reference_ids`). Проверяем,
    что после загрузки карточки перевозчик в форме — ТА ЖЕ запись
    справочника, а не её копия.
    """
    from core.contract_data import ContractData

    bound = {}
    original = ContractData.bind_reference_ids

    def spy(self, **kwargs):
        bound.update(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(ContractData, "bind_reference_ids", spy)

    window.contract_tab.number.setText("TREE-1")
    window._load_driver_from_db(_driver_record(isolated_db, carrier_a))
    window._on_save_to_db()

    assert bound.get("carrier_id") == carrier_a
    assert bound.get("driver_id"), "водитель тоже связан с записью"


def test_load_driver_no_exception_on_corrupt_carrier_data(
    window, isolated_db, carrier_a, monkeypatch
):
    """Битые данные перевозчика не мешают загрузить водителя."""
    import ui.main_window as module

    def broken(org_id, is_carrier=False):
        raise RuntimeError("битая запись перевозчика")

    monkeypatch.setattr(module, "load_organization_by_id", broken)

    window._load_driver_from_db(_driver_record(isolated_db, carrier_a))

    assert window.driver_tab.full_name.text() == DRIVER_NAME
    assert window.tabs.currentWidget() is window.driver_tab


def test_load_driver_keeps_carrier_in_driver_tab_combo(
    window, isolated_db, carrier_a
):
    """
    Список перевозчиков вкладки перечитывается перед заполнением.

    Перевозчиков заводят в «Менеджере базы» во время работы: без
    перечитывания привязка водителя не нашлась бы в старом списке, и вкладка
    показала бы «— не указан —» (привязка потерялась бы при сохранении).
    """
    window._load_driver_from_db(_driver_record(isolated_db, carrier_a))

    assert window.driver_tab.carrier_combo.currentData() == carrier_a
    assert window.driver_tab.get_data()["default_carrier_id"] == carrier_a


# ─────────────────────────────────────────────────────────────
# Тягач и полуприцеп: водитель приходит с тем же набором полей
# ─────────────────────────────────────────────────────────────

def test_load_driver_with_tractor_pulls_trailer_data(
    window, isolated_db, carrier_a
):
    """Данные тягача из карточки попадают на вкладку «Тягач и полуприцеп»."""
    record = _driver_record(isolated_db, carrier_a)
    isolated_db.save_driver_vehicle(
        record["id"],
        {
            "tractor_brand": "Foton Auman",
            "tractor_plate": "O844XY196",
            "trailer_brand": "YANGMINDA",
            "trailer_plate": "71ABF18",
        },
    )

    window._load_driver_from_db(record)

    assert window.trailer_tab.get_tractor_data()["plate_number"] == "O844XY196"
    assert window.trailer_tab.get_trailer_data()["plate_number"] == "71ABF18"


def test_load_driver_with_tractor_only(window, isolated_db, carrier_a):
    """Заполнен только тягач — загружается он, прицеп остаётся пустым."""
    record = _driver_record(isolated_db, carrier_a)
    isolated_db.save_driver_vehicle(
        record["id"],
        {"tractor_brand": "Foton Auman", "tractor_plate": "O844XY196"},
    )

    window._load_driver_from_db(record)

    assert window.trailer_tab.get_tractor_data()["brand_model"] == "Foton Auman"
    assert window.trailer_tab.get_trailer_data()["plate_number"] == ""


def test_load_driver_with_trailer_only(window, isolated_db, carrier_a):
    """Заполнен только полуприцеп — он и загружается."""
    record = _driver_record(isolated_db, carrier_a)
    isolated_db.save_driver_vehicle(
        record["id"],
        {"trailer_brand": "YANGMINDA", "trailer_plate": "71ABF18"},
    )

    window._load_driver_from_db(record)

    assert window.trailer_tab.get_trailer_data()["brand_model"] == "YANGMINDA"
    assert window.trailer_tab.get_tractor_data()["plate_number"] == ""


def test_load_driver_without_vehicle_keeps_current_form(
    window, isolated_db, carrier_a
):
    """У водителя нет ТС — вкладка тягача очищена, а не осталась от прошлого."""
    window.trailer_tab.fill_data(
        {"brand_model": "Старый тягач", "plate_number": "A001AA"},
        {"brand_model": "Старый прицеп", "plate_number": "B002BB"},
    )

    window._load_driver_from_db(_driver_record(isolated_db, carrier_a))

    assert window.trailer_tab.get_tractor_data()["plate_number"] == ""
    assert window.trailer_tab.get_trailer_data()["plate_number"] == ""


def test_load_driver_pulls_carrier_with_license(window, isolated_db, carrier_a):
    """Лицензия перевозчика (есть только у carriers) доходит до формы."""
    window._load_driver_from_db(_driver_record(isolated_db, carrier_a))

    assert window.carrier_tab.license_number.text() == "АК-123456"
    assert window.carrier_tab.license_date.date().toString("yyyy-MM-dd") == "2024-05-06"


def test_load_driver_without_carrier_keeps_hand_filled_form(
    window, isolated_db
):
    """Ручной ввод в форме перевозчика не затирается водителем без привязки."""
    window.carrier_tab.clear()
    window.carrier_tab.full_name.setText("ООО «Временный перевозчик»")
    window.carrier_tab.inn.setText("7799999999")

    window._load_driver_from_db(_driver_record(isolated_db))

    assert window.carrier_tab.full_name.text() == "ООО «Временный перевозчик»"
    assert window.carrier_tab.inn.text() == "7799999999"
