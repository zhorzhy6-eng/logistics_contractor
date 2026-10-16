# -*- coding: utf-8 -*-
"""
Слой unit-тестов: быстрые тесты без Qt и без БД (Этап 7, 16.10.2026).

Здесь лежат тесты, которые проверяют логику ядра, контрактов, агента и
утилит и не поднимают ни ``QApplication``, ни SQLite. Прогон слоя:

    pytest tests/unit/ -o addopts="" -q --basetemp=tests/_tmp/_pt_unit

Общие фикстуры (``work_dir``, ``project_root``, ``templates_dir``, …)
наследуются из ``tests/conftest.py``. Qt-фикстуры живут в ``tests/ui/``,
``isolated_db`` — в ``tests/conftest.py`` (её используют и UI-тесты:
27 файлов слоя ``tests/ui/`` работают с временной базой).
"""
