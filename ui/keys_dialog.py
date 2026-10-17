#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Окно ввода ключей API: GigaChat и DaData.

Зачем окно
----------
Ключи лежат в системном хранилище (Windows Credential Manager, через
keyring — infrastructure/secrets/store.py). До этого шага их можно было
положить только командой в консоли: ``python set_key.py`` и
``python set_dadata_key.py``. Коллеге, которому передают программу, такой
путь не подходит: консоль ему не нужна вовсе.

Поэтому ключи вводятся здесь, а программа сама показывает это окно при
первом запуске, если ключа GigaChat нет (main.py::_ask_keys_if_missing).
Позже то же окно открывается из настроек кнопкой «Ключи API…».

Три правила, которых окно держится
----------------------------------
1. **Ключ не показывается и не пишется в файлы.** Поля — в режиме пароля,
   значение уходит только в keyring. Ни в журнал, ни в config/settings.json,
   ни в сообщения об ошибке сам ключ не попадает: в текстах — только
   название поля.
2. **Пустое поле — не ошибка, если ключ не обязателен.** DaData и его
   секретный ключ нужны не всем; оставленное пустым поле просто не
   трогает сохранённое значение.
3. **Отказ — не поломка.** «Отмена» ничего не сохраняет и не мешает
   программе работать: без GigaChat остаются генерация по шаблонам и ручное
   заполнение формы.

Почему содержимое в области прокрутки
-------------------------------------
Поля, подсказки о состоянии ключей, ссылки на личные кабинеты и блок
ошибок вместе выше низкого экрана (768 px хватает не всегда). Кнопки
живут ВНЕ прокручиваемой области: «Сохранить» и «Отмена» видны всегда.
Тот же приём — в диалоге настроек Максимилиана
(ui/agent_settings_dialog.py), и по той же причине: прошлый вариант
с `adjustSize` ужимал строки формы друг на друга.

Проверка ввода — отдельными функциями (validate_keys, is_valid_base64_key):
их проверяют тесты без Qt, и ими же пользуется кнопка «Сохранить», чтобы
логика проверки не разъезжалась с показом.
"""

import base64
import binascii
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget,
)

from core import secrets_store
from ui import theme

logger = logging.getLogger("ui.keys_dialog")

#: Личные кабинеты, откуда берутся ключи (пункт задания «ссылки на ЛК»).
GIGACHAT_CABINET_URL = "https://developers.sber.ru/portal/products/gigachat-api"
DADATA_CABINET_URL = "https://dadata.ru/api/"

#: Доля высоты рабочего стола, которую окно занимает максимум. Остальное
#: уходит в прокрутку: окно не должно уезжать нижним краем за экран.
MAX_SCREEN_SHARE = 0.8

#: Минимум для области прокрутки: ниже содержимое уже нечитаемо.
MIN_SCROLL_HEIGHT = 200

#: Запас высоты окна после подгонки под содержимое. Без него раскладка
#: округляет высоту на пиксель меньше нужной, и внизу окна появляется
#: полоса прокрутки с диапазоном в один пиксель — она выглядит как поломка.
LAYOUT_SLACK_PX = 4

#: Префикс, который оператор иногда копирует вместе с ключом: в документации
#: GigaChat заголовок выглядит как «Authorization: Basic <ключ>».
_BASIC_PREFIX_RE = re.compile(r"^\s*basic\s+", re.IGNORECASE)

#: Допустимые символы Base64 (классический алфавит с «+» и «/»).
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")

#: Сколько символов минимум в ключе GigaChat: «Authorization key» — это
#: Base64 от пары client_id:client_secret, то есть десятки символов.
#: Короткая строка Base64 формально корректна, но ключом быть не может.
GIGACHAT_KEY_MIN_LENGTH = 20


# ─────────────────────────────────────────────────────────────
# Проверка ввода (без Qt — проверяется тестами отдельно)
# ─────────────────────────────────────────────────────────────

def normalize_key(value: Optional[str]) -> str:
    """
    Значение поля в том виде, в каком его принимает хранилище.

    Убираются пробелы и переводы строк (ключ часто копируют с переносом),
    а также служебный префикс «Basic »: в Credential Manager должен лежать
    сам ключ, а не заголовок авторизации.
    """
    text = _BASIC_PREFIX_RE.sub("", str(value or ""))
    return "".join(text.split())


def is_valid_base64_key(value: Optional[str]) -> bool:
    """
    Похоже ли значение на Base64-ключ GigaChat.

    Проверяется именно ФОРМА (алфавит Base64 и успешная расшифровка), а не
    содержимое: ключ — секрет, и разбирать его на части незачем. Хвост «=»
    дополняется, если оператор скопировал ключ без выравнивания, — иначе
    честный ключ отвергался бы из-за одного недостающего символа.

    :return: True, если строка — Base64 и в ней не меньше
        ``GIGACHAT_KEY_MIN_LENGTH`` символов.
    """
    text = normalize_key(value)
    if len(text) < GIGACHAT_KEY_MIN_LENGTH:
        return False
    if not _BASE64_RE.match(text):
        return False

    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        return False
    return bool(raw)


def validate_keys(gigachat: Optional[str], dadata: Optional[str],
                  dadata_secret: Optional[str],
                  require_gigachat: bool = False) -> List[str]:
    """
    Ошибки ввода: пустой список — можно сохранять.

    :param gigachat: ключ GigaChat (Authorization key, Base64);
    :param dadata: ключ DaData (подсказки по ИНН и адресам);
    :param dadata_secret: секретный ключ DaData (cleaner-методы,
        необязателен);
    :param require_gigachat: True при первом запуске — поле обязательно.

    Текст ошибки называет ПОЛЕ, а не значение: ключ — секрет, и в подсказке
    ему делать нечего.
    """
    gigachat = normalize_key(gigachat)
    dadata = normalize_key(dadata)
    dadata_secret = normalize_key(dadata_secret)

    errors: List[str] = []

    if require_gigachat and not gigachat:
        errors.append(
            "Введите ключ GigaChat: без него не работают распознавание "
            "документов и Максимилиан."
        )
    if gigachat and not is_valid_base64_key(gigachat):
        errors.append(
            "Ключ GigaChat должен быть Base64-строкой из личного кабинета "
            "Sber Developer Studio (раздел «Authorization key»). "
            "Скопируйте ключ заново — целиком, без заголовка «Basic»."
        )

    if not (gigachat or dadata or dadata_secret) and not errors:
        errors.append("Введите хотя бы один ключ.")

    return errors


def keys_status_text() -> Tuple[bool, bool, bool]:
    """
    Что уже лежит в хранилище: (GigaChat, DaData, секрет DaData).

    Не раскрывает значения — только наличие. Нужен окну, чтобы подписать
    поля: «ключ уже сохранён, оставьте пустым, чтобы не менять».
    """
    return (
        bool(secrets_store.has_gigachat_key()),
        bool(secrets_store.has_dadata_key()),
        bool(secrets_store.has_dadata_secret()),
    )


def should_prompt_on_startup() -> bool:
    """
    Показывать ли окно ключей при запуске программы.

    Два условия, и оба — про возможность спросить человека:

    * **есть QApplication** — без приложения модальное окно не открыть;
    * **есть экран** (платформа не offscreen). Прогоны без экрана —
      тесты и CI — запускают main.py отдельным процессом и ждут, что
      он закроется сам; модальное окно повесило бы их до таймаута.
      Ключи в таких прогонах не вводят, поэтому спрашивать некого.
    """
    if QApplication.instance() is None:
        return False

    platform = str(os.environ.get("QT_QPA_PLATFORM") or "").strip().casefold()
    if platform == "offscreen":
        logger.info(
            "Окно ключей не показывается: платформа offscreen (прогон без экрана)"
        )
        return False

    return True


# ─────────────────────────────────────────────────────────────
# Окно
# ─────────────────────────────────────────────────────────────

class KeysDialog(QDialog):
    """
    Ввод ключей GigaChat и DaData.

    :param parent: родительское окно (из настроек — само окно настроек);
    :param require_gigachat: True при первом запуске — без ключа GigaChat
        окно не закроется кнопкой «Сохранить» (от «Отмены» это не мешает).

    После успешного сохранения в ``saved_keys`` лежит, что именно
    сохранилось: {'gigachat': True, 'dadata': False, 'dadata_secret': False}.
    Ключ, поле которого оставили пустым, в хранилище не трогается.
    """

    def __init__(self, parent: Optional[QWidget] = None,
                 require_gigachat: bool = False):
        super().__init__(parent)

        self.require_gigachat = bool(require_gigachat)
        #: Что реально сохранилось при последнем «Сохранить» (пусто — не было).
        self.saved_keys: Dict[str, bool] = {}

        self.setWindowTitle("🔑 Ключи API")
        self.setObjectName("keysDialog")
        self.setMinimumWidth(560)
        if parent is not None:
            self.setStyleSheet(theme.qss())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Ключи API")
        title.setObjectName("sectionHeading")
        layout.addWidget(title)

        # ── Прокручиваемое содержимое: поля, подсказки, ссылки ──
        self.scroll = QScrollArea()
        self.scroll.setObjectName("keysDialogScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        intro = QLabel(
            "Ключи хранятся в системном хранилище Windows и в файлы проекта "
            "не записываются. Без ключа GigaChat доступны генерация по "
            "шаблонам и ручное заполнение формы; распознавание документов и "
            "Максимилиан требуют ключа."
        )
        intro.setObjectName("mutedLabel")
        intro.setWordWrap(True)
        content_layout.addWidget(intro)

        stored_gigachat, stored_dadata, stored_secret = self._stored_keys()

        # ── GigaChat ──
        gigachat_group = QGroupBox("GigaChat")
        gigachat_layout = QFormLayout(gigachat_group)

        self.gigachat_edit = self._make_key_edit(
            "gigachatKeyEdit", "Вставьте Authorization key (Base64)"
        )
        self.gigachat_label = self._field_label(
            "Authorization key:", self.require_gigachat
        )
        gigachat_layout.addRow(self.gigachat_label, self.gigachat_edit)

        self.gigachat_hint = self._hint_label(self._stored_hint(stored_gigachat))
        gigachat_layout.addRow(self.gigachat_hint)

        self.gigachat_link = self._cabinet_label(
            GIGACHAT_CABINET_URL, "Личный кабинет Sber Developer Studio"
        )
        gigachat_layout.addRow(self.gigachat_link)
        content_layout.addWidget(gigachat_group)

        # ── DaData ──
        dadata_group = QGroupBox("DaData")
        dadata_layout = QFormLayout(dadata_group)

        self.dadata_edit = self._make_key_edit(
            "dadataKeyEdit", "Вставьте ключ из раздела «API-ключи»"
        )
        dadata_layout.addRow("API key:", self.dadata_edit)

        self.dadata_hint = self._hint_label(self._stored_hint(stored_dadata))
        dadata_layout.addRow(self.dadata_hint)

        self.dadata_secret_edit = self._make_key_edit(
            "dadataSecretEdit", "Необязательно: для очистки адресов"
        )
        dadata_layout.addRow("Secret key (опционально):", self.dadata_secret_edit)

        self.dadata_secret_hint = self._hint_label(self._stored_hint(stored_secret))
        dadata_layout.addRow(self.dadata_secret_hint)

        self.dadata_link = self._cabinet_label(
            DADATA_CABINET_URL, "Личный кабинет DaData"
        )
        dadata_layout.addRow(self.dadata_link)
        content_layout.addWidget(dadata_group)

        # ── Показать ключи (по умолчанию — звёздочки) ──
        self.show_keys_checkbox = QCheckBox("Показать ключи")
        self.show_keys_checkbox.setToolTip(
            "Показывает введённое, чтобы проверить, что ключ скопирован целиком."
        )
        self.show_keys_checkbox.toggled.connect(self._on_show_keys_toggled)
        content_layout.addWidget(self.show_keys_checkbox)
        content_layout.addStretch(1)

        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

        # ── Ошибки ввода: ВНЕ прокрутки, чтобы их не пришлось искать ──
        self.error_label = QLabel()
        self.error_label.setObjectName("keysDialogError")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme.palette()['danger']};")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        # ── Кнопки ──
        buttons = QHBoxLayout()

        self.btn_cancel = theme.secondary_button(
            "Отмена",
            tooltip="Закрыть без сохранения: работа продолжится без ключей",
            parent=self,
        )
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_cancel)

        buttons.addStretch()

        self.btn_save = theme.primary_button(
            "Сохранить", tooltip="Сохранить ключи в системном хранилище", parent=self
        )
        self.btn_save.clicked.connect(self._on_save)
        self.btn_save.setDefault(True)
        buttons.addWidget(self.btn_save)

        layout.addLayout(buttons)

        self._fit_height()

        logger.debug(
            "KeysDialog инициализирован (обязательный GigaChat: %s)",
            self.require_gigachat,
        )

    # ── Размеры окна ──

    def _fit_height(self) -> None:
        """
        Подгоняет высоту окна под содержимое, НЕ трогая ширину.

        `adjustSize` здесь не годится: он меняет и ширину, и высоту, а после
        появления ошибки окно прыгало бы шире — оператор видит, что «что-то
        щёлкнуло», вместо ровно того же окна с текстом под кнопками.

        Высота не превышает ``MAX_SCREEN_SHARE`` рабочего стола; всё, что не
        поместилось, уходит в прокрутку.
        """
        screen = None
        app = QApplication.instance()
        if app is not None:
            screen = self.screen() or app.primaryScreen()

        if screen is None:
            logger.debug("Экран не определён — высота окна не ограничена")
            self.resize(self.width(), max(self.height(), self.sizeHint().height()))
            return

        available = screen.availableGeometry().height()
        if available <= 0:
            return

        frame = max(0, self.frameGeometry().height() - self.height())
        allowed = max(MIN_SCROLL_HEIGHT, int(available * MAX_SCREEN_SHARE) - frame)

        # Цель — высота содержимого, а не та, что получилась после
        # `adjustSize`: тот сам обрезает окно двумя третями экрана и на
        # невысоком мониторе прячет в прокрутку то, что поместилось бы.
        # Плюс запас на округление раскладки: без него внизу появляется
        # полоса прокрутки с диапазоном в один пиксель.
        wanted = max(self.height(), self.sizeHint().height()) + LAYOUT_SLACK_PX
        target = min(wanted, allowed)
        if target != self.height():
            self.resize(self.width(), target)
        self.setMaximumHeight(allowed)

        logger.debug(
            "Окно ключей: экран %s px, высота окна %s px, предел %s px",
            available, self.height(), allowed,
        )

    # ── Сборка полей ──

    def _stored_keys(self) -> Tuple[bool, bool, bool]:
        """Наличие ключей в хранилище (сбой хранилища — «ключей нет»)."""
        try:
            return keys_status_text()
        except Exception as error:  # noqa: BLE001 — окно не должно падать из-за хранилища
            logger.warning("Не удалось прочитать состояние ключей: %s", error)
            return False, False, False

    @staticmethod
    def _make_key_edit(object_name: str, placeholder: str) -> QLineEdit:
        """Поле ключа: режим пароля, подсказка, без автодополнения."""
        field = QLineEdit()
        field.setObjectName(object_name)
        field.setEchoMode(QLineEdit.Password)
        field.setPlaceholderText(placeholder)
        field.setClearButtonEnabled(True)
        return field

    @staticmethod
    def _field_label(text: str, required: bool) -> QLabel:
        """Подпись поля; у обязательного — звёздочка цвета акцента темы."""
        return theme.required_label(text, required)

    @staticmethod
    def _hint_label(text: str) -> QLabel:
        """Строка состояния под полем — во всю ширину формы."""
        label = QLabel(text)
        label.setObjectName("mutedLabel")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _stored_hint(stored: bool) -> str:
        """Подпись под полем: ключ уже есть или его нет."""
        if stored:
            return "Ключ уже сохранён. Оставьте поле пустым, чтобы не менять."
        return "Ключ не сохранён."

    @staticmethod
    def _cabinet_label(url: str, text: str) -> QLabel:
        """Ссылка на личный кабинет: открывается в браузере по клику."""
        label = QLabel(f'<a href="{url}">{text}</a>')
        label.setOpenExternalLinks(True)
        label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        label.setToolTip(url)
        return label

    # ── Сигналы ──

    def _on_show_keys_toggled(self, shown: bool) -> None:
        """Показывает или снова скрывает введённые ключи."""
        mode = QLineEdit.Normal if shown else QLineEdit.Password
        for field in (self.gigachat_edit, self.dadata_edit, self.dadata_secret_edit):
            field.setEchoMode(mode)

    def _on_save(self) -> None:
        """
        Проверяет ввод и сохраняет ключи; закрывает окно только при успехе.

        Порядок важен: сначала проверка (ничего не сохраняем при ошибке),
        потом запись. Если хранилище отказало — окно остаётся открытым и
        говорит об этом: молча закрыться значило бы соврать оператору.
        """
        errors = validate_keys(
            self.gigachat_edit.text(),
            self.dadata_edit.text(),
            self.dadata_secret_edit.text(),
            require_gigachat=self.require_gigachat,
        )
        if errors:
            logger.info("Ключи не сохранены: ввод не прошёл проверку")
            self.show_errors(errors)
            return

        failures = self.save_keys()
        if failures:
            self.show_errors(failures)
            return

        self.clear_errors()
        self.accept()

    # ── Сохранение ──

    def save_keys(self) -> List[str]:
        """
        Пишет введённые ключи в системное хранилище.

        Пустое поле не трогает сохранённое значение: оператор пришёл
        поменять один ключ, а не стереть остальные.

        :return: список неудач (пустой — всё сохранилось). Значения ключей
            в тексте неудачи не участвуют.
        """
        planned: List[Tuple[str, str, str]] = []
        gigachat = normalize_key(self.gigachat_edit.text())
        dadata = normalize_key(self.dadata_edit.text())
        secret = normalize_key(self.dadata_secret_edit.text())
        if gigachat:
            planned.append(("gigachat", gigachat, "Ключ GigaChat"))
        if dadata:
            planned.append(("dadata", dadata, "Ключ DaData"))
        if secret:
            planned.append(("dadata_secret", secret, "Секретный ключ DaData"))

        failures: List[str] = []
        self.saved_keys = {}

        for name, value, title in planned:
            saved = self._store(name, value)
            self.saved_keys[name] = saved
            if not saved:
                failures.append(
                    f"{title} не сохранён: системное хранилище Windows "
                    "недоступно. Проверьте, что установлена библиотека "
                    "keyring и служба «Диспетчер учётных данных» работает."
                )

        if self.saved_keys:
            logger.info(
                "Ключи сохранены: %s",
                ", ".join(name for name, ok in self.saved_keys.items() if ok) or "нет",
            )
        return failures

    @staticmethod
    def _store(name: str, value: str) -> bool:
        """Один ключ в хранилище; исключение хранилища — False, не падение."""
        setters = {
            "gigachat": secrets_store.set_gigachat_key,
            "dadata": secrets_store.set_dadata_key,
            "dadata_secret": secrets_store.set_dadata_secret,
        }
        try:
            return bool(setters[name](value))
        except Exception as error:  # noqa: BLE001 — окно не должно падать
            logger.error("Хранилище ключей отказало (%s): %s", name, error)
            return False

    # ── Ошибки ──

    def show_errors(self, errors: List[str]) -> None:
        """Показывает ошибки списком под областью прокрутки."""
        self.error_label.setText("\n".join(f"• {line}" for line in errors if line))
        self.error_label.setVisible(bool(errors))
        self._fit_height()

    def clear_errors(self) -> None:
        """Убирает ошибки с экрана."""
        self.error_label.clear()
        self.error_label.hide()


def ask_keys(parent: Optional[QWidget] = None,
             require_gigachat: bool = False) -> bool:
    """
    Показывает окно ключей модально.

    Отдельная точка входа для настроек и запуска: вызывающему не нужно
    знать ни про поля, ни про кнопки, ни про удаление окна.

    :return: True, если оператор сохранил хотя бы один ключ.
    """
    dialog = KeysDialog(parent=parent, require_gigachat=require_gigachat)
    try:
        accepted = dialog.exec_() == QDialog.Accepted
    finally:
        dialog.deleteLater()
    return accepted


__all__ = [
    "DADATA_CABINET_URL",
    "GIGACHAT_CABINET_URL",
    "GIGACHAT_KEY_MIN_LENGTH",
    "KeysDialog",
    "MAX_SCREEN_SHARE",
    "MIN_SCROLL_HEIGHT",
    "ask_keys",
    "is_valid_base64_key",
    "keys_status_text",
    "normalize_key",
    "should_prompt_on_startup",
    "validate_keys",
]
