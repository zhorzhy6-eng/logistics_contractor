#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ширина колонок запоминается между запусками (часть D ночного прогона, тесты).

Что было не так
---------------
Автозапись ширин отложена на полсекунды (`AUTOSAVE_DELAY_MS`). Если оператор
растянул колонку и сразу закрыл окно (диалог, программу), таймер не успевал
сработать — раскладка терялась. При закрытии таблица уничтожалась, и ширины
прочитать было уже нельзя.

Что проверяется
---------------
1. **Запись и восстановление.** Одна колонка, все колонки, таблица без
   ключа хранилища (ничего не пишется).
2. **Сохранение на закрытии.** `done()`/`closeEvent` диалогов и
   `force_close()` окна пишут раскладку явно, без ожидания таймера.
3. **Уничтожение таблицы.** Ширины пишутся из кэша, снятого при правке:
   у разрушаемой таблицы читать их уже нельзя.
4. **`save_all_column_widths`.** Обходит таблицы и деревья окна
   рекурсивно, пишет только те, у которых есть ключ хранилища.
5. **Живой сценарий.** «База данных» → растянуть «Наименование» → закрыть
   → открыть снова: ширина та же.

QSettings перенаправлен в tests/_tmp (фикстура `isolated_qsettings` в
conftest.py): рабочий INI-файл оператора не читается и не пишется.
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("PyQt5")

from PyQt5.QtCore import QSettings, QTimer, QEventLoop  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QTableWidget, QTableWidgetItem, QTreeWidget, QWidget,
    QVBoxLayout,
)

from ui.widgets.table_helpers import (  # noqa: E402
    MODE_FIXED, STORAGE_KEY_PROPERTY, SETTINGS_APPLICATION,
    SETTINGS_ORGANIZATION, WidthsSaver, _settings, flush_widths_saver,
    restore_all_column_widths, restore_column_widths, save_all_column_widths,
    save_column_widths, setup_point_table, stored_widths, tables_of,
    width_storage_key,
)

#: Ключ тестового хранилища (боевые начинаются с «ui/»).
KEY = "ui/test/widths_part_d"


@pytest.fixture(scope="module")
def qapp():
    """Одно приложение Qt на модуль (offscreen)."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def clean_settings():
    """Пустое хранилище до и после теста."""
    settings = _settings()
    settings.remove(KEY)
    settings.sync()
    yield
    settings.remove(KEY)
    settings.sync()


def make_table(columns: int = 3, rows: int = 1) -> QTableWidget:
    """Таблица с заголовками и одной строкой."""
    table = QTableWidget(rows, columns)
    table.setHorizontalHeaderLabels([f"Колонка {index}" for index in range(columns)])
    for row in range(rows):
        for column in range(columns):
            table.setItem(row, column, QTableWidgetItem(""))
    return table


def wait(ms: int) -> None:
    """Крутит цикл событий: нужен таймерам и deleteLater."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


# ─────────────────────────────────────────────────────────────
# 1. Запись и восстановление
# ─────────────────────────────────────────────────────────────

def test_save_and_restore_one_column(qapp):
    """Одна колонка: сохранили ширину — новая таблица её получила."""
    table = make_table()
    setup_point_table(table, [(0, MODE_FIXED, 100), (1, MODE_FIXED, 100),
                              (2, MODE_FIXED, 100)], storage_key=KEY)
    table.horizontalHeader().resizeSection(1, 321)
    save_column_widths(table, KEY)

    assert stored_widths(table, KEY) == [100, 321, 100]

    other = make_table()
    setup_point_table(other, [(0, MODE_FIXED, 100), (1, MODE_FIXED, 100),
                              (2, MODE_FIXED, 100)], storage_key=KEY)
    assert other.horizontalHeader().sectionSize(1) == 321
    table.deleteLater()
    other.deleteLater()


def test_save_and_restore_all_columns(qapp):
    """Все колонки: раскладка восстанавливается целиком."""
    widths = [90, 210, 330, 60]
    table = make_table(columns=4)
    setup_point_table(table, [(index, MODE_FIXED, 100) for index in range(4)],
                      storage_key=KEY)
    for index, width in enumerate(widths):
        table.horizontalHeader().resizeSection(index, width)
    save_column_widths(table, KEY)

    other = make_table(columns=4)
    restore_column_widths(other, KEY)
    got = [other.horizontalHeader().sectionSize(index) for index in range(4)]
    assert got == widths
    table.deleteLater()
    other.deleteLater()


def test_no_storage_key_writes_nothing(qapp):
    """Без ключа хранилища раскладка не пишется."""
    table = make_table()
    setup_point_table(table, [(0, MODE_FIXED, 100)], storage_key="")
    table.horizontalHeader().resizeSection(0, 300)
    save_column_widths(table, "")

    assert table.property(STORAGE_KEY_PROPERTY) in (None, "")
    assert stored_widths(table, "") == []
    table.deleteLater()


def test_restore_ignores_broken_values(qapp):
    """Битое значение в настройках не ломает таблицу."""
    settings = _settings()
    settings.setValue(KEY, ["мусор", -5, None])
    settings.sync()

    table = make_table()
    restore_column_widths(table, KEY)
    # Ширины остались положительными (Qt не даёт нулевую колонку).
    for index in range(3):
        assert table.horizontalHeader().sectionSize(index) > 0
    table.deleteLater()


def test_settings_are_isolated_to_tmp():
    """Тесты пишут в файл tests/_tmp, а не в рабочие настройки оператора."""
    settings = QSettings(QSettings.IniFormat, QSettings.UserScope,
                         SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
    file_name = settings.fileName().replace("\\", "/")
    assert "tests/_tmp" in file_name, file_name


# ─────────────────────────────────────────────────────────────
# 2. Запись на закрытии: диалоги и окно
# ─────────────────────────────────────────────────────────────

def test_db_manager_dialog_saves_on_done(qapp, isolated_db):
    """Диалог «База данных» пишет раскладку при закрытии (accept/Esc)."""
    from ui.db_manager_dialog import DbManagerDialog, ORGANIZATIONS_WIDTHS_KEY

    settings = _settings()
    settings.remove(ORGANIZATIONS_WIDTHS_KEY)
    settings.sync()

    dialog = DbManagerDialog()
    try:
        dialog.customers_table.horizontalHeader().resizeSection(1, 690)
        dialog.accept()                     # кнопка «Закрыть» / Esc → done()
        assert stored_widths(dialog.customers_table,
                             ORGANIZATIONS_WIDTHS_KEY)[1] == 690
    finally:
        dialog.deleteLater()


def test_db_manager_dialog_saves_on_close(qapp, isolated_db):
    """Крестик диалога тоже сохраняет раскладку."""
    from ui.db_manager_dialog import DbManagerDialog, DRIVERS_WIDTHS_KEY

    settings = _settings()
    settings.remove(DRIVERS_WIDTHS_KEY)
    settings.sync()

    dialog = DbManagerDialog()
    try:
        dialog.drivers_table.horizontalHeader().resizeSection(1, 333)
        dialog.close()
        assert stored_widths(dialog.drivers_table, DRIVERS_WIDTHS_KEY)[1] == 333
    finally:
        dialog.deleteLater()


def test_db_manager_widths_survive_reopen(qapp, isolated_db):
    """Живой сценарий: растянули «Наименование» — новый диалог видит ширину."""
    from ui.db_manager_dialog import DbManagerDialog, ORGANIZATIONS_WIDTHS_KEY

    settings = _settings()
    settings.remove(ORGANIZATIONS_WIDTHS_KEY)
    settings.sync()

    first = DbManagerDialog()
    first.customers_table.horizontalHeader().resizeSection(1, 640)
    first.accept()                          # без ожидания таймера
    first.deleteLater()
    wait(50)

    second = DbManagerDialog()
    try:
        assert second.customers_table.horizontalHeader().sectionSize(1) == 640
    finally:
        second.deleteLater()


def test_main_window_saves_all_widths_on_force_close(qapp, isolated_db):
    """`force_close()` окна «Экспедиторство» пишет раскладку всех таблиц."""
    from ui.main_window import MainWindow
    from ui.tabs.contract_tab import LOADINGS_WIDTHS_KEY
    from ui.tabs.vehicles_tab import WIDTHS_STORAGE_KEY

    settings = _settings()
    for key in (WIDTHS_STORAGE_KEY, LOADINGS_WIDTHS_KEY):
        settings.remove(key)
    settings.sync()

    window = MainWindow()
    window.show()
    try:
        window.vehicles_tab.table.horizontalHeader().resizeSection(1, 511)
        # Колонка «Адрес» у погрузок тянется (Interactive); соседние
        # заданы режимами contents/stretch, и Qt считает их сам.
        loadings = window.contract_tab.loadings_table
        loadings.horizontalHeader().resizeSection(1, 422)
        window.force_close()                # сразу, без ожидания таймера

        assert stored_widths(window.vehicles_tab.table,
                             WIDTHS_STORAGE_KEY)[1] == 511
        assert LOADINGS_WIDTHS_KEY in _settings().allKeys()
    finally:
        window.deleteLater()


def test_main_window_hide_saves_widths(qapp, isolated_db):
    """Закрытие крестиком (окно прячется) тоже сохраняет раскладку."""
    from ui.main_window import MainWindow
    from ui.tabs.vehicles_tab import WIDTHS_STORAGE_KEY

    settings = _settings()
    settings.remove(WIDTHS_STORAGE_KEY)
    settings.sync()

    window = MainWindow()
    window.show()
    try:
        window.vehicles_tab.table.horizontalHeader().resizeSection(2, 199)
        window.close()                      # крестик: окно прячется
        assert stored_widths(window.vehicles_tab.table,
                             WIDTHS_STORAGE_KEY)[2] == 199
    finally:
        window.force_close()
        window.deleteLater()


def test_flush_widths_saver(qapp):
    """`flush_widths_saver` пишет раскладку немедленно."""
    table = make_table()
    setup_point_table(table, [(0, MODE_FIXED, 100), (1, MODE_FIXED, 100)],
                      storage_key=KEY)
    table.horizontalHeader().resizeSection(1, 280)

    assert flush_widths_saver(table) is True
    assert stored_widths(table, KEY)[1] == 280
    table.deleteLater()


def test_flush_widths_saver_without_saver(qapp):
    """У таблицы без помощника записи нет — и падать нечему."""
    table = make_table()
    assert flush_widths_saver(table) is False
    table.deleteLater()


# ─────────────────────────────────────────────────────────────
# 3. Уничтожение таблицы
# ─────────────────────────────────────────────────────────────

def test_widths_saver_flushes_on_destroyed(qapp):
    """
    Таблица уничтожена до срабатывания таймера — ширины всё равно записаны.

    Читать ширины у разрушаемой таблицы нельзя (сигнал `destroyed` приходит,
    когда виджет уже разбирается), поэтому помощник пишет их из кэша,
    снятого при правке раскладки.
    """
    table = make_table()
    setup_point_table(table, [(0, MODE_FIXED, 100), (1, MODE_FIXED, 100)],
                      storage_key=KEY)
    saver = table._widths_saver
    assert isinstance(saver, WidthsSaver)

    table.horizontalHeader().resizeSection(1, 321)
    assert saver.cached_widths[:2] == [100, 321]

    table.deleteLater()                     # таймер не успевает сработать
    wait(150)

    assert stored_widths(None, KEY)[:2] == [100, 321]


def test_widths_saver_caches_on_resize(qapp):
    """Кэш ширин обновляется при каждой правке раскладки."""
    table = make_table()
    setup_point_table(table, [(0, MODE_FIXED, 100), (1, MODE_FIXED, 100)],
                      storage_key=KEY)
    saver = table._widths_saver

    table.horizontalHeader().resizeSection(0, 150)
    assert saver.cached_widths[0] == 150
    table.horizontalHeader().resizeSection(1, 250)
    assert saver.cached_widths[:2] == [150, 250]
    table.deleteLater()


def test_widths_saver_without_key_does_not_write_on_destroy(qapp):
    """Без ключа хранилища уничтожение таблицы ничего не пишет."""
    table = make_table()
    setup_point_table(table, [(0, MODE_FIXED, 100)], storage_key="")
    table.horizontalHeader().resizeSection(0, 300)
    table.deleteLater()
    wait(100)

    settings = _settings()
    assert settings.value(KEY) is None


# ─────────────────────────────────────────────────────────────
# 4. save_all_column_widths / restore_all_column_widths
# ─────────────────────────────────────────────────────────────

def test_tables_of_finds_nested_tables(qapp):
    """Таблицы находятся рекурсивно, вместе с деревьями."""
    root = QWidget()
    layout = QVBoxLayout(root)
    table = QTableWidget(1, 2)
    tree = QTreeWidget()
    layout.addWidget(table)
    layout.addWidget(tree)

    found = tables_of(root)
    assert table in found
    assert tree in found
    root.deleteLater()


def test_save_all_and_restore_all(qapp):
    """Раскладка всех таблиц окна: сохранили и восстановили."""
    root = QWidget()
    layout = QVBoxLayout(root)
    first = QTableWidget(1, 2)
    second = QTableWidget(1, 2)
    layout.addWidget(first)
    layout.addWidget(second)

    key_first = KEY + "/first"
    key_second = KEY + "/second"
    setup_point_table(first, [(0, MODE_FIXED, 100), (1, MODE_FIXED, 100)],
                      storage_key=key_first)
    setup_point_table(second, [(0, MODE_FIXED, 100), (1, MODE_FIXED, 100)],
                      storage_key=key_second)
    first.horizontalHeader().resizeSection(1, 210)
    second.horizontalHeader().resizeSection(0, 310)

    assert save_all_column_widths(root) == 2
    assert stored_widths(first, key_first)[1] == 210
    assert stored_widths(second, key_second)[0] == 310

    # Новое окно с такими же таблицами получает ту же раскладку.
    other = QWidget()
    other_layout = QVBoxLayout(other)
    first_copy = QTableWidget(1, 2)
    second_copy = QTableWidget(1, 2)
    other_layout.addWidget(first_copy)
    other_layout.addWidget(second_copy)
    setup_point_table(first_copy, [(0, MODE_FIXED, 100), (1, MODE_FIXED, 100)],
                      storage_key=key_first)
    setup_point_table(second_copy, [(0, MODE_FIXED, 100), (1, MODE_FIXED, 100)],
                      storage_key=key_second)
    restore_all_column_widths(other)
    assert first_copy.horizontalHeader().sectionSize(1) == 210
    assert second_copy.horizontalHeader().sectionSize(0) == 310

    settings = _settings()
    for key in (key_first, key_second):
        settings.remove(key)
    settings.sync()
    root.deleteLater()
    other.deleteLater()


def test_save_all_skips_tables_without_key(qapp):
    """Таблицы без ключа хранилища пропускаются."""
    root = QWidget()
    layout = QVBoxLayout(root)
    plain = QTableWidget(1, 2)
    layout.addWidget(plain)

    assert save_all_column_widths(root) == 0
    root.deleteLater()


def test_width_storage_key(qapp):
    """Ключ хранилища читается у таблицы, а у чужого объекта его нет."""
    table = make_table()
    setup_point_table(table, [(0, MODE_FIXED, 100)], storage_key=KEY)
    assert width_storage_key(table) == KEY
    assert width_storage_key(QWidget()) == ""
    table.deleteLater()


def test_save_all_with_none():
    """Ничего не передали — ничего не сохраняем и не падаем."""
    assert save_all_column_widths(None) == 0
    assert restore_all_column_widths(None) == 0
