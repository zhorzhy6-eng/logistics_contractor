"""Run the document import pipeline without the UI; never persist extracted data."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import logging
from pathlib import Path
import re
import statistics
import sys
from threading import Event, Lock
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.document_import_service import DocumentImportService  # noqa: E402
from core.gigachat_client import GigaChatClient  # noqa: E402
from core.settings_service import SettingsService  # noqa: E402


def mask_sensitive(value: str) -> str:
    value = re.sub(r"[^\W\d_]+", "[WORD]", str(value), flags=re.UNICODE)
    value = re.sub(r"\d+", "[DIGITS]", value)
    return value[:200]


def safe_error(value: str) -> str:
    """Keep only known pipeline diagnostics; suppress arbitrary exception text."""
    value = str(value)
    if re.search(r"GigaChat: HTTP \d{3}", value):
        return re.search(r"GigaChat: HTTP \d{3}", value).group()
    if "невалидный JSON" in value:
        match = re.search(r"(Expecting [^:]{1,80}|Extra data|Unterminated string|Invalid control character)", value)
        return "Модель вернула невалидный JSON" + (": " + match.group(1) if match else "")
    if "таймаут" in value.lower() or "timeout" in value.lower():
        return "Таймаут запроса"
    if "Неподдерживаемый формат файла" in value:
        return "Неподдерживаемый формат файла"
    if "Для DOC нужен LibreOffice" in value:
        return "Для DOC нужен LibreOffice"
    if "LibreOffice не смог прочитать DOC" in value:
        return "LibreOffice не смог прочитать DOC"
    if "Не удалось удалить загруженный файл" in value:
        return "Не удалось удалить загруженный файл из GigaChat"
    if "Текст распознан, но поддерживаемые поля не выделены" in value:
        return "Текст распознан, но поддерживаемые поля не выделены"
    if "Использован текстовый fallback GigaChat" in value:
        return "Использован текстовый fallback GigaChat"
    return mask_sensitive(value)


def classify(error: str) -> str:
    if "невалидный JSON" in error:
        return "невалидный JSON"
    if re.search(r"HTTP 4\d\d|\b4(?:01|03|29)\b", error):
        return "HTTP 4xx"
    if "таймаут" in error.lower() or "timeout" in error.lower():
        return "Timeout"
    if "RuntimeError" in error:
        return "RuntimeError"
    return "Другое"


class MethodProbe(logging.Handler):
    def __init__(self):
        super().__init__()
        self.methods = set()

    def emit(self, record):
        if record.name != "core.document_import_service":
            return
        message = record.getMessage()
        for method in ("GigaChat Vision", "GigaChat Текст", "OCR"):
            if "ожидание " + method in message:
                self.methods.add(method)
        if "текстовых групп=" in message:
            self.methods.add("Текст")


def summarize(rows: list[dict], inventory: list[Path]) -> str:
    n = len(rows)
    success = sum(r["groups"] >= 1 for r in rows)
    empty = sum(r["groups"] == 0 and not r["error"] for r in rows)
    errors = [r for r in rows if r["error"]]
    durations = [r["duration_sec"] for r in rows]
    pct = lambda count: f"{100 * count / n:.1f}%" if n else "0.0%"
    largest = max(rows, key=lambda r: r["duration_sec"]) if rows else None
    ext = Counter(p.suffix.lower() for p in inventory)
    lines = [
        f"Всего файлов: {n}",
        f"Общий объём: {sum(p.stat().st_size for p in inventory)} байт",
        "Расширения: " + ", ".join(f"{k or '(без расширения)'}={v}" for k, v in sorted(ext.items())),
        f"Успешно (групп ≥ 1): {success} ({pct(success)})",
        f"Пусто (групп = 0, без ошибки): {empty} ({pct(empty)})",
        f"Ошибки (RuntimeError/обработка): {len(errors)} ({pct(len(errors))})",
        f"Среднее время: {statistics.mean(durations):.2f} сек" if durations else "Среднее время: 0 сек",
        f"Медиана времени: {statistics.median(durations):.2f} сек" if durations else "Медиана времени: 0 сек",
        f"Максимум: {largest['duration_sec']:.2f} сек (файл {largest['file']})" if largest else "Максимум: 0 сек",
        "", "Топ-5 самых медленных файлов:",
    ]
    lines += [f"  {i}. {r['file']} — {r['duration_sec']:.2f} сек, групп={r['groups']}, метод={r['method']}"
              for i, r in enumerate(sorted(rows, key=lambda r: r["duration_sec"], reverse=True)[:5], 1)]
    lines += ["", "Файлы с ошибками:"]
    lines += [f"  - {r['file']}: {r['error']}" for r in errors] or ["  (нет)"]
    lines += ["", "Файлы с пустым результатом (групп=0):"]
    lines += [f"  - {r['file']}: {r['warning_text'] or 'предупреждения нет'}"
              for r in rows if r["groups"] == 0 and not r["error"]] or ["  (нет)"]
    counts = Counter(classify(r["error"]) for r in errors)
    lines += ["", "Классификация ошибок:"]
    lines += [f"  {name}: {counts[name]}" for name in
              ("невалидный JSON", "HTTP 4xx", "Timeout", "RuntimeError", "Другое")]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("before", "after"), default="after")
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--stem", help="префикс файлов в logs/ (по умолчанию — по фазе)")
    parser.add_argument("--match", help="только файлы, чей путь содержит подстроку")
    parser.add_argument("--retry-of", type=Path,
                        help="JSON прошлого прогона: взять только пустые/ошибочные файлы")
    args = parser.parse_args()
    folder = args.folder or next((p for p in ROOT.iterdir() if p.is_dir() and p.name.startswith("Документы для распозн")), None)
    if folder is None or not folder.is_dir():
        parser.error("Папка документов не найдена")
    files = sorted((p for p in folder.rglob("*") if p.is_file()), key=lambda p: str(p).casefold())
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    stem = args.stem or ("import_bench_before" if args.phase == "before" else "import_bench")
    if args.match:
        needle = args.match.casefold()
        files = [p for p in files if needle in str(p).casefold()]
    if args.retry_of:
        previous = json.loads(args.retry_of.read_text(encoding="utf-8"))
        retry = {row["file"] for row in previous
                 if not row.get("groups") or row.get("error")}
        files = [p for p in files if str(p.relative_to(folder)) in retry]
        print(f"Повторная попытка: {len(files)} файлов из {args.retry_of}", flush=True)
    if not files:
        parser.error("Под фильтры не попал ни один файл")
    inventory = [f"{p.relative_to(folder)}\t{p.stat().st_size} байт" for p in files]
    (logs / f"{stem}_inventory.txt").write_text("\n".join(inventory) + "\n", encoding="utf-8")
    handler = logging.FileHandler(logs / f"{stem}_app.log", encoding="utf-8", mode="w")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    settings = SettingsService(str(ROOT / "config" / "settings.json"))
    config = settings.as_dict()
    client = None
    if config.get("document_cloud_enabled") is True:
        client = GigaChatClient(
            scope=settings.get_str("gigachat_scope", "GIGACHAT_API_PERS"),
            model=settings.get_str("gigachat_model", "GigaChat-2"),
            timeout=settings.get_int("gigachat_timeout", 150),
            verify_ssl=settings.get_bool("gigachat_verify_ssl", True),
            ca_bundle=settings.get_str("gigachat_ca_bundle", "") or None,
        )
        original_recognize = client.recognize_image
        call_lock = Lock()
        last_call = [0.0]
        def paced_recognize(*args, **kwargs):
            with call_lock:
                delay = 1.0 - (time.monotonic() - last_call[0])
                if delay > 0:
                    time.sleep(delay)
                last_call[0] = time.monotonic()
            return original_recognize(*args, **kwargs)
        client.recognize_image = paced_recognize
    service = DocumentImportService(config, client)
    rows = []
    for index, path in enumerate(files, 1):
        logging.info("Bench: файл %d/%d | %s", index, len(files), path.relative_to(folder))
        evidence, warnings, methods = [], [], set()
        probe = MethodProbe()
        logging.getLogger("core.document_import_service").addHandler(probe)
        def on_result(source, items, warning):
            evidence.extend(items)
            methods.update(item.method for item in items)
            if warning:
                warnings.append(warning)
        started = time.perf_counter()
        failure = None
        try:
            service.process([str(path)], Event(), on_result, lambda *_: None)
        except Exception as exc:
            failure = f"{type(exc).__name__}: {safe_error(str(exc))}"
            frames = traceback.extract_tb(exc.__traceback__)
            safe_frames = " -> ".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in frames)
            logging.error("Bench: exception at file index %d | %s | %s", index,
                          type(exc).__name__, safe_frames)
        finally:
            logging.getLogger("core.document_import_service").removeHandler(probe)
        elapsed = round(time.perf_counter() - started, 2)
        # Cleanup failures are reported in warning_text but do not make the
        # recognition itself an error.
        errors = [w for w in warnings
                  if re.search(r"невалидный JSON|HTTP \d{3}|ошибка|таймаут|"
                               r"неподдерживаемый формат|нужен LibreOffice|"
                               r"не смог прочитать|Не удалось", w, re.I)
                  and "Не удалось удалить загруженный файл" not in w]
        error = failure or ("RuntimeError: " + safe_error(errors[0]) if errors else None)
        method = ", ".join(sorted(methods | probe.methods)) or "не определён"
        row = {"file": str(path.relative_to(folder)), "size_kb": round(path.stat().st_size / 1024, 1),
               "ext": path.suffix.lower(), "duration_sec": elapsed, "method": method,
               "groups": len(evidence), "warning": bool(warnings), "error": error,
               "fields_count": sum(len(item.values) for item in evidence),
               "warning_text": safe_error(" | ".join(warnings)) if warnings else ""}
        rows.append(row)
        (logs / f"{stem}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{index}/{len(files)} {elapsed:.1f}s groups={len(evidence)} error={bool(error)}", flush=True)
        if client and index < len(files):
            time.sleep(1)
    summary = summarize(rows, files)
    (logs / f"{stem}_summary.txt").write_text(summary, encoding="utf-8")
    console_encoding = sys.stdout.encoding or "utf-8"
    print(summary.encode(console_encoding, errors="replace").decode(console_encoding))


if __name__ == "__main__":
    main()
