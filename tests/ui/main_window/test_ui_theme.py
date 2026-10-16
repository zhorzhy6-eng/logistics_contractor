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

    assert panel.btn_recognize.text() == "Распознать вкладку"
    assert panel.btn_paste.text() == "Вставить из буфера"
    assert panel.btn_recognize.objectName() == "primary"
    assert panel.btn_paste.objectName() == "clipboard"


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


# ─────────────────────────────────────────────────────────────
# Выбор темы оформления (добавлено вместе с переключателем тем)
#
# Активная тема — состояние всего процесса, поэтому каждый тест, который её
# меняет, обязан вернуть «Классику» в finally: иначе следующие тесты увидят
# чужую палитру.
# ─────────────────────────────────────────────────────────────

def _restore_classic(qt_app):
    """Возвращает светлую тему — оформление по умолчанию."""
    theme.apply_theme(qt_app, "classic")


def test_all_themes_have_complete_palette():
    """У каждой темы заполнены все роли: шаблон QSS не зависит от темы."""
    from ui import theme_palettes

    reference = set(theme_palettes.CLASSIC)
    for key in theme.theme_names():
        palette = theme.palette(key)
        assert set(palette) == reference, f"тема {key}: расходится набор ролей"
        for role, value in palette.items():
            if role == "dark":
                continue
            assert str(value) != "", f"тема {key}: пустая роль {role}"


def test_every_theme_renders_qss_with_key_rules():
    required_rules = (
        "QTabBar::tab:selected",
        "QGroupBox::title",
        "QStatusBar",
        "QPushButton#primary",
        "QPushButton#secondary",
        "QPushButton#accent",
        "QPushButton#ghost",
        "QFrame#sideNav",
        "QPushButton#navItem",
    )
    for key in theme.theme_names():
        stylesheet = theme.qss(key)
        assert theme.palette(key)["primary"] in stylesheet
        assert "None" not in stylesheet
        for rule in required_rules:
            assert rule in stylesheet, f"тема {key}: нет правила {rule}"


def test_apply_theme_switches_stylesheet_and_qt_palette(qt_app):
    from PyQt5.QtGui import QPalette

    try:
        theme.apply_theme(qt_app, "dark_pro")

        assert theme.active_theme() == "dark_pro"
        assert theme.is_dark() is True
        assert theme.palette("dark_pro")["primary"] in qt_app.styleSheet()
        window_color = qt_app.palette().color(QPalette.Window).name().lower()
        assert window_color == "#070b14"          # фон окна из макета 02
    finally:
        _restore_classic(qt_app)

    assert theme.active_theme() == "classic"
    assert theme.PRIMARY in qt_app.styleSheet()


def test_apply_theme_without_name_stays_classic(qt_app):
    """Совместимость: прежний вызов apply_theme(app) даёт светлую тему."""
    try:
        theme.apply_theme(qt_app, "dark_pro")
        theme.apply_theme(qt_app)

        assert theme.active_theme() == "classic"
        assert theme.PRIMARY in qt_app.styleSheet()
    finally:
        _restore_classic(qt_app)


def test_theme_names_and_aliases():
    """Тем ровно две, названия — «Светлая тема» и «Тёмная тема»."""
    from ui import theme_palettes

    assert theme.theme_names() == ["classic", "dark_pro"]
    assert theme.theme_label("classic") == "Светлая тема"
    assert theme.theme_label("dark_pro") == "Тёмная тема"

    # ID тем не менялись: сохранённый выбор в settings.json продолжает работать
    assert theme.normalize("classic") == "classic"
    assert theme.normalize("dark_pro") == "dark_pro"
    assert theme.normalize("dark-pro") == "dark_pro"

    # значения прежних версий (Fluent) приводятся к светлой/тёмной теме
    assert theme.normalize("fluent_light") == "classic"
    assert theme.normalize("fluent_dark") == "dark_pro"

    assert theme.normalize("неизвестная тема") == theme_palettes.DEFAULT_THEME
    assert theme.normalize("") == theme_palettes.DEFAULT_THEME

    assert dict(theme.available_themes()) == {
        "classic": "Светлая тема",
        "dark_pro": "Тёмная тема",
    }


def test_two_themes_differ_by_palette():
    """
    Светлая и тёмная темы различаются палитрой (актуализировано: тем стало
    две, прежний тест про пару Fluent переписан на пару classic/dark_pro).
    """
    light = theme.palette("classic")
    dark = theme.palette("dark_pro")

    assert light["window_bg"] != dark["window_bg"]
    assert light["surface"] != dark["surface"]
    assert theme.is_dark("classic") is False
    assert theme.is_dark("dark_pro") is True


def test_theme_choice_is_saved_and_loaded(work_file):
    """Выбор темы сохраняется в settings.json и читается при следующем старте."""
    from core.settings_service import SettingsService

    path = work_file("settings.json")
    service = SettingsService(str(path))
    assert service.update({"ui_theme": "dark_pro"}) is True

    reloaded = SettingsService(str(path))
    assert reloaded.get_str("ui_theme", "classic") == "dark_pro"

    # Нет ключа — используется «Светлая тема»: старые файлы настроек работают
    empty = SettingsService(str(work_file("empty.json")))
    assert empty.get_str("ui_theme", "classic") == "classic"


def test_switching_theme_keeps_form_data(qt_app):
    """Переключение темы не трогает данные формы."""
    from ui.tabs.carrier_tab import CarrierTab

    tab = CarrierTab()
    tab.full_name.setText("ООО «Тема»")
    tab.inn.setText("7707654321")
    before = tab.get_data()

    try:
        theme.apply_theme(qt_app, "dark_pro")

        assert tab.get_data() == before
        assert tab.full_name.text() == "ООО «Тема»"
        assert tab.inn.text() == "7707654321"
    finally:
        _restore_classic(qt_app)


def test_required_highlight_follows_active_theme(qt_app):
    """Подсветка пустого обязательного поля перекрашивается вместе с темой."""
    from ui.widgets import PasteableLineEdit

    field = PasteableLineEdit("ИНН")
    field.set_required(True)
    assert theme.WARN_BG in field.line_edit.styleSheet()

    try:
        theme.apply_theme(qt_app, "dark_pro")

        assert theme.palette("dark_pro")["warn_bg"] in field.line_edit.styleSheet()
    finally:
        _restore_classic(qt_app)

    assert theme.WARN_BG in field.line_edit.styleSheet()


def test_readonly_field_and_deleted_row_colors_come_from_theme(qt_app):
    try:
        theme.apply_theme(qt_app, "dark_pro")
        palette = theme.palette("dark_pro")

        assert palette["disabled_bg"] in theme.readonly_field_qss()
        assert theme.deleted_row_color().name().lower() == palette["danger"].lower()
    finally:
        _restore_classic(qt_app)


def test_settings_dialog_lists_themes_and_returns_choice(qt_app):
    """Диалог настроек показывает обе темы и отдаёт выбранную."""
    from ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog({"ui_theme": "classic"})
    try:
        assert dialog.theme_combo.count() == len(theme.theme_names())

        index = dialog.theme_combo.findData("dark_pro")
        dialog.theme_combo.setCurrentIndex(index)
        assert dialog.get_settings()["ui_theme"] == "dark_pro"

        index = dialog.theme_combo.findData("classic")
        dialog.theme_combo.setCurrentIndex(index)
        assert dialog.get_settings()["ui_theme"] == "classic"
    finally:
        dialog.close()


def test_main_window_sidebar_switches_tabs_and_theme(qt_app, monkeypatch):
    """Сайдбар переключает прежние вкладки; тема применяется программно."""
    from PyQt5.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    window = MainWindow()
    try:
        # навигация — сайдбар, страницы остались тем же QTabWidget
        assert window.side_nav.count() == window.tabs.count()
        assert window.tabs.tabBar().isHidden() is True

        window.side_nav.buttons()[2].click()
        assert window.tabs.currentIndex() == 2
        assert window.side_nav.current_index() == 2

        # программный переход тоже подсвечивает пункт сайдбара
        window.tabs.setCurrentIndex(0)
        assert window.side_nav.current_index() == 0

        # тема применяется программно: переключатель живёт в «Настройках»
        window._apply_theme_choice("dark_pro", save=False)
        assert theme.active_theme() == "dark_pro"
    finally:
        _restore_classic(qt_app)
        window.system_theme_watcher.stop()
        window.close()


def test_main_window_has_no_theme_button(qt_app, monkeypatch):
    """Кнопка «Тема» из шапки удалена: переключение только в настройках."""
    from PyQt5.QtWidgets import QMessageBox, QToolButton

    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    window = MainWindow()
    try:
        assert not hasattr(window, "theme_button")

        theme_pickers = [
            button
            for button in window.findChildren(QToolButton)
            if button.objectName() == "themePicker"
        ]
        assert theme_pickers == []

        # шапка содержит только финальное действие
        assert window.btn_create_contract.parent() is not None
    finally:
        window.system_theme_watcher.stop()
        window.close()


# ─────────────────────────────────────────────────────────────
# Тема Windows: определение режима и слежение
#
# Реестр подменяется заглушкой: тесты не должны зависеть от того, в каком
# режиме оформлена машина, на которой они запускаются.
# ─────────────────────────────────────────────────────────────

class _FakeRegistryKey:
    """Контекстный менеджер вместо открытого ключа реестра."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _install_fake_registry(monkeypatch, value=None, error=None):
    """
    Подменяет winreg и платформу на Windows.

    :param value: значение AppsUseLightTheme (0 — тёмная, 1 — светлая).
    :param error: исключение, которое должен бросить OpenKey.
    """
    import sys as _sys
    import types

    fake = types.ModuleType("winreg")
    fake.HKEY_CURRENT_USER = object()
    fake.QueryValueEx = lambda key, name: (value, 4)

    def open_key(root, path):
        if error is not None:
            raise error
        return _FakeRegistryKey()

    fake.OpenKey = open_key
    monkeypatch.setitem(_sys.modules, "winreg", fake)
    monkeypatch.setattr(_sys, "platform", "win32")


def test_read_system_theme_maps_registry_value(monkeypatch):
    """AppsUseLightTheme: 0 — тёмная тема, 1 — светлая."""
    from ui import system_theme

    _install_fake_registry(monkeypatch, value=0)
    assert system_theme.read_system_theme() == "dark_pro"

    _install_fake_registry(monkeypatch, value=1)
    assert system_theme.read_system_theme() == "classic"


def test_read_system_theme_returns_none_when_unavailable(monkeypatch):
    from ui import system_theme

    _install_fake_registry(monkeypatch, error=OSError("ключа нет"))
    assert system_theme.read_system_theme() is None

    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "linux")
    assert system_theme.read_system_theme() is None


def test_preferred_theme_follows_system_until_manual_choice(work_file, monkeypatch):
    """Системный режим применяется, пока пользователь не выбрал тему сам."""
    from core.settings_service import SettingsService
    from ui import system_theme

    monkeypatch.setattr(system_theme, "read_system_theme", lambda: "dark_pro")
    service = SettingsService(str(work_file("settings.json")))

    # по умолчанию следование включено — берём режим Windows
    assert service.get_bool("ui_theme_follow_system", True) is True
    assert system_theme.preferred_theme(service) == "dark_pro"

    # ручной выбор приоритетнее системного
    service.update({"ui_theme": "classic", "ui_theme_follow_system": False})
    assert system_theme.preferred_theme(service) == "classic"

    # система недоступна — тоже остаётся ручной выбор
    service.update({"ui_theme": "dark_pro", "ui_theme_follow_system": True})
    monkeypatch.setattr(system_theme, "read_system_theme", lambda: None)
    assert system_theme.preferred_theme(service) == "dark_pro"


def test_system_theme_watcher_emits_only_on_change(qt_app, monkeypatch):
    """Сигнал приходит только при фактической смене режима Windows."""
    from ui import system_theme

    values = iter(["classic", "classic", "dark_pro", "dark_pro"])
    monkeypatch.setattr(
        system_theme, "read_system_theme", lambda: next(values, "dark_pro")
    )

    watcher = system_theme.SystemThemeWatcher(interval_ms=0)
    received = []
    watcher.theme_changed.connect(received.append)

    assert watcher.start() == "classic"
    watcher.check_now()      # без изменений — сигнала нет
    watcher.check_now()      # режим сменился — сигнал
    watcher.check_now()      # снова без изменений
    watcher.stop()

    assert received == ["dark_pro"]
    assert watcher.current() == "dark_pro"


class _FakeSettings:
    """Настройки-заглушка: только то, что читает MainWindow."""

    def __init__(self, follow=True, manual="classic"):
        self.follow = follow
        self.manual = manual

    def get_bool(self, key, default=False):
        return self.follow if key == "ui_theme_follow_system" else default

    def get_str(self, key, default=""):
        return self.manual if key == "ui_theme" else default

    def update(self, values, save=True):
        return True


def test_main_window_applies_system_theme_change(qt_app, monkeypatch):
    """Смена режима Windows переключает тему, пока выбор не ручной."""
    from PyQt5.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr(
        MainWindow, "_init_gigachat_client", lambda self, show_dialog=True: False
    )
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    window = MainWindow()
    try:
        window.system_theme_watcher.stop()

        # следование включено — системное изменение применяется
        window.settings_service = _FakeSettings(follow=True)
        window._on_system_theme_changed("dark_pro")
        assert theme.active_theme() == "dark_pro"

        # ручной выбор: системные изменения игнорируются
        _restore_classic(qt_app)
        window.settings_service = _FakeSettings(follow=False)
        window._on_system_theme_changed("dark_pro")
        assert theme.active_theme() == "classic"
    finally:
        _restore_classic(qt_app)
        window.close()


def test_settings_dialog_follow_default_is_true(qt_app):
    """Следование за Windows включено по умолчанию."""
    from ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog({"ui_theme": "classic"})
    try:
        assert dialog.follow_system_checkbox.isChecked() is True
        assert dialog.get_settings()["ui_theme_follow_system"] is True
    finally:
        dialog.close()


def test_settings_dialog_manual_choice_disables_follow(qt_app, monkeypatch):
    """Ручной выбор темы в списке отключает следование за Windows."""
    from ui import system_theme
    from ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(system_theme, "read_system_theme", lambda: "dark_pro")

    dialog = SettingsDialog({"ui_theme": "classic", "ui_theme_follow_system": True})
    try:
        index = dialog.theme_combo.findData("dark_pro")
        dialog.theme_combo.setCurrentIndex(index)

        assert dialog.follow_system_checkbox.isChecked() is False
        settings = dialog.get_settings()
        assert settings["ui_theme"] == "dark_pro"
        assert settings["ui_theme_follow_system"] is False
    finally:
        dialog.close()


def test_settings_dialog_follow_picks_system_theme(qt_app, monkeypatch):
    """Включение флажка показывает в списке тему, выбранную в Windows."""
    from ui import system_theme
    from ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(system_theme, "read_system_theme", lambda: "dark_pro")

    dialog = SettingsDialog({"ui_theme": "classic", "ui_theme_follow_system": False})
    try:
        dialog.follow_system_checkbox.setChecked(True)

        assert dialog.get_settings()["ui_theme"] == "dark_pro"
        assert dialog.get_settings()["ui_theme_follow_system"] is True
    finally:
        dialog.close()
