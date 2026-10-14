#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проактивность Максимилиана (задание, часть 4).

Задача части: помощник не ждёт вопроса, а сам замечает, что в форме чего-то
не хватает, и предлагает это заполнить. Здесь живёт «мозг» этой части —
чистая логика без Qt и без базы:

  * ``REQUIRED`` / ``ORDER`` — какие поля обязательны у каждого типа
    договора и в каком порядке их обычно заполняют;
  * ``missing_required()`` — чего в форме не хватает;
  * ``next_field()`` — что логично заполнить следующим;
  * ``warn_items()`` — что истекает (по готовым спискам из базы);
  * ``HabitMemory`` — привычки оператора: что он обычно делает в этом
    месте формы (наполняется журналом действий, см. ui/action_logging.py);
  * ``SuggestionEngine`` — решает, показывать ли подсказку: не чаще одной
    в ``interval`` секунд и только если подсказки включены настройкой.

Почему отдельный модуль: правила «что обязательно» и «что дальше» — это
знание о формах, а не о Qt и не о диалоге с моделью. Их проверяют тестами
без поднятия интерфейса, а инструменты агента (core/agent/proactivity_tools.py)
и панель только пользуются готовыми ответами.
"""

import logging
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("core.agent.proactivity")

#: Как часто максимум показывать подсказку (секунды). Задание, часть 4:
#: «не навязчиво: не больше 1 подсказки в 5 минут».
SUGGESTION_INTERVAL = 300.0

#: Через сколько дней считать срок «истекающим» по умолчанию.
DEFAULT_WARNING_DAYS = 30


# ─────────────────────────────────────────────────────────────
# Обязательные поля и порядок заполнения
# ─────────────────────────────────────────────────────────────
#: Общая часть «Экспедиторства»: она же основа для остальных типов.
#: Ключ раздела — как у вкладок формы (driver_tab, carrier_tab…),
#: значение — пары «поле, подпись».
_REQUIRED_BASE: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "carrier_tab": (
        ("full_name", "наименование перевозчика"),
        ("inn", "ИНН перевозчика"),
    ),
    "driver_tab": (
        ("full_name", "ФИО водителя"),
        ("license_number", "номер водительского удостоверения"),
        ("phone", "телефон водителя"),
    ),
    "customer_tab": (
        ("full_name", "наименование заказчика"),
        ("inn", "ИНН заказчика"),
    ),
    "contract_tab": (
        ("number", "номер договора"),
        ("date", "дата договора"),
        ("route.route", "маршрут"),
        ("route.price_with_vat", "сумма договора"),
    ),
    "vehicles_tab": (
        ("vin", "VIN машины"),
        ("brand_model", "марка и модель машины"),
    ),
    "tractor": (
        ("tractor_brand", "марка тягача"),
        ("tractor_plate", "госномер тягача"),
    ),
    "trailer": (
        ("trailer_brand", "марка полуприцепа"),
        ("trailer_plate", "госномер полуприцепа"),
    ),
}

#: Обязательные поля по типам договоров (задание, часть 4: «check_required»).
REQUIRED: Dict[str, Dict[str, Tuple[Tuple[str, str], ...]]] = {
    "perevozka": dict(_REQUIRED_BASE),
    "arenda_ts": {
        "lessor_tab": (
            ("full_name", "наименование арендодателя"),
            ("inn", "ИНН арендодателя"),
        ),
        "lessee_tab": (
            ("full_name", "наименование арендатора"),
            ("inn", "ИНН арендатора"),
        ),
        "crew_tab": (
            ("full_name", "ФИО водителя"),
            ("license_number", "номер водительского удостоверения"),
        ),
        "vehicle_tab": (
            ("brand_model", "марка и модель ТС"),
            ("plate_number", "госномер ТС"),
        ),
        "price_tab": (
            ("price_with_vat", "стоимость аренды"),
        ),
    },
    "logistiks_rus": {
        "cargo_tab": (
            ("brand_model", "марка и модель машины"),
            ("vin", "VIN машины"),
        ),
        "route_tab": (
            ("loading_address", "адрес погрузки"),
            ("unloading_address", "адрес выгрузки"),
        ),
        "contract_tab": (
            ("number", "номер договора"),
            ("date", "дата договора"),
        ),
    },
    "formika": {
        "cargo_tab": (
            ("brand_model", "марка и модель машины"),
            ("vin", "VIN машины"),
        ),
        "contract_tab": (
            ("number", "номер договора"),
            ("date", "дата договора"),
        ),
    },
    "zayavka_excel": {
        "cargo_tab": (
            ("brand_model", "марка и модель машины"),
            ("vin", "VIN машины"),
        ),
        "contract_tab": (
            ("number", "номер заявки"),
            ("date", "дата заявки"),
        ),
    },
}

#: Порядок заполнения формы: что за чем логично вводить. Порядок общий для
#: всех типов — он про работу оператора, а не про конкретный бланк.
ORDER: Tuple[str, ...] = (
    "carrier_tab", "customer_tab", "driver_tab", "tractor", "trailer",
    "vehicle_tab", "vehicles_tab", "lessor_tab", "lessee_tab", "crew_tab",
    "cargo_tab", "route_tab", "loadings", "unloadings", "contract_tab",
    "price_tab", "act_places_tab",
)

#: Русские названия разделов, которые оператор видит в интерфейсе.
SECTION_TITLES: Dict[str, str] = {
    "carrier_tab": "Перевозчик",
    "customer_tab": "Заказчик",
    "driver_tab": "Водитель",
    "tractor": "Тягач",
    "trailer": "Полуприцеп",
    "trailer_tab": "Тягач/прицеп",
    "vehicles_tab": "Перевозимые ТС",
    "vehicle_tab": "Транспорт",
    "contract_tab": "Договор",
    "lessor_tab": "Арендодатель",
    "lessee_tab": "Арендатор",
    "crew_tab": "Экипаж",
    "cargo_tab": "Груз",
    "route_tab": "Маршрут",
    "price_tab": "Стоимость",
    "act_places_tab": "Места составления акта",
    "loadings": "Погрузки",
    "unloadings": "Выгрузки",
}

#: Поля, которые обычно заполняют, хотя они не обязательны (подсказки).
RECOMMENDED: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "driver_tab": (
        ("passport_number", "серия и номер паспорта"),
        ("license_expiry_date", "срок действия ВУ"),
    ),
    "carrier_tab": (
        ("kpp", "КПП перевозчика"),
        ("legal_address", "юридический адрес перевозчика"),
        ("director_name", "руководитель перевозчика"),
    ),
    "customer_tab": (
        ("kpp", "КПП заказчика"),
        ("legal_address", "юридический адрес заказчика"),
    ),
    "contract_tab": (
        ("start_date", "дата начала работ"),
        ("end_date", "дата окончания работ"),
    ),
    "vehicles_tab": (
        ("plate_number", "госномер машины"),
        ("year", "год выпуска машины"),
    ),
}


def required_fields(contract_type: str) -> Dict[str, Tuple[Tuple[str, str], ...]]:
    """
    Обязательные поля типа договора: раздел → пары «поле, подпись».

    Неизвестный тип получает общий набор «Экспедиторства»: у окна без
    собственного списка лучше подсказать хоть что-то осмысленное, чем
    молчать.
    """
    key = str(contract_type or "").strip()
    return REQUIRED.get(key, REQUIRED["perevozka"])


def section_title(section: str) -> str:
    """Русское название раздела формы (для текста подсказки)."""
    key = str(section or "").strip()
    return SECTION_TITLES.get(key, key)


def order_index(section: str) -> int:
    """Место раздела в порядке заполнения (неизвестный — в конце)."""
    key = str(section or "").strip()
    try:
        return ORDER.index(key)
    except ValueError:
        return len(ORDER)


def _field_value(section_data: Any, field: str) -> Any:
    """
    Значение поля раздела, в том числе вложенного («route.route»).

    Данные формы приходят из ``collect_form_data``: раздел — словарь, а
    вложенные блоки (реквизиты, маршрут) — тоже словари. Путь с точкой
    разбирается по частям.
    """
    current = section_data
    for part in str(field or "").split("."):
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, (list, tuple)):
            return current
        else:
            return None
    return current


def _is_empty(value: Any) -> bool:
    """
    Пусто ли значение поля.

    Пусто: None, пустая строка, строка из пробелов, пустой список (у
    «Перевозимых ТС» строк может не быть вовсе) и ноль в поле-сумме —
    ноль оператор не вводит специально.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return float(value) == 0
    return False


def missing_required(
    sections: Dict[str, Any],
    contract_type: str = "perevozka",
    *,
    only_sections: Optional[Iterable[str]] = None,
) -> List[Dict[str, str]]:
    """
    Чего не хватает в форме: список «раздел → поле → подпись».

    Проверяются ТОЛЬКО те разделы, которые форма отдаёт: если вкладки с
    таким именем у окна нет (у «Аренды» нет перевозчика), её обязательные
    поля не считаются пропущенными — иначе подсказка была бы ложной.

    :param sections: данные формы (``collect_form_data`` или заглушка);
    :param contract_type: тип договора (ключ из core/contracts);
    :param only_sections: ограничить проверку этими разделами.
    """
    wanted = set(only_sections) if only_sections else None
    result: List[Dict[str, str]] = []

    for section, fields in required_fields(contract_type).items():
        if section not in sections:
            continue
        if wanted is not None and section not in wanted:
            continue
        data = sections.get(section)
        if isinstance(data, list):
            # Таблица (перевозимые ТС): пустой список — все поля пусты.
            if data:
                continue
            for field, title in fields:
                result.append(_item(section, field, title, "обязательное поле"))
            continue
        for field, title in fields:
            if _is_empty(_field_value(data, field)):
                result.append(_item(section, field, title, "обязательное поле"))
    return result


def recommended_missing(
    sections: Dict[str, Any],
    *,
    only_sections: Optional[Iterable[str]] = None,
) -> List[Dict[str, str]]:
    """Что стоит заполнить, хотя это и не обязательно."""
    wanted = set(only_sections) if only_sections else None
    result: List[Dict[str, str]] = []
    for section, fields in RECOMMENDED.items():
        if section not in sections:
            continue
        if wanted is not None and section not in wanted:
            continue
        data = sections.get(section)
        for field, title in fields:
            if _is_empty(_field_value(data, field)):
                result.append(_item(section, field, title, "обычно заполняют"))
    return result


def _item(section: str, field: str, title: str, reason: str) -> Dict[str, str]:
    """Одна недостающая позиция в общем виде."""
    return {
        "section": section,
        "section_title": section_title(section),
        "field": field,
        "title": title,
        "reason": reason,
    }


def next_field(
    sections: Dict[str, Any],
    contract_type: str = "perevozka",
    *,
    limit: int = 5,
) -> Dict[str, Any]:
    """
    Что логично заполнить следующим.

    Порядок: обязательные поля в порядке заполнения формы, затем полезные
    необязательные. Возвращается первая позиция и ещё несколько — чтобы
    модель могла предложить выбор, а не одну строчку.

    :return: словарь «next» (или None), «others», «missing_count» и «text».
    """
    missing = missing_required(sections, contract_type)
    extra = recommended_missing(sections)

    def sort_key(item: Dict[str, str]) -> Tuple[int, int]:
        """Сначала обязательные, внутри группы — по порядку разделов."""
        group = 0 if item["reason"] == "обязательное поле" else 1
        return (group, order_index(item["section"]))

    queue = sorted(missing + extra, key=sort_key)
    first = queue[0] if queue else None
    others = queue[1: max(1, int(limit))]

    return {
        "next": first,
        "others": others,
        "missing_count": len(missing),
        "recommended_count": len(extra),
        "contract_type": str(contract_type or ""),
        "text": _next_text(first, len(missing), len(extra)),
    }


def _next_text(first: Optional[Dict[str, str]], missing: int,
               extra: int) -> str:
    """Фраза подсказки «что заполнить дальше»."""
    if first is None:
        return "В форме заполнено всё, что нужно."
    text = (f"Дальше логично заполнить: {first['title']} "
            f"({first['section_title']}).")
    if missing > 1:
        text += f" Обязательных полей не хватает: {missing}."
    elif missing == 1:
        text += " Это последнее обязательное поле."
    elif extra:
        text += " Обязательное всё заполнено — остались необязательные поля."
    return text


# ─────────────────────────────────────────────────────────────
# Сроки: что истекает
# ─────────────────────────────────────────────────────────────

def warn_items(
    licences: Sequence[Dict[str, Any]] = (),
    contracts: Sequence[Dict[str, Any]] = (),
    *,
    days: int = DEFAULT_WARNING_DAYS,
    today: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """
    Собирает предупреждения о сроках из готовых списков базы.

    Данные приходят снаружи (``db/crud/drivers.py::get_expiring_licenses`` и
    ``db/crud/contracts.py::get_expiring_contracts``): этот модуль ничего не
    знает про SQL, а только складывает предупреждения в один ответ.

    :return: словарь «warnings» (по видам), «count» и «text».
    """
    warnings: List[Dict[str, Any]] = []
    total = 0

    licences = list(licences or ())
    if licences:
        items = [_warn_item(item, "ВУ", "driver_name", "license_expiry")
                 for item in licences[: max(1, int(limit))]]
        warnings.append({
            "kind": "driver_license",
            "title": "водительские удостоверения",
            "count": len(licences),
            "items": items,
        })
        total += len(licences)

    contracts = list(contracts or ())
    if contracts:
        items = [
            {
                "id": item.get("id"),
                "title": (
                    f"Договор {item.get('contract_number') or item.get('id')}"
                    f" — до {item.get('end_date')}"
                ),
                "date": _iso_day(item.get("end_date")),
                "detail": str(item.get("route") or item.get("carrier_name") or ""),
            }
            for item in contracts[: max(1, int(limit))]
        ]
        warnings.append({
            "kind": "contract",
            "title": "договоры",
            "count": len(contracts),
            "items": items,
        })
        total += len(contracts)

    return {
        "warnings": warnings,
        "count": total,
        "days": int(days),
        "on": str(today or ""),
        "text": _warn_text(warnings, int(days), total),
    }


def _warn_item(item: Dict[str, Any], kind: str, name_key: str,
               date_key: str) -> Dict[str, Any]:
    """Одно предупреждение о сроке."""
    return {
        "id": item.get("id"),
        "title": str(item.get(name_key) or "").strip() or f"запись {item.get('id')}",
        "kind": kind,
        "date": _iso_day(item.get(date_key)),
        "detail": str(item.get("phone") or ""),
    }


def _iso_day(value: Any) -> str:
    """Дата в виде ГГГГ-ММ-ДД (пусто — не разобрали)."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        from core.dates import to_iso
        return to_iso(text) or text
    except Exception:  # noqa: BLE001 — парсер дат не должен ломать подсказку
        return text


def _warn_text(warnings: Sequence[Dict[str, Any]], days: int,
               total: int) -> str:
    """Фраза предупреждения о сроках."""
    if not total:
        return f"Ничего не истекает в ближайшие {days} дней."
    parts = []
    for item in warnings:
        titles = ", ".join(
            str(entry.get("title") or "") for entry in item["items"][:3]
        )
        parts.append(f"{item['title']}: {item['count']} ({titles})")
    return f"Истекает в ближайшие {days} дней — " + "; ".join(parts)


# ─────────────────────────────────────────────────────────────
# Привычки: что оператор обычно делает в этом месте формы
# ─────────────────────────────────────────────────────────────

#: Что логично сделать после действия оператора (привычка «шаг → шаг»).
NEXT_STEP: Dict[str, str] = {
    "Загрузить в форму": "заполнить следующие поля этой же записи",
    "Распознать": "проверить распознанные поля и перенести их в форму",
    "Распознать вкладку": "проверить распознанные поля вкладки",
    "Распознать и заполнить": "проверить заполненные поля",
    "Создать договор": "проверить договор и сохранить его в базу",
    "Сохранить в базу": "создать договор",
    "Очистить форму": "заполнить форму заново",
    "Добавить ТС": "заполнить марку и VIN добавленной машины",
    "Добавить погрузку": "указать адрес и дату погрузки",
    "Добавить выгрузку": "указать адрес и дату выгрузки",
    "Из справочника": "выбрать запись и подтянуть её в форму",
    "Импорт из Excel": "проверить загруженный файл",
    "Добавить в справочник": "заполнить реквизиты новой записи",
}

#: Поля, которые обычно заполняют после загрузки записи из справочника.
SECTION_NEXT_STEP: Dict[str, str] = {
    "carrier_tab": "подтянуть водителя этого перевозчика",
    "driver_tab": "указать тягач и полуприцеп водителя",
    "customer_tab": "заполнить условия договора",
    "vehicles_tab": "указать погрузку и выгрузку для машины",
    "contract_tab": "проверить маршрут и сумму",
}


class HabitMemory:
    """
    Привычки оператора: какие действия он обычно делает в этом месте.

    Память in-memory и намеренно простая: она нужна для подсказки «что
    обычно делаешь тут», а не для аналитики. Хранятся ТОЛЬКО надписи
    действий и ключи разделов — значения полей сюда не попадают
    (правило проекта: логи и подсказки без персональных данных).

    Долговременные привычки и исправления живут в базе (таблица
    agent_memory, часть 5): сюда попадает только то, что видно из журнала
    действий за последние минуты.
    """

    #: Сколько последних действий держим на раздел.
    LIMIT = 20

    def __init__(self) -> None:
        self._by_section: Dict[str, List[str]] = {}
        self._by_window: Dict[str, List[str]] = {}

    def remember(self, action: str, *, section: str = "",
                 window: str = "") -> None:
        """Запоминает действие оператора (надпись и место — без значений)."""
        label = str(action or "").strip()
        if not label:
            return
        if section:
            self._push(self._by_section, str(section), label)
        if window:
            self._push(self._by_window, str(window), label)

    @staticmethod
    def _push(storage: Dict[str, List[str]], key: str,
              label: str) -> None:
        """Кладёт действие в список ключа, не давая ему расти."""
        items = storage.setdefault(key, [])
        items.append(label)
        if len(items) > HabitMemory.LIMIT:
            del items[: len(items) - HabitMemory.LIMIT]

    def frequent(self, section: str = "", window: str = "",
                 limit: int = 3) -> List[Dict[str, Any]]:
        """
        Что оператор чаще всего делал в этом разделе (или окне).

        Возвращает список «действие → сколько раз», начиная с частого.
        Пусто — привычек пока нет: подсказка тогда честно молчит.
        """
        counter: Dict[str, int] = {}
        for label in self._labels(section, window):
            counter[label] = counter.get(label, 0) + 1
        ordered = sorted(counter.items(), key=lambda pair: (-pair[1], pair[0]))
        return [
            {"action": label, "count": count}
            for label, count in ordered[: max(1, int(limit))]
        ]

    def _labels(self, section: str, window: str) -> List[str]:
        """Надписи действий: по разделу, иначе по окну."""
        key = str(section or "").strip()
        if key and key in self._by_section:
            return list(self._by_section[key])
        window_key = str(window or "").strip()
        if window_key and window_key in self._by_window:
            return list(self._by_window[window_key])
        return []

    def clear(self) -> None:
        """Забыть привычки (нужно тестам и смене окна)."""
        self._by_section.clear()
        self._by_window.clear()


#: Общая память привычек приложения. Инструмент агента читает её.
_HABITS = HabitMemory()


def habits() -> HabitMemory:
    """Память привычек приложения (одна на процесс)."""
    return _HABITS


def suggest_from_history(
    actions: Sequence[Dict[str, Any]] = (),
    *,
    section: str = "",
    window: str = "",
    memory: Optional[HabitMemory] = None,
    limit: int = 3,
) -> Dict[str, Any]:
    """
    Что обычно делают в этом месте формы.

    Источники по порядку: последнее действие оператора (журнал нажатий),
    привычки этого раздела (``HabitMemory``) и привычки этого окна.
    Возвращается подсказка «что дальше» с пояснением, откуда она взялась.
    """
    memory = memory if memory is not None else habits()
    recent = [str(item.get("action") or "").strip()
              for item in (actions or ())
              if str(item.get("action") or "").strip()]

    frequent = memory.frequent(section, window, limit=limit)
    last_action = recent[-1] if recent else ""

    step = ""
    source = ""
    if last_action and last_action in NEXT_STEP:
        step = NEXT_STEP[last_action]
        source = "последнее действие оператора"
    elif section and str(section) in SECTION_NEXT_STEP:
        step = SECTION_NEXT_STEP[str(section)]
        source = "этап работы с формой"
    elif frequent:
        step = f"обычно здесь: {frequent[0]['action']}"
        source = "привычки оператора"

    return {
        "last_action": last_action,
        "recent_actions": recent[-5:],
        "frequent": frequent,
        "step": step,
        "source": source,
        "text": _history_text(step, last_action, frequent, source),
    }


def _history_text(step: str, last_action: str,
                  frequent: Sequence[Dict[str, Any]], source: str) -> str:
    """Фраза подсказки «что обычно делаешь тут»."""
    if not step:
        return "Пока не видно, что вы обычно делаете в этом месте формы."
    text = f"Обычно дальше: {step}."
    if last_action:
        text += f" Последнее действие — «{last_action}»."
    if source:
        text += f" (Источник: {source}.)"
    if frequent:
        habits_text = ", ".join(
            f"{item['action']} ×{item['count']}" for item in frequent[:3]
        )
        text += f" Чаще всего здесь: {habits_text}."
    return text


# ─────────────────────────────────────────────────────────────
# Движок подсказок: когда их показывать
# ─────────────────────────────────────────────────────────────

class SuggestionEngine:
    """
    Решает, показывать ли подсказку: включена ли она и не рано ли снова.

    «Не навязчиво» из задания — это одно правило: между подсказками должно
    пройти не меньше ``interval`` секунд (по умолчанию 5 минут). Движок
    ничего не рисует: он только отвечает «да/нет» и что именно сказать, а
    показывает панель (ui/agent_widget.py).
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        interval: float = SUGGESTION_INTERVAL,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.interval = max(0.0, float(interval))
        #: Часы подменяются тестами: ждать пять минут в тесте нельзя.
        self._clock = clock or time.monotonic
        self._last_shown: Optional[float] = None
        self._last_text = ""

    def configure(self, enabled: bool, interval: Optional[float] = None) -> None:
        """Включает/выключает подсказки (зовётся при смене настроек)."""
        self.enabled = bool(enabled)
        if interval is not None:
            self.interval = max(0.0, float(interval))

    def reset(self) -> None:
        """Забывает время последней подсказки (смена окна, новый оператор)."""
        self._last_shown = None
        self._last_text = ""

    def due(self, text: str = "") -> bool:
        """
        Пора ли показывать подсказку.

        Учитывается: включены ли подсказки; прошёл ли интервал; не тот же
        ли это текст, что в прошлый раз (повторять одно и то же оператору
        незачем — это и есть навязчивость).
        """
        if not self.enabled:
            return False
        body = str(text or "").strip()
        if not body:
            return False
        if body == self._last_text:
            return False
        if self._last_shown is None:
            return True
        return (self._clock() - self._last_shown) >= self.interval

    def mark_shown(self, text: str) -> None:
        """Отмечает, что подсказка показана (время и текст)."""
        self._last_shown = self._clock()
        self._last_text = str(text or "").strip()

    def offer(self, text: str) -> str:
        """
        Возвращает текст, если подсказку пора показать, иначе пустую строку.

        Единственный метод, который нужен вызывающему коду: «есть что
        сказать и можно ли сейчас» одним вызовом. Отметку о показе ставит
        он же — вызывающему не нужно помнить про два шага.
        """
        body = str(text or "").strip()
        if not self.due(body):
            return ""
        self.mark_shown(body)
        return body

    @property
    def last_text(self) -> str:
        """Текст последней показанной подсказки (для тестов и панели)."""
        return self._last_text


__all__ = [
    "DEFAULT_WARNING_DAYS",
    "ORDER",
    "RECOMMENDED",
    "REQUIRED",
    "SECTION_TITLES",
    "SUGGESTION_INTERVAL",
    "HabitMemory",
    "SuggestionEngine",
    "habits",
    "missing_required",
    "next_field",
    "order_index",
    "recommended_missing",
    "required_fields",
    "section_title",
    "suggest_from_history",
    "warn_items",
]
