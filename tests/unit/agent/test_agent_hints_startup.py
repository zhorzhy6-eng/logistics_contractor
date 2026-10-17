#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Подсказки при запуске: тишина первые полминуты и пустая форма.

Зачем этот файл
---------------
Панель, открытая вместе с программой, через пять секунд после старта
выдавала юридическое предупреждение: «Маршрут перевозки: Поле
«contract_tab.route» не заполнено…». Форма в этот момент пустая ПО
ОПРЕДЕЛЕНИЮ — оператор только что запустил программу. Помощь, которая
начинает разговор с упрёка, — это не помощь.

Что проверяется
---------------
1. ``StartupQuiet`` — таймер тишины: сколько молчать, когда кончается,
   как снимается досрочно. Часы подменяются: ждать полминуты нельзя.
2. ``legal_hints.any_filled`` — есть ли в форме хоть одно заполненное
   значение (пустая строка таблицы — всё ещё пустая форма).
3. Связка: пустая форма не даёт подсказок, заполненная — даёт.

Qt здесь не нужен: это чистая логика (панель проверяет её через
``ui/agent_widget.py``, отдельный UI-тест — рядом, в tests/ui).
"""

from __future__ import annotations

import pytest

from services.agent.tools import legal_hints as lh
from services.agent.tools import proactivity


class FakeClock:
    """Часы, которые двигает тест."""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


# ─────────────────────────────────────────────────────────────
# 1. Таймер тишины
# ─────────────────────────────────────────────────────────────

def test_quiet_defaults_to_half_a_minute():
    """Тишина по умолчанию — 30 секунд (полминуты на «осмотреться»)."""
    assert proactivity.STARTUP_QUIET_SECONDS == 30.0


def test_quiet_is_active_right_after_start():
    """Сразу после запуска подсказки молчат."""
    clock = FakeClock()
    quiet = proactivity.StartupQuiet(clock=clock)
    quiet.start()

    assert quiet.active() is True
    assert quiet.remaining() == pytest.approx(30.0)

    clock.advance(29.9)
    assert quiet.active() is True


def test_quiet_ends_after_the_period():
    """Через полминуты подсказывать уже можно."""
    clock = FakeClock()
    quiet = proactivity.StartupQuiet(clock=clock)
    quiet.start()

    clock.advance(30.0)
    assert quiet.active() is False
    assert quiet.remaining() == 0.0


def test_quiet_without_start_does_not_block():
    """Таймер не включён — тишины нет (панель открыли вручную позже)."""
    quiet = proactivity.StartupQuiet(clock=FakeClock())
    assert quiet.active() is False


def test_quiet_stop_silences_nothing_is_immediate():
    """stop() снимает тишину немедленно (оператор попросил подсказку сам)."""
    clock = FakeClock()
    quiet = proactivity.StartupQuiet(clock=clock)
    quiet.start()
    quiet.stop()
    assert quiet.active() is False


def test_quiet_seconds_can_be_changed():
    """Период тишины задаётся при старте (тесты и настройка не ждут 30 с)."""
    clock = FakeClock()
    quiet = proactivity.StartupQuiet(seconds=5, clock=clock)
    quiet.start(seconds=10)

    clock.advance(9)
    assert quiet.active() is True
    clock.advance(2)
    assert quiet.active() is False


# ─────────────────────────────────────────────────────────────
# 2. Заполнена ли форма
# ─────────────────────────────────────────────────────────────

def test_empty_form_has_nothing_filled():
    """Пустая форма: пустые строки и пробелы — не содержимое."""
    assert lh.any_filled({}) is False
    assert lh.any_filled({"contract_tab.route": "", "contract_tab.vat_rate": None}) is False
    assert lh.any_filled({"contract_tab.route": "   "}) is False


def test_zero_counts_as_entered_value():
    """
    Ноль в поле — уже введённое значение (для «а вводил ли оператор что-то»).

    Это НЕ противоречит правилу «0.0 == falsy» в проверке формы: там ноль
    значит «не заполнено» (денежное поле с нулём смысла не имеет), а здесь
    вопрос другой — трогал ли оператор форму вообще.
    """
    assert lh.any_filled({"contract_tab.price": 0}) is True
    assert lh.any_filled({"contract_tab.price": 150000}) is True


def test_any_value_makes_form_filled():
    """Одно заполненное поле — форма уже не пустая."""
    assert lh.any_filled({"contract_tab.route": "Москва — Тула"}) is True


def test_empty_table_row_is_not_content():
    """
    Пустая строка таблицы — всё ещё пустая форма.

    Формы держат строки (машины, точки маршрута) в виде списков словарей,
    и в новой форме такая строка уже есть — но заполнять её оператор ещё
    не начал.
    """
    empty_rows = {"vehicles": [{"vin": "", "brand_model": ""}, {"vin": None}]}
    assert lh.any_filled(empty_rows) is False

    filled_rows = {"vehicles": [{"vin": "XW8ZZZ61ZJG012345"}]}
    assert lh.any_filled(filled_rows) is True


# ─────────────────────────────────────────────────────────────
# 3. Связка: пустая форма не подсказывает
# ─────────────────────────────────────────────────────────────

def test_empty_form_gives_no_hints():
    """Ровно случай запуска: маршрут пуст, но подсказывать нечего."""
    tracker = lh.LegalHintTracker(enabled=True, clock=FakeClock())
    assert tracker.refresh(values={"contract_tab.route": ""},
                           contract_type="perevozka") == []


def test_empty_field_is_not_a_hint():
    """
    Незаполненное поле — не повод для юридической подсказки.

    «Поле не заполнено» — состояние формы, а не разошедшееся с нормой
    значение. За незаполненными полями следят проверка формы по кнопке
    «⚖» и подсветка обязательных полей.
    """
    tracker = lh.LegalHintTracker(enabled=True, clock=FakeClock())
    values = {"contract_tab.number": "17102026-14",
              "contract_tab.route": "",
              "contract_tab.payment_days": 0}
    assert tracker.refresh(values=values, contract_type="perevozka") == []
    assert tracker.last_texts == []


def test_wrong_value_gives_hint():
    """А вот неверное значение нормы — это как раз повод подсказать."""
    tracker = lh.LegalHintTracker(enabled=True, clock=FakeClock())
    hints = tracker.refresh(values={"contract_tab.vat_rate": "20%",
                                    "contract_tab.route": ""},
                            contract_type="perevozka")
    assert hints
    assert "Ставка НДС" in hints[0]
    assert hints[0].endswith(proactivity.LEGAL_HINT_SUFFIX)
