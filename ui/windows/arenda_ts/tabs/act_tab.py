#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Акт» окна типа «Разовая аренда» (ШАГ FIX-3).

Приложение № 1 к договору аренды — «Акт приема-передачи и возврата
транспортного средства с экипажем». Это часть ТОГО ЖЕ файла: акт
начинается с новой страницы и печатается теми же плейсхолдерами, что и
договор (templates/shablon_arenda_ts_*.docx).

Зачем отдельная вкладка
-----------------------
До шага FIX-3 эти поля заполнялись только вручную в Word: бланк печатал
пустые ячейки. Полей десять, и на вкладке «ТС» они бы смешались с данными
договора (номер, срок аренды, тягач, прицеп) — поэтому они вынесены на
свою вкладку, как и предлагал шаг.

Группы и поля (порядок — как в таблицах Акта):

    Передача ТС:  transfer_place      — Место передачи;
                  transfer_datetime   — Фактические дата и время передачи;
                  transfer_mileage    — Пробег на момент передачи;
                  transfer_condition  — Внешнее состояние / замечания;
                  transfer_documents  — Переданные документы;
    Возврат ТС:   return_place        — Место возврата;
                  return_datetime     — Фактические дата и время возврата;
                  return_mileage      — Пробег на момент возврата;
                  return_condition    — Состояние ТС / замечания;
                  return_notes        — Иные отметки.

Ключи get_data() — ровно эти десять: их читает
ui/windows/arenda_ts/data.py::_build_act и кладёт в contract, откуда их
берёт карта замен генератора (ArendaTsGenerator._fill_act).

Перечень документов (transfer_documents) — ОБЫЧНОЕ редактируемое поле:
генератор печатает в нём своё значение по умолчанию, если поле оставили
пустым (TRANSFER_DOCUMENTS в core/contracts/arenda_ts/generator.py).
Плейсхолдер снят с бланка на шаге FIX-3 — у строки одно место правки.

Распознавание (панель сверху) на этой вкладке есть как у всех вкладок
окна, но промпт аренды поля Акта НЕ извлекает: акт — форма для заполнения
при передаче ТС, и в ответе модели этих данных нет (core/prompts/
arenda_ts.py, раздел «ЧЕГО В ОТВЕТЕ БЫТЬ НЕ ДОЛЖНО»). Панель нужна, чтобы
вставить текст акта и заполнить поля вручную.
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from ui import theme
from ui.tabs.base_tab import TabMixin
from ui.widgets import (
    PasteableLineEdit, PasteableTextEdit, RecognitionPanel,
)

logger = logging.getLogger("ui.windows.arenda_ts.tabs.act_tab")

#: Поля-строки вкладки: место, дата и время, пробег. Порядок — как в Акте.
LINE_FIELDS = (
    "transfer_place", "transfer_datetime", "transfer_mileage",
    "return_place", "return_datetime", "return_mileage",
)

#: Поля-абзацы: состояние ТС, замечания и отметки (могут быть длинными).
TEXT_FIELDS = (
    "transfer_condition", "return_condition", "return_notes",
)

#: Переданные документы — тоже строка, но идёт последней в блоке передачи.
DOCUMENTS_FIELD = "transfer_documents"

#: Все десять полей Акта — ровно ключи get_data().
ACT_FIELDS = (
    "transfer_place", "transfer_datetime", "transfer_mileage",
    "transfer_condition", "transfer_documents",
    "return_place", "return_datetime", "return_mileage",
    "return_condition", "return_notes",
)

#: Подсказка к полю «Переданные документы»: пустое поле не оставит строку
#: пустой — генератор напечатает своё значение по умолчанию.
DOCUMENTS_TOOLTIP = (
    "Если оставить пустым, в Акте напечатается перечень по умолчанию: "
    "«СТС на тягач и прицеп/полуприцеп; ОСАГО; иные:»"
)


class ActTab(TabMixin, QWidget):
    """Приложение № 1: передача и возврат ТС."""

    # Сигнал для передачи текста в окно на распознавание
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    def __init__(self):
        super().__init__()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)

        # ── Панель распознавания ──
        self.recognition_panel = RecognitionPanel(
            placeholder="Вставьте текст акта приёма-передачи (место, дата, "
                        "пробег, состояние ТС)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Группа «Передача ТС в аренду» ──
        transfer_group, transfer_layout = theme.section_box("Передача ТС в аренду")

        self.transfer_place = PasteableLineEdit("г. Москва, ул. Складская, д. 1")
        transfer_layout.addRow("Место передачи", self.transfer_place)

        self.transfer_datetime = PasteableLineEdit("21.09.2026 08:30")
        transfer_layout.addRow(
            "Фактические дата и время передачи", self.transfer_datetime
        )

        self.transfer_mileage = PasteableLineEdit("125 400 км")
        transfer_layout.addRow("Пробег на момент передачи", self.transfer_mileage)

        self.transfer_condition = PasteableTextEdit(
            "Внешнее состояние ТС, замечания при передаче...", max_height=60
        )
        transfer_layout.addRow(
            "Внешнее состояние / замечания", self.transfer_condition
        )

        self.transfer_documents = PasteableLineEdit()
        self.transfer_documents.setToolTip(DOCUMENTS_TOOLTIP)
        transfer_layout.addRow("Переданные документы", self.transfer_documents)

        layout.addWidget(transfer_group)

        # ── Группа «Возврат ТС» ──
        return_group, return_layout = theme.section_box("Возврат ТС")

        self.return_place = PasteableLineEdit("г. Москва, ул. Складская, д. 1")
        return_layout.addRow("Место возврата", self.return_place)

        self.return_datetime = PasteableLineEdit("27.09.2026 19:00")
        return_layout.addRow(
            "Фактические дата и время возврата", self.return_datetime
        )

        self.return_mileage = PasteableLineEdit("128 130 км")
        return_layout.addRow("Пробег на момент возврата", self.return_mileage)

        self.return_condition = PasteableTextEdit(
            "Состояние ТС и замечания при возврате...", max_height=60
        )
        return_layout.addRow("Состояние ТС / замечания", self.return_condition)

        self.return_notes = PasteableTextEdit(
            "Иные отметки по акту...", max_height=60
        )
        return_layout.addRow("Иные отметки", self.return_notes)

        layout.addWidget(return_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Разовая аренда ActTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def get_data(self) -> Dict[str, Any]:
        """
        Десять полей Акта (ключи — те же, что читает сборка данных).

        Пустые значения отдаются пустыми строками: генератор напечатает
        пустое место, а значение по умолчанию у него своё (перечень
        документов) — подставлять его во вкладку не нужно, иначе поле
        выглядело бы заполненным без участия пользователя.
        """
        return {field: self._field_value(field) for field in ACT_FIELDS}

    def _field_value(self, field: str) -> str:
        """Значение поля без крайних пробелов: строка или абзац."""
        widget = getattr(self, field)
        if field in TEXT_FIELDS:
            return widget.toPlainText().strip()
        return widget.text().strip()

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет поля Акта.

        Пустые значения игнорируются: частичный ответ не должен сбрасывать
        уже введённые данные. Ключи принимаются и с блока contract ответа
        распознавания — вкладка читает их теми же именами, что и генератор.
        """
        if not data:
            return

        for field in ACT_FIELDS:
            value = str(data.get(field) or "").strip()
            if not value:
                continue

            widget = getattr(self, field)
            if field in TEXT_FIELDS:
                widget.setPlainText(value)
            else:
                widget.setText(value)

        logger.info("Разовая аренда: данные акта заполнены")

    def clear(self) -> None:
        """Очищает десять полей Акта и панель распознавания."""
        for field in ACT_FIELDS:
            getattr(self, field).clear()
        self.recognition_panel.clear()

        logger.debug("Разовая аренда: поля акта очищены")


__all__ = [
    "ActTab",
    "ACT_FIELDS",
    "LINE_FIELDS",
    "TEXT_FIELDS",
    "DOCUMENTS_FIELD",
    "DOCUMENTS_TOOLTIP",
]
