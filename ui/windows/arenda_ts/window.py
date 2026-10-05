#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Окно типа «Разовая аренда» (ЭТАП 2C). Каркас, вкладки — заглушки.

Тип — договор аренды транспортного средства с экипажем.

Генерация договора и распознавание данных появятся на следующих этапах:
кнопки вкладок пока показывают сообщение «в разработке».
"""

from ui.windows.base_window import BaseContractWindow


class ArendaTsWindow(BaseContractWindow):
    """Окно типа «Разовая аренда»: шапка с селектором, сайдбар, вкладки."""

    CONTRACT_TYPE = "arenda_ts"
    WINDOW_TITLE = "Разовая аренда"
    #: Разделы окна: семь вкладок в порядке разделов бланка. Сами вкладки
    #: написаны (ui/windows/arenda_ts/tabs) — на них окно переводится шагом
    #: 3.1.D.B.3, здесь пока только разметка разделов.
    TAB_CONFIGS = [
        ("Арендатор", "customer.svg"),
        ("Арендодатель", "carrier.svg"),
        ("ТС", "vehicles.svg"),
        ("Маршрут", "trailer.svg"),
        ("Груз", "contract.svg"),
        ("Экипаж", "driver.svg"),
        ("Стоимость", "contract.svg"),
    ]


__all__ = ["ArendaTsWindow"]
