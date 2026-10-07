# -*- coding: utf-8 -*-
"""Восемь вкладок окна типа «Разовая аренда» (ЭТАП 3.1.D.B.2, FIX-3).

Раскладка полей вкладок ровно та, которую читает
ui/windows/arenda_ts/data.py::collect_arenda_ts_data:

    LesseeTab  — Арендатор (наша сторона): вид Арендатора (ООО / ИП с НДС /
                 ИП без НДС), реквизиты, банк, руководитель и основание
                 полномочий. У ИП строка «КПП» убирается, основание
                 переключается на свидетельство о регистрации;
    LessorTab  — Арендодатель (вторая сторона): те же реквизиты без вида и
                 без КПП — бланки рассчитаны на Арендодателя-ООО;
    VehicleTab — ТС: номер и дата договора, срок аренды (п. 2.5), тягач
                 (марка, госномер, тип ТС) и прицеп (марка, госномер);
    RouteTab   — Маршрут: направление и таблицы точек погрузки (до 10,
                 с датой и временем подачи ТС) и выгрузки (до 10);
    CargoTab   — Груз: перевозимые автомобили таблицы п. 3.1 (до 12) с VIN и
                 точками в строке; счётчик заполненных машин — cargo_count;
    CrewTab    — Экипаж: девять полей водителя (п. 3.5). Паспорт и
                 водительское удостоверение — одной строкой: разбирает их
                 сборщик данных, а не вкладка;
    PriceTab   — Стоимость: сумма без НДС, ставка НДС, расчётные НДС и итог,
                 сумма прописью и особые условия;
    ActTab     — Акт (Приложение № 1): десять полей приёма-передачи и
                 возврата ТС (шаг FIX-3) — до него эти ячейки бланка
                 заполнялись только вручную в Word.

Вкладки общие по устройству (см. ui/tabs/base_tab.py): QWidget + TabMixin,
панель распознавания сверху, панель действий внизу. Реквизиты сторон взяты с
вкладки организации (ui/tabs/carrier_tab.py), экипаж — с вкладки водителя
(ui/tabs/driver_tab.py), таблицы точек — с вкладки договора
(ui/tabs/contract_tab.py).
"""

from ui.windows.arenda_ts.tabs.act_tab import ActTab
from ui.windows.arenda_ts.tabs.cargo_tab import CargoTab
from ui.windows.arenda_ts.tabs.crew_tab import CrewTab
from ui.windows.arenda_ts.tabs.lessee_tab import LesseeTab
from ui.windows.arenda_ts.tabs.lessor_tab import LessorTab
from ui.windows.arenda_ts.tabs.price_tab import PriceTab
from ui.windows.arenda_ts.tabs.route_tab import RouteTab
from ui.windows.arenda_ts.tabs.vehicle_tab import VehicleTab

__all__ = [
    "LesseeTab",
    "LessorTab",
    "VehicleTab",
    "RouteTab",
    "CargoTab",
    "CrewTab",
    "PriceTab",
    "ActTab",
]
