#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Настройки TLS для обращения к GigaChat (Шаг 3 задания по безопасности).

Раньше проверка сертификата была отключена (verify_ssl=false), то есть
трафик с ключом авторизации можно было перехватить. Теперь проверка включена
всегда, а корневой сертификат (например, НУЦ Минцифры) подхватывается из
resources/certs/, если файл там лежит.

Порядок поиска сертификата (см. default_ca_bundle):
  1. явный путь из config/settings.json → "gigachat_ca_bundle";
  2. рядом со сборкой/точкой входа: sys._MEIPASS, затем sys.executable;
  3. рядом с кодом: resources/certs/ проекта;
  4. переменная окружения LOGISTICS_CA_BUNDLE (тесты, CI).

Если ни один файл не найден — возвращается None и requests работает с
системным набором сертификатов Windows: отсутствие НУЦ Минцифры в проекте
не означает, что он не установлен в хранилище.

Скачать сертификат НУЦ Минцифры: https://gu-st.ru/content/lending/
(файлы russian_trusted_root_ca.cer / russian_trusted_root_ca_pem.crt).
"""

import logging
import os
import sys
from typing import Optional

logger = logging.getLogger("core.tls_config")

# Возможные имена файла корневого сертификата в resources/certs/
CA_FILENAMES = (
    "russian_trusted_root_ca.cer",
    "russian_trusted_root_ca.pem",
    "russian_trusted_root_ca.crt",
    "russian_trusted_sub_ca.cer",
)

CERTS_SUBDIR = os.path.join("resources", "certs")

#: Переменная окружения с явным путём к CA-файлу (тесты, CI, портативные сборки)
CA_ENV_VAR = "LOGISTICS_CA_BUNDLE"


def _find_project_root() -> str:
    """
    Корень проекта — по маркеру (`main.py` или `pytest.ini`).

    Не считаем от `__file__`: модуль переезжает (`core/tls_config.py` →
    `infrastructure/api/tls.py`), и жёсткая глубина ломается молча — после
    переезда два `dirname` давали `infrastructure/`, а сертификат искался
    в `infrastructure/resources/certs`. Маркер надёжнее: он лежит в корне
    и не зависит от глубины модуля.

    Порядок:
      1. поднимаемся вверх от `__file__`, ищем `main.py` или `pytest.ini`;
      2. fallback — старое поведение (два `dirname` от `__file__`): он нужен
         для собранного exe (PyInstaller), где маркера в `_MEIPASS` нет.
         Там `certs_dir()` всё равно не главный источник — каталог сборки
         проверяется раньше, в `_search_dirs()`.
    """
    here = os.path.abspath(__file__)
    for _ in range(6):  # не больше 6 уровней вверх
        here = os.path.dirname(here)
        if (os.path.exists(os.path.join(here, "main.py"))
                or os.path.exists(os.path.join(here, "pytest.ini"))):
            return here
    # Fallback — старое поведение (два dirname).
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def certs_dir() -> str:
    """Папка проекта с сертификатами."""
    return os.path.join(_find_project_root(), CERTS_SUBDIR)


def _settings_ca_bundle() -> str:
    """
    Путь к корневому сертификату из настроек приложения.

    Импорт SettingsService локальный: core.tls_config подключают и утилиты,
    которым сервис настроек не нужен. Настройки — необязательный источник,
    поэтому сбой их чтения не должен ломать поиск сертификата.
    """
    try:
        from core.settings_service import get_settings_service
        return (get_settings_service().get_str("gigachat_ca_bundle", "") or "").strip()
    except Exception as exc:
        logger.warning(
            f"Не удалось прочитать gigachat_ca_bundle из настроек "
            f"({type(exc).__name__}) — ищем сертификат в других местах"
        )
        return ""


def _search_dirs():
    """
    Папки поиска сертификата в порядке приоритета.

    Сборка и точка входа идут раньше кода: при запуске из другой копии
    проекта или из PyInstaller-сборки актуальный сертификат лежит именно
    рядом с запущенным приложением, а не рядом с импортированным модулем.

    Пути сборки берутся из общего модуля `core/app_paths.py`: там же
    решается, где ресурсы (`_MEIPASS`) и где сама программа. Оба места
    проверяются — сертификат кладут и внутрь сборки, и рядом с .exe.
    """
    from core import app_paths

    candidates = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(("meipass", os.path.join(meipass, CERTS_SUBDIR)))
        candidates.append(("meipass", meipass))
    executable_dir = os.path.dirname(os.path.abspath(sys.executable or ""))
    if executable_dir:
        candidates.append(("exe", os.path.join(executable_dir, CERTS_SUBDIR)))
        candidates.append(("exe", executable_dir))
    candidates.append(("resources", str(app_paths.resource_path(CERTS_SUBDIR))))
    candidates.append(("program", str(app_paths.writable_path(CERTS_SUBDIR))))
    candidates.append(("project", certs_dir()))

    seen, result = set(), []
    for source, directory in candidates:
        if directory and directory not in seen:
            seen.add(directory)
            result.append((source, directory))
    return result


def _find_ca_file(directory: str) -> Optional[str]:
    """Первый существующий CA-файл из CA_FILENAMES в указанной папке."""
    for name in CA_FILENAMES:
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            return path
    return None


def default_ca_bundle() -> Optional[str]:
    """
    Ищет корневой сертификат (НУЦ Минцифры) в порядке приоритета:

      1. явный путь из настроек (config/settings.json → gigachat_ca_bundle);
      2. рядом со сборкой/точкой входа (sys._MEIPASS, затем sys.executable);
      3. рядом с кодом (resources/certs/ текущего проекта);
      4. переменная окружения LOGISTICS_CA_BUNDLE.

    В лог пишется и путь, и источник: при следующем сбое причина видна
    с первой строки. Отсутствие сертификата не является ошибкой.

    :return: путь к сертификату или None, если файла нет
    """
    configured = _settings_ca_bundle()
    if configured:
        if os.path.isfile(configured):
            logger.info(f"CA-сертификат: {configured} (источник: settings)")
            return configured
        logger.warning(
            f"Указанный в настройках CA-сертификат не найден: {configured} — "
            f"ищем сертификат рядом с приложением и в проекте"
        )

    for source, directory in _search_dirs():
        found = _find_ca_file(directory)
        if found:
            logger.info(f"CA-сертификат: {found} (источник: {source})")
            return found

    env_path = os.environ.get(CA_ENV_VAR, "").strip()
    if env_path:
        if os.path.isfile(env_path):
            logger.info(f"CA-сертификат: {env_path} (источник: env {CA_ENV_VAR})")
            return env_path
        logger.warning(f"{CA_ENV_VAR} указывает на несуществующий файл: {env_path}")

    logger.info(
        f"CA-сертификат не найден (settings, сборка, {certs_dir()}, "
        f"{CA_ENV_VAR}) — используется системный набор сертификатов"
    )
    return None


def resolve_ca_bundle(explicit: Optional[str] = None) -> Optional[str]:
    """
    Проверяет явный путь к CA-файлу и при отсутствии файла ищет рабочий.

    Явный, но несуществующий путь — след переезда проекта или копии со
    старой настройкой. requests падает на таком пути с OSError ещё до
    соединения, поэтому в verify его отдавать нельзя.

    :param explicit: путь из настроек/аргумента клиента
    :return: существующий путь к сертификату или None
    """
    explicit = str(explicit or "").strip()
    if explicit:
        if os.path.isfile(explicit):
            logger.info(f"CA-сертификат: {explicit} (источник: настройки)")
            return explicit
        logger.warning(
            f"CA-файл из настроек не найден: {explicit} — "
            f"ищем рабочий сертификат автоматически"
        )
    return default_ca_bundle()


def ssl_error_hint() -> str:
    """Понятная подсказка при ошибке проверки сертификата."""
    return (
        "Не удалось проверить TLS-сертификат GigaChat.\n"
        "Варианты решения:\n"
        "  1) скачайте корневой сертификат НУЦ Минцифры "
        "(https://gu-st.ru/content/lending/) и положите его в "
        f"{certs_dir()} с именем russian_trusted_root_ca.cer;\n"
        "  2) либо укажите путь к своему CA-файлу в config/settings.json → "
        "\"gigachat_ca_bundle\";\n"
        "  3) либо установите сертификат в хранилище Windows "
        "(«Доверенные корневые центры сертификации»).\n"
        "Отключать проверку сертификата не рекомендуется: вместе с трафиком "
        "может утечь ключ авторизации."
    )


def describe_tls(verify_ssl: bool, ca_bundle: Optional[str]) -> str:
    """Краткое описание режима TLS (для логов и интерфейса)."""
    if not verify_ssl:
        return "TLS: проверка сертификата ОТКЛЮЧЕНА (небезопасно)"
    if ca_bundle:
        return f"TLS: проверка включена, CA-файл: {ca_bundle}"
    return "TLS: проверка включена, системный набор сертификатов"
