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

from core.secrets_store import MISSING_DADATA_KEY_MESSAGE, get_dadata_key

logger = logging.getLogger(__name__)

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

    def __init__(self, api_key: Optional[str] = None, timeout: int = 10):
        self.api_key = (api_key or "").strip()

        # ── Ключ: аргумент → системное хранилище ──
        if not self.api_key:
            self.api_key = (get_dadata_key() or "").strip()
            if self.api_key:
                logger.info("Ключ DaData получен из системного хранилища")

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
            logger.error("DaData: HTTP %s — ключ не принят", status)
            raise RuntimeError(
                "DaData: неверный или отозванный ключ DaData.\n\n"
                "Проверьте ключ командой: python set_dadata_key.py --check"
            )
        if status == 429:
            logger.error("DaData: HTTP 429 — превышен лимит запросов")
            raise RuntimeError(
                f"DaData: превышен лимит запросов ({RATE_LIMIT_HINT}). "
                f"Подождите и повторите."
            )
        if 500 <= status < 600:
            logger.error("DaData: HTTP %s — сервер недоступен", status)
            raise RuntimeError(
                f"DaData: сервер DaData недоступен (HTTP {status}), "
                f"повторите позже."
            )
        if not 200 <= status < 300:
            logger.error("DaData: HTTP %s — неожиданный ответ", status)
            raise RuntimeError(
                f"DaData: ошибка API (HTTP {status}). "
                f"Подробности — в logs/errors.log."
            )

        try:
            body = response.json()
        except ValueError as exc:
            logger.error("DaData: ответ не является JSON (%s)", type(exc).__name__)
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
            director_position = _clean(management.get("post"))
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


__all__: List[str] = [
    "BANK_STATUS_TITLES",
    "DadataClient",
    "FIND_BANK_URL",
    "FIND_PARTY_URL",
    "FMS_CODE_MIN_DIGITS",
    "IP_DIRECTOR_POSITION",
    "STATUS_TITLES",
    "SUGGEST_FMS_URL",
    "bank_status_warning",
    "find_bank_by_bic",
    "find_party_by_inn",
    "normalize_fms_code",
    "status_warning",
    "suggest_fms_unit",
]
