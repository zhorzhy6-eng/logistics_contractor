#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты диалога выбора типа договора (ui/contract_picker.py, шаг 5).

Проверяют состав и порядок пунктов выпадающего списка, соответствие пунктов
значениям ContractType, результат выбора и поведение кнопок. Qt поднимается
в offscreen-режиме: окна не показываются, тесты не зависят от дисплея.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QDialog  # noqa: E402

from core.contracts.contract_types import ContractType  # noqa: E402
from ui.contract_picker import (  # noqa: E402
    DEFAULT_PICKER_TYPE,
    PICKER_ORDER,
    ContractPickerDialog,
    picker_items,
    picker_title,
)

#: Ожидаемый порядок пунктов — из задания, а не из реестра (там алфавит).
EXPECTED_ORDER = [
    ("perevozka", "Экспедиторство"),
    ("formika", "Формика"),
    ("logistiks_rus", "Логистикс Рус"),
    ("arenda_ts", "Разовая аренда"),
    ("zayavka_excel", "Хавалы"),
]


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dialog(qt_app):
    dlg = ContractPickerDialog()
    yield dlg
    dlg.deleteLater()


# ─────────────────────────────────────────────────────────────
# Состав и порядок пунктов
# ─────────────────────────────────────────────────────────────

def test_picker_has_five_items_in_required_order(dialog):
    assert dialog.combo.count() == 5

    actual = [
        (dialog.combo.itemData(index), dialog.combo.itemText(index))
        for index in range(dialog.combo.count())
    ]
    assert actual == EXPECTED_ORDER


def test_label_and_window_title(dialog):
    assert dialog.label.text() == "Выбрать"
    assert dialog.windowTitle() == "Выбор типа договора"


def test_picker_keys_are_contract_types():
    values = {member.value for member in ContractType}
    for key, _title in PICKER_ORDER:
        assert key in values, f"{key!r} нет в ContractType"


def test_picker_excludes_service_type():
    keys = [key for key, _title in PICKER_ORDER]
    assert "expediciya" not in keys


def test_picker_order_constant_matches_expected():
    assert list(PICKER_ORDER) == EXPECTED_ORDER
    assert picker_items() == EXPECTED_ORDER


# ─────────────────────────────────────────────────────────────
# Выбор типа
# ─────────────────────────────────────────────────────────────

def test_default_selection_is_perevozka(dialog):
    assert DEFAULT_PICKER_TYPE == "perevozka"
    assert dialog.combo.currentIndex() == 0
    assert dialog.selected_type() == "perevozka"
    assert dialog.selected_title() == "Экспедиторство"


def test_selected_type_follows_combo_selection(dialog):
    for index, (key, title) in enumerate(EXPECTED_ORDER):
        dialog.combo.setCurrentIndex(index)
        assert dialog.selected_type() == key
        assert dialog.selected_title() == title


def test_select_type_programmatically(dialog):
    assert dialog.select_type("arenda_ts") is True
    assert dialog.selected_type() == "arenda_ts"

    # Служебного типа в списке нет — выбор не меняется
    assert dialog.select_type("expediciya") is False
    assert dialog.selected_type() == "arenda_ts"


def test_every_item_returns_its_key(dialog):
    for index, (key, _title) in enumerate(EXPECTED_ORDER):
        assert dialog.select_type(key) is True
        assert dialog.selected_type() == key
        assert dialog.combo.currentIndex() == index


# ─────────────────────────────────────────────────────────────
# Кнопки
# ─────────────────────────────────────────────────────────────

def test_open_button_accepts_dialog(dialog):
    dialog.select_type("formika")
    dialog.btn_open.click()
    assert dialog.result() == QDialog.Accepted


def test_cancel_button_rejects_dialog(dialog):
    dialog.btn_cancel.click()
    assert dialog.result() == QDialog.Rejected


def test_buttons_are_themed(dialog):
    assert dialog.btn_open.objectName() == "accent"
    assert dialog.btn_cancel.objectName() == "secondary"


# ─────────────────────────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────────────────────────

def test_picker_title_helper():
    assert picker_title("perevozka") == "Экспедиторство"
    assert picker_title("formika") == "Формика"
    assert picker_title("zayavka_excel") == "Хавалы"
    # Неизвестный ключ возвращается как есть — окно-заглушка не падает
    assert picker_title("unknown") == "unknown"
    assert picker_title("unknown_type") == "unknown_type"
    assert picker_title("") == ""


# ─────────────────────────────────────────────────────────────
# Порядок пунктов вынесен в core (ЭТАП 2C)
#
# Раньше PICKER_ORDER жил в этом же модуле. Теперь он нужен и селектору
# типа в шапке окна (ui/controls/contract_type_selector.py), поэтому
# переехал в core/contracts/picker_order.py, а ui.contract_picker только
# реэкспортирует имена — старые импорты продолжают работать.
# ─────────────────────────────────────────────────────────────

def test_core_picker_order_has_five_items_in_required_order():
    from core.contracts.picker_order import PICKER_ORDER as core_order

    assert len(core_order) == 5
    assert [key for key, _title in core_order] == [k for k, _t in EXPECTED_ORDER]
    assert [title for _key, title in core_order] == [t for _k, t in EXPECTED_ORDER]


def test_core_picker_title_helper():
    from core.contracts.picker_order import picker_title as core_title

    assert core_title("formika") == "Формика"
    assert core_title("unknown") == "unknown"
    assert core_title("") == ""


def test_core_picker_titles_map():
    from core.contracts.picker_order import picker_titles

    titles = picker_titles()
    assert titles["perevozka"] == "Экспедиторство"
    assert titles["formika"] == "Формика"
    assert titles["logistiks_rus"] == "Логистикс Рус"
    assert titles["arenda_ts"] == "Разовая аренда"
    assert titles["zayavka_excel"] == "Хавалы"
    assert "expediciya" not in titles


def test_ui_picker_reexports_core_objects():
    """Импорт из ui.contract_picker возвращает объекты из core."""
    from core.contracts import picker_order as core_order
    from ui import contract_picker as ui_picker

    assert ui_picker.PICKER_ORDER is core_order.PICKER_ORDER
    assert ui_picker.picker_items is core_order.picker_items
    assert ui_picker.picker_title is core_order.picker_title
    assert ui_picker.DEFAULT_PICKER_TYPE == core_order.DEFAULT_PICKER_TYPE
