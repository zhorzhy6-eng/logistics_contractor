# -*- coding: utf-8 -*-
"""Шесть вкладок окна типа «Логистикс Рус» (ЭТАП 3.1.C.B.2).

Раскладка полей вкладок ровно та, которую читает
ui/windows/logistiks_rus/data.py::collect_logistiks_rus_data:

    CustomerTab — номер и дата заявки, наименование заказчика;
    CargoTab    — перевозимые машины (марка, модель + VIN, до 12 штук);
    RouteTab    — направление, грузоотправители и грузополучатели (до 10
                  блоков каждый) и план погрузки/выгрузки;
    DriverTab   — водитель: только ФИО (остального в бланке нет);
    VehicleTab  — автовоз: марка и госномер тягача и прицепа;
    PriceTab    — стоимость: тип экспедитора (ООО / ИП), суммы и НДС.

Вкладки общие по устройству (см. ui/tabs/base_tab.py): QWidget + TabMixin,
панель распознавания сверху, панель действий внизу. Стороны заявки —
заказчик и экспедитор — в бланке Логистикс Рус фиксированы, поэтому на
вкладке «Заказчик» только шапка документа и наименование заказчика.
"""

from ui.windows.logistiks_rus.tabs.cargo_tab import CargoTab
from ui.windows.logistiks_rus.tabs.customer_tab import CustomerTab
from ui.windows.logistiks_rus.tabs.driver_tab import DriverTab
from ui.windows.logistiks_rus.tabs.price_tab import PriceTab
from ui.windows.logistiks_rus.tabs.route_tab import RouteTab
from ui.windows.logistiks_rus.tabs.vehicle_tab import VehicleTab

__all__ = [
    "CustomerTab",
    "CargoTab",
    "RouteTab",
    "DriverTab",
    "VehicleTab",
    "PriceTab",
]
