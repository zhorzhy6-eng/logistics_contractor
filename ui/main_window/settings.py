# -*- coding: utf-8 -*-
"""
Настройки приложения и клиент GigaChat.

Этап 6 (16.10.2026), подэтап 1.I: сюда переехали из прежнего
`ui/main_window.py` инициализация клиента GigaChat (ШАГ 5 рефакторинга) и
диалог настроек. Логика не менялась — только переезд.

Ключ доступа берётся из системного хранилища Windows (Credential Manager),
а не из `config/settings.json` — см. ШАГ 2 задания по безопасности. Поэтому
смена ключа не требует перезапуска приложения: `_init_gigachat_client`
пересоздаёт клиента прямо из диалога настроек.

ВНИМАНИЕ: строка `"gigachat_model", "GigaChat-3-Pro"` ниже — предмет
проверки `tests/test_gigachat3_switch.py` (приватная часть): шаблон ищет
дефолт модели поколения GigaChat-2 и не должен его находить. Меняя дефолт,
сверьтесь с `ui/windows/*/window.py` и `tools/run_import_bench.py`.
"""

import logging
from typing import Optional

from PyQt5.QtWidgets import QDialog, QMessageBox

from core import audit, secrets_store
from core.gigachat_client import GigaChatClient
from core.secrets_store import MISSING_KEY_MESSAGE

from ui import theme
from ui.settings_dialog import SettingsDialog

# Имя логгера задано ЯВНО (а не __name__): после переезда в пакет
# `ui.main_window.settings` прежние записи выпали бы из настройки
# `config/logging_config.py` (APP_LOGGERS = "core", "ui", "db", ...).
logger = logging.getLogger("ui.main_window")


def _validate_gigachat_key(key: str) -> Optional[str]:
    """
    Проверяет ключ GigaChat до создания клиента.

    :return: текст ошибки для оператора или None, если ключ пригоден.
    """
    if not key:
        return (
            f"{MISSING_KEY_MESSAGE}\n\n"
            "Ключ хранится в системном хранилище Windows "
            "(см. core/secrets_store.py), а не в файлах проекта."
        )
    try:
        key.encode("ascii")
    except UnicodeEncodeError as e:
        return (
            f"Ключ GigaChat содержит не-ASCII символы (позиция {e.start}).\n\n"
            f"HTTP-заголовок Authorization допускает только latin-1. "
            f"Проверьте ключ: python set_key.py --check"
        )
    return None


class SettingsMixin:
    """
    Настройки приложения: сервис настроек, клиент GigaChat, диалог.

    Своего `__init__` у миксина нет: `self.settings_service` создаёт
    `MainWindow.__init__` ДО вызова `_init_gigachat_client`, потому что
    значения читаются именно из сервиса.
    """

    def _init_gigachat_client(self, show_dialog: bool = True) -> bool:
        """
        Создаёт (или пересоздаёт) клиента GigaChat по текущим настройкам.

        Ключ берётся из системного хранилища (Windows Credential Manager),
        а не из config/settings.json — см. Шаг 2 задания по безопасности.
        Вызывается при старте и после сохранения настроек, поэтому
        перезапуск приложения для смены ключа не нужен.
        """
        self.gigachat = None

        auth_key = secrets_store.get_gigachat_key() or ""
        key_error = _validate_gigachat_key(auth_key)
        if key_error:
            logger.error(f"Ключ GigaChat недоступен: {key_error}")
            audit.log_denied("gigachat_client_init", "ключ не найден в хранилище")
            if show_dialog:
                QMessageBox.critical(
                    self, "Ошибка GigaChat",
                    f"Не удалось инициализировать GigaChat:\n\n{key_error}"
                )
            return False

        try:
            self.gigachat = GigaChatClient(
                auth_key=auth_key,
                scope=self.settings_service.get_str("gigachat_scope", "GIGACHAT_API_PERS"),
                model=self.settings_service.get_str("gigachat_model", "GigaChat-3-Pro"),
                timeout=self.settings_service.get_int("gigachat_timeout", 150),
                verify_ssl=self.settings_service.get_bool("gigachat_verify_ssl", True),
                ca_bundle=self.settings_service.get_str("gigachat_ca_bundle", "") or None,
            )
            logger.info("GigaChatClient инициализирован")
            return True
        except Exception as e:
            logger.exception("Не удалось инициализировать GigaChat")
            if show_dialog:
                QMessageBox.critical(
                    self, "Ошибка GigaChat",
                    f"Не удалось инициализировать GigaChat:\n{e}"
                )
            return False

    def _on_open_settings(self):
        """Диалог настроек: провайдер и его параметры."""
        logger.info("Открытие диалога настроек")
        self._log_ui_action("нажата кнопка «Настройки»")
        audit.log_event("settings_opened")

        dialog = SettingsDialog(self.settings_service.as_dict(), self)
        if dialog.exec_() != QDialog.Accepted:
            logger.info("Настройки не изменены")
            self._log_ui_action("диалог настроек закрыт без изменений")
            return

        new_settings = dialog.get_settings()
        saved = self.settings_service.update(new_settings)

        if not saved:
            QMessageBox.warning(
                self, "Настройки",
                "Не удалось записать config/settings.json.\n\n"
                "Изменения действуют до перезапуска приложения."
            )

        # Применяем сразу: клиент пересоздаётся с новыми параметрами
        self._init_gigachat_client(show_dialog=False)

        # Тема могла измениться в диалоге — применяем без перезапуска.
        # Настройки уже записаны выше, поэтому повторно их не сохраняем.
        self._apply_theme_choice(
            new_settings.get("ui_theme", theme.active_theme()), save=False
        )
        # Пользователь мог включить или выключить следование за Windows
        self._sync_system_theme_watcher()

        logger.info(f"Настройки применены: провайдер={new_settings.get('provider')}")
        self.statusBar().showMessage("Настройки применены", 5000)
        QMessageBox.information(self, "Настройки", "Настройки применены.")
