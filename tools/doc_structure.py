#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Разбор СТРУКТУРЫ реальных документов (ЧАСТЬ 2A стресс-теста). Только локально.

    python tools/doc_structure.py

Читает папку оператора с реальными документами, определяет вид каждого
документа и собирает АГРЕГАТ: сколько документов какого вида, какие форматы,
какие размеры и пропорции, какие блоки полей есть в текстовом слое.

ЧЕГО ЗДЕСЬ НЕТ И БЫТЬ НЕ МОЖЕТ
------------------------------
Ни одного значения из документа: ни ФИО, ни серий и номеров, ни адресов,
ни названий организаций, ни кодов подразделения. В отчёт идут только:

  * числа (сколько, какого размера, сколько страниц);
  * виды документов и имена ПОЛЕЙ («фамилия», «дата выдачи»);
  * агрегированная геометрия — пропорции и порядок блоков, без координат
    конкретного скана.

Пути и имена файлов не печатаются вовсе: в папках оператора они содержат
фамилии («Иванов/паспорт.jpg»). В отчёте они заменены номером вида
«<файл:050>». Правило — AGENTS.md § 4: ПДн не покидают машину, и даже в
локальный отчёт они не попадают.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.import_cancel import ImportCancelled  # noqa: E402

#: Папка оператора. Имя на диске — с опечаткой («распознования»), и это
#: НЕ ошибка: так называется папка. Маска взята из .gitignore — одной
#: ошибки в букве достаточно, чтобы реальные ПДн уехали в репозиторий.
DEFAULT_SOURCE = PROJECT_ROOT / "Документы для распознования"
DEFAULT_REPORT = PROJECT_ROOT / "tests" / "_tmp" / "doc_structure.md"
DEFAULT_JSON = PROJECT_ROOT / "tests" / "_tmp" / "doc_structure.json"

#: Виды документов, которые различает приём: по ним считаются метрики.
DOC_KINDS = (
    "passport", "license", "sts", "pts", "bank_requisites", "inn_ogrn",
    "egrul", "registration_certificate", "rostransnadzor", "counterparty_card",
    "vehicle_card", "other_image", "other_text", "unreadable",
)

# Байты-маркеры форматов: по ним формат определяется даже без расширения.
_MAGIC = (
    (b"%PDF", "pdf"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"PK\x03\x04", "zip"),
    (b"{\\rtf", "rtf"),
    (b"\xd0\xcf\x11\xe0", "ole"),
)

_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря")

#: Сколько текста считать «есть текстовый слой».
MIN_TEXT_ALNUM = 40


def sniff_format(path: Path) -> str:
    """Формат файла по содержимому, а не по расширению."""
    try:
        head = path.open("rb").read(16)
    except OSError:
        return "unreadable"
    for magic, name in _MAGIC:
        if head.startswith(magic):
            return name
    return path.suffix.lower().lstrip(".") or "unknown"


def classify_text(text: str) -> str:
    """
    Вид документа по текстовому слою.

    Порядок проверок повторяет core.document_fields.extract_document_fields:
    вид определяется теми же признаками, что и при разборе, — иначе карта
    структуры описывала бы не то, что видит приём.
    """
    if re.search(r"карта\s+контрагента", text, re.I):
        return "counterparty_card"
    if (re.search(r"постановке\s+на\s+учет\s+российской\s+организации|"
                  r"поставлена\s+на\s+учет", text, re.I)
            and re.search(r"ИНН\s*/?\s*КПП", text)):
        return "registration_certificate"
    if re.search(r"выписка\s+из\s+реестра\s+уведомлений|"
                 r"реестр[а-я]*\s+уведомлений\s+о\s+транспортно", text, re.I):
        return "rostransnadzor"
    if (re.search(r"единого\s+государственного\s+реестра\s+юридических\s+лиц",
                  text, re.I)
            or re.search(r"лист\s+записи\s+егрюл", text, re.I)):
        return "egrul"
    if re.search(r"паспорт\s+выдан|PNRUS|фамилия[\s\S]{0,150}отчество",
                 text, re.I):
        return "passport"
    if re.search(r"\bМВД\b", text) and re.search(r"\b[0-9]{3}[-–][0-9]{3}\b", text):
        return "passport"
    if re.search(r"водительское\s+удостоверение|driving\s+licen[cs]e", text,
                 re.I):
        return "license"
    if re.search(r"свидетельство[\s\S]{0,35}регистрации|vehicle\s+registration|"
                 r"регистрационн[а-я]+\s+номер|собственник\s*\(владелец\)", text,
                 re.I):
        return "sts"
    if re.search(r"паспорт\s+транспортного\s+средства|\bПТС\b", text, re.I):
        return "pts"
    if re.search(r"банк\s+получателя|номер\s+сч[её]та\s+получателя", text, re.I):
        return "bank_requisites"
    if re.search(r"ИНН\s*/?\s*КПП|ОГРН|ОГРНИП", text, re.I):
        return "inn_ogrn"
    if re.search(r"\bсобственник\b|\bвладелец\b|\bowner\b", text, re.I):
        return "vehicle_card"
    if re.search(r"собственник\(владелец\)|особые\s+отметки", text, re.I):
        return "sts"
    return "other_text"


def classify_image(path: Path, image) -> str:
    """
    Вид документа-картинки без текста: по пропорциям сторон.

    Пропорция — единственный признак, который есть у скана без текстового
    слоя и который при этом не является персональными данными:

      * паспорт РФ 125×88 мм  → 1,42;
      * ВУ и банковская карта (ISO/IEC 7810 ID-1) 85,6×54 мм → 1,59;
      * СТС 100×75 мм → 1,33; ПТС 210×148 мм (A5) → 1,42.

    Паспорт и ПТС по пропорции неразличимы — оба попадают в «passport»:
    приём и сам их различает только по тексту, а текста тут нет. В отчёте
    такие файлы помечены как «по пропорции».
    """
    if image is None:
        return "other_image"
    width, height = image.size
    if not width or not height:
        return "other_image"
    ratio = width / height
    if ratio < 1.2:
        return "other_image"          # вертикальный скан или фото
    if 1.35 <= ratio <= 1.48:
        return "passport"             # 1,42: паспорт РФ или ПТС (A5)
    if 1.52 <= ratio <= 1.66:
        return "license"              # 1,59: ВУ или банковская карта
    if 1.25 <= ratio < 1.35:
        return "sts"                  # 1,33: СТС
    if 1.66 < ratio <= 2.2:
        return "other_text"           # широкая полоса: выписка, таблица
    return "other_image"


#: Имена полей, наличие которых проверяется в текстовом слое. Это ЯРЛЫКИ
#: бланка, а не значения: «фамилия», «дата выдачи». Значений в отчёте нет.
FIELD_LABELS = (
    (r"фамилия", "surname"),
    (r"\bимя\b", "given_name"),
    (r"отчество", "patronymic"),
    (r"пол\b", "sex"),
    (r"дата\s+рождения|д\.\s?р\.", "birth_date"),
    (r"место\s+рождения", "birth_place"),
    (r"дата\s+выдачи", "issue_date"),
    (r"кем\s+выдан|паспорт\s+выдан", "issuer"),
    (r"код\s+подразделения", "subdivision_code"),
    (r"^\s*[0-9]{2}\s+[0-9]{2}\s+[0-9]{6}\s*$", "series_number_line"),
    (r"адрес\s+регистрации|место\s+жительства|прописка", "registration_address"),
    (r"категори", "categories"),
    (r"срок\s+до|действительно\s+до", "expiry"),
    (r"стаж", "experience"),
    (r"ИНН", "inn"),
    (r"КПП", "kpp"),
    (r"ОГРН|ОГРНИП", "ogrn"),
    (r"банк\s+получателя|наименование\s+банка", "bank_name"),
    (r"БИК", "bik"),
    (r"р/с|расчетный\s+счет|номер\s+сч[её]та", "account"),
    (r"к/с|корр", "corr_account"),
    (r"VIN", "vin"),
    (r"регистрационный\s+номер|госномер|г/н", "plate"),
    (r"год\s+выпуска", "year"),
    (r"марка|модель", "brand_model"),
    (r"собственник|владелец", "owner"),
    (r"телефон|тел\.", "phone"),
    (r"e-?mail", "email"),
)


def field_presence(text: str) -> List[str]:
    """Какие ЯРЛЫКИ полей встречаются в текстовом слое (значения не берутся)."""
    present = []
    for pattern, name in FIELD_LABELS:
        if re.search(pattern, text, re.I | re.M):
            present.append(name)
    return present


def text_geometry(text: str) -> Dict[str, Any]:
    """
    Геометрия текстового слоя БЕЗ содержимого: строки, их длина, даты.

    Длины и количество строк описывают бланк (сколько строк в шапке, есть ли
    длинные строки адреса), но не раскрывают данные.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    lengths = [len(line.strip()) for line in lines]
    return {
        "lines": len(lines),
        "max_line": max(lengths) if lengths else 0,
        "avg_line": round(sum(lengths) / len(lengths), 1) if lengths else 0.0,
        "dates": len(re.findall(r"\b\d{2}[.\-/]\d{2}[.\-/]\d{4}\b", text)),
        "digit_runs": len(re.findall(r"\b\d{6,}\b", text)),
        "month_words": sum(1 for m in _MONTHS if m in text.lower()),
        "cyrillic": len(re.findall(r"[А-Яа-яЁё]", text)),
        "latin": len(re.findall(r"[A-Za-z]", text)),
    }


def image_geometry(image) -> Dict[str, Any]:
    """Геометрия картинки: размер, пропорция, режим цвета, «чернильность»."""
    if image is None:
        return {}
    width, height = image.size
    gray = image.convert("L")
    histogram = gray.histogram()
    total = max(1, width * height)
    dark = sum(histogram[:100])
    return {
        "width": width,
        "height": height,
        "ratio": round(width / height, 3) if height else 0,
        "megapixels": round(width * height / 1_000_000, 2),
        "mode": image.mode,
        "dark_share": round(dark / total, 4),
    }


def analyze_file(path: Path, index: int, cancel, use_ocr: bool = False) -> Dict[str, Any]:
    """
    Один документ → структурная запись БЕЗ данных.

    Читается тем же core.document_reader, что и в приложении: структура,
    описанная по другому читателю, была бы структурой другого продукта.

    :param use_ocr: распознать картинку локальным Tesseract, чтобы определить
        ВИД документа. Распознанный текст используется ТОЛЬКО для проверки
        ярлыков полей и вида документа и никуда не сохраняется — в отчёт
        уходят имена полей, а не значения (см. docstring модуля).
    """
    from core.document_reader import read_document

    record: Dict[str, Any] = {
        "index": index,
        "format": sniff_format(path),
        "pages": 0,
        "kind": "unreadable",
        "kind_source": "format",
        "has_text": False,
        "fields": [],
        "geometry": {},
        "error": "",
    }

    try:
        pages = list(read_document(path, cancel, ""))
    except ImportCancelled:
        raise
    except Exception as exc:                                # noqa: BLE001
        record["error"] = type(exc).__name__
        return record

    record["pages"] = len(pages)
    if not pages:
        record["error"] = "нет страниц"
        return record

    page = pages[0]
    text = page.text or ""
    record["has_text"] = sum(c.isalnum() for c in text) >= MIN_TEXT_ALNUM
    if not record["has_text"] and use_ocr:
        ocr_text = _ocr_page(page, cancel)
        if sum(c.isalnum() for c in ocr_text) >= MIN_TEXT_ALNUM:
            text = ocr_text
            record["has_text"] = True
            record["kind_source"] = "ocr"

    if record["has_text"]:
        record["kind"] = classify_text(text)
        if record["kind_source"] != "ocr":
            record["kind_source"] = "text"
        record["fields"] = field_presence(text)
        record["geometry"] = text_geometry(text)
        record["text_pages"] = sum(
            1 for p in pages if sum(c.isalnum() for c in (p.text or "")) >= MIN_TEXT_ALNUM
        )
    else:
        image = None
        try:
            image = page.load_image()
        except Exception:                                   # noqa: BLE001
            image = None
        record["kind"] = classify_image(path, image)
        record["kind_source"] = "ratio" if image is not None else "format"
        record["geometry"] = image_geometry(image)
        record["text_pages"] = 0

    return record


def _ocr_page(page, cancel) -> str:
    """
    Локальное распознавание одной страницы. Ошибка OCR — не ошибка чтения.

    Возвращаемый текст живёт только внутри `analyze_file`: наружу уходят
    имена найденных полей и вид документа.
    """
    from core.document_ocr import recognize_image

    try:
        image = page.load_image()
        if image is None:
            return ""
        return recognize_image(image, cancel)
    except Exception:                                       # noqa: BLE001
        return ""


def walk(source: Path) -> List[Path]:
    """Все файлы папки оператора, отсортированные по пути (без вывода имён)."""
    return sorted(p for p in source.rglob("*") if p.is_file())


def render_report(records: List[Dict[str, Any]], source: Path) -> str:
    """
    Markdown-отчёт: только числа, виды и имена полей.

    Ни одного имени файла: у каждого документа есть только порядковый номер.
    """
    from collections import Counter as C

    kinds = C(r["kind"] for r in records)
    formats = C(r["format"] for r in records)
    errors = C(r["error"] for r in records if r["error"])
    text_docs = [r for r in records if r["has_text"]]
    image_docs = [r for r in records if not r["has_text"] and r["geometry"]]

    lines: List[str] = []
    lines.append("# Структура реальных документов (ЧАСТЬ 2A)")
    lines.append("")
    lines.append("> Отчёт собран локально по папке оператора. **Данных в нём нет:**")
    lines.append("> только количество, форматы, размеры, пропорции и ИМЕНА полей")
    lines.append("> бланка. Ни ФИО, ни серий, ни номеров, ни адресов, ни путей.")
    lines.append("")
    lines.append(f"- Всего файлов: **{len(records)}**")
    lines.append(f"- С текстовым слоем: **{len(text_docs)}**")
    lines.append(f"- Только изображение: **{len(image_docs)}**")
    lines.append(f"- Не прочитано: **{sum(1 for r in records if r['error'])}**")
    lines.append("")

    lines.append("## Виды документов")
    lines.append("")
    lines.append("| Вид | Файлов | Определён по тексту | по пропорции |")
    lines.append("|---|---:|---:|---:|")
    for kind, count in kinds.most_common():
        by_text = sum(1 for r in records
                      if r["kind"] == kind and r["kind_source"] == "text")
        by_ratio = sum(1 for r in records
                       if r["kind"] == kind and r["kind_source"] == "ratio")
        lines.append(f"| {kind} | {count} | {by_text} | {by_ratio} |")
    lines.append("")

    lines.append("## Форматы")
    lines.append("")
    lines.append("| Формат | Файлов |")
    lines.append("|---|---:|")
    for fmt, count in formats.most_common():
        lines.append(f"| {fmt} | {count} |")
    lines.append("")

    lines.append("## Страниц в документе")
    lines.append("")
    pages = C(r["pages"] for r in records)
    lines.append("| Страниц | Документов |")
    lines.append("|---:|---:|")
    for count_pages, docs in sorted(pages.items()):
        lines.append(f"| {count_pages} | {docs} |")
    lines.append("")

    lines.append("## Какие поля встречаются в бланках")
    lines.append("")
    lines.append("Считаются ЯРЛЫКИ полей («фамилия», «дата выдачи»), а не значения.")
    lines.append("")
    lines.append("| Поле | Документов |")
    lines.append("|---|---:|")
    fields = C(f for r in records for f in r["fields"])
    for name, count in fields.most_common():
        lines.append(f"| {name} | {count} |")
    lines.append("")

    lines.append("## Геометрия текстового слоя")
    lines.append("")
    if text_docs:
        line_counts = [r["geometry"].get("lines", 0) for r in text_docs]
        max_lines = [r["geometry"].get("max_line", 0) for r in text_docs]
        dates = [r["geometry"].get("dates", 0) for r in text_docs]
        runs = [r["geometry"].get("digit_runs", 0) for r in text_docs]
        lines.append(f"- Строк в документе: от {min(line_counts)} до {max(line_counts)}, "
                     f"в среднем {sum(line_counts) / len(line_counts):.1f}")
        lines.append(f"- Самая длинная строка: от {min(max_lines)} до {max(max_lines)} символов")
        lines.append(f"- Дат в формате ДД.ММ.ГГГГ: от {min(dates)} до {max(dates)}")
        lines.append(f"- Числовых последовательностей от 6 цифр: от {min(runs)} до {max(runs)}")
        with_months = sum(1 for r in text_docs
                          if r["geometry"].get("month_words", 0) > 0)
        lines.append(f"- Документов с названием месяца словами: {with_months}")
    else:
        lines.append("- Текстовых документов нет.")
    lines.append("")

    lines.append("## Геометрия изображений")
    lines.append("")
    if image_docs:
        ratios = sorted({r["geometry"].get("ratio", 0) for r in image_docs})
        widths = [r["geometry"].get("width", 0) for r in image_docs]
        heights = [r["geometry"].get("height", 0) for r in image_docs]
        mp = [r["geometry"].get("megapixels", 0) for r in image_docs]
        dark = [r["geometry"].get("dark_share", 0) for r in image_docs]
        lines.append(f"- Размеры: ширина {min(widths)}–{max(widths)}, "
                     f"высота {min(heights)}–{max(heights)}")
        lines.append(f"- Мегапикселей: {min(mp):.2f}–{max(mp):.2f}")
        lines.append(f"- Доля тёмных пикселей: {min(dark):.3f}–{max(dark):.3f}")
        lines.append(f"- Пропорции (всего {len(ratios)} различных):")
        for ratio in ratios:
            count = sum(1 for r in image_docs
                        if r["geometry"].get("ratio") == ratio)
            lines.append(f"  - {ratio:.3f} — {count} шт.")
        lines.append("")
        lines.append("Пропорции бланков: паспорт РФ и ПТС (A5) ≈ 1,42; "
                     "ВУ и банковская карта ≈ 1,59; СТС ≈ 1,33.")
    else:
        lines.append("- Изображений без текстового слоя нет.")
    lines.append("")

    if errors:
        lines.append("## Ошибки чтения")
        lines.append("")
        lines.append("| Тип ошибки | Файлов |")
        lines.append("|---|---:|")
        for error, count in errors.most_common():
            lines.append(f"| {error} | {count} |")
        lines.append("")

    lines.append("## Выводы для шаблонов (ЧАСТЬ 2B)")
    lines.append("")
    lines.append("Шаблоны синтетических документов собираются по этому списку:")
    lines.append("берутся пропорции и порядок блоков выше, а значения —")
    lines.append("вымышленные. Ни один реальный документ в шаблон не копируется.")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(
        description="Разбор структуры реальных документов (только локально)"
    )
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--json", default=str(DEFAULT_JSON))
    parser.add_argument("--limit", type=int, default=0,
                        help="ограничить число файлов (0 — все)")
    parser.add_argument("--ocr", action="store_true",
                        help="распознавать картинки локальным Tesseract "
                             "(дольше, но вид документа определяется точнее)")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.exists():
        print(f"Папка не найдена: {source}")
        return 2

    import threading
    cancel = threading.Event()

    files = walk(source)
    if args.limit:
        files = files[: args.limit]

    records = []
    for index, path in enumerate(files):
        try:
            records.append(analyze_file(path, index, cancel, use_ocr=args.ocr))
        except ImportCancelled:
            break
        except Exception as exc:                            # noqa: BLE001
            records.append({"index": index, "format": "unknown", "pages": 0,
                            "kind": "unreadable", "kind_source": "format",
                            "has_text": False, "fields": [], "geometry": {},
                            "error": type(exc).__name__})

    report = render_report(records, source)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    json_path = Path(args.json)
    json_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    kinds = Counter(r["kind"] for r in records)
    print(f"Файлов обработано: {len(records)}")
    print(f"Виды: {dict(kinds.most_common())}")
    print(f"Отчёт: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
