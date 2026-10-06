#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Справочники организаций и водителей для окна «Разовая аренда» (ШАГ FIX-1-T2).

Зачем отдельный модуль
----------------------
Вкладки аренды («Арендатор», «Арендодатель», «Экипаж») работают с ТЕМИ ЖЕ
справочниками, что «Экспедиторство»: организации лежат в таблицах customers
и carriers, водители — в таблице drivers (см. db/database.py). Отдельной
базы у аренды нет и не заводится.

Здесь живёт вся связка «поля вкладки ↔ запись справочника»:

  * константы ролей (ROLE_LESSEE / ROLE_LESSOR) и их соответствие таблицам
    справочника (ROLE_SCOPE): Арендатор — НАША сторона договора аренды,
    её реквизиты хранятся там же, где реквизиты заказчика (customers,
    is_carrier=False); Арендодатель — вторая сторона, таблица carriers
    (is_carrier=True);
  * маппинги имён (ORGANIZATION_FIELDS / DRIVER_FIELDS): у вкладки аренды
    свои имена полей (address, account, bank, driver_passport — одной
    строкой), у справочника — свои (legal_address, bank_account, bank_name,
    passport_series + passport_number);
  * поиск ДУБЛЯ и запись (save_organization_record / save_driver_record):
    найденная запись ОБНОВЛЯЕТСЯ, новой не создаётся;
  * чтение записи из базы по id (load_organization_record /
    load_driver_record).

Ключ дубля
----------
  * Организация — ИНН внутри своей таблицы (customers или carriers). Пустой
    ИНН ключом быть не может: у двух ИП его может не быть вовсе, и склеивать
    их в одну запись нельзя — такая запись сохраняется новой.
  * Водитель — первое подходящее: серия+номер ВУ → серия+номер паспорта →
    ФИО + дата рождения. Уникального ограничения в таблице drivers нет
    (db/database.py), а ВУ — документ, по которому водителя и различают;
    когда его в форме нет, остаётся связка «ФИО + дата рождения» — та же,
    по которой водителя узнают в списке справочника. Сравниваются только
    цифры документов: справочник хранит серию и номер раздельно
    («99 36» + «123456»), вкладка аренды — одной строкой («99 36 123456»).

Даты
----
В справочнике даты лежат строками, и в рабочей базе встречаются оба вида:
«1980-01-01» (так пишет вкладка водителя «Экспедиторства») и «01.01.1980».
Поэтому при сравнении даты приводятся к ISO, а запись всегда сохраняется
в ISO — раскладка, которую читают другие окна программы.

Логи без ПДн: пишутся только роль, признак «обновление/создание» и id.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from core.dates import parse_date

#: Модуль базы, а не отдельные функции: путь к базе (DB_PATH) подменяется
#: в тестах на уровне модуля (см. tests/conftest.py::isolated_db), поэтому
#: вызовы должны идти через db.database.* — иначе подмена не подействует.
from db import database

logger = logging.getLogger("ui.windows.arenda_ts.contacts")

# ─────────────────────────────────────────────────────────────
# Роли сторон аренды и таблицы справочника
# ─────────────────────────────────────────────────────────────

#: Роль «Арендатор» — наша сторона договора аренды.
ROLE_LESSEE = "Арендатор"

#: Роль «Арендодатель» — вторая сторона договора аренды.
ROLE_LESSOR = "Арендодатель"

#: Соответствие роли и таблицы справочника: (имя для аренды, is_carrier).
#: Роли аренды укладываются в существующую схему без новых таблиц:
#: арендатор — это customers, арендодатель — carriers.
ROLE_SCOPE: Dict[str, Tuple[str, bool]] = {
    ROLE_LESSEE: ("customers", False),
    ROLE_LESSOR: ("carriers", True),
}

#: Таблица справочника по роли (для логов и сообщений).
ROLE_TABLE: Dict[str, str] = {
    role: table for role, (table, _is_carrier) in ROLE_SCOPE.items()
}


def role_label(role: str) -> str:
    """Название роли для интерфейса («Арендатор» / «Арендодатель»)."""
    return role if role in ROLE_SCOPE else ROLE_LESSEE


def is_carrier_role(role: str) -> bool:
    """True, если роль хранится в таблице carriers (Арендодатель)."""
    return ROLE_SCOPE.get(role, ROLE_SCOPE[ROLE_LESSEE])[1]

# ─────────────────────────────────────────────────────────────
# Раскладка полей: вкладка ↔ справочник
# ─────────────────────────────────────────────────────────────

#: Поля организации: имя поля вкладки аренды → колонка справочника.
#: Адрес вкладки — юридический адрес бланка, счёт — расчётный счёт, банк —
#: наименование банка. Фактический адрес, корр. счёт и телефон вкладка
#: умеет показать, но полей ввода для них у неё нет: если такие данные
#: в записи есть, они уходят в форму и оттуда — в сборку данных.
ORGANIZATION_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("full_name", "full_name"),
    ("short_name", "short_name"),
    ("inn", "inn"),
    ("kpp", "kpp"),
    ("ogrn", "ogrn"),
    ("address", "legal_address"),
    ("actual_address", "actual_address"),
    ("account", "bank_account"),
    ("bank", "bank_name"),
    ("bik", "bik"),
    ("corr_account", "correspondent_account"),
    ("email", "email"),
    ("phone", "phone"),
    ("director_position", "director_position"),
    ("director_name", "director_name"),
)

#: Колонки справочника организаций, которые заполняет вкладка аренды.
ORGANIZATION_RECORD_FIELDS: Tuple[str, ...] = tuple(
    dict.fromkeys(column for _form, column in ORGANIZATION_FIELDS)
)

#: ВСЕ колонки таблиц customers / carriers, которые пишут save_organization
#: и update_organization. Лицензия есть только у перевозчика: у вкладок
#: аренды полей для неё нет, поэтому при обновлении она берётся из
#: существующей записи (merge_organization_records).
ORGANIZATION_TABLE_FIELDS: Tuple[str, ...] = tuple(
    dict.fromkeys(
        ORGANIZATION_RECORD_FIELDS + ("license_number", "license_date")
    )
)

#: Поля водителя: имя поля вкладки «Экипаж» → колонка справочника.
#: Паспорт и водительское удостоверение у вкладки — одной строкой, поэтому
#: в маппинге их нет: строку собирает и разбирает split_document.
DRIVER_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("driver_full_name", "full_name"),
    ("driver_birth_date", "birth_date"),
    ("driver_passport_issue_date", "passport_issue_date"),
    ("driver_passport_issuer", "passport_issuer"),
    ("driver_license_issue_date", "license_issue_date"),
    ("driver_registration_address", "registration_address"),
    ("driver_phone", "phone"),
)

#: Поля-даты вкладки «Экипаж» и их колонки в справочнике: сравнивать и
#: сохранять их нужно в одном формате (ISO).
DRIVER_DATE_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("driver_birth_date", "birth_date"),
    ("driver_passport_issue_date", "passport_issue_date"),
    ("driver_license_issue_date", "license_issue_date"),
)

#: Документы вкладки «Экипаж»: имя поля формы ↔ пара колонок справочника
#: (серия, номер).
DRIVER_DOCUMENTS: Dict[str, Tuple[str, str]] = {
    "driver_passport": ("passport_series", "passport_number"),
    "driver_license": ("license_series", "license_number"),
}

#: Колонки справочника водителей, которые у вкладки «Экипаж» есть.
#: Собирается из DRIVER_FIELDS: колонка паспорта и ВУ добавляется отдельно
#: (у вкладки они лежат одной строкой).
DRIVER_RECORD_FIELDS: Tuple[str, ...] = tuple(
    dict.fromkeys(
        [column for _form, column in DRIVER_FIELDS]
        + [column for pair in DRIVER_DOCUMENTS.values() for column in pair]
    )
)

#: ВСЕ колонки таблицы drivers, которые пишут save_driver / update_driver.
#: Колонки, которых у вкладки аренды нет, при обновлении берутся из
#: существующей записи (merge_driver_records) — их не теряет и не выдумывает
#: вкладка; у новой записи они пустые.
DRIVER_TABLE_FIELDS: Tuple[str, ...] = tuple(
    dict.fromkeys(
        DRIVER_RECORD_FIELDS
        + ("birth_place", "passport_code", "license_expiry_date",
           "license_categories")
    )
)

#: Колонки-даты справочника водителей: при сравнении и записи приводятся
#: к ISO — в рабочей базе встречаются оба вида («1980-01-01», «01.01.1980»).
DRIVER_RECORD_DATES = frozenset(
    column for _form, column in DRIVER_DATE_FIELDS
)

#: Сколько цифр в серии и номере документа: паспорт («18 22 926830») и
#: водительское удостоверение («99 36 123456») печатаются одинаково.
DOCUMENT_SERIES_DIGITS = 4
DOCUMENT_NUMBER_DIGITS = 6


# ─────────────────────────────────────────────────────────────
# Результат сохранения
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SaveResult:
    """Что вышло из сохранения записи в справочник."""

    #: id записи (None — сохранить не удалось).
    record_id: Optional[int]
    #: True — запись с таким ключом уже была и обновлена; False — создана.
    updated: bool
    #: Текст ошибки (пустая строка — ошибки нет).
    error: str = ""

    @property
    def ok(self) -> bool:
        """Сохранение прошло без ошибки (даже если это было обновление)."""
        return self.record_id is not None and not self.error

    def message(self, title: str) -> str:
        """Сообщение пользователю об итоге сохранения."""
        if not self.ok:
            return self.error or "Не удалось сохранить запись в справочник."

        return (
            f"{title}: запись с такими данными уже была в справочнике — "
            f"она обновлена (не задублирована)."
            if self.updated else
            f"{title}: запись сохранена в справочник."
        )


# ─────────────────────────────────────────────────────────────
# Даты и документы
# ─────────────────────────────────────────────────────────────

def normalize_date(value: Any) -> str:
    """
    Дата строкой в ISO (ГГГГ-ММ-ДД) — как её хранит справочник.

    Рабочая база принимает оба вида («1980-01-01» и «01.01.1980»), поэтому
    перед сравнением и записью дата приводится к одному виду. Непонятное
    значение остаётся как есть (пустая строка — пустой строкой).
    """
    text = str(value or "").strip()
    if not text:
        return ""

    parsed = parse_date(text, warn=False)
    return parsed.strftime("%Y-%m-%d") if parsed else text


def _digits(value: Any) -> str:
    """Только цифры значения («99 36» → «9936»)."""
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def split_document(value: Any) -> Tuple[str, str]:
    """
    Серия и номер документа из одной строки: «99 36 123456» → («99 36», «123456»).

    Вкладка «Экипаж» держит паспорт и ВУ одной строкой (их так отдаёт
    распознавание), а справочник хранит серию и номер раздельно — ровно так,
    как их отдаёт вкладка водителя «Экспедиторства».

    Разбор по количеству цифр: 10 — серия и номер (4 + 6), 6 — только номер,
    4 — только серия. Разделители между ними бывают любыми, поэтому берутся
    только цифры. Разобрать не удалось — обе части пустые, и вызывающий код
    сохраняет строку как есть.
    """
    digits = _digits(value)
    if len(digits) == DOCUMENT_SERIES_DIGITS + DOCUMENT_NUMBER_DIGITS:
        return (
            f"{digits[:2]} {digits[2:4]}",
            digits[DOCUMENT_SERIES_DIGITS:],
        )
    if len(digits) == DOCUMENT_NUMBER_DIGITS:
        return "", digits
    if len(digits) == DOCUMENT_SERIES_DIGITS:
        return f"{digits[:2]} {digits[2:]}", ""
    return "", ""


def join_document(series: Any, number: Any) -> str:
    """Строка документа из серии и номера: «99 36» + «123456» → «99 36 123456»."""
    return " ".join(
        part for part in (str(series or "").strip(), str(number or "").strip()) if part
    )


# ─────────────────────────────────────────────────────────────
# Организации
# ─────────────────────────────────────────────────────────────

def organization_to_form(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Запись справочника → поля вкладки аренды.

    Имена, которые у вкладки и справочника совпадают, переносятся как есть;
    остальные — по ORGANIZATION_FIELDS. Пустые значения не отдаются: они не
    должны стирать уже введённое (правило fill_data самих вкладок).

    Нужны обе стороны: fill_data() вкладки принимает и имена справочника,
    и свои — тест вправе позвать его с «сырой» записью.
    """
    source = record if isinstance(record, dict) else {}
    if not source:
        return {}

    values: Dict[str, Any] = {}
    for form_field, column in ORGANIZATION_FIELDS:
        value = str(source.get(column) or "").strip()
        if value:
            values[form_field] = value

    # Запись справочника может прийти с «сырыми» именами: примем и их.
    for raw_field in ("legal_address", "bank_account", "bank_name",
                      "correspondent_account"):
        value = str(source.get(raw_field) or "").strip()
        if value:
            values.setdefault(raw_field, value)

    return values


def organization_from_form(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Поля вкладки аренды → запись справочника (полный набор колонок).

    Пустые значения пишутся пустыми строками: update_organization очищает
    поля, которых нет в data, поэтому набор должен быть полным — иначе
    правка одной вкладки затрёт чужие поля записи. Полей формы у вкладки
    меньше, чем колонок справочника (лицензия перевозчика, телефон), о них
    заботится merge_organization_records: при обновлении они сохраняются
    из существующей записи.
    """
    form = data if isinstance(data, dict) else {}

    return {
        column: str(form.get(form_field) or "").strip()
        for form_field, column in ORGANIZATION_FIELDS
    }


def merge_organization_records(existing: Optional[Dict[str, Any]],
                               record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Запись организации для сохранения: поля вкладки поверх существующей записи.

    Вкладки аренды знают поля бланка, но не все колонки справочника: номер и
    дату лицензии перевозчика вводят только в «Экспедиторстве». Такие колонки
    берутся из существующей записи целиком (даже пустыми — вкладка их не
    обнуляет), а колонки вкладки заменяются её значениями. Если существующей
    записи нет, недостающие колонки заполняются пустыми строками: INSERT
    пишет их пустыми.
    """
    merged: Dict[str, Any] = {}
    for column in ORGANIZATION_TABLE_FIELDS:
        if column in record:
            merged[column] = record[column]
        else:
            merged[column] = (existing or {}).get(column, "")

    return merged


def organization_key(data: Dict[str, Any]) -> str:
    """Ключ дубля организации — ИНН (пустой ИНН ключом не является)."""
    return str((data or {}).get("inn") or "").strip()


def find_organization(data: Dict[str, Any], role: str = ROLE_LESSEE,
                      include_deleted: bool = True) -> Optional[Dict[str, Any]]:
    """
    Запись справочника с тем же ИНН в таблице этой роли (или None).

    Мягко удалённые записи тоже находятся (include_deleted=True): повторное
    сохранение должно обновить запись и вернуть её в справочник, а не
    оставить рядом её двойника.
    """
    inn = organization_key(data)
    if not inn:
        return None

    is_carrier = is_carrier_role(role)
    for record in database.get_all_organizations(
        is_carrier=is_carrier, include_deleted=include_deleted
    ):
        if str(record.get("inn") or "").strip() == inn:
            return record
    return None


def load_organization_record(record_id: Any) -> Optional[Dict[str, Any]]:
    """Запись организации по id (любой из двух таблиц) или None."""
    if record_id in (None, ""):
        return None

    try:
        wanted = int(record_id)
    except (TypeError, ValueError):
        return None

    for is_carrier in (True, False):
        for record in database.get_all_organizations(
            is_carrier=is_carrier, include_deleted=True
        ):
            if int(record.get("id") or 0) == wanted:
                return record
    return None


def save_organization_record(data: Dict[str, Any],
                             role: str = ROLE_LESSEE) -> SaveResult:
    """
    Сохраняет организацию вкладки в общий справочник.

    Дубль по ИНН обновляется, а не создаётся заново (У3.А): так одна и та же
    организация не расползается по базе копиями. Мягко удалённая запись с тем
    же ИНН обновляется и возвращается в справочник.
    """
    record = organization_from_form(data)
    is_carrier = is_carrier_role(role)

    if not record.get("full_name"):
        return SaveResult(
            None, False,
            "Нечего сохранять: заполните полное наименование организации.",
        )

    try:
        existing = find_organization(record, role)
        if existing is not None:
            record_id = int(existing.get("id") or 0)
            # Колонки, которых у вкладки нет, остаются из существующей записи.
            database.update_organization(
                record_id,
                merge_organization_records(existing, record),
                is_carrier=is_carrier,
            )
            if existing.get("is_deleted"):
                # Запись была убрана из справочника: сохранение возвращает её.
                _restore_organization(record_id, is_carrier)

            logger.info(
                f"Аренда: организация обновлена по ИНН (роль={role}, "
                f"таблица={ROLE_TABLE.get(role, '')}, ID={record_id})"
            )
            return SaveResult(record_id, True)

        record_id = database.save_organization(record, is_carrier=is_carrier)
        logger.info(
            f"Аренда: организация сохранена (роль={role}, "
            f"таблица={ROLE_TABLE.get(role, '')}, ID={record_id})"
        )
        return SaveResult(record_id, False)
    except Exception as e:  # noqa: BLE001 — окно не должно падать из-за базы
        logger.exception("Аренда: не удалось сохранить организацию")
        return SaveResult(None, False, f"Не удалось сохранить организацию:\n{e}")


def _restore_organization(record_id: int, is_carrier: bool) -> None:
    """
    Возвращает мягко удалённую организацию в справочник.

    Импорт внутри функции: restore_organization нужен только на этом пути,
    и держать его в общем импорте модуля незачем.
    """
    from db.database import restore_organization

    restore_organization(record_id, is_carrier=is_carrier)


# ─────────────────────────────────────────────────────────────
# Водители
# ─────────────────────────────────────────────────────────────

def driver_to_form(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Запись водителя из справочника → поля вкладки «Экипаж».

    Паспорт и водительское удостоверение справочник хранит серией и номером,
    а вкладка — одной строкой: здесь они склеиваются (join_document).
    Даты приводятся к ISO — вкладка принимает оба вида, но ISO однозначен.

    Нужны обе стороны: fill_data() вкладки принимает и имена справочника,
    и свои — тест вправе позвать его с «сырой» записью.
    """
    source = record if isinstance(record, dict) else {}
    if not source:
        return {}

    values: Dict[str, Any] = {}
    for form_field, column in DRIVER_FIELDS:
        value = str(source.get(column) or "").strip()
        if value:
            values[form_field] = value

    for form_field, column in DRIVER_DATE_FIELDS:
        value = normalize_date(source.get(column))
        if value:
            values[form_field] = value

    for form_field, (series_column, number_column) in DRIVER_DOCUMENTS.items():
        document = join_document(source.get(series_column), source.get(number_column))
        if document:
            values[form_field] = document

    # Запись может прийти с именами вкладки водителя «Экспедиторства»
    # (passport / license одной строкой) — примем и такой вид.
    for form_field in DRIVER_DOCUMENTS:
        value = str(source.get(form_field) or "").strip()
        if value:
            values.setdefault(form_field, value)

    return values


def driver_from_form(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Поля вкладки «Экипаж» → запись справочника (полный набор колонок).

    Пустые значения пишутся пустыми строками: update_driver очищает поля,
    которых нет в data, поэтому набор должен быть полным — иначе правка
    вкладки аренды затрёт чужие поля записи. Колонок у справочника больше,
    чем полей у вкладки (место рождения, код подразделения, категории и срок
    действия ВУ): при обновлении их сохраняет merge_driver_records.
    """
    form = data if isinstance(data, dict) else {}

    record: Dict[str, Any] = {}
    for form_field, column in DRIVER_FIELDS:
        record[column] = (
            normalize_date(form.get(form_field))
            if column in DRIVER_RECORD_DATES else
            str(form.get(form_field) or "").strip()
        )

    for form_field, (series_column, number_column) in DRIVER_DOCUMENTS.items():
        series, number = split_document(form.get(form_field))
        record[series_column] = series
        record[number_column] = number

    return record


def merge_driver_records(existing: Optional[Dict[str, Any]],
                         record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Запись водителя для сохранения: поля вкладки поверх существующей записи.

    Вкладка «Экипаж» знает девять полей бланка аренды, а в справочнике их
    пятнадцать: место рождения, код подразделения, категории и срок действия
    ВУ она не показывает. Эти колонки берутся из существующей записи ЦЕЛИКОМ
    (даже пустыми — их не обнуляет и не выдумывает вкладка), а колонки вкладки
    заменяются её значениями. Если существующей записи нет, недостающие
    колонки заполняются пустыми строками: INSERT пишет их пустыми.

    Так сохранение из аренды и не теряет чужие данные, и по-прежнему умеет
    очистить поле, которое пользователь стёр на вкладке.
    """
    merged: Dict[str, Any] = {}
    for column in DRIVER_TABLE_FIELDS:
        if column in record:
            merged[column] = record[column]
        else:
            merged[column] = (existing or {}).get(column, "")

    return merged


def _driver_keys(record: Dict[str, Any]) -> List[Tuple[str, ...]]:
    """
    Ключи дубля водителя в порядке силы: ВУ → паспорт → ФИО + дата рождения.

    record — ЗАПИСЬ СПРАВОЧНИКА (колонки full_name, license_series, ...),
    а не поля вкладки: сюда приходят и записи из базы, и результат
    driver_from_form(), у которого те же имена колонок.

    Пустые ключи в список не попадают: у водителя без ВУ и без паспорта
    остаётся только связка «ФИО + дата рождения».
    """
    source = record if isinstance(record, dict) else {}
    keys: List[Tuple[str, ...]] = []

    license_digits = _digits(
        f"{source.get('license_series', '')}{source.get('license_number', '')}"
    )
    if len(license_digits) >= DOCUMENT_NUMBER_DIGITS:
        keys.append(("license", license_digits))

    passport_digits = _digits(
        f"{source.get('passport_series', '')}{source.get('passport_number', '')}"
    )
    if len(passport_digits) >= DOCUMENT_NUMBER_DIGITS:
        keys.append(("passport", passport_digits))

    full_name = str(source.get("full_name") or "").strip().casefold()
    birth_date = normalize_date(source.get("birth_date"))
    if full_name and birth_date:
        keys.append(("person", full_name, birth_date))

    return keys


def driver_key_names(record: Dict[str, Any]) -> List[str]:
    """
    Названия ключей дубля, которые есть у записи справочника.

    Для данных вкладки «Экипаж» их сначала переводит driver_from_form():
    у результата этого перевода те же имена колонок, что у записи.
    """
    return [key[0] for key in _driver_keys(record)]


def find_driver_record(record: Optional[Dict[str, Any]],
                       include_deleted: bool = True) -> Optional[Dict[str, Any]]:
    """
    Дубль в справочнике для записи водителя (колонки как у таблицы drivers).

    Если подходящих ключей у записи нет (пустые ФИО, документы и дата
    рождения), дубль не ищется: сопоставлять нечего.
    """
    candidates = _driver_keys(record or {})
    if not candidates:
        return None

    for existing in database.get_all_drivers(include_deleted=include_deleted):
        existing_keys = _driver_keys(existing)
        existing_names = {key[0] for key in existing_keys}
        for key in candidates:
            # Ключом может быть ВУ, паспорт или «ФИО + дата рождения»:
            # сравниваются ключи одного вида, а не соседние.
            if key[0] in existing_names and key in existing_keys:
                return existing
    return None


def find_driver(data: Dict[str, Any],
                include_deleted: bool = True) -> Optional[Dict[str, Any]]:
    """
    Дубль для данных вкладки «Экипаж» (ключи driver_full_name, driver_passport…).

    Сравнение идёт с записями справочника по цифрам документов и по ISO-дате:
    справочник хранит серию и номер раздельно, а вкладка аренды — одной
    строкой. Сила ключей: ВУ → паспорт → «ФИО + дата рождения».
    """
    return find_driver_record(driver_from_form(data), include_deleted)


def load_driver_record(record_id: Any) -> Optional[Dict[str, Any]]:
    """Запись водителя по id (включая мягко удалённую) или None."""
    if record_id in (None, ""):
        return None

    try:
        return database.load_driver(int(record_id))
    except (TypeError, ValueError):
        return None


def save_driver_record(data: Dict[str, Any]) -> SaveResult:
    """
    Сохраняет водителя вкладки «Экипаж» в общий справочник.

    Дубль по ВУ (или по ФИО + дате рождения, если ВУ не заполнено)
    обновляется, а не создаётся заново, — как у организаций. Мягко удалённый
    водитель обновляется и возвращается в справочник.
    """
    record = driver_from_form(data)

    if not record.get("full_name"):
        return SaveResult(
            None, False, "Нечего сохранять: заполните ФИО водителя."
        )

    try:
        existing = find_driver_record(record)
        if existing is not None:
            record_id = int(existing.get("id") or 0)
            # Колонки, которых у вкладки нет, остаются из существующей записи.
            database.update_driver(record_id, merge_driver_records(existing, record))
            if existing.get("is_deleted"):
                # Запись была убрана из справочника: сохранение возвращает её.
                from db.database import restore_driver

                restore_driver(record_id)

            logger.info(
                f"Аренда: водитель обновлён по ключу "
                f"({'+'.join(driver_key_names(record))}, ID={record_id})"
            )
            return SaveResult(record_id, True)

        record_id = database.save_driver(record)
        logger.info(f"Аренда: водитель сохранён (ID={record_id})")
        return SaveResult(record_id, False)
    except Exception as e:  # noqa: BLE001 — окно не должно падать из-за базы
        logger.exception("Аренда: не удалось сохранить водителя")
        return SaveResult(None, False, f"Не удалось сохранить водителя:\n{e}")


__all__ = [
    "ROLE_LESSEE",
    "ROLE_LESSOR",
    "ROLE_SCOPE",
    "ROLE_TABLE",
    "ORGANIZATION_FIELDS",
    "ORGANIZATION_RECORD_FIELDS",
    "ORGANIZATION_TABLE_FIELDS",
    "DRIVER_FIELDS",
    "DRIVER_DATE_FIELDS",
    "DRIVER_DOCUMENTS",
    "DRIVER_RECORD_FIELDS",
    "DRIVER_TABLE_FIELDS",
    "SaveResult",
    "role_label",
    "is_carrier_role",
    "normalize_date",
    "split_document",
    "join_document",
    "organization_to_form",
    "organization_from_form",
    "merge_organization_records",
    "organization_key",
    "find_organization",
    "load_organization_record",
    "save_organization_record",
    "driver_to_form",
    "driver_from_form",
    "merge_driver_records",
    "driver_key_names",
    "find_driver",
    "find_driver_record",
    "load_driver_record",
    "save_driver_record",
]
