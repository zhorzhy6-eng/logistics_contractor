#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты темы оформления и визуальной иерархии.

Проверяется то, что легко сломать при правках:
  * палитра и ключевые правила общего QSS;
  * главные/второстепенные кнопки (objectName + тексты не переименованы);
  * тёмно-синяя звёздочка у обязательных полей (не красная);
  * бледно-жёлтая подсветка пустого обязательного поля и её снятие;
  * блоки вкладки «Перевозчик» (в т.ч. отдельный блок «Адреса»);
  * соответствие звёздочек валидатору: КПП обязателен для ООО и не обязателен для ИП;
  * подсчёт заполненных полей для статус-бара.

Qt поднимается в offscreen-режиме: окна не показываются, тесты не зависят
от наличия дисплея.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QGroupBox  # noqa: E402

from ui import theme  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


# ─────────────────────────────────────────────────────────────
# Палитра и общий стиль
# ─────────────────────────────────────────────────────────────

def test_palette_values_are_stable():
    assert theme.NAVY == "#1E3A5F"
    assert theme.PRIMARY == "#1E5FA8"
    assert theme.ACCENT == "#2F855A"
    assert theme.WARN_BG == "#FFF8E1"
    # красный для звёздочек и подсветки не используется
    assert "red" not in theme.REQUIRED_EMPTY_QSS.lower()
    assert "#f00" not in theme.REQUIRED_EMPTY_QSS.lower()


def test_qss_covers_key_hierarchy_rules():
    stylesheet = theme.qss()

    # активная вкладка выделена жирным и полосой, неактивные приглушены
    assert "QTabBar::tab:selected" in stylesheet
    assert "font-weight: bold" in stylesheet
    assert theme.PRIMARY in stylesheet
    # блоки-карточки с заголовком
    assert "QGroupBox::title" in stylesheet
    # три вида кнопок
    for name in ("primary", "secondary", "accent", "ghost"):
        assert f"QPushButton#{name}" in stylesheet
    # статус-бар
    assert "QStatusBar" in stylesheet


def test_apply_theme_sets_application_stylesheet(qt_app):
    theme.apply_theme(qt_app)
    assert theme.PRIMARY in qt_app.styleSheet()


# ─────────────────────────────────────────────────────────────
# Кнопки: главные и второстепенные
# ─────────────────────────────────────────────────────────────

def test_button_factories_set_roles(qt_app):
    primary = theme.primary_button("Главная")
    secondary = theme.secondary_button("Второстепенная")
    accent = theme.accent_button("Создать договор")
    ghost = theme.ghost_button("📋")

    assert primary.objectName() == "primary"
    assert secondary.objectName() == "secondary"
    assert accent.objectName() == "accent"
    assert ghost.objectName() == "ghost"

    # главная кнопка вкладки крупнее вспомогательных
    assert primary.minimumHeight() > secondary.minimumHeight()
    assert accent.minimumHeight() >= secondary.minimumHeight()
    assert ghost.width() <= 32


def test_recognition_panel_button_texts_unchanged(qt_app):
    """Тексты кнопок — часть привычного интерфейса, их не переименовываем."""
    from ui.widgets import RecognitionPanel

    panel = RecognitionPanel()

    assert panel.btn_recognize.text() == "🧠 Распознать вкладку"
    assert panel.btn_paste.text() == "📋 Вставить из буфера"
    assert panel.btn_recognize.objectName() == "primary"
    assert panel.btn_paste.objectName() == "secondary"


# ─────────────────────────────────────────────────────────────
# Обязательные поля: звёздочка и подсветка
# ─────────────────────────────────────────────────────────────

def test_required_label_uses_navy_star(qt_app):
    required = theme.required_label("ИНН")
    optional = theme.make_label("ОГРН")

    assert required.textFormat() == 1  # Qt.RichText
    assert "*" in required.text()
    assert theme.PRIMARY in required.text()
    assert "*" not in optional.text()


def test_set_label_required_toggles_star(qt_app):
    label = theme.required_label("КПП")
    theme.set_label_required(label, "КПП", False)
    assert "*" not in label.text()
    theme.set_label_required(label, "КПП", True)
    assert "*" in label.text()


def test_line_edit_highlight_appears_and_clears(qt_app):
    from ui.widgets import PasteableLineEdit

    field = PasteableLineEdit("ИНН")
    field.set_required(True)

    # пустое обязательное поле подсвечено бледно-жёлтым
    assert theme.WARN_BG in field.line_edit.styleSheet()
    assert field.is_empty() is True

    # заполнили — подсветка снята
    field.setText("7707654321")
    assert field.line_edit.styleSheet() == ""
    assert field.is_empty() is False

    # очистили — подсветка вернулась
    field.clear()
    assert theme.WARN_BG in field.line_edit.styleSheet()


def test_non_required_field_is_never_highlighted(qt_app):
    from ui.widgets import PasteableLineEdit

    field = PasteableLineEdit("ОГРН")
    assert field.is_required() is False
    assert field.line_edit.styleSheet() == ""


def test_text_edit_highlight_toggles(qt_app):
    from ui.widgets import PasteableTextEdit

    area = PasteableTextEdit("Адрес")
    area.set_required(True)
    assert theme.WARN_BG in area.text_edit.styleSheet()

    area.setPlainText("г. Москва")
    assert area.text_edit.styleSheet() == ""


# ─────────────────────────────────────────────────────────────
# Вкладка «Перевозчик»
# ─────────────────────────────────────────────────────────────

def test_carrier_tab_has_semantic_sections(qt_app):
    from ui.tabs.carrier_tab import CarrierTab

    tab = CarrierTab()
    titles = [box.title() for box in tab.findChildren(QGroupBox)]

    assert titles == [
        "Тип перевозчика",
        "Общие сведения",
        "Адреса",                 # вынесены из «Общих сведений» отдельным блоком
        "Банковские реквизиты",
        "Руководитель и контакты",
        "Лицензия на перевозки",
    ]


def test_carrier_tab_required_fields_match_validator(qt_app):
    """Звёздочки стоят там, где пустое поле действительно даёт ошибку."""
    from ui.tabs.carrier_tab import CarrierTab

    tab = CarrierTab()

    assert tab.full_name.is_required() is True
    assert tab.inn.is_required() is True
    assert tab.kpp.is_required() is True
    assert tab.bank_account.is_required() is True
    assert tab.bik.is_required() is True

    # валидатор их не требует — значит и звёздочки/подсветки нет
    for field in (
        tab.short_name, tab.ogrn, tab.legal_address, tab.actual_address,
        tab.correspondent_account, tab.bank_name,
        tab.director_name, tab.director_position, tab.phone, tab.email,
    ):
        assert field.is_required() is False


def test_carrier_tab_kpp_not_required_for_ip(qt_app):
    from ui.tabs.carrier_tab import CarrierTab

    tab = CarrierTab()
    assert tab.kpp.is_required() is True          # по умолчанию ООО

    tab.carrier_type.setCurrentIndex(2)           # «ИП без НДС»
    assert tab.kpp.is_required() is False
    assert "*" not in tab.kpp_label.text()
    assert tab.kpp.line_edit.styleSheet() == ""

    tab.carrier_type.setCurrentIndex(0)           # обратно на ООО
    assert tab.kpp.is_required() is True
    assert "*" in tab.kpp_label.text()


def test_carrier_tab_count_filled_fields(qt_app):
    from ui.tabs.carrier_tab import CarrierTab

    tab = CarrierTab()
    filled, total = tab.count_filled_fields()

    assert total == len(tab.get_data())
    assert 0 < filled < total          # тип и ставка НДС заполнены по умолчанию

    tab.full_name.setText("ООО «Тест»")
    filled_after, _ = tab.count_filled_fields()
    assert filled_after == filled + 1


def test_count_filled_fields_survives_broken_get_data():
    """Счётчик статус-бара не должен падать из-за вкладки."""
    from ui.tabs.base_tab import TabMixin

    class Broken(TabMixin):
        def get_data(self):
            raise RuntimeError("нет данных")

    assert Broken().count_filled_fields() == (0, 0)
