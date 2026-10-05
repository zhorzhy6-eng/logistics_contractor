#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вкладка «Арендатор» окна типа «Разовая аренда» (ЭТАП 3.1.D.B.2).

Арендатор в этом типе — НАША сторона (в заявке на перевозку она называется
Экспедитором), Арендодатель — вторая. Реквизиты обеих сторон печатаются в
разделах 1.1 / 1.2 и 9 бланка, поэтому набор полей взят с вкладки
организации (ui/tabs/carrier_tab.py): наименования, ИНН, КПП, ОГРН(ИП),
адреса, банк, руководитель и основание полномочий.

Отличие от «Перевозчика» — вид Арендатора: он выбирается здесь
(«ООО» / «ИП с НДС» / «ИП без НДС») и определяет вариант бланка. От вида
зависит КПП: у индивидуального предпринимателя его не существует, поэтому
у ИП строка «КПП» убирается целиком и очищается (вернувшись к ООО, номер
вводят заново), ОГРН меняется на ОГРНИП, а основание полномочий
переключается автоматически: у ООО — «Устава», у ИП — «свидетельства о
государственной регистрации».

Ключи get_data() — carrier_type, full_name, short_name, inn, kpp, ogrn,
address, account, bik, bank, corr_account, email, edo, director_position,
director_name, basis — читает
ui/windows/arenda_ts/data.py::_build_lessee (имена полей вкладки совпадают
с именами в _LESSEE_FIELDS).

Кнопки «🔎» (только по нажатию, без автозаполнения при вводе):
  * у поля ИНН — реквизиты организации (DadataFillMixin);
  * у поля БИК — банк и корр. счёт (DadataBankMixin).
"""

import logging
from typing import Any, Dict

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QScrollArea, QVBoxLayout, QWidget,
)

from core.dadata_client import bank_status_warning
from ui import theme
from ui.tabs.base_tab import DadataBankMixin, DadataFillMixin
from ui.widgets import (
    PasteableLineEdit, PasteableTextEdit, RecognitionPanel,
)

logger = logging.getLogger("ui.windows.arenda_ts.tabs.lessee_tab")

#: Вид Арендатора — значения совпадают с ui/windows/arenda_ts/data.py
#: (CARRIER_TYPE_OOO / CARRIER_TYPE_IP_WITH_VAT / CARRIER_TYPE_IP_WITHOUT_VAT)
#: и с вариантами бланка генератора.
CARRIER_TYPES = ("ООО", "ИП с НДС", "ИП без НДС")

#: Вид по умолчанию — как в data.py::DEFAULT_CARRIER_TYPE.
CARRIER_TYPE_OOO = "ООО"
DEFAULT_CARRIER_TYPE = CARRIER_TYPE_OOO

#: Основание полномочий подставляется по виду Арендатора — те же константы,
#: что читает data.py::_registration (BASIS_OOO / BASIS_IP).
BASIS_OOO = "Устава"
BASIS_IP = "свидетельства о государственной регистрации"

#: Виды, у которых КПП существует (у ИП его не бывает).
TYPES_WITH_KPP = ("ООО",)

#: Длина реквизитов (как в бланке): КПП — 9 цифр, ОГРН(ИП) — 15, счёт — 20.
KPP_LENGTH = 9
OGRN_MAX_LENGTH = 15
ACCOUNT_MAX_LENGTH = 20
BIK_LENGTH = 9


class _NoWheelComboBox(QComboBox):
    """Не переключает вид Арендатора при прокрутке формы колёсиком."""

    def wheelEvent(self, event):
        event.ignore()


class LesseeTab(DadataFillMixin, DadataBankMixin, QWidget):
    """Реквизиты Арендатора (нашей стороны) и вид бланка."""

    # Сигнал для передачи текста в окно на распознавание
    recognize_requested = pyqtSignal(str)

    # ── Действия вкладки (ЭТАП 2B) ──
    create_contract_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    #: Название вкладки для сообщений и логов DaData
    DADATA_TAB_TITLE = "Арендатор"

    #: Текстовые поля вкладки, которые заполняются одним setText: ровно те
    #: ключи, которые читает сборка данных (_LESSEE_FIELDS в data.py).
    #: Адреса — отдельно: они QTextEdit и заполняются через setPlainText.
    FIELDS = (
        "full_name", "short_name", "inn", "kpp", "ogrn",
        "account", "bik", "bank", "corr_account", "email", "edo",
        "director_position", "director_name", "basis",
    )

    #: Поля, которые заполняет DaData, → поля формы вкладки: слева имена из
    #: core/dadata_client.py, справа ключи get_data(). Адрес вкладки — это
    #: юридический адрес бланка (address), счёт — account, банк — bank:
    #: сборка данных переводит их в legal_address / bank_account / bank_name
    #: (см. _LESSEE_FIELDS в ui/windows/arenda_ts/data.py). Поля телефон
    #: у вкладки нет — в бланке аренды его не печатают.
    DADATA_FIELDS = {
        "full_name": "full_name",
        "short_name": "short_name",
        "inn": "inn",
        "kpp": "kpp",
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
            placeholder="Вставьте текст с реквизитами Арендатора (нашей стороны)..."
        )
        self.recognition_panel.recognize_requested.connect(self._on_recognize_requested)
        layout.addWidget(self.recognition_panel)

        # ── Блок 1. Вид Арендатора ──
        type_group, type_layout = theme.section_box("Вид Арендатора")
        self.carrier_type = _NoWheelComboBox()
        self.carrier_type.addItems(list(CARRIER_TYPES))
        self.carrier_type.setCurrentText(DEFAULT_CARRIER_TYPE)
        self.carrier_type.setToolTip(
            "Вид Арендатора выбирает вариант бланка: у ИП КПП не печатается, "
            "а основание полномочий — свидетельство о регистрации"
        )
        # Переключение вида: КПП у ИП не существует, основание меняется само.
        self.carrier_type.currentIndexChanged.connect(self._on_carrier_type_changed)
        type_layout.addRow(theme.required_label("Вид"), self.carrier_type)
        layout.addWidget(type_group)

        # ── Блок 2. Общие сведения ──
        general_group, general_layout = theme.section_box("Общие сведения")

        self.full_name = PasteableLineEdit("ООО «Логистик-Транс»")
        self.full_name.set_required(True)
        general_layout.addRow(
            theme.required_label("Полное наименование"), self.full_name
        )

        self.short_name = PasteableLineEdit("ООО «Логистик-Транс»")
        general_layout.addRow(
            theme.make_label("Сокращённое наименование"), self.short_name
        )

        self.inn = PasteableLineEdit("7707654321")
        self.inn.setMaxLength(12)
        self.inn.set_required(True)
        # Кнопка «🔎» — реквизиты организации по ИНН (DaData).
        general_layout.addRow(
            theme.required_label("ИНН"), self._setup_dadata_fill(self.inn)
        )

        self.kpp = PasteableLineEdit("770701001")
        self.kpp.setMaxLength(KPP_LENGTH)
        self.kpp.set_required(True)
        self._kpp_label = theme.required_label("КПП")
        general_layout.addRow(self._kpp_label, self.kpp)
        # Строка «КПП»: PyQt5 не возвращает QLayoutItem из addRow(label, field),
        # поэтому помним подпись и поле — по ним и прячется вся строка.
        self._kpp_row = (self._kpp_label, self.kpp)

        self.ogrn = PasteableLineEdit("1027700261234")
        self.ogrn.setMaxLength(OGRN_MAX_LENGTH)
        self.ogrn.set_required(True)
        # Метка госрегистрации зависит от вида: ОГРН у ООО, ОГРНИП у ИП.
        self._ogrn_label = theme.required_label("ОГРН")
        general_layout.addRow(self._ogrn_label, self.ogrn)

        layout.addWidget(general_group)

        # ── Блок 3. Адреса ──
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

        # ── Блок 4. Банковские реквизиты ──
        bank_group, bank_layout = theme.section_box("Банковские реквизиты")

        self.account = PasteableLineEdit("40702810000000000002")
        self.account.setMaxLength(ACCOUNT_MAX_LENGTH)
        self.account.set_required(True)
        bank_layout.addRow(theme.required_label("Расчётный счёт"), self.account)

        self.bik = PasteableLineEdit("044525225")
        self.bik.setMaxLength(BIK_LENGTH)
        self.bik.set_required(True)
        # Кнопка «🔎» — банк и корр. счёт по БИК (DaData).
        bank_layout.addRow(
            theme.required_label("БИК"), self._setup_dadata_bank_fill(self.bik)
        )

        self.bank = PasteableLineEdit("ПАО Сбербанк")
        bank_layout.addRow(theme.make_label("Наименование банка"), self.bank)

        self.corr_account = PasteableLineEdit("30101810400000000225")
        self.corr_account.setMaxLength(ACCOUNT_MAX_LENGTH)
        bank_layout.addRow(theme.make_label("Корр. счёт"), self.corr_account)

        layout.addWidget(bank_group)

        # ── Блок 5. Контакты ──
        contact_group, contact_layout = theme.section_box("Контакты")

        self.email = PasteableLineEdit("arenda@example.ru")
        contact_layout.addRow(theme.make_label("E-mail"), self.email)

        self.edo = PasteableLineEdit("2AE-7F31-4C50")
        contact_layout.addRow(
            theme.make_label("ЭДО"), self.edo
        )

        layout.addWidget(contact_group)

        # ── Блок 6. Руководитель ──
        director_group, director_layout = theme.section_box("Руководитель")

        self.director_position = PasteableLineEdit("Генеральный директор")
        director_layout.addRow(
            theme.make_label("Должность"), self.director_position
        )

        self.director_name = PasteableLineEdit("Сидоров Сидор Сидорович")
        self.director_name.set_required(True)
        director_layout.addRow(
            theme.required_label("ФИО руководителя"), self.director_name
        )

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

        # Вид по умолчанию — ООО: КПП на месте, основание «Устава».
        self._on_carrier_type_changed()

        logger.debug("Разовая аренда LesseeTab инициализирована")

    # ─────────────────────────────────────────────────────────
    # Вид Арендатора
    # ─────────────────────────────────────────────────────────

    def is_ooo(self) -> bool:
        """ООО ли текущий Арендатор (только у ООО есть КПП)."""
        return self.carrier_type.currentText().startswith(TYPES_WITH_KPP)

    def basis_for_type(self) -> str:
        """Основание полномочий для текущего вида Арендатора."""
        return BASIS_OOO if self.is_ooo() else BASIS_IP

    def _on_carrier_type_changed(self, index: int = 0) -> None:
        """
        Переключает КПП, метку госрегистрации и основание по виду Арендатора.

        У ИП строка «КПП» убирается целиком (и очищается — КПП у ИП не
        существует), ОГРН меняется на ОГРНИП. Основание подставляется
        автоматически, но только если в поле стоит автоподстановка:
        введённое человеком основание не перезаписывается.
        """
        is_ooo = self.is_ooo()
        self._set_kpp_visible(is_ooo)

        theme.set_label_required(
            self._ogrn_label, "ОГРН" if is_ooo else "ОГРНИП", True
        )

        if self._auto_basis_value():
            self.basis.setText(self.basis_for_type())

        # В лог — только выбранный вид: наименование и ИНН стороны не пишем.
        logger.debug(
            "Разовая аренда: вид Арендатора %r, КПП %s",
            self.carrier_type.currentText(),
            "печатается" if is_ooo else "не печатается",
        )

    def _set_kpp_visible(self, visible: bool) -> None:
        """
        Показывает строку «КПП» у ООО и убирает её у ИП.

        Значение поля при этом очищается: у индивидуального предпринимателя
        КПП не существует, и оставлять его в данных вкладки нельзя — в бланке
        ИП такого плейсхолдера нет (data.py::_build_lessee пропускает kpp).
        Вернувшись к ООО, номер вводят заново.
        """
        if not visible:
            self.kpp.clear()
        self._show_form_row(self._kpp_row, visible)

    @staticmethod
    def _show_form_row(row: Any, visible: bool) -> None:
        """
        Показывает или прячет строку формы вместе с подписью.

        Строка — пара (подпись, поле). Видимость ставится каждому виджету:
        у QFormLayout нет способа скрыть строку целиком, а спрятанное поле
        без подписи выглядело бы как обрывок формы.
        """
        for widget in row or ():
            if widget is not None and hasattr(widget, "setVisible"):
                widget.setVisible(visible)

    def _auto_basis_value(self) -> bool:
        """
        Стоит ли в поле основания автоподстановка (или пусто).

        Различить «Устава», подставленное вкладкой, и «Устава», введённое
        пользователем, нельзя — значений всего два. Зато видно обратное:
        любое другое основание («доверенности», «свидетельства…») введено
        человеком или пришло из распознавания, и переключать его нельзя.
        """
        return self.basis.text().strip() in ("", BASIS_OOO, BASIS_IP)

    def _apply_carrier_type(self, value: Any) -> None:
        """
        Ставит вид Арендатора из данных (пустое значение не трогает).

        Принимаются и точные значения вкладки («ООО», «ИП с НДС», «ИП без
        НДС»), и вид организации от распознавания («ООО», «ИП»): у «ИП» без
        уточнения о НДС остаётся вариант «ИП с НДС» — ставку налога видно на
        вкладке «Стоимость», и там же её можно поменять.
        """
        text = str(value or "").strip()
        if not text:
            return

        if text in CARRIER_TYPES:
            self.carrier_type.setCurrentText(text)
            return

        index = self.carrier_type.findText(CARRIER_TYPE_OOO)
        if "ИП" in text.upper() or "ПРЕДПРИНИМАТЕЛЬ" in text.upper():
            index = self.carrier_type.findText("ИП с НДС")
        if index >= 0:
            self.carrier_type.setCurrentIndex(index)

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
        Реквизиты Арендатора (ключи — как ждёт сборка данных).

        Адрес вкладки — это юридический адрес бланка (сборка переводит его в
        lessee.legal_address), счёт — bank_account, банк — bank_name.
        """
        return {
            "carrier_type": self.carrier_type.currentText(),
            "full_name": self.full_name.text().strip(),
            "short_name": self.short_name.text().strip(),
            "inn": self.inn.text().strip(),
            "kpp": self.kpp.text().strip(),
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

    def fill_data(self, data: Dict[str, Any]) -> None:
        """
        Заполняет реквизиты Арендатора.

        Пустые значения игнорируются: распознавание приходит неполным, и
        введённые вручную реквизиты не должны исчезать. Вид Арендатора
        принимается и как carrier_type, и как entity_type («ООО» / «ИП») —
        промпт отдаёт блок lessee целиком.
        """
        if not data:
            return

        self._apply_carrier_type(
            data.get("carrier_type") or data.get("entity_type")
        )

        for field in self.FIELDS:
            value = str(data.get(field) or "").strip()
            if value:
                getattr(self, field).setText(value)

        # Юридический адрес распознавания лежит и в address, и в legal_address.
        address = str(
            data.get("address") or data.get("legal_address") or ""
        ).strip()
        if address:
            self.address.setPlainText(address)

        actual_address = str(data.get("actual_address") or "").strip()
        if actual_address:
            self.actual_address.setPlainText(actual_address)

        logger.info("Разовая аренда: данные Арендатора заполнены")

    def clear(self) -> None:
        """Очищает реквизиты и возвращает вид Арендатора к ООО."""
        self.carrier_type.setCurrentText(DEFAULT_CARRIER_TYPE)
        self.full_name.clear()
        self.short_name.clear()
        self.inn.clear()
        self.kpp.clear()
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

        logger.debug("Разовая аренда: поля Арендатора очищены")


__all__ = [
    "LesseeTab",
    "CARRIER_TYPES",
    "CARRIER_TYPE_OOO",
    "DEFAULT_CARRIER_TYPE",
    "BASIS_OOO",
    "BASIS_IP",
    "TYPES_WITH_KPP",
    "KPP_LENGTH",
    "OGRN_MAX_LENGTH",
    "ACCOUNT_MAX_LENGTH",
    "BIK_LENGTH",
]
