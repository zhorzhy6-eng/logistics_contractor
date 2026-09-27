#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог настроек приложения.
Позволяет выбрать провайдера распознавания (Ollama / GigaChat)
и настроить его параметры.
"""

import logging
from typing import Dict, Any

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit,
    QSpinBox, QPushButton, QHBoxLayout, QLabel,
    QComboBox, QGroupBox, QMessageBox, QCheckBox,
)
from PyQt5.QtCore import Qt

from core import secrets_store

logger = logging.getLogger("ui.settings_dialog")


class SettingsDialog(QDialog):
    """
    Диалог настроек: провайдер + его параметры.
    """

    def __init__(self, settings: Dict[str, Any], parent=None):
        super().__init__(parent)

        self.setWindowTitle("⚙ Настройки")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        # ── Выбор провайдера ──
        provider_group = QGroupBox("Провайдер распознавания")
        provider_layout = QFormLayout(provider_group)

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["Ollama (локально)", "GigaChat (облако)"])
        current_provider = settings.get("provider", "ollama")
        if current_provider == "gigachat":
            self.provider_combo.setCurrentIndex(1)
        else:
            self.provider_combo.setCurrentIndex(0)

        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_layout.addRow("Провайдер:", self.provider_combo)

        layout.addWidget(provider_group)

        # ═══════════════════════════════════════════════════════
        # ── Ollama ──
        # ═══════════════════════════════════════════════════════
        self.ollama_group = QGroupBox("Ollama")
        ollama_layout = QFormLayout(self.ollama_group)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("http://127.0.0.1:11434")
        self.url_edit.setText(settings.get("ollama_url", "http://127.0.0.1:11434"))
        ollama_layout.addRow("URL сервера:", self.url_edit)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("qwen2.5:7b")
        self.model_edit.setText(settings.get("ollama_model", "qwen2.5:7b"))
        ollama_layout.addRow("Модель:", self.model_edit)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 600)
        self.timeout_spin.setSuffix(" сек")
        self.timeout_spin.setValue(settings.get("ollama_timeout", 150))
        ollama_layout.addRow("Таймаут:", self.timeout_spin)

        layout.addWidget(self.ollama_group)

        # ═══════════════════════════════════════════════════════
        # ── GigaChat ──
        # ═══════════════════════════════════════════════════════
        self.gigachat_group = QGroupBox("GigaChat")
        gigachat_layout = QFormLayout(self.gigachat_group)

        # ── Ключ авторизации больше НЕ вводится здесь (Шаг 2 задания) ──
        # Он хранится в системном хранилище (Windows Credential Manager)
        # и не должен попадать ни в этот диалог, ни в settings.json.
        self.key_status_label = QLabel(secrets_store.describe_key_state())
        self.key_status_label.setWordWrap(True)
        self.key_status_label.setStyleSheet("color: #555; font-size: 11px;")
        gigachat_layout.addRow("Ключ авторизации:", self.key_status_label)

        self.btn_check_key = QPushButton("Обновить статус ключа")
        self.btn_check_key.clicked.connect(self._on_check_key)
        gigachat_layout.addRow("", self.btn_check_key)

        self.gigachat_model_edit = QLineEdit()
        self.gigachat_model_edit.setPlaceholderText("GigaChat")
        self.gigachat_model_edit.setText(
            settings.get("gigachat_model", "GigaChat")
        )
        gigachat_layout.addRow("Модель:", self.gigachat_model_edit)

        self.gigachat_scope_combo = QComboBox()
        self.gigachat_scope_combo.addItems([
            "GIGACHAT_API_PERS",
            "GIGACHAT_API_B2B",
            "GIGACHAT_API_CORP",
        ])
        current_scope = settings.get("gigachat_scope", "GIGACHAT_API_PERS")
        idx = self.gigachat_scope_combo.findText(current_scope)
        if idx >= 0:
            self.gigachat_scope_combo.setCurrentIndex(idx)
        gigachat_layout.addRow("Scope:", self.gigachat_scope_combo)

        self.gigachat_timeout_spin = QSpinBox()
        self.gigachat_timeout_spin.setRange(10, 600)
        self.gigachat_timeout_spin.setSuffix(" сек")
        self.gigachat_timeout_spin.setValue(
            settings.get("gigachat_timeout", 150)
        )
        gigachat_layout.addRow("Таймаут:", self.gigachat_timeout_spin)

        self.gigachat_verify_ssl_checkbox = QCheckBox("Проверять SSL-сертификат")
        self.gigachat_verify_ssl_checkbox.setChecked(
            settings.get("gigachat_verify_ssl", False)
        )
        gigachat_layout.addRow("", self.gigachat_verify_ssl_checkbox)

        layout.addWidget(self.gigachat_group)

        # ── Подсказка ──
        hint_label = QLabel(
            "💡 GigaChat: ключ авторизации берите в личном кабинете "
            "Sber Developer Studio.\n"
            "💡 Ollama: для распознавания изображений нужны "
            "мультимодальные модели (llava, pixtral, bakllava)."
        )
        hint_label.setStyleSheet("color: #666; font-size: 11px;")
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)

        # ── Кнопки ──
        button_layout = QHBoxLayout()

        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        button_layout.addStretch()

        self.btn_save = QPushButton("Сохранить")
        self.btn_save.clicked.connect(self.accept)
        self.btn_save.setDefault(True)
        button_layout.addWidget(self.btn_save)

        layout.addLayout(button_layout)

        # Показать/скрыть группы под текущий провайдер
        self._on_provider_changed(self.provider_combo.currentIndex())

        logger.debug("SettingsDialog инициализирован")

    # ─────────────────────────────────────────────────────────
    # Служебные методы
    # ─────────────────────────────────────────────────────────

    def _on_provider_changed(self, index: int) -> None:
        is_gigachat = (index == 1)
        self.ollama_group.setVisible(not is_gigachat)
        self.gigachat_group.setVisible(is_gigachat)

        # диалог подгоняет размер под содержимое
        self.adjustSize()

    def _on_check_key(self) -> None:
        """Обновляет статус ключа, не показывая его значение."""
        state = secrets_store.describe_key_state()
        self.key_status_label.setText(state)
        if not secrets_store.has_gigachat_key():
            QMessageBox.information(
                self, "Ключ GigaChat",
                f"{secrets_store.MISSING_KEY_MESSAGE}\n\n"
                "Диалог настроек ключ не хранит: он попадает в системное "
                "хранилище Windows, а не в config/settings.json."
            )
        else:
            QMessageBox.information(
                self, "Ключ GigaChat",
                f"{state}\n\nЗначение ключа нигде в проекте не сохраняется."
            )

    # ─────────────────────────────────────────────────────────
    # Получение настроек
    # ─────────────────────────────────────────────────────────

    def get_settings(self) -> Dict[str, Any]:
        provider = "gigachat" if self.provider_combo.currentIndex() == 1 else "ollama"

        return {
            "provider": provider,

            # Ollama
            "ollama_url": self.url_edit.text().strip(),
            "ollama_model": self.model_edit.text().strip(),
            "ollama_timeout": self.timeout_spin.value(),

            # GigaChat (ключ здесь отсутствует: он в системном хранилище)
            "gigachat_model": self.gigachat_model_edit.text().strip() or "GigaChat",
            "gigachat_scope": self.gigachat_scope_combo.currentText(),
            "gigachat_timeout": self.gigachat_timeout_spin.value(),
            "gigachat_verify_ssl": self.gigachat_verify_ssl_checkbox.isChecked(),
        }