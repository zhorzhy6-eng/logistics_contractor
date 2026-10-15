#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скачивание правовой базы с pravo.gov.ru.

    python tools/download_legal.py --all
    python tools/download_legal.py --category government_decrees
    python tools/download_legal.py --only-required
    python tools/download_legal.py --search "путевой лист" --limit 5
    python tools/download_legal.py --stats
    python tools/download_legal.py --all --no-ocr        # только файлы, без распознавания
    python tools/download_legal.py --all --force         # перекачать уже скачанное
    python tools/download_legal.py --url <eo>            # один документ по номеру опубликования

Что делает
----------
Читает config/legal_sources.json и складывает документы в
``data/knowledge/legal/original`` (оригиналы) и ``.../text`` (извлечённый
текст), а метаданные — в ``db/legal_metadata.db``.

Про OCR
-------
PDF портала опубликования — сканы без текстового слоя. По умолчанию текст
распознаётся локальным Tesseract (``rus+eng``), примерно 1,5 с на страницу.
Ключ ``--no-ocr`` оставляет только скачанные файлы: полезно, когда Tesseract
не установлен или нужна быстрая проверка сети.

Сеть: только официальные адреса портала (publication.pravo.gov.ru,
pravo.gov.ru). Логи без персональных данных.
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

logger = logging.getLogger("tools.download_legal")


def _progress(stage: str, done: int, total: int) -> None:
    """Печатает ход работы одной строкой (без перерисовки экрана)."""
    names = {
        "prepare": "подготовка",
        "resolve": "поиск документов",
        "download": "скачивание",
    }
    label = names.get(stage, stage)
    if total:
        print(f"\r{label}: {done}/{total}", end="", flush=True)


def print_stats(parser: LegalParser) -> None:
    """Печатает сводку по правовой базе."""
    data = parser.stats()
    print("Правовая база pravo.gov.ru")
    print(f"  документов:          {data['documents']}")
    print(f"  скачано файлов:      {data['downloaded']}")
    print(f"  символов текста:     {data['text_chars']:,}".replace(",", " "))
    print(f"  оригиналы на диске:  {human_size(data['original_bytes'])}")
    print(f"  текст на диске:      {human_size(data['text_bytes'])}")
    print(f"  база метаданных:     {data['db_path']}")
    print(f"  каталог:             {data['data_dir']}")
    print(f"  последнее обновление:{data['last_update'] or ' —'}")
    print(f"  последняя проверка:  {data['last_check'] or ' —'}")
    if data["by_category"]:
        print("  по категориям:")
        for name, count in data["by_category"].items():
            print(f"    {name}: {count}")


def main(argv=None) -> int:
    """Точка входа инструмента. Возвращает код выхода."""
    arg_parser = argparse.ArgumentParser(
        description="Скачивание правовых документов с pravo.gov.ru (открытые данные).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    arg_parser.add_argument("--all", action="store_true", help="скачать все категории списка источников")
    arg_parser.add_argument("--category", help="скачать одну категорию (codes, federal_laws, …)")
    arg_parser.add_argument("--list-categories", action="store_true", help="показать категории и выйти")
    arg_parser.add_argument("--only-required", action="store_true", help="только обязательные документы")
    arg_parser.add_argument("--search", help="поиск по порталу и скачивание первых найденных")
    arg_parser.add_argument("--doc-type", help="вид документа для --search (Федеральный закон, …)")
    arg_parser.add_argument("--url", help="номер официального опубликования (eoNumber) одного документа")
    arg_parser.add_argument("--limit", type=int, default=0, help="предел числа документов")
    arg_parser.add_argument("--stats", action="store_true", help="показать сводку и выйти")
    arg_parser.add_argument("--no-ocr", action="store_true", help="не распознавать сканы (только файлы)")
    arg_parser.add_argument("--force", action="store_true",
                            help="скачивать заново даже то, что уже есть в базе")
    args = arg_parser.parse_args(argv)

    setup_logging()
    parser = LegalParser(ocr=False if args.no_ocr else None)

    if args.stats:
        print_stats(parser)
        return 0

    if args.list_categories:
        for name, count in parser.sources_categories().items():
            print(f"{name}: {count}")
        return 0

    if not any((args.all, args.category, args.search, args.url)):
        arg_parser.print_help()
        return 2

    started = time.time()
    results = []

    if args.url:
        # Один документ по номеру официального опубликования: метаданные
        # берём с портала, чтобы в базе было осмысленное название.
        from core.legal_parser import LegalDocument  # локальный импорт: нужен только здесь

        document = None
        for candidate in parser.search("", limit=50, max_pages=5):  # пустой запрос — последние документы
            if candidate.eo_number == args.url:
                document = candidate
                break
        if document is None:
            document = LegalDocument(
                id=f"eo:{args.url}",
                title=f"Документ {args.url}",
                source="publication",
                eo_number=args.url,
                file_url=parser.publication.pdf_url(args.url),
                url=parser.publication.document_url(args.url),
                category="manual",
            )
        document.category = document.category or "manual"
        parser._save(document)
        results = [parser.download_document(document)]

    elif args.search:
        found = parser.search(args.search, doc_type=args.doc_type, limit=args.limit or 10)
        print(f"Поиск «{args.search}»: найдено {len(found)}")
        for document in found:
            parser._save(document)
            print(f"  {document.publish_date} | {document.number or '—'} | {document.title[:70]}")
            results.append(parser.download_document(document))

    else:
        results = parser.download_all(
            category=args.category,
            only_required=args.only_required,
            skip_existing=not args.force,
            limit=args.limit or None,
        )

    print()
    ok = [item for item in results if item.ok]
    skipped = [item for item in results if item.skipped]
    unavailable = [item for item in results if item.unavailable]
    failed = [item for item in results if item.failed]
    ocr_pages_used = sum(1 for item in ok if item.ocr_used)

    for item in failed:
        print(f"  ОШИБКА {item.document.display_title[:60]}: {item.error}")
    for item in unavailable:
        print(f"  БЕЗ ИСТОЧНИКА {item.document.display_title[:44]}: акт опубликован до 2011 г.")
    for item in ok:
        mark = " (распознан OCR)" if item.ocr_used else ""
        print(f"  OK {item.document.display_title[:52]}{mark}: {item.text_chars} символов")
    for item in skipped:
        print(f"  ЕСТЬ {item.document.display_title[:52]}: {item.text_chars} символов (повторно не качался)")

    print()
    print(f"Скачано: {len(ok)} | уже было: {len(skipped)} | "
          f"без источника на портале: {len(unavailable)} | ошибок: {len(failed)}")
    print(f"С OCR: {ocr_pages_used} | время: {time.time() - started:.1f} с")
    print_stats(parser)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
