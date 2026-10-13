#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Виджет Максимилиана: иконка в углу, выезжающая панель, плавающий кот
(задание, часть D.1).

Три части, каждая включается настройками:

  * ``AgentIconButton`` — маленький кот в углу окна (D.1.1). Клик
    открывает и закрывает панель;
  * ``AgentPanel`` — панель справа шириной ~350 px, выезжает анимацией
    200 мс (D.1.2): аватар, история диалога, поле ввода, «Отправить»;
  * ``FloatingCat`` — плавающий кот в правом нижнем углу (D.1.3):
    моргает, «думает», перетаскивается, клик открывает панель.

``install_agent(window)`` встраивает всё это в окно приложения и
возвращает ``AgentWidget`` — единственный объект, которым потом управляют
(настройки, горячая клавиша, закрытие).

Работа с моделью идёт в рабочем потоке (``AgentTask`` в ``QThreadPool``):
окно не должно замирать на время запроса. Подтверждение опасных действий
показывает ``ConfirmationDialog`` (задание, часть D.5).

Про потоки: агент внутри рабочего потока читает и заполняет форму. Так
сделано осознанно — операции занимают доли секунды (диалоги Qt в этом же
потоке), а держать два потока (сеть и форма) значило бы синхронизировать
доступ к виджетам. Известное ограничение записано в docs/STATE.md.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from PyQt5.QtCore import (
    QEasingCurve, QEvent, QObject, QPropertyAnimation, QSize, Qt, QThreadPool,
    QTimer, pyqtSignal,
)
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QShortcut, QSizePolicy, QTextEdit, QToolButton,
    QVBoxLayout, QWidget,
)

from core.agent.agent import Agent, AgentReply
from core.agent.memory import DEFAULT_MAX_HISTORY, AgentMemory
from core.agent.personas import DEFAULT_PERSONA, persona
from core.agent.tools import AgentContext, Tool, active_window, set_context_window
from core.settings_service import get_settings_service
from ui import theme
from ui.agent_avatar import CatAvatar, avatar_icon
from ui.qt_shutdown import wait_for_thread_pools

logger = logging.getLogger("ui.agent_widget")

#: Ширина панели (задание, часть D.1.2).
PANEL_WIDTH = 350
#: Длительность выезда панели, мс (задание, часть D.1.2).
PANEL_ANIMATION_MS = 200
#: Размеры аватара: иконка, панель, плавающий кот (задание, часть D.2).
ICON_SIZE = 32
PANEL_AVATAR_SIZE = 64
FLOATING_SIZE = 96


# ─────────────────────────────────────────────────────────────
# Рабочий поток: запрос к агенту
# ─────────────────────────────────────────────────────────────

class AgentTaskSignals(QObject):
    """Сигналы задачи агента (Qt требует QObject для сигналов)."""

    #: Ответ готов: (текст, имена инструментов, достигнут ли лимит шагов)
    finished = pyqtSignal(str, list, bool)
    #: Ошибка запроса: текст для оператора
    failed = pyqtSignal(str)
    #: Промежуточный шаг («Выполняю: поиск водителя»)
    activity = pyqtSignal(str)


class AgentTask:
    """
    Запрос к агенту в рабочем потоке.

    Наследник QRunnable создаётся отдельным классом ниже: так задача
    остаётся простой (только вызов), а сигналы живут в QObject.
    """

    def __init__(self, agent: Agent, message: str, signals: AgentTaskSignals):
        self.agent = agent
        self.message = message
        self.signals = signals

    def run(self) -> None:
        """Выполняет запрос и сообщает результат сигналами."""
        try:
            reply: AgentReply = self.agent.send(self.message)
        except Exception as e:  # noqa: BLE001 — сеть не должна ронять поток
            logger.exception("Запрос к Максимилиану не удался")
            self.signals.failed.emit(f"{type(e).__name__}: {e}")
            return
        try:
            self.signals.finished.emit(
                reply.text, list(reply.tool_names), bool(reply.step_limit_reached)
            )
        except RuntimeError:
            # Окно уже закрыто — панель результата не ждёт.
            logger.info("Ответ Максимилиана получен после закрытия панели")


def _make_runnable(task: AgentTask):
    """QRunnable-обёртка над задачей агента (импорт Qt внутри функции)."""
    from PyQt5.QtCore import QRunnable

    class _Runnable(QRunnable):
        def __init__(self, inner: AgentTask):
            super().__init__()
            self._inner = inner

        def run(self) -> None:
            self._inner.run()

    return _Runnable(task)


# ─────────────────────────────────────────────────────────────
# Диалог подтверждения (D.5)
# ─────────────────────────────────────────────────────────────

class ConfirmationDialog(QDialog):
    """
    «Максимилиан хочет выполнить действие. Подтвердить?»

    Кнопки: [Да] [Нет] [Показать детали]. Третий вариант не закрывает
    диалог: он показывает аргументы вызова (что именно запишется в форму)
    и возвращает оператора к вопросу.

    Реализация — свой QDialog, а не QMessageBox. Причина практическая:
    агент вызывает подтверждение из РАБОЧЕГО потока (там же идёт запрос к
    GigaChat), и QMessageBox в этом сценарии роняет процесс
    (access violation при показе модального окна из чужого потока).
    Диалог с явными кнопками ведёт себя предсказуемо.

    Для тестов и проб есть ``auto_answer``: диалог сам нажимает «Да» или
    «Нет» через заданное время. Без этого проверка заполнения формы
    требовала бы живого оператора.
    """

    def __init__(self, tool: Tool, arguments: Dict[str, Any], parent=None):
        super().__init__(parent)
        self._tool = tool
        self._arguments = dict(arguments or {})
        self._reference_dialog = None
        self._auto_timer: Optional[QTimer] = None
        #: Ответ, который диалог даст сам (None — ждём оператора).
        self._auto_answer: Optional[bool] = None

        self.setWindowTitle("Максимилиан")
        self.setWindowModality(Qt.ApplicationModal)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        question = QLabel(tool.confirmation_text(self._arguments))
        question.setWordWrap(True)
        layout.addWidget(question)

        hint = QLabel("Заполнение формы можно отменить: данные в базе не меняются.")
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.yes_button = QPushButton("Да")
        self.no_button = QPushButton("Нет")
        self.details_button = QPushButton("Показать детали")
        self.no_button.setDefault(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self.details_button)
        buttons.addStretch()
        buttons.addWidget(self.no_button)
        buttons.addWidget(self.yes_button)
        layout.addLayout(buttons)

        self.yes_button.clicked.connect(self.accept)
        self.no_button.clicked.connect(self.reject)
        self.details_button.clicked.connect(self._show_details)
        self._confirmed = False

    def accept(self) -> None:
        """Диалог подтверждён оператором."""
        self._confirmed = True
        super().accept()

    def _show_details(self) -> None:
        """
        Показывает аргументы вызова, не закрывая диалог.

        Вложенный QMessageBox создаётся БЕЗ родителя, но удерживается
        ссылкой (``self._reference_dialog``): объект, убранный сборщиком
        мусора Python раньше, чем Qt закончит с ним работать, роняет
        процесс (access violation, грабли проекта).
        """
        box = QMessageBox()
        box.setWindowTitle("Детали вызова")
        box.setIcon(QMessageBox.Information)
        box.setText(f"Инструмент: {self._tool.name}")
        lines = [f"{key} = {value!r}" for key, value in self._arguments.items()]
        box.setDetailedText("\n".join(lines) or "аргументов нет")
        self._reference_dialog = box
        try:
            box.exec_()
        finally:
            self._reference_dialog = None
            box.deleteLater()

    def auto_answer(self, answer: bool, delay_ms: int = 0) -> None:
        """
        Отвечает за оператора через ``delay_ms`` (тесты, пробы, скриншоты).

        Диалог всё равно показывается, поэтому проверяется настоящий путь
        подтверждения, а не обходной. Слот — метод диалога, без замыкания
        на кнопку: замыкание даёт цикл ссылок Python ↔ Qt и роняет процесс
        при завершении (AGENTS.md § 5.1).
        """
        self._auto_answer = bool(answer)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._apply_auto_answer)
        timer.start(max(0, int(delay_ms)))
        self._auto_timer = timer

    def _apply_auto_answer(self) -> None:
        """Закрывает диалог ответом, который задал auto_answer()."""
        if self._auto_answer:
            self.accept()
        else:
            self.reject()

    def is_confirmed(self) -> bool:
        """Оператор нажал «Да»."""
        return self._confirmed

    def confirmation_text(self) -> str:
        """Текст вопроса (нужно тестам)."""
        return self._tool.confirmation_text(self._arguments)


def ask_confirmation(tool: Tool, arguments: Dict[str, Any], parent=None,
                     auto_answer: Optional[bool] = None,
                     auto_delay_ms: int = 0) -> bool:
    """
    Диалог подтверждения для агента (передаётся в AgentContext.confirm).

    Вызывается и из рабочего потока: Qt показывает модальное окно и в
    неглавном потоке, а оператор всё равно ждёт ответа агента.

    :param auto_answer: True/False — ответить за оператора (пробы и
        скриншоты); None — спросить по-настоящему.
    """
    # Родителем может оказаться любой объект (панель, окно, заглушка в
    # тесте): не-виджет в этом месте роняет Qt, поэтому проверяем тип.
    safe_parent = parent if isinstance(parent, QWidget) else None
    dialog = ConfirmationDialog(tool, arguments, safe_parent)
    if auto_answer is not None:
        dialog.auto_answer(auto_answer, auto_delay_ms)
    try:
        dialog.exec_()
        return dialog.is_confirmed()
    finally:
        # Явно отпускаем C++-объект: диалог создан без родителя, и сборщик
        # мусора Python мог бы убрать его раньше, чем Qt закончит работу.
        dialog.deleteLater()


# ─────────────────────────────────────────────────────────────
# Иконка в углу (D.1.1)
# ─────────────────────────────────────────────────────────────

class AgentIconButton(QToolButton):
    """Маленький кот в углу окна: клик открывает и закрывает панель."""

    def __init__(self, persona_key: str = DEFAULT_PERSONA, parent=None):
        super().__init__(parent)
        self.setObjectName("agentIconButton")
        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.setFixedSize(QSize(ICON_SIZE + 12, ICON_SIZE + 12))
        self.set_persona(persona_key)
        self.setToolTip("Максимилиан — нажмите, чтобы открыть")

    def set_persona(self, persona_key: str) -> None:
        """Меняет образ на иконке."""
        self._persona = persona_key
        self.setIcon(avatar_icon(ICON_SIZE, persona_key))


# ─────────────────────────────────────────────────────────────
# Плавающий кот (D.1.3)
# ─────────────────────────────────────────────────────────────

class FloatingCat(QWidget):
    """
    Плавающий кот в правом нижнем углу: моргает, «думает», таскается.

    Окно без рамки и всегда сверху — это украшение, а не рабочее окно:
    оно не должно попадать в панель задач и мешать формам.
    """

    #: Кот нажат (открыть панель).
    clicked = pyqtSignal()

    def __init__(self, persona_key: str = DEFAULT_PERSONA, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(QSize(FLOATING_SIZE, FLOATING_SIZE))
        self.setToolTip("Максимилиан — нажмите, чтобы открыть панель")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.avatar = CatAvatar(FLOATING_SIZE, persona_key, self)
        layout.addWidget(self.avatar)

        self._drag_offset = None
        self._moved = False

    def set_persona(self, persona_key: str) -> None:
        """Меняет образ плавающего кота."""
        self.avatar.set_persona(persona_key)

    def set_thinking(self, thinking: bool) -> None:
        """«Думает» — глаза широко открыты."""
        self.avatar.set_thinking(thinking)

    # ── Перетаскивание и клик ──
    def mousePressEvent(self, event) -> None:  # noqa: N802 — имя метода Qt
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            self._moved = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
            self._moved = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and not self._moved:
            self.clicked.emit()
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def move_to_corner(self, window: Optional[QWidget] = None) -> None:
        """Ставит кота в правый нижний угол окна (или экрана)."""
        if window is not None and window.isVisible():
            frame = window.frameGeometry()
        else:
            screen = QApplication.primaryScreen()
            frame = screen.availableGeometry() if screen else None
        if frame is None:
            return
        self.move(
            frame.right() - FLOATING_SIZE - 16,
            frame.bottom() - FLOATING_SIZE - 16,
        )


# ─────────────────────────────────────────────────────────────
# Панель диалога (D.1.2)
# ─────────────────────────────────────────────────────────────

class AgentPanel(QFrame):
    """
    Боковая панель: аватар, имя, история диалога, поле ввода.

    Панель не знает про настройки и модель: она показывает то, что ей дают
    (``append_message``), и сообщает о нажатиях сигналами.
    """

    #: Оператор отправил сообщение.
    submitted = pyqtSignal(str)
    #: Оператор просит очистить историю.
    clear_requested = pyqtSignal()
    #: Оператор просит открыть настройки Максимилиана.
    settings_requested = pyqtSignal()

    def __init__(self, agent_name: str = "Максимилиан",
                 persona_key: str = DEFAULT_PERSONA,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("agentPanel")
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedWidth(PANEL_WIDTH)
        #: Владелец (AgentWidget): им панель закрывается, потому что показ —
        #: это анимация и место в окне, а не просто show()/hide().
        self._owner: Optional[Any] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(self._build_header(agent_name, persona_key))

        # ── История диалога ──
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setObjectName("agentScroll")
        self.history = QWidget()
        self.history_layout = QVBoxLayout(self.history)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(8)
        self.history_layout.addStretch()
        self.scroll.setWidget(self.history)
        layout.addWidget(self.scroll, 1)

        # ── Строка состояния агента ──
        self.status = QLabel("")
        self.status.setObjectName("mutedLabel")
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        layout.addWidget(self.status)

        # ── Поле ввода ──
        self.input = QTextEdit()
        self.input.setObjectName("agentInput")
        self.input.setPlaceholderText(
            "Спросите: «что такое ИНН?», «найди Иванова», "
            "«заполни перевозчика»"
        )
        self.input.setFixedHeight(64)
        self.input.installEventFilter(self)
        layout.addWidget(self.input)

        self.btn_send = theme.primary_button("Отправить")
        self.btn_send.clicked.connect(self.emit_submitted)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.btn_send)
        layout.addLayout(row)

        self._pending = 0

    # ── Построение шапки панели ──
    def _build_header(self, agent_name: str, persona_key: str) -> QWidget:
        """Шапка панели: аватар, имя, кнопки «настройки» и «закрыть»."""
        frame = QFrame()
        frame.setObjectName("agentHeader")
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.avatar = CatAvatar(PANEL_AVATAR_SIZE, persona_key, frame)
        row.addWidget(self.avatar)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.name_label = QLabel(agent_name)
        self.name_label.setObjectName("agentName")
        self.role_label = QLabel(persona(persona_key).title)
        self.role_label.setObjectName("mutedLabel")
        titles.addWidget(self.name_label)
        titles.addWidget(self.role_label)
        row.addLayout(titles)
        row.addStretch()

        self.btn_settings = QToolButton()
        self.btn_settings.setText("⚙")
        self.btn_settings.setObjectName("agentToolButton")
        self.btn_settings.setToolTip("Настройки Максимилиана")
        self.btn_settings.setAutoRaise(True)
        self.btn_settings.clicked.connect(self.settings_requested)
        row.addWidget(self.btn_settings)

        self.btn_close = QToolButton()
        self.btn_close.setText("✕")
        self.btn_close.setObjectName("agentToolButton")
        self.btn_close.setToolTip("Закрыть панель")
        self.btn_close.setAutoRaise(True)
        self.btn_close.clicked.connect(self._on_close_clicked)
        row.addWidget(self.btn_close)

        return frame

    def _on_close_clicked(self) -> None:
        """
        Кнопка «✕»: закрывает панель через владельца (AgentWidget).

        Панель не умеет убирать себя сама: её показом и скрытием управляет
        AgentWidget (анимация, место в окне). Если владельца нет (панель
        в тесте), просто прячемся.
        """
        owner = self._owner
        if owner is not None:
            owner.hide_panel()
        else:
            self.hide()

    def set_owner(self, owner: Any) -> None:
        """Запоминает владельца панели (AgentWidget)."""
        self._owner = owner

    # ── Сообщения ──
    def append_message(self, text: str, author: str = "agent",
                       tool_names: Optional[List[str]] = None) -> None:
        """
        Добавляет сообщение в историю.

        :param author: «user», «agent» или «system» (служебная строка).
        """
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        if author == "user":
            bubble.setObjectName("agentBubbleUser")
        elif author == "system":
            bubble.setObjectName("agentBubbleSystem")
        else:
            bubble.setObjectName("agentBubbleAgent")
        if tool_names:
            bubble.setToolTip("Инструменты: " + ", ".join(tool_names))

        self.history_layout.insertWidget(self.history_layout.count() - 1, bubble)
        self._scroll_to_bottom()

    def set_history(self, messages) -> None:
        """Показывает историю диалога (после загрузки из базы)."""
        self.clear_messages()
        for message in messages:
            role = getattr(message, "role", "")
            if role == "user":
                self.append_message(str(getattr(message, "content", "")), "user")
            elif role == "assistant":
                content = str(getattr(message, "content", "")).strip()
                if content:
                    self.append_message(content, "agent")

    def clear_messages(self) -> None:
        """Убирает все сообщения из истории."""
        while self.history_layout.count() > 1:
            item = self.history_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    # ── Состояние ──
    def set_status(self, text: str, busy: bool = False) -> None:
        """Показывает состояние («думаю…», ошибка) и переключает аватар."""
        self.status.setText(text)
        self.status.setVisible(bool(text))
        self.avatar.set_thinking(busy)
        self.btn_send.setEnabled(not busy)
        self.input.setEnabled(not busy)

    def set_persona(self, persona_key: str) -> None:
        """Меняет образ панели (аватар и подпись образа)."""
        self.avatar.set_persona(persona_key)
        self.role_label.setText(persona(persona_key).title)

    def set_agent_name(self, name: str) -> None:
        """Меняет подпись имени в шапке панели."""
        self.name_label.setText(name)

    # ── Ввод ──
    def emit_submitted(self) -> None:
        """
        Отправляет текст из поля ввода.

        Пустое сообщение не уходит: кнопка нажата случайно.
        """
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.submitted.emit(text)

    def eventFilter(self, source, event) -> bool:  # noqa: N802 — имя метода Qt
        """
        Enter отправляет сообщение, Shift+Enter — перенос строки.

        Фильтр, а не подкласс QTextEdit: поведение поля нужно только здесь,
        и отдельный класс ради двух строк кода не нужен.
        """
        if source is self.input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if not (event.modifiers() & Qt.ShiftModifier):
                    self.emit_submitted()
                    return True
        return super().eventFilter(source, event)

    def _scroll_to_bottom(self) -> None:
        """
        Прокручивает историю к последнему сообщению.

        Прокрутка откладывается на один такт: только что добавленный пузырь
        ещё не пересчитал свою высоту, и `maximum()` вернул бы старое
        значение. Отложенный вызов — метод панели (без lambda: замыкания
        на виджеты в проекте запрещены, AGENTS.md § 5.1).
        """
        QTimer.singleShot(0, self.scroll_to_bottom_now)

    def scroll_to_bottom_now(self) -> None:
        """Прокрутка вниз немедленно (вызывается по таймеру и тестами)."""
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())


# ─────────────────────────────────────────────────────────────
# Главный объект: иконка + панель + плавающий кот
# ─────────────────────────────────────────────────────────────

class AgentWidget(QObject):
    """
    Максимилиан внутри окна приложения.

    Собирает три части, следит за настройками и горячей клавишей, держит
    агента и рабочий поток. Создаётся функцией ``install_agent(window)``.
    """

    def __init__(self, window: QWidget,
                 client_factory: Optional[Callable[[], Any]] = None,
                 auto_confirm: Optional[bool] = None):
        super().__init__(window)
        self.window = window
        self.settings = get_settings_service()
        self._client_factory = client_factory
        #: Ответ за оператора в диалоге подтверждения (None — спросить).
        #: Нужно пробам и скриншотам: они идут без живого оператора.
        self.auto_confirm = auto_confirm

        self.panel: Optional[AgentPanel] = None
        self.icon_button: Optional[AgentIconButton] = None
        self.floating: Optional[FloatingCat] = None
        self.shortcut: Optional[QShortcut] = None

        self._agent: Optional[Agent] = None
        self._thread_pool = QThreadPool(window)
        self._thread_pool.setMaxThreadCount(1)
        self._task: Optional[AgentTask] = None
        self._busy = False

        self._animation = QPropertyAnimation(self)
        self._animation.setDuration(PANEL_ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._on_panel_width)

        #: Виджет окна, в котором сейчас лежит панель. Ставится ДО
        #: apply_settings: показ панели «при старте» вешает фильтр событий
        #: на этот виджет, и он должен быть определён заранее.
        self._host: Optional[QWidget] = None

        self.apply_settings()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    # ---------------------------------------------------------
    # Настройки
    # ---------------------------------------------------------
    def agent_settings(self) -> Dict[str, Any]:
        """Текущий раздел agent из настроек (со значениями по умолчанию)."""
        return self.settings.agent_settings()

    def apply_settings(self) -> None:
        """
        Приводит интерфейс в соответствие настройкам.

        Выключенный агент не показывает ничего: ни иконки, ни панели, ни
        кота, а горячая клавиша не срабатывает (задание, часть E.4).
        """
        values = self.agent_settings()
        enabled = bool(values["enabled"])
        mode = values["mode"]
        persona_key = values["avatar"]

        show_icon = enabled and mode in ("icon", "hybrid")
        show_panel_possible = enabled and mode in ("panel", "hybrid")

        if not enabled:
            self.hide_panel()
            if self.floating is not None:
                self.floating.hide()

        self._ensure_icon(show_icon, persona_key)
        self._ensure_floating(
            enabled and bool(values["show_floating"]), persona_key
        )
        self._apply_hotkey(enabled and show_panel_possible, values["hotkey"])

        if self.icon_button is not None:
            self.icon_button.setVisible(show_icon)

        # Панель: создаём при первой необходимости, дальше только правим.
        if show_panel_possible:
            self._ensure_panel(
                str(values["name"]), persona_key,
                int(values["max_history"]), int(values["max_tool_steps"]),
            )
            if bool(values["show_panel_on_start"]) and not self.panel.isVisible():
                self.show_panel()
        elif self.panel is not None:
            self.panel.set_persona(persona_key)

        logger.info(
            "Максимилиан: включён=%s, режим=%s, образ=%s, плавающий=%s",
            enabled, mode, persona_key, values["show_floating"],
        )

    def _ensure_icon(self, needed: bool, persona_key: str) -> None:
        """Создаёт иконку в углу окна, если её ещё нет."""
        if self.icon_button is None:
            if not needed:
                return
            self.icon_button = AgentIconButton(persona_key, self.window)
            self.icon_button.clicked.connect(self.toggle_panel)
            if not _place_icon(self.window, self.icon_button):
                # Шапку найти не удалось (другой каркас окна) — иконка
                # переезжает в статус-бар: агент должен быть доступен.
                status = getattr(self.window, "statusBar", None)
                if callable(status):
                    status().addPermanentWidget(self.icon_button)
                else:
                    logger.warning("Не удалось разместить иконку Максимилиана")
        self.icon_button.set_persona(persona_key)

    def _ensure_floating(self, needed: bool, persona_key: str) -> None:
        """Создаёт плавающего кота, если он включён настройкой."""
        if self.floating is None:
            if not needed:
                return
            self.floating = FloatingCat(persona_key)
            self.floating.clicked.connect(self.toggle_panel)
        self.floating.set_persona(persona_key)
        if needed:
            self.floating.move_to_corner(self.window)
            self.floating.show()
        else:
            self.floating.hide()

    def _ensure_panel(self, name: str, persona_key: str,
                      max_history: int, max_tool_steps: int) -> None:
        """Создаёт панель и агента при первом обращении."""
        if self.panel is None:
            self.panel = AgentPanel(name, persona_key, self.window)
            self.panel.set_owner(self)
            self.panel.submitted.connect(self.on_submit)
            self.panel.clear_requested.connect(self.on_clear_history)
            self.panel.settings_requested.connect(self.on_open_settings)
            self._animation.setTargetObject(self.panel)
            self._animation.setPropertyName(b"maximumWidth")
            self._load_history(max_history, max_tool_steps)
        self.panel.set_agent_name(name)
        self.panel.set_persona(persona_key)

    def _apply_hotkey(self, enabled: bool, sequence: str) -> None:
        """Включает или выключает горячую клавишу панели."""
        if self.shortcut is not None:
            self.shortcut.setEnabled(False)
            self.shortcut.deleteLater()
            self.shortcut = None
        if not enabled or not str(sequence or "").strip():
            return
        try:
            self.shortcut = QShortcut(QKeySequence(str(sequence)), self.window)
        except Exception as e:  # noqa: BLE001 — плохая клавиша не должна ломать окно
            logger.warning("Горячая клавиша %r не подключена: %s", sequence, e)
            return
        self.shortcut.activated.connect(self.toggle_panel)
        logger.info("Максимилиан: горячая клавиша %s", sequence)

    def reload_settings(self) -> None:
        """Перечитывает настройки и применяет их (после диалога настроек)."""
        self.settings.reload()
        self.apply_settings()

    # ---------------------------------------------------------
    # Агент
    # ---------------------------------------------------------
    def _make_agent(self, max_history: int, max_tool_steps: int) -> Agent:
        """Создаёт агента: клиент, контекст окна, история диалога."""
        context = AgentContext(
            window=self.window,
            contract_type=str(getattr(self.window, "CONTRACT_TYPE", "") or ""),
            confirm=self._confirm,
            on_activity=self._on_activity,
        )
        memory = AgentMemory(max_history=max_history)
        agent = Agent(
            client=None,
            context=context,
            memory=memory,
            persona_key=self.agent_settings()["avatar"],
            max_tool_steps=max_tool_steps,
            max_history=max_history,
        )
        return agent

    def _client(self) -> Any:
        """Клиент GigaChat для агента (создаётся при первом запросе)."""
        if self._client_factory is None:
            raise RuntimeError(
                "GigaChat не подключён: проверьте ключ авторизации в настройках"
            )
        return self._client_factory()

    def _attach_client(self, agent: Agent) -> None:
        """Подключает клиента к агенту, если это ещё не сделано."""
        if agent.chat is not None:
            return
        from core.agent.llm import SafeChat

        agent.chat = SafeChat(self._client())

    def _load_history(self, max_history: int, max_tool_steps: int) -> None:
        """Готовит агента и показывает сохранённую историю диалога."""
        self._agent = self._make_agent(max_history, max_tool_steps)
        messages = self._agent.memory.messages
        if messages:
            self.panel.set_history(messages)

    def _confirm(self, tool: Tool, arguments: Dict[str, Any]) -> bool:
        """Подтверждение опасного действия (задание, часть D.5)."""
        parent = self.panel if self.panel is not None else self.window
        return ask_confirmation(tool, arguments, parent,
                                auto_answer=self.auto_confirm)

    def _on_activity(self, message: str) -> None:
        """Показывает шаг агента в панели (вызывает рабочий поток)."""
        if self.panel is None:
            return
        self._panel_call(self.panel.set_status, message, True)

    def _panel_call(self, func, *args) -> None:
        """
        Вызывает метод панели в главном потоке.

        Из рабочего потока менять виджеты нельзя: если мы не в главном
        потоке, вызов уходит сигналом Qt.
        """
        app = QApplication.instance()
        thread = getattr(app, "thread", None) if app is not None else None
        if thread is None or thread() is None or thread() is _current_thread():
            func(*args)
            return
        QTimer.singleShot(0, lambda: func(*args))

    # ---------------------------------------------------------
    # Диалог
    # ---------------------------------------------------------
    def on_submit(self, message: str) -> None:
        """Отправляет сообщение агенту в рабочем потоке."""
        if self._busy:
            self.panel.set_status("Уже думаю над прошлым вопросом…", True)
            return
        if self._agent is None:
            values = self.agent_settings()
            self._load_history(int(values["max_history"]), int(values["max_tool_steps"]))

        self.panel.append_message(message, "user")
        try:
            self._attach_client(self._agent)
        except Exception as e:  # noqa: BLE001 — понятная ошибка оператору
            logger.error("Клиент GigaChat для агента не создан: %s", e)
            self.panel.append_message(
                f"Не удалось подключиться к GigaChat: {e}", "system"
            )
            self.panel.set_status("", False)
            return

        signals = AgentTaskSignals()
        signals.finished.connect(self._on_finished)
        signals.failed.connect(self._on_failed)
        signals.activity.connect(self._on_activity)

        self._busy = True
        self.panel.set_status("Думаю…", True)
        self._task = AgentTask(self._agent, message, signals)
        self._thread_pool.start(_make_runnable(self._task))

    def _on_finished(self, text: str, tool_names: List[str], limit: bool) -> None:
        """Ответ агента готов."""
        self._busy = False
        self._task = None
        self.panel.append_message(text, "agent", tool_names)
        status = "Достигнут лимит шагов — уточните запрос" if limit else ""
        self.panel.set_status(status, False)

    def _on_failed(self, error: str) -> None:
        """Запрос к агенту не удался: показываем причину."""
        self._busy = False
        self._task = None
        self.panel.set_status("", False)
        self.panel.append_message(f"Ошибка запроса: {error}", "system")

    def on_clear_history(self) -> None:
        """Очищает историю диалога (пункт меню панели)."""
        if self._agent is None:
            return
        removed = self._agent.clear_history()
        self.panel.clear_messages()
        logger.info("История Максимилиана очищена (удалено записей: %s)", removed)

    def on_open_settings(self) -> None:
        """Диалог настроек Максимилиана."""
        from ui.agent_settings_dialog import AgentSettingsDialog

        values = self.agent_settings()
        dialog = AgentSettingsDialog(values, self.window)
        if dialog.exec_() != dialog.Accepted:
            return

        self.settings.update_agent(dialog.get_settings())
        self.reload_settings()
        if self._agent is not None:
            self._agent.persona_key = self.agent_settings()["avatar"]

    # ---------------------------------------------------------
    # Панель: показ и скрытие
    # ---------------------------------------------------------
    def toggle_panel(self) -> None:
        """Открывает панель, если она закрыта, и наоборот."""
        if self.panel is None or not self.panel.isVisible():
            self.show_panel()
        else:
            self.hide_panel()

    def show_panel(self) -> None:
        """
        Выезжает справа (анимация 200 мс).

        Панель — накладка внутри центрального виджета окна: она не меняет
        раскладку формы (вкладки не сдвигаются) и работает в любом окне
        приложения — MainWindow и окнах типов одинаково.
        """
        if self.panel is None:
            values = self.agent_settings()
            self._ensure_panel(
                str(values["name"]), str(values["avatar"]),
                int(values["max_history"]), int(values["max_tool_steps"]),
            )

        host = _panel_host(self.window)
        if host is None:
            logger.warning("Панель Максимилиана: не найдено место в окне")
            return

        if self.panel.parentWidget() is not host:
            if self._host is not None:
                self._host.removeEventFilter(self)
            self.panel.setParent(host)
        if self._host is not host:
            if self._host is not None:
                self._host.removeEventFilter(self)
            self._host = host
            host.installEventFilter(self)

        self.panel.show()
        self._layout_panel(0)
        self.panel.raise_()

        self._animation.stop()
        self._animation.setStartValue(0)
        self._animation.setEndValue(PANEL_WIDTH)
        self._animation.start()

        if self.floating is not None and self.floating.isVisible():
            self.floating.raise_()

    def hide_panel(self) -> None:
        """
        Убирает панель (анимация в обратную сторону).

        Состояние меняется СРАЗУ, а анимация только доигрывает: панель,
        которая уже уезжает, не должна считаться открытой — иначе быстрый
        второй клик по иконке не сработал бы (toggle смотрит на isVisible).
        """
        if self.panel is None or not self.panel.isVisible():
            return

        self._animation.stop()
        self.panel.setMaximumWidth(0)
        self._layout_panel(0)
        self.panel.hide()

        # Анимация сворачивания: панель уже скрыта, поэтому движение видно
        # только у её содержимого — этого достаточно, чтобы уход не был рывком.
        self._animation.setStartValue(PANEL_WIDTH)
        self._animation.setEndValue(0)
        self._animation.start()

    def _on_panel_width(self, value: Any) -> None:
        """
        Ширина панели изменилась во время анимации — двигаем её к краю.

        Панель — накладка, поэтому её положение считается вручную: без
        этого она «росла» бы влево от правого края только на бумаге.
        """
        width = int(value)
        if self.panel is not None:
            self.panel.setMaximumWidth(width)
        self._layout_panel(width)

    def _layout_panel(self, width: Optional[int] = None) -> None:
        """Ставит панель к правому краю окна и задаёт её высоту."""
        if self.panel is None:
            return
        host = _panel_host(self.window)
        if host is None:
            return
        size = host.size()
        actual = self.panel.maximumWidth() if width is None else int(width)
        self.panel.setGeometry(
            size.width() - actual, 0,
            max(0, actual), max(200, size.height()),
        )
        # Раскладка панели пересчитывается сразу: иначе при съёмке кадра
        # (offscreen, скриншоты) её содержимое рисуется со старыми
        # координатами и «уезжает» за правый край окна.
        layout = self.panel.layout()
        if layout is not None:
            layout.activate()
        self.panel.raise_()

    def eventFilter(self, source, event) -> bool:  # noqa: N802 — имя метода Qt
        """
        Окно изменило размер — панель остаётся у правого края.

        Панель не в раскладке (накладка), поэтому её положение надо
        пересчитывать самим: иначе при развороте окна на весь экран она
        осталась бы висеть посередине.
        """
        # getattr, а не self._host: фильтр может сработать на объекте,
        # который ещё не достроен (настройки ставятся до конца __init__).
        host = getattr(self, "_host", None)
        if source is host and host is not None and event.type() == QEvent.Resize:
            if self.panel is not None and self.panel.isVisible():
                self._layout_panel()
        return super().eventFilter(source, event)

    def is_panel_visible(self) -> bool:
        """Открыта ли панель (нужно тестам и горячей клавише)."""
        return self.panel is not None and self.panel.isVisible()

    def finish_panel_animation(self) -> None:
        """
        Доводит выезд панели до конечного состояния немедленно.

        Нужно тестам и пробам: анимация идёт 200 мс, и в offscreen-режиме
        события таймера не приходят сами. После вызова панель стоит на
        месте так, как её увидит оператор.
        """
        if self.panel is None:
            return
        self._animation.setCurrentTime(self._animation.duration())
        self._on_panel_width(PANEL_WIDTH)
        layout = self.panel.layout()
        if layout is not None:
            layout.activate()

    # ---------------------------------------------------------
    # Завершение
    # ---------------------------------------------------------
    def shutdown(self) -> None:
        """
        Останавливает фоновую работу перед выходом.

        Пул — Qt-ребёнок окна: если его разрушить с работающей задачей,
        процесс падает при завершении (см. ui/qt_shutdown.py). Поэтому
        сначала ждём, потом отпускаем ссылки.
        """
        try:
            self._thread_pool.clear()
            wait_for_thread_pools(self.window)
        except RuntimeError:
            return
        self._task = None


# ─────────────────────────────────────────────────────────────
# Установка в окно
# ─────────────────────────────────────────────────────────────

def _current_thread():
    """Текущий QThread (нужен, чтобы понять, в главном ли мы потоке)."""
    from PyQt5.QtCore import QThread

    return QThread.currentThread()


def _place_icon(window: QWidget, button: QWidget) -> bool:
    """
    Ставит иконку в шапку окна (перед селектором типа договора).

    Шапка есть у всех окон (QFrame с именем «appHeader»), но её раскладка
    не сохраняется атрибутом, поэтому ищем её по имени объекта и вставляем
    кнопку предпоследней — левее кнопок действий. Не нашли — False, и
    вызывающий код уводит иконку в статус-бар.
    """
    try:
        from PyQt5.QtWidgets import QFrame

        header = window.findChild(QFrame, "appHeader")
        if header is None:
            return False
        layout = header.layout()
        if layout is None:
            return False
        layout.insertWidget(max(0, layout.count() - 1), button)
        return True
    except Exception as e:  # noqa: BLE001 — не нашли шапку, найдём статус-бар
        logger.warning("Иконка Максимилиана не встала в шапку: %s", e)
        return False


def _panel_host(window: QWidget) -> Optional[QWidget]:
    """
    Виджет, внутри которого живёт панель.

    Это центральный виджет окна (QMainWindow.centralWidget): панель
    становится его накладкой справа и не двигает вкладки формы.
    """
    central = getattr(window, "centralWidget", None)
    if callable(central):
        widget = central()
        if widget is not None:
            return widget
    return window


def default_client_factory() -> Any:
    """
    Клиент GigaChat по текущим настройкам.

    Функция живёт в ui, а не в core: она читает настройки и системное
    хранилище ключа — это сборка приложения, а не логика агента. Ключ
    берётся один раз на запрос, поэтому смена ключа в настройках не требует
    перезапуска программы.
    """
    from core import secrets_store
    from core.gigachat_client import GigaChatClient

    settings = get_settings_service()
    auth_key = secrets_store.get_gigachat_key() or ""
    if not auth_key.strip():
        raise RuntimeError(
            "ключ GigaChat не найден в системном хранилище — "
            "запустите set_key.py"
        )
    return GigaChatClient(
        auth_key=auth_key,
        scope=settings.get_str("gigachat_scope", "GIGACHAT_API_PERS"),
        model=settings.get_str("gigachat_model", "GigaChat-3-Pro"),
        timeout=settings.get_int("gigachat_timeout", 150),
        verify_ssl=settings.get_bool("gigachat_verify_ssl", True),
        ca_bundle=settings.get_str("gigachat_ca_bundle", "") or None,
    )


#: Открыть панель при старте разрешено один раз за запуск программы:
#: окна создаются по мере переключения, и «открывать при старте» не должно
#: означать «открывать в каждом новом окне».
_startup_panel_pending = True


def agent_should_open_on_start() -> bool:
    """Первый ли это показ окна в текущем запуске (для show_panel_on_start)."""
    global _startup_panel_pending
    if not _startup_panel_pending:
        return False
    _startup_panel_pending = False
    return True


def reset_startup_panel() -> None:
    """Сбрасывает флаг «панель при старте» (нужно тестам)."""
    global _startup_panel_pending
    _startup_panel_pending = True


def install_agent(
    window: QWidget,
    client_factory: Optional[Callable[[], Any]] = None,
    open_on_start: Optional[bool] = None,
    auto_confirm: Optional[bool] = None,
) -> Optional[AgentWidget]:
    """
    Ставит Максимилиана в окно приложения.

    Идемпотентна: повторный вызов возвращает уже установленный виджет.
    Если агент выключен настройкой, функция возвращает None — в окне не
    появляется ничего (задание, часть E.4).

    :param window: окно (MainWindow или окно типа договора);
    :param client_factory: функция без аргументов, создающая клиента
        GigaChat. По умолчанию — default_client_factory. Клиент создаётся
        при первом запросе: если ключа нет, ошибка появится в панели, а не
        при старте программы (нужно тестам — они подставляют мок);
    :param open_on_start: открыть панель сразу (None — по настройке и
        только для первого окна запуска).
    """
    existing = getattr(window, "agent_widget", None)
    if existing is not None:
        return existing

    settings = get_settings_service()
    if not settings.agent_enabled():
        logger.info("Максимилиан выключен настройкой agent.enabled=false")
        return None

    widget = AgentWidget(
        window, client_factory=client_factory or default_client_factory,
        auto_confirm=auto_confirm,
    )
    window.agent_widget = widget

    if open_on_start is None:
        open_on_start = bool(
            settings.agent_setting("show_panel_on_start", False)
        ) and agent_should_open_on_start()
    if open_on_start:
        widget.show_panel()

    logger.info("Максимилиан установлен в окно %s", type(window).__name__)
    return widget


def install_agent_safely(window: QWidget,
                         auto_confirm: Optional[bool] = None) -> Optional[AgentWidget]:
    """
    Ставит агента, не мешая окну открыться.

    Сбой установки (нет ключа, ошибка в настройках) не должен ломать
    запуск программы: окно важнее помощника. Причина уходит в лог.
    """
    try:
        return install_agent(window, auto_confirm=auto_confirm)
    except Exception as e:  # noqa: BLE001 — помощник не должен мешать работе
        logger.exception("Максимилиан не установлен: %s", e)
        return None


def uninstall_agent(window: QWidget) -> None:
    """Убирает Максимилиана из окна (нужно тестам и смене настроек)."""
    widget: Optional[AgentWidget] = getattr(window, "agent_widget", None)
    if widget is None:
        return
    widget.shutdown()
    if widget.panel is not None:
        widget.panel.hide()
        widget.panel.deleteLater()
    if widget.icon_button is not None:
        widget.icon_button.hide()
        widget.icon_button.deleteLater()
    if widget.floating is not None:
        widget.floating.hide()
        widget.floating.deleteLater()
    if widget.shortcut is not None:
        widget.shortcut.setEnabled(False)
        widget.shortcut.deleteLater()
    set_context_window(None)
    window.agent_widget = None


def active_form_window() -> Optional[Any]:
    """
    Окно, которое агент считает открытой формой.

    Используется панелью и тестами: сначала активное окно приложения,
    затем — окно, зарегистрированное контекстом.
    """
    return active_window()


__all__ = [
    "FLOATING_SIZE",
    "ICON_SIZE",
    "PANEL_ANIMATION_MS",
    "PANEL_AVATAR_SIZE",
    "PANEL_WIDTH",
    "AgentIconButton",
    "AgentPanel",
    "AgentTask",
    "AgentTaskSignals",
    "AgentWidget",
    "ConfirmationDialog",
    "FloatingCat",
    "active_form_window",
    "agent_should_open_on_start",
    "ask_confirmation",
    "default_client_factory",
    "install_agent",
    "install_agent_safely",
    "reset_startup_panel",
    "uninstall_agent",
]
