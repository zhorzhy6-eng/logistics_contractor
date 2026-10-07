#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Описание колонок таблиц «Перевозимые авто» (ШАГ FIX-6, часть B3).

Одно место, где сказано, какие колонки есть у таблиц машин и какие из них
обязательные. Таблиц пять, и состав у них разный:

  * Экспедиторство (`ui/tabs/vehicles_tab.py`) — восемь колонок: пять
    рабочих и три необязательные (госномер, год, цвет). Необязательные
    скрыты по умолчанию: в бланк договора перевозки они не печатаются;
  * Логистикс Рус, Разовая аренда, Формика, Хавалы — свои небольшие
    наборы (марка, VIN, тип ТС и т.п.), у каждого типа обязательные
    колонки свои.

Обязательные колонки скрыть нельзя: без VIN и марки строка машины теряет
смысл. Остальные оператор включает и выключает сам — правый клик по шапке
таблицы («Какие колонки показывать»), выбор сохраняется в QSettings
(см. `ui/widgets/column_settings.py`).

Здесь только ОПИСАНИЕ. Номера колонок в таблицах заданы отдельно и не
меняются при настройке состава: колонки прячутся, а не удаляются —
иначе сдвинулись бы делегаты, выпадающие списки и сохранённая раскладка.
"""

from typing import Tuple

from ui.widgets.column_settings import ColumnSpec

# ─────────────────────────────────────────────────────────────
# Ключи полей машины
# ─────────────────────────────────────────────────────────────

FIELD_VIN = "vin"
FIELD_BRAND = "brand_model"
FIELD_TYPE = "vehicle_type"
FIELD_LOADING = "loading_index"
FIELD_UNLOADING = "unloading_index"
#: Госномер: в Экспедиторстве — колонка таблицы, в других типах — номер
#: тягача/прицепа на вкладке «ТС».
FIELD_PLATE = "plate_number"
FIELD_YEAR = "year"
FIELD_COLOR = "color"

# ─────────────────────────────────────────────────────────────
# Экспедиторство: полный набор
# ─────────────────────────────────────────────────────────────

#: Колонки «Перевозимых авто» Экспедиторства.
#:
#: Первые пять — рабочие: VIN и марка обязательны, тип ТС и точки маршрута
#: нужны для таблиц погрузок/выгрузок договора. Три последние — карточка
#: машины: по умолчанию СКРЫТЫ, потому что в бланк перевозки они не
#: попадают, и оператор их не заполнял (ШАГ FIX-6, часть B1/B2).
VEHICLE_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec(FIELD_VIN, "VIN-код", required=True),
    ColumnSpec(FIELD_BRAND, "Марка/Модель", required=True),
    ColumnSpec(FIELD_TYPE, "Тип ТС", required=True),
    ColumnSpec(FIELD_LOADING, "Погрузка", required=True),
    ColumnSpec(FIELD_UNLOADING, "Выгрузка", required=True),
    ColumnSpec(FIELD_PLATE, "Госномер", default_visible=False),
    ColumnSpec(FIELD_YEAR, "Год выпуска", default_visible=False),
    ColumnSpec(FIELD_COLOR, "Цвет", default_visible=False),
)

#: Колонки Хавалов (вкладка «Груз»): схема .xlsx-заявки.
HAVALY_VEHICLE_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("number", "№", required=True),
    ColumnSpec("vin", "VIN", required=True),
    ColumnSpec("brand", "Марка", required=True),
    ColumnSpec("model", "Модель", required=True),
    ColumnSpec("dealer", "Дилер", required=True),
    ColumnSpec("dealer_code", "Код дилера", required=True),
)

__all__ = [
    "FIELD_VIN",
    "FIELD_BRAND",
    "FIELD_TYPE",
    "FIELD_LOADING",
    "FIELD_UNLOADING",
    "FIELD_PLATE",
    "FIELD_YEAR",
    "FIELD_COLOR",
    "VEHICLE_COLUMNS",
    "HAVALY_VEHICLE_COLUMNS",
]
