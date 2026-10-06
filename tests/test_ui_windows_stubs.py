#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Общие тесты окон новых типов договоров (ЭТАП 2C, шаг 2C.4).

Формика, Логистикс Рус, Разовая аренда и Хавалы — рабочие типы: вкладки
написаны, окна переведены на них (3.1.B, 3.1.C, 3.1.D, 3.1.E.B.3).
Проверяются общие для всех четырёх окон вещи: ключ типа, заголовок, состав
вкладок и сайдбара, иконки, селектор типа в шапке, сигналы окна и кнопки
действий на вкладках. Слово «stubs» в имени файла — историческое (вкладки
тогда были заглушками); переименование не делается, чтобы не ломать импорты.

Qt — в offscreen-режиме.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ui.windows.arenda_ts import ArendaTsWindow  # noqa: E402
from ui.windows.base_window import BaseContractWindow  # noqa: E402
from ui.windows.formika import FormikaWindow  # noqa: E402
from ui.windows.havaly import HavalyWindow  # noqa: E402
from ui.windows.logistiks_rus import LogistiksRusWindow  # noqa: E402

#: Окно → (ключ типа, заголовок, вкладки). Вкладки — из задания ЭТАПА 2C.
WINDOW_SPECS = [
    (
        FormikaWindow, "formika", "Формика",
        ["Заказчик", "Груз", "Маршрут", "Водитель", "ТС", "Стоимость"],
    ),
    (
        LogistiksRusWindow, "logistiks_rus", "Логистикс Рус",
        ["Заказчик", "Груз", "Маршрут", "Водитель", "ТС", "Стоимость"],
    ),
    (
        ArendaTsWindow, "arenda_ts", "Разовая аренда",
        # Семь разделов договора аренды ТС с экипажем (ЭТАП 3.1.D.B.2):
        # вкладки написаны, окно переводится на них шагом 3.1.D.B.3.
        ["Арендатор", "Арендодатель", "ТС", "Маршрут", "Груз", "Экипаж",
         "Стоимость"],
    ),
    (
        HavalyWindow, "zayavka_excel", "Хавалы",
        ["Заявка", "Груз", "Маршрут", "Водитель", "ТС", "Стоимость"],
    ),
]

SPEC_IDS = [spec[0].__name__ for spec in WINDOW_SPECS]


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_window(qt_app):
    """Фабрика окон: всё созданное закрывается по-настоящему."""
    created = []

    def _make(window_class):
        window = window_class()
        created.append(window)
        return window

    yield _make

    for window in created:
        window.force_close()


# ─────────────────────────────────────────────────────────────
# Каркас окна
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("window_class,contract_type,title,tabs",
                         WINDOW_SPECS, ids=SPEC_IDS)
def test_window_is_base_contract_window(make_window, window_class,
                                        contract_type, title, tabs):
    window = make_window(window_class)

    assert isinstance(window, BaseContractWindow)
    assert window.CONTRACT_TYPE == contract_type
    assert window.WINDOW_TITLE == title
    assert window.windowTitle() == title


@pytest.mark.parametrize("window_class,contract_type,title,tabs",
                         WINDOW_SPECS, ids=SPEC_IDS)
def test_tabs_follow_spec(make_window, window_class, contract_type, title, tabs):
    window = make_window(window_class)

    assert window.tabs.count() == len(tabs)
    assert window.tab_titles() == tabs
    assert window.side_nav.count() == len(tabs)
    assert [window.side_nav.item_text(i) for i in range(len(tabs))] == tabs


@pytest.mark.parametrize("window_class,contract_type,title,tabs",
                         WINDOW_SPECS, ids=SPEC_IDS)
def test_every_tab_has_icon(make_window, window_class, contract_type,
                            title, tabs):
    """Иконки вкладок берутся из resources/icons/tabs и реально находятся."""
    window = make_window(window_class)

    for index in range(window.tabs.count()):
        assert window.tabs.tabIcon(index).isNull() is False, (
            f"нет иконки у вкладки {window.tabs.tabText(index)!r}"
        )


@pytest.mark.parametrize("window_class,contract_type,title,tabs",
                         WINDOW_SPECS, ids=SPEC_IDS)
def test_selector_shows_own_type(make_window, window_class, contract_type,
                                 title, tabs):
    window = make_window(window_class)

    assert window.selector.current_type() == contract_type
    assert window.selector.combo.currentText() == title


@pytest.mark.parametrize("window_class,contract_type,title,tabs",
                         WINDOW_SPECS, ids=SPEC_IDS)
def test_window_has_switch_and_exit_signals(make_window, window_class,
                                            contract_type, title, tabs):
    window = make_window(window_class)

    assert hasattr(window, "switch_to_type_requested")
    assert hasattr(window, "exit_requested")
    assert window.btn_exit.text() == "Выход"
    assert window.btn_exit.parent() is not None


@pytest.mark.parametrize("window_class,contract_type,title,tabs",
                         WINDOW_SPECS, ids=SPEC_IDS)
def test_every_tab_has_action_buttons(make_window, window_class,
                                      contract_type, title, tabs):
    """
    На каждой вкладке есть панель действий: «Создать договор» и «Очистить».

    Кнопки создаёт сама вкладка (у рабочих вкладок —
    ui/tabs/base_tab.py::TabMixin._build_tab_actions), а окно только
    подключает их сигналы. Проверяется наличие и доступность кнопки
    создания: на ней держится сценарий «заполнил → создал документ».
    """
    window = make_window(window_class)

    for index in range(window.tabs.count()):
        tab = window.tabs.widget(index)
        assert hasattr(tab, "btn_create_contract")
        assert hasattr(tab, "btn_clear_form")
        assert tab.btn_create_contract.isEnabled() is True


# ─────────────────────────────────────────────────────────────
# Реэкспорт из пакетов
# ─────────────────────────────────────────────────────────────

def test_packages_reexport_window_classes():
    import ui.windows.arenda_ts as arenda_pkg
    import ui.windows.formika as formika_pkg
    import ui.windows.havaly as havaly_pkg
    import ui.windows.logistiks_rus as logistiks_pkg

    assert formika_pkg.FormikaWindow is FormikaWindow
    assert logistiks_pkg.LogistiksRusWindow is LogistiksRusWindow
    assert arenda_pkg.ArendaTsWindow is ArendaTsWindow
    assert havaly_pkg.HavalyWindow is HavalyWindow


def test_havaly_window_uses_excel_contract_type():
    """«Хавалы» в интерфейсе — это тип zayavka_excel, а не отдельный ключ."""
    assert HavalyWindow.CONTRACT_TYPE == "zayavka_excel"
    assert HavalyWindow.WINDOW_TITLE == "Хавалы"


def test_all_four_types_are_covered():
    keys = {spec[1] for spec in WINDOW_SPECS}

    assert keys == {"formika", "logistiks_rus", "arenda_ts", "zayavka_excel"}
