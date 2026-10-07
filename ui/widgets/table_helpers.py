#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Настройка таблиц: режимы колонок, минимальные ширины, раскладка, подсказки.

ШАГ FIX-5. Таблиц точек маршрута в проекте шесть (погрузки и выгрузки
Экспедиторства, адреса погрузки и грузополучатели Логистикса, точки
погрузки и выгрузки аренды ТС) плюс таблица справочника салонов.
Настраивать у каждой из них QHeaderView вручную — шесть мест, где ширины
расходятся; поэтому настройка живёт здесь, а вкладка описывает только
СВОИ колонки.

Что умеет модуль:

  * ``setup_point_table`` — режимы колонок («растянуть» / «по содержимому» /
    «фиксированная») и минимальные ширины по описанию колонок;
  * ``save_column_widths`` / ``restore_column_widths`` — раскладка колонок
    в QSettings: оператор растянул границу — после перезапуска она та же;
  * ``install_tooltip_on_table`` — полный текст ячейки во всплывающей
    подсказке (обрезанный адрес иначе не прочитать).

Ключ ``storage_key`` включает сохранение: таблица помнит свои ширины
(запись идёт с паузой после того, как оператор отпустил границу) и
восстанавливает их при следующем создании. Пустой ключ — таблица
настраивается, но нигде не сохраняется.

QSettings в проекте больше нигде не используется, поэтому ключи этого
модуля начинаются с «ui/» и принадлежат только интерфейсу. Хранилище —
INI-файл в папке настроек пользователя (формат задан явно): раскладка
колонок не лезет в реестр Windows, её видно и можно удалить, а тесты
перенаправляют её одним `QSettings.setPath` (см. `tests/conftest.py`).
Организация и приложение заданы явно: main.py их не выставляет, а
раскладка должна лежать в одном месте независимо от того, откуда создана
таблица.
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from PyQt5.QtCore import QObject, QSettings, QTimer
from PyQt5.QtWidgets import QHeaderView, QTableWidget

logger = logging.getLogger("ui.widgets.table_helpers")

#: Организация и приложение для QSettings (main.py их не задаёт).
SETTINGS_ORGANIZATION = "logistics_contractor"
SETTINGS_APPLICATION = "logistics_contractor"

#: Режим колонки: тянется по ширине таблицы (адрес).
MODE_STRETCH = "stretch"
#: Режим колонки: ширина по содержимому (наименование, код, город).
MODE_CONTENTS = "contents"
#: Режим колонки: ширина задана, оператор может её менять (дата, время).
MODE_FIXED = "fixed"

#: Режимы колонок → режимы QHeaderView.
RESIZE_MODES: Dict[str, Any] = {
    MODE_STRETCH: QHeaderView.Stretch,
    MODE_CONTENTS: QHeaderView.ResizeToContents,
    MODE_FIXED: QHeaderView.Interactive,
}

#: Пауза перед записью ширин: оператор тянет границу мыши, и сохранять
#: размер на каждый пиксель незачем.
AUTOSAVE_DELAY_MS = 500

#: Свойство таблицы, под которым она помнит свой ключ QSettings.
STORAGE_KEY_PROPERTY = "columnWidthsStorageKey"

#: Имя атрибута-признака «подсказки уже подключены».
_TOOLTIPS_FLAG = "_table_tooltips_installed"


def setup_point_table(
    table: QTableWidget,
    columns_config: Sequence[Tuple[Union[int, str], str, int]],
    *,
    storage_key: str = "",
    minimums: Optional[Dict[Union[int, str], int]] = None,
) -> None:
    """
    Настраивает таблицу: режимы колонок, минимальные ширины, сохранение.

    :param table: таблица (QTableWidget).
    :param columns_config: описание колонок — список кортежей
        ``(колонка, режим, ширина_по_умолчанию)``, где режим — одна из
        констант MODE_STRETCH / MODE_CONTENTS / MODE_FIXED:

          * ``stretch``  — QHeaderView.Stretch (тянется по ширине таблицы);
          * ``contents`` — QHeaderView.ResizeToContents (по содержимому);
          * ``fixed``    — QHeaderView.Interactive + resizeSection
            (ширина задана, оператор может её менять).

        Колонка задаётся номером (0, 1, …) или заголовком («Адрес»):
        заголовок ищется по тексту шапки, поэтому описание читается
        как таблица колонок бланка.
    :param storage_key: ключ QSettings для сохранения ширин.
        Пусто — раскладка не сохраняется.
    :param minimums: ``{колонка: минимальная_ширина}`` — нижняя граница
        ширины: применяется через setMinimumSectionSize (общий минимум для
        ручного перетаскивания) и resizeSection (пол для конкретной
        колонки). Уже сохранённую ширину минимум не уменьшает, а слишком
        узкую — поднимает.
    """
    header = table.horizontalHeader()

    for column, mode, width in columns_config:
        index = column_index(table, column)
        if index < 0:
            logger.warning(
                "setup_point_table: колонка %r не найдена (колонок %s)",
                column, table.columnCount(),
            )
            continue

        resize_mode = RESIZE_MODES.get(str(mode).strip().lower())
        if resize_mode is None:
            logger.warning(
                "setup_point_table: неизвестный режим колонки %r (%r)",
                mode, column,
            )
            continue

        header.setSectionResizeMode(index, resize_mode)
        if resize_mode == QHeaderView.Interactive and int(width) > 0:
            header.resizeSection(index, int(width))

    if storage_key:
        table.setProperty(STORAGE_KEY_PROPERTY, storage_key)
        restore_column_widths(table, storage_key)
        _install_widths_autosave(table, storage_key)

    _apply_minimums(table, minimums)


def install_tooltip_on_table(table: QTableWidget) -> None:
    """
    Подсказка на ячейки таблицы: при наведении видно полный текст.

    Ячейка таблицы текст не переносит и не показывает целиком — длинный
    адрес обрезается. Подсказка ставится в момент наведения
    (itemEntered + setMouseTracking(True)), а не при заполнении: строки
    таблиц пересоздаются при каждой перерисовке.

    Своя подсказка ячейки (например, «справочно» у даты выгрузки аренды)
    НЕ затирается — она важнее повтора текста.

    Вызывается один раз при инициализации таблицы: повторный вызов
    ничего не делает.
    """
    table.setMouseTracking(True)
    if getattr(table, _TOOLTIPS_FLAG, False):
        return

    table.itemEntered.connect(apply_cell_tooltip)
    setattr(table, _TOOLTIPS_FLAG, True)


def apply_cell_tooltip(item: Any) -> None:
    """
    Ставит подсказку ячейки по её тексту (слот itemEntered).

    Пустая ячейка подсказки не получает: повторять нечего.
    """
    if item is None:
        return
    if item.toolTip():
        return

    text = str(item.text() or "").strip()
    if text:
        item.setToolTip(text)


def save_column_widths(table: QTableWidget, storage_key: str) -> None:
    """Сохраняет ширины колонок в QSettings (список по номерам колонок)."""
    if not storage_key:
        return

    header = table.horizontalHeader()
    widths = [int(header.sectionSize(column)) for column in range(table.columnCount())]
    settings = _settings()
    settings.setValue(storage_key, widths)
    settings.sync()
    logger.debug(
        "Раскладка колонок сохранена: ключ=%s, колонок=%s", storage_key, len(widths)
    )


def restore_column_widths(table: QTableWidget, storage_key: str) -> None:
    """
    Восстанавливает ширины колонок из QSettings, если они там есть.

    Ничего не сохранено (или значение битое) — таблица остаётся с ширинами
    по умолчанию: молча, без предупреждений. Ширины колонок с режимом
    Stretch / ResizeToContents Qt всё равно считает сам — для них
    восстановление ничего не меняет.
    """
    if not storage_key:
        return

    stored = _settings().value(storage_key)
    if not stored:
        return

    if not isinstance(stored, (list, tuple)):
        stored = [stored]

    header = table.horizontalHeader()
    restored = 0
    for column, value in enumerate(list(stored)[:table.columnCount()]):
        try:
            width = int(value)
        except (TypeError, ValueError):
            logger.warning(
                "Раскладка колонок: ширина %r не разобрана (ключ=%s)",
                value, storage_key,
            )
            continue
        if width > 0:
            header.resizeSection(column, width)
            restored += 1

    if restored:
        logger.debug(
            "Раскладка колонок восстановлена: ключ=%s, колонок=%s",
            storage_key, restored,
        )


def column_index(table: QTableWidget, column: Union[int, str]) -> int:
    """
    Номер колонки по номеру или по заголовку.

    Заголовок («Адрес») ищется по тексту шапки; не найден — -1.
    """
    if isinstance(column, int) and not isinstance(column, bool):
        return column if 0 <= column < table.columnCount() else -1

    title = str(column)
    for index in range(table.columnCount()):
        item = table.horizontalHeaderItem(index)
        if item is not None and item.text() == title:
            return index
    return -1


def stored_widths(table: QTableWidget, storage_key: str) -> List[int]:
    """
    Сохранённые ширины колонок (для тестов и диагностики).

    Пусто — ничего не сохранено.
    """
    if not storage_key:
        return []

    stored = _settings().value(storage_key)
    if not stored:
        return []
    if not isinstance(stored, (list, tuple)):
        stored = [stored]

    widths: List[int] = []
    for value in stored:
        try:
            widths.append(int(value))
        except (TypeError, ValueError):
            continue
    return widths


# ─────────────────────────────────────────────────────────────
# Внутреннее
# ─────────────────────────────────────────────────────────────

def _apply_minimums(
    table: QTableWidget,
    minimums: Optional[Dict[Union[int, str], int]],
) -> None:
    """
    Нижние границы ширин колонок.

    setMinimumSectionSize задаёт общий минимум — ниже него Qt не даёт
    сжать НИ ОДНУ колонку, и именно он страхует шапку от «Дат» и «Вре».
    resizeSection поднимает до минимума конкретную колонку.

    Важно: персональный минимум держится только у колонок с режимом
    Interactive (даты и время). У растягиваемых (stretch) и считающихся по
    содержимому (contents) колонок ширину пересчитывает сам Qt, поэтому
    там работает общий минимум, а не персональный: ширина stretch-колонки —
    это остаток места в таблице.
    """
    if not minimums:
        return

    header = table.horizontalHeader()
    floors: List[Tuple[int, int]] = []
    for column, min_width in minimums.items():
        index = column_index(table, column)
        if index < 0:
            logger.warning(
                "Минимальная ширина: колонка %r не найдена (колонок %s)",
                column, table.columnCount(),
            )
            continue
        width = int(min_width)
        if width > 0:
            floors.append((index, width))

    if not floors:
        return

    header.setMinimumSectionSize(min(width for _index, width in floors))
    for index, width in floors:
        if header.sectionSize(index) < width:
            header.resizeSection(index, width)


def _settings() -> QSettings:
    """
    Хранилище настроек интерфейса.

    Формат задан ЯВНО (INI, настройки пользователя): конструктор
    `QSettings(организация, приложение)` берёт формат платформы, то есть
    реестр Windows — его не перенаправить в тестах, и раскладка из прогона
    попала бы в рабочие настройки оператора.
    """
    return QSettings(
        QSettings.IniFormat,
        QSettings.UserScope,
        SETTINGS_ORGANIZATION,
        SETTINGS_APPLICATION,
    )


def _install_widths_autosave(table: QTableWidget, storage_key: str) -> None:
    """
    Включает сохранение ширин при изменении.

    Окна типов не закрываются крестиком, а прячутся (см.
    ui/windows/base_window.py), поэтому запись «на закрытии окна» до
    реального выхода не дошла бы. Сохраняем по факту изменения ширины,
    с паузой: оператор тянет границу, а не печатает.
    """
    if getattr(table, "_widths_saver", None) is not None:
        return
    table._widths_saver = WidthsSaver(table, storage_key)


class WidthsSaver(QObject):
    """
    Сохраняет ширины колонок таблицы после правки раскладки.

    Родитель — сама таблица: объект живёт ровно столько же, отдельной
    ссылки на таблицу не держит (берёт её у Qt), поэтому цикла
    Python ↔ Qt здесь не возникает.
    """

    def __init__(self, table: QTableWidget, storage_key: str):
        super().__init__(table)
        self._storage_key = storage_key

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(AUTOSAVE_DELAY_MS)
        self._timer.timeout.connect(self.flush)

        table.horizontalHeader().sectionResized.connect(self.schedule)

    def schedule(self, *_args: Any) -> None:
        """Откладывает запись: раскладку правят перетаскиванием."""
        self._timer.start()

    def flush(self) -> None:
        """Записывает текущие ширины колонок в QSettings."""
        table = self.parent()
        if isinstance(table, QTableWidget):
            save_column_widths(table, self._storage_key)


__all__ = [
    "MODE_STRETCH",
    "MODE_CONTENTS",
    "MODE_FIXED",
    "RESIZE_MODES",
    "AUTOSAVE_DELAY_MS",
    "STORAGE_KEY_PROPERTY",
    "SETTINGS_ORGANIZATION",
    "SETTINGS_APPLICATION",
    "WidthsSaver",
    "setup_point_table",
    "install_tooltip_on_table",
    "apply_cell_tooltip",
    "save_column_widths",
    "restore_column_widths",
    "column_index",
    "stored_widths",
]
