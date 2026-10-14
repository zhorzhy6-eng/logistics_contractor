#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Аналитика Максимилиана (задание, часть 3).

Пять инструментов этого модуля отвечают на «сколько», «средняя», «топ»,
«сравни» и «что не заполнено». Они устроены иначе, чем поиск: оператору
нужны ГОТОВЫЕ ЧИСЛА, а не список записей, поэтому инструменты возвращают
сводку, топ или отчёт, а не строки базы.

Почему отдельный модуль
-----------------------
SQL здесь нет вовсе — он живёт в `db/crud/analytics.py` (правило проекта:
инструменты только зовут функции базы). Здесь — язык: русские названия
полей и операций, разбор «что посчитать», понятные тексты ответов и
аккуратные ошибки вместо исключений.

Что важно для модели:

  * каждый инструмент возвращает ``{"ok": True, "data": …}`` или
    ``{"ok": False, "error": "…"}`` — как остальные инструменты агента;
  * поле ``text`` в данных — готовая фраза с числом, её можно пересказать
    оператору почти дословно;
  * период, поле и операция понимаются по-русски («средняя», «за октябрь»,
    «3 месяца») — модель не обязана знать ключи.
"""

import logging
from typing import Any, Dict, List, Optional, Sequence

from db.crud import analytics

logger = logging.getLogger("core.agent.analytics_tools")

#: Сколько записей показывать в топе по умолчанию.
DEFAULT_TOP_LIMIT = 5

#: Русские названия полей, которые модель называет словами оператора.
#: Ключ — как сказал бы человек, значение — ключ каталога analytics.
FIELD_ALIASES: Dict[str, str] = {
    "сумма": "amount", "суммы": "amount", "стоимость": "amount",
    "цена": "amount", "стоимость договора": "amount", "сумма договора": "amount",
    "сумма с ндс": "amount", "с ндс": "amount", "выручка": "amount",
    "сумма без ндс": "amount_without_vat", "без ндс": "amount_without_vat",
    "предоплата": "prepayment", "аванс": "prepayment",
    "дата": "contract_date", "дата договора": "contract_date",
    "начало": "start_date", "дата начала": "start_date",
    "окончание": "end_date", "дата окончания": "end_date", "срок": "end_date",
    "номер": "number", "номер договора": "number",
    "маршрут": "route", "рейс": "route", "направление": "route",
    "валюта": "currency",
    "ву": "license_expiry", "права": "license_expiry",
    "срок ву": "license_expiry", "водительское удостоверение": "license_expiry",
    "фио": "driver_name", "водитель": "driver_name", "фамилия": "driver_name",
    "телефон": "phone", "паспорт": "passport",
    "инн": "inn", "кпп": "kpp", "огрн": "ogrn",
    "наименование": "org_name", "название": "org_name",
    "организация": "org_name", "руководитель": "director",
    "директор": "director", "адрес": "legal_address",
    "юридический адрес": "legal_address",
    "расчётный счёт": "bank_account", "расчетный счет": "bank_account",
    "счет": "bank_account", "бик": "bik",
    "vin": "vin", "вин": "vin",
    "марка": "brand_model", "модель": "brand_model", "марка, модель": "brand_model",
    "госномер": "plate_number", "номер машины": "plate_number",
    "год": "year", "год выпуска": "year",
    "тип": "vehicle_type", "тип тс": "vehicle_type",
    "точка": "point_name", "салон": "point_name", "город": "address",
    "адрес точки": "address", "дата точки": "point_date",
}

#: Русские названия операций (то, чего нет в analytics.OPERATION_ALIASES).
OPERATION_EXTRA: Dict[str, str] = {
    "посчитать": "count", "количество записей": "count",
    "суммарная": "sum", "суммарно": "sum",
    "средний чек": "avg", "средняя сумма": "avg",
    "самый большой": "max", "самый маленький": "min",
}

#: Что значит «топ-N» по умолчанию: считаем суммы по договорам, а группируем
#: по перевозчику. «Топ-5 перевозчиков» — это ровно такой запрос.
TOP_ENTITY_DEFAULT = "contracts"
TOP_GROUP_DEFAULT = "carrier"

#: Синонимы того, что группируем в топе. «Перевозчики» и «контрагенты» —
#: это разбивка по СТОРОНЕ договора (`counterparty`): внутри одной сущности
#: их нельзя различить, а база сама выбирает нужную сторону по сущности.
GROUP_ALIASES: Dict[str, str] = {
    "перевозчик": "counterparty", "перевозчики": "counterparty",
    "перевозчиков": "counterparty", "carriers": "counterparty",
    "carrier": "counterparty",
    "контрагент": "counterparty", "контрагенты": "counterparty",
    "сторона": "counterparty", "counterparty": "counterparty",
    "водитель": "driver", "водители": "driver", "водителей": "driver",
    "driver": "driver", "drivers": "driver",
    "заказчик": "customer", "заказчики": "customer",
    "заказчиков": "customer", "customer": "customer", "клиент": "customer",
    "маршрут": "route", "маршруты": "route", "route": "route",
    "месяц": "month", "месяцы": "month", "month": "month",
    "договор": "none", "договоры": "none", "none": "none",
}

#: Сущности, в которых «перевозчик» — это сама запись, а не её сторона:
#: группировать перевозчика по перевозчику нечего, в топ идут записи.
SELF_GROUP_ENTITIES = ("carriers",)


def _ok(data: Any = None, **extra: Any) -> Dict[str, Any]:
    """Ответ «получилось»."""
    result: Dict[str, Any] = {"ok": True, "data": data}
    result.update(extra)
    return result


def _error(message: str, **extra: Any) -> Dict[str, Any]:
    """Ответ «не получилось» с пояснением для модели."""
    result: Dict[str, Any] = {"ok": False, "error": message}
    result.update(extra)
    return result


# ─────────────────────────────────────────────────────────────
# Разбор аргументов
# ─────────────────────────────────────────────────────────────

def resolve_field(field: str, entity_type: str = "contracts") -> str:
    """
    Ключ поля по русскому названию.

    Понимает и «как в форме» (``price_with_vat``), и «как говорит оператор»
    («сумма», «ВУ», «госномер»). Неизвестное имя возвращается как есть:
    функция базы сама скажет, что такого поля нет, и перечислит доступные.
    """
    name = " ".join(str(field or "").strip().casefold().split())
    if not name:
        return ""
    if name in FIELD_ALIASES:
        return FIELD_ALIASES[name]
    entity = analytics.resolve_entity(entity_type)
    if entity is not None and name in entity.fields:
        return name
    if name in analytics.FIELD_CATALOG:
        return name
    return name


def resolve_group(group_by: str, entity_type: str = "contracts") -> str:
    """
    Ключ разбивки по группам («перевозчики» → ``counterparty``).

    Отдельная тонкость: когда считают самих перевозчиков
    (``entity_type="carriers"``), «топ-5 перевозчиков» — это топ ЗАПИСЕЙ, а
    не разбивка по стороне: группировать перевозчика по перевозчику нечего.
    Такой случай возвращает ``none``.
    """
    name = " ".join(str(group_by or "").strip().casefold().split())
    if not name:
        return ""
    key = GROUP_ALIASES.get(name, name)
    if entity_type in SELF_GROUP_ENTITIES and key == "counterparty":
        return "none"
    return key


def _period_text(period: Any) -> str:
    """
    Период строкой.

    Модель иногда присылает период объектом (``{"start": …, "end": …}``) —
    такой вид принимается и переводится в понятную базе запись.
    """
    if isinstance(period, dict):
        start = str(period.get("start") or "").strip()
        end = str(period.get("end") or "").strip()
        if start and end:
            return f"с {start} по {end}"
        if start:
            return f"с {start} по {start}"
        return str(period.get("title") or period.get("key") or "")
    return str(period or "").strip()


def _resolve_operation(operation: str) -> str:
    """Операция по русскому названию (пусто — пустая строка)."""
    name = " ".join(str(operation or "").strip().casefold().split())
    if not name:
        return ""
    if name in OPERATION_EXTRA:
        return OPERATION_EXTRA[name]
    return analytics.resolve_operation(name) or name


def _entity_title(entity_type: str) -> str:
    """Русское название сущности (для текста ошибки)."""
    entity = analytics.resolve_entity(entity_type)
    return entity.title if entity else str(entity_type)


# ─────────────────────────────────────────────────────────────
# Инструменты
# ─────────────────────────────────────────────────────────────

def aggregate_contracts(
    counterparty: str = "",
    period: str = "",
    field: str = "amount",
    operation: str = "sum",
    *,
    entity_type: str = "contracts",
    today: Any = None,
) -> Dict[str, Any]:
    """
    Сводка по числу: сумма, среднее, максимум, минимум или количество.

    Примеры запросов, которые сюда ведут:

      * «средняя сумма с Ромашкой за 3 месяца» → ``operation="avg"``,
        ``field="amount"``, ``period="3 месяца"``, ``counterparty="Ромашка"``;
      * «сколько договоров всего» → ``operation="count"``, ``field=""``;
      * «самая большая сумма за октябрь» → ``operation="max"``.

    :param entity_type: что считаем (``contracts``, ``drivers``, ``vehicles``,
        ``carriers``, ``customers``, ``points``) — по умолчанию договоры;
    :return: готовое число (``data.value``) и фраза ``data.text``.
    """
    operation_key = _resolve_operation(operation) or "sum"
    field_key = resolve_field(field, entity_type) if str(field or "").strip() else ""

    if operation_key == "count" and not field_key:
        field_key = ""

    result = analytics.aggregate(
        entity_type,
        field_key,
        operation_key,
        period=_period_text(period),
        counterparty=str(counterparty or "").strip(),
        today=today,
    )
    if not result.get("ok"):
        return _error(str(result.get("error") or "посчитать не удалось"),
                      available=result.get("available"))
    return _ok(result["data"])


def compare_periods(
    period1: str,
    period2: str,
    *,
    group_by: str = "",
    entity_type: str = "contracts",
    field: str = "amount",
    operation: str = "sum",
    counterparty: str = "",
    today: Any = None,
) -> Dict[str, Any]:
    """
    Сравнение двух периодов: «сравни октябрь с сентябрём».

    Возвращает оба числа, разницу в деньгах и в процентах, а при
    ``group_by`` — ещё и разбивку по контрагентам внутри каждого периода.

    :param group_by: ``counterparty`` (контрагенты), ``driver`` (водители),
        ``carrier`` (перевозчики), ``route`` (маршруты), ``month`` (месяцы).
    """
    operation_key = _resolve_operation(operation) or "sum"
    field_key = resolve_field(field, entity_type) if str(field or "").strip() else ""
    group = resolve_group(group_by, entity_type)

    result = analytics.compare_periods(
        _period_text(period1),
        _period_text(period2),
        entity_type=entity_type,
        field=field_key,
        operation=operation_key,
        group_by="" if group in ("none",) else group,
        counterparty=str(counterparty or "").strip(),
        today=today,
    )
    if not result.get("ok"):
        return _error(str(result.get("error") or "сравнить не удалось"),
                      available=result.get("available"))
    return _ok(result["data"])


def top_contractors(
    period: str = "",
    *,
    field: str = "amount",
    operation: str = "sum",
    limit: int = DEFAULT_TOP_LIMIT,
    group_by: str = "",
    entity_type: str = TOP_ENTITY_DEFAULT,
    counterparty: str = "",
    today: Any = None,
) -> Dict[str, Any]:
    """
    Топ-N по числу: «топ-5 перевозчиков за октябрь».

    По умолчанию считаются суммы договоров в разбивке по перевозчику — это
    самый частый вопрос оператора. Если ``entity_type="carriers"``, в топ
    попадают сами записи перевозчиков (их суммы за период).
    """
    operation_key = _resolve_operation(operation) or "sum"
    field_key = resolve_field(field, entity_type) if str(field or "").strip() else ""
    group = resolve_group(group_by, entity_type)
    if group == "none":
        # «Топ договоров» — топ самих записей, без разбивки по стороне.
        group = ""
    elif not group:
        group = TOP_GROUP_DEFAULT

    result = analytics.top_entities(
        entity_type=entity_type,
        field=field_key,
        operation=operation_key,
        period=_period_text(period),
        group_by="" if group in ("none",) else group,
        limit=int(limit or DEFAULT_TOP_LIMIT),
        counterparty=str(counterparty or "").strip(),
        today=today,
    )
    if not result.get("ok"):
        return _error(str(result.get("error") or "топ не построен"),
                      available=result.get("available"))
    return _ok(result["data"])


def count_records(
    entity_type: str = "drivers",
    filters: Optional[Sequence[Any]] = None,
    *,
    period: str = "",
    counterparty: str = "",
    today: Any = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """
    Сколько записей подходит под условия: «сколько водителей с истекающим ВУ».

    Фильтры — список словарей. Каждый фильтр описывает одно условие:

      * ``{"field": "license_expiry", "days": 30}`` — срок истекает в
        ближайшие 30 дней (``days: -1`` — уже просрочен);
      * ``{"field": "inn", "empty": true}`` — поле не заполнено;
      * ``{"field": "amount", "operator": "gt", "value": 100000}`` — сравнение;
      * ``{"field": "route", "contains": "Москва"}`` — поиск по части текста.

    Готовые фильтры по имени: ``empty_inn``, ``empty_vin``, ``empty_phone``,
    ``empty_passport``, ``expired_license``, ``expiring_soon``.
    """
    result = analytics.count_records(
        entity_type,
        filters=list(filters or []),
        period=_period_text(period),
        counterparty=str(counterparty or "").strip(),
        today=today,
        limit=int(limit or 10),
    )
    if not result.get("ok"):
        return _error(str(result.get("error") or "посчитать не удалось"),
                      available=result.get("available"))
    return _ok(result["data"])


def check_data_quality(
    entity_type: str = "drivers",
    *,
    required_fields: Optional[Sequence[str]] = None,
    today: Any = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """
    Что не заполнено и где дубликаты: «проверь данные по водителям».

    Проверяются пустые важные поля, повторы по естественному ключу (ИНН,
    паспорт, VIN, номер договора) и просроченные сроки. Свои обязательные
    поля можно назвать списком ``required_fields`` — имена понимаются
    по-русски («телефон», «ИНН»).
    """
    entity = analytics.resolve_entity(entity_type)
    fields: List[str] = []
    if required_fields:
        fields = [resolve_field(name, entity_type)
                  for name in required_fields if str(name or "").strip()]
        if entity is not None:
            unknown = [name for name in fields if entity.field(name) is None]
            if unknown:
                return _error(
                    f"поля {unknown} у «{entity.title}» нет",
                    available=list(entity.fields),
                )

    result = analytics.check_data_quality(
        entity_type,
        required_fields=fields or None,
        today=today,
        limit=int(limit or 5),
    )
    if not result.get("ok"):
        return _error(str(result.get("error") or "проверить не удалось"),
                      available=result.get("available"))
    return _ok(result["data"])


# ─────────────────────────────────────────────────────────────
# Описания для реестра инструментов
# ─────────────────────────────────────────────────────────────

def tool_specs() -> List[Dict[str, Any]]:
    """
    Описания пяти инструментов аналитики для реестра агента.

    Возвращаются словарями, а не объектами Tool: реестр собирается в
    core/agent/tools.py, и модуль аналитики не должен знать про его типы
    (иначе получится цикл импортов).
    """
    return [
        {
            "name": "aggregate_contracts",
            "description": (
                "Посчитать ОДНО число по базе: сумма, среднее, максимум, "
                "минимум или количество. Примеры: «средняя сумма с Ромашкой "
                "за 3 месяца», «сколько договоров за октябрь», «самая большая "
                "сумма за сентябрь». Период понимается по-русски: «октябрь», "
                "«октябрь 2026», «3 месяца», «2026», «с 01.09.2026 по "
                "30.09.2026». Поле можно называть словами: «сумма», «ВУ», "
                "«телефон», «госномер»."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "counterparty": {
                        "type": "string",
                        "description": "перевозчик, заказчик или водитель "
                                       "(можно часть названия)",
                    },
                    "period": {
                        "type": "string",
                        "description": "период: «октябрь», «3 месяца», «2026»",
                    },
                    "field": {
                        "type": "string",
                        "description": "что считаем: amount (сумма), "
                                       "license_expiry (срок ВУ), year и т.д.",
                    },
                    "operation": {
                        "type": "string",
                        "enum": ["sum", "avg", "max", "min", "count"],
                        "description": "sum — сумма, avg — среднее, max/min — "
                                       "границы, count — количество",
                    },
                    "entity_type": {
                        "type": "string",
                        "enum": list(analytics.entity_keys()),
                        "description": "что считаем; по умолчанию договоры",
                    },
                },
                "additionalProperties": False,
            },
            "summary": "Сводка по базе",
        },
        {
            "name": "compare_periods",
            "description": (
                "Сравнить два периода между собой: «сравни октябрь с "
                "сентябрём», «сравни этот квартал с прошлым». Возвращает оба "
                "числа, разницу и рост в процентах. Можно попросить разбивку "
                "по контрагентам, водителям, перевозчикам, маршрутам."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period1": {
                        "type": "string",
                        "description": "первый период (обычно более поздний)",
                    },
                    "period2": {
                        "type": "string",
                        "description": "второй период (с чем сравниваем)",
                    },
                    "group_by": {
                        "type": "string",
                        "description": "разбивка: counterparty, driver, "
                                       "carrier, customer, route, month",
                    },
                    "field": {
                        "type": "string",
                        "description": "что сравниваем (по умолчанию сумма)",
                    },
                    "operation": {
                        "type": "string",
                        "enum": ["sum", "avg", "max", "min", "count"],
                    },
                    "counterparty": {
                        "type": "string",
                        "description": "ограничить одним контрагентом",
                    },
                },
                "required": ["period1", "period2"],
                "additionalProperties": False,
            },
            "summary": "Сравнение двух периодов",
        },
        {
            "name": "top_contractors",
            "description": (
                "Топ-N по сумме: «топ-5 перевозчиков за октябрь», «кто больше "
                "всех перевёз в сентябре», «топ водителей по количеству "
                "рейсов». По умолчанию — суммы договоров в разбивке по "
                "перевозчику."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "период: «октябрь», «3 месяца», «2026»",
                    },
                    "field": {
                        "type": "string",
                        "description": "по чему топ (по умолчанию сумма)",
                    },
                    "operation": {
                        "type": "string",
                        "enum": ["sum", "avg", "max", "min", "count"],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "сколько строк показать (по умолчанию 5)",
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "group_by": {
                        "type": "string",
                        "description": "кого считаем: carrier (по умолчанию), "
                                       "driver, customer, counterparty, route",
                    },
                    "entity_type": {
                        "type": "string",
                        "enum": list(analytics.entity_keys()),
                    },
                },
                "additionalProperties": False,
            },
            "summary": "Топ по сумме",
        },
        {
            "name": "count_records",
            "description": (
                "Сколько записей подходит под условия: «сколько водителей с "
                "истекающим ВУ», «сколько перевозчиков без ИНН», «сколько "
                "машин без VIN». Условия — список фильтров: срок («days»), "
                "пустое поле («empty»), сравнение («operator» + «value»), "
                "поиск по части текста («contains»). Готовые фильтры: "
                "empty_inn, empty_vin, empty_phone, empty_passport, "
                "expired_license, expiring_soon."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": list(analytics.entity_keys()),
                        "description": "кого считаем",
                    },
                    "filters": {
                        "type": "array",
                        "description": "список условий отбора",
                        "items": {"type": "object"},
                    },
                    "period": {
                        "type": "string",
                        "description": "ограничить периодом (необязательно)",
                    },
                    "counterparty": {
                        "type": "string",
                        "description": "ограничить контрагентом",
                    },
                },
                "required": ["entity_type"],
                "additionalProperties": False,
            },
            "summary": "Количество записей по условию",
        },
        {
            "name": "check_data_quality",
            "description": (
                "Проверить качество данных: что не заполнено, где дубликаты, "
                "что просрочено. Примеры: «чего не хватает в карточках "
                "водителей», «есть ли дубликаты ИНН», «проверь данные "
                "перевозчиков»."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": list(analytics.entity_keys()),
                        "description": "что проверяем",
                    },
                    "required_fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "свои обязательные поля "
                                       "(«телефон», «ИНН»…)",
                    },
                },
                "required": ["entity_type"],
                "additionalProperties": False,
            },
            "summary": "Качество данных",
        },
    ]


__all__ = [
    "DEFAULT_TOP_LIMIT",
    "FIELD_ALIASES",
    "GROUP_ALIASES",
    "OPERATION_EXTRA",
    "aggregate_contracts",
    "check_data_quality",
    "compare_periods",
    "count_records",
    "resolve_field",
    "resolve_group",
    "tool_specs",
    "top_contractors",
]
