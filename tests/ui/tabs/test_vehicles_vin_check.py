#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Подсветка ненадёжного VIN в таблице «Перевозимые ТС» (часть E.4).

Правило: VIN — ровно 17 символов. Всё, что короче или длиннее, —
ненадёжно: часть символов потерялась при чтении документа или при склейке
строк, и печатать такое значение в договор нельзя.

Проверяется:
  * ячейка VIN получает жёлтый фон темы и подсказку с длиной значения;
  * ячейка с нормальным VIN фон не получает;
  * пустая ячейка не подсвечивается (VIN ещё не введён);
  * подпись «ненадёжных VIN: N» показывает счёт и прячется, когда всё в порядке;
  * правка ячейки оператором пересчитывает подсветку сразу;
  * импорт документов вызывает проверку после переноса значений.

ЛОВУШКА ОКРУЖЕНИЯ. В этой сборке PyQt5 (Python 3.14 + PyQt5 5.15.11) запись
роли ToolTip и роли фона в `QTableWidgetItem` иногда не читается обратно:
`item.setToolTip("X")` возвращает пустую строку у элемента, который лежит
в таблице вкладки. Запись при этом ДОХОДИТ до Qt — реальная подсказка в
приложении показывается. Поэтому проверки ниже смотрят на ВЫЗОВЫ
`setBackground` / `setToolTip` (через подмену методов у элемента), а не на
чтение значения обратно.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ui import theme  # noqa: E402
from ui.tabs.vehicles_tab import VIN_LENGTH, VehiclesTab  # noqa: E402
from ui.widgets.table_helpers import WARNING_TOOLTIP_PREFIX  # noqa: E402

VIN_OK = "EC37CUSM4TC008647"
VIN_SHORT = "EC37CUSM4TC0086"          # 16 символов
VIN_LONG = "EC37CUSM4TC0086477"        # 18 символов


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qt_app):
    return VehiclesTab()


def _vin_item(tab, row=0):
    return tab.table.item(row, tab.COL_VIN)


class _ItemSpy:
    """Запоминает, что вкладка ставит ячейке: фон и подсказку."""

    def __init__(self, item):
        self.item = item
        self.backgrounds = []
        self.tooltips = []
        self._real_background = item.setBackground
        self._real_tooltip = item.setToolTip

    def __enter__(self):
        def background(brush):
            self.backgrounds.append(brush.color().name().lower())
            return self._real_background(brush)

        def tooltip(text):
            self.tooltips.append(text)
            return self._real_tooltip(text)

        self.item.setBackground = background
        self.item.setToolTip = tooltip
        return self

    def __exit__(self, *exc):
        self.item.setBackground = self._real_background
        self.item.setToolTip = self._real_tooltip
        return False

    @property
    def last_background(self):
        return self.backgrounds[-1] if self.backgrounds else ""

    @property
    def last_tooltip(self):
        return self.tooltips[-1] if self.tooltips else ""


# ─────────────────────────────────────────────────────────────
# Проверка значения
# ─────────────────────────────────────────────────────────────

def test_vin_length_constant_is_seventeen():
    assert VIN_LENGTH == 17


@pytest.mark.parametrize("vin, expected", [
    (VIN_OK, True),
    ("  " + VIN_OK + "  ", True),      # пробелы вокруг не мешают
    (VIN_SHORT, False),
    (VIN_LONG, False),
    ("", False),
    (None, False),
])
def test_vin_is_reliable(vin, expected):
    assert VehiclesTab.vin_is_reliable(vin) is expected


# ─────────────────────────────────────────────────────────────
# Подсветка ячейки
# ─────────────────────────────────────────────────────────────

def test_short_vin_is_highlighted(tab):
    tab.fill_data([{"vin": VIN_SHORT, "brand_model": "JETOUR T1"}])
    item = _vin_item(tab)

    with _ItemSpy(item) as spy:
        tab.refresh_vin_warnings()

    assert spy.last_background == theme.warning_cell_color().name().lower()
    assert str(VIN_LENGTH) in spy.last_tooltip
    assert str(len(VIN_SHORT)) in spy.last_tooltip


def test_long_vin_gets_warning_tooltip(tab):
    tab.fill_data([{"vin": VIN_LONG, "brand_model": "JETOUR T1"}])
    item = _vin_item(tab)

    with _ItemSpy(item) as spy:
        tab.refresh_vin_warnings()

    assert spy.last_background == theme.warning_cell_color().name().lower()
    assert spy.last_tooltip == (
        f"{WARNING_TOOLTIP_PREFIX} VIN {len(VIN_LONG)} символов вместо "
        f"{VIN_LENGTH} — проверьте по документу"
    )


def test_correct_vin_is_not_highlighted(tab):
    tab.fill_data([{"vin": VIN_OK, "brand_model": "JETOUR T1"}])
    item = _vin_item(tab)

    with _ItemSpy(item) as spy:
        tab.refresh_vin_warnings()

    assert spy.last_background == "#000000"      # пустая кисть: фон не задан
    assert spy.last_tooltip == ""
    assert tab.vin_warning.text() == ""


def test_empty_vin_is_not_highlighted(tab):
    """Новая строка: VIN ещё не введён — подсвечивать нечего."""
    tab.fill_data([{"brand_model": "JETOUR T1"}])
    item = _vin_item(tab)

    with _ItemSpy(item) as spy:
        tab.refresh_vin_warnings()

    assert spy.last_background == "#000000"
    assert spy.last_tooltip == ""


def test_highlight_is_removed_when_vin_is_fixed(tab):
    """Оператор дописал VIN до 17 символов — подсветка снимается."""
    tab.fill_data([{"vin": VIN_SHORT, "brand_model": "JETOUR T1"}])
    assert tab.vin_warning.text() != ""

    item = _vin_item(tab)
    with _ItemSpy(item) as spy:
        item.setText(VIN_OK)

    assert spy.last_background == "#000000"
    assert spy.last_tooltip == ""
    assert tab.vin_warning.text() == ""


def test_highlight_appears_when_vin_is_broken(tab):
    """Оператор стёр символ — подсветка появляется сразу, без перезаполнения."""
    tab.fill_data([{"vin": VIN_OK, "brand_model": "JETOUR T1"}])
    assert tab.vin_warning.text() == ""

    item = _vin_item(tab)
    with _ItemSpy(item) as spy:
        item.setText(VIN_SHORT)

    assert spy.last_background == theme.warning_cell_color().name().lower()
    assert tab.vin_warning.text() != ""


# ─────────────────────────────────────────────────────────────
# Подпись «ненадёжных VIN: N»
# ─────────────────────────────────────────────────────────────

def test_warning_label_counts_bad_vins(tab):
    tab.fill_data([
        {"vin": VIN_OK, "brand_model": "JETOUR T1"},
        {"vin": VIN_SHORT, "brand_model": "GAC S9"},
        {"vin": VIN_LONG, "brand_model": "GAC GS8IIFL"},
    ])

    assert "2" in tab.vin_warning.text()
    assert str(VIN_LENGTH) in tab.vin_warning.text()


def test_warning_label_hidden_when_all_vins_are_fine(tab):
    tab.fill_data([
        {"vin": VIN_OK, "brand_model": "JETOUR T1"},
        {"vin": "EC37LUSM8TC007923", "brand_model": "JETOUR X70PLUS"},
    ])

    assert tab.vin_warning.text() == ""
    assert tab.vin_warning.isVisible() is False


def test_warning_label_hidden_when_table_is_empty(tab):
    tab.fill_data([{"vin": VIN_SHORT, "brand_model": "JETOUR T1"}])
    assert tab.vin_warning.text() != ""

    tab.clear()

    assert tab.vin_warning.text() == ""
    assert tab.table.rowCount() == 0


def test_removing_row_updates_warning(tab):
    tab.fill_data([
        {"vin": VIN_SHORT, "brand_model": "JETOUR T1"},
        {"vin": VIN_OK, "brand_model": "GAC S9"},
    ])
    assert "1" in tab.vin_warning.text()

    tab.table.removeRow(0)
    tab.refresh_vin_warnings()

    assert tab.vin_warning.text() == ""


# ─────────────────────────────────────────────────────────────
# Импорт документов
# ─────────────────────────────────────────────────────────────

def test_imported_values_are_checked(qt_app, monkeypatch):
    """
    Импорт документов заканчивается проверкой VIN.

    Значения приходят по одному (`set_field`), поэтому проверка вызывается
    один раз в конце — иначе она пересчитывалась бы на каждое поле.

    Проверку значения на входе в импорт (`valid_value`) здесь отключаем:
    она VIN короче 17 символов и так не пропускает, а проверить надо именно
    подсветку уже перенесённого значения.
    """
    from PyQt5.QtWidgets import QMessageBox

    import ui.document_import_dialog as import_dialog
    from ui.document_import_dialog import apply_approved
    from ui.main_window import MainWindow

    monkeypatch.setattr(import_dialog, "valid_value", lambda key, value: True)
    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    window = MainWindow()
    window.vehicles_tab.fill_data([{"vin": VIN_OK, "brand_model": "JETOUR T1"}])

    apply_approved(window, [("vehicles", 0, "vin", VIN_SHORT)])

    assert window.vehicles_tab.get_field(0, "vin") == VIN_SHORT
    assert window.vehicles_tab.vin_warning.text() != ""

    item = window.vehicles_tab.table.item(0, window.vehicles_tab.COL_VIN)
    with _ItemSpy(item) as spy:
        window.vehicles_tab.refresh_vin_warnings()

    assert spy.last_background == theme.warning_cell_color().name().lower()


# ─────────────────────────────────────────────────────────────
# Цвет берётся из темы
# ─────────────────────────────────────────────────────────────

def test_warning_color_comes_from_palette():
    """Фон подсветки — из палитры темы, а не жёстко заданный."""
    for name in theme.theme_names():
        color = theme.warning_cell_color(name)
        assert color.isValid()
        assert color.name().lower() != "#000000"


def test_warning_qss_uses_theme_border():
    for name in theme.theme_names():
        assert theme.palette(name)["warn_border"] in theme.warning_text_qss(name)
