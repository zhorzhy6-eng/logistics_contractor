#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты чтения полей кнопками DaData (ui/tabs/base_tab.py).

Регрессия: канал bank читал поле «bic» вместо «bik» (реальное поле формы),
поэтому кнопка 🔎 у БИК всегда видела пустое значение (в логе длина=0)
и показывала «Поле БИК пустое».

Проверяется:
  * MRO вкладок: обработчик канала один (в _DadataChannelMixin);
  * канал bank читает БИК из self.bik, party — ИНН, fms — код подразделения
    (хотя кнопка ФМС стоит у поля «Кем выдан»);
  * каналы не путаются: кнопка БИК не дёргает find_party_by_inn и наоборот;
  * в лог уходит длина значения, а не само значение;
  * имена полей из DADATA_CHANNELS существуют у вкладок.

Сеть не используется: DadataClient подменяется фейком через monkeypatch.
"""

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QInputDialog, QMessageBox,
)

from ui.tabs import base_tab  # noqa: E402
from ui.tabs.carrier_tab import CarrierTab  # noqa: E402
from ui.tabs.customer_tab import CustomerTab  # noqa: E402
from ui.tabs.driver_tab import DriverTab  # noqa: E402

BIC = "044525104"
INN = "7719402047"
FMS_CODE = "500-123"

BANK_RESULT = {
    "bic": BIC,
    "bank_name": "ТЕСТОВЫЙ БАНК",
    "correspondent_account": "30101810400000000225",
    "swift": "TESTRUMM",
    "payment_city": "Москва",
    "state": "ACTIVE",
}
PARTY_RESULT = {"full_name": "ООО «Тест»", "inn": INN, "status": "ACTIVE"}
FMS_RESULT = [{"value": "Отделением УФМС России по г. Москве № 1"}]


class FakeClient:
    """Подмена DadataClient: пишет вызовы и не ходит в сеть."""

    calls = []

    def find_party_by_inn(self, inn):
        FakeClient.calls.append(("party", inn))
        return dict(PARTY_RESULT)

    def find_bank_by_bic(self, bic):
        FakeClient.calls.append(("bank", bic))
        return dict(BANK_RESULT)

    def suggest_fms_unit(self, code):
        FakeClient.calls.append(("fms", code))
        return list(FMS_RESULT)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_dialogs(monkeypatch):
    """Диалоги не открываются: тесты не должны ждать пользователя."""
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    monkeypatch.setattr(QInputDialog, "getItem",
                        staticmethod(lambda *a, **k: ("", False)))


@pytest.fixture
def fake_client(monkeypatch):
    """Подменяет клиента DaData и возвращает журнал вызовов."""
    FakeClient.calls = []
    monkeypatch.setattr(base_tab, "DadataClient", FakeClient)
    return FakeClient.calls


def click(tab, method, qt_app):
    """Нажимает кнопку и дожидается завершения фоновой задачи."""
    method()
    pool = getattr(tab, "_dadata_pool", None)
    if pool is not None:
        pool.waitForDone(5000)
    qt_app.processEvents()


# ─────────────────────────────────────────────────────────────
# Структура каналов
# ─────────────────────────────────────────────────────────────

def test_carrier_mro_has_single_handler(qt_app):
    """Обработчик клика один — в _DadataChannelMixin, а не в миксинах-каналах."""
    names = [cls.__name__ for cls in CarrierTab.__mro__]
    assert names[:4] == [
        "CarrierTab", "DadataFillMixin", "DadataBankMixin", "_DadataChannelMixin",
    ]
    # Ни один наследник не переопределяет обработчик
    assert "_on_dadata_button_clicked" not in vars(base_tab.DadataFillMixin)
    assert "_on_dadata_button_clicked" not in vars(base_tab.DadataBankMixin)
    assert "_on_dadata_button_clicked" in vars(base_tab._DadataChannelMixin)


@pytest.mark.parametrize("channel,field", [
    ("party", "inn"),
    ("bank", "bik"),
    ("fms", "passport_code"),
])
def test_query_field_names_exist_on_tabs(channel, field):
    """Имя поля в DADATA_CHANNELS совпадает с реальным полем формы."""
    assert base_tab.DADATA_CHANNELS[channel]["query_field"] == field

    tabs = [CarrierTab(), CustomerTab(), DriverTab()]
    for tab in tabs:
        spec = base_tab.DADATA_CHANNELS[channel]
        # Проверяем только те вкладки, где канал реально используется
        if getattr(tab, spec["button_attr"], None) is None:
            continue
        assert getattr(tab, field, None) is not None, (
            f"{type(tab).__name__}: нет поля {field!r} для канала {channel}"
        )


def test_query_widget_points_to_source_field(qt_app):
    """Источник значения: БИК — у кнопки БИК, ФМС — код, а не поле у кнопки."""
    carrier = CarrierTab()
    driver = DriverTab()

    assert carrier._dadata_query_widget("bank") is carrier.bik
    assert carrier._dadata_query_widget("party") is carrier.inn
    assert driver._dadata_query_widget("fms") is driver.passport_code
    # Кнопка ФМС стоит у поля-приёмника, а не у источника
    assert driver._dadata_query_widgets["fms"] is driver.passport_issuer


# ─────────────────────────────────────────────────────────────
# Чтение значений по каналам
# ─────────────────────────────────────────────────────────────

def test_bank_button_sends_bic(qt_app, fake_client):
    """Кнопка 🔎 у БИК отправляет в DaData именно БИК."""
    carrier = CarrierTab()
    carrier.bik.setText(BIC)

    click(carrier, carrier._on_fill_by_bic_clicked, qt_app)

    assert fake_client == [("bank", BIC)]


def test_bank_button_fills_bank_fields(qt_app, fake_client):
    carrier = CarrierTab()
    carrier.bik.setText(BIC)

    click(carrier, carrier._on_fill_by_bic_clicked, qt_app)

    assert carrier.bank_name.text() == BANK_RESULT["bank_name"]
    assert carrier.correspondent_account.text() == BANK_RESULT["correspondent_account"]
    assert carrier.bank_account.text() == ""      # расчётный счёт не выдумываем


def test_bank_click_logs_length_not_value(qt_app, fake_client, caplog):
    """В логе — длина БИК, а не сам БИК."""
    carrier = CarrierTab()
    carrier.bik.setText(BIC)

    with caplog.at_level(logging.INFO, logger="ui.tabs.base_tab"):
        click(carrier, carrier._on_fill_by_bic_clicked, qt_app)

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "нажата кнопка «Заполнить банк по БИК»" in text
    assert f"старт поиска банка по БИК (длина={len(BIC)})" in text
    assert BIC not in text


def test_party_button_sends_inn(qt_app, fake_client):
    """Кнопка 🔎 у ИНН продолжает работать и отправляет ИНН."""
    carrier = CarrierTab()
    carrier.inn.setText(INN)

    click(carrier, carrier._on_fill_by_inn_clicked, qt_app)

    assert fake_client == [("party", INN)]
    assert carrier.full_name.text() == PARTY_RESULT["full_name"]


def test_customer_bank_button_sends_bic(qt_app, fake_client):
    customer = CustomerTab()
    customer.bik.setText(BIC)

    click(customer, customer._on_fill_by_bic_clicked, qt_app)

    assert fake_client == [("bank", BIC)]
    assert customer.bank_name.text() == BANK_RESULT["bank_name"]


def test_fms_button_sends_passport_code(qt_app, fake_client):
    """Кнопка 🔎 у «Кем выдан» берёт значение из кода подразделения."""
    driver = DriverTab()
    driver.passport_code.setText(FMS_CODE)

    click(driver, driver._on_fill_by_fms_clicked, qt_app)

    assert fake_client == [("fms", FMS_CODE)]
    assert driver.passport_issuer.text() == FMS_RESULT[0]["value"]


# ─────────────────────────────────────────────────────────────
# Каналы не путаются
# ─────────────────────────────────────────────────────────────

def test_bank_click_does_not_call_party(qt_app, fake_client):
    carrier = CarrierTab()
    carrier.bik.setText(BIC)
    carrier.inn.setText(INN)

    click(carrier, carrier._on_fill_by_bic_clicked, qt_app)

    assert [kind for kind, _ in fake_client] == ["bank"]


def test_party_click_does_not_call_bank(qt_app, fake_client):
    carrier = CarrierTab()
    carrier.bik.setText(BIC)
    carrier.inn.setText(INN)

    click(carrier, carrier._on_fill_by_inn_clicked, qt_app)

    assert [kind for kind, _ in fake_client] == ["party"]


def test_empty_bic_does_not_reach_api(qt_app, fake_client, monkeypatch):
    """Пустой БИК — предупреждение, запрос не уходит (и не читается ИНН)."""
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda parent, title, text, *a, **k: warnings.append(text)),
    )

    carrier = CarrierTab()
    carrier.inn.setText(INN)          # ИНН заполнен, БИК пуст

    click(carrier, carrier._on_fill_by_bic_clicked, qt_app)

    assert fake_client == []
    assert len(warnings) == 1
    assert "БИК" in warnings[0]


def test_task_state_is_reset_after_lookup(qt_app, fake_client):
    """После запроса канал снова свободен, кнопка активна."""
    carrier = CarrierTab()
    carrier.bik.setText(BIC)

    click(carrier, carrier._on_fill_by_bic_clicked, qt_app)

    assert carrier._dadata_tasks.get("bank") is None
    assert carrier.btn_fill_by_bic.isEnabled() is True
