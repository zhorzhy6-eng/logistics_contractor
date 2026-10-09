#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Заказчики — таблица customers.

Своей реализации у заказчика нет: и заказчика, и перевозчика обслуживают
одни и те же функции с флагом `is_carrier` (см. db/crud/organizations.py).
Модуль даёт таблице своё имя и точку входа, чтобы по коду было видно, где
искать работу с заказчиком, и чтобы правка одной стороны не искалась по
всему пакету.
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
