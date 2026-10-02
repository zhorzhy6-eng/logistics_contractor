#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Палитры тем оформления приложения.

Здесь лежат только ДАННЫЕ: цвета, шрифты, радиусы и плотность для каждой
темы. Никакой логики и никакой вёрстки — QSS собирает ui/theme.py из этих
значений (см. theme.qss()).

Темы
----
  * ``classic``  — «Светлая тема»: светлый фон, спокойные синие акценты;
  * ``dark_pro`` — «Тёмная тема»: тёмная студия из макета «02-dark-pro»,
    фон #0F172A, бирюзовый акцент #2DD4BF.

Ключи тем (``classic``/``dark_pro``) — часть формата настроек: они лежат
в ``config/settings.json`` (``ui_theme``), поэтому меняются только
отображаемые названия. Старые значения ``fluent_light``/``fluent_dark``
из прежних версий приложения приводятся к светлой/тёмной теме
(см. ``_ALIASES``), чтобы сохранённый выбор не терялся.

Ключи у обеих тем одинаковые — шаблон QSS не должен зависеть от того,
какая тема активна.
"""

from typing import Any, Dict, List

#: Ключ темы по умолчанию. Совпадает с оформлением, которое приложение
#: использовало до появления переключателя: тесты и старый код, вызывающие
#: theme.apply_theme() без имени темы, обязаны получить именно её.
DEFAULT_THEME = "classic"


# ─────────────────────────────────────────────────────────────
# 1. Светлая тема (палитра ui/theme.py до появления тёмной)
# ─────────────────────────────────────────────────────────────

CLASSIC: Dict[str, Any] = {
    "key": "classic",
    "label": "Светлая тема",
    "hint": "Светлый фон, синее главное действие",
    "dark": False,

    "font_family": '"Segoe UI"',
    "font_size": "10pt",
    "font_size_small": "9pt",
    "radius": "8px",
    "radius_input": "6px",
    "radius_small": "6px",
    "nav_width": 240,

    # Окно и поверхности
    "window_bg": "#F4F7FB",
    "surface": "#FFFFFF",
    "surface_alt": "#F8FAFC",
    "surface_header": "#F1F5F9",
    "bar_bg": "#F1F5F9",
    "hover_bg": "#EEF2F7",
    "disabled_bg": "#F1F5F9",

    # Границы
    "border": "#D7E0EA",
    "border_input": "#AEBCCC",
    "border_strong": "#AEBCCC",

    # Текст
    "text": "#1F2937",
    "text_muted": "#64748B",
    "text_disabled": "#9AA5B1",
    "heading": "#1E3A5F",
    "heading_hover": "#16304F",

    # Главное действие
    "primary": "#1E5FA8",
    "primary_hover": "#164B85",
    "primary_pressed": "#123E6E",
    "primary_text": "#FFFFFF",
    "primary_disabled_bg": "#A9BFD8",
    "primary_disabled_text": "#F1F5F9",

    # Финальное действие («Создать договор»)
    "accent": "#2F855A",
    "accent_hover": "#276749",
    "accent_pressed": "#1F5137",
    "accent_text": "#FFFFFF",
    "accent_disabled_bg": "#9DBFAE",
    "accent_disabled_text": "#F1F5F9",

    # Опасное действие
    "danger": "#B42318",
    "danger_hover_bg": "#FFF5F5",
    "danger_border": "#F1C0BB",

    # Кнопка «Вставить из буфера»
    "clipboard_bg": "#FFF7E4",
    "clipboard_text": "#6D5421",
    "clipboard_border": "#E7C979",
    "clipboard_hover_bg": "#FFF0C2",
    "clipboard_hover_border": "#D9B85D",
    "clipboard_pressed_bg": "#FBE6A7",

    # Обязательное поле, пока пусто
    "warn_bg": "#FFF8E1",
    "warn_border": "#E6C86E",
    "warn_text": "#8A6D1F",
    "ok_text": "#2F855A",
    "err_text": "#B42318",

    # Панели главного окна
    "header_bg": "#FFFFFF",
    "header_border": "#D7E0EA",
    "header_text": "#1E3A5F",
    "header_sub_text": "#64748B",
    "action_bar_bg": "#FFFFFF",
    "panel_bg": "#FFFFFF",
    "panel_border": "#D7E0EA",
    "recog_bg": "#FFFFFF",
    "recog_border": "#D7E0EA",
    "recog_title": "#1E3A5F",

    # Сайдбар
    "nav_bg": "#FFFFFF",
    "nav_border": "#D7E0EA",
    "nav_item_text": "#64748B",
    "nav_item_hover_bg": "#EEF2F7",
    "nav_item_hover_text": "#1E3A5F",
    "nav_item_active_bg": "#E8F1FA",
    "nav_item_active_text": "#1E5FA8",
    "nav_item_active_border": "#1E5FA8",
    "nav_indicator": "#1E5FA8",

    # Вкладки (верхние ярлыки; в приложении скрыты, правила сохранены)
    "tab_text": "#64748B",
    "tab_hover_bg": "#EEF2F7",
    "tab_selected_text": "#1E3A5F",
    "tab_selected_bg": "#FFFFFF",
    "tab_indicator": "#1E5FA8",

    # Поля ввода
    "input_bg": "#FFFFFF",
    "input_text": "#1F2937",
    "placeholder_text": "#94A3B8",
    "focus_border": "#1E5FA8",

    # Таблицы
    "table_bg": "#FFFFFF",
    "table_alt_bg": "#F8FAFC",
    "table_header_bg": "#F1F5F9",
    "table_header_text": "#1E3A5F",
    "table_grid": "#D7E0EA",
    "table_row_hover": "#EEF2F7",

    # Полосы прокрутки
    "scroll_track": "transparent",
    "scroll_handle": "#C5D0DE",
    "scroll_handle_hover": "#9EADC0",

    # Прочее
    "tooltip_bg": "#1E3A5F",
    "tooltip_text": "#FFFFFF",
    "selection_bg": "#1E5FA8",
    "selection_text": "#FFFFFF",
    "progress_bg": "#FFFFFF",
    "progress_chunk": "#1E5FA8",
    "menu_bg": "#FFFFFF",
    "menu_text": "#1F2937",
    "menu_hover_bg": "#EEF2F7",
    "dup_row_text": "#B42318",
}


# ─────────────────────────────────────────────────────────────
# 2. Тёмная тема — макет «02-dark-pro»
# ─────────────────────────────────────────────────────────────

DARK_PRO: Dict[str, Any] = {
    "key": "dark_pro",
    "label": "Тёмная тема",
    "hint": "Тёмный фон #0F172A, бирюзовый акцент",
    "dark": True,

    "font_family": '"Segoe UI"',
    "font_size": "10.5pt",
    "font_size_small": "9pt",
    "radius": "12px",
    "radius_input": "10px",
    "radius_small": "9px",
    "nav_width": 232,

    "window_bg": "#070B14",
    "surface": "#111C33",
    "surface_alt": "#0E1A2E",
    "surface_header": "#16213C",
    "bar_bg": "#111C33",
    "hover_bg": "#1D2B4A",
    "disabled_bg": "#0E1A2E",

    "border": "#1E2A44",
    "border_input": "#26344F",
    "border_strong": "#33456A",

    "text": "#E2E8F0",
    "text_muted": "#8FA3BF",
    "text_disabled": "#6F86A3",
    "heading": "#F1F5FB",
    "heading_hover": "#5EEAD4",

    "primary": "#2DD4BF",
    "primary_hover": "#4FE3D0",
    "primary_pressed": "#14B8A6",
    "primary_text": "#06231F",
    "primary_disabled_bg": "#1B3A44",
    "primary_disabled_text": "#6F86A3",

    "accent": "#A3E635",
    "accent_hover": "#BEF264",
    "accent_pressed": "#84CC16",
    "accent_text": "#16240A",
    "accent_disabled_bg": "#35431F",
    "accent_disabled_text": "#6F86A3",

    "danger": "#FB7185",
    "danger_hover_bg": "#2A1622",
    "danger_border": "#7A3145",

    "clipboard_bg": "#16213C",
    "clipboard_text": "#CFE0F5",
    "clipboard_border": "#33456A",
    "clipboard_hover_bg": "#1D2B4A",
    "clipboard_hover_border": "#2DD4BF",
    "clipboard_pressed_bg": "#123043",

    "warn_bg": "#2E2612",
    "warn_border": "#FBBF24",
    "warn_text": "#FBBF24",
    "ok_text": "#A3E635",
    "err_text": "#FB7185",

    "header_bg": "#111C33",
    "header_border": "#1E2A44",
    "header_text": "#F1F5FB",
    "header_sub_text": "#8FA3BF",
    "action_bar_bg": "#111C33",
    "panel_bg": "#111C33",
    "panel_border": "#1E2A44",
    "recog_bg": "#11202E",
    "recog_border": "#2A5A57",
    "recog_title": "#5EEAD4",

    "nav_bg": "#111C33",
    "nav_border": "#1E2A44",
    "nav_item_text": "#8FA3BF",
    "nav_item_hover_bg": "#1B2A47",
    "nav_item_hover_text": "#E2E8F0",
    "nav_item_active_bg": "#14332F",
    "nav_item_active_text": "#5EEAD4",
    "nav_item_active_border": "#2A5A57",
    "nav_indicator": "#2DD4BF",

    "tab_text": "#8FA3BF",
    "tab_hover_bg": "#1B2A47",
    "tab_selected_text": "#5EEAD4",
    "tab_selected_bg": "#111C33",
    "tab_indicator": "#2DD4BF",

    "input_bg": "#0B1424",
    "input_text": "#E2E8F0",
    "placeholder_text": "#64789B",
    "focus_border": "#2DD4BF",

    "table_bg": "#0E1A2E",
    "table_alt_bg": "#0E1A2E",
    "table_header_bg": "#111C33",
    "table_header_text": "#7C93B0",
    "table_grid": "#1E2A44",
    "table_row_hover": "#14332F",

    "scroll_track": "#0B1424",
    "scroll_handle": "#24344F",
    "scroll_handle_hover": "#2DD4BF",

    "tooltip_bg": "#16213C",
    "tooltip_text": "#E2E8F0",
    "selection_bg": "#2DD4BF",
    "selection_text": "#06231F",
    "progress_bg": "#0B1424",
    "progress_chunk": "#2DD4BF",
    "menu_bg": "#16213C",
    "menu_text": "#E2E8F0",
    "menu_hover_bg": "#1D2B4A",
    "dup_row_text": "#FB7185",
}


# ─────────────────────────────────────────────────────────────
# Реестр тем
# ─────────────────────────────────────────────────────────────

THEMES: Dict[str, Dict[str, Any]] = {
    CLASSIC["key"]: CLASSIC,
    DARK_PRO["key"]: DARK_PRO,
}

#: Порядок тем в переключателе.
THEME_ORDER: List[str] = ["classic", "dark_pro"]

#: Дружественные варианты записи (settings.json, аргументы командной строки).
#: Значения прежних версий (fluent_light/fluent_dark) приводятся к светлой и
#: тёмной теме по светлоте: сохранённый выбор пользователя не теряется.
_ALIASES: Dict[str, str] = {
    "": DEFAULT_THEME,
    "classic": "classic",
    "классика": "classic",
    "классическая": "classic",
    "light": "classic",
    "default": "classic",
    "светлая": "classic",
    "светлая_тема": "classic",
    "fluent": "classic",
    "fluent_light": "classic",
    "fluent-light": "classic",
    "dark_pro": "dark_pro",
    "dark-pro": "dark_pro",
    "darkpro": "dark_pro",
    "dark": "dark_pro",
    "дарк": "dark_pro",
    "тёмная": "dark_pro",
    "темная": "dark_pro",
    "тёмная_тема": "dark_pro",
    "темная_тема": "dark_pro",
    "fluent_dark": "dark_pro",
    "fluent-dark": "dark_pro",
}


def normalize(name: Any) -> str:
    """
    Приводит имя темы к ключу реестра.

    Неизвестное имя — не ошибка: возвращается тема по умолчанию, чтобы
    испорченный settings.json не оставлял пользователя без оформления.
    """
    key = str(name or "").strip().casefold().replace(" ", "_")
    return _ALIASES.get(key, DEFAULT_THEME)


def get_palette(name: Any = None) -> Dict[str, Any]:
    """Палитра темы по имени (с учётом алиасов)."""
    return THEMES[normalize(name)]


def theme_names() -> List[str]:
    """Ключи всех тем в порядке переключателя."""
    return list(THEME_ORDER)


def theme_label(name: Any) -> str:
    """Подпись темы для интерфейса."""
    return str(get_palette(name)["label"])


def theme_hint(name: Any) -> str:
    """Короткое пояснение к теме (показывается в списке настроек)."""
    return str(get_palette(name)["hint"])


def is_dark(name: Any) -> bool:
    """Тёмная ли тема (нужно для подбора набора иконок)."""
    return bool(get_palette(name)["dark"])


def label_for_key() -> Dict[str, str]:
    """Словарь «ключ темы → подпись» (для QComboBox в настройках)."""
    return {key: str(THEMES[key]["label"]) for key in THEME_ORDER}


def find_key_by_label(label: str) -> str:
    """Обратный поиск: подпись из QComboBox → ключ темы."""
    for key in THEME_ORDER:
        if str(THEMES[key]["label"]) == str(label):
            return key
    return DEFAULT_THEME


__all__ = [
    "DEFAULT_THEME",
    "THEMES",
    "THEME_ORDER",
    "CLASSIC",
    "DARK_PRO",
    "normalize",
    "get_palette",
    "theme_names",
    "theme_label",
    "theme_hint",
    "is_dark",
    "label_for_key",
    "find_key_by_label",
]
