#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Настройки TLS для обращения к GigaChat (Шаг 3 задания по безопасности).

Раньше проверка сертификата была отключена (verify_ssl=false), то есть
трафик с ключом авторизации можно было перехватить. Теперь проверка включена
всегда, а корневой сертификат (например, НУЦ Минцифры) подхватывается из
resources/certs/, если файл там лежит.

Порядок работы:
  1. verify_ssl=True (по умолчанию) + системный набор сертификатов;
  2. если в resources/certs/ найден файл russian_trusted_root_ca.* —
     он используется как дополнительный/основной CA bundle;
  3. путь можно задать явно: config/settings.json → "gigachat_ca_bundle".

Скачать сертификат НУЦ Минцифры: https://gu-st.ru/content/lending/
(файлы russian_trusted_root_ca.cer / russian_trusted_root_ca_pem.crt).
"""

import logging
import os
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


def certs_dir() -> str:
    """Папка проекта с сертификатами."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, CERTS_SUBDIR)


def default_ca_bundle() -> Optional[str]:
    """
    Ищет корневой сертификат в resources/certs/.

    :return: путь к сертификату или None, если файла нет
    """
    directory = certs_dir()
    for name in CA_FILENAMES:
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            logger.info(f"Найден корневой сертификат: {path}")
            return path

    logger.debug(
        f"Корневой сертификат не найден в {directory} — "
        f"используется системный набор сертификатов"
    )
    return None


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
