#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты ключа папки рейса для плоской формы Хавалов (ШАГ «Папка на рейс»).

У Хавалов выход — .xlsx, и ContractData не собирается: генератор читает схему
промпта напрямую, плоскими полями (ui/windows/havaly/data.py). Поэтому общая
функция имени папки (core/contracts/paths.py) получает от него знакомую форму
через адаптер ``folder_key_from_form``: водитель складывается из
``driver_last_name`` / ``driver_first_name`` / ``driver_middle_name``,
маршрут — из городов погрузки и доставки, дата берётся как есть.

Проверяется:
  * форма ContractData проходит через адаптер БЕЗ изменений (её читают
    DOCX-типы, и адаптер не должен её портить);
  * плоская схема даёт тот же ключ, а значит и то же имя папки, что у
    договора Экспедиторства по этому рейсу;
  * пустые поля имени не выдумывают.

Данные синтетические, реальных ПДн нет.
"""

import pytest

from core.contracts.paths import contract_folder_name, folder_key_from_form

#: Форма ContractData: её отдают сборщики Формики, Логистикса и аренды.
#: Маршрут и дата — те же, что в плоской форме ниже: документы одного рейса.
CONTRACT_DATA = {
    "driver": {"full_name": "Иванов Иван Иванович"},
    "contract": {"route": "Калуга-Москва", "date": "2026-10-08"},
    "vehicles": [{"vin": "X"}],
}

#: Плоская форма Хавалов: тридцать полей блока "zayavka" (схема промпта).
#: Дата — в том виде, в каком её отдаёт вкладка заявки (ДД.ММ.ГГГГ).
FLAT_FORM = {
    "zayavka": {
        "date": "08.10.2026",
        "lot_number": "LOT-42",
        "loading_city": "Калуга",
        "loading_point": "ул. Промышленная, 12",
        "unloading_city": "Москва",
        "unloading_point": "Складской проезд, 5",
        "driver_last_name": "Иванов",
        "driver_first_name": "Иван",
        "driver_middle_name": "Иванович",
    },
    "vehicles": [{"vin": "X"}],
}


def test_contract_data_form_passes_through():
    """Форма ContractData возвращается как есть — адаптер её не трогает."""
    assert folder_key_from_form(CONTRACT_DATA) is CONTRACT_DATA


def test_flat_form_gives_same_key_as_contract_data():
    """
    Плоская схема Хавалов даёт то же имя папки, что форма ContractData.

    Это и есть связь документов одного рейса: заявка Хавалов ляжет рядом с
    договором, если водитель, маршрут и дата совпали.
    """
    assert contract_folder_name(folder_key_from_form(FLAT_FORM)) == \
        contract_folder_name(CONTRACT_DATA)


def test_flat_form_reads_driver_route_and_date():
    """Ключ собирается из полей заявки: водитель, города, дата."""
    key = folder_key_from_form(FLAT_FORM)

    assert key["driver"]["full_name"] == "Иванов Иван Иванович"
    assert key["contract"]["route"] == "Калуга-Москва"
    assert key["contract"]["date"] == "08.10.2026"
    assert contract_folder_name(key) == "Иванов_И.И._Калуга-Москва_08.10.2026"


def test_flat_form_missing_middle_name():
    """Отчества нет — инициалы только те, что есть; лишних пробелов нет."""
    flat = {"zayavka": {
        "date": "2026-10-08",
        "driver_last_name": "Петров",
        "driver_first_name": "Пётр",
    }}

    assert folder_key_from_form(flat)["driver"]["full_name"] == "Петров Пётр"
    assert contract_folder_name(folder_key_from_form(flat)) == \
        "Петров_П._08.10.2026"


def test_flat_form_without_driver_and_route_leaves_key_empty():
    """
    Нет водителя и городов — ключ пустой, и это не ошибка.

    Имя папки в этом случае соберёт сама contract_folder_name: «водителя
    нет», а дата заявки в ключе остаётся — папка нужна всегда.
    """
    key = folder_key_from_form({"zayavka": {"date": "2026-10-08"}})

    assert key == {"contract": {"date": "2026-10-08"}}
    assert contract_folder_name(key) == "Без_водителя_08.10.2026"


def test_flat_form_reads_point_when_city_is_empty():
    """Города нет — берётся пункт: маршрут всё равно нужен."""
    flat = {"zayavka": {
        "date": "2026-10-08",
        "loading_point": "Калуга",
        "unloading_city": "Москва",
    }}

    assert folder_key_from_form(flat)["contract"]["route"] == "Калуга-Москва"


def test_no_form_at_all_gives_empty_key():
    """Пустой вход и мусор: ключ пустой, исключения нет."""
    for source in (None, {}, {"zayavka": None}, {"zayavka": {}}, "мусор",
                   {"vehicles": []}):
        assert folder_key_from_form(source) == {}


@pytest.mark.parametrize("bad_date", ["", None])
def test_flat_form_without_date_gives_no_date_key(bad_date):
    """
    Даты нет — ключа с датой нет, сегодняшнюю подставит имя папки.

    Непустой мусор в поле даты не выбрасывается: имя папки его не разберёт
    и возьмёт сегодняшний день (см. tests/test_paths.py), но решать это
    здесь, а не отбрасывать молча, — дело одной функции.
    """
    flat = {"zayavka": {"date": bad_date, "loading_city": "Калуга"}}

    assert "date" not in folder_key_from_form(flat).get("contract", {})