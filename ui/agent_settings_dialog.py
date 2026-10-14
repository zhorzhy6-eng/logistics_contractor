#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Настройки Максимилиана (задание, часть D.3).

Диалог правит ТОЛЬКО раздел ``agent`` (core/settings_service.py):
включение, образ, режим показа, плавающий кот, запуск с открытой панелью,
горячая клавиша, лимиты и подсказки без вопроса. Существующие настройки
программы (провайдер, тема, ключи) здесь не трогаются — они живут в общем
диалоге настроек.

ИМЯ ПОМОЩНИКА ЗДЕСЬ НЕ РЕДАКТИРУЕТСЯ: Максимилиан — всегда Максимилиан
(core/settings_service.py::AGENT_NAME). Поля «Имя» в диалоге нет вовсе;
значение из старого файла настроек игнорируется.

Кнопка «Очистить историю диалога» удаляет историю ТЕКУЩЕЙ сессии; вторая
кнопка — «Удалить прошлые сессии» — чистит истории прежних запусков.
Отдельный раздел «Что Максимилиан помнит» показывает алиасы, привычки и
исправления (часть 5): их можно удалить по одному или забыть все сразу.
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel, QMessageBox,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.agent import long_memory
from core.agent.memory import AgentMemory
from core.agent.personas import PERSONAS, persona_keys
from core.agent.tools import build_registry
from core.settings_service import (
    AGENT_LIMITS, AGENT_MODES, AGENT_NAME, DEFAULT_AGENT_SETTINGS,
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

        # ── Имя помощника: только показ, менять нельзя (часть B) ──
        # Поля ввода здесь НЕТ намеренно: имя зафиксировано (AGENT_NAME),
        # и редактировать его нечем — ни в диалоге, ни в файле настроек.
        self.name_label = QLabel(AGENT_NAME)
        self.name_label.setToolTip(
            "Имя помощника зафиксировано: Максимилиан — всегда Максимилиан."
        )
        form.addRow("Имя:", self.name_label)

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

        # ── Подсказки без вопроса (задание, часть 4) ──
        hints_group = QGroupBox("Подсказки без вопроса")
        hints_form = QFormLayout(hints_group)

        self.proactive = QCheckBox("Подсказывать, чего не хватает в форме")
        self.proactive.setChecked(bool(self._settings.get("proactive", False)))
        self.proactive.setToolTip(
            "Максимилиан сам заметит незаполненные обязательные поля и "
            "истекающие сроки. Подсказка показывается не чаще одной в "
            "заданный интервал."
        )
        hints_form.addRow("", self.proactive)

        self.proactive_interval = QSpinBox()
        low, high = AGENT_LIMITS["proactive_interval"]
        self.proactive_interval.setRange(low, high)
        self.proactive_interval.setSuffix(" с")
        self.proactive_interval.setValue(int(self._settings.get(
            "proactive_interval", DEFAULT_AGENT_SETTINGS["proactive_interval"]
        )))
        self.proactive_interval.setToolTip(
            "Пауза между подсказками: одна подсказка в этот интервал"
        )
        hints_form.addRow("Пауза между подсказками:", self.proactive_interval)

        layout.addWidget(hints_group)

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

        # ── Что Максимилиан помнит (часть 5) ──
        layout.addWidget(self._build_memory_group())

        # ── Подсказка про возможности v1 ──
        abilities = QLabel(
            "💡 Максимилиан умеет: объяснять поля, искать в справочниках, "
            "подтягивать записи в форму, распознавать документы, считать "
            "суммы и топы.\n"
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
    # Что Максимилиан помнит (часть 5)
    # ---------------------------------------------------------
    def _build_memory_group(self) -> QGroupBox:
        """
        Раздел «Что Максимилиан помнит»: список, удаление, «Забыть всё».

        Показываются все записи памяти (алиасы, привычки, исправления) с
        пояснением вида. Удалить можно по одной строке или всё сразу —
        справочники и договоры это не затрагивает: у помощника своя
        таблица (db/crud/agent_memory.py).
        """
        group = QGroupBox("Что Максимилиан помнит")
        layout = QVBoxLayout(group)

        hint = QLabel(
            "Память помощника: алиасы («Ромашка» = ООО «Ромашка»), привычки "
            "(«всегда НДС 20 % для Ромашки») и исправления. Она подсказывает "
            "помощнику, как вы называете записи, и не меняет данные в базе."
        )
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.memory_table = QTableWidget(0, 3)
        self.memory_table.setHorizontalHeaderLabels(["Вид", "Что", "Значение"])
        self.memory_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.memory_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.memory_table.setWordWrap(False)
        self.memory_table.verticalHeader().setVisible(False)
        header = self.memory_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.memory_table.setMinimumHeight(140)
        layout.addWidget(self.memory_table)

        self.memory_hint = QLabel("")
        self.memory_hint.setObjectName("mutedLabel")
        layout.addWidget(self.memory_hint)

        buttons = QHBoxLayout()
        self.btn_forget = theme.danger_button(
            "Удалить запись", tooltip="Удалить выбранную запись из памяти"
        )
        self.btn_forget.clicked.connect(self._on_forget_selected)
        buttons.addWidget(self.btn_forget)

        self.btn_forget_all = theme.danger_button(
            "Забыть всё", tooltip="Очистить память помощника целиком"
        )
        self.btn_forget_all.clicked.connect(self._on_forget_all)
        buttons.addWidget(self.btn_forget_all)

        self.btn_refresh_memory = theme.secondary_button(
            "Обновить", tooltip="Перечитать память из базы"
        )
        self.btn_refresh_memory.clicked.connect(self.reload_memory)
        buttons.addWidget(self.btn_refresh_memory)

        buttons.addStretch()
        layout.addLayout(buttons)

        self.reload_memory()
        return group

    def reload_memory(self) -> None:
        """Перечитывает память из базы и заполняет таблицу."""
        try:
            records = long_memory.list_records()
        except Exception as e:  # noqa: BLE001 — настройки не должны падать
            logger.warning("Память агента не прочитана: %s", e)
            records = []

        self.memory_table.setRowCount(len(records))
        for row, item in enumerate(records):
            kind_item = QTableWidgetItem(str(item.get("kind_title") or ""))
            kind_item.setToolTip(str(item.get("kind_hint") or ""))
            name_item = QTableWidgetItem(str(item.get("key") or ""))
            value_item = QTableWidgetItem(str(item.get("value") or ""))
            #: ID записи — в данных строки: по нему удаляем.
            kind_item.setData(Qt.UserRole, item.get("id"))
            self.memory_table.setItem(row, 0, kind_item)
            self.memory_table.setItem(row, 1, name_item)
            self.memory_table.setItem(row, 2, value_item)

        self.memory_hint.setText(
            f"Записей в памяти: {len(records)}" if records
            else "Пока ничего не запомнил."
        )

    def _selected_memory_id(self) -> Any:
        """ID выбранной записи памяти (None — ничего не выбрано)."""
        row = self.memory_table.currentRow()
        if row < 0:
            return None
        item = self.memory_table.item(row, 0)
        return None if item is None else item.data(Qt.UserRole)

    def _on_forget_selected(self) -> None:
        """Удаляет выбранную запись памяти."""
        record_id = self._selected_memory_id()
        if record_id is None:
            QMessageBox.information(
                self, "Память", "Выберите запись в списке."
            )
            return
        long_memory.forget(int(record_id))
        self.reload_memory()

    def _on_forget_all(self) -> None:
        """Очищает память помощника целиком («Забыть всё»)."""
        answer = QMessageBox.question(
            self, "Память",
            "Забыть всё, что Максимилиан запомнил?\n\n"
            "Справочники, договоры и история диалога не затрагиваются.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        removed = long_memory.forget_all()
        self.reload_memory()
        QMessageBox.information(
            self, "Память", f"Удалено записей: {removed}."
        )

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
            # Имя всегда одно и то же: поля ввода в диалоге нет (часть B).
            "name": AGENT_NAME,
            "avatar": self.avatar_combo.currentData() or DEFAULT_AGENT_SETTINGS["avatar"],
            "mode": self.mode_combo.currentData() or DEFAULT_AGENT_SETTINGS["mode"],
            "show_floating": self.show_floating.isChecked(),
            "show_panel_on_start": self.show_on_start.isChecked(),
            "hotkey": hotkey or DEFAULT_AGENT_SETTINGS["hotkey"],
            "max_history": self.max_history.value(),
            "max_tool_steps": self.max_steps.value(),
            "proactive": self.proactive.isChecked(),
            "proactive_interval": self.proactive_interval.value(),
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
