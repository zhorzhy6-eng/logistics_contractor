# -*- coding: utf-8 -*-
"""
Базовый генератор DOCX-договоров и его модули-помощники.

Разбиение на модули (Этап 4, 16.10.2026):
  * generator.py   — BaseContractGenerator: оркестрация + контракт;
  * render.py      — движок рендера docxtpl (и fallback);
  * tables.py      — работа с таблицами машин и точек;
  * formatting.py  — копирование стилей, обход абзацев;
  * postprocess.py — PostprocessStep + шаги постобработки;
  * payments.py    — разбивка оплаты, суммы прописью;
  * dates.py       — форматы дат для шаблонов.

Публичный API не меняется: BaseContractGenerator, PostprocessStep,
ConvertNewlinesStep, NormalizeSpacesStep доступны отсюда.
Подклассы (PerevozkaGenerator + клиентские) работают без правок.

Старый путь `core.contracts.base_generator` продолжает работать через
алиасы в `core/__init__.py` и реэкспорт в `core/contracts/__init__.py`.
"""

from core.contracts.base.generator import BaseContractGenerator
from core.contracts.base.postprocess import (
    ConvertNewlinesStep,
    NormalizeSpacesStep,
    PostprocessStep,
)

__all__ = [
    "BaseContractGenerator",
    "PostprocessStep",
    "ConvertNewlinesStep",
    "NormalizeSpacesStep",
]
