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
  * собранные данные несут ключ `name` (его читает генератор договора);
  * таблицы точек растягиваются по вертикали, а строка вмещает две строки
    текста — длинный адрес виден целиком (ШАГ «Высота таблиц точек»).

База — временная (`isolated_db`), все данные синтетические, ПДн нет.
"""

import os
from typing import Dict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtGui import QFontMetrics  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QHeaderView, QSizePolicy,
)

from db.database import save_address  # noqa: E402
from ui.tabs import contract_tab as tab_module  # noqa: E402
from ui.tabs.contract_tab import (  # noqa: E402
    COL_ADDRESS, COL_DATE, COL_NAME, COL_TIME, POINT_TABLE_MIN_HEIGHT,
    POINT_HEADERS, ContractTab,
)
from ui.widgets.table_helpers import ROW_HEIGHT_TWO_LINES  # noqa: E402


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


# ─────────────────────────────────────────────────────────────
# Удаление последней строки (ШАГ FIX-5, часть B)
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def quiet_dialogs(monkeypatch):
    """
    Модальные окна не должны останавливать тест.

    В offscreen-режиме модальный QMessageBox роняет прогон (access
    violation), поэтому тексты перехватываются: словарь со списками
    warning / information.
    """
    from PyQt5.QtWidgets import QMessageBox

    seen = {"warning": [], "information": []}

    def recorder(kind):
        def _record(parent, title, text, *args, **kwargs):
            seen[kind].append(text)
            return QMessageBox.Ok
        return staticmethod(_record)

    for kind in seen:
        monkeypatch.setattr(QMessageBox, kind, recorder(kind))
    return seen


def _delete_all_points(tab: ContractTab) -> None:
    """Удаляет все строки обеих таблиц — как оператор: строка выбрана."""
    for table, remove in (
        (tab.loadings_table, tab._on_remove_loading),
        (tab.unloadings_table, tab._on_remove_unloading),
    ):
        while table.rowCount():
            table.setCurrentCell(0, COL_ADDRESS)
            remove()


def test_can_delete_last_loading_row(tab):
    """Последнюю строку погрузки удалить можно: пустая таблица — норма."""
    table = tab.loadings_table
    table.setCurrentCell(0, COL_ADDRESS)

    tab._on_remove_loading()

    assert table.rowCount() == 0


def test_can_delete_last_unloading_row(tab):
    """Последнюю строку выгрузки удалить тоже можно."""
    table = tab.unloadings_table
    table.setCurrentCell(0, COL_ADDRESS)

    tab._on_remove_unloading()

    assert table.rowCount() == 0


def test_delete_without_selection_warns(tab, quiet_dialogs):
    """Без выбранной строки удаление не идёт — но и не падает."""
    table = tab.loadings_table
    table.clearSelection()
    table.setCurrentCell(-1, -1)

    tab._on_remove_loading()

    assert table.rowCount() == 1
    assert quiet_dialogs["warning"] == ["Выберите строку для удаления."]


def test_delete_leaves_no_empty_row_behind(tab):
    """После удаления пустая строка НЕ добавляется автоматически."""
    _delete_all_points(tab)

    assert tab.loadings_table.rowCount() == 0
    assert tab.unloadings_table.rowCount() == 0


def test_empty_loadings_table_returns_empty_list(tab):
    """Пустая таблица отдаёт пустой список (не None, без падения)."""
    _delete_all_points(tab)

    assert tab.get_loadings() == []
    assert tab.get_unloadings() == []

    data = tab.get_data()
    assert data["loadings"] == []
    assert data["unloadings"] == []
    assert data["loading_address"] == ""
    assert data["unloading_address_1"] == ""


def test_empty_points_reach_contract_data_as_empty_list(tab):
    """Пустые таблицы доходят до ContractData пустыми списками, а не None."""
    from core.contract_data import ContractData

    _delete_all_points(tab)

    contract_data = ContractData.coerce(tab.get_data())

    assert contract_data.loadings == []
    assert contract_data.unloadings == []


def test_create_contract_blocked_on_empty_loadings(tab):
    """
    Пустая таблица погрузок — путь создания договора закрыт.

    Диалог «Проверьте данные» печатает ошибки валидатора; при пустой
    таблице в нём есть строка про место погрузки (ШАГ FIX-5, часть B.4).
    """
    from core.contract_data import ContractData
    from core.contracts.perevozka.validator import PerevozkaValidator

    _delete_all_points(tab)

    report = PerevozkaValidator().check(ContractData.coerce(tab.get_data()))

    assert report.has_errors is True
    assert "Укажите хотя бы одно место погрузки" in report.errors
    assert "Укажите хотя бы одно место выгрузки" in report.errors


def test_deleted_rows_can_be_added_back(tab):
    """После удаления всех строк «Добавить погрузку» снова даёт строку."""
    _delete_all_points(tab)

    tab._on_add_loading()
    tab._on_add_unloading()

    assert tab.loadings_table.rowCount() == 1
    assert tab.unloadings_table.rowCount() == 1


# ─────────────────────────────────────────────────────────────
# Ширины колонок и подсказки (ШАГ FIX-5, часть C)
# ─────────────────────────────────────────────────────────────

def test_loadings_address_is_stretch(tab):
    """Адрес — главная колонка: тянется по ширине таблицы."""
    header = tab.loadings_table.horizontalHeader()

    assert header.sectionResizeMode(COL_ADDRESS) == QHeaderView.Stretch
    assert (
        tab.unloadings_table.horizontalHeader().sectionResizeMode(COL_ADDRESS)
        == QHeaderView.Stretch
    )


def test_loadings_name_is_contents(tab):
    """Наименование салона — по содержимому: длина у названий разная."""
    header = tab.loadings_table.horizontalHeader()

    assert header.sectionResizeMode(COL_NAME) == QHeaderView.ResizeToContents
    assert (
        tab.unloadings_table.horizontalHeader().sectionResizeMode(COL_NAME)
        == QHeaderView.ResizeToContents
    )


def test_loadings_date_is_fixed(tab):
    """Дата и время — фиксированные колонки: 90 и 80 пикселей, не меньше."""
    for table in (tab.loadings_table, tab.unloadings_table):
        header = table.horizontalHeader()

        assert header.sectionResizeMode(COL_DATE) == QHeaderView.Interactive
        assert header.sectionResizeMode(COL_TIME) == QHeaderView.Interactive
        assert header.sectionSize(COL_DATE) == 90
        assert header.sectionSize(COL_TIME) == 80
        assert header.minimumSectionSize() == 70


def test_loadings_widths_persist_between_sessions(qapp, isolated_db):
    """
    Растянутая колонка остаётся растянутой после перезапуска.

    «Новая сессия» — новая вкладка: ширины читаются из QSettings
    (хранилище тестов изолировано, см. isolated_qsettings).
    """
    first = ContractTab()
    first.loadings_table.horizontalHeader().resizeSection(COL_DATE, 130)
    first.loadings_table._widths_saver.flush()

    second = ContractTab()

    assert second.loadings_table.horizontalHeader().sectionSize(COL_DATE) == 130
    # Выгрузки хранятся отдельным ключом: чужая раскладка их не трогает.
    assert second.unloadings_table.horizontalHeader().sectionSize(COL_DATE) == 90

    first.deleteLater()
    second.deleteLater()


def test_tooltip_on_long_address(tab):
    """Наведение на длинный адрес показывает его целиком."""
    table = tab.loadings_table
    long_address = (
        "183052, Мурманская область, г. Мурманск, пр. Кольский, д. 53, "
        "строение 2, склад № 17"
    )
    table.item(0, COL_ADDRESS).setText(long_address)

    table.itemEntered.emit(table.item(0, COL_ADDRESS))

    assert table.item(0, COL_ADDRESS).toolTip() == long_address


# ─────────────────────────────────────────────────────────────
# Высота таблиц точек — растяжение по вертикали (ШАГ «Высота таблиц точек»)
# ─────────────────────────────────────────────────────────────

def test_point_tables_expand_vertically(tab):
    """
    Обе таблицы точек растягиваются по вертикали.

    Такое же поведение у таблицы «Перевозимые авто» (эталон): таблица
    забирает свободное место, а не отдаёт его stretch-распорке ниже.
    """
    for table in (tab.loadings_table, tab.unloadings_table):
        policy = table.sizePolicy()

        assert policy.verticalPolicy() == QSizePolicy.Expanding
        assert policy.horizontalPolicy() == QSizePolicy.Expanding


def test_point_table_minimum_height_allows_two_rows(tab):
    """
    Минимум высоты — 120 пикселей, а не 80.

    Восемьдесят пикселей — это шапка и полторы строки: адрес второй точки
    оператор не видел. Сто двадцать — шапка и две полные строки по 40.
    """
    for table in (tab.loadings_table, tab.unloadings_table):
        assert table.minimumHeight() == POINT_TABLE_MIN_HEIGHT
        assert table.minimumHeight() >= 120
        assert table.verticalHeader().defaultSectionSize() == ROW_HEIGHT_TWO_LINES
        assert table.wordWrap() is True


def test_point_table_row_takes_two_lines_of_the_current_font(tab):
    """
    Строка таблицы точек вмещает две строки текущего шрифта.

    Длинный адрес переносится по словам; при прежней высоте строки
    (31 пиксель по умолчанию) вторая строка адреса обрезалась.
    """
    for table in (tab.loadings_table, tab.unloadings_table):
        metrics = QFontMetrics(table.font())

        assert (
            table.verticalHeader().defaultSectionSize()
            >= 2 * metrics.lineSpacing()
        )


# ─────────────────────────────────────────────────────────────
# Наименование салона в базе (ШАГ FIX-6, часть F)
# ─────────────────────────────────────────────────────────────

def test_point_name_round_trip_through_database(tab, isolated_db):
    """
    Наименование салона из вкладки доходит до базы и возвращается обратно.

    Стык «вкладка → `save_contract_points` → `load_contract_points` →
    вкладка»: раньше колонки `name` в базе не было, и имя терялось при
    перезагрузке сохранённого договора.
    """
    tab.loadings_table.item(0, COL_NAME).setText("ООО «Салон Погрузки»")
    tab.loadings_table.item(0, COL_ADDRESS).setText("Склад А")
    tab.unloadings_table.item(0, COL_NAME).setText("ООО «Салон Выгрузки»")
    tab.unloadings_table.item(0, COL_ADDRESS).setText("Склад Б")

    loadings = tab.get_loadings()
    unloadings = tab.get_unloadings()

    contract_id = isolated_db.save_contract({"number": "ROUND-TRIP-1"})
    isolated_db.save_contract_points(contract_id, loadings, unloadings)

    saved = isolated_db.load_contract_points(contract_id)

    assert saved["loadings"][0]["name"] == "ООО «Салон Погрузки»"
    assert saved["unloadings"][0]["name"] == "ООО «Салон Выгрузки»"

    # И обратно во вкладку: имя заполняет свою колонку.
    restored = ContractTab()
    try:
        restored.fill_data({
            "loadings": saved["loadings"],
            "unloadings": saved["unloadings"],
        })

        assert restored.loadings_table.item(0, COL_NAME).text() == (
            "ООО «Салон Погрузки»"
        )
        assert restored.unloadings_table.item(0, COL_NAME).text() == (
            "ООО «Салон Выгрузки»"
        )
    finally:
        restored.deleteLater()
