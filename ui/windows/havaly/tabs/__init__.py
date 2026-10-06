# -*- coding: utf-8 -*-
"""Шесть вкладок окна типа «Хавалы» (ЭТАП 3.1.E.B.2).

Раскладка полей вкладок ровно та, которую читает
ui/windows/havaly/data.py::collect_havaly_data:

    CustomerTab — Заявка: номер лота и дата заявки; стороны («Заказчик» и
                  «Перевозчик») фиксированы и показаны справочно;
    CargoTab    — Груз: перевозимые машины (VIN, марка, модель, дилер, код
                  дилера — пять полей схемы, до 10 строк) и номер лота;
    RouteTab    — Маршрут: города и пункты погрузки и разгрузки, плановая
                  дата и время погрузки;
    DriverTab   — Водитель: ФИО тремя полями, В/У, паспорт (серия и номер
                  отдельно), гражданство, дата рождения, прописка, телефон;
    VehicleTab  — ТС: автовоз (марка, цвет кабины, номер) и прицеп (марка,
                  номер); наименование транспортной компании фиксировано;
    PriceTab    — Стоимость: ставка с НДС одна на всю заявку, ставка НДС.

Имена полей вкладок СОВПАДАЮТ с ключами схемы ответа промпта
(core/prompts/havaly.py): вкладка отдаёт ``loading_city``, а не
``loading_city_edit`` (соглашение ЭТАПА 3.1.E.B.1). Сборщик ничего не
переводит — он раскладывает эти же имена по разделам, поэтому раскладка
не может разойтись с промптом незаметно.

Вкладки общие по устройству (см. ui/tabs/base_tab.py): QWidget + TabMixin,
панель распознавания сверху, панель действий внизу. Номер лота и дата заявки
есть на двух вкладках («Заявка» и «Груз»): в бланке это шапка таблицы,
значение у них одно, и приоритет у вкладки «Заявка»
(ui/windows/havaly/data.py::_SHARED_ZAYAVKA_FIELDS).
"""

from ui.windows.havaly.tabs.cargo_tab import CargoTab
from ui.windows.havaly.tabs.customer_tab import CustomerTab
from ui.windows.havaly.tabs.driver_tab import DriverTab
from ui.windows.havaly.tabs.price_tab import PriceTab
from ui.windows.havaly.tabs.route_tab import RouteTab
from ui.windows.havaly.tabs.vehicle_tab import VehicleTab

__all__ = [
    "CustomerTab",
    "CargoTab",
    "RouteTab",
    "DriverTab",
    "VehicleTab",
    "PriceTab",
]
