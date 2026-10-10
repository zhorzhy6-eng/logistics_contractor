#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка данных договора аренды ТС с экипажем из вкладок окна (ЭТАП 3.1.D.B.1).

Зачем отдельный модуль
----------------------
Вкладок семь, и каждая знает только свои поля. Здесь они собираются в один
ContractData — тот же объект, что читают генератор
(core/contracts/arenda_ts/generator.py) и валидатор
(core/contracts/arenda_ts/validator.py). Сборка живёт вне окна, поэтому её
можно проверить тестом без поднятия интерфейса. Образец раскладки —
ui/windows/logistiks_rus/data.py.

Раскладка полей (согласована на ЭТАПЕ 3.1.D.B):

    lessee_tab   → contract.lessee{...}, contract.carrier_type,
                   customer.full_name / customer.short_name
    lessor_tab   → contract.lessor{...},
                   carrier.full_name / carrier.short_name
    vehicle_tab  → contract.number, contract.date,
                   contract.lease_start_date, contract.lease_end_date,
                   contract.planned_completion_date,
                   tractor.brand_model / plate_number / vehicle_type,
                   trailer.brand_model / plate_number
    route_tab    → contract.route, contract.loadings / contract.unloadings,
                   loadings[*], unloadings[*]
    cargo_tab    → vehicles[*].brand_model / vin /
                   loading_point / unloading_point
    crew_tab     → driver.full_name, birth_date, passport_*, license_*,
                   passport_issuer, registration_address, phone
    price_tab    → contract.sum_wo_vat / sum_vat / sum_total,
                   contract.price_without_vat / price_with_vat,
                   contract.vat_rate / vat_rate_num,
                   contract.payment_days,
                   contract.special_conditions

Арендатор в этом типе — НАША сторона (в заявке на перевозку наша сторона
называется Экспедитором), Арендодатель — вторая. Обе стороны лежат в contract
блоками lessee / lessor: именно оттуда их читают
ArendaTsGenerator._party_block и ArendaTsValidator._party_block.

Пять маппингов, которые закрывает этот шаг (TODO 3.1.D.B.1)
-----------------------------------------------------------
1. КОРНЕВЫЕ ПОЛЯ → В CONTRACT. Распознавание отдаёт lessee, lessor, route,
   lease_start_date и lease_end_date в КОРНЕ ответа, а ContractData.coerce
   корневые ключи не хранит (core/contract_data.py на этом шаге не трогаем).
   Сборщик кладёт их СРАЗУ в contract и не надеется на
   ArendaTsGenerator._hoist_contract_fields: генератор получает от окна уже
   готовый ContractData, а _hoist_contract_fields не-Mapping возвращает как
   есть — то есть для данных интерфейса не делает ничего.

2. ТОЧКИ С time_from / time_to. ContractData._as_point_list оставляет у точки
   только {address, date, time_window} и теряет время подачи ТС, а генератор
   печатает «с 08:00 до 18:00» именно из time_from / time_to. Поэтому точки
   кладутся ДВУМЯ способами:

     * contract["loadings"] / contract["unloadings"] — полный набор
       {name, address, date, time_from, time_to, time_window} (этот путь
       читают генератор и валидатор);
     * ContractData.loadings / .unloadings — приведённые точки
       {address, date, time_window} для верхнеуровневой совместимости
       (name и time_from здесь теряются — так и задумано).

   Окно времени собирается из границ: «08:00-18:00». Если границ нет, а окно
   пришло одной строкой, оно сохраняется как есть — генератор разберёт его сам
   (ArendaTsGenerator._parse_time_window).

3. СУММЫ. Промпт распознавания (core/prompts/arenda_ts.py) кладёт суммы в
   sum_wo_vat / sum_vat / sum_total, а генератор считает арендную плату от
   price_without_vat и берёт базу из sum_wo_vat (варианты с НДС) или из
   sum_total (ИП без НДС). Маппинг зависит от вида Арендатора:

     * ООО и ИП с НДС — три суммы документа: price_without_vat = sum_wo_vat,
       price_with_vat = sum_total, vat_amount = sum_vat,
       vat_rate_num — ставка («22%» → 22.0);
     * ИП без НДС — сумма одна: price_without_vat = sum_total,
       vat_rate_num = 0.0 (НДС не облагается);
     * в варианте с НДС, а в документе только итог (sum_wo_vat нет или он
       нулевой): price_without_vat = sum_total, vat_rate_num = 0.0 — считать
       НДС от неё было бы выдумыванием, о расхождении скажет валидатор.

   Ставка строкой разбирается в число («22%» → 22.0, «Без НДС» → 0.0);
   если ставки нет вовсе, берётся DEFAULT_VAT_RATE_NUM — как в генераторе.

4. ЭКИПАЖ — РАЗБОР СТРОК. Промпт отдаёт паспорт и водительское удостоверение
   одной строкой (passport, license), а справочник водителя хранит серию и
   номер отдельно. Сборщик разбирает строку на серию и номер
   («18 22 926830» → series «18 22», number «926830»), а дату выдачи, если она
   попала в ту же строку, — в *_issue_date. Если вкладка уже отдаёт серию и
   номер раздельно или строку разобрать не удалось, значение остаётся как есть:
   генератор и валидатор принимают оба вида.

5. carrier_type. Вид Арендатора выбирает вариант бланка, метку госрегистрации,
   основание полномочий и состав сумм. Порядок:

     * значение, выбранное пользователем на вкладке «Арендатор» («ООО» /
       «ИП с НДС» / «ИП без НДС») — важнее всего;
     * пусто на вкладке — вид выводится из распознавания: entity_type блока
       lessee («ООО» / «ИП») и ставки НДС (у ИП «0%» → ИП без НДС, иначе
       ИП с НДС) — ровно так же, как в ArendaTsGenerator._resolve_carrier_type;
     * иначе ООО — бланк и расчёт по умолчанию.

Три даты и срок оплаты (FIX-1)
------------------------------

Сборщик знает ТРИ РАЗНЫЕ даты и не связывает их между собой:

  * contract.lease_start_date / contract.lease_end_date — плановый период
    аренды (п. 2.5 бланка), приходит с вкладки «ТС» и из корня ответа
    распознавания;
  * contract.planned_completion_date — планируемая дата завершения рейса
    (п. 3.3.2 бланка), отдельное поле вкладки «ТС».

Автоподстановки между ними нет: в образце ТЛ-574 окончание аренды
(28.09.2026) и плановая дата завершения рейса (26.09.2026) — разные даты,
и любая из трёх может совпасть с другой или отличаться. Одна и та же дата
в двух полях — это выбор пользователя, а не признак ошибки.

contract.payment_days — срок оплаты из п. 4.5 бланка целым числом банковских
дней. Его даёт вкладка «Стоимость» (по умолчанию 30); ноль и отрицательное
значение означают «срок не задан» и в contract не попадают — о незаполненном
сроке скажет валидатор.

Две точки входа
---------------

    build(lessee_tab, lessor_tab, ...)          — по именам вкладок (окно);
    collect_arenda_ts_data({"lessee": tab, ...}) — по словарю.

Первая — тонкая обёртка над второй: раскладка полей живёт в одном месте,
и оба вызова дают одинаковый ContractData. Ключ «act» (вкладка «Акт»,
шаг FIX-3) необязателен: без него сборка даёт те же данные, только без
десяти полей Приложения № 1.

Устойчивость
------------
Сборка не падает никогда: отсутствующая вкладка, вкладка без get_data(),
исключение внутри get_data() или неожиданный тип результата дают пустой
словарь — раздел просто останется пустым. Вкладка читается ровно один раз
(данные нужны сразу нескольким разделам). В лог попадают только имена полей,
количества и вид Арендатора: адреса, ФИО, VIN, госномера, названия организаций
и суммы не пишем.
"""

import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.contract_data import ContractData
from core.vat import (
    VAT_FREE,
    compute_vat,
    normalize_vat_rate,
    total_from_base,
    vat_rate_number,
)

logger = logging.getLogger("ui.windows.arenda_ts.data")

#: Ключи вкладок и человекочитаемые названия — для сообщений в логе.
SECTION_TITLES: Dict[str, str] = {
    "lessee": "Арендатор",
    "lessor": "Арендодатель",
    "vehicle": "ТС",
    "route": "Маршрут",
    "cargo": "Груз",
    "crew": "Экипаж",
    "price": "Стоимость",
    "act": "Акт",
}

# ─────────────────────────────────────────────────────────────
# Вид Арендатора и связанные с ним подстановки
# ─────────────────────────────────────────────────────────────

#: Вид Арендатора — значение contract["carrier_type"]. Три варианта бланка
#: совпадают со значениями ArendaTsGenerator.TEMPLATE_NAMES.
CARRIER_TYPE_OOO = "ООО"
CARRIER_TYPE_IP_WITH_VAT = "ИП с НДС"
CARRIER_TYPE_IP_WITHOUT_VAT = "ИП без НДС"

#: Вид Арендатора по умолчанию: этот вариант выбирают и генератор, и валидатор,
#: если поле не заполнено.
DEFAULT_CARRIER_TYPE = CARRIER_TYPE_OOO

#: Ставка НДС, когда её не удалось ни ввести, ни разобрать («22%»).
DEFAULT_VAT_RATE_NUM = 22.0

#: Метка госрегистрации: у ООО — ОГРН, у ИП — ОГРНИП. Ровно те же константы
#: печатает генератор (ArendaTsGenerator.OGRN_LABEL / OGRNIP_LABEL).
OGRN_LABEL = "ОГРН"
OGRNIP_LABEL = "ОГРНИП"

#: Основание полномочий: у ООО — устав, у ИП — свидетельство о регистрации.
BASIS_OOO = "Устава"
BASIS_IP = "свидетельства о государственной регистрации"

#: Сколько машин помещается в таблицу п. 3.1 бланка (см. ArendaTsGenerator).
MAX_CARS = 12

#: Сколько точек погрузки и выгрузки помещается в разделы 3.2 и 3.3 бланка.
MAX_POINTS = 10

# ─────────────────────────────────────────────────────────────
# Таблицы перевода полей вкладок в поля ContractData
# ─────────────────────────────────────────────────────────────

#: Поля вкладки «Арендатор» → поля блока lessee. Имена целевых полей — те же,
#: что читают ArendaTsGenerator._fill_lessee и ArendaTsValidator._party_required:
#: адрес вкладки — это юридический адрес бланка (legal_address), счёт — bank_account,
#: банк — bank_name.
_LESSEE_FIELDS: Tuple[Tuple[str, str], ...] = (
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
    ("corr_account", "corr_account"),
    ("email", "email"),
    ("edo", "edo"),
    ("phone", "phone"),
    ("director_position", "director_position"),
    ("director_name", "director_name"),
    ("basis", "basis"),
)

#: У Арендодателя те же поля, кроме КПП: плейсхолдера lessor_kpp нет ни в одном
#: бланке (генератор его не заполняет).
_LESSOR_FIELDS: Tuple[Tuple[str, str], ...] = tuple(
    (source, target) for source, target in _LESSEE_FIELDS if target != "kpp"
)

#: Поля Акта приёма-передачи (Приложение № 1, шаг FIX-3) — ровно те
#: плейсхолдеры, которые печатает бланк и заполняет генератор
#: (ArendaTsGenerator._fill_act). Имена совпадают с ключами вкладки «Акт»
#: (ui/windows/arenda_ts/tabs/act_tab.py::ACT_FIELDS) и с ключами contract:
#: вкладка, сборщик и генератор говорят об этих полях одними словами.
#: Импортировать кортеж из act_tab нельзя — этот модуль не должен тянуть
#: PyQt5 (проверяется тестом test_data_module_does_not_import_qt).
ACT_FIELDS: Tuple[str, ...] = (
    "transfer_place",
    "transfer_datetime",
    "transfer_mileage",
    "transfer_condition",
    "transfer_documents",
    "return_place",
    "return_datetime",
    "return_mileage",
    "return_condition",
    "return_notes",
)

#: Ключи распознавания, которые вкладка может отдать блоком как есть
#: (core/prompts/arenda_ts.py): поле ищется на верхнем уровне вкладки, а затем
#: в этом блоке.
_LESSEE_BLOCK = "lessee"
_LESSOR_BLOCK = "lessor"
#: Блок contract распознавания: номер, дата, суммы и ставка НДС.
_CONTRACT_BLOCK = "contract"
_CREW_BLOCK = "driver"

#: Заголовок точки маршрута в логе (сами адреса в лог не идут).
_POINTS_LOGGED: Dict[str, str] = {
    "loadings": "погрузки",
    "unloadings": "выгрузки",
}

#: Дата внутри строки документа: «30.01.2023», «30/01/2023», «30-01-2023».
#: Нужна там, где распознавание склеило дату выдачи с номером документа.
_DATE_IN_TEXT_RE = re.compile(r"\d{2}[.\-/]\d{2}[.\-/]\d{4}")


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
    пустой словарь и продолжаем сборку.
    """
    if not isinstance(tabs, Mapping):
        logger.warning(
            "Разовая аренда: данные вкладок не словарь (%s) — сборка пропущена",
            type(tabs).__name__,
        )
        return {}

    source = tabs.get(key)
    if source is None:
        logger.debug(
            "Разовая аренда: вкладки «%s» нет — раздел пропущен",
            _section_title(key),
        )
        return {}

    if isinstance(source, Mapping):
        return dict(source)

    getter = getattr(source, "get_data", None)
    if getter is None:
        logger.warning(
            "Разовая аренда: у вкладки «%s» нет get_data() — раздел пропущен",
            _section_title(key),
        )
        return {}

    try:
        data = getter()
    except Exception as e:  # noqa: BLE001 — сборка не должна падать из-за вкладки
        logger.error(
            "Разовая аренда: вкладка «%s» не отдала данные (%s) — раздел пропущен",
            _section_title(key), type(e).__name__,
        )
        return {}

    if data is None:
        return {}
    if not isinstance(data, Mapping):
        logger.warning(
            "Разовая аренда: вкладка «%s» вернула %s вместо словаря — раздел пропущен",
            _section_title(key), type(data).__name__,
        )
        return {}

    return dict(data)


def _read_sections(tabs: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Данные всех семи вкладок сразу: каждая читается ровно один раз.

    Вид Арендатора выводится из вкладок «Арендатор» и «Стоимость», а от него
    зависит сборка блоков сторон и сумм, поэтому данные вкладок нужны всем
    разделам сразу — читаем их одним проходом и передаём словарями.
    """
    return {key: _raw_data(tabs, key) for key in SECTION_TITLES}


def _nested(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """Вложенный блок распознавания внутри данных вкладки (или пустой словарь)."""
    nested = data.get(key)
    return nested if isinstance(nested, Mapping) else {}


def _find_field(data: Mapping[str, Any], block: str, *keys: str) -> str:
    """
    Значение поля вкладки строкой: верхний уровень, затем блок распознавания.

    Вкладка отдаёт свои поля плоско (full_name, tractor_brand, driver_phone),
    но может вернуть и распознанный блок как есть (lessee, lessor, contract,
    driver — см. core/prompts/arenda_ts.py). Имена полей блока принимаются
    наравне с именами вкладки: в *keys они перечислены по порядку.
    """
    for key in keys:
        value = _field(data, key)
        if value:
            return value

    nested = _nested(data, block)
    for key in keys:
        value = _field(nested, key)
        if value:
            return value

    return ""


def _find_number(data: Mapping[str, Any], block: str, *keys: str) -> Optional[float]:
    """Число из поля вкладки: верхний уровень, затем блок распознавания."""
    for key in keys:
        number = _to_float(data.get(key))
        if number is not None:
            return number

    nested = _nested(data, block)
    for key in keys:
        number = _to_float(nested.get(key))
        if number is not None:
            return number

    return None


def _field(data: Mapping[str, Any], key: str) -> str:
    """Значение поля вкладки строкой без обрамляющих пробелов (None → «»)."""
    value = data.get(key)
    if value is None:
        return ""
    if isinstance(value, bool):
        # bool — подкласс int: «True» в бланке не нужен, это пустое значение.
        return ""
    return str(value).strip()


def _set_if_filled(target: Dict[str, Any], key: str, value: Any) -> None:
    """
    Кладёт значение в словарь, если оно непустое.

    Пустая строка не записывается: у вкладки-заглушки незаполненное поле
    остаётся None, и в ContractData не должно появиться ни None, ни мусора.
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
    Заполнено ли поле вкладки.

    Пустое — None, пустая строка и bool (в форме такого поля нет): значение
    «0» при этом заполнено, ноль — это ставка или сумма, а не пустота.
    """
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _has_any_value(data: Mapping[str, Any]) -> bool:
    """
    Есть ли во вкладке хоть одно заполненное значение (включая вложенные блоки).

    По этому признаку решается, заполнять ли contract.carrier_type и ставку
    НДС: совершенно пустой вход должен остаться пустым, а не получить
    «ООО, 22%» из ниоткуда.
    """
    for value in data.values():
        if isinstance(value, Mapping):
            if _has_any_value(value):
                return True
        elif isinstance(value, (list, tuple)):
            if value:
                return True
        elif _filled(value):
            return True
    return False


# ─────────────────────────────────────────────────────────────
# Числа, ставки НДС, окна времени и документы
# ─────────────────────────────────────────────────────────────

def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """
    Число из значения любого вида: «221 099,18», «269741.00», 180300, «22%».

    Разделители тысяч и запятая как десятичный разделитель — обычный формат
    документов. Пустое или непонятное значение даёт default (по умолчанию
    None — «числа нет», чтобы не подменять его нулём).
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
        .replace("%", "")
        .replace("\u00a0", "")
        .replace(" ", "")
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
    а не сумма): такое число не разбираем, чтобы не считать по нему НДС —
    вместо него генератор возьмёт ставку по умолчанию или разберёт строку
    vat_rate.
    """
    number = _to_float(value)
    if number is None or number < 0 or number > 100:
        return None
    return number


def _parse_vat_rate(value: Any) -> Optional[float]:
    """Ставка из строки бланка: «22%» → 22.0, «Без НДС» → 0.0, иначе None."""
    if value is None or isinstance(value, bool):
        return None

    text = str(value).strip()
    if not text:
        return None
    if "без ндс" in text.lower() or "не облагается" in text.lower():
        return 0.0
    return _valid_rate_num(text)


def _format_vat_rate(number: float) -> str:
    """Ставка строкой, как в бланке: 22.0 → «22%», 0.0 → «0%»."""
    if float(number).is_integer():
        return f"{number:.0f}%"
    return f"{number:g}%"


def _time_window(time_from: Any, time_to: Any) -> str:
    """
    Окно времени строкой: «08:00-18:00».

    Обе границы пусты — пустая строка; заполнена одна — она и попадает
    в окно, без выдуманной второй (как в ui/windows/logistiks_rus/data.py).
    """
    start = _field({"value": time_from}, "value")
    end = _field({"value": time_to}, "value")
    if start and end:
        return f"{start}-{end}"
    return start or end


def _digits(value: Any) -> str:
    """Только цифры значения («18 22» → «1822»); пустое — пустая строка."""
    return re.sub(r"\D", "", str(value or ""))


def _split_series_number(text: str) -> Tuple[str, str]:
    """
    Серия и номер документа из одной строки: «18 22 926830» → («18 22», «926830»).

    Паспорт и водительское удостоверение печатаются как 4 цифры серии и 6 цифр
    номера; разделители между ними бывают любыми, поэтому сначала берутся
    только цифры. Разобрать удалось не всё (нестандартный документ) — вернутся
    пустые строки, и вызывающий код оставит строку как есть.
    """
    digits = _digits(text)
    if len(digits) == 10:
        return f"{digits[:2]} {digits[2:4]}", digits[4:]
    if len(digits) == 6:
        return "", digits
    if len(digits) == 4:
        return f"{digits[:2]} {digits[2:]}", ""
    return "", ""


def _split_date(text: str) -> Tuple[str, str]:
    """
    Дата, попавшая в строку документа: («ВУ 99 36 123456 », «30.01.2023»).

    Промпт предупреждает, что дата выдачи удостоверения может стоять в той же
    строке, что и его номер (core/prompts/arenda_ts.py) — тогда её нужно
    отделить, иначе цифры даты испортят разбор номера. Даты нет — строка
    возвращается без изменений.
    """
    match = _DATE_IN_TEXT_RE.search(text or "")
    if not match:
        return text, ""
    rest = f"{text[:match.start()]} {text[match.end():]}"
    return rest, match.group(0)


# ─────────────────────────────────────────────────────────────
# Вид Арендатора
# ─────────────────────────────────────────────────────────────

def _variant_flags(carrier_type: str) -> Tuple[bool, bool, bool]:
    """
    Признаки варианта бланка: (ООО, ИП с НДС, ИП без НДС).

    Правило совпадает с ArendaTsGenerator._variant_flags: «ИП без НДС» →
    вариант без НДС, «ИП с НДС» → ИП с НДС, всё остальное — ООО.
    """
    is_ip_without_vat = CARRIER_TYPE_IP_WITHOUT_VAT in carrier_type
    is_ip_with_vat = CARRIER_TYPE_IP_WITH_VAT in carrier_type
    is_ooo = not (is_ip_without_vat or is_ip_with_vat)
    return is_ooo, is_ip_with_vat, is_ip_without_vat


def _registration(is_ooo: bool) -> Tuple[str, str]:
    """Метка госрегистрации и основание полномочий стороны: (метка, основание)."""
    if is_ooo:
        return OGRN_LABEL, BASIS_OOO
    return OGRNIP_LABEL, BASIS_IP


def _is_ip(entity_type: str) -> bool:
    """Похоже ли значение entity_type на индивидуального предпринимателя."""
    text = str(entity_type or "").strip().upper()
    return "ИП" in text or "ПРЕДПРИНИМАТЕЛЬ" in text


def _vat_rate_is_zero(data: Mapping[str, Any]) -> bool:
    """True, если ставка НДС в данных нулевая («0%», «Без НДС», 0, 0.0)."""
    rate = _valid_rate_num(_find_number(data, _CONTRACT_BLOCK, "vat_rate_num"))
    if rate is None:
        rate = _parse_vat_rate(_find_field(data, _CONTRACT_BLOCK, "vat_rate"))
    if rate is None:
        return False
    return float(rate) <= 0


def _resolve_carrier_type(
    lessee_data: Mapping[str, Any],
    price_data: Mapping[str, Any],
) -> str:
    """
    Вид Арендатора: «ООО» / «ИП с НДС» / «ИП без НДС» (маппинг 5).

    Явный выбор пользователя на вкладке «Арендатор» важнее всего. Если поле
    пустое, вид выводится из распознавания ровно как в
    ArendaTsGenerator._resolve_carrier_type: entity_type блока lessee
    («ООО» / «ИП»), а для ИП ещё и ставка НДС — «0%» означает вариант без НДС.
    Не угаданный тип даёт ООО: этот бланк и расчёт по умолчанию.
    """
    explicit = _find_field(lessee_data, _LESSEE_BLOCK, "carrier_type")
    if explicit:
        if CARRIER_TYPE_IP_WITHOUT_VAT in explicit:
            return CARRIER_TYPE_IP_WITHOUT_VAT
        if CARRIER_TYPE_IP_WITH_VAT in explicit:
            return CARRIER_TYPE_IP_WITH_VAT
        if _is_ip(explicit):
            # «ИП» без уточнения: вариант выбирает ставка НДС.
            if _vat_rate_is_zero(price_data):
                return CARRIER_TYPE_IP_WITHOUT_VAT
            return CARRIER_TYPE_IP_WITH_VAT
        return explicit

    entity_type = _find_field(lessee_data, _LESSEE_BLOCK, "entity_type")
    if _is_ip(entity_type):
        if _vat_rate_is_zero(price_data):
            return CARRIER_TYPE_IP_WITHOUT_VAT
        return CARRIER_TYPE_IP_WITH_VAT
    if entity_type:
        # «ООО», «Общество с ограниченной ответственностью» и прочие виды
        # организаций: бланк ООО — вариант по умолчанию.
        return CARRIER_TYPE_OOO

    return DEFAULT_CARRIER_TYPE


# ─────────────────────────────────────────────────────────────
# Сборка по разделам
# ─────────────────────────────────────────────────────────────

def _party_fields(
    data: Mapping[str, Any],
    block_name: str,
    fields: Tuple[Tuple[str, str], ...],
    *,
    skip: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    """
    Реквизиты стороны: поля вкладки → поля блока lessee / lessor.

    Пустые значения не записываются — незаполненная вкладка не должна
    оставлять за собой пустой блок. Поля из skip пропускаются (КПП у ИП).
    """
    party: Dict[str, Any] = {}
    for source, target in fields:
        if target in skip:
            continue
        _set_if_filled(party, target, _find_field(data, block_name, source))
    return party


def _build_lessee(data: Mapping[str, Any], carrier_type: str) -> Dict[str, Any]:
    """
    Арендатор — наша сторона: реквизиты блока lessee (разделы 1.1 и 9 бланка).

    Вид Арендатора определяет КПП (только у ООО: в ИП-бланке плейсхолдера
    lessee_kpp нет), метку госрегистрации (ОГРН / ОГРНИП) и основание
    полномочий (устав / свидетельство о регистрации). Заполненное на вкладке
    основание важнее подстановки по варианту.

    Пустая вкладка даёт пустой словарь: генератор и валидатор возьмут блок
    Арендатора из customer (ArendaTsValidator._party_block), а не из пустого
    contract["lessee"].
    """
    is_ooo = _variant_flags(carrier_type)[0]
    label, basis = _registration(is_ooo)

    party = _party_fields(
        data, _LESSEE_BLOCK, _LESSEE_FIELDS,
        skip=() if is_ooo else ("kpp",),
    )
    if not party:
        return {}

    party["entity_type"] = CARRIER_TYPE_OOO if is_ooo else "ИП"
    party["ogrn_label"] = label
    if not party.get("basis"):
        party["basis"] = basis
    return party


def _build_lessor(data: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Арендодатель — вторая сторона: реквизиты блока lessor (разделы 1.2 и 9).

    Бланки рассчитаны на Арендодателя-ООО, поэтому по умолчанию метка «ОГРН»
    и основание «Устава» — те же константы, что подставляет генератор
    (ArendaTsGenerator._fill_lessor). Если вкладка назвала Арендодателя ИП,
    метка и основание берутся для ИП: это расхождение данных, о нём
    предупредит генератор.
    """
    entity_type = _find_field(data, _LESSOR_BLOCK, "entity_type")
    is_ooo = not _is_ip(entity_type)
    label, basis = _registration(is_ooo)

    party = _party_fields(data, _LESSOR_BLOCK, _LESSOR_FIELDS)
    if not party:
        return {}

    if entity_type:
        party["entity_type"] = entity_type
    party["ogrn_label"] = label
    if not party.get("basis"):
        party["basis"] = basis
    return party


def _build_vehicle(data: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Договор и объект аренды: номер, дата, три даты рейса, тягач и прицеп.

    Возвращает две части: поля шапки (contract) и сам автопоезд. Читаются
    ТРИ РАЗНЫЕ даты (FIX-1) — каждая своим полем, без вывода одной из другой:

      * lease_start_date — начало планового периода аренды (п. 2.5);
      * lease_end_date — конец планового периода аренды (п. 2.5);
      * planned_completion_date — планируемая дата завершения рейса (п. 3.3.2).

    Срок аренды (lease_start_date / lease_end_date) лежит в корне ответа
    распознавания, поэтому читается и оттуда (см. маппинг 1). Тип ТС тягача
    обязателен: без него валидатор не пропустит договор.
    """
    contract: Dict[str, Any] = {}
    _set_if_filled(
        contract, "number",
        _find_field(data, _CONTRACT_BLOCK, "contract_number", "number"),
    )
    _set_if_filled(
        contract, "date",
        _find_field(data, _CONTRACT_BLOCK, "contract_date", "date"),
    )
    _set_if_filled(
        contract, "lease_start_date",
        _find_field(data, _CONTRACT_BLOCK, "lease_start_date"),
    )
    _set_if_filled(
        contract, "lease_end_date",
        _find_field(data, _CONTRACT_BLOCK, "lease_end_date"),
    )
    _set_if_filled(
        contract, "planned_completion_date",
        _find_field(data, _CONTRACT_BLOCK, "planned_completion_date"),
    )

    tractor: Dict[str, Any] = {}
    _set_if_filled(
        tractor, "brand_model",
        _find_field(data, "tractor", "tractor_brand", "brand_model"),
    )
    _set_if_filled(
        tractor, "plate_number",
        _find_field(data, "tractor", "tractor_plate", "plate_number"),
    )
    _set_if_filled(
        tractor, "vehicle_type",
        _find_field(data, "tractor", "tractor_type", "vehicle_type", "ts_type"),
    )

    trailer: Dict[str, Any] = {}
    _set_if_filled(
        trailer, "brand_model",
        _find_field(data, "trailer", "trailer_brand", "brand_model"),
    )
    _set_if_filled(
        trailer, "plate_number",
        _find_field(data, "trailer", "trailer_plate", "plate_number"),
    )

    return {"contract": contract, "tractor": tractor, "trailer": trailer}


def _route_points(data: Mapping[str, Any], key: str) -> List[Dict[str, Any]]:
    """
    Точки маршрута: loadings / unloadings вкладки «Маршрут».

    Точка кладётся в ПОЛНОМ виде — {name, address, date, time_from, time_to,
    time_window} (маппинг 2): время подачи ТС генератор печатает из
    time_from / time_to, а ContractData его отбрасывает. Окно времени
    собирается из границ («08:00-18:00»), а если границ нет, берётся окно,
    которое вкладка отдала одной строкой, — генератор умеет разобрать и его
    (ArendaTsGenerator._parse_time_window). Точка без названия и без адреса —
    пустая строка таблицы, в список не попадает; лишние точки (сверх 10)
    отсекаются — строк в бланке ровно 10.
    """
    raw_points = data.get(key)
    if not isinstance(raw_points, (list, tuple)):
        return []

    points: List[Dict[str, Any]] = []
    for item in raw_points:
        if not isinstance(item, Mapping):
            continue

        name = _field(item, "name")
        address = _field(item, "address")
        if not name and not address:
            continue

        time_from = _field(item, "time_from")
        time_to = _field(item, "time_to")
        points.append({
            "name": name,
            "address": address,
            "date": _field(item, "date"),
            "time_from": time_from,
            "time_to": time_to,
            # Границы точнее окна одной строкой, поэтому окно собирается из
            # них; готовая строка вкладки берётся, только если границ нет.
            "time_window": (
                _time_window(time_from, time_to) or _field(item, "time_window")
            ),
        })

    if len(points) > MAX_POINTS:
        logger.warning(
            "Разовая аренда: точек %s %s, в бланк помещается %s — лишние не выводятся",
            _POINTS_LOGGED.get(key, key), len(points), MAX_POINTS,
        )
        points = points[:MAX_POINTS]

    return points


def _build_route(data: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Маршрут: направление, точки погрузки и точки выгрузки.

    Точки возвращаются в полном виде — их кладут и в contract["loadings"] /
    ["unloadings"], и в ContractData.loadings / .unloadings (там они приведутся
    к {address, date, time_window}). Порядок строк вкладки сохраняется.
    """
    contract: Dict[str, Any] = {}
    # Маршрут и в ответе распознавания, и во вкладке — строка верхнего уровня.
    _set_if_filled(contract, "route", _field(data, "route"))

    return {
        "contract": contract,
        "loadings": _route_points(data, "loadings"),
        "unloadings": _route_points(data, "unloadings"),
    }


def _build_cargo(data: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """
    Перевозимые машины (таблица п. 3.1): марка/модель, VIN и точки в строке.

    Строка без марки и без VIN отбрасывается, лишние машины (сверх 12)
    отсекаются: в таблицу бланка больше не помещается. Тягач и прицеп
    в этот список не попадают — они в разделе 2.1 бланка.
    """
    raw_vehicles = data.get("vehicles")
    if not isinstance(raw_vehicles, (list, tuple)):
        return []

    vehicles: List[Dict[str, Any]] = []
    for item in raw_vehicles:
        if not isinstance(item, Mapping):
            continue

        brand = _field(item, "brand_model")
        vin = _field(item, "vin")
        if not brand and not vin:
            continue

        vehicle: Dict[str, Any] = {}
        _set_if_filled(vehicle, "brand_model", brand)
        _set_if_filled(vehicle, "vin", vin)
        _set_if_filled(vehicle, "loading_point", _field(item, "loading_point"))
        _set_if_filled(vehicle, "unloading_point", _field(item, "unloading_point"))
        vehicles.append(vehicle)

    if len(vehicles) > MAX_CARS:
        logger.warning(
            "Разовая аренда: машин %s, в бланк помещается %s — лишние отброшены",
            len(vehicles), MAX_CARS,
        )
        vehicles = vehicles[:MAX_CARS]

    return vehicles


def _fill_document(
    driver: Dict[str, Any],
    data: Mapping[str, Any],
    field: str,
) -> None:
    """
    Паспорт и водительское удостоверение: серия и номер отдельными полями.

    Промпт отдаёт документ одной строкой (passport / license), справочник
    водителя — серией и номером; принимаются оба вида (маппинг 4). Дата выдачи,
    попавшая в ту же строку, уходит в <field>_issue_date — но только если
    отдельного поля даты во вкладке нет.
    """
    # Уже раздельные поля вкладки — ничего не разбираем и не переписываем.
    series = _find_field(data, _CREW_BLOCK, f"driver_{field}_series", f"{field}_series")
    number = _find_field(data, _CREW_BLOCK, f"driver_{field}_number", f"{field}_number")
    if series or number:
        _set_if_filled(driver, f"{field}_series", series)
        _set_if_filled(driver, f"{field}_number", number)
        return

    raw = _find_field(data, _CREW_BLOCK, f"driver_{field}", field)
    if not raw:
        return

    text, date = _split_date(raw)
    if date and not _filled(driver.get(f"{field}_issue_date")):
        _set_if_filled(driver, f"{field}_issue_date", date)

    series, number = _split_series_number(text)
    if series or number:
        _set_if_filled(driver, f"{field}_series", series)
        _set_if_filled(driver, f"{field}_number", number)
    else:
        # Разобрать не удалось — строка остаётся как есть: и генератор
        # (_fill_driver), и валидатор (_document) принимают оба вида.
        _set_if_filled(driver, field, text)


def _build_crew(data: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Экипаж (п. 3.5): член экипажа Арендодателя — водитель.

    Собираются девять полей бланка: ФИО, дата рождения, паспорт (серия и номер),
    кем и когда выдан, водительское удостоверение (серия, номер, дата выдачи),
    адрес регистрации и телефон. Адрес регистрации распознавания лежит в поле
    address — оно и переводится в registration_address, как ждёт генератор.
    """
    driver: Dict[str, Any] = {}

    _set_if_filled(
        driver, "full_name",
        _find_field(data, _CREW_BLOCK, "driver_full_name", "full_name"),
    )
    _set_if_filled(
        driver, "birth_date",
        _find_field(data, _CREW_BLOCK, "driver_birth_date", "birth_date"),
    )
    _set_if_filled(
        driver, "passport_issuer",
        _find_field(data, _CREW_BLOCK, "driver_passport_issuer", "passport_issuer"),
    )
    _set_if_filled(
        driver, "passport_issue_date",
        _find_field(
            data, _CREW_BLOCK,
            "driver_passport_issue_date", "passport_issue_date",
        ),
    )
    _set_if_filled(
        driver, "license_issue_date",
        _find_field(
            data, _CREW_BLOCK,
            "driver_license_issue_date", "license_issue_date",
        ),
    )
    _set_if_filled(
        driver, "registration_address",
        _find_field(
            data, _CREW_BLOCK,
            "driver_registration_address", "registration_address", "address",
        ),
    )
    _set_if_filled(
        driver, "phone",
        _find_field(data, _CREW_BLOCK, "driver_phone", "phone"),
    )

    # Паспорт и удостоверение — после полей-строк: разбор не должен перетирать
    # дату выдачи, если вкладка отдала её отдельным полем.
    _fill_document(driver, data, "passport")
    _fill_document(driver, data, "license")
    return driver


def _payment_days_value(value: Any) -> Optional[int]:
    """
    Срок оплаты целым числом банковских дней: 45, «45», «45 дн.» → 45.

    Ноль и отрицательное значение читаются как «срок не задан» (None): в бланке
    п. 4.5 печатается число банковских дней, и ноль там смысла не имеет — о
    незаполненном сроке скажет валидатор. Разобрать число не удалось — тоже
    None: мусор в бланк не попадает.
    """
    number = _to_float(value)
    if number is None:
        return None
    days = int(number)
    if days <= 0:
        return None
    return days


def _build_price(
    data: Mapping[str, Any],
    carrier_type: str,
) -> Dict[str, Any]:
    """
    Арендная плата (п. 4.1) и порядок оплаты (п. 4.5): суммы, ставка НДС,
    срок оплаты в банковских днях и особые условия.

    Суммы считаются «НДС В ТОМ ЧИСЛЕ» — единым правилом ядра (core/vat.py),
    тем же, что у перевозки, Логистикса и Формики: главная величина — ИТОГ
    договора (`price_with_vat` / `sum_total`), а база без НДС и налог
    выводятся из него. До этого шага главной была БАЗА: вкладка отдавала
    сумму без НДС, а генератор считал налог сверху и прибавлял — из-за этого
    сумма в договоре отличалась от той, что называл оператор.

    Записи, сохранённые до перехода, хранят только базу без НДС: итог
    восстанавливается умножением на (1 + ставка/100) — тем же множителем,
    каким он считался раньше, поэтому пересборка старого договора даёт
    прежние суммы до копейки (проверено: round(round(база × 1.22, 2) / 1.22, 2)
    возвращает исходную базу).

    Вариант «ИП без НДС» — налогом не облагается: в бланке одна сумма, и
    плейсхолдеров sum_wo_vat / sum_vat там нет. Ставка в этом варианте
    нулевая независимо от выбранного пункта списка: бланк говорит
    «НДС не облагается», и ненулевая ставка ему противоречила бы.
    """
    contract: Dict[str, Any] = {}

    _is_ooo, _is_ip_with_vat, is_ip_without_vat = _variant_flags(carrier_type)

    # Ставка: строка «22%» / «Без НДС» важнее числа (в старых записях числа
    # могло не быть вовсе), число — запасной источник, пусто — ставка ядра.
    rate_text = normalize_vat_rate(_find_field(data, _CONTRACT_BLOCK, "vat_rate"))
    rate_num = _valid_rate_num(_find_number(data, _CONTRACT_BLOCK, "vat_rate_num"))
    if rate_num is None:
        rate_num = vat_rate_number(rate_text)
    label = rate_text or _format_vat_rate(rate_num)

    # Итог — главная величина; база нужна только для записей, где итога нет.
    total_in = _find_number(data, _CONTRACT_BLOCK, "price_with_vat", "sum_total")
    base_in = _find_number(data, _CONTRACT_BLOCK, "price_without_vat", "sum_wo_vat")

    if is_ip_without_vat:
        # НДС не облагается: в документе одна сумма — она и есть итог.
        total = total_in if total_in is not None else base_in
        rate_num = 0.0
        label = _format_vat_rate(0.0)
    elif total_in is not None and total_in > 0:
        total = total_in
    elif base_in is not None and base_in > 0:
        total = total_from_base(base_in, rate_num)
    else:
        total = None

    if total is not None:
        vat = compute_vat(total, VAT_FREE if is_ip_without_vat else rate_num)

        # Единые ключи типа: по ним генератор считает суммы (и по ним же
        # договор ложится в базу — там две колонки, база и итог).
        _set_if_filled(contract, "price_without_vat", vat["sum_wo_nds"])
        _set_if_filled(contract, "price_with_vat", vat["sum_total"])
        _set_if_filled(contract, "vat_amount", vat["sum_nds"])
        # Исторические имена сумм документа.
        _set_if_filled(contract, "sum_total", vat["sum_total"])
        if not is_ip_without_vat:
            _set_if_filled(contract, "sum_wo_vat", vat["sum_wo_nds"])
            _set_if_filled(contract, "sum_vat", vat["sum_nds"])

    _set_if_filled(
        contract, "special_conditions",
        _find_field(data, _CONTRACT_BLOCK, "special_conditions"),
    )

    # Срок оплаты (п. 4.5): целое число банковских дней. Плейсхолдера
    # {payment_days} в бланке пока нет (см. STATE.md, FIX-1) — ключ собирается
    # для бланка и валидатора, значение по умолчанию даёт вкладка (30 дней).
    _set_if_filled(
        contract, "payment_days",
        _payment_days_value(
            _find_number(data, _CONTRACT_BLOCK, "payment_days")
        ),
    )

    if _has_any_value(data):
        contract["vat_rate_num"] = rate_num
        contract["vat_rate"] = label

    return contract


# ─────────────────────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────────────────────

def _build_act(data: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Акт приёма-передачи и возврата ТС (Приложение № 1) — шаг FIX-3.

    Десять полей Акта лежат в contract простыми строками: их читает карта
    замен генератора (ArendaTsGenerator._fill_act) и печатает в таблицы
    «Передача ТС» и «Возврат ТС». До этого шага бланк печатал там пустые
    ячейки — значения можно было вписать только в Word.

    Ключи вкладки и ключи contract совпадают (соглашение вкладок аренды:
    вкладка отдаёт то, что читает генератор), поэтому перевод не нужен.
    Пустые значения не записываются: незаполненное поле Акта даёт пустое
    место в документе, а перечень документов генератор подставит сам.

    Поля ищутся и на верхнем уровне вкладки, и в блоке contract — как
    остальные разделы этого сборщика (_find_field).
    """
    act: Dict[str, Any] = {}

    for field in ACT_FIELDS:
        value = _find_field(data, _CONTRACT_BLOCK, field)
        if value:
            act[field] = value

    return act


def collect_arenda_ts_data(tabs: Mapping[str, Any]) -> ContractData:
    """
    Собирает ContractData договора аренды ТС с экипажем из вкладок окна.

    :param tabs: словарь «ключ вкладки → вкладка или dict». Ключи: lessee,
        lessor, vehicle, route, cargo, crew, price. Лишние ключи игнорируются,
        отсутствующие означают пустой раздел.
    :return: заполненный ContractData.

    Данные ложатся одним куском, пригодным и для генератора, и для валидатора:

      * блоки сторон lessee / lessor и срок аренды — ВНУТРЬ contract: корневые
        ключи распознавания ContractData.coerce не хранит (маппинг 1);
      * точки маршрута — ДВАЖДЫ: в contract["loadings"] / ["unloadings"]
        с названиями и временем подачи ТС (этот путь читают генератор и
        валидатор) и в ContractData.loadings / .unloadings в приведённом виде
        {address, date, time_window} (маппинг 2);
      * суммы — в price_without_vat / price_with_vat и заодно в sum_*: базу
        генератор берёт по виду Арендатора (маппинг 3);
      * Арендатор дублируется в customer, Арендодатель — в carrier: так их
        читает валидатор, если блока стороны в contract не окажется.

    Функция не поднимает исключений: всё, что не удалось прочитать, остаётся
    пустым и попадает в лог.
    """
    sections = _read_sections(tabs)
    lessee_data = sections["lessee"]
    lessor_data = sections["lessor"]
    price_data = sections["price"]

    carrier_type = _resolve_carrier_type(lessee_data, price_data)
    lessee = _build_lessee(lessee_data, carrier_type)
    lessor = _build_lessor(lessor_data)
    vehicle = _build_vehicle(sections["vehicle"])
    route = _build_route(sections["route"])

    contract: Dict[str, Any] = {}
    contract.update(vehicle["contract"])
    contract.update(route["contract"])
    contract.update(_build_price(price_data, carrier_type))
    # Поля Акта (Приложение № 1, шаг FIX-3) — простые строки contract.
    contract.update(_build_act(sections["act"]))
    # Вид Арендатора пишется только тогда, когда о нём есть данные: у пустого
    # входа поле остаётся незаполненным, и генератор берёт вариант по умолчанию.
    if _has_any_value(lessee_data) or _has_any_value(price_data):
        contract["carrier_type"] = carrier_type
    if lessee:
        contract["lessee"] = lessee
    if lessor:
        contract["lessor"] = lessor
    # Пустые массивы точек остаются в contract: генератор и валидатор читают
    # contract["loadings"] / ["unloadings"] и на отсутствии ключа не должны
    # отличать «точек нет» от «раздел не собирался».
    contract["loadings"] = route["loadings"]
    contract["unloadings"] = route["unloadings"]

    contract_data = ContractData(
        driver=_build_crew(sections["crew"]),
        customer={
            key: lessee[key] for key in ("full_name", "short_name") if key in lessee
        },
        carrier={
            key: lessor[key] for key in ("full_name", "short_name") if key in lessor
        },
        vehicles=_build_cargo(sections["cargo"]),
        tractor=vehicle["tractor"],
        trailer=vehicle["trailer"],
        contract=contract,
        loadings=route["loadings"],
        unloadings=route["unloadings"],
        city="",
    )

    logger.info(
        "Разовая аренда: данные формы собраны — вариант=%s, машин=%s, "
        "точек погрузки=%s, точек выгрузки=%s, экипаж=%s, тягач=%s, "
        "прицеп=%s, ставка НДС=%s; %s",
        carrier_type,
        len(contract_data.vehicles),
        len(route["loadings"]),
        len(route["unloadings"]),
        "да" if contract_data.driver.get("full_name") else "нет",
        "да" if contract_data.tractor.get("plate_number") else "нет",
        "да" if contract_data.trailer.get("plate_number") else "нет",
        contract.get("vat_rate") or "—",
        contract_data.summary(),
    )
    return contract_data


def build(
    lessee_tab: Any,
    lessor_tab: Any,
    vehicle_tab: Any,
    route_tab: Any,
    cargo_tab: Any,
    crew_tab: Any,
    price_tab: Any,
    act_tab: Any = None,
) -> ContractData:
    """
    Собирает ContractData из восьми вкладок окна «Разовая аренда».

    Точка входа для окна: вкладки передаются по именам и в порядке разделов,
    а не словарём — так вызов читается и его нельзя перепутать местами
    незаметно для теста. Раскладка полей при этом одна: метод собирает
    словарь и вызывает collect_arenda_ts_data.

    Обращений к интерфейсу здесь нет: у вкладок читается только get_data().
    Вкладка может быть None или не отдавать данные — раздел останется пустым,
    исключение не поднимется (см. _raw_data).

    :param lessee_tab: вкладка «Арендатор» (наша сторона).
    :param lessor_tab: вкладка «Арендодатель» (вторая сторона).
    :param vehicle_tab: вкладка «ТС» (номер и дата договора, срок аренды,
        тягач и прицеп).
    :param route_tab: вкладка «Маршрут» (направление, точки погрузки/выгрузки).
    :param cargo_tab: вкладка «Груз» (перевозимые автомобили).
    :param crew_tab: вкладка «Экипаж» (водитель).
    :param price_tab: вкладка «Стоимость» (суммы, НДС, особые условия).
    :param act_tab: вкладка «Акт» (Приложение № 1, шаг FIX-3). Необязательна:
        вызовы прежних шагов без неё дают те же данные, только без полей
        акта — раздел остаётся пустым.
    :return: ContractData.
    """
    return collect_arenda_ts_data({
        "lessee": lessee_tab,
        "lessor": lessor_tab,
        "vehicle": vehicle_tab,
        "route": route_tab,
        "cargo": cargo_tab,
        "crew": crew_tab,
        "price": price_tab,
        "act": act_tab,
    })


__all__ = [
    "build",
    "collect_arenda_ts_data",
    "SECTION_TITLES",
    "ACT_FIELDS",
    "CARRIER_TYPE_OOO",
    "CARRIER_TYPE_IP_WITH_VAT",
    "CARRIER_TYPE_IP_WITHOUT_VAT",
    "DEFAULT_CARRIER_TYPE",
    "DEFAULT_VAT_RATE_NUM",
    "OGRN_LABEL",
    "OGRNIP_LABEL",
    "BASIS_OOO",
    "BASIS_IP",
    "MAX_CARS",
    "MAX_POINTS",
]

