#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единый сервис настроек (Шаг 5 рефакторинга архитектуры).

До рефакторинга ui/main_window.py читал config/settings.json один раз при
импорте модуля и раскладывал значения в модульные константы
(AUTH_KEY, GIGACHAT_MODEL, ...). Изменения в файле не подхватывались:
диалог настроек не был подключён, а перезапуск оставался единственным
способом применить правки.

Теперь все, кому нужны настройки, работают через SettingsService:
  * значения читаются из файла при создании;
  * reload() перечитывает файл;
  * update()/save() пишут изменения, сохраняя неизвестные ключи;
  * get_settings_service() возвращает общий экземпляр на процесс.

Путь к файлу можно переопределить переменной окружения
LOGISTICS_SETTINGS_PATH (удобно для тестов и портативных сборок).
"""

import json
import logging
import os
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger("core.settings_service")

ENV_PATH = "LOGISTICS_SETTINGS_PATH"


def default_settings_path() -> str:
    """Путь к settings.json по умолчанию (config/ рядом с проектом)."""
    env_path = os.environ.get(ENV_PATH, "").strip()
    if env_path:
        return env_path

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, "config", "settings.json")


class SettingsService:
    """Чтение, изменение и перечитывание настроек приложения."""

    DEFAULTS: Dict[str, Any] = {
        "provider": "gigachat",

        # GigaChat (ключа здесь НЕТ: он хранится в системном хранилище,
        # см. core/secrets_store.py и set_key.py)
        "gigachat_model": "GigaChat-2",
        "gigachat_scope": "GIGACHAT_API_PERS",
        "gigachat_timeout": 150,
        # Проверка TLS включена (Шаг 3 задания по безопасности)
        "gigachat_verify_ssl": True,
        # Путь к корневому сертификату; пусто — ищем в resources/certs/
        "gigachat_ca_bundle": "",

        # Ollama (резервный провайдер)
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "qwen2.5:7b",
        "ollama_timeout": 150,
    }

    # Поля-секреты: их нельзя ни читать из файла, ни записывать в него.
    # Ключ GigaChat живёт только в системном хранилище (Шаг 2 задания).
    SECRET_KEYS = ("gigachat_credentials", "gigachat_api_key", "api_key")

    def __init__(self, path: Optional[str] = None):
        self._path = path or default_settings_path()
        self._data: Dict[str, Any] = dict(self.DEFAULTS)
        self.load()

        logger.info(
            f"SettingsService инициализирован: {self._path}, "
            f"провайдер={self._data.get('provider')}"
        )

    # ---------------------------------------------------------
    # Путь и данные
    # ---------------------------------------------------------

    @property
    def path(self) -> str:
        return self._path

    def as_dict(self) -> Dict[str, Any]:
        """Копия текущих настроек (для диалога настроек)."""
        return dict(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, self.DEFAULTS.get(key, default))

    def get_str(self, key: str, default: str = "") -> str:
        return str(self.get(key, default) or "").strip()

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            logger.warning(f"Настройка {key!r} не является числом — берём {default}")
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "да", "on")

    # ---------------------------------------------------------
    # Чтение и запись
    # ---------------------------------------------------------

    def load(self) -> Dict[str, Any]:
        """
        Читает файл настроек. Отсутствующие ключи берутся из DEFAULTS,
        неизвестные ключи сохраняются как есть.
        """
        data: Dict[str, Any] = {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.error(
                    f"{self._path}: ожидался объект JSON, получено {type(data).__name__}"
                )
                data = {}
        except FileNotFoundError:
            logger.warning(f"Файл настроек не найден: {self._path} — используются значения по умолчанию")
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка разбора {self._path}: {e} — используются значения по умолчанию")
        except OSError as e:
            logger.error(f"Не удалось прочитать {self._path}: {e}")

        merged = dict(self.DEFAULTS)
        merged.update(data)

        # Защита от унаследованных файлов, где ключ лежал открытым текстом:
        # такой секрет не используется и немедленно вычищается из файла.
        leaked = [
            key for key in self.SECRET_KEYS
            if str(merged.get(key) or "").strip()
        ]
        if leaked:
            for key in leaked:
                merged.pop(key, None)
            self._data = merged
            logger.warning(
                f"В {self._path} обнаружены секреты ({', '.join(leaked)}) — "
                f"удаляю их из файла. Ключ GigaChat хранится в системном "
                f"хранилище: запустите set_key.py"
            )
            self.save()

        self._data = merged
        return dict(self._data)

    def reload(self) -> Dict[str, Any]:
        """Перечитывает настройки с диска (изменения применяются без перезапуска)."""
        logger.info("Перечитывание настроек...")
        return self.load()

    def update(self, values: Dict[str, Any], save: bool = True) -> bool:
        """
        Обновляет настройки значениями из values и (по умолчанию) пишет файл.

        Секреты (SECRET_KEYS) игнорируются: ключ GigaChat хранится только в
        системном хранилище и в settings.json попасть не должен.

        :return: True, если файл успешно записан (или save=False)
        """
        if not isinstance(values, dict):
            logger.error(f"SettingsService.update: ожидался dict, получено {type(values).__name__}")
            return False

        safe_values = {}
        for key, value in values.items():
            if key in self.SECRET_KEYS:
                logger.warning(
                    f"Настройка {key!r} похожа на секрет — в файл не записывается. "
                    f"Используйте set_key.py (системное хранилище)."
                )
                continue
            safe_values[key] = value

        self._data.update(safe_values)

        if not save:
            return True
        return self.save()

    def save(self) -> bool:
        """
        Записывает настройки в файл.

        Ошибка записи (например, папка только для чтения) не прерывает работу
        приложения: настройки остаются применёнными в памяти на текущий сеанс.
        """
        directory = os.path.dirname(self._path)
        try:
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            logger.info(f"Настройки сохранены: {self._path}")
            return True
        except OSError as e:
            logger.error(
                f"Не удалось сохранить настройки в {self._path}: {e}. "
                f"Изменения применены только до перезапуска."
            )
            return False


# ─────────────────────────────────────────────────────────────
# Общий экземпляр на процесс
# ─────────────────────────────────────────────────────────────

_service: Optional[SettingsService] = None


def get_settings_service(path: Optional[str] = None) -> SettingsService:
    """Возвращает общий экземпляр SettingsService (создаётся при первом вызове)."""
    global _service
    if _service is None:
        _service = SettingsService(path)
    return _service


def reset_settings_service() -> None:
    """Сбрасывает общий экземпляр (используется в тестах)."""
    global _service
    _service = None
