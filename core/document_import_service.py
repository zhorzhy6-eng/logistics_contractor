"""Import evidence, conservative entity matching, and explicit field approval."""
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from pathlib import Path
import json
import logging
import re
import traceback

from core.import_cancel import ImportCancelled, check_cancel, interruptible_sleep
from core.document_reader import read_document
from core.recognizer import DataMapper

logger = logging.getLogger(__name__)

# ── Имена методов распознавания ────────────────────────────────────────
# Это одновременно и признак пути для оператора, и ключ ветвлений ниже:
# строки методов попадают в дерево импорта, в подсказки и в лог.
VISION_METHOD = "GigaChat Vision"
TEXT_METHOD = "GigaChat Текст"
LOCAL_TEXT_METHOD = "Текст"
LOCAL_OCR_METHOD = "OCR"
#: Локальный Tesseract ПОСЛЕ отказа GigaChat: отличается от обычного «OCR»,
#: иначе оператор не увидит, что облако не сработало.
LOCAL_OCR_FALLBACK_METHOD = "OCR локально"
LOCAL_OCR_NOTE = "GigaChat недоступен, распознано локально"
LOCAL_OCR_LIMIT_NOTE = "GigaChat вернул 429 (лимит запросов), распознано локально"
LOCAL_OCR_FAILED_NOTE = "GigaChat Vision не сработал, распознано локально"
VISION_METHODS = (VISION_METHOD,)
TEXT_MODEL_METHODS = (TEXT_METHOD,)
LOCAL_OCR_METHODS = (LOCAL_OCR_METHOD, LOCAL_OCR_FALLBACK_METHOD)
STRUCTURED_TEXT_METHODS = (LOCAL_OCR_METHOD, LOCAL_TEXT_METHOD)

# ── Почему изображение читал локальный OCR ─────────────────────────────
# Одной пометки «OCR локально» мало: оператор должен различать «облако
# выключено», «облако недоступно» и «облако отказало». Код причины —
# машинный (в дерево и в лог идёт только он), текст собирает UI.
CLOUD_DISABLED = "cloud_disabled"          # оператор снял галочку облака
CLOUD_CLIENT_MISSING = "client_missing"    # клиента нет, ключа нет, ответ 401
CLOUD_RATE_LIMIT = "rate_limit"            # GigaChat ответил 429
CLOUD_VISION_FAILED = "vision_failed"      # Vision упал по другой причине

#: Короткая приписка к пометке в дереве: «OCR локально (облако выключено)».
LOCAL_OCR_REASON_LABELS = {
    CLOUD_DISABLED: "облако выключено",
    CLOUD_CLIENT_MISSING: "GigaChat недоступен",
    CLOUD_RATE_LIMIT: "GigaChat вернул 429",
    CLOUD_VISION_FAILED: "Vision не сработал",
}
#: Развёрнутая причина для подсказки (tooltip) строки источника.
LOCAL_OCR_REASON_HINTS = {
    CLOUD_DISABLED: "Tesseract, русский + английский. Облако выключено в настройках.",
    CLOUD_CLIENT_MISSING: "Tesseract, русский + английский. GigaChat недоступен: нет ключа.",
    CLOUD_RATE_LIMIT: "Tesseract, русский + английский. GigaChat вернул 429 (лимит).",
    CLOUD_VISION_FAILED: "Tesseract, русский + английский. GigaChat вернул ошибку.",
}
#: Примечание к полю (`Evidence.note`) для каждой причины.
LOCAL_OCR_REASON_NOTES = {
    CLOUD_DISABLED: "",
    CLOUD_CLIENT_MISSING: LOCAL_OCR_NOTE,
    CLOUD_RATE_LIMIT: LOCAL_OCR_LIMIT_NOTE,
    CLOUD_VISION_FAILED: LOCAL_OCR_FAILED_NOTE,
}


def local_ocr_label(reason):
    """Пометка локального пути: «OCR локально (облако выключено)»."""
    suffix = LOCAL_OCR_REASON_LABELS.get(str(reason or ""), "")
    return f"{LOCAL_OCR_FALLBACK_METHOD} ({suffix})" if suffix else LOCAL_OCR_FALLBACK_METHOD


def local_ocr_hint(reason):
    """Подсказка локального пути: чем читали и почему не облаком."""
    return LOCAL_OCR_REASON_HINTS.get(str(reason or ""), "")


def vision_failure_reason(exc):
    """Причина отказа Vision по тексту ошибки: 429, 401 или прочая ошибка."""
    text = str(exc)
    if "429" in text:
        return CLOUD_RATE_LIMIT
    if "401" in text:
        # 401 — ключ отозван или неверен: клиент в этом запуске бесполезен.
        return CLOUD_CLIENT_MISSING
    return CLOUD_VISION_FAILED


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
class CloudOcr:
    """Итог выбора облачного OCR на один запуск импорта.

    Решение принимается ОДИН раз (в `process`) и передаётся в страницы: иначе
    на каждой странице пришлось бы заново гадать, доступен ли GigaChat.
    """

    enabled: bool                                    # будут ходить в Vision
    used: bool = False                               # Vision уже подтвердил работу
    rate_limited: bool = False                       # Vision ответил 429
    user_disabled: bool = False                      # оператор снял галочку облака
    reason: str = ""                                 # код: почему локальный путь
    note: str = ""                                   # что сказать оператору
    failed: str = ""                                 # код: почему отказал Vision


@dataclass
class Evidence:
    section: str
    values: dict
    source: str
    method: str
    uncertain: bool = False
    document_kind: str = ""
    note: str = ""
    #: Почему значение прочитано локально (`CLOUD_*`), если это был резерв:
    #: пусто — обычный путь. По этому коду дерево строит пометку и подсказку.
    local_reason: str = ""


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

    #: Пауза при ответе 429 (превышен лимит запросов) перед повтором Vision.
    RATE_LIMIT_PAUSE_SECONDS = 30.0

    def __init__(self, settings=None, client=None):
        self.settings = settings or {}
        self.client = client

    def process(self, paths, cancel, on_result, on_text=None):
        logger.info("Импорт документов: начат, файлов=%s", len(paths))
        cloud = self._cloud_state()
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
                                                      cloud, state)
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

    def _cloud_state(self):
        """Кто распознаёт картинки в этом запуске: GigaChat Vision или Tesseract.

        Ключ, изображения и текст в лог не попадают — только факт выбора пути.
        Отдельно различаются два случая: оператор САМ выключил облако и облако
        включено, но GigaChat недоступен. Во втором документ прочитан резервным
        путём, и оператор об этом узнаёт. Код причины (`CloudOcr.reason`)
        доходит до дерева: «облако выключено» и «облако отказало» — разные
        пометки, а не одна.
        """
        key = "document_cloud_enabled"
        enabled = self.settings.get(key) is True
        vision_model = getattr(self.client, "vision_model", "") or self.settings.get("gigachat_model", "")
        if enabled and self.client is not None:
            logger.info("Импорт документов: облачный OCR включён (model=%s)", vision_model)
            return CloudOcr(True)
        if enabled:
            # Ключ есть, а клиента нет: GigaChat в этом запуске недоступен.
            logger.warning("Импорт документов: облако включено, но GigaChat недоступен, "
                           "используется локальный OCR (Tesseract)")
            return CloudOcr(False, reason=CLOUD_CLIENT_MISSING, note=LOCAL_OCR_NOTE)
        logger.info("Импорт документов: оператор выключил облако, "
                    "используется локальный OCR (Tesseract)")
        return CloudOcr(False, user_disabled=True, reason=CLOUD_DISABLED)

    def _process_page(self, page, source, file_index, cancel,
                      on_result, on_text, workers, cloud, state):
        """One page may yield text evidence and image evidence at once."""
        deep_allowed = (not state["produced"]
                        and state["deep_used"] < self.MAX_DEEP_PAGES_PER_FILE)
        fallback_attempted = False
        local_evidence = []
        if page.text and page.text.strip():
            if on_text is not None:
                on_text(source, [page.text])
            local_evidence = self._evidence(
                extract_local_fields(page.text), source, LOCAL_TEXT_METHOD)
            if local_evidence:
                logger.info("Импорт документов: файл=%s, страница=%s, текстовых групп=%s",
                            file_index, page.number, len(local_evidence))
                on_result(source, local_evidence, "")

        jobs = []
        produced = bool(local_evidence)
        if is_image_page(page):
            # Фото и сканы: ПЕРВЫЙ путь — GigaChat Vision (на «плохих» снимках
            # он даёт в 4–8 раз больше полей, чем Tesseract), Tesseract —
            # резерв, когда облако недоступно или не ответило.
            produced = self._run_image(page, source, file_index, cancel, on_result,
                                       on_text, workers, cloud) or produced
            # Резервных попыток у изображения нет: бюджет резерва считает
            # только текстовые страницы, которые ходят в облако повторно.
        elif (not local_evidence and cloud.enabled
              and deep_allowed and not cancel.is_set()):
            # Читаемый текст не подошёл под локальные правила — отдаём его
            # текстовой модели (дешевле и точнее, чем Vision для текста).
            # cancel передаётся и сюда: паузы перед повторами при 429 внутри
            # клиента тоже должны прерываться кнопкой «Отменить».
            jobs.append((TEXT_METHOD, workers.submit(self.client.recognize_text,
                                                     page.text, cancel=cancel)))
            fallback_attempted = True

        for method, future in jobs:
            produced = self._run_job(method, future, page, source, file_index,
                                     cancel, on_result, on_text) or produced

        # Последняя попытка для текстовой страницы: рендерим её и отдаём Vision.
        if (not produced and page.image is None and cloud.enabled
                and deep_allowed and not cancel.is_set()):
            image = page.load_image()
            if image is not None:
                # Текст уже не помог — повторяем только структурированный запрос
                # по картинке, без глубокой цепочки транскрипции.
                future = workers.submit(self.client.recognize_image, image, cancel, False)
                fallback_attempted = True
                produced = self._run_job(VISION_METHOD, future, page, source,
                                         file_index, cancel, on_result, on_text,
                                         cloud=cloud) or produced
        if fallback_attempted and not produced:
            state["deep_used"] += 1
        return produced

    @staticmethod
    def _local_ocr():
        # Импорт внутри функции: Tesseract нужен только на локальном пути.
        from core.document_ocr import recognize_image as local_ocr
        return local_ocr

    def _run_image(self, page, source, file_index, cancel, on_result, on_text,
                   workers, cloud):
        """Изображение: GigaChat Vision первым, локальный Tesseract — резервом.

        Глубокая цепочка Vision (транскрипция, усиление скана, текстовая
        модель) включена всегда: именно она вытягивает «плохие» фото, ради
        которых Vision и стал основным путём распознавания изображений.
        """
        if not cloud.enabled:
            # Облака нет по решению оператора или из-за недоступности ключа:
            # метод, причина и примечание говорят, что именно произошло.
            method = LOCAL_OCR_METHOD if cloud.user_disabled else LOCAL_OCR_FALLBACK_METHOD
            return self._run_job(method, workers.submit(self._local_ocr(), page.image, cancel),
                                 page, source, file_index, cancel, on_result, on_text,
                                 note=cloud.note, local_reason=cloud.reason)
        cloud.failed = ""          # причина относится к ЭТОМУ кадру, не к прошлому
        produced = self._run_job(
            VISION_METHOD,
            workers.submit(self.client.recognize_image, page.image, cancel, True),
            page, source, file_index, cancel, on_result, on_text, cloud=cloud)
        if produced or cancel.is_set():
            return produced
        # Резерв после отказа Vision: тот же кадр читает локальный Tesseract.
        # Причина отказа берётся от самого Vision (429, 401, прочая ошибка),
        # а не угадывается: от неё зависит пометка в дереве. Пусто — значит
        # Vision ответил, но полей не нашёл.
        reason = cloud.failed or CLOUD_VISION_FAILED
        logger.warning("Импорт документов: %s, переключение на Tesseract",
                       LOCAL_OCR_REASON_HINTS.get(reason, reason))
        note = LOCAL_OCR_REASON_NOTES.get(reason, LOCAL_OCR_NOTE)
        try:
            text = self._local_ocr()(page.image, cancel)
        except ImportCancelled:
            raise
        except Exception as exc:
            logger.error("Импорт документов: локальный OCR не удался (%s); стек: %s",
                         type(exc).__name__, _safe_traceback(exc))
            on_result(source, [], "Локальный OCR (Tesseract) также не смог прочитать "
                                  "изображение. Проверьте оригинал.")
            return False
        logger.info("Импорт документов: файл=%s, страница=%s, резервный путь=%s (причина=%s)",
                    file_index, page.number, LOCAL_OCR_FALLBACK_METHOD, reason)
        return self._run_job(LOCAL_OCR_FALLBACK_METHOD, _ReadyFuture(text),
                             page, source, file_index, cancel, on_result, on_text,
                             note=note, local_reason=reason)

    def _vision_retry(self, exc, cancel, cloud, page):
        """Повтор Vision после 429: пауза 30 секунд, один раз за импорт.

        Возвращает ready-future для повтора или None, если повторять нечего.
        Повтор идёт БЕЗ глубокой цепочки: лимит запросов уже исчерпан, лишние
        обращения только продлят ограничение.
        """
        if cloud.rate_limited or not _is_rate_limit_error(exc):
            return None
        cloud.rate_limited = True
        pause = self.RATE_LIMIT_PAUSE_SECONDS
        logger.warning("Импорт документов: GigaChat Vision вернул 429 (лимит запросов), "
                       "повтор через %.0f сек", pause)
        if cancel.is_set():
            return None
        # Пауза прерываемая: ждём короткими шагами, чтобы кнопка «Отменить»
        # не ждала все 30 секунд (раньше здесь был time.sleep одним куском).
        try:
            interruptible_sleep(pause, cancel)
        except ImportCancelled:
            logger.info("Импорт документов: пауза 429 прервана пользователем")
            raise
        if cancel.is_set():
            return None
        logger.info("Импорт документов: повтор GigaChat Vision после 429")
        return _submit_once(self.client.recognize_image, page.image, cancel, False)

    def _run_job(self, method, future, page, source, file_index,
                 cancel, on_result, on_text, note="", cloud=None, local_reason=""):
        logger.info("Импорт документов: файл=%s, страница=%s, ожидание %s",
                    file_index, page.number, method)
        try:
            data = future.result()
        except ImportCancelled:
            logger.info("Импорт документов: метод=%s отменён", method)
            return False
        except Exception as exc:
            self._report_job_failure(method, exc, source, file_index, page, on_result)
            if method not in VISION_METHODS or cloud is None:
                return False
            # Почему Vision не отдал данные: от этого зависит пометка в дереве
            # («GigaChat вернул 429», «GigaChat недоступен» или «Vision не сработал»).
            cloud.failed = vision_failure_reason(exc)
            # 429: ждём и пробуем ещё раз, прежде чем уходить в локальный OCR.
            repeated = self._vision_retry(exc, cancel, cloud, page)
            if repeated is None:
                return False
            return self._run_job(VISION_METHOD, repeated, page, source, file_index,
                                 cancel, on_result, on_text, cloud=cloud)

        warnings, evidence = "", []
        if method in VISION_METHODS:
            # GigaChat возвращает структурированный JSON
            # вместе с предупреждением об очистке хранилища.
            data, warnings = data
            if isinstance(data, dict) and "_raw_text" in data:
                raw_text = data["_raw_text"]
                evidence = self._evidence(
                    extract_local_fields(page.text + "\n" + raw_text), source, method,
                    note=note, local_reason=local_reason)
                if on_text is not None and not cancel.is_set():
                    on_text(source, [raw_text])
                warnings = " ".join(filter(None, [
                    warnings, "Использован текстовый fallback GigaChat."]))
            else:
                evidence = self._evidence(data, source, method, note=note,
                                          local_reason=local_reason)
                if on_text is not None and not cancel.is_set():
                    on_text(source, [json.dumps(data, ensure_ascii=False, indent=2)])
        elif method in TEXT_MODEL_METHODS:
            evidence = self._evidence(data, source, method, note=note,
                                      local_reason=local_reason)
            if on_text is not None and not cancel.is_set():
                on_text(source, [json.dumps(data, ensure_ascii=False, indent=2)])
        else:
            # Локальный OCR возвращает строку текста — парсим как раньше.
            text = data
            if on_text is not None and not cancel.is_set():
                on_text(source, [text])
            evidence = self._evidence(
                extract_local_fields(page.text + "\n" + text), source, method,
                note=note, local_reason=local_reason)
        if not evidence and not warnings:
            # «Текст распознан, но поля не выделены» — неправда, если текста
            # нет вовсе: локальный OCR вернул пустую строку (смазанное фото).
            if method in LOCAL_OCR_METHODS and not str(data or "").strip():
                warnings = ("Локальный OCR не нашёл текст в изображении. "
                            "Проверьте оригинал.")
            else:
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
    def _report_job_failure(method, exc, source, file_index, page, on_result):
        """Ошибка метода: в лог — тип и стек, оператору — понятный текст."""
        logger.error("Импорт документов: ошибка метода %s (%s); стек: %s",
                     method, type(exc).__name__, _safe_traceback(exc))
        # Never expose network bodies, file contents, or raw parser exceptions.
        message = str(exc) if type(exc) is RuntimeError else \
            "Не удалось обработать документ. Проверьте формат и зависимости."
        on_result(source, [], method + ": " + message[:300])

    @staticmethod
    def _evidence(data, source, method, note="", local_reason=""):
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
                    own_note = " ".join(filter(None, [
                        note, str(value.get("_note", ""))]))
                    result.append(Evidence(
                        section, clean, source, method,
                        method in LOCAL_OCR_METHODS,
                        str(value.get("_kind", "")) if method in STRUCTURED_TEXT_METHODS else "",
                        own_note, local_reason))
        return result


def is_image_page(page):
    """Страница-изображение: фото, скан или PDF с нечитаемым текстовым слоем.

    Отличается от текстовой страницы PDF с ленивым рендером (`image_loader`):
    там источник правды — текст, и картинка нужна только как последняя
    попытка. Такие страницы идут прежним путём (текст → текстовая модель →
    Vision по рендеру), иначе Vision перебивал бы точный текстовый слой.
    """
    return page.image is not None and page.image_loader is None


def _is_rate_limit_error(exc):
    """Ответ 429 (превышен лимит запросов) — единственный случай, когда ждём."""
    return "429" in str(exc)


def _submit_once(function, *args):
    """Один вызов в отдельном пуле: рабочий пул страницы уже мог закрыться."""
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(function, *args)
    future.add_done_callback(lambda _: pool.shutdown(wait=False))
    return future


class _ReadyFuture:
    """Готовый результат вместо future: резервный путь уже посчитан.

    Нужен, чтобы результат резерва проходил ровно тот же разбор, что и
    основной путь (`_execute_job`), и не плодил вторую копию логики.
    """

    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value
