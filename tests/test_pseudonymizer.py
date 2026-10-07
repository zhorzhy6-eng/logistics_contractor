#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты обратимого обезличивания ПДн (core/pseudonymizer.py).

Проверяется то, ради чего модуль написан:

  * в «безопасном» тексте нет оригинальных значений — только токены;
  * ``restore(anonymize(text)) == text`` для всех видов данных;
  * испорченные моделью токены всё равно восстанавливаются;
  * неизвестные токены не превращаются в выдуманные данные;
  * в логах нет ни одного значения ПДн и нет маппинга;
  * маппинг не хранится внутри объекта — только у вызывающей стороны.

Все значения в тестах синтетические.
"""

import logging
import threading
import types

import pytest

import core.pseudonymizer as pseudonymizer
from core.pseudonymizer import Pseudonymizer, TOKEN_TYPES, token_name

# ── Синтетические данные ─────────────────────────────────────
PERSON = "Иванов Иван Иванович"
PERSON_2 = "Петров Пётр Петрович"
PASSPORT = "18 22 926830"
LICENSE = "99 36 123456"
PHONE = "+7 (999) 123-45-67"
EMAIL = "ivanov@example.ru"
INN_10 = "7707083893"
INN_12 = "500100732259"
INN_BROKEN = "7701234567"
SNILS = "112-233-445 95"
SNILS_BROKEN = "112-233-445 94"
VIN = "EC3TEUMB0T0002608"
PLATE = "А123ВС77"
PLATE_LATIN = "O844XY196"

FULL_TEXT = (
    f"Водитель: {PERSON}, паспорт {PASSPORT} выдан 30.01.2023 "
    f"Отделом УФМС России по г. Москве.\n"
    f"Водительское удостоверение {LICENSE}, категории B, C, E.\n"
    f"Телефон {PHONE}, email: {EMAIL}\n"
    f"ИНН {INN_10}, СНИЛС {SNILS}\n"
    f"VIN {VIN}, госномер {PLATE}\n"
    f"Адрес: 183052, г. Мурманск, ул. Тестовая, д. 1, кв. 5\n"
    f"ООО «Ромашка», директор {PERSON_2}\n"
    f"Стоимость 180300 руб., номер договора 23092026-74, дата 24.09.2026\n"
)

ALL_SECRETS = (
    PERSON, PERSON_2, PASSPORT, LICENSE, PHONE, EMAIL, INN_10, INN_12,
    SNILS, VIN, PLATE, PLATE_LATIN,
)


@pytest.fixture
def pseudo():
    return Pseudonymizer()


# ─────────────────────────────────────────────────────────────
# Обезличивание
# ─────────────────────────────────────────────────────────────

def test_all_supported_types_are_found(pseudo):
    safe, mapping = pseudo.anonymize(FULL_TEXT)
    kinds = {token_name(token).rsplit("_", 1)[0] for token in mapping}
    assert {"PERSON", "PASSPORT", "LICENSE", "PHONE", "EMAIL",
            "INN", "SNILS", "VIN", "GOSNOMER", "ADDRESS"} <= kinds


def test_safe_text_contains_no_original_values(pseudo):
    safe, _ = pseudo.anonymize(FULL_TEXT)
    for secret in (PERSON, PERSON_2, PASSPORT, LICENSE, PHONE, EMAIL,
                   INN_10, SNILS, VIN, PLATE):
        assert secret not in safe
    # Части значений тоже не должны оставаться (номера паспорта, ФИО и т.п.).
    assert "926830" not in safe
    assert "123456" not in safe
    assert "Иванович" not in safe
    assert "123-45-67" not in safe


def test_mapping_values_are_not_inside_tokens(pseudo):
    _, mapping = pseudo.anonymize(FULL_TEXT)
    for token, value in mapping.items():
        assert token.startswith("<<") and token.endswith(">>")
        for part in str(value).split():
            if len(part) >= 4:
                assert part not in token


def test_restore_returns_exact_original(pseudo):
    safe, mapping = pseudo.anonymize(FULL_TEXT)
    assert pseudo.restore(safe, mapping) == FULL_TEXT


def test_roundtrip_on_driver_fixture(pseudo, driver_data):
    text = "\n".join(f"{key}: {value}" for key, value in driver_data.items())
    safe, mapping = pseudo.anonymize(text)
    assert pseudo.restore(safe, mapping) == text
    assert driver_data["full_name"] not in safe


def test_repeated_value_gets_single_token(pseudo):
    text = f"{PERSON} — водитель. {PERSON} — он же."
    safe, mapping = pseudo.anonymize(text)
    assert safe.count("<<PERSON_1>>") == 2
    assert len([t for t in mapping if t.startswith("<<PERSON")]) == 1
    assert pseudo.restore(safe, mapping) == text


@pytest.mark.parametrize("text", ["", None, "Обычный текст без персональных данных."])
def test_text_without_pii_is_returned_unchanged(pseudo, text):
    safe, mapping = pseudo.anonymize(text)
    assert mapping == {}
    assert safe == ("" if text is None else text)


# ─────────────────────────────────────────────────────────────
# Восстановление, в том числе испорченных токенов
# ─────────────────────────────────────────────────────────────

def test_broken_tokens_are_restored(pseudo):
    _, mapping = pseudo.anonymize(f"{PERSON}, {PHONE}")
    broken = ("ФИО: <<PERSON 1>>; ещё: <<person-1>>; снова: PERSON_1; "
              "и в кавычках: «PERSON 1»; тел: <<PHONE 1>>")
    restored = pseudo.restore(broken, mapping)
    # Четыре варианта написания одного токена — все восстановлены.
    assert restored.count(PERSON) == 4
    assert PHONE in restored
    assert "<<" not in restored


def test_unknown_token_is_removed_and_logged_without_values(pseudo, caplog):
    _, mapping = pseudo.anonymize(PERSON)
    with caplog.at_level(logging.DEBUG, logger="core.pseudonymizer"):
        restored = pseudo.restore("ФИО: <<PERSON_9>>, тел: <<PHONE_3>>", mapping)
    assert "PERSON_9" not in restored and "PHONE_3" not in restored
    assert "ФИО" in restored and "тел" in restored
    assert "PERSON_9" in caplog.text          # имя токена — можно
    assert "PHONE_3" in caplog.text
    assert PERSON not in caplog.text          # значения — нельзя


def test_unknown_token_kept_when_drop_unknown_disabled(pseudo):
    _, mapping = pseudo.anonymize(PERSON)
    restored = pseudo.restore("<<PERSON_9>>", mapping, drop_unknown=False)
    assert restored == "<<PERSON_9>>"


def test_restore_with_empty_mapping_changes_nothing(pseudo):
    text = "Иванов Иван Иванович, <<PERSON_1>>"
    assert pseudo.restore(text, {}) == text


def test_restore_json_restores_nested_values(pseudo):
    _, mapping = pseudo.anonymize(f"{PERSON} {PHONE}")
    payload = {
        "driver": {"full_name": "<<PERSON_1>>", "phone": "<<PHONE_1>>"},
        "vehicles": [{"vin": "<<VIN_9>>"}],
        "empty": "",
    }
    restored = pseudo.restore_json(payload, mapping)
    assert restored["driver"]["full_name"] == PERSON
    assert restored["driver"]["phone"] == PHONE
    assert restored["vehicles"][0]["vin"] == ""
    # Входная структура не изменяется.
    assert payload["driver"]["full_name"] == "<<PERSON_1>>"


# ─────────────────────────────────────────────────────────────
# Логи: только типы и количества
# ─────────────────────────────────────────────────────────────

def test_logs_contain_types_and_counts_only(pseudo, caplog):
    with caplog.at_level(logging.DEBUG, logger="core.pseudonymizer"):
        safe, mapping = pseudo.anonymize(FULL_TEXT)
        pseudo.restore(safe, mapping)
    text = caplog.text
    assert "PERSON=" in text
    assert "Обезличивание" in text
    for secret in ALL_SECRETS:
        assert secret not in text
    assert "926830" not in text
    assert "ivanov@example.ru" not in text


def test_mapping_is_not_stored_in_instance(pseudo, caplog):
    with caplog.at_level(logging.DEBUG, logger="core.pseudonymizer"):
        safe, mapping = pseudo.anonymize(FULL_TEXT)
        pseudo.restore(safe, mapping)
    # Маппинг живёт только у вызывающей стороны.
    assert vars(pseudo) == {}
    for token, value in mapping.items():
        assert value not in caplog.text


# ─────────────────────────────────────────────────────────────
# Контрольные суммы и контекст: ложные срабатывания
# ─────────────────────────────────────────────────────────────

def test_inn_requires_valid_checksum(pseudo):
    safe, mapping = pseudo.anonymize(f"ИНН {INN_10} и {INN_12}")
    assert "<<INN_1>>" in safe and "<<INN_2>>" in safe

    safe_broken, mapping_broken = pseudo.anonymize(f"ИНН {INN_BROKEN}")
    assert mapping_broken == {}
    assert INN_BROKEN in safe_broken


def test_snils_requires_valid_checksum(pseudo):
    assert "<<SNILS_1>>" in pseudo.anonymize(f"СНИЛС {SNILS}")[0]
    safe, mapping = pseudo.anonymize(f"СНИЛС {SNILS_BROKEN}")
    assert mapping == {} and SNILS_BROKEN in safe


def test_twelve_digit_inn_is_not_confused_with_ten(pseudo):
    safe, mapping = pseudo.anonymize(f"ИНН {INN_12}")
    assert list(mapping.values()) == [INN_12]
    assert "<<INN_1>>" in safe


def test_parent_plate_formats(pseudo):
    text = f"Тягач {PLATE_LATIN}, полуприцеп гос. номер 71ABF18, авто {PLATE}"
    safe, mapping = pseudo.anonymize(text)
    assert "<<GOSNOMER_1>>" in safe
    assert "<<GOSNOMER_3>>" in safe
    assert PLATE not in safe and PLATE_LATIN not in safe
    assert pseudo.restore(safe, mapping) == text


def test_vin_requires_letter(pseudo):
    digits_only = "12345678901234567"
    safe, mapping = pseudo.anonymize(f"номер {digits_only}")
    assert mapping == {} and digits_only in safe


def test_city_stays_while_street_and_house_are_replaced(pseudo):
    text = "183052, г. Мурманск, ул. Тестовая, д. 53, кв. 12"
    safe, mapping = pseudo.anonymize(text)
    assert "Мурманск" in safe
    assert "Тестовая" not in safe and "53" not in safe and "12" not in safe
    assert pseudo.restore(safe, mapping) == text


def test_organizations_and_regions_are_not_person(pseudo):
    text = "ООО «Ромашка», Республика Башкортостан, Краснодарский край"
    safe, mapping = pseudo.anonymize(text)
    assert "<<PERSON" not in safe
    assert mapping == {}


def test_person_without_patronymic_needs_context(pseudo):
    # Два слова без отчества и без слова-маркера ФИО не трогаем:
    # иначе под токен попали бы названия организаций и регионов.
    without_context = "В анкете указано Сергеев Сергей"
    assert pseudo.anonymize(without_context)[1] == {}

    with_context = "Водитель Сергеев Сергей"
    safe, mapping = pseudo.anonymize(with_context)
    assert "<<PERSON_1>>" in safe
    assert list(mapping.values()) == ["Сергеев Сергей"]


def test_dates_and_amounts_are_not_touched(pseudo):
    text = "Дата 24.09.2026, срок оплаты 10 дней, сумма 180300 руб., договор 23092026-74"
    safe, mapping = pseudo.anonymize(text)
    assert mapping == {}
    assert safe == text


def test_person_initials_are_replaced(pseudo):
    safe, mapping = pseudo.anonymize("Директор Смирнов А. В.")
    assert "<<PERSON_1>>" in safe
    assert list(mapping.values()) == ["Смирнов А. В."]


# ─────────────────────────────────────────────────────────────
# Совместимость и устойчивость
# ─────────────────────────────────────────────────────────────

def test_token_types_cover_all_documented_kinds():
    assert set(TOKEN_TYPES) == {
        "EMAIL", "SNILS", "VIN", "PASSPORT", "LICENSE",
        "GOSNOMER", "INN", "PHONE", "PERSON", "ADDRESS",
    }


def test_restore_is_idempotent_for_text_without_tokens(pseudo):
    _, mapping = pseudo.anonymize(PERSON)
    clean = "Здесь нет ни одного плейсхолдера."
    assert pseudo.restore(clean, mapping) == clean


def test_single_instance_is_safe_for_parallel_use(pseudo):
    """Экземпляр не хранит состояния — им можно пользоваться из потоков."""
    texts = [f"Водитель {PERSON}, тел {PHONE}",
             f"Водитель {PERSON_2}, тел +7 (495) 123-45-67",
             f"ИНН {INN_10}, СНИЛС {SNILS}",
             f"VIN {VIN}, госномер {PLATE}"]
    results = [None] * len(texts)

    def worker(index: int) -> None:
        safe, mapping = pseudo.anonymize(texts[index])
        results[index] = pseudo.restore(safe, mapping)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(texts))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == texts
    assert vars(pseudo) == {}


def test_describe_reports_kinds_without_values(pseudo):
    _, mapping = pseudo.anonymize(FULL_TEXT)
    described = pseudo.describe(mapping)
    assert "PERSON=" in described and "ADDRESS=" in described
    for secret in ALL_SECRETS:
        assert secret not in described


# ─────────────────────────────────────────────────────────────
# Интеграция с GigaChatClient: что реально уходит в модель
# ─────────────────────────────────────────────────────────────

class _FakeChatResponse:
    """Ответ GigaChat с заданным содержимым (реальные запросы не выполняются)."""

    status_code = 200

    def __init__(self, content: str):
        self._content = content
        self.text = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture
def gigachat_client(monkeypatch):
    """Клиент GigaChat с подменённым транспортом; возвращает (client, sent)."""
    import core.gigachat_client as module

    client = module.GigaChatClient(auth_key="synthetic-key", ca_bundle="")
    monkeypatch.setattr(client, "_get_token", lambda force=False: "TOKEN-12345")

    sent = {"payloads": [], "answer": "{}"}

    def fake_post(url, **kwargs):
        sent["payloads"].append(kwargs.get("json"))
        return _FakeChatResponse(sent["answer"])

    monkeypatch.setattr(module.requests, "post", fake_post)
    return client, sent


def test_gigachat_receives_tokens_instead_of_pii(gigachat_client):
    client, sent = gigachat_client
    sent["answer"] = (
        '{"driver": {"full_name": "<<PERSON_1>>", "phone": "<<PHONE_1>>", '
        '"passport_series": "<<PASSPORT_1>>"}}'
    )

    parsed = client.recognize_text(f"Водитель: {PERSON}, тел {PHONE}, паспорт {PASSPORT}")

    user_content = sent["payloads"][-1]["messages"][1]["content"]
    for secret in (PERSON, PHONE, PASSPORT):
        assert secret not in user_content
    assert "<<PERSON_1>>" in user_content
    assert "<<PHONE_1>>" in user_content
    assert "<<PASSPORT_1>>" in user_content

    # Ответ модели с плейсхолдерами вернулся с оригиналами; пара серия/номер
    # раскладывается ядром: модель видела один токен и вернула его в одно поле.
    assert parsed["driver"]["full_name"] == PERSON
    assert parsed["driver"]["phone"] == PHONE
    assert parsed["driver"]["passport_series"] == "18 22"
    assert parsed["driver"]["passport_number"] == "926830"


def test_gigachat_system_prompt_explains_tokens(gigachat_client):
    client, sent = gigachat_client
    client.recognize_text(f"Водитель {PERSON}")
    system_content = sent["payloads"][-1]["messages"][0]["content"]
    assert "<<PERSON_1>>" in system_content
    assert "плейсхолдер" in system_content.lower()


def test_gigachat_broken_tokens_from_model_are_restored(gigachat_client):
    client, sent = gigachat_client
    sent["answer"] = (
        '{"driver": {"full_name": "<<PERSON 1>>"}, '
        '"vehicles": [{"vin": "VIN_1"}], "carrier": {"inn": "<<inn-1>>"}}'
    )
    parsed = client.recognize_text(f"Иванов Иван Иванович, VIN {VIN}, ИНН {INN_10}")
    assert parsed["driver"]["full_name"] == "Иванов Иван Иванович"
    assert parsed["vehicles"][0]["vin"] == VIN
    assert parsed["carrier"]["inn"] == INN_10


def test_gigachat_unrestorable_token_is_cleared(gigachat_client):
    client, sent = gigachat_client
    sent["answer"] = '{"driver": {"full_name": "<<PERSON_9>>", "phone": "<<PHONE_4>>"}}'
    parsed = client.recognize_text(f"Водитель {PERSON}")
    assert parsed["driver"]["full_name"] == ""
    assert parsed["driver"]["phone"] == ""


def test_gigachat_text_without_pii_is_sent_unchanged(gigachat_client):
    client, sent = gigachat_client
    text = "Стоимость 180300 руб., дата 24.09.2026, срок оплаты 10 дней"
    client.recognize_text(text)
    assert sent["payloads"][-1]["messages"][1]["content"] == text


def test_gigachat_logs_contain_no_pii(gigachat_client, caplog):
    client, sent = gigachat_client
    sent["answer"] = '{"driver": {"full_name": "<<PERSON_1>>"}}'
    with caplog.at_level(logging.DEBUG):
        client.recognize_text(f"Водитель: {PERSON}, {PHONE}, {EMAIL}, паспорт {PASSPORT}")
    for secret in (PERSON, PHONE, EMAIL, PASSPORT, "926830", "123-45-67"):
        assert secret not in caplog.text
    # В лог попадают только типы и количество найденного — без значений.
    assert "PERSON=1" in caplog.text
    assert "в запрос уходят только плейсхолдеры" in caplog.text


# ─────────────────────────────────────────────────────────────
# Утечка ПДн: дефис между группами серии документа
# ─────────────────────────────────────────────────────────────
# «60-26 123456» раньше не распознавался и уходил в модель открытым текстом.

def test_passport_series_hyphen_is_anonymized(pseudo):
    text = "Водитель: Кузнецов Пётр Иванович\nПаспорт РФ: 60-26 123456, выдан 20.06.2015"
    safe, mapping = pseudo.anonymize(text)
    assert "<<PASSPORT_1>>" in safe
    assert "60-26 123456" in mapping.values()
    assert "60-26" not in safe and "123456" not in safe
    assert pseudo.restore(safe, mapping) == text


@pytest.mark.parametrize("raw", ["60 26 123456", "6026123456"])
def test_passport_series_space_and_solid_still_work(pseudo, raw):
    text = f"Водитель: Кузнецов Пётр Иванович\nПаспорт РФ: {raw}, выдан 20.06.2015"
    safe, mapping = pseudo.anonymize(text)
    assert "<<PASSPORT_1>>" in safe
    assert raw not in safe
    assert pseudo.restore(safe, mapping) == text


def test_license_hyphen_series_is_anonymized(pseudo):
    text = ("Водитель: Кузнецов Пётр Иванович\n"
            "Водительское удостоверение: 99-12 654321, выдано 10.10.2020")
    safe, mapping = pseudo.anonymize(text)
    assert "<<LICENSE_1>>" in safe
    assert "99-12 654321" in mapping.values()
    assert "99-12" not in safe and "654321" not in safe
    assert pseudo.restore(safe, mapping) == text


@pytest.mark.parametrize("raw", ["12-34 567890", "12 34 567890", "1234567890"])
def test_document_number_without_context_is_not_tokenized(pseudo, raw):
    """Без слова «паспорт»/«удостоверение» рядом номер остаётся текстом."""
    text = f"Реквизиты: {raw} на странице"
    safe, mapping = pseudo.anonymize(text)
    assert mapping == {}
    assert safe == text


@pytest.mark.parametrize("raw", ["20-06-2015", "20.06.2015", "20/06/2015"])
def test_hyphen_date_is_not_a_document(pseudo, raw):
    text = f"Дата выдачи: {raw}"
    safe, mapping = pseudo.anonymize(text)
    assert mapping == {}
    assert safe == text


def test_document_tokens_contain_no_original_digits(pseudo):
    texts = (
        "Водитель: Кузнецов Пётр Иванович\nПаспорт РФ: 60-26 123456",
        "Водитель: Кузнецов Пётр Иванович\nПаспорт РФ: 60 26 123456",
        "Водитель: Кузнецов Пётр Иванович\nПаспорт РФ: 6026123456",
        "Водитель: Кузнецов Пётр Иванович\nВодительское удостоверение: 99-12 654321",
    )
    for text in texts:
        safe, mapping = pseudo.anonymize(text)
        for token, value in mapping.items():
            digits = "".join(ch for ch in value if ch.isdigit())
            if digits:
                assert digits not in token
                assert digits not in safe
        assert pseudo.restore(safe, mapping) == text


# ─────────────────────────────────────────────────────────────
# Слой Natasha (опциональный)
# ─────────────────────────────────────────────────────────────
# Ядро маскирования — регулярки; Natasha ДОПОЛНЯЕТ их и нужна там, где
# регулярка молчит: иностранные имена без отчества, фамилии без контекста,
# цельные адреса. Все тексты синтетические.
#
# По умолчанию слой выключен (autouse-фикстура `natasha_layer_off`
# в tests/conftest.py): юнит-тесты проверяют логику, а не чужую NER-модель.
# Тесты самого слоя просят фикстуру `natasha_layer` и помечены `slow`.

#: Иностранное имя без отчества — регулярка его не видит вовсе.
FOREIGN_NAME = "Харуки Мураками"
#: Цельный адрес: регулярка режет его на «ул. Тверская» и «д. 5».
WHOLE_ADDRESS = "г. Москва, ул. Тверская, д. 5"


@pytest.fixture
def natasha_layer(natasha_warmed_up, monkeypatch):
    """
    Включает реальный слой Natasha для одного теста.

    ``natasha_warmed_up`` (tests/conftest.py) строит модели один раз на
    прогон. Если библиотеки нет или модели не поднялись — тест
    пропускается: маскирование обязано работать и без Natasha.
    """
    monkeypatch.setattr(pseudonymizer, "_NATASHA_AVAILABLE", None)
    if pseudonymizer._natasha_pipeline() is None:
        pytest.skip("natasha недоступна — маскирование работает на регулярках")
    return pseudonymizer


class _FakeAddrMatch:
    """Заглушка совпадения AddrExtractor: границы и тип части адреса."""

    def __init__(self, start: int, stop: int, part_type):
        self.start = start
        self.stop = stop
        self.fact = types.SimpleNamespace(type=part_type)


class _FakeExtractor:
    """Заглушка AddressExtractor: отдаёт заранее заданные части."""

    def __init__(self, parts):
        self._parts = parts

    def __call__(self, text):
        return [_FakeAddrMatch(start, stop, part_type)
                for start, stop, part_type in self._parts]


def test_pseudonymizer_works_without_natasha(monkeypatch):
    """Без Natasha маскирование работает на регулярках и не падает."""
    monkeypatch.setattr(pseudonymizer, "_NATASHA_AVAILABLE", False)
    monkeypatch.setattr(pseudonymizer, "_NATASHA", {})

    pseudo = Pseudonymizer()
    safe, mapping = pseudo.anonymize(FULL_TEXT)

    assert pseudonymizer._natasha_pipeline() is None
    kinds = {token_name(token).rsplit("_", 1)[0] for token in mapping}
    assert {"PERSON", "PASSPORT", "LICENSE", "PHONE", "EMAIL",
            "INN", "SNILS", "VIN", "GOSNOMER", "ADDRESS"} <= kinds
    for secret in ALL_SECRETS:
        assert secret not in safe
    assert pseudo.restore(safe, mapping) == FULL_TEXT


def test_broken_natasha_does_not_break_masking(monkeypatch, caplog):
    """Сломанный слой Natasha не роняет маскирование: работает регулярка."""
    class _BrokenExtractor:
        def __call__(self, text):
            raise RuntimeError("сломанный извлекатель")

    monkeypatch.setattr(pseudonymizer, "_NATASHA_AVAILABLE", None)
    monkeypatch.setattr(pseudonymizer, "_NATASHA", {
        "ready": True,
        "segmenter": object(),
        "ner_tagger": object(),
        "addr_extractor": _BrokenExtractor(),
    })

    with caplog.at_level(logging.DEBUG, logger="core.pseudonymizer"):
        safe, mapping = Pseudonymizer().anonymize(f"Водитель {PERSON}, тел {PHONE}")

    assert pseudonymizer._natasha_pipeline() is not None
    assert PERSON not in safe and PHONE not in safe
    assert "<<PERSON_1>>" in safe
    assert PERSON not in caplog.text


def test_address_parts_are_merged_into_one_span():
    """Части одного адреса склеиваются в один span — токен будет один."""
    text = "г. Москва, ул. Тверская, д. 5, кв. 17"
    matches = [
        _FakeAddrMatch(0, 9, "город"),
        _FakeAddrMatch(11, 23, "улица"),
        _FakeAddrMatch(25, 29, "дом"),
        _FakeAddrMatch(31, 37, "квартира"),
    ]
    assert pseudonymizer._merge_addr_parts(text, matches) == [(0, 37)]


def test_address_parts_separated_by_words_are_different_addresses():
    """Между частями стоит слово — это уже не один адрес."""
    text = "ул. Тверская, склад, ул. Центральная"
    matches = [
        _FakeAddrMatch(0, 12, "улица"),
        _FakeAddrMatch(21, 36, "улица"),
    ]
    assert pseudonymizer._merge_addr_parts(text, matches) == [(0, 12), (21, 36)]


def test_address_part_without_type_is_not_an_address():
    """Часть без типа — просто слово с заглавной буквы, а не адрес."""
    text = "Иванов Иван Иванович, ул. Тверская"
    matches = [
        _FakeAddrMatch(0, 6, None),
        _FakeAddrMatch(22, 34, "улица"),
    ]
    assert pseudonymizer._merge_addr_parts(text, matches) == [(22, 34)]


def test_address_without_street_is_not_an_address():
    """
    Без улицы адреса нет: город, индекс и номер дома — не адрес.

    Так отсекаются ложные срабатывания AddrExtractor: «180300» в «Стоимость
    180300 руб.» он считает индексом, «с Остапом Бендером» — селом
    (маркер «с» — сокращение от «село»), «д. 53» без улицы закрывает
    регулярка.
    """
    assert pseudonymizer._merge_addr_parts(
        "Стоимость 180300 руб.", [_FakeAddrMatch(10, 16, "индекс")]
    ) == []
    assert pseudonymizer._merge_addr_parts(
        "д. 53", [_FakeAddrMatch(0, 5, "дом")]
    ) == []
    assert pseudonymizer._merge_addr_parts(
        "г. Москва", [_FakeAddrMatch(0, 9, "город")]
    ) == []
    assert pseudonymizer._merge_addr_parts(
        "Договор заключён с Остапом Бендером.",
        [_FakeAddrMatch(17, 35, "село")],
    ) == []


def test_index_and_city_join_the_street_group():
    """Индекс и город — часть того же адреса, что и улица."""
    text = "183052, г. Мурманск, ул. Тестовая"
    matches = [
        _FakeAddrMatch(0, 6, "индекс"),
        _FakeAddrMatch(8, 19, "город"),
        _FakeAddrMatch(21, 33, "улица"),
    ]
    assert pseudonymizer._merge_addr_parts(text, matches) == [(0, 33)]


def test_address_span_is_widened_over_regular_house_number():
    """
    Номер дома, который нашла регулярка, не остаётся открытым.

    «Бештаугорское шоссе 17»: Natasha закрывает только название улицы,
    а «шоссе 17» ловит регулярка — span адреса обязан дойти до номера.
    """
    text = "г. Пятигорск, Бештаугорское шоссе 17"
    regular = [
        pseudonymizer._Candidate(29, 36, "ADDRESS", "шоссе 17"),
    ]
    assert pseudonymizer._widen_addr_over_regular(0, 33, regular) == (0, 36)

    # Совпадение с тем же началом span не расширяет: там выигрывает регулярка.
    same_start = [pseudonymizer._Candidate(0, 12, "ADDRESS", "г. Пятигорск")]
    assert pseudonymizer._widen_addr_over_regular(0, 33, same_start) == (0, 33)


def test_natasha_address_path_without_models(monkeypatch):
    """Полный путь «адрес → один токен» на заглушке, без реальных моделей."""
    text = "г. Пятигорск, Бештаугорское шоссе 17"
    monkeypatch.setattr(pseudonymizer, "_NATASHA_AVAILABLE", None)
    monkeypatch.setattr(pseudonymizer, "_NATASHA", {
        "ready": True,
        "addr_extractor": _FakeExtractor([(0, 12, "город"), (14, 33, "шоссе")]),
    })

    safe, mapping = Pseudonymizer().anonymize(text)

    assert safe == "<<ADDRESS_1>>"
    assert list(mapping.values()) == [text]

    # Ложное «село» из предлога «с» адресом не становится.
    monkeypatch.setattr(pseudonymizer, "_NATASHA", {
        "ready": True,
        "addr_extractor": _FakeExtractor([(17, 35, "село")]),
    })
    assert Pseudonymizer().anonymize("Договор заключён с Остапом Бендером.")[1] == {}


def test_natasha_kinds_are_not_token_types():
    """Natasha-типы нужны только для приоритета, в TOKEN_TYPES их нет."""
    assert [kind for kind in TOKEN_TYPES if "NATASHA" in kind] == []
    assert pseudonymizer._PRIORITY["PERSON_NATASHA"] < pseudonymizer._PRIORITY["PERSON"]
    assert pseudonymizer._PRIORITY["ADDRESS_NATASHA"] < pseudonymizer._PRIORITY["ADDRESS"]
    # Natasha-детекторы идут ПОСЛЕ всех регулярных.
    assert pseudonymizer._DETECTORS[-2:] == (
        pseudonymizer._find_person_natasha,
        pseudonymizer._find_address_natasha,
    )


@pytest.mark.slow
def test_natasha_finds_foreign_name(pseudo, natasha_layer):
    """Иностранное имя без отчества регулярка пропускает, Natasha — нет."""
    text = f"{FOREIGN_NAME} прибыл с визитом."
    safe, mapping = pseudo.anonymize(text)

    assert "<<PERSON_1>>" in safe
    assert mapping["<<PERSON_1>>"] == FOREIGN_NAME
    assert "Мураками" not in safe
    assert pseudo.restore(safe, mapping) == text


@pytest.mark.slow
def test_natasha_finds_last_name_without_context(pseudo, natasha_layer):
    """Фамилия без слов-маркеров рядом («водитель», «ФИО») — тоже ПДн."""
    text = "В анкете указано Сергеев Сергей"
    safe, mapping = pseudo.anonymize(text)

    assert list(mapping.values()) == ["Сергеев Сергей"]
    assert "Сергеев" not in safe
    assert pseudo.restore(safe, mapping) == text


@pytest.mark.slow
def test_natasha_oblique_case_is_a_person_not_an_address(pseudo, natasha_layer):
    """
    Косвенный падеж без слов-маркеров: регулярка молчит, Natasha находит.

    Регресс-проверка: AddrExtractor считает «с Остапом Бендером» селом
    («с» — сокращение от «село»), поэтому без якоря-улицы имя уходило бы
    в адрес вместе с предлогом.
    """
    text = "Договор заключён с Остапом Бендером."
    safe, mapping = pseudo.anonymize(text)

    assert list(mapping.values()) == ["Остапом Бендером"]
    assert "<<PERSON_1>>" in safe
    assert "Бендером" not in safe
    assert pseudo.restore(safe, mapping) == text


@pytest.mark.slow
def test_natasha_finds_whole_address(pseudo, natasha_layer):
    """Один токен на ВЕСЬ адрес, а не по токену на улицу и дом."""
    safe, mapping = pseudo.anonymize(WHOLE_ADDRESS)

    assert list(mapping.values()) == [WHOLE_ADDRESS]
    assert "<<ADDRESS_1>>" in safe
    assert "Тверская" not in safe and "Москва" not in safe
    assert pseudo.restore(safe, mapping) == WHOLE_ADDRESS


def test_regular_wins_over_natasha(natasha_layer):
    """При пересечении выигрывает регулярка: тип PERSON, не PERSON_NATASHA."""
    candidates = pseudonymizer._detect(PERSON)
    assert [item.kind for item in candidates] == ["PERSON"]

    safe, mapping = Pseudonymizer().anonymize(PERSON)
    assert list(mapping.values()) == [PERSON]
    assert not [token for token in mapping if "NATASHA" in token]


@pytest.mark.slow
def test_restore_roundtrip_with_natasha(pseudo, natasha_layer):
    """Обратимость сохраняется и на сущностях Natasha."""
    text = (
        f"{FOREIGN_NAME} прибыл в {WHOLE_ADDRESS}, кв. 17.\n"
        "Остап Бендер подписал договор.\n"
        f"Водитель: {PERSON}, тел {PHONE}, паспорт {PASSPORT}\n"
        "Адрес: 183052, г. Мурманск, ул. Тестовая, д. 53, кв. 12\n"
    )
    assert pseudo.restore(*pseudo.anonymize(text)) == text

    safe, mapping = pseudo.anonymize(text)
    for secret in (FOREIGN_NAME, "Остап Бендер", PERSON, PHONE, PASSPORT,
                   "Тверская", "Тестовая", "926830", "123-45-67"):
        assert secret not in safe


@pytest.mark.slow
def test_logs_contain_no_pii_with_natasha(pseudo, natasha_layer, caplog):
    """В логах Natasha-прогона — только типы и количества, без значений."""
    text = f"{FOREIGN_NAME} прибыл в {WHOLE_ADDRESS}.\nВодитель: {PERSON}, тел {PHONE}\n"
    with caplog.at_level(logging.DEBUG, logger="core.pseudonymizer"):
        safe, mapping = pseudo.anonymize(text)
        pseudo.restore(safe, mapping)

    records = [r for r in caplog.records if r.name == "core.pseudonymizer"]
    assert records, "модуль не записал в лог ни одной строки"
    for record in records:
        rendered = f"{record.getMessage()} {record.args}"
        for secret in (FOREIGN_NAME, WHOLE_ADDRESS, PERSON, PHONE,
                       "Мураками", "Тверская", "123-45-67"):
            assert secret not in rendered
    assert "PERSON=" in caplog.text
    assert "ADDRESS=" in caplog.text
