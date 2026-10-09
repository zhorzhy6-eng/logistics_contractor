#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Перевозчики — таблица carriers.

Своей реализации у перевозчика нет: и заказчика, и перевозчика обслуживают
одни и те же функции с флагом `is_carrier` (см. db/crud/organizations.py).
Модуль даёт таблице своё имя и точку входа: у перевозчика, кроме общих
полей, есть лицензия, а на него ссылаются водители (drivers.default_carrier_id)
и ТС (vehicles.carrier_id).
"""

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

__all__ = [
    "delete_organization",
    "find_organization_id",
    "get_all_organizations",
    "load_organization",
    "load_organization_by_id",
    "restore_organization",
    "save_organization",
    "search_organizations",
    "update_organization",
]
