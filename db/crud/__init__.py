#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Справочники базы данных (CRUD) — по модулю на таблицу.

Модули пакета знают только db/connection.py (соединение) и db/fts.py
(поисковый индекс). Импортировать db/database.py отсюда НЕЛЬЗЯ: это фасад
над пакетом, и обратный импорт дал бы кольцо.

  addresses.py       — справочник адресов (места загрузки и выгрузки)
  counterparties.py  — справочник контрагентов (по типу договора и роли)
  drivers.py         — водители и история работы у перевозчиков (driver_carriers)
  organizations.py   — заказчики и перевозчики: общая реализация (is_carrier)
  customers.py       — заказчики — имена из db/crud/organizations.py
  carriers.py        — перевозчики — имена из db/crud/organizations.py
  vehicles.py        — ТС: машины договора и тягач/прицеп водителя
  search.py          — общий слой поиска (синхронизация и подсчёт FTS)
"""

from db.crud.addresses import (
    count_addresses,
    delete_address,
    get_addresses,
    import_addresses_from_list,
    save_address,
    update_address,
)

from db.crud.counterparties import (
    COUNTERPARTY_FIELDS,
    delete_counterparty,
    get_all_counterparties,
    load_counterparty,
    restore_counterparty,
    save_counterparty,
    search_counterparties,
    update_counterparty,
)

from db.crud.drivers import (
    ACTIVE_LINK_SQL,
    delete_driver,
    get_all_drivers,
    get_carrier_drivers,
    get_driver_carriers,
    link_driver_to_carrier,
    load_driver,
    restore_driver,
    save_driver,
    search_drivers,
    set_default_carrier,
    unlink_driver_from_carrier,
    update_driver,
)

from db.crud.organizations import (
    delete_organization,
    find_organization_id,
    get_all_organizations,
    load_organization,
    load_organization_by_id,
    restore_organization,
    save_organization,
    search_organizations,
    update_organization,
)

from db.crud.customers import (
    delete_organization,
    find_organization_id,
    get_all_organizations,
    load_organization,
    load_organization_by_id,
    restore_organization,
    save_organization,
    search_organizations,
    update_organization,
)

from db.crud.carriers import (
    delete_organization,
    find_organization_id,
    get_all_organizations,
    load_organization,
    load_organization_by_id,
    restore_organization,
    save_organization,
    search_organizations,
    update_organization,
)

from db.crud.vehicles import (
    load_driver_vehicle,
    save_driver_vehicle,
    save_vehicles,
)

from db.crud.search import (
    addresses_fts_count,
    ensure_fts_fresh,
)

__all__ = [
    "ACTIVE_LINK_SQL",
    "COUNTERPARTY_FIELDS",
    "addresses_fts_count",
    "count_addresses",
    "delete_address",
    "delete_counterparty",
    "delete_driver",
    "delete_organization",
    "ensure_fts_fresh",
    "find_organization_id",
    "get_addresses",
    "get_all_counterparties",
    "get_all_drivers",
    "get_all_organizations",
    "get_carrier_drivers",
    "get_driver_carriers",
    "import_addresses_from_list",
    "link_driver_to_carrier",
    "load_counterparty",
    "load_driver",
    "load_driver_vehicle",
    "load_organization",
    "load_organization_by_id",
    "restore_counterparty",
    "restore_driver",
    "restore_organization",
    "save_address",
    "save_counterparty",
    "save_driver",
    "save_driver_vehicle",
    "save_organization",
    "save_vehicles",
    "search_counterparties",
    "search_drivers",
    "search_organizations",
    "set_default_carrier",
    "unlink_driver_from_carrier",
    "update_address",
    "update_counterparty",
    "update_driver",
    "update_organization",
]
