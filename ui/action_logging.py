"""Журнал нажатий кнопок во всех окнах приложения без значений из форм."""

import logging
import re

from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtWidgets import QAbstractButton, QApplication, QWidget

from core import audit

logger = logging.getLogger(__name__)

# Только надписи, заданные программой. Текст произвольной кнопки может
# содержать имя, адрес или другое значение из пользовательского документа.
SAFE_LABELS = frozenset({
    "Отмена", "Закрыть", "Сохранить", "💾 Сохранить", "Сбросить",
    "Добавить файлы", "Открыть выбранный оригинал", "Распознать",
    "Отменить обработку", "Добавить пустые поля",
    "Перенести подтверждённые поля", "Показать ещё", "Все адреса показаны",
    "➕ Добавить в справочник", "📥 Импорт из Excel", "✏ Редактировать",
    "🗑 Удалить", "✓ Выбрать", "📂 Загрузить в форму",
    "Добавить погрузку", "Удалить погрузку", "Из справочника",
    "Добавить выгрузку", "Удалить выгрузку", "Добавить ТС", "Удалить ТС",
    "Обновить статус ключа", "Создать договор", "Исправить",
    "Открыть папку", "OK", "База данных", "Настройки", "Очистить форму",
    "Загрузить документы", "Отменить распознавание", "Распознать и заполнить",
    "Вставить", "Вставить из буфера", "Распознать данные",
    "Распознать вкладку", "Сохранить в базу",
    "Да", "Нет", "ОК", "Yes", "No", "Cancel", "Close",
})


def _button_name(button: QAbstractButton) -> str:
    label = re.sub(r"\s+", " ", button.text()).strip().replace("&", "")
    if label in SAFE_LABELS:
        return label
    tooltip = button.toolTip()
    if tooltip in {"Вставить из буфера обмена", "Вставить дату из буфера обмена",
                   "Распознать через GigaChat",
                   "Заполнить реквизиты по ИНН через DaData"}:
        return tooltip
    if re.fullmatch(r"Показать ещё \d+", label):
        return "Показать ещё"
    name = button.objectName()
    if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", name):
        return name
    return type(button).__name__


class ActionLogger(QObject):
    """Подписывается на clicked у видимых и позднее созданных кнопок."""

    def __init__(self, app: QApplication):
        super().__init__(app)
        self.app = app
        app.installEventFilter(self)
        for widget in app.allWidgets():
            self._register(widget)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Show and isinstance(watched, QWidget):
            self._register(watched)
            for button in watched.findChildren(QAbstractButton):
                self._register(button)
        return False

    def _register(self, widget: QWidget) -> None:
        if not isinstance(widget, QAbstractButton):
            return
        if widget.property("_action_logging_connected"):
            return
        widget.setProperty("_action_logging_connected", True)
        widget.clicked.connect(lambda _checked=False, button=widget: self._clicked(button))

    @staticmethod
    def _clicked(button: QAbstractButton) -> None:
        label = _button_name(button)
        window = button.window()
        context = type(window).__name__ if window is not None else "unknown"
        logger.info("Нажата кнопка: %s | окно=%s", label, context)
        audit.log_event("button_clicked", source=label, entity=context)


def install_action_logging(app: QApplication) -> ActionLogger:
    """Устанавливается один раз после создания QApplication."""
    existing = getattr(app, "_action_logger", None)
    if existing is not None:
        return existing
    app._action_logger = ActionLogger(app)
    logger.info("Журнал нажатий кнопок включён")
    return app._action_logger
