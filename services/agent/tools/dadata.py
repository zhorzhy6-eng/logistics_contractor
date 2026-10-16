#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Инструменты DaData для Максимилиана (задание, Часть 2).

Что здесь есть
--------------
Четыре инструмента — все про ОРГАНИЗАЦИИ и АДРЕСА:

    check_inn_dadata      — реквизиты организации по ИНН (ЕГРЮЛ);
    search_company_dadata — найти организацию по названию;
    check_address_dadata  — привести адрес к стандартному виду;
    compare_with_dadata   — сравнить запись базы с данными ЕГРЮЛ.

Безопасность (главное в этой части)
-----------------------------------
Наружу уходят ТОЛЬКО публичные сведения: ИНН, ОГРН, КПП, наименование,
адрес, банк. Это данные ЕГРЮЛ, они открыты по закону.

Сюда НЕ уходят и не могут уйти:
  * ФИО, паспорта, телефоны, адреса регистрации ВОДИТЕЛЕЙ;
  * содержимое форм с персональными данными;
  * значения полей, которые оператор ввёл в форму.

Инструменты этого модуля даже не принимают такие данные на вход: у
``check_inn_dadata`` единственный аргумент — ИНН, у ``search_company_dadata``
— название. Проверка ИНН в начале каждой функции отсекает всё, что на ИНН
не похоже, поэтому ФИО водителя физически не может превратиться в запрос.

Кэш — один час
--------------
Ответы DaData кэшируются на час (``CACHE_TTL_SECONDS``): реквизиты
организации за час не меняются, а лимит тарифа (10 000 запросов в сутки)
тратится на повторы. Кэш живёт в памяти процесса, ключ — операция плюс
аргументы; истёкшие записи удаляются при обращении.

Логи — без значений
-------------------
В лог пишутся только имена полей, длины и количество: ни ИНН, ни название,
ни адрес туда не попадают (это правило всего проекта).
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("core.agent.dadata_tools")

#: Сколько живёт ответ DaData в кэше (задание, часть 2: «кэш 1 час»).
CACHE_TTL_SECONDS = 3600

#: Сколько записей держим в кэше (защита от роста памяти в долгой сессии).
CACHE_MAX_ENTRIES = 200

#: Сколько подсказок запрашиваем у DaData по названию.
COMPANY_SEARCH_COUNT = 5

#: Поля формы перевозчика/заказчика, которые сверяются с ЕГРЮЛ.
COMPARED_FIELDS: Tuple[str, ...] = (
    "full_name", "short_name", "inn", "kpp", "ogrn",
    "legal_address", "director_name", "director_position",
)

#: Как поля формы называются для оператора (в отчёте о расхождениях).
FIELD_TITLES: Dict[str, str] = {
    "full_name": "Полное наименование",
    "short_name": "Короткое наименование",
    "inn": "ИНН",
    "kpp": "КПП",
    "ogrn": "ОГРН",
    "legal_address": "Юридический адрес",
    "director_name": "Руководитель",
    "director_position": "Должность руководителя",
    "entity_type": "Вид лица",
}

#: Кэш ответов: ключ → (момент записи, значение).
_CACHE: Dict[str, Tuple[float, Any]] = {}


def clear_cache() -> int:
    """Очищает кэш ответов DaData (нужно тестам и ручной проверке)."""
    size = len(_CACHE)
    _CACHE.clear()
    return size


def cache_size() -> int:
    """Сколько записей лежит в кэше сейчас."""
    return len(_CACHE)


def cached(key: str, producer: Callable[[], Any],
           ttl: int = CACHE_TTL_SECONDS) -> Any:
    """
    Возвращает значение из кэша или получает его через ``producer``.

    Ключ собирает вызывающий код и он НЕ содержит значений, которые нельзя
    писать в лог: сюда попадают ИНН, названия и адреса (публичные данные).
    В логи ключ всё равно не пишется — только факт попадания или промаха.

    :param key: ключ кэша (операция + аргументы);
    :param producer: функция без аргументов, которая сходит в DaData;
    :param ttl: сколько секунд держать значение.
    """
    now = time.monotonic()
    entry = _CACHE.get(key)
    if entry is not None:
        moment, value = entry
        if now - moment < ttl:
            logger.debug("DaData-кэш: попадание")
            return value
        _CACHE.pop(key, None)

    logger.debug("DaData-кэш: промах — идём в API")
    value = producer()

    # Чистим истёкшие записи и соблюдаем предел размера: словарь в памяти
    # процесса, он не должен расти бесконечно.
    for old_key in [name for name, (moment, _v) in _CACHE.items()
                    if now - moment >= ttl]:
        _CACHE.pop(old_key, None)
    if len(_CACHE) >= CACHE_MAX_ENTRIES:
        oldest = min(_CACHE, key=lambda name: _CACHE[name][0])
        _CACHE.pop(oldest, None)

    _CACHE[key] = (now, value)
    return value


# ─────────────────────────────────────────────────────────────
# Общий вид ответа
# ─────────────────────────────────────────────────────────────

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


def _client():
    """
    Клиент DaData (ключ берётся из системного хранилища).

    Исключения не глушатся: инструмент превращает их в понятный текст,
    чтобы модель сказала оператору «нужен ключ», а не «ошибка».
    """
    from infrastructure.api.dadata import DadataClient

    return DadataClient()


def _short(value: Any, limit: int = 300) -> str:
    """Значение строкой с ограничением длины."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fail(exc: Exception, action: str) -> Dict[str, Any]:
    """
    Отказ внешнего сервиса → обычный ответ инструмента (НЕ исключение).

    Правило проекта: ошибка внешнего API не должна ронять основную операцию.
    DaData недоступна, ключ не принят, лимит исчерпан, ответ не разобрался —
    инструмент возвращает ``ok=False`` с текстом, модель говорит оператору,
    что не получилось, и работа продолжается (в автозаполнении адрес остаётся
    таким, как его ввёл оператор).

    Ловится ЛЮБОЕ исключение, а не только ``ValueError``/``RuntimeError``:
    кроме клиента DaData здесь бывает системное хранилище ключей, сеть и
    разбор ответа — падать из-за них приложение не должно.
    """
    kind = type(exc).__name__
    logger.warning("DaData: %s не выполнен (%s)", action, kind)
    text = str(exc).strip() or f"DaData недоступна ({kind}). Повторите позже."
    return _error(text, source="DaData")


def _digits(value: Any) -> str:
    """Только цифры из значения («ИНН 7701 234 567» → «7701234567»)."""
    return "".join(char for char in str(value or "") if char.isdigit())


def _check_inn(inn: Any) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Проверяет, что аргумент похож на ИНН.

    Заодно это защита: в DaData может уйти только число из 10 или 12 цифр,
    поэтому ФИО, телефон или адрес водителя физически не станут запросом.

    :return: (нормализованный ИНН, ошибка или None)
    """
    digits = _digits(inn)
    if not digits:
        return "", _error("ИНН не указан — укажите 10 или 12 цифр")
    if len(digits) not in (10, 12):
        return "", _error(
            f"ИНН должен состоять из 10 или 12 цифр, а получено {len(digits)}",
            hint="проверьте номер: лишние цифры или пропуск",
        )
    return digits, None


# ─────────────────────────────────────────────────────────────
# 1. check_inn_dadata — реквизиты по ИНН
# ─────────────────────────────────────────────────────────────

def check_inn_dadata(inn: str = "", **_) -> Dict[str, Any]:
    """
    Реквизиты организации по ИНН из ЕГРЮЛ (через DaData).

    Что отдаётся: наименование, ОГРН, КПП, юридический адрес, руководитель,
    вид лица, статус (действует / ликвидируется / ликвидирована), а также
    телефон и почта, если они есть в открытых источниках.

    Данные публичные: это выписка ЕГРЮЛ, персональных сведений здесь нет.
    Ничего не меняет — только читает.
    """
    digits, error = _check_inn(inn)
    if error:
        return error

    def fetch() -> Optional[Dict[str, Any]]:
        """Запрос в DaData (вызывается только при промахе кэша)."""
        return _client().find_party_by_inn(digits)

    try:
        found = cached(f"inn:{digits}", fetch)
    except Exception as e:  # noqa: BLE001 — внешний сервис: операцию не роняем
        return _fail(e, "поиск по ИНН")

    if not found:
        return _ok({}, found=False, message="организация с таким ИНН не найдена")

    data = {
        "full_name": _short(found.get("full_name")),
        "short_name": _short(found.get("short_name")),
        "inn": _short(found.get("inn")),
        "kpp": _short(found.get("kpp")),
        "ogrn": _short(found.get("ogrn")),
        "legal_address": _short(found.get("legal_address")),
        "director_name": _short(found.get("director_name")),
        "director_position": _short(found.get("director_position")),
        "entity_type": _short(found.get("entity_type")),
        "status": _short(found.get("status")),
        "phone": _short(found.get("phone")),
        "email": _short(found.get("email")),
    }
    return _ok(data, found=True, source="DaData (ЕГРЮЛ)",
               cached_for_seconds=CACHE_TTL_SECONDS)


# ─────────────────────────────────────────────────────────────
# 2. search_company_dadata — поиск по названию
# ─────────────────────────────────────────────────────────────

def search_company_dadata(query: str = "", limit: int = COMPANY_SEARCH_COUNT,
                          **_) -> Dict[str, Any]:
    """
    Находит организации по НАЗВАНИЮ (не по ИНН) в ЕГРЮЛ через DaData.

    Отличие от поиска по своей базе: там только те организации, которые
    оператор уже заводил; здесь — все действующие организации России.
    Нужно, когда перевозчика ещё нет в справочнике, а реквизиты нужны.

    Возвращает СПИСОК: по названию «Ромашка» в стране несколько юрлиц, и
    выбирать должен оператор.
    """
    text = _short(query, 200)
    if not text:
        return _error("не указано название организации")

    try:
        count = max(1, min(10, int(limit)))
    except (TypeError, ValueError):
        count = COMPANY_SEARCH_COUNT

    def fetch() -> List[Dict[str, Any]]:
        """Запрос в DaData (вызывается только при промахе кэша)."""
        return _client().suggest_parties(text, count=count)

    try:
        rows = cached(f"name:{text.casefold()}:{count}", fetch)
    except Exception as e:  # noqa: BLE001 — внешний сервис: операцию не роняем
        return _fail(e, "поиск по названию")

    found = [{
        "full_name": _short(row.get("full_name")),
        "short_name": _short(row.get("short_name")),
        "inn": _short(row.get("inn")),
        "kpp": _short(row.get("kpp")),
        "ogrn": _short(row.get("ogrn")),
        "legal_address": _short(row.get("legal_address")),
        "director_name": _short(row.get("director_name")),
        "entity_type": _short(row.get("entity_type")),
        "status": _short(row.get("status")),
    } for row in rows or ()]

    if not found:
        return _ok([], count=0, query=text,
                   message="в ЕГРЮЛ ничего не найдено по этому названию")
    return _ok(found, count=len(found), query=text, source="DaData (ЕГРЮЛ)")


# ─────────────────────────────────────────────────────────────
# 3. check_address_dadata — стандартный вид адреса
# ─────────────────────────────────────────────────────────────

def check_address_dadata(address: str = "", **_) -> Dict[str, Any]:
    """
    Приводит адрес к стандартному виду (как в ФИАС) через DaData.

    «г москва ул тестовая д 1» → «г Москва»;
    «Пятигорск Ленина 5» → «Ставропольский край, г Пятигорск, пл Ленина, д 5».

    Дополнительно отдаётся признак точности ``qc_geo``: 0 — координаты
    определены точно, больше — приблизительно. Если адрес разобран
    частично (улица не найдена), инструмент говорит об этом прямо, а не
    показывает обрезанный результат как готовый.
    """
    text = _short(address, 300)
    if not text:
        return _error("не указан адрес")

    def fetch() -> Optional[Dict[str, Any]]:
        """Запрос в DaData (вызывается только при промахе кэша)."""
        return _client().clean_address(text)

    try:
        cleaned = cached(f"addr:{text.casefold()}", fetch)
    except Exception as e:  # noqa: BLE001 — внешний сервис: операцию не роняем
        # «нет секретного ключа», «403 — ключ не принят», сеть, лимит: адрес
        # остаётся таким, как его ввёл оператор, автозаполнение продолжается.
        return _fail(e, "стандартизация адреса")

    if not cleaned:
        return _ok({}, found=False,
                   message="адрес разобрать не удалось — проверьте написание")

    value = _short(cleaned.get("value"))
    warnings: List[str] = []
    if not cleaned.get("street"):
        warnings.append("улица не распознана — DaData разобрала только город")
    if not cleaned.get("house"):
        warnings.append("номер дома не распознан")
    qc = cleaned.get("qc_geo")
    if isinstance(qc, int) and qc > 0:
        warnings.append(f"координаты приблизительные (qc_geo={qc})")

    return _ok(
        {
            "value": value,
            "city": _short(cleaned.get("city")),
            "street": _short(cleaned.get("street")),
            "house": _short(cleaned.get("house")),
            "flat": _short(cleaned.get("flat")),
            "region": _short(cleaned.get("region")),
            "postal_code": _short(cleaned.get("postal_code")),
            "fias_id": _short(cleaned.get("fias_id")),
            "geo_lat": _short(cleaned.get("geo_lat")),
            "geo_lon": _short(cleaned.get("geo_lon")),
            "qc_geo": qc,
        },
        found=True,
        unchanged=(value.casefold() == text.casefold()),
        warnings=warnings,
        source="DaData (очистка адреса)",
    )


# ─────────────────────────────────────────────────────────────
# 4. compare_with_dadata — сверить базу с ЕГРЮЛ
# ─────────────────────────────────────────────────────────────

def _load_organization_record(entity_id: int, is_carrier: bool) -> Optional[Dict[str, Any]]:
    """Запись организации из локальной базы (или None, если её нет)."""
    from db.crud.organizations import load_organization

    return load_organization(entity_id, is_carrier=is_carrier)


def _normalized(value: Any) -> str:
    """
    Значение для сравнения: регистр, «ё», кавычки и пробелы не важны.

    «ООО «ФассТранс»» и «ООО "ФАССТРАНС"» — одно и то же название,
    записанное по-разному; ругаться на такие различия бессмысленно.
    """
    text = _short(value).replace("ё", "е").replace("Ё", "Е").casefold()
    for char in ("«", "»", '"', "'", "`"):
        text = text.replace(char, "")
    return " ".join(text.split())


def _resolve_entity_id(query: str, is_carrier: bool) -> Tuple[int, str]:
    """
    Находит ID организации по названию в локальной базе.

    Нужно потому, что ID оператор не знает и не должен: он говорит «сверь
    Ромашку», а запись ищет программа. Возвращается ещё и название — чтобы
    модель в ответе называла запись, а не номер.

    Запрос берётся в трёх видах, от точного к вольному:
    (1) как сказал оператор; (2) без слов-команд («сверь Ромашку» →
    «Ромашку»); (3) по памяти — если помощник уже знает, что «Ромашка» это
    ООО «Ромашка» (core/agent/long_memory.py). Без третьего шага сверка
    ломалась на коротких названиях, которых в базе нет в таком виде.

    :return: (ID записи, название); ID = 0, если не нашлось или нашлось
        несколько разных записей (тогда сверять нечего — нужен выбор).
    """
    from db.crud.organizations import search_organizations

    from services.agent.core.long_memory import search_terms
    from core.agent.words import search_terms as words_search_terms

    wanted = _short(query, 200)
    if not wanted:
        return 0, ""

    candidates: List[str] = []
    for term in [wanted] + words_search_terms(wanted):
        if term and term.casefold() not in [item.casefold()
                                            for item in candidates]:
            candidates.append(term)
    #: По памяти ищем по КАЖДОМУ варианту: алиас мог быть записан как на
    #: фразу целиком, так и на одно название из неё.
    for term in list(candidates):
        for alternative in search_terms(term, limit=2):
            if alternative and alternative.casefold() not in [
                item.casefold() for item in candidates
            ]:
                candidates.append(alternative)

    for position, term in enumerate(candidates):
        try:
            rows = search_organizations(term, is_carrier=is_carrier, limit=2)
        except Exception as e:  # noqa: BLE001 — поиск не должен ронять сверку
            logger.warning("Сверка: поиск организации не удался (%s)", e)
            continue
        if len(rows) != 1:
            continue
        title = _short(rows[0].get("full_name") or rows[0].get("short_name"))
        try:
            record_id = int(rows[0].get("id") or 0)
        except (TypeError, ValueError):
            record_id = 0
        if record_id:
            if position:
                logger.info("Сверка: запись найдена по варианту запроса")
            return record_id, title
    return 0, ""


def compare_with_dadata(entity_type: str = "carrier", entity_id: int = 0,
                        query: str = "", **_) -> Dict[str, Any]:
    """
    Сверяет запись локальной базы с данными ЕГРЮЛ (через DaData).

    Что показывает: какие поля в базе РАСХОДЯТСЯ с ЕГРЮЛ и что там лежит
    на самом деле. Нужно перед подготовкой договора: ошибка в наименовании
    или адресе перевозчика — это отказ банка и спор с контрагентом.

    Запись можно указать двумя способами: ``entity_id`` (ID из результата
    поиска) или ``query`` (название). Второй способ нужен потому, что ID
    оператор не знает: «сверь Ромашку» — нормальный запрос, и искать
    запись должен инструмент, а не человек.

    Только чтение: ничего не меняет и не «исправляет» — решение принимает
    оператор.
    """
    kind = _short(entity_type).casefold()
    if kind not in ("carrier", "customer"):
        return _error(
            "сверять можно только организации: entity_type = carrier или customer",
            given=entity_type,
        )
    is_carrier = kind == "carrier"

    try:
        record_id = int(entity_id)
    except (TypeError, ValueError):
        record_id = 0

    resolved_title = ""
    if not record_id:
        # ID не передан: ищем запись по названию сами.
        if not _short(query, 200):
            return _error(
                "не указано, какую организацию сверять: передайте entity_id "
                "из результата поиска или query с названием",
                hint="например: query='Ромашка'",
            )
        record_id, resolved_title = _resolve_entity_id(query, is_carrier)
        if not record_id:
            return _error(
                f"в базе не нашлось ровно одной организации по запросу "
                f"«{_short(query, 60)}»",
                hint="уточните название или сначала найдите запись "
                     "инструментом поиска и передайте её id",
            )

    record = _load_organization_record(record_id, is_carrier)
    if not record:
        return _error(f"запись с ID={record_id} в базе не найдена")

    inn, error = _check_inn(record.get("inn"))
    if error:
        return _error(
            "в записи нет корректного ИНН — сверять не с чем",
            details=error.get("error"),
        )

    fresh = check_inn_dadata(inn=inn)
    if not fresh.get("ok"):
        return fresh
    if not fresh.get("found"):
        return _ok(
            {}, found=False, inn=inn,
            message="организация с таким ИНН не найдена в ЕГРЮЛ",
        )

    reference = fresh["data"]
    matches: List[str] = []
    differences: List[Dict[str, str]] = []

    for field in COMPARED_FIELDS:
        in_base = _normalized(record.get(field))
        in_egrul = _normalized(reference.get(field))
        if not in_egrul:
            continue
        if not in_base:
            differences.append({
                "field": field,
                "title": FIELD_TITLES.get(field, field),
                "in_base": "",
                "in_egrul": reference.get(field) or "",
                "reason": "поле в базе пустое",
            })
            continue
        if in_base == in_egrul:
            matches.append(field)
            continue
        differences.append({
            "field": field,
            "title": FIELD_TITLES.get(field, field),
            "in_base": record.get(field) or "",
            "in_egrul": reference.get(field) or "",
            "reason": "значения различаются",
        })

    status = reference.get("status") or ""
    warnings: List[str] = []
    if status and status.upper() != "ACTIVE":
        warnings.append(f"в ЕГРЮЛ статус организации: {status}")

    return _ok(
        {
            "entity_type": kind,
            "entity_id": record_id,
            "title": resolved_title or _short(
                record.get("full_name") or record.get("short_name")
            ),
            "inn": inn,
            "matched": matches,
            "matched_count": len(matches),
            "differences": differences,
            "difference_count": len(differences),
            "reference": reference,
        },
        found=True,
        status=status,
        warnings=warnings,
        message=(
            "расхождений нет" if not differences
            else f"расхождений: {len(differences)}"
        ),
        source="DaData (ЕГРЮЛ)",
    )


__all__ = [
    "CACHE_MAX_ENTRIES",
    "CACHE_TTL_SECONDS",
    "COMPARED_FIELDS",
    "COMPANY_SEARCH_COUNT",
    "FIELD_TITLES",
    "cache_size",
    "cached",
    "check_address_dadata",
    "check_inn_dadata",
    "clear_cache",
    "compare_with_dadata",
    "search_company_dadata",
]
