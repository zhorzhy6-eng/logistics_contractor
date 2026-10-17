#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты окна ввода ключей API (ui/keys_dialog.py) — Часть 2 задания 18.10.2026.

Что проверяется
---------------
* окно создаётся, поля на месте, ссылки на личные кабинеты есть;
* проверка ввода: пустое обязательное поле — ошибка, «не Base64» — ошибка;
* сохранение: ключ уходит в хранилище БЕЗ пробелов и префикса «Basic»;
* отмена ничего не сохраняет;
* отказ хранилища не закрывает окно молча;
* при первом запуске окно не показывается в offscreen-прогонах — иначе
  `main.py` в тестах и CI повис бы на модальном окне до таймаута.

Keyring здесь всегда подменён: настоящий Windows Credential Manager тесты
не трогают — иначе прогон писал бы в систему и зависел от машины.

Qt поднимается в режиме offscreen (tests/ui/conftest.py).
"""

import base64
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QDialog, QLineEdit  # noqa: E402

from ui import keys_dialog  # noqa: E402
from ui.keys_dialog import (  # noqa: E402
    DADATA_CABINET_URL,
    GIGACHAT_CABINET_URL,
    KeysDialog,
    is_valid_base64_key,
    normalize_key,
    should_prompt_on_startup,
    validate_keys,
)

#: Настоящий по форме ключ GigaChat: Base64 от пары «client_id:secret».
VALID_GIGACHAT_KEY = base64.b64encode(
    b"01234567-89ab-cdef-0123-456789abcdef:fedcba98-7654-3210-fedc-ba9876543210"
).decode()

#: Внешний вид клавиши: то, что оператор видит у себя на клавиатуре.
DADATA_KEY = "1234567890abcdef1234567890abcdef12345678"
DADATA_SECRET = "abcdef1234567890abcdef1234567890abcdef12"


@pytest.fixture
def stored(monkeypatch):
    """
    Подменяет хранилище ключей: ничего не читает и не пишет наружу.

    Возвращает словарь с журналом вызовов: ``calls`` — что пытались
    сохранить, ``state`` — что «уже сохранено» (GigaChat, DaData, секрет).
    """
    journal = {
        "calls": {},
        "state": (False, False, False),
        "fail": False,
    }

    def _make_setter(name):
        def _setter(value):
            journal["calls"][name] = value
            return not journal["fail"]
        return _setter

    monkeypatch.setattr(
        keys_dialog.secrets_store, "set_gigachat_key", _make_setter("gigachat")
    )
    monkeypatch.setattr(
        keys_dialog.secrets_store, "set_dadata_key", _make_setter("dadata")
    )
    monkeypatch.setattr(
        keys_dialog.secrets_store, "set_dadata_secret", _make_setter("dadata_secret")
    )
    monkeypatch.setattr(
        keys_dialog.secrets_store, "has_gigachat_key",
        lambda: journal["state"][0],
    )
    monkeypatch.setattr(
        keys_dialog.secrets_store, "has_dadata_key",
        lambda: journal["state"][1],
    )
    monkeypatch.setattr(
        keys_dialog.secrets_store, "has_dadata_secret",
        lambda: journal["state"][2],
    )
    return journal


@pytest.fixture
def dialog(qt_app, stored):
    """Окно ввода ключей (закрывается после теста)."""
    widget = KeysDialog(require_gigachat=True)
    yield widget
    widget.close()
    widget.deleteLater()


# ─────────────────────────────────────────────────────────────
# 1. Проверка ввода — без Qt
# ─────────────────────────────────────────────────────────────

def test_normalize_key_strips_spaces_and_basic_prefix():
    """Ключ из буфера обмена: пробелы, переносы и «Basic » убираются."""
    assert normalize_key("  Basic abcdef==\n") == "abcdef=="
    assert normalize_key("ab cd\nef") == "abcdef"
    assert normalize_key(None) == ""


def test_valid_base64_key_is_accepted():
    """Настоящий по форме ключ GigaChat проходит проверку."""
    assert is_valid_base64_key(VALID_GIGACHAT_KEY) is True


def test_base64_key_without_padding_is_accepted():
    """Ключ без хвостовых «=» не отвергается: выравнивание добавляется само."""
    stripped = VALID_GIGACHAT_KEY.rstrip("=")
    assert stripped != VALID_GIGACHAT_KEY
    assert is_valid_base64_key(stripped) is True


def test_base64_key_with_basic_prefix_is_accepted():
    """Заголовок «Basic» перед ключом — не повод ругаться."""
    assert is_valid_base64_key(f"Basic {VALID_GIGACHAT_KEY}") is True


@pytest.mark.parametrize("value", [
    "",
    "   ",
    None,
    "не ключ, а текст",
    "abc",                       # короче минимальной длины
    "MDEyMzQ1Njc4OWFiY2RlZg!!!",  # символы вне алфавита Base64
])
def test_invalid_base64_keys_are_rejected(value):
    """Пустое, короткое и не-Base64 значение — не ключ."""
    assert is_valid_base64_key(value) is False


def test_validate_requires_gigachat_on_first_run():
    """Первый запуск: пустой ключ GigaChat — ошибка с названием поля."""
    errors = validate_keys("", "", "", require_gigachat=True)
    assert len(errors) == 1
    assert "GigaChat" in errors[0]


def test_validate_empty_without_requirement_asks_for_any_key():
    """Из настроек пустая форма — «введите хотя бы один ключ», а не молчание."""
    errors = validate_keys("", "", "", require_gigachat=False)
    assert errors and "хотя бы один" in errors[0]


def test_validate_reports_bad_base64():
    """Ключ GigaChat не в Base64 — ошибка про личный кабинет Sber."""
    errors = validate_keys("просто текст", "", "")
    assert errors and "Base64" in errors[0]


def test_validate_accepts_only_dadata_keys():
    """DaData без GigaChat — нормальный ввод: он нужен не всем."""
    assert validate_keys("", DADATA_KEY, "") == []


def test_validate_accepts_full_set():
    """Все три ключа корректны — ошибок нет."""
    assert validate_keys(VALID_GIGACHAT_KEY, DADATA_KEY, DADATA_SECRET) == []


def test_validate_ignores_empty_optional_fields():
    """Пустой секретный ключ DaData — не ошибка (поле опциональное)."""
    assert validate_keys(VALID_GIGACHAT_KEY, "", "") == []


# ─────────────────────────────────────────────────────────────
# 2. Окно: состав
# ─────────────────────────────────────────────────────────────

def test_dialog_is_created(dialog):
    """Окно создаётся и подписано как «Ключи API»."""
    assert dialog.objectName() == "keysDialog"
    assert "Ключи API" in dialog.windowTitle()


def test_dialog_has_three_key_fields(dialog):
    """Поля: GigaChat, DaData API key, DaData Secret key."""
    for name in ("gigachat_edit", "dadata_edit", "dadata_secret_edit"):
        field = getattr(dialog, name)
        assert isinstance(field, QLineEdit), f"нет поля {name}"
        assert field.echoMode() == QLineEdit.Password, f"{name}: ключ виден"


def test_dialog_has_save_and_cancel_buttons(dialog):
    """Кнопки «Сохранить» и «Отмена» на месте."""
    assert dialog.btn_save.text() == "Сохранить"
    assert dialog.btn_cancel.text() == "Отмена"


def test_dialog_shows_cabinet_links(dialog):
    """Ссылки на личные кабинеты Sber и DaData есть в окне."""
    assert GIGACHAT_CABINET_URL.startswith("https://developers.sber.ru")
    assert DADATA_CABINET_URL.startswith("https://dadata.ru")
    assert GIGACHAT_CABINET_URL in dialog.gigachat_link.text()
    assert DADATA_CABINET_URL in dialog.dadata_link.text()


def test_dialog_marks_gigachat_as_required_on_first_run(dialog):
    """При первом запуске подпись поля GigaChat помечена звёздочкой."""
    assert dialog.require_gigachat is True
    assert "*" in dialog.gigachat_label.text()


def test_dialog_without_requirement_has_no_star(qt_app, stored):
    """Их настроек обязательных полей нет: звёздочки у GigaChat тоже."""
    widget = KeysDialog()
    try:
        assert widget.require_gigachat is False
        assert "*" not in widget.gigachat_label.text()
    finally:
        widget.close()
        widget.deleteLater()


def test_dialog_says_keys_are_not_stored_yet(dialog, stored):
    """Пока ключей нет, под полями так и написано: «не сохранён»."""
    assert "не сохранён" in dialog.gigachat_hint.text()
    assert "не сохранён" in dialog.dadata_hint.text()


def test_dialog_marks_already_stored_keys(qt_app, monkeypatch):
    """Сохранённый ключ подписан «оставьте пустым, чтобы не менять»."""
    for name in ("has_gigachat_key", "has_dadata_key", "has_dadata_secret"):
        monkeypatch.setattr(keys_dialog.secrets_store, name, lambda: True)

    widget = KeysDialog()
    try:
        assert "уже сохранён" in widget.gigachat_hint.text()
        assert "уже сохранён" in widget.dadata_hint.text()
        assert "уже сохранён" in widget.dadata_secret_hint.text()
    finally:
        widget.close()
        widget.deleteLater()


# ─────────────────────────────────────────────────────────────
# 3. Валидация в окне
# ─────────────────────────────────────────────────────────────

def test_empty_required_field_blocks_saving(dialog, stored):
    """Пустое обязательное поле: окно не закрылось, в хранилище ничего не ушло."""
    dialog.gigachat_edit.setText("")
    dialog._on_save()

    assert dialog.result() != QDialog.Accepted
    assert "GigaChat" in dialog.error_label.text()
    assert not dialog.error_label.isHidden()
    assert stored["calls"] == {}


def test_bad_base64_blocks_saving(dialog, stored):
    """Не-Base64 ключ GigaChat: ошибка под полями, сохранения нет."""
    dialog.gigachat_edit.setText("это не ключ")
    dialog._on_save()

    assert dialog.result() != QDialog.Accepted
    assert "Base64" in dialog.error_label.text()
    assert stored["calls"] == {}


def test_save_writes_keys_without_spaces(dialog, stored):
    """Сохранение: ключ уходит в хранилище очищенным, окно закрывается."""
    dialog.gigachat_edit.setText(f" Basic {VALID_GIGACHAT_KEY} ")
    dialog.dadata_edit.setText(f" {DADATA_KEY} ")
    dialog.dadata_secret_edit.setText(DADATA_SECRET)

    dialog._on_save()

    assert dialog.result() == QDialog.Accepted
    assert stored["calls"] == {
        "gigachat": VALID_GIGACHAT_KEY,
        "dadata": DADATA_KEY,
        "dadata_secret": DADATA_SECRET,
    }
    assert dialog.saved_keys == {
        "gigachat": True, "dadata": True, "dadata_secret": True,
    }


def test_save_keeps_existing_key_when_field_is_empty(dialog, stored):
    """Пустое поле не трогает сохранённый ключ: меняется только введённое."""
    dialog.gigachat_edit.setText(VALID_GIGACHAT_KEY)
    dialog.dadata_edit.setText("")
    dialog.dadata_secret_edit.setText("")

    dialog._on_save()

    assert dialog.result() == QDialog.Accepted
    assert set(stored["calls"]) == {"gigachat"}


def test_cancel_does_not_save(dialog, stored):
    """«Отмена» не сохраняет ничего — даже если поля заполнены."""
    dialog.gigachat_edit.setText(VALID_GIGACHAT_KEY)
    dialog.dadata_edit.setText(DADATA_KEY)

    dialog.btn_cancel.click()

    assert dialog.result() != QDialog.Accepted
    assert stored["calls"] == {}
    assert dialog.saved_keys == {}


def test_storage_failure_keeps_dialog_open(dialog, stored):
    """Хранилище отказало — окно остаётся открытым и говорит, что не сохранилось."""
    stored["fail"] = True
    dialog.gigachat_edit.setText(VALID_GIGACHAT_KEY)

    dialog._on_save()

    assert dialog.result() != QDialog.Accepted
    assert "не сохранён" in dialog.error_label.text()
    assert dialog.saved_keys == {"gigachat": False}


def test_errors_are_cleared_after_successful_save(dialog, stored):
    """Ошибки не остаются на экране после удачного сохранения."""
    dialog.gigachat_edit.setText("плохой ключ")
    dialog._on_save()
    assert dialog.error_label.text()

    dialog.gigachat_edit.setText(VALID_GIGACHAT_KEY)
    dialog._on_save()

    assert dialog.error_label.text() == ""
    assert dialog.error_label.isHidden() is True


def test_error_does_not_change_dialog_width(dialog, stored):
    """Ошибка не растягивает окно: меняется высота, а не ширина."""
    dialog.show()
    width_before = dialog.width()

    dialog.gigachat_edit.setText("плохой ключ")
    dialog._on_save()

    assert dialog.width() == width_before
    assert dialog.error_label.text()


# ─────────────────────────────────────────────────────────────
# 4. Показ ключей и запуск программы
# ─────────────────────────────────────────────────────────────

def test_show_keys_switch_reveals_and_hides(dialog):
    """Флажок «Показать ключи» открывает значения и снова скрывает их."""
    dialog.show_keys_checkbox.setChecked(True)
    for field in (dialog.gigachat_edit, dialog.dadata_edit, dialog.dadata_secret_edit):
        assert field.echoMode() == QLineEdit.Normal

    dialog.show_keys_checkbox.setChecked(False)
    for field in (dialog.gigachat_edit, dialog.dadata_edit, dialog.dadata_secret_edit):
        assert field.echoMode() == QLineEdit.Password


def test_dialog_not_shown_offscreen(qt_app):
    """В прогоне без экрана окно при запуске не показывается (иначе — таймаут)."""
    assert should_prompt_on_startup() is False


def test_dialog_shown_on_real_platform(qt_app, monkeypatch):
    """На обычной платформе окно при запуске показывается."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "windows")
    assert should_prompt_on_startup() is True


def test_ask_keys_accepts_dialog_immediately(qt_app, stored, monkeypatch):
    """`ask_keys` — одна дверь: показывает окно и возвращает «сохранил или нет»."""
    monkeypatch.setattr(KeysDialog, "exec_", lambda self: QDialog.Accepted)
    assert keys_dialog.ask_keys() is True


def test_ask_keys_returns_false_on_cancel(qt_app, stored, monkeypatch):
    """Отмена в `ask_keys` — False, а не исключение."""
    monkeypatch.setattr(KeysDialog, "exec_", lambda self: QDialog.Rejected)
    assert keys_dialog.ask_keys() is False


def test_startup_keys_are_not_asked_when_key_exists(qt_app, monkeypatch):
    """Ключ уже в хранилище — при запуске ничего не спрашиваем."""
    monkeypatch.setattr(sys, "argv", ["main.py"])
    import main

    monkeypatch.setattr(
        keys_dialog.secrets_store, "keyring_available", lambda: True
    )
    monkeypatch.setattr(
        keys_dialog.secrets_store, "has_gigachat_key", lambda: True
    )
    assert main._ask_keys_if_missing() is True


def test_startup_keys_are_not_asked_when_keyring_is_missing(qt_app, monkeypatch):
    """Хранилища нет — окно не показывается: сохранить ключ было бы некуда."""
    monkeypatch.setattr(sys, "argv", ["main.py"])
    import main

    monkeypatch.setattr(
        keys_dialog.secrets_store, "keyring_available", lambda: False
    )
    assert main._ask_keys_if_missing() is False


def test_startup_keys_are_not_asked_offscreen(qt_app, monkeypatch):
    """Ключа нет, но прогон без экрана — окно не показывается (не вешаем CI)."""
    monkeypatch.setattr(sys, "argv", ["main.py"])
    import main

    monkeypatch.setattr(
        keys_dialog.secrets_store, "keyring_available", lambda: True
    )
    monkeypatch.setattr(
        keys_dialog.secrets_store, "has_gigachat_key", lambda: False
    )
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    assert main._ask_keys_if_missing() is False


def test_startup_keys_dialog_is_required_and_returns_true(qt_app, monkeypatch):
    """Первый запуск: окно открывается с ОБЯЗАТЕЛЬНЫМ полем GigaChat."""
    monkeypatch.setattr(sys, "argv", ["main.py"])
    import main

    monkeypatch.setattr(
        keys_dialog.secrets_store, "keyring_available", lambda: True
    )
    monkeypatch.setattr(
        keys_dialog.secrets_store, "has_gigachat_key", lambda: False
    )
    monkeypatch.setattr(
        keys_dialog, "should_prompt_on_startup", lambda: True
    )

    seen = {}

    def _auto_accept(self):
        seen["require_gigachat"] = self.require_gigachat
        return QDialog.Accepted

    monkeypatch.setattr(KeysDialog, "exec_", _auto_accept)

    assert main._ask_keys_if_missing() is True
    assert seen["require_gigachat"] is True


def test_startup_keys_cancel_is_not_an_error(qt_app, monkeypatch):
    """Отказ от ввода ключей — не поломка: программа работает без GigaChat."""
    monkeypatch.setattr(sys, "argv", ["main.py"])
    import main

    monkeypatch.setattr(
        keys_dialog.secrets_store, "keyring_available", lambda: True
    )
    monkeypatch.setattr(
        keys_dialog.secrets_store, "has_gigachat_key", lambda: False
    )
    monkeypatch.setattr(
        keys_dialog, "should_prompt_on_startup", lambda: True
    )
    monkeypatch.setattr(KeysDialog, "exec_", lambda self: QDialog.Rejected)

    assert main._ask_keys_if_missing() is False


# ─────────────────────────────────────────────────────────────
# 5. Кнопка в диалоге настроек
# ─────────────────────────────────────────────────────────────

def test_settings_dialog_has_keys_button(qt_app, monkeypatch):
    """В настройках есть кнопка «Ключи API…» — поменять ключи можно позже."""
    from ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(
        "ui.settings_dialog.ask_keys", lambda parent=None: False
    )
    widget = SettingsDialog({})
    try:
        assert widget.btn_edit_keys.text() == "Ключи API…"
        assert widget.btn_edit_keys.isEnabled()
    finally:
        widget.close()
        widget.deleteLater()


def test_settings_button_opens_keys_dialog(qt_app, monkeypatch):
    """Нажатие кнопки открывает окно ключей (и обновляет статус)."""
    from ui.settings_dialog import SettingsDialog

    opened = []
    monkeypatch.setattr(
        "ui.settings_dialog.ask_keys",
        lambda parent=None: opened.append(parent) or False,
    )
    widget = SettingsDialog({})
    try:
        widget.btn_edit_keys.click()
        assert len(opened) == 1
        assert opened[0] is widget
    finally:
        widget.close()
        widget.deleteLater()
