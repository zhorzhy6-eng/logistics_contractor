# -*- coding: utf-8 -*-
"""
Закрытие окна Экспедиторства: скрытие крестиком и реальный выход.

Этап 6 (16.10.2026), подэтап 1.L: сюда переехали из прежнего
`ui/main_window.py` обработчик закрытия (ЭТАП 2B) и метод реального выхода.
Логика не менялась — только переезд.

Два разных закрытия — это важно:

  * `closeEvent` (крестик) — окно ПРЯЧЕТСЯ: окна типов живут всё время
    работы программы, и закрытие не должно уничтожать данные в формах;
  * `force_close()` — настоящее закрытие: вызывается `WindowManager.close_all()`
    при выходе из программы.

ПОРЯДОК В `force_close` МЕНЯТЬ НЕЛЬЗЯ. Пул распознавания — Qt-ребёнок окна,
и его разрушение с работающей задачей (запрос к GigaChat) роняет процесс:
сначала отменяем задачу, потом останавливаем Максимилиана, закрываем
плавающего кота, сохраняем раскладку колонок и только затем ждём потоки.
"""

import logging

from ui.qt_shutdown import (
    cancel_running_tasks,
    wait_for_thread_pools,
    wait_for_threads,
)
from ui.widgets.table_helpers import save_all_column_widths

# Имя логгера задано ЯВНО (а не __name__): после переезда в пакет
# `ui.main_window.close` прежние записи выпали бы из настройки
# `config/logging_config.py` (APP_LOGGERS = "core", "ui", "db", ...).
logger = logging.getLogger("ui.main_window")


class CloseMixin:
    """
    Закрытие окна: скрытие по крестику и реальный выход из программы.

    Своего `__init__` у миксина нет: флаг `self._force_close` появляется
    только в `force_close`, а `self.agent_widget` создаёт Максимилиан —
    оба ищутся через `getattr`.
    """

    def closeEvent(self, event):
        """
        Закрытие окна = скрытие (ЭТАП 2B).

        Окна типов живут всё время приложения: закрытие крестиком не должно
        уничтожать данные в формах. Реальное завершение — через
        ui/windows/manager.py (close_all() вызывает force_close()).

        Плавающий кот при этом прячется ВМЕСТЕ с окном (баг #18): он окно
        верхнего уровня, и без явного закрытия оставался на экране даже
        после того, как программа выключилась.
        """
        if getattr(self, "_force_close", False):
            event.accept()
            return

        self._close_floating_cat()
        self._save_all_column_widths()
        logger.info("Окно скрыто (закрытие крестиком данные не теряет)")
        event.ignore()
        self.hide()

    def _close_floating_cat(self) -> None:
        """Закрывает плавающего кота Максимилиана (баг #18)."""
        agent_widget = getattr(self, "agent_widget", None)
        if agent_widget is None:
            return
        try:
            agent_widget.close_floating()
        except Exception as e:  # noqa: BLE001 — кот не должен мешать закрытию
            logger.warning("Плавающий кот не закрыт: %s", e)

    def _save_all_column_widths(self) -> None:
        """
        Записывает раскладку всех таблиц окна (часть D ночного прогона).

        Своя автозапись у таблиц отложена на полсекунды: если оператор
        растянул колонку и сразу закрыл программу, таймер не срабатывает, и
        при следующем запуске ширина сбрасывалась. На закрытии окна
        раскладка пишется явно — таблицы ищутся рекурсивно, обходить вкладки
        не нужно.
        """
        try:
            saved = save_all_column_widths(self)
            logger.debug("Раскладка колонок окна сохранена: таблиц %s", saved)
        except Exception as e:  # noqa: BLE001 — раскладка не важнее закрытия
            logger.warning("Раскладка колонок не сохранена: %s", e)

    def force_close(self) -> None:
        """Закрывает окно по-настоящему: выход из приложения, не скрытие."""
        # Сначала свои рабочие потоки: пул распознавания — Qt-ребёнок окна, и
        # его разрушение с работающей задачей (GigaChat) роняет процесс
        # (access violation при выходе). Отменяем текущее распознавание, чтобы
        # ожидание было коротким и результат уже не применялся.
        cancel_running_tasks(self, "recognition_task")

        # Максимилиан отпускает свои ресурсы ДО закрытия окна: живой
        # QShortcut и пул агента, оставленные сборщику мусора, роняют
        # процесс при завершении (см. ui/agent_widget.py::shutdown).
        agent_widget = getattr(self, "agent_widget", None)
        if agent_widget is not None:
            agent_widget.shutdown()

        # Кот закрывается отдельно и ЯВНО: shutdown() тоже это делает, но
        # порядок «сначала кот, потом всё остальное» надёжнее — окно
        # верхнего уровня не должно пережить программу (баг #18).
        self._close_floating_cat()

        # Раскладка колонок — на выходе, ДО закрытия окна: таблицы ещё живы,
        # а отложенная автозапись может не успеть (часть D ночного прогона).
        self._save_all_column_widths()

        wait_for_thread_pools(self)
        # Потоки (не пулы) ждут отдельно: поток запроса к модели у
        # Максимилиана вне пула, и `waitForDone` его не касается —
        # см. ui/qt_shutdown.py::wait_for_threads. К этому месту
        # agent_widget.shutdown() уже остановил его; ожидание подтверждает,
        # что поток встал, и ловит чужие потоки, если они появятся.
        wait_for_threads(self)

        self._force_close = True
        self.close()
