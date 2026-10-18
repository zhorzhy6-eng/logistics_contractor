#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Анализатор шаблонов договоров (DOCX/XLSX) — ТОЛЬКО ЧТЕНИЕ.

Зачем модуль
------------
Правка шаблона — самая опасная операция в программе: шаблон заполняется
docxtpl, и испорченный плейсхолдер ({{contract_number}}) или разорванная
пара {%p if %}…{%p endif %} даёт либо пустое поле в договоре, либо ошибку при
создании документа — уже у оператора, на живом клиенте. Поэтому прежде чем
что-то менять, шаблон нужно ПРОЧИТАТЬ и понять:

  * какие в нём плейсхолдеры и сколько раз каждый;
  * какие условные блоки и все ли они закрыты;
  * сколько абзацев и таблиц (чтобы заметить «пустой» шаблон);
  * какие стили абзацев используются (их сохранность проверяет правка);
  * есть ли устаревший текст — например «НДС 20%» при ставке 22%.

Модуль НИЧЕГО не меняет: ни байта в файле. Правка — отдельный модуль
(template_editor.py), и она всегда идёт через диалог подтверждения.

Что считается условным блоком
-----------------------------
docxtpl поддерживает два вида тегов:

  * {{ … }}      — подстановка значения;
  * {%p if … %} … {%p endif %} — условный блок на абзац;
  * {% for … %} … {% endfor %}   — цикл по строкам таблицы.

Парность проверяется стеком по порядку появления тегов в документе:
незакрытый if/for попадает в unclosed_tags и в warnings.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from core import app_paths

logger = logging.getLogger("services.agent.tools.template_analyzer")

#: Корень ЧТЕНИЯ ресурсов: в .exe — папка сборки (`_MEIPASS`), в разработке —
#: корень проекта (см. core/app_paths.py). Отсюда берутся папки шаблонов.
PROJECT_ROOT = app_paths.resource_path()

#: Подстановка значения docxtpl: {{ имя }} или {{ объект.поле }}.
PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

#: Теги блоков: {%p if … %}, {%p endif %}, {% for … %}, {% endfor %},
#: {% if … %}, {% endif %}. Регистр и лишние пробелы допускаются.
TAG_RE = re.compile(r"\{%\s*p?\s*(if|endif|for|endfor|elif|else)\b([^%]*)%\}", re.IGNORECASE)

#: Устаревшие ставки НДС. 20% перестали применяться с 01.01.2026 (ст. 164
#: НК РФ, ред. 425-ФЗ), но в старых шаблонах они остались.
VAT_20_RE = re.compile(r"(?:НДС[^\n]{0,20}?20\s*%|20\s*%[^\n]{0,20}?НДС)", re.IGNORECASE)
VAT_18_RE = re.compile(r"(?:НДС[^\n]{0,20}?18\s*%|18\s*%[^\n]{0,20}?НДС)", re.IGNORECASE)

#: Сколько символов устаревшего фрагмента показывать в предупреждении.
OUTDATED_SNIPPET = 120

#: Поддерживаемые форматы шаблонов.
SUPPORTED_SUFFIXES = (".docx", ".xlsx")


# ─────────────────────────────────────────────────────────────
# Чтение текста
# ─────────────────────────────────────────────────────────────

def _docx_blocks(path: Path) -> Tuple[List[str], int, List[str], int]:
    """
    Текст шаблона DOCX: абзацы, число таблиц, стили, число абзацев.

    Читается тело документа, затем таблицы по ячейкам. Порядок важен для
    парности тегов: если {%p if %} и {%p endif %} стоят в одной таблице, они
    должны попасть в список рядом (абзацы таблицы идут подряд).

    :return: (тексты, таблиц, стили, абзацев).
    """
    from docx import Document  # локальный импорт: модуль нужен только здесь

    document = Document(str(path))
    texts: List[str] = []
    styles: List[str] = []
    paragraphs = 0

    for paragraph in document.paragraphs:
        style = getattr(paragraph.style, "name", "") or ""
        if style:
            styles.append(style)
        texts.append(paragraph.text)
        paragraphs += 1

    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    style = getattr(paragraph.style, "name", "") or ""
                    if style:
                        styles.append(style)
                    texts.append(paragraph.text)
                    paragraphs += 1

    return texts, len(document.tables), styles, paragraphs


def _xlsx_blocks(path: Path) -> Tuple[List[str], int, List[str], int]:
    """
    Текст шаблона XLSX: значения ячеек как абзацы.

    :return: (тексты, «таблиц» = листов, стили пустые, ячеек).
    """
    from openpyxl import load_workbook

    workbook = load_workbook(str(path), data_only=False, read_only=True)
    texts: List[str] = []
    try:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                for value in row:
                    if isinstance(value, str) and value.strip():
                        texts.append(value)
    finally:
        workbook.close()
    return texts, len(workbook.sheetnames), [], len(texts)


def read_template_text(template_path: Any) -> Tuple[List[str], Dict[str, Any]]:
    """
    Тексты шаблона и его «паспорт»: формат, таблицы, стили, абзацы.

    Ошибка открытия не бросается наружу: возвращается пустой текст и error
    в паспорте — анализатор обязан честно сказать «файл не открылся», а не
    упасть.

    :return: (тексты, {"format", "tables", "styles", "paragraphs", "error"}).
    """
    path = Path(str(template_path or ""))
    passport: Dict[str, Any] = {
        "format": "", "tables": 0, "styles": [], "paragraphs": 0, "error": "",
    }
    if not path.is_file():
        passport["error"] = f"файла нет: {path}"
        return [], passport

    suffix = path.suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        passport["error"] = (
            f"формат {suffix or '(без расширения)'} не поддерживается: "
            f"анализируются {', '.join(SUPPORTED_SUFFIXES)}"
        )
        return [], passport

    try:
        if suffix == ".docx":
            texts, tables, styles, paragraphs = _docx_blocks(path)
            passport["format"] = "docx"
        else:
            texts, tables, styles, paragraphs = _xlsx_blocks(path)
            passport["format"] = "xlsx"
    except Exception as error:  # noqa: BLE001 — битый файл не должен ронять окно
        logger.warning("Шаблон %s не открылся: %s", path.name, type(error).__name__)
        passport["error"] = f"файл не открывается ({type(error).__name__})"
        return [], passport

    passport["tables"] = tables
    passport["styles"] = styles
    passport["paragraphs"] = paragraphs
    return texts, passport


# ─────────────────────────────────────────────────────────────
# Разбор плейсхолдеров и блоков
# ─────────────────────────────────────────────────────────────

def find_placeholders(texts: Iterable[str]) -> List[Dict[str, Any]]:
    """
    Плейсхолдеры документа: имя, как записан, сколько раз встречается.

    Порядок — по первому появлению: оператор читает список так же, как
    документ.
    """
    order: List[str] = []
    counts: Dict[str, int] = {}
    raw: Dict[str, str] = {}
    for text in texts:
        for match in PLACEHOLDER_RE.finditer(text or ""):
            name = match.group(1).strip()
            if not name:
                continue
            if name not in counts:
                order.append(name)
                raw[name] = match.group(0)
            counts[name] = counts.get(name, 0) + 1
    return [{"name": name, "raw": raw[name], "count": counts[name]}
            for name in order]


def find_blocks(texts: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Условные блоки и циклы документа: какие, где и закрыты ли.

    :return: {"blocks": […], "unclosed": […], "extra_closing": […]}
        * blocks — все встреченные теги;
        * unclosed — открытые и не закрытые (шаблон сломан);
        * extra_closing — закрывающие теги без открытия (тоже поломка).
    """
    blocks: List[Dict[str, Any]] = []
    stack: List[Dict[str, Any]] = []
    extra_closing: List[Dict[str, Any]] = []

    for index, text in enumerate(texts):
        for match in TAG_RE.finditer(text or ""):
            kind = match.group(1).casefold()
            expression = " ".join((match.group(2) or "").split())
            block = {
                "kind": kind,
                "expression": expression,
                "paragraph_index": index,
                "closed": False,
                "index": len(blocks),
            }
            blocks.append(block)

            if kind in ("if", "for"):
                stack.append(block)
            elif kind in ("endif", "endfor"):
                expected = "if" if kind == "endif" else "for"
                # Ищем на верхушке стека парный открывающий тег.
                for depth in range(len(stack) - 1, -1, -1):
                    if stack[depth]["kind"] == expected:
                        stack[depth]["closed"] = True
                        del stack[depth:]
                        break
                else:
                    extra_closing.append(block)

    return {"blocks": blocks, "unclosed": stack,
            "extra_closing": extra_closing}


def find_outdated(texts: Iterable[str]) -> List[Dict[str, str]]:
    """
    Устаревшие тексты: старые ставки НДС.

    Проверка важна именно на шаблонах: в договоре 2026 года «НДС 20%»
    занижает налог, а оператор этого не видит за реквизитами.
    """
    found: List[Dict[str, str]] = []
    for text in texts:
        line = text or ""
        for pattern, message, kind in (
            (VAT_20_RE, "Устаревшая ставка НДС 20%: с 01.01.2026 основная "
                        "ставка — 22% (НК РФ ст. 164 п. 3).", "vat_20"),
            (VAT_18_RE, "Устаревшая ставка НДС 18%: с 01.01.2019 основная "
                        "ставка — 20%, с 01.01.2026 — 22%.", "vat_18"),
        ):
            match = pattern.search(line)
            if match:
                found.append({
                    "kind": kind,
                    "text": " ".join(line[max(0, match.start() - 20):
                                          match.start() + OUTDATED_SNIPPET].split()),
                    "message": message,
                })
                break
    return found


# ─────────────────────────────────────────────────────────────
# Анализ
# ─────────────────────────────────────────────────────────────

def analyze_template(template_path: Any) -> Dict[str, Any]:
    """
    Анализ шаблона: плейсхолдеры, блоки, стили, устаревшие тексты.

    Шаблон НЕ меняется. Результат — словарь вида::

        {
          "path": "templates/shablon_ooo.docx",
          "exists": True, "format": "docx",
          "placeholders": [{"name": "contract_number",
                            "raw": "{{contract_number}}", "count": 3}],
          "placeholder_count": 12,
          "conditional_blocks": [{"kind": "if", "expression": "is_vat_free",
                                  "closed": True, …}],
          "unclosed_tags": [],
          "paragraphs_count": 214,
          "tables_count": 5,
          "styles_count": 7, "styles": ["Normal", …],
          "has_vat_20": False,
          "outdated": [{"kind": "vat_20", "text": "НДС 20%", "message": "…"}],
          "warnings": ["…"],
          "ok": True,
        }

    :param template_path: путь к .docx или .xlsx.
    """
    path = Path(str(template_path or ""))
    texts, passport = read_template_text(path)

    warnings: List[str] = []
    if passport["error"]:
        warnings.append(passport["error"])

    placeholders = find_placeholders(texts)
    blocks = find_blocks(texts)
    outdated = find_outdated(texts)

    for block in blocks["unclosed"]:
        warnings.append(
            f"Не закрыт блок «{block['kind']} {block['expression']}» "
            f"(абзац {block['paragraph_index'] + 1}): документ не соберётся."
        )
    for block in blocks["extra_closing"]:
        warnings.append(
            f"Закрывающий тег «{block['kind']}» без открытия "
            f"(абзац {block['paragraph_index'] + 1})."
        )
    for item in outdated:
        warnings.append(item["message"])

    if not passport["error"]:
        if not placeholders:
            warnings.append(
                "Плейсхолдеров не найдено: шаблон не заполняется данными — "
                "проверьте, тот ли это файл."
            )
        if not texts:
            warnings.append("В шаблоне нет текста.")
        # У XLSX нет стилей абзацев — это свойство формата, а не поломка.
        if (passport["format"] == "docx" and passport["paragraphs"]
                and not passport["styles"]):
            warnings.append("У абзацев нет стилей: проверьте оформление.")

    has_vat_20 = any(item["kind"] == "vat_20" for item in outdated)
    styles = passport["styles"]

    logger.info(
        "Анализ шаблона %s: плейсхолдеров %s, блоков %s, предупреждений %s",
        path.name, len(placeholders), len(blocks["blocks"]), len(warnings),
    )
    return {
        "path": str(path),
        "exists": path.is_file(),
        "format": passport["format"],
        "placeholders": placeholders,
        "placeholder_count": len(placeholders),
        "conditional_blocks": blocks["blocks"],
        "unclosed_tags": blocks["unclosed"],
        "extra_closing_tags": blocks["extra_closing"],
        "paragraphs_count": passport["paragraphs"],
        "tables_count": passport["tables"],
        "styles_count": len(set(styles)),
        "styles": sorted(set(styles)),
        "has_vat_20": has_vat_20,
        "outdated": outdated,
        "warnings": warnings,
        "ok": not passport["error"],
    }


# ─────────────────────────────────────────────────────────────
# Сравнение «до / после» — для безопасной правки
# ─────────────────────────────────────────────────────────────

def regressions(before: Dict[str, Any],
                after: Dict[str, Any]) -> List[str]:
    """
    Что сломалось после правки шаблона (пустой список — всё цело).

    Сравниваются три вещи, за которые отвечает правка:

      1. набор и кратность плейсхолдеров (пропал или задвоился — поломка);
      2. парность условных блоков (незакрытый блок — документ не соберётся);
      3. стили абзацев (новый стиль — переоформление текста).

    Списки стилей сравниваются как множества: правка вправе добавить или
    убрать абзац, но не вправе перевести текст в другой стиль незаметно.
    """
    problems: List[str] = []

    before_names = {item["name"]: item["count"] for item in before.get("placeholders") or []}
    after_names = {item["name"]: item["count"] for item in after.get("placeholders") or []}

    lost = sorted(set(before_names) - set(after_names))
    if lost:
        problems.append("Пропали плейсхолдеры: " + ", ".join(lost))

    added = sorted(set(after_names) - set(before_names))
    if added:
        problems.append("Появились новые плейсхолдеры: " + ", ".join(added))

    changed = [
        f"{name} ({before_names[name]} → {after_names[name]})"
        for name in sorted(set(before_names) & set(after_names))
        if before_names[name] != after_names[name]
    ]
    if changed:
        problems.append("Изменилось число вхождений: " + ", ".join(changed))

    if after.get("unclosed_tags"):
        problems.append("Остались незакрытые блоки: "
                        + ", ".join(str(item.get("kind")) for item in after["unclosed_tags"]))

    lost_styles = sorted(set(before.get("styles") or []) - set(after.get("styles") or []))
    if lost_styles:
        problems.append("Пропали стили абзацев: " + ", ".join(lost_styles))

    if not after.get("ok", True):
        problems.append("Шаблон после правки не открывается")

    return problems


def is_template_safe(template_path: Any) -> bool:
    """
    Можно ли считать шаблон целым: открывается, плейсхолдеры есть, блоки парные.

    Короткий ответ для панели и тестов; подробности — в analyze_template.
    """
    result = analyze_template(template_path)
    return bool(
        result.get("ok")
        and result.get("placeholder_count")
        and not result.get("unclosed_tags")
        and not result.get("extra_closing_tags")
    )




# ─────────────────────────────────────────────────────────────
# Где лежат шаблоны
# ─────────────────────────────────────────────────────────────

def template_dirs() -> List[Path]:
    """
    Папки шаблонов: публичная и (если есть) приватная.

    Приватных шаблонов в публичном репозитории нет — они лежат в соседней
    папке (core/private_path.py). Отсутствие приватной — норма: анализатор
    тогда видит только публичные бланки.
    """
    dirs: List[Path] = [PROJECT_ROOT / "templates"]
    try:
        from core.private_path import private_path

        if private_path is not None:
            private_templates = private_path("templates")
            if private_templates is not None and Path(private_templates).is_dir():
                dirs.append(Path(private_templates))
    except Exception:  # noqa: BLE001 — приватной части может не быть
        pass
    return [path for path in dirs if path.is_dir()]


def list_templates() -> List[Dict[str, Any]]:
    """
    Найденные шаблоны: имя, путь, формат, папка.

    Список нужен диалогу шаблонов и тестам: не нужно помнить имена файлов,
    а новый шаблон появляется сам.
    """
    found: List[Dict[str, Any]] = []
    seen = set()
    for directory in template_dirs():
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.casefold() not in SUPPORTED_SUFFIXES:
                continue
            key = path.name.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append({
                "name": path.name,
                "path": str(path),
                "suffix": path.suffix.casefold(),
                "directory": str(directory),
            })
    return found


def resolve_template_path(name: Any) -> Path:
    """
    Путь шаблона по имени или пути.

    Порядок: абсолютный/относительный путь как есть → имя в папках шаблонов.
    Не нашлось — возвращается путь, которого нет: анализатор честно
    ответит «файла нет», а не подставит чужой шаблон.
    """
    text = str(name or "").strip()
    if not text:
        return PROJECT_ROOT / "templates"
    candidate = Path(text)
    if candidate.is_file():
        return candidate
    for directory in template_dirs():
        path = directory / text
        if path.is_file():
            return path
    # Имя без расширения: ищем однозначное совпадение по началу имени.
    if not candidate.suffix:
        matches = [item for item in list_templates()
                   if Path(item["name"]).stem.casefold() == text.casefold()]
        if len(matches) == 1:
            return Path(matches[0]["path"])
    return PROJECT_ROOT / "templates" / text


__all__ = [
    "OUTDATED_SNIPPET",
    "PLACEHOLDER_RE",
    "SUPPORTED_SUFFIXES",
    "TAG_RE",
    "VAT_18_RE",
    "VAT_20_RE",
    "analyze_template",
    "find_blocks",
    "find_outdated",
    "find_placeholders",
    "is_template_safe",
    "list_templates",
    "read_template_text",
    "regressions",
    "resolve_template_path",
    "template_dirs",
]

