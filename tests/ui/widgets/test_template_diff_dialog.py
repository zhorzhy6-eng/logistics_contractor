#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты диалога подтверждения правки шаблона (ui/template_diff_dialog.py).

Диалог — последний рубеж перед изменением шаблона. Проверяется, что он:

  * показывает каждое изменение отдельной строкой с флажком;
  * НЕ даёт отметить небезопасную правку (абзац с плейсхолдером);
  * применяет только отмеченное и только безопасное;
  * при отказе/ошибке остаётся открытым и объясняет причину;
  * сам не пишет в файлы (применение — переданная функция).

Qt поднимается в режиме offscreen (tests/ui/conftest.py).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import Qt

from ui.template_diff_dialog import (
    TemplateDiffDialog,
    apply_summary,
    plan_rows,
    safety_note,
)

#: Предложение правки, как его отдаёт propose_template_fix.
PLAN = {
    "ok": True,
    "template": "templates/shablon_ooo.docx",
    "issue": "НДС 20%",
    "old": "НДС 20%",
    "new": "НДС 22%",
    "changes": [
        {
            "type": "replace", "paragraph_index": 4, "location": "body",
            "run_index": 0, "old": "НДС 20%", "new": "НДС 22%",
            "has_placeholder": False, "safe": True, "reason": "",
        },
        {
            "type": "replace", "paragraph_index": 7, "location": "body",
            "run_index": -1, "old": "НДС 20%", "new": "НДС 22%",
            "has_placeholder": True, "safe": False,
            "reason": "в абзаце есть плейсхолдер или тег docxtpl",
        },
    ],
    "warnings": ["одно место придётся поправить руками"],
}


@pytest.fixture
def qt_app():
    """QApplication на модуль тестов (offscreen)."""
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# ─────────────────────────────────────────────────────────────
# 1. Подготовка строк (без Qt)
# ─────────────────────────────────────────────────────────────

def test_plan_rows_marks_safe_checked():
    """Безопасная правка отмечена сразу, небезопасная — нет."""
    rows = plan_rows(PLAN)
    assert [row["checked"] for row in rows] == [True, False]
    assert [row["safe"] for row in rows] == [True, False]
    assert rows[0]["place"] == "абзац 5"


def test_plan_rows_xlsx_place():
    """Для ячейки в «месте» пишется лист и адрес."""
    plan = {"changes": [{"sheet": "Заявка", "cell": "B7", "safe": True,
                          "old": "a", "new": "b"}]}
    rows = plan_rows(plan)
    assert rows[0]["place"] == "Заявка!B7"


def test_safety_note_explains_manual():
    """У небезопасной строки причина видна в таблице."""
    rows = plan_rows(PLAN)
    assert safety_note(rows[0]) == "можно применить"
    assert "руками" in safety_note(rows[1])
    assert "плейсхолдер" in safety_note(rows[1])


def test_plan_rows_empty():
    """Пустое предложение — пустая таблица."""
    assert plan_rows({"changes": []}) == []
    assert plan_rows({}) == []


def test_apply_summary_success_and_rollback():
    """Итог применения: успех с копией или откат с причинами."""
    success = apply_summary({"ok": True, "applied": 2,
                             "backup": "шаблон.docx.bak",
                             "message": "Применено правок: 2."})
    assert "Применено правок: 2" in success
    assert "Резервная копия" in success

    failure = apply_summary({"ok": False, "rolled_back": True,
                             "problems": ["пропал плейсхолдер"],
                             "backup": "шаблон.docx.bak"})
    assert "НЕ применена" in failure
    assert "восстановлен" in failure
    assert "пропал плейсхолдер" in failure


# ─────────────────────────────────────────────────────────────
# 2. Диалог
# ─────────────────────────────────────────────────────────────

def test_dialog_shows_all_changes(qt_app):
    """Каждое изменение — отдельная строка с местом, «было» и «стало»."""
    dialog = TemplateDiffDialog(PLAN)
    try:
        assert dialog.table.rowCount() == 2
        assert dialog.table.item(0, 1).text() == "абзац 5"
        assert dialog.table.item(0, 2).text() == "НДС 20%"
        assert dialog.table.item(0, 3).text() == "НДС 22%"
        assert "руками" in dialog.table.item(1, 4).text()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_dialog_summary_names_counts_and_warnings(qt_app):
    """В шапке — сколько мест, сколько применимо и предупреждения."""
    dialog = TemplateDiffDialog(PLAN)
    try:
        text = dialog.summary_label.text()
        assert "Найдено мест: 2" in text
        assert "можно применить: 1" in text
        assert "руками" in text
    finally:
        dialog.close()
        dialog.deleteLater()


def test_dialog_selected_indexes(qt_app):
    """Отмеченное собирается по флажкам и не включает небезопасное."""
    dialog = TemplateDiffDialog(PLAN)
    try:
        assert dialog.selected_indexes() == [0]
        dialog.table.item(0, 0).setCheckState(Qt.Unchecked)
        assert dialog.selected_indexes() == []
    finally:
        dialog.close()
        dialog.deleteLater()


def test_dialog_apply_button_needs_callback(qt_app):
    """Без функции применения кнопка выключена: диалог не пишет сам."""
    dialog = TemplateDiffDialog(PLAN)
    try:
        assert dialog.apply_button.isEnabled() is False
    finally:
        dialog.close()
        dialog.deleteLater()


def test_dialog_apply_calls_callback_with_selection(qt_app):
    """Кнопка зовёт переданную функцию с номерами отмеченных правок."""
    calls = []

    def fake_apply(plan, selected):
        calls.append((plan, list(selected)))
        return {"ok": True, "applied": 1, "backup": "x.bak",
                "message": "Применено правок: 1."}

    dialog = TemplateDiffDialog(PLAN, apply=fake_apply)
    try:
        assert dialog.apply_button.isEnabled() is True
        result = dialog.apply_selected()
        assert calls and calls[0][1] == [0]
        assert result["ok"] is True
        assert "Применено правок: 1" in dialog.result_label.text()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_dialog_stays_open_when_apply_fails(qt_app):
    """Откат — диалог открыт, текст об откате на экране."""
    def failing_apply(plan, selected):
        return {"ok": False, "rolled_back": True,
                "problems": ["пропал плейсхолдер"], "backup": "x.bak"}

    dialog = TemplateDiffDialog(PLAN, apply=failing_apply)
    try:
        dialog.apply_selected()
        assert dialog.result["ok"] is False
        assert "восстановлен" in dialog.result_label.text()
        assert "пропал плейсхолдер" in dialog.result_label.text()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_dialog_apply_without_selection_does_nothing(qt_app):
    """Ничего не отмечено — функция не зовётся, сказано об этом."""
    calls = []

    def fake_apply(plan, selected):
        calls.append(selected)
        return {"ok": True}

    dialog = TemplateDiffDialog(PLAN, apply=fake_apply)
    try:
        dialog.table.item(0, 0).setCheckState(Qt.Unchecked)
        assert dialog.apply_selected() is None
        assert calls == []
        assert "Не отмечено" in dialog.result_label.text()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_dialog_without_safe_changes_has_disabled_button(qt_app):
    """Все правки небезопасны — применять нечего, кнопка выключена."""
    plan = {"changes": [{"paragraph_index": 1, "safe": False,
                          "old": "a", "new": "b", "reason": "плейсхолдер"}]}

    def fake_apply(plan_, selected_):
        return {"ok": True}

    dialog = TemplateDiffDialog(plan, apply=fake_apply)
    try:
        assert dialog.apply_button.isEnabled() is False
        assert dialog.selected_indexes() == []
    finally:
        dialog.close()
        dialog.deleteLater()

