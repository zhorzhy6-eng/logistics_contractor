#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог «Юридическая проверка»: что в форме расходится с нормами права.

Что показывает
--------------
Итог проверки (сколько нарушений и какой важности), таблицу «важность —
нарушение — источник — комментарий» и норму целиком внизу: цитату, акт и
статью из правовой базы. Кнопка «Скопировать» отдаёт тот же текст в буфер —
оператор вставляет его в письмо клиенту или в замечания.

Чего диалог НЕ делает
---------------------
* **Не правит форму.** Проверка — чтение; исправляет оператор сам.
* **Не выдумывает нормы.** Текст берётся из результата проверки
  (services/agent/tools/legal_check.py), а описание нормы — из каталога
  (core/legal_rules.py). Пустой источник в диалоге выглядит как
  «источник не указан», а не как правдоподобная ссылка.

Почему отдельный файл: диалог нужен и кнопке в панели Максимилиана, и
тестам без окна. Логика проверки здесь не живёт — только показ.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
    QWidget,
)

from core import legal_rules
from services.agent.tools import legal_check as legal_check_tools
from ui import theme

logger = logging.getLogger("ui.legal_check_dialog")

#: Колонки таблицы нарушений: заголовок и ширина.
COLUMNS: Tuple[Tuple[str, int], ...] = (
    ("Важность", 110),
    ("Нарушение", 220),
    ("Источник", 240),
    ("Комментарий", 560),
)

#: Подписи важности для таблицы.
SEVERITY_LABELS = {
    "error": "Ошибка",
    "warning": "Замечание",
    "info": "Справка",
}


def violation_rows(result: Dict[str, Any]) -> List[Tuple[str, str, str, str]]:
    """
    Нарушения строкой таблицы: (важность, название, источник, комментарий).

    Функция чистая — её проверяют тесты без Qt, и ею же заполняется таблица:
    логика вывода не должна разъезжаться с проверкой.
    """
    rows: List[Tuple[str, str, str, str]] = []
    for item in (result or {}).get("violations") or []:
        severity = str(item.get("severity") or "warning")
        rows.append((
            SEVERITY_LABELS.get(severity, severity),
            str(item.get("title") or item.get("rule") or ""),
            str(item.get("source") or "источник не указан"),
            " ".join(str(item.get("message") or "").split()),
        ))
    return rows


def detail_text(result: Dict[str, Any], row: int) -> str:
    """
    Подробности по строке таблицы: описание нормы с цитатой.

    Для строки вне таблицы возвращается итог проверки — пустой экран при
    нажатии на пустое место выглядел бы как сломанная кнопка.
    """
    violations = list((result or {}).get("violations") or [])
    if not 0 <= row < len(violations):
        return str((result or {}).get("summary") or "")

    item = violations[row]
    lines = [
        str(item.get("title") or item.get("rule") or ""),
        "",
        str(item.get("message") or ""),
        "",
    ]
    description = legal_rules.describe_rule(str(item.get("rule") or ""))
    if description:
        lines.append(description)
    return "\n".join(lines).strip()


def summary_with_skipped(result: Dict[str, Any]) -> str:
    """
    Итог для шапки диалога: сколько проверено и что осталось без проверки.

    Пропуски показываются числом, а не списком: оператору важно понять,
    что проверка была неполной, а причины он читает при желании.
    """
    result = result or {}
    summary = str(result.get("summary") or "")
    skipped = result.get("skipped") or []
    if skipped:
        summary += f" Без проверки: {len(skipped)} (нет поля в форме или справочная норма)."
    return summary


class LegalCheckDialog(QDialog):
    """
    Диалог результатов юридической проверки.

    :param result: ответ check_contract_legal (violations, summary, skipped);
    :param parent: родительское окно.

    Диалог ничего не сохраняет и не меняет: только показывает.
    """

    def __init__(self, result: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.result = dict(result or {})

        self.setWindowTitle("Юридическая проверка формы")
        self.setObjectName("legalCheckDialog")
        self.setMinimumSize(980, 560)
        if parent is not None:
            self.setStyleSheet(theme.qss())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Юридическая проверка формы")
        title.setObjectName("sectionHeading")
        layout.addWidget(title)

        self.summary_label = QLabel(summary_with_skipped(self.result))
        self.summary_label.setWordWrap(True)
        self.summary_label.setObjectName("mutedLabel")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(len(self.result.get("violations") or []), len(COLUMNS))
        self.table.setObjectName("legalCheckTable")
        self.table.setHorizontalHeaderLabels([title for title, _ in COLUMNS])
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

        self.details = QTextEdit()
        self.details.setObjectName("legalCheckDetails")
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(140)
        layout.addWidget(self.details, 2)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.btn_copy = theme.secondary_button(
            "Скопировать", tooltip="Скопировать текст проверки в буфер обмена", parent=self
        )
        self.btn_copy.clicked.connect(self.copy_text)
        buttons.addWidget(self.btn_copy)

        self.btn_close = theme.primary_button("Закрыть", parent=self)
        self.btn_close.clicked.connect(self.accept)
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)

        self._fill_table()
        self.table.itemSelectionChanged.connect(self.show_selected_norm)
        if self.table.rowCount():
            self.table.selectRow(0)
            self.show_selected_norm()

    # ── Таблица ──

    def _fill_table(self) -> None:
        """Заполняет таблицу нарушений (важность, название, источник, текст)."""
        rows = violation_rows(self.result)
        self.table.setRowCount(len(rows))
        for row, (severity, title, source, message) in enumerate(rows):
            for column, text in enumerate((severity, title, source, message)):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                item.setToolTip(text)
                self.table.setItem(row, column, item)
        self.table.resizeRowsToContents()

    def selected_row(self) -> int:
        """Номер выбранного нарушения (-1 — ничего не выбрано)."""
        selected = self.table.selectionModel().selectedRows()
        return selected[0].row() if selected else -1

    def show_selected_norm(self) -> None:
        """Показывает норму выбранного нарушения в нижнем поле."""
        self.details.setPlainText(detail_text(self.result, self.selected_row()))

    # ── Буфер обмена ──

    def text_for_clipboard(self) -> str:
        """Текст проверки для буфера: итог и все нарушения с источниками."""
        return legal_check_tools.format_violations(self.result)

    def copy_text(self) -> None:
        """Кладёт текст проверки в буфер обмена."""
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.text_for_clipboard())


def open_legal_check_dialog(result: Dict[str, Any],
                            parent: Optional[QWidget] = None) -> int:
    """
    Показывает диалог проверки модально и возвращает код закрытия.

    Отдельная функция — точка входа для кнопки панели: панели не нужно
    знать ни про таблицу, ни про кнопки диалога.
    """
    dialog = LegalCheckDialog(result, parent=parent)
    try:
        return int(dialog.exec_())
    finally:
        dialog.deleteLater()


__all__ = [
    "COLUMNS",
    "SEVERITY_LABELS",
    "LegalCheckDialog",
    "detail_text",
    "open_legal_check_dialog",
    "summary_with_skipped",
    "violation_rows",
]
