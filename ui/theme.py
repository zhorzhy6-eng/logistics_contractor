#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единая светлая тема оформления (визуальная иерархия интерфейса).

Зачем модуль
------------
До него каждый виджет нёс свой inline-стиль (или не нёс вовсе), поэтому
интерфейс выглядел «плоским»: все поля, кнопки и блоки были одинаковыми.
Здесь собраны в одном месте:

  * палитра — цвета в одном месте, без «магических» строк по файлам;
  * APP_QSS — общий стиль приложения (вкладки, карточки-блоки, поля, кнопки,
    статус-бар, таблицы);
  * фабрики — primary_button / secondary_button / accent_button / ghost_button,
    section_box / required_label, чтобы вкладки не дублировали оформление.

Иерархия, которую задаёт тема:
  1) главное действие вкладки — крупная синяя кнопка (primary);
  2) финальное действие — зелёная accent («Создать договор»), спокойнее primary;
  3) вспомогательные действия — серые secondary с тонкой рамкой;
  4) кнопки-иконки у полей (📋/🧠) — ghost: без фона, фон только при наведении;
  5) обязательные поля — тёмно-синяя звёздочка и бледно-жёлтый фон, пока пусто.
"""

import logging
from typing import Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication, QFormLayout, QGroupBox, QLabel, QPushButton, QWidget,
)

logger = logging.getLogger("ui.theme")


# ─────────────────────────────────────────────────────────────
# Палитра (светлая тема)
# ─────────────────────────────────────────────────────────────

NAVY = "#1E3A5F"          # заголовки вкладок и блоков
NAVY_HOVER = "#16304F"
PRIMARY = "#1E5FA8"       # главное действие вкладки
PRIMARY_HOVER = "#164B85"
PRIMARY_PRESSED = "#123E6E"
ACCENT = "#2F855A"        # «Создать договор»
ACCENT_HOVER = "#276749"
DANGER = "#B42318"        # удаление: красный только в тексте и рамке
DANGER_HOVER = "#FFF5F5"
DANGER_BORDER = "#F1C0BB"
MUTED = "#64748B"         # подсказки, неактивные вкладки
TEXT = "#1F2937"
BORDER = "#E2E8F0"
BORDER_INPUT = "#CBD5E1"
SOFT_BG = "#F8FAFC"       # карточки, второстепенные кнопки
HOVER_BG = "#EEF2F7"
BAR_BG = "#F1F5F9"        # статус-бар, заголовки таблиц
WARN_BG = "#FFF8E1"       # пустое обязательное поле
WARN_BORDER = "#E6C86E"

#: Стиль пустого обязательного поля. Локальный stylesheet, а не
#: attribute-селектор: Qt не пересчитывает QSS-атрибуты у уже отрисованного
#: поля, поэтому подсветка «не загоралась». Здесь продублированы базовые
#: свойства поля, иначе локальный стиль сбросил бы рамку и отступы темы.
REQUIRED_EMPTY_QSS = (
    f"QLineEdit {{ background-color: {WARN_BG}; border: 1px solid {WARN_BORDER};"
    f" border-radius: 5px; padding: 5px 7px; }}"
)
REQUIRED_EMPTY_QSS_TEXT = (
    f"QTextEdit {{ background-color: {WARN_BG}; border: 1px solid {WARN_BORDER};"
    f" border-radius: 5px; padding: 5px 7px; }}"
)


def qss() -> str:
    """Общий стиль приложения (собирается из палитры)."""
    return f"""
/* ── База ── */
QWidget {{ font-size: 10pt; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BAR_BG}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ── Вкладки: активная видна сразу ── */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    background: {BAR_BG};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 8px 16px;
    margin-right: 2px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {NAVY}; }}
QTabBar::tab:selected {{
    color: {NAVY};
    font-weight: bold;
    border-bottom: 2px solid {PRIMARY};
}}

/* ── Блоки-карточки ── */
QGroupBox {{
    background: #FFFFFF;
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding: 14px 12px 10px 12px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    color: {NAVY};
    font-size: 10.5pt;
}}

/* ── Поля ввода ── */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {{
    background: #FFFFFF;
    border: 1px solid {BORDER_INPUT};
    border-radius: 5px;
    padding: 5px 7px;
    min-height: 20px;
    selection-background-color: {PRIMARY};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QDateEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {PRIMARY};
}}
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled {{ background: {BAR_BG}; color: {MUTED}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}

/* ── Кнопки ── */
QPushButton#primary {{
    background: {PRIMARY}; color: #FFFFFF; font-weight: bold; font-size: 10.5pt;
    padding: 9px 22px; border: none; border-radius: 6px; min-height: 24px;
}}
QPushButton#primary:hover {{ background: {PRIMARY_HOVER}; }}
QPushButton#primary:pressed {{ background: {PRIMARY_PRESSED}; }}
QPushButton#primary:disabled {{ background: #A9BFD8; color: #F1F5F9; }}

QPushButton#accent {{
    background: {ACCENT}; color: #FFFFFF; font-weight: bold;
    padding: 8px 18px; border: none; border-radius: 6px; min-height: 22px;
}}
QPushButton#accent:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#accent:disabled {{ background: #9DBFAE; color: #F1F5F9; }}

QPushButton#secondary {{
    background: {SOFT_BG}; color: #374151;
    border: 1px solid {BORDER_INPUT}; border-radius: 6px;
    padding: 7px 16px; min-height: 20px;
}}
QPushButton#secondary:hover {{ background: {HOVER_BG}; }}
QPushButton#secondary:disabled {{ color: #9AA5B1; border-color: {BORDER}; }}

QPushButton#danger {{
    background: #FFFFFF; color: {DANGER};
    border: 1px solid {DANGER_BORDER}; border-radius: 6px;
    padding: 7px 16px; min-height: 20px;
}}
QPushButton#danger:hover {{ background: {DANGER_HOVER}; }}

QPushButton#ghost {{
    background: transparent; border: none; color: {MUTED};
    padding: 2px 4px; font-size: 11pt;
}}
QPushButton#ghost:hover {{ background: {HOVER_BG}; border-radius: 4px; }}

/* Кнопки, которые создаются без objectName (диалоги, таблицы) */
QPushButton {{ padding: 6px 14px; border-radius: 6px; }}

/* ── Таблицы ── */
QTableWidget, QTableView {{
    background: #FFFFFF; border: 1px solid {BORDER}; border-radius: 6px;
    gridline-color: {BORDER};
    alternate-background-color: {SOFT_BG};
}}
QHeaderView::section {{
    background: {BAR_BG}; color: {NAVY}; font-weight: bold;
    padding: 6px 8px; border: none; border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
}}

/* ── Статус-бар: где я, что заполнено, когда сохранял ── */
QStatusBar {{ background: {BAR_BG}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QStatusBar QLabel {{ color: {MUTED}; padding: 2px 10px; }}
QStatusBar::item {{ border: none; }}

/* ── Прочее ── */
QCheckBox {{ color: {TEXT}; spacing: 6px; }}
QProgressBar {{
    border: 1px solid {BORDER_INPUT}; border-radius: 5px; background: #FFFFFF;
    text-align: center; color: {TEXT};
}}
QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 4px; }}
QToolTip {{ background: {NAVY}; color: #FFFFFF; border: none; padding: 4px 6px; }}
"""


def apply_theme(app: Optional[QApplication] = None) -> QApplication:
    """
    Применяет тему к приложению (по умолчанию — к текущему QApplication).

    Вызывается один раз в main.py после app.setStyle("Fusion").
    """
    application = app or QApplication.instance()
    if application is None:
        logger.warning("apply_theme: QApplication ещё не создан — тема не применена")
        return application

    application.setStyleSheet(qss())
    logger.info("Тема оформления применена (светлая, Fusion)")
    return application


# ─────────────────────────────────────────────────────────────
# Фабрики элементов
# ─────────────────────────────────────────────────────────────

def primary_button(text: str, tooltip: str = "", parent: Optional[QWidget] = None) -> QPushButton:
    """Главное действие вкладки: крупная синяя кнопка."""
    button = QPushButton(text, parent)
    button.setObjectName("primary")
    button.setMinimumHeight(40)
    button.setCursor(Qt.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def accent_button(text: str, tooltip: str = "", parent: Optional[QWidget] = None) -> QPushButton:
    """Финальное действие («Создать договор»): зелёная, но не ярче primary."""
    button = QPushButton(text, parent)
    button.setObjectName("accent")
    button.setMinimumHeight(36)
    button.setCursor(Qt.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def secondary_button(text: str, tooltip: str = "", parent: Optional[QWidget] = None) -> QPushButton:
    """Вспомогательное действие: светлая кнопка с тонкой рамкой."""
    button = QPushButton(text, parent)
    button.setObjectName("secondary")
    button.setMinimumHeight(32)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def danger_button(text: str, tooltip: str = "", parent: Optional[QWidget] = None) -> QPushButton:
    """Опасное действие (удаление): красный текст и рамка, без заливки."""
    button = QPushButton(text, parent)
    button.setObjectName("danger")
    button.setMinimumHeight(32)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def ghost_button(text: str, size: int = 28, tooltip: str = "",
                 parent: Optional[QWidget] = None) -> QPushButton:
    """Кнопка-иконка у поля ввода: не отвлекает внимание."""
    button = QPushButton(text, parent)
    button.setObjectName("ghost")
    button.setFixedSize(size, size)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def required_label(text: str, required: bool = True) -> QLabel:
    """
    Подпись поля. У обязательного — тёмно-синяя звёздочка.

    Красный цвет не используется: он читается как «ошибка», хотя поле
    просто ещё не заполнено.
    """
    return make_label(text, required)


def make_label(text: str, required: bool = False) -> QLabel:
    label = QLabel()
    set_label_required(label, text, required)
    return label


def set_label_required(label: QLabel, text: str, required: bool) -> None:
    """Меняет текст подписи, добавляя/убирая звёздочку (например, КПП у ИП)."""
    if required:
        label.setText(
            f'{text} <span style="color:{PRIMARY}; font-weight:bold;">*</span>'
        )
        label.setTextFormat(Qt.RichText)
        label.setToolTip("Обязательное поле")
    else:
        label.setText(text)
        label.setTextFormat(Qt.PlainText)
        label.setToolTip("")


def section_box(title: str, parent: Optional[QWidget] = None) -> Tuple[QGroupBox, QFormLayout]:
    """
    Блок-карточка с заголовком и готовой формой внутри.

    Возвращает (группа, форма): заголовок жирный и тёмно-синий, блок отделён
    рамкой и отступом от соседнего (см. APP_QSS).
    """
    box = QGroupBox(title, parent)
    form = QFormLayout(box)
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(7)
    return box, form


def page_title(text: str, hint: str = "") -> QWidget:
    """Заголовок вкладки с подсказкой — чтобы было видно, где находишься."""
    from PyQt5.QtWidgets import QVBoxLayout

    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)

    title = QLabel(text)
    title.setStyleSheet(f"font-size: 14pt; font-weight: bold; color: {NAVY};")
    layout.addWidget(title)

    if hint:
        hint_label = QLabel(hint)
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet(f"color: {MUTED}; font-size: 9pt;")
        layout.addWidget(hint_label)

    return widget
