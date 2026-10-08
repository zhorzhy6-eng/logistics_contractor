#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог «Перевозчик водителя» (ДОПОЛНЕНИЕ к шагу «Дерево перевозчиков +
двусторонняя загрузка водитель ↔ перевозчик»).

Открывается с вкладки «Водители» менеджера базы по кнопке «🚛 Перевозчик…».
Что внутри:

  * список ВСЕХ перевозчиков справочника; текущий перевозчик водителя
    выделен и помечен галочкой, внизу подпись «Текущий перевозчик: …»;
  * поле поиска по наименованию и ИНН (список сужается на месте);
  * «✓ Привязать выбранного» — пишет `drivers.default_carrier_id` и заводит
    связь в истории (`driver_carriers`), закрывая прежнюю активную связь;
  * «✗ Отвязать» — с подтверждением: очищает привязку и закрывает активную
    связь; записи истории НЕ удаляются (мягкая логика проекта);
  * «📂 Загрузить в форму» — отдаёт выбранного перевозчика обработчику
    и закрывает диалог (в менеджере базы это загрузка вкладки
    «Перевозчик»);
  * «Закрыть» — просто закрыть.

Кнопка не трогает вкладку «Водитель»: она про привязку к справочнику, а не
про данные водителя в форме.

Логи — без Пдн: только ID и количества.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QVBoxLayout,
)

from db.database import (
    get_all_organizations,
    link_driver_to_carrier,
    load_organization,
    set_default_carrier,
    unlink_driver_from_carrier,
)

from ui import theme

logger = logging.getLogger("ui.driver_carrier_dialog")

#: Кнопки диалога (подписи заданы здесь один раз: на них смотрят тесты).
BIND_BUTTON_TEXT = "✓ Привязать выбранного"
UNBIND_BUTTON_TEXT = "✗ Отвязать"
LOAD_BUTTON_TEXT = "📂 Загрузить в форму"
CLOSE_BUTTON_TEXT = "Закрыть"

#: Метка текущей привязки в списке перевозчиков.
CURRENT_MARK = "✓ "

#: Что писать в подписи, когда перевозчика у водителя нет.
NO_CARRIER_TITLE = "— не указан —"


class DriverCarrierDialog(QDialog):
    """Привязка водителя к перевозчику из справочника."""

    def __init__(
        self,
        driver: Dict[str, Any],
        parent=None,
        on_load_carrier: Optional[Callable] = None,
        driver_name: str = "",
    ):
        """
        :param driver: запись водителя (нужен `id` и `default_carrier_id`).
        :param on_load_carrier: обработчик «📂 Загрузить в форму» — получает
            запись перевозчика из справочника (в менеджере базы это
            заполнение вкладки «Перевозчик»).
        :param driver_name: ФИО для заголовка (пусто — берётся из записи).
        """
        super().__init__(parent)

        driver = driver if isinstance(driver, dict) else {}
        self.driver = dict(driver)
        self.driver_id = self.driver.get("id")
        self.driver_name = str(driver_name or self.driver.get("full_name") or "").strip()
        self.on_load_carrier = on_load_carrier

        #: Менялась ли привязка: по этому признаку менеджер обновляет таблицы.
        self.changed = False

        self.setWindowTitle("🚛 Перевозчик водителя")
        self.setMinimumSize(620, 560)

        layout = QVBoxLayout(self)

        layout.addWidget(theme.page_title(
            "Перевозчик водителя",
            "💡 Текущий перевозчик отмечен галочкой и уже выделен\n"
            "💡 «✓ Привязать выбранного» — водитель переходит к перевозчику;\n"
            "   прежняя связь закрывается, история работы сохраняется\n"
            "💡 «✗ Отвязать» — водитель остаётся без основного перевозчика",
        ))

        if self.driver_name:
            self.driver_label = QLabel(f"Водитель: {self.driver_name}")
        else:
            self.driver_label = QLabel("Водитель: (без ФИО)")
        layout.addWidget(self.driver_label)

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("🔍 Поиск:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Наименование или ИНН перевозчика...")
        self.search_input.setClearButtonEnabled(True)
        search_layout.addWidget(self.search_input, 1)
        self.btn_reset = theme.secondary_button("Сбросить")
        self.btn_reset.clicked.connect(self.search_input.clear)
        search_layout.addWidget(self.btn_reset)
        layout.addLayout(search_layout)

        self.carriers_list = QListWidget()
        self.carriers_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.carriers_list.setMinimumHeight(240)
        self.carriers_list.setToolTip(
            "Справочник перевозчиков. Текущий перевозчик водителя помечен «✓»."
        )
        layout.addWidget(self.carriers_list, 1)

        self.current_label = QLabel("")
        self.current_label.setWordWrap(True)
        layout.addWidget(self.current_label)

        buttons = QHBoxLayout()
        self.btn_bind = theme.primary_button(
            BIND_BUTTON_TEXT,
            tooltip="Записать выбранного перевозчика основным для водителя",
        )
        self.btn_bind.clicked.connect(self._on_bind)
        buttons.addWidget(self.btn_bind)

        self.btn_unbind = theme.secondary_button(
            UNBIND_BUTTON_TEXT,
            tooltip="Очистить основного перевозчика (история работы сохранится)",
        )
        self.btn_unbind.clicked.connect(self._on_unbind)
        buttons.addWidget(self.btn_unbind)

        self.btn_load = theme.secondary_button(
            LOAD_BUTTON_TEXT,
            tooltip="Заполнить вкладку «Перевозчик» выбранной записью",
        )
        self.btn_load.clicked.connect(self._on_load_to_form)
        buttons.addWidget(self.btn_load)

        buttons.addStretch()

        self.btn_close = theme.secondary_button(CLOSE_BUTTON_TEXT)
        self.btn_close.clicked.connect(self.accept)
        buttons.addWidget(self.btn_close)

        layout.addLayout(buttons)

        self.search_input.textChanged.connect(self._on_search_changed)
        self.carriers_list.itemDoubleClicked.connect(self._on_item_double_clicked)

        self.reload()

    # ─────────────────────────────────────────────────────────
    # Данные
    # ─────────────────────────────────────────────────────────

    def current_carrier_id(self) -> Optional[int]:
        """ID текущего перевозчика водителя (None — привязки нет)."""
        return self._as_id(self.driver.get("default_carrier_id"))

    def reload(self) -> None:
        """
        Перечитывает справочник перевозчиков и текущую привязку.

        Список идёт по алфавиту; текущий перевозчик отмечен «✓» и выделен.
        Мягко удалённого перевозчика в списке нет (списки удалённых
        скрывают) — тогда показывает только подпись, а «📂 Загрузить
        в форму» его всё равно отдаст: запись читается по ID.
        """
        try:
            carriers = get_all_organizations(is_carrier=True)
        except Exception as e:  # noqa: BLE001 — без справочника список пуст
            logger.error(f"Справочник перевозчиков недоступен: {e}")
            carriers = []

        current_id = self.current_carrier_id()

        # Отключаем сигналы, пока заполняем: выделение строки не должно
        # ничего пересчитывать.
        self.carriers_list.blockSignals(True)
        self.carriers_list.clear()

        needle = (self.search_input.text() or "").strip().lower()
        shown = 0
        for org in carriers:
            title = _carrier_title(org)
            if not title:
                continue
            is_current = self._as_id(org.get("id")) == current_id

            item = QListWidgetItem(
                f"{CURRENT_MARK if is_current else ''}{title}"
            )
            item.setData(Qt.UserRole, org)
            item.setToolTip(_carrier_tooltip(org, is_current=is_current))
            self.carriers_list.addItem(item)
            shown += 1

            if is_current:
                item.setSelected(True)
                self.carriers_list.setCurrentItem(item)

            if needle and not _matches(org, needle):
                item.setHidden(True)

        self.carriers_list.blockSignals(False)

        self._refresh_current_label()
        logger.info(
            f"Диалог перевозчика водителя: перевозчиков {shown}, "
            f"привязка {'есть' if current_id else 'нет'}"
        )

    def _refresh_current_label(self) -> None:
        """Подпись под списком: кто у водителя сейчас."""
        current_id = self.current_carrier_id()
        if not current_id:
            self.current_label.setText(f"Текущий перевозчик: {NO_CARRIER_TITLE}")
            self.btn_unbind.setEnabled(False)
            return

        self.btn_unbind.setEnabled(True)
        try:
            record = load_organization(current_id, is_carrier=True)
        except Exception as e:  # noqa: BLE001 — подпись не должна ломать диалог
            logger.warning(f"Перевозчик водителя не прочитан: {e}")
            record = None

        if not record:
            self.current_label.setText(
                f"Текущий перевозчик: ID={current_id} (запись не найдена)"
            )
            return

        title = _carrier_title(record) or f"ID={current_id}"
        suffix = " (удалён из справочника)" if record.get("is_deleted") else ""
        self.current_label.setText(f"Текущий перевозчик: {title}{suffix}")

    def selected_carrier_id(self) -> Optional[int]:
        """ID выбранного в списке перевозчика (учитывая «✓»-пометку)."""
        item = self.carriers_list.currentItem()
        if item is None:
            selected = self.carriers_list.selectedItems()
            item = selected[0] if selected else None
        if item is None:
            return None
        record = item.data(Qt.UserRole)
        return self._as_id(record.get("id")) if isinstance(record, dict) else None

    def visible_carrier_ids(self) -> List[int]:
        """ID перевозчиков, видимых в списке (для тестов и диагностики)."""
        result = []
        for index in range(self.carriers_list.count()):
            item = self.carriers_list.item(index)
            if item.isHidden():
                continue
            record = item.data(Qt.UserRole)
            if isinstance(record, dict):
                result.append(self._as_id(record.get("id")))
        return result

    # ─────────────────────────────────────────────────────────
    # Действия
    # ─────────────────────────────────────────────────────────

    def _on_bind(self) -> None:
        """«✓ Привязать выбранного»: водитель переходит к перевозчику."""
        carrier_id = self.selected_carrier_id()
        if carrier_id is None:
            QMessageBox.warning(self, "Привязка", "Выберите перевозчика из списка.")
            return

        if not self.driver_id:
            QMessageBox.warning(
                self, "Привязка",
                "Водитель ещё не сохранён в справочнике — привязывать некому.",
            )
            return

        self._bind_carrier(carrier_id)
        title = self._carrier_title_by_id(carrier_id)
        QMessageBox.information(
            self, "Готово",
            f"Водитель привязан к перевозчику.\n\n{title}" if title
            else "Водитель привязан к перевозчику.",
        )

    def _bind_carrier(self, carrier_id: int) -> bool:
        """
        Пишет привязку: сначала связь в истории, затем основного перевозчика.

        Порядок важен: `link_driver_to_carrier` закрывает прежние активные
        связи (водитель ушёл от прошлого перевозчика), а `set_default_carrier`
        видит готовую связь и дубль не создаёт.
        """
        try:
            link_driver_to_carrier(self.driver_id, carrier_id)
            ok = set_default_carrier(self.driver_id, carrier_id)
        except Exception as e:  # noqa: BLE001 — диалог не должен падать
            logger.exception(f"Ошибка привязки водителя: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось привязать водителя:\n{e}")
            return False

        if not ok:
            QMessageBox.critical(self, "Ошибка", "Не удалось привязать водителя.")
            return False

        self.changed = True
        self.driver["default_carrier_id"] = carrier_id
        logger.info(
            f"Водитель привязан к перевозчику: driver_id={self.driver_id}, "
            f"carrier_id={carrier_id}"
        )
        self.reload()
        return True

    def _on_unbind(self) -> None:
        """«✗ Отвязать»: убирает основного перевозчика (с подтверждением)."""
        carrier_id = self.current_carrier_id()
        if carrier_id is None:
            QMessageBox.information(
                self, "Отвязка", "Водитель и так не привязан к перевозчику."
            )
            return

        title = self._carrier_title_by_id(carrier_id)
        reply = QMessageBox.question(
            self,
            "Отвязка",
            f"Отвязать водителя от перевозчика?\n\n{title}\n\n"
            f"Водитель останется в справочнике, история работы сохранится — "
            f"привязать его можно снова в любой момент.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        if not self.driver_id:
            QMessageBox.warning(
                self, "Отвязка", "Водитель ещё не сохранён в справочнике."
            )
            return

        try:
            ok = set_default_carrier(self.driver_id, None)
            # Активную связь закрываем: водитель больше не работает здесь.
            # Запись истории остаётся — её не удаляем.
            unlink_driver_from_carrier(self.driver_id, carrier_id)
        except Exception as e:  # noqa: BLE001 — диалог не должен падать
            logger.exception(f"Ошибка отвязки водителя: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось отвязать водителя:\n{e}")
            return

        if not ok:
            QMessageBox.critical(self, "Ошибка", "Не удалось отвязать водителя.")
            return

        self.changed = True
        self.driver["default_carrier_id"] = None
        logger.info(
            f"Водитель отвязан от перевозчика: driver_id={self.driver_id}, "
            f"carrier_id={carrier_id}"
        )
        self.reload()
        QMessageBox.information(self, "Готово", "Водитель отвязан от перевозчика.")

    def _on_load_to_form(self) -> None:
        """«📂 Загрузить в форму»: отдаёт перевозчика и закрывает диалог."""
        carrier_id = self.selected_carrier_id() or self.current_carrier_id()
        if carrier_id is None:
            QMessageBox.warning(self, "Загрузка", "Выберите перевозчика из списка.")
            return

        try:
            record = load_organization(carrier_id, is_carrier=True)
        except Exception as e:  # noqa: BLE001 — диалог не должен падать
            logger.exception(f"Ошибка чтения перевозчика: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать запись:\n{e}")
            return

        if not record:
            QMessageBox.warning(
                self, "Загрузка", "Запись перевозчика не найдена в справочнике."
            )
            return

        logger.info(f"Загрузка перевозчика в форму из диалога: ID={carrier_id}")
        if self.on_load_carrier:
            self.on_load_carrier(record)
        else:
            logger.warning("Обработчик загрузки в форму не задан")
        self.accept()

    def _on_search_changed(self, text: str) -> None:
        """Поиск по наименованию и ИНН: список сужается на месте."""
        needle = (text or "").strip().lower()
        for index in range(self.carriers_list.count()):
            item = self.carriers_list.item(index)
            record = item.data(Qt.UserRole)
            if not isinstance(record, dict):
                continue
            item.setHidden(bool(needle) and not _matches(record, needle))

        if needle:
            # Скрытую строку выделять нельзя: выбор сбрасываем, чтобы
            # «Привязать выбранного» не сработал по невидимой записи.
            self.carriers_list.clearSelection()
            self.carriers_list.setCurrentItem(None)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        """Двойной клик по перевозчику = привязать его."""
        record = item.data(Qt.UserRole) if item is not None else None
        carrier_id = self._as_id(record.get("id")) if isinstance(record, dict) else None
        if carrier_id is None or not self.driver_id:
            return
        self._bind_carrier(carrier_id)

    # ─────────────────────────────────────────────────────────
    # Мелочи
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _as_id(value: Any) -> Optional[int]:
        """ID записи числом; пустое и мусор — None."""
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _carrier_title_by_id(self, carrier_id: int) -> str:
        """Название перевозчика по ID (пусто — запись не прочитана)."""
        try:
            record = load_organization(carrier_id, is_carrier=True)
        except Exception:  # noqa: BLE001 — название не критично
            return ""
        return _carrier_title(record) if record else ""


def _carrier_title(org: Optional[Dict[str, Any]]) -> str:
    """Наименование перевозчика: полное, иначе сокращённое."""
    org = org if isinstance(org, dict) else {}
    return str(org.get("full_name") or org.get("short_name") or "").strip()


def _carrier_tooltip(org: Dict[str, Any], is_current: bool = False) -> str:
    """Подсказка строки списка: ИНН, КПП и пометка текущей привязки."""
    parts = []
    if is_current:
        parts.append("Текущий перевозчик водителя")
    if org.get("inn"):
        parts.append(f"ИНН: {org['inn']}")
    if org.get("kpp"):
        parts.append(f"КПП: {org['kpp']}")
    if org.get("director_name"):
        parts.append(f"Директор: {org['director_name']}")
    if org.get("is_deleted"):
        parts.append("Удалён из справочника")
    return "\n".join(parts)


def _matches(org: Dict[str, Any], needle: str) -> bool:
    """Подходит ли перевозчик под поиск (наименование, сокращённое, ИНН)."""
    for value in (org.get("full_name"), org.get("short_name"), org.get("inn")):
        if needle in str(value or "").lower():
            return True
    return False
