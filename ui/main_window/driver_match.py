# -*- coding: utf-8 -*-
"""
Согласование перевозчика договора с привязкой водителя.

Этап 6 (16.10.2026), подэтап 1.D: сюда переехали из прежнего
`ui/main_window.py` методы ШАГА «Привязка водителей к перевозчикам».
Логика не менялась — только переезд.

Смысл блока: у водителя в справочнике может быть закреплён основной
перевозчик (`default_carrier_id`). Если в форме перевозчик не заполнен —
подставляем его из справочника; если заполнен ДРУГОЙ — спрашиваем
оператора, продолжать ли (отказ прерывает операцию).
"""

import logging
from typing import Any, Dict, Optional

from PyQt5.QtWidgets import QMessageBox

from core import audit
from core.contract_data import ContractData

from db.database import find_organization_id, load_organization

# Имя логгера задано ЯВНО (а не __name__): после переезда в пакет
# `ui.main_window.driver_match` прежние записи выпали бы из настройки
# `config/logging_config.py` (APP_LOGGERS = "core", "ui", "db", ...).
logger = logging.getLogger("ui.main_window")


class DriverMatchMixin:
    """
    Перевозчик из карточки водителя: подстановка и предупреждение.

    Своего `__init__` у миксина нет: всё нужное (вкладки, статус-бар)
    создаёт `MainWindow.__init__` вместе с `LayoutMixin`.
    """

    @staticmethod
    def _driver_carrier_id(data: ContractData) -> Optional[int]:
        """
        Основной перевозчик водителя из формы (None — привязки нет).

        Вкладка отдаёт id числом; через промежуточные словари значение может
        прийти строкой — приводим к int, пустое и мусор считаем отсутствием.
        """
        driver = data.driver if isinstance(data.driver, dict) else {}
        value = driver.get("default_carrier_id")
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _carrier_form_is_empty(carrier: Dict[str, Any]) -> bool:
        """
        Пуст ли блок перевозчика в форме.

        Признаки заполнения — те же, по которым договор считает перевозчика
        указанным (наименование или ИНН): пустая форма заполняется молча.
        """
        carrier = carrier if isinstance(carrier, dict) else {}
        for key in ("full_name", "short_name", "inn"):
            if str(carrier.get(key) or "").strip():
                return False
        return True

    @staticmethod
    def _carrier_title(record: Optional[Dict[str, Any]]) -> str:
        """Название перевозчика для сообщений: полное, иначе сокращённое."""
        record = record if isinstance(record, dict) else {}
        return str(
            record.get("full_name") or record.get("short_name") or ""
        ).strip()

    def _check_driver_carrier_match(
        self, data: ContractData
    ) -> Optional[ContractData]:
        """
        Согласует перевозчика договора с основным перевозчиком водителя.

        Правила (ШАГ «Привязка водителей к перевозчикам»):

          * у водителя привязки нет — ничего не делаем;
          * перевозчик в форме пуст, а у водителя он есть — подставляем
            перевозчика из справочника и пересобираем данные;
          * перевозчик в форме заполнен и это ДРУГОЙ перевозчик — спрашиваем
            оператора; «Нет» (отмена) прерывает операцию.

        :return: данные для дальнейшей работы (возможно, пересобранные) или
            None, если оператор отказался продолжать.
        """
        driver_carrier_id = self._driver_carrier_id(data)

        if not driver_carrier_id:
            logger.info(
                "Договор: у водителя нет default_carrier_id — перевозчик "
                "из карточки не подставляется"
            )
            return data

        carrier = data.carrier if isinstance(data.carrier, dict) else {}

        # ── Перевозчик в форме пуст: берём основного у водителя ──
        if self._carrier_form_is_empty(carrier):
            record = None
            try:
                record = load_organization(driver_carrier_id, is_carrier=True)
            except Exception as e:  # noqa: BLE001 — без базы форма не меняется
                logger.warning(f"Перевозчик водителя не прочитан: {e}")

            if not record:
                logger.warning(
                    f"Перевозчик ID={driver_carrier_id} из карточки водителя "
                    f"не найден в справочнике — поле остаётся пустым"
                )
                return data

            self.carrier_tab.clear()
            self.carrier_tab.fill_data(record)
            logger.info(
                f"Договор: carrier подставлен из водителя (ID={driver_carrier_id})"
            )
            self._log_ui_action(
                "перевозчик подставлен из карточки водителя",
                carrier_id=driver_carrier_id,
            )
            self.statusBar().showMessage(
                "Перевозчик подставлен из карточки водителя", 5000
            )
            return self._collect_data()

        # ── Перевозчик в форме заполнен: сверяем с привязкой водителя ──
        contract_carrier_id = None
        try:
            contract_carrier_id = find_organization_id(carrier, is_carrier=True)
        except Exception as e:  # noqa: BLE001 — сверка не должна мешать работе
            logger.warning(f"Перевозчик формы не найден в справочнике: {e}")

        if contract_carrier_id and contract_carrier_id == driver_carrier_id:
            logger.info(
                f"Договор: перевозчик совпадает с привязкой водителя "
                f"(ID={driver_carrier_id})"
            )
            return data

        driver_carrier = self._carrier_title(
            self._safe_organization(driver_carrier_id)
        )
        contract_carrier = self._carrier_title(carrier) or str(
            carrier.get("full_name") or carrier.get("short_name") or ""
        ).strip()
        driver_name = str(
            (data.driver or {}).get("full_name") or "без имени"
        ).strip()

        logger.warning(
            f"Договор: перевозчик водителя (ID={driver_carrier_id}) и "
            f"перевозчик договора (ID={contract_carrier_id}) не совпадают"
        )
        if not self._confirm_carrier_mismatch(
            driver_name, driver_carrier, contract_carrier
        ):
            logger.info("Операция прервана: перевозчики не совпадают")
            audit.log_denied(
                "contract_carrier_mismatch",
                "оператор отказался продолжать с другим перевозчиком",
            )
            return None

        # Продолжаем как есть — расхождение записано в аудит.
        audit.log_event(
            "contract_carrier_mismatch",
            driver_id=(data.driver or {}).get("id"),
            carrier_id=driver_carrier_id,
            mode="продолжено",
        )
        return data

    def _safe_organization(self, org_id: int) -> Optional[Dict[str, Any]]:
        """Организация по ID или None: справочник не должен ронять диалог."""
        try:
            return load_organization(org_id, is_carrier=True)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Не удалось прочитать перевозчика ID={org_id}: {e}")
            return None

    def _confirm_carrier_mismatch(
        self, driver_name: str, driver_carrier: str, contract_carrier: str
    ) -> bool:
        """
        Спрашивает оператора, продолжать ли с «чужим» перевозчиком.

        :return: True — продолжать, False — прервать операцию.
        """
        answer = QMessageBox.warning(
            self,
            "Перевозчики не совпадают",
            f"Водитель {driver_name} закреплён за перевозчиком "
            f"{driver_carrier or 'из справочника'}, "
            f"а в договоре выбран {contract_carrier or 'другой перевозчик'}.\n\n"
            f"Продолжить?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes
