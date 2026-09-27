#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Кастомные виджеты для UI.
Добавляет кнопку «📋» к полям ввода для быстрой вставки из буфера.
"""

import logging
from typing import Optional, Callable

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QPushButton,
    QTextEdit, QVBoxLayout, QApplication, QLabel,
    QFrame, QSizePolicy, QDialog, QMessageBox,
)
from PyQt5.QtCore import Qt, QDate, pyqtSignal
from PyQt5.QtGui import QFont

from core.dates import parse_date
from ui import theme

logger = logging.getLogger("ui.widgets")


class PasteableLineEdit(QWidget):
    """QLineEdit с кнопкой «📋» для вставки из буфера обмена."""

    paste_requested = pyqtSignal()
    recognize_requested = pyqtSignal()

    def __init__(
        self,
        placeholder: str = "",
        parent: Optional[QWidget] = None,
        show_paste_button: bool = True,
        show_recognize_button: bool = True,
    ):
        super().__init__(parent)

        self._required = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.line_edit = QLineEdit()
        self.line_edit.setPlaceholderText(placeholder)
        layout.addWidget(self.line_edit, 1)

        if show_paste_button:
            self.btn_paste = theme.ghost_button(
                "📋", tooltip="Вставить из буфера обмена"
            )
            self.btn_paste.clicked.connect(self._on_paste_clicked)
            layout.addWidget(self.btn_paste)

        if show_recognize_button:
            self.btn_recognize = theme.ghost_button(
                "🧠", tooltip="Распознать через GigaChat"
            )
            self.btn_recognize.clicked.connect(self._on_recognize_clicked)
            layout.addWidget(self.btn_recognize)

        self.line_edit.textChanged.connect(self._refresh_required_style)

    # ── Обязательное поле: бледно-жёлтый фон, пока пусто ──
    def set_required(self, required: bool) -> None:
        self._required = bool(required)
        self._refresh_required_style()

    def is_required(self) -> bool:
        return self._required

    def is_empty(self) -> bool:
        return not self.line_edit.text().strip()

    def _refresh_required_style(self) -> None:
        highlighted = self._required and self.is_empty()
        self.line_edit.setStyleSheet(theme.REQUIRED_EMPTY_QSS if highlighted else "")

    def _on_paste_clicked(self) -> None:
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text:
            self.line_edit.setText(text)
            # Содержимое буфера — персональные данные, поэтому только длина
            logger.debug(f"Вставлено из буфера: {len(text)} символов")
            self.paste_requested.emit()

    def _on_recognize_clicked(self) -> None:
        self.recognize_requested.emit()

    def text(self) -> str:
        return self.line_edit.text()

    def setText(self, text: str) -> None:
        self.line_edit.setText(text)

    def clear(self) -> None:
        self.line_edit.clear()

    def setPlaceholderText(self, text: str) -> None:
        self.line_edit.setPlaceholderText(text)

    def setMaxLength(self, length: int) -> None:
        self.line_edit.setMaxLength(length)


class PasteableTextEdit(QWidget):
    """QTextEdit с кнопками «📋» и «🧠»."""

    paste_requested = pyqtSignal()
    recognize_requested = pyqtSignal()

    def __init__(
        self,
        placeholder: str = "",
        parent: Optional[QWidget] = None,
        max_height: int = 100,
        show_paste_button: bool = True,
        show_recognize_button: bool = True,
    ):
        super().__init__(parent)

        self._required = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText(placeholder)
        self.text_edit.setMaximumHeight(max_height)
        layout.addWidget(self.text_edit)

        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)

        if show_paste_button:
            self.btn_paste = theme.secondary_button("📋 Вставить из буфера")
            self.btn_paste.clicked.connect(self._on_paste_clicked)
            button_layout.addWidget(self.btn_paste)

        if show_recognize_button:
            self.btn_recognize = theme.secondary_button("🧠 Распознать")
            self.btn_recognize.clicked.connect(self._on_recognize_clicked)
            button_layout.addWidget(self.btn_recognize)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        self.text_edit.textChanged.connect(self._refresh_required_style)

    # ── Обязательное поле: бледно-жёлтый фон, пока пусто ──
    def set_required(self, required: bool) -> None:
        self._required = bool(required)
        self._refresh_required_style()

    def is_required(self) -> bool:
        return self._required

    def is_empty(self) -> bool:
        return not self.text_edit.toPlainText().strip()

    def _refresh_required_style(self) -> None:
        highlighted = self._required and self.is_empty()
        self.text_edit.setStyleSheet(
            theme.REQUIRED_EMPTY_QSS_TEXT if highlighted else ""
        )

    def _on_paste_clicked(self) -> None:
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text:
            self.text_edit.setPlainText(text)
            self.paste_requested.emit()

    def _on_recognize_clicked(self) -> None:
        self.recognize_requested.emit()

    def toPlainText(self) -> str:
        return self.text_edit.toPlainText()

    def setPlainText(self, text: str) -> None:
        self.text_edit.setPlainText(text)

    def clear(self) -> None:
        self.text_edit.clear()

    def setPlaceholderText(self, text: str) -> None:
        self.text_edit.setPlaceholderText(text)


class PasteableDateEdit(QWidget):
    """
    QDateEdit с кнопкой «📋» для вставки даты из буфера.

    Поддерживает:
      - setDate(datetime) — конвертирует в QDate
      - setDate(QDate)    — передаёт как есть
      - setDate(str)      — парсит и устанавливает
    """

    def __init__(self, date_edit, parent: Optional[QWidget] = None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.date_edit = date_edit
        layout.addWidget(self.date_edit, 1)

        self.btn_paste = theme.ghost_button(
            "📋", tooltip="Вставить дату из буфера обмена"
        )
        self.btn_paste.clicked.connect(self._on_paste_clicked)
        layout.addWidget(self.btn_paste)

    def _on_paste_clicked(self) -> None:
        clipboard = QApplication.clipboard()
        text = clipboard.text().strip()

        if not text:
            return

        dt = parse_date(text)
        if dt is None:
            # Текст из буфера может содержать персональные данные — пишем
            # только длину, а не сам текст.
            logger.warning(
                f"Не удалось распознать дату из буфера: {len(text)} символов"
            )
            return

        self.date_edit.setDate(QDate(dt.year, dt.month, dt.day))
        logger.debug(f"Дата вставлена из буфера: {dt.strftime('%d.%m.%Y')}")

    def date(self):
        return self.date_edit.date()

    def setDate(self, date) -> None:
        """
        Универсальный метод установки даты.

        Принимает QDate / datetime / str. Разбор строк — общий,
        из core.dates (раньше здесь был пятый дубль парсера).
        """
        # ── QDate: передаём как есть ──
        if isinstance(date, QDate):
            self.date_edit.setDate(date)
            return

        # ── Пустое значение: молча ничего не делаем ──
        if date is None or (isinstance(date, str) and not date.strip()):
            return

        dt = parse_date(date)
        if dt is None:
            shown = (
                f"<строка, {len(date)} символов>"
                if isinstance(date, str) else repr(date)
            )
            logger.warning(
                f"PasteableDateEdit.setDate: не удалось установить {shown}"
            )
            return

        self.date_edit.setDate(QDate(dt.year, dt.month, dt.day))


class RecognitionPanel(QWidget):
    """
    Панель для вставки текста и распознавания через GigaChat.
    Используется на каждой вкладке отдельно.
    """

    recognize_requested = pyqtSignal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        placeholder: str = "Вставьте текст для распознавания...",
    ):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)

        self.btn_paste = theme.secondary_button("📋 Вставить из буфера")
        self.btn_paste.clicked.connect(self._on_paste)
        button_layout.addWidget(self.btn_paste)

        # Главное действие вкладки: крупная акцентная кнопка (см. ui/theme.py)
        self.btn_recognize = theme.primary_button(
            "🧠 Распознать вкладку",
            tooltip="Распознать вставленный текст и заполнить поля вкладки",
        )
        self.btn_recognize.clicked.connect(self._on_recognize)
        button_layout.addWidget(self.btn_recognize)

        button_layout.addStretch()

        layout.addLayout(button_layout)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText(placeholder)
        self.text_edit.setMaximumHeight(80)
        layout.addWidget(self.text_edit)

    def _on_paste(self) -> None:
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text:
            self.text_edit.setPlainText(text)
            logger.debug(f"Текст вставлен из буфера: {len(text)} символов")

    def _on_recognize(self) -> None:
        text = self.text_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Нет данных", "Вставьте текст для распознавания.")
            return

        self.recognize_requested.emit(text)

    def clear(self) -> None:
        self.text_edit.clear()

    def get_text(self) -> str:
        return self.text_edit.toPlainText().strip()


class BulkPasteDialog(QDialog):
    """
    Диалог для вставки всего текста договора/реквизитов.
    Пользователь вставляет текст, и GigaChat распознаёт все данные.
    """

    def __init__(self, parent=None, on_recognize=None):
        super().__init__(parent)

        self.on_recognize = on_recognize

        self.setWindowTitle("🧠 Вставить все данные для распознавания")
        self.setMinimumSize(700, 500)

        layout = QVBoxLayout(self)

        # Заголовок
        title_label = QLabel("Вставьте текст с данными (реквизиты, паспорт, ВУ и т.д.):")
        title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title_label)

        # Текстовое поле для вставки
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText(
            "Вставьте сюда весь текст из документов:\n"
            "— реквизиты организаций\n"
            "— паспортные данные\n"
            "— данные водительского удостоверения\n"
            "— данные автомобилей\n"
            "и т.д.\n\n"
            "Или нажмите «📋 Вставить из буфера», чтобы вставить всё из буфера обмена."
        )
        self.text_edit.setFont(QFont("Consolas", 10))
        layout.addWidget(self.text_edit)

        # Кнопки
        button_layout = QHBoxLayout()

        self.btn_paste = QPushButton("📋 Вставить из буфера")
        self.btn_paste.clicked.connect(self._on_paste)
        button_layout.addWidget(self.btn_paste)

        self.btn_recognize = QPushButton("🧠 Распознать и заполнить")
        self.btn_recognize.clicked.connect(self._on_recognize)
        self.btn_recognize.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 8px 20px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        button_layout.addWidget(self.btn_recognize)

        button_layout.addStretch()

        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        layout.addLayout(button_layout)

        logger.debug("BulkPasteDialog инициализирован")

    def _on_paste(self) -> None:
        """Вставляет текст из буфера."""
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text:
            self.text_edit.setPlainText(text)
            logger.info(f"Текст вставлен из буфера: {len(text)} символов")

    def _on_recognize(self) -> None:
        """Запускает распознавание."""
        text = self.text_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Нет данных", "Вставьте текст для распознавания.")
            return

        if self.on_recognize:
            self.on_recognize(text)
            self.accept()


__all__ = [
    'PasteableLineEdit',
    'PasteableTextEdit',
    'PasteableDateEdit',
    'RecognitionPanel',
    'BulkPasteDialog',
]