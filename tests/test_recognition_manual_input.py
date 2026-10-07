#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Регресс-тесты: распознавание не должно стирать данные, введённые вручную.

Исходная жалоба: «в графу "заказчик" вношу данные вручную, но они пропадают».
Причина: модель по системному промпту отдаёт блок «customer» со всеми пустыми
строками («все реквизиты из текста — это перевозчик»), код считал такой блок
заполненным и вызывал fill_data(), который перезаписывал все поля пустыми.

Что проверяется теперь:
  * пустые поля ответа отбрасываются (filled_only / filled_only_list);
  * вкладки обновляют только те поля, которые реально пришли;
  * сценарий целиком через MainWindow._on_recognition_finished;
  * непустой ответ по-прежнему заполняет вкладки.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core.recognizer import filled_only, filled_only_list, has_content  # noqa: E402

#: Типичный «пустой» блок организации в ответе модели
EMPTY_ORG = {
    "full_name": "", "short_name": "", "inn": "", "kpp": "", "ogrn": "",
    "legal_address": "", "actual_address": "", "bank_account": "",
    "bik": "", "correspondent_account": "", "bank_name": "",
    "director_name": "", "director_position": "", "phone": "", "email": "",
}


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qt_app, monkeypatch):
    """MainWindow без GigaChat и без блокирующих диалогов."""
    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    return MainWindow()


# ─────────────────────────────────────────────────────────────
# Чистые функции отбора значений
# ─────────────────────────────────────────────────────────────

def test_has_content():
    assert has_content("ООО «Ромашка»") is True
    assert has_content("  ") is False
    assert has_content("") is False
    assert has_content(None) is False
    assert has_content(0) is True
    assert has_content([]) is False
    assert has_content([{"vin": "X"}]) is True


def test_filled_only_drops_empty_fields():
    section = dict(EMPTY_ORG)
    section["full_name"] = "ООО «Ромашка»"
    section["inn"] = "7701234567"

    result = filled_only(section)

    assert result == {"full_name": "ООО «Ромашка»", "inn": "7701234567"}
    assert filled_only(EMPTY_ORG) == {}
    assert filled_only(None) == {}
    assert filled_only("строка") == {}


def test_filled_only_list_drops_empty_rows():
    rows = [
        {"vin": "", "brand_model": "", "plate_number": ""},
        {"vin": "EC3TEUMB0T0002608", "brand_model": "", "plate_number": ""},
        {},
    ]
    result = filled_only_list(rows)

    assert len(result) == 1
    assert result[0]["vin"] == "EC3TEUMB0T0002608"
    assert filled_only_list([]) == []
    assert filled_only_list(None) == []


# ─────────────────────────────────────────────────────────────
# Вкладки: частичное заполнение не стирает ручной ввод
# ─────────────────────────────────────────────────────────────

def test_customer_tab_keeps_manual_values(qt_app):
    from ui.tabs.customer_tab import CustomerTab

    tab = CustomerTab()
    tab.full_name.setText("ООО «Ручной Заказчик»")
    tab.inn.setText("7712345678")
    tab.bank_account.setText("40702810900000012345")

    # Пустой блок от модели отбрасывается ещё до fill_data
    assert filled_only(EMPTY_ORG) == {}

    # А если передать пустые значения явно — вкладка тоже не должна очиститься
    tab.fill_data({})
    tab.fill_data({"full_name": "", "inn": "7700000000"})

    assert tab.full_name.text() == "ООО «Ручной Заказчик»"
    assert tab.inn.text() == "7700000000"           # это поле реально пришло
    assert tab.bank_account.text() == "40702810900000012345"


def test_customer_tab_full_replace_after_clear(qt_app):
    from ui.tabs.customer_tab import CustomerTab

    tab = CustomerTab()
    tab.full_name.setText("ООО «Старый»")
    tab.inn.setText("1111111111")

    tab.clear()
    tab.fill_data({"full_name": "ООО «Новый»"})

    assert tab.full_name.text() == "ООО «Новый»"
    assert tab.inn.text() == ""


def test_carrier_tab_keeps_manual_values(qt_app):
    from ui.tabs.carrier_tab import CarrierTab

    tab = CarrierTab()
    tab.full_name.setText("ООО «Ручной Перевозчик»")
    tab.inn.setText("7701234567")
    tab.kpp.setText("770101001")

    tab.fill_data({"full_name": "", "bank_name": "Новый банк"})

    assert tab.full_name.text() == "ООО «Ручной Перевозчик»"
    assert tab.inn.text() == "7701234567"
    assert tab.bank_name.text() == "Новый банк"


def test_contract_tab_keeps_manual_values(qt_app):
    """Пустой блок «contract» не должен сбрасывать НДС и срок оплаты."""
    from ui.tabs.contract_tab import ContractTab

    tab = ContractTab()
    tab.number.setText("23092026-77")
    tab.route.setText("Москва - Тверь")
    tab.vat_rate.setText("20")
    tab.payment_days.setText("5")
    tab.radio_without_vat.setChecked(True)

    # именно так выглядел старый баг: пустой dict → значения по умолчанию
    tab.fill_data({})

    assert tab.number.text() == "23092026-77"
    assert tab.route.text() == "Москва - Тверь"
    assert tab.vat_rate.text() == "20"
    assert tab.payment_days.text() == "5"


# ─────────────────────────────────────────────────────────────
# Сценарий целиком: распознавание через MainWindow
# ─────────────────────────────────────────────────────────────

def test_recognition_does_not_wipe_manual_customer(window):
    tab = window.customer_tab
    tab.full_name.setText("ООО «Ручной Заказчик»")
    tab.inn.setText("7712345678")
    tab.kpp.setText("771201001")
    tab.bank_account.setText("40702810900000012345")
    tab.director_name.setText("Петров Пётр Петрович")

    # Ответ модели: перевозчик распознан, заказчик — пустой блок
    window._on_recognition_finished({
        "driver": {},
        "customer": dict(EMPTY_ORG),
        "carrier": {"full_name": "ООО «Перевозчик»", "inn": "7701234567"},
        "vehicles": [{}],
        "tractor": {},
        "trailer": {},
        "contract": {},
    })

    assert tab.full_name.text() == "ООО «Ручной Заказчик»"
    assert tab.inn.text() == "7712345678"
    assert tab.kpp.text() == "771201001"
    assert tab.bank_account.text() == "40702810900000012345"
    assert tab.director_name.text() == "Петров Пётр Петрович"

    # зато распознанный перевозчик заполнился
    assert window.carrier_tab.full_name.text() == "ООО «Перевозчик»"
    assert window.carrier_tab.inn.text() == "7701234567"


def test_recognition_fills_customer_when_present(window):
    tab = window.customer_tab
    tab.full_name.setText("ООО «Старый Заказчик»")

    window._on_recognition_finished({
        "customer": {"full_name": "ООО «Новый Заказчик»", "inn": "7707654321"},
    })

    assert tab.full_name.text() == "ООО «Новый Заказчик»"
    assert tab.inn.text() == "7707654321"


def test_recognition_does_not_wipe_manual_driver(window):
    tab = window.driver_tab
    tab.full_name.setText("Иванов Иван Иванович")
    tab.passport_number.setText("926830")

    window._on_recognition_finished({
        "driver": {"full_name": "", "passport_number": ""},
        "customer": dict(EMPTY_ORG),
    })

    assert tab.full_name.text() == "Иванов Иван Иванович"
    assert tab.passport_number.text() == "926830"


# ─────────────────────────────────────────────────────────────
# Загрузка из справочника и сохранение в базу
# ─────────────────────────────────────────────────────────────

def test_load_customer_from_db_replaces_form(window):
    """«Загрузить в форму» даёт полное соответствие записи из справочника."""
    tab = window.customer_tab
    tab.full_name.setText("ООО «Старый»")
    tab.inn.setText("1111111111")
    tab.kpp.setText("111111111")

    window._load_customer_from_db({
        "id": 5,
        "full_name": "ООО «Из Справочника»",
        "inn": "7700000000",
        "kpp": "",
    })

    assert tab.full_name.text() == "ООО «Из Справочника»"
    assert tab.inn.text() == "7700000000"
    assert tab.kpp.text() == ""            # в записи КПП пуст — поле очищено


def test_manual_customer_is_saved_to_db(window, isolated_db):
    """Введённый вручную заказчик сохраняется в справочник."""
    window.customer_tab.full_name.setText("ООО «Ручной Заказчик»")
    window.customer_tab.inn.setText("7712345678")
    window.customer_tab.bank_account.setText("40702810900000012345")
    window.customer_tab.bik.setText("044525999")

    window.carrier_tab.full_name.setText("ООО «Перевозчик»")
    window.driver_tab.full_name.setText("Иванов Иван Иванович")
    window.contract_tab.number.setText("23092026-99")

    window._on_save_to_db()

    customers = isolated_db.get_all_organizations(is_carrier=False)
    assert [c["full_name"] for c in customers] == ["ООО «Ручной Заказчик»"]
    assert customers[0]["inn"] == "7712345678"
    assert customers[0]["bank_account"] == "40702810900000012345"
    assert customers[0]["bik"] == "044525999"


def test_saving_contract_keeps_route_points(window, isolated_db, monkeypatch):
    """
    Маршрут из формы должен быть доступен по ID сохранённого договора.

    В хранилище лежат три поля точки — address / date / time_window: колонки
    name у таблицы contract_points нет (ШАГ FIX-2.5 наименование салона в базу
    не добавлял). Приведённая же точка несёт ещё и name — пустой строкой,
    поэтому сравниваются только сохраняемые поля.
    """
    from core.contract_data import ContractData

    data = ContractData(
        contract={"number": "ROUTE-1"},
        vehicles=[{"vin": "XTEST000000000001", "brand_model": "Тестовое ТС"}],
        loadings=[{"address": "Склад А", "date": "2026-09-27", "time_window": "09:00-12:00"}],
        unloadings=[{"address": "Склад Б", "date": "2026-09-28", "time_window": "13:00-18:00"}],
    )
    monkeypatch.setattr(window, "_collect_data", lambda: data)

    window._on_save_to_db()

    conn = isolated_db.get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM contracts WHERE contract_number = ?", ("ROUTE-1",)
        ).fetchone()
        vehicles = conn.execute(
            "SELECT vin FROM vehicles WHERE contract_id = ?", (row[0],)
        ).fetchall() if row else []
    finally:
        conn.close()
    assert row is not None

    def _persisted(points):
        """Точка в том виде, в каком её хранит contract_points."""
        return [
            {key: point[key] for key in ("address", "date", "time_window")}
            for point in points
        ]

    points = isolated_db.load_contract_points(row[0])
    assert points["loadings"] == _persisted(data.loadings)
    assert points["unloadings"] == _persisted(data.unloadings)
    assert points["loadings"][0]["address"] == "Склад А"
    assert vehicles == [("XTEST000000000001",)]
