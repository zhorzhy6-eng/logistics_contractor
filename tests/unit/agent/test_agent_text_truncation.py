#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Обрезка текста в подсказках агента: по границе слова, а не посередине.

Зачем этот файл
---------------
Оператор видел при запуске предупреждение, оборванное на полуслове:

    «Маршрут перевозки: Поле «contract_tab.route» не заполнено. … Пустой
    маршрут оставляет дого…»

Причина — не раскладка панели, а сама функция обрезки: она резала строку
по СЧЁТУ СИМВОЛОВ (`text[:limit - 1] + "…"`) и ставила многоточие ровно
там, где кончилась арифметика. Такой текст читается как ошибка программы:
непонятно, что именно хотел сказать помощник.

Правила, которые проверяются здесь
----------------------------------
1. Текст короче лимита не меняется вовсе (и многоточия в нём нет).
2. Текст ровно по лимиту не меняется (это ещё не «длинный»).
3. Текст длиннее лимита режется по границе слова; результат всегда не
   длиннее лимита.
4. Многоточие появляется ТОЛЬКО если текст реально обрезан.
5. Плейсхолдер шаблона (`{{…}}`, `{%…%}`, `<<…>>`) не разрывается.
6. Юридическая подсказка — законченная мысль: режется по концу
   предложения, а совет «Проверьте.» сохраняется всегда.

Тесты написаны ДО правки и на старом коде падают (обрезка по символам).
"""

from __future__ import annotations

from core.utils.text_truncate import ELLIPSIS, shorten

# ─────────────────────────────────────────────────────────────
# 1. Размер: короткий, ровно по лимиту, на символ длиннее
# ─────────────────────────────────────────────────────────────

def test_short_text_is_unchanged():
    """Короткий текст не трогается: ни обрезки, ни многоточия."""
    text = "Маршрут заполнен"
    assert shorten(text, 60) == text
    assert ELLIPSIS not in shorten(text, 60)


def test_text_exactly_at_limit_is_unchanged():
    """Текст ровно по лимиту — ещё не длинный: многоточия быть не должно."""
    text = "я" * 40
    assert shorten(text, 40) == text
    assert ELLIPSIS not in shorten(text, 40)


def test_text_one_char_over_limit_is_shortened():
    """Один символ сверх лимита — уже обрезка (и видно, что она была)."""
    text = "я" * 41
    result = shorten(text, 40)
    assert result.endswith(ELLIPSIS)
    assert len(result) <= 40


def test_empty_and_broken_input_does_not_raise():
    """Пустое значение и None — пустая строка, а не исключение."""
    assert shorten("", 10) == ""
    assert shorten(None, 10) == ""


# ─────────────────────────────────────────────────────────────
# 2. Граница слова
# ─────────────────────────────────────────────────────────────

def test_long_text_is_cut_by_word_boundary():
    """Обрезка идёт по последнему пробелу перед лимитом, а не по счёту."""
    text = "Раз два три четыре пять шесть семь"
    result = shorten(text, 20)
    # всё, что осталось до многоточия, — целые слова из исходной строки
    head = result[: -len(ELLIPSIS)]
    assert text.startswith(head)
    assert not head.endswith(" ")
    assert head.split()[-1] in text.split()
    assert len(result) <= 20


def test_cut_word_is_dropped_not_broken():
    """Недописанного слова в конце нет: оно отбрасывается целиком."""
    text = "Маршрут перевозки: Поле не заполнено"
    result = shorten(text, 20)
    head = result[: -len(ELLIPSIS)]
    assert head.split()[-1] == "перевозки"
    assert "запол" not in result


def test_word_cut_does_not_eat_the_whole_text():
    """
    Если пробел нашёлся только в самом начале, режется по символам.

    Иначе одна длинная «слово-строка» (номер, VIN, адрес без пробелов)
    превращалась бы в пустоту с многоточием.
    """
    text = "а" * 300
    result = shorten(text, 50)
    assert len(result) > 20
    assert result.endswith(ELLIPSIS)


def test_result_never_exceeds_limit():
    """Лимит — жёсткая граница при любых входных данных."""
    text = "Очень длинная строка с пробелами " * 20
    for limit in range(2, 130):
        assert len(shorten(text, limit)) <= limit


def test_tiny_limit_does_not_raise():
    """Лимит меньше длины многоточия — просто жёсткая обрезка."""
    assert len(shorten("abcdefgh", 1)) <= 1
    assert len(shorten("abcdefgh", 0)) == 0


# ─────────────────────────────────────────────────────────────
# 3. То, что обрезать нельзя: плейсхолдеры и числа
# ─────────────────────────────────────────────────────────────

def test_placeholder_is_not_broken():
    """Плейсхолдер шаблона не разрывается посередине."""
    text = "Заполните {{contract_tab.route}} в шаблоне договора"
    for limit in (12, 20, 30, 40):
        result = shorten(text, limit)
        assert result.count("{{") == result.count("}}")
        assert "{{contract_tab.route" not in result or \
            "{{contract_tab.route}}" in result


def test_block_tag_is_not_broken():
    """Тег условного блока `{%p if %}` тоже не режется пополам."""
    text = "Проверьте блок {%p if arenda %} и его пару {%p endif %}"
    for limit in (15, 25, 35, 45):
        result = shorten(text, limit)
        assert result.count("{%") == result.count("%}")


def test_mask_placeholder_is_not_broken():
    """Маска ПДн (`<<PERSON_1>>`) остаётся целой: иначе это утечка вида."""
    text = "В договоре указан перевозчик <<PERSON_1>> и его адрес"
    for limit in (14, 24, 34, 44):
        result = shorten(text, limit)
        assert result.count("<<") == result.count(">>")


def test_numbers_are_not_broken():
    """Число и ставка в юридическом тексте не обрезаются по цифрам."""
    text = "Штраф 1234567890 рублей за простой вагона"
    result = shorten(text, 22)
    if "1234567890" in text[:22]:
        assert "1234567890" in result


# ─────────────────────────────────────────────────────────────
# 4. Юридическая подсказка: законченная мысль
# ─────────────────────────────────────────────────────────────

def _route_violation() -> dict:
    """Настоящее нарушение формы из каталога правил (пустой маршрут)."""
    from services.agent.tools import legal_check

    result = legal_check.check_contract_legal(
        contract_type="perevozka", values={"contract_tab.route": ""},
    )
    violations = [item for item in result.get("violations") or []
                  if item.get("rule") == "route_required"]
    assert violations, "каталог правил должен давать нарушение по маршруту"
    return violations[0]


def test_route_hint_is_not_cut_mid_word():
    """
    Живой случай оператора: предупреждение про маршрут читается ЦЕЛИКОМ.

    Текст нарушения — 351 символ, с заголовком и советом — около 381.
    На старом коде (лимит 260) он обрывался на «оставляет дого…».
    """
    from services.agent.tools import proactivity

    text = proactivity.legal_hint_text(_route_violation())
    assert "дого" + ELLIPSIS not in text
    assert text.endswith(proactivity.LEGAL_HINT_SUFFIX)
    assert "оставляет договор без согласованного пути перевозки" in text
    assert "Правила перевозок грузов" in text
    assert len(text) <= proactivity.LEGAL_HINT_MAX_CHARS


def test_hint_limit_is_readable():
    """Лимит подсказки — не «телеграфные» 60 символов и не обрубок."""
    from services.agent.tools import proactivity

    assert proactivity.LEGAL_HINT_MAX_CHARS >= 300
    assert proactivity.LEGAL_HINT_MAX_CHARS <= 600


def test_long_hint_is_cut_by_sentence_and_keeps_advice():
    """
    Очень длинное нарушение режется по концу предложения.

    Совет «Проверьте.» при этом обязан остаться: подсказка — совет
    проверить, а не приказ (требование задания).
    """
    from services.agent.tools import proactivity

    violation = {
        "rule": "vat_rate",
        "title": "Ставка НДС",
        "message": "Значение 20% не совпадает с нормой. " * 40,
        "source": "НК РФ ст. 164 п. 3",
    }
    text = proactivity.legal_hint_text(violation)
    assert text.endswith(proactivity.LEGAL_HINT_SUFFIX)
    assert text.endswith(" " + proactivity.LEGAL_HINT_SUFFIX)
    assert len(text) <= proactivity.LEGAL_HINT_MAX_CHARS
    assert text.startswith("Ставка НДС: Значение 20%")
    # обрезано по концу предложения: точка снята, стоит многоточие
    assert "с нормой" + ELLIPSIS in text


# ─────────────────────────────────────────────────────────────
# 5. Служебный ответ агента: значение поля тоже режется по слову
# ─────────────────────────────────────────────────────────────

def test_short_value_cuts_by_word_boundary():
    """Короткое значение поля в служебном ответе не рвётся пополам."""
    from services.agent.core.agent import _short_value

    source = "Москва — Тула, обратно пустым"
    text = _short_value(source, limit=20)
    assert text.endswith(ELLIPSIS)
    head = text[: -len(ELLIPSIS)]
    assert source.startswith(head)
    # обрезка пришлась на промежуток между словами, а не на слово
    assert source[len(head)] in " ,;"


def test_short_value_keeps_short_text():
    """Короткое значение не обрезается и многоточия не получает."""
    from services.agent.core.agent import _short_value

    assert _short_value("Ромашка") == "Ромашка"
    assert ELLIPSIS not in _short_value("Ромашка")
