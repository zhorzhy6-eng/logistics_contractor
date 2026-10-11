#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты границ поля «Место рождения» (шаг «Четыре бага», баг C).

ЧТО ЗА БАГ
---------
На странице паспорта OCR отдаёт «Место рождения …» одной строкой, а
следующей — «Паспорт выдан …» без двоеточия. Разбор брал значение вслепую
— до трёх строк подряд (`_following(lines, r"место\\s+рождения", 3)`) — и в
`birth_place` попадали выдача, дата выдачи и код подразделения:

    Место рождения г. Хабаровск, ул. Садовая
    Паспорт выдан Отделом УФМС России по г. Москве
    → birth_place = «г. Хабаровск, ул. Садовая Паспорт выдан Отделом
      УФМС России по г. Москве»

В стресс-тесте (1000 синтетических документов) так ломалось 210 случаев:
значение находилось, но сравнивалось с эталоном как «иное» — поле считалось
потерянным.

КАК ПОЧИНЕНО
------------
Значение обрывается на СЛЕДУЮЩЕЙ МЕТКЕ поля (`_FIELD_LABEL_IN_LINE` в
core/document_fields.py): «Паспорт (РФ)», «выдан», «Кем выдан», «Дата
выдачи», «Код подразделения», «Место рождения», «Дата рождения», «Адрес
регистрации», «Водительское удостоверение», «Телефон», «Пол», а также
номерной пункт бланка ВУ («1 Фамилия») и заголовок блока.

Проверки идут по настоящему конвейеру разбора
(`core.document_import_service.extract_local_fields`) — тому же, которым
пользуется распознавание в приложении. Данные синтетические, реальных
документов и ПДн здесь нет.
"""

import pytest

from core.document_import_service import extract_local_fields

#: Ожидаемое место рождения во всех кейсах, кроме специально оговорённых.
BIRTH_PLACE = "г. Пример, ул. Тестовая"


def _driver(text: str) -> dict:
    """Карточка водителя из текста документа (первая запись)."""
    fields = extract_local_fields(text)
    driver = fields.get("driver")
    assert driver, f"раздел «driver» не найден: {list(fields)}"
    return driver[0]


#: (имя кейса, строки ПОСЛЕ места рождения, ожидаемое значение).
#: Каждая пара — «метка, которая раньше попадала в значение», и то, что
#: должно остаться в поле.
STOP_LABEL_CASES = (
    (
        "passport_issued",
        ["Паспорт выдан Отделом УФМС России по г. Примеру"],
        BIRTH_PLACE,
    ),
    (
        "passport_short",
        ["Паспорт 45 12 345678"],
        BIRTH_PLACE,
    ),
    (
        "issued_only",
        ["выдан Отделом МВД России по Примерской области"],
        BIRTH_PLACE,
    ),
    (
        "issued_by",
        ["Кем выдан: Отделом УФМС России по г. Примеру"],
        BIRTH_PLACE,
    ),
    (
        "issue_date",
        ["Дата выдачи 01.02.2020"],
        BIRTH_PLACE,
    ),
    (
        "subdivision_code",
        ["Код подразделения 123-456"],
        BIRTH_PLACE,
    ),
    (
        "registration_address",
        ["Адрес регистрации: г. Пример, ул. Другая, д. 1"],
        BIRTH_PLACE,
    ),
    (
        "phone",
        ["Телефон: +7 (999) 000-11-22"],
        BIRTH_PLACE,
    ),
    (
        "driving_licence",
        ["Водительское удостоверение: 12 34 567890"],
        BIRTH_PLACE,
    ),
    (
        "driving_licence_short",
        ["ВУ: 12 34 567890"],
        BIRTH_PLACE,
    ),
    (
        "glued_issue_line",
        ["Дата выдачи 01.02.2020 Код подразделения 123-456"],
        BIRTH_PLACE,
    ),
    (
        "end_of_document",
        [],
        BIRTH_PLACE,
    ),
)

#: Общая «шапка» документа: строки ДО места рождения.
PASSPORT_HEAD = (
    "РОССИЙСКАЯ ФЕДЕРАЦИЯ",
    "45 12 345678",
    "ПАСПОРТ",
    "Фамилия ПРИМЕРОВ",
    "Имя Тест",
    "Отчество Тестович",
    "Пол МУЖ. Дата рождения 03.04.1990",
)


@pytest.mark.parametrize(
    "tail",
    [case[1] for case in STOP_LABEL_CASES],
    ids=[case[0] for case in STOP_LABEL_CASES],
)
def test_birth_place_stops_before_the_next_field_label(tail):
    """Следующая метка поля обрывает значение: место рождения — только адрес."""
    text = "\n".join(PASSPORT_HEAD + (f"Место рождения {BIRTH_PLACE}",) + tuple(tail))

    assert _driver(text)["birth_place"] == BIRTH_PLACE


def test_birth_place_at_the_end_of_the_document():
    """Следующего поля нет — значение берётся до конца текста."""
    text = "\n".join(PASSPORT_HEAD + (f"Место рождения {BIRTH_PLACE}",))

    assert _driver(text)["birth_place"] == BIRTH_PLACE


def test_birth_place_with_multiline_value():
    """Многострочное значение берётся ЦЕЛИКОМ — до следующей метки."""
    text = "\n".join(PASSPORT_HEAD + (
        "Место рождения г. Москва,",
        "Ленинградский р-н",
        "Паспорт выдан Отделом УФМС России по г. Москве",
    ))

    assert _driver(text)["birth_place"] == "г. Москва, Ленинградский р-н"


def test_birth_place_with_three_lines_stops_at_the_label():
    """Три строки значения и метка после них: лишнего в поле нет."""
    text = "\n".join(PASSPORT_HEAD + (
        "Место рождения г. Москва,",
        "Ленинградский р-н,",
        "ул. Тестовая, д. 1",
        "Дата выдачи 01.02.2020",
    ))

    assert _driver(text)["birth_place"] == (
        "г. Москва, Ленинградский р-н, ул. Тестовая, д. 1"
    )


def test_empty_birth_place_gives_empty_value():
    """Пустого места рождения нет — поле пустое, ничего не подставляется."""
    text = "\n".join(PASSPORT_HEAD + (
        "Паспорт выдан Отделом УФМС России по г. Примеру",
        "Дата выдачи 01.02.2020",
    ))
    driver = _driver(text)

    assert driver.get("birth_place", "") == ""


def test_birth_place_without_a_value_in_the_line():
    """Метка отдельной строкой: значение — следующая строка, не метка."""
    text = "\n".join(PASSPORT_HEAD + (
        "Место рождения",
        "г. Пример, ул. Тестовая",
        "Паспорт выдан Отделом УФМС России по г. Примеру",
    ))

    assert _driver(text)["birth_place"] == BIRTH_PLACE


def test_birth_place_does_not_break_other_passport_fields():
    """
    Регресс: соседние поля паспорта разбираются как раньше.

    Обрыв значения по метке не должен «съедать» выдачу, дату выдачи и код
    подразделения — они стоят НИЖЕ места рождения.
    """
    text = "\n".join(PASSPORT_HEAD + (
        f"Место рождения {BIRTH_PLACE}",
        "Паспорт выдан Отделом УФМС России по г. Примеру",
        "Дата выдачи 01.02.2020 Код подразделения 123-456",
    ))
    driver = _driver(text)

    assert driver["birth_place"] == BIRTH_PLACE
    assert driver["passport_issuer"] == "Отделом УФМС России по г. Примеру"
    assert driver["passport_issue_date"] == "01.02.2020"
    assert driver["passport_code"] == "123-456"
    assert driver["birth_date"] == "03.04.1990"
    assert driver["full_name"] == "ПРИМЕРОВ Тест Тестович"


def test_registration_address_still_reads_its_own_block():
    """
    Регресс: адрес регистрации берётся целиком, вместе с домом и квартирой.

    Текст — карточка водителя (строка «Водитель:»), поэтому разбор идёт
    ветвью `_driver_card`, а не паспортной: в ней место рождения читается
    `_label_value`, и проверка держит её поведение явным.
    """
    text = "\n".join((
        "Водитель: Примеров Тест Тестович",
        "дата рождения: 03.04.1990",
        f"место рождения: {BIRTH_PLACE}",
        "Адрес регистрации: г. Пример, ул. Другая, д. 1, кв. 2",
        "Телефон: +7 (999) 000-11-22",
    ))
    driver = _driver(text)

    assert driver["registration_address"].startswith("г. Пример, ул. Другая, д. 1")
    assert driver["birth_place"] == BIRTH_PLACE
