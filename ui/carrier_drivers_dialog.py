#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог «Водители перевозчика» (ДОПОЛНЕНИЕ к шагу «Дерево перевозчиков +
двусторонняя загрузка водитель ↔ перевозчик»).

Два списка рядом — так привязку видно целиком, а не по одной записи:

    Привязанные водители        → Привязать        Доступные водители
    ← Отвязать

  * слева — водители, у которых основной перевозчик (drivers.default_carrier_id)
    уже этот;
  * справа — все остальные активные водители справочника (в том числе
    закреплённые за другими перевозчиками: при привязке прежняя активная
    связь закрывается, история работы сохраняется);
  * «→ Привязать» и «← Отвязать» работают с ВЫДЕЛЕННЫМИ строками
    (множественный выбор, ExtendedSelection), «Отвязать» спрашивает
    подтверждение;
  * поиск над списками фильтрует оба сразу по ФИО;
  * двойной клик по водителю переносит его в другой список.

Диалог не удаляет записи: отвязка очищает основного перевозчика и закрывает
активную связь, а история работы (`driver_carriers`) остаётся.

Логи — без Пдн: только ID и количества.
"""

import logging
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QVBoxLayout,
)

from db.database import (
    get_all_drivers,
    link_driver_to_carrier,
    set_default_carrier,
    unlink_driver_from_carrier,
)

from ui import theme

logger = logging.getLogger("ui.carrier_drivers_dialog")

#: Подписи кнопок (заданы здесь один раз: на них смотрят тесты).
LINK_BUTTON_TEXT = "→ Привязать"
UNLINK_BUTTON_TEXT = "← Отвязать"
CLOSE_BUTTON_TEXT = "Закрыть"

#: Заголовки списков.
LINKED_TITLE = "Привязанные водители"
AVAILABLE_TITLE = "Доступные водители"

#: Что писать вместо пустого ФИО.
DRIVER_WITHOUT_NAME = "(без ФИО)"


class CarrierDriversDialog(QDialog):
    """Привязка и отвязка водителей перевозчика двумя списками."""

    def __init__(
        self,
        carrier_id: int,
        parent=None,
        carrier_name: str = "",
    ):
        """
        :param carrier_id: перевозчик, чьих водителей показываем.
        :param carrier_name: наименование для заголовка (пусто — «ID=N»).
        """
        super().__init__(parent)

        self.carrier_id = carrier_id
        self.carrier_name = str(carrier_name or "").strip()

        #: Менялись ли привязки: по этому признаку менеджер обновляет таблицы.
        self.changed = False

        title = self.carrier_name or f"ID={self.carrier_id}"
        #: Заголовок вкладки-шапки: по нему видно, чьи это водители.
        self.header_text = f"Водители перевозчика: {title}"
        self.setWindowTitle("👤 Водители перевозчика")
        self.setMinimumSize(900, 560)

        layout = QVBoxLayout(self)

        layout.addWidget(theme.page_title(
            self.header_text,
            "💡 Слева — кто уже работает у перевозчика, справа — остальные\n"
            "💡 Отметьте несколько строк (Ctrl/Shift) и нажмите «→ Привязать»\n"
            "💡 Двойной клик по водителю переносит его в другой список\n"
            "💡 Отвязка не удаляет запись: история работы сохраняется",
        ))

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("🔍 Поиск по ФИО:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Иванов...")
        self.search_input.setClearButtonEnabled(True)
        search_layout.addWidget(self.search_input, 1)
        self.btn_reset = theme.secondary_button("Сбросить")
        self.btn_reset.clicked.connect(self.search_input.clear)
        search_layout.addWidget(self.btn_reset)
        layout.addLayout(search_layout)

        lists_layout = QHBoxLayout()

        linked_layout = QVBoxLayout()
        self.linked_label = QLabel(LINKED_TITLE)
        linked_layout.addWidget(self.linked_label)
        self.linked_list = self._make_list(
            "Водители, для которых этот перевозчик — основной"
        )
        linked_layout.addWidget(self.linked_list, 1)
        lists_layout.addLayout(linked_layout, 1)

        middle_layout = QVBoxLayout()
        middle_layout.addStretch()
        self.btn_link = theme.primary_button(
            LINK_BUTTON_TEXT,
            tooltip="Привязать выделенных водителей к перевозчику",
        )
        self.btn_link.clicked.connect(self._on_link)
        middle_layout.addWidget(self.btn_link)

        self.btn_unlink = theme.secondary_button(
            UNLINK_BUTTON_TEXT,
            tooltip="Отвязать выделенных водителей (история работы сохранится)",
        )
        self.btn_unlink.clicked.connect(self._on_unlink)
        middle_layout.addWidget(self.btn_unlink)
        middle_layout.addStretch()
        lists_layout.addLayout(middle_layout)

        available_layout = QVBoxLayout()
        self.available_label = QLabel(AVAILABLE_TITLE)
        available_layout.addWidget(self.available_label)
        self.available_list = self._make_list(
            "Остальные водители справочника (в том числе чужие: привязка "
            "закрывает прежнюю связь)"
        )
        available_layout.addWidget(self.available_list, 1)
        lists_layout.addLayout(available_layout, 1)

        layout.addLayout(lists_layout, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        self.btn_close = theme.secondary_button(CLOSE_BUTTON_TEXT)
        self.btn_close.clicked.connect(self.accept)
        footer.addWidget(self.btn_close)
        layout.addLayout(footer)

        self.search_input.textChanged.connect(self._on_search_changed)
        self.linked_list.itemDoubleClicked.connect(self._on_linked_double_clicked)
        self.available_list.itemDoubleClicked.connect(
            self._on_available_double_clicked
        )

        self.reload()

    def _make_list(self, tooltip: str) -> QListWidget:
        """Список водителей с множественным выбором."""
        widget = QListWidget()
        widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        widget.setToolTip(tooltip)
        widget.setMinimumHeight(220)
        return widget

    # ─────────────────────────────────────────────────────────
    # Данные
    # ─────────────────────────────────────────────────────────

    def reload(self) -> None:
        """
        Перечитывает оба списка.

        Привязанные — те, у кого `default_carrier_id` этого перевозчика;
        доступные — остальные активные водители. Порядок — по ФИО.
        """
        try:
            drivers = get_all_drivers()
        except Exception as e:  # noqa: BLE001 — без базы списки пусты
            logger.error(f"Список водителей недоступен: {e}")
            drivers = []

        linked: List[Dict[str, Any]] = []
        available: List[Dict[str, Any]] = []
        for driver in sorted(drivers, key=lambda d: _driver_title(d).lower()):
            if self._as_id(driver.get("default_carrier_id")) == self.carrier_id:
                linked.append(driver)
            else:
                available.append(driver)

        self._fill_list(self.linked_list, linked)
        self._fill_list(self.available_list, available)

        self.linked_label.setText(f"{LINKED_TITLE} ({len(linked)})")
        self.available_label.setText(f"{AVAILABLE_TITLE} ({len(available)})")
        self._apply_search()

        logger.info(
            f"Диалог водителей перевозчика: carrier_id={self.carrier_id}, "
            f"привязано {len(linked)}, доступно {len(available)}"
        )

    def _fill_list(
        self, widget: QListWidget, drivers: List[Dict[str, Any]]
    ) -> None:
        """Заполняет список водителями (запись — в данных строки)."""
        widget.blockSignals(True)
        widget.clear()
        for driver in drivers:
            item = QListWidgetItem(_driver_title(driver))
            item.setData(Qt.UserRole, driver)
            item.setToolTip(_driver_tooltip(driver))
            widget.addItem(item)
        widget.blockSignals(False)

    def linked_driver_ids(self) -> List[int]:
        """ID водителей в списке «Привязанные» (для тестов и диагностики)."""
        return self._driver_ids(self.linked_list)

    def available_driver_ids(self) -> List[int]:
        """ID водителей в списке «Доступные»."""
        return self._driver_ids(self.available_list)

    def visible_linked_ids(self) -> List[int]:
        """ID видимых (не отфильтрованных поиском) привязанных водителей."""
        return [
            driver_id
            for driver_id, item in zip(
                self._driver_ids(self.linked_list), self._items(self.linked_list)
            )
            if not item.isHidden()
        ]

    def visible_available_ids(self) -> List[int]:
        """ID видимых доступных водителей."""
        return [
            driver_id
            for driver_id, item in zip(
                self._driver_ids(self.available_list),
                self._items(self.available_list),
            )
            if not item.isHidden()
        ]

    def selected_linked_ids(self) -> List[int]:
        """ID выделенных в списке «Привязанные»."""
        return self._selected_ids(self.linked_list)

    def selected_available_ids(self) -> List[int]:
        """ID выделенных в списке «Доступные»."""
        return self._selected_ids(self.available_list)

    # ─────────────────────────────────────────────────────────
    # Действия
    # ─────────────────────────────────────────────────────────

    def _on_link(self) -> None:
        """«→ Привязать»: выделенные из правого списка уходят в левый."""
        items = self.available_list.selectedItems()
        if not items:
            QMessageBox.warning(
                self, "Привязка", "Выберите водителей в списке «Доступные»."
            )
            return

        done = 0
        for item in items:
            if self._link_driver(self._driver_of(item)):
                done += 1

        self.reload()
        logger.info(f"Привязано водителей к перевозчику: {done}")
        QMessageBox.information(self, "Готово", f"Привязано водителей: {done}")

    def _on_unlink(self) -> None:
        """«← Отвязать»: выделенные из левого списка уходят в правый."""
        items = self.linked_list.selectedItems()
        if not items:
            QMessageBox.warning(
                self, "Отвязка", "Выберите водителей в списке «Привязанные»."
            )
            return

        reply = QMessageBox.question(
            self,
            "Отвязка",
            f"Отвязать водителей: {len(items)}?\n\n"
            f"Записи не удаляются: водители останутся в справочнике, "
            f"история работы сохранится — привязать их можно снова.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        done = self._unlink_items(items)
        self.reload()
        QMessageBox.information(self, "Готово", f"Отвязано водителей: {done}")

    def _on_linked_double_clicked(self, item: QListWidgetItem) -> None:
        """Двойной клик слева: водитель уходит в доступные (с подтверждением)."""
        if item is None:
            return
        reply = QMessageBox.question(
            self,
            "Отвязка",
            f"Отвязать водителя от перевозчика?\n\n{_driver_title(self._driver_of(item))}\n\n"
            f"Запись не удаляется: история работы сохранится.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._unlink_items([item])
        self.reload()

    def _on_available_double_clicked(self, item: QListWidgetItem) -> None:
        """Двойной клик справа: водитель привязывается к перевозчику."""
        if item is None:
            return
        self._link_driver(self._driver_of(item))
        self.reload()

    def _link_driver(self, driver: Dict[str, Any]) -> bool:
        """
        Привязывает одного водителя.

        Сначала связь в истории (`link_driver_to_carrier` закрывает прежние
        активные связи — водитель уходит от прошлого перевозчика), затем
        основной перевозчик (`set_default_carrier` дубль не создаёт).
        """
        driver_id = self._as_id(driver.get("id"))
        if driver_id is None:
            return False

        try:
            link_driver_to_carrier(driver_id, self.carrier_id)
            ok = set_default_carrier(driver_id, self.carrier_id)
        except Exception as e:  # noqa: BLE001 — диалог не должен падать
            logger.exception(f"Ошибка привязки водителя: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось привязать водителя:\n{e}")
            return False

        if not ok:
            QMessageBox.critical(self, "Ошибка", "Не удалось привязать водителя.")
            return False

        self.changed = True
        logger.info(
            f"Водитель привязан к перевозчику: driver_id={driver_id}, "
            f"carrier_id={self.carrier_id}"
        )
        return True

    def _unlink_items(self, items: List[QListWidgetItem]) -> int:
        """Отвязывает перечисленных водителей; возвращает количество."""
        done = 0
        for item in items:
            driver = self._driver_of(item)
            driver_id = self._as_id(driver.get("id"))
            if driver_id is None:
                continue
            try:
                ok = set_default_carrier(driver_id, None)
                # Активную связь закрываем: водитель здесь больше не работает.
                unlink_driver_from_carrier(driver_id, self.carrier_id)
            except Exception as e:  # noqa: BLE001 — диалог не должен падать
                logger.exception(f"Ошибка отвязки водителя: {e}")
                QMessageBox.critical(
                    self, "Ошибка", f"Не удалось отвязать водителя:\n{e}"
                )
                continue

            if ok:
                done += 1
                self.changed = True
                logger.info(
                    f"Водитель отвязан от перевозчика: driver_id={driver_id}, "
                    f"carrier_id={self.carrier_id}"
                )
        return done

    # ─────────────────────────────────────────────────────────
    # Поиск
    # ─────────────────────────────────────────────────────────

    def _on_search_changed(self, text: str) -> None:
        """Поиск по ФИО: фильтрует оба списка сразу."""
        self._apply_search()

    def _apply_search(self) -> None:
        """
        Прячет несовпавшие строки в обоих списках.

        Выделение сбрасывается: иначе «Привязать»/«Отвязать» сработали бы по
        строке, которой оператор уже не видит.
        """
        needle = (self.search_input.text() or "").strip().lower()

        for widget in (self.linked_list, self.available_list):
            for item in self._items(widget):
                driver = self._driver_of(item)
                item.setHidden(bool(needle) and needle not in _driver_title(driver).lower())
            if needle:
                widget.clearSelection()

    # ─────────────────────────────────────────────────────────
    # Мелочи
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _items(widget: QListWidget) -> List[QListWidgetItem]:
        return [widget.item(index) for index in range(widget.count())]

    @staticmethod
    def _driver_of(item: Optional[QListWidgetItem]) -> Dict[str, Any]:
        """Запись водителя из данных строки (пусто — не строка)."""
        record = item.data(Qt.UserRole) if item is not None else None
        return record if isinstance(record, dict) else {}

    def _driver_ids(self, widget: QListWidget) -> List[int]:
        ids = []
        for item in self._items(widget):
            driver_id = self._as_id(self._driver_of(item).get("id"))
            if driver_id is not None:
                ids.append(driver_id)
        return ids

    def _selected_ids(self, widget: QListWidget) -> List[int]:
        ids = []
        for item in widget.selectedItems():
            driver_id = self._as_id(self._driver_of(item).get("id"))
            if driver_id is not None:
                ids.append(driver_id)
        return ids

    @staticmethod
    def _as_id(value: Any) -> Optional[int]:
        """ID записи числом; пустое и мусор — None."""
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


def _driver_title(driver: Optional[Dict[str, Any]]) -> str:
    """ФИО водителя (пусто — «(без ФИО)»)."""
    driver = driver if isinstance(driver, dict) else {}
    return str(driver.get("full_name") or "").strip() or DRIVER_WITHOUT_NAME


def _driver_tooltip(driver: Dict[str, Any]) -> str:
    """Подсказка строки: паспорт и телефон, если они есть."""
    parts = []
    passport = (
        f"{driver.get('passport_series') or ''} "
        f"{driver.get('passport_number') or ''}"
    ).strip()
    if passport:
        parts.append(f"Паспорт: {passport}")
    if driver.get("phone"):
        parts.append(f"Телефон: {driver['phone']}")
    return "\n".join(parts)


__all__ = [
    "AVAILABLE_TITLE",
    "CLOSE_BUTTON_TEXT",
    "DRIVER_WITHOUT_NAME",
    "LINKED_TITLE",
    "LINK_BUTTON_TEXT",
    "UNLINK_BUTTON_TEXT",
    "CarrierDriversDialog",
]
