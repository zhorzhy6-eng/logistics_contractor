"""Import evidence, conservative entity matching, and explicit field approval."""
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from pathlib import Path
import json
import logging
import re
import traceback

from core.import_cancel import ImportCancelled, check_cancel
from core.document_reader import read_document
from core.recognizer import DataMapper

logger = logging.getLogger(__name__)


def _safe_traceback(exc):
    """All stack frames, without exception values or document paths."""
    return " -> ".join(
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
        for frame in traceback.extract_tb(exc.__traceback__)
    )

TITLES = dict(carrier="Перевозчик", customer="Заказчик", driver="Водитель",
              tractor="Тягач", trailer="Полуприцеп", vehicles="Перевозимые авто", contract="Договор")
LABELS = {
    "full_name": "Наименование / ФИО", "short_name": "Краткое наименование",
    "inn": "ИНН", "kpp": "КПП", "ogrn": "ОГРН", "carrier_type": "Тип перевозчика",
    "legal_address": "Юридический адрес", "actual_address": "Фактический адрес",
    "bank_account": "Расчётный счёт", "correspondent_account": "Корреспондентский счёт",
    "bik": "БИК", "bank_name": "Банк", "director_name": "Руководитель",
    "director_position": "Должность", "phone": "Телефон", "email": "Электронная почта",
    "birth_date": "Дата рождения", "birth_place": "Место рождения",
    "passport_series": "Серия паспорта", "passport_number": "Номер паспорта",
    "passport_issue_date": "Дата выдачи паспорта", "passport_issuer": "Кем выдан паспорт",
    "passport_code": "Код подразделения", "registration_address": "Адрес регистрации",
    "license_series": "Серия ВУ", "license_number": "Номер ВУ", "license_date": "Дата лицензии",
    "license_issue_date": "Дата выдачи ВУ", "license_expiry_date": "ВУ действует до",
    "license_categories": "Категории ВУ", "vin": "VIN", "brand_model": "Марка / модель",
    "plate_number": "Госномер", "year": "Год выпуска", "color": "Цвет",
    "vehicle_type": "Тип ТС", "number": "Номер договора", "date": "Дата договора",
    "route": "Маршрут", "payment_days": "Срок оплаты (дней)",
    "special_conditions": "Особые условия",
    "price_input": "Стоимость (по выбранному режиму НДС)", "vat_rate": "НДС (%)",
    "loading_plan_date": "Плановая дата погрузки", "unloading_plan_date": "Плановая дата выгрузки",
}
SCHEMA = {
    "driver": list(DataMapper.DRIVER_FIELDS),
    "carrier": DataMapper.ORGANIZATION_FIELDS + ["carrier_type", "vat_rate"] + DataMapper.CARRIER_EXTRA_FIELDS,
    "customer": DataMapper.ORGANIZATION_FIELDS,
    "vehicles": ["vin", "brand_model", "plate_number", "year", "color", "vehicle_type"],
    "tractor": ["brand_model", "plate_number", "year", "color"],
    "trailer": ["brand_model", "plate_number", "year", "color"],
    "contract": ["number", "date", "route", "payment_days", "special_conditions",
                 "price_input", "vat_rate", "loading_plan_date", "unloading_plan_date"],
}


def normalized(value):
    return " ".join(str(value or "").split()).casefold()


def canonical_value(key, value):
    """Normalize formatting only; never repair or complete document characters."""
    value = str(value or "").strip()
    if "date" in key:
        from core.dates import to_iso
        bare = re.sub(r"\s*г\.?$", "", value, flags=re.I).strip()
        return to_iso(bare)
    return value


def valid_value(key, value):
    """Reject uncertainty markers and malformed identifiers; never repair digits."""
    value = str(value or "").strip()
    if not value or value.lower() in {"null", "none", "unknown"} or re.search(r"[?*�]", value):
        return False
    sizes = {"inn": (10, 12), "kpp": (9,), "ogrn": (13, 15), "bik": (9,),
             "bank_account": (20,), "correspondent_account": (20,),
             "passport_series": (4,), "passport_number": (6,)}
    if key in sizes:
        digits = value.replace(" ", "")
        return bool(re.fullmatch(r"[0-9]+", digits)) and len(digits) in sizes[key]
    if key == "vin":
        return bool(re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", value))
    if key == "passport_code":
        return bool(re.fullmatch(r"[0-9]{3}[-–][0-9]{3}", value))
    if "date" in key:
        bare = re.sub(r"\s*г\.?$", "", value, flags=re.I).strip()
        return bool(re.fullmatch(r"(?:[0-9]{4}[-./][0-9]{1,2}[-./][0-9]{1,2}|[0-9]{1,2}[-./][0-9]{1,2}[-./][0-9]{4}|[0-9]{8})", bare)) and bool(canonical_value(key, value))
    if key == "year":
        return value.isdigit() and 1886 <= int(value) <= 2100
    if key == "payment_days":
        return value.isdigit() and 0 <= int(value) <= 3650
    if key in {"price_input", "vat_rate"}:
        return bool(re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value)) and float(value) <= (100 if key == "vat_rate" else 100000000)
    if key == "carrier_type":
        return value in {"ООО (с НДС)", "ИП с НДС", "ИП без НДС"}
    if key == "vehicle_type":
        return value in {"Легковой автомобиль", "Тягач", "Прицеп", "Фургон", "Автобус"}
    return True


def extract_local_fields(text):
    """Label-based extraction only. No synthetic dates, transliteration or digit fixes."""
    from core.document_fields import extract_document_fields
    structured = extract_document_fields(text)
    if structured is not None:
        return structured
    result, section, current = {}, "carrier", {}

    def flush():
        nonlocal current
        if current:
            result.setdefault(section, []).append(current)
        current = {}

    aliases = {normalized(v): k for k, v in LABELS.items()}
    aliases.update({"фио": "full_name", "наименование": "full_name", "инн": "inn",
        "р/с": "bank_account", "к/с": "correspondent_account", "vin": "vin",
        "марка": "brand_model", "модель": "brand_model", "гос. номер": "plate_number",
        "госномер": "plate_number", "расчетный счет": "bank_account",
        "full name": "full_name", "date of birth": "birth_date",
        "vehicle identification number": "vin", "registration number": "plate_number"})
    headings = {normalized(v): k for k, v in TITLES.items()}
    headings.update({"автомобиль": "vehicles", "транспортное средство": "vehicles"})
    lines = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 1 and len(cells) % 2 == 0:
            lines.extend(cells[i] + ": " + cells[i+1] for i in range(0, len(cells), 2))
        else:
            lines.append(line)
    for line in lines:
        line = line.strip()
        heading = normalized(line.rstrip(":"))
        if heading in headings:
            flush()
            section = headings[heading]
            continue
        parts = re.split(r"\s*[:|]\s*", line, maxsplit=1)
        if len(parts) != 2:
            match = re.match("(" + "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)) + r")\s+(.+)$", line, re.IGNORECASE)
            if not match:
                continue
            parts = [match.group(1), match.group(2)]
        key = aliases.get(normalized(parts[0]))
        value = parts[1].strip()
        if not key or not value:
            continue
        inferred = section
        if key == "vin":
            inferred = "vehicles"
        elif key.startswith("passport_") or key in {"birth_date", "birth_place"} or normalized(parts[0]) == "фио":
            inferred = "driver"
        if inferred != section:
            flush()
            section = inferred
        if key in SCHEMA[section]:
            if key in current:
                flush()
            current[key] = value
    flush()
    # Unlabelled complete VINs remain separate vehicles, never joined by list order.
    known = {v.get("vin") for v in result.get("vehicles", [])}
    for vin in DataMapper.VIN_PATTERN.findall(text):
        if vin not in known:
            result.setdefault("vehicles", []).append({"vin": vin})
            known.add(vin)
    return result


def identities(section, data):
    keys = {"driver": ("full_name", "birth_date"), "carrier": ("inn", "full_name"),
            "customer": ("inn", "full_name"), "vehicles": ("vin", "plate_number"),
            "tractor": ("plate_number", "_vin"), "trailer": ("plate_number", "_vin"),
            "contract": ("number",)}[section]
    return {k: normalized(data[k]) for k in keys if valid_value("vin" if k == "_vin" else k, data.get(k))}


def same_entity(section, a, b):
    a, b = identities(section, a), identities(section, b)
    common = a.keys() & b.keys()
    if section == "driver" and "full_name" not in common:
        return False
    return bool(common) and all(a[k] == b[k] for k in common)


@dataclass
class Evidence:
    section: str
    values: dict
    source: str
    method: str
    uncertain: bool = False
    document_kind: str = ""
    note: str = ""


@dataclass
class FieldResult:
    group: int
    section: str
    key: str
    value: str
    sources: str
    state: str
    identity: dict = field(default_factory=dict)


def compare_fields(evidence):
    groups = []
    for item in evidence:
        matches = [g for g in groups if g[0].section == item.section
                   and all((item.document_kind and item.document_kind == x.document_kind
                            and item.source == x.source)
                           or same_entity(item.section, x.values, item.values) for x in g)]
        if len(matches) == 1:
            matches[0].append(item)
        else:
            groups.append([item])
    rows = []
    for index, group in enumerate(groups):
        section = group[0].section
        identity = {}
        for item in group:
            identity.update(identities(section, item.values))
        for key in SCHEMA[section]:
            entries = [(item, str(item.values.get(key, "") or "").strip()) for item in group]
            filled = [(item, value) for item, value in entries if value]
            good = [(item, value) for item, value in filled if valid_value(key, value)]
            variants = {normalized(value) for _, value in filled}
            methods = {item.method for item, _ in good}
            if not good:
                state, value = "не прочитано", ""
            elif len(variants) > 1 or len(good) != len(filled):
                state, value = "расхождение", ""
            elif len(methods) >= 2:
                state = "совпало (проверьте OCR)" if any(item.uncertain for item, _ in good) else "совпало"
                value = good[0][1]
            else:
                state, value = "требует проверки", good[0][1]
            sources = "\n".join(f"{item.source} · {item.method}: {v or '—'}" +
                                ("\n" + item.note if item.note else "") for item, v in entries)
            rows.append(FieldResult(index, section, key, value, sources, state, identity))
    # Put useful candidates first, then disagreements, with unread fields last.
    return sorted(rows, key=lambda row: (0 if row.value else 1 if row.state == "расхождение" else 2, row.group))


class DocumentImportService:
    # Глубокий резерв (транскрипция + текстовая модель) ограничен на файл:
    # иначе многостраничные сканы тратят на каждую пустую страницу цепочку
    # из нескольких запросов и упираются в лимиты GigaChat.
    MAX_DEEP_PAGES_PER_FILE = 2

    def __init__(self, settings=None, client=None):
        self.settings = settings or {}
        self.client = client

    def process(self, paths, cancel, on_result, on_text=None):
        logger.info("Импорт документов: начат, файлов=%s", len(paths))
        # Один раз сообщаем, какой OCR выбран: облако — GigaChat Vision,
        # иначе — локальный Tesseract. Ключ, изображения и текст не логируются.
        cloud_enabled = self.settings.get("document_cloud_enabled") is True
        if cloud_enabled and self.client is not None:
            logger.info(
                "Импорт документов: облачный OCR включён (model=%s)",
                self.settings.get("gigachat_model", ""),
            )
        else:
            if cloud_enabled:
                logger.warning(
                    "Облако включено, но GigaChat недоступен, используется локальный OCR"
                )
            logger.info(
                "Импорт документов: используется локальный OCR (cloud_enabled=%s, client=%s)",
                self.settings.get("document_cloud_enabled"), self.client is not None,
            )
        # The coordinator is a QThreadPool runnable; these two workers overlap OCR/network.
        with ThreadPoolExecutor(max_workers=2) as workers:
            for file_index, path in enumerate(paths, 1):
                if cancel.is_set():
                    logger.info("Импорт документов: остановлен перед файлом %s", file_index)
                    break
                try:
                    logger.info("Импорт документов: чтение файла %s/%s", file_index, len(paths))
                    state = {"produced": False, "deep_used": 0}
                    for page in read_document(path, cancel, self.settings.get("document_soffice", "")):
                        check_cancel(cancel)
                        logger.info("Импорт документов: файл=%s, страница=%s, чтение завершено", file_index, page.number)
                        source = page.label or f"{Path(path).name}, стр. {page.number}"
                        produced = self._process_page(page, source, file_index, cancel,
                                                      on_result, on_text, workers,
                                                      cloud_enabled, state)
                        state["produced"] = state["produced"] or produced
                except ImportCancelled:
                    logger.info("Импорт документов: чтение отменено, файл=%s", file_index)
                    break
                except Exception as exc:
                    logger.error("Импорт документов: ошибка чтения файла %s (%s); стек: %s",
                                 file_index, type(exc).__name__, _safe_traceback(exc))
                    message = str(exc) if type(exc) is RuntimeError else "Не удалось прочитать файл: повреждение, пароль или нет доступа."
                    on_result(Path(path).name, [], message)
        logger.info("Импорт документов: обработка завершена, отменено=%s", cancel.is_set())

    def _process_page(self, page, source, file_index, cancel,
                      on_result, on_text, workers, cloud_enabled, state):
        """One page may yield text evidence and image evidence at once."""
        deep_allowed = (not state["produced"]
                        and state["deep_used"] < self.MAX_DEEP_PAGES_PER_FILE)
        fallback_attempted = False
        local_evidence = []
        if page.text and page.text.strip():
            if on_text is not None:
                on_text(source, [page.text])
            local_evidence = self._evidence(
                extract_local_fields(page.text), source, "Текст")
            if local_evidence:
                logger.info("Импорт документов: файл=%s, страница=%s, текстовых групп=%s",
                            file_index, page.number, len(local_evidence))
                on_result(source, local_evidence, "")

        jobs = {}
        if page.image is not None:
            # Единственный облачный OCR — GigaChat Vision. Он уже настроен.
            if cloud_enabled and self.client is not None:
                jobs["GigaChat Vision"] = workers.submit(
                    self.client.recognize_image, page.image, cancel, deep_allowed)
                fallback_attempted = deep_allowed
            else:
                # Fallback: локальный Tesseract, если облако выключено или ключ недоступен.
                from core.document_ocr import recognize_image as local_ocr
                jobs["OCR"] = workers.submit(local_ocr, page.image, cancel)
        elif (not local_evidence and cloud_enabled and self.client is not None
              and deep_allowed and not cancel.is_set()):
            # Читаемый текст не подошёл под локальные правила — отдаём его
            # текстовой модели (дешевле и точнее, чем Vision для текста).
            jobs["GigaChat Текст"] = workers.submit(self.client.recognize_text, page.text)
            fallback_attempted = True

        produced = bool(local_evidence)
        for method, future in jobs.items():
            produced = self._execute_job(method, future, page, source, file_index,
                                         cancel, on_result, on_text) or produced

        # Последняя попытка для текстовой страницы: рендерим её и отдаём Vision.
        if (not produced and page.image is None and cloud_enabled
                and self.client is not None and deep_allowed and not cancel.is_set()):
            image = page.load_image()
            if image is not None:
                # Текст уже не помог — повторяем только структурированный запрос
                # по картинке, без глубокой цепочки транскрипции.
                future = workers.submit(self.client.recognize_image, image, cancel, False)
                fallback_attempted = True
                produced = self._execute_job("GigaChat Vision", future, page, source,
                                             file_index, cancel, on_result, on_text) or produced
        if fallback_attempted and not produced:
            state["deep_used"] += 1
        return produced

    def _execute_job(self, method, future, page, source, file_index,
                     cancel, on_result, on_text):
        logger.info("Импорт документов: файл=%s, страница=%s, ожидание %s",
                    file_index, page.number, method)
        try:
            data = future.result()
        except ImportCancelled:
            logger.info("Импорт документов: метод=%s отменён", method)
            return False
        except Exception as exc:
            logger.error("Импорт документов: ошибка метода %s (%s); стек: %s",
                         method, type(exc).__name__, _safe_traceback(exc))
            # Never expose network bodies, file contents, or raw parser exceptions.
            message = str(exc) if type(exc) is RuntimeError else \
                "Не удалось обработать документ. Проверьте формат и зависимости."
            on_result(source, [], method + ": " + message)
            return False

        warnings, evidence = "", []
        if method == "GigaChat Vision":
            # GigaChat возвращает структурированный JSON
            # вместе с предупреждением об очистке хранилища.
            data, warnings = data
            if isinstance(data, dict) and "_raw_text" in data:
                raw_text = data["_raw_text"]
                evidence = self._evidence(
                    extract_local_fields(page.text + "\n" + raw_text), source, method)
                if on_text is not None and not cancel.is_set():
                    on_text(source, [raw_text])
                warnings = " ".join(filter(None, [
                    warnings, "Использован текстовый fallback GigaChat."]))
            else:
                evidence = self._evidence(data, source, method)
                if on_text is not None and not cancel.is_set():
                    on_text(source, [json.dumps(data, ensure_ascii=False, indent=2)])
        elif method == "GigaChat Текст":
            evidence = self._evidence(data, source, method)
            if on_text is not None and not cancel.is_set():
                on_text(source, [json.dumps(data, ensure_ascii=False, indent=2)])
        else:
            # Локальный OCR возвращает строку текста — парсим как раньше.
            text = data
            if on_text is not None and not cancel.is_set():
                on_text(source, [text])
            evidence = self._evidence(
                extract_local_fields(page.text + "\n" + text), source, method)
        if not evidence and not warnings:
            warnings = ("Текст распознан, но поддерживаемые поля не выделены. "
                        "Документ требует ручной проверки.")
        if cancel.is_set():
            logger.info("Импорт документов: результат %s пропущен из-за отмены", method)
            if warnings:
                on_result(source, [], warnings)
            return False
        logger.info("Импорт документов: файл=%s, страница=%s, метод=%s, групп=%s, предупреждение=%s",
                    file_index, page.number, method, len(evidence), bool(warnings))
        on_result(source, evidence, warnings)
        return bool(evidence)

    @staticmethod
    def _evidence(data, source, method):
        if not isinstance(data, dict):
            raise RuntimeError("Ожидался структурированный ответ.")
        result = []
        for section in SCHEMA:
            values = data.get(section, [])
            if isinstance(values, dict):
                values = [values]
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, dict):
                    continue
                clean = {k: str(v).strip() for k, v in value.items()
                         if (k in SCHEMA[section] or k == "_vin") and isinstance(v, (str, int, float)) and v}
                if clean:
                    result.append(Evidence(section, clean, source, method, method == "OCR",
                                           str(value.get("_kind", "")) if method in {"OCR", "Текст"} else "",
                                           str(value.get("_note", ""))))
        return result
