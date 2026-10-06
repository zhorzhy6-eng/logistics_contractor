#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Арендодатель» окна типа «Разовая аренда» (ЭТАП 3.1.D.B.2).

Арендодатель — вторая сторона договора; в образце это ООО «ЛЦ». Поля те же,
что у Арендатора (ui/windows/arenda_ts/tabs/lessee_tab.py), кроме двух:
  * нет вида стороны: бланки рассчитаны на Арендодателя-ООО, и вариант
    выбирает Арендатор (у ИП-Арендатора бланк всё равно печатает
    Арендодателя как ООО — см. ArendaTsGenerator._fill_lessor);
  * нет КПП: плейсхолдера lessor_kpp нет ни в одном бланке, и генератор его
    не заполняет (data.py::_LESSOR_FIELDS — поля Арендатора без КПП).

Ключи get_data() — full_name, short_name, inn, ogrn, address,
actual_address, account, bik, bank, corr_account, email, edo,
director_position, director_name, basis — читает
ui/windows/arenda_ts/data.py::_build_lessor.

Кнопки «🔎» (только по нажатию, без автозаполнения при вводе):
  * у поля ИНН — реквизиты организации (DadataFillMixin);
  * у поля БИК — банк и корр. счёт (DadataBankMixin).
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import QSize, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QMessageBox, QScrollArea, QVBoxLayout, QWidget,
)

from core.dadata_client import bank_status_warning
from ui import theme
from ui.db_manager_dialog import (
    OPEN_TAB_CARRIERS,
    DbManagerDialog,
)
from ui.icons import action_icon
from ui.tabs.base_tab import DadataBankMixin, DadataFillMixin
from ui.widgets import (
    PasteableLineEdit, PasteableTextEdit, RecognitionPanel,
)
from ui.windows.arenda_ts import contacts as _contacts
from ui.windows.arenda_ts.contacts import SaveResult

logger = logging.getLogger("ui.windows.arenda_ts.tabs.lessor_tab")

#: Основание полномочий Арендодателя — та же константа, что подставляет
#: генератор (ArendaTsGenerator.LESSOR_BASIS) и data.py::BASIS_OOO.
BASIS_OOO = "Устава"

#: Длина реквизитов (как в бланке): ОГРН — 15 цифр, счёт — 20, БИК — 9.
OGRN_MAX_LENGTH = 15
ACCOUNT_MAX_LENGTH = 20
BIK_LENGTH = 9


class LessorTab(DadataFillMixin, DadataBankMixin, QWidget):
    """Реквизиты Арендодателя (второй стороны договора)."""

    # Сигнал для передачи текста в окно на распознавание
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    #: Название вкладки для сообщений и логов DaData
    DADATA_TAB_TITLE = "Арендодатель"

    #: Текстовые поля вкладки: ровно те ключи, которые читает сборка данных
    #: (_LESSOR_FIELDS в data.py). Адреса заполняются отдельно — они QTextEdit.
    FIELDS = (
        "full_name", "short_name", "inn", "ogrn",
        "account", "bik", "bank", "corr_account", "email", "edo",
        "director_position", "director_name", "basis",
    )

    #: Поля, которые заполняет DaData, → поля формы вкладки. КПП у
    #: Арендодателя нет, поэтому в раскладке его тоже нет.
    DADATA_FIELDS = {
        "full_name": "full_name",
        "short_name": "short_name",
        "inn": "inn",
        "ogrn": "ogrn",
        "legal_address": "address",
        "director_name": "director_name",
        "director_position": "director_position",
        "email": "email",
    }

    def __init__(self):
        super().__init__()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)

        # ── Панель распознавания ──
        self.recognition_panel = RecognitionPanel(
            placeholder="Вставьте текст с реквизитами Арендодателя..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Справочник организаций (ШАГ FIX-1-T2) ──
        # Кнопки стоят сверху, рядом с панелью распознавания: у вкладки три
        # источника реквизитов (вручную, распознаванием и из базы), и видно
        # их должно быть сразу. База общая с «Экспедиторством».
        layout.addWidget(self._build_directory_panel())

        # ── Блок 1. Общие сведения ──
        general_group, general_layout = theme.section_box("Общие сведения")

        self.full_name = PasteableLineEdit("ООО «ЛЦ»")
        self.full_name.set_required(True)
        general_layout.addRow(
            theme.required_label("Полное наименование"), self.full_name
        )

        self.short_name = PasteableLineEdit("ООО «ЛЦ»")
        general_layout.addRow(
            theme.make_label("Сокращённое наименование"), self.short_name
        )

        self.inn = PasteableLineEdit("7701234567")
        self.inn.setMaxLength(12)
        self.inn.set_required(True)
        # Кнопка «🔎» — реквизиты организации по ИНН (DaData).
        general_layout.addRow(
            theme.required_label("ИНН"), self._setup_dadata_fill(self.inn)
        )

        self.ogrn = PasteableLineEdit("1027700132195")
        self.ogrn.setMaxLength(OGRN_MAX_LENGTH)
        self.ogrn.set_required(True)
        general_layout.addRow(
            theme.required_label("ОГРН"), self.ogrn
        )

        layout.addWidget(general_group)

        # ── Блок 2. Адреса ──
        address_group, address_layout = theme.section_box("Адреса")

        self.address = PasteableTextEdit("Юридический адрес", max_height=54)
        self.address.set_required(True)
        address_layout.addRow(
            theme.required_label("Юридический адрес"), self.address
        )

        self.actual_address = PasteableTextEdit("Фактический адрес", max_height=54)
        address_layout.addRow(
            theme.make_label("Фактический адрес"), self.actual_address
        )

        layout.addWidget(address_group)

        # ── Блок 3. Банковские реквизиты ──
        bank_group, bank_layout = theme.section_box("Банковские реквизиты")

        self.account = PasteableLineEdit("40702810000000000003")
        self.account.setMaxLength(ACCOUNT_MAX_LENGTH)
        self.account.set_required(True)
        bank_layout.addRow(theme.required_label("Расчётный счёт"), self.account)

        self.bik = PasteableLineEdit("044525226")
        self.bik.setMaxLength(BIK_LENGTH)
        self.bik.set_required(True)
        # Кнопка «🔎» — банк и корр. счёт по БИК (DaData).
        bank_layout.addRow(
            theme.required_label("БИК"), self._setup_dadata_bank_fill(self.bik)
        )

        self.bank = PasteableLineEdit("АО «Банк Второй»")
        bank_layout.addRow(theme.make_label("Наименование банка"), self.bank)

        self.corr_account = PasteableLineEdit("30101810400000000226")
        self.corr_account.setMaxLength(ACCOUNT_MAX_LENGTH)
        bank_layout.addRow(theme.make_label("Корр. счёт"), self.corr_account)

        layout.addWidget(bank_group)

        # ── Блок 4. Контакты ──
        contact_group, contact_layout = theme.section_box("Контакты")

        self.email = PasteableLineEdit("lessor@example.ru")
        contact_layout.addRow(theme.make_label("E-mail"), self.email)

        self.edo = PasteableLineEdit("3CD-8A42-5D60")
        contact_layout.addRow(theme.make_label("ЭДО"), self.edo)

        layout.addWidget(contact_group)

        # ── Блок 5. Руководитель ──
        director_group, director_layout = theme.section_box("Руководитель")

        self.director_position = PasteableLineEdit("Директор")
        director_layout.addRow(
            theme.make_label("Должность"), self.director_position
        )

        self.director_name = PasteableLineEdit("Петров Пётр Петрович")
        self.director_name.set_required(True)
        director_layout.addRow(
            theme.required_label("ФИО руководителя"), self.director_name
        )

        # Арендодатель в бланках всегда ООО: основание — устав, без выбора.
        self.basis = PasteableLineEdit(BASIS_OOO)
        director_layout.addRow(theme.make_label("Действует на основании"), self.basis)

        layout.addWidget(director_group)
        layout.addStretch()

        scroll.setWidget(content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll)

        # ── Панель действий внизу вкладки (ЭТАП 2B) ──
        self._tab_actions = self._build_tab_actions()
        main_layout.addWidget(self._tab_actions)

        logger.debug("Разовая аренда LessorTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Справочник организаций (ШАГ FIX-1-T2)
    # ─────────────────────────────────────────────────────────

    def _build_directory_panel(self) -> QFrame:
        """
        Панель «Из справочника» / «Сохранить в базу» для этой вкладки.

        Кнопки привязаны к вкладке, а не к «текущей стороне» окна: Арендодатель
        всегда читает и пишет таблицу carriers, Арендатор — customers. Поэтому
        роли не могут перепутаться, даже если открыты обе вкладки.
        """
        frame = QFrame()
        frame.setObjectName("directoryBar")
        panel = QHBoxLayout(frame)
        panel.setContentsMargins(12, 8, 12, 8)
        panel.setSpacing(8)

        self.btn_load_organization = theme.secondary_button(
            "Из справочника",
            tooltip="Выбрать Арендодателя из общего справочника организаций",
        )
        self.btn_load_organization.setIcon(action_icon("database.svg"))
        self.btn_load_organization.setIconSize(QSize(18, 18))
        # Слот — метод вкладки, без lambda: в connect она захватывалась бы
        # замыканием (цикл ссылок Python ↔ Qt).
        self.btn_load_organization.clicked.connect(self.load_from_directory)
        panel.addWidget(self.btn_load_organization)

        self.btn_save_organization = theme.secondary_button(
            "Сохранить в базу",
            tooltip="Сохранить реквизиты этой вкладки в общий справочник "
                    "организаций (дубль по ИНН обновляется)",
        )
        self.btn_save_organization.setIcon(action_icon("save.svg"))
        self.btn_save_organization.setIconSize(QSize(18, 18))
        self.btn_save_organization.clicked.connect(self.on_save_clicked)
        panel.addWidget(self.btn_save_organization)

        panel.addStretch()
        return frame

    def load_from_directory(self) -> None:
        """
        «Из справочника»: выбор Арендодателя в базе организаций.

        Открывается тот же диалог справочника, что кнопка «База данных» в
        «Экспедиторстве» (ui/db_manager_dialog.py), но в режиме выбора: видна
        одна вкладка — перевозчики (у Арендодателя с ними общая таблица),
        запись уходит в _fill_organization, а не в чужую форму.
        """
        dialog = DbManagerDialog(
            self,
            open_tab=OPEN_TAB_CARRIERS,
            on_pick=self._fill_organization,
        )
        if dialog.exec_():
            logger.info("Арендодатель: запись выбрана в справочнике организаций")

    def on_save_clicked(self) -> None:
        """Нажатие «Сохранить в базу»: сохраняет и рассказывает об итоге."""
        result = self.save_to_directory()
        if result.ok:
            QMessageBox.information(
                self, "Справочник организаций",
                result.message("Арендодатель"),
            )
            return

        QMessageBox.warning(
            self, "Справочник организаций", result.message("Арендодатель")
        )

    # ─────────────────────────────────────────────────────────
    # Данные вкладки
    # ─────────────────────────────────────────────────────────

    def _dadata_values(self, data: Dict[str, Any]) -> Dict[str, str]:
        """
        Поля формы ← данные DaData (см. DADATA_FIELDS).

        Имена полей у вкладки свои: адрес бланка — address (в DaData
        legal_address), счёт — account, банк — bank. Общая раскладка
        DadataFillMixin рассчитана на вкладку «Перевозчик» и здесь не
        подходит: она искала бы поля legal_address и bank_name, которых
        у вкладки аренды нет. Пустые значения пропускаются — незаполненные
        поля DaData не должны стирать введённое вручную.
        """
        source = data if isinstance(data, dict) else {}
        values: Dict[str, str] = {}
        for source_key, form_field in self.DADATA_FIELDS.items():
            value = str(source.get(source_key) or "").strip()
            if value:
                values[form_field] = value
        return values

    def _apply_dadata_bank(self, data: Dict[str, Any]) -> None:
        """
        Заполняет название банка и корреспондентский счёт по БИК.

        Расчётный счёт DaData не возвращает — его заполняют вручную; об этом
        сказано в итоговом диалоге. Непустые поля перезаписываются только
        после подтверждения (общая логика _fill_dadata_fields).
        """
        source = data if isinstance(data, dict) else {}
        values: Dict[str, str] = {}
        for source_key, form_field in (
            ("bank_name", "bank"), ("correspondent_account", "corr_account"),
        ):
            value = str(source.get(source_key) or "").strip()
            if value:
                values[form_field] = value

        filled, replaced, skipped = self._fill_dadata_fields(values)

        # Только количество полей — без БИК и названия банка.
        logger.info("DaData: банк найден, заполнено полей: %s", len(filled))

        lines = self._dadata_summary_lines(filled, replaced, skipped)
        lines.append("")
        lines.append("Расчётный счёт DaData не возвращает — заполните его вручную.")

        warning = bank_status_warning(str(source.get("state") or ""))
        if warning:
            logger.warning(
                "DaData: банк со статусом, отличным от ACTIVE (вкладка %s)",
                self.DADATA_TAB_TITLE,
            )
        self._show_dadata_summary(lines, warning)

    def get_data(self) -> Dict[str, Any]:
        """
        Реквизиты Арендодателя (ключи — как ждёт сборка данных).

        Адрес вкладки — юридический адрес бланка (сборка переводит его в
        lessor.legal_address), счёт — bank_account, банк — bank_name.
        """
        return {
            "full_name": self.full_name.text().strip(),
            "short_name": self.short_name.text().strip(),
            "inn": self.inn.text().strip(),
            "ogrn": self.ogrn.text().strip(),
            "address": self.address.toPlainText().strip(),
            "actual_address": self.actual_address.toPlainText().strip(),
            "account": self.account.text().strip(),
            "bik": self.bik.text().strip(),
            "bank": self.bank.text().strip(),
            "corr_account": self.corr_account.text().strip(),
            "email": self.email.text().strip(),
            "edo": self.edo.text().strip(),
            "director_position": self.director_position.text().strip(),
            "director_name": self.director_name.text().strip(),
            "basis": self.basis.text().strip(),
        }

    def fill_data(self, data: Dict[str, Any], replace: bool = False) -> None:
        """
        Заполняет реквизиты Арендодателя.

        Пустые значения игнорируются: распознавание приходит неполным, и
        введённые вручную реквизиты не должны исчезать. Адрес принимается и
        как address, и как legal_address: промпт отдаёт блок lessor целиком.

        replace=True — полная замена формы (загрузка записи из справочника):
        сначала форма очищается, поэтому незаполненные в записи поля
        остаются пустыми, а не от прошлой организации (как в Expediting,
        где _load_customer_from_db делает clear() перед fill_data).
        """
        if not data:
            return

        if replace:
            self.clear()

        for field in self.FIELDS:
            value = str(data.get(field) or "").strip()
            if value:
                getattr(self, field).setText(value)

        address = str(
            data.get("address") or data.get("legal_address") or ""
        ).strip()
        if address:
            self.address.setPlainText(address)

        actual_address = str(data.get("actual_address") or "").strip()
        if actual_address:
            self.actual_address.setPlainText(actual_address)

        logger.info("Разовая аренда: данные Арендодателя заполнены")

    def _fill_organization(self, record: Dict[str, Any]) -> None:
        """
        Заполняет форму записью из справочника организаций (ШАГ FIX-1-T2).

        Запись приходит из общей базы («Экспедиторство» и аренда работают
        с одними таблицами: customers у Арендатора, carriers у Арендодателя).
        Раскладка имён полей — в ui/windows/arenda_ts/contacts.py, здесь
        только замена формы: пустые поля записи обнуляют поле вкладки, иначе
        на ней остались бы реквизиты предыдущей организации.
        """
        values = _contacts.organization_to_form(record)
        if not values:
            return

        self.fill_data(values, replace=True)
        logger.info("Арендодатель: реквизиты загружены из справочника")

    def save_to_directory(self) -> SaveResult:
        """
        «Сохранить в базу»: пишет реквизиты вкладки в общий справочник.

        Роль этой вкладки — Арендодатель, поэтому запись уходит в таблицу
        carriers: та же, что у перевозчика в «Экспедиторстве». Дубль по ИНН
        обновляется, а не создаётся заново.
        """
        return _contacts.save_organization_record(
            self.get_data(), _contacts.ROLE_LESSOR
        )

    def clear(self) -> None:
        """Очищает реквизиты Арендодателя."""
        self.full_name.clear()
        self.short_name.clear()
        self.inn.clear()
        self.ogrn.clear()
        self.address.clear()
        self.actual_address.clear()
        self.account.clear()
        self.bik.clear()
        self.bank.clear()
        self.corr_account.clear()
        self.email.clear()
        self.edo.clear()
        self.director_position.clear()
        self.director_name.clear()
        self.basis.setText(BASIS_OOO)
        self.recognition_panel.clear()

        logger.debug("Разовая аренда: поля Арендодателя очищены")


__all__ = [
    "LessorTab",
    "BASIS_OOO",
    "OGRN_MAX_LENGTH",
    "ACCOUNT_MAX_LENGTH",
    "BIK_LENGTH",
    "SaveResult",
]
