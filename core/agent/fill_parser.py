#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Разбор текста оператора в поля договора (задание, часть 1, пункты A.1–A.3).

Зачем модуль
------------
Оператор присылает Максимилиану текст: «Вины: … Загрузка: … Перевозчик: …
Сумма: 80000, НДС 20». Раньше помощник такой текст только пересказывал —
заполнять форму приходилось руками. Здесь живёт ЧИСТЫЙ РАЗБОР: текст на
входе, структура «что куда» на выходе. Ни Qt, ни прямой работы с SQL, ни
загрузки формы — поэтому разбор проверяется десятками тестов без
интерфейса. Заполнение (``core/agent/tools.py``) и превью
(``ui/agent_fill_preview.py``) — отдельные слои.

Что умеет
---------
1. **Разбор по ключевым словам** (A.1): «Вины:» / «VIN:» → машины,
   «Загрузка:» / «Погрузка:» → точки погрузки, «Выгрузка:» → точки
   выгрузки, «Перевозчик:», «Заказчик:», «Сумма:» / «Стоимость:»,
   «НДС:», «Дата:», «Маршрут:», «№» / «Номер:», «Срок оплаты:».
   Ключ действует и как заголовок раздела: строки ПОД ним (до следующего
   ключа) относятся к нему — так разбирается список вин столбиком.
2. **Разбор без ключевых слов** (A.2): VIN — 17 символов с буквами и
   цифрами, адреса — по признакам «г.», «ул.», «д.», «обл.», сумма —
   большое число, даты — «ДД.ММ.ГГГГ».
3. **Обогащение** (A.3): VIN нормализуется (кириллические буквы-двойники →
   латиница, как в ``core/recognizer.py``); перевозчик и заказчик ищутся в
   базе нечётким поиском; адреса при включённом DaData приводятся к
   стандартному виду; сумма приводится к числу.

Чего модуль НЕ делает
---------------------
Не пишет в форму и не меняет базу. Поиск организаций идёт через
``db/crud/organizations.py`` поверх готового ``search_organizations``. Если
база недоступна, разбор не падает — сущность просто остаётся ненайденной,
и оператор видит это в превью.

Правило про деньги: сумма — число (``80000`` → ``80000.0``), ставка НДС —
строка как в форме (``20`` → ``"20%"``).
"""

import json
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.dates import to_display as _date_display
from core.dates import to_iso
from core.recognizer import DataMapper, _VIN_SOURCE_RE

logger = logging.getLogger("core.agent.fill_parser")

# ─────────────────────────────────────────────────────────────
# Куда что попадает (разделы формы)
# ─────────────────────────────────────────────────────────────

SECTION_CONTRACT = "contract"
SECTION_CARRIER = "carrier"
SECTION_CUSTOMER = "customer"
SECTION_VEHICLE = "vehicles"
SECTION_LOADING = "loading"
SECTION_UNLOADING = "unloading"

#: Имя вкладки формы для каждого раздела. Те же ключи, что у окон
#: (``ui/main_window.py::_tab_keys``) и у импорта документов.
SECTION_TABS: Dict[str, str] = {
    SECTION_CONTRACT: "contract_tab",
    SECTION_CARRIER: "carrier_tab",
    SECTION_CUSTOMER: "customer_tab",
    SECTION_VEHICLE: "vehicles_tab",
}

#: Русская подпись раздела — для превью и ответа модели.
SECTION_TITLES: Dict[str, str] = {
    SECTION_CONTRACT: "Договор",
    SECTION_CARRIER: "Перевозчик",
    SECTION_CUSTOMER: "Заказчик",
    SECTION_VEHICLE: "Перевозимые ТС",
    SECTION_LOADING: "Погрузка",
    SECTION_UNLOADING: "Выгрузка",
}

#: Русские подписи полей договора: в превью оператор должен видеть «Сумма»,
#: а не ``price_with_vat``.
CONTRACT_FIELD_TITLES: Dict[str, str] = {
    "number": "Номер договора",
    "date": "Дата договора",
    "route": "Маршрут",
    "loadings": "Погрузка",
    "unloadings": "Выгрузка",
    "price_with_vat": "Сумма",
    "vat_rate": "Ставка НДС",
    "payment_days": "Срок оплаты, дней",
}

#: Подписи полей машины.
VEHICLE_FIELD_TITLES: Dict[str, str] = {
    "vin": "VIN",
    "brand_model": "Марка/Модель",
    "plate_number": "Госномер",
    "year": "Год выпуска",
    "color": "Цвет",
    "vehicle_type": "Тип ТС",
}

#: Подписи полей организации (в превью и в ответе модели).
ORGANIZATION_FIELD_TITLES: Dict[str, str] = {
    "full_name": "Наименование",
    "short_name": "Краткое наименование",
    "inn": "ИНН",
    "kpp": "КПП",
    "ogrn": "ОГРН",
    "legal_address": "Юридический адрес",
    "actual_address": "Фактический адрес",
    "bank_name": "Банк",
    "bank_account": "Расчётный счёт",
    "bik": "БИК",
    "correspondent_account": "Корр. счёт",
    "director_name": "Руководитель",
    "director_position": "Должность руководителя",
    "phone": "Телефон",
    "email": "Почта",
    "entity_type": "Форма",
    "basis": "Основание",
}

# ─────────────────────────────────────────────────────────────
# Ключевые слова (A.1)
# ─────────────────────────────────────────────────────────────

#: Ключ строки → (раздел, поле). Проверяются ПО ПОРЯДКУ, поэтому длинные
#: ключи стоят раньше коротких: «срок оплаты» должен сработать прежде, чем
#: «оплата», а «вин» — прежде, чем «вина».
KEYWORD_FIELDS: Tuple[Tuple[str, str, str], ...] = (
    ("вин", SECTION_VEHICLE, "vin"),
    ("вины", SECTION_VEHICLE, "vin"),
    ("вина", SECTION_VEHICLE, "vin"),
    ("vin", SECTION_VEHICLE, "vin"),
    ("тс", SECTION_VEHICLE, "vin"),
    ("груз", SECTION_VEHICLE, "vin"),
    ("срок оплаты", SECTION_CONTRACT, "payment_days"),
    ("оплата", SECTION_CONTRACT, "payment_days"),
    ("точка загрузки", SECTION_LOADING, "address"),
    ("загрузка", SECTION_LOADING, "address"),
    ("погрузка", SECTION_LOADING, "address"),
    ("точка выгрузки", SECTION_UNLOADING, "address"),
    ("выгрузка", SECTION_UNLOADING, "address"),
    ("разгрузка", SECTION_UNLOADING, "address"),
    ("перевозчик", SECTION_CARRIER, "full_name"),
    ("заказчик", SECTION_CUSTOMER, "full_name"),
    ("грузополучатель", SECTION_CUSTOMER, "full_name"),
    ("стоимость", SECTION_CONTRACT, "price_with_vat"),
    ("сумма", SECTION_CONTRACT, "price_with_vat"),
    ("цена", SECTION_CONTRACT, "price_with_vat"),
    ("ндс", SECTION_CONTRACT, "vat_rate"),
    ("маршрут", SECTION_CONTRACT, "route"),
    ("номер", SECTION_CONTRACT, "number"),
    ("дата", SECTION_CONTRACT, "date"),
)

#: Ключи-«не-поля»: по ним строка не разбирается, но ключом она быть не
#: должна (иначе «Примечание:» утащило бы в себя весь текст ниже).
IGNORED_KEYS: Tuple[str, ...] = (
    "примечание", "комментарий", "итого", "всего", "телефон", "марка",
    "модель", "водитель", "тип",
)

#: Что считается разделителем ключа (оператор пишет и «:», и «-»).
_KEY_SEPARATOR = re.compile(r"\s*[:：]\s*|\s+[—–-]\s+")

#: Ключ ВНУТРИ значения: «Сумма: 80000, НДС 20» — НДС идёт вторым ключом в
#: той же строке. Список слов берётся из KEYWORD_FIELDS (единый источник).
_INLINE_KEY_RE = re.compile(
    r"(?:(?<=^)|(?<=[\s,;.]))(?P<key>"
    + "|".join(word for word, _section, _name in KEYWORD_FIELDS)
    + r")\s*:?\s*(?=\d)",
    re.IGNORECASE,
)

#: Знаки по краям значения, которые к данным не относятся.
_VALUE_TRIM = " \t\"'«».。,;"

# ─────────────────────────────────────────────────────────────
# Признаки данных (A.2)
# ─────────────────────────────────────────────────────────────

#: Дата в любом из привычных видов: 15.10.2026, 2026-10-15, 15/10/2026.
_DATE_RE = re.compile(r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{4}-\d{2}-\d{2})\b")

#: Время погрузки/выгрузки: «с 9:00 до 18:00», «09:00-18:00».
_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")

#: Деньги: «80 000», «80000,50», «80 000.50 руб».
_MONEY_RE = re.compile(r"\d[\d\s\u00a0]*(?:[.,]\d{1,2})?")

#: Признаки адреса: «г.», «ул.», «д.», «обл.», а также «шоссе», «проспект».
_ADDRESS_RE = re.compile(
    r"(\bг\.|\bгор\.|\bгород\b|\bул\.|\bулица\b|\bд\.\s*\d|\bдом\b|"
    r"\bобл\.|\bобласть\b|\bкрай\b|\bпер\.|\bпереулок\b|\bпр-т|\bпроспект\b|"
    r"\bшоссе\b|\bпрош\b|\bпроезд\b|\bнаб\.|\bнабережная\b|\bпл\.|"
    r"\bплощадь\b|\bкв\.|\bофис\b|\bсклад\b|\bтерминал\b|\bсалон\b)",
    re.IGNORECASE,
)

#: Наименьшее число, которое считается суммой договора (A.2: «большое
#: число»). Тысяча — это ставка или предоплата, а не стоимость рейса.
_MONEY_MIN = 10000

#: Личные документы и телефоны: оператор иногда присылает их вместе с
#: данными рейса. В форму такое не раскладывается — только сообщается.
_SENSITIVE_RE = re.compile(
    r"(\bпаспорт\b|\bсерия\b|\bвыдан\b|\bСНИЛС\b|\+7[\s(]|\b8\s?\(?\d{3})",
    re.IGNORECASE,
)

#: Что не может быть названием организации.
_NOT_A_NAME = frozenset({
    "нет", "не", "неизвестно", "нет данных", "пусто", "-", "—", "бн", "б/н",
})

#: Почти-VIN: 15–19 символов «буквы VIN и цифры». Так выглядит VIN,
#: набранный с ошибкой (одна буква потеряна или лишняя): в таблицу он не
#: идёт, но и молчать о нём нельзя — оператор увидит его в превью.
_NEAR_VIN_RE = re.compile(
    rf"(?<![A-Za-z0-9])"
    rf"([A-HJ-NPR-Za-hj-npr-z0-9]{{15,19}})"
    rf"(?![A-Za-z0-9])"
)


# ─────────────────────────────────────────────────────────────
# Данные разбора
# ─────────────────────────────────────────────────────────────

@dataclass
class ParsedItem:
    """
    Одна строка превью: «что нашли → куда попадёт».

    :param section: раздел формы (contract, carrier, customer, vehicles,
        loading, unloading);
    :param field: имя поля в разделе (``full_name``, ``price_with_vat``…);
    :param label: подпись для оператора («Сумма», «Перевозчик»);
    :param value: значение, как оно уйдёт в форму;
    :param source: текст оператора, из которого значение взято;
    :param status: ``ok`` (готово к записи) или ``warning`` (есть сомнение);
    :param note: пояснение к статусу («VIN 10 символов вместо 17»).
    """

    section: str
    field: str
    label: str
    value: Any
    source: str = ""
    status: str = "ok"
    note: str = ""

    @property
    def target(self) -> str:
        """Раздел формы, куда попадёт значение (для превью и отчёта)."""
        return SECTION_TITLES.get(self.section, self.section)

    def preview_text(self) -> str:
        """Значение так, как его видит оператор в превью."""
        return display_value(self.value)

    def to_dict(self) -> Dict[str, Any]:
        """Словарь для передачи между слоями (UI, инструмент, тесты)."""
        return {
            "section": self.section,
            "field": self.field,
            "label": self.label,
            "value": self.value,
            "source": self.source,
            "status": self.status,
            "note": self.note,
            "target": self.target,
            "text": display_value(self.value),
        }


@dataclass
class ParsedFill:
    """Результат разбора текста: что нашли, что поняли не полностью."""

    raw_text: str = ""
    #: Строки превью по порядку: договор → точки маршрута → стороны → машины.
    items: List[ParsedItem] = field(default_factory=list)
    #: Найденные записи: перевозчик и заказчик (``{id, name, fields}``).
    entities: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Машины: ``{vin, brand_model}`` — строки таблицы «Перевозимые ТС».
    vehicles: List[Dict[str, Any]] = field(default_factory=list)
    #: Не разобрано: текст, который показывается оператору как есть.
    unresolved: List[str] = field(default_factory=list)
    #: Какие ключевые слова встретились (нужно тестам и отчёту).
    keywords: List[str] = field(default_factory=list)
    #: Замечания: «VIN короче 17 символов», «адрес распознан неточно».
    warnings: List[str] = field(default_factory=list)
    #: Что искали в базе и НЕ нашли (для ответа модели и оператору).
    missing_entities: List[Dict[str, str]] = field(default_factory=list)
    #: Несколько совпадений по названию: оператор выбирает в превью.
    ambiguous_entities: List[Dict[str, Any]] = field(default_factory=list)

    # ---------------------------------------------------------
    @property
    def is_empty(self) -> bool:
        """
        Разобрать не удалось ничего.

        Найденная в базе запись (перевозчик, заказчик) тоже считается
        разбором: её поля раскладываются по форме, хотя отдельной строки в
        превью у неё нет.
        """
        return not self.items and not self.vehicles and not self.entities

    def contract_fields(self) -> Dict[str, Any]:
        """Поля вкладки «Договор»: имя поля → значение (без машин и точек)."""
        return self.fields_for(SECTION_CONTRACT)

    def fields_for(self, section: str) -> Dict[str, Any]:
        """Поля одного раздела: имя поля → значение."""
        return {
            item.field: item.value
            for item in self.items
            if item.section == section
        }

    def point_rows(self, section: str) -> List[Dict[str, str]]:
        """Точки погрузки или выгрузки списком строк таблицы договора."""
        value = self.fields_for(section).get("address")
        if isinstance(value, list):
            return [dict(row) for row in value]
        return []

    def tabs_of(self) -> List[str]:
        """Какие вкладки формы будут заполнены (договор — первой)."""
        order: List[str] = []
        for item in self.items:
            tab = SECTION_TABS.get(item.section)
            if tab and tab not in order:
                order.append(tab)
        vehicle_tab = SECTION_TABS[SECTION_VEHICLE]
        if self.vehicles and vehicle_tab not in order:
            order.append(vehicle_tab)
        if "contract_tab" in order:
            order.remove("contract_tab")
            order.insert(0, "contract_tab")
        return order

    def counts(self) -> Dict[str, int]:
        """Сколько чего разобрано — для ответа оператору."""
        contract = len([item for item in self.items
                        if item.section == SECTION_CONTRACT])
        points = len([item for item in self.items
                      if item.section in (SECTION_LOADING, SECTION_UNLOADING)])
        carrier = len((self.entities.get(SECTION_CARRIER) or {}).get("fields")
                      or {})
        customer = len((self.entities.get(SECTION_CUSTOMER) or {}).get("fields")
                       or {})
        return {
            "vehicles": len(self.vehicles),
            "contract": contract,
            "points": points,
            "carrier": carrier,
            "customer": customer,
            "entity_fields": carrier + customer,
            "items": len(self.items),
            "fields_total": len(self.preview_rows()),
            "unresolved": len(self.unresolved),
        }

    def preview_rows(self) -> List[Dict[str, str]]:
        """
        Таблица превью: «поле — значение — куда».

        Одна строка на значение; точки маршрута и машины разворачиваются по
        одной строке, чтобы оператор правил адрес, а не список целиком
        (задание, часть B.1).
        """
        rows: List[Dict[str, str]] = []
        for item in self.items:
            if self._replaced_by_entity(item):
                #: Сторону нашли в базе — вместо строки «Перевозчик: …» в
                #: превью идут поля найденной записи (см. ниже).
                continue
            if item.section == SECTION_VEHICLE:
                #: Машины печатаются ниже, по одной строке на машину
                #: (``self.vehicles``): у строки разбора значение — список
                #: машин, и в таблице он читался бы как «ТС (2) 1».
                continue
            if isinstance(item.value, list):
                many = len(item.value) > 1
                for index, row in enumerate(item.value, start=1):
                    rows.append({
                        "field": f"{item.label} {index}" if many else item.label,
                        "field_name": item.field,
                        "value": display_value(row),
                        "target": item.target,
                        "section": item.section,
                        "status": item.status,
                        "note": item.note,
                    })
                continue
            rows.append({
                "field": item.label,
                "field_name": item.field,
                "value": display_value(item.value),
                "target": item.target,
                "section": item.section,
                "status": item.status,
                "note": item.note,
            })

        for index, vehicle in enumerate(self.vehicles, start=1):
            #: Машина показывается ОДНОЙ строкой («VIN · марка»): строк
            #: превью и без того много, а правит оператор исходный текст.
            vin = str(vehicle.get("vin") or "")
            brand = str(vehicle.get("brand_model") or "").strip()
            rows.append({
                "field": f"ТС {index}",
                "field_name": "vehicle",
                "value": " · ".join(part for part in (vin, brand) if part),
                "target": SECTION_TITLES[SECTION_VEHICLE],
                "section": SECTION_VEHICLE,
                "status": str(vehicle.get("status") or "ok"),
                "note": str(vehicle.get("note") or ""),
            })

        for kind in (SECTION_CARRIER, SECTION_CUSTOMER):
            record = self.entities.get(kind) or {}
            title = SECTION_TITLES[kind]
            for name, value in (record.get("fields") or {}).items():
                rows.append({
                    "field": f"{title}: {ORGANIZATION_FIELD_TITLES.get(name, name)}",
                    "field_name": name,
                    "value": display_value(value),
                    "target": title,
                    "section": kind,
                    "status": "ok",
                    "note": "",
                })
        return rows

    def _replaced_by_entity(self, item: "ParsedItem") -> bool:
        """
        Заменяет ли эту строку найденная в базе запись.

        Название стороны, найденное в справочнике, показывается полями
        записи (наименование, ИНН, адрес), а не одной строкой «Перевозчик».
        """
        return item.field == "full_name" and item.section in self.entities

    def summary_lines(self) -> List[str]:
        """Короткое описание разбора — для ответа модели и панели."""
        counts = self.counts()
        lines: List[str] = []
        if counts["vehicles"]:
            lines.append(f"ТС ({counts['vehicles']}): " + ", ".join(
                str(vehicle.get("vin") or "") for vehicle in self.vehicles
            ))

        for item in self.items:
            if item.section in (SECTION_CARRIER, SECTION_CUSTOMER) \
                    and not item.section in self.entities:
                #: Ненайденная сторона печатается своей строкой — оператор
                #: должен видеть, какое название ушло в форму.
                if isinstance(item.value, list):
                    for row in item.value:
                        lines.append(f"{item.label}: {display_value(row)}")
                else:
                    lines.append(f"{item.label}: {display_value(item.value)}")
                continue
            if item.section in (SECTION_CARRIER, SECTION_CUSTOMER):
                #: Сторона печатается ниже — найденной записью.
                continue
            if item.section == SECTION_VEHICLE:
                # Машины печатаются одной строкой выше: в строках превью у
                # каждой из них отдельная запись (VIN + марка).
                continue
            if isinstance(item.value, list):
                for row in item.value:
                    lines.append(f"{item.label}: {display_value(row)}")
                continue
            lines.append(f"{item.label}: {display_value(item.value)}")
        for kind in (SECTION_CARRIER, SECTION_CUSTOMER):
            record = self.entities.get(kind) or {}
            if record:
                lines.append(
                    f"{SECTION_TITLES[kind]}: {record.get('name')} "
                    f"(нашёл в базе, полей: {len(record.get('fields') or {})})"
                )
        for record in self.missing_entities:
            title = SECTION_TITLES.get(record.get("kind", ""), "Запись")
            lines.append(f"{title}: «{record.get('name')}» — в базе не нашёл")
        for record in self.ambiguous_entities:
            title = SECTION_TITLES.get(record.get("kind", ""), "Запись")
            lines.append(
                f"{title}: «{record.get('name')}» — похожих записей "
                f"{len(record.get('candidates') or [])}, нужно выбрать"
            )
        return lines

    def to_dict(self) -> Dict[str, Any]:
        """Полный результат разбора словарём (ответ инструмента)."""
        return {
            "items": [item.to_dict() for item in self.items],
            "vehicles": [dict(vehicle) for vehicle in self.vehicles],
            "entities": {
                kind: {
                    "id": record.get("id"),
                    "name": record.get("name"),
                    "fields": dict(record.get("fields") or {}),
                }
                for kind, record in self.entities.items()
            },
            "unresolved": list(self.unresolved),
            "warnings": list(self.warnings),
            "missing_entities": [dict(record) for record in self.missing_entities],
            "ambiguous_entities": [
                {"kind": record.get("kind"), "name": record.get("name"),
                 "candidates": [
                     {"id": item.get("id"), "name": item.get("name")}
                     for item in record.get("candidates") or ()
                 ]}
                for record in self.ambiguous_entities
            ],
            "keywords": list(self.keywords),
            "counts": self.counts(),
            "summary": self.summary_lines(),
            "preview": self.preview_rows(),
            "tabs": self.tabs_of(),
            "empty": self.is_empty,
        }

    # ---------------------------------------------------------
    # Правки оператора (превью, часть B.2)
    # ---------------------------------------------------------
    def overrides(self, items: Any = None, vehicles: Any = None, **kwargs
                  ) -> Dict[str, Any]:
        """
        Собирает правки оператора для инструмента: ``{"items", "vehicles"}``.

        Оставлено ради единой точки: диалог превью отдаёт правки словарём,
        а инструмент принимает их теми же именами аргументов.
        """
        if items is None:
            items = kwargs.get("items")
        if vehicles is None:
            vehicles = kwargs.get("vehicles")
        result: Dict[str, Any] = {}
        if items:
            result["items"] = items
        if vehicles:
            result["vehicles"] = vehicles
        return result

    def apply_overrides(self, overrides: Any = None,
                        vehicles: Any = None) -> "ParsedFill":
        """
        Заменяет значения правками из превью и повторяет обогащение.

        Оператор правит ячейку в диалоге: значение подменяется, а стороны
        (перевозчик, заказчик) и адреса ищутся заново — иначе исправленное
        название осталось бы без реквизитов.

        :param overrides: список ``{"section", "field", "value"}``;
        :param vehicles: новый список машин (VIN и марка) или None.
        """
        for raw in _as_list(overrides):
            if not isinstance(raw, dict):
                continue
            section = str(raw.get("section") or "").strip()
            name = str(raw.get("field") or "").strip()
            if not section or not name:
                continue
            value = _coerce_override(name, raw.get("value"))
            for item in self.items:
                if item.section == section and item.field == name:
                    if isinstance(item.value, list) and not isinstance(value, list):
                        #: Точки маршрута — СПИСОК строк, а не поле. Правка
                        #: приходит из превью текстом («Воронеж, Кольцовская 1»),
                        #: и подстановка строки вместо списка стёрла бы ВЕСЬ
                        #: маршрут. Такие правки диалог не присылает (адрес
                        #: правится в исходном тексте), поэтому строка-правка
                        #: здесь игнорируется — данные важнее.
                        logger.debug(
                            "Разбор: правка «%s.%s» строкой пропущена — "
                            "точки маршрута правит исходный текст",
                            section, name,
                        )
                        break
                    item.value = value
                    item.status = "ok"
                    item.note = ""
                    break
            else:
                # Строки в разборе не было (например, перевозчика в первый
                # раз не нашли, и оператор вписал название в превью):
                # правка добавляется новым значением, а не теряется.
                self.items.append(ParsedItem(
                    section=section, field=name,
                    label=SECTION_TITLES.get(section, name), value=value,
                ))

        if vehicles is not None:
            self.vehicles = normalize_vehicles(vehicles)

        # Значения изменились — стороны и адреса пересобираются заново.
        self._resolve_entities()
        self._resolve_entity_items()
        self._clean_points()
        return self

    def _resolve_entities(self) -> None:
        """
        Ищет перевозчика и заказчика в базе.

        Запись переносится в ``entities`` только при ОДНОМ совпадении:
        выбрать «Ромашку» из пяти похожих должен оператор, а не догадка.
        """
        self.entities = {}
        self.missing_entities = []
        self.ambiguous_entities = []
        for section in (SECTION_CARRIER, SECTION_CUSTOMER):
            value = self.fields_for(section).get("full_name")
            term = str(value or "").strip()
            if not term:
                continue
            record, candidates = resolve_organization(
                term, is_carrier=(section == SECTION_CARRIER)
            )
            if record:
                self.entities[section] = record
            elif candidates:
                self.ambiguous_entities.append({
                    "kind": section, "name": term, "candidates": candidates,
                })
            else:
                self.missing_entities.append({"kind": section, "name": term})

    def _resolve_entity_items(self) -> None:
        """
        Оставляет в разборе строку стороны, найденной в базе.

        Убирать её НЕЛЬЗЯ, хотя в превью она не показывается (там её
        заменяют поля найденной записи). Причина: оператор правит значения в
        превью, и после правки стороны ищутся заново — а искать можно только
        по той строке, где записано название. Если её удалить, исправленное
        название потерялось бы, и в форму ушли бы только договор и машины
        (проверено живым прогоном).
        """
        return

    def _clean_points(self) -> None:
        """
        Приводит адреса точек к стандартному виду (DaData) — если включено.

        Ни один сбой здесь не отменяет разбор: адрес остаётся ровно таким,
        как его написал оператор, а в предупреждения попадает причина. DaData
        для автозаполнения — удобство, а не условие его работы (B.2).

        Про сбой сообщается ОДИН раз на разбор, а не на каждый адрес: в
        маршруте их до десяти, и десять одинаковых предупреждений оператору
        не нужны.
        """
        failed = 0
        total = 0
        last_error = ""
        for section in (SECTION_LOADING, SECTION_UNLOADING):
            for item in self.items:
                if item.section != section or item.field != "address":
                    continue
                if not isinstance(item.value, list):
                    continue
                for row in item.value:
                    address = str(row.get("address") or "").strip()
                    if not address:
                        continue
                    total += 1
                    try:
                        cleaned, note = clean_address(address)
                    except Exception as e:  # noqa: BLE001 — разбор важнее DaData
                        # Второй рубеж: clean_address() уже ловит всё, но
                        # ошибка внешнего сервиса не должна ронять разбор
                        # даже при будущих правках (B.2, B.3).
                        failed += 1
                        last_error = type(e).__name__
                        continue
                    if cleaned and cleaned != address:
                        row["address"] = cleaned
                    if note and note not in self.warnings:
                        self.warnings.append(note)

        if failed:
            logger.warning(
                "DaData: адреса не стандартизированы (%s из %s, %s) — "
                "оставлены как есть", failed, total, last_error,
            )
            if DADATA_UNAVAILABLE_NOTE not in self.warnings:
                self.warnings.append(DADATA_UNAVAILABLE_NOTE)


# ─────────────────────────────────────────────────────────────
# Преобразования значений
# ─────────────────────────────────────────────────────────────

def display_value(value: Any) -> str:
    """
    Значение для превью и ответа оператору.

    Списки и словари (машины, точки маршрута) печатаются одной строкой: в
    таблице превью у них отдельные строки, а здесь — короткое описание.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        parts = [display_value(item) for item in value]
        return "; ".join(part for part in parts if part)
    if isinstance(value, dict):
        if "vin" in value:
            parts = [str(value.get("vin") or "").strip(),
                     str(value.get("brand_model") or "").strip()]
            return " ".join(part for part in parts if part)
        address = str(value.get("address") or "").strip()
        date = to_display(value.get("date"))
        window = str(value.get("time_window") or "").strip()
        parts = [part for part in (address, date, window) if part]
        return ", ".join(parts) if parts else "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float):
        # «80000.0» оператору читать неприятно: сумма — без хвоста.
        if value == int(value):
            return f"{int(value):,}".replace(",", " ")
        return f"{value:,.2f}".replace(",", " ")
    return str(value)


def to_display(value: Any) -> str:
    """Дата в виде ДД.ММ.ГГГГ — одна реализация на весь проект."""
    return _date_display(value)


def normalize_vin(value: Any) -> str:
    """
    VIN к латинице и верхнему регистру (A.3).

    Используется та же нормализация, что и при распознавании документов:
    русская «Т» в VIN — это латинская «T» (``core/recognizer.py``).
    """
    return DataMapper.normalize_vin(value)


def vin_is_valid(value: Any) -> bool:
    """Настоящий ли VIN (17 символов, без I, O, Q)."""
    return DataMapper.vin_is_valid(value)


def parse_price(value: Any) -> Optional[float]:
    """
    Сумма → число: «80 000,50» → ``80000.5``, «80000 руб.» → ``80000.0``.

    None — если числа в значении нет вовсе.
    """
    text = "" if value is None else str(value)
    match = _MONEY_RE.search(text)
    if not match:
        return None
    candidate = match.group(0).replace("\u00a0", "").replace(" ", "")
    candidate = candidate.replace(",", ".")
    try:
        number = Decimal(candidate)
    except (InvalidOperation, ValueError):
        return None
    return float(number)


def parse_int(value: Any) -> Optional[int]:
    """Целое из значения: «10 дней» → ``10``."""
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def normalize_vat_rate(value: Any) -> str:
    """
    Ставка НДС в вид формы: «20» → ``20%``, «НДС 20 %» → ``20%``.

    Форма знает ставки «20%», «10%», «0%», «22%», «5%», «7%» и «без НДС»:
    к ним значение и приводится. Незнакомая ставка всё равно возвращается
    ставкой (``«7%»``) — оператор увидит её в превью и поправит.
    """
    text = "" if value is None else str(value).strip().casefold()
    if not text:
        return ""
    if "без" in text or "не облага" in text:
        return "без НДС"
    match = re.search(r"(\d{1,2})(?:[.,]\d+)?\s*%?", text)
    if not match:
        return ""
    rate = match.group(1).lstrip("0") or "0"
    return f"{rate}%"


def parse_date_iso(value: Any) -> str:
    """Дата в формате ГГГГ-ММ-ДД (так её ждёт поле договора)."""
    if value in (None, ""):
        return ""
    text = str(value).strip()
    match = _DATE_RE.search(text)
    if not match:
        # ISO-дата без времени тоже принимается: «2026-10-15».
        return to_iso(text) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else ""
    return to_iso(match.group(1))


def _date_after_address(text: str) -> str:
    """Дата, стоящая в строке с адресом (после самого адреса)."""
    matches = list(_DATE_RE.finditer(text))
    return matches[-1].group(1) if matches else ""


def _time_window(text: str) -> str:
    """Окно времени из строки: «с 9:00 до 18:00» → ``09:00-18:00``."""
    times = _TIME_RE.findall(str(text or ""))
    if len(times) < 2:
        return ""
    first, second = times[0], times[1]
    if len(first) == 4:
        first = "0" + first
    if len(second) == 4:
        second = "0" + second
    return f"{first}-{second}"


def _address_without_noise(value: str) -> str:
    """Адрес без даты и окна времени: они едут отдельными полями."""
    text = str(value or "")
    # Даты и время убираются вместе с окружающими знаками: «Ленина, д. 5 —
    # 15.10.2026» не должно превратиться в «Ленина, д. 5 —».
    text = _DATE_RE.sub(" ", text)
    text = re.sub(r"\bс\s*\d{1,2}:\d{2}\s*(?:до|-|—)\s*\d{1,2}:\d{2}\b", " ",
                  text, flags=re.IGNORECASE)
    text = _TIME_RE.sub(" ", text)
    text = re.sub(r"^\s*(?:загрузка|погрузка|выгрузка|разгрузка)\s*[:—-]?\s*",
                  "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\s,;]*(?:—|–|-|,|;|—\s*)+$", "", text.strip())
    return " ".join(text.split()).strip(" ,;.")


def _looks_like_address(text: str) -> bool:
    """Похожа ли строка на адрес (A.2)."""
    text = str(text or "").strip()
    if not text:
        return False
    if _ADDRESS_RE.search(text):
        return True
    # «Москва, Ленина 5» — без «г.» и «ул.», но с городом и домом.
    return bool(re.match(r"^[А-ЯЁ][а-яё-]+,\s*\S+.*\d", text))


# ─────────────────────────────────────────────────────────────
# Обогащение: база и DaData (A.3)
# ─────────────────────────────────────────────────────────────

#: Поля организации, которые есть в форме (порядок — как в форме).
ORGANIZATION_FIELDS: Tuple[str, ...] = (
    "full_name", "short_name", "inn", "kpp", "ogrn", "legal_address",
    "actual_address", "bank_account", "bik", "correspondent_account",
    "bank_name", "director_name", "director_position", "phone", "email",
    "entity_type", "basis",
)


def resolve_organization(name: str, is_carrier: bool = True,
                         limit: int = 5
                         ) -> Tuple[Optional[Dict[str, Any]],
                                    List[Dict[str, Any]]]:
    """
    Нечёткий поиск организации в базе (A.3).

    :return: ``(запись, варианты)``. Запись возвращается, только если
        совпадение РОВНО одно; иначе ``None`` и список вариантов — выбор
        делает оператор в превью. Ошибка базы не роняет разбор:
        ``(None, [])``.
    """
    term = str(name or "").strip()
    if not term or term.casefold() in _NOT_A_NAME:
        return None, []

    try:
        from core.agent.tools import _clean_term, looks_like_command_or_noise
        from db.crud.organizations import search_organizations

        cleaned = _clean_term(term) or term
        if looks_like_command_or_noise(cleaned):
            return None, []
        rows = search_organizations(cleaned, is_carrier=is_carrier,
                                    limit=max(1, int(limit)))
    except Exception as e:  # noqa: BLE001 — база может быть недоступна
        logger.warning("Поиск организации не удался: %s", type(e).__name__)
        return None, []

    candidates: List[Dict[str, Any]] = []
    for row in rows or ():
        record = _organization_record(row)
        if record:
            candidates.append(record)
    if not candidates:
        return None, []
    if len(candidates) == 1:
        return candidates[0], candidates

    # Совпадений несколько — решаем по ИМЕНИ, а не по порядку выдачи поиска:
    # «Ромашка» в базе с «ООО «Ромашка»» и «ООО «Ромашка-Логистик»» — это
    # первая, а не та, что нашлась раньше.
    wanted = _name_key(term)
    scored = [(_name_score(wanted, record), record) for record in candidates]
    scored.sort(key=lambda pair: pair[0][0], reverse=True)
    best_score, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else (0, 0, 0)
    if best_score[0] > 0 and (
        best_score[0] > runner_up[0] or best_score[1] > runner_up[1]
    ):
        return best, candidates
    return None, candidates


def _name_key(value: Any) -> str:
    """Название для сравнения: без кавычек, «ООО», «ИП» и знаков."""
    text = str(value or "").casefold()
    text = re.sub(r"[«»\"'(),.]", " ", text)
    text = re.sub(r"\b(ооо|оао|зао|пао|ао|ип|нко|тоо|оо)\b", " ", text)
    return " ".join(text.replace("-", " ").split())


def _name_score(wanted: str, record: Dict[str, Any]
                ) -> Tuple[int, int, int]:
    """
    Насколько запись отвечает названию, которое назвал оператор.

    :return: ``(точное совпадение, совпадение начала, длина названия)`` —
        сравнение идёт по каждому пункту по очереди.
    """
    if not wanted:
        return 0, 0, 0
    fields = record.get("fields") or {}
    best = (0, 0, 0)
    for value in (record.get("name"), fields.get("short_name"),
                  fields.get("full_name")):
        key = _name_key(value)
        if not key:
            continue
        if key == wanted:
            score = (2, 1, len(key))
        elif key.startswith(wanted) or wanted.startswith(key):
            score = (1, 1, len(key))
        elif wanted in key:
            score = (1, 0, len(key))
        else:
            score = (0, 0, len(key))
        if score > best:
            best = score
    return best


def _organization_record(row: Dict[str, Any]) -> Dict[str, Any]:
    """Запись базы → «id, name, поля формы» (пустые поля не переносятся)."""
    if not isinstance(row, dict):
        return {}
    fields = {
        key: row.get(key)
        for key in ORGANIZATION_FIELDS
        if str(row.get(key) if row.get(key) is not None else "").strip()
    }
    return {
        "id": row.get("id"),
        "name": str(row.get("full_name") or row.get("short_name") or "").strip(),
        "fields": fields,
    }


#: Что оператор видит, когда адреса не удалось привести к стандартному виду.
DADATA_UNAVAILABLE_NOTE = (
    "DaData недоступна — адреса оставлены так, как вы их написали"
)


def clean_address(address: str) -> Tuple[str, str]:
    """
    Стандартный вид адреса через DaData (A.3).

    Возвращает ``(адрес, замечание)``. Правило проекта: ошибка ВНЕШНЕГО
    сервиса не должна ронять основную операцию (B.2), поэтому при любом
    сбое — нет ключа, 403 «ключ не принят», лимит, сеть, неожиданный ответ —
    возвращается адрес КАК ЕСТЬ и замечание для оператора. Автозаполнение
    продолжается, просто без стандартизации адресов; в лог идёт WARNING,
    а не ERROR: программа не сломалась, отказал внешний сервис.

    Пусто и без замечания — адрес пустой, спрашивать нечего.
    """
    text = str(address or "").strip()
    if not text:
        return "", ""

    try:
        from core.agent.dadata_tools import check_address_dadata

        result = check_address_dadata(address=text)
    except Exception as e:  # noqa: BLE001 — DaData не обязательна
        logger.warning(
            "DaData: адрес не проверен (%s) — оставляю как есть",
            type(e).__name__,
        )
        return text, DADATA_UNAVAILABLE_NOTE

    if not isinstance(result, dict) or not result.get("ok"):
        # Инструмент отвечает словарём и сам не поднимает исключений, но
        # «не ok» — это тоже «стандартизировать не вышло».
        logger.warning(
            "DaData: адрес не стандартизирован (%s) — оставляю как есть",
            str((result or {}).get("error") or "сервис не ответил")[:120],
        )
        return text, DADATA_UNAVAILABLE_NOTE

    data = result.get("data") or {}
    cleaned = str(data.get("address") or data.get("result") or "").strip()
    if not cleaned:
        # Сервис ответил, но разобрать адрес не смог: это не сбой, а ответ
        # «такой адрес не знаю». Молча оставляем как написал оператор.
        return text, ""
    note = ""
    quality = data.get("qc")
    if quality not in (None, 0, "0"):
        note = f"Адрес «{text}» распознан неточно — проверьте"
    return cleaned, note


# ─────────────────────────────────────────────────────────────
# Разбор строк
# ─────────────────────────────────────────────────────────────

def _keyword_of(line: str) -> Tuple[Optional[Tuple[str, str, str]],
                                    List[Tuple[Tuple[str, str, str], str]],
                                    str]:
    """
    Разбирает строку на «ключ» и «значение».

    Ключом считается только начало строки: «Перевозчик: Ромашка» — ключ,
    «Ромашка — перевозчик» — уже нет (иначе данные стали бы заголовком).

    Кроме главного ключа ищутся ключи ВНУТРИ значения: оператор пишет
    «Сумма: 80000, НДС 20» одной строкой, и без этого НДС терялся бы.

    :return: ``(главный ключ, дополнительные ключи со значениями, значение)``
    """
    if not line:
        return None, [], ""

    head, separator, tail = line.partition(":")
    if not separator:
        # Ключ без двоеточия бывает только у номера: «№ 123», «N 123».
        match = re.match(r"^\s*(?:№|N|No)\s*([0-9][\w/-]*)\s*$", line,
                         flags=re.IGNORECASE)
        if match:
            return ("номер", SECTION_CONTRACT, "number"), [], match.group(1)
        head, separator, tail = _split_dash(line)
        if not separator:
            return None, [], line

    key = _key_name(head)
    if not key:
        return None, [], line
    if key in IGNORED_KEYS:
        return None, [], tail

    found: Optional[Tuple[str, str, str]] = None
    for word, section, field_name in KEYWORD_FIELDS:
        if key == word or key.startswith(word + " "):
            found = (word, section, field_name)
            break
    if found is None and key.startswith("номер"):
        found = ("номер", SECTION_CONTRACT, "number")
    if found is None and key.startswith("дата"):
        found = ("дата", SECTION_CONTRACT, "date")
    if found is None:
        return None, [], line

    return found, _inline_keys(tail), tail


def _inline_keys(value: str) -> List[Tuple[Tuple[str, str, str], str]]:
    """
    Ключи внутри значения: «80000, НДС 20» → ``[(«ндс», «20»)]``.

    Берутся только ключи известных полей: строка «Москва, Ленина 5» ключей
    не содержит, и разбирать её повторно не нужно.
    """
    text = str(value or "")
    if not text:
        return []
    matches = list(_INLINE_KEY_RE.finditer(text))
    if not matches:
        return []

    result: List[Tuple[Tuple[str, str, str], str]] = []
    for index, match in enumerate(matches):
        word = match.group("key").casefold()
        found = next((entry for entry in KEYWORD_FIELDS if entry[0] == word),
                     None)
        if found is None:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[match.end():end].strip(" ,;.")
        if chunk:
            result.append((found, chunk))
    return result


def _split_dash(line: str) -> Tuple[str, str, str]:
    """Делит строку по длинному тире или дефису с пробелами."""
    match = _KEY_SEPARATOR.search(line)
    if not match:
        return line, "", ""
    return line[:match.start()], ":", line[match.end():]


def _key_name(head: str) -> str:
    """Ключ строки в нижнем регистре без знаков и лишних слов."""
    text = str(head or "").strip().strip(" \t\"'«».,;")
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).casefold()
    # «2 вина», «Вины (ТС)» — числительные и скобки ключу не мешают.
    text = re.sub(r"^[\d.)\s]+", "", text)
    text = re.sub(r"\s*\(.*?\)\s*", " ", text).strip()
    return text


def _vin_tokens(text: str) -> List[Tuple[str, str]]:
    """
    Все VIN-кандидаты строки: ``(как напечатано, нормализованный)``.

    Кандидат — токен из 17 символов латиницы или кириллических двойников
    (шаблон распознавания). Ровно 17 — потому что «похоже на VIN»
    определяется именно длиной; всё остальное остаётся маркой.
    """
    found: List[Tuple[str, str]] = []
    seen: set = set()
    for match in _VIN_SOURCE_RE.finditer(str(text or "")):
        raw = match.group(1)
        normalized = normalize_vin(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        found.append((raw, normalized))
    return found


def normalize_vehicles(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    """
    Строки машин к виду таблицы ТС: ``{vin, brand_model}`` (+ замечание).

    Принимает и словари, и строки «Марка VIN» — так превью может вернуть
    правки оператора в любом виде.
    """
    result: List[Dict[str, Any]] = []
    seen: set = set()
    for row in rows or ():
        if isinstance(row, dict):
            raw_vin = str(row.get("vin") or "").strip()
            brand = str(row.get("brand_model") or "").strip()
            year = row.get("year") or 0
        else:
            text = str(row or "").strip()
            tokens = _vin_tokens(text)
            raw_vin = tokens[0][1] if tokens else ""
            brand = text
            year = 0
            for raw, _normalized in tokens:
                brand = brand.replace(raw, " ")
            brand = " ".join(brand.split()).strip(" -–—:;,.")
            brand = re.sub(r"^\d+[.)]\s*", "", brand).strip()

        vin = normalize_vin(raw_vin)
        if not vin or vin in seen:
            continue
        seen.add(vin)

        vehicle: Dict[str, Any] = {"vin": vin, "brand_model": brand,
                                   "status": "ok", "note": ""}
        if str(year or "").strip() not in ("", "0"):
            try:
                vehicle["year"] = int(year)
            except (TypeError, ValueError):
                pass
        if not vin_is_valid(vin):
            vehicle["status"] = "warning"
            vehicle["note"] = (
                f"VIN {len(vin)} символов вместо 17 — проверьте по документу"
            )
        result.append(vehicle)
    return result


def _vehicle_item(vehicles: List[Dict[str, Any]]) -> ParsedItem:
    """Строка превью для списка машин."""
    warnings = [str(vehicle.get("note") or "") for vehicle in vehicles
                if vehicle.get("note")]
    return ParsedItem(
        section=SECTION_VEHICLE,
        field="vehicles",
        label=f"ТС ({len(vehicles)})",
        value=[dict(vehicle) for vehicle in vehicles],
        status="warning" if warnings else "ok",
        note=warnings[0] if warnings else "",
    )


#: Сколько подряд идущих букв должно быть в слове, чтобы считать его
#: маркой. Отсекает номера, годы и телефоны.
_BRAND_LETTERS_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)

#: Год выпуска: «2024», «2024 г.в.», «2011 год».
_YEAR_RE = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")

#: Что не может быть маркой: служебные слова таблиц.
_NOT_A_BRAND = frozenset({
    "вин", "vin", "тс", "номер", "марка", "модель", "итого", "всего", "нет",
})


def _brand_text(value: Any) -> str:
    """Годен ли текст на роль марки (буквы есть, мусора нет)."""
    text = " ".join(str(value or "").replace("\t", " ").split())
    text = text.strip(_VALUE_TRIM).strip()
    if len(text) < 3 or text.casefold() in _NOT_A_BRAND:
        return ""
    if not _BRAND_LETTERS_RE.search(text):
        return ""
    return text


def _is_vin_cell(value: Any) -> bool:
    """Ячейка таблицы целиком является VIN (без пробелов внутри)."""
    text = str(value or "").strip()
    if len(text) != 17:
        return False
    return bool(_VIN_SOURCE_RE.fullmatch(text))


def _brand_from_cells(cells: Sequence[str], vin_index: int) -> str:
    """
    Марка — текстовая ячейка рядом с VIN.

    Табличная строка (вставка из Excel) приходит как «… | VIN | Марка | …»,
    поэтому кандидаты ищутся СПРАВА от VIN, а если их нет — слева. Из
    кандидатов берётся самый «текстовый»: в строке Excel рядом стоят город,
    дилер и телефоны, и брать первую попавшуюся ячейку нельзя.
    """
    right = list(range(vin_index + 1, len(cells)))
    left = list(range(vin_index - 1, -1, -1))

    def score(index: int) -> Tuple[int, int, int]:
        text = _brand_text(cells[index])
        letters = len(_BRAND_LETTERS_RE.findall(text)) if text else 0
        return (1 if text else 0, letters, len(text))

    for order in (right, left):
        best = ""
        best_score = (0, 0, 0)
        for index in order:
            candidate = _brand_text(cells[index])
            if not candidate:
                continue
            if score(index) > best_score:
                best, best_score = candidate, score(index)
        if best:
            return best
    return ""


def _year_from_cells(cells: Sequence[str], vin_index: int) -> int:
    """Год выпуска рядом с VIN (0 — не найден)."""
    for index in range(vin_index + 1, len(cells)):
        cell = str(cells[index] or "").strip()
        if not cell:
            continue
        match = _YEAR_RE.search(cell)
        if match and not _is_vin_cell(cell):
            return int(match.group(1))
    return 0


def _add_vehicle(vehicles: List[Dict[str, Any]], vin: Any,
                 brand: Any = "", year: Any = 0) -> None:
    """
    Добавляет машину в список (без повторов по VIN).

    Марка дописывается к уже найденной машине: оператор часто присылает
    сначала столбик вин, а потом строки «марка + VIN» — терять марку нельзя.
    """
    normalized = normalize_vin(vin)
    if not normalized:
        return

    brand_text = _brand_text(brand)
    for existing in vehicles:
        if existing["vin"] == normalized:
            if brand_text and not existing.get("brand_model"):
                existing["brand_model"] = brand_text
            if year and not existing.get("year"):
                existing["year"] = int(year)
            return

    vehicle: Dict[str, Any] = {
        "vin": normalized,
        "brand_model": brand_text,
        "status": "ok",
        "note": "",
    }
    if year:
        vehicle["year"] = int(year)
    if not vin_is_valid(normalized):
        vehicle["status"] = "warning"
        vehicle["note"] = (
            f"VIN {len(normalized)} символов вместо 17 — проверьте по документу"
        )
    vehicles.append(vehicle)


def _add_vehicles_from_line(line: Any, vehicles: List[Dict[str, Any]],
                            unresolved: Optional[List[str]] = None) -> bool:
    """
    Разбирает ОДНУ строку как машину (или список машин) — A.2.

    :return: True, если в строке нашлась хоть одна машина. False — строка
        разбирается дальше (адрес, сумма, дата).
    """
    text = str(line or "").replace("\t", " ").strip()
    if not text:
        return False

    cells = [cell.strip() for cell in re.split(r"\s*\|\s*|\t", str(line or ""))]
    vin_index = next((i for i, cell in enumerate(cells) if _is_vin_cell(cell)),
                     -1)

    if vin_index >= 0 and len(cells) > 1:
        # Вставка из таблицы: у машины своя колонка марки и года.
        _add_vehicle(vehicles, cells[vin_index],
                     _brand_from_cells(cells, vin_index),
                     _year_from_cells(cells, vin_index))
        return True

    tokens = _vin_tokens(text)
    if not tokens:
        near = _near_vin(text)
        if near:
            # Похоже на VIN, но символов не 17: значение НЕ теряется — строка
            # попадает в таблицу с предупреждением (как в подсветке ТС), а в
            # превью оператор видит, что именно проверить. Исключение — когда
            # чистка даёт ровно 17 символов: это VIN, набранный с разделителем.
            cleaned = normalize_vin(near)
            if len(cleaned) == 17 and vin_is_valid(cleaned):
                _add_vehicle(vehicles, cleaned)
            else:
                _add_vehicle(vehicles, near)
            if unresolved is not None:
                unresolved.append(
                    f"{near} — похоже на VIN, но символов "
                    f"{len(near)} вместо 17"
                )
            return False
        if unresolved is not None and len(text.strip(" ,.;")) >= 3:
            unresolved.append(text.strip(" ,.;"))
        return False

    if len(tokens) == 1:
        raw, normalized = tokens[0]
        brand = " ".join(text.replace(raw, " ").split())
        brand = re.sub(r"^\d+[.)]\s*", "", brand).strip(" -–—:;,.")
        _add_vehicle(vehicles, normalized, brand)
        return True

    # Несколько вин в одной строке — это несколько машин БЕЗ марок: текст
    # между ними маркой не является.
    for _raw, normalized in tokens:
        _add_vehicle(vehicles, normalized)
    return True


def _count_vins(text: Any) -> int:
    """Сколько VIN-кандидатов в строке (нужно эвристике свободного разбора)."""
    return len(list(_VIN_SOURCE_RE.finditer(str(text or ""))))


def _near_vin(text: str) -> str:
    """
    Похожее на VIN значение неверной длины (иначе — пусто).

    Нужно, чтобы «EC37CUSM4TC00864» (16 символов) не уходило в «не
    разобрано» безликой строкой: оператору важно понять, что это VIN и что
    в нём ошибка. Требуется хотя бы одна БУКВА — иначе длинный номер счёта
    или телефона выглядел бы «почти VIN».
    """
    for match in _NEAR_VIN_RE.finditer(str(text or "")):
        candidate = match.group(1)
        if not re.search(r"[A-Za-z]", candidate):
            continue
        if not _vin_tokens(candidate):
            return candidate
    return ""


#: Заголовки колонок таблиц, которые оператор копирует вместе с данными.
_TABLE_HEADER_WORDS = frozenset({
    "vin", "вин", "марка", "модель", "год", "дата", "город", "пункт",
    "дилер", "номер", "цвет", "ставка", "лот", "тс", "водитель", "фио",
    "серия", "паспорт", "телефон", "адрес", "итого", "всего",
})


def _looks_like_table_header(text: str) -> bool:
    """
    Строка — заголовок таблицы, а не данные.

    Оператор копирует машины из Excel вместе с шапкой («VIN Марка Год»),
    и без этой проверки заголовок попадал бы в «не разобрано».
    """
    words = [word.strip(_VALUE_TRIM).casefold() for word in str(text).split()]
    words = [word for word in words if word]
    if len(words) < 2:
        return False
    known = [word for word in words if word in _TABLE_HEADER_WORDS]
    return len(known) >= 2 and len(known) >= len(words) - 1


def _point_item(section: str, rows: List[Dict[str, str]]) -> ParsedItem:
    """Строка превью для точек погрузки или выгрузки."""
    return ParsedItem(
        section=section,
        field="address",
        label=SECTION_TITLES[section],
        value=rows,
    )


def _add_point(rows: List[Dict[str, str]], value: str) -> None:
    """Добавляет точку маршрута: адрес, дата и окно времени — по полям."""
    date = parse_date_iso(_date_after_address(value))
    window = _time_window(value)
    address = _address_without_noise(value)
    if not address and not date:
        return
    rows.append({"name": "", "address": address, "date": date,
                 "time_window": window})


# ─────────────────────────────────────────────────────────────
# Главная функция
# ─────────────────────────────────────────────────────────────

def parse_text(text: Any, use_dadata: bool = True) -> ParsedFill:
    """
    Разбирает текст оператора в поля договора (A.1–A.3).

    Ничего не меняет: результат — структура для превью и подтверждения.
    Заполнение формы делает ``core/agent/tools.py::fill_parsed`` после
    того, как оператор нажал «Подтвердить».

    :param text: текст оператора (многострочный);
    :param use_dadata: спрашивать ли DaData про адреса (в тестах — False).
    """
    raw = "" if text is None else str(text)
    result = ParsedFill(raw_text=raw)

    contract: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    loadings: List[Dict[str, str]] = []
    unloadings: List[Dict[str, str]] = []
    vehicles: List[Dict[str, Any]] = []
    entity_names: Dict[str, str] = {}
    unresolved: List[str] = []

    #: Куда класть значение — одна дорога для главного ключа строки и для
    #: ключей внутри значения («Сумма: 80000, НДС 20»).
    state = {
        "contract": contract, "sources": sources, "loadings": loadings,
        "unloadings": unloadings, "vehicles": vehicles,
        "entities": entity_names, "unresolved": unresolved,
        "free_points": [],
    }

    current: Optional[Tuple[str, str, str]] = None
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or _looks_like_table_header(line):
            # Пустая строка и шапка таблицы («VIN Марка Год») — не данные.
            continue

        key, inline, value = _keyword_of(line)
        if key:
            current = key
            if key[0] not in result.keywords:
                result.keywords.append(key[0])
            if not value.strip():
                # Значение придёт следующими строками: ключ работает как
                # заголовок раздела («Вины:» и столбик вин под ним).
                continue
        else:
            # Ключа нет: значение разбирается в разделе действующего ключа,
            # а если и его нет — эвристикой (A.2).
            value = line

        word, section, name = current if current else ("", "", "")
        _store_parsed_value(section, name, value, state, quoted=bool(key))

        for extra_key, extra_value in inline:
            _store_parsed_value(extra_key[1], extra_key[2], extra_value, state,
                                quoted=True)

    # ── Второй проход: номер и дата могли прийти без ключевого слова ──
    _assign_free_points(state)
    _free_pass(contract, sources, raw, loadings + unloadings)

    # ── Сборка строк превью: договор → точки → стороны → машины ──
    _add_contract_items(result, contract, sources)
    if loadings:
        result.items.append(_point_item(SECTION_LOADING, loadings))
    if unloadings:
        result.items.append(_point_item(SECTION_UNLOADING, unloadings))
    for section, name in entity_names.items():
        result.items.append(ParsedItem(
            section=section, field="full_name",
            label=SECTION_TITLES[section], value=name, source=name,
        ))
    result.vehicles = normalize_vehicles(vehicles)
    if result.vehicles:
        result.items.append(_vehicle_item(result.vehicles))

    # ── Обогащение (A.3): база и DaData ──
    result._resolve_entities()
    result._resolve_entity_items()
    if use_dadata:
        try:
            result._clean_points()
        except Exception as e:  # noqa: BLE001 — внешний сервис: разбор важнее
            # Последний рубеж перед выдачей результата: что бы ни случилось
            # на стороне DaData, разобранный текст оператор получит (B.2).
            logger.warning(
                "DaData: адреса не стандартизированы (%s) — разбор продолжен",
                type(e).__name__,
            )
            if DADATA_UNAVAILABLE_NOTE not in result.warnings:
                result.warnings.append(DADATA_UNAVAILABLE_NOTE)

    for item in result.items:
        if item.status == "warning" and item.note:
            result.warnings.append(item.note)
    for vehicle in result.vehicles:
        if vehicle.get("note"):
            result.warnings.append(str(vehicle["note"]))

    result.unresolved = _dedupe(unresolved)
    logger.info(
        "Разбор текста: строк=%s, строк превью=%s, машин=%s, не разобрано=%s",
        len(raw.splitlines()), len(result.items), len(result.vehicles),
        len(result.unresolved),
    )
    return result


def _store_parsed_value(section: str, name: str, value: str,
                        state: Dict[str, Any], quoted: bool = False) -> None:
    """
    Кладёт значение в свой раздел разбора.

    :param quoted: значение пришло от КЛЮЧА (а не строкой под ним). Разница
        важна для точек маршрута: под ключом бывает список через «;», а
        строкой ниже — одна точка.
    """
    if section == SECTION_VEHICLE:
        _collect_vehicles(value, state["vehicles"], state["unresolved"])
        return
    if section in (SECTION_LOADING, SECTION_UNLOADING):
        rows = (state["loadings"] if section == SECTION_LOADING
                else state["unloadings"])
        for chunk in _split_points(value) if quoted else [value]:
            _add_point(rows, chunk)
        return
    if section in (SECTION_CARRIER, SECTION_CUSTOMER):
        cleaned = _clean_value(value)
        if cleaned:
            state["entities"][section] = cleaned
        return
    if section == SECTION_CONTRACT:
        _store_contract_value(name, value, state["contract"], state["sources"],
                              state["unresolved"])
        return

    # Ключа нет и раздела нет: разбираем строку эвристикой (A.2).
    _parse_free_line(value, state["vehicles"], state["contract"],
                     state["sources"], state, state["unresolved"])


def _assign_free_points(state: Dict[str, Any]) -> None:
    """
    Раскладывает адреса без ключевых слов на погрузку и выгрузку (A.2).

    Без подписей адрес сам по себе не говорит, погрузка это или выгрузка,
    поэтому работает порядок рейса: ПЕРВЫЙ адрес — погрузка, остальные —
    выгрузки. Если даты в адресах есть и они разные, адрес с поздней датой
    считается выгрузкой даже первым — рейс идёт от погрузки к выгрузке.
    """
    points = state.get("free_points") or []
    if not points:
        return
    parsed_rows = []
    for text in points:
        rows: List[Dict[str, str]] = []
        _add_point(rows, text)
        if rows:
            parsed_rows.append(rows[0])
    if not parsed_rows:
        return

    #: Строки без даты сортировать нечем — они остаются в исходном порядке.
    dated = sorted((row["date"], index) for index, row in enumerate(parsed_rows)
                   if row.get("date"))
    if len(dated) >= 2:
        #: Даты известны — рейс идёт от ранней к поздней, порядок строк в
        #: тексте значения не имеет.
        order = [index for _date, index in dated]
    else:
        order = list(range(len(parsed_rows)))

    state["loadings"].append(parsed_rows[order[0]])
    state["unloadings"].extend(parsed_rows[index] for index in order[1:])


def _split_points(value: str) -> List[str]:
    """Делит значение ключа на несколько точек маршрута (;, |, «и»)."""
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"\s*[;|]\s*|\s+и\s+(?=[А-ЯЁ])", text)
    return [part.strip() for part in parts if part.strip()]


def _clean_value(value: Any) -> str:
    """Значение без кавычек и знаков по краям."""
    text = "" if value is None else str(value)
    return text.strip(_VALUE_TRIM).strip()


def _coerce_override(name: str, value: Any) -> Any:
    """
    Правка оператора из превью к типу поля.

    В таблице превью всё выглядит текстом, но поле формы ждёт число: без
    приведения «95000» из ячейки уходило бы в форму строкой.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = _clean_value(value)
    if not text:
        return text
    if name == "price_with_vat":
        number = parse_price(text)
        return number if number is not None else text
    if name == "payment_days":
        number = parse_int(text)
        return number if number is not None else text
    if name == "vat_rate":
        return normalize_vat_rate(text) or text
    if name == "date":
        return parse_date_iso(text) or text
    return text


def _collect_vehicles(value: str, vehicles: List[Dict[str, Any]],
                      unresolved: List[str]) -> None:
    """
    Машины из значения ключа «Вины:» / «VIN:» — построчно.

    Марка берётся по правилу распознавания: всё, что стоит ПЕРЕД VIN в этой
    же строке (``core/recognizer.py::repair_vehicle_list``), — иначе марку
    пришлось бы угадывать, и она съезжала бы на чужую машину.
    """
    for chunk in str(value or "").splitlines():
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.endswith(":"):
            # Внутри раздела встретился новый ключ («Водитель:»): значение
            # к нему разбирает основной цикл, здесь его быть не должно.
            continue
        _add_vehicles_from_line(chunk, vehicles, unresolved)


def _store_contract_value(name: str, value: str, contract: Dict[str, Any],
                          sources: Dict[str, str],
                          unresolved: List[str]) -> None:
    """Кладёт значение ключа в поле договора, приводя его к типу."""
    if not name:
        return
    raw = _clean_value(value)
    if not raw:
        return

    converted: Any = raw
    if name == "price_with_vat":
        number = parse_price(raw)
        if number is None:
            unresolved.append(f"сумма: {raw}")
            return
        converted = number
    elif name == "vat_rate":
        converted = normalize_vat_rate(raw)
        if not converted:
            unresolved.append(f"НДС: {raw}")
            return
    elif name == "date":
        converted = parse_date_iso(raw)
        if not converted:
            unresolved.append(f"дата: {raw}")
            return
    elif name == "payment_days":
        converted = parse_int(raw)
        if converted is None:
            unresolved.append(f"срок оплаты: {raw}")
            return

    contract[name] = converted
    sources[name] = raw


def _free_pass(contract: Dict[str, Any], sources: Dict[str, str],
               raw: str, points: Sequence[Dict[str, str]] = ()) -> None:
    """
    Доразбор договора без ключевых слов (A.2).

    Ищем то, что нельзя спутать: № договора и дату. Значение добавляется,
    только если такого поля ещё нет — ключевое слово главнее.

    :param points: найденные точки маршрута: дата, которая стоит в адресе
        погрузки или выгрузки, — это дата рейса, а не дата договора.
    """
    if "number" not in contract:
        match = re.search(r"(?:№|N|No)\s*([0-9][\w/-]{2,})", raw, re.IGNORECASE)
        if match:
            contract["number"] = match.group(1)
            sources["number"] = match.group(0)
    if "date" not in contract:
        # Дата попадает в договор БЕЗ ключевого слова, только если она в
        # тексте одна и не относится к точке маршрута: две даты — это почти
        # наверняка погрузка и выгрузка, и первая из них датой договора не
        # является.
        point_dates = {str(row.get("date") or "") for row in points}
        dates = _DATE_RE.findall(raw)
        if len(dates) == 1:
            converted = parse_date_iso(dates[0])
            if converted and converted not in point_dates:
                contract["date"] = converted
                sources["date"] = dates[0]


def _add_contract_items(result: ParsedFill, contract: Dict[str, Any],
                        sources: Dict[str, str]) -> None:
    """Строки превью по полям договора — в порядке формы."""
    for name in ("number", "date", "route", "price_with_vat", "vat_rate",
                 "payment_days"):
        if name not in contract:
            continue
        result.items.append(ParsedItem(
            section=SECTION_CONTRACT,
            field=name,
            label=CONTRACT_FIELD_TITLES.get(name, name),
            value=contract[name],
            source=sources.get(name, ""),
        ))


def _parse_free_line(line: str, vehicles: List[Dict[str, Any]],
                     contract: Dict[str, Any], sources: Dict[str, str],
                     state: Dict[str, Any],
                     unresolved: List[str]) -> None:
    """
    Строка без ключевого слова и без действующего раздела (A.2).

    Порядок проверок — от самого надёжного признака к слабому: VIN →
    сумма → адрес → дата → «не понял».
    """
    text = str(line or "").strip()
    if not text:
        return
    if _looks_like_table_header(text):
        return

    # Строка, в которой несколько VIN, — это перечень машин и ничего больше:
    # ни дат, ни сумм в ней искать не нужно (вставка из Excel).
    if _count_vins(text) > 1:
        _add_vehicles_from_line(text, vehicles)
        return

    if _add_vehicles_from_line(text, vehicles):
        return

    if "price_with_vat" not in contract:
        number = parse_price(text)
        if number is not None and number >= _MONEY_MIN:
            contract["price_with_vat"] = number
            sources["price_with_vat"] = text
            return

    if _looks_like_address(text):
        state["free_points"].append(text)
        return

    match = _DATE_RE.search(text)
    if match and "date" not in contract:
        converted = parse_date_iso(match.group(1))
        if converted:
            contract["date"] = converted
            sources["date"] = text
            return

    if _SENSITIVE_RE.search(text):
        unresolved.append(f"{text} (личные данные — заполните вручную)")
        return

    unresolved.append(text)


def _as_list(value: Any) -> List[Any]:
    """Список из значения любого вида (строка JSON — тоже список)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                return []
            return list(parsed) if isinstance(parsed, list) else []
        return [text] if text else []
    return []


def _dedupe(values: Sequence[str]) -> List[str]:
    """Список без повторов, с сохранением порядка."""
    seen: set = set()
    result: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        result.append(text)
    return result


__all__ = [
    "CONTRACT_FIELD_TITLES",
    "DADATA_UNAVAILABLE_NOTE",
    "IGNORED_KEYS",
    "KEYWORD_FIELDS",
    "ORGANIZATION_FIELDS",
    "ORGANIZATION_FIELD_TITLES",
    "SECTION_CARRIER",
    "SECTION_CONTRACT",
    "SECTION_CUSTOMER",
    "SECTION_LOADING",
    "SECTION_TABS",
    "SECTION_TITLES",
    "SECTION_UNLOADING",
    "SECTION_VEHICLE",
    "VEHICLE_FIELD_TITLES",
    "ParsedFill",
    "ParsedItem",
    "clean_address",
    "display_value",
    "normalize_vat_rate",
    "normalize_vehicles",
    "normalize_vin",
    "parse_date_iso",
    "parse_int",
    "parse_price",
    "parse_text",
    "resolve_organization",
    "vin_is_valid",
]
