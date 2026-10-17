#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты диалога юридической проверки (ui/legal_check_dialog.py).

Проверяется показ результата проверки: строки таблицы, итог, норма внизу
и текст в буфер. Логика самой проверки живёт в legal_check.py и проверена
отдельными тестами — здесь только интерфейс.

Qt поднимается в режиме offscreen (tests/ui/conftest.py).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from ui.legal_check_dialog import (
    SEVERITY_LABELS,
    LegalCheckDialog,
    detail_text,
    summary_with_skipped,
    violation_rows,
)

#: Результат проверки, как его отдаёт check_contract_legal.
RESULT = {
    "ok": True,
    "contract_type": "perevozka",
    "violations": [
        {
            "rule": "vat_rate",
            "title": "Ставка НДС",
            "severity": "error",
            "message": "Значение 20%, а по НК РФ ст. 164 п. 3 — 22%.",
            "source": "НК РФ ст. 164 п. 3",
            "field": "contract_tab.vat_rate",
            "current": "20%",
            "required": 22,
            "quote": "налоговой ставке 22 процента",
        },
        {
            "rule": "route_required",
            "title": "Маршрут перевозки",
            "severity": "warning",
            "message": "Поле «contract_tab.route» не заполнено.",
            "source": "Правила перевозок грузов (ПП РФ № 2200), приложение № 6, п. 6",
            "field": "contract_tab.route",
            "current": "",
            "required": True,
            "quote": "6. Маршрут и место подачи транспортного средства",
        },
    ],
    "checked": 4,
    "skipped": [{"rule": "claim_period", "reason": "норма справочная"}],
    "summary": "Найдено нарушений: 2 (ошибок 1, замечаний 1). Проверено правил: 4.",
}


# ─────────────────────────────────────────────────────────────
# 1. Подготовка строк (без Qt)
# ─────────────────────────────────────────────────────────────

def test_violation_rows_map_severity_to_russian():
    """Важность в таблице — по-русски (Ошибка/Замечание), а не «error»."""
    rows = violation_rows(RESULT)
    assert [row[0] for row in rows] == [SEVERITY_LABELS["error"],
                                        SEVERITY_LABELS["warning"]]
    assert rows[0][1] == "Ставка НДС"
    assert rows[0][2].startswith("НК РФ")


def test_violation_rows_without_violations_is_empty():
    """Нарушений нет — таблица пустая, а не «одна пустая строка»."""
    assert violation_rows({"violations": []}) == []
    assert violation_rows({}) == []


def test_detail_text_contains_norm_quote():
    """Внизу диалога — текст нормы с цитатой из правовой базы."""
    text = detail_text(RESULT, 0)
    assert "Ставка НДС" in text
    assert "22 процента" in text
    assert "НК РФ ст. 164 п. 3" in text


def test_detail_text_outside_table_shows_summary():
    """Строка вне таблицы — итог проверки (пустой экран был бы поломкой)."""
    assert detail_text(RESULT, 99) == RESULT["summary"]


def test_summary_mentions_skipped_rules():
    """В итоге видно, что часть правил не проверялась (и почему в общем)."""
    text = summary_with_skipped(RESULT)
    assert "Без проверки: 1" in text


# ─────────────────────────────────────────────────────────────
# 2. Диалог
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def dialog(qt_app):
    """Диалог с результатом проверки (закрывается после теста)."""
    widget = LegalCheckDialog(RESULT)
    yield widget
    widget.close()
    widget.deleteLater()


def test_dialog_fills_table(dialog):
    """Таблица заполнена нарушениями: строки, колонки, источники."""
    assert dialog.table.rowCount() == 2
    assert dialog.table.columnCount() == 4
    assert dialog.table.item(0, 1).text() == "Ставка НДС"
    assert "НК РФ" in dialog.table.item(0, 2).text()


def test_dialog_selects_first_violation(dialog):
    """При открытии выбрано первое нарушение — норма уже на экране."""
    assert dialog.selected_row() == 0
    assert "Ставка НДС" in dialog.details.toPlainText()


def test_dialog_second_row_shows_its_norm(dialog):
    """Выбор другой строки меняет текст нормы внизу."""
    dialog.table.selectRow(1)
    dialog.show_selected_norm()
    text = dialog.details.toPlainText()
    assert "Маршрут перевозки" in text
    assert "2200" in text


def test_dialog_clipboard_text_has_sources(dialog):
    """Текст для буфера — итог и нарушения с источниками."""
    text = dialog.text_for_clipboard()
    assert "Найдено нарушений: 2" in text
    assert "Источник: НК РФ ст. 164 п. 3" in text


def test_dialog_without_violations(qt_app):
    """Пустой результат: диалог открывается, таблица пустая, итог виден."""
    widget = LegalCheckDialog({
        "violations": [], "checked": 5, "skipped": [],
        "summary": "Нарушений нет. Проверено правил: 5.",
    })
    try:
        assert widget.table.rowCount() == 0
        assert "Нарушений нет" in widget.summary_label.text()
        assert widget.details.toPlainText() == ""
    finally:
        widget.close()
        widget.deleteLater()
