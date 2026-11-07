#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Связанные договоры: «Создать договор» → Экспедиторство + целевой тип.

Проверяется поток кнопки без приватных окон: цель — заглушка, которая
повторяет публичный контракт `BaseContractWindow` (конфликты, раскладка
плана, сумма, генерация), а менеджер окон — подставной. Поэтому тесты идут
и без приватной части проекта (CI).

Что подтверждается:
  * создаются ДВА файла: договор Экспедиторства и связанный документ;
  * сумма из диалога доходит до цели без искажений (итог с НДС);
  * «Перезаписать?» спрашивается только когда зеркало изменит заполненные
    оператором поля; «Да» — перенос применяется, «Нет» — нет, но сумма
    подставляется и документ создаётся;
  * «Отмена» в диалоге не создаёт ничего;
  * без менеджера окон (режим ядра) диалог не показывается и работает
    прежний поток «только Экспедиторство»;
  * публичный API окна-цели: `apply_price_amount`, `mirror_conflicts`
    (заводские значения формы не считаются вводом оператора) и
    `generate_document`.
"""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtCore import QEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

import core.mirror as mirror_module  # noqa: E402
import ui.main_window.contract as contract_module  # noqa: E402
from ui.main_window.contract import (  # noqa: E402
    LinkedContractChoice, LinkedContractResult,
)
from ui.windows.base_window import (  # noqa: E402
    BaseContractWindow, clear_source_windows,
)
from ui.windows.manager import (  # noqa: E402
    clear_window_manager, register_window_manager,
)


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def clean_manager():
    """Реестр менеджера окон не должен переживать тест."""
    clear_window_manager()
    yield
    clear_window_manager()


# ─────────────────────────────────────────────────────────────
# Заглушки: план зеркала, окно-цель, менеджер, генератор
# ─────────────────────────────────────────────────────────────

class FakePlan:
    """План зеркала: конфликты и данные плана."""

    def __init__(self, conflicts=(), tabs=None):
        self.conflicts = list(conflicts)
        self.tabs = dict(tabs or {"customer_tab": {"number": "ЦБ-1"}})

    def field_count(self):
        return sum(len(fields) for fields in self.tabs.values())


class StubTargetWindow:
    """
    Заглушка приватного окна-цели: публичный контракт BaseContractWindow.

    Пишет свой «документ» в отдельную папку и запоминает всё, что с ним
    делали, — по этим вызовам видно, применилось ли зеркало и какая сумма
    ушла в окно.
    """

    CONTRACT_TYPE = "formika"

    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.calls = []
        self.amount = 0.0
        #: Форма и НДС, переданные в окно вместе с суммой (задача 3).
        self.entity_type = ""
        self.vat_rate = ""
        #: None — обычная генерация; "" — оператор отменил; путь — готовый.
        self.generate_result = None
        self.generate_confirmed = None

    def mirror_conflicts(self, plan):
        self.calls.append(("conflicts", list(plan.conflicts)))
        return list(plan.conflicts)

    def apply_mirror_plan(self, plan, overwrite_existing=True):
        self.calls.append(("mirror", bool(overwrite_existing)))

    def apply_price_amount(self, amount, entity_type="", vat_rate=""):
        self.calls.append(("amount", float(amount), entity_type, vat_rate))
        self.amount = float(amount)
        self.entity_type = entity_type
        self.vat_rate = vat_rate
        return True

    def generate_document(self, confirm=True, output_dir=None):
        self.calls.append(("generate", confirm))
        self.generate_confirmed = confirm
        if self.generate_result == "":
            return ""
        if self.generate_result:
            return self.generate_result
        folder = Path(output_dir or self.output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "Связанный документ.docx"
        path.write_text("stub", encoding="utf-8")
        return str(path)

    def call_names(self):
        return [name for name, *_rest in self.calls]


class FakeManager:
    """Менеджер окон: отдаёт типы и уже созданное окно-цель."""

    def __init__(self, types, window):
        self._types = tuple(types)
        self._window = window

    def contract_types(self):
        return self._types

    def window_for(self, contract_type):
        if contract_type in self._types:
            return self._window
        return None


class FakeExpeditionGenerator:
    """Генератор договора Экспедиторства: файл в своей папке."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.calls = 0

    def generate(self, data):
        self.calls += 1
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / "Договор Экспедиторства.docx"
        path.write_text("expedition", encoding="utf-8")
        return str(path)


class Recorder:
    """Запоминает вызовы (вместо лямбды в тестах)."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result

    @property
    def called(self):
        return bool(self.calls)


# ─────────────────────────────────────────────────────────────
# Окно Экспедиторства
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def window(qt_app, isolated_db, monkeypatch):
    """MainWindow без GigaChat и без блокирующих диалогов."""
    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(
        lambda *args, **kwargs: None
    ))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(
        lambda *args, **kwargs: None
    ))
    warnings = Recorder()
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(warnings))

    win = MainWindow()
    monkeypatch.setattr(win, "_confirm_validation", Recorder(result=True))

    def keep_data(data):
        """Согласование перевозчика не меняет данные в этих тестах."""
        return data

    monkeypatch.setattr(win, "_check_driver_carrier_match", keep_data)
    yield win

    try:
        win.system_theme_watcher.stop()
    except Exception:  # noqa: BLE001 — наблюдателя может не быть
        pass
    win.force_close()
    win.deleteLater()
    app = QApplication.instance()
    if app is not None:
        app.sendPostedEvents(None, QEvent.DeferredDelete)
    clear_source_windows()


def _choose(window, monkeypatch, target_type="", amount=0.0):
    """Оператор «уже выбрал» режим в диалоге."""
    choice = LinkedContractChoice(target_type=target_type, amount=amount)
    monkeypatch.setattr(window, "_ask_linked_contract", Recorder(result=choice))
    return choice


def _prepare(window, monkeypatch, tmp_path, plan=None, manager_types=None,
             mirror_calls=None):
    """
    Готовит поток: подставной менеджер, окно-цель, фейковый генератор.

    `mirror_from_expedition` и `supported_targets` подменяются на уровне
    модуля ui.main_window.contract — тест не зависит от приватной части.
    """
    stub = StubTargetWindow(tmp_path / "target")
    types = manager_types if manager_types is not None else ("perevozka", "formika")
    if "formika" in types:
        manager = FakeManager(types, stub)
        # Реестр держит ССЫЛКУ СЛАБУЮ: без живой ссылки менеджер соберётся
        # сборщиком сразу после регистрации.
        stub.manager = manager
        register_window_manager(manager)

    if plan is not None:
        def fake_mirror(target_window, target_type, main_window=None):
            if mirror_calls is not None:
                mirror_calls.append((target_window, target_type, main_window))
            return plan

        monkeypatch.setattr(contract_module, "mirror_from_expedition", fake_mirror)
        monkeypatch.setattr(
            contract_module, "supported_targets",
            Recorder(result=frozenset({"formika", "logistiks_rus"})),
        )

    generator = FakeExpeditionGenerator(tmp_path / "expedition")
    monkeypatch.setattr(window, "contract_generator", generator)
    return stub, generator


def _output_files(folder):
    return sorted(path.name for path in Path(folder).glob("*") if path.is_file())


# ─────────────────────────────────────────────────────────────
# Пара договоров: два файла и сумма
# ─────────────────────────────────────────────────────────────

def test_pair_creates_two_files_with_amount(window, monkeypatch, tmp_path):
    stub, generator = _prepare(window, monkeypatch, tmp_path, plan=FakePlan())
    _choose(window, monkeypatch, "formika", 150000.0)
    pair_shown = Recorder()
    single_shown = Recorder()
    monkeypatch.setattr(window, "_show_pair_created", pair_shown)
    monkeypatch.setattr(window, "_show_contract_created", single_shown)

    window._on_create_contract()

    assert generator.calls == 1
    assert _output_files(tmp_path / "expedition") == ["Договор Экспедиторства.docx"]
    assert _output_files(tmp_path / "target") == ["Связанный документ.docx"]
    assert stub.amount == pytest.approx(150000.0)
    assert ("mirror", True) in stub.calls
    assert pair_shown.called is True
    assert single_shown.called is False


def test_pair_amount_matches_the_one_entered(window, monkeypatch, tmp_path):
    """Сумма доходит до цели без искажений — включая копейки."""
    stub, _generator = _prepare(window, monkeypatch, tmp_path, plan=FakePlan())
    _choose(window, monkeypatch, "formika", 212500.50)
    monkeypatch.setattr(window, "_show_pair_created", Recorder())
    monkeypatch.setattr(window, "_show_contract_created", Recorder())

    window._on_create_contract()

    assert stub.amount == pytest.approx(212500.50)
    assert ("mirror", True) in stub.calls


def test_pair_uses_registered_target_window(window, monkeypatch, tmp_path):
    """Окно цели берётся из менеджера; тип и сумма — из выбора."""
    stub, _generator = _prepare(window, monkeypatch, tmp_path, plan=FakePlan())
    _choose(window, monkeypatch, "formika", 1000.0)
    monkeypatch.setattr(window, "_show_pair_created", Recorder())
    monkeypatch.setattr(window, "_show_contract_created", Recorder())

    window._on_create_contract()

    assert ("generate", True) in stub.calls
    assert stub.generate_confirmed is True


# ─────────────────────────────────────────────────────────────
# Вопрос «Перезаписать?»
# ─────────────────────────────────────────────────────────────

def test_no_conflicts_means_no_question(window, monkeypatch, tmp_path):
    """Заводские значения цели вопрос не вызывают."""
    stub, _generator = _prepare(window, monkeypatch, tmp_path, plan=FakePlan())

    def fail(_title):
        raise AssertionError("вопрос «Перезаписать?» не должен задаваться")

    monkeypatch.setattr(window, "_ask_overwrite_target", fail)
    _choose(window, monkeypatch, "formika", 1000.0)
    monkeypatch.setattr(window, "_show_pair_created", Recorder())
    monkeypatch.setattr(window, "_show_contract_created", Recorder())

    window._on_create_contract()

    assert ("mirror", True) in stub.calls


def test_conflicts_yes_applies_mirror(window, monkeypatch, tmp_path):
    plan = FakePlan(conflicts=[("driver_tab", "full_name", "Петров", "Иванов")])
    stub, _generator = _prepare(window, monkeypatch, tmp_path, plan=plan)
    asked = Recorder(result=True)
    monkeypatch.setattr(window, "_ask_overwrite_target", asked)
    _choose(window, monkeypatch, "formika", 1000.0)
    monkeypatch.setattr(window, "_show_pair_created", Recorder())
    monkeypatch.setattr(window, "_show_contract_created", Recorder())

    window._on_create_contract()

    assert asked.called is True
    assert ("mirror", True) in stub.calls


def test_conflicts_no_keeps_data_but_sets_amount_and_generates(
        window, monkeypatch, tmp_path):
    """«Нет» — зеркало не применяется, сумма и документ — да."""
    plan = FakePlan(conflicts=[("driver_tab", "full_name", "Петров", "Иванов")])
    stub, _generator = _prepare(window, monkeypatch, tmp_path, plan=plan)
    monkeypatch.setattr(window, "_ask_overwrite_target", Recorder(result=False))
    _choose(window, monkeypatch, "formika", 175000.0)
    monkeypatch.setattr(window, "_show_pair_created", Recorder())
    monkeypatch.setattr(window, "_show_contract_created", Recorder())

    window._on_create_contract()

    assert "mirror" not in stub.call_names()
    assert stub.amount == pytest.approx(175000.0)
    assert "generate" in stub.call_names()
    assert _output_files(tmp_path / "target") == ["Связанный документ.docx"]


# ─────────────────────────────────────────────────────────────
# Отмена и ветки без пары
# ─────────────────────────────────────────────────────────────

def test_cancel_creates_nothing(window, monkeypatch, tmp_path):
    _stub, generator = _prepare(window, monkeypatch, tmp_path)
    monkeypatch.setattr(
        window, "_ask_linked_contract", Recorder(result=None)
    )

    window._on_create_contract()

    assert generator.calls == 0
    assert not (tmp_path / "expedition").exists()
    assert not (tmp_path / "target").exists()


def test_only_expedition_creates_one_file(window, monkeypatch, tmp_path):
    stub, generator = _prepare(window, monkeypatch, tmp_path)
    _choose(window, monkeypatch)
    pair_shown = Recorder()
    single_shown = Recorder()
    monkeypatch.setattr(window, "_show_pair_created", pair_shown)
    monkeypatch.setattr(window, "_show_contract_created", single_shown)

    window._on_create_contract()

    assert generator.calls == 1
    assert _output_files(tmp_path / "expedition") == ["Договор Экспедиторства.docx"]
    assert stub.calls == []
    assert pair_shown.called is False
    assert single_shown.called is True


def test_without_manager_dialog_is_not_shown(window, monkeypatch, tmp_path):
    """Режим ядра: связанных типов нет — диалог не появляется."""
    generator = FakeExpeditionGenerator(tmp_path / "expedition")
    monkeypatch.setattr(window, "contract_generator", generator)

    def fail(*args, **kwargs):
        raise AssertionError("без менеджера диалог не должен создаваться")

    monkeypatch.setattr(contract_module, "LinkedContractDialog", fail)
    single_shown = Recorder()
    monkeypatch.setattr(window, "_show_contract_created", single_shown)

    window._on_create_contract()

    assert generator.calls == 1
    assert single_shown.called is True


def test_plan_missing_warns_and_creates_expedition(window, monkeypatch, tmp_path):
    stub, generator = _prepare(window, monkeypatch, tmp_path, plan=None)
    # supported_targets/менеджер есть, а зеркало не собралось (нет данных).
    monkeypatch.setattr(
        contract_module, "supported_targets", Recorder(result=frozenset({"formika"}))
    )
    monkeypatch.setattr(
        contract_module, "mirror_from_expedition", Recorder(result=None)
    )
    _choose(window, monkeypatch, "formika", 1000.0)
    single_shown = Recorder()
    pair_shown = Recorder()
    monkeypatch.setattr(window, "_show_contract_created", single_shown)
    monkeypatch.setattr(window, "_show_pair_created", pair_shown)

    window._on_create_contract()

    assert generator.calls == 1
    assert single_shown.called is True
    assert pair_shown.called is False
    assert stub.calls == []


def test_cancelled_target_generation_warns(window, monkeypatch, tmp_path):
    stub, generator = _prepare(window, monkeypatch, tmp_path, plan=FakePlan())
    stub.generate_result = ""
    _choose(window, monkeypatch, "formika", 1000.0)
    single_shown = Recorder()
    pair_shown = Recorder()
    monkeypatch.setattr(window, "_show_contract_created", single_shown)
    monkeypatch.setattr(window, "_show_pair_created", pair_shown)

    window._on_create_contract()

    assert generator.calls == 1
    assert single_shown.called is True
    assert pair_shown.called is False


# ─────────────────────────────────────────────────────────────
# Список типов для диалога
# ─────────────────────────────────────────────────────────────

def test_linked_target_titles_are_manager_and_mirror_intersection(
        window, monkeypatch):
    stub = StubTargetWindow("unused")
    manager = FakeManager(("perevozka", "formika", "arenda_ts"), stub)
    stub.manager = manager  # сильная ссылка: реестр держит слабую
    register_window_manager(manager)
    monkeypatch.setattr(
        contract_module, "supported_targets",
        Recorder(result=frozenset({"formika", "logistiks_rus"})),
    )

    titles = window._linked_target_titles()

    assert [key for key, _title in titles] == ["formika"]
    assert titles[0][1]


def test_linked_target_titles_empty_without_manager(window):
    assert window._linked_target_titles() == []


# ─────────────────────────────────────────────────────────────
# Форма и НДС по умолчанию — из Экспедиторства (задача 3)
# ─────────────────────────────────────────────────────────────

def test_linked_defaults_come_from_real_expedition_tab(window):
    """
    Живая вкладка «Договор» Экспедиторства отдаёт форму и НДС.

    Это те самые значения, которые диалог создания пары показывает по
    умолчанию: «ИП без НДС» в Экспедиторстве не должен превращаться в
    «ООО, 22%» в связанном договоре.
    """
    assert window._linked_price_defaults() == ("ООО", "22%")

    window.contract_tab.entity_type.setCurrentText("ИП")
    window.contract_tab.vat_rate.setCurrentText("Без НДС")

    assert window._linked_price_defaults() == ("ИП", "Без НДС")


# ─────────────────────────────────────────────────────────────
# Источник данных (нужен приватному тесту и проверяется здесь)
# ─────────────────────────────────────────────────────────────

def test_create_linked_contract_uses_given_source(window, monkeypatch, tmp_path):
    stub, _generator = _prepare(window, monkeypatch, tmp_path, plan=FakePlan())
    mirror_calls = []
    monkeypatch.setattr(
        contract_module, "mirror_from_expedition",
        lambda target, target_type, main_window=None: mirror_calls.append(
            (target, target_type, main_window)
        ) or FakePlan(),
    )
    monkeypatch.setattr(
        contract_module, "supported_targets", Recorder(result=frozenset({"formika"}))
    )
    source = object()

    result = window._create_linked_contract(
        LinkedContractChoice("formika", 1000.0), source_window=source
    )

    assert result.is_created() is True
    assert mirror_calls and mirror_calls[0][2] is source


def test_create_linked_contract_without_manager_returns_reason(
        window, monkeypatch):
    result = window._create_linked_contract(
        LinkedContractChoice("formika", 1000.0)
    )

    assert isinstance(result, LinkedContractResult)
    assert result.is_created() is False
    assert "менеджер" in result.reason


# ─────────────────────────────────────────────────────────────
# Публичный API окна-цели
# ─────────────────────────────────────────────────────────────

class _StubTab(QWidget):
    """Вкладка-заглушка: хранит данные и принимает fill_data."""

    def __init__(self, data=None):
        super().__init__()
        self._data = dict(data or {})

    def get_data(self):
        return dict(self._data)

    def fill_data(self, data):
        self._data.update(data)


_TITLES = ("Заказчик", "Груз", "Маршрут", "Водитель", "ТС", "Стоимость")


class ProbeWindow(BaseContractWindow):
    """Подкласс каркаса с шестью вкладками-заглушками."""

    CONTRACT_TYPE = "formika"
    WINDOW_TITLE = "Тестовое окно-цель"
    TAB_CONFIGS = [(title, "contract.svg") for title in _TITLES]

    #: Заводские значения формы: «сегодняшняя дата» и пустой номер.
    DEFAULTS = {"customer_tab": {"number": "", "date": "2026-11-05"}}

    def _make_tab(self, title, icon_key):
        key = self._TAB_KEYS[_TITLES.index(title)]
        return _StubTab(self.DEFAULTS.get(key))


@pytest.fixture
def probe(qt_app, messages_off):
    win = ProbeWindow()
    yield win
    win.force_close()


@pytest.fixture
def messages_off(monkeypatch):
    """Модальные окна каркаса в offscreen не показываем."""
    monkeypatch.setattr(QMessageBox, "information", staticmethod(
        lambda *args, **kwargs: None
    ))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(
        lambda *args, **kwargs: None
    ))


def test_apply_price_amount_puts_total(probe):
    assert probe.apply_price_amount(150000.0) is True

    price = probe._tab_by_key("price_tab").get_data()
    assert price["price_with_vat"] == pytest.approx(150000.0)


def test_apply_price_amount_accepts_text_with_spaces(probe):
    assert probe.apply_price_amount("212 500,50") is True

    assert probe._tab_by_key("price_tab").get_data()["price_with_vat"] == (
        pytest.approx(212500.50)
    )


@pytest.mark.parametrize("amount", [0, 0.0, -100, "", "abc", None])
def test_apply_price_amount_rejects_bad_values(probe, amount):
    assert probe.apply_price_amount(amount) is False


def test_mirror_conflicts_ignore_factory_defaults(probe, monkeypatch):
    """Дата из формы и имя заказчика — не ввод оператора."""
    monkeypatch.setattr(
        mirror_module, "target_profiles",
        Recorder(result={"formika": {"customer_tab": {}}}),
    )
    plan = FakePlan(tabs={
        "customer_tab": {"number": "ЦБ-1", "date": "2026-09-23"},
    })

    assert probe.mirror_conflicts(plan) == []


def test_mirror_conflicts_see_operator_input(probe, monkeypatch):
    monkeypatch.setattr(
        mirror_module, "target_profiles",
        Recorder(result={"formika": {"customer_tab": {}}}),
    )
    probe._tab_by_key("customer_tab").fill_data({"number": "ФМ-7"})
    plan = FakePlan(tabs={
        "customer_tab": {"number": "ЦБ-1", "date": "2026-09-23"},
    })

    conflicts = probe.mirror_conflicts(plan)

    assert [(tab, name) for tab, name, _old, _new in conflicts] == [
        ("customer_tab", "number"),
    ]


def test_generate_document_without_collector_returns_none(probe):
    """У окна без сбора данных генерация не выдумывается."""
    assert probe.generate_document(confirm=False) is None


# ─────────────────────────────────────────────────────────────
# Сообщение «Оба договора созданы»
# ─────────────────────────────────────────────────────────────

class _FakeButton:
    def __init__(self, label):
        self.label = label


class FakeMessageBox:
    """Минимальная замена QMessageBox для проверки текста и кнопок."""

    Information = 1
    Question = 2
    ActionRole = 3
    AcceptRole = 4

    instances = []
    #: Индекс кнопки, которую «нажимает» оператор в `exec_` (None — никакую).
    auto_click = None

    def __init__(self, parent=None):
        self.title = ""
        self.text = ""
        self.info = ""
        self.buttons = []
        self.clicked = None
        FakeMessageBox.instances.append(self)

    def setWindowTitle(self, text):
        self.title = text

    def setIcon(self, icon):
        self.icon = icon

    def setText(self, text):
        self.text = text

    def setInformativeText(self, text):
        self.info = text

    def addButton(self, label, role):
        button = _FakeButton(label)
        self.buttons.append(button)
        return button

    def exec_(self):
        if FakeMessageBox.auto_click is not None:
            self.clicked = self.buttons[FakeMessageBox.auto_click]
        return 0

    def clickedButton(self):
        return self.clicked


def test_pair_message_says_both_created_and_opens_folder(window, monkeypatch,
                                                          tmp_path):
    FakeMessageBox.instances = []
    FakeMessageBox.auto_click = None
    monkeypatch.setattr(contract_module, "QMessageBox", FakeMessageBox)
    opened = Recorder(result=True)
    monkeypatch.setattr(window, "_open_folder", opened)
    first = tmp_path / "expedition" / "Договор Экспедиторства.docx"
    second = tmp_path / "expedition" / "Связанный документ.docx"
    first.parent.mkdir(parents=True)
    first.write_text("1", encoding="utf-8")
    second.write_text("2", encoding="utf-8")

    window._show_pair_created(str(first), str(second))

    box = FakeMessageBox.instances[-1]
    assert box.text == "Оба договора созданы"
    assert [button.label for button in box.buttons] == ["Открыть папку", "OK"]
    assert "Договор Экспедиторства.docx" in box.info
    assert "Связанный документ.docx" in box.info
    assert opened.called is False

    FakeMessageBox.auto_click = 0
    window._show_pair_created(str(first), str(second))

    assert opened.called is True
    assert opened.calls[-1][0][0] == str(first.parent)


def test_pair_message_mentions_other_folder(window, monkeypatch, tmp_path):
    FakeMessageBox.instances = []
    FakeMessageBox.auto_click = None
    monkeypatch.setattr(contract_module, "QMessageBox", FakeMessageBox)
    first = tmp_path / "a" / "Экспедиторство.docx"
    second = tmp_path / "b" / "Связанный.docx"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("1", encoding="utf-8")
    second.write_text("2", encoding="utf-8")

    window._show_pair_created(str(first), str(second))

    box = FakeMessageBox.instances[-1]
    assert f"Второй документ: {second.parent}" in box.info
