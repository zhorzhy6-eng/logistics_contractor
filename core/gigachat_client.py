import json
import logging
import re
import time
import uuid
from typing import Optional

import requests

from core.secrets_store import MISSING_KEY_MESSAGE, get_gigachat_key
from core.tls_config import default_ca_bundle, describe_tls, ssl_error_hint

logger = logging.getLogger(__name__)


# ============================================================
# БЕЗОПАСНЫЙ ПАРСИНГ JSON
# ============================================================
def _clean_and_parse_json(raw_text: str) -> dict:
    """
    Очищает ответ модели от markdown-оберток и лишнего текста,
    затем парсит JSON.

    Учитывает особенность GigaChat-2: иногда он возвращает
    {{ ... }} вместо { ... } (экранирует фигурные скобки из промпта).
    """
    if not raw_text:
        raise RuntimeError("Пустой ответ от GigaChat")

    text = raw_text.strip()

    # 1. Убираем markdown-обертки ```json ... ``` и ``` ... ```
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*```$', '', text)

    # 2. Убираем префиксы вроде "Ответ:", "JSON:", "Результат:"
    text = re.sub(r'^(?:Ответ|JSON|Результат)\s*:\s*', '', text, flags=re.IGNORECASE)

    # 3. Ищем первую { и последнюю }
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    # 4. FIX: GigaChat-2 иногда возвращает {{ ... }} вместо { ... }
    if text.startswith("{{") and text.endswith("}}"):
        logger.warning(
            "GigaChat вернул двойные фигурные скобки {{ }} — "
            "исправляем на одинарные { }"
        )
        text = text[1:-1]

    # 5. Парсим
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # Содержимое ответа содержит персональные данные — в лог не попадает,
        # для диагностики достаточно объёма и места ошибки.
        logger.error(
            f"Ответ GigaChat не является JSON | символов: {len(raw_text)} | "
            f"строк: {raw_text.count(chr(10)) + 1} | ошибка: {e.msg} "
            f"(строка {e.lineno}, позиция {e.colno}); содержимое не логируется"
        )
        raise RuntimeError(f"Модель вернула невалидный JSON: {e}")


# ============================================================
# КЛИЕНТ GIGACHAT
# ============================================================
class GigaChatClient:
    BASE_URL = "https://api.giga.chat/v1"
    AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

    # ── Повторы и ограничения (Шаг 7 оптимизации) ──
    # При 429/5xx/таймауте повторяем с задержкой 1с → 2с → 4с.
    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 1.0
    RETRYABLE_STATUSES = (429, 500, 502, 503, 504)
    # Ограничение длины текста, отправляемого в модель: слишком большой
    # промпт GigaChat отклоняет, а платим за него токенами.
    MAX_PROMPT_CHARS = 60000
    # Запрос дольше этого времени помечается в логе как SLOW
    SLOW_REQUEST_SECONDS = 5.0

    SYSTEM_PROMPT = (
        "Ты — парсер данных для логистической компании. "
        "Твоя задача — извлечь структурированные данные из текста и вернуть их "
        "СТРОГО в формате JSON. "
        "НЕ используй markdown. НЕ используй ```json```. "
        "НЕ добавляй пояснений и комментариев. "
        "НЕ добавляй текст до или после JSON. "
        "Ответ должен начинаться с символа открывающей фигурной скобки "
        "и заканчиваться символом закрывающей фигурной скобки. "

        # ── ГЛАВНОЕ ПРАВИЛО ПРО ПЕРЕВОЗЧИКА ──
        "ВАЖНО! По умолчанию ВСЕ реквизиты организации из текста — это "
        "РЕКВИЗИТЫ ПЕРЕВОЗЧИКА. Клади их в блок \"carrier\", НЕ в \"customer\". "
        "Блок \"customer\" оставляй ПУСТЫМ (все поля = \"\") — "
        "исключение: только если в тексте явно написано слово «Заказчик» "
        "(с двоеточием или без). "

        "Если в тексте «ИП» или «Индивидуальный предприниматель» — "
        "это ВСЕГДА реквизиты ПЕРЕВОЗЧИКА. Клади их в \"carrier\": "
        "entity_type = \"ИП\", "
        "full_name = \"Индивидуальный предприниматель ФИО\" или как в тексте, "
        "kpp = \"\" (у ИП нет КПП), "
        "ogrn = ОГРНИП (15 цифр), "
        "director_name = ФИО ИП, "
        "director_position = \"Индивидуальный предприниматель\". "

        "Если в тексте «ООО» или «Общество с ограниченной ответственностью» — "
        "тоже клади в \"carrier\": entity_type = \"ООО\", "
        "ogrn = ОГРН (13 цифр), kpp = КПП (9 цифр). "

        # ── Правила по типам данных ──
        "НДС в РФ: 22% (НЕ 20%!). Если явно не указано иное — ставь \"22%\". "
        "Тягач и полуприцеп НЕ добавляй в массив \"vehicles\" — "
        "они идут в отдельные блоки \"tractor\" и \"trailer\". "
        "Если данных нет — пустая строка \"\" (НЕ null, НЕ \"null\", НЕ \"None\"). "
        "НЕ придумывай даты! Если даты нет — оставляй \"\". "
        "Год выпуска ТС: если НЕ указан явно — ставь 0 (программа сама подставит текущий год). "

        "Схема ответа: "
        "{"
        "\"driver\": {\"full_name\":\"\", \"birth_date\":\"\", \"birth_place\":\"\", "
        "\"passport_series\":\"\", \"passport_number\":\"\", \"passport_issue_date\":\"\", "
        "\"passport_issuer\":\"\", \"passport_code\":\"\", \"registration_address\":\"\", "
        "\"license_series\":\"\", \"license_number\":\"\", \"license_issue_date\":\"\", "
        "\"license_expiry_date\":\"\", \"license_categories\":\"\", \"phone\":\"\"}, "
        "\"customer\": {\"full_name\":\"\", \"short_name\":\"\", \"inn\":\"\", \"kpp\":\"\", "
        "\"ogrn\":\"\", \"legal_address\":\"\", \"actual_address\":\"\", \"bank_account\":\"\", "
        "\"bik\":\"\", \"correspondent_account\":\"\", \"bank_name\":\"\", \"director_name\":\"\", "
        "\"director_position\":\"\", \"phone\":\"\", \"email\":\"\", \"entity_type\":\"\"}, "
        "\"carrier\": {\"full_name\":\"\", \"short_name\":\"\", \"inn\":\"\", \"kpp\":\"\", "
        "\"ogrn\":\"\", \"legal_address\":\"\", \"actual_address\":\"\", \"bank_account\":\"\", "
        "\"bik\":\"\", \"correspondent_account\":\"\", \"bank_name\":\"\", \"director_name\":\"\", "
        "\"director_position\":\"\", \"phone\":\"\", \"email\":\"\", \"entity_type\":\"\"}, "
        "\"vehicles\": [{\"vin\":\"\", \"brand_model\":\"\", \"plate_number\":\"\", "
        "\"year\":0, \"color\":\"\", \"vehicle_type\":\"\"}], "
        "\"tractor\": {\"brand_model\":\"\", \"plate_number\":\"\", \"color\":\"\", \"year\":0}, "
        "\"trailer\": {\"brand_model\":\"\", \"plate_number\":\"\", \"color\":\"\", \"year\":0}, "
        "\"contract\": {\"number\":\"\", \"date\":\"\", \"loading_address\":\"\", "
        "\"loading_date\":\"\", \"unloading_address\":\"\", \"unloading_date\":\"\", "
        "\"route\":\"\", \"price_without_vat\":0.0, \"vat_rate\":\"22%\", "
        "\"payment_days\":10, \"special_conditions\":\"\"}"
        "}"
    )

    def __init__(self, auth_key: Optional[str] = None,
                 scope: str = "GIGACHAT_API_PERS",
                 model: str = "GigaChat-2",
                 timeout: int = 300,
                 verify_ssl: bool = True,
                 ca_bundle: Optional[str] = None):
        self.auth_key = (auth_key or "").strip()

        # ── Ключ: аргумент → системное хранилище (Шаг 2 задания) ──
        # В config/settings.json ключа больше нет: он лежит в Windows
        # Credential Manager (см. core/secrets_store.py, set_key.py).
        if not self.auth_key:
            self.auth_key = (get_gigachat_key() or "").strip()
            if self.auth_key:
                logger.info("Ключ GigaChat получен из системного хранилища")

        self.scope = scope
        self.model = model
        self.timeout = timeout
        # ── TLS: проверка сертификата включена (Шаг 3 задания) ──
        # ca_bundle — путь к корневому сертификату (например, НУЦ Минцифры);
        # если явный путь не задан, ищем файл в resources/certs/.
        self.verify_ssl = verify_ssl
        self.ca_bundle = (ca_bundle or "").strip() or default_ca_bundle()

        self._token = None
        self._token_expires = 0

        # ── Ранняя валидация ключа ──
        if not self.auth_key:
            raise ValueError(MISSING_KEY_MESSAGE)
        try:
            self.auth_key.encode("ascii")
        except UnicodeEncodeError as e:
            raise ValueError(
                f"auth_key содержит не-ASCII символы (позиция {e.start}). "
                f"HTTP-заголовок Authorization допускает только latin-1, "
                f"поэтому ключ должен быть чистым Base64. "
                f"Проверьте ключ командой: python set_key.py --check"
            )

        logger.info(
            f"GigaChatClient инициализирован: model={model}, "
            f"scope={scope}, timeout={timeout}, "
            f"{describe_tls(self.verify_ssl, self.ca_bundle)}"
        )

    def _verify(self):
        """Значение параметра verify для requests (Шаг 3 задания)."""
        if not self.verify_ssl:
            return False
        return self.ca_bundle or True

    # --------------------------------------------------------
    # ТОКЕН
    # --------------------------------------------------------
    def _invalidate_token(self) -> None:
        """
        Сбрасывает кэшированный токен (Шаг 7).

        Нужно при ответе 401: сервер мог отозвать токен до истечения
        срока, и без сброса клиент оставался «протухшим» до перезапуска.
        """
        self._token = None
        self._token_expires = 0
        logger.info("Токен GigaChat сброшен — будет получен новый")

    def _get_token(self, force: bool = False) -> str:
        if force:
            self._invalidate_token()

        if self._token and time.time() < self._token_expires - 60:
            return self._token

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {self.auth_key}",
        }
        data = {"scope": self.scope}

        # Подробности авторизации идут только в logs/debug.log.
        # Сам ключ и токен не логируются никогда.
        logger.debug(
            f"GigaChat auth: запрос токена | scope={self.scope} | timeout=30 с"
        )
        started = time.perf_counter()

        try:
            resp = requests.post(
                self.AUTH_URL,
                headers=headers,
                data=data,
                timeout=30,
                verify=self._verify(),
            )
        except requests.exceptions.Timeout:
            raise RuntimeError("GigaChat: таймаут при получении токена (30 сек)")
        except requests.exceptions.SSLError as e:
            logger.error(f"Ошибка TLS при авторизации GigaChat: {e}")
            raise RuntimeError(f"{ssl_error_hint()}\n\nТехническая деталь: {e}")
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(f"Не удалось подключиться к GigaChat (auth): {e}")

        auth_elapsed = time.perf_counter() - started
        logger.debug(
            f"GigaChat auth: ответ | статус={resp.status_code} | "
            f"время={auth_elapsed * 1000:.0f} мс"
        )
        if auth_elapsed >= self.SLOW_REQUEST_SECONDS:
            logger.warning(
                f"SLOW GigaChat auth: получение токена заняло "
                f"{auth_elapsed:.1f} с (порог {self.SLOW_REQUEST_SECONDS:.0f} с)"
            )

        if resp.status_code == 401:
            raise RuntimeError(
                "GigaChat: 401 при авторизации. Ключ неверен или отозван. "
                "Проверьте ключ: python set_key.py --check"
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Ошибка авторизации GigaChat: {resp.status_code} {resp.text[:300]}"
            )

        token_data = resp.json()
        self._token = token_data["access_token"]
        self._token_expires = token_data["expires_at"]
        logger.info("Токен GigaChat успешно получен")
        logger.debug(
            f"GigaChat auth: токен действителен до "
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._token_expires))}"
        )
        return self._token

    # --------------------------------------------------------
    # РАСПОЗНАВАНИЕ
    # --------------------------------------------------------
    def _limit_prompt(self, text: str) -> str:
        """
        Ограничивает длину текста, отправляемого в модель (Шаг 7).

        Обрезка идёт по границе строки, чтобы не разорвать реквизит
        посередине. О факте обрезки пишем в лог — это видно пользователю
        в logs/app.log.
        """
        if len(text) <= self.MAX_PROMPT_CHARS:
            return text

        truncated = text[:self.MAX_PROMPT_CHARS]
        cut = truncated.rfind("\n")
        if cut > self.MAX_PROMPT_CHARS // 2:
            truncated = truncated[:cut]

        logger.warning(
            f"Текст слишком длинный ({len(text)} символов) — "
            f"отправляем первые {len(truncated)} символов"
        )
        return truncated

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """Задержка перед повтором: 1с → 2с → 4с."""
        return GigaChatClient.RETRY_BASE_DELAY * (2 ** attempt)

    def recognize_text(self, text: str) -> dict:
        """
        Отправляет текст в GigaChat и возвращает разобранный JSON.

        Шаг 7 оптимизации:
          * при 401 токен сбрасывается и запрос повторяется один раз
            (раньше клиент оставался с «протухшим» токеном до перезапуска);
          * при 429/5xx и таймауте — до MAX_RETRIES повторов с задержкой
            1с → 2с → 4с;
          * слишком длинный текст обрезается по границе строки.
        """
        prompt = self._limit_prompt(text)
        url = f"{self.BASE_URL}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
            # GigaChat НЕ поддерживает response_format: json_object!
        }

        logger.info(f"Отправка текста в GigaChat ({len(prompt)} символов)...")
        logger.debug(
            f"Распознавание старт | вход: {len(text)} символов | "
            f"промпт: {len(prompt)} символов (лимит {self.MAX_PROMPT_CHARS}) | "
            f"модель={self.model} | temperature={payload['temperature']} | "
            f"max_tokens={payload['max_tokens']}"
        )

        auth_retry_used = False
        # Номер фактического повтора: задержка всегда 1с → 2с → 4с,
        # независимо от того, что было причиной (401, 429 или таймаут).
        retry_index = 0

        for attempt in range(self.MAX_RETRIES + 1):
            token = self._get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

            # ── Сетевые ошибки: повторяем с задержкой ──
            logger.debug(
                f"GigaChat: отправка запроса | попытка {attempt + 1}/"
                f"{self.MAX_RETRIES + 1} | timeout={self.timeout} с | "
                f"длина промпта: {len(prompt)} символов"
            )
            request_started = time.perf_counter()

            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                    verify=self._verify(),
                )
            except requests.exceptions.Timeout:
                if attempt < self.MAX_RETRIES:
                    delay = self._backoff_delay(retry_index)
                    retry_index += 1
                    logger.warning(
                        f"GigaChat: таймаут ({self.timeout} сек), "
                        f"повтор через {delay:.0f} сек (попытка {attempt + 2}/{self.MAX_RETRIES + 1})"
                    )
                    time.sleep(delay)
                    continue
                raise RuntimeError(
                    f"GigaChat: превышено время ожидания ({self.timeout} сек) "
                    f"после {self.MAX_RETRIES + 1} попыток"
                )
            except requests.exceptions.SSLError as e:
                logger.error(f"Ошибка TLS при обращении к GigaChat: {e}")
                raise RuntimeError(f"{ssl_error_hint()}\n\nТехническая деталь: {e}")
            except requests.exceptions.ConnectionError as e:
                if attempt < self.MAX_RETRIES:
                    delay = self._backoff_delay(retry_index)
                    retry_index += 1
                    logger.warning(
                        f"GigaChat: ошибка соединения, повтор через {delay:.0f} сек: {e}"
                    )
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"Не удалось подключиться к GigaChat: {e}")

            # ── Ответ получен: пишем статус, время и объём ──
            request_elapsed = time.perf_counter() - request_started
            logger.debug(
                f"GigaChat: ответ получен | статус={resp.status_code} | "
                f"время={request_elapsed * 1000:.0f} мс | "
                f"длина ответа: {len(resp.text or '')} символов"
            )
            if request_elapsed >= self.SLOW_REQUEST_SECONDS:
                logger.warning(
                    f"SLOW GigaChat: ответ занял {request_elapsed:.1f} с "
                    f"(порог {self.SLOW_REQUEST_SECONDS:.0f} с) | "
                    f"модель={self.model} | промпт: {len(prompt)} символов"
                )

            # ── 401: токен отозван/просрочен — сбрасываем и пробуем снова ──
            if resp.status_code == 401 and not auth_retry_used:
                logger.warning(
                    "GigaChat вернул 401 — сбрасываем токен и повторяем запрос"
                )
                self._invalidate_token()
                auth_retry_used = True
                continue

            # ── 429 / 5xx: ждём и повторяем ──
            if resp.status_code in self.RETRYABLE_STATUSES and attempt < self.MAX_RETRIES:
                delay = self._backoff_delay(retry_index)
                retry_index += 1
                logger.warning(
                    f"GigaChat вернул {resp.status_code} — повтор через {delay:.0f} сек "
                    f"(попытка {attempt + 2}/{self.MAX_RETRIES + 1})"
                )
                time.sleep(delay)
                continue

            # ── Остальные коды — как раньше ──
            if resp.status_code == 400:
                raise RuntimeError(
                    f"GigaChat вернул 400 (неверный запрос): {resp.text[:300]}"
                )
            if resp.status_code == 404:
                raise RuntimeError(
                    f"GigaChat вернул 404: модель '{self.model}' не найдена. "
                    "Попробуйте GigaChat-2, GigaChat-2-Pro или GigaChat-2-Max."
                )
            if resp.status_code == 401:
                raise RuntimeError(
                    "GigaChat вернул 401: неверный или просроченный access_token "
                    "(повтор после обновления токена не помог)."
                )
            if resp.status_code == 429:
                raise RuntimeError(
                    "GigaChat вернул 429: превышен лимит запросов. "
                    f"Повторы исчерпаны ({self.MAX_RETRIES}). Подождите и повторите."
                )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"GigaChat вернул ошибку {resp.status_code}: {resp.text[:300]}"
                )

            break  # успешный ответ

        data = resp.json()
        raw_content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )

        logger.info(f"Ответ получен, длина: {len(raw_content)} символов")
        # Сам ответ содержит распознанные персональные данные, поэтому в лог
        # идёт только его объём и форма — без текста.
        logger.debug(
            f"GigaChat: тело ответа | символов: {len(raw_content)} | "
            f"строк: {raw_content.count(chr(10)) + 1} | "
            f"похоже на JSON: {raw_content.lstrip().startswith('{')}"
        )

        parsed = _clean_and_parse_json(raw_content)
        logger.info("JSON успешно распарсен")
        logger.debug(
            f"Распознавание: разобраны разделы {list(parsed.keys())} | "
            f"размер ответа: {len(raw_content)} символов"
        )
        return parsed