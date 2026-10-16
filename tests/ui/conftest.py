# -*- coding: utf-8 -*-
"""
Слой UI-тестов: Qt в режиме offscreen (Этап 7, 16.10.2026).

Здесь лежат тесты, которые создают объекты Qt (окна, вкладки, диалоги,
виджеты). Прогон слоя:

    pytest tests/ui/ -o addopts="" -q --basetemp=tests/_tmp/_pt_ui

Если один процесс на весь слой падает с 0xC0000409 (свойство PyQt5 на
Python 3.14 в этом окружении), слой прогоняется по подпапкам:
``tests/ui/main_window/``, ``windows/``, ``tabs/``, ``agent_widget/``,
``widgets/``.

``QT_QPA_PLATFORM=offscreen`` выставляется ЗДЕСЬ и ДО первого импорта
PyQt5: без платформы offscreen Qt пытается поднять настоящее окно.
В самих тестах та же строка остаётся — тест обязан быть
самодостаточным при запуске по одному файлу.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    """
    Единственный ``QApplication`` на модуль тестов.

    Второй экземпляр Qt создать нельзя — процесс падает; поэтому берём
    существующий, если он уже поднят другим тестом или фикстурой.
    """
    app = QApplication.instance() or QApplication([])
    yield app
