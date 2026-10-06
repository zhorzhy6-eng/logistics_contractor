# -*- coding: utf-8 -*-
"""
Генератор заявки Хавалов — Excel-форма «ЗАЯВКА на перевозку автомобилей»
(ЭТАП 3.1.E.A.3).

В отличие от остальных типов НЕ наследуется от BaseContractGenerator:
выход — .xlsx (openpyxl уже в зависимостях), а не DOCX. Общий с другими
типами — только реестр и валидатор. Рендерер-абстракцию не строим
(решение по пункту 4 ответов на аудит).

ЧТО ДЕЛАЕТ ГЕНЕРАТОР
--------------------
Два направления работы с одним и тем же бланком:

  * ``generate(data, output_dir)`` — берёт НАШ эталонный бланк
    (templates/shablon_havaly.xlsx, см. tools/make_havaly_template.py),
    заполняет его данными и сохраняет в
    ``output/Заявка_Хавалы_<дата ISO>.xlsx``;
  * ``fill_from_template(path, data, output_dir)`` — то же самое, но
    источник бланка — присланный пользователем .xlsx: заказчики присылают
    ту же форму, но со своим оформлением и своим числом строк;
  * ``read_template(path)`` — обратный ход: читает ЛЮБОЙ такой файл
    (наш бланк, присланный файл или только что сгенерированный) и
    возвращает те же данные в виде
    ``{"zayavka": {...}, "vehicles": [{...}, ...]}``.

То есть генератор работает в обе стороны: и заполняет файл, и разбирает
его обратно.

ГЛАВНОЕ РЕШЕНИЕ: МАТЧИНГ ПО ЗАГОЛОВКАМ, А НЕ ПО БУКВАМ КОЛОНОК
--------------------------------------------------------------
Разные заказчики присылают файлы с разным порядком колонок, поэтому
колонка нигде не задаётся буквой: ``_header_columns()`` строит карту
«поле → буква колонки» по строке шапки (шапка ищется по заголовку
«Номер Лота») и дальше вся работа идёт только через неё. Порядок колонок
в присланном файле может быть любым — данные встанут на свои места.

Заголовки узнаются мягко: убираются крайние пробелы, BOM, переносы строк
и хвостовое двоеточие. Точного совпадения с эталоном не требуем: файл
приходит от заказчика, и «Время погрузки » с лишним пробелом — не повод
отказываться от работы. Чего в присланном файле не нашлось — перечисляется
в логе (WARNING), а соответствующее поле остаётся пустым: выдумывать
данные нельзя, но и падать на одной нестандартной шапке незачем.
Жёсткая проверка есть только на нашем бланке (``strict_headers``): эталон
мы собираем сами, и расхождение в нём — наша поломка, а не чужая шапка.

ЧЕГО В БЛАНКЕ НЕТ
-----------------
Поля схемы промпта (core/prompts/havaly.py), для которых в форме нет
отдельной ячейки, в файл не пишутся и при чтении возвращаются пустыми
(``vat_rate`` — значение по умолчанию из промпта):

  * ``vat_rate`` («22%») — в бланке есть только колонка «Ставка с НДС»;
  * ``customer_name`` — Заказчик в бланке напечатан в нижнем блоке
    («Сюрлогистик»), от заявки к заявке не меняется;
  * ``carrier_name`` — Перевозчик там же (ООО «ТЕХНОЛОГИСТИКА»);
  * ``lot_number`` — «Номер Лота» повторяется в каждой строке таблицы,
    это поле строки, а не общие сведения заявки.

Колонка «Наименование транспортной компании» (K) не заполняется: в схеме
промпта для неё нет поля — перевозчик назван полем ``carrier_name`` и
напечатан в бланке. Читается она тоже в никуда.

СТИЛИ
-----
Зелёные обводки из присланного файла не воспроизводятся: их в образце и
нет (см. tools/make_havaly_template.py). Форматирование присланного файла
НЕ ломается: свои стили накладываются ровно на те ячейки, которые
генератор заполняет, и только там, где без них значение читается неверно
(числовой формат даты, перенос длинного текста). Остальное оформление —
рамки, шрифты, ширины колонок, высоты строк — остаётся как было.

При генерации из НАШЕГО бланка лишние пустые строки таблицы обрезаются
(core/contracts/zayavka/postprocess.py): машин в заявке обычно меньше
десяти, и «хвост» из пустых строк в готовом файле не нужен. При
``fill_from_template`` работают те же шаги: если в присланном файле строк
больше, чем машин в данных, лишние удаляются.

ЛОГИ БЕЗ ПДН
------------
В лог попадают только имена полей, количества и длины: ни ФИО, ни VIN,
ни номеров, ни адресов. Значения ячеек не логируются вообще
(``_log_cell`` — координата, имя поля и длина значения).
"""

import logging
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Alignment
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from core.contracts.contract_types import ContractType
from core.contracts.paths import OUTPUT_DIR, TEMPLATES_DIR
from core.contracts.zayavka.postprocess import TrimVehicleRowsStep
from core.dates import parse_date
from core.prompts.havaly import PROMPT

logger = logging.getLogger("core.contract_generator")

TITLE = "Заявка на перевозку авто (Excel)"

# ─────────────────────────────────────────────────────────────
# Файлы, лист и шапка
# ─────────────────────────────────────────────────────────────

#: Эталонный пустой бланк в templates/ (ЭТАП 3.1.E.A.1).
TEMPLATE_NAME = "shablon_havaly.xlsx"

#: Префикс имени готового файла: output/Заявка_Хавалы_<дата ISO>.xlsx.
FILE_PREFIX = "Заявка_Хавалы"

#: Лист всегда один и всегда с этим именем (требование заказчика).
SHEET_NAME = "TDSheet"

#: Заголовок колонки, по которому ищется строка шапки: она в форме первая.
FIRST_HEADER = "Номер Лота"

#: Подпись поля даты заявки над таблицей (ищется по ТЕКСТУ, не по строке).
DATE_LABEL = "Дата заявки"

#: Подпись нижнего блока сторон: по ней видно, где кончается таблица.
PARTY_LABEL = "Заказчик"

#: Подписи сторон в нижнем блоке (в бланке — колонки C и J).
PARTY_LABELS: Tuple[str, ...] = ("Заказчик", "Перевозчик")

#: Сколько строк бланка стоит между последней строкой таблицы и подписью
#: «Заказчик»: две пустые строки и строка самой подписи. По этому числу
#: генератор находит конец таблицы — в образце заказчика ровно так.
PARTY_TAIL_ROWS = 3

#: Максимум машин = максимум строк данных бланка (в эталоне их 10).
MAX_VEHICLES = 10

#: Сколько строк просматривается в поисках подписи «Дата заявки:».
DATE_SEARCH_LIMIT = 30

#: Заголовки эталонного бланка, A..AF (порядок и тексты — из образца).
#: Единственный источник списка колонок для этого модуля; те же тексты
#: лежат в tools/make_havaly_template.py и tests/test_havaly_template.py.
HEADERS: Tuple[str, ...] = (
    "Номер Лота",                           # A
    "VIN",                                  # B
    "Марка",                                # C
    "Модель",                               # D
    "Город погрузки",                       # E
    "Пункт погрузки",                       # F
    "Город доставки",                       # G
    "Пункт разгрузки",                      # H
    "Дилер",                                # I
    "Код дилера",                           # J
    "Наименование транспортной компании",   # K
    "Марка Автовоза",                       # L
    "Цвет кабины",                          # M
    "Номер автовоза",                       # N
    "Марка прицепа",                        # O
    "Номер Прицепа",                        # P
    "Фамилия",                              # Q
    "Имя",                                  # R
    "Отчество",                             # S
    "Номер В/У",                            # T
    "Дата выдачи В/У",                      # U
    "Серия Паспорта",                       # V
    "Номер Паспорта",                       # W
    "Кем выдан паспорт",                    # X
    "Дата выдачи паспорта",                 # Y
    "Гражданство",                          # Z
    "Дата Рождения",                        # AA
    "Прописка",                             # AB
    "Планируемая дата\n погрузки",          # AC — перенос строки как в образце
    "Время погрузки",                       # AD
    "№ телефона водителя",                  # AE
    "Ставка с НДС",                         # AF
)

#: Текст заголовка по имени поля схемы: ключ — поле JSON, значение —
#: заголовок бланка. Все тексты берутся ИЗ HEADERS, а не набираются руками:
#: опечатка в этом словаре иначе тихо развела бы чтение и запись.
HEADER_OF: Mapping[str, str] = {
    "lot_number": HEADERS[0],
    "vin": HEADERS[1],
    "brand": HEADERS[2],
    "model": HEADERS[3],
    "loading_city": HEADERS[4],
    "loading_point": HEADERS[5],
    "unloading_city": HEADERS[6],
    "unloading_point": HEADERS[7],
    "dealer": HEADERS[8],
    "dealer_code": HEADERS[9],
    "tractor_brand": HEADERS[11],
    "tractor_color": HEADERS[12],
    "tractor_plate": HEADERS[13],
    "trailer_brand": HEADERS[14],
    "trailer_plate": HEADERS[15],
    "driver_last_name": HEADERS[16],
    "driver_first_name": HEADERS[17],
    "driver_middle_name": HEADERS[18],
    "driver_license_number": HEADERS[19],
    "driver_license_issue_date": HEADERS[20],
    "driver_passport_series": HEADERS[21],
    "driver_passport_number": HEADERS[22],
    "driver_passport_issuer": HEADERS[23],
    "driver_passport_issue_date": HEADERS[24],
    "driver_citizenship": HEADERS[25],
    "driver_birth_date": HEADERS[26],
    "driver_registration": HEADERS[27],
    "loading_plan_date": HEADERS[28],
    "loading_plan_time": HEADERS[29],
    "driver_phone": HEADERS[30],
    "price_with_vat": HEADERS[31],
}

#: Поля машины: колонки, которые в бланке отличаются от строки к строке.
#: Порядок — как в схеме промпта ("vehicles" в core/prompts/havaly.py).
VEHICLE_FIELD_HEADERS: Tuple[Tuple[str, str], ...] = (
    ("vin", HEADER_OF["vin"]),
    ("brand", HEADER_OF["brand"]),
    ("model", HEADER_OF["model"]),
    ("dealer", HEADER_OF["dealer"]),
    ("dealer_code", HEADER_OF["dealer_code"]),
)

#: Поля заявки, которые бланк раскладывает ПО СТРОКАМ таблицы: пишутся
#: в каждую строку (как в образце), читаются из первой заполненной.
PER_ROW_ZAYAVKA_FIELDS: Tuple[str, ...] = (
    "lot_number", "loading_city", "loading_point", "unloading_city",
    "unloading_point", "tractor_brand", "tractor_color", "tractor_plate",
    "trailer_brand", "trailer_plate", "driver_last_name", "driver_first_name",
    "driver_middle_name", "driver_license_number", "driver_license_issue_date",
    "driver_passport_series", "driver_passport_number", "driver_passport_issuer",
    "driver_passport_issue_date", "driver_citizenship", "driver_birth_date",
    "driver_registration", "loading_plan_date", "loading_plan_time",
    "driver_phone", "price_with_vat",
)

#: Поля, которые генератор в файл НЕ пишет: в бланке для них нет ячейки
#: (см. docstring модуля). При чтении возвращаются пустыми, ``lot_number``
#: берётся из строки таблицы.
UNMAPPED_ZAYAVKA_FIELDS: Tuple[str, ...] = (
    "vat_rate", "customer_name", "carrier_name",
)

#: Поля заявки, которых нет в бланке вовсе (нет и в HEADER_OF).
ABSENT_ZAYAVKA_KEYS: Tuple[str, ...] = (
    "vat_rate", "customer_name", "carrier_name",
)

#: Поля машины, которых нет в бланке вовсе.
ABSENT_VEHICLE_KEYS: Tuple[str, ...] = ()

#: Колонки, в которых текст длиннее ширины колонки: включаем перенос,
#: иначе адрес, прописка и «кем выдан паспорт» обрежутся на границе ячейки.
WRAP_HEADERS: Tuple[str, ...] = (
    HEADER_OF["loading_point"],
    HEADER_OF["unloading_point"],
    HEADER_OF["dealer"],
    HEADER_OF["tractor_brand"],
    HEADER_OF["driver_passport_issuer"],
    HEADER_OF["driver_registration"],
)

#: Числовые форматы заполняемых ячеек: без них Excel покажет порядковый
#: номер дня вместо даты.
DATE_FORMAT = "DD.MM.YYYY"
TIME_FORMAT = "h:mm"

#: Время в документе: «ЧЧ:ММ». Проверяет и валидатор — через эту же
#: функцию, чтобы не разойтись с тем, что генератор считает временем.
TIME_TEXT_PATTERN = re.compile(r"^\d{1,2}:\d{2}$")


def is_document_time(value: Any) -> bool:
    """
    Похоже ли значение на время документа («ЧЧ:ММ»).

    Полное время Excel («09:00:00») тоже подходит: так выглядит значение,
    прочитанное из присланного файла, где время хранится временем, а не
    текстом. Генератор такое значение разбирает и записывает обратно
    временем (см. _time_text).
    """
    text = _text(value)
    if not text:
        return False
    if TIME_TEXT_PATTERN.match(text):
        return True
    match = re.match(r"^(\d{1,2}):(\d{2}):\d{2}$", text)
    if not match:
        return False
    return int(match.group(1)) <= 23 and int(match.group(2)) <= 59

#: Поля-даты: на них накладывается числовой формат.
DATE_FIELDS: Tuple[str, ...] = (
    "date", "driver_license_issue_date", "driver_passport_issue_date",
    "driver_birth_date", "loading_plan_date",
)

#: Поля, значение которых повторяется в КАЖДОЙ строке таблицы: даты и
#: время. Пустая ячейка у них означает потерю значения при обрезке строк,
#: поэтому они пишутся во все строки (см. _repeat_value_fields).
VALUE_FIELDS: Tuple[str, ...] = DATE_FIELDS + ("loading_plan_time",)

#: Стороны заявки: в бланке напечатаны, от заявки к заявке не меняются.
#: Значения — из схемы промпта (core/prompts/havaly.py).
CUSTOMER_NAME = "Сюрлогистик"
CARRIER_NAME = "ООО ТЕХНОЛОГИСТИКА"

#: Заголовки «нет значения» в колонке VIN — как в промпте.
VIN_STUBS = {
    "нет", "нет.", "-", "--", "—", "уточняется", "не указан", "не указано",
    "vin по факту погрузки", "по факту погрузки", "отсутствует", "unknown",
    "null", "none", "nan",
}

#: Мусор в денежных значениях: пробелы, знаки валюты, проценты.
_MONEY_NOISE = ("\u00a0", "\u202f", " ", "₽", "р.", "руб.", "руб", "%")


class ZayavkaTemplateError(RuntimeError):
    """Структурная ошибка бланка: файл, лист TDSheet или строка шапки."""


# ─────────────────────────────────────────────────────────────
# Приведение значений
# ─────────────────────────────────────────────────────────────

def _text(value: Any) -> str:
    """
    Значение строкой без крайних пробелов.

    ``None`` — пустая строка, числовой ноль остаётся «0»: пустое значение
    и явный ноль — разные вещи (правило проекта, грабли 2B.4).
    """
    return "" if value is None else str(value).strip()


def _normalize_header(value: Any) -> str:
    """
    Ключ заголовка для сравнения: без крайних пробелов, BOM, переносов
    строк, хвостового двоеточия и лишних пробелов внутри.
    """
    if value is None:
        return ""
    text = str(value).replace("\ufeff", "").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(":").strip()


def _money(value: Any) -> float:
    """
    Число из значения любого вида: «1 234,56 руб.», «22%», 1234.56, 0.

    Пустое и непонятное значение даёт 0.0 — как и требует схема промпта
    («если ставка не указана — 0.0»).
    """
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    for noise in _MONEY_NOISE:
        text = text.replace(noise, "")
    if not text:
        return 0.0
    text = text.replace(",", ".")
    if text.count(".") > 1:
        head, _, tail = text.rpartition(".")
        text = head.replace(".", "") + "." + tail
    try:
        return float(text)
    except ValueError:
        return 0.0


def _date_text(value: Any) -> str:
    """Дата в формате ДД.ММ.ГГГГ (как в документе); непонятное — как есть."""
    text = _text(value)
    if not text:
        return ""
    parsed = parse_date(value, warn=False)
    return parsed.strftime("%d.%m.%Y") if parsed else text


def _date_value(value: Any) -> Optional[date]:
    """Дата объектом — для записи в Excel; непонятное значение не пишем."""
    parsed = parse_date(value, warn=False) if _text(value) else None
    return parsed.date() if parsed else None


def _time_text(value: Any) -> str:
    """
    Время в формате «ЧЧ:ММ».

    Принимает и время Excel (``datetime.time``), и «9:00», и «09:00:00»:
    форма заказчика допускает любую запись, а схема промпта требует одну.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, (datetime, time)):
        return value.strftime("%H:%M")

    text = _text(value)
    match = re.match(r"^(\d{1,2})[:.\s](\d{1,2})", text)
    if match:
        hours, minutes = int(match.group(1)), int(match.group(2))
        if 0 <= hours <= 23 and 0 <= minutes <= 59:
            return f"{hours:02d}:{minutes:02d}"
    return text


def _time_value(value: Any) -> Optional[time]:
    """Время объектом — для записи в Excel (числовой формат «h:mm»)."""
    match = re.match(r"^(\d{1,2}):(\d{2})$", _time_text(value))
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return time(hour=hours, minute=minutes)


def _vin(value: Any) -> str:
    """
    VIN без пробелов; заглушки («нет», прочерк, «Vin по факту погрузки»)
    дают пустую строку — ровно как требует промпт.
    """
    text = _text(value)
    if not text:
        return ""
    # Заглушку узнаём ДО удаления пробелов: «Vin по факту погрузки» без
    # пробелов перестало бы совпадать со списком.
    if re.sub(r"\s+", " ", text).strip().lower() in VIN_STUBS:
        return ""
    return re.sub(r"\s+", "", text)


def _as_dict(value: Any) -> Dict[str, Any]:
    """Значение словарём: не словарь — пустой словарь."""
    return dict(value) if isinstance(value, Mapping) else {}


# ─────────────────────────────────────────────────────────────
# Описание полей: куда писать и откуда читать
# ─────────────────────────────────────────────────────────────

class FieldSpec:
    """
    Одно поле заявки: заголовок колонки + чтение и запись значения.

    ``write(raw)`` отдаёт то, что кладётся в ячейку (None — не писать),
    ``read(raw)`` — то, что возвращается в JSON. Спецификации лежат
    таблицей ниже, поэтому чтение и запись не могут разойтись: одна и та
    же строка в файл и из файла описана в одном месте.
    """

    __slots__ = ("key", "header", "reader", "writer")

    def __init__(
        self,
        key: str,
        header: str,
        reader: Callable[[Any], Any] = _text,
        writer: Optional[Callable[[Any], Any]] = None,
    ):
        self.key = key
        self.header = header
        self.reader = reader
        self.writer = writer

    def read(self, raw: Any) -> Any:
        return self.reader(raw)

    def write(self, raw: Any) -> Any:
        """Значение для ячейки; None — ячейку не трогаем."""
        if raw is None:
            return None
        write = self.writer or (lambda value: _text(value) or None)
        return write(raw)

    def __repr__(self) -> str:  # без значений: только имя поля
        return f"FieldSpec({self.key!r})"


#: Заголовок поля даты заявки: своей колонки у неё нет, значение лежит
#: в ячейке справа от подписи «Дата заявки:» над таблицей. Этот заголовок
#: стоит только у поля ``date``: остальные даты (выдача В/У, паспорта,
#: рождение, план погрузки) — обычные колонки таблицы.
DATE_HEADER = DATE_LABEL


def _date_spec(key: str) -> FieldSpec:
    """Поле-дата: пишется датой, читается строкой ДД.ММ.ГГГГ."""
    return FieldSpec(key, HEADER_OF[key], reader=_date_text, writer=_date_value)


def _time_spec(key: str) -> FieldSpec:
    """Поле-время: пишется временем, читается строкой «ЧЧ:ММ»."""
    return FieldSpec(key, HEADER_OF[key], reader=_time_text, writer=_time_value)


def _money_spec(key: str) -> FieldSpec:
    """Поле-ставка: пишется и читается числом (0.0 — «не указана»)."""
    return FieldSpec(
        key, HEADER_OF[key],
        reader=_money,
        writer=lambda raw: _money(raw) or None,
    )


def _build_field_specs() -> Tuple[FieldSpec, ...]:
    """
    Таблица полей заявки в порядке колонок бланка.

    Большинство полей — строка; даты, время и ставка разбираются и
    пишутся своими типами. Полей без ячейки в таблице нет: они
    перечислены в UNMAPPED_ZAYAVKA_FIELDS.

    У поля ``date`` заголовок особый (DATE_HEADER): своей колонки у даты
    заявки нет, координата берётся из подписи над таблицей. У остальных
    дат заголовки обычные — это колонки U, Y, AA и AC.
    """
    builders: Mapping[str, Callable[[str], FieldSpec]] = {
        "driver_license_issue_date": _date_spec,
        "driver_passport_issue_date": _date_spec,
        "driver_birth_date": _date_spec,
        "loading_plan_date": _date_spec,
        "loading_plan_time": _time_spec,
        "price_with_vat": _money_spec,
    }
    # Порядок — как в шапке бланка: отчёт о ненайденных колонках читается
    # слева направо.
    order = ("date",) + PER_ROW_ZAYAVKA_FIELDS
    return tuple(
        FieldSpec("date", DATE_HEADER, reader=_date_text, writer=_date_value)
        if key == "date"
        else builders[key](key) if key in builders
        else FieldSpec(key, HEADER_OF[key])
        for key in order
    )


#: Все поля заявки, которые бланк умеет хранить, в порядке колонок.
FIELD_SPECS: Tuple[FieldSpec, ...] = _build_field_specs()

#: Поля машины: пять колонок таблицы; все текстовые, VIN чистится.
VEHICLE_SPECS: Tuple[FieldSpec, ...] = tuple(
    FieldSpec(key, header, reader=_vin, writer=lambda raw: _vin(raw) or None)
    if key == "vin" else FieldSpec(key, header)
    for key, header in VEHICLE_FIELD_HEADERS
)

#: Поле по имени — быстрый поиск для записи и оформления.
SPEC_BY_KEY: Mapping[str, FieldSpec] = {
    spec.key: spec for spec in FIELD_SPECS + VEHICLE_SPECS
}

#: Ключи JSON, которые генератор кладёт в блок "zayavka" (порядок колонок).
ZAYAVKA_KEYS: Tuple[str, ...] = tuple(spec.key for spec in FIELD_SPECS)

#: Порядок ключей блока "zayavka" — ровно как в «СХЕМЕ ОТВЕТА» промпта
#: (core/prompts/havaly.py), включая поля, для которых в бланке нет ячейки
#: (vat_rate и стороны). Ответ генератора должен читаться так же, как ответ
#: распознавания: один и тот же документ — одна и та же схема.
ZAYAVKA_SCHEMA_ORDER: Tuple[str, ...] = (
    "date", "lot_number",
    "loading_city", "loading_point", "unloading_city", "unloading_point",
    "carrier_name", "customer_name",
    "tractor_brand", "tractor_color", "tractor_plate",
    "trailer_brand", "trailer_plate",
    "driver_last_name", "driver_first_name", "driver_middle_name",
    "driver_license_number", "driver_license_issue_date",
    "driver_passport_series", "driver_passport_number",
    "driver_passport_issuer", "driver_passport_issue_date",
    "driver_citizenship", "driver_birth_date", "driver_registration",
    "driver_phone",
    "loading_plan_date", "loading_plan_time",
    "price_with_vat", "vat_rate",
)

#: Ключи JSON, которые генератор кладёт в элемент "vehicles".
VEHICLE_KEYS: Tuple[str, ...] = tuple(spec.key for spec in VEHICLE_SPECS)


class ZayavkaExcelGenerator:
    """
    Заполняет Excel-форму заявки Хавалов и читает её обратно.

    Соблюдает контракт фабрики (принимает templates_dir), но от DOCX-базы
    не наследуется: выход — .xlsx. Публичные методы:

      * ``read_template(path)`` → ``{"zayavka": {...}, "vehicles": [...]}``;
      * ``fill_from_template(path, data, output_dir)`` → путь готового файла;
      * ``generate(data, output_dir)`` → путь готового файла.

    Ни один из них не трогает исходный файл: чтение идёт через openpyxl,
    запись — всегда в новый файл в output_dir.
    """

    CONTRACT_TYPE = ContractType.ZAYAVKA_EXCEL.value

    # ── Контракт типа (имена те же, что у DOCX-типов: их читает UI) ──
    TITLE = TITLE

    #: Человеческое имя бланка — как TEMPLATE_NAMES у DOCX-типов, только
    #: ключ один: у Хавалов одна форма, вариантов нет.
    TEMPLATE_NAMES: Mapping[str, str] = {"zayavka": TEMPLATE_NAME}

    #: Промпт распознавания этого типа: он описывает ровно те поля,
    #: которые заполняет генератор (единый источник имён ключей).
    PROMPT = PROMPT

    #: Сколько машин помещается в бланк (строк данных в эталоне).
    MAX_VEHICLES = MAX_VEHICLES

    def __init__(self, templates_dir: str = ""):
        self.templates_dir = templates_dir or str(TEMPLATES_DIR)
        self.templates = {
            key: str(Path(self.templates_dir) / filename)
            for key, filename in self.TEMPLATE_NAMES.items()
        }

        # Номер строки шапки — заполняется при разборе листа.
        self._header_row: int = 0

    # ─────────────────────────────────────────────────────────
    # Служебное
    # ─────────────────────────────────────────────────────────

    @classmethod
    def default_output_dir(cls) -> str:
        """Папка готовых файлов: <корень проекта>/output."""
        return str(OUTPUT_DIR)

    def template_path(self) -> str:
        """Путь эталонного бланка в templates_dir."""
        return self.templates["zayavka"]

    @staticmethod
    def _log_cell(what: str, cell: Any, value: Any) -> None:
        """
        Лог об одной записанной ячейке — без значения.

        Пишутся координата, имя поля и ДЛИНА значения: по длине видно,
        что данные доехали, а ФИО, VIN и номера в лог не попадают.
        """
        length = len(str(value)) if value is not None else 0
        logger.debug(f"{what}: {cell.coordinate}, длина значения={length}")

    # ─────────────────────────────────────────────────────────
    # ЧТЕНИЕ БЛАНКА
    # ─────────────────────────────────────────────────────────

    def read_template(self, path: str) -> Dict[str, Any]:
        """
        Читает Excel-форму заявки и возвращает её данные.

        Геометрия не задана номерами строк: подпись «Дата заявки:» ищется
        по тексту, шапка — по заголовку «Номер Лота», конец таблицы — по
        пустой строке (одновременно пусты «Номер Лота», «VIN» и «Марка»)
        или по началу нижнего блока «Заказчик».

        :returns: ``{"zayavka": {...}, "vehicles": [...]}``. Ненайденные
            поля — пустые строки, ставка — 0.0, машин нет — пустой список.
        :raises ZayavkaTemplateError: файл не открывается, листа TDSheet
            нет, строка шапки не распознана.
        """
        source = Path(path)
        sheet = self._load_sheet(source)
        columns = self._header_columns(sheet, strict=False)
        header_row = self._header_row

        zayavka = self._read_zayavka(sheet, columns, header_row)
        vehicles = self._read_vehicles(sheet, columns, header_row)

        self._log_read(source, zayavka, vehicles)
        return {"zayavka": zayavka, "vehicles": vehicles}

    def _load_sheet(self, path: Path) -> Worksheet:
        """Открывает книгу и отдаёт лист TDSheet (иначе — понятная ошибка)."""
        if not path.exists():
            raise ZayavkaTemplateError(f"Файл заявки не найден: {path}")

        try:
            workbook = load_workbook(str(path), data_only=True)
        except Exception as error:  # noqa: BLE001 — наружу нужна понятная ошибка
            raise ZayavkaTemplateError(
                f"Не удалось открыть файл заявки {path.name}: "
                f"{type(error).__name__}: {error}"
            ) from error

        if SHEET_NAME not in workbook.sheetnames:
            raise ZayavkaTemplateError(
                f"В файле {path.name} нет листа {SHEET_NAME}: "
                f"листы — {', '.join(workbook.sheetnames) or 'нет'}"
            )
        return workbook[SHEET_NAME]

    def _header_columns(self, sheet: Worksheet, strict: bool) -> Dict[str, str]:
        """
        Карта «поле → буква колонки», построенная по строке шапки.

        Шапка ищется по заголовку FIRST_HEADER: в форме это первая колонка
        таблицы, и по ней же определяется номер строки шапки —
        ``self._header_row``.

        :param strict: True (наш бланк) — расхождение с эталоном это
            ошибка; False (присланный файл) — отсутствующие заголовки
            перечисляются в логе, поле остаётся пустым.
        """
        header_row = self._find_row_by_text(sheet, FIRST_HEADER)
        if header_row is None:
            raise ZayavkaTemplateError(
                f"В листе {SHEET_NAME} не найдена строка шапки: "
                f"нет колонки «{FIRST_HEADER}»"
            )
        self._header_row = header_row

        # Заголовок → буква колонки. Тексты ячеек шапки в лог не пишем:
        # это содержимое документа, а не наши данные.
        positions: Dict[str, str] = {}
        for row in sheet.iter_rows(min_row=header_row, max_row=header_row):
            for cell in row:
                key = _normalize_header(cell.value)
                if key and key not in positions:
                    positions[key] = get_column_letter(cell.column)

        columns: Dict[str, str] = {}
        # Поля без ячейки (vat_rate, стороны) в проверке не участвуют: их
        # отсутствие — не дефект шапки (см. UNMAPPED_ZAYAVKA_FIELDS).
        candidates = [
            spec for spec in FIELD_SPECS + VEHICLE_SPECS
            if spec.key not in UNMAPPED_ZAYAVKA_FIELDS
        ]

        missing: List[str] = []
        for spec in candidates:
            if spec.key in columns:
                continue
            if spec.key == "date":
                # У даты заявки нет своей колонки: она лежит в ячейке
                # справа от подписи «Дата заявки:» (см. _date_label_cell).
                continue
            column = positions.get(_normalize_header(spec.header))
            if column:
                columns[spec.key] = column
            else:
                missing.append(spec.header)

        expected = len([spec for spec in candidates if spec.key != "date"])
        if missing:
            if strict:
                raise ZayavkaTemplateError(
                    f"В шапке листа {SHEET_NAME} (эталонный бланк) нет колонок: "
                    + ", ".join(missing)
                )
            logger.warning(
                "В шапке листа %s не найдено %d колонок из %d: %s",
                SHEET_NAME, len(missing), expected, ", ".join(missing),
            )

        logger.debug(
            "Шапка листа %s: строка %d, распознано колонок=%d, не найдено=%d",
            SHEET_NAME, header_row, len(columns), len(missing),
        )
        return columns

    def _read_zayavka(
        self,
        sheet: Worksheet,
        columns: Mapping[str, str],
        header_row: int,
    ) -> Dict[str, Any]:
        """
        Общие сведения заявки.

        Берутся из ПЕРВОЙ заполненной строки таблицы — бланк повторяет их
        в каждой строке, а относятся они ко всей заявке (как требует
        промпт). Дата — из строки «Дата заявки:» над таблицей.

        Поля-строки читаются до первого заполненного значения: в
        присланном файле одна машина может стоять с полным адресом, а
        следующая — с пустой клеткой, и «первая заполненная строка» не
        должна из-за этого обнулять уже найденное.

        Стороны (``customer_name`` / ``carrier_name``) из формы НЕ
        читаются, а подставляются константами: в бланке они напечатаны в
        нижнем блоке и от заявки к заявке не меняются — ровно поэтому в
        промпте они фиксированы. Так результат чтения совпадает с ответом
        распознавания даже тогда, когда присланный файл обрезан или блок
        сторон из него убрали.
        """
        result: Dict[str, Any] = {key: "" for key in ZAYAVKA_KEYS}
        result["date"] = self._read_date_label(sheet, header_row)
        result["price_with_vat"] = 0.0

        rows = list(self._data_rows(sheet, columns, header_row))
        if not rows:
            logger.info("Заявка: строк с машинами нет, таблица пуста")
        for spec in FIELD_SPECS:
            if spec.key == "date" or spec.key not in columns:
                continue
            column = columns[spec.key]
            for row in rows:
                value = spec.read(sheet[f"{column}{row}"].value)
                if value != "" and value != 0.0:
                    result[spec.key] = value
                    break

        if not result["date"]:
            # Дата заявки не подписана — берём планируемую дату погрузки:
            # в форме это та же дата.
            result["date"] = _date_text(result["loading_plan_date"])

        # Стороны фиксированы и лежат плоскими полями (core/prompts/havaly.py):
        # из формы они не извлекаются, а подставляются константами — так
        # чтение даёт ровно тот ответ, которое вернуло бы распознавание.
        result["customer_name"] = CUSTOMER_NAME
        result["carrier_name"] = CARRIER_NAME
        return self._ordered_zayavka(result)

    @staticmethod
    def _ordered_zayavka(result: Mapping[str, Any]) -> Dict[str, Any]:
        """
        Блок заявки в порядке схемы промпта — вместе с полями без ячейки.

        Порядок ключей — как в «СХЕМЕ ОТВЕТА» core/prompts/havaly.py: ответ
        генератора и ответ распознавания должны читаться одинаково. Поля,
        которых в бланке нет, возвращаются пустыми (для сторон — своими
        фиксированными значениями), но СВОИМИ ключами и на своих местах.
        """
        answer = {
            key: result.get(key, "")
            for key in ZAYAVKA_SCHEMA_ORDER
        }
        answer["price_with_vat"] = _money(result.get("price_with_vat"))
        answer["customer_name"] = result.get("customer_name") or CUSTOMER_NAME
        answer["carrier_name"] = result.get("carrier_name") or CARRIER_NAME
        return answer

    def _read_vehicles(
        self,
        sheet: Worksheet,
        columns: Mapping[str, str],
        header_row: int,
    ) -> List[Dict[str, Any]]:
        """
        Перевозимые машины: одна строка таблицы — одна машина.

        Строка считается машиной, если заполнено хоть одно из пяти её
        полей: VIN может быть неизвестен («Vin по факту погрузки»), но
        марка, модель, дилер или код дилера такую машину выдают.
        """
        vehicles: List[Dict[str, Any]] = []
        for row in self._data_rows(sheet, columns, header_row):
            vehicle = {
                spec.key: (
                    spec.read(sheet[f"{columns[spec.key]}{row}"].value)
                    if spec.key in columns else ""
                )
                for spec in VEHICLE_SPECS
            }
            if not any(_text(value) for value in vehicle.values()):
                break
            vehicles.append(vehicle)

        if len(vehicles) > MAX_VEHICLES:
            # В бланк помещается 10 строк: лишние в файл не попадут.
            logger.warning(
                "В файле %d строк машин — в бланк помещается %d, лишние отброшены",
                len(vehicles), MAX_VEHICLES,
            )
            vehicles = vehicles[:MAX_VEHICLES]

        return vehicles

    def _data_rows(
        self,
        sheet: Worksheet,
        columns: Mapping[str, str],
        header_row: int,
    ) -> Iterator[int]:
        """
        Номера строк таблицы: от шапки до её конца.

        Конец таблицы — пустая строка (одновременно пусты «Номер Лота»,
        «VIN» и «Марка») или строка нижнего блока сторон.
        """
        watch = [columns[spec.key] for spec in VEHICLE_SPECS if spec.key in columns]
        lot_column = columns.get("lot_number")

        for row in range(header_row + 1, sheet.max_row + 1):
            if self._is_party_row(sheet, row, lot_column):
                return
            if not any(_text(sheet[f"{column}{row}"].value) for column in watch):
                return
            yield row

    def _read_date_label(self, sheet: Worksheet, header_row: int) -> str:
        """
        Дата заявки из строки «Дата заявки:».

        Подпись ищется по тексту в строках над шапкой, значение берётся
        из ячейки справа. Значение ячейки в лог не пишется.
        """
        cell = self._date_label_cell(sheet, header_row)
        if cell is None:
            logger.warning("В листе %s не найдена подпись «%s»", SHEET_NAME, DATE_LABEL)
            return ""
        value = sheet.cell(row=cell.row, column=cell.column + 1).value
        return _date_text(value)

    @staticmethod
    def _date_label_cell(sheet: Worksheet, header_row: int) -> Optional[Any]:
        """
        Ячейка с подписью «Дата заявки:» (в эталоне это A5).

        Подпись ищется по тексту, а не по номеру строки: у присланного
        файла она может стоять в другой строке.
        """
        limit = min(header_row or DATE_SEARCH_LIMIT, DATE_SEARCH_LIMIT)
        for row in range(1, limit + 1):
            for cell in sheet[row]:
                if DATE_LABEL.lower() in _normalize_header(cell.value).lower():
                    return cell
        return None

    @staticmethod
    def _is_party_row(sheet: Worksheet, row: int, lot_column: Optional[str]) -> bool:
        """
        Началась ли строка нижнего блока сторон.

        Признак — подпись «Заказчик» в колонке «Номер Лота» или в колонке
        C (как в образце): блок стоит под таблицей, спутать его со
        строкой машины нельзя.
        """
        for column in (lot_column, "C"):
            if not column:
                continue
            value = _normalize_header(sheet[f"{column}{row}"].value)
            if value.lower() == PARTY_LABEL.lower():
                return True
        return False

    # ─────────────────────────────────────────────────────────
    # ЗАПИСЬ В БЛАНК
    # ─────────────────────────────────────────────────────────

    def generate(
        self,
        data: Dict[str, Any],
        output_dir: Optional[str] = None,
    ) -> str:
        """
        Заполняет НАШ эталонный бланк и сохраняет готовый файл.

        По сути это ``fill_from_template`` с источником
        templates/shablon_havaly.xlsx: если бланк пересобрали, генерация
        из эталона подхватит изменения автоматически.
        """
        return self.fill_from_template(self.template_path(), data, output_dir)

    def fill_from_template(
        self,
        path: str,
        data: Dict[str, Any],
        output_dir: Optional[str] = None,
    ) -> str:
        """
        Заполняет присланный .xlsx данными заявки и сохраняет результат.

        Форматирование присланного файла сохраняется: свои стили
        накладываются только на заполняемые ячейки (числовой формат даты,
        перенос длинного текста). Пустые строки данных, оставшиеся от
        прежнего заполнения, обрезаются (postprocess.py).

        :param path: присланный или эталонный .xlsx.
        :param data: ``{"zayavka": {...}, "vehicles": [...]}``.
        :param output_dir: куда положить готовый файл (по умолчанию output/).
        :returns: путь готового файла.
        :raises ZayavkaTemplateError: бланк структурно непригоден.
        """
        source = Path(path)
        payload = self._payload(data)

        sheet = self._load_sheet(source)
        columns = self._header_columns(
            sheet, strict=self._is_own_template(source)
        )

        target_dir = Path(output_dir) if output_dir else Path(self.default_output_dir())
        target = target_dir / self.get_filename(payload)
        self._guard_same_file(source, target)

        written = self._write_payload(sheet, columns, payload)
        self._trim_rows(sheet, payload)
        self._save(sheet, target)

        logger.info(
            "Заявка Хавалов: файл=%s, заполнено ячеек=%d, машин=%d",
            target.name, written, len(payload["vehicles"]),
        )
        return str(target)

    def get_filename(self, data: Mapping[str, Any]) -> str:
        """Имя готового файла: Заявка_Хавалы_<дата ISO>.xlsx."""
        zayavka = _as_dict(data.get("zayavka")) if isinstance(data, Mapping) else {}
        parsed = parse_date(zayavka.get("date"), warn=False)
        stamp = parsed or datetime.now()
        return f"{FILE_PREFIX}_{stamp.strftime('%Y-%m-%d')}.xlsx"

    def _payload(self, data: Any) -> Dict[str, Any]:
        """
        Данные генератора из произвольного входа.

        Понимает и ``{"zayavka": {...}, "vehicles": [...]}`` (схема
        промпта A.2), и плоский словарь: распознавание отдаёт блоки
        верхнего уровня, а сборщик UI может передать те же поля внутри
        ``contract``. Если есть и блок, и плоские поля, приоритет у БЛОКА
        ``zayavka``: он и есть схема промпта, а ``contract`` — способ
        донести те же значения через ContractData.
        """
        source = _as_dict(data)
        vehicles = [
            dict(item) for item in (source.get("vehicles") or [])
            if isinstance(item, Mapping)
        ]

        flat = {**_as_dict(source.get("contract")), **source}
        zayavka = {
            key: flat[key]
            for key in ZAYAVKA_KEYS + ABSENT_ZAYAVKA_KEYS
            if key in flat
        }
        # Блок заявки перекрывает плоские поля: если его ключ задан (пусть
        # даже пустой строкой), берётся значение из блока.
        for key, value in _as_dict(source.get("zayavka")).items():
            zayavka[key] = value

        return {"zayavka": zayavka, "vehicles": vehicles}

    def _is_own_template(self, path: Path) -> bool:
        """Наш ли это эталонный бланк: для него шапка проверяется строго."""
        try:
            return path.resolve() == Path(self.template_path()).resolve()
        except OSError:
            return False

    @staticmethod
    def _guard_same_file(source: Path, target: Path) -> None:
        """
        Не даём перезаписать исходный файл: он же может быть присланным.

        Имя вывода зависит только от даты заявки, поэтому теоретически
        может совпасть с именем источника — тогда сохранение затрёт
        присланный бланк.
        """
        try:
            same = source.resolve() == target.resolve()
        except OSError:
            return
        if same:
            raise ZayavkaTemplateError(
                f"Файл {target.name} совпадает с исходным бланком: "
                f"выберите другую папку вывода"
            )

    def _write_payload(
        self,
        sheet: Worksheet,
        columns: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> int:
        """
        Пишет данные в лист и возвращает число заполненных ячеек.

        Строки таблицы идут подряд от шапки: сколько машин, столько строк
        и заполняется. Поля, общие для заявки, бланк повторяет в каждой
        строке — значит, повторяем и мы: иначе в готовом файле одна машина
        окажется с адресом, а другая с пустой клеткой.
        """
        zayavka = _as_dict(payload.get("zayavka"))
        vehicles = list(payload.get("vehicles") or [])
        first_row = self._header_row + 1
        written = 0

        # Дата заявки — отдельная ячейка над таблицей (в эталоне B5).
        label = self._date_label_cell(sheet, self._header_row)
        if label is not None:
            written += self._write_cell(
                sheet, "date", zayavka.get("date"),
                row=label.row, column=label.column + 1,
            )

        if len(vehicles) > MAX_VEHICLES:
            logger.warning(
                "Машин %d — в бланк помещается %d, лишние не записаны",
                len(vehicles), MAX_VEHICLES,
            )
            vehicles = vehicles[:MAX_VEHICLES]

        for offset, vehicle in enumerate(vehicles):
            merged = {**zayavka, **vehicle}
            for spec in FIELD_SPECS + VEHICLE_SPECS:
                if spec.key == "date" or spec.key not in columns:
                    continue
                written += self._write_cell(
                    sheet, spec.key, merged.get(spec.key),
                    row=first_row + offset,
                    column=column_index_from_string(columns[spec.key]),
                )

        if vehicles:
            written += self._repeat_value_fields(
                sheet, columns, zayavka, len(vehicles), first_row
            )

        if not vehicles:
            logger.info(
                "Заявка: машин 0 — заполнена только дата заявки, строки таблицы пусты"
            )
        return written

    def _repeat_value_fields(
        self,
        sheet: Worksheet,
        columns: Mapping[str, str],
        zayavka: Mapping[str, Any],
        count: int,
        first_row: int,
    ) -> int:
        """
        Повторяет поля-ЗНАЧЕНИЯ (даты и время) в каждой строке таблицы.

        Поля-строки бланк тоже повторяет, но там пустая ячейка — это
        «данных нет»: писать в неё нечего, а в присланном файле может
        лежать чужой текст, который затирать нельзя. У даты и времени
        пустая ячейка означает другое: строку таблицы удалят при обрезке,
        и значение пропадёт. Поэтому значения-даты пишутся во все строки —
        ровно так заполнен бланк в образце заказчика.

        :returns: сколько ячеек дописано.
        """
        written = 0
        value_specs = [spec for spec in FIELD_SPECS if spec.key in VALUE_FIELDS]
        for offset in range(1, count):
            for spec in value_specs:
                if spec.key not in columns:
                    continue
                written += self._write_cell(
                    sheet, spec.key, zayavka.get(spec.key),
                    row=first_row + offset,
                    column=column_index_from_string(columns[spec.key]),
                )
        return written

    def _write_cell(
        self,
        sheet: Worksheet,
        key: str,
        raw: Any,
        *,
        row: int,
        column: int,
    ) -> int:
        """
        Пишет одно поле в ячейку. 1 — записали, 0 — писать нечего.

        Ячейка может оказаться частью объединения (в образце так объединена
        ставка AF7:AF12): писать в такую ячейку openpyxl не даёт (она
        read-only), поэтому значение уходит в левую верхнюю ячейку
        диапазона — первую строку таблицы, как это и делает Excel.
        """
        spec = SPEC_BY_KEY[key]
        value = spec.write(raw)
        if value is None:
            return 0

        cell = self._anchor_cell(sheet, row, column)
        cell.value = value
        self._style_filled_cell(cell, key)
        self._log_cell("записано", cell, value)
        return 1

    @staticmethod
    def _anchor_cell(sheet: Worksheet, row: int, column: int) -> Any:
        """Ячейка для записи: сама ячейка или начало её объединения."""
        cell = sheet.cell(row=row, column=column)
        if not isinstance(cell, MergedCell):
            return cell

        for merged in sheet.merged_cells.ranges:
            if cell.coordinate in merged:
                return sheet.cell(row=merged.min_row, column=merged.min_col)

        # Ячейка помечена объединённой, но диапазона нет — такой лист
        # испорчен, и писать в него нельзя.
        raise ZayavkaTemplateError(
            f"Ячейка {cell.coordinate} помечена объединённой, но диапазон не найден"
        )

    @staticmethod
    def _style_filled_cell(cell: Any, key: str) -> None:
        """
        Оформление заполненной ячейки — минимально необходимое.

        Форматирование бланка не переписывается: меняются только два
        свойства, без которых значение читается неверно, — числовой формат
        даты (иначе Excel покажет 46397 вместо 05.10.2026) и перенос
        длинного текста (адреса, прописка, «кем выдан паспорт»).
        """
        if key in DATE_FIELDS:
            cell.number_format = DATE_FORMAT
        elif key == "loading_plan_time":
            cell.number_format = TIME_FORMAT

        spec = SPEC_BY_KEY[key]
        if spec.header in WRAP_HEADERS:
            cell.alignment = Alignment(
                wrap_text=True,
                vertical="center",
                horizontal=cell.alignment.horizontal,
            )

    def _trim_rows(
        self,
        sheet: Worksheet,
        payload: Mapping[str, Any],
    ) -> None:
        """
        Обрезает строки таблицы до числа машин (шаг постобработки).

        Постобработка — украшение готового файла, а не его суть: если шаг
        не сработал, файл всё равно сохраняется (в лог уходит warning).
        Так же ведёт себя база DOCX-типов
        (BaseContractGenerator._postprocess_document).
        """
        for step in self.postprocess_steps():
            logger.debug(f"Постобработка: шаг {step.name}")
            try:
                step.apply(sheet, payload)
            except Exception as error:  # noqa: BLE001 — файл важнее оформления
                logger.warning(
                    "Шаг постобработки %s не выполнен: %s: %s",
                    step.name, type(error).__name__, error,
                )

    def postprocess_steps(self) -> List[TrimVehicleRowsStep]:
        """
        Конвейер постобработки листа.

        Пока шаг один — привести число строк таблицы к числу машин. Шаги
        живут отдельным модулем (core/contracts/zayavka/postprocess.py),
        как у DOCX-типов: набор и порядок задаёт генератор.
        """
        return [TrimVehicleRowsStep(self)]

    def _trim_surplus_vehicle_rows(
        self,
        sheet: Worksheet,
        vehicles: int,
    ) -> None:
        """
        Удаляет строки таблицы, оставшиеся без машин, и поднимает низ бланка.

        Геометрия берётся из самого листа, а не из номеров строк эталона:
        шапка ищется по заголовку «Номер Лота» (ниже неё — по подписи
        «Заказчик»), начало блока сторон — по этой же подписи, последняя
        строка таблицы — за PARTY_TAIL_ROWS строк до неё.

        Удаляются ВСЕ строки таблицы ниже последней записанной машины:
        в присланном файле там могут стоять значения прежней заявки, и
        оставлять их нельзя — это чужие данные в готовом документе. Когда
        машин столько же, сколько строк в бланке (10 из 10 в эталоне),
        удалять нечего, и раскладка файла совпадает с бланком до последней
        строки.

        Низ бланка переезжает наверх вместе с высотами строк, поэтому
        «Заказчик / Перевозчик», место под подписи и «ФИО, подпись,
        печать» остаются на своих местах относительно конца таблицы.
        """
        layout = self._sheet_layout(sheet)
        first_data = layout["first_data"]
        last_table_row = layout["last_table"]
        written = max(int(vehicles), 0)
        last_written = first_data + written - 1

        empty = [
            row for row in range(first_data, last_table_row + 1)
            if row > last_written
        ]
        if not empty:
            logger.debug(
                "Постобработка: строк таблицы %d, машин %d — лишних строк нет",
                last_table_row - first_data + 1, written,
            )
            return

        # Строки, которые переезжают наверх: всё, что ниже удаляемых, вместе
        # с хвостом бланка. Их высоты переносятся на новые позиции.
        tail_start = empty[-1] + 1
        heights = {
            empty[0] + offset: sheet.row_dimensions[row].height
            if row in sheet.row_dimensions else None
            for offset, row in enumerate(range(tail_start, layout["last_row"] + 1))
        }
        removed = len(empty)
        self._drop_row_heights(sheet, empty)

        sheet.delete_rows(empty[0], removed)
        self._shift_merged_ranges(sheet, empty)

        for row, height in heights.items():
            if height is not None:
                sheet.row_dimensions[row].height = height

        logger.info(
            "Постобработка: удалено строк таблицы без машин=%d (машин %d, "
            "строк в таблице было %d), блок сторон переехал на строку %d",
            removed, written, last_table_row - first_data + 1, empty[0],
        )

    def _sheet_layout(self, sheet: Worksheet) -> Dict[str, int]:
        """
        Границы таблицы и нижнего блока по содержимому листа.

        Ключи: ``header`` — строка шапки, ``first_data`` — первая строка
        данных, ``last_table`` — последняя размеченная строка таблицы,
        ``party_start`` — начало нижнего блока (строка «Заказчик» или
        первая строка после таблицы, если подписи нет), ``last_row`` —
        последняя строка листа.

        Шапка и блок сторон ищутся в НИЖНЕЙ части листа: в присланном
        файле сверху может стоять шапка другой таблицы, и «Номер Лота» из
        неё — не тот заголовок, по которому мы заполняем заявку.

        Конец таблицы считается от подписи «Заказчик»: между последней
        строкой таблицы и ею всегда стоят PARTY_TAIL_ROWS строк (две
        пустые и сама подпись). По содержимому его не найти — пустые
        строки есть и внутри таблицы, и в хвосте.
        """
        start = self._header_row or 1
        header = self._find_row_by_text(sheet, FIRST_HEADER, start=start) or start
        party_start = (
            self._find_row_by_text(sheet, PARTY_LABEL, start=header)
            or (sheet.max_row + 1)
        )
        last_table = max(party_start - PARTY_TAIL_ROWS, header)

        return {
            "header": header,
            "first_data": header + 1,
            "last_table": last_table,
            "party_start": party_start,
            "last_row": sheet.max_row,
        }

    @staticmethod
    def _drop_row_heights(sheet: Worksheet, rows: Sequence[int]) -> None:
        """
        Убирает из книги высоты удаляемых строк.

        ``del sheet.row_dimensions[row]`` здесь не годится: DimensionHolder
        сам сдвигает ключи ниже удалённого вниз (openpyxl 3.1.5), и второе
        подряд удаление падает с KeyError. Поэтому объекты нужных строк
        берутся заранее, а хранилище пересобирается без них.
        """
        removed = set(rows)
        kept = [
            item for key, item in sheet.row_dimensions.items()
            if key not in removed
        ]
        sheet.row_dimensions.clear()
        for item in kept:
            sheet.row_dimensions[item.index] = item

    @staticmethod
    def _shift_merged_ranges(sheet: Worksheet, rows: Sequence[int]) -> None:
        """
        Пересобирает объединения после удаления строк.

        ``delete_rows`` объединённые ячейки не двигает: без этой правки
        диапазон, целиком лежащий ниже удалённых строк (в образце так
        объединена ставка «на все строки» AF7:AF12), остался бы на прежнем
        месте и накрыл бы уже другие строки.

        Диапазон, который удаление не задело, сохраняется как есть; тот,
        что удаление пересекло, укорачивается — часть выше разреза
        остаётся на месте, часть ниже поднимается на число удалённых
        строк. Диапазон, который удалился целиком, исчезает.
        """
        removed = set(rows)
        rebuilt: List[Tuple[int, int, int, int]] = []
        touched: List[Any] = []

        for merged in sheet.merged_cells.ranges:
            span = range(merged.min_row, merged.max_row + 1)
            if not removed.intersection(span):
                rebuilt.append((
                    merged.min_col, merged.min_row, merged.max_col, merged.max_row,
                ))
                continue

            touched.append(merged)
            kept = [row for row in span if row not in removed]
            if not kept:
                continue

            first, last = kept[0], kept[-1]
            rebuilt.append((
                merged.min_col, first,
                merged.max_col, last - sum(1 for row in removed if row < last),
            ))

        # Снимаем только те объединения, которые задело удаление:
        # остальные остаются в книге ровно такими, какими были.
        for merged in touched:
            sheet.unmerge_cells(str(merged))

        for min_col, min_row, max_col, max_row in rebuilt:
            sheet.merge_cells(
                start_row=min_row, start_column=min_col,
                end_row=max_row, end_column=max_col,
            )

    def _save(self, sheet: Worksheet, target: Path) -> None:
        """Сохраняет книгу в output_dir, создавая папку при необходимости."""
        target.parent.mkdir(parents=True, exist_ok=True)
        sheet.parent.save(str(target))

    # ─────────────────────────────────────────────────────────
    # Поиск по листу
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _find_row_by_text(
        sheet: Worksheet,
        text: str,
        start: int = 1,
    ) -> Optional[int]:
        """
        Номер строки, в которой встречается текст (без учёта регистра).

        :param start: с какой строки искать. Шапка ищется с 1-й строки,
            блок сторон — от шапки: в присланном файле выше может стоять
            другая таблица с такими же подписями.
        """
        needle = text.lower()
        for row in sheet.iter_rows(min_row=max(start, 1)):
            for cell in row:
                if needle in _normalize_header(cell.value).lower():
                    return cell.row
        return None

    @staticmethod
    def _log_read(
        path: Path,
        zayavka: Mapping[str, Any],
        vehicles: Sequence[Mapping[str, Any]],
    ) -> None:
        """
        Лог чтения — только количества и длины, без значений.

        По длинам видно, что данные разобрались (поля не пустые), а сами
        ФИО, VIN, номера и адреса в лог не попадают.
        """
        filled = sum(
            1 for value in zayavka.values() if _text(value) or value == 0.0
        )
        total = sum(len(_text(value)) for value in zayavka.values())
        logger.info(
            "Прочитан бланк %s: полей заявки заполнено %d из %d "
            "(суммарная длина значений %d), машин %d",
            path.name, filled, len(zayavka), total, len(vehicles),
        )
        lengths = [
            len(_text(value)) for vehicle in vehicles for value in vehicle.values()
        ]
        logger.debug(
            "Строки машин: значений %d, суммарная длина %d",
            len(lengths), sum(lengths),
        )


__all__ = [
    "ABSENT_VEHICLE_KEYS",
    "ABSENT_ZAYAVKA_KEYS",
    "CARRIER_NAME",
    "CUSTOMER_NAME",
    "DATE_FIELDS",
    "DATE_FORMAT",
    "DATE_HEADER",
    "DATE_LABEL",
    "FIELD_SPECS",
    "FILE_PREFIX",
    "HEADERS",
    "HEADER_OF",
    "MAX_VEHICLES",
    "PER_ROW_ZAYAVKA_FIELDS",
    "PARTY_LABELS",
    "PARTY_LABEL",
    "SHEET_NAME",
    "SPEC_BY_KEY",
    "TEMPLATE_NAME",
    "TIME_FORMAT",
    "TITLE",
    "UNMAPPED_ZAYAVKA_FIELDS",
    "VEHICLE_KEYS",
    "VEHICLE_SPECS",
    "WRAP_HEADERS",
    "ZAYAVKA_KEYS",
    "ZAYAVKA_SCHEMA_ORDER",
    "ZayavkaExcelGenerator",
    "ZayavkaTemplateError",
    "is_document_time",
]
