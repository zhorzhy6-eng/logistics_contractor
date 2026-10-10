#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка данных заявки «Логистикс Рус» из вкладок окна (ЭТАП 3.1.C.B.1).

Зачем отдельный модуль
----------------------
Вкладок шесть, и каждая знает только свои поля. Здесь они собираются в один
ContractData — тот же объект, что читают генератор
(core/contracts/logistiks_rus/generator.py) и валидатор
(core/contracts/logistiks_rus/validator.py). Сборка живёт вне окна, поэтому её
можно проверить тестом без поднятия интерфейса. Образец раскладки —
ui/windows/formika/data.py.

Раскладка полей (согласована на ЭТАПЕ 3.1.C.B):

    customer_tab  → contract.number, contract.date,
                    contract.loading_date,
                    contract.loading_time_from / _to,
                    customer.full_name, customer.short_name (= full_name)
    cargo_tab     → vehicles[*].brand_model / vin
    route_tab     → contract.route,
                    contract.unloading_date,
                    contract.unloading_time_from / _to,
                    loadings[*] (из shipper_name + loading_addresses),
                    unloadings[*]
    driver_tab    → driver.full_name (в этом бланке печатается только ФИО)
    vehicle_tab   → tractor.brand_model / plate_number,
                    trailer.brand_model / plate_number
    price_tab     → contract.carrier_type («ООО» / «ИП»),
                    contract.price_without_vat,
                    contract.price_with_vat,
                    contract.vat_rate_num, contract.vat_rate,
                    contract.special_conditions

Три маппинга, которые закрывает этот шаг
----------------------------------------
1. ТОЧКИ С НАЗВАНИЯМИ. ContractData хранит точки маршрута как
   {address, date, time_window}: поле name при приведении отбрасывается
   (core.contract_data._as_point_list). А генератор и валидатор берут
   название грузоотправителя/грузополучателя именно из name. Поэтому точки
   кладутся ДВУМЯ способами:

     * contract["loadings"] / contract["unloadings"] — полный набор
       {name, address, date, time_window} (этот путь читают генератор
       и валидатор);
     * ContractData.loadings / .unloadings — приведённые точки
       {address, date, time_window} для верхнеуровневой совместимости
       (name здесь теряется — так и задумано).

2. ГРУЗООТПРАВИТЕЛЬ И ГРУЗОПОЛУЧАТЕЛИ. Раздел 1 заявки — ОДИН
   грузоотправитель и список адресов погрузки: вкладка «Маршрут» отдаёт
   поле shipper_name и массив loading_addresses (строки таблицы). Сборщик
   раскладывает их в contract.loadings: имя из поля повторяется в каждой
   точке, адреса идут по порядку. Грузополучатели приходят массивом
   consignees: [{name, address}, …] и раскладываются в contract.unloadings.
   В обе части добавляются date / time_window из contract.*. Старый массив
   shippers (прежний формат вкладки и распознавания) принимается как
   fallback.

3. СУММЫ. Промпт распознавания (core/prompts/logistiks_rus.py) кладёт суммы
   в contract.sum_wo_vat / sum_vat / sum_total, а генератор читает
   price_without_vat / price_input. В сборке читается форма
   (price_without_vat — «Сумма без НДС»), а при распознавании суммы
   маппятся:

     * ИП  — одна сумма без НДС: price_without_vat = sum_total;
     * ООО — price_without_vat = sum_wo_vat, а если её нет — sum_total.

   Ставка НДС строкой «22%» разбирается в vat_rate_num = 22.0 (и обратно,
   если число пришло без строки). У ИП ставка НДС не применяется:
   vat_rate_num = 0.0.

Две точки входа
---------------

    build(customer_tab, cargo_tab, ...)  — по именам вкладок (окно);
    collect_logistiks_rus_data({"customer": tab, ...}) — по словарю.

Первая — тонкая обёртка над второй: раскладка полей живёт в одном месте,
и оба вызова дают одинаковый ContractData.

Экспедитор в ContractData не заполняется: в этой заявке он фиксирован
шаблоном (ООО «ТЕХНОЛОГИСТИКА» или ИП Хейгетян Е.В.), а вариант бланка
выбирается по contract.carrier_type.

Устойчивость
------------
Сборка не падает никогда: отсутствующая вкладка, вкладка без get_data(),
исключение внутри get_data() или неожиданный тип результата дают пустой
словарь — раздел просто останется пустым. В лог попадают только имена полей,
количества и длины: адреса, ФИО, VIN и названия организаций не пишем.
"""

import logging
from typing import Any, Dict, List, Mapping, Optional

from core.contract_data import ContractData
from core.vat import (
    VAT_FREE,
    compute_vat,
    normalize_vat_rate,
    total_from_base,
    vat_rate_number,
)

logger = logging.getLogger("ui.windows.logistiks_rus.data")

#: Ключи вкладок и человекочитаемые названия — для сообщений в логе.
SECTION_TITLES: Dict[str, str] = {
    "customer": "Заказчик",
    "cargo": "Груз",
    "route": "Маршрут",
    "driver": "Водитель",
    "vehicle": "ТС",
    "price": "Стоимость",
}

#: Тип экспедитора по умолчанию: вариант бланка и расчёта, который выбирает
#: и генератор, и валидатор, если carrier_type не заполнен.
DEFAULT_CARRIER_TYPE = "ООО"

#: Ставка НДС, когда её не удалось ни ввести, ни разобрать («22%»).
DEFAULT_VAT_RATE_NUM = 22.0

#: Ставка ИП: в бланке одна сумма без НДС, ставка не применяется.
ZERO_VAT_RATE = "0%"

#: Сколько машин помещается в таблицу заявки (см. LogistiksRusGenerator).
MAX_CARS = 12

#: Сколько адресов погрузки и блоков грузополучателей в бланке.
MAX_POINTS = 10

#: Заголовок точки маршрута в логе (сами названия в лог не идут).
_POINTS_LOGGED: Dict[str, str] = {
    "loadings": "грузоотправителей",
    "unloadings": "грузополучателей",
}

#: Массив вкладки «Маршрут» → префикс полей плана в contract: дата и время
#: у точек маршрута не вводятся, они общие для всего раздела (loading_date,
#: loading_time_from / _to, unloading_date, unloading_time_from / _to).
_ROUTE_FIELD_PREFIX: Dict[str, str] = {
    "shippers": "loading",
    "consignees": "unloading",
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
            "Логистикс Рус: данные вкладок не словарь (%s) — сборка пропущена",
            type(tabs).__name__,
        )
        return {}

    source = tabs.get(key)
    if source is None:
        logger.debug("Логистикс Рус: вкладки «%s» нет — раздел пропущен", _section_title(key))
        return {}

    if isinstance(source, Mapping):
        return dict(source)

    getter = getattr(source, "get_data", None)
    if getter is None:
        logger.warning(
            "Логистикс Рус: у вкладки «%s» нет get_data() — раздел пропущен",
            _section_title(key),
        )
        return {}

    try:
        data = getter()
    except Exception as e:  # noqa: BLE001 — сборка не должна падать из-за вкладки
        logger.error(
            "Логистикс Рус: вкладка «%s» не отдала данные (%s) — раздел пропущен",
            _section_title(key), type(e).__name__,
        )
        return {}

    if data is None:
        return {}
    if not isinstance(data, Mapping):
        logger.warning(
            "Логистикс Рус: вкладка «%s» вернула %s вместо словаря — раздел пропущен",
            _section_title(key), type(data).__name__,
        )
        return {}

    return dict(data)


def _field(data: Mapping[str, Any], key: str) -> str:
    """Значение поля вкладки строкой без обрамляющих пробелов (None → «»)."""
    value = data.get(key)
    if value is None:
        return ""
    if isinstance(value, bool):
        # bool — подкласс int: «True» в бланке не нужен, это пустое значение.
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


def _filled(value: Any) -> bool:
    """
    Заполнено ли поле вкладки.

    Пустое — None, пустая строка и bool (в форме такого поля нет): значение
    «0» при этом заполнено, ноль — это ставка или сумма, а не пустота.
    """
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


# ─────────────────────────────────────────────────────────────
# Числа, ставки НДС и окна времени
# ─────────────────────────────────────────────────────────────

def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """
    Число из значения любого вида: «221 099,18», «269741.00», 180300, «22%».

    Разделители тысяч и запятая как десятичный разделитель — обычный формат
    документов. Пустое или непонятное значение даёт default (по умолчанию
    None — «числа нет», чтобы не подменять его нулём).
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)

    text = (
        str(value)
        .strip()
        .replace("%", "")
        .replace("\u00a0", "")
        .replace(" ", "")
    )
    if not text:
        return default

    text = text.replace(",", ".")
    if text.count(".") > 1:
        # «1.234.567» — точки как разделители тысяч.
        head, _, tail = text.rpartition(".")
        text = head.replace(".", "") + "." + tail

    try:
        return float(text)
    except ValueError:
        return default


def _valid_rate_num(value: Any) -> Optional[float]:
    """
    Ставка НДС числом, если в поле действительно ставка.

    Значение больше 100 ставкой быть не может (поле вкладки — «Ставка НДС»,
    а не сумма): такое число не разбираем, чтобы не считать по нему НДС —
    вместо него генератор возьмёт ставку по умолчанию или разберёт строку
    vat_rate.
    """
    number = _to_float(value)
    if number is None or number < 0 or number > 100:
        return None
    return number


def _parse_vat_rate(value: Any) -> Optional[float]:
    """Ставка из строки бланка: «22%» → 22.0, «Без НДС» → 0.0, иначе None."""
    if value is None or isinstance(value, bool):
        return None

    text = str(value).strip()
    if not text:
        return None
    if "без ндс" in text.lower():
        return 0.0
    return _valid_rate_num(text)


def _format_vat_rate(number: float) -> str:
    """Ставка строкой, как в бланке: 22.0 → «22%», 0.0 → «0%»."""
    if float(number).is_integer():
        return f"{number:.0f}%"
    return f"{number:g}%"


def _time_window(time_from: Any, time_to: Any) -> str:
    """
    Окно времени строкой: «08:00-20:00».

    Обе границы пусты — пустая строка; заполнена одна — она и попадает
    в окно, без выдуманной второй (как в ui/windows/formika/data.py).
    """
    start = _field({"value": time_from}, "value")
    end = _field({"value": time_to}, "value")
    if start and end:
        return f"{start}-{end}"
    return start or end


# ─────────────────────────────────────────────────────────────
# Сборка по разделам
# ─────────────────────────────────────────────────────────────

def _build_customer(tabs: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Заказчик: номер и дата заявки, наименование и план ПОГРУЗКИ.

    Заказчик этой заявки фиксирован, поэтому вкладка отдаёт одно поле name;
    в ContractData оно кладётся и в full_name, и в short_name (валидатор
    проверяет оба, а бланк печатает полное наименование).

    С шага FIX-3 вкладка «Заказчик» отдаёт ещё и план погрузки —
    loading_date, loading_time_from / _to: дата и время подачи ТС относятся
    к заявке в целом, поэтому переехали с вкладки «Маршрут». Ключи contract
    остались теми же, и генератор с валидатором читают их как раньше —
    поменялся только источник.

    Возвращает две части: поля шапки (contract) и сам заказчик (customer).
    """
    data = _raw_data(tabs, "customer")
    contract: Dict[str, Any] = {}
    customer: Dict[str, Any] = {}

    _set_if_filled(contract, "number", _field(data, "number"))
    _set_if_filled(contract, "date", _field(data, "date"))

    # План погрузки (шаг FIX-3) — с этой же вкладки.
    for field in ("loading_date", "loading_time_from", "loading_time_to"):
        _set_if_filled(contract, field, _field(data, field))

    name = _field(data, "name")
    _set_if_filled(customer, "full_name", name)
    _set_if_filled(customer, "short_name", name)

    return {"contract": contract, "customer": customer}


def _build_cargo(tabs: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """
    Перевозимые машины: марка/модель и VIN.

    Строка без марки и без VIN отбрасывается, лишние машины (сверх 12)
    отсекаются: в таблицу бланка больше не помещается. Автовоз (тягач
    и прицеп) в этот список не попадает — он в разделе 4 бланка.
    """
    data = _raw_data(tabs, "cargo")
    raw_vehicles = data.get("vehicles")
    if not isinstance(raw_vehicles, (list, tuple)):
        return []

    vehicles: List[Dict[str, Any]] = []
    for item in raw_vehicles:
        if not isinstance(item, Mapping):
            continue

        brand = _field(item, "brand_model")
        vin = _field(item, "vin")
        if not brand and not vin:
            continue

        vehicle: Dict[str, Any] = {}
        _set_if_filled(vehicle, "brand_model", brand)
        _set_if_filled(vehicle, "vin", vin)
        vehicles.append(vehicle)

    if len(vehicles) > MAX_CARS:
        logger.warning(
            "Логистикс Рус: машин %s, в бланк помещается %s — лишние отброшены",
            len(vehicles), MAX_CARS,
        )
        vehicles = vehicles[:MAX_CARS]

    return vehicles


def _route_points(data: Mapping[str, Any], source: str) -> List[Dict[str, Any]]:
    """
    Точки маршрута с названиями: consignees → unloadings,
    shippers → loadings (fallback старого формата вкладки).

    Название точки (name) обязательно для бланка: по нему печатается блок
    «Грузополучатель №N: …». Точка без адреса и без названия в список не
    попадает; лишние точки (сверх 10) отсекаются — блоков в бланке ровно
    10. Дата и окно времени берутся из contract.* вкладки «Маршрут»:
    у самих точек маршрута их нет.
    """
    raw_points = data.get(source)
    if not isinstance(raw_points, (list, tuple)):
        return []

    points: List[Dict[str, Any]] = []
    for item in raw_points:
        if not isinstance(item, Mapping):
            continue

        name = _field(item, "name")
        address = _field(item, "address")
        if not name and not address:
            continue

        points.append({"name": name, "address": address})

    if len(points) > MAX_POINTS:
        logger.warning(
            "Логистикс Рус: %s %s, в бланк помещается %s — лишние не выводятся",
            _POINTS_LOGGED.get(source, source), len(points), MAX_POINTS,
        )
        points = points[:MAX_POINTS]

    # Дата и окно времени — общие для всех точек раздела: в бланке они
    # выводятся строкой плана («Дата / время погрузки»), а не по точкам.
    # Префикс полей разный: грузоотправители — loading_*, грузополучатели —
    # unloading_* (см. _ROUTE_FIELD_PREFIX).
    prefix = _ROUTE_FIELD_PREFIX.get(source, source)
    date = _field(data, f"{prefix}_date")
    window = _time_window(
        data.get(f"{prefix}_time_from"), data.get(f"{prefix}_time_to")
    )

    return [
        {
            "name": point["name"],
            "address": point["address"],
            "date": date,
            "time_window": window,
        }
        for point in points
    ]


def _build_route(
    tabs: Mapping[str, Any],
    loadings_plan: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Маршрут: направление, план ВЫГРУЗКИ и точки с названиями.

    Раздел 1 вкладки «Маршрут» — ОДНО поле «Грузоотправитель» (shipper_name)
    и таблица адресов погрузки (loading_addresses, до 10 строк). В бланке
    грузоотправитель тоже один, поэтому имя из поля идёт в КАЖДУЮ точку
    погрузки, а адреса — по порядку строк таблицы: так точки получают
    привычный генератору и валидатору вид [{name, address, date,
    time_window}, …].

    План ПОГРУЗКИ (loading_date, loading_time_from / _to) читается с вкладки
    «Заказчик» (шаг FIX-3) и приходит в contract отдельной частью
    (_build_customer). Здесь остаётся только план выгрузки — unloading_date,
    unloading_time_from / _to, а точкам погрузки дата и окно времени
    приходят из той же части (loadings_plan).

    Старый массив shippers (формат прежних распознаваний и сохранённых
    ответов) принимается как fallback: если нового формата нет, а массив
    пришёл — собираем точки из него, как раньше.

    Возвращает три части: поля шапки (contract), точки погрузки и точки
    выгрузки. Точки без названия и без адреса отбрасываются, порядок
    сохраняется.
    """
    data = _raw_data(tabs, "route")
    contract: Dict[str, Any] = {}

    _set_if_filled(contract, "route", _field(data, "route"))

    for field in (
        "unloading_date",
        "unloading_time_from",
        "unloading_time_to",
    ):
        _set_if_filled(contract, field, _field(data, field))

    shipper_name = _field(data, "shipper_name")
    loading_addresses = data.get("loading_addresses") or []
    if not isinstance(loading_addresses, (list, tuple)):
        loading_addresses = []

    # Дата и окно времени погрузки — с вкладки «Заказчик» (FIX-3); если
    # сборку вызвали без неё, ключи ищутся и на самой вкладке «Маршрут»:
    # так работали до переноса, и старые вызовы не ломаются.
    plan = dict(loadings_plan or {})
    for field in ("loading_date", "loading_time_from", "loading_time_to"):
        if plan.get(field) in (None, ""):
            plan[field] = data.get(field)

    loadings: List[Dict[str, Any]] = []
    for index, addr in enumerate(loading_addresses):
        address = _field({"value": addr}, "value")
        if not address:
            continue
        loadings.append({
            "name": shipper_name,
            "address": address,
            "date": _field(plan, "loading_date"),
            "time_window": _time_window(
                plan.get("loading_time_from"),
                plan.get("loading_time_to"),
            ),
        })

    if len(loadings) > MAX_POINTS:
        logger.warning(
            "Логистикс Рус: адресов погрузки %s, в бланк помещается %s — "
            "лишние не выводятся",
            len(loadings), MAX_POINTS,
        )
        loadings = loadings[:MAX_POINTS]

    # Fallback: если новый формат пуст, а старый массив shippers пришёл
    # (например, из старого распознавания) — принимаем его.
    if not loadings:
        loadings = _route_points(data, "shippers")

    return {
        "contract": contract,
        "loadings": loadings,
        "unloadings": _route_points(data, "consignees"),
    }


def _build_driver(tabs: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Водитель: в этой заявке печатается только ФИО.

    Паспорт, водительское удостоверение и телефон в бланке отсутствуют,
    поэтому и в данные не собираются: пустых ключей не добавляем.
    """
    data = _raw_data(tabs, "driver")
    driver: Dict[str, Any] = {}

    _set_if_filled(driver, "full_name", _field(data, "full_name"))
    return driver


def _build_vehicle(tabs: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Автовоз: тягач и прицеп (раздел 4 бланка).

    Читаются ровно те ключи, что отдаёт VehicleTab.get_data(): марка/модель
    и госномер. Незаполненные поля не записываются — в бланке останется
    пустое место, а о незаполненном автовозе сообщит валидатор.
    """
    data = _raw_data(tabs, "vehicle")

    tractor: Dict[str, Any] = {}
    _set_if_filled(tractor, "brand_model", _field(data, "tractor_brand"))
    _set_if_filled(tractor, "plate_number", _field(data, "tractor_plate"))

    trailer: Dict[str, Any] = {}
    _set_if_filled(trailer, "brand_model", _field(data, "trailer_brand"))
    _set_if_filled(trailer, "plate_number", _field(data, "trailer_plate"))

    return {"tractor": tractor, "trailer": trailer}


def _resolve_price_contract(
    recognized: Mapping[str, Any],
    form_price: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Стоимость: итог, база без НДС, НДС и особые условия.

    Суммы считаются «НДС В ТОМ ЧИСЛЕ» — единым правилом ядра (core/vat.py),
    тем же, что в Аренде, Формике и перевозке: главная величина — ИТОГ
    (`price_with_vat` / `sum_total`), а база без НДС и налог выводятся из
    него. До этого шага главной была БАЗА, и налог считался сверху.

    Источники итога: поле вкладки «Стоимость (итог)» (`price_with_vat`),
    затем распознанная сумма документа `sum_total` («Итого с НДС»), затем —
    если итога нет — база («Стоимость услуг» / поле вкладки), из которой итог
    восстанавливается прежней формулой `база × (1 + ставка/100)`: так
    читаются записи, сохранённые до перехода.

    Ставка НДС: поле вкладки (`vat_rate` строкой, `vat_rate_num` числом),
    затем распознанная ставка; у ИП ставка не применяется — ноль и «0%».
    """
    contract: Dict[str, Any] = {}

    carrier_field = _field(form_price, "carrier_type") or _field(
        form_price, "entity_type"
    )
    carrier_is_ip = "ИП" in carrier_field
    carrier_type = "ИП" if carrier_is_ip else carrier_field
    if not carrier_type:
        carrier_type = DEFAULT_CARRIER_TYPE

    if carrier_is_ip:
        rate_num = 0.0
        rate_text = ZERO_VAT_RATE
    else:
        rate_text = (
            normalize_vat_rate(form_price.get("vat_rate"))
            or normalize_vat_rate(recognized.get("vat_rate"))
        )
        rate_num = _valid_rate_num(form_price.get("vat_rate_num"))
        if rate_num is None:
            rate_num = vat_rate_number(rate_text)
        if not rate_text:
            rate_text = _format_vat_rate(rate_num)

    # ── Итог — главная величина; база нужна только там, где итога нет ──
    total = _first_positive(
        form_price.get("price_with_vat"),
        recognized.get("price_with_vat"),
        recognized.get("sum_total"),
        recognized.get("amount_with_vat"),
    )
    base = _first_positive(
        form_price.get("price_without_vat"),
        recognized.get("amount_without_vat"),
        recognized.get("sum_wo_vat"),
        recognized.get("price_input"),
    )
    if total is None and base is not None:
        total = total_from_base(base, rate_num)

    if total is not None:
        vat = compute_vat(total, VAT_FREE if carrier_is_ip else rate_num)
        # Единые ключи типа: по ним генератор считает суммы, и по ним же
        # заявка ложится в базу (там две колонки — база и итог).
        _set_if_filled(contract, "price_without_vat", vat["sum_wo_nds"])
        _set_if_filled(contract, "price_with_vat", vat["sum_total"])
        _set_if_filled(contract, "vat_amount", vat["sum_nds"])
    _set_if_filled(contract, "price_input", _field(form_price, "price_input"))

    # Тип экспедитора и ставка НДС пишутся только тогда, когда во вкладке
    # вообще есть данные о стоимости: по ним генератор выбирает вариант
    # бланка и считает НДС. Совершенно пустая вкладка не должна оставлять
    # за собой «ООО, 22%» — иначе пустой вход перестал бы быть пустым.
    # Ставка 0% — тоже данные (у ИП, см. выше), поэтому смотрится факт
    # заполнения поля, а не его отличие от ставки по умолчанию.
    has_price_data = any(
        _filled(form_price.get(key)) or _filled(recognized.get(key))
        for key in (
            "carrier_type", "entity_type", "price_without_vat",
            "price_with_vat", "vat_amount", "price_input", "amount",
            "amount_without_vat", "amount_with_vat", "vat_rate",
            "vat_rate_num", "special_conditions", "sum_wo_vat", "sum_vat",
            "sum_total",
        )
    )
    if has_price_data:
        contract["carrier_type"] = carrier_type
        contract["vat_rate_num"] = rate_num
        contract["vat_rate"] = rate_text

    _set_if_filled(contract, "special_conditions", _field(form_price, "special_conditions"))
    return contract


def _first_positive(*values: Any) -> Optional[float]:
    """
    Первое положительное число из значений — источник для сумм.

    Ноль и пустое значение пропускаются: у промпта 0.0 означает «суммы
    в документе не было», и нулём нельзя затирать уже посчитанную сумму.
    """
    for value in values:
        number = _to_float(value)
        if number is not None and number > 0:
            return number
    return None


def _build_price(tabs: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Стоимость (раздел 5): суммы, ставка НДС и особые условия.

    Ключи вкладки переводятся в ключи ContractData.contract; пустые значения
    пропускаются. Единые ключи вкладки (price_with_vat / price_without_vat /
    vat_amount / entity_type) читаются наравне с историческими
    (amount_without_vat / amount): распознанные суммы (sum_wo_vat / sum_vat /
    sum_total) при этом не теряются — их читает _resolve_price_contract.
    """
    data = _raw_data(tabs, "price")

    # Ключи кладутся только у заполненных полей: по НАЛИЧИЮ ключа решается,
    # есть ли во вкладке данные о стоимости (см. _resolve_price_contract),
    # поэтому None в словарь не попадает — пустая вкладка остаётся пустой.
    fields = {
        "carrier_type": "carrier_type",
        "entity_type": "entity_type",
        "price_without_vat": "price_without_vat",
        "price_with_vat": "price_with_vat",
        "vat_amount": "vat_amount",
        "price_input": "amount",
        "vat_rate": "vat_rate",
        "vat_rate_num": "vat_rate_num",
        "special_conditions": "special_conditions",
    }
    form_price: Dict[str, Any] = {
        target: data[source]
        for target, source in fields.items()
        if data.get(source) is not None
    }

    return _resolve_price_contract(data, form_price)


# ─────────────────────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────────────────────

def collect_logistiks_rus_data(tabs: Mapping[str, Any]) -> ContractData:
    """
    Собирает ContractData заявки «Логистикс Рус» из вкладок окна.

    :param tabs: словарь «ключ вкладки → вкладка или dict». Ключи: customer,
        cargo, route, driver, vehicle, price. Лишние ключи игнорируются,
        отсутствующие означают пустой раздел.
    :return: заполненный ContractData.

    Точки маршрута лежат ДВУМЯ способами: в contract["loadings"] /
    contract["unloadings"] — с названиями грузоотправителя и грузополучателя
    (этот путь читают генератор и валидатор), и в ContractData.loadings /
    .unloadings — в приведённом виде {address, date, time_window}.

    Экспедитор не заполняется: он фиксирован шаблоном заявки, а вариант
    бланка выбирается по contract.carrier_type.

    Функция не поднимает исключений: всё, что не удалось прочитать,
    остаётся пустым и попадает в лог.
    """
    customer = _build_customer(tabs)
    # План погрузки приходит с вкладки «Заказчик» (FIX-3): из него берутся
    # и поля contract.loading_*, и дата с окном времени у точек погрузки.
    route = _build_route(tabs, customer["contract"])
    vehicle = _build_vehicle(tabs)

    # Точки с названиями генератор и валидатор читают из contract: при
    # приведении через ContractData поле name у точек верхнего уровня
    # отбрасывается (core.contract_data._as_point_list).
    contract: Dict[str, Any] = {}
    contract.update(customer["contract"])
    contract.update(route["contract"])
    contract.update(_build_price(tabs))
    # Пустые массивы точек остаются в contract: генератор и валидатор читают
    # contract["loadings"] / ["unloadings"] и на отсутствии ключа не должны
    # отличать «точек нет» от «раздел не собирался».
    contract["loadings"] = route["loadings"]
    contract["unloadings"] = route["unloadings"]

    contract_data = ContractData(
        driver=_build_driver(tabs),
        customer=customer["customer"],
        carrier={},
        vehicles=_build_cargo(tabs),
        tractor=vehicle["tractor"],
        trailer=vehicle["trailer"],
        contract=contract,
        loadings=route["loadings"],
        unloadings=route["unloadings"],
        city="",
    )

    listed = ", ".join(
        f"{_POINTS_LOGGED[key]}={len(contract_data.contract.get(key) or [])}"
        for key in ("loadings", "unloadings")
    )
    logger.info(
        "Логистикс Рус: данные формы собраны — машин=%s, %s, водитель=%s, "
        "тягач=%s, прицеп=%s, ставка НДС=%s, вариант=%s; %s",
        len(contract_data.vehicles),
        listed,
        "да" if contract_data.driver.get("full_name") else "нет",
        "да" if contract_data.tractor.get("plate_number") else "нет",
        "да" if contract_data.trailer.get("plate_number") else "нет",
        contract_data.contract.get("vat_rate") or "—",
        contract_data.contract.get("carrier_type") or DEFAULT_CARRIER_TYPE,
        contract_data.summary(),
    )
    return contract_data


def build(
    customer_tab: Any,
    cargo_tab: Any,
    route_tab: Any,
    driver_tab: Any,
    vehicle_tab: Any,
    price_tab: Any,
) -> ContractData:
    """
    Собирает ContractData из шести вкладок окна «Логистикс Рус».

    Точка входа для окна: вкладки передаются по именам и в порядке разделов,
    а не словарём — так вызов читается и его нельзя перепутать местами
    незаметно для теста. Раскладка полей при этом одна: метод собирает
    словарь и вызывает collect_logistiks_rus_data.

    Обращений к интерфейсу здесь нет: у вкладок читается только get_data().
    Вкладка может быть None или не отдавать данные — раздел останется пустым,
    исключение не поднимется (см. _raw_data).

    :param customer_tab: вкладка «Заказчик» (номер, дата, наименование).
    :param cargo_tab: вкладка «Груз» (перевозимые автомобили).
    :param route_tab: вкладка «Маршрут» (направление, точки, план).
    :param driver_tab: вкладка «Водитель» (ФИО).
    :param vehicle_tab: вкладка «ТС» (тягач и прицеп).
    :param price_tab: вкладка «Стоимость» (суммы, НДС, особые условия).
    :return: ContractData.
    """
    return collect_logistiks_rus_data({
        "customer": customer_tab,
        "cargo": cargo_tab,
        "route": route_tab,
        "driver": driver_tab,
        "vehicle": vehicle_tab,
        "price": price_tab,
    })


__all__ = [
    "build",
    "collect_logistiks_rus_data",
    "SECTION_TITLES",
    "DEFAULT_CARRIER_TYPE",
    "DEFAULT_VAT_RATE_NUM",
    "MAX_CARS",
    "MAX_POINTS",
]
