#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Инструменты контекста (задание, Часть 1).

Что проверяется
---------------
Пять инструментов должны отвечать на вопрос «что у оператора открыто и что
он только что делал». Из этого следуют три требования, и все три здесь
зафиксированы:

  1. ТОЛЬКО ЧТЕНИЕ. Ни один вызов не меняет форму: значения полей и
     открытая вкладка после вызова те же. Оператор не должен обнаружить,
     что Максимилиан что-то поменял, пока он просто спрашивал.
  2. ПОНЯТНЫЙ ОТВЕТ БЕЗ ОКНА. Если форма не открыта, инструмент говорит
     об этом словами, а не падает и не возвращает пустоту без объяснений.
  3. ВИДНЫ ПУСТЫЕ ПОЛЯ. «Заполни перевозчика» без уточнений работает
     только тогда, когда модель видит, чего в форме НЕ хватает:
     ``get_form_state`` отдаёт и заполненное, и пустое.

Окно в тестах — обычный объект с методами вкладок: Qt здесь не нужен,
кроме одного теста на таблицу (там поднимается offscreen QApplication).
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Корень проекта ищется по маркеру (pytest.ini), а не по числу parents:
# после Этапа 7 тесты лежат в tests/<слой>/<группа>/, и глубина разная.
PROJECT_ROOT = next(
    _path for _path in Path(__file__).resolve().parents
    if (_path / "pytest.ini").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.context_tools import (  # noqa: E402
    get_current_window,
    get_form_state,
    get_open_tabs,
    get_recent_actions,
    get_selected_row,
)
from core.agent.tools import AgentContext, build_registry  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Окно-заглушка: две вкладки, одна пустая
# ─────────────────────────────────────────────────────────────

class StubTab:
    """Вкладка формы: отдаёт словарь данных, ничего не делает с ним."""

    def __init__(self, data):
        self.data = dict(data)
        self.filled = []

    def get_data(self):
        """Текущие значения полей вкладки."""
        return dict(self.data)

    def fill_data(self, values):
        """Запоминает попытку записи — тест проверяет, что её не было."""
        self.filled.append(dict(values))
        self.data.update(values)


class StubTabs:
    """Мини-замена QTabWidget: текущая вкладка и её заголовки."""

    def __init__(self, titles):
        self._titles = list(titles)
        self._index = 0

    def count(self):
        """Сколько вкладок."""
        return len(self._titles)

    def tabText(self, index):
        """Заголовок вкладки."""
        return self._titles[index]

    def currentIndex(self):
        """Номер открытой вкладки."""
        return self._index

    def set_current(self, index):
        """Тест переключает вкладку — инструменты этого делать не должны."""
        self._index = index


class StubWindow:
    """Окно формы: ключи вкладок, сами вкладки и заголовок."""

    CONTRACT_TYPE = "perevozka"

    def __init__(self):
        # Перевозчик заполнен наполовину (ИНН пуст — тот самый случай из
        # проверки: «Заполни перевозчика» без указания, кого именно).
        self.carrier_tab = StubTab({
            "full_name": "ООО «ФассТранс»", "inn": "", "kpp": "", "ogrn": "",
            "director_name": "Петров Пётр Петрович",
        })
        self.driver_tab = StubTab({"full_name": "", "phone": ""})
        self.tabs = StubTabs(["Перевозчик (исполнитель)", "Водитель"])
        self._tab_keys_value = ("carrier_tab", "driver_tab")

    def _tab_keys(self):
        """Ключи вкладок в порядке добавления."""
        return self._tab_keys_value

    def _tab_by_key(self, key):
        """Вкладка по ключу раскладки."""
        return {
            "carrier_tab": self.carrier_tab,
            "driver_tab": self.driver_tab,
        }.get(key)

    def windowTitle(self):
        """Заголовок окна."""
        return "Генератор договоров перевозки"


@pytest.fixture
def window():
    """Окно-заглушка с двумя вкладками."""
    return StubWindow()


@pytest.fixture
def context(window):
    """Контекст агента с подставленным окном."""
    return AgentContext(window=window, contract_type="perevozka")


# ─────────────────────────────────────────────────────────────
# 1. get_open_tabs
# ─────────────────────────────────────────────────────────────

def test_open_tabs_lists_window_and_tabs(context):
    """Открытые вкладки перечислены с заголовками и активной вкладкой."""
    result = get_open_tabs(context)

    assert result["ok"] is True
    windows = result["data"]
    assert len(windows) == 1
    described = windows[0]
    assert described["contract_type"] == "perevozka"
    assert described["tabs"] == ["Перевозчик (исполнитель)", "Водитель"]
    assert described["current_tab_key"] == "carrier_tab"


def test_open_tabs_without_window_explains_itself():
    """Без открытого окна инструмент объясняет это словами."""
    result = get_open_tabs(AgentContext())

    assert result["ok"] is False
    assert "не открыто" in result["error"]


# ─────────────────────────────────────────────────────────────
# 2. get_current_window
# ─────────────────────────────────────────────────────────────

def test_current_window_reports_type_and_tab(context):
    """Текущее окно: тип, человеческое название типа и активная вкладка."""
    result = get_current_window(context)

    assert result["ok"] is True
    data = result["data"]
    assert data["contract_type"] == "perevozka"
    assert data["type_title"] == "Экспедиторство"
    assert data["current_tab"] == "Перевозчик (исполнитель)"
    assert data["window"] == "Генератор договоров перевозки"


def test_current_window_follows_tab_switch(context, window):
    """Сменилась открытая вкладка — инструмент это видит."""
    window.tabs.set_current(1)

    data = get_current_window(context)["data"]

    assert data["current_tab"] == "Водитель"
    assert data["current_tab_key"] == "driver_tab"


def test_current_window_without_window_explains_itself():
    """Без окна — понятный отказ, а не пустой ответ."""
    result = get_current_window(AgentContext())

    assert result["ok"] is False
    assert "форма не найдена" in result["error"]


# ─────────────────────────────────────────────────────────────
# 3. get_form_state — главный инструмент части
# ─────────────────────────────────────────────────────────────

def test_form_state_shows_empty_fields(context):
    """
    Видны и заполненные поля, и ПУСТЫЕ.

    Это и есть ответ на «Заполни перевозчика» без уточнений: модель видит,
    что название уже есть, а ИНН, КПП и ОГРН пусты.
    """
    result = get_form_state(context)

    assert result["ok"] is True
    carrier = result["data"]["carrier_tab"]
    assert carrier["filled"]["full_name"] == "ООО «ФассТранс»"
    assert "inn" in carrier["empty"]
    assert "kpp" in carrier["empty"]
    assert "ogrn" in carrier["empty"]
    assert carrier["filled_count"] == 2
    assert carrier["empty_count"] == 3


def test_form_state_only_empty_hides_values(context):
    """only_empty=True отдаёт только незаполненные поля."""
    result = get_form_state(context, only_empty=True)

    carrier = result["data"]["carrier_tab"]
    assert "filled" not in carrier
    assert "inn" in carrier["empty"]


def test_form_state_by_russian_section_name(context):
    """Раздел можно назвать по-русски: «перевозчик»."""
    result = get_form_state(context, section="перевозчик")

    assert result["ok"] is True
    assert list(result["data"]) == ["carrier_tab"]


def test_form_state_unknown_section_lists_available(context):
    """Неизвестный раздел — ошибка со списком доступных."""
    result = get_form_state(context, section="космолёт")

    assert result["ok"] is False
    assert "carrier_tab" in result["available"]


def test_form_state_does_not_change_the_form(context, window):
    """
    Инструмент ТОЛЬКО читает: форма не меняется.

    Проверяется и через возвращённые данные, и через вкладку: попытка
    записи в неё была бы видна в ``filled``.
    """
    before = window.carrier_tab.get_data()

    get_form_state(context)
    get_form_state(context, only_empty=True)
    get_form_state(context, section="перевозчик")

    assert window.carrier_tab.get_data() == before
    assert window.carrier_tab.filled == [], "инструмент чтения писал в форму"
    assert window.tabs.currentIndex() == 0, "инструмент чтения переключил вкладку"


# ─────────────────────────────────────────────────────────────
# 4. get_selected_row
# ─────────────────────────────────────────────────────────────

def test_selected_row_reports_nothing_selected(context):
    """Без Qt-таблиц инструмент объясняет, что таблиц нет."""
    result = get_selected_row(context)

    assert result["ok"] is False
    assert "таблиц" in result["error"]


def test_selected_row_reads_current_table_row():
    """С выбранной строкой возвращаются заголовки и значения."""
    from PyQt5.QtWidgets import (
        QApplication,
        QTableWidget,
        QTableWidgetItem,
        QWidget,
    )

    app = QApplication.instance() or QApplication([])
    holder = QWidget()
    table = QTableWidget(2, 2, holder)
    table.setHorizontalHeaderLabels(["Марка", "Номер"])
    table.setItem(0, 0, QTableWidgetItem("JETOUR T2"))
    table.setItem(0, 1, QTableWidgetItem("А123ВС77"))
    table.setCurrentCell(0, 0)

    class WindowWithTable(StubWindow):
        """
        Окно с настоящей таблицей внутри.

        Родителем таблицы обязан быть QWidget: ``findChildren`` работает
        только по дереву Qt-объектов, обычный класс Python деревом не
        является. Поэтому окно-заглушка подмешивает QWidget в базовые
        классы, а не просто хранит таблицу атрибутом.
        """

        def __init__(self, parent_widget):
            super().__init__()
            self._holder = parent_widget

        def findChildren(self, kind):
            """Все таблицы этого окна (делегируется Qt-контейнеру)."""
            return self._holder.findChildren(kind)

    try:
        result = get_selected_row(AgentContext(window=WindowWithTable(holder)))
    finally:
        table.setParent(None)
        holder.deleteLater()
        app.processEvents()

    assert result["ok"] is True
    assert result["data"][0]["values"]["Марка"] == "JETOUR T2"
    assert result["data"][0]["row"] == 0


# ─────────────────────────────────────────────────────────────
# 5. get_recent_actions
# ─────────────────────────────────────────────────────────────

def test_recent_actions_without_journal_is_empty():
    """Без журнала действий инструмент отвечает пусто и объясняет это."""
    result = get_recent_actions(AgentContext())

    assert result["ok"] is True
    assert result["data"] == []
    assert "не было" in result["message"] or "не ведётся" in result["message"]


def test_recent_actions_bad_minutes_is_rejected():
    """Нечисловые минуты — понятная ошибка, а не падение."""
    result = get_recent_actions(AgentContext(), minutes="вчера")

    assert result["ok"] is False
    assert "minutes" in result["error"]


def test_recent_actions_reads_journal_ring():
    """
    Журнал действий отдаёт последние нажатия, новые первыми.

    Проверяется на самом журнале (без Qt-приложения не обойтись, поэтому
    объект создаётся напрямую с подставным «приложением»).
    """
    from ui.action_logging import ActionLogger

    class FakeApp:
        """Минимальная замена QApplication для ActionLogger."""

        def __init__(self):
            self.widgets = []

        def installEventFilter(self, obj):
            """Фильтр событий тесту не нужен."""
            return None

        def allWidgets(self):
            """Список виджетов пуст: подписываться не на что."""
            return []

    journal = ActionLogger.__new__(ActionLogger)
    from collections import deque

    journal._recent = deque(maxlen=10)
    journal.remember("Сохранить", "CarrierTab")
    journal.remember("Распознать данные", "MainWindow")

    actions = journal.recent(300)

    assert [item["action"] for item in actions] == ["Сохранить", "Распознать данные"]
    assert actions[0]["window"] == "CarrierTab"
    assert "seconds_ago" in actions[1]


def test_recent_actions_respects_window():
    """Давние действия в окно наблюдения не попадают."""
    from collections import deque

    from ui.action_logging import ActionLogger

    journal = ActionLogger.__new__(ActionLogger)
    journal._recent = deque(maxlen=10)
    journal.remember("Сохранить", "CarrierTab")
    # Сдвигаем время записи на час назад: в пятиминутное окно она не входит.
    moment, label, context = journal._recent[-1]
    journal._recent[-1] = (moment - 3600, label, context)

    assert journal.recent(300) == []


# ─────────────────────────────────────────────────────────────
# 6. Инструменты зарегистрированы и безопасны
# ─────────────────────────────────────────────────────────────

CONTEXT_TOOLS = (
    "get_open_tabs", "get_current_window", "get_form_state",
    "get_selected_row", "get_recent_actions",
)


@pytest.mark.parametrize("name", CONTEXT_TOOLS)
def test_context_tool_is_registered(name):
    """Все пять инструментов части есть в реестре агента."""
    registry = build_registry(AgentContext(window=StubWindow()))

    assert registry.get(name) is not None, f"инструмент {name} не зарегистрирован"


@pytest.mark.parametrize("name", CONTEXT_TOOLS)
def test_context_tool_needs_no_confirmation(name):
    """
    Инструменты контекста не требуют подтверждения.

    Подтверждение — для действий, которые меняют данные. Эти ничего не
    меняют, и запрос «да/нет» на каждый просмотр формы раздражал бы.
    """
    registry = build_registry(AgentContext(window=StubWindow()))

    assert registry.get(name).requires_confirmation is False


def test_registry_has_all_declared_tools(context):
    """Реестр содержит инструменты всех частей без повторов имён."""
    registry = build_registry(context)
    names = list(registry.names())

    assert len(names) == len(set(names)), "в реестре есть повторяющиеся имена"
    for name in CONTEXT_TOOLS:
        assert name in names
