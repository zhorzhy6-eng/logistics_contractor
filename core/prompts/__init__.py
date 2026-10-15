# -*- coding: utf-8 -*-
"""
Промпты распознавания по типам договоров (шаг 4 инфраструктуры типов).

У каждого типа есть модуль с константой PROMPT. У «Экспедиторства»
``PROMPT = None`` — это значит «использовать дефолтный промпт клиента»
(GigaChatClient.SYSTEM_PROMPT, core/gigachat_client.py), то есть поведение
распознавания не меняется.

Тексты промптов клиентских типов лежат в приватной части проекта
(``core/prompts/<ключ>.py``) и находятся по имени модуля: список ключей
здесь не хранится, потому что ключи клиентских типов — это и есть
коммерческая информация. Поиск модуля — стандартный механизм импорта
(``find_spec``), поэтому приватная папка просто должна быть подключена
вызовом ``core.private_path.attach_private_root()``.

Ошибки импорта изолированы: недоступный или сломанный модуль промпта даёт
None (дефолт), а не падение распознавания.
"""

import importlib
import importlib.util
import logging
from typing import Optional

logger = logging.getLogger("core.prompts")

#: Префикс модулей промптов: ключ типа → core.prompts.<ключ>.
MODULE_PREFIX = "core.prompts."

#: Ключ типа → имя модуля промпта. Наполняется по мере обнаружения:
#: публичные модули видны сразу, клиентские появляются при подключённой
#: приватной части. Ключа нет в словаре — промпта для типа нет.
PROMPT_MODULES = {}

#: Ключи, у которых промпта не бывает по устройству программы:
#: служебный тип без пользовательского окна.
_NO_PROMPT_KEYS = frozenset({"expediciya"})

#: Искали ли модули промптов в этом запуске. Поиск ленивый: на момент
#: импорта этого модуля реестр типов ещё пуст.
_discovered = False


def _discover_modules(force: bool = False) -> None:
    """
    Дополняет PROMPT_MODULES модулями, которые реально импортируются.

    Ключ типа совпадает с именем модуля (``perevozka`` →
    ``core.prompts.perevozka``), поэтому список не нужно поддерживать
    руками: новый тип приносит свой модуль промпта, и он находится сам.
    """
    global _discovered
    if _discovered and not force:
        return
    _discovered = True

    names = set(PROMPT_MODULES.values())
    for key in _candidate_keys():
        module_name = f"{MODULE_PREFIX}{key}"
        if module_name in names:
            continue
        try:
            available = importlib.util.find_spec(module_name) is not None
        except (ImportError, ValueError):  # нет родительского пакета
            available = False
        if available:
            PROMPT_MODULES[key] = module_name


def _candidate_keys():
    """
    Ключи типов, для которых имеет смысл искать модуль промпта.

    Источник — реестр типов договоров: он знает и публичные, и клиентские
    ключи (клиентские появляются в нём, когда подключена приватная часть).
    Реестр недоступен — ищем только публичный ключ «Экспедиторства».
    """
    keys = ["perevozka"]
    try:
        from core.contracts.registry import ContractTypeRegistry

        keys.extend(ContractTypeRegistry.known_types())
    except Exception:  # noqa: BLE001 — список промптов не должен ломать старт
        logger.debug("Реестр типов недоступен — ищем только публичные промпты")

    seen = set()
    for key in keys:
        text = str(key).strip()
        if not text or text in _NO_PROMPT_KEYS or text in seen:
            continue
        seen.add(text)
        yield text


def refresh_prompts() -> dict:
    """
    Пересобирает список модулей промптов (после загрузки типов) и отдаёт его.

    Нужна там, где типы регистрируются позже первого обращения к промптам
    (приложение: реестр наполняется в main.py).
    """
    _discover_modules(force=True)
    return dict(PROMPT_MODULES)


def get_prompt(contract_type: str) -> Optional[str]:
    """
    Промпт распознавания для типа договора или None.

    None возвращается, когда:
      * тип неизвестен (нет модуля промпта);
      * модуль промпта не импортируется (ошибка логируется);
      * PROMPT не заполнен (у «Экспедиторства» так и есть) или пуст.

    None для вызывающего кода означает «используй дефолтный промпт» —
    так распознавание продолжает работать без промптов конкретных типов.
    """
    _discover_modules()

    key = str(contract_type or "").strip()
    module_name = PROMPT_MODULES.get(key)
    if not module_name:
        # Тип мог появиться после обнаружения: проверим ещё раз точечно.
        module_name = f"{MODULE_PREFIX}{key}" if key else ""
        if not module_name:
            logger.debug("Промпт не задан: неизвестный тип договора %r", key)
            return None
        try:
            available = importlib.util.find_spec(module_name) is not None
        except (ImportError, ValueError):
            available = False
        if not available:
            logger.debug("Промпт не задан: неизвестный тип договора %r", key)
            return None
        PROMPT_MODULES[key] = module_name

    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 — заготовка не должна ломать распознавание
        logger.warning(
            f"Промпт типа {key!r} недоступен ({module_name}): "
            f"{type(exc).__name__}: {exc}"
        )
        return None

    prompt = getattr(module, "PROMPT", None)
    if prompt is None:
        return None

    text = str(prompt).strip()
    return text or None


__all__ = ["MODULE_PREFIX", "PROMPT_MODULES", "get_prompt", "refresh_prompts"]
