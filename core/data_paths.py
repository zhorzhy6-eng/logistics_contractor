#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Пути к ДАННЫМ ПОЛЬЗОВАТЕЛЯ: база, настройки, журналы, готовые договоры.

Зачем отдельный модуль
----------------------
`core/app_paths.py` отвечает на вопрос «где лежит программа и откуда читать
ресурсы». Здесь — другой вопрос: «где лежат данные, которые нельзя терять».
Пока ответ был один и тот же (папка программы), и это работало ровно до
первого обновления: папку программы установщик при обновлении заменяет
целиком (а если программа стоит в `Program Files`, то и записать в неё
нельзя). Данные, положенные рядом с `.exe`, живут до следующей установки.

Отсюда правило, которое держит весь модуль:

* **читаем** — из сборки (`core/app_paths.resource_path`): шаблоны,
  правовая база, промпты, приватная часть;
* **пишем** — в папку данных (этот модуль): `contracts.db`,
  `config/settings.json`, `logs/`, `output/`.

Где папка данных
----------------
* **Windows, собранный `.exe`** — `%APPDATA%\\LogisticsContractor`.
  Это стандартное место для изменяемых данных Windows-приложений:
  установщик его не трогает, права на запись есть у обычного пользователя,
  и оно переживает и обновление, и переустановку.
* **Linux/macOS** — `~/.local/share/LogisticsContractor` (тот же стандарт
  XDG; нужно для разработки и тестов, в сборке не используется).
* **Разработка (не `.exe`)** — `<корень проекта>/tests/_tmp/_appdata`.
  Намеренно НЕ `%APPDATA%`: прогон тестов и запуск из исходников не должны
  писать в рабочие данные оператора. Папка `tests/_tmp` — временная и уже
  в `.gitignore`.
* **Переменная окружения `LOGISTICS_DATA_ROOT`** — главнее всего
  перечисленного (портативный режим и тесты). Тот же приём, что
  `LOGISTICS_SETTINGS_PATH` в настройках: путь можно переопределить, не
  меняя код.

Как считается путь
------------------
Только функциями и в момент вызова: констант-путей в модуле НЕТ. Иначе
тест, подменивший `sys.frozen` или `APPDATA`, получил бы значение,
посчитанное при импорте (та же ловушка, что в `core/app_paths.py`).

Переезд старых данных
---------------------
`migrate_old_data()` копирует базу и настройки из папки программы в папку
данных, если там их ещё нет. Копирует — и никогда не удаляет: старая папка
остаётся как была, пользователь сам решит, что с ней делать. Повторный
вызов ничего не делает (второй копии не появляется).

Как проверяется
---------------
`tests/unit/core/test_data_paths.py` — подменяет `APPDATA`, `sys.platform`
и признак сборки, проверяет каждый путь, создание папок и миграцию
(в том числе что существующие данные не перезаписываются).
"""

import logging
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Union

from core.app_paths import app_root, is_frozen

logger = logging.getLogger("core.data_paths")

#: Имя папки данных. Оно же — имя подпапки `%APPDATA%`.
APP_NAME = "LogisticsContractor"

#: Переменная окружения для портативного режима и тестов.
ENV_DATA_ROOT = "LOGISTICS_DATA_ROOT"

#: Подпапки папки данных, которые должны существовать до первого запуска.
SUBDIRS = ("config", "logs", "output")

#: Имя файла базы. Вынесено отдельно: на него смотрят миграция и самопроверка.
DB_FILENAME = "contracts.db"

#: Настройки лежат в подпапке `config`, рядом — образец для первого запуска.
SETTINGS_RELATIVE = Path("config") / "settings.json"


def data_root() -> Path:
    """
    Стандартная папка данных приложения.

    * Windows — `%APPDATA%\\LogisticsContractor`;
    * Linux/macOS — `~/.local/share/LogisticsContractor`.

    Если `APPDATA` не задана (редкий, но возможный случай: запуск из
    службы или из урезанного профиля), берётся `~/AppData/Roaming` —
    путь тот же, что дала бы переменная.

    :return: путь к папке данных (может ещё не существовать).
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", "").strip()
        if not base:
            logger.warning(
                "Переменная APPDATA пуста — берём ~/AppData/Roaming"
            )
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def data_dev_root() -> Path:
    """
    Папка данных для разработки (запуск из исходников, не `.exe`).

    `tests/_tmp/_appdata` внутри проекта: тесты и запуск из исходников не
    должны трогать рабочие данные оператора. Папка временная, в git не
    попадает (см. `.gitignore`).
    """
    return Path(__file__).resolve().parent.parent / "tests" / "_tmp" / "_appdata"


def resolved_data_root() -> Path:
    """
    Папка данных, которая действует СЕЙЧАС.

    Порядок, от сильного к слабому:

    1. `LOGISTICS_DATA_ROOT` (портативный режим, тесты);
    2. `%APPDATA%\\LogisticsContractor` — если запущен собранный `.exe`;
    3. `tests/_tmp/_appdata` — если запущены исходники.
    """
    env_value = os.environ.get(ENV_DATA_ROOT, "").strip()
    if env_value:
        return Path(env_value).expanduser()
    if is_frozen():
        return data_root()
    return data_dev_root()


def db_path() -> Path:
    """Файл базы `contracts.db` в папке данных."""
    return resolved_data_root() / DB_FILENAME


def config_path() -> Path:
    """Файл настроек `config/settings.json` в папке данных."""
    return resolved_data_root() / SETTINGS_RELATIVE


def logs_dir() -> Path:
    """Папка журналов `logs/` в папке данных."""
    return resolved_data_root() / "logs"


def output_dir() -> Path:
    """Папка готовых договоров `output/` в папке данных."""
    # Имя совпадает с BaseContractGenerator.DEFAULT_OUTPUT_DIRNAME: папка
    # одна, и считается она здесь, чтобы не было второго источника правды.
    return resolved_data_root() / "output"


def settings_example_path() -> Path:
    """
    Образец настроек из сборки: `config/settings.example.json`.

    Читаемое — из ресурсов (см. `core/app_paths.py`), поэтому в `.exe`
    файл берётся из `_internal`, а не рядом с `.exe`. Файл может
    отсутствовать (например, его забыли положить в сборку) — вызывающий
    обязан это проверить.
    """
    from core.app_paths import resource_path

    return resource_path(str(SETTINGS_RELATIVE.with_name("settings.example.json")))


def ensure_data_root() -> Path:
    """
    Создаёт папку данных и обязательные подпапки.

    Вызывается до первого обращения к базе, настройкам и журналам: в
    свежей установке подпапок ещё нет, а `sqlite3.connect` создаёт файл, но
    не папку.

    :return: корень данных (существующая папка).
    """
    root = resolved_data_root()
    root.mkdir(parents=True, exist_ok=True)
    for name in SUBDIRS:
        (root / name).mkdir(exist_ok=True)
    return root


def ensure_settings_file(example_path: Optional[Union[str, Path]] = None) -> Path:
    """
    Кладёт `settings.json` в папку данных, если его там ещё нет.

    Настройки при первом запуске удобнее начинать с образца, чем с пустого
    файла: в образце есть все разделы, и диалог настроек сразу показывает
    разумные значения. Если образца нет — файл не создаётся: сервис
    настроек и без файла работает на умолчаниях и создаст его при
    сохранении.

    :param example_path: образец; по умолчанию `config/settings.example.json`
        из сборки.
    :return: путь к `settings.json` (существующему или только что
        созданному; может не существовать, если нет образца).
    """
    target = config_path()

    if example_path is None:
        example = settings_example_path()
    else:
        example = Path(example_path)

    if target.exists():
        return target

    if not example.is_file():
        logger.warning(
            "Образец настроек не найден (%s) — settings.json будет создан "
            "при первом сохранении", example,
        )
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy(example, target)
    except OSError as error:
        # Прав на файл нет — это не повод не запускаться: умолчания
        # работают и без файла.
        logger.warning("Не удалось создать settings.json (%s): %s", target, error)
        return target

    logger.info("Создан settings.json из образца: %s", target)
    return target


def app_root_for_migration() -> Path:
    """
    Папка, в которой лежали данные СТАРЫХ версий программы.

    До перехода на `%APPDATA%` база, настройки, журналы и договоры лежали
    рядом с программой:

    * `.exe` — папка с `LogisticsContractor.exe`;
    * исходники — корень проекта.

    Ровно это и отдаёт `core/app_paths.app_root()` (см. его докстроку:
    «папка ПРОГРАММЫ — та, рядом с которой можно писать»). Своей копии
    этой логики здесь нет намеренно: два ответа на вопрос «где программа»
    рано или поздно разошлись бы.
    """
    return app_root()


def _copy_database(source: Path, target: Path) -> None:
    """
    Копирует файл базы, не теряя данные из журнала WAL.

    `shutil.copy2` копирует только `contracts.db`, а в режиме WAL часть
    данных (и последние транзакции) лежит в `contracts.db-wal`. Если
    скопировать один файл, часть записей пропадёт. Поэтому база читается
    через SQLite (он сам учитывает WAL) и переносится штатным API backup.

    Если прочитать базу не удалось (файл повреждён, занят монопольно),
    остаётся побайтовое копирование вместе с журналом `-wal`: лучше
    перенести файл с журналом, чем ничего. Недописанная база после
    неудачного backup удаляется, чтобы не осталось битого получателя.
    """
    try:
        with sqlite3.connect(str(source), timeout=10) as src:
            with sqlite3.connect(str(target)) as dst:
                src.backup(dst)
        return
    except (sqlite3.Error, OSError) as error:
        logger.warning(
            "Не удалось скопировать базу через SQLite (%s) — копируем файлы: %s",
            source, error,
        )

    try:
        target.unlink(missing_ok=True)
    except OSError:  # pragma: no cover — удалить нечего или нет прав
        pass

    shutil.copy2(source, target)
    for suffix in ("-wal", "-shm"):
        extra = source.with_name(source.name + suffix)
        if extra.is_file():
            shutil.copy2(extra, target.with_name(target.name + suffix))


def migrate_old_data(old_root: Union[str, Path]) -> List[str]:
    """
    Копирует данные старых версий из папки программы в папку данных.

    Что переносится:

    * `contracts.db` — договоры, водители, справочники;
    * `config/settings.json` — настройки оператора.

    Что НЕ переносится:

    * `logs/` — это не данные, а журнал прошлых запусков;
    * `output/` — готовые документы остаются на месте: копирование
      договоров могло бы занять гигабайты, а старые файлы никуда не
      деваются;
    * `backup/` и `db/legal_metadata.db` — восстановимое и производное.

    Главное правило: **не удалять и не перезаписывать**. Если в папке данных
    файл уже есть, он остаётся как есть — миграция одного базы не должна
    затирать данные новой версии. Старые файлы остаются на месте: их
    судьбу решает пользователь.

    :param old_root: папка программы старой версии.
    :return: список путей, которые были скопированы (пустой — переносить
        нечего или всё уже на месте).
    """
    copied: List[str] = []

    try:
        source_root = Path(old_root)
    except (TypeError, ValueError):
        logger.warning("Некорректный путь старых данных: %r", old_root)
        return copied

    if not source_root.exists():
        return copied

    target_root = resolved_data_root()
    try:
        if source_root.resolve() == target_root.resolve():
            # Данные уже в целевом месте (например, LOGISTICS_DATA_ROOT
            # указывает на папку программы) — копировать нечего.
            return copied
    except OSError:  # pragma: no cover — путь мог исчезнуть между вызовами
        pass

    ensure_data_root()

    old_db = source_root / DB_FILENAME
    new_db = target_root / DB_FILENAME
    if old_db.is_file() and not new_db.exists():
        try:
            _copy_database(old_db, new_db)
            copied.append(str(old_db))
            logger.info("Перенесена база: %s → %s", old_db, new_db)
        except OSError as error:
            logger.warning("Не удалось перенести базу %s: %s", old_db, error)

    old_settings = source_root / SETTINGS_RELATIVE
    new_settings = target_root / SETTINGS_RELATIVE
    if old_settings.is_file() and not new_settings.exists():
        try:
            new_settings.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old_settings, new_settings)
            copied.append(str(old_settings))
            logger.info("Перенесены настройки: %s → %s", old_settings, new_settings)
        except OSError as error:
            logger.warning(
                "Не удалось перенести настройки %s: %s", old_settings, error
            )

    return copied


def describe() -> str:
    """
    Строка для журнала и диагностики: где данные и откуда они взялись.

    Пишется один раз при старте: при разборе «куда пропали договоры»
    первая строка журнала сразу показывает и папку, и режим.
    """
    env_value = os.environ.get(ENV_DATA_ROOT, "").strip()
    if env_value:
        source = f"переменная {ENV_DATA_ROOT}"
    elif is_frozen():
        source = "папка данных приложения"
    else:
        source = "разработка"

    return f"данные: {resolved_data_root()} (источник: {source})"


__all__ = [
    "APP_NAME",
    "DB_FILENAME",
    "ENV_DATA_ROOT",
    "SETTINGS_RELATIVE",
    "SUBDIRS",
    "app_root_for_migration",
    "config_path",
    "data_dev_root",
    "data_root",
    "db_path",
    "describe",
    "ensure_data_root",
    "ensure_settings_file",
    "logs_dir",
    "migrate_old_data",
    "output_dir",
    "resolved_data_root",
    "settings_example_path",
]
