#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты ручного создания записей в «Управлении базой данных».

Проверяется кнопка «➕ Добавить» на трёх вкладках:
  * диалог открывается в режиме создания (пустые поля, заголовок «➕ Новый …»);
  * по «Сохранить» вызывается save_organization / save_driver (+ ТС);
  * запись появляется в таблице диалога и в базе;
  * редактирование существующих записей по-прежнему идёт через update_*;
  * кнопки созданы через ui/theme.py (никаких inline-стилей).

Qt поднимается в offscreen-режиме, диалоги подменяются через monkeypatch —
окна не показываются, тест не требует дисплея.
"""

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton  # noqa: E402

from ui import theme  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def no_dialogs(monkeypatch):
    """Модальные окна не показываем: тест не должен ничего ждать."""
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))


@pytest.fixture
def manager(qt_app, isolated_db, no_dialogs):
    """Диалог управления базой поверх изолированной БД."""
    from ui.db_manager_dialog import DbManagerDialog

    dialog = DbManagerDialog()
    yield dialog
    dialog.close()


def _buttons_with_text(widget, text: str):
    return [
        button for button in widget.findChildren(QPushButton)
        if button.text() == text
    ]


# ─────────────────────────────────────────────────────────────
# Кнопка «➕ Добавить» есть на всех трёх вкладках
# ─────────────────────────────────────────────────────────────

def test_add_buttons_present_on_all_tabs(manager):
    for tab in (manager.carriers_tab, manager.drivers_tab, manager.customers_tab):
        buttons = _buttons_with_text(tab, "➕ Добавить")
        assert len(buttons) == 1, "нет кнопки «➕ Добавить» на вкладке"
        # создаётся через theme.primary_button → роль primary из темы
        assert buttons[0].objectName() == "primary"
        assert buttons[0].minimumHeight() >= 40


def test_existing_buttons_are_not_renamed(manager):
    """Кнопки «Загрузить в форму», «Редактировать», «Удалить», «Восстановить» на месте."""
    for tab in (manager.carriers_tab, manager.drivers_tab, manager.customers_tab):
        texts = {button.text() for button in tab.findChildren(QPushButton)}
        assert "📂 Загрузить в форму" in texts
        assert "✏ Редактировать" in texts
        assert "🗑 Удалить" in texts
        assert "♻ Восстановить" in texts


def test_buttons_have_no_inline_styles(manager):
    """Оформление — только из темы: локальных stylesheet у кнопок нет."""
    for tab in (manager.carriers_tab, manager.drivers_tab, manager.customers_tab):
        for button in tab.findChildren(QPushButton):
            assert button.styleSheet() == "", f"inline-стиль у {button.text()!r}"


# ─────────────────────────────────────────────────────────────
# Диалоги: режим создания и режим редактирования
# ─────────────────────────────────────────────────────────────

def test_new_org_dialog_title_and_empty_fields(qt_app, no_dialogs):
    from ui.db_manager_dialog import EditCarrierDialog

    carrier_dialog = EditCarrierDialog({}, is_carrier=True)
    assert carrier_dialog.is_new is True
    assert carrier_dialog.windowTitle() == "➕ Новый перевозчик"
    assert carrier_dialog.full_name.text() == ""
    assert carrier_dialog.inn.text() == ""

    customer_dialog = EditCarrierDialog({}, is_carrier=False)
    assert customer_dialog.windowTitle() == "➕ Новый заказчик"


def test_edit_org_dialog_keeps_edit_title(qt_app, no_dialogs):
    from ui.db_manager_dialog import EditCarrierDialog

    dialog = EditCarrierDialog(
        {"id": 7, "full_name": "ООО «Ромашка»", "inn": "7701234567"},
        is_carrier=True,
    )
    assert dialog.is_new is False
    assert dialog.windowTitle() == "✏ Редактирование перевозчика"
    assert dialog.full_name.text() == "ООО «Ромашка»"
    assert dialog.get_data()["inn"] == "7701234567"


def test_new_driver_dialog_title_and_empty_fields(qt_app, no_dialogs, isolated_db):
    from ui.db_manager_dialog import EditDriverDialog

    dialog = EditDriverDialog({})
    assert dialog.is_new is True
    assert dialog.windowTitle() == "➕ Новый водитель"
    assert dialog.full_name.text() == ""
    assert dialog.tractor_plate.text() == ""
    # без id тягач/прицеп из БД не подгружаются
    assert dialog.driver_id is None

    edit_dialog = EditDriverDialog({"id": 3, "full_name": "Иванов Иван Иванович"})
    assert edit_dialog.windowTitle() == "✏ Редактирование водителя"


def test_dialog_buttons_use_theme(qt_app, no_dialogs):
    from ui.db_manager_dialog import EditCarrierDialog

    dialog = EditCarrierDialog({}, is_carrier=True)
    roles = {
        button.text(): button.objectName()
        for button in dialog.findChildren(QPushButton)
    }
    assert roles["💾 Сохранить"] == "accent"
    assert roles["Отмена"] == "secondary"
    for button in dialog.findChildren(QPushButton):
        assert button.styleSheet() == ""


# ─────────────────────────────────────────────────────────────
# Создание перевозчика / заказчика
# ─────────────────────────────────────────────────────────────

def test_add_carrier_creates_record(manager, isolated_db, monkeypatch):
    from ui.db_manager_dialog import EditCarrierDialog

    payload = {
        "full_name": "ООО «Новый Перевозчик»",
        "inn": "7701234567",
        "kpp": "770101001",
        "bank_account": "40702810000000000001",
        "bik": "044525225",
    }
    monkeypatch.setattr(EditCarrierDialog, "exec_", lambda self: QDialog.Accepted)
    monkeypatch.setattr(EditCarrierDialog, "get_data", lambda self: dict(payload))

    manager._on_add_org(is_carrier=True)

    saved = isolated_db.get_all_organizations(is_carrier=True)
    assert [o["full_name"] for o in saved] == ["ООО «Новый Перевозчик»"]
    assert saved[0]["inn"] == "7701234567"
    # запись появилась и в таблице диалога
    assert manager.carriers_table.rowCount() == 1


def test_add_customer_creates_record(manager, isolated_db, monkeypatch):
    from ui.db_manager_dialog import EditCarrierDialog

    payload = {"full_name": "ООО «Новый Заказчик»", "inn": "7707654321"}
    monkeypatch.setattr(EditCarrierDialog, "exec_", lambda self: QDialog.Accepted)
    monkeypatch.setattr(EditCarrierDialog, "get_data", lambda self: dict(payload))

    manager._on_add_org(is_carrier=False)

    saved = isolated_db.get_all_organizations(is_carrier=False)
    assert [o["full_name"] for o in saved] == ["ООО «Новый Заказчик»"]
    assert manager.customers_table.rowCount() == 1
    # перевозчиков это не затронуло
    assert isolated_db.get_all_organizations(is_carrier=True) == []


def test_add_org_cancel_creates_nothing(manager, isolated_db, monkeypatch):
    from ui.db_manager_dialog import EditCarrierDialog

    monkeypatch.setattr(EditCarrierDialog, "exec_", lambda self: QDialog.Rejected)

    manager._on_add_org(is_carrier=True)

    assert isolated_db.get_all_organizations(is_carrier=True) == []
    assert manager.carriers_table.rowCount() == 0


# ─────────────────────────────────────────────────────────────
# Создание водителя (+ тягач/прицеп)
# ─────────────────────────────────────────────────────────────

def test_add_driver_creates_record_with_vehicle(manager, isolated_db, monkeypatch):
    from ui.db_manager_dialog import EditDriverDialog

    driver_data = {
        "full_name": "Иванов Иван Иванович",
        "passport_series": "18 22",
        "passport_number": "926830",
        "phone": "+7 (999) 111-22-33",
    }
    vehicle_data = {
        "tractor_brand": "Foton Auman",
        "tractor_plate": "O844XY196",
        "tractor_color": "Белый",
        "tractor_year": "2023",
        "trailer_brand": "YANGMINDA",
        "trailer_plate": "71ABF18",
        "trailer_color": "Серый",
        "trailer_year": "2020",
    }
    monkeypatch.setattr(EditDriverDialog, "exec_", lambda self: QDialog.Accepted)
    monkeypatch.setattr(EditDriverDialog, "get_driver_data", lambda self: dict(driver_data))
    monkeypatch.setattr(EditDriverDialog, "get_vehicle_data", lambda self: dict(vehicle_data))

    manager._on_add_driver()

    drivers = isolated_db.get_all_drivers()
    assert [d["full_name"] for d in drivers] == ["Иванов Иван Иванович"]

    new_id = drivers[0]["id"]
    vehicle = isolated_db.load_driver_vehicle(new_id)
    assert vehicle is not None
    assert vehicle["tractor_plate"] == "O844XY196"
    assert vehicle["trailer_plate"] == "71ABF18"

    assert manager.drivers_table.rowCount() == 1


def test_add_driver_without_vehicle_skips_driver_vehicles(manager, isolated_db, monkeypatch):
    from ui.db_manager_dialog import EditDriverDialog

    monkeypatch.setattr(EditDriverDialog, "exec_", lambda self: QDialog.Accepted)
    monkeypatch.setattr(
        EditDriverDialog, "get_driver_data",
        lambda self: {"full_name": "Петров Пётр Петрович"},
    )
    monkeypatch.setattr(
        EditDriverDialog, "get_vehicle_data",
        lambda self: {
            "tractor_brand": "", "tractor_plate": "", "tractor_color": "",
            "tractor_year": "", "trailer_brand": "", "trailer_plate": "",
            "trailer_color": "", "trailer_year": "",
        },
    )

    manager._on_add_driver()

    drivers = isolated_db.get_all_drivers()
    assert len(drivers) == 1
    assert isolated_db.load_driver_vehicle(drivers[0]["id"]) is None


# ─────────────────────────────────────────────────────────────
# Редактирование существующих записей не сломано
# ─────────────────────────────────────────────────────────────

def test_edit_org_still_updates(manager, isolated_db, monkeypatch):
    from ui.db_manager_dialog import EditCarrierDialog

    org_id = isolated_db.save_organization({"full_name": "ООО «Старое»"}, is_carrier=True)
    manager._load_organizations(is_carrier=True)
    manager.carriers_table.selectRow(0)

    monkeypatch.setattr(EditCarrierDialog, "exec_", lambda self: QDialog.Accepted)
    monkeypatch.setattr(
        EditCarrierDialog, "get_data",
        lambda self: {"full_name": "ООО «Обновлённое»", "inn": "7712345678"},
    )

    manager._on_edit_org(is_carrier=True)

    saved = isolated_db.get_all_organizations(is_carrier=True)
    assert len(saved) == 1                    # новая запись не создавалась
    assert saved[0]["id"] == org_id
    assert saved[0]["full_name"] == "ООО «Обновлённое»"
    assert saved[0]["inn"] == "7712345678"


def test_edit_driver_still_updates(manager, isolated_db, monkeypatch):
    from ui.db_manager_dialog import EditDriverDialog

    driver_id = isolated_db.save_driver({"full_name": "Иванов Иван Иванович"})
    manager._load_drivers()
    manager.drivers_table.selectRow(0)

    monkeypatch.setattr(EditDriverDialog, "exec_", lambda self: QDialog.Accepted)
    monkeypatch.setattr(
        EditDriverDialog, "get_driver_data",
        lambda self: {"full_name": "Сидоров Сидор Сидорович"},
    )
    monkeypatch.setattr(
        EditDriverDialog, "get_vehicle_data",
        lambda self: {
            "tractor_brand": "", "tractor_plate": "A001AA", "tractor_color": "",
            "tractor_year": "", "trailer_brand": "", "trailer_plate": "",
            "trailer_color": "", "trailer_year": "",
        },
    )

    manager._on_edit_driver()

    drivers = isolated_db.get_all_drivers()
    assert len(drivers) == 1
    assert drivers[0]["id"] == driver_id
    assert drivers[0]["full_name"] == "Сидоров Сидор Сидорович"
    assert isolated_db.load_driver_vehicle(driver_id)["tractor_plate"] == "A001AA"


# ─────────────────────────────────────────────────────────────
# Оформление: роли кнопок из темы
# ─────────────────────────────────────────────────────────────

def test_theme_roles_used_for_manager_buttons(manager):
    expected = {
        "➕ Добавить": "primary",
        "📂 Загрузить в форму": "secondary",
        "✏ Редактировать": "secondary",
        "🗑 Удалить": "danger",
        "♻ Восстановить": "secondary",
        "Сбросить": "secondary",
    }
    for tab in (manager.carriers_tab, manager.drivers_tab, manager.customers_tab):
        for button in tab.findChildren(QPushButton):
            if button.text() in expected:
                assert button.objectName() == expected[button.text()], button.text()
    assert manager.btn_close.objectName() == "secondary"


# ─────────────────────────────────────────────────────────────
# Логирование (--debug): видно открытие диалога и id созданной записи
# ─────────────────────────────────────────────────────────────

def _reset_logging() -> None:
    """Снимает хендлеры, поставленные setup_logging(): файлы нужно закрыть."""
    for name in list(logging.Logger.manager.loggerDict):
        obj = logging.getLogger(name)
        if not isinstance(obj, logging.Logger):
            continue
        for handler in list(obj.handlers):
            obj.removeHandler(handler)
            handler.close()

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.WARNING)


def test_add_org_is_logged_without_personal_data(
    qt_app, isolated_db, no_dialogs, monkeypatch, work_dir
):
    import uuid
    from pathlib import Path

    import config.logging_config as logging_config
    from ui.db_manager_dialog import DbManagerDialog, EditCarrierDialog

    logs_dir = work_dir / f"{uuid.uuid4().hex[:8]}_dbm_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # icacls в тестах не нужен и только тормозит
    monkeypatch.setattr(
        "core.security.restrict_to_current_user", lambda *a, **k: True
    )
    logging_config.setup_logging(str(logs_dir), debug=True)

    payload = {
        "full_name": "ООО «Логируемый Перевозчик»",
        "inn": "7701234567",
        "passport_number": "000000",
    }
    monkeypatch.setattr(EditCarrierDialog, "exec_", lambda self: QDialog.Accepted)
    monkeypatch.setattr(EditCarrierDialog, "get_data", lambda self: dict(payload))

    try:
        dialog = DbManagerDialog()
        dialog._on_add_org(is_carrier=True)
        dialog.close()

        for handler in logging.getLogger("ui.db_manager_dialog").handlers:
            handler.flush()

        debug_log = logs_dir / logging_config.DEBUG_LOG_FILENAME
        text = Path(debug_log).read_text(encoding="utf-8", errors="replace")

        assert "Открытие диалога добавления организации" in text
        assert "Организация создана: ID=" in text
        assert "➕ Добавить" in text
        # персональные данные в лог не попадают
        assert "Логируемый" not in text
        assert "7701234567" not in text
        assert "000000" not in text
    finally:
        _reset_logging()
