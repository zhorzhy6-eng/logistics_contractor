#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты юридических подсказок (services/agent/tools/legal_hints.py).

Подсказка — это не проверка формы, а её «мягкая» версия:

* она никогда не меняет форму (проверяется тем, что входной словарь цел);
* она не повторяется и не частит (движок SuggestionEngine: 1 раз в 5 минут);
* без источника нормы она не показывается вовсе;
* выключается настройкой agent.legal_hints.

Часы подменяются: ждать пять минут в тесте нельзя.
"""

from __future__ import annotations

import copy

import pytest

from services.agent.tools import legal_hints as lh
from services.agent.tools import proactivity


#: Нарушение, как его отдаёт check_contract_legal.
VAT_VIOLATION = {
    "rule": "vat_rate",
    "title": "Ставка НДС",
    "severity": "error",
    "message": "Значение 20%, а по НК РФ ст. 164 п. 3 — 22%.",
    "source": "НК РФ ст. 164 п. 3",
    "field": "contract_tab.vat_rate",
    "current": "20%",
    "required": 22,
    "quote": "налоговой ставке 22 процента",
}

#: Подсказки для формы, где ставка НДС неверна.
BAD_VALUES = {"contract_tab.vat_rate": "20%"}


class FakeClock:
    """Часы, которые двигает тест."""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class FakeSettings:
    """Настройки приложения: только раздел agent."""

    def __init__(self, values):
        self._values = dict(values)

    def agent_settings(self):
        return dict(self._values)


class BrokenSettings:
    """Настройки, которые падают при чтении (файл занят, нет доступа)."""

    def agent_settings(self):
        raise RuntimeError("настройки недоступны")


# ─────────────────────────────────────────────────────────────
# 1. Текст подсказки
# ─────────────────────────────────────────────────────────────

def test_hint_text_ends_with_check_advice():
    """Подсказка заканчивается советом проверить — это требование задания."""
    text = proactivity.legal_hint_text(VAT_VIOLATION)
    assert text.endswith(proactivity.LEGAL_HINT_SUFFIX)
    assert text.startswith("Ставка НДС:")


def test_hint_text_contains_number_and_source():
    """В подсказке есть фактическое значение и норма со ссылкой."""
    text = proactivity.legal_hint_text(VAT_VIOLATION)
    assert "20%" in text
    assert "22%" in text
    assert "НК РФ ст. 164 п. 3" in text


def test_hint_text_without_source_is_empty():
    """Норма без ссылки — не подсказка: молчим, а не выдумываем."""
    violation = dict(VAT_VIOLATION, source="")
    assert proactivity.legal_hint_text(violation) == ""


def test_hint_text_without_message_is_empty():
    """Без текста нарушения подсказывать нечего."""
    assert proactivity.legal_hint_text(dict(VAT_VIOLATION, message="")) == ""
    assert proactivity.legal_hint_text({}) == ""
    assert proactivity.legal_hint_text("строка") == ""


def test_hint_text_is_shortened():
    """
    Слишком длинное нарушение обрезается — но по границе слова.

    Совет «Проверьте.» сохраняется всегда: подсказка — совет проверить,
    а не обрывок абзаца (на старом коде текст кончался на середине слова).
    """
    violation = dict(VAT_VIOLATION, message="Очень длинное объяснение. " * 40)
    text = proactivity.legal_hint_text(violation)
    assert len(text) <= proactivity.LEGAL_HINT_MAX_CHARS
    assert text.endswith("Проверьте.")
    assert text.endswith(" " + proactivity.LEGAL_HINT_SUFFIX)
    assert "…" in text


def test_hint_texts_skips_reference_rules():
    """Справочные правила и правила вне списка подсказок не подсказываются."""
    result = {"violations": [
        VAT_VIOLATION,
        dict(VAT_VIOLATION, rule="claim_period", title="Срок давности"),
        dict(VAT_VIOLATION, rule="payment_days", title="Срок оплаты"),
    ]}
    texts = proactivity.legal_hint_texts(result)
    assert len(texts) == 2
    assert all("НК РФ" in text for text in texts)


def test_hint_texts_deduplicates():
    """Одинаковая подсказка второй раз не добавляется."""
    result = {"violations": [VAT_VIOLATION, dict(VAT_VIOLATION)]}
    assert len(proactivity.legal_hint_texts(result)) == 1


def test_hint_texts_skips_empty_values():
    """
    Подсказка — про значение, а не про пустое поле.

    Именно «пустые» нарушения давали при запуске предупреждение о пустом
    маршруте: форма новая, оператор ещё ничего не вводил, а панель уже
    ругалась. Для незаполненных полей есть проверка формы по кнопке.
    """
    empty_route = dict(VAT_VIOLATION, rule="route_required",
                       title="Маршрут перевозки",
                       message="Поле «contract_tab.route» не заполнено.",
                       source="ПП РФ № 2200", current="")
    empty_days = dict(VAT_VIOLATION, rule="payment_days", title="Срок оплаты",
                      message="Поле «contract_tab.payment_days» не заполнено.",
                      source="ГК РФ ст. 790 п. 1", current=0)
    texts = proactivity.legal_hint_texts(
        {"violations": [empty_route, empty_days, VAT_VIOLATION]}
    )
    assert len(texts) == 1
    assert texts[0].startswith("Ставка НДС")


# ─────────────────────────────────────────────────────────────
# 2. Движок подсказок
# ─────────────────────────────────────────────────────────────

def test_interval_is_five_minutes():
    """Пауза по умолчанию — 5 минут (то же правило, что у подсказок)."""
    assert lh.LEGAL_HINT_INTERVAL == 300.0
    tracker = lh.LegalHintTracker()
    assert tracker.engine.interval == 300.0


def test_disabled_tracker_is_silent():
    """Выключенные подсказки не показываются вовсе."""
    tracker = lh.LegalHintTracker(enabled=False)
    assert tracker.refresh(values=BAD_VALUES, contract_type="perevozka") == []


def test_tracker_offers_hint_for_bad_value():
    """Неверная ставка НДС — подсказка появляется сразу."""
    clock = FakeClock()
    tracker = lh.LegalHintTracker(enabled=True, clock=clock)
    hints = tracker.refresh(values=BAD_VALUES, contract_type="perevozka")
    assert len(hints) == 1
    assert "22%" in hints[0]
    assert hints[0].endswith("Проверьте.")


def test_tracker_is_silent_without_violations():
    """Правильная форма — молчание, а не «всё хорошо» на каждом шаге."""
    tracker = lh.LegalHintTracker(enabled=True, clock=FakeClock())
    values = {"contract_tab.vat_rate": "22%", "contract_tab.route": "Москва — Тула"}
    assert tracker.refresh(values=values, contract_type="perevozka") == []


def test_tracker_does_not_repeat_same_hint():
    """Тот же текст второй раз не показывается."""
    clock = FakeClock()
    tracker = lh.LegalHintTracker(enabled=True, interval=0, clock=clock)
    assert tracker.refresh(values=BAD_VALUES) != []
    assert tracker.refresh(values=BAD_VALUES) == []


def test_tracker_respects_interval():
    """Новое нарушение раньше паузы не показывается, позже — показывается."""
    clock = FakeClock()
    tracker = lh.LegalHintTracker(enabled=True, interval=300, clock=clock)

    first = tracker.refresh(values=BAD_VALUES, contract_type="perevozka")
    assert first, "первая подсказка должна показаться"

    clock.advance(10)
    # Другое неверное значение: текст другой, а пауза та же.
    other = {"contract_tab.vat_rate": "21%"}
    assert tracker.refresh(values=other, contract_type="perevozka") == []

    clock.advance(300)
    assert tracker.refresh(values=other, contract_type="perevozka") != []


def test_tracker_is_silent_on_empty_form():
    """
    Пустая форма не подсказывает «поле не заполнено».

    Именно это происходило при запуске программы: панель открыта, форма
    пустая, и Максимилиан начинал разговор с упрёка. Проверка по кнопке
    «⚖» при этом работает как прежде — она явная.
    """
    tracker = lh.LegalHintTracker(enabled=True, clock=FakeClock())
    empty = {"contract_tab.route": "", "contract_tab.vat_rate": "",
             "vehicles": [{"vin": "", "brand_model": ""}]}
    assert tracker.refresh(values=empty, contract_type="perevozka") == []
    assert tracker.last_texts == []


def test_tracker_speaks_once_form_has_data():
    """Как только оператор что-то ввёл, непустая форма снова подсказывает."""
    tracker = lh.LegalHintTracker(enabled=True, clock=FakeClock())
    values = {"contract_tab.route": "", "contract_tab.vat_rate": "20%"}
    hints = tracker.refresh(values=values, contract_type="perevozka")
    assert hints, "заполненная форма с нарушением нормы должна подсказать"
    assert any("22%" in text for text in tracker.last_texts)


def test_tracker_skips_unchanged_form():
    """Форма не менялась — проверка не повторяется (лишняя работа и шум)."""
    calls = []

    def fake_check(**kwargs):
        calls.append(kwargs)
        return {"violations": [VAT_VIOLATION]}

    clock = FakeClock()
    tracker = lh.LegalHintTracker(enabled=True, interval=0, clock=clock,
                                  check=fake_check)
    tracker.refresh(values=BAD_VALUES)
    tracker.refresh(values=BAD_VALUES)
    assert len(calls) == 1


def test_tracker_calls_injected_check():
    """Проверка подменяется: тест не зависит от каталога правил."""
    seen = {}

    def fake_check(contract_type="", values=None, **_):
        seen["contract_type"] = contract_type
        seen["values"] = dict(values or {})
        return {"violations": [VAT_VIOLATION]}

    tracker = lh.LegalHintTracker(enabled=True, clock=FakeClock(), check=fake_check)
    tracker.refresh(values=BAD_VALUES, contract_type="arenda_ts")
    assert seen["contract_type"] == "arenda_ts"
    assert seen["values"] == BAD_VALUES


def test_tracker_does_not_modify_form():
    """Подсказка ничего не правит: значения формы остались прежними."""
    values = dict(BAD_VALUES)
    before = copy.deepcopy(values)
    tracker = lh.LegalHintTracker(enabled=True, clock=FakeClock())
    tracker.refresh(values=values, contract_type="perevozka")
    assert values == before


def test_tracker_reset_allows_repeat():
    """reset() забывает прошлую подсказку (сменилось окно — можно снова)."""
    clock = FakeClock()
    tracker = lh.LegalHintTracker(enabled=True, interval=300, clock=clock)
    assert tracker.refresh(values=BAD_VALUES) != []
    tracker.reset()
    assert tracker.refresh(values=BAD_VALUES) != []


def test_tracker_configure_turns_hints_off():
    """configure(False) гасит подсказки без пересоздания движка."""
    tracker = lh.LegalHintTracker(enabled=True, clock=FakeClock())
    tracker.configure(False)
    assert tracker.enabled is False
    assert tracker.refresh(values=BAD_VALUES) == []


def test_tracker_check_error_does_not_crash():
    """Упавшая проверка — молчание и запись в журнал, а не падение окна."""
    def broken(**kwargs):
        raise RuntimeError("каталог не прочитан")

    tracker = lh.LegalHintTracker(enabled=True, clock=FakeClock(), check=broken)
    assert tracker.refresh(values=BAD_VALUES) == []


def test_tracker_last_texts_reports_all_hints():
    """last_texts отдаёт все подсказки последней проверки (журнал, тесты)."""
    values = {"contract_tab.vat_rate": "20%",
              "driver_tab.license_expiry_date": "2020-01-01",
              "contract_tab.route": ""}
    tracker = lh.LegalHintTracker(enabled=True, clock=FakeClock())
    tracker.refresh(values=values, contract_type="perevozka")
    assert len(tracker.last_texts) >= 2


# ─────────────────────────────────────────────────────────────
# 3. Настройка agent.legal_hints
# ─────────────────────────────────────────────────────────────

def test_settings_enable_and_disable():
    """Настройка agent.legal_hints читается в обе стороны."""
    assert lh.enabled_from_settings(FakeSettings({"legal_hints": False})) is False
    assert lh.enabled_from_settings(FakeSettings({"legal_hints": True})) is True


def test_settings_default_is_enabled():
    """Ключа нет — подсказки включены (значение по умолчанию)."""
    assert lh.enabled_from_settings(FakeSettings({})) is lh.DEFAULT_ENABLED


def test_settings_error_falls_back_to_default():
    """Ошибка чтения настроек не выключает подсказки и не роняет окно."""
    assert lh.enabled_from_settings(BrokenSettings()) is lh.DEFAULT_ENABLED
