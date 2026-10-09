#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Справочники базы данных (CRUD) — по модулю на таблицу.

Модули пакета знают только db/connection.py (соединение) и db/fts.py
(поисковый индекс). Импортировать db/database.py отсюда НЕЛЬЗЯ: это фасад
над пакетом, и обратный импорт дал бы кольцо.

  addresses.py       — справочник адресов (места загрузки/выгрузки)
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

__all__ = [
    "count_addresses",
    "delete_address",
    "get_addresses",
    "import_addresses_from_list",
    "save_address",
    "update_address",
]
