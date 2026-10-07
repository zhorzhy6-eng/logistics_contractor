#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты вкладки «Условия договора» (ui/tabs/contract_tab.py) — ШАГ FIX-2.5.

Что проверяется:

  * таблицы погрузок и выгрузок имеют ЧЕТЫРЕ колонки:
    «Наименование | Адрес | Дата | Время»;
  * наименование салона подтягивается из справочника адресов при вводе
    адреса (поиск по адресу, LIKE/FTS);
  * ручной ввод наименования не затирается подстановкой;
  * кнопка «Из справочника» кладёт в строку И наименование, И адрес;
  * собранные данные несут ключ `name` (его читает генератор договора).

База — временная (`isolated_db`), все данные синтетические, ПДн нет.
"""

import os
from typing import Dict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from db.database import save_address  # noqa: E402
from ui.tabs import contract_tab as tab_module  # noqa: E402
from ui.tabs.contract_tab import (  # noqa: E402
    COL_ADDRESS, COL_DATE, COL_NAME, COL_TIME,
    POINT_HEADERS, ContractTab,
)


@pytest.fixture(scope="module")
def qapp():
    """Одно приложение Qt на модуль (offscreen)."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qapp, isolated_db) -> ContractTab:
    """Вкладка на пустой временной базе."""
    return ContractTab()


#: Салон для справочника выгрузки: адрес из «Мест выгрузок» Логистикса.
SALON_ADDRESS = "г. Москва, ул. Перерва, д. 19, стр. 3"
SALON_NAME = "ООО «Тестовый Салон»"


def _seed_salon() -> None:
    """Одна запись справочника ВЫГРУЗКИ с наименованием салона."""
    save_address(
        "unloading",
        SALON_ADDRESS,
        salon_name=SALON_NAME,
        salon_code="JMR-A999",
        salon_inn="7701234567",
        salon_city="Москва",
    )


def _fill_address(tab: ContractTab, table, row: int, address: str) -> None:
    """Имитация ручного ввода адреса: правка ячейки + сигнал таблицы."""
    table.item(row, COL_ADDRESS).setText(address)


# ─────────────────────────────────────────────────────────────
# Структура таблиц
# ─────────────────────────────────────────────────────────────

def test_loadings_table_has_name_column(tab):
    """Таблица погрузок: четыре колонки, первая — «Наименование»."""
    table = tab.loadings_table

    assert table.columnCount() == 4
    assert table.columnCount() == len(POINT_HEADERS)
    headers = [table.horizontalHeaderItem(col).text() for col in range(4)]
    assert headers == ["Наименование", "Адрес *", "Дата", "Время"]
    assert headers[COL_NAME] == "Наименование"


def test_unloadings_table_has_name_column(tab):
    """Таблица выгрузок: четыре колонки, первая — «Наименование»."""
    table = tab.unloadings_table

    assert table.columnCount() == 4
    headers = [table.horizontalHeaderItem(col).text() for col in range(4)]
    assert headers == ["Наименование", "Адрес *", "Дата", "Время"]


def test_new_rows_have_all_four_cells(tab):
    """У добавленной строки заполнены все четыре ячейки (нет None)."""
    tab._on_add_loading()

    for col in (COL_NAME, COL_ADDRESS, COL_DATE, COL_TIME):
        assert tab.loadings_table.item(tab.loadings_table.rowCount() - 1, col) is not None


# ─────────────────────────────────────────────────────────────
# Подтягивание салона по адресу
# ─────────────────────────────────────────────────────────────

def test_address_change_triggers_salon_lookup(tab):
    """Ввод адреса подтягивает наименование салона из справочника."""
    _seed_salon()
    table = tab.unloadings_table

    _fill_address(tab, table, 0, SALON_ADDRESS)
    tab._lookup_salon_name()          # то же, что сделает таймер через 400 мс

    assert table.item(0, COL_NAME).text() == SALON_NAME
    assert table.item(0, COL_ADDRESS).text() == SALON_ADDRESS


def test_salon_lookup_does_not_overwrite_manual_name(tab):
    """Ручное наименование остаётся: подстановка идёт только в пустую ячейку."""
    _seed_salon()
    table = tab.unloadings_table

    table.item(0, COL_NAME).setText("Моё название")
    _fill_address(tab, table, 0, SALON_ADDRESS)
    tab._lookup_salon_name()

    assert table.item(0, COL_NAME).text() == "Моё название"


def test_unknown_address_leaves_name_empty(tab):
    """Записи в справочнике нет — наименование пустое, адрес не тронут."""
    _seed_salon()
    table = tab.unloadings_table
    address = "г. Владивосток, ул. Неизвестная, д. 1"

    _fill_address(tab, table, 0, address)
    tab._lookup_salon_name()

    assert table.item(0, COL_NAME).text() == ""
    assert table.item(0, COL_ADDRESS).text() == address


def test_empty_address_does_not_look_up(tab):
    """Пустой адрес поиск не запускает: искать нечего."""
    _seed_salon()
    table = tab.unloadings_table

    _fill_address(tab, table, 0, "")
    tab._lookup_salon_name()

    assert table.item(0, COL_NAME).text() == ""


def test_address_edit_schedules_lookup(tab):
    """Правка колонки «Адрес» ставит таймер поиска, правка других — нет."""
    table = tab.unloadings_table
    tab._salon_cell = None

    table.item(0, COL_DATE).setText("01.01.2026")
    assert tab._salon_cell is None, "поиск запустился от правки даты"

    table.item(0, COL_ADDRESS).setText(SALON_ADDRESS)
    assert tab._salon_cell == (table, 0)


def test_loading_lookup_uses_loading_book(tab, isolated_db):
    """Погрузки ищут в своём справочнике, а не в справочнике выгрузок."""
    save_address("loading", SALON_ADDRESS, salon_name="Только для погрузок")

    loading_address = "г. Москва, ул. Складская, д. 7"
    save_address("loading", loading_address, salon_name="Склад Погрузки")

    table = tab.loadings_table
    _fill_address(tab, table, 0, loading_address)
    tab._lookup_salon_name()

    assert table.item(0, COL_NAME).text() == "Склад Погрузки"


# ─────────────────────────────────────────────────────────────
# Кнопка «Из справочника»
# ─────────────────────────────────────────────────────────────

class _FakeBookDialog:
    """Заглушка диалога справочника: сразу «выбирает» запись."""

    selected: Dict[str, str] = {}

    def __init__(self, point_type, parent=None):
        self.point_type = point_type
        self.selected_address = dict(self.selected)

    def exec_(self) -> int:
        return 1


def test_book_dialog_fills_name_and_address(tab, monkeypatch):
    """
    «Из справочника» заполняет наименование, адрес, дату и время.

    Наименование берётся из графы «Юр. Лицо» записи справочника салонов.
    """
    _FakeBookDialog.selected = {
        "salon_name": SALON_NAME,
        "address": SALON_ADDRESS,
        "date": "05.10.2026",
        "time_window": "09:00-18:00",
    }
    monkeypatch.setattr(tab_module, "AddressBookDialog", _FakeBookDialog)

    tab._on_open_book("unloading")

    table = tab.unloadings_table
    assert table.item(0, COL_NAME).text() == SALON_NAME
    assert table.item(0, COL_ADDRESS).text() == SALON_ADDRESS
    assert table.item(0, COL_DATE).text() == "05.10.2026"
    assert table.item(0, COL_TIME).text() == "09:00-18:00"


def test_book_dialog_without_salon_name_leaves_name_empty(tab, monkeypatch):
    """У записи нет наименования — колонка остаётся пустой, адрес на месте."""
    _FakeBookDialog.selected = {
        "salon_name": "",
        "address": "г. Тверь, ул. Тестовая, д. 3",
        "date": "",
        "time_window": "",
    }
    monkeypatch.setattr(tab_module, "AddressBookDialog", _FakeBookDialog)

    tab._on_open_book("loading")

    table = tab.loadings_table
    assert table.item(0, COL_NAME).text() == ""
    assert table.item(0, COL_ADDRESS).text() == "г. Тверь, ул. Тестовая, д. 3"


# ─────────────────────────────────────────────────────────────
# Сбор данных: ключ name доходит до генератора
# ─────────────────────────────────────────────────────────────

def test_get_unloadings_carries_name(tab, monkeypatch):
    """Собранная точка несёт ключ name — его читает генератор договора."""
    _FakeBookDialog.selected = {
        "salon_name": SALON_NAME,
        "address": SALON_ADDRESS,
        "date": "",
        "time_window": "",
    }
    monkeypatch.setattr(tab_module, "AddressBookDialog", _FakeBookDialog)
    tab._on_open_book("unloading")

    unloadings = tab.get_unloadings()

    assert unloadings == [{
        "name": SALON_NAME,
        "address": SALON_ADDRESS,
        "date": "",
        "time_window": "",
    }]


def test_get_loadings_without_name_has_empty_key(tab):
    """Точка без наименования отдаёт пустую строку, а не отсутствие ключа."""
    table = tab.loadings_table
    _fill_address(tab, table, 0, "г. Москва, ул. Складская, д. 7")

    loadings = tab.get_loadings()

    assert loadings[0]["name"] == ""
    assert loadings[0]["address"] == "г. Москва, ул. Складская, д. 7"


def test_fill_data_accepts_name_and_salon_name(tab):
    """Заполнение формы понимает и `name`, и `salon_name` (данные Логистикса)."""
    tab.fill_data({"unloadings": [
        {"name": "Из name", "address": "Адрес 1", "date": "", "time_window": ""},
        {"salon_name": "Из salon_name", "address": "Адрес 2",
         "date": "", "time_window": ""},
    ]})

    table = tab.unloadings_table
    assert table.item(0, COL_NAME).text() == "Из name"
    assert table.item(1, COL_NAME).text() == "Из salon_name"


def test_clear_resets_name_column(tab):
    """Очистка формы сбрасывает и наименование."""
    table = tab.unloadings_table
    table.item(0, COL_NAME).setText(SALON_NAME)

    tab.clear()

    assert tab.unloadings_table.item(0, COL_NAME).text() == ""
