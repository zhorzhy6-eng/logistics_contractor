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

# ─────────────────────────────────────────────────────────────
# Настройки ИИ-агента «Максимилиан» (раздел "agent")
# ─────────────────────────────────────────────────────────────
# Раздел лежит внутри settings.json вложенным объектом, а не плоскими
# ключами agent_*: так видно, что это настройки одной подсистемы, и
# существующие ключи не пересекаются с ними по имени.

#: Имя помощника. ЕДИНСТВЕННЫЙ источник: имя не настраивается и не может
#: быть изменено — ни через диалог настроек, ни через файл settings.json.
#: Раньше имя лежало в разделе "agent" и правилось оператором; теперь это
#: константа (см. normalize_agent_settings).
AGENT_NAME = "Максимилиан"

#: Значения по умолчанию для раздела "agent" (задание, часть E.1).
DEFAULT_AGENT_SETTINGS: Dict[str, Any] = {
    "enabled": True,
    #: Имя помощника. ОНО НЕ НАСТРАИВАЕТСЯ: Максимилиан — всегда
    #: Максимилиан. Ключ остался ради совместимости со старыми файлами
    #: настроек и всегда приводится к этому значению.
    "name": AGENT_NAME,
    "avatar": "cat_scientist",
    "mode": "hybrid",
    "show_floating": False,
    "show_panel_on_start": False,
    "hotkey": "Ctrl+M",
    "max_history": 20,
    #: Сколько вызовов инструментов разрешено на один вопрос. 20 — потолок
    #: AGENT_LIMITS и значение рабочего файла: на сложных запросах («внеси
    #: данные по организации») трёх шагов не хватало. От зацикливания и
    #: долгого ожидания защищают REPEAT_LIMIT и таймаут в core/agent/agent.py.
    "max_tool_steps": 20,
    #: Подсказки без вопроса (задание, часть 4). Выключены по умолчанию:
    #: помощник не должен встревать, пока оператор сам этого не попросил.
    "proactive": False,
    #: Пауза между подсказками в секундах («не больше одной в 5 минут»).
    "proactive_interval": 300,
    #: Режим «Обучение» (задание: система обучения): подсказки на каждом
    #: шаге заполнения. Выключен по умолчанию — включается кнопкой в панели
    #: или предложением при первом запуске.
    "tutorial_mode": False,
    #: Показано ли предложение обучения при первом запуске. False — при
    #: первом запуске предложить включить обучение и поставить True.
    "tutorial_seen": False,
}

#: Режимы показа агента: только иконка, только панель, и то и другое.
AGENT_MODES = ("icon", "panel", "hybrid")

#: Границы числовых настроек агента — защита от значения «1000000» в файле.
AGENT_LIMITS = {
    "max_history": (1, 200),
    "max_tool_steps": (1, 20),
    "proactive_interval": (30, 3600),
}

#: Поля-числа с дробным значением (остальные — целые).
AGENT_FLOAT_KEYS = ("proactive_interval",)


def normalize_agent_settings(values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Приводит раздел "agent" к полному и разумному виду.

    Неизвестные ключи сохраняются (как и в остальных настройках), но
    известные проверяются: режим — из списка, числа — в границах,
    строки — без пробелов по краям. Ошибка в файле не должна ронять
    программу: подставляется значение по умолчанию.
    """
    result = dict(DEFAULT_AGENT_SETTINGS)
    if isinstance(values, dict):
        result.update(values)

    for key in ("enabled", "show_floating", "show_panel_on_start", "proactive",
                "tutorial_mode", "tutorial_seen"):
        value = result.get(key)
        if not isinstance(value, bool):
            result[key] = str(value).strip().lower() in ("1", "true", "yes", "да", "on")

    # Имя помощника НЕ берётся из настроек: Максимилиан — всегда
    # Максимилиан. Значение в файле (старое или правленое руками)
    # игнорируется, чтобы имя нельзя было поменять в обход интерфейса.
    if str(result.get("name") or "").strip() != AGENT_NAME:
        logger.info(
            "agent.name=%r игнорируется: имя помощника зафиксировано (%s)",
            result.get("name"), AGENT_NAME,
        )
    result["name"] = AGENT_NAME

    for key in ("avatar", "hotkey"):
        result[key] = str(result.get(key) or "").strip() or DEFAULT_AGENT_SETTINGS[key]

    mode = str(result.get("mode") or "").strip().lower()
    if mode not in AGENT_MODES:
        logger.warning(
            f"agent.mode={result.get('mode')!r} неизвестен — берём "
            f"{DEFAULT_AGENT_SETTINGS['mode']!r}"
        )
        mode = DEFAULT_AGENT_SETTINGS["mode"]
    result["mode"] = mode

    for key, (low, high) in AGENT_LIMITS.items():
        as_float = key in AGENT_FLOAT_KEYS
        try:
            number = float(result.get(key)) if as_float else int(result.get(key))
        except (TypeError, ValueError):
            number = DEFAULT_AGENT_SETTINGS[key]
        if not low <= number <= high:
            logger.warning(
                f"agent.{key}={result.get(key)!r} вне границ {low}..{high} — "
                f"берём {DEFAULT_AGENT_SETTINGS[key]}"
            )
            number = DEFAULT_AGENT_SETTINGS[key]
        result[key] = number

    return result


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
        "document_cloud_enabled": False,
        "document_soffice": "",
        "provider": "gigachat",

        # GigaChat (ключа здесь НЕТ: он хранится в системном хранилище,
        # см. core/secrets_store.py и set_key.py)
        "gigachat_model": "GigaChat-3-Pro",
        "gigachat_scope": "GIGACHAT_API_PERS",
        "gigachat_timeout": 150,
        # Проверка TLS включена (Шаг 3 задания по безопасности)
        "gigachat_verify_ssl": True,
        # Путь к корневому сертификату; пусто — ищем в resources/certs/
        "gigachat_ca_bundle": "",

        # DaData (ключа здесь тоже нет: он живёт в системном хранилище,
        # см. core/secrets_store.py и set_dadata_key.py). Настройка пока
        # не используется клиентом — задел на будущее (п. 2.3 задания).
        "dadata_timeout": 10,

        # Ollama (резервный провайдер)
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "qwen2.5:7b",
        "ollama_timeout": 150,

        # ИИ-агент «Максимилиан» (раздел agent, задание часть E.1).
        # Хранится ВЛОЖЕННЫМ объектом — см. normalize_agent_settings.
        "agent": dict(DEFAULT_AGENT_SETTINGS),
    }

    # Поля-секреты: их нельзя ни читать из файла, ни записывать в него.
    # Ключ GigaChat живёт только в системном хранилище (Шаг 2 задания).
    SECRET_KEYS = ("gigachat_credentials", "gigachat_api_key", "api_key")
    # Любые унаследованные ключи вида *_api_key / *_credentials вычищаются
    # из файла, даже если их имя больше не упоминается в коде.
    SECRET_SUFFIXES = ("_api_key", "_credentials")

    @classmethod
    def is_secret_key(cls, key: Any) -> bool:
        """Имя настройки похоже на секрет (точное совпадение или суффикс)."""
        name = str(key or "").strip().casefold()
        return name in cls.SECRET_KEYS or name.endswith(cls.SECRET_SUFFIXES)

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
        # Retire obsolete local OCR configuration without touching other user settings.
        for obsolete in ("document_tesseract", "document_ocr_languages"):
            merged.pop(obsolete, None)

        # Раздел агента доводится до полного вида: в файле он мог быть
        # записан частично (или старым форматом) — панель должна получить
        # все ключи, а не падать на отсутствующем.
        merged["agent"] = normalize_agent_settings(merged.get("agent"))

        # Защита от унаследованных файлов, где ключ лежал открытым текстом:
        # такой секрет не используется и немедленно вычищается из файла.
        leaked = [
            key for key in merged
            if self.is_secret_key(key)
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

    # ---------------------------------------------------------
    # Настройки ИИ-агента «Максимилиан» (раздел agent)
    # ---------------------------------------------------------
    def agent_settings(self) -> Dict[str, Any]:
        """
        Полный раздел "agent" (отсутствующие ключи — из значений по умолчанию).

        Возвращается копия: правка словаря снаружи не меняет настройки.
        """
        return normalize_agent_settings(self._data.get("agent"))

    def agent_setting(self, key: str, default: Any = None) -> Any:
        """Одно значение раздела "agent" по имени ключа."""
        settings = self.agent_settings()
        if default is not None:
            return settings.get(key, default)
        return settings.get(key, DEFAULT_AGENT_SETTINGS.get(key, default))

    def agent_enabled(self) -> bool:
        """
        Включён ли Максимилиан.

        Отдельный метод, потому что от этого значения зависит многое:
        иконка, панель, горячая клавиша, запросы к GigaChat и загрузка
        истории (задание, часть E.4).
        """
        return bool(self.agent_setting("enabled", True))

    def update_agent(self, values: Dict[str, Any], save: bool = True) -> bool:
        """
        Меняет значения раздела "agent" (остальные настройки не трогаются).

        Ключи, похожие на секрет, отбрасываются: раздел агента — обычные
        настройки, и ключу GigaChat там не место (он живёт в системном
        хранилище, см. core/secrets_store.py).

        :return: True, если файл успешно записан (или save=False)
        """
        if not isinstance(values, dict):
            logger.error(
                "update_agent: ожидался dict, получено %s", type(values).__name__
            )
            return False

        safe_values = {}
        for key, value in values.items():
            if self.is_secret_key(key):
                logger.warning(
                    "agent.%s похож на секрет — в настройки не записывается", key
                )
                continue
            safe_values[key] = value

        merged = self.agent_settings()
        merged.update(safe_values)
        merged = normalize_agent_settings(merged)

        self._data["agent"] = merged
        if not save:
            return True
        return self.save()

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
            if self.is_secret_key(key):
                logger.warning(
                    f"Настройка {key!r} похожа на секрет — в файл не записывается. "
                    f"Используйте set_key.py (системное хранилище)."
                )
                continue
            if key in {"document_tesseract", "document_ocr_languages"}:
                continue
            # Раздел агента приводится к полному виду: частичный словарь
            # (например, только {"mode": "icon"}) не должен оставлять
            # остальные ключи без значений.
            if key == "agent":
                value = normalize_agent_settings(value)
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
