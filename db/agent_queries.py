#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Запросы для ИИ-агента «Максимилиан»: отдельная точка входа в базу.

Почему отдельный модуль, а не имена в фасаде `db/database.py`: фасад —
тонкая точка входа приложения (не больше 200 строк, это проверяет
`tests/test_db_facade.py`), а поиск договоров агенту нужен поверх уже
существующего CRUD. Модуль не дублирует SQL: он импортирует функции из
`db/crud/contracts.py`, чтобы у поиска была ровно одна реализация.

Использование (core/agent/tools.py):

    from db import agent_queries
    agent_queries.search_contracts("23092026-74")

Путь к базе берётся соединением (`db/connection.py`) — как и во всём
пакете db, поэтому подмена `db.database.DB_PATH` в тестах действует и тут
(см. docstring `db/connection.py`).
"""

from db.crud.contracts import (
    CONTRACT_SEARCH_FIELDS,
    get_expiring_contracts,
    search_contracts,
)

__all__ = [
    "CONTRACT_SEARCH_FIELDS",
    "get_expiring_contracts",
    "search_contracts",
]
