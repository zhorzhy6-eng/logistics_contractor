#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
`register_module_alias` — общая функция регистрации алиасов модулей
(Этап 5, 16.10.2026).

Зачем отдельный файл. Одна и та же грабля всплывала в Этапах 3, 4 и 5:
алиасу переехавшего модуля нужны ДВЕ регистрации —

  * запись в ``sys.modules`` (её читает ``from core.X import ...``);
  * атрибут пакета-родителя (его читает ``import core.X`` и обращение
    ``core.X`` после загрузки).

Одного ``sys.modules`` МАЛО, и это не видно в общем прогоне pytest:
там пакет уже поднят `conftest.py`, а в свежем процессе ПЕРВЫЙ импорт
старого пути падает с ``AttributeError``.

Проверки идут на ВРЕМЕННОМ пакете, а не на живом проекте: так функция
проверяется там, где её поведение задано, и тест не зависит от того,
какие модули уже успел поднять `conftest.py`.
"""

import sys
import types

import pytest

from core.private_path import register_module_alias

#: Имя временного пакета: с префиксом, которого нет в проекте, чтобы
#: случайно не затереть живой модуль.
TMP_PACKAGE = "_alias_probe_pkg"
TMP_MODULE = TMP_PACKAGE + ".old_name"


@pytest.fixture
def temp_package():
    """
    Временный пакет с подпапкой и «переехавшим» модулем.

    Устроен ровно как настоящие пакеты проекта: обычный пакет
    (``__path__`` есть), внутри — новый модуль, на который и указывает
    алиас старого имени. После теста всё убирается из ``sys.modules``.
    """
    parent = types.ModuleType(TMP_PACKAGE)
    parent.__path__ = []  # обычный пакет, а не пространство имён
    moved = types.ModuleType(TMP_PACKAGE + ".new_name")
    moved.MARKER = "новый модуль"

    sys.modules[TMP_PACKAGE] = parent
    sys.modules[TMP_PACKAGE + ".new_name"] = moved
    try:
        yield parent, moved
    finally:
        for name in (TMP_PACKAGE, TMP_PACKAGE + ".new_name", TMP_MODULE):
            sys.modules.pop(name, None)


def test_alias_lands_in_sys_modules(temp_package):
    """Первая регистрация: старое имя модуля появляется в `sys.modules`."""
    _, moved = temp_package

    register_module_alias(TMP_MODULE, moved)

    assert sys.modules[TMP_MODULE] is moved


def test_alias_lands_as_package_attribute(temp_package):
    """
    Вторая регистрация: старое имя появляется АТРИБУТОМ пакета.

    Без неё `import _alias_probe_pkg.old_name` падает с AttributeError:
    инструкция `import a.b` после загрузки читает последнее имя как
    атрибут родителя.
    """
    parent, moved = temp_package

    register_module_alias(TMP_MODULE, moved)

    assert getattr(parent, "old_name", None) is moved


def test_object_is_the_same_not_a_copy(temp_package):
    """Копий и обёрток нет: объект модуля ОДИН."""
    parent, moved = temp_package

    register_module_alias(TMP_MODULE, moved)

    assert sys.modules[TMP_MODULE] is getattr(parent, "old_name")
    assert sys.modules[TMP_MODULE] is moved
    assert sys.modules[TMP_MODULE].MARKER == "новый модуль"


def test_import_of_the_old_name_works(temp_package):
    """`import <пакет>.<старое_имя>` действительно проходит."""
    _, moved = temp_package
    register_module_alias(TMP_MODULE, moved)

    import importlib

    assert importlib.import_module(TMP_MODULE) is moved


def test_missing_parent_does_not_raise(temp_package):
    """
    Родителя ещё нет в `sys.modules` — функция не падает.

    Так бывает при самом первом импорте: старый путь регистрируется
    раньше, чем импортирован пакет-родитель. Атрибут в этом случае
    выставит тот, кто родителя импортирует (в проекте — `core.agent`).
    """
    _, moved = temp_package
    sys.modules.pop(TMP_PACKAGE, None)

    register_module_alias(TMP_MODULE, moved)  # не должно быть исключения

    assert sys.modules[TMP_MODULE] is moved


def test_top_level_name_without_dots(temp_package):
    """Имя без точек (верхний уровень) тоже регистрируется — без падения."""
    moved = types.ModuleType("_alias_probe_top")
    try:
        register_module_alias("_alias_probe_top", moved)
        assert sys.modules["_alias_probe_top"] is moved
    finally:
        sys.modules.pop("_alias_probe_top", None)


def test_register_module_alias_is_public():
    """Функция объявлена публичной: она нужна всем алиасам проекта."""
    import core.private_path as private_path

    assert "register_module_alias" in private_path.__all__


def test_function_is_reused_by_the_project():
    """
    Живая проверка: алиасы агента (Этап 5) зарегистрированы этой функцией.

    До подэтапа 1.G (переезд `core/agent/` → `services/agent/`) алиасов
    агента ещё нет — тогда проверка ПРОПУСКАЕТСЯ, а не падает: файл
    написан в Части 0, где переезд ещё не сделан. После 1.G она
    включается сама.
    """
    import core  # noqa: F401 — поднимает блок алиасов
    import core.agent as agent

    if "core.agent.llm" not in sys.modules:
        pytest.skip("алиасы агента появятся в подэтапе 1.G")

    assert sys.modules["core.agent.llm"] is not None
    assert getattr(agent, "llm", None) is sys.modules["core.agent.llm"]
