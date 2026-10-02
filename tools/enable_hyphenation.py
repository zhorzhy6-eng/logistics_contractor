#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Включает автоматическую расстановку переносов во всех шаблонах договора.

Зачем
-----
Абзацы договора выровнены по ширине (w:jc="both"). Когда длинное слово
(«"ТЕХНОЛОГИСТИКА"», «ответственностью») не помещается в строку, Word
переносит его целиком, а предыдущую строку растягивает пробелами:

    Общество        с        ограниченной        ответственностью
    "ТЕХНОЛОГИСТИКА" (ООО ...), именуемое в дальнейшем «Заказчик» ...

Автопереносы позволяют Word разбить слово по слогам, и растягивать
пробелы ему уже не нужно — выравнивание по ширине сохраняется.

Что добавляется в word/settings.xml (порядок по схеме CT_Settings,
между w:defaultTabStop и w:characterSpacingControl):

    <w:autoHyphenation w:val="true"/>
    <w:consecutiveHyphenLimit w:val="2"/>   не больше двух переносов подряд
    <w:hyphenationZone w:val="360"/>        зона переноса 0.25" (значение Word)

Запуск:
    python tools/enable_hyphenation.py --check   # показать состояние
    python tools/enable_hyphenation.py           # включить во всех шаблонах
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

TEMPLATES = (
    "shablon_ooo.docx",
    "shablon_ip_with_vat.docx",
    "shablon_ip_without_vat.docx",
)

#: Настройки переносов: тег -> значение w:val.
HYPHENATION_SETTINGS = (
    ("w:autoHyphenation", "true"),
    ("w:consecutiveHyphenLimit", "2"),
    ("w:hyphenationZone", "360"),
)

#: Элементы, которые по схеме идут ПОСЛЕ настроек переносов: перед первым
#: из них и вставляем, чтобы не нарушить порядок CT_Settings.
FOLLOWING_TAGS = (
    "w:doNotHyphenateCaps",
    "w:showEnvelope",
    "w:summaryLength",
    "w:clickAndTypeStyle",
    "w:defaultTableStyle",
    "w:characterSpacingControl",
    "w:compat",
    "w:docVars",
    "w:rsids",
    "w:themeFontLang",
    "w:clrSchemeMapping",
)


def hyphenation_state(doc: Document) -> dict:
    """Текущие значения настроек переносов в документе."""
    settings = doc.settings.element
    state = {}
    for tag, _value in HYPHENATION_SETTINGS:
        node = settings.find(qn(tag))
        state[tag] = node.get(qn("w:val")) if node is not None else None
    return state


def enable_hyphenation(doc: Document) -> list:
    """
    Включает автопереносы в документе.

    Возвращает список изменений (пустой, если уже было включено).
    """
    settings = doc.settings.element
    changes = []

    index = len(settings)
    for position, child in enumerate(settings):
        if child.tag in {qn(tag) for tag in FOLLOWING_TAGS}:
            index = position
            break

    for tag, value in HYPHENATION_SETTINGS:
        node = settings.find(qn(tag))
        if node is None:
            node = OxmlElement(tag)
            settings.insert(index, node)
            index += 1
            changes.append(f"{tag}={value} (добавлено)")
            node.set(qn("w:val"), value)
            continue
        if node.get(qn("w:val")) != value:
            node.set(qn("w:val"), value)
            changes.append(f"{tag}={value} (обновлено)")

    return changes


def process(path: Path, apply: bool) -> str:
    doc = Document(str(path))

    if not apply:
        state = hyphenation_state(doc)
        parts = ", ".join(
            f"{tag.split(':')[1]}={value or 'нет'}" for tag, value in state.items()
        )
        return parts

    changes = enable_hyphenation(doc)
    if not changes:
        return "уже включено — пропуск"

    doc.save(str(path))
    return "; ".join(changes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="только показать текущие настройки")
    parser.add_argument("--templates", default=None,
                        help="папка с шаблонами (по умолчанию templates/)")
    parser.add_argument("--no-backup", action="store_true",
                        help="не делать резервную копию шаблонов")
    args = parser.parse_args()

    templates_dir = Path(args.templates) if args.templates else PROJECT_ROOT / "templates"

    if not args.check and not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = PROJECT_ROOT / "backup" / f"templates_before_hyphenation_{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for name in TEMPLATES:
            source = templates_dir / name
            if source.exists():
                shutil.copy2(source, backup_dir / name)
        print(f"Резервная копия: {backup_dir}\n")

    for name in TEMPLATES:
        path = templates_dir / name
        if not path.exists():
            print(f"{name}: НЕ НАЙДЕН")
            continue
        print(f"{name}: {process(path, apply=not args.check)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
