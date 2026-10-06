#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты справочника организаций на вкладках аренды (ШАГ FIX-1-T2).

Проверяется, что вкладки «Арендатор» и «Арендодатель» работают с ОБЩИМ
справочником организаций — тем же, что «Экспедиторство» (db/database.py:
таблицы customers и carriers), — и не путают роли:

  * «Сохранить в базу» пишет поля вкладки в свою таблицу: Арендатор —
    в customers (заказчики, is_carrier=False), Арендодатель — в carriers
    (перевозчики, is_carrier=True);
  * «Из справочника» заполняет ВСЕ поля вкладки, а незаполненные в записи
    поля обнуляются (форма заменяется целиком, а не подмешивается);
  * дубль по ИНН обновляет существующую запись, а не создаёт вторую;
  * сохранение на одной вкладке не трогает поля другой: у каждой свой
    экземпляр формы и своя роль;
  * запись, сохранённая в аренде, видна через общий API справочника —
    ровно тому коду, которым её читает «Экспедиторство»;
  * модальный диалог выбора подменяется заглушкой: тест проверяет и режим,
    в котором он открывается, и раскладку полученной записи по полям.

Qt — в offscreen-режиме. Данные синтетические, реальных ПДн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QFrame, QMessageBox, QPushButton,
)

from ui.windows.arenda_ts import contacts as contacts_module  # noqa: E402
from ui.windows.arenda_ts.tabs.lessee_tab import LesseeTab  # noqa: E402
from ui.windows.arenda_ts.tabs.lessor_tab import LessorTab  # noqa: E402

# ─────────────────────────────────────────────────────────────
# Данные тестов
# ─────────────────────────────────────────────────────────────

#: Реквизиты Арендатора (нашей стороны) — таблица customers.
LESSEE = {
    "full_name": "ООО «Логистик-Транс»",
    "short_name": "ООО «ЛТ»",
    "inn": "7707654321",
    "kpp": "770701001",
    "ogrn": "1027700261234",
    "address": "г. Москва, ул. Складская, д. 5",
    "actual_address": "г. Москва, ул. Фактическая, д. 6",
    "account": "40702810000000000002",
    "bik": "044525225",
    "bank": "ПАО Сбербанк",
    "corr_account": "30101810400000000225",
    "email": "arenda@example.ru",
    "phone": "+7 (495) 000-11-22",
    "director_position": "Генеральный директор",
    "director_name": "Сидоров Сидор Сидорович",
}

#: Реквизиты Арендодателя (второй стороны) — таблица carriers.
LESSOR = {
    "full_name": "ООО «ЛЦ»",
    "short_name": "ООО «ЛЦ»",
    "inn": "7701234567",
    "ogrn": "1027700132195",
    "address": "г. Москва, ул. Тестовая, д. 1",
    "actual_address": "г. Москва, ул. Тестовая, д. 2",
    "account": "40702810000000000003",
    "bik": "044525226",
    "bank": "АО «Банк Второй»",
    "corr_account": "30101810400000000226",
    "email": "lessor@example.ru",
    "director_position": "Директор",
    "director_name": "Петров Пётр Петрович",
}

#: Поля формы, которые заполняются из записи справочника: Арендатор целиком,
#: у Арендодателя без КПП (плейсхолдера lessor_kpp в бланке нет).
LESSEE_FORM_KEYS = (
    "full_name", "short_name", "inn", "kpp", "ogrn", "address",
    "actual_address", "account", "bik", "bank", "corr_account", "email",
    "director_position", "director_name",
)
LESSOR_FORM_KEYS = tuple(key for key in LESSEE_FORM_KEYS if key != "kpp")

#: Заведомо чужие данные: по ним видно, что загрузка заменила форму.
STALE_NAME = "ООО «Прошлая Организация»"
STALE_INN = "1111111111"

#: Имена режимов выбора в диалоге справочника.
OPEN_TAB_CUSTOMERS = "customers"
OPEN_TAB_CARRIERS = "carriers"


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def quiet_dialogs(monkeypatch):
    """Ни один тест не должен останавливаться на модальном окне."""
    for name in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *args, **kwargs: QMessageBox.Ok),
        )


@pytest.fixture
def lessee(qt_app):
    """Свежая вкладка «Арендатор» поверх изолированной базы."""
    widget = LesseeTab()
    yield widget
    widget.deleteLater()


@pytest.fixture
def lessor(qt_app):
    """Свежая вкладка «Арендодатель» поверх изолированной базы."""
    widget = LessorTab()
    yield widget
    widget.deleteLater()


@pytest.fixture
def picker(monkeypatch):
    """
    Заглушка диалога выбора организации вместо модального QDialog.

    Тесты кнопки «Из справочника» проверяют две вещи: в каком режиме вкладка
    открывает диалог (своя роль!) и как раскладывает полученную запись по
    полям. Настоящее окно в offscreen-режиме показывать нельзя, поэтому
    exec_() подменяется: он отдаёт запись в on_pick и возвращает Accepted.
    """
    created = []

    class FakeDialog:
        #: Запись, которую «выбрал пользователь» (тест кладёт её сюда).
        picked = None

        def __init__(self, parent=None, **kwargs):
            self.parent = parent
            self.open_tab = kwargs.get("open_tab", "")
            self.on_pick = kwargs.get("on_pick")
            created.append(self)

        def exec_(self):
            if self.picked is not None and self.on_pick is not None:
                self.on_pick(self.picked)
            return 1

    # Имена режимов должны совпадать с ui/db_manager_dialog.py: подмена
    # проверяет именно то, что вкладка просит нужную таблицу справочника.
    assert OPEN_TAB_CUSTOMERS == contacts_module.ROLE_SCOPE[
        contacts_module.ROLE_LESSEE
    ][0]
    assert OPEN_TAB_CARRIERS == contacts_module.ROLE_SCOPE[
        contacts_module.ROLE_LESSOR
    ][0]

    monkeypatch.setattr(
        "ui.windows.arenda_ts.tabs.lessee_tab.DbManagerDialog", FakeDialog
    )
    monkeypatch.setattr(
        "ui.windows.arenda_ts.tabs.lessor_tab.DbManagerDialog", FakeDialog
    )
    return created, FakeDialog


# ─────────────────────────────────────────────────────────────
# Вспомогательное
# ─────────────────────────────────────────────────────────────

def _fill(tab, data):
    """Заполняет вкладку реквизитами из словаря (адреса — QTextEdit)."""
    for key, value in data.items():
        if key in ("address", "actual_address"):
            getattr(tab, key).setPlainText(value)
        elif key == "phone":
            tab.phone = value
        else:
            getattr(tab, key).setText(value)


def _buttons_with_text(widget, text):
    """Кнопки вкладки с указанной подписью."""
    return [
        button for button in widget.findChildren(QPushButton)
        if button.text() == text
    ]


# ─────────────────────────────────────────────────────────────
# Кнопки на вкладках
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fixture_name", ["lessee", "lessor"])
def test_directory_buttons_present(request, fixture_name):
    """«Из справочника» и «Сохранить в базу» есть на обеих вкладках сторон."""
    tab = request.getfixturevalue(fixture_name)

    load = _buttons_with_text(tab, "Из справочника")
    save = _buttons_with_text(tab, "Сохранить в базу")

    assert len(load) == 1, "нет кнопки «Из справочника»"
    assert len(save) == 1, "нет кнопки «Сохранить в базу»"
    assert load[0] is tab.btn_load_organization
    assert save[0] is tab.btn_save_organization


@pytest.mark.parametrize("fixture_name", ["lessee", "lessor"])
def test_directory_buttons_are_topmost(request, fixture_name):
    """Кнопки стоят сверху вкладки — сразу под панелью распознавания."""
    tab = request.getfixturevalue(fixture_name)
    panel = tab.findChild(QFrame, "directoryBar")

    assert panel is not None, "нет панели справочника"

    content_layout = tab.recognition_panel.parent().layout()
    assert content_layout.indexOf(panel) == (
        content_layout.indexOf(tab.recognition_panel) + 1
    ), "панель справочника должна идти сразу после панели распознавания"


@pytest.mark.parametrize("fixture_name", ["lessee", "lessor"])
def test_directory_buttons_have_no_inline_styles(request, fixture_name):
    """Оформление — только из темы: локальных stylesheet у кнопок нет."""
    tab = request.getfixturevalue(fixture_name)

    for button in (tab.btn_load_organization, tab.btn_save_organization):
        assert button.styleSheet() == ""


def test_roles_are_not_global(lessee, lessor):
    """
    Роль привязана к вкладке, а не к «текущей стороне» окна.

    Кнопки разных вкладок — разные объекты, и каждая знает свою роль:
    Арендатор — customers, Арендодатель — carriers.
    """
    assert lessee.btn_load_organization is not lessor.btn_load_organization
    assert lessee.btn_save_organization is not lessor.btn_save_organization
    assert contacts_module.ROLE_SCOPE[contacts_module.ROLE_LESSEE] == (
        "customers", False
    )
    assert contacts_module.ROLE_SCOPE[contacts_module.ROLE_LESSOR] == (
        "carriers", True
    )


# ─────────────────────────────────────────────────────────────
# Сохранение в базу
# ─────────────────────────────────────────────────────────────

def test_lessee_saves_organization_to_customers(lessee, isolated_db):
    """Сохранение с «Арендатора» → запись есть в базе (таблица customers)."""
    _fill(lessee, LESSEE)

    result = lessee.save_to_directory()

    assert result.ok, result.error
    assert result.updated is False, "новой записи неоткуда взяться"

    saved = isolated_db.get_all_organizations(is_carrier=False)
    assert len(saved) == 1
    record = saved[0]
    assert record["full_name"] == LESSEE["full_name"]
    assert record["short_name"] == LESSEE["short_name"]
    assert record["inn"] == LESSEE["inn"]
    assert record["kpp"] == LESSEE["kpp"]
    assert record["ogrn"] == LESSEE["ogrn"]
    assert record["legal_address"] == LESSEE["address"]
    assert record["actual_address"] == LESSEE["actual_address"]
    assert record["bank_account"] == LESSEE["account"]
    assert record["bik"] == LESSEE["bik"]
    assert record["bank_name"] == LESSEE["bank"]
    assert record["correspondent_account"] == LESSEE["corr_account"]
    assert record["director_name"] == LESSEE["director_name"]
    assert record["director_position"] == LESSEE["director_position"]
    assert record["email"] == LESSEE["email"]
    # Арендатор — НЕ перевозчик: в carriers записи быть не должно.
    assert isolated_db.get_all_organizations(is_carrier=True) == []


def test_lessor_saves_organization_to_carriers(lessor, isolated_db):
    """Сохранение с «Арендодателя» → запись в carriers, а не в customers."""
    _fill(lessor, LESSOR)

    result = lessor.save_to_directory()

    assert result.ok, result.error
    saved = isolated_db.get_all_organizations(is_carrier=True)
    assert len(saved) == 1
    assert saved[0]["full_name"] == LESSOR["full_name"]
    assert saved[0]["inn"] == LESSOR["inn"]
    assert saved[0]["legal_address"] == LESSOR["address"]
    assert saved[0]["bank_account"] == LESSOR["account"]
    assert saved[0]["director_name"] == LESSOR["director_name"]
    assert isolated_db.get_all_organizations(is_carrier=False) == []


def test_save_without_name_reports_problem(lessee, isolated_db):
    """Без наименования сохранять нечего: запись не создаётся."""
    lessee.inn.setText("7707654321")

    result = lessee.save_to_directory()

    assert result.ok is False
    assert "наименование" in result.error
    assert isolated_db.get_all_organizations(is_carrier=False) == []


def test_save_button_reports_result(lessee, isolated_db, monkeypatch):
    """Кнопка «Сохранить в базу» сохраняет и показывает итог."""
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *args, **kwargs: shown.append(args)),
    )

    _fill(lessee, LESSEE)
    lessee.btn_save_organization.click()

    assert len(shown) == 1, "итоговый диалог не показан"
    assert len(isolated_db.get_all_organizations(is_carrier=False)) == 1


# ─────────────────────────────────────────────────────────────
# Загрузка из справочника
# ─────────────────────────────────────────────────────────────

def test_lessee_picker_asks_for_customers(picker):
    """«Из справочника» на «Арендаторе» открывает таблицу заказчиков."""
    created, _fake = picker
    tab = LesseeTab()

    tab.load_from_directory()

    assert [dialog.open_tab for dialog in created] == [OPEN_TAB_CUSTOMERS]
    assert created[0].parent is tab
    assert created[0].on_pick is not None

    tab.deleteLater()


def test_lessor_picker_asks_for_carriers(picker):
    """«Из справочника» на «Арендодателе» открывает таблицу перевозчиков."""
    created, _fake = picker
    tab = LessorTab()

    tab.load_from_directory()

    assert [dialog.open_tab for dialog in created] == [OPEN_TAB_CARRIERS]
    assert created[0].on_pick is not None

    tab.deleteLater()


# ── Сам диалог справочника в режиме выбора ──
# Проверяется на настоящем DbManagerDialog: заглушка выше проверяет вызов,
# а эти тесты — что режим выбора действительно открывает нужную таблицу,
# прячет правку справочника и отдаёт выбранную запись обработчику.

def _open_dialog(open_tab, on_pick):
    """Настоящий DbManagerDialog в режиме выбора записи."""
    from ui.db_manager_dialog import DbManagerDialog

    return DbManagerDialog(None, open_tab=open_tab, on_pick=on_pick)


def test_picker_dialog_shows_only_its_own_tab(qt_app, isolated_db):
    """В режиме выбора видна одна вкладка: чужая роль не выбирается."""
    from ui.db_manager_dialog import (
        OPEN_TAB_CARRIERS, OPEN_TAB_DRIVERS, OPEN_TAB_INDEX,
    )

    expected = {
        OPEN_TAB_CUSTOMERS: 2,
        OPEN_TAB_CARRIERS: 0,
        OPEN_TAB_DRIVERS: 1,
    }
    for open_tab, index in expected.items():
        dialog = _open_dialog(open_tab, lambda record: None)
        visible = [dialog.tabs.isTabVisible(i) for i in range(3)]
        assert visible == [i == index for i in range(3)], open_tab
        assert dialog.tabs.currentIndex() == OPEN_TAB_INDEX[open_tab]
        dialog.close()


def test_picker_dialog_hides_editing_buttons(qt_app, isolated_db):
    """В режиме выбора справочник только читают: правка и удаление скрыты."""
    dialog = _open_dialog(OPEN_TAB_CUSTOMERS, lambda record: None)

    texts = {
        button.text() for button in dialog.customers_tab.findChildren(QPushButton)
        if button.isVisible() or not button.isHidden()
    }
    assert "📂 Выбрать" in texts, "кнопка выбора должна остаться"
    for hidden in ("➕ Добавить", "✏ Редактировать", "🗑 Удалить", "♻ Восстановить"):
        assert hidden not in texts, hidden
    dialog.close()


def test_picker_dialog_gives_the_record_to_the_tab(qt_app, isolated_db):
    """Выбранная запись уходит в on_pick, а диалог закрывается с Accepted."""
    saved_id = isolated_db.save_organization(
        contacts_module.organization_from_form(LESSEE), is_carrier=False
    )
    picked = []
    dialog = _open_dialog(OPEN_TAB_CUSTOMERS, picked.append)

    dialog.customers_table.setCurrentCell(0, 0)
    dialog._on_load_org(False)

    assert [record.get("id") for record in picked] == [saved_id]
    assert picked[0]["full_name"] == LESSEE["full_name"]
    assert dialog.result() == dialog.Accepted
    dialog.close()


def test_picker_dialog_keeps_manager_mode_intact(qt_app, isolated_db):
    """Без open_tab диалог работает как прежде: три вкладки и их кнопки."""
    from ui.db_manager_dialog import DbManagerDialog

    dialog = DbManagerDialog()

    assert all(dialog.tabs.isTabVisible(i) for i in range(3))
    assert dialog.windowTitle() == "🗄 Управление базой данных"
    for tab in (dialog.carriers_tab, dialog.drivers_tab, dialog.customers_tab):
        texts = {button.text() for button in tab.findChildren(QPushButton)}
        assert "📂 Загрузить в форму" in texts
        assert "➕ Добавить" in texts
        assert "✏ Редактировать" in texts
        assert "🗑 Удалить" in texts
        assert "♻ Восстановить" in texts
    dialog.close()


def test_picker_dialog_asks_for_the_right_table(qt_app, isolated_db):
    """
    Арендатор выбирает из customers, Арендодатель — из carriers.

    Даже при одинаковых данных роли не смешиваются: таблицы разные, и запись
    из одной не попадает в другую.
    """
    contacts_module.save_organization_record(LESSEE, contacts_module.ROLE_LESSEE)
    contacts_module.save_organization_record(LESSOR, contacts_module.ROLE_LESSOR)

    picked = []
    lessee_dialog = _open_dialog(OPEN_TAB_CUSTOMERS, picked.append)
    lessee_dialog.customers_table.setCurrentCell(0, 0)
    lessee_dialog._on_load_org(False)

    lessor_dialog = _open_dialog(OPEN_TAB_CARRIERS, picked.append)
    lessor_dialog.carriers_table.setCurrentCell(0, 0)
    lessor_dialog._on_load_org(True)

    assert [record["full_name"] for record in picked] == [
        LESSEE["full_name"], LESSOR["full_name"],
    ]

    lessee_dialog.close()
    lessor_dialog.close()


def test_lessee_load_fills_all_fields(lessee, picker, isolated_db):
    """Загрузка на «Арендатора» заполняет все поля вкладки."""
    from ui.windows.arenda_ts import contacts

    record_id = isolated_db.save_organization(
        contacts.organization_from_form(LESSEE), is_carrier=False
    )
    record = isolated_db.get_all_organizations(is_carrier=False)[0]
    assert record["id"] == record_id

    _created, fake = picker
    fake.picked = record
    lessee.load_from_directory()

    data = lessee.get_data()
    for key in LESSEE_FORM_KEYS:
        assert data[key] == LESSEE[key], key
    # Телефон приходит из записи и уходит в данные вкладки (поля формы нет).
    assert data["phone"] == LESSEE["phone"]
    assert lessee.carrier_type.currentText() == "ООО"


def test_lessor_load_fills_all_fields(lessor, isolated_db):
    """Загрузка на «Арендодателя» заполняет все поля вкладки."""
    from ui.windows.arenda_ts import contacts

    isolated_db.save_organization(
        contacts.organization_from_form(LESSOR), is_carrier=True
    )
    record = isolated_db.get_all_organizations(is_carrier=True)[0]

    lessor._fill_organization(record)

    data = lessor.get_data()
    for key in LESSOR_FORM_KEYS:
        assert data[key] == LESSOR[key], key
    assert "kpp" not in data, "у Арендодателя КПП нет"


def test_load_replaces_form_completely(lessee):
    """
    Загрузка заменяет форму целиком: пустые поля записи обнуляются.

    Иначе на вкладке остались бы реквизиты прошлой организации — и в договор
    ушла бы их смесь.
    """
    _fill(lessee, {**LESSEE, "inn": STALE_INN})
    lessee.full_name.setText(STALE_NAME)

    lessee._fill_organization({
        "id": 5, "full_name": "ООО «Новая»", "inn": "7712345678",
        "legal_address": "г. Тверь",
    })

    data = lessee.get_data()
    assert data["full_name"] == "ООО «Новая»"
    assert data["inn"] == "7712345678"
    assert data["address"] == "г. Тверь"
    assert data["kpp"] == "", "в записи КПП пуст — поле очищено"
    assert data["actual_address"] == ""
    assert data["bank"] == ""
    assert data.get("phone", "") == "", "телефон прошлой организации убран"
    assert data["carrier_type"] == "ООО", "вид по умолчанию — ООО"


def test_load_accepts_raw_directory_names(lessee):
    """Запись справочника принимается и с «сырыми» именами колонок.

    Такую запись отдаёт таблица базы (get_all_organizations): legal_address,
    bank_account, bank_name, correspondent_account.
    """
    lessee._fill_organization({
        "id": 1,
        "full_name": "ООО «Ромашка»",
        "inn": "7701234567",
        "legal_address": "г. Москва, ул. Ромашковая, д. 1",
        "bank_account": "40702810000000000001",
        "bank_name": "ПАО Сбербанк",
        "correspondent_account": "30101810400000000225",
    })

    data = lessee.get_data()
    assert data["address"] == "г. Москва, ул. Ромашковая, д. 1"
    assert data["account"] == "40702810000000000001"
    assert data["bank"] == "ПАО Сбербанк"
    assert data["corr_account"] == "30101810400000000225"


def test_load_ignores_empty_record(lessee):
    """Пустая запись ничего не меняет: форма остаётся как была."""
    _fill(lessee, LESSEE)
    before = lessee.get_data()

    lessee._fill_organization({})

    assert lessee.get_data() == before


# ─────────────────────────────────────────────────────────────
# Дубль по ИНН
# ─────────────────────────────────────────────────────────────

def test_duplicate_inn_updates_instead_of_creating(lessee, isolated_db):
    """Дубль по ИНН обновляет существующую запись — второй не появляется."""
    _fill(lessee, LESSEE)
    first = lessee.save_to_directory()
    assert first.updated is False

    lessee.full_name.setText("ООО «Логистик-Транс Плюс»")
    lessee.bank.setText("АО «Новый Банк»")
    second = lessee.save_to_directory()

    assert second.updated is True, "запись с таким ИНН должна обновляться"
    assert second.record_id == first.record_id

    saved = isolated_db.get_all_organizations(is_carrier=False)
    assert len(saved) == 1, "дубль по ИНН не должен создавать вторую запись"
    assert saved[0]["full_name"] == "ООО «Логистик-Транс Плюс»"
    assert saved[0]["bank_name"] == "АО «Новый Банк»"


def test_duplicate_inn_is_per_role(lessor, isolated_db):
    """Один и тот же ИНН у разных ролей — разные записи, а не дубль."""
    from ui.windows.arenda_ts import contacts

    isolated_db.save_organization(
        contacts.organization_from_form(LESSEE), is_carrier=False
    )
    _fill(lessor, {**LESSOR, "inn": LESSEE["inn"]})

    result = lessor.save_to_directory()

    assert result.updated is False, "в carriers такой ИНН ещё не встречался"
    assert len(isolated_db.get_all_organizations(is_carrier=True)) == 1
    assert len(isolated_db.get_all_organizations(is_carrier=False)) == 1


def test_empty_inn_always_creates_new_record(lessee, isolated_db):
    """Пустой ИНН ключом не является: две организации без ИНН не склеиваются."""
    _fill(lessee, {**LESSEE, "inn": ""})
    assert lessee.save_to_directory().updated is False

    lessee.full_name.setText("ИП Без ИНН")
    assert lessee.save_to_directory().updated is False

    assert len(isolated_db.get_all_organizations(is_carrier=False)) == 2


def test_update_keeps_columns_the_tab_does_not_know(lessee, isolated_db):
    """
    Обновление не теряет колонки, которых у вкладки аренды нет.

    У вкладки «Арендатор» нет полей фактического адреса и корр. счёта: они
    приходят из записи справочника и должны пережить повторное сохранение с
    вкладки, а не обнулиться.
    """
    from ui.windows.arenda_ts import contacts

    # Запись создаётся «в Экспедиторстве»: с реквизитами, которых у вкладки
    # аренды нет (фактический адрес и корр. счёт полей формы не имеют).
    record_data = contacts.organization_from_form(LESSEE)
    record_data["actual_address"] = LESSEE["actual_address"]
    record_data["correspondent_account"] = LESSEE["corr_account"]
    isolated_db.save_organization(record_data, is_carrier=False)

    # На вкладке — тот же ИНН и изменённый банк: сохранение обязано обновить
    # существующую запись, не потеряв её «невидимые» для вкладки колонки.
    _fill(lessee, {**LESSEE, "bank": "АО «Ещё Один Банк»"})
    lessee.save_to_directory()

    saved = isolated_db.get_all_organizations(is_carrier=False)
    assert len(saved) == 1, "должна обновиться одна и та же запись"
    assert saved[0]["bank_name"] == "АО «Ещё Один Банк»"
    assert saved[0]["actual_address"] == LESSEE["actual_address"], (
        "фактический адрес не должен теряться при обновлении"
    )
    assert saved[0]["correspondent_account"] == LESSEE["corr_account"]


def test_deleted_record_is_restored_by_saving(lessee, isolated_db):
    """Мягко удалённая запись с тем же ИНН обновляется и возвращается."""
    _fill(lessee, LESSEE)
    result = lessee.save_to_directory()
    isolated_db.delete_organization(result.record_id, is_carrier=False)
    assert isolated_db.get_all_organizations(is_carrier=False) == []

    again = lessee.save_to_directory()

    assert again.record_id == result.record_id
    assert again.updated is True
    saved = isolated_db.get_all_organizations(is_carrier=False)
    assert [org["id"] for org in saved] == [result.record_id]


# ─────────────────────────────────────────────────────────────
# Изоляция ролей
# ─────────────────────────────────────────────────────────────

def test_saving_on_one_tab_does_not_touch_the_other(lessee, lessor, isolated_db):
    """Сохранение на одной вкладке не затирает поля другой."""
    _fill(lessee, LESSEE)
    _fill(lessor, LESSOR)
    before_lessor = lessor.get_data()

    lessee.save_to_directory()

    assert lessor.get_data() == before_lessor
    assert lessor.full_name.text() == LESSOR["full_name"]
    assert isolated_db.get_all_organizations(is_carrier=True) == []
    assert len(isolated_db.get_all_organizations(is_carrier=False)) == 1


def test_loading_on_one_tab_does_not_touch_the_other(lessee, lessor):
    """Загрузка на одной вкладке не меняет поля другой."""
    _fill(lessor, LESSOR)
    before_lessor = lessor.get_data()

    lessee._fill_organization({
        "id": 1, "full_name": "ООО «Только Арендатор»", "inn": "7711111111",
    })

    assert lessor.get_data() == before_lessor
    assert lessee.get_data()["full_name"] == "ООО «Только Арендатор»"


# ─────────────────────────────────────────────────────────────
# Общий справочник: обмен с «Экспедиторством»
# ─────────────────────────────────────────────────────────────

def test_organization_saved_in_arenda_is_visible_through_common_api(
    lessee, isolated_db
):
    """
    Сохранённая в аренде организация читается общим API справочника.

    Тем же кодом её видит «Экспедиторство»: get_all_organizations и
    search_organizations — публичные функции db/database.py.
    """
    _fill(lessee, LESSEE)
    result = lessee.save_to_directory()

    by_id = [
        org for org in isolated_db.get_all_organizations(is_carrier=False)
        if org["id"] == result.record_id
    ]
    assert by_id, "записи нет в общем списке справочника"
    assert by_id[0]["full_name"] == LESSEE["full_name"]

    found = isolated_db.search_organizations(LESSEE["inn"], is_carrier=False)
    assert [org["id"] for org in found] == [result.record_id]

    found_by_name = isolated_db.search_organizations("Логистик", is_carrier=False)
    assert result.record_id in [org["id"] for org in found_by_name]


def test_organization_from_expediting_is_loaded_into_arenda(lessor, isolated_db):
    """
    Обратный обмен: запись, созданная как в «Экспедиторстве», попадает
    на вкладку аренды.

    Запись создаётся общим save_organization(is_carrier=True) — тем же
    вызовом, что в MainWindow._on_save_to_db, а на вкладку попадает
    результат поиска по ИНН (find_organization).
    """
    isolated_db.save_organization(
        {
            "full_name": "ООО «Перевозчик из Экспедиторства»",
            "short_name": "ООО «ПЭ»",
            "inn": "7799999999",
            "legal_address": "г. Пермь, ул. Заводская, д. 3",
            "bank_account": "40702810000000000009",
            "bank_name": "АО «Банк Третий»",
            "director_name": "Кузнецов Кузьма Кузьмич",
        },
        is_carrier=True,
    )

    found = contacts_module.find_organization(
        {"inn": "7799999999"}, contacts_module.ROLE_LESSOR
    )
    assert found is not None, "запись не найдена в общем справочнике"

    lessor._fill_organization(found)

    data = lessor.get_data()
    assert data["full_name"] == "ООО «Перевозчик из Экспедиторства»"
    assert data["inn"] == "7799999999"
    assert data["address"] == "г. Пермь, ул. Заводская, д. 3"
    assert data["account"] == "40702810000000000009"
    assert data["director_name"] == "Кузнецов Кузьма Кузьмич"


def test_arenda_uses_shared_tables_not_its_own(isolated_db):
    """
    Аренда не заводит своей таблицы: роли ложатся на customers / carriers.

    Проверяется и состав таблиц базы, и то, что в аренде «Арендатор» —
    это customers, а «Арендодатель» — carriers (ROLE_SCOPE).
    """
    conn = isolated_db.get_connection()
    try:
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert "customers" in names and "carriers" in names
    assert not {name for name in names if "arenda" in name.lower()}, (
        "для аренды не должно быть отдельной таблицы"
    )


# ─────────────────────────────────────────────────────────────
# Раскладка полей (чистые функции contacts.py)
# ─────────────────────────────────────────────────────────────

def test_organization_from_form_fills_all_directory_columns():
    """Поля вкладки раскладываются по колонкам справочника без потерь."""
    record = contacts_module.organization_from_form(
        {**LESSEE, "carrier_type": "ООО", "basis": "Устава", "edo": "X-1"}
    )

    assert record["full_name"] == LESSEE["full_name"]
    assert record["legal_address"] == LESSEE["address"]
    assert record["actual_address"] == LESSEE["actual_address"]
    assert record["bank_account"] == LESSEE["account"]
    assert record["bank_name"] == LESSEE["bank"]
    assert record["correspondent_account"] == LESSEE["corr_account"]
    assert record["phone"] == LESSEE["phone"]
    # Вид стороны, основание и ЭДО в справочнике организаций не хранятся.
    assert "carrier_type" not in record
    assert "basis" not in record
    assert "edo" not in record


def test_organization_to_form_skips_empty_values():
    """Пустые колонки не подставляются: они не должны стирать ввод."""
    values = contacts_module.organization_to_form(
        {"full_name": "", "inn": "  ", "legal_address": "", "email": "a@b.ru"}
    )

    assert values == {"email": "a@b.ru"}


def test_role_helpers():
    """Роль → таблица справочника и подпись."""
    assert contacts_module.role_label(contacts_module.ROLE_LESSOR) == "Арендодатель"
    assert contacts_module.is_carrier_role(contacts_module.ROLE_LESSOR) is True
    assert contacts_module.is_carrier_role(contacts_module.ROLE_LESSEE) is False
    assert contacts_module.ROLE_TABLE[contacts_module.ROLE_LESSEE] == "customers"
    assert contacts_module.ROLE_TABLE[contacts_module.ROLE_LESSOR] == "carriers"
