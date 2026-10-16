# -*- coding: utf-8 -*-
"""
Сбор данных со всех вкладок окна и проверка перед генерацией.

Этап 6 (16.10.2026), подэтап 1.H: сюда переехали из прежнего
`ui/main_window.py` методы единого сбора данных (ШАГ 1 рефакторинга
архитектуры контрактов), диалог проверки (ШАГ 2) и отметка о сохранении в
статус-баре. Логика не менялась — только переезд.

Почему сбор данных вынесен отдельно: раньше данные собирались ДВАЖДЫ и
по-разному — в `_on_create_contract` (для DOCX) и в `_on_save_to_db` (для
БД). Наборы ключей расходились, из-за чего в договор не попадали тягач,
полуприцеп и город. Теперь оба потребителя получают ОДИН объект
`ContractData` из `_collect_data()`.
"""

import logging
from datetime import datetime

from core.contract_data import ContractData
from core.validator import ValidationReport

from PyQt5.QtWidgets import QMessageBox

# Имя логгера задано ЯВНО (а не __name__): после переезда в пакет
# `ui.main_window.data` прежние записи выпали бы из настройки
# `config/logging_config.py` (APP_LOGGERS = "core", "ui", "db", ...).
logger = logging.getLogger("ui.main_window")


class DataMixin:
    """
    Единый сбор данных с вкладок и проверка перед генерацией.

    Своего `__init__` у миксина нет: вкладки и статус-бар готовит
    `MainWindow.__init__` вместе с `LayoutMixin`.
    """

    def _collect_data(self) -> ContractData:
        """
        Собирает данные со всех вкладок ОДИН раз и возвращает ContractData.

        Раньше данные собирались дважды и по-разному: в _on_create_contract
        (для DOCX) и в _on_save_to_db (для БД). Наборы ключей расходились, из-за
        чего в договор не попадали тягач, полуприцеп и город. Теперь оба
        потребителя получают один и тот же объект.

        ВАЖНО: метод не меняет состояние UI (никаких setText/clear), поэтому
        его можно вызывать многократно.
        """
        contract = self.contract_tab.get_data()

        data = ContractData(
            driver=self.driver_tab.get_data(),
            carrier=self.carrier_tab.get_data(),
            customer=self.customer_tab.get_data(),
            vehicles=self.vehicles_tab.get_data(),
            tractor=self.trailer_tab.get_tractor_data(),
            trailer=self.trailer_tab.get_trailer_data(),
            contract=contract,
            loadings=contract.get("loadings") or [],
            unloadings=contract.get("unloadings") or [],
            # city появится здесь автоматически, если вкладка «Договор» начнёт
            # его отдавать; сейчас город вычисляется в ContractData.resolved_city()
            city=str(contract.get("city", "") or ""),
        )

        logger.info(f"Данные собраны: {data.summary()}")
        return data

    def _confirm_validation(self, report: ValidationReport) -> bool:
        """
        Показывает результат проверки данных перед генерацией договора.

        Возвращает True, если можно продолжать:
          * замечаний нет — сразу True (диалогов не будет, как раньше);
          * есть ошибки или замечания — один диалог со списком и вопросом.
            Кнопка по умолчанию — «Исправить», но возможность напечатать
            документ сохраняется (поведение не заблокировано жёстко).
        """
        if report.is_clean:
            logger.info("Проверка данных пройдена без замечаний")
            return True

        logger.warning(
            f"Проверка данных: ошибок={len(report.errors)}, "
            f"замечаний={len(report.warnings)}"
        )

        box = QMessageBox(self)
        box.setWindowTitle("Проверьте данные" if report.has_errors else "Замечания к данным")
        box.setIcon(QMessageBox.Critical if report.has_errors else QMessageBox.Warning)
        box.setText(
            "В данных есть ошибки. Договор можно создать, но проверьте реквизиты."
            if report.has_errors
            else "В данных есть замечания. Поля, отмеченные ниже, останутся пустыми "
                 "(у денежных полей будет 0.00, у текстовых — пусто)."
        )
        box.setInformativeText(report.format_text())
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)

        yes_button = box.button(QMessageBox.Yes)
        no_button = box.button(QMessageBox.No)
        if yes_button is not None:
            yes_button.setText("Создать договор")
        if no_button is not None:
            no_button.setText("Исправить")

        return box.exec_() == QMessageBox.Yes

    def _mark_saved(self) -> None:
        """Отмечает в статус-баре время последнего сохранения в базу."""
        self._last_save_at = datetime.now()
        self._status_saved_label.setText(
            f"Сохранено: {self._last_save_at.strftime('%H:%M')}"
        )
        self._log_ui_action(
            "статус-бар: сохранение",
            time=self._last_save_at.strftime("%H:%M:%S"),
        )
