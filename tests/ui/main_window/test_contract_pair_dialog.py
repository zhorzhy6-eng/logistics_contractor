#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диалог «Только Экспедиторство / Экспедиторство + [Тип]» (задача 2).

Проверяется только сам выбор режима: какие радиокнопки построены, когда
появляется поле суммы, что отдаёт `choice()` и когда доступна кнопка
«Создать». Поток создания пары договоров — в
`tests/ui/main_window/test_contract_pair_creation.py`.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QDialogButtonBox,
)

from ui.main_window.contract import (  # noqa: E402
    ONLY_EXPEDITION_LABEL, LinkedContractChoice, LinkedContractDialog,
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
    assert dialog.choice() == LinkedContractChoice()


def test_single_target_dialog_has_two_modes(qt_app):
    dialog = LinkedContractDialog((("formika", "Формика"),))

    assert set(dialog.radios) == {"", "formika"}


# ─────────────────────────────────────────────────────────────
# Поле суммы
# ─────────────────────────────────────────────────────────────

def test_target_shows_amount_field_with_its_type(dialog):
    dialog.radios["formika"].setChecked(True)

    assert dialog.selected_target() == "formika"
    assert dialog.amount.isHidden() is False
    assert "Формика" in dialog.amount_label.text()
    assert "НДС" in dialog.amount_label.text()


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
