#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вёрстка панели Максимилиана: имя, подпись, кнопки, отступы, чат.

Что было не так (скриншоты оператора, 17.10.2026)
-------------------------------------------------
* имя «Максимилиан» обрезалось до «Максим»: в одном `QHBoxLayout` с именем
  стояли шесть кнопок, и на текст оставалось 57 px из 99 нужных;
* подпись образа «Кот-логист» обрезалась до «Кот-логи» (34 px из 68);
* кнопки действий жались до 25 px и прижимались к тексту;
* аватар 64 px, а текст рядом с ним не был отцентрирован;
* длинный текст в чате обрезался: у пузырей не был выставлен
  `heightForWidth`, а список сообщений вообще не прокручивался
  (полоса прокрутки с максимумом 0).

Что проверяется
---------------
1. Части шапки на месте: аватар, имя, подпись, пять кнопок действий, «✕».
2. Ничего не обрезано: ширина подписи не меньше `sizeHint` для ВСЕХ образов.
3. Кнопки не наезжают друг на друга и на текст, размер у них квадратный.
4. Шапка влезает в ширину панели.
5. Пузырь длинного сообщения получает высоту по переносу строк.
6. История прокручивается и прокручивается до конца.

Qt — в offscreen-режиме.
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

pytest.importorskip("PyQt5")

from PyQt5.QtCore import QSize  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QHBoxLayout, QWidget,
)

from ui.agent_widget import (  # noqa: E402
    PANEL_AVATAR_SIZE, PANEL_TOOL_SIZE, PANEL_TOOL_SPACING, PANEL_WIDTH,
    AgentPanel,
)
from ui.agent_avatar import avatar_keys  # noqa: E402
from ui.theme import apply_theme  # noqa: E402

#: Высота панели в пробе: как у окна 800 px с шапкой и статус-баром.
PANEL_HEIGHT = 640

#: Длинное юридическое предупреждение — то, на котором была видна обрезка.
LONG_LEGAL = (
    "Согласно статье 34 Федерального закона от 08.11.2007 № 259-ФЗ «Устав "
    "автомобильного транспорта и городского наземного электрического "
    "транспорта», срок оплаты перевозки согласуется сторонами в договоре; "
    "законного срока в 30 дней не существует. Пеня за просрочку также "
    "определяется соглашением сторон. Обратите внимание: в заполненной форме "
    "срок оплаты — 45 календарных дней, а в типовом договоре — 30. Это "
    "расхождение не является нарушением закона, но его стоит обсудить с "
    "контрагентом до подписания."
)


@pytest.fixture(scope="module")
def qapp():
    """Одно приложение Qt на модуль (offscreen) — с темой приложения."""
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


def make_panel(parent_widget=None) -> AgentPanel:
    """Панель на подставке: так её видно оператор (ширина 350, высота 640)."""
    panel = AgentPanel(parent=parent_widget)
    panel.resize(PANEL_WIDTH, PANEL_HEIGHT)
    return panel


@pytest.fixture
def panel(qapp):
    """Панель внутри окна-подставки: раскладка считается как в программе."""
    stage = QWidget()
    stage.resize(PANEL_WIDTH, PANEL_HEIGHT)
    row = QHBoxLayout(stage)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    panel = make_panel(stage)
    row.addWidget(panel)
    stage.show()
    stage.grab()              # первый проход раскладки
    qapp.processEvents()
    yield panel
    stage.grab()
    stage.close()
    stage.deleteLater()
    qapp.processEvents()


def header_widgets(panel: AgentPanel) -> list:
    """Виджеты шапки: аватар, имя, подпись, «закрыть» и пять действий."""
    return [
        panel.avatar, panel.name_label, panel.role_label, panel.btn_close,
        panel.btn_settings, panel.btn_diagnostics, panel.btn_tutorial,
        panel.btn_legal, panel.btn_templates,
    ]


def action_buttons(panel: AgentPanel) -> list:
    """Только кнопки действий (без «✕»)."""
    return [
        panel.btn_settings, panel.btn_diagnostics, panel.btn_tutorial,
        panel.btn_legal, panel.btn_templates,
    ]


# ─────────────────────────────────────────────────────────────
# 1. Части шапки на месте
# ─────────────────────────────────────────────────────────────

def test_panel_has_fixed_width(panel):
    """Панель держит ширину 350 px (задание, часть D.1.2)."""
    assert panel.width() == PANEL_WIDTH
    assert panel.minimumWidth() == PANEL_WIDTH


def test_header_parts_are_visible(panel):
    """Аватар, имя, подпись, «✕» и все кнопки действий видны."""
    for widget in header_widgets(panel):
        assert widget.isVisible() is True, f"{widget.objectName() or widget} не виден"


def test_avatar_is_square_and_panel_sized(panel):
    """Аватар — квадрат PANEL_AVATAR_SIZE (64×64), а не вытянутый по высоте."""
    assert panel.avatar.avatar_size() == PANEL_AVATAR_SIZE
    assert panel.avatar.size() == QSize(PANEL_AVATAR_SIZE, PANEL_AVATAR_SIZE)


def test_name_is_not_clipped(panel):
    """Имя «Максимилиан» показано целиком: ширина не меньше нужной."""
    label = panel.name_label
    assert label.text() == "Максимилиан"
    needed = label.sizeHint().width()
    assert label.width() >= needed, (
        f"имя обрезано: {label.width()} px при нужных {needed} px"
    )


@pytest.mark.parametrize("persona_key", avatar_keys())
def test_role_is_not_clipped_for_every_persona(panel, persona_key):
    """Подпись образа видна целиком у КАЖДОГО из пяти образов."""
    panel.set_persona(persona_key)
    panel.parentWidget().grab()
    label = panel.role_label
    needed = label.sizeHint().width()
    assert label.width() >= needed, (
        f"подпись {label.text()!r} обрезана: {label.width()} px "
        f"при нужных {needed} px"
    )


def test_send_hint_is_visible(panel):
    """Рядом с «Отправить» есть подсказка про Enter."""
    assert panel.hint_label.isVisible() is True
    assert "Enter" in panel.hint_label.text()
    assert "Enter" in panel.hint_label.toolTip()


# ─────────────────────────────────────────────────────────────
# 2. Кнопки: размер и отступы
# ─────────────────────────────────────────────────────────────

def test_action_buttons_are_square_and_fixed(panel):
    """Каждая кнопка — квадрат PANEL_TOOL_SIZE (не сжатый QToolButton)."""
    for button in action_buttons(panel):
        assert button.size() == QSize(PANEL_TOOL_SIZE, PANEL_TOOL_SIZE), (
            f"{button.text()}: {button.size()}"
        )
        assert button.toolTip(), "у кнопки нет подсказки"
        assert button.accessibleName(), "у кнопки нет доступного имени"


def test_action_buttons_do_not_overlap(panel):
    """Кнопки действий не наезжают друг на друга и стоят в один ряд."""
    buttons = action_buttons(panel)
    for previous, current in zip(buttons, buttons[1:]):
        assert current.geometry().left() > previous.geometry().right()
        gap = current.geometry().left() - previous.geometry().right()
        assert gap >= PANEL_TOOL_SPACING, f"зазор между кнопками {gap} px"
        assert current.geometry().top() == previous.geometry().top()


def test_action_buttons_are_below_the_text(panel):
    """Кнопки действий стоят отдельным рядом ПОД именем, а не рядом с ним."""
    name_bottom = panel.name_label.mapTo(
        panel, panel.name_label.rect().bottomLeft()).y()
    role_bottom = panel.role_label.mapTo(
        panel, panel.role_label.rect().bottomLeft()).y()
    text_bottom = max(name_bottom, role_bottom)
    for button in action_buttons(panel):
        top = button.mapTo(panel, button.rect().topLeft()).y()
        assert top > text_bottom, f"{button.text()} стоит на одном уровне с именем"


def test_close_button_is_in_the_top_row(panel):
    """«✕» стоит в верхнем ряду справа, а не в общем списке действий."""
    close_top = panel.btn_close.mapTo(panel, panel.btn_close.rect().topLeft()).y()
    name_top = panel.name_label.mapTo(panel, panel.name_label.rect().topLeft()).y()
    assert close_top <= name_top, "«✕» должен быть в верхнем ряду"
    assert panel.btn_close.width() == PANEL_TOOL_SIZE


def test_header_widgets_do_not_overlap(panel):
    """Ни одна пара виджетов шапки не перекрывается (проверка по geometry)."""
    rects = []
    for widget in header_widgets(panel):
        top_left = widget.mapTo(panel, widget.rect().topLeft())
        rects.append((widget, top_left, widget.rect().translated(top_left)))
    for index, (first, _, first_rect) in enumerate(rects):
        for second, _, second_rect in rects[index + 1:]:
            assert not first_rect.intersects(second_rect), (
                f"{first.objectName() or first} перекрывает "
                f"{second.objectName() or second}"
            )


def test_header_fits_panel_width(panel):
    """Шапка целиком влезает в панель: ни один виджет не выходит за край."""
    for widget in header_widgets(panel):
        right = widget.mapTo(panel, widget.rect().topRight()).x()
        assert right <= PANEL_WIDTH - 12, (
            f"{widget.objectName() or widget} выходит за край панели: {right}"
        )


# ─────────────────────────────────────────────────────────────
# 3. Чат: длинный текст и прокрутка
# ─────────────────────────────────────────────────────────────

def test_long_message_is_not_clipped(panel, qapp):
    """
    Пузырь длинного сообщения получает высоту по переносу строк.

    До правки: текст на 386 символов занимал 116 px при нужных 209 —
    обрезала раскладка (``heightForWidth`` у пузыря не был включён).
    """
    panel.append_message(LONG_LEGAL, "agent")
    panel.parentWidget().grab()
    qapp.processEvents()

    bubble = panel.history_layout.itemAt(0).widget()
    needed = bubble.heightForWidth(bubble.width())
    assert needed > 0
    assert bubble.height() >= needed, (
        f"текст обрезан: высота {bubble.height()} px при нужных {needed} px"
    )


def fill_history(panel: AgentPanel, count: int = 15) -> None:
    """
    Наполняет историю так, чтобы она ГАРАНТИРОВАННО была выше окна.

    Длина сообщения и их число выбраны с запасом: в CI (offscreen без
    шрифтов) метрики текста меньше, и на десяти коротких строках история
    могла бы уместиться в окно — тогда тест про прокрутку проверял бы не то.
    """
    for index in range(count):
        panel.append_message(
            f"Строка {index}: сообщение длиной в две строки панели, "
            "чтобы история точно не поместилась в окно просмотра.",
            "agent",
        )


def test_history_scrolls_when_longer_than_view(panel, qapp):
    """
    История прокручивается: у полосы есть диапазон, и низ достижим.

    До правки `QScrollArea` держал список ровно по высоте окна просмотра:
    двенадцать сообщений требовали 1000 px, виджет был 378 px, а максимум
    полосы — 0. Последние сообщения оператор не видел вовсе.
    """
    fill_history(panel)
    panel.parentWidget().grab()
    qapp.processEvents()
    panel.scroll_to_bottom_now()
    qapp.processEvents()

    viewport = panel.scroll.viewport().height()
    need = panel.history_layout.heightForWidth(
        panel.scroll.viewport().width())
    assert need > viewport, (
        f"тест бессмыслен: истории нужно {need} px, а окно просмотра {viewport} px"
    )
    bar = panel.scroll.verticalScrollBar()
    assert bar.maximum() > 0, "история не прокручивается (максимум полосы 0)"
    assert bar.value() == bar.maximum(), "прокрутка не дошла до последнего сообщения"
    assert panel.history.height() >= viewport


def test_clear_messages_resets_history_height(panel, qapp):
    """Очистка истории убирает и высоту прокрутки (пустой список не «длинный»)."""
    fill_history(panel)
    panel.parentWidget().grab()
    qapp.processEvents()
    assert panel.scroll.verticalScrollBar().maximum() > 0

    panel.clear_messages()
    panel.scroll_to_bottom_now()
    qapp.processEvents()
    assert panel.history.minimumHeight() == 0
    assert panel.scroll.verticalScrollBar().maximum() == 0


def test_scroll_survives_narrow_width(panel, qapp):
    """При узкой панели (анимация выезда) текст переносится, а не обрезается."""
    panel.resize(PANEL_WIDTH, PANEL_HEIGHT)
    panel.append_message(LONG_LEGAL, "agent")
    qapp.processEvents()
    panel.setMaximumWidth(200)
    panel.resize(200, PANEL_HEIGHT)
    panel.parentWidget().grab()
    qapp.processEvents()

    bubble = panel.history_layout.itemAt(0).widget()
    needed = bubble.heightForWidth(bubble.width())
    assert bubble.height() >= needed, (
        f"на узкой панели текст обрезан: {bubble.height()} < {needed}"
    )
