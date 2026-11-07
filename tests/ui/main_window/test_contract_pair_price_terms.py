#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Связанные договоры: форма и НДС связанного договора (задача 3).

Проверяется стык диалога создания, окна-цели и вкладки стоимости:

  * по умолчанию форма и НДС берутся из текущего окна Экспедиторства —
    «ИП без НДС» в Экспедиторстве даёт «ИП» и «Без НДС» в диалоге;
  * вкладка, которая значения не отдала (заглушка, ошибка), не роняет
    диалог: остаются заводские «ООО» и «22%»;
  * выбранные форма и ставка уходят в окно-цель ОДНИМ вызовом
    `apply_price_amount` вместе с суммой;
  * `apply_price_amount` кладёт их в `fill_data` вкладки стоимости
    (`entity_type`, `vat_rate`, `price_with_vat`), а мусор и пустые
    значения не подставляет — прежний вызов с одной суммой работает как
    раньше.

Реальные бланки Логистикса и Формики (база без НДС, ветвь «НДС не
облагается») проверяются в приватной части проекта —
`tests/test_contract_pair_price_terms.py`.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtCore import QEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

import ui.main_window.contract as contract_module  # noqa: E402
from ui.main_window.contract import (  # noqa: E402
    ContractMixin, LinkedContractChoice,
)
from ui.windows.base_window import BaseContractWindow  # noqa: E402
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
# Заглушки: вкладка Экспедиторства, окно-цель, диалог, менеджер
# ─────────────────────────────────────────────────────────────

class FakeContractTab:
    """Вкладка «Договор» Экспедиторства: только форма и ставка НДС."""

    def __init__(self, entity_type="ООО", vat_rate="22%"):
        self._entity_type = entity_type
        self._vat_rate = vat_rate

    def current_entity_type(self):
        return self._entity_type

    def current_vat_rate(self):
        return self._vat_rate


class BrokenContractTab:
    """Вкладка, которая падает при чтении (чужое окно, заглушка)."""

    def current_entity_type(self):
        raise RuntimeError("вкладка сломана")

    def current_vat_rate(self):
        raise RuntimeError("вкладка сломана")


class FakeHost:
    """
    Хозяин методов `ContractMixin`: вкладка Экспедиторства и два метода.

    Методы миксина подключаются как атрибуты класса — они становятся
    связанными, и внутри `_ask_linked_contract` вызов `self.…` работает
    так же, как у MainWindow.
    """

    _linked_price_defaults = ContractMixin._linked_price_defaults
    _ask_linked_contract = ContractMixin._ask_linked_contract
    _create_linked_contract = ContractMixin._create_linked_contract

    def __init__(self, contract_tab=None, titles=None):
        self.contract_tab = contract_tab
        self.titles = list(titles or [("formika", "Формика")])
        self.logged = []

    def _linked_target_titles(self):
        return list(self.titles)

    def _log_ui_action(self, action, **details):
        self.logged.append((action, details))

    def _ask_overwrite_target(self, title):
        return True


class FakeDialog:
    """Диалог-заглушка: запоминает параметры и отдаёт готовый выбор."""

    instances = []

    def __init__(self, titles, parent=None, entity_type="", vat_rate=""):
        self.titles = list(titles)
        self.parent = parent
        self.default_entity = entity_type
        self.default_vat = vat_rate
        self.result_choice = LinkedContractChoice(
            target_type="formika", amount=150000.0,
            entity_type=entity_type, vat_rate=vat_rate,
        )
        self.exec_result = 1  # QDialog.Accepted
        FakeDialog.instances.append(self)

    def exec_(self):
        return self.exec_result

    def choice(self):
        return self.result_choice


class StubPlan:
    """План зеркала: одна вкладка, конфликтов нет."""

    conflicts = ()
    tabs = {"customer_tab": {"number": "ЦБ-1"}}

    def field_count(self):
        return 1


class StubTargetWindow:
    """Окно-цель: запоминает вызовы публичного API окна типа."""

    CONTRACT_TYPE = "formika"

    def __init__(self, output_dir):
        self.output_dir = str(output_dir)
        self.calls = []
        self.price_kwargs = None

    def mirror_conflicts(self, plan):
        return []

    def apply_mirror_plan(self, plan, overwrite_existing=True):
        self.calls.append(("mirror", bool(overwrite_existing)))

    def apply_price_amount(self, amount, entity_type="", vat_rate=""):
        self.price_kwargs = {
            "amount": float(amount),
            "entity_type": entity_type,
            "vat_rate": vat_rate,
        }
        self.calls.append(("amount", float(amount), entity_type, vat_rate))
        return True

    def generate_document(self, confirm=True, output_dir=None):
        self.calls.append(("generate", confirm))
        return os.path.join(self.output_dir, "Связанный документ.docx")


class FakeManager:
    """Менеджер окон: отдаёт список типов и готовое окно-цель."""

    def __init__(self, types, window):
        self._types = tuple(types)
        self._window = window

    def contract_types(self):
        return self._types

    def window_for(self, contract_type):
        return self._window if contract_type in self._types else None


class Recorder:
    """Подмена функции в тесте (вместо lambda)."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


# ─────────────────────────────────────────────────────────────
# Значения по умолчанию — из Экспедиторства
# ─────────────────────────────────────────────────────────────

def test_defaults_read_form_and_vat_from_expedition_tab(qt_app):
    """«ИП без НДС» в Экспедиторстве — «ИП» и «Без НДС» в диалоге."""
    host = FakeHost(FakeContractTab("ИП", "Без НДС"))

    assert host._linked_price_defaults() == ("ИП", "Без НДС")


def test_defaults_are_core_values_without_useful_tab(qt_app):
    """Вкладки нет — заводские значения ядра, а не пустые строки."""
    assert FakeHost(None)._linked_price_defaults() == ("ООО", "22%")


def test_defaults_survive_broken_tab(qt_app):
    """Исключение вкладки не роняет диалог: остаются значения ядра."""
    assert FakeHost(BrokenContractTab())._linked_price_defaults() == (
        "ООО", "22%"
    )


def test_defaults_normalize_junk_from_tab(qt_app):
    """Мусор из вкладки в бланк не попадает — берутся значения ядра."""
    assert FakeHost(FakeContractTab("АО", "20%"))._linked_price_defaults() == (
        "ООО", "22%"
    )


def test_ask_linked_contract_passes_defaults_to_dialog(
        qt_app, monkeypatch):
    """Диалог создаётся с формой и НДС текущего окна Экспедиторства."""
    FakeDialog.instances = []
    monkeypatch.setattr(contract_module, "LinkedContractDialog", FakeDialog)
    host = FakeHost(FakeContractTab("ИП", "Без НДС"))

    choice = host._ask_linked_contract()

    dialog = FakeDialog.instances[-1]
    assert dialog.default_entity == "ИП"
    assert dialog.default_vat == "Без НДС"
    assert choice.entity_type == "ИП"
    assert choice.vat_rate == "Без НДС"


def test_ask_linked_contract_cancel_returns_none(qt_app, monkeypatch):
    """«Отмена» в диалоге — ничего не создаём."""

    class RejectedDialog(FakeDialog):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.exec_result = 0  # QDialog.Rejected

    monkeypatch.setattr(contract_module, "LinkedContractDialog", RejectedDialog)
    host = FakeHost(FakeContractTab())

    assert host._ask_linked_contract() is None


# ─────────────────────────────────────────────────────────────
# Передача формы и НДС в окно-цель
# ─────────────────────────────────────────────────────────────

def test_create_linked_contract_passes_form_vat_and_amount(
        qt_app, monkeypatch, tmp_path):
    """В цель уходит всё одним вызовом `apply_price_amount`."""
    window = StubTargetWindow(tmp_path)
    manager = FakeManager(("formika",), window)
    register_window_manager(manager)
    monkeypatch.setattr(
        contract_module, "mirror_from_expedition", Recorder(result=StubPlan())
    )
    host = FakeHost()
    choice = LinkedContractChoice(
        target_type="formika", amount=212500.50,
        entity_type="ИП", vat_rate="Без НДС",
    )

    result = host._create_linked_contract(choice, source_window=object())

    assert result.is_created() is True
    assert window.price_kwargs == {
        "amount": pytest.approx(212500.50),
        "entity_type": "ИП",
        "vat_rate": "Без НДС",
    }
    assert ("mirror", True) in window.calls
    assert ("generate", True) in window.calls


def test_create_linked_contract_keeps_old_call_without_terms(
        qt_app, monkeypatch, tmp_path):
    """Выбор без формы и НДС (старые вызовы) передаёт пустые значения."""
    window = StubTargetWindow(tmp_path)
    manager = FakeManager(("formika",), window)
    register_window_manager(manager)
    monkeypatch.setattr(
        contract_module, "mirror_from_expedition", Recorder(result=StubPlan())
    )
    host = FakeHost()

    result = host._create_linked_contract(
        LinkedContractChoice("formika", 1000.0), source_window=object()
    )

    assert result.is_created() is True
    assert window.price_kwargs == {
        "amount": pytest.approx(1000.0), "entity_type": "", "vat_rate": "",
    }


# ─────────────────────────────────────────────────────────────
# apply_price_amount: данные доходят до вкладки стоимости
# ─────────────────────────────────────────────────────────────

class _PriceTab(QWidget):
    """Вкладка стоимости-заглушка: принимает fill_data, отдаёт данные."""

    def __init__(self):
        super().__init__()
        self._data = {}

    def get_data(self):
        return dict(self._data)

    def fill_data(self, data):
        self._data.update(data)


class _EmptyTab(QWidget):
    """Любая другая вкладка окна-заглушки."""

    def get_data(self):
        return {}

    def fill_data(self, data):
        return None


_TITLES = ("Заказчик", "Груз", "Маршрут", "Водитель", "ТС", "Стоимость")


class PriceProbeWindow(BaseContractWindow):
    """Окно-цель с настоящим `apply_price_amount` и заглушкой стоимости."""

    CONTRACT_TYPE = "formika"
    WINDOW_TITLE = "Тестовое окно-цель"
    TAB_CONFIGS = [(title, "contract.svg") for title in _TITLES]

    def _make_tab(self, title, icon_key):
        if self._TAB_KEYS[_TITLES.index(title)] == "price_tab":
            return _PriceTab()
        return _EmptyTab()


@pytest.fixture
def price_window(qt_app, monkeypatch):
    """Окно без модальных окон; закрывается после теста."""
    monkeypatch.setattr(QMessageBox, "information", staticmethod(
        lambda *args, **kwargs: None
    ))
    window = PriceProbeWindow()
    yield window
    window.force_close()
    app = QApplication.instance()
    if app is not None:
        app.sendPostedEvents(None, QEvent.DeferredDelete)


def test_apply_price_amount_puts_total_form_and_vat(price_window):
    assert price_window.apply_price_amount(
        150000.0, entity_type="ИП", vat_rate="Без НДС"
    ) is True

    price = price_window._tab_by_key("price_tab").get_data()
    assert price["price_with_vat"] == pytest.approx(150000.0)
    assert price["entity_type"] == "ИП"
    assert price["vat_rate"] == "Без НДС"


def test_apply_price_amount_normalizes_terms(price_window):
    """«ООО (с НДС)» и « 22 % » приводятся к пунктам ядра."""
    assert price_window.apply_price_amount(
        1000.0, entity_type="ООО (с НДС)", vat_rate=" 22 % "
    ) is True

    price = price_window._tab_by_key("price_tab").get_data()
    assert price["entity_type"] == "ООО"
    assert price["vat_rate"] == "22%"


def test_apply_price_amount_without_terms_keeps_old_payload(price_window):
    """Прежний вызов с одной суммой не добавляет лишних ключей."""
    assert price_window.apply_price_amount(150000.0) is True

    assert price_window._tab_by_key("price_tab").get_data() == {
        "price_with_vat": pytest.approx(150000.0),
    }


def test_apply_price_amount_ignores_unknown_terms(price_window):
    """Неизвестная форма и ставка не попадают во вкладку стоимости."""
    assert price_window.apply_price_amount(
        150000.0, entity_type="АО", vat_rate="20%"
    ) is True

    price = price_window._tab_by_key("price_tab").get_data()
    assert "entity_type" not in price
    assert "vat_rate" not in price
    assert price["price_with_vat"] == pytest.approx(150000.0)


@pytest.mark.parametrize("amount", [0, 0.0, -100, "", "abc", None])
def test_apply_price_amount_still_rejects_bad_amount(price_window, amount):
    assert price_window.apply_price_amount(
        amount, entity_type="ИП", vat_rate="Без НДС"
    ) is False
