#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Индексация правовой базы: фрагменты текста и эмбеддинги (RAG).

Что делает
----------
Берёт тексты, скачанные парсером (``data/knowledge/legal/text/*.txt``,
а также оригиналы, если текста ещё нет), режет их на фрагменты по
500–1000 символов с перекрытием 100 символов, считает эмбеддинги через
GigaChat и складывает всё в ``db/legal_metadata.db``
(таблицы из db/crud/knowledge.py).

Зачем перекрытие
----------------
Статья про ответственность за просрочку может начаться на последней
строке одного фрагмента и продолжиться в следующем. Перекрытие в 100
символов гарантирует, что стык попадёт хотя бы в один фрагмент целиком.

Разбиение по смыслу, а не по счётчику
-------------------------------------
Резать текст ровно по 900 символов нельзя: фрагмент начнётся с середины
слова. Поэтому граница ищется от конца окна назад, по приоритету:
граница статьи → пустая строка → конец абзаца → конец предложения →
пробел. Жёсткий разрез остаётся только для текста без единого пробела.

Если эмбеддинги недоступны
--------------------------
Нет ключа GigaChat, нет сети или модель не отвечает — индексация всё
равно проходит: фрагменты сохраняются, а поиск работает по словам
(полнотекстовый индекс, db/crud/knowledge.search_lexical). В отчёте это
видно как ``embedded = 0``, а в результатах поиска — ``mode = "lexical"``.
Молча подменять смысловой поиск словесным модуль не имеет права.
"""

import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from core.legal_parser import LegalParser, extract_text, human_size
from db.crud import knowledge, legal_metadata

logger = logging.getLogger("core.legal_indexer")

#: Рабочий диапазон размера фрагмента из задания.
MIN_CHUNK_SIZE = 500
MAX_CHUNK_SIZE = 1000

#: Перекрытие соседних фрагментов из задания.
DEFAULT_CHUNK_OVERLAP = 100

#: Размер фрагмента по умолчанию (внутри рабочего диапазона).
DEFAULT_CHUNK_SIZE = 900

#: Сколько текстов отправлять в модель за один запрос.
EMBED_BATCH = 10

#: Адреса GigaChat (те же, что в core/gigachat_client.py).
GIGACHAT_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_BASE_URL = "https://api.giga.chat/v1"

#: Модель эмбеддингов GigaChat.
DEFAULT_EMBEDDING_MODEL = "Embeddings"

#: Заголовки статей в правовых текстах: «Статья 793», «Статья 12.1».
ARTICLE_RE = re.compile(r"(?m)^\s*(Стать[яи]\s+\d+(?:\.\d+)?)", re.IGNORECASE)

#: Главы и разделы — попадают в метаданные фрагмента как «part».
PART_RE = re.compile(r"(?m)^\s*((?:Глава|Раздел|Пункт)\s+[\dIVXLC]+(?:\.\d+)?)", re.IGNORECASE)

#: Слова и числа: остальное — разделители (то же правило, что в db/fts.py).
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


# ─────────────────────────────────────────────────────────────
# Модели
# ─────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    """Фрагмент текста правового документа."""

    doc_id: str
    index: int
    text: str
    article: str = ""
    part: str = ""
    char_start: int = 0
    char_end: int = 0
    checksum: str = ""

    @property
    def chars(self) -> int:
        """Длина фрагмента в символах."""
        return len(self.text)

    def to_dict(self) -> Dict[str, Any]:
        """Словарь для базы (поля совпадают с колонками knowledge_chunks)."""
        data = asdict(self)
        data.pop("index", None)
        return data


# ─────────────────────────────────────────────────────────────
# Разбиение текста
# ─────────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """
    Приводит текст к виду, удобному для разбиения.

    Мягкие переносы и «висячие» пробелы ломают поиск границ, поэтому
    строки обрезаются, а длинные цепочки пустых строк сжимаются до одной.

    :param text: исходный текст;
    :return: нормализованный текст.
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    # Ведущие и висячие пробелы в строках — шум распознавания: у сканов
    # OCR часто добавляет отступ, из-за которого фрагмент начинается с
    # пустоты. Для правового текста отступ смысла не несёт.
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _best_break(text: str, start: int, end: int) -> int:
    """
    Ищет лучшую границу фрагмента в окне ``[start, end)``.

    Просматривается последняя треть окна: этого достаточно, чтобы не
    потерять много текста, и недорого по времени. Приоритет — от границы
    статьи к пробелу: чем «крупнее» разрыв, тем меньше шансов разрезать
    мысль пополам.

    :param text: весь текст документа;
    :param start: начало фрагмента;
    :param end: предполагаемый конец;
    :return: позиция разреза.
    """
    window_start = start + max(1, int((end - start) * 0.6))
    window = text[window_start:end]
    for pattern in (r"\n\s*Стать[яи]\s+\d", r"\n\n+", r"\n", r"(?<=[.;:])\s", r"\s"):
        matches = list(re.finditer(pattern, window, re.IGNORECASE))
        if matches:
            return window_start + matches[-1].end()
    return end


def article_map(text: str) -> List[Tuple[int, str]]:
    """
    Собирает карту «позиция в тексте → статья».

    :param text: текст документа;
    :return: отсортированный список пар (позиция, название статьи).
    """
    result: List[Tuple[int, str]] = []
    for match in ARTICLE_RE.finditer(text):
        result.append((match.start(), re.sub(r"\s+", " ", match.group(1)).strip()))
    return result


def part_map(text: str) -> List[Tuple[int, str]]:
    """
    Собирает карту «позиция в тексте → глава/раздел».

    :param text: текст документа;
    :return: отсортированный список пар (позиция, название части).
    """
    result: List[Tuple[int, str]] = []
    for match in PART_RE.finditer(text):
        result.append((match.start(), re.sub(r"\s+", " ", match.group(1)).strip()))
    return result


def _value_at(mapping: Sequence[Tuple[int, str]], position: int, end: int = -1) -> str:
    """
    Значение карты, действующее во фрагменте ``[position, end)``.

    Сначала берётся последнее значение, начавшееся ДО фрагмента (норма, в
    которую фрагмент попал по смыслу). Если такого нет — первое значение
    ВНУТРИ фрагмента: иначе первый фрагмент документа, начинающийся прямо
    со статьи, остался бы без указания статьи.

    :param mapping: отсортированная карта «позиция → значение»;
    :param position: начало фрагмента;
    :param end: конец фрагмента (-1 — не учитывать);
    :return: значение или пустая строка.
    """
    current = ""
    for start, value in mapping:
        if start > position:
            break
        current = value
    if current:
        return current
    if end > position:
        for start, value in mapping:
            if position <= start < end:
                return value
    return ""


def chunk_text(text: str, doc_id: str = "", chunk_size: int = DEFAULT_CHUNK_SIZE,
               overlap: int = DEFAULT_CHUNK_OVERLAP,
               checksum: str = "") -> List[Chunk]:
    """
    Режет текст на фрагменты с перекрытием.

    :param text: текст документа;
    :param doc_id: идентификатор документа (попадёт в каждый фрагмент);
    :param chunk_size: желаемый размер фрагмента (задание: 500–1000);
    :param overlap: перекрытие соседних фрагментов (задание: 100);
    :param checksum: контрольная сумма исходного текста;
    :return: список фрагментов.
    """
    prepared = normalize_text(text)
    if not prepared:
        return []

    size = max(1, int(chunk_size))
    step_overlap = max(0, min(int(overlap), size - 1))
    articles = article_map(prepared)
    parts = part_map(prepared)

    chunks: List[Chunk] = []
    position = 0
    guard = 0
    limit = len(prepared) + size
    while position < len(prepared) and guard < limit:
        guard += 1
        end = min(position + size, len(prepared))
        if end < len(prepared):
            end = _best_break(prepared, position, end)
        body = prepared[position:end].strip()
        if body:
            chunks.append(Chunk(
                doc_id=doc_id,
                index=len(chunks),
                text=body,
                article=_value_at(articles, position, end),
                part=_value_at(parts, position, end),
                char_start=position,
                char_end=end,
                checksum=checksum,
            ))
        if end >= len(prepared):
            break
        position = max(end - step_overlap, position + 1)
    return chunks


def text_checksum(text: str) -> str:
    """
    Контрольная сумма текста (по ней видно, менялся ли документ).

    :param text: текст;
    :return: hex-строка SHA-256.
    """
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────
# Эмбеддинги GigaChat
# ─────────────────────────────────────────────────────────────

def tls_verify(explicit: Optional[Any] = None) -> Any:
    """
    Значение параметра ``verify`` для запросов к GigaChat.

    GigaChat отвечает сертификатом НУЦ Минцифры, которого нет в стандартном
    наборе certifi, поэтому «просто requests» падает с
    ``CERTIFICATE_VERIFY_FAILED``. Порядок тот же, что у распознавания
    (core/tls_config.py): явный путь → настройка ``gigachat_ca_bundle`` →
    файл в resources/certs → системный набор.

    Отключение проверки разрешено только явной настройкой
    ``gigachat_verify_ssl = false`` — и тогда об этом пишется предупреждение:
    вместе с трафиком может утечь ключ авторизации.

    :param explicit: явное значение (False — отключить; строка — путь к CA);
    :return: значение для requests: False, путь к файлу или True.
    """
    if explicit is False:
        return False
    if isinstance(explicit, str) and explicit:
        return explicit
    try:
        from core.settings_service import get_settings_service
        if not get_settings_service().get_bool("gigachat_verify_ssl", True):
            logger.warning("Проверка TLS-сертификата GigaChat отключена настройкой")
            return False
    except Exception as e:  # noqa: BLE001 — настройки необязательны
        logger.debug(f"Настройки TLS не прочитаны: {e}")
    try:
        from core.tls_config import resolve_ca_bundle
        return resolve_ca_bundle(explicit if isinstance(explicit, str) else None) or True
    except Exception as e:  # noqa: BLE001 — модуль TLS может отсутствовать
        logger.debug(f"CA-сертификат не найден: {e}")
        return True


class LocalEmbedder:
    """
    Локальный вектор текста: слова и n-граммы без внешних сервисов.

    Зачем он нужен
    --------------
    Эмбеддинги GigaChat доступны не на всех тарифах: обращение к
    ``/v1/embeddings`` отвечает ``402 Payment Required``, хотя ключ верный и
    чат-модели работают. Оставлять в этом случае поиск без векторов —
    значит терять косинусную близость там, где она может работать.

    Что это за вектор
    -----------------
    Классическая модель «мешок слов» с хешированием признаков (hashing
    trick): слова, пары соседних слов и четырёхбуквенные фрагменты
    раскладываются в вектор фиксированной длины, вес — подлинейная частота,
    вектор нормируется по длине. Это НЕ нейросетевые эмбеддинги: смысловые
    связи он не ловит, но лексическую близость считает устойчиво, офлайн и
    одинаково от запуска к запуску.

    Хеш считается через ``blake2b``, а не встроенным ``hash()``: у строк в
    Python хеш солится при каждом запуске, и индекс перестал бы совпадать
    с запросом после перезапуска программы.
    """

    #: Размерность вектора.
    DIM = 512

    #: Минимальная длина слова, попадающего в вектор.
    MIN_WORD = 3

    def __init__(self, dim: int = DIM):
        """
        :param dim: размерность вектора.
        """
        self.dim = max(64, int(dim))

    def available(self) -> bool:
        """Локальный вектор доступен всегда (сети не требует)."""
        return True

    @property
    def model(self) -> str:
        """Имя модели для метаданных фрагмента."""
        return f"local-bow-{self.dim}"

    @staticmethod
    def _tokens(text: str) -> List[str]:
        """
        Разбирает текст на признаки: слова, пары слов и 4-граммы.

        :param text: текст;
        :return: список признаков.
        """
        words = [word for word in _WORD_RE.findall(str(text).lower()) if len(word) >= LocalEmbedder.MIN_WORD]
        features = list(words)
        features.extend(f"{words[i]}_{words[i + 1]}" for i in range(len(words) - 1))
        compact = re.sub(r"\s+", " ", str(text).lower())
        features.extend(compact[i:i + 4] for i in range(0, max(0, len(compact) - 3), 2))
        return features

    def _index(self, feature: str) -> Tuple[int, float]:
        """
        Индекс и знак признака в векторе.

        :param feature: признак;
        :return: пара «индекс, знак».
        """
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        return value % self.dim, (1.0 if value >> 63 == 0 else -1.0)

    def vector(self, text: str) -> List[float]:
        """
        Считает вектор одного текста.

        :param text: текст;
        :return: список чисел длиной ``dim``.
        """
        import numpy as np

        result = np.zeros(self.dim, dtype="float32")
        counts: Dict[str, int] = {}
        for feature in self._tokens(text):
            counts[feature] = counts.get(feature, 0) + 1
        for feature, count in counts.items():
            index, sign = self._index(feature)
            result[index] += sign * (1.0 + math.log(count))
        norm = float(np.linalg.norm(result))
        if norm:
            result /= norm
        return [float(value) for value in result]

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """
        Считает вектора для списка текстов.

        :param texts: список текстов;
        :return: список векторов.
        """
        return [self.vector(text) for text in texts]


class GigaChatEmbedder:
    """
    Считает эмбеддинги через GigaChat (``POST /v1/embeddings``).

    Авторизация — та же, что у распознавания (core/gigachat_client.py):
    Basic-ключ из системного хранилища → OAuth-токен на 30 минут. Ключ и
    токен в логи не попадают никогда.

    Класс отдельный, а не вызов клиента распознавания, по двум причинам:
    у GigaChatClient нет метода эмбеддингов, и трогать его нельзя — файл
    ведёт другой разработчик (изоляция шага).
    """

    def __init__(self, auth_key: Optional[str] = None, model: str = DEFAULT_EMBEDDING_MODEL,
                 scope: str = "GIGACHAT_API_PERS", timeout: int = 60,
                 session: Optional[requests.Session] = None, verify: Optional[Any] = None,
                 retries: int = 3, pause: float = 2.0):
        """
        :param auth_key: ключ авторизации; None — взять из системного хранилища;
        :param model: имя модели эмбеддингов;
        :param scope: область доступа GigaChat;
        :param timeout: таймаут запроса, секунд;
        :param session: готовая requests-сессия (в тестах — MOCK);
        :param verify: значение для requests (None — определить самому, см. tls_verify);
        :param retries: число попыток;
        :param pause: пауза между попытками, секунд.
        """
        self._auth_key = auth_key
        self.model = model
        self.scope = scope
        self.timeout = int(timeout)
        self.verify = tls_verify(verify)
        self.retries = max(1, int(retries))
        self.pause = float(pause)
        self.session = session or requests.Session()
        self._token: Optional[str] = None
        self._token_expires = 0.0

    # ── авторизация ──

    @property
    def auth_key(self) -> str:
        """
        Ключ авторизации.

        Пустая строка — это явное «ключа нет» (так проверяют поведение без
        ключа); None — «возьми из системного хранилища».
        """
        if self._auth_key is not None:
            return self._auth_key
        from core.secrets_store import get_gigachat_key

        self._auth_key = get_gigachat_key()
        return self._auth_key or ""

    def available(self) -> bool:
        """Есть ли ключ: без него считать эмбеддинги нечем."""
        try:
            return bool(self.auth_key)
        except Exception as e:  # noqa: BLE001 — хранилище может быть недоступно
            logger.warning(f"Ключ GigaChat недоступен: {e}")
            return False

    def token(self) -> str:
        """
        Возвращает действующий OAuth-токен (получает при необходимости).

        :return: токен доступа;
        :raises RuntimeError: если авторизация не прошла.
        """
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        key = self.auth_key
        if not key:
            raise RuntimeError(
                "Нет ключа GigaChat: задайте его командой python set_key.py — "
                "без ключа эмбеддинги не считаются"
            )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {key}",
        }
        last_error = ""
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.post(GIGACHAT_AUTH_URL, headers=headers,
                                             data={"scope": self.scope},
                                             timeout=self.timeout, verify=self.verify)
                if response.status_code == 200:
                    data = response.json()
                    self._token = str(data["access_token"])
                    expires = float(data.get("expires_at") or 0)
                    if expires > 10_000_000_000:  # миллисекунды → секунды
                        expires /= 1000.0
                    self._token_expires = expires or (time.time() + 1800)
                    return self._token
                last_error = f"HTTP {response.status_code}"
            except (requests.RequestException, ValueError, OSError, KeyError) as e:
                last_error = f"{type(e).__name__}: {e}"
            if attempt < self.retries:
                time.sleep(self.pause)
        raise RuntimeError(f"GigaChat: не удалось получить токен — {last_error}")

    # ── эмбеддинги ──

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """
        Считает вектора для списка текстов.

        Тексты отправляются порциями (``EMBED_BATCH``): один запрос на
        тысячи фрагментов GigaChat не примет.

        :param texts: список текстов;
        :return: список векторов в том же порядке;
        :raises RuntimeError: если модель не ответила.
        """
        if not texts:
            return []
        token = self.token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        vectors: List[List[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch = [str(item)[:4000] for item in texts[start:start + EMBED_BATCH]]
            payload = {"model": self.model, "input": batch}
            last_error = ""
            for attempt in range(1, self.retries + 1):
                try:
                    response = self.session.post(GIGACHAT_BASE_URL + "/embeddings",
                                                 headers=headers, json=payload,
                                                 timeout=self.timeout, verify=self.verify)
                    if response.status_code == 200:
                        data = response.json()
                        items = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
                        vectors.extend([list(map(float, item["embedding"])) for item in items])
                        last_error = ""
                        break
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                except (requests.RequestException, ValueError, OSError, KeyError) as e:
                    last_error = f"{type(e).__name__}: {e}"
                if attempt < self.retries:
                    time.sleep(self.pause)
            if last_error:
                raise RuntimeError(f"GigaChat embeddings: {last_error}")
        return vectors


# ─────────────────────────────────────────────────────────────
# Индексатор
# ─────────────────────────────────────────────────────────────

class LegalIndexer:
    """
    Индексирует скачанные правовые документы: фрагменты + эмбеддинги.

    Пример:
        indexer = LegalIndexer()
        summary = indexer.index_documents()
        print(summary["chunks"], summary["embedded"])
    """

    def __init__(self, parser: Optional[LegalParser] = None, db_path: Optional[str] = None,
                 chunk_size: int = DEFAULT_CHUNK_SIZE, chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
                 embedding_model: str = DEFAULT_EMBEDDING_MODEL,
                 embedder: Optional[Any] = None, use_embeddings: bool = True,
                 local_fallback: bool = True,
                 progress: Optional[Callable[[str, int, int], None]] = None,
                 ocr: bool = True):
        """
        :param parser: парсер правовой базы (для путей и статистики);
        :param db_path: путь к базе правовой базы (None — общий);
        :param chunk_size: размер фрагмента;
        :param chunk_overlap: перекрытие фрагментов;
        :param embedding_model: имя модели эмбеддингов GigaChat;
        :param embedder: готовый объект с методом ``embed`` (в тестах — MOCK);
        :param use_embeddings: считать ли вектора (False — только текст);
        :param local_fallback: переходить ли на локальный вектор, если
            GigaChat-эмбеддинги недоступны (нет ключа или тариф без них);
        :param progress: обратный вызов «(этап, сделано, всего)»;
        :param ocr: разрешать ли OCR, если текста у документа ещё нет.
        """
        self.parser = parser or LegalParser()
        if db_path:
            legal_metadata.set_legal_db_path(db_path)
        self.chunk_size = max(MIN_CHUNK_SIZE, min(MAX_CHUNK_SIZE, int(chunk_size)))
        self.chunk_overlap = max(0, min(int(chunk_overlap), self.chunk_size - 1))
        self.embedding_model = embedding_model
        self.use_embeddings = bool(use_embeddings)
        self.local_fallback = bool(local_fallback)
        self.progress = progress
        self.ocr = bool(ocr)
        self._embedder = embedder
        # Проверка модели идёт один раз и для подставленного объекта тоже:
        # «свой» эмбеддер вполне может оказаться недоступным (нет ключа,
        # тариф без модели), и тогда работает запасной вариант.
        self._embedder_checked = False
        self._embedder_disabled = False
        self._embedding_note = ""

    # ── служебное ──

    def _notify(self, stage: str, done: int, total: int) -> None:
        """Сообщает о прогрессе (если обработчик задан)."""
        if self.progress is None:
            return
        try:
            self.progress(stage, done, total)
        except Exception as e:  # noqa: BLE001 — интерфейс не должен ронять индексацию
            logger.debug(f"Обработчик прогресса упал: {e}")

    @property
    def embedder(self) -> Any:
        """
        Объект для векторов: GigaChat, а при его недоступности — локальный.

        Выбор делается один раз за жизнь индексатора и запоминается, чтобы
        не дёргать сеть на каждый документ. Если вектора отключены или
        локальный запасной вариант запрещён и GigaChat не ответил, свойство
        возвращает None — «векторов не будет» — и больше не пытается
        создать новый объект.
        """
        if self._embedder_disabled or not self.use_embeddings:
            return None
        if self._embedder is None:
            self._embedder = GigaChatEmbedder(model=self.embedding_model)
        if not self._embedder_checked:
            self._embedder_checked = True
            probe = getattr(self._embedder, "available", None)
            if callable(probe) and not probe():
                self._embedding_note = "нет ключа GigaChat"
                self._switch_to_local()
            elif getattr(self._embedder, "model", "") == self.embedding_model:
                # Ключ есть, но тариф может не включать эмбеддинги: проверяем
                # одним коротким запросом, чтобы не узнать об этом на тысячном
                # фрагменте. Подставленную модель (её имя отличается от имени
                # модели GigaChat) не проверяем — это чужая реализация.
                try:
                    self._embedder.embed(["проверка"])
                except Exception as e:  # noqa: BLE001
                    self._embedding_note = str(e)[:200]
                    logger.warning(f"GigaChat-эмбеддинги недоступны ({self._embedding_note})")
                    self._switch_to_local()
        return self._embedder

    def _switch_to_local(self) -> None:
        """Переходит на локальный вектор (или отключает вектора вовсе)."""
        if not self.local_fallback or not self.use_embeddings:
            self._embedder = None
            self._embedder_disabled = True
            note = self._embedding_note or "GigaChat-эмбеддинги недоступны"
            self._embedding_note = f"{note}; вектора не считаются"
            return
        self._embedder = LocalEmbedder()
        note = self._embedding_note or "GigaChat-эмбеддинги недоступны"
        self._embedding_note = f"{note}; считаю локальный вектор {self._embedder.model}"
        logger.info(f"Вектора считает локальная модель: {self._embedding_note}")

    def embedding_kind(self) -> str:
        """
        Чем считаются вектора.

        :return: «gigachat», «local» или «none».
        """
        if not self.use_embeddings:
            return "none"
        embedder = self.embedder
        if embedder is None:
            return "none"
        return "local" if isinstance(embedder, LocalEmbedder) else "gigachat"

    def embeddings_available(self) -> Tuple[bool, str]:
        """
        Можно ли считать вектора прямо сейчас.

        :return: пара «доступны, причина отказа».
        """
        if not self.use_embeddings:
            return False, "вектора отключены настройкой"
        return (self.embedder is not None), (self._embedding_note or "нет объекта для векторов")

    # ── источники текста ──

    def source_files(self, directory: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Собирает список текстов для индексации.

        Берутся: документы из базы метаданных (у кого есть текстовый файл) и
        любые файлы в каталоге текстов, которых в базе нет (их положил
        оператор вручную — они индексируются как «ручные»).

        :param directory: каталог текстов (None — из настроек парсера);
        :return: список словарей «doc_id, title, text_path, checksum, meta».
        """
        text_dir = directory or self.parser.text_dir
        sources: List[Dict[str, Any]] = []
        known: Dict[str, Dict[str, Any]] = {}

        for row in legal_metadata.list_documents(limit=None, conn=self.parser._connect()):
            path = row.get("text_path") or ""
            if path and os.path.exists(path):
                known[os.path.abspath(path)] = row
                sources.append({
                    "doc_id": row["id"],
                    "title": row.get("short_title") or row.get("title") or row["id"],
                    "text_path": path,
                    "checksum": row.get("checksum") or "",
                    "meta": row,
                })

        if text_dir and os.path.isdir(text_dir):
            for name in sorted(os.listdir(text_dir)):
                path = os.path.join(text_dir, name)
                if not os.path.isfile(path):
                    continue
                absolute = os.path.abspath(path)
                if absolute in known:
                    continue
                doc_id = f"file:{os.path.splitext(name)[0]}"
                sources.append({
                    "doc_id": doc_id,
                    "title": os.path.splitext(name)[0],
                    "text_path": path,
                    "checksum": "",
                    "meta": {"id": doc_id, "title": os.path.splitext(name)[0],
                             "source": "file", "category": "manual"},
                })
        return sources

    def read_source_text(self, source: Dict[str, Any]) -> str:
        """
        Читает текст документа; при необходимости готовит его из оригинала.

        :param source: запись источника из source_files();
        :return: текст документа (пустая строка — не удалось получить).
        """
        path = source["text_path"]
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()

        original = (source.get("meta") or {}).get("original_path") or ""
        if original and os.path.exists(original):
            result = extract_text(original, allow_ocr=self.ocr,
                                  dpi=int(self.parser.storage.get("ocr_dpi", 300)),
                                  lang=str(self.parser.storage.get("ocr_lang", "rus+eng")),
                                  min_chars=int(self.parser.storage.get("ocr_min_chars", 200)),
                                  max_pages=int(self.parser.storage.get("ocr_max_pages", 80)))
            return result.text
        return ""

    # ── индексация ──

    def index_documents(self, directory: Optional[str] = None, reindex: bool = False,
                        limit: Optional[int] = None) -> Dict[str, Any]:
        """
        Индексирует документы правовой базы.

        :param directory: каталог текстов (None — из настроек парсера);
        :param reindex: True — сначала очистить индекс целиком;
        :param limit: предел числа документов (для быстрой проверки);
        :return: сводка «documents / chunks / embedded / skipped / errors / seconds».
        """
        started = time.time()
        conn = knowledge.get_connection()
        try:
            knowledge.init_db(conn)
            if reindex:
                knowledge.clear_all(conn=conn)
            sources = self.source_files(directory)
            if limit:
                sources = sources[: int(limit)]
        finally:
            conn.close()

        can_embed, reason = self.embeddings_available()
        if not can_embed and self.use_embeddings:
            logger.warning(f"Вектора недоступны ({reason}); индексирую только текст")
        vector_model = getattr(self.embedder, "model", self.embedding_model) if can_embed else ""

        summary: Dict[str, Any] = {
            "documents": 0, "chunks": 0, "embedded": 0, "skipped": 0,
            "errors": {}, "seconds": 0.0, "embeddings": can_embed,
            "embedding_kind": self.embedding_kind(), "embedding_model": vector_model,
            "embedding_note": self._embedding_note or reason,
        }
        total = len(sources)
        self._notify("scan", 0, total)

        for position, source in enumerate(sources, start=1):
            doc_id = source["doc_id"]
            try:
                text = self.read_source_text(source)
                if not text.strip():
                    summary["errors"][source["title"][:60]] = "пустой текст"
                    self._notify("scan", position, total)
                    continue
                checksum = text_checksum(text)
                conn = knowledge.get_connection()
                try:
                    state = knowledge.document_state(doc_id, conn=conn)
                finally:
                    conn.close()
                # Текст не менялся И вектора считала та же модель — пропускаем.
                if (not reindex and state and state.get("checksum") == checksum
                        and (state.get("model") or "") == (vector_model or state.get("model") or "")):
                    summary["skipped"] += 1
                    self._notify("scan", position, total)
                    continue

                chunks = chunk_text(text, doc_id=doc_id, chunk_size=self.chunk_size,
                                    overlap=self.chunk_overlap, checksum=checksum)
                self._notify("chunk", position, total)
                if not chunks:
                    summary["errors"][source["title"][:60]] = "текст не разбился на фрагменты"
                    continue

                vectors: List[List[float]] = []
                if can_embed:
                    self._notify("embed", position, total)
                    try:
                        vectors = self.embedder.embed([chunk.text for chunk in chunks])
                    except Exception as e:  # noqa: BLE001 — индексируем текст без векторов
                        summary["errors"][source["title"][:60]] = f"вектора: {e}"
                        vectors = []
                        can_embed = False
                        summary["embeddings"] = False
                        summary["embedding_kind"] = "none"

                payload = []
                for index, chunk in enumerate(chunks):
                    item = chunk.to_dict()
                    if index < len(vectors):
                        item["embedding"] = vectors[index]
                        item["model"] = vector_model or self.embedding_model
                    payload.append(item)

                conn = knowledge.get_connection()
                try:
                    knowledge.replace_document_chunks(doc_id, payload, conn=conn)
                finally:
                    conn.close()

                summary["documents"] += 1
                summary["chunks"] += len(payload)
                summary["embedded"] += sum(1 for item in payload if item.get("embedding"))
                self._notify("save", position, total)
            except Exception as e:  # noqa: BLE001 — один документ не должен срывать проход
                logger.exception(f"Индексация «{source['title'][:60]}» не удалась")
                summary["errors"][source["title"][:60]] = f"{type(e).__name__}: {e}"
            finally:
                self._notify("scan", position, total)

        summary["seconds"] = time.time() - started
        return summary

    # ── поиск ──

    def search(self, query: str, top_k: int = 5, min_score: float = 0.0) -> List[Dict[str, Any]]:
        """
        Ищет релевантные фрагменты.

        Режимы:
          * есть вектора — **гибридный поиск**: косинусная близость плюс
            полнотекстовый поиск, результаты сливаются по Reciprocal Rank
            Fusion (``mode = "hybrid"``);
          * векторов нет — только полнотекстовый поиск (``mode = "lexical"``).

        Почему гибрид, а не «сначала вектор»
        -----------------------------------
        Вектор ловит смысл, но ошибается в мелочах: вопрос «ответственность
        за просрочку доставки груза» он может увести в статью про
        ответственность по ценным бумагам — слова те же, тема другая. Точное
        слово «просрочк» при этом безошибочно указывает на статью 793 ГК РФ.
        Слияние по рангам (RRF) складывает оба списка, не требуя приводить
        их оценки к одному масштабу: у косинуса и у BM25 шкалы разные, а
        ранги — всегда 1, 2, 3…

        :param query: вопрос или фраза;
        :param top_k: сколько фрагментов вернуть;
        :param min_score: нижняя граница итоговой оценки;
        :return: список фрагментов с полями score, mode, article, doc_title.
        """
        term = (query or "").strip()
        if not term:
            return []
        depth = max(int(top_k) * 6, 30)
        lexical = self._lexical(term, depth, 0.0)
        semantic = self._semantic(term, depth, 0.0)
        if not semantic:
            return lexical[: max(1, int(top_k))]
        if not lexical:
            return semantic[: max(1, int(top_k))]

        fused = _fuse_ranks(lexical, semantic)
        result = [item for item in fused if item["score"] >= float(min_score)]
        return result[: max(1, int(top_k))]

    def _semantic(self, term: str, top_k: int, min_score: float) -> List[Dict[str, Any]]:
        """
        Поиск по косинусной близости к вектору запроса.

        Вектор запроса считается ТОЙ ЖЕ моделью, что и вектора индекса:
        иначе косинус сравнивал бы разные пространства.

        :param term: строка запроса;
        :param top_k: сколько фрагментов вернуть;
        :param min_score: нижняя граница косинуса;
        :return: список фрагментов (пустой — векторов нет или модель не та).
        """
        if not self.use_embeddings:
            return []
        conn = knowledge.get_connection()
        try:
            knowledge.init_db(conn)
            chunks = knowledge.iter_chunks(with_vectors=True, conn=conn)
        finally:
            conn.close()
        with_vectors = [chunk for chunk in chunks if chunk.get("vector")]
        if not with_vectors:
            return []

        model = str(with_vectors[0].get("model") or "")
        if model.startswith("local-") and not isinstance(self.embedder, LocalEmbedder):
            # Индекс собран локальными векторами, а сейчас доступна другая
            # модель — сравнивать их нельзя, честнее промолчать.
            return []
        try:
            vector = self.embedder.embed([term])[0]
        except Exception as e:  # noqa: BLE001 — остаётся полнотекстовый поиск
            logger.warning(f"Вектор запроса не посчитан ({e}); ищу по словам")
            return []
        if not vector or len(vector) != int(with_vectors[0].get("dim") or len(vector)):
            logger.warning("Размерность вектора запроса не совпала с индексом")
            return []
        return self._rank_by_cosine(vector, with_vectors, int(top_k), float(min_score))

    @staticmethod
    def _rank_by_cosine(vector: Sequence[float], chunks: List[Dict[str, Any]],
                        top_k: int, min_score: float) -> List[Dict[str, Any]]:
        """
        Сортирует фрагменты по косинусной близости к вектору запроса.

        :param vector: вектор запроса;
        :param chunks: фрагменты с векторами;
        :param top_k: сколько вернуть;
        :param min_score: нижняя граница оценки;
        :return: список фрагментов с полем score.
        """
        import numpy as np

        matrix = np.asarray([chunk["vector"] for chunk in chunks], dtype="float32")
        query = np.asarray(vector, dtype="float32")
        if matrix.shape[1] != query.shape[0]:
            logger.warning("Размерность векторов не совпала с запросом — ищу по словам")
            return []
        norms = np.linalg.norm(matrix, axis=1) * float(np.linalg.norm(query))
        norms[norms == 0] = 1.0
        scores = (matrix @ query) / norms

        order = np.argsort(-scores)[: max(1, top_k)]
        result = []
        for index in order:
            score = float(scores[index])
            if score < min_score:
                continue
            item = dict(chunks[int(index)])
            item.pop("vector", None)
            item["score"] = score
            item["mode"] = "semantic"
            result.append(item)
        return result

    def _lexical(self, query: str, top_k: int, min_score: float) -> List[Dict[str, Any]]:
        """
        Поиск по словам, когда векторов нет.

        :param query: строка поиска;
        :param top_k: сколько вернуть;
        :param min_score: нижняя граница оценки;
        :return: список фрагментов с полями score и mode.
        """
        conn = knowledge.get_connection()
        try:
            rows = knowledge.search_lexical(query, limit=max(top_k * 4, 20), conn=conn)
        finally:
            conn.close()
        result = []
        for row in rows:
            score = float(row.get("score") or 0.0)
            if score < min_score:
                continue
            item = dict(row)
            item["score"] = score
            item["mode"] = "lexical"
            result.append(item)
        return result[: max(1, top_k)]

    # ── сводка ──

    def stats(self) -> Dict[str, Any]:
        """
        Сводка по индексу для интерфейса.

        :return: словарь из db/crud/knowledge.stats() плюс размер на диске.
        """
        conn = knowledge.get_connection()
        try:
            data = knowledge.stats(conn=conn)
        finally:
            conn.close()
        data["index_size"] = human_size(data["bytes"])
        return data

    def clear(self) -> None:
        """Удаляет индекс (документы на диске остаются)."""
        conn = knowledge.get_connection()
        try:
            knowledge.clear_all(conn=conn)
        finally:
            conn.close()


def _fuse_ranks(lexical: List[Dict[str, Any]], semantic: List[Dict[str, Any]],
                k: int = 60) -> List[Dict[str, Any]]:
    """
    Сливает два ранжированных списка по Reciprocal Rank Fusion.

    Оценка фрагмента = сумма ``1 / (k + ранг)`` по всем спискам, где он
    встретился. Метод не требует приводить оценки к одной шкале (у косинуса
    и BM25 они разные) и устойчив к выбросам: один список не может
    «продавить» результат другого.

    :param lexical: результаты полнотекстового поиска (лучшие — первыми);
    :param semantic: результаты поиска по векторам (лучшие — первыми);
    :param k: сглаживание (стандартное значение — 60);
    :return: объединённый список, отсортированный по итоговой оценке.
    """
    merged: Dict[Any, Dict[str, Any]] = {}
    for source, items in (("lexical", lexical), ("semantic", semantic)):
        for rank, item in enumerate(items, start=1):
            key = (item.get("doc_id"), item.get("chunk_index"))
            # Оценку источника НЕ переносим в итоговую: у косинуса и BM25
            # разные шкалы, и итог считается только из рангов.
            entry = merged.setdefault(
                key, {name: value for name, value in item.items() if name != "score"}
            )
            entry["rrf"] = float(entry.get("rrf", 0.0)) + 1.0 / (k + rank)
            if source == "lexical":
                entry["lexical_score"] = float(item.get("score") or 0.0)
                entry.setdefault("strict", item.get("strict"))
            else:
                entry["semantic_score"] = float(item.get("score") or 0.0)
            entry["mode"] = "hybrid"
    for entry in merged.values():
        entry["score"] = entry.pop("rrf", 0.0)
    return sorted(merged.values(), key=lambda item: item["score"], reverse=True)


def index_summary_text(summary: Dict[str, Any]) -> str:
    """
    Собирает отчёт об индексации одной строкой на пункт.

    :param summary: сводка index_documents();
    :return: многострочный текст.
    """
    lines = [
        f"Документов проиндексировано: {summary.get('documents', 0)}",
        f"Фрагментов: {summary.get('chunks', 0)}",
        f"С векторами: {summary.get('embedded', 0)}",
        f"Пропущено без изменений: {summary.get('skipped', 0)}",
        f"Поиск: {_mode_text(summary)}",
        f"Время: {summary.get('seconds', 0):.1f} с",
    ]
    note = summary.get("embedding_note")
    if note:
        lines.append(f"Вектора: {note}")
    for name, error in (summary.get("errors") or {}).items():
        lines.append(f"ОШИБКА {name}: {error}")
    return "\n".join(lines)


def _mode_text(summary: Dict[str, Any]) -> str:
    """
    Описание режима поиска по сводке индексации.

    :param summary: сводка index_documents();
    :return: текст для отчёта.
    """
    kind = str(summary.get("embedding_kind") or "")
    if not summary.get("embedded"):
        return "по словам (векторов нет)"
    if kind == "local":
        return "по косинусной близости, локальные вектора (GigaChat-эмбеддинги недоступны)"
    return "по косинусной близости, эмбеддинги GigaChat"


def save_index_report(summary: Dict[str, Any], path: str) -> str:
    """
    Сохраняет отчёт об индексации в JSON (для истории и разбора).

    :param summary: сводка index_documents();
    :param path: путь к файлу;
    :return: путь к записанному файлу.
    """
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    payload = dict(summary)
    payload["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    return path


__all__ = [
    "ARTICLE_RE",
    "Chunk",
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_EMBEDDING_MODEL",
    "EMBED_BATCH",
    "GIGACHAT_AUTH_URL",
    "GIGACHAT_BASE_URL",
    "GigaChatEmbedder",
    "LegalIndexer",
    "LocalEmbedder",
    "MAX_CHUNK_SIZE",
    "MIN_CHUNK_SIZE",
    "PART_RE",
    "article_map",
    "chunk_text",
    "index_summary_text",
    "normalize_text",
    "part_map",
    "save_index_report",
    "text_checksum",
    "tls_verify",
]
