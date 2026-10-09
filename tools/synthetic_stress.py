#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Стресс-прогон распознавания документов (ЧАСТИ 2D и 2E стресс-теста).

Три режима:

    python tools/synthetic_stress.py real          # ЧАСТЬ 2D: реальные документы
    python tools/synthetic_stress.py synthetic     # ЧАСТЬ 2E: 1000 синтетических
    python tools/synthetic_stress.py gigachat      # ЧАСТЬ 2E: ≤25 запросов к модели
    python tools/synthetic_stress.py baseline      # baseline.json для регрессии

РЕАЛЬНЫЕ ДОКУМЕНТЫ (режим `real`) — САМАЯ ОПАСНАЯ ЧАСТЬ
------------------------------------------------------
Читаются ЛОКАЛЬНО, поля извлекаются ЛОКАЛЬНО, ground truth для них
НЕИЗВЕСТЕН. Проверяется только одно: разбор не падает и что-то находит.
В отчёт уходят ЧИСЛА: сколько файлов, сколько обработано, сколько ошибок и
каких ТИПОВ. Ни имён файлов, ни путей, ни значений полей. Примеры ошибок —
только текст исключения, без фрагментов документа.

СИНТЕТИЧЕСКИЕ ДОКУМЕНТЫ (режим `synthetic`)
-------------------------------------------
1000 сценариев из `tools/make_synthetic_docs.py`. Ground truth известен,
поэтому считаются recall / precision / F1 — по видам документов, по уровням
качества и по каждому полю.

GIGACHAT (режим `gigachat`)
---------------------------
Не больше 25 запросов, ТОЛЬКО синтетические документы, пауза 2 с между
запросами. 429 → пауза 30 с и повтор; 401 → СТОП (ключ перестал работать);
5xx → пауза 10 с и повтор; недоступен — пропустить и продолжить.
"""

import argparse
import json
import re
import shutil
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: Папка оператора. Имя на диске — с опечаткой, и это НЕ ошибка.
REAL_SOURCE = PROJECT_ROOT / "Документы для распознования"
SYNTHETIC_DIR = PROJECT_ROOT / "tests" / "_tmp" / "synthetic_docs"
OUT_DIR = PROJECT_ROOT / "tests" / "_tmp"

REAL_REPORT = OUT_DIR / "real_docs_report.json"
SYNTH_REPORT = SYNTHETIC_DIR / "local_report.json"
GIGA_REPORT = SYNTHETIC_DIR / "gigachat_report.json"
BASELINE_PATH = PROJECT_ROOT / "tests" / "data" / "synthetic_docs" / "baseline.json"

#: Сколько файлов реальных документов брать (0 — все).
REAL_LIMIT = 500

#: Лимит запросов к GigaChat и пауза между ними (требование задания).
GIGACHAT_MAX_REQUESTS = 25
GIGACHAT_PAUSE = 2.0
GIGACHAT_PAUSE_429 = 30.0
GIGACHAT_PAUSE_5XX = 10.0

#: Сколько процессов использовать для локального распознавания. Tesseract
#: упирается в процессор, а не в диск: на четырёх ядрах выигрыш почти
#: линейный. Больше 4 процессов смысла нет — памяти у машины немного
#: (см. STATE.md: полный pytest в один процесс падает по MemoryError).
WORKERS = 4


# ─────────────────────────────────────────────────────────────
# Сравнение полей
# ─────────────────────────────────────────────────────────────

def _norm(value: Any) -> str:
    """
    Значение к сравнимому виду: регистр, пробелы, разделители.

    «45 12» и «4512», «г. Москва» и «г Москва», «B, C, CE» и «bcce» —
    одно и то же значение, записанное чуть иначе. Считать это ошибкой
    распознавания нельзя: разбор возвращает то, что напечатано, а
    нормализацию делает следующий слой (core.recognizer).

    ЦИФРЫ при этом не «чинятся»: сравниваются ровно те цифры, что есть.
    """
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        value = f"{value:g}"
    text = str(value).strip().casefold()
    text = re.sub(r"[^\w]+", "", text, flags=re.UNICODE)
    for article in ("г", "ул", "д", "кв", "обл", "край", "респ"):
        text = re.sub(rf"{article}(?=[а-яё])", "", text)
    return text


#: Поля, значения которых сравниваются как даты.
DATE_KEYS = ("birth_date", "passport_issue_date", "license_issue_date",
             "license_expiry_date", "date")


def _norm_field(key: str, value: Any) -> str:
    """Нормализация с учётом вида поля: даты — к ДД.ММ.ГГГГ."""
    if any(key.endswith(name) for name in DATE_KEYS):
        from core.dates import to_display
        shown = to_display(value)
        return _norm(shown) if shown else ""
    return _norm(value)


def flatten(section_data: Any) -> Dict[str, str]:
    """
    Данные разбора к плоскому виду «раздел.поле» → значение.

    Списки (машины, тягачи, прицепы) разворачиваются по индексу: сравнение
    «первая машина с первой» — единственный осмысленный вариант, когда
    ground truth содержит ровно одну машину на документ.
    """
    flat: Dict[str, str] = {}
    if not isinstance(section_data, dict):
        return flat
    for section, payload in section_data.items():
        if section.startswith("_"):
            continue
        if isinstance(payload, list):
            for index, item in enumerate(payload):
                if isinstance(item, dict):
                    for key, value in item.items():
                        if key.startswith("_"):
                            continue
                        flat[f"{section}[{index}].{key}"] = value
        elif isinstance(payload, dict):
            for key, value in payload.items():
                if key.startswith("_"):
                    continue
                flat[f"{section}.{key}"] = value
    return flat


def score(expected: Dict[str, Any], got: Any) -> Dict[str, Any]:
    """
    Recall / precision / F1 одного документа.

    :param expected: ground truth В ТОЙ ЖЕ форме, что отдаёт разбор:
        `{"driver": {...}}` или `{"tractor": [{...}]}`. Форма важна: ключи
        обеих сторон должны получаться одним правилом, иначе сравнение
        вырождается в «всё пропущено» (эта ошибка была в первой версии
        инструмента и давала нулевой recall на всех 1000 сценариях).
    :param got: то, что вернул `extract_local_fields`.

    Знаменатель recall — поля ground truth, у которых ЕСТЬ значение: пустое
    поле нечего «вспоминать». Знаменатель precision — поля, которые разбор
    заполнил: пустые ответы качество не портят.

    Списки сопоставляются ПО ИНДЕКСУ («первая машина с первой»): ground truth
    содержит не больше одной машины на документ, поэтому другого
    сопоставления и не требуется.
    """
    found = flatten(got)
    expected_flat = flatten(expected)

    def norm_map(source: Dict[str, Any]) -> Dict[str, str]:
        """Ключи без индексов списка: «driver[0].full_name» → «driver.full_name»."""
        result: Dict[str, str] = {}
        for key, value in source.items():
            if not _norm(value):
                continue
            result[re.sub(r"\[\d+\]", "", key)] = _norm_field(key, value)
        return result

    expected_norm = norm_map(expected_flat)
    found_norm = norm_map(found)
    # Индексные ключи остаются для второго прохода: если поле помечено
    # списком, а ground truth — нет (и наоборот), сравнение всё равно должно
    # состояться.
    found_indexed = {key: _norm_field(key, value)
                     for key, value in found.items() if _norm(value)}

    true_positive = 0
    missed: List[str] = []
    wrong: List[str] = []
    matched_indexed: set = set()
    for key, value in expected_norm.items():
        actual = found_norm.get(key)
        if actual is None:
            # Поле могло прийти с индексом списка: «tractor[0].plate_number».
            for indexed_key, indexed_value in found_indexed.items():
                if indexed_key not in matched_indexed and \
                        re.sub(r"\[\d+\]", "", indexed_key) == key:
                    actual = indexed_value
                    matched_indexed.add(indexed_key)
                    break
        if actual is None:
            missed.append(key)
        elif actual == value:
            true_positive += 1
        else:
            wrong.append(key)

    extra = [key for key in found_norm if key not in expected_norm]

    recall = true_positive / len(expected_norm) if expected_norm else 1.0
    precision = (true_positive / len(found_norm)) if found_norm else 0.0
    f1 = (2 * recall * precision / (recall + precision)
          if (recall + precision) else 0.0)

    return {
        "expected": len(expected_norm),
        "found": len(found_norm),
        "true_positive": true_positive,
        "missed": missed,
        "wrong": wrong,
        "extra": extra,
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "f1": round(f1, 4),
    }


# ─────────────────────────────────────────────────────────────
# Локальное распознавание одного синтетического документа
# ─────────────────────────────────────────────────────────────

def _recognize_path(path: Path, use_ocr: bool = True) -> Tuple[str, str]:
    """
    Текст документа: сначала текстовый слой, затем локальный OCR.

    Читается тем же core.document_reader, что и в приложении, и
    распознаётся тем же core.document_ocr — иначе метрика описывала бы
    другой конвейер.

    :return: (текст, как получен: "text" | "ocr" | "")
    """
    from core.document_reader import read_document

    cancel = threading.Event()
    pages = list(read_document(path, cancel, ""))
    if not pages:
        return "", ""
    page = pages[0]
    text = page.text or ""
    if sum(c.isalnum() for c in text) >= 40:
        return text, "text"
    if not use_ocr:
        return "", ""
    try:
        from core.document_ocr import recognize_image

        image = page.load_image()
        if image is None:
            return "", ""
        return recognize_image(image, cancel), "ocr"
    except Exception:                                       # noqa: BLE001
        return "", ""


def _process_synthetic(number: str) -> Dict[str, Any]:
    """
    Один синтетический сценарий: распознать и сравнить с ground truth.

    Функция живёт на верхнем уровне модуля: её зовут из ProcessPoolExecutor,
    а вложенные функции туда не передать.
    """
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from core.document_import_service import extract_local_fields

    gt_file = SYNTHETIC_DIR / "gt" / f"{number}.json"
    record = json.loads(gt_file.read_text(encoding="utf-8"))
    # «image» в ground truth — путь ОТ папки synthetic_docs («images/1.png»).
    image = SYNTHETIC_DIR / record["image"]

    result: Dict[str, Any] = {
        "number": record["number"],
        "kind": record["kind"],
        "quality": record["quality"],
        "defect": record["defect"],
        "source": "",
        "error": "",
    }
    try:
        text, source = _recognize_path(image)
        result["source"] = source
        if not text:
            result["error"] = "нет текста"
            result["score"] = score(record["gt"], {})
            return result
        data = extract_local_fields(text)
        result["score"] = score(record["gt"], data or {})
    except Exception as exc:                                # noqa: BLE001
        result["error"] = type(exc).__name__
        result["score"] = score(record["gt"], {})
    return result


def run_synthetic(limit: int = 0, workers: int = WORKERS,
                  out_path: Path = SYNTH_REPORT) -> Dict[str, Any]:
    """Локальный прогон по всем синтетическим сценариям."""
    index_path = SYNTHETIC_DIR / "index.json"
    if not index_path.exists():
        print("Нет синтетических документов. Сначала:")
        print("  python tools/make_synthetic_docs.py")
        return {}

    index = json.loads(index_path.read_text(encoding="utf-8"))
    numbers = [str(item["number"]) for item in index["scenarios"]]
    if limit:
        numbers = numbers[:limit]

    started = time.time()
    results: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for done, result in enumerate(pool.map(_process_synthetic, numbers), 1):
            results.append(result)
            if done % 100 == 0:
                print(f"  обработано {done} из {len(numbers)}")

    report = summarise_local(results, time.time() - started)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    # Подробности по каждому сценарию — отдельным файлом: сводка остаётся
    # читаемой, а разбор «почему провалился сценарий 731» — возможным.
    details_path = out_path.with_name("local_details.json")
    details_path.write_text(json.dumps(results, ensure_ascii=False),
                            encoding="utf-8")
    return report


def summarise_local(results: List[Dict[str, Any]],
                    seconds: float) -> Dict[str, Any]:
    """Сводка локального прогона: по видам, по качеству, по полям."""
    def aggregate(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not items:
            return {}
        expected = sum(r["score"]["expected"] for r in items)
        found = sum(r["score"]["found"] for r in items)
        tp = sum(r["score"]["true_positive"] for r in items)
        recall = tp / expected if expected else 0.0
        precision = tp / found if found else 0.0
        f1 = (2 * recall * precision / (recall + precision)
              if (recall + precision) else 0.0)
        return {
            "documents": len(items),
            "expected": expected,
            "found": found,
            "true_positive": tp,
            "recall": round(recall, 4),
            "precision": round(precision, 4),
            "f1": round(f1, 4),
            "empty_results": sum(1 for r in items if not r["score"]["found"]),
            "errors": sum(1 for r in items if r["error"]),
        }

    by_kind: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_quality: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_defect: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_kind_quality: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for result in results:
        by_kind[result["kind"]].append(result)
        by_quality[result["quality"]].append(result)
        by_defect[result["defect"] or "нет"].append(result)
        by_kind_quality[f"{result['kind']}/{result['quality']}"].append(result)

    missed = Counter()
    wrong = Counter()
    for result in results:
        missed.update(result["score"]["missed"])
        wrong.update(result["score"]["wrong"])

    return {
        "documents": len(results),
        "seconds": round(seconds, 1),
        "overall": aggregate(results),
        "by_kind": {k: aggregate(v) for k, v in sorted(by_kind.items())},
        "by_quality": {k: aggregate(v) for k, v in sorted(by_quality.items())},
        "by_defect": {k: aggregate(v) for k, v in sorted(by_defect.items())},
        "by_kind_quality": {k: aggregate(v)
                            for k, v in sorted(by_kind_quality.items())},
        "top_missed_fields": missed.most_common(20),
        "top_wrong_fields": wrong.most_common(20),
        "worst_scenarios": sorted(
            ({"number": r["number"], "kind": r["kind"], "quality": r["quality"],
              "defect": r["defect"], "recall": r["score"]["recall"],
              "error": r["error"]}
             for r in results),
            key=lambda item: (item["recall"], -item["number"]),
        )[:25],
    }


# ─────────────────────────────────────────────────────────────
# Реальные документы (ЧАСТЬ 2D)
# ─────────────────────────────────────────────────────────────

def _error_kind(outcome: Dict[str, Any]) -> str:
    """
    Вид ошибки — ТИП, а не файл и не значение.

    «Неподдерживаемый формат файла.» и «Изображение больше 40 мегапикселей.»
    различаются; «RuntimeError» — нет. Поэтому у своих ошибок берётся текст
    сообщения, а у чужих типов — имя типа: сообщение сторонней библиотеки
    может содержать фрагмент документа.
    """
    error = outcome.get("error") or ""
    if not error:
        return "нет"
    head, _, tail = error.partition(": ")
    if head == "RuntimeError" and tail:
        return tail[:80]
    return head


def _process_real(path_text: str) -> Dict[str, Any]:
    """
    Один реальный документ. Значения полей НЕ возвращаются.

    Наружу уходит только: прочитался ли файл, сколько страниц, распознался ли
    текст, сколько ПОЛЕЙ заполнено и сколько из них прошло проверку формата.
    Ни одного значения, ни имени файла.
    """
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from core.document_import_service import extract_local_fields, SCHEMA, valid_value

    path = Path(path_text)
    outcome: Dict[str, Any] = {
        "format": path.suffix.lower().lstrip("."),
        "ok": False,
        "pages": 0,
        "text_source": "",
        "sections": [],
        "fields_filled": 0,
        "fields_valid": 0,
        "error": "",
    }
    try:
        from core.document_reader import read_document

        cancel = threading.Event()
        pages = list(read_document(path, cancel, ""))
        outcome["pages"] = len(pages)
        text, source = _recognize_path(path)
        outcome["text_source"] = source
        if not text:
            outcome["error"] = "нет текста"
            return outcome
        data = extract_local_fields(text)
        if not data:
            outcome["ok"] = True
            outcome["error"] = "поля не найдены"
            return outcome

        filled = valid = 0
        for section, payload in (data or {}).items():
            if section.startswith("_"):
                continue
            outcome["sections"].append(section)
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if not isinstance(item, dict):
                    continue
                for key, value in item.items():
                    if key.startswith("_") or not value:
                        continue
                    filled += 1
                    shape = "vin" if key == "_vin" else key
                    if section in SCHEMA and shape in SCHEMA[section] \
                            and valid_value(shape, value):
                        valid += 1
        outcome.update(ok=True, fields_filled=filled, fields_valid=valid)
    except Exception as exc:                                # noqa: BLE001
        # В отчёт идёт ТОЛЬКО тип и текст исключения: наши сообщения
        # значений документа не содержат (проверено по всем raise в
        # core/document_reader.py и core/document_ocr.py).
        outcome["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return outcome


def run_real(source: Path = REAL_SOURCE, limit: int = REAL_LIMIT,
             workers: int = WORKERS,
             out_path: Path = REAL_REPORT) -> Dict[str, Any]:
    """Прогон по папке оператора. Только локально, только числа в отчёте."""
    if not source.exists():
        print(f"Папка не найдена: {source}")
        return {}

    files = sorted(p for p in source.rglob("*") if p.is_file())
    if limit and len(files) > limit:
        import random

        files = random.Random(20261010).sample(files, limit)
        files.sort()

    started = time.time()
    outcomes: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for done, outcome in enumerate(
                pool.map(_process_real, [str(p) for p in files]), 1):
            outcomes.append(outcome)
            if done % 50 == 0:
                print(f"  обработано {done} из {len(files)}")

    processed_ok = sum(1 for o in outcomes if o["ok"])
    failed = len(outcomes) - processed_ok
    error_kinds = Counter()
    for outcome in outcomes:
        if outcome["ok"] and not outcome["error"]:
            continue
        error_kinds[_error_kind(outcome)] += 1
    formats = Counter(o["format"] for o in outcomes)
    sources = Counter(o["text_source"] for o in outcomes if o["text_source"])
    sections = Counter(s for o in outcomes for s in o["sections"])

    report = {
        "total": len(outcomes),
        "processed_ok": processed_ok,
        "failed": failed,
        "with_fields": sum(1 for o in outcomes if o["fields_filled"]),
        "fields_filled": sum(o["fields_filled"] for o in outcomes),
        "fields_valid": sum(o["fields_valid"] for o in outcomes),
        "formats": dict(formats.most_common()),
        "text_sources": dict(sources.most_common()),
        "sections": dict(sections.most_common()),
        # Виды ошибок — ТИПЫ, а не файлы: «файл повреждён», «формат не поддержан».
        "errors": dict(error_kinds.most_common()),
        "seconds": round(time.time() - started, 1),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return report


# ─────────────────────────────────────────────────────────────
# GigaChat (ЧАСТЬ 2E) — только синтетика, не больше 25 запросов
# ─────────────────────────────────────────────────────────────

def _worst_synthetic(limit: int) -> List[int]:
    """
    Самые плохие синтетические сценарии для проверки моделью.

    Отбор — «локальный разбор ПЛОХО разобрал», а не «не увидел вовсе»:

      * берутся сценарии, где локальный текст ЕСТЬ (иначе сравнение
        вырождается: 25 из 25 «нет текста» не говорят ничего о разборе полей,
        зато съедают весь лимит запросов);
      * сортировка — по возрастанию recall, то есть сначала те, где разбор
        потерял больше всего полей;
      * заявки исключены: у них своя, свободная форма записи, и метрика по ним
        несравнима с бланками.

    Если локального прогона ещё не было — список пуст, и вызывающий код
    сообщает об этом.
    """
    details_path = SYNTH_REPORT.with_name("local_details.json")
    if not details_path.exists():
        return []
    results = json.loads(details_path.read_text(encoding="utf-8"))
    usable = [r for r in results
              if r["kind"] != "application" and r["score"]["expected"]]
    usable.sort(key=lambda r: (r["score"]["recall"], r["number"]))

    # Кандидат годится только если локальный OCR ВЫДАЛ ТЕКСТ: источник «ocr»
    # ставится и тогда, когда распознавание вернуло пустую строку (очень
    # плохое качество, ошибка Tesseract). Сравнивать на таких сценариях
    # нечего — и, что важнее, они съедают лимит запросов, ничего не измеряя.
    picked: List[int] = []
    for result in usable:
        if len(picked) >= limit:
            break
        number = result["number"]
        try:
            gt = json.loads((SYNTHETIC_DIR / "gt" / f"{number}.json")
                            .read_text(encoding="utf-8"))
            text, _source = _recognize_path(SYNTHETIC_DIR / gt["image"])
        except Exception:                                   # noqa: BLE001
            continue
        if sum(c.isalnum() for c in text) >= 40:
            picked.append(number)
    return picked


def run_gigachat(limit: int = GIGACHAT_MAX_REQUESTS,
                 out_path: Path = GIGA_REPORT) -> Dict[str, Any]:
    """
    Прогон 25 худших синтетических сценариев через GigaChat Vision.

    429 → пауза 30 с и повтор; 401 → СТОП (ключ не работает); 5xx → пауза
    10 с и повтор; недоступен — пропустить и продолжить. Ключ в отчёт и в
    лог НЕ попадает.
    """
    from core.gigachat_client import GigaChatClient

    numbers = _worst_synthetic(limit)
    if not numbers:
        print("Нет данных локального прогона — сначала:")
        print("  python tools/synthetic_stress.py synthetic")
        return {}

    client = GigaChatClient()
    if not getattr(client, "auth_key", ""):
        report = {"requests": 0, "skipped": "нет ключа GigaChat",
                  "results": []}
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print("GigaChat: ключ не настроен — прогон пропущен.")
        return report

    from core.document_import_service import extract_local_fields
    from core.recognizer import DataMapper
    from PIL import Image

    results: List[Dict[str, Any]] = []
    requests_made = 0
    stopped = ""
    cancel = threading.Event()

    for number in numbers:
        record = json.loads(
            (SYNTHETIC_DIR / "gt" / f"{number}.json").read_text(encoding="utf-8"))
        image_path = SYNTHETIC_DIR / record["image"]
        entry: Dict[str, Any] = {
            "number": number, "kind": record["kind"],
            "quality": record["quality"], "defect": record["defect"],
            "status": "", "error": "",
        }

        attempt = 0
        while attempt < 2:
            attempt += 1
            try:
                image = Image.open(image_path).convert("RGB")
                requests_made += 1
                # recognize_image возвращает ПАРУ (данные, предупреждение):
                # без распаковки в разбор уходит кортеж, и падает уже он —
                # запрос при этом потрачен.
                data, _warning = client.recognize_image(image, cancel)
                entry["status"] = "ok"
                mapped = DataMapper.process_full_response(data or {})
                entry["score"] = score(record["gt"], mapped or {})
                break
            except Exception as exc:                        # noqa: BLE001
                message = str(exc)
                status = re.search(r"HTTP (\d{3})", message)
                code = int(status.group(1)) if status else 0
                if code == 401:
                    stopped = "401: ключ GigaChat перестал работать"
                    entry.update(status="401", error=stopped)
                    break
                if code == 429:
                    time.sleep(GIGACHAT_PAUSE_429)
                    entry.update(status="429", error="лимит запросов")
                    continue
                if 500 <= code < 600:
                    time.sleep(GIGACHAT_PAUSE_5XX)
                    entry.update(status=str(code), error="ошибка сервера")
                    continue
                entry.update(status="error",
                             error=f"{type(exc).__name__}: {message[:120]}")
                break
        if not entry.get("score"):
            entry.setdefault("score", score(record["gt"], {}))
        results.append(entry)

        if stopped:
            break
        time.sleep(GIGACHAT_PAUSE)

    report = {
        "mode": "gigachat_vision",
        "requests": requests_made,
        "limit": limit,
        "pause_seconds": GIGACHAT_PAUSE,
        "stopped": stopped,
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "results": results,
        "metrics": summarise_local(
            [{"number": r["number"], "kind": r["kind"], "quality": r["quality"],
              "defect": r["defect"], "error": r["error"], "score": r["score"]}
             for r in results], 0.0) if results else {},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return report


# ─────────────────────────────────────────────────────────────
# Baseline для регрессии (ЧАСТЬ 4)
# ─────────────────────────────────────────────────────────────

def write_baseline(report: Optional[Dict[str, Any]] = None,
                   out_path: Path = BASELINE_PATH,
                   report_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Фиксирует метрики локального прогона как baseline регрессии.

    В baseline идут ТОЛЬКО числа: recall / precision / F1 по видам и уровням
    качества, число документов и время. Ни одного значения из документа —
    файл коммитится в git (tests/data/), и ПДн в нём быть не может.

    :param report: готовый отчёт; если не передан, читается `report_path`.
    """
    if report is None:
        source = report_path or SYNTH_REPORT
        if not source.exists():
            print("Нет отчёта локального прогона — сначала:")
            print("  python tools/synthetic_stress.py synthetic")
            return {}
        report = json.loads(source.read_text(encoding="utf-8"))

    baseline = {
        "documents": report["documents"],
        "overall": report["overall"],
        "by_kind": {k: {"recall": v["recall"], "precision": v["precision"],
                        "f1": v["f1"], "documents": v["documents"]}
                    for k, v in report["by_kind"].items()},
        "by_quality": {k: {"recall": v["recall"], "precision": v["precision"],
                           "f1": v["f1"], "documents": v["documents"]}
                       for k, v in report["by_quality"].items()},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return baseline


def _print_local(report: Dict[str, Any]) -> None:
    overall = report["overall"]
    print(f"Документов: {report['documents']} "
          f"({report['seconds']} с)")
    print(f"ОБЩЕЕ: recall={overall['recall']:.3f} "
          f"precision={overall['precision']:.3f} f1={overall['f1']:.3f}")
    print("По видам:")
    for kind, value in report["by_kind"].items():
        print(f"  {kind:12s} n={value['documents']:4d} "
              f"recall={value['recall']:.3f} precision={value['precision']:.3f}")
    print("По качеству:")
    for quality, value in report["by_quality"].items():
        print(f"  {quality:10s} n={value['documents']:4d} "
              f"recall={value['recall']:.3f} precision={value['precision']:.3f}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(
        description="Стресс-прогон распознавания документов"
    )
    parser.add_argument("mode", choices=("real", "synthetic", "gigachat",
                                         "baseline", "all-local"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--source", default=str(REAL_SOURCE))
    parser.add_argument("--report", default="",
                        help="готовый отчёт для режима baseline")
    args = parser.parse_args(argv)

    if args.mode in ("synthetic", "all-local"):
        report = run_synthetic(args.limit, args.workers)
        if report:
            _print_local(report)
    if args.mode == "real":
        report = run_real(Path(args.source), args.limit or REAL_LIMIT,
                          args.workers)
        if report:
            print(f"Всего: {report['total']}, обработано: "
                  f"{report['processed_ok']}, ошибок: {report['failed']}")
            print(f"Ошибки по типам: {report['errors']}")
    if args.mode == "gigachat":
        report = run_gigachat(args.limit or GIGACHAT_MAX_REQUESTS)
        if report:
            print(f"Запросов: {report['requests']} "
                  f"(лимит {report['limit']}), успешных: {report.get('ok', 0)}")
            if report.get("stopped"):
                print(f"ОСТАНОВЛЕНО: {report['stopped']}")
    if args.mode == "baseline":
        baseline = write_baseline(
            report_path=Path(args.report) if args.report else None)
        if baseline:
            print(f"Baseline: {BASELINE_PATH}")
            print(json.dumps(baseline["overall"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
