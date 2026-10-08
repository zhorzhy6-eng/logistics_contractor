#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Договор и карточка водителя (ШАГ «Привязка водителей к перевозчикам»,
часть E).

Правила шага:

  * у водителя есть основной перевозчик, а в форме перевозчик не заполнен —
    подставляем его из справочника (договор печатает того, на кого водитель
    работает);
  * перевозчик в форме заполнен, но это ДРУГОЙ перевозчик — спрашиваем
    оператора; отказ прерывает операцию;
  * у водителя привязки нет — ничего не делаем и ни о чём не спрашиваем.

Обе точки входа проверяются живьём: «Сохранить в базу» (там же смотрим
ссылку contracts.carrier_id) и «Создать договор» (генератор подменён —
файл не создаётся, проверяются данные, которые в него ушли).

Qt поднимается в offscreen-режиме, база — временная (`isolated_db`),
данные синтетические, ПДн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

#: Синтетический водитель (выдуманные ФИО и паспорт).
DRIVER_NAME = "Иванов Иван Иванович"


class WarningRecorder:
    """
    Подмена QMessageBox.warning: помнит вызовы и отдаёт нужный ответ.

    Тест проверяет ФАКТ предупреждения и текст, а не кнопки Qt: ответ
    задаётся заранее (`answer`).
    """

    def __init__(self, answer=QMessageBox.Yes):
        self.answer = answer
        self.calls = []

    def __call__(self, parent, title, text, *args, **kwargs):
        self.calls.append({"parent": parent, "title": title, "text": text})
        return self.answer

    @property
    def texts(self):
        return [call["text"] for call in self.calls]


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def warning_recorder(monkeypatch):
    """Перехватывает QMessageBox.warning (по умолчанию отвечает «Да»)."""
    recorder = WarningRecorder()
    monkeypatch.setattr(QMessageBox, "warning", recorder)
    return recorder


@pytest.fixture
def quiet_dialogs(monkeypatch):
    """Информационные окна не показываем: offscreen их не переживает."""
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)


@pytest.fixture
def window(qt_app, isolated_db, warning_recorder, quiet_dialogs, monkeypatch):
    """MainWindow без GigaChat и без блокирующих диалогов."""
    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    win = MainWindow()
    yield win
    win.close()


@pytest.fixture
def carrier_a(isolated_db):
    """Перевозчик, за которым закреплён водитель."""
    return isolated_db.save_organization(
        {"full_name": "ООО «Альфа»", "inn": "7701234567"}, is_carrier=True
    )


@pytest.fixture
def carrier_b(isolated_db):
    """Другой перевозчик — его выбирают в договоре."""
    return isolated_db.save_organization(
        {"full_name": "ООО «Бета»", "inn": "7709876543"}, is_carrier=True
    )


def _bind_driver(window, carrier_id=None):
    """Заполняет карточку водителя и, если нужно, выбирает перевозчика."""
    window.driver_tab.fill_carriers()
    window.driver_tab.full_name.setText(DRIVER_NAME)
    window.driver_tab._select_carrier(carrier_id)


def _select_carrier_in_form(window, isolated_db, carrier_id):
    """Кладёт перевозчика в форму так, как это делает загрузка из базы."""
    record = isolated_db.load_organization(carrier_id, is_carrier=True)
    window._load_carrier_from_db(record)


def _contracts(isolated_db):
    """Строки договоров из базы (id, номер, ссылки на справочники)."""
    conn = isolated_db.get_connection()
    try:
        rows = conn.execute(
            "SELECT id, contract_number, driver_id, customer_id, carrier_id "
            "FROM contracts ORDER BY id"
        ).fetchall()
        cols = [desc[0] for desc in conn.execute(
            "SELECT id, contract_number, driver_id, customer_id, carrier_id "
            "FROM contracts LIMIT 0"
        ).description]
    finally:
        conn.close()
    return [dict(zip(cols, row)) for row in rows]


# ─────────────────────────────────────────────────────────────
# E.1: перевозчик в форме пуст — подставляем из водителя
# ─────────────────────────────────────────────────────────────

def test_save_uses_driver_carrier_if_carrier_empty(
    window, isolated_db, carrier_a, warning_recorder
):
    """Перевозчик в форме пуст — договор ссылается на перевозчика водителя."""
    _bind_driver(window, carrier_a)
    window.contract_tab.number.setText("CARRIER-1")

    assert window.carrier_tab.full_name.text() == "", "форма перевозчика пуста"

    window._on_save_to_db()

    # Подстановка в форму: оператор видит, кто попал в договор.
    assert window.carrier_tab.full_name.text() == "ООО «Альфа»"

    contracts = _contracts(isolated_db)
    assert len(contracts) == 1
    assert contracts[0]["contract_number"] == "CARRIER-1"
    assert contracts[0]["carrier_id"] == carrier_a
    assert contracts[0]["driver_id"], "водитель тоже сохранён и связан"

    # Совпадение — не повод спрашивать.
    assert warning_recorder.calls == []


def test_substituted_carrier_is_not_duplicated(
    window, isolated_db, carrier_a, warning_recorder
):
    """Подставленный перевозчик не заводится второй записью в справочнике."""
    _bind_driver(window, carrier_a)
    window.contract_tab.number.setText("CARRIER-2")

    window._on_save_to_db()

    carriers = isolated_db.get_all_organizations(is_carrier=True)
    assert [org["full_name"] for org in carriers] == ["ООО «Альфа»"]
    assert carriers[0]["id"] == carrier_a


def test_create_contract_gets_driver_carrier(
    window, isolated_db, carrier_a, warning_recorder, monkeypatch
):
    """«Создать договор» тоже получает перевозчика из карточки водителя."""
    class FakeGenerator:
        def __init__(self):
            self.data = None

        def generate(self, data):
            self.data = data
            return os.path.join("output", "fake.docx")

    generator = FakeGenerator()
    monkeypatch.setattr(window, "contract_generator", generator)
    monkeypatch.setattr(window, "_confirm_validation", lambda report: True)
    monkeypatch.setattr(window, "_show_contract_created", lambda path: None)

    _bind_driver(window, carrier_a)
    window.contract_tab.number.setText("CARRIER-3")

    window._on_create_contract()

    assert generator.data is not None, "генератор не вызван"
    assert generator.data.carrier["full_name"] == "ООО «Альфа»"
    assert generator.data.carrier["inn"] == "7701234567"
    assert warning_recorder.calls == []


# ─────────────────────────────────────────────────────────────
# E.1: перевозчики не совпадают — предупреждение
# ─────────────────────────────────────────────────────────────

def test_warning_when_carrier_mismatch(
    window, isolated_db, carrier_a, carrier_b, warning_recorder
):
    """У водителя Альфа, в договоре Бета — предупреждение и продолжение."""
    _bind_driver(window, carrier_a)
    _select_carrier_in_form(window, isolated_db, carrier_b)
    window.contract_tab.number.setText("CARRIER-4")

    window._on_save_to_db()

    assert len(warning_recorder.calls) == 1, "предупреждения не было"
    text = warning_recorder.texts[0]
    assert DRIVER_NAME in text
    assert "ООО «Альфа»" in text and "ООО «Бета»" in text

    # «Продолжить» — сохраняем как есть, с перевозчиком из формы.
    contracts = _contracts(isolated_db)
    assert len(contracts) == 1
    assert contracts[0]["carrier_id"] == carrier_b


def test_create_contract_warns_about_mismatch(
    window, isolated_db, carrier_a, carrier_b, warning_recorder, monkeypatch
):
    """Предупреждение стоит и на пути «Создать договор»."""
    class FakeGenerator:
        def __init__(self):
            self.data = None

        def generate(self, data):
            self.data = data
            return os.path.join("output", "fake.docx")

    generator = FakeGenerator()
    monkeypatch.setattr(window, "contract_generator", generator)
    monkeypatch.setattr(window, "_confirm_validation", lambda report: True)
    monkeypatch.setattr(window, "_show_contract_created", lambda path: None)

    _bind_driver(window, carrier_a)
    _select_carrier_in_form(window, isolated_db, carrier_b)

    window._on_create_contract()

    assert len(warning_recorder.calls) == 1
    assert generator.data.carrier["full_name"] == "ООО «Бета»"


def test_cancel_on_mismatch_stops_save(
    window, isolated_db, carrier_a, carrier_b, warning_recorder
):
    """Ответ «Нет» прерывает сохранение: в базу ничего не уходит."""
    warning_recorder.answer = QMessageBox.No

    _bind_driver(window, carrier_a)
    _select_carrier_in_form(window, isolated_db, carrier_b)
    window.contract_tab.number.setText("CARRIER-5")

    window._on_save_to_db()

    assert len(warning_recorder.calls) == 1
    assert _contracts(isolated_db) == [], "договор сохранён, хотя оператор отказался"
    assert isolated_db.get_all_drivers() == []


def test_cancel_on_mismatch_stops_creation(
    window, isolated_db, carrier_a, carrier_b, warning_recorder, monkeypatch
):
    """Отказ прерывает и создание договора: генератор не вызывается."""
    warning_recorder.answer = QMessageBox.No

    class FakeGenerator:
        def __init__(self):
            self.called = False

        def generate(self, data):
            self.called = True
            return os.path.join("output", "fake.docx")

    generator = FakeGenerator()
    monkeypatch.setattr(window, "contract_generator", generator)
    monkeypatch.setattr(window, "_confirm_validation", lambda report: True)
    monkeypatch.setattr(window, "_show_contract_created", lambda path: None)

    _bind_driver(window, carrier_a)
    _select_carrier_in_form(window, isolated_db, carrier_b)

    window._on_create_contract()

    assert generator.called is False, "договор создан, хотя оператор отказался"


# ─────────────────────────────────────────────────────────────
# E.1: спрашивать не о чем
# ─────────────────────────────────────────────────────────────

def test_no_warning_when_driver_has_no_carrier(
    window, isolated_db, carrier_b, warning_recorder
):
    """У водителя привязки нет — договор идёт со своим перевозчиком молча."""
    _bind_driver(window)
    _select_carrier_in_form(window, isolated_db, carrier_b)
    window.contract_tab.number.setText("CARRIER-6")

    window._on_save_to_db()

    assert warning_recorder.calls == []

    contracts = _contracts(isolated_db)
    assert len(contracts) == 1
    assert contracts[0]["carrier_id"] == carrier_b


def test_no_substitution_when_carrier_already_selected(
    window, isolated_db, carrier_a, warning_recorder
):
    """Перевозчик в форме — тот же, что у водителя: форма не переписывается."""
    _bind_driver(window, carrier_a)
    _select_carrier_in_form(window, isolated_db, carrier_a)
    window.contract_tab.number.setText("CARRIER-7")

    window._on_save_to_db()

    assert warning_recorder.calls == []
    contracts = _contracts(isolated_db)
    assert len(contracts) == 1
    assert contracts[0]["carrier_id"] == carrier_a


def test_missing_carrier_record_keeps_form_empty(
    window, isolated_db, warning_recorder
):
    """Привязка ведёт на несуществующую запись — форма остаётся пустой."""
    _bind_driver(window, 999999)
    window.contract_tab.number.setText("CARRIER-8")

    window._on_save_to_db()

    assert window.carrier_tab.full_name.text() == ""
    assert warning_recorder.calls == [], "сверять не с чем — и спрашивать не о чем"
    contracts = _contracts(isolated_db)
    assert len(contracts) == 1
    assert contracts[0]["carrier_id"] is None
