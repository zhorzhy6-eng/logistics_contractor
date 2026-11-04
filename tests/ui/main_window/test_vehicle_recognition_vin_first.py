#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Распознавание списка ТС формата «VIN + пробел + Марка» (живой случай).

Оператор вставил в распознавание столбик из таблицы: в каждой строке VIN,
пробел и марка — «LMGJU3G83T1067383 GS8IIFL». У трёх машин VIN не помещался
в ячейку, и последний символ при копировании уехал на отдельную строку
(«LMGAE3G58T100564 GS4» + «3»).

Что сделала модель: оторвала VIN-ы от марок, марки отправила в конец
списка отдельными «машинами», а одиночные цифры сделала строками таблицы.
В итоге вместо 7 машин — 16 записей, из них ни одной верной пары.

Здесь проверяется весь путь:
  * текст, разрезанный переносом, склеивается в 7 пар VIN-Марка;
  * ответ модели «VIN-ы отдельно, марки отдельно, цифры отдельно» даёт
    ровно 7 машин с правильными парами;
  * запись без настоящего 17-символьного VIN (оторванная марка, обрывок
    VIN, цифры) в списке по строкам машиной не считается;
  * в обычном документе (не список) невалидный VIN не выбрасывается —
    его подсвечивает таблица (tests/ui/tabs/test_vehicles_vin_check.py),
    а отбрасывается только запись без единой буквы;
  * промпт требует ровно эту пару на строку при ЛЮБОМ порядке VIN/марки;
  * таблица MainWindow заполняется 7 строками без пустых и мусорных.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core.gigachat_client import GigaChatClient, normalized_source_text  # noqa: E402
from core.recognizer import DataMapper  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


#: Дословный текст оператора со скриншота: 7 машин и три цифры-переноса.
#: VIN у трёх последних машин разрезан: 16 символов на строке с маркой,
#: 17-й символ — отдельной строкой.
OPERATOR_TEXT = (
    "LMGJU3G83T1067383 GS8IIFL\n"
    "LMGJU3G89T1065928 GS8IIFL\n"
    "LMGJU3G88T1066097 GS8IIFL\n"
    "LMGJU3G8XT1066232 GS8IIFL\n"
    "LMGAE3G58T100564 GS4\n"
    "3\n"
    "LMGAE3G85T100527 GS4\n"
    "1\n"
    "LMGAE3G5XT100557 GS4\n"
    "7\n"
)

#: Ожидаемый результат: ровно семь машин, VIN склеены, марки — свои.
EXPECTED = [
    ("LMGJU3G83T1067383", "GS8IIFL"),
    ("LMGJU3G89T1065928", "GS8IIFL"),
    ("LMGJU3G88T1066097", "GS8IIFL"),
    ("LMGJU3G8XT1066232", "GS8IIFL"),
    ("LMGAE3G58T1005643", "GS4"),
    ("LMGAE3G85T1005271", "GS4"),
    ("LMGAE3G5XT1005577", "GS4"),
]

#: Что реально вернула модель: 7 VIN без марок, 7 марок отдельными
#: «машинами» и три строки только из цифр.
BROKEN_ANSWER = (
    [{"vin": vin, "brand_model": ""} for vin, _brand in EXPECTED]
    + [{"vin": "", "brand_model": brand} for _vin, brand in EXPECTED]
    + [{"vin": "", "brand_model": number} for number in ("3", "1", "7")]
)

#: Ответ модели, которая увидела только четыре целых VIN (остальные ей
#: пришли разрезанными): три машины должны восстановиться из текста.
PARTIAL_ANSWER = (
    [{"vin": vin, "brand_model": ""} for vin, _brand in EXPECTED[:4]]
    + [{"vin": "", "brand_model": brand} for _vin, brand in EXPECTED]
    + [{"vin": "", "brand_model": number} for number in ("3", "1", "7")]
)


def _pairs(vehicles):
    return [(v["vin"], v["brand_model"]) for v in vehicles]


def _assert_clean_table(vehicles):
    """В таблице только настоящие машины: 17-символьный VIN и марка с буквой."""
    for vehicle in vehicles:
        assert DataMapper.vin_is_valid(vehicle["vin"]), vehicle
        assert vehicle["brand_model"], vehicle
        assert any(char.isalpha() for char in vehicle["brand_model"]), vehicle


# ─────────────────────────────────────────────────────────────
# 1. Склейка VIN, разрезанного переносом строки
# ─────────────────────────────────────────────────────────────

def test_wrapped_vins_are_joined():
    """«…T100564» + строка «3» — это один VIN «…T1005643»."""
    joined = DataMapper.join_wrapped_vins(OPERATOR_TEXT)

    assert "LMGAE3G58T100564 GS4" not in joined
    assert "LMGAE3G58T1005643 GS4" in joined
    assert "LMGAE3G85T1005271 GS4" in joined
    assert "LMGAE3G5XT1005577 GS4" in joined
    # Одиночные цифры ушли в VIN, а не остались отдельными строками.
    assert "\n3\n" not in joined
    assert "\n1\n" not in joined
    assert "\n7\n" not in joined


def test_join_is_idempotent():
    once = DataMapper.join_wrapped_vins(OPERATOR_TEXT)

    assert DataMapper.join_wrapped_vins(once) == once


def test_join_does_not_glue_a_brand_to_a_complete_vin():
    """После целого VIN одиночная цифра — мусор, а не продолжение VIN."""
    text = "JETOUR X70PLUS  EC37CUSM4TC008647\n3"

    assert DataMapper.join_wrapped_vins(text) == text


def test_join_does_not_glue_two_letter_brand():
    """Продолжение из двух символов — только цифры: «S9» это марка."""
    text = "LMGAE3G58T100564 GS4\nS9"

    assert DataMapper.join_wrapped_vins(text) == text


def test_join_keeps_text_without_partial_vin():
    text = "Водитель: Иванов Иван Иванович\nМосква\n"

    assert DataMapper.join_wrapped_vins(text) == text
    assert DataMapper.join_wrapped_vins("") == ""
    assert DataMapper.join_wrapped_vins(None) == ""


# ─────────────────────────────────────────────────────────────
# 2. Строки списка: VIN и марка — из одной строки
# ─────────────────────────────────────────────────────────────

def test_operator_text_gives_seven_pairs():
    """Главный случай задания: 7 машин с правильными парами VIN-Марка."""
    rows = DataMapper.vehicle_rows_from_text(OPERATOR_TEXT)

    assert _pairs(rows) == EXPECTED


def test_every_vin_from_operator_text_is_valid():
    rows = DataMapper.vehicle_rows_from_text(OPERATOR_TEXT)

    assert all(DataMapper.vin_is_valid(row["vin"]) for row in rows)


def test_numeric_lines_are_not_vehicles():
    """Строки «3», «1», «7» (переносы VIN) не становятся машинами."""
    rows = DataMapper.vehicle_rows_from_text(OPERATOR_TEXT)

    assert all(row["brand_model"] for row in rows)
    assert len(rows) == 7


def test_brand_is_taken_after_vin():
    """«VIN + марка»: марка стоит ПОСЛЕ VIN и не теряется."""
    brand = DataMapper._extract_brand_for_vin(OPERATOR_TEXT, EXPECTED[0][0])

    assert brand == "GS8IIFL"


def test_marka_first_format_still_works():
    """Обратный порядок («марка + VIN») не сломан."""
    text = "JETOUR X70PLUS  EC37CUSM4TC008647\nGAC S9  LVTDD24B0TDC48177\n"
    rows = DataMapper.vehicle_rows_from_text(text)

    assert _pairs(rows) == [
        ("EC37CUSM4TC008647", "JETOUR X70PLUS"),
        ("LVTDD24B0TDC48177", "GAC S9"),
    ]


# ─────────────────────────────────────────────────────────────
# 3. Ответ модели «VIN отдельно, марки отдельно, цифры отдельно»
# ─────────────────────────────────────────────────────────────

def test_broken_answer_gives_exactly_seven_machines():
    """Главная починка: 16 записей модели → 7 машин."""
    repaired = DataMapper.repair_vehicle_list(
        [dict(vehicle) for vehicle in BROKEN_ANSWER], OPERATOR_TEXT
    )

    assert len(repaired) == 7
    assert _pairs(repaired) == EXPECTED
    _assert_clean_table(repaired)


def test_broken_answer_has_no_empty_or_numeric_rows():
    """Ни пустых строк, ни строк из одних цифр."""
    repaired = DataMapper.repair_vehicle_list(
        [dict(vehicle) for vehicle in BROKEN_ANSWER], OPERATOR_TEXT
    )

    assert all(vehicle["vin"] for vehicle in repaired)
    assert all(vehicle["brand_model"] for vehicle in repaired)


def test_partial_answer_restores_three_machines_from_text():
    """Модель увидела 4 VIN — остальные три добираются из текста."""
    repaired = DataMapper.repair_vehicle_list(
        [dict(vehicle) for vehicle in PARTIAL_ANSWER], OPERATOR_TEXT
    )

    assert _pairs(repaired) == EXPECTED


def test_truncated_vin_row_is_not_a_machine():
    """
    16-символьный обрывок VIN из ответа машиной не считается.

    Иначе оператор получил бы отдельную строку с обрезанным VIN рядом
    с настоящей машиной того же номера.
    """
    recognized = [
        {"vin": "LMGAE3G58T100564", "brand_model": "GS4"},
        {"vin": "LMGJU3G83T1067383", "brand_model": "GS8IIFL"},
    ]

    repaired = DataMapper.repair_vehicle_list(recognized, OPERATOR_TEXT)

    assert len(repaired) == 7
    assert "LMGAE3G58T100564" not in [v["vin"] for v in repaired]
    assert _pairs(repaired) == EXPECTED


def test_foreign_valid_vin_is_kept_at_the_end():
    """VIN, которого нет в тексте, но который похож на VIN, не теряется."""
    recognized = [
        {"vin": "ZZZZZ9999ZZZZZ999", "brand_model": "НЕИЗВЕСТНО"},
    ]

    repaired = DataMapper.repair_vehicle_list(recognized, OPERATOR_TEXT)

    assert len(repaired) == 8
    assert repaired[-1]["vin"] == "ZZZZZ9999ZZZZZ999"


# ─────────────────────────────────────────────────────────────
# 4. Жёсткий фильтр только в режиме списка
# ─────────────────────────────────────────────────────────────

def test_document_row_without_letters_is_dropped():
    """Строка «только цифры» не машина и в обычном документе."""
    recognized = [
        {"vin": "", "brand_model": "123456"},
        {"vin": "", "brand_model": "Лада Веста"},
    ]

    repaired = DataMapper.repair_vehicle_list(recognized, "Реквизиты сторон")

    assert len(repaired) == 1
    assert repaired[0]["brand_model"] == "Лада Веста"


def test_short_vin_in_document_is_not_dropped():
    """
    В обычном документе невалидный VIN остаётся: его подсветит таблица.

    Жёсткий отсев «VIN не 17 символов» работает только для списка по
    строкам — там VIN берётся из текста. Здесь же значение пришло из
    документа, и оператор должен увидеть его с пометкой, а не потерять.
    """
    recognized = [{"vin": "EC37CUSM4TC0086", "brand_model": "JETOUR T1"}]
    text = "Перевозчик: ООО «Ромашка», ИНН 7701234567"

    repaired = DataMapper.repair_vehicle_list(recognized, text)

    assert repaired == recognized


# ─────────────────────────────────────────────────────────────
# 5. Промпт: одна пара «VIN + марка» на строку
# ─────────────────────────────────────────────────────────────

def test_prompt_describes_both_orders():
    prompt = GigaChatClient.SYSTEM_PROMPT

    assert "«VIN + марка»" in prompt
    assert "«марка + VIN» ИЛИ" in prompt


def test_prompt_forbids_split_vin_and_brands_lists():
    prompt = GigaChatClient.SYSTEM_PROMPT

    assert "НЕ выноси марки отдельным списком" in prompt
    assert "НЕ выноси VIN отдельным списком" in prompt
    assert "сначала все VIN, потом все марки" in prompt


def test_prompt_rejects_rows_without_letters():
    prompt = GigaChatClient.SYSTEM_PROMPT

    assert "нет ни одной БУКВЫ (только цифры)" in prompt
    assert "— это НЕ машины" in prompt


def test_prompt_names_wrapped_vin():
    prompt = GigaChatClient.SYSTEM_PROMPT

    assert "VIN может быть разорван переносом строки" in prompt
    assert "LMGAE3G58T1005643" in prompt
    assert "LMGJU3G83T1067383 GS8IIFL" in prompt


# ─────────────────────────────────────────────────────────────
# 6. Живой путь: MainWindow → RecognitionTask → таблица ТС
# ─────────────────────────────────────────────────────────────

def _make_window(monkeypatch):
    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    return MainWindow()


def _table_pairs(window, rows):
    return [
        (
            window.vehicles_tab.get_field(row, "vin"),
            window.vehicles_tab.get_field(row, "brand_model"),
        )
        for row in range(rows)
    ]


def test_main_window_fills_seven_rows_from_broken_answer(qt_app, monkeypatch):
    """Сквозная проверка: ответ модели со «разъездом» → ровная таблица 7 строк."""
    window = _make_window(monkeypatch)
    answer = {"vehicles": [dict(vehicle) for vehicle in BROKEN_ANSWER]}

    window._on_recognition_finished(answer, normalized_source_text(OPERATOR_TEXT))

    rows = window.vehicles_tab.table.rowCount()
    assert rows == 7
    assert _table_pairs(window, rows) == EXPECTED


def test_main_window_restores_machines_from_text(qt_app, monkeypatch):
    """Сквозная проверка с частичным ответом модели: 4 VIN → 7 строк."""
    window = _make_window(monkeypatch)
    answer = {"vehicles": [dict(vehicle) for vehicle in PARTIAL_ANSWER]}

    window._on_recognition_finished(answer, normalized_source_text(OPERATOR_TEXT))

    rows = window.vehicles_tab.table.rowCount()
    assert rows == 7
    assert _table_pairs(window, rows) == EXPECTED
