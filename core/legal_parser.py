#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер правовой базы: документы с pravo.gov.ru.

Источник — официальный портал правовой информации (открытые данные):
скачивание нормативных актов для локальной правовой базы и RAG-поиска.

Два независимых канала (оба принадлежат порталу)
-----------------------------------------------
1. ``PublicationApi`` — REST-API портала опубликования
   (``publication.pravo.gov.ru/api``): поиск, метаданные, файлы.
   Именно его вызывает веб-интерфейс портала, поэтому он стабилен.
   Отдаёт официальный PDF — но это СКАН без текстового слоя.
2. ``IpsApi`` — банк «Законодательство России»
   (``pravo.gov.ru/proxy/ips/``): консолидированный текст акта в HTML
   по идентификатору ``nd``. Текстовый слой есть, поэтому для кодексов
   этот канал даёт лучший результат.

Почему текст берётся через OCR
------------------------------
PDF на портале опубликования — отсканированные страницы, текстового слоя
в них нет (проверено на 2200, 1639, 1640, 1642, 368, 141, 440: 0 символов
на весь документ). Поэтому после скачивания текст извлекается так:
сначала пробуем текстовый слой, а если его нет — распознаём страницы
локальным Tesseract (``rus+eng``). Приёмка: 1,5 с на страницу, качество
русского текста достаточное для поиска. Если Tesseract недоступен, файл
всё равно скачивается, а в метаданных остаётся ``text_chars = 0`` и
пометка в ``extra['text_error']`` — база не притворяется полной.

Хранилище
---------
``data/knowledge/legal/original`` — скачанные оригиналы (PDF, HTML);
``data/knowledge/legal/text``     — извлечённый текст (TXT, UTF-8);
``data/knowledge/legal/cache``    — кэш метаданных и справочников API;
``db/legal_metadata.db``          — метаданные (db/crud/legal_metadata.py).

Сеть в тестах не используется: ``LegalParser`` принимает готовую сессию
(``session=``) и подменяемые бэкенды, поэтому все тесты идут на MOCK.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

import requests

from db.crud import legal_metadata

logger = logging.getLogger("core.legal_parser")

# ─────────────────────────────────────────────────────────────
# Адреса источников
# ─────────────────────────────────────────────────────────────

PUBLICATION_BASE = "http://publication.pravo.gov.ru"
PUBLICATION_API = PUBLICATION_BASE + "/api"
IPS_BASE = "http://pravo.gov.ru/proxy/ips/"

DEFAULT_USER_AGENT = "logistics_contractor-legal-parser/1.0 (открытые данные pravo.gov.ru)"

#: Размеры страницы, которые принимает API портала (значение перечислением).
ALLOWED_PAGE_SIZES = (10, 30, 100)

#: Идентификаторы видов документов портала (нужны для фильтра поиска).
DOCUMENT_TYPE_IDS = {
    "Федеральный закон": "82a8bf1c-3bc7-47ed-827f-7affd43a7f27",
    "Постановление": "fd5a8766-f6fd-4ac2-8fd9-66f414d314ac",
    "Приказ": "2dddb344-d3e2-4785-a899-7aa12bd47b6f",
    "Кодекс": "36fd214d-52fb-44b8-b22d-6f9e2f5004d8",
    "Указ": "0790e34b-784b-4372-884e-3282622a24bd",
    "Распоряжение": "7ff5b3b5-3757-44f1-bb76-3766cabe3593",
}

#: Путь к списку источников относительно корня проекта.
SOURCES_RELATIVE_PATH = os.path.join("config", "legal_sources.json")

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ─────────────────────────────────────────────────────────────
# Модели
# ─────────────────────────────────────────────────────────────

@dataclass
class LegalDocument:
    """Один правовой акт: метаданные + пути к скачанным файлам."""

    id: str
    title: str
    number: str = ""
    doc_date: str = ""
    publish_date: str = ""
    authority: str = ""
    doc_type: str = ""
    category: str = ""
    source: str = ""
    url: str = ""
    file_url: str = ""
    eo_number: str = ""
    nd: str = ""
    short_title: str = ""
    original_path: str = ""
    text_path: str = ""
    text_chars: int = 0
    checksum: str = ""
    ocr_used: bool = False
    downloaded_at: str = ""
    updated_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Словарь для базы метаданных (все поля совпадают с колонками)."""
        return asdict(self)

    @property
    def display_title(self) -> str:
        """Короткое имя для списков интерфейса (короткое — если задано)."""
        return self.short_title or self.title

    @property
    def is_downloaded(self) -> bool:
        """Скачан ли оригинал."""
        return bool(self.original_path and os.path.exists(self.original_path))


@dataclass
class TextResult:
    """Итог извлечения текста из файла."""

    text: str = ""
    ocr_used: bool = False
    pages: int = 0
    error: str = ""


@dataclass
class DownloadResult:
    """Итог скачивания одного документа."""

    document: LegalDocument
    original_path: str = ""
    text_path: str = ""
    text_chars: int = 0
    ocr_used: bool = False
    skipped: bool = False
    unavailable: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        """Успешно ли скачивание (файл на месте, ошибок нет)."""
        return bool(self.original_path) and not self.error

    @property
    def failed(self) -> bool:
        """Настоящая ошибка — не «пропущено» и не «нет источника на портале»."""
        return bool(self.error) and not self.unavailable


# ─────────────────────────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────────────────────────

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def translit(value: str) -> str:
    """
    Переводит строку в латиницу (для имён файлов).

    Кириллица в именах файлов допустима, но латиница надёжнее: её одинаково
    видят Windows, git и внешние утилиты.

    :param value: исходная строка;
    :return: строка из латиницы, цифр и дефисов.
    """
    out = []
    for char in (value or "").lower():
        if char in _TRANSLIT:
            out.append(_TRANSLIT[char])
        elif char.isalnum() and char.isascii():
            out.append(char)
        else:
            out.append("-")
    return re.sub(r"-{2,}", "-", "".join(out)).strip("-")


def slugify(document: "LegalDocument", limit: int = 80) -> str:
    """
    Строит имя файла документа: ``<дата>-<номер>-<название>``.

    :param document: документ;
    :param limit: предельная длина имени без расширения;
    :return: имя файла без расширения.
    """
    parts = [
        document.doc_date or document.publish_date or "",
        document.number or "",
        document.short_title or document.title or document.id,
    ]
    raw = "-".join(part for part in parts if part)
    slug = translit(raw)[:limit].strip("-")
    return slug or translit(document.id) or "document"


def normalize_date(value: Any) -> str:
    """
    Приводит дату к ISO (``YYYY-MM-DD``).

    Понимает форматы портала: ``2020-12-21T00:00:00``, ``21.12.2020``.

    :param value: дата строкой, datetime/date или None;
    :return: дата в ISO или пустая строка.
    """
    if value in (None, ""):
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return ""
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    match = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})", text)
    if match:
        return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
    return ""


def file_checksum(path: str, chunk: int = 1 << 20) -> str:
    """
    Считает SHA-256 файла (для контроля повторных скачиваний).

    :param path: путь к файлу;
    :param chunk: размер блока чтения;
    :return: hex-строка или пустая строка, если файла нет.
    """
    if not path or not os.path.exists(path):
        return ""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _clean_for_match(value: str) -> str:
    """
    Нормализует строку для сравнения названий.

    Убирает разметку, кавычки, лишние пробелы и регистр: «Правил перевозок
    грузов автомобильным транспортом» и «ПРАВИЛА ПЕРЕВОЗОК ГРУЗОВ
    АВТОМОБИЛЬНЫМ ТРАНСПОРТОМ» должны считаться одним и тем же.

    :param value: исходная строка;
    :return: нормализованная строка.
    """
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = text.replace("«", '"').replace("»", '"').replace("“", '"').replace("”", '"')
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _same_number(left: str, right: str) -> bool:
    """
    Сравнивает номера актов, не придираясь к пробелам и регистру.

    :param left: номер из метаданных;
    :param right: ожидаемый номер;
    :return: True, если номера совпадают.
    """
    def normalize(value: Any) -> str:
        return re.sub(r"\s+", "", str(value or "")).upper()

    return bool(normalize(left)) and normalize(left) == normalize(right)


def load_sources(path: Optional[str] = None) -> Dict[str, Any]:
    """
    Читает config/legal_sources.json.

    :param path: путь к файлу; None — файл проекта;
    :return: разобранный словарь источников.
    :raises FileNotFoundError: если файла нет.
    """
    target = path or os.path.join(_project_root, SOURCES_RELATIVE_PATH)
    with open(target, "r", encoding="utf-8") as handle:
        return json.load(handle)


def category_items(sources: Dict[str, Any], category: Optional[str] = None) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Разворачивает категории списка источников в плоский список.

    :param sources: содержимое legal_sources.json;
    :param category: имя категории; None — все категории;
    :return: список пар «категория, запись».
    """
    result: List[Tuple[str, Dict[str, Any]]] = []
    for name, items in (sources.get("categories") or {}).items():
        if category and name != category:
            continue
        for item in items or []:
            result.append((name, item))
    return result


# ─────────────────────────────────────────────────────────────
# Извлечение текста
# ─────────────────────────────────────────────────────────────

def html_to_text(html: str) -> str:
    """
    Превращает HTML в плоский текст без разметки.

    Скрипты и стили выбрасываются, блочные теги превращаются в переводы
    строк — иначе абзацы акта склеиваются в одну строку.

    :param html: разметка;
    :return: текст.
    """
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover — bs4 есть в окружении проекта
        return re.sub(r"<[^>]+>", " ", html)

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "head"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def rtf_to_text(raw: str) -> str:
    """
    Извлекает текст из RTF без внешних зависимостей.

    Поддерживается ровно то, что встречается в правовых актах: управляющие
    слова отбрасываются, ``\\'xx`` раскрывается как байт в CP1251, перевод
    строки даёт ``\\par``. Полноценный RTF-парсер здесь не нужен: в проекте
    нет зависимости ``striprtf``, а тянуть её ради справочника не стоит.

    :param raw: содержимое RTF;
    :return: текст.
    """
    if not raw:
        return ""
    text = raw
    # Группы, которые не являются текстом документа.
    text = re.sub(r"\{\\\*?\\(?:fonttbl|colortbl|stylesheet|info|pict|latentstyles)[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", " ", text)
    text = text.replace("\\par", "\n").replace("\\line", "\n").replace("\\tab", "\t")

    def _hex_char(match: "re.Match[str]") -> str:
        try:
            return bytes([int(match.group(1), 16)]).decode("cp1251", errors="replace")
        except ValueError:  # pragma: no cover — защита от битой разметки
            return ""

    text = re.sub(r"\\'([0-9a-fA-F]{2})", _hex_char, text)
    text = re.sub(r"\\u(-?\d+)\s?\??", lambda m: chr(int(m.group(1)) % 65536), text)
    text = re.sub(r"\\[a-zA-Z]+-?\d*\s?", " ", text)
    text = text.replace("{", "").replace("}", "")
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _pdf_native_text(path: str, max_pages: int) -> Tuple[str, int]:
    """
    Читает текстовый слой PDF.

    :param path: путь к PDF;
    :param max_pages: сколько страниц просматривать;
    :return: пара «текст, число прочитанных страниц».
    """
    # pypdfium2 объявлен в requirements.txt — он и основной.
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(path)
        try:
            pages = min(len(pdf), max(1, max_pages))
            chunks: List[str] = []
            for index in range(pages):
                page = pdf[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        chunks.append(textpage.get_text_range())
                    finally:
                        textpage.close()
                finally:
                    page.close()
            return "\n".join(chunks), pages
        finally:
            pdf.close()
    except Exception as e:  # noqa: BLE001 — пробуем следующий движок
        logger.debug(f"pypdfium2 не смог прочитать {os.path.basename(path)}: {e}")

    try:  # запасной вариант, если pypdfium2 недоступен
        from pypdf import PdfReader

        reader = PdfReader(path)
        pages = min(len(reader.pages), max(1, max_pages))
        chunks = []
        for index in range(pages):
            chunks.append(reader.pages[index].extract_text() or "")
        return "\n".join(chunks), pages
    except Exception as e:  # noqa: BLE001
        logger.debug(f"pypdf не смог прочитать {os.path.basename(path)}: {e}")
        return "", 0


def _pdf_ocr_text(path: str, dpi: int, lang: str, max_pages: int) -> Tuple[str, int, str]:
    """
    Распознаёт скан PDF локальным Tesseract.

    :param path: путь к PDF;
    :param dpi: разрешение рендера страницы;
    :param lang: языки Tesseract (``rus+eng``);
    :param max_pages: сколько страниц распознавать;
    :return: тройка «текст, число страниц, текст ошибки».
    """
    try:
        import pypdfium2 as pdfium
        import pytesseract
    except ImportError as e:
        return "", 0, f"OCR недоступен: {e}"

    try:
        pdf = pdfium.PdfDocument(path)
    except Exception as e:  # noqa: BLE001
        return "", 0, f"не удалось открыть PDF: {e}"

    chunks: List[str] = []
    pages = 0
    try:
        total = min(len(pdf), max(1, max_pages))
        for index in range(total):
            page = pdf[index]
            bitmap = None
            try:
                bitmap = page.render(scale=max(1.0, dpi / 72.0))
                image = bitmap.to_pil()
                chunks.append(pytesseract.image_to_string(image, lang=lang))
                pages += 1
            except pytesseract.TesseractNotFoundError:
                return "\n".join(chunks), pages, "Tesseract OCR не найден (tesseract.exe не в PATH)"
            except Exception as e:  # noqa: BLE001 — одна плохая страница не рушит документ
                logger.warning(f"OCR: страница {index + 1} в {os.path.basename(path)}: {e}")
            finally:
                if bitmap is not None:
                    bitmap.close()
                page.close()
    finally:
        pdf.close()
    return "\n".join(chunks), pages, ""


def extract_text(path: str, *, allow_ocr: bool = True, dpi: int = 300, lang: str = "rus+eng",
                 min_chars: int = 200, max_pages: int = 80) -> TextResult:
    """
    Извлекает текст из скачанного файла.

    Поддерживаются PDF, HTML/HTM, TXT, RTF, DOCX и ZIP (внутри архива
    выбирается самый пригодный файл). Для PDF без текстового слоя
    включается OCR.

    :param path: путь к файлу;
    :param allow_ocr: разрешён ли OCR;
    :param dpi: разрешение рендера страницы для OCR;
    :param lang: языки Tesseract;
    :param min_chars: порог, ниже которого текстовый слой считается пустым;
    :param max_pages: предел страниц для PDF;
    :return: TextResult с текстом и признаком OCR.
    """
    if not path or not os.path.exists(path):
        return TextResult(error="файл не найден")

    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            text, pages = _pdf_native_text(path, max_pages)
            if len(text.strip()) >= min_chars:
                return TextResult(text=text, pages=pages)
            if not allow_ocr:
                return TextResult(text=text, pages=pages,
                                  error="текстовый слой пуст, OCR отключён")
            ocr_text, ocr_pages, error = _pdf_ocr_text(path, dpi, lang, max_pages)
            if error and not ocr_text.strip():
                return TextResult(text=text, pages=pages, error=error)
            return TextResult(text=ocr_text or text, ocr_used=bool(ocr_text.strip()),
                              pages=ocr_pages or pages, error=error)

        if ext in (".html", ".htm"):
            for encoding in ("utf-8", "cp1251"):
                try:
                    with open(path, "r", encoding=encoding) as handle:
                        return TextResult(text=html_to_text(handle.read()))
                except UnicodeDecodeError:
                    continue
            return TextResult(error="не удалось определить кодировку HTML")

        if ext == ".rtf":
            for encoding in ("cp1251", "utf-8"):
                try:
                    with open(path, "r", encoding=encoding, errors="strict") as handle:
                        return TextResult(text=rtf_to_text(handle.read()))
                except UnicodeDecodeError:
                    continue
            with open(path, "r", encoding="cp1251", errors="replace") as handle:
                return TextResult(text=rtf_to_text(handle.read()))

        if ext == ".docx":
            from docx import Document

            document = Document(path)
            parts = [paragraph.text for paragraph in document.paragraphs]
            for table in document.tables:
                for row in table.rows:
                    parts.append(" | ".join(cell.text.strip() for cell in row.cells))
            return TextResult(text="\n".join(part for part in parts if part.strip()))

        if ext == ".txt":
            for encoding in ("utf-8", "cp1251"):
                try:
                    with open(path, "r", encoding=encoding) as handle:
                        return TextResult(text=handle.read())
                except UnicodeDecodeError:
                    continue
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return TextResult(text=handle.read())

        if ext == ".zip":
            return _extract_from_zip(path, allow_ocr=allow_ocr, dpi=dpi, lang=lang,
                                     min_chars=min_chars, max_pages=max_pages)

        return TextResult(error=f"формат {ext or 'без расширения'} не поддерживается")
    except Exception as e:  # noqa: BLE001 — ошибка чтения не должна ронять парсер
        logger.warning(f"Извлечение текста из {os.path.basename(path)}: {e}")
        return TextResult(error=f"{type(e).__name__}: {e}")


def _extract_from_zip(path: str, **kwargs: Any) -> TextResult:
    """
    Извлекает текст из ZIP-архива (берётся самый «читаемый» файл внутри).

    :param path: путь к архиву;
    :param kwargs: параметры extract_text;
    :return: TextResult.
    """
    priority = {".html": 0, ".htm": 0, ".txt": 1, ".rtf": 2, ".docx": 3, ".pdf": 4}
    temp_dir = tempfile.mkdtemp(prefix="legal_zip_")
    try:
        with zipfile.ZipFile(path) as archive:
            members = [
                name for name in archive.namelist()
                if not name.endswith("/") and os.path.splitext(name)[1].lower() in priority
            ]
            if not members:
                return TextResult(error="в архиве нет файлов поддерживаемых форматов")
            members.sort(key=lambda name: priority.get(os.path.splitext(name)[1].lower(), 9))
            best = members[0]
            target = os.path.join(temp_dir, os.path.basename(best))
            with archive.open(best) as source, open(target, "wb") as dest:
                shutil.copyfileobj(source, dest)
        result = extract_text(target, **kwargs)
        result.error = result.error or ("" if result.text.strip() else "в архиве пустой текст")
        return result
    except zipfile.BadZipFile as e:
        return TextResult(error=f"битый ZIP: {e}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def save_text(text: str, path: str) -> int:
    """
    Сохраняет текст документа в UTF-8 и возвращает число символов.

    :param text: текст;
    :param path: путь к TXT;
    :return: длина записанного текста.
    """
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text or "")
    return len(text or "")


# ─────────────────────────────────────────────────────────────
# REST-API портала опубликования
# ─────────────────────────────────────────────────────────────

class PublicationApi:
    """
    Клиент REST-API портала опубликования (``publication.pravo.gov.ru/api``).

    Проверенные возможности (диагностика 2026-10):
      * ``GET /Documents?pageSize=100&index=N`` — страница списка;
      * ``name=``, ``number=``, ``DocumentTypes=``, ``SortedBy=`` — фильтры;
      * ``SortedBy=1`` — от старых к новым, по умолчанию — от новых;
      * ``GET /DocumentTypes`` и ``/SignatoryAuthorities`` — справочники;
      * ``GET /file/pdf/<номер опубликования>`` — сам документ (скан).
    Фильтра по дате в API нет, поэтому период отбирается на клиенте.
    """

    def __init__(self, session: Optional[requests.Session] = None, timeout: int = 60,
                 retries: int = 3, pause: float = 2.0, user_agent: str = DEFAULT_USER_AGENT,
                 base_url: str = PUBLICATION_API):
        """``session`` подменяется в тестах; сеть трогается только при запросе."""
        self.base_url = base_url.rstrip("/")
        self.timeout = int(timeout)
        self.retries = max(1, int(retries))
        self.pause = float(pause)
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", user_agent)
        self.session.headers.setdefault("Accept", "application/json")
        self._types: Optional[List[Dict[str, Any]]] = None
        self._authorities: Optional[List[Dict[str, Any]]] = None

    # ── низкий уровень ──

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None,
             expect_json: bool = True) -> Any:
        """
        Выполняет GET с повторами.

        :param path: путь от базы API;
        :param params: параметры запроса;
        :param expect_json: разбирать ли ответ как JSON;
        :return: разобранный JSON или requests.Response;
        :raises RuntimeError: если все попытки исчерпаны.
        """
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        last_error = ""
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 200:
                    return response.json() if expect_json else response
                last_error = f"HTTP {response.status_code}"
                if response.status_code < 500 and response.status_code != 429:
                    break
            except (requests.RequestException, ValueError, OSError) as e:
                last_error = f"{type(e).__name__}: {e}"
            if attempt < self.retries:
                time.sleep(self.pause)
        raise RuntimeError(f"Портал опубликования: {url} — {last_error}")

    # ── справочники ──

    def document_types(self, use_cache: bool = True) -> List[Dict[str, Any]]:
        """
        Справочник видов документов (``name`` + ``id``).

        :param use_cache: брать из памяти при повторном вызове;
        :return: список словарей справочника.
        """
        if use_cache and self._types is not None:
            return self._types
        data = self._get("DocumentTypes")
        self._types = data if isinstance(data, list) else []
        return self._types

    def signatory_authorities(self, use_cache: bool = True) -> List[Dict[str, Any]]:
        """
        Справочник органов, подписавших документ.

        :param use_cache: брать из памяти при повторном вызове;
        :return: список словарей справочника.
        """
        if use_cache and self._authorities is not None:
            return self._authorities
        try:
            data = self._get("SignatoryAuthorities")
        except RuntimeError as e:
            logger.warning(f"Справочник органов недоступен: {e}")
            data = []
        self._authorities = data if isinstance(data, list) else []
        return self._authorities

    def type_id(self, doc_type: str) -> str:
        """
        Идентификатор вида документа по его названию.

        :param doc_type: название вида («Федеральный закон») или сам id;
        :return: id для параметра DocumentTypes или пустая строка.
        """
        name = (doc_type or "").strip()
        if not name:
            return ""
        if re.fullmatch(r"[0-9a-fA-F-]{36}", name):
            return name
        if name in DOCUMENT_TYPE_IDS:
            return DOCUMENT_TYPE_IDS[name]
        for item in self.document_types():
            if str(item.get("name", "")).strip().lower() == name.lower():
                return str(item.get("id") or "")
        return ""

    def authority_name(self, authority_id: str) -> str:
        """
        Название органа по его идентификатору.

        :param authority_id: GUID из поля signatoryAuthorityId;
        :return: название или пустая строка.
        """
        if not authority_id:
            return ""
        for item in self.signatory_authorities():
            if str(item.get("id")) == str(authority_id):
                return str(item.get("name") or "")
        return ""

    # ── документы ──

    def page(self, index: int = 1, page_size: int = 100,
             name: Optional[str] = None, number: Optional[str] = None,
             document_types: Optional[Iterable[str]] = None,
             authority: Optional[str] = None,
             sorted_by: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Одна страница списка документов.

        :param index: номер страницы (с 1);
        :param page_size: размер страницы — только 10, 30 или 100;
        :param name: подстрока названия;
        :param number: номер акта;
        :param document_types: идентификаторы видов документов;
        :param authority: идентификатор органа, подписавшего акт (ГУИД);
        :param sorted_by: 1 — от старых к новым, 0/None — от новых к старым;
        :return: список записей API.
        """
        size = int(page_size)
        if size not in ALLOWED_PAGE_SIZES:
            size = min(ALLOWED_PAGE_SIZES, key=lambda value: abs(value - size))
        params: Dict[str, Any] = {"pageSize": size, "index": max(1, int(index))}
        if name:
            params["name"] = name
        if number:
            params["number"] = number
        types = [t for t in (document_types or []) if t]
        if types:
            params["DocumentTypes"] = list(types)
        if authority:
            params["SignatoryAuthorityId"] = str(authority)
        if sorted_by is not None:
            params["SortedBy"] = int(sorted_by)

        data = self._get("Documents", params=params)
        if isinstance(data, dict):
            items = data.get("items") or []
            return items if isinstance(items, list) else []
        return data if isinstance(data, list) else []

    def iter_documents(self, name: Optional[str] = None, number: Optional[str] = None,
                       document_types: Optional[Iterable[str]] = None,
                       authority: Optional[str] = None,
                       max_pages: int = 5, page_size: int = 100,
                       sorted_by: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        """
        Перебирает страницы списка документов.

        :param name: подстрока названия;
        :param number: номер акта;
        :param document_types: идентификаторы видов;
        :param authority: идентификатор органа, подписавшего акт;
        :param max_pages: сколько страниц просматривать;
        :param page_size: размер страницы;
        :param sorted_by: порядок сортировки;
        :return: итератор записей API.
        """
        for index in range(1, max(1, max_pages) + 1):
            items = self.page(index=index, page_size=page_size, name=name, number=number,
                              document_types=document_types, authority=authority,
                              sorted_by=sorted_by)
            if not items:
                return
            for item in items:
                yield item
            if len(items) < page_size:
                return

    def pdf_url(self, eo_number: str) -> str:
        """
        Адрес официального PDF документа.

        :param eo_number: номер официального опубликования;
        :return: URL.
        """
        return f"{self.base_url.replace('/api', '')}/file/pdf/{eo_number}"

    def document_url(self, eo_number: str) -> str:
        """
        Адрес страницы документа на портале.

        :param eo_number: номер официального опубликования;
        :return: URL.
        """
        return f"{self.base_url.replace('/api', '')}/document/{eo_number}"

    def download(self, url: str, dest: str) -> str:
        """
        Скачивает файл по URL (с повторами).

        :param url: адрес файла;
        :param dest: путь сохранения;
        :return: путь к сохранённому файлу;
        :raises RuntimeError: если скачать не удалось.
        """
        directory = os.path.dirname(os.path.abspath(dest))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        last_error = ""
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout, stream=True)
                if response.status_code == 200:
                    with open(dest, "wb") as handle:
                        for block in response.iter_content(chunk_size=1 << 16):
                            if block:
                                handle.write(block)
                    return dest
                last_error = f"HTTP {response.status_code}"
                if response.status_code < 500 and response.status_code != 429:
                    break
            except (requests.RequestException, OSError) as e:
                last_error = f"{type(e).__name__}: {e}"
            if attempt < self.retries:
                time.sleep(self.pause)
        raise RuntimeError(f"Портал опубликования: не скачан {url} — {last_error}")


# ─────────────────────────────────────────────────────────────
# Банк «Законодательство России»
# ─────────────────────────────────────────────────────────────

class IpsApi:
    """
    Клиент банка «Законодательство России» (``pravo.gov.ru/proxy/ips``).

    Отдаёт консолидированный текст акта в HTML по идентификатору ``nd``.
    Страницы в CP1251. Это единственный канал портала, где у документов
    есть текстовый слой, поэтому кодексы берутся отсюда.

    Поиска здесь нет намеренно: у банка он устроен как набор фреймов,
    которыми управляет JavaScript, и без браузера не воспроизводится
    (проверено: параметры поиска сервер игнорирует). Поэтому ``nd``
    задаётся в config/legal_sources.json явно.
    """

    def __init__(self, session: Optional[requests.Session] = None, timeout: int = 90,
                 retries: int = 3, pause: float = 2.0, user_agent: str = DEFAULT_USER_AGENT,
                 base_url: str = IPS_BASE):
        """``session`` подменяется в тестах; сеть трогается только при запросе."""
        self.base_url = base_url
        self.timeout = int(timeout)
        self.retries = max(1, int(retries))
        self.pause = float(pause)
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", user_agent)

    def document_html(self, nd: str, page: int = 1) -> str:
        """
        HTML документа по его ``nd``.

        :param nd: идентификатор документа в банке;
        :param page: номер страницы (банк отдаёт документ частями);
        :return: HTML в Unicode;
        :raises RuntimeError: если документ не получен.
        """
        url = self.base_url
        params = {"doc_itself": "", "nd": str(nd), "page": int(page), "rdk": 0}
        last_error = ""
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 200 and response.content:
                    response.encoding = "cp1251"
                    return response.text
                last_error = f"HTTP {response.status_code}"
            except (requests.RequestException, OSError) as e:
                last_error = f"{type(e).__name__}: {e}"
            if attempt < self.retries:
                time.sleep(self.pause)
        raise RuntimeError(f"Законодательство России: nd={nd} — {last_error}")

    def document_title(self, nd: str) -> str:
        """
        Заголовок документа (``<title>`` страницы банка).

        :param nd: идентификатор документа;
        :return: заголовок или пустая строка.
        """
        try:
            html = self.document_html(nd)
        except RuntimeError as e:
            logger.warning(f"Заголовок nd={nd} не получен: {e}")
            return ""
        match = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
        return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""

    @staticmethod
    def url(nd: str) -> str:
        """Адрес страницы документа в банке (для метаданных)."""
        return f"{IPS_BASE}?doc_itself=&nd={nd}&page=1&rdk=0"


# ─────────────────────────────────────────────────────────────
# Парсер
# ─────────────────────────────────────────────────────────────

class LegalParser:
    """
    Парсер правовой базы: поиск, скачивание, мониторинг обновлений.

    Пример:
        parser = LegalParser()
        docs = parser.search("перевозка грузов", doc_type="Федеральный закон")
        parser.download(docs[0].id)
        parser.download_all(category="government_decrees")
        parser.list_updates("2026-01-01")

    Сеть используется только в методах search/download/list_updates;
    конструктор её не трогает, поэтому объект безопасно создавать в тестах
    и в интерфейсе.
    """

    def __init__(self, sources_path: Optional[str] = None, data_dir: Optional[str] = None,
                 db_path: Optional[str] = None, session: Optional[requests.Session] = None,
                 publication: Optional[PublicationApi] = None, ips: Optional[IpsApi] = None,
                 sources: Optional[Dict[str, Any]] = None, ocr: Optional[bool] = None,
                 progress: Optional[Callable[[str, int, int], None]] = None):
        """
        :param sources_path: путь к config/legal_sources.json;
        :param data_dir: корень хранилища (по умолчанию из списка источников);
        :param db_path: путь к базе метаданных (по умолчанию — из модуля);
        :param session: готовая requests-сессия (в тестах — MOCK-транспорт);
        :param publication: готовый клиент портала опубликования;
        :param ips: готовый клиент банка «Законодательство России»;
        :param sources: готовый разбор списка источников (минуя файл);
        :param ocr: включён ли OCR (None — из настроек списка источников);
        :param progress: обратный вызов «(этап, сделано, всего)» для интерфейса.
        """
        self.sources_path = sources_path
        self.sources = sources if sources is not None else load_sources(sources_path)
        storage = self.sources.get("storage") or {}
        self.storage = storage

        self.publication = publication or PublicationApi(
            session=session,
            timeout=int(storage.get("timeout", 60)),
            retries=int(storage.get("retries", 3)),
            pause=float(storage.get("retry_pause", 2.0)),
            user_agent=str(storage.get("user_agent", DEFAULT_USER_AGENT)),
        )
        self.ips = ips or IpsApi(
            session=session,
            timeout=max(90, int(storage.get("timeout", 60))),
            retries=int(storage.get("retries", 3)),
            pause=float(storage.get("retry_pause", 2.0)),
            user_agent=str(storage.get("user_agent", DEFAULT_USER_AGENT)),
        )

        base_dir = data_dir or os.path.join(_project_root, str(storage.get("dir", "data/knowledge/legal")))
        self.data_dir = base_dir
        self.original_dir = os.path.join(base_dir, str(storage.get("original_subdir", "original")))
        self.text_dir = os.path.join(base_dir, str(storage.get("text_subdir", "text")))
        self.cache_dir = os.path.join(base_dir, str(storage.get("cache_subdir", "cache")))
        for directory in (self.original_dir, self.text_dir, self.cache_dir):
            os.makedirs(directory, exist_ok=True)

        self.db_path = db_path or legal_metadata.legal_db_path()
        self.ocr_enabled = bool(storage.get("ocr", True)) if ocr is None else bool(ocr)
        self.progress = progress
        self._search_cache: Dict[str, List[LegalDocument]] = {}

    # ── служебное ──

    def _notify(self, stage: str, done: int, total: int) -> None:
        """Сообщает интерфейсу о прогрессе (если обработчик задан)."""
        if self.progress is None:
            return
        try:
            self.progress(stage, done, total)
        except Exception as e:  # noqa: BLE001 — интерфейс не должен ронять парсер
            logger.debug(f"Обработчик прогресса упал: {e}")

    def _connect(self):
        """Соединение с базой метаданных (с созданием схемы)."""
        conn = legal_metadata.get_connection(self.db_path)
        legal_metadata.init_db(conn)
        return conn

    def _save(self, document: LegalDocument) -> LegalDocument:
        """Пишет документ в базу метаданных."""
        saved = legal_metadata.save_document(document.to_dict(), conn=self._connect())
        return LegalDocument(**{key: saved[key] for key in saved if key in LegalDocument.__dataclass_fields__})

    def _from_api(self, item: Dict[str, Any], category: str = "") -> LegalDocument:
        """Преобразует запись API портала в LegalDocument."""
        eo = str(item.get("eoNumber") or "")
        return LegalDocument(
            id=f"eo:{eo}" if eo else f"api:{item.get('id')}",
            title=re.sub(r"<[^>]+>", "", str(item.get("complexName") or item.get("name") or "")).strip(),
            number=str(item.get("number") or "").strip(),
            doc_date=normalize_date(item.get("documentDate")),
            publish_date=normalize_date(item.get("publishDateShort") or item.get("viewDate")),
            authority=self.publication.authority_name(str(item.get("signatoryAuthorityId") or "")),
            doc_type="",
            category=category,
            source="publication",
            url=self.publication.document_url(eo) if eo else "",
            file_url=self.publication.pdf_url(eo) if eo else "",
            eo_number=eo,
            extra={
                "pages": item.get("pagesCount"),
                "pdf_size": item.get("pdfFileLength"),
                "api_id": item.get("id"),
            },
        )

    # ── поиск ──

    def search(self, query: str, doc_type: Optional[str] = None, date_from: Optional[str] = None,
               limit: int = 20, max_pages: int = 3, category: str = "") -> List[LegalDocument]:
        """
        Ищет документы на портале опубликования.

        Фильтра по дате в API нет, поэтому ``date_from`` применяется к уже
        полученной странице (документы идут от новых к старым, так что
        достаточно отбросить всё старше порога).

        :param query: строка поиска (подстрока названия);
        :param doc_type: вид документа («Федеральный закон») или его id;
        :param date_from: нижняя граница даты публикации в ISO;
        :param limit: сколько документов вернуть;
        :param max_pages: сколько страниц API просматривать;
        :param category: категория для сохранённых метаданных;
        :return: список найденных документов.
        """
        term = (query or "").strip()
        cache_key = f"{term}|{doc_type or ''}|{date_from or ''}|{limit}|{max_pages}"
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        type_id = self.publication.type_id(doc_type) if doc_type else ""
        items = self.publication.iter_documents(
            name=term or None,
            document_types=[type_id] if type_id else None,
            max_pages=max_pages,
            page_size=int(self.storage.get("page_size", 100)),
        )

        threshold = normalize_date(date_from)
        found: List[LegalDocument] = []
        for item in items:
            document = self._from_api(item, category=category)
            if type_id and doc_type:
                document.doc_type = str(doc_type)
            published = document.publish_date or document.doc_date
            if threshold and published and published < threshold:
                continue
            if not published and threshold:
                continue
            found.append(document)
            if len(found) >= max(1, int(limit)):
                break

        self._search_cache[cache_key] = found
        logger.info(f"Поиск «{term}»: найдено {len(found)} документов")
        return found

    def find_document(self, number: str, doc_type: Optional[str] = None,
                      date_hint: str = "", name_hint: str = "") -> Optional[LegalDocument]:
        """
        Находит конкретный акт по номеру, дате и названию.

        Совпадение только по номеру считается НЕДОСТАТОЧНЫМ: номеров вида
        «259-ФЗ» на портале десятки (по одному на каждый год), и выбрать из
        них «похожий» — значит однажды положить в правовую базу чужой акт.
        Поэтому кандидат принимается, только если кроме номера совпала дата
        или название.

        :param number: номер акта («2200», «259-ФЗ»);
        :param doc_type: вид документа или его id;
        :param date_hint: ожидаемая дата в ISO — уточняет выбор;
        :param name_hint: подстрока названия — уточняет выбор;
        :return: документ или None, если уверенного совпадения нет.
        """
        if not number and not name_hint:
            return None
        type_id = self.publication.type_id(doc_type) if doc_type else ""
        items: List[Dict[str, Any]] = []
        if number:
            items = list(self.publication.iter_documents(
                number=number,
                document_types=[type_id] if type_id else None,
                max_pages=2,
                page_size=int(self.storage.get("page_size", 100)),
            ))
        if not items and name_hint:
            items = list(self.publication.iter_documents(
                name=name_hint,
                document_types=[type_id] if type_id else None,
                max_pages=2,
                page_size=int(self.storage.get("page_size", 100)),
            ))
        if not items:
            return None

        expected = normalize_date(date_hint)
        needle = _clean_for_match(name_hint)[:60]
        best: Optional[LegalDocument] = None
        best_score = 0
        for item in items:
            document = self._from_api(item)
            if doc_type:
                document.doc_type = str(doc_type)
            number_ok = bool(number) and _same_number(document.number, number)
            date_ok = bool(expected) and document.doc_date == expected
            name_ok = bool(needle) and needle in _clean_for_match(document.title)
            if not (number_ok or name_ok):
                continue
            # Номер без подтверждения датой или названием — не улика.
            if number_ok and not (date_ok or name_ok):
                continue
            score = (5 if number_ok else 0) + (5 if date_ok else 0) + (3 if name_ok else 0)
            if score > best_score:
                best, best_score = document, score
        return best

    # ── обновления ──

    def list_updates(self, since_date: str, query: Optional[str] = None,
                     doc_type: Optional[str] = None, limit: int = 200,
                     max_pages: int = 3, authority: str = "") -> List[LegalDocument]:
        """
        Документы, опубликованные не раньше указанной даты.

        Страницы API идут от новых к старым, поэтому обход прекращается,
        как только встретилась запись старше порога.

        :param since_date: дата в ISO (``YYYY-MM-DD``);
        :param query: необязательная подстрока названия;
        :param doc_type: необязательный вид документа;
        :param limit: предел числа документов;
        :param max_pages: сколько страниц API просматривать;
        :param authority: идентификатор органа, подписавшего акт (ГУИД) —
            так лента сужается до нужного ведомства;
        :return: список документов (от новых к старым).
        """
        threshold = normalize_date(since_date)
        if not threshold:
            raise ValueError("list_updates: дата должна быть в формате ГГГГ-ММ-ДД")
        type_id = self.publication.type_id(doc_type) if doc_type else ""

        found: List[LegalDocument] = []
        page_size = int(self.storage.get("page_size", 100))
        for index in range(1, max(1, max_pages) + 1):
            items = self.publication.page(
                index=index, page_size=page_size, name=query or None,
                document_types=[type_id] if type_id else None,
                authority=authority or None,
            )
            if not items:
                break
            stop = False
            for item in items:
                document = self._from_api(item)
                published = document.publish_date or document.doc_date
                if published and published < threshold:
                    stop = True
                    break
                found.append(document)
                if len(found) >= max(1, int(limit)):
                    stop = True
                    break
            if stop or len(items) < page_size:
                break
        logger.info(f"Обновления с {threshold}: {len(found)} документов")
        return found

    # ── список источников ──

    def sources_categories(self) -> Dict[str, int]:
        """Число документов в каждой категории списка источников."""
        return {
            name: len(items or [])
            for name, items in (self.sources.get("categories") or {}).items()
        }

    def keywords(self) -> List[str]:
        """Ключевые слова поиска из списка источников."""
        return [str(word) for word in (self.sources.get("keywords") or [])]

    def monitor_authorities(self) -> Dict[str, str]:
        """
        Ведомства, за которыми следит мониторинг обновлений.

        Берутся из блока ``monitor.authorities`` списка источников:
        «название → ГУИД органа на портале». Пустой словарь означает, что
        ведомства не заданы и мониторинг пойдёт по ключевым словам.

        :return: словарь «название органа → ГУИД».
        """
        raw = (self.sources.get("monitor") or {}).get("authorities") or {}
        if isinstance(raw, list):  # допускается и список ГУИДов без названий
            return {str(guid): str(guid) for guid in raw}
        return {str(name): str(guid) for name, guid in raw.items() if guid}

    def resolve_item(self, item: Dict[str, Any], category: str = "") -> Optional[LegalDocument]:
        """
        Превращает запись списка источников в документ с метаданными.

        Порядок: явный ``nd`` (банк «Законодательство России») → явный ``eo``
        (номер опубликования, метаданные из списка) → поиск по ``query``.
        Записи с ``"unavailable": true`` (акт опубликован до 2011 года и в
        открытом доступе портала его нет) возвращаются как «ручные»: файл
        для них кладёт оператор.

        :param item: запись из config/legal_sources.json;
        :param category: имя категории;
        :return: документ или None, если ничего не нашлось.
        """
        doc_id = f"cfg:{item.get('id')}" if item.get("id") else ""
        base = LegalDocument(
            id=doc_id,
            title=str(item.get("title") or item.get("short") or ""),
            short_title=str(item.get("short") or ""),
            number=str(item.get("number") or ""),
            doc_date=normalize_date(item.get("date")),
            authority=str(item.get("authority") or ""),
            doc_type=str(item.get("doc_type") or ""),
            category=category,
            nd=str(item.get("nd") or ""),
            eo_number=str(item.get("eo") or ""),
            extra={"topics": list(item.get("topics") or []), "required": bool(item.get("required"))},
        )

        if item.get("unavailable"):
            base.source = "manual"
            base.extra["unavailable"] = True
            base.extra["note"] = str(item.get("note") or "")
            base.url = str(item.get("url") or "")
            return base

        if base.nd:
            base.source = "ips"
            base.url = IpsApi.url(base.nd)
            base.file_url = base.url
            title = self.ips.document_title(base.nd)
            if title:
                # Заголовок банка короче описания из списка — уточняем только
                # если в списке не задано своё пояснение.
                if not item.get("title"):
                    base.title = title
                base.extra["ips_title"] = title
            return base

        if base.eo_number:
            base.source = "publication"
            base.url = self.publication.document_url(base.eo_number)
            base.file_url = self.publication.pdf_url(base.eo_number)
            return base

        query = str(item.get("query") or item.get("title") or "").strip()
        if not query:
            return None
        try:
            found = self.find_document(
                number=base.number, doc_type=base.doc_type or None,
                date_hint=base.doc_date, name_hint=query,
            )
            if found is None:
                # Свободный поиск принимается только при совпадении названия:
                # иначе в базу попадёт случайный одноимённый акт другого года.
                needle = _clean_for_match(query)[:60]
                for candidate in self.search(query, doc_type=base.doc_type or None, limit=10):
                    if needle and needle in _clean_for_match(candidate.title):
                        found = candidate
                        break
        except RuntimeError as e:
            # Портал недоступен: это не «документа нет», а «не смогли узнать».
            # Запись не выдумываем, причину пишем в лог.
            logger.warning(f"Источник «{item.get('short') or item.get('id')}»: портал недоступен — {e}")
            return None
        if found is None:
            logger.warning(f"Источник «{item.get('short') or item.get('id')}» не найден на портале")
            return None
        found.category = category
        found.short_title = base.short_title
        found.extra = {**found.extra, **base.extra}
        if base.doc_date and not found.doc_date:
            found.doc_date = base.doc_date
        if base.authority and not found.authority:
            found.authority = base.authority
        return found

    # ── скачивание ──

    def download(self, doc_id: str, save_dir: Optional[str] = None) -> DownloadResult:
        """
        Скачивает документ и извлекает из него текст.

        Для документов банка «Законодательство России» сохраняется HTML
        (в нём уже есть текст), для остальных — официальный PDF портала.

        :param doc_id: идентификатор документа (``eo:…``, ``nd:…``, ``cfg:…``);
        :param save_dir: каталог оригиналов (по умолчанию — из настроек);
        :return: DownloadResult.
        """
        conn = self._connect()
        try:
            row = legal_metadata.get_document(doc_id, conn=conn)
        finally:
            conn.close()
        if row is None:
            # Идентификатор мог прийти из поиска, где запись ещё не сохранена.
            raise KeyError(f"Документ {doc_id} не найден в правовой базе — сначала сохраните метаданные")

        document = LegalDocument(**{key: row[key] for key in row if key in LegalDocument.__dataclass_fields__})
        document.extra = row.get("extra") or {}
        return self.download_document(document, save_dir=save_dir)

    def download_document(self, document: LegalDocument, save_dir: Optional[str] = None) -> DownloadResult:
        """
        Скачивает конкретный документ (без обращения к базе за метаданными).

        :param document: документ с заполненными ``source``/``eo_number``/``nd``;
        :param save_dir: каталог оригиналов;
        :return: DownloadResult.
        """
        original_dir = save_dir or self.original_dir
        os.makedirs(original_dir, exist_ok=True)
        os.makedirs(self.text_dir, exist_ok=True)
        slug = slugify(document)
        result = DownloadResult(document=document)

        if document.source == "manual" or document.extra.get("unavailable"):
            # Акт опубликован до 2011 года — на портале опубликования его нет,
            # а в банке «Законодательство России» он лежит под неизвестным
            # нам nd. Файл для такого документа кладёт оператор; индекс его
            # подхватит (core/legal_indexer.py читает папку целиком).
            result.unavailable = True
            result.error = (
                "нет открытого источника на портале (акт опубликован до 2011 г.) — "
                "положите файл в data/knowledge/legal/original вручную"
            )
            self._store_result(document, result)
            return result

        if document.source == "ips" and document.nd:
            return self._download_ips(document, slug, original_dir, result)
        return self._download_publication(document, slug, original_dir, result)

    def _download_ips(self, document: LegalDocument, slug: str, original_dir: str,
                      result: DownloadResult) -> DownloadResult:
        """Скачивает консолидированный текст из банка «Законодательство России»."""
        dest = os.path.join(original_dir, f"{slug}.html")
        try:
            html = self.ips.document_html(document.nd)
        except RuntimeError as e:
            result.error = str(e)
            self._store_result(document, result)
            return result

        with open(dest, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(html)
        text_result = extract_text(dest)
        text = text_result.text
        if not text.strip():
            # HTML банка — таблица; если bs4 не справился, пробуем регулярки.
            text = html_to_text(html)
        text_path = os.path.join(self.text_dir, f"{slug}.txt")
        result.original_path = dest
        result.text_path = text_path
        result.text_chars = save_text(text, text_path)
        result.ocr_used = False
        document.source = "ips"
        if not text.strip():
            result.error = text_result.error or "пустой текст документа"
        self._store_result(document, result)
        return result

    def _download_publication(self, document: LegalDocument, slug: str, original_dir: str,
                              result: DownloadResult) -> DownloadResult:
        """Скачивает официальный PDF портала опубликования и распознаёт его."""
        eo = document.eo_number
        if not eo:
            result.error = "у документа нет номера официального опубликования"
            return result
        url = document.file_url or self.publication.pdf_url(eo)
        dest = os.path.join(original_dir, f"{slug}.pdf")
        try:
            self.publication.download(url, dest)
        except RuntimeError as e:
            result.error = str(e)
            self._store_result(document, result)
            return result

        ocr_kwargs = {
            "allow_ocr": self.ocr_enabled,
            "dpi": int(self.storage.get("ocr_dpi", 300)),
            "lang": str(self.storage.get("ocr_lang", "rus+eng")),
            "min_chars": int(self.storage.get("ocr_min_chars", 200)),
            "max_pages": int(self.storage.get("ocr_max_pages", 80)),
        }
        text_result = extract_text(dest, **ocr_kwargs)
        text_path = os.path.join(self.text_dir, f"{slug}.txt")
        result.original_path = dest
        result.text_path = text_path
        result.text_chars = save_text(text_result.text, text_path)
        result.ocr_used = text_result.ocr_used
        if not text_result.text.strip():
            result.error = text_result.error or "текст не извлечён"
        elif text_result.error:
            document.extra["text_warning"] = text_result.error
        document.extra["pages"] = text_result.pages or document.extra.get("pages")
        self._store_result(document, result)
        return result

    def _store_result(self, document: LegalDocument, result: DownloadResult) -> None:
        """Пишет в базу метаданных итог скачивания (успех или ошибку)."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        document.original_path = result.original_path
        document.text_path = result.text_path
        document.text_chars = result.text_chars
        document.ocr_used = result.ocr_used
        document.checksum = file_checksum(result.original_path)
        if result.original_path:
            document.downloaded_at = now
        if result.error:
            document.extra = {**document.extra, "last_error": result.error}
        else:
            document.extra = {k: v for k, v in document.extra.items() if k != "last_error"}
        document.updated_at = now
        try:
            saved = self._save(document)
        except Exception as e:  # noqa: BLE001 — база не должна терять уже скачанный файл
            logger.error(f"Метаданные {document.id} не сохранены: {e}")
            return
        result.document = saved

    # ── массовые операции ──

    def download_all(self, category: Optional[str] = None, only_required: bool = False,
                     skip_existing: bool = True, limit: Optional[int] = None) -> List[DownloadResult]:
        """
        Скачивает документы из списка источников.

        :param category: имя категории; None — все категории;
        :param only_required: только записи с ``"required": true``;
        :param skip_existing: пропускать уже скачанные (файл на месте);
        :param limit: предел числа документов;
        :return: список результатов по каждому документу.
        """
        items = category_items(self.sources, category)
        if only_required:
            items = [(name, item) for name, item in items if item.get("required")]
        if limit:
            items = items[: int(limit)]

        results: List[DownloadResult] = []
        total = len(items)
        self._notify("prepare", 0, total)
        for index, (name, item) in enumerate(items, start=1):
            label = str(item.get("short") or item.get("id") or "")
            self._notify("resolve", index - 1, total)
            try:
                document = self.resolve_item(item, category=name)
            except Exception as e:  # noqa: BLE001 — один сбойный источник не должен ломать проход
                logger.error(f"Источник «{label}»: {e}")
                document = None
            if document is None:
                results.append(DownloadResult(
                    document=LegalDocument(id=f"cfg:{item.get('id')}", title=str(item.get("title") or label)),
                    error="документ не найден на портале",
                ))
                self._notify("resolve", index, total)
                continue
            self._save(document)
            if skip_existing:
                sticky = self._downloaded_state(document)
                if sticky is not None:
                    results.append(sticky)
                    self._notify("download", index, total)
                    continue
            self._notify("download", index - 1, total)
            try:
                results.append(self.download_document(document))
            except Exception as e:  # noqa: BLE001
                logger.error(f"Скачивание «{label}»: {e}")
                results.append(DownloadResult(document=document, error=f"{type(e).__name__}: {e}"))
            self._notify("download", index, total)

        ok = sum(1 for item in results if item.ok)
        legal_metadata.set_meta("last_download_all", datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                conn=self._connect())
        logger.info(f"Скачивание завершено: успешно {ok} из {len(results)}")
        return results

    def _downloaded_state(self, document: LegalDocument) -> Optional[DownloadResult]:
        """
        Состояние уже скачанного документа — или None, если качать надо.

        Значения берутся из базы, а не из свежей записи списка источников:
        иначе отчёт показывал бы «0 символов» у файла, который лежит на
        диске и успешно распознан в прошлый проход.

        :param document: документ, который собирались качать;
        :return: DownloadResult со ``skipped=True`` либо None.
        """
        existing = self._existing_file(document)
        if not existing:
            return None
        conn = self._connect()
        try:
            row = legal_metadata.get_document(document.id, conn=conn)
        finally:
            conn.close()
        if row:
            document.original_path = row.get("original_path") or existing
            document.text_path = row.get("text_path") or document.text_path
            document.text_chars = int(row.get("text_chars") or 0)
            document.ocr_used = bool(row.get("ocr_used"))
        return DownloadResult(
            document=document,
            original_path=document.original_path or existing,
            text_path=document.text_path,
            text_chars=document.text_chars,
            ocr_used=document.ocr_used,
            skipped=True,
        )

    def _existing_file(self, document: LegalDocument) -> str:
        """Путь к уже скачанному оригиналу, если он есть на диске."""
        for path in (document.original_path, document.text_path):
            if path and os.path.exists(path):
                return document.original_path or path
        slug = slugify(document)
        for extension in (".pdf", ".html"):
            candidate = os.path.join(self.original_dir, f"{slug}{extension}")
            if os.path.exists(candidate):
                return candidate
        return ""

    def update(self, days: int = 30, query: Optional[str] = None,
               categories: Optional[Iterable[str]] = None,
               limit: int = 200, keywords: Optional[Iterable[str]] = None,
               max_pages: int = 40, download: bool = False) -> Dict[str, Any]:
        """
        Ищет (и по желанию скачивает) новые акты за последние N дней.

        Два режима:
          * ``query`` задан — поиск на портале по этой строке;
          * ``query`` пуст — обход ленты по ведомствам из блока
            ``monitor.authorities`` списка источников (если они заданы) либо
            по ключевым словам.

        ВАЖНО про скачивание: ``download=False`` по умолчанию. Проверка
        обновлений — это чтение, и она не должна незаметно тянуть на диск
        десятки мегабайт сканов. Скачивание включается явно (ключ
        ``--download`` у tools/update_legal.py).

        Про ``max_pages``: портал публикует несколько сотен актов в день,
        страница содержит 100 записей, поэтому 40 страниц — это примерно
        двухнедельная лента. Меньшее значение сузит проверку по дате, но
        не сломает её: обход всё равно остановится на границе периода.

        :param days: глубина проверки в днях;
        :param query: подстрока названия; None — обход ленты;
        :param categories: категории, к которым отнести найденное;
        :param limit: предел числа документов, возвращаемых в сводке;
        :param keywords: свои ключевые слова для отбора (None — из списка источников);
        :param max_pages: сколько страниц ленты просматривать;
        :param download: скачивать ли найденное;
        :return: сводка «найдено / скачано / ошибок / документы».
        """
        since = datetime.now().timestamp() - max(1, int(days)) * 86400
        since_date = datetime.fromtimestamp(since).strftime("%Y-%m-%d")
        category = next(iter(categories), "") if categories else ""

        queries = [query] if query else None
        found: Dict[str, LegalDocument] = {}
        if queries:
            # Оператор спросил про конкретную тему — ищем на портале по ней.
            for word in queries:
                for document in self.list_updates(since_date, query=word or None,
                                                  limit=limit, max_pages=max_pages):
                    found.setdefault(document.id, document)
        else:
            authorities = self.monitor_authorities()
            if authorities:
                # Общая проверка идёт по ведомствам: у актов Минтранса и
                # Правительства тема в названии почти никогда не совпадает
                # с ключевым словом («О внесении изменений в…»), поэтому
                # отбор по словам давал бы ноль. Ведомство — надёжный признак.
                for name, guid in authorities.items():
                    for document in self.list_updates(since_date, limit=max(limit, 500),
                                                      max_pages=max_pages, authority=guid):
                        document.extra["monitor_authority"] = name or document.authority
                        found.setdefault(document.id, document)
            else:
                # Ведомства не заданы — обходим всю ленту и отбираем по словам.
                words = [word.lower() for word in
                         (keywords if keywords is not None else self.keywords()) if word]
                for document in self.list_updates(since_date, limit=max(limit, 2000),
                                                  max_pages=max_pages):
                    title = _clean_for_match(document.title)
                    if not words or any(word in title for word in words):
                        found.setdefault(document.id, document)

        downloaded: List[DownloadResult] = []
        for document in found.values():
            if category:
                document.category = category
            self._save(document)
            if not download or self._existing_file(document):
                continue
            try:
                downloaded.append(self.download_document(document))
            except Exception as e:  # noqa: BLE001
                downloaded.append(DownloadResult(document=document, error=f"{type(e).__name__}: {e}"))

        errors = [item.error for item in downloaded if item.failed]
        summary = {
            "since": since_date,
            "found": len(found),
            "downloaded": sum(1 for item in downloaded if item.ok),
            "unavailable": sum(1 for item in downloaded if item.unavailable),
            "errors": len(errors),
            "error_list": errors,
            "documents": [item.document for item in downloaded],
            "found_documents": list(found.values()),
            "download_enabled": bool(download),
        }
        legal_metadata.set_meta("last_update_check", datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                conn=self._connect())
        return summary

    # ── сводка для интерфейса ──

    def stats(self) -> Dict[str, Any]:
        """
        Сводка по базе: число документов, объём текста, размер папок.

        :return: словарь для диалога «Правовая база».
        """
        conn = self._connect()
        try:
            data = legal_metadata.stats(conn=conn)
            data["last_check"] = legal_metadata.get_meta("last_update_check", conn=conn)
            data["last_download"] = legal_metadata.get_meta("last_download_all", conn=conn)
        finally:
            conn.close()
        data["original_bytes"] = _dir_size(self.original_dir)
        data["text_bytes"] = _dir_size(self.text_dir)
        data["data_dir"] = self.data_dir
        return data

    def documents(self, category: Optional[str] = None, query: Optional[str] = None,
                  only_downloaded: bool = False, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Список документов базы для интерфейса.

        :param category: категория;
        :param query: строка поиска по названию/номеру;
        :param only_downloaded: только скачанные;
        :param limit: предел числа строк;
        :return: список словарей.
        """
        conn = self._connect()
        try:
            if query:
                rows = legal_metadata.search_documents(query, limit=limit or 200, conn=conn)
                if category:
                    rows = [row for row in rows if row.get("category") == category]
                if only_downloaded:
                    rows = [row for row in rows if row.get("original_path")]
                return rows
            return legal_metadata.list_documents(category=category, only_downloaded=only_downloaded,
                                                 limit=limit, conn=conn)
        finally:
            conn.close()


def _dir_size(path: str) -> int:
    """
    Суммарный размер файлов в каталоге (в байтах).

    :param path: каталог;
    :return: размер в байтах (0, если каталога нет).
    """
    if not path or not os.path.isdir(path):
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:  # noqa: BLE001 — файл могли удалить во время обхода
                continue
    return total


def human_size(size: int) -> str:
    """
    Размер в удобочитаемом виде («12,3 МБ»).

    :param size: размер в байтах;
    :return: строка.
    """
    value = float(size or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return f"{value:.1f} ГБ"


__all__ = [
    "ALLOWED_PAGE_SIZES",
    "DOCUMENT_TYPE_IDS",
    "DEFAULT_USER_AGENT",
    "DownloadResult",
    "IPS_BASE",
    "IpsApi",
    "LegalDocument",
    "LegalParser",
    "PUBLICATION_API",
    "PUBLICATION_BASE",
    "PublicationApi",
    "TextResult",
    "category_items",
    "extract_text",
    "file_checksum",
    "html_to_text",
    "human_size",
    "load_sources",
    "normalize_date",
    "rtf_to_text",
    "save_text",
    "slugify",
    "translit",
]
