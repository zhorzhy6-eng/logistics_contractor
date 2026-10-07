#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты настройки таблиц — ui/widgets/table_helpers.py (ШАГ FIX-5, часть A).

Проверяется helper, на котором стоят все таблицы точек и справочник:

  * режимы колонок — «растянуть» / «по содержимому» / «фиксированная»
    (QHeaderView.Stretch / ResizeToContents / Interactive);
  * минимальные ширины колонок;
  * подсказка с полным текстом ячейки (itemEntered + mouse tracking);
  * сохранение и восстановление ширин через QSettings;
  * без ключа хранилища таблица ничего не пишет.

QSettings перенаправлен в tests/_tmp (см. `isolated_qsettings` в
tests/conftest.py): рабочие настройки оператора не читаются и не пишутся.
Qt — в offscreen-режиме.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QHeaderView, QTableWidget, QTableWidgetItem,
)

from ui.widgets.table_helpers import (  # noqa: E402
    MODE_CONTENTS, MODE_FIXED, MODE_STRETCH, STORAGE_KEY_PROPERTY,
    WidthsSaver, column_index, install_tooltip_on_table,
    restore_column_widths, save_column_widths, setup_point_table,
    stored_widths,
)

#: Ключ тестового хранилища (боевые начинаются с «ui/»).
STORAGE_KEY = "ui/test/point_columns"

#: Длинный адрес: в колонке не помещается — нужен для подсказки.
LONG_ADDRESS = (
    "183052, Мурманская область, г. Мурманск, пр. Кольский, д. 53, "
    "строение 2, склад № 17"
)


@pytest.fixture(scope="module")
def qapp():
    """Одно приложение Qt на модуль (offscreen)."""
    app = QApplication.instance() or QApplication([])
    yield app


def make_table(headers, rows=1) -> QTableWidget:
    """Свежая таблица с заголовками и одной пустой строкой."""
    table = QTableWidget(rows, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    for row in range(rows):
        for column in range(len(headers)):
            table.setItem(row, column, QTableWidgetItem(""))
    return table


@pytest.fixture
def table(qapp) -> QTableWidget:
    """Таблица точек: четыре колонки, как у погрузок Экспедиторства."""
    widget = make_table(["Наименование", "Адрес", "Дата", "Время"])
    yield widget
    widget.deleteLater()


# ─────────────────────────────────────────────────────────────
# Режимы колонок
# ─────────────────────────────────────────────────────────────

def test_setup_point_table_sets_modes(table):
    """Описание колонок доходит до QHeaderView: у каждой свой режим."""
    setup_point_table(table, [
        (0, MODE_CONTENTS, 0),
        (1, MODE_STRETCH, 0),
        (2, MODE_FIXED, 90),
        (3, MODE_FIXED, 80),
    ])

    header = table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeToContents
    assert header.sectionResizeMode(1) == QHeaderView.Stretch
    assert header.sectionResizeMode(2) == QHeaderView.Interactive
    assert header.sectionResizeMode(3) == QHeaderView.Interactive


def test_setup_stretch_mode(table):
    """«stretch» — колонка тянется по ширине таблицы."""
    setup_point_table(table, [("Адрес", MODE_STRETCH, 0)])

    assert table.horizontalHeader().sectionResizeMode(1) == QHeaderView.Stretch


def test_setup_contents_mode(table):
    """«contents» — ширина по содержимому колонки."""
    setup_point_table(table, [("Наименование", MODE_CONTENTS, 0)])

    assert (
        table.horizontalHeader().sectionResizeMode(0)
        == QHeaderView.ResizeToContents
    )


def test_setup_fixed_mode(table):
    """«fixed» — колонка с заданной шириной, оператор может её менять."""
    setup_point_table(table, [(2, MODE_FIXED, 90)])

    header = table.horizontalHeader()
    assert header.sectionResizeMode(2) == QHeaderView.Interactive
    assert header.sectionSize(2) == 90


def test_setup_unknown_mode_is_skipped(table):
    """Неизвестный режим не роняет настройку — колонка остаётся как была."""
    setup_point_table(table, [(0, "растянуть", 0), (1, MODE_STRETCH, 0)])

    header = table.horizontalHeader()
    assert header.sectionResizeMode(0) != QHeaderView.Stretch
    assert header.sectionResizeMode(1) == QHeaderView.Stretch


def test_column_index_accepts_number_and_title(table):
    """Колонка ищется и по номеру, и по заголовку шапки."""
    assert column_index(table, 1) == 1
    assert column_index(table, "Адрес") == 1
    assert column_index(table, "Такой колонки нет") == -1
    assert column_index(table, 99) == -1


def test_minimum_widths_applied(table):
    """Минимумы колонок поднимают узкие ширины до заданных значений."""
    setup_point_table(
        table,
        [(0, MODE_CONTENTS, 0), (1, MODE_STRETCH, 0),
         (2, MODE_FIXED, 40), (3, MODE_FIXED, 40)],
        minimums={0: 100, 2: 80, 3: 70},
    )

    header = table.horizontalHeader()
    assert header.sectionSize(0) >= 100
    assert header.sectionSize(2) == 80
    assert header.sectionSize(3) == 70
    assert header.minimumSectionSize() == 70


def test_minimums_do_not_shrink_restored_width(qapp):
    """Минимум — это пол: восстановленную широкую колонку он не сужает."""
    first = make_table(["Наименование", "Адрес", "Дата", "Время"])
    setup_point_table(first, [(2, MODE_FIXED, 90)])
    first.horizontalHeader().resizeSection(2, 200)
    save_column_widths(first, STORAGE_KEY)

    second = make_table(["Наименование", "Адрес", "Дата", "Время"])
    setup_point_table(
        second, [(2, MODE_FIXED, 90)],
        storage_key=STORAGE_KEY, minimums={2: 80},
    )

    assert second.horizontalHeader().sectionSize(2) == 200

    first.deleteLater()
    second.deleteLater()


# ─────────────────────────────────────────────────────────────
# Подсказки
# ─────────────────────────────────────────────────────────────

def test_tooltip_appears_on_hover(table):
    """Наведение на ячейку даёт подсказку с полным текстом."""
    install_tooltip_on_table(table)

    table.item(0, 1).setText(LONG_ADDRESS)
    table.itemEntered.emit(table.item(0, 1))

    assert table.item(0, 1).toolTip() == LONG_ADDRESS
    assert table.hasMouseTracking() is True


def test_tooltip_keeps_own_cell_tooltip(table):
    """Своя подсказка ячейки («справочно») не затирается текстом ячейки."""
    install_tooltip_on_table(table)

    item = table.item(0, 2)
    item.setText("26.09.2026")
    item.setToolTip("Справочно. В договор идёт другая дата")
    table.itemEntered.emit(item)

    assert item.toolTip() == "Справочно. В договор идёт другая дата"


def test_tooltip_skips_empty_cell(table):
    """Пустая ячейка подсказки не получает: повторять нечего."""
    install_tooltip_on_table(table)

    table.itemEntered.emit(table.item(0, 1))

    assert table.item(0, 1).toolTip() == ""


def test_tooltip_installed_once(table):
    """Повторная установка не подключает обработчик второй раз."""
    install_tooltip_on_table(table)
    install_tooltip_on_table(table)

    table.item(0, 0).setText("Салон")
    table.itemEntered.emit(table.item(0, 0))

    assert table.item(0, 0).toolTip() == "Салон"


# ─────────────────────────────────────────────────────────────
# Сохранение и восстановление ширин
# ─────────────────────────────────────────────────────────────

def test_save_restore_widths_roundtrip(qapp):
    """Ширины, сохранённые одной таблицей, читает следующая (новая сессия)."""
    first = make_table(["Наименование", "Адрес", "Дата", "Время"])
    setup_point_table(first, [(2, MODE_FIXED, 90), (3, MODE_FIXED, 80)])
    first.horizontalHeader().resizeSection(2, 130)
    first.horizontalHeader().resizeSection(3, 110)
    save_column_widths(first, STORAGE_KEY)

    assert stored_widths(first, STORAGE_KEY)[2:] == [130, 110]

    # Новая таблица — как после перезапуска приложения.
    second = make_table(["Наименование", "Адрес", "Дата", "Время"])
    setup_point_table(second, [(2, MODE_FIXED, 90), (3, MODE_FIXED, 80)],
                      storage_key=STORAGE_KEY)

    assert second.horizontalHeader().sectionSize(2) == 130
    assert second.horizontalHeader().sectionSize(3) == 110

    first.deleteLater()
    second.deleteLater()


def test_restore_without_saved_widths_keeps_defaults(table):
    """Ничего не сохранено — таблица остаётся с ширинами по умолчанию."""
    setup_point_table(table, [(2, MODE_FIXED, 90)], storage_key=STORAGE_KEY)

    assert table.horizontalHeader().sectionSize(2) == 90


def test_restore_ignores_broken_values(table):
    """Битое значение в хранилище не роняет восстановление."""
    from PyQt5.QtCore import QSettings

    settings = QSettings(
        QSettings.IniFormat, QSettings.UserScope,
        "logistics_contractor", "logistics_contractor",
    )
    settings.setValue(STORAGE_KEY, ["мусор", "", 120])
    settings.sync()

    setup_point_table(table, [(2, MODE_FIXED, 90)], storage_key=STORAGE_KEY)

    assert table.horizontalHeader().sectionSize(2) == 120


def test_no_storage_key_does_not_save(table):
    """Без ключа хранилища таблица не сохраняется и не восстанавливается."""
    setup_point_table(table, [(2, MODE_FIXED, 90)])

    save_column_widths(table, "")
    restore_column_widths(table, "")

    assert table.property(STORAGE_KEY_PROPERTY) is None
    assert getattr(table, "_widths_saver", None) is None
    assert stored_widths(table, STORAGE_KEY) == []


def test_setup_remembers_storage_key(table):
    """Ключ хранилища запоминается в свойстве таблицы и включает автосохранение."""
    setup_point_table(table, [(2, MODE_FIXED, 90)], storage_key=STORAGE_KEY)

    assert table.property(STORAGE_KEY_PROPERTY) == STORAGE_KEY
    assert isinstance(table._widths_saver, WidthsSaver)


def test_width_change_is_saved_after_pause(table, qapp):
    """Правка раскладки записывается сама — окна типов прячутся, не закрываются."""
    setup_point_table(table, [(2, MODE_FIXED, 90)], storage_key=STORAGE_KEY)

    table.horizontalHeader().resizeSection(2, 140)
    table._widths_saver.flush()          # то же, что сделает таймер через 500 мс

    assert stored_widths(table, STORAGE_KEY)[2] == 140
