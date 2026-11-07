#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог «Только Экспедиторство / Экспедиторство + [Тип]» (задача 2).

Проверяется только сам выбор режима: какие радиокнопки построены, когда
появляются поля формы, НДС и суммы, что отдаёт `choice()` и когда доступна
кнопка «Создать». Поток создания пары договоров — в
`tests/ui/main_window/test_contract_pair_creation.py`, передача формы и
НДС в окно-цель — в `test_contract_pair_price_terms.py`.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QDialogButtonBox,
)

from ui.main_window.contract import (  # noqa: E402
    ENTITY_CHOICES, LINKED_VAT_CHOICES, ONLY_EXPEDITION_LABEL,
    LinkedContractChoice, LinkedContractDialog,
)


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


#: Типы-цели так, как их отдал бы менеджер окон (ключ, подпись).
TITLES = (("formika", "Формика"), ("logistiks_rus", "Логистикс Рус"))


@pytest.fixture
def dialog(qt_app):
    return LinkedContractDialog(TITLES)


# ─────────────────────────────────────────────────────────────
# Разметка
# ─────────────────────────────────────────────────────────────

def test_dialog_has_only_expedition_and_each_target(dialog):
    assert set(dialog.radios) == {"", "formika", "logistiks_rus"}
    assert dialog.radios[""].text() == ONLY_EXPEDITION_LABEL
    assert dialog.radios["formika"].text() == "Экспедиторство + Формика"
    assert (
        dialog.radios["logistiks_rus"].text()
        == "Экспедиторство + Логистикс Рус"
    )


def test_only_expedition_is_default(dialog):
    assert dialog.radios[""].isChecked() is True
    assert dialog.selected_target() == ""
    assert dialog.amount.isHidden() is True
    assert dialog.entity_type.isHidden() is True
    assert dialog.vat_rate.isHidden() is True
    assert dialog.choice() == LinkedContractChoice()


def test_single_target_dialog_has_two_modes(qt_app):
    dialog = LinkedContractDialog((("formika", "Формика"),))

    assert set(dialog.radios) == {"", "formika"}


# ─────────────────────────────────────────────────────────────
# Форма и НДС связанного договора (задача 3)
# ─────────────────────────────────────────────────────────────

def test_form_and_vat_lists_are_as_ordered(dialog):
    """Форма — ООО/ИП; НДС — четыре пункта задания из списка ядра."""
    assert [
        dialog.entity_type.itemText(i)
        for i in range(dialog.entity_type.count())
    ] == list(ENTITY_CHOICES) == ["ООО", "ИП"]
    assert [
        dialog.vat_rate.itemText(i)
        for i in range(dialog.vat_rate.count())
    ] == list(LINKED_VAT_CHOICES) == ["Без НДС", "0%", "10%", "22%"]


def test_factory_defaults_are_ooo_and_22(qt_app):
    """Без данных Экспедиторства диалог показывает заводские значения."""
    dialog = LinkedContractDialog(TITLES)

    assert dialog.entity_type.currentText() == "ООО"
    assert dialog.vat_rate.currentText() == "22%"


def test_defaults_come_from_expedition_window(qt_app):
    """Переданные значения Экспедиторства становятся выбором диалога."""
    dialog = LinkedContractDialog(
        TITLES, None, entity_type="ИП", vat_rate="Без НДС"
    )

    assert dialog.entity_type.currentText() == "ИП"
    assert dialog.vat_rate.currentText() == "Без НДС"


def test_unknown_defaults_fall_back_to_core_values(qt_app):
    """Мусор в подсказке не попадает в поля: берутся значения ядра."""
    dialog = LinkedContractDialog(
        TITLES, None, entity_type="АО", vat_rate="20%"
    )

    assert dialog.entity_type.currentText() == "ООО"
    assert dialog.vat_rate.currentText() == "22%"


def test_vat_outside_the_four_is_kept_as_default(qt_app):
    """Ставка УСН из Экспедиторства не подменяется на 22% молча."""
    dialog = LinkedContractDialog(TITLES, None, vat_rate="5%")

    assert dialog.vat_rate.currentText() == "5%"
    assert "5%" in [
        dialog.vat_rate.itemText(i) for i in range(dialog.vat_rate.count())
    ]


def test_target_shows_form_vat_and_amount_fields(dialog):
    dialog.radios["formika"].setChecked(True)

    assert dialog.selected_target() == "formika"
    assert dialog.entity_type.isHidden() is False
    assert dialog.vat_rate.isHidden() is False
    assert dialog.amount.isHidden() is False
    assert "Формика" in dialog.amount_label.text()
    assert "Сумма (Итог)" in dialog.amount_label.text()


def test_switching_back_hides_form_vat_and_amount(dialog):
    dialog.radios["formika"].setChecked(True)
    dialog.radios[""].setChecked(True)

    assert dialog.entity_type.isHidden() is True
    assert dialog.vat_rate.isHidden() is True
    assert dialog.amount.isHidden() is True


# ─────────────────────────────────────────────────────────────
# Поле суммы
# ─────────────────────────────────────────────────────────────

def test_amount_label_follows_selected_target(dialog):
    dialog.radios["formika"].setChecked(True)
    assert "Формика" in dialog.amount_label.text()

    dialog.radios["logistiks_rus"].setChecked(True)

    assert dialog.selected_target() == "logistiks_rus"
    assert "Логистикс Рус" in dialog.amount_label.text()


def test_switching_back_hides_amount_field(dialog):
    dialog.radios["formika"].setChecked(True)
    dialog.amount.setValue(150000.0)

    dialog.radios[""].setChecked(True)

    assert dialog.amount.isHidden() is True
    assert dialog.choice() == LinkedContractChoice()


def test_create_is_disabled_until_amount_is_positive(dialog):
    create = dialog.buttons.button(QDialogButtonBox.Ok)
    assert create.isEnabled() is True  # режим «только Экспедиторство»

    dialog.radios["formika"].setChecked(True)
    assert create.isEnabled() is False, "пару без суммы создавать нельзя"

    dialog.amount.setValue(150000.0)
    assert create.isEnabled() is True


# ─────────────────────────────────────────────────────────────
# Результат
# ─────────────────────────────────────────────────────────────

def test_choice_returns_target_and_amount(dialog):
    dialog.radios["logistiks_rus"].setChecked(True)
    dialog.amount.setValue(212500.50)

    choice = dialog.choice()

    assert choice.target_type == "logistiks_rus"
    assert choice.amount == pytest.approx(212500.50)
    assert choice.is_linked() is True


def test_choice_returns_form_and_vat(dialog):
    """«ИП» и «Без НДС» доезжают до выбора оператора без искажений."""
    dialog.radios["logistiks_rus"].setChecked(True)
    dialog.amount.setValue(150000.0)
    dialog.entity_type.setCurrentText("ИП")
    dialog.vat_rate.setCurrentText("Без НДС")

    choice = dialog.choice()

    assert choice.entity_type == "ИП"
    assert choice.vat_rate == "Без НДС"


def test_choice_without_target_has_zero_amount(dialog):
    dialog.amount.setValue(1000.0)  # значение поля ни на что не влияет
    dialog.radios[""].setChecked(True)

    assert dialog.choice() == LinkedContractChoice()
    assert dialog.choice().is_linked() is False


def test_cancel_button_rejects(dialog):
    dialog.buttons.rejected.emit()

    assert dialog.result() == QDialog.Rejected


def test_create_button_accepts(dialog):
    dialog.radios["formika"].setChecked(True)
    dialog.amount.setValue(1000.0)

    dialog.buttons.accepted.emit()

    assert dialog.result() == QDialog.Accepted
