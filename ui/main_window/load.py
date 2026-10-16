# -*- coding: utf-8 -*-
"""
Загрузка записей справочника в форму: водитель, заказчик, перевозчик.

Этап 6 (16.10.2026), подэтап 1.F: сюда переехали из прежнего
`ui/main_window.py` методы менеджера базы. Логика не менялась — только
переезд.

Особенность этого блока: функции базы импортированы в ЭТОТ модуль и
вызываются по короткому имени (`load_organization_by_id(...)`) — так тест
может подменить функцию:
`monkeypatch.setattr(ui.main_window.load, "load_organization_by_id", …)`
(см. `tests/test_ui_main_window_load_driver_carrier.py`).

⚠️ Подменять надо ИМЕННО ЭТОТ модуль. Реэкспорт в `ui/main_window/__init__.py`
(оставлен ради прежнего вида одиночного модуля) вызов НЕ перехватывает:
`monkeypatch.setattr(ui.main_window, "load_organization_by_id", …)` меняет
атрибут пакета, а `_load_driver_from_db` читает своё глобальное имя.
Проверено пробой: после подмены пакета `ui.main_window.load.
load_organization_by_id` остаётся прежней функцией.
"""

import logging
from typing import Any, Dict

from PyQt5.QtWidgets import QMessageBox

from core.vat import split_carrier_type

from db.database import (
    load_driver,
    load_driver_vehicle,
    load_organization,
    load_organization_by_id,
)

from ui.db_manager_dialog import DbManagerDialog

# Имя логгера задано ЯВНО (а не __name__): после переезда в пакет
# `ui.main_window.load` прежние записи выпали бы из настройки
# `config/logging_config.py` (APP_LOGGERS = "core", "ui", "db", ...).
logger = logging.getLogger("ui.main_window")


class LoadMixin:
    """
    Загрузка записей справочника: водитель, заказчик, перевозчик.

    Своего `__init__` у миксина нет: вкладки и статус-бар готовит
    `MainWindow.__init__` вместе с `LayoutMixin`.
    """

    def _on_open_db_manager(self):
        logger.info("Открытие менеджера базы данных")
        self._log_ui_action("нажата кнопка «База данных»")
        try:
            dialog = DbManagerDialog(
                self,
                on_load_driver=self._load_driver_from_db,
                on_load_customer=self._load_customer_from_db,
                on_load_carrier=self._load_carrier_from_db,
            )
            logger.info(
                f"DbManagerDialog создан: "
                f"on_load_driver={dialog.on_load_driver is not None}, "
                f"on_load_customer={dialog.on_load_customer is not None}, "
                f"on_load_carrier={dialog.on_load_carrier is not None}"
            )
            dialog.exec_()
        except Exception as e:
            logger.exception("Ошибка при открытии менеджера БД")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось открыть базу:\n{e}"
            )

    def _load_driver_from_db(self, driver: Dict[str, Any]):
        logger.info(
            f"Загрузка водителя из БД: ID={driver.get('id')}"
        )
        self._log_ui_action("загрузка водителя из БД", id=driver.get("id"))

        try:
            # Полное соответствие записи из справочника (см. комментарий
            # в _load_customer_from_db про частичное заполнение).
            self.driver_tab.clear()
            # Справочник перевозчиков мог пополниться, пока окно открыто
            # (перевозчиков заводят в «Менеджере базы» прямо во время
            # работы): без перечитывания привязка водителя не нашлась бы
            # в списке и вкладка показала бы «— не указан —».
            self.driver_tab.fill_carriers()
            self.driver_tab.fill_data(driver)
            self.trailer_tab.clear()

            driver_id = driver.get("id")
            if driver_id:
                try:
                    vehicle = load_driver_vehicle(driver_id)
                    if vehicle:
                        tractor = {
                            "brand_model": vehicle.get("tractor_brand", "") or "",
                            "plate_number": vehicle.get("tractor_plate", "") or "",
                            "color": vehicle.get("tractor_color", "") or "",
                            "year": vehicle.get("tractor_year", 0) or 0,
                        }
                        trailer = {
                            "brand_model": vehicle.get("trailer_brand", "") or "",
                            "plate_number": vehicle.get("trailer_plate", "") or "",
                            "color": vehicle.get("trailer_color", "") or "",
                            "year": vehicle.get("trailer_year", 0) or 0,
                        }
                        self.trailer_tab.fill_data(tractor, trailer)
                        logger.info(
                            f"Тягач/прицеп подгружены для водителя ID={driver_id}"
                        )
                    else:
                        logger.info(
                            f"Для водителя ID={driver_id} нет данных о ТС"
                        )
                except Exception as e:
                    logger.exception(
                        f"Не удалось загрузить ТС водителя ID={driver_id}: {e}"
                    )

            # ── Подтянуть перевозчика водителя (если привязан) ──
            # Раньше грузился только водитель, а перевозчик (даже если он
            # есть в карточке) не подставлялся — приходилось выбирать вручную.
            # Теперь привязка используется автоматически, и мягко удалённый
            # перевозчик тоже (load_organization_by_id читает запись по ID).
            carrier_id = driver.get("default_carrier_id")
            if carrier_id:
                try:
                    carrier = load_organization_by_id(
                        carrier_id, is_carrier=True
                    )
                    if carrier:
                        self.carrier_tab.clear()
                        self.carrier_tab.fill_data(carrier)
                        self._sync_contract_form_type()
                        logger.info(
                            f"Перевозчик подтянут из водителя: ID={carrier_id}"
                        )
                        self._log_ui_action(
                            "перевозчик подтянут из карточки водителя",
                            id=carrier_id,
                        )
                except Exception as e:  # noqa: BLE001 — водителя грузим дальше
                    logger.exception(
                        f"Не удалось подтянуть перевозчика ID={carrier_id}: {e}"
                    )

            self.tabs.setCurrentWidget(self.driver_tab)

            self.statusBar().showMessage(
                f"Загружен водитель: {driver.get('full_name', '')}", 5000
            )
        except Exception as e:
            logger.exception("Ошибка загрузки водителя из БД")
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось загрузить водителя:\n{e}"
            )

    def _load_customer_from_db(self, customer: Dict[str, Any]):
        logger.info(
            f"Загрузка заказчика из БД: ID={customer.get('id')}"
        )
        self._log_ui_action("загрузка заказчика из БД", id=customer.get("id"))
        try:
            # Форма должна соответствовать записи из справочника, поэтому
            # сначала очищаем вкладку: fill_data обновляет только те поля,
            # которые пришли (это защищает ручной ввод при распознавании).
            self.customer_tab.clear()
            self.customer_tab.fill_data(customer)
            self._apply_party_type(self.customer_tab, customer)
            self.tabs.setCurrentWidget(self.customer_tab)
            self.statusBar().showMessage(
                f"Загружен заказчик: {customer.get('full_name', '')}", 5000
            )
        except Exception as e:
            logger.exception("Ошибка загрузки заказчика из БД")
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось загрузить заказчика:\n{e}"
            )

    def _load_carrier_from_db(self, carrier: Dict[str, Any]):
        logger.info(
            f"Загрузка перевозчика из БД: ID={carrier.get('id')}"
        )
        self._log_ui_action("загрузка перевозчика из БД", id=carrier.get("id"))
        try:
            self.carrier_tab.clear()
            self.carrier_tab.fill_data(carrier)
            self._apply_party_type(self.carrier_tab, carrier)
            self._sync_contract_form_type()
            self.tabs.setCurrentWidget(self.carrier_tab)
            self.statusBar().showMessage(
                f"Загружен перевозчик: {carrier.get('full_name', '')}", 5000
            )
        except Exception as e:
            logger.exception("Ошибка загрузки перевозчика из БД")
            QMessageBox.critical(
                self, "Ошибка",
                f"Не удалось загрузить перевозчика:\n{e}"
            )

    def _apply_party_type(self, tab, record: Dict[str, Any]) -> None:
        """
        Вид стороны из справочника — в форму (вкладка «Заказчик» и
        «Перевозчик»).

        У вкладки «Перевозчик» вид лица выбирает бланк и ветвь п. 1.2, а в
        справочнике он теперь хранится (`entity_type`): без этого шага ИП из
        базы печатался как ООО — с КПП, ОГРН и «именуемое». У вкладки
        «Заказчик» своего переключателя нет (вид выводится из
        {{client_legal_form_prefix}} в бланке), поэтому её вид уходит во
        вкладку «Договор»: он больше не сбрасывается при загрузке стороны.

        Записи, заведённые до появления колонки, вида не имеют — тогда
        подставляется он по наименованию, и в лог идёт только вид, без
        наименований и ФИО.
        """
        entity_type = self._party_entity_type(tab, record)
        if not entity_type:
            return

        setter = getattr(tab, "apply_entity_type", None)
        if setter is None:
            logger.debug(
                "У вкладки нет переключателя вида стороны — вид берётся из данных"
            )
            return

        setter(entity_type)
        logger.info(f"Вид стороны из справочника: {entity_type}")

    def _sync_contract_form_type(self) -> None:
        """
        Форма перевозчика с вкладки «Перевозчик» — в поле «Форма» договора.

        Бланк договора выбирается по вкладке «Договор» (поля «Форма» и
        «Ставка НДС»), поэтому её «Форма» должна совпадать с видом
        загруженного перевозчика: у ИП из справочника оператор должен видеть
        ИП, а не оставшееся от прошлой загрузки ООО. Ставка НДС не трогается
        — в справочнике её нет, а угадывать её по виду стороны нельзя.
        """
        entity_type, _rate = split_carrier_type(
            self.carrier_tab.carrier_type.currentText()
        )
        if entity_type:
            self.contract_tab.apply_entity_type(entity_type)

    @staticmethod
    def _party_entity_type(tab, record: Dict[str, Any]) -> str:
        """
        Вид стороны: из записи, а если её вид пуст — по полю «Тип» вкладки.

        У перевозчика переключатель есть на самой вкладке, и `fill_data` уже
        мог его выставить по наименованию; у заказчика переключателя нет, и
        вид берётся из вкладки «Договор» (там же, где выбирается бланк).
        """
        saved = str(record.get("entity_type") or "").strip()
        if saved:
            return saved
        if tab is not None and hasattr(tab, "carrier_type"):
            return tab.carrier_type.currentText()
        return ""
