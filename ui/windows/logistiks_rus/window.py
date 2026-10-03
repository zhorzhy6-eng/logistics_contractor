#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Окно типа «Логистикс Рус» (ЭТАП 2C). Каркас, вкладки — заглушки.

Тип — приложение к генеральному договору экспедиции.

Генерация договора и распознавание данных появятся на следующих этапах:
кнопки вкладок пока показывают сообщение «в разработке».
"""

from ui.windows.base_window import BaseContractWindow


class LogistiksRusWindow(BaseContractWindow):
    """Окно типа «Логистикс Рус»: шапка с селектором, сайдбар, вкладки."""

    CONTRACT_TYPE = "logistiks_rus"
    WINDOW_TITLE = "Логистикс Рус"
    TAB_CONFIGS = [
        ("Заказчик", "customer.svg"),
        ("Груз", "contract.svg"),
        ("Маршрут", "trailer.svg"),
        ("Водитель", "driver.svg"),
        ("ТС", "vehicles.svg"),
        ("Стоимость", "contract.svg"),
    ]


__all__ = ["LogistiksRusWindow"]
