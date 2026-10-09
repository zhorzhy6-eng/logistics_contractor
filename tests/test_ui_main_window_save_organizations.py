#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
«Сохранить в базу»: стороны попадают в справочник (ШАГ «Фикс сохранения +
динамические стороны в шаблонах», часть A).

Что проверяем
-------------
Кнопка «Сохранить в базу» печатала «Успех. Данные сохранены в базу!», а
перевозчик в справочник НЕ попадал. Причина нашлась в логе рабочей сессии:
`find_organization_id` находил запись с тем же ИНН, `_on_save_to_db` писал
«Перевозчик найден в справочнике» и НИЧЕГО не делал — ни обновления, ни
создания. А найденная запись была мягко удалённой, поэтому в дереве базы
её не видно: «успех» есть, перевозчика нет.

Правило теперь одно для обеих сторон:

  * организация ищется по реквизитам формы (ИНН, затем наименование);
  * НАЙДЕНА — ОБНОВЛЯЕТСЯ данными формы (телефон, КПП и банк могли
    измениться), мягко удалённая возвращается в справочник;
  * НЕ найдена — перевозчик создаётся, заказчик НЕ создаётся (автосоздание
    заказчиков плодило дубли — ШАГ «Заказчик без дублей»);
  * заказчик и перевозчик не путаются местами: это РАЗНЫЕ таблицы.

Qt поднимается в offscreen-режиме, база временная (`isolated_db`), данные
синтетические, ПДн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

#: Синтетические организации (выдуманные наименования, ИНН и ОГРНИП).
#: Данные из рабочего сценария оператора в тесты не переносятся: файлы тестов
#: коммитятся, а ПДн в git быть не должно (AGENTS.md § 4).
CARRIER_INN = "770123456789"
CARRIER_NAME = "Индивидуальный предприниматель Смирнов Алексей Николаевич"
CARRIER_SHORT = ""
CARRIER_KPP = "770701001"
CARRIER_OGRN = "315770000000012"
CARRIER_PHONE_NEW = "+7 (8442) 55-66-77"

CUSTOMER_INN = "7707654321"
CUSTOMER_NAME = "ООО «Заказчик»"
CUSTOMER_PHONE = "+7 (495) 000-11-22"


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def quiet_dialogs(monkeypatch):
    """Информационные окна не показываем: offscreen их не переживает."""
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Yes)


@pytest.fixture
def window(qt_app, isolated_db, quiet_dialogs, monkeypatch):
    """MainWindow без GigaChat и без блокирующих диалогов."""
    from PyQt5.QtCore import QEvent
    from PyQt5.QtWidgets import QApplication

    from ui.main_window import MainWindow
    from ui.windows.base_window import clear_source_windows

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    win = MainWindow()
    yield win

    # Окно — top-level виджет: пока C++ объект жив, его находит запасной путь
    # поиска окна-источника (QApplication.topLevelWidgets) и ломает чужие
    # тесты зеркала. Закрываем по-настоящему, как tests/test_window_close_hides.py.
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


def _fill_carrier(window, *, name=CARRIER_NAME, inn=CARRIER_INN, kpp=CARRIER_KPP,
                  ogrn=CARRIER_OGRN, phone=CARRIER_PHONE_NEW):
    """Заполняет вкладку «Перевозчик» так, как это делает оператор."""
    window.carrier_tab.clear()
    window.carrier_tab.full_name.setText(name)
    window.carrier_tab.short_name.setText(CARRIER_SHORT)
    window.carrier_tab.inn.setText(inn)
    window.carrier_tab.kpp.setText(kpp)
    window.carrier_tab.ogrn.setText(ogrn)
    window.carrier_tab.phone.setText(phone)


def _fill_customer(window, *, name=CUSTOMER_NAME, inn=CUSTOMER_INN,
                   phone=CUSTOMER_PHONE):
    """Заполняет вкладку «Заказчик»."""
    window.customer_tab.clear()
    window.customer_tab.full_name.setText(name)
    window.customer_tab.inn.setText(inn)
    window.customer_tab.phone.setText(phone)


def _contracts(isolated_db):
    """Строки договоров: номер и ссылки на справочники."""
    conn = isolated_db.get_connection()
    try:
        sql = ("SELECT id, contract_number, driver_id, customer_id, carrier_id "
               "FROM contracts ORDER BY id")
        rows = conn.execute(sql).fetchall()
        cols = [desc[0] for desc in conn.execute(sql).description]
    finally:
        conn.close()
    return [dict(zip(cols, row)) for row in rows]


def _carriers(isolated_db, include_deleted=False):
    return isolated_db.get_all_organizations(
        is_carrier=True, include_deleted=include_deleted
    )


def _customers(isolated_db, include_deleted=False):
    return isolated_db.get_all_organizations(
        is_carrier=False, include_deleted=include_deleted
    )


# ─────────────────────────────────────────────────────────────
# Новый перевозчик
# ─────────────────────────────────────────────────────────────

def test_new_carrier_saves_to_db(window, isolated_db):
    """Нового перевозчика «Сохранить в базу» заводит в справочник."""
    _fill_carrier(window)
    window.contract_tab.number.setText("SAVE-NEW-1")

    window._on_save_to_db()

    carriers = _carriers(isolated_db)
    assert len(carriers) == 1, "перевозчик не сохранён"
    assert carriers[0]["inn"] == CARRIER_INN
    assert carriers[0]["full_name"] == CARRIER_NAME
    assert carriers[0]["kpp"] == CARRIER_KPP
    assert carriers[0]["ogrn"] == CARRIER_OGRN

    # Договор ссылается на ту же запись.
    assert _contracts(isolated_db)[0]["carrier_id"] == carriers[0]["id"]


def test_save_carrier_with_empty_name_skips(window, isolated_db):
    """Пустое наименование — сохранять нечего, но и падать не на чем."""
    window.carrier_tab.clear()
    window.contract_tab.number.setText("SAVE-EMPTY-1")

    window._on_save_to_db()

    assert _carriers(isolated_db) == []
    contracts = _contracts(isolated_db)
    assert len(contracts) == 1, "договор сохраняется и без перевозчика"
    assert contracts[0]["carrier_id"] is None


def test_save_two_different_carriers_creates_two_records(window, isolated_db):
    """Два разных ИНН — две записи, ни одна не затирает другую."""
    _fill_carrier(window)
    window.contract_tab.number.setText("SAVE-TWO-1")
    window._on_save_to_db()

    _fill_carrier(window, name="ООО «Вторая»", inn="7709876543",
                  kpp="770901001", ogrn="1027700132196", phone="")
    window.contract_tab.number.setText("SAVE-TWO-2")
    window._on_save_to_db()

    carriers = _carriers(isolated_db)
    assert sorted(org["inn"] for org in carriers) == [CARRIER_INN, "7709876543"]


# ─────────────────────────────────────────────────────────────
# Существующий перевозчик: обновление, а не пропуск
# ─────────────────────────────────────────────────────────────

def test_existing_carrier_updates_not_duplicates(window, isolated_db):
    """Найденного по ИНН перевозчика форма ОБНОВЛЯЕТ (а не пропускает)."""
    carrier_id = isolated_db.save_organization(
        {"full_name": CARRIER_NAME, "inn": CARRIER_INN, "kpp": "",
         "phone": "+7 (000) 000-00-00"},
        is_carrier=True,
    )

    _fill_carrier(window)
    window.contract_tab.number.setText("SAVE-UPD-1")
    window._on_save_to_db()

    carriers = _carriers(isolated_db)
    assert len(carriers) == 1, "появился дубль"
    assert carriers[0]["id"] == carrier_id
    assert carriers[0]["phone"] == CARRIER_PHONE_NEW, "правки формы не сохранены"
    assert carriers[0]["kpp"] == CARRIER_KPP, "КПП из формы не сохранён"
    assert _contracts(isolated_db)[0]["carrier_id"] == carrier_id


def test_save_carrier_with_soft_deleted_same_inn(window, isolated_db):
    """
    Мягко удалённая запись с тем же ИНН обновляется и возвращается.

    Это и есть баг оператора: запись лежала удалённой, `find_organization_id`
    её находил (ссылки договоров её переживают), обновления не было — договор
    сохранялся, а в справочнике перевозчика не появлялось.
    """
    carrier_id = isolated_db.save_organization(
        {"full_name": CARRIER_NAME, "inn": CARRIER_INN}, is_carrier=True
    )
    assert isolated_db.delete_organization(carrier_id, is_carrier=True)
    assert _carriers(isolated_db) == [], "перед проверкой запись скрыта"

    _fill_carrier(window)
    window.contract_tab.number.setText("SAVE-DEL-1")
    window._on_save_to_db()

    visible = _carriers(isolated_db)
    assert len(visible) == 1, "перевозчик не вернулся в справочник"
    assert visible[0]["id"] == carrier_id, "создана вторая запись вместо возврата"
    assert visible[0]["kpp"] == CARRIER_KPP
    assert _contracts(isolated_db)[0]["carrier_id"] == carrier_id


# ─────────────────────────────────────────────────────────────
# Заказчик: обновление без автосоздания; стороны не путаются
# ─────────────────────────────────────────────────────────────

def test_existing_customer_updates_not_duplicates(window, isolated_db):
    """Найденного заказчика форма тоже обновляет — правило одно для сторон."""
    customer_id = isolated_db.save_organization(
        {"full_name": CUSTOMER_NAME, "inn": CUSTOMER_INN, "phone": ""},
        is_carrier=False,
    )

    _fill_customer(window)
    window.contract_tab.number.setText("SAVE-CUST-1")
    window._on_save_to_db()

    customers = _customers(isolated_db)
    assert len(customers) == 1, "появился дубль заказчика"
    assert customers[0]["id"] == customer_id
    assert customers[0]["phone"] == CUSTOMER_PHONE
    assert _contracts(isolated_db)[0]["customer_id"] == customer_id


def test_new_customer_is_not_created(window, isolated_db):
    """Заказчик не автосоздаётся: договор сохраняется без ссылки (как решено)."""
    _fill_customer(window)
    window.contract_tab.number.setText("SAVE-CUST-2")

    window._on_save_to_db()

    assert _customers(isolated_db) == []
    assert _contracts(isolated_db)[0]["customer_id"] is None


def test_save_to_db_keeps_customer_and_carrier_separate(window, isolated_db):
    """
    Заказчик и перевозчик не путаются местами.

    Вкладки разные, таблицы справочника разные: заказчик уходит в customers,
    перевозчик — в carriers, ссылки договора указывают в свои таблицы.
    """
    _fill_customer(window)
    _fill_carrier(window)
    window.contract_tab.number.setText("SAVE-SIDES-1")

    window._on_save_to_db()

    # Заказчика в форме не было в справочнике — записи нет (автосоздание
    # заказчиков запрещено), поэтому заводим его руками и повторяем.
    assert _customers(isolated_db) == []
    isolated_db.save_organization(
        {"full_name": CUSTOMER_NAME, "inn": CUSTOMER_INN}, is_carrier=False
    )

    window.contract_tab.number.setText("SAVE-SIDES-2")
    window._on_save_to_db()

    carriers = _carriers(isolated_db)
    customers = _customers(isolated_db)
    assert len(carriers) == 1 and len(customers) == 1

    # Перевозчик не попал в заказчиков и наоборот.
    assert carriers[0]["inn"] == CARRIER_INN
    assert customers[0]["inn"] == CUSTOMER_INN
    assert carriers[0]["full_name"] == CARRIER_NAME
    assert customers[0]["full_name"] == CUSTOMER_NAME

    last = _contracts(isolated_db)[-1]
    assert last["carrier_id"] == carriers[0]["id"]
    assert last["customer_id"] == customers[0]["id"]
