#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Клиент DaData: реквизиты организации по ИНН, банк по БИК, подразделение ФМС.

Используется API подсказок (suggestions) DaData:

    POST .../rs/findById/party      — организация по ИНН   (find_party_by_inn)
    POST .../rs/findById/bank       — банк по БИК          (find_bank_by_bic)
    POST .../rs/suggest/fms_unit    — подразделение ФМС    (suggest_fms_unit)
    Authorization: Token <ключ>

Ключ берётся из системного хранилища (Windows Credential Manager) —
см. core/secrets_store.py и set_dadata_key.py. В файлах проекта ключа нет.

Секретный ключ (SECRET_KEY, cleaner-методы) хранится отдельно
(set_dadata_secret.py) и этим клиентом пока не используется.

Логирование без персональных данных:
  * ИНН, БИК и код подразделения ФМС пишутся только длиной, не значением;
  * ключ и заголовок Authorization не логируются никогда;
  * тело ответа (название, адрес, ФИО руководителя, банк) не логируется;
  * по ошибкам пишутся только тип исключения и HTTP-статус.

Уровень записи при отказе сервиса — WARNING, а не ERROR. DaData — внешний
сервис: «403 — ключ не принят» или «429 — лимит» это НЕ сбой программы, а
условие, о котором оператор узнаёт и продолжает работу (адрес остаётся таким,
как его ввели; реквизиты заполняются вручную). Запись уровня ERROR попадала бы
в logs/errors.log с присоединённым стеком вызова (config/logging_config.py,
StackInfoFilter) и выглядела бы как падение приложения, хотя приложение
работает. Настоящие ошибки (сеть, TLS, таймаут, не-JSON) тоже WARNING по той
же причине: их обрабатывает вызывающий код, а не программа падает.

Ожидаемая структура ответа DaData (findById/party):

    {
      "suggestions": [
        {
          "value": "ООО «МОТОРИКА»",
          "unrestricted_value": "ООО «МОТОРИКА»",
          "data": {
            "inn": "7719402047",
            "kpp": "772301001",
            "ogrn": "1157746078984",
            "type": "LEGAL" | "INDIVIDUAL",
            "name": {"full_with_opf": "...", "short_with_opf": "..."},
            "address": {"value": "г Москва, ..."},
            "management": {"name": "Давидюк Андрей Павлович",
                           "post": "ГЕНЕРАЛЬНЫЙ ДИРЕКТОР"},
            "fio": {"surname": "...", "name": "...", "patronymic": "..."},
            "state": {"status": "ACTIVE" | "LIQUIDATING" | "LIQUIDATED"
                                | "BANKRUPT" | "REORGANIZING"},
            "phones": [{"value": "+7 495 123-45-67"}],
            "emails": [{"value": "info@example.ru"}]
          }
        }
      ]
    }

Ожидаемая структура ответа findById/bank:

    {
      "suggestions": [
        {
          "value": "ПАО СБЕРБАНК",
          "unrestricted_value": "ПАО СБЕРБАНК",
          "data": {
            "bic": "044525225",
            "swift": "SABRRUMM",
            "inn": "7707083893",
            "kpp": "773601001",
            "correspondent_account": "30101810400000000225",
            "name": {"payment": "ПАО СБЕРБАНК",
                     "full": "ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО ..."},
            "payment_city": "Москва",
            "state": {"status": "ACTIVE"}
          }
        }
      ]
    }

Ожидаемая структура ответа suggest/fms_unit:

    {
      "suggestions": [
        {"value": "Отделом УФМС России по г. Москве по району Хамовники",
         "unrestricted_value": "...", "data": {...}},
        ...
      ]
    }

Пустой "suggestions" означает «ничего не найдено» — это не ошибка:
для организации и банка возвращается None, для ФМС — пустой список.
"""

import logging
import re
from typing import Any, Dict, List, Optional

import requests

from core.utils.text_normalize import normalize_position as _normalize_position
from infrastructure.secrets.store import (
    MISSING_DADATA_KEY_MESSAGE,
    MISSING_DADATA_SECRET_MESSAGE,
    get_dadata_key,
    get_dadata_secret,
)

# Имя логгера задано ЯВНО, а не через __name__ (было бы
# "infrastructure.api.dadata"). Это часть контракта: config/logging_config.py
# настраивает только APP_LOGGERS = ("core", "ui", "db", "config", "main", "qt"),
# и логи из "infrastructure.*" выпали бы из настройки. На старое имя опираются
# и caplog-проверки в тестах.
logger = logging.getLogger("core.dadata_client")

#: Эндпоинт поиска организации по ИНН (ИП — тоже, ИНН из 12 цифр).
FIND_PARTY_URL = (
    "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"
)
#: Эндпоинт поиска банка по БИК (9 цифр).
FIND_BANK_URL = (
    "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/bank"
)
#: Эндпоинт подсказок по подразделениям ФМС (код вида «500-123»).
SUGGEST_FMS_URL = (
    "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/fms_unit"
)
#: Эндпоинт поиска организации по НАЗВАНИЮ (то, как его помнит оператор).
SUGGEST_PARTY_URL = (
    "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party"
)
#: Эндпоинт подсказок по адресу: разбирает адрес и отдаёт готовые части.
SUGGEST_ADDRESS_URL = (
    "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
)
#: Эндпоинт СЕРВИСА ОЧИСТКИ адресов: приводит запись к стандартному виду.
#:
#: Требует СЕКРЕТНЫЙ ключ (заголовок ``X-Secret``) и принимает ровно одну
#: запись за вызов — иначе 400 «Only one record allowed». Проверено живьём
#: 24.10.2026: «Пятигорск Ленина 5» → «Ставропольский край, г Пятигорск,
#: пл Ленина, д 5»; «г москва ул тестовая д 1» → «г Москва».
CLEAN_ADDRESS_URL = "https://cleaner.dadata.ru/api/v1/clean/address"

#: Должность руководителя у индивидуального предпринимателя.
IP_DIRECTOR_POSITION = "Индивидуальный предприниматель"
#: Префикс полного наименования ИП (у DaData нет готовой строки с ОПФ).
IP_NAME_PREFIX = "Индивидуальный предприниматель"

#: Ограничения тарифа DaData — нужны для понятного текста ошибки 429.
RATE_LIMIT_HINT = "10 000/день или 30/сек"

#: Статусы организации (data.state.status) для предупреждения пользователя.
#: ACTIVE — рабочая организация, предупреждать не о чем.
STATUS_TITLES: Dict[str, str] = {
    "ACTIVE": "",
    "LIQUIDATING": "ликвидируется",
    "LIQUIDATED": "ликвидирована",
    "BANKRUPT": "банкротство",
    "REORGANIZING": "в процессе присоединения к другому юрлицу",
}

#: Статусы банка (data.state.status в findById/bank).
#: Отличаются родом: «банк ликвидирован», а не «ликвидирована».
BANK_STATUS_TITLES: Dict[str, str] = {
    "ACTIVE": "",
    "LIQUIDATING": "ликвидируется",
    "LIQUIDATED": "ликвидирован",
    "BANKRUPT": "банкротство",
    "REORGANIZING": "в процессе реорганизации",
}


def status_warning(status: str, subject: str = "организация",
                   titles: Optional[Dict[str, str]] = None) -> str:
    """
    Предупреждение о статусе (п. 3.3 задания).

    :param status: код статуса DaData (data.state.status)
    :param subject: о ком речь — «организация», «банк»
    :param titles: словарь статусов (по умолчанию — для организаций)
    :return: текст предупреждения или пустая строка, если статус рабочий
    """
    code = str(status or "").strip().upper()
    if not code or code == "ACTIVE":
        return ""

    table = titles or STATUS_TITLES
    title = table.get(code) or f"неизвестный статус ({code})"
    title = title[0].upper() + title[1:]
    return (
        f"Внимание: {subject} имеет статус «{title}». "
        f"Проверьте реквизиты перед заключением договора."
    )


def bank_status_warning(status: str) -> str:
    """Предупреждение о статусе банка (ликвидация, банкротство и т.п.)."""
    return status_warning(status, subject="банк", titles=BANK_STATUS_TITLES)


#: Минимальное число цифр в коде подразделения ФМС («500-123» → 6).
FMS_CODE_MIN_DIGITS = 6
#: Формат кода подразделения: три цифры, дефис, три цифры.
FMS_CODE_LENGTH = 7


def normalize_fms_code(code: str) -> str:
    """
    Проверяет и при необходимости приводит код подразделения ФМС к «XXX-XXX».

    :raises ValueError: если цифр в коде меньше FMS_CODE_MIN_DIGITS
    """
    text = str(code or "").strip()
    digits = re.sub(r"\D", "", text)

    if len(digits) < FMS_CODE_MIN_DIGITS:
        raise ValueError(
            "DaData: код подразделения ФМС должен состоять из "
            f"{FMS_CODE_MIN_DIGITS} цифр (например, 500-123)."
        )

    if "-" not in text and len(digits) == FMS_CODE_MIN_DIGITS:
        # «500123» → «500-123»: DaData понимает и так, но формат надёжнее.
        return f"{digits[:3]}-{digits[3:]}"

    return text


def normalize_position(value: Any) -> str:
    """
    Должность из DaData в общепринятый регистр (п. 2 задания шага).

    DaData отдаёт `data.management.post` капсом («ГЕНЕРАЛЬНЫЙ ДИРЕКТОР»):
    это их формат хранения, а не общепринятый. В документах принято
    «Директор», «Генеральный директор».

    Правила — в `core/text_normalize.normalize_position` (общие для
    DaData, справочника и склонения должности, чтобы копии правил не
    разъехались):

      * пустое значение → пустая строка;
      * капс длиннее 3 символов → заглавная у первого содержательного
        слова, остальные строчные, служебные слова («и», «в», «на», «по»,
        «с», «у», «для», «из», «о», «об») — строчными;
      * капс длиной не больше 3 символов («ИП», «ООО», «АО») и любое
        другое значение → как есть.
    """
    return _normalize_position(value)


def _first_value(items: Any) -> str:
    """Первое значение из data.phones / data.emails (или пустая строка)."""
    if not isinstance(items, list):
        return ""
    for item in items:
        if isinstance(item, dict):
            value = str(item.get("value") or "").strip()
        else:
            value = str(item or "").strip()
        if value:
            return value
    return ""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


class DadataClient:
    """Клиент подсказок DaData: реквизиты организации по ИНН."""

    #: Значение branch_type: только головное подразделение, без филиалов.
    BRANCH_TYPE = "MAIN"

    def __init__(self, api_key: Optional[str] = None, timeout: int = 10,
                 secret: Optional[str] = None):
        self.api_key = (api_key or "").strip()

        # ── Ключ: аргумент → системное хранилище ──
        if not self.api_key:
            self.api_key = (get_dadata_key() or "").strip()
            if self.api_key:
                logger.info("Ключ DaData получен из системного хранилища")

        # ── Секретный ключ: нужен ТОЛЬКО очистке адресов ──
        # Он необязателен: без него работает всё остальное (реквизиты по
        # ИНН, поиск по названию, банк, ФМС), а clean_address() честно
        # скажет, что секрета нет.
        self.secret = (secret or "").strip()
        if not self.secret:
            self.secret = (get_dadata_secret() or "").strip()
            if self.secret:
                logger.info("Секретный ключ DaData получен из системного хранилища")

        self.timeout = timeout

        if not self.api_key:
            raise ValueError(MISSING_DADATA_KEY_MESSAGE)

        logger.debug("DadataClient инициализирован: timeout=%s с", timeout)

    # ---------------------------------------------------------
    # Поиск организации
    # ---------------------------------------------------------
    def find_party_by_inn(self, inn: str) -> Optional[Dict[str, Any]]:
        """
        Ищет организацию по ИНН через DaData.

        :return: словарь с полями (см. ниже) или None, если не найдено
        :raises ValueError: если ИНН не передан
        :raises RuntimeError: при сетевой ошибке / ошибке API
        """
        inn = (inn or "").strip()
        if not inn:
            raise ValueError("DaData: ИНН не указан — запрос не отправлен.")

        # В лог идёт только длина: ИНН — идентификатор организации,
        # по нему находится и руководитель, поэтому сам номер не пишем.
        logger.info("DaData: запрос по ИНН (длина=%s)", len(inn))

        payload = {"query": inn, "branch_type": self.BRANCH_TYPE}
        suggestions = self._request_suggestions(FIND_PARTY_URL, payload)
        if not suggestions:
            logger.info("DaData: ничего не найдено (пустой suggestions)")
            return None

        suggestion = suggestions[0]
        if not isinstance(suggestion, dict):
            logger.warning("DaData: неожиданный формат подсказки — пропускаем")
            return None

        parsed = self._parse_suggestion(suggestion)
        if parsed is None:
            logger.info("DaData: в подсказке нет реквизитов — считаем, что не найдено")
        return parsed

    # ---------------------------------------------------------
    # Поиск организации по названию
    # ---------------------------------------------------------
    def suggest_parties(self, query: str, count: int = 5,
                        active_only: bool = True) -> List[Dict[str, Any]]:
        """
        Ищет организации по НАЗВАНИЮ (не по ИНН).

        Отличие от ``find_party_by_inn``: там запрос — точный
        идентификатор (ИНН или ОГРН), а здесь — то, как название помнит
        оператор: «ФассТранс», «ромашка», «сбербанк». Отвечает DaData
        списком подсказок, отсортированных по близости, поэтому
        возвращается СПИСОК, а не одна запись: выбирать — дело оператора.

        :param query: название словами;
        :param count: сколько подсказок запросить (1..10);
        :param active_only: True — только действующие организации;
        :return: список разобранных карточек (см. ``_parse_suggestion``);
                 пустой список, если ничего не найдено
        :raises ValueError: если название не передано
        :raises RuntimeError: при сетевой ошибке / ошибке API
        """
        text = (query or "").strip()
        if not text:
            raise ValueError("DaData: название не указано — запрос не отправлен.")

        # Само название в лог не пишем: это может быть фамилия («Иванов»)
        # или адрес, а лог обезличен. Длины достаточно для диагностики.
        logger.info("DaData: поиск организации по названию (длина=%s)", len(text))

        payload: Dict[str, Any] = {
            "query": text,
            "count": max(1, min(10, int(count))),
            "branch_type": self.BRANCH_TYPE,
        }
        if active_only:
            # Действующие: ликвидированные и обанкротившиеся в справочник
            # перевозчиков не нужны, они только путают выдачу.
            payload["status"] = ["ACTIVE"]

        suggestions = self._request_suggestions(SUGGEST_PARTY_URL, payload)
        found: List[Dict[str, Any]] = []
        for suggestion in suggestions:
            if not isinstance(suggestion, dict):
                continue
            parsed = self._parse_suggestion(suggestion)
            if parsed is not None:
                found.append(parsed)
        logger.info("DaData: найдено организаций по названию: %s", len(found))
        return found

    # ---------------------------------------------------------
    # Стандартизация адреса
    # ---------------------------------------------------------
    def clean_address(self, address: str) -> Optional[Dict[str, Any]]:
        """
        Приводит адрес к стандартному виду через сервис очистки DaData.

        Почему отдельный сервис. Подсказки по адресу (suggest/address)
        отдают город и улицу только когда DaData уверена в разборе, а на
        бытовой записи «г москва ул тестовая д 1» возвращают пустой
        список — проверено живьём 24.10.2026. Сервис очистки
        (cleaner.dadata.ru/api/v1/clean/address) разбирает такую запись
        и отдаёт готовую строку:

            «г москва ул тестовая д 1» → «г Москва»
            «Пятигорск Ленина 5» → «Ставропольский край, г Пятигорск,
                                    пл Ленина, д 5»

        ВАЖНО: очистка требует СЕКРЕТНЫЙ ключ (заголовок ``X-Secret``) и
        принимает РОВНО ОДНУ запись за запрос — иначе отвечает
        400 «Only one record allowed». Секретный ключ хранится в системном
        хранилище рядом с обычным (core/secrets_store.py).

        :param address: адрес как его ввёл оператор;
        :return: словарь с полями ``value`` (стандартный вид), ``city``,
            ``street``, ``house``, ``flat``, ``region``, ``postal_code``,
            ``fias_id``, ``geo_lat``, ``geo_lon``, ``qc_geo`` (точность
            определения координат: 0 — точно, выше — приблизительно);
            None, если разобрать не удалось
        :raises ValueError: если адрес не передан
        :raises RuntimeError: при сетевой ошибке / ошибке API
        """
        text = (address or "").strip()
        if not text:
            raise ValueError("DaData: адрес не указан — запрос не отправлен.")

        secret = (self.secret or "").strip()
        if not secret:
            raise RuntimeError(MISSING_DADATA_SECRET_MESSAGE)

        # Адрес — персональные данные: в лог уходит только длина.
        logger.info("DaData: очистка адреса (длина=%s)", len(text))

        headers = self._headers()
        headers["X-Secret"] = secret
        # Одна запись за запрос: сервис очистки иначе отвечает 400
        # «Only one record allowed». Ответ — МАССИВ разобранных записей.
        response = self._post(CLEAN_ADDRESS_URL, headers, [text])
        status = response.status_code
        logger.debug("DaData: ответ очистки адреса | статус=%s", status)

        if status in (401, 403):
            logger.warning(
                "DaData: HTTP %s — ключ или секрет не принят; "
                "адрес остаётся как ввёл оператор", status,
            )
            raise RuntimeError(
                "DaData: неверный или отозванный ключ (секрет) DaData.\n\n"
                "Проверьте ключи: python set_dadata_key.py --check и "
                "python set_dadata_secret.py --check"
            )
        if status == 429:
            logger.warning(
                "DaData: HTTP 429 — превышен лимит запросов; "
                "адрес остаётся как ввёл оператор",
            )
            raise RuntimeError(
                f"DaData: превышен лимит запросов ({RATE_LIMIT_HINT}). "
                f"Подождите и повторите."
            )
        if 500 <= status < 600:
            logger.warning(
                "DaData: HTTP %s — сервер недоступен; "
                "адрес остаётся как ввёл оператор", status,
            )
            raise RuntimeError(
                f"DaData: сервер DaData недоступен (HTTP {status}), "
                f"повторите позже."
            )
        if not 200 <= status < 300:
            logger.warning(
                "DaData: HTTP %s — неожиданный ответ очистки; "
                "адрес остаётся как ввёл оператор", status,
            )
            raise RuntimeError(
                f"DaData: ошибка API очистки адреса (HTTP {status}). "
                f"Подробности — в logs/errors.log."
            )

        try:
            body = response.json()
        except ValueError as exc:
            logger.warning("DaData: ответ очистки не является JSON (%s)",
                           type(exc).__name__)
            raise RuntimeError(
                "DaData: некорректный ответ API (не JSON). Повторите позже."
            ) from exc

        if not isinstance(body, list) or not body:
            logger.info("DaData: адрес не разобран (пустой ответ очистки)")
            return None

        item = body[0]
        if not isinstance(item, dict):
            logger.warning("DaData: неожиданный формат ответа очистки адреса")
            return None

        value = _clean(item.get("result"))
        if not value:
            logger.info("DaData: в ответе очистки нет разобранного адреса")
            return None

        return {
            "value": value,
            "city": _clean(item.get("city")),
            "street": _clean(item.get("street_with_type") or item.get("street")),
            "house": _clean(item.get("house")),
            "flat": _clean(item.get("flat")),
            "region": _clean(item.get("region_with_type") or item.get("region")),
            "postal_code": _clean(item.get("postal_code")),
            "fias_id": _clean(item.get("fias_id")),
            "geo_lat": _clean(item.get("geo_lat")),
            "geo_lon": _clean(item.get("geo_lon")),
            "qc_geo": item.get("qc_geo"),
        }

    # ---------------------------------------------------------
    # Поиск банка по БИК
    # ---------------------------------------------------------
    def find_bank_by_bic(self, bic: str) -> Optional[Dict[str, Any]]:
        """
        Ищет банк по БИК через DaData.

        :param bic: 9 цифр
        :return: плоский dict или None, если не найдено
        :raises ValueError: если БИК не похож на 9 цифр
        :raises RuntimeError: при сетевой ошибке / ошибке API
        """
        bic = (bic or "").strip()
        if not re.fullmatch(r"\d{9}", bic):
            raise ValueError(
                "DaData: БИК должен состоять из 9 цифр — запрос не отправлен."
            )

        # В лог идёт только длина: БИК не пишем даже открытым видом.
        logger.info("DaData: запрос банка по БИК (длина=%s)", len(bic))

        payload = {"query": bic}
        suggestions = self._request_suggestions(FIND_BANK_URL, payload)
        if not suggestions:
            logger.info("DaData: банк не найден (пустой suggestions)")
            return None

        suggestion = suggestions[0]
        if not isinstance(suggestion, dict):
            logger.warning("DaData: неожиданный формат подсказки банка")
            return None

        parsed = self._parse_bank_suggestion(suggestion)
        if parsed is None:
            logger.info("DaData: в подсказке нет реквизитов банка")
        return parsed

    # ---------------------------------------------------------
    # Подразделение ФМС по коду
    # ---------------------------------------------------------
    def suggest_fms_unit(self, code: str) -> List[Dict[str, Any]]:
        """
        Ищет подразделение ФМС по коду подразделения паспорта.

        :param code: код вида "500-123" или "500123"
        :return: список подсказок [{value, unrestricted_value, data}, ...];
                 пустой список, если ничего не найдено
        :raises ValueError: если в коде меньше 6 цифр
        :raises RuntimeError: при сетевой ошибке / ошибке API
        """
        normalized = normalize_fms_code(code)

        # Сам код не логируем: он привязан к конкретному отделению и региону.
        logger.info("DaData: запрос подразделения ФМС (длина кода=%s)",
                    len(normalized))

        payload = {"query": normalized}
        suggestions = self._request_suggestions(SUGGEST_FMS_URL, payload)
        if not suggestions:
            logger.info("DaData: подразделение ФМС не найдено (пустой suggestions)")
            return []

        # UI работает в первую очередь с value, но data отдаём как есть —
        # у части подсказок ФМС его может не быть вовсе.
        return [item for item in suggestions if isinstance(item, dict)]

    # ---------------------------------------------------------
    # Транспорт
    # ---------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        """Заголовки запроса (ключ никогда не попадает в лог)."""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Token {self.api_key}",
        }

    def _post(self, url: str, headers: Dict[str, str],
              payload: Dict[str, Any]):
        """Отправляет запрос и разбирает сетевые сбои (без ключа в логах)."""
        endpoint = url.rsplit("/", 2)[-2:]
        endpoint = "/".join(endpoint) or url
        try:
            return requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.exceptions.SSLError as exc:
            logger.error(
                "DaData: %s при запросе к %s", type(exc).__name__, endpoint
            )
            raise RuntimeError(
                "DaData: ошибка TLS-соединения.\n\n"
                "Проверьте системные сертификаты, антивирус и прокси — "
                "они часто подменяют HTTPS-сертификат."
            ) from exc
        except requests.exceptions.Timeout as exc:
            logger.error(
                "DaData: %s при запросе к %s (timeout=%s с)",
                type(exc).__name__, endpoint, self.timeout,
            )
            raise RuntimeError(
                f"DaData: превышено время ожидания ответа ({self.timeout} с). "
                f"Повторите позже."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            logger.error(
                "DaData: %s при запросе к %s", type(exc).__name__, endpoint
            )
            raise RuntimeError(
                "DaData: нет соединения с dadata.ru.\n\n"
                "Проверьте интернет, VPN и файрвол."
            ) from exc
        except (requests.exceptions.RequestException, OSError) as exc:
            logger.error(
                "DaData: %s при запросе к %s", type(exc).__name__, endpoint
            )
            raise RuntimeError(
                f"DaData: не удалось выполнить запрос "
                f"({type(exc).__name__}). Подробности — в logs/errors.log."
            ) from exc

    def _request_suggestions(self, url: str,
                             payload: Dict[str, Any]) -> List[Any]:
        """
        Выполняет запрос и возвращает список suggestions.

        Проверяет HTTP-статус (401/403, 429, 5xx, прочие) и разбирает JSON.
        Пустой/отсутствующий suggestions — это пустой список, то есть
        «ничего не найдено», а не ошибка.
        """
        response = self._post(url, self._headers(), payload)
        status = response.status_code
        logger.debug("DaData: ответ получен | статус=%s", status)

        if status in (401, 403):
            logger.warning("DaData: HTTP %s — ключ не принят", status)
            raise RuntimeError(
                "DaData: неверный или отозванный ключ DaData.\n\n"
                "Проверьте ключ командой: python set_dadata_key.py --check"
            )
        if status == 429:
            logger.warning("DaData: HTTP 429 — превышен лимит запросов")
            raise RuntimeError(
                f"DaData: превышен лимит запросов ({RATE_LIMIT_HINT}). "
                f"Подождите и повторите."
            )
        if 500 <= status < 600:
            logger.warning("DaData: HTTP %s — сервер недоступен", status)
            raise RuntimeError(
                f"DaData: сервер DaData недоступен (HTTP {status}), "
                f"повторите позже."
            )
        if not 200 <= status < 300:
            logger.warning("DaData: HTTP %s — неожиданный ответ", status)
            raise RuntimeError(
                f"DaData: ошибка API (HTTP {status}). "
                f"Подробности — в logs/errors.log."
            )

        try:
            body = response.json()
        except ValueError as exc:
            logger.warning("DaData: ответ не является JSON (%s)",
                           type(exc).__name__)
            raise RuntimeError(
                "DaData: некорректный ответ API (не JSON). "
                "Повторите позже."
            ) from exc

        suggestions = body.get("suggestions") if isinstance(body, dict) else None
        if not isinstance(suggestions, list):
            return []
        return suggestions

    @staticmethod
    def _parse_suggestion(suggestion: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Превращает элемент suggestions в плоский словарь нужных приложению полей.

        Для ИП (data.type == "INDIVIDUAL") КПП не существует, должность —
        «Индивидуальный предприниматель», а ФИО собирается из data.fio.

        Нормализуется ТОЛЬКО должность (`normalize_position`): у DaData она
        лежит капсом («ГЕНЕРАЛЬНЫЙ ДИРЕКТОР»). Остальные поля отдаются как
        есть — `full_name`, `short_name`, `director_name`, `legal_address`,
        `bank_name`. Для наименования DaData авторитетный источник (данные
        ЕГРЮЛ): «ООО "АВАТЭК"» капсом — это реестр, а не формат источника,
        и «ООО "Аватэк"» было бы уже другим наименованием.
        """
        data = suggestion.get("data")
        if not isinstance(data, dict):
            data = {}

        entity_type = _clean(data.get("type")).upper()

        if entity_type == "INDIVIDUAL":
            fio = data.get("fio")
            if not isinstance(fio, dict):
                fio = {}
            person = _clean(" ".join(
                _clean(fio.get(key))
                for key in ("surname", "name", "patronymic")
            ))
            full_name = _clean(f"{IP_NAME_PREFIX} {person}")
            short_name = full_name
            director_name = person
            director_position = IP_DIRECTOR_POSITION
            kpp = ""
        else:
            name = data.get("name")
            if not isinstance(name, dict):
                name = {}
            full_name = _clean(name.get("full_with_opf")) or _clean(suggestion.get("value"))
            short_name = _clean(name.get("short_with_opf")) or full_name

            management = data.get("management")
            if not isinstance(management, dict):
                management = {}
            director_name = _clean(management.get("name"))
            # DaData отдаёт должность капсом («ГЕНЕРАЛЬНЫЙ ДИРЕКТОР») — это
            # формат источника, а не общепринятый. В договоре нужно
            # «Генеральный директор»; сокращения («ИП», «ООО») не трогаются.
            director_position = _normalize_position(management.get("post"))
            kpp = _clean(data.get("kpp"))

        address = data.get("address")
        if not isinstance(address, dict):
            address = {}
        state = data.get("state")
        if not isinstance(state, dict):
            state = {}

        result = {
            "full_name": full_name,
            "short_name": short_name,
            "inn": _clean(data.get("inn")),
            "kpp": kpp,
            "ogrn": _clean(data.get("ogrn")),
            "legal_address": _clean(address.get("value")),
            "director_name": director_name,
            "director_position": director_position,
            "entity_type": entity_type,
            "status": _clean(state.get("status")).upper(),
            "phone": _first_value(data.get("phones")),
            "email": _first_value(data.get("emails")),
        }

        # Подсказка без названия и без ИНН бесполезна: считаем, что не найдено.
        if not result["full_name"] and not result["inn"]:
            return None
        return result

    @staticmethod
    def _parse_bank_suggestion(suggestion: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Превращает элемент suggestions (findById/bank) в плоский словарь.

        Приложению нужны два поля — название банка и корреспондентский счёт;
        остальное (swift, город, статус) отдаём для справки и предупреждений.
        """
        data = suggestion.get("data")
        if not isinstance(data, dict):
            data = {}

        name = data.get("name")
        if not isinstance(name, dict):
            name = {}
        state = data.get("state")
        if not isinstance(state, dict):
            state = {}

        # Если data.name нет, годится value подсказки («ПАО СБЕРБАНК»).
        fallback_name = _clean(suggestion.get("value"))

        result = {
            "bic": _clean(data.get("bic")),
            "bank_name": _clean(name.get("payment")) or fallback_name,
            "correspondent_account": _clean(data.get("correspondent_account")),
            "swift": _clean(data.get("swift")),
            "payment_city": _clean(data.get("payment_city")),
            "state": _clean(state.get("status")).upper(),
        }

        # Без названия и без БИК подсказка бесполезна: «не найдено».
        if not result["bank_name"] and not result["bic"]:
            return None
        return result


def find_party_by_inn(inn: str, api_key: Optional[str] = None,
                      timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Удобная обёртка: разовый поиск организации по ИНН."""
    return DadataClient(api_key=api_key, timeout=timeout).find_party_by_inn(inn)


def find_bank_by_bic(bic: str, api_key: Optional[str] = None,
                     timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Удобная обёртка: разовый поиск банка по БИК."""
    return DadataClient(api_key=api_key, timeout=timeout).find_bank_by_bic(bic)


def suggest_fms_unit(code: str, api_key: Optional[str] = None,
                     timeout: int = 10) -> List[Dict[str, Any]]:
    """Удобная обёртка: разовые подсказки по коду подразделения ФМС."""
    return DadataClient(api_key=api_key, timeout=timeout).suggest_fms_unit(code)


def suggest_parties(query: str, api_key: Optional[str] = None,
                    timeout: int = 10, count: int = 5,
                    active_only: bool = True) -> List[Dict[str, Any]]:
    """Удобная обёртка: разовый поиск организаций по названию."""
    return DadataClient(api_key=api_key, timeout=timeout).suggest_parties(
        query, count=count, active_only=active_only
    )


def suggest_addresses(query: str, api_key: Optional[str] = None,
                      timeout: int = 10, count: int = 5) -> List[Dict[str, Any]]:
    """Удобная обёртка: разовые подсказки по адресу (разбор на части)."""
    return DadataClient(api_key=api_key, timeout=timeout).suggest_addresses(
        query, count=count
    )


def clean_address(address: str, api_key: Optional[str] = None,
                  secret: Optional[str] = None,
                  timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Удобная обёртка: разовая стандартизация адреса (нужен секрет)."""
    return DadataClient(api_key=api_key, timeout=timeout,
                        secret=secret).clean_address(address)


__all__: List[str] = [
    "BANK_STATUS_TITLES",
    "CLEAN_ADDRESS_URL",
    "DadataClient",
    "FIND_BANK_URL",
    "FIND_PARTY_URL",
    "FMS_CODE_MIN_DIGITS",
    "IP_DIRECTOR_POSITION",
    "STATUS_TITLES",
    "SUGGEST_ADDRESS_URL",
    "SUGGEST_FMS_URL",
    "SUGGEST_PARTY_URL",
    "bank_status_warning",
    "clean_address",
    "find_bank_by_bic",
    "find_party_by_inn",
    "normalize_fms_code",
    "normalize_position",
    "status_warning",
    "suggest_addresses",
    "suggest_fms_unit",
    "suggest_parties",
    "_normalize_position",
]
