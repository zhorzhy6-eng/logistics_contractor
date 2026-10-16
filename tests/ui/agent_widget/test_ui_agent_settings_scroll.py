#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Баг #19: окно настроек Максимилиана — прокрутка, кнопки, сохранение.

Что было не так
---------------
Окно настроек собиралось ОДНИМ столбцом без прокрутки: `QScrollArea` в нём
не было вовсе, а `sizeHint` раскладки просил 995 px. На рабочем столе
1920x1032 диалог открывался высотой 941 px — 91 % экрана, и его нижний
край уходил за границу рабочего стола вместе с кнопкой «Сохранить»
(замер: `tests/_tmp/_settings_fix/_diag.log`). Снять галочку «плавающий
кот» и сохранить это было физически нечем, поэтому настройка и «не
сохранялась».

Что проверяется здесь
---------------------
1. **Прокрутка.** Содержимое лежит в `QScrollArea`, кнопки «Сохранить» и
   «Отмена» — ВНЕ неё, отдельным рядом: они видны и при прокрутке в самый
   низ, и при прокрутке вверх (части B.1 и B.3).
2. **Высота.** Окно не выше доли экрана (B.2): проверяется расчёт по
   настоящей геометрии экрана, а не зашитое число.
3. **Сохранение.** Значения из диалога уходят в файл настроек, и обратное
   чтение даёт то же самое — в том числе `show_floating = false` (B.4, B.5).
4. **Выключенный помощник.** При `enabled = false` поля образа, режима,
   кота, клавиши и лимитов неактивны; при обратном включении значения не
   теряются (B.6).
5. **Кот.** `show_floating = false` — кот не создаётся; `true` — создаётся
   и виден.

Qt-объекты живут до конца процесса, поэтому окон в файле немного и каждое
закрывается явно (см. предупреждение в tests/test_agent_dialogs.py).
"""

import json
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

from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QScrollArea,
    QVBoxLayout, QWidget,
)

from core.settings_service import SettingsService  # noqa: E402
from ui import theme  # noqa: E402
from ui.agent_settings_dialog import (  # noqa: E402
    MAX_SCREEN_SHARE, MIN_SCROLL_HEIGHT, AgentSettingsDialog,
)


@pytest.fixture(scope="module")
def app():
    """QApplication на весь модуль (Qt допускает только один)."""
    application = QApplication.instance() or QApplication([])
    return application


@pytest.fixture(autouse=True)
def flush_events():
    """Прокручивает события после каждого теста (очередь удалений Qt)."""
    yield
    application = QApplication.instance()
    if application is not None:
        application.processEvents()


@pytest.fixture
def settings_path(work_file):
    """Отдельный файл настроек: рабочий config оператора не трогаем."""
    return work_file("agent_settings_scroll.json")


@pytest.fixture
def service(settings_path):
    """Сервис настроек с разделом agent по умолчанию."""
    instance = SettingsService(str(settings_path))
    instance.update({"agent": dict(SettingsService.DEFAULTS["agent"])})
    return instance


@pytest.fixture
def dialog(app, service):
    """Открытый диалог настроек (закрывается после теста)."""
    window = AgentSettingsDialog(service.agent_settings())
    window.show()
    app.processEvents()
    yield window
    window.close()
    app.processEvents()


# ─────────────────────────────────────────────────────────────
# 1. Прокрутка и кнопки (части B.1, B.3)
# ─────────────────────────────────────────────────────────────

def test_dialog_has_scroll_area(dialog):
    """Содержимое диалога лежит в области прокрутки (баг #19)."""
    areas = dialog.findChildren(QScrollArea)
    assert len(areas) == 1, "ровно одна область прокрутки"
    assert areas[0].widget() is dialog.content
    assert areas[0].widgetResizable() is True


def test_dialog_buttons_are_outside_scroll_area(dialog):
    """
    Кнопки «Сохранить»/«Отмена» не внутри прокрутки (часть B.1).

    Если бы они уехали внутрь, вместе с содержимым их можно было бы
    «прокрутить» за пределы окна — ровно та беда и была.
    """
    area = dialog.findChildren(QScrollArea)[0]
    for button in (dialog.btn_save, dialog.btn_cancel):
        parent = button.parentWidget()
        while parent is not None:
            assert parent is not area, (
                f"{button.text()}: кнопка внутри прокрутки"
            )
            parent = parent.parentWidget()


def test_save_button_visible_at_any_scroll_position(app, dialog):
    """Кнопка «Сохранить» видна и сверху, и при прокрутке в самый низ."""
    area = dialog.findChildren(QScrollArea)[0]
    bar = area.verticalScrollBar()

    for value in (0, bar.maximum()):
        bar.setValue(value)
        app.processEvents()
        top = dialog.btn_save.mapTo(
            dialog, dialog.btn_save.rect().topLeft()).y()
        bottom = dialog.btn_save.mapTo(
            dialog, dialog.btn_save.rect().bottomRight()).y()
        assert dialog.btn_save.isVisible(), f"прокрутка {value}: кнопки не видно"
        assert 0 <= top and bottom <= dialog.height(), (
            f"прокрутка {value}: кнопка за границей окна "
            f"({top}..{bottom} при высоте {dialog.height()})"
        )


def test_dialog_height_is_limited_to_screen_share(app, dialog):
    """
    Высота окна не больше доли экрана (часть B.2).

    Считается по настоящему экрану, а не по зашитому числу: на большом
    мониторе предел выше, на маленьком — ниже.
    """
    screen = dialog.screen() or app.primaryScreen()
    available = screen.availableGeometry().height()
    allowed = int(available * MAX_SCREEN_SHARE)
    assert dialog.height() <= allowed, (
        f"окно {dialog.height()} px выше предела {allowed} px "
        f"(экран {available} px)"
    )
    assert dialog.maximumHeight() <= allowed
    assert dialog.maximumHeight() >= MIN_SCROLL_HEIGHT


def test_working_height_shows_several_sections(app, dialog):
    """Окно открывается выше минимума — видно несколько разделов сразу."""
    assert dialog.height() > MIN_SCROLL_HEIGHT
    area = dialog.findChildren(QScrollArea)[0]
    assert area.viewport().height() > 0


# ─────────────────────────────────────────────────────────────
# 2. Сохранение значений (части B.4, B.5)
# ─────────────────────────────────────────────────────────────

def test_all_fields_are_saved_to_file(settings_path, service):
    """Все галочки/поля диалога попадают в файл настроек (B.5)."""
    window = AgentSettingsDialog(service.agent_settings())
    window.enabled.setChecked(True)
    window.avatar_combo.setCurrentIndex(
        window.avatar_combo.findData("cat_detective"))
    window.mode_combo.setCurrentIndex(window.mode_combo.findData("panel"))
    window.show_floating.setChecked(True)
    window.show_on_start.setChecked(True)
    window.hotkey_edit.setKeySequence("Ctrl+Shift+K")
    window.max_history.setValue(33)
    window.max_steps.setValue(4)
    window.proactive.setChecked(True)
    window.proactive_interval.setValue(120)

    assert service.update_agent(window.get_settings()) is True
    window.close()

    with open(settings_path, "r", encoding="utf-8") as file:
        raw = json.load(file)["agent"]
    assert raw["enabled"] is True
    assert raw["avatar"] == "cat_detective"
    assert raw["mode"] == "panel"
    assert raw["show_floating"] is True
    assert raw["show_panel_on_start"] is True
    assert raw["hotkey"] == "Ctrl+Shift+K"
    assert raw["max_history"] == 33
    assert raw["max_tool_steps"] == 4
    assert raw["proactive"] is True
    assert raw["proactive_interval"] == 120


def test_unchecked_floating_cat_is_saved_as_false(settings_path, service):
    """Снятие галочки «плавающий кот» записывается как false (B.4)."""
    assert service.agent_settings()["show_floating"] is False

    window = AgentSettingsDialog(service.agent_settings())
    window.show_floating.setChecked(True)
    service.update_agent(window.get_settings())
    window.close()

    with open(settings_path, "r", encoding="utf-8") as file:
        assert json.load(file)["agent"]["show_floating"] is True

    #: Оператор снимает галочку и жмёт «Сохранить».
    second = AgentSettingsDialog(service.agent_settings())
    assert second.show_floating.isChecked() is True, "диалог открылся на текущем"
    second.show_floating.setChecked(False)
    service.update_agent(second.get_settings())
    second.close()

    with open(settings_path, "r", encoding="utf-8") as file:
        assert json.load(file)["agent"]["show_floating"] is False


def test_saved_settings_are_read_back_by_new_dialog(settings_path, service):
    """После перезапуска диалог открывается на сохранённых значениях (C.4)."""
    window = AgentSettingsDialog(service.agent_settings())
    window.show_floating.setChecked(True)
    window.mode_combo.setCurrentIndex(window.mode_combo.findData("icon"))
    service.update_agent(window.get_settings())
    window.close()

    #: Новый сервис — как после перезапуска программы: читаем файл заново.
    restarted = SettingsService(str(settings_path))
    assert restarted.agent_settings()["show_floating"] is True
    assert restarted.agent_settings()["mode"] == "icon"

    again = AgentSettingsDialog(restarted.agent_settings())
    assert again.show_floating.isChecked() is True
    assert again.mode_combo.currentData() == "icon"
    again.close()


def test_memory_records_are_shown_in_dialog(app, service, monkeypatch):
    """Раздел «Что Максимилиан помнит» показывает записи памяти (часть B.4)."""
    import ui.agent_settings_dialog as module

    monkeypatch.setattr(module.long_memory, "list_records", lambda **_: [
        {"id": 7, "kind": "alias", "key": "Ромашка",
         "value": "ООО «Ромашка»", "kind_title": "Алиас",
         "kind_hint": "сокращение"},
    ])
    window = AgentSettingsDialog(service.agent_settings())
    assert window.memory_table.rowCount() == 1
    assert window.memory_table.item(0, 1).text() == "Ромашка"
    assert window.memory_table.item(0, 2).text() == "ООО «Ромашка»"
    assert "1" in window.memory_hint.text()
    window.close()


# ─────────────────────────────────────────────────────────────
# 3. Выключенный помощник: поля серые (часть B.6)
# ─────────────────────────────────────────────────────────────

def _dependent_widgets(dialog):
    """Поля, которые обязаны гаснуть вместе с «Включить Максимилиана»."""
    return {
        "avatar": dialog.avatar_combo,
        "mode": dialog.mode_combo,
        "floating": dialog.show_floating,
        "on_start": dialog.show_on_start,
        "hotkey": dialog.hotkey_edit,
        "max_history": dialog.max_history,
        "max_steps": dialog.max_steps,
        "proactive": dialog.proactive,
        "interval": dialog.proactive_interval,
        "clear_history": dialog.btn_clear_history,
        "purge_sessions": dialog.btn_purge_sessions,
    }


def test_enabled_false_disables_every_other_field(dialog):
    """enabled = false → все настройки неактивны (часть B.6)."""
    dialog.enabled.setChecked(False)
    states = {name: widget.isEnabled()
              for name, widget in _dependent_widgets(dialog).items()}
    active = sorted(name for name, on in states.items() if on)
    assert active == [], f"при выключенном помощнике активны: {active}"


def test_dialog_buttons_stay_enabled_when_agent_is_off(dialog):
    """Кнопки самого диалога остаются рабочими: выйти можно всегда."""
    dialog.enabled.setChecked(False)
    assert dialog.btn_save.isEnabled() is True
    assert dialog.btn_cancel.isEnabled() is True


def test_enabling_back_restores_fields_without_losing_values(dialog):
    """Обратное включение возвращает поля и НЕ сбрасывает выбор (B.6)."""
    dialog.avatar_combo.setCurrentIndex(
        dialog.avatar_combo.findData("cat_keeper"))
    dialog.enabled.setChecked(False)
    dialog.enabled.setChecked(True)

    assert dialog.avatar_combo.isEnabled() is True
    assert dialog.avatar_combo.currentData() == "cat_keeper"
    assert dialog.get_settings()["avatar"] == "cat_keeper"


def test_interval_depends_on_proactive_checkbox(dialog):
    """Пауза подсказок активна только при включённых подсказках."""
    dialog.enabled.setChecked(True)
    dialog.proactive.setChecked(False)
    assert dialog.proactive_interval.isEnabled() is False

    dialog.proactive.setChecked(True)
    assert dialog.proactive_interval.isEnabled() is True

    dialog.enabled.setChecked(False)
    assert dialog.proactive_interval.isEnabled() is False


def test_get_settings_survives_disabled_state(dialog):
    """Выключение помощника не меняет остальные значения в get_settings."""
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("panel"))
    dialog.max_steps.setValue(7)
    dialog.enabled.setChecked(False)

    values = dialog.get_settings()
    assert values["enabled"] is False
    assert values["mode"] == "panel"
    assert values["max_tool_steps"] == 7


# ─────────────────────────────────────────────────────────────
# 4. Плавающий кот (часть B.4)
# ─────────────────────────────────────────────────────────────

class StubWindow(QMainWindow):
    """Минимальное окно с шапкой: агент ставится и в такое."""

    CONTRACT_TYPE = "perevozka"

    def __init__(self):
        super().__init__()
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        header = QFrame()
        header.setObjectName("appHeader")
        QHBoxLayout(header).addWidget(QLabel("Заголовок"))
        layout.addWidget(header)


@pytest.fixture
def stub_window(app):
    """Окно-заготовка, закрывается после теста."""
    window = StubWindow()
    window.show()
    app.processEvents()
    yield window
    window.close()
    app.processEvents()


def test_show_floating_false_creates_no_cat(app, service, stub_window,
                                            monkeypatch):
    """show_floating = false → плавающий кот НЕ создаётся (часть B.4)."""
    service.update_agent({"enabled": True, "show_floating": False})

    import ui.agent_widget as widget_module

    monkeypatch.setattr(widget_module, "get_settings_service", lambda: service)

    widget = widget_module.install_agent(stub_window,
                                         client_factory=lambda: None)
    app.processEvents()
    assert widget is not None, "включённый помощник должен установиться"
    assert widget.floating is None, "кота не должно быть вовсе"
    widget.shutdown()


def test_show_floating_true_creates_visible_cat(app, service, stub_window,
                                                monkeypatch):
    """show_floating = true → кот создан и виден (часть B.4)."""
    service.update_agent({"enabled": True, "show_floating": True})

    import ui.agent_widget as widget_module

    monkeypatch.setattr(widget_module, "get_settings_service", lambda: service)

    widget = widget_module.install_agent(stub_window,
                                         client_factory=lambda: None)
    app.processEvents()
    assert widget.floating is not None
    assert widget.floating.isVisible() is True
    widget.shutdown()


def test_saving_unchecked_cat_removes_it_on_next_run(app, settings_path,
                                                     service, stub_window,
                                                     monkeypatch):
    """
    Полный цикл части C: сняли галочку → сохранили → кот не создаётся.

    Повторный запуск эмулируется новым сервисом настроек: он читает тот же
    файл с диска, как это делает программа при старте.
    """
    service.update_agent({"enabled": True, "show_floating": True})

    import ui.agent_widget as widget_module

    monkeypatch.setattr(widget_module, "get_settings_service", lambda: service)

    first = widget_module.install_agent(stub_window,
                                        client_factory=lambda: None)
    app.processEvents()
    assert first.floating is not None, "до снятия галочки кот есть"
    first.shutdown()
    stub_window.agent_widget = None

    #: Оператор снимает галочку в диалоге и жмёт «Сохранить».
    window = AgentSettingsDialog(service.agent_settings())
    window.show_floating.setChecked(False)
    service.update_agent(window.get_settings())
    window.close()

    restarted = SettingsService(str(settings_path))
    assert restarted.agent_settings()["show_floating"] is False
    monkeypatch.setattr(widget_module, "get_settings_service",
                        lambda: restarted)

    second = widget_module.install_agent(stub_window,
                                         client_factory=lambda: None)
    app.processEvents()
    assert second.floating is None, "после перезапуска кота быть не должно"
    second.shutdown()


def test_theme_helpers_used_by_dialog_are_available(app):
    """Диалог собирается на общих кнопках темы (регресс на импорт)."""
    assert callable(theme.secondary_button)
    assert callable(theme.danger_button)
