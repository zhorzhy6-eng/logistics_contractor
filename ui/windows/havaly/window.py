#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Окно типа «Хавалы» (ЭТАП 2C). Каркас, вкладки — заглушки.

Тип — заявка на перевозку авто (Excel-формат), в интерфейсе — «Хавалы».

Генерация договора и распознавание данных появятся на следующих этапах:
кнопки вкладок пока показывают сообщение «в разработке».
"""

from ui.windows.base_window import BaseContractWindow


class HavalyWindow(BaseContractWindow):
    """Окно типа «Хавалы»: шапка с селектором, сайдбар, вкладки."""

    CONTRACT_TYPE = "zayavka_excel"
    WINDOW_TITLE = "Хавалы"
    TAB_CONFIGS = [
        ("Заявка", "contract.svg"),
        ("Груз", "contract.svg"),
        ("Маршрут", "trailer.svg"),
        ("Водитель", "driver.svg"),
        ("ТС", "vehicles.svg"),
        ("Стоимость", "contract.svg"),
    ]


__all__ = ["HavalyWindow"]
