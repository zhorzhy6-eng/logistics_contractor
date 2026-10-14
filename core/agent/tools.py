#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Инструменты Максимилиана (задание, часть B).

Инструмент — это описание (имя, текст для модели, JSON Schema аргументов)
плюс функция. Функция ВСЕГДА возвращает словарь одного вида:

    {"ok": True,  "data": ...}      — получилось
    {"ok": False, "error": "..."}   — не получилось (текст для модели)

Правила, которые здесь соблюдаются:

  * никакого SQL: работа с базой — только через db/crud/*;
  * чтение формы — только чтение (ContractData API не меняется);
  * опасные действия (заполнение формы) помечены requires_confirmation=True:
    агент спрашивает оператора перед вызовом (core/agent/agent.py);
  * удаления, создания договоров и работы с деньгами нет вовсе.

Инструменты не знают про Qt: «окно» — это объект с вкладками (get_data /
fill_data), каким его видит ui/agent_widget.py. Поэтому весь модуль
проверяется тестами без поднятия интерфейса.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.agent.words import NON_NAME_WORDS

logger = logging.getLogger("core.agent.tools")

#: Сколько записей максимум отдаёт один поиск.
MAX_SEARCH_RESULTS = 10

#: Длина одного значения в ответе инструмента: длинные адреса и описания
#: ни к чему не помогают модели, а токены тратят.
MAX_VALUE_CHARS = 300


# ─────────────────────────────────────────────────────────────
# Описание инструмента
# ─────────────────────────────────────────────────────────────

@dataclass
class Tool:
    """
    Один инструмент агента (задание, часть A.3).

    :param requires_confirmation: True — вызов выполняется только после
        подтверждения оператором (диалог показывает ui/agent_widget.py).
    :param summary: шаблон строки «что собираемся сделать» для диалога
        подтверждения, например «Заполнить поле {field}».
    """

    name: str
    description: str
    parameters: Dict[str, Any]
    function: Callable[..., Any]
    requires_confirmation: bool = False
    summary: str = ""
    #: Имена аргументов, которые НЕ попадают в диалог подтверждения
    #: (значения полей бывают большими — в диалоге достаточно имени поля).
    hide_value_args: Tuple[str, ...] = field(default_factory=tuple)

    def schema(self) -> Dict[str, Any]:
        """Описание инструмента в формате GigaChat (OpenAI-совместимый)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def confirmation_text(self, arguments: Dict[str, Any]) -> str:
        """
        Текст вопроса для подтверждения (без раскрытия лишних значений).

        Значения аргументов из ``hide_value_args`` в вопрос не попадают:
        оператор увидит их в самой форме после заполнения.
        """
        parts = []
        for key, value in (arguments or {}).items():
            if key in self.hide_value_args:
                continue
            text = _short(value, 80)
            parts.append(f"{key} = «{text}»" if text else f"{key} = (пусто)")
        args_text = ", ".join(parts) if parts else "без аргументов"
        base = self.summary or f"Вызвать инструмент «{self.name}»"
        return f"{base}\n\nАргументы: {args_text}"


class ToolRegistry:
    """Реестр инструментов: поиск по имени и описания для модели."""

    def __init__(self, tools: Sequence[Tool] = ()):
        self._tools: Dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        """Добавляет инструмент; повторное имя заменяет прежний."""
        if not tool.name:
            raise ValueError("Инструмент без имени зарегистрировать нельзя")
        if tool.name in self._tools:
            logger.warning("Инструмент %r переопределён", tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        """Инструмент по имени (None — такого нет)."""
        return self._tools.get(str(name or "").strip())

    def names(self) -> Tuple[str, ...]:
        """Имена инструментов в порядке регистрации."""
        return tuple(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())

    def schemas(self) -> List[Dict[str, Any]]:
        """Все описания для запроса к GigaChat."""
        return [tool.schema() for tool in self._tools.values()]

    def prompt_lines(self) -> List[Tuple[str, str]]:
        """Пары «имя, описание» — для системного промпта."""
        return [(tool.name, tool.description) for tool in self._tools.values()]

    def call(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Вызывает инструмент и приводит результат к общему виду.

        Исключение внутри инструмента не роняет агента: модель получает
        ``{"ok": False, "error": …}`` и может объяснить проблему оператору.
        """
        tool = self.get(name)
        if tool is None:
            logger.warning("Агент запросил неизвестный инструмент %r", name)
            return {
                "ok": False,
                "error": f"инструмента «{name}» не существует",
                "available": list(self.names()),
            }

        try:
            result = tool.function(**(arguments or {}))
        except TypeError as e:
            # Частая причина — модель придумала аргумент, которого нет.
            logger.warning("Инструмент %s: неверные аргументы (%s)", name, e)
            return {"ok": False, "error": f"неверные аргументы вызова: {e}"}
        except Exception as e:  # noqa: BLE001 — инструмент не должен ронять агента
            logger.exception("Инструмент %s упал", name)
            return {"ok": False, "error": f"ошибка инструмента: {type(e).__name__}: {e}"}

        if not isinstance(result, dict) or "ok" not in result:
            logger.error("Инструмент %s вернул результат неверного вида", name)
            return {
                "ok": False,
                "error": "инструмент вернул результат неверного вида",
            }
        return result


# ─────────────────────────────────────────────────────────────
# Вспомогательное
# ─────────────────────────────────────────────────────────────

def _short(value: Any, limit: int = MAX_VALUE_CHARS) -> str:
    """Строка значения с ограничением длины (значения бывают «полотном»)."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _error(message: str, **extra: Any) -> Dict[str, Any]:
    """Ответ «не получилось» с дополнительными пояснениями."""
    result: Dict[str, Any] = {"ok": False, "error": message}
    result.update(extra)
    return result


def _ok(data: Any = None, **extra: Any) -> Dict[str, Any]:
    """Ответ «получилось»."""
    result: Dict[str, Any] = {"ok": True, "data": data}
    result.update(extra)
    return result


#: Признак «метода нет». Нужен, потому что заполнение формы возвращает
#: None даже при успехе: по одному None отличить вызов от его отсутствия
#: нельзя, а от этого зависит «поле заполнено / форма не приняла значение».
_MISSING = object()


def _call(target: Any, method: str, *args: Any) -> Any:
    """
    Зовёт метод объекта и возвращает результат (``_MISSING`` — метода нет).

    Отсутствие метода (другая версия вкладки, заглушка в тесте) не должно
    превращаться в исключение: вызывающий код сам решит, что делать.
    """
    func = getattr(target, method, None)
    if not callable(func):
        return _MISSING
    try:
        return func(*args)
    except Exception as e:  # noqa: BLE001 — форма не должна ронять инструмент
        logger.warning("Метод %s.%s не отработал: %s", type(target).__name__, method, e)
        return _MISSING


def _tab_data(tab: Any) -> Dict[str, Any]:
    """Данные вкладки словарём (пустой словарь — если вкладка их не отдаёт)."""
    data = _tab_raw(tab)
    return dict(data) if isinstance(data, dict) else {}


def _tab_raw(tab: Any) -> Any:
    """
    Что вернула вкладка: словарь, список (машины) или ``_MISSING``.

    Разделение нужно, чтобы отличить «вкладка пуста» от «вкладка не умеет
    отдавать данные»: в первом случае поля проверяются по её ключам, во
    втором — заполнение не блокируется (проверить нечем).
    """
    return _call(tab, "get_data")


def _window_tab_keys(window: Any) -> Tuple[str, ...]:
    """
    Ключи вкладок окна.

    Сначала спрашиваем у окна (``_tab_keys()`` или ``_TAB_KEYS``), и только
    потом берём раскладку MainWindow: главное окно разбито на отдельные
    вкладки (перевозчик, водитель, тягач/прицеп, ТС, договор, заказчик), и
    это единственный набор, который описывает его форму целиком.
    """
    keys = _call(window, "_tab_keys")
    if isinstance(keys, (list, tuple)) and keys:
        return tuple(str(key) for key in keys)

    keys = getattr(window, "_TAB_KEYS", None)
    if isinstance(keys, (list, tuple)) and keys:
        return tuple(str(key) for key in keys)

    # MainWindow: собственного _TAB_KEYS нет, вкладки лежат атрибутами.
    if all(
        hasattr(window, name)
        for name in ("customer_tab", "driver_tab", "contract_tab")
    ):
        return (
            "carrier_tab", "driver_tab", "trailer_tab",
            "vehicles_tab", "contract_tab", "customer_tab",
        )

    return ()


def _tab_for_key(window: Any, key: str) -> Any:
    """Вкладка окна по ключу раскладки (None — вкладки нет)."""
    by_key = getattr(window, "_tab_by_key", None)
    if callable(by_key):
        try:
            tab = by_key(key)
        except Exception as e:  # noqa: BLE001
            logger.warning("Окно не отдало вкладку %r: %s", key, e)
            tab = None
        if tab is not None:
            return tab

    tab = getattr(window, key, None)
    if tab is not None:
        return tab

    # Запасной путь: страницы QTabWidget по порядку раскладки.
    tabs = getattr(window, "tabs", None)
    keys = _window_tab_keys(window)
    if tabs is not None and key in keys:
        try:
            return tabs.widget(keys.index(key))
        except Exception:  # noqa: BLE001 — QTabWidget может быть не готов
            return None
    return None


def active_window() -> Optional[Any]:
    """
    Окно, форму которого видит оператор.

    Порядок поиска:

      1. окно, зарегистрированное контекстом агента (панель передаёт его
         при каждом запросе);
      2. активное окно приложения;
      3. единственное видимое окно верхнего уровня.

    Возвращается любой объект: агент работает и без Qt (в тестах окно —
    обычный объект с методами вкладок). Если Qt не поднят вовсе, функция
    возвращает None, и инструменты честно сообщают «форма не открыта».
    """
    if _CONTEXT_WINDOW is not None:
        return _CONTEXT_WINDOW

    try:
        from PyQt5.QtWidgets import QApplication
    except ImportError:  # Qt не установлен — работаем без формы
        return None

    app = QApplication.instance()
    if app is None:
        return None

    window = app.activeWindow()
    if window is not None:
        return window

    visible = [widget for widget in app.topLevelWidgets() if widget.isVisible()]
    return visible[0] if len(visible) == 1 else None


def set_context_window(window: Optional[Any]) -> Optional[Any]:
    """
    Запоминает окно для инструментов формы; возвращает прежнее значение.

    Нужно тестам (подставить окно без Qt) и панели агента: оператор может
    держать фокус в другом окне, а форма — вот она.
    """
    global _CONTEXT_WINDOW
    previous = _CONTEXT_WINDOW
    _CONTEXT_WINDOW = window
    return previous


def collect_form_data(window: Any) -> Dict[str, Dict[str, Any]]:
    """
    Текущие значения полей формы: раздел → {поле: значение}.

    Разделы собираются из вкладок окна. Раскладка берётся у самого окна
    (``_tab_keys``/``_TAB_KEYS`` и ``_tab_by_key``), поэтому функция
    работает и с «Экспедиторством» (MainWindow), и с окнами типов: у них
    разный состав вкладок, но одинаковый способ доступа к ним.

    Только чтение: ни один вызов не меняет форму (задание, часть B.2).
    """
    if window is None:
        return {}

    sections: Dict[str, Dict[str, Any]] = {}
    for key in _window_tab_keys(window):
        tab = _tab_for_key(window, key)
        if tab is None:
            continue
        data = _tab_data(tab)
        if not data:
            continue

        if key == "contract_tab":
            # Вкладка «Договор» держит маршрут и стоимость вложенными
            # блоками: поднимаем их наверх с префиксом, чтобы имя поля было
            # однозначным (`route.route`, `price.total`).
            flat: Dict[str, Any] = {}
            for name, value in data.items():
                if isinstance(value, dict):
                    for sub_name, sub_value in value.items():
                        flat[f"{name}.{sub_name}"] = sub_value
                else:
                    flat[name] = value
            sections[key] = flat
            continue

        sections[key] = dict(data)

    # Тягач и полуприцеп: у вкладки «Тягач/прицеп» два отдельных метода.
    trailer_tab = getattr(window, "trailer_tab", None)
    if trailer_tab is not None:
        tractor = _call(trailer_tab, "get_tractor_data")
        trailer = _call(trailer_tab, "get_trailer_data")
        if isinstance(tractor, dict) and tractor:
            sections["tractor"] = tractor
        if isinstance(trailer, dict) and trailer:
            sections["trailer"] = trailer

    return sections


def _filled_only(data: Dict[str, Any]) -> Dict[str, Any]:
    """Только заполненные поля: пустые в ответе модели — шум."""
    result: Dict[str, Any] = {}
    for key, value in (data or {}).items():
        if isinstance(value, (list, tuple, dict)):
            if value:
                result[key] = value
            continue
        if str(value if value is not None else "").strip():
            result[key] = _short(value)
    return result


def _json(data: Any) -> str:
    """Компактный JSON для ответа модели (без переносов и лишних пробелов)."""
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(data), ensure_ascii=False)


# ─────────────────────────────────────────────────────────────
# Контекст агента
# ─────────────────────────────────────────────────────────────

@dataclass
class AgentContext:
    """
    То, что агент знает об окружении (задание, часть I.2).

    :param window: окно, форму которого читаем и заполняем;
    :param contract_type: ключ типа договора открытого окна;
    :param confirm: функция-диалог подтверждения: принимает Tool и
        аргументы, возвращает True/False. None — опасные действия
        запрещены (агент отвечает «отменено»).
    :param on_activity: уведомление о шаге (имя инструмента) — панель
        показывает «думает…».
    """

    window: Optional[Any] = None
    contract_type: str = ""
    confirm: Optional[Callable[[Tool, Dict[str, Any]], bool]] = None
    on_activity: Optional[Callable[[str], None]] = None

    def current_window(self) -> Optional[Any]:
        """Окно для инструментов: из контекста или найденное в приложении."""
        return self.window if self.window is not None else active_window()

    def notify(self, message: str) -> None:
        """Сообщает панели о шаге агента (ошибка уведомления не важна)."""
        if self.on_activity is None:
            return
        try:
            self.on_activity(message)
        except Exception:  # noqa: BLE001 — интерфейс не должен ломать агента
            logger.debug("Уведомление панели агента не доставлено")


#: Окно, подставленное тестами или панелью (см. set_context_window).
_CONTEXT_WINDOW: Optional[Any] = None


# ─────────────────────────────────────────────────────────────
# B.1. Поиск в справочниках (только чтение, без подтверждения)
# ─────────────────────────────────────────────────────────────

def _search_driver(query: str = "", limit: int = MAX_SEARCH_RESULTS, **_) -> Dict[str, Any]:
    """
    Ищет водителей по ФИО, телефону или паспорту.

    Пустой запрос — не ошибка ввода, а вопрос «сколько их всего»: модель
    часто зовёт поиск с пустой строкой на «сколько водителей в базе».
    Поэтому в ответе есть количество записей, а не только текст ошибки.
    """
    term = str(query or "").strip()
    if not term:
        return _error(
            "пустой запрос: укажите ФИО, телефон или паспорт водителя",
            total=_count_drivers(),
            hint="для количества записей вызови count_records "
                 "с entity_type='driver'",
        )

    noise = looks_like_command_or_noise(term)
    if noise:
        return _error(noise, looks_like_command=True)

    found = _driver_rows(term, limit)
    return _ok(found, count=len(found), query=term)


def _driver_rows(term: str, limit: int) -> List[Dict[str, Any]]:
    """Краткие карточки водителей по запросу (общие для поиска и «найти всё»)."""
    from db.crud.drivers import search_drivers

    rows = search_drivers(term, limit=max(1, int(limit)), with_carrier_name=True)
    return [{
        "id": row.get("id"),
        "full_name": _short(row.get("full_name")),
        "phone": _short(row.get("phone")),
        "birth_date": _short(row.get("birth_date")),
        "carrier": _short(row.get("carrier_name")),
        "license": _short(
            f"{row.get('license_series') or ''} {row.get('license_number') or ''}".strip()
        ),
    } for row in rows]


def _count_drivers() -> int:
    """Сколько всего водителей в справочнике (для подсказки при пустом запросе)."""
    from db.crud.drivers import get_all_drivers

    try:
        return len(get_all_drivers())
    except Exception:  # noqa: BLE001 — подсказка не должна ронять инструмент
        return 0


def _search_carrier(query: str = "", limit: int = MAX_SEARCH_RESULTS, **_) -> Dict[str, Any]:
    """Ищет перевозчиков по названию, ИНН или ОГРН."""
    term = str(query or "").strip()
    if not term:
        return _error("пустой запрос: укажите название, ИНН или ОГРН перевозчика")

    noise = looks_like_command_or_noise(term)
    if noise:
        return _error(noise, looks_like_command=True)

    found = _carrier_rows(term, limit)
    return _ok(found, count=len(found), query=term)


def _carrier_rows(term: str, limit: int) -> List[Dict[str, Any]]:
    """Краткие карточки перевозчиков по запросу."""
    from db.crud.organizations import search_organizations

    rows = search_organizations(term, is_carrier=True, limit=max(1, int(limit)))
    return [{
        "id": row.get("id"),
        "name": _short(row.get("full_name") or row.get("short_name")),
        "inn": _short(row.get("inn")),
        "ogrn": _short(row.get("ogrn")),
        "phone": _short(row.get("phone")),
    } for row in rows]


def _customer_rows(term: str, limit: int) -> List[Dict[str, Any]]:
    """Краткие карточки заказчиков по запросу."""
    from db.crud.organizations import search_organizations

    rows = search_organizations(term, is_carrier=False, limit=max(1, int(limit)))
    return [{
        "id": row.get("id"),
        "name": _short(row.get("full_name") or row.get("short_name")),
        "inn": _short(row.get("inn")),
        "ogrn": _short(row.get("ogrn")),
        "director": _short(row.get("director_name")),
    } for row in rows]


def _search_customer(query: str = "", limit: int = MAX_SEARCH_RESULTS, **_) -> Dict[str, Any]:
    """Ищет заказчиков по названию, ИНН или ФИО руководителя."""
    term = str(query or "").strip()
    if not term:
        return _error("пустой запрос: укажите название, ИНН или руководителя заказчика")

    noise = looks_like_command_or_noise(term)
    if noise:
        return _error(noise, looks_like_command=True)

    found = _customer_rows(term, limit)
    return _ok(found, count=len(found), query=term)


#: Сколько записей на каждый вид отдаёт поиск «сразу везде».
ALL_SEARCH_PER_KIND = 5

#: Слова, которые НЕ являются ни именем, ни названием: команды, вопросы,
#: вежливость и ругательства. Список живёт в core/agent/words.py и тот же
#: текст подставляется в промпт — иначе модель и код разошлись бы.
#:
#: Запрет стоит в КОДЕ, а не только в промпте: модель — не единственный
#: вызывающий, и правило должно работать всегда.
_NON_NAME_QUERIES = NON_NAME_WORDS

#: Минимальная длина слова, которое считается названием или фамилией.
#: Короче — это предлог, союз или инициал, а не имя: «и», «в», «на», «от».
_NAME_MIN_LENGTH = 4

#: Сколько подряд идущих БУКВ должно быть в слове, чтобы считать его
#: названием. Отсекает «и», «ну», «ок» (они не названия) и пропускает
#: «12345»: это может быть ИНН или номер договора, и такой запрос
#: разбирает промпт («похоже на ИНН — что искать?»), а не запрет.
_NAME_MIN_LETTERS_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def _query_words(term: str) -> List[str]:
    """Слова запроса без пунктуации, в нижнем регистре."""
    words = [word.strip(".,!?;:()\"'«»—-").casefold() for word in str(term or "").split()]
    return [word for word in words if word]


def looks_like_command_or_noise(term: str) -> str:
    """
    Похож ли запрос на команду, вопрос или ругательство, а не на название.

    Правило простое: если в запросе есть ХОТЬ ОДНО слово, похожее на имя
    или название (достаточно длинное и не служебное) — искать можно.
    Запрет срабатывает, только когда ВСЕ слова служебные: «Заполни»,
    «Найди», «блядь, заебал», «Сможешь заполнить договор?».

    Так «Ромашка, покажи» остаётся запросом (в нём есть название), а
    «Отмени» — нет (кроме команды в нём ничего нет).

    :return: короткое пояснение для модели или пустая строка, если запрос
        похож на имя или название и его можно искать.
    """
    words = _query_words(term)
    if not words:
        return ""

    blocked = [word for word in words if word in _NON_NAME_QUERIES]
    if not blocked:
        return ""

    # Есть ли в запросе настоящее название? Тогда служебное слово — просто
    # обрамление («Ромашка, покажи»), и искать нужно.
    meaningful = [
        word for word in words
        if len(word) >= _NAME_MIN_LENGTH
        and word not in _NON_NAME_QUERIES
        and _NAME_MIN_LETTERS_RE.search(word)
    ]
    if meaningful:
        return ""

    return (
        f"«{' '.join(blocked)}» — это команда или вопрос, а не имя и не "
        f"название. Такое в базе не ищут: спроси оператора, кого или что "
        f"нужно найти."
    )


def _search_anything(query: str = "", limit: int = ALL_SEARCH_PER_KIND, **_) -> Dict[str, Any]:
    """
    Ищет запись СРАЗУ ВО ВСЕХ справочниках: водители, перевозчики,
    заказчики, транспорт, договоры.

    Зачем отдельный инструмент (Часть 0, E). Живой диалог показал главную
    трудность: по одному слову («Фасстранс», «королёв») модель не знает,
    КТО это, и вместо поиска начинает уточнять. Правильный вопрос тут не
    «что уточнить», а «где искать» — и ответ на него даёт программа, а не
    модель: этот инструмент сам обходит все справочники и возвращает то,
    что нашлось, с указанием вида записи.

    Модели остаётся выбрать запись из готового списка — это она делает
    хорошо. Поэтому «найди X» без уточнений нужно начинать отсюда.
    """
    term = str(query or "").strip()
    if not term:
        return _error("пустой запрос: укажите что искать (название, ФИО, номер)")

    # Служебное слово вместо названия (команда, вопрос, ругательство) —
    # это не поиск, а ошибка вызова. Возврат понятного текста учит модель
    # правильному поведению лучше, чем «не нашёл Заполни».
    noise = looks_like_command_or_noise(term)
    if noise:
        logger.info("Поиск отклонён: запрос похож на команду (длина=%s)", len(term))
        return _error(noise, looks_like_command=True)

    per_kind = max(1, int(limit))
    sections: Dict[str, List[Dict[str, Any]]] = {}

    # Порядок обхода — от самых частых записей к редким. Исключение
    # внутри каждого вида не роняет поиск: остальные всё равно нужны.
    lookups = (
        ("carrier", lambda: _carrier_rows(term, per_kind)),
        ("customer", lambda: _customer_rows(term, per_kind)),
        ("driver", lambda: _driver_rows(term, per_kind)),
        ("vehicle", lambda: _search_vehicle(query=term, limit=per_kind).get("data") or []),
        ("contract", lambda: _search_contract(query=term, limit=per_kind).get("data") or []),
    )
    for kind, lookup in lookups:
        try:
            rows = lookup()
        except Exception as e:  # noqa: BLE001 — один вид не должен ломать поиск
            logger.warning("Поиск «%s» по виду %s не удался: %s", term, kind, e)
            continue
        if rows:
            sections[kind] = rows

    if not sections:
        return _ok({}, count=0, query=term,
                   message="ничего не найдено ни в одном справочнике")

    total = sum(len(rows) for rows in sections.values())
    return _ok(
        sections,
        count=total,
        query=term,
        found_in=sorted(sections),
        message=f"найдено записей: {total} — по видам: "
                + ", ".join(f"{kind}={len(rows)}" for kind, rows in sorted(sections.items())),
    )


def _search_vehicle(query: str = "", limit: int = MAX_SEARCH_RESULTS, **_) -> Dict[str, Any]:
    """
    Ищет транспорт по VIN, госномеру или марке.

    Два источника: ``vehicles`` (машины договоров) и ``driver_vehicles``
    (тягач и прицеп водителя). Ищем в обоих — иначе «найди машину по
    номеру» не нашло бы тягач, который в договор ещё не попадал.
    """
    import sqlite3

    from db.connection import get_connection

    term = str(query or "").strip()
    if not term:
        return _error("пустой запрос: укажите VIN, госномер или марку ТС")

    noise = looks_like_command_or_noise(term)
    if noise:
        return _error(noise, looks_like_command=True)

    like = f"%{term}%"
    found: List[Dict[str, Any]] = []

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, carrier_id, contract_id, vin, brand_model, plate_number, "
            "year, color, vehicle_type FROM vehicles "
            "WHERE COALESCE(vin, '') LIKE ? OR COALESCE(plate_number, '') LIKE ? "
            "   OR COALESCE(brand_model, '') LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (like, like, like, max(1, int(limit))),
        )
        for row in cursor.fetchall():
            found.append({
                "source": "vehicles",
                "id": row[0],
                "brand_model": _short(row[4]),
                "plate_number": _short(row[5]),
                "vin": _short(row[3]),
                "vehicle_type": _short(row[8]),
                "carrier_id": row[1],
                "contract_id": row[2],
            })

        cursor.execute(
            "SELECT dv.driver_id, d.full_name, dv.tractor_brand, dv.tractor_plate, "
            "       dv.trailer_brand, dv.trailer_plate "
            "FROM driver_vehicles dv LEFT JOIN drivers d ON d.id = dv.driver_id "
            "WHERE COALESCE(dv.tractor_plate, '') LIKE ? "
            "   OR COALESCE(dv.trailer_plate, '') LIKE ? "
            "   OR COALESCE(dv.tractor_brand, '') LIKE ? "
            "   OR COALESCE(dv.trailer_brand, '') LIKE ? "
            "LIMIT ?",
            (like, like, like, like, max(1, int(limit))),
        )
        for row in cursor.fetchall():
            found.append({
                "source": "driver_vehicles",
                "driver_id": row[0],
                "driver": _short(row[1]),
                "tractor": _short(f"{row[2] or ''} {row[3] or ''}".strip()),
                "trailer": _short(f"{row[4] or ''} {row[5] or ''}".strip()),
            })
    except sqlite3.DatabaseError as e:
        logger.warning("Поиск ТС не удался: %s", e)
        return _error(f"поиск по транспорту не удался: {e}")
    finally:
        conn.close()

    return _ok(found[:max(1, int(limit))], count=len(found), query=term)


def _search_contract(query: str = "", limit: int = MAX_SEARCH_RESULTS, **_) -> Dict[str, Any]:
    """Ищет договоры по номеру, дате, маршруту и сторонам."""
    from db import agent_queries

    term = str(query or "").strip()

    # Пустой запрос больше НЕ отдаёт «последние договоры» (проблема #15).
    # Десять случайных строк на непонятный запрос выглядят как выдумка:
    # оператор не понимает, почему ему показали именно эти договоры.
    if not term:
        return _error(
            "не указано, какой договор искать: назовите номер, дату, "
            "маршрут или сторону договора",
            hint="например: «договор 23092026-74» или "
                 "«договоры с Ромашкой»",
        )

    noise = looks_like_command_or_noise(term)
    if noise:
        return _error(noise, looks_like_command=True)

    rows = agent_queries.search_contracts(term, limit=max(1, int(limit)))
    found = [{
        "id": row.get("id"),
        "number": _short(row.get("contract_number")),
        "date": _short(row.get("contract_date")),
        "start_date": _short(row.get("start_date")),
        "end_date": _short(row.get("end_date")),
        "route": _short(row.get("route")),
        "driver": _short(row.get("driver_name")),
        "carrier": _short(row.get("carrier_name")),
        "customer": _short(row.get("customer_name")),
    } for row in rows]
    return _ok(found, count=len(found), query=term)


def _list_expiring_contracts(days: int = 30, limit: int = MAX_SEARCH_RESULTS, **_) -> Dict[str, Any]:
    """Договоры, у которых срок истекает в ближайшие N дней."""
    from db import agent_queries

    try:
        horizon = int(days)
    except (TypeError, ValueError):
        return _error("параметр days должен быть числом дней (например, 30)")
    if horizon < 0:
        return _error("параметр days не может быть отрицательным")

    rows = agent_queries.get_expiring_contracts(days=horizon, limit=max(1, int(limit)))
    found = [{
        "id": row.get("id"),
        "number": _short(row.get("contract_number")),
        "end_date": _short(row.get("end_date")),
        "route": _short(row.get("route")),
        "carrier": _short(row.get("carrier_name")),
        "driver": _short(row.get("driver_name")),
    } for row in rows]
    return _ok(found, count=len(found), days=horizon)


# ─────────────────────────────────────────────────────────────
# B.2. Чтение формы (без подтверждения)
# ─────────────────────────────────────────────────────────────

def _current_window_or_error(context: AgentContext) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """Окно из контекста или понятная ошибка «форма не открыта»."""
    window = context.current_window()
    if window is None:
        return None, _error(
            "открытая форма не найдена: откройте окно нужного типа договора"
        )
    return window, None


def _get_current_form(context: AgentContext, section: str = "", **_) -> Dict[str, Any]:
    """Текущие значения полей открытой формы (только чтение)."""
    window, error = _current_window_or_error(context)
    if error:
        return error

    sections = collect_form_data(window)
    if not sections:
        return _error("форма открыта, но поля из неё прочитать не удалось")

    if section:
        key = str(section).strip()
        if key in sections:
            return _ok(_filled_only(sections[key]), section=key,
                       contract_type=context.contract_type)
        # Модель могла назвать раздел по-русски («водитель»).
        alias = _section_alias(key, sections)
        if alias:
            return _ok(_filled_only(sections[alias]), section=alias,
                       contract_type=context.contract_type)
        return _error(
            f"раздела «{section}» в открытой форме нет",
            available=sorted(sections),
        )

    return _ok(
        {name: _filled_only(data) for name, data in sections.items()},
        contract_type=context.contract_type,
    )


#: Русские названия разделов формы — модель называет их как в интерфейсе.
_SECTION_ALIASES: Dict[str, str] = {
    "водитель": "driver_tab", "driver": "driver_tab",
    "перевозчик": "carrier_tab", "carrier": "carrier_tab",
    "заказчик": "customer_tab", "клиент": "customer_tab",
    "customer": "customer_tab",
    "договор": "contract_tab", "contract": "contract_tab",
    "маршрут": "contract_tab", "стоимость": "contract_tab",
    "тягач": "tractor", "полуприцеп": "trailer", "прицеп": "trailer",
    "машины": "vehicles_tab", "тс": "vehicles_tab",
    "перевозимые тс": "vehicles_tab", "автомобили": "vehicles_tab",
    "vehicles": "vehicles_tab",
}

#: Разделы распознавания документов → вкладки формы (одно и то же место
#: называется по-разному: в импорте — «driver», в форме — «driver_tab»).
#: Тягач и полуприцеп ведут на вкладку «Тягач/прицеп» (`trailer_tab`), а
#: имя БЛОКА (`tractor` / `trailer`) сохраняет _resolve_section: у вкладки
#: два блока, и fill_fields различает их по имени.
_DOCUMENT_SECTION_TABS: Dict[str, str] = {
    "driver": "driver_tab",
    "carrier": "carrier_tab",
    "customer": "customer_tab",
    "contract": "contract_tab",
    "tractor": "trailer_tab",
    "trailer": "trailer_tab",
}

#: Блоки вкладки «Тягач/прицеп»: у неё нет отдельных ключей вкладок.
_TRAILER_BLOCKS = ("tractor", "trailer")


def _resolve_section(section: str, keys: Sequence[str]) -> Optional[str]:
    """
    Ключ вкладки (или имя блока) по имени раздела.

    Порядок: точное совпадение, русское название («водитель»), раздел
    импорта документов («driver» → driver_tab), блоки тягача и полуприцепа
    (у них своей вкладки нет — они живут внутри «Тягач/прицеп»), затем
    вариант с суффиксом вкладки («vehicle» → vehicles_tab).
    """
    name = str(section or "").strip()
    if name in keys:
        return name

    alias = _SECTION_ALIASES.get(name.casefold())
    if alias in keys:
        return alias

    mapped = _DOCUMENT_SECTION_TABS.get(name)
    if mapped in keys:
        # Блок тягача/полуприцепа отдаётся СВОИМ именем: вкладка у них
        # общая, и различает их вызывающий код (fill_fields).
        return name if name in _TRAILER_BLOCKS else mapped

    for candidate in (f"{name}_tab", f"{name}s_tab", f"{name}es_tab"):
        if candidate in keys:
            return candidate
    return None


def _section_alias(name: str, sections: Dict[str, Any]) -> Optional[str]:
    """Раздел по русскому названию или короткому ключу (None — не нашли)."""
    return _resolve_section(name, tuple(sections))


def _get_schema(contract_type: str = "", window: Any = None, **_) -> Dict[str, Any]:
    """
    Схема разделов формы: какие поля есть у типа договора.

    Источник — открытая форма (разделы вкладок и их поля) и реестр типов
    (человеческое название типа). Отдельного описания схемы полей в проекте
    нет: перечень полей вкладки — это и есть схема, а core/contract_data.py
    не меняется (только чтение).
    """
    from core.contracts.registry import ContractTypeRegistry

    # Реестр наполняется в main.py при старте. Если агента спросили раньше
    # (панель в тесте, отдельный вызов) — догружаем встроенные типы. Вызов
    # безопасен и когда реестр уже наполнен: импортированные модули
    # пропускаются (см. ContractTypeRegistry.load_builtin).
    ContractTypeRegistry.load_builtin()

    key = str(contract_type or "").strip()
    spec = ContractTypeRegistry.find(key) if key else None

    fields: Dict[str, List[str]] = {}
    if window is not None:
        for name, data in collect_form_data(window).items():
            fields[name] = sorted(data)

    if not fields and not spec:
        return _error(
            "не удалось определить схему: укажите тип договора "
            "(например, perevozka) или откройте окно типа"
        )

    return _ok(
        {
            "contract_type": spec.contract_type if spec else key,
            "title": spec.title if spec else "",
            "sections": fields,
            "known_types": list(ContractTypeRegistry.known_types()),
        }
    )


def _validate_current_form(context: AgentContext, **_) -> Dict[str, Any]:
    """
    Проверка открытой формы: ошибки и замечания (только чтение).

    Данные собираются в ContractData — тот же контракт, которым пользуется
    генерация договора, и проверяются общим Validator. ContractData и
    Validator не изменяются: это чтение.
    """
    window, error = _current_window_or_error(context)
    if error:
        return error

    from core.validator import Validator

    data = collect_form_data(window)
    if not data:
        return _error("форма открыта, но данные из неё собрать не удалось")

    try:
        from core.contract_data import ContractData

        contract_data = ContractData(
            driver=dict(data.get("driver_tab", {})),
            carrier=dict(data.get("carrier_tab", {})),
            customer=dict(data.get("customer_tab", {})),
            vehicles=list(data.get("vehicles_tab", []) or []),
            tractor=dict(data.get("tractor", {})),
            trailer=dict(data.get("trailer", {})),
            contract=dict(data.get("contract_tab", {})),
        )
        report = Validator.check(contract_data)
    except Exception as e:  # noqa: BLE001 — проверка не должна ронять агента
        logger.exception("Проверка формы не удалась")
        return _error(f"проверить форму не удалось: {type(e).__name__}: {e}")

    return _ok(
        {
            "errors": list(report.errors),
            "warnings": list(report.warnings),
            "is_clean": report.is_clean,
        },
        contract_type=context.contract_type,
    )


# ─────────────────────────────────────────────────────────────
# B.3. Заполнение формы (требует подтверждения)
# ─────────────────────────────────────────────────────────────

#: Поля, которые в форме числовые: строка «2024» из базы должна стать числом.
_INT_FIELDS = frozenset({"year", "payment_days", "prepayment_amount",
                         "prepayment_percent", "vat_rate_num"})

#: Порядок разделов, когда имя поля указано БЕЗ раздела и встречается в
#: нескольких вкладках сразу. Живой случай: `full_name` есть и у водителя,
#: и у перевозчика, и у заказчика — ФИО без раздела должно попасть
#: в водителя, а не в первую вкладку окна.
_FORM_SECTION_PRIORITY: Tuple[str, ...] = (
    "driver_tab", "tractor", "trailer", "vehicles_tab",
    "carrier_tab", "customer_tab", "contract_tab",
)

#: Колонки таблицы driver_vehicles → имена полей формы тягача и полуприцепа.
#: Разные имена заложены исторически: в базе «tractor_plate», в форме
#: «plate_number» (см. MainWindow._load_driver_from_db).
_DRIVER_VEHICLE_COLUMNS: Dict[str, Dict[str, str]] = {
    "tractor": {
        "brand_model": "tractor_brand", "plate_number": "tractor_plate",
        "color": "tractor_color", "year": "tractor_year",
    },
    "trailer": {
        "brand_model": "trailer_brand", "plate_number": "trailer_plate",
        "color": "trailer_color", "year": "trailer_year",
    },
}


def _coerce_field(field_name: str, value: Any) -> Any:
    """Приводит значение к типу поля формы (числа — числами)."""
    name = str(field_name).rsplit(".", 1)[-1]
    if name in _INT_FIELDS:
        text = str(value if value is not None else "").strip()
        if text.isdigit():
            return int(text)
    return value


def fill_fields(window: Any, fields: Dict[str, Any]) -> Dict[str, Any]:
    """
    Раскладывает значения по вкладкам формы: «раздел.поле» → значение.

    Возвращает отчёт: что удалось заполнить, что нет и почему. Функция
    НЕ спрашивает подтверждения — его получает агент ДО вызова (задание,
    часть G.2). Здесь только раскладка, поэтому функцию удобно проверять
    тестом на «окне-заглушке».

    Имя поля принимается в трёх видах: ``driver_tab.full_name``,
    ``full_name`` (ищем во всех разделах) и ``route.route``
    (поле вложенного блока вкладки «Договор»). Раздел можно назвать
    по-русски («водитель») или как в импорте документов («driver»).

    Поле проверяется по списку полей вкладки: если вкладка отдаёт данные и
    такого поля в них нет, значение не пишется, а в отчёт попадает причина.
    Без этой проверки форма молча принимала бы любую опечатку модели.
    """
    if window is None:
        return {"filled": {}, "failed": {"форма": "окно не открыто"}}
    if not isinstance(fields, dict) or not fields:
        return {"filled": {}, "failed": {"поля": "ничего не передано"}}

    keys = _window_tab_keys(window)
    tabs = {key: _tab_for_key(window, key) for key in keys}
    #: Какие поля есть у каждой вкладки (пустой набор — вкладка не отдаёт
    #: данные: проверять нечем, заполнение не блокируем).
    known = {key: _tab_data(tab) for key, tab in tabs.items() if tab is not None}

    filled: Dict[str, Any] = {}
    failed: Dict[str, str] = {}

    for raw_name, value in fields.items():
        name = str(raw_name or "").strip()
        if not name:
            continue

        section, _, field_name = name.partition(".")
        if not field_name:
            # Раздел не указан: ищем поле в разделах формы по порядку.
            # Имя поля бывает НЕ уникальным (`full_name` есть и у водителя,
            # и у перевозчика), поэтому порядок раздела задан явно — иначе
            # ФИО водителя уезжало бы в перевозчика.
            candidates = [
                (key, name) for key in _FORM_SECTION_PRIORITY
                if known.get(key) and name in known[key]
            ]
            if not candidates:
                failed[name] = "поля с таким именем в открытой форме нет"
                continue
            section, field_name = candidates[0]
        else:
            section = _resolve_section(section, keys) or section

        # У блока (тягач, полуприцеп) своего ключа вкладки нет: сам он
        # остаётся именем блока, а вкладку берём из таблицы разделов.
        tab_key = _DOCUMENT_SECTION_TABS.get(section, section)
        tab = tabs.get(tab_key)
        if tab is None:
            failed[name] = f"раздела «{section}» в форме нет"
            continue

        tab_fields = known.get(tab_key) or {}
        if tab_fields and field_name not in tab_fields:
            failed[name] = f"поля «{field_name}» в разделе «{section}» нет"
            continue

        # Тягач и полуприцеп у вкладки «Тягач/прицеп» — вложенные блоки,
        # и fill_data принимает их СРАЗУ оба, поэтому передаём пустой блок.
        if section in ("tractor", "trailer"):
            ok = _fill_trailer_tab(tab, section, field_name,
                                   _coerce_field(field_name, value))
        else:
            # Поля идут по одному, а не одним словарём. Причина: некоторые
            # вкладки (например, «Водитель») внутри fill_data пересобирают
            # часть полей из своего словаря и обнуляют то, чего в нём нет.
            # Пакетный вызов терял бы уже записанное значение, а пофайловый —
            # трогает ровно одно поле формы.
            ok = _call(tab, "fill_data",
                       {field_name: _coerce_field(field_name, value)}) is not _MISSING

        if ok:
            filled[name] = _short(value, 120)
        else:
            failed[name] = "форма не приняла значение (поле не найдено?)"

    return {"filled": filled, "failed": failed}


def _fill_trailer_tab(tab: Any, block: str, field_name: str, value: Any) -> bool:
    """Заполняет поле тягача или полуприцепа (у вкладки два блока сразу)."""
    tractor: Dict[str, Any] = {}
    trailer: Dict[str, Any] = {}
    if block == "tractor":
        tractor[field_name] = value
    else:
        trailer[field_name] = value
    return _call(tab, "fill_data", tractor, trailer) is not _MISSING


def _fill_field(context: AgentContext, field: str = "", value: Any = "") -> Dict[str, Any]:
    """
    Заполняет ОДНО поле в открытой форме (опасное действие).

    Вызывается агентом только после подтверждения оператором: диалог
    показывает панель (ui/agent_widget.py).
    """
    window, error = _current_window_or_error(context)
    if error:
        return error

    name = str(field or "").strip()
    if not name:
        return _error("не указано имя поля")

    report = fill_fields(window, {name: value})
    if report["filled"]:
        return _ok(
            {"field": name, "value": _short(value, 120)},
            message="поле заполнено",
        )
    return _error(
        f"поле «{name}» заполнить не удалось",
        details=report["failed"],
    )


def _fill_from_entity(context: AgentContext, entity_type: str = "",
                      entity_id: int = 0, **_) -> Dict[str, Any]:
    """
    Подтягивает данные записи справочника в форму.

    НЕ заполняет форму сама: возвращает НАБОР ПОЛЕЙ, который агент
    показывает оператору и после подтверждения раскладывает вызовами
    fill_field. Так подтверждение одно на всю операцию, а оператор видит
    список значений до записи (задание, часть G.2).

    :param entity_type: driver | carrier | customer | vehicle;
    :param entity_id: ID записи (его вернул поиск).
    """
    from db.crud.drivers import load_driver
    from db.crud.organizations import load_organization
    from db.crud.vehicles import load_driver_vehicle

    kind = str(entity_type or "").strip().casefold()
    try:
        record_id = int(entity_id)
    except (TypeError, ValueError):
        return _error("entity_id должен быть числом (ID записи из поиска)")

    #: Блоки тягача и полуприцепа — только у водителя (у остальных пусто).
    blocks: Dict[str, Dict[str, Any]] = {}

    if kind == "driver":
        record = load_driver(record_id)
        if not record:
            return _error(f"водитель с ID={record_id} не найден")
        fields = {
            key: record.get(key)
            for key in (
                "full_name", "birth_date", "birth_place", "passport_series",
                "passport_number", "passport_issue_date", "passport_issuer",
                "passport_code", "registration_address", "license_series",
                "license_number", "license_issue_date", "license_expiry_date",
                "license_categories", "phone",
            )
            if str(record.get(key) or "").strip()
        }
        target = "driver_tab"
        title = _short(record.get("full_name"))
        # Тягач и полуприцеп водителя отдаются ОТДЕЛЬНЫМИ блоками: это две
        # части одной вкладки формы, и смешивать их с полями водителя нельзя.
        # Имена колонок driver_vehicles (tractor_plate) не совпадают с
        # именами полей формы (plate_number) — переводим явно, как это
        # делает MainWindow._load_driver_from_db.
        vehicle = load_driver_vehicle(record_id)
        if vehicle:
            for block, columns in _DRIVER_VEHICLE_COLUMNS.items():
                values = {
                    field_name: vehicle.get(column)
                    for field_name, column in columns.items()
                    if str(vehicle.get(column) or "").strip()
                }
                if values:
                    blocks[block] = values

    elif kind in ("carrier", "customer"):
        record = load_organization(record_id, is_carrier=(kind == "carrier"))
        if not record:
            return _error(f"запись с ID={record_id} не найдена")
        fields = {
            key: record.get(key)
            for key in (
                "full_name", "short_name", "inn", "kpp", "ogrn",
                "legal_address", "actual_address", "bank_account", "bik",
                "correspondent_account", "bank_name", "director_name",
                "director_position", "phone", "email", "entity_type", "basis",
            )
            if str(record.get(key) or "").strip()
        }
        target = "carrier_tab" if kind == "carrier" else "customer_tab"
        title = _short(record.get("full_name") or record.get("short_name"))

    elif kind == "vehicle":
        import sqlite3

        from db.connection import get_connection

        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT vin, brand_model, plate_number, year, color, vehicle_type "
                "FROM vehicles WHERE id = ?",
                (record_id,),
            )
            row = cursor.fetchone()
        except sqlite3.DatabaseError as e:
            return _error(f"транспорт не прочитан: {e}")
        finally:
            conn.close()
        if not row:
            return _error(f"транспорт с ID={record_id} не найден")
        keys = ("vin", "brand_model", "plate_number", "year", "color", "vehicle_type")
        fields = {
            key: value for key, value in zip(keys, row)
            if str(value if value is not None else "").strip()
        }
        target = "vehicles_tab"
        title = _short(f"{row[1] or ''} {row[2] or ''}".strip())

    else:
        return _error(
            "неизвестный тип записи: ожидается driver, carrier, customer "
            "или vehicle",
            given=entity_type,
        )

    if not fields and not blocks:
        return _error(f"у записи ID={record_id} нет заполненных полей")

    # Пустые значения в форму не отдаём: подтверждать нечего.
    data: Dict[str, Any] = {
        "entity_type": kind, "entity_id": record_id, "title": title,
        "target": target, "fields": fields,
    }
    for block, values in (blocks or {}).items():
        data[block] = values
    count = len(fields) + sum(len(values) for values in (blocks or {}).values())
    return _ok(
        data,
        message=(
            f"найдено полей: {count}. Показать оператору и после "
            f"подтверждения заполнить"
        ),
    )


def _fill_entity_fields(context: AgentContext, fields: Any = None,
                        tractor: Any = None, trailer: Any = None,
                        **_) -> Dict[str, Any]:
    """
    Заполняет форму набором полей (опасное действие, требует подтверждения).

    Вызывается после fill_from_entity: модель передаёт сюда ровно то, что
    вернул предыдущий инструмент, — поля записи и (для водителя) отдельные
    блоки тягача и полуприцепа.
    """
    window, error = _current_window_or_error(context)
    if error:
        return error
    if not isinstance(fields, dict) or not fields:
        return _error("не переданы поля для заполнения")

    prepared: Dict[str, Any] = dict(fields)
    # Блоки тягача и полуприцепа раскладываются с префиксом раздела: так их
    # понимает fill_fields (это два блока одной вкладки формы).
    if isinstance(tractor, dict) and tractor:
        prepared.update({f"tractor.{key}": value for key, value in tractor.items()})
    if isinstance(trailer, dict) and trailer:
        prepared.update({f"trailer.{key}": value for key, value in trailer.items()})

    report = fill_fields(window, prepared)
    if not report["filled"]:
        return _error("ни одно поле заполнить не удалось", details=report["failed"])
    return _ok(
        {"filled": report["filled"], "failed": report["failed"]},
        message=f"заполнено полей: {len(report['filled'])}",
    )


# ─────────────────────────────────────────────────────────────
# B.4. Документы
# ─────────────────────────────────────────────────────────────

def _recognize_document(file_path: str = "", **_) -> Dict[str, Any]:
    """
    Распознаёт документ существующим сервисом импорта (только чтение).

    Путь берётся у оператора (файл на диске), текст читается локально, а
    в GigaChat уходят только замаскированные данные — это делает
    core/document_import_service.py, здесь ничего не меняется.

    Возвращаются НАЙДЕННЫЕ поля: раздел → {поле: значение}. Значения — те,
    что распознаны; заполнение формы делает отдельный инструмент с
    подтверждением.
    """
    import os
    from threading import Event

    path = str(file_path or "").strip().strip('"')
    if not path:
        return _error("не указан путь к файлу")
    if not os.path.isfile(path):
        return _error(f"файл не найден: {path}")

    from core.document_import_service import DocumentImportService
    from core.settings_service import get_settings_service

    try:
        settings = get_settings_service().as_dict()
    except Exception:  # noqa: BLE001 — настроек нет, импорт работает по умолчанию
        settings = {}

    service = DocumentImportService(settings=settings)
    results: List[Dict[str, Any]] = []
    errors: List[str] = []

    def on_result(name: str, rows: Any, error: str = "") -> None:
        """Собирает распознанные поля страницы (вызывает сервис импорта)."""
        if error:
            errors.append(f"{name}: {error}")
            return
        for row in rows or ():
            value = str(getattr(row, "value", "") or "").strip()
            if value:
                results.append({
                    "section": getattr(row, "section", ""),
                    "field": getattr(row, "key", ""),
                    "value": _short(value),
                })

    try:
        service.process([path], Event(), on_result)
    except Exception as e:  # noqa: BLE001 — распознавание не должно ронять агента
        logger.exception("Распознавание документа не удалось")
        return _error(f"распознать документ не удалось: {type(e).__name__}: {e}")

    fields: Dict[str, Dict[str, str]] = {}
    for item in results:
        fields.setdefault(item["section"], {})[item["field"]] = item["value"]

    if not fields:
        return _error(
            "в документе не найдено ни одного поля"
            + (f" ({'; '.join(errors)})" if errors else ""),
            file=os.path.basename(path),
        )
    return _ok(
        {"file": os.path.basename(path), "sections": fields,
         "fields_count": len(results)},
        errors=errors,
        message="поля найдены; переносить их в форму — отдельным подтверждением",
    )


def _apply_recognized_fields(context: AgentContext, fields: Any = None,
                             sections: Any = None, **_) -> Dict[str, Any]:
    """
    Применяет распознанные поля в форму (опасное действие).

    Принимает ЛИБО плоский набор ``{"driver_tab.full_name": "…"}``, ЛИБО
    структуру из ``recognize_document`` (``sections``: раздел → поля).
    """
    window, error = _current_window_or_error(context)
    if error:
        return error

    flat: Dict[str, Any] = {}
    keys = _window_tab_keys(window)

    if isinstance(sections, dict):
        for section, values in sections.items():
            if not isinstance(values, dict):
                continue
            # Раздел импорта («driver») приводим к вкладке формы
            # («driver_tab»): иначе перенос молча не находил бы место.
            key = _resolve_section(section, keys) or section
            for name, value in values.items():
                flat[f"{key}.{name}" if "." not in str(name) else str(name)] = value
    if isinstance(fields, dict):
        for name, value in fields.items():
            flat[str(name)] = value

    if not flat:
        return _error("не переданы поля для переноса в форму")

    report = fill_fields(window, flat)
    if not report["filled"]:
        return _error("ни одно распознанное поле не перенесено",
                      details=report["failed"])
    return _ok(
        {"filled": report["filled"], "failed": report["failed"]},
        message=f"перенесено полей: {len(report['filled'])}",
    )


# ─────────────────────────────────────────────────────────────
# B.5. Справочные инструменты
# ─────────────────────────────────────────────────────────────

#: Что означает поле и как его заполнять. Ключи — имена полей формы;
#: значения — короткое пояснение для оператора. Основа — подписи и правила
#: из core/validator.py и core/document_import_service.py, дополненные
#: практикой заполнения.
FIELD_HELP: Dict[str, str] = {
    "full_name": "Полное наименование или ФИО. Для организации — как в ЕГРЮЛ, для ИП — «Индивидуальный предприниматель Фамилия Имя Отчество».",
    "short_name": "Короткое название для документов (например, ООО «Ромашка»).",
    "inn": "ИНН: 10 цифр у организации, 12 у ИП. Только цифры, без слова «ИНН».",
    "kpp": "КПП: 9 цифр. У индивидуального предпринимателя КПП нет — поле пустое.",
    "ogrn": "ОГРН: 13 цифр у организации, ОГРНИП — 15 цифр у ИП.",
    "legal_address": "Юридический адрес по ЕГРЮЛ — тот, что стоит в выписке.",
    "actual_address": "Адрес, по которому сторона фактически находится. Может совпадать с юридическим.",
    "bank_account": "Расчётный счёт: ровно 20 цифр, без «р/с» и пробелов.",
    "bik": "БИК банка: 9 цифр.",
    "correspondent_account": "Корреспондентский счёт: 20 цифр, без «к/с».",
    "bank_name": "Название банка как напечатано, вместе с организационной формой (например, «ПАО Сбербанк»), без слова «Банк» в начале.",
    "director_name": "ФИО руководителя, который подписывает договор. Без слова «Директор» или «Руководитель».",
    "director_position": "Должность подписанта: «Генеральный директор», «Индивидуальный предприниматель».",
    "entity_type": "Вид лица: ООО, ИП с НДС, ИП без НДС. От него зависят формулировки в договоре.",
    "basis": "Основание полномочий подписанта: «Устава» или «свидетельства о государственной регистрации».",
    "phone": "Телефон в любом удобном виде, программа сама приведёт к формату.",
    "email": "Электронная почта для переписки по договору.",
    "birth_date": "Дата рождения водителя (ДД.ММ.ГГГГ).",
    "birth_place": "Место рождения — как в паспорте, вместе с «г.» и областью.",
    "passport_series": "Серия паспорта: 4 цифры (например, 1822).",
    "passport_number": "Номер паспорта: 6 цифр.",
    "passport_issue_date": "Дата выдачи паспорта.",
    "passport_issuer": "Кем выдан паспорт — как в штампе, вместе с отделом.",
    "passport_code": "Код подразделения: три цифры, дефис, три цифры (123-456).",
    "registration_address": "Адрес регистрации по паспорту (штамп «зарегистрирован по адресу»).",
    "license_series": "Серия водительского удостоверения: 4 цифры.",
    "license_number": "Номер водительского удостоверения: 6 цифр.",
    "license_issue_date": "Дата выдачи водительского удостоверения.",
    "license_expiry_date": "Срок действия ВУ. Просроченное ВУ — повод проверить данные.",
    "license_categories": "Категории ВУ: «B, C, E» — нужны для допуска к ТС.",
    "vin": "VIN: 17 символов (латинские буквы и цифры, без I, O, Q).",
    "brand_model": "Марка и модель: «JETOUR T2», «Foton Auman».",
    "plate_number": "Государственный регистрационный номер.",
    "year": "Год выпуска. Если не указан — 0: программа подставит текущий.",
    "color": "Цвет кузова по документам.",
    "vehicle_type": "Тип ТС: Тягач, Прицеп, Полуприцеп, Легковой автомобиль, Фургон, Автобус.",
    "number": "Номер договора — обычно вида 23092026-74 (дата и код).",
    "date": "Дата договора.",
    "start_date": "Дата начала перевозки (рейса).",
    "end_date": "Дата окончания договора: по ней видно, какие договоры истекают.",
    "route": "Направление перевозки: «Мурманск — Пятигорск».",
    "price_without_vat": "Стоимость без НДС, числом, без пробелов и знака рубля.",
    "price_with_vat": "Стоимость с НДС. Программа считает её из суммы без НДС и ставки.",
    "vat_rate": "Ставка НДС. По умолчанию 22 %; «без НДС» — отдельный вид лица.",
    "payment_days": "Срок оплаты в днях от даты выгрузки.",
    "special_conditions": "Особые условия: то, что не попало в другие поля.",
    "loading_address": "Адрес погрузки.",
    "unloading_address": "Адрес выгрузки.",
}


def _explain_field(field: str = "", contract_type: str = "", **_) -> Dict[str, Any]:
    """
    Объясняет, что за поле и как его заполнять.

    Ищет поле по имени (с разделом или без), затем по-русски — по подписи
    из импорта документов. Ничего не выдумывает: если поля нет в справке,
    честно говорит об этом и предлагает список известных.
    """
    name = str(field or "").strip()
    if not name:
        return _error("не указано имя поля")

    bare = name.rsplit(".", 1)[-1].strip()
    key = bare.casefold()

    text = FIELD_HELP.get(bare) or FIELD_HELP.get(key)

    if not text:
        # Поле могли назвать по-русски: «расчётный счёт», «ВУ».
        from core.document_import_service import LABELS

        for code, title in LABELS.items():
            if title.casefold() == key or title.casefold() in key or key in title.casefold():
                text = FIELD_HELP.get(code)
                if text:
                    name = code
                    break

    if not text:
        return _error(
            f"про поле «{field}» в справке ничего нет",
            known=sorted(FIELD_HELP)[:40],
        )

    return _ok(
        {"field": name, "explanation": text},
        contract_type=contract_type,
    )


def _get_help(topic: str = "", **_) -> Dict[str, Any]:
    """
    Общая справка по программе: типы договоров, порядок работы, ограничения.

    Тема необязательна: без неё возвращается краткий обзор всех тем, с
    темой — её текст.

    ВАЖНО про формулировки (проблема #5). Этот текст уходит в модель и
    часто попадает в ответ оператору почти дословно. Поэтому здесь НЕТ
    имён служебных команд и технического жаргона — только то, что оператор
    видит в интерфейсе: вкладки, кнопки, названия документов.
    """
    topics: Dict[str, str] = {
        "типы": (
            "Программа готовит пять типов документов: Экспедиторство "
            "(договор-заявка на перевозку), клиентский тип D ТС, клиентский тип B, "
            "клиентский тип A и клиентский тип C (заявка в Excel). Тип выбирается в списке "
            "в шапке окна."
        ),
        "формы": (
            "Данные вводятся по вкладкам: стороны договора, водитель, "
            "тягач и полуприцеп, перевозимые ТС, условия договора и маршрут. "
            "Кнопка «Создать договор» проверяет данные и формирует документ."
        ),
        "распознавание": (
            "Кнопка «Распознать данные» (и «Импорт документов») умеет читать "
            "PDF, DOCX, сканы и фото: текст извлекается, при включённом "
            "облаке — через GigaChat. Все персональные данные перед отправкой "
            "в облако заменяются плейсхолдерами."
        ),
        "база": (
            "Водители, перевозчики, заказчики, ТС и договоры хранятся "
            "в локальной базе. Удаление записей — мягкое: запись "
            "помечается удалённой и её можно вернуть."
        ),
        "максимилиан": (
            "Я — Максимилиан, помощник внутри программы. Объясняю поля "
            "форм, ищу данные в справочниках, подтягиваю записи в форму, "
            "проверяю договор на нехватку данных, сверяю организации с "
            "государственным реестром и умею распознавать документы. "
            "Договоры я не создаю и данные не удаляю: заполнение формы "
            "всегда требует вашего подтверждения."
        ),
        "настройки": (
            "Настройки открываются кнопкой «Настройки»: провайдер "
            "распознавания, тема оформления, а также раздел «Максимилиан» — "
            "включение помощника, образ и горячая клавиша."
        ),
    }

    key = str(topic or "").strip().casefold()
    if not key:
        return _ok({"topics": sorted(topics), "text": topics["максимилиан"]})
    if key in topics:
        return _ok({"topic": key, "text": topics[key]})

    for name, text in topics.items():
        if key in name or name in key:
            return _ok({"topic": name, "text": text})

    return _ok(
        {"topics": sorted(topics), "text": topics["максимилиан"]},
        message=f"отдельной справки по теме «{topic}» нет — вот общий обзор",
    )


# ─────────────────────────────────────────────────────────────
# Реестр инструментов
# ─────────────────────────────────────────────────────────────

def _json_schema(properties: Dict[str, Any], required: Sequence[str] = ()) -> Dict[str, Any]:
    """JSON Schema объекта с аргументами инструмента."""
    schema: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


_QUERY_PROPERTY = {
    "type": "string",
    "description": "строка поиска (ФИО, номер, название, ИНН)",
}


def build_registry(context: Optional[AgentContext] = None) -> ToolRegistry:
    """
    Собирает реестр инструментов Максимилиана.

    Состав: 16 инструментов версии 1 (задание, часть B), 5 инструментов
    контекста (часть 1), 4 инструмента DaData (часть 2) и 5 инструментов
    аналитики (часть 3) — всего 30.

    :param context: AgentContext с окном, типом договора и диалогом
        подтверждения. Без него доступны поиск, справка и распознавание
        документов; инструменты формы честно ответят «форма не открыта».
    """
    ctx = context if context is not None else AgentContext()

    registry = ToolRegistry()

    # ── B.1. Поиск ──
    registry.register(Tool(
        name="search_anything",
        description=(
            "Найти что угодно по одному слову или фразе: ищет СРАЗУ во всех "
            "справочниках (перевозчики, заказчики, водители, транспорт, "
            "договоры) и возвращает то, что нашлось, с указанием вида записи. "
            "ЭТО ГЛАВНЫЙ ИНСТРУМЕНТ ПОИСКА: если непонятно, кто это — "
            "человек или организация, — начинай с него. "
            "Понимает опечатки, падежи, дефисы и отсутствие «ООО»/«ИП»: "
            "«Фасстранс» находит «ООО «ФассТранс»»."
        ),
        parameters=_json_schema(
            {"query": _QUERY_PROPERTY}, required=["query"]
        ),
        function=_search_anything,
        summary="Поиск по всем справочникам сразу",
    ))
    registry.register(Tool(
        name="search_driver",
        description=(
            "Найти водителя в справочнике по ФИО, телефону или паспорту. "
            "Возвращает краткие данные водителя; найденную запись потом "
            "можно подтянуть в форму."
        ),
        parameters=_json_schema(
            {"query": _QUERY_PROPERTY}, required=["query"]
        ),
        function=_search_driver,
        summary="Поиск водителя в справочнике",
    ))
    registry.register(Tool(
        name="search_carrier",
        description=(
            "Найти перевозчика по названию, ИНН или ОГРН. "
            "Возвращает краткие реквизиты организации."
        ),
        parameters=_json_schema(
            {"query": _QUERY_PROPERTY}, required=["query"]
        ),
        function=_search_carrier,
        summary="Поиск перевозчика в справочнике",
    ))
    registry.register(Tool(
        name="search_customer",
        description=(
            "Найти заказчика по названию, ИНН или ФИО руководителя."
        ),
        parameters=_json_schema(
            {"query": _QUERY_PROPERTY}, required=["query"]
        ),
        function=_search_customer,
        summary="Поиск заказчика в справочнике",
    ))
    registry.register(Tool(
        name="search_vehicle",
        description=(
            "Найти транспорт по VIN, госномеру или марке. Ищет и в машинах "
            "договоров, и в тягачах/прицепах водителей."
        ),
        parameters=_json_schema(
            {"query": _QUERY_PROPERTY}, required=["query"]
        ),
        function=_search_vehicle,
        summary="Поиск транспорта",
    ))
    registry.register(Tool(
        name="search_contract",
        description=(
            "Найти договор по номеру, дате, маршруту, водителю или стороне. "
            "Запрос обязателен: без него договоры не показываются."
        ),
        parameters=_json_schema({"query": {
            "type": "string",
            "description": "номер, дата, маршрут или название стороны; можно пусто",
        }}),
        function=_search_contract,
        summary="Поиск договора",
    ))
    registry.register(Tool(
        name="list_expiring_contracts",
        description=(
            "Показать договоры, срок которых истекает в ближайшие N дней. "
            "Уже истёкшие договоры не возвращаются."
        ),
        parameters=_json_schema({"days": {
            "type": "integer",
            "description": "горизонт в днях, например 30",
            "minimum": 0,
        }}, required=["days"]),
        function=_list_expiring_contracts,
        summary="Договоры с истекающим сроком",
    ))

    # ── B.2. Чтение формы ──
    def get_current_form(section: str = "", **_):
        return _get_current_form(ctx, section=section)

    def get_schema(contract_type: str = "", **_):
        return _get_schema(contract_type=contract_type, window=ctx.current_window())

    def validate_current_form(**_):
        return _validate_current_form(ctx)

    registry.register(Tool(
        name="get_current_form",
        description=(
            "Показать текущие значения полей открытой формы. Можно указать "
            "раздел: водитель, перевозчик, заказчик, договор, машины."
        ),
        parameters=_json_schema({"section": {
            "type": "string",
            "description": "раздел формы; пусто — все разделы",
        }}),
        function=get_current_form,
        summary="Чтение открытой формы",
    ))
    registry.register(Tool(
        name="get_schema",
        description=(
            "Показать схему полей: какие разделы и поля есть у типа договора "
            "и в открытой форме."
        ),
        parameters=_json_schema({"contract_type": {
            "type": "string",
            "description": "ключ типа: perevozka, arenda_ts, logistiks_rus, "
                           "formika, zayavka_excel",
        }}),
        function=get_schema,
        summary="Схема полей типа договора",
    ))
    registry.register(Tool(
        name="validate_current_form",
        description=(
            "Проверить открытую форму: ошибки, из-за которых договор не "
            "создастся, и замечания."
        ),
        parameters=_json_schema({}),
        function=validate_current_form,
        summary="Проверка открытой формы",
    ))

    # ── B.3. Заполнение формы (подтверждение обязательно) ──
    def fill_field(field: str = "", value: Any = ""):
        return _fill_field(ctx, field=field, value=value)

    def fill_from_entity(entity_type: str = "", entity_id: int = 0):
        return _fill_from_entity(ctx, entity_type=entity_type, entity_id=entity_id)

    def fill_entity_fields(fields: Any = None, tractor: Any = None,
                           trailer: Any = None):
        return _fill_entity_fields(ctx, fields=fields, tractor=tractor,
                                   trailer=trailer)

    registry.register(Tool(
        name="fill_field",
        description=(
            "Заполнить ОДНО поле открытой формы. Имя поля — как в форме "
            "(можно с разделом: driver_tab.full_name). Требует подтверждения "
            "оператора."
        ),
        parameters=_json_schema({
            "field": {"type": "string", "description": "имя поля формы"},
            "value": {"type": "string", "description": "значение поля"},
        }, required=["field", "value"]),
        function=fill_field,
        requires_confirmation=True,
        summary="Заполнить поле формы",
        hide_value_args=("value",),
    ))
    registry.register(Tool(
        name="fill_from_entity",
        description=(
            "Подтянуть запись из базы (водитель, перевозчик, заказчик, ТС) и "
            "получить набор данных для формы. Форму НЕ меняет: сначала "
            "показывает набор оператору. Затем набор записывается в форму "
            "отдельным шагом."
        ),
        parameters=_json_schema({
            "entity_type": {
                "type": "string",
                "enum": ["driver", "carrier", "customer", "vehicle"],
                "description": "что подтягиваем",
            },
            "entity_id": {
                "type": "integer",
                "description": "номер записи из результата поиска",
            },
        }, required=["entity_type", "entity_id"]),
        function=fill_from_entity,
        summary="Подтянуть запись из базы в форму",
    ))
    registry.register(Tool(
        name="fill_entity_fields",
        description=(
            "Заполнить форму данными, которые вернул fill_from_entity. "
            "Блоки tractor и trailer (тягач и полуприцеп водителя) "
            "передаются отдельными аргументами. Требует подтверждения "
            "оператора."
        ),
        parameters=_json_schema({
            "fields": {
                "type": "object",
                "description": "объект «поле: значение» (можно с разделом)",
            },
            "tractor": {
                "type": "object",
                "description": "поля тягача из fill_from_entity (если есть)",
            },
            "trailer": {
                "type": "object",
                "description": "поля полуприцепа из fill_from_entity (если есть)",
            },
        }, required=["fields"]),
        function=fill_entity_fields,
        requires_confirmation=True,
        summary="Заполнить поля формы данными из базы",
        hide_value_args=("fields", "tractor", "trailer"),
    ))

    # ── B.4. Документы ──
    def recognize_document(file_path: str = ""):
        return _recognize_document(file_path=file_path)

    def apply_recognized_fields(fields: Any = None, sections: Any = None):
        return _apply_recognized_fields(ctx, fields=fields, sections=sections)

    registry.register(Tool(
        name="recognize_document",
        description=(
            "Распознать документ по пути к файлу (PDF, DOCX, скан, фото) и "
            "показать найденные поля. Форму не меняет."
        ),
        parameters=_json_schema({"file_path": {
            "type": "string",
            "description": "полный путь к файлу на диске",
        }}, required=["file_path"]),
        function=recognize_document,
        summary="Распознать документ",
    ))
    registry.register(Tool(
        name="apply_recognized_fields",
        description=(
            "Перенести распознанные поля в открытую форму. Требует "
            "подтверждения оператора."
        ),
        parameters=_json_schema({
            "fields": {
                "type": "object",
                "description": "объект «поле: значение» (можно с разделом)",
            },
            "sections": {
                "type": "object",
                "description": "раздел → {поле: значение} (ответ recognize_document)",
            },
        }),
        function=apply_recognized_fields,
        requires_confirmation=True,
        summary="Перенести распознанные поля в форму",
        hide_value_args=("fields", "sections"),
    ))

    # ── B.5. Справочные ──
    def explain_field(field: str = "", contract_type: str = ""):
        return _explain_field(field=field, contract_type=contract_type)

    def get_help(topic: str = ""):
        return _get_help(topic=topic)

    registry.register(Tool(
        name="explain_field",
        description=(
            "Объяснить, что значит поле формы и как его заполнять. "
            "Работает и без открытой формы."
        ),
        parameters=_json_schema({"field": {
            "type": "string",
            "description": "имя поля (inn, passport_series, route…) или его "
                           "русское название",
        }}, required=["field"]),
        function=explain_field,
        summary="Объяснение поля",
    ))
    registry.register(Tool(
        name="get_help",
        description=(
            "Общая справка по программе: типы договоров, распознавание, "
            "база, настройки, сам Максимилиан."
        ),
        parameters=_json_schema({"topic": {
            "type": "string",
            "description": "тема справки; пусто — общий обзор",
        }}),
        function=get_help,
        summary="Справка по программе",
    ))

    # ── 1. Контекст: что открыто и что делал оператор (Часть 1) ──
    # Все пять — только чтение. Реализация в core/agent/context_tools.py:
    # они смотрят в интерфейс, а не в базу, и это другая ответственность.
    #
    # Функции объявлены обычными def, а не lambda: в проекте lambda
    # запрещены в связях сигналов (цикл ссылок Python ↔ Qt), и хотя здесь
    # связи нет, единое правило читать проще, чем исключение из него.
    from core.agent.context_tools import (  # локальный импорт: без цикла модулей
        get_current_window as context_window,
        get_form_state as context_form_state,
        get_open_tabs as context_open_tabs,
        get_recent_actions as context_recent_actions,
        get_selected_row as context_selected_row,
    )

    def get_open_tabs(**_):
        """Список открытых окон и вкладок."""
        return context_open_tabs(ctx)

    def get_current_window(**_):
        """Текущее окно и активная вкладка."""
        return context_window(ctx)

    def get_form_state(section: str = "", only_empty: bool = False, **_):
        """Поля открытой формы: значения и пустые поля."""
        return context_form_state(ctx, section=section, only_empty=only_empty)

    def get_selected_row(**_):
        """Выбранная строка таблицы открытой формы."""
        return context_selected_row(ctx)

    def get_recent_actions(minutes: int = 5, **_):
        """Последние действия оператора (нажатия кнопок)."""
        return context_recent_actions(ctx, minutes=minutes)

    registry.register(Tool(
        name="get_open_tabs",
        description=(
            "Показать, какие окна и вкладки открыты сейчас и какая вкладка "
            "активна. Нужно, когда оператор говорит «тут», «в этой вкладке»."
        ),
        parameters=_json_schema({}),
        function=get_open_tabs,
        summary="Список открытых вкладок",
    ))
    registry.register(Tool(
        name="get_current_window",
        description=(
            "Какое окно открыто: тип договора и активная вкладка. Самый "
            "дешёвый способ понять, о какой форме спрашивает оператор."
        ),
        parameters=_json_schema({}),
        function=get_current_window,
        summary="Текущее окно",
    ))
    registry.register(Tool(
        name="get_form_state",
        description=(
            "Состояние полей открытой формы: значения И пустые поля. "
            "Отличие от get_current_form — видно, чего НЕ заполнено. "
            "Раздел: перевозчик, водитель, заказчик, договор, машины."
        ),
        parameters=_json_schema({
            "section": {
                "type": "string",
                "description": "раздел формы; пусто — все разделы",
            },
            "only_empty": {
                "type": "boolean",
                "description": "True — вернуть только незаполненные поля",
            },
        }),
        function=get_form_state,
        summary="Поля и пустые поля открытой формы",
    ))
    registry.register(Tool(
        name="get_selected_row",
        description=(
            "Что выбрано в таблице открытой формы (перевозимые ТС, адреса). "
            "Только чтение: выделение не меняется."
        ),
        parameters=_json_schema({}),
        function=get_selected_row,
        summary="Выбранная строка таблицы",
    ))
    registry.register(Tool(
        name="get_recent_actions",
        description=(
            "Что оператор делал последние минуты: нажатия кнопок с указанием "
            "окна. Значения из полей формы в журнал не попадают."
        ),
        parameters=_json_schema({"minutes": {
            "type": "integer",
            "description": "за сколько минут смотреть, по умолчанию 5",
            "minimum": 1,
            "maximum": 60,
        }}),
        function=get_recent_actions,
        summary="Последние действия оператора",
    ))

    # ── 2. DaData: реквизиты, поиск по названию, адрес, сверка (Часть 2) ──
    # Наружу уходят только публичные данные (ЕГРЮЛ): ИНН, ОГРН, название,
    # адрес. Проверка ИНН внутри инструментов не даёт отправить в DaData
    # ничего другого — ФИО и паспорта водителей сюда попасть не могут.
    # Ответы кэшируются на час: см. core/agent/dadata_tools.py.
    from core.agent.dadata_tools import (  # локальный импорт: без цикла модулей
        check_address_dadata as dadata_address,
        check_inn_dadata as dadata_inn,
        compare_with_dadata as dadata_compare,
        search_company_dadata as dadata_search,
    )

    def check_inn_dadata(inn: str = "", **_):
        """Реквизиты организации по ИНН (ЕГРЮЛ)."""
        return dadata_inn(inn=inn)

    def search_company_dadata(query: str = "", limit: int = 5, **_):
        """Поиск организации по названию в ЕГРЮЛ."""
        return dadata_search(query=query, limit=limit)

    def check_address_dadata(address: str = "", **_):
        """Стандартный вид адреса по данным DaData."""
        return dadata_address(address=address)

    def compare_with_dadata(entity_type: str = "carrier", entity_id: int = 0,
                            query: str = "", **_):
        """Сверка записи базы с ЕГРЮЛ (по ID или по названию)."""
        return dadata_compare(entity_type=entity_type, entity_id=entity_id,
                              query=query)

    registry.register(Tool(
        name="check_inn_dadata",
        description=(
            "Проверить организацию по ИНН в ЕГРЮЛ (через DaData): "
            "наименование, ОГРН, КПП, юридический адрес, руководитель, "
            "статус (действует или ликвидирована). Только публичные данные."
        ),
        parameters=_json_schema({"inn": {
            "type": "string",
            "description": "ИНН организации: 10 цифр (12 у ИП)",
        }}, required=["inn"]),
        function=check_inn_dadata,
        summary="Проверка ИНН в DaData",
    ))
    registry.register(Tool(
        name="search_company_dadata",
        description=(
            "Найти организацию по НАЗВАНИЮ среди всех действующих юрлиц "
            "России (ЕГРЮЛ, через DaData). Нужно, когда перевозчика ещё нет "
            "в справочнике, а реквизиты нужны."
        ),
        parameters=_json_schema({
            "query": {
                "type": "string",
                "description": "название организации, можно неполное",
            },
            "limit": {
                "type": "integer",
                "description": "сколько вариантов вернуть (1..10)",
                "minimum": 1,
                "maximum": 10,
            },
        }, required=["query"]),
        function=search_company_dadata,
        summary="Поиск организации в ЕГРЮЛ по названию",
    ))
    registry.register(Tool(
        name="check_address_dadata",
        description=(
            "Привести адрес к стандартному виду (ФИАС) через DaData: "
            "«г москва ул тестовая д 1» → «г Москва». Показывает, что "
            "распознано точно, а что приблизительно."
        ),
        parameters=_json_schema({"address": {
            "type": "string",
            "description": "адрес как его ввёл оператор",
        }}, required=["address"]),
        function=check_address_dadata,
        summary="Стандартизация адреса в DaData",
    ))
    registry.register(Tool(
        name="compare_with_dadata",
        description=(
            "Сверить запись базы (перевозчика или заказчика) с данными "
            "ЕГРЮЛ: какие поля расходятся и что там на самом деле. "
            "Запись указывается ЛИБО entity_id (номер из результата поиска), "
            "ЛИБО query (название) — второе удобнее, когда ID неизвестен. "
            "Только чтение — ничего не исправляет."
        ),
        parameters=_json_schema({
            "entity_type": {
                "type": "string",
                "enum": ["carrier", "customer"],
                "description": "кого сверяем",
            },
            "entity_id": {
                "type": "integer",
                "description": "номер записи из результата поиска (если известен)",
            },
            "query": {
                "type": "string",
                "description": "название организации для поиска в базе "
                               "(если ID неизвестен)",
            },
        }, required=["entity_type"]),
        function=compare_with_dadata,
        summary="Сверка записи базы с ЕГРЮЛ",
    ))

    # ── 3. Аналитика: сводки, сравнения, топы, качество данных (Часть 3) ──
    # Пять инструментов этой части считают ЧИСЛА, а не ищут записи: SQL
    # живёт в db/crud/analytics.py (правило проекта), разбор русских
    # названий полей и операций — в core/agent/analytics_tools.py.
    # Описания инструментов берутся из analytics_tools.tool_specs(): один
    # источник и для реестра, и для тестов.
    from core.agent import analytics_tools  # локальный импорт: без цикла модулей

    def aggregate_contracts(counterparty: str = "", period: str = "",
                            field: str = "amount", operation: str = "sum",
                            entity_type: str = "contracts", **_):
        """Сводка по числу: сумма, среднее, максимум, минимум, количество."""
        return analytics_tools.aggregate_contracts(
            counterparty=counterparty, period=period, field=field,
            operation=operation, entity_type=entity_type,
        )

    def compare_periods(period1: str = "", period2: str = "",
                        group_by: str = "", field: str = "amount",
                        operation: str = "sum", counterparty: str = "",
                        entity_type: str = "contracts", **_):
        """Сравнение двух периодов между собой."""
        return analytics_tools.compare_periods(
            period1, period2, group_by=group_by, entity_type=entity_type,
            field=field, operation=operation, counterparty=counterparty,
        )

    def top_contractors(period: str = "", field: str = "amount",
                        operation: str = "sum", limit: int = 5,
                        group_by: str = "", entity_type: str = "contracts",
                        counterparty: str = "", **_):
        """Топ-N по сумме (по умолчанию — перевозчики за период)."""
        return analytics_tools.top_contractors(
            period, field=field, operation=operation, limit=limit,
            group_by=group_by, entity_type=entity_type,
            counterparty=counterparty,
        )

    def count_records(entity_type: str = "drivers", filters: Any = None,
                      period: str = "", counterparty: str = "", **_):
        """Сколько записей подходит под условия."""
        return analytics_tools.count_records(
            entity_type, filters, period=period, counterparty=counterparty,
        )

    def check_data_quality(entity_type: str = "drivers",
                           required_fields: Any = None, **_):
        """Что не заполнено и где дубликаты."""
        return analytics_tools.check_data_quality(
            entity_type, required_fields=required_fields,
        )

    _ANALYTICS_FUNCTIONS = {
        "aggregate_contracts": aggregate_contracts,
        "compare_periods": compare_periods,
        "top_contractors": top_contractors,
        "count_records": count_records,
        "check_data_quality": check_data_quality,
    }

    for spec in analytics_tools.tool_specs():
        registry.register(Tool(
            name=spec["name"],
            description=spec["description"],
            parameters=spec["parameters"],
            function=_ANALYTICS_FUNCTIONS[spec["name"]],
            summary=spec["summary"],
        ))

    # ── 4. Проактивность: что дальше (Часть 4) ──
    # Тоже только чтение: подсказка — предложение, а не действие. Логика
    # «что обязательно и что дальше» живёт в core/agent/proactivity.py,
    # сроки берутся из db/crud/*.
    from core.agent import proactivity_tools  # локальный импорт: без цикла

    def suggest_next_field(**_):
        return proactivity_tools.suggest_next_field(ctx)

    def check_required(section: str = "", **_):
        return proactivity_tools.check_required(ctx, section=section)

    def warn_expiring(days: int = 30, entity_type: str = "all", **_):
        return proactivity_tools.warn_expiring(days=days, entity_type=entity_type)

    def suggest_from_history(**_):
        return proactivity_tools.suggest_from_history(ctx)

    registry.register(Tool(
        name="suggest_next_field",
        description=(
            "Что логично заполнить в открытой форме дальше. Смотри сюда, "
            "когда оператор спрашивает «что дальше?», «чего не хватает?», "
            "«чем продолжить?» — или когда сам видишь, что форма заполнена "
            "наполовину."
        ),
        parameters=_json_schema({}),
        function=suggest_next_field,
        summary="Подсказать следующее поле",
    ))
    registry.register(Tool(
        name="check_required",
        description=(
            "Какие ОБЯЗАТЕЛЬНЫЕ поля открытой формы пусты. Отличие от "
            "проверки формы: здесь только обязательные поля, списком, с "
            "указанием раздела. Можно ограничить одним разделом."
        ),
        parameters=_json_schema({"section": {
            "type": "string",
            "description": "раздел формы; пусто — все разделы",
        }}),
        function=check_required,
        summary="Проверить обязательные поля",
    ))
    registry.register(Tool(
        name="warn_expiring",
        description=(
            "Что истекает в ближайшие дни: водительские удостоверения и "
            "договоры. Например, «что скоро закончится», «у кого ВУ "
            "заканчивается», «какие договоры заканчиваются в этом месяце»."
        ),
        parameters=_json_schema({
            "days": {
                "type": "integer",
                "description": "горизонт в днях (по умолчанию 30)",
                "minimum": 0,
                "maximum": 365,
            },
            "entity_type": {
                "type": "string",
                "enum": ["all", "drivers", "contracts"],
                "description": "что проверять",
            },
        }),
        function=warn_expiring,
        summary="Предупредить об истекающих сроках",
    ))
    registry.register(Tool(
        name="suggest_from_history",
        description=(
            "Что оператор обычно делает в этом месте формы: подсказка по "
            "последним действиям и привычкам. Нужна на вопрос «что обычно "
            "тут делают дальше?» и для подсказок без вопроса."
        ),
        parameters=_json_schema({}),
        function=suggest_from_history,
        summary="Подсказать по истории действий",
    ))

    return registry


__all__ = [
    "ALL_SEARCH_PER_KIND",
    "FIELD_HELP",
    "MAX_SEARCH_RESULTS",
    "MAX_VALUE_CHARS",
    "AgentContext",
    "Tool",
    "ToolRegistry",
    "active_window",
    "build_registry",
    "collect_form_data",
    "fill_fields",
    "looks_like_command_or_noise",
    "set_context_window",
]
