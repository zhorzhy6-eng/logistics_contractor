#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Акты до 2011 года: скачивание с официального банка документов kremlin.ru.

Зачем отдельный инструмент. Портал `pravo.gov.ru` ведётся с 2011 года, и
актов, опубликованных раньше, в нём нет — записи помечены
``"unavailable": true`` (`config/legal_sources.json`). Банк «Законодательство
России» (`pravo.gov.ru/proxy/ips`) отдаёт консолидированные тексты и старых
актов, но идентификаторы ``nd`` для них неизвестны, а поиск по банку устроен
на JavaScript и без браузера не работает (проверено, см. `docs/LEGAL_BASE.md`
§ 2.3 и § 7).

Поэтому источник — **официальный банк документов Президента России**
`kremlin.ru/acts/bank/<номер>`. Это тот же официальный портал правовой
информации, который используется в проекте как источник актов: тексты
федеральных законов публикуются там с 1917 года, и для актов до 2011 года
это единственный доступный официальный канал (проверено живым запросом:
`pravo.gov.ru` недоступен с этой машины — таймаут соединения, а
`kremlin.ru/acts/bank` отвечает 200).

Что делает инструмент:

  1. берёт записи списка источников с ``"unavailable": true``;
  2. берёт для каждой номер документа в банке Президента из поля
     ``kremlin_bank`` (его добавляют в `config/legal_sources.json`);
  3. скачивает страницу и вытаскивает из неё текст акта;
  4. кладёт оригинал в ``original/``, текст — в ``text/`` (индексатор
     подхватывает файлы из ``text/`` как «ручные» документы);
  5. записывает метаданные в базу правовой базы: у документа остаётся ТОТ
     ЖЕ идентификатор ``cfg:<id>``, что и в списке источников, — поэтому
     связь с реестром не теряется, а `text_chars` перестаёт быть нулём.

Запуск:

    python tools/download_legal_pre2011.py --list
    python tools/download_legal_pre2011.py --all
    python tools/download_legal_pre2011.py --only nk_rf_p1 koap_rf

После скачивания — пересобрать индекс:

    python tools/update_legal.py --index
"""

import argparse
import html as html_module
import logging
import os
import re
import sys
import time
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import requests  # noqa: E402

from core.legal_parser import (  # noqa: E402
    LegalDocument, LegalParser, extract_text, file_checksum, save_text, slugify,
)

logger = logging.getLogger("tools.download_legal_pre2011")

#: Банк документов Президента России: страница акта.
KREMLIN_BANK = "http://www.kremlin.ru/acts/bank/{number}"

#: Печатная версия страницы: тот же текст, без навигации по редакциям.
KREMLIN_PRINT = "http://www.kremlin.ru/acts/bank/{number}/print"

#: Пауза между обращениями: банк не любит частые запросы.
PAUSE_SECONDS = 1.5

#: Сколько ждать ответа (страницы актов крупные — до 10 МБ).
TIMEOUT = 90


def fetch_page(url: str, session: Optional[requests.Session] = None) -> str:
    """
    Скачивает страницу банка Президента и возвращает HTML.

    Заголовок ``User-Agent`` — латиницей: кириллица в заголовке ломает
    запрос (`UnicodeEncodeError: 'latin-1' codec`), это проверено живым
    обращением.
    """
    getter = (session or requests).get
    response = getter(
        url,
        timeout=TIMEOUT,
        headers={"User-Agent": "logistics_contractor-legal-parser/1.0 "
                               "(open data kremlin.ru)"},
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def html_to_act_text(page: str) -> str:
    """
    Вытаскивает текст акта из страницы банка Президента.

    Разметка страницы простая (без классов и идентификаторов), поэтому текст
    собирается из абзацев ``<p>``: скрипты и стили выбрасываются, теги
    заменяются пробелами, сущности разворачиваются, пустые строки
    схлопываются. Первая строка — название акта, дальше идёт текст.
    """
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    lines: List[str] = []
    for fragment in re.split(r"</(?:p|div|h\d|li|tr)>", body, flags=re.I):
        text = re.sub(r"<br\s*/?>", " ", fragment, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_module.unescape(text)
        text = re.sub(r"[ \t\u00a0]+", " ", text).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def kremlin_number(item: Dict) -> str:
    """Номер документа в банке Президента из записи списка источников."""
    return str(item.get("kremlin_bank") or "").strip()


def documents_to_fetch(parser: LegalParser,
                       only: Optional[List[str]] = None) -> List[Dict]:
    """
    Записи списка источников, которые нужно скачать с банка Президента.

    Берутся только те, у кого стоит ``unavailable`` И заполнен
    ``kremlin_bank``: без номера в банке скачивать нечего.
    """
    result: List[Dict] = []
    for category, items in (parser.sources.get("categories") or {}).items():
        for item in items or []:
            if not item.get("unavailable"):
                continue
            if not kremlin_number(item):
                continue
            if only and item.get("id") not in only:
                continue
            result.append({**item, "category": category})
    return result


def fetch_document(parser: LegalParser, item: Dict,
                   overwrite: bool = False) -> Dict:
    """
    Скачивает один акт: оригинал + текст + метаданные.

    :return: словарь-отчёт «id, short, status, text_chars, detail».
    """
    document: LegalDocument = parser.resolve_item(item, item.get("category", ""))
    if document is None:
        return {"id": item.get("id"), "short": item.get("short", ""),
                "status": "ошибка", "text_chars": 0,
                "detail": "запись не разобрана"}

    slug = slugify(document)
    original_path = os.path.join(parser.original_dir, f"{slug}.html")
    text_path = os.path.join(parser.text_dir, f"{slug}.txt")

    if os.path.exists(text_path) and not overwrite:
        text_chars = os.path.getsize(text_path)
        return {"id": document.id, "short": document.short_title,
                "status": "уже есть", "text_chars": document.text_chars or text_chars,
                "detail": text_path}

    number = kremlin_number(item)
    url = KREMLIN_PRINT.format(number=number)
    try:
        page = fetch_page(url)
    except Exception as e:  # noqa: BLE001 — сеть не должна ронять инструмент
        # Печатная версия может быть недоступна — пробуем обычную страницу.
        url = KREMLIN_BANK.format(number=number)
        try:
            page = fetch_page(url)
        except Exception as second:  # noqa: BLE001
            return {"id": document.id, "short": document.short_title,
                    "status": "ошибка", "text_chars": 0,
                    "detail": f"{type(second).__name__}: {second}"}

    text = html_to_act_text(page)
    if len(text) < int(parser.storage.get("text_min_chars", 200)):
        return {"id": document.id, "short": document.short_title,
                "status": "ошибка", "text_chars": len(text),
                "detail": f"текст слишком короткий ({len(text)} символов)"}

    with open(original_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(page)
    text_chars = save_text(text, text_path)

    document.source = "manual"
    document.url = url
    document.original_path = original_path
    document.text_path = text_path
    document.text_chars = text_chars
    document.ocr_used = False
    document.checksum = file_checksum(original_path)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    document.downloaded_at = now
    document.updated_at = now
    document.extra = {
        **{k: v for k, v in document.extra.items() if k != "last_error"},
        "kremlin_bank": number,
        "kremlin_url": url,
        "manual_note": "Скачано с официального банка документов kremlin.ru: "
                       "на портале опубликования (с 2011 года) акта нет.",
    }
    parser._save(document)

    logger.info("Скачан акт %s: %s символов текста", document.short_title, text_chars)
    return {"id": document.id, "short": document.short_title, "status": "скачан",
            "text_chars": text_chars, "detail": text_path}


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа инструмента."""
    parser_args = argparse.ArgumentParser(
        description="Скачивание актов до 2011 года с банка kremlin.ru")
    parser_args.add_argument("--all", action="store_true",
                             help="скачать все акты с заполненным kremlin_bank")
    parser_args.add_argument("--only", nargs="+", metavar="ID",
                             help="только эти идентификаторы из списка источников")
    parser_args.add_argument("--list", action="store_true",
                             help="показать, что будет скачано, и выйти")
    parser_args.add_argument("--overwrite", action="store_true",
                             help="перекачать уже скачанные")
    args = parser_args.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = LegalParser()
    items = documents_to_fetch(parser, args.only)

    if not items:
        print("Нечего скачивать: у записей до 2011 года не заполнено поле "
              "kremlin_bank в config/legal_sources.json.")
        print("Добавьте номер документа в банке: "
              "https://www.kremlin.ru/acts/bank/<номер>")
        return 0

    if args.list:
        for item in items:
            print(f"{item.get('id'):16} банк №{kremlin_number(item):<10} "
                  f"{item.get('short', '')}")
        return 0

    if not args.all and not args.only:
        print("Укажите --all или --only <ID> (см. --list).")
        return 2

    print(f"Актов к скачиванию: {len(items)}")
    reports = []
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] {item.get('short', '')} …", flush=True)
        reports.append(fetch_document(parser, item, overwrite=args.overwrite))
        if index < len(items):
            time.sleep(PAUSE_SECONDS)

    print("\nИтог:")
    for report in reports:
        print(f"  {report['status']:<9} {report['short']:<32} "
              f"{report['text_chars']:>8} симв. {report['detail']}")

    done = sum(1 for report in reports if report["status"] in ("скачан", "уже есть"))
    print(f"\nГотово: {done} из {len(reports)}.")
    print("Дальше пересоберите индекс: python tools/update_legal.py --index")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
