#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Перетаскивание СТРОК таблицы мышью — общий помощник (только интерфейс).

Зачем: в таблице «Перевозимые ТС» порядок строк — это порядок машин в
договоре, а он связан с порядком городов выгрузки. Оператору нужно менять
машины местами, не перепечатывая строки, поэтому строки тащатся мышью.

ПОЧЕМУ СВОЙ `dropEvent`, А НЕ ВСТРОЕННЫЙ `InternalMove`
    Встроенный режим `QAbstractItemView.InternalMove` переставляет только
    ЯЧЕЙКИ: строка удаляется из модели и вставляется на новом месте, а
    виджеты ячеек (`setCellWidget`: QComboBox, QSpinBox) Qt уничтожает
    вместе с удаляемой строкой. Проверено пробой: после `removeRow` обёртка
    виджета отвечает «wrapped C/C++ object of type QComboBox has been
    deleted» (та же ловушка описана в шапке `tests/test_ui_vehicle_row_reorder.py`,
    там же она и закрыта тестами). Значит, «перенести виджет руками ПОСЛЕ
    drop» нельзя — переносить уже нечего. Поэтому строка переносится целиком
    и своим кодом:

      1. ячейки снимаются `takeItem` ДО удаления строки: владение переходит
         Python, и они возвращаются на новое место как есть — вместе с
         текстом, подсказками и оформлением (жёлтый фон ненадёжного VIN не
         теряется, `refresh_vin_warnings` его только подтверждает);
      2. виджеты КОПИРУЮТСЯ, пока живы (`clone_cell_widget`): тот же тип,
         те же пункты и то же выбранное значение — поэтому «Выгрузка 2»
         остаётся «Выгрузкой 2», а не сбрасывается в «— (все)»;
      3. строка вставляется на место цели (`removeRow` + `insertRow`), а
         виджеты СОСЕДНИХ строк Qt переносит сам — они остаются живыми
         (тоже проверено пробой).

ПОРЯДОК СТРОК ПОСЛЕ ПЕРЕНОСА
    Строка `source` встаёт НА МЕСТО `target`, строки между ними сдвигаются
    на одну. Тот же закон применяется к хранилищу строк вкладки — одной
    функцией `moved_row_index` (таблица и данные не разъезжаются).

    Уменьшать `target` на единицу при переносе ВНИЗ (`source < target`) НЕ
    НУЖНО, и это не упущение: `removeRow(source)` + `insertRow(target)`
    вставляет строку в список, из которого источник УЖЕ удалён, поэтому
    сдвиг учитывается сам. Строки `[0, 1, 2, 3]`, перенос `0 → 2`: после
    удаления `[1, 2, 3]`, вставка на место 2 даёт `[1, 2, 0, 3]` — то, что
    видит оператор. `target - 1` дал бы `[1, 0, 2, 3]`, то есть сломало бы
    перенос (тест `test_move_down_target_is_not_shifted_by_hand`).

ПОЧЕМУ ПОСЛЕ ПЕРЕНОСА СНИМАЕТСЯ ВЫДЕЛЕНИЕ (главная ловушка)
    `QAbstractItemView::startDrag` после системного перетаскивания смотрит
    на его итог: если перетаскивание кончилось переносом (`Qt.MoveAction`),
    Qt зовёт `clearOrRemove()` — и удаляет ВЫДЕЛЕННЫЕ строки. Так Qt доводит
    до конца СВОЙ перенос: встроенный `dropEvent` вставляет данные на новое
    место, а исходные строки удаляются уже здесь. Наш `dropEvent` переставляет
    строку сам, поэтому то же удаление попадает по только что переехавшей
    машине: строка просто ИСЧЕЗАЕТ (проверено вживую — 5 машин → 4, без
    единой ошибки в логе; `tests/_tmp/_row_reorder_live/`).

    Поэтому `move_row` СНИМАЕТ выделение и текущую ячейку
    (`selectionModel().clear()` — снимает и то и другое): к моменту возврата
    из `startDrag` удалять Qt нечего. Выделение возвращается сразу после —
    в `startDrag`, когда цикл перетаскивания уже закрыт (`_moved_row`).
    `clearSelection()` для этого мало: она оставляет текущую ячейку.

ЧТО ПОЛУЧАЕТ ТАБЛИЦА
    * выделение строк целиком (SelectRows + SingleSelection);
    * перетаскивание только СТРОК: шапка и колонки не двигаются (C.6);
    * курсор-«рука» над строками (C.1) и «сжатая рука» при перетаскивании
      (C.2); при запрете перетаскивания курсор обычный (C.5);
    * подсветку строки-источника (C.3) и линию места вставки между
      строками (C.4);
    * сигнал `rows_moved(source, target)` — вкладка обновляет свои данные
      (хранилище строк, нумерацию).

ЗА КАКУЮ ЧАСТЬ СТРОКИ ТАЩАТ
    Строка берётся за любую СВОБОДНУЮ ячейку (VIN, марка, госномер, цвет).
    Из выпадающего списка или поля года перетаскивание не начинается: это
    обычные поля со своим кликом (раскрыть список, изменить год), и отнимать
    его нельзя — иначе оператор не смог бы выбрать точку маршрута. Поэтому
    «рука» появляется там, где строка действительно берётся.
"""

import logging
from typing import Any, List, Optional

from PyQt5.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QCursor, QPainter
from PyQt5.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QComboBox, QTableWidget, QWidget,
)

from ui import theme

logger = logging.getLogger("ui.widgets.row_reorder")

#: Толщина линии-индикатора места вставки, точки экрана (C.4).
DROP_LINE_HEIGHT = 2

#: Сколько строк нужно, чтобы перетаскивание имело смысл: одну тащить некуда.
MIN_ROWS_TO_DRAG = 2


def moved_row_index(row: int, source: int, target: int) -> int:
    """
    Куда переезжает строка `row`, когда строка `source` встаёт на `target`.

    Один закон на таблицу и на данные вкладки: строка-источник занимает
    место цели, строки между ними сдвигаются на одну. Функция чистая —
    её проверяют тесты, и ею же переставляется хранилище строк.
    """
    if row == source:
        return target
    if source < target and source < row <= target:
        return row - 1
    if target < source and target <= row < source:
        return row + 1
    return row


def clone_cell_widget(widget: Any) -> Optional[Any]:
    """
    Копия виджета ячейки: тот же тип, те же пункты и то же значение.

    Тип берётся у ИСХОДНОГО виджета (`type(widget)()`), а не базовый класс:
    вкладка узнаёт свои поля по типу (год выпуска в «Перевозимых ТС» —
    `_NoDefaultSpin`), и подмена на обычный QSpinBox сломала бы чтение года.

    Виджет не копируется, а именно создаётся заново: Qt уничтожает виджеты
    удаляемой строки, поэтому переносить приходится ЗНАЧЕНИЕ, а не объект.

    Не вышло создать копию — возвращается None и пишется ошибка в лог:
    ячейка останется пустой, но перенос строки не упадёт.
    """
    if widget is None:
        return None

    try:
        clone = type(widget)()
    except Exception as error:  # noqa: BLE001 — чужой виджет может требовать аргументов
        logger.error(
            "Виджет ячейки %s не скопирован (нужен конструктор без "
            "аргументов): %s", type(widget).__name__, error,
        )
        return None

    try:
        clone.setEnabled(widget.isEnabled())
        clone.setToolTip(widget.toolTip())
        if isinstance(widget, QComboBox) and isinstance(clone, QComboBox):
            _copy_combo(widget, clone)
        elif isinstance(widget, QAbstractSpinBox) and isinstance(clone, QAbstractSpinBox):
            _copy_spin(widget, clone)
    except Exception as error:  # noqa: BLE001 — копия важнее, но падать нельзя
        logger.error(
            "Содержимое виджета %s скопировано не полностью: %s",
            type(widget).__name__, error,
        )
    return clone


class ReorderableTable(QTableWidget):
    """
    Таблица, строки которой оператор переносит мышью.

    Перетаскивание включено сразу; выключить можно `set_row_reorder_enabled`
    (например, пока идёт распознавание — C.5).
    """

    #: Строка `source` встала на место `target`: вкладка обновляет данные.
    rows_moved = pyqtSignal(int, int)

    def __init__(self, rows: int = 0, columns: int = 0,
                 parent: Optional[QWidget] = None):
        super().__init__(rows, columns, parent)

        self._reorder_enabled = True
        self._drag_source_row: Optional[int] = None
        self._drop_gap: Optional[int] = None
        #: Куда переехала строка: выделение ставится уже ПОСЛЕ перетаскивания
        #: (см. `_restore_selection` — Qt удаляет выделенные строки).
        self._moved_row: Optional[int] = None

        # Выделяется строка целиком: тащат строку, а не ячейку.
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)

        # Колонки НЕ перетаскиваются: тащатся только строки, шапка остаётся
        # на месте (C.6). Иначе в одной шапке жили бы два перетаскивания.
        self.horizontalHeader().setSectionsMovable(False)

        # Индикатор места вставки рисует сама таблица — ровной линией между
        # строками (C.4). Встроенный индикатор Qt на курсор посреди строки
        # рисует РАМКУ вокруг ячейки, поэтому он выключен.
        self.setDropIndicatorShown(False)
        self.setDragDropOverwriteMode(False)
        self.setDefaultDropAction(Qt.MoveAction)

        # Курсор-«руку» надо менять у ВЕДУЩЕЙ области: события мыши приходят
        # ей, а не самой таблице.
        self.viewport().setMouseTracking(True)

        self.set_row_reorder_enabled(True)

    # ─────────────────────────────────────────────────────────
    # Включение и выключение переноса
    # ─────────────────────────────────────────────────────────

    def set_row_reorder_enabled(self, enabled: bool) -> None:
        """
        Разрешает или запрещает перенос строк мышью.

        Выключено — перетаскивание не начинается вовсе, а курсор остаётся
        обычным (C.5): так таблица ведёт себя, пока идёт распознавание.
        """
        self._reorder_enabled = bool(enabled)

        self.setDragEnabled(self._reorder_enabled)
        self.setAcceptDrops(self._reorder_enabled)
        self.setDragDropMode(
            QAbstractItemView.InternalMove if self._reorder_enabled
            else QAbstractItemView.NoDragDrop
        )

        if not self._reorder_enabled:
            self._drag_source_row = None
            self._drop_gap = None
            self.viewport().unsetCursor()
        self.viewport().update()

    def row_reorder_enabled(self) -> bool:
        """Разрешён ли сейчас перенос строк мышью."""
        return self._reorder_enabled

    # ─────────────────────────────────────────────────────────
    # Перенос строки
    # ─────────────────────────────────────────────────────────

    def can_move_row(self, source: int, target: int) -> bool:
        """Можно ли перенести строку `source` на место `target`."""
        if not self._reorder_enabled:
            return False

        count = self.rowCount()
        if not (0 <= source < count) or not (0 <= target < count):
            return False
        return source != target

    def move_row(self, source: int, target: int) -> bool:
        """
        Переносит строку `source` НА МЕСТО `target` — целиком.

        Возвращает True, если строки переставились. Ячейки, виджеты,
        хранилище строк и нумерация едут вместе со строкой: см. шапку
        модуля о том, почему перенос делается своим кодом.

        Выделение НЕ ставится: строка запоминается в `_moved_row`, а
        выделяет её `startDrag` — уже после того, как Qt закончит своё
        перетаскивание. Иначе Qt удалила бы выделенную (только что
        переехавшую) строку: см. шапку модуля, «Почему после переноса
        снимается выделение».
        """
        if not self.can_move_row(source, target):
            return False

        columns = self.columnCount()

        # 1. Ячейки: `takeItem` отдаёт владение Python, поэтому текст,
        #    подсказки и оформление переезжают как есть.
        items = [self.takeItem(source, column) for column in range(columns)]

        # 2. Виджеты: снимаем их СОСТОЯНИЕ, пока они живы, — строка сейчас
        #    будет удалена, и Qt уничтожит их вместе с ней.
        widgets = [
            clone_cell_widget(self.cellWidget(source, column))
            for column in range(columns)
        ]

        # 3. Строка на новое место. Виджеты соседних строк Qt переносит сам.
        #    `insertRow(target)` вставляет строку в список, из которого
        #    источник уже удалён, поэтому сдвиг цели учитывается сам:
        #    уменьшать `target` при переносе вниз не нужно.
        self.removeRow(source)
        self.insertRow(target)

        # 4. Возвращаем содержимое на новое место.
        for column, item in enumerate(items):
            if item is not None:
                self.setItem(target, column, item)
        for column, widget in enumerate(widgets):
            if widget is not None:
                self.setCellWidget(target, column, widget)

        # 5. Выделение снимается ЦЕЛИКОМ — и строки, и текущая ячейка:
        #    Qt после перетаскивания удаляет выделенные строки, а выделена
        #    была бы как раз переехавшая машина. `clearSelection()` для
        #    этого мало — она оставляет текущую ячейку.
        self._moved_row = target
        self.selectionModel().clear()

        logger.debug("Строка %s перенесена на место %s", source + 1, target + 1)
        self.rows_moved.emit(source, target)
        return True

    # ─────────────────────────────────────────────────────────
    # Где окажется строка: разрыв и место
    # ─────────────────────────────────────────────────────────

    def gap_at(self, position: QPoint) -> int:
        """
        Разрыв между строками, к которому ведёт курсор (0 … rowCount).

        Разрыв `g` — место ПЕРЕД строкой `g`; разрыв `rowCount` — после
        последней строки. Верхняя половина строки даёт разрыв над ней,
        нижняя — под ней: строка встаёт туда, где оператор видит линию.
        """
        count = self.rowCount()
        if count == 0:
            return 0

        row = self.rowAt(position.y())
        if row < 0:
            # Мимо строк: выше первой — в начало, ниже последней — в конец.
            return 0 if position.y() < self.rowViewportPosition(0) else count

        top = self.rowViewportPosition(row)
        height = self.rowHeight(row)
        if (position.y() - top) * 2 < height:
            return row
        return min(row + 1, count)

    def row_for_gap(self, gap: int, source: int) -> int:
        """
        Номер строки, НА МЕСТО которой встанет строка `source` при разрыве `gap`.

        Перенос вниз (`source < gap`) — строка встаёт на место строки ПЕРЕД
        разрывом, перенос вверх — на место строки ПОСЛЕ него. Так «перенести
        строку 1 на позицию 3» даёт ровно то, что видит оператор.
        """
        count = self.rowCount()
        if count == 0:
            return -1

        gap = max(0, min(int(gap), count))
        target = gap - 1 if source < gap else gap
        return max(0, min(target, count - 1))

    def drop_row(self, position: QPoint, source: int) -> int:
        """Куда встанет строка `source`, если отпустить мышь в точке `position`."""
        return self.row_for_gap(self.gap_at(position), source)

    # ─────────────────────────────────────────────────────────
    # События Qt: перетаскивание и перо
    # ─────────────────────────────────────────────────────────

    def startDrag(self, supported_actions: Any) -> None:
        """
        Начало перетаскивания: подсветка источника (C.3) и «сжатая рука» (C.2).

        `super().startDrag` показывает перетаскивание и возвращается только
        тогда, когда оператор отпустил мышь, поэтому состояние снимается в
        `finally` — даже если перетаскивание прервано клавишей Esc.

        Там же возвращается выделение: пока перенос идёт, выделения нет
        (иначе Qt удалила бы выделенную строку — см. шапку модуля).
        """
        source = self.currentRow()
        if (not self._reorder_enabled or source < 0
                or self.rowCount() < MIN_ROWS_TO_DRAG):
            return

        self._drag_source_row = source
        # Прошлый перенос к этому перетаскиванию не относится.
        self._moved_row = None
        self.viewport().setCursor(Qt.ClosedHandCursor)
        self.viewport().update()
        try:
            super().startDrag(supported_actions)
        finally:
            self._drag_source_row = None
            self._drop_gap = None
            self._restore_selection()
            self._update_cursor()
            self.viewport().update()

    def dragEnterEvent(self, event: Any) -> None:
        """Принимает только СВОИ строки: чужое перетаскивание таблице не нужно."""
        if self._is_internal_drag(event):
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        event.ignore()

    def dragMoveEvent(self, event: Any) -> None:
        """Ведёт линию места вставки за курсором (C.4)."""
        if not self._is_internal_drag(event):
            event.ignore()
            return

        self._drop_gap = self.gap_at(event.pos())
        self.viewport().setCursor(Qt.ClosedHandCursor)
        self.viewport().update()
        event.setDropAction(Qt.MoveAction)
        event.accept()

    def dragLeaveEvent(self, event: Any) -> None:
        """Курсор ушёл с таблицы — линия места вставки убирается."""
        self._drop_gap = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: Any) -> None:
        """
        Строка отпущена: переносим её ЦЕЛИКОМ (см. шапку модуля).

        Своё место, промах мимо строк и пустая таблица — событие не
        принимается, в таблице ничего не меняется.

        Перенос идёт под `try`: исключение здесь оставило бы таблицу
        наполовину переставленной, а оператор — без объяснения. Ошибка
        уходит в лог, строка остаётся на месте.
        """
        self._drop_gap = None
        self.viewport().update()

        if not self._reorder_enabled or not self._accepts_source(event):
            event.ignore()
            return

        source = self._drag_source_row
        if source is None:
            source = self.currentRow()
        target = self.drop_row(event.pos(), source)

        try:
            moved = self.move_row(source, target)
        except Exception:  # noqa: BLE001 — падать в обработчике перетаскивания нельзя
            logger.exception(
                "Строка %s не перенесена на место %s: перенос отменён",
                source + 1, target + 1,
            )
            # Выделение всё равно снимается: иначе Qt удалит выделенную
            # строку уже после возврата из перетаскивания.
            self._moved_row = None
            self.selectionModel().clear()
            moved = False

        if moved:
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def _restore_selection(self) -> None:
        """
        Ставит выделение на строку, которая только что переехала.

        Вызывается из `startDrag` ПОСЛЕ возврата из цикла перетаскивания:
        раньше выделять нельзя — Qt удаляет выделенные строки (шапка модуля).
        """
        row = self._moved_row
        self._moved_row = None

        if row is None or not 0 <= row < self.rowCount():
            return
        self.setCurrentCell(row, 0)

    def mouseMoveEvent(self, event: Any) -> None:
        """Курсор-«рука» над строками (C.1); обычный — вне строк и при запрете."""
        # Сначала обычная обработка: в ней Qt и начинает перетаскивание,
        # а вернётся она только после того, как мышь отпустят.
        super().mouseMoveEvent(event)
        self._update_cursor(event.pos())

    def leaveEvent(self, event: Any) -> None:
        """Мышь ушла с таблицы — курсор обычный, линия убрана."""
        if self._drag_source_row is None:
            self._drop_gap = None
            self.viewport().unsetCursor()
            self.viewport().update()
        super().leaveEvent(event)

    def paintEvent(self, event: Any) -> None:
        """Поверх таблицы: подсветка переносимой строки и линия места вставки."""
        super().paintEvent(event)

        if not self._reorder_enabled:
            return

        painter = QPainter(self.viewport())
        try:
            row = self._drag_source_row
            if row is not None and 0 <= row < self.rowCount():
                painter.fillRect(
                    self._row_rect(row), QBrush(theme.drag_source_color())
                )
            if self._drop_gap is not None:
                painter.fillRect(
                    QRect(0, self._gap_line_y(self._drop_gap) - DROP_LINE_HEIGHT // 2,
                          self.viewport().width(), DROP_LINE_HEIGHT),
                    QBrush(theme.drag_indicator_color()),
                )
        finally:
            painter.end()

    # ─────────────────────────────────────────────────────────
    # Внутреннее
    # ─────────────────────────────────────────────────────────

    def _is_internal_drag(self, event: Any) -> bool:
        """Тащит ли строку сама эта таблица."""
        if not self._reorder_enabled:
            return False

        source = event.source()
        if source is self:
            return True
        # Отправителя может не быть (проба, синтетическое событие): тогда
        # признак — начатое нами перетаскивание.
        return source is None and self._drag_source_row is not None

    def _accepts_source(self, event: Any) -> bool:
        """Не чужая ли таблица (чужое перетаскивание не применяем)."""
        source = event.source()
        return source is None or source is self

    def _update_cursor(self, position: Optional[QPoint] = None) -> None:
        """Курсор над строками — «рука», вне строк или при запрете — обычный."""
        if not self._reorder_enabled or self.rowCount() == 0:
            self.viewport().unsetCursor()
            return

        if position is None:
            position = self.viewport().mapFromGlobal(QCursor.pos())
        if self.rowAt(position.y()) >= 0:
            self.viewport().setCursor(Qt.OpenHandCursor)
        else:
            self.viewport().unsetCursor()

    def _row_rect(self, row: int) -> QRect:
        """Прямоугольник строки в координатах таблицы (для подсветки)."""
        return QRect(0, self.rowViewportPosition(row),
                     self.viewport().width(), self.rowHeight(row))

    def _gap_line_y(self, gap: int) -> int:
        """Y-координата линии-индикатора для разрыва `gap`."""
        count = self.rowCount()
        if count == 0:
            return 0

        gap = max(0, min(int(gap), count))
        if gap >= count:
            return self.rowViewportPosition(count - 1) + self.rowHeight(count - 1)
        return self.rowViewportPosition(gap)


def _copy_combo(source: QComboBox, clone: QComboBox) -> None:
    """
    Переносит пункты и выбранный пункт списка.

    Значения НЕ сбрасываются: выбранный пункт копируется по номеру, а сами
    пункты — вместе со скрытыми данными (`itemData`).
    """
    blocked = clone.blockSignals(True)
    try:
        clone.setEditable(source.isEditable())
        for index in range(source.count()):
            clone.addItem(source.itemText(index), source.itemData(index))
        clone.setCurrentIndex(source.currentIndex())
    finally:
        clone.blockSignals(blocked)


def _copy_spin(source: QAbstractSpinBox, clone: QAbstractSpinBox) -> None:
    """
    Переносит границы, прочерк и значение числового поля.

    Прочерк (`setSpecialValueText`) копируется отдельно: без него пустой
    год выглядел бы нулём, то есть «данными, которых оператор не вводил».
    """
    blocked = clone.blockSignals(True)
    try:
        clone.setRange(source.minimum(), source.maximum())
        clone.setSingleStep(source.singleStep())
        clone.setSpecialValueText(source.specialValueText())
        clone.setPrefix(source.prefix())
        clone.setSuffix(source.suffix())
        clone.setValue(source.value())
    finally:
        clone.blockSignals(blocked)


__all__: List[str] = [
    "DROP_LINE_HEIGHT",
    "MIN_ROWS_TO_DRAG",
    "ReorderableTable",
    "clone_cell_widget",
    "moved_row_index",
]
