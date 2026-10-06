#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты окна типа «Хавалы» на реальных вкладках (ЭТАП 3.1.E.B.3).

Проверяют то, что появилось на этом шаге: окно собирается на шести настоящих
вкладках (а не на заглушках «в разработке»), вкладки лежат атрибутами
``zayavka_tab`` … ``price_tab`` — по ним их ищет сборщик данных B.1, сигналы
вкладок подключены к слотам окна, «Очистить форму» чистит только свою вкладку,
«Создать договор» проходит путь валидатор → диалог → генератор → .xlsx →
диалог успеха, а распознавание раскладывает ответ модели по вкладкам, не стирая
ручной ввод.

Отдельная группа тестов — сама раскладка блока «zayavka» по вкладкам
(_zayavka_tab_data, _route_tab_data, _driver_tab_data, _vehicle_tab_data,
_price_tab_data, _cargo_tab_data): у Хавалов имена полей вкладок СОВПАДАЮТ
с ключами схемы промпта (core/prompts/havaly.py), поэтому окно не переименовывает
поля, а отбирает свои — и проверять это удобнее напрямую, без интерфейса.

Qt — в offscreen-режиме. Сеть и системное хранилище ключей не трогаются:
клиент GigaChat подменяется заглушкой, а промпт типа проверяется по аргументам,
с которыми окно позвало recognize_text.

Данные синтетические, реальных ПДн нет. Генерация идёт в tests/_tmp: рабочий
output/ тесты не трогают, дата заявки в них — 01.01.2001 (файл
``Заявка_Хавалы_2001-01-01.xlsx`` за прогон перезаписывается только тестами).
"""

import gc
import inspect
import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

import ui.windows.havaly.window as havaly_window_module  # noqa: E402
from core.contracts.factory import GeneratorFactory  # noqa: E402
from core.contracts.registry import ContractTypeRegistry  # noqa: E402
from core.contracts.zayavka.generator import (  # noqa: E402
    CARRIER_NAME as GENERATOR_CARRIER_NAME,
    CUSTOMER_NAME as GENERATOR_CUSTOMER_NAME,
    ZayavkaExcelGenerator,
)
from core.contracts.zayavka.validator import ZayavkaExcelValidator  # noqa: E402
from core.dates import parse_date  # noqa: E402
from core.prompts import get_prompt  # noqa: E402
from ui.windows.havaly import HavalyWindow  # noqa: E402
from ui.windows.havaly.data import SECTION_TITLES, collect_havaly_data  # noqa: E402
from ui.windows.havaly.tabs import (  # noqa: E402
    CargoTab, CustomerTab, DriverTab, PriceTab, RouteTab, VehicleTab,
)
from ui.windows.havaly.window import RecognitionTask  # noqa: E402

# ─────────────────────────────────────────────────────────────
# Константы тестовых данных (синтетика)
# ─────────────────────────────────────────────────────────────

#: Ожидаемый состав вкладок: (заголовок, класс) — порядок как в окне.
TAB_SPECS = (
    ("Заявка", CustomerTab),
    ("Груз", CargoTab),
    ("Маршрут", RouteTab),
    ("Водитель", DriverTab),
    ("ТС", VehicleTab),
    ("Стоимость", PriceTab),
)

#: Атрибут окна → вкладка: ровно эти имена ищет сборщик B.1
#: (ui/windows/havaly/data.py::_tabs_of, ключи SECTION_TITLES).
TAB_ATTRIBUTES = (
    ("zayavka_tab", CustomerTab),
    ("cargo_tab", CargoTab),
    ("route_tab", RouteTab),
    ("driver_tab", DriverTab),
    ("vehicle_tab", VehicleTab),
    ("price_tab", PriceTab),
)

#: Дата заявки. Год 2001 — чтобы имя готового файла (оно зависит ТОЛЬКО от
#: даты заявки) не совпало с рабочей заявкой пользователя в output/.
DATE_ISO = "2001-01-01"
DATE_DOCUMENT = "01.01.2001"

LOT_NUMBER = "ЛОТ-2001-001"
LOADING_CITY = "г. Москва"
LOADING_POINT = "Склад Север, ул. Складская, д. 1"
UNLOADING_CITY = "г. Казань"
UNLOADING_POINT = "Площадка Юг, ул. Промышленная, д. 5"
LOADING_PLAN_TIME = "09:00"

TRACTOR_BRAND = "КАМАЗ-5490"
TRACTOR_COLOR = "Белый"
TRACTOR_PLATE = "А001АА77"
TRAILER_BRAND = "Тонар-9741"
TRAILER_PLATE = "БВ002277"

DRIVER_LAST_NAME = "Иванов"
DRIVER_FIRST_NAME = "Иван"
DRIVER_MIDDLE_NAME = "Иванович"
DRIVER_BIRTH_DATE = "1985-05-05"
DRIVER_LICENSE_NUMBER = "99 АА 123456"
DRIVER_LICENSE_ISSUE_DATE = "2020-01-01"
DRIVER_PASSPORT_SERIES = "18 22"
DRIVER_PASSPORT_NUMBER = "926830"
DRIVER_PASSPORT_ISSUER = "Отделом УФМС России по г. Москве"
DRIVER_PASSPORT_ISSUE_DATE = "2023-01-30"
DRIVER_CITIZENSHIP = "Российская Федерация"
DRIVER_REGISTRATION = "г. Москва, ул. Водительская, д. 3"
DRIVER_PHONE = "+7 (999) 123-45-67"

PRICE_WITH_VAT = 180300.0
VAT_RATE = "22%"

#: Машины таблицы груза: пять полей схемы промпта, VIN — 17 символов без
#: букв I, O, Q (иначе валидатор типа справедливо предупредит о VIN).
VEHICLES = (
    {"vin": "XTC651150N0001001", "brand": "LADA", "model": "Vesta",
     "dealer": "Дилер-1", "dealer_code": "D-001"},
    {"vin": "XTC651150N0001002", "brand": "UAZ", "model": "Profi",
     "dealer": "Дилер-2", "dealer_code": "D-002"},
)

#: Заголовок запущенного окна и имя его модуля — для проверок логов.
WINDOW_LOGGER = "ui.windows.havaly.window"


# ─────────────────────────────────────────────────────────────
# Фикстуры и помощники
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _release_documents():
    """Excel-файлы на Windows освобождаем до удаления временных копий."""
    yield
    gc.collect()


@pytest.fixture
def quiet_messages(monkeypatch):
    """
    Глушит диалоги QMessageBox и запоминает их тексты.

    :return: словарь со списками текстов information / warning / critical.
    """
    seen = {"information": [], "warning": [], "critical": []}

    def recorder(kind):
        def _record(parent, title, text, *args, **kwargs):
            seen[kind].append(text)
            return QMessageBox.Ok
        return staticmethod(_record)

    for kind in seen:
        monkeypatch.setattr(QMessageBox, kind, recorder(kind))
    return seen


@pytest.fixture
def window(qt_app, quiet_messages):
    """
    Свежее окно «Хавалов»; по завершении теста закрывается по-настоящему.

    Перед закрытием ждём пул распознавания: поток, переживший окно, роняет
    процесс при разрушении QThreadPool (access violation на выходе).
    """
    win = HavalyWindow()
    yield win
    win.thread_pool.waitForDone(5000)
    qt_app.processEvents()
    win.force_close()


@pytest.fixture
def hav_template(templates_dir):
    """Эталонный бланк Хавалов: без него генерировать нечего."""
    path = Path(templates_dir) / "shablon_havaly.xlsx"
    assert path.exists(), f"нет эталонного бланка: {path}"
    return path


@pytest.fixture
def output_dir(work_dir):
    """
    Папка вывода теста: рабочий output/ тесты не трогают.

    Перед тестом папка чистится: имя готового файла зависит от даты заявки,
    поэтому файл от прошлого прогона выглядел бы как созданный этим тестом
    (проверки «файла нет» иначе теряют смысл).
    """
    folder = work_dir / "havaly_window_output"
    folder.mkdir(parents=True, exist_ok=True)
    for leftover in folder.glob("*.xlsx"):
        leftover.unlink(missing_ok=True)
    return folder


@pytest.fixture
def hav_generator(templates_dir, output_dir, monkeypatch):
    """
    Настоящий генератор Хавалов, пишущий в папку теста.

    Фабрика подменяется целиком: окно создаёт генератор само
    (GeneratorFactory.get_generator), и другого места, куда подставить
    каталог вывода, у него нет. default_output_dir — метод класса, поэтому
    подменяем его у класса, а не у экземпляра.
    """
    generator = ZayavkaExcelGenerator(templates_dir=str(templates_dir))
    monkeypatch.setattr(
        GeneratorFactory, "get_generator",
        classmethod(lambda cls, *args, **kwargs: generator),
    )
    monkeypatch.setattr(
        ZayavkaExcelGenerator, "default_output_dir",
        classmethod(lambda cls: str(output_dir)),
    )
    return generator


class FakeClient:
    """Заглушка клиента GigaChat: в сеть не ходит, помнит аргументы вызова."""

    def __init__(self, answer=None):
        self.answer = answer if answer is not None else {}
        self.calls = []

    def recognize_text(self, text, prompt=None):
        self.calls.append({"text": text, "prompt": prompt})
        return self.answer


def _zayavka_block(**overrides) -> dict:
    """
    Блок «zayavka» схемы промпта Хавалов — все 30 полей.

    Заполнены все: так у валидатора типа не остаётся замечаний, и видно,
    что каждая вкладка получила ровно свои поля. Стороны — константы
    генератора (в схеме они фиксированы).
    """
    block = {
        "date": DATE_DOCUMENT,
        "lot_number": LOT_NUMBER,
        "loading_city": LOADING_CITY,
        "loading_point": LOADING_POINT,
        "unloading_city": UNLOADING_CITY,
        "unloading_point": UNLOADING_POINT,
        "carrier_name": GENERATOR_CARRIER_NAME,
        "customer_name": GENERATOR_CUSTOMER_NAME,
        "tractor_brand": TRACTOR_BRAND,
        "tractor_color": TRACTOR_COLOR,
        "tractor_plate": TRACTOR_PLATE,
        "trailer_brand": TRAILER_BRAND,
        "trailer_plate": TRAILER_PLATE,
        "driver_last_name": DRIVER_LAST_NAME,
        "driver_first_name": DRIVER_FIRST_NAME,
        "driver_middle_name": DRIVER_MIDDLE_NAME,
        "driver_license_number": DRIVER_LICENSE_NUMBER,
        "driver_license_issue_date": DRIVER_LICENSE_ISSUE_DATE,
        "driver_passport_series": DRIVER_PASSPORT_SERIES,
        "driver_passport_number": DRIVER_PASSPORT_NUMBER,
        "driver_passport_issuer": DRIVER_PASSPORT_ISSUER,
        "driver_passport_issue_date": DRIVER_PASSPORT_ISSUE_DATE,
        "driver_citizenship": DRIVER_CITIZENSHIP,
        "driver_birth_date": DRIVER_BIRTH_DATE,
        "driver_registration": DRIVER_REGISTRATION,
        "driver_phone": DRIVER_PHONE,
        "loading_plan_date": DATE_DOCUMENT,
        "loading_plan_time": LOADING_PLAN_TIME,
        "price_with_vat": PRICE_WITH_VAT,
        "vat_rate": VAT_RATE,
    }
    block.update(overrides)
    return block


def _recognized_answer() -> dict:
    """
    Ответ модели в схеме промпта Хавалов: блок «zayavka» и массив «vehicles».

    Других блоков у этого типа нет — ни contract, ни driver, ни tractor
    (core/prompts/havaly.py, раздел «ЧЕГО В ОТВЕТЕ БЫТЬ НЕ ДОЛЖНО»).
    """
    return {"zayavka": _zayavka_block(), "vehicles": [dict(v) for v in VEHICLES]}


def _fill_all_tabs(win) -> None:
    """
    Заполняет все шесть вкладок данными, которых хватает валидатору.

    Даты — в ISO: так их отдают сами вкладки (QDateEdit → toString), и так
    же они приходят в сборщик из живого окна. Формат документа собирает
    сборщик B.1.
    """
    win.zayavka_tab.fill_data({"date": DATE_ISO, "lot_number": LOT_NUMBER})
    win.cargo_tab.fill_data({"vehicles": [dict(v) for v in VEHICLES]})
    win.route_tab.fill_data({
        "loading_city": LOADING_CITY,
        "loading_point": LOADING_POINT,
        "unloading_city": UNLOADING_CITY,
        "unloading_point": UNLOADING_POINT,
        "loading_plan_date": DATE_ISO,
        "loading_plan_time": LOADING_PLAN_TIME,
    })
    win.driver_tab.fill_data({
        "driver_last_name": DRIVER_LAST_NAME,
        "driver_first_name": DRIVER_FIRST_NAME,
        "driver_middle_name": DRIVER_MIDDLE_NAME,
        "driver_license_number": DRIVER_LICENSE_NUMBER,
        "driver_license_issue_date": DRIVER_LICENSE_ISSUE_DATE,
        "driver_passport_series": DRIVER_PASSPORT_SERIES,
        "driver_passport_number": DRIVER_PASSPORT_NUMBER,
        "driver_passport_issuer": DRIVER_PASSPORT_ISSUER,
        "driver_passport_issue_date": DRIVER_PASSPORT_ISSUE_DATE,
        "driver_citizenship": DRIVER_CITIZENSHIP,
        "driver_birth_date": DRIVER_BIRTH_DATE,
        "driver_registration": DRIVER_REGISTRATION,
        "driver_phone": DRIVER_PHONE,
    })
    win.vehicle_tab.fill_data({
        "tractor_brand": TRACTOR_BRAND,
        "tractor_color": TRACTOR_COLOR,
        "tractor_plate": TRACTOR_PLATE,
        "trailer_brand": TRAILER_BRAND,
        "trailer_plate": TRAILER_PLATE,
    })
    win.price_tab.fill_data({
        "price_with_vat": PRICE_WITH_VAT,
        "vat_rate": VAT_RATE,
    })


def _clear_checkers() -> tuple:
    """
    Как проверить, что вкладка действительно очистилась: (индекс, проверка).

    У «Заявки» после очистки стороны остаются константами генератора (они
    фиксированы), у «Водителя» даты возвращаются к значениям по умолчанию —
    поэтому проверяются только те поля, которые вкладка реально чистит.
    """
    return (
        (0, "Заявка", lambda tab: tab.get_data()["lot_number"] == ""),
        (1, "Груз", lambda tab: tab.get_data()["vehicles"] == []),
        (2, "Маршрут", lambda tab: tab.get_data()["loading_city"] == ""
            and tab.get_data()["loading_point"] == ""),
        (3, "Водитель", lambda tab: tab.get_data()["driver_last_name"] == ""
            and tab.get_data()["driver_phone"] == ""),
        (4, "ТС", lambda tab: tab.get_data()["tractor_brand"] == ""
            and tab.get_data()["trailer_plate"] == ""),
        (5, "Стоимость", lambda tab: tab.get_data().get("price_with_vat") is None
            or "price_with_vat" not in tab.get_data()),
    )


def _assert_other_tabs_intact(win, cleared_index: int) -> None:
    """Соседние вкладки после «Очистить форму» остались заполненными."""
    if cleared_index != 0:
        assert win.zayavka_tab.get_data()["lot_number"] == LOT_NUMBER
    if cleared_index != 1:
        assert len(win.cargo_tab.get_data()["vehicles"]) == len(VEHICLES)
    if cleared_index != 2:
        assert win.route_tab.get_data()["loading_city"] == LOADING_CITY
    if cleared_index != 3:
        assert win.driver_tab.get_data()["driver_last_name"] == DRIVER_LAST_NAME
    if cleared_index != 4:
        assert win.vehicle_tab.get_data()["tractor_plate"] == TRACTOR_PLATE
    if cleared_index != 5:
        assert win.price_tab.get_data()["price_with_vat"] == PRICE_WITH_VAT


def _answer_recognition(window, data):
    """
    Отдаёт окну ответ модели так же, как это сделала бы задача распознавания.

    Задача передаётся аргументом слота: именно так её связывает
    _start_recognition (functools.partial), и так слот понимает, чей
    результат пришёл.
    """
    task = RecognitionTask(window.gigachat or FakeClient(), "текст", prompt="промпт")
    window.recognition_task = task

    window._on_recognition_finished(data, task=task)
    return task


def _same_date(left: str, right: str) -> bool:
    """
    Одна и та же дата, записанная по-разному.

    Вкладки отдают ISO («2001-01-01»), а бланк и чтение формы — формат
    документа («01.01.2001»): сравнивать строки нельзя, только даты.
    """
    return parse_date(left, warn=False) == parse_date(right, warn=False)


# ─────────────────────────────────────────────────────────────
# 1. Окно и его вкладки
# ─────────────────────────────────────────────────────────────

def test_window_builds_with_six_tabs(window):
    assert window.CONTRACT_TYPE == "zayavka_excel"
    assert window.windowTitle() == "Хавалы"
    assert window.tabs.count() == 6
    assert window.tab_titles() == [title for title, _cls in TAB_SPECS]
    assert window.side_nav.count() == 6


def test_window_builds_without_arguments(qt_app, quiet_messages):
    """Окно создаётся без аргументов — как его и создаёт менеджер окон."""
    win = HavalyWindow()
    try:
        assert win.parent() is None
        assert win.tabs.count() == 6
    finally:
        win.force_close()


def test_make_tab_returns_real_tab_classes(window):
    """Хук _make_tab отдаёт настоящие вкладки, а не заглушки «в разработке»."""
    for index, (title, tab_class) in enumerate(TAB_SPECS):
        tab = window.tabs.widget(index)
        assert isinstance(tab, tab_class), title

    for tab in window._tabs():
        assert hasattr(tab, "recognition_panel"), type(tab).__name__
        assert hasattr(tab, "get_data"), type(tab).__name__


def test_tabs_are_named_attributes(window):
    """
    Окно держит вкладки по именам, которые ищет сборщик B.1.

    Это главный стык шага: collect_havaly_data берёт вкладки атрибутами
    ``<раздел>_tab`` (data.SECTION_TITLES), и переименование здесь молча
    оставило бы разделы пустыми.
    """
    for attribute, tab_class in TAB_ATTRIBUTES:
        tab = getattr(window, attribute, None)
        assert isinstance(tab, tab_class), attribute


def test_tab_attribute_matches_position(window):
    """Имя вкладки соответствует её месту в TAB_CONFIGS."""
    named = {
        "zayavka_tab": 0, "cargo_tab": 1, "route_tab": 2,
        "driver_tab": 3, "vehicle_tab": 4, "price_tab": 5,
    }
    for attribute, index in named.items():
        assert getattr(window, attribute) is window.tabs.widget(index), attribute


def test_tab_attributes_match_collector_sections(window):
    """
    Ключи сборщика и атрибуты окна — одно и то же множество.

    SECTION_TITLES задаёт и имена атрибутов, и заголовки вкладок: если
    одна из сторон разойдётся, сборщик отдаст пустые разделы.
    """
    assert set(SECTION_TITLES) == {
        attribute[: -len("_tab")] for attribute, _cls in TAB_ATTRIBUTES
    }
    assert set(SECTION_TITLES.values()) == set(window.tab_titles())


def test_unknown_tab_title_falls_back_to_placeholder(window):
    """Опечатка в TAB_CONFIGS не оставляет окно без страницы."""
    tab = window._make_tab("Такой вкладки нет", "contract.svg")

    assert tab is not None
    assert not hasattr(tab, "get_data")


def test_header_has_create_contract_button(window):
    """«Создать договор» есть и в шапке, и на вкладках (как в MainWindow)."""
    assert window.btn_create_contract.text() == "Создать договор"
    assert window.btn_create_contract.parent() is not None
    for tab in window._tabs():
        assert tab.btn_create_contract.text() == "Создать договор"


def test_header_button_stands_before_exit(window):
    """Кнопка шапки встаёт перед «Выход», а не после неё."""
    header = window.btn_exit.parentWidget().layout()
    assert header.indexOf(window.btn_create_contract) < header.indexOf(window.btn_exit)


def test_every_tab_has_buttons_and_recognition_panel(window):
    """У всех шести вкладок есть панель действий и панель распознавания."""
    for tab in window._tabs():
        assert tab.btn_create_contract.isEnabled() is True
        assert tab.btn_clear_form.isEnabled() is True
        # «Распознать данные» живёт на панели распознавания вкладки.
        assert tab.recognition_panel.btn_recognize.isEnabled() is True


def test_gigachat_is_not_created_in_init(window):
    """Клиент GigaChat — ленивый: простое открытие окна ключа не требует."""
    assert window.gigachat is None
    assert window.recognition_task is None


def test_type_is_registered_for_factory(window):
    """
    Окно прогревает реестр типов: фабрика знает «zayavka_excel».

    Без этого GeneratorFactory взяла бы генератор по умолчанию, и заявка
    собиралась бы по чужому шаблону.
    """
    spec = ContractTypeRegistry.find("zayavka_excel")

    assert spec is not None
    assert spec.generator_class is ZayavkaExcelGenerator
    assert spec.validator_class is ZayavkaExcelValidator


# ─────────────────────────────────────────────────────────────
# 2. Сигналы вкладок подключены к слотам окна
# ─────────────────────────────────────────────────────────────
# Сигнал подключён к связанному методу окна, поэтому доказательство
# связи — вызов этого же пути: клик по кнопке вкладки доходит до
# _collect_data() и _confirm_validation(). Подменять сами слоты после
# создания окна бессмысленно: связь уже держит исходный метод.

def test_tab_create_signal_is_connected_to_window(window, monkeypatch):
    reports = []
    monkeypatch.setattr(
        HavalyWindow, "_confirm_validation",
        lambda self, report: reports.append(report) and False,
    )

    for tab in window._tabs():
        reports.clear()
        tab.btn_create_contract.click()
        assert len(reports) == 1, type(tab).__name__


def test_header_create_button_uses_the_same_slot_as_tabs(window, monkeypatch):
    """
    «Создать договор» в шапке и на вкладке ведут в один слот окна.

    Проверяется по исходнику: обе связи идут на _on_create_contract, а не
    на разные обработчики — иначе шапка и вкладка давали бы разный результат.
    """
    assert "_on_create_contract" in inspect.getsource(
        HavalyWindow._build_header_actions
    )
    assert "_on_create_contract" in inspect.getsource(HavalyWindow._bind_tabs)

    reports = []
    monkeypatch.setattr(
        HavalyWindow, "_confirm_validation",
        lambda self, report: reports.append(report) and False,
    )

    window.btn_create_contract.click()

    assert len(reports) == 1


#: Вкладки, у которых проверяется сигнал распознавания: (индекс, заголовок).
RECOGNIZE_SPECS = tuple(
    (index, title) for index, (title, _cls) in enumerate(TAB_SPECS)
)


@pytest.mark.parametrize(
    "index,title", RECOGNIZE_SPECS, ids=[spec[1] for spec in RECOGNIZE_SPECS]
)
def test_tab_recognize_signal_is_connected_to_window(
    window, monkeypatch, index, title
):
    """Кнопка «Распознать вкладку» каждой вкладки доходит до слота окна."""
    clients = []
    monkeypatch.setattr(
        HavalyWindow, "_ensure_gigachat",
        lambda self: clients.append(FakeClient()) or clients[-1],
    )

    tab = window.tabs.widget(index)
    tab.recognition_panel.text_edit.setPlainText("текст заявки Хавалов")
    tab.recognition_panel.btn_recognize.click()

    assert len(clients) == 1, title
    assert window.recognition_task is not None
    assert window.recognition_task.text == "текст заявки Хавалов"


@pytest.mark.parametrize(
    "index,title,is_cleared", _clear_checkers(),
    ids=[spec[1] for spec in _clear_checkers()],
)
def test_tab_clear_signal_clears_only_its_own_tab(
    window, index, title, is_cleared
):
    """Кнопка «Очистить форму» каждой вкладки чистит только свою вкладку."""
    _fill_all_tabs(window)

    tab = window.tabs.widget(index)
    tab.btn_clear_form.click()

    assert is_cleared(tab), title
    _assert_other_tabs_intact(window, index)


def test_window_connect_does_not_use_lambda():
    """
    В connect — методы и partial, но не lambda: lambda, захватывающая окно
    или вкладку, создаёт цикл ссылок Python ↔ Qt и роняет процесс при
    завершении.
    """
    for method in (
        HavalyWindow.__init__,
        HavalyWindow._build_header_actions,
        HavalyWindow._bind_tabs,
        HavalyWindow._start_recognition,
    ):
        source = inspect.getsource(method)
        assert ".connect(lambda" not in source.replace(" ", ""), source.splitlines()[0]


def test_window_connects_only_bound_methods_and_partial():
    """
    Все connect окна ведут на связанные методы окна или на partial.

    Иначе говоря, среди связей нет замыканий на вкладку: вкладка живёт
    дольше сигнала, и цикл Python ↔ Qt не даёт сборщику мусора освободить
    ни окно, ни вкладки при выходе из программы.
    """
    source = inspect.getsource(havaly_window_module)
    targets = re.findall(r"\.connect\(([^)]*)", source)

    assert targets, "в модуле окна не нашлось ни одной связи сигналов"
    for target in targets:
        text = target.strip()
        assert text.startswith(("self._", "partial(")), text


def test_recognition_signals_are_bound_to_task_through_partial():
    """
    Сигналы задачи привязываются к самой задаче (partial), а не к lambda.

    Именованный аргумент task виден в подписи слота и отличает результат
    «своей» задачи от результата отменённой.
    """
    source = inspect.getsource(HavalyWindow._start_recognition)

    assert source.count("partial(") == 3
    assert source.count("task=task") == 3


def test_tabs_do_not_reference_the_window(window):
    """
    Вкладки не держат ссылок на окно — цикла Python ↔ Qt нет.

    Связь идёт только в одну сторону (окно → вкладка): обратная ссылка
    замкнула бы цикл, и ни окно, ни вкладки не освободились бы при выходе.
    """
    for tab in window._tabs():
        for name, value in vars(tab).items():
            assert value is not window, f"{type(tab).__name__}.{name}"


# ─────────────────────────────────────────────────────────────
# 3. Сбор данных: сборщик B.1 находит вкладки по атрибутам
# ─────────────────────────────────────────────────────────────

def test_collect_data_finds_every_tab_by_attribute(window):
    """
    collect_havaly_data(window) находит все шесть вкладок по атрибутам.

    Проверка стыка «окно ↔ сборщик»: сборщик вызывается на живом окне, и
    каждая вкладка реально отдаёт свои поля. Если бы атрибуты назывались
    иначе, сборщик честно вернул бы пустые разделы — здесь этого не видно
    только потому, что имена совпадают.

    Дата заявки и план погрузки идут через QDateEdit: вкладка отдаёт ISO,
    а сборщик переводит её в формат документа — поэтому они сравниваются
    разбором даты, а не строкой.
    """
    _fill_all_tabs(window)

    payload = collect_havaly_data(window)

    assert sorted(payload) == ["contract", "vehicles", "zayavka"]

    zayavka = payload["zayavka"]
    # Поля всех шести вкладок, кроме «Груза» (он уходит в vehicles).
    assert zayavka["lot_number"] == LOT_NUMBER
    assert zayavka["date"] == DATE_DOCUMENT
    assert zayavka["loading_city"] == LOADING_CITY
    assert zayavka["loading_point"] == LOADING_POINT
    assert zayavka["unloading_city"] == UNLOADING_CITY
    assert zayavka["unloading_point"] == UNLOADING_POINT
    assert _same_date(zayavka["loading_plan_date"], DATE_ISO)
    assert zayavka["loading_plan_time"] == LOADING_PLAN_TIME
    assert zayavka["driver_last_name"] == DRIVER_LAST_NAME
    assert zayavka["driver_passport_series"] == DRIVER_PASSPORT_SERIES
    assert zayavka["driver_phone"] == DRIVER_PHONE
    assert zayavka["tractor_brand"] == TRACTOR_BRAND
    assert zayavka["tractor_plate"] == TRACTOR_PLATE
    assert zayavka["trailer_brand"] == TRAILER_BRAND
    assert zayavka["trailer_plate"] == TRAILER_PLATE
    assert zayavka["price_with_vat"] == PRICE_WITH_VAT
    assert zayavka["vat_rate"] == VAT_RATE

    assert payload["vehicles"] == [dict(v) for v in VEHICLES]


def test_collect_data_returns_prompt_schema_not_contract_data(window):
    """
    Данные окна — словарь схемы промпта, а не ContractData.

    Генератор .xlsx читает схему напрямую, а ContractData корневой блок
    ``zayavka`` не хранит: поля поэтому лежат дважды — в схеме и в форме
    ContractData (ключ ``contract``), см. ui/windows/havaly/data.py.
    """
    _fill_all_tabs(window)

    payload = window._collect_data()

    assert isinstance(payload, dict)
    assert set(payload["zayavka"]) >= {"lot_number", "tractor_plate"}
    assert payload["contract"]["contract"]["lot_number"] == LOT_NUMBER
    assert payload["contract"]["tractor"]["brand_model"] == TRACTOR_BRAND


def test_collect_data_is_repeatable_and_does_not_change_ui(window):
    """_collect_data только читает: повторный вызов даёт тот же результат."""
    _fill_all_tabs(window)

    first = window._collect_data()
    second = window._collect_data()

    assert first == second
    assert window.vehicle_tab.get_data()["tractor_plate"] == TRACTOR_PLATE


def test_empty_window_gives_empty_sections(window):
    """
    Пустое окно: разделы пустые, но фиксированные стороны на месте.

    Незаполненные поля ключа не создают: сборщик отдаёт только то, что
    вкладка реально заполнила (пустая строка в схему не попадает).
    """
    payload = window._collect_data()

    assert payload["vehicles"] == []
    assert "lot_number" not in payload["zayavka"]
    assert "tractor_brand" not in payload["zayavka"]
    # Даты вкладок заполнены по умолчанию — они в схеме есть.
    assert payload["zayavka"]["date"]
    # Стороны заявки фиксированы и заполнены всегда — даже на пустой форме.
    assert payload["zayavka"]["customer_name"] == GENERATOR_CUSTOMER_NAME
    assert payload["zayavka"]["carrier_name"] == GENERATOR_CARRIER_NAME
    # Ставка НДС в пустой форме ключа не создаёт (решение B.2).
    assert "vat_rate" not in payload["zayavka"]


def test_full_form_passes_validator_without_errors(window):
    """Заполненная форма проходит валидатор типа чисто — диалог не помешает."""
    _fill_all_tabs(window)

    report = ZayavkaExcelValidator().check(window._collect_data())

    assert report.errors == [], f"неожиданные ошибки: {report.errors}"
    assert report.warnings == [], f"неожиданные замечания: {report.warnings}"
    assert report.is_clean is True


# ─────────────────────────────────────────────────────────────
# 4. Очистка: только своя вкладка
# ─────────────────────────────────────────────────────────────

def test_clear_tab_clears_only_sender_tab(window):
    _fill_all_tabs(window)

    window.driver_tab.btn_clear_form.click()

    assert window.driver_tab.get_data()["driver_last_name"] == ""
    assert window.zayavka_tab.get_data()["lot_number"] == LOT_NUMBER
    assert window.vehicle_tab.get_data()["tractor_plate"] == TRACTOR_PLATE
    assert window.price_tab.get_data()["price_with_vat"] == PRICE_WITH_VAT


def test_clear_slot_without_sender_is_safe(window):
    """Прямой вызов слота без отправителя ничего не чистит и не падает."""
    _fill_all_tabs(window)

    window._on_clear_tab()

    assert window.zayavka_tab.get_data()["lot_number"] == LOT_NUMBER
    assert window.driver_tab.get_data()["driver_last_name"] == DRIVER_LAST_NAME


def test_clear_marks_status_bar(window):
    """Очистка видна пользователю в статус-баре."""
    _fill_all_tabs(window)

    window.cargo_tab.btn_clear_form.click()

    assert window.statusBar().currentMessage() == "Вкладка очищена"


# ─────────────────────────────────────────────────────────────
# 5. Закрытие окна
# ─────────────────────────────────────────────────────────────

def test_close_hides_window_without_destroying_it(qt_app, quiet_messages):
    win = HavalyWindow()
    win.show()
    _fill_all_tabs(win)
    try:
        win.close()

        assert win.isVisible() is False
        assert win.zayavka_tab.get_data()["lot_number"] == LOT_NUMBER
    finally:
        win.force_close()


def test_force_close_really_closes_window(qt_app, quiet_messages):
    win = HavalyWindow()
    win.show()
    win.force_close()

    assert win.isVisible() is False
    assert win._force_close is True


# ─────────────────────────────────────────────────────────────
# 6. «Создать договор»: валидатор → диалог → генератор → XLSX → диалог успеха
# ─────────────────────────────────────────────────────────────

def test_empty_form_asks_for_confirmation_and_generates(
    window, hav_generator, output_dir, quiet_messages, monkeypatch
):
    """
    Пустая форма: валидатор даёт замечания, диалог показывается.

    У Хавалов это не ошибка: заявка — форма заказчика, и валидатор типа
    ничего не блокирует. Ответ «Создать заявку» ведёт к генерации файла.
    """
    dialogs = []
    monkeypatch.setattr(
        QMessageBox, "exec_",
        lambda self: dialogs.append(self.windowTitle()) or QMessageBox.Yes,
    )
    created = []
    monkeypatch.setattr(
        HavalyWindow, "_show_contract_created",
        lambda self, path: created.append(path),
    )

    window._on_create_contract()

    # Первый диалог — предупреждение валидатора (ошибок у типа нет).
    assert dialogs == ["Замечания к данным"]
    assert len(created) == 1
    name = os.path.basename(created[0])
    assert name.startswith("Заявка_Хавалы_") and name.endswith(".xlsx")


def test_genuinely_empty_form_gets_report_without_errors(window):
    """Отчёт по пустому окну: ошибок нет, замечания — есть."""
    report = ZayavkaExcelValidator().check(window._collect_data())

    assert report.errors == []
    assert report.has_errors is False
    assert report.warnings, "пустая форма должна давать замечания"
    assert "В заявке нет ни одной машины" in report.warnings


def test_create_contract_can_be_cancelled_in_dialog(
    window, hav_generator, output_dir, quiet_messages, monkeypatch
):
    """Пользователь нажал «Исправить» — генерации нет, файл не создан."""
    _fill_all_tabs(window)
    window.price_tab.clear()          # ставка с НДС не заполнена
    window.vehicle_tab.clear()        # автовоз и прицеп пусты

    report = ZayavkaExcelValidator().check(window._collect_data())
    assert report.is_clean is False
    assert "Не заполнена ставка с НДС" in report.warnings

    dialog_titles = []
    monkeypatch.setattr(
        QMessageBox, "exec_",
        lambda self: dialog_titles.append(self.windowTitle()) or QMessageBox.No,
    )

    window._on_create_contract()

    assert dialog_titles == ["Замечания к данным"]
    assert window.statusBar().currentMessage() == "Заявка не создана — исправьте данные"
    assert list(output_dir.glob("*.xlsx")) == []


def test_clean_data_skips_confirmation_dialog(
    window, hav_generator, quiet_messages, monkeypatch
):
    """
    Данные без замечаний: диалог проверки не показывается вовсе.

    Данные подставляются напрямую — так виден именно шаг проверки, а не то,
    что вкладки успели заполнить.
    """
    _fill_all_tabs(window)
    payload = window._collect_data()
    assert ZayavkaExcelValidator().check(payload).is_clean is True

    monkeypatch.setattr(HavalyWindow, "_collect_data", lambda self: payload)

    generated = []
    monkeypatch.setattr(
        HavalyWindow, "_show_contract_created",
        lambda self, path: generated.append(path),
    )
    monkeypatch.setattr(
        hav_generator, "generate",
        lambda data: generated.append(data) and "output/x.xlsx",
    )

    dialogs = []
    monkeypatch.setattr(
        QMessageBox, "exec_", lambda self: dialogs.append(self.windowTitle()) or 0
    )

    window._on_create_contract()

    assert dialogs == []
    assert generated[-1] is payload


def test_create_contract_generates_xlsx_end_to_end(
    window, hav_generator, output_dir, hav_template, quiet_messages, monkeypatch
):
    """
    Заполненная форма: генератор → XLSX в папке вывода → диалог успеха.

    Файл проверяется целиком: он создан, имя зависит от даты заявки, данные
    всех шести вкладок дошли до бланка, а исходный бланк не изменился.
    """
    _fill_all_tabs(window)

    created = []
    monkeypatch.setattr(
        HavalyWindow, "_show_contract_created",
        lambda self, path: created.append(path),
    )
    # Данных достаточно, поэтому диалог проверки не показывается; ответ на
    # всякий случай — «Создать заявку».
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: QMessageBox.Yes)

    before = hav_template.read_bytes()

    window._on_create_contract()

    assert len(created) == 1
    path = Path(created[0])
    try:
        assert path.exists() and path.suffix == ".xlsx"
        assert path.name == "Заявка_Хавалы_2001-01-01.xlsx"
        assert path.parent == output_dir
        # Исходный бланк не перезаписан: генератор пишет только в output.
        assert hav_template.read_bytes() == before

        payload = ZayavkaExcelGenerator(
            templates_dir=str(hav_template.parent)
        ).read_template(str(path))

        assert payload["vehicles"] == [dict(v) for v in VEHICLES]
        zayavka = payload["zayavka"]
        assert _same_date(zayavka["date"], DATE_ISO)
        assert zayavka["lot_number"] == LOT_NUMBER
        assert zayavka["tractor_brand"] == TRACTOR_BRAND
        assert zayavka["tractor_plate"] == TRACTOR_PLATE
        assert zayavka["trailer_plate"] == TRAILER_PLATE
        assert zayavka["driver_last_name"] == DRIVER_LAST_NAME
        assert zayavka["driver_passport_series"] == DRIVER_PASSPORT_SERIES
        assert zayavka["driver_passport_number"] == DRIVER_PASSPORT_NUMBER
        assert zayavka["loading_city"] == LOADING_CITY
        assert zayavka["unloading_point"] == UNLOADING_POINT
        assert zayavka["loading_plan_time"] == LOADING_PLAN_TIME
        assert zayavka["price_with_vat"] == PRICE_WITH_VAT
        assert _same_date(zayavka["loading_plan_date"], DATE_ISO)
        # Стороны в бланке напечатаны и читаются константами.
        assert zayavka["customer_name"] == GENERATOR_CUSTOMER_NAME
        assert zayavka["carrier_name"] == GENERATOR_CARRIER_NAME
    finally:
        path.unlink(missing_ok=True)


def test_create_contract_from_header_button_generates_file(
    window, hav_generator, output_dir, quiet_messages, monkeypatch
):
    """Кнопка шапки делает то же, что кнопка вкладки: файл появляется."""
    _fill_all_tabs(window)

    created = []
    monkeypatch.setattr(
        HavalyWindow, "_show_contract_created",
        lambda self, path: created.append(path),
    )
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: QMessageBox.Yes)

    window.btn_create_contract.click()

    try:
        assert len(created) == 1
        assert Path(created[0]).exists()
    finally:
        for path in created:
            Path(path).unlink(missing_ok=True)


def test_generator_failure_is_reported(window, monkeypatch, quiet_messages):
    """Падение генератора не роняет окно: показываем ошибку."""
    _fill_all_tabs(window)
    monkeypatch.setattr(
        GeneratorFactory, "get_generator",
        classmethod(lambda cls, *args, **kwargs: object()),
    )
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: QMessageBox.Yes)

    window._on_create_contract()

    assert any("Не удалось создать заявку" in text
               for text in quiet_messages["critical"])


def test_more_than_ten_cars_are_capped_by_form_and_reported(window, quiet_messages):
    """
    Машин больше десяти: форма оставляет десять, валидатор об этом говорит.

    Вкладка «Груз» не создаёт строк сверх MAX_CARS (в бланке их ровно 10) —
    лишние отбрасываются при заполнении. Валидатор типа предупреждает о
    том же, когда машин больше десяти приходят в данных (например, из
    присланного файла).
    """
    _fill_all_tabs(window)

    twelve = [
        {"vin": f"XTC651150N0001{number:03d}", "brand": "LADA", "model": "Vesta",
         "dealer": "Дилер", "dealer_code": f"D-{number:03d}"}
        for number in range(1, 13)
    ]
    window.cargo_tab.fill_data({"vehicles": twelve})

    payload = window._collect_data()
    assert len(payload["vehicles"]) == 10
    assert payload["vehicles"][-1]["vin"] == twelve[9]["vin"]

    # Валидатор на тех же данных, где машин действительно 12.
    report = ZayavkaExcelValidator().check({
        "zayavka": payload["zayavka"], "vehicles": twelve,
    })

    assert "Машин 12 — в бланк помещается 10, лишние в файл не попадут" in (
        report.warnings
    )


def test_show_contract_created_offers_open_folder(
    window, monkeypatch, quiet_messages, output_dir
):
    """Диалог успеха: «Заявка создана» и кнопки «Открыть папку» + «OK»."""
    path = output_dir / "Заявка_Хавалы_2001-01-01.xlsx"
    path.write_bytes(b"PK\x03\x04")

    buttons, titles = [], []
    origin_add_button = QMessageBox.addButton

    def spy_add_button(self, *args, **kwargs):
        button = origin_add_button(self, *args, **kwargs)
        buttons.append(button.text())
        return button

    def spy_exec(self):
        titles.append(self.text())
        return 0

    monkeypatch.setattr(QMessageBox, "addButton", spy_add_button)
    # Диалог не показываем: интересна его начинка, а не модальное окно.
    monkeypatch.setattr(QMessageBox, "exec_", spy_exec)

    opened = []
    monkeypatch.setattr(
        HavalyWindow, "_open_folder", lambda self, folder: opened.append(folder)
    )
    try:
        window._show_contract_created(str(path))

        assert buttons == ["Открыть папку", "OK"]
        assert titles == ["Заявка создана"]
        assert opened == []  # нажали «OK»: папку не открываем
    finally:
        path.unlink(missing_ok=True)


def test_show_contract_created_opens_folder_on_request(
    window, monkeypatch, quiet_messages, output_dir
):
    """Кнопка «Открыть папку» открывает папку готовой заявки."""
    path = output_dir / "Заявка_Хавалы_2001-01-01.xlsx"
    path.write_bytes(b"PK\x03\x04")

    opened = []
    monkeypatch.setattr(
        HavalyWindow, "_open_folder", lambda self, folder: opened.append(folder)
    )

    # Запоминаем кнопку «Открыть папку»: в диалоге она первая.
    open_button = None
    origin_add_button = QMessageBox.addButton

    def spy_add_button(self, *args, **kwargs):
        nonlocal open_button
        button = origin_add_button(self, *args, **kwargs)
        if button.text() == "Открыть папку":
            open_button = button
        return button

    monkeypatch.setattr(QMessageBox, "addButton", spy_add_button)

    def spy_exec(box):
        box._clicked = open_button   # нажата «Открыть папку»
        return 0

    monkeypatch.setattr(QMessageBox, "exec_", spy_exec)
    monkeypatch.setattr(
        QMessageBox, "clickedButton", lambda self: getattr(self, "_clicked", None)
    )

    try:
        window._show_contract_created(str(path))

        assert open_button is not None
        assert opened == [str(output_dir)]
    finally:
        path.unlink(missing_ok=True)


def test_open_folder_returns_false_for_missing_folder(window):
    """Несуществующая папка не открывается и не роняет окно."""
    assert window._open_folder("") is False
    assert window._open_folder(str(Path("такой-папки-нет-12345"))) is False


# ─────────────────────────────────────────────────────────────
# 7. Распознавание
# ─────────────────────────────────────────────────────────────

def test_recognize_request_starts_task_with_havaly_prompt(window, monkeypatch, qt_app):
    """Промпт типа передаётся в recognize_text вторым аргументом."""
    client = FakeClient(_recognized_answer())
    monkeypatch.setattr(HavalyWindow, "_ensure_gigachat", lambda self: client)

    prompt = get_prompt("zayavka_excel")
    assert prompt, "у типа «Хавалы» должен быть свой промпт"

    window._on_recognize_requested("текст заявки Хавалов")
    task = window.recognition_task
    assert isinstance(task, RecognitionTask)
    assert task.prompt == prompt

    # Ждём пул: задача уже выполняется в потоке. Сигнал из потока приходит
    # в поток интерфейса, поэтому очередь событий доводим до конца —
    # иначе слот не успеет сбросить состояние задачи.
    window.thread_pool.waitForDone(5000)
    qt_app.processEvents()

    assert len(client.calls) == 1
    assert client.calls[0]["text"] == "текст заявки Хавалов"
    assert client.calls[0]["prompt"] == prompt

    # Связь сигналов с partial(task=...) действительно сработала.
    assert window.recognition_task is None


def test_recognition_without_key_shows_message(window, monkeypatch, quiet_messages):
    """Нет ключа — показываем MISSING_KEY_MESSAGE, задачу не запускаем."""
    monkeypatch.setattr(HavalyWindow, "_ensure_gigachat", lambda self: None)

    window._on_recognize_requested("текст заявки Хавалов")

    assert window.recognition_task is None
    assert any("set_key.py" in text for text in quiet_messages["critical"])


def test_empty_text_is_not_sent_to_recognition(window, monkeypatch, quiet_messages):
    """Пустой текст на распознавание не уходит."""
    called = []
    monkeypatch.setattr(HavalyWindow, "_ensure_gigachat",
                        lambda self: called.append("client"))

    window._on_recognize_requested("   ")

    assert called == []
    assert quiet_messages["warning"]


def test_recognition_fills_all_six_tabs(window, quiet_messages):
    """
    Ответ модели раскладывается по вкладкам: у каждой — свои поля.

    Блок «zayavka» один на весь ответ, поэтому каждая вкладка получает
    ровно свой список полей: «Заявка» — дату и лот, «Маршрут» — места и
    план погрузки, «Водитель» — тринадцать полей, «ТС» — автовоз и прицеп,
    «Стоимость» — ставку с НДС и ставку НДС. Машины уходят в «Груз».
    """
    _answer_recognition(window, _recognized_answer())

    # Заявка: дата и номер лота; стороны на вкладке фиксированы.
    zayavka = window.zayavka_tab.get_data()
    assert zayavka["lot_number"] == LOT_NUMBER
    assert _same_date(zayavka["date"], DATE_ISO)
    assert zayavka["customer_name"] == GENERATOR_CUSTOMER_NAME
    assert zayavka["carrier_name"] == GENERATOR_CARRIER_NAME

    # Груз: таблица перерисована по ответу модели (вторая машина без VIN).
    assert window.cargo_tab.get_data()["vehicles"] == [dict(v) for v in VEHICLES]

    # Маршрут: города, пункты и план погрузки — своими ключами.
    route = window.route_tab.get_data()
    assert route["loading_city"] == LOADING_CITY
    assert route["loading_point"] == LOADING_POINT
    assert route["unloading_city"] == UNLOADING_CITY
    assert route["unloading_point"] == UNLOADING_POINT
    assert _same_date(route["loading_plan_date"], DATE_ISO)
    assert route["loading_plan_time"] == LOADING_PLAN_TIME

    # Водитель: тринадцать плоских полей блока заявки.
    driver = window.driver_tab.get_data()
    assert driver["driver_last_name"] == DRIVER_LAST_NAME
    assert driver["driver_first_name"] == DRIVER_FIRST_NAME
    assert driver["driver_middle_name"] == DRIVER_MIDDLE_NAME
    assert driver["driver_license_number"] == DRIVER_LICENSE_NUMBER
    assert _same_date(driver["driver_license_issue_date"], DRIVER_LICENSE_ISSUE_DATE)
    assert driver["driver_passport_series"] == DRIVER_PASSPORT_SERIES
    assert driver["driver_passport_number"] == DRIVER_PASSPORT_NUMBER
    assert driver["driver_passport_issuer"] == DRIVER_PASSPORT_ISSUER
    assert _same_date(driver["driver_passport_issue_date"], DRIVER_PASSPORT_ISSUE_DATE)
    assert driver["driver_citizenship"] == DRIVER_CITIZENSHIP
    assert _same_date(driver["driver_birth_date"], DRIVER_BIRTH_DATE)
    assert driver["driver_registration"] == DRIVER_REGISTRATION
    assert driver["driver_phone"] == DRIVER_PHONE

    # ТС: автовоз и прицеп плоскими ключами (блоков tractor/trailer нет).
    vehicle = window.vehicle_tab.get_data()
    assert vehicle["tractor_brand"] == TRACTOR_BRAND
    assert vehicle["tractor_color"] == TRACTOR_COLOR
    assert vehicle["tractor_plate"] == TRACTOR_PLATE
    assert vehicle["trailer_brand"] == TRAILER_BRAND
    assert vehicle["trailer_plate"] == TRAILER_PLATE

    # Стоимость: ставка с НДС и ставка НДС.
    price = window.price_tab.get_data()
    assert price["price_with_vat"] == PRICE_WITH_VAT
    assert price["vat_rate"] == VAT_RATE

    assert window.recognition_task is None
    assert window.statusBar().currentMessage() == "Готово"


def test_recognition_does_not_overwrite_manual_input(window, quiet_messages):
    """Пустые поля ответа не стирают то, что пользователь ввёл руками."""
    _fill_all_tabs(window)

    _answer_recognition(window, {
        # Модель вернула схему целиком, но с пустыми строками и нулями.
        "zayavka": {
            "date": "", "lot_number": "",
            "loading_city": "", "loading_point": "",
            "unloading_city": "", "unloading_point": "",
            "tractor_brand": "", "tractor_color": "", "tractor_plate": "",
            "trailer_brand": "", "trailer_plate": "",
            "driver_last_name": "", "driver_first_name": "",
            "driver_middle_name": "", "driver_license_number": "",
            "driver_license_issue_date": "", "driver_passport_series": "",
            "driver_passport_number": "", "driver_passport_issuer": "",
            "driver_passport_issue_date": "", "driver_citizenship": "",
            "driver_birth_date": "", "driver_registration": "",
            "driver_phone": "",
            "loading_plan_date": "", "loading_plan_time": "",
            "price_with_vat": 0.0, "vat_rate": "",
        },
        "vehicles": [{"vin": "", "brand": "", "model": "",
                      "dealer": "", "dealer_code": ""}],
    })

    assert window.zayavka_tab.get_data()["lot_number"] == LOT_NUMBER
    assert window.cargo_tab.get_data()["vehicles"] == [dict(v) for v in VEHICLES]
    assert window.route_tab.get_data()["loading_city"] == LOADING_CITY
    assert window.route_tab.get_data()["loading_point"] == LOADING_POINT
    assert window.driver_tab.get_data()["driver_last_name"] == DRIVER_LAST_NAME
    assert window.driver_tab.get_data()["driver_phone"] == DRIVER_PHONE
    assert window.vehicle_tab.get_data()["tractor_plate"] == TRACTOR_PLATE
    assert window.price_tab.get_data()["price_with_vat"] == PRICE_WITH_VAT
    assert window.price_tab.get_data()["vat_rate"] == VAT_RATE


def test_recognition_ignores_stale_and_unknown_sections(window, quiet_messages):
    """Разделы, которых у типа нет, раскладку не ломают."""
    _answer_recognition(window, {
        "zayavka": {"lot_number": LOT_NUMBER},
        "contract": {"number": "ТЛ-1"},
        "driver": {"full_name": "Чужой Водитель"},
        "чегo-то_ещё": {"вложено": True},
    })

    assert window.zayavka_tab.get_data()["lot_number"] == LOT_NUMBER
    # Блоков contract и driver в схеме Хавалов нет: вкладки их не читают.
    assert window.driver_tab.get_data()["driver_last_name"] == ""
    assert window.vehicle_tab.get_data()["tractor_brand"] == ""


def test_recognition_takes_vehicles_from_cargo_block(window, quiet_messages):
    """Машины принимаются и блоком «cargo» — так их отдаёт сборщик B.1."""
    _answer_recognition(window, {
        "zayavka": {"lot_number": LOT_NUMBER},
        "cargo": {"vehicles": [dict(VEHICLES[0])]},
    })

    assert window.cargo_tab.get_data()["vehicles"] == [dict(VEHICLES[0])]


def test_recognition_keeps_manual_table_when_answer_has_no_vehicles(
    window, quiet_messages
):
    """Пустой массив машин таблицу не очищает."""
    _fill_all_tabs(window)

    _answer_recognition(window, {"zayavka": {"lot_number": "ЛОТ-НОВЫЙ"},
                                 "vehicles": []})

    assert window.zayavka_tab.get_data()["lot_number"] == "ЛОТ-НОВЫЙ"
    assert window.cargo_tab.get_data()["vehicles"] == [dict(v) for v in VEHICLES]


def test_recognition_logs_have_no_personal_data(caplog, window, quiet_messages):
    """
    В лог распознавания не попадают данные заявки.

    Пишутся только имена разделов, имена полей и количества: ФИО, адреса,
    VIN, номера, телефоны и суммы в логе быть не должно.
    """
    import logging

    with caplog.at_level(logging.DEBUG):
        _answer_recognition(window, _recognized_answer())

    messages = "\n".join(record.getMessage() for record in caplog.records)

    for fragment in (
        DRIVER_LAST_NAME, DRIVER_PHONE, DRIVER_PASSPORT_NUMBER,
        VEHICLES[0]["vin"], TRACTOR_PLATE, LOADING_POINT, LOT_NUMBER,
        str(int(PRICE_WITH_VAT)),
    ):
        assert fragment not in messages, f"в логе есть «{fragment}»"

    # А служебные сведения о разделах и полях — есть.
    assert "разделы получены" in messages
    assert "поля заявки" in messages


def test_recognition_ui_action_log_has_no_personal_data(
    caplog, window, quiet_messages
):
    """Журнал действий окна тоже обходится без данных заявки."""
    import logging

    with caplog.at_level(logging.INFO, logger=WINDOW_LOGGER):
        _answer_recognition(window, _recognized_answer())

    messages = "\n".join(
        record.getMessage() for record in caplog.records
        if record.name == WINDOW_LOGGER
    )

    for fragment in (
        DRIVER_LAST_NAME, DRIVER_PHONE, VEHICLES[0]["vin"], LOT_NUMBER,
    ):
        assert fragment not in messages, f"в логе есть «{fragment}»"
    assert "ответ разложен по вкладкам" in messages


def test_recognition_error_is_reported(window, quiet_messages):
    """Ошибка распознавания: QMessageBox.critical и сброс состояния."""
    task = RecognitionTask(FakeClient(), "текст", prompt="промпт")
    window.recognition_task = task

    window._on_recognition_error("Модель недоступна", task=task)

    assert any("Модель недоступна" in text for text in quiet_messages["critical"])
    assert window.recognition_task is None


def test_error_slot_without_sender_is_safe(window, quiet_messages):
    """Ошибка без отправителя: сообщение показываем, состояние сбрасываем."""
    window._on_recognition_error("Модель недоступна")

    assert any("Модель недоступна" in text for text in quiet_messages["critical"])
    assert window.recognition_task is None


def test_result_of_stale_task_is_ignored(window, quiet_messages):
    """Результат устаревшей (отменённой) задачи к вкладкам не применяется."""
    _fill_all_tabs(window)
    stale = RecognitionTask(FakeClient(), "текст", prompt="промпт")

    window._on_recognition_finished(
        {"zayavka": {"lot_number": "ЧУЖОЙ-ЛОТ"}}, task=stale
    )

    assert window.zayavka_tab.get_data()["lot_number"] == LOT_NUMBER


def test_recognition_cancelled_task_does_not_fill_tabs(window, quiet_messages):
    """Отмена задачи освобождает интерфейс и не трогает вкладки."""
    task = RecognitionTask(FakeClient(), "текст", prompt="промпт")
    window.recognition_task = task

    window._on_recognition_cancelled(task=task)

    assert window.recognition_task is None
    assert window.statusBar().currentMessage() == "Готово"


def test_recognition_progress_is_shown_in_status_bar(window):
    """Ход распознавания виден в статус-баре."""
    window._on_recognition_progress(20, "Отправка в GigaChat")

    assert window.statusBar().currentMessage() == "Отправка в GigaChat (20%)"


# ─────────────────────────────────────────────────────────────
# 8. Задача распознавания (своя, а не из MainWindow)
# ─────────────────────────────────────────────────────────────

def test_recognition_task_passes_prompt_to_client():
    """Своя задача типа: промпт уходит в клиент вторым аргументом."""
    client = FakeClient({"zayavka": {"lot_number": LOT_NUMBER}})
    task = RecognitionTask(client, "исходный текст", prompt="свой промпт")

    received = []
    task.signals.finished.connect(received.append)
    task.run()

    assert received == [{"zayavka": {"lot_number": LOT_NUMBER}}]
    assert client.calls[0]["prompt"] == "свой промпт"


def test_recognition_task_reports_empty_answer():
    """None от клиента — сигнал ошибки, а не пустой результат."""
    class NoneClient(FakeClient):
        def recognize_text(self, text, prompt=None):
            return None

    task = RecognitionTask(NoneClient(), "текст", prompt="промпт")
    errors = []
    task.signals.error.connect(errors.append)
    task.run()

    assert errors == ["Модель вернула пустой ответ"]


def test_cancelled_recognition_task_does_not_apply_result():
    """Отменённая задача результат не отдаёт — интерфейс не тронут."""
    client = FakeClient({"zayavka": {"lot_number": LOT_NUMBER}})
    task = RecognitionTask(client, "исходный текст", prompt="свой промпт")
    task.cancel()

    finished, cancelled = [], []
    task.signals.finished.connect(finished.append)
    task.signals.cancelled.connect(lambda: cancelled.append(True))  # noqa: B023
    task.run()

    assert finished == []
    assert cancelled == [True]
    assert task.is_cancelled is True


# ─────────────────────────────────────────────────────────────
# 9. Раскладка блока «zayavka» по вкладкам (прямые проверки)
# ─────────────────────────────────────────────────────────────

def test_zayavka_tab_data_takes_own_fields_only():
    """«Заявке» уходят дата, номер лота и стороны — и ничего лишнего."""
    data = HavalyWindow._zayavka_tab_data(_zayavka_block())

    assert data == {
        "date": DATE_DOCUMENT,
        "lot_number": LOT_NUMBER,
        "customer_name": GENERATOR_CUSTOMER_NAME,
        "carrier_name": GENERATOR_CARRIER_NAME,
    }


def test_zayavka_tab_data_skips_empty_and_non_dict():
    """Пустой блок и не-словарь вкладку не трогают."""
    assert HavalyWindow._zayavka_tab_data({}) == {}
    assert HavalyWindow._zayavka_tab_data(None) == {}
    assert HavalyWindow._zayavka_tab_data("мусор") == {}
    assert HavalyWindow._zayavka_tab_data({"lot_number": "  "}) == {}


def test_route_tab_data_takes_route_fields():
    """«Маршруту» уходят шесть полей: города, пункты и план погрузки."""
    data = HavalyWindow._route_tab_data(_zayavka_block())

    assert data == {
        "loading_city": LOADING_CITY,
        "loading_point": LOADING_POINT,
        "unloading_city": UNLOADING_CITY,
        "unloading_point": UNLOADING_POINT,
        "loading_plan_date": DATE_DOCUMENT,
        "loading_plan_time": LOADING_PLAN_TIME,
    }


def test_driver_tab_data_takes_thirteen_flat_fields():
    """
    «Водителю» уходят тринадцать плоских полей — без блока driver.

    Фамилия, имя и отчество — разные поля (требование промпта), серия и
    номер паспорта — тоже: окно их не склеивает и не разбивает.
    """
    data = HavalyWindow._driver_tab_data(_zayavka_block())

    assert set(data) == set(HavalyWindow._DRIVER_FIELDS)
    assert len(data) == 13
    assert data["driver_last_name"] == DRIVER_LAST_NAME
    assert data["driver_first_name"] == DRIVER_FIRST_NAME
    assert data["driver_middle_name"] == DRIVER_MIDDLE_NAME
    assert data["driver_passport_series"] == DRIVER_PASSPORT_SERIES
    assert data["driver_passport_number"] == DRIVER_PASSPORT_NUMBER


def test_vehicle_tab_data_takes_five_unit_fields():
    """«ТС» уходят пять полей автовоза и прицепа — без блоков tractor/trailer."""
    data = HavalyWindow._vehicle_tab_data(_zayavka_block())

    assert data == {
        "tractor_brand": TRACTOR_BRAND,
        "tractor_color": TRACTOR_COLOR,
        "tractor_plate": TRACTOR_PLATE,
        "trailer_brand": TRAILER_BRAND,
        "trailer_plate": TRAILER_PLATE,
    }


def test_price_tab_data_keeps_rate_and_amount():
    """«Стоимости» уходят ставка с НДС и ставка НДС."""
    data = HavalyWindow._price_tab_data(_zayavka_block())

    assert data == {"price_with_vat": PRICE_WITH_VAT, "vat_rate": VAT_RATE}


def test_price_tab_data_keeps_zero_amount_but_drops_empty_rate():
    """
    Ноль в ставке — значение, пустая ставка — отсутствие ключа.

    У промпта price_with_vat = 0.0 значит «ставки в документе не было»:
    вкладка на ноль ничего не меняет, и ключ ей передать можно. А пустую
    ставку НДС передавать нельзя — вкладка не должна сбросить свой выбор.
    """
    data = HavalyWindow._price_tab_data(_zayavka_block(price_with_vat=0.0,
                                                       vat_rate=""))

    assert data == {"price_with_vat": 0.0}
    assert HavalyWindow._price_tab_data({}) == {}


def test_cargo_tab_data_takes_filled_rows_only():
    """Машины: пустые строки ответа таблицу не очищают."""
    rows = [dict(v) for v in VEHICLES]

    assert HavalyWindow._cargo_tab_data(rows) == {"vehicles": rows}
    assert HavalyWindow._cargo_tab_data({"vehicles": rows}) == {"vehicles": rows}
    assert HavalyWindow._cargo_tab_data(
        [{"vin": "", "brand": "", "model": "", "dealer": "", "dealer_code": ""}]
    ) == {}
    assert HavalyWindow._cargo_tab_data([]) == {}
    assert HavalyWindow._cargo_tab_data(None) == {}
    assert HavalyWindow._cargo_tab_data("мусор") == {}


def test_cargo_source_prefers_filled_vehicles():
    """Машины берутся из первого непустого источника: vehicles или cargo."""
    rows = [dict(VEHICLES[0])]

    assert HavalyWindow._cargo_source({"vehicles": rows}) == rows
    assert HavalyWindow._cargo_source({"cargo": {"vehicles": rows}}) == {
        "vehicles": rows
    }
    # Пустой верхний массив не должен затирать заполненный блок cargo.
    assert HavalyWindow._cargo_source({
        "vehicles": [], "cargo": {"vehicles": rows}
    }) == {"vehicles": rows}
    # Ни одного источника с машинами — вкладка «Груз» остаётся как есть.
    assert HavalyWindow._cargo_source({"vehicles": []}) == []


def test_tab_field_lists_cover_the_whole_prompt_schema():
    """
    Списки полей окна покрывают схему промпта Хавалов целиком.

    Схема — это ZAYAVKA_SCHEMA_ORDER генератора (он же порядок «СХЕМЫ ОТВЕТА»
    промпта): каждое её поле должно попадать на какую-то вкладку, иначе
    распознанное значение молча потеряется. Машины (VEHICLE_KEYS) уходят
    вкладке «Груз» отдельным списком.
    """
    from core.contracts.zayavka.generator import (  # noqa: E402
        VEHICLE_KEYS, ZAYAVKA_SCHEMA_ORDER,
    )

    covered = set(HavalyWindow._ZAYAVKA_FIELDS)
    covered |= set(HavalyWindow._ROUTE_FIELDS)
    covered |= set(HavalyWindow._DRIVER_FIELDS)
    covered |= set(HavalyWindow._VEHICLE_FIELDS)
    covered |= set(HavalyWindow._PRICE_FIELDS)

    assert covered == set(ZAYAVKA_SCHEMA_ORDER)
    assert set(HavalyWindow._PRICE_FIELDS) == {"price_with_vat", "vat_rate"}
    # Машины — не поля заявки: у них свой список и своя вкладка.
    assert set(VEHICLE_KEYS) == set(CargoTab.VEHICLE_FIELDS)
    assert not set(VEHICLE_KEYS) & covered
