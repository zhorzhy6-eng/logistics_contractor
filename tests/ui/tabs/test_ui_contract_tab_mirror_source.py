#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Зеркало: живая вкладка Экспедиторства отдаёт даты договора и погрузки РАЗДЕЛЬНО.

Фикс 05.11.2026. Дефект: в целевом окне вместо плановой даты погрузки из
Экспедиторства оставалось заводское значение поля — сегодняшнее число, то есть
дата самого договора. Причина не во вкладке: она аккуратно отдаёт три разные
даты. Причина была в картах зеркала (приватная часть), которые не переносили
плановую дату вовсе.

Этот файл проверяет СТЫК, на котором ошибка была бы видна сразу:

    fill_data() реальной вкладки (так её заполняют оператор и распознавание)
        → get_data(): date, loading_plan_date, loading_date — три разных ключа
        → collect_source(): движок зеркала видит их же
        → loading_plan_date(): правило ядра берёт ПЛАН, а не дату договора

Дата погрузки берётся «сегодня + 10 дней», дата договора — «сегодня»:
тест не зависит от календаря и проверяет ровно то, на что жаловался оператор.
Qt — в offscreen-режиме (правило слоя tests/ui/).
"""

import os
from datetime import date, timedelta
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.mirror import collect_source, loading_plan_date  # noqa: E402
from ui.tabs.contract_tab import ContractTab  # noqa: E402

#: Даты рейса: договор — сегодня, погрузка — через 10 дней (заведомо другая).
TODAY = date.today()
CONTRACT_DATE = TODAY.isoformat()
LOADING_PLAN_DATE = (TODAY + timedelta(days=10)).isoformat()
LOADING_POINT_DATE = (TODAY + timedelta(days=9)).isoformat()
UNLOADING_DATE = (TODAY + timedelta(days=14)).isoformat()


def _filled_tab() -> ContractTab:
    """Вкладка «Условия договора», заполненная как у оператора."""
    tab = ContractTab()
    tab.fill_data({
        "date": CONTRACT_DATE,
        "loading_plan_date": LOADING_PLAN_DATE,
        "loadings": [
            {
                "name": "ООО «Салон Север»",
                "address": "183052, г. Мурманск, пр. Кольский, д. 53",
                "date": LOADING_POINT_DATE,
                "time_window": "09:00-18:00",
            },
        ],
        "unloadings": [
            {
                "name": "ООО «Салон Кавказ»",
                "address": "г. Пятигорск, Бештаугорское шоссе 17",
                "date": UNLOADING_DATE,
                "time_window": "",
            },
        ],
    })
    return tab


def _source_window(tab: ContractTab) -> SimpleNamespace:
    """
    Окно-источник с одной заполненной вкладкой.

    Остальные вкладки зеркалу не нужны, а ``collect_source`` читает их через
    ``getattr(..., None)`` — отсутствующие разделы останутся пустыми.
    """
    return SimpleNamespace(contract_tab=tab)


def test_contract_tab_keeps_three_dates_apart(qt_app):
    """Дата договора, плановая дата погрузки и дата точки — разные ключи."""
    data = _filled_tab().get_data()

    assert data["date"] == CONTRACT_DATE
    assert data["loading_plan_date"] == LOADING_PLAN_DATE
    assert data["loading_date"] == LOADING_POINT_DATE

    assert data["date"] != data["loading_plan_date"]
    assert data["loading_plan_date"] != data["loading_date"]


def test_collect_source_passes_both_dates_to_the_engine(qt_app):
    """Движок зеркала получает и дату договора, и плановую дату погрузки."""
    source = collect_source(_source_window(_filled_tab()))

    contract = source["contract"]
    assert contract["date"] == CONTRACT_DATE
    assert contract["loading_plan_date"] == LOADING_PLAN_DATE
    # Дата первой погрузки тоже на месте: она запасной источник правила.
    assert source["loadings"][0]["date"] == LOADING_POINT_DATE

    assert loading_plan_date(source) == LOADING_PLAN_DATE
    assert loading_plan_date(source) != source["contract"]["date"]


def test_engine_reads_plan_date_even_when_contract_is_today(qt_app):
    """
    Договор от сегодня — погрузка через 10 дней: правило берёт погрузку.

    Ровно этот случай оператор и описал: «вместо плановой даты подставляется
    сегодняшняя» — то есть дата договора.
    """
    source = collect_source(_source_window(_filled_tab()))

    assert loading_plan_date(source) == LOADING_PLAN_DATE
    assert loading_plan_date(source) != TODAY.isoformat()
