#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог подтверждения правки шаблона: «было / стало», по одному флажку.

Зачем диалог обязателен
-----------------------
Шаблон — то, из чего печатаются договоры. Правка в нём необратима для уже
созданных документов, поэтому программа НИКОГДА не меняет шаблон сама:
оператор видит каждое изменение, отмечает нужные и только потом нажимает
«Применить выбранные».

Что показывает
---------------
  * место правки (номер абзаца или ячейка листа);
  * «было» и «стало»;
  * можно ли применить автоматически и почему нет, если нельзя;
  * итог применения: сколько применено, где резервная копия, что откатили.

Чего диалог НЕ делает
----------------------
Диалог не трогает файлы: применение выполняет переданная функция
(template_editor.apply_plan), а диалог только спрашивает и показывает
результат. Поэтому диалог проверяется тестами без записи в шаблоны.
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from services.agent.tools import template_editor
from ui import theme

logger = logging.getLogger("ui.template_diff_dialog")

#: Колонки таблицы изменений: заголовок и ширина, точки.
COLUMNS: Tuple[Tuple[str, int], ...] = (
    ("Применить", 90),
    ("Место", 90),
    ("Было", 260),
    ("Стало", 260),
    ("Безопасность", 300),
)

#: Что пишется в колонке «Применить» у небезопасной правки.
UNSAFE_MARK = "—"


# ─────────────────────────────────────────────────────────────
# Чистые помощники (проверяются без Qt)
# ─────────────────────────────────────────────────────────────

def plan_rows(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Строки диалога из предложения правки.

    Одна строка на изменение: место, было/стало, можно ли применить.
    Небезопасные правки по умолчанию НЕ отмечены и не выбираются — их
    делают руками, диалог лишь показывает, что они есть.

    :param plan: результат propose_template_fix;
    :return: список словарей строк.
    """
    rows: List[Dict[str, Any]] = []
    for index, change in enumerate((plan or {}).get("changes") or []):
        safe = bool(change.get("safe"))
        place = change.get("paragraph_index")
        if place is None and change.get("cell"):
            place = f"{change.get('sheet', '')}!{change.get('cell', '')}"
        else:
            place = f"абзац {int(place) + 1}" if place is not None else "—"
        rows.append({
            "index": index,
            "checked": safe,
            "place": str(place),
            "old": str(change.get("old") or ""),
            "new": str(change.get("new") or ""),
            "safe": safe,
            "reason": str(change.get("reason") or ""),
        })
    return rows


def safety_note(row: Dict[str, Any]) -> str:
    """
    Текст колонки «Безопасность»: можно применять или почему нельзя.

    Причина у небезопасной правки обязательна — иначе оператор не поймёт,
    почему флажок не ставится.
    """
    if row.get("safe"):
        return "можно применить"
    reason = str(row.get("reason") or "").strip()
    return f"руками: {reason}" if reason else "руками"


def apply_summary(result: Dict[str, Any]) -> str:
    """
    Итог применения для оператора: что сделано, что откатили и почему.

    Ошибка и откат описываются одинаково подробно: оператор должен знать,
    что шаблон остался прежним и где лежит резервная копия.
    """
    result = result or {}
    lines: List[str] = []
    if result.get("ok"):
        lines.append(str(result.get("message") or "Правка применена."))
    else:
        lines.append("Правка НЕ применена.")
        if result.get("rolled_back"):
            lines.append("Шаблон восстановлен из резервной копии.")
        for problem in result.get("problems") or []:
            lines.append(f"• {problem}")
    skipped = result.get("skipped") or []
    if skipped:
        lines.append(f"Пропущено правок: {len(skipped)} — файл изменился "
                     f"после предложения.")
    backup = str(result.get("backup") or "")
    if backup:
        lines.append(f"Резервная копия: {backup}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Диалог
# ─────────────────────────────────────────────────────────────

class TemplateDiffDialog(QDialog):
    """
    Диалог подтверждения правки шаблона.

    :param plan: предложение propose_template_fix (changes, warnings);
    :param apply: функция применения: (plan, selected) -> результат;
        None — применить нельзя (кнопка выключена);
    :param parent: родительское окно.
    """

    def __init__(self, plan: Dict[str, Any],
                 parent: Optional[QWidget] = None,
                 apply: Optional[Callable[..., Dict[str, Any]]] = None):
        super().__init__(parent)
        self.plan = dict(plan or {})
        self._apply = apply
        self.result: Optional[Dict[str, Any]] = None
        self.rows = plan_rows(self.plan)

        self.setWindowTitle("Правка шаблона")
        self.setObjectName("templateDiffDialog")
        self.setMinimumSize(1080, 600)
        if parent is not None:
            self.setStyleSheet(theme.qss())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Правка шаблона: проверьте изменения")
        title.setObjectName("sectionHeading")
        layout.addWidget(title)

        self.summary_label = QLabel(self._summary_text())
        self.summary_label.setWordWrap(True)
        self.summary_label.setObjectName("mutedLabel")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(len(self.rows), len(COLUMNS))
        self.table.setObjectName("templateDiffTable")
        self.table.setHorizontalHeaderLabels([name for name, _ in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self.table.horizontalHeader()
        for column, (_, width) in enumerate(COLUMNS):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            self.table.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        layout.addWidget(self.table, 3)
        self._fill_table()

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setObjectName("mutedLabel")
        self.result_label.setVisible(False)
        layout.addWidget(self.result_label)

        buttons = QHBoxLayout()
        self.cancel_button = theme.secondary_button("Отмена", parent=self)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        self.apply_button = theme.primary_button(
            "Применить выбранные",
            tooltip="Применить отмеченные правки; перед этим создаётся "
                    "резервная копия шаблона",
            parent=self,
        )
        self.apply_button.clicked.connect(self.apply_selected)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

        self.apply_button.setEnabled(bool(self._apply) and self.safe_count() > 0)

    # ── Таблица ──

    def _summary_text(self) -> str:
        """Текст шапки: что за правка и сколько мест нашлось."""
        warnings = list(self.plan.get("warnings") or [])
        title = str(self.plan.get("template") or "")
        old = str(self.plan.get("old") or "")
        new = str(self.plan.get("new") or "")
        parts = [f"Шаблон: {title}"]
        if old or new:
            parts.append(f"Замена: «{old}» → «{new}»")
        parts.append(f"Найдено мест: {len(self.rows)}, можно применить: "
                     f"{self.safe_count()}")
        parts.extend(warnings)
        return "\n".join(part for part in parts if part)

    def _fill_table(self) -> None:
        """Заполняет таблицу: флажок, место, было, стало, безопасность."""
        self.table.setRowCount(len(self.rows))
        for row, item in enumerate(self.rows):
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable
                           if item["safe"] else Qt.ItemIsEnabled)
            check.setCheckState(Qt.Checked if item["checked"] else Qt.Unchecked)
            if not item["safe"]:
                check.setText("")
            self.table.setItem(row, 0, check)

            values = [item["place"], item["old"], item["new"],
                      safety_note(item)]
            for offset, text in enumerate(values, start=1):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
                if not item["safe"]:
                    cell.setFlags(Qt.ItemIsEnabled)
                self.table.setItem(row, offset, cell)
        self.table.resizeRowsToContents()

    def safe_count(self) -> int:
        """Сколько правок можно применить автоматически."""
        return sum(1 for item in self.rows if item["safe"])

    def selected_indexes(self) -> List[int]:
        """
        Номера отмеченных правок БЕЗ небезопасных.

        Небезопасная строка не ставится в список никогда: её флажок
        выключен намеренно (плейсхолдер, текст в нескольких runs).
        """
        selected: List[int] = []
        for row, item in enumerate(self.rows):
            if not item["safe"]:
                continue
            cell = self.table.item(row, 0)
            if cell is not None and cell.checkState() == Qt.Checked:
                selected.append(item["index"])
        return selected

    # ── Применение ──

    def apply_selected(self) -> Optional[Dict[str, Any]]:
        """
        Применяет отмеченные правки через переданную функцию.

        Успех — диалог закрывается кодом «принято». Ошибка — диалог остаётся
        открытым и показывает причину: оператор может снять галочки и
        попробовать снова или выйти.

        :return: результат применения (None — применять нечем).
        """
        if self._apply is None:
            return None
        selected = self.selected_indexes()
        if not selected:
            self._show_result("Не отмечено ни одной правки.")
            return None
        try:
            self.result = self._apply(self.plan, selected)
        except Exception as error:  # noqa: BLE001 — диалог не должен падать
            logger.exception("Правка шаблона из диалога не выполнена")
            self._show_result(f"Ошибка применения: {type(error).__name__}")
            return None
        self._show_result(apply_summary(self.result))
        if (self.result or {}).get("ok"):
            self.accept()
        return self.result

    def _show_result(self, text: str) -> None:
        """Показывает итог применения под таблицей."""
        self.result_label.setText(text)
        self.result_label.setVisible(bool(text))


def open_template_diff_dialog(plan: Dict[str, Any],
                              parent: Optional[QWidget] = None) -> int:
    """
    Открывает диалог правки с настоящим применением к файлу.

    Единственная точка, где диалог связан с записью: применяет
    template_editor.apply_plan (с .bak, проверкой и откатом).
    """
    dialog = TemplateDiffDialog(plan, parent=parent,
                                apply=template_editor.apply_plan)
    try:
        return int(dialog.exec_())
    finally:
        dialog.deleteLater()


__all__ = [
    "COLUMNS",
    "UNSAFE_MARK",
    "TemplateDiffDialog",
    "apply_summary",
    "open_template_diff_dialog",
    "plan_rows",
    "safety_note",
]

