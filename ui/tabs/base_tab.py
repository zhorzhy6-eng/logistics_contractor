#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Общие методы вкладок (Шаг 3 рефакторинга архитектуры).

Раньше каждая вкладка несла свои копии одного и того же кода:
  * _on_recognize_requested — проброс текста в MainWindow (6 копий);
  * _set_date — установка даты в QDateEdit/PasteableDateEdit (3 копии);
  * count_filled_fields — 6 копий, не использовались нигде;
  * get_all_text — 5 копий, не использовались нигде.

Первые два переехали сюда, неиспользуемые удалены.

Миксин НЕ наследует QWidget, поэтому в объявлении вкладки он идёт первым:

    class DriverTab(TabMixin, QWidget):
        recognize_requested = pyqtSignal(str)
"""

import logging
from typing import Any, Tuple

from PyQt5.QtCore import QDate

from core.dates import parse_date

logger = logging.getLogger("ui.tabs.base_tab")


class TabMixin:
    """Общее поведение вкладок: распознавание, даты и статистика заполнения."""

    def count_filled_fields(self) -> Tuple[int, int]:
        """
        Сколько полей вкладки заполнено: (заполнено, всего).

        Нужно статус-бару («Заполнено 12 из 20»). Считаем по get_data(),
        который есть у каждой вкладки: списки/словари заполнены, если непусты,
        остальные значения — если после strip() что-то осталось.

        Метод только читает данные: интерфейс не меняется (никаких setText).
        """
        try:
            data = self.get_data()
        except Exception as e:  # noqa: BLE001 — счётчик не должен ломать UI
            logger.debug(f"{type(self).__name__}: подсчёт полей не удался: {e}")
            return (0, 0)

        if not isinstance(data, dict):
            return (0, 0)

        filled = 0
        for value in data.values():
            if isinstance(value, (list, tuple, dict, set)):
                filled += 1 if value else 0
            elif str(value or "").strip():
                filled += 1

        return (filled, len(data))

    def _on_recognize_requested(self, text: str) -> None:
        """Пробрасывает текст вкладки в MainWindow для распознавания."""
        logger.debug(
            f"{type(self).__name__}: запрошено распознавание, "
            f"{len(text or '')} символов"
        )
        self.recognize_requested.emit(text)

    def _set_date(self, date_edit, value: Any) -> bool:
        """
        Устанавливает дату в QDateEdit или PasteableDateEdit.

        Понимает форматы 2023-01-26, 26.01.2023, 26/01/2023, 2023.01.26,
        26-01-2023, 20230126, 26 01 2023, а также даты со временем.

        :return: True, если дата установлена; False, если разобрать не удалось
        """
        dt = parse_date(value)
        if dt is None:
            # Значение приходит из распознанных данных: без самого текста.
            shown = (
                f"<строка, {len(value)} символов>"
                if isinstance(value, str) else repr(value)
            )
            logger.warning(
                f"{type(self).__name__}: не удалось установить дату {shown}"
            )
            return False

        date_edit.setDate(QDate(dt.year, dt.month, dt.day))
        return True
