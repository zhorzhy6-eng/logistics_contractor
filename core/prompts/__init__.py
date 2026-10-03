# -*- coding: utf-8 -*-
"""
Промпты распознавания по типам договоров (шаг 4 инфраструктуры типов).

Заготовки: у каждого типа есть модуль с константой PROMPT. Пока у всех
типов PROMPT = None — это значит «использовать дефолтный промпт клиента»
(GigaChatClient.SYSTEM_PROMPT, core/gigachat_client.py), то есть поведение
распознавания не меняется.

core/gigachat_client.py на этом шаге не изменяется: get_prompt() — только
точка роста для будущих типов. Когда появится собственный промпт типа,
достаточно заполнить PROMPT в его модуле.

Ошибки импорта изолированы: недоступный или сломанный модуль промпта даёт
None (дефолт), а не падение распознавания.
"""

import importlib
import logging
from typing import Mapping, Optional

logger = logging.getLogger("core.prompts")

#: Ключ типа договора (ContractType) → модуль с его промптом.
#: «Хавалы» — это тип zayavka_excel (Excel-форма заявки), поэтому промпт
#: этого типа лежит в core/prompts/havaly.py.
#: expediciya («Экспедиторская заявка») в списке пользовательских типов нет,
#: поэтому отдельная заготовка под неё не заводится.
PROMPT_MODULES: Mapping[str, str] = {
    "perevozka": "core.prompts.perevozka",
    "formika": "core.prompts.formika",
    "logistiks_rus": "core.prompts.logistiks_rus",
    "arenda_ts": "core.prompts.arenda_ts",
    "zayavka_excel": "core.prompts.havaly",
}


def get_prompt(contract_type: str) -> Optional[str]:
    """
    Промпт распознавания для типа договора или None.

    None возвращается, когда:
      * тип неизвестен (нет модуля промпта);
      * модуль промпта не импортируется (ошибка логируется);
      * PROMPT не заполнен (сейчас так у всех типов) или пуст.

    None для вызывающего кода означает «используй дефолтный промпт» —
    так распознавание продолжает работать без промптов конкретных типов.
    """
    key = str(contract_type or "").strip()
    module_name = PROMPT_MODULES.get(key)
    if not module_name:
        logger.debug(f"Промпт не задан: неизвестный тип договора {key!r}")
        return None

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


__all__ = ["PROMPT_MODULES", "get_prompt"]
