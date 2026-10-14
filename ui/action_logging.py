"""Журнал нажатий кнопок во всех окнах приложения без значений из форм."""

import logging
import re
import time
from collections import deque
from typing import Deque, Dict, List, Tuple

from PyQt5.QtCore import QEvent, QObject, pyqtSignal
from PyQt5.QtWidgets import QAbstractButton, QApplication, QWidget

from core import audit

logger = logging.getLogger(__name__)

#: Сколько последних нажатий держим в памяти для Максимилиана (Часть 1).
#: Кольцевой буфер: старые записи вытесняются, память не растёт.
RECENT_LIMIT = 50

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
                   "Заполнить реквизиты по ИНН через DaData",
                   "Заполнить банк и корр. счёт по БИК через DaData",
                   "Найти организацию по названию в ЕГРЮЛ (DaData)"}:
        return tooltip
    if re.fullmatch(r"Показать ещё \d+", label):
        return "Показать ещё"
    name = button.objectName()
    if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", name):
        return name
    return type(button).__name__


class ActionLogger(QObject):
    """Подписывается на clicked у видимых и позднее созданных кнопок."""

    #: Оператор нажал кнопку: (надпись кнопки). Слушателю нужен только
    #: факт «оператор работает», поэтому значение из формы сюда не идёт.
    #: Подсказки Максимилиана (часть 4) по этому сигналу ждут паузы в
    #: работе оператора, прежде чем что-то предлагать.
    action_recorded = pyqtSignal(str)

    def __init__(self, app: QApplication):
        super().__init__(app)
        # Ссылка на приложение СИЛЬНАЯ и оставлена осознанно: она держит
        # QApplication живым, пока жив журнал (так же ведёт себя пара
        # «приложение ↔ его Qt-ребёнок»). Слабую ссылку пробовали — тогда
        # после возврата из main() приложение уничтожалось раньше времени
        # (тест tests/test_main_dispatch.py: автозакрытие проверяется у ЖИВОГО
        # приложения). К падению при выходе эта пара отношения не имеет.
        self.app = app
        # Последние нажатия: (время, надпись, окно) — для инструмента
        # агента «что делал оператор последние 5 минут» (Часть 1).
        # Значения из полей сюда НЕ попадают: берётся только имя кнопки.
        self._recent: Deque[Tuple[float, str, str]] = deque(maxlen=RECENT_LIMIT)
        app.installEventFilter(self)
        for widget in app.allWidgets():
            self._register(widget)

    # ---------------------------------------------------------
    # Последние действия (для Максимилиана)
    # ---------------------------------------------------------
    def recent(self, seconds: float = 300.0) -> List[Dict[str, object]]:
        """
        Нажатия кнопок за последние ``seconds`` секунд, новые в конце.

        :return: список словарей «когда / что / где»; значения из полей
            формы в журнал не попадают вовсе.
        """
        now = time.monotonic()
        horizon = max(0.0, float(seconds))
        result: List[Dict[str, object]] = []
        for moment, label, context in self._recent:
            age = now - moment
            if age > horizon:
                continue
            result.append({
                "seconds_ago": int(age),
                "action": label,
                "window": context,
            })
        return result

    def remember(self, label: str, context: str = "") -> None:
        """Запоминает одно действие (нужно тестам и ручной проверке)."""
        self._recent.append((time.monotonic(), str(label), str(context)))

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
        # Слот — метод самого ActionLogger, кнопка берётся у отправителя
        # сигнала. lambda, захватывающая кнопку и логгер, создаёт цикл ссылок
        # Python ↔ Qt (кнопка → связь в C++ → lambda → кнопка), а такой цикл
        # роняет процесс при выходе (грабли 2B.7).
        widget.clicked.connect(self._on_button_clicked)

    def _on_button_clicked(self, _checked: bool = False) -> None:
        """Нажатие кнопки: запись в журнал (кнопка — отправитель сигнала)."""
        button = self.sender()
        if not isinstance(button, QAbstractButton):
            logger.warning("Журнал кнопок: отправитель сигнала не кнопка — пропуск")
            return
        self._remember_click(button)
        self._clicked(button)

    @staticmethod
    def _clicked(button: QAbstractButton) -> None:
        label = _button_name(button)
        window = button.window()
        context = type(window).__name__ if window is not None else "unknown"
        logger.info("Нажата кнопка: %s | окно=%s", label, context)
        audit.log_event("button_clicked", source=label, entity=context)

    def _remember_click(self, button: QAbstractButton) -> None:
        """Кладёт нажатие в кольцевой буфер последних действий."""
        window = button.window()
        context = type(window).__name__ if window is not None else "unknown"
        label = _button_name(button)
        self._recent.append((time.monotonic(), label, context))
        # Слушатели (подсказки агента, часть 4) узнают, что оператор работает.
        self.action_recorded.emit(label)


def recent_actions(seconds: float = 300.0) -> List[Dict[str, object]]:
    """
    Последние действия оператора за N секунд (для инструмента агента).

    Читает тот же кольцевой буфер, что наполняет журнал нажатий: отдельного
    хранилища не нужно, а значения из полей формы в буфер не попадают.
    Если приложение поднято без журнала (тесты, скрипт) — возвращается
    пустой список: агент честно скажет, что действий не видел.
    """
    app = QApplication.instance()
    logger_instance = getattr(app, "_action_logger", None) if app is not None else None
    if logger_instance is None:
        return []
    try:
        return logger_instance.recent(seconds)
    except Exception as e:  # noqa: BLE001 — журнал не должен ронять агента
        logger.warning("Последние действия не прочитаны: %s", e)
        return []


def install_action_logging(app: QApplication) -> ActionLogger:
    """Устанавливается один раз после создания QApplication."""
    existing = getattr(app, "_action_logger", None)
    if existing is not None:
        return existing
    app._action_logger = ActionLogger(app)
    logger.info("Журнал нажатий кнопок включён")
    return app._action_logger
