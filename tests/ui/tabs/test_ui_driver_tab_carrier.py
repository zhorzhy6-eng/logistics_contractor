#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Водитель»: выпадающий список перевозчиков (ШАГ «Привязка водителей
к перевозчикам», часть C).

Что проверяется:

  * у вкладки есть QComboBox перевозчиков и кнопка «🔄 Обновить список»;
  * первый пункт — «— не указан —» с itemData = None (водитель может быть
    не привязан ни к кому: у заведённых раньше привязки нет);
  * остальные пункты — из справочника carriers (itemText — наименование,
    itemData — id), мягко удалённые в список не попадают;
  * get_data() отдаёт default_carrier_id (None, если «не указан»);
  * fill_data() выбирает перевозчика по id, а неизвестный id сбрасывает
    выбор на «— не указан —»;
  * fill_data() без ключа default_carrier_id выбор НЕ трогает (зеркало
    и распознавание этого поля не несут);
  * clear() возвращает «— не указан —»;
  * кнопка обновления перечитывает справочник: перевозчика, заведённого
    после создания вкладки, видно без перезапуска.

Qt поднимается в offscreen-режиме, база — временная (`isolated_db`),
данные синтетические, ПДн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QComboBox, QPushButton  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def carrier_a(isolated_db):
    """Перевозчик ООО «Альфа»."""
    return isolated_db.save_organization(
        {"full_name": "ООО «Альфа»", "inn": "7701234567"}, is_carrier=True
    )


@pytest.fixture
def carrier_b(isolated_db):
    """Перевозчик ООО «Бета»."""
    return isolated_db.save_organization(
        {"full_name": "ООО «Бета»", "inn": "7709876543"}, is_carrier=True
    )


@pytest.fixture
def tab(qt_app, isolated_db):
    """Вкладка «Водитель» поверх временной базы."""
    from ui.tabs.driver_tab import DriverTab

    widget = DriverTab()
    yield widget
    widget.deleteLater()


def _titles(combo: QComboBox):
    return [combo.itemText(index) for index in range(combo.count())]


# ─────────────────────────────────────────────────────────────
# C.1: элементы вкладки
# ─────────────────────────────────────────────────────────────

def test_driver_tab_has_carrier_combo(tab):
    """У вкладки есть комбобокс перевозчиков и кнопка обновления списка."""
    assert isinstance(tab.carrier_combo, QComboBox)

    buttons = [
        button for button in tab.findChildren(QPushButton)
        if button.text() == "🔄 Обновить список"
    ]
    assert len(buttons) == 1, "кнопка «Обновить список» — одна"


def test_carrier_combo_lists_carriers(tab, carrier_a, carrier_b):
    """Список перевозчиков повторяет справочник."""
    tab.fill_carriers()

    titles = _titles(tab.carrier_combo)
    assert "ООО «Альфа»" in titles
    assert "ООО «Бета»" in titles

    data = [
        tab.carrier_combo.itemData(index)
        for index in range(tab.carrier_combo.count())
    ]
    assert carrier_a in data and carrier_b in data


def test_carrier_combo_first_item_is_none(tab, carrier_a):
    """Первый пункт — «— не указан —» с itemData = None."""
    from ui.tabs.driver_tab import CARRIER_NONE_TITLE

    tab.fill_carriers()

    assert tab.carrier_combo.itemText(0) == CARRIER_NONE_TITLE
    assert tab.carrier_combo.itemData(0) is None


def test_deleted_carrier_is_not_listed(tab, isolated_db, carrier_a, carrier_b):
    """Мягко удалённого перевозчика в списке нет."""
    isolated_db.delete_organization(carrier_b, is_carrier=True)
    tab.fill_carriers()

    assert "ООО «Бета»" not in _titles(tab.carrier_combo)


def test_refresh_button_rereads_directory(tab, isolated_db):
    """«🔄 Обновить список» видит перевозчика, заведённого после открытия."""
    tab.fill_carriers()
    assert tab.carrier_combo.count() == 1, "справочник на старте пуст"

    isolated_db.save_organization({"full_name": "ООО «Гамма»"}, is_carrier=True)
    tab.btn_refresh_carriers.click()

    assert "ООО «Гамма»" in _titles(tab.carrier_combo)


def test_refresh_keeps_selection(tab, isolated_db, carrier_a, carrier_b):
    """Обновление списка не сбрасывает уже выбранного перевозчика."""
    tab.fill_carriers()
    tab._select_carrier(carrier_b)

    isolated_db.save_organization({"full_name": "ООО «Гамма»"}, is_carrier=True)
    tab.fill_carriers()

    assert tab.carrier_combo.currentData() == carrier_b


# ─────────────────────────────────────────────────────────────
# C.2: get_data
# ─────────────────────────────────────────────────────────────

def test_get_data_returns_default_carrier_id(tab, carrier_a):
    """Выбран перевозчик — get_data() отдаёт его id."""
    # Вкладка создаётся раньше перевозчика (фикстуры по порядку), поэтому
    # список перечитываем — как кнопкой «Обновить список» в работе.
    tab.fill_carriers()
    tab._select_carrier(carrier_a)

    assert tab.get_data()["default_carrier_id"] == carrier_a


def test_get_data_returns_none_when_not_selected(tab, carrier_a):
    """«— не указан —» — в данные уходит None, а не 0 и не пустая строка."""
    tab.fill_carriers()

    assert tab.carrier_combo.currentIndex() == 0
    assert tab.get_data()["default_carrier_id"] is None


def test_get_data_key_is_always_present(tab):
    """Ключ есть всегда: договор и база ждут его и у водителя без привязки."""
    assert "default_carrier_id" in tab.get_data()


# ─────────────────────────────────────────────────────────────
# C.3–C.4: fill_data / clear
# ─────────────────────────────────────────────────────────────

def test_fill_data_selects_carrier(tab, carrier_a, carrier_b):
    """fill_data выбирает перевозчика по id записи справочника."""
    tab.fill_carriers()
    tab.fill_data({"full_name": "Иванов Иван Иванович", "default_carrier_id": carrier_b})

    assert tab.carrier_combo.currentData() == carrier_b
    assert tab.carrier_combo.currentText() == "ООО «Бета»"
    assert tab.get_data()["default_carrier_id"] == carrier_b


def test_fill_data_unknown_carrier_resets_to_none(tab, carrier_a):
    """Неизвестный id (запись убрана из справочника) — «— не указан —»."""
    tab.fill_carriers()
    tab._select_carrier(carrier_a)

    tab.fill_data({"default_carrier_id": 999999})

    assert tab.carrier_combo.currentIndex() == 0
    assert tab.get_data()["default_carrier_id"] is None


def test_fill_data_without_key_keeps_selection(tab, carrier_a):
    """Ключа нет (распознавание, зеркало) — выбор не сбрасывается."""
    tab.fill_carriers()
    tab._select_carrier(carrier_a)

    tab.fill_data({"full_name": "Петров Пётр Петрович"})

    assert tab.carrier_combo.currentData() == carrier_a


def test_fill_data_accepts_string_id(tab, carrier_a):
    """id строкой (через промежуточные словари) тоже выбирается."""
    tab.fill_carriers()
    tab.fill_data({"default_carrier_id": str(carrier_a)})

    assert tab.carrier_combo.currentData() == carrier_a


def test_clear_resets_to_none(tab, carrier_a):
    """clear() возвращает «— не указан —»."""
    tab.fill_carriers()
    tab._select_carrier(carrier_a)

    tab.clear()

    assert tab.carrier_combo.currentIndex() == 0
    assert tab.get_data()["default_carrier_id"] is None


def test_load_driver_record_fills_carrier(tab, isolated_db, carrier_a):
    """
    Загрузка водителя из справочника подставляет его перевозчика.

    Так работает путь «Менеджер базы → Загрузить в форму»:
    `_load_driver_from_db` передаёт в вкладку запись целиком, а
    `default_carrier_id` — обычная её колонка.
    """
    driver_id = isolated_db.save_driver({
        "full_name": "Иванов Иван Иванович",
        "default_carrier_id": carrier_a,
    })
    record = isolated_db.load_driver(driver_id)

    tab.clear()
    tab.fill_carriers()
    tab.fill_data(record)

    assert tab.carrier_combo.currentData() == carrier_a
    assert tab.full_name.text() == "Иванов Иван Иванович"
