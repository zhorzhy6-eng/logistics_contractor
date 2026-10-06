#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты справочника водителей на вкладке «Экипаж» аренды (ШАГ FIX-1-T2).

Вкладка работает с ОБЩИМ справочником водителей — тем же, что
«Экспедиторство» (db/database.py, таблица drivers). Отдельной таблицы
для аренды нет. Проверяется:

  * «Сохранить в базу» пишет данные вкладки в общий справочник;
  * «Из справочника» заполняет ВСЕ девять полей вкладки, включая паспорт
    и водительское удостоверение, которые справочник хранит серией и
    номером, а вкладка — одной строкой;
  * дубль обновляет существующую запись, а не создаёт вторую. Ключ дубля:
    серия+номер ВУ → серия+номер паспорта → ФИО + дата рождения;
  * форма заменяется целиком: пустые поля записи обнуляются;
  * водитель, сохранённый в аренде, читается общим API справочника.

Qt — в offscreen-режиме. Данные синтетические, реальных ПДн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QFrame, QMessageBox, QPushButton,
)

from ui.windows.arenda_ts import contacts as contacts_module  # noqa: E402
from ui.windows.arenda_ts.tabs.crew_tab import CrewTab  # noqa: E402

# ─────────────────────────────────────────────────────────────
# Данные тестов
# ─────────────────────────────────────────────────────────────

#: Полностью заполненный водитель — девять полей бланка аренды (п. 3.5).
DRIVER = {
    "driver_full_name": "Иванов Иван Иванович",
    "driver_birth_date": "1980-01-01",
    "driver_passport": "18 22 926830",
    "driver_passport_issuer": "Отделом УФМС России по г. Москве",
    "driver_passport_issue_date": "2023-01-30",
    "driver_license": "99 36 123456",
    "driver_license_issue_date": "2020-01-01",
    "driver_registration_address": "г. Москва, ул. Тестовая, д. 1",
    "driver_phone": "+7 (999) 123-45-67",
}

#: Девять ключей get_data() — ровно те, что читает сборка данных.
FORM_KEYS = tuple(DRIVER)

#: Реквизиты, которые вводятся только в «Экспедиторстве»: вкладка аренды их
#: не показывает и не отдаёт, но при обновлении записи они не теряются.
EXTRA_RECORD = {
    "birth_place": "г. Москва",
    "passport_code": "500-123",
    "license_expiry_date": "2030-01-01",
    "license_categories": "B, C, E",
}


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
def crew(qt_app):
    """Свежая вкладка «Экипаж» поверх изолированной базы."""
    widget = CrewTab()
    yield widget
    widget.deleteLater()


@pytest.fixture
def picker(monkeypatch):
    """Заглушка диалога выбора водителя вместо модального QDialog."""
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

    monkeypatch.setattr(
        "ui.windows.arenda_ts.tabs.crew_tab.DbManagerDialog", FakeDialog
    )
    return created, FakeDialog


# ─────────────────────────────────────────────────────────────
# Вспомогательное
# ─────────────────────────────────────────────────────────────

def _fill(tab, data):
    """Заполняет вкладку «Экипаж» данными водителя."""
    for key, value in data.items():
        widget = getattr(tab, key)
        if key == "driver_registration_address":
            widget.setPlainText(value)
        elif key in CrewTab.DATE_FIELDS:
            # Даты — QDateEdit: значение ставится через разбор даты вкладки.
            assert tab._set_date(widget, value), key
        else:
            widget.setText(value)


def _buttons_with_text(widget, text):
    """Кнопки вкладки с указанной подписью."""
    return [
        button for button in widget.findChildren(QPushButton)
        if button.text() == text
    ]


# ─────────────────────────────────────────────────────────────
# Кнопки вкладки
# ─────────────────────────────────────────────────────────────

def test_directory_buttons_present(crew):
    """«Из справочника» и «Сохранить в базу» есть на вкладке «Экипаж»."""
    load = _buttons_with_text(crew, "Из справочника")
    save = _buttons_with_text(crew, "Сохранить в базу")

    assert len(load) == 1, "нет кнопки «Из справочника»"
    assert len(save) == 1, "нет кнопки «Сохранить в базу»"
    assert load[0] is crew.btn_load_driver
    assert save[0] is crew.btn_save_driver


def test_directory_buttons_are_topmost(crew):
    """Кнопки стоят сверху вкладки — сразу под панелью распознавания."""
    panel = crew.findChild(QFrame, "directoryBar")

    assert panel is not None, "нет панели справочника"
    content_layout = crew.recognition_panel.parent().layout()
    assert content_layout.indexOf(panel) == (
        content_layout.indexOf(crew.recognition_panel) + 1
    ), "панель справочника должна идти сразу после панели распознавания"


def test_directory_buttons_have_no_inline_styles(crew):
    """Оформление — только из темы: локальных stylesheet у кнопок нет."""
    for button in (crew.btn_load_driver, crew.btn_save_driver):
        assert button.styleSheet() == ""


def test_picker_asks_for_drivers(picker):
    """«Из справочника» открывает вкладку водителей справочника."""
    created, _fake = picker
    tab = CrewTab()

    tab.load_from_directory()

    assert [dialog.open_tab for dialog in created] == ["drivers"]
    assert created[0].parent is tab
    assert created[0].on_pick is not None

    tab.deleteLater()


def test_picker_dialog_gives_the_driver_to_the_tab(qt_app, isolated_db):
    """Настоящий диалог в режиме выбора отдаёт водителя на вкладку «Экипаж»."""
    from ui.db_manager_dialog import DbManagerDialog

    record = {**contacts_module.driver_from_form(DRIVER), **EXTRA_RECORD}
    saved_id = isolated_db.save_driver(record)
    crew = CrewTab()
    dialog = DbManagerDialog(None, open_tab="drivers", on_pick=crew._fill_driver)

    # В режиме выбора видна одна вкладка — водители.
    assert [dialog.tabs.isTabVisible(i) for i in range(3)] == [False, True, False]

    dialog.drivers_table.setCurrentCell(0, 0)
    dialog._on_load_driver()

    data = crew.get_data()
    assert data["driver_full_name"] == DRIVER["driver_full_name"]
    assert data["driver_passport"] == DRIVER["driver_passport"]
    assert data["driver_license"] == DRIVER["driver_license"]
    assert data["driver_phone"] == DRIVER["driver_phone"]
    assert crew.passport_code.text() == EXTRA_RECORD["passport_code"]
    # Запись в базе не изменилась: диалог в этом режиме только читает.
    assert isolated_db.load_driver(saved_id)["full_name"] == DRIVER["driver_full_name"]

    dialog.close()
    crew.deleteLater()


# ─────────────────────────────────────────────────────────────
# Сохранение в базу
# ─────────────────────────────────────────────────────────────

def test_driver_is_saved_to_common_directory(crew, isolated_db):
    """Сохранение водителя → запись есть в общем справочнике."""
    _fill(crew, DRIVER)

    result = crew.save_to_directory()

    assert result.ok, result.error
    assert result.updated is False

    saved = isolated_db.get_all_drivers()
    assert len(saved) == 1
    record = saved[0]
    assert record["full_name"] == DRIVER["driver_full_name"]
    assert record["birth_date"] == DRIVER["driver_birth_date"]
    assert record["passport_series"] == "18 22"
    assert record["passport_number"] == "926830"
    assert record["passport_issuer"] == DRIVER["driver_passport_issuer"]
    assert record["passport_issue_date"] == DRIVER["driver_passport_issue_date"]
    assert record["license_series"] == "99 36"
    assert record["license_number"] == "123456"
    assert record["license_issue_date"] == DRIVER["driver_license_issue_date"]
    assert record["registration_address"] == DRIVER["driver_registration_address"]
    assert record["phone"] == DRIVER["driver_phone"]


def test_save_without_name_reports_problem(crew, isolated_db):
    """Без ФИО сохранять нечего: запись не создаётся."""
    crew.driver_license.setText("99 36 123456")

    result = crew.save_to_directory()

    assert result.ok is False
    assert "ФИО" in result.error
    assert isolated_db.get_all_drivers() == []


def test_save_button_reports_result(crew, isolated_db, monkeypatch):
    """Кнопка «Сохранить в базу» сохраняет и показывает итог."""
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *args, **kwargs: shown.append(args)),
    )

    _fill(crew, DRIVER)
    crew.btn_save_driver.click()

    assert len(shown) == 1, "итоговый диалог не показан"
    assert len(isolated_db.get_all_drivers()) == 1


def test_documents_are_split_into_series_and_number(crew, isolated_db):
    """Документ одной строкой разбирается на серию и номер — как в справочнике."""
    _fill(crew, DRIVER)

    crew.save_to_directory()

    record = isolated_db.get_all_drivers()[0]
    assert (record["license_series"], record["license_number"]) == ("99 36", "123456")
    assert (record["passport_series"], record["passport_number"]) == ("18 22", "926830")


def test_saving_without_license_stores_empty_license(crew, isolated_db):
    """Водителя без ВУ можно сохранить: серия и номер остаются пустыми."""
    _fill(crew, {**DRIVER, "driver_license": ""})

    result = crew.save_to_directory()

    assert result.ok, result.error
    record = isolated_db.get_all_drivers()[0]
    assert record["license_series"] == ""
    assert record["license_number"] == ""


# ─────────────────────────────────────────────────────────────
# Загрузка из справочника
# ─────────────────────────────────────────────────────────────

def test_load_fills_all_nine_fields(crew, picker, isolated_db):
    """Загрузка водителя из базы → все девять полей вкладки заполнены."""
    record = {
        "full_name": DRIVER["driver_full_name"],
        "birth_date": DRIVER["driver_birth_date"],
        "passport_series": "18 22",
        "passport_number": "926830",
        "passport_issuer": DRIVER["driver_passport_issuer"],
        "passport_issue_date": DRIVER["driver_passport_issue_date"],
        "license_series": "99 36",
        "license_number": "123456",
        "license_issue_date": DRIVER["driver_license_issue_date"],
        "registration_address": DRIVER["driver_registration_address"],
        "phone": DRIVER["driver_phone"],
        "passport_code": "500-123",
    }
    record_id = isolated_db.save_driver(record)
    stored = isolated_db.load_driver(record_id)

    _created, fake = picker
    fake.picked = stored
    crew.load_from_directory()

    data = crew.get_data()
    assert set(data) == set(FORM_KEYS), "должны быть ровно девять полей"
    for key in FORM_KEYS:
        assert data[key] == DRIVER[key], key
    # Код подразделения в данные вкладки не входит, но кнопке «🔎» он нужен.
    assert crew.passport_code.text() == "500-123"


def test_load_skips_empty_values_without_leaving_stale_data(crew):
    """
    Пустые поля записи обнуляют форму.

    Иначе на вкладке остались бы документы и телефон предыдущего водителя,
    и в договор ушла бы их смесь.
    """
    _fill(crew, DRIVER)

    crew._fill_driver({
        "id": 7, "full_name": "Петров Пётр Петрович",
        "birth_date": "1985-05-05",
    })

    data = crew.get_data()
    assert data["driver_full_name"] == "Петров Пётр Петрович"
    assert data["driver_birth_date"] == "1985-05-05"
    assert data["driver_passport"] == "", "паспорт прошлого водителя убран"
    assert data["driver_license"] == ""
    assert data["driver_registration_address"] == ""
    assert data["driver_phone"] == ""
    assert data["driver_passport_issuer"] == ""


def test_load_accepts_record_dates_in_both_formats(crew):
    """Даты принимаются и в ISO, и в «дд.мм.гггг» — в базе встречаются обе."""
    crew._fill_driver({
        "id": 1,
        "full_name": "Иванов Иван Иванович",
        "birth_date": "01.01.1980",
        "passport_issue_date": "30.01.2023",
        "license_issue_date": "01.01.2020",
    })

    data = crew.get_data()
    assert data["driver_birth_date"] == "1980-01-01"
    assert data["driver_passport_issue_date"] == "2023-01-30"
    assert data["driver_license_issue_date"] == "2020-01-01"


def test_load_ignores_empty_record(crew):
    """Пустая запись ничего не меняет: форма остаётся как была."""
    _fill(crew, DRIVER)
    before = crew.get_data()

    crew._fill_driver({})

    assert crew.get_data() == before


# ─────────────────────────────────────────────────────────────
# Дубль: ключи ВУ / паспорт / ФИО + дата рождения
# ─────────────────────────────────────────────────────────────

def test_duplicate_license_updates_instead_of_creating(crew, isolated_db):
    """Дубль по водительскому удостоверению обновляет запись."""
    _fill(crew, DRIVER)
    first = crew.save_to_directory()
    assert first.updated is False

    crew.driver_phone.setText("+7 (999) 000-00-00")
    second = crew.save_to_directory()

    assert second.updated is True, "дубль по ВУ должен обновляться"
    assert second.record_id == first.record_id
    saved = isolated_db.get_all_drivers()
    assert len(saved) == 1
    assert saved[0]["phone"] == "+7 (999) 000-00-00"


def test_duplicate_by_passport_when_license_changes(crew, isolated_db):
    """Если ВУ в форме нет, дубль ищется по паспорту."""
    _fill(crew, DRIVER)
    first = crew.save_to_directory()

    # Новое удостоверение и то же ФИО: без паспорта это была бы новая запись.
    crew.driver_license.setText("")
    second = crew.save_to_directory()

    assert second.record_id == first.record_id
    assert second.updated is True
    assert len(isolated_db.get_all_drivers()) == 1


def test_duplicate_by_name_and_birth_date(crew, isolated_db):
    """Без документов дубль ищется по ФИО и дате рождения."""
    _fill(crew, {
        "driver_full_name": "Сидоров Семён Семёнович",
        "driver_birth_date": "1975-03-03",
        "driver_phone": "+7 (111) 111-11-11",
    })
    first = crew.save_to_directory()
    assert first.updated is False

    crew.driver_phone.setText("+7 (222) 222-22-22")
    second = crew.save_to_directory()

    assert second.record_id == first.record_id
    assert second.updated is True
    saved = isolated_db.get_all_drivers()
    assert len(saved) == 1
    assert saved[0]["phone"] == "+7 (222) 222-22-22"


def test_same_person_but_other_birth_date_is_new_record(crew, isolated_db):
    """Одно ФИО при разной дате рождения — разные люди, а не дубль."""
    _fill(crew, {"driver_full_name": "Иванов Иван Иванович",
                 "driver_birth_date": "1980-01-01"})
    assert crew.save_to_directory().updated is False

    crew.driver_birth_date.setDate(crew.driver_birth_date.date().addYears(-5))
    assert crew.save_to_directory().updated is False

    assert len(isolated_db.get_all_drivers()) == 2


def test_duplicate_keys_order():
    """Сила ключей дубля: ВУ → паспорт → ФИО + дата рождения."""
    record = contacts_module.driver_from_form(DRIVER)

    assert contacts_module.driver_key_names(record) == [
        "license", "passport", "person",
    ]

    without_license = contacts_module.driver_from_form(
        {**DRIVER, "driver_license": ""}
    )
    assert contacts_module.driver_key_names(without_license) == [
        "passport", "person",
    ]

    only_person = contacts_module.driver_from_form({
        "driver_full_name": DRIVER["driver_full_name"],
        "driver_birth_date": DRIVER["driver_birth_date"],
    })
    assert contacts_module.driver_key_names(only_person) == ["person"]

    empty = contacts_module.driver_from_form({})
    assert contacts_module.driver_key_names(empty) == []


def test_document_digits_are_compared_not_spacing():
    """Серия и номер сравниваются по цифрам: пробелы и дефисы не важны."""
    assert contacts_module.split_document("9936123456") == ("99 36", "123456")
    assert contacts_module.split_document("99-36-123456") == ("99 36", "123456")
    assert contacts_module.split_document("99 36 123456") == ("99 36", "123456")
    assert contacts_module.split_document("123456") == ("", "123456")
    assert contacts_module.split_document("9936") == ("99 36", "")
    assert contacts_module.split_document("") == ("", "")
    assert contacts_module.split_document("мусор") == ("", "")


def test_update_keeps_columns_the_tab_does_not_know(crew, isolated_db):
    """
    Обновление не теряет колонки, которых у вкладки аренды нет.

    Место рождения, код подразделения, категории и срок действия ВУ
    заполняются только в «Экспедиторстве»: сохранение из аренды их не стирает.
    """
    record = {**contacts_module.driver_from_form(DRIVER), **EXTRA_RECORD}
    isolated_db.save_driver(record)

    _fill(crew, {**DRIVER, "driver_phone": "+7 (999) 555-55-55"})
    crew.save_to_directory()

    saved = isolated_db.get_all_drivers()
    assert len(saved) == 1, "должна обновиться одна и та же запись"
    assert saved[0]["phone"] == "+7 (999) 555-55-55"
    for column, value in EXTRA_RECORD.items():
        assert saved[0][column] == value, column


def test_deleted_driver_is_restored_by_saving(crew, isolated_db):
    """Мягко удалённый водитель с тем же ВУ обновляется и возвращается."""
    _fill(crew, DRIVER)
    result = crew.save_to_directory()
    isolated_db.delete_driver(result.record_id)
    assert isolated_db.get_all_drivers() == []

    again = crew.save_to_directory()

    assert again.record_id == result.record_id
    assert again.updated is True
    assert [d["id"] for d in isolated_db.get_all_drivers()] == [result.record_id]


# ─────────────────────────────────────────────────────────────
# Общий справочник: обмен с «Экспедиторством»
# ─────────────────────────────────────────────────────────────

def test_driver_saved_in_arenda_is_visible_through_common_api(crew, isolated_db):
    """
    Сохранённый в аренде водитель читается общим API справочника.

    Тем же кодом его видит «Экспедиторство»: get_all_drivers и search_drivers —
    публичные функции db/database.py.
    """
    _fill(crew, DRIVER)
    result = crew.save_to_directory()

    by_id = [d for d in isolated_db.get_all_drivers() if d["id"] == result.record_id]
    assert by_id, "записи нет в общем списке водителей"
    assert by_id[0]["full_name"] == DRIVER["driver_full_name"]

    by_name = isolated_db.search_drivers("Иванов")
    assert result.record_id in [d["id"] for d in by_name]

    # Поиск по паспорту идёт по склейке «серия + пробел + номер».
    by_passport = isolated_db.search_drivers("18 22 926830")
    assert result.record_id in [d["id"] for d in by_passport]


def test_driver_from_expediting_is_loaded_into_arenda(crew, isolated_db):
    """
    Обратный обмен: запись, созданная как в «Экспедиторстве», попадает
    на вкладку «Экипаж».

    Запись создаётся общим save_driver — тем же вызовом, что в
    MainWindow._on_save_to_db, а на вкладку попадает результат
    find_driver_record (поиск дубля по документу).
    """
    driver_id = isolated_db.save_driver({
        "full_name": "Кузнецов Кузьма Кузьмич",
        "birth_date": "1970-07-07",
        "passport_series": "45 07",
        "passport_number": "112233",
        "license_series": "77 01",
        "license_number": "654321",
        "registration_address": "г. Тверь, ул. Ленина, д. 7",
        "phone": "+7 (482) 000-11-22",
    })

    record = contacts_module.find_driver_record({
        "license_series": "77 01", "license_number": "654321",
    })
    assert record is not None and record["id"] == driver_id

    crew._fill_driver(record)

    data = crew.get_data()
    assert data["driver_full_name"] == "Кузнецов Кузьма Кузьмич"
    assert data["driver_passport"] == "45 07 112233"
    assert data["driver_license"] == "77 01 654321"
    assert data["driver_registration_address"] == "г. Тверь, ул. Ленина, д. 7"
    assert data["driver_phone"] == "+7 (482) 000-11-22"


def test_arenda_uses_the_shared_drivers_table(isolated_db):
    """Справочник водителей один: отдельной таблицы для аренды нет."""
    conn = isolated_db.get_connection()
    try:
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert "drivers" in names
    assert not {name for name in names if "arenda" in name.lower()}, (
        "для аренды не должно быть отдельной таблицы водителей"
    )


# ─────────────────────────────────────────────────────────────
# Раскладка полей (чистые функции contacts.py)
# ─────────────────────────────────────────────────────────────

def test_driver_from_form_fills_all_record_columns():
    """Поля вкладки раскладываются по колонкам справочника без потерь."""
    record = contacts_module.driver_from_form(DRIVER)

    assert record["full_name"] == DRIVER["driver_full_name"]
    assert record["registration_address"] == DRIVER["driver_registration_address"]
    assert record["phone"] == DRIVER["driver_phone"]
    assert record["license_issue_date"] == DRIVER["driver_license_issue_date"]
    # Колонки, которых у вкладки нет, сюда не попадают: их при сохранении
    # подставляет merge_driver_records из существующей записи (иначе правка
    # вкладки аренды обнулила бы место рождения и категории ВУ).
    assert set(record) == set(contacts_module.DRIVER_RECORD_FIELDS)
    assert "birth_place" not in record
    assert "license_categories" not in record


def test_merge_driver_records_keeps_columns_of_the_tab_and_of_the_record():
    """Слияние: колонки вкладки — её, остальные — из существующей записи."""
    record = contacts_module.driver_from_form(DRIVER)
    existing = {
        "birth_place": "г. Москва", "license_categories": "B, C, E",
        "phone": "+7 (000) 000-00-00", "full_name": "Старое ФИО",
    }

    merged = contacts_module.merge_driver_records(existing, record)

    assert merged["birth_place"] == "г. Москва"
    assert merged["license_categories"] == "B, C, E"
    assert merged["full_name"] == DRIVER["driver_full_name"]
    assert merged["phone"] == DRIVER["driver_phone"]
    # Существующей записи нет — чужие колонки пустые, а не выдуманные.
    assert contacts_module.merge_driver_records(None, record)["birth_place"] == ""


def test_merge_organization_records_keeps_licence_of_the_carrier():
    """Лицензия перевозчика, введённая в «Экспедиторстве», сохраняется."""
    record = contacts_module.organization_from_form({
        "full_name": "ООО «Перевозчик»",
        "inn": "7707654321",
        "address": "г. Москва, ул. Складская, д. 5",
    })

    merged = contacts_module.merge_organization_records(
        {"license_number": "АК-77-123456", "license_date": "2020-01-01"}, record
    )

    assert merged["license_number"] == "АК-77-123456"
    assert merged["license_date"] == "2020-01-01"
    assert merged["full_name"] == "ООО «Перевозчик»"
    assert merged["legal_address"] == "г. Москва, ул. Складская, д. 5"


def test_driver_to_form_skips_empty_values():
    """Пустые колонки не подставляются: они не должны стирать ввод."""
    values = contacts_module.driver_to_form(
        {"full_name": "", "phone": "  ", "birth_date": "", "birth_place": "X"}
    )

    assert values == {}


def test_normalize_date_keeps_unknown_value():
    """Разобрать дату не удалось — значение остаётся как есть."""
    assert contacts_module.normalize_date("1980-01-01") == "1980-01-01"
    assert contacts_module.normalize_date("01.01.1980") == "1980-01-01"
    assert contacts_module.normalize_date("") == ""
    assert contacts_module.normalize_date(None) == ""
    assert contacts_module.normalize_date("не помню") == "не помню"


def test_join_document():
    """Строка документа собирается из серии и номера без лишних пробелов."""
    assert contacts_module.join_document("99 36", "123456") == "99 36 123456"
    assert contacts_module.join_document("", "123456") == "123456"
    assert contacts_module.join_document("99 36", "") == "99 36"
    assert contacts_module.join_document("", "") == ""
