#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Окно типа «Формика» (ЭТАП 2C). Каркас, вкладки — заглушки.

Тип — приложение к генеральному договору перевозки.

Генерация договора и распознавание данных появятся на следующих этапах:
кнопки вкладок пока показывают сообщение «в разработке».
"""

from ui.windows.base_window import BaseContractWindow


class FormikaWindow(BaseContractWindow):
    """Окно типа «Формика»: шапка с селектором, сайдбар, вкладки."""

    CONTRACT_TYPE = "formika"
    WINDOW_TITLE = "Формика"
    TAB_CONFIGS = [
        ("Заказчик", "customer.svg"),
        ("Груз", "contract.svg"),
        ("Маршрут", "trailer.svg"),
        ("Водитель", "driver.svg"),
        ("ТС", "vehicles.svg"),
        ("Стоимость", "contract.svg"),
    ]


__all__ = ["FormikaWindow"]
