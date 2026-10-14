#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Мониторинг обновлений правовой базы pravo.gov.ru.

    python tools/update_legal.py                 # проверить обновления за 30 дней
    python tools/update_legal.py --days 7
    python tools/update_legal.py --days 90 --download
    python tools/update_legal.py --check-only    # только показать, что нового
    python tools/update_legal.py --watch "путевой лист" --days 365
    python tools/update_legal.py --status

Зачем отдельный инструмент
--------------------------
`tools/download_legal.py` собирает базу по списку из
`config/legal_sources.json` — это разовая (или редкая) операция.
Этот инструмент отвечает на другой вопрос: «что появилось на портале
за последние N дней?» Он обходит ленту опубликования по ключевым словам
списка источников, показывает новые акты и (с `--download`) складывает их
в ту же правовую базу. Повторный запуск ничего не скачивает второй раз:
файл с тем же именем уже лежит в `data/knowledge/legal/original`.

Сеть: только официальные адреса портала. Логи без персональных данных.
"""

import argparse
import logging
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.logging_config import setup_logging  # noqa: E402
from core.legal_parser import LegalParser, human_size  # noqa: E402
from db.crud import legal_metadata  # noqa: E402

logger = logging.getLogger("tools.update_legal")


def show_documents(documents, title: str) -> None:
    """Печатает список документов в виде таблицы."""
    print(f"\n{title}: {len(documents)}")
    for document in documents:
        number = document.number or "б/н"
        print(f"  {document.publish_date or document.doc_date or '—':10s} | {number[:14]:14s} | {document.title[:78]}")


def main(argv=None) -> int:
    """Точка входа инструмента. Возвращает код выхода."""
    arg_parser = argparse.ArgumentParser(
        description="Проверка обновлений правовой базы pravo.gov.ru (открытые данные).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    arg_parser.add_argument("--days", type=int, default=30, help="глубина проверки в днях (по умолчанию 30)")
    arg_parser.add_argument("--watch", help="своя строка поиска вместо ключевых слов списка источников")
    arg_parser.add_argument("--category", help="категория, к которой отнести найденное")
    arg_parser.add_argument("--doc-type", help="вид документа (Федеральный закон, Постановление, Приказ, …)")
    arg_parser.add_argument("--limit", type=int, default=200, help="предел числа документов на запрос")
    arg_parser.add_argument("--download", action="store_true", help="скачать найденные документы")
    arg_parser.add_argument("--check-only", action="store_true", help="только показать список (по умолчанию)")
    arg_parser.add_argument("--status", action="store_true", help="показать состояние базы и выйти")
    args = arg_parser.parse_args(argv)

    setup_logging()
    parser = LegalParser()
    started = time.time()

    if args.status:
        data = parser.stats()
        print("Правовая база pravo.gov.ru")
        print(f"  документов:        {data['documents']}")
        print(f"  скачано файлов:    {data['downloaded']}")
        print(f"  символов текста:   {data['text_chars']:,}".replace(",", " "))
        print(f"  оригиналы:         {human_size(data['original_bytes'])}")
        print(f"  последняя проверка:{data['last_check'] or ' —'}")
        print(f"  последнее скачивание:{data['last_download'] or ' —'}")
        return 0

    queries = [args.watch] if args.watch else (parser.keywords() or [""])
    found = {}
    print(f"Проверяю опубликованное за последние {args.days} дн. по {len(queries)} запросам…")
    for word in queries:
        try:
            documents = parser.list_updates(
                _since_date(args.days), query=word or None,
                doc_type=args.doc_type, limit=args.limit,
            )
        except Exception as e:  # noqa: BLE001 — один запрос не должен рушить проверку
            print(f"  запрос «{word}»: ошибка {type(e).__name__}: {e}")
            continue
        for document in documents:
            found.setdefault(document.id, document)
        print(f"  «{word or 'вся лента'}»: {len(documents)}")

    documents = sorted(found.values(), key=lambda item: item.publish_date, reverse=True)
    show_documents(documents, "Новые документы")

    downloaded = 0
    unavailable = 0
    errors = 0
    if args.download:
        print("\nСкачивание…")
        for document in documents:
            if args.category:
                document.category = args.category
            parser._save(document)
            if parser._existing_file(document):
                continue
            try:
                result = parser.download_document(document)
            except Exception as e:  # noqa: BLE001
                print(f"  ОШИБКА {document.display_title[:50]}: {e}")
                errors += 1
                continue
            if result.ok:
                downloaded += 1
                mark = " (OCR)" if result.ocr_used else ""
                print(f"  OK {document.display_title[:50]}{mark}: {result.text_chars} символов")
            elif result.unavailable:
                unavailable += 1
            else:
                errors += 1
                print(f"  ОШИБКА {document.display_title[:50]}: {result.error}")

    legal_metadata.set_meta("last_update_check", time.strftime("%Y-%m-%d %H:%M:%S"),
                            conn=parser._connect())
    print()
    print(f"Найдено: {len(documents)} | скачано: {downloaded} | "
          f"без источника: {unavailable} | ошибок: {errors} | время: {time.time() - started:.1f} с")
    if not args.download:
        print("Файлы не скачивались: добавьте --download.")
    return 1 if errors else 0


def _since_date(days: int) -> str:
    """
    Дата «N дней назад» в ISO.

    :param days: глубина в днях;
    :return: дата в формате ГГГГ-ММ-ДД.
    """
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - max(1, int(days)) * 86400))


if __name__ == "__main__":
    sys.exit(main())
