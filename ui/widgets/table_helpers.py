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
    подсказке (обрезанный адрес иначе не прочитать);
  * ``make_table_expandable`` — политика «растягивайся по вертикали»:
    таблица точек забирает свободное место, длинный адрес переносится по
    словам, а высота строки вмещает две строки текста.

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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from PyQt5.QtCore import QObject, QPoint, QSettings, QTimer, Qt
from PyQt5.QtWidgets import QHeaderView, QMenu, QSizePolicy, QTableWidget

from ui.widgets.column_settings import (
    ColumnSpec,
    column_keys,
    load_hidden,
    make_specs,
    resolve_selection,
    save_hidden,
)

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

#: Высота строки таблицы точек: две строки текста. При шрифте интерфейса
#: одна строка занимает около 19 пикселей, а строка таблицы по умолчанию
#: (31 пиксель) вмещает только одну — второй строке перенесённого адреса
#: места уже нет, и адрес обрезается.
ROW_HEIGHT_TWO_LINES = 40

#: Свойство таблицы, под которым она помнит свой ключ QSettings.
STORAGE_KEY_PROPERTY = "columnWidthsStorageKey"

#: Свойство таблицы с описанием её колонок (для меню «Какие колонки показывать»).
COLUMN_SPECS_PROPERTY = "columnSpecs"

#: Свойство таблицы с ключом QSettings для состава колонок.
COLUMN_STORAGE_KEY_PROPERTY = "columnSettingsStorageKey"

#: Имя атрибута-признака «подсказки уже подключены».
_TOOLTIPS_FLAG = "_table_tooltips_installed"

#: Имя атрибута-признака «меню состава колонок подключено».
_COLUMN_MENU_FLAG = "_table_column_menu_installed"


def apply_minimum_widths(
    table: QTableWidget,
    minimums: Optional[Dict[Union[int, str], int]] = None,
) -> None:
    """
    Нижние границы ширин колонок — публичная обёртка над _apply_minimums.

    Нужна там, где таблица настраивается не через setup_point_table
    (например, у «Перевозимых авто» свои режимы и ширины, а минимумы —
    общие с остальными таблицами).
    """
    _apply_minimums(table, minimums)


def install_column_settings_menu(
    table: QTableWidget,
    specs: Sequence[Union[ColumnSpec, Tuple[str, str], Tuple[str, str, bool]]],
    *,
    storage_key: str = "",
    on_changed: Optional[Callable[[], None]] = None,
) -> None:
    """
    Меню «Какие колонки показывать» по правому клику на шапке таблицы.

    Как это выглядит: оператор щёлкает правой кнопкой по заголовкам —
    открывается список колонок с галочками. Снятая галочка ПРЯЧЕТ колонку
    (данные в ней остаются), состав сохраняется в QSettings и
    восстанавливается при следующем открытии окна.

    Обязательные колонки (`ColumnSpec.required`) в меню видны, но
    выключены: VIN и марку скрыть нельзя — без них строка теряет смысл.

    :param specs: описание колонок (см. `column_settings.make_specs`).
    :param storage_key: ключ QSettings для состава; пусто — не сохраняется.
    :param on_changed: что вызвать после смены состава (например, чтобы
        пересобрать делегаты и комбобоксы скрытых колонок).
    """
    resolved = make_specs(specs)
    table.setProperty(COLUMN_SPECS_PROPERTY, resolved)
    table.setProperty(COLUMN_STORAGE_KEY_PROPERTY, storage_key)

    apply_column_selection(table, load_hidden(table, storage_key, resolved))

    header = table.horizontalHeader()
    if getattr(table, _COLUMN_MENU_FLAG, False):
        return

    # Меню вешается на ШАПКУ: у таблицы политику контекстного меню занимает
    # своё (правка ячейки, вставка), и перебивать её нельзя.
    header.setContextMenuPolicy(Qt.CustomContextMenu)
    header.customContextMenuRequested.connect(
        _ColumnMenuHandler(table, on_changed).show
    )
    setattr(table, _COLUMN_MENU_FLAG, True)


def apply_column_selection(table: QTableWidget, hidden: Sequence[str]) -> None:
    """
    Прячет и показывает колонки по списку ключей.

    Колонки не удаляются: `setColumnHidden` оставляет их в модели, поэтому
    номера колонок, делегаты и сохранённая раскладка не съезжают, а
    `get_data()` продолжает читать значения скрытых колонок.
    """
    specs = table_specs(table)
    if not specs:
        return

    positions = {spec.key: index for index, spec in enumerate(specs)}
    hidden_set = set(hidden)

    for spec in specs:
        index = positions[spec.key]
        # Обязательную колонку показываем всегда: снять её галочку нельзя.
        is_hidden = spec.key in hidden_set and spec.optional
        table.setColumnHidden(index, is_hidden)


def apply_column_checks(table: QTableWidget, checked: Dict[str, bool]) -> None:
    """
    Применяет галочки меню: считает скрытые колонки и прячет их.

    Галочки, которых в словаре нет, берутся по ТЕКУЩЕМУ состоянию таблицы:
    вызов `apply_column_checks(table, {"vin": False})` меняет только VIN,
    а не прячет заодно всё остальное. Скрыть обязательную колонку нельзя —
    она остаётся видимой, даже если галочку сняли.
    """
    specs = table_specs(table)

    current = {
        spec.key: not table.isColumnHidden(index)
        for index, spec in enumerate(specs)
    }
    current.update(checked)

    applied = resolve_selection(
        specs, current, [spec.key for spec in specs if spec.required]
    )
    apply_column_selection(table, applied)


def visible_column_keys(table: QTableWidget) -> List[str]:
    """Ключи видимых колонок (скрытые не входят)."""
    return column_keys(
        table_specs(table), visible_only=True, hidden=hidden_column_keys(table)
    )


def hidden_column_keys(table: QTableWidget) -> List[str]:
    """Ключи скрытых колонок — то, что лежит в настройках."""
    return [
        spec.key
        for index, spec in enumerate(table_specs(table))
        if table.isColumnHidden(index)
    ]


def table_specs(table: QTableWidget) -> Tuple[ColumnSpec, ...]:
    """Описание колонок таблицы (пусто — таблица его не описывала)."""
    specs = table.property(COLUMN_SPECS_PROPERTY)
    return tuple(specs) if specs else ()


class _ColumnMenuHandler(QObject):
    """
    Показывает меню состава колонок и сохраняет выбор.

    Отдельный объект, а не лямбда: `lambda` в `connect` запрещены
    (цикл ссылок Python ↔ Qt, см. AGENTS.md § 5.1). Родитель — шапка
    таблицы, поэтому объект живёт ровно столько же, сколько таблица.
    """

    def __init__(self, table: QTableWidget, on_changed: Optional[Callable[[], None]]):
        super().__init__(table.horizontalHeader())
        self._table = table
        self._on_changed = on_changed

    def show(self, position: QPoint) -> None:
        """Открывает меню в точке клика по шапке."""
        table = self._table
        specs = table_specs(table)
        if not specs:
            return

        menu = QMenu(table)
        title = menu.addAction("Какие колонки показывать")
        title.setEnabled(False)
        menu.addSeparator()

        actions = {}
        for spec in specs:
            index = _column_index_by_key(table, spec.key)
            action = menu.addAction(spec.title)
            action.setCheckable(True)
            action.setChecked(index < 0 or not table.isColumnHidden(index))
            if spec.required:
                action.setEnabled(False)
                action.setToolTip("Обязательная колонка — скрыть нельзя")
            else:
                actions[spec.key] = action

        header = table.horizontalHeader()
        menu.exec_(header.mapToGlobal(position))

        checked = {key: action.isChecked() for key, action in actions.items()}
        self._apply(checked)

    def _apply(self, checked: Dict[str, bool]) -> None:
        """Прячет колонки по галочкам, пишет настройки и сообщает таблице."""
        table = self._table
        specs = table_specs(table)

        apply_column_checks(table, checked)
        save_hidden(table, table.property(COLUMN_STORAGE_KEY_PROPERTY) or "",
                    hidden_column_keys(table))

        if self._on_changed is not None:
            self._on_changed()

        logger.info(
            "Состав колонок изменён: видимых %s из %s",
            len(visible_column_keys(table)), len(specs),
        )


def _column_index_by_key(table: QTableWidget, key: str) -> int:
    """Номер колонки по её ключу; -1, если такой колонки в таблице нет."""
    for index, spec in enumerate(table_specs(table)):
        if spec.key == key:
            return index
    return -1


def setup_keyed_table(
    table: QTableWidget,
    columns_config: Sequence[Tuple[str, str, int]],
    *,
    column_index_by_key: Dict[str, int],
    storage_key: str = "",
    minimums: Optional[Dict[str, int]] = None,
) -> None:
    """
    Настраивает таблицу, у которой колонки описаны КЛЮЧАМИ, а не номерами.

    «Перевозимые авто» описывают колонки ключами полей («vin», «year»):
    те же ключи читает `get_data()`, и таблица не зависит от порядка
    колонок. Номера при этом стабильны — колонки прячутся, а не удаляются.

    :param columns_config: ``[(ключ, режим, ширина_по_умолчанию), ...]``
        (см. `setup_point_table`).
    :param column_index_by_key: ``{ключ: номер_колонки}`` — переводит ключ
        описания в номер колонки таблицы.
    :param minimums: ``{ключ: минимальная_ширина}``.
    """
    setup_point_table(
        table,
        [(column_index_by_key[column], mode, width)
         for column, mode, width in columns_config
         if column in column_index_by_key],
        storage_key=storage_key,
    )

    apply_minimum_widths(
        table,
        {column_index_by_key[key]: width
         for key, width in (minimums or {}).items()
         if key in column_index_by_key},
    )


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


def make_table_expandable(table: QTableWidget) -> None:
    """
    Даёт таблице политику «растягивайся по вертикали».

    Проблема: QTableWidget внутри QVBoxLayout по умолчанию получает
    минимум высоты, а свободное место уходит в stretch ниже. Из-за
    этого длинные адреса обрезаются, а вторая строка не видна.

    Явный SizePolicy.Expanding по вертикали говорит layout-у:
    «эта таблица хочет всё свободное место». Это то же поведение,
    что у таблицы «Перевозимые авто» (ui/tabs/vehicles_tab.py).

    Плюс:
      * setWordWrap(True) — длинный текст переносится в ячейке,
        а не обрезается многоточием;
      * высота строки по умолчанию 40px — две строки текста видны.

    Вызывается вкладками рядом с setup_point_table, а НЕ из него самого:
    через setup_point_table идут ещё таблицы справочника салонов, менеджера
    базы, импорта документов и — через setup_keyed_table — таблица
    «Перевозимые авто», у которой строка должна остаться прежней высоты
    (она эталон, см. `tests/test_ui_vehicles_tab.py`). Так же поступает
    соседний помощник `install_tooltip_on_table`.
    """
    table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    table.setWordWrap(True)
    table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT_TWO_LINES)


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
    "ROW_HEIGHT_TWO_LINES",
    "STORAGE_KEY_PROPERTY",
    "COLUMN_SPECS_PROPERTY",
    "COLUMN_STORAGE_KEY_PROPERTY",
    "SETTINGS_ORGANIZATION",
    "SETTINGS_APPLICATION",
    "WidthsSaver",
    "setup_point_table",
    "setup_keyed_table",
    "make_table_expandable",
    "install_tooltip_on_table",
    "apply_cell_tooltip",
    "save_column_widths",
    "restore_column_widths",
    "column_index",
    "stored_widths",
    # Состав колонок (ШАГ FIX-6, часть B3)
    "apply_minimum_widths",
    "install_column_settings_menu",
    "apply_column_selection",
    "apply_column_checks",
    "visible_column_keys",
    "hidden_column_keys",
    "table_specs",
]
