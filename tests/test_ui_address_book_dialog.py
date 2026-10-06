#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты диалога справочника адресов/салонов (ШАГ FIX-2.2, часть C).

Проверяется ui/address_book_dialog.py:

  * колонки таблицы — код, наименование салона, город, адрес, счётчик
    использований (значения берутся из полей записи, а не из адреса);
  * EditAddressDialog: поля кода, наименования, ИНН и города, get_data()
    и обязательность адреса;
  * импорт из Excel: графы ищутся по заголовкам (КОД / ИНН / Юр. Лицо /
    Город / Адрес доставки автомобилей), а файл прежнего формата
    (одна колонка) читается запасным путём;
  * выбор записи отдаёт наружу и наименование салона, и адрес выгрузки;
  * поиск в UI по коду и наименованию доходит до базы.

Qt — в offscreen-режиме. Модальные диалоги подменяются: ни один тест
не должен останавливаться на QMessageBox и QFileDialog.

Все данные синтетические, реальных ПДн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import openpyxl  # noqa: E402
import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QDialog  # noqa: E402

from db.database import get_addresses, save_address  # noqa: E402
from ui.address_book_dialog import (  # noqa: E402
    COL_ADDRESS, COL_CODE, COL_SALON_CITY, COL_SALON_NAME, COL_USAGE,
    AddressBookDialog, EditAddressDialog,
)

#: Заголовки граф тестового файла справочника салонов.
SALON_HEADERS = (
    "№", "КОД", "ИНН", "КПП", "Юр. Лицо", "КОД", "Город",
    "Юридический адрес", "Адрес доставки автомобилей",
    "Получатели уведомлений (e-mail)", "Контактный номер",
    "Контактный номер приемщика", "Комментарии (график и время приемки)",
    "Региональный Менеджер",
)

SALON_ROW = (
    "JMR-A048", "7701234567", 'ООО "КАР АЦ"', "Москва",
    "г. Москва, ул. Складская, д. 8",
)


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _no_real_salons_file(monkeypatch):
    """Изолированная база не заливает рабочий файл справочника салонов."""
    import db.database as database

    monkeypatch.setattr(
        database, "salons_xlsx_path",
        lambda: "tests/_tmp/no-such-salons-file.xlsx",
    )


@pytest.fixture
def quiet_dialogs(monkeypatch):
    """Модальные окна не должны останавливать тест."""
    from PyQt5.QtWidgets import QMessageBox

    def answer(box, *args, **kwargs):
        # question ждёт Yes/No, остальным достаточно Ok.
        return QMessageBox.Yes if box is QMessageBox.question else QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: answer(QMessageBox.warning)))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: answer(QMessageBox.information)))
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: answer(QMessageBox.critical)))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: answer(QMessageBox.question)))


@pytest.fixture
def salon_xlsx(work_dir):
    """Файл справочника салонов с шапкой «Места выгрузок»."""
    path = work_dir / "salons.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(list(SALON_HEADERS))
    sheet.append([
        1, SALON_ROW[0], SALON_ROW[1], "667845001", SALON_ROW[2], SALON_ROW[0],
        SALON_ROW[3], "юридический адрес", SALON_ROW[4],
        "mail@example.ru", "8(900) 000-00-00", "", "с 9 до 20", "Менеджеров М",
    ])
    workbook.save(str(path))
    return str(path)


@pytest.fixture
def plain_xlsx(work_dir):
    """Файл прежнего формата: одна колонка «Адрес»."""
    path = work_dir / "plain.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Адрес"])
    sheet.append(["г. Москва, ул. Южная, д. 2"])
    sheet.append(["г. Москва, ул. Третья, д. 3"])
    workbook.save(str(path))
    return str(path)


# ─────────────────────────────────────────────────────────────
# Таблица справочника
# ─────────────────────────────────────────────────────────────

def test_table_columns(qt_app, isolated_db):
    dialog = AddressBookDialog("unloading")

    headers = [
        dialog.table.horizontalHeaderItem(i).text()
        for i in range(dialog.table.columnCount())
    ]
    assert headers == [
        "ID", "Код", "Наименование салона", "Город", "Адрес", "Использован",
    ]


def test_table_shows_salon_fields(qt_app, isolated_db):
    """Колонки заполняются полями записи, а не только адресом."""
    save_address(
        "unloading", SALON_ROW[4],
        salon_name=SALON_ROW[2], salon_code=SALON_ROW[0],
        salon_inn=SALON_ROW[1], salon_city=SALON_ROW[3],
    )

    dialog = AddressBookDialog("unloading")

    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, COL_CODE).text() == "JMR-A048"
    assert dialog.table.item(0, COL_SALON_NAME).text() == 'ООО "КАР АЦ"'
    assert dialog.table.item(0, COL_SALON_CITY).text() == "Москва"
    assert dialog.table.item(0, COL_ADDRESS).text() == SALON_ROW[4]
    assert dialog.table.item(0, COL_USAGE).text() == "1 раз"


def test_table_row_without_salon_fields(qt_app, isolated_db):
    """Прежняя запись (только адрес) показывается пустыми колонками салона."""
    save_address("loading", "г. Москва, ул. Южная, д. 2")

    dialog = AddressBookDialog("loading")

    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, COL_CODE).text() == ""
    assert dialog.table.item(0, COL_SALON_NAME).text() == ""
    assert dialog.table.item(0, COL_ADDRESS).text() == "г. Москва, ул. Южная, д. 2"


def test_search_by_code_and_name(qt_app, isolated_db):
    """Поиск в UI по коду и по наименованию доходит до базы."""
    save_address(
        "unloading", SALON_ROW[4],
        salon_name=SALON_ROW[2], salon_code=SALON_ROW[0],
    )
    save_address(
        "unloading", "г. Тверь, ул. Новая, д. 1",
        salon_name='ООО "Другой салон"', salon_code="JMR-B001",
    )
    dialog = AddressBookDialog("unloading")

    dialog.search_input.setText("JMR-A048")
    dialog._reload_first_page()
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, COL_CODE).text() == "JMR-A048"

    dialog.search_input.setText("Другой")
    dialog._reload_first_page()
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, COL_SALON_NAME).text() == 'ООО "Другой салон"'

    dialog._on_reset_search()
    dialog._reload_first_page()
    assert dialog.table.rowCount() == 2


def test_pick_returns_salon_name_and_address(qt_app, isolated_db):
    """Выбор записи отдаёт и «Юр. Лицо», и «Адрес доставки»."""
    save_address(
        "unloading", SALON_ROW[4],
        salon_name=SALON_ROW[2], salon_code=SALON_ROW[0],
        salon_inn=SALON_ROW[1], salon_city=SALON_ROW[3],
    )
    dialog = AddressBookDialog("unloading")
    dialog.table.setCurrentCell(0, 0)

    dialog._on_pick()

    assert dialog.result() == QDialog.Accepted
    assert dialog.selected_address == {
        "address": SALON_ROW[4],
        "date": "",
        "time_window": "",
        "salon_name": SALON_ROW[2],
        "salon_code": SALON_ROW[0],
        "salon_inn": SALON_ROW[1],
        "salon_city": SALON_ROW[3],
    }


# ─────────────────────────────────────────────────────────────
# EditAddressDialog
# ─────────────────────────────────────────────────────────────

def test_edit_dialog_has_salon_fields(qt_app):
    dialog = EditAddressDialog(
        address="г. Москва, ул. Складская, д. 8",
        salon_name='ООО "КАР АЦ"', salon_code="JMR-A048",
        salon_inn="7701234567", salon_city="Москва",
    )

    data = dialog.get_data()

    assert data == {
        "address": "г. Москва, ул. Складская, д. 8",
        "date": "",
        "time_window": "",
        "salon_name": 'ООО "КАР АЦ"',
        "salon_code": "JMR-A048",
        "salon_inn": "7701234567",
        "salon_city": "Москва",
    }


def test_edit_dialog_empty_values_are_empty_strings(qt_app):
    dialog = EditAddressDialog()

    assert dialog.get_data() == {
        "address": "", "date": "", "time_window": "",
        "salon_name": "", "salon_code": "", "salon_inn": "", "salon_city": "",
    }


def test_edit_dialog_requires_address(qt_app, quiet_dialogs):
    """Без адреса диалог не закрывается: адрес — ключ уникальности записи."""
    dialog = EditAddressDialog(salon_name='ООО "КАР АЦ"')

    dialog._on_save()

    assert dialog.result() != QDialog.Accepted


def test_edit_dialog_accepts_with_address(qt_app, quiet_dialogs):
    dialog = EditAddressDialog(address="г. Москва, ул. Складская, д. 8")

    dialog._on_save()

    assert dialog.result() == QDialog.Accepted


# ─────────────────────────────────────────────────────────────
# Импорт из Excel
# ─────────────────────────────────────────────────────────────

def _patch_file_dialog(monkeypatch, path):
    """QFileDialog.getOpenFileName возвращает заданный файл."""
    from PyQt5.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (path, "")),
    )


def test_import_reads_columns_by_header(
    qt_app, isolated_db, salon_xlsx, monkeypatch, quiet_dialogs
):
    """Импорт из файла с шапкой раскладывает графы по полям записи."""
    _patch_file_dialog(monkeypatch, salon_xlsx)
    dialog = AddressBookDialog("unloading")
    monkeypatch.setattr(dialog, "_reload_first_page", lambda: None)

    dialog._on_import_excel()

    rows = get_addresses("unloading")
    assert len(rows) == 1
    row = rows[0]
    assert row["salon_code"] == SALON_ROW[0]
    assert row["salon_name"] == SALON_ROW[2]
    assert row["salon_inn"] == SALON_ROW[1]
    assert row["salon_city"] == SALON_ROW[3]
    assert row["address"] == SALON_ROW[4]
    # Контакты и региональный менеджер не импортируются.
    assert "@" not in str(row)
    assert "Менеджеров" not in str(row)


def test_import_falls_back_to_first_column(
    qt_app, isolated_db, plain_xlsx, monkeypatch, quiet_dialogs
):
    """Файл без шапки справочника читается как раньше — по первой колонке."""
    _patch_file_dialog(monkeypatch, plain_xlsx)
    dialog = AddressBookDialog("unloading")
    monkeypatch.setattr(dialog, "_reload_first_page", lambda: None)

    dialog._on_import_excel()

    rows = get_addresses("unloading")
    # Записи полные: адрес есть, поля салона пустые (в файле их не было).
    assert sorted(row["address"] for row in rows) == [
        "г. Москва, ул. Третья, д. 3",
        "г. Москва, ул. Южная, д. 2",
    ]
    assert {row["salon_name"] for row in rows} == {""}


def test_import_cancelled_does_nothing(
    qt_app, isolated_db, salon_xlsx, monkeypatch, quiet_dialogs
):
    """Отмена выбора файла ничего не импортирует."""
    _patch_file_dialog(monkeypatch, "")
    dialog = AddressBookDialog("unloading")
    monkeypatch.setattr(dialog, "_reload_first_page", lambda: None)

    dialog._on_import_excel()

    assert get_addresses("unloading") == []
