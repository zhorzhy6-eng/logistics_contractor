#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Часть 1: ошибка внешнего сервиса DaData НЕ роняет автозаполнение (A–D).

Что проверяется
---------------
1. **Причина** (A). В журнале `logs/errors.log` строка
   `ERROR core.dadata_client clean_address:427 | DaData: HTTP 403 …` со
   строками `File …` — это НЕ падение: так `config/logging_config.py`
   (`StackInfoFilter`) дописывает стек вызова к любой записи уровня ERROR.
   Стек обрывается на `f.filter(record)` — то есть на самом логировании.
   Настоящий сбой выглядел бы иначе: последний кадр был бы в коде проекта.
   Тест `test_error_log_record_has_no_project_frame` это фиксирует.
2. **Фикс** (B). Отказ DaData — WARNING, а не ERROR; адрес возвращается КАК
   ЕСТЬ; исключение не выходит ни из клиента, ни из инструмента, ни из
   разбора текста.
3. **Проверка** (C). Текст оператора → `parse_and_fill` → форма
   заполняется, адреса остаются такими, как их написали.
4. **Тесты** (D). DaData отвечает 403 (MOCK на уровне `requests.post`),
   DaData отвечает ошибкой-исключением (MOCK на уровне клиента).

Сеть не трогается: `requests.post` подменён, ответ — настоящий
`requests.Response` со статусом 403.
"""

import logging
import os
import sys
from pathlib import Path

import pytest
import requests

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.agent.dadata_tools as dadata_tools  # noqa: E402
import core.dadata_client as dadata_client  # noqa: E402
from core.agent import fill_parser  # noqa: E402
from core.agent.fill_parser import (  # noqa: E402
    DADATA_UNAVAILABLE_NOTE, clean_address, parse_text,
)
from core.agent.tools import AgentContext, build_registry, set_context_window  # noqa: E402

#: Текст оператора из задания: две точки маршрута, то есть ДВА адреса.
OPERATOR_TEXT = (
    "Вины: EC37CUSM4TC008647\n"
    "Загрузка: Москва, Ленина 5, 15.10.2026\n"
    "Выгрузка: Казань, Пушкина 10, 17.10.2026\n"
    "Перевозчик: Ромашка\n"
    "Сумма: 80000, НДС 20"
)

#: Адрес, который DaData должна была бы стандартизировать.
RAW_ADDRESS = "Москва, Ленина 5"


# ─────────────────────────────────────────────────────────────
# Заглушки: окно, клиент, сеть
# ─────────────────────────────────────────────────────────────

class StubTab:
    """Вкладка формы: словарь полей и вызовы fill_data."""

    def __init__(self, data=None):
        self.data = dict(data or {})
        self.calls = []

    def get_data(self):
        return dict(self.data)

    def fill_data(self, values=None, *args):
        self.calls.append(values)
        if isinstance(values, dict):
            self.data.update(values)


class StubVehiclesTab(StubTab):
    """Таблица машин: принимает СПИСОК."""

    def __init__(self):
        super().__init__()
        self.rows = []

    def get_data(self):
        return list(self.rows)

    def fill_data(self, vehicles=None, *args, **kwargs):
        self.calls.append(list(vehicles or []))
        self.rows = [dict(v) for v in (vehicles or [])]


class StubWindow:
    """Окно «Экспедиторство» с теми же вкладками, что у MainWindow."""

    CONTRACT_TYPE = "perevozka"

    def __init__(self):
        self.carrier_tab = StubTab({"full_name": "", "inn": ""})
        self.driver_tab = StubTab({"full_name": ""})
        self.trailer_tab = None
        self.vehicles_tab = StubVehiclesTab()
        self.contract_tab = StubTab({
            "number": "", "route": "", "price_with_vat": 0.0, "vat_rate": "22%",
            "payment_days": 0, "date": "2026-01-01",
            "loadings": [], "unloadings": [],
        })
        self.customer_tab = StubTab({"full_name": ""})

    def _tab_keys(self):
        return ("carrier_tab", "driver_tab", "vehicles_tab",
                "contract_tab", "customer_tab")

    def _tab_by_key(self, key):
        return getattr(self, key, None)


def make_403_response(url: str) -> requests.Response:
    """Настоящий ответ requests со статусом 403 (так отвечает DaData)."""
    response = requests.Response()
    response.status_code = 403
    response._content = b'{"detail": "Invalid token"}'
    response.url = url
    response.request = requests.Request("POST", url).prepare()
    return response


@pytest.fixture
def dadata_down(monkeypatch):
    """
    DaData отвечает 403 на ЛЮБОЙ запрос (невалидный ключ или секрет).

    Подменяется только транспорт (`requests.post`): весь путь — клиент,
    разбор ответа, инструмент, автозаполнение — настоящий.
    """
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        calls.append({"url": url, "json": json, "headers": dict(headers or {})})
        return make_403_response(url)

    monkeypatch.setattr(requests, "post", fake_post)
    # Ключи «есть»: проверяем именно 403, а не отсутствие ключа.
    monkeypatch.setattr(dadata_client, "get_dadata_key", lambda: "test-key")
    monkeypatch.setattr(dadata_client, "get_dadata_secret", lambda: "test-secret")
    dadata_tools.clear_cache()
    yield calls
    dadata_tools.clear_cache()


@pytest.fixture
def window():
    """Окно-заглушка как «открытая форма» (снимается после теста)."""
    win = StubWindow()
    previous = set_context_window(win)
    yield win
    set_context_window(previous)


@pytest.fixture
def registry(window):
    """Реестр инструментов с окном-заглушкой."""
    return build_registry(AgentContext(window=window, contract_type="perevozka"))


# ─────────────────────────────────────────────────────────────
# A. Диагностика: что на самом деле было в логе
# ─────────────────────────────────────────────────────────────

class _CaptureRecords(logging.Handler):
    """Обычный обработчик, который складывает записи в список."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_error_log_record_has_no_project_frame():
    """
    Строки `File …` в errors.log — стек вызова, а не падение.

    `StackInfoFilter` (config/logging_config.py) дописывает стек к записям
    уровня ERROR, и стек обрывается на самом логировании
    (`logging/__init__.py`, `f.filter(record)`). Если бы приложение падало,
    последний кадр был бы в нашем коде. Запись проходит через НАСТОЯЩИЙ
    обработчик — так же, как в работающем приложении; сеть не нужна.
    """
    from config.logging_config import StackInfoFilter

    handler = _CaptureRecords()
    handler.addFilter(StackInfoFilter())
    logger = logging.getLogger("core.dadata_client")
    logger.addHandler(handler)
    try:
        logger.error("DaData: HTTP 403 — ключ или секрет не принят")
    finally:
        logger.removeHandler(handler)

    assert len(handler.records) == 1
    record = handler.records[0]
    assert record.stack_info, "стек должен быть дописан"

    lines = record.stack_info.rstrip().splitlines()
    assert lines[-1].strip() == "result = f.filter(record)", (
        "стек обрывается на самом логировании — значит, падения не было"
    )
    file_line = lines[-2].strip()
    assert file_line.startswith('File "') and "logging" in file_line, (
        f"последний кадр должен быть из стандартной библиотеки: {file_line}"
    )
    assert "core" not in file_line and "tests" not in file_line


def test_client_still_raises_on_403(dadata_down):
    """Клиент по-прежнему ЧЕСТНО сообщает об ошибке: контракт не изменён."""
    client = dadata_client.DadataClient(api_key="k", secret="s", timeout=1)

    with pytest.raises(RuntimeError) as exc:
        client.clean_address(RAW_ADDRESS)

    assert "403" in str(exc.value) or "ключ" in str(exc.value).lower()
    assert dadata_down, "запрос должен был уйти"


def test_403_is_logged_as_warning_not_error(dadata_down, caplog):
    """Отказ внешнего сервиса — WARNING: ERROR в журнале выглядел падением."""
    caplog.set_level(logging.DEBUG, logger="core.dadata_client")

    client = dadata_client.DadataClient(api_key="k", secret="s", timeout=1)
    with pytest.raises(RuntimeError):
        client.clean_address(RAW_ADDRESS)

    levels = {record.levelno for record in caplog.records}
    assert logging.ERROR not in levels, "403 не должен логироваться как ERROR"
    assert logging.WARNING in levels
    assert "403" in caplog.text


# ─────────────────────────────────────────────────────────────
# B, D.1. Инструмент: 403 → ok=False, без исключения
# ─────────────────────────────────────────────────────────────

def test_check_address_tool_returns_error_dict_on_403(dadata_down):
    """D.1. DaData 403 → инструмент отвечает словарём, а не исключением."""
    result = dadata_tools.check_address_dadata(address=RAW_ADDRESS)

    assert result["ok"] is False
    assert result["error"], "должно быть объяснение для оператора"
    assert result["source"] == "DaData"


def test_check_inn_tool_survives_403(dadata_down):
    """Тот же путь для реквизитов по ИНН (общее правило, B.3)."""
    result = dadata_tools.check_inn_dadata(inn="7701234567")

    assert result["ok"] is False
    assert "DaData" in result["error"]


def test_search_company_tool_survives_403(dadata_down):
    """Поиск по названию тоже не роняет запрос."""
    result = dadata_tools.search_company_dadata(query="Ромашка")

    assert result["ok"] is False
    assert "DaData" in result["error"]


def test_compare_with_dadata_survives_403(dadata_down, isolated_db):
    """Сверка с ЕГРЮЛ при недоступной DaData — понятный отказ, не падение."""
    from db.crud.organizations import save_organization

    record_id = save_organization(
        {"full_name": "ООО «Ромашка»", "inn": "7701234567"}, is_carrier=True
    )

    result = dadata_tools.compare_with_dadata(entity_type="carrier",
                                              entity_id=record_id)

    assert result["ok"] is False


def test_unexpected_exception_is_also_swallowed(monkeypatch):
    """
    B.3. Ловится ЛЮБОЕ исключение, а не только ValueError/RuntimeError.

    Системное хранилище ключей, сеть и разбор ответа поднимают и другие
    ошибки; из инструмента они выходить не должны.
    """
    monkeypatch.setattr(dadata_tools, "_client",
                        lambda: (_ for _ in ()).throw(OSError("keyring сломался")))
    dadata_tools.clear_cache()

    result = dadata_tools.check_address_dadata(address=RAW_ADDRESS)

    assert result["ok"] is False
    assert "keyring" in result["error"]


# ─────────────────────────────────────────────────────────────
# B.1, D.2. Адрес берётся как есть
# ─────────────────────────────────────────────────────────────

def test_clean_address_returns_address_as_is_on_403(dadata_down):
    """D.2. Не удалось стандартизировать — возвращается исходный адрес."""
    cleaned, note = clean_address(RAW_ADDRESS)

    assert cleaned == RAW_ADDRESS, "адрес должен вернуться КАК ЕСТЬ"
    assert note == DADATA_UNAVAILABLE_NOTE


def test_clean_address_returns_address_as_is_on_client_failure(monkeypatch):
    """То же, когда инструмент отвечает ошибкой (не исключением)."""
    monkeypatch.setattr(
        dadata_tools, "check_address_dadata",
        lambda address="", **_: {"ok": False, "error": "нет секретного ключа"},
    )

    cleaned, note = clean_address(RAW_ADDRESS)

    assert cleaned == RAW_ADDRESS
    assert note == DADATA_UNAVAILABLE_NOTE


def test_clean_address_survives_broken_tool(monkeypatch):
    """Даже исключение из самого инструмента не уходит наружу."""
    def boom(*args, **kwargs):
        raise MemoryError("совсем плохо")

    monkeypatch.setattr(dadata_tools, "check_address_dadata", boom)

    cleaned, note = clean_address(RAW_ADDRESS)

    assert cleaned == RAW_ADDRESS
    assert note == DADATA_UNAVAILABLE_NOTE


def test_clean_address_logs_warning_not_error(monkeypatch, caplog):
    """B.1. В лог идёт warning, а не error."""
    monkeypatch.setattr(
        dadata_tools, "check_address_dadata",
        lambda address="", **_: {"ok": False, "error": "нет ключа"},
    )
    caplog.set_level(logging.DEBUG, logger="core.agent.fill_parser")

    clean_address(RAW_ADDRESS)

    levels = {record.levelno for record in caplog.records}
    assert logging.WARNING in levels
    assert logging.ERROR not in levels


def test_empty_address_is_not_sent_anywhere(dadata_down):
    """Пустой адрес — не ошибка, запроса нет."""
    assert clean_address("") == ("", "")
    assert clean_address("   ") == ("", "")
    assert dadata_down == []


def test_successful_cleaning_still_works(monkeypatch):
    """Успешный ответ DaData по-прежнему подставляется (фикс ничего не сломал)."""
    monkeypatch.setattr(
        dadata_tools, "check_address_dadata",
        lambda address="", **_: {
            "ok": True,
            "data": {"address": "г Москва, ул Ленина, д 5"},
        },
    )

    cleaned, note = clean_address(RAW_ADDRESS)

    assert cleaned == "г Москва, ул Ленина, д 5"
    assert note == ""


# ─────────────────────────────────────────────────────────────
# C, D.1. Автозаполнение при невалидном ключе DaData
# ─────────────────────────────────────────────────────────────

def test_parse_text_survives_403(dadata_down):
    """D.1. Разбор текста при 403 работает — без стандартизации адресов."""
    parsed = parse_text(OPERATOR_TEXT, use_dadata=True)

    loading = parsed.point_rows("loading")
    unloading = parsed.point_rows("unloading")
    assert loading[0]["address"] == "Москва, Ленина 5"
    assert unloading[0]["address"] == "Казань, Пушкина 10"
    assert DADATA_UNAVAILABLE_NOTE in parsed.warnings
    assert parsed.warnings.count(DADATA_UNAVAILABLE_NOTE) == 1, (
        "замечание показывается один раз, а не на каждый адрес"
    )


def test_parse_and_fill_fills_form_when_dadata_is_down(registry, window,
                                                       dadata_down):
    """
    D.1, C.1–C.3. «Подтвердить» при 403: форма заполняется.

    Проверяется весь путь: текст → разбор → инструмент → вкладки формы.
    """
    result = registry.get("parse_and_fill").function(
        text=OPERATOR_TEXT, use_dadata=True
    )

    assert result.get("ok") is True
    contract = window.contract_tab.get_data()
    assert contract["price_with_vat"] == 80000.0
    assert len(window.vehicles_tab.get_data()) == 1
    assert window.vehicles_tab.get_data()[0]["vin"] == "EC37CUSM4TC008647"
    assert [row["address"] for row in contract["loadings"]] == ["Москва, Ленина 5"]
    assert [row["address"] for row in contract["unloadings"]] == [
        "Казань, Пушкина 10"
    ]


def test_parse_and_fill_reports_dadata_problem_to_operator(registry, window,
                                                           dadata_down):
    """Оператор видит, что адреса не стандартизированы (а не молчание)."""
    result = registry.get("parse_and_fill").function(
        text=OPERATOR_TEXT, use_dadata=True
    )

    #: Модель получает предупреждение в результате инструмента,
    #: оператор — в сводке диалога превью (ui/agent_fill_preview.py).
    assert DADATA_UNAVAILABLE_NOTE in (result.get("data") or {}).get("warnings", [])


def test_other_enrichment_still_works_without_dadata(registry, window,
                                                      isolated_db, dadata_down):
    """База и VIN обогащаются независимо от DaData."""
    from db.crud.organizations import save_organization

    save_organization({"full_name": "ООО «Ромашка»", "short_name": "Ромашка",
                       "inn": "7701234567"}, is_carrier=True)

    result = registry.get("parse_and_fill").function(
        text=OPERATOR_TEXT, use_dadata=True
    )

    assert result.get("ok") is True
    assert window.carrier_tab.get_data().get("full_name") == "ООО «Ромашка»"
    assert window.carrier_tab.get_data().get("inn") == "7701234567"


def test_dadata_disabled_means_no_requests(dadata_down):
    """use_dadata=False — внешний сервис не спрашивают вовсе."""
    parsed = parse_text(OPERATOR_TEXT, use_dadata=False)

    assert dadata_down == []
    assert parsed.point_rows("loading")[0]["address"] == "Москва, Ленина 5"
    assert DADATA_UNAVAILABLE_NOTE not in parsed.warnings


def test_clean_points_survives_broken_clean_address(monkeypatch):
    """B.3. Сбой в самом clean_address не отменяет разбор."""
    def boom(address):
        raise RuntimeError("непредвиденное")

    monkeypatch.setattr(fill_parser, "clean_address", boom)

    result = parse_text(OPERATOR_TEXT, use_dadata=True)

    assert result.point_rows("loading")[0]["address"] == "Москва, Ленина 5"
    assert DADATA_UNAVAILABLE_NOTE in result.warnings


def test_clean_points_failure_is_logged_once(monkeypatch, caplog):
    """Внешний сбой попадает в лог ровно один раз на разбор."""
    monkeypatch.setattr(
        fill_parser, "clean_address",
        lambda address: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    caplog.set_level(logging.WARNING, logger="core.agent.fill_parser")

    parse_text(OPERATOR_TEXT, use_dadata=True)

    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING
                and "адреса не стандартизированы" in r.getMessage()]
    assert len(warnings) == 1


def test_overrides_do_not_wipe_route_points(registry, window, dadata_down):
    """
    Правка точки маршрута строкой не стирает маршрут.

    Точки — СПИСОК строк, а не поле; строка вместо списка оставила бы форму
    без адресов вовсе. Такие правки диалог превью не присылает (адрес
    правится в исходном тексте), но инструмент обязан быть устойчивым.
    """
    first = registry.get("parse_and_fill").function(
        text=OPERATOR_TEXT, use_dadata=True
    )
    assert first.get("ok") is True

    items = [{"section": "loading", "field": "address",
              "value": "Тверь, Советская 12"}]
    second = registry.get("parse_and_fill").function(
        text=OPERATOR_TEXT, use_dadata=True, items=items
    )

    assert second.get("ok") is True
    addresses = [row["address"]
                 for row in window.contract_tab.get_data()["loadings"]]
    assert addresses == ["Москва, Ленина 5"], "маршрут не должен пропасть"
