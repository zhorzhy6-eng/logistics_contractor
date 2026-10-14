#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Аналитика по базе: сводки, сравнения периодов, топы, проверка качества данных.

Задание, части 3 (аналитика) и 4 (проактивность). Модуль отвечает на вопросы
вида «средняя сумма с Ромашкой за 3 месяца», «сравни октябрь с сентябрём»,
«топ-5 перевозчиков за октябрь», «сколько водителей с истекающим ВУ»,
«что не заполнено и где дубликаты».

Почему отдельный модуль
-----------------------
В `contracts` и `drivers` сотни тысяч строк, а агенту нужны ГОТОВЫЕ ЧИСЛА, а
не сырые выборки: тянуть в модель десять тысяч договоров незачем. Здесь
живут только агрегаты (SUM/AVG/MAX/MIN/COUNT) и короткие списки-выжимки.

Правила, которые здесь соблюдаются:

  * **никакого SQL от модели.** Имена таблиц, колонок, операций и фильтров
    берутся ИЗ БЕЛЫХ СПИСКОВ этого файла. Всё, что не описано ниже,
    отклоняется с понятной ошибкой — подстановки чужого текста в запрос
    невозможны. Это же закрывает SQL-инъекцию: значения уходят параметрами
    (`?`), а имена — только из словарей;
  * **периоды считаются в Python**, а не в SQL: в базе даты лежат и как ISO,
    и как «01.01.2001», поэтому границы приводятся к ISO и сравниваются
    через `date(...)` (SQLite понимает ISO, а кривую дату просто пропускает);
  * **соединение всегда закрывается** — как в остальных модулях db/crud.

Разделы белого списка:

  ``ENTITIES``      — что считаем (договоры, перевозчики, водители, ТС, точки);
  ``FIELD_CATALOG`` — поле: колонка, тип (число / дата / текст), подпись;
  ``OPERATIONS``    — sum / avg / max / min / count и их русские синонимы.
"""

import logging
import sqlite3
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from db.connection import get_connection

logger = logging.getLogger("db.crud.analytics")

#: Сколько строк максимум возвращает «выжимка» (примеры и топы).
MAX_ROWS = 100


# ─────────────────────────────────────────────────────────────
# Белый список полей
# ─────────────────────────────────────────────────────────────
#: Типы полей: как приводить значение, когда оно пришло из базы.
TYPE_NUMBER = "number"
TYPE_DATE = "date"
TYPE_TEXT = "text"

#: Описание одного поля: колонка в SQL, тип, подпись для ответа.
FieldSpec = Dict[str, str]


def _field(column: str, kind: str, title: str, **extra: str) -> FieldSpec:
    """Одна запись каталога полей (короткая форма для читаемости таблицы)."""
    spec: FieldSpec = {"column": column, "type": kind, "title": title}
    spec.update(extra)
    return spec


#: Каталог полей по ключам, которые видит модель.
#:
#: Ключ — короткое имя без префикса таблицы (`price_with_vat`, а не
#: `c.price_with_vat`): модель не должна знать про алиасы SQL. Колонка при
#: необходимости пишется полной (`c.price_with_vat`).
FIELD_CATALOG: Dict[str, FieldSpec] = {
    # ── Договоры ──
    "amount": _field("COALESCE(c.price_with_vat, 0)", TYPE_NUMBER,
                     "сумма договора с НДС"),
    "amount_without_vat": _field("COALESCE(c.price_without_vat, 0)", TYPE_NUMBER,
                                 "сумма без НДС"),
    "prepayment": _field("COALESCE(c.prepayment_amount, 0)", TYPE_NUMBER,
                         "предоплата"),
    "contract_date": _field("c.contract_date", TYPE_DATE, "дата договора"),
    "start_date": _field("c.start_date", TYPE_DATE, "начало работ"),
    "end_date": _field("c.end_date", TYPE_DATE, "окончание работ"),
    "number": _field("c.contract_number", TYPE_TEXT, "номер договора"),
    "route": _field("c.route", TYPE_TEXT, "маршрут"),
    "currency": _field("c.currency", TYPE_TEXT, "валюта"),
    # ── Водители ──
    "license_expiry": _field("d.license_expiry_date", TYPE_DATE,
                             "срок действия водительского удостоверения"),
    "license_issue": _field("d.license_issue_date", TYPE_DATE,
                            "дата выдачи водительского удостоверения"),
    "birth_date": _field("d.birth_date", TYPE_DATE, "дата рождения водителя"),
    "driver_name": _field("d.full_name", TYPE_TEXT, "ФИО водителя"),
    "phone": _field("d.phone", TYPE_TEXT, "телефон"),
    "passport": _field("d.passport_number", TYPE_TEXT, "номер паспорта"),
    "carrier_id": _field("d.default_carrier_id", TYPE_NUMBER,
                         "номер перевозчика водителя"),
    # ── Организации (перевозчики и заказчики) ──
    "inn": _field("ca.inn", TYPE_TEXT, "ИНН"),
    "kpp": _field("ca.kpp", TYPE_TEXT, "КПП"),
    "ogrn": _field("ca.ogrn", TYPE_TEXT, "ОГРН"),
    "org_name": _field("ca.full_name", TYPE_TEXT, "наименование организации"),
    "director": _field("ca.director_name", TYPE_TEXT, "руководитель"),
    "legal_address": _field("ca.legal_address", TYPE_TEXT, "юридический адрес"),
    "bank_account": _field("ca.bank_account", TYPE_TEXT, "расчётный счёт"),
    "bik": _field("ca.bik", TYPE_TEXT, "БИК"),
    # ── Транспорт ──
    "vin": _field("v.vin", TYPE_TEXT, "VIN"),
    "brand_model": _field("v.brand_model", TYPE_TEXT, "марка и модель"),
    "plate_number": _field("v.plate_number", TYPE_TEXT, "госномер"),
    "year": _field("COALESCE(v.year, 0)", TYPE_NUMBER, "год выпуска"),
    "vehicle_type": _field("v.vehicle_type", TYPE_TEXT, "тип ТС"),
    # ── Точки маршрута ──
    "point_type": _field("cp.point_type", TYPE_TEXT, "вид точки"),
    "point_name": _field("cp.name", TYPE_TEXT, "наименование салона"),
    "address": _field("cp.address", TYPE_TEXT, "адрес точки"),
    "point_date": _field("cp.date", TYPE_DATE, "дата точки"),
    "time_window": _field("cp.time_window", TYPE_TEXT, "интервал времени"),
    "sort_order": _field("COALESCE(cp.sort_order, 0)", TYPE_NUMBER, "порядок точки"),
}


# ─────────────────────────────────────────────────────────────
# Белый список сущностей
# ─────────────────────────────────────────────────────────────
#: Сущность: откуда считать, по какому полю считать «сколько», как называть
#: одну запись, какие поля доступны и по каким полям искать повторы.
class Entity:
    """Описание одной сущности аналитики (таблица + разрешённые поля)."""

    def __init__(
        self,
        key: str,
        title: str,
        one: str,
        source: str,
        id_column: str,
        label_expression: str,
        fields: Sequence[str],
        *,
        name_column: str = "",
        soft_delete: str = "",
        date_column: str = "",
        duplicate_by: Sequence[str] = (),
    ):
        """
        :param date_column: КЛЮЧ каталога с датой сущности («когда это
            было»): у договоров — дата договора, у водителей — срок ВУ,
            у точек — дата точки. По нему считается период, если модель
            не назвала поле-дату сама. Пусто — даты у сущности нет.
        """
        self.key = key
        self.title = title
        self.one = one
        self.source = source
        self.id_column = id_column
        self.label_expression = label_expression
        self.fields = tuple(fields)
        self.name_column = name_column
        self.soft_delete = soft_delete
        self.date_column = date_column
        self.duplicate_by = tuple(duplicate_by)

    def field(self, key: str) -> Optional[FieldSpec]:
        """Описание поля, если оно разрешено у этой сущности."""
        name = str(key or "").strip()
        if name not in self.fields:
            return None
        return FIELD_CATALOG.get(name)


#: Алиасы таблиц: короткие имена для формул в FIELD_CATALOG.
_ALIAS = {
    "contracts": ("contracts c", "c.id"),
    "drivers": ("drivers d", "d.id"),
    "carriers": ("carriers ca", "ca.id"),
    "customers": ("customers ca", "ca.id"),
    "vehicles": ("vehicles v", "v.id"),
    "contract_points": ("contract_points cp", "cp.id"),
}

#: Сущности аналитики. Порядок — от самой частой к редкой.
ENTITIES: Dict[str, Entity] = {
    "contracts": Entity(
        key="contracts",
        title="договоры",
        one="договор",
        source=_ALIAS["contracts"][0],
        id_column=_ALIAS["contracts"][1],
        label_expression=(
            "COALESCE(NULLIF(TRIM(c.contract_number), ''), '№' || c.id)"
        ),
        fields=("amount", "amount_without_vat", "prepayment", "contract_date",
                "start_date", "end_date", "number", "route", "currency"),
        name_column="c.contract_number",
        date_column="contract_date",
        duplicate_by=("number",),
    ),
    "carriers": Entity(
        key="carriers",
        title="перевозчики",
        one="перевозчик",
        source=_ALIAS["carriers"][0],
        id_column=_ALIAS["carriers"][1],
        label_expression="COALESCE(NULLIF(TRIM(ca.full_name), ''), ca.short_name)",
        fields=("inn", "kpp", "ogrn", "org_name", "director", "legal_address",
                "bank_account", "bik"),
        name_column="ca.full_name",
        soft_delete="ca.is_deleted = 0",
        duplicate_by=("inn",),
    ),
    "customers": Entity(
        key="customers",
        title="заказчики",
        one="заказчик",
        source=_ALIAS["customers"][0],
        id_column=_ALIAS["customers"][1],
        label_expression="COALESCE(NULLIF(TRIM(ca.full_name), ''), ca.short_name)",
        fields=("inn", "kpp", "ogrn", "org_name", "director", "legal_address",
                "bank_account", "bik"),
        name_column="ca.full_name",
        soft_delete="ca.is_deleted = 0",
        duplicate_by=("inn",),
    ),
    "drivers": Entity(
        key="drivers",
        title="водители",
        one="водитель",
        source=_ALIAS["drivers"][0],
        id_column=_ALIAS["drivers"][1],
        label_expression="d.full_name",
        fields=("driver_name", "license_expiry", "license_issue", "birth_date",
                "phone", "passport", "carrier_id"),
        name_column="d.full_name",
        soft_delete="d.is_deleted = 0",
        date_column="license_expiry",
        duplicate_by=("passport",),
    ),
    "vehicles": Entity(
        key="vehicles",
        title="перевозимые ТС",
        one="машина",
        source=_ALIAS["vehicles"][0],
        id_column=_ALIAS["vehicles"][1],
        label_expression=(
            "COALESCE(NULLIF(TRIM(v.brand_model), ''), v.vin, 'машина')"
        ),
        fields=("vin", "brand_model", "plate_number", "year", "vehicle_type"),
        name_column="v.vin",
        duplicate_by=("vin",),
    ),
    "points": Entity(
        key="points",
        title="точки маршрута",
        one="точка",
        source=_ALIAS["contract_points"][0],
        id_column=_ALIAS["contract_points"][1],
        label_expression=(
            "COALESCE(NULLIF(TRIM(cp.name), ''), NULLIF(TRIM(cp.address), ''), "
            "'точка')"
        ),
        fields=("point_type", "point_name", "address", "point_date",
                "time_window", "sort_order"),
        name_column="cp.address",
        date_column="point_date",
        duplicate_by=("address",),
    ),
}

#: Русские названия сущностей: модель может спросить «перевозчики».
ENTITY_ALIASES: Dict[str, str] = {
    "договор": "contracts", "договоры": "contracts", "contract": "contracts",
    "перевозчик": "carriers", "перевозчики": "carriers",
    "carrier": "carriers", "carriers": "carriers",
    "заказчик": "customers", "заказчики": "customers",
    "customer": "customers", "customers": "customers",
    "водитель": "drivers", "водители": "drivers", "driver": "drivers",
    "drivers": "drivers",
    "машина": "vehicles", "машины": "vehicles", "тс": "vehicles",
    "транспорт": "vehicles", "vehicle": "vehicles", "vehicles": "vehicles",
    "точка": "points", "точки": "points", "маршрут": "points",
    "point": "points", "points": "points",
}


def resolve_entity(entity_type: str) -> Optional[Entity]:
    """Сущность по ключу или русскому названию (None — такой нет)."""
    key = str(entity_type or "").strip().casefold()
    if not key:
        return None
    if key in ENTITIES:
        return ENTITIES[key]
    return ENTITIES.get(ENTITY_ALIASES.get(key, ""))


def entity_keys() -> Tuple[str, ...]:
    """Ключи сущностей — для подсказки в ошибке и для промпта."""
    return tuple(ENTITIES)


def fields_of(entity_type: str) -> Tuple[str, ...]:
    """Разрешённые поля сущности (пусто — сущности нет)."""
    entity = resolve_entity(entity_type)
    return entity.fields if entity else ()


# ─────────────────────────────────────────────────────────────
# Белый список операций
# ─────────────────────────────────────────────────────────────
#: Операция → SQL-функция. Считаются только эти пять агрегатов.
OPERATIONS: Dict[str, str] = {
    "sum": "SUM",
    "avg": "AVG",
    "max": "MAX",
    "min": "MIN",
    "count": "COUNT",
}

#: Русские и разговорные синонимы операций («средняя», «всего», «самый»).
OPERATION_ALIASES: Dict[str, str] = {
    "сумма": "sum", "сумму": "sum", "итого": "sum", "всего": "sum",
    "общая": "sum", "total": "sum",
    "средняя": "avg", "средний": "avg", "среднее": "avg", "в среднем": "avg",
    "average": "avg", "mean": "avg",
    "максимум": "max", "максимальная": "max", "максимальный": "max",
    "самая": "max", "самый": "max", "наибольшая": "max",
    "минимум": "min", "минимальная": "min", "минимальный": "min",
    "наименьшая": "min",
    "количество": "count", "сколько": "count", "число": "count",
    "count": "count",
}


def resolve_operation(operation: str) -> Optional[str]:
    """Операция по имени или русскому синониму (None — неизвестная)."""
    key = str(operation or "").strip().casefold()
    if not key:
        return None
    if key in OPERATIONS:
        return key
    return OPERATION_ALIASES.get(key)


def operation_keys() -> Tuple[str, ...]:
    """Ключи операций (для подсказки в ошибке)."""
    return tuple(OPERATIONS)


def _operation_title(operation: str) -> str:
    """Подпись операции для ответа оператору."""
    return {
        "sum": "сумма",
        "avg": "среднее",
        "max": "максимум",
        "min": "минимум",
        "count": "количество",
    }.get(operation, operation)


# ─────────────────────────────────────────────────────────────
# Периоды
# ─────────────────────────────────────────────────────────────

def _today() -> date:
    """Сегодняшняя дата (отдельная функция — её подменяют тесты)."""
    return date.today()


def _iso(value: Any) -> str:
    """Дата в ISO (пусто — не разобрали)."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        from core.dates import to_iso
        return to_iso(text) or ""
    except Exception:  # noqa: BLE001 — парсер дат не должен ломать аналитику
        return ""


def _as_date(value: Any) -> Optional[date]:
    """Дата из значения любого вида (None — не разобрали)."""
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        from core.dates import parse_date
        parsed = parse_date(text)
        if isinstance(parsed, date):
            return parsed
    except Exception:  # noqa: BLE001
        pass
    for candidate in (text[:10], text):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None


#: Русские названия месяцев (в родительном падеже — как их пишет оператор:
#: «сравни октябрь с сентябрём», «за январь»).
MONTHS: Dict[str, int] = {
    "январь": 1, "января": 1, "январе": 1, "january": 1,
    "февраль": 2, "февраля": 2, "феврале": 2, "february": 2,
    "март": 3, "марта": 3, "марте": 3, "march": 3,
    "апрель": 4, "апреля": 4, "апреле": 4, "april": 4,
    "май": 5, "мая": 5, "мае": 5, "may": 5,
    "июнь": 6, "июня": 6, "июне": 6, "june": 6,
    "июль": 7, "июля": 7, "июле": 7, "july": 7,
    "август": 8, "августа": 8, "августе": 8, "august": 8,
    "сентябрь": 9, "сентября": 9, "сентябре": 9, "сентябрём": 9,
    "сентябрем": 9, "september": 9,
    "октябрь": 10, "октября": 10, "октябре": 10, "октябрём": 10,
    "октябрем": 10, "october": 10,
    "ноябрь": 11, "ноября": 11, "ноябре": 11, "ноябрём": 11,
    "ноябрем": 11, "november": 11,
    "декабрь": 12, "декабря": 12, "декабре": 12, "декабрём": 12,
    "декабрем": 12, "december": 12,
}

#: Названия месяцев в именительном падеже — для подписи периода в ответе.
MONTH_TITLES = (
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)

#: Относительные периоды, которые понимает аналитика без разбора дат.
RELATIVE_PERIODS: Dict[str, int] = {
    "месяц": 30, "месяца": 30, "месяцев": 30, "month": 30, "months": 30,
    "квартал": 91, "квартала": 91, "кварталов": 91,
    "quarter": 91, "quarters": 91,
    "год": 365, "года": 365, "лет": 365, "year": 365, "years": 365,
    "неделя": 7, "недели": 7, "недель": 7, "week": 7, "weeks": 7,
    "полгода": 182,
}


class Period:
    """Разобранный период: границы в ISO (пусто — границы нет)."""

    def __init__(self, key: str, title: str, start: str = "", end: str = "",
                 date_field: str = ""):
        self.key = key
        self.title = title
        self.start = start
        self.end = end
        self.date_field = date_field

    def empty(self) -> bool:
        """Период не задан — ограничения по датам нет."""
        return not self.start and not self.end

    def as_dict(self) -> Dict[str, Any]:
        """Период для ответа инструмента."""
        return {
            "key": self.key,
            "title": self.title,
            "start": self.start,
            "end": self.end,
        }


def resolve_period(period: str = "", *, today: Optional[date] = None,
                   date_field: str = "") -> Tuple[Optional[Period], str]:
    """
    Разбирает период из человеческой записи.

    Понимает:

      * пусто / «всё» / «all»   — без ограничения по датам;
      * «октябрь», «за октябрь», «октябрь 2026», «10.2026», «2026-10»;
      * «3 месяца», «за 3 месяца», «90 дней», «последний месяц»;
      * «2026», «2026 год», «этот год», «прошлый год», «этот месяц»;
      * «с 01.09.2026 по 30.09.2026», «сентябрь — октябрь» (два месяца).

    :param period: запись периода (может быть пустой);
    :param today: «сегодня» (нужно тестам);
    :param date_field: поле-дата сущности: подставляется в результат, если
        период относительный и без явных границ (договоры — дата договора).
    :return: пара «период, текст ошибки» (ошибка — период не разобран).
    """
    # «Сегодня» спрашивается у модуля КАЖДЫЙ раз, а не берётся значением по
    # умолчанию: иначе подмена _today (тесты, «сегодня» из настроек) не
    # дошла бы до разбора периода — значение по умолчанию вычисляется при
    # определении функции и навсегда держит исходную дату.
    base = today if today is not None else _today()
    text = " ".join(str(period or "").strip().casefold().split())
    if not text or text in ("всё", "все", "all", "весь", "любой"):
        return Period("all", "за всё время", date_field=date_field), ""

    # ── Явный диапазон: «с 01.09.2026 по 30.09.2026» ──
    if " - " in text or " — " in text or "-" in text or "—" in text:
        parts = [part.strip() for part in _split_range(text)]
        if len(parts) == 2 and all(parts):
            left = _month_or_date(parts[0], base)
            right = _month_or_date(parts[1], base)
            if left and right:
                start, end, title = _span(left, right)
                return Period(f"{start}..{end}", title, start, end,
                              date_field=date_field), ""

    # ── «с <дата> по <дата>» / «с <месяц> по <месяц>» ──
    if text.startswith("с ") and (" по " in text):
        left_text, _, right_text = text[2:].partition(" по ")
        left = _month_or_date(left_text.strip(), base)
        right = _month_or_date(right_text.strip(), base)
        if left and right:
            start, end, title = _span(left, right)
            return Period(f"{start}..{end}", title, start, end,
                          date_field=date_field), ""

    # ── «N месяцев / дней / лет» ──
    amount, unit = _amount_and_unit(text)
    if amount is not None and unit is not None:
        days = _unit_days(unit)
        if days is None:
            return None, f"не понимаю единицу периода «{unit}»"
        # Окно «N месяцев» — ровно N единиц назад от сегодняшнего дня:
        # «3 месяца» в середине октября начинается в середине июля.
        start = base - timedelta(days=days * amount)
        return Period(
            f"last_{amount}_{unit}",
            f"за последние {amount} {_unit_title(unit, amount)}",
            start.isoformat(), base.isoformat(), date_field=date_field,
        ), ""

    # ── «последний месяц», «прошлый квартал» ──
    for prefix, shift in (("последний ", 1), ("последние ", 1),
                          ("прошлый ", 2), ("прошлая ", 2), ("прошлое ", 2),
                          ("текущий ", 3), ("текущая ", 3), ("этот ", 3),
                          ("эта ", 3), ("это ", 3)):
        if text.startswith(prefix):
            unit = text[len(prefix):].strip()
            if unit in RELATIVE_PERIODS:
                if shift == 3:      # «этот месяц» — с начала месяца
                    start = base.replace(day=1)
                    return Period("current", _period_title(unit, base),
                                  start.isoformat(), base.isoformat(),
                                  date_field=date_field), ""
                days = RELATIVE_PERIODS[unit]
                if shift == 2:      # «прошлый месяц» — предыдущий отрезок
                    end = base - timedelta(days=days)
                    start = end - timedelta(days=days - 1)
                else:
                    start = base - timedelta(days=days - 1)
                    end = base
                return Period("previous" if shift == 2 else "last",
                              _period_title(unit, base),
                              start.isoformat(), end.isoformat(),
                              date_field=date_field), ""

    # ── «октябрь», «октябрь 2026» ──
    month_day = _month_range(text, base)
    if month_day:
        start, end, title = month_day
        return Period(f"{start}..{end}", title, start, end,
                      date_field=date_field), ""

    # ── «2026», «2026 год» ──
    year = _year_range(text)
    if year:
        start, end, title = year
        return Period(f"{start}..{end}", title, start, end,
                      date_field=date_field), ""

    # ── «01.10.2026» — один день ──
    single = _as_date(text)
    if single:
        iso = single.isoformat()
        return Period(iso, f"за {iso}", iso, iso, date_field=date_field), ""

    return None, (
        f"период «{period}» не разобран. Можно так: «октябрь», «октябрь 2026», "
        f"«3 месяца», «2026», «с 01.09.2026 по 30.09.2026»"
    )


def _split_range(text: str) -> List[str]:
    """Делит запись диапазона на две части («сентябрь — октябрь»)."""
    for separator in (" — ", " – ", " - ", "—", "–", " -", "- "):
        if separator in text:
            return text.split(separator, 1)
    return []


def _amount_and_unit(text: str) -> Tuple[Optional[int], Optional[str]]:
    """«3 месяца» → (3, «месяца»); (None, None) — не похоже на «N единиц»."""
    parts = text.replace("за ", " ", 1).split()
    if len(parts) < 2:
        return None, None
    try:
        amount = int(parts[0])
    except ValueError:
        return None, None
    if amount <= 0:
        return None, None
    return amount, parts[1]


def _unit_days(unit: str) -> Optional[int]:
    """Длительность единицы периода в днях (None — не знаем такую)."""
    return RELATIVE_PERIODS.get(unit)


def _unit_title(unit: str, amount: int) -> str:
    """Подпись единицы периода с правильным числом («3 месяца»)."""
    titles = {
        "месяц": ("месяц", "месяца", "месяцев"),
        "месяца": ("месяц", "месяца", "месяцев"),
        "месяцев": ("месяц", "месяца", "месяцев"),
        "month": ("месяц", "месяца", "месяцев"),
        "months": ("месяц", "месяца", "месяцев"),
        "неделя": ("неделю", "недели", "недель"),
        "недели": ("неделю", "недели", "недель"),
        "недель": ("неделю", "недели", "недель"),
        "день": ("день", "дня", "дней"),
        "дня": ("день", "дня", "дней"),
        "дней": ("день", "дня", "дней"),
        "day": ("день", "дня", "дней"),
        "days": ("день", "дня", "дней"),
        "год": ("год", "года", "лет"),
        "года": ("год", "года", "лет"),
        "лет": ("год", "года", "лет"),
        "year": ("год", "года", "лет"),
        "years": ("год", "года", "лет"),
    }
    forms = titles.get(unit)
    if not forms:
        return unit
    index = 2 if 11 <= amount % 100 <= 14 else (0 if amount % 10 == 1
                                                else (1 if 2 <= amount % 10 <= 4
                                                      else 2))
    return forms[index]


def _period_title(unit: str, base: date) -> str:
    """Подпись относительного периода («последний месяц»)."""
    return f"последний {_unit_title(unit, 1)}"


def _month_or_date(text: str, base: date) -> Optional[date]:
    """Первое число месяца или дата из записи."""
    months = _month_range(text, base)
    if months:
        return date.fromisoformat(months[0])
    return _as_date(text)


def _span(left: date, right: date) -> Tuple[str, str, str]:
    """Границы диапазона: меньшая дата — начало, большая — конец."""
    if left > right:
        left, right = right, left
    start = left.replace(day=1).isoformat()
    if right.day == 1:
        # Конец — первое число месяца: берём последний день этого месяца.
        end = (right.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = (end - timedelta(days=1)).isoformat()
    else:
        end = right.isoformat()
    return start, end, f"с {start} по {end}"


def _month_range(text: str, base: date) -> Optional[Tuple[str, str, str]]:
    """Диапазон месяца: «октябрь» → весь октябрь; None — не месяц."""
    words = [word.strip(" .,") for word in text.split() if word.strip(" .,")]
    month: Optional[int] = None
    year: Optional[int] = None
    for word in words:
        if word in MONTHS:
            month = MONTHS[word]
        elif word.isdigit() and len(word) == 4:
            year = int(word)
    if month is None:
        # Числовая запись «10.2026» или «2026-10».
        for word in words:
            parts = word.replace("/", ".").split(".")
            if len(parts) == 2 and all(part.isdigit() for part in parts):
                first, second = int(parts[0]), int(parts[1])
                if 1 <= first <= 12 and second > 12:
                    month, year = first, second
                elif 1 <= second <= 12 and first > 12:
                    month, year = second, first
    if month is None:
        return None

    if year is None:
        # Месяц без года — это месяц ТЕКУЩЕГО года, даже если он ещё не
        # начался: оператор в октябре, говоря «ноябрь», имеет в виду
        # ноябрь этого года (обычно речь о плане или о прошлогоднем
        # договоре, у которого год указан явно — «ноябрь 2025»).
        year = base.year

    start = date(year, month, 1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    end = end - timedelta(days=1)
    title = f"{MONTH_TITLES[month]} {year}"
    return start.isoformat(), end.isoformat(), title


def _year_range(text: str) -> Optional[Tuple[str, str, str]]:
    """Диапазон года: «2026» или «2026 год»."""
    for word in text.split():
        if word.isdigit() and len(word) == 4:
            year = int(word)
            if 1990 <= year <= 2100:
                return (f"{year}-01-01", f"{year}-12-31", f"{year} год")
    return None


# ─────────────────────────────────────────────────────────────
# Условия отбора
# ─────────────────────────────────────────────────────────────

#: Операторы фильтров → фрагмент SQL.
FILTER_OPERATORS: Dict[str, str] = {
    "eq": "= ?",
    "ne": "<> ?",
    "gt": "> ?",
    "gte": ">= ?",
    "lt": "< ?",
    "lte": "<= ?",
    "like": "LIKE ?",
}

#: Русские синонимы операторов.
FILTER_ALIASES: Dict[str, str] = {
    "=": "eq", "==": "eq", "равно": "eq", "is": "eq",
    "!=": "ne", "<>": "ne", "не": "ne", "not": "ne",
    ">": "gt", "больше": "gt",
    ">=": "gte", "не меньше": "gte",
    "<": "lt", "меньше": "lt",
    "<=": "lte", "не больше": "lte",
    "содержит": "like", "похоже": "like", "contains": "like",
}


class FilterError(ValueError):
    """Фильтр описан неверно (неизвестное поле, оператор или тип значения)."""


def _condition(
    entity: Entity,
    field: str = "",
    value: Any = None,
    operator: str = "eq",
    *,
    empty: Optional[bool] = None,
    search: str = "",
) -> Tuple[str, List[Any]]:
    """
    Условие WHERE по одному фильтру (фрагмент SQL и параметры).

    Все имена — из белого списка сущности; значения уходят параметрами.
    """
    spec = entity.field(field) if field else None
    if field and spec is None:
        raise FilterError(
            f"поле «{field}» для «{entity.title}» не поддерживается. "
            f"Доступны: {', '.join(entity.fields)}"
        )

    column = spec["column"] if spec else entity.name_column
    kind = spec["type"] if spec else TYPE_TEXT

    if empty is not None:
        # «Пусто» — NULL, пустая строка и строка из пробелов: в базе
        # встречаются все три вида, и оператору они одинаково «не заполнено».
        is_empty = "COALESCE(TRIM(CAST({col} AS TEXT)), '') = ''".format(col=column)
        return (is_empty, []) if empty else (f"NOT ({is_empty})", [])

    text = str(search or "").strip()
    if text:
        return f"COALESCE(CAST({column} AS TEXT), '') LIKE ?", [f"%{text}%"]

    if value is None:
        raise FilterError(f"для поля «{field}» не указано значение")

    op = FILTER_ALIASES.get(str(operator or "").strip().casefold(),
                            str(operator or "").strip().casefold())
    if op not in FILTER_OPERATORS:
        raise FilterError(
            f"оператор «{operator}» не поддерживается. "
            f"Доступны: {', '.join(sorted(FILTER_OPERATORS))}"
        )

    if kind == TYPE_DATE:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # Дата, заданная числом: 20261015 или 2026.
            number = str(int(value))
            iso = (f"{number[:4]}-{number[4:6]}-{number[6:8]}"
                   if len(number) == 8 else "")
            if not iso:
                raise FilterError(f"дата «{value}» не разобрана")
        else:
            iso = _iso(value)
            if not iso:
                raise FilterError(f"дата «{value}» не разобрана")
        # Сравниваем приведённые даты: в базе встречаются обе записи.
        return (f"date({column}) {FILTER_OPERATORS[op]}", [iso])

    if kind == TYPE_NUMBER:
        try:
            number = float(str(value).replace(" ", "").replace(",", "."))
        except (TypeError, ValueError):
            raise FilterError(f"значение «{value}» не число")
        return f"COALESCE({column}, 0) {FILTER_OPERATORS[op]}", [number]

    if op == "like":
        return f"COALESCE(CAST({column} AS TEXT), '') LIKE ?", [f"%{value}%"]
    return f"COALESCE(CAST({column} AS TEXT), '') {FILTER_OPERATORS[op]}", [str(value)]


#: Готовые именованные фильтры — то, что чаще всего спрашивают словами.
NAMED_FILTERS: Dict[str, Dict[str, Any]] = {
    "expiring_soon": {"days": 30},
    "empty_inn": {"field": "inn", "empty": True},
    "empty_vin": {"field": "vin", "empty": True},
    "empty_phone": {"field": "phone", "empty": True},
    "empty_passport": {"field": "passport", "empty": True},
    "expired_license": {"field": "license_expiry", "days": -1},
}


def build_conditions(
    entity: Entity,
    filters: Optional[Sequence[Any]] = None,
    *,
    period: Optional[Period] = None,
    counterparty: str = "",
    today: Optional[date] = None,
) -> Tuple[List[str], List[Any]]:
    """
    Собирает условия WHERE из фильтров, периода и контрагента.

    Фильтр — либо словарь (``{"field": …, "value": …, "operator": …,
    "empty": …}``), либо строка с именем готового фильтра
    (``NAMED_FILTERS``). Неизвестный фильтр — ошибка с подсказкой.
    """
    base = today or _today()
    where: List[str] = []
    params: List[Any] = []

    if entity.soft_delete:
        where.append(entity.soft_delete)

    if period is not None and not period.empty():
        column = _period_column(entity, period)
        where.append("date({column}) BETWEEN date(?) AND date(?)".format(
            column=column))
        params.extend([period.start, period.end])

    if counterparty:
        column = _counterparty_column(entity)
        if column is None:
            raise FilterError(
                f"отбор по контрагенту для «{entity.title}» не поддерживается"
            )
        where.append(f"COALESCE(CAST({column} AS TEXT), '') LIKE ?")
        params.append(f"%{str(counterparty).strip()}%")

    for raw in filters or ():
        if isinstance(raw, str):
            key = raw.strip()
            if key not in NAMED_FILTERS:
                raise FilterError(
                    f"готового фильтра «{raw}» нет. Доступны: "
                    f"{', '.join(sorted(NAMED_FILTERS))}"
                )
            raw = dict(NAMED_FILTERS[key])

        if not isinstance(raw, dict):
            raise FilterError(f"фильтр {raw!r} описан неверно")

        # «Истекает через N дней» — отдельный вид фильтра по дате.
        if _is_days_filter(entity, raw):
            spec = entity.field(str(raw.get("field") or ""))
            if spec is None:
                raise FilterError(
                    f"фильтр по сроку требует поле из списка: "
                    f"{', '.join(ENTITY_DATE_FIELDS.get(entity.key, ())) or '—'}"
                )
            days = int(raw["days"])
            if days < 0:
                where.append(f"date({spec['column']}) < date(?)")
                params.append(base.isoformat())
            else:
                where.append(
                    f"date({spec['column']}) BETWEEN date(?) AND date(?)"
                )
                params.extend([
                    base.isoformat(),
                    (base + timedelta(days=days)).isoformat(),
                ])
            where.append(
                "COALESCE(TRIM(CAST({column} AS TEXT)), '') <> ''".format(
                    column=spec["column"])
            )
            continue

        fragment, values = _condition(
            entity,
            field=str(raw.get("field") or ""),
            value=raw.get("value"),
            operator=str(raw.get("operator") or "eq"),
            empty=raw.get("empty"),
            search=str(raw.get("search") or raw.get("contains") or ""),
        )
        where.append(fragment)
        params.extend(values)

    return where, params


#: Поля-даты по сущностям: только по ним работает фильтр «через N дней».
ENTITY_DATE_FIELDS: Dict[str, Tuple[str, ...]] = {
    "contracts": ("end_date", "start_date", "contract_date"),
    "drivers": ("license_expiry", "license_issue", "birth_date"),
    "points": ("point_date",),
    "vehicles": (),
    "carriers": (),
    "customers": (),
}


def _period_column(entity: Entity, period: Period) -> str:
    """
    Колонка, по которой ограничивается период.

    Поле-дата приходит КЛЮЧОМ каталога (`contract_date`); если ключ не
    назван, берётся первое поле-дата сущности — у договоров это дата
    договора, у водителей срок действия ВУ, у точек дата точки.
    """
    field = _date_field(entity, period.date_field)
    if not field:
        raise FilterError(
            f"у «{entity.title}» нет даты для периода — считать за период нечего"
        )
    return entity.field(field)["column"]


def _date_field(entity: Entity, field: str = "") -> str:
    """
    Ключ поля-даты, по которому ограничивается период.

    Порядок: названное моделью поле (если оно ДЕЙСТВИТЕЛЬНО дата у этой
    сущности) → дата сущности из реестра (`Entity.date_column`: у договоров
    — дата договора, у водителей — срок ВУ) → первое поле-дата из списка.
    Названное не-дату поле игнорируется с записью в лог: иначе «за октябрь»
    молча считалось бы по другому полю.
    """
    name = str(field or "").strip()
    spec = entity.field(name) if name else None
    if spec is not None and spec["type"] == TYPE_DATE:
        return name

    own = entity.date_column
    if own and entity.field(own) is not None:
        if spec is not None:
            logger.debug(
                "Поле %r у «%s» не дата — период считается по %r",
                name, entity.title, own,
            )
        return own

    available = [item for item in ENTITY_DATE_FIELDS.get(entity.key, ())
                 if entity.field(item)]
    if spec is not None:
        logger.debug(
            "Поле %r у «%s» не дата — период считается по %r",
            name, entity.title, available[0] if available else "",
        )
    return available[0] if available else ""


#: Поле по умолчанию для сводок и топов: у договоров — сумма, у остальных
#: сущностей «суммы» нет вовсе, и по умолчанию считается количество записей.
DEFAULT_FIELDS: Dict[str, str] = {
    "contracts": "amount",
    "drivers": "driver_name",
    "carriers": "org_name",
    "customers": "org_name",
    "vehicles": "brand_model",
    "points": "point_name",
}


def _default_field(entity: Entity, operation: str, field: str) -> str:
    """
    Поле, если модель его не назвала.

    Для сводки берётся «сумма» там, где она есть (договоры), иначе — поле
    количества: операция «сколько» без поля считается как COUNT(*).
    Для операции «сколько» пустое поле — это нормальный случай.
    """
    name = str(field or "").strip()
    if name:
        return name
    if operation == "count":
        return ""
    candidate = DEFAULT_FIELDS.get(entity.key, "")
    return candidate if entity.field(candidate) else ""


def _counterparty_column(entity: Entity) -> Optional[str]:
    """Колонка для отбора по контрагенту (None — отбор невозможен)."""
    if entity.key == "contracts":
        # Названия сторон подзапросами: JOIN на три таблицы раздул бы выборку.
        return (
            "(SELECT COALESCE(NULLIF(TRIM(ca.full_name), ''), ca.short_name, '') "
            "   FROM carriers ca WHERE ca.id = c.carrier_id) "
            "|| ' ' || "
            "(SELECT COALESCE(NULLIF(TRIM(cu.full_name), ''), cu.short_name, '') "
            "   FROM customers cu WHERE cu.id = c.customer_id) "
            "|| ' ' || "
            "(SELECT COALESCE(d.full_name, '') FROM drivers d "
            "   WHERE d.id = c.driver_id)"
        )
    if entity.key in ("carriers", "customers"):
        return "ca.full_name"
    if entity.key == "drivers":
        return (
            "(SELECT COALESCE(NULLIF(TRIM(ca.full_name), ''), ca.short_name, '') "
            "   FROM carriers ca WHERE ca.id = d.default_carrier_id)"
        )
    if entity.key == "vehicles":
        return (
            "(SELECT COALESCE(NULLIF(TRIM(ca.full_name), ''), ca.short_name, '') "
            "   FROM carriers ca WHERE ca.id = v.carrier_id)"
        )
    return None


# ─────────────────────────────────────────────────────────────
# Значения из базы
# ─────────────────────────────────────────────────────────────

def _to_number(value: Any) -> Optional[float]:
    """Число из значения базы (None — не число)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _value_of(spec: FieldSpec, value: Any) -> Any:
    """
    Значение поля с приведением по типу.

    Числовые поля возвращаются числом (в базе они бывают записаны строкой с
    пробелами), даты — в ISO, текстовые — строкой без краевых пробелов.
    """
    kind = spec["type"]
    if kind == TYPE_NUMBER:
        return _to_number(value)
    if kind == TYPE_DATE:
        return _iso(value) or None
    return None if value is None else str(value).strip()


# ─────────────────────────────────────────────────────────────
# Публичные функции
# ─────────────────────────────────────────────────────────────

def aggregate(
    entity_type: str,
    field: str,
    operation: str = "sum",
    *,
    period: str = "",
    counterparty: str = "",
    filters: Optional[Sequence[Any]] = None,
    today: Optional[date] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """
    Сводка по одному полю: сумма, среднее, максимум, минимум или количество.

    Пример: «средняя сумма с Ромашкой за 3 месяца» → ``entity_type="contracts"``,
    ``field="amount"``, ``operation="avg"``, ``period="3 месяца"``,
    ``counterparty="Ромашка"``.

    :return: словарь с готовым числом, подписями и (для max/min) примерами
        записей, на которых число получено.
    """
    entity = resolve_entity(entity_type)
    if entity is None:
        return _error(
            f"считать «{entity_type}» не умею",
            available=list(entity_keys()),
        )

    op = resolve_operation(operation)
    if op is None:
        return _error(
            f"операция «{operation}» не поддерживается",
            available=list(operation_keys()),
        )

    field_key = _default_field(entity, op, field)
    if op == "count" and not field_key:
        # COUNT(*) — «сколько записей», поле не нужно.
        spec: Optional[FieldSpec] = {
            "column": entity.id_column, "type": TYPE_NUMBER,
            "title": "записей",
        }
    else:
        spec = entity.field(field_key)
        if spec is None:
            return _error(
                f"поля «{field}» у «{entity.title}» нет",
                available=list(entity.fields),
            )

    parsed, error = resolve_period(
        period, today=today,
        date_field=_default_date_field(entity, op, field_key),
    )
    if error:
        return _error(error)

    try:
        where, params = build_conditions(
            entity, filters, period=parsed, counterparty=counterparty,
            today=today,
        )
    except FilterError as e:
        return _error(str(e))

    sql_function = OPERATIONS[op]
    column = spec["column"]
    #: COUNT без поля — это «сколько записей», а не «сколько заполнено».
    count_all = op == "count" and not field_key
    expression = "COUNT(*)" if count_all else f"{sql_function}({column})"

    sample_rows: List[Dict[str, Any]] = []
    conn = get_connection()
    try:
        cursor = conn.cursor()
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        cursor.execute(
            f"SELECT {expression} AS value FROM {entity.source}{where_sql}",
            params,
        )
        row = cursor.fetchone()
        raw = row[0] if row else None

        if op in ("max", "min") and field_key:
            # Примеры записей: оператору важно видеть, ЧТО дало максимум.
            label = entity.label_expression
            cursor.execute(
                f"SELECT {label} AS label, {column} AS value, "
                f"{entity.id_column} AS record_id "
                f"FROM {entity.source}{where_sql} "
                f"ORDER BY {column} "
                f"{'DESC' if op == 'max' else 'ASC'} LIMIT ?",
                (*params, max(1, int(limit))),
            )
            sample_rows = _rows(cursor, entity, limit)
    finally:
        conn.close()

    if count_all:
        number: Any = int(raw or 0)
    elif op == "count":
        number = int(raw or 0)
    else:
        number = _to_number(raw)

    if number is None:
        return _ok(
            {
                "value": None,
                "operation": op,
                "operation_title": _operation_title(op),
                "entity": entity.key,
                "entity_title": entity.title,
                "field": field_key,
                "field_title": spec["title"] if spec else "",
                "period": parsed.as_dict(),
                "counterparty": str(counterparty or ""),
                "records": 0,
                "text": _empty_text(entity, op, spec, parsed, counterparty),
            }
        )

    if op == "count":
        number = int(number)

    return _ok(
        {
            "value": number,
            "operation": op,
            "operation_title": _operation_title(op),
            "entity": entity.key,
            "entity_title": entity.title,
            "field": field_key,
            "field_title": spec["title"] if spec else "записей",
            "period": parsed.as_dict(),
            "counterparty": str(counterparty or ""),
            "text": _aggregate_text(entity, op, spec, field_key, number,
                                    parsed, counterparty),
            "examples": sample_rows,
        }
    )


def _default_date_field(entity: Entity, operation: str, field: str) -> str:
    """
    По какому полю-дате ограничивать период, если он не назван.

    Правило простое: считаем по ДАТЕ САМОГО ПОЛЯ («сумма за октябрь» — это
    договоры, ЗАКЛЮЧЁННЫЕ в октябре), а если поле не дата — по дате
    сущности (у водителей это срок действия ВУ, у точек — дата точки).
    """
    spec = entity.field(field)
    if spec is not None and spec["type"] == TYPE_DATE:
        return field
    return _date_field(entity) or ""


def _rows(cursor: sqlite3.Cursor, entity: Entity, limit: int) -> List[Dict[str, Any]]:
    """Строки выборки словарями (не больше limit + защита от мусора)."""
    columns = [desc[0] for desc in cursor.description or ()]
    result: List[Dict[str, Any]] = []
    for row in cursor.fetchall()[: max(1, min(int(limit), MAX_ROWS))]:
        item = dict(zip(columns, row))
        item["label"] = str(item.get("label") or "").strip()
        item["value"] = _to_number(item.get("value"))
        result.append(item)
    return result


def _empty_text(entity: Entity, operation: str, spec: Optional[FieldSpec],
                period: Period, counterparty: str) -> str:
    """Текст «ничего не нашлось» для ответа оператору."""
    what = spec["title"] if spec else "записи"
    tail = f" ({period.title})" if not period.empty() else ""
    who = f" у «{counterparty}»" if counterparty else ""
    return f"{_operation_title(operation)} «{what}»{who}{tail}: данных нет"


def _aggregate_text(entity: Entity, operation: str, spec: Optional[FieldSpec],
                    field_key: str, number: Any, period: Period,
                    counterparty: str) -> str:
    """Готовая фраза с числом — её и показывает модель оператору."""
    if operation == "count" and not field_key:
        text = f"{entity.title}: {int(number)}"
    else:
        title = spec["title"] if spec else ""
        if operation == "count":
            text = f"{title}: заполнено у {int(number)} записей"
        else:
            text = f"{_operation_title(operation)} «{title}»: {_number_text(number)}"
    if counterparty:
        text += f" — контрагент «{counterparty}»"
    if not period.empty():
        text += f", {period.title}"
    return text


def _number_text(value: Any) -> str:
    """Число для человека: целые — без дробной части, деньги — с пробелами."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}".replace(",", " ") if abs(value) >= 10000 else str(value)
    number = _to_number(value)
    if number is None:
        return "—"
    if float(number).is_integer() and abs(number) < 1e15:
        number = int(number)
    if abs(number) >= 10000:
        return f"{number:,.2f}".rstrip("0").rstrip(".").replace(",", " ")
    return f"{number:.2f}".rstrip("0").rstrip(".")


def compare_periods(
    period1: str,
    period2: str,
    *,
    entity_type: str = "contracts",
    field: str = "amount",
    operation: str = "sum",
    group_by: str = "",
    counterparty: str = "",
    filters: Optional[Sequence[Any]] = None,
    today: Optional[date] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """
    Сравнение двух периодов: «сравни октябрь с сентябрём».

    Возвращает два числа, разницу (абсолютную и в процентах) и — если задан
    ``group_by`` — разбивку по контрагентам внутри каждого периода.

    :param group_by: ``counterparty`` (контрагент), ``driver`` (водитель),
        ``carrier`` (перевозчик), ``none``/пусто — без разбивки.
    """
    entity = resolve_entity(entity_type)
    if entity is None:
        return _error(f"считать «{entity_type}» не умею",
                      available=list(entity_keys()))

    op = resolve_operation(operation)
    if op is None:
        return _error(f"операция «{operation}» не поддерживается",
                      available=list(operation_keys()))

    field_key = _default_field(entity, op, field)
    if op == "count" and not field_key:
        spec: Optional[FieldSpec] = {"column": entity.id_column,
                                     "type": TYPE_NUMBER, "title": "записей"}
    else:
        spec = entity.field(field_key)
        if spec is None:
            return _error(f"поля «{field}» у «{entity.title}» нет",
                          available=list(entity.fields))

    first, error1 = resolve_period(
        period1, today=today, date_field=_period_field(entity, spec, field_key))
    if error1:
        return _error(f"первый период: {error1}")
    second, error2 = resolve_period(
        period2, today=today, date_field=_period_field(entity, spec, field_key))
    if error2:
        return _error(f"второй период: {error2}")

    group = str(group_by or "").strip().casefold()
    if group in ("", "none", "нет"):
        group = ""

    def measure(period: Period) -> Dict[str, Any]:
        """Одно число (и разбивка) по периоду."""
        try:
            where, params = build_conditions(
                entity, filters, period=period, counterparty=counterparty,
                today=today,
            )
        except FilterError as e:
            return {"error": str(e)}
        conn = get_connection()
        try:
            cursor = conn.cursor()
            where_sql = (" WHERE " + " AND ".join(where)) if where else ""
            expression = "COUNT(*)" if (op == "count" and not field_key) \
                else f"{OPERATIONS[op]}({spec['column']})"
            cursor.execute(
                f"SELECT {expression} AS value, COUNT(*) AS records "
                f"FROM {entity.source}{where_sql}",
                params,
            )
            row = cursor.fetchone()
            value = _to_number(row[0]) if row else None
            records = int(row[1] or 0) if row else 0
            if op == "count" and not field_key:
                value = float(records)

            groups: List[Dict[str, Any]] = []
            if group:
                column = _group_column(entity, group)
                if column is not None:
                    cursor.execute(
                        f"SELECT {column} AS label, {expression} AS value, "
                        f"COUNT(*) AS records FROM {entity.source}{where_sql} "
                        f"GROUP BY {column} ORDER BY value DESC LIMIT ?",
                        (*params, max(1, min(int(limit), MAX_ROWS))),
                    )
                    groups = _rows(cursor, entity, limit)
        finally:
            conn.close()

        return {
            "period": period.as_dict(),
            "value": value,
            "records": records,
            "groups": groups,
        }

    left = measure(first)
    right = measure(second)
    for side in (left, right):
        if "error" in side:
            return _error(side["error"])

    diff = _difference(left["value"], right["value"])
    return _ok({
        "operation": op,
        "operation_title": _operation_title(op),
        "entity": entity.key,
        "entity_title": entity.title,
        "field": field_key,
        "field_title": spec["title"] if spec else "записей",
        "group_by": group,
        "first": left,
        "second": right,
        "difference": diff["absolute"],
        "difference_percent": diff["percent"],
        "grew": diff["grew"],
        "text": _compare_text(entity, spec, field_key, op, left, right, diff),
    })


def _period_field(entity: Entity, spec: Optional[FieldSpec], field_key: str) -> str:
    """Поле-дата для сравнения периодов."""
    if spec is not None and spec["type"] == TYPE_DATE:
        return field_key
    return entity.date_column


def _group_column(entity: Entity, group: str) -> Optional[str]:
    """Колонка разбивки по группам (None — такой разбивки нет)."""
    if group in ("counterparty", "контрагент", "сторона", "партнёр", "партнер"):
        return _counterparty_column(entity)
    if entity.key == "contracts":
        if group in ("driver", "водитель", "водители"):
            return ("(SELECT COALESCE(d.full_name, '') FROM drivers d "
                    "WHERE d.id = c.driver_id)")
        if group in ("carrier", "перевозчик", "перевозчики"):
            return ("(SELECT COALESCE(NULLIF(TRIM(ca.full_name), ''), "
                    "ca.short_name, '') FROM carriers ca WHERE ca.id = c.carrier_id)")
        if group in ("customer", "заказчик", "заказчики"):
            return ("(SELECT COALESCE(NULLIF(TRIM(cu.full_name), ''), "
                    "cu.short_name, '') FROM customers cu "
                    "WHERE cu.id = c.customer_id)")
        if group in ("route", "маршрут"):
            return "c.route"
        if group in ("month", "месяц", "месяцы"):
            return "substr(COALESCE(c.contract_date, ''), 1, 7)"
    if entity.key == "vehicles" and group in ("carrier", "перевозчик",
                                              "перевозчики", "counterparty",
                                              "контрагент"):
        return ("(SELECT COALESCE(NULLIF(TRIM(ca.full_name), ''), ca.short_name, '') "
                "FROM carriers ca WHERE ca.id = v.carrier_id)")
    return None


def _difference(first: Any, second: Any) -> Dict[str, Any]:
    """Разница двух чисел: абсолютная, в процентах и направление."""
    left = _to_number(first)
    right = _to_number(second)
    if left is None or right is None:
        return {"absolute": None, "percent": None, "grew": None}
    absolute = left - right
    percent = None
    if right:
        percent = round(absolute / abs(right) * 100, 1)
    return {
        "absolute": round(absolute, 2),
        "percent": percent,
        "grew": absolute > 0 if absolute else None,
    }


def _records_text(count: int) -> str:
    """«2 записи», «5 записей» — правильное число для подписи."""
    forms = ("запись", "записи", "записей")
    if 11 <= count % 100 <= 14:
        form = forms[2]
    elif count % 10 == 1:
        form = forms[0]
    elif 2 <= count % 10 <= 4:
        form = forms[1]
    else:
        form = forms[2]
    return f"{count} {form}"


def _compare_text(entity: Entity, spec: Optional[FieldSpec], field_key: str,
                  operation: str, left: Dict[str, Any], right: Dict[str, Any],
                  diff: Dict[str, Any]) -> str:
    """Фраза сравнения периодов для оператора."""
    title = spec["title"] if spec else "записей"
    if operation == "count" and not field_key:
        title = entity.title
    first_title = left["period"]["title"]
    second_title = right["period"]["title"]
    text = (
        f"{_operation_title(operation)} «{title}»: "
        f"{first_title} — {_number_text(left['value'])} "
        f"({_records_text(left['records'])}), "
        f"{second_title} — {_number_text(right['value'])} "
        f"({_records_text(right['records'])})"
    )
    if diff["percent"] is not None:
        direction = "больше" if (diff["absolute"] or 0) > 0 else "меньше"
        if diff["absolute"] == 0:
            text += ". Изменений нет"
        else:
            text += (f". {first_title} {direction} на "
                     f"{_number_text(abs(diff['absolute']))} "
                     f"({abs(diff['percent'])}%)")
    return text


def top_entities(
    *,
    entity_type: str = "carriers",
    field: str = "amount",
    operation: str = "sum",
    period: str = "",
    group_by: str = "",
    limit: int = 5,
    counterparty: str = "",
    filters: Optional[Sequence[Any]] = None,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Топ-N по числу: «топ-5 перевозчиков за октябрь».

    Группировка берётся из ``group_by`` (по умолчанию — сторона сущности:
    у договоров перевозчик, у остальных — сама запись).
    """
    entity = resolve_entity(entity_type)
    if entity is None:
        return _error(f"считать «{entity_type}» не умею",
                      available=list(entity_keys()))

    op = resolve_operation(operation)
    if op is None:
        return _error(f"операция «{operation}» не поддерживается",
                      available=list(operation_keys()))

    field_key = _default_field(entity, op, field)
    if op == "count" and not field_key:
        spec: Optional[FieldSpec] = {"column": entity.id_column,
                                     "type": TYPE_NUMBER, "title": "записей"}
    else:
        spec = entity.field(field_key)
        if spec is None:
            return _error(f"поля «{field}» у «{entity.title}» нет",
                          available=list(entity.fields))

    parsed, error = resolve_period(
        period, today=today, date_field=_period_field(entity, spec, field_key))
    if error:
        return _error(error)

    group = str(group_by or "").strip().casefold()
    if not group:
        group = "carrier" if entity.key == "contracts" else "counterparty"
    column = _group_column(entity, group)
    if column is None:
        return _error(
            f"разбивка «{group_by}» для «{entity.title}» не поддерживается",
            available=["counterparty", "driver", "carrier", "customer",
                       "route", "month"],
        )

    try:
        where, params = build_conditions(
            entity, filters, period=parsed, counterparty=counterparty,
            today=today,
        )
    except FilterError as e:
        return _error(str(e))

    expression = "COUNT(*)" if (op == "count" and not field_key) \
        else f"{OPERATIONS[op]}({spec['column']})"

    conn = get_connection()
    try:
        cursor = conn.cursor()
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        top_limit = max(1, min(int(limit), MAX_ROWS))
        cursor.execute(
            f"SELECT {column} AS label, {expression} AS value, "
            f"COUNT(*) AS records FROM {entity.source}{where_sql} "
            f"GROUP BY {column} "
            f"HAVING COALESCE(TRIM(CAST({column} AS TEXT)), '') <> '' "
            f"ORDER BY value DESC LIMIT ?",
            (*params, top_limit),
        )
        rows = _rows(cursor, entity, top_limit)

        cursor.execute(
            f"SELECT COUNT(DISTINCT {column}) AS groups, "
            f"{expression} AS value, COUNT(*) AS records "
            f"FROM {entity.source}{where_sql}",
            params,
        )
        row = cursor.fetchone()
        total_groups = int(row[0] or 0) if row else 0
        total_value = _to_number(row[1]) if row else None
        total_records = int(row[2] or 0) if row else 0
    finally:
        conn.close()

    for index, item in enumerate(rows, start=1):
        item["rank"] = index

    return _ok({
        "operation": op,
        "operation_title": _operation_title(op),
        "entity": entity.key,
        "entity_title": entity.title,
        "field": field_key,
        "field_title": spec["title"] if spec else "записей",
        "group_by": group,
        "period": parsed.as_dict(),
        "limit": int(limit),
        "items": rows,
        "groups_total": total_groups,
        "records_total": total_records,
        "value_total": total_value,
        "text": _top_text(entity, spec, field_key, op, rows, parsed,
                          total_groups),
    })


def _top_text(entity: Entity, spec: Optional[FieldSpec], field_key: str,
              operation: str, rows: List[Dict[str, Any]], period: Period,
              total_groups: int) -> str:
    """Фраза топа для оператора (без выдумывания данных)."""
    title = spec["title"] if spec else "записей"
    if operation == "count" and not field_key:
        title = "записей"
    head = f"{_operation_title(operation)} «{title}»"
    if not period.empty():
        head += f" {period.title}"
    if not rows:
        return f"{head}: данных нет"
    parts = [
        f"{item['rank']}. {item['label']} — {_number_text(item['value'])}"
        for item in rows
    ]
    return (f"{head} (всего групп: {total_groups}): " + "; ".join(parts))


def count_records(
    entity_type: str,
    *,
    filters: Optional[Sequence[Any]] = None,
    period: str = "",
    counterparty: str = "",
    today: Optional[date] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """
    Сколько записей подходит под условия: «сколько водителей с истекающим ВУ».

    Фильтры — те же, что у сводки: словарь с полем, значением или признаком
    «пусто», либо имя готового фильтра (``empty_inn``, ``empty_vin``,
    ``empty_phone``, ``empty_passport``, ``expired_license``).
    """
    entity = resolve_entity(entity_type)
    if entity is None:
        return _error(f"считать «{entity_type}» не умею",
                      available=list(entity_keys()))

    parsed = None
    if period:
        parsed, error = resolve_period(period, today=today,
                                       date_field=_date_field(entity))
        if error:
            return _error(error)
    else:
        parsed = Period("all", "за всё время", date_field=_date_field(entity))

    try:
        where, params = build_conditions(
            entity, filters, period=parsed, counterparty=counterparty,
            today=today,
        )
    except FilterError as e:
        return _error(str(e))

    conn = get_connection()
    try:
        cursor = conn.cursor()
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        cursor.execute(
            f"SELECT COUNT(*) AS value FROM {entity.source}{where_sql}", params
        )
        row = cursor.fetchone()
        count = int(row[0] or 0) if row else 0

        examples: List[Dict[str, Any]] = []
        if count:
            cursor.execute(
                f"SELECT {entity.label_expression} AS label, "
                f"{entity.id_column} AS record_id "
                f"FROM {entity.source}{where_sql} LIMIT ?",
                (*params, max(1, min(int(limit), MAX_ROWS))),
            )
            columns = [desc[0] for desc in cursor.description or ()]
            examples = [
                {**dict(zip(columns, item)),
                 "label": str(dict(zip(columns, item)).get("label") or "").strip()}
                for item in cursor.fetchall()
            ]
    finally:
        conn.close()

    what = _filters_title(entity, filters, counterparty, parsed)
    return _ok({
        "count": count,
        "entity": entity.key,
        "entity_title": entity.title,
        "what": what,
        "period": parsed.as_dict(),
        "examples": examples,
        "text": f"{what}: {count}",
    })


def _filters_title(entity: Entity, filters: Optional[Sequence[Any]],
                   counterparty: str, period: Period) -> str:
    """Человеческая подпись того, что считали."""
    parts: List[str] = []
    for raw in filters or ():
        if isinstance(raw, str):
            raw = NAMED_FILTERS.get(raw.strip(), {})
        if not isinstance(raw, dict):
            continue
        spec = entity.field(str(raw.get("field") or ""))
        title = spec["title"] if spec else str(raw.get("field") or "")
        if raw.get("empty"):
            parts.append(f"без «{title}»")
        elif "days" in raw:
            days = int(raw["days"])
            parts.append(f"«{title}» истекает в ближайшие {days} дней"
                         if days >= 0 else f"«{title}» просрочен")
        elif raw.get("value") is not None:
            parts.append(f"«{title}» = {raw['value']}")
    if counterparty:
        parts.append(f"контрагент «{counterparty}»")
    head = entity.title
    if parts:
        head += " (" + ", ".join(parts) + ")"
    if not period.empty():
        head += f" {period.title}"
    return head


def check_data_quality(
    entity_type: str,
    *,
    required_fields: Optional[Sequence[str]] = None,
    today: Optional[date] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """
    Что не заполнено и где дубликаты: «проверь качество данных по водителям».

    Проверяются:

      * пустые важные поля сущности (у водителя — ФИО, паспорт, телефон, ВУ;
        у перевозчика — ИНН, КПП, адрес, руководитель; у договора — дата,
        марка/сумма; у машины — VIN и марка);
      * дубликаты по естественному ключу (ИНН у организаций, паспорт у
        водителей, VIN у машин, номер у договоров);
      * просроченные даты (если у сущности есть срок: ВУ водителя,
        окончание договора).

    :param required_fields: свои поля вместо набора по умолчанию.
    :return: отчёт: сколько записей проверено, список проблем по полям,
        список дубликатов и общая оценка.
    """
    entity = resolve_entity(entity_type)
    if entity is None:
        return _error(
            f"проверить качество «{entity_type}» не умею",
            available=list(entity_keys()),
        )

    fields = [str(name).strip() for name in (required_fields or ())
              if str(name).strip()]
    if not fields:
        fields = [name for name in DEFAULT_REQUIRED.get(entity.key, ())
                  if entity.field(name)]
    else:
        unknown = [name for name in fields if entity.field(name) is None]
        if unknown:
            return _error(
                f"поля {unknown} у «{entity.title}» нет",
                available=list(entity.fields),
            )

    conn = get_connection()
    try:
        cursor = conn.cursor()
        base = entity.soft_delete
        cursor.execute(
            f"SELECT COUNT(*) FROM {entity.source}{_where(base)}"
        )
        total = int(cursor.fetchone()[0] or 0)

        gaps: List[Dict[str, Any]] = []
        for name in fields:
            spec = entity.field(name)
            column = spec["column"]
            is_empty = f"COALESCE(TRIM(CAST({column} AS TEXT)), '') = ''"
            cursor.execute(
                f"SELECT COUNT(*) FROM {entity.source}{_where(base, is_empty)}"
            )
            missing = int(cursor.fetchone()[0] or 0)
            if not missing:
                continue
            cursor.execute(
                f"SELECT {entity.label_expression} AS label, "
                f"{entity.id_column} AS record_id FROM {entity.source}"
                f"{_where(base, is_empty)} LIMIT ?",
                (max(1, min(int(limit), MAX_ROWS)),),
            )
            columns = [desc[0] for desc in cursor.description or ()]
            gaps.append({
                "field": name,
                "title": spec["title"],
                "missing": missing,
                "percent": round(missing / total * 100, 1) if total else 0.0,
                "examples": [
                    {**dict(zip(columns, row)),
                     "label": str(dict(zip(columns, row)).get("label") or "").strip()}
                    for row in cursor.fetchall()
                ],
            })

        duplicates: List[Dict[str, Any]] = []
        for name in entity.duplicate_by:
            spec = entity.field(name)
            if spec is None:
                continue
            column = spec["column"]
            filled = f"COALESCE(TRIM(CAST({column} AS TEXT)), '') <> ''"
            cursor.execute(
                f"SELECT {column} AS value, COUNT(*) AS count "
                f"FROM {entity.source}{_where(base, filled)} "
                f"GROUP BY {column} HAVING COUNT(*) > 1 "
                f"ORDER BY COUNT(*) DESC LIMIT ?",
                (max(1, min(int(limit), MAX_ROWS)),),
            )
            items = [
                {"value": str(row[0]).strip(), "count": int(row[1] or 0)}
                for row in cursor.fetchall()
            ]
            if items:
                duplicates.append({
                    "field": name,
                    "title": spec["title"],
                    "groups": len(items),
                    "items": items,
                })

        expired: List[Dict[str, Any]] = []
        base_date = (today or _today()).isoformat()
        for name in ENTITY_DATE_FIELDS.get(entity.key, ()):
            spec = entity.field(name)
            if spec is None:
                continue
            column = spec["column"]
            filled = f"COALESCE(TRIM(CAST({column} AS TEXT)), '') <> ''"
            cursor.execute(
                f"SELECT COUNT(*) FROM {entity.source}"
                f"{_where(base, filled, f'date({column}) < date(?)')}",
                (base_date,),
            )
            count = int(cursor.fetchone()[0] or 0)
            if count:
                expired.append({
                    "field": name,
                    "title": spec["title"],
                    "count": count,
                    "on": base_date,
                })
    finally:
        conn.close()

    clean = not gaps and not duplicates and not expired
    return _ok({
        "entity": entity.key,
        "entity_title": entity.title,
        "total": total,
        "checked_fields": fields,
        "gaps": gaps,
        "duplicates": duplicates,
        "expired": expired,
        "is_clean": clean,
        "text": _quality_text(entity, total, gaps, duplicates, expired),
    })


#: Поля, которые должны быть заполнены у каждой сущности.
DEFAULT_REQUIRED: Dict[str, Tuple[str, ...]] = {
    "contracts": ("number", "contract_date", "route", "amount"),
    "carriers": ("inn", "kpp", "legal_address", "director"),
    "customers": ("inn", "kpp", "legal_address", "director"),
    "drivers": ("driver_name", "passport", "phone", "license_expiry"),
    "vehicles": ("vin", "brand_model"),
    "points": ("address", "point_date"),
}


def _is_days_filter(entity: Entity, raw: Dict[str, Any]) -> bool:
    """
    Похож ли фильтр на «срок истекает через N дней».

    Отличается от обычного фильтра тем, что вместо значения несёт число
    дней. Признак — ключ ``days`` и поле-дата (или поле не названо вовсе:
    тогда берётся срок сущности по умолчанию).
    """
    if "days" not in raw:
        return False
    name = str(raw.get("field") or "").strip()
    if not name:
        return bool(entity.date_column)
    return name in ENTITY_DATE_FIELDS.get(entity.key, ())


def _and(*conditions: str) -> str:
    """WHERE из простых условий «И» (пустые условия пропускаются)."""
    return _where(*conditions)


def _where(*conditions: str) -> str:
    """
    Собирает « WHERE …» из простых условий (пустые пропускаются).

    Условия — только фрагменты вида «колонка = ?»: имена берутся из белого
    списка, значения уходят параметрами. Пустой список даёт пустую строку,
    а не «WHERE» без условий (это синтаксическая ошибка SQL).
    """
    parts = [f"({item})" for item in conditions if item]
    return (" WHERE " + " AND ".join(parts)) if parts else ""


def _quality_text(entity: Entity, total: int, gaps: List[Dict[str, Any]],
                  duplicates: List[Dict[str, Any]],
                  expired: List[Dict[str, Any]]) -> str:
    """Фраза отчёта о качестве данных."""
    if not total:
        return f"{entity.title}: записей нет"
    if not gaps and not duplicates and not expired:
        return f"{entity.title} ({total}): пустых полей и дубликатов нет"
    parts: List[str] = [f"{entity.title}: проверено {total}"]
    for gap in gaps:
        parts.append(f"без «{gap['title']}» — {gap['missing']}")
    for item in duplicates:
        parts.append(f"повторов по «{item['title']}» — {item['groups']}")
    for item in expired:
        parts.append(f"просрочено «{item['title']}» — {item['count']}")
    return "; ".join(parts)


def _ok(data: Any = None, **extra: Any) -> Dict[str, Any]:
    """Ответ «получилось» (тот же вид, что у инструментов агента)."""
    result: Dict[str, Any] = {"ok": True, "data": data}
    result.update(extra)
    return result


def _error(message: str, **extra: Any) -> Dict[str, Any]:
    """Ответ «не получилось» с пояснением для модели."""
    result: Dict[str, Any] = {"ok": False, "error": message}
    result.update(extra)
    return result


__all__ = [
    "ENTITIES",
    "ENTITY_ALIASES",
    "ENTITY_DATE_FIELDS",
    "FIELD_CATALOG",
    "FILTER_OPERATORS",
    "MAX_ROWS",
    "MONTHS",
    "NAMED_FILTERS",
    "OPERATIONS",
    "OPERATION_ALIASES",
    "Entity",
    "FilterError",
    "Period",
    "aggregate",
    "build_conditions",
    "check_data_quality",
    "compare_periods",
    "count_records",
    "entity_keys",
    "fields_of",
    "operation_keys",
    "resolve_entity",
    "resolve_operation",
    "resolve_period",
    "top_entities",
]
