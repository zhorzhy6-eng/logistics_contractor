#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог «Шаблоны»: анализ бланка и предложение правки.

Зачем отдельное окно
--------------------
Шаблоны правятся руками в Word, и ошибку в них находят уже на печати.
Этот диалог показывает то, что раньше приходилось проверять глазами:

  * сколько плейсхолдеров и все ли они на месте;
  * парные ли условные блоки (%p if … %p endif);
  * есть ли устаревший текст — «НДС 20%» при действующей ставке 22%.

Если устаревший текст найден, кнопка «Предложить правку» строит diff и
открывает диалог подтверждения (ui/template_diff_dialog.py). Только там
оператор отмечает, что применять; само предложение файл не меняет.

Диалог ничего не пишет в шаблоны сам: применение выполняет
template_editor.apply_template_changes (.bak → правка → проверка → откат).
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from services.agent.tools import template_analyzer, template_editor
from ui import theme
from ui.template_diff_dialog import TemplateDiffDialog

logger = logging.getLogger("ui.template_tools_dialog")

#: Какое «что не так» соответствует найденному устаревшему тексту:
#: ключ — вид из анализатора, значение — текст, который ищет редактор.
ISSUE_BY_KIND: Dict[str, str] = {
    "vat_20": "НДС 20%",
    "vat_18": "НДС 18%",
}

#: Сколько плейсхолдеров показывать в анализе (остальные — числом).
MAX_PLACEHOLDERS_SHOWN = 40


def analysis_text(result: Dict[str, Any]) -> str:
    """
    Текст анализа шаблона для окна: паспорт, блоки, предупреждения.

    Формат — для чтения оператором, а не для машины: сначала «что это за
    файл», потом «что с ним не так».
    """
    result = result or {}
    lines: List[str] = []
    path_text = str(result.get("path") or "")
    lines.append("Файл: " + (Path(path_text).name if path_text else "—"))
    lines.append("Формат: " + (str(result.get("format") or "не определён")))
    placeholders = result.get("placeholders") or []
    total = sum(int(item.get("count") or 0) for item in placeholders)
    lines.append("Плейсхолдеров: " + str(result.get("placeholder_count", 0))
                 + " (всего вхождений: " + str(total) + ")")
    lines.append("Абзацев: " + str(result.get("paragraphs_count", 0))
                 + ", таблиц: " + str(result.get("tables_count", 0))
                 + ", стилей: " + str(result.get("styles_count", 0)))
    blocks = result.get("conditional_blocks") or []
    unclosed = result.get("unclosed_tags") or []
    lines.append("Условных блоков: " + str(len(blocks))
                 + " (не закрыто: " + str(len(unclosed)) + ")")
    outdated = result.get("outdated") or []
    if outdated:
        lines.append("")
        lines.append("Устаревший текст:")
        for item in outdated:
            lines.append("• " + str(item.get("message") or ""))
    warnings = result.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("Предупреждения:")
        for warning in warnings:
            lines.append("! " + str(warning))
    if placeholders:
        lines.append("")
        lines.append("Плейсхолдеры:")
        for item in placeholders[:MAX_PLACEHOLDERS_SHOWN]:
            name = str(item.get("name") or "")
            count = item.get("count")
            lines.append("• {{" + name + "}} × " + str(count))
        if len(placeholders) > MAX_PLACEHOLDERS_SHOWN:
            lines.append("… и ещё " + str(len(placeholders) - MAX_PLACEHOLDERS_SHOWN))
    return "\n".join(lines)


class TemplateToolsDialog(QDialog):
    """
    Окно «Шаблоны»: список бланков, анализ выбранного, предложение правки.

    :param templates: список путей (None — все найденные шаблоны проекта);
    :param parent: родительское окно;
    :param apply: функция применения правки (None — по умолчанию
        template_editor.apply_plan; подменяется тестами).
    """

    def __init__(self, templates: Optional[List[Any]] = None,
                 parent: Optional[QWidget] = None,
                 apply: Optional[Callable[..., Dict[str, Any]]] = None):
        super().__init__(parent)
        self._apply = apply
        self.last_plan: Optional[Dict[str, Any]] = None
        self.last_analysis: Optional[Dict[str, Any]] = None

        if templates is None:
            paths = [item["path"] for item in template_analyzer.list_templates()]
        else:
            paths = [str(item) for item in templates]
        self.templates: List[Path] = [Path(path) for path in paths]

        self.setWindowTitle("Шаблоны договоров")
        self.setObjectName("templateToolsDialog")
        self.setMinimumSize(1100, 640)
        if parent is not None:
            self.setStyleSheet(theme.qss())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Шаблоны договоров: анализ и безопасная правка")
        title.setObjectName("sectionHeading")
        layout.addWidget(title)

        hint = QLabel(
            "Шаблон меняется только после подтверждения в диалоге "
            "«было / стало». Перед правкой создаётся резервная копия .bak."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Horizontal)

        self.list = QListWidget()
        self.list.setObjectName("templateList")
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        for path in self.templates:
            item = QListWidgetItem(path.name)
            item.setData(Qt.UserRole, str(path))
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self.on_template_selected)
        splitter.addWidget(self.list)

        self.details = QTextEdit()
        self.details.setObjectName("templateDetails")
        self.details.setReadOnly(True)
        splitter.addWidget(self.details)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("mutedLabel")
        self.status.setVisible(False)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.refresh_button = theme.secondary_button(
            "Обновить", tooltip="Перечитать выбранный шаблон", parent=self)
        self.refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self.refresh_button)

        self.propose_button = theme.secondary_button(
            "Предложить правку",
            tooltip="Показать «было / стало» для устаревшего текста",
            parent=self)
        self.propose_button.clicked.connect(self.propose_fix)
        self.propose_button.setEnabled(False)
        buttons.addWidget(self.propose_button)
        buttons.addStretch()

        self.close_button = theme.primary_button("Закрыть", parent=self)
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        if self.templates:
            self.list.setCurrentRow(0)

    # ── Выбор и анализ ──

    def current_template(self) -> Optional[Path]:
        """Выбранный шаблон (None — ничего не выбрано)."""
        item = self.list.currentItem()
        if item is None:
            return None
        path_text = str(item.data(Qt.UserRole) or "")
        return Path(path_text) if path_text else None

    def on_template_selected(self, row: int) -> None:
        """Выбор шаблона: показать его анализ."""
        path = self.current_template()
        if path is None:
            return
        self.last_analysis = template_analyzer.analyze_template(path)
        self.details.setPlainText(analysis_text(self.last_analysis))
        issues = self.fix_issues()
        self.propose_button.setEnabled(bool(issues))
        if issues:
            self._set_status("Устаревший текст найден: есть что предложить.")
        else:
            self._set_status("Проверка пройдена: предлагать нечего.")

    def fix_issues(self) -> List[str]:
        """
        Что можно предложить поправить у выбранного шаблона.

        Возвращаются «что не так» для редактора: по одному на вид проблемы.
        Пустой список — предлагать нечего (кнопка выключается).
        """
        result = self.last_analysis or {}
        issues: List[str] = []
        for item in result.get("outdated") or []:
            issue = ISSUE_BY_KIND.get(str(item.get("kind")))
            if issue and issue not in issues:
                issues.append(issue)
        return issues

    def refresh(self) -> None:
        """Перечитывает выбранный шаблон (файл мог измениться)."""
        self.on_template_selected(self.list.currentRow())

    # ── Правка ──

    def propose_fix(self) -> Optional[Dict[str, Any]]:
        """
        Строит предложение правки и открывает диалог «было / стало».

        Предложение файл НЕ меняет. Применение — в диалоге подтверждения.
        """
        path = self.current_template()
        if path is None:
            return None
        issues = self.fix_issues()
        if not issues:
            self._set_status("Предлагать нечего: устаревшего текста нет.")
            return None

        plan = template_editor.propose_template_fix(path, issues[0])
        self.last_plan = plan
        if not plan.get("changes"):
            self._set_status("Правок не найдено: "
                             + "; ".join(plan.get("warnings") or []))
            return plan

        self._open_diff(plan)
        return plan

    def _open_diff(self, plan: Dict[str, Any]) -> None:
        """Открывает диалог подтверждения правки (модально)."""
        apply = self._apply or template_editor.apply_plan
        dialog = TemplateDiffDialog(plan, parent=self, apply=apply)
        try:
            dialog.exec_()
        finally:
            dialog.deleteLater()
        self.refresh()

    def _set_status(self, text: str) -> None:
        """Показывает строку состояния под списком."""
        self.status.setText(text)
        self.status.setVisible(bool(text))


def open_template_tools(parent: Optional[QWidget] = None) -> int:
    """
    Открывает окно «Шаблоны» модально.

    Точка входа для панели Максимилиана: кнопка не должна знать ни про
    список файлов, ни про анализ.
    """
    dialog = TemplateToolsDialog(parent=parent)
    try:
        return int(dialog.exec_())
    finally:
        dialog.deleteLater()


__all__ = [
    "ISSUE_BY_KIND",
    "MAX_PLACEHOLDERS_SHOWN",
    "TemplateToolsDialog",
    "analysis_text",
    "open_template_tools",
]

