#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Настройки Максимилиана (задание, часть D.3).

Диалог правит ТОЛЬКО раздел ``agent`` (core/settings_service.py):
включение, образ, режим показа, плавающий кот, запуск с открытой панелью,
горячая клавиша и лимиты. Существующие настройки программы (провайдер,
тема, ключи) здесь не трогаются — они живут в общем диалоге настроек.

Кнопка «Очистить историю диалога» удаляет историю ТЕКУЩЕЙ сессии; вторая
кнопка — «Удалить прошлые сессии» — чистит истории прежних запусков.
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QKeySequenceEdit, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QVBoxLayout,
)

from core.agent.memory import AgentMemory
from core.agent.personas import PERSONAS, persona_keys
from core.agent.tools import build_registry
from core.settings_service import (
    AGENT_LIMITS, AGENT_MODES, DEFAULT_AGENT_SETTINGS,
)
from ui import theme

logger = logging.getLogger("ui.agent_settings_dialog")

#: Подписи режимов показа агента.
MODE_TITLES = {
    "icon": "Только иконка",
    "panel": "Только панель",
    "hybrid": "Гибрид (иконка и панель)",
}


class AgentSettingsDialog(QDialog):
    """Диалог настроек ИИ-ассистента «Максимилиан»."""

    def __init__(self, settings: Dict[str, Any], parent=None):
        super().__init__(parent)
        self._settings = dict(settings or {})

        self.setWindowTitle("Настройки Максимилиана")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        # ── Основное ──
        main_group = QGroupBox("Максимилиан")
        form = QFormLayout(main_group)

        self.enabled = QCheckBox("Включить Максимилиана")
        self.enabled.setChecked(bool(self._settings.get("enabled", True)))
        self.enabled.setToolTip(
            "Выключенный помощник не показывается вовсе: ни иконки, ни "
            "панели, ни горячей клавиши, и запросы к GigaChat не уходят."
        )
        form.addRow("", self.enabled)

        self.name_edit = QLineEdit(str(self._settings.get("name", "Максимилиан")))
        self.name_edit.setToolTip("Как подписывать помощника в панели")
        form.addRow("Имя:", self.name_edit)

        self.avatar_combo = QComboBox()
        for key in persona_keys():
            self.avatar_combo.addItem(PERSONAS[key].title, key)
        self.avatar_combo.setToolTip(
            "Образ влияет только на аватар и тон ответов"
        )
        self._select(self.avatar_combo, "avatar")
        form.addRow("Аватар:", self.avatar_combo)

        self.avatar_hint = QLabel(PERSONAS[self.avatar_combo.currentData()].description)
        self.avatar_hint.setObjectName("mutedLabel")
        self.avatar_hint.setWordWrap(True)
        form.addRow("", self.avatar_hint)
        self.avatar_combo.currentIndexChanged.connect(self._on_avatar_changed)

        self.mode_combo = QComboBox()
        for key in AGENT_MODES:
            self.mode_combo.addItem(MODE_TITLES.get(key, key), key)
        self._select(self.mode_combo, "mode")
        form.addRow("Режим:", self.mode_combo)

        self.show_floating = QCheckBox("Показывать плавающего кота")
        self.show_floating.setChecked(bool(self._settings.get("show_floating", False)))
        self.show_floating.setToolTip(
            "Кот сидит в правом нижнем углу, моргает и открывает панель "
            "по клику. Его можно перетаскивать."
        )
        form.addRow("", self.show_floating)

        self.show_on_start = QCheckBox("Открывать панель при старте")
        self.show_on_start.setChecked(
            bool(self._settings.get("show_panel_on_start", False))
        )
        form.addRow("", self.show_on_start)

        layout.addWidget(main_group)

        # ── Клавиша и лимиты ──
        limits_group = QGroupBox("Клавиша и лимиты")
        limits_form = QFormLayout(limits_group)

        self.hotkey_edit = QKeySequenceEdit()
        self.hotkey_edit.setKeySequence(str(self._settings.get("hotkey", "Ctrl+M")))
        self.hotkey_edit.setToolTip("Горячая клавиша открывает и закрывает панель")
        limits_form.addRow("Горячая клавиша:", self.hotkey_edit)

        self.max_history = QSpinBox()
        low, high = AGENT_LIMITS["max_history"]
        self.max_history.setRange(low, high)
        self.max_history.setValue(int(self._settings.get(
            "max_history", DEFAULT_AGENT_SETTINGS["max_history"]
        )))
        self.max_history.setToolTip("Сколько последних сообщений помнит агент")
        limits_form.addRow("Помнить сообщений:", self.max_history)

        self.max_steps = QSpinBox()
        low, high = AGENT_LIMITS["max_tool_steps"]
        self.max_steps.setRange(low, high)
        self.max_steps.setValue(int(self._settings.get(
            "max_tool_steps", DEFAULT_AGENT_SETTINGS["max_tool_steps"]
        )))
        self.max_steps.setToolTip(
            "Сколько вызовов инструментов разрешено на один вопрос"
        )
        limits_form.addRow("Шагов на вопрос:", self.max_steps)

        layout.addWidget(limits_group)

        # ── История диалога ──
        history_group = QGroupBox("История диалога")
        history_layout = QVBoxLayout(history_group)
        hint = QLabel(
            "История живёт в базе (таблица agent_history) и не смешивается "
            "с данными справочников. Сессия — один запуск программы."
        )
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        history_layout.addWidget(hint)

        buttons = QHBoxLayout()
        self.btn_clear_history = theme.secondary_button(
            "Очистить историю диалога",
            tooltip="Удалить сообщения текущего запуска программы",
        )
        self.btn_clear_history.clicked.connect(self._on_clear_history)
        buttons.addWidget(self.btn_clear_history)

        self.btn_purge_sessions = theme.secondary_button(
            "Удалить прошлые сессии",
            tooltip="Удалить истории прежних запусков программы",
        )
        self.btn_purge_sessions.clicked.connect(self._on_purge_sessions)
        buttons.addWidget(self.btn_purge_sessions)
        buttons.addStretch()
        history_layout.addLayout(buttons)

        layout.addWidget(history_group)

        # ── Подсказка про возможности v1 ──
        abilities = QLabel(
            "💡 Максимилиан умеет: объяснять поля, искать в справочниках, "
            "подтягивать записи в форму и распознавать документы.\n"
            "Он не создаёт договоры, не удаляет данные и не меняет "
            "настройки программы. Заполнение формы всегда требует "
            "подтверждения."
        )
        abilities.setObjectName("mutedLabel")
        abilities.setWordWrap(True)
        layout.addWidget(abilities)

        # ── Кнопки диалога ──
        dialog_buttons = QHBoxLayout()
        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.clicked.connect(self.reject)
        dialog_buttons.addWidget(self.btn_cancel)
        dialog_buttons.addStretch()
        self.btn_save = QPushButton("Сохранить")
        self.btn_save.clicked.connect(self.accept)
        self.btn_save.setDefault(True)
        dialog_buttons.addWidget(self.btn_save)
        layout.addLayout(dialog_buttons)

        logger.debug("AgentSettingsDialog инициализирован")

    # ---------------------------------------------------------
    # Вспомогательное
    # ---------------------------------------------------------
    def _select(self, combo: QComboBox, key: str) -> None:
        """Ставит в списке значение настройки (неизвестное — первое)."""
        value = str(self._settings.get(key, DEFAULT_AGENT_SETTINGS.get(key, "")))
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _on_avatar_changed(self, index: int) -> None:
        """Показывает пояснение к выбранному образу."""
        key = self.avatar_combo.itemData(index)
        self.avatar_hint.setText(PERSONAS.get(key, PERSONAS["cat_scientist"]).description)

    # ---------------------------------------------------------
    # История
    # ---------------------------------------------------------
    def _memory(self) -> AgentMemory:
        """Память текущей сессии без загрузки истории из базы."""
        return AgentMemory(autoload=False)

    def _on_clear_history(self) -> None:
        """Очищает историю текущей сессии."""
        answer = QMessageBox.question(
            self, "История диалога",
            "Удалить историю текущего диалога?\n\n"
            "Данные справочников и договоры не затрагиваются.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        removed = self._memory().clear()
        QMessageBox.information(
            self, "История диалога",
            f"История очищена (удалено записей: {removed}).",
        )

    def _on_purge_sessions(self) -> None:
        """Удаляет истории прошлых сессий."""
        answer = QMessageBox.question(
            self, "История диалога",
            "Удалить истории прошлых запусков программы?\n\n"
            "История текущего диалога останется.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        removed = self._memory().purge_other_sessions()
        QMessageBox.information(
            self, "История диалога",
            f"Удалено записей прошлых сессий: {removed}.",
        )

    # ---------------------------------------------------------
    # Результат
    # ---------------------------------------------------------
    def get_settings(self) -> Dict[str, Any]:
        """Раздел agent с новыми значениями (для SettingsService.update_agent)."""
        hotkey = self.hotkey_edit.keySequence().toString()
        return {
            "enabled": self.enabled.isChecked(),
            "name": self.name_edit.text().strip() or DEFAULT_AGENT_SETTINGS["name"],
            "avatar": self.avatar_combo.currentData() or DEFAULT_AGENT_SETTINGS["avatar"],
            "mode": self.mode_combo.currentData() or DEFAULT_AGENT_SETTINGS["mode"],
            "show_floating": self.show_floating.isChecked(),
            "show_panel_on_start": self.show_on_start.isChecked(),
            "hotkey": hotkey or DEFAULT_AGENT_SETTINGS["hotkey"],
            "max_history": self.max_history.value(),
            "max_tool_steps": self.max_steps.value(),
        }


def tool_titles() -> Dict[str, str]:
    """
    Имена инструментов агента — для подсказок и диагностики.

    Функция держит зависимость диалога от реестра инструментов явной: если
    инструментов не станет, это будет видно тестом, а не молчанием.
    """
    registry = build_registry()
    return {tool.name: tool.description for tool in registry}


__all__ = ["MODE_TITLES", "AgentSettingsDialog", "tool_titles"]
