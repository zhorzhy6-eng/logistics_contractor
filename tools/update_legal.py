#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Мониторинг обновлений правовой базы pravo.gov.ru.

    python tools/update_legal.py                 # что нового за 30 дней
    python tools/update_legal.py --days 7
    python tools/update_legal.py --days 90 --download
    python tools/update_legal.py --watch "путевой лист" --days 365
    python tools/update_legal.py --watch "тахограф" --pages 10
    python tools/update_legal.py --status

Зачем отдельный инструмент
--------------------------
`tools/download_legal.py` собирает базу по списку из
`config/legal_sources.json` — это разовая (или редкая) операция.
Этот инструмент отвечает на другой вопрос: «что появилось на портале
за последние N дней?»

Отбор идёт **по ведомствам**, а не по ключевым словам: в
`config/legal_sources.json` есть блок `monitor.authorities` (Минтранс,
Правительство, Госдума, Ространснадзор), и лента сужается до их актов.
По словам мониторинг находил ноль: тема в названии акта почти никогда не
совпадает с ключевым словом — почти все изменения называются «О внесении
изменений в…». Свой поиск задаётся ключом `--watch`.

Проверка — это ЧТЕНИЕ: без `--download` найденное только показывается, в
реестр правовой базы ничего не записывается. С `--download` новые акты
скачиваются и попадают в базу; повторный запуск уже скачанное пропускает
(файл с тем же именем лежит в `data/knowledge/legal/original`).

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
    arg_parser.add_argument("--limit", type=int, default=200, help="предел числа документов в отчёте")
    arg_parser.add_argument("--pages", type=int, default=40,
                            help="сколько страниц ленты просматривать (100 актов на страницу)")
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

    if args.watch:
        print(f"Проверяю опубликованное за последние {args.days} дн. по запросу «{args.watch}»…")
    else:
        print(f"Проверяю ленту опубликования за последние {args.days} дн. "
              f"по {len(parser.keywords())} ключевым словам списка источников…")

    summary = parser.update(
        days=args.days, query=args.watch, limit=args.limit,
        categories=[args.category] if args.category else None,
        max_pages=args.pages, download=args.download,
    )
    documents = summary["found_documents"]
    show_documents(documents, "Новые документы")

    downloaded = summary["downloaded"]
    unavailable = summary["unavailable"]
    errors = summary["errors"]
    if args.download:
        print("\nСкачано:")
        for document in summary["documents"]:
            print(f"  OK {document.display_title[:60]}: {document.text_chars} символов")
    print()
    print(f"Найдено: {len(documents)} | скачано: {downloaded} | "
          f"без источника: {unavailable} | ошибок: {errors} | время: {time.time() - started:.1f} с")
    if not args.download:
        print("Найденное только показано: в правовую базу оно не записано. "
              "Добавьте --download, чтобы скачать и сохранить.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())