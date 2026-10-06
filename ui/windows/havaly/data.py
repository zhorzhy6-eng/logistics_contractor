#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка данных заявки Хавалов из вкладок окна (ЭТАП 3.1.E.B.1).

Зачем отдельный модуль
----------------------
Вкладок шесть, и каждая знает только свои поля. Здесь они собираются в одну
структуру — ту самую, которую ждут генератор и валидатор типа:
``{"zayavka": {...}, "vehicles": [{...}, ...]}`` (схема ответа промпта,
core/prompts/havaly.py). Сборка живёт вне окна, поэтому её можно проверить
тестом без поднятия интерфейса. Образцы раскладки —
ui/windows/logistiks_rus/data.py и ui/windows/arenda_ts/data.py.

ПОЧЕМУ ВОЗВРАЩАЕТСЯ СЛОВАРЬ, А НЕ ContractData
----------------------------------------------
У остальных типов сборщик отдаёт ContractData: генераторы там DOCX и читают
именно её. У Хавалов иначе:

  * выход — .xlsx, и генератор (ZayavkaExcelGenerator) принимает
    ``{"zayavka": ..., "vehicles": ...}`` напрямую (метод ``_payload``
    разбирает и блок ``zayavka``, и плоские поля);
  * ContractData.coerce знает только свои блоки (driver, carrier, customer,
    vehicles, tractor, trailer, contract): корневой ``zayavka`` она хранить
    не умеет, и общие сведения заявки потерялись бы
    (см. docstring core/contracts/zayavka/validator.py).

Поэтому сборщик отдаёт СХЕМУ ПРОМПТА плюс третий ключ ``contract`` —
те же данные в форме ContractData. Правило AGENTS.md § 5.2 «ContractData
теряет ключи при coerce — класть поля дважды» соблюдено: одни и те же
значения лежат и в блоке ``zayavka``, и в форме ContractData
(``contract`` / ``tractor`` / ``trailer`` / ``driver``).

Раскладка полей (шесть вкладок)
------------------------------
Поля бланка распределены по вкладкам так (одно поле — одна вкладка):

    Заявка     → date, lot_number, customer_name, carrier_name
    Груз       → vehicles[*] (vin, brand, model, dealer, dealer_code)
    Маршрут    → loading_city, loading_point, unloading_city, unloading_point,
                 loading_plan_date, loading_plan_time
    Водитель   → driver_last_name, driver_first_name, driver_middle_name,
                 driver_license_number, driver_license_issue_date,
                 driver_passport_series, driver_passport_number,
                 driver_passport_issuer, driver_passport_issue_date,
                 driver_citizenship, driver_birth_date, driver_registration,
                 driver_phone
    ТС         → tractor_brand, tractor_color, tractor_plate,
                 trailer_brand, trailer_plate
    Стоимость  → price_with_vat, vat_rate

Имена ключей JSON взяты ИЗ ПРОМПТА (core/prompts/havaly.py) и сверены с
генератором (ZAYAVKA_SCHEMA_ORDER / VEHICLE_KEYS) тестами — не по памяти.
Имена полей вкладок те же, что ключи JSON: вкладка отдаёт ровно имя поля
схемы, поэтому таблицы перевода читаются без второй колонки и не могут
разойтись с промптом незаметно.

Два послабления — там, где поле бланка живёт не в своей вкладке
----------------------------------------------------------------
1. ПОЛЯ ШАПКИ. В бланке дата заявки стоит над таблицей, а «Номер Лота»,
   «Марка Автовоза», «Цвет кабины», «Номер автовоза», «Марка прицепа»
   и «Номер Прицепа» — колонки, которые в образце заказчика заполнены
   одинаково во всех строках. Поэтому эти поля читаются и из вкладки
   «Заявка» (шапка формы), и из своей вкладки («Груз» или «ТС»): значение
   своей вкладки важнее, шапка подхватывается, если вкладка молчит.
   Ключи у полей одни и те же (``_SHARED_ZAYAVKA_FIELDS``).
2. СТАВКА НДС. В бланке для неё ячейки нет, но в схеме промпта поле есть
   (``vat_rate``, по умолчанию «22%»). Если вкладка «Стоимость» отдаёт
   ставку числом (``vat_rate_num``, как её читает logistiks_rus), строка
   собирается из числа: 22.0 → «22%», 0.0 → «0%». Ставку вкладка не
   отдала — поля в ответе НЕТ: подставлять «22%» за пользователя нельзя,
   иначе пустая форма перестанет быть пустой (проверка стоит тестом).

Что заполняется всегда
----------------------
``customer_name`` («Сюрлогистик») и ``carrier_name`` («ООО ТЕХНОЛОГИСТИКА»)
— стороны этой заявки ФИКСИРОВАНЫ и в бланке напечатаны, от заявки к заявке
не меняются. По промпту они заполнены ВСЕГДА (не пустые и не null), и
генератор при чтении формы подставляет те же константы. Сборщик берёт их
из вкладки «Заявка», а если вкладка поле не отдала — из констант генератора:
ответ сборки должен читаться так же, как ответ распознавания.

Устойчивость
------------
Сборка не падает никогда: отсутствующая вкладка, вкладка без get_data(),
исключение внутри get_data() или неожиданный тип результата дают пустой
словарь — раздел просто останется пустым (частичное окно). В лог попадают
только имена полей, количества и длины: ФИО, VIN, номера, адреса и телефоны
не пишем.
"""

import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core.contracts.zayavka.generator import CARRIER_NAME, CUSTOMER_NAME
from core.dates import parse_date

logger = logging.getLogger("ui.windows.havaly.data")

# ─────────────────────────────────────────────────────────────
# Вкладки окна
# ─────────────────────────────────────────────────────────────

#: Ключи вкладок и человекочитаемые названия — для сообщений в логе.
#: Заголовки совпадают с TAB_CONFIGS окна Хавалов (ui/windows/havaly/window.py)
#: и с разделами бланка: по ним же находится вкладка в контейнере окна.
#: Порядок — как разделы бланка: он же порядок сборки полей заявки.
SECTION_TITLES: Dict[str, str] = {
    "zayavka": "Заявка",
    "cargo": "Груз",
    "route": "Маршрут",
    "driver": "Водитель",
    "vehicle": "ТС",
    "price": "Стоимость",
}

#: Ключ вкладки «Груз» и ключ, под которым машины могут прийти разделом
#: верхнего уровня (как их отдаёт распознавание и ждёт генератор).
CARGO_SECTION = "cargo"
VEHICLES_SECTION = "vehicles"

#: Сколько машин помещается в бланк: строк данных в эталоне 10. Столько же
#: у генератора (ZayavkaExcelGenerator.MAX_VEHICLES) и в валидаторе.
MAX_VEHICLES = 10

#: Поля машины, в порядке схемы промпта. Одна строка таблицы — одна машина.
VEHICLE_FIELDS: Tuple[str, ...] = (
    "vin", "brand", "model", "dealer", "dealer_code",
)

# ─────────────────────────────────────────────────────────────
# Маппинги «ключ вкладки → ключ JSON»
# ─────────────────────────────────────────────────────────────

#: Поля, которые могут прийти из нескольких вкладок. На первом месте —
#: вкладка-владелец поля (её значение важнее), дальше — вкладка «Заявка»
#: (шапка формы): поле, введённое в шапке, не должно теряться, если своя
#: вкладка его не отдала. Порядок внутри пары — «сначала владелец».
_SHARED_ZAYAVKA_FIELDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("date", ("zayavka", "cargo")),
    ("lot_number", ("zayavka", "cargo")),
    # В бланке марка автовоза, цвет кабины, номер автовоза и прицеп стоят
    # в шапке таблицы над колонками — их видно на вкладке «Заявка».
    ("tractor_brand", ("vehicle", "zayavka")),
    ("tractor_color", ("vehicle", "zayavka")),
    ("tractor_plate", ("vehicle", "zayavka")),
    ("trailer_brand", ("vehicle", "zayavka")),
    ("trailer_plate", ("vehicle", "zayavka")),
)

#: Вкладка «Заявка» → блок "zayavka". Номер и дата заявки, стороны.
_ZAYAVKA_FIELDS: Tuple[str, ...] = (
    "date", "lot_number", "customer_name", "carrier_name",
)

#: Вкладка «Маршрут» → блок "zayavka": места погрузки и разгрузки, план.
_ROUTE_FIELDS: Tuple[str, ...] = (
    "loading_city", "loading_point", "unloading_city", "unloading_point",
    "loading_plan_date", "loading_plan_time",
)

#: Вкладка «Водитель» → блок "zayavka": водитель плоскими полями
#: (отдельного блока driver в схеме Хавалов нет).
_DRIVER_FIELDS: Tuple[str, ...] = (
    "driver_last_name", "driver_first_name", "driver_middle_name",
    "driver_license_number", "driver_license_issue_date",
    "driver_passport_series", "driver_passport_number",
    "driver_passport_issuer", "driver_passport_issue_date",
    "driver_citizenship", "driver_birth_date", "driver_registration",
    "driver_phone",
)

#: Вкладка «ТС» → блок "zayavka": автовоз и прицеп (тоже плоскими полями).
_VEHICLE_FIELDS: Tuple[str, ...] = (
    "tractor_brand", "tractor_color", "tractor_plate",
    "trailer_brand", "trailer_plate",
)

#: Вкладка «Стоимость» → блок "zayavka": ставка с НДС и ставка НДС строкой.
_PRICE_FIELDS: Tuple[str, ...] = ("price_with_vat", "vat_rate")

#: Имя поля ставки НДС числом: вкладка может отдать ставку как её читает
#: logistiks_rus/data.py (vat_rate_num = 22.0) — строка собирается из числа.
VAT_RATE_NUM_FIELD = "vat_rate_num"

#: Разделы в порядке сборки полей заявки. Поле, которое отдали две вкладки,
#: получает значение ПОСЛЕДНЕЙ: «Стоимость» и «ТС» уточняют то, что могли
#: ввести в шапке. Поля, разложенные по нескольким вкладкам иначе (шапка
#: против своей вкладки), перечислены в _SHARED_ZAYAVKA_FIELDS.
_ZAYAVKA_SECTIONS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("zayavka", _ZAYAVKA_FIELDS),
    ("route", _ROUTE_FIELDS),
    ("driver", _DRIVER_FIELDS),
    ("vehicle", _VEHICLE_FIELDS),
    ("price", _PRICE_FIELDS),
)

#: Поля-даты: значения приводятся к формату документа ДД.ММ.ГГГГ. Список —
#: как у генератора (DATE_FIELDS): иначе дата из QDateEdit («2026-10-06»)
#: попала бы в отчёт валидатора неразобранной строкой.
DATE_FIELDS: Tuple[str, ...] = (
    "date", "driver_license_issue_date", "driver_passport_issue_date",
    "driver_birth_date", "loading_plan_date",
)


# ─────────────────────────────────────────────────────────────
# Чтение данных вкладки
# ─────────────────────────────────────────────────────────────

def _section_title(key: str) -> str:
    """Название раздела для лога (без самих данных)."""
    return SECTION_TITLES.get(key, str(key))


def _raw_data(tabs: Mapping[str, Any], key: str) -> Dict[str, Any]:
    """
    Данные одной вкладки: get_data() у вкладки-объекта или готовый dict.

    Ничего не поднимает наверх: вкладки может не быть, у неё может не быть
    get_data(), а сам get_data() может упасть — во всех случаях получаем
    пустой словарь и продолжаем сборку. Так сборщик переживает частичное
    окно (вкладки ещё не созданы — ЭТАП 3.1.E.B.2).
    """
    if not isinstance(tabs, Mapping):
        logger.warning(
            "Хавалы: данные вкладок не словарь (%s) — сборка пропущена",
            type(tabs).__name__,
        )
        return {}

    source = tabs.get(key)
    if source is None:
        logger.debug(
            "Хавалы: вкладки «%s» нет — раздел пропущен", _section_title(key)
        )
        return {}

    if isinstance(source, Mapping):
        return dict(source)

    getter = getattr(source, "get_data", None)
    if getter is None:
        logger.warning(
            "Хавалы: у вкладки «%s» нет get_data() — раздел пропущен",
            _section_title(key),
        )
        return {}

    try:
        data = getter()
    except Exception as e:  # noqa: BLE001 — сборка не должна падать из-за вкладки
        logger.error(
            "Хавалы: вкладка «%s» не отдала данные (%s) — раздел пропущен",
            _section_title(key), type(e).__name__,
        )
        return {}

    if data is None:
        return {}
    if not isinstance(data, Mapping):
        logger.warning(
            "Хавалы: вкладка «%s» вернула %s вместо словаря — раздел пропущен",
            _section_title(key), type(data).__name__,
        )
        return {}

    return dict(data)


def _read_sections(tabs: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Данные всех вкладок окна: каждая читается ровно один раз.

    Машины могут лежать и во вкладке «Груз» (поле ``vehicles``), и отдельным
    разделом верхнего уровня, причём раздел бывает как словарём
    ``{"vehicles": [...]}``, так и самим списком — распознавание отдаёт
    второй вид, а генератор читает ``data["vehicles"]``. Принимаются оба.
    """
    sections = {key: _raw_data(tabs, key) for key in SECTION_TITLES}

    if not sections[CARGO_SECTION].get(VEHICLES_SECTION):
        # Вкладка «Груз» машин не дала — ищем их в корне окна.
        extra = tabs.get(VEHICLES_SECTION) if isinstance(tabs, Mapping) else None
        if isinstance(extra, (list, tuple)):
            sections[CARGO_SECTION][VEHICLES_SECTION] = list(extra)
        else:
            fallback = _raw_data(tabs, VEHICLES_SECTION)
            if fallback.get(VEHICLES_SECTION):
                sections[CARGO_SECTION][VEHICLES_SECTION] = fallback[VEHICLES_SECTION]

    return sections


def _field(data: Mapping[str, Any], key: str) -> str:
    """
    Значение поля вкладки строкой без обрамляющих пробелов (None → «»).

    bool — подкласс int, но «True» в бланке не нужен: это пустое значение.
    Числовой ноль остаётся «0»: пустое значение и явный ноль — разные вещи
    (грабли 2B.4).
    """
    value = data.get(key)
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _set_if_filled(target: Dict[str, Any], key: str, value: Any) -> None:
    """
    Кладёт значение в словарь, если оно непустое.

    Пустая строка не записывается: у вкладки-заглушки незаполненное поле
    остаётся None, и в ответе не должно появиться ни None, ни мусора.
    Числа (0 — это значение, а не пустота) проходят как есть.
    """
    if value is None:
        return
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return
    target[key] = value


def _filled(value: Any) -> bool:
    """
    Заполнено ли значение поля.

    Пустое — None, пустая строка и bool (в форме такого поля нет). Числовой
    ноль заполненным считается: это значение, а не отсутствие данных
    (у ставки «не указана» проверяется отдельно — см. _price).
    """
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """
    Число из значения любого вида: «1 234,56 руб.», «269741.00», 180300, «22%».

    Разделители тысяч и запятая как десятичный разделитель — обычный формат
    документов и полей ввода. Пустое или непонятное значение даёт default
    (по умолчанию None — «числа нет», чтобы не подменять его нулём).
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)

    text = (
        str(value)
        .strip()
        .lower()
        .replace("%", "")
        .replace("\u00a0", "")
        .replace("\u202f", "")
        .replace(" ", "")
        .replace("₽", "")
        .replace("руб.", "")
        .replace("руб", "")
        .replace("р.", "")
    )
    if not text:
        return default

    text = text.replace(",", ".")
    if text.count(".") > 1:
        # «1.234.567» — точки как разделители тысяч.
        head, _, tail = text.rpartition(".")
        text = head.replace(".", "") + "." + tail

    try:
        return float(text)
    except ValueError:
        return default


def _valid_rate_num(value: Any) -> Optional[float]:
    """
    Ставка НДС числом, если в поле действительно ставка.

    Значение больше 100 ставкой быть не может (поле вкладки — «Ставка НДС»,
    а не сумма): такое число не разбираем, чтобы не считать по нему НДС.
    """
    number = _to_float(value)
    if number is None or number < 0 or number > 100:
        return None
    return number


def _format_vat_rate(number: float) -> str:
    """Ставка строкой, как в бланке: 22.0 → «22%», 0.0 → «0%»."""
    if float(number).is_integer():
        return f"{number:.0f}%"
    return f"{number:g}%"


def _date_text(value: Any) -> str:
    """
    Дата в формате документа ДД.ММ.ГГГГ; непонятное значение — как есть.

    Вкладка отдаёт дату из QDateEdit строкой «2026-10-06» (так делают все
    окна проекта), а схема промпта требует формат документа. Неразобранное
    значение не выбрасываем: его покажет валидатор («Не разобрана дата»).
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    parsed = parse_date(text, warn=False)
    return parsed.strftime("%d.%m.%Y") if parsed else text


def _time_text(value: Any) -> str:
    """
    Время в формате «ЧЧ:ММ»; непонятное значение — как есть.

    QTimeEdit отдаёт «9:00» или «09:00:00» — форма заказчика допускает
    любую запись, а схема промпта требует одну. Разбор тот же, что
    у генератора (``is_document_time`` принимает оба вида).
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    match = re.match(r"^(\d{1,2})[:.\s](\d{1,2})", text)
    if match:
        hours, minutes = int(match.group(1)), int(match.group(2))
        if 0 <= hours <= 23 and 0 <= minutes <= 59:
            return f"{hours:02d}:{minutes:02d}"
    return text


def _money(value: Any) -> float:
    """
    Денежное значение числом: «1 234,56 руб.» → 1234.56, пустое → 0.0.

    Ноль — это «ставка не указана»: ровно так её читают промпт
    («если ставка не указана — 0.0»), генератор и валидатор. Функция нужна
    там, где число требуется ОБЯЗАТЕЛЬНО, — в сводке для лога.
    """
    number = _to_float(value)
    return 0.0 if number is None else number


def _price(value: Any) -> Any:
    """
    Ставка с НДС числом — в том виде, в каком её ждёт схема промпта.

    Незаполненная и нулевая ставка дают пустую строку: поля в ответе не
    появляется. «Ставка не указана — 0.0» — это правило ПРОМПТА для ответа
    распознавания; форма с пустым полем ввода не должна подсовывать ноль
    (иначе в отчёте валидатора «ставка заполнена нулём» вместо «не
    заполнена»). Ноль читается как «ставки нет» — ровно так его понимают
    генератор (``_money``) и валидатор, когда поля нет вовсе.
    """
    number = _to_float(value)
    if number is None or number == 0:
        return ""
    return number


# ─────────────────────────────────────────────────────────────
# Сборка по разделам
# ─────────────────────────────────────────────────────────────

def _value_for(field: str, data: Mapping[str, Any]) -> Any:
    """
    Значение поля вкладки в том виде, в каком его ждёт схема промпта.

    Даты — «ДД.ММ.ГГГГ», время — «ЧЧ:ММ», ставка — числом, остальное —
    строкой без обрамляющих пробелов. Ставка НДС числом превращается
    в строку «22%»: в схеме это строка (послабление 2).
    """
    if field in DATE_FIELDS:
        return _date_text(data.get(field))
    if field == "loading_plan_time":
        return _time_text(data.get(field))
    if field == "price_with_vat":
        return _price(data.get(field))
    if field == "vat_rate":
        text = _field(data, "vat_rate")
        if text:
            return text
        number = _valid_rate_num(data.get(VAT_RATE_NUM_FIELD))
        return _format_vat_rate(number) if number is not None else ""
    return _field(data, field)


def _merge_zayavka(
    zayavka: Dict[str, Any],
    fields: Sequence[str],
    data: Mapping[str, Any],
) -> None:
    """
    Кладёт поля одной вкладки в блок "zayavka" (порядок вкладок — в
    _ZAYAVKA_SECTIONS: заполненное позже перекрывает заполненное раньше).
    """
    for field in fields:
        _set_if_filled(zayavka, field, _value_for(field, data))


def _build_vehicles(data: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """
    Перевозимые машины: одна строка вкладки — одна машина (блок "vehicles").

    Строка без единого заполненного поля пропускается: это пустая строка
    таблицы, а не машина (правило промпта). Лишние машины (сверх 10)
    отсекаются — в бланке ровно 10 строк данных. VIN может быть пустым:
    «Vin по факту погрузки» — обычное дело, марка и модель машину выдают.
    """
    raw_vehicles = data.get(VEHICLES_SECTION)
    if not isinstance(raw_vehicles, (list, tuple)):
        return []

    vehicles: List[Dict[str, Any]] = []
    for item in raw_vehicles:
        if not isinstance(item, Mapping):
            continue

        vehicle = {
            field: _field(item, field)
            for field in VEHICLE_FIELDS
        }
        if not any(vehicle.values()):
            continue
        vehicles.append(vehicle)

    if len(vehicles) > MAX_VEHICLES:
        logger.warning(
            "Хавалы: машин %s, в бланк помещается %s — лишние отброшены",
            len(vehicles), MAX_VEHICLES,
        )
        vehicles = vehicles[:MAX_VEHICLES]

    return vehicles


def _zayavka_of(sections: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """
    Блок "zayavka" из всех вкладок (маппинги «вкладка → ключ JSON»).

    Поля собираются по вкладкам-владельцам (``_ZAYAVKA_SECTIONS``), а потом
    добираются те, что могут прийти из шапки формы (``_SHARED_ZAYAVKA_FIELDS``):
    заполнено в двух местах — берётся значение вкладки-владельца. Стороны
    фиксированы и заполнены всегда (см. docstring модуля).
    """
    zayavka: Dict[str, Any] = {}

    for section, fields in _ZAYAVKA_SECTIONS:
        data = sections.get(section) or {}
        _merge_zayavka(zayavka, fields, data)

    # Поля, которые в бланке стоят в шапке таблицы (дата заявки, номер лота,
    # автовоз и прицеп), читаются ещё и из вкладки «Заявка»: их видно над
    # колонками, и пользователь может ввести их в шапке. Своя вкладка
    # важнее — её значение уже лежит в zayavka и не переписывается.
    for field, sections_order in _SHARED_ZAYAVKA_FIELDS:
        if field in zayavka:
            continue
        for section in sections_order:
            value = _value_for(field, sections.get(section) or {})
            if _filled(value):
                _set_if_filled(zayavka, field, value)
                break

    zayavka["customer_name"] = zayavka.get("customer_name") or CUSTOMER_NAME
    zayavka["carrier_name"] = zayavka.get("carrier_name") or CARRIER_NAME
    return zayavka


def _filled_counts(
    zayavka: Mapping[str, Any],
    vehicles: Sequence[Mapping[str, Any]],
) -> Dict[str, int]:
    """
    Сколько полей заполнено в каждой вкладке — для лога.

    Считаются поля, а не значения: ни ФИО, ни VIN, ни номера в лог не идут.
    У вкладки «Груз» считаются заполненные значения машин (пять полей на
    строку), у «Заявки» — ещё и поля шапки формы, которые она подхватывает
    у других вкладок (``_SHARED_ZAYAVKA_FIELDS``): вкладка их показывает,
    значит для пользователя они её.
    """
    counts = {
        section: sum(1 for key in fields if _filled(zayavka.get(key)))
        for section, fields in _ZAYAVKA_SECTIONS
    }
    counts[CARGO_SECTION] = sum(
        1 for vehicle in vehicles for value in vehicle.values() if value
    )
    counts["zayavka"] += sum(
        1 for field, _order in _SHARED_ZAYAVKA_FIELDS
        if field in zayavka and _filled(zayavka.get(field))
    )
    return counts


def _tractor_block(zayavka: Mapping[str, Any]) -> Dict[str, Any]:
    """Автовоз в форме ContractData: brand_model / color / plate_number."""
    return {
        target: zayavka[source]
        for target, source in (
            ("brand_model", "tractor_brand"),
            ("color", "tractor_color"),
            ("plate_number", "tractor_plate"),
        )
        if source in zayavka
    }


def _trailer_block(zayavka: Mapping[str, Any]) -> Dict[str, Any]:
    """Прицеп в форме ContractData: brand_model / plate_number."""
    return {
        target: zayavka[source]
        for target, source in (
            ("brand_model", "trailer_brand"),
            ("plate_number", "trailer_plate"),
        )
        if source in zayavka
    }


def _driver_block(zayavka: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Водитель в форме ContractData.

    В схеме Хавалов поля водителя плоские (driver_last_name и т. д.) —
    отдельного блока driver там нет. В справочник водителей проекта поля
    ложатся короткими именами (last_name, phone, passport_*), поэтому здесь
    и снимается префикс: driver_last_name → last_name, driver_phone → phone.
    """
    return {
        field[len("driver_"):]: zayavka[field]
        for field in _DRIVER_FIELDS
        if field in zayavka
    }


def _contract_block(zayavka: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Общие сведения заявки — в блок ``contract`` формы ContractData.

    ContractData хранит общие сведения в ``contract``, а автовоз, прицеп
    и водителя — отдельными блоками: их собирают _tractor_block /
    _trailer_block / _driver_block. Здесь лежат ВСЕ поля заявки, включая
    ``vat_rate`` и стороны (``customer_name`` / ``carrier_name``): в форме
    бланка у них ячейки нет, но в данных заявки они есть, а лишний ключ
    коерция сохранит — потерять данные хуже.
    """
    return dict(zayavka)


def _contract_of(
    zayavka: Mapping[str, Any],
    vehicles: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    Данные заявки в форме ContractData: contract + tractor / trailer / driver.

    Трактовка «дважды» — из AGENTS.md § 5.2: одни и те же значения лежат
    и в схеме промпта (``zayavka``), и в форме ContractData. Форма собрана
    так, чтобы ``ContractData.coerce(data["contract"])`` сохранила поля
    заявки: они лежат ВНУТРИ ``contract``, потому что коерция читает блоки
    по именам (contract, tractor, trailer, driver, vehicles) и посторонние
    корневые ключи в ``contract`` не поднимает.
    """
    return {
        "contract": _contract_block(zayavka),
        "tractor": _tractor_block(zayavka),
        "trailer": _trailer_block(zayavka),
        "driver": _driver_block(zayavka),
        "vehicles": [dict(vehicle) for vehicle in vehicles],
    }


# ─────────────────────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────────────────────

def collect_havaly_data(window: Any) -> Dict[str, Any]:
    """
    Собирает данные заявки Хавалов со всех шести вкладок окна.

    :param window: окно типа (HavalyWindow), словарь «ключ вкладки → вкладка
        или dict» или каркасное окно с контейнером вкладок. Ключи: zayavka,
        cargo, route, driver, vehicle, price. Лишние ключи игнорируются,
        отсутствующие означают пустой раздел — сборщик работает и на
        частичном окне.
    :return: ``{"zayavka": {...}, "vehicles": [...], "contract": {...}}``.

    Блок ``zayavka`` — схема ответа промпта (core/prompts/havaly.py):
    тридцать полей, включая те, для которых в бланке нет ячейки
    (``vat_rate``, стороны). Ключ появляется, только если поле отдано
    и заполнено (для ставки ноль — «не заполнено»); исключение —
    фиксированные стороны, они заполнены всегда. Блок ``vehicles`` — до
    десяти машин по пять полей.

    Ключ ``contract`` — те же данные в форме ContractData: ``contract``
    с полями заявки плюс блоки ``tractor`` / ``trailer`` / ``driver`` /
    ``vehicles`` (AGENTS.md § 5.2: ContractData теряет ключи при coerce,
    поэтому поля кладутся дважды).

    Функция не поднимает исключений: всё, что не удалось прочитать, остаётся
    пустым и попадает в лог.
    """
    tabs = _tabs_of(window)
    sections = _read_sections(tabs)

    zayavka = _zayavka_of(sections)
    vehicles = _build_vehicles(sections[CARGO_SECTION])

    # Поля заявки лежат ДВАЖДЫ: в схеме промпта (её читает генератор .xlsx)
    # и в contract — форме, которую понимает ContractData (AGENTS.md § 5.2).
    contract = _contract_of(zayavka, vehicles)

    rate = _money(zayavka.get("price_with_vat"))
    logger.info(
        "Хавалы: данные формы собраны — машин=%s, полей заявки=%s, "
        "водитель=%s, автовоз=%s, ставка с НДС=%s, заполнено по вкладкам: %s",
        len(vehicles),
        len(zayavka),
        "да" if zayavka.get("driver_last_name") else "нет",
        "да" if zayavka.get("tractor_plate") else "нет",
        rate if rate > 0 else "—",
        ", ".join(
            f"{_section_title(section)}={count}"
            for section, count in _filled_counts(zayavka, vehicles).items()
        ),
    )
    return {"zayavka": zayavka, "vehicles": vehicles, "contract": contract}


def _safe_attr(window: Any, name: str) -> Any:
    """
    Атрибут окна или None: сломанное окно не должно ронять сборку.

    Каркас окна (ЭТАП 3.1.E.B.3) может быть недоделан, а свойство — падать.
    Обращение к вкладке здесь не цель, а способ узнать данные: исключение
    означает «вкладки нет».
    """
    try:
        return getattr(window, name, None)
    except Exception as e:  # noqa: BLE001 — окно не должно ронять сборку
        logger.warning(
            "Хавалы: атрибут окна %s не читается (%s) — вкладка пропущена",
            name, type(e).__name__,
        )
        return None


def _tabs_from_container(window: Any) -> Dict[str, Any]:
    """
    Вкладки из контейнера окна (``window.tabs``) — по заголовкам.

    Пока вкладки Хавалов не написаны, окно отдаёт их одним QTabWidget
    (ui/windows/base_window.py). Заголовки вкладок совпадают с разделами
    бланка, поэтому вкладка находится по названию. Контейнер читается
    аккуратно: у каркаса окна его может не быть вовсе.
    """
    container = _safe_attr(window, "tabs")
    if container is None:
        return {}

    index_of = _safe_attr(container, "indexOf")
    widget_of = _safe_attr(container, "widget")
    if not callable(index_of) or not callable(widget_of):
        return {}

    tabs: Dict[str, Any] = {}
    by_title = {title: key for key, title in SECTION_TITLES.items()}
    for title, key in by_title.items():
        try:
            index = index_of(title)
        except Exception as e:  # noqa: BLE001 — контейнер не должен ронять сборку
            logger.warning(
                "Хавалы: вкладка «%s» не найдена в окне (%s)",
                title, type(e).__name__,
            )
            continue
        if index < 0:
            continue
        tabs[key] = widget_of(index)
    return tabs


def _tabs_of(window: Any) -> Mapping[str, Any]:
    """
    Вкладки окна: словарь как есть, атрибуты окна или контейнер вкладок.

    Окно Хавалов (ЭТАП 3.1.E.B.3) будет держать вкладки атрибутами
    (``window.zayavka_tab``, ``window.cargo_tab``, ...) — как окна других
    типов. Каркасное окно отдаёт их контейнером ``window.tabs``, поэтому
    есть и второй путь. Ни одного пути нет — разделы остаются пустыми,
    а не падают.
    """
    if isinstance(window, Mapping):
        return window
    if window is None:
        logger.warning("Хавалы: окно не передано — сборка пропущена")
        return {}

    tabs: Dict[str, Any] = {
        key: _safe_attr(window, f"{key}_tab") for key in SECTION_TITLES
    }
    if any(tab is not None for tab in tabs.values()):
        return tabs

    return _tabs_from_container(window)


__all__ = [
    "CARGO_SECTION",
    "CUSTOMER_NAME",
    "CARRIER_NAME",
    "DATE_FIELDS",
    "MAX_VEHICLES",
    "SECTION_TITLES",
    "VAT_RATE_NUM_FIELD",
    "VEHICLE_FIELDS",
    "collect_havaly_data",
]
