# -*- coding: utf-8 -*-
"""
Создание договора: проверка данных, генерация DOCX, диалог успеха.

Этап 6 (16.10.2026), подэтап 1.G: сюда переехали из прежнего
`ui/main_window.py` методы кнопки «Создать договор». Логика не менялась —
только переезд.

Задача 2 (05.11.2026): здесь же — быстрое создание СВЯЗАННЫХ договоров.
Раньше оператор создавал договор Экспедиторства, переходил в окно клиентского
типа, жал «Отзеркалить», отвечал на диалог конфликтов и правил сумму вручную.
Теперь при нажатии «Создать договор» появляется диалог: только Экспедиторство
или Экспедиторство + целевой тип с суммой. Целевое окно получает данные
зеркала, сумму из диалога и генерирует свой документ само; оба файла ложатся
в папку рейса.

Порядок работы кнопки (важен, менять нельзя):
  1. `_ask_linked_contract()` — только Экспедиторство или пара с типом;
  2. `_collect_data()` — данные со всех вкладок собираются ОДИН раз;
  3. `_check_driver_carrier_match()` — согласование перевозчика с привязкой
     водителя (отказ оператора прерывает операцию);
  4. `PerevozkaValidator().check()` — валидатор ТИПА, не базовый;
  5. `_confirm_validation()` — диалог с замечаниями;
  6. `ContractGenerator.generate()` — файл;
  7. если выбран связанный тип — `_create_linked_contract()` (зеркало +
     сумма + документ цели), иначе `_show_contract_created()`;
  8. `_show_contract_created()` / `_show_pair_created()` — «Открыть папку».

Валидатор берётся прямым путём (`core.contracts.perevozka.validator`), а не
реэкспортом из `core.contract_generator`: так видно, чей это валидатор, и не
тянется лишняя зависимость от модуля генератора.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PyQt5.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QLabel, QMessageBox, QRadioButton, QVBoxLayout,
)

from core import audit
from core.contracts.perevozka.validator import PerevozkaValidator
from core.contracts.picker_order import picker_title
from core.mirror import mirror_from_expedition, supported_targets
from ui.windows.manager import current_window_manager

# Имя логгера задано ЯВНО (а не __name__): после переезда в пакет
# `ui.main_window.contract` прежние записи выпали бы из настройки
# `config/logging_config.py` (APP_LOGGERS = "core", "ui", "db", ...).
logger = logging.getLogger("ui.main_window")

#: Подпись режима «без связанного договора» — одна на диалог и тесты.
ONLY_EXPEDITION_LABEL = "Только Экспедиторство"


@dataclass(frozen=True)
class LinkedContractChoice:
    """
    Что выбрал оператор в диалоге создания.

    :param target_type: ключ типа-цели ("" — только Экспедиторство).
    :param amount: сумма для целевого документа, ИТОГ с НДС (0 — без цели).
    """

    target_type: str = ""
    amount: float = 0.0

    def is_linked(self) -> bool:
        """Нужно ли создавать второй (связанный) договор."""
        return bool(self.target_type)


@dataclass(frozen=True)
class LinkedContractResult:
    """
    Итог создания связанного договора.

    path пустой — второй документ не создан, причина в reason (её видит
    оператор). Исключения генератора сюда не заворачиваются: их ловит
    вызывающий и показывает отдельным сообщением.
    """

    path: str = ""
    reason: str = ""

    def is_created(self) -> bool:
        """Создан ли связанный документ."""
        return bool(self.path)


class LinkedContractDialog(QDialog):
    """
    Выбор при «Создать договор»: только Экспедиторство или пара с типом.

    Радиокнопки строятся по списку типов-целей (те, у которых есть кнопка
    «Отзеркалить»); поле суммы появляется только при выборе пары и говорит,
    для какого типа сумма. «Создать» недоступно, пока сумма пары не
    положительна: нулевая сумма в бланке бессмысленна.
    """

    def __init__(self, target_titles: Sequence[Tuple[str, str]],
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создание договора")
        self.setModal(True)

        #: Ключ типа → радиокнопка ("" — только Экспедиторство).
        self.radios: Dict[str, QRadioButton] = {}
        self._titles: Dict[str, str] = dict(target_titles)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        hint = QLabel(
            "Создать только договор Экспедиторства или сразу пару: "
            "договор Экспедиторства и связанный договор другого типа?"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._group = QButtonGroup(self)
        only = QRadioButton(ONLY_EXPEDITION_LABEL)
        only.setChecked(True)
        self._group.addButton(only, 0)
        self.radios[""] = only
        layout.addWidget(only)

        for index, (key, title) in enumerate(target_titles, start=1):
            radio = QRadioButton(f"Экспедиторство + {title}")
            self._group.addButton(radio, index)
            self.radios[key] = radio
            layout.addWidget(radio)

        form = QFormLayout()
        self.amount_label = QLabel("")
        self.amount = QDoubleSpinBox()
        self.amount.setDecimals(2)
        self.amount.setRange(0.0, 1_000_000_000.0)
        self.amount.setSingleStep(1000.0)
        self.amount.setGroupSeparatorShown(True)
        self.amount.setValue(0.0)
        form.addRow(self.amount_label, self.amount)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.buttons.button(QDialogButtonBox.Ok).setText("Создать")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        for radio in self.radios.values():
            radio.toggled.connect(self._on_mode_changed)
        self.amount.valueChanged.connect(self._on_amount_changed)

        self._refresh_amount_field()

    # ---------------------------------------------------------
    # Состояние диалога
    # ---------------------------------------------------------
    def selected_target(self) -> str:
        """Ключ выбранного типа-цели или "" (только Экспедиторство)."""
        for key, radio in self.radios.items():
            if radio.isChecked():
                return key
        return ""

    def choice(self) -> LinkedContractChoice:
        """Выбор оператора (после `exec_() == Accepted`)."""
        target = self.selected_target()
        if not target:
            return LinkedContractChoice()
        return LinkedContractChoice(
            target_type=target, amount=float(self.amount.value())
        )

    def _on_mode_changed(self, checked: bool = False) -> None:
        """Переключение режима: показать/спрятать поле суммы."""
        self._refresh_amount_field()

    def _on_amount_changed(self, value: float = 0.0) -> None:
        """Сумма изменилась: пересчитать доступность «Создать»."""
        self._refresh_ok()

    def _refresh_amount_field(self) -> None:
        """Поле суммы видно только для пары; подпись называет тип."""
        target = self.selected_target()
        linked = bool(target)
        self.amount_label.setVisible(linked)
        self.amount.setVisible(linked)
        if linked:
            title = self._titles.get(target, target)
            self.amount_label.setText(
                f"Сумма для «{title}» (итог с НДС), ₽:"
            )
        self._refresh_ok()

    def _refresh_ok(self) -> None:
        """«Создать» доступно, если сумма пары положительна."""
        button = self.buttons.button(QDialogButtonBox.Ok)
        if button is None:
            return
        target = self.selected_target()
        button.setEnabled(not target or self.amount.value() > 0)


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
            # 1. Только Экспедиторство или пара со связанным типом.
            choice = self._ask_linked_contract()
            if choice is None:
                logger.info("Создание договора отменено на выборе режима")
                self.statusBar().showMessage("Договор не создан — отменено", 5000)
                return

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

            if not choice.is_linked():
                self._show_contract_created(path)
                return

            logger.info(
                "Создание связанного договора: тип=%s", choice.target_type
            )
            try:
                result = self._create_linked_contract(choice)
            except Exception as e:  # noqa: BLE001 — первый договор уже создан
                logger.exception("Связанный договор: ошибка создания")
                audit.log_event("linked_contract_creation_failed", result="error")
                QMessageBox.critical(
                    self, "Связанный договор",
                    "Договор Экспедиторства создан, связанный — нет:\n"
                    f"{e}"
                )
                self._show_contract_created(path)
                return

            if result.is_created():
                audit.log_event(
                    "linked_contract_created",
                    target_type=choice.target_type,
                    file=os.path.basename(result.path),
                )
                self._show_pair_created(path, result.path)
            else:
                logger.warning(
                    "Связанный договор не создан: %s", result.reason
                )
                QMessageBox.warning(
                    self, "Связанный договор",
                    "Договор Экспедиторства создан.\n"
                    f"Связанный договор не создан: {result.reason}"
                )
                self._show_contract_created(path)
        except Exception as e:
            logger.exception("Ошибка при создании договора")
            audit.log_event("contract_creation_failed", result="error")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось создать договор:\n{e}"
            )

    # ---------------------------------------------------------
    # Связанные договоры (задача 2)
    # ---------------------------------------------------------
    def _linked_target_titles(self) -> List[Tuple[str, str]]:
        """
        Типы-цели для диалога: фабрики менеджера окон ∩ цели зеркала.

        Менеджер — единственный, кто знает, какие окна реально можно
        открыть; `supported_targets()` — те, у которых есть кнопка
        «Отзеркалить». Пересечение и даёт список «Экспедиторство + [Тип]».

        Пустой список — нормальный режим: менеджер не зарегистрирован
        (тесты, запуск без окна) или приватной части нет. Тогда диалог не
        показывается, и кнопка работает как раньше.
        """
        manager = current_window_manager()
        if manager is None:
            logger.debug("Связанные договоры: менеджер окон не зарегистрирован")
            return []

        try:
            available = tuple(manager.contract_types())
        except Exception as e:  # noqa: BLE001 — менеджер не должен ронять кнопку
            logger.error(
                "Связанные договоры: менеджер не отдал список типов (%s)",
                type(e).__name__,
            )
            return []

        try:
            targets = supported_targets()
        except Exception as e:  # noqa: BLE001 — реестр зеркала не обязателен
            logger.error(
                "Связанные договоры: список целей зеркала недоступен (%s)",
                type(e).__name__,
            )
            return []

        return [(key, picker_title(key)) for key in available if key in targets]

    def _ask_linked_contract(self) -> Optional[LinkedContractChoice]:
        """
        Спрашивает режим создания: только Экспедиторство или пара.

        :return: выбор оператора; None — «Отмена» (ничего не создаём).
            Если связывать нечего — режим «только Экспедиторство» без
            лишнего диалога.
        """
        titles = self._linked_target_titles()
        if not titles:
            return LinkedContractChoice()

        dialog = LinkedContractDialog(titles, self)
        if dialog.exec_() != QDialog.Accepted:
            self._log_ui_action("диалог связанных договоров закрыт без создания")
            return None

        choice = dialog.choice()
        self._log_ui_action(
            "диалог связанных договоров: режим",
            linked=choice.is_linked(),
        )
        logger.info(
            "Связанные договоры: режим выбран (связанный=%s)",
            choice.is_linked(),
        )
        return choice

    def _ask_overwrite_target(self, title: str) -> bool:
        """«Перезаписать данные в окне «[Тип]»?» — Да/Нет."""
        box = QMessageBox(self)
        box.setWindowTitle("Связанные договоры")
        box.setIcon(QMessageBox.Question)
        box.setText(f"Перезаписать данные в окне «{title}»?")
        box.setInformativeText(
            "В окне заполнены поля, которые изменит перенос из Экспедиторства.\n"
            "«Да» — заменить их данными рейса.\n"
            "«Нет» — оставить заполненное, но сумму подставить и документ создать."
        )

        yes_button = box.addButton("Да", QMessageBox.YesRole)
        no_button = box.addButton("Нет", QMessageBox.NoRole)
        box.setDefaultButton(yes_button)
        box.setEscapeButton(no_button)
        box.exec_()

        overwrite = box.clickedButton() is yes_button
        logger.info(
            "Связанные договоры: перезапись окна — %s",
            "да" if overwrite else "нет",
        )
        return overwrite

    def _create_linked_contract(self, choice: LinkedContractChoice,
                                source_window: Any = None) -> LinkedContractResult:
        """
        Зеркало → сумма → документ цели. Диалог успеха НЕ показывает.

        :param choice: выбранный тип и сумма (итог с НДС).
        :param source_window: источник данных; по умолчанию — это окно
            (Экспедиторство). Аргумент нужен тестам с подставным источником.
        :return: путь связанного документа или причина, почему его нет.
        """
        manager = current_window_manager()
        if manager is None:
            return LinkedContractResult(reason="менеджер окон недоступен")

        title = picker_title(choice.target_type)
        window = manager.window_for(choice.target_type)
        if window is None:
            return LinkedContractResult(
                reason=f"окно типа «{title}» недоступно"
            )

        source = source_window if source_window is not None else self
        plan = mirror_from_expedition(
            window, choice.target_type, main_window=source
        )
        if plan is None:
            return LinkedContractResult(
                reason="в Экспедиторстве нет данных для переноса (ВИН, адреса)"
            )

        # Вопрос «перезаписать?» — только если зеркало изменит поля, которые
        # заполнил ОПЕРАТОР. Заводские значения формы (сегодняшняя дата, имя
        # заказчика) вопрос не вызывают — их отсекает сравнение со снимком
        # вкладок, снятым при создании окна (см. base_window.mirror_conflicts).
        conflicts = window.mirror_conflicts(plan)
        if conflicts:
            if not self._ask_overwrite_target(title):
                logger.info(
                    "Связанные договоры: зеркало не применяется, "
                    "остаётся заполненное (тип=%s)", choice.target_type,
                )
                plan = None
        else:
            logger.info(
                "Связанные договоры: заполненных полей, которые изменит "
                "зеркало, нет — перенос без вопроса"
            )

        if plan is not None:
            window.apply_mirror_plan(plan, overwrite_existing=True)

        if not window.apply_price_amount(choice.amount):
            return LinkedContractResult(
                reason="не удалось подставить сумму в окно цели"
            )

        path = window.generate_document(confirm=True)
        if not path:
            return LinkedContractResult(
                reason="генерация отменена после проверки данных"
            )
        return LinkedContractResult(path=str(path))

    def _show_pair_created(self, expedition_path: str, linked_path: str):
        """
        «Оба договора созданы» + «Открыть папку».

        Папка открывается та, где лежит договор Экспедиторства. Если
        связанный документ попал в другую папку (оператор отказался от
        зеркала и заполнил своё окно), это видно в тексте сообщения.
        """
        expedition_folder = os.path.dirname(os.path.abspath(expedition_path))
        linked_folder = os.path.dirname(os.path.abspath(linked_path))

        lines = [
            os.path.basename(expedition_path),
            os.path.basename(linked_path),
            "",
            f"Папка: {expedition_folder}",
        ]
        if linked_folder != expedition_folder:
            lines.append(f"Второй документ: {linked_folder}")

        box = QMessageBox(self)
        box.setWindowTitle("Успех")
        box.setIcon(QMessageBox.Information)
        box.setText("Оба договора созданы")
        box.setInformativeText("\n".join(lines))

        open_button = box.addButton("Открыть папку", QMessageBox.ActionRole)
        box.addButton("OK", QMessageBox.AcceptRole)

        box.exec_()

        if box.clickedButton() is open_button:
            self._log_ui_action("диалог пары договоров: «Открыть папку»")
            self._open_folder(expedition_folder)
        else:
            self._log_ui_action("диалог пары договоров закрыт")

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
