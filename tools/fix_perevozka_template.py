#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Правит бланки перевозки: пробел перед плейсхолдером срока оплаты (FIX-2.5).

Что чиним
---------
П. 4.4 бланка печатался как «Оплата производится в течение{{payment_days}}
({{payment_days_words}}) банковских дней…» — без пробела между словом
«течение» и числом. В готовом договоре это выглядело как «в течение20
(двадцати) банковских дней».

Отдельного сборщика бланков перевозки в проекте нет: они пришли от
заказчика и правятся точечными скриптами (`enable_hyphenation.py`,
`rebuild_executor_table.py`). Этот скрипт — из того же ряда.

Как правим
----------
У run, текст которого заканчивается на «Оплата производится в течение»,
дописывается пробел и ставится `xml:space="preserve"`. Второе обязательно:
без него Word схлопывает пробелы по краям `<w:t>`, и пробел исчезает
снова. Больше в XML ничего не меняется.

Почему не python-docx
---------------------
`Document.save()` переписывает все части пакета: изменяются встроенные
шрифты (`word/fonts/`), идентификаторы и порядок элементов. Здесь же
переписывается РОВНО одна запись архива — `word/document.xml`, остальные
копируются побайтово (`ZipInfo` сохраняется как есть). Для бланков
перевозки это важно: в них встроены шрифты, а SHA256 фиксируется тестами.

Скрипт идемпотентен: если пробел уже сохраняется (`xml:space` стоит или
текст заканчивается на пробел с `xml:space`), файл не меняется.

Запуск:
    python tools/fix_perevozka_template.py --check   # только показать план
    python tools/fix_perevozka_template.py           # применить
    python tools/fix_perevozka_template.py --templates <папка>
"""

import argparse
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Бланки перевозки: выбор шаблона идёт по типу перевозчика.
TEMPLATES = (
    "shablon_ooo.docx",
    "shablon_ip_with_vat.docx",
    "shablon_ip_without_vat.docx",
)

#: Часть пакета, в которой живёт п. 4.4.
DOCUMENT_PART = "word/document.xml"

#: Хвост текста run перед плейсхолдером срока оплаты. Именно этого пробела
#: не хватает в бланке: без него в договоре печатается «в течение20».
TAIL = "Оплата производится в течение"

#: Атрибут, без которого Word схлопывает пробелы по краям `<w:t>`.
PRESERVE = 'xml:space="preserve"'


def _tail_run_pattern() -> "re.Pattern[str]":
    """
    `<w:t ...>…Оплата производится в течение</w:t>` — точное совпадение.

    Захватывает и вариант с уже дописанным пробелом, и вариант без него:
    скрипт должен уметь и починить бланк, и распознать уже исправленный.
    """
    return re.compile(
        r"<w:t(?P<attrs>[^>]*)>(?P<text>" + re.escape(TAIL) + r" ?)</w:t>"
    )


def fix_document_xml(xml: str) -> tuple:
    """
    Правит XML документа: пробел в конце текста + xml:space="preserve".

    Возвращает (новый_xml, число_правок). Правка ровно одна на документ.
    Если хвост не найден — 0; если и пробел, и атрибут уже на месте — 0
    (скрипт идемпотентен).
    """
    match = _tail_run_pattern().search(xml)
    if match is None:
        return xml, 0

    attrs = match.group("attrs")
    text = match.group("text")

    needs_space = not text.endswith(" ")
    needs_preserve = PRESERVE not in attrs
    if not needs_space and not needs_preserve:
        return xml, 0

    new_text = text + " " if needs_space else text
    if needs_preserve:
        new_open = f"<w:t{attrs} {PRESERVE}>"
    else:
        new_open = f"<w:t{attrs}>"

    replacement = new_open + new_text + "</w:t>"
    return xml[:match.start()] + replacement + xml[match.end():], 1


def _rewrite_document_part(path: Path, new_xml: bytes) -> None:
    """
    Перезаписывает `word/document.xml`, не трогая остальные части пакета.

    ZipInfo каждой записи переносится как есть (дата, атрибуты, способ
    сжатия), поэтому встроенные шрифты и связи остаются побайтово теми же.
    """
    with zipfile.ZipFile(str(path)) as source:
        items = [(info, source.read(info.filename)) for info in source.infolist()]

    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as target:
        for info, data in items:
            if info.filename == DOCUMENT_PART:
                data = new_xml
            target.writestr(info, data)


def fix_template(path: Path, apply: bool) -> str:
    """Правит один бланк. Возвращает отчёт одной строкой."""
    if not path.exists():
        return "НЕ НАЙДЕН"

    with zipfile.ZipFile(str(path)) as archive:
        xml = archive.read(DOCUMENT_PART).decode("utf-8")

    fixed, changes = fix_document_xml(xml)

    if not changes:
        return "уже с пробелом — пропуск"

    if not apply:
        return 'будет добавлен пробел и xml:space="preserve" у run «…в течение»'

    _rewrite_document_part(path, fixed.encode("utf-8"))
    return 'добавлен пробел и xml:space="preserve" у run «…в течение»'


def check_state(path: Path) -> str:
    """Состояние бланка: есть ли пробел и сохраняется ли он."""
    if not path.exists():
        return "НЕ НАЙДЕН"

    with zipfile.ZipFile(str(path)) as archive:
        xml = archive.read(DOCUMENT_PART).decode("utf-8")

    runs = _tail_run_pattern().findall(xml)
    if not runs:
        return "хвост «…в течение» не найден"

    with_space = sum(1 for _attrs, text in runs if text.endswith(" "))
    preserved = sum(1 for attrs, _text in runs if PRESERVE in attrs)

    if with_space == len(runs) and preserved == len(runs):
        return f"пробел сохраняется (run: {len(runs)})"
    return (
        f"пробел СХЛОПЫВАЕТСЯ (run: {len(runs)}, "
        f"с пробелом: {with_space}, с xml:space: {preserved})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="только показать состояние бланков")
    parser.add_argument("--templates", default=None,
                        help="папка с шаблонами (по умолчанию templates/)")
    parser.add_argument("--no-backup", action="store_true",
                        help="не делать резервную копию шаблонов")
    args = parser.parse_args()

    templates_dir = Path(args.templates) if args.templates else PROJECT_ROOT / "templates"

    if args.check:
        for name in TEMPLATES:
            print(f"{name}: {check_state(templates_dir / name)}")
        return 0

    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = PROJECT_ROOT / "backup" / f"templates_before_payment_space_{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for name in TEMPLATES:
            source = templates_dir / name
            if source.exists():
                shutil.copy2(source, backup_dir / name)
        print(f"Резервная копия: {backup_dir}\n")

    for name in TEMPLATES:
        print(f"{name}: {fix_template(templates_dir / name, apply=True)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
