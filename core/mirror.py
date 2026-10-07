#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Зеркало данных: Экспедиторство → Формика / Логистикс Рус (ШАГ FIX-4).

Зачем это нужно
---------------
Оператор заполняет один и тот же рейс дважды: сначала в «Экспедиторстве»
(договор с перевозчиком), потом в «Формике» (приложение к генеральному
договору) или в «Логистикс Рус» (заявка генподрядчику). Списки машин (VIN),
адреса погрузок и выгрузок, водитель и автовоз в этих документах совпадают,
и вводить их второй раз — потеря времени.

Этот модуль — «ядро» зеркала: без Qt, без диалогов, без записи в интерфейс.
Он умеет три вещи:

  1. **собрать источник** — данные вкладок MainWindow (Экспедиторство)
     в словарь по разделам (collect_source);
  2. **построить план переноса** — что и куда кладём, по картам соответствия
     (plan_for_formika / plan_for_logistiks);
  3. **найти конфликты** — что в целевом окне уже заполнено и отличается
     (collect_conflicts).

Нажатие кнопки, диалог конфликтов и раскладка по вкладкам живут в
ui/windows/base_window.py: ядро не знает ни про кнопки, ни про QMessageBox.

Что НЕ переносится (осознанно)
------------------------------
  * **стоимость** — price_input / price_without_vat / price_with_vat /
    vat_rate / vat_rate_num / payment_days. У каждого типа свой порядок
    расчёта и своя ставка НДС: у Формики сумма считается от итога, у
    Логистикса вариант бланка зависит от типа экспедитора. Перенесённая
    сумма «как было» разошлась бы с бланком молча;
  * **реквизиты сторон** — customer / carrier. В Формике и Логистиксе
    стороны ФИКСИРОВАНЫ бланком (ООО «Формика», ООО «ТЕХНОЛОГИСТИКА»,
    ООО «ДжейСиСиТиЭс Интернэшнл Логистикс Рус»): переносить туда
    заказчика экспедиторского договора значило бы напечатать в документе
    чужую сторону;
  * **особые условия** — special_conditions: они относятся к конкретному
    договору, а не к рейсу.

Обратное зеркало (Формика / Логистикс → Экспедиторство) в этом шаге НЕ
делается: источник пока только Экспедиторство (SOURCE_TYPE).

Границы модуля
--------------
Ни один публичный метод не поднимает исключений: недоступная вкладка,
падение её get_data(), окно-заглушка вместо настоящего — всё это даёт
пустой раздел и запись в лог с именем раздела и типом ошибки. Сами данные
(ФИО, адреса, VIN, номера) в лог не попадают — только имена полей и
количества (AGENTS.md § 3.3).
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger("core.mirror")

#: Источник — пока только Экспедиторство.
SOURCE_TYPE = "perevozka"

#: Целевые типы, поддерживающие зеркало.
SUPPORTED_TARGETS = {"formika", "logistiks_rus"}

#: Человекочитаемые названия разделов источника — для сообщений в логе.
SOURCE_SECTIONS: Dict[str, str] = {
    "driver": "водитель",
    "vehicles": "перевозимые ТС",
    "loadings": "погрузки",
    "unloadings": "выгрузки",
    "tractor": "тягач",
    "trailer": "полуприцеп",
    "contract": "условия договора",
}


@dataclass
class MirrorPlan:
    """План переноса: что кладём в целевые вкладки и что этому мешает."""

    #: {ключ_вкладки: {поле: значение}} — что переносим.
    tabs: Dict[str, Dict[str, Any]]
    #: [(вкладка, поле, старое, новое)] — конфликты.
    conflicts: List[tuple] = field(default_factory=list)

    def field_count(self) -> int:
        """Сколько полей всего в плане (для лога и отчёта)."""
        return sum(len(data) for data in self.tabs.values())


@dataclass(frozen=True)
class FieldProfile:
    """
    Как сравнивать значение поля цели с тем, что несёт план.

    Простое равенство тут не работает: целевые вкладки отдают данные своими
    ключами и своими структурами — адреса погрузки Логистикса приходят
    списком строк, грузополучатели — списком словарей {name, address},
    машины Формики — списком словарей {brand_model, vin}. Поэтому у поля
    есть профиль: для списков — по каким вложенным ключам сравнивать.

    :param keys: ключи вложенных словарей (пусто — поле сравнивается как есть).
    """

    keys: Tuple[str, ...] = ()

    def signature(self, value: Any) -> Tuple[str, ...]:
        """
        Приводит значение к кортежу строк для сравнения.

        Пустые элементы списка отбрасываются: вкладка отдаёт только
        заполненные строки, а план может нести пустые — это не различие.
        """
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(
                _item_signature(item, self.keys) for item in value
                if not _is_empty(item, self.keys)
            )
        return (text(value),)


#: Профили сравнения по вкладкам цели: ключ вкладки → поле → профиль.
#: Поля, которых здесь нет, сравниваются как обычный текст.
TARGET_PROFILES: Dict[str, Dict[str, FieldProfile]] = {
    "formika": {
        "cargo_tab": {"vehicles": FieldProfile(("brand_model", "vin"))},
    },
    "logistiks_rus": {
        "cargo_tab": {"vehicles": FieldProfile(("brand_model", "vin"))},
        "route_tab": {
            "loading_addresses": FieldProfile(),
            "consignees": FieldProfile(("name", "address")),
        },
    },
}

#: Порядок вкладок в плане: по нему же идёт раскладка по вкладкам окна.
TAB_ORDER: Tuple[str, ...] = (
    "customer_tab",
    "cargo_tab",
    "route_tab",
    "driver_tab",
    "vehicle_tab",
    "price_tab",
)


# ─────────────────────────────────────────────────────────────
# Значения: пустота, текст, числа
# ─────────────────────────────────────────────────────────────

def text(value: Any) -> str:
    """
    Значение строкой без обрамляющих пробелов.

    None, bool и нестроковые пустые значения дают "": bool — это флажок
    интерфейса, а не данные (в форме такого поля нет), а 0 — наоборот,
    значение, поэтому ноль остаётся «0».
    """
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def is_empty(value: Any) -> bool:
    """
    Пустое ли значение — None, пустая строка, пустой список, bool.

    Ноль, ноль целых и «0» заполненными НЕ считаются пустыми: это значения
    ставки, суммы или года. Правило одно для всего модуля (в том числе для
    кнопки в шапке окна: пустые поля формы её включают).
    """
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def _is_empty(item: Any, keys: Tuple[str, ...]) -> bool:
    """
    Пустой ли элемент списка.

    У словарей с профилем «пустой» — тот, где пусты ВСЕ перечисленные ключи:
    строка таблицы без наименования и без адреса точкой не считается.
    """
    if isinstance(item, Mapping):
        if keys:
            return all(is_empty(item.get(key)) for key in keys)
        return all(is_empty(value) for value in item.values())
    return is_empty(item)


def _item_signature(item: Any, keys: Tuple[str, ...]) -> str:
    """Строковый отпечаток одного элемента списка (для сравнения)."""
    if isinstance(item, Mapping) and keys:
        # Разделитель — «\x1f»: он не встречается в данных и не даёт
        # склеек вида «AB» + «C» == «A» + «BC».
        return "\x1f".join(text(item.get(key)) for key in keys)
    return text(item)


# ─────────────────────────────────────────────────────────────
# Чтение источника (MainWindow — Экспедиторство)
# ─────────────────────────────────────────────────────────────

def _section_data(window: Any, tab_attr: str, method: str,
                  section: str) -> Any:
    """
    Значение раздела источника: window.<tab_attr>.<method>().

    Ничего не поднимает наверх: окна может не быть, у вкладки может не быть
    метода, а сам метод может упасть — раздел останется пустым, а причина
    попадёт в лог. Значения данных в лог не пишутся: только имена.
    """
    if window is None:
        return None

    # getattr с default ловит только отсутствие атрибута, но не исключение
    # из свойства: у окна-заглушки вкладка может быть property, которое
    # падает. Ловим и это — раздел просто останется пустым.
    try:
        tab = getattr(window, tab_attr, None)
    except Exception as e:  # noqa: BLE001 — источник не должен ронять зеркало
        logger.error(
            "Зеркало: вкладка %r недоступна (%s) — раздел «%s» пуст",
            tab_attr, type(e).__name__, SOURCE_SECTIONS.get(section, section),
        )
        return None

    if tab is None:
        logger.debug("Зеркало: вкладки %r нет — раздел «%s» пуст",
                     tab_attr, SOURCE_SECTIONS.get(section, section))
        return None

    getter = getattr(tab, method, None)
    if getter is None:
        logger.warning(
            "Зеркало: у вкладки %r нет метода %s() — раздел «%s» пуст",
            tab_attr, method, SOURCE_SECTIONS.get(section, section),
        )
        return None

    try:
        return getter()
    except Exception as e:  # noqa: BLE001 — источник не должен ронять зеркало
        logger.error(
            "Зеркало: вкладка %r не отдала данные (%s) — раздел «%s» пуст",
            tab_attr, type(e).__name__, SOURCE_SECTIONS.get(section, section),
        )
        return None


def _as_list(value: Any) -> List[Any]:
    """
    Значение списком.

    Вкладка «Перевозимые автомобили» отдаёт список машин напрямую, но
    старые вызовы (и тесты) могут положить его в словарь ключом vehicles —
    принимаем обе формы.
    """
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, Mapping):
        inner = value.get("vehicles")
        if isinstance(inner, (list, tuple)):
            return list(inner)
    return []


def _as_mapping(value: Any) -> Dict[str, Any]:
    """Значение словарём (иначе пустой словарь)."""
    return dict(value) if isinstance(value, Mapping) else {}


def collect_source(main_window: Any) -> Dict[str, Any]:
    """
    Данные из MainWindow (Экспедиторство) в словарь по разделам.

    Используются публичные методы вкладок окна «Экспедиторство»:

        main_window.driver_tab.get_data()
        main_window.vehicles_tab.get_data()
        main_window.contract_tab.get_loadings()
        main_window.contract_tab.get_unloadings()
        main_window.trailer_tab.get_tractor_data()
        main_window.trailer_tab.get_trailer_data()
        main_window.contract_tab.get_data()

    Метод MainWindow._collect_data() здесь НЕ используется: он собирает
    ContractData для генератора (с приведением точек и городом) — для
    зеркала это лишнее преобразование, а приватный метод точки входа
    связал бы ядро с конкретным окном.

    :param main_window: окно «Экспедиторство» (MainWindow) или его замена.
    :return: словарь разделов driver, vehicles, loadings, unloadings,
        tractor, trailer, contract. Ключи есть всегда, значения могут быть
        пустыми: отсутствующая вкладка не ломает сборку.
    """
    source = {
        "driver": _as_mapping(_section_data(main_window, "driver_tab", "get_data", "driver")),
        "vehicles": _as_list(_section_data(main_window, "vehicles_tab", "get_data", "vehicles")),
        "loadings": _as_list(
            _section_data(main_window, "contract_tab", "get_loadings", "loadings")
        ),
        "unloadings": _as_list(
            _section_data(main_window, "contract_tab", "get_unloadings", "unloadings")
        ),
        "tractor": _as_mapping(
            _section_data(main_window, "trailer_tab", "get_tractor_data", "tractor")
        ),
        "trailer": _as_mapping(
            _section_data(main_window, "trailer_tab", "get_trailer_data", "trailer")
        ),
        "contract": _as_mapping(
            _section_data(main_window, "contract_tab", "get_data", "contract")
        ),
    }

    logger.info(
        "Зеркало: источник собран — машин=%s, погрузок=%s, выгрузок=%s, "
        "водитель=%s, тягач=%s, прицеп=%s",
        len(source["vehicles"]),
        len(source["loadings"]),
        len(source["unloadings"]),
        "да" if text(source["driver"].get("full_name")) else "нет",
        "да" if text(source["tractor"].get("plate_number")) else "нет",
        "да" if text(source["trailer"].get("plate_number")) else "нет",
    )
    return source


def source_has_data(source: Mapping[str, Any]) -> bool:
    """
    Есть ли в источнике хоть что-то, что имеет смысл переносить.

    Признаки — VIN машины и адрес погрузки или выгрузки (как в задании).
    Пустой договор Экспедиторства зеркалить нечего: перенос возвращает None,
    а подсказка кнопки в шапке говорит, чего не хватает (сама кнопка
    остаётся активной — выключенная ничего не объяснила бы).
    """
    if not isinstance(source, Mapping):
        return False

    vehicles = source.get("vehicles")
    for vehicle in (vehicles if isinstance(vehicles, (list, tuple)) else []):
        if isinstance(vehicle, Mapping) and text(vehicle.get("vin")):
            return True

    for section in ("loadings", "unloadings"):
        points = source.get(section)
        for point in (points if isinstance(points, (list, tuple)) else []):
            if isinstance(point, Mapping) and text(point.get("address")):
                return True

    return False


def _first_point(source: Mapping[str, Any], section: str) -> Dict[str, Any]:
    """Первая точка раздела источника (пустой словарь, если точек нет)."""
    points = source.get(section)
    if not isinstance(points, (list, tuple)):
        return {}
    for point in points:
        if isinstance(point, Mapping):
            return dict(point)
    return {}


def _contract_field(source: Mapping[str, Any], key: str) -> str:
    """Поле раздела «условия договора» источника строкой."""
    contract = source.get("contract")
    if not isinstance(contract, Mapping):
        return ""
    return text(contract.get(key))


def _drop_empty(data: Mapping[str, Any]) -> Dict[str, Any]:
    """Поля плана без пустых значений: пустое место значения не несёт."""
    return {key: value for key, value in data.items() if not is_empty(value)}


# ─────────────────────────────────────────────────────────────
# Карта: Экспедиторство → Формика
# ─────────────────────────────────────────────────────────────

#: Поля водителя: у Формики те же ключи, что у вкладки Экспедиторства
#: (ui/tabs/driver_tab.py и ui/windows/formika/tabs/driver_tab.py).
DRIVER_FIELDS: Tuple[str, ...] = (
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


def _vehicle_rows(vehicles: Any, keys: Iterable[str]) -> List[Dict[str, str]]:
    """
    Машины источника строками целевой вкладки.

    Берутся только перечисленные ключи: у вкладки Экспедиторства машина
    богаче (госномер, год, цвет, тип, привязка к точкам), но у Формики и
    Логистикса в таблице груза есть только марка/модель и VIN.
    """
    rows: List[Dict[str, str]] = []
    for vehicle in (vehicles if isinstance(vehicles, (list, tuple)) else []):
        if not isinstance(vehicle, Mapping):
            continue
        row = {key: text(vehicle.get(key)) for key in keys}
        if any(row.values()):
            rows.append(row)
    return rows


def plan_for_formika(source: Mapping[str, Any]) -> MirrorPlan:
    """
    Экспедиторство → Формика.

    Переносится (по вкладкам окна Формики):

        cargo_tab     — vehicles[*].vin, vehicles[*].brand_model
        route_tab     — маршрут (contract.route), адрес первой погрузки,
                        адрес первой выгрузки
        driver_tab    — все поля водителя (ФИО, паспорт, ВУ, телефон)
        vehicle_tab   — марка/номер тягача и полуприцепа, тип тягача
        customer_tab  — номер и дата договора (contract.number, contract.date)

    Погрузок и выгрузок в экспедиторском договоре может быть несколько,
    а в бланке Формики место ровно под одну точку с каждой стороны, поэтому
    берётся ПЕРВАЯ точка: остальные адреса в этот бланк не помещаются.

    НЕ переносится: стоимость (price_*, vat_rate, payment_days), реквизиты
    сторон (customer, carrier) и особые условия — см. docstring модуля.
    """
    contract = source.get("contract") if isinstance(source, Mapping) else {}
    contract = contract if isinstance(contract, Mapping) else {}

    loading = _first_point(source, "loadings")
    unloading = _first_point(source, "unloadings")
    tractor = _as_mapping(source.get("tractor"))
    trailer = _as_mapping(source.get("trailer"))
    driver = _as_mapping(source.get("driver"))

    tabs: Dict[str, Dict[str, Any]] = {
        "customer_tab": _drop_empty({
            "date": _contract_field(source, "date"),
            "number": _contract_field(source, "number"),
        }),
        "cargo_tab": _drop_empty({
            "vehicles": _vehicle_rows(source.get("vehicles"), ("brand_model", "vin")),
        }),
        "route_tab": _drop_empty({
            "route": text(contract.get("route")),
            "loading_address": text(loading.get("address")),
            "unloading_address": text(unloading.get("address")),
        }),
        "driver_tab": _drop_empty({
            field: text(driver.get(field)) for field in DRIVER_FIELDS
        }),
        "vehicle_tab": _drop_empty({
            "tractor_brand": text(tractor.get("brand_model")),
            "tractor_plate": text(tractor.get("plate_number")),
            # Тип ТС у Экспедиторства лежит в tractor_type (см. трактовку
            # в ui/windows/formika/data.py::_build_vehicle).
            "tractor_type": text(
                tractor.get("vehicle_type") or tractor.get("tractor_type")
            ),
            "trailer_brand": text(trailer.get("brand_model")),
            "trailer_plate": text(trailer.get("plate_number")),
        }),
    }

    plan = MirrorPlan(tabs=_ordered_tabs(tabs))
    logger.info(
        "Зеркало: план для Формики — вкладок=%s, полей=%s",
        len(plan.tabs), plan.field_count(),
    )
    return plan


# ─────────────────────────────────────────────────────────────
# Карта: Экспедиторство → Логистикс Рус
# ─────────────────────────────────────────────────────────────

def plan_for_logistiks(source: Mapping[str, Any]) -> MirrorPlan:
    """
    Экспедиторство → Логистикс Рус.

    Переносится (по вкладкам окна Логистикса):

        cargo_tab    — vehicles[*] (марка/модель и VIN)
        route_tab    — маршрут, адреса ВСЕХ погрузок, грузополучатели
                       (наименование + адрес) и «Грузоотправитель»
        driver_tab   — ФИО водителя (в бланке Логистикса других полей нет)
        vehicle_tab  — марка/номер тягача и прицепа
        customer_tab — номер и дата заявки

    Особенность раздела 1 заявки: грузоотправитель ОДИН, а адресов погрузки
    до десяти. Поэтому из погрузок источника берутся ВСЕ адреса подряд
    (loading_addresses), а имя грузоотправителя — из ПЕРВОЙ погрузки, у
    которой оно заполнено (shipper_name). Остальные имена не теряются молча:
    о них говорит лог, а в бланке места для второго грузоотправителя нет —
    ровно как в ui/windows/logistiks_rus/data.py.

    Раздел 2 — грузополучатели: имя и адрес каждой выгрузки.

    НЕ переносится: стоимость, реквизиты сторон (заказчик в этой заявке
    фиксирован) и особые условия.
    """
    contract = source.get("contract") if isinstance(source, Mapping) else {}
    contract = contract if isinstance(contract, Mapping) else {}

    loadings = [
        point for point in (source.get("loadings") or [])
        if isinstance(point, Mapping)
    ]
    unloadings = [
        point for point in (source.get("unloadings") or [])
        if isinstance(point, Mapping)
    ]

    loading_addresses = [
        text(point.get("address")) for point in loadings if text(point.get("address"))
    ]

    shipper_name = ""
    for point in loadings:
        shipper_name = text(point.get("name"))
        if shipper_name:
            break

    named = [text(point.get("name")) for point in loadings if text(point.get("name"))]
    if len(set(named)) > 1:
        logger.info(
            "Зеркало: у погрузок источника %s разных наименований — "
            "в Логистикс идёт первое (грузоотправитель в бланке один)",
            len(set(named)),
        )

    consignees = [
        {"name": text(point.get("name")), "address": text(point.get("address"))}
        for point in unloadings
        if text(point.get("name")) or text(point.get("address"))
    ]

    tractor = _as_mapping(source.get("tractor"))
    trailer = _as_mapping(source.get("trailer"))
    driver = _as_mapping(source.get("driver"))

    tabs: Dict[str, Dict[str, Any]] = {
        "customer_tab": _drop_empty({
            "date": _contract_field(source, "date"),
            "number": _contract_field(source, "number"),
        }),
        "cargo_tab": _drop_empty({
            "vehicles": _vehicle_rows(
                source.get("vehicles"), ("brand_model", "vin")
            ),
        }),
        "route_tab": _drop_empty({
            "route": text(contract.get("route")),
            "shipper_name": shipper_name,
            "loading_addresses": loading_addresses,
            "consignees": consignees,
        }),
        "driver_tab": _drop_empty({"full_name": text(driver.get("full_name"))}),
        "vehicle_tab": _drop_empty({
            "tractor_brand": text(tractor.get("brand_model")),
            "tractor_plate": text(tractor.get("plate_number")),
            "trailer_brand": text(trailer.get("brand_model")),
            "trailer_plate": text(trailer.get("plate_number")),
        }),
    }

    plan = MirrorPlan(tabs=_ordered_tabs(tabs))
    logger.info(
        "Зеркало: план для Логистикса — вкладок=%s, полей=%s, "
        "адресов погрузки=%s, грузополучателей=%s",
        len(plan.tabs), plan.field_count(),
        len(loading_addresses), len(consignees),
    )
    return plan


def _ordered_tabs(tabs: Mapping[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Вкладки плана в порядке TAB_ORDER; неизвестные — в конце."""
    ordered: Dict[str, Dict[str, Any]] = {}
    for key in TAB_ORDER:
        if tabs.get(key):
            ordered[key] = dict(tabs[key])
    for key, data in tabs.items():
        if key not in ordered and data:
            ordered[key] = dict(data)
    return ordered


# ─────────────────────────────────────────────────────────────
# Чтение цели и конфликты
# ─────────────────────────────────────────────────────────────

def _tab_data(window: Any, tab_key: str) -> Dict[str, Any]:
    """
    Текущие данные вкладки целевого окна.

    Вкладка ищется тем же способом, что и при раскладке плана
    (ui/windows/base_window.py::_tab_by_key): сначала метод окна, потом
    вкладки по порядку. Окно-заглушка вместо настоящего окна даст пустой
    словарь — это не ошибка зеркала.
    """
    getter = getattr(window, "_tab_by_key", None)
    tab = getter(tab_key) if callable(getter) else None
    if tab is None:
        return {}

    data_getter = getattr(tab, "get_data", None)
    if data_getter is None:
        return {}

    try:
        data = data_getter()
    except Exception as e:  # noqa: BLE001 — цель не должна ронять зеркало
        logger.error(
            "Зеркало: вкладка %r цели не отдала данные (%s)",
            tab_key, type(e).__name__,
        )
        return {}

    return dict(data) if isinstance(data, Mapping) else {}


def current_target_data(target_window: Any,
                        target_type: str) -> Dict[str, Dict[str, Any]]:
    """Текущие данные всех вкладок цели: {ключ вкладки: get_data()}."""
    profiles = TARGET_PROFILES.get(target_type) or {}
    data: Dict[str, Dict[str, Any]] = {}
    for key in profiles:
        data[key] = _tab_data(target_window, key)
    return data


def collect_conflicts(target_window: Any, plan: MirrorPlan,
                      *, target_type: str) -> List[tuple]:
    """
    Возвращает [(ключ_вкладки, имя_поля, старое, новое)].

    Для каждого поля плана берётся текущее значение целевой вкладки
    (tab.get_data()): если оно ЗАПОЛНЕНО и отличается от того, что несёт
    план, — это конфликт, о котором спросят пользователя.

    Пустое текущее значение конфликтом не считается: заполнить пустое поле
    можно молча. Поля, которых в данных вкладки нет вовсе, пропускаются:
    раскладывать их некуда, и предупреждать о них не о чем.
    """
    profiles = (TARGET_PROFILES.get(target_type) or {})
    if not profiles:
        # Тип цели без профилей — это не цель зеркала (Аренда, Хавалы,
        # само Экспедиторство): сравнивать нечего и предупреждать не о чем.
        logger.info(
            "Зеркало: у типа %r нет карты сравнения — конфликты не считаются",
            target_type,
        )
        return []

    conflicts: List[tuple] = []

    for tab_key, fields in plan.tabs.items():
        current = _tab_data(target_window, tab_key)
        if not current:
            continue

        tab_profiles = profiles.get(tab_key) or {}
        for name, new_value in fields.items():
            if name not in current:
                logger.debug(
                    "Зеркало: поле %s.%s в целевой вкладке отсутствует — "
                    "конфликт не проверяется", tab_key, name,
                )
                continue

            old_value = current.get(name)
            if is_empty(old_value):
                continue

            profile = tab_profiles.get(name) or FieldProfile()
            if profile.signature(old_value) != profile.signature(new_value):
                conflicts.append((tab_key, name, old_value, new_value))

    logger.info(
        "Зеркало: конфликтов найдено %s (цель=%s)", len(conflicts), target_type
    )
    return conflicts


# ─────────────────────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────────────────────

def mirror_from_expedition(target_window: Any,
                           target_type: str,
                           main_window: Any = None) -> Optional[MirrorPlan]:
    """
    Собирает данные источника (MainWindow), строит план, находит конфликты.

    :param target_window: окно цели (Формика / Логистикс Рус).
    :param target_type: ключ типа цели (ContractType.value).
    :param main_window: окно «Экспедиторство». Обычно не передаётся: окно
        цели ищется само (find_expedition_window), а аргумент нужен тестам
        и коду, который уже держит источник на руках.
    :return: MirrorPlan с планом и конфликтами или None, если:
        * target_type не в SUPPORTED_TARGETS;
        * источник (окно «Экспедиторство») недоступен;
        * в источнике нет данных (все VIN и адреса пустые).
    """
    if target_type not in SUPPORTED_TARGETS:
        logger.info("Зеркало: тип %r не поддержан — перенос пропущен", target_type)
        return None

    if main_window is None:
        # Импорт внутри функции: ссылка на окно разрешается в ui, а core
        # от ui зависеть не должен (на импорте это дало бы цикл ui ↔ core).
        from ui.windows.base_window import find_expedition_window

        main_window = find_expedition_window()

    if main_window is None:
        logger.info("Зеркало: окно «Экспедиторство» не найдено — перенос пропущен")
        return None

    source = collect_source(main_window)
    if not source_has_data(source):
        logger.info(
            "Зеркало: в источнике нет данных (VIN и адреса пусты) — "
            "перенос пропущен"
        )
        return None

    if target_type == "formika":
        plan = plan_for_formika(source)
    else:
        plan = plan_for_logistiks(source)

    if not plan.tabs:
        logger.info("Зеркало: план пуст — перенос пропущен")
        return None

    plan.conflicts = collect_conflicts(target_window, plan, target_type=target_type)
    return plan


__all__ = [
    "SOURCE_TYPE",
    "SUPPORTED_TARGETS",
    "TAB_ORDER",
    "TARGET_PROFILES",
    "DRIVER_FIELDS",
    "SOURCE_SECTIONS",
    "MirrorPlan",
    "FieldProfile",
    "collect_source",
    "collect_conflicts",
    "current_target_data",
    "mirror_from_expedition",
    "plan_for_formika",
    "plan_for_logistiks",
    "source_has_data",
    "text",
    "is_empty",
]
