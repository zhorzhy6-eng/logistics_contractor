#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка данных договора-заявки «Формика» из вкладок окна (ЭТАП 3.1.B.1).

Зачем отдельный модуль
----------------------
Вкладок шесть, и каждая знает только свои поля. Здесь они собираются в один
ContractData — тот же объект, что читают генератор (core/contracts/formika/
generator.py) и валидатор (core/contracts/formika/validator.py). Сборка живёт
вне окна, поэтому её можно проверить тестом без поднятия интерфейса.

Раскладка полей (согласована на ЭТАПЕ 3.1.B):

    customer_tab  → contract.number, contract.date
    cargo_tab     → vehicles[*].brand_model / vin
    route_tab     → contract.route, contract.loading_plan_date,
                    contract.loading_plan_time_from / _to,
                    loadings[0], unloadings[0]
    driver_tab    → driver[*]
    vehicle_tab   → tractor[*], trailer[*]
    price_tab     → contract.price_input / price_without_vat /
                    price_with_vat / vat_rate / vat_rate_num /
                    payment_days / special_conditions

Стороны договора — заказчик и экспедитор — в бланке Формики фиксированы
(ООО «Формика» и ООО «ТЕХНОЛОГИСТИКА»), поэтому customer и carrier остаются
пустыми: значения не выдумываются.

Город в ContractData тоже не заполняется: ContractData.resolved_city()
сам возьмёт город первой погрузки, а если его нет — «Москва».

Устойчивость
------------
Сборка не падает никогда: отсутствующая вкладка, вкладка без get_data()
исключение внутри get_data() или неожиданный тип результата дают пустой
словарь — форма просто останется без этих данных. В лог попадают только
имя раздела и тип ошибки: адреса, ФИО и VIN в лог не пишем.
"""

import logging
from typing import Any, Dict, List, Mapping

from core.contract_data import ContractData

logger = logging.getLogger("ui.windows.formika.data")

#: Ключи вкладок и человекочитаемые названия — для сообщений в логе.
SECTION_TITLES: Dict[str, str] = {
    "customer": "Заказчик",
    "cargo": "Груз",
    "route": "Маршрут",
    "driver": "Водитель",
    "vehicle": "ТС",
    "price": "Стоимость",
}

#: Тип машины по умолчанию: в бланке Формики таблица груза — это
#: перевозимые автомобили (тягач и полуприцеп идут отдельными блоками).
DEFAULT_VEHICLE_TYPE = "Легковой автомобиль"

#: Сколько машин помещается в таблицу груза бланка (см. FormikaGenerator).
MAX_CARS = 12

#: Поля водителя, которые кладутся в ContractData.driver. Ключи — как в
#: ContractData и в шаблоне Формики; отсутствующие поля становятся "".
DRIVER_FIELDS = (
    "full_name",
    "birth_date",
    "birth_place",
    "passport_series",
    "passport_number",
    "passport_issue_date",
    "passport_issuer",
    "passport_code",
    "registration_address",
    "license_series",
    "license_number",
    "license_issue_date",
    "license_expiry_date",
    "license_categories",
    "phone",
)

#: Поля стоимости: ключ вкладки → ключ ContractData.contract.
PRICE_FIELDS = (
    "amount",
    "amount_without_vat",
    "amount_with_vat",
    "vat_rate",
    "vat_rate_num",
    "payment_days",
    "special_conditions",
)

PRICE_KEYS: Dict[str, str] = {
    "amount": "price_input",
    "amount_without_vat": "price_without_vat",
    "amount_with_vat": "price_with_vat",
    "vat_rate": "vat_rate",
    "vat_rate_num": "vat_rate_num",
    "payment_days": "payment_days",
    "special_conditions": "special_conditions",
}


# ─────────────────────────────────────────────────────────────
# Чтение данных вкладки
# ─────────────────────────────────────────────────────────────

def _section_title(key: str) -> str:
    """Название раздела для лога (без самих данных)."""
    return SECTION_TITLES.get(key, str(key))


def _raw_data(tabs: Mapping[str, Any], key: str) -> Dict[str, Any]:
    """
    Данные одной вкладки: get_data() у вкладки-объекта или готовый dict.

    Ничего не поднимает наверх: вкладки может не быть, у неё может не быть
    get_data(), а сам get_data() может упасть — во всех случаях получаем
    пустой словарь и продолжаем сборку.
    """
    if not isinstance(tabs, Mapping):
        logger.warning(
            "Формика: данные вкладок не словарь (%s) — сборка пропущена",
            type(tabs).__name__,
        )
        return {}

    source = tabs.get(key)
    if source is None:
        logger.debug("Формика: вкладки «%s» нет — раздел пропущен", _section_title(key))
        return {}

    if isinstance(source, Mapping):
        return dict(source)

    getter = getattr(source, "get_data", None)
    if getter is None:
        logger.warning(
            "Формика: у вкладки «%s» нет get_data() — раздел пропущен",
            _section_title(key),
        )
        return {}

    try:
        data = getter()
    except Exception as e:  # noqa: BLE001 — сборка не должна падать из-за вкладки
        logger.error(
            "Формика: вкладка «%s» не отдала данные (%s) — раздел пропущен",
            _section_title(key), type(e).__name__,
        )
        return {}

    if data is None:
        return {}
    if not isinstance(data, Mapping):
        logger.warning(
            "Формика: вкладка «%s» вернула %s вместо словаря — раздел пропущен",
            _section_title(key), type(data).__name__,
        )
        return {}

    return dict(data)


def _text(value: Any) -> str:
    """Строка без обрамляющих пробелов (None → "")."""
    if value is None:
        return ""
    return str(value).strip()


def _set_if_filled(target: Dict[str, Any], key: str, value: Any) -> None:
    """
    Кладёт значение в словарь, если оно непустое.

    Пустая строка не записывается: у вкладки-заглушки незаполненное поле
    остаётся None, и в ContractData не должно появиться ни None, ни мусора.
    Числа (0 — это значение, а не пустота) проходят как есть.
    """
    if value is None:
        return
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return
    target[key] = value


# ─────────────────────────────────────────────────────────────
# Сборка по разделам
# ─────────────────────────────────────────────────────────────

def _build_customer(tabs: Mapping[str, Any]) -> Dict[str, Any]:
    """Номер и дата договора-заявки (шапка бланка)."""
    data = _raw_data(tabs, "customer")
    contract: Dict[str, Any] = {}

    _set_if_filled(contract, "number", _text(data.get("number")))
    _set_if_filled(contract, "date", _text(data.get("date")))
    return contract


def _build_cargo(tabs: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """
    Перевозимые машины: марка/модель и VIN.

    Строка без марки и без VIN отбрасывается, лишние машины (сверх 12)
    отсекаются: в таблицу бланка больше не помещается.
    """
    data = _raw_data(tabs, "cargo")
    raw_vehicles = data.get("vehicles")
    if not isinstance(raw_vehicles, (list, tuple)):
        return []

    vehicles: List[Dict[str, Any]] = []
    for item in raw_vehicles:
        if not isinstance(item, Mapping):
            continue

        brand = _text(item.get("brand_model"))
        vin = _text(item.get("vin"))
        if not brand and not vin:
            continue

        vehicle: Dict[str, Any] = {"vehicle_type": DEFAULT_VEHICLE_TYPE}
        _set_if_filled(vehicle, "brand_model", brand)
        _set_if_filled(vehicle, "vin", vin)
        vehicles.append(vehicle)

    if len(vehicles) > MAX_CARS:
        logger.warning(
            "Формика: машин %s, в бланк помещается %s — лишние отброшены",
            len(vehicles), MAX_CARS,
        )
        vehicles = vehicles[:MAX_CARS]

    return vehicles


def _build_route(tabs: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Маршрут: направление, адреса и плановые дата/время погрузки.

    Возвращает три части: поля шапки (contract), точки погрузки (loadings)
    и точки выгрузки (unloadings). Точки лежат отдельно от contract — так
    же, как их ждёт генератор Формики и валидатор.

    Пункт погрузки без адреса в loadings не попадает (иначе валидатор
    счёл бы его заполненным), выгрузка — так же.
    """
    data = _raw_data(tabs, "route")
    contract: Dict[str, Any] = {}
    loadings: List[Dict[str, str]] = []
    unloadings: List[Dict[str, str]] = []

    _set_if_filled(contract, "route", _text(data.get("route")))
    _set_if_filled(contract, "loading_plan_date", _text(data.get("loading_plan_date")))
    _set_if_filled(
        contract, "loading_plan_time_from", _text(data.get("loading_plan_time_from"))
    )
    _set_if_filled(
        contract, "loading_plan_time_to", _text(data.get("loading_plan_time_to"))
    )

    loading_address = _text(data.get("loading_address"))
    if loading_address:
        loadings.append({
            "address": loading_address,
            "date": _text(data.get("loading_plan_date")),
            "time_window": _time_window(
                data.get("loading_plan_time_from"), data.get("loading_plan_time_to")
            ),
        })

    unloading_address = _text(data.get("unloading_address"))
    if unloading_address:
        unloadings.append({"address": unloading_address})

    return {"contract": contract, "loadings": loadings, "unloadings": unloadings}


def _time_window(time_from: Any, time_to: Any) -> str:
    """
    Окно времени погрузки строкой: «09:00-18:00».

    Обе границы пусты — пустая строка (генератор сам решит, что печатать);
    заполнена одна — она и попадает в окно, без выдуманной второй.
    """
    start = _text(time_from)
    end = _text(time_to)
    if start and end:
        return f"{start}-{end}"
    return start or end


def _build_driver(tabs: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Все поля водителя; отсутствующие — пустые строки.

    Пустые строки, а не пропуск ключа: шаблон Формики печатает поля блока
    исполнителя напрямую, и «нет ключа» и «пусто» для него одно и то же.
    Если вкладки водителя нет вовсе — раздел остаётся пустым словарём.
    """
    data = _raw_data(tabs, "driver")
    if not data:
        return {}
    return {field: _text(data.get(field)) for field in DRIVER_FIELDS}


def _build_vehicle(tabs: Mapping[str, Any]) -> Dict[str, Any]:
    """Тягач и полуприцеп: поля ContractData.tractor / .trailer."""
    data = _raw_data(tabs, "vehicle")

    tractor: Dict[str, Any] = {}
    _set_if_filled(tractor, "brand_model", _text(data.get("tractor_brand")))
    _set_if_filled(tractor, "plate_number", _text(data.get("tractor_plate")))
    _set_if_filled(tractor, "vehicle_type", _text(data.get("tractor_type")))
    _set_if_filled(tractor, "color", _text(data.get("tractor_color")))
    _set_if_filled(tractor, "year", _text(data.get("tractor_year")))

    trailer: Dict[str, Any] = {}
    _set_if_filled(trailer, "brand_model", _text(data.get("trailer_brand")))
    _set_if_filled(trailer, "plate_number", _text(data.get("trailer_plate")))
    _set_if_filled(trailer, "color", _text(data.get("trailer_color")))
    _set_if_filled(trailer, "year", _text(data.get("trailer_year")))

    return {"tractor": tractor, "trailer": trailer}


def _build_price(tabs: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Стоимость: суммы, ставка НДС, срок оплаты и особые условия.

    Ключи вкладки переводятся в ключи ContractData.contract; пустые
    значения пропускаются (генератор считает сумму сам).
    """
    data = _raw_data(tabs, "price")
    contract: Dict[str, Any] = {}

    for field in PRICE_FIELDS:
        _set_if_filled(contract, PRICE_KEYS[field], data.get(field))
    return contract


# ─────────────────────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────────────────────

def collect_formika_data(tabs: Mapping[str, Any]) -> ContractData:
    """
    Собирает ContractData договора-заявки «Формика» из вкладок окна.

    :param tabs: словарь «ключ вкладки → вкладка или dict». Ключи: customer,
        cargo, route, driver, vehicle, price. Лишние ключи игнорируются,
        отсутствующие означают пустой раздел.
    :return: заполненный ContractData (customer и carrier пустые: стороны
        в бланке Формики фиксированы).

    Функция не поднимает исключений: всё, что не удалось прочитать,
    остаётся пустым и попадает в лог.
    """
    customer = _build_customer(tabs)
    route = _build_route(tabs)
    vehicle = _build_vehicle(tabs)

    contract: Dict[str, Any] = {}
    contract.update(route["contract"])
    contract.update(_build_price(tabs))
    contract.update(customer)

    contract_data = ContractData(
        driver=_build_driver(tabs),
        customer={},
        carrier={},
        vehicles=_build_cargo(tabs),
        tractor=vehicle["tractor"],
        trailer=vehicle["trailer"],
        contract=contract,
        loadings=route["loadings"],
        unloadings=route["unloadings"],
        city="",
    )

    logger.info("Формика: данные формы собраны — %s", contract_data.summary())
    return contract_data


__all__ = [
    "collect_formika_data",
    "SECTION_TITLES",
    "DRIVER_FIELDS",
    "PRICE_FIELDS",
    "PRICE_KEYS",
    "DEFAULT_VEHICLE_TYPE",
    "MAX_CARS",
]
