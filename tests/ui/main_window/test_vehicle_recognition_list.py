#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Распознавание списка ТС: без сдвига строк + проверка VIN.

Живой случай (задание «распознавание списка ТС сдвигает строки»): оператор
вставил шесть строк «марка + VIN», а в таблице оказалось ПЯТЬ машин, и в
строке 3 стояла марка из строки 2.

Причина. VIN третьей строки был набран с русской буквой-двойником
(«LVТDD24B0TDC48177»). Обнаружено живьём (tests/_tmp/_vehicle_recognition_fix):
  1. обезличиватель ищет VIN ЛАТИНСКИМ шаблоном, поэтому такой VIN уходит
     в модель открытым и БЕЗ плейсхолдера: модель видит 6 строк, но
     5 плейсхолдеров;
  2. модель отвечает пятью машинами, привязывая марку не к VIN, а к номеру
     строки, — марки всех следующих строк съезжают на строку вверх;
  3. VIN в ответе остаётся верным (отдельный плейсхолдер), то есть VIN —
     надёжный ключ, а порядок строк — нет.

Здесь проверяются обе половины фикса на уровне ядра и на живом пути
распознавания (MainWindow → RecognitionTask → GigaChatClient):
  * E.1 — шесть строк в тексте дают шесть ТС;
  * E.2 — марка не сдвигается на строку выше;
  * E.3 — русские буквы в VIN нормализуются;
  * E.4 — VIN не из 17 символов помечается ненадёжным (и подсвечивается
    в таблице — см. tests/test_vehicles_vin_check.py).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from core.gigachat_client import GigaChatClient, normalized_source_text  # noqa: E402
from core.recognizer import DataMapper  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


#: VIN без кириллицы (латиница).
VIN_1 = "EC37CUSM4TC008647"
VIN_2 = "EC37LUSM8TC007923"
VIN_3 = "LVTDD24B0TDC48177"
VIN_4 = "EC37CUSM4TC008714"
VIN_5 = "LMGJU3G89T1065296"
VIN_6 = "LMGJF3S87T1000433"

#: Ровно тот текст, что вставил оператор: в третьей строке «Т» — РУССКАЯ.
TEXT = (
    f"JETOUR X70PLUS  {VIN_1}\n"
    f"JETOUR X70PLUS  {VIN_2}\n"
    f"JETOUR T1       {VIN_3.replace('T', chr(0x422), 1)}\n"
    f"JETOUR X70PLUS  {VIN_4}\n"
    f"GAC GS8IIFL     {VIN_5}\n"
    f"GAC S9          {VIN_6}\n"
)

#: Ожидаемый результат: ровно то, что напечатано в тексте.
EXPECTED = [
    (VIN_1, "JETOUR X70PLUS"),
    (VIN_2, "JETOUR X70PLUS"),
    (VIN_3, "JETOUR T1"),
    (VIN_4, "JETOUR X70PLUS"),
    (VIN_5, "GAC GS8IIFL"),
    (VIN_6, "GAC S9"),
]

#: Ответ модели на вход с пятью плейсхолдерами — со сдвигом марок на строку
#: вверх. Именно это видел оператор: 5 строк, в третьей «JETOUR X70PLUS».
SHIFTED_ANSWER = [
    {"vin": VIN_1, "brand_model": "JETOUR X70PLUS"},
    {"vin": VIN_2, "brand_model": "JETOUR X70PLUS"},
    {"vin": VIN_4, "brand_model": "JETOUR T1"},
    {"vin": VIN_5, "brand_model": "JETOUR X70PLUS"},
    {"vin": VIN_6, "brand_model": "GAC GS8IIFL"},
]


def _pairs(vehicles):
    return [(v["vin"], v["brand_model"]) for v in vehicles]


# ─────────────────────────────────────────────────────────────
# E.3. Нормализация VIN: русские буквы → латинские
# ─────────────────────────────────────────────────────────────

def test_cyrillic_t_in_vin_is_replaced():
    """Русская «Т» внутри VIN — латинская «T»."""
    assert DataMapper.normalize_vin("LV\u0422DD24B0TDC48177") == VIN_3


def test_normalize_vin_replaces_every_lookalike():
    """Все буквы-двойники заменяются, а цифры остаются цифрами."""
    # «АВСЕНКМРТУХ» — кириллица; «У» — это латинская «Y».
    assert DataMapper.normalize_vin("АВСЕНКМРТУХ") == "ABCEHKMPTYX"
    # «О» — это ноль, «З» — тройка: латинских O и Q в VIN не бывает.
    assert DataMapper.normalize_vin("ОЗ") == "03"


def test_normalize_vin_drops_spaces_and_separators():
    assert DataMapper.normalize_vin(" ec37-cusm4 tc008647 ") == VIN_1
    assert DataMapper.normalize_vin("EC37–CUSM4—TC008647") == VIN_1


def test_normalize_vin_keeps_short_value():
    """Короткое значение не «дочиняется»: возвращается как есть."""
    assert DataMapper.normalize_vin("EC37CUSM4") == "EC37CUSM4"
    assert DataMapper.normalize_vin("") == ""
    assert DataMapper.normalize_vin(None) == ""


def test_normalize_vin_lookalikes_touches_only_vin_tokens():
    """
    Нормализуется только 17-символьный токен: остальной текст не трогается.

    Иначе «Тягач», «ПОГРУЗКА» и русские слова в реквизитах превратились бы
    в латинскую абракадабру.
    """
    text = "Тягач Volvo  Погрузка  Москва\n" f"JETOUR T1  {VIN_3.replace('T', chr(0x422), 1)}"
    cleaned = DataMapper.normalize_vin_lookalikes(text)

    assert "Тягач Volvo" in cleaned
    assert "Погрузка" in cleaned
    assert "Москва" in cleaned
    assert VIN_3 in cleaned


def test_normalize_vin_lookalikes_keeps_text_without_vin():
    text = "Водитель: <<PERSON_1>>, паспорт <<PASSPORT_1>>"
    assert DataMapper.normalize_vin_lookalikes(text) == text
    assert DataMapper.normalize_vin_lookalikes("") == ""
    assert DataMapper.normalize_vin_lookalikes(None) == ""


def test_find_vins_in_text_sees_cyrillic_vin():
    """VIN с русской буквой находится наравне с латинским."""
    vins = DataMapper._find_vins_in_text(TEXT)

    assert vins == [VIN_1, VIN_2, VIN_3, VIN_4, VIN_5, VIN_6]


def test_extract_brand_for_vin_finds_line_with_cyrillic_vin():
    """Марка берётся из строки, где VIN набран с русской буквой."""
    brand = DataMapper._extract_brand_for_vin(TEXT, VIN_3)

    assert brand == "JETOUR T1"


# ─────────────────────────────────────────────────────────────
# E.4. Проверка VIN: ровно 17 символов
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("vin, expected", [
    (VIN_1, True),
    ("EC37CUSM4TC00864", False),        # 16 символов
    ("EC37CUSM4TC0086477", False),      # 18 символов
    ("", False),
    (None, False),
    ("EC37CUSM4TC0086I7", False),       # буква I в VIN запрещена
])
def test_vin_is_valid(vin, expected):
    assert DataMapper.vin_is_valid(vin) is expected


# ─────────────────────────────────────────────────────────────
# E.1. Строк в тексте шесть — машин должно быть шесть
# ─────────────────────────────────────────────────────────────

def test_six_lines_give_six_vehicles():
    """Главный случай задания: 6 строк → 6 ТС."""
    repaired = DataMapper.repair_vehicle_list(list(SHIFTED_ANSWER), TEXT)

    assert len(repaired) == 6
    assert DataMapper.vehicle_line_count(TEXT) == 6


def test_dropped_line_is_restored_with_its_own_brand():
    """Потерянная строка возвращается на своё место и со своей маркой."""
    repaired = DataMapper.repair_vehicle_list(list(SHIFTED_ANSWER), TEXT)

    assert _pairs(repaired) == EXPECTED


def test_vehicle_written_with_cyrillic_vin_is_normalized():
    """VIN, пришедший из текста с русской буквой, в таблице — латиницей."""
    repaired = DataMapper.repair_vehicle_list(list(SHIFTED_ANSWER), TEXT)
    vins = [v["vin"] for v in repaired]

    assert VIN_3 in vins
    assert all(DataMapper.vin_is_valid(vin) for vin in vins)


# ─────────────────────────────────────────────────────────────
# E.2. Марка не сдвигается на строку выше
# ─────────────────────────────────────────────────────────────

def test_brand_does_not_shift_to_previous_row():
    """В строке 3 — «JETOUR T1», а не «JETOUR X70PLUS» из строки 2."""
    repaired = DataMapper.repair_vehicle_list(list(SHIFTED_ANSWER), TEXT)

    assert repaired[2]["brand_model"] == "JETOUR T1"
    assert repaired[3]["brand_model"] == "JETOUR X70PLUS"


def test_brand_is_taken_from_own_text_line():
    """
    Марка привязана к VIN, а не к номеру строки в ответе модели.

    Даже если машины пришли в другом порядке, после починки каждая марка
    стоит со своим VIN, а строки идут как в тексте.
    """
    shuffled = [
        {"vin": VIN_6, "brand_model": "GAC S9"},
        {"vin": VIN_3, "brand_model": "JETOUR T1"},
        {"vin": VIN_1, "brand_model": "JETOUR X70PLUS"},
        {"vin": VIN_2, "brand_model": "JETOUR X70PLUS"},
        {"vin": VIN_4, "brand_model": "JETOUR X70PLUS"},
        {"vin": VIN_5, "brand_model": "GAC GS8IIFL"},
    ]
    repaired = DataMapper.repair_vehicle_list(shuffled, TEXT)

    assert _pairs(repaired) == EXPECTED


def test_wrong_brand_in_answer_is_replaced_by_text():
    """Марка из ответа, не совпавшая со строкой текста, заменяется текстом."""
    wrong = [
        {"vin": VIN_1, "brand_model": "НЕВЕРНАЯ МАРКА"},
        {"vin": VIN_2, "brand_model": "JETOUR X70PLUS"},
        {"vin": VIN_3, "brand_model": "JETOUR T1"},
        {"vin": VIN_4, "brand_model": "JETOUR X70PLUS"},
        {"vin": VIN_5, "brand_model": "GAC GS8IIFL"},
        {"vin": VIN_6, "brand_model": "GAC S9"},
    ]
    repaired = DataMapper.repair_vehicle_list(wrong, TEXT)

    assert _pairs(repaired) == EXPECTED


def test_correct_answer_is_not_reordered():
    """
    Когда порядок ответа верный, марки не переписываются.

    Порядок строк меняется только там, где он заведомо поехал: иначе
    осмысленный порядок сложного документа был бы перетасован.
    """
    correct = [
        {"vin": VIN_1, "brand_model": "JETOUR X70PLUS"},
        {"vin": VIN_2, "brand_model": "JETOUR X70PLUS"},
        {"vin": VIN_3, "brand_model": "JETOUR T1"},
        {"vin": VIN_4, "brand_model": "JETOUR X70PLUS"},
        {"vin": VIN_5, "brand_model": "GAC GS8IIFL"},
        {"vin": VIN_6, "brand_model": "GAC S9"},
    ]
    repaired = DataMapper.repair_vehicle_list(list(correct), TEXT)

    assert repaired == correct


def test_foreign_vin_goes_to_the_end_and_keeps_its_brand():
    """
    VIN, которого нет в тексте, не теряется и не «прилипает» к чужой строке.

    Машины из текста встают по порядку строк, лишняя — в конец, со своей
    маркой из ответа: выдуманное значение лучше показать оператору, чем
    молча выбросить.
    """
    recognized = [
        {"vin": VIN_1, "brand_model": "JETOUR X70PLUS"},
        {"vin": "ZZZZZ9999ZZZZZ999", "brand_model": "НЕИЗВЕСТНО"},
    ]
    repaired = DataMapper.repair_vehicle_list(recognized, TEXT)

    assert len(repaired) == 7
    assert [v["vin"] for v in repaired[:6]] == [vin for vin, _ in EXPECTED]
    assert repaired[6]["vin"] == "ZZZZZ9999ZZZZZ999"
    assert repaired[6]["brand_model"] == "НЕИЗВЕСТНО"


def test_text_without_vin_is_returned_as_is():
    """Обычный договор (реквизиты, адреса) список ТС не выравнивает."""
    recognized = [{"vin": VIN_1, "brand_model": "JETOUR X70PLUS"}]
    text = "Перевозчик: ООО «Ромашка», ИНН 7701234567"

    assert DataMapper.repair_vehicle_list(recognized, text) == recognized


def test_empty_list_stays_empty():
    assert DataMapper.repair_vehicle_list([], TEXT) == []


def test_line_count_ignores_blank_lines():
    assert DataMapper.vehicle_line_count("а\n\n  \nб\n") == 2
    assert DataMapper.vehicle_line_count("") == 0
    assert DataMapper.vehicle_line_count(None) == 0


# ─────────────────────────────────────────────────────────────
# Промпт: правило «каждая строка — отдельное ТС»
# ─────────────────────────────────────────────────────────────

def test_prompt_requires_one_vehicle_per_line():
    prompt = GigaChatClient.SYSTEM_PROMPT

    assert "СПИСОК ТС ПО СТРОКАМ" in prompt
    assert "КАЖДАЯ СТРОКА — ОТДЕЛЬНАЯ МАШИНА" in prompt
    assert "НЕ переноси марку из строки выше" in prompt
    assert "НЕ объединяй две строки" in prompt
    assert "НЕ пропускай строки" in prompt


def test_prompt_asks_to_check_line_count():
    """Проверка «N строк — N машин» есть в промпте."""
    prompt = GigaChatClient.SYSTEM_PROMPT

    assert "ПРОВЕРЬ СЕБЯ ПЕРЕД ОТВЕТОМ" in prompt
    assert "должно быть N элементов" in prompt


def test_prompt_names_cyrillic_vin_as_vin():
    """VIN с русскими буквами промпт называет VIN-ом, а не мусором."""
    prompt = GigaChatClient.SYSTEM_PROMPT

    assert "РУССКИМИ буквами-двойниками" in prompt
    assert VIN_3 in prompt


# ─────────────────────────────────────────────────────────────
# Живой путь: MainWindow → RecognitionTask → клиент
# ─────────────────────────────────────────────────────────────

def test_limit_prompt_normalizes_cyrillic_vin():
    """В модель уходит текст с латинским VIN: тогда он и маскируется."""
    client = GigaChatClient(auth_key="test-key", timeout=1)

    assert VIN_3 in client._limit_prompt(TEXT)


def test_normalized_source_text_is_idempotent():
    once = normalized_source_text(TEXT)

    assert once == normalized_source_text(once)


def test_recognition_task_sends_normalized_text():
    """
    Задача распознавания знает нормализованный текст.

    Именно он уходит в модель и именно по нему выравнивается список ТС:
    у распознанных VIN и у строк текста должно быть одно написание.
    """
    from ui.main_window import RecognitionTask

    task = RecognitionTask(None, TEXT)
    text = normalized_source_text(task.text)

    assert VIN_3 in text
    assert DataMapper.vehicle_line_count(text) == 6
    # Нормализация длину не меняет: буква заменяется буквой.
    assert len(text) == len(task.text)


def test_main_window_fills_table_without_shift(qt_app, monkeypatch):
    """
    Сквозная проверка: ответ модели со сдвигом → ровная таблица.

    Повторяет путь оператора: текст со вкладки «Перевозимые ТС» уходит в
    распознавание, готовый ответ модели применяется к таблице. Раньше здесь
    и получалось «5 строк вместо 6» и чужая марка в строке 3.
    """
    from PyQt5.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    window = MainWindow()
    answer = {
        "vehicles": [
            dict(vehicle, vehicle_type="", plate_number="", year=0, color="")
            for vehicle in SHIFTED_ANSWER
        ]
    }
    # Нормализованный текст — ровно тот, что ушёл бы в модель.
    window._on_recognition_finished(answer, normalized_source_text(TEXT))

    rows = window.vehicles_tab.table.rowCount()
    assert rows == 6

    got = [
        (window.vehicles_tab.get_field(row, "vin"),
         window.vehicles_tab.get_field(row, "brand_model"))
        for row in range(rows)
    ]
    assert got == EXPECTED


def test_vehicle_line_count_matches_answer_after_repair():
    """Строк в ответе столько же, сколько строк в тексте."""
    repaired = DataMapper.repair_vehicle_list(list(SHIFTED_ANSWER), TEXT)

    assert len(repaired) == DataMapper.vehicle_line_count(TEXT)
