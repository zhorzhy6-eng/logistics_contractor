#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты восьми вкладок «Разовой аренды» (ЭТАП 3.1.D.B.2, дополнены на FIX-3).

Проверяется то, на что опирается сборка данных
(ui/windows/arenda_ts/data.py::collect_arenda_ts_data): набор ключей
get_data(), заполнение fill_data(), очистка clear(), сигналы вкладки и
панель действий. Отдельно — особенности этого типа договора:

  * Арендатор: вид стороны (ООО / ИП с НДС / ИП без НДС) убирает КПП и сам
    переключает основание полномочий;
  * Арендодатель: те же реквизиты без вида и без КПП (плейсхолдера в бланке
    нет), кнопки «🔎» у ИНН и БИК;
  * ТС: договор, срок аренды, тягач с типом ТС и прицеп;
  * Маршрут: две таблицы по 10 точек, у погрузки — дата и время подачи ТС;
  * Груз: до 12 машин с VIN и точками в строке, счётчик cargo_count;
  * Экипаж: ровно девять полей, паспорт и ВУ — одной строкой;
  * Стоимость: пересчёт НДС по ставке и сумма прописью;
  * Акт (Приложение № 1, шаг FIX-3): десять полей приёма-передачи и
    возврата ТС — до этого шага бланк печатал там пустые ячейки.

Последний раздел собирает ContractData из настоящих вкладок и проверяет, что
валидатор типа не находит в них ни ошибок, ни замечаний, а генератор получает
из вкладок те же данные, что и из словарей.

Qt — в offscreen-режиме. Модальные диалоги подменяются: ни один тест
не должен останавливаться на QMessageBox.
"""

import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtCore import QDate  # noqa: E402
from PyQt5.QtGui import QFontMetrics  # noqa: E402
from PyQt5.QtTest import QSignalSpy  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QDoubleSpinBox, QFrame, QGroupBox, QHeaderView,
    QLineEdit, QMessageBox, QPushButton, QSizePolicy, QSpinBox, QTableWidget,
    QTableWidgetItem, QWidget,
)

from core.contract_data import ContractData  # noqa: E402
from core.contracts.arenda_ts.validator import ArendaTsValidator  # noqa: E402
from ui.tabs.base_tab import TabMixin  # noqa: E402
from ui.widgets import (  # noqa: E402
    PasteableLineEdit, PasteableTextEdit, RecognitionPanel,
)
from ui.widgets.table_helpers import ROW_HEIGHT_TWO_LINES  # noqa: E402
from ui.windows.arenda_ts import data as data_module  # noqa: E402
from ui.windows.arenda_ts.data import build  # noqa: E402
from ui.windows.arenda_ts.tabs import (  # noqa: E402
    ActTab, CargoTab, CrewTab, LesseeTab, LessorTab, PriceTab, RouteTab,
    VehicleTab,
)
from ui.windows.arenda_ts.tabs import act_tab as act_tab_module  # noqa: E402
from ui.windows.arenda_ts.tabs import cargo_tab as cargo_tab_module  # noqa: E402
from ui.windows.arenda_ts.tabs import crew_tab as crew_tab_module  # noqa: E402
from ui.windows.arenda_ts.tabs import lessee_tab as lessee_tab_module  # noqa: E402
from ui.windows.arenda_ts.tabs import price_tab as price_tab_module  # noqa: E402
from ui.windows.arenda_ts.tabs import route_tab as route_tab_module  # noqa: E402
from ui.windows.arenda_ts.window import ArendaTsWindow  # noqa: E402

# ─────────────────────────────────────────────────────────────
# Данные тестов
# ─────────────────────────────────────────────────────────────

#: VIN из conftest (17 символов, ISO 3779) — на нём данных хватает валидатору.
VIN_1 = "EC3TEUMB0T0002608"
VIN_2 = "XTC651150N0001001"

#: Суммы ООО-варианта: 221 099,18 + 22% = 48 641,82 → 269 741,00.
SUM_WITHOUT_VAT = 221099.18
SUM_VAT = 48641.82
SUM_TOTAL = 269741.00

#: Одна сумма ИП без НДС: НДС не облагается.
IP_SUM = 135833.00

#: Все вкладки окна: (имя для отчёта, класс).
TAB_FACTORIES = [
    ("lessee", LesseeTab),
    ("lessor", LessorTab),
    ("vehicle", VehicleTab),
    ("route", RouteTab),
    ("cargo", CargoTab),
    ("crew", CrewTab),
    ("price", PriceTab),
    ("act", ActTab),
]

#: Ключи get_data() каждой вкладки — ровно те, что читает сборка данных.
#: У Арендатора ключ phone появляется только заполненным (телефон приходит
#: из справочника организаций, поля формы у вкладки нет — ШАГ FIX-1-T2).
EXPECTED_KEYS = {
    LesseeTab: {
        "carrier_type", "full_name", "short_name", "inn", "kpp", "ogrn",
        "address", "actual_address", "account", "bik", "bank", "corr_account",
        "email", "edo", "director_position", "director_name", "basis",
    },
    LessorTab: {
        "full_name", "short_name", "inn", "ogrn", "address", "actual_address",
        "account", "bik", "bank", "corr_account", "email", "edo",
        "director_position", "director_name", "basis",
    },
    VehicleTab: {
        "contract_number", "contract_date", "lease_start_date",
        "lease_end_date", "planned_completion_date",
        "tractor_brand", "tractor_plate", "tractor_type",
        "trailer_brand", "trailer_plate",
    },
    RouteTab: {"route", "loadings", "unloadings"},
    CargoTab: {"cargo_count", "vehicles"},
    CrewTab: {
        "driver_full_name", "driver_birth_date", "driver_passport",
        "driver_passport_issuer", "driver_passport_issue_date",
        "driver_license", "driver_license_issue_date",
        "driver_registration_address", "driver_phone",
    },
    PriceTab: {
        "sum_wo_vat", "sum_vat", "sum_total", "vat_rate", "vat_rate_num",
        "payment_days", "special_conditions",
    },
    # Акт (Приложение № 1, шаг FIX-3): десять полей передачи и возврата.
    ActTab: set(act_tab_module.ACT_FIELDS),
}

#: Образец заполнения для каждой вкладки: то, что мог бы дать распознаватель.
SAMPLE_DATA = {
    LesseeTab: {
        "carrier_type": "ООО",
        "full_name": "ООО «Логистик-Транс»",
        "short_name": "ООО «Логистик-Транс»",
        "inn": "7707654321",
        "kpp": "770701001",
        "ogrn": "1027700261234",
        "address": "г. Москва, ул. Складская, д. 5",
        "actual_address": "г. Москва, ул. Складская, д. 5",
        "account": "40702810000000000002",
        "bik": "044525225",
        "bank": "ПАО Сбербанк",
        "corr_account": "30101810400000000225",
        "email": "arenda@example.ru",
        "edo": "2AE-7F31-4C50",
        "director_position": "Генеральный директор",
        "director_name": "Сидоров Сидор Сидорович",
    },
    LessorTab: {
        "full_name": "ООО «ЛЦ»",
        "short_name": "ООО «ЛЦ»",
        "inn": "7701234567",
        "ogrn": "1027700132195",
        "address": "г. Москва, ул. Тестовая, д. 1",
        "actual_address": "г. Москва, ул. Тестовая, д. 1",
        "account": "40702810000000000003",
        "bik": "044525226",
        "bank": "АО «Банк Второй»",
        "corr_account": "30101810400000000226",
        "email": "lessor@example.ru",
        "edo": "3CD-8A42-5D60",
        "director_position": "Директор",
        "director_name": "Петров Пётр Петрович",
    },
    VehicleTab: {
        "contract_number": "01/2026",
        "contract_date": "2026-09-23",
        "lease_start_date": "2026-09-24",
        "lease_end_date": "2027-09-24",
        # Плановая дата завершения рейса (п. 3.3.2) — НЕ окончание аренды:
        # рейс завершается раньше, чем истекает срок аренды.
        "planned_completion_date": "2026-09-26",
        "tractor_brand": "DAF XF 95.430",
        "tractor_plate": "М342СА761",
        "tractor_type": "Седельный тягач",
        "trailer_brand": "KRONE SD",
        "trailer_plate": "ВК123478",
    },
    RouteTab: {
        "route": "Москва - Казань",
        # Дата в таблице вводится строкой, в данные уходит ISO: в образце
        # стоит то, что вернёт get_data().
        "loadings": [
            {"name": "ООО «Склад Север»", "address": "г. Москва, ул. Складская, д. 1",
             "date": "2026-09-26", "time_from": "08:00", "time_to": "18:00"},
        ],
        "unloadings": [
            {"name": "ООО «Приёмка»", "address": "г. Казань, ул. Приёмная, д. 3",
             "date": "2026-09-27"},
        ],
    },
    CargoTab: {
        "vehicles": [
            {"brand_model": "JETOUR T2", "vin": VIN_1,
             "loading_point": "Москва", "unloading_point": "Казань"},
        ],
    },
    CrewTab: {
        "driver_full_name": "Иванов Иван Иванович",
        "driver_birth_date": "1980-01-01",
        "driver_passport": "18 22 926830",
        "driver_passport_issuer": "Отделом УФМС России по г. Москве",
        "driver_passport_issue_date": "2023-01-30",
        "driver_license": "99 36 123456",
        "driver_license_issue_date": "2020-01-01",
        "driver_registration_address": "г. Москва, ул. Тестовая, д. 1",
        "driver_phone": "+7 (999) 123-45-67",
    },
    PriceTab: {
        "sum_wo_vat": SUM_WITHOUT_VAT,
        "vat_rate": "22%",
        "payment_days": 45,
        "special_conditions": "Простой не более 24 часов",
    },
    # Акт: значения приходят только из формы — распознавание их не извлекает
    # (core/prompts/arenda_ts.py), поэтому образец задаёт их вручную.
    ActTab: {
        "transfer_place": "г. Москва, ул. Передающая, д. 1",
        "transfer_datetime": "21.09.2026 08:30",
        "transfer_mileage": "125 400 км",
        "transfer_condition": "Без замечаний",
        "transfer_documents": "СТС; ОСАГО; иные: доверенность № 5",
        "return_place": "г. Калуга, ул. Возвратная, д. 2",
        "return_datetime": "27.09.2026 19:00",
        "return_mileage": "128 130 км",
        "return_condition": "Царапина на левом борту",
        "return_notes": "Акт подписан без разногласий",
    },
}

#: Поля-значения по умолчанию: их clear() не обнуляет, а возвращает к норме.
#: Даты показывают сегодняшний день (окончание аренды — с запасом в год,
#: плановое завершение рейса — через несколько дней), вид Арендатора — ООО,
#: ставка НДС — 22%, режим ввода суммы — «Без НДС», срок оплаты — 30 дней,
#: основание полномочий — устав.
DEFAULT_VALUE_KEYS = {
    "carrier_type", "basis", "vat_rate", "vat_rate_num", "payment_days",
    "contract_date", "lease_start_date", "lease_end_date",
    "planned_completion_date",
    "driver_birth_date", "driver_passport_issue_date", "driver_license_issue_date",
}


# ─────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(params=TAB_FACTORIES, ids=[name for name, _ in TAB_FACTORIES])
def tab(qt_app, request):
    """Свежая вкладка каждого типа."""
    _, factory = request.param
    widget = factory()
    yield widget
    widget.deleteLater()


@pytest.fixture
def quiet_dialogs(monkeypatch):
    """Ни один тест не должен останавливаться на модальном диалоге."""
    for name in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *args, **kwargs: QMessageBox.Ok),
        )


@pytest.fixture
def filled_tabs(qt_app):
    """Все восемь вкладок окна, заполненные как в жизни."""
    widgets = {
        key: factory() for key, factory in TAB_FACTORIES
    }
    for widget in widgets.values():
        widget.fill_data(SAMPLE_DATA[type(widget)])

    yield widgets

    for widget in widgets.values():
        widget.deleteLater()


# ─────────────────────────────────────────────────────────────
# Вспомогательное
# ─────────────────────────────────────────────────────────────

def _build_from(widgets: dict) -> ContractData:
    """Собирает ContractData из восьми вкладок в порядке разделов."""
    return build(
        widgets["lessee"], widgets["lessor"], widgets["vehicle"],
        widgets["route"], widgets["cargo"], widgets["crew"], widgets["price"],
        widgets.get("act"),
    )


def _without_default_keys(data: dict) -> dict:
    """
    Данные вкладки без полей со значением по умолчанию.

    Такие поля (даты, вид стороны, ставка НДС, основание) не обнуляются, а
    возвращаются к норме — у только что созданной вкладки и у очищенной они
    совпадают не всегда: основание Арендодателя подставляет clear(), а
    у свежей вкладки поле пустое.
    """
    return {
        key: value for key, value in data.items()
        if key not in DEFAULT_VALUE_KEYS
    }


def _headers(table: QTableWidget) -> list:
    """Заголовки колонок таблицы."""
    return [
        table.horizontalHeaderItem(i).text()
        for i in range(table.columnCount())
    ]


# ─────────────────────────────────────────────────────────────
# Общее для всех вкладок
# ─────────────────────────────────────────────────────────────

def test_tab_is_created_without_arguments(qt_app):
    for _, factory in TAB_FACTORIES:
        widget = factory()
        try:
            assert isinstance(widget, QWidget)
        finally:
            widget.deleteLater()


def test_tab_is_qwidget_with_tab_mixin(tab):
    assert isinstance(tab, QWidget)
    assert isinstance(tab, TabMixin)


def test_tab_has_required_api(tab):
    for name in ("get_data", "fill_data", "clear"):
        assert callable(getattr(tab, name)), name


def test_tab_declares_three_signals(tab):
    for name in ("recognize_requested", "create_contract_requested", "clear_requested"):
        assert hasattr(tab, name), name


def test_get_data_returns_dict(tab):
    data = tab.get_data()

    assert isinstance(data, dict)
    assert data  # у каждой вкладки есть хотя бы одно поле


def test_get_data_returns_expected_keys(tab):
    assert set(tab.get_data()) == EXPECTED_KEYS[type(tab)]


def test_fill_data_fills_fields(tab):
    """fill_data(sample) заполняет вкладку значениями образца."""
    sample = SAMPLE_DATA[type(tab)]
    before = tab.get_data()

    tab.fill_data(sample)
    after = tab.get_data()

    assert after != before, f"{type(tab).__name__}: fill_data ничего не изменил"
    for key, value in sample.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            assert after[key] == pytest.approx(value), key
        else:
            assert after[key] == value, key


def test_fill_data_with_empty_dict_changes_nothing(tab):
    tab.fill_data(SAMPLE_DATA[type(tab)])
    before = tab.get_data()

    tab.fill_data({})

    assert tab.get_data() == before


def test_clear_returns_fresh_state(qt_app, tab):
    """После clear() вкладка выглядит как только что созданная."""
    tab.fill_data(SAMPLE_DATA[type(tab)])
    tab.clear()

    fresh = type(tab)()
    try:
        assert _without_default_keys(tab.get_data()) == (
            _without_default_keys(fresh.get_data())
        )
    finally:
        fresh.deleteLater()


def test_clear_empties_text_fields(tab):
    """Текстовые поля вкладки после clear() пусты (кроме значений по умолчанию)."""
    tab.fill_data(SAMPLE_DATA[type(tab)])
    tab.clear()

    for key, value in tab.get_data().items():
        if key in DEFAULT_VALUE_KEYS:
            continue
        if isinstance(value, str):
            assert value == "", f"{type(tab).__name__}: поле {key} не очищено"
        elif isinstance(value, list):
            assert value == [], f"{type(tab).__name__}: список {key} не очищен"
        elif isinstance(value, int):
            assert value == 0, f"{type(tab).__name__}: счётчик {key} не очищен"


def test_tab_has_action_buttons(tab):
    assert isinstance(tab.btn_create_contract, QPushButton)
    assert tab.btn_create_contract.text() == "Создать договор"
    assert isinstance(tab.btn_clear_form, QPushButton)
    assert tab.btn_clear_form.text() == "Очистить форму"


def test_action_panel_is_action_bar(tab):
    assert isinstance(tab._tab_actions, QFrame)
    assert tab._tab_actions.objectName() == "actionBar"


def test_create_button_emits_signal(tab):
    spy = QSignalSpy(tab.create_contract_requested)

    tab.btn_create_contract.click()

    assert len(spy) == 1


def test_clear_button_emits_signal(tab):
    spy = QSignalSpy(tab.clear_requested)

    tab.btn_clear_form.click()

    assert len(spy) == 1


def test_action_buttons_do_not_change_fields(tab):
    """Кнопки только сообщают о намерении: чистит и создаёт окно, не вкладка."""
    tab.fill_data(SAMPLE_DATA[type(tab)])
    before = tab.get_data()

    tab.btn_clear_form.click()
    tab.btn_create_contract.click()

    assert tab.get_data() == before


def test_recognition_panel_relays_signal(tab):
    assert isinstance(tab.recognition_panel, RecognitionPanel)
    spy = QSignalSpy(tab.recognize_requested)

    tab.recognition_panel.recognize_requested.emit("текст документа")

    assert len(spy) == 1
    assert spy[0][0] == "текст документа"


def test_tab_has_recognition_panel_at_top(tab):
    layout = tab.layout()
    scroll = layout.itemAt(0).widget()
    content = scroll.widget()
    first = content.layout().itemAt(0).widget()

    assert first is tab.recognition_panel


def test_tab_connect_does_not_use_lambda(tab):
    """
    В connect — только метод класса (грабли 2B.7).

    lambda в connect создаёт цикл ссылок Python ↔ Qt и роняет процесс
    при завершении.
    """
    source = inspect.getsource(type(tab).__init__)

    assert "lambda" not in source, f"{type(tab).__name__}: lambda в connect"


def test_tab_actions_panel_is_last_widget(tab):
    """Панель действий внизу: она последняя в layout вкладки."""
    layout = tab.layout()
    assert layout is not None

    last = layout.itemAt(layout.count() - 1).widget()
    assert last is tab._tab_actions


def test_modules_do_not_use_lambda_in_connect():
    """Ни одна вкладка этого типа не подключает обработчики через lambda."""
    import ui.windows.arenda_ts.tabs.cargo_tab as cargo
    import ui.windows.arenda_ts.tabs.crew_tab as crew
    import ui.windows.arenda_ts.tabs.lessee_tab as lessee
    import ui.windows.arenda_ts.tabs.lessor_tab as lessor
    import ui.windows.arenda_ts.tabs.price_tab as price
    import ui.windows.arenda_ts.tabs.route_tab as route
    import ui.windows.arenda_ts.tabs.vehicle_tab as vehicle

    for module in (lessee, lessor, vehicle, route, cargo, crew, price):
        source = inspect.getsource(module)
        assert ".connect(lambda" not in source, module.__name__


# ─────────────────────────────────────────────────────────────
# «Арендатор»
# ─────────────────────────────────────────────────────────────

def test_lessee_has_seventeen_keys(qt_app):
    """Ключи вкладки — ровно те, что читает _build_lessee."""
    assert len(LesseeTab().get_data()) == 17


def test_lessee_default_carrier_type_is_ooo(qt_app):
    tab = LesseeTab()

    assert tab.get_data()["carrier_type"] == lessee_tab_module.CARRIER_TYPE_OOO
    assert tab.carrier_type.count() == 3


def test_lessee_kpp_is_visible_for_ooo(qt_app):
    tab = LesseeTab()

    assert tab._kpp_label.isHidden() is False
    assert tab.kpp.isHidden() is False


def test_lessee_kpp_is_hidden_for_ip(qt_app):
    """У индивидуального предпринимателя КПП не существует."""
    tab = LesseeTab()

    tab.carrier_type.setCurrentText("ИП без НДС")

    assert tab.kpp.isHidden() is True, "поле КПП осталось на форме"
    assert tab._kpp_label.isHidden() is True, "подпись КПП осталась на форме"


def test_lessee_kpp_is_cleared_for_ip(qt_app):
    """Скрытая строка КПП не оставляет значения: у ИП КПП не существует."""
    tab = LesseeTab()
    tab.fill_data({"kpp": "770701001"})

    tab.carrier_type.setCurrentText("ИП с НДС")

    assert tab.get_data()["kpp"] == ""

    tab.carrier_type.setCurrentText("ООО")
    assert tab.kpp.isHidden() is False
    assert tab.get_data()["kpp"] == ""


def test_lessee_basis_switches_with_carrier_type(qt_app):
    tab = LesseeTab()
    assert tab.get_data()["basis"] == lessee_tab_module.BASIS_OOO

    tab.carrier_type.setCurrentText("ИП без НДС")
    assert tab.get_data()["basis"] == lessee_tab_module.BASIS_IP

    tab.carrier_type.setCurrentText("ООО")
    assert tab.get_data()["basis"] == lessee_tab_module.BASIS_OOO


def test_lessee_basis_keeps_manual_value(qt_app):
    """Основание, отличное от подстановки, видом стороны не перетирается."""
    tab = LesseeTab()
    tab.basis.setText("доверенности")

    tab.carrier_type.setCurrentText("ИП с НДС")

    assert tab.get_data()["basis"] == "доверенности"


def test_lessee_ogrn_label_follows_carrier_type(qt_app):
    """У ИП метка госрегистрации — ОГРНИП, у ООО — ОГРН."""
    tab = LesseeTab()

    tab.carrier_type.setCurrentText("ИП без НДС")
    assert "ОГРНИП" in tab._ogrn_label.text()

    tab.carrier_type.setCurrentText("ООО")
    assert "ОГРНИП" not in tab._ogrn_label.text()


def test_lessee_has_dadata_buttons(qt_app):
    """Кнопки «🔎»: реквизиты по ИНН и банк по БИК."""
    tab = LesseeTab()

    assert tab.btn_fill_by_inn.text() == "🔎"
    assert tab.btn_fill_by_bic.text() == "🔎"


def test_lessee_dadata_applier_fills_requisites(qt_app, monkeypatch):
    """Данные DaData раскладываются по полям вкладки (диалог — заглушка)."""
    tab = LesseeTab()
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *args, **kwargs: QMessageBox.Ok),
    )

    tab._apply_dadata({
        "full_name": "ООО «Ромашка»",
        "short_name": "ООО «Ромашка»",
        "inn": "7701234567",
        "kpp": "770101001",
        "ogrn": "1027700132195",
        "legal_address": "г. Москва, ул. Тестовая, д. 1",
        "director_name": "Петров Пётр Петрович",
        "director_position": "Генеральный директор",
        "status": "ACTIVE",
    })
    data = tab.get_data()

    assert data["full_name"] == "ООО «Ромашка»"
    assert data["inn"] == "7701234567"
    assert data["kpp"] == "770101001"
    assert data["address"] == "г. Москва, ул. Тестовая, д. 1"
    assert data["director_name"] == "Петров Пётр Петрович"


def test_lessee_carrier_type_from_entity_type(qt_app):
    """Распознавание отдаёт вид стороны ключом entity_type."""
    tab = LesseeTab()

    tab.fill_data({"entity_type": "ИП", "full_name": "ИП Иванов И. И."})

    assert tab.get_data()["carrier_type"] == "ИП с НДС"


def test_lessee_empty_values_keep_manual_input(qt_app):
    tab = LesseeTab()
    tab.fill_data({"full_name": "ООО «Ромашка»"})

    tab.fill_data({"full_name": "   ", "inn": ""})

    assert tab.get_data()["full_name"] == "ООО «Ромашка»"


# ─────────────────────────────────────────────────────────────
# «Арендодатель»
# ─────────────────────────────────────────────────────────────

def test_lessor_has_fifteen_keys(qt_app):
    """У Арендодателя нет ни вида стороны, ни КПП."""
    data = LessorTab().get_data()

    assert len(data) == 15
    assert "carrier_type" not in data
    assert "kpp" not in data


def test_lessor_has_no_kpp_attribute(qt_app):
    tab = LessorTab()

    assert not hasattr(tab, "kpp"), "у Арендодателя КПП быть не должно"


def test_lessor_has_dadata_buttons(qt_app):
    """Кнопки «🔎» у ИНН и БИК — как у Арендатора."""
    tab = LessorTab()

    assert tab.btn_fill_by_inn.text() == "🔎"
    assert tab.btn_fill_by_bic.text() == "🔎"


def test_lessor_defaults_are_empty(qt_app):
    """У Арендодателя нет значений по умолчанию: вторая сторона каждый раз своя."""
    data = LessorTab().get_data()

    for key, value in data.items():
        assert value == "", f"поле {key} заполнено по умолчанию"


def test_lessor_basis_after_fill(qt_app):
    tab = LessorTab()

    tab.fill_data({"basis": "Устава"})

    assert tab.get_data()["basis"] == "Устава"


def test_lessor_bank_applier_fills_bank_and_corr_account(qt_app, monkeypatch):
    tab = LessorTab()
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *args, **kwargs: QMessageBox.Ok),
    )

    tab._apply_dadata_bank({
        "bank_name": "АО «Банк Второй»",
        "correspondent_account": "30101810400000000226",
        "state": "ACTIVE",
    })
    data = tab.get_data()

    assert data["bank"] == "АО «Банк Второй»"
    assert data["corr_account"] == "30101810400000000226"


def test_lessor_fill_and_read_back(qt_app):
    tab = LessorTab()

    tab.fill_data(SAMPLE_DATA[LessorTab])
    data = tab.get_data()

    assert data["full_name"] == "ООО «ЛЦ»"
    assert data["inn"] == "7701234567"
    assert data["address"] == "г. Москва, ул. Тестовая, д. 1"
    assert data["actual_address"] == "г. Москва, ул. Тестовая, д. 1"


# ─────────────────────────────────────────────────────────────
# «ТС»
# ─────────────────────────────────────────────────────────────

def test_vehicle_has_ten_keys(qt_app):
    """Десять полей: договор, три даты, тягач (марка, номер, тип) и прицеп."""
    assert len(VehicleTab().get_data()) == 10


def test_vehicle_has_contract_and_lease_groups(qt_app):
    """Договор, срок аренды, тягач и прицеп — четыре группы полей."""
    tab = VehicleTab()
    titles = [group.title() for group in tab.findChildren(QGroupBox)]

    assert "Договор аренды" in titles
    assert "Срок аренды" in titles
    assert "Тягач" in titles
    assert "Прицеп" in titles


def test_vehicle_dates_are_iso(qt_app):
    tab = VehicleTab()

    tab.fill_data({"contract_date": "23.09.2026", "lease_start_date": "24/09/2026"})

    assert tab.get_data()["contract_date"] == "2026-09-23"
    assert tab.get_data()["lease_start_date"] == "2026-09-24"


def test_vehicle_has_three_editable_dates(qt_app):
    """Три РАЗНЫЕ даты (FIX-1): начало и конец аренды и завершение рейса."""
    tab = VehicleTab()

    tab.fill_data({
        "lease_start_date": "2026-09-21",
        "lease_end_date": "2026-09-28",
        "planned_completion_date": "2026-09-26",
    })
    data = tab.get_data()

    assert data["lease_start_date"] == "2026-09-21"
    assert data["lease_end_date"] == "2026-09-28"
    assert data["planned_completion_date"] == "2026-09-26"


def test_vehicle_lease_end_does_not_change_completion_date(qt_app):
    """Окончание аренды и завершение рейса — независимые даты."""
    tab = VehicleTab()
    tab.fill_data(SAMPLE_DATA[VehicleTab])
    before = tab.get_data()["planned_completion_date"]

    tab.fill_data({"lease_end_date": "2027-01-31"})

    assert tab.get_data()["lease_end_date"] == "2027-01-31"
    assert tab.get_data()["planned_completion_date"] == before


def test_vehicle_completion_date_does_not_change_lease_dates(qt_app):
    """Обратная проверка: дата рейса не трогает срок аренды."""
    tab = VehicleTab()
    tab.fill_data(SAMPLE_DATA[VehicleTab])
    before = tab.get_data()

    tab.fill_data({"planned_completion_date": "2026-10-05"})
    after = tab.get_data()

    assert after["planned_completion_date"] == "2026-10-05"
    assert after["lease_start_date"] == before["lease_start_date"]
    assert after["lease_end_date"] == before["lease_end_date"]


def test_vehicle_equal_dates_are_kept_as_entered(qt_app):
    """Одинаковые даты — выбор пользователя, а не ошибка: так и остаётся."""
    tab = VehicleTab()

    tab.fill_data({
        "lease_end_date": "2026-09-26",
        "planned_completion_date": "2026-09-26",
    })
    data = tab.get_data()

    assert data["lease_end_date"] == data["planned_completion_date"] == "2026-09-26"


def test_vehicle_default_completion_date_is_independent(qt_app):
    """По умолчанию дата рейса не равна окончанию аренды."""
    data = VehicleTab().get_data()

    assert data["planned_completion_date"] != data["lease_end_date"]


def test_vehicle_default_lease_end_is_after_start(qt_app):
    """Срок аренды по умолчанию — год: окончание позже начала."""
    data = VehicleTab().get_data()

    assert data["lease_end_date"] > data["lease_start_date"]


def test_vehicle_date_labels_name_the_contract_clauses(qt_app):
    """Подписи полей дат повторяют формулировки бланка (п. 2.5 и 3.3.2)."""
    from PyQt5.QtWidgets import QLabel

    tab = VehicleTab()
    joined = " | ".join(
        label.text() for label in tab.findChildren(QLabel)
    )

    assert "Плановый период аренды, с" in joined
    assert "Плановый период аренды, по" in joined
    assert "Планируемая дата завершения рейса" in joined


def test_vehicle_accepts_number_key(qt_app):
    """Промпт отдаёт номер договора и как contract_number, и как number."""
    tab = VehicleTab()

    tab.fill_data({"number": "05/2026"})

    assert tab.get_data()["contract_number"] == "05/2026"


def test_vehicle_tractor_type_is_required(qt_app):
    """Тип ТС тягача обязателен: без него валидатор не пропустит договор."""
    tab = VehicleTab()

    assert tab.tractor_type.is_required() is True


def test_vehicle_fill_and_read_back(qt_app):
    tab = VehicleTab()

    tab.fill_data(SAMPLE_DATA[VehicleTab])
    data = tab.get_data()

    assert data["tractor_brand"] == "DAF XF 95.430"
    assert data["tractor_plate"] == "М342СА761"
    assert data["tractor_type"] == "Седельный тягач"
    assert data["trailer_brand"] == "KRONE SD"
    assert data["trailer_plate"] == "ВК123478"


# ─────────────────────────────────────────────────────────────
# «Маршрут»
# ─────────────────────────────────────────────────────────────

def test_route_tables_have_expected_columns(qt_app):
    tab = RouteTab()

    assert _headers(tab.loadings_table) == route_tab_module.LOADING_HEADERS
    assert _headers(tab.unloadings_table) == route_tab_module.UNLOADING_HEADERS
    assert tab.loadings_table.columnCount() == 5
    assert tab.unloadings_table.columnCount() == 3


def test_route_max_points_matches_data_module():
    assert route_tab_module.MAX_POINTS == data_module.MAX_POINTS == 10


def test_route_tables_start_with_one_row(qt_app):
    tab = RouteTab()

    assert tab.loadings_table.rowCount() == route_tab_module.MIN_ROWS == 1
    assert tab.unloadings_table.rowCount() == route_tab_module.MIN_ROWS == 1
    assert tab.get_data()["loadings"] == []
    assert tab.get_data()["unloadings"] == []


def test_route_loading_point_has_times(qt_app):
    """У точки погрузки — дата и время подачи ТС; у выгрузки времени нет."""
    tab = RouteTab()

    tab.fill_data(SAMPLE_DATA[RouteTab])
    data = tab.get_data()

    assert data["loadings"] == [{
        "name": "ООО «Склад Север»",
        "address": "г. Москва, ул. Складская, д. 1",
        "date": "2026-09-26",
        "time_from": "08:00",
        "time_to": "18:00",
    }]
    assert data["unloadings"] == [{
        "name": "ООО «Приёмка»",
        "address": "г. Казань, ул. Приёмная, д. 3",
        "date": "2026-09-27",
    }]


def test_route_point_without_address_is_not_returned(qt_app):
    """Пустая строка таблицы точкой не считается."""
    tab = RouteTab()
    tab.fill_data(SAMPLE_DATA[RouteTab])

    tab.btn_add_loading.click()
    tab.btn_add_unloading.click()

    assert len(tab.get_data()["loadings"]) == 1
    assert len(tab.get_data()["unloadings"]) == 1


def test_route_point_with_address_only_is_kept(qt_app):
    """Наименование может не распознаться: адрес всё равно данные."""
    tab = RouteTab()

    tab.fill_data({"loadings": [{"name": "", "address": "г. Москва, ул. Южная, д. 2"}]})

    assert tab.get_data()["loadings"] == [{
        "name": "", "address": "г. Москва, ул. Южная, д. 2",
        "date": "", "time_from": "", "time_to": "",
    }]


def test_route_add_row_in_both_tables(qt_app):
    tab = RouteTab()

    tab.btn_add_loading.click()
    tab.btn_add_unloading.click()

    assert tab.loadings_table.rowCount() == 2
    assert tab.unloadings_table.rowCount() == 2


def test_route_add_row_stops_at_max_points(qt_app, quiet_dialogs):
    tab = RouteTab()

    for _ in range(data_module.MAX_POINTS + 3):
        tab.btn_add_loading.click()
        tab.btn_add_unloading.click()

    assert tab.loadings_table.rowCount() == data_module.MAX_POINTS
    assert tab.unloadings_table.rowCount() == data_module.MAX_POINTS


def test_route_remove_row_in_both_tables(qt_app, quiet_dialogs):
    tab = RouteTab()
    tab.fill_data(SAMPLE_DATA[RouteTab])
    tab.btn_add_loading.click()
    tab.btn_add_unloading.click()

    tab.loadings_table.setCurrentCell(1, 0)
    tab.btn_remove_loading.click()
    tab.unloadings_table.setCurrentCell(1, 0)
    tab.btn_remove_unloading.click()

    assert tab.loadings_table.rowCount() == 1
    assert tab.unloadings_table.rowCount() == 1


def test_route_last_row_can_be_removed(qt_app, quiet_dialogs):
    """
    Удалить можно и последнюю строку: пустая таблица — норма.

    ШАГ FIX-5: ограничение «минимум одна строка» убрано (оператор не мог
    получить пустую таблицу). О пустом разделе маршрута скажет валидатор.
    """
    tab = RouteTab()
    tab.loadings_table.setCurrentCell(0, 0)
    tab.unloadings_table.setCurrentCell(0, 0)

    tab.btn_remove_loading.click()
    tab.btn_remove_unloading.click()

    assert tab.loadings_table.rowCount() == 0
    assert tab.unloadings_table.rowCount() == 0


def test_route_empty_tables_give_empty_lists(qt_app, quiet_dialogs):
    """Пустые таблицы отдают пустые списки, а не None (ШАГ FIX-5, часть B.3)."""
    tab = RouteTab()
    tab.loadings_table.setCurrentCell(0, 0)
    tab.unloadings_table.setCurrentCell(0, 0)
    tab.btn_remove_loading.click()
    tab.btn_remove_unloading.click()

    data = tab.get_data()

    assert data["loadings"] == []
    assert data["unloadings"] == []


def test_route_empty_points_block_contract_creation(qt_app, quiet_dialogs):
    """Без точек погрузки и выгрузки создание договора аренды не проходит."""
    from core.contract_data import ContractData

    tab = RouteTab()
    tab.loadings_table.setCurrentCell(0, 0)
    tab.unloadings_table.setCurrentCell(0, 0)
    tab.btn_remove_loading.click()
    tab.btn_remove_unloading.click()

    report = ArendaTsValidator().check(ContractData.coerce(tab.get_data()))

    assert report.has_errors is True
    assert "Укажите хотя бы одну точку погрузки" in report.errors
    assert "Укажите хотя бы одну точку выгрузки" in report.errors


def test_route_rows_can_be_added_back_after_deleting_all(qt_app, quiet_dialogs):
    """После удаления всех строк кнопки «Добавить» снова дают строку."""
    tab = RouteTab()
    tab.loadings_table.setCurrentCell(0, 0)
    tab.unloadings_table.setCurrentCell(0, 0)
    tab.btn_remove_loading.click()
    tab.btn_remove_unloading.click()

    tab.btn_add_loading.click()
    tab.btn_add_unloading.click()

    assert tab.loadings_table.rowCount() == 1
    assert tab.unloadings_table.rowCount() == 1


# ─────────────────────────────────────────────────────────────
# Ширины колонок и подсказки (ШАГ FIX-5, часть C)
# ─────────────────────────────────────────────────────────────

def test_loadings_name_is_contents(qt_app):
    """Наименование — по содержимому: длина названий разная."""
    tab = RouteTab()
    header = tab.loadings_table.horizontalHeader()

    assert header.sectionResizeMode(route_tab_module.COL_NAME) == (
        QHeaderView.ResizeToContents
    )


def test_loadings_address_is_stretch(qt_app):
    """Адрес — главная колонка: тянется по ширине таблицы."""
    tab = RouteTab()

    assert tab.loadings_table.horizontalHeader().sectionResizeMode(
        route_tab_module.COL_ADDRESS
    ) == QHeaderView.Stretch
    assert tab.unloadings_table.horizontalHeader().sectionResizeMode(
        route_tab_module.COL_ADDRESS
    ) == QHeaderView.Stretch


def test_loadings_date_and_time_are_fixed(qt_app):
    """Дата и время подачи ТС — фиксированные колонки, «Дат» не выходит."""
    tab = RouteTab()
    header = tab.loadings_table.horizontalHeader()

    for column, width in (
        (route_tab_module.COL_DATE, 90),
        (route_tab_module.COL_TIME_FROM, 80),
        (route_tab_module.COL_TIME_TO, 80),
    ):
        assert header.sectionResizeMode(column) == QHeaderView.Interactive
        assert header.sectionSize(column) == width

    assert header.minimumSectionSize() == 70


def test_unloading_date_is_fixed(qt_app):
    """У выгрузки три колонки: дата тоже фиксированная."""
    tab = RouteTab()
    header = tab.unloadings_table.horizontalHeader()

    assert header.sectionResizeMode(route_tab_module.COL_DATE) == (
        QHeaderView.Interactive
    )
    assert header.sectionSize(route_tab_module.COL_DATE) == 90


def test_route_widths_persist_between_sessions(qt_app):
    """
    Растянутая колонка остаётся растянутой после перезапуска.

    «Новая сессия» — новая вкладка: ширины читаются из QSettings
    (хранилище тестов изолировано, см. isolated_qsettings).
    """
    first = RouteTab()
    first.loadings_table.horizontalHeader().resizeSection(
        route_tab_module.COL_TIME_FROM, 120
    )
    first.loadings_table._widths_saver.flush()

    second = RouteTab()

    assert second.loadings_table.horizontalHeader().sectionSize(
        route_tab_module.COL_TIME_FROM
    ) == 120
    # У выгрузок свой ключ: чужая раскладка их не трогает.
    assert second.unloadings_table.horizontalHeader().sectionSize(
        route_tab_module.COL_DATE
    ) == 90


# ─────────────────────────────────────────────────────────────
# Высота таблиц точек — растяжение по вертикали (ШАГ «Высота таблиц точек»)
# ─────────────────────────────────────────────────────────────

def test_point_tables_expand_vertically(qt_app):
    """
    Обе таблицы точек аренды растягиваются по вертикали.

    Такое же поведение у таблицы «Перевозимые авто» Экспедиторства (эталон):
    свободное место забирает таблица, а не stretch-распорка под ней.
    """
    tab = RouteTab()

    for table in (tab.loadings_table, tab.unloadings_table):
        assert table.sizePolicy().verticalPolicy() == QSizePolicy.Expanding
        assert table.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding


def test_point_table_minimum_height_allows_two_rows(qt_app):
    """
    Минимум высоты — 120 пикселей, а не 90.

    Шапка (около 21) и две полные строки по 40; верхние границы у таблиц
    свои (180 у погрузки, 160 у выгрузки) и минимум не перебивают.
    """
    tab = RouteTab()

    for table, maximum in (
        (tab.loadings_table, route_tab_module.LOADING_TABLE_MAX_HEIGHT),
        (tab.unloadings_table, route_tab_module.UNLOADING_TABLE_MAX_HEIGHT),
    ):
        assert table.minimumHeight() == route_tab_module.POINT_TABLE_MIN_HEIGHT
        assert table.minimumHeight() >= 120
        assert table.minimumHeight() <= maximum
        assert table.maximumHeight() == maximum
        assert table.verticalHeader().defaultSectionSize() == ROW_HEIGHT_TWO_LINES
        assert table.wordWrap() is True


def test_point_table_row_takes_two_lines_of_the_current_font(qt_app):
    """
    Строка таблицы точек вмещает две строки текущего шрифта.

    Длинный адрес переносится по словам; при прежней высоте строки
    (31 пиксель по умолчанию) вторая строка адреса обрезалась.
    """
    tab = RouteTab()

    for table in (tab.loadings_table, tab.unloadings_table):
        metrics = QFontMetrics(table.font())

        assert (
            table.verticalHeader().defaultSectionSize()
            >= 2 * metrics.lineSpacing()
        )


def test_tooltip_on_long_address_keeps_unloading_date_tooltip(qt_app):
    """Длинный адрес виден целиком, а пояснение к дате выгрузки не затёрто."""
    tab = RouteTab()
    table = tab.unloadings_table
    long_address = (
        "г. Москва, ул. Перерва, д. 19, стр. 3, въезд со стороны "
        "Курьяновского бульвара, пост охраны № 2"
    )
    table.item(0, route_tab_module.COL_ADDRESS).setText(long_address)

    table.itemEntered.emit(table.item(0, route_tab_module.COL_ADDRESS))
    table.itemEntered.emit(table.item(0, route_tab_module.COL_DATE))

    assert table.item(0, route_tab_module.COL_ADDRESS).toolTip() == long_address
    assert table.item(0, route_tab_module.COL_DATE).toolTip() == (
        route_tab_module.UNLOADING_DATE_TOOLTIP
    )


def test_route_fill_over_limit_is_truncated(qt_app):
    """Больше 10 точек в бланк не помещается: лишние отбрасываются."""
    tab = RouteTab()
    points = [
        {"name": f"Точка {i}", "address": f"Адрес {i}", "date": ""}
        for i in range(data_module.MAX_POINTS + 5)
    ]

    tab.fill_data({"loadings": points, "unloadings": points})

    assert len(tab.get_data()["loadings"]) == data_module.MAX_POINTS
    assert tab.get_data()["loadings"][0]["name"] == "Точка 0"


def test_route_broken_date_is_not_invented(qt_app):
    """Нераспознанная дата точки в бланк не попадает."""
    tab = RouteTab()

    tab.fill_data({"loadings": [
        {"name": "Склад", "address": "г. Москва", "date": "не дата"},
    ]})

    assert tab.get_data()["loadings"][0]["date"] == ""


def test_route_empty_route_keeps_manual_input(qt_app):
    tab = RouteTab()
    tab.fill_data({"route": "Москва - Казань"})

    tab.fill_data({"route": "  "})

    assert tab.get_data()["route"] == "Москва - Казань"


# ── Подсказка у колонки «Дата» точек выгрузки (ШАГ FIX-1-T2) ──

def test_unloading_date_tooltip_text():
    """Текст подсказки — тот, что согласован в задании."""
    assert route_tab_module.UNLOADING_DATE_TOOLTIP == (
        "Справочно. В договор идёт планируемая дата завершения рейса "
        "(вкладка ТС)"
    )


def test_unloading_date_cells_have_tooltip(qt_app):
    """У ячейки «Дата» точек выгрузки есть пояснение."""
    tab = RouteTab()
    table = tab.unloadings_table

    for row in range(table.rowCount()):
        item = table.item(row, route_tab_module.COL_DATE)
        assert item is not None
        assert item.toolTip() == route_tab_module.UNLOADING_DATE_TOOLTIP


def test_loading_date_cells_have_no_tooltip(qt_app):
    """У погрузки подсказки нет: её дата печатается в бланке (п. 3.2)."""
    tab = RouteTab()
    table = tab.loadings_table

    for row in range(table.rowCount()):
        item = table.item(row, route_tab_module.COL_DATE)
        assert item is not None
        assert item.toolTip() == ""


def test_unloading_date_header_has_tooltip(qt_app):
    """Пояснение стоит и на заголовке колонки «Дата» у выгрузки."""
    tab = RouteTab()
    date_column = route_tab_module.COL_DATE

    header = tab.unloadings_table.horizontalHeaderItem(date_column)
    assert header is not None
    assert header.toolTip() == route_tab_module.UNLOADING_DATE_TOOLTIP

    loading_header = tab.loadings_table.horizontalHeaderItem(date_column)
    assert loading_header is not None
    assert loading_header.toolTip() == ""


def test_unloading_tooltip_survives_fill_add_and_clear(qt_app):
    """Подсказка возвращается после перерисовки таблицы, добавления и очистки."""
    tab = RouteTab()
    date_column = route_tab_module.COL_DATE

    tab.fill_data(SAMPLE_DATA[RouteTab])
    for row in range(tab.unloadings_table.rowCount()):
        assert tab.unloadings_table.item(row, date_column).toolTip() == (
            route_tab_module.UNLOADING_DATE_TOOLTIP
        )

    tab._on_add_unloading()
    last = tab.unloadings_table.rowCount() - 1
    assert tab.unloadings_table.item(last, date_column).toolTip() == (
        route_tab_module.UNLOADING_DATE_TOOLTIP
    )

    tab.clear()
    for row in range(tab.unloadings_table.rowCount()):
        assert tab.unloadings_table.item(row, date_column).toolTip() == (
            route_tab_module.UNLOADING_DATE_TOOLTIP
        )


def test_unloading_tooltip_does_not_change_data(qt_app):
    """Подсказка не меняет ни значение даты, ни поведение вкладки."""
    tab = RouteTab()

    tab.fill_data(SAMPLE_DATA[RouteTab])

    assert tab.get_data()["unloadings"] == SAMPLE_DATA[RouteTab]["unloadings"]


# ─────────────────────────────────────────────────────────────
# «Груз»
# ─────────────────────────────────────────────────────────────

def test_cargo_table_has_five_columns(qt_app):
    tab = CargoTab()

    assert _headers(tab.vehicles_table) == cargo_tab_module.HEADERS
    assert tab.vehicles_table.columnCount() == 5


def test_cargo_max_cars_matches_data_module(qt_app):
    assert cargo_tab_module.MAX_CARS == data_module.MAX_CARS == 12


def test_cargo_starts_with_min_rows_and_zero_count(qt_app):
    tab = CargoTab()

    assert tab.vehicles_table.rowCount() == cargo_tab_module.MIN_ROWS == 3
    assert tab.get_data()["vehicles"] == []
    assert tab.get_data()["cargo_count"] == 0


def test_cargo_count_is_readonly(qt_app):
    assert CargoTab().cargo_count.isReadOnly() is True


def test_cargo_count_updates_after_fill(qt_app):
    tab = CargoTab()

    tab.fill_data(SAMPLE_DATA[CargoTab])

    assert tab.get_data()["cargo_count"] == 1
    assert tab.cargo_count.text() == "1"


def test_cargo_count_updates_when_rows_are_added_and_filled(qt_app):
    """Счётчик считается по таблице: заполнили строку — вырос."""
    tab = CargoTab()
    tab.btn_add_vehicle.click()
    tab.vehicles_table.setItem(
        tab.vehicles_table.rowCount() - 1, cargo_tab_module.COL_BRAND,
        QTableWidgetItem("Lada Vesta"),
    )

    assert tab.get_data()["cargo_count"] == 1


def test_cargo_count_updates_on_table_edit(qt_app):
    """Счётчик меняется сам, когда правят ячейки таблицы."""
    tab = CargoTab()
    assert tab.get_data()["cargo_count"] == 0

    tab.vehicles_table.setItem(0, cargo_tab_module.COL_BRAND, QTableWidgetItem("JETOUR T2"))
    assert tab.get_data()["cargo_count"] == 1
    assert tab.cargo_count.text() == "1"

    # Стерли марку и VIN — строка снова пустая, машина не считается.
    tab.vehicles_table.setItem(0, cargo_tab_module.COL_BRAND, QTableWidgetItem(""))
    assert tab.get_data()["cargo_count"] == 0


def test_cargo_count_updates_after_remove(qt_app, quiet_dialogs):
    tab = CargoTab()
    tab.fill_data(SAMPLE_DATA[CargoTab])
    tab.vehicles_table.setCurrentCell(0, 1)

    tab.btn_remove_vehicle.click()

    assert tab.get_data()["cargo_count"] == 0
    assert tab.cargo_count.text() == "0"


def test_cargo_manual_count_is_rolled_back(qt_app):
    """Счётчик — только для чтения: чужое число в нём не останется."""
    tab = CargoTab()

    tab.cargo_count.setText("99")

    assert tab.cargo_count.text() == "0"


def test_cargo_row_can_hold_points(qt_app):
    tab = CargoTab()

    tab.fill_data(SAMPLE_DATA[CargoTab])

    assert tab.get_data()["vehicles"] == [{
        "brand_model": "JETOUR T2", "vin": VIN_1,
        "loading_point": "Москва", "unloading_point": "Казань",
    }]


def test_cargo_row_with_only_brand_is_kept(qt_app):
    """Строка с одной маркой тоже данные: VIN бывает не распознан."""
    tab = CargoTab()

    tab.fill_data({"vehicles": [{"brand_model": "Lada Vesta", "vin": ""}]})

    assert tab.get_data()["vehicles"] == [{
        "brand_model": "Lada Vesta", "vin": "",
        "loading_point": "", "unloading_point": "",
    }]


def test_cargo_fill_over_limit_is_truncated(qt_app):
    """Больше 12 машин в бланк не помещается: лишние отбрасываются."""
    tab = CargoTab()
    vehicles = [
        {"brand_model": f"Машина {i}", "vin": VIN_2} for i in range(20)
    ]

    tab.fill_data({"vehicles": vehicles})

    assert len(tab.get_data()["vehicles"]) == data_module.MAX_CARS
    assert tab.get_data()["vehicles"][0]["brand_model"] == "Машина 0"
    assert tab.get_data()["cargo_count"] == data_module.MAX_CARS


def test_cargo_add_row_stops_at_max_cars(qt_app, quiet_dialogs):
    tab = CargoTab()

    for _ in range(data_module.MAX_CARS + 3):
        tab.btn_add_vehicle.click()

    assert tab.vehicles_table.rowCount() == data_module.MAX_CARS


def test_cargo_clear_returns_min_rows_and_zero_count(qt_app):
    tab = CargoTab()
    tab.fill_data(SAMPLE_DATA[CargoTab])

    tab.clear()

    assert tab.vehicles_table.rowCount() == cargo_tab_module.MIN_ROWS
    assert tab.get_data() == {"cargo_count": 0, "vehicles": []}


# ─────────────────────────────────────────────────────────────
# «Экипаж»
# ─────────────────────────────────────────────────────────────

def test_crew_has_exactly_nine_fields(qt_app):
    """В контракте девять полей: серии и номера раздельно здесь нет."""
    assert len(CrewTab().get_data()) == 9


def test_crew_has_no_separate_series_fields(qt_app):
    """Паспорт и ВУ — одной строкой, отдельных серии и номера быть не должно."""
    tab = CrewTab()

    for name in (
        "passport_series", "passport_number",
        "license_series", "license_number",
    ):
        assert not hasattr(tab, name), f"лишнее поле {name}"


def test_crew_documents_are_single_lines(qt_app):
    tab = CrewTab()

    tab.fill_data(SAMPLE_DATA[CrewTab])
    data = tab.get_data()

    assert data["driver_passport"] == "18 22 926830"
    assert data["driver_license"] == "99 36 123456"


def test_crew_joins_split_document(qt_app):
    """Раздельные серия и номер (справочник водителя) склеиваются в строку."""
    tab = CrewTab()

    tab.fill_data({"passport_series": "18 22", "passport_number": "926830"})

    assert tab.get_data()["driver_passport"] == "18 22 926830"


def test_crew_has_dadata_fms_button(qt_app):
    """Кнопка «🔎» — подразделение ФМС по коду подразделения."""
    tab = CrewTab()

    assert tab.btn_fill_fms.text() == "🔎"
    # Код подразделения — источник запроса: кнопка берёт его из этого поля.
    assert tab.passport_code is not None


def test_crew_passport_code_is_not_in_data(qt_app):
    """Код подразделения — только для поиска: в контракте его нет."""
    tab = CrewTab()

    assert "passport_code" not in tab.get_data()


def test_crew_fms_applier_fills_issuer(qt_app, monkeypatch):
    tab = CrewTab()
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *args, **kwargs: QMessageBox.Ok),
    )
    tab.driver_passport_issuer.clear()

    tab._apply_dadata_fms([{"value": "Отделом УФМС России по г. Москве"}])

    assert tab.get_data()["driver_passport_issuer"] == (
        "Отделом УФМС России по г. Москве"
    )


def test_crew_birth_date_default_is_in_the_past(qt_app):
    tab = CrewTab()

    year = int(tab.get_data()["driver_birth_date"][:4])

    assert year < QDate.currentDate().year()


def test_crew_fill_and_read_back(qt_app):
    tab = CrewTab()

    tab.fill_data(SAMPLE_DATA[CrewTab])
    data = tab.get_data()

    assert data["driver_full_name"] == "Иванов Иван Иванович"
    assert data["driver_birth_date"] == "1980-01-01"
    assert data["driver_passport_issue_date"] == "2023-01-30"
    assert data["driver_license_issue_date"] == "2020-01-01"
    assert data["driver_registration_address"] == "г. Москва, ул. Тестовая, д. 1"
    assert data["driver_phone"] == "+7 (999) 123-45-67"


def test_crew_accepts_recognition_block_without_prefix(qt_app):
    """Распознавание отдаёт блок driver без приставки driver_."""
    tab = CrewTab()

    tab.fill_data({
        "full_name": "Петров Пётр Петрович",
        "passport": "40 15 123456",
        "phone": "+7 (900) 000-00-00",
        "registration_address": "г. Тверь, ул. Мира, д. 7",
    })
    data = tab.get_data()

    assert data["driver_full_name"] == "Петров Пётр Петрович"
    assert data["driver_passport"] == "40 15 123456"
    assert data["driver_phone"] == "+7 (900) 000-00-00"
    assert data["driver_registration_address"] == "г. Тверь, ул. Мира, д. 7"


def test_crew_constants_match_data_module():
    """Дата рождения по умолчанию и формат дат — как в остальных вкладках."""
    assert crew_tab_module.DEFAULT_BIRTH_YEARS_AGO == 30
    assert crew_tab_module.DATE_FORMAT == "dd.MM.yyyy"


# ─────────────────────────────────────────────────────────────
# «Стоимость»
# ─────────────────────────────────────────────────────────────

def test_price_has_combo_and_spinboxes(qt_app):
    tab = PriceTab()

    assert isinstance(tab.vat_rate, QComboBox)
    assert tab.vat_rate.count() == 4
    assert [tab.vat_rate.itemText(i) for i in range(4)] == list(
        price_tab_module.VAT_RATES
    )
    for name in ("sum_wo_vat", "sum_vat", "sum_total"):
        assert isinstance(getattr(tab, name), QDoubleSpinBox), name


def test_price_readonly_fields(qt_app):
    """НДС, итог и сумма прописью — расчётные поля."""
    tab = PriceTab()

    assert tab.sum_vat.isReadOnly() is True
    assert tab.sum_total.isReadOnly() is True
    assert tab.sum_total_words.isReadOnly() is True
    assert tab.sum_wo_vat.isReadOnly() is False


def test_price_sum_words_is_line_edit(qt_app):
    """Сумма прописью — строка, а не число: в бланке это текст."""
    tab = PriceTab()

    assert isinstance(tab.sum_total_words, QLineEdit)
    assert not isinstance(tab.sum_total_words, QDoubleSpinBox)


def test_price_default_vat_rate_is_22(qt_app):
    tab = PriceTab()

    assert tab.get_data()["vat_rate"] == price_tab_module.DEFAULT_VAT_RATE
    assert tab.get_data()["vat_rate_num"] == 22.0


def test_price_recalculates_on_sum_change(qt_app):
    """221 099,18 + 22% = 48 641,82 → итог 269 741,00."""
    tab = PriceTab()

    tab.sum_wo_vat.setValue(SUM_WITHOUT_VAT)
    data = tab.get_data()

    assert data["sum_wo_vat"] == pytest.approx(SUM_WITHOUT_VAT)
    assert data["sum_vat"] == pytest.approx(SUM_VAT)
    assert data["sum_total"] == pytest.approx(SUM_TOTAL)


def test_price_recalculates_on_rate_change(qt_app):
    """Ставка 10% при той же сумме даёт другой НДС и итог."""
    tab = PriceTab()
    tab.sum_wo_vat.setValue(SUM_WITHOUT_VAT)

    tab.vat_rate.setCurrentText("10%")
    data = tab.get_data()

    assert data["vat_rate_num"] == 10.0
    assert data["sum_vat"] == pytest.approx(round(SUM_WITHOUT_VAT * 0.10, 2))
    assert data["sum_total"] == pytest.approx(
        round(SUM_WITHOUT_VAT + SUM_WITHOUT_VAT * 0.10, 2)
    )


def test_price_zero_rate_gives_total_equal_to_sum(qt_app):
    """При «0%» налога нет, итог равен сумме без НДС."""
    tab = PriceTab()
    tab.sum_wo_vat.setValue(IP_SUM)

    tab.vat_rate.setCurrentText("0%")
    data = tab.get_data()

    assert data["sum_vat"] == 0.0
    assert data["sum_total"] == pytest.approx(IP_SUM)
    assert data["vat_rate_num"] == 0.0


def test_price_sum_words_is_filled(qt_app):
    """Сумма прописью считается через core.num_to_words."""
    tab = PriceTab()

    tab.sum_wo_vat.setValue(SUM_WITHOUT_VAT)
    text = tab.get_data()

    assert tab.sum_total_words.text().startswith("Двести шестьдесят девять тысяч")
    assert text["sum_total"] == pytest.approx(SUM_TOTAL)


def test_price_sum_words_follows_recalculation(qt_app):
    tab = PriceTab()
    tab.sum_wo_vat.setValue(1000)

    tab.vat_rate.setCurrentText("0%")

    assert "тысяча" in tab.sum_total_words.text()


def test_price_fill_data_accepts_contract_keys(qt_app):
    """Распознавание отдаёт блок contract: price_without_vat и vat_rate."""
    tab = PriceTab()

    tab.fill_data({"price_without_vat": SUM_WITHOUT_VAT, "vat_rate": "22%"})

    assert tab.get_data()["sum_wo_vat"] == pytest.approx(SUM_WITHOUT_VAT)


def test_price_fill_data_accepts_number_vat_rate(qt_app):
    tab = PriceTab()

    tab.fill_data({"sum_wo_vat": SUM_WITHOUT_VAT, "vat_rate_num": 10})

    assert tab.get_data()["vat_rate_num"] == 10.0


def test_price_fill_data_accepts_float_vat_rate(qt_app):
    """22.0 — та же ставка, что 22: «22.0%» в списке нет."""
    tab = PriceTab()

    tab.fill_data({"sum_wo_vat": SUM_WITHOUT_VAT, "vat_rate_num": 22.0})

    assert tab.get_data()["vat_rate"] == "22%"


def test_price_zero_sum_does_not_erase_manual_input(qt_app):
    """У промпта 0.0 значит «суммы в документе не было»."""
    tab = PriceTab()
    tab.sum_wo_vat.setValue(SUM_WITHOUT_VAT)

    tab.fill_data({"sum_wo_vat": 0})

    assert tab.get_data()["sum_wo_vat"] == pytest.approx(SUM_WITHOUT_VAT)


def test_price_fill_and_read_back(qt_app):
    tab = PriceTab()

    tab.fill_data(SAMPLE_DATA[PriceTab])
    data = tab.get_data()

    assert data["sum_wo_vat"] == pytest.approx(SUM_WITHOUT_VAT)
    assert data["sum_vat"] == pytest.approx(SUM_VAT)
    assert data["sum_total"] == pytest.approx(SUM_TOTAL)
    assert data["special_conditions"] == "Простой не более 24 часов"


def test_price_vat_rates_match_logistiks_rus():
    """Ставки НДС те же, что в остальных окнах проекта."""
    assert price_tab_module.VAT_RATES == ("22%", "20%", "10%", "0%")
    assert price_tab_module.MAX_AMOUNT == 100_000_000


# ─────────────────────────────────────────────────────────────
# «Стоимость»: режим ввода суммы (FIX-1, БАГ 1)
# ─────────────────────────────────────────────────────────────

#: Сумма «с НДС» из задания: 230 000,00 при ставке 22% —
#: база 188 524,59, НДС 41 475,41.
SUM_WITH_VAT_230K = 230000.00
BASE_230K = 188524.59
VAT_230K = 41475.41


def test_price_has_amount_mode_switch(qt_app):
    """Переключатель «Считать от»: «Без НДС» (по умолчанию) и «С НДС»."""
    tab = PriceTab()

    assert isinstance(tab.amount_mode, QComboBox)
    assert [tab.amount_mode.itemText(i) for i in range(tab.amount_mode.count())] \
        == list(price_tab_module.AMOUNT_MODES)
    assert tab.amount_mode_text() == price_tab_module.DEFAULT_AMOUNT_MODE
    assert tab.amount_mode_text() == price_tab_module.MODE_WITHOUT_VAT
    assert tab.calculates_from_total() is False


def test_price_input_field_is_always_editable(qt_app):
    """Поле суммы — одно и то же, редактируемое в обоих режимах."""
    tab = PriceTab()

    assert tab.sum_wo_vat.isReadOnly() is False
    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)
    assert tab.sum_wo_vat.isReadOnly() is False


def test_price_sum_label_follows_mode(qt_app):
    """Подпись поля суммы объясняет, что означает введённое число."""
    tab = PriceTab()

    assert price_tab_module.BASE_LABEL in tab._sum_edit_label.text()

    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)
    assert price_tab_module.TOTAL_LABEL in tab._sum_edit_label.text()

    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITHOUT_VAT)
    assert price_tab_module.BASE_LABEL in tab._sum_edit_label.text()


def test_price_mode_without_vat_adds_vat_on_top(qt_app):
    """Ввод «без НДС»: итог = база × (1 + ставка/100)."""
    tab = PriceTab()
    tab.sum_wo_vat.setValue(SUM_WITHOUT_VAT)

    data = tab.get_data()

    assert data["sum_wo_vat"] == pytest.approx(SUM_WITHOUT_VAT)
    assert data["sum_total"] == pytest.approx(
        round(SUM_WITHOUT_VAT * (1 + 22 / 100), 2)
    )
    assert data["sum_vat"] == pytest.approx(
        round(data["sum_total"] - data["sum_wo_vat"], 2)
    )


def test_price_mode_with_vat_extracts_vat(qt_app):
    """Ввод «с НДС»: база = итог / (1 + ставка/100), НДС = итог − база."""
    tab = PriceTab()
    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)

    tab.sum_wo_vat.setValue(SUM_WITH_VAT_230K)
    data = tab.get_data()

    assert data["sum_total"] == pytest.approx(SUM_WITH_VAT_230K)
    assert data["sum_wo_vat"] == pytest.approx(BASE_230K)
    assert data["sum_vat"] == pytest.approx(VAT_230K)


def test_price_mode_with_vat_rounding_matches_task(qt_app):
    """230 000,00 с НДС 22% → 188 524,59 без НДС и 41 475,41 НДС."""
    tab = PriceTab()
    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)
    tab.sum_wo_vat.setValue(230000.00)

    data = tab.get_data()

    assert data["sum_wo_vat"] == 188524.59
    assert data["sum_vat"] == 41475.41
    assert data["sum_total"] == 230000.00


def test_price_mode_switch_keeps_value(qt_app):
    """Смена режима не сбрасывает и не меняет введённое число."""
    tab = PriceTab()
    tab.sum_wo_vat.setValue(SUM_WITH_VAT_230K)

    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)

    assert tab.input_amount() == pytest.approx(SUM_WITH_VAT_230K)


def test_price_mode_switch_recalculates_the_other_side(qt_app):
    """Переключение режима пересчитывает вторую сумму, не теряя число."""
    tab = PriceTab()
    tab.sum_wo_vat.setValue(SUM_WITH_VAT_230K)
    base_mode_total = tab.get_data()["sum_total"]

    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)

    assert tab.get_data()["sum_total"] == pytest.approx(SUM_WITH_VAT_230K)
    assert tab.get_data()["sum_wo_vat"] == pytest.approx(BASE_230K)
    assert base_mode_total != pytest.approx(tab.get_data()["sum_total"])


def test_price_mode_switch_back_restores_amounts(qt_app):
    """Туда и обратно: суммы возвращаются к исходным."""
    tab = PriceTab()
    tab.sum_wo_vat.setValue(SUM_WITH_VAT_230K)
    before = tab.get_data()

    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)
    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITHOUT_VAT)

    after = tab.get_data()
    assert after["sum_wo_vat"] == pytest.approx(before["sum_wo_vat"])
    assert after["sum_vat"] == pytest.approx(before["sum_vat"])
    assert after["sum_total"] == pytest.approx(before["sum_total"])


def test_price_both_modes_give_the_same_total(qt_app):
    """
    Оба режима дают один и тот же итог — это и есть смысл переключателя.

    «Без НДС»: база 188 524,59 → итог 230 000,00.
    «С НДС»:   итог 230 000,00 → база 188 524,59.
    """
    without_vat = PriceTab()
    without_vat.sum_wo_vat.setValue(BASE_230K)

    with_vat = PriceTab()
    with_vat.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)
    with_vat.sum_wo_vat.setValue(SUM_WITH_VAT_230K)

    left = without_vat.get_data()
    right = with_vat.get_data()

    assert left["sum_wo_vat"] == right["sum_wo_vat"] == pytest.approx(BASE_230K)
    assert left["sum_vat"] == right["sum_vat"] == pytest.approx(VAT_230K)
    assert left["sum_total"] == right["sum_total"] == pytest.approx(SUM_WITH_VAT_230K)


def test_price_words_are_about_total_in_both_modes(qt_app):
    """Сумма прописью — от итога и от режима не зависит."""
    without_vat = PriceTab()
    without_vat.sum_wo_vat.setValue(BASE_230K)

    with_vat = PriceTab()
    with_vat.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)
    with_vat.sum_wo_vat.setValue(SUM_WITH_VAT_230K)

    assert without_vat.sum_total_words.text() == with_vat.sum_total_words.text()
    assert without_vat.sum_total_words.text().startswith("Двести тридцать тысяч")


def test_price_zero_rate_in_both_modes_gives_equal_sums(qt_app):
    """«0%»: НДС нет, итог равен введённому числу в обоих режимах."""
    for mode in price_tab_module.AMOUNT_MODES:
        tab = PriceTab()
        tab.vat_rate.setCurrentText("0%")
        tab.amount_mode.setCurrentText(mode)
        tab.sum_wo_vat.setValue(IP_SUM)

        data = tab.get_data()
        assert data["sum_vat"] == 0.0, mode
        assert data["sum_wo_vat"] == pytest.approx(IP_SUM), mode
        assert data["sum_total"] == pytest.approx(IP_SUM), mode


def test_price_fill_data_base_uses_without_vat_mode(qt_app):
    """Документ с суммой без НДС заполняет вкладку в режиме «Без НДС»."""
    tab = PriceTab()
    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)

    tab.fill_data({"price_without_vat": SUM_WITHOUT_VAT, "vat_rate": "22%"})

    assert tab.amount_mode_text() == price_tab_module.MODE_WITHOUT_VAT
    assert tab.get_data()["sum_wo_vat"] == pytest.approx(SUM_WITHOUT_VAT)


def test_price_fill_data_total_only_uses_with_vat_mode(qt_app):
    """
    Документ, где есть только итог, заполняется в режиме «С НДС».

    Иначе единственная сумма документа получила бы НДС сверху и итог
    разошёлся бы с бумагой.
    """
    tab = PriceTab()

    tab.fill_data({"sum_total": 135833.0, "vat_rate": "0%", "vat_rate_num": 0.0})

    assert tab.amount_mode_text() == price_tab_module.MODE_WITH_VAT
    data = tab.get_data()
    assert data["sum_wo_vat"] == pytest.approx(135833.0)
    assert data["sum_total"] == pytest.approx(135833.0)


def test_price_fill_data_restores_with_vat_sums(qt_app):
    """
    Восстановление сохранённых сумм: числа не теряются и не сдвигаются.

    Режим ввода в данных не хранится — хранятся три суммы. Пара «база 188 524,59
    + итог 230 000,00» одинаково верна для режима «С НДС» и для режима
    «Без НДС» с той же базой, поэтому вкладка восстанавливает сумму БЕЗ НДС
    (так эта пара читается из документа) — а итог, НДС и прописью выходят те же.
    """
    tab = PriceTab()
    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)
    tab.sum_wo_vat.setValue(SUM_WITH_VAT_230K)
    saved = tab.get_data()

    restored = PriceTab()
    restored.fill_data(saved)
    data = restored.get_data()

    assert restored.amount_mode_text() == price_tab_module.MODE_WITHOUT_VAT
    assert data["sum_wo_vat"] == pytest.approx(BASE_230K)
    assert data["sum_vat"] == pytest.approx(VAT_230K)
    assert data["sum_total"] == pytest.approx(SUM_WITH_VAT_230K)
    assert restored.sum_total_words.text() == tab.sum_total_words.text()


def test_price_fill_data_restores_without_vat_sums(qt_app):
    """Восстановление сумм, сохранённых в режиме «Без НДС»."""
    tab = PriceTab()
    tab.sum_wo_vat.setValue(SUM_WITHOUT_VAT)
    saved = tab.get_data()

    restored = PriceTab()
    restored.fill_data(saved)
    data = restored.get_data()

    assert restored.amount_mode_text() == price_tab_module.MODE_WITHOUT_VAT
    assert data["sum_wo_vat"] == pytest.approx(SUM_WITHOUT_VAT)
    assert data["sum_vat"] == pytest.approx(SUM_VAT)
    assert data["sum_total"] == pytest.approx(SUM_TOTAL)


def test_price_fill_data_restores_total_only_as_with_vat(qt_app):
    """Единственная сумма документа восстанавливается как итог, а не база."""
    tab = PriceTab()

    tab.fill_data({"sum_total": SUM_WITH_VAT_230K, "vat_rate": "22%"})

    assert tab.amount_mode_text() == price_tab_module.MODE_WITH_VAT
    data = tab.get_data()
    assert data["sum_total"] == pytest.approx(SUM_WITH_VAT_230K)
    assert data["sum_wo_vat"] == pytest.approx(BASE_230K)


def test_price_fill_data_round_trip_is_stable(qt_app):
    """Повторное заполнение теми же данными ничего не сдвигает."""
    tab = PriceTab()
    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)
    tab.sum_wo_vat.setValue(SUM_WITH_VAT_230K)

    first = tab.get_data()
    tab.fill_data(first)
    second = tab.get_data()

    assert second == pytest.approx(first)


def test_price_clear_returns_default_mode(qt_app):
    """clear() возвращает режим по умолчанию, а не оставляет «С НДС»."""
    tab = PriceTab()
    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)

    tab.clear()

    assert tab.amount_mode_text() == price_tab_module.DEFAULT_AMOUNT_MODE
    assert tab.get_data()["sum_wo_vat"] == 0.0
    assert price_tab_module.BASE_LABEL in tab._sum_edit_label.text()


def test_price_vat_amount_plus_base_equals_total(qt_app):
    """База и НДС в сумме дают ровно итог — ни копейки мимо."""
    for mode in price_tab_module.AMOUNT_MODES:
        tab = PriceTab()
        tab.amount_mode.setCurrentText(mode)
        tab.sum_wo_vat.setValue(SUM_WITH_VAT_230K)

        data = tab.get_data()
        assert round(data["sum_wo_vat"] + data["sum_vat"], 2) \
            == pytest.approx(data["sum_total"]), mode


# ─────────────────────────────────────────────────────────────
# «Стоимость»: срок оплаты (FIX-1, БАГ 3)
# ─────────────────────────────────────────────────────────────

def test_price_has_payment_days_field(qt_app):
    """Поле «Срок оплаты, банковских дней» — целое число, по умолчанию 30."""
    tab = PriceTab()

    assert isinstance(tab.payment_days, QSpinBox)
    assert not isinstance(tab.payment_days, QDoubleSpinBox)
    assert tab.payment_days.value() == price_tab_module.DEFAULT_PAYMENT_DAYS
    assert tab.payment_days.value() == 30
    assert tab.get_data()["payment_days"] == 30


def test_price_payment_days_range(qt_app):
    """Границы срока: 0 — «срок не задан», больше 365 банковских дней нет."""
    tab = PriceTab()

    assert tab.payment_days.minimum() == price_tab_module.MIN_PAYMENT_DAYS == 0
    assert tab.payment_days.maximum() == price_tab_module.MAX_PAYMENT_DAYS == 365


def test_price_payment_days_accepts_value(qt_app):
    tab = PriceTab()

    tab.payment_days.setValue(45)

    assert tab.get_data()["payment_days"] == 45


def test_price_payment_days_rejects_negative(qt_app):
    """Отрицательного срока оплаты не бывает: поле не принимает минус."""
    tab = PriceTab()

    tab.payment_days.setValue(-5)

    assert tab.get_data()["payment_days"] == 0


def test_price_payment_days_fill_and_read_back(qt_app):
    tab = PriceTab()

    tab.fill_data({"sum_wo_vat": SUM_WITHOUT_VAT, "payment_days": 45})

    assert tab.get_data()["payment_days"] == 45


def test_price_payment_days_ignores_empty_value(qt_app):
    """Пустое значение не сбрасывает срок: у промпта пусто — «не было»."""
    tab = PriceTab()
    tab.payment_days.setValue(45)

    tab.fill_data({"payment_days": ""})
    tab.fill_data({"payment_days": None})

    assert tab.get_data()["payment_days"] == 45


def test_price_payment_days_ignores_garbage(qt_app):
    """Мусор вместо числа оставляет поле как есть, а не превращает в 0."""
    tab = PriceTab()
    tab.payment_days.setValue(45)

    tab.fill_data({"payment_days": "мусор"})

    assert tab.get_data()["payment_days"] == 45


def test_price_payment_days_is_independent_of_amount(qt_app):
    """Срок оплаты не зависит ни от суммы, ни от режима ввода."""
    tab = PriceTab()
    tab.payment_days.setValue(45)
    tab.sum_wo_vat.setValue(SUM_WITHOUT_VAT)

    before = tab.get_data()["payment_days"]
    tab.amount_mode.setCurrentText(price_tab_module.MODE_WITH_VAT)

    assert before == 45
    assert tab.get_data()["payment_days"] == 45


def test_price_payment_days_is_integer(qt_app):
    """В бланке печатается целое число банковских дней."""
    tab = PriceTab()
    tab.payment_days.setValue(45)

    assert isinstance(tab.get_data()["payment_days"], int)


# ─────────────────────────────────────────────────────────────
# «Акт» (Приложение № 1, шаг FIX-3)
# ─────────────────────────────────────────────────────────────

def test_act_has_ten_fields(qt_app):
    """Ровно десять полей Акта — по одному на строку таблиц бланка."""
    tab = ActTab()

    assert len(act_tab_module.ACT_FIELDS) == 10
    assert set(tab.get_data()) == set(act_tab_module.ACT_FIELDS)


def test_act_field_widgets(qt_app):
    """Место, дата, пробег и документы — строки; состояние — абзацы."""
    tab = ActTab()

    for name in ("transfer_place", "transfer_datetime", "transfer_mileage",
                 "transfer_documents", "return_place", "return_datetime",
                 "return_mileage"):
        assert isinstance(getattr(tab, name), PasteableLineEdit), name

    for name in ("transfer_condition", "return_condition", "return_notes"):
        assert isinstance(getattr(tab, name), PasteableTextEdit), name


def test_act_groups_are_named_like_the_blank(qt_app):
    """Группы вкладки названы как разделы Акта в бланке."""
    tab = ActTab()
    titles = [box.title() for box in tab.findChildren(QGroupBox)]

    assert "Передача ТС в аренду" in titles
    assert "Возврат ТС" in titles


def test_act_fill_and_read_back(qt_app):
    tab = ActTab()

    tab.fill_data(SAMPLE_DATA[ActTab])

    assert tab.get_data() == SAMPLE_DATA[ActTab]


def test_act_empty_values_keep_manual_input(qt_app):
    """Пустые значения ответа ручной ввод не стирают."""
    tab = ActTab()
    tab.fill_data(SAMPLE_DATA[ActTab])

    tab.fill_data({field: "" for field in act_tab_module.ACT_FIELDS})

    assert tab.get_data() == SAMPLE_DATA[ActTab]


def test_act_clear_empties_all_fields(qt_app):
    tab = ActTab()
    tab.fill_data(SAMPLE_DATA[ActTab])

    tab.clear()

    assert set(tab.get_data().values()) == {""}


def test_act_documents_field_is_empty_by_default(qt_app):
    """
    Перечень документов вкладка не подставляет.

    Значение по умолчанию печатает генератор
    (core/contracts/arenda_ts/generator.py::TRANSFER_DOCUMENTS), иначе поле
    выглядело бы заполненным без участия пользователя.
    """
    tab = ActTab()

    assert tab.get_data()["transfer_documents"] == ""
    assert "СТС" in act_tab_module.DOCUMENTS_TOOLTIP
    assert tab.transfer_documents.toolTip() == act_tab_module.DOCUMENTS_TOOLTIP


def test_act_data_goes_to_contract(filled_tabs):
    """Поля вкладки собираются в contract теми же ключами — их читает генератор."""
    contract = _build_from(filled_tabs).contract

    for field, value in SAMPLE_DATA[ActTab].items():
        assert contract[field] == value, field


def test_act_tab_is_optional_in_build(filled_tabs):
    """Сборка без вкладки «Акт» даёт те же данные, только без полей акта."""
    without_act = build(
        filled_tabs["lessee"], filled_tabs["lessor"], filled_tabs["vehicle"],
        filled_tabs["route"], filled_tabs["cargo"], filled_tabs["crew"],
        filled_tabs["price"],
    )

    assert not [key for key in act_tab_module.ACT_FIELDS
                if key in without_act.contract]
    assert without_act.contract["number"] == \
        _build_from(filled_tabs).contract["number"]


def test_empty_act_tab_leaves_contract_without_act_fields(qt_app):
    """Пустая вкладка «Акт» не оставляет в contract пустых ключей."""
    widgets = {key: factory() for key, factory in TAB_FACTORIES}
    try:
        contract = _build_from(widgets).contract
    finally:
        for widget in widgets.values():
            widget.deleteLater()

    assert not [key for key in act_tab_module.ACT_FIELDS if key in contract]


def test_act_fields_reach_the_generated_document(filled_tabs, templates_dir):
    """Значения вкладки «Акт» доходят до таблиц Приложения № 1."""
    from core.contracts.arenda_ts.generator import ArendaTsGenerator

    generator = ArendaTsGenerator(templates_dir=str(templates_dir))
    replacements = generator.build_replacements(_build_from(filled_tabs))

    for field, value in SAMPLE_DATA[ActTab].items():
        assert replacements[field] == value, field


# ─────────────────────────────────────────────────────────────
# Окно: восемь разделов
# ─────────────────────────────────────────────────────────────

def test_window_tab_configs_have_eight_tabs():
    assert ArendaTsWindow.TAB_CONFIGS == [
        ("Арендатор", "customer.svg"),
        ("Арендодатель", "carrier.svg"),
        ("ТС", "vehicles.svg"),
        ("Маршрут", "trailer.svg"),
        ("Груз", "contract.svg"),
        ("Экипаж", "driver.svg"),
        ("Стоимость", "contract.svg"),
        ("Акт", "contract.svg"),
    ]


def test_tab_classes_count_matches_window_configs():
    """Восемь вкладок написаны и восемь разделов объявлено в окне."""
    assert len(TAB_FACTORIES) == len(ArendaTsWindow.TAB_CONFIGS) == 8


def test_every_window_icon_exists():
    """Иконки разделов берутся из resources/icons/tabs и реально находятся."""
    from ui.icons import tab_icon

    for _title, icon_key in ArendaTsWindow.TAB_CONFIGS:
        assert tab_icon(icon_key).isNull() is False, icon_key


# ─────────────────────────────────────────────────────────────
# Сборка данных из настоящих вкладок
# ─────────────────────────────────────────────────────────────

def test_build_from_real_tabs_gives_contract_data(filled_tabs):
    contract_data = _build_from(filled_tabs)

    assert isinstance(contract_data, ContractData)
    assert contract_data.contract["number"] == "01/2026"
    assert contract_data.tractor["plate_number"] == "М342СА761"
    assert len(contract_data.vehicles) == 1


def test_build_from_real_tabs_splits_documents(filled_tabs):
    """Строки паспорта и ВУ разбирает сборщик: в данных — серия и номер."""
    driver = _build_from(filled_tabs).driver

    assert driver["passport_series"] == "18 22"
    assert driver["passport_number"] == "926830"
    assert driver["license_series"] == "99 36"
    assert driver["license_number"] == "123456"


def test_build_from_real_tabs_keeps_point_times(filled_tabs):
    """Время подачи ТС читает генератор — оно должно дойти до contract."""
    contract = _build_from(filled_tabs).contract

    assert contract["loadings"][0]["time_window"] == "08:00-18:00"
    assert contract["loadings"][0]["date"] == "2026-09-26"


def test_build_from_real_tabs_passes_validator(filled_tabs):
    """Данных настоящих вкладок хватает валидатору без ошибок и замечаний."""
    from core.contracts.arenda_ts.validator import ArendaTsValidator

    report = ArendaTsValidator().check(_build_from(filled_tabs))

    assert report.errors == [], report.errors
    assert report.warnings == [], report.warnings


def test_build_from_real_tabs_gives_generator_replacements(filled_tabs, templates_dir):
    """Генератор читает из вкладок ровно то, что ждёт."""
    from core.contracts.arenda_ts.generator import ArendaTsGenerator

    generator = ArendaTsGenerator(templates_dir=str(templates_dir))
    replacements = generator.build_replacements(_build_from(filled_tabs))

    assert replacements["contract_number"] == "01/2026"
    assert replacements["lessee_kpp"] == "770701001"
    assert replacements["cargo_count"] == "1"
    # Разделитель тысяч — неразрывный пробел, как в бланке.
    assert replacements["sum_wo_vat"] == "221\u00a0099,18"
    assert "Двести шестьдесят девять тысяч" in replacements["sum_total_words"]


def test_build_from_ip_tabs_has_no_kpp(filled_tabs):
    """ИП-вариант: КПП со вкладки не попадает в contract, основание — ИП."""
    filled_tabs["lessee"].carrier_type.setCurrentText("ИП без НДС")

    contract = _build_from(filled_tabs).contract

    assert "kpp" not in contract["lessee"]
    assert contract["carrier_type"] == "ИП без НДС"
    assert contract["lessee"]["ogrn_label"] == "ОГРНИП"


def test_build_from_empty_tabs_does_not_fail(qt_app):
    """Пустое окно собирается без исключений: разделы просто пустые."""
    widgets = {key: factory() for key, factory in TAB_FACTORIES}

    contract_data = _build_from(widgets)
    try:
        assert isinstance(contract_data, ContractData)
    finally:
        for widget in widgets.values():
            widget.deleteLater()
