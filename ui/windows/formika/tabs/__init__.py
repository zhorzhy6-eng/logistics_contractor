# -*- coding: utf-8 -*-
"""Шесть вкладок окна типа «Формика» (ЭТАП 3.1.B.2).

Раскладка полей вкладок ровно та, которую читает
ui/windows/formika/data.py::collect_formika_data:

    CustomerTab — номер и дата договора-заявки;
    CargoTab    — перевозимые машины (марка/модель + VIN, до 12 штук);
    RouteTab    — маршрут, адреса и плановые дата/время погрузки;
    DriverTab   — водитель и водительское удостоверение;
    VehicleTab  — тягач и полуприцеп;
    PriceTab    — стоимость, НДС, срок оплаты и особые условия.

Вкладки общие по устройству (см. ui/tabs/base_tab.py): QWidget + TabMixin,
панель распознавания сверху, панель действий внизу. Стороны договора —
заказчик и экспедитор — в бланке Формики фиксированы, поэтому вкладки
«Заказчик» здесь только шапка документа (номер и дата).
"""

from ui.windows.formika.tabs.cargo_tab import CargoTab
from ui.windows.formika.tabs.customer_tab import CustomerTab
from ui.windows.formika.tabs.driver_tab import DriverTab
from ui.windows.formika.tabs.price_tab import PriceTab
from ui.windows.formika.tabs.route_tab import RouteTab
from ui.windows.formika.tabs.vehicle_tab import VehicleTab

__all__ = [
    "CustomerTab",
    "CargoTab",
    "RouteTab",
    "DriverTab",
    "VehicleTab",
    "PriceTab",
]
