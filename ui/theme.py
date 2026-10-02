#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Темы оформления приложения (визуальная иерархия интерфейса).

Зачем модуль
------------
До него каждый виджет нёс свой inline-стиль (или не нёс вовсе), поэтому
интерфейс выглядел «плоским»: все поля, кнопки и блоки были одинаковыми.
Здесь собраны в одном месте:

  * палитра — цвета в одном месте, без «магических» строк по файлам;
  * APP_QSS — общий стиль приложения (шапка, сайдбар, карточки-блоки,
    поля, кнопки, вкладки, статус-бар, таблицы);
  * фабрики — primary_button / secondary_button / accent_button / ghost_button,
    section_box / required_label, чтобы вкладки не дублировали оформление.

Темы
----
Палитры лежат в ui/theme_palettes.py (только данные). Доступны две темы:

  * ``classic``  — «Светлая тема», она же по умолчанию;
  * ``dark_pro`` — «Тёмная тема» (макет «02-dark-pro»).

Выбор темы живёт в диалоге настроек (блок «Оформление»); приложение умеет
следовать за режимом Windows — см. ui/system_theme.py.

Иерархия, которую задаёт тема:
  1) главное действие вкладки — крупная кнопка (primary);
  2) финальное действие — кнопка accent («Создать договор»), спокойнее primary;
  3) вспомогательные действия — secondary с тонкой рамкой;
  4) кнопки-иконки у полей (📋/🧠) — ghost: без фона, фон только при наведении;
  5) обязательные поля — звёздочка цветом акцента и подсветка фона, пока пусто.

Совместимость
-------------
``apply_theme(app)`` без имени темы применяет «Классику» — ровно то
оформление, которое было до появления переключателя. Прежние константы
палитры (NAVY, PRIMARY, ACCENT, WARN_BG, REQUIRED_EMPTY_QSS, ...) сохранены
и равны значениям светлой темы, поэтому существующий код и тесты работают
без изменений. Тема из настроек применяется явным вызовом:
``apply_theme(app, saved_theme_name)``.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QFormLayout, QGroupBox, QLabel, QLineEdit, QPushButton, QWidget,
)

from ui import theme_palettes as palettes

logger = logging.getLogger("ui.theme")


# ─────────────────────────────────────────────────────────────
# Палитра светлой темы («Светлая тема») — исторические константы.
# Значения берутся из ui/theme_palettes.CLASSIC, чтобы не было
# двух источников правды; имена оставлены для совместимости.
# ─────────────────────────────────────────────────────────────

NAVY = palettes.CLASSIC["heading"]            # заголовки вкладок и блоков
NAVY_HOVER = palettes.CLASSIC["heading_hover"]
PRIMARY = palettes.CLASSIC["primary"]         # главное действие вкладки
PRIMARY_HOVER = palettes.CLASSIC["primary_hover"]
PRIMARY_PRESSED = palettes.CLASSIC["primary_pressed"]
ACCENT = palettes.CLASSIC["accent"]           # «Создать договор»
ACCENT_HOVER = palettes.CLASSIC["accent_hover"]
DANGER = palettes.CLASSIC["danger"]           # удаление: красный только в тексте и рамке
DANGER_HOVER = palettes.CLASSIC["danger_hover_bg"]
DANGER_BORDER = palettes.CLASSIC["danger_border"]
MUTED = palettes.CLASSIC["text_muted"]        # подсказки, неактивные вкладки
TEXT = palettes.CLASSIC["text"]
BORDER = palettes.CLASSIC["border"]
BORDER_INPUT = palettes.CLASSIC["border_input"]
SOFT_BG = palettes.CLASSIC["surface_alt"]     # карточки, второстепенные кнопки
HOVER_BG = palettes.CLASSIC["hover_bg"]
BAR_BG = palettes.CLASSIC["bar_bg"]           # статус-бар, заголовки таблиц
WARN_BG = palettes.CLASSIC["warn_bg"]         # пустое обязательное поле
WARN_BORDER = palettes.CLASSIC["warn_border"]

#: Тема, активная в текущем процессе (меняется apply_theme/set_active_theme).
_active_theme: str = palettes.DEFAULT_THEME


# ─────────────────────────────────────────────────────────────
# Реестр тем
# ─────────────────────────────────────────────────────────────

def theme_names() -> List[str]:
    """Ключи всех доступных тем в порядке переключателя."""
    return palettes.theme_names()


def available_themes() -> List[Tuple[str, str]]:
    """Список «(ключ, подпись)» для выпадающих списков и меню."""
    return [(key, palettes.theme_label(key)) for key in palettes.theme_names()]


def theme_label(name: Any = None) -> str:
    """Подпись темы для интерфейса (по умолчанию — активной)."""
    return palettes.theme_label(active_theme() if name is None else name)


def theme_hint(name: Any = None) -> str:
    """Короткое пояснение к теме (по умолчанию — к активной)."""
    return palettes.theme_hint(active_theme() if name is None else name)


def is_dark(name: Any = None) -> bool:
    """Тёмная ли тема (подбор иконок, флажок «тёмное оформление»)."""
    return palettes.is_dark(active_theme() if name is None else name)


def normalize(name: Any) -> str:
    """Имя темы → ключ реестра (неизвестное имя → тема по умолчанию)."""
    return palettes.normalize(name)


def palette(name: Any = None) -> Dict[str, Any]:
    """Палитра темы (по умолчанию — активная)."""
    return palettes.get_palette(active_theme() if name is None else name)


def active_theme() -> str:
    """Ключ активной темы процесса."""
    return _active_theme


def set_active_theme(name: Any) -> str:
    """
    Запоминает активную тему БЕЗ применения стилей.

    Нужна там, где стиль ставит кто-то другой (например, тесты или
    предпросмотр); обычный путь — apply_theme().
    """
    global _active_theme
    _active_theme = normalize(name)
    return _active_theme


# ─────────────────────────────────────────────────────────────
# Стиль пустого обязательного поля.
#
# Локальный stylesheet, а не attribute-селектор: Qt не пересчитывает
# QSS-атрибуты у уже отрисованного поля, поэтому подсветка «не загоралась».
# Здесь продублированы базовые свойства поля, иначе локальный стиль сбросил
# бы рамку и отступы темы.
# ─────────────────────────────────────────────────────────────

def required_empty_qss(theme_name: Any = None) -> str:
    """Стиль пустого обязательного поля для указанной (или активной) темы."""
    p = palette(theme_name)
    return (
        f"QLineEdit {{ background-color: {p['warn_bg']};"
        f" border: 1px solid {p['warn_border']};"
        f" border-radius: 5px; padding: 5px 7px;"
        f" color: {p['input_text']}; }}"
    )


def required_empty_qss_text(theme_name: Any = None) -> str:
    """То же для многострочного поля (QTextEdit)."""
    p = palette(theme_name)
    return (
        f"QTextEdit {{ background-color: {p['warn_bg']};"
        f" border: 1px solid {p['warn_border']};"
        f" border-radius: 5px; padding: 5px 7px;"
        f" color: {p['input_text']}; }}"
    )


#: Исторические константы светлой темы (используются ui/widgets.py и тестами).
REQUIRED_EMPTY_QSS = required_empty_qss(palettes.DEFAULT_THEME)
REQUIRED_EMPTY_QSS_TEXT = required_empty_qss_text(palettes.DEFAULT_THEME)


def readonly_field_qss(theme_name: Any = None) -> str:
    """
    Стиль поля «только для чтения» (расчётные суммы, ставка НДС без НДС).

    Как и подсветка обязательного поля — локальный стиль: Qt не пересчитывает
    QSS при изменении свойства readOnly у уже отрисованного поля. Базовые
    свойства продублированы, иначе локальный стиль сбросил бы рамку и отступы.
    """
    p = palette(theme_name)
    return (
        f"QLineEdit {{ background-color: {p['disabled_bg']};"
        f" color: {p['text_muted']}; font-weight: bold;"
        f" border: 1px solid {p['border_input']};"
        f" border-radius: 5px; padding: 5px 7px; }}"
    )


def deleted_row_color(theme_name: Any = None) -> QColor:
    """
    Цвет пометки «удалён» в таблицах справочника.

    Раньше это был жёсткий Qt.red: на тёмном фоне он выглядел чужеродно.
    Теперь берётся цвет опасности активной темы.
    """
    return QColor(palette(theme_name)["danger"])


# ─────────────────────────────────────────────────────────────
# Общий QSS приложения
# ─────────────────────────────────────────────────────────────

_QSS_TEMPLATE = """
/* ── База ── */
QWidget {{ font-family: {font_family}; font-size: {font_size}; color: {text}; }}
QMainWindow, QDialog {{ background: {window_bg}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QToolTip {{ background: {tooltip_bg}; color: {tooltip_text}; border: none; padding: 4px 6px; }}

/* ── Шапка окна ── */
QFrame#appHeader {{
    background: {header_bg};
    border-bottom: 1px solid {header_border};
    border-radius: {radius};
}}
QLabel#appHeading {{ color: {header_text}; font-size: 16pt; font-weight: 700; }}
QLabel#appSubheading {{ color: {header_sub_text}; font-size: {font_size_small}; }}
QLabel#pageTitle {{ color: {heading}; font-size: 14pt; font-weight: bold; }}
QLabel#pageHint {{ color: {text_muted}; font-size: {font_size_small}; }}

/* ── Сайдбар: навигация по разделам ── */
QFrame#sideNav {{
    background: {nav_bg};
    border: 1px solid {nav_border};
    border-radius: {radius};
    min-width: {nav_width}px;
    max-width: {nav_width}px;
}}
QLabel#navSectionTitle {{
    color: {nav_item_text};
    font-size: {font_size_small};
    font-weight: 600;
    padding: 4px 6px 8px 6px;
}}
QPushButton#navItem {{
    background: transparent;
    color: {nav_item_text};
    border: none;
    border-left: 3px solid transparent;
    border-radius: {radius_small};
    padding: 9px 12px;
    text-align: left;
    font-size: {font_size};
}}
QPushButton#navItem:hover {{ background: {nav_item_hover_bg}; color: {nav_item_hover_text}; }}
QPushButton#navItem:checked {{
    background: {nav_item_active_bg};
    color: {nav_item_active_text};
    border-left: 3px solid {nav_indicator};
    font-weight: 600;
}}
QFrame#navSeparator {{ background: {nav_border}; border: none; }}

/* ── Вкладки: активная видна сразу ── */
QTabWidget::pane {{
    border: 1px solid {border};
    border-radius: {radius};
    background: {surface};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {tab_text};
    padding: 10px 12px;
    margin-right: 4px;
    border-bottom: 3px solid transparent;
    min-width: 112px;
}}
QTabBar::tab:hover {{ color: {heading}; background: {tab_hover_bg}; }}
QTabBar::tab:selected {{
    color: {tab_selected_text};
    font-weight: 600;
    background: {tab_selected_bg};
    border-bottom: 3px solid {tab_indicator};
}}

/* ── Блоки-карточки ── */
QGroupBox {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {radius};
    margin-top: 16px;
    padding: 16px 16px 12px 16px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 8px;
    color: {heading};
    font-size: {font_size};
}}
QFrame#actionBar {{ background: {action_bar_bg}; border: 1px solid {panel_border}; border-radius: {radius}; }}
QFrame#recognitionPanel {{ background: {recog_bg}; border: 1px solid {recog_border}; border-radius: {radius}; }}
QLabel#sectionHeading {{ color: {recog_title}; font-size: {font_size}; font-weight: 600; }}
QLabel#sectionHint {{ color: {text_muted}; font-size: {font_size_small}; }}

/* ── Поля ввода ── */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox, QDoubleSpinBox {{
    background: {input_bg};
    color: {input_text};
    border: 1px solid {border_input};
    border-radius: {radius_input};
    padding: 6px 9px;
    selection-background-color: {selection_bg};
    selection-color: {selection_text};
}}
QLineEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox, QDoubleSpinBox {{
    min-height: 20px;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QDateEdit:focus, QTimeEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {focus_border};
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QComboBox:disabled, QDateEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {disabled_bg}; color: {text_disabled};
}}
QLineEdit[readOnly="true"], QTextEdit[readOnly="true"], QPlainTextEdit[readOnly="true"] {{
    background: {disabled_bg}; color: {text_muted};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {menu_bg};
    color: {menu_text};
    border: 1px solid {border};
    selection-background-color: {menu_hover_bg};
    selection-color: {menu_text};
}}

/* ── Кнопки ── */
QPushButton#primary {{
    background: {primary}; color: {primary_text}; font-weight: 600;
    padding: 8px 18px; border: none; border-radius: {radius_input}; min-height: 24px;
}}
QPushButton#primary:hover {{ background: {primary_hover}; }}
QPushButton#primary:pressed {{ background: {primary_pressed}; }}
QPushButton#primary:disabled {{ background: {primary_disabled_bg}; color: {primary_disabled_text}; }}

QPushButton#accent {{
    background: {accent}; color: {accent_text}; font-weight: 600;
    padding: 8px 18px; border: none; border-radius: {radius_input}; min-height: 24px;
}}
QPushButton#accent:hover {{ background: {accent_hover}; }}
QPushButton#accent:pressed {{ background: {accent_pressed}; }}
QPushButton#accent:disabled {{ background: {accent_disabled_bg}; color: {accent_disabled_text}; }}

QPushButton#secondary {{
    background: {surface}; color: {heading};
    border: 1px solid {border_input}; border-radius: {radius_input};
    padding: 7px 14px; min-height: 20px;
}}
QPushButton#secondary:hover {{ background: {hover_bg}; }}
QPushButton#secondary:pressed {{ background: {nav_item_active_bg}; }}
QPushButton#secondary:disabled {{ color: {text_disabled}; border-color: {border}; }}

QPushButton#clipboard {{
    background: {clipboard_bg}; color: {clipboard_text};
    border: 1px solid {clipboard_border}; border-radius: {radius_input};
    padding: 7px 14px; min-height: 20px;
}}
QPushButton#clipboard:hover {{ background: {clipboard_hover_bg}; border-color: {clipboard_hover_border}; }}
QPushButton#clipboard:pressed {{ background: {clipboard_pressed_bg}; }}

QPushButton#danger {{
    background: {surface}; color: {danger};
    border: 1px solid {danger_border}; border-radius: {radius_input};
    padding: 7px 16px; min-height: 20px;
}}
QPushButton#danger:hover {{ background: {danger_hover_bg}; }}
QPushButton#danger:pressed {{ background: {danger_hover_bg}; border-color: {danger}; }}

QPushButton#ghost {{
    background: transparent; border: none; color: {text_muted};
    padding: 2px 4px;
}}
QPushButton#ghost:hover {{ background: {hover_bg}; border-radius: 4px; }}

/* Кнопки, которые создаются без objectName (диалоги, таблицы) */
QPushButton {{ background: {surface}; color: {text}; border: 1px solid {border_input};
    padding: 6px 14px; border-radius: {radius_input}; }}
QPushButton:hover {{ background: {hover_bg}; }}
QPushButton:pressed {{ background: {nav_item_active_bg}; }}
QPushButton:disabled {{ color: {text_disabled}; border-color: {border}; }}

/* ── Таблицы ── */
QTableWidget, QTableView {{
    background: {table_bg}; color: {text};
    border: 1px solid {border}; border-radius: {radius_small};
    gridline-color: {table_grid};
    alternate-background-color: {table_alt_bg};
    selection-background-color: {selection_bg};
    selection-color: {selection_text};
}}
QTableWidget::item:hover, QTableView::item:hover {{ background: {table_row_hover}; }}
QHeaderView::section {{
    background: {table_header_bg}; color: {table_header_text}; font-weight: bold;
    padding: 6px 8px; border: none; border-right: 1px solid {border};
    border-bottom: 1px solid {border};
}}
QTableCornerButton::section {{ background: {table_header_bg}; border: none; }}
QListWidget, QListView, QTreeWidget, QTreeView {{
    background: {table_bg}; color: {text};
    border: 1px solid {border}; border-radius: {radius_small};
    selection-background-color: {selection_bg};
    selection-color: {selection_text};
}}

/* ── Статус-бар: где я, что заполнено, когда сохранял ── */
QStatusBar {{ background: {bar_bg}; border-top: 1px solid {border}; color: {text_muted}; }}
QStatusBar QLabel {{ color: {text_muted}; padding: 2px 10px; }}
QStatusBar::item {{ border: none; }}

/* ── Полосы прокрутки ── */
QScrollBar:vertical {{ background: {scroll_track}; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {scroll_handle}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {scroll_handle_hover}; }}
QScrollBar:horizontal {{ background: {scroll_track}; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {scroll_handle}; border-radius: 4px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {scroll_handle_hover}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

/* ── Меню (например, выбор темы) ── */
QMenu {{ background: {menu_bg}; color: {menu_text}; border: 1px solid {border}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {menu_hover_bg}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}

/* ── Прочее ── */
QCheckBox {{ color: {text}; spacing: 6px; }}
QRadioButton {{ color: {text}; spacing: 6px; }}
QProgressBar {{
    border: 1px solid {border_input}; border-radius: 5px; background: {progress_bg};
    text-align: center; color: {text};
}}
QProgressBar::chunk {{ background: {progress_chunk}; border-radius: 4px; }}
QLabel#mutedLabel {{ color: {text_muted}; }}
QFrame#divider {{ background: {border}; border: none; max-height: 1px; }}
"""


def qss(theme_name: Any = None) -> str:
    """
    Общий стиль приложения для темы (по умолчанию — активной).

    Без аргумента после apply_theme(app) возвращает стиль активной темы;
    до первого применения активна «Светлая тема» — то есть прежнее оформление.
    """
    p = palette(theme_name)
    return _QSS_TEMPLATE.format(**p)


# ─────────────────────────────────────────────────────────────
# Палитра Qt (QPalette)
#
# QSS не перекрашивает то, что Qt рисует сам: стрелки QComboBox/QSpinBox,
# галочки QCheckBox, рамки выделения. Поэтому вместе со стилем ставим
# палитру приложения — иначе в тёмной теме эти элементы остаются светлыми.
# ─────────────────────────────────────────────────────────────

def _solid_color(value: str) -> str:
    """
    Приводит значение роли к сплошному цвету.

    Часть тем задаёт фон окна градиентом — QPalette
    градиент не принимает, поэтому берём первый цвет градиента.
    """
    text = str(value or "").strip()
    if text.lower().startswith("qlineargradient"):
        for chunk in text.split(","):
            part = chunk.strip()
            if part.startswith("stop:"):
                # stop:0 #F3F6FB → #F3F6FB
                color = part.split(")", 1)[0].split()[-1].strip()
                if color.startswith("#"):
                    return color
        return "#808080"
    return text


def build_palette(theme_name: Any = None) -> QPalette:
    """QPalette для темы (используется apply_theme)."""
    p = palette(theme_name)
    pal = QPalette()

    window = _solid_color(p["window_bg"])
    pal.setColor(QPalette.Window, QColor(window))
    pal.setColor(QPalette.WindowText, QColor(p["text"]))
    pal.setColor(QPalette.Base, QColor(p["input_bg"]))
    pal.setColor(QPalette.AlternateBase, QColor(p["surface_alt"]))
    pal.setColor(QPalette.Text, QColor(p["input_text"]))
    pal.setColor(QPalette.Button, QColor(p["surface"]))
    pal.setColor(QPalette.ButtonText, QColor(p["text"]))
    pal.setColor(QPalette.BrightText, QColor(p["err_text"]))
    pal.setColor(QPalette.Highlight, QColor(p["selection_bg"]))
    pal.setColor(QPalette.HighlightedText, QColor(p["selection_text"]))
    pal.setColor(QPalette.ToolTipBase, QColor(p["tooltip_bg"]))
    pal.setColor(QPalette.ToolTipText, QColor(p["tooltip_text"]))
    pal.setColor(QPalette.Link, QColor(p["primary"]))

    placeholder_role = getattr(QPalette, "PlaceholderText", None)
    if placeholder_role is not None:
        pal.setColor(placeholder_role, QColor(p["placeholder_text"]))

    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(p["text_disabled"]))

    return pal


def _refresh_themed_widgets(application: QApplication) -> int:
    """
    Перекрашивает элементы, у которых оформление хранится вне QSS.

    Смена темы не должна оставлять «хвосты»: подсвеченные пустые обязательные
    поля и звёздочки обязательных подписей задаются локальными стилями и
    иконками, поэтому их нужно пересобрать по новой палитре. Данные формы
    при этом не читаются и не меняются.
    """
    updated = 0

    for widget in application.allWidgets():
        # Обязательные подписи: перерисовать звёздочку в цвете темы
        base_text = widget.property("requiredText")
        if base_text is not None and isinstance(widget, QLabel):
            set_label_required(widget, str(base_text), bool(widget.property("isRequired")))
            updated += 1
            continue

        # Поля «только для чтения»: расчётные суммы и ставка НДС без НДС
        if isinstance(widget, QLineEdit) and widget.property("readonlyField"):
            widget.setStyleSheet(readonly_field_qss())
            updated += 1
            continue

        # Пустое обязательное поле: вернуть/снять подсветку
        refresh = getattr(widget, "refresh_required_style", None)
        if callable(refresh):
            try:
                refresh()
                updated += 1
            except Exception as e:  # noqa: BLE001 — оформление не должно ломать UI
                logger.debug("Не удалось обновить подсветку поля: %s", e)
            continue

        # Кнопки-иконки у полей (📋/🧠) нарисованы вручную
        action = widget.property("fieldActionIcon")
        if action and isinstance(widget, QPushButton):
            widget.setIcon(_field_action_icon(str(action)))
            updated += 1

    return updated


def apply_theme(
    app: Optional[QApplication] = None,
    theme_name: Any = None,
) -> QApplication:
    """
    Применяет тему к приложению (по умолчанию — к текущему QApplication).

    :param app: приложение; None — текущий QApplication.
    :param theme_name: ключ темы. None — «Светлая тема»: прежнее поведение
        функции сохранено, поэтому старые вызовы apply_theme(app) не меняют
        внешний вид приложения. Тема из настроек передаётся явно.

    Вызывается один раз в main.py после app.setStyle("Fusion"); повторный
    вызов переключает тему «на лету» — данные формы не затрагиваются.
    """
    application = app or QApplication.instance()
    if application is None:
        logger.warning("apply_theme: QApplication ещё не создан — тема не применена")
        return application

    key = set_active_theme(palettes.DEFAULT_THEME if theme_name is None else theme_name)
    application.setPalette(build_palette(key))
    application.setStyleSheet(qss(key))

    refreshed = _refresh_themed_widgets(application)
    logger.info(
        "Тема оформления применена: %s (%s), обновлено элементов: %s",
        key, theme_label(key), refreshed,
    )
    return application


# ─────────────────────────────────────────────────────────────
# Фабрики элементов
# ─────────────────────────────────────────────────────────────

def primary_button(text: str, tooltip: str = "", parent: Optional[QWidget] = None) -> QPushButton:
    """Главное действие вкладки: крупная кнопка акцента темы."""
    button = QPushButton(text, parent)
    button.setObjectName("primary")
    button.setMinimumHeight(40)
    button.setCursor(Qt.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def accent_button(text: str, tooltip: str = "", parent: Optional[QWidget] = None) -> QPushButton:
    """Финальное действие («Создать договор»): заметное, но спокойнее primary."""
    button = QPushButton(text, parent)
    button.setObjectName("accent")
    button.setMinimumHeight(36)
    button.setCursor(Qt.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def secondary_button(text: str, tooltip: str = "", parent: Optional[QWidget] = None) -> QPushButton:
    """Вспомогательное действие: кнопка с тонкой рамкой."""
    button = QPushButton(text, parent)
    button.setObjectName("secondary")
    button.setMinimumHeight(32)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def clipboard_button(
    text: str = "Вставить из буфера",
    tooltip: str = "Вставить текст из буфера обмена",
    parent: Optional[QWidget] = None,
) -> QPushButton:
    """Кнопка вставки с компактной иконкой буфера обмена."""
    button = secondary_button(text, tooltip=tooltip, parent=parent)
    button.setObjectName("clipboard")
    button.setProperty("fieldActionIcon", "paste")
    button.setIcon(_field_action_icon("paste"))
    button.setIconSize(QSize(18, 18))
    return button


def danger_button(text: str, tooltip: str = "", parent: Optional[QWidget] = None) -> QPushButton:
    """Опасное действие (удаление): цвет опасности в тексте и рамке, без заливки."""
    button = QPushButton(text, parent)
    button.setObjectName("danger")
    button.setMinimumHeight(32)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def ghost_button(text: str, size: int = 28, tooltip: str = "",
                 parent: Optional[QWidget] = None) -> QPushButton:
    """Кнопка-иконка у поля ввода: не отвлекает внимание."""
    button = QPushButton(parent)
    button.setObjectName("ghost")
    button.setFixedSize(size, size)
    button.setCursor(Qt.PointingHandCursor)
    if text in ("📋", "🧠"):
        action = "paste" if text == "📋" else "recognize"
        button.setProperty("fieldActionIcon", action)
        button.setIcon(_field_action_icon(action))
        button.setIconSize(QSize(18, 18))
    else:
        button.setText(text)
    if tooltip:
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
    return button


def _field_action_icon(action: str) -> QIcon:
    """Нейтральные значки для действий рядом с полями (цвета — из темы)."""
    p = palette()
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    line_color = QColor(p["text_muted"])
    painter.setPen(QPen(line_color, 1.6))
    if action == "paste":
        painter.setBrush(QColor(p["input_bg"]))
        painter.drawRoundedRect(4, 4, 12, 14, 2, 2)
        painter.setBrush(QColor(p["primary"]))
        painter.drawRoundedRect(7, 2, 6, 4, 1, 1)
        painter.setPen(QPen(line_color, 1.1))
        painter.drawLine(7, 9, 13, 9)
        painter.drawLine(7, 12, 13, 12)
        painter.drawLine(7, 15, 11, 15)
    else:
        painter.drawLine(10, 2, 10, 18)
        painter.drawLine(2, 10, 18, 10)
        painter.drawLine(5, 5, 15, 15)
        painter.drawLine(15, 5, 5, 15)
    painter.end()
    return QIcon(pixmap)


def required_label(text: str, required: bool = True) -> QLabel:
    """
    Подпись поля. У обязательного — звёздочка цветом акцента темы.

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
    # Исходный текст хранится в свойстве: по нему подпись перерисовывается
    # при смене темы (см. _refresh_themed_widgets).
    label.setProperty("requiredText", text)
    label.setProperty("isRequired", bool(required))

    if required:
        star_color = palette()["primary"]
        label.setText(
            f'{text} <span style="color:{star_color}; font-weight:bold;">*</span>'
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

    Возвращает (группа, форма): заголовок жирный и цвета заголовков темы,
    блок отделён рамкой и отступом от соседнего (см. QSS).
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
    title.setObjectName("pageTitle")
    layout.addWidget(title)

    if hint:
        hint_label = QLabel(hint)
        hint_label.setObjectName("pageHint")
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)

    return widget


__all__ = [
    # темы
    "available_themes", "theme_names", "theme_label", "theme_hint",
    "active_theme", "set_active_theme", "normalize", "palette", "is_dark",
    "build_palette", "qss", "apply_theme",
    # подсветка обязательных полей
    "required_empty_qss", "required_empty_qss_text", "readonly_field_qss",
    "deleted_row_color",
    "REQUIRED_EMPTY_QSS", "REQUIRED_EMPTY_QSS_TEXT",
    # палитра светлой темы (совместимость)
    "NAVY", "NAVY_HOVER", "PRIMARY", "PRIMARY_HOVER", "PRIMARY_PRESSED",
    "ACCENT", "ACCENT_HOVER", "DANGER", "DANGER_HOVER", "DANGER_BORDER",
    "MUTED", "TEXT", "BORDER", "BORDER_INPUT", "SOFT_BG", "HOVER_BG",
    "BAR_BG", "WARN_BG", "WARN_BORDER",
    # фабрики
    "primary_button", "accent_button", "secondary_button", "clipboard_button",
    "danger_button", "ghost_button", "required_label", "make_label",
    "set_label_required", "section_box", "page_title",
]
