#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Привязка водителей к перевозчикам: база (ШАГ «Привязка водителей
к перевозчикам», часть B).

До этого шага связь «водитель ↔ перевозчик» существовала ТОЛЬКО через
договоры (contracts.driver_id + contracts.carrier_id), и в справочнике
водителей не было видно, на кого он работает. Теперь:

  * `drivers.default_carrier_id` — основной перевозчик водителя;
  * `driver_carriers` — история работы (started_at / ended_at; запись без
    ended_at считается активной).

Что проверяется:

  * привязка создаётся один раз: повторный вызов для того же перевозчика
    не плодит дубли, а возвращает id существующей записи;
  * переход к другому перевозчику ЗАКРЫВАЕТ прежнюю активную связь —
    двух «текущих» перевозчиков в истории не бывает;
  * закрытие связи ставит дату, но запись НЕ удаляет (это история);
  * выборки истории и водителей перевозчика работают и с фильтром
    «только активные»;
  * очистка основного перевозчика историю не трогает;
  * мягкое удаление ничего не рвёт: ни `default_carrier_id` при удалении
    перевозчика, ни историю при удалении водителя;
  * `update_driver` без ключа `default_carrier_id` привязку НЕ обнуляет
    (иначе сохранение из аренды стирало бы её молча).

База — временная (fixture `isolated_db`), данные синтетические, ПДн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

#: Синтетический водитель: выдуманное ФИО и документы.
DRIVER = {
    "full_name": "Иванов Иван Иванович",
    "birth_date": "1980-01-01",
    "birth_place": "г. Москва",
    "passport_series": "60 26",
    "passport_number": "123456",
    "passport_issue_date": "2023-01-30",
    "passport_issuer": "Отделом УФМС России по г. Москве",
    "passport_code": "500-123",
    "registration_address": "г. Москва, ул. Тестовая, д. 1",
    "license_series": "99 36",
    "license_number": "123456",
    "license_issue_date": "2020-01-01",
    "license_expiry_date": "2030-01-01",
    "license_categories": "B, C, E",
    "phone": "+7 (999) 123-45-67",
}


@pytest.fixture
def carrier_a(isolated_db):
    """Первый перевозчик (ООО «Альфа»)."""
    return isolated_db.save_organization(
        {"full_name": "ООО «Альфа»", "inn": "7701234567"}, is_carrier=True
    )


@pytest.fixture
def carrier_b(isolated_db):
    """Второй перевозчик (ООО «Бета») — «новая работа» водителя."""
    return isolated_db.save_organization(
        {"full_name": "ООО «Бета»", "inn": "7709876543"}, is_carrier=True
    )


@pytest.fixture
def driver_id(isolated_db):
    """Водитель без привязки."""
    return isolated_db.save_driver(dict(DRIVER))


# ─────────────────────────────────────────────────────────────
# B.1: link_driver_to_carrier
# ─────────────────────────────────────────────────────────────

def test_link_driver_to_carrier_creates_record(isolated_db, driver_id, carrier_a):
    """Первая привязка создаёт запись истории и активна (ended_at пуст)."""
    link_id = isolated_db.link_driver_to_carrier(driver_id, carrier_a)

    assert link_id > 0

    links = isolated_db.get_driver_carriers(driver_id)
    assert len(links) == 1
    assert links[0]["id"] == link_id
    assert links[0]["carrier_id"] == carrier_a
    assert links[0]["carrier_name"] == "ООО «Альфа»"
    assert not links[0]["ended_at"], "свежая связь должна быть активной"
    assert links[0]["started_at"], "дата начала подставляется сама"


def test_link_uses_given_started_at(isolated_db, driver_id, carrier_a):
    """Дата начала из аргумента сохраняется как есть."""
    isolated_db.link_driver_to_carrier(
        driver_id, carrier_a, started_at="2026-02-01"
    )

    assert isolated_db.get_driver_carriers(driver_id)[0]["started_at"] == "2026-02-01"


def test_link_twice_same_carrier_returns_same_id(isolated_db, driver_id, carrier_a):
    """Повторная привязка к тому же перевозчику дубль не создаёт."""
    first = isolated_db.link_driver_to_carrier(driver_id, carrier_a)
    second = isolated_db.link_driver_to_carrier(driver_id, carrier_a)

    assert first == second
    assert len(isolated_db.get_driver_carriers(driver_id)) == 1


def test_link_second_carrier_closes_previous(
    isolated_db, driver_id, carrier_a, carrier_b
):
    """Связь с новым перевозчиком закрывает активную связь со старым."""
    isolated_db.link_driver_to_carrier(
        driver_id, carrier_a, started_at="2026-01-10"
    )
    isolated_db.link_driver_to_carrier(
        driver_id, carrier_b, started_at="2026-03-05"
    )

    links = isolated_db.get_driver_carriers(driver_id)
    assert len(links) == 2, "история сохраняется, а не переписывается"

    # Активная — только новая связь.
    active = isolated_db.get_driver_carriers(driver_id, active_only=True)
    assert [link["carrier_name"] for link in active] == ["ООО «Бета»"]

    by_carrier = {link["carrier_id"]: link for link in links}
    assert by_carrier[carrier_a]["ended_at"] == "2026-03-05"
    assert not by_carrier[carrier_b]["ended_at"]


def test_link_closed_by_new_one_keeps_started_at(
    isolated_db, driver_id, carrier_a, carrier_b
):
    """Закрытая прежняя связь сохраняет свою дату начала."""
    isolated_db.link_driver_to_carrier(
        driver_id, carrier_a, started_at="2026-01-10"
    )
    isolated_db.link_driver_to_carrier(
        driver_id, carrier_b, started_at="2026-03-05"
    )

    by_carrier = {
        link["carrier_id"]: link
        for link in isolated_db.get_driver_carriers(driver_id)
    }
    assert by_carrier[carrier_a]["started_at"] == "2026-01-10"


# ─────────────────────────────────────────────────────────────
# B.1: unlink_driver_from_carrier
# ─────────────────────────────────────────────────────────────

def test_unlink_sets_ended_at(isolated_db, driver_id, carrier_a):
    """Закрытие связи ставит дату окончания и убирает её из активных."""
    isolated_db.link_driver_to_carrier(driver_id, carrier_a)

    assert isolated_db.unlink_driver_from_carrier(
        driver_id, carrier_a, ended_at="2026-04-01"
    ) is True

    links = isolated_db.get_driver_carriers(driver_id)
    assert len(links) == 1, "запись истории не удаляется"
    assert links[0]["ended_at"] == "2026-04-01"
    assert isolated_db.get_driver_carriers(driver_id, active_only=True) == []


def test_unlink_without_date_uses_today(isolated_db, driver_id, carrier_a):
    """Пустая дата окончания — сегодняшняя (значение не выдумывается)."""
    import datetime

    isolated_db.link_driver_to_carrier(driver_id, carrier_a)
    isolated_db.unlink_driver_from_carrier(driver_id, carrier_a)

    today = datetime.date.today().isoformat()
    assert isolated_db.get_driver_carriers(driver_id)[0]["ended_at"] == today


def test_unlink_without_active_link_returns_false(isolated_db, driver_id, carrier_a):
    """Закрывать нечего — False, а не ошибка."""
    assert isolated_db.unlink_driver_from_carrier(driver_id, carrier_a) is False


# ─────────────────────────────────────────────────────────────
# B.1: выборки
# ─────────────────────────────────────────────────────────────

def test_get_driver_carriers_returns_history(
    isolated_db, driver_id, carrier_a, carrier_b
):
    """История отдаётся с названием перевозчика и активными впереди."""
    isolated_db.link_driver_to_carrier(
        driver_id, carrier_a, started_at="2026-01-10"
    )
    isolated_db.link_driver_to_carrier(
        driver_id, carrier_b, started_at="2026-03-05"
    )

    links = isolated_db.get_driver_carriers(driver_id)

    assert [link["carrier_name"] for link in links] == [
        "ООО «Бета»", "ООО «Альфа»",
    ]
    assert all(link["driver_id"] == driver_id for link in links)
    assert links[0]["carrier_inn"] == "7709876543"


def test_get_driver_carriers_of_unknown_driver(isolated_db):
    """У водителя без истории — пустой список, без исключения."""
    assert isolated_db.get_driver_carriers(9999) == []
    assert isolated_db.get_driver_carriers(9999, active_only=True) == []


def test_get_driver_carriers_active_only(
    isolated_db, driver_id, carrier_a, carrier_b
):
    """active_only=True отдаёт только действующие связи."""
    isolated_db.link_driver_to_carrier(driver_id, carrier_a)
    isolated_db.link_driver_to_carrier(driver_id, carrier_b)

    active = isolated_db.get_driver_carriers(driver_id, active_only=True)

    assert len(active) == 1
    assert active[0]["carrier_id"] == carrier_b
    assert active[0]["ended_at"] in (None, "")


def test_get_carrier_drivers_active_only(
    isolated_db, driver_id, carrier_a, carrier_b
):
    """У перевозчика видны его водители; ушедший — только без фильтра."""
    isolated_db.link_driver_to_carrier(driver_id, carrier_a)
    isolated_db.link_driver_to_carrier(driver_id, carrier_b)

    # У Альфы водитель уже не работает: связь закрыта переходом к Бете.
    assert isolated_db.get_carrier_drivers(carrier_a) == []

    all_a = isolated_db.get_carrier_drivers(carrier_a, active_only=False)
    assert [driver["full_name"] for driver in all_a] == ["Иванов Иван Иванович"]
    assert all_a[0]["carrier_id"] == carrier_a
    assert all_a[0]["ended_at"], "связь закрыта"

    active_b = isolated_db.get_carrier_drivers(carrier_b)
    assert [driver["full_name"] for driver in active_b] == ["Иванов Иван Иванович"]
    assert active_b[0]["id"] == driver_id


def test_get_carrier_drivers_has_driver_columns(isolated_db, driver_id, carrier_a):
    """Запись водителя приходит целиком (паспорт, телефон, привязка)."""
    isolated_db.link_driver_to_carrier(driver_id, carrier_a)

    driver = isolated_db.get_carrier_drivers(carrier_a)[0]

    assert driver["passport_series"] == "60 26"
    assert driver["phone"] == "+7 (999) 123-45-67"
    assert driver["default_carrier_id"] is None
    assert driver["started_at"]


# ─────────────────────────────────────────────────────────────
# B.1–B.2: основной перевозчик водителя
# ─────────────────────────────────────────────────────────────

def test_set_default_carrier_updates_drivers(isolated_db, driver_id, carrier_a):
    """Основной перевозчик записывается в drivers и в историю."""
    assert isolated_db.set_default_carrier(driver_id, carrier_a) is True

    assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_a
    links = isolated_db.get_driver_carriers(driver_id, active_only=True)
    assert [link["carrier_id"] for link in links] == [carrier_a]


def test_set_default_carrier_twice_keeps_one_link(
    isolated_db, driver_id, carrier_a
):
    """Повторная установка того же перевозчика второй связи не создаёт."""
    isolated_db.set_default_carrier(driver_id, carrier_a)
    isolated_db.set_default_carrier(driver_id, carrier_a)

    assert len(isolated_db.get_driver_carriers(driver_id)) == 1


def test_set_default_carrier_none_clears(isolated_db, driver_id, carrier_a):
    """carrier_id = None очищает поле, но историю работы не трогает."""
    isolated_db.set_default_carrier(driver_id, carrier_a)

    assert isolated_db.set_default_carrier(driver_id, None) is True

    assert isolated_db.load_driver(driver_id)["default_carrier_id"] is None
    assert len(isolated_db.get_driver_carriers(driver_id)) == 1, (
        "история работы — не привязка формы: она остаётся"
    )


def test_save_driver_with_carrier(isolated_db, carrier_a):
    """save_driver принимает default_carrier_id из данных вкладки."""
    driver_id = isolated_db.save_driver(
        {**DRIVER, "default_carrier_id": carrier_a}
    )

    assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_a


def test_save_driver_without_carrier_gets_null(isolated_db):
    """Старый вызов (без ключа) заводит водителя без привязки."""
    driver_id = isolated_db.save_driver(dict(DRIVER))

    assert isolated_db.load_driver(driver_id)["default_carrier_id"] is None


def test_update_driver_sets_carrier(isolated_db, driver_id, carrier_a):
    """update_driver обновляет привязку, когда ключ пришёл в данных."""
    assert isolated_db.update_driver(
        driver_id, {**DRIVER, "default_carrier_id": carrier_a}
    )

    assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_a


def test_update_driver_without_key_keeps_carrier(
    isolated_db, driver_id, carrier_a
):
    """
    Ключа в данных нет — привязка не обнуляется.

    Так сохранение из аренды (`merge_driver_records` собирает запись из
    колонок своей вкладки, ключа default_carrier_id в ней нет) не стирает
    привязку, выставленную в «Экспедиторстве».
    """
    isolated_db.set_default_carrier(driver_id, carrier_a)

    assert isolated_db.update_driver(
        driver_id, {"full_name": "Петров Пётр Петрович"}
    )
    assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_a

    # А явный None (оператор выбрал «— не указан —») привязку снимает.
    assert isolated_db.update_driver(
        driver_id, {"full_name": "Петров Пётр Петрович", "default_carrier_id": None}
    )
    assert isolated_db.load_driver(driver_id)["default_carrier_id"] is None


def test_update_driver_ignores_garbage_carrier(isolated_db, driver_id, carrier_a):
    """Мусор вместо id перевозчика сохраняется как «привязки нет»."""
    isolated_db.update_driver(
        driver_id, {"full_name": DRIVER["full_name"], "default_carrier_id": "нет"}
    )

    assert isolated_db.load_driver(driver_id)["default_carrier_id"] is None


# ─────────────────────────────────────────────────────────────
# B.3: списки и поиск отдают название перевозчика
# ─────────────────────────────────────────────────────────────

def test_get_all_drivers_with_carrier_name(isolated_db, driver_id, carrier_a):
    """Флаг with_carrier_name добавляет колонку carrier_name."""
    isolated_db.set_default_carrier(driver_id, carrier_a)

    with_name = isolated_db.get_all_drivers(with_carrier_name=True)
    assert [driver["carrier_name"] for driver in with_name] == ["ООО «Альфа»"]

    without = isolated_db.get_all_drivers()
    assert "carrier_name" not in without[0], "по умолчанию набор колонок прежний"


def test_search_drivers_with_carrier_name_by_name(isolated_db, driver_id, carrier_a):
    """Словесный поиск (путь FTS5) тоже отдаёт перевозчика."""
    isolated_db.set_default_carrier(driver_id, carrier_a)

    found = isolated_db.search_drivers("Иванов", with_carrier_name=True)

    assert [driver["carrier_name"] for driver in found] == ["ООО «Альфа»"]


def test_search_drivers_with_carrier_name_by_passport(
    isolated_db, driver_id, carrier_a
):
    """Поиск по паспорту (путь LIKE) тоже отдаёт перевозчика."""
    isolated_db.set_default_carrier(driver_id, carrier_a)

    found = isolated_db.search_drivers("60 26 123456", with_carrier_name=True)

    assert len(found) == 1
    assert found[0]["carrier_name"] == "ООО «Альфа»"


def test_search_drivers_without_carrier_keeps_columns(isolated_db, driver_id):
    """Старый вызов search_drivers не меняет набор ключей."""
    found = isolated_db.search_drivers("Иванов")

    assert found and "carrier_name" not in found[0]
    assert found[0]["default_carrier_id"] is None


# ─────────────────────────────────────────────────────────────
# B.4–B.5: мягкое удаление ничего не рвёт
# ─────────────────────────────────────────────────────────────

def test_soft_delete_carrier_keeps_link_in_drivers(isolated_db, driver_id, carrier_a):
    """После мягкого удаления перевозчика привязка водителя остаётся."""
    isolated_db.set_default_carrier(driver_id, carrier_a)

    assert isolated_db.delete_organization(carrier_a, is_carrier=True) is True

    assert isolated_db.load_driver(driver_id)["default_carrier_id"] == carrier_a
    links = isolated_db.get_driver_carriers(driver_id)
    assert [link["carrier_id"] for link in links] == [carrier_a]
    assert links[0]["carrier_name"] == "ООО «Альфа»", (
        "история показывает название и у убранного из справочника перевозчика"
    )


def test_soft_delete_driver_keeps_history(isolated_db, driver_id, carrier_a):
    """После мягкого удаления водителя история работы остаётся."""
    isolated_db.set_default_carrier(driver_id, carrier_a)

    assert isolated_db.delete_driver(driver_id) is True

    assert isolated_db.get_all_drivers() == [], "удалённый скрыт из списка"
    assert len(isolated_db.get_driver_carriers(driver_id)) == 1

    # Восстановление возвращает и водителя, и видимую историю.
    assert isolated_db.restore_driver(driver_id) is True
    assert [driver["id"] for driver in isolated_db.get_all_drivers()] == [driver_id]
    assert len(isolated_db.get_driver_carriers(driver_id)) == 1


def test_soft_deleted_driver_still_listed_for_carrier(isolated_db, driver_id, carrier_a):
    """Перевозчик видит своего водителя и после мягкого удаления."""
    isolated_db.link_driver_to_carrier(driver_id, carrier_a)
    isolated_db.delete_driver(driver_id)

    assert [driver["full_name"] for driver in isolated_db.get_carrier_drivers(carrier_a)] \
        == ["Иванов Иван Иванович"]
