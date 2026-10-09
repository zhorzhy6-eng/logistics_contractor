#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Поля карточки заказчика и перевозчика в «Управлении базой данных»
(ШАГ «Полные стороны + склонение с учётом рода», часть E).

Что было
--------
В диалоге правки стороны не было полей «Тип», «Основание» и контактов в
одном месте с подписантом, а в базе не было колонок `entity_type` и `basis`.
Из-за этого после сохранения карточки вид стороны и основание полномочий
терялись: ИП при следующей загрузке печатался как ООО — с КПП, ОГРН и
«именуемое», а в п. 1.1 вместо свидетельства о регистрации стоял Устав.

Проверяется и форма (поля есть, значения подставляются), и запись: диалог
отдаёт поля, `save_organization` / `update_organization` их сохраняют,
`load_organization` возвращает.

Qt поднимается в offscreen-режиме, модальные окна подменяются. Данные
синтетические, ПДн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QLineEdit, QMessageBox  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def no_dialogs(monkeypatch):
    """Модальные окна не показываем: тест не должен ничего ждать."""
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))


def _dialog(org=None, is_carrier=True):
    from ui.db_manager_dialog import EditCarrierDialog

    return EditCarrierDialog(org or {}, is_carrier=is_carrier)


OOO = {
    "id": 1, "full_name": "ООО «Ромашка»", "short_name": "ООО «Ромашка»",
    "inn": "7701234567", "kpp": "770101001", "ogrn": "1027700132195",
    "legal_address": "г. Москва", "actual_address": "",
    "bank_account": "40702810000000000001", "bik": "044525225",
    "correspondent_account": "30101810400000000225", "bank_name": "ПАО Сбербанк",
    "director_name": "Петров Пётр Петрович",
    "director_position": "Генеральный директор",
    "phone": "+7 (495) 123-45-67", "email": "info@example.ru",
    "entity_type": "ООО (с НДС)", "basis": "Устава",
}

IP = {
    "id": 2,
    "full_name": "Индивидуальный предприниматель Смирнова Елена Владимировна",
    "short_name": "ИП Смирнова Е.В.", "inn": "770123456789", "kpp": "",
    "ogrn": "315770000000012", "legal_address": "г. Волгоград",
    "actual_address": "", "bank_account": "40702810123456789012",
    "bik": "044525225", "correspondent_account": "30101810400000000225",
    "bank_name": "ПАО Сбербанк", "director_name": "",
    "director_position": "", "phone": "", "email": "",
    "entity_type": "ИП без НДС",
    "basis": "свидетельства о государственной регистрации",
}


# ─────────────────────────────────────────────────────────────
# Поля формы: заказчик
# ─────────────────────────────────────────────────────────────

def test_edit_customer_form_has_type_field(qt_app, no_dialogs):
    """У заказчика есть поле «Тип» с тремя видами стороны."""
    dialog = _dialog(OOO, is_carrier=False)

    titles = [dialog.entity_type.itemText(i) for i in range(dialog.entity_type.count())]
    assert titles == ["ООО (с НДС)", "ИП с НДС", "ИП без НДС"]
    assert dialog.entity_type.currentText() == "ООО (с НДС)"


def test_edit_customer_form_has_director_name(qt_app, no_dialogs):
    """ФИО директора подставляется из записи."""
    dialog = _dialog(OOO, is_carrier=False)

    assert dialog.director_name.text() == "Петров Пётр Петрович"


def test_edit_customer_form_has_director_position(qt_app, no_dialogs):
    """Должность директора подставляется из записи."""
    dialog = _dialog(OOO, is_carrier=False)

    assert dialog.director_position.text() == "Генеральный директор"


def test_edit_customer_form_has_basis(qt_app, no_dialogs):
    """Основание полномочий подставляется из записи."""
    dialog = _dialog(OOO, is_carrier=False)

    assert dialog.basis.text() == "Устава"


def test_edit_customer_form_has_phone(qt_app, no_dialogs):
    """Телефон — на форме (в договоре печатается в п. 9)."""
    dialog = _dialog(OOO, is_carrier=False)

    assert dialog.phone.text() == "+7 (495) 123-45-67"


def test_edit_customer_form_has_email(qt_app, no_dialogs):
    """E-mail — на форме (необязательная строка бланка)."""
    dialog = _dialog(OOO, is_carrier=False)

    assert dialog.email.text() == "info@example.ru"


def test_edit_customer_form_has_bank_name(qt_app, no_dialogs):
    """Наименование банка — на форме: в бланке стоит «БИК …» и банк."""
    dialog = _dialog(OOO, is_carrier=False)

    assert dialog.bank_name.text() == "ПАО Сбербанк"


def test_form_has_inn_kpp_ogrn_fields(qt_app, no_dialogs):
    """ИНН, КПП и ОГРН/ОГРНИП — тоже на форме."""
    dialog = _dialog(OOO, is_carrier=False)

    assert dialog.inn.text() == "7701234567"
    assert dialog.kpp.text() == "770101001"
    assert dialog.ogrn.text() == "1027700132195"


# ─────────────────────────────────────────────────────────────
# Поля формы: перевозчик — те же
# ─────────────────────────────────────────────────────────────

def test_edit_carrier_form_has_same_fields(qt_app, no_dialogs):
    """У перевозчика набор полей тот же, что у заказчика."""
    carrier = _dialog(OOO, is_carrier=True)
    customer = _dialog(OOO, is_carrier=False)

    for field in ("entity_type", "full_name", "short_name", "inn", "kpp",
                  "ogrn", "bank_name", "bank_account", "bik",
                  "corr_account", "director_name",
                  "director_position", "basis", "phone", "email"):
        assert hasattr(carrier, field), f"у перевозчика нет поля {field}"
        assert hasattr(customer, field), f"у заказчика нет поля {field}"


def test_carrier_form_keeps_license_fields(qt_app, no_dialogs):
    """Поля лицензии у перевозчика остались, у заказчика их нет."""
    carrier = _dialog(OOO, is_carrier=True)
    customer = _dialog(OOO, is_carrier=False)

    assert isinstance(carrier.license_number, QLineEdit)
    assert isinstance(carrier.license_date, QLineEdit)
    assert customer.license_number is None


def test_form_saves_all_fields(qt_app, no_dialogs):
    """`get_data()` отдаёт ВСЕ поля формы, включая вид лица и основание."""
    dialog = _dialog(OOO, is_carrier=True)

    data = dialog.get_data()

    assert data["entity_type"] == "ООО (с НДС)"
    assert data["basis"] == "Устава"
    assert data["director_name"] == "Петров Пётр Петрович"
    assert data["director_position"] == "Генеральный директор"
    assert data["phone"] == "+7 (495) 123-45-67"
    assert data["email"] == "info@example.ru"
    assert data["bank_name"] == "ПАО Сбербанк"
    assert data["inn"] == "7701234567"
    assert data["kpp"] == "770101001"
    assert data["ogrn"] == "1027700132195"


def test_ip_form_clears_kpp(qt_app, no_dialogs):
    """У ИП КПП уходит пустым: в бланке он не печатается."""
    record = dict(OOO, entity_type="ИП с НДС", kpp="770101001")
    dialog = _dialog(record, is_carrier=True)

    assert dialog.get_data()["kpp"] == ""


def test_entity_type_switch_updates_basis(qt_app, no_dialogs):
    """Смена вида стороны меняет основание по умолчанию и гасит поле КПП."""
    dialog = _dialog(OOO, is_carrier=True)
    assert dialog.basis.text() == "Устава"
    assert dialog.kpp.isEnabled() is True

    dialog.entity_type.setCurrentText("ИП с НДС")

    assert dialog.basis.text() == "свидетельства о государственной регистрации"
    assert dialog.kpp.isEnabled() is False


def test_entity_type_switch_keeps_custom_basis(qt_app, no_dialogs):
    """Своё основание оператора смена вида не затирает."""
    dialog = _dialog(OOO, is_carrier=True)
    dialog.basis.setText("доверенности № 5")

    dialog.entity_type.setCurrentText("ИП без НДС")

    assert dialog.basis.text() == "доверенности № 5"


def test_new_form_defaults(qt_app, no_dialogs):
    """Новая карточка: вид «ООО (с НДС)», основание «Устава»."""
    dialog = _dialog({}, is_carrier=True)

    assert dialog.entity_type.currentText() == "ООО (с НДС)"
    assert dialog.basis.text() == "Устава"


def test_old_record_without_type_guessed_as_ip(qt_app, no_dialogs):
    """
    Запись без вида (до миграции) определяется по наименованию.

    Колонки `entity_type` в базе не было, поэтому у заказчиков и
    перевозчиков, заведённых раньше, значение пустое: вид выводится из
    приставки в наименовании, иначе ИП печатался бы как ООО.
    """
    dialog = _dialog(dict(IP, entity_type=""), is_carrier=True)

    assert dialog.entity_type.currentText() == "ИП с НДС"
    assert dialog.basis.text() == "свидетельства о государственной регистрации"


# ─────────────────────────────────────────────────────────────
# Запись в базу
# ─────────────────────────────────────────────────────────────

def test_db_saves_and_loads_entity_type(isolated_db):
    """`save_organization` пишет вид лица и основание, `load_organization` читает."""
    org_id = isolated_db.save_organization(
        dict(OOO, entity_type="ИП без НДС", basis="свидетельства о регистрации"),
        is_carrier=True,
    )

    record = isolated_db.load_organization(org_id, is_carrier=True)

    assert record["entity_type"] == "ИП без НДС"
    assert record["basis"] == "свидетельства о регистрации"


def test_db_updates_entity_type_and_basis(isolated_db):
    """`update_organization` обновляет и новые поля тоже."""
    org_id = isolated_db.save_organization(OOO, is_carrier=True)

    assert isolated_db.update_organization(
        org_id,
        dict(OOO, entity_type="ИП с НДС", basis="свидетельства о регистрации"),
        is_carrier=True,
    )

    record = isolated_db.load_organization(org_id, is_carrier=True)
    assert record["entity_type"] == "ИП с НДС"
    assert record["basis"] == "свидетельства о регистрации"


def test_db_has_columns_in_both_tables(isolated_db):
    """Колонки есть и в carriers, и в customers."""
    conn = isolated_db.get_connection()
    try:
        for table in ("carriers", "customers"):
            columns = [
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            ]
            assert "entity_type" in columns, table
            assert "basis" in columns, table
    finally:
        conn.close()


def test_db_migration_adds_columns_to_old_table(isolated_db, monkeypatch):
    """
    Миграция: у базы без колонок они появляются, данные не теряются.

    Так выглядит рабочая база: приложение обновляют, а таблицы в ней уже
    созданы прежней версией.
    """
    import sqlite3

    conn = isolated_db.get_connection()
    try:
        conn.execute("ALTER TABLE carriers RENAME TO carriers_old")
        conn.execute("""
            CREATE TABLE carriers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                short_name TEXT, inn TEXT, kpp TEXT, ogrn TEXT,
                legal_address TEXT, actual_address TEXT, bank_account TEXT,
                bik TEXT, correspondent_account TEXT, bank_name TEXT,
                director_name TEXT, director_position TEXT, phone TEXT,
                email TEXT, license_number TEXT, license_date TEXT,
                is_deleted INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            INSERT INTO carriers (full_name, inn, kpp)
            VALUES ('ООО «Старое»', '7701234567', '770101001')
        """)
        conn.execute("DROP TABLE carriers_old")
        conn.commit()
    finally:
        conn.close()

    isolated_db.init_database()

    conn = isolated_db.get_connection()
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(carriers)")]
        row = conn.execute(
            "SELECT full_name, entity_type, basis FROM carriers"
        ).fetchone()
    finally:
        conn.close()

    assert "entity_type" in columns
    assert "basis" in columns
    assert row[0] == "ООО «Старое»", "данные записи потерялись"
    assert row[1] is None and row[2] is None
