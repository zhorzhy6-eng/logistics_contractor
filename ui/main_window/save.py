# -*- coding: utf-8 -*-
"""
Сохранение данных формы в справочники и в договоры.

Этап 6 (16.10.2026), подэтап 1.E: сюда переехали из прежнего
`ui/main_window.py` методы кнопки «Сохранить в базу». Логика не менялась —
только переезд.

Порядок сохранения (важен, менять нельзя):
  1. водитель (`save_driver`) + тягач/полуприцеп (`save_driver_vehicle`);
  2. заказчик — только ОБНОВЛЕНИЕ найденной записи (`create=False`):
     автосоздание плодило дубли, заказчиков в справочнике всего два;
  3. перевозчик — обновление найденной записи либо создание новой;
  4. ссылки на справочник попадают в договор (`bind_reference_ids`);
  5. сам договор с точками маршрута и таблицей ТС
     (`save_contract_with_details`).
"""

import logging
from typing import Any, Dict, Optional

from PyQt5.QtWidgets import QMessageBox

from core import audit
from core.trace import filled_fields_summary

from db.database import (
    find_organization_id,
    load_organization,
    restore_organization,
    save_contract_with_details,
    save_driver,
    save_driver_vehicle,
    save_organization,
    update_organization,
)

# Имя логгера задано ЯВНО (а не __name__): после переезда в пакет
# `ui.main_window.save` прежние записи выпали бы из настройки
# `config/logging_config.py` (APP_LOGGERS = "core", "ui", "db", ...).
logger = logging.getLogger("ui.main_window")


class SaveMixin:
    """
    Сохранение формы в базу: справочники, ссылки и договор.

    Своего `__init__` у миксина нет: вкладки, статус-бар и клиента готовит
    `MainWindow.__init__` вместе с `LayoutMixin` и `SettingsMixin`.
    """

    def _sync_organization_with_db(
        self, org: Dict[str, Any], is_carrier: bool, create: bool = True
    ) -> Optional[int]:
        """
        Приводит запись справочника в соответствие с данными формы.

        Логика одна для перевозчика и заказчика:

          * организация ищется по реквизитам формы (`find_organization_id`:
            ИНН, затем полное наименование);
          * НАЙДЕНА — ОБНОВЛЯЕТСЯ данными формы. Раньше найденная запись
            просто пропускалась, и правки оператора (телефон, КПП, банк)
            терялись: писало «Перевозчик найден в справочнике», а в базе
            оставалось старое значение;
          * НЕ найдена — создаётся новая (`create=True`) либо договор
            сохраняется без ссылки (`create=False`, так ведёт себя заказчик:
            автосоздание плодило дубли — ШАГ «Заказчик без дублей»);
          * мягко удалённая запись возвращается в справочник: иначе «Успех»
            печатался бы про запись, которой в дереве базы не видно (так и
            выглядел баг «перевозчик не сохранился» — запись с тем же ИНН
            лежала удалённой, обновления не было, а в справочнике её нет).

        :return: id записи справочника или None, если её нет и создавать
            не разрешено (либо создать не удалось).
        """
        title = "Перевозчик" if is_carrier else "Заказчик"

        existing_id = find_organization_id(org, is_carrier=is_carrier)
        if not existing_id:
            if not create:
                logger.info(
                    f"{title} не найден в справочнике — запись не создана, "
                    f"договор сохранится без ссылки"
                )
                return None

            record_id = save_organization(org, is_carrier=is_carrier)
            logger.info(f"{title} сохранён: ID={record_id}")
            return record_id

        # Существующая запись: форма — источник правды, поэтому обновляем.
        # Прочитанная ДО обновления запись нужна ради флага is_deleted.
        was_deleted = bool(
            (self._safe_organization_record(existing_id, is_carrier) or {}).get(
                "is_deleted"
            )
        )

        if not update_organization(existing_id, org, is_carrier=is_carrier):
            logger.error(
                f"{title}: не удалось обновить запись ID={existing_id} — "
                f"в справочнике остались прежние данные"
            )
            return existing_id

        logger.info(f"{title} обновлён: ID={existing_id}")
        if was_deleted and restore_organization(existing_id, is_carrier=is_carrier):
            logger.info(f"{title} возвращён в справочник: ID={existing_id}")

        return existing_id

    @staticmethod
    def _safe_organization_record(
        org_id: int, is_carrier: bool
    ) -> Optional[Dict[str, Any]]:
        """Запись справочника по id (включая мягко удалённую) или None."""
        try:
            return load_organization(org_id, is_carrier=is_carrier)
        except Exception as e:  # noqa: BLE001 — чтение не должно мешать записи
            logger.warning(f"Не удалось прочитать организацию ID={org_id}: {e}")
            return None

    def _on_save_to_db(self):
        logger.info("Нажата кнопка «Сохранить в базу»")
        self._log_ui_action("нажата кнопка «Сохранить в базу»")
        try:
            logger.info("Сохранение в базу: сбор данных")
            data = self._collect_data()

            # Перевозчик из карточки водителя: подстановка в пустое поле или
            # предупреждение о расхождении (ШАГ «Привязка водителей»).
            data = self._check_driver_carrier_match(data)
            if data is None:
                self.statusBar().showMessage(
                    "Не сохранено — перевозчики не совпадают", 5000
                )
                return

            driver = data.driver
            customer = data.customer
            carrier = data.carrier
            tractor = data.tractor
            trailer = data.trailer

            # Углублённое логирование: по каждому разделу видно, сколько полей
            # заполнено и какие остались пустыми (только имена полей).
            logger.debug(
                f"Сохранение в БД: водитель — {filled_fields_summary(driver)}"
            )
            logger.debug(
                f"Сохранение в БД: заказчик — {filled_fields_summary(customer)}"
            )
            logger.debug(
                f"Сохранение в БД: перевозчик — {filled_fields_summary(carrier)}"
            )
            logger.debug(
                f"Сохранение в БД: тягач — {filled_fields_summary(tractor)} | "
                f"полуприцеп — {filled_fields_summary(trailer)}"
            )
            logger.debug(
                f"Сохранение в БД: перевозимых ТС — "
                f"{len(data.vehicles) if isinstance(data.vehicles, list) else 0} | "
                f"погрузок — {len(data.loadings)} | "
                f"выгрузок — {len(data.unloadings)}"
            )

            # ── Сохраняем водителя + тягач/прицеп ──
            # Вариант В: ID сохранённых записей сразу попадают в договор
            # (contracts.driver_id / customer_id / carrier_id). Благодаря
            # мягкому удалению ссылка не превратится в «ничто»: даже удалённый
            # из справочника водитель остаётся связанным с историей договоров.
            driver_id = customer_id = carrier_id = None

            if driver.get("full_name"):
                driver_id = save_driver(driver)
                logger.info(f"Водитель сохранён: ID={driver_id}")
                audit.log_event("driver_saved", driver_id=driver_id)

                # ── СОХРАНЯЕМ ТЯГАЧ/ПРИЦЕП ──
                tractor_plate = (tractor.get("plate_number") or "").strip()
                trailer_plate = (trailer.get("plate_number") or "").strip()
                tractor_brand = (tractor.get("brand_model") or "").strip()
                trailer_brand = (trailer.get("brand_model") or "").strip()

                if tractor_plate or trailer_plate or tractor_brand or trailer_brand:
                    save_driver_vehicle(driver_id, {
                        "tractor_brand": tractor_brand,
                        "tractor_plate": tractor_plate,
                        "tractor_color": (tractor.get("color") or "").strip(),
                        "tractor_year": str(tractor.get("year", "") or ""),
                        "trailer_brand": trailer_brand,
                        "trailer_plate": trailer_plate,
                        "trailer_color": (trailer.get("color") or "").strip(),
                        "trailer_year": str(trailer.get("year", "") or ""),
                    })
                    logger.info(
                        f"Тягач/прицеп сохранены для водителя ID={driver_id}"
                    )
                else:
                    logger.info(
                        "Тягач/прицеп НЕ сохранены — пустые поля "
                        "(марка/номер не заполнены)"
                    )
            else:
                logger.info("Водитель не сохранён — ФИО пустое")

            # ── Заказчик ──
            # Автосохранение НЕ создаёт запись в customers: заказчиков в
            # организации всего 2, автосоздание плодит дубли. НО найденная
            # по ИНН/наименованию запись ОБНОВЛЯЕТСЯ данными формы — иначе
            # правки оператора (адрес, банк, руководитель) терялись бы молча.
            # Не нашли — договор сохраняется без ссылки; пользователь
            # добавит заказчика вручную через «База данных», если нужно.
            customer_id = None
            if customer.get("full_name") or customer.get("short_name"):
                customer_id = self._sync_organization_with_db(
                    customer, is_carrier=False, create=False
                )
                if customer_id:
                    logger.info(
                        f"Заказчик в справочнике: ID={customer_id}, "
                        f"ссылка записана в договор"
                    )
                else:
                    logger.info(
                        "Заказчик не найден в справочнике — запись не создана, "
                        "договор сохранится без ссылки. Добавьте заказчика "
                        "через «База данных», если нужно связать."
                    )
            else:
                logger.info("Заказчик в форме пуст — ссылка не записана")

            # ── Перевозчик ──
            # Перевозчик мог быть подставлен из карточки водителя (или
            # загружен из справочника) — тогда договор ссылается на ту же
            # запись, а не на её копию; сама запись при этом обновляется
            # данными формы. Совсем нового перевозчика по-прежнему создаём.
            if carrier.get("full_name") or carrier.get("short_name"):
                carrier_id = self._sync_organization_with_db(
                    carrier, is_carrier=True
                )
            else:
                logger.info("Перевозчик не сохранён — наименование пустое")

            # ── Ссылки договора на справочник (вариант В) ──
            data.bind_reference_ids(
                driver_id=driver_id,
                customer_id=customer_id,
                carrier_id=carrier_id,
            )

            # ── Перевозимые ТС + тягач/прицеп (для общей таблицы) ──
            all_vehicles = data.to_vehicle_rows()

            # ── Договор ──
            contract = data.to_db_dict()
            contract_id = save_contract_with_details(
                contract, data.loadings, data.unloadings, all_vehicles
            )
            if all_vehicles:
                logger.info(f"Сохранено ТС: {len(all_vehicles)}")
            logger.info(
                f"Договор сохранён: ID={contract_id}, №={contract.get('number')}"
            )
            logger.debug(
                f"Сохранение в БД: договор — {filled_fields_summary(contract)} | "
                f"ID={contract_id} | строк ТС={len(all_vehicles)} | "
                f"ссылки: driver_id={data.driver_id}, "
                f"customer_id={data.customer_id}, carrier_id={data.carrier_id}"
            )
            audit.log_event(
                "saved_to_db",
                contract_id=contract_id,
                contract_number=contract.get("number", ""),
                entities="driver,customer,carrier,vehicles,contract",
                count=len(all_vehicles),
            )

            self._mark_saved()
            self._refresh_status_indicators()
            QMessageBox.information(self, "Успех", "Данные сохранены в базу!")
        except Exception as e:
            logger.exception("Ошибка при сохранении в базу")
            audit.log_event("database_save_failed", result="error")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось сохранить данные:\n{e}"
            )
