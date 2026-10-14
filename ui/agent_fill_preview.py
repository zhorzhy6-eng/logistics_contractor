#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог превью автозаполнения: «что нашли → куда попадёт» (задание, часть 1, B).

Зачем отдельный диалог
----------------------
Оператор присылает Максимилиану текст с данными рейса. Прежде чем писать
что-то в форму, он должен увидеть разбор целиком: какие поля нашлись, какими
значениями и в какой раздел формы они пойдут, а что осталось непонятым.
Этот диалог — тот самый шаг подтверждения (часть B.1), но не с вопросом
«заполнить?», а с таблицей:

    Поле            | Значение          | Куда
    ТС (2)          | EC37CUSM4TC008647 | Перевозимые ТС
    Сумма           | 80 000            | Договор
    Перевозчик      | ООО «Ромашка»     | Перевозчик

Строки разбора повторно не считаются: диалог показывает ровно то, что вернул
``core/agent/fill_parser.py::parse_text`` — иначе оператор увидел бы один
разбор, а в форму попал бы другой.

Правка значений (часть B.2)
---------------------------
Колонка «Значение» редактируется прямо в таблице: адрес, сумму и VIN можно
исправить, не закрывая диалог. Исправленное возвращается в вызов инструмента
(``parsed.overrides()`` → аргументы ``items`` и ``vehicles``), поэтому
заполнение идёт уже по правленым данным. Кнопка «Править» ставит курсор в
ячейку значения и подсказывает, что её можно менять.

Границы: диалог ничего не пишет в форму и не трогает базу — только показывает
и собирает правки.
"""

import logging
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.agent.fill_parser import ParsedFill, parse_text
from ui import theme

logger = logging.getLogger("ui.agent_fill_preview")

#: Роли данных ячейки: разбор строки кладётся рядом с текстом, чтобы правки
#: можно было собрать точно (по разделу и полю), а не по номеру строки.
PARSE_ROLE = Qt.UserRole + 41

#: Знак статуса строки. Латинские «OK» и «!» вместо галочек: сборка Qt без
#: своей папки шрифтов (обычная для offscreen-прогонов) не рисует «✔» и «⚠» —
#: вместо знака в таблице появлялся пустой квадрат.
STATUS_MARKS = {"ok": "OK", "warning": "!"}


def _text(value: Any) -> str:
    """Значение строкой без «None» и лишних пробелов."""
    if value is None:
        return ""
    return str(value).strip()


class FillPreviewDialog(QDialog):
    """
    Таблица разбора с кнопками [Подтвердить] [Отменить] [Править].

    :param parsed: результат ``parse_text`` (что нашли в тексте);
    :param parent: родитель-виджет (панель агента или окно);
    :param raw_text: исходный текст оператора — показывается в подробностях.
    """

    def __init__(self, parsed: ParsedFill, parent: Optional[QWidget] = None,
                 raw_text: str = ""):
        super().__init__(parent)
        self._parsed = parsed
        self._raw_text = raw_text or parsed.raw_text
        self._rows: List[Dict[str, Any]] = list(parsed.preview_rows())

        self.setWindowTitle("Максимилиан — проверить заполнение")
        self.setMinimumSize(760, 520)
        self._confirmed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        title = QLabel("Разобрал текст. Проверьте, что и куда попадёт:")
        title.setObjectName("appSubheading")
        title.setWordWrap(True)
        layout.addWidget(title)

        layout.addWidget(self._build_table(), 1)
        layout.addWidget(self._build_summary())

        self.yes_button = QPushButton("Подтвердить")
        self.no_button = QPushButton("Отменить")
        self.edit_button = QPushButton("Править")
        self.no_button.setDefault(True)
        self.no_button.clicked.connect(self.reject)
        self.yes_button.clicked.connect(self.accept)
        self.edit_button.clicked.connect(self.focus_value_cell)

        buttons = QHBoxLayout()
        buttons.addWidget(self.edit_button)
        buttons.addStretch()
        buttons.addWidget(self.no_button)
        buttons.addWidget(self.yes_button)
        layout.addLayout(buttons)

        if not self._rows:
            self._no_data_hint(layout)

    # ---------------------------------------------------------
    # Построение
    # ---------------------------------------------------------
    def _build_table(self) -> QTableWidget:
        """
        Таблица «поле — значение — куда».

        Редактируется только колонка значения: имена полей и разделы — из
        разбора, менять их оператору нечем и незачем.
        """
        table = QTableWidget(len(self._rows), 3, self)
        table.setHorizontalHeaderLabels(["Поле", "Значение", "Куда"])
        table.setObjectName("fillPreviewTable")
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.DoubleClicked
                              | QAbstractItemView.EditKeyPressed)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        for row, data in enumerate(self._rows):
            status = _text(data.get("status")) or "ok"
            first = QTableWidgetItem(status_mark(status) + " " + _text(data.get("field")))
            first.setFlags(first.flags() & ~Qt.ItemIsEditable)
            first.setData(PARSE_ROLE, dict(data))
            note = _text(data.get("note"))
            if note:
                first.setToolTip(note)

            value = QTableWidgetItem(_text(data.get("value")))
            value.setData(PARSE_ROLE, dict(data))
            if _text(data.get("section")) == "vehicles":
                #: Машина — это VIN и марка вместе: править их в одной
                #: ячейке нельзя, а правит оператор исходный текст.
                value.setFlags(value.flags() & ~Qt.ItemIsEditable)
            if note:
                value.setToolTip(note)
            if status == "warning":
                value.setBackground(theme.warning_cell_color())
                value.setToolTip(note or "Проверьте значение")

            target = QTableWidgetItem(_text(data.get("target")))
            target.setFlags(target.flags() & ~Qt.ItemIsEditable)

            table.setItem(row, 0, first)
            table.setItem(row, 1, value)
            table.setItem(row, 2, target)
            table.setRowHeight(row, 26)

        self.table = table
        return table

    def _build_summary(self) -> QWidget:
        """Сводка: сколько полей, чего не поняли, о чём предупредить."""
        lines: List[str] = []
        counts = self._parsed.counts()
        lines.append(
            f"Разобрано: {counts['fields_total']} значений"
            + (f", машин: {counts['vehicles']}" if counts["vehicles"] else "")
            + (f", полей из базы: {counts['entity_fields']}"
               if counts["entity_fields"] else "")
        )

        for note in self._parsed.warnings[:3]:
            lines.append("⚠ " + note)
        for record in self._parsed.missing_entities:
            title = "Перевозчик" if record.get("kind") == "carrier" else "Заказчик"
            lines.append(
                f"⚠ {title}: «{record.get('name')}» в базе не нашёл — "
                f"проверьте название"
            )
        for record in self._parsed.ambiguous_entities:
            title = "Перевозчик" if record.get("kind") == "carrier" else "Заказчик"
            lines.append(
                f"⚠ {title}: «{record.get('name')}» — похожих записей "
                f"{len(record.get('candidates') or [])}, уточните название"
            )

        unparsed = self._parsed.unresolved
        if unparsed:
            shown = "; ".join(unparsed[:3])
            more = f" и ещё {len(unparsed) - 3}" if len(unparsed) > 3 else ""
            lines.append(f"Не понял: {shown}{more}")

        label = QLabel("\n".join(lines))
        label.setObjectName("mutedLabel")
        label.setWordWrap(True)
        return label

    def _no_data_hint(self, layout: QVBoxLayout) -> None:
        """Отдельная подсказка, если разобрать не удалось ничего."""
        hint = QLabel(
            "В тексте не нашлось данных для формы: ни винов, ни адресов, "
            "ни суммы. Пришлите данные строками — «Вины:», «Загрузка:», "
            "«Выгрузка:», «Перевозчик:», «Сумма:»."
        )
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        layout.insertWidget(1, hint)

    # ---------------------------------------------------------
    # Управление
    # ---------------------------------------------------------
    def focus_value_cell(self) -> None:
        """«Править»: курсор в первую ячейку значения и подсказка внизу."""
        if self.table.rowCount() == 0:
            return
        self.table.setCurrentCell(0, 1)
        self.table.editItem(self.table.item(0, 1))
        self.edit_button.setToolTip("Исправьте значение и нажмите «Подтвердить»")

    def accept(self) -> None:
        """Оператор подтвердил разбор."""
        self._confirmed = True
        super().accept()

    def is_confirmed(self) -> bool:
        """Нажато «Подтвердить» (а не «Отменить» и не крестик)."""
        return self._confirmed

    def table_values(self) -> List[Dict[str, Any]]:
        """
        Значения из таблицы: строка на каждое поле (с учётом правок).

        Каждая строка несёт свой разбор (``PARSE_ROLE``): по нему видно и
        раздел, и поле, и исходное значение.
        """
        rows: List[Dict[str, Any]] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            value_item = self.table.item(row, 1)
            data = dict(item.data(PARSE_ROLE) or {}) if item is not None else {}
            if not data:
                continue
            #: В ячейке «Поле» стоит ПОДПИСЬ для оператора («Сумма»), а имя
            #: поля для формы лежит в разборе строки: по нему правка и
            #: возвращается в инструмент.
            data["label"] = _text(data.get("field"))
            data["new_value"] = value_item.text() if value_item is not None else ""
            rows.append(data)
        return rows

    @property
    def parsed(self) -> ParsedFill:
        """Разбор, который показывает диалог (повторного разбора нет)."""
        return self._parsed

    def overrides(self) -> Dict[str, Any]:
        """
        Правки оператора для инструмента: ``{"items": …, "vehicles": …}``.

        Сравнение идёт с исходным разбором: неизменённые строки в правки не
        попадают, поэтому в форму уходит ровно то, что оператор видоизменил
        (правило «перезаписываем только правленое»).
        """
        original: Dict[tuple, str] = {}
        for item in self._parsed.items:
            if isinstance(item.value, list):
                continue
            original[(item.section, item.field)] = item.preview_text()

        vehicles: List[Dict[str, Any]] = [
            {"vin": str(vehicle.get("vin") or ""),
             "brand_model": str(vehicle.get("brand_model") or "")}
            for vehicle in self._parsed.vehicles
        ]

        items: List[Dict[str, Any]] = []
        for row in self.table_values():
            section = _text(row.get("section"))
            name = _text(row.get("field_name")) or _text(row.get("field"))
            new_value = _text(row.get("new_value"))
            if section == "vehicles":
                #: Машины показываются одной строкой «VIN · марка» и в
                #: превью не правятся: правка VIN ушла бы в марку.
                continue
            key = (section, name)
            if key not in original or original[key] == new_value:
                continue
            items.append({"section": section, "field": name,
                          "value": new_value})

        edited_vehicles = [
            row for row, source in zip(vehicles, self._parsed.vehicles)
            if (row["vin"] != str(source.get("vin") or "")
                or row["brand_model"] != str(source.get("brand_model") or ""))
        ]

        result: Dict[str, Any] = {}
        if items:
            result["items"] = items
        if edited_vehicles:
            result["vehicles"] = edited_vehicles
        return result

    # ---------------------------------------------------------
    # Тесты и пробы
    # ---------------------------------------------------------
    def auto_answer(self, answer: bool, delay_ms: int = 0) -> None:
        """
        Отвечает за оператора через ``delay_ms`` (тесты, пробы, скриншоты).

        Диалог всё равно показывается, поэтому проверяется настоящий путь
        подтверждения, а не обходной. Слот — метод диалога, без замыкания на
        кнопку: замыкание даёт цикл ссылок Python ↔ Qt и роняет процесс при
        завершении (AGENTS.md § 5.1).
        """
        from PyQt5.QtCore import QTimer

        self._auto_answer = bool(answer)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._apply_auto_answer)
        timer.start(max(0, int(delay_ms)))
        self._auto_timer = timer

    def _apply_auto_answer(self) -> None:
        """Закрывает диалог ответом, который задал auto_answer()."""
        if self._auto_answer:
            self.accept()
        else:
            self.reject()

    def set_value(self, row: int, value: str) -> None:
        """Меняет значение в строке — так правка проверяется тестом."""
        item = self.table.item(row, 1)
        if item is not None:
            item.setText(str(value))


def status_mark(status: Any) -> str:
    """Знак статуса строки: «OK» разобрано, «!» требует внимания."""
    return STATUS_MARKS.get(_text(status) or "ok", "OK")


def _index_from_label(label: str) -> Optional[int]:
    """Номер машины из подписи «ТС 2» (None — подпись не о машине)."""
    digits = "".join(char for char in str(label) if char.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def preview_confirmation(text: Any, parent: Optional[QWidget] = None,
                         auto_answer: Optional[bool] = None,
                         auto_delay_ms: int = 0,
                         use_dadata: bool = True
                         ) -> Dict[str, Any]:
    """
    Показывает превью разбора и возвращает решение оператора.

    :return: ``{"confirmed": bool, "overrides": {...}, "parsed": ParsedFill}``.
        ``overrides`` пусто, если оператор ничего не правил.
    """
    parsed = parse_text(text, use_dadata=use_dadata)
    safe_parent = parent if isinstance(parent, QWidget) else None
    dialog = FillPreviewDialog(parsed, safe_parent, raw_text=str(text or ""))
    if auto_answer is not None:
        dialog.auto_answer(auto_answer, auto_delay_ms)
    try:
        dialog.exec_()
        confirmed = dialog.is_confirmed()
        return {
            "confirmed": confirmed,
            "overrides": dialog.overrides() if confirmed else {},
            "parsed": parsed,
        }
    finally:
        # Явно отпускаем C++-объект: диалог мог быть создан без родителя, и
        # сборщик мусора Python убрал бы его раньше, чем Qt закончит работу.
        dialog.deleteLater()


__all__ = [
    "FillPreviewDialog",
    "PARSE_ROLE",
    "STATUS_MARKS",
    "preview_confirmation",
    "status_mark",
]
