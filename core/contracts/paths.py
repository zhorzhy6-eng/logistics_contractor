# -*- coding: utf-8 -*-
"""
Пути проекта для пакета контрактов (Шаг 1 рефакторинга).

Единая точка вычисления корня проекта: старый код в
core/contract_generator.py считал его двумя dirname() от своего __file__,
что ломается при переезде файла глубже (core/contracts/...). Здесь корень
вычисляется один раз от местоположения этого модуля.
"""

from pathlib import Path

#: Корень проекта: parents[2] = core/contracts/paths.py → core → корень.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Папка шаблонов DOCX (общая для всех типов, пока без подпапок).
TEMPLATES_DIR = PROJECT_ROOT / "templates"

#: Папка готовых договоров (как DEFAULT_OUTPUT_DIRNAME = "output").
OUTPUT_DIR = PROJECT_ROOT / "output"
