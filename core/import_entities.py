"""Группировка полей импорта документов по сущностям — без Qt.

Зачем модуль. Раньше диалог импорта показывал ПЛОСКИЙ список из 57 строк
«поле — значение»: оператор не видел, где кончается один водитель и
начинается другой документ, и получал непонятную ошибку «разные группы».
Здесь живёт ЛОГИКА группировки: плоский список evidence превращается в
сущности (водитель, организация, машина), у каждой — свои источники и поля.
Диалог только показывает то, что вернул `group_fields()`.

Конвейер (пакетная обработка папки) — отдельный шаг, но он вызывает те же
функции: `group_fields()` + `detect_conflicts()` и складывает результат в
JSON. Дублирующей логики не будет.

Границы: модуль не зависит от Qt и ничего не знает про виджеты.
Сборка полей (значение, состояние, источники вариантов) остаётся в
`core/document_import_service.py::compare_fields` — здесь она только
переупаковывается в `Entity`/`FieldValue`, чтобы правило «что считать
одним полем» жило в одном месте.
"""
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from core.document_import_service import LABELS, compare_fields, valid_value

# ─────────────────────────────────────────────────────────────
# Состояния поля (задел на будущее: сюда же придёт уверенность модели)
# ─────────────────────────────────────────────────────────────

STATE_OK = "ok"                    # прочитано и проверено
STATE_UNREADABLE = "unreadable"    # не прочитано (нет значения)
STATE_CONFLICT = "conflict"        # источники дают разные значения
STATE_INVALID = "invalid"          # значение есть, но формату не отвечает

# Состояния, которыми называет поля сборщик (compare_fields).
ROW_STATE_UNREADABLE = "не прочитано"
ROW_STATE_CONFLICT = "расхождение"

# ─────────────────────────────────────────────────────────────
# Сущности: раздел формы → вид сущности и подписи
# ─────────────────────────────────────────────────────────────

KIND_DRIVER = "driver"
KIND_ORGANIZATION = "organization"
KIND_VEHICLE = "vehicle"
KIND_CONTRACT = "contract"

KIND_BY_SECTION = {
    "driver": KIND_DRIVER,
    "carrier": KIND_ORGANIZATION,
    "customer": KIND_ORGANIZATION,
    "vehicles": KIND_VEHICLE,
    "tractor": KIND_VEHICLE,
    "trailer": KIND_VEHICLE,
    "contract": KIND_CONTRACT,
}

# Заголовок сущности в дереве. Для организаций роль важна: одна и та же
# выписка может попасть и в «Перевозчика», и в «Заказчика».
SECTION_HEADINGS = {
    "driver": "ВОДИТЕЛЬ",
    "carrier": "ОРГАНИЗАЦИЯ (перевозчик)",
    "customer": "ОРГАНИЗАЦИЯ (заказчик)",
    "vehicles": "АВТОМОБИЛЬ",
    "tractor": "ТЯГАЧ",
    "trailer": "ПОЛУПРИЦЕП",
    "contract": "ДОГОВОР",
}

# Винительный падеж — для кнопки «Подтвердить водителя целиком».
SECTION_ACCUSATIVE = {
    "driver": "водителя",
    "carrier": "перевозчика",
    "customer": "заказчика",
    "vehicles": "автомобиль",
    "tractor": "тягач",
    "trailer": "полуприцеп",
    "contract": "договор",
}

# Порядок блоков в дереве: сначала люди, потом организации, потом техника.
KIND_ORDER = {KIND_DRIVER: 0, KIND_ORGANIZATION: 1, KIND_VEHICLE: 2, KIND_CONTRACT: 3}

#: Поля-опознаватели: по ним сущность связывается с человеком, организацией
#: или машиной. Если такое поле есть не во всех источниках одной сущности,
#: оператору нужна подсказка «проверить: …» — связали по тому, что совпало.
IDENTITY_REVIEW_FIELDS = {
    "driver": ("full_name", "birth_date"),
    "carrier": ("inn", "full_name"),
    "customer": ("inn", "full_name"),
    "vehicles": ("vin", "plate_number"),
    "tractor": ("plate_number",),
    "trailer": ("plate_number",),
    "contract": ("number",),
}

#: Слова, которыми помечается сущность без опознания (нет ни ФИО, ни VIN).
UNKNOWN_KEY_PREFIX = "unknown_"
UNIDENTIFIED_MARK = "опознать не удалось — проверьте вручную"

KIND_COUNT_NAMES = OrderedDict((
    (KIND_DRIVER, "водителей"),
    (KIND_ORGANIZATION, "организаций"),
    (KIND_VEHICLE, "машин"),
    (KIND_CONTRACT, "договоров"),
))

EMPTY_SOURCE = ""                       # поля, не найденные ни в одном файле
UNREADABLE_SOURCE_LABEL = "Не прочитано ни в одном документе"

# Строку источников собирает compare_fields:
#   "<источник> · <метод>: <значение>" и, если есть, второй строкой примечание.
SOURCE_LINE_RE = re.compile(r"^(?P<source>.+?) · (?P<method>[^:]+): (?P<value>.*)$")
SOURCE_EMPTY_MARK = "—"


def parse_sources(text: str) -> List[Tuple[str, str, str]]:
    """
    Разбирает строку источников поля в список ``(источник, метод, значение)``.

    Формат задаёт `compare_fields`; строки примечаний (`_note`) пропускаются.
    Значение-заглушка «—» превращается в пустую строку.
    """
    entries = []
    for line in str(text or "").splitlines():
        match = SOURCE_LINE_RE.match(line.strip())
        if not match:
            continue
        value = match.group("value").strip()
        entries.append((match.group("source").strip(),
                        match.group("method").strip(),
                        "" if value == SOURCE_EMPTY_MARK else value))
    return entries


def state_from_row(state: str, value: str, key: str) -> str:
    """
    Состояние сборщика → состояние поля для интерфейса и конвейера.

    Порядок важен: у расхождения значение пустое (выбирать за оператора
    нельзя), поэтому сначала проверяется само расхождение.
    """
    if state == ROW_STATE_CONFLICT:
        return STATE_CONFLICT
    if state == ROW_STATE_UNREADABLE or not str(value or "").strip():
        return STATE_UNREADABLE
    if not valid_value(key, value):
        return STATE_INVALID
    return STATE_OK


@dataclass
class FieldValue:
    """Одно поле сущности: значение, источник и состояние."""

    field: str
    value: str
    source: str
    method: str
    confidence: float = 1.0       # пока всегда 1.0 (модель ещё не отдаёт оценку)
    state: str = STATE_OK
    section: str = ""
    label: str = ""
    sources: List[str] = field(default_factory=list)
    variants: List[Tuple[str, str]] = field(default_factory=list)   # (источник, значение)
    detail: str = ""              # как это состояние назвал сборщик
    note: str = ""

    @property
    def readable(self) -> bool:
        return self.state in (STATE_OK, STATE_INVALID) and bool(self.value)

    @property
    def unreadable(self) -> bool:
        return self.state == STATE_UNREADABLE

    @property
    def conflicted(self) -> bool:
        return self.state == STATE_CONFLICT

    @property
    def confirmable(self) -> bool:
        """Поле можно подтвердить: значение есть и формат подходит."""
        return self.state == STATE_OK and valid_value(self.field, self.value)


@dataclass
class Entity:
    """Сущность документа: водитель, организация, машина, договор."""

    kind: str
    key: str
    title: str
    fields: List[FieldValue] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    section: str = ""
    identity: dict = field(default_factory=dict)
    group: int = 0
    needs_review: bool = False
    notes: List[str] = field(default_factory=list)

    # ── подписи ──

    @property
    def heading(self) -> str:
        return SECTION_HEADINGS.get(self.section, self.section or "ЗАПИСЬ")

    @property
    def uid(self) -> str:
        """Устойчивый ключ сущности: подтверждения переживают перерисовку."""
        return f"{self.section}#{self.group}"

    @property
    def display_name(self) -> str:
        if not self.title:
            return f"{self.heading} ({UNIDENTIFIED_MARK})"
        return f"{self.heading}: {self.title}"

    @property
    def button_text(self) -> str:
        return f"Подтвердить {SECTION_ACCUSATIVE.get(self.section, 'запись')} целиком"

    # ── состав полей ──

    def field_value(self, name: str) -> Optional[FieldValue]:
        for value in self.fields:
            if value.field == name:
                return value
        return None

    def readable_fields(self) -> List[FieldValue]:
        return [value for value in self.fields if value.readable]

    def values(self) -> Dict[str, str]:
        """
        Прочитанные значения полей — как их видит форма.

        Нужно там, где сущность сверяется с уже заполненной формой
        (`same_entity`): в `identity` значения нормализованы (регистр и
        пробелы сняты), и для VIN такая нормализация не проходит проверку
        формата — сверять надо исходные значения.
        """
        return {value.field: value.value for value in self.readable_fields()}

    def unreadable_fields(self) -> List[FieldValue]:
        return [value for value in self.fields if value.unreadable]

    def conflict_fields(self) -> List[FieldValue]:
        return [value for value in self.fields if value.conflicted]

    def confirmable_fields(self) -> List[FieldValue]:
        return [value for value in self.fields if value.confirmable]

    def fields_by_source(self) -> "OrderedDict[str, List[FieldValue]]":
        """
        Поля, разложенные по источникам: ``{файл: [поля]}``.

        Поле попадает ровно в один источник — свой основной (`FieldValue.source`),
        иначе в дереве было бы две галочки на одно и то же поле. Остальные
        источники видны в подсказке вариантов. Непрочитанные поля собираются
        в один блок ``EMPTY_SOURCE`` и идут последними: оператор видит, чего
        в документах не нашлось, и может ввести значение вручную.
        """
        grouped = OrderedDict((source, []) for source in self.sources)
        grouped.setdefault(EMPTY_SOURCE, [])
        for value in self.fields:
            key = EMPTY_SOURCE if value.unreadable else (value.source or EMPTY_SOURCE)
            grouped.setdefault(key, []).append(value)
        return OrderedDict((source, values) for source, values in grouped.items() if values)

    def counts_text(self) -> str:
        """Краткая сводка по сущности: сколько полей прочитано и спорных."""
        parts = [f"полей: {len(self.fields)}",
                 f"прочитано: {len(self.readable_fields())}",
                 f"не прочитано: {len(self.unreadable_fields())}"]
        conflicts = len(self.conflict_fields())
        if conflicts:
            parts.append(f"спорных: {conflicts}")
        return " · ".join(parts)

    def sources_text(self) -> str:
        return ", ".join(self.sources) if self.sources else "источники не определены"

    def review_text(self) -> str:
        """Подсказки «проверить» одной строкой (для подсказки узла дерева)."""
        return " ".join("⚠ " + note for note in self.notes)


def review_notes(section: str, values: Sequence[FieldValue]) -> List[str]:
    """
    «Проверить: …» — что мешает связать документы одной сущности.

    Документы одного водителя связываются по ФИО (см. `same_entity`), поэтому
    если дата рождения есть в паспорте, но её нет в ВУ, — это один человек,
    но оператору нужно об этом сказать. Подсказка появляется только когда
    поле РАЗОБРАНО хотя бы в одном источнике и отсутствует в другом: для
    одного файла предупреждать не о чем, иначе дерево утонуло бы в «проверить».
    """
    notes = []
    for name in IDENTITY_REVIEW_FIELDS.get(section, ()):
        value = next((item for item in values if item.field == name), None)
        if value is None or not value.value or len(value.variants) < 2:
            continue
        filled = {source for source, variant in value.variants if variant}
        empty = {source for source, variant in value.variants if not variant}
        if filled and empty:
            notes.append(f"проверить: «{value.label}» не найден в {', '.join(sorted(empty))}")
    return notes


def kind_by_section(section: str) -> str:
    return KIND_BY_SECTION.get(section, KIND_CONTRACT)


def field_from_row(row) -> FieldValue:
    """Строка сборки (`FieldResult`) → поле сущности."""
    entries = parse_sources(getattr(row, "sources", ""))
    value = str(getattr(row, "value", "") or "").strip()
    state = state_from_row(str(getattr(row, "state", "") or ""), value, row.key)
    if state == STATE_UNREADABLE:
        # Поля нет ни в одном документе: «источник с прочерком» только шумит.
        entries = []
    source, method = "", ""
    for entry_source, entry_method, entry_value in entries:
        if entry_value and not source:
            source, method = entry_source, entry_method
    if not source and entries:
        source, method = entries[0][0], entries[0][1]
    notes = [line.strip() for line in str(getattr(row, "sources", "") or "").splitlines()
             if line.strip() and not SOURCE_LINE_RE.match(line.strip())]
    return FieldValue(
        field=row.key,
        value=value,
        source=source,
        method=method,
        confidence=1.0,
        state=state,
        section=row.section,
        label=LABELS.get(row.key, row.key) or row.key,
        sources=_unique_sources(row, entries),
        variants=[(entry_source, entry_value) for entry_source, _, entry_value in entries],
        detail=str(getattr(row, "state", "") or ""),
        note=" ".join(notes),
    )


def _unique_sources(row, entries: Sequence[Tuple[str, str, str]]) -> List[str]:
    """
    Список источников поля без повторов, в порядке появления.

    Если разобрать строку не удалось (ручная группа: там просто подпись
    файла), источником считается эта подпись — иначе ручная проверка
    потеряла бы связь с оригиналом.
    """
    sources = []
    for source, _, _ in entries:
        if source not in sources:
            sources.append(source)
    if sources:
        return sources
    for line in str(getattr(row, "sources", "") or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if not SOURCE_LINE_RE.match(line):
            return [line]
        break
    return []


def _field_title(section: str, fields: Dict[str, FieldValue]) -> str:
    """Человекочитаемое опознание сущности: «Иванов Иван Иванович, 15.03.1985»."""

    def value(name: str) -> str:
        found = fields.get(name)
        return str(found.value).strip() if found else ""

    if section == "driver":
        return ", ".join(part for part in (value("full_name"), value("birth_date")) if part)
    if section in {"carrier", "customer"}:
        name = value("full_name") or value("short_name")
        inn = value("inn")
        if name and inn:
            return f"{name}, ИНН {inn}"
        return name or (f"ИНН {inn}" if inn else "")
    if section in {"vehicles", "tractor", "trailer"}:
        if value("vin"):
            return f"VIN {value('vin')}"
        if value("plate_number"):
            return f"госномер {value('plate_number')}"
        return value("brand_model")
    if section == "contract":
        number = value("number")
        return f"№ {number}" if number else ""
    return ""


def entities_from_rows(rows: Sequence) -> List[Entity]:
    """
    Строки сборки → сущности (для диалога и для конвейера).

    Строки без раздела (файл не прочитан) сущностями не становятся: их
    диалог показывает отдельным списком.
    """
    grouped = OrderedDict()
    for row in rows:
        if not getattr(row, "section", ""):
            continue
        grouped.setdefault(row.group, []).append(row)

    entities = []
    for group, group_rows in grouped.items():
        section = group_rows[0].section
        values = [field_from_row(row) for row in group_rows]
        by_name = {value.field: value for value in values}
        identity = {}
        for row in group_rows:
            identity.update(getattr(row, "identity", {}) or {})
        sources = []
        for value in values:
            for source in value.sources:
                if source not in sources:
                    sources.append(source)
        title = _field_title(section, by_name)
        key = ", ".join(f"{name}={value}" for name, value in sorted(identity.items())) or title
        needs_review = not title
        if not key:
            # Опознать не удалось (ни ФИО, ни ИНН, ни VIN) — ключ-заглушка,
            # чтобы сущность всё равно была отдельной записью и её видел оператор.
            key = f"{UNKNOWN_KEY_PREFIX}{group + 1}"
        entities.append(Entity(kind=kind_by_section(section), key=key, title=title,
                               fields=values, sources=sources, section=section,
                               identity=identity, group=group, needs_review=needs_review,
                               notes=review_notes(section, values)))
    return sorted(entities, key=lambda entity: (KIND_ORDER.get(entity.kind, 9), entity.group))


def group_fields(evidence_list: Sequence) -> List[Entity]:
    """
    Плоский список `Evidence` → сущности.

    Точка входа и для диалога, и для будущего конвейера: группировку делает
    `compare_fields` (одно правило на весь проект), здесь результат
    упаковывается в сущности.
    """
    return entities_from_rows(compare_fields(list(evidence_list)))


def detect_conflicts(entities: Sequence[Entity]) -> List[dict]:
    """
    Конфликты полей: один и тот же ключ внутри сущности, разные значения.

    Расхождение значений из РАЗНЫХ сущностей конфликтом не считается — это
    разные люди и разные машины. Возвращаются словари (без ПДн в логах):
    сущность, поле, источники и варианты значений.
    """
    conflicts = []
    for entity in entities:
        for value in entity.conflict_fields():
            conflicts.append({
                "entity": entity.display_name,
                "section": entity.section,
                "field": value.field,
                "label": value.label,
                "sources": list(value.sources),
                "variants": list(value.variants),
            })
    return conflicts


def count_by_kind(entities: Sequence[Entity]) -> Dict[str, int]:
    counts = {}
    for entity in entities:
        counts[entity.kind] = counts.get(entity.kind, 0) + 1
    return counts


def conflict_count(entities: Sequence[Entity]) -> int:
    return sum(len(entity.conflict_fields()) for entity in entities)


def unidentified_count(entities: Sequence[Entity]) -> int:
    """Сколько сущностей не удалось опознать (нет ни ФИО, ни ИНН, ни VIN)."""
    return sum(1 for entity in entities if entity.needs_review)


def summary_text(entities: Sequence[Entity]) -> str:
    """«Найдено: водителей — 1, машин — 2, организаций — 1. Спорных полей — 1.»"""
    counts = count_by_kind(entities)
    parts = [f"{KIND_COUNT_NAMES[kind]} — {counts[kind]}"
             for kind in KIND_COUNT_NAMES if counts.get(kind)]
    found = ("Найдено: " + ", ".join(parts) + ".") if parts else "Сущности не найдены."
    tail = f"Спорных полей — {conflict_count(entities)}."
    unknown = unidentified_count(entities)
    if unknown:
        tail += f" Опознать не удалось — {unknown}."
    return f"{found} {tail}"


def progress_text(processed: int, total: int, entities: Optional[Sequence[Entity]] = None,
                  prefix: str = "") -> str:
    """
    Строка прогресса над деревом: сколько файлов обработано и что найдено.

    Считается без ПДн: только числа и виды сущностей.
    """
    head = f"Обработано {int(processed)} из {int(total)} файлов."
    tail = summary_text(entities) if entities is not None else ""
    return " ".join(part for part in (prefix.strip(), head, tail) if part)


def entity_by_uid(entities: Sequence[Entity], uid: str) -> Optional[Entity]:
    for entity in entities:
        if entity.uid == uid:
            return entity
    return None
