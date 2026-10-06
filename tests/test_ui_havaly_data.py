#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты сборки данных заявки Хавалов из вкладок (ЭТАП 3.1.E.B.1).

Проверяется ui/windows/havaly/data.py::collect_havaly_data: раскладка полей
шести вкладок по схеме ответа промпта (core/prompts/havaly.py), закрытие
стыков «вкладка → сборщик → генератор/валидатор», устойчивость сборки
(нет вкладки, нет get_data(), get_data() упал, вернул не словарь, окно
без вкладок) и логи без ПДн.

Qt в тестах не поднимается: вкладки подменяются словарями и простыми
объектами-заглушками со своим get_data(). Отдельные тесты проверяют, что
собранных данных достаточно генератору (.xlsx) и валидатору типа: генератор
принимает результат сборщика как есть, а не как ContractData.

Все данные синтетические, реальных ПДн нет.
"""

import html
import io
import logging
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from core.contracts.zayavka.generator import (
    CARRIER_NAME,
    CUSTOMER_NAME,
    DATE_FIELDS as GENERATOR_DATE_FIELDS,
    VEHICLE_KEYS,
    ZAYAVKA_SCHEMA_ORDER,
    ZayavkaExcelGenerator,
)
from core.contracts.zayavka.validator import ZayavkaExcelValidator
from core.prompts.havaly import PROMPT
from ui.windows.havaly.data import (
    DATE_FIELDS,
    MAX_VEHICLES,
    SECTION_TITLES,
    VEHICLE_FIELDS,
    collect_havaly_data,
)

# ─────────────────────────────────────────────────────────────
# Синтетические данные (реальных ПДн нет)
# ─────────────────────────────────────────────────────────────

#: Дата заявки и план погрузки: вкладки отдают ISO, схема ждёт ДД.ММ.ГГГГ.
DATE_ISO = "2026-10-06"
DATE_DOC = "06.10.2026"
LOADING_PLAN_DATE_ISO = "2026-10-08"
LOADING_PLAN_DATE_DOC = "08.10.2026"
LOADING_PLAN_TIME_FULL = "09:00:00"
LOADING_PLAN_TIME = "09:00"

#: Номер лота: в шапке заявки и в строке таблицы он один и тот же.
LOT_NUMBER = "ЛОТ-2026-001"

#: Маршрут: город и пункт — разные поля, склеивать их нельзя.
LOADING_CITY = "г. Москва"
LOADING_POINT = "Склад Север, ул. Складская, д. 1"
UNLOADING_CITY = "г. Казань"
UNLOADING_POINT = "Площадка Юг, ул. Промышленная, д. 5"

#: Автовоз и прицеп.
TRACTOR_BRAND = "КАМАЗ-5490"
TRACTOR_COLOR = "Белый"
TRACTOR_PLATE = "А001АА77"
TRAILER_BRAND = "Тонар-9741"
TRAILER_PLATE = "БВ002277"

#: Водитель. VIN машин — 17 символов без букв I, O, Q.
DRIVER_LAST_NAME = "Иванов"
DRIVER_FIRST_NAME = "Иван"
DRIVER_MIDDLE_NAME = "Иванович"
DRIVER_LICENSE_NUMBER = "99 АА 123456"
DRIVER_LICENSE_ISSUE_DATE_ISO = "2020-01-01"
DRIVER_LICENSE_ISSUE_DATE_DOC = "01.01.2020"
DRIVER_PASSPORT_SERIES = "18 22"
DRIVER_PASSPORT_NUMBER = "926830"
DRIVER_PASSPORT_ISSUER = "Отделом УФМС России по г. Москве"
DRIVER_PASSPORT_ISSUE_DATE_ISO = "2023-01-30"
DRIVER_PASSPORT_ISSUE_DATE_DOC = "30.01.2023"
DRIVER_CITIZENSHIP = "Российская Федерация"
DRIVER_BIRTH_DATE_ISO = "1980-01-01"
DRIVER_BIRTH_DATE_DOC = "01.01.1980"
DRIVER_REGISTRATION = "г. Москва, ул. Водительская, д. 3"
DRIVER_PHONE = "+7 (999) 123-45-67"

#: Перевозимые машины: одна строка таблицы — одна машина.
VIN_1 = "XTC651150N0001001"
BRAND_1 = "Haval"
MODEL_1 = "Jolion"
DEALER_1 = "АвтоДилер-Тест"
DEALER_CODE_1 = "D-001"
BRAND_2 = "Haval"
MODEL_2 = "F7"
DEALER_2 = "АвтоДилер-Юг"
DEALER_CODE_2 = "D-002"

#: Ставка с НДС и ставка НДС строкой.
PRICE_WITH_VAT = 1234.56
PRICE_WITH_VAT_TEXT = "1 234,56 руб."
VAT_RATE = "22%"
VAT_RATE_NUM = 22.0

#: Названия вкладок окна (TAB_CONFIGS в ui/windows/havaly/window.py).
TAB_TITLES = ("Заявка", "Груз", "Маршрут", "Водитель", "ТС", "Стоимость")

#: Префиксы данных для теста логов без ПДн: проверяются по ним.
PII_SAMPLES = {
    "PII-LASTNAME": DRIVER_LAST_NAME,
    "PII-PASSPORT": DRIVER_PASSPORT_NUMBER,
    "PII-VIN": VIN_1,
    "PII-PHONE": DRIVER_PHONE,
    "PII-POINT": LOADING_POINT,
    "PII-DEALER": DEALER_1,
}


# ─────────────────────────────────────────────────────────────
# Вкладки: словари и объекты-заглушки
# ─────────────────────────────────────────────────────────────

def filled_zayavka_tab() -> Dict[str, Any]:
    """Вкладка «Заявка»: номер и дата заявки, стороны."""
    return {
        "date": DATE_ISO,
        "lot_number": LOT_NUMBER,
        "customer_name": CUSTOMER_NAME,
        "carrier_name": CARRIER_NAME,
    }


def filled_cargo_tab() -> Dict[str, Any]:
    """Вкладка «Груз»: перевозимые машины."""
    return {
        "vehicles": [
            {
                "vin": VIN_1,
                "brand": BRAND_1,
                "model": MODEL_1,
                "dealer": DEALER_1,
                "dealer_code": DEALER_CODE_1,
            },
            {
                "vin": "",
                "brand": BRAND_2,
                "model": MODEL_2,
                "dealer": DEALER_2,
                "dealer_code": DEALER_CODE_2,
            },
        ]
    }


def filled_route_tab() -> Dict[str, Any]:
    """Вкладка «Маршрут»: места погрузки и разгрузки, план погрузки."""
    return {
        "loading_city": LOADING_CITY,
        "loading_point": LOADING_POINT,
        "unloading_city": UNLOADING_CITY,
        "unloading_point": UNLOADING_POINT,
        "loading_plan_date": LOADING_PLAN_DATE_ISO,
        "loading_plan_time": LOADING_PLAN_TIME_FULL,
    }


def filled_driver_tab() -> Dict[str, Any]:
    """Вкладка «Водитель»: тринадцать полей водителя."""
    return {
        "driver_last_name": DRIVER_LAST_NAME,
        "driver_first_name": DRIVER_FIRST_NAME,
        "driver_middle_name": DRIVER_MIDDLE_NAME,
        "driver_license_number": DRIVER_LICENSE_NUMBER,
        "driver_license_issue_date": DRIVER_LICENSE_ISSUE_DATE_ISO,
        "driver_passport_series": DRIVER_PASSPORT_SERIES,
        "driver_passport_number": DRIVER_PASSPORT_NUMBER,
        "driver_passport_issuer": DRIVER_PASSPORT_ISSUER,
        "driver_passport_issue_date": DRIVER_PASSPORT_ISSUE_DATE_ISO,
        "driver_citizenship": DRIVER_CITIZENSHIP,
        "driver_birth_date": DRIVER_BIRTH_DATE_ISO,
        "driver_registration": DRIVER_REGISTRATION,
        "driver_phone": DRIVER_PHONE,
    }


def filled_vehicle_tab() -> Dict[str, Any]:
    """Вкладка «ТС»: автовоз и прицеп."""
    return {
        "tractor_brand": TRACTOR_BRAND,
        "tractor_color": TRACTOR_COLOR,
        "tractor_plate": TRACTOR_PLATE,
        "trailer_brand": TRAILER_BRAND,
        "trailer_plate": TRAILER_PLATE,
    }


def filled_price_tab() -> Dict[str, Any]:
    """Вкладка «Стоимость»: ставка с НДС и ставка НДС."""
    return {"price_with_vat": PRICE_WITH_VAT, "vat_rate": VAT_RATE}


def filled_tabs() -> Dict[str, Any]:
    """Все шесть вкладок окна, заполненные полностью."""
    return {
        "zayavka": filled_zayavka_tab(),
        "cargo": filled_cargo_tab(),
        "route": filled_route_tab(),
        "driver": filled_driver_tab(),
        "vehicle": filled_vehicle_tab(),
        "price": filled_price_tab(),
    }


class FakeTab:
    """
    Вкладка-заглушка: отдаёт то, что положено, или падает, как велено.

    Настоящие вкладки (ЭТАП 3.1.E.B.2) устроены так же: у них есть
    ``get_data()``, возвращающий словарь полей. Тесты сборщика Qt не
    поднимают — вкладка подменяется этим объектом.
    """

    def __init__(self, data: Any = None, error: Exception = None):
        self._data = {} if data is None else data
        self._error = error
        self.calls = 0

    def get_data(self) -> Any:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._data


class BrokenTab:
    """Вкладка без get_data() — так выглядит ещё не написанная вкладка."""


class FakeWindow:
    """
    Окно-заглушка: вкладки лежат атрибутами ``<раздел>_tab``.

    Так же держит вкладки настоящее окно типа (см. ui/windows/arenda_ts/
    window.py). Атрибутов может не быть вовсе — сборщик переживает и это.
    """

    def __init__(self, **tabs: Any):
        for key, tab in tabs.items():
            setattr(self, f"{key}_tab", tab)


class ExplodingWindow:
    """
    Окно, атрибуты которого падают при чтении.

    Худший случай частичного окна: сборщик не должен поднимать исключение
    наружу, даже если окно сломано (ЭТАП 3.1.E.B.3 ещё впереди).
    """

    @property
    def zayavka_tab(self):
        raise RuntimeError("окно сломано")


class FakeTabContainer:
    """
    Контейнер вкладок окна — как QTabWidget каркаса (window.tabs).

    Каркасное окно Хавалов держит вкладки одним контейнером и не заводит
    атрибутов ``<раздел>_tab``. Сборщик должен работать и с ним: вкладка
    ищется по заголовку (indexOf → widget).
    """

    def __init__(self, tabs: List[Any]):
        self._tabs = list(tabs)

    def indexOf(self, title: str) -> int:  # noqa: N802 — имя метода Qt
        for index, (name, _tab) in enumerate(self._tabs):
            if name == title:
                return index
        return -1

    def widget(self, index: int) -> Any:
        return self._tabs[index][1]


class SkeletonWindow:
    """Каркасное окно: вкладки только в контейнере, атрибутов нет."""

    def __init__(self, container: Any):
        self.tabs = container


# ─────────────────────────────────────────────────────────────
# Помощники
# ─────────────────────────────────────────────────────────────

def zayavka_of(data: Dict[str, Any]) -> Dict[str, Any]:
    """Блок заявки из результата сборки."""
    block = data.get("zayavka")
    assert isinstance(block, dict), "сборщик не вернул блок zayavka"
    return block


def book_text_of(path: Path) -> str:
    """
    Текст ячеек книги .xlsx — из листа и таблицы строк.

    .xlsx — это zip: строки лежат либо прямо в листе (``<is><t>значение</t>``
    или rich-text run), либо в xl/sharedStrings.xml. Проверка читает оба
    места и раскрывает HTML-мнемоники (openpyxl пишет кириллицу как
    ``&#1047;``), поэтому не зависит от того, как сохранено значение.
    """
    text = []
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if not (name.startswith("xl/worksheets/sheet")
                    or name == "xl/sharedStrings.xml"):
                continue
            chunk = archive.read(name).decode("utf-8", errors="replace")
            text.extend(re.findall(r"<t[^>]*>(.*?)</t>", chunk, flags=re.S))
    return html.unescape("\n".join(text))


def log_lines(caplog) -> List[str]:
    """Сообщения, попавшие в лог во время теста."""
    return [record.getMessage() for record in caplog.records]


class TempFolder:
    """
    Временная папка в tests/_tmp — уборка после теста.

    Своя папка нужна там, где проверяется генератор: он пишет готовый файл
    в output_dir, а имя файла зависит только от даты заявки. Штатный
    tmp_path не используется: в проекте для временных файлов есть work_dir
    (tests/_tmp), там же лежат файлы остальных тестов.
    """

    def __init__(self, work_dir: Path, name: str):
        self.path = work_dir / f"tmp_{uuid.uuid4().hex[:8]}_{name}"
        self.path.mkdir(parents=True, exist_ok=True)

    def files(self) -> List[Path]:
        """Файлы, созданные в папке (без подпапок)."""
        return sorted(self.path.glob("*"))

    def cleanup(self) -> None:
        """Удаляет папку со всем содержимым."""
        shutil.rmtree(self.path, ignore_errors=True)


# ─────────────────────────────────────────────────────────────
# Пустое и частичное окно
# ─────────────────────────────────────────────────────────────

def test_collect_empty_window_returns_valid_dict() -> None:
    """Пустое окно: сборщик возвращает валидный dict, машин нет."""
    data = collect_havaly_data({})

    assert isinstance(data, dict)
    assert isinstance(zayavka_of(data), dict)
    assert data["vehicles"] == []
    assert data["contract"]["contract"] == zayavka_of(data)


def test_collect_empty_window_fills_fixed_parties() -> None:
    """
    Стороны заявки заполнены всегда — даже на пустом окне.

    Это не выдумывание данных: Заказчик и Перевозчик в бланке напечатаны,
    от заявки к заявке не меняются, и промпт требует заполнять их всегда
    («пустыми они не бывают»). Те же константы подставляет генератор,
    когда читает форму.
    """
    zayavka = zayavka_of(collect_havaly_data({}))

    assert zayavka["customer_name"] == CUSTOMER_NAME
    assert zayavka["carrier_name"] == CARRIER_NAME


def test_collect_empty_window_does_not_invent_other_fields() -> None:
    """Остальные поля пустое окно не выдумывает: ни ставки НДС, ни даты."""
    zayavka = zayavka_of(collect_havaly_data({}))

    assert "vat_rate" not in zayavka
    assert "price_with_vat" not in zayavka
    assert "date" not in zayavka
    assert "lot_number" not in zayavka
    assert set(zayavka) == {"customer_name", "carrier_name"}


def test_collect_window_without_tabs_attribute() -> None:
    """Окно каркаса без вкладок: атрибутов нет — сборка не падает."""
    data = collect_havaly_data(object())

    assert zayavka_of(data)["customer_name"] == CUSTOMER_NAME
    assert data["vehicles"] == []


def test_collect_none_window() -> None:
    """Вместо окна передан None — пустой результат, без исключения."""
    data = collect_havaly_data(None)

    assert data["vehicles"] == []
    assert set(zayavka_of(data)) == {"customer_name", "carrier_name"}


def test_collect_partial_window() -> None:
    """Частичное окно: есть только «Маршрут» — остальные разделы пусты."""
    data = collect_havaly_data({"route": filled_route_tab()})

    zayavka = zayavka_of(data)
    assert zayavka["loading_city"] == LOADING_CITY
    assert zayavka["unloading_point"] == UNLOADING_POINT
    assert zayavka["loading_plan_date"] == LOADING_PLAN_DATE_DOC
    assert "driver_last_name" not in zayavka
    assert "tractor_brand" not in zayavka
    assert data["vehicles"] == []


def test_collect_tab_without_get_data() -> None:
    """Вкладка без get_data() — раздел пустой, исключения нет."""
    data = collect_havaly_data({"driver": BrokenTab(), "cargo": BrokenTab()})

    assert "driver_last_name" not in zayavka_of(data)
    assert data["vehicles"] == []


def test_collect_tab_get_data_raises() -> None:
    """get_data() упал — сборка продолжается, раздел пустой."""
    data = collect_havaly_data({
        "driver": FakeTab(error=RuntimeError("вкладка сломана")),
        "price": FakeTab(error=ValueError("нет данных")),
    })

    zayavka = zayavka_of(data)
    assert "driver_last_name" not in zayavka
    assert "price_with_vat" not in zayavka


def test_collect_tab_returns_not_a_mapping() -> None:
    """Вкладка вернула не словарь — раздел пустой, исключения нет."""
    data = collect_havaly_data({
        "route": FakeTab(data=["не", "словарь"]),
        "cargo": FakeTab(data="строка"),
    })

    assert "loading_city" not in zayavka_of(data)
    assert data["vehicles"] == []


def test_collect_tabs_not_a_mapping_of_tabs() -> None:
    """Вместо словаря вкладок передано что-то другое — пустой результат."""
    data = collect_havaly_data(["zayavka", "cargo"])

    assert data["vehicles"] == []
    assert "loading_city" not in zayavka_of(data)


def test_collect_window_with_fake_tabs_reads_each_tab_once() -> None:
    """Окно с вкладками-заглушками: данные читаются, вкладка — один раз."""
    driver_tab = FakeTab(data=filled_driver_tab())
    cargo_tab = FakeTab(data=filled_cargo_tab())
    window = FakeWindow(driver=driver_tab, cargo=cargo_tab)

    data = collect_havaly_data(window)

    assert zayavka_of(data)["driver_last_name"] == DRIVER_LAST_NAME
    assert len(data["vehicles"]) == 2
    assert driver_tab.calls == 1
    assert cargo_tab.calls == 1


def test_collect_broken_window_does_not_raise() -> None:
    """Атрибут окна падает при чтении — сборщик не поднимает исключение."""
    data = collect_havaly_data(ExplodingWindow())

    assert data["vehicles"] == []
    assert zayavka_of(data)["carrier_name"] == CARRIER_NAME


def test_collect_window_with_tab_container() -> None:
    """
    Каркасное окно: вкладки лежат в контейнере, атрибутов ``*_tab`` нет.

    Вкладка находится по заголовку из TAB_CONFIGS, а её данные читаются
    тем же get_data(): путь «окно → вкладка → сборщик» закрыт и для каркаса.
    """
    container = FakeTabContainer([
        ("Заявка", FakeTab(data=filled_zayavka_tab())),
        ("Груз", FakeTab(data=filled_cargo_tab())),
        ("Маршрут", FakeTab(data=filled_route_tab())),
        ("Водитель", FakeTab(data=filled_driver_tab())),
        ("ТС", FakeTab(data=filled_vehicle_tab())),
        ("Стоимость", FakeTab(data=filled_price_tab())),
    ])

    data = collect_havaly_data(SkeletonWindow(container))

    assert zayavka_of(data)["lot_number"] == LOT_NUMBER
    assert zayavka_of(data)["driver_last_name"] == DRIVER_LAST_NAME
    assert zayavka_of(data)["tractor_plate"] == TRACTOR_PLATE
    assert zayavka_of(data)["price_with_vat"] == PRICE_WITH_VAT
    assert len(data["vehicles"]) == 2


def test_collect_window_with_incomplete_tab_container() -> None:
    """В контейнере не все вкладки (частичное окно) — сборка не падает."""
    container = FakeTabContainer([("Маршрут", FakeTab(data=filled_route_tab()))])

    data = collect_havaly_data(SkeletonWindow(container))

    assert zayavka_of(data)["loading_city"] == LOADING_CITY
    assert data["vehicles"] == []


def test_collect_attributes_win_over_container() -> None:
    """Вкладки-атрибуты важнее контейнера: их ставит настоящее окно типа."""
    window = FakeWindow(route=FakeTab(data=filled_route_tab()))
    window.tabs = FakeTabContainer([
        ("Маршрут", FakeTab(data={"loading_city": "иная"})),
    ])

    data = collect_havaly_data(window)

    assert zayavka_of(data)["loading_city"] == LOADING_CITY


# ─────────────────────────────────────────────────────────────
# Заполненное окно: ключи JSON и значения
# ─────────────────────────────────────────────────────────────

def test_collect_filled_window_zayavka_values() -> None:
    """Заполненное окно: значения полей ложатся в блок zayavka."""
    zayavka = zayavka_of(collect_havaly_data(filled_tabs()))

    assert zayavka["lot_number"] == LOT_NUMBER
    assert zayavka["loading_city"] == LOADING_CITY
    assert zayavka["loading_point"] == LOADING_POINT
    assert zayavka["unloading_city"] == UNLOADING_CITY
    assert zayavka["unloading_point"] == UNLOADING_POINT
    assert zayavka["tractor_brand"] == TRACTOR_BRAND
    assert zayavka["tractor_color"] == TRACTOR_COLOR
    assert zayavka["tractor_plate"] == TRACTOR_PLATE
    assert zayavka["trailer_brand"] == TRAILER_BRAND
    assert zayavka["trailer_plate"] == TRAILER_PLATE
    assert zayavka["driver_last_name"] == DRIVER_LAST_NAME
    assert zayavka["driver_first_name"] == DRIVER_FIRST_NAME
    assert zayavka["driver_middle_name"] == DRIVER_MIDDLE_NAME
    assert zayavka["driver_license_number"] == DRIVER_LICENSE_NUMBER
    assert zayavka["driver_passport_series"] == DRIVER_PASSPORT_SERIES
    assert zayavka["driver_passport_number"] == DRIVER_PASSPORT_NUMBER
    assert zayavka["driver_passport_issuer"] == DRIVER_PASSPORT_ISSUER
    assert zayavka["driver_citizenship"] == DRIVER_CITIZENSHIP
    assert zayavka["driver_registration"] == DRIVER_REGISTRATION
    assert zayavka["driver_phone"] == DRIVER_PHONE
    assert zayavka["price_with_vat"] == PRICE_WITH_VAT
    assert zayavka["vat_rate"] == VAT_RATE


def test_collect_filled_window_normalizes_dates_and_time() -> None:
    """
    Даты — в формате документа, время — «ЧЧ:ММ».

    Вкладки отдают дату из QDateEdit («2026-10-06») и время из QTimeEdit
    («09:00:00»), а схема промпта и бланк ждут ДД.ММ.ГГГГ и «ЧЧ:ММ».
    """
    zayavka = zayavka_of(collect_havaly_data(filled_tabs()))

    assert zayavka["date"] == DATE_DOC
    assert zayavka["loading_plan_date"] == LOADING_PLAN_DATE_DOC
    assert zayavka["loading_plan_time"] == LOADING_PLAN_TIME
    assert zayavka["driver_license_issue_date"] == DRIVER_LICENSE_ISSUE_DATE_DOC
    assert zayavka["driver_passport_issue_date"] == DRIVER_PASSPORT_ISSUE_DATE_DOC
    assert zayavka["driver_birth_date"] == DRIVER_BIRTH_DATE_DOC


def test_collect_filled_window_keeps_price_as_number() -> None:
    """Ставка с НДС — числом (схема промпта), в том числе из строки документа."""
    tabs = filled_tabs()
    tabs["price"] = {"price_with_vat": PRICE_WITH_VAT_TEXT, "vat_rate": VAT_RATE}

    assert zayavka_of(collect_havaly_data(tabs))["price_with_vat"] == PRICE_WITH_VAT


def test_collect_filled_window_derives_vat_rate_from_number() -> None:
    """Ставка НДС числом превращается в строку «22%» (как в схеме и бланке)."""
    tabs = filled_tabs()
    tabs["price"] = {"price_with_vat": PRICE_WITH_VAT, "vat_rate_num": VAT_RATE_NUM}

    assert zayavka_of(collect_havaly_data(tabs))["vat_rate"] == VAT_RATE


def test_collect_zero_price_is_not_written() -> None:
    """
    Незаполненная ставка (0 или 0.0) поля в ответе не создаёт.

    «Ставка не указана — 0.0» это значение распознавания, а не формы:
    пустое поле ввода не должно попадать в ответ нулём, иначе пользователь
    увидит в бланке 0 там, где не вводил ничего.
    """
    data = collect_havaly_data({"price": {"price_with_vat": 0.0}})

    assert "price_with_vat" not in zayavka_of(data)


def test_collect_whitespace_values_are_empty() -> None:
    """Пробелы и None в полях вкладки — пустые значения, ключей не создают."""
    data = collect_havaly_data({
        "route": {"loading_city": "   ", "unloading_city": None},
        "driver": {"driver_phone": "\t"},
    })

    zayavka = zayavka_of(data)
    assert "loading_city" not in zayavka
    assert "unloading_city" not in zayavka
    assert "driver_phone" not in zayavka


def test_collect_values_are_stripped() -> None:
    """Обрамляющие пробелы снимаются: в бланк уходит чистое значение."""
    data = collect_havaly_data({"route": {"loading_city": "  г. Москва  "}})

    assert zayavka_of(data)["loading_city"] == LOADING_CITY


def test_collect_date_and_lot_from_cargo_tab() -> None:
    """
    Дата и номер лота читаются и из «Груза».

    В бланке номер лота — колонка строки таблицы, а дата заявки повторяется
    в каждой строке: обе вкладки эти поля отдают, и оба пути должны работать.
    """
    data = collect_havaly_data({
        "cargo": {"date": DATE_ISO, "lot_number": LOT_NUMBER, "vehicles": []},
    })

    zayavka = zayavka_of(data)
    assert zayavka["date"] == DATE_DOC
    assert zayavka["lot_number"] == LOT_NUMBER


def test_collect_zayavka_tab_wins_over_cargo_tab() -> None:
    """Заполнено в обеих вкладках — берётся значение из шапки («Заявка»)."""
    data = collect_havaly_data({
        "zayavka": {"date": DATE_ISO, "lot_number": LOT_NUMBER},
        "cargo": {"date": "2026-10-09", "lot_number": "ЛОТ-ИЗ-СТРОКИ"},
    })

    zayavka = zayavka_of(data)
    assert zayavka["date"] == DATE_DOC
    assert zayavka["lot_number"] == LOT_NUMBER


def test_collect_section_wins_over_zayavka_tab() -> None:
    """
    Поле отдали две вкладки — берётся значение вкладки-владельца.

    Поля бланка распределены по вкладкам (см. docstring data.py): если одно
    и то же поле ввели и в шапке, и в своей вкладке, правда — за вкладкой.
    """
    tabs = filled_tabs()
    tabs["zayavka"] = {**filled_zayavka_tab(), "loading_city": "г. Дубна"}

    zayavka = zayavka_of(collect_havaly_data(tabs))
    assert zayavka["loading_city"] == LOADING_CITY


def test_collect_zayavka_tab_field_is_used_when_section_is_silent() -> None:
    """Вкладка-владелец поля не дала — берётся значение из шапки заявки."""
    data = collect_havaly_data({
        "zayavka": {"date": DATE_ISO, "tractor_plate": TRACTOR_PLATE},
    })

    assert zayavka_of(data)["tractor_plate"] == TRACTOR_PLATE


# ─────────────────────────────────────────────────────────────
# Машины: сборка и ограничения
# ─────────────────────────────────────────────────────────────

def test_collect_vehicles_fields_and_order() -> None:
    """Машины: пять полей схемы, порядок строк вкладки сохранён."""
    vehicles = collect_havaly_data(filled_tabs())["vehicles"]

    assert len(vehicles) == 2
    assert list(vehicles[0]) == list(VEHICLE_FIELDS)
    assert vehicles[0] == {
        "vin": VIN_1, "brand": BRAND_1, "model": MODEL_1,
        "dealer": DEALER_1, "dealer_code": DEALER_CODE_1,
    }
    assert vehicles[1]["brand"] == BRAND_2
    assert vehicles[1]["model"] == MODEL_2
    assert vehicles[1]["vin"] == ""


def test_collect_vehicles_without_vin_are_kept() -> None:
    """
    Машина без VIN остаётся в списке.

    «Vin по факту погрузки» — обычное дело в заявках Хавалов: марка и модель
    машину выдают, и промпт требует такую строку вернуть (с пустым vin).
    """
    vehicles = collect_havaly_data(filled_tabs())["vehicles"]

    assert len(vehicles) == 2
    assert vehicles[1]["vin"] == ""
    assert vehicles[1]["dealer_code"] == DEALER_CODE_2


def test_collect_vehicles_skips_empty_rows() -> None:
    """Строка без единого заполненного поля машиной не считается."""
    data = collect_havaly_data({
        "cargo": {"vehicles": [
            {"vin": "", "brand": "", "model": "", "dealer": "", "dealer_code": ""},
            {"vin": VIN_1, "brand": BRAND_1, "model": MODEL_1,
             "dealer": DEALER_1, "dealer_code": DEALER_CODE_1},
            "не словарь",
        ]},
    })

    vehicles = data["vehicles"]
    assert len(vehicles) == 1
    assert vehicles[0]["vin"] == VIN_1


def test_collect_vehicles_trims_to_ten() -> None:
    """Машин больше десяти — лишние отбрасываются: в бланке 10 строк."""
    raw = [
        {"vin": f"XTC651150N000{n:04d}", "brand": BRAND_1, "model": MODEL_1,
         "dealer": DEALER_1, "dealer_code": DEALER_CODE_1}
        for n in range(1, 13)
    ]

    vehicles = collect_havaly_data({"cargo": {"vehicles": raw}})["vehicles"]

    assert len(vehicles) == MAX_VEHICLES == 10
    assert vehicles[-1]["vin"] == "XTC651150N0000010"


def test_collect_vehicles_missing_key() -> None:
    """Во вкладке «Груз» нет ключа vehicles — список машин пустой."""
    data = collect_havaly_data({"cargo": {"lot_number": LOT_NUMBER}})

    assert data["vehicles"] == []
    assert zayavka_of(data)["lot_number"] == LOT_NUMBER


def test_collect_vehicles_as_top_level_section() -> None:
    """
    Машины могут прийти разделом верхнего уровня — списком.

    Так их отдаёт распознавание: ``{"vehicles": [...]}`` само по себе,
    без вкладки «Груз». Сборщик принимает и этот вид.
    """
    data = collect_havaly_data({"vehicles": filled_cargo_tab()["vehicles"]})

    assert len(data["vehicles"]) == 2
    assert data["vehicles"][0]["vin"] == VIN_1


def test_collect_vehicles_as_tab_with_wrapped_key() -> None:
    """Раздел «Груз» без вкладки: ``{"vehicles": {"vehicles": [...]}}``."""
    data = collect_havaly_data(dict(filled_cargo_tab()))

    assert len(data["vehicles"]) == 2


# ─────────────────────────────────────────────────────────────
# Стык с промптом: имена ключей JSON
# ─────────────────────────────────────────────────────────────

def test_zayavka_keys_are_in_prompt_schema() -> None:
    """Ни одного ключа заявки, которого нет в схеме промпта."""
    zayavka = zayavka_of(collect_havaly_data(filled_tabs()))
    unknown = set(zayavka) - set(ZAYAVKA_SCHEMA_ORDER)

    assert not unknown, f"ключей нет в схеме промпта: {sorted(unknown)}"


def test_vehicle_keys_are_in_prompt_schema() -> None:
    """Ни одного ключа машины, которого нет в схеме промпта."""
    vehicles = collect_havaly_data(filled_tabs())["vehicles"]
    unknown = {key for vehicle in vehicles for key in vehicle} - set(VEHICLE_KEYS)

    assert not unknown, f"ключей нет в схеме промпта: {sorted(unknown)}"
    assert list(VEHICLE_FIELDS) == list(VEHICLE_KEYS)


def test_schema_order_matches_prompt() -> None:
    """
    Тридцать полей схемы — ровно те, что перечислены в промпте.

    Имена ключей берутся из генератора (ZAYAVKA_SCHEMA_ORDER): он читает и
    пишет бланк по этой схеме, и его тесты сверяют её с промптом. Здесь
    проверяется, что каждое имя действительно есть в тексте промпта —
    то есть стык «промпт → генератор → сборщик» закрыт именами, а не
    соглашением на словах.
    """
    assert len(ZAYAVKA_SCHEMA_ORDER) == 30
    for key in ZAYAVKA_SCHEMA_ORDER:
        assert f'"{key}"' in PROMPT, f"поля {key!r} нет в промпте"


def test_collect_covers_every_prompt_field() -> None:
    """
    Все поля схемы промпта разложены по вкладкам — ни одно не забыто.

    Собирается заявка, где заполнено КАЖДОЕ поле схемы, и проверяется, что
    в ответе они все (кроме vat_rate и сторон: у них своя логика).
    """
    tabs = {
        "zayavka": {
            key: f"значение-{key}"
            for key in ZAYAVKA_SCHEMA_ORDER
            if key not in ("price_with_vat", "vat_rate")
        },
        "cargo": filled_cargo_tab(),
        "route": {
            key: f"значение-{key}"
            for key in ZAYAVKA_SCHEMA_ORDER
            if key not in ("date", "lot_number", "price_with_vat", "vat_rate")
        },
        "driver": {
            key: f"значение-{key}"
            for key in ZAYAVKA_SCHEMA_ORDER
            if key.startswith("driver_")
        },
        "vehicle": {
            key: f"значение-{key}"
            for key in (
                "tractor_brand", "tractor_color", "tractor_plate",
                "trailer_brand", "trailer_plate",
            )
        },
        "price": {"price_with_vat": PRICE_WITH_VAT, "vat_rate": VAT_RATE},
    }

    zayavka = zayavka_of(collect_havaly_data(tabs))
    missing = [key for key in ZAYAVKA_SCHEMA_ORDER if key not in zayavka]

    assert not missing, f"сборщик не переносит поля промпта: {missing}"


def test_section_titles_match_window_tabs() -> None:
    """Ключи вкладок сборщика — те же шесть вкладок, что в окне типа."""
    assert tuple(SECTION_TITLES.values()) == TAB_TITLES


def test_tab_field_names_match_json_keys() -> None:
    """
    Вкладки отдают поля под именами схемы промпта.

    Это контракт между сборщиком и вкладками (ЭТАП 3.1.E.B.2): имена полей
    вкладки не переводятся, а совпадают с ключами JSON — поэтому раскладка
    не может разойтись с промптом незаметно.
    """
    tabs = filled_tabs()

    for section, fields in tabs.items():
        if section == "cargo":
            continue
        for key in fields:
            assert key in ZAYAVKA_SCHEMA_ORDER, (
                f"поле {key!r} вкладки «{SECTION_TITLES[section]}» "
                f"отсутствует в схеме промпта"
            )

    for vehicle in tabs["cargo"]["vehicles"]:
        for key in vehicle:
            assert key in VEHICLE_KEYS


# ─────────────────────────────────────────────────────────────
# Стык со сборщиком: генератор и валидатор типа
# ─────────────────────────────────────────────────────────────

def template_path() -> str:
    """Путь эталонного бланка Хавалов (templates/shablon_havaly.xlsx)."""
    from core.contracts.paths import TEMPLATES_DIR

    return str(Path(TEMPLATES_DIR) / "shablon_havaly.xlsx")


def test_generator_accepts_collected_data(work_dir) -> None:
    """Генератор принимает результат сборки как есть и заполняет бланк."""
    folder = TempFolder(work_dir, "gen")
    try:
        data = collect_havaly_data(filled_tabs())
        path = ZayavkaExcelGenerator().fill_from_template(
            template_path(), data, str(folder.path)
        )

        assert Path(path).exists(), "генератор не создал файл"
        assert Path(path).parent == folder.path
    finally:
        folder.cleanup()


def test_generator_writes_collected_values(work_dir) -> None:
    """
    Значения сборщика доезжают до готового файла.

    Проверяется содержимое книги: строки ячеек (номер лота, маршрут,
    автовоз, водитель, машины). Даты и ставка пишутся ЧИСЛАМИ с числовым
    форматом, поэтому их проверяет обратное чтение файла
    (test_generator_reads_back_collected_data), а не поиск текста.
    """
    folder = TempFolder(work_dir, "gen_values")
    try:
        data = collect_havaly_data(filled_tabs())
        path = Path(ZayavkaExcelGenerator().fill_from_template(
            template_path(), data, str(folder.path)
        ))
        content = book_text_of(path)

        for expected in (
            LOT_NUMBER, LOADING_CITY, LOADING_POINT,
            UNLOADING_CITY, UNLOADING_POINT, TRACTOR_BRAND, TRACTOR_PLATE,
            TRAILER_BRAND, TRAILER_PLATE, DRIVER_LAST_NAME, DRIVER_FIRST_NAME,
            DRIVER_MIDDLE_NAME, DRIVER_PHONE, VIN_1, BRAND_2, MODEL_2,
        ):
            assert expected in content, f"в файле нет значения: {expected}"
    finally:
        folder.cleanup()


def test_generator_reads_back_collected_data(work_dir) -> None:
    """Обратный ход: генератор читает файл и отдаёт те же данные заявки."""
    folder = TempFolder(work_dir, "gen_read")
    try:
        generator = ZayavkaExcelGenerator()
        path = generator.fill_from_template(
            template_path(), collect_havaly_data(filled_tabs()), str(folder.path)
        )
        read = generator.read_template(path)
    finally:
        folder.cleanup()

    assert read["zayavka"]["lot_number"] == LOT_NUMBER
    assert read["zayavka"]["loading_city"] == LOADING_CITY
    assert read["zayavka"]["driver_last_name"] == DRIVER_LAST_NAME
    assert read["zayavka"]["driver_phone"] == DRIVER_PHONE
    assert read["zayavka"]["date"] == DATE_DOC
    assert read["zayavka"]["price_with_vat"] == PRICE_WITH_VAT
    assert len(read["vehicles"]) == 2
    assert read["vehicles"][0]["vin"] == VIN_1


def test_validator_accepts_collected_data() -> None:
    """Валидатор принимает сборку: данных достаточно, ошибок нет."""
    report = ZayavkaExcelValidator().check(collect_havaly_data(filled_tabs()))

    assert report.errors == [], f"ошибки валидатора: {report.errors}"


def test_validator_sees_collected_zayavka_fields() -> None:
    """
    Валидатор видит поля заявки из сборки.

    Если бы сборщик отдавал ContractData вместо схемы промпта, блок заявки
    потерялся бы при coerce — и валидатор пожаловался бы на пустой маршрут
    и водителя (см. docstring core/contracts/zayavka/validator.py).
    """
    report = ZayavkaExcelValidator().check(collect_havaly_data(filled_tabs()))
    warnings = " | ".join(report.warnings)

    assert "Не заполнено в заявке" not in warnings
    assert "Не заполнены данные водителя" not in warnings
    assert "Не заполнена ставка с НДС" not in warnings
    assert "не заполнена фиксированная сторона" not in warnings


def test_validator_warns_on_empty_collected_data() -> None:
    """Пустая сборка: валидатор замечает, что заполнять нечего (не ошибка)."""
    report = ZayavkaExcelValidator().check(collect_havaly_data({}))

    assert report.errors == []
    assert any("нет ни одной машины" in warning for warning in report.warnings)
    assert any("ставка с НДС" in warning for warning in report.warnings)


def test_validator_sees_vehicles_without_vin() -> None:
    """Машина без VIN доезжает до валидатора как замечание, а не ошибка."""
    report = ZayavkaExcelValidator().check(collect_havaly_data(filled_tabs()))

    assert report.errors == []
    assert any("не заполнен VIN" in warning for warning in report.warnings)


# ─────────────────────────────────────────────────────────────
# Двойная раскладка (§ 5.2) и ContractData
# ─────────────────────────────────────────────────────────────

def test_contract_block_repeats_zayavka_fields() -> None:
    """Поля заявки лежат дважды: в блоке zayavka и в блоке contract."""
    data = collect_havaly_data(filled_tabs())

    for key, value in data["zayavka"].items():
        assert data["contract"]["contract"][key] == value, (
            f"поле {key!r} потерялось в contract"
        )


def test_contract_block_has_contract_data_sections() -> None:
    """В contract есть блоки автовоза, прицепа, водителя и машин."""
    contract = collect_havaly_data(filled_tabs())["contract"]

    assert contract["tractor"] == {
        "brand_model": TRACTOR_BRAND,
        "color": TRACTOR_COLOR,
        "plate_number": TRACTOR_PLATE,
    }
    assert contract["trailer"] == {
        "brand_model": TRAILER_BRAND,
        "plate_number": TRAILER_PLATE,
    }
    assert contract["driver"]["last_name"] == DRIVER_LAST_NAME
    assert contract["driver"]["phone"] == DRIVER_PHONE
    assert contract["driver"]["passport_number"] == DRIVER_PASSPORT_NUMBER
    assert len(contract["vehicles"]) == 2
    assert contract["vehicles"][0]["vin"] == VIN_1


def test_contract_data_survives_coerce() -> None:
    """
    ContractData сохраняет собранные поля.

    Коерция читает только свои блоки (contract, tractor, trailer, driver,
    vehicles): поэтому поля заявки лежат и в блоке zayavka, и внутри
    contract — иначе они потерялись бы (AGENTS.md § 5.2).
    """
    from core.contract_data import ContractData

    data = collect_havaly_data(filled_tabs())
    cd = ContractData.coerce(data["contract"])

    assert cd.contract["loading_city"] == LOADING_CITY
    assert cd.contract["driver_last_name"] == DRIVER_LAST_NAME
    assert cd.contract["lot_number"] == LOT_NUMBER
    assert cd.contract["date"] == DATE_DOC
    assert cd.contract["price_with_vat"] == PRICE_WITH_VAT
    assert cd.contract["vat_rate"] == VAT_RATE
    assert len(cd.vehicles) == 2
    assert cd.vehicles[0]["vin"] == VIN_1
    assert cd.tractor["plate_number"] == TRACTOR_PLATE
    assert cd.trailer["plate_number"] == TRAILER_PLATE
    assert cd.driver["last_name"] == DRIVER_LAST_NAME


def test_contract_data_keeps_fields_after_round_trip() -> None:
    """
    Поля заявки переживают to_generator_dict → coerce.

    Это и есть проверка «поля кладутся дважды»: ContractData отдала данные
    генератору, генератор вернул их обратно — и ничего не потерялось.
    """
    from core.contract_data import ContractData

    data = collect_havaly_data(filled_tabs())
    cd = ContractData.coerce(data["contract"])
    again = ContractData.coerce(cd.to_generator_dict())

    assert again.contract["loading_city"] == LOADING_CITY
    assert again.contract["driver_last_name"] == DRIVER_LAST_NAME
    assert again.contract["tractor_plate"] == TRACTOR_PLATE
    assert len(again.vehicles) == 2


def test_collected_data_is_json_ready() -> None:
    """Данные сборки — простые типы: строки, числа и списки словарей."""
    data = collect_havaly_data(filled_tabs())

    for key, value in data["zayavka"].items():
        assert isinstance(value, (str, int, float)), (
            f"поле {key!r} имеет тип {type(value).__name__}"
        )
    assert isinstance(data["zayavka"]["price_with_vat"], float)
    for vehicle in data["vehicles"]:
        assert all(isinstance(value, str) for value in vehicle.values())


# ─────────────────────────────────────────────────────────────
# Логи без ПДн
# ─────────────────────────────────────────────────────────────

def test_logs_have_no_personal_data(caplog) -> None:
    """В лог не попадают ФИО, номера, VIN, адреса и телефоны."""
    with caplog.at_level(logging.DEBUG):
        collect_havaly_data(filled_tabs())

    logged = "\n".join(log_lines(caplog))
    for marker, value in PII_SAMPLES.items():
        assert value not in logged, f"{marker}: значение попало в лог"
    assert "Иванов" not in logged
    assert "926830" not in logged


def test_logs_are_written(caplog) -> None:
    """Сборка пишет в лог сводку: количества и имена разделов, без значений."""
    with caplog.at_level(logging.INFO):
        collect_havaly_data(filled_tabs())

    summary = [line for line in log_lines(caplog) if "данные формы собраны" in line]
    assert summary, "сборщик не записал сводку в лог"
    assert "машин=2" in summary[0]
    assert "Заявка" in summary[0] and "Стоимость" in summary[0]
    # Ставка в логе — суммой, а не ставкой НДС: перепутать их нельзя.
    assert f"ставка с НДС={PRICE_WITH_VAT}" in summary[0]


def test_logs_on_empty_tabs_have_no_values(caplog) -> None:
    """Пустые и сломанные вкладки: в логе имена разделов, а не данные."""
    with caplog.at_level(logging.DEBUG):
        collect_havaly_data({
            "driver": FakeTab(error=RuntimeError("вкладка сломана")),
            "route": FakeTab(data="строка"),
        })

    logged = "\n".join(log_lines(caplog))
    assert "Водитель" in logged
    assert "Маршрут" in logged
    assert "вкладка сломана" not in logged


def test_module_does_not_require_qt() -> None:
    """
    Модуль сборки не тянет PyQt5 сам.

    Сборщик живёт вне окна и проверяется без интерфейса: PyQt5 появляется
    только при импорте пакета окна (ui/windows/havaly/__init__.py), а сам
    data.py от него не зависит.
    """
    source = io.open(
        Path(__file__).resolve().parent.parent
        / "ui" / "windows" / "havaly" / "data.py",
        encoding="utf-8",
    ).read()

    assert "PyQt5" not in source


# ─────────────────────────────────────────────────────────────
# Даты: формат документа
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("2026-10-06", DATE_DOC),
    ("06.10.2026", DATE_DOC),
    ("06/10/2026", DATE_DOC),
    ("20261006", DATE_DOC),
    ("2026-10-06T00:00:00", DATE_DOC),
])
def test_date_formats_are_normalized(value: str, expected: str) -> None:
    """Дата приводится к ДД.ММ.ГГГГ из любого формата, что отдаёт вкладка."""
    data = collect_havaly_data({"zayavka": {"date": value}})

    assert zayavka_of(data)["date"] == expected


def test_unparsed_date_is_kept_as_is() -> None:
    """Непонятную дату сборщик не выбрасывает: её покажет валидатор."""
    data = collect_havaly_data({"zayavka": {"date": "начало октября"}})

    assert zayavka_of(data)["date"] == "начало октября"


def test_date_fields_cover_generator_dates() -> None:
    """Список дат сборщика совпадает с датами, которые форматирует генератор."""
    assert set(DATE_FIELDS) == set(GENERATOR_DATE_FIELDS)


@pytest.mark.parametrize("value,expected", [
    ("09:00", "09:00"),
    ("09:00:00", "09:00"),
    ("9:00", "09:00"),
    ("9.5", "09:05"),
])
def test_time_formats_are_normalized(value: str, expected: str) -> None:
    """Время приводится к «ЧЧ:ММ» из любого формата, что отдаёт вкладка."""
    data = collect_havaly_data({"route": {"loading_plan_time": value}})

    assert zayavka_of(data)["loading_plan_time"] == expected


def test_strange_time_is_kept_as_is() -> None:
    """Непонятное время остаётся как есть — о нём скажет валидатор."""
    data = collect_havaly_data({"route": {"loading_plan_time": "утром"}})

    assert zayavka_of(data)["loading_plan_time"] == "утром"


# ─────────────────────────────────────────────────────────────
# Ставка НДС
# ─────────────────────────────────────────────────────────────

def test_vat_rate_from_text() -> None:
    """Ставка НДС строкой сохраняется как напечатана."""
    data = collect_havaly_data({"price": {"vat_rate": "Без НДС"}})

    assert zayavka_of(data)["vat_rate"] == "Без НДС"


@pytest.mark.parametrize("number,expected", [
    (22.0, "22%"),
    (22, "22%"),
    (0.0, "0%"),
    ("20", "20%"),
])
def test_vat_rate_from_number(number: Any, expected: str) -> None:
    """Ставка НДС числом превращается в строку, как её ждёт схема промпта."""
    data = collect_havaly_data({"price": {"vat_rate_num": number}})

    assert zayavka_of(data)["vat_rate"] == expected
