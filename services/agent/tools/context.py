#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Инструменты контекста Максимилиана (задание, Часть 1).

Зачем отдельный модуль
----------------------
Пять инструментов этой части отвечают на один вопрос: «что у оператора
сейчас открыто и что он только что делал». Они устроены иначе, чем поиск
и заполнение формы: смотрят не в базу, а в интерфейс — на окна, вкладки,
поля и последние действия. Держать их рядом с SQL-поиском неудобно, а
правила у них общие, поэтому они собраны здесь.

Все инструменты — ТОЛЬКО ЧТЕНИЕ. Ни один не меняет форму, не переключает
вкладки и не нажимает кнопки: оператор не должен обнаружить, что
Максимилиан что-то поменял, пока он просто спрашивал.

Зависимости: Qt не обязателен. Если окно передано контекстом (тесты,
скрипт), работа идёт с ним; если Qt поднят — окна ищутся в приложении;
если ни того, ни другого нет — инструмент честно отвечает «не открыто».
"""

import logging
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger("core.agent.context_tools")

#: Заголовки, которыми подписываются окна типов, если у окна нет своего.
#: Публичный тип известен ядру; названия клиентских типов лежат в приватной
#: части проекта и подмешиваются ниже (нет приватной папки — их просто нет).
TYPE_TITLES: Dict[str, str] = {
    "perevozka": "Экспедиторство",
}

#: Загружены ли уже названия типов из приватной части. Флаг есть потому,
#: что `core.agent.labels` может стать доступен НЕ сразу: при импорте `core`
#: алиасы агента поднимают этот модуль раньше, чем `attach_private_root()`
#: дополнит `__path__` пакета `core.agent` приватной папкой. Флаг ставится
#: только при УДАЧНОЙ загрузке, поэтому повтор возможен. Без этого повтора
#: клиентские типы молча выпадали бы из подсказок агента (Этап 5).
_private_titles_loaded = False


def _load_private_titles() -> None:
    """Домешивает названия клиентских типов из приватной части (один раз)."""
    global _private_titles_loaded
    if _private_titles_loaded:
        return

    try:  # приватная часть проекта: названия клиентских типов
        from core.agent.labels import TYPE_TITLES as _PRIVATE_TYPE_TITLES
    except ImportError:  # режим ядра — клиентских типов нет
        return

    TYPE_TITLES.update(_PRIVATE_TYPE_TITLES)
    _private_titles_loaded = True


class _LazyTitles(dict):
    """
    Тот же `TYPE_TITLES`, но с добором приватных названий при обращении.

    Вид словаря сохранён намеренно: `TYPE_TITLES` читают и тесты, и другие
    инструменты (`TYPE_TITLES.get(...)`, перебор, `in`). Наследник `dict`
    ведёт себя как обычный словарь, а перед ответом один раз домешивает
    приватные названия — если к этому моменту приватная часть подключена.
    """

    def _ensure(self) -> None:
        _load_private_titles()

    def get(self, key, default=None):
        self._ensure()
        return super().get(key, default)

    def __getitem__(self, key):
        self._ensure()
        return super().__getitem__(key)

    def __contains__(self, key):
        self._ensure()
        return super().__contains__(key)

    def __iter__(self) -> Iterator:
        self._ensure()
        return super().__iter__()

    def __len__(self) -> int:
        self._ensure()
        return super().__len__()

    def items(self):
        self._ensure()
        return super().items()

    def keys(self):
        self._ensure()
        return super().keys()

    def values(self):
        self._ensure()
        return super().values()


TYPE_TITLES = _LazyTitles(TYPE_TITLES)

#: Русские названия разделов формы (те же, что в подсказках инструментов).
SECTION_TITLES: Dict[str, str] = {
    "carrier_tab": "Перевозчик",
    "customer_tab": "Заказчик",
    "driver_tab": "Водитель",
    "vehicles_tab": "Перевозимые ТС",
    "contract_tab": "Договор",
    "tractor": "Тягач",
    "trailer": "Полуприцеп",
    "cargo_tab": "Груз",
    "route_tab": "Маршрут",
    "vehicle_tab": "Транспорт",
    "price_tab": "Стоимость",
    "lessor_tab": "Арендодатель",
    "lessee_tab": "Арендатор",
    "crew_tab": "Экипаж",
    "act_places_tab": "Места составления акта",
}


def _ok(data: Any = None, **extra: Any) -> Dict[str, Any]:
    """Ответ «получилось» (общий вид инструментов агента)."""
    result: Dict[str, Any] = {"ok": True, "data": data}
    result.update(extra)
    return result


def _error(message: str, **extra: Any) -> Dict[str, Any]:
    """Ответ «не получилось» с пояснением для модели."""
    result: Dict[str, Any] = {"ok": False, "error": message}
    result.update(extra)
    return result


# ─────────────────────────────────────────────────────────────
# Поиск окон
# ─────────────────────────────────────────────────────────────

def contract_windows() -> List[Any]:
    """
    Все окна типов договоров, созданные в приложении.

    Источник — ``QApplication.topLevelWidgets()``: окна типов живут до
    выхода из программы и прячутся, а не закрываются, поэтому «создано»
    и «открыто» здесь почти одно и то же. Различает их признак
    ``is_visible`` в ответе инструмента.

    Без Qt (тесты, скрипт) возвращается пустой список: контекст тогда
    берётся из ``AgentContext.window``.
    """
    try:
        from PyQt5.QtWidgets import QApplication
    except ImportError:  # Qt не установлен — работаем без окон
        return []

    app = QApplication.instance()
    if app is None:
        return []

    windows: List[Any] = []
    for widget in app.topLevelWidgets():
        # Признак окна типа — раскладка вкладок: она есть у MainWindow
        # («Экспедиторство») и у BaseContractWindow, и только у них.
        if callable(getattr(widget, "_tab_keys", None)) and hasattr(widget, "tabs"):
            windows.append(widget)
    return windows


def _window_visible(window: Any) -> bool:
    """Видно ли окно сейчас (ошибка проверки считается «не видно»)."""
    try:
        return bool(window.isVisible())
    except Exception:  # noqa: BLE001 — заглушка в тесте может не уметь
        return False


def _window_title(window: Any) -> str:
    """Название окна: заголовок окна, затем название типа, затем класс."""
    try:
        title = str(window.windowTitle() or "").strip()
    except Exception:  # noqa: BLE001
        title = ""
    if title:
        return title

    contract_type = str(getattr(window, "CONTRACT_TYPE", "") or "")
    return TYPE_TITLES.get(contract_type) or type(window).__name__


def _window_type(window: Any) -> str:
    """Ключ типа договора у окна (пусто — окно его не сообщает)."""
    return str(getattr(window, "CONTRACT_TYPE", "") or "")


def _tab_pairs(window: Any) -> List[Tuple[str, str]]:
    """
    Пары «ключ вкладки, заголовок» в порядке вкладок окна.

    Ключ берётся у окна (``_tab_keys``), заголовок — из QTabWidget: так
    ответ инструмента совпадает с тем, что оператор видит на экране.
    """
    keys = ()
    try:
        keys = tuple(window._tab_keys())
    except Exception as e:  # noqa: BLE001 — окно-заглушка может не уметь
        logger.debug("Окно не отдало ключи вкладок: %s", e)
        return []

    tabs = getattr(window, "tabs", None)
    pairs: List[Tuple[str, str]] = []
    for index, key in enumerate(keys):
        title = ""
        if tabs is not None:
            try:
                title = str(tabs.tabText(index) or "").strip()
            except Exception:  # noqa: BLE001 — вкладки может не быть
                title = ""
        pairs.append((str(key), title or SECTION_TITLES.get(str(key), str(key))))
    return pairs


def _current_tab_index(window: Any) -> int:
    """Номер открытой вкладки (-1 — окно не сообщает)."""
    tabs = getattr(window, "tabs", None)
    if tabs is None:
        return -1
    try:
        return int(tabs.currentIndex())
    except Exception:  # noqa: BLE001
        return -1


def _current_tab(window: Any) -> Tuple[str, str]:
    """Ключ и заголовок ОТКРЫТОЙ вкладки (пустые строки — если не определить)."""
    index = _current_tab_index(window)
    pairs = _tab_pairs(window)
    if index < 0 or index >= len(pairs):
        return "", ""
    return pairs[index]


#: Ключевое поле каждого раздела — то, чем раздел называется В ФОРМЕ.
#: Нужно для вопросов «какой перевозчик открыт»: оператор ждёт название
#: («ООО «Ромашка»»), а не имя вкладки (задание, часть 5).
#:
#: Имена полей сверены с живой формой (`collect_form_data` на настоящем
#: MainWindow): у перевозчика название лежит в ``full_name``, у договора
#: номер — в ``number``, маршрут — в ``route`` (вложенный блок поднят
#: наверх как ``route.route``).
PRIMARY_FIELDS: Dict[str, Tuple[str, ...]] = {
    "carrier_tab": ("full_name", "short_name", "inn"),
    "customer_tab": ("full_name", "short_name", "inn"),
    "lessor_tab": ("full_name", "short_name", "inn"),
    "lessee_tab": ("full_name", "short_name", "inn"),
    "driver_tab": ("full_name", "phone"),
    "vehicles_tab": ("brand_model", "plate_number", "vin"),
    "vehicle_tab": ("brand_model", "plate_number", "vin"),
    "tractor": ("brand_model", "plate_number", "vin"),
    "trailer": ("brand_model", "plate_number", "vin"),
    "contract_tab": ("number", "route.route", "date"),
    "cargo_tab": ("cargo_name", "name"),
    "route_tab": ("route", "loading_address", "unloading_address_1"),
    "price_tab": ("price_with_vat", "price_without_vat", "price_input"),
}


def primary_field_value(section_key: str, fields: Any) -> str:
    """
    Значение ключевого поля раздела: «чем этот раздел сейчас является».

    Пусто — если раздел пуст или поля раздела не подходят под список.
    """
    if not isinstance(fields, dict):
        return ""
    for field in PRIMARY_FIELDS.get(str(section_key or ""), ()):
        value = fields.get(field)
        if isinstance(value, (list, tuple, dict)):
            continue
        text = _short(value)
        if text:
            return text
    return ""


# ─────────────────────────────────────────────────────────────
# 1. get_open_tabs — что открыто
# ─────────────────────────────────────────────────────────────

def get_open_tabs(context: Any, **_) -> Dict[str, Any]:
    """
    Список открытых вкладок: окно, тип, вкладки и какая из них сейчас.

    Оператор говорит «тут», «в этой вкладке», «на этой странице» — и без
    этого инструмента модель не знает, о чём речь.
    """
    windows = contract_windows()
    if not windows:
        window = context.current_window() if context is not None else None
        if window is None:
            return _error(
                "ни одного окна с формой не открыто: откройте окно типа договора"
            )
        windows = [window]

    described: List[Dict[str, Any]] = []
    for window in windows:
        pairs = _tab_pairs(window)
        index = _current_tab_index(window)
        described.append({
            "window": _window_title(window),
            "contract_type": _window_type(window),
            "is_visible": _window_visible(window),
            "current_tab": pairs[index][1] if 0 <= index < len(pairs) else "",
            "current_tab_key": pairs[index][0] if 0 <= index < len(pairs) else "",
            "tabs": [title for _key, title in pairs],
        })

    visible = [item for item in described if item["is_visible"]]
    return _ok(
        described,
        count=len(described),
        visible_count=len(visible),
        message=(
            f"открыто окон: {len(described)}, из них на экране: {len(visible)}"
        ),
    )


# ─────────────────────────────────────────────────────────────
# 2. get_current_window — где мы
# ─────────────────────────────────────────────────────────────

def get_current_window(context: Any, **_) -> Dict[str, Any]:
    """
    Что за окно открыто: тип договора, название, открытая вкладка.

    Самый дешёвый вызов: модель зовёт его, чтобы понять, о какой форме
    спрашивает оператор, прежде чем читать поля.

    Кроме имени вкладки отдаётся ``current_subject`` — ЗНАЧЕНИЕ ключевого
    поля открытого раздела («ООО «Ромашка»» для перевозчика). На вопрос
    «какой перевозчик открыт» правильный ответ — это название, а не слово
    «Перевозчик» (задание, часть 5: раньше модель отвечала именем вкладки).
    """
    window = context.current_window() if context is not None else None
    if window is None:
        return _error(
            "открытая форма не найдена: откройте окно нужного типа договора"
        )

    contract_type = _window_type(window)
    key, title = _current_tab(window)
    pairs = _tab_pairs(window)

    #: Значение ключевого поля открытого раздела — читается тем же
    #: способом, что и в get_form_state, чтобы ответы не расходились.
    subject = ""
    try:
        from services.agent.tools.registry import collect_form_data

        sections = collect_form_data(window)
        if key in sections:
            subject = primary_field_value(key, sections[key])
    except Exception as e:  # noqa: BLE001 — подпись не должна ронять ответ
        logger.debug("Ключевое поле раздела не прочитано: %s", e)

    return _ok(
        {
            "window": _window_title(window),
            "contract_type": contract_type,
            "type_title": TYPE_TITLES.get(contract_type, ""),
            "current_tab": title,
            "current_tab_key": key,
            "current_subject": subject,
            "current_subject_title": SECTION_TITLES.get(key, key),
            "tabs": [item[1] for item in pairs],
        },
        is_visible=_window_visible(window),
    )


# ─────────────────────────────────────────────────────────────
# 3. get_form_state — что в полях
# ─────────────────────────────────────────────────────────────

def _short(value: Any, limit: int = 200) -> str:
    """Значение строкой с ограничением длины (для ответа модели)."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def get_form_state(context: Any, section: str = "", only_empty: bool = False,
                   **_) -> Dict[str, Any]:
    """
    Состояние полей открытой формы: значения и ПУСТЫЕ поля.

    Отличие от ``get_current_form``: тот показывает только заполненное, а
    здесь видны ещё и пустые поля — именно они нужны, когда оператор
    говорит «заполни перевозчика» и непонятно, чего не хватает.

    :param section: раздел формы (перевозчик, водитель, …); пусто — все;
    :param only_empty: True — вернуть только незаполненные поля.

    В ответе, кроме полей, идут ``current_tab`` (какая вкладка открыта) и
    ``current_subject`` — значение ключевого поля этой вкладки. По нему
    модель отвечает на «какой перевозчик открыт?» названием, а не словом
    «Перевозчик». Пустая строка в ``current_subject`` — поле не заполнено,
    и так и надо сказать оператору.
    """
    from services.agent.tools.registry import collect_form_data

    window = context.current_window() if context is not None else None
    if window is None:
        return _error(
            "открытая форма не найдена: откройте окно нужного типа договора"
        )

    sections = collect_form_data(window)
    if not sections:
        return _error("форма открыта, но поля из неё прочитать не удалось")

    wanted = str(section or "").strip()
    if wanted:
        from services.agent.tools.registry import _section_alias  # локальный импорт: общий код

        key = wanted if wanted in sections else _section_alias(wanted, sections)
        if not key:
            return _error(
                f"раздела «{wanted}» в открытой форме нет",
                available=sorted(sections),
            )
        sections = {key: sections[key]}

    result: Dict[str, Any] = {}
    for name, data in sections.items():
        filled: Dict[str, Any] = {}
        empty: List[str] = []
        for field, value in (data or {}).items():
            if isinstance(value, (list, tuple, dict)):
                if value:
                    filled[field] = value
                else:
                    empty.append(field)
                continue
            text = _short(value)
            if text:
                filled[field] = text
            else:
                empty.append(field)

        item: Dict[str, Any] = {}
        if not only_empty:
            item["filled"] = filled
            item["filled_count"] = len(filled)
        item["empty"] = sorted(empty)
        item["empty_count"] = len(empty)
        result[name] = item

    current_key, current_title = _current_tab(window)
    #: Ключевое поле открытого раздела — «какой перевозчик открыт».
    subject_section = current_key if current_key in sections else ""
    if not subject_section and len(sections) == 1:
        subject_section = next(iter(sections))
    return _ok(
        result,
        section_titles={name: SECTION_TITLES.get(name, name) for name in result},
        current_tab=current_title,
        current_tab_key=current_key,
        current_subject=primary_field_value(subject_section,
                                            sections.get(subject_section)),
        current_subject_title=SECTION_TITLES.get(subject_section, ""),
    )


# ─────────────────────────────────────────────────────────────
# 4. get_selected_row — что выбрано в таблице
# ─────────────────────────────────────────────────────────────

#: Максимум колонок в описании выбранной строки (широкие таблицы).
MAX_ROW_COLUMNS = 20


def _table_summary(table: Any, index: int) -> Optional[Dict[str, Any]]:
    """
    Описание выбранной строки таблицы: номера строк и значения по колонкам.

    Значения берутся как есть (это данные оператора в его же базе); в
    логи они не пишутся.
    """
    try:
        row = int(table.currentRow())
    except Exception:  # noqa: BLE001 — таблица-заглушка может не уметь
        return None
    if row < 0:
        return None

    headers: List[str] = []
    try:
        model = table.model()
        for column in range(min(model.columnCount(), MAX_ROW_COLUMNS)):
            header = model.headerData(column, 1, 0)  # Qt.Horizontal, DisplayRole
            headers.append(str(header or f"колонка {column + 1}"))
    except Exception:  # noqa: BLE001
        headers = []

    values: Dict[str, str] = {}
    for column, header in enumerate(headers):
        try:
            item = table.item(row, column)
            values[header] = _short(item.text() if item is not None else "")
        except Exception:  # noqa: BLE001
            values[header] = ""

    return {
        "table": index,
        "row": row,
        "values": values,
        "title": _short(getattr(table, "objectName", lambda: "")() or ""),
    }


def get_selected_row(context: Any, window: Any = None, **_) -> Dict[str, Any]:
    """
    Что выбрано в таблице открытой формы (перевозимые ТС, адреса, список).

    Только чтение: ни выделение, ни текущая ячейка не меняются.
    """
    target = window if window is not None else (
        context.current_window() if context is not None else None
    )
    if target is None:
        return _error(
            "открытая форма не найдена: откройте окно нужного типа договора"
        )

    try:
        from PyQt5.QtWidgets import QTableWidget
    except ImportError:  # Qt не установлен — таблиц заведомо нет
        return _error("в открытой форме нет таблиц с данными")

    tables = target.findChildren(QTableWidget) if hasattr(target, "findChildren") else []
    if not tables:
        return _error("в открытой форме нет таблиц с данными")

    found: List[Dict[str, Any]] = []
    for index, table in enumerate(tables):
        summary = _table_summary(table, index)
        if summary is not None:
            found.append(summary)

    if not found:
        return _ok(
            [],
            count=0,
            message="ни в одной таблице открытой формы строка не выбрана",
            tables=len(tables),
        )
    return _ok(found, count=len(found), tables=len(tables))


# ─────────────────────────────────────────────────────────────
# 5. get_recent_actions — что делал оператор
# ─────────────────────────────────────────────────────────────

#: Окно наблюдения по умолчанию — пять минут (задание, часть 1).
RECENT_SECONDS = 300


def get_recent_actions(context: Any, minutes: int = 5, **_) -> Dict[str, Any]:
    """
    Что оператор делал последние N минут (нажатия кнопок).

    Источник — журнал действий интерфейса (ui/action_logging.py). В него
    попадают ТОЛЬКО надписи кнопок и название окна: значения из полей
    формы там не сохраняются, поэтому личные данные сюда попасть не могут.

    :param minutes: сколько минут назад смотреть (1..60).
    """
    try:
        span = int(minutes)
    except (TypeError, ValueError):
        return _error("параметр minutes должен быть числом минут (например, 5)")
    span = max(1, min(60, span))

    from ui.action_logging import recent_actions

    actions = recent_actions(span * 60)
    if not actions:
        return _ok(
            [],
            count=0,
            minutes=span,
            message=(
                "за это время нажатий кнопок не было "
                "(или журнал действий не ведётся)"
            ),
        )

    # Новые в конце — оператору и модели удобнее видеть последнее первым.
    ordered = list(reversed(actions))
    return _ok(
        ordered[:20],
        count=len(ordered),
        minutes=span,
        message=f"последних действий: {len(ordered)} (новые первыми)",
    )


__all__ = [
    "RECENT_SECONDS",
    "SECTION_TITLES",
    "TYPE_TITLES",
    "contract_windows",
    "get_current_window",
    "get_form_state",
    "get_open_tabs",
    "get_recent_actions",
    "get_selected_row",
]
