#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Дерево перевозчиков в «Менеджере базы» (ШАГ «Дерево перевозчиков +
двусторонняя загрузка водитель ↔ перевозчик», части B и C).

Вкладка «Перевозчики» — дерево: верхний уровень перевозчики, дети — их
водители (`drivers.default_carrier_id`). Что проверяется:

  * структура: перевозчики сверху, водители — только под своим перевозчиком;
  * мягкое удаление: по умолчанию удалённых нет ни на одном уровне,
    с «Показывать удалённые» они появляются и помечены;
  * двойной клик: перевозчик — только перевозчик (вкладка «Водитель» не
    трогается), водитель — водитель И его перевозчик;
  * стрелка «+»/«−» раскрывает узел и НЕ подтягивает запись в форму;
  * поиск фильтрует оба уровня, пустой поиск возвращает всё;
  * сортировка по колонкам работает на обоих уровнях;
  * «✏ Редактировать», «🗑 Удалить», «♻ Восстановить» работают с тем, что
    выбрано: перевозчик — с перевозчиком, водитель — с водителем;
  * состояние раскрытия переживает перестройку дерева.

Двойной клик в тестах подаётся сигналом `itemDoubleClicked`: синтетический
`QTest.mouseDClick` в offscreen-режиме этот сигнал не порождает вовсе
(проверено пробой), а щелчок по стрелке проверяется настоящим мышиным
кликом — Qt обрабатывает его сам, без нашего кода.

Qt поднимается в offscreen-режиме, база — временная (`isolated_db`),
данные синтетические, Пдн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtCore import QPoint, Qt  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QAbstractItemView, QDialog, QMessageBox, QTableWidget,
    QTreeWidget,
)

DRIVER_NAME = "Иванов Иван Иванович"
GALUSHKIN_NAME = "Галушкин Петр Михайлович"
PETROV_NAME = "Петров Пётр Петрович"

CARRIER_A_NAME = "ООО «Фас Транс»"
CARRIER_B_NAME = "ООО «Ромашка»"


class FormStub:
    """
    Заглушка формы MainWindow: обработчики диалога её заполняют.

    Повторяет правило шага: водитель приходит со своей привязкой, и
    перевозчика по ней подтягивает принимающая сторона (в программе —
    `MainWindow._load_driver_from_db`).
    """

    def __init__(self, db):
        self._db = db
        self.driver = {}
        self.carrier = {}

    def on_load_driver(self, driver):
        self.driver = dict(driver)
        carrier_id = driver.get("default_carrier_id")
        if carrier_id:
            self.carrier = self._db.load_organization_by_id(
                carrier_id, is_carrier=True
            ) or {}

    def on_load_carrier(self, carrier):
        self.carrier = dict(carrier)


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def quiet_dialogs(monkeypatch):
    """
    Модальные окна не показываем: offscreen их не переживает.

    `information` в списке обязателен: без подмены настоящее модальное окно
    роняет прогон access violation (а падает при этом не тот тест, который
    его открыл).
    """
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)


@pytest.fixture
def manager(qt_app, isolated_db, quiet_dialogs):
    """Менеджер базы поверх временной базы."""
    from ui.db_manager_dialog import DbManagerDialog

    dialog = DbManagerDialog()
    yield dialog
    dialog.close()


@pytest.fixture
def form_stub(isolated_db):
    return FormStub(isolated_db)


@pytest.fixture
def form_manager(qt_app, isolated_db, quiet_dialogs, form_stub):
    """Менеджер базы с обработчиками формы (как у MainWindow)."""
    from ui.db_manager_dialog import DbManagerDialog

    dialog = DbManagerDialog(
        on_load_driver=form_stub.on_load_driver,
        on_load_carrier=form_stub.on_load_carrier,
    )
    yield dialog
    dialog.close()


@pytest.fixture
def carrier_a(isolated_db):
    return isolated_db.save_organization(
        {
            "full_name": CARRIER_A_NAME,
            "inn": "7701234567",
            "kpp": "770101001",
            "director_name": PETROV_NAME,
        },
        is_carrier=True,
    )


@pytest.fixture
def carrier_b(isolated_db):
    return isolated_db.save_organization(
        {"full_name": CARRIER_B_NAME, "inn": "7707654321"}, is_carrier=True
    )


# ─────────────────────────────────────────────────────────────
# Помощники
# ─────────────────────────────────────────────────────────────

def _tree(manager):
    return manager.carriers_tree


def _top_items(manager):
    tree = _tree(manager)
    return [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]


def _top_names(manager):
    from ui.db_manager_dialog import CARRIER_COL_NAME, CARRIER_NODE_PREFIX

    return [
        item.text(CARRIER_COL_NAME).removeprefix(CARRIER_NODE_PREFIX)
        for item in _top_items(manager)
    ]


def _top_by_name(manager, name: str):
    for item in _top_items(manager):
        if name in item.text(0):
            return item
    raise AssertionError(f"перевозчик {name!r} не найден в дереве")


def _children(item):
    return [item.child(index) for index in range(item.childCount())]


def _child_names(item):
    from ui.db_manager_dialog import DRIVER_NODE_PREFIX

    return [
        child.text(0).removeprefix(DRIVER_NODE_PREFIX) for child in _children(item)
    ]


def _driver_child(item, full_name: str):
    for child in _children(item):
        if full_name in child.text(0):
            return child
    raise AssertionError(f"водитель {full_name!r} не найден у {item.text(0)!r}")


def _select(manager, item):
    _tree(manager).setCurrentItem(item)


def _double_click(manager, item):
    """Двойной клик по узлу — через сигнал, который шлёт QTreeWidget."""
    _tree(manager).itemDoubleClicked.emit(item, 0)


def _add_driver(isolated_db, full_name, carrier_id=None, **extra):
    payload = {"full_name": full_name, "phone": "+7 (999) 111-22-33"}
    if carrier_id is not None:
        payload["default_carrier_id"] = carrier_id
    payload.update(extra)
    return isolated_db.save_driver(payload)


def _search(manager, text):
    """Ввод в поиске вкладки «Перевозчики» и немедленное применение."""
    manager._schedule_carrier_tree_filter(text)
    manager._apply_carrier_tree_filter()


# ─────────────────────────────────────────────────────────────
# B.1–B.3: структура дерева
# ─────────────────────────────────────────────────────────────

def test_carrier_tab_is_tree_widget(manager):
    """Вкладка «Перевозчики» — дерево; таблицы на ней больше нет."""
    assert isinstance(_tree(manager), QTreeWidget)
    assert manager.carriers_tab.findChildren(QTableWidget) == []
    assert _tree(manager) in manager.carriers_tab.findChildren(QTreeWidget)


def test_tree_view_options_are_set(manager):
    """Раскрытие, чередование строк, выделение строк, сортировка, шапка."""
    tree = _tree(manager)
    from ui.db_manager_dialog import CARRIER_TREE_HEADERS

    assert tree.rootIsDecorated() is True
    assert tree.alternatingRowColors() is True
    assert tree.selectionBehavior() == QAbstractItemView.SelectRows
    assert tree.isSortingEnabled() is True
    assert tree.header().sectionsMovable() is True
    assert tree.columnCount() == len(CARRIER_TREE_HEADERS)
    assert [
        tree.headerItem().text(column) for column in range(tree.columnCount())
    ] == list(CARRIER_TREE_HEADERS)


def test_tree_has_carriers_as_top_level_items(
    manager, isolated_db, carrier_a, carrier_b
):
    """Верхний уровень — активные перевозчики, в записи узла лежит запись."""
    from ui.db_manager_dialog import (
        CARRIER_COL_INN, CARRIER_COL_NAME, NODE_KIND_CARRIER, NODE_KIND_ROLE,
        NODE_RECORD_ROLE,
    )

    manager._load_carriers_tree()

    assert sorted(_top_names(manager)) == sorted([CARRIER_A_NAME, CARRIER_B_NAME])

    item = _top_by_name(manager, CARRIER_A_NAME)
    assert item.text(CARRIER_COL_NAME).startswith("🚛")
    assert item.text(CARRIER_COL_INN) == "7701234567"
    assert item.data(0, NODE_KIND_ROLE) == NODE_KIND_CARRIER
    assert item.data(0, NODE_RECORD_ROLE)["id"] == carrier_a


def test_tree_carrier_node_shows_requisites(manager, isolated_db, carrier_a):
    """Колонки перевозчика: наименование, ИНН, КПП, директор, статус."""
    from ui.db_manager_dialog import (
        CARRIER_COL_DIRECTOR, CARRIER_COL_KPP, CARRIER_COL_STATUS,
    )

    manager._load_carriers_tree()
    item = _top_by_name(manager, CARRIER_A_NAME)

    assert item.text(CARRIER_COL_KPP) == "770101001"
    assert item.text(CARRIER_COL_DIRECTOR) == PETROV_NAME
    assert item.text(CARRIER_COL_STATUS) == ""


def test_tree_drivers_are_children_of_carriers(
    manager, isolated_db, carrier_a
):
    """Водитель — дочерний узел своего перевозчика (вариант A разбора ТЗ)."""
    from ui.db_manager_dialog import (
        CARRIER_COL_DIRECTOR, CARRIER_COL_INN, CARRIER_COL_KPP,
        CARRIER_COL_STATUS, NODE_KIND_DRIVER, NODE_KIND_ROLE, NODE_RECORD_ROLE,
    )

    _add_driver(
        isolated_db, GALUSHKIN_NAME, carrier_a,
        birth_date="1985-03-12", phone="+7 (999) 111-22-33",
    )
    manager._load_carriers_tree()

    top = _top_by_name(manager, CARRIER_A_NAME)
    child = _driver_child(top, GALUSHKIN_NAME)

    assert child.text(0).startswith("👤")
    assert child.text(CARRIER_COL_INN) == ""
    assert child.text(CARRIER_COL_KPP) == ""
    assert child.text(CARRIER_COL_DIRECTOR) == "1985-03-12", "дата рождения"
    assert child.text(CARRIER_COL_STATUS) == "+7 (999) 111-22-33", "телефон"
    assert child.data(0, NODE_KIND_ROLE) == NODE_KIND_DRIVER
    assert child.data(0, NODE_RECORD_ROLE)["full_name"] == GALUSHKIN_NAME
    assert child.parent() is top


def test_tree_driver_without_name_shows_placeholder(manager, isolated_db, carrier_a):
    """Пустое ФИО — «(без ФИО)», а не пустая строка."""
    from ui.db_manager_dialog import DRIVER_WITHOUT_NAME

    _add_driver(isolated_db, "", carrier_a)
    manager._load_carriers_tree()

    top = _top_by_name(manager, CARRIER_A_NAME)
    assert _child_names(top) == [DRIVER_WITHOUT_NAME]


def test_tree_driver_not_shown_under_wrong_carrier(
    manager, isolated_db, carrier_a, carrier_b
):
    """Водитель виден только у своего перевозчика."""
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    manager._load_carriers_tree()

    assert _child_names(_top_by_name(manager, CARRIER_A_NAME)) == [DRIVER_NAME]
    assert _child_names(_top_by_name(manager, CARRIER_B_NAME)) == []


def test_tree_carrier_without_drivers_has_no_children(
    manager, isolated_db, carrier_b
):
    """Нет водителей — нет детей (и стрелки «+» у узла)."""
    manager._load_carriers_tree()
    top = _top_by_name(manager, CARRIER_B_NAME)

    assert top.childCount() == 0


def test_tree_carrier_with_drivers_has_children(manager, isolated_db, carrier_a):
    """Есть водители — они дети, и узел раскрывается."""
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    _add_driver(isolated_db, GALUSHKIN_NAME, carrier_a)
    manager._load_carriers_tree()

    top = _top_by_name(manager, CARRIER_A_NAME)
    assert top.childCount() == 2

    top.setExpanded(True)
    assert top.isExpanded() is True


def test_tree_driver_without_carrier_is_not_in_tree(manager, isolated_db, carrier_a):
    """Водитель без привязки в дереве не показывается (он на вкладке «Водители»)."""
    _add_driver(isolated_db, PETROV_NAME)
    manager._load_carriers_tree()

    assert _child_names(_top_by_name(manager, CARRIER_A_NAME)) == []


# ─────────────────────────────────────────────────────────────
# B.4: мягкое удаление на обоих уровнях
# ─────────────────────────────────────────────────────────────

def test_tree_soft_deleted_carrier_not_shown(
    manager, isolated_db, carrier_a, carrier_b
):
    """По умолчанию удалённого перевозчика в дереве нет."""
    isolated_db.delete_organization(carrier_b, is_carrier=True)
    manager._load_carriers_tree()

    assert _top_names(manager) == [CARRIER_A_NAME]


def test_tree_soft_deleted_driver_not_shown(manager, isolated_db, carrier_a):
    """Мягко удалённого водителя в дереве тоже нет."""
    driver_id = _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    isolated_db.delete_driver(driver_id)
    manager._load_carriers_tree()

    assert _child_names(_top_by_name(manager, CARRIER_A_NAME)) == []


def test_tree_soft_deleted_driver_shown_with_checkbox(
    manager, isolated_db, carrier_a
):
    """«Показывать удалённые»: удалённый водитель виден и помечен."""
    from ui.db_manager_dialog import (
        CARRIER_COL_STATUS, DELETED_STATUS_TITLE, NODE_RECORD_ROLE,
    )

    driver_id = _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    isolated_db.delete_driver(driver_id)

    manager.chk_deleted.setChecked(True)

    top = _top_by_name(manager, CARRIER_A_NAME)
    child = _driver_child(top, DRIVER_NAME)
    assert child.text(CARRIER_COL_STATUS) == DELETED_STATUS_TITLE
    assert child.data(0, NODE_RECORD_ROLE)["is_deleted"] == 1


def test_tree_soft_deleted_carrier_shown_with_checkbox(
    manager, isolated_db, carrier_a, carrier_b
):
    """Удалённый перевозчик с чекбоксом виден, в «Статусе» — «удалён»."""
    from ui.db_manager_dialog import CARRIER_COL_STATUS, DELETED_STATUS_TITLE

    isolated_db.delete_organization(carrier_b, is_carrier=True)

    manager.chk_deleted.setChecked(True)

    top = _top_by_name(manager, CARRIER_B_NAME)
    assert top.text(CARRIER_COL_STATUS) == DELETED_STATUS_TITLE
    assert sorted(_top_names(manager)) == sorted([CARRIER_A_NAME, CARRIER_B_NAME])


def test_deleted_records_are_coloured(manager, isolated_db, carrier_a):
    """Удалённый узел подкрашен — видно, что запись убрана из справочника."""
    from ui.db_manager_dialog import CARRIER_COL_NAME

    driver_id = _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    isolated_db.delete_driver(driver_id)

    manager.chk_deleted.setChecked(True)

    child = _driver_child(_top_by_name(manager, CARRIER_A_NAME), DRIVER_NAME)
    assert child.foreground(CARRIER_COL_NAME).color().isValid()


# ─────────────────────────────────────────────────────────────
# C.1–C.3: двойной клик
# ─────────────────────────────────────────────────────────────

def test_double_click_carrier_loads_only_carrier(
    form_manager, form_stub, isolated_db, carrier_a
):
    """Двойной клик по перевозчику: только перевозчик, водителя не трогаем."""
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    form_manager._load_carriers_tree()

    _double_click(form_manager, _top_by_name(form_manager, CARRIER_A_NAME))

    assert form_stub.carrier["full_name"] == CARRIER_A_NAME
    assert form_stub.carrier["inn"] == "7701234567"
    assert form_stub.driver == {}, "водитель подтягиваться не должен"
    assert form_manager.result() == QDialog.Accepted


def test_double_click_driver_loads_driver_and_carrier(
    form_manager, form_stub, isolated_db, carrier_a
):
    """Двойной клик по водителю: и водитель, и его перевозчик."""
    _add_driver(isolated_db, GALUSHKIN_NAME, carrier_a)
    form_manager._load_carriers_tree()

    top = _top_by_name(form_manager, CARRIER_A_NAME)
    _double_click(form_manager, _driver_child(top, GALUSHKIN_NAME))

    assert form_stub.driver["full_name"] == GALUSHKIN_NAME
    assert form_stub.driver["default_carrier_id"] == carrier_a
    assert form_stub.carrier["full_name"] == CARRIER_A_NAME
    assert form_manager.result() == QDialog.Accepted


def test_double_click_driver_under_carrier_loads_both(
    form_manager, form_stub, isolated_db, carrier_a, carrier_b
):
    """Водитель второго перевозчика тянет ЗА СОБОЙ второго, а не первого."""
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    _add_driver(isolated_db, GALUSHKIN_NAME, carrier_b)
    form_manager._load_carriers_tree()

    top_b = _top_by_name(form_manager, CARRIER_B_NAME)
    _double_click(form_manager, _driver_child(top_b, GALUSHKIN_NAME))

    assert form_stub.driver["full_name"] == GALUSHKIN_NAME
    assert form_stub.carrier["full_name"] == CARRIER_B_NAME
    assert form_stub.carrier["inn"] == "7707654321"


def test_double_click_empty_space_does_nothing(form_manager, form_stub):
    """Клик мимо узлов: Qt сигнала не шлёт, а обработчик молчит."""
    form_manager._on_carrier_item_double_clicked(None, 0)

    assert form_stub.driver == {}
    assert form_stub.carrier == {}


def test_expand_arrow_does_not_trigger_load(
    form_manager, form_stub, isolated_db, carrier_a
):
    """
    Стрелка «+»/«−» только раскрывает узел — запись в форму не уходит.

    Щелчок настоящий: Qt сам обрабатывает украшение узла (клик по нему не
    доходит ни до `itemDoubleClicked`, ни до нашей загрузки).
    """
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    form_manager._load_carriers_tree()
    tree = _tree(form_manager)
    top = _top_by_name(form_manager, CARRIER_A_NAME)

    tree.show()
    QApplication.processEvents()

    rect = tree.visualItemRect(top)
    branch_point = QPoint(
        max(2, rect.left() - tree.indentation() // 2),
        rect.top() + rect.height() // 2,
    )
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, branch_point)
    QApplication.processEvents()

    assert top.isExpanded() is True, "клик по стрелке должен раскрывать узел"
    assert form_stub.driver == {}, "раскрытие не подтягивает водителя"
    assert form_stub.carrier == {}, "раскрытие не подтягивает перевозчика"

    # Программное раскрытие/сворачивание — тоже только вид.
    tree.expandItem(top)
    tree.collapseItem(top)
    assert form_stub.driver == {} and form_stub.carrier == {}


# ─────────────────────────────────────────────────────────────
# B.5: поиск по обоим уровням
# ─────────────────────────────────────────────────────────────

def test_search_filters_carriers(manager, isolated_db, carrier_a, carrier_b):
    """Подошёл перевозчик — виден он и ВСЕ его водители."""
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    _add_driver(isolated_db, GALUSHKIN_NAME, carrier_a)

    _search(manager, "Ромашка")

    assert _top_names(manager) == [CARRIER_B_NAME]
    assert _child_names(_top_by_name(manager, CARRIER_B_NAME)) == []

    _search(manager, "фас транс")

    top = _top_by_name(manager, CARRIER_A_NAME)
    assert sorted(_child_names(top)) == sorted([DRIVER_NAME, GALUSHKIN_NAME])


def test_search_filters_drivers_and_keeps_parent(
    manager, isolated_db, carrier_a, carrier_b
):
    """Подошёл водитель — виден его перевозчик и только совпавшие водители."""
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    _add_driver(isolated_db, GALUSHKIN_NAME, carrier_a)
    _add_driver(isolated_db, PETROV_NAME, carrier_b)

    _search(manager, "Галушкин")

    assert _top_names(manager) == [CARRIER_A_NAME]
    assert _child_names(_top_by_name(manager, CARRIER_A_NAME)) == [GALUSHKIN_NAME]


def test_search_by_driver_phone(manager, isolated_db, carrier_a):
    """Поиск водителя идёт и по телефону (как на вкладке «Водители»)."""
    _add_driver(isolated_db, GALUSHKIN_NAME, carrier_a, phone="+7 (999) 777-66-55")

    _search(manager, "777-66-55")

    assert _child_names(_top_by_name(manager, CARRIER_A_NAME)) == [GALUSHKIN_NAME]


def test_search_by_carrier_inn(manager, isolated_db, carrier_a, carrier_b):
    """Поиск перевозчика идёт и по ИНН."""
    _search(manager, "7707654321")

    assert _top_names(manager) == [CARRIER_B_NAME]


def test_search_empty_shows_all(manager, isolated_db, carrier_a, carrier_b):
    """Пустой поиск возвращает оба уровня целиком."""
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    _search(manager, "Галушкин")
    assert _top_names(manager) == []

    _search(manager, "")

    assert sorted(_top_names(manager)) == sorted([CARRIER_A_NAME, CARRIER_B_NAME])
    assert _child_names(_top_by_name(manager, CARRIER_A_NAME)) == [DRIVER_NAME]


def test_search_match_inside_carrier_is_expanded(manager, isolated_db, carrier_a):
    """Совпадение внутри перевозчика раскрывается — водителя видно сразу."""
    _add_driver(isolated_db, GALUSHKIN_NAME, carrier_a)

    _search(manager, "Галушкин")

    top = _top_by_name(manager, CARRIER_A_NAME)
    assert top.isExpanded() is True


# ─────────────────────────────────────────────────────────────
# B.5 (продолжение): сортировка и раскрытие
# ─────────────────────────────────────────────────────────────

def test_sort_by_name_works(manager, isolated_db, carrier_a, carrier_b):
    """Сортировка по «Наименованию» работает на обоих уровнях."""
    _add_driver(isolated_db, GALUSHKIN_NAME, carrier_a)
    _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    manager._load_carriers_tree()
    tree = _tree(manager)

    assert _top_names(manager) == [CARRIER_B_NAME, CARRIER_A_NAME], "по алфавиту"
    top = _top_by_name(manager, CARRIER_A_NAME)
    assert _child_names(top) == [GALUSHKIN_NAME, DRIVER_NAME]

    tree.sortItems(0, Qt.DescendingOrder)

    assert _top_names(manager) == [CARRIER_A_NAME, CARRIER_B_NAME]
    assert _child_names(_top_by_name(manager, CARRIER_A_NAME)) == [
        DRIVER_NAME, GALUSHKIN_NAME,
    ]


def test_expanded_state_kept_on_rebuild(manager, isolated_db, carrier_a, carrier_b):
    """Раскрытие перевозчика переживает перестройку дерева."""
    manager._load_carriers_tree()
    top_a = _top_by_name(manager, CARRIER_A_NAME)
    top_a.setExpanded(True)

    manager._load_carriers_tree()

    assert _top_by_name(manager, CARRIER_A_NAME).isExpanded() is True
    assert _top_by_name(manager, CARRIER_B_NAME).isExpanded() is False

    _top_by_name(manager, CARRIER_A_NAME).setExpanded(False)
    manager._load_carriers_tree()

    assert _top_by_name(manager, CARRIER_A_NAME).isExpanded() is False


def test_expanded_state_kept_after_driver_edit(
    manager, isolated_db, carrier_a, monkeypatch
):
    """После правки водителя дерево пересобирается, но раскрытие не теряется."""
    driver_id = _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    manager._load_carriers_tree()
    _top_by_name(manager, CARRIER_A_NAME).setExpanded(True)

    import ui.db_manager_dialog as module

    class FakeDialog:
        def __init__(self, driver, parent=None):
            self.driver = driver

        def exec_(self):
            return QDialog.Accepted

        def get_driver_data(self):
            return {"full_name": "Сидоров Сидор Сидорович"}

        def get_vehicle_data(self):
            return {}

    monkeypatch.setattr(module, "EditDriverDialog", FakeDialog)
    manager._edit_driver_record(isolated_db.load_driver(driver_id))

    top = _top_by_name(manager, CARRIER_A_NAME)
    assert top.isExpanded() is True
    assert _child_names(top) == ["Сидоров Сидор Сидорович"]


# ─────────────────────────────────────────────────────────────
# B.7: кнопки работают с тем, что выбрано
# ─────────────────────────────────────────────────────────────

def test_edit_carrier_opens_carrier_dialog(
    manager, isolated_db, carrier_a, monkeypatch
):
    """«✏ Редактировать» на перевозчике открывает диалог перевозчика."""
    import ui.db_manager_dialog as module

    seen = {}

    class FakeDialog:
        def __init__(self, org, is_carrier=True, parent=None):
            seen["org"] = dict(org)
            seen["is_carrier"] = is_carrier

        def exec_(self):
            return QDialog.Accepted

        def get_data(self):
            return {"full_name": "ООО «Фас Транс-2»", "inn": "7701234567"}

    monkeypatch.setattr(module, "EditCarrierDialog", FakeDialog)
    manager._load_carriers_tree()
    _select(manager, _top_by_name(manager, CARRIER_A_NAME))

    manager._on_edit_carrier()

    assert seen["is_carrier"] is True
    assert seen["org"]["id"] == carrier_a
    assert isolated_db.load_organization_by_id(
        carrier_a, is_carrier=True
    )["full_name"] == "ООО «Фас Транс-2»"


def test_edit_driver_opens_driver_dialog(
    manager, isolated_db, carrier_a, monkeypatch
):
    """«✏ Редактировать» на водителе открывает диалог водителя."""
    import ui.db_manager_dialog as module

    driver_id = _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    seen = {}

    class FakeDialog:
        def __init__(self, driver, parent=None):
            seen["driver"] = dict(driver)

        def exec_(self):
            return QDialog.Accepted

        def get_driver_data(self):
            return {"full_name": "Сидоров Сидор Сидорович"}

        def get_vehicle_data(self):
            return {}

    monkeypatch.setattr(module, "EditDriverDialog", FakeDialog)
    manager._load_carriers_tree()
    top = _top_by_name(manager, CARRIER_A_NAME)
    _select(manager, _driver_child(top, DRIVER_NAME))

    manager._on_edit_carrier()

    assert seen["driver"]["id"] == driver_id
    assert isolated_db.load_driver(driver_id)["full_name"] == "Сидоров Сидор Сидорович"


def test_delete_carrier_marks_soft_deleted(manager, isolated_db, carrier_a):
    """«🗑 Удалить» на перевозчике: запись остаётся, но помечена удалённой."""
    manager._load_carriers_tree()
    _select(manager, _top_by_name(manager, CARRIER_A_NAME))

    manager._on_delete_carrier()

    assert isolated_db.load_organization_by_id(
        carrier_a, is_carrier=True
    )["is_deleted"] == 1
    assert _top_names(manager) == [], "из дерева удалённый уходит"


def test_delete_driver_marks_soft_deleted(manager, isolated_db, carrier_a):
    """«🗑 Удалить» на водителе: водитель мягко удалён и ушёл из дерева."""
    driver_id = _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    manager._load_carriers_tree()
    top = _top_by_name(manager, CARRIER_A_NAME)
    _select(manager, _driver_child(top, DRIVER_NAME))

    manager._on_delete_carrier()

    assert isolated_db.load_driver(driver_id)["is_deleted"] == 1
    assert _child_names(_top_by_name(manager, CARRIER_A_NAME)) == []


def test_restore_carrier_returns_it_to_tree(manager, isolated_db, carrier_a):
    """«♻ Восстановить» возвращает перевозчика в дерево без статуса."""
    from ui.db_manager_dialog import CARRIER_COL_STATUS

    isolated_db.delete_organization(carrier_a, is_carrier=True)
    manager.chk_deleted.setChecked(True)
    _select(manager, _top_by_name(manager, CARRIER_A_NAME))

    manager._on_restore_carrier()

    top = _top_by_name(manager, CARRIER_A_NAME)
    assert top.text(CARRIER_COL_STATUS) == ""
    assert isolated_db.load_organization_by_id(
        carrier_a, is_carrier=True
    )["is_deleted"] == 0


def test_restore_driver_returns_it_to_carrier(manager, isolated_db, carrier_a):
    """«♻ Восстановить» возвращает водителя под его перевозчика."""
    driver_id = _add_driver(isolated_db, DRIVER_NAME, carrier_a)
    isolated_db.delete_driver(driver_id)
    manager.chk_deleted.setChecked(True)
    top = _top_by_name(manager, CARRIER_A_NAME)
    _select(manager, _driver_child(top, DRIVER_NAME))

    manager._on_restore_carrier()

    assert isolated_db.load_driver(driver_id)["is_deleted"] == 0
    assert _child_names(_top_by_name(manager, CARRIER_A_NAME)) == [DRIVER_NAME]


def test_add_carrier_builds_tree_node(manager, isolated_db, monkeypatch):
    """«➕ Добавить» на вкладке перевозчиков создаёт узел дерева."""
    import ui.db_manager_dialog as module

    class FakeDialog:
        def __init__(self, org, is_carrier=True, parent=None):
            self.is_carrier = is_carrier

        def exec_(self):
            return QDialog.Accepted

        def get_data(self):
            return {"full_name": CARRIER_B_NAME, "inn": "7707654321"}

    monkeypatch.setattr(module, "EditCarrierDialog", FakeDialog)

    manager._on_add_carrier()

    assert _top_names(manager) == [CARRIER_B_NAME]
