# -*- coding: utf-8 -*-
"""
Создание договора: проверка данных, генерация DOCX, диалог успеха.

Этап 6 (16.10.2026), подэтап 1.G: сюда переехали из прежнего
`ui/main_window.py` методы кнопки «Создать договор». Логика не менялась —
только переезд.

Порядок работы кнопки (важен, менять нельзя):
  1. `_collect_data()` — данные со всех вкладок собираются ОДИН раз;
  2. `_check_driver_carrier_match()` — согласование перевозчика с привязкой
     водителя (отказ оператора прерывает операцию);
  3. `PerevozkaValidator().check()` — валидатор ТИПА, не базовый;
  4. `_confirm_validation()` — диалог с замечаниями;
  5. `ContractGenerator.generate()` — файл;
  6. `_show_contract_created()` — «Открыть папку» / «OK».

Валидатор берётся прямым путём (`core.contracts.perevozka.validator`), а не
реэкспортом из `core.contract_generator`: так видно, чей это валидатор, и не
тянется лишняя зависимость от модуля генератора.
"""

import logging
import os

from PyQt5.QtWidgets import QMessageBox

from core import audit
from core.contract_generator import ContractGenerator
from core.contracts.perevozka.validator import PerevozkaValidator

# Имя логгера задано ЯВНО (а не __name__): после переезда в пакет
# `ui.main_window.contract` прежние записи выпали бы из настройки
# `config/logging_config.py` (APP_LOGGERS = "core", "ui", "db", ...).
logger = logging.getLogger("ui.main_window")


class ContractMixin:
    """
    Создание договора DOCX и сообщение о результате.

    Своего `__init__` у миксина нет: генератор (`self.contract_generator`),
    вкладки и статус-бар готовит `MainWindow.__init__` вместе с
    `LayoutMixin`.
    """

    def _on_create_contract(self):
        logger.info("Нажата кнопка «Создать договор»")
        self._log_ui_action("нажата кнопка «Создать договор»")
        try:
            logger.info("Создание договора: сбор данных")
            data = self._collect_data()

            # Перевозчик из карточки водителя: подстановка или предупреждение
            # о расхождении (ШАГ «Привязка водителей к перевозчикам»).
            data = self._check_driver_carrier_match(data)
            if data is None:
                self.statusBar().showMessage(
                    "Договор не создан — перевозчики не совпадают", 5000
                )
                return

            logger.info("Создание договора: проверка данных")
            # Валидатор ТИПА, а не базовый: у перевозки есть свои замечания
            # (например, длины БИК и корр. счёта — ШАГ FIX-6, часть A3).
            # Диалог подтверждения — тот же (_confirm_validation ниже).
            report = PerevozkaValidator().check(data)
            if not self._confirm_validation(report):
                logger.info("Генерация договора отменена пользователем после проверки")
                audit.log_denied("contract_created", "проверка данных не пройдена")
                self.statusBar().showMessage("Договор не создан — исправьте данные", 5000)
                return

            logger.info("Создание договора: генерация файла")
            path = self.contract_generator.generate(data)
            logger.info(f"Договор создан: {path}")
            audit.log_event(
                "contract_created",
                contract_number=data.contract.get("number", ""),
                file=os.path.basename(path),
                mode=data.contract.get("carrier_type", ""),
            )
            self._show_contract_created(path)
        except Exception as e:
            logger.exception("Ошибка при создании договора")
            audit.log_event("contract_creation_failed", result="error")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось создать договор:\n{e}"
            )

    def _show_contract_created(self, path: str):
        """
        Сообщает о созданном договоре и предлагает открыть папку (Шаг 6).

        Вместо простого «ОК» добавлена кнопка «Открыть папку» — файлы лежат
        в output/, и до них больше не нужно добираться вручную.
        """
        folder = os.path.dirname(os.path.abspath(path))

        box = QMessageBox(self)
        box.setWindowTitle("Успех")
        box.setIcon(QMessageBox.Information)
        box.setText("Договор создан")
        box.setInformativeText(f"{os.path.basename(path)}\n\nПапка: {folder}")

        open_button = box.addButton("Открыть папку", QMessageBox.ActionRole)
        box.addButton("OK", QMessageBox.AcceptRole)

        box.exec_()

        if box.clickedButton() is open_button:
            self._log_ui_action("диалог создания договора: «Открыть папку»")
            self._open_folder(folder)
        else:
            self._log_ui_action("диалог создания договора закрыт")

    def _open_folder(self, folder: str) -> bool:
        """Открывает папку в системном файловом менеджере."""
        if not folder or not os.path.isdir(folder):
            logger.warning(f"Папка для открытия не найдена: {folder!r}")
            return False

        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtGui import QDesktopServices

            if QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
                logger.info(f"Открыта папка: {folder}")
                return True
            logger.warning(f"QDesktopServices не смог открыть папку: {folder}")
        except Exception as e:
            logger.warning(f"Не удалось открыть папку через QDesktopServices: {e}")

        # Резерв для Windows, если Qt не справился
        try:
            os.startfile(folder)  # type: ignore[attr-defined]
            logger.info(f"Открыта папка (os.startfile): {folder}")
            return True
        except Exception as e:
            logger.error(f"Не удалось открыть папку {folder}: {e}")
            return False
