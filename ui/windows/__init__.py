#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Пакет окон типов договоров (ЭТАП 2B).

WindowManager держит окна по ключу типа договора: окна создаются лениво
и живут до выхода из приложения, поэтому данные в формах не теряются при
переключении типа.
"""

from ui.windows.manager import WindowManager

__all__ = ["WindowManager"]
