#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единый контракт данных договора перевозки (Шаг 1 рефакторинга архитектуры).

Зачем это нужно
--------------
Раньше UI собирал данные дважды и по-разному:

  * ui/main_window.py::_on_create_contract  — для генерации DOCX;
  * ui/main_window.py::_on_save_to_db       — для сохранения в БД.

Наборы ключей разошлись, и это дало целый класс ошибок:
  * тягач и полуприцеп не попадали в договор (генератор ждал
    data["trailer"]["tractor"], а UI отдавал data["tractor"] / data["trailer"]);
  * город в договоре всегда был «Москва», потому что UI его не передавал.

Теперь UI собирает данные ОДИН раз в ContractData (MainWindow._collect_data()),
а генератор DOCX и слой БД читают один и тот же объект. Рассинхрон ключей
становится невозможным на уровне типов.

Обратная совместимость
----------------------
ContractData.coerce() принимает и старый dict-формат (включая историческую
вложенную структуру trailer.tractor), поэтому ContractGenerator продолжает
работать с dict-вызовами.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from core.address_utils import city_for_document, extract_city

logger = logging.getLogger("core.contract_data")

# Историческое поведение: если город определить не удалось — «Москва»
DEFAULT_CITY = "Москва"


# ─────────────────────────────────────────────────────────────
# Нормализаторы
# ─────────────────────────────────────────────────────────────

def _as_dict(value: Any) -> Dict[str, Any]:
    """Приводит значение к обычному dict (копия)."""
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _as_vehicle_list(value: Any) -> List[Dict[str, Any]]:
    """Приводит значение к списку словарей ТС."""
    result: List[Dict[str, Any]] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, Mapping):
                result.append(dict(item))
    return result


def _as_point_list(value: Any) -> List[Dict[str, str]]:
    """
    Приводит список точек маршрута к виду
    [{"name": str, "address": str, "date": str, "time_window": str}].

    `name` — наименование салона точки (ШАГ FIX-2.5): его подтягивает из
    справочника вкладка «Условия договора», а печатает генератор перевозки
    в заголовке блока («Выгрузка 1: <наименование> <адрес>»). Поле
    НЕОБЯЗАТЕЛЬНОЕ: точка без справочника приходит с пустой строкой, и
    генератор печатает только адрес, как раньше.

    Ключ `name` есть у точки ВСЕГДА — пустой строкой, если имени нет. На это
    опирается валидатор Логистикса (`_point_name_is_known`): пустое
    наименование он показывает замечанием, а отсутствие самого поля означало
    бы, что наименование в этих данных не предусмотрено.

    `date` и `time_window` по-прежнему строки: генератор и валидаторы
    разбирают их сами.
    """
    result: List[Dict[str, str]] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            if not isinstance(item, Mapping):
                continue
            result.append({
                "name": str(item.get("name", "") or ""),
                "address": str(item.get("address", "") or ""),
                "date": str(item.get("date", "") or ""),
                "time_window": str(item.get("time_window", "") or ""),
            })
    return result


# ─────────────────────────────────────────────────────────────
# Контракт данных
# ─────────────────────────────────────────────────────────────

@dataclass
class ContractData:
    """
    Полный набор данных одного договора-заявки.

    Поля сгруппированы по смыслу:
      * субъекты договора — driver / carrier / customer;
      * транспорт — vehicles (перевозимые авто), tractor, trailer;
      * условия — contract (номер, дата, ставка, НДС, оплата, плановые даты);
      * маршрут — loadings / unloadings (точки погрузки и выгрузки);
      * место заключения — city.

    Поля driver_id / customer_id / carrier_id — ссылки на записи справочника
    (вариант В). Их проставляет MainWindow._on_save_to_db сразу после
    сохранения водителя и организаций, поэтому договор помнит, кто его
    исполнял, даже если запись потом убрали из справочника: удаление там
    мягкое (is_deleted), и ссылка остаётся целой.
    """

    # ── Субъекты договора ──
    driver: Dict[str, Any] = field(default_factory=dict)
    carrier: Dict[str, Any] = field(default_factory=dict)
    customer: Dict[str, Any] = field(default_factory=dict)

    # ── Транспорт ──
    vehicles: List[Dict[str, Any]] = field(default_factory=list)
    tractor: Dict[str, Any] = field(default_factory=dict)
    trailer: Dict[str, Any] = field(default_factory=dict)

    # ── Условия договора (шапка из вкладки «Договор») ──
    contract: Dict[str, Any] = field(default_factory=dict)

    # ── Точки маршрута ──
    loadings: List[Dict[str, str]] = field(default_factory=list)
    unloadings: List[Dict[str, str]] = field(default_factory=list)

    # ── Место заключения договора ──
    city: str = ""

    # ── Ссылки на записи справочника (вариант В, см. docstring) ──
    driver_id: Optional[int] = None
    customer_id: Optional[int] = None
    carrier_id: Optional[int] = None

    # ---------------------------------------------------------
    # Инициализация / нормализация
    # ---------------------------------------------------------

    def __post_init__(self) -> None:
        self.driver = _as_dict(self.driver)
        self.carrier = _as_dict(self.carrier)
        self.customer = _as_dict(self.customer)
        self.vehicles = _as_vehicle_list(self.vehicles)
        self.tractor = _as_dict(self.tractor)
        self.trailer = _as_dict(self.trailer)
        self.contract = _as_dict(self.contract)

        # Точки могут прийти как отдельными полями, так и внутри contract
        loadings = self.loadings or self.contract.get("loadings")
        unloadings = self.unloadings or self.contract.get("unloadings")
        self.loadings = _as_point_list(loadings)
        self.unloadings = _as_point_list(unloadings)

        self.city = str(self.city or self.contract.get("city") or "").strip()

    def bind_reference_ids(
        self,
        driver_id: Optional[int] = None,
        customer_id: Optional[int] = None,
        carrier_id: Optional[int] = None,
    ) -> None:
        """
        Проставляет ссылки на сохранённые записи справочника (вариант В).

        Отдельный метод, чтобы логику можно было проверить тестом без
        поднятия интерфейса: MainWindow вызывает его перед to_db_dict().
        """
        if driver_id:
            self.driver_id = int(driver_id)
        if customer_id:
            self.customer_id = int(customer_id)
        if carrier_id:
            self.carrier_id = int(carrier_id)

    # ---------------------------------------------------------
    # Город
    # ---------------------------------------------------------

    def resolved_city(self) -> str:
        """
        Город для договора.

        Приоритет:
          1) явно заданный city (поле UI / данные распознавания);
          2) город первой точки погрузки;
          3) город из юридического адреса перевозчика;
          4) DEFAULT_CITY («Москва») — как было раньше.
        """
        if self.city:
            normalized = city_for_document(self.city, default="")
            return normalized or self.city.strip()

        for point in self.loadings:
            city = city_for_document(point.get("address", ""), default="")
            if city:
                return city

        carrier_address = str(self.carrier.get("legal_address", "") or "")
        city = city_for_document(carrier_address, default="")
        if city:
            return city

        return DEFAULT_CITY

    def city_key(self) -> str:
        """Технический ключ города (нижний регистр) — для логов и сортировки."""
        return extract_city(self.loadings[0].get("address", "")) if self.loadings else ""

    # ---------------------------------------------------------
    # Представления для потребителей
    # ---------------------------------------------------------

    def to_generator_dict(self) -> Dict[str, Any]:
        """
        Структура для core.contract_generator.ContractGenerator.

        Отличие от исторического dict: tractor и trailer лежат ПЛОСКО (как их
        отдаёт UI), а не во вложенном data["trailer"]["tractor"].
        """
        contract = dict(self.contract)
        contract["loadings"] = [dict(p) for p in self.loadings]
        contract["unloadings"] = [dict(p) for p in self.unloadings]

        return {
            "driver": dict(self.driver),
            "carrier": dict(self.carrier),
            "customer": dict(self.customer),
            "vehicles": [dict(v) for v in self.vehicles],
            "tractor": dict(self.tractor),
            "trailer": dict(self.trailer),
            "contract": contract,
            "city": self.resolved_city(),
        }

    def to_db_dict(self) -> Dict[str, Any]:
        """Шапка договора для db.database.save_contract()."""
        payload = dict(self.contract)
        payload["driver_id"] = self.driver_id
        payload["customer_id"] = self.customer_id
        payload["carrier_id"] = self.carrier_id
        return payload

    def to_vehicle_rows(self) -> List[Dict[str, Any]]:
        """
        Строки для db.database.save_vehicles(): перевозимые авто + тягач +
        полуприцеп (ровно та логика, что была в MainWindow._on_save_to_db).
        """
        rows = [dict(v) for v in self.vehicles]

        if str(self.tractor.get("plate_number", "") or "").strip():
            rows.append({**self.tractor, "vehicle_type": "Тягач"})
        if str(self.trailer.get("plate_number", "") or "").strip():
            rows.append({**self.trailer, "vehicle_type": "Полуприцеп"})

        return rows

    # ---------------------------------------------------------
    # Совместимость со старым dict-форматом
    # ---------------------------------------------------------

    @classmethod
    def coerce(cls, value: Any) -> "ContractData":
        """
        Приводит произвольный вход (ContractData / dict / None) к ContractData.

        Понимает историческую структуру data["trailer"]["tractor"], поэтому
        старые вызовы генератора продолжают работать без изменений.
        """
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            logger.warning(
                f"ContractData.coerce: неожиданный тип {type(value).__name__}, "
                f"данные проигнорированы"
            )
            return cls()

        contract = _as_dict(value.get("contract"))

        raw_tractor = _as_dict(value.get("tractor"))
        raw_trailer = _as_dict(value.get("trailer"))

        # Историческая вложенность: data["trailer"] = {"tractor": {...}, "trailer": {...}}
        if "tractor" in raw_trailer or "trailer" in raw_trailer:
            tractor = raw_tractor or _as_dict(raw_trailer.get("tractor"))
            trailer = _as_dict(raw_trailer.get("trailer"))
        else:
            tractor = raw_tractor
            trailer = raw_trailer

        loadings = value.get("loadings")
        if loadings is None:
            loadings = contract.get("loadings")
        unloadings = value.get("unloadings")
        if unloadings is None:
            unloadings = contract.get("unloadings")

        return cls(
            driver=_as_dict(value.get("driver")),
            carrier=_as_dict(value.get("carrier")),
            customer=_as_dict(value.get("customer")),
            vehicles=_as_vehicle_list(value.get("vehicles")),
            tractor=tractor,
            trailer=trailer,
            contract=contract,
            loadings=_as_point_list(loadings),
            unloadings=_as_point_list(unloadings),
            city=str(value.get("city", "") or ""),
            driver_id=value.get("driver_id"),
            customer_id=value.get("customer_id"),
            carrier_id=value.get("carrier_id"),
        )

    # ---------------------------------------------------------
    # Диагностика
    # ---------------------------------------------------------

    def __repr__(self) -> str:
        """
        Компактное представление БЕЗ персональных данных.

        Важно: core/trace.py логирует аргументы функций, поэтому repr()
        не должен содержать паспорт, ФИО, адреса, названия организаций и
        город (см. Шаг 4 задания по безопасности).
        """
        return (
            f"ContractData(number={self.contract.get('number')!r}, "
            f"vehicles={len(self.vehicles)}, "
            f"loadings={len(self.loadings)}, "
            f"unloadings={len(self.unloadings)})"
        )

    def summary(self) -> str:
        """
        Короткая сводка для логов — только «есть/нет» и количества.

        Названия организаций, город и реквизиты в лог не попадают:
        перевозчик может быть индивидуальным предпринимателем, то есть
        его наименование содержит ФИО.
        """
        return (
            f"driver={'да' if self.driver.get('full_name') else 'нет'}, "
            f"carrier={'да' if (self.carrier.get('full_name') or self.carrier.get('short_name')) else 'нет'}, "
            f"customer={'да' if (self.customer.get('full_name') or self.customer.get('short_name')) else 'нет'}, "
            f"vehicles={len(self.vehicles)}, "
            f"tractor={'да' if self.tractor.get('plate_number') else 'нет'}, "
            f"trailer={'да' if self.trailer.get('plate_number') else 'нет'}, "
            f"loadings={len(self.loadings)}, "
            f"unloadings={len(self.unloadings)}, "
            f"city={'да' if self.resolved_city() else 'нет'}"
        )
