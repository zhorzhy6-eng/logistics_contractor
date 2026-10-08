#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты ядра зеркала данных «Экспедиторство → Формика / Логистикс» (ШАГ FIX-4).

Проверяется core/mirror.py: карты соответствия вкладок, сбор источника,
план переноса, определение конфликтов и точка входа. Qt здесь нужен только
затем, чтобы поднять QApplication перед созданием вкладок, — если Qt
не установлен, весь модуль пропускается.

Источник и цель подменяются заглушками: настоящий MainWindow тянет за собой
базу, настройки и справочники, а ядру зеркала от него нужны ровно семь
методов вкладок. У заглушки-источника эти методы возвращают те же ключи,
что и живая вкладка «Экспедиторства» (см. ui/tabs/*.py).

Все данные синтетические, реальных ПДн нет.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from core.contract_data import ContractData  # noqa: E402
from core.contracts.formika.generator import FormikaGenerator  # noqa: E402
from core.contracts.paths import TEMPLATES_DIR, contract_folder_name  # noqa: E402
from core.contracts.perevozka.generator import PerevozkaGenerator  # noqa: E402
from core.mirror import (  # noqa: E402
    DRIVER_FIELDS,
    SOURCE_TYPE,
    SUPPORTED_TARGETS,
    TAB_ORDER,
    MirrorPlan,
    collect_conflicts,
    collect_source,
    is_empty,
    mirror_from_expedition,
    plan_for_formika,
    plan_for_logistiks,
    source_has_data,
)
from ui.windows.formika.data import collect_formika_data  # noqa: E402

#: Ключи вкладок окна цели — те же, что у _tab_by_key в окне типа.
TAB_KEYS = (
    "customer_tab", "cargo_tab", "route_tab",
    "driver_tab", "vehicle_tab", "price_tab",
)


# ─────────────────────────────────────────────────────────────
# Заглушки источника и цели
# ─────────────────────────────────────────────────────────────

class FakeTab:
    """Вкладка-заглушка: помнит данные и отдаёт их своим методом."""

    def __init__(self, data, method="get_data"):
        self.data = data
        self._method = method

    def get_data(self):
        return self.data

    def get_loadings(self):
        return self.data

    def get_unloadings(self):
        return self.data

    def get_tractor_data(self):
        return self.data

    def get_trailer_data(self):
        return self.data


class FakeExpedition:
    """
    Источник: окно «Экспедиторство» с четырьмя вкладками.

    Методы вкладок называются ровно так, как их зовёт collect_source:
    get_data / get_loadings / get_unloadings / get_tractor_data /
    get_trailer_data.
    """

    CONTRACT_TYPE = SOURCE_TYPE

    def __init__(self, driver=None, vehicles=None, loadings=None,
                 unloadings=None, tractor=None, trailer=None, contract=None):
        self.driver_tab = FakeTab(driver or {})
        self.vehicles_tab = FakeTab(vehicles or [])
        self.contract_tab = self._ContractTab(
            contract or {}, loadings or [], unloadings or []
        )
        self.trailer_tab = self._TrailerTab(tractor or {}, trailer or {})

    class _ContractTab:
        def __init__(self, contract, loadings, unloadings):
            self._contract = contract
            self._loadings = loadings
            self._unloadings = unloadings

        def get_data(self):
            return self._contract

        def get_loadings(self):
            return self._loadings

        def get_unloadings(self):
            return self._unloadings

    class _TrailerTab:
        def __init__(self, tractor, trailer):
            self._tractor = tractor
            self._trailer = trailer

        def get_tractor_data(self):
            return self._tractor

        def get_trailer_data(self):
            return self._trailer


class FakeTarget:
    """
    Цель: окно типа с вкладками по ключам.

    Вкладка — либо словарь (тогда get_data() возвращает его копию), либо
    объект с get_data(). Ключа нет — вкладки нет: раскладка и поиск
    конфликтов должны это пережить.
    """

    def __init__(self, tabs=None):
        self._tabs = dict(tabs or {})

    def _tab_by_key(self, key):
        return self._tabs.get(key)


class DictTab:
    """Вкладка цели, которая просто отдаёт свой словарь."""

    def __init__(self, data):
        self.data = dict(data)

    def get_data(self):
        return dict(self.data)


# ─────────────────────────────────────────────────────────────
# Данные источника
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def driver():
    """Полностью заполненный водитель — ключи вкладки «Экспедиторства»."""
    return {
        "full_name": "Иванов Иван Иванович",
        "birth_date": "1980-01-01",
        "birth_place": "г. Москва",
        "passport_series": "18 22",
        "passport_number": "926830",
        "passport_issue_date": "2023-01-30",
        "passport_issuer": "Отделом УФМС России по г. Москве",
        "passport_code": "500-123",
        "registration_address": "г. Москва, ул. Тестовая, д. 1",
        "license_series": "99 36",
        "license_number": "123456",
        "license_issue_date": "2020-01-01",
        "license_expiry_date": "2030-01-01",
        "license_categories": "B, C, E",
        "phone": "+7 (999) 123-45-67",
    }


@pytest.fixture
def vehicles():
    """Машины — как их отдаёт вкладка «Перевозимые автомобили»."""
    return [
        {
            "vin": "EC3TEUMB0T0002608",
            "brand_model": "JETOUR T2",
            "plate_number": "А123ВС77",
            "year": 2024,
            "color": "Белый",
            "vehicle_type": "Легковой автомобиль",
            "loading_index": 1,
            "unloading_index": 1,
        },
        {
            "vin": "EC3TEUMB0T0002609",
            "brand_model": "JETOUR T3",
            "plate_number": "В456ЕК77",
            "year": 2025,
            "color": "Серый",
            "vehicle_type": "Легковой автомобиль",
            "loading_index": 1,
            "unloading_index": 2,
        },
    ]


@pytest.fixture
def loadings():
    """Погрузки с наименованиями салонов."""
    return [
        {
            "name": "ООО «Салон Север»",
            "address": "183052, г. Мурманск, пр. Кольский, д. 53",
            "date": "2026-09-24",
            "time_window": "09:00-18:00",
        },
        {
            "name": "ООО «Салон Юг»",
            "address": "г. Мурманск, ул. Портовая, д. 7",
            "date": "2026-09-24",
            "time_window": "10:00-12:00",
        },
    ]


@pytest.fixture
def unloadings():
    """Выгрузки с наименованиями салонов."""
    return [
        {
            "name": "ООО «Салон Кавказ»",
            "address": "г. Пятигорск, Бештаугорское шоссе 17",
            "date": "2026-09-27",
            "time_window": "",
        },
        {
            "name": "ООО «Салон Ставрополь»",
            "address": "г. Ставрополь, ул. Доваторцев, д. 40",
            "date": "2026-09-27",
            "time_window": "",
        },
    ]


@pytest.fixture
def tractor():
    """Тягач — ключи вкладки «Тягач и полуприцеп»."""
    return {
        "brand_model": "Foton Auman",
        "plate_number": "O844XY196",
        "vehicle_type": "Седельный тягач",
        "color": "Белый",
        "year": 2023,
    }


@pytest.fixture
def trailer():
    """Полуприцеп."""
    return {
        "brand_model": "YANGMINDA",
        "plate_number": "71ABF18",
        "color": "Серый",
        "year": 2020,
    }


@pytest.fixture
def contract():
    """Условия договора Экспедиторства — со стоимостью и реквизитами."""
    return {
        "number": "23092026-74",
        "date": "2026-09-23",
        "route": "Мурманск - Пятигорск",
        "loadings": [{"address": "183052, г. Мурманск, пр. Кольский, д. 53"}],
        "unloadings": [{"address": "г. Пятигорск, Бештаугорское шоссе 17"}],
        "loading_address": "183052, г. Мурманск, пр. Кольский, д. 53",
        "unloading_address_1": "г. Пятигорск, Бештаугорское шоссе 17",
        "carrier_type": "ООО (с НДС)",
        "vat_rate": "22%",
        "vat_rate_num": 22.0,
        "price_input": 219966.0,
        "price_without_vat": 180300.0,
        "price_with_vat": 219966.0,
        "vat_type": "with_vat",
        "payment_days": 10,
        "special_conditions": "Погрузка только по предварительному звонку",
        "loading_plan_date": "2026-09-24",
        "loading_plan_time_from": "09:00",
        "loading_plan_time_to": "18:00",
        "unloading_plan_date": "2026-09-27",
    }


@pytest.fixture
def expedition(driver, vehicles, loadings, unloadings, tractor, trailer, contract):
    """Полностью заполненный источник."""
    return FakeExpedition(
        driver=driver,
        vehicles=vehicles,
        loadings=loadings,
        unloadings=unloadings,
        tractor=tractor,
        trailer=trailer,
        contract=contract,
    )


@pytest.fixture
def source(expedition):
    """Собранные данные источника (то, что видит ядро зеркала)."""
    return collect_source(expedition)


#: Значения, которые НЕ должны попасть ни в один план.
FORBIDDEN_VALUES = (
    "219966.0", "219966", "180300.0", "180300", "22%", "22.0",
    "10", "ООО (с НДС)",
    "Погрузка только по предварительному звонку",
)

#: Ключи, которые НЕ должны попасть ни в один план.
FORBIDDEN_KEYS = (
    "price_input", "price_without_vat", "price_with_vat",
    "vat_rate", "vat_rate_num", "vat_type", "payment_days",
    "special_conditions", "customer", "carrier", "carrier_type",
)


#: Имена модулей «чужих» подсистем, на которые ядро зеркала не должно
#: опираться: core/mirror.py — доменная логика без Qt и без генераторов.
FOREIGN_MODULES = (
    "core.validator",
    "core.contract_data",
    "core.contracts.factory",
    "core.contracts.registry",
    "core.contracts.formika.generator",
    "core.contracts.logistiks_rus.generator",
    "ui.windows.formika.data",
    "ui.windows.logistiks_rus.data",
)


def _all_plan_values(plan):
    """Все значения плана одним списком (для проверок «не переносится»)."""
    values = []
    for data in plan.tabs.values():
        for value in data.values():
            if isinstance(value, (list, tuple)):
                values.extend(str(item) for item in value)
            else:
                values.append(str(value))
    return values


def _all_plan_keys(plan):
    """Все ключи плана (по всем вкладкам)."""
    keys = []
    for data in plan.tabs.values():
        keys.extend(data.keys())
    return keys


@pytest.fixture(scope="module")
def qt_app():
    """QApplication для тестов, которым нужны настоящие вкладки."""
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ─────────────────────────────────────────────────────────────
# A.8. Константы и сбор источника
# ─────────────────────────────────────────────────────────────

def test_supported_targets_constant():
    """Поддержаны ровно два целевых типа: Формика и Логистикс Рус."""
    assert SUPPORTED_TARGETS == {"formika", "logistiks_rus"}
    assert SOURCE_TYPE == "perevozka"
    assert SOURCE_TYPE not in SUPPORTED_TARGETS
    # Порядок вкладок плана — те ключи, по которым окно отдаёт вкладки.
    assert TAB_ORDER == TAB_KEYS


def test_collect_source_returns_dict(source):
    """Сбор источника отдаёт словарь всех семи разделов."""
    assert isinstance(source, dict)
    assert set(source) == {
        "driver", "vehicles", "loadings", "unloadings",
        "tractor", "trailer", "contract",
    }
    assert isinstance(source["driver"], dict)
    assert isinstance(source["vehicles"], list)
    assert isinstance(source["loadings"], list)
    assert isinstance(source["unloadings"], list)
    assert isinstance(source["tractor"], dict)
    assert isinstance(source["trailer"], dict)
    assert isinstance(source["contract"], dict)


def test_collect_source_survives_broken_window():
    """Битое окно-источник не роняет сбор: разделы просто пустые."""
    class Broken:
        @property
        def driver_tab(self):
            raise RuntimeError("вкладка недоступна")

    source = collect_source(Broken())
    assert isinstance(source, dict)
    assert all(not source[key] for key in source)

    assert all(not value for value in collect_source(None).values())

    class HalfBroken:
        driver_tab = None

        class _Tab:
            def get_data(self):
                raise ValueError("вкладка упала")

        vehicles_tab = _Tab()

    source = collect_source(HalfBroken())
    assert source["driver"] == {}
    assert source["vehicles"] == []


# ─────────────────────────────────────────────────────────────
# A.8. План Формики
# ─────────────────────────────────────────────────────────────

def test_plan_for_formika_has_expected_tabs(source):
    """План Формики заполняет пять вкладок; «Стоимость» не трогает."""
    plan = plan_for_formika(source)

    assert isinstance(plan, MirrorPlan)
    assert set(plan.tabs) == {
        "customer_tab", "cargo_tab", "route_tab", "driver_tab", "vehicle_tab",
    }
    assert "price_tab" not in plan.tabs
    # Вкладки идут в порядке окна, а не в порядке словаря.
    assert list(plan.tabs) == sorted(plan.tabs, key=TAB_ORDER.index)
    assert plan.field_count() > 0


def test_vehicles_mirrored_to_formika(source):
    """Машины уходят в таблицу груза Формики — марка/модель и VIN."""
    plan = plan_for_formika(source)
    vehicles = plan.tabs["cargo_tab"]["vehicles"]

    assert len(vehicles) == 2
    assert vehicles[0] == {
        "brand_model": "JETOUR T2", "vin": "EC3TEUMB0T0002608",
    }
    assert vehicles[1]["vin"] == "EC3TEUMB0T0002609"
    # Лишние поля машины источника в план не попадают.
    assert set(vehicles[0]) == {"brand_model", "vin"}


def test_loadings_mirrored_to_formika(source):
    """У Формики одна точка погрузки и одна выгрузки — берётся первая."""
    plan = plan_for_formika(source)
    route = plan.tabs["route_tab"]

    assert route["loading_address"] == "183052, г. Мурманск, пр. Кольский, д. 53"
    assert route["unloading_address"] == "г. Пятигорск, Бештаугорское шоссе 17"
    assert route["route"] == "Мурманск - Пятигорск"
    # Ни наименование салона, ни дата/время точки в бланк Формики не идут.
    assert "name" not in route
    assert "loading_plan_date" not in route


def test_driver_full_name_mirrored(source):
    """ФИО водителя и весь его блок доходят до плана без изменений."""
    for plan in (plan_for_formika(source), plan_for_logistiks(source)):
        assert plan.tabs["driver_tab"]["full_name"] == "Иванов Иван Иванович"

    formika_driver = plan_for_formika(source).tabs["driver_tab"]
    assert set(formika_driver) == set(DRIVER_FIELDS)
    assert formika_driver["passport_series"] == "18 22"
    assert formika_driver["license_number"] == "123456"
    assert formika_driver["phone"] == "+7 (999) 123-45-67"


def test_tractor_trailer_mirrored(source):
    """Тягач и полуприцеп уходят в план обеих целей."""
    formika = plan_for_formika(source).tabs["vehicle_tab"]
    assert formika == {
        "tractor_brand": "Foton Auman",
        "tractor_plate": "O844XY196",
        "tractor_type": "Седельный тягач",
        "trailer_brand": "YANGMINDA",
        "trailer_plate": "71ABF18",
    }

    logistiks = plan_for_logistiks(source).tabs["vehicle_tab"]
    assert logistiks == {
        "tractor_brand": "Foton Auman",
        "tractor_plate": "O844XY196",
        "trailer_brand": "YANGMINDA",
        "trailer_plate": "71ABF18",
    }


def test_number_and_date_mirrored(source):
    """Номер и дата договора уходят в шапку целевого документа."""
    for plan in (plan_for_formika(source), plan_for_logistiks(source)):
        assert plan.tabs["customer_tab"]["number"] == "23092026-74"
        assert plan.tabs["customer_tab"]["date"] == "2026-09-23"


def test_formika_tractor_type_from_source_key(source):
    """Тип ТС берётся из tractor_type, а не выдумывается."""
    fallback = dict(source)
    fallback["tractor"] = {
        "brand_model": "Foton Auman",
        "plate_number": "O844XY196",
        "tractor_type": "Автопоезд",
    }
    assert plan_for_formika(fallback).tabs["vehicle_tab"]["tractor_type"] == "Автопоезд"

    empty = dict(source)
    empty["tractor"] = {"brand_model": "Foton Auman"}
    assert "tractor_type" not in plan_for_formika(empty).tabs["vehicle_tab"]


# ─────────────────────────────────────────────────────────────
# A.8. План Логистикса
# ─────────────────────────────────────────────────────────────

def test_plan_for_logistiks_has_expected_tabs(source):
    """План Логистикса заполняет пять вкладок; «Стоимость» не трогает."""
    plan = plan_for_logistiks(source)

    assert set(plan.tabs) == {
        "customer_tab", "cargo_tab", "route_tab", "driver_tab", "vehicle_tab",
    }
    assert "price_tab" not in plan.tabs
    assert list(plan.tabs) == sorted(plan.tabs, key=TAB_ORDER.index)


def test_vehicles_mirrored_to_logistiks(source):
    """Машины уходят в таблицу груза Логистикса."""
    plan = plan_for_logistiks(source)
    vehicles = plan.tabs["cargo_tab"]["vehicles"]

    assert [item["vin"] for item in vehicles] == [
        "EC3TEUMB0T0002608", "EC3TEUMB0T0002609",
    ]
    assert vehicles[0]["brand_model"] == "JETOUR T2"


def test_loadings_mirrored_to_logistiks_as_addresses(source):
    """Грузоотправитель один, адреса погрузки — списком строк."""
    plan = plan_for_logistiks(source)
    route = plan.tabs["route_tab"]

    assert route["shipper_name"] == "ООО «Салон Север»"
    assert route["loading_addresses"] == [
        "183052, г. Мурманск, пр. Кольский, д. 53",
        "г. Мурманск, ул. Портовая, д. 7",
    ]
    assert route["route"] == "Мурманск - Пятигорск"


def test_unloadings_mirrored_to_logistiks_with_names(source):
    """Грузополучатели Логистикса — пары «наименование + адрес»."""
    plan = plan_for_logistiks(source)
    consignees = plan.tabs["route_tab"]["consignees"]

    assert consignees == [
        {
            "name": "ООО «Салон Кавказ»",
            "address": "г. Пятигорск, Бештаугорское шоссе 17",
        },
        {
            "name": "ООО «Салон Ставрополь»",
            "address": "г. Ставрополь, ул. Доваторцев, д. 40",
        },
    ]


def test_logistiks_shipper_name_from_first_named_loading(source):
    """Имя грузоотправителя — из первой погрузки, где оно заполнено."""
    unnamed_first = dict(source)
    unnamed_first["loadings"] = [
        {"address": "г. Мурманск, пр. Кольский, д. 53"},
        {"name": "ООО «Салон Юг»", "address": "г. Мурманск, ул. Портовая, д. 7"},
    ]
    plan = plan_for_logistiks(unnamed_first)

    assert plan.tabs["route_tab"]["shipper_name"] == "ООО «Салон Юг»"
    assert plan.tabs["route_tab"]["loading_addresses"] == [
        "г. Мурманск, пр. Кольский, д. 53",
        "г. Мурманск, ул. Портовая, д. 7",
    ]


def test_logistiks_driver_has_only_full_name(source):
    """В бланке Логистикса у водителя печатается только ФИО."""
    assert plan_for_logistiks(source).tabs["driver_tab"] == {
        "full_name": "Иванов Иван Иванович",
    }


# ─────────────────────────────────────────────────────────────
# A.8. Что НЕ переносится
# ─────────────────────────────────────────────────────────────

def test_price_is_not_mirrored(source):
    """Стоимость не переносится ни в одну цель — переносится рейс, не деньги."""
    for plan in (plan_for_formika(source), plan_for_logistiks(source)):
        assert "price_tab" not in plan.tabs
        keys = _all_plan_keys(plan)
        for forbidden in (
            "price_input", "price_without_vat", "price_with_vat",
            "vat_rate", "vat_rate_num", "vat_type", "payment_days",
            "amount", "amount_with_vat", "amount_without_vat",
            "sum_wo_vat", "sum_vat", "sum_total",
        ):
            assert forbidden not in keys, forbidden


def test_customer_carrier_not_mirrored(source):
    """Реквизиты сторон не переносятся: в бланках цели они фиксированы."""
    for plan in (plan_for_formika(source), plan_for_logistiks(source)):
        keys = _all_plan_keys(plan)
        for forbidden in ("customer", "carrier", "customer_name", "carrier_name"):
            assert forbidden not in keys, forbidden


def test_special_conditions_not_mirrored(source):
    """Особые условия — про конкретный договор, а не про рейс."""
    for plan in (plan_for_formika(source), plan_for_logistiks(source)):
        assert "special_conditions" not in _all_plan_keys(plan)
        assert "Погрузка только по предварительному звонку" not in _all_plan_values(plan)


def test_no_other_value_leaks_into_plans(source):
    """Ни одно «запрещённое» значение источника в план не попадает."""
    for plan in (plan_for_formika(source), plan_for_logistiks(source)):
        values = _all_plan_values(plan)
        for forbidden in FORBIDDEN_VALUES:
            assert forbidden not in values, forbidden


def test_core_mirror_does_not_touch_foreign_modules(qt_app):
    """
    Зеркало не подменяет контракты чужих подсистем.

    Тест читает исходник модуля: ядро зеркала не импортирует ни валидатор,
    ни ContractData, ни генераторы, ни сборщики данных окон типов. Список
    ключей плана — свой, и менять чужие модули шагу FIX-4 не разрешено.
    """
    import inspect

    import core.mirror as mirror_module

    code = inspect.getsource(mirror_module)
    for name in FOREIGN_MODULES:
        assert f"import {name}" not in code, name
        assert f"from {name} import" not in code, name


# ─────────────────────────────────────────────────────────────
# A.8. Пустой источник и точка входа
# ─────────────────────────────────────────────────────────────

def test_empty_source_returns_none():
    """Пустой источник — зеркалить нечего: точка входа отдаёт None."""
    empty = FakeExpedition()
    target = FakeTarget({"route_tab": DictTab({})})

    assert source_has_data(collect_source(empty)) is False
    assert mirror_from_expedition(target, "formika", main_window=empty) is None
    assert mirror_from_expedition(
        target, "logistiks_rus", main_window=empty
    ) is None


def test_no_source_returns_none(monkeypatch):
    """Нет окна «Экспедиторство» — переноса нет."""
    monkeypatch.setattr(
        "ui.windows.base_window.find_expedition_window", lambda: None
    )
    assert mirror_from_expedition(FakeTarget(), "formika") is None


def test_unsupported_target_returns_none(expedition):
    """Аренда, Хавалы и само Экспедиторство зеркало не поддерживают."""
    for target_type in ("arenda_ts", "zayavka_excel", "perevozka", ""):
        assert mirror_from_expedition(
            FakeTarget(), target_type, main_window=expedition
        ) is None


def test_source_has_data_reads_vin_and_addresses(source):
    """Признак «в источнике есть данные» — VIN машины или адрес точки."""
    assert source_has_data(source) is True
    assert source_has_data({"vehicles": [{"vin": "X"}]}) is True
    assert source_has_data({"loadings": [{"address": "адрес"}]}) is True
    assert source_has_data({"unloadings": [{"address": "адрес"}]}) is True
    # Одна марка без VIN и точки без адреса признаком не являются.
    assert source_has_data({"vehicles": [{"brand_model": "JETOUR T2"}]}) is False
    assert source_has_data({"loadings": [{"name": "Салон"}]}) is False
    assert source_has_data({}) is False
    assert source_has_data(None) is False


def test_mirror_from_expedition_returns_plan(expedition):
    """Полный путь: источник найден, план построен, конфликтов нет."""
    target = FakeTarget()

    for target_type in ("formika", "logistiks_rus"):
        plan = mirror_from_expedition(target, target_type, main_window=expedition)
        assert isinstance(plan, MirrorPlan)
        assert plan.tabs
        assert plan.conflicts == []


# ─────────────────────────────────────────────────────────────
# A.8. Конфликты
# ─────────────────────────────────────────────────────────────

def test_no_conflicts_on_empty_target(source):
    """Пустая цель — все поля заполняются молча, конфликтов нет."""
    empty = {
        key: DictTab({}) for key in TAB_KEYS
    }
    target = FakeTarget(empty)

    for target_type, plan in (
        ("formika", plan_for_formika(source)),
        ("logistiks_rus", plan_for_logistiks(source)),
    ):
        assert collect_conflicts(target, plan, target_type=target_type) == []


def test_conflicts_detected_when_target_filled(source):
    """Заполненное и отличающееся поле цели — конфликт с парой «было/станет»."""
    target = FakeTarget({
        "driver_tab": DictTab({"full_name": "Петров Пётр Петрович"}),
        "customer_tab": DictTab({
            "number": "ФМ-2026-1",
            "date": "2026-09-23",   # совпадает с источником — не конфликт
        }),
        "route_tab": DictTab({
            "route": "Мурманск - Пятигорск",   # совпадает — не конфликт
            "loading_address": "другой адрес погрузки",
        }),
    })

    conflicts = collect_conflicts(
        target, plan_for_formika(source), target_type="formika"
    )
    found = {(tab, field) for tab, field, _old, _new in conflicts}

    assert ("driver_tab", "full_name") in found
    assert ("customer_tab", "number") in found
    assert ("route_tab", "loading_address") in found
    assert ("customer_tab", "date") not in found
    assert ("route_tab", "route") not in found

    driver_conflict = next(
        item for item in conflicts if item[1] == "full_name"
    )
    assert driver_conflict == (
        "driver_tab", "full_name", "Петров Пётр Петрович", "Иванов Иван Иванович",
    )


def test_conflict_only_when_target_value_differs(source):
    """Совпадающие значения конфликтом не считаются."""
    same = FakeTarget({
        "driver_tab": DictTab({"full_name": "Иванов Иван Иванович"}),
        "vehicle_tab": DictTab({"tractor_plate": "O844XY196"}),
        "cargo_tab": DictTab({"vehicles": [
            {"brand_model": "JETOUR T2", "vin": "EC3TEUMB0T0002608"},
            {"brand_model": "JETOUR T3", "vin": "EC3TEUMB0T0002609"},
        ]}),
    })

    assert collect_conflicts(
        same, plan_for_formika(source), target_type="formika"
    ) == []


def test_vehicles_list_conflict_is_single_entry(source):
    """Список машин сравнивается целиком: одна строка — один конфликт."""
    target = FakeTarget({
        "cargo_tab": DictTab({"vehicles": [
            {"brand_model": "ДРУГАЯ МОДЕЛЬ", "vin": "X0000000000000001"},
        ]}),
    })

    conflicts = collect_conflicts(
        target, plan_for_formika(source), target_type="formika"
    )

    assert len(conflicts) == 1
    tab, field_name, old, new = conflicts[0]
    assert (tab, field_name) == ("cargo_tab", "vehicles")
    assert old == [{"brand_model": "ДРУГАЯ МОДЕЛЬ", "vin": "X0000000000000001"}]
    assert len(new) == 2


def test_consignees_compared_by_name_and_address(source):
    """Грузополучатели сравниваются парой «наименование + адрес»."""
    target = FakeTarget({
        "route_tab": DictTab({
            "consignees": [
                {"name": "ООО «Салон Кавказ»",
                 "address": "г. Пятигорск, Бештаугорское шоссе 17"},
                {"name": "ООО «Салон Ставрополь»", "address": "другой адрес"},
            ],
        }),
    })

    conflicts = collect_conflicts(
        target, plan_for_logistiks(source), target_type="logistiks_rus"
    )
    assert [item[1] for item in conflicts] == ["consignees"]


def test_loading_addresses_compared_as_plain_list(source):
    """Адреса погрузки Логистикса — список строк, сравнивается как есть."""
    matching = FakeTarget({
        "route_tab": DictTab({
            "loading_addresses": [
                "183052, г. Мурманск, пр. Кольский, д. 53",
                "г. Мурманск, ул. Портовая, д. 7",
            ],
        }),
    })
    plan = plan_for_logistiks(source)
    assert collect_conflicts(
        matching, plan, target_type="logistiks_rus"
    ) == []

    different = FakeTarget({
        "route_tab": DictTab({"loading_addresses": ["один адрес"]}),
    })
    conflicts = collect_conflicts(
        different, plan, target_type="logistiks_rus"
    )
    assert [item[1] for item in conflicts] == ["loading_addresses"]


def test_empty_target_list_is_not_a_conflict(source):
    """Пустой список в цели — не конфликт: заполнить его можно молча."""
    target = FakeTarget({
        "cargo_tab": DictTab({"vehicles": []}),
        "route_tab": DictTab({"consignees": [], "loading_addresses": []}),
    })

    for target_type, plan in (
        ("formika", plan_for_formika(source)),
        ("logistiks_rus", plan_for_logistiks(source)),
    ):
        assert collect_conflicts(target, plan, target_type=target_type) == []


def test_unknown_target_type_has_no_profiles(source):
    """У неподдержанного типа конфликты не считаются — это не его работа."""
    target = FakeTarget({"driver_tab": DictTab({"full_name": "Петров"})})
    assert collect_conflicts(
        target, plan_for_formika(source), target_type="arenda_ts"
    ) == []


def test_broken_target_tab_is_not_a_conflict(source):
    """Вкладка цели, которая падает, конфликтов не создаёт."""
    class BrokenTab:
        def get_data(self):
            raise RuntimeError("вкладка недоступна")

    target = FakeTarget({"driver_tab": BrokenTab()})
    assert collect_conflicts(
        target, plan_for_formika(source), target_type="formika"
    ) == []


def test_is_empty_rules():
    """Пустота: None, пустая строка, пустой список и bool — пусто; 0 — нет."""
    assert is_empty(None) is True
    assert is_empty("") is True
    assert is_empty("   ") is True
    assert is_empty([]) is True
    assert is_empty({}) is True
    assert is_empty(True) is True
    assert is_empty(False) is True

    assert is_empty(0) is False
    assert is_empty(0.0) is False
    assert is_empty("0") is False
    assert is_empty("адрес") is False
    assert is_empty(["адрес"]) is False


def test_plan_field_count(source):
    """field_count — это число полей плана, а не вкладок."""
    plan = plan_for_formika(source)
    assert plan.field_count() == sum(len(data) for data in plan.tabs.values())
    assert plan.field_count() == (
        len(plan.tabs["customer_tab"])
        + len(plan.tabs["cargo_tab"])
        + len(plan.tabs["route_tab"])
        + len(plan.tabs["driver_tab"])
        + len(plan.tabs["vehicle_tab"])
    )


# ─────────────────────────────────────────────────────────────
# Папка рейса: зеркало и договор ложатся вместе (ШАГ «Папка на рейс»)
# ─────────────────────────────────────────────────────────────

#: Имя папки рейса источника: водитель + маршрут + дата договора.
SOURCE_FOLDER = "Иванов_И.И._Мурманск-Пятигорск_23.09.2026"


def test_mirrored_data_keeps_folder_key(source):
    """
    План зеркала несёт те же водителя, маршрут и дату, что источник.

    Проверяются ДАННЫЕ, а не окна: имя папки рейса считается из одного и
    того же ключа (core/contracts/paths.py), поэтому совпадение ключа и есть
    «документы одного рейса лежат рядом» — безо всякой связи между окнами.
    """
    assert contract_folder_name(source) == SOURCE_FOLDER

    for plan in (plan_for_formika(source), plan_for_logistiks(source)):
        mirrored = {
            "driver": {"full_name": plan.tabs["driver_tab"]["full_name"]},
            "contract": {
                "route": plan.tabs["route_tab"]["route"],
                "date": plan.tabs["customer_tab"]["date"],
            },
        }
        assert contract_folder_name(mirrored) == SOURCE_FOLDER
        # Маршрут и дата переносятся как есть, а не пересчитываются.
        assert mirrored["contract"]["route"] == source["contract"]["route"]
        assert mirrored["contract"]["date"] == source["contract"]["date"]


def test_mirror_then_generate_puts_both_in_one_folder(source, work_dir):
    """
    Договор Экспедиторства и зеркалённая заявка Формики — в ОДНОЙ папке.

    Сценарий A из ТЗ: сначала создали договор (папка рейса появилась), потом
    зеркалили данные в Формику и создали заявку — она обязана лечь в ту же
    папку, а не рядом и не в папку с суффиксом «_2».
    """
    output_dir = work_dir / "mirror_folder"
    output_dir.mkdir(parents=True, exist_ok=True)

    expeditor = PerevozkaGenerator(templates_dir=str(TEMPLATES_DIR))
    contract_path = Path(expeditor.generate(
        ContractData.coerce({
            "driver": source["driver"],
            "contract": source["contract"],
            "vehicles": source["vehicles"],
            "tractor": source["tractor"],
            "trailer": source["trailer"],
            "loadings": source["loadings"],
            "unloadings": source["unloadings"],
        }),
        output_dir=str(output_dir),
    ))

    # Данные Формики — ровно то, что раскладывает по вкладкам зеркало.
    plan = plan_for_formika(source)
    formika_data = collect_formika_data({
        "customer": plan.tabs["customer_tab"],
        "cargo": plan.tabs["cargo_tab"],
        "route": plan.tabs["route_tab"],
        "driver": plan.tabs["driver_tab"],
        "vehicle": plan.tabs["vehicle_tab"],
    })
    formika_path = Path(FormikaGenerator(
        templates_dir=str(TEMPLATES_DIR)
    ).generate(formika_data, output_dir=str(output_dir)))

    try:
        assert contract_path.parent.name == SOURCE_FOLDER
        assert contract_path.parent == formika_path.parent
        assert sorted(item.name for item in contract_path.parent.iterdir()) == \
            sorted([contract_path.name, formika_path.name])
    finally:
        shutil.rmtree(contract_path.parent, ignore_errors=True)


def test_mirrored_folder_names_differ_when_driver_differs(source, work_dir):
    """Другой водитель в источнике — другая папка: рейсы не смешиваются."""
    other = dict(source)
    other["driver"] = dict(source["driver"], full_name="Петров Пётр Петрович")

    assert contract_folder_name(other) != contract_folder_name(source)
    assert contract_folder_name(other) == "Петров_П.П._Мурманск-Пятигорск_23.09.2026"
